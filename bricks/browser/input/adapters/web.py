"""The web: a browser that opens one URL (GET only) and gives back the page, or a screenshot of it.

Each call runs this file as a new process (``python web.py``, the request as
JSON on stdin), so each call has a new, empty browser profile. CHIP_WEB_COMMAND
replaces the command, for example to run it as a separate Linux user that can
read none of CHIP's files:

    CHIP_WEB_COMMAND="sudo -n -u chipweb /opt/chip-web/bin/python /opt/chip/bricks/browser/input/adapters/web.py"

The browser blocks every request that is not GET, every scheme but http, https,
data and blob, and every host that resolves to an address that is not global
(so a page cannot reach the server itself, its network or the cloud metadata
address). It downloads nothing.
"""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from urllib.parse import urlsplit

TIMEOUT = 45          # seconds for one call, browser start included
PAGE_TIMEOUT = 20000  # milliseconds for one page load
MAX_TEXT = 20000
MAX_LINKS = 200


def call(request: dict, ctx: dict) -> dict:
    return ctx["io"].external("web", request, _live)


def _live(request: dict) -> dict:
    command = shlex.split(os.environ.get("CHIP_WEB_COMMAND", "")) or [sys.executable, str(Path(__file__).resolve())]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp", "LANG": "C.UTF-8"}
    for name in ("PLAYWRIGHT_BROWSERS_PATH",):
        if name in os.environ:
            env[name] = os.environ[name]
    done = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=TIMEOUT, env=env)
    if done.returncode != 0:
        return {"error": f"browser failed: {done.stderr.strip()[-300:]}"}
    return json.loads(done.stdout)


# ------------------------------------------------------------------ the browser process

def _global(host: str) -> bool:
    """True when every address the host resolves to is global."""
    host = host.strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0].split("%")[0]).is_global for info in infos)


def _browse(request: dict) -> dict:
    from playwright.sync_api import Error, sync_playwright

    url = str(request.get("url", ""))
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not _global(parts.hostname or ""):
        return {"error": "blocked: not a public http or https address"}
    allowed: dict[str, bool] = {}

    def route(r) -> None:
        req = r.request
        target = urlsplit(req.url)
        if req.method != "GET" or target.scheme not in ("http", "https", "data", "blob"):
            r.abort()
            return
        host = target.hostname or ""
        if target.scheme in ("http", "https"):
            if host not in allowed:
                allowed[host] = _global(host)
            if not allowed[host]:
                r.abort()
                return
        r.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            context = browser.new_context(accept_downloads=False, viewport={"width": 1280, "height": 900},
                                          service_workers="block", java_script_enabled=True)
            context.route("**/*", route)
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Error:
                    pass
            except Error as exc:
                return {"error": f"could not open the page: {str(exc).splitlines()[0][:200]}"}
            final = page.url
            if request.get("op") == "look":
                import base64
                return {"url": final, "image": base64.b64encode(page.screenshot(type="png")).decode()}
            text = page.inner_text("body") if page.query_selector("body") else ""
            links = page.eval_on_selector_all(
                "a[href]", "els => els.map(a => ({text: (a.innerText || a.title || a.getAttribute('aria-label') || '')"
                           ".trim().replace(/\\s+/g, ' ').slice(0, 120), href: a.href}))")
            seen, out = set(), []
            for link in links:
                href = str(link.get("href", "")).split("#")[0]
                if urlsplit(href).scheme in ("http", "https") and href not in seen:
                    seen.add(href)
                    out.append({"text": link.get("text", ""), "href": href})
            return {"url": final, "title": page.title()[:300], "text": text[:MAX_TEXT], "links": out[:MAX_LINKS]}
        finally:
            browser.close()


if __name__ == "__main__":
    request = json.loads(sys.stdin.read())
    if request.get("op") not in ("open", "look"):
        result = {"error": "op must be open or look"}
    else:
        result = _browse(request)
    sys.stdout.write(json.dumps(result))
