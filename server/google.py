"""Google OAuth for the server: sign-in, and the one-time connection of CHIP's own account.

Each flow uses a random state, PKCE (S256) and a nonce, kept in a short-lived
HttpOnly cookie. The callback exchanges the code with the client secret, then
asks Google to check the ID token (tokeninfo) and checks its audience, issuer,
nonce, expiry and verified email itself. No token ever enters a brick.

Settings (environment):
  CHIP_GOOGLE_CLIENT_ID           the OAuth client ID
  CHIP_GOOGLE_CLIENT_SECRET_FILE  a file with the client secret
  CHIP_PUBLIC_URL                 for example https://184-197-162-29.sslip.io
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
# What CHIP's own account grants, one time, to the admin's connect step.
CONNECT_SCOPES = (
    "openid", "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
)


class GoogleError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("CHIP_GOOGLE_CLIENT_ID")) and Path(os.environ.get("CHIP_GOOGLE_CLIENT_SECRET_FILE", "/nonexistent")).is_file()


def client_id() -> str:
    return os.environ["CHIP_GOOGLE_CLIENT_ID"]


def client_secret() -> str:
    return Path(os.environ["CHIP_GOOGLE_CLIENT_SECRET_FILE"]).read_text().strip()


def redirect_uri() -> str:
    return os.environ.get("CHIP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/") + "/auth/google/callback"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def start(purpose: str, scopes: list[str] | tuple[str, ...], offline: bool = False) -> tuple[str, str]:
    """(the Google URL to send the browser to, the cookie value that the callback needs)."""
    state, verifier, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(16)
    params = {"client_id": client_id(), "redirect_uri": redirect_uri(), "response_type": "code",
              "scope": " ".join(scopes), "state": state, "nonce": nonce,
              "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
              "prompt": "consent" if offline else "select_account"}
    if offline:
        params["access_type"] = "offline"
    saved = _b64(json.dumps({"state": state, "verifier": verifier, "nonce": nonce, "purpose": purpose,
                             "made": int(time.time())}).encode())
    return f"{AUTH_URL}?{urlencode(params)}", saved


def _json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        # Google's error body names the problem (invalid_grant and so on); it holds no secret.
        try:
            detail = json.load(exc).get("error", exc.code)
        except ValueError:
            detail = exc.code
        raise GoogleError(f"Google answered {detail}") from None


def finish(saved_cookie: str, state: str, code: str) -> dict:
    """Check the callback and return {"identity": {sub, email, name}, "refresh_token"?, "scopes", "purpose"}."""
    try:
        saved = json.loads(base64.urlsafe_b64decode(saved_cookie + "=" * (-len(saved_cookie) % 4)))
    except ValueError:
        raise GoogleError("the sign-in cookie is missing or damaged") from None
    if not secrets.compare_digest(str(saved.get("state", "")), state or ""):
        raise GoogleError("the sign-in state does not match")
    if time.time() - saved.get("made", 0) > 600:
        raise GoogleError("the sign-in took more than 10 minutes; start again")
    body = urlencode({"code": code, "client_id": client_id(), "client_secret": client_secret(),
                      "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
                      "code_verifier": saved["verifier"]}).encode()
    tokens = _json(Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}))
    if "id_token" not in tokens:
        raise GoogleError("Google sent no ID token")
    info = _json(Request(f"{TOKENINFO_URL}?{urlencode({'id_token': tokens['id_token']})}"))
    if info.get("aud") != client_id() or info.get("iss") not in ISSUERS:
        raise GoogleError("the ID token is not for CHIP")
    if info.get("nonce") != saved["nonce"]:
        raise GoogleError("the ID token nonce does not match")
    if int(info.get("exp", 0)) < time.time():
        raise GoogleError("the ID token has expired")
    if str(info.get("email_verified")).lower() != "true":
        raise GoogleError("the Google account's email address is not verified")
    identity = {"sub": info["sub"], "email": info["email"], "name": info.get("name") or info["email"].split("@")[0]}
    return {"identity": identity, "refresh_token": tokens.get("refresh_token"),
            "scopes": tokens.get("scope", "").split(), "purpose": saved.get("purpose")}
