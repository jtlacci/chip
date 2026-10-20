"""The store sibling: posts, chats, messages, activity, approvals and settings."""

from bricks.store import run


def call(request: dict, ctx: dict) -> dict:
    return ctx["io"].sibling("store", request, run)
