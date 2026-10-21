"""Check the request, ask the provider once, and give back its text."""

import json

from ..contract import DEFAULT_TOKENS, MAX_CHARS, MAX_MESSAGES, MAX_TOKENS, ROLES, BrickInput, BrickOutput
from ..input.adapters import anthropic

PROVIDERS = {"anthropic": anthropic.complete}
STOPS = {"end_turn": "end", "stop_sequence": "end", "max_tokens": "max_tokens"}


def _invalid(inputs: dict) -> str | None:
    if inputs.get("op") != "complete":
        return "op must be complete"
    messages = inputs.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        return f"messages must be a list of 1 to {MAX_MESSAGES}"
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ROLES \
                or not isinstance(message.get("content"), str) or not message["content"].strip():
            return "each message needs a role (user or assistant) and text"
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        return "the first and the last message must be from the user"
    system = inputs.get("system", "")
    if not isinstance(system, str):
        return "system must be text"
    if len(system) + sum(len(m["content"]) for m in messages) > MAX_CHARS:
        return f"a request has at most {MAX_CHARS} characters"
    tokens = inputs.get("max_tokens", DEFAULT_TOKENS)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= MAX_TOKENS:
        return f"max_tokens must be 1 to {MAX_TOKENS}"
    if not isinstance(inputs.get("json", False), bool):
        return "json must be true or false"
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
    try:
        answer = provider(request, ctx)
    except Exception as exc:   # the provider failed: the caller gets a reason, not a crash
        return {"ok": False, "error": f"provider: {str(exc)[:200]}"}
    if not isinstance(answer, dict) or not isinstance(answer.get("content"), list):
        return {"ok": False, "error": "provider: the answer has no content"}
    text = "".join(b.get("text", "") for b in answer["content"] if isinstance(b, dict) and b.get("type") == "text")
    usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
    out: BrickOutput = {"ok": True, "text": text, "stop": STOPS.get(answer.get("stop_reason"), "other"),
                        "usage": {"input": int(usage.get("input_tokens", 0) or 0),
                                  "output": int(usage.get("output_tokens", 0) or 0)}}
    if inputs.get("json"):
        try:
            out["data"] = _json(text)
        except ValueError:
            return {"ok": False, "error": "provider: the answer is not one JSON value"}
    return out
