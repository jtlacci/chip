"""store: CHIP's records, in named collections, over one database.

Every other brick keeps its state here. A record is a JSON object under a
(collection, key) pair. The brick validates each request and makes exactly one
database call for a valid one, and none for an invalid one.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"

# The output is a function of the input and the recorded database response.
DETERMINISTIC = True

SIBLING_DEPENDENCIES: dict[str, str] = {}

OWNED_STATE: tuple[str, ...] = ("database:chip",)

# Human-only. Inputs and outputs are JSON values, because the runtime records them.

OPS = ("get", "put", "delete", "scan")


class BrickInput(TypedDict, total=False):
    """op: get | put | delete | scan. collection: [a-z_]{1,32}.

    key: 1 to 200 characters, no control characters (get, put, delete).
    doc: a JSON object of at most 65536 bytes as compact JSON (put).
    prefix: a key prefix, may be empty (scan). limit: 1 to 500, default 100 (scan).
    """

    op: str
    collection: str
    key: str
    doc: dict
    prefix: str
    limit: int


class BrickOutput(TypedDict, total=False):
    """ok, and one of: doc (get), docs [{key, doc}] sorted by key (scan),
    deleted (delete), or error ("invalid: ..." or "not_found")."""

    ok: bool
    doc: dict
    docs: list
    deleted: bool
    error: str
