"""The web worker: runs the browser brick's browser for the CHIP server, as a separate user.

It listens on a Unix socket (CHIP_WEB_SOCKET). Each connection is one request
(JSON, then end of input); for each one it runs the browser adapter's file in a
new process (a new, empty browser profile) and sends back its JSON answer. One
request runs at a time, so one browser at a time uses the server's memory.

systemd runs it as "chipweb" (chip-web.service, deploy/app/web_setup.sh): that
user can read none of CHIP's secrets or data, and the kernel blocks its network
access to private, loopback, link-local and metadata addresses. This file is
outside every brick, so the runner does not evaluate it; a human reviews each
change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bricks.browser.input.adapters import web  # noqa: E402

MAX_REQUEST = 10_000


def handle(conn: socket.socket, command: list[str]) -> None:
    conn.settimeout(10)
    data = b""
    while len(data) <= MAX_REQUEST:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
        if b"\n" in data:
            break
    try:
        request = json.loads(data.decode())
        if not isinstance(request, dict) or request.get("op") not in ("open", "look"):
            raise ValueError("op must be open or look")
        answer = web.run_worker({"op": request["op"], "url": str(request.get("url", ""))[:2000]}, command)
    except (ValueError, UnicodeDecodeError) as exc:
        answer = {"error": f"bad request: {exc}"[:200]}
    conn.settimeout(30)
    conn.sendall(json.dumps(answer).encode())


def main() -> None:
    path = os.environ["CHIP_WEB_SOCKET"]
    python = os.environ.get("CHIP_WEB_PYTHON", sys.executable)
    command = [python, str(Path(web.__file__).resolve())]
    if os.path.exists(path):
        os.remove(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o660)   # the group (chipweb) holds the CHIP server's user
    server.listen(8)
    print(f"web worker: listening on {path}", file=sys.stderr, flush=True)
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                handle(conn, command)
            except OSError as exc:
                print(f"web worker: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
