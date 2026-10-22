"""model: one interface for every model provider. Version 1: Anthropic (Claude).

One op, "complete": a system prompt and messages in, the model's text out.
With "tools", the model can ask for tool calls: the output has tool_calls and
the assistant's content blocks, which the caller sends back with the tool
results in the next request (the agent brick runs that loop).
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
BLOCKS = ("text", "tool_use", "tool_result", "image")
MAX_TOOLS = 32
MAX_IMAGE = 5_000_000          # base64 characters of one image
TOOLSETS = ("computer_toolset_20260801",)   # provider toolsets a caller may ask for


class BrickInput(TypedDict, total=False):
    """op: "complete"; system: text; messages: [{role: user|assistant, content: text or a list of blocks
    (text, tool_use, tool_result, image)}], first and last from the user; max_tokens: 1 to 4096 (default 1024);
    json: the answer must be one JSON value; tools: up to 32 of {name, description, input_schema} or a toolset
    {type} from TOOLSETS."""

    op: str
    system: str
    messages: list
    max_tokens: int
    json: bool
    tools: list


class BrickOutput(TypedDict, total=False):
    """ok; text; data (with json); stop ("end", "tool_use", "max_tokens", "other"); usage {input, output};
    tool_calls: [{id, name, input}]; content: the assistant's blocks, to send back with the tool results;
    or error ("invalid: ..." for a bad request, "provider: ..." when the provider failed or the answer is wrong)."""

    ok: bool
    text: str
    tool_calls: list
    content: list
    data: object
    stop: str
    usage: dict
    error: str
