"""calendar: CHIP's own Google Calendar, read side.

One op, "list": the events between two times on CHIP's primary calendar,
normalised. Writes (creating an event) come later, and only through approvals.
The access token never enters a brick: the adapter reads the refresh token
from a file outside every record.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
# The answer is live data from Google.
DETERMINISTIC = False
SIBLING_DEPENDENCIES: dict[str, str] = {}
OWNED_STATE: tuple[str, ...] = ()

MAX_EVENTS = 250
MAX_DAYS = 62


class BrickInput(TypedDict, total=False):
    """op: "list"; time_min, time_max: ISO 8601 with offset, at most 62 days apart; max: 1 to 250 (default 50)."""

    op: str
    time_min: str
    time_max: str
    max: int


class BrickOutput(TypedDict, total=False):
    """ok; events: [{id, title, start, end, all_day, location, status, organizer, attendees, link}],
    in start order, cancelled events left out; or error ("invalid: ...")."""

    ok: bool
    events: list
    error: str
