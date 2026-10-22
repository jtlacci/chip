"""The model brick: one request shape for every provider."""

from bricks.model import run


def complete(request: dict, ctx: dict) -> dict:
    return ctx["io"].sibling("model", request, run)
