"""The framework runtime: brick runs, I/O routing and capture. Standard library only.

Every brick's ``runner/run.py`` calls :func:`run_brick`. Every adapter does its
I/O through ``ctx["io"]``:

- ``ctx["io"].external(name, request, call)`` for a source outside the repository
- ``ctx["io"].sibling(brick, request, run)`` for a sibling brick's ``run``

In production the io is :class:`LiveIO`. It makes the call and, when the
``BRICK_CAPTURE_DIR`` environment variable is set, appends one record to a local
spool file. The evaluation runner installs its own io with the same two
methods, to replay recorded data. Brick code never chooses the mode.

What is captured:

- every external exchange, request and response in full (app-boundary data);
- every sibling exchange, with the full response only when the sibling declares
  ``DETERMINISTIC = False``, else a hash (a replay can compute it again);
- every brick run, input in full, output in full only for a nondeterministic
  brick, else a hash.
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
import functools
import hashlib
import importlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Any, Callable
import uuid

from . import _yaml

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ENV = "BRICK_CAPTURE_DIR"
TREES_ENV = "BRICK_TREES"


class OutOfEvidence(LookupError):
    """A replay has no recorded response for this request."""


def canonical(value: Any) -> bytes:
    """The one byte form of a JSON value: sorted keys, no spaces, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@functools.lru_cache(maxsize=None)
def is_deterministic(brick: str) -> bool:
    """A brick is deterministic only when its contract says so."""
    contract = importlib.import_module(f"bricks.{brick}.contract")
    return getattr(contract, "DETERMINISTIC", False) is True


@functools.lru_cache(maxsize=None)
def tree_hash(brick: str) -> str:
    """The Git tree hash of the brick folder this process runs.

    A deployment may pin it in ``BRICK_TREES`` (a JSON map of brick to hash);
    otherwise Git is asked; otherwise it is unknown.
    """
    pinned = os.environ.get(TREES_ENV)
    if pinned:
        try:
            value = json.loads(pinned).get(brick)
            if isinstance(value, str):
                return value
        except (ValueError, AttributeError):
            pass
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"HEAD:bricks/{brick}"],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@functools.lru_cache(maxsize=None)
def load_config(brick: str) -> dict[str, Any]:
    path = ROOT / "bricks" / brick / "input" / "config.yml"
    if not path.is_file():
        return {}
    return _yaml.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- capture

_spool_lock = threading.Lock()
# A number for each captured exchange in this process. Two identical calls in
# one run would otherwise be identical records, and the runner's store (which
# names a record by the hash of its content) would keep only one of them.
_seq = itertools.count()


def _capture(record: dict[str, Any]) -> None:
    folder = os.environ.get(CAPTURE_ENV)
    if not folder:
        return
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    with _spool_lock, open(path / f"{day}-{os.getpid()}.jsonl", "a", encoding="utf-8") as spool:
        spool.write(line)


def _error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


# ---------------------------------------------------------------- runs and io

_frame: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("brick_frame", default=None)
_io: contextvars.ContextVar[Any] = contextvars.ContextVar("brick_io", default=None)


def _where() -> dict[str, str | None]:
    frame = _frame.get()
    return {"trace_id": frame[0] if frame else None, "run_id": frame[1] if frame else None}


class LiveIO:
    """Production io: make the call, then capture the exchange."""

    def external(self, name: str, request: Any, call: Callable[[Any], Any]) -> Any:
        record = {"kind": "exchange", "name": f"external:{name}", "request": request,
                  "request_hash": digest(request), "time": now(), "seq": next(_seq), **_where()}
        try:
            response = call(request)
        except Exception as exc:
            _capture({**record, "error": _error(exc)})
            raise
        _capture({**record, "response": response, "response_hash": digest(response)})
        return response

    def sibling(self, brick: str, request: Any, call: Callable[[Any], Any]) -> Any:
        record = {"kind": "exchange", "name": f"sibling:{brick}", "request": request,
                  "request_hash": digest(request), "time": now(), "seq": next(_seq), **_where()}
        try:
            response = call(request)
        except Exception as exc:
            _capture({**record, "error": _error(exc)})
            raise
        record["response_hash"] = digest(response)
        if not is_deterministic(brick):
            record["response"] = response
        _capture(record)
        return response


LIVE = LiveIO()


def current_io() -> Any:
    return _io.get() or LIVE


def use_io(io: Any) -> contextvars.Token:
    """Install an io for this context. The evaluation runner calls this."""
    return _io.set(io)


def run_brick(brick: str, inputs: Any, execute: Callable[[Any, dict[str, Any]], Any]) -> Any:
    """Run one brick: build its context, call its logic, record the run."""
    parent = _frame.get()
    trace_id = parent[0] if parent else uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    deterministic = is_deterministic(brick)
    record: dict[str, Any] = {
        "kind": "run", "brick": brick, "tree": tree_hash(brick),
        "deterministic": deterministic, "trace_id": trace_id, "run_id": run_id,
        "parent_id": parent[1] if parent else None, "input": inputs,
        "input_hash": digest(inputs), "time": now(),
    }
    ctx = {"run_id": run_id, "trace_id": trace_id, "config": load_config(brick), "io": current_io()}
    token = _frame.set((trace_id, run_id))
    try:
        output = execute(inputs, ctx)
    except Exception as exc:
        _capture({**record, "error": _error(exc)})
        raise
    finally:
        _frame.reset(token)
    record["output_hash"] = digest(output)
    if not deterministic:
        record["output"] = output
    _capture(record)
    return output
