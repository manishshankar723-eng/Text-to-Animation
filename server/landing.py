"""landing.py — the pictures in the LANDING PAGE HERO, one per workflow.

THE PROBLEM THIS SOLVES, in one sentence: the four tiles in the hero were
HAND-DRAWN SVG inside `Landing.jsx` — a screenplay page, four board panels, a
pose strip and a timeline — so the biggest picture on the page a stranger lands
on could only be changed by a developer, and could not be a real frame from the
product at all. Asked for directly: *"mai chahta hun ki ye four icon ke jagh mai
image lagun so admin panel mai landing page ka fuction bano so mai image dall
sakun har workflow ka."*

⚠ **ONE ROW PER WORKFLOW, AND THE WORKFLOW ID IS THE KEY.** There is no `create`
here and no `delete` — unlike `banners.py`, where an administrator invents the
rows. The rows ARE the workflow catalogue in `features.py`, so a picture is
*attached to* a workflow rather than being an object of its own. That is the
whole reason a seventh workflow needs no code: it turns up in the panel the
moment it is in the catalogue, and its picture lands on its own id.

⚠ **HIDDEN MEANS NOT SERVED, AND THAT IS DECIDED HERE, NOT IN THE BROWSER.**
`public_payload()` asks `features.public_workflows()` which ids a stranger may be
shown and drops every other picture on the floor. Asked for in the same breath:
*"jo live hai uska dikhe image yaha pe aur jo hide hai uska nhi dikhe magar mai
jab hide se unhide karun to yeha pe image aa jana chaiye."* The client filters
too — it only draws tiles for the workflows it was told about — but a filter that
lives only in the client is a picture of a switched-off workflow sitting in a
public JSON payload, which is the same leak the landing page's hand-written
workflow list used to be.

⚠ **AND THE PICTURE IS KEPT WHEN A WORKFLOW IS HIDDEN.** Hiding is reversible in
one click in the Features tab, so the row survives and the tile comes straight
back when it is un-hidden. Deleting the picture is a separate, deliberate button.

⚠ **THE IMAGE IS ADDRESSED BY ITS OWN ID, WHICH IS THE CACHE.** Same trick
`branding.py` and `banners.py` use, for the same reason: a NEW upload is a NEW
URL, so every browser, proxy and already-open tab picks it up without anybody
reasoning about cache headers.

⚠ **WEBP AT 900px, NOT 1280.** A banner is a full-width billboard; a hero tile is
one cell of a 2x2 barely 200px wide on a laptop. Four of these download on the
page every prospect lands on, so this is the one place where the smaller number
is the product decision — see `IMAGE_MAX_PX`.

⚠ **THE READ IS PUBLIC**, like `/public/branding`, `/public/workflows` and
`/public/banners`. The page it feeds is reached before a token exists.

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

from . import config, features

logger = logging.getLogger(__name__)

router = APIRouter(tags=["landing"])

_lock = threading.Lock()

# The picture, once normalised. ⚠ SMALL ON PURPOSE — see the module docstring.
IMAGE_MAX_PX = 900
ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

# How many tiles the hero draws. ⚠ THIS IS A LAYOUT NUMBER, NOT A STORAGE ONE:
# the tiles are a 2x2 grid (see `.lp-art-steps` in landing.css), and a fifth one
# either makes a third row taller than the hero or leaves a hole in it. Every
# workflow may still HOLD a picture; the client draws the first four it is told
# about, and WHICH four is the `order` an administrator sets in the Features tab.
HERO_TILES = 4

# ⚠ THE ID GOES INTO A FILE PATH AND INTO A URL, so it is checked against this
# rather than trusted. `save_image` generates it and nothing else may, but the
# public route takes one straight off the wire.
_ID_RE = re.compile(r"^[a-f0-9]{12}$")

# A workflow key, as `features.py` writes them. ⚠ CHECKED BEFORE THE CATALOGUE
# LOOKUP, not instead of it: the catalogue answer is the real gate, this is what
# stops a path-shaped id ever reaching a file name.
_WORKFLOW_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# What counts as a workflow
# ===========================================================================
def known_workflows() -> list[dict]:
    """Every workflow in the catalogue, in the administrator's own order.

    ⚠ READ FROM `features.py`, NEVER LISTED HERE. A second copy of the workflow
    list is exactly the drift that made the landing page advertise a switched-off
    workflow in the first place — and this module exists to end a hand-written
    list, not to add one.
    """
    feats = features.all_features()
    rows = [f for f in feats.values() if f.get("group") == features.GROUP_WORKFLOW]
    rows.sort(key=lambda f: (int(f.get("order") or 0), f.get("label") or ""))
    return [
        {
            "id": (f.get("key") or "").split(".", 1)[-1],
            "label": f.get("label") or "",
            "icon": f.get("icon") or "•",
            "status": f.get("status") or features.STATUS_LIVE,
        }
        for f in rows
    ]


def is_workflow(workflow_id: str) -> bool:
    if not _WORKFLOW_RE.match(workflow_id or ""):
        return False
    return f"workflow.{workflow_id}" in features.all_features()


# ===========================================================================
# Storage — the same shape as banners.py
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_LANDING_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local landing store at %s is unreadable — ignoring it.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


_collection = None


def get_collection():
    """The landing-art collection, connecting on first use."""
    global _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        from .mongo import get_db

        col = get_db()[config.LANDING_COLLECTION]
        try:
            col.create_index("id", unique=True)
        except Exception as e:  # noqa: BLE001 — an index is an optimisation
            logger.warning("Could not index landing art (%s).", e)
        _collection = col
        logger.info(
            "MongoDB landing-art store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.LANDING_COLLECTION,
        )
        return _collection


# ===========================================================================
# The cache
# ===========================================================================
# ⚠ ONLY EVER REPLACED BY A SUCCESSFUL READ, so a database that goes away leaves
# the last known-good pictures in the hero instead of dropping every visitor back
# to the drawn tiles mid-session. Same rule as `banners._cache`.
_cache: dict | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    """Drop the cache so the next read goes to the store. Called after a write."""
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def all_art(fresh: bool = False) -> dict:
    """Every stored picture, keyed by workflow id. NEVER RAISES.

    An unreadable store answers with an empty map, which the client reads as "no
    pictures" and falls back to the drawn tiles — the hero exactly as it was
    before this module existed.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            if _cache is not None and now - _cache_at < config.LANDING_CACHE_TTL_S:
                return _cache

    rows: dict = {}
    try:
        if _use_local():
            with _lock:
                rows = {
                    str(k): dict(v)
                    for k, v in _local_load().items()
                    if isinstance(v, dict)
                }
        else:
            rows = {
                str(r.get("id")): dict(r)
                for r in get_collection().find({}, {"_id": 0})
                if r.get("id")
            }
    except Exception as e:  # noqa: BLE001 — a dead store must not blank a hero
        logger.warning("Could not read landing art (%s) — falling back to none.", e)
        return _cache or {}

    with _cache_lock:
        _cache = rows
        _cache_at = now
    return rows


def get_art(workflow_id: str) -> dict | None:
    return all_art(fresh=True).get(workflow_id)


# ===========================================================================
# The picture
# ===========================================================================
def image_path(image_id: str) -> str:
    return os.path.join(config.LANDING_DIR, f"{image_id}.webp")


def save_image(workflow_id: str, webp_bytes: bytes, actor: str = "") -> dict:
    """Store already-normalised WEBP bytes as this workflow's hero tile.

    ⚠ IT UPSERTS. There is no "create the row first" step the way there is for a
    banner: the row's identity is the workflow, so the first upload IS the row.
    An administrator should not have to make an empty object before they can
    attach a picture to a workflow that already exists.

    ⚠ A NEW ID EVERY TIME, and the old file is deleted AFTER the row points at
    the new one — never before. Between those two writes the OLD picture is still
    the live one and is still on disk, so a crash mid-upload leaves the tile
    wearing its previous image rather than a broken one.
    """
    if not is_workflow(workflow_id):
        raise KeyError(workflow_id)
    previous = (get_art(workflow_id) or {}).get("image_id") or ""
    image_id = uuid.uuid4().hex[:12]
    os.makedirs(config.LANDING_DIR, exist_ok=True)
    with open(image_path(image_id), "wb") as fh:
        fh.write(webp_bytes)

    _write_image_id(workflow_id, image_id, actor)
    if previous and previous != image_id:
        _delete_file(previous)
    return get_art(workflow_id) or {}


def clear_image(workflow_id: str, actor: str = "") -> dict:
    """Back to no picture — the hero draws this workflow's built-in tile again."""
    if not is_workflow(workflow_id):
        raise KeyError(workflow_id)
    previous = (get_art(workflow_id) or {}).get("image_id") or ""
    _write_image_id(workflow_id, "", actor)
    if previous:
        _delete_file(previous)
    return get_art(workflow_id) or {}


def _write_image_id(workflow_id: str, image_id: str, actor: str) -> None:
    """The ONE writer.

    ⚠ Nothing on the wire may set `image_id` — there is no PATCH route on this
    store at all, precisely so a row can never be pointed at a file it does not
    own. Same rule as `banners._write_image_id`, one step stricter.
    """
    patch = {
        "id": workflow_id,
        "image_id": image_id,
        "updated_at": _now_iso(),
        "updated_by": (actor or "").strip().lower() or None,
    }
    if _use_local():
        with _lock:
            data = _local_load()
            row = data.get(workflow_id)
            row = dict(row) if isinstance(row, dict) else {"created_at": _now_iso()}
            row.update(patch)
            data[workflow_id] = row
            _local_save(data)
    else:
        get_collection().update_one(
            {"id": workflow_id},
            {"$set": patch, "$setOnInsert": {"created_at": _now_iso()}},
            upsert=True,
        )
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

    ⚠ RGBA, NOT RGB, for the same reason the logo and the banners keep their
    alpha: the tile's own background is `--lp-sheet`, a paper colour, and a
    cut-out dropped on it would otherwise arrive inside a hard black rectangle.

    ⚠ AND IT IS DOWNSCALED, NOT REJECTED, when it is bigger than `IMAGE_MAX_PX`.
    The person uploading has whatever their designer sent them and no way to
    resize it; refusing it would stop them putting a picture on the page at all.
    `thumbnail` keeps the aspect ratio — the tile crops with `object-fit: cover`,
    so a picture that is not 4:3 is centred rather than squashed.
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
def image_url(row: dict) -> str:
    """Relative, not absolute: the API's own address is the client's
    `VITE_API_BASE`, and it is the client that knows it."""
    image_id = (row or {}).get("image_id") or ""
    return f"/public/landing/image/{image_id}" if image_id else ""


def public_payload() -> dict:
    """The hero pictures a STRANGER may be shown, keyed by workflow id.

    ⚠ THE VISIBILITY FILTER IS THE POINT OF THIS FUNCTION. `public_workflows()`
    has already applied the kill switch and the rollout rules for an account that
    does not exist, so a workflow staged as "hidden" simply is not in the answer
    and neither is its picture. Un-hiding it puts both back within one page load.

    ⚠ "SOON" IS STILL SHOWN. The landing page draws a soon workflow with a badge
    on it — it is a roadmap teaser, not a secret — so its tile keeps its picture.
    Only "hidden" is a picture nobody outside gets to see.
    """
    try:
        visible = {w["id"] for w in features.public_workflows()["workflows"]}
    except Exception as e:  # noqa: BLE001 — fail OPEN, like everything else here
        logger.warning("Could not resolve public workflows (%s) — no hero art.", e)
        return {"art": {}}

    return {
        "art": {
            wid: image_url(row)
            for wid, row in all_art().items()
            if wid in visible and row.get("image_id")
        }
    }


# ===========================================================================
# Routes — PUBLIC. See the module docstring for why.
# ===========================================================================
@router.get("/public/landing/art")
def public_landing_art() -> dict:
    """The hero tiles' pictures. An empty map is normal and means "draw them"."""
    return public_payload()


@router.get("/public/landing/image/{stamp}")
def public_landing_image(stamp: str):
    """One tile's picture. 404 when there isn't one — the hero draws its own.

    ⚠ CACHED FOREVER, because the address IS the file: a new upload is a new id
    and therefore a new URL, so these bytes can never change under this path.

    ⚠ AND IT IS NOT FILTERED BY VISIBILITY, deliberately. This route serves a
    stamp somebody was already handed by `/public/landing/art`, which IS
    filtered; re-checking here would mean an already-cached tile 404-ing for one
    visitor and not another the moment a workflow is staged, and an unguessable
    12-hex name is not a way to enumerate anything.
    """
    if not _ID_RE.match(stamp or ""):
        raise HTTPException(status_code=404, detail="No image.")
    # ⚠ THE ID MUST BELONG TO A ROW WE HOLD, not merely look like one. Serving
    # any well-formed name out of the directory would turn this into a read of
    # whatever else ever landed there.
    known = {row.get("image_id") for row in all_art().values()}
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
