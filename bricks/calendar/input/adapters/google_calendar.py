"""Google Calendar for CHIP's own account.

The live call gets an access token from the refresh token file
(CHIP_GOOGLE_TOKEN_FILE), the client ID (CHIP_GOOGLE_CLIENT_ID) and the client
secret file (CHIP_GOOGLE_CLIENT_SECRET_FILE). None of them is in the request or
the response, so none is recorded.
"""

import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
KEEP = ("id", "status", "summary", "start", "end", "location", "organizer", "attendees", "htmlLink")
_token: dict = {}


def events(request: dict, ctx: dict) -> dict:
    """request: {"op": "list", "time_min", "time_max", "max"} -> {"items": [raw Google events]}."""
    return ctx["io"].external("google_calendar", request, _list_live)


def _access_token() -> str:
    if _token.get("expires", 0) > time.time() + 60:
        return _token["value"]
    body = urlencode({
        "client_id": os.environ["CHIP_GOOGLE_CLIENT_ID"],
        "client_secret": Path(os.environ["CHIP_GOOGLE_CLIENT_SECRET_FILE"]).read_text().strip(),
        "refresh_token": Path(os.environ["CHIP_GOOGLE_TOKEN_FILE"]).read_text().strip(),
        "grant_type": "refresh_token",
    }).encode()
    with urlopen(Request(TOKEN_URL, data=body), timeout=20) as response:
        answer = json.load(response)
    _token.update(value=answer["access_token"], expires=time.time() + int(answer.get("expires_in", 3000)))
    return _token["value"]


def _list_live(request: dict) -> dict:
    query = urlencode({"timeMin": request["time_min"], "timeMax": request["time_max"], "singleEvents": "true",
                       "orderBy": "startTime", "maxResults": request["max"], "showDeleted": "false"})
    url = f"{EVENTS_URL}?{query}"
    with urlopen(Request(url, headers={"Authorization": f"Bearer {_access_token()}"}), timeout=20) as response:
        answer = json.load(response)
    # Keep only the fields CHIP uses, so the record holds no more of the calendar than needed.
    return {"items": [{k: item[k] for k in KEEP if k in item} for item in answer.get("items", [])]}
