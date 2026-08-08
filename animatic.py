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


# Quality name → x264 CRF. Lower is better and bigger; the range is narrow
# because an animatic is mostly still frames, which compress very well.
_CRF = {"high": 18, "medium": 21, "low": 25}
# The short edge the sizes in `_EXACT_SIZES` are written for.
BASE_SHORT_EDGE = 1080


def resolve_size(aspect_ratio: str, resolution: int = BASE_SHORT_EDGE) -> tuple[int, int]:
    """Pixel size for an aspect ratio like '16:9' at the given SHORT edge.

    `resolution` is the short edge — 1080 means 1920×1080 for 16:9 and 1080×1920
    for 9:16, which is how "1080p" is normally meant. The familiar sizes below
    are written for 1080 and scaled from there, so passing the default returns
    exactly what it always did.

    Known ratios get an exact, familiar size; anything else is derived from the
    ratio. Unparseable input falls back to 16:9 rather than failing an export
    over a typo.
    """
    base = _base_size(aspect_ratio)
    scale = max(0.1, (resolution or BASE_SHORT_EDGE) / BASE_SHORT_EDGE)
    if abs(scale - 1.0) < 1e-6:
        return base
    return _even(base[0] * scale), _even(base[1] * scale)


def _base_size(aspect_ratio: str) -> tuple[int, int]:
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


# --- On-screen text ---------------------------------------------------------
# Font height as a fraction of the frame height, per size name.
_TEXT_DIVISOR = {"small": 30, "medium": 21, "large": 14}
# Text never runs edge to edge — this is the usable width, as a fraction.
_TEXT_WIDTH = 0.86
_LINE_SPACING = 1.28


def _text_font(height: int, size_name: str):
    px = max(14, int(height / _TEXT_DIVISOR.get(size_name, _TEXT_DIVISOR["medium"])))
    for name in _LABEL_FONTS:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: float) -> list[str]:
    """Break `text` into lines that fit `max_width`, keeping the user's newlines.

    A single word longer than the line is left to overflow rather than being cut
    mid-word — a broken word is harder to read than a slightly wide line.
    """
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def draw_texts(canvas: Image.Image, clips: list[dict]) -> None:
    """Draw the text clips that are on screen for this moment.

    Clips sharing a position stack downward in the order given, so two captions
    at the bottom don't land on top of each other.
    """
    if not clips:
        return
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas, "RGBA")
    max_width = width * _TEXT_WIDTH
    margin = height * 0.055

    # Measure everything first: a position's block can only be placed once the
    # total height of every clip sharing that position is known.
    blocks: dict[str, list[dict]] = {"top": [], "middle": [], "bottom": []}
    for clip in clips:
        text = (clip.get("text") or "").strip()
        if not text:
            continue
        font = _text_font(height, clip.get("size", "medium"))
        lines = _wrap_text(draw, text, font, max_width)
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * _LINE_SPACING)
        pad = max(6, int(line_h * 0.28))
        widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        position = clip.get("position", "bottom")
        if position not in blocks:
            position = "bottom"
        blocks[position].append(
            {
                "clip": clip,
                "font": font,
                "lines": lines,
                "line_h": line_h,
                "pad": pad,
                "text_w": widest,
                "height": line_h * len(lines) + pad * 2,
            }
        )

    for position, group in blocks.items():
        if not group:
            continue
        total = sum(b["height"] for b in group) + margin * 0.25 * (len(group) - 1)
        if position == "top":
            y = margin
        elif position == "middle":
            y = (height - total) / 2
        else:
            y = height - total - margin

        for block in group:
            clip = block["clip"]
            font, lines, line_h, pad = block["font"], block["lines"], block["line_h"], block["pad"]
            align = clip.get("align", "center")
            ink = _parse_colour(clip.get("color", "#ffffff"))
            backdrop = clip.get("backdrop", "scrim")

            box_w = min(width - margin, block["text_w"] + pad * 2)
            if align == "left":
                box_x = margin
            elif align == "right":
                box_x = width - margin - box_w
            else:
                box_x = (width - box_w) / 2

            if backdrop in ("scrim", "box"):
                alpha = 140 if backdrop == "scrim" else 225
                draw.rounded_rectangle(
                    [box_x, y, box_x + box_w, y + block["height"]],
                    radius=max(4, int(pad * 0.6)),
                    fill=(0, 0, 0, alpha),
                )

            ty = y + pad
            for line in lines:
                line_w = draw.textlength(line, font=font)
                if align == "left":
                    tx = box_x + pad
                elif align == "right":
                    tx = box_x + box_w - pad - line_w
                else:
                    tx = box_x + (box_w - line_w) / 2
                # With no backdrop the text sits straight on the art, which for a
                # grey storyboard thumbnail can be white-on-white — so it always
                # gets a dark outline in that mode.
                if backdrop == "none":
                    draw.text(
                        (tx, ty), line, font=font, fill=(*ink, 255),
                        stroke_width=max(2, line_h // 14), stroke_fill=(0, 0, 0, 210),
                    )
                else:
                    draw.text((tx, ty), line, font=font, fill=(*ink, 255))
                ty += line_h
            y += block["height"] + margin * 0.25


# --- Shapes -----------------------------------------------------------------
# Each shape is defined as points on the UNIT SQUARE (0–1), scaled into its box.
# ⚠ The SAME polygons are in client/src/components/Shapes.jsx as CSS clip-paths —
# that is what makes the preview and the exported frame agree. Change one, change
# the other. 'ellipse' has no point list: it is drawn as an ellipse.
_SHAPE_POINTS: dict[str, tuple[tuple[float, float], ...]] = {
    "rect": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
    "pentagon": ((0.5, 0.0), (1.0, 0.38), (0.82, 1.0), (0.18, 1.0), (0.0, 0.38)),
    "star": (
        (0.5, 0.0), (0.61, 0.35), (0.98, 0.35), (0.68, 0.57),
        (0.79, 0.91), (0.5, 0.7), (0.21, 0.91), (0.32, 0.57),
        (0.02, 0.35), (0.39, 0.35),
    ),
}
SHAPE_KINDS = ("rect", "ellipse", "pentagon", "star")


def draw_shapes(canvas: Image.Image, shapes: list[dict]) -> None:
    """Draw the shapes that are on screen for this moment, in order.

    Geometry arrives as FRACTIONS of the frame, so the same project draws the
    same picture at 720p and at 4K. Each shape gets its own RGBA layer: that is
    what makes rotation work for every kind (Pillow can't draw a rotated
    ellipse) and keeps opacity exact, since the layer is composited once rather
    than blended shape-by-shape.
    """
    if not shapes:
        return
    width, height = canvas.size

    for shape in shapes:
        opacity = float(shape.get("opacity", 1.0) or 0.0)
        if opacity <= 0:
            continue
        box_w = max(1, int(round(abs(float(shape.get("w", 0.25))) * width)))
        box_h = max(1, int(round(abs(float(shape.get("h", 0.25))) * height)))
        # A shape bigger than the frame is legal (a wash over the whole picture),
        # but there is no reason to allocate a layer larger than one can be seen.
        box_w, box_h = min(box_w, width * 3), min(box_h, height * 3)

        layer = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        alpha = max(0, min(255, int(round(opacity * 255))))
        fill = (*_parse_colour(shape.get("color", "#c2185b")), alpha)
        kind = (shape.get("kind") or "rect").lower()

        if kind == "ellipse":
            pen.ellipse([0, 0, box_w - 1, box_h - 1], fill=fill)
        else:
            points = _SHAPE_POINTS.get(kind, _SHAPE_POINTS["rect"])
            pen.polygon([(px * (box_w - 1), py * (box_h - 1)) for px, py in points], fill=fill)

        rotation = float(shape.get("rotation", 0.0) or 0.0) % 360
        if rotation:
            # NEGATED because Pillow rotates anticlockwise and the editor (like
            # CSS) treats a positive angle as clockwise.
            layer = layer.rotate(-rotation, resample=Image.BICUBIC, expand=True)

        # x/y are the CENTRE, which is why rotation doesn't move the shape.
        cx = float(shape.get("x", 0.5)) * width
        cy = float(shape.get("y", 0.5)) * height
        at = (int(round(cx - layer.width / 2)), int(round(cy - layer.height / 2)))
        # The layer is its own mask, so the transparent corners of a star stay
        # transparent and a part-opaque shape blends with the art underneath.
        canvas.paste(layer, at, layer)


def draw_overlays(canvas: Image.Image, overlays: list[dict]) -> None:
    """Composite the overlay PICTURES that are on screen for this moment.

    Geometry is identical to a shape's — fractions of the frame, `x`/`y` the
    centre — because they are placed with the same handles in the editor. The
    difference is only the fill: a file rather than a colour.

    Each picture is fitted INSIDE its box preserving aspect ratio ("contain"),
    so a logo dropped into a square box isn't stretched into a different logo.
    A file that has gone is skipped: one missing overlay must not kill a whole
    export.
    """
    if not overlays:
        return
    width, height = canvas.size

    for item in overlays:
        opacity = float(item.get("opacity", 1.0) or 0.0)
        path = item.get("path")
        if opacity <= 0 or not path or not os.path.isfile(path):
            continue
        box_w = max(1, min(int(round(abs(float(item.get("w", 0.3))) * width)), width * 3))
        box_h = max(1, min(int(round(abs(float(item.get("h", 0.3))) * height)), height * 3))

        try:
            with Image.open(path) as source:
                picture = source.convert("RGBA")
        except OSError:
            logger.warning("[animatic] overlay image unreadable (%s) — skipped", path)
            continue

        # `resize`, NOT `thumbnail`: thumbnail only ever shrinks, so a small
        # logo dragged out to fill half the frame would stubbornly stay small
        # in the export while the preview showed it big. Scale is the same
        # "contain" rule the frames use, and it goes both ways.
        scale = min(box_w / picture.width, box_h / picture.height)
        picture = picture.resize(
            (max(1, int(round(picture.width * scale))), max(1, int(round(picture.height * scale)))),
            Image.LANCZOS,
        )
        if opacity < 1:
            # Scale the EXISTING alpha rather than replacing it, so a cut-out
            # PNG stays cut out when it is faded.
            alpha = picture.getchannel("A").point(lambda a: int(a * opacity))
            picture.putalpha(alpha)

        rotation = float(item.get("rotation", 0.0) or 0.0) % 360
        if rotation:
            # NEGATED: Pillow rotates anticlockwise, the editor (like CSS)
            # treats a positive angle as clockwise. Same as draw_shapes.
            picture = picture.rotate(-rotation, resample=Image.BICUBIC, expand=True)

        cx = float(item.get("x", 0.5)) * width
        cy = float(item.get("y", 0.5)) * height
        canvas.paste(
            picture,
            (int(round(cx - picture.width / 2)), int(round(cy - picture.height / 2))),
            picture,
        )


def render_frame(
    src_path: str,
    size: tuple[int, int],
    fit: str = "contain",
    background: str = "#000000",
    label: str = "",
    texts: list[dict] | None = None,
    shapes: list[dict] | None = None,
    overlays: list[dict] | None = None,
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
    # Stacking order, bottom to top: picture → shapes → overlay images → text.
    # Shapes sit under the overlays because a shape is usually a highlight or a
    # mask ON the art, while an overlay is a picture element (a logo, an inset)
    # that belongs above it. Text is last so a caption is always readable.
    if shapes:
        draw_shapes(canvas, shapes)
    if overlays:
        draw_overlays(canvas, overlays)
    # Text goes on before the shot label, so the label always stays legible in
    # its corner even if a caption is placed at the bottom too.
    if texts:
        draw_texts(canvas, texts)
    if label:
        _draw_label(canvas, label)
    return canvas


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def plan_segments(
    frames: list[dict],
    texts: list[dict],
    end_ms: int | None = None,
    shapes: list[dict] | None = None,
    overlays: list[dict] | None = None,
) -> tuple[list[dict], int]:
    """Cut the timeline into stretches where the picture, text AND shapes hold still.

    Text clips and shapes have their own start and length, so one can appear
    half-way through a held image or run across a cut. Rather than fight that in
    an ffmpeg filter graph, the timeline is split at every clip boundary and each
    piece is rendered as its own still. With no text and no shapes there is
    exactly one segment per frame, so nothing changes for an animatic with
    neither.

    `end_ms` extends the video past the last picture — the LAST FRAME IS HELD
    for the remainder. That's what makes an export cover a music bed that
    outlasts the pictures instead of stopping dead at the final image. Passing
    None (or anything shorter than the frames) keeps the old behaviour exactly.

    Returns (segments, total_ms) where each segment is
    {"frame": index into `frames`, "start_ms", "duration_ms", "texts": [clip…],
    "shapes": [shape…]}.
    """
    spans: list[tuple[int, int, int]] = []  # (start, end, frame index)
    clock = 0
    for i, frame in enumerate(frames):
        length = max(100, int(frame.get("duration_ms") or 2000))
        spans.append((clock, clock + length, i))
        clock += length
    if not spans:
        return [], 0
    # Hold the last picture out to `end_ms` when something else runs longer.
    if end_ms and end_ms > clock:
        start, _end, index = spans[-1]
        spans[-1] = (start, end_ms, index)
        clock = end_ms
    total_ms = clock

    # Normalise the clips once, and drop any that fall entirely off the end.
    clips: list[tuple[int, int, dict]] = []
    for clip in texts:
        if not (clip.get("text") or "").strip():
            continue
        start = max(0, int(clip.get("start_ms") or 0))
        end = start + max(100, int(clip.get("duration_ms") or 0))
        if start >= total_ms:
            continue
        clips.append((start, min(end, total_ms), clip))

    # The same treatment for shapes and overlay pictures. An invisible one
    # (fully transparent) is dropped here rather than drawn as nothing, so it
    # can't cost a segment.
    def _timed(items) -> list[tuple[int, int, dict]]:
        out: list[tuple[int, int, dict]] = []
        for item in items or []:
            if float(item.get("opacity", 1.0) or 0.0) <= 0:
                continue
            start = max(0, int(item.get("start_ms") or 0))
            end = start + max(100, int(item.get("duration_ms") or 0))
            if start >= total_ms:
                continue
            out.append((start, min(end, total_ms), item))
        return out

    figures = _timed(shapes)
    pictures = _timed(overlays)

    cuts = {0, total_ms}
    for start, end, _ in spans:
        cuts.add(start)
        cuts.add(end)
    for start, end, _ in clips + figures + pictures:
        cuts.add(start)
        cuts.add(end)
    ordered = sorted(c for c in cuts if 0 <= c <= total_ms)

    segments: list[dict] = []
    for a, b in zip(ordered, ordered[1:]):
        if b - a <= 0:
            continue
        frame_index = next((i for (s, e, i) in spans if s <= a < e), None)
        if frame_index is None:
            continue
        segments.append(
            {
                "frame": frame_index,
                "start_ms": a,
                "duration_ms": b - a,
                "texts": [clip for (s, e, clip) in clips if s <= a < e],
                "shapes": [shape for (s, e, shape) in figures if s <= a < e],
                "overlays": [pic for (s, e, pic) in pictures if s <= a < e],
            }
        )

    # A boundary landing exactly on a frame edge can leave a sliver too short for
    # ffmpeg to show; fold anything under 40ms into its neighbour.
    merged: list[dict] = []
    for segment in segments:
        if merged and segment["duration_ms"] < 40 and merged[-1]["frame"] == segment["frame"]:
            merged[-1]["duration_ms"] += segment["duration_ms"]
            continue
        merged.append(segment)
    return merged, total_ms


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


def run_ffmpeg(
    cmd: list[str],
    total_ms: int,
    progress_cb=None,
    cancel_check=None,
) -> bool:
    """Run ffmpeg, reporting progress. Returns False if it was cancelled.

    Progress comes from `-progress pipe:1`, which emits plain `key=value` lines
    — stable across ffmpeg versions, unlike scraping the human-readable stderr.

    Public because video_assemble.py drives ffmpeg too and must handle progress,
    cancellation and error reporting identically — one runner, one behaviour.
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
    texts: list[dict] | None = None,
    shapes: list[dict] | None = None,
    overlays: list[dict] | None = None,
    audio_tracks: list[dict] | None = None,
    aspect_ratio: str = "16:9",
    resolution: int = BASE_SHORT_EDGE,
    quality: str = "high",
    end_ms: int | None = None,
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
        texts: [{"id", "text", "start_ms", "duration_ms", "position", "align",
            "size", "color", "backdrop"}] — the text layer, timed independently
            of the frames. Note that because a missing frame is dropped, text
            timed against a timeline that HAD that frame shifts with everything
            after it; the alternative (holding a blank) would be worse.
        shapes: [{"id", "kind", "start_ms", "duration_ms", "x", "y", "w", "h",
            "color", "opacity", "rotation"}] — the shape layer, drawn UNDER the
            text. Geometry is in fractions of the frame, so it is resolution
            independent (see `draw_shapes`).
        overlays: the same, but each carries a "path" to a picture instead of a
            colour — the image layers. Composited over the shapes and under the
            text; one whose file has gone is skipped.
        audio_tracks: [{"path", "offset_ms", "volume"}] laid under the sequence
            and MIXED together — music under a voiceover is the usual pair.
            `offset_ms` is how far into that file playback starts; `volume` is
            1.0 for as-recorded. A track whose file is missing is skipped.
        progress_cb: called with {"percent", "message", "stage"}.
        cancel_check: called between frames and during encoding; True stops.

    Returns a summary dict; `stopped` is True if the user cancelled.
    """
    if not frames:
        raise AnimaticError("This animatic has no frames yet — add some images first.")

    exe = ffmpeg_exe()  # fail early, before any work is done
    size = resolve_size(aspect_ratio, resolution)
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

    # --- 1. Work out what has to be drawn ----------------------------------
    # Frames whose image has gone are dropped FIRST, so every later calculation
    # works on the timeline that will actually be encoded.
    usable: list[dict] = []
    skipped: list[int] = []
    for i, frame in enumerate(frames):
        path = frame.get("path")
        if not path or not os.path.isfile(path):
            logger.warning("[animatic %s] frame %d has no image (%s) — skipped", job_id, i, path)
            skipped.append(i)
            continue
        usable.append(frame)

    if not usable:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise AnimaticError(
            "None of the frames could be read — their images may have been deleted."
        )

    segments, total_ms = plan_segments(
        usable, texts or [], end_ms, shapes or [], overlays or []
    )

    # --- 2. Render one PNG per segment -------------------------------------
    # A text clip can start or end part-way through a held image, so the unit of
    # rendering is a SEGMENT (a stretch where both the picture and the visible
    # text are constant), not a frame. With no text there is exactly one segment
    # per frame, so this costs nothing in the common case.
    entries: list[tuple[str, float]] = []
    rendered: dict[tuple, str] = {}  # (frame index, active text ids) → filename
    total = len(segments)

    for n, segment in enumerate(segments):
        if _cancelled():
            shutil.rmtree(build_dir, ignore_errors=True)
            return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}

        frame = usable[segment["frame"]]
        # The shape ids are part of the key: without them two segments differing
        # ONLY in which shapes are up would share one rendered still, and a
        # shape would appear or vanish at the wrong moment.
        key = (
            segment["frame"],
            tuple(t.get("id") for t in segment["texts"]),
            tuple(s.get("id") for s in segment.get("shapes") or ()),
            tuple(o.get("id") for o in segment.get("overlays") or ()),
        )
        name = rendered.get(key)

        if name is None:
            name = f"f{len(rendered):04d}.png"
            try:
                image = render_frame(
                    frame["path"],
                    size,
                    fit=fit,
                    background=background,
                    label=frame.get("label", "") if show_labels else "",
                    texts=segment["texts"],
                    shapes=segment.get("shapes") or [],
                    overlays=segment.get("overlays") or [],
                )
                image.save(os.path.join(build_dir, name), "PNG")
            except AnimaticError:
                raise
            except Exception as e:  # noqa: BLE001 — one bad file, not a dead export
                logger.warning(
                    "[animatic %s] segment %d (frame %d) unreadable (%s) — skipped",
                    job_id, n, segment["frame"], e,
                )
                continue
            rendered[key] = name

        entries.append((name, max(0.1, segment["duration_ms"] / 1000)))
        # Preparing frames is the first 55% — it's real work on big images.
        _report(int(55 * (n + 1) / total), f"Preparing frame {n + 1} of {total}", "frames")

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

    # Tracks whose file has gone are dropped rather than failing the export.
    tracks = [
        t for t in (audio_tracks or [])
        if t.get("path") and os.path.isfile(t["path"])
    ]
    has_audio = bool(tracks)

    cmd = [exe, "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
    cmd += ["-f", "concat", "-safe", "0", "-i", list_path]
    for track in tracks:
        # `-ss` BEFORE `-i` seeks the input, i.e. starts this far into the file.
        offset = max(0, int(track.get("offset_ms") or 0))
        if offset:
            cmd += ["-ss", f"{offset / 1000:.3f}"]
        # `-t` BEFORE `-i` limits how much of the input is read — this is the
        # clip's trimmed length, set by dragging its right edge.
        trim = track.get("trim_ms")
        if trim:
            cmd += ["-t", f"{max(100, int(trim)) / 1000:.3f}"]
        cmd += ["-i", track["path"]]

    # `fps=` FILTER, not just `-r`. The concat demuxer hands over a variable-rate
    # stream (one image, held for its declared duration), and `-r` alone does NOT
    # reliably expand those holds into real frames: a single 2s frame came out as
    # a 0.04s video, and a 14s sequence as 13.46s, depending on the exact
    # duration pattern. The fps filter resamples from the input TIMESTAMPS, which
    # is exact. `-r` is kept so the container is tagged with the same rate.
    volumes = [float(t.get("volume", 1.0) or 0.0) for t in tracks]
    needs_graph = len(tracks) > 1 or any(abs(v - 1.0) > 1e-3 for v in volumes)

    if not needs_graph:
        # One track at its recorded level (or none) — the plain path, unchanged.
        cmd += ["-vf", f"fps={fps}", "-map", "0:v:0"]
        if has_audio:
            cmd += ["-map", "1:a:0"]
    else:
        # Video goes through the same graph so ffmpeg never has to reconcile a
        # simple `-vf` with a complex one.
        parts = [f"[0:v]fps={fps}[vout]"]
        labels = []
        for i, volume in enumerate(volumes):
            parts.append(f"[{i + 1}:a]volume={volume:.3f}[a{i}]")
            labels.append(f"[a{i}]")
        if len(tracks) == 1:
            parts[-1] = f"[1:a]volume={volumes[0]:.3f}[aout]"
        else:
            # `normalize=0` is the important bit: amix divides every input by the
            # number of inputs by default, so a voiceover mixed over music would
            # come out at half the level the user set. We want the levels the
            # user chose, not an automatic average.
            parts.append(
                "".join(labels)
                + f"amix=inputs={len(tracks)}:duration=longest:normalize=0[aout]"
            )
        cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]"]

    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(_CRF.get((quality or "high").lower(), _CRF["high"])),
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
        "[animatic %s] encoding %d frame(s) in %d segment(s), %.1fs, %dx%d @%dfps%s",
        job_id, len(usable), len(entries), total_ms / 1000, size[0], size[1], fps,
        f" + {len(tracks)} audio track(s)" if has_audio else "",
    )

    def _enc_progress(fraction: float):
        _report(58 + int(40 * fraction), "Encoding video…")

    finished = run_ffmpeg(cmd, total_ms, _enc_progress, cancel_check)
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
        # Pictures in the finished cut — NOT the number of segments encoded, which
        # is higher whenever text starts or ends part-way through a held image.
        "frame_count": len(usable),
        "segment_count": len(entries),
        "text_count": len([t for t in (texts or []) if (t.get("text") or "").strip()]),
        "shape_count": len(shapes or []),
        "overlay_count": len(overlays or []),
        "skipped_frames": skipped,
        "width": size[0],
        "height": size[1],
        "fps": fps,
        "has_audio": has_audio,
        "audio_track_count": len(tracks),
        "size_bytes": size_bytes,
    }
