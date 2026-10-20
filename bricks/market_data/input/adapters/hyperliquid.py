"""The Hyperliquid info API: all mid prices in one call."""

import json
import urllib.request

INFO_URL = "https://api.hyperliquid.xyz/info"


def all_mids(ctx: dict) -> dict[str, float]:
    """Every perpetual's mid price, by symbol."""
    raw = ctx["io"].external("hyperliquid", {"type": "allMids"}, _fetch_live)
    return {symbol: float(price) for symbol, price in raw.items()}


def _fetch_live(request: dict) -> dict:
    data = json.dumps(request).encode()
    call = urllib.request.Request(INFO_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(call, timeout=10) as response:
        return json.loads(response.read().decode())
