"""Hermes: CHIP's agent, through its OpenAI-compatible API server.

CHIP_HERMES_URL is the server (default http://127.0.0.1:8642) and
CHIP_HERMES_KEY_FILE holds its key, outside every record. The request is
streamed, so Hermes's tool progress events (hermes.tool.progress) give the
steps. The server's Hermes already runs the profile's commit, model and persona
(deploy/app/hermes_setup.sh), so the profile is not sent.
"""

import json
import os
from pathlib import Path
import urllib.request

TIMEOUT = 300


def reply(request: dict, ctx: dict) -> dict:
    return ctx["io"].external("hermes", request, _live)


def _live(request: dict) -> dict:
    key = Path(os.environ["CHIP_HERMES_KEY_FILE"]).read_text().strip()
    body = {"model": "hermes-agent", "stream": True,
            "messages": [{"role": "system", "content": request["system"]}] + request["messages"]}
    http = urllib.request.Request(os.environ.get("CHIP_HERMES_URL", "http://127.0.0.1:8642") + "/v1/chat/completions",
                                  data=json.dumps(body).encode(), method="POST",
                                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(http, timeout=TIMEOUT) as response:
        return read_stream(response)


def read_stream(response) -> dict:
    """{"text", "steps"} from a streamed chat completion with Hermes's tool progress events."""
    text, steps, event = [], [], None
    for raw in response:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            if event == "hermes.tool.progress":
                if data.get("status") == "running":
                    tool, label = str(data.get("tool", "")), str(data.get("label") or "")
                    steps.append({"tool": tool, "label": "" if label == tool else label})
            else:
                for choice in data.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if isinstance(piece, str):
                        text.append(piece)
        elif not line:
            event = None
    return {"text": "".join(text), "steps": steps}
