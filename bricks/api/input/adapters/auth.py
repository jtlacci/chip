"""The auth sibling: every permission decision."""

from bricks.auth import run


def call(request: dict, ctx: dict) -> dict:
    return ctx["io"].sibling("auth", request, run)
