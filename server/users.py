"""
users.py — MongoDB-backed user store for authentication.

Stores one document per user in the `users` collection:
    {
        "_id": ObjectId,
        "email": "user@example.com",   (unique, lowercased)
        "password_hash": "<bcrypt>",
        "created_at": "<iso8601>",
        "disabled": false,
    }
"""

import logging
import threading
from datetime import datetime, timezone

from . import config

logger = logging.getLogger(__name__)

_client = None
_collection = None
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_collection():
    """Return the users collection, connecting to MongoDB on first use.

    A unique index on `email` is ensured once so duplicate registrations fail
    at the database level, not just in application code.
    """
    global _client, _collection
    if _collection is not None:
        return _collection

    with _lock:
        if _collection is not None:  # re-check inside the lock
            return _collection
        from pymongo import MongoClient

        _client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
        db = _client[config.MONGODB_DB]
        col = db[config.USERS_COLLECTION]
        col.create_index("email", unique=True)
        _collection = col
        logger.info(
            "MongoDB user store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.USERS_COLLECTION,
        )
        return _collection


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(email: str) -> dict | None:
    """Return the raw user document (incl. password_hash) or None."""
    return get_collection().find_one({"email": _normalize_email(email)})


def create_user(email: str, password_hash: str) -> dict:
    """Insert a new user. Raises DuplicateUser if the email already exists."""
    from pymongo.errors import DuplicateKeyError

    doc = {
        "email": _normalize_email(email),
        "password_hash": password_hash,
        "created_at": _now_iso(),
        "disabled": False,
    }
    try:
        result = get_collection().insert_one(doc)
    except DuplicateKeyError as e:
        raise DuplicateUser(email) from e
    doc["_id"] = result.inserted_id
    return doc


def update_password(email: str, password_hash: str) -> bool:
    """Set a new password hash for an existing user. Returns True if updated."""
    result = get_collection().update_one(
        {"email": _normalize_email(email)},
        {"$set": {"password_hash": password_hash}},
    )
    return result.matched_count > 0


def delete_user(email: str) -> bool:
    """Permanently delete a user by email. Returns True if a user was removed."""
    result = get_collection().delete_one({"email": _normalize_email(email)})
    return result.deleted_count > 0


def check_connection() -> dict:
    """Ping MongoDB and report connectivity (never raises).

    Returns {"connected": bool, "db": str, "error": str | None}. Used by the
    /health endpoint so operators can see at a glance whether auth will work.
    """
    status = {"connected": False, "db": config.MONGODB_DB, "error": None}
    try:
        col = get_collection()
        col.database.client.admin.command("ping")
        status["connected"] = True
    except Exception as e:  # noqa: BLE001 — health must not throw
        status["error"] = str(e)
    return status


class DuplicateUser(Exception):
    """Raised when registering an email that already exists."""

    def __init__(self, email: str):
        super().__init__(f"User already exists: {email}")
        self.email = email
