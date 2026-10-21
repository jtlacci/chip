"""feed: the posts CHIP adds to the shared feed, from its sources and the instructions.

One op, "refresh". The caller (the app server) gives the sources: the events
from the calendar brick, the messages from the gmail brick, and the sources
that already have a post. The feed chooses, with the model brick, what to post
about, and checks every post before it gives it back:

- each post is about one item from the input, and names it ("calendar:<id>"
  or "gmail:<id>");
- no post is about an event that has ended, or about an item that already has
  a post, and no two posts are about one item;
- no post shows a one-time code or a sign-in or password-reset link;
- with nothing new, the model is not asked.

The feed does not write: the caller saves the posts. So the feed's only
sibling is the model, and its evaluation needs no prediction of store
requests.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
# The model writes the posts.
DETERMINISTIC = False
SIBLING_DEPENDENCIES: dict[str, str] = {"model": "eventual"}
OWNED_STATE: tuple[str, ...] = ()

DEFAULT_INSTRUCTIONS = "Make me a feed about what is happening in CHIP's calendar and mail."
MAX_TITLE = 120
MAX_BODY = 600


class BrickInput(TypedDict, total=False):
    """op: "refresh"; now: ISO 8601 with an offset; instructions: text (empty: the default);
    events: calendar brick events; mail: gmail brick messages; posted: sources that already have a post."""

    op: str
    now: str
    instructions: str
    events: list
    mail: list
    posted: list


class BrickOutput(TypedDict, total=False):
    """ok; posts: [{key, source, time, icon, title, body, kind ("calendar" | "text"), card?}];
    skipped: [{source, reason}]; asked: whether the model was asked; or error ("invalid: ..." or "model: ...")."""

    ok: bool
    posts: list
    skipped: list
    asked: bool
    error: str
