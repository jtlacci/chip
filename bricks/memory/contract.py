"""memory: CHIP's shared memory. The agent saves facts and finds them again by words.

All users share one memory, as they share the feed and the chats. There is no
page for it: only the agent reads and writes it.

Ops:
- remember: text (1 to 1000 characters) -> saves it; the same text again is the
  same fact. A text with a one-time code or a sign-in or reset link is refused.
- search: query (1 to 200 characters), limit (1 to 20, default 5) -> the facts
  that match, best match first. A query with no words finds nothing, with no call.

The calls to the memory database (external "memory_db") are exactly:
- {"op": "insert", "text": <the text, each run of whitespace one space, trimmed>, "time": <now>}
  -> {"id": text, "new": bool}
- {"op": "search", "terms": <the query's words: lowercase runs of letters and digits, 2 or more
  characters, first 8, in order, no repeats>, "limit": <limit>}
  -> {"items": [{"id", "text", "time"}]}
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
# The answer depends on what the database holds.
DETERMINISTIC = False
SIBLING_DEPENDENCIES: dict[str, str] = {}
OWNED_STATE: tuple[str, ...] = ("database:memory",)

MAX_TEXT = 1000
MAX_QUERY = 200
MAX_LIMIT = 20
MAX_TERMS = 8


class BrickInput(TypedDict, total=False):
    """op: remember (text, now) | search (query, limit)."""

    op: str
    text: str
    now: str
    query: str
    limit: int


class BrickOutput(TypedDict, total=False):
    """ok; remember -> id, new; search -> items [{id, text, time}]; or error ("invalid: ...")."""

    ok: bool
    id: str
    new: bool
    items: list
    error: str
