"""How the store brick is evaluated. Evaluation loop only.

A reference model of the contract gives the expected output for each case,
from the case input and the recorded database response. A valid request must
make exactly the recorded database call; an invalid request must make none
(an unrecorded call is out of evidence, and the policy caps that share).
"""

import json
import random
import re

from ..contract import BrickInput

GATES: tuple[tuple[str, str, float], ...] = (
    ("matches", ">=", 1.0),
    ("rejects_invalid", ">=", 1.0),
)

COLLECTION = re.compile(r"[a-z_]{1,32}")


def build(record: dict) -> BrickInput:
    return record["input"]


def _valid(inputs: dict) -> bool:
    op = inputs.get("op")
    if op not in ("get", "put", "delete", "scan"):
        return False
    if not isinstance(inputs.get("collection"), str) or not COLLECTION.fullmatch(inputs["collection"]):
        return False
    if op in ("get", "put", "delete"):
        key = inputs.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or any(ord(c) < 32 or ord(c) == 127 for c in key):
            return False
    if op == "put":
        doc = inputs.get("doc")
        if not isinstance(doc, dict) or len(json.dumps(doc, separators=(",", ":"))) > 65536:
            return False
    if op == "scan":
        prefix, limit = inputs.get("prefix", ""), inputs.get("limit", 100)
        if not isinstance(prefix, str) or len(prefix) > 200:
            return False
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            return False
    return True


def expected(record: dict) -> dict | None:
    """The expected output, or None for an invalid input (any error is right)."""
    inputs = record["input"]
    if not _valid(inputs):
        return None
    answers = [e["response"] for e in record.get("exchanges", []) if e["name"] == "external:database"]
    answer = answers[0] if answers else {}
    op = inputs["op"]
    if op == "get":
        return {"ok": True, "doc": answer["doc"]} if answer.get("doc") is not None else {"ok": False, "error": "not_found"}
    if op == "put":
        return {"ok": True}
    if op == "delete":
        return {"ok": True, "deleted": bool(answer.get("deleted"))}
    return {"ok": True, "docs": sorted(answer.get("docs", []), key=lambda d: d["key"])}


def score(record: dict, output: dict | None, later: list[dict]) -> dict[str, float]:
    want = expected(record)
    if want is None:
        ok = isinstance(output, dict) and output.get("ok") is False and str(output.get("error", "")).startswith("invalid:")
        return {"rejects_invalid": 1.0 if ok else 0.0, "matches": 1.0 if ok else 0.0}
    return {"matches": 1.0 if output == want else 0.0}


def _doc(rng: random.Random) -> dict:
    return {"title": rng.choice(["Meeting with Jacob", "Open House", "Free this weekend?"]),
            "n": rng.randint(0, 99), "tags": rng.sample(["a", "b", "c", "d"], rng.randint(0, 3))}


def _case(rng: random.Random) -> dict:
    collection = rng.choice(["posts", "chats", "messages", "users", "sessions"])
    key = rng.choice(["p1", "chat-1/2026-10-20T15:00:00Z/u1", "google:123", "x" * 40])
    kind = rng.choice(["get", "get_missing", "put", "delete", "scan", "invalid"])
    if kind == "get":
        inputs = {"op": "get", "collection": collection, "key": key}
        return {"input": inputs, "exchanges": [{"name": "database", "request": inputs, "response": {"doc": _doc(rng)}}]}
    if kind == "get_missing":
        inputs = {"op": "get", "collection": collection, "key": key}
        return {"input": inputs, "exchanges": [{"name": "database", "request": inputs, "response": {"doc": None}}]}
    if kind == "put":
        inputs = {"op": "put", "collection": collection, "key": key, "doc": _doc(rng)}
        return {"input": inputs, "exchanges": [{"name": "database", "request": inputs, "response": {"ok": True}}]}
    if kind == "delete":
        inputs = {"op": "delete", "collection": collection, "key": key}
        return {"input": inputs, "exchanges": [{"name": "database", "request": inputs,
                                                "response": {"deleted": rng.random() < 0.5}}]}
    if kind == "scan":
        prefix = rng.choice(["", "chat-1/", "p"])
        limit = rng.choice([None, 1, 50, 500])
        inputs = {"op": "scan", "collection": collection, "prefix": prefix}
        if limit is not None:
            inputs["limit"] = limit
        request = {"op": "scan", "collection": collection, "prefix": prefix, "limit": limit or 100}
        docs = [{"key": f"{prefix}{k}", "doc": _doc(rng)} for k in rng.sample(["c", "a", "b", "e"], rng.randint(0, 4))]
        return {"input": inputs, "exchanges": [{"name": "database", "request": request, "response": {"docs": docs}}]}
    bad = rng.choice([
        {"op": "drop", "collection": "posts"},
        {"op": "get", "collection": "Posts", "key": "a"},
        {"op": "get", "collection": "posts", "key": ""},
        {"op": "get", "collection": "posts", "key": "a\nb"},
        {"op": "put", "collection": "posts", "key": "a", "doc": ["not", "an", "object"]},
        {"op": "put", "collection": "posts", "key": "a", "doc": {"big": "x" * 70000}},
        {"op": "scan", "collection": "posts", "limit": 0},
        {"op": "scan", "collection": "posts", "limit": True},
        {"op": "get", "collection": "a" * 33, "key": "a"},
    ])
    return {"input": bad}


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    return [_case(rng) for _ in range(60)]
