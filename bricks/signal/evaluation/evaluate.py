"""signal is correct when it buys exactly when the recorded price is below the threshold."""

from ..contract import BrickInput

GATES = (("accuracy", ">=", 0.95),)


def build(record: dict) -> BrickInput:
    return record["input"]


def _price(record: dict) -> float:
    for exchange in record["exchanges"]:
        if exchange["name"] == "sibling:market_data" and "response" in exchange:
            return float(exchange["response"]["price"])
    raise LookupError("no recorded market_data price")


def score(record: dict, output: dict | None, later: list[dict]) -> dict[str, float]:
    expected = "buy" if _price(record) < record["input"]["threshold"] else "hold"
    return {"accuracy": 1.0 if output is not None and output.get("action") == expected else 0.0}


def generate(seed: int) -> list[dict]:
    import random

    rng = random.Random(seed)
    items = []
    for _ in range(40):
        symbol = rng.choice(["BTC", "ETH", "SOL"])
        price = round(rng.uniform(10, 100), 2)
        items.append({"input": {"symbol": symbol, "threshold": round(rng.uniform(10, 100), 2)},
                      "exchanges": [{"name": "sibling:market_data", "request": {"symbol": symbol},
                                     "response": {"symbol": symbol, "price": price}}]})
    return items
