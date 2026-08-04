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
    }
"""

import json
import logging
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
