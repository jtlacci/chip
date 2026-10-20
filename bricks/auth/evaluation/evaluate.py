"""How the auth brick is evaluated. Evaluation loop only.

A reference model of the spec (Users and permissions) runs each case against
the recorded store answers. It gives the expected output and the writes auth
must make. ``score`` compares both: the output, and that every expected write
(a put or a delete to store) is among the calls the case made.

Hard gates: the permission table, the invite rules (7 days, one use, a role no
higher than the sender's, no admin invites), the session rules (expired,
missing, removed), sign-in scopes, and the writes.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import random

from ..contract import BrickInput

GATES: tuple[tuple[str, str, float], ...] = (
    ("correct", ">=", 1.0),
    ("permission", ">=", 1.0),
    ("invite_rules", ">=", 1.0),
    ("session_rules", ">=", 1.0),
    ("scopes", ">=", 1.0),
    ("writes", ">=", 1.0),
)

RANK = {"guest": 0, "owner": 1, "admin": 2}
GUEST = {"read_feed", "read_chats", "post_message", "ask_agent"}
OWNER = GUEST | {"edit_feed_instructions", "invite", "remove_user", "approve_action", "read_activity"}
ADMIN = OWNER | {"connect_account", "change_role"}
ALLOWED = {"guest": GUEST, "owner": OWNER, "admin": ADMIN}
BASE = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def _is_hash(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def reference(inputs: dict, ask) -> tuple[dict, list[dict]]:
    """(expected output, expected writes). ``ask(request)`` answers like the store brick."""
    writes: list[dict] = []

    def get(collection, key):
        answer = ask({"op": "get", "collection": collection, "key": key})
        return answer.get("doc") if answer.get("ok") else None

    def put(collection, key, doc):
        request = {"op": "put", "collection": collection, "key": key, "doc": doc}
        writes.append(request)
        ask(request)

    def session_user(session_hash, now):
        if not _is_hash(session_hash):
            return None, None, "no_session"
        session = get("sessions", session_hash)
        if session is None:
            return None, None, "no_session"
        ends = _parse(session.get("expires"))
        if ends is None or now >= ends:
            return None, None, "session_expired"
        user = get("users", session["user_id"]) if isinstance(session.get("user_id"), str) else None
        if user is None or user.get("removed"):
            return None, None, "no_user"
        return user, session["user_id"], None

    op = inputs.get("op")
    if op == "scopes":
        return {"scopes": ["openid", "email", "profile"]}, writes
    if op == "sign_out":
        if not _is_hash(inputs.get("session_hash")):
            return {"ok": False, "reason": "invalid"}, writes
        request = {"op": "delete", "collection": "sessions", "key": inputs["session_hash"]}
        writes.append(request)
        ask(request)
        return {"ok": True}, writes
    if op not in ("check", "sign_in", "invite", "remove"):
        return {"ok": False, "reason": "invalid"}, writes
    now = _parse(inputs.get("now"))
    if now is None:
        return ({"allow": False, "reason": "invalid"} if op == "check" else {"ok": False, "reason": "invalid"}), writes
    now = now.astimezone(timezone.utc)

    if op == "check":
        if inputs.get("action") not in ADMIN:
            return {"allow": False, "reason": "unknown_action"}, writes
        user, user_id, reason = session_user(inputs.get("session_hash"), now)
        if user is None:
            return {"allow": False, "reason": reason}, writes
        shown = {"user_id": user_id, "role": user["role"], "name": user.get("name", ""), "email": user.get("email", "")}
        if inputs["action"] in ALLOWED.get(user["role"], set()):
            return {"allow": True, **shown}, writes
        return {"allow": False, "reason": "forbidden", **shown}, writes

    if op == "sign_in":
        identity, session_hash, invite_hash = inputs.get("identity"), inputs.get("session_hash"), inputs.get("invite_hash")
        if (not isinstance(identity, dict) or not all(isinstance(identity.get(k), str) and identity[k]
                                                      for k in ("sub", "email", "name"))
                or not _is_hash(session_hash) or (invite_hash is not None and not _is_hash(invite_hash))):
            return {"ok": False, "reason": "invalid"}, writes
        user_id = "google:" + identity["sub"]
        user = get("users", user_id)
        if user is not None and not user.get("removed"):
            role = user["role"]
        elif invite_hash is not None:
            invite = get("invites", invite_hash)
            ends = _parse(invite.get("expires")) if invite else None
            if invite is None or "used_by" in invite or ends is None or now >= ends or invite.get("role") not in ("guest", "owner"):
                return {"ok": False, "reason": "invite_invalid"}, writes
            role = invite["role"]
            put("users", user_id, {"email": identity["email"], "name": identity["name"], "role": role, "created": _iso(now)})
            put("invites", invite_hash, {**invite, "used_by": user_id, "used": _iso(now)})
        elif user is None and not ask({"op": "scan", "collection": "users", "prefix": "", "limit": 1}).get("docs"):
            role = "admin"
            put("users", user_id, {"email": identity["email"], "name": identity["name"], "role": role, "created": _iso(now)})
        else:
            return {"ok": False, "reason": "invite_required"}, writes
        put("sessions", session_hash, {"user_id": user_id, "created": _iso(now), "expires": _iso(now + timedelta(days=30))})
        return {"ok": True, "user_id": user_id, "role": role}, writes

    if op == "invite":
        role, invite_hash = inputs.get("role"), inputs.get("invite_hash")
        if role not in ("guest", "owner") or not _is_hash(invite_hash):
            return {"ok": False, "reason": "invalid"}, writes
        user, user_id, reason = session_user(inputs.get("session_hash"), now)
        if user is None:
            return {"ok": False, "reason": reason}, writes
        if "invite" not in ALLOWED.get(user["role"], set()) or RANK[role] > RANK[user["role"]]:
            return {"ok": False, "reason": "forbidden"}, writes
        ends = _iso(now + timedelta(days=7))
        put("invites", invite_hash, {"role": role, "by": user_id, "created": _iso(now), "expires": ends})
        return {"ok": True, "role": role, "expires": ends}, writes

    target = inputs.get("user_id")
    if not isinstance(target, str) or not target:
        return {"ok": False, "reason": "invalid"}, writes
    user, user_id, reason = session_user(inputs.get("session_hash"), now)
    if user is None:
        return {"ok": False, "reason": reason}, writes
    if "remove_user" not in ALLOWED.get(user["role"], set()):
        return {"ok": False, "reason": "forbidden"}, writes
    doc = get("users", target)
    if doc is None or doc.get("removed"):
        return {"ok": False, "reason": "not_found"}, writes
    if doc.get("role") == "admin":
        return {"ok": False, "reason": "forbidden"}, writes
    put("users", target, {**doc, "removed": True})
    return {"ok": True, "user_id": target}, writes


def _recorded(record: dict):
    table = [(e["request"], e["response"]) for e in record.get("exchanges", []) if e["name"] == "sibling:store"]

    def ask(request):
        for recorded_request, response in table:
            if recorded_request == request:
                return response
        return {"ok": False, "error": "not_found"}
    return ask


def build(record: dict) -> BrickInput:
    return record["input"]


def _kind(inputs: dict) -> str:
    op = inputs.get("op")
    if op == "check":
        return "permission"
    if op == "invite" or (op == "sign_in" and "invite_hash" in inputs):
        return "invite_rules"
    if op == "scopes":
        return "scopes"
    return ""


def score(record: dict, output: dict | None, later: list[dict], calls=()) -> dict[str, float]:
    want, writes = reference(record["input"], _recorded(record))
    made = [c["request"] for c in calls if c["name"] == "sibling:store"]
    same = 1.0 if output == want else 0.0
    wrote = 1.0 if all(w in made for w in writes) else 0.0
    terms = {"correct": same, "writes": wrote}
    kind = _kind(record["input"])
    if kind:
        terms[kind] = same * wrote
    if isinstance(want, dict) and want.get("reason") in ("session_expired", "no_session", "no_user"):
        terms["session_rules"] = same
    return terms


# ---------------------------------------------------------------- synthetic data

class World:
    """A store state for the generator. ``ask`` records every call as an exchange."""

    def __init__(self, users: dict, sessions: dict, invites: dict) -> None:
        self.data = {"users": dict(users), "sessions": dict(sessions), "invites": dict(invites)}
        self.exchanges: list[dict] = []

    def ask(self, request: dict) -> dict:
        collection = self.data.setdefault(request["collection"], {})
        op = request["op"]
        if op == "get":
            doc = collection.get(request["key"])
            response = {"ok": True, "doc": doc} if doc is not None else {"ok": False, "error": "not_found"}
        elif op == "put":
            collection[request["key"]] = request["doc"]
            response = {"ok": True}
        elif op == "delete":
            response = {"ok": True, "deleted": collection.pop(request["key"], None) is not None}
        else:
            keys = sorted(k for k in collection if k.startswith(request.get("prefix", "")))[:request.get("limit", 100)]
            response = {"ok": True, "docs": [{"key": k, "doc": collection[k]} for k in keys]}
        self.exchanges.append({"name": "sibling:store", "request": request, "response": response})
        return response


def _case(rng: random.Random, index: int) -> dict:
    now = BASE + timedelta(hours=rng.randint(0, 2000), seconds=rng.randint(0, 59))
    people = {}
    sessions = {}
    for role in ("admin", "owner", "guest"):
        uid = f"google:{role}-{index}"
        people[uid] = {"email": f"{role}{index}@example.com", "name": role.title(), "role": role,
                       "created": _iso(BASE - timedelta(days=40))}
        state = rng.choice(["valid", "valid", "valid", "expired", "edge", "removed"])
        ends = {"valid": now + timedelta(days=rng.randint(1, 29)), "expired": now - timedelta(days=1),
                "edge": now, "removed": now + timedelta(days=3)}[state]
        if state == "removed" and role != "admin":
            people[uid] = {**people[uid], "removed": True}
        sessions[_hex(f"s-{role}-{index}")] = {"user_id": uid, "created": _iso(now - timedelta(days=1)), "expires": _iso(ends)}
    invites = {}
    for name, role, state in (("ok-guest", "guest", "fresh"), ("ok-owner", "owner", "fresh"), ("used", "guest", "used"),
                              ("old", "guest", "old"), ("edge", "owner", "edge"), ("admin", "admin", "fresh")):
        made = now - timedelta(days={"fresh": 2, "used": 2, "old": 8, "edge": 7}[state])
        doc = {"role": role, "by": f"google:owner-{index}", "created": _iso(made), "expires": _iso(made + timedelta(days=7))}
        if state == "used":
            doc.update(used_by="google:someone", used=_iso(made + timedelta(hours=1)))
        invites[_hex(f"i-{name}-{index}")] = doc
    if rng.random() < 0.1:
        people, sessions = {}, {}
    world = World(people, sessions, invites)
    roles = ["admin", "owner", "guest"]
    session = rng.choice([_hex(f"s-{r}-{index}") for r in roles] + [_hex("missing"), "not-a-hash"])
    op = rng.choice(["check", "check", "check", "sign_in", "sign_in", "invite", "remove", "sign_out", "scopes", "bogus"])
    if op == "check":
        inputs = {"op": "check", "session_hash": session, "now": _iso(now),
                  "action": rng.choice(sorted(ADMIN) + ["delete_everything"])}
    elif op == "sign_in":
        who = rng.choice([f"{r}-{index}" for r in roles] + [f"new-{index}"])
        inputs = {"op": "sign_in", "identity": {"sub": who, "email": f"{who}@example.com", "name": who},
                  "session_hash": _hex(f"new-session-{index}"), "now": _iso(now)}
        if rng.random() < 0.7:
            inputs["invite_hash"] = rng.choice([_hex(f"i-{n}-{index}") for n in ("ok-guest", "ok-owner", "used", "old", "edge", "admin")]
                                               + [_hex("no-such-invite")])
        if rng.random() < 0.05:
            inputs["identity"] = {"sub": "", "email": "x", "name": "x"}
    elif op == "invite":
        inputs = {"op": "invite", "session_hash": session, "now": _iso(now),
                  "role": rng.choice(["guest", "owner", "admin", "root"]), "invite_hash": _hex(f"invite-{index}")}
    elif op == "remove":
        inputs = {"op": "remove", "session_hash": session, "now": _iso(now),
                  "user_id": rng.choice([f"google:{r}-{index}" for r in roles] + ["google:nobody"])}
    elif op == "sign_out":
        inputs = {"op": "sign_out", "session_hash": session}
    elif op == "scopes":
        inputs = {"op": "scopes"}
    else:
        inputs = {"op": "bogus", "now": _iso(now)}
    reference(inputs, world.ask)
    return {"input": inputs, "exchanges": world.exchanges}


def _matrix(index: int) -> list[dict]:
    """Every role against every action, invite role and removal target, with valid sessions."""
    now = BASE + timedelta(days=index % 50, seconds=index % 60)
    people = {f"google:{r}-m{index}": {"email": f"{r}@example.com", "name": r.title(), "role": r,
                                       "created": _iso(BASE - timedelta(days=40))} for r in ("admin", "owner", "guest")}
    sessions = {_hex(f"m-{r}-{index}"): {"user_id": f"google:{r}-m{index}", "created": _iso(now - timedelta(days=1)),
                                         "expires": _iso(now + timedelta(days=5))} for r in ("admin", "owner", "guest")}
    cases = []
    for role in ("admin", "owner", "guest"):
        session = _hex(f"m-{role}-{index}")
        asks = [{"op": "check", "session_hash": session, "now": _iso(now), "action": a} for a in sorted(ADMIN)]
        asks += [{"op": "invite", "session_hash": session, "now": _iso(now), "role": r,
                  "invite_hash": _hex(f"m-invite-{role}-{r}-{index}")} for r in ("guest", "owner", "admin")]
        asks += [{"op": "remove", "session_hash": session, "now": _iso(now), "user_id": f"google:{t}-m{index}"}
                 for t in ("admin", "owner", "guest")]
        for inputs in asks:
            world = World(people, sessions, {})
            reference(inputs, world.ask)
            cases.append({"input": inputs, "exchanges": world.exchanges})
    return cases


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    return _matrix(seed) + [_case(rng, seed * 1000 + i) for i in range(120)]
