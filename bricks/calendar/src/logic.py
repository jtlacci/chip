"""Check the time range, ask Google once, and normalise the events."""

from datetime import datetime

from ..contract import MAX_DAYS, MAX_EVENTS, BrickInput, BrickOutput
from ..input.adapters import google_calendar


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _event(item: dict) -> dict:
    start, end = item.get("start", {}), item.get("end", {})
    all_day = "date" in start and "dateTime" not in start
    attendees = item.get("attendees") or []
    return {
        "id": item.get("id", ""),
        "title": (item.get("summary") or "").strip() or "(no title)",
        "start": start.get("dateTime") or start.get("date") or "",
        "end": end.get("dateTime") or end.get("date") or "",
        "all_day": all_day,
        "location": item.get("location", ""),
        "status": item.get("status", "confirmed"),
        "organizer": (item.get("organizer") or {}).get("email", ""),
        "attendees": len([a for a in attendees if isinstance(a, dict)]),
        "link": item.get("htmlLink", ""),
    }


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    if inputs.get("op") != "list":
        return {"ok": False, "error": "invalid: op must be list"}
    start, end = _time(inputs.get("time_min")), _time(inputs.get("time_max"))
    if start is None or end is None:
        return {"ok": False, "error": "invalid: time_min and time_max must be ISO 8601 with an offset"}
    if end <= start or (end - start).days > MAX_DAYS:
        return {"ok": False, "error": f"invalid: time_max must be after time_min, at most {MAX_DAYS} days later"}
    limit = inputs.get("max", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVENTS:
        return {"ok": False, "error": f"invalid: max must be 1 to {MAX_EVENTS}"}
    answer = google_calendar.events({"op": "list", "time_min": inputs["time_min"], "time_max": inputs["time_max"],
                                     "max": limit}, ctx)
    events = [_event(item) for item in answer.get("items", []) if item.get("status") != "cancelled"]
    events.sort(key=lambda e: (e["start"], e["id"]))
    return {"ok": True, "events": events[:limit]}
