"""showcase.py — the picture-and-video wall on the PUBLIC Explore page.

THE PROBLEM THIS SOLVES, in one sentence: Explore became the marketing page a
STRANGER lands on, and its wall was the signed-in account's own projects — which
is an empty grid to somebody who has not signed up yet, on the one screen whose
entire job is to make them want to.

⚠ **THIS IS CURATED WORK, NOT A COMMUNITY FEED.** The old Explore carried a long
note explaining why its wall was the account's own library: this app has no
public gallery and nothing is shared by default, so publishing customers'
storyboards to sell the product would be publishing customers' storyboards. That
reasoning has not changed one word. What changed is WHOSE work is on the wall:
an administrator uploads it deliberately, item by item, in the admin panel. No
customer's project is ever read by this module.

⚠ **AN ITEM IS AN IMAGE OR A VIDEO, AND THE VIDEO IS THE POINT.** Asked for
directly: *"the videos or images should be clickable and be able to use it
properly play"*. So `kind` is `image` or `video`, a card is a button, and
clicking one opens the viewer — a full-size picture, or a real `<video>` with
controls. A wall of thumbnails that only navigates somewhere else is what this
replaced.

⚠ **A VIDEO CARRIES A POSTER, AND IT IS PULLED OUT OF THE CLIP.** This used to
read *"the poster is a separate upload"*, on the grounds that an `imageio-ffmpeg`
install has no `ffprobe`. That is true and it was the wrong conclusion: **ffprobe
is not what extracts a frame - ffmpeg is**, and `imageio-ffmpeg` ships ffmpeg
itself, which `animatic.ffmpeg_exe()` has been locating for the exporter all
along. So `poster_from_video()` takes frame one on the way in and the wall has a
thumbnail without anybody being asked for a second file.

Reported as a bug, and it read as one: *"when i upload video from admin panel but
when i see explore page so no thumbnail show in my upload video."* The still was
never missing - it had simply never been asked for, and nothing on the wall said
so.

⚠ **THE OLD REASONING IS STILL RIGHT ABOUT ONE THING: A BLACK FRAME IS WORSE
THAN NO FRAME.** Films open on black, so second zero is the worst possible guess;
`POSTER_PROBE_SECONDS` tries a little way in first and walks backwards, and a
frame that comes back essentially black is REJECTED rather than shipped. If every
probe is black the item keeps no poster at all and the card draws the workflow
glyph, exactly as it did before - a wall of black rectangles is what this avoids.

⚠ **AND THE MANUAL UPLOAD DID NOT GO AWAY.** `save_poster()` and the panel's
"Replace still" are untouched: a grab is a good default, not a better choice than
the frame a person picked. The grab only ever fills an EMPTY poster slot.

⚠ **AND `aspect` IS MEASURED FOR BOTH NOW, FOR THE SAME REASON.** An image's
ratio is read by Pillow on upload. A video's used to be whatever the admin picked
in the dropdown, defaulting to 16:9 - so a portrait phone clip left on the default
was cropped hard into a landscape slot. The grabbed frame IS the video's real
shape, so it answers the question the dropdown was guessing at. The wall still
clamps whatever it gets between 4:5 and 16:9 (see `wallAspect` in Explore.jsx),
so a wrong ratio costs a crop, not a broken layout.

⚠ **THE READ IS PUBLIC, LIKE `/public/branding`, `/public/workflows` AND
`/public/banners`.** It has to be: the page it feeds is the page you reach
BEFORE you have a token. Nothing leaks — the payload is words, a ratio and two
addresses.

⚠ **THE FILE IS ADDRESSED BY ITS OWN ID, WHICH IS THE CACHE.** Same trick
`branding.py` and `banners.py` use, for the same reason: a NEW upload is a NEW
URL, so every browser, proxy and already-open tab picks it up without anybody
reasoning about cache headers.

⚠ **VIDEO IS STORED AS IT ARRIVED; ONLY IMAGES ARE RE-ENCODED.** Re-encoding a
clip needs ffmpeg on the request path, and a marketing upload that takes two
minutes and sometimes fails is worse than one that stores 40MB. The type
allow-list is what keeps the directory sane, and `SHOWCASE_MAX_VIDEO_BYTES` is
what keeps it small. ⚠ THE RANGE HEADER IS HONOURED (`FileResponse` does it),
which is what lets a browser seek in a clip instead of downloading all of it
before the first frame appears.

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

router = APIRouter(tags=["showcase"])

_lock = threading.Lock()

KIND_IMAGE = "image"
KIND_VIDEO = "video"
KINDS = (KIND_IMAGE, KIND_VIDEO)

# ⚠ CAPPED HERE RATHER THAN TRUSTED FROM THE PANEL, and measured against the
# card the client draws: the caption clamps to two lines under the picture. Same
# rule as `banners.py` — anything past this is typed, stored and then silently
# cut off, which is worse than being refused.
TITLE_MAX = 60
BLURB_MAX = 140

# How many items the public wall is served. ⚠ A CEILING ON THE PAYLOAD, not a
# storage limit: an admin may keep fifty and show twenty-four. Past this a
# marketing page stops being a marketing page and becomes a stock library.
MAX_PUBLIC = 24

# The picture, once normalised. ⚠ BIGGER THAN A BANNER'S 1280 because these open
# FULL SCREEN in the viewer — a banner is only ever seen at card size.
IMAGE_MAX_PX = 1600
ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
# ⚠ MP4 FIRST AND WEBM SECOND, AND NOTHING ELSE. QuickTime `.mov` is what a
# phone hands you and Safari plays it, but Chrome on Windows does not — a clip
# that plays for the admin who uploaded it and for nobody else is the worst
# possible failure on this particular screen.
ALLOWED_VIDEO_TYPES = ("video/mp4", "video/webm")

# What the panel may write. ⚠ `media_id`, `media_kind` and `poster_id` ARE NOT IN
# HERE — media is changed by uploading it, never by PATCHing an id, or a row
# could be pointed at a file it does not own.
EDITABLE = frozenset({"title", "blurb", "workflow", "aspect", "rank", "active"})

# ⚠ THE ID GOES INTO A FILE PATH AND INTO A URL, so it is checked against this
# rather than trusted. `save_media` generates it and nothing else may, but the
# public route takes one straight off the wire.
_ID_RE = re.compile(r"^[a-f0-9]{12}$")

# The workflow this piece of work came out of, used as the card's tag and as the
# viewer's "Use this workflow" button. Empty is allowed and means "no tag".
_WORKFLOW_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# What the wall may be told a video's shape is. ⚠ A CLOSED SET, not a free
# string: it lands in a CSS custom property, and the four here are every shape
# this studio actually renders.
# Where to look for a poster frame, in order, seconds into the clip.
# ⚠ NOT ZERO FIRST, AND THAT IS THE WHOLE LIST'S POINT. A film that opens on
# black - which is most of them - hands back a black rectangle at second zero,
# and a wall of those is precisely what the "ask for a still instead" decision
# was avoiding. A little way in is where the picture actually is; zero is kept
# only as the last resort for a clip too short to have anything else.
POSTER_PROBE_SECONDS = (1.0, 2.5, 0.5, 0.0)

# A grabbed frame this dark is treated as "the film had not started yet" and the
# next probe is tried. 0-255 mean over the greyscale; 6 is very nearly black and
# deliberately conservative, because a genuinely dim NIGHT shot is still a real
# frame of the film and rejecting it would leave the card emptier than it is.
POSTER_MIN_BRIGHTNESS = 6.0

# One probe's ffmpeg call. A seek-then-decode of a single frame is a fraction of
# a second even on a big file; anything near this means something is wrong with
# the clip, and the upload must not hang on it.
POSTER_TIMEOUT_SECONDS = 20

ASPECTS = ("16:9", "4:5", "1:1", "9:16")
DEFAULT_ASPECT = "16:9"

# Video is stored under its own extension so the browser is handed the type it
# was given. Keyed by the content type the upload declared.
_VIDEO_EXT = {"video/mp4": "mp4", "video/webm": "webm"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, limit: int) -> str:
    """One typed line, made safe to print.

    ⚠ WHITESPACE IS COLLAPSED, NOT JUST TRIMMED — same rule as `banners._text`.
    These land in elements that clamp their lines, and a pasted newline in the
    middle of a title is an invisible character that changes where the clamp
    falls.
    """
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# ===========================================================================
# Storage — the same shape as banners.py
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_SHOWCASE_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local showcase store at %s is unreadable — ignoring it.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


_collection = None


def get_collection():
    """The showcase collection, connecting on first use."""
    global _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        from .mongo import get_db

        col = get_db()[config.SHOWCASE_COLLECTION]
        try:
            col.create_index("id", unique=True)
        except Exception as e:  # noqa: BLE001 — an index is an optimisation
            logger.warning("Could not index showcase (%s).", e)
        _collection = col
        logger.info(
            "MongoDB showcase store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.SHOWCASE_COLLECTION,
        )
        return _collection


# ===========================================================================
# The cache
# ===========================================================================
# ⚠ ONLY EVER REPLACED BY A SUCCESSFUL READ, so a database that goes away leaves
# the last known-good wall on screen instead of blanking the sales page
# mid-session. Same rule as `banners._cache`.
_cache: list | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    """Drop the cache so the next read goes to the store. Called after a write."""
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def all_items(fresh: bool = False) -> list[dict]:
    """Every showcase item, in rank order. NEVER RAISES.

    An unreadable store answers with an empty list, which the page reads as "no
    wall" and simply does not draw one. A marketing page missing its gallery is
    a bad afternoon; a marketing page answering 500 is a lost customer.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            if _cache is not None and now - _cache_at < config.SHOWCASE_CACHE_TTL_S:
                return _cache

    rows: list[dict] = []
    try:
        if _use_local():
            with _lock:
                rows = list(_local_load().values())
        else:
            rows = [dict(r) for r in get_collection().find({}, {"_id": 0})]
    except Exception as e:  # noqa: BLE001 — a dead store must not blank a page
        logger.warning("Could not read the showcase (%s) — falling back to none.", e)
        return _cache or []

    # Rank first (what an administrator dragged), then newest — so two rows an
    # admin never ordered still come out in a stable, explicable sequence.
    rows.sort(key=lambda r: (int(r.get("rank") or 0), r.get("created_at") or ""))
    with _cache_lock:
        _cache = rows
        _cache_at = now
    return rows


def get_item(item_id: str) -> dict | None:
    return next((i for i in all_items(fresh=True) if i.get("id") == item_id), None)


def _clean(fields: dict) -> dict:
    out: dict = {}
    for key, value in (fields or {}).items():
        if key not in EDITABLE:
            continue
        if key == "title":
            out[key] = _text(value, TITLE_MAX)
        elif key == "blurb":
            out[key] = _text(value, BLURB_MAX)
        elif key == "workflow":
            tag = _text(value, 64).lower()
            # ⚠ EMPTY IS ALLOWED AND MEANS "no tag". A clip that shows off the
            # whole pipeline does not belong to any one workflow.
            if tag and not _WORKFLOW_RE.match(tag):
                raise ValueError("That is not a workflow id.")
            out[key] = tag
        elif key == "aspect":
            shape = _text(value, 8) or DEFAULT_ASPECT
            if shape not in ASPECTS:
                raise ValueError(f"Shape has to be one of: {', '.join(ASPECTS)}.")
            out[key] = shape
        elif key == "rank":
            try:
                out[key] = max(0, min(999, int(value)))
            except (TypeError, ValueError):
                raise ValueError("Order has to be a whole number.")
        elif key == "active":
            out[key] = bool(value)
    return out


def create_item(fields: dict, actor: str = "") -> dict:
    clean = _clean(fields)
    if not clean.get("title"):
        raise ValueError("A showcase item needs a title.")
    row = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "created_by": (actor or "").strip().lower() or None,
        # ⚠ THE ROW EXISTS BEFORE THE FILE DOES, on purpose. The upload route
        # needs something to attach the file TO, and the same two-step is what
        # `banners.py` does. An item with no media is simply not served — see
        # `public_payload` — so a half-finished row is invisible, not broken.
        "media_id": "",
        "media_kind": "",
        "poster_id": "",
        **clean,
    }
    row.setdefault("workflow", "")
    row.setdefault("blurb", "")
    row.setdefault("aspect", DEFAULT_ASPECT)
    row.setdefault("rank", 0)
    # ⚠ LIVE BY DEFAULT, like a banner. Somebody who has just typed a title and
    # pressed Create meant it to be seen; hiding it is one click. It still will
    # not appear on the page until a file is on it.
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


def save_item(item_id: str, fields: dict, actor: str = "") -> dict:
    clean = _clean(fields)
    clean["updated_at"] = _now_iso()
    clean["updated_by"] = (actor or "").strip().lower() or None

    if _use_local():
        with _lock:
            data = _local_load()
            if item_id not in data:
                raise KeyError(item_id)
            data[item_id].update(clean)
            _local_save(data)
    else:
        if get_collection().update_one(
            {"id": item_id}, {"$set": clean}
        ).matched_count == 0:
            raise KeyError(item_id)
    _bump()
    return get_item(item_id) or {}


def delete_item(item_id: str) -> None:
    """Remove an item and both files that belonged to it.

    ⚠ THE ROW GOES FIRST — same rule as `banners.delete_banner`. A file deleted
    before the document that points at it is a live card with a broken video in
    it; a document deleted before its file is litter nobody ever sees.
    """
    row = get_item(item_id)
    if not row:
        raise KeyError(item_id)
    if _use_local():
        with _lock:
            data = _local_load()
            data.pop(item_id, None)
            _local_save(data)
    else:
        get_collection().delete_one({"id": item_id})
    _bump()
    _delete_file(row.get("media_id") or "", row.get("media_kind") or "")
    _delete_file(row.get("poster_id") or "", KIND_IMAGE)


# ===========================================================================
# The files
# ===========================================================================
def media_path(media_id: str, kind: str, content_type: str = "") -> str:
    """Where one stored file lives.

    Images are always WEBP because they are re-encoded on the way in. A video
    keeps the container it arrived in, so the extension is looked up from the
    type — and falls back to mp4, which is what all but a rounding error of
    uploads are.
    """
    if kind == KIND_VIDEO:
        ext = _VIDEO_EXT.get(content_type, "")
        if not ext:
            # Stored earlier, type not remembered: use whichever is on disk.
            for guess in ("mp4", "webm"):
                candidate = os.path.join(config.SHOWCASE_DIR, f"{media_id}.{guess}")
                if os.path.isfile(candidate):
                    return candidate
            ext = "mp4"
        return os.path.join(config.SHOWCASE_DIR, f"{media_id}.{ext}")
    return os.path.join(config.SHOWCASE_DIR, f"{media_id}.webp")


def save_media(item_id: str, blob: bytes, kind: str, content_type: str = "",
               actor: str = "") -> dict:
    """Store this item's picture or clip. Replaces whatever was there.

    ⚠ A NEW ID EVERY TIME, and the old file is deleted AFTER the row points at
    the new one — never before. Between those two writes the OLD file is still
    the live one and is still on disk, so a crash mid-upload leaves the card
    wearing its previous media rather than a broken one. Straight out of
    `banners.save_image`, and for exactly the same reason.

    ⚠ SWAPPING AN IMAGE FOR A VIDEO IS ALLOWED, and it rewrites `media_kind` —
    which is why the PREVIOUS file's own kind is what gets deleted, not the new
    one's. Deleting `<old-id>.mp4` when the old file was a `.webp` would leave
    the picture on disk for ever.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown media kind: {kind!r}")
    row = get_item(item_id)
    if not row:
        raise KeyError(item_id)
    prev_id = row.get("media_id") or ""
    prev_kind = row.get("media_kind") or ""
    media_id = uuid.uuid4().hex[:12]
    os.makedirs(config.SHOWCASE_DIR, exist_ok=True)
    with open(media_path(media_id, kind, content_type), "wb") as fh:
        fh.write(blob)

    # ⚠ NOT THROUGH `save_item`: these keys are deliberately outside `EDITABLE`
    # so nothing on the wire can point a row at a file. This is the one writer.
    patch = {"media_id": media_id, "media_kind": kind}
    # ⚠ A POSTER BELONGS TO A VIDEO. Replacing a clip with a picture leaves the
    # old poster pointing at nothing anyone will ever draw, so it goes with it.
    if kind == KIND_IMAGE and row.get("poster_id"):
        patch["poster_id"] = ""
        _delete_file(row.get("poster_id") or "", KIND_IMAGE)
    _write_files(item_id, patch, actor)
    if prev_id and prev_id != media_id:
        _delete_file(prev_id, prev_kind)
    return get_item(item_id) or {}


def save_poster(item_id: str, webp_bytes: bytes, actor: str = "") -> dict:
    """The still a video's card shows before anybody presses play."""
    row = get_item(item_id)
    if not row:
        raise KeyError(item_id)
    previous = row.get("poster_id") or ""
    poster_id = uuid.uuid4().hex[:12]
    os.makedirs(config.SHOWCASE_DIR, exist_ok=True)
    with open(media_path(poster_id, KIND_IMAGE), "wb") as fh:
        fh.write(webp_bytes)
    _write_files(item_id, {"poster_id": poster_id}, actor)
    if previous and previous != poster_id:
        _delete_file(previous, KIND_IMAGE)
    return get_item(item_id) or {}


def clear_poster(item_id: str, actor: str = "") -> dict:
    """Back to no still — the card draws the workflow glyph instead."""
    row = get_item(item_id)
    if not row:
        raise KeyError(item_id)
    previous = row.get("poster_id") or ""
    _write_files(item_id, {"poster_id": ""}, actor)
    if previous:
        _delete_file(previous, KIND_IMAGE)
    return get_item(item_id) or {}


def _write_files(item_id: str, patch: dict, actor: str) -> None:
    """The ONE writer of the file-pointer keys. See `save_media`."""
    patch = {
        **patch,
        "updated_at": _now_iso(),
        "updated_by": (actor or "").strip().lower() or None,
    }
    if _use_local():
        with _lock:
            data = _local_load()
            if item_id not in data:
                raise KeyError(item_id)
            data[item_id].update(patch)
            _local_save(data)
    else:
        if get_collection().update_one(
            {"id": item_id}, {"$set": patch}
        ).matched_count == 0:
            raise KeyError(item_id)
    _bump()


def _delete_file(media_id: str, kind: str) -> None:
    """Remove a superseded file.

    ⚠ NEVER RAISES. The row is already correct by the time this runs, so a file
    that will not delete is litter, not a fault — and turning it into one would
    fail an upload that has already succeeded.
    """
    if not _ID_RE.match(media_id or ""):
        return
    # A video's extension is not remembered on the row, so both are tried.
    if kind == KIND_VIDEO:
        paths = [
            os.path.join(config.SHOWCASE_DIR, f"{media_id}.{ext}")
            for ext in ("mp4", "webm")
        ]
    else:
        paths = [media_path(media_id, KIND_IMAGE)]
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def normalise_image(contents: bytes) -> tuple[bytes, str]:
    """An uploaded picture → the WEBP that gets stored, and the shape it is.

    ⚠ RETURNS THE ASPECT TOO, which is the one thing this does that
    `banners.normalise_image` does not. Pillow is already holding the image, so
    measuring it is free — and a wall laid out from a MEASURED ratio never has
    to crop a landscape still into a portrait slot because somebody left the
    dropdown alone. The answer is snapped to the closed `ASPECTS` set, because
    that set is what the client's CSS knows.

    ⚠ RGBA, NOT RGB, for the same reason the logo keeps its alpha: a cut-out
    would otherwise arrive inside a hard black rectangle.

    ⚠ AND IT IS DOWNSCALED, NOT REJECTED, above `IMAGE_MAX_PX` — the person
    uploading has whatever their designer sent them and no way to resize it.
    """
    import io as _io

    from PIL import Image as PILImage

    try:
        img = PILImage.open(_io.BytesIO(contents))
        img.load()
        img = img.convert("RGBA")
    except Exception as e:  # noqa: BLE001 — bad/corrupt upload
        raise ValueError(f"Couldn't read that image: {e}")

    aspect = nearest_aspect(img.size[0], img.size[1])
    if max(img.size) > IMAGE_MAX_PX:
        img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX), PILImage.LANCZOS)

    out = _io.BytesIO()
    img.save(out, "WEBP", quality=84, method=4)
    return out.getvalue(), aspect


def poster_from_video(contents: bytes, content_type: str = "") -> tuple[bytes, str] | None:
    """A clip -> the WEBP still its card shows, and the shape the clip really is.

    Returns `None` when no usable frame could be taken, which the caller must
    treat as "leave the poster empty" rather than as an error: an upload is not
    worth failing over a thumbnail, and the card has a glyph to fall back on.

    ⚠ IT IS ffmpeg, NOT ffprobe. The note this replaces said the module could
    not pull a frame because `imageio-ffmpeg` ships no `ffprobe` - true, and
    beside the point, since extracting a frame is ffmpeg's own job. The binary is
    found by `animatic.ffmpeg_exe()` (FFMPEG_BINARY -> PATH -> the bundled copy),
    imported lazily here for the same reason every other module does it: that
    import chain is heavy and this is not on the hot path.

    ⚠ `-ss` GOES BEFORE `-i`, WHICH IS THE DIFFERENCE BETWEEN INSTANT AND SLOW.
    In front of the input it is a seek to the nearest keyframe and the decoder
    starts there; after the input it decodes every frame from zero and throws
    them away. On a 96MB clip - the ceiling this route allows - that is the gap
    between a fast upload and one that looks hung.

    ⚠ THE FILE GOES TO DISK FIRST because an MP4's index (`moov`) can live at
    the END of the file, so a decoder that cannot seek may not be able to read it
    at all. Piping the bytes in would work for some uploads and mysteriously fail
    for others, which is worse than not working.
    """
    import io as _io
    import subprocess
    import tempfile

    from PIL import Image as PILImage
    from PIL import ImageStat as PILImageStat

    try:
        from animatic import ffmpeg_exe
    except Exception:  # noqa: BLE001 - no ffmpeg is not an upload failure
        logger.warning("showcase: no ffmpeg available, skipping poster grab")
        return None

    try:
        exe = ffmpeg_exe()
    except Exception:  # noqa: BLE001 - ffmpeg_exe raises its own advice
        logger.warning("showcase: ffmpeg not found, skipping poster grab")
        return None

    suffix = "." + (_VIDEO_EXT.get(content_type) or "mp4")

    with tempfile.TemporaryDirectory(prefix="showcase-poster-") as work:
        clip = os.path.join(work, "clip" + suffix)
        with open(clip, "wb") as fh:
            fh.write(contents)

        for index, seconds in enumerate(POSTER_PROBE_SECONDS):
            frame = os.path.join(work, f"frame{index}.png")
            try:
                subprocess.run(
                    [
                        exe,
                        "-v", "error",
                        "-ss", str(seconds),
                        "-i", clip,
                        "-frames:v", "1",
                        "-y", frame,
                    ],
                    check=True,
                    timeout=POSTER_TIMEOUT_SECONDS,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:  # noqa: BLE001 - a clip shorter than this probe
                continue

            if not os.path.exists(frame) or os.path.getsize(frame) == 0:
                continue

            try:
                img = PILImage.open(frame)
                img.load()
            except Exception:  # noqa: BLE001 - unreadable frame, try the next
                continue

            # ⚠ MEASURED ON A THUMBNAIL, not the full frame: this runs inside an
            # upload and the answer to "is this black" does not get better with
            # more pixels.
            probe = img.convert("L")
            probe.thumbnail((64, 64))
            if PILImageStat.Stat(probe).mean[0] < POSTER_MIN_BRIGHTNESS:
                continue

            aspect = nearest_aspect(img.size[0], img.size[1])
            img = img.convert("RGB")
            if max(img.size) > IMAGE_MAX_PX:
                img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX), PILImage.LANCZOS)

            out = _io.BytesIO()
            img.save(out, "WEBP", quality=84, method=4)
            logger.info(
                "showcase: grabbed a poster at %.1fs (%s)", seconds, aspect
            )
            return out.getvalue(), aspect

    logger.info("showcase: no usable frame in the clip, leaving the poster empty")
    return None


def nearest_aspect(width: int, height: int) -> str:
    """The entry in `ASPECTS` closest to a real width x height."""
    if not width or not height:
        return DEFAULT_ASPECT
    ratio = width / height

    def gap(name: str) -> float:
        w, h = (float(n) for n in name.split(":"))
        return abs(ratio - w / h)

    return min(ASPECTS, key=gap)


# ===========================================================================
# What a browser is told
# ===========================================================================
def public_item(row: dict) -> dict:
    """One item as a visitor may see it.

    ⚠ AN ALLOW-LIST, NOT A DELETE-LIST — the same rule `banners.public_banner`
    keeps. The stored row carries `created_by` and `updated_by`; a route that
    spread the row and popped two keys would leak the third one somebody adds
    later.
    """
    media_id = row.get("media_id") or ""
    poster_id = row.get("poster_id") or ""
    return {
        "id": row.get("id") or "",
        "title": row.get("title") or "",
        "blurb": row.get("blurb") or "",
        "workflow": row.get("workflow") or "",
        "kind": row.get("media_kind") or "",
        "aspect": row.get("aspect") or DEFAULT_ASPECT,
        # Relative, not absolute: the API's own address is the client's
        # `VITE_API_BASE`, and it is the client that knows it.
        "media_url": f"/public/showcase/media/{media_id}" if media_id else "",
        "poster_url": f"/public/showcase/media/{poster_id}" if poster_id else "",
    }


def public_payload() -> dict:
    """The live items, capped at what a sales page should carry.

    ⚠ AN ITEM WITH NO FILE IS NOT SERVED. A row is created before its upload
    lands, so "live but empty" is a normal thirty-second state inside the admin
    panel — and a card with nothing in it on the page everyone lands on is worse
    than one fewer card.
    """
    live = [
        i for i in all_items()
        if i.get("active", True) and i.get("media_id") and i.get("media_kind")
    ]
    return {"items": [public_item(i) for i in live][:MAX_PUBLIC]}


# ===========================================================================
# Routes — PUBLIC. See the module docstring for why.
# ===========================================================================
@router.get("/public/showcase")
def public_showcase() -> dict:
    """Explore's wall. An empty list is normal and means "draw no gallery"."""
    return public_payload()


@router.get("/public/showcase/media/{stamp}")
def public_showcase_media(stamp: str):
    """One item's picture, poster or clip.

    ⚠ CACHED FOREVER, because the address IS the file: a new upload is a new id
    and therefore a new URL, so these bytes can never change under this path.

    ⚠ ONE ROUTE FOR ALL THREE, and the type is worked out from what is on disk
    rather than from the URL. A poster and a picture are both `<id>.webp`, and
    the client already knows which is which from `kind` — putting it in the path
    as well would be two sources of truth for one fact.
    """
    if not _ID_RE.match(stamp or ""):
        raise HTTPException(status_code=404, detail="No media.")
    # ⚠ THE ID MUST BELONG TO AN ITEM WE HOLD, not merely look like one. Serving
    # any well-formed name out of the directory would turn this into a read of
    # whatever else ever landed there.
    known: set[str] = set()
    for row in all_items():
        if row.get("media_id"):
            known.add(row["media_id"])
        if row.get("poster_id"):
            known.add(row["poster_id"])
    if stamp not in known:
        raise HTTPException(status_code=404, detail="No media.")

    for ext, media_type in (
        ("webp", "image/webp"),
        ("mp4", "video/mp4"),
        ("webm", "video/webm"),
    ):
        path = os.path.join(config.SHOWCASE_DIR, f"{stamp}.{ext}")
        if os.path.isfile(path):
            return FileResponse(
                path,
                media_type=media_type,
                # ⚠ `FileResponse` ANSWERS RANGE REQUESTS, which is what lets a
                # browser SEEK in a clip instead of pulling all of it down
                # before the first frame. That is the difference between a video
                # that plays and one that spins.
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
    raise HTTPException(status_code=404, detail="No media.")
