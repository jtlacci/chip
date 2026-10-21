"""How the api brick is evaluated. Evaluation loop only.

A reference model of the route table (contract.py) runs each case against the
recorded auth and store answers. ``score`` compares the reply, the order of
calls (auth is always asked before store is read or written), and the writes.

Every route is tested with every kind of session: admin, owner, guest, an
expired session and no session.
"""

from datetime import datetime, timedelta, timezone
import random

from ..contract import BrickInput

GATES: tuple[tuple[str, str, float], ...] = (
    ("correct", ">=", 1.0),
    ("status_matrix", ">=", 1.0),
    ("auth_first", ">=", 1.0),
    ("writes", ">=", 1.0),
)

GUEST = {"read_feed", "read_chats", "post_message", "ask_agent"}
OWNER = GUEST | {"edit_feed_instructions", "invite", "remove_user", "approve_action", "read_activity"}
ALLOWED = {"guest": GUEST, "owner": OWNER, "admin": OWNER | {"connect_account", "change_role"}}
UNAUTHORISED = ("no_session", "session_expired", "no_user")
BASE = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


def _r(status, body):
    return {"status": status, "body": body}


def reference(inputs: dict, auth, store) -> tuple[dict, list[dict]]:
    """(expected reply, expected writes as (name, request))."""
    writes: list[dict] = []
    now = inputs.get("now", "")
    session = inputs.get("session_hash", "")

    def check(action):
        answer = auth({"op": "check", "session_hash": session, "action": action, "now": now})
        if answer.get("allow"):
            return answer, None
        reason = answer.get("reason")
        if reason in UNAUTHORISED:
            return None, _r(401, {"error": "sign_in_required"})
        if reason == "forbidden":
            return None, _r(403, {"error": "forbidden"})
        return None, _r(400, {"error": reason or "invalid"})

    def scan(collection, prefix="", limit=100):
        answer = store({"op": "scan", "collection": collection, "prefix": prefix, "limit": limit})
        return [{**d["doc"], "id": d["key"]} for d in answer.get("docs", [])]

    def newest(items, field):
        return sorted(items, key=lambda i: (str(i.get(field, "")), i["id"]), reverse=True)

    def text(body):
        t = body.get("text") if isinstance(body, dict) else None
        return t if isinstance(t, str) and t.strip() and len(t) <= 4000 else None

    def write(name, request):
        writes.append({"name": name, "request": request})

    method, path = inputs.get("method", ""), inputs.get("path", "")
    if not isinstance(path, str) or not isinstance(method, str):
        return _r(404, {"error": "not_found"}), writes
    path = path.split("?", 1)[0].rstrip("/") or "/"

    if path == "/api/session":
        if method == "POST":
            request = {"op": "sign_in", "identity": inputs.get("identity"), "session_hash": session, "now": now}
            if "invite_hash" in inputs:
                request["invite_hash"] = inputs["invite_hash"]
            write("sibling:auth", request)
            answer = auth(request)
            if answer.get("ok"):
                return _r(200, {"user_id": answer["user_id"], "role": answer["role"]}), writes
            return _r(400 if answer.get("reason") == "invalid" else 403, {"error": answer.get("reason", "invalid")}), writes
        if method == "DELETE":
            request = {"op": "sign_out", "session_hash": session}
            write("sibling:auth", request)
            answer = auth(request)
            return _r(200 if answer.get("ok") else 400, {"ok": bool(answer.get("ok"))}), writes
        return _r(405, {"error": "method_not_allowed"}), writes
    if path == "/api/session/scopes":
        if method != "GET":
            return _r(405, {"error": "method_not_allowed"}), writes
        return _r(200, {"scopes": auth({"op": "scopes"}).get("scopes", [])}), writes

    reads = {"/api/me": "read_feed", "/api/feed": "read_feed", "/api/chats": "read_chats",
             "/api/activity": "read_activity", "/api/approvals": "read_activity"}
    if path in reads:
        if method != "GET":
            return _r(405, {"error": "method_not_allowed"}), writes
        user, error = check(reads[path])
        if error:
            return error, writes
        if path == "/api/me":
            return _r(200, {k: user.get(k) for k in ("user_id", "role", "name", "email")}), writes
        if path == "/api/feed":
            return _r(200, {"posts": newest(scan("posts"), "time")}), writes
        if path == "/api/chats":
            return _r(200, {"chats": newest(scan("chats"), "updated")}), writes
        name = path.rsplit("/", 1)[1]
        return _r(200, {name: newest(scan(name), "time")}), writes

    if path == "/api/feed/instructions":
        if method == "GET":
            user, error = check("read_feed")
            if error:
                return error, writes
            answer = store({"op": "get", "collection": "settings", "key": "feed_instructions"})
            return _r(200, {"text": answer.get("doc", {}).get("text", "") if answer.get("ok") else ""}), writes
        if method == "PUT":
            user, error = check("edit_feed_instructions")
            if error:
                return error, writes
            t = text(inputs.get("body"))
            if t is None:
                return _r(400, {"error": "text must be 1 to 4000 characters"}), writes
            request = {"op": "put", "collection": "settings", "key": "feed_instructions",
                       "doc": {"text": t, "updated_by": user["user_id"], "updated": now}}
            write("sibling:store", request)
            store(request)
            return _r(200, {"text": t}), writes
        return _r(405, {"error": "method_not_allowed"}), writes

    parts = path.split("/")
    if len(parts) == 5 and parts[:3] == ["", "api", "chats"] and parts[4] == "messages" and _chat_id(parts[3]):
        chat_id = parts[3]
        if method not in ("GET", "POST"):
            return _r(405, {"error": "method_not_allowed"}), writes
        user, error = check("read_chats" if method == "GET" else "post_message")
        if error:
            return error, writes
        t = None
        if method == "POST":
            t = text(inputs.get("body"))
            if t is None:
                return _r(400, {"error": "text must be 1 to 4000 characters"}), writes
        if not store({"op": "get", "collection": "chats", "key": chat_id}).get("ok"):
            return _r(404, {"error": "not_found"}), writes
        if method == "GET":
            return _r(200, {"messages": scan("messages", f"{chat_id}/", 500)}), writes
        message = {"author": user["user_id"], "name": user.get("name", ""), "text": t, "time": now}
        key = f"{chat_id}/{now}/{user['user_id']}"
        request = {"op": "put", "collection": "messages", "key": key, "doc": message}
        write("sibling:store", request)
        store(request)
        return _r(201, {"message": {**message, "id": key}}), writes

    if path == "/api/account":
        if method == "GET":
            user, error = check("read_feed")
            if error:
                return error, writes
            answer = store({"op": "get", "collection": "settings", "key": "account"})
            doc = answer.get("doc", {}) if answer.get("ok") else {}
            return _r(200, {"connected": bool(doc.get("email")), "email": doc.get("email", "")}), writes
        if method == "PUT":
            user, error = check("connect_account")
            if error:
                return error, writes
            body = inputs.get("body") if isinstance(inputs.get("body"), dict) else {}
            email, scopes = body.get("email"), body.get("scopes")
            if (not isinstance(email, str) or "@" not in email or len(email) > 254
                    or not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes)):
                return _r(400, {"error": "email and scopes are required"}), writes
            request = {"op": "put", "collection": "settings", "key": "account",
                       "doc": {"email": email, "scopes": sorted(scopes), "connected": now}}
            write("sibling:store", request)
            store(request)
            return _r(200, {"connected": True, "email": email}), writes
        return _r(405, {"error": "method_not_allowed"}), writes

    if path == "/api/invites":
        if method != "POST":
            return _r(405, {"error": "method_not_allowed"}), writes
        body = inputs.get("body") if isinstance(inputs.get("body"), dict) else {}
        request = {"op": "invite", "session_hash": session, "role": body.get("role"),
                   "invite_hash": inputs.get("invite_hash", ""), "now": now}
        write("sibling:auth", request)
        answer = auth(request)
        if answer.get("ok"):
            return _r(201, {"role": answer["role"], "expires": answer["expires"]}), writes
        reason = answer.get("reason", "invalid")
        return _r(401 if reason in UNAUTHORISED else 403 if reason == "forbidden" else 400, {"error": reason}), writes

    if len(parts) == 4 and parts[:3] == ["", "api", "users"] and _user_id(parts[3]):
        if method != "DELETE":
            return _r(405, {"error": "method_not_allowed"}), writes
        request = {"op": "remove", "session_hash": session, "user_id": parts[3], "now": now}
        write("sibling:auth", request)
        answer = auth(request)
        if answer.get("ok"):
            return _r(200, {"user_id": answer["user_id"]}), writes
        reason = answer.get("reason", "invalid")
        status = {"forbidden": 403, "not_found": 404}.get(reason, 401 if reason in UNAUTHORISED else 400)
        return _r(status, {"error": reason}), writes

    return _r(404, {"error": "not_found"}), writes


def _chat_id(value: str) -> bool:
    return 1 <= len(value) <= 64 and all(c.isascii() and (c.isalnum() or c in "_-") for c in value)


def _user_id(value: str) -> bool:
    return 1 <= len(value) <= 128 and all(c.isascii() and (c.isalnum() or c in ":_.@-") for c in value)


def _answers(record: dict, name: str):
    table = [(e["request"], e["response"]) for e in record.get("exchanges", []) if e["name"] == name]

    def ask(request):
        for recorded, response in table:
            if recorded == request:
                return response
        return {"ok": False, "error": "not_found", "allow": False, "reason": "no_session"}
    return ask


def build(record: dict) -> BrickInput:
    return record["input"]


def score(record: dict, output: dict | None, later: list[dict], calls=()) -> dict[str, float]:
    want, writes = reference(record["input"], _answers(record, "sibling:auth"), _answers(record, "sibling:store"))
    same = 1.0 if output == want else 0.0
    made = [{"name": c["name"], "request": c["request"]} for c in calls]
    wrote = 1.0 if all(w in made for w in writes) else 0.0
    names = [c["name"] for c in calls]
    first_store = names.index("sibling:store") if "sibling:store" in names else None
    first_auth = names.index("sibling:auth") if "sibling:auth" in names else None
    ordered = first_store is None or (first_auth is not None and first_auth < first_store)
    terms = {"correct": same, "writes": wrote, "auth_first": 1.0 if ordered else 0.0}
    if record["input"].get("path", "").startswith("/api/") and want["status"] in (200, 201, 401, 403):
        terms["status_matrix"] = 1.0 if isinstance(output, dict) and output.get("status") == want["status"] else 0.0
    return terms


# ---------------------------------------------------------------- synthetic data

def _iso(moment):
    return moment.isoformat(timespec="seconds")


class Fake:
    """Auth and store answers for one case; every call is recorded as an exchange."""

    def __init__(self, sessions: dict, data: dict) -> None:
        self.sessions, self.data, self.exchanges = sessions, data, []

    def auth(self, request):
        op = request["op"]
        who = self.sessions.get(request.get("session_hash"))
        if op == "check":
            if who is None or isinstance(who, str):
                answer = {"allow": False, "reason": who or "no_session"}
            else:
                shown = {"user_id": who["user_id"], "role": who["role"], "name": who["name"], "email": who["email"]}
                answer = ({"allow": True, **shown} if request["action"] in ALLOWED[who["role"]]
                          else {"allow": False, "reason": "forbidden", **shown})
        elif op == "scopes":
            answer = {"scopes": ["openid", "email", "profile"]}
        elif op == "sign_out":
            answer = {"ok": True}
        elif op == "sign_in":
            answer = ({"ok": True, "user_id": "google:" + request["identity"]["sub"], "role": "guest"}
                      if isinstance(request.get("identity"), dict) and request["identity"].get("sub")
                      else {"ok": False, "reason": "invalid"})
        elif who is None or isinstance(who, str):
            answer = {"ok": False, "reason": who or "no_session"}
        elif op == "invite":
            answer = ({"ok": True, "role": request["role"], "expires": "2026-10-27T15:00:00+00:00"}
                      if who["role"] != "guest" and request.get("role") in ("guest", "owner") else {"ok": False, "reason": "forbidden"})
        else:
            answer = ({"ok": True, "user_id": request["user_id"]} if who["role"] != "guest" else {"ok": False, "reason": "forbidden"})
        self.exchanges.append({"name": "sibling:auth", "request": request, "response": answer})
        return answer

    def store(self, request):
        collection = self.data.setdefault(request["collection"], {})
        if request["op"] == "get":
            doc = collection.get(request["key"])
            answer = {"ok": True, "doc": doc} if doc is not None else {"ok": False, "error": "not_found"}
        elif request["op"] == "put":
            collection[request["key"]] = request["doc"]
            answer = {"ok": True}
        else:
            keys = sorted(k for k in collection if k.startswith(request["prefix"]))[:request["limit"]]
            answer = {"ok": True, "docs": [{"key": k, "doc": collection[k]} for k in keys]}
        self.exchanges.append({"name": "sibling:store", "request": request, "response": answer})
        return answer


def _data(rng: random.Random) -> dict:
    times = [_iso(BASE - timedelta(hours=h)) for h in range(12)]
    posts = {f"p{i}": {"title": f"Post {i}", "body": "Body", "time": rng.choice(times), "kind": "text"} for i in range(rng.randint(0, 5))}
    chats = {"general": {"title": "General", "updated": rng.choice(times)}, "trip": {"title": "Trip", "updated": rng.choice(times)}}
    messages = {f"general/{t}/google:u{i}": {"author": f"google:u{i}", "name": "U", "text": "hi", "time": t}
                for i, t in enumerate(rng.sample(times, 3))}
    messages["trip/2026-10-20T10:00:00+00:00/google:x"] = {"author": "google:x", "name": "X", "text": "yo", "time": "2026-10-20T10:00:00+00:00"}
    activity = {f"a{i}": {"title": "Find hoodie", "time": rng.choice(times)} for i in range(rng.randint(0, 3))}
    approvals = {f"r{i}": {"title": "Send email", "state": "allowed", "time": rng.choice(times)} for i in range(rng.randint(0, 3))}
    data = {"posts": posts, "chats": chats, "messages": messages, "activity": activity, "approvals": approvals}
    if rng.random() < 0.6:
        data["settings"] = {"feed_instructions": {"text": "Local NYC events", "updated_by": "google:o", "updated": times[0]}}
    if rng.random() < 0.5:
        data.setdefault("settings", {})["account"] = {"email": "chip@example.com", "scopes": ["a"], "connected": times[0]}
    return data


SESSIONS = {
    "admin": {"user_id": "google:a", "role": "admin", "name": "Ada", "email": "ada@example.com"},
    "owner": {"user_id": "google:o", "role": "owner", "name": "Otto", "email": "otto@example.com"},
    "guest": {"user_id": "google:g", "role": "guest", "name": "Gus", "email": "gus@example.com"},
    "expired": "session_expired",
}
ROUTES = [("GET", "/api/me"), ("GET", "/api/feed"), ("GET", "/api/feed/instructions"),
          ("PUT", "/api/feed/instructions"), ("GET", "/api/chats"), ("GET", "/api/chats/general/messages"),
          ("POST", "/api/chats/general/messages"), ("POST", "/api/chats/nochat/messages"),
          ("GET", "/api/account"), ("PUT", "/api/account"), ("POST", "/api/account"),
          ("GET", "/api/activity"), ("GET", "/api/approvals"), ("POST", "/api/invites"),
          ("DELETE", "/api/users/google:g"), ("POST", "/api/session"), ("DELETE", "/api/session"),
          ("GET", "/api/session/scopes"), ("GET", "/api/nowhere"), ("DELETE", "/api/feed"), ("PATCH", "/api/chats/general/messages")]


def _hash(name: str) -> str:
    return (name.encode().hex() * 64)[:64]


def _case(rng: random.Random, method: str, path: str, who: str | None) -> dict:
    sessions = {_hash(k): v for k, v in SESSIONS.items()}
    fake = Fake(sessions, _data(rng))
    now = _iso(BASE + timedelta(minutes=rng.randint(0, 600)))
    inputs = {"method": method, "path": path, "now": now}
    if who:
        inputs["session_hash"] = _hash(who)
    if method in ("PUT", "POST"):
        inputs["body"] = rng.choice([{"text": "Show me NYC events"}, {"text": ""}, {"text": "x" * 4001}, {"role": "guest"},
                                     {"role": "owner"}, {"role": "admin"}, {},
                                     {"email": "chip@example.com", "scopes": ["b", "a"]}, {"email": "no-at", "scopes": []}])
    if path == "/api/invites":
        inputs["invite_hash"] = _hash("invite")
    if path == "/api/session" and method == "POST":
        inputs["identity"] = rng.choice([{"sub": "new", "email": "n@example.com", "name": "New"}, {"sub": ""}])
        inputs["session_hash"] = _hash("fresh")
        if rng.random() < 0.5:
            inputs["invite_hash"] = _hash("invite")
    if rng.random() < 0.1:
        inputs["path"] = path + "/"
    reference(inputs, fake.auth, fake.store)
    return {"input": inputs, "exchanges": fake.exchanges}


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = [_case(rng, m, p, who) for m, p in ROUTES for who in ("admin", "owner", "guest", "expired", None)]
    cases += [_case(rng, *rng.choice(ROUTES), rng.choice(["admin", "owner", "guest", "expired", None])) for _ in range(60)]
    return cases
