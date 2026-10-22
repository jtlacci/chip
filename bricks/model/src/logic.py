"""Check the request, ask the provider once, and give back its text and tool calls."""

import json
import re

from ..contract import (BLOCKS, DEFAULT_TOKENS, MAX_CHARS, MAX_IMAGE, MAX_MESSAGES, MAX_TOKENS, MAX_TOOLS, ROLES,
                        TOOLSETS, BrickInput, BrickOutput)
from ..input.adapters import anthropic

PROVIDERS = {"anthropic": anthropic.complete}
STOPS = {"end_turn": "end", "stop_sequence": "end", "max_tokens": "max_tokens", "tool_use": "tool_use"}
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _block_chars(block: dict) -> int | None:
    """The text size of a block, or None when it is not a block this brick passes on."""
    kind = block.get("type")
    if kind == "text":
        return len(block["text"]) if isinstance(block.get("text"), str) else None
    if kind == "tool_use":
        if not isinstance(block.get("id"), str) or not isinstance(block.get("name"), str) \
                or not isinstance(block.get("input", {}), dict):
            return None
        return len(json.dumps(block.get("input", {})))
    if kind == "tool_result":
        if not isinstance(block.get("tool_use_id"), str):
            return None
        content = block.get("content", "")
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list) and all(isinstance(b, dict) and b.get("type") in ("text", "image") for b in content):
            sizes = [_block_chars(b) for b in content]
            return None if None in sizes else sum(sizes)
        return None
    if kind == "image":
        source = block.get("source")
        if not isinstance(source, dict) or source.get("type") != "base64" \
                or source.get("media_type") not in ("image/png", "image/jpeg") \
                or not isinstance(source.get("data"), str) or len(source["data"]) > MAX_IMAGE:
            return None
        return 0   # images have their own limit
    return None


def _content_chars(content) -> int | None:
    if isinstance(content, str):
        return len(content) if content.strip() else None
    if isinstance(content, list) and content and all(isinstance(b, dict) and b.get("type") in BLOCKS for b in content):
        sizes = [_block_chars(b) for b in content]
        return None if None in sizes else sum(sizes)
    return None


def _tools_invalid(tools) -> str | None:
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        return f"tools must be a list of at most {MAX_TOOLS}"
    names = set()
    for tool in tools:
        if not isinstance(tool, dict):
            return "each tool must be an object"
        if "type" in tool:
            if tool["type"] not in TOOLSETS or set(tool) - {"type", "configs"}:
                return f"a toolset must be one of {', '.join(TOOLSETS)}"
            continue
        if not isinstance(tool.get("name"), str) or not TOOL_NAME.fullmatch(tool["name"]) or tool["name"] in names:
            return "each tool needs a unique name of letters, digits, _ or -"
        if not isinstance(tool.get("description"), str) or not isinstance(tool.get("input_schema"), dict) \
                or set(tool) - {"name", "description", "input_schema"}:
            return "a tool has name, description and input_schema only"
        names.add(tool["name"])
    return None


def _invalid(inputs: dict) -> str | None:
    if inputs.get("op") != "complete":
        return "op must be complete"
    messages = inputs.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        return f"messages must be a list of 1 to {MAX_MESSAGES}"
    total = 0
    for message in messages:
        size = _content_chars(message.get("content")) if isinstance(message, dict) else None
        if size is None or message.get("role") not in ROLES:
            return "each message needs a role (user or assistant) and text or blocks"
        total += size
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        return "the first and the last message must be from the user"
    system = inputs.get("system", "")
    if not isinstance(system, str):
        return "system must be text"
    if len(system) + total > MAX_CHARS:
        return f"a request has at most {MAX_CHARS} characters"
    tokens = inputs.get("max_tokens", DEFAULT_TOKENS)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= MAX_TOKENS:
        return f"max_tokens must be 1 to {MAX_TOKENS}"
    if not isinstance(inputs.get("json", False), bool):
        return "json must be true or false"
    if "tools" in inputs:
        problem = _tools_invalid(inputs["tools"])
        if problem:
            return problem
        if inputs.get("json"):
            return "json and tools cannot be used together"
    return None


def _json(text: str):
    """The one JSON value in the text: the whole text, or the text in a ``` block."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    return json.loads(body)


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    problem = _invalid(inputs)
    if problem:
        return {"ok": False, "error": f"invalid: {problem}"}
    config = ctx["config"]
    provider = PROVIDERS.get(config.get("provider"))
    if provider is None:
        return {"ok": False, "error": f"provider: {config.get('provider')!r} has no adapter"}
    request = {"model": config["model"], "max_tokens": inputs.get("max_tokens", DEFAULT_TOKENS),
               "messages": [{"role": m["role"], "content": m["content"]} for m in inputs["messages"]]}
    if inputs.get("system"):
        request["system"] = inputs["system"]
    if inputs.get("tools"):
        request["tools"] = inputs["tools"]
    try:
        answer = provider(request, ctx)
    except Exception as exc:   # the provider failed: the caller gets a reason, not a crash
        return {"ok": False, "error": f"provider: {str(exc)[:200]}"}
    if not isinstance(answer, dict) or not isinstance(answer.get("content"), list):
        return {"ok": False, "error": "provider: the answer has no content"}
    blocks = [b for b in answer["content"] if isinstance(b, dict)]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
    out: BrickOutput = {"ok": True, "text": text, "stop": STOPS.get(answer.get("stop_reason"), "other"),
                        "usage": {"input": int(usage.get("input_tokens", 0) or 0),
                                  "output": int(usage.get("output_tokens", 0) or 0)}}
    if inputs.get("tools"):
        out["tool_calls"] = [{"id": str(b.get("id", "")), "name": str(b.get("name", "")),
                              "input": b.get("input") if isinstance(b.get("input"), dict) else {}}
                             for b in blocks if b.get("type") == "tool_use"]
        out["content"] = blocks
    if inputs.get("json"):
        try:
            out["data"] = _json(text)
        except ValueError:
            return {"ok": False, "error": "provider: the answer is not one JSON value"}
    return out
