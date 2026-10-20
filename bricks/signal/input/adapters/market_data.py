from bricks.market_data import run


def latest_price(symbol: str, ctx: dict) -> float:
    response = ctx["io"].sibling("market_data", {"symbol": symbol}, run)
    return float(response["price"])
