"""Validate a store request, make one database call, and shape the answer."""

import json
import re

from ..contract import OPS, BrickInput, BrickOutput
from ..input.adapters import database

COLLECTION = re.compile(r"[a-z_]{1,32}")
MAX_KEY = 200
MAX_DOC_BYTES = 65536
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _key_problem(key) -> str | None:
    if not isinstance(key, str) or not 1 <= len(key) <= MAX_KEY:
        return f"key must be a string of 1 to {MAX_KEY} characters"
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        return "key may not contain control characters"
    return None


def _problem(inputs: dict) -> str | None:
    op = inputs.get("op")
    if op not in OPS:
        return f"op must be one of {', '.join(OPS)}"
    collection = inputs.get("collection")
    if not isinstance(collection, str) or not COLLECTION.fullmatch(collection):
        return "collection must match [a-z_]{1,32}"
    if op in ("get", "put", "delete"):
        problem = _key_problem(inputs.get("key"))
        if problem:
            return problem
    if op == "put":
        doc = inputs.get("doc")
        if not isinstance(doc, dict):
            return "doc must be a JSON object"
        if len(json.dumps(doc, separators=(",", ":"))) > MAX_DOC_BYTES:
            return f"doc is larger than {MAX_DOC_BYTES} bytes"
    if op == "scan":
        prefix = inputs.get("prefix", "")
        if not isinstance(prefix, str) or len(prefix) > MAX_KEY:
            return f"prefix must be a string of at most {MAX_KEY} characters"
        limit = inputs.get("limit", DEFAULT_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            return f"limit must be an integer from 1 to {MAX_LIMIT}"
    return None


def _request(inputs: dict) -> dict:
    op = inputs["op"]
    request = {"op": op, "collection": inputs["collection"]}
    if op in ("get", "put", "delete"):
        request["key"] = inputs["key"]
    if op == "put":
        request["doc"] = inputs["doc"]
    if op == "scan":
        request["prefix"] = inputs.get("prefix", "")
        request["limit"] = inputs.get("limit", DEFAULT_LIMIT)
    return request


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    problem = _problem(dict(inputs))
    if problem:
        return {"ok": False, "error": f"invalid: {problem}"}
    op = inputs["op"]
    answer = database.query(_request(dict(inputs)), ctx)
    if op == "get":
        if answer.get("doc") is None:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "doc": answer["doc"]}
    if op == "put":
        return {"ok": bool(answer.get("ok"))}
    if op == "delete":
        return {"ok": True, "deleted": bool(answer.get("deleted"))}
    docs = sorted(answer.get("docs", []), key=lambda d: d["key"])
    return {"ok": True, "docs": docs}
