from ..input.adapters import market_data


def execute(inputs, ctx):
    """Buy when the latest price is below the threshold, else hold."""
    price = market_data.latest_price(inputs["symbol"], ctx)
    if price < inputs["threshold"]:
        return {"action": "buy"}
    return {"action": "hold"}
