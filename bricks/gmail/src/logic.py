"""Search and read CHIP's mail; hide one-time codes and sign-in links."""

import base64
import html
import re

from ..contract import MAX_QUERY, MAX_RESULTS, MAX_TEXT, BrickInput, BrickOutput
from ..input.adapters import google_gmail

ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
URL = re.compile(r"https?://[^\s<>\"')\]]+")
LINK_WORDS = ("reset", "password", "passwd", "verify", "verification", "confirm", "token", "magic", "signin",
              "sign-in", "sign_in", "login", "log-in", "auth", "otp", "passcode", "2fa", "mfa", "code=")
CODE = re.compile(r"(?<![\w$.,:/-])(\d{4,8}|(?=[A-Z0-9]*\d[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w%:/-]|[.,]\d)")
CODE_WORDS = ("code", "verification", "verify", "otp", "passcode", "pin", "one-time", "one time", "security",
              "2fa", "two-factor", "two factor", "login", "log in", "sign in", "sign-in")
YEAR = re.compile(r"(19|20)\d\d")


def hide(text: str) -> tuple[str, int]:
    """(text with secrets replaced, how many were replaced)."""
    count = 0

    def link(match):
        nonlocal count
        if any(word in match.group(0).lower() for word in LINK_WORDS):
            count += 1
            return "[link hidden]"
        return match.group(0)

    text = URL.sub(link, text)
    out, last = [], 0
    for match in CODE.finditer(text):
        token = match.group(1)
        if YEAR.fullmatch(token):
            continue
        window = text[max(0, match.start() - 60):match.end() + 60].lower()
        if any(word in window for word in CODE_WORDS):
            out.append(text[last:match.start()] + "[code hidden]")
            last = match.end()
            count += 1
    out.append(text[last:])
    return "".join(out), count


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _text(part: dict) -> tuple[str, str]:
    """(plain text, html) found in a MIME part tree."""
    mime = part.get("mimeType", "")
    data = (part.get("body") or {}).get("data")
    if mime == "text/plain" and data:
        return _decode(data), ""
    if mime == "text/html" and data:
        return "", _decode(data)
    plain, rich = "", ""
    for child in part.get("parts", []) or []:
        p, h = _text(child)
        plain, rich = plain or p, rich or h
    return plain, rich


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def _summary(raw: dict) -> dict:
    headers = {h.get("name", "").lower(): h.get("value", "") for h in (raw.get("payload") or {}).get("headers", [])}
    subject, a = hide(headers.get("subject", ""))
    snippet, b = hide(html.unescape(raw.get("snippet", "")))
    labels = raw.get("labelIds", []) or []
    return {"id": raw.get("id", ""), "thread_id": raw.get("threadId", ""), "from": headers.get("from", ""),
            "to": headers.get("to", ""), "subject": subject, "date": headers.get("date", ""), "snippet": snippet,
            "labels": sorted(labels), "unread": "UNREAD" in labels, "hidden": a + b}


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    op = inputs.get("op")
    if op == "search":
        query, limit = inputs.get("query", ""), inputs.get("max", 20)
        if not isinstance(query, str) or len(query) > MAX_QUERY:
            return {"ok": False, "error": f"invalid: query must be text of at most {MAX_QUERY} characters"}
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
            return {"ok": False, "error": f"invalid: max must be 1 to {MAX_RESULTS}"}
        found = google_gmail.call({"op": "list", "q": query, "max": limit}, ctx).get("messages", [])[:limit]
        messages = []
        for item in found:
            raw = google_gmail.call({"op": "get", "id": item["id"], "format": "metadata"}, ctx)
            if not raw.get("missing"):
                messages.append(_summary(raw))
        return {"ok": True, "messages": messages}
    if op == "get":
        message_id = inputs.get("id")
        if not isinstance(message_id, str) or not ID.fullmatch(message_id):
            return {"ok": False, "error": "invalid: id must be a Gmail message ID"}
        raw = google_gmail.call({"op": "get", "id": message_id, "format": "full"}, ctx)
        if raw.get("missing"):
            return {"ok": False, "error": "not_found"}
        message = _summary(raw)
        plain, rich = _text(raw.get("payload") or {})
        text, count = hide((plain or _strip_html(rich))[:MAX_TEXT])
        message.update(text=text, hidden=message["hidden"] + count)
        return {"ok": True, "message": message}
    return {"ok": False, "error": "invalid: op must be search or get"}
