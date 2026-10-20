"""How the calendar brick is evaluated. Evaluation loop only.

A reference model of the contract normalises the recorded Google answer. A
valid request makes exactly one call with the recorded request; an invalid one
makes none. Cancelled events never show.
"""

from datetime import datetime, timedelta, timezone
import random

GATES: tuple[tuple[str, str, float], ...] = (
    ("matches", ">=", 1.0),
    ("no_cancelled", ">=", 1.0),
    ("rejects_invalid", ">=", 1.0),
)


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _valid(inputs: dict) -> bool:
    start, end = _time(inputs.get("time_min")), _time(inputs.get("time_max"))
    limit = inputs.get("max", 50)
    return (inputs.get("op") == "list" and start is not None and end is not None and start < end
            and (end - start).days <= 62 and isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 250)


def expected(record: dict) -> dict | None:
    inputs = record["input"]
    if not _valid(inputs):
        return None
    answer = next((e["response"] for e in record.get("exchanges", []) if e["name"] == "external:google_calendar"), {})
    events = []
    for item in answer.get("items", []):
        if item.get("status") == "cancelled":
            continue
        start, end = item.get("start", {}), item.get("end", {})
        events.append({
            "id": item.get("id", ""),
            "title": (item.get("summary") or "").strip() or "(no title)",
            "start": start.get("dateTime") or start.get("date") or "",
            "end": end.get("dateTime") or end.get("date") or "",
            "all_day": "date" in start and "dateTime" not in start,
            "location": item.get("location", ""),
            "status": item.get("status", "confirmed"),
            "organizer": (item.get("organizer") or {}).get("email", ""),
            "attendees": len([a for a in item.get("attendees") or [] if isinstance(a, dict)]),
            "link": item.get("htmlLink", ""),
        })
    events.sort(key=lambda e: (e["start"], e["id"]))
    return {"ok": True, "events": events[:inputs.get("max", 50)]}


def build(record: dict) -> dict:
    return record["input"]


def score(record: dict, output: dict | None, later: list[dict]) -> dict[str, float]:
    want = expected(record)
    if want is None:
        ok = isinstance(output, dict) and output.get("ok") is False and str(output.get("error", "")).startswith("invalid:")
        return {"matches": float(ok), "rejects_invalid": float(ok)}
    terms = {"matches": 1.0 if output == want else 0.0}
    answer = next((e["response"] for e in record.get("exchanges", []) if e["name"] == "external:google_calendar"), {})
    cancelled = {i.get("id") for i in answer.get("items", []) if i.get("status") == "cancelled"}
    if cancelled:
        shown = {e.get("id") for e in (output or {}).get("events", [])} if isinstance(output, dict) else set()
        terms["no_cancelled"] = 0.0 if cancelled & shown else 1.0
    return terms


def _item(rng: random.Random, day: datetime, n: int) -> dict:
    kind = rng.choice(["timed", "timed", "all_day", "cancelled", "untitled", "busy"])
    start = day + timedelta(hours=rng.randint(7, 20), minutes=rng.choice([0, 15, 30]))
    item = {"id": f"ev{n}", "status": "cancelled" if kind == "cancelled" else rng.choice(["confirmed", "tentative"]),
            "summary": rng.choice(["Meeting with Jacob", "Team updates and product retro", "Frontier Markets after party",
                                   "  Dentist  ", "Open House New York"]),
            "htmlLink": f"https://www.google.com/calendar/event?eid=ev{n}"}
    if kind == "all_day":
        item["start"], item["end"] = {"date": day.date().isoformat()}, {"date": (day + timedelta(days=1)).date().isoformat()}
    else:
        offset = rng.choice([timezone.utc, timezone(timedelta(hours=-4))])
        item["start"] = {"dateTime": start.astimezone(offset).isoformat()}
        item["end"] = {"dateTime": (start + timedelta(minutes=rng.choice([30, 60, 90]))).astimezone(offset).isoformat()}
    if kind == "untitled":
        item.pop("summary")
    if rng.random() < 0.5:
        item["location"] = rng.choice(["Club Reign", "Zoom", "123 Grand St, New York"])
    if rng.random() < 0.6:
        item["organizer"] = {"email": "jacob@example.com"}
    if kind == "busy":
        item["attendees"] = [{"email": f"p{i}@example.com"} for i in range(rng.randint(1, 6))] + ["not-a-dict"]
    return item


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    base = datetime(2026, 10, 19, tzinfo=timezone.utc)
    for i in range(50):
        day = base + timedelta(days=rng.randint(0, 20))
        span = rng.choice([1, 7, 30])
        inputs = {"op": "list", "time_min": day.isoformat(), "time_max": (day + timedelta(days=span)).isoformat()}
        if rng.random() < 0.4:
            inputs["max"] = rng.choice([1, 5, 50, 250])
        items = [_item(rng, day + timedelta(days=rng.randint(0, span - 1)), i * 100 + n) for n in range(rng.randint(0, 9))]
        request = {"op": "list", "time_min": inputs["time_min"], "time_max": inputs["time_max"], "max": inputs.get("max", 50)}
        cases.append({"input": inputs, "exchanges": [{"name": "google_calendar", "request": request, "response": {"items": items}}]})
    bad = [{"op": "create"}, {"op": "list", "time_min": "tomorrow", "time_max": "2026-10-21T00:00:00+00:00"},
           {"op": "list", "time_min": "2026-10-20T00:00:00", "time_max": "2026-10-21T00:00:00"},
           {"op": "list", "time_min": "2026-10-21T00:00:00+00:00", "time_max": "2026-10-20T00:00:00+00:00"},
           {"op": "list", "time_min": "2026-01-01T00:00:00+00:00", "time_max": "2026-06-01T00:00:00+00:00"},
           {"op": "list", "time_min": "2026-10-20T00:00:00+00:00", "time_max": "2026-10-21T00:00:00+00:00", "max": 0},
           {"op": "list", "time_min": "2026-10-20T00:00:00+00:00", "time_max": "2026-10-21T00:00:00+00:00", "max": 251}]
    cases += [{"input": inputs} for inputs in bad]
    return cases
