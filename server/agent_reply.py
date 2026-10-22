"""CHIP answers in chat: the app server gives the agent brick a snapshot and saves what it gives back.

After a person posts a message, a worker thread makes the snapshot (the chat's
recent messages, who wrote the last one, CHIP's calendar, its recent mail with
the text of the newest, and the memory facts that match the message), calls the
agent brick, and saves:
- CHIP's reply, as a message in the same chat;
- the facts to remember, through the memory brick;
- each step, as an item in the activity log (the agent panel's Activity tab).

One chat is answered at a time, in order. It runs only when the model key file
exists (CHIP_ANTHROPIC_KEY_FILE). Without CHIP's Google account, the snapshot has
no calendar and no mail. This file is outside every brick, so the runner does
not evaluate it; a human reviews each change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import queue
import sys
import threading

from bricks.agent import run as agent
from bricks.calendar import run as calendar
from bricks.gmail import run as gmail
from bricks.memory import run as memory
from bricks.store import run as store

CHIP = "chip"
MESSAGES = 30
MAIL_QUERY = "newer_than:14d"
READ_NEWEST = 5
TITLES = {"calendar_list": "Checked the calendar", "gmail_search": "Searched the mail", "gmail_read": "Read an email",
          "memory_search": "Searched the memory", "remember": "Saved to the memory"}


def log(message: str) -> None:
    print(f"agent reply: {message}", file=sys.stderr, flush=True)


def _file_ready(name: str) -> bool:
    path = os.environ.get(name)
    return bool(path) and Path(path).is_file() and bool(Path(path).read_text().strip())


def snapshot(chat_id: str, moment: datetime) -> dict | None:
    chat = store({"op": "get", "collection": "chats", "key": chat_id})
    title = (chat.get("doc") or {}).get("title", "General") if chat.get("ok") else "General"
    scanned = store({"op": "scan", "collection": "messages", "prefix": f"{chat_id}/", "limit": 500}).get("docs", [])
    messages = [d["doc"] for d in scanned][-MESSAGES:]
    if not messages or messages[-1].get("author") == CHIP:
        return None   # nothing to answer
    last = messages[-1]
    person = store({"op": "get", "collection": "users", "key": last.get("author", "")})
    role = (person.get("doc") or {}).get("role", "guest") if person.get("ok") else "guest"
    events, mail = [], []
    if _file_ready("CHIP_GOOGLE_TOKEN_FILE"):
        found = calendar({"op": "list", "time_min": (moment - timedelta(days=1)).isoformat(),
                          "time_max": (moment + timedelta(days=14)).isoformat(), "max": 100})
        events = found.get("events", []) if found.get("ok") else []
        listed = gmail({"op": "search", "query": MAIL_QUERY, "max": 30})
        mail = listed.get("messages", []) if listed.get("ok") else []
        for item in mail[:READ_NEWEST]:
            full = gmail({"op": "get", "id": item["id"]})
            if full.get("ok"):
                item["text"] = str(full["message"].get("text", ""))[:5000]
    facts = memory({"op": "search", "query": str(last.get("text", ""))[:200], "limit": 10})
    return {"op": "reply", "now": moment.isoformat(), "chat": {"id": chat_id, "title": title},
            "messages": [{k: m.get(k) for k in ("author", "name", "text", "time")} for m in messages],
            "user": {"user_id": last.get("author"), "name": last.get("name"), "role": role},
            "events": events, "mail": mail, "memory": facts.get("items", []) if facts.get("ok") else []}


def answer(chat_id: str) -> dict:
    moment = datetime.now(timezone.utc).replace(microsecond=0)
    inputs = snapshot(chat_id, moment)
    if inputs is None:
        return {"skipped": "nothing to answer"}
    out = agent(inputs)
    if not out.get("ok"):
        return {"error": out.get("error")}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    message = {"author": CHIP, "name": "CHIP", "text": out["text"], "time": now}
    saved = store({"op": "put", "collection": "messages", "key": f"{chat_id}/{now}/{CHIP}", "doc": message})
    if not saved.get("ok"):
        return {"error": f"store: {saved.get('error')}"}
    chat = store({"op": "get", "collection": "chats", "key": chat_id})
    title = (chat.get("doc") or {}).get("title", "General") if chat.get("ok") else "General"
    store({"op": "put", "collection": "chats", "key": chat_id, "doc": {"title": title, "updated": now}})
    for text in out.get("remember", []):
        memory({"op": "remember", "text": text, "now": now})
    for index, step in enumerate(out.get("steps", [])):
        store({"op": "put", "collection": "activity", "key": f"{now}-{index:02d}",
               "doc": {"title": TITLES.get(step.get("tool"), "Used a tool"), "detail": str(step.get("summary", ""))[:200],
                       "time": now}})
    return {"replied": True, "steps": len(out.get("steps", [])), "remembered": len(out.get("remember", []))}


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
            if not _file_ready("CHIP_ANTHROPIC_KEY_FILE"):
                log("waiting: CHIP_ANTHROPIC_KEY_FILE is not set up")
                continue
            try:
                result = answer(chat_id)
                if result.get("error"):
                    log(str(result))
            except Exception as exc:  # noqa: BLE001 -- one failed answer must not stop the next
                log(f"failed: {type(exc).__name__}: {str(exc)[:300]}")
