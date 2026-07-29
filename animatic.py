"""
animatic.py — Turn a timed image sequence (+ one audio track) into a video.

This is the only module that knows ffmpeg exists. It takes frames whose picture
paths are ALREADY resolved (the API knows where board panels and uploads live,
this module does not) and produces an MP4.

Two deliberate choices:

1. **Every frame is normalised with Pillow before ffmpeg sees it.** Uploaded
   images arrive at all sizes; the concat demuxer needs them uniform. Doing the
   letterbox / crop / label work in Python keeps it out of an unreadable ffmpeg
   filter graph, and reuses the Pillow dependency the pipeline already has.

2. **Length is set by the FRAMES, not by `-shortest`.** The output is cut at the
   exact sum of the frame durations, so a short audio file can't truncate the
   video and a long one can't extend it.

Nothing here spends AI quota — an animatic is images, timing and audio.
"""

import logging
import os
import re
import shutil
import subprocess
import threading

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class AnimaticError(RuntimeError):
    """Export failed for a reason worth showing the user verbatim."""


# The long edge of the exported video. Panels are generated around 1080px, so
# going higher than this only upscales.
LONG_EDGE = 1920

# Aspect ratios we pin to exact, familiar frame sizes rather than computing them.
_EXACT_SIZES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "4:5": (1080, 1350),
    "21:9": (1920, 824),
}

# Fonts tried for burned-in labels, in order. Falls back to Pillow's built-in.
_LABEL_FONTS = ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")


# ---------------------------------------------------------------------------
# ffmpeg discovery
# ---------------------------------------------------------------------------
def ffmpeg_exe() -> str:
    """Locate an ffmpeg binary: FFMPEG_BINARY → PATH → the imageio-ffmpeg copy.

    `imageio-ffmpeg` is in requirements.txt precisely so this works with no
    system install: it ships a static binary for the current platform.
    """
    explicit = os.environ.get("FFMPEG_BINARY", "").strip()
    if explicit:
        return explicit

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001 — turn any import/lookup failure into advice
        raise AnimaticError(
            "ffmpeg was not found, so the video can't be encoded. Install it with "
            "`pip install imageio-ffmpeg` (no system install needed), or set "
            "FFMPEG_BINARY to the full path of an ffmpeg executable."
        ) from e


def ffmpeg_available() -> bool:
    """True if an export could run right now (used by GET /health)."""
    try:
        return os.path.isfile(ffmpeg_exe()) or bool(shutil.which("ffmpeg"))
    except AnimaticError:
        return False


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------
def _even(n: int) -> int:
    """H.264 needs even dimensions on both axes."""
    n = int(round(n))
    return n if n % 2 == 0 else n + 1


def resolve_size(aspect_ratio: str) -> tuple[int, int]:
    """Pixel size for an aspect ratio string like '16:9'.

    Known ratios get an exact, familiar size; anything else is derived from the
    ratio with the long edge at LONG_EDGE. Unparseable input falls back to
    1920×1080 rather than failing an export over a typo.
    """
    key = (aspect_ratio or "").strip()
    if key in _EXACT_SIZES:
        return _EXACT_SIZES[key]

    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)\s*$", key)
    if not m:
        return _EXACT_SIZES["16:9"]
    w, h = float(m.group(1)), float(m.group(2))
    if w <= 0 or h <= 0:
        return _EXACT_SIZES["16:9"]

    ratio = w / h
    if ratio >= 1:
        return _even(LONG_EDGE), _even(LONG_EDGE / ratio)
    return _even(LONG_EDGE * ratio), _even(LONG_EDGE)


def _parse_colour(value: str) -> tuple[int, int, int]:
    """'#rrggbb' → (r, g, b). Anything unreadable is black."""
    s = (value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (0, 0, 0)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def _label_font(height: int):
    size = max(16, height // 32)
    for name in _LABEL_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    # Pillow >= 10.1 can scale the built-in font; older versions can't.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_label(canvas: Image.Image, text: str) -> None:
    """Burn a small caption into the bottom-left, on a translucent bar."""
    text = (text or "").strip()
    if not text:
        return
    w, h = canvas.size
    font = _label_font(h)
    draw = ImageDraw.Draw(canvas, "RGBA")
    pad = max(8, h // 90)
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x, y = pad * 2, h - th - pad * 3
    draw.rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad * 2],
        fill=(0, 0, 0, 150),
    )
    draw.text((x - box[0], y - box[1]), text, font=font, fill=(255, 255, 255, 235))


def render_frame(
    src_path: str,
    size: tuple[int, int],
    fit: str = "contain",
    background: str = "#000000",
    label: str = "",
) -> Image.Image:
    """Fit one source image onto the video frame.

    "contain" letterboxes it (nothing is lost — the default, because a
    storyboard frame you cropped is a frame you can't read); "cover" scales up
    and centre-crops so the frame is filled edge to edge.
    """
    target_w, target_h = size
    canvas = Image.new("RGB", (target_w, target_h), _parse_colour(background))

    with Image.open(src_path) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        if sw <= 0 or sh <= 0:
            raise AnimaticError(f"'{os.path.basename(src_path)}' has no pixels.")

        scale = (
            max(target_w / sw, target_h / sh)
            if fit == "cover"
            else min(target_w / sw, target_h / sh)
        )
        new = im.resize(
            (max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))),
            Image.LANCZOS,
        )

    left = (target_w - new.width) // 2
    top = (target_h - new.height) // 2
    canvas.paste(new, (left, top))
    if label:
        _draw_label(canvas, label)
    return canvas


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _write_concat_list(path: str, entries: list[tuple[str, float]]) -> None:
    """Write an ffconcat list of (filename, seconds) pairs.

    Filenames are relative to the list file's own directory, which sidesteps
    every Windows path-quoting problem the concat demuxer has. The last image is
    repeated with no duration — without that, the concat demuxer drops the final
    frame, so the animatic would end one picture early.
    """
    lines = ["ffconcat version 1.0"]
    for name, seconds in entries:
        lines.append(f"file {name}")
        lines.append(f"duration {seconds:.3f}")
    lines.append(f"file {entries[-1][0]}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _run_ffmpeg(
    cmd: list[str],
    total_ms: int,
    progress_cb=None,
    cancel_check=None,
) -> bool:
    """Run ffmpeg, reporting progress. Returns False if it was cancelled.

    Progress comes from `-progress pipe:1`, which emits plain `key=value` lines
    — stable across ffmpeg versions, unlike scraping the human-readable stderr.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    # Drained in a thread so a long error log can't fill the pipe and deadlock us.
    errors: list[str] = []

    def _drain():
        for line in proc.stderr:
            errors.append(line.rstrip())

    t = threading.Thread(target=_drain, daemon=True)
    t.start()

    cancelled = False
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us=") and total_ms > 0 and progress_cb:
                try:
                    done_ms = int(line.split("=", 1)[1]) / 1000
                except ValueError:
                    continue
                progress_cb(max(0.0, min(1.0, done_ms / total_ms)))
            if cancel_check and cancel_check():
                cancelled = True
                proc.terminate()
                break
    finally:
        proc.stdout.close()
        proc.wait()
        t.join(timeout=2)

    if cancelled:
        return False
    if proc.returncode != 0:
        tail = "\n".join(errors[-6:]).strip() or f"exit code {proc.returncode}"
        raise AnimaticError(f"ffmpeg couldn't encode the video: {tail}")
    return True


def build_animatic(
    job_id: str,
    frames: list[dict],
    *,
    audio_path: str | None = None,
    audio_offset_ms: int = 0,
    aspect_ratio: str = "16:9",
    fps: int = 24,
    fit: str = "contain",
    background: str = "#000000",
    show_labels: bool = False,
    output_dir: str = "output",
    progress_cb=None,
    cancel_check=None,
) -> dict:
    """Encode `frames` into an MP4 under output/_animatics/{job_id}/.

    Args:
        frames: [{"path": str, "duration_ms": int, "label": str}] in play order.
            A frame whose file is missing is SKIPPED (a panel may have been
            deleted from the board since) and reported in the result.
        audio_path: optional audio file laid under the whole sequence.
        audio_offset_ms: how far into that file playback starts.
        progress_cb: called with {"percent", "message", "stage"}.
        cancel_check: called between frames and during encoding; True stops.

    Returns a summary dict; `stopped` is True if the user cancelled.
    """
    if not frames:
        raise AnimaticError("This animatic has no frames yet — add some images first.")

    exe = ffmpeg_exe()  # fail early, before any work is done
    size = resolve_size(aspect_ratio)
    fps = max(1, min(60, int(fps or 24)))

    out_root = os.path.join(output_dir, "_animatics", job_id)
    build_dir = os.path.join(out_root, "_build")
    shutil.rmtree(build_dir, ignore_errors=True)
    os.makedirs(build_dir, exist_ok=True)

    def _report(percent: int, message: str, stage: str = "encoding"):
        if progress_cb:
            try:
                progress_cb({"percent": percent, "message": message, "stage": stage})
            except Exception:  # noqa: BLE001 — progress must never kill an export
                logger.debug("[animatic %s] progress callback failed", job_id, exc_info=True)

    def _cancelled() -> bool:
        if not cancel_check:
            return False
        try:
            return bool(cancel_check())
        except Exception:  # noqa: BLE001 — a broken check must not stop the export
            return False

    # --- 1. Normalise every frame to the exact video size ------------------
    entries: list[tuple[str, float]] = []
    skipped: list[int] = []
    total = len(frames)

    for i, frame in enumerate(frames):
        if _cancelled():
            shutil.rmtree(build_dir, ignore_errors=True)
            return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}

        path = frame.get("path")
        if not path or not os.path.isfile(path):
            logger.warning("[animatic %s] frame %d has no image (%s) — skipped", job_id, i, path)
            skipped.append(i)
            continue

        name = f"f{len(entries):04d}.png"
        try:
            image = render_frame(
                path,
                size,
                fit=fit,
                background=background,
                label=frame.get("label", "") if show_labels else "",
            )
            image.save(os.path.join(build_dir, name), "PNG")
        except AnimaticError:
            raise
        except Exception as e:  # noqa: BLE001 — one unreadable file, not a dead export
            logger.warning("[animatic %s] frame %d unreadable (%s) — skipped", job_id, i, e)
            skipped.append(i)
            continue

        seconds = max(0.1, int(frame.get("duration_ms") or 2000) / 1000)
        entries.append((name, seconds))
        # Preparing frames is the first 55% — it's real work on big images.
        _report(int(55 * (i + 1) / total), f"Preparing frame {i + 1} of {total}", "frames")

    if not entries:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise AnimaticError(
            "None of the frames could be read — their images may have been deleted."
        )

    total_ms = int(round(sum(sec for _, sec in entries) * 1000))
    list_path = os.path.join(build_dir, "list.txt")
    _write_concat_list(list_path, entries)

    # --- 2. Encode ---------------------------------------------------------
    out_path = os.path.join(out_root, "animatic.mp4")
    tmp_path = os.path.join(build_dir, "out.mp4")

    cmd = [exe, "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
    cmd += ["-f", "concat", "-safe", "0", "-i", list_path]
    has_audio = bool(audio_path and os.path.isfile(audio_path))
    if has_audio:
        if audio_offset_ms > 0:
            cmd += ["-ss", f"{audio_offset_ms / 1000:.3f}"]
        cmd += ["-i", audio_path]

    cmd += ["-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",  # required for playback in browsers / QuickTime
        "-r", str(fps),
        # The frames decide the length: a short audio file must not truncate the
        # video (which is what -shortest would do), and a long one must not run on.
        "-t", f"{total_ms / 1000:.3f}",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        tmp_path,
    ]

    _report(58, "Encoding video…")
    logger.info(
        "[animatic %s] encoding %d frame(s), %.1fs, %dx%d @%dfps%s",
        job_id, len(entries), total_ms / 1000, size[0], size[1], fps,
        " + audio" if has_audio else "",
    )

    def _enc_progress(fraction: float):
        _report(58 + int(40 * fraction), "Encoding video…")

    finished = _run_ffmpeg(cmd, total_ms, _enc_progress, cancel_check)
    if not finished:
        shutil.rmtree(build_dir, ignore_errors=True)
        logger.info("[animatic %s] export STOPPED by user", job_id)
        return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}

    os.replace(tmp_path, out_path)
    shutil.rmtree(build_dir, ignore_errors=True)
    _report(100, "Done")

    size_bytes = os.path.getsize(out_path)
    logger.info(
        "[animatic %s] exported %s (%.1f MB)", job_id, out_path, size_bytes / 1_048_576
    )
    return {
        "stopped": False,
        "video": out_path,
        "duration_ms": total_ms,
        "frame_count": len(entries),
        "skipped_frames": skipped,
        "width": size[0],
        "height": size[1],
        "fps": fps,
        "has_audio": has_audio,
        "size_bytes": size_bytes,
    }
