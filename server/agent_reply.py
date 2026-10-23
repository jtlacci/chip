"""CHIP answers in chat: the Hermes agent writes the reply, and the app server saves it.

After a person posts a message, a worker thread gives the assistant brick the
chat's recent messages, who wrote the last one and the time. The brick builds
CHIP's prompt and the conversation, asks the server's Hermes (CHIP_HERMES_URL,
key in CHIP_HERMES_KEY_FILE), and hides secrets in the reply. Hermes uses CHIP's
tools (the calendar, Gmail, the shared memory) through server/chip_mcp.py, and
the web. The server saves:
- CHIP's reply, as a message in the same chat;
- each tool step, as an item in the activity log (the agent panel's Activity tab).

One chat is answered at a time, in order. This file is outside every brick, so
the runner does not evaluate it; a human reviews each change.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import queue
import sys
import threading

from bricks.assistant import run as assistant
from bricks.store import run as store

CHIP = "chip"
MESSAGES = 30


def log(message: str) -> None:
    print(f"agent reply: {message}", file=sys.stderr, flush=True)


def _file_ready(name: str) -> bool:
    path = os.environ.get(name)
    return bool(path) and Path(path).is_file() and bool(Path(path).read_text().strip())


STEP_TITLES = {"mcp__chip__calendar_list": "Checked the calendar", "mcp__chip__gmail_search": "Searched the mail",
               "mcp__chip__gmail_read": "Read an email", "mcp__chip__memory_search": "Searched the memory",
               "mcp__chip__remember": "Saved to the memory", "web_search": "Searched the web",
               "web_extract": "Read a web page"}


def hermes_answer(chat_id: str) -> dict:
    """CHIP's reply from the Hermes agent, through the assistant brick."""
    chat = store({"op": "get", "collection": "chats", "key": chat_id})
    title = (chat.get("doc") or {}).get("title", "General") if chat.get("ok") else "General"
    scanned = store({"op": "scan", "collection": "messages", "prefix": f"{chat_id}/", "limit": 500}).get("docs", [])
    messages = [d["doc"] for d in scanned][-MESSAGES:]
    if not messages or messages[-1].get("author") == CHIP:
        return {"skipped": "nothing to answer"}
    last = messages[-1]
    person = store({"op": "get", "collection": "users", "key": last.get("author", "")})
    role = (person.get("doc") or {}).get("role", "guest") if person.get("ok") else "guest"
    started = datetime.now(timezone.utc)
    out = assistant({"op": "reply", "now": started.replace(microsecond=0).isoformat(),
                     "chat": {"id": chat_id, "title": title},
                     "messages": [{k: m.get(k) for k in ("author", "name", "text", "time")} for m in messages],
                     "user": {"user_id": last.get("author"), "name": last.get("name"), "role": role}})
    if not out.get("ok"):
        return {"error": out.get("error")}
    done = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    message = {"author": CHIP, "name": "CHIP", "text": out["text"], "time": done}
    saved = store({"op": "put", "collection": "messages", "key": f"{chat_id}/{done}/{CHIP}", "doc": message})
    if not saved.get("ok"):
        return {"error": f"store: {saved.get('error')}"}
    store({"op": "put", "collection": "chats", "key": chat_id, "doc": {"title": title, "updated": done}})
    seconds = int((datetime.now(timezone.utc) - started).total_seconds())
    steps = out.get("steps", [])
    for index, step in enumerate(steps):
        store({"op": "put", "collection": "activity", "key": f"{done}-{index:02d}",
               "doc": {"title": STEP_TITLES.get(step.get("tool"), "Used a tool"), "detail": step.get("label", ""),
                       "time": done}})
    store({"op": "put", "collection": "activity", "key": f"{done}-{len(steps):02d}",
           "doc": {"title": "Answered", "detail": f"{seconds} s, {len(steps)} steps", "time": done}})
    return {"replied": True, "engine": "hermes", "seconds": seconds, "steps": len(steps)}


class Replier:
    def __init__(self) -> None:
        self._jobs: queue.Queue[str] = queue.Queue()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="agent-reply", daemon=True).start()

    def new_message(self, chat_id: str) -> None:
        self._jobs.put(chat_id)

    def _loop(self) -> None:
        while True:
            chat_id = self._jobs.get()
            if not _file_ready("CHIP_HERMES_KEY_FILE"):
                log("waiting: CHIP_HERMES_KEY_FILE is not set up (deploy/app/hermes_setup.sh)")
                continue
            try:
                result = hermes_answer(chat_id)
                if result.get("error"):
                    log(str(result))
            except Exception as exc:  # noqa: BLE001 -- one failed answer must not stop the next
                log(f"failed: {type(exc).__name__}: {str(exc)[:300]}")
