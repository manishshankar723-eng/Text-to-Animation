"""
events.py — the activity log. Who registered, who signed in, what an admin did.

Until now the app kept no record of anything a PERSON did. Jobs record what was
produced, but "when did this customer last open the app", "who signed up this
week" and "who turned that feature off" had no answer anywhere — the closest
thing was a uvicorn log line that scrolls away. This is that record.

ONE STREAM, NOT THREE. Sign-ins, sign-ups and administrative changes all land
here with a `type` and a `meta` blob, rather than in an audit table beside an
analytics table beside a login table. They are read together far more often than
separately ("what happened to this account?" is one query over all three), and a
single stream cannot disagree with itself about the order things happened in.

    {
        "id":         "<uuid4 hex>",
        "at":         "<iso8601 utc>",     sortable as a string; everything is UTC
        "type":       "user.login",        see TYPES below
        "email":      "who it happened TO", lowercased, or None
        "actor":      "who DID it",         set only when that differs from email
        "ip":         "1.2.3.4" | None,
        "user_agent": "<truncated>" | None,
        "meta":       {...}                 type-specific, always a dict
    }

⚠ `email` AND `actor` ARE NOT THE SAME QUESTION. On a login they are the same
person and `actor` is left empty. On `admin.user_disabled` the `email` is the
customer who lost access and the `actor` is the administrator who took it — and
the only reason this file is worth having during an incident is that it can tell
those two apart.

⚠ **`record()` NEVER RAISES.** It is called from the middle of login, register
and every admin mutation. A logging failure must not turn a successful sign-in
into a 500 — the user's account works whether or not we managed to write a note
about it. Every failure in here is swallowed and logged.

Backend follows the USER STORE (`API_USER_STORE`), exactly as `drafts.py` does:
MongoDB in normal operation, a JSON file on disk when Mongo is unreachable. One
switch, so there is no way to end up with accounts in one place and the record
of what they did in another.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# The event types this app writes. A plain list of constants rather than an Enum
# because they are also STRINGS IN A DATABASE that predate any code reading them
# — an old row must stay readable after a type is renamed or retired, and an
# Enum would refuse to load it. The admin filter offers these; it does not
# require them.
#
# The naming is `subject.past_tense_verb`, and the prefix is what the panel
# groups and filters on — so anything an administrator did starts `admin.`.
TYPE_REGISTERED = "user.registered"
TYPE_LOGIN = "user.login"
TYPE_LOGIN_FAILED = "user.login_failed"
TYPE_ACCOUNT_DELETED = "user.deleted"
TYPE_PASSWORD_CHANGED = "user.password_changed"
TYPE_ADMIN_USER_DISABLED = "admin.user_disabled"
TYPE_ADMIN_USER_ENABLED = "admin.user_enabled"
TYPE_ADMIN_ROLE_CHANGED = "admin.role_changed"
TYPE_ADMIN_USER_DELETED = "admin.user_deleted"
TYPE_ADMIN_NOTE_SAVED = "admin.note_saved"
# Phase 2 — the hide/launch switchboard. ⚠ A FEATURE CHANGE HAS NO `email`: it
# happened to the whole SITE, not to one account, so only `actor` is set. The
# readers already treat a missing email as "not about one person".
TYPE_ADMIN_FEATURE_CHANGED = "admin.feature_changed"
TYPE_ADMIN_OVERRIDE_SET = "admin.override_set"
# Phase 3 — pricing. `admin.tier_changed` is a change to the PRICE LIST and so
# has no `email`, like a feature change; `admin.user_tier_changed` is one
# account being moved onto a tier and does.
TYPE_ADMIN_TIER_CHANGED = "admin.tier_changed"
TYPE_ADMIN_USER_TIER_CHANGED = "admin.user_tier_changed"
# Phase 4 — offers and subscriptions. ⚠ A SUBSCRIPTION EVENT NAMES THE CUSTOMER
# (`email`) AS WELL AS THE ADMIN (`actor`); an offer change is about the site and
# so, like a price change, carries only the actor.
TYPE_ADMIN_OFFER_CHANGED = "admin.offer_changed"
TYPE_SUBSCRIPTION_STARTED = "subscription.started"
TYPE_SUBSCRIPTION_CANCELLED = "subscription.cancelled"

# Shown in the panel's filter, in the order they appear there. Grouped: the
# things users do, then the things administrators do.
KNOWN_TYPES = (
    TYPE_REGISTERED,
    TYPE_LOGIN,
    TYPE_LOGIN_FAILED,
    TYPE_PASSWORD_CHANGED,
    TYPE_ACCOUNT_DELETED,
    TYPE_ADMIN_USER_DISABLED,
    TYPE_ADMIN_USER_ENABLED,
    TYPE_ADMIN_ROLE_CHANGED,
    TYPE_ADMIN_USER_DELETED,
    TYPE_ADMIN_NOTE_SAVED,
    TYPE_ADMIN_FEATURE_CHANGED,
    TYPE_ADMIN_OVERRIDE_SET,
    TYPE_ADMIN_TIER_CHANGED,
    TYPE_ADMIN_USER_TIER_CHANGED,
    TYPE_ADMIN_OFFER_CHANGED,
    TYPE_SUBSCRIPTION_STARTED,
    TYPE_SUBSCRIPTION_CANCELLED,
)

# A user agent string is attacker-controlled and unbounded. It is kept because
# "signed in from a phone" is genuinely useful context on an account, and capped
# because nothing downstream needs more than this to say so.
_MAX_UA_CHARS = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    return email.strip().lower() or None


def _use_local() -> bool:
    return config.USER_STORE == "local"


# ===========================================================================
# Local JSON-file backend (mirrors users.py / drafts.py)
# ===========================================================================
def _local_path() -> Path:
    return Path(config.LOCAL_EVENTS_PATH)


def _local_load() -> list:
    """Return the event list, newest LAST. Missing/corrupt file → empty."""
    path = _local_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        logger.warning("Local event log at %s is unreadable — starting empty.", path)
        return []


def _local_save(rows: list) -> None:
    # Trim from the FRONT: the oldest events are the ones worth losing.
    if len(rows) > config.MAX_LOCAL_EVENTS:
        rows = rows[-config.MAX_LOCAL_EVENTS :]
    _local_path().write_text(json.dumps(rows, indent=2), encoding="utf-8")


# ===========================================================================
# Mongo backend
# ===========================================================================
_collection = None


def get_collection():
    """Return the events collection, connecting (and indexing) on first use."""
    global _collection
    if _collection is not None:
        return _collection

    with _lock:
        if _collection is not None:  # re-check inside the lock
            return _collection
        from .mongo import get_db

        col = get_db()[config.EVENTS_COLLECTION]
        _ensure_indexes(col)
        _collection = col
        logger.info(
            "MongoDB event log ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.EVENTS_COLLECTION,
        )
        return _collection


def _ensure_indexes(col) -> None:
    """Indexes for the three ways the panel actually reads this.

    Idempotent — Mongo ignores a create_index for one that already exists.
    """
    try:
        # The activity feed: newest first, optionally narrowed by type.
        col.create_index([("at", -1)], name="at_desc")
        col.create_index([("type", 1), ("at", -1)], name="type_at")
        # One account's history — the user detail panel's third tab.
        col.create_index([("email", 1), ("at", -1)], name="email_at", sparse=True)

        # ⚠ RETENTION IS THE DATABASE'S JOB, NOT A CLEANUP SCRIPT'S. A TTL index
        # needs a real BSON datetime, and `at` is deliberately an ISO STRING
        # (sortable, and the same shape every other timestamp in this codebase
        # has). So the row carries a second field that exists ONLY for this
        # index — `expires_at`, the moment the row should vanish — and
        # expireAfterSeconds=0 means "when that moment passes".
        if config.EVENT_RETENTION_DAYS > 0:
            col.create_index("expires_at", name="ttl", expireAfterSeconds=0)
    except Exception as e:  # noqa: BLE001 — indexes are an optimisation
        logger.warning("Could not create event indexes (%s). Queries still work.", e)


# ===========================================================================
# Writing
# ===========================================================================
def record(
    type: str,
    email: str | None = None,
    *,
    actor: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    **meta,
) -> None:
    """Append one event. NEVER RAISES — see the module docstring.

    `actor` is left unset when the person acting is the person acted upon; the
    readers treat an absent actor as "themselves".
    """
    try:
        row = {
            "id": uuid.uuid4().hex,
            "at": _now_iso(),
            "type": type,
            "email": _normalize_email(email),
            "actor": _normalize_email(actor),
            "ip": ip or None,
            "user_agent": (user_agent or "")[:_MAX_UA_CHARS] or None,
            "meta": meta or {},
        }

        if _use_local():
            with _lock:
                rows = _local_load()
                rows.append(row)
                _local_save(rows)
            return

        doc = dict(row)
        if config.EVENT_RETENTION_DAYS > 0:
            doc["expires_at"] = datetime.now(timezone.utc) + timedelta(
                days=config.EVENT_RETENTION_DAYS
            )
        get_collection().insert_one(doc)
    except Exception as e:  # noqa: BLE001 — logging must never break the caller
        logger.warning("Could not record event %s for %s: %s", type, email, e)


def request_context(request) -> dict:
    """`{ip, user_agent}` for a FastAPI Request, for splatting into `record`.

    ⚠ THE CLIENT IP IS ONLY AS TRUSTWORTHY AS THE DEPLOYMENT. Behind a proxy
    `request.client.host` is the PROXY, so `X-Forwarded-For`'s first hop is
    preferred — and that header is trivially forged by anyone talking to the
    server directly. It is context on a support ticket, never a security
    control, and nothing in this app makes a decision on it.
    """
    try:
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        return {
            "ip": forwarded or (request.client.host if request.client else None),
            "user_agent": request.headers.get("user-agent"),
        }
    except Exception:  # noqa: BLE001 — context is optional, the event is not
        return {}


# ===========================================================================
# Reading
# ===========================================================================
def list_events(
    limit: int = 50,
    *,
    types: list[str] | None = None,
    email: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Events, newest first.

    `since` is an ISO timestamp compared as a STRING, which is exact here
    because every `at` this module writes is UTC and therefore lexicographically
    ordered. Do not pass a local-time string.
    """
    limit = max(1, min(limit, config.ADMIN_MAX_PAGE))
    key = _normalize_email(email)

    if _use_local():
        with _lock:
            rows = _local_load()
        rows = [r for r in rows if _matches(r, types, key, since)]
        rows.sort(key=lambda r: r.get("at") or "", reverse=True)
        return rows[:limit]

    query: dict = {}
    if types:
        query["type"] = {"$in": list(types)}
    if key:
        # An admin action names the customer in `email` and the administrator in
        # `actor`. Asking for one address must find both, or "what did this
        # admin do" returns nothing at all.
        query["$or"] = [{"email": key}, {"actor": key}]
    if since:
        query["at"] = {"$gte": since}

    cursor = (
        get_collection()
        .find(query, {"_id": 0, "expires_at": 0})
        .sort("at", -1)
        .limit(limit)
    )
    return list(cursor)


def _matches(row: dict, types, email, since) -> bool:
    """The local backend's filter — the Mongo query above, in Python."""
    if types and row.get("type") not in types:
        return False
    if email and email not in (row.get("email"), row.get("actor")):
        return False
    if since and (row.get("at") or "") < since:
        return False
    return True


def count_since(since: str, types: list[str] | None = None) -> int:
    """How many events of these types since `since`. Used by the dashboard."""
    try:
        if _use_local():
            with _lock:
                rows = _local_load()
            return sum(1 for r in rows if _matches(r, types, None, since))
        query: dict = {"at": {"$gte": since}}
        if types:
            query["type"] = {"$in": list(types)}
        return get_collection().count_documents(query)
    except Exception as e:  # noqa: BLE001 — a dashboard tile, not a transaction
        logger.warning("Could not count events since %s: %s", since, e)
        return 0


def distinct_emails_since(since: str, types: list[str] | None = None) -> int:
    """How many DISTINCT accounts produced one of these events since `since`.

    This is the honest definition of "active users" for this app: someone who
    signed in. It is not session-based and does not try to be — a person who
    stays logged in for a fortnight and works every day counts once, on the day
    they signed in, which is why the dashboard labels it "signed in", not
    "active".
    """
    try:
        if _use_local():
            with _lock:
                rows = _local_load()
            return len(
                {
                    r.get("email")
                    for r in rows
                    if r.get("email") and _matches(r, types, None, since)
                }
            )
        query: dict = {"at": {"$gte": since}, "email": {"$ne": None}}
        if types:
            query["type"] = {"$in": list(types)}
        return len(get_collection().distinct("email", query))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not count distinct emails since %s: %s", since, e)
        return 0


def daily_counts(days: int, types: list[str] | None = None) -> list[dict]:
    """`[{day: "2026-08-25", count: 3}, …]` for the last `days` days, oldest
    first, with EMPTY DAYS INCLUDED.

    ⚠ The zero-filling is the point. A grouped query returns only the days that
    had something, so a sparse week draws a chart where three signups on Monday
    and three on Friday look adjacent and continuous. Every day gets a row.
    """
    days = max(1, min(days, 90))
    start = datetime.now(timezone.utc) - timedelta(days=days - 1)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    buckets = {
        (start + timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(days)
    }

    try:
        rows = _rows_since(start.isoformat(), types)
        for at in rows:
            day = (at or "")[:10]
            if day in buckets:
                buckets[day] += 1
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not build daily counts: %s", e)

    return [{"day": d, "count": c} for d, c in sorted(buckets.items())]


def _rows_since(since: str, types: list[str] | None) -> list[str]:
    """Just the `at` strings since `since` — everything `daily_counts` needs."""
    if _use_local():
        with _lock:
            rows = _local_load()
        return [r.get("at") for r in rows if _matches(r, types, None, since)]

    query: dict = {"at": {"$gte": since}}
    if types:
        query["type"] = {"$in": list(types)}
    return [d.get("at") for d in get_collection().find(query, {"_id": 0, "at": 1})]


def check_connection() -> dict:
    """Report event-log connectivity (never raises). Mirrors users.check_connection."""
    if _use_local():
        return {"connected": True, "db": f"local:{config.LOCAL_EVENTS_PATH}", "error": None}
    status = {"connected": False, "db": config.MONGODB_DB, "error": None}
    try:
        get_collection().database.client.admin.command("ping")
        status["connected"] = True
    except Exception as e:  # noqa: BLE001 — health must not throw
        status["error"] = str(e)
    return status
