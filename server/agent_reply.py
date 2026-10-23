"""CHIP answers in chat: the app server gives the agent brick a snapshot and saves what it gives back.

After a person posts a message, a worker thread makes the snapshot (the chat's
recent messages, who wrote the last one, CHIP's calendar, its recent mail with
the text of the newest, and the memory facts that match the message), calls the
agent brick, and saves:
- CHIP's reply, as a message in the same chat;
- the facts to remember, through the memory brick;
- each step, as an item in the activity log (the agent panel's Activity tab).

When the agent's answer waits for web tasks ("pending"), the server runs each
(at most 2) with the browser brick, through the web worker (CHIP_WEB_SOCKET),
and asks the agent again with the results in "web". Without the web worker, a
task's result says that the web is not set up.

With CHIP_AGENT_ENGINE=hermes, the Hermes agent answers instead (hermes_answer),
through the assistant brick: it builds CHIP's prompt and the conversation, asks
the server's Hermes (CHIP_HERMES_URL, key in CHIP_HERMES_KEY_FILE), and hides
secrets in the reply. Hermes uses CHIP's tools through server/chip_mcp.py.

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
from bricks.assistant import run as assistant
from bricks.browser import run as browser
from bricks.calendar import run as calendar
from bricks.gmail import run as gmail
from bricks.memory import run as memory
from bricks.store import run as store

CHIP = "chip"
MESSAGES = 30
MAIL_QUERY = "newer_than:14d"
READ_NEWEST = 5
TITLES = {"calendar_list": "Checked the calendar", "gmail_search": "Searched the mail", "gmail_read": "Read an email",
          "memory_search": "Searched the memory", "remember": "Saved to the memory", "web_task": "Read the web"}
MAX_WEB = 2
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
    web_steps = []
    if out.get("ok") and out.get("pending"):
        inputs["web"] = []
        for job in out["pending"][:MAX_WEB]:
            result, step = web_task(job)
            inputs["web"].append(result)
            web_steps.append(step)
        out = agent(inputs)
    if not out.get("ok"):
        return {"error": out.get("error")}
    out["steps"] = web_steps + list(out.get("steps", []))
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


def web_task(job: dict) -> tuple[dict, dict]:
    """(the result for the agent, a step for the activity log)."""
    args = job.get("input") if isinstance(job, dict) and isinstance(job.get("input"), dict) else {}
    goal = str(args.get("goal", "")).strip()[:1000]
    if not os.environ.get("CHIP_WEB_SOCKET"):
        result = {"goal": goal, "answer": "The web is not set up on this server.", "sources": [], "stopped": "fail"}
    else:
        request = {"op": "task", "goal": goal or "(no goal)"}
        if isinstance(args.get("start_url"), str) and args["start_url"]:
            request["start_url"] = args["start_url"]
        done = browser(request)
        if done.get("ok"):
            result = {"goal": goal, "answer": done.get("answer", ""), "sources": done.get("sources", []),
                      "stopped": done.get("stopped", "fail")}
        else:
            result = {"goal": goal, "answer": f"The web task failed: {done.get('error', '')}"[:300], "sources": [],
                      "stopped": "fail"}
    return result, {"tool": "web_task", "summary": f"{goal[:120]} ({result['stopped']})"}


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
            if os.environ.get("CHIP_AGENT_ENGINE") == "hermes" and not _file_ready("CHIP_HERMES_KEY_FILE"):
                log("waiting: CHIP_HERMES_KEY_FILE is not set up")
                continue
            if os.environ.get("CHIP_AGENT_ENGINE") != "hermes" and not _file_ready("CHIP_ANTHROPIC_KEY_FILE"):
                log("waiting: CHIP_ANTHROPIC_KEY_FILE is not set up")
                continue
            try:
                engine = hermes_answer if os.environ.get("CHIP_AGENT_ENGINE") == "hermes" else answer
                result = engine(chat_id)
                if result.get("error"):
                    log(str(result))
            except Exception as exc:  # noqa: BLE001 -- one failed answer must not stop the next
                log(f"failed: {type(exc).__name__}: {str(exc)[:300]}")
