"""The redact brick: one-time codes and sign-in or reset links hidden."""

from bricks.redact import run


def hide(text: str, ctx: dict) -> dict:
    return ctx["io"].sibling("redact", {"op": "hide", "text": text}, run)
