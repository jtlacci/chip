"""CHIP's HTTP server: it serves the web app and turns each /api/* request into one api brick run.

Tokens never enter a brick. The server mints session and invite tokens, keeps
them in HttpOnly cookies or invite links, and passes only their SHA-256 hashes
to the api brick (the runtime records every brick input).

Sign-in is Google (server/google.py): /auth/google signs a person in with
the openid, email and profile scopes only; /auth/google/connect lets the admin
connect CHIP's own Google account one time. CHIP_DEV=1 also adds /auth/dev for
local work. server/feed_refresh.py adds the feed's posts on a timer
(CHIP_FEED_REFRESH=0 turns it off). Run it locally:

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
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bricks.api import run as api  # noqa: E402
from bricks.auth import run as auth  # noqa: E402
import google  # noqa: E402  (server/google.py)
from feed_refresh import Refresher  # noqa: E402  (server/feed_refresh.py)
from agent_reply import Replier  # noqa: E402  (server/agent_reply.py)

REFRESHER = Refresher()
REPLIER = Replier()

DIST = ROOT / "bricks" / "web" / "app" / "dist"
SESSION_COOKIE, INVITE_COOKIE, OAUTH_COOKIE = "chip_session", "chip_invite", "chip_oauth"
MAX_BODY = 65536
SECURE = os.environ.get("CHIP_SECURE") == "1"
DEV = os.environ.get("CHIP_DEV") == "1"
# The app's security headers. The web evaluation sends the same file (human-only).
HEADERS = json.loads((ROOT / "bricks" / "web" / "app" / "headers.json").read_text())


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
        if path == "/api/feed/instructions" and method == "PUT" and answer["status"] == 200:
            REFRESHER.wake()   # new instructions: the feed looks at every item again
        if method == "POST" and answer["status"] == 201 and path.startswith("/api/chats/") and path.endswith("/messages"):
            REPLIER.new_message(path.split("/")[3])   # CHIP answers in the same chat
        self.json_reply(answer["status"], body, extra)

    def join(self) -> None:
        token = urlsplit(self.path).path.split("/", 2)[2]
        self.send_response(303)
        self.send_header("Set-Cookie", cookie(INVITE_COOKIE, token, 7 * 24 * 3600))
        self.send_header("Location", "/auth/dev" if DEV and not google.configured() else "/auth/google")
        self.end_headers()

    def redirect(self, location: str, cookies: list[str] = ()) -> None:
        self.send_response(303)
        for value in cookies:
            self.send_header("Set-Cookie", value)
        self.send_header("Location", location)
        self.end_headers()

    def problem(self, status: int, text: str) -> None:
        data = (f"<!doctype html><meta charset=utf-8><title>CHIP</title><p>{text}</p>"
                "<p><a href=/feed>Back to CHIP</a></p>").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def may(self, action: str) -> bool:
        session = self.cookies().get(SESSION_COOKIE)
        if not session:
            return False
        return bool(auth({"op": "check", "session_hash": digest(session), "action": action, "now": now()}).get("allow"))

    def google_start(self, connect: bool) -> None:
        if not google.configured():
            self.problem(503, "Google sign-in is not set up on this server.")
            return
        if connect:
            if not self.may("connect_account"):
                self.problem(403, "Only the admin can connect CHIP's Google account.")
                return
            url, saved = google.start("connect", google.CONNECT_SCOPES, offline=True)
        else:
            scopes = api({"method": "GET", "path": "/api/session/scopes", "now": now()})["body"]["scopes"]
            url, saved = google.start("signin", scopes)
        self.redirect(url, [cookie(OAUTH_COOKIE, saved, 600)])

    def google_callback(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        clear = cookie(OAUTH_COOKIE, "", 0)
        if "error" in query:
            self.problem(400, "Google sign-in was cancelled.")
            return
        try:
            result = google.finish(self.cookies().get(OAUTH_COOKIE, ""), (query.get("state") or [""])[0],
                                   (query.get("code") or [""])[0])
        except google.GoogleError as exc:
            self.problem(400, f"Google sign-in failed: {exc}.")
            return
        if result["purpose"] == "connect":
            self.connect_account(result, clear)
            return
        token = secrets.token_urlsafe(32)
        request = {"method": "POST", "path": "/api/session", "now": now(), "session_hash": digest(token),
                   "identity": result["identity"]}
        invite = self.cookies().get(INVITE_COOKIE)
        if invite:
            request["invite_hash"] = digest(invite)
        answer = api(request)
        if answer["status"] != 200:
            reason = answer["body"].get("error")
            self.problem(403, "You need an invite to join CHIP. Ask an owner for a link." if reason == "invite_required"
                         else "This invite link has expired or was already used. Ask an owner for a new one.")
            return
        self.redirect("/feed", [cookie(SESSION_COOKIE, token, 30 * 24 * 3600), cookie(INVITE_COOKIE, "", 0), clear])

    def connect_account(self, result: dict, clear: str) -> None:
        """Keep CHIP's refresh token out of every brick: a file only the server reads, and SSM for restores."""
        if not self.may("connect_account"):
            self.problem(403, "Only the admin can connect CHIP's Google account.")
            return
        missing = [s for s in google.CONNECT_SCOPES if s.startswith("https://") and s not in result["scopes"]]
        if missing or not result.get("refresh_token"):
            self.problem(400, "Google did not grant everything CHIP needs. Connect again and allow every item.")
            return
        path = Path(os.environ.get("CHIP_GOOGLE_TOKEN_FILE", str(ROOT / "google-refresh-token")))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        path.write_text(result["refresh_token"])
        if os.environ.get("CHIP_SSM_REGION"):
            subprocess.run(["aws", "ssm", "put-parameter", "--region", os.environ["CHIP_SSM_REGION"],
                            "--name", "/chip/google/refresh-token", "--type", "SecureString", "--overwrite",
                            "--value", "file://" + str(path)], capture_output=True, timeout=30)
        session = self.cookies().get(SESSION_COOKIE, "")
        api({"method": "PUT", "path": "/api/account", "now": now(), "session_hash": digest(session),
             "body": {"email": result["identity"]["email"], "scopes": result["scopes"]}})
        self.redirect("/agent", [clear])

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
        elif path == "/auth/google":
            self.google_start(connect=False)
        elif path == "/auth/google/connect":
            self.google_start(connect=True)
        elif path == "/auth/google/callback":
            self.google_callback()
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
    if os.environ.get("CHIP_FEED_REFRESH", "1") == "1":
        REFRESHER.start()
    if os.environ.get("CHIP_AGENT", "1") == "1":
        REPLIER.start()
    if os.environ.get("CHIP_TOOLS_SOCKET"):
        import chip_tools   # server/chip_tools.py: the Hermes agent's tools
        chip_tools.serve(os.environ["CHIP_TOOLS_SOCKET"], os.environ.get("CHIP_TOOLS_GROUP"))
    http.server.ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
