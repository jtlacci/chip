"""CHIP's tools as an MCP server (stdio), for the Hermes agent.

Hermes starts this file (mcp_servers.chip in its config.yaml). It speaks MCP
(JSON-RPC 2.0, one message on each line) on stdin and stdout, and forwards each
tool call to the CHIP server's tool socket (CHIP_TOOLS_SOCKET, server/chip_tools.py).
It imports nothing from CHIP and holds no secret: it runs as the hermes user,
and the CHIP server does the work.

This file is outside every brick, so the runner does not evaluate it; a human
reviews each change.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time

PROTOCOL = "2025-06-18"
TIMEOUT = 60


def _tools_call(request: dict) -> dict:
    path = os.environ.get("CHIP_TOOLS_SOCKET", "/run/chip-tools/tools.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(TIMEOUT)
        conn.connect(path)
        conn.sendall(json.dumps(request).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode() or "{}")


def _wait_for_tools(seconds: int = 30) -> list:
    """The tool list. At boot Hermes can start before the CHIP server's socket exists: wait for it."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return _tools_call({"op": "list"}).get("tools", [])
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def handle(message: dict) -> dict | None:
    """The answer to one JSON-RPC message, or None for a notification."""
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        return None   # notifications (initialized, cancelled) need no answer
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "chip", "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        try:
            result = {"tools": _wait_for_tools()}
        except OSError as exc:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32000, "message": f"CHIP is not reachable: {exc}"}}
    elif method == "tools/call":
        params = message.get("params") or {}
        try:
            answer = _tools_call({"op": "call", "tool": params.get("name"), "args": params.get("arguments") or {}})
        except OSError as exc:
            answer = {"error": f"CHIP is not reachable: {exc}"}
        result = {"content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}],
                  "isError": "error" in answer}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"unknown method {method!r}"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
