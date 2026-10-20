"""market_data is correct when it returns the mid price the source gave for the symbol."""

from ..contract import BrickInput

GATES = (("matches_source", ">=", 1.0),)


def build(record: dict) -> BrickInput:
    return record["input"]


def _source_price(record: dict) -> float | None:
    for exchange in record["exchanges"]:
        if exchange["name"] == "external:hyperliquid" and "response" in exchange:
            price = exchange["response"].get(record["input"]["symbol"])
            return None if price is None else float(price)
    return None


def score(record: dict, output: dict | None, later: list[dict]) -> dict[str, float]:
    expected = _source_price(record)
    ok = (output is not None and expected is not None
          and output.get("symbol") == record["input"]["symbol"] and output.get("price") == expected)
    return {"matches_source": 1.0 if ok else 0.0}


def generate(seed: int) -> list[dict]:
    """Synthetic allMids responses, until real captured data is pinned in data.lock."""
    import random

    rng = random.Random(seed)
    symbols = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "AVAX"]
    items = []
    for _ in range(40):
        mids = {s: f"{rng.uniform(0.05, 120000):.6g}" for s in symbols}
        items.append({"input": {"symbol": rng.choice(symbols)},
                      "exchanges": [{"name": "external:hyperliquid", "request": {"type": "allMids"},
                                     "response": mids}]})
    return items
