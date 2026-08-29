"""branding.py — the app's own NAME and MARK, moved out of the JSX and into the
admin panel.

THE PROBLEM THIS SOLVES, in one sentence: the product name was typed into eight
files and the mark was drawn in two more, so renaming the app meant a developer,
an editor and a redeploy — and the drawn mark could never be the customer's own
logo at all.

⚠ **ONE ROW, NOT A LIST.** There is exactly one product being named, so this
store holds a SINGLE document under `_DOC_ID` and every reader asks for that same
one. It is the only store in `server/` shaped that way, and it is deliberate: a
"brandings" collection with one row in it invites a second row nobody can
explain, and a `GET /public/branding` that has to pick which one is live.

⚠ **THE READ IS PUBLIC AND UNAUTHENTICATED, BECAUSE THE NAME IS ON THE LANDING
PAGE.** The sign-in card, the marketing page and a shared storyboard link are all
seen by somebody with no account, and every one of them prints the name and draws
the mark. Same reasoning as `/public/workflows` and `GET /billing/tiers`: what
you are CALLED is public by nature. Nothing here leaks anything else — the
payload is a name, a URL and a stamp.

⚠ **THE STAMP IS THE CACHE.** The logo's URL carries the id of the file it is
serving (`/public/branding/logo/{stamp}`), so a NEW upload is a NEW address —
every browser, every proxy and every already-open tab picks it up the moment the
name call answers, and nobody has to reason about cache headers. That is why the
id is regenerated on every upload rather than the bytes being written over the
same path: overwriting one URL is exactly how a logo change fails to appear for
the one person who most needed to see it.

⚠ **TWO LOGO SLOTS, ONE PER THEME, AND THAT IS NOT A LUXURY.** A logo is a
FLAT PICTURE — unlike the drawn mark below it, which is painted in `currentColor`
and re-colours itself for free. The first white wordmark uploaded here vanished
into the light theme completely: *"jab mai dark mode mai hun to mera logo dikh
raha hai magar jab light mode mai karta hun to mera logo white mai merge ho raha
hai."* So there is a `dark` slot and a `light` slot, and the app picks by theme.

⚠ **EITHER SLOT FILLS IN FOR THE OTHER** (`resolve_slot`). One upload is a
complete, working answer — the commonest logo is a full-colour mark that reads on
both grounds, and forcing somebody to upload the same file twice to get started
would be a worse product than the one-slot version this replaces. The second slot
is there for when one file genuinely cannot do both.

⚠ **IT FAILS BACK TO THE BUILT-IN, NEVER TO NOTHING.** An unreachable store, a
missing file, a half-finished upload — every one of them answers with the
compiled-in name and no logo, which is the app exactly as it shipped. A brand
call that fails must never produce a screen with no title on it.

Backend follows the USER STORE (`API_USER_STORE`), like every other panel store.
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["branding"])

_lock = threading.Lock()

# The single document's id, in both stores.
_DOC_ID = "app"

# ⚠ THE COMPILED-IN NAME, AND THE ONE PLACE IT IS STILL WRITTEN DOWN. Every
# screen used to carry its own copy of this string; they all read the store now,
# and the store falls back to here. Changing the shipped default is a code
# change — changing what customers SEE is a click in the panel.
DEFAULT_NAME = "Aniwala AI Studio"

# A name has to fit the sidebar, which ellipsises (`.sb-brand-name`), and the
# admin top bar, which does too. 40 is measured against the 280px rail at its
# real font — see `tests/brand_landing_check.py`, which fails if the rail trims.
NAME_MAX_CHARS = 40

# What the panel may write. Anything else in a PATCH body is ignored rather than
# rejected, the same way `features.save_feature` treats its own EDITABLE set.
EDITABLE = frozenset({"name"})

# The logo, once normalised. Kept small because EVERY visitor downloads it on
# EVERY cold page load, including the marketing page — a 4000px master would be
# a megabyte on the front door.
LOGO_MAX_PX = 512
ALLOWED_LOGO_TYPES = ("image/png", "image/jpeg", "image/webp")

# ---------------------------------------------------------------------------
# The two slots
# ---------------------------------------------------------------------------
# ⚠ THE DARK SLOT IS STORED UNDER THE UN-SUFFIXED `logo_id`, AND THAT IS NOT
# UNTIDINESS — it is the field every deployment that uploaded a logo before the
# split already has. Renaming it to `logo_dark_id` would orphan those files: the
# app would come back up wearing the built-in mark and the person who uploaded
# theirs would have no idea why. `dark` is also the right one to inherit it,
# because dark is this app's default theme and is what that upload was made for.
SLOT_DARK = "dark"
SLOT_LIGHT = "light"
SLOTS = (SLOT_DARK, SLOT_LIGHT)
_SLOT_FIELD = {SLOT_DARK: "logo_id", SLOT_LIGHT: "logo_light_id"}
# The slot each one borrows from when it is empty. See `resolve_slot`.
_SLOT_FALLBACK = {SLOT_DARK: SLOT_LIGHT, SLOT_LIGHT: SLOT_DARK}


def slot_field(slot: str) -> str:
    """`"light"` → `"logo_light_id"`. Raises ValueError on anything else."""
    try:
        return _SLOT_FIELD[slot]
    except KeyError:
        raise ValueError(f"Unknown logo slot: {slot!r}. Use one of {', '.join(SLOTS)}.")


def resolve_slot(row: dict, slot: str) -> str:
    """The logo id a theme should actually draw — its own, or the other one.

    ⚠ THE FALLBACK IS THE WHOLE REASON ONE UPLOAD IS ENOUGH. A deployment with a
    single full-colour mark uploads it once and both themes use it; only somebody
    whose mark genuinely cannot work on both grounds ever touches the second slot.
    """
    own = row.get(slot_field(slot)) or ""
    if own:
        return own
    return row.get(slot_field(_SLOT_FALLBACK[slot])) or ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _defaults() -> dict:
    return {
        "id": _DOC_ID,
        "name": DEFAULT_NAME,
        # "" in BOTH means: draw the built-in mark
        # (`client/src/components/Logo.jsx`). "" in one of them means: borrow the
        # other — see `resolve_slot`. ⚠ `logo_id` IS THE DARK SLOT; read the note
        # on `SLOT_DARK` before renaming it.
        "logo_id": "",
        "logo_light_id": "",
        "updated_at": None,
        "updated_by": None,
    }


def clean_name(name: str | None) -> str:
    """A typed name, made safe to print. Empty → the built-in default.

    ⚠ WHITESPACE IS COLLAPSED, NOT JUST TRIMMED. The field is a textarea (it has
    to wrap while being typed), so a pasted name can arrive with a newline in the
    middle of it — and a newline inside `.sb-brand-name`, which is
    `white-space: nowrap`, is an invisible character that silently widens the row.
    """
    clean = re.sub(r"\s+", " ", (name or "")).strip()
    return clean[:NAME_MAX_CHARS] or DEFAULT_NAME


# ===========================================================================
# Storage
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_BRANDING_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local branding store at %s is unreadable — using defaults.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


_collection = None


def get_collection():
    """The branding collection, connecting on first use."""
    global _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        from .mongo import get_db

        col = get_db()[config.BRANDING_COLLECTION]
        try:
            col.create_index("id", unique=True)
        except Exception as e:  # noqa: BLE001 — an index is an optimisation
            logger.warning("Could not index branding (%s).", e)
        _collection = col
        logger.info(
            "MongoDB branding store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.BRANDING_COLLECTION,
        )
        return _collection


# ===========================================================================
# The cache
# ===========================================================================
# ⚠ ONLY EVER REPLACED BY A SUCCESSFUL READ, so a database that goes away leaves
# the last known-good name on screen instead of reverting every visitor to the
# built-in one mid-session. Same rule as `features._cache`.
_cache: dict | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    """Drop the cache so the next read goes to the store. Called after a write."""
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def get_branding(fresh: bool = False) -> dict:
    """The one branding row, defaults merged in. NEVER RAISES.

    A field added in a later release appears with its default on a document
    written before it existed, exactly as `all_features` merges its catalogue.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            if _cache is not None and now - _cache_at < config.BRANDING_CACHE_TTL_S:
                return _cache

    merged = _defaults()
    try:
        stored = _read_stored()
        merged.update({k: v for k, v in stored.items() if v is not None and k != "_id"})
        merged["id"] = _DOC_ID
        merged["name"] = clean_name(merged.get("name"))
    except Exception as e:  # noqa: BLE001 — fail back to the built-in; see the docstring
        with _cache_lock:
            if _cache is not None:
                logger.warning("Branding store unreachable (%s) — serving the last known good.", e)
                return _cache
        logger.warning("Branding store unreachable (%s) — serving the built-in name.", e)
        return merged

    # ⚠ A LOGO ID WHOSE FILE IS GONE IS NOT A LOGO. The document and the file are
    # two different stores and they can drift — a restored database, a wiped
    # uploads volume — and the failure mode without this check is every screen in
    # the app drawing a broken-image icon where its mark should be. Checked PER
    # SLOT, so losing the light-mode file leaves the dark one working and the
    # light theme simply borrows it.
    for _slot in SLOTS:
        field = slot_field(_slot)
        if merged.get(field) and not os.path.isfile(logo_path(merged[field])):
            logger.warning("Branding logo %s is recorded but missing on disk.", merged[field])
            merged[field] = ""

    with _cache_lock:
        _cache = merged
        _cache_at = now
    return merged


def _read_stored() -> dict:
    if _use_local():
        with _lock:
            return _local_load()
    return get_collection().find_one({"id": _DOC_ID}) or {}


def save_branding(fields: dict, actor: str = "") -> dict:
    """Write the panel's changes onto the one row. Returns the merged result."""
    clean = {k: v for k, v in (fields or {}).items() if k in EDITABLE}
    if "name" in clean:
        clean["name"] = clean_name(clean["name"])
    _write(clean, actor)
    return get_branding(fresh=True)


def _write(clean: dict, actor: str) -> None:
    """The store half of a save. `clean` is already validated."""
    clean = dict(clean)
    clean["id"] = _DOC_ID
    clean["updated_at"] = _now_iso()
    clean["updated_by"] = (actor or "").strip().lower() or None

    if _use_local():
        with _lock:
            data = _local_load()
            data.update(clean)
            _local_save(data)
    else:
        get_collection().update_one({"id": _DOC_ID}, {"$set": clean}, upsert=True)
    _bump()


# ===========================================================================
# The logo file
# ===========================================================================
# ⚠ THE ID GOES INTO A FILE PATH AND INTO A URL, so it is checked against this
# rather than trusted. `save_logo` generates it and nothing else may, but the
# public route takes one straight off the wire.
_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def logo_path(logo_id: str) -> str:
    """Where one logo's bytes live."""
    return os.path.join(config.BRANDING_DIR, f"{logo_id}.png")


def save_logo(png_bytes: bytes, slot: str = SLOT_DARK, actor: str = "") -> dict:
    """Store already-normalised PNG bytes in one slot. Returns the new row.

    ⚠ A NEW ID EVERY TIME, and the old file is deleted AFTER the document points
    at the new one — never before. Between those two writes the OLD logo is still
    the live one and is still on disk, so a crash mid-upload leaves the app
    wearing its previous mark rather than a broken image. See the module note on
    the stamp.
    """
    field = slot_field(slot)
    previous = get_branding(fresh=True).get(field) or ""
    logo_id = uuid.uuid4().hex[:12]
    os.makedirs(config.BRANDING_DIR, exist_ok=True)
    with open(logo_path(logo_id), "wb") as fh:
        fh.write(png_bytes)

    _write({field: logo_id}, actor)

    if previous and previous != logo_id:
        _delete_file(previous)
    return get_branding(fresh=True)


def clear_logo(slot: str = SLOT_DARK, actor: str = "") -> dict:
    """Empty one slot. Returns the new row.

    ⚠ CLEARING ONE SLOT IS NOT "NO LOGO" — the theme it belonged to falls back to
    the OTHER slot (`resolve_slot`), and only clearing BOTH puts the built-in
    drawn mark back. That is the honest behaviour and the panel says so, because
    "Remove" on one card silently blanking the other would be the worse surprise.
    """
    field = slot_field(slot)
    previous = get_branding(fresh=True).get(field) or ""
    _write({field: ""}, actor)
    if previous:
        _delete_file(previous)
    return get_branding(fresh=True)


def _delete_file(logo_id: str) -> None:
    """Remove a superseded logo.

    ⚠ NEVER RAISES. The document is already correct by the time this runs, so a
    file that will not delete is litter, not a fault — and turning it into one
    would fail an upload that has already succeeded.
    """
    if not _ID_RE.match(logo_id or ""):
        return
    try:
        os.remove(logo_path(logo_id))
    except OSError:
        pass


def normalise_logo(contents: bytes) -> bytes:
    """An uploaded image → the PNG that gets stored. Raises ValueError on junk.

    ⚠ RGBA, NOT RGB — the same rule as `POST /brand/logo` in `main.py`, and for
    the same reason: `.convert("RGB")` fills a transparent background with black,
    so a designer's transparent PNG would appear in the sidebar inside a hard
    black rectangle. This mark sits on `--panel` in the rail and on `--bg` on the
    landing page; it MUST keep its alpha.

    ⚠ AND IT IS DOWNSCALED, NOT REJECTED, when it is bigger than `LOGO_MAX_PX`.
    The person uploading has a print master and no way to resize it; refusing it
    would stop them branding the app at all. `thumbnail` keeps the aspect ratio,
    so a wide wordmark stays wide.
    """
    import io as _io

    from PIL import Image as PILImage

    try:
        img = PILImage.open(_io.BytesIO(contents))
        img.load()
        img = img.convert("RGBA")
    except Exception as e:  # noqa: BLE001 — bad/corrupt upload
        raise ValueError(f"Couldn't read that image: {e}")

    if max(img.size) > LOGO_MAX_PX:
        img.thumbnail((LOGO_MAX_PX, LOGO_MAX_PX), PILImage.LANCZOS)

    out = _io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def public_payload(row: dict | None = None) -> dict:
    """What a browser is told.

    ⚠ THE ONE SHAPE. The public route, the admin route and the tests all build
    the client's answer through here, so there is no second definition of what
    `logo_url` looks like.

    ⚠ **THE FALLBACK IS RESOLVED HERE, NOT IN THE BROWSER.** Both URLs come down
    already pointing at a real file, so a deployment with one upload sends the
    same address twice and the client needs no rule of its own. Putting
    "borrow the other slot" in the JSX would mean writing it again in the favicon
    code, and a rule written twice is a rule that disagrees with itself.
    """
    row = row or get_branding()
    dark = resolve_slot(row, SLOT_DARK)
    light = resolve_slot(row, SLOT_LIGHT)
    return {
        "name": row.get("name") or DEFAULT_NAME,
        # Relative, not absolute: the API's own address is the client's
        # `VITE_API_BASE`, and it is the client that knows it.
        "logo_url": f"/public/branding/logo/{dark}" if dark else "",
        "logo_url_light": f"/public/branding/logo/{light}" if light else "",
        # Change whenever the marks do — what an already-open tab compares.
        "stamp": dark,
        "stamp_light": light,
    }


# ===========================================================================
# Routes — PUBLIC. See the module docstring for why.
# ===========================================================================
@router.get("/public/branding")
def public_branding() -> dict:
    """The app's name and the address of its mark, for anybody at all.

    Called once per page load, before the sign-in card is drawn, and remembered
    in the browser between loads so a reload never flashes the built-in name.
    """
    return public_payload()


@router.get("/public/branding/logo/{stamp}")
def public_branding_logo(stamp: str):
    """One uploaded logo, by file id. 404 when there is none — the app draws its
    own mark instead.

    ⚠ ONE ROUTE FOR BOTH SLOTS, because the address is the FILE's id and a file
    does not care which theme asked for it. A `/logo/dark` and a `/logo/light`
    would have been two addresses whose contents change under them, which is the
    caching problem this whole scheme exists to avoid.

    ⚠ CACHED FOREVER ONLY WHEN THE STAMP IS A CURRENT ONE. A stale stamp still
    serves a live file (so a tab holding an old address is never shown a broken
    image) but tells the browser not to keep it — pinning today's bytes to
    yesterday's URL is how a logo change comes back after a hard refresh.
    """
    if not _ID_RE.match(stamp or ""):
        raise HTTPException(status_code=404, detail="No logo.")
    row = get_branding()
    live = [row.get(slot_field(s)) or "" for s in SLOTS]
    live = [i for i in live if i]
    if not live:
        raise HTTPException(status_code=404, detail="No logo.")
    # The asked-for file when it is still one of ours; otherwise whichever is
    # left, so an open tab gets a mark rather than a gap.
    serve = stamp if stamp in live else live[0]
    path = logo_path(serve)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No logo.")
    cache = "public, max-age=31536000, immutable" if serve == stamp else "no-store"
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": cache})
