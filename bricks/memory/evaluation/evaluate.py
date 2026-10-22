"""How the memory brick is evaluated. Evaluation loop only.

A reference from the contract predicts the one database call each request
makes, and what the brick gives back:

- calls_exact:     a valid remember makes one insert with the normalized text; a
                   search makes one search with the contract's terms; an invalid
                   request, a secret, or a query with no words makes no call.
- matches:         the output is the database's answer (search: at most limit items).
- no_secrets:      a text with a planted one-time code or sign-in link is refused,
                   and never reaches the database.
- rejects_invalid: an invalid request fails with "invalid:".
"""

import random
import re

GATES: tuple[tuple[str, str, float], ...] = (
    ("calls_exact", ">=", 1.0),
    ("matches", ">=", 1.0),
    ("no_secrets", ">=", 1.0),
    ("rejects_invalid", ">=", 1.0),
)

WORD = re.compile(r"[a-z0-9]+")


def _terms(query: str) -> list[str]:
    out: list[str] = []
    for word in WORD.findall(query.lower()):
        if len(word) >= 2 and word not in out:
            out.append(word)
    return out[:8]


def expected_call(inputs: dict) -> dict | None:
    """The one call the contract names, or None (no call)."""
    if inputs.get("op") == "remember":
        text = inputs.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 1000 or not inputs.get("now") \
                or not isinstance(inputs.get("now"), str) or inputs.get("_secret"):
            return None
        return {"op": "insert", "text": " ".join(text.split()), "time": inputs["now"]}
    if inputs.get("op") == "search":
        query, limit = inputs.get("query"), inputs.get("limit", 5)
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            return None
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20 or not _terms(query):
            return None
        return {"op": "search", "terms": _terms(query), "limit": limit}
    return None


def _valid(inputs: dict) -> bool:
    if inputs.get("op") == "remember":
        text = inputs.get("text")
        return isinstance(text, str) and bool(text.strip()) and len(text) <= 1000 and isinstance(inputs.get("now"), str) \
            and bool(inputs["now"]) and not inputs.get("_secret")
    if inputs.get("op") == "search":
        query, limit = inputs.get("query"), inputs.get("limit", 5)
        return isinstance(query, str) and bool(query.strip()) and len(query) <= 200 and isinstance(limit, int) \
            and not isinstance(limit, bool) and 1 <= limit <= 20
    return False


def build(record: dict) -> dict:
    return {k: v for k, v in record["input"].items() if not k.startswith("_")}


def score(record: dict, output: dict | None, later: list[dict], calls=()) -> dict[str, float]:
    inputs = record["input"]
    out = output if isinstance(output, dict) else {}
    made = [c for c in calls if c.get("name") == "external:memory_db"]
    want = expected_call(inputs)
    terms = {"calls_exact": float([c.get("request") for c in made] == ([want] if want else []))}
    if inputs.get("_secret"):
        terms["no_secrets"] = float(not made and out.get("ok") is False and str(out.get("error", "")).startswith("invalid:"))
    elif not _valid(inputs):
        terms["rejects_invalid"] = float(out.get("ok") is False and str(out.get("error", "")).startswith("invalid:"))
    else:
        response = made[0].get("response", {}) if made else {}
        if inputs["op"] == "remember":
            ok = out == {"ok": True, "id": str(response.get("id", "")), "new": bool(response.get("new"))}
        else:
            items = [{"id": str(i.get("id", "")), "text": str(i.get("text", "")), "time": str(i.get("time", ""))}
                     for i in response.get("items", [])][:inputs.get("limit", 5)] if want else []
            ok = out == {"ok": True, "items": items}
        terms["matches"] = float(ok)
    terms["correct"] = float(all(v >= 1.0 for v in terms.values()))
    return terms


# ---------------------------------------------------------------- synthetic data

NOW = "2026-10-21T20:00:00-04:00"
FACTS = ["Sam likes Lilia in Williamsburg", "John's size is L for hoodies", "The team retro is on Fridays at noon",
         "Jamie prefers Dark Shadow over Heather Frost", "Mom's birthday is on March 3",
         "  CHIP books   dinner for two   at 8 PM  ", "The office is on the 5th floor"]
SECRETS = ["Your verification code is 482913", "Reset here: https://acme.example/reset?token=9f8e7d",
           "Bank login code 771204, do not share", "Sign in with https://app.example/magic-link/abc123"]
QUERIES = ["Where does Sam like to eat?", "hoodie size", "retro", "a", "Mom birthday March",
           "Dark Shadow or Heather Frost?!", "?? !!", "the the the office office"]


def _fact(rng, i):
    return {"id": f"f{i:04x}", "text": rng.choice(FACTS).strip(), "time": "2026-10-20T10:00:00-04:00"}


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    for _ in range(20):
        inputs = {"op": "remember", "text": rng.choice(FACTS), "now": NOW}
        call = expected_call(inputs)
        cases.append({"input": inputs, "exchanges": [{"name": "external:memory_db", "request": call,
                                                      "response": {"id": f"{rng.getrandbits(64):016x}", "new": rng.random() < 0.7}}]})
    for _ in range(20):
        inputs = {"op": "search", "query": rng.choice(QUERIES)}
        if rng.random() < 0.6:
            inputs["limit"] = rng.choice([1, 3, 5, 20])
        call = expected_call(inputs)
        exchanges = []
        if call:
            items = [_fact(rng, rng.randrange(9999)) for _ in range(rng.randint(0, 6))]
            exchanges = [{"name": "external:memory_db", "request": call, "response": {"items": items}}]
        cases.append({"input": inputs, "exchanges": exchanges})
    for _ in range(10):
        cases.append({"input": {"op": "remember", "text": rng.choice(SECRETS), "now": NOW, "_secret": True}, "exchanges": []})
    for _ in range(12):
        cases.append({"input": rng.choice([
            {"op": "forget", "id": "f1"}, {"op": "remember", "text": "", "now": NOW}, {"op": "remember", "text": "x" * 1001, "now": NOW},
            {"op": "remember", "text": "fine", "now": ""}, {"op": "remember", "text": 5, "now": NOW},
            {"op": "search", "query": ""}, {"op": "search", "query": "x" * 201}, {"op": "search", "query": "retro", "limit": 0},
            {"op": "search", "query": "retro", "limit": 21}, {"op": "search", "query": "retro", "limit": True}]), "exchanges": []})
    return cases
