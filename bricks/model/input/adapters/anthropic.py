"""The Anthropic Messages API.

The request is a Messages API body: model, max_tokens, system, messages. The
live call adds the key from the file in CHIP_ANTHROPIC_KEY_FILE; the key is in
a header, never in the request or the response, so it is never recorded.
"""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"


def complete(request: dict, ctx: dict) -> dict:
    return ctx["io"].external("anthropic", request, _live)


def _live(request: dict) -> dict:
    key = Path(os.environ["CHIP_ANTHROPIC_KEY_FILE"]).read_text().strip()
    data = json.dumps(request).encode()
    headers = {"content-type": "application/json", "anthropic-version": VERSION, "x-api-key": key}
    try:
        with urlopen(Request(URL, data=data, headers=headers), timeout=120) as response:
            return json.load(response)
    except HTTPError as exc:
        # The status only: the body of an error can echo the request.
        raise RuntimeError(f"the model API answered HTTP {exc.code}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"the model API cannot be reached: {type(exc).__name__}") from None
