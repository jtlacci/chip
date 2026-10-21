"""The feed refresh: the app server gives the feed brick its sources and saves its posts.

A thread checks every CHECK seconds. It reads the feed instructions, CHIP's
calendar (from a day ago to two weeks ahead), its mail (the last week) and the
sources that already have a post, and calls the feed brick only when there is
an item it has not looked at yet. The sources the feed has looked at are kept
(settings/feed_state), so an item the instructions leave out is not sent to the
model again on every check; the sources that have a post are kept there too. When the instructions change, that list is
cleared and the feed looks at every item again; wake() starts a check at once.

It runs only when CHIP's Google account is connected (the refresh token file)
and the model key file exists (CHIP_ANTHROPIC_KEY_FILE). This file is outside
every brick, so the runner does not evaluate it; a human reviews each change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import threading
import time

from bricks.calendar import run as calendar
from bricks.feed import run as feed
from bricks.gmail import run as gmail
from bricks.store import run as store

CHECK = 300
PAST = timedelta(days=1)
AHEAD = timedelta(days=14)
MAIL_QUERY = "newer_than:7d"
STATE = ("settings", "feed_state")


def log(message: str) -> None:
    print(f"feed refresh: {message}", file=sys.stderr, flush=True)


def ready() -> str | None:
    """None when a refresh can run, else the reason it cannot."""
    for name in ("CHIP_GOOGLE_TOKEN_FILE", "CHIP_ANTHROPIC_KEY_FILE"):
        path = os.environ.get(name)
        if not path or not Path(path).is_file() or not Path(path).read_text().strip():
            return f"{name} is not set up"
    return None


def _doc(collection: str, key: str) -> dict:
    answer = store({"op": "get", "collection": collection, "key": key})
    return answer.get("doc", {}) if answer.get("ok") else {}


def refresh_once() -> dict:
    """One check. Returns what it did."""
    moment = datetime.now(timezone.utc).replace(microsecond=0)
    now = moment.isoformat()
    instructions = _doc("settings", "feed_instructions").get("text", "")
    state = _doc(*STATE)
    seen = set(state.get("seen", [])) if state.get("instructions") == instructions else set()
    events = calendar({"op": "list", "time_min": (moment - PAST).isoformat(), "time_max": (moment + AHEAD).isoformat(),
                       "max": 100})
    mail = gmail({"op": "search", "query": MAIL_QUERY, "max": 30})
    if not events.get("ok") or not mail.get("ok"):
        return {"error": f"sources: {events.get('error') or mail.get('error')}"}
    # The sources that have a post. (A store scan returns at most 500 records and has no paging,
    # so the refresh keeps this list itself.)
    posted = set(state.get("posted", []))
    sources = {f"calendar:{e['id']}" for e in events["events"]} | {f"gmail:{m['id']}" for m in mail["messages"]}
    if not sources - posted - seen:
        return {"new": 0}
    answer = feed({"op": "refresh", "now": now, "instructions": instructions, "events": events["events"],
                   "mail": mail["messages"], "posted": sorted(posted | seen)})
    if not answer.get("ok"):
        return {"error": f"feed: {answer.get('error')}"}
    for post in answer["posts"]:
        doc = {k: post[k] for k in ("source", "time", "icon", "title", "body", "kind") if k in post}
        if "card" in post:
            doc["card"] = post["card"]
        saved = store({"op": "put", "collection": "posts", "key": post["key"], "doc": doc})
        if not saved.get("ok"):
            return {"error": f"store: {saved.get('error')}"}
        posted.add(post["source"])
    # Every item the feed was given has now been looked at, posted or not.
    seen |= sources - posted
    store({"op": "put", "collection": STATE[0], "key": STATE[1],
           "doc": {"instructions": instructions, "seen": sorted(seen), "posted": sorted(posted), "updated": now}})
    return {"new": len(sources - posted), "posts": len(answer["posts"]), "skipped": len(answer.get("skipped", []))}


class Refresher:
    def __init__(self) -> None:
        self._wake = threading.Event()

    def wake(self) -> None:
        """Check now (for example after the instructions changed)."""
        self._wake.set()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="feed-refresh", daemon=True).start()

    def _loop(self) -> None:
        time.sleep(10)   # let the server start first
        while True:
            reason = ready()
            if reason:
                log(f"waiting: {reason}")
            else:
                try:
                    result = refresh_once()
                    if result.get("error") or result.get("posts"):
                        log(str(result))
                except Exception as exc:  # noqa: BLE001 -- one failed check must not stop the next
                    log(f"failed: {type(exc).__name__}: {str(exc)[:300]}")
            self._wake.wait(CHECK)
            self._wake.clear()
