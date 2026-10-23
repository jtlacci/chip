"""assistant: CHIP's answer in chat, from the Hermes agent.

One op, "reply". The app server gives the chat's recent messages (the last one
is the message to answer), who wrote it, and the time. The brick builds CHIP's
system prompt and the conversation (people as "user" messages that start with
their names, CHIP as "assistant"; one message for each turn), asks the Hermes
agent, and hides one-time codes and sign-in or reset links in the reply and the
step labels (the redact brick).

The call to Hermes (external "hermes") is exactly:
    {"system": <the system prompt>, "messages": [{"role", "content"}],
     "profile": {"commit": config hermes_commit, "model": config model, "soul": config soul}}
    -> {"text": the reply, "steps": [{"tool", "label"}]} or {"error": text}
The profile names the Hermes that answers: the server's Hermes runs that commit,
model and persona (deploy/app/hermes_setup.sh reads them from config.yml), and
the evaluation runner starts that commit. Hermes's tools are CHIP's tools (the
calendar, Gmail and the shared memory, served by the app server) and the web.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
# The agent writes the reply.
DETERMINISTIC = False
SIBLING_DEPENDENCIES: dict[str, str] = {"redact": "eventual"}
OWNED_STATE: tuple[str, ...] = ()

MAX_REPLY = 4000
MAX_STEPS_SHOWN = 20


class BrickInput(TypedDict, total=False):
    """op: "reply"; now: ISO 8601 with offset; chat: {id, title}; messages: [{author, name, text, time}],
    oldest first, the last is the one to answer (author "chip" is CHIP); user: {user_id, name, role}."""

    op: str
    now: str
    chat: dict
    messages: list
    user: dict


class BrickOutput(TypedDict, total=False):
    """ok; text: the reply; steps: [{tool, label}]; or error ("invalid: ..." or "hermes: ...")."""

    ok: bool
    text: str
    steps: list
    error: str
