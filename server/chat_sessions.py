"""
chat_sessions.py — THE ✨ AI EDITOR'S CONVERSATIONS, SAVED WITH THE PROJECT.

    one project  →  many chats  →  each chat a list of turns

⚠ **THE TRANSCRIPT USED TO BE THE BROWSER'S, AND THAT WAS THE WHOLE PROBLEM.**
It lived in one `localStorage` key per project, which means: one conversation per
film, gone on a different computer, gone when somebody clears site data, and no
way to keep "the sound pass" apart from "the titles pass". Asked for outright:
*"user new chat bana kar alag alag baat kar sake … aur sab chat save hona chahiye,
user jo karwaya hai usko us project mai dekh sake fir baad mai — project by
project save karna"*.

⚠ **IT IS STILL NOT THE MODEL'S MEMORY.** `POST /editor-chat/{id}/turn` stays
stateless and the browser still posts the whole conversation on every turn — see
that route's header for why. This store is the RECORD, read by a person looking
at what they had already done, not by the agent. Nothing in here is ever sent to
a model, and no route here spends a penny.

⚠ **IT FOLLOWS THE USER STORE (`API_USER_STORE`), LIKE `drafts.py`.** Accounts,
their drafts and the record of what those accounts said belong in one place, and
that is one switch to configure rather than three. MongoDB in normal operation,
a JSON file on disk when Mongo is unreachable.

⚠ **OWNERSHIP IS IN THE KEY, NOT ONLY IN THE ROUTE.** Every document carries
`owner` and every query filters on it, so a `job_id` guessed by hand still
returns nothing. The route checks ownership as well (`_owned_animatic`); this is
the second lock, and a store that can only be read by its owner cannot leak a
conversation through a bug in a route added later.

⚠ **A CHAT IS DELETED WITH ITS PROJECT.** `delete_project` sweeps them, or a
deleted film leaves its conversations behind for ever with nothing pointing at
them.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    """Minted here, not in the browser — two tabs open on one project must not
    be able to agree on the same id by accident."""
    return uuid.uuid4().hex[:16]


def _use_local() -> bool:
    return config.USER_STORE == "local"


def _key(email: str) -> str:
    return (email or "").strip().lower()


# ===========================================================================
# What a stored chat looks like
# ===========================================================================
def _summarise(doc: dict) -> dict:
    """A row for the 🕘 list: everything EXCEPT the turns.

    ⚠ THE LIST MUST NOT CARRY THE TRANSCRIPTS. Forty chats of sixty turns is
    megabytes to draw a dozen titles, on a panel that opens on every project.
    The same reasoning as `JobStore.list(drop=…)`, and the same trap avoided:
    this returns its own shape rather than a chat with an empty `turns`, so
    nothing downstream can mistake "not loaded" for "no messages".
    """
    turns = doc.get("turns") or []
    return {
        "session_id": doc.get("session_id", ""),
        "title": doc.get("title", ""),
        "turn_count": sum(1 for t in turns if (t or {}).get("role") == "user"),
        "created_at": doc.get("created_at", ""),
        "updated_at": doc.get("updated_at", ""),
    }


def _public(doc: dict) -> dict:
    """One whole chat, as the panel reads it."""
    return {
        "session_id": doc.get("session_id", ""),
        "title": doc.get("title", ""),
        "turns": doc.get("turns") or [],
        "created_at": doc.get("created_at", ""),
        "updated_at": doc.get("updated_at", ""),
    }


# ===========================================================================
# Local JSON-file backend (mirrors drafts.py)
# ===========================================================================
def _local_load() -> dict:
    """`{email: {job_id: {session_id: doc}}}`. Missing/corrupt → empty."""
    path = config.LOCAL_CHAT_SESSIONS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local chat store at %s is unreadable — starting empty.", path)
        return {}


def _local_save(data: dict) -> None:
    with open(config.LOCAL_CHAT_SESSIONS_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


# ===========================================================================
# Mongo backend
# ===========================================================================
_collection = None


def get_collection():
    """The chat-sessions collection, connecting on first use.

    Reuses the shared client, so this is not a second connection pool to the
    same cluster — the same thing `drafts.get_collection` says.
    """
    global _collection
    if _collection is not None:
        return _collection

    with _lock:
        if _collection is not None:  # re-check inside the lock
            return _collection
        from .mongo import get_db

        col = get_db()[config.CHAT_SESSIONS_COLLECTION]
        # ⚠ THE UNIQUE KEY IS ALL THREE. A session id is unique on its own by
        # construction, but the index is what makes an upsert from two tabs
        # land on ONE document instead of racing into two.
        col.create_index(
            [("owner", 1), ("job_id", 1), ("session_id", 1)], unique=True
        )
        # What the 🕘 list actually asks for: this project's chats, newest first.
        col.create_index([("owner", 1), ("job_id", 1), ("updated_at", -1)])
        _collection = col
        logger.info(
            "MongoDB chat-session store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.CHAT_SESSIONS_COLLECTION,
        )
        return _collection


def reset() -> None:
    """Drop the cached handle — the tests point the store at a temp file or a
    temp collection between cases and must not keep the previous one."""
    global _collection
    _collection = None


# ===========================================================================
# Store API — every call is owner-scoped, always
# ===========================================================================
def list_sessions(email: str, job_id: str) -> list[dict]:
    """This project's chats, newest first, WITHOUT their transcripts."""
    owner = _key(email)
    if _use_local():
        with _lock:
            rows = list((_local_load().get(owner, {}).get(job_id, {})).values())
    else:
        rows = list(
            get_collection().find({"owner": owner, "job_id": job_id}, {"_id": 0})
        )
    rows.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return [_summarise(d) for d in rows]


def get_session(email: str, job_id: str, session_id: str) -> dict | None:
    """One whole chat, or None when this owner has no such chat."""
    owner = _key(email)
    if _use_local():
        with _lock:
            doc = _local_load().get(owner, {}).get(job_id, {}).get(session_id)
    else:
        doc = get_collection().find_one(
            {"owner": owner, "job_id": job_id, "session_id": session_id},
            {"_id": 0},
        )
    return _public(doc) if doc else None


def count_sessions(email: str, job_id: str) -> int:
    owner = _key(email)
    if _use_local():
        with _lock:
            return len(_local_load().get(owner, {}).get(job_id, {}))
    return get_collection().count_documents({"owner": owner, "job_id": job_id})


def save_session(
    email: str,
    job_id: str,
    session_id: str,
    *,
    title: str | None = None,
    turns: list | None = None,
) -> dict:
    """Create or overwrite one chat. Returns the stored record.

    ⚠ `None` MEANS "LEAVE IT ALONE" AND `[]` MEANS "IT IS EMPTY". A rename posts
    a title and no turns; an autosave posts turns and no title. Collapsing the
    two with `or` would make renaming a chat delete its transcript, which is the
    kind of bug only the person it happened to ever finds.

    ⚠ `created_at` IS WRITTEN ONCE. On Mongo that is `$setOnInsert`, not `$set` —
    an upsert that re-stamped it would make every chat look new on every keypress
    and shuffle a list that is ordered by time.
    """
    owner = _key(email)
    now = _now_iso()
    patch: dict = {"updated_at": now}
    if title is not None:
        patch["title"] = title
    if turns is not None:
        patch["turns"] = turns

    if _use_local():
        with _lock:
            data = _local_load()
            byjob = data.setdefault(owner, {}).setdefault(job_id, {})
            doc = byjob.get(session_id) or {
                "owner": owner,
                "job_id": job_id,
                "session_id": session_id,
                "title": "",
                "turns": [],
                "created_at": now,
            }
            doc.update(patch)
            byjob[session_id] = doc
            _local_save(data)
        return _public(doc)

    get_collection().update_one(
        {"owner": owner, "job_id": job_id, "session_id": session_id},
        {
            "$set": patch,
            "$setOnInsert": {
                "owner": owner,
                "job_id": job_id,
                "session_id": session_id,
                "created_at": now,
                # ⚠ ONLY FOR THE HALF THAT WAS NOT SENT. A default inserted
                # beside the field it defaults would be Mongo writing the same
                # path twice in one update, which is an error, not a fallback.
                **({} if title is not None else {"title": ""}),
                **({} if turns is not None else {"turns": []}),
            },
        },
        upsert=True,
    )
    return get_session(owner, job_id, session_id) or _public(
        {"session_id": session_id, **patch}
    )


def delete_session(email: str, job_id: str, session_id: str) -> bool:
    """Remove one chat. True when one was actually there."""
    owner = _key(email)
    if _use_local():
        with _lock:
            data = _local_load()
            byjob = data.get(owner, {}).get(job_id, {})
            existed = byjob.pop(session_id, None) is not None
            if existed:
                _local_save(data)
        return existed
    return (
        get_collection()
        .delete_one({"owner": owner, "job_id": job_id, "session_id": session_id})
        .deleted_count
        > 0
    )


def delete_project(email: str, job_id: str) -> int:
    """Every chat of one project. Called when the project itself is deleted."""
    owner = _key(email)
    if _use_local():
        with _lock:
            data = _local_load()
            byjob = data.get(owner, {}).pop(job_id, None)
            if byjob is None:
                return 0
            _local_save(data)
            return len(byjob)
    return get_collection().delete_many({"owner": owner, "job_id": job_id}).deleted_count


def drop_one_unused(email: str, job_id: str) -> bool:
    """Delete the OLDEST chat nobody ever typed in. True when one was found.

    ⚠ THIS IS THE ONLY AUTOMATIC DELETE IN THE FILE, AND IT ONLY EVER TOUCHES A
    CHAT WITH ZERO MESSAGES IN IT. A ＋ pressed by mistake leaves an empty chat,
    and those must not be what fills somebody's ceiling — but pruning the oldest
    chat regardless would throw away work to make room, silently, which is the
    one thing a store like this must never do. When there is nothing empty to
    drop, the caller refuses out loud instead.
    """
    owner = _key(email)
    rows = [r for r in list_sessions(owner, job_id) if not r["turn_count"]]
    if not rows:
        return False
    rows.sort(key=lambda r: r.get("updated_at", ""))
    return delete_session(owner, job_id, rows[0]["session_id"])
