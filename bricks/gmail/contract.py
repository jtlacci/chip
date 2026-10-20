"""gmail: CHIP's own Gmail account, read side.

search: messages that match a Gmail query, as summaries.
get:    one message with its text.

Before anything leaves this brick, one-time codes and sign-in, verification
and password-reset links are replaced with "[code hidden]" and "[link hidden]"
in the subject, the snippet and the text. That is how the spec's rule ("one-time
codes and password-reset links never show in the feed or in a chat") holds for
every brick after this one. Sending comes later, and only through approvals.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
DETERMINISTIC = False
SIBLING_DEPENDENCIES: dict[str, str] = {}
OWNED_STATE: tuple[str, ...] = ()

MAX_RESULTS = 50
MAX_QUERY = 500
MAX_TEXT = 20000


class BrickInput(TypedDict, total=False):
    """op: search (query, max 1 to 50, default 20) | get (id)."""

    op: str
    query: str
    max: int
    id: str


class BrickOutput(TypedDict, total=False):
    """ok; search -> messages [{id, thread_id, from, to, subject, date, snippet, labels, unread, hidden}];
    get -> message {the same fields, and text}; or error ("invalid: ..." or "not_found")."""

    ok: bool
    messages: list
    message: dict
    error: str
