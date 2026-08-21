"""
video_frames.py — turn a video file into the stills the animatic renderer needs.

Pillow cannot decode video, and the whole animatic export is built on Pillow:
`render_frame` composites a PIL image per moment. So a video clip on the
timeline is rendered by first tearing the video into numbered PNGs and then
treating "which frame of the source is showing at time t" as a lookup.

Three things this module is careful about:

1. **THERE IS NO FFPROBE.** `imageio-ffmpeg` ships the ffmpeg binary and nothing
   else, so `probe_duration()` reads the length out of ffmpeg's own banner —
   the `Duration: 00:00:12.34` line it prints while analysing an input. That
   costs one process start and no decoding. `video_assemble.py` learned this the
   expensive way; the note at the top of its `clip_duration_ms` is the same
   lesson from the other direction.

2. **Extraction is CACHED BY CONTENT.** The cache key is the sha1 of the file's
   bytes plus the fps plus the source range, so re-exporting an unchanged
   project re-uses the stills instead of spending minutes decoding again. The
   key deliberately ignores the file's NAME and path: the same clip dropped into
   two animatics is the same work.

3. **A range, not the whole file.** 24fps over a two-minute clip is 2,880 PNGs
   whether or not the timeline shows more than four seconds of it. Callers pass
   the source range they actually need, and only that is written to disk.

ffmpeg discovery, the progress/cancel runner and the user-facing error type come
from `animatic.py` rather than being reimplemented — there is ONE ffmpeg
integration in this codebase and this module is a third caller of it (after
`video_assemble.py`), not a third implementation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading

from animatic import AnimaticError, ffmpeg_exe, run_ffmpeg

logger = logging.getLogger(__name__)

# The stills of one extraction, named so ffmpeg's own `%06d` writes them and a
# plain sort puts them in play order.
_FRAME_GLOB = "f_%06d.png"
_FRAME_NAME = "f_{:06d}.png"

# Written only after ffmpeg has exited 0. A directory WITHOUT it is a half-done
# extraction (a crash, a stop, a full disk) and is thrown away rather than
# trusted — half a clip silently rendering as the wrong frames is far worse than
# paying to extract it again.
_DONE = ".done"

# A guard against one clip filling the disk. At 24fps this is ~14 minutes of
# video in one extraction, well past anything an animatic frame should hold, and
# it is checked BEFORE ffmpeg runs so the failure is a sentence rather than a
# full volume. `MAX_RENDERED_STILLS` in animatic.py guards the same disk from
# the other end (how many frames get RENDERED); this one guards how many get
# DECODED.
MAX_EXTRACTED_FRAMES = 20_000


class VideoFrameError(AnimaticError):
    """Extraction failed for a reason worth showing the user verbatim.

    Subclasses AnimaticError so the worker's existing "show this one verbatim"
    branch catches it without a second except clause — same rule as AssembleError.
    """


# ---------------------------------------------------------------------------
# How long is it?
# ---------------------------------------------------------------------------
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def probe_duration(path: str) -> int:
    """Length of a video in milliseconds, or 0 if it can't be determined.

    ⚠ Deliberately does NOT use ffprobe: the common install here has none (see
    the module docstring). ffmpeg prints the container's duration to stderr
    while analysing any input, so asking it to do nothing at all with the file
    still gets the answer — `-t 0` means it stops before decoding a single
    frame, which makes this about as cheap as opening the file.

    Returns 0 rather than raising. A duration is used to SIZE a clip when it
    lands on the timeline; not knowing it means the clip gets a default hold,
    which is a worse guess but not a broken editor.
    """
    if not path or not os.path.isfile(path):
        return 0
    try:
        exe = ffmpeg_exe()
    except AnimaticError:
        return 0
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-nostdin", "-i", path, "-t", "0", "-f", "null", "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001 — an unmeasurable file is not a crash
        logger.debug("could not probe %s (ignored)", path, exc_info=True)
        return 0

    # ffmpeg exits non-zero here on some builds (there is no output stream), so
    # the return code is deliberately not checked — the banner is printed either
    # way, and the banner is the whole point.
    match = _DURATION_RE.search(proc.stderr or "")
    if not match:
        logger.debug("no Duration line in ffmpeg's banner for %s", path)
        return 0
    hours, minutes, seconds = match.groups()
    return int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))


# ---------------------------------------------------------------------------
# The cache key
# ---------------------------------------------------------------------------
# The digest memo. Keyed on the stat triple `proxies.cache_key` uses — the
# absolute path, mtime and byte length — mapping to the digest that triple last
# produced.
#
# ⚠ THIS DOES NOT CHANGE WHAT THE KEY MEANS. `content_hash` still answers "what
# is the sha1 of these bytes", and the same bytes still produce the same digest,
# so the cross-animatic dedupe below is untouched. A file rewritten in place
# changes its mtime and length, misses the memo, and is re-hashed.
#
# WHY: the digest is the extraction cache's KEY, so it is computed on the way in
# even when the extraction is already on disk — a cache HIT still read the whole
# file. The editor asks for a video thumbnail once per clip and again per library
# card, so opening a project with three 80MB clips read half a gigabyte off disk
# to answer questions it had already answered.
_HASH_MEMO_MAX = 256
_hash_memo: dict[tuple[str, int, int], str] = {}
_hash_memo_lock = threading.Lock()


def content_hash(path: str) -> str:
    """sha1 of the file's bytes, read in chunks.

    By CONTENT, not by name or mtime: the same clip dropped into two animatics,
    or re-uploaded under a new id, is the same extraction and should not be paid
    for twice. Hashing 50MB costs a fraction of a second next to decoding it.

    Memoised per (path, mtime, size) so a repeat question is free — see above.
    """
    try:
        stat = os.stat(path)
        memo_key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        # Unreadable: fall through to the open() below, which raises the real
        # error for the caller rather than this module inventing one.
        memo_key = None

    if memo_key is not None:
        with _hash_memo_lock:
            hit = _hash_memo.get(memo_key)
        if hit is not None:
            return hit

    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()

    if memo_key is not None:
        with _hash_memo_lock:
            # A flat cap and a clear, not an LRU: the entries are tiny, the
            # working set is "the clips in the project someone has open", and a
            # miss costs exactly what this function cost before the memo.
            if len(_hash_memo) >= _HASH_MEMO_MAX:
                _hash_memo.clear()
            _hash_memo[memo_key] = hexdigest
    return hexdigest


def _cache_name(digest: str, fps: int, start_ms: int, span_ms: int) -> str:
    """The folder one extraction lives in.

    The RANGE is in the key as well as the fps. Two clips cut from different
    parts of the same source are different extractions, and a cache that ignored
    that would serve the first one's stills for the second's timeline — every
    frame wrong, and wrong in a way that looks like a rendering bug rather than
    a caching one.
    """
    return f"{digest[:16]}_{fps}fps_{start_ms}_{span_ms}"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def extract_frames(
    path: str,
    fps: int,
    out_dir: str,
    *,
    start_ms: int = 0,
    span_ms: int | None = None,
    progress_cb=None,
    cancel_check=None,
) -> dict:
    """Tear `path` into numbered PNGs at `fps`, and say where they landed.

    Args:
        path: the source video.
        fps: stills per second of SOURCE time. Pass the export's frame rate —
            extracting finer buys nothing, because the export can never show a
            frame between two of its own.
        out_dir: the cache root. One sub-folder per (content, fps, range).
        start_ms: where in the source to start. Everything before it is skipped
            rather than decoded and thrown away.
        span_ms: how much source to take. None = to the end of the file.
        progress_cb: called with a 0–1 fraction while ffmpeg runs.
        cancel_check: called during extraction; True abandons it.

    Returns:
        {"dir", "fps", "count", "start_ms", "span_ms", "cached", "stopped"}
        — `cached` is True when nothing had to be decoded, which is what makes a
        second export of an unchanged project fast. `count` is 0 only when the
        run was cancelled.

    Raises:
        VideoFrameError: with a message written for the user.
    """
    if not path or not os.path.isfile(path):
        raise VideoFrameError(
            f"'{os.path.basename(path or 'video')}' is missing, so its frames "
            "can't be read. Re-upload the clip and try again."
        )

    fps = max(1, min(60, int(fps or 24)))
    start_ms = max(0, int(start_ms or 0))
    span = None if span_ms is None else max(1, int(span_ms))

    # Checked before anything is decoded: a refusal the user can act on beats a
    # full disk half an hour in.
    if span is not None:
        wanted = int(span * fps / 1000) + 2
        if wanted > MAX_EXTRACTED_FRAMES:
            raise VideoFrameError(
                f"That clip would need {wanted:,} extracted frames, more than the "
                f"{MAX_EXTRACTED_FRAMES:,} one clip can use. Trim it shorter, or "
                "lower the project's frame rate."
            )

    digest = content_hash(path)
    folder = os.path.join(out_dir, _cache_name(digest, fps, start_ms, -1 if span is None else span))

    # --- Already done? ------------------------------------------------------
    if os.path.isfile(os.path.join(folder, _DONE)):
        count = _count_frames(folder)
        if count:
            logger.info(
                "[video_frames] cache hit: %d still(s) for %s @%dfps",
                count, os.path.basename(path), fps,
            )
            return {
                "dir": folder, "fps": fps, "count": count, "start_ms": start_ms,
                "span_ms": span, "cached": True, "stopped": False,
            }
        # A .done with no frames behind it is a lie; fall through and redo it.
        logger.warning("[video_frames] %s claims to be complete but is empty", folder)

    # A partial extraction (no .done) is thrown away rather than added to — the
    # numbering has to be contiguous from 1 for `frame_path` to be a lookup.
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)

    exe = ffmpeg_exe()
    cmd = [exe, "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
    # `-ss` BEFORE `-i` seeks the INPUT, which skips the decode of everything
    # before it instead of decoding and discarding. Output timestamps then
    # restart at zero, which is why `-t` (not `-to`) is the right pairing.
    if start_ms:
        cmd += ["-ss", f"{start_ms / 1000:.3f}"]
    cmd += ["-i", path]
    if span is not None:
        cmd += ["-t", f"{span / 1000:.3f}"]
    cmd += [
        "-an",  # the timeline owns the audio; a clip's own track is mixed separately
        "-vf", f"fps={fps}",
        "-progress", "pipe:1",
        os.path.join(folder, _FRAME_GLOB),
    ]

    logger.info(
        "[video_frames] extracting %s @%dfps from %dms%s",
        os.path.basename(path), fps, start_ms,
        f" for {span}ms" if span is not None else " to the end",
    )
    try:
        finished = run_ffmpeg(cmd, span or 0, progress_cb, cancel_check)
    except AnimaticError as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise VideoFrameError(
            f"'{os.path.basename(path)}' couldn't be read as video: {e}"
        ) from e

    if not finished:
        shutil.rmtree(folder, ignore_errors=True)
        return {
            "dir": folder, "fps": fps, "count": 0, "start_ms": start_ms,
            "span_ms": span, "cached": False, "stopped": True,
        }

    count = _count_frames(folder)
    if not count:
        shutil.rmtree(folder, ignore_errors=True)
        raise VideoFrameError(
            f"No frames could be read out of '{os.path.basename(path)}'. It may "
            "be audio-only, or in a format this build of ffmpeg can't decode."
        )

    # Written LAST, so its presence means the whole extraction succeeded.
    with open(os.path.join(folder, _DONE), "w", encoding="utf-8") as fh:
        fh.write(f"{digest}\n{fps}\n{start_ms}\n{span}\n{count}\n")

    logger.info("[video_frames] extracted %d still(s) → %s", count, folder)
    return {
        "dir": folder, "fps": fps, "count": count, "start_ms": start_ms,
        "span_ms": span, "cached": False, "stopped": False,
    }


def _count_frames(folder: str) -> int:
    """How many stills are in an extraction.

    Counted by walking UP from 1 rather than by listing the directory, because
    the numbering has to be contiguous for `frame_path` to be arithmetic. A hole
    (a half-written file, a manual delete) ends the count there — the same
    honesty rule `panel_sequence.frames_on_disk` follows for key poses, one
    level down.
    """
    n = 0
    while os.path.isfile(os.path.join(folder, _FRAME_NAME.format(n + 1))):
        n += 1
    return n


def frame_path(info: dict, source_ms: float) -> str | None:
    """The still showing at `source_ms` of the ORIGINAL video, or None.

    ⚠ `source_ms` is measured on the SOURCE, not on the timeline — the caller
    has already turned "2.4s into this clip, at 2× speed" into "4.8s into the
    file". Keeping that conversion out of here is what lets the same extraction
    serve two clips cut from the same source at different speeds.

    Outside the extracted range the value is CLAMPED to the first or last still
    rather than returning nothing. That mirrors the way a keyframe holds outside
    its first and last key: a clip trimmed one frame past the end of its source
    shows a held last frame, not a black hole.
    """
    count = int(info.get("count") or 0)
    if count <= 0:
        return None
    fps = max(1, int(info.get("fps") or 24))
    # Frame k of an `fps=` filter output covers [k/fps, (k+1)/fps) of the range,
    # so the index is a floor. The epsilon stops a time landing exactly on a
    # boundary from falling to the frame before it through float error.
    into = float(source_ms) - float(info.get("start_ms") or 0)
    index = int(into * fps / 1000.0 + 1e-6)
    index = max(0, min(count - 1, index))
    return os.path.join(info["dir"], _FRAME_NAME.format(index + 1))


def clear_cache(out_dir: str) -> None:
    """Throw away every extraction under `out_dir`.

    Called when an animatic is deleted. The stills are pure derived data — they
    can always be made again from the uploads — so there is nothing to preserve
    and no reason to keep them once the project is gone.
    """
    shutil.rmtree(out_dir, ignore_errors=True)
