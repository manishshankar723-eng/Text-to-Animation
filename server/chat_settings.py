"""chat_settings.py — how the ✨ AI Editor chat BEHAVES, owned by the admin panel.

⚠ **ONE ROW, NOT A LIST**, exactly like `branding.py`: there is one chat feature
being configured, so this store holds a SINGLE document under `_DOC_ID` and every
reader asks for that same one.

---------------------------------------------------------------------------
⚠ **THREE DIFFERENT QUESTIONS, THREE DIFFERENT OWNERS. THIS FILE OWNS ONE.**
---------------------------------------------------------------------------
The admin panel's Chat tab shows all three together, because an operator thinks
of them as one screen. They are NOT one store, and merging them would be the
same mistake `features.py` opens by warning about:

    "Is the chat switched on, and for whom?"   →  features.py, `cap.editor-chat`
                                                  (status / rollout / min_tier)
    "How many messages does a tier get?"       →  billing.py, the tier's
                                                  `limits["chat_turns"]`
    "How does it behave when it IS on?"        →  HERE

The middle one is the one people try to put here, and it must not go here. The
rule at the top of `server/usage.py` is that a counter's name matches the TIER'S
`limits` key exactly, "because the pricing card quotes those keys and enforcement
that used different names would describe a different product from the one being
sold". A second turn-limit table in this file would be a number the pricing page
does not know about, and the first customer to hit it would be reading a limit
nobody advertised.

---------------------------------------------------------------------------
⚠ **THE DOCK IS A SETTING BECAUSE IT WAS ASKED TO BE ONE.**
---------------------------------------------------------------------------
*"tu dono kar do mai admin panel se change kar lunga"* — the panel can open as a
right-hand dock (what Descript's Underlord and Premiere's AI Assistant both do),
slide out of the sidebar under the button that launched it, or float as a small
window the person drags where they want it. All three are built; this decides
which one a deployment gets. `DOCK_USER` hands the choice to the person using the
editor and remembers it in their browser.

⚠ **AND `opacity` + `blur` ARE THE OTHER HALF OF THAT REQUEST** — *"admin panel mai ai
editor se chatbot panel ko transparent kar sake"*. They are a percentage and a
pixel count, they are the operator's to set, and the client turns each into one
CSS custom property. They are NOT user preferences: a customer who drags the
panel to 40% and then cannot read the chat has no way back, so the numbers live
here where support can see them.

⚠ **AND `blur` EXISTS BECAUSE A HARD-CODED ONE WAS WRONG TWICE** — 16px baked in
(unreadable-in-light), then removed (unreadable-at-low-opacity). Both were one
number chosen for every screen. It is a slider now, next to `opacity`, and the
two are judged together against a real deployment.

---------------------------------------------------------------------------
⚠ **THE TWO SAFETY RAILS DEFAULT ON AND SHOULD STAY ON.**
---------------------------------------------------------------------------
`ask_on_spend` and `ask_on_destructive` are the "ask before you act" half of the
product. They are switches rather than constants because an operator running an
internal deployment may genuinely want a faster loop — but the failure they
prevent is a typed sentence spending real money, so the defaults are on and the
admin screen says what turning one off means.

⚠ **AND `allow_paid_passes` IS NOT ONE OF THOSE RAILS — IT IS A WALL.** The chat
can never start a render itself: nothing in this feature spends anything but text
quota. See the module docstring of `server/director.py`, which states the same
rule for the same reason.

⚠ **WHAT IT ACTUALLY CONTROLS TODAY IS ONE SENTENCE IN THE PROMPT**, and the
sentence it used to produce was a refusal: switched off, `rails_text` told the
model *"this deployment does NOT let the chat start paid work at all — do not
offer to start one"*, and the model duly changed the subject when asked for a
voiceover. It ships ON now, which makes the chat OFFER the paid work and name the
button that starts it. ⚠ **A PRICED CONFIRM INSIDE THE CHAT IS NOT BUILT** —
nothing on the client reads this flag (only `AdminChat.jsx`, which sets it), so
do not read the old wording here as a description of a feature that exists.

⚠ **IT FAILS BACK TO THE DEFAULTS, NEVER TO NOTHING.** An unreachable store
answers with the shipped configuration, which is a working chat. Same rule as
branding and features: a settings read that fails must never produce a dead
feature.

Backend follows the USER STORE (`API_USER_STORE`), like every other panel store.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# The single document's id, in both stores.
_DOC_ID = "editor_chat"

# ---------------------------------------------------------------------------
# Where the panel opens
# ---------------------------------------------------------------------------
# ⚠ THE IDS GO STRAIGHT INTO A CSS CLASS on the client (`ec-dock-right` /
# `ec-dock-sidebar` / `ec-dock-float`), so they are checked against this list
# rather than trusted.
DOCK_RIGHT = "right"
DOCK_SIDE = "sidebar"
# ⚠ THE FLOATING WINDOW IS THE ONE THAT MOVES. Both docks above are pinned to an
# edge by CSS, so "put it somewhere else" has no answer in either of them — asked
# for outright: *"chat bot ka popup screen ko move kar sake and chhota aur bara
# kar sake"*. This one is placed by the person using it and remembers where it
# was left. The two docked ones resize (width only); only this one moves.
DOCK_FLOAT = "float"
DOCK_USER = "user"
DOCKS = (DOCK_RIGHT, DOCK_SIDE, DOCK_FLOAT, DOCK_USER)

# What each one is, in the words the admin screen prints. Kept beside the ids so
# a dock added later cannot be added without also being described.
DOCK_INFO = {
    DOCK_RIGHT: {
        "label": "Right-hand dock",
        "note": "Opens as a column on the right of the editor, beside the timeline. "
                "What Descript and Premiere both do — most room to read a plan.",
    },
    DOCK_SIDE: {
        "label": "Slides out of the sidebar",
        "note": "Opens straight out of the ✨ AI Editor button in the rail. "
                "Keeps the editor full width; narrower.",
    },
    DOCK_FLOAT: {
        "label": "Floating window",
        "note": "Opens as a small window over the editor. Drag its title bar to "
                "move it anywhere, drag the bottom-right corner to resize it, and "
                "it remembers where it was left in that browser.",
    },
    DOCK_USER: {
        "label": "Let each person choose",
        "note": "Both are offered and the editor remembers the choice in that "
                "browser. Pick this if you are not sure.",
    },
}

# ---------------------------------------------------------------------------
# Bounds on the numbers an operator can type
# ---------------------------------------------------------------------------
# ⚠ A SETTINGS SCREEN IS AN INPUT LIKE ANY OTHER. `transcript_keep` becomes the
# size of every prompt this feature sends; a fat-fingered 2000 there is a bill,
# not a preference. Clamped rather than rejected, so a save never fails over a
# number — the screen shows what was actually stored.
LIMITS = {
    # How many past messages ride along on each turn. 20 is ten exchanges, which
    # covers "no, the other one" without re-sending an hour of conversation.
    "transcript_keep": {"min": 4, "max": 60, "default": 20},
    # A hard stop on one conversation, so a stuck loop cannot bill all night.
    # 0 means no ceiling beyond the tier's monthly count.
    "max_turns_per_session": {"min": 0, "max": 500, "default": 120},
    # How many shots of the film are described to the model before the read-model
    # is summarised instead. A 500-shot project must not put 500 shots in a prompt.
    "shot_detail_limit": {"min": 10, "max": 200, "default": 60},
    # ⚠ HOW SOLID THE PANEL IS, AS A PERCENTAGE — 100 is the panel this app has
    # always had, and anything lower lets the film show through it. Asked for
    # outright: *"admin panel mai ai editor se chatbot panel ko transparent kar
    # sake"*.
    #
    # ⚠ **THE FLOOR WAS 40 FOR ONE DAY AND 40 WAS THE WRONG NUMBER.** It was
    # picked from the DARK theme, where a dark panel over a dark editor is
    # obviously see-through by 60%. The light theme is `--panel: #ffffff` over an
    # `#f4f6fa` page — **white on white** — so 40% white mixed into white is a
    # difference nobody can see, and the report was exactly that: *"dark mai
    # thora ho raha hai white mai to ho hi nhi raha hai"*. A floor set by how one
    # theme happens to look is a floor that lies about the other one.
    #
    # ⚠ **SO IT GOES TO 0, AND THE OPERATOR JUDGES** — *"0 to 100 rakho mai admin
    # panel se check kar lunga kitna better hai"*. 0 is a panel with no ground at
    # all, which is a real answer over a full-screen preview and a bad one over a
    # busy timeline. The admin screen says so beside the slider; it is not this
    # file's job to decide it for them.
    "opacity": {"min": 0, "max": 100, "default": 100},
    # ⚠ HOW MUCH THE FILM UNDERNEATH IS BLURRED, IN PIXELS — and it is a SETTING
    # rather than a constant because the constant was wrong twice. It shipped as
    # a hard-coded `blur(16px)`, which is right over a busy timeline in the dark
    # theme and is white-frosted-onto-white in the light one; it was then removed
    # entirely, and the text became hard to read at low opacity. Both of those
    # were me picking a number for somebody else's screen. Asked for outright:
    # *"text padhne mai mushkil hai isliye blur daalo — tum nhi, admin panel pe
    # daal do, mai set kar lunga blur ko v"*.
    #
    # ⚠ **0 IS THE DEFAULT AND IT IS THE HONEST ONE.** It is exactly what is on
    # screen today, so nobody's deployment changes underneath them, and the admin
    # screen says what raising it buys. ⚠ **AND IT ONLY DOES ANYTHING WHEN
    # `opacity` IS BELOW 100** — there is nothing showing through a solid panel to
    # blur, and `backdrop-filter` is a per-frame GPU cost over a playing timeline.
    "blur": {"min": 0, "max": 40, "default": 0},
}

EDITABLE = frozenset({
    "dock",
    "model",
    "planner_model",
    "transcript_keep",
    "max_turns_per_session",
    "shot_detail_limit",
    "opacity",
    "blur",
    "ask_on_spend",
    "ask_on_destructive",
    "allow_paid_passes",
    "greeting",
})

GREETING_MAX_CHARS = 240


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def defaults() -> dict:
    """The shipped configuration. ⚠ A WORKING CHAT, not an empty one."""
    return {
        "id": _DOC_ID,
        "dock": DOCK_RIGHT,
        # "" → whatever `llm_json` resolves, which is the Director's model and
        # therefore the one already known to answer with valid JSON here. ⚠ NOT a
        # hard-coded id: `AGENTS.md` warns that `gemini-2.5-flash` is a rolling
        # alias, and pinning one in a settings default would freeze a deployment
        # onto a snapshot nobody chose.
        "model": "",
        # The model used for the turns that actually write an EDIT PLAN, when it
        # should differ from the one that just talks. "" → same as `model`.
        # ⚠ THIS IS THE CHEAPEST REAL LEVER IN THE FEATURE: most turns are
        # conversation, and paying planner prices for "how many shots is this?"
        # is where the money goes.
        "planner_model": "",
        "transcript_keep": LIMITS["transcript_keep"]["default"],
        "max_turns_per_session": LIMITS["max_turns_per_session"]["default"],
        "shot_detail_limit": LIMITS["shot_detail_limit"]["default"],
        # Fully solid, which is the panel that shipped. See LIMITS above.
        "opacity": LIMITS["opacity"]["default"],
        # No blur, which is also what is on screen today. See LIMITS above.
        "blur": LIMITS["blur"]["default"],
        "ask_on_spend": True,
        "ask_on_destructive": True,
        # ⚠ ON SINCE 2026-09-03, AND WHAT IT TURNS ON IS THE OFFER. Off, the
        # rails told the model not to offer paid work at all, so the chat went
        # quiet about the three most valuable things this app does — ask it for a
        # voiceover and it changed the subject. Asked for outright: *"jo free hai
        # wo free mai hoga aur jismai paisa lagta hai usmai paisa lagega — uske
        # liye subscription/plan lega agar nahi liya hua hai to"*.
        #
        # ⚠ IT NEVER DECIDES WHETHER THE CHAT MAY SPEND — nothing in this feature
        # may, ever. The spend goes through the door ✨ Animate uses, with the
        # estimate on the button, and the chat's job is to name that door.
        # `ask_on_spend` (also on) is what makes it ask before it offers.
        "allow_paid_passes": True,
        # The first line in an empty chat. "" → the client's own wording, which
        # is written to match the rest of the editor's voice.
        "greeting": "",
        "updated_at": None,
        "updated_by": None,
    }


# ===========================================================================
# Cleaning
# ===========================================================================
def _clamp_int(value, spec, fallback) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(spec["min"], min(spec["max"], n))


def _clean_model(value) -> str:
    """A model id, trimmed. ⚠ NOT validated against a list, on purpose.

    Every provider this app can reach names its models differently, and
    `DIRECTOR_PROVIDER=openai_compatible` can point at anything with an
    OpenAI-shaped endpoint (see the note in `AGENTS.md`). A whitelist here would
    be a list that goes stale the week a new model ships, in the direction that
    HURTS — refusing the id an operator is trying to move to.
    """
    return " ".join(str(value or "").split())[:120]


def clean(fields: dict) -> dict:
    """An admin's edits, made safe to store. Never raises; unknown keys ignored."""
    base = defaults()
    out: dict = {}
    for key, value in (fields or {}).items():
        if key not in EDITABLE:
            continue
        if key == "dock":
            out["dock"] = value if value in DOCKS else base["dock"]
        elif key in ("model", "planner_model"):
            out[key] = _clean_model(value)
        elif key in LIMITS:
            out[key] = _clamp_int(value, LIMITS[key], base[key])
        elif key == "greeting":
            out["greeting"] = " ".join(str(value or "").split())[:GREETING_MAX_CHARS]
        else:
            out[key] = bool(value)
    return out


# ===========================================================================
# Storage — the same two backends every panel store uses
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_CHAT_SETTINGS_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local chat-settings store at %s is unreadable — using defaults.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


_collection = None


def get_collection():
    """The chat-settings collection, connecting on first use."""
    global _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        from .mongo import get_db

        col = get_db()[config.CHAT_SETTINGS_COLLECTION]
        try:
            col.create_index("id", unique=True)
        except Exception as e:  # noqa: BLE001 — an index is an optimisation
            logger.warning("Could not index chat settings (%s).", e)
        _collection = col
        logger.info(
            "MongoDB chat-settings store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.CHAT_SETTINGS_COLLECTION,
        )
        return _collection


# ===========================================================================
# The cache
# ===========================================================================
# ⚠ ONLY EVER REPLACED BY A SUCCESSFUL READ, so a database that goes away leaves
# the last known-good settings in force instead of silently reverting every
# session to the shipped defaults mid-day. Same rule as `features._cache` and
# `branding._cache`.
_cache: dict | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    """Drop the cache so the next read goes to the store. Called after a write."""
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def get_settings(fresh: bool = False) -> dict:
    """The one settings row, defaults merged in. NEVER RAISES.

    A field added in a later release appears with its default on a document
    written before it existed, which is what makes this safe to extend.
    """
    global _cache, _cache_at

    if not fresh:
        with _cache_lock:
            if _cache is not None and time.time() - _cache_at < config.CHAT_SETTINGS_CACHE_TTL_S:
                return dict(_cache)

    row = {}
    try:
        if _use_local():
            with _lock:
                row = _local_load()
        else:
            row = get_collection().find_one({"id": _DOC_ID}) or {}
    except Exception as e:  # noqa: BLE001 — a dead store must not kill the chat
        logger.warning("Could not read chat settings (%s) — serving the last good copy.", e)
        with _cache_lock:
            if _cache is not None:
                return dict(_cache)
        return defaults()

    merged = defaults()
    for key, value in (row or {}).items():
        if key in merged or key in ("updated_at", "updated_by"):
            merged[key] = value
    # ⚠ RE-CLEANED ON THE WAY OUT, not just on the way in. A row written by an
    # older build, by a migration, or straight into Mongo by hand has never been
    # through `clean()` — and `transcript_keep: 5000` read back unchecked is a
    # prompt nobody meant to send.
    merged.update(clean(merged))
    merged["id"] = _DOC_ID

    with _cache_lock:
        _cache = dict(merged)
        _cache_at = time.time()
    return dict(merged)


def save_settings(fields: dict, actor: str = "") -> dict:
    """Write the panel's changes onto the one row. Returns the merged result."""
    row = clean(fields)
    row["id"] = _DOC_ID
    row["updated_at"] = _now_iso()
    row["updated_by"] = (actor or "").strip().lower() or None

    if _use_local():
        with _lock:
            data = _local_load()
            data.update(row)
            _local_save(data)
    else:
        get_collection().update_one({"id": _DOC_ID}, {"$set": row}, upsert=True)
    _bump()
    logger.info("[chat-settings] saved by %s: %s", row["updated_by"] or "?", sorted(fields or {}))
    return get_settings(fresh=True)


def admin_payload() -> dict:
    """Everything the admin screen needs to draw itself, in one call.

    ⚠ THE BOUNDS TRAVEL WITH THE VALUES. The screen puts `min`/`max` on its
    number inputs, and a second copy of them in JSX would be a second opinion
    about what is legal — the browser would accept 2000 and the server would
    quietly store 60, which is a settings page that lies about what it saved.
    """
    return {
        "settings": get_settings(fresh=True),
        "docks": [{"id": d, **DOCK_INFO[d]} for d in DOCKS],
        "limits": {k: dict(v) for k, v in LIMITS.items()},
        "greeting_max": GREETING_MAX_CHARS,
        # ⚠ NAMED HERE SO THE SCREEN CANNOT INVENT ITS OWN. These are the keys
        # the other two owners use, and the Chat tab edits them THROUGH those
        # owners rather than keeping copies. See the module docstring.
        "feature_key": "cap.editor-chat",
        "limit_key": "chat_turns",
    }
