from ..input.adapters import hyperliquid


def execute(inputs, ctx):
    mids = hyperliquid.all_mids(ctx)
    symbol = inputs["symbol"]
    if symbol not in mids:
        raise KeyError(f"Hyperliquid has no mid price for {symbol!r}")
    return {"symbol": symbol, "price": mids[symbol]}
