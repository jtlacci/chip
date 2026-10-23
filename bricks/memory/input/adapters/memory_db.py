"""The memory database: one SQLite file with a full-text index.

Its path comes from CHIP_MEMORY_DB (default: memory.db beside CHIP_DB).
"""

import hashlib
import os
from pathlib import Path
import sqlite3


def call(request: dict, ctx: dict) -> dict:
    return ctx["io"].external("memory_db", request, _live)


def _path() -> str:
    if os.environ.get("CHIP_MEMORY_DB"):
        return os.environ["CHIP_MEMORY_DB"]
    return str(Path(os.environ.get("CHIP_DB", "chip.db")).resolve().parent / "memory.db")


# The porter stemmer: a search for "preference" finds "prefers", "dinners" finds "dinner".
TABLE = "fts5(id UNINDEXED, text, time UNINDEXED, tokenize='porter unicode61')"


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_path())
    row = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'facts'").fetchone()
    if row is None:
        connection.execute(f"CREATE VIRTUAL TABLE facts USING {TABLE}")
    elif "porter" not in row[0]:
        # A table from before the stemmer: copy its facts into a new one.
        with connection:
            connection.execute(f"CREATE VIRTUAL TABLE facts_porter USING {TABLE}")
            connection.execute("INSERT INTO facts_porter (id, text, time) SELECT id, text, time FROM facts")
            connection.execute("DROP TABLE facts")
            connection.execute("ALTER TABLE facts_porter RENAME TO facts")
    return connection


def _live(request: dict) -> dict:
    with _connect() as db:
        if request["op"] == "insert":
            fact_id = hashlib.sha256(request["text"].encode()).hexdigest()[:16]
            if db.execute("SELECT 1 FROM facts WHERE id = ?", (fact_id,)).fetchone():
                return {"id": fact_id, "new": False}
            db.execute("INSERT INTO facts (id, text, time) VALUES (?, ?, ?)", (fact_id, request["text"], request["time"]))
            return {"id": fact_id, "new": True}
        if request["op"] == "search":
            # Each term is quoted, so no term can be FTS syntax.
            match = " OR ".join(f'"{term}"' for term in request["terms"])
            rows = db.execute("SELECT id, text, time FROM facts WHERE facts MATCH ? ORDER BY bm25(facts) LIMIT ?",
                              (match, int(request["limit"]))).fetchall()
            return {"items": [{"id": r[0], "text": r[1], "time": r[2]} for r in rows]}
    raise ValueError(f"unknown op {request.get('op')!r}")
