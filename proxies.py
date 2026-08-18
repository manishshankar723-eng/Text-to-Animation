"""
proxies.py — half-res copies of the pictures, for the editor to scrub on.

A storyboard panel is drawn around 1080px and an upload can be far bigger. The
editor fetches every one of them as an authed blob and holds it for the whole
session: a sixty-panel board is sixty full-size PNGs down the wire, decoded, and
kept in memory to draw a monitor that is 600px wide and timeline tiles that are
100px wide. A proxy is the same picture at the size it is actually looked at.

⚠ WHAT A PROXY IS GUARANTEED TO SAVE IS PIXELS, NOT BYTES. The rung is a cap on
the LONG EDGE, so a 1920px panel — the export's own long edge — comes back at
960px, which is a QUARTER of the decoded bitmap: a quarter of the memory the
browser holds per clip and a quarter of the work to decode it and push it to a
texture. That is the win that made this worth building, and how much of it you
get depends on how big the source was. The FILE is usually smaller too,
and for a drawn or photographed panel it is much smaller, but it is not smaller
for everything: resampling turns hard edges into anti-aliased gradients, and a
picture that is mostly hard edges (a test card, flat vector line art) can encode
LARGER at half the size than it did at full. That is a real property of PNG, not
a bug to tune away, and `tests/export_perf_check.py` states it both ways.

Four rules, and the first one is the only one that could ever make the preview
lie:

1. **A PROXY IS LOSSLESS AND SMALLER — never re-encoded, never re-coloured.**
   It is a PNG, resized with LANCZOS, and nothing else happens to it. So the
   difference between the monitor and the export is SHARPNESS at high zoom, and
   nothing about geometry, colour, timing or which frame is on screen. That
   matters because the effects chain runs on whatever texture the monitor was
   given: a chroma key against a half-res edge keys a very slightly softer edge.
   That is the standard NLE bargain and it is the reason `PROXY_EDGES` tops out
   at 1440 rather than at something aggressive.

2. **THE EXPORT NEVER TOUCHES THIS MODULE.** `build_animatic` opens the source
   file, always. A proxy that reached the encoder would be an export quietly
   rendered at half resolution, which is the exact failure this codebase keeps
   calling "a preview that lies" — only in the direction nobody would check.

3. **Keyed by a STAT, never by a decode.** The cache name is the sha1 of
   (path, mtime_ns, size, edge). `_frame_version` in `server/animatics.py` made
   the same choice for the same reason — it is asked on every frame of every
   read — and unlike `video_frames.content_hash` there is nothing expensive on
   the other side of the question to amortise a full read against.

4. **A miss is not an error.** Every function here falls back to the SOURCE
   PATH: an unreadable image, a Pillow without the codec, a read-only cache
   directory. The worst case is what the editor did before this module existed.

The cache is pure derived data — it can always be made again from the source —
so `clear_cache` is safe to call whenever the project it belongs to goes away.
"""

from __future__ import annotations

import hashlib
import logging
import os

from PIL import Image

logger = logging.getLogger(__name__)

# The sizes a proxy is allowed to be, as a LONG edge. A ladder rather than a
# free number so that two clients asking for "about 900" share one cached file
# instead of writing two — a cache that fragments per browser window is a cache
# that never hits.
#
# 960 is the default and it is where the phrase "half-res" comes from: the
# export's long edge is 1920 (see `LONG_EDGE` in animatic.py), so this is
# exactly half of a 1080p frame. 480 is for tiles, 1440 for a big display.
PROXY_EDGES = (480, 960, 1440)
DEFAULT_PROXY_EDGE = 960

# The whole feature, off. `ANIMATIC_PROXY_EDGE=0` serves every picture at full
# size exactly as this app did before proxies existed — worth having as one
# environment variable rather than as a code path someone has to reason about.
_ENV_EDGE = "ANIMATIC_PROXY_EDGE"

# A source this small is served as itself: making a "proxy" that is the same
# size or bigger costs a decode, an encode and a file, and returns a picture
# that is at best identical and at worst upscaled.
_MIN_GAIN = 1.15


def proxy_edge(requested: int | None = None) -> int:
    """The ladder rung a request lands on, or 0 when proxies are switched off.

    Rounds UP to the next rung, so asking for 500 gets 960 rather than a picture
    softer than the one you asked for. Above the top rung there is no proxy at
    all — the source is already the best answer.
    """
    limit = _configured_edge()
    if limit <= 0:
        return 0
    wanted = int(requested or DEFAULT_PROXY_EDGE)
    if wanted <= 0:
        return 0
    for edge in PROXY_EDGES:
        if wanted <= edge:
            return min(edge, limit)
    return 0


def _configured_edge() -> int:
    """`ANIMATIC_PROXY_EDGE` — the largest proxy this install will make.

    Read on every call rather than at import, so a test (and `.env`, which
    uvicorn does not reload) can turn the feature off without reimporting half
    the app. It is one `os.environ` lookup against a decode.
    """
    raw = (os.environ.get(_ENV_EDGE) or "").strip()
    if not raw:
        return PROXY_EDGES[-1]
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("%s=%r is not a number — proxies left on", _ENV_EDGE, raw)
        return PROXY_EDGES[-1]


def cache_key(path: str, edge: int) -> str:
    """The name one proxy lives under: sha1(path, mtime_ns, size, edge).

    ⚠ A STAT, NOT A DECODE — see rule 3 in the module docstring. The mtime and
    the byte length together are what make a REDRAWN panel a different proxy:
    the storyboard writes the new picture over the same path, and a key built
    from the path alone would serve the old drawing for ever. That is the same
    bug `_frame_version` exists to prevent one layer up, and it has already been
    found in this codebase twice.
    """
    try:
        stat = os.stat(path)
        stamp = f"{stat.st_mtime_ns}-{stat.st_size}"
    except OSError:
        stamp = "0-0"
    digest = hashlib.sha1(f"{os.path.abspath(path)}|{stamp}|{edge}".encode("utf-8"))
    return f"{digest.hexdigest()[:20]}_{edge}.png"


def proxy_for(path: str, cache_dir: str, edge: int | None = None) -> str:
    """A cached half-res copy of `path`, or `path` itself.

    Returns a path either way, so every caller is a one-liner and none of them
    has to carry a "did it work" branch — see rule 4. The reasons it hands back
    the source are all ordinary: proxies are off, the request is above the top
    rung, the picture is already small, the file is unreadable, the cache is not
    writable.

    ⚠ NOT FOR THE EXPORT. `build_animatic` reads sources. See rule 2.
    """
    target = proxy_edge(edge)
    if not target or not path or not os.path.isfile(path):
        return path

    out = os.path.join(cache_dir, cache_key(path, target))
    if os.path.isfile(out):
        return out

    try:
        with Image.open(path) as im:
            width, height = im.size
            if max(width, height) < target * _MIN_GAIN:
                # Already about this size. Serving the source is both cheaper
                # and better than writing a copy of it.
                return path
            scale = target / max(width, height)
            small = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.LANCZOS,
            )
            os.makedirs(cache_dir, exist_ok=True)
            # Written beside the real name and then MOVED, so a reader can never
            # open a half-written proxy — two requests for the same new picture
            # arrive together every single time the editor loads a board.
            tmp = f"{out}.{os.getpid()}.part"
            small.save(tmp, "PNG")
            os.replace(tmp, out)
    except Exception:  # noqa: BLE001 — a proxy is never worth failing a request
        logger.warning("[proxies] could not make a proxy of %s — serving the source",
                       os.path.basename(path), exc_info=True)
        return path

    return out


def clear_cache(cache_dir: str) -> None:
    """Throw away every proxy under `cache_dir`.

    Pure derived data, exactly like `video_frames.clear_cache` — the pictures
    can always be made again from their sources, so there is nothing here worth
    preserving once the project that pointed at them is gone.
    """
    import shutil

    shutil.rmtree(cache_dir, ignore_errors=True)
