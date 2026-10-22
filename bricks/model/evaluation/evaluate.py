"""How the model brick is evaluated. Evaluation loop only.

The brick is a thin, strict pass-through, so its evaluation checks the two
sides of the one provider call, from the calls the case made (recorded, or
live when input/config.yml changes the model):

- request_shape: a valid request makes exactly one call, with the caller's
  messages and system prompt, the right max_tokens, a model, and no other
  field; an invalid request makes no call.
- matches: the output is the provider's answer: its text, its stop reason,
  its token counts.
- json: with "json", the output data is the one JSON value in the answer, or
  the request fails with "provider:" when there is none.
- tools: with "tools", the tools are sent as given, and the output's
  tool_calls and content are the answer's tool_use blocks and blocks.
- provider_errors: when the provider fails, the output is an error that
  starts with "provider:", and nothing crashes.
- rejects_invalid: an invalid request fails with "invalid:".
"""

import json
import random
import re

GATES: tuple[tuple[str, str, float], ...] = (
    ("request_shape", ">=", 1.0),
    ("matches", ">=", 1.0),
    ("json", ">=", 1.0),
    ("tools", ">=", 1.0),
    ("provider_errors", ">=", 1.0),
    ("rejects_invalid", ">=", 1.0),
)

MODEL = "claude-sonnet-5-5"   # the model the synthetic records were made with
FIELDS = {"model", "max_tokens", "system", "messages", "tools"}
STOPS = {"end_turn": "end", "stop_sequence": "end", "max_tokens": "max_tokens", "tool_use": "tool_use"}
TOOLSETS = ("computer_toolset_20260801",)


def _block_ok(block) -> bool:
    if not isinstance(block, dict):
        return False
    kind = block.get("type")
    if kind == "text":
        return isinstance(block.get("text"), str)
    if kind == "tool_use":
        return isinstance(block.get("id"), str) and isinstance(block.get("name"), str) and isinstance(block.get("input", {}), dict)
    if kind == "tool_result":
        content = block.get("content", "")
        return isinstance(block.get("tool_use_id"), str) and (isinstance(content, str) or (
            isinstance(content, list) and all(isinstance(b, dict) and b.get("type") in ("text", "image") and _block_ok(b) for b in content)))
    if kind == "image":
        source = block.get("source")
        return isinstance(source, dict) and source.get("type") == "base64" and source.get("media_type") in ("image/png", "image/jpeg") \
            and isinstance(source.get("data"), str) and len(source["data"]) <= 5_000_000
    return False


def _size(content) -> int | None:
    if isinstance(content, str):
        return len(content) if content.strip() else None
    if not isinstance(content, list) or not content or not all(_block_ok(b) for b in content):
        return None
    total = 0
    for b in content:
        if b["type"] == "text":
            total += len(b["text"])
        elif b["type"] == "tool_use":
            total += len(json.dumps(b.get("input", {})))
        elif b["type"] == "tool_result":
            inner = b.get("content", "")
            total += len(inner) if isinstance(inner, str) else sum(len(x.get("text", "")) for x in inner)
    return total


def _tools_ok(tools) -> bool:
    if not isinstance(tools, list) or len(tools) > 32:
        return False
    names = set()
    for tool in tools:
        if not isinstance(tool, dict):
            return False
        if "type" in tool:
            if tool["type"] not in TOOLSETS or set(tool) - {"type", "configs"}:
                return False
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
            return False
        if not isinstance(tool.get("description"), str) or not isinstance(tool.get("input_schema"), dict) \
                or set(tool) - {"name", "description", "input_schema"}:
            return False
        names.add(name)
    return True


def _valid(inputs: dict) -> bool:
    messages = inputs.get("messages")
    if inputs.get("op") != "complete" or not isinstance(messages, list) or not 1 <= len(messages) <= 50:
        return False
    total = 0
    for m in messages:
        size = _size(m.get("content")) if isinstance(m, dict) else None
        if size is None or m.get("role") not in ("user", "assistant"):
            return False
        total += size
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        return False
    system = inputs.get("system", "")
    if not isinstance(system, str) or len(system) + total > 100_000:
        return False
    if "tools" in inputs and (not _tools_ok(inputs["tools"]) or inputs.get("json")):
        return False
    tokens = inputs.get("max_tokens", 1024)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= 4096:
        return False
    return isinstance(inputs.get("json", False), bool)


def _request_ok(inputs: dict, request) -> bool:
    if not isinstance(request, dict) or not set(request) <= FIELDS:
        return False
    want = [{"role": m["role"], "content": m["content"]} for m in inputs["messages"]]
    system_ok = request.get("system") == inputs["system"] if inputs.get("system") else "system" not in request
    tools_ok = request.get("tools") == inputs["tools"] if inputs.get("tools") else "tools" not in request
    return (tools_ok and request.get("messages") == want and system_ok and request.get("max_tokens") == inputs.get("max_tokens", 1024)
            and isinstance(request.get("model"), str) and bool(request["model"]))


def _text(response: dict) -> str:
    return "".join(b.get("text", "") for b in response.get("content", []) if isinstance(b, dict) and b.get("type") == "text")


def _one_json(text: str):
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    return json.loads(body)


def build(record: dict) -> dict:
    return record["input"]


def score(record: dict, output: dict | None, later: list[dict], calls=()) -> dict[str, float]:
    inputs = record["input"]
    out = output if isinstance(output, dict) else {}
    provider = [c for c in calls if c.get("name") == "external:anthropic"]
    if not _valid(inputs):
        ok = out.get("ok") is False and str(out.get("error", "")).startswith("invalid:") and not provider
        return {"rejects_invalid": float(ok), "request_shape": float(not provider), "correct": float(ok)}
    terms = {"request_shape": float(len(provider) == 1 and _request_ok(inputs, provider[0].get("request")))}
    call = provider[0] if provider else {}
    if "error" in call or "response" not in call:
        ok = out.get("ok") is False and str(out.get("error", "")).startswith("provider:")
        terms["provider_errors"] = float(ok)
    else:
        response = call["response"] if isinstance(call["response"], dict) else {}
        text = _text(response)
        usage = response.get("usage") or {}
        expected_stop = STOPS.get(response.get("stop_reason"), "other")
        if inputs.get("tools"):
            blocks = [b for b in response.get("content", []) if isinstance(b, dict)]
            calls = [{"id": str(b.get("id", "")), "name": str(b.get("name", "")),
                      "input": b.get("input") if isinstance(b.get("input"), dict) else {}}
                     for b in blocks if b.get("type") == "tool_use"]
            terms["tools"] = float(out.get("ok") is True and out.get("tool_calls") == calls and out.get("content") == blocks
                                   and out.get("text") == text and out.get("stop") == expected_stop)
        elif inputs.get("json"):
            try:
                data = _one_json(text)
                good = out.get("ok") is True and out.get("data") == data and out.get("text") == text
            except ValueError:
                good = out.get("ok") is False and str(out.get("error", "")).startswith("provider:")
            terms["json"] = float(good)
        else:
            terms["matches"] = float(out.get("ok") is True and out.get("text") == text and out.get("stop") == expected_stop
                                     and out.get("usage") == {"input": int(usage.get("input_tokens", 0) or 0),
                                                              "output": int(usage.get("output_tokens", 0) or 0)})
    terms["correct"] = float(all(v >= 1.0 for v in terms.values()))
    return terms


# ---------------------------------------------------------------- synthetic data

WORDS = ["calendar", "Friday", "dinner", "invoice", "reset", "trip", "Jacob", "hoodie", "NYC", "weekend", "ticket"]


def _messages(rng: random.Random) -> list[dict]:
    turns = rng.choice([1, 1, 1, 3, 5])
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": " ".join(rng.choice(WORDS) for _ in range(rng.randint(2, 12)))} for i in range(turns)]


def _request(inputs: dict) -> dict:
    request = {"model": MODEL, "max_tokens": inputs.get("max_tokens", 1024),
               "messages": [{"role": m["role"], "content": m["content"]} for m in inputs["messages"]]}
    if inputs.get("system"):
        request["system"] = inputs["system"]
    if inputs.get("tools"):
        request["tools"] = inputs["tools"]
    return request


def _answer(rng: random.Random, text: str) -> dict:
    return {"id": f"msg_{rng.randrange(10**8)}", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "text", "text": text}],
            "stop_reason": rng.choice(["end_turn", "end_turn", "max_tokens", "stop_sequence"]),
            "usage": {"input_tokens": rng.randint(5, 900), "output_tokens": rng.randint(1, 400)}}


def _valid_case(rng: random.Random, json_mode: bool = False) -> dict:
    inputs = {"op": "complete", "messages": _messages(rng)}
    if rng.random() < 0.6:
        inputs["system"] = "You are CHIP. " + " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 8)))
    if rng.random() < 0.5:
        inputs["max_tokens"] = rng.choice([1, 64, 512, 2048, 4096])
    if json_mode:
        inputs["json"] = True
        text = rng.choice(['{"posts": [{"title": "Dinner Friday", "source": "calendar:e1"}]}',
                           '```json\n[1, 2, 3]\n```', '```\n{"ok": true}\n```', "Sure! Here are the posts.",
                           '{"broken": ', "[]"])
    else:
        text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(0, 30)))
    return {"input": inputs, "exchanges": [{"name": "external:anthropic", "request": _request(inputs),
                                            "response": _answer(rng, text)}]}


def _failed_case(rng: random.Random) -> dict:
    inputs = {"op": "complete", "messages": _messages(rng)}
    return {"input": inputs, "exchanges": [{"name": "external:anthropic", "request": _request(inputs),
                                            "error": rng.choice(["the model API answered HTTP 529",
                                                                 "the model API cannot be reached: TimeoutError"])}]}


def _invalid_inputs(rng: random.Random) -> dict:
    good = _messages(rng)
    return rng.choice([
        {"op": "chat", "messages": good},
        {"op": "complete", "messages": []},
        {"op": "complete", "messages": "hello"},
        {"op": "complete", "messages": [{"role": "assistant", "content": "hi"}]},
        {"op": "complete", "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]},
        {"op": "complete", "messages": [{"role": "system", "content": "a"}]},
        {"op": "complete", "messages": [{"role": "user", "content": "   "}]},
        {"op": "complete", "messages": [{"role": "user", "content": "x" * 100_001}]},
        {"op": "complete", "messages": good, "max_tokens": 0},
        {"op": "complete", "messages": good, "max_tokens": 5000},
        {"op": "complete", "messages": good, "max_tokens": True},
        {"op": "complete", "messages": good, "json": "yes"},
        {"op": "complete", "messages": good, "system": 5},
        {"op": "complete", "messages": [{"role": "user", "content": "a"}] * 51},
    ])


TOOLS = [{"name": "calendar_list", "description": "List CHIP's events between two days.",
          "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
         {"name": "gmail_search", "description": "Search CHIP's mail.",
          "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
         {"name": "remember", "description": "Save a fact to CHIP's shared memory.",
          "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}]
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="


def _tool_case(rng: random.Random) -> dict:
    tools = rng.sample(TOOLS, rng.randint(1, len(TOOLS)))
    if rng.random() < 0.3:
        tools = tools + [{"type": "computer_toolset_20260801"}]
    messages = [{"role": "user", "content": "What is on Friday? " + rng.choice(WORDS)}]
    if rng.random() < 0.6:
        call = {"type": "tool_use", "id": f"toolu_{rng.randrange(10**6)}", "name": tools[0].get("name", "screenshot"),
                "input": {"days": rng.randint(1, 7)}}
        result = {"type": "tool_result", "tool_use_id": call["id"],
                  "content": rng.choice(["Dinner with Sam at 8 PM", [{"type": "text", "text": "No events"}],
                                         [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PIXEL}}]])}
        messages += [{"role": "assistant", "content": [{"type": "text", "text": "Let me look."}, call]},
                     {"role": "user", "content": [result]}]
    inputs = {"op": "complete", "messages": messages, "tools": tools, "max_tokens": rng.choice([512, 1024])}
    if rng.random() < 0.5:
        blocks = [{"type": "text", "text": "Checking."},
                  {"type": "tool_use", "id": f"toolu_{rng.randrange(10**6)}", "name": rng.choice(tools).get("name", "left_click"),
                   "input": {"query": rng.choice(WORDS)}}]
        if rng.random() < 0.3:
            blocks.append({"type": "tool_use", "id": f"toolu_{rng.randrange(10**6)}", "name": "remember", "input": {"text": "Sam likes Lilia"}})
        stop = "tool_use"
    else:
        blocks, stop = [{"type": "text", "text": "Friday you have dinner with Sam at 8 PM."}], "end_turn"
    answer = _answer(rng, "")
    answer.update(content=blocks, stop_reason=stop)
    return {"input": inputs, "exchanges": [{"name": "external:anthropic", "request": _request(inputs), "response": answer}]}


def _invalid_tools(rng: random.Random) -> dict:
    good = _messages(rng)
    return rng.choice([
        {"op": "complete", "messages": good, "tools": [{"name": "bad name!", "description": "", "input_schema": {}}]},
        {"op": "complete", "messages": good, "tools": [TOOLS[0], TOOLS[0]]},
        {"op": "complete", "messages": good, "tools": [dict(TOOLS[0], cache=True)]},
        {"op": "complete", "messages": good, "tools": [{"type": "bash_20250124"}]},
        {"op": "complete", "messages": good, "tools": TOOLS * 11},
        {"op": "complete", "messages": good, "tools": "calendar"},
        {"op": "complete", "messages": good, "tools": [TOOLS[0]], "json": True},
        {"op": "complete", "messages": [{"role": "user", "content": [{"type": "video"}]}]},
        {"op": "complete", "messages": [{"role": "user", "content": [{"type": "tool_result", "content": "x"}]}]},
        {"op": "complete", "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "http://x"}}]}]},
        {"op": "complete", "messages": [{"role": "user", "content": []}]},
    ])


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = [_valid_case(rng) for _ in range(24)]
    cases += [_valid_case(rng, json_mode=True) for _ in range(12)]
    cases += [_failed_case(rng) for _ in range(8)]
    cases += [{"input": _invalid_inputs(rng), "exchanges": []} for _ in range(14)]
    cases += [_tool_case(rng) for _ in range(16)]
    cases += [{"input": _invalid_tools(rng), "exchanges": []} for _ in range(10)]
    return cases
