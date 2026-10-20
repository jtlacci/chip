"""signal: buy when the mid price is below a threshold, else hold."""

from typing import TypedDict


CONTRACT_VERSION = 1

LANE = "strict"

DETERMINISTIC = True

SIBLING_DEPENDENCIES = {"market_data": "eventual"}

OWNED_STATE: tuple[str, ...] = ()

# This file is human-only: no agent loop may change it. Inputs and outputs must
# be JSON values, because the runtime records them.


class BrickInput(TypedDict):
    symbol: str
    threshold: float


class BrickOutput(TypedDict):
    action: str  # "buy" or "hold"
