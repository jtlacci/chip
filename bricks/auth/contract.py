"""auth: users, roles, sessions and invites, and the one permission table.

The api brick asks auth before each read or write. No other brick makes a
permission decision. Session and invite tokens never enter a brick: the server
mints them and passes only their SHA-256 hashes (64 lowercase hex characters).

Records in store (auth owns these collections):
- users/<user_id>      {"email", "name", "role", "created"} and "removed": true after removal.
                       user_id is "google:" + the Google subject.
- sessions/<hash>      {"user_id", "created", "expires"}; a session lasts 30 days.
- invites/<hash>       {"role", "by", "created", "expires"}; an invite lasts 7 days and is used once:
                       on use, "used_by" and "used" are added.

Times are ISO 8601 with a UTC offset. auth writes them as YYYY-MM-DDTHH:MM:SS+00:00.
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
DETERMINISTIC = True
SIBLING_DEPENDENCIES: dict[str, str] = {"store": "orchestrated"}
OWNED_STATE: tuple[str, ...] = ("store:users", "store:sessions", "store:invites")

# Human-only. These tables are the product decision; changing them needs a human.
ROLES = ("guest", "owner", "admin")          # in rank order
GUEST_ACTIONS = ("read_feed", "read_chats", "post_message", "ask_agent")
OWNER_ACTIONS = GUEST_ACTIONS + ("edit_feed_instructions", "invite", "remove_user", "approve_action", "read_activity")
ADMIN_ACTIONS = OWNER_ACTIONS + ("connect_account", "change_role")
PERMISSIONS = {"guest": GUEST_ACTIONS, "owner": OWNER_ACTIONS, "admin": ADMIN_ACTIONS}
INVITABLE = ("guest", "owner")                # there is one admin; nobody invites another
SIGN_IN_SCOPES = ("openid", "email", "profile")
SESSION_DAYS = 30
INVITE_DAYS = 7


class BrickInput(TypedDict, total=False):
    """op and its fields:

    check:    session_hash, action, now -> may this session do this action?
    sign_in:  identity {sub, email, name} (verified by the server), session_hash, invite_hash?, now
    invite:   session_hash, role, invite_hash, now
    remove:   session_hash, user_id, now
    sign_out: session_hash
    scopes:   (nothing) -> the Google scopes sign-in may ask for
    """

    op: str
    session_hash: str
    action: str
    now: str
    identity: dict
    invite_hash: str
    role: str
    user_id: str


class BrickOutput(TypedDict, total=False):
    """check: allow, reason (when not allowed), user_id, role, name, email (when there is a user).
    sign_in / invite / remove / sign_out: ok, reason (when not ok), user_id, role, expires.
    scopes: scopes.
    Reasons: invalid, unknown_action, no_session, session_expired, no_user, forbidden,
    invite_required, invite_invalid, not_found."""

    allow: bool
    ok: bool
    reason: str
    user_id: str
    role: str
    name: str
    email: str
    expires: str
    scopes: list
