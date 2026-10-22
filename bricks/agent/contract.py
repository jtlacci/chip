"""agent: CHIP's answer to a chat message, with tools.

One op, "reply". The app server gives a snapshot: the chat's recent messages
(the last one is the message to answer), who wrote it, the time, CHIP's
calendar (a day back to two weeks ahead), its recent mail (and the text of the
newest), and the memory facts that match the message.

The agent asks the model brick, which may call tools. The tools search the
snapshot only, so the agent's one sibling is the model:
- calendar_list {from_day, to_day}: the events between two days (YYYY-MM-DD).
- gmail_search {query}: the messages whose sender, subject or snippet has the words.
- gmail_read {id}: one message, with its text when the snapshot has it.
- memory_search {query}: the facts with the words.
- remember {text}: a fact to save to the shared memory (the server saves it).

At most config max_steps model calls. The output has the reply, the steps (the
Activity tab shows them), and the facts to remember. One-time codes and
sign-in or reset links never show in the reply, and are never remembered.
Writes to Gmail or the calendar come later, only through approvals.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
# The model writes the reply.
DETERMINISTIC = False
SIBLING_DEPENDENCIES: dict[str, str] = {"model": "eventual"}
OWNED_STATE: tuple[str, ...] = ()

TOOLS = ("calendar_list", "gmail_search", "gmail_read", "memory_search", "remember")
MAX_REPLY = 4000


class BrickInput(TypedDict, total=False):
    """op: "reply"; now: ISO 8601 with offset; chat: {id, title}; messages: [{author, name, text, time}],
    oldest first, the last is the one to answer; user: {user_id, name, role}; events: calendar brick events;
    mail: gmail brick messages (may have "text"); memory: [{id, text, time}]."""

    op: str
    now: str
    chat: dict
    messages: list
    user: dict
    events: list
    mail: list
    memory: list


class BrickOutput(TypedDict, total=False):
    """ok; text: the reply; steps: [{tool, input, ok, summary}]; remember: [text]; usage {input, output};
    or error ("invalid: ..." or "model: ...")."""

    ok: bool
    text: str
    steps: list
    remember: list
    usage: dict
    error: str
