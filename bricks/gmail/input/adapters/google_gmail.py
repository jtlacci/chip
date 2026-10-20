"""Gmail for CHIP's own account.

The live call gets an access token from the refresh token file
(CHIP_GOOGLE_TOKEN_FILE), the client ID (CHIP_GOOGLE_CLIENT_ID) and the client
secret file (CHIP_GOOGLE_CLIENT_SECRET_FILE). None of them is in the request or
the response, so none is recorded. Attachment data is never fetched.
"""

import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_token: dict = {}


def call(request: dict, ctx: dict) -> dict:
    """{"op": "list", "q", "max"} -> {"messages": [{id, threadId}]};
    {"op": "get", "id", "format": "metadata" | "full"} -> the raw Gmail message, or {"missing": true}."""
    return ctx["io"].external("gmail", request, _call_live)


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


def _get(url: str) -> dict:
    with urlopen(Request(url, headers={"Authorization": f"Bearer {_access_token()}"}), timeout=20) as response:
        return json.load(response)


def _strip_attachments(part: dict) -> dict:
    body = part.get("body", {})
    if body.get("attachmentId"):
        part = {**part, "body": {"attachmentId": body["attachmentId"], "size": body.get("size", 0)}}
    if "parts" in part:
        part = {**part, "parts": [_strip_attachments(p) for p in part["parts"]]}
    return part


def _call_live(request: dict) -> dict:
    if request["op"] == "list":
        answer = _get(f"{API}?{urlencode({'q': request['q'], 'maxResults': request['max']})}")
        return {"messages": [{"id": m["id"], "threadId": m.get("threadId", "")} for m in answer.get("messages", [])]}
    url = f"{API}/{quote(request['id'], safe='')}?format={request['format']}"
    if request["format"] == "metadata":
        url += "&" + "&".join(f"metadataHeaders={h}" for h in ("From", "To", "Subject", "Date"))
    try:
        message = _get(url)
    except HTTPError as error:
        if error.code == 404:
            return {"missing": True}
        raise
    keep = {k: message[k] for k in ("id", "threadId", "labelIds", "snippet", "payload") if k in message}
    if "payload" in keep:
        keep["payload"] = _strip_attachments(keep["payload"])
    return keep
