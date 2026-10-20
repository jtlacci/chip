"""auth's rules. The tables are in contract.py; this file applies them."""

from datetime import datetime, timedelta, timezone
import re

from ..contract import (INVITABLE, INVITE_DAYS, PERMISSIONS, ROLES, SESSION_DAYS, SIGN_IN_SCOPES,
                        BrickInput, BrickOutput)
from ..input.adapters import store

HASH = re.compile(r"[0-9a-f]{64}")
ALL_ACTIONS = PERMISSIONS["admin"]


def _time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _who(session_hash, now: datetime, ctx: dict) -> tuple[dict | None, str | None, str | None]:
    """(user doc, user_id, reason) for a session."""
    if not isinstance(session_hash, str) or not HASH.fullmatch(session_hash):
        return None, None, "no_session"
    session = store.get("sessions", session_hash, ctx)
    if session is None:
        return None, None, "no_session"
    expires = _time(session.get("expires"))
    if expires is None or now >= expires:
        return None, None, "session_expired"
    user_id = session.get("user_id")
    user = store.get("users", user_id, ctx) if isinstance(user_id, str) else None
    if user is None or user.get("removed"):
        return None, None, "no_user"
    return user, user_id, None


def check(inputs: dict, now: datetime, ctx: dict) -> BrickOutput:
    action = inputs.get("action")
    if action not in ALL_ACTIONS:
        return {"allow": False, "reason": "unknown_action"}
    user, user_id, reason = _who(inputs.get("session_hash"), now, ctx)
    if user is None:
        return {"allow": False, "reason": reason}
    role = user.get("role")
    shown = {"user_id": user_id, "role": role, "name": user.get("name", ""), "email": user.get("email", "")}
    if action in PERMISSIONS.get(role, ()):
        return {"allow": True, **shown}
    return {"allow": False, "reason": "forbidden", **shown}


def sign_in(inputs: dict, now: datetime, ctx: dict) -> BrickOutput:
    identity = inputs.get("identity")
    session_hash = inputs.get("session_hash")
    if (not isinstance(identity, dict) or not all(isinstance(identity.get(k), str) and identity.get(k)
                                                  for k in ("sub", "email", "name"))
            or not isinstance(session_hash, str) or not HASH.fullmatch(session_hash)):
        return {"ok": False, "reason": "invalid"}
    invite_hash = inputs.get("invite_hash")
    if invite_hash is not None and (not isinstance(invite_hash, str) or not HASH.fullmatch(invite_hash)):
        return {"ok": False, "reason": "invalid"}
    user_id = "google:" + identity["sub"]
    user = store.get("users", user_id, ctx)
    if user is not None and not user.get("removed"):
        role = user["role"]
    elif invite_hash is not None:
        invite = store.get("invites", invite_hash, ctx)
        expires = _time(invite.get("expires")) if invite else None
        if invite is None or "used_by" in invite or expires is None or now >= expires or invite.get("role") not in INVITABLE:
            return {"ok": False, "reason": "invite_invalid"}
        role = invite["role"]
        store.put("users", user_id, {"email": identity["email"], "name": identity["name"], "role": role,
                                     "created": _text(now)}, ctx)
        store.put("invites", invite_hash, {**invite, "used_by": user_id, "used": _text(now)}, ctx)
    elif user is None and not store.any_user(ctx):
        role = "admin"   # the first person to sign in sets CHIP up
        store.put("users", user_id, {"email": identity["email"], "name": identity["name"], "role": role,
                                     "created": _text(now)}, ctx)
    else:
        return {"ok": False, "reason": "invite_required"}
    store.put("sessions", session_hash, {"user_id": user_id, "created": _text(now),
                                         "expires": _text(now + timedelta(days=SESSION_DAYS))}, ctx)
    return {"ok": True, "user_id": user_id, "role": role}


def invite(inputs: dict, now: datetime, ctx: dict) -> BrickOutput:
    role, invite_hash = inputs.get("role"), inputs.get("invite_hash")
    if role not in INVITABLE or not isinstance(invite_hash, str) or not HASH.fullmatch(invite_hash):
        return {"ok": False, "reason": "invalid"}
    user, user_id, reason = _who(inputs.get("session_hash"), now, ctx)
    if user is None:
        return {"ok": False, "reason": reason}
    mine = user.get("role")
    if "invite" not in PERMISSIONS.get(mine, ()) or ROLES.index(role) > ROLES.index(mine):
        return {"ok": False, "reason": "forbidden"}
    expires = _text(now + timedelta(days=INVITE_DAYS))
    store.put("invites", invite_hash, {"role": role, "by": user_id, "created": _text(now), "expires": expires}, ctx)
    return {"ok": True, "role": role, "expires": expires}


def remove(inputs: dict, now: datetime, ctx: dict) -> BrickOutput:
    target = inputs.get("user_id")
    if not isinstance(target, str) or not target:
        return {"ok": False, "reason": "invalid"}
    user, user_id, reason = _who(inputs.get("session_hash"), now, ctx)
    if user is None:
        return {"ok": False, "reason": reason}
    if "remove_user" not in PERMISSIONS.get(user.get("role"), ()):
        return {"ok": False, "reason": "forbidden"}
    doc = store.get("users", target, ctx)
    if doc is None or doc.get("removed"):
        return {"ok": False, "reason": "not_found"}
    if doc.get("role") == "admin":
        return {"ok": False, "reason": "forbidden"}
    store.put("users", target, {**doc, "removed": True}, ctx)
    return {"ok": True, "user_id": target}


def execute(inputs: BrickInput, ctx: dict) -> BrickOutput:
    op = inputs.get("op")
    if op == "scopes":
        return {"scopes": list(SIGN_IN_SCOPES)}
    if op == "sign_out":
        session_hash = inputs.get("session_hash")
        if not isinstance(session_hash, str) or not HASH.fullmatch(session_hash):
            return {"ok": False, "reason": "invalid"}
        store.delete("sessions", session_hash, ctx)
        return {"ok": True}
    handlers = {"check": check, "sign_in": sign_in, "invite": invite, "remove": remove}
    if op not in handlers:
        return {"ok": False, "reason": "invalid"}
    now = _time(inputs.get("now"))
    if now is None:
        return {"allow": False, "reason": "invalid"} if op == "check" else {"ok": False, "reason": "invalid"}
    return handlers[op](dict(inputs), now, ctx)
