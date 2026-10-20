"""CHIP's HTTP server: it serves the web app and turns each /api/* request into one api brick run.

Tokens never enter a brick. The server mints session and invite tokens, keeps
them in HttpOnly cookies or invite links, and passes only their SHA-256 hashes
to the api brick (the runtime records every brick input).

Run it locally (Google sign-in comes in M3; CHIP_DEV=1 adds /auth/dev):

    (cd bricks/web/app && node build.js)
    CHIP_DEV=1 CHIP_DB=chip.db python3 server/serve.py --port 8000

This file is outside every brick, so the runner does not evaluate it; a human
reviews each change (CODEOWNERS).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bricks.api import run as api  # noqa: E402

DIST = ROOT / "bricks" / "web" / "app" / "dist"
SESSION_COOKIE, INVITE_COOKIE = "chip_session", "chip_invite"
MAX_BODY = 65536
SECURE = os.environ.get("CHIP_SECURE") == "1"
DEV = os.environ.get("CHIP_DEV") == "1"
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: https:; style-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cookie(name: str, value: str, max_age: int) -> str:
    flags = f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
    return flags + ("; Secure" if SECURE else "")


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "chip"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def end_headers(self) -> None:
        for name, value in HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    # ------------------------------------------------------------ helpers

    def cookies(self) -> dict[str, str]:
        found = {}
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" in part:
                name, value = part.strip().split("=", 1)
                found[name] = value
        return found

    def json_reply(self, status: int, body: dict, extra: list[tuple[str, str]] = ()) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or urlsplit(origin).netloc == self.headers.get("Host")

    # ------------------------------------------------------------ routes

    def api(self, method: str) -> None:
        path = urlsplit(self.path).path
        if method != "GET" and (not self.same_origin() or "application/json" not in self.headers.get("Content-Type", "")):
            self.json_reply(403, {"error": "cross_site_request"})
            return
        if path.startswith("/api/session") and method == "POST":
            self.json_reply(404, {"error": "not_found"})   # sign-in goes through /auth/*, never the browser
            return
        request = {"method": method, "path": path, "now": now()}
        session = self.cookies().get(SESSION_COOKIE)
        if session:
            request["session_hash"] = digest(session)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.json_reply(413, {"error": "too_large"})
            return
        if length:
            try:
                request["body"] = json.loads(self.rfile.read(length))
            except ValueError:
                self.json_reply(400, {"error": "bad_json"})
                return
        token = None
        if path == "/api/invites" and method == "POST":
            token = secrets.token_urlsafe(32)
            request["invite_hash"] = digest(token)
        answer = api(request)
        body, extra = dict(answer["body"]), []
        if token and answer["status"] == 201:
            body["invite_url"] = f"/join/{token}"
        if path == "/api/session" and method == "DELETE":
            extra.append(("Set-Cookie", cookie(SESSION_COOKIE, "", 0)))
        self.json_reply(answer["status"], body, extra)

    def join(self) -> None:
        token = urlsplit(self.path).path.split("/", 2)[2]
        self.send_response(303)
        self.send_header("Set-Cookie", cookie(INVITE_COOKIE, token, 7 * 24 * 3600))
        self.send_header("Location", "/auth/dev" if DEV else "/auth/google")
        self.end_headers()

    def dev_sign_in(self) -> None:
        name = (parse_qs(urlsplit(self.path).query).get("as") or ["dev"])[0][:40]
        token = secrets.token_urlsafe(32)
        request = {"method": "POST", "path": "/api/session", "now": now(), "session_hash": digest(token),
                   "identity": {"sub": f"dev-{name}", "email": f"{name}@localhost", "name": name.title()}}
        invite = self.cookies().get(INVITE_COOKIE)
        if invite:
            request["invite_hash"] = digest(invite)
        answer = api(request)
        if answer["status"] != 200:
            self.json_reply(answer["status"], answer["body"])
            return
        self.send_response(303)
        self.send_header("Set-Cookie", cookie(SESSION_COOKIE, token, 30 * 24 * 3600))
        self.send_header("Set-Cookie", cookie(INVITE_COOKIE, "", 0))
        self.send_header("Location", "/feed")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self.api("GET")
        elif path.startswith("/join/"):
            self.join()
        elif path == "/auth/dev" and DEV:
            self.dev_sign_in()
        else:
            if "." not in path.rsplit("/", 1)[-1]:
                self.path = "/index.html"   # the app routes /feed, /chat and /agent itself
            super().do_GET()

    def do_POST(self) -> None:
        self.api("POST") if urlsplit(self.path).path.startswith("/api/") else self.json_reply(404, {"error": "not_found"})

    def do_PUT(self) -> None:
        self.api("PUT") if urlsplit(self.path).path.startswith("/api/") else self.json_reply(404, {"error": "not_found"})

    def do_DELETE(self) -> None:
        self.api("DELETE") if urlsplit(self.path).path.startswith("/api/") else self.json_reply(404, {"error": "not_found"})


def main() -> None:
    parser = argparse.ArgumentParser(description="CHIP's HTTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not (DIST / "index.html").is_file():
        sys.exit("build the app first: (cd bricks/web/app && node build.js)")
    print(f"CHIP on http://{args.host}:{args.port}" + (" (dev sign-in at /auth/dev)" if DEV else ""))
    http.server.ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
