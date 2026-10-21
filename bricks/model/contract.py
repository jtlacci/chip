"""model: one interface for every model provider. Version 1: Anthropic (Claude).

One op, "complete": a system prompt and messages in, the model's text out.
input/config.yml names the provider and the model, so no caller names them,
and a new provider is one new adapter. With "json", the answer must be one
JSON value, returned as data. The API key never enters a brick: the adapter
reads it from a file outside every record.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
# A model's answer is not a function of its input.
DETERMINISTIC = False
SIBLING_DEPENDENCIES: dict[str, str] = {}
OWNED_STATE: tuple[str, ...] = ()

MAX_TOKENS = 4096
DEFAULT_TOKENS = 1024
MAX_MESSAGES = 50
MAX_CHARS = 100_000   # all text of one request
ROLES = ("user", "assistant")


class BrickInput(TypedDict, total=False):
    """op: "complete"; system: text; messages: [{role: user|assistant, content: text}], first and last from
    the user; max_tokens: 1 to 4096 (default 1024); json: the answer must be one JSON value."""

    op: str
    system: str
    messages: list
    max_tokens: int
    json: bool


class BrickOutput(TypedDict, total=False):
    """ok; text; data (with json); stop ("end", "max_tokens", "other"); usage {input, output};
    or error ("invalid: ..." for a bad request, "provider: ..." when the provider failed or the answer is wrong)."""

    ok: bool
    text: str
    data: object
    stop: str
    usage: dict
    error: str
