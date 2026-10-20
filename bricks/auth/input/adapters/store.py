"""The store sibling: auth's users, sessions and invites."""

from bricks.store import run


def call(request: dict, ctx: dict) -> dict:
    return ctx["io"].sibling("store", request, run)


def get(collection: str, key: str, ctx: dict) -> dict | None:
    answer = call({"op": "get", "collection": collection, "key": key}, ctx)
    return answer["doc"] if answer.get("ok") else None


def put(collection: str, key: str, doc: dict, ctx: dict) -> bool:
    return bool(call({"op": "put", "collection": collection, "key": key, "doc": doc}, ctx).get("ok"))


def delete(collection: str, key: str, ctx: dict) -> None:
    call({"op": "delete", "collection": collection, "key": key}, ctx)


def any_user(ctx: dict) -> bool:
    answer = call({"op": "scan", "collection": "users", "prefix": "", "limit": 1}, ctx)
    return bool(answer.get("docs"))
