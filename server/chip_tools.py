"""CHIP's tools for the Hermes agent: the calendar, Gmail and the shared memory, through the bricks.

The CHIP server serves them on a Unix socket (CHIP_TOOLS_SOCKET). Hermes runs
as another user and reaches them through server/chip_mcp.py, a small MCP server
that forwards each call here, so the Google tokens never leave the CHIP server.

Every text that leaves a tool goes through hide(): a one-time code near a code
word, or a sign-in, verification or reset link, is replaced. The tools only read,
except remember (a fact to the shared memory, refused when it holds a secret).
Writes to Gmail or the calendar come later, through approvals.

This file is outside every brick, so the runner does not evaluate it; a human
reviews each change.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import os
import re
import socket
import sys
import threading

from bricks.calendar import run as calendar
from bricks.gmail import run as gmail
from bricks.memory import run as memory

MAX_TEXT = 8000
MAX_REQUEST = 20_000

TOOLS = [
    {"name": "calendar_list", "description": "CHIP's calendar events between two days (inclusive), in start order.",
     "inputSchema": {"type": "object", "properties": {"from_day": {"type": "string", "description": "YYYY-MM-DD"},
                                                       "to_day": {"type": "string", "description": "YYYY-MM-DD"}},
                     "required": ["from_day", "to_day"]}},
    {"name": "gmail_search", "description": "CHIP's mail that matches a Gmail search query (for example 'from:jamie "
                                            "newer_than:7d'). An empty query lists the newest mail.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "gmail_read", "description": "One message by its id, with its text.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "memory_search", "description": "Facts in CHIP's shared memory that have the words.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "remember", "description": "Save one fact to CHIP's shared memory, for example a person's preference. "
                                        "Every person who uses CHIP shares this memory.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
]

# ------------------------------------------------------------------ secrets

LINK = re.compile(r"https?://\S+", re.I)
LINK_WORDS = ("reset", "password", "passwd", "verify", "verification", "confirm", "token", "magic", "signin", "sign-in",
              "login", "log-in", "auth", "otp")
CODE = re.compile(r"(?<![\w$.,:/-])(\d{4,8}|(?=[A-Z0-9]*\d[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w%:/-]|[.,]\d)")
CODE_WORDS = ("code", "verification", "verify", "otp", "passcode", "pin", "one-time", "one time", "security", "login",
              "sign-in", "sign in", "2fa", "two-factor", "password")


def hide(text: str) -> tuple[str, int]:
    """Replace one-time codes and sign-in or reset links. Returns (text, how many were hidden)."""
    count = 0

    def link(match: re.Match) -> str:
        nonlocal count
        if any(word in match.group(0).lower() for word in LINK_WORDS):
            count += 1
            return "[link hidden]"
        return match.group(0)

    text = LINK.sub(link, text)
    lower = text.lower()
    spans = [m.span() for m in CODE.finditer(text)
             if any(w in lower[max(0, m.start() - 40):m.end() + 40] for w in CODE_WORDS)]
    for start, end in reversed(spans):
        text = text[:start] + "[code hidden]" + text[end:]
        count += 1
    return text, count


def _clean(value):
    """hide() on every text in a JSON value."""
    if isinstance(value, str):
        return hide(value)[0]
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


# ------------------------------------------------------------------ the tools

def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def call(name: str, args: dict, now: datetime | None = None) -> dict:
    """One tool call. Returns a JSON object (with "error" when the call failed)."""
    now = now or datetime.now(timezone.utc)
    if name == "calendar_list":
        start, end = _day(args.get("from_day")), _day(args.get("to_day"))
        if start is None or end is None or end < start or (end - start).days > 62:
            return {"error": "from_day and to_day must be days (YYYY-MM-DD), to_day not before from_day, at most 62 days"}
        found = calendar({"op": "list", "time_min": datetime.combine(start, time.min, timezone.utc).isoformat(),
                          "time_max": datetime.combine(end + timedelta(days=1), time.min, timezone.utc).isoformat(),
                          "max": 100})
        if not found.get("ok"):
            return {"error": f"calendar: {found.get('error', 'failed')}"[:200]}
        keep = ("id", "title", "start", "end", "all_day", "location", "status")
        return _clean({"events": [{k: e.get(k) for k in keep} for e in found["events"] if e.get("status") != "cancelled"]})
    if name == "gmail_search":
        listed = gmail({"op": "search", "query": str(args.get("query", "")).strip()[:300] or "newer_than:14d", "max": 20})
        if not listed.get("ok"):
            return {"error": f"gmail: {listed.get('error', 'failed')}"[:200]}
        keep = ("id", "from", "subject", "date", "snippet")
        return _clean({"messages": [{k: m.get(k) for k in keep} for m in listed["messages"]]})
    if name == "gmail_read":
        got = gmail({"op": "get", "id": str(args.get("id", ""))[:100]})
        if not got.get("ok"):
            return {"error": f"gmail: {got.get('error', 'no such message')}"[:200]}
        message = got["message"]
        keep = ("id", "from", "to", "subject", "date", "snippet")
        out = {k: message.get(k) for k in keep}
        out["text"] = str(message.get("text", ""))[:MAX_TEXT]
        return _clean(out)
    if name == "memory_search":
        found = memory({"op": "search", "query": str(args.get("query", ""))[:200] or "-", "limit": 10})
        if not found.get("ok"):
            return {"facts": []}
        return _clean({"facts": [{"text": f.get("text"), "saved": f.get("time")} for f in found.get("items", [])]})
    if name == "remember":
        text = " ".join(str(args.get("text", "")).split())
        if not text or len(text) > 1000:
            return {"error": "a fact is 1 to 1000 characters"}
        if hide(text)[1]:
            return {"error": "a one-time code or a sign-in link is never saved"}
        saved = memory({"op": "remember", "text": text, "now": now.replace(microsecond=0).isoformat()})
        return {"saved": bool(saved.get("ok"))} if saved.get("ok") else {"error": f"memory: {saved.get('error')}"[:200]}
    return {"error": f"there is no tool {name!r}"}


# ------------------------------------------------------------------ the socket

def log(message: str) -> None:
    print(f"chip tools: {message}", file=sys.stderr, flush=True)


def _handle(conn: socket.socket) -> None:
    conn.settimeout(10)
    data = b""
    while b"\n" not in data and len(data) <= MAX_REQUEST:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    try:
        request = json.loads(data.decode())
        if request.get("op") == "list":
            answer = {"tools": TOOLS}
        else:
            name, args = str(request.get("tool", "")), request.get("args") if isinstance(request.get("args"), dict) else {}
            answer = call(name, args)
            log(f"{name} -> {'error: ' + answer['error'] if 'error' in answer else 'ok'}")
    except (ValueError, UnicodeDecodeError) as exc:
        answer = {"error": f"bad request: {exc}"[:200]}
    except Exception as exc:  # noqa: BLE001 -- one failed call must not stop the server
        answer = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    conn.settimeout(30)
    conn.sendall(json.dumps(answer).encode() + b"\n")


def serve(path: str, group: str | None = None) -> None:
    """Serve the tools on a Unix socket, one thread for each connection."""
    if os.path.exists(path):
        os.remove(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o660)
    if group:
        import grp
        os.chown(path, -1, grp.getgrnam(group).gr_gid)
    server.listen(16)
    log(f"listening on {path}")

    def loop() -> None:
        while True:
            conn, _ = server.accept()

            def run(c=conn) -> None:
                with c:
                    try:
                        _handle(c)
                    except OSError as exc:
                        log(f"{type(exc).__name__}: {exc}")
            threading.Thread(target=run, daemon=True).start()

    threading.Thread(target=loop, name="chip-tools", daemon=True).start()
