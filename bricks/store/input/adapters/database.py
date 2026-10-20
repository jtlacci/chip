"""The database: one SQLite file. Its path comes from CHIP_DB (default chip.db)."""

import json
import os
import sqlite3


def query(request: dict, ctx: dict) -> dict:
    """One database call. The request is the store's own: {op, collection, key?, doc?, prefix?, limit?}."""
    return ctx["io"].external("database", request, _query_live)


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(os.environ.get("CHIP_DB", "chip.db"))
    connection.execute("CREATE TABLE IF NOT EXISTS records (collection TEXT NOT NULL, key TEXT NOT NULL,"
                       " doc TEXT NOT NULL, PRIMARY KEY (collection, key))")
    return connection


def _query_live(request: dict) -> dict:
    with _connect() as connection:
        op, collection = request["op"], request["collection"]
        if op == "get":
            row = connection.execute("SELECT doc FROM records WHERE collection = ? AND key = ?",
                                     (collection, request["key"])).fetchone()
            return {"doc": json.loads(row[0]) if row else None}
        if op == "put":
            connection.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?)",
                               (collection, request["key"], json.dumps(request["doc"], sort_keys=True)))
            return {"ok": True}
        if op == "delete":
            cursor = connection.execute("DELETE FROM records WHERE collection = ? AND key = ?",
                                        (collection, request["key"]))
            return {"deleted": cursor.rowcount > 0}
        if op == "scan":
            rows = connection.execute(
                "SELECT key, doc FROM records WHERE collection = ? AND substr(key, 1, ?) = ? ORDER BY key LIMIT ?",
                (collection, len(request["prefix"]), request["prefix"], request["limit"])).fetchall()
            return {"docs": [{"key": key, "doc": json.loads(doc)} for key, doc in rows]}
        raise ValueError(f"unknown op {op!r}")
