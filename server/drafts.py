"""
drafts.py — the script the user is CURRENTLY writing, saved as they type.

One live draft per user. Until now a script only became durable once it had been
turned into a storyboard (it is stored on the job); anything typed and not yet
generated lived in React state alone and died with a page refresh. This is the
insurance for that window.

It is NOT a script library — there is exactly one draft per account, and saving
overwrites it. That keeps the contract simple: the text panel remembers what you
last had in it.

Backend follows the USER STORE (`API_USER_STORE`), so drafts land wherever the
accounts do — MongoDB in normal operation, a local JSON file when Mongo is
unreachable. Sharing one switch means there is no second thing to configure and
no way to end up with accounts in one place and their drafts in another.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from . import config
from .auth import CurrentUser, get_current_user
from .schemas import ScriptDraft, ScriptDraftUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scripts", tags=["scripts"])

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _use_local() -> bool:
    return config.USER_STORE == "local"


# ===========================================================================
# Local JSON-file backend (mirrors users.py)
# ===========================================================================
def _local_path() -> Path:
    return Path(config.LOCAL_DRAFTS_PATH)


def _local_load() -> dict:
    """Return {email: draft_dict}. Missing/corrupt file → empty store."""
    path = _local_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Local draft store at %s is unreadable — starting empty.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


# ===========================================================================
# Mongo backend
# ===========================================================================
_collection = None


def get_collection():
    """Return the drafts collection, connecting on first use.

    Reuses users.get_collection()'s client so the two stores share one
    connection pool rather than opening a second one to the same cluster.
    """
    global _collection
    if _collection is not None:
        return _collection

    with _lock:
        if _collection is not None:  # re-check inside the lock
            return _collection
        from .mongo import get_db

        col = get_db()[config.DRAFTS_COLLECTION]
        col.create_index("email", unique=True)
        _collection = col
        logger.info(
            "MongoDB draft store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.DRAFTS_COLLECTION,
        )
        return _collection


# ===========================================================================
# Store API
# ===========================================================================
def get_draft(email: str) -> dict | None:
    """The user's saved draft, or None when they have never saved one."""
    key = (email or "").strip().lower()
    if _use_local():
        with _lock:
            return _local_load().get(key)
    doc = get_collection().find_one({"email": key}, {"_id": 0, "email": 0})
    return doc


def save_draft(
    email: str, text: str, title: str = "", concept: dict | None = None
) -> dict:
    """Create or overwrite the user's draft. Returns the stored record.

    ⚠ `concept` rides along with the text it was developed FROM. Keeping the
    two in one record is the point: a refresh used to restore the box and lose
    the card, and the only way back to a card was to generate a new one — which
    returns a different film. They are one draft, so they are one row.
    """
    key = (email or "").strip().lower()
    record = {
        "text": text,
        "title": title,
        "concept": concept,
        "updated_at": _now_iso(),
    }
    if _use_local():
        with _lock:
            data = _local_load()
            data[key] = record
            _local_save(data)
        return record
    get_collection().update_one(
        {"email": key}, {"$set": {**record, "email": key}}, upsert=True
    )
    return record


def clear_draft(email: str) -> bool:
    """Delete the user's draft. True when one was actually removed."""
    key = (email or "").strip().lower()
    if _use_local():
        with _lock:
            data = _local_load()
            existed = data.pop(key, None) is not None
            if existed:
                _local_save(data)
        return existed
    return get_collection().delete_one({"email": key}).deleted_count > 0


# ===========================================================================
# Routes — all owner-scoped: a user only ever touches their OWN draft
# ===========================================================================
@router.get("/draft", response_model=ScriptDraft)
def read_draft(current: CurrentUser = Depends(get_current_user)):
    """The caller's saved draft. Never 404s — an empty draft is a valid state,
    and the client shouldn't have to treat 'nothing saved yet' as an error."""
    return ScriptDraft(**(get_draft(current.email) or {}))


@router.put("/draft", response_model=ScriptDraft)
def write_draft(
    body: ScriptDraftUpdate,
    current: CurrentUser = Depends(get_current_user),
):
    """Save (overwrite) the caller's draft — called on a debounce as they type."""
    if len(body.text) > config.MAX_SCRIPT_CHARS:
        raise HTTPException(
            # Literal 413: Starlette renamed this constant and deprecated the
            # old name, so the number outlives both spellings.
            status_code=413,
            detail=(
                f"Script is too long to autosave "
                f"({len(body.text):,} characters, limit {config.MAX_SCRIPT_CHARS:,})."
            ),
        )
    # The same ceiling as the text. A concept is a few hundred characters of
    # our own JSON; anything near the script limit is a paste into a scene
    # field, and it is cheaper to refuse it here than to store it for ever.
    if body.concept is not None:
        size = len(json.dumps(body.concept))
        if size > config.MAX_SCRIPT_CHARS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Concept is too large to autosave "
                    f"({size:,} characters, limit {config.MAX_SCRIPT_CHARS:,})."
                ),
            )
    return ScriptDraft(
        **save_draft(current.email, body.text, body.title, body.concept)
    )


@router.delete("/draft", status_code=204)
def remove_draft(current: CurrentUser = Depends(get_current_user)):
    """Discard the caller's draft (the 'clear' action on the text panel)."""
    clear_draft(current.email)
