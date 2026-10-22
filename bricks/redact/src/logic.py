"""Replace one-time codes and sign-in or reset links (the rules are in the contract)."""

import re

from ..contract import BrickInput, BrickOutput

LINK_WORDS = ("reset", "password", "passwd", "verify", "verification", "confirm", "token", "magic", "signin", "sign-in",
              "login", "log-in", "auth", "otp")
CODE_WORDS = ("code", "verification", "verify", "otp", "passcode", "pin", "one-time", "one time", "security", "login",
              "sign-in", "sign in", "2fa", "two-factor", "password")
MAX_TEXT = 200_000
LINK = re.compile(r"https?://\S+", re.I)
CODE = re.compile(r"(?<![\w$.,:/-])(\d{4,8}|(?=[A-Z0-9]*\d[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w%:/-]|[.,]\d)")
NEAR = 40


def hide(text: str) -> tuple[str, int]:
    count = 0

    def link(match: re.Match) -> str:
        nonlocal count
        if any(word in match.group(0).lower() for word in LINK_WORDS):
            count += 1
            return "[link hidden]"
        return match.group(0)

    text = LINK.sub(link, text)
    lower = text.lower()
    spans = [m.span() for m in CODE.finditer(text)
             if any(w in lower[max(0, m.start() - NEAR):m.end() + NEAR] for w in CODE_WORDS)]
    for start, end in reversed(spans):
        text = text[:start] + "[code hidden]" + text[end:]
        count += 1
    return text, count


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    if inputs.get("op") != "hide":
        return {"ok": False, "error": "invalid: op must be hide"}
    text = inputs.get("text")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        return {"ok": False, "error": f"invalid: text must be a string of at most {MAX_TEXT} characters"}
    out, count = hide(text)
    return {"ok": True, "text": out, "hidden": count}
