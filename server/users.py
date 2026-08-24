"""
users.py — user store for authentication.

Two interchangeable backends, selected by `config.USER_STORE`:
  * "mongo" (default) — one document per user in the MongoDB `users` collection.
  * "local"           — a JSON file on disk (handy for dev when MongoDB Atlas is
                        unreachable). Same public API; passwords are already
                        bcrypt-hashed before they reach the store.

Each user record looks like:
    {
        "email": "user@example.com",   (unique, lowercased)
        "password_hash": "<bcrypt>",
        "created_at": "<iso8601>",
        "disabled": false,
        "api_keys": { "meshy": "...", ... },   (optional)

        --- added with the admin panel; ABSENT ON EVERY OLDER ACCOUNT ---
        "account_role": "user"|"admin",  absent → "user", see `role_of`
        "last_login_at": "<iso8601>",  absent → never signed in since this shipped
        "login_count": 12,             absent → 0
        "admin_note": "…",             private to administrators; never returned
                                       by /auth/me
        "feature_overrides": {...},    per-feature force on/off; see set_override
        "tier": "starter",             billing tier id; absent → billing.DEFAULT_TIER
        "tier_expires_at": "<iso>",    when that lapses; absent → never
    }

⚠ NOTHING HERE IS MIGRATED. Every field above the rule is what an account has
always had; everything below it is written on first use and read with a default.
A record created in July must keep loading, which is why `role_of` answers
"user" for a missing role rather than None.
"""

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class DuplicateUser(Exception):
    """Raised when registering an email that already exists."""

    def __init__(self, email: str):
        super().__init__(f"User already exists: {email}")
        self.email = email


def _use_local() -> bool:
    return config.USER_STORE == "local"


# ===========================================================================
# Local JSON-file backend
# ===========================================================================
def _local_path() -> Path:
    return Path(config.LOCAL_USERS_PATH)


def _local_load() -> dict:
    """Return {email: user_dict}. Missing/corrupt file → empty store."""
    path = _local_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Local user store at %s is unreadable — starting empty.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


# ===========================================================================
# Mongo backend
# ===========================================================================
_client = None
_collection = None


def get_collection():
    """Return the users collection, connecting to MongoDB on first use."""
    global _client, _collection
    if _collection is not None:
        return _collection

    with _lock:
        if _collection is not None:  # re-check inside the lock
            return _collection
        from .mongo import get_client

        # One shared client across users / drafts / jobs — see mongo.py.
        _client = get_client()
        db = _client[config.MONGODB_DB]
        col = db[config.USERS_COLLECTION]
        col.create_index("email", unique=True)
        _collection = col
        logger.info(
            "MongoDB user store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.USERS_COLLECTION,
        )
        return _collection


# ===========================================================================
# Public API (dispatches to the active backend)
# ===========================================================================
def get_user_by_email(email: str) -> dict | None:
    """Return the raw user document (incl. password_hash) or None."""
    key = _normalize_email(email)
    if _use_local():
        with _lock:
            return _local_load().get(key)
    return get_collection().find_one({"email": key})


def create_user(email: str, password_hash: str) -> dict:
    """Insert a new user. Raises DuplicateUser if the email already exists."""
    key = _normalize_email(email)
    doc = {
        "email": key,
        "password_hash": password_hash,
        "created_at": _now_iso(),
        "disabled": False,
    }

    if _use_local():
        with _lock:
            data = _local_load()
            if key in data:
                raise DuplicateUser(email)
            data[key] = doc
            _local_save(data)
        return doc

    from pymongo.errors import DuplicateKeyError

    try:
        result = get_collection().insert_one(dict(doc))
    except DuplicateKeyError as e:
        raise DuplicateUser(email) from e
    doc["_id"] = result.inserted_id
    return doc


def update_password(email: str, password_hash: str) -> bool:
    """Set a new password hash for an existing user. Returns True if updated."""
    key = _normalize_email(email)
    if _use_local():
        with _lock:
            data = _local_load()
            if key not in data:
                return False
            data[key]["password_hash"] = password_hash
            _local_save(data)
            return True
    result = get_collection().update_one(
        {"email": key}, {"$set": {"password_hash": password_hash}}
    )
    return result.matched_count > 0


# Profile fields a user is allowed to change about themselves. An allow-list,
# not a passthrough: without it a crafted PATCH could set `password_hash`,
# `disabled` or `email` and take over or lock out the account.
#
# ⚠ `role` IN HERE IS THE PERSON'S JOB TITLE — "Director", "Producer" — and it
# is self-service by design. It is NOT the privilege field, which is
# `account_role` (see ROLE_FIELD below) and is deliberately absent from this
# list. The two nearly collided: had the admin role been stored as `role`, this
# allow-list would have let anybody PATCH themselves an administrator.
PROFILE_FIELDS = frozenset(
    {
        "full_name",
        "display_name",
        "company",
        "role",
        "default_style",
        "default_aspect_ratio",
        "default_genre",
        "timezone",
    }
)


def update_profile(email: str, fields: dict) -> bool:
    """Write profile fields onto the user record. Returns True if updated.

    Only keys in PROFILE_FIELDS are written; anything else is dropped silently.
    Values are stored as given (already length-capped by the request model).
    """
    key = _normalize_email(email)
    clean = {k: v for k, v in (fields or {}).items() if k in PROFILE_FIELDS}
    if not clean:
        return True  # nothing to do is success, not failure

    if _use_local():
        with _lock:
            data = _local_load()
            if key not in data:
                return False
            data[key].update(clean)
            _local_save(data)
            return True
    result = get_collection().update_one({"email": key}, {"$set": clean})
    return result.matched_count > 0


def delete_user(email: str) -> bool:
    """Permanently delete a user by email. Returns True if a user was removed."""
    key = _normalize_email(email)
    if _use_local():
        with _lock:
            data = _local_load()
            if key not in data:
                return False
            del data[key]
            _local_save(data)
            return True
    result = get_collection().delete_one({"email": key})
    return result.deleted_count > 0


# ===========================================================================
# Roles, administration and the sign-in record
# ===========================================================================
# ⚠ ABSENT MEANS "user". Every account that existed before roles did has no
# `role` field at all, and reading one has to give the SAFE answer rather than
# None — a missing field must never be mistaken for a privileged one.
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLES = (ROLE_USER, ROLE_ADMIN)

# ⚠ THE DOCUMENT KEY IS `account_role`, NOT `role`, AND THAT IS A SECURITY
# DECISION RATHER THAN A NAMING ONE. `role` was already taken — by the person's
# JOB TITLE, which is in `PROFILE_FIELDS` and therefore writable by the account
# itself through `PATCH /auth/me`. Storing the privilege under that name would
# have made "make me an administrator" an ordinary profile edit.
ROLE_FIELD = "account_role"


def role_of(user: dict | None) -> str:
    """The effective role of a user document.

    ⚠ `config.ADMIN_EMAILS` IS A FLOOR THAT THE DOCUMENT CANNOT LOWER. An
    address listed in the environment is an admin whatever its record says —
    that is the bootstrap for a fresh database (nobody can grant the first role,
    because granting one requires already having it) and the way back in when
    the last admin demotes themselves by accident.
    """
    if not user:
        return ROLE_USER
    if (user.get("email") or "") in config.ADMIN_EMAILS:
        return ROLE_ADMIN
    role = user.get(ROLE_FIELD)
    return role if role in ROLES else ROLE_USER


def is_admin(email: str) -> bool:
    """Whether this address is an administrator. One lookup; never raises."""
    try:
        return role_of(get_user_by_email(email)) == ROLE_ADMIN
    except Exception:  # noqa: BLE001 — an unreachable store is not an admin
        return False


def set_role(email: str, role: str) -> bool:
    """Grant or revoke the admin role. Returns True if a user matched."""
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role!r}")
    return _set_fields(email, {ROLE_FIELD: role})


def set_disabled(email: str, disabled: bool) -> bool:
    """Lock or unlock an account. Returns True if a user matched.

    ⚠ THE CALLER MUST ALSO DROP THE AUTH CACHE — `auth.forget_cached_email`.
    `get_current_user` remembers a resolved user for thirty seconds keyed on
    the token, so without that the account goes on working for up to half a
    minute after it is locked, which is exactly the window that matters when
    somebody is being locked out on purpose.
    """
    return _set_fields(email, {"disabled": bool(disabled)})


def set_tier(email: str, tier_id: str) -> bool:
    """Put an account on a billing tier. Returns True if a user matched.

    ⚠ THE TIER IS STORED, THE RANK IS NOT. `billing.rank_of` reads the ladder
    live, so re-ranking a tier moves everybody on it at once — which is the only
    way "Pro is now above Studio" can mean anything. Denormalising the rank onto
    the account would freeze each subscriber at the ladder as it was on the day
    they subscribed.

    ⚠ THIS IS NOT A PAYMENT. It records WHICH tier an account is on; taking the
    money and remembering that it was taken is Phase 4 (`subscriptions`). Until
    then this is the manual lever, which is genuinely how early sales close.
    """
    return _set_fields(email, {"tier": (tier_id or "").strip().lower()})


def set_tier_expiry(email: str, expires_at: str | None) -> bool:
    """When the account's paid tier lapses. `None` = it does not.

    ⚠ THIS IS WHAT MAKES EXPIRY WORK WITHOUT A SCHEDULER. There is no cron in
    this app; `billing.tier_of` compares this field to the clock on every read,
    off the same document it was already loading. Access therefore ends on the
    minute rather than whenever somebody remembers to run a sweep — and there is
    no job that can silently stop running.
    """
    return _set_fields(email, {"tier_expires_at": expires_at})


def set_note(email: str, note: str) -> bool:
    """Save the administrator's private note on an account.

    Never shown to the user it is about — `_to_profile` does not read it, and
    `/auth/me` therefore cannot leak it.
    """
    return _set_fields(email, {"admin_note": note})


def record_login(email: str) -> None:
    """Stamp a successful sign-in onto the account. Never raises.

    Denormalised onto the user document rather than derived from the event log
    on every read: "last seen" is wanted on EVERY ROW of the admin user table,
    and an aggregation per row would make that page a hundred queries. The event
    log keeps the full history; this is the cached answer to the one question
    the table asks.
    """
    key = _normalize_email(email)
    try:
        if _use_local():
            with _lock:
                data = _local_load()
                if key not in data:
                    return
                data[key]["last_login_at"] = _now_iso()
                data[key]["login_count"] = int(data[key].get("login_count") or 0) + 1
                _local_save(data)
            return
        get_collection().update_one(
            {"email": key},
            {"$set": {"last_login_at": _now_iso()}, "$inc": {"login_count": 1}},
        )
    except Exception as e:  # noqa: BLE001 — a sign-in must not fail over this
        logger.warning("Could not record login for %s: %s", key, e)


def set_override(email: str, key: str, value: bool | None) -> bool:
    """Force one feature on or off for ONE account, or clear that force.

    ⚠ THIS IS THE HIGHEST-PRECEDENCE ANSWER IN THE RESOLVER, and the only thing
    that can reopen a feature whose status is "hidden" — which is how an
    administrator looks at something that is switched off for the site. See
    `features.resolve`.

    `value=None` removes the override, so the account goes back to being decided
    by the rollout rule. That is deliberately not the same as `False`: "no
    opinion" and "explicitly denied" behave differently the moment the rule
    changes underneath them.
    """
    normalized = _normalize_email(email)
    field = f"feature_overrides.{key}"

    if _use_local():
        with _lock:
            data = _local_load()
            if normalized not in data:
                return False
            overrides = data[normalized].setdefault("feature_overrides", {})
            if value is None:
                overrides.pop(key, None)
            else:
                overrides[key] = bool(value)
            _local_save(data)
            return True

    update = {"$unset": {field: ""}} if value is None else {"$set": {field: bool(value)}}
    return get_collection().update_one({"email": normalized}, update).matched_count > 0


def get_overrides(email: str) -> dict:
    """`{feature_key: bool}` for one account. Never raises."""
    try:
        raw = (get_user_by_email(email) or {}).get("feature_overrides") or {}
        return {k: bool(v) for k, v in raw.items() if isinstance(v, bool)}
    except Exception as e:  # noqa: BLE001 — no overrides is a safe answer
        logger.warning("Could not read feature overrides for %s: %s", email, e)
        return {}


def _set_fields(email: str, fields: dict) -> bool:
    """Write arbitrary fields onto a user record. INTERNAL — the callers above
    are the allow-list; this is deliberately not exported for general use."""
    key = _normalize_email(email)
    if _use_local():
        with _lock:
            data = _local_load()
            if key not in data:
                return False
            data[key].update(fields)
            _local_save(data)
            return True
    return get_collection().update_one({"email": key}, {"$set": fields}).matched_count > 0


# ===========================================================================
# Admin listing
# ===========================================================================
# Never leaves this module on a listing. `api_keys` is out for the same reason
# the password hash is: an administrator has no business reading a customer's
# third-party credentials, and a field that is never sent cannot leak.
_LIST_HIDDEN = ("password_hash", "api_keys")


def _public_user(doc: dict) -> dict:
    """A user document with the secrets stripped and the role resolved."""
    out = {k: v for k, v in (doc or {}).items() if k not in _LIST_HIDDEN and k != "_id"}
    out[ROLE_FIELD] = role_of(doc)
    out["disabled"] = bool(doc.get("disabled"))
    return out


def _search_filter(search: str):
    """A case-insensitive substring match over the fields worth searching.

    ⚠ THE INPUT IS ESCAPED. Straight into `$regex` it is a user-supplied pattern
    against a remote collection: `.*` scans the lot, an unbalanced `(` throws,
    and a nested quantifier is a denial of service. `re.escape` makes it the
    literal text the person typed, which is all a search box ever means.
    """
    pattern = re.escape(search.strip())
    return {
        "$or": [
            {f: {"$regex": pattern, "$options": "i"}}
            for f in ("email", "full_name", "display_name", "company")
        ]
    }


def list_users(
    limit: int = 50,
    skip: int = 0,
    search: str = "",
    role: str | None = None,
    disabled: bool | None = None,
    sort: str = "created_at",
    desc: bool = True,
) -> list[dict]:
    """A page of accounts for the admin table, secrets stripped.

    Sorting is over an ISO string for the timestamps, which orders correctly
    because every one this app writes is UTC.
    """
    limit = max(1, min(limit, config.ADMIN_MAX_PAGE))
    skip = max(0, skip)
    if sort not in ("created_at", "last_login_at", "email", "login_count"):
        sort = "created_at"

    if _use_local():
        with _lock:
            rows = list(_local_load().values())
        rows = [r for r in rows if _row_matches(r, search, role, disabled)]
        # `or ""` so an account that has never signed in sorts as the oldest
        # rather than raising on a None comparison.
        rows.sort(key=lambda r: r.get(sort) or "", reverse=desc)
        return [_public_user(r) for r in rows[skip : skip + limit]]

    query = _mongo_query(search, role, disabled)
    cursor = (
        get_collection()
        .find(query)
        .sort(sort, -1 if desc else 1)
        .skip(skip)
        .limit(limit)
    )
    return [_public_user(d) for d in cursor]


def count_users(
    search: str = "",
    role: str | None = None,
    disabled: bool | None = None,
    created_since: str | None = None,
) -> int:
    """How many accounts match. `created_since` is the dashboard's signup tile."""
    try:
        if _use_local():
            with _lock:
                rows = list(_local_load().values())
            return sum(
                1
                for r in rows
                if _row_matches(r, search, role, disabled)
                and (not created_since or (r.get("created_at") or "") >= created_since)
            )
        query = _mongo_query(search, role, disabled)
        if created_since:
            query["created_at"] = {"$gte": created_since}
        return get_collection().count_documents(query)
    except Exception as e:  # noqa: BLE001 — a dashboard tile, not a transaction
        logger.warning("Could not count users: %s", e)
        return 0


def count_users_on_tier(tier_id: str) -> int:
    """How many accounts are on one tier. Never raises.

    ⚠ THE DEFAULT TIER COUNTS EVERY ACCOUNT THAT HAS NO `tier` FIELD, which
    today is nearly all of them — an absent value means "the tier everybody
    starts on", not "no tier", and reporting 0 for Trial would make the pricing
    screen look like nobody had ever signed up.
    """
    from . import billing

    key = (tier_id or "").strip().lower()
    try:
        if _use_local():
            with _lock:
                rows = list(_local_load().values())
            return sum(1 for r in rows if billing.tier_of(r) == key)
        if key == billing.DEFAULT_TIER:
            query = {"$or": [{"tier": key}, {"tier": {"$exists": False}}, {"tier": None}, {"tier": ""}]}
        else:
            query = {"tier": key}
        return get_collection().count_documents(query)
    except Exception as e:  # noqa: BLE001 — a dashboard tile, not a transaction
        logger.warning("Could not count users on tier %s: %s", key, e)
        return 0


def _mongo_query(search: str, role: str | None, disabled: bool | None) -> dict:
    query: dict = {}
    if search:
        query.update(_search_filter(search))
    if role == ROLE_ADMIN:
        query[ROLE_FIELD] = ROLE_ADMIN
    elif role == ROLE_USER:
        # Absent means "user", so this cannot be an equality test.
        query[ROLE_FIELD] = {"$ne": ROLE_ADMIN}
    if disabled is not None:
        query["disabled"] = True if disabled else {"$ne": True}
    return query


def _row_matches(row: dict, search: str, role: str | None, disabled: bool | None) -> bool:
    """The local backend's filter — `_mongo_query` above, in Python."""
    if search:
        needle = search.strip().lower()
        haystack = " ".join(
            str(row.get(f) or "") for f in ("email", "full_name", "display_name", "company")
        ).lower()
        if needle not in haystack:
            return False
    if role and role_of(row) != role:
        return False
    if disabled is not None and bool(row.get("disabled")) != disabled:
        return False
    return True


# --- Third-party 3D API keys (stored plaintext under user.api_keys.{provider}) ---
def set_api_key(email: str, provider: str, api_key: str) -> bool:
    """Save a 3D provider API key on the user record."""
    key = _normalize_email(email)
    if _use_local():
        with _lock:
            data = _local_load()
            if key not in data:
                return False
            data[key].setdefault("api_keys", {})[provider] = api_key
            _local_save(data)
            return True
    result = get_collection().update_one(
        {"email": key}, {"$set": {f"api_keys.{provider}": api_key}}
    )
    return result.matched_count > 0


def get_api_key(email: str, provider: str) -> str | None:
    """Return the user's saved key for a provider, or None."""
    user = get_user_by_email(email) or {}
    return (user.get("api_keys") or {}).get(provider)


def get_saved_providers(email: str) -> dict:
    """Return {provider: True} for each provider the user has a key stored for."""
    user = get_user_by_email(email) or {}
    return {p: True for p, v in (user.get("api_keys") or {}).items() if v}


def delete_api_key(email: str, provider: str) -> bool:
    """Remove a saved provider key. Returns True if a user matched."""
    key = _normalize_email(email)
    if _use_local():
        with _lock:
            data = _local_load()
            if key not in data:
                return False
            (data[key].get("api_keys") or {}).pop(provider, None)
            _local_save(data)
            return True
    result = get_collection().update_one(
        {"email": key}, {"$unset": {f"api_keys.{provider}": ""}}
    )
    return result.matched_count > 0


def check_connection() -> dict:
    """Report user-store connectivity (never raises).

    Returns {"connected": bool, "db": str, "error": str | None}. Used by the
    /health endpoint so operators can see at a glance whether auth will work.
    """
    if _use_local():
        return {"connected": True, "db": f"local:{config.LOCAL_USERS_PATH}", "error": None}

    status = {"connected": False, "db": config.MONGODB_DB, "error": None}
    try:
        col = get_collection()
        col.database.client.admin.command("ping")
        status["connected"] = True
    except Exception as e:  # noqa: BLE001 — health must not throw
        status["error"] = str(e)
    return status
