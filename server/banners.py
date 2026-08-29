"""banners.py — the billboards on Explore, owned by the admin panel.

THE PROBLEM THIS SOLVES, in one sentence: Explore's two banners were BUILT FROM
THE WORKFLOW LIST — headline, body, button and a faded glyph for artwork — so the
one part of the app whose whole job is to say something to a customer could only
be changed by a developer, and could not carry a picture at all. Asked for
directly: *"this banner should be change aur hide by the admin — of it text and
image."*

⚠ **A LIST, AND EVERY ROW KNOWS WHICH SLOT IT IS FOR.** `slot` is `hero` (the
rotating billboard on the left) or `side` (the fixed one on the right). Two
slots, one collection, because they are the same object in two places on one
page — and an admin thinks about them together, which is the opposite of the
sale/coupon split in `offers.py`.

⚠ **AN EMPTY STORE IS NOT AN EMPTY PAGE.** With no active banner in a slot, the
client falls back to exactly what it drew before this module existed: slides
generated from the workflows the account may see. That is why the shipped state
is "no banners" rather than "six rows nobody asked for" — the panel adds voice,
it does not become a prerequisite for the page rendering.

⚠ **THE READ IS PUBLIC, LIKE `/public/branding` AND `/public/workflows`.** What
you advertise is public by nature, and making it public means the client needs
no token juggling for a decorative row. Nothing else leaks — the payload is
words, an address and a rank.

⚠ **THE IMAGE IS ADDRESSED BY ITS OWN ID, WHICH IS THE CACHE.** Same trick
`branding.py` uses and for the same reason: a NEW upload is a NEW URL, so every
browser, proxy and already-open tab picks it up without anybody reasoning about
cache headers. Overwriting one path is exactly how a picture change fails to
appear for the one person who most needed to see it.

⚠ **WEBP, NOT PNG.** A banner is 1280px of photograph; the logo is 512px of
mark. The same PNG rule there would put a megabyte on a page that already draws
a wall of thumbnails — and WEBP keeps the alpha channel, so a cut-out still
works.

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

router = APIRouter(tags=["banners"])

_lock = threading.Lock()

SLOT_HERO = "hero"
SLOT_SIDE = "side"
SLOTS = (SLOT_HERO, SLOT_SIDE)

# ⚠ CAPPED HERE RATHER THAN TRUSTED FROM THE PANEL, because the panel is not the
# only thing that can PATCH a row. These are measured against the card the
# client draws: the title clamps to two lines and the body to two (see
# `.xp-banner-title` / `.xp-banner-sub`), so anything past this is typed, stored
# and then silently cut off — worse than being refused.
KICKER_MAX = 40
TITLE_MAX = 60
BODY_MAX = 200
CTA_MAX = 30

# How many banners a slot may show. ⚠ FOUR IS THE DOT COUNT, not a storage
# limit: past four the dots under the carousel stop reading as "there is more"
# and start reading as a progress bar for something nobody asked to sit through.
# A fifth ACTIVE row is stored and simply not served.
MAX_PER_SLOT = 4

# The picture, once normalised. Wide enough to fill the billboard on a large
# screen and no wider — every visitor downloads it on the page they land on.
IMAGE_MAX_PX = 1280
ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

# What the panel may write. Anything else in a body is ignored rather than
# rejected, the same way `features.save_feature` treats its own EDITABLE set.
# ⚠ `image_id` IS NOT IN HERE — a picture is changed by uploading one, never by
# PATCHing an id, or a row could be pointed at a file it does not own.
EDITABLE = frozenset({
    "slot", "kicker", "title", "body", "cta_label", "cta_target", "rank", "active",
})

# ⚠ THE ID GOES INTO A FILE PATH AND INTO A URL, so it is checked against this
# rather than trusted. `save_image` generates it and nothing else may, but the
# public route takes one straight off the wire.
_ID_RE = re.compile(r"^[a-f0-9]{12}$")

# Where a button goes. Either a workflow id the shell knows (`script-to-
# storyboard`), or an http(s) address. ⚠ NOTHING ELSE — a `javascript:` target
# typed into an admin field is a stored XSS with a nice form around it.
_TARGET_RE = re.compile(r"^(?:[a-z0-9][a-z0-9-]{0,63}|https?://\S{1,300})$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, limit: int) -> str:
    """One typed line, made safe to print.

    ⚠ WHITESPACE IS COLLAPSED, NOT JUST TRIMMED — the same rule
    `branding.clean_name` keeps. These land in elements that clamp their lines,
    and a pasted newline in the middle of a title is an invisible character that
    silently changes where the clamp falls.
    """
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# ===========================================================================
# Storage — the same shape as offers.py
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_BANNERS_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local banner store at %s is unreadable — ignoring it.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


_collection = None


def get_collection():
    """The banners collection, connecting on first use."""
    global _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        from .mongo import get_db

        col = get_db()[config.BANNERS_COLLECTION]
        try:
            col.create_index("id", unique=True)
        except Exception as e:  # noqa: BLE001 — an index is an optimisation
            logger.warning("Could not index banners (%s).", e)
        _collection = col
        logger.info(
            "MongoDB banner store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.BANNERS_COLLECTION,
        )
        return _collection


# ===========================================================================
# The cache
# ===========================================================================
# ⚠ ONLY EVER REPLACED BY A SUCCESSFUL READ, so a database that goes away leaves
# the last known-good billboards on screen instead of dropping every visitor
# back to the generated ones mid-session. Same rule as `branding._cache`.
_cache: list | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    """Drop the cache so the next read goes to the store. Called after a write."""
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def all_banners(fresh: bool = False) -> list[dict]:
    """Every banner, newest first within its rank. NEVER RAISES.

    An unreadable store answers with an empty list, which the client reads as
    "no banners" and falls back to its generated slides — the app exactly as it
    was before this module existed.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            if _cache is not None and now - _cache_at < config.BANNERS_CACHE_TTL_S:
                return _cache

    rows: list[dict] = []
    try:
        if _use_local():
            with _lock:
                rows = list(_local_load().values())
        else:
            rows = [dict(r) for r in get_collection().find({}, {"_id": 0})]
    except Exception as e:  # noqa: BLE001 — a dead store must not blank a page
        logger.warning("Could not read banners (%s) — falling back to none.", e)
        return _cache or []

    # Rank first (what an administrator dragged), then newest — so two rows an
    # admin never ordered still come out in a stable, explicable sequence.
    rows.sort(key=lambda r: (int(r.get("rank") or 0), r.get("created_at") or ""))
    with _cache_lock:
        _cache = rows
        _cache_at = now
    return rows


def get_banner(banner_id: str) -> dict | None:
    return next((b for b in all_banners(fresh=True) if b.get("id") == banner_id), None)


def _clean(fields: dict) -> dict:
    out: dict = {}
    for key, value in (fields or {}).items():
        if key not in EDITABLE:
            continue
        if key == "slot":
            if value not in SLOTS:
                raise ValueError(f"Unknown slot: {value!r}")
            out[key] = value
        elif key == "kicker":
            out[key] = _text(value, KICKER_MAX)
        elif key == "title":
            out[key] = _text(value, TITLE_MAX)
        elif key == "body":
            out[key] = _text(value, BODY_MAX)
        elif key == "cta_label":
            out[key] = _text(value, CTA_MAX)
        elif key == "cta_target":
            target = _text(value, 320)
            # ⚠ EMPTY IS ALLOWED AND MEANS "no button". A banner that only says
            # something is a legitimate banner; a button pointing nowhere is not.
            if target and not _TARGET_RE.match(target):
                raise ValueError(
                    "A button target must be a workflow id or an http(s) address."
                )
            out[key] = target
        elif key == "rank":
            try:
                out[key] = max(0, min(999, int(value)))
            except (TypeError, ValueError):
                raise ValueError("Order has to be a whole number.")
        elif key == "active":
            out[key] = bool(value)
    return out


def create_banner(fields: dict, actor: str = "") -> dict:
    clean = _clean(fields)
    if not clean.get("title"):
        raise ValueError("A banner needs a heading.")
    row = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "created_by": (actor or "").strip().lower() or None,
        "image_id": "",
        **clean,
    }
    row.setdefault("slot", SLOT_HERO)
    row.setdefault("rank", 0)
    # ⚠ LIVE BY DEFAULT. Somebody who has just typed a headline and pressed
    # Create meant it to be seen; hiding it is one click, and a banner that
    # silently did nothing would be reported as a broken form.
    row.setdefault("active", True)

    if _use_local():
        with _lock:
            data = _local_load()
            data[row["id"]] = row
            _local_save(data)
    else:
        get_collection().insert_one(dict(row))
    _bump()
    return row


def save_banner(banner_id: str, fields: dict, actor: str = "") -> dict:
    clean = _clean(fields)
    clean["updated_at"] = _now_iso()
    clean["updated_by"] = (actor or "").strip().lower() or None

    if _use_local():
        with _lock:
            data = _local_load()
            if banner_id not in data:
                raise KeyError(banner_id)
            data[banner_id].update(clean)
            _local_save(data)
    else:
        if get_collection().update_one(
            {"id": banner_id}, {"$set": clean}
        ).matched_count == 0:
            raise KeyError(banner_id)
    _bump()
    return get_banner(banner_id) or {}


def delete_banner(banner_id: str) -> None:
    """Remove a banner and the picture that belonged to it.

    ⚠ THE ROW GOES FIRST. A file deleted before the document that points at it
    is a live banner with a broken image in it; a document deleted before its
    file is one orphaned picture on disk, which nobody ever sees.
    """
    row = get_banner(banner_id)
    if not row:
        raise KeyError(banner_id)
    if _use_local():
        with _lock:
            data = _local_load()
            data.pop(banner_id, None)
            _local_save(data)
    else:
        get_collection().delete_one({"id": banner_id})
    _bump()
    _delete_file(row.get("image_id") or "")


# ===========================================================================
# The picture
# ===========================================================================
def image_path(image_id: str) -> str:
    return os.path.join(config.BANNERS_DIR, f"{image_id}.webp")


def save_image(banner_id: str, webp_bytes: bytes, actor: str = "") -> dict:
    """Store already-normalised WEBP bytes as this banner's picture.

    ⚠ A NEW ID EVERY TIME, and the old file is deleted AFTER the row points at
    the new one — never before. Between those two writes the OLD picture is
    still the live one and is still on disk, so a crash mid-upload leaves the
    banner wearing its previous image rather than a broken one.
    """
    row = get_banner(banner_id)
    if not row:
        raise KeyError(banner_id)
    previous = row.get("image_id") or ""
    image_id = uuid.uuid4().hex[:12]
    os.makedirs(config.BANNERS_DIR, exist_ok=True)
    with open(image_path(image_id), "wb") as fh:
        fh.write(webp_bytes)

    # ⚠ NOT THROUGH `save_banner`: `image_id` is deliberately outside `EDITABLE`
    # so nothing on the wire can point a row at a file. This is the one writer.
    _write_image_id(banner_id, image_id, actor)
    if previous and previous != image_id:
        _delete_file(previous)
    return get_banner(banner_id) or {}


def clear_image(banner_id: str, actor: str = "") -> dict:
    """Back to no picture — the client draws the workflow glyph instead."""
    row = get_banner(banner_id)
    if not row:
        raise KeyError(banner_id)
    previous = row.get("image_id") or ""
    _write_image_id(banner_id, "", actor)
    if previous:
        _delete_file(previous)
    return get_banner(banner_id) or {}


def _write_image_id(banner_id: str, image_id: str, actor: str) -> None:
    patch = {
        "image_id": image_id,
        "updated_at": _now_iso(),
        "updated_by": (actor or "").strip().lower() or None,
    }
    if _use_local():
        with _lock:
            data = _local_load()
            if banner_id not in data:
                raise KeyError(banner_id)
            data[banner_id].update(patch)
            _local_save(data)
    else:
        if get_collection().update_one(
            {"id": banner_id}, {"$set": patch}
        ).matched_count == 0:
            raise KeyError(banner_id)
    _bump()


def _delete_file(image_id: str) -> None:
    """Remove a superseded picture.

    ⚠ NEVER RAISES. The row is already correct by the time this runs, so a file
    that will not delete is litter, not a fault — and turning it into one would
    fail an upload that has already succeeded.
    """
    if not _ID_RE.match(image_id or ""):
        return
    try:
        os.remove(image_path(image_id))
    except OSError:
        pass


def normalise_image(contents: bytes) -> bytes:
    """An uploaded picture → the WEBP that gets stored. Raises ValueError on junk.

    ⚠ RGBA, NOT RGB, for the same reason the logo keeps its alpha: a cut-out
    dropped onto a banner would otherwise arrive inside a hard black rectangle,
    and the banner's own background is a theme colour that changes.

    ⚠ AND IT IS DOWNSCALED, NOT REJECTED, when it is bigger than
    `IMAGE_MAX_PX`. The person uploading has whatever their designer sent them
    and no way to resize it; refusing it would stop them putting a picture on the
    page at all. `thumbnail` keeps the aspect ratio.
    """
    import io as _io

    from PIL import Image as PILImage

    try:
        img = PILImage.open(_io.BytesIO(contents))
        img.load()
        img = img.convert("RGBA")
    except Exception as e:  # noqa: BLE001 — bad/corrupt upload
        raise ValueError(f"Couldn't read that image: {e}")

    if max(img.size) > IMAGE_MAX_PX:
        img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX), PILImage.LANCZOS)

    out = _io.BytesIO()
    img.save(out, "WEBP", quality=82, method=4)
    return out.getvalue()


# ===========================================================================
# What a browser is told
# ===========================================================================
def public_banner(row: dict) -> dict:
    """One banner as Explore may see it.

    ⚠ AN ALLOW-LIST, NOT A DELETE-LIST — the same rule `offers.public_offer`
    keeps. The stored row carries `created_by` and `updated_by`; a route that
    spread the row and popped two keys would leak the third one somebody adds
    later.
    """
    image_id = row.get("image_id") or ""
    return {
        "id": row.get("id") or "",
        "slot": row.get("slot") or SLOT_HERO,
        "kicker": row.get("kicker") or "",
        "title": row.get("title") or "",
        "body": row.get("body") or "",
        "cta_label": row.get("cta_label") or "",
        "cta_target": row.get("cta_target") or "",
        # Relative, not absolute: the API's own address is the client's
        # `VITE_API_BASE`, and it is the client that knows it.
        "image_url": f"/public/banners/image/{image_id}" if image_id else "",
    }


def public_payload() -> dict:
    """The live banners, by slot, capped at what the carousel can show."""
    live = [b for b in all_banners() if b.get("active", True)]
    return {
        slot: [
            public_banner(b) for b in live if (b.get("slot") or SLOT_HERO) == slot
        ][:MAX_PER_SLOT]
        for slot in SLOTS
    }


# ===========================================================================
# Routes — PUBLIC. See the module docstring for why.
# ===========================================================================
@router.get("/public/banners")
def public_banners() -> dict:
    """Explore's billboards. Empty lists are normal and mean "use the built-in"."""
    return public_payload()


@router.get("/public/banners/image/{stamp}")
def public_banner_image(stamp: str):
    """One banner's picture. 404 when there isn't one — the card draws its glyph.

    ⚠ CACHED FOREVER, because the address IS the file: a new upload is a new id
    and therefore a new URL, so these bytes can never change under this path.
    That is the whole point of the stamp — see the module docstring.
    """
    if not _ID_RE.match(stamp or ""):
        raise HTTPException(status_code=404, detail="No image.")
    # ⚠ THE ID MUST BELONG TO A BANNER WE HOLD, not merely look like one. Serving
    # any well-formed name out of the directory would turn this into a read of
    # whatever else ever landed there.
    known = {b.get("image_id") for b in all_banners()}
    if stamp not in known:
        raise HTTPException(status_code=404, detail="No image.")
    path = image_path(stamp)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No image.")
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
