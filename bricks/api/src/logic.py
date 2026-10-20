"""Route one HTTP request: ask auth, then read or write store."""

import re

from ..contract import MAX_TEXT, MESSAGES_PAGE, PAGE, BrickInput, BrickOutput
from ..input.adapters import auth, store

CHAT_MESSAGES = re.compile(r"/api/chats/([A-Za-z0-9_-]{1,64})/messages")
USER = re.compile(r"/api/users/([A-Za-z0-9:_.@-]{1,128})")
UNAUTHORISED = ("no_session", "session_expired", "no_user")


def reply(status: int, body: dict) -> BrickOutput:
    return {"status": status, "body": body}


def ask(inputs: dict, action: str, ctx: dict) -> tuple[dict | None, BrickOutput | None]:
    """(the user, or None and the error reply)."""
    answer = auth.call({"op": "check", "session_hash": inputs.get("session_hash", ""), "action": action,
                        "now": inputs.get("now", "")}, ctx)
    if answer.get("allow"):
        return answer, None
    if answer.get("reason") in UNAUTHORISED:
        return None, reply(401, {"error": "sign_in_required"})
    if answer.get("reason") == "forbidden":
        return None, reply(403, {"error": "forbidden"})
    return None, reply(400, {"error": answer.get("reason", "invalid")})


def scan(collection: str, ctx: dict, prefix: str = "", limit: int = PAGE) -> list[dict]:
    answer = store.call({"op": "scan", "collection": collection, "prefix": prefix, "limit": limit}, ctx)
    return [{**item["doc"], "id": item["key"]} for item in answer.get("docs", [])]


def newest_first(items: list[dict], field: str) -> list[dict]:
    return sorted(items, key=lambda item: (str(item.get(field, "")), item["id"]), reverse=True)


def text_of(body) -> str | None:
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        return None
    return text


def session_route(inputs: dict, method: str, ctx: dict) -> BrickOutput:
    if method == "POST":
        request = {"op": "sign_in", "identity": inputs.get("identity"), "session_hash": inputs.get("session_hash", ""),
                   "now": inputs.get("now", "")}
        if "invite_hash" in inputs:
            request["invite_hash"] = inputs["invite_hash"]
        answer = auth.call(request, ctx)
        if answer.get("ok"):
            return reply(200, {"user_id": answer["user_id"], "role": answer["role"]})
        return reply(400 if answer.get("reason") == "invalid" else 403, {"error": answer.get("reason", "invalid")})
    if method == "DELETE":
        answer = auth.call({"op": "sign_out", "session_hash": inputs.get("session_hash", "")}, ctx)
        return reply(200 if answer.get("ok") else 400, {"ok": bool(answer.get("ok"))})
    return reply(405, {"error": "method_not_allowed"})


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    method, path = inputs.get("method", ""), inputs.get("path", "")
    if not isinstance(path, str) or not isinstance(method, str):
        return reply(404, {"error": "not_found"})
    path = path.split("?", 1)[0].rstrip("/") or "/"
    data = dict(inputs)

    if path == "/api/session":
        return session_route(data, method, ctx)
    if path == "/api/session/scopes":
        if method != "GET":
            return reply(405, {"error": "method_not_allowed"})
        return reply(200, {"scopes": auth.call({"op": "scopes"}, ctx).get("scopes", [])})

    reads = {"/api/me": "read_feed", "/api/feed": "read_feed", "/api/chats": "read_chats",
             "/api/activity": "read_activity", "/api/approvals": "read_activity"}
    if path in reads:
        if method != "GET":
            return reply(405, {"error": "method_not_allowed"})
        user, error = ask(data, reads[path], ctx)
        if error:
            return error
        if path == "/api/me":
            return reply(200, {k: user.get(k) for k in ("user_id", "role", "name", "email")})
        if path == "/api/feed":
            return reply(200, {"posts": newest_first(scan("posts", ctx), "time")})
        if path == "/api/chats":
            return reply(200, {"chats": newest_first(scan("chats", ctx), "updated")})
        name = path.rsplit("/", 1)[1]
        return reply(200, {name: newest_first(scan(name, ctx), "time")})

    if path == "/api/feed/instructions":
        if method == "GET":
            user, error = ask(data, "read_feed", ctx)
            if error:
                return error
            answer = store.call({"op": "get", "collection": "settings", "key": "feed_instructions"}, ctx)
            return reply(200, {"text": answer.get("doc", {}).get("text", "") if answer.get("ok") else ""})
        if method == "PUT":
            user, error = ask(data, "edit_feed_instructions", ctx)
            if error:
                return error
            text = text_of(data.get("body"))
            if text is None:
                return reply(400, {"error": "text must be 1 to 4000 characters"})
            store.call({"op": "put", "collection": "settings", "key": "feed_instructions",
                        "doc": {"text": text, "updated_by": user["user_id"], "updated": data.get("now", "")}}, ctx)
            return reply(200, {"text": text})
        return reply(405, {"error": "method_not_allowed"})

    match = CHAT_MESSAGES.fullmatch(path)
    if match:
        chat_id = match.group(1)
        if method not in ("GET", "POST"):
            return reply(405, {"error": "method_not_allowed"})
        user, error = ask(data, "read_chats" if method == "GET" else "post_message", ctx)
        if error:
            return error
        if method == "POST":
            text = text_of(data.get("body"))
            if text is None:
                return reply(400, {"error": "text must be 1 to 4000 characters"})
        chat = store.call({"op": "get", "collection": "chats", "key": chat_id}, ctx)
        if not chat.get("ok"):
            return reply(404, {"error": "not_found"})
        if method == "GET":
            messages = scan("messages", ctx, prefix=f"{chat_id}/", limit=MESSAGES_PAGE)
            return reply(200, {"messages": messages})
        message = {"author": user["user_id"], "name": user.get("name", ""), "text": text, "time": data.get("now", "")}
        key = f"{chat_id}/{data.get('now', '')}/{user['user_id']}"
        store.call({"op": "put", "collection": "messages", "key": key, "doc": message}, ctx)
        return reply(201, {"message": {**message, "id": key}})

    if path == "/api/invites":
        if method != "POST":
            return reply(405, {"error": "method_not_allowed"})
        body = data.get("body") if isinstance(data.get("body"), dict) else {}
        answer = auth.call({"op": "invite", "session_hash": data.get("session_hash", ""), "role": body.get("role"),
                            "invite_hash": data.get("invite_hash", ""), "now": data.get("now", "")}, ctx)
        if answer.get("ok"):
            return reply(201, {"role": answer["role"], "expires": answer["expires"]})
        reason = answer.get("reason", "invalid")
        status = 401 if reason in UNAUTHORISED else 403 if reason == "forbidden" else 400
        return reply(status, {"error": reason})

    match = USER.fullmatch(path)
    if match:
        if method != "DELETE":
            return reply(405, {"error": "method_not_allowed"})
        answer = auth.call({"op": "remove", "session_hash": data.get("session_hash", ""), "user_id": match.group(1),
                            "now": data.get("now", "")}, ctx)
        if answer.get("ok"):
            return reply(200, {"user_id": answer["user_id"]})
        reason = answer.get("reason", "invalid")
        status = {"forbidden": 403, "not_found": 404}.get(reason, 401 if reason in UNAUTHORISED else 400)
        return reply(status, {"error": reason})

    return reply(404, {"error": "not_found"})
