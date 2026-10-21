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
- provider_errors: when the provider fails, the output is an error that
  starts with "provider:", and nothing crashes.
- rejects_invalid: an invalid request fails with "invalid:".
"""

import json
import random

GATES: tuple[tuple[str, str, float], ...] = (
    ("request_shape", ">=", 1.0),
    ("matches", ">=", 1.0),
    ("json", ">=", 1.0),
    ("provider_errors", ">=", 1.0),
    ("rejects_invalid", ">=", 1.0),
)

MODEL = "claude-sonnet-5-5"   # the model the synthetic records were made with
FIELDS = {"model", "max_tokens", "system", "messages"}
STOPS = {"end_turn": "end", "stop_sequence": "end", "max_tokens": "max_tokens"}


def _valid(inputs: dict) -> bool:
    messages = inputs.get("messages")
    if inputs.get("op") != "complete" or not isinstance(messages, list) or not 1 <= len(messages) <= 50:
        return False
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") \
                or not isinstance(m.get("content"), str) or not m["content"].strip():
            return False
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        return False
    system = inputs.get("system", "")
    if not isinstance(system, str) or len(system) + sum(len(m["content"]) for m in messages) > 100_000:
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
    return (request.get("messages") == want and system_ok and request.get("max_tokens") == inputs.get("max_tokens", 1024)
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
        if inputs.get("json"):
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


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = [_valid_case(rng) for _ in range(24)]
    cases += [_valid_case(rng, json_mode=True) for _ in range(12)]
    cases += [_failed_case(rng) for _ in range(8)]
    cases += [{"input": _invalid_inputs(rng), "exchanges": []} for _ in range(14)]
    return cases
