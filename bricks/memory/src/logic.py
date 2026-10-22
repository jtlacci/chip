"""Check the request, then make the one database call the contract names."""

import re

from ..contract import MAX_LIMIT, MAX_QUERY, MAX_TERMS, MAX_TEXT, BrickInput, BrickOutput
from ..input.adapters import memory_db

WORD = re.compile(r"[a-z0-9]+")
LINK = re.compile(r"https?://\S+", re.I)
LINK_WORDS = ("reset", "password", "passwd", "verify", "verification", "confirm", "token", "magic", "signin", "sign-in",
              "login", "log-in", "auth", "otp")
CODE = re.compile(r"(?<![\w$.,:/-])(\d{4,8}|(?=[A-Z0-9]*\d[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w%:/-]|[.,]\d)")
CODE_WORDS = ("code", "verification", "verify", "otp", "passcode", "pin", "one-time", "one time", "security", "login",
              "sign-in", "sign in", "2fa", "two-factor", "password")


def has_secret(text: str) -> bool:
    for match in LINK.finditer(text):
        if any(word in match.group(0).lower() for word in LINK_WORDS):
            return True
    lower = text.lower()
    for match in CODE.finditer(text):
        window = lower[max(0, match.start() - 40):match.end() + 40]
        if any(word in window for word in CODE_WORDS):
            return True
    return False


def terms(query: str) -> list[str]:
    out: list[str] = []
    for word in WORD.findall(query.lower()):
        if len(word) >= 2 and word not in out:
            out.append(word)
    return out[:MAX_TERMS]


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    op = inputs.get("op")
    if op == "remember":
        text = inputs.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            return {"ok": False, "error": f"invalid: text must be 1 to {MAX_TEXT} characters"}
        if not isinstance(inputs.get("now"), str) or not inputs["now"]:
            return {"ok": False, "error": "invalid: now is required"}
        if has_secret(text):
            return {"ok": False, "error": "invalid: a one-time code or a sign-in link is never saved"}
        answer = memory_db.call({"op": "insert", "text": " ".join(text.split()), "time": inputs["now"]}, ctx)
        return {"ok": True, "id": str(answer.get("id", "")), "new": bool(answer.get("new"))}
    if op == "search":
        query = inputs.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
            return {"ok": False, "error": f"invalid: query must be 1 to {MAX_QUERY} characters"}
        limit = inputs.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            return {"ok": False, "error": f"invalid: limit must be 1 to {MAX_LIMIT}"}
        words = terms(query)
        if not words:
            return {"ok": True, "items": []}
        answer = memory_db.call({"op": "search", "terms": words, "limit": limit}, ctx)
        items = [{"id": str(i.get("id", "")), "text": str(i.get("text", "")), "time": str(i.get("time", ""))}
                 for i in answer.get("items", []) if isinstance(i, dict)]
        return {"ok": True, "items": items[:limit]}
    return {"ok": False, "error": "invalid: op must be remember or search"}
