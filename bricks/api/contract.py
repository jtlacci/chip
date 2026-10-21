"""api: the HTTP surface for the web brick.

The server (server/ at the repository root) turns each HTTP request into one
api run. It handles cookies and tokens itself: it passes only the SHA-256 hash
of the session cookie (and of a new invite or session token it minted), never
a token. The api brick asks auth before each read or write and keeps its data
in store.

Routes (all JSON):

| Method | Path | Action asked of auth | Store |
| --- | --- | --- | --- |
| GET | /api/me | read_feed | - |
| GET | /api/feed | read_feed | scan posts |
| GET | /api/feed/instructions | read_feed | get settings/feed_instructions |
| PUT | /api/feed/instructions | edit_feed_instructions | put settings/feed_instructions |
| GET | /api/chats | read_chats | scan chats |
| GET | /api/chats/<id>/messages | read_chats | get chats/<id>, scan messages "<id>/" |
| POST | /api/chats/<id>/messages | post_message | get chats/<id>, put messages/<id>/<now>/<user_id> |
| GET | /api/activity | read_activity | scan activity |
| GET | /api/approvals | read_activity | scan approvals |
| GET | /api/account | read_feed | get settings/account -> {connected, email} |
| PUT | /api/account | connect_account | put settings/account {email, scopes, connected: now} |
| POST | /api/invites | auth op invite | - |
| DELETE | /api/users/<user_id> | auth op remove | - |
| POST | /api/session | auth op sign_in | - |
| DELETE | /api/session | auth op sign_out | - |
| GET | /api/session/scopes | auth op scopes | - |

Status codes: 200, 201 (created), 400 (bad body), 401 (no valid session),
403 (not allowed), 404 (no such route or record), 405 (method not allowed).
"""

from typing import TypedDict


CONTRACT_VERSION = 1
LANE = "strict"
DETERMINISTIC = True
SIBLING_DEPENDENCIES: dict[str, str] = {"auth": "orchestrated", "store": "orchestrated"}
OWNED_STATE: tuple[str, ...] = ("store:posts", "store:chats", "store:messages", "store:activity",
                                "store:approvals", "store:settings")

MAX_TEXT = 4000
PAGE = 100
MESSAGES_PAGE = 500


class BrickInput(TypedDict, total=False):
    """method, path, now; session_hash (from the cookie), body (parsed JSON);
    invite_hash (POST /api/invites), identity {sub, email, name} (POST /api/session)."""

    method: str
    path: str
    now: str
    session_hash: str
    body: dict
    invite_hash: str
    identity: dict


class BrickOutput(TypedDict):
    status: int
    body: dict
