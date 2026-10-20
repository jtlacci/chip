"""How the gmail brick is evaluated. Evaluation loop only.

Two kinds of check:
- a reference model of the contract gives the expected output from the
  recorded Gmail answers (matches);
- planted values that the reference does not decide: the one-time codes and
  sign-in links in SECRETS must never appear in any output (hides_secrets),
  and the ordinary values in ORDINARY must survive (keeps_ordinary).
"""

import base64
import html
import json
import random
import re

GATES: tuple[tuple[str, str, float], ...] = (
    ("matches", ">=", 1.0),
    ("hides_secrets", ">=", 1.0),
    ("keeps_ordinary", ">=", 1.0),
    ("rejects_invalid", ">=", 1.0),
)

SECRETS = ("482913", "907713", "K7Q2P9", "https://accounts.example.com/reset?token=Zq81", "https://app.example.com/login/magic/f00d",
           "https://kalshi.com/password/reset/abc123", "https://mail.example.com/verify-email?u=9")
ORDINARY = ("$120.50", "5:08 PM", "2026", "https://luma.com/reign-fm", "Order 4471", "212-555-0199", "Club Reign")

URL = re.compile(r"https?://[^\s<>\"')\]]+")
LINK_WORDS = ("reset", "password", "passwd", "verify", "verification", "confirm", "token", "magic", "signin",
              "sign-in", "sign_in", "login", "log-in", "auth", "otp", "passcode", "2fa", "mfa", "code=")
CODE = re.compile(r"(?<![\w$.,:/-])(\d{4,8}|(?=[A-Z0-9]*\d[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w,%:/-]|\.\d)")
CODE_WORDS = ("code", "verification", "verify", "otp", "passcode", "pin", "one-time", "one time", "security",
              "2fa", "two-factor", "two factor", "login", "log in", "sign in", "sign-in")


def _hide(text: str) -> tuple[str, int]:
    count = 0
    pieces, last = [], 0
    for match in URL.finditer(text):
        if any(w in match.group(0).lower() for w in LINK_WORDS):
            pieces.append(text[last:match.start()] + "[link hidden]")
            last = match.end()
            count += 1
    text = "".join(pieces) + text[last:]
    pieces, last = [], 0
    for match in CODE.finditer(text):
        token = match.group(1)
        if len(token) == 4 and token[:2] in ("19", "20") and token.isdigit():
            continue
        window = text[max(0, match.start() - 60):match.end() + 60].lower()
        if any(w in window for w in CODE_WORDS):
            pieces.append(text[last:match.start()] + "[code hidden]")
            last = match.end()
            count += 1
    return "".join(pieces) + text[last:], count


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _parts(part: dict) -> tuple[str, str]:
    mime, data = part.get("mimeType", ""), (part.get("body") or {}).get("data")
    if mime == "text/plain" and data:
        return _decode(data), ""
    if mime == "text/html" and data:
        return "", _decode(data)
    plain = rich = ""
    for child in part.get("parts", []) or []:
        p, h = _parts(child)
        plain, rich = plain or p, rich or h
    return plain, rich


def _strip(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def _summary(raw: dict) -> dict:
    headers = {h.get("name", "").lower(): h.get("value", "") for h in (raw.get("payload") or {}).get("headers", [])}
    subject, a = _hide(headers.get("subject", ""))
    snippet, b = _hide(html.unescape(raw.get("snippet", "")))
    labels = raw.get("labelIds", []) or []
    return {"id": raw.get("id", ""), "thread_id": raw.get("threadId", ""), "from": headers.get("from", ""),
            "to": headers.get("to", ""), "subject": subject, "date": headers.get("date", ""), "snippet": snippet,
            "labels": sorted(labels), "unread": "UNREAD" in labels, "hidden": a + b}


def expected(record: dict) -> dict | None:
    inputs = record["input"]
    answers = {json.dumps(e["request"], sort_keys=True): e["response"] for e in record.get("exchanges", [])
               if e["name"] == "external:gmail"}

    def ask(request):
        return answers.get(json.dumps(request, sort_keys=True), {"missing": True})

    if inputs.get("op") == "search":
        query, limit = inputs.get("query", ""), inputs.get("max", 20)
        if not isinstance(query, str) or len(query) > 500 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            return None
        found = ask({"op": "list", "q": query, "max": limit}).get("messages", [])[:limit]
        out = []
        for item in found:
            raw = ask({"op": "get", "id": item["id"], "format": "metadata"})
            if not raw.get("missing"):
                out.append(_summary(raw))
        return {"ok": True, "messages": out}
    if inputs.get("op") == "get":
        mid = inputs.get("id")
        if not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", mid):
            return None
        raw = ask({"op": "get", "id": mid, "format": "full"})
        if raw.get("missing"):
            return {"ok": False, "error": "not_found"}
        message = _summary(raw)
        plain, rich = _parts(raw.get("payload") or {})
        text, count = _hide((plain or _strip(rich))[:20000])
        message.update(text=text, hidden=message["hidden"] + count)
        return {"ok": True, "message": message}
    return None


def build(record: dict) -> dict:
    return record["input"]


def score(record: dict, output: dict | None, later: list[dict]) -> dict[str, float]:
    want = expected(record)
    if want is None:
        ok = isinstance(output, dict) and output.get("ok") is False and str(output.get("error", "")).startswith("invalid:")
        return {"matches": float(ok), "rejects_invalid": float(ok)}
    shown = json.dumps(output)
    raw = json.dumps([e["response"] for e in record.get("exchanges", [])])
    decoded = raw + "".join(_decode(d) for d in re.findall(r'"data": "([A-Za-z0-9_-]+=*)"', raw))
    planted_secrets = [s for s in SECRETS if s in decoded]
    planted_ordinary = [o for o in ORDINARY if o in decoded and o in json.dumps(want)]
    terms = {"matches": 1.0 if output == want else 0.0}
    if planted_secrets:
        terms["hides_secrets"] = 0.0 if any(s in shown for s in planted_secrets) else 1.0
    if planted_ordinary:
        terms["keeps_ordinary"] = 1.0 if all(o in shown for o in planted_ordinary) else 0.0
    return terms


# ---------------------------------------------------------------- synthetic data

BODIES = [
    ("Your verification code", "Your verification code is 482913. It expires in 10 minutes."),
    ("Sign in to Example", "Use this one-time code to sign in: K7Q2P9"),
    ("Reset your password", "Someone asked to reset your Kalshi password at 5:08 PM.\nReset it here: https://kalshi.com/password/reset/abc123"),
    ("Your login link", "Click to log in: https://app.example.com/login/magic/f00d"),
    ("Confirm your email", "Confirm your address https://mail.example.com/verify-email?u=9 before 2026 ends."),
    ("Account security", "Your security code: 907713\nIf this wasn't you, reset at https://accounts.example.com/reset?token=Zq81"),
    ("Frontier Markets After Party", "Down to 2 spots left. Last real window to get in: https://luma.com/reign-fm at Club Reign."),
    ("Your order", "Order 4471 is confirmed. Total $120.50. Questions? Call 212-555-0199."),
    ("Dinner Friday?", "Are you free Friday at 5:08 PM? The place is on Grand St."),
]


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _raw(rng: random.Random, mid: str, full: bool) -> dict:
    subject, body = rng.choice(BODIES)
    headers = [{"name": "From", "value": rng.choice(["Kalshi <no-reply@kalshi.com>", "Jacob <jacob@example.com>"])},
               {"name": "To", "value": "chip@example.com"}, {"name": "Subject", "value": subject},
               {"name": "Date", "value": "Tue, 20 Oct 2026 17:08:00 -0400"}]
    raw = {"id": mid, "threadId": "t" + mid, "labelIds": rng.sample(["INBOX", "UNREAD", "IMPORTANT", "CATEGORY_UPDATES"], rng.randint(1, 3)),
           "snippet": html.escape(body[:90]), "payload": {"mimeType": "multipart/alternative", "headers": headers}}
    if full:
        style = rng.choice(["plain", "html", "both", "nested", "empty"])
        rich = "<html><body><p>" + html.escape(body).replace("\n", "<br>") + "</p><style>p{}</style></body></html>"
        if style == "plain":
            raw["payload"]["parts"] = [{"mimeType": "text/plain", "body": {"data": _b64(body)}}]
        elif style == "html":
            raw["payload"]["parts"] = [{"mimeType": "text/html", "body": {"data": _b64(rich)}}]
        elif style == "both":
            raw["payload"]["parts"] = [{"mimeType": "text/html", "body": {"data": _b64(rich)}},
                                       {"mimeType": "text/plain", "body": {"data": _b64(body)}}]
        elif style == "nested":
            raw["payload"]["parts"] = [{"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body)}}]},
                {"mimeType": "application/pdf", "filename": "plan.pdf", "body": {"attachmentId": "att1", "size": 9}}]
        else:
            raw["payload"]["parts"] = []
    return raw


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    for i in range(30):
        mid = f"m{seed}x{i}"
        if rng.random() < 0.1:
            exchanges = [{"name": "gmail", "request": {"op": "get", "id": mid, "format": "full"}, "response": {"missing": True}}]
        else:
            exchanges = [{"name": "gmail", "request": {"op": "get", "id": mid, "format": "full"}, "response": _raw(rng, mid, True)}]
        cases.append({"input": {"op": "get", "id": mid}, "exchanges": exchanges})
    for i in range(20):
        query = rng.choice(["", "is:unread", "from:kalshi.com", "newer_than:2d"])
        limit = rng.choice([1, 5, 20])
        ids = [f"s{seed}x{i}x{n}" for n in range(rng.randint(0, 6))]
        exchanges = [{"name": "gmail", "request": {"op": "list", "q": query, "max": limit},
                      "response": {"messages": [{"id": m, "threadId": "t" + m} for m in ids]}}]
        for m in ids[:limit]:
            response = {"missing": True} if rng.random() < 0.1 else _raw(rng, m, False)
            exchanges.append({"name": "gmail", "request": {"op": "get", "id": m, "format": "metadata"}, "response": response})
        inputs = {"op": "search", "query": query}
        if limit != 20:
            inputs["max"] = limit
        cases.append({"input": inputs, "exchanges": exchanges})
    bad = [{"op": "send"}, {"op": "get", "id": "../../etc"}, {"op": "get"}, {"op": "search", "query": "x" * 501},
           {"op": "search", "max": 0}, {"op": "search", "max": 51}, {"op": "search", "max": True}]
    cases += [{"input": inputs} for inputs in bad]
    return cases
