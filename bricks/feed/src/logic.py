"""Choose the new items, ask the model to write posts about them, and check every post."""

from datetime import datetime, timezone
import hashlib
import json
import re

from ..contract import DEFAULT_INSTRUCTIONS, MAX_BODY, MAX_TITLE, BrickInput, BrickOutput
from ..input.adapters import model

SYSTEM = """You write CHIP's feed: short posts for the people who share CHIP's calendar and mail.

Rules:
- Write only about the items you are given. One post is about one item.
- Give each post the item's "source" exactly as given.
- Follow the feed instructions: leave out the items they do not want. An empty list is fine.
- Be accurate: use only facts from the item. Do not invent times, places or people.
- Never copy a one-time code, a password, or a sign-in, verification or password-reset link.
- A title is one line. A body is one to three short sentences.

Answer with one JSON object and nothing else:
{"posts": [{"source": "...", "title": "...", "body": "...", "icon": "one emoji"}]}"""

LINK = re.compile(r"https?://\S+", re.I)
LINK_WORDS = ("reset", "password", "passwd", "verify", "verification", "confirm", "token", "magic", "signin", "sign-in",
              "login", "log-in", "auth", "otp")
CODE = re.compile(r"(?<![\w$.,:/-])(\d{4,8}|(?=[A-Z0-9]*\d[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w%:/-]|[.,]\d)")
CODE_WORDS = ("code", "verification", "verify", "otp", "passcode", "pin", "one-time", "one time", "security", "login",
              "sign-in", "sign in", "2fa", "two-factor")


def _time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:   # an all-day date: midnight UTC
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def has_secret(text: str) -> bool:
    """A one-time code near a code word, or a sign-in or reset link: such text never shows."""
    for match in LINK.finditer(text):
        if any(word in match.group(0).lower() for word in LINK_WORDS):
            return True
    lower = text.lower()
    for match in CODE.finditer(text):
        window = lower[max(0, match.start() - 40):match.end() + 40]
        if any(word in window for word in CODE_WORDS):
            return True
    return False


def _invalid(inputs: dict) -> str | None:
    if inputs.get("op") != "refresh":
        return "op must be refresh"
    if _time(inputs.get("now")) is None or "T" not in str(inputs.get("now")):
        return "now must be ISO 8601 with an offset"
    for field in ("events", "mail", "posted"):
        if not isinstance(inputs.get(field, []), list):
            return f"{field} must be a list"
    if not isinstance(inputs.get("instructions", ""), str):
        return "instructions must be text"
    return None


def _items(inputs: dict, now: datetime, limit: int) -> tuple[list[dict], list[dict], dict]:
    """(items for the model, skipped, source -> item)."""
    posted = {str(s) for s in inputs.get("posted", [])}
    items, skipped, by_source = [], [], {}
    for event in inputs.get("events", []):
        if not isinstance(event, dict) or not event.get("id"):
            continue
        source = f"calendar:{event['id']}"
        end = _time(event.get("end")) or _time(event.get("start"))
        if source in posted:
            skipped.append({"source": source, "reason": "posted"})
        elif event.get("status") == "cancelled":
            skipped.append({"source": source, "reason": "cancelled"})
        elif end is None or end <= now:
            skipped.append({"source": source, "reason": "past"})
        else:
            item = {"source": source, "type": "event", "title": str(event.get("title", ""))[:200],
                    "start": event.get("start", ""), "end": event.get("end", ""), "all_day": bool(event.get("all_day")),
                    "location": str(event.get("location", ""))[:200]}
            items.append(item)
            by_source[source] = {**item, "_event": event}
    for message in inputs.get("mail", []):
        if not isinstance(message, dict) or not message.get("id"):
            continue
        source = f"gmail:{message['id']}"
        if source in posted:
            skipped.append({"source": source, "reason": "posted"})
            continue
        item = {"source": source, "type": "email", "from": str(message.get("from", ""))[:200],
                "subject": str(message.get("subject", ""))[:300], "date": message.get("date", ""),
                "snippet": str(message.get("snippet", ""))[:500]}
        items.append(item)
        by_source[source] = item
    if len(items) > limit:
        for item in items[limit:]:
            skipped.append({"source": item["source"], "reason": "too many items"})
            by_source.pop(item["source"], None)
        items = items[:limit]
    return items, skipped, by_source


def _clock(moment: datetime) -> str:
    return moment.strftime("%I:%M %p").lstrip("0")


def _card(event: dict) -> dict:
    start = _time(event.get("start"))
    day = start.strftime("%A, %b %d").replace(" 0", " ") if start else ""
    when = "All day" if event.get("all_day") or start is None else _clock(start)
    return {"type": "calendar", "day": day, "rows": [{"time": when, "title": str(event.get("title", ""))[:200],
                                                       "note": f"({event['location']})" if event.get("location") else ""}]}


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    problem = _invalid(inputs)
    if problem:
        return {"ok": False, "error": f"invalid: {problem}"}
    now = _time(inputs["now"])
    config = ctx["config"]
    items, skipped, by_source = _items(inputs, now, int(config.get("max_items", 40)))
    if not items:
        return {"ok": True, "posts": [], "skipped": skipped, "asked": False}
    instructions = (inputs.get("instructions") or "").strip() or DEFAULT_INSTRUCTIONS
    prompt = json.dumps({"now": inputs["now"], "instructions": instructions, "items": items}, ensure_ascii=False)
    answer = model.complete({"op": "complete", "system": SYSTEM, "max_tokens": 2048, "json": True,
                             "messages": [{"role": "user", "content": prompt}]}, ctx)
    if not answer.get("ok"):
        return {"ok": False, "error": f"model: {answer.get('error', 'no answer')}"[:300], "asked": True}
    data = answer.get("data")
    proposed = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(proposed, list):
        return {"ok": False, "error": "model: the answer has no posts list", "asked": True}
    posts, used = [], set()
    for post in proposed:
        if not isinstance(post, dict):
            continue
        source = str(post.get("source", ""))
        title = post.get("title") if isinstance(post.get("title"), str) else ""
        body = post.get("body") if isinstance(post.get("body"), str) else ""
        title, body = title.strip()[:MAX_TITLE], body.strip()[:MAX_BODY]
        if source not in by_source:
            skipped.append({"source": source[:100], "reason": "not an item"})
        elif source in used:
            skipped.append({"source": source, "reason": "duplicate"})
        elif not title:
            skipped.append({"source": source, "reason": "no title"})
        elif has_secret(f"{title}\n{body}"):
            skipped.append({"source": source, "reason": "secret"})
        elif len(posts) >= int(config.get("max_posts", 8)):
            skipped.append({"source": source, "reason": "too many posts"})
        else:
            used.add(source)
            item = by_source[source]
            icon = post.get("icon") if isinstance(post.get("icon"), str) and 0 < len(post["icon"]) <= 8 else \
                ("📅" if item["type"] == "event" else "✉️")
            out = {"key": "p-" + hashlib.sha256(source.encode()).hexdigest()[:20], "source": source,
                   "time": inputs["now"], "icon": icon, "title": title, "body": body,
                   "kind": "calendar" if item["type"] == "event" else "text"}
            if item["type"] == "event":
                out["card"] = _card(item["_event"])
            posts.append(out)
    return {"ok": True, "posts": posts, "skipped": skipped, "asked": True}
