"""redact: hide one-time codes and sign-in or reset links in text that CHIP shows or saves.

One op, "hide": text in, the same text out with each secret replaced, and how
many were replaced. CHIP's tools pass every text from Gmail, the calendar and the
memory through it before the agent sees it, and every reply before it is saved.

What is a secret:
- a link (http or https, up to the next whitespace) that has one of LINK_WORDS
  (lowercase, anywhere in the link) -> "[link hidden]";
- a code: 4 to 8 digits, or 6 to 8 capitals and digits with at least two digits
  and one letter, not part of a longer word, number, amount or time (not after a
  letter, digit, "$", ".", ",", ":", "/" or "-"; not before a letter, digit, "%",
  ":", "/", "-", or "." or "," and a digit), with one of CODE_WORDS (lowercase)
  within 40 characters before or after it -> "[code hidden]".
Links are replaced first, then codes. Everything else stays as it was.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "pure"
DETERMINISTIC = True
SIBLING_DEPENDENCIES: dict[str, str] = {}
OWNED_STATE: tuple[str, ...] = ()

LINK_WORDS = ("reset", "password", "passwd", "verify", "verification", "confirm", "token", "magic", "signin", "sign-in",
              "login", "log-in", "auth", "otp")
CODE_WORDS = ("code", "verification", "verify", "otp", "passcode", "pin", "one-time", "one time", "security", "login",
              "sign-in", "sign in", "2fa", "two-factor", "password")
MAX_TEXT = 200_000


class BrickInput(TypedDict, total=False):
    """op: "hide"; text: up to 200,000 characters."""

    op: str
    text: str


class BrickOutput(TypedDict, total=False):
    """ok; text: the text with secrets replaced; hidden: how many; or error ("invalid: ...")."""

    ok: bool
    text: str
    hidden: int
    error: str
