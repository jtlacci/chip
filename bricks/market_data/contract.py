"""market_data: the latest Hyperliquid mid price for one perpetual."""

from typing import TypedDict


CONTRACT_VERSION = 1

LANE = "strict"

# Live prices change from one call to the next, so outputs are stored in full.
DETERMINISTIC = False

SIBLING_DEPENDENCIES: dict[str, str] = {}

OWNED_STATE: tuple[str, ...] = ()

# This file is human-only: no agent loop may change it. Inputs and outputs must
# be JSON values, because the runtime records them.


class BrickInput(TypedDict):
    symbol: str  # a Hyperliquid perpetual, for example "BTC"


class BrickOutput(TypedDict):
    symbol: str
    price: float  # the mid price
