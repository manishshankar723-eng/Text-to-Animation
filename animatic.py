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

3. **There are two ways to plan the stills, and the project chooses.** If
   nothing in it is keyframed, `plan_segments` cuts the timeline into stretches
   where the picture holds still and renders one PNG per stretch — cheap, and
   exactly what this module has always done. If something IS keyframed, that
   trick no longer works (the picture changes continuously), so
   `plan_animated_segments` samples the scene at every video frame instead.
   Both hand the same segment shape to the same renderer, so only the planning
   differs. See `animatic_render.py` for the scene model both go through.
   A TRANSITION counts as movement for exactly the same reason a keyframe does
   — every frame of a dissolve is a different picture — so one anywhere in the
   project puts the whole export on the sampling planner.

Nothing here spends AI quota — an animatic is images, timing and audio.
"""

import logging
import math
import os
import re
import shutil
import subprocess
import threading

from PIL import Image, ImageDraw, ImageFont

import animatic_fonts
from animatic_effects import apply_effects, apply_mask, blend_onto
from animatic_render import (
    DEFAULT_BLEND,
    DEFAULT_MASK,
    FRAME_DEFAULTS,
    frame_spans,
    is_animated,
    place_picture,
    resolve_look,
    scene_at,
    scene_signature,
    text_place,
    value_at,
)

logger = logging.getLogger(__name__)

# An animated export renders one still per DISTINCT picture. Identical
# consecutive frames share one file, so a project that only moves here and there
# costs little — but a long timeline at a high frame rate genuinely is tens of
# thousands of PNGs, and filling the disk mid-export is a worse failure than
# refusing up front with a number the user can act on.
MAX_RENDERED_STILLS = 20_000


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
# ⚠ These three numbers are also in `client/src/styles/animatic-text.css` as
# `calc(100cqh / 30)` and friends. That is what makes the caption in the monitor
# the size of the caption in the MP4 — see `.an-screen { container-type: size }`.
_TEXT_DIVISOR = {"small": 30, "medium": 21, "large": 14}
# Text never runs edge to edge — this is the usable width, as a fraction.
_TEXT_WIDTH = 0.86
_LINE_SPACING = 1.28
# The frame height every "…px" measurement on a caption is quoted at. A stroke
# is given in pixels because that is how thick an outline is thought about, but
# the same project exports at 720p and at 4K, so the number is scaled by the
# real frame height. 1080 because that is the default `resolution`.
_TEXT_REFERENCE_HEIGHT = 1080
# A drop shadow's ink. Hard-edged and black at 55%, matching the `text-shadow`
# in `animatic-text.css` — which has a blur radius of 0 for exactly this reason.
# A blurred shadow in the browser and a hard one in Pillow is a preview that
# lies, and blurring in Pillow means a separate layer per caption per frame.
_SHADOW_ALPHA = 140


def _text_px(height: int, size_name: str) -> int:
    """The font size, in pixels, a caption of this size gets on this frame.

    Its own function because `stroke_px`, `shadow` and `letter_spacing` are all
    quoted as fractions of it — and because the browser computes the same number
    as `calc(100cqh / <divisor>)`, so there must be exactly one formula here to
    match against.
    """
    return max(14, int(height / _TEXT_DIVISOR.get(size_name, _TEXT_DIVISOR["medium"])))


def _text_font(height: int, size_name: str, font_id: str | None = None):
    """The face a caption is drawn in, at the right size for this frame.

    ⚠ LOADED FROM A BUNDLED FILE, never resolved by name. Both this and the
    browser open the same .ttf out of `client/public/fonts/`, addressed by the
    same id — see `animatic_fonts.py` for why a font looked up by name is the
    difference between a caption that wraps onto two lines in the monitor and
    three in the exported video.

    The old name-based chain survives only as a LAST RESORT for an install where
    the bundled files have gone missing: an ugly caption still beats an export
    that dies over a font.
    """
    px = _text_px(height, size_name)
    path = animatic_fonts.font_path(font_id)
    if path:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            logger.warning("bundled font %s could not be read", path)
    for name in _LABEL_FONTS:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def _line_width(draw, text: str, font, spacing: float = 0.0) -> float:
    """How wide one line is, with letter spacing taken into account.

    ⚠ MEASURED THE WAY IT IS DRAWN. With spacing on, the glyphs are placed one
    at a time (Pillow has no letter-spacing), so kerning between pairs is lost —
    measuring the string in one go would then report a width the line does not
    have and centre it wrongly. With spacing off this is the original single
    call, so an untouched caption measures exactly as it always did.

    The trailing gap after the LAST glyph is counted, because CSS
    `letter-spacing` counts it too. Both sides therefore shift a centred line
    left by half a space, which is invisible and, more importantly, identical.
    """
    if not spacing:
        return draw.textlength(text, font=font)
    return sum(draw.textlength(ch, font=font) for ch in text) + spacing * len(text)


def _draw_line(draw, xy, text: str, font, spacing: float = 0.0, **kwargs) -> None:
    """Draw one line, glyph by glyph when it is letter-spaced."""
    if not spacing:
        draw.text(xy, text, font=font, **kwargs)
        return
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, **kwargs)
        x += draw.textlength(ch, font=font) + spacing


def _wrap_text(draw, text: str, font, max_width: float, spacing: float = 0.0) -> list[str]:
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
            if _line_width(draw, trial, font, spacing) <= max_width:
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

    A clip's `opacity` scales the alpha of everything it draws — its backdrop,
    its ink and its outline — which is what makes a caption fade in rather than
    appear. Note that this fades the PARTS rather than the finished block, so a
    half-faded caption is a half-faded scrim with half-faded text over it, not
    the whole block at half strength. The two differ where the text overlaps its
    own backdrop; at the speed a caption fades, not visibly. Doing it properly
    means a separate RGBA layer per clip, which is a lot of allocation for a
    difference nobody can see.
    """
    if not clips:
        return
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas, "RGBA")
    max_width = width * _TEXT_WIDTH
    margin = height * 0.055

    # Measure everything first: a zone's block can only be placed once the total
    # height of every clip sharing that zone is known. A FREE clip is measured
    # by the same code and then placed on its own — it stacks with nothing,
    # because you put it where it is.
    blocks: dict[str, list[dict]] = {"top": [], "middle": [], "bottom": [], "free": []}
    for clip in clips:
        text = (clip.get("text") or "").strip()
        if not text:
            continue
        size_name = clip.get("size", "medium")
        font = _text_font(height, size_name, clip.get("font"))
        # Letter spacing and the shadow offset are fractions of the FONT SIZE,
        # so they scale with the frame like everything else here and `em` in the
        # browser is the same number with no conversion. Taken from `_text_px`
        # rather than off the font object, because the last-resort bitmap face
        # has no size to read.
        px = _text_px(height, size_name)
        spacing = float(clip.get("letter_spacing") or 0.0) * px
        lines = _wrap_text(draw, text, font, max_width, spacing)
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * _LINE_SPACING)
        pad = max(6, int(line_h * 0.28))
        widest = max((_line_width(draw, ln, font, spacing) for ln in lines), default=0)
        place = text_place(clip)
        position = clip.get("position", "bottom")
        if position not in blocks:
            position = "bottom"
        blocks["free" if place == "free" else position].append(
            {
                "clip": clip,
                "font": font,
                "px": px,
                "spacing": spacing,
                "lines": lines,
                "line_h": line_h,
                "pad": pad,
                "text_w": widest,
                "height": line_h * len(lines) + pad * 2,
            }
        )

    for zone, group in blocks.items():
        if not group:
            continue
        # A free block is placed by its own x/y; a flow block is stacked into
        # its zone, which is what stops two subtitles landing on each other.
        if zone == "free":
            for block in group:
                clip = block["clip"]
                box_w = min(width - margin, block["text_w"] + block["pad"] * 2)
                cx = float(clip.get("x", 0.5)) * width
                cy = float(clip.get("y", 0.85)) * height
                _draw_text_block(
                    draw, block, height,
                    box_x=cx - box_w / 2, box_w=box_w, top=cy - block["height"] / 2,
                )
            continue

        total = sum(b["height"] for b in group) + margin * 0.25 * (len(group) - 1)
        if zone == "top":
            y = margin
        elif zone == "middle":
            y = (height - total) / 2
        else:
            y = height - total - margin

        for block in group:
            clip = block["clip"]
            align = clip.get("align", "center")
            box_w = min(width - margin, block["text_w"] + block["pad"] * 2)
            if align == "left":
                box_x = margin
            elif align == "right":
                box_x = width - margin - box_w
            else:
                box_x = (width - box_w) / 2
            _draw_text_block(
                draw, block, height, box_x=box_x, box_w=box_w, top=y,
            )
            y += block["height"] + margin * 0.25


def _draw_text_block(
    draw, block: dict, height: int, box_x: float, box_w: float, top: float
) -> None:
    """Draw ONE measured caption at the box it has been given.

    Split out of `draw_texts` when free placement arrived: the two layouts
    differ only in where the box lands, and everything after that — the
    backdrop, the shadow, the outline, the ink, the alignment of each line
    inside the box — has to be identical or a caption would change appearance
    when you dragged it off its zone.
    """
    clip = block["clip"]
    font, lines, line_h, pad = block["font"], block["lines"], block["line_h"], block["pad"]
    spacing = block["spacing"]
    align = clip.get("align", "center")
    ink = _parse_colour(clip.get("color", "#ffffff"))
    backdrop = clip.get("backdrop", "scrim")
    # Resolved by `scene_at` when the clip is keyframed, otherwise the clip's
    # own value, otherwise fully opaque — which is every caption written before
    # fades existed.
    op = max(0.0, min(1.0, float(clip.get("opacity", 1.0) or 0.0)))
    if op <= 0:
        return

    def _a(value: int) -> int:
        return max(0, min(255, int(round(value * op))))

    # An explicit outline, in pixels at 1080p and scaled to this frame — so the
    # same project outlines its captions identically at 720p and at 4K.
    stroke = int(round(
        float(clip.get("stroke_px") or 0.0) * height / _TEXT_REFERENCE_HEIGHT
    ))
    stroke_ink = _parse_colour(clip.get("stroke_color", "#000000"))
    # With no backdrop the text sits straight on the art, which for a pale
    # storyboard thumbnail can be white-on-white — so it always gets a dark
    # outline in that mode. An explicit stroke overrides it rather than adding
    # to it: two outlines is one outline.
    if backdrop == "none" and stroke <= 0:
        stroke, stroke_ink = max(2, line_h // 14), (0, 0, 0)
        stroke_alpha = 210
    else:
        stroke_alpha = 255
    stroke_kwargs = (
        {"stroke_width": stroke, "stroke_fill": (*stroke_ink, _a(stroke_alpha))}
        if stroke > 0
        else {}
    )
    # A hard-edged drop shadow, offset by a fraction of the font size. Hard
    # rather than blurred so the `text-shadow` in the browser can be the same
    # picture with a blur radius of 0 — see _SHADOW_ALPHA.
    shadow = float(clip.get("shadow") or 0.0) * block["px"]

    if backdrop in ("scrim", "box"):
        alpha = 140 if backdrop == "scrim" else 225
        draw.rounded_rectangle(
            [box_x, top, box_x + box_w, top + block["height"]],
            radius=max(4, int(pad * 0.6)),
            fill=(0, 0, 0, _a(alpha)),
        )

    ty = top + pad
    for line in lines:
        line_w = _line_width(draw, line, font, spacing)
        if align == "left":
            tx = box_x + pad
        elif align == "right":
            tx = box_x + box_w - pad - line_w
        else:
            tx = box_x + (box_w - line_w) / 2
        if shadow > 0:
            # Cast from the OUTLINED glyph, not the bare one, or a stroked
            # caption's shadow reads as a second, thinner caption behind it.
            _draw_line(
                draw, (tx + shadow, ty + shadow), line, font, spacing,
                fill=(0, 0, 0, _a(_SHADOW_ALPHA)), **stroke_kwargs,
            )
        _draw_line(
            draw, (tx, ty), line, font, spacing, fill=(*ink, _a(255)), **stroke_kwargs
        )
        ty += line_h


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


def draw_overlays(canvas: Image.Image, overlays: list[dict]) -> Image.Image:
    """Composite the overlay PICTURES that are on screen for this moment.

    Geometry is identical to a shape's — fractions of the frame, `x`/`y` the
    centre — because they are placed with the same handles in the editor. The
    difference is only the fill: a file rather than a colour.

    Each picture is fitted INSIDE its box preserving aspect ratio ("contain"),
    so a logo dropped into a square box isn't stretched into a different logo.
    A file that has gone is skipped: one missing overlay must not kill a whole
    export.

    ⚠ RETURNS THE CANVAS rather than drawing into the one it was given. An
    overlay carries its own effects, mask and BLEND MODE, and a blend is a
    function of the pixels already underneath — there is no paste that expresses
    "multiply this logo into the shot". An overlay with none of the three still
    takes the old paste path, so nothing that predates this costs an allocation.
    """
    if not overlays:
        return canvas
    width, height = canvas.size

    for item in overlays:
        opacity = float(item.get("opacity", 1.0) or 0.0)
        path = item.get("path")
        if opacity <= 0 or not path or not os.path.isfile(path):
            continue
        look = _look_of(item)
        box_w = max(1, min(int(round(abs(float(item.get("w", 0.3))) * width)), width * 3))
        box_h = max(1, min(int(round(abs(float(item.get("h", 0.3))) * height)), height * 3))

        try:
            with Image.open(path) as source:
                picture = source.convert("RGBA")
        except OSError:
            logger.warning("[animatic] overlay image unreadable (%s) — skipped", path)
            continue

        # Graded at the overlay's OWN resolution, before it is scaled into its
        # box — the same rule a frame follows, and the reason a logo's chroma
        # key doesn't change when the box is dragged bigger.
        picture = apply_effects(picture, look.get("effects"))

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
        at = (int(round(cx - picture.width / 2)), int(round(cy - picture.height / 2)))

        blend = look.get("blend") or DEFAULT_BLEND
        masked = (look.get("mask") or {}).get("kind", "none") != "none"
        if blend == DEFAULT_BLEND and not masked:
            # The path every overlay written before this takes, byte for byte.
            canvas.paste(picture, at, picture)
            continue

        # A mask is in FRAME coordinates and a blend needs the whole backdrop,
        # so both want the overlay promoted to a full-frame layer first.
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        layer.paste(picture, at, picture)
        canvas = blend_onto(canvas, apply_mask(layer, look.get("mask")), blend)

    return canvas


def _look_of(clip: dict | None) -> dict:
    """The effects / mask / blend to draw this clip with — READ, never resolved.

    ⚠ EVERYTHING THAT REACHES HERE HAS ALREADY BEEN RESOLVED, by whichever
    planner ran: `plan_animated_segments` off `scene_at`, `plan_segments` through
    `_resolved_look` at t=0. That is deliberate and it is not a detail. Resolving
    HERE would be wrong for an animated clip — the keyframe tracks are still on
    it, so a second pass at t=0 would throw away the values the planner just
    worked out at the real time and freeze every grade at its first key.
    """
    clip = clip or {}
    return {
        "effects": clip.get("effects") or [],
        "mask": clip.get("mask") or dict(DEFAULT_MASK),
        "blend": clip.get("blend") or DEFAULT_BLEND,
    }


def _resolved_look(clip: dict | None) -> dict:
    """A RAW clip's look, resolved — the fast planner's half of the pair.

    ⚠ `resolve_look(clip, 0)`, not "read the stored values". A LONE keyframe is
    not an animation (`is_animated` needs two), so a clip with one key on a LUT's
    amount takes the fast planner — and reading the stored value would then
    ignore that key, while the monitor honours it everywhere. `value_at` returns
    a single key's value at any time, so asking at 0 is asking correctly.
    """
    return resolve_look(clip or {}, 0)


def _static_transform(clip: dict | None) -> dict:
    """A raw clip's pan / zoom / fade, for the planner that doesn't resolve.

    ⚠ THIS IS A FIX, not just plumbing. `plan_segments` never produced a
    transform, so a frame with a stored `scale` of 1.5 and no keyframes exported
    at 1.0 while the Program monitor showed it at 1.5 — the preview lying about
    the export, in the one direction nothing was checking.

    Through `value_at` rather than off the clip, for the same reason
    `_resolved_look` is: one key is not an animation, so a clip carrying a single
    `scale` key is planned as static and would otherwise lose it.
    """
    clip = clip or {}
    return {
        prop: value_at(clip, prop, 0, fallback)
        for prop, fallback in FRAME_DEFAULTS.items()
    }


def _flatten(layer: Image.Image, size: tuple[int, int], background: str, blend: str) -> Image.Image:
    """One finished RGBA layer, composited onto the bar colour. Always RGB out."""
    canvas = Image.new("RGB", size, _parse_colour(background))
    return blend_onto(canvas, layer, blend)


def _picture_layer(
    source: dict | str,
    size: tuple[int, int],
    fit: str,
    transform: dict | None,
    look: dict | None = None,
) -> Image.Image:
    """ONE clip's picture, fitted onto its own full-frame canvas.

    `source` says what to draw, and is one of:

        {"path": "…png"}    a still — an image clip, or the extracted frame of a
                            video clip that `build_animatic` looked up for this
                            exact moment. By the time it reaches here a video is
                            a PNG like any other, which is the whole reason
                            `video_frames.py` exists.
        {"color": "#rrggbb"} a colour card. No file at all, so no fit and no
                            pan/zoom apply — a flat colour has no edges to place.

    A bare string is still accepted and means `{"path": …}`, so any caller
    written before clips existed keeps working.

    "contain" letterboxes the picture (nothing is lost — the default, because a
    storyboard frame you cropped is a frame you can't read); "cover" scales up
    and centre-crops so the frame is filled edge to edge.

    `transform` is the picture's OWN pan/zoom/fade on top of that — the resolved
    `scale`/`x`/`y`/`opacity` from `scene_at`. Absent, or at its defaults, the
    result is byte-for-byte what this has always produced.

    `look` is the resolved effects and mask. ⚠ THEY APPLY IN DIFFERENT PLACES
    AND THE ORDER IS THE POINT: the effect chain runs on the SOURCE PICTURE,
    before it is fitted, so a letterboxed shot doesn't have its bars graded and
    a chroma key sees the pixels the camera recorded; the mask runs afterwards
    in FRAME coordinates, because a vignette is a region of the film you are
    making rather than of the file you fed in. The blend mode is NOT applied
    here — that is between this finished layer and whatever is under it, and is
    the caller's business.

    RETURNS RGBA, transparent everywhere the picture isn't. It used to return
    the picture already sitting on the bar colour; a chroma key and a feathered
    mask both need real alpha to survive as far as the composite, and flattening
    the result over the background reproduces the old bytes exactly.

    Split out of `render_frame` so a transition can build TWO of these and blend
    them. Both pictures then get the identical fit, background and rounding,
    which they must: a dissolve between two pictures placed by two slightly
    different calculations would shimmer at the edges.
    """
    if isinstance(source, str):
        source = {"path": source}
    target_w, target_h = size
    empty = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))

    tf = transform or {}
    scale = float(tf.get("scale", 1.0) or 1.0)
    origin_x = float(tf.get("x", 0.5))
    origin_y = float(tf.get("y", 0.5))
    alpha = float(tf.get("opacity", 1.0))
    look = look or {}
    if alpha <= 0:
        return empty

    colour = source.get("color")
    if colour:
        # A colour card fills the frame edge to edge. `fit`, `scale` and the pan
        # are deliberately ignored: there is no picture to letterbox or crop, and
        # a "zoomed" flat colour is the same flat colour. Its opacity still
        # applies, so a card can be faded to the bar colour like any other clip —
        # which is what makes a colour card usable as a dip or a flash.
        card = Image.new("RGBA", size, (*_parse_colour(colour), 255))
        return _finish_layer(card, alpha, look)

    src_path = source.get("path")
    if not src_path:
        # A video clip whose extracted still is missing for this instant. Empty
        # is the honest answer and it cannot fail an export — the same
        # forgiveness a deleted panel gets — and it flattens to the bar colour
        # exactly as it always did.
        return empty

    with Image.open(src_path) as im:
        # ⚠ STILL `convert("RGB")`, not RGBA. A source PNG's own transparency has
        # never shown the backdrop through a frame — it came out black — and
        # honouring it now would silently change every animatic that has ever
        # used a cut-out still. Alpha on a frame is what the chroma key is for.
        im = im.convert("RGB")
        sw, sh = im.size
        if sw <= 0 or sh <= 0:
            raise AnimaticError(f"'{os.path.basename(src_path)}' has no pixels.")
        # Graded BEFORE fitting — see the note above — and therefore at the
        # source's own resolution, which is also the cheaper end to do it at for
        # anything being scaled down.
        picture = apply_effects(im.convert("RGBA"), look.get("effects"))
        # The fit, the zoom and the pan are one calculation — doing them in
        # sequence would round twice and drift the picture by a pixel per step.
        new, left, top = place_picture(picture, size, fit, scale, origin_x, origin_y)

    layer = empty.copy()
    layer.paste(new, (left, top))
    return _finish_layer(layer, alpha, look, effects_done=True)


def _finish_layer(
    layer: Image.Image,
    alpha: float,
    look: dict,
    effects_done: bool = False,
) -> Image.Image:
    """Grade (if it hasn't been), fade, then mask — the tail every layer shares."""
    if not effects_done:
        layer = apply_effects(layer, look.get("effects"))
    if alpha < 1.0:
        # Scale the EXISTING alpha rather than replacing it, so a chroma key or
        # a source cut-out stays cut out when the clip is faded.
        layer.putalpha(layer.getchannel("A").point(lambda a: int(a * alpha)))
    # LAST, and in frame coordinates: the mask is a region of the finished
    # picture, not of the file. That is what lets one be keyframed to sweep
    # across a shot while the shot itself is panning underneath it.
    return apply_mask(layer, look.get("mask"))


def _picture_canvas(
    source: dict | str,
    size: tuple[int, int],
    fit: str,
    background: str,
    transform: dict | None,
    look: dict | None = None,
) -> Image.Image:
    """One clip's picture as a finished RGB frame — layer, then blend mode.

    The blend happens HERE rather than in `_picture_layer` because it is a
    question about two things: this picture and whatever is under it. Under the
    base picture there is only the bar colour, so "multiply" on a frame darkens
    it toward the backdrop — which is exactly what the monitor shows, since the
    canvas is cleared to the same colour before anything is drawn.
    """
    layer = _picture_layer(source, size, fit, transform, look)
    return _flatten(layer, size, background, (look or {}).get("blend") or DEFAULT_BLEND)


def _faded(transform: dict | None, factor: float) -> dict:
    """A copy of a transform with its opacity scaled — how a dip fades a shot."""
    tf = dict(transform or {})
    tf["opacity"] = float(tf.get("opacity", 1.0)) * max(0.0, min(1.0, factor))
    return tf


def _transition_canvas(
    source: dict | str,
    source_b: dict | str,
    size: tuple[int, int],
    fit: str,
    background: str,
    transform: dict | None,
    transform_b: dict | None,
    kind: str,
    mix: float,
    look: dict | None = None,
    look_b: dict | None = None,
) -> Image.Image:
    """The two pictures of a transition, composited at `mix` (0 → 1).

    0 is the outgoing picture alone, 1 is the incoming one alone. Every kind is
    defined so that those two ends hold, which is what makes a transition
    invisible at its own edges and so lets it straddle the cut without anything
    appearing to jump.

    ⚠ Each kind here has a counterpart in the Program monitor, now in
    `ProgramCanvas`/`compositor.js` rather than in CSS. They are matched by
    construction — the same fractions, the same direction — because a preview
    that dissolves where the export wipes is worse than having no preview.

    ⚠ THE INCOMING PICTURE IS COMPOSITED OVER THE OUTGOING ONE, not blended with
    it. This CLOSES the known limit the DOM monitor carried: it composited
    against what was behind, while this function used to fit each picture onto
    the bar colour and blend the two results. The two agreed only while both
    pictures were fully opaque — the moment one was faded by its own keyframes
    mid-transition, or (now) carried a chroma key or a mask, the preview showed
    the shot underneath where the export showed the backdrop.
    Compositing is also the more useful of the two readings: a caption keyed out
    of the arriving shot should reveal the shot it is arriving over, not black.
    For two opaque pictures — which is every clip that doesn't say otherwise —
    it is byte-for-byte what this produced before.
    """
    width, height = size
    m = max(0.0, min(1.0, float(mix)))
    blend_b = (look_b or {}).get("blend") or DEFAULT_BLEND

    if kind == "dip":
        # NOT a two-picture blend: the shot goes out through the bar colour and
        # the next one comes up from it, so only ever ONE picture is on screen.
        # That is what makes a dip read as a beat rather than a cross-fade.
        if m < 0.5:
            return _picture_canvas(
                source, size, fit, background, _faded(transform, 1 - 2 * m), look
            )
        return _picture_canvas(
            source_b, size, fit, background, _faded(transform_b, 2 * m - 1), look_b
        )

    # Each picture is graded and masked ON ITS OWN before the two meet. A
    # dissolve between an ungraded shot and a graded one then dissolves the two
    # LOOKS, which is what every NLE shows — grading the blended result instead
    # would make one clip's LUT bleed onto its neighbour.
    b_layer = _picture_layer(source_b, size, fit, transform_b, look_b)

    if kind == "slide":
        # A push: the outgoing picture is driven off to the left while the
        # incoming one comes in behind it. Both move, which is what separates a
        # push from a cover — and the background shows through neither, because
        # together they are always exactly one frame wide.
        offset = int(round(width * m))
        a_layer = _picture_layer(source, size, fit, transform, look)
        out = Image.new("RGB", size, _parse_colour(background))
        out = blend_onto(out, _shifted(a_layer, -offset), (look or {}).get("blend") or DEFAULT_BLEND)
        return blend_onto(out, _shifted(b_layer, width - offset), blend_b)

    base = _picture_canvas(source, size, fit, background, transform, look)

    if kind == "wipe":
        # A hard edge travelling left → right, revealing the incoming picture.
        edge = int(round(width * m))
        if edge <= 0:
            return base
        revealed = Image.new("RGBA", size, (0, 0, 0, 0))
        revealed.paste(b_layer.crop((0, 0, edge, height)), (0, 0))
        return blend_onto(base, revealed, blend_b)

    # dissolve, and anything unrecognised — but `transition_window` has already
    # folded unknown kinds down to this one, so both sides fall back together.
    return blend_onto(base, _faded_layer(b_layer, m), blend_b)


def _faded_layer(layer: Image.Image, factor: float) -> Image.Image:
    """A copy of an RGBA layer with its alpha scaled — a dissolve's other half."""
    factor = max(0.0, min(1.0, float(factor)))
    if factor >= 1.0:
        return layer
    out = layer.copy()
    out.putalpha(out.getchannel("A").point(lambda a: int(a * factor)))
    return out


def _shifted(layer: Image.Image, dx: int) -> Image.Image:
    """An RGBA layer moved sideways on its own full-frame canvas — for a slide."""
    if dx == 0:
        return layer
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(layer, (dx, 0))
    return out


def render_frame(
    source: dict | str,
    size: tuple[int, int],
    fit: str = "contain",
    background: str = "#000000",
    label: str = "",
    texts: list[dict] | None = None,
    shapes: list[dict] | None = None,
    overlays: list[dict] | None = None,
    transform: dict | None = None,
    picture_b: dict | str | None = None,
    transform_b: dict | None = None,
    transition: str | None = None,
    mix: float = 0.0,
    look: dict | None = None,
    look_b: dict | None = None,
) -> Image.Image:
    """One video frame: the picture (or two, mid-transition) plus every layer.

    `source` (and `picture_b`) is what to draw — see `_picture_layer` for the
    shape. A bare path string still works and means a still, which is what every
    caller written before video clips existed passes.

    `picture_b` / `transition` / `mix` are the transition, resolved by
    `scene_at`. With no second picture this is exactly what it always was, which
    is the whole of every animatic written before transitions existed.

    `look` / `look_b` are the resolved effects, mask and blend mode of the two
    pictures. Absent — which is every animatic written before this — nothing is
    graded and the result is byte-for-byte what it was.

    Shapes, overlays and text are drawn ON TOP of the finished transition, not
    into it — a caption spanning a cut stays legible all the way through rather
    than dissolving with the picture underneath it.
    """
    if picture_b and transition:
        canvas = _transition_canvas(
            source, picture_b, size, fit, background,
            transform, transform_b, transition, mix,
            look, look_b,
        )
    else:
        canvas = _picture_canvas(source, size, fit, background, transform, look)
    # Stacking order, bottom to top: picture → shapes → overlay images → text.
    # Shapes sit under the overlays because a shape is usually a highlight or a
    # mask ON the art, while an overlay is a picture element (a logo, an inset)
    # that belongs above it. Text is last so a caption is always readable.
    if shapes:
        draw_shapes(canvas, shapes)
    if overlays:
        # ⚠ REBOUND, not drawn in place. An overlay with a blend mode cannot be
        # pasted — the mode is a function of the pixels underneath it — so the
        # composite comes back as a new image. Every other layer is still drawn
        # in place, which is why only this one is an assignment.
        canvas = draw_overlays(canvas, overlays)
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
    def _timed(items, look: bool = False) -> list[tuple[int, int, dict]]:
        out: list[tuple[int, int, dict]] = []
        for item in items or []:
            if float(item.get("opacity", 1.0) or 0.0) <= 0:
                continue
            start = max(0, int(item.get("start_ms") or 0))
            end = start + max(100, int(item.get("duration_ms") or 0))
            if start >= total_ms:
                continue
            # An OVERLAY carries a look, and this is the one place on the fast
            # path where it can be resolved — after which `draw_overlays` reads
            # the same shape whichever planner ran, and `_look_of` never has to
            # guess which it is looking at.
            out.append((start, min(end, total_ms), {**item, **_resolved_look(item)} if look else item))
        return out

    figures = _timed(shapes)
    pictures = _timed(overlays, look=True)

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


def plan_animated_segments(
    frames: list[dict],
    texts: list[dict],
    end_ms: int | None = None,
    shapes: list[dict] | None = None,
    overlays: list[dict] | None = None,
    fps: int = 24,
    transitions: list[dict] | None = None,
) -> tuple[list[dict], int]:
    """`plan_segments`, for a project where something MOVES.

    Cutting the timeline where clips start and end only works while the picture
    between those cuts holds still. A keyframed property doesn't, so the scene is
    sampled at every video frame instead and each sample becomes its own
    one-frame segment.

    That sounds ruinous and mostly isn't: consecutive samples that resolve to the
    same picture carry the same `signature`, and `build_animatic` renders one
    still per distinct signature. A three-second push inside a two-minute
    animatic costs three seconds' worth of stills, not two minutes'.

    Returns the same (segments, total_ms) shape `plan_segments` does, so the
    renderer downstream cannot tell which planner ran — with two additions per
    segment: `signature` (the render cache key) and `transform` (the picture's
    resolved pan/zoom/fade). A segment inside a TRANSITION carries three more:
    `frame_b` (the picture arriving, as an index into `frames`), `transform_b`
    and `mix`.

    Note that transitions cannot change `total_ms`: they are boundary-local, so
    the sampling grid below is the same one an untransitioned project gets.
    """
    project = {
        "frames": frames,
        "texts": texts or [],
        "shapes": shapes or [],
        "overlays": overlays or [],
        "transitions": transitions or [],
    }
    _spans, total_ms = frame_spans(frames, end_ms)
    if total_ms <= 0:
        return [], 0

    fps = max(1, min(60, int(fps or 24)))
    count = max(1, int(round(total_ms * fps / 1000)))

    segments: list[dict] = []
    for n in range(count):
        # Boundaries are computed from the frame INDEX rather than accumulated,
        # so rounding cannot drift over a long timeline and the last segment
        # lands exactly on total_ms.
        start = n * total_ms / count
        end = (n + 1) * total_ms / count
        scene = scene_at(project, start, end_ms)
        if scene["frame"] is None:
            continue
        segment = {
            "frame": scene["frame"]["index"],
            "start_ms": start,
            "duration_ms": end - start,
            "texts": scene["texts"],
            "shapes": scene["shapes"],
            "overlays": scene["overlays"],
            "signature": scene_signature(scene),
            "transform": _transform_of(scene["frame"]),
            # WHICH MOMENT OF THE SOURCE FILE this sample shows, for a video
            # clip; None for a still or a colour card. Carried on the segment
            # rather than recomputed downstream so there is exactly one place
            # that answers it — `source_at` — and the still the export draws is
            # provably the one the preview drew.
            "source_ms": scene["frame"].get("source_ms"),
            # The grade, resolved at this instant. Carried rather than looked up
            # downstream for the same reason `source_ms` is: one place answers
            # "what does this clip look like now", and it is the place the
            # monitor asked too.
            "look": _look_of(scene["frame"]),
        }
        # Mid-transition: the second picture rides along on the segment, so the
        # renderer downstream still only ever looks at one dict.
        if scene["frame_b"] is not None:
            segment["frame_b"] = scene["frame_b"]["index"]
            segment["transform_b"] = _transform_of(scene["frame_b"])
            segment["transition"] = scene["transition"]
            segment["mix"] = scene["mix"]
            segment["source_ms_b"] = scene["frame_b"].get("source_ms")
            segment["look_b"] = _look_of(scene["frame_b"])
        segments.append(segment)
    return segments, total_ms


def _transform_of(picture: dict) -> dict:
    """The four values `render_frame` needs off a resolved picture."""
    return {
        "scale": picture["scale"],
        "x": picture["x"],
        "y": picture["y"],
        "opacity": picture["opacity"],
    }


# ---------------------------------------------------------------------------
# Clips: what a segment is actually a picture OF
# ---------------------------------------------------------------------------
def source_window(clip: dict) -> tuple[int, int]:
    """The stretch of the source file a video clip will show: (start_ms, span_ms).

    Derived from `source_at`, never recomputed by hand, so the range extracted
    is exactly the range the scene model will ask for. Getting these two out of
    step is how you extract 3 seconds and then look up frame 4 — which reads as
    a video that freezes part way through, not as a missing file.

    The last moment shown is `source_at(clip, duration - 1)`, because a clip is
    alive up to but NOT including its end. A frame's worth of headroom is added
    so rounding at the tail can never leave the final still unwritten.
    """
    from animatic_render import source_at

    in_ms = max(0, int(clip.get("in_ms") or 0))
    length = max(100, int(clip.get("duration_ms") or 2000))
    last = source_at(clip, length - 1)
    if last is None:
        return in_ms, 0
    return in_ms, max(1, int(round(last - in_ms)) + 1)


def _source_for(clip: dict, source_ms: float | None) -> dict:
    """What `render_frame` should draw for this clip at this instant.

    The one place the three kinds diverge. An image clip is its file; a colour
    card is a colour; a video clip is whichever extracted still covers
    `source_ms` — which is why extraction has to have happened first.
    """
    from animatic_render import DEFAULT_CLIP_COLOR, clip_kind

    kind = clip_kind(clip)
    if kind == "color":
        return {"color": clip.get("color") or DEFAULT_CLIP_COLOR}
    if kind == "video":
        # Imported locally: video_frames imports THIS module for ffmpeg_exe and
        # run_ffmpeg, so a module-level import here would be a cycle.
        import video_frames

        info = clip.get("_stills")
        return {"path": video_frames.frame_path(info, source_ms or 0) if info else None}
    return {"path": clip.get("path")}


# ---------------------------------------------------------------------------
# The mix: levels, fades, tone, ducking
# ---------------------------------------------------------------------------
# ⚠ TWIN FILE: `client/src/animatic/audio_mix.js`. `track_play_ms`, `fade_window`
# and `EQ_BANDS` are written twice, in the same shape and with the same clamps,
# because the editor has to fade the preview exactly where the encoder fades the
# export — a track that ramps out over the last two seconds in the monitor and
# over the last four in the MP4 is the audio version of a preview that lies.
# `tests/audio_mix_check.py` runs the JS half through node and compares the two
# window for window, band for band; if you change a number here, change it there.

# --- Tone -------------------------------------------------------------------
# THREE FIXED BANDS, not a parametric EQ, and the reason is the twin. Each band
# below is one RBJ cookbook biquad, which is EXACTLY what a `BiquadFilterNode`
# is in the browser — so the same three numbers make the same three filters on
# both sides and the editor sounds like the export. A parametric EQ would put
# the frequency and the Q in the project too, and every one of them would be
# another number that has to mean the same thing in two filter implementations.
#
# ⚠ THE WIDTHS ARE STATED, NOT DEFAULTED. ffmpeg's `bass`/`treble` default to
# `t=q:w=0.5`, WebAudio's shelves are cookbook shelves with a SLOPE of 1, and
# the two are different filters. `t=s:w=1` is what makes them the same one.
EQ_BANDS = (
    {"id": "low", "field": "eq_low", "hz": 120, "q": 1.0},
    {"id": "mid", "field": "eq_mid", "hz": 1000, "q": 1.0},
    {"id": "high", "field": "eq_high", "hz": 6000, "q": 1.0},
)
# Below this a band is treated as untouched — half a decibel is inaudible, and
# a project full of 0.0000001s must not force every track through three filters.
_EQ_EPSILON = 0.05


def eq_gains(track: dict) -> list[float]:
    """The three band gains in dB, in `EQ_BANDS` order. All zero = untouched."""
    return [float(track.get(band["field"]) or 0.0) for band in EQ_BANDS]


def eq_chain(track: dict) -> list[str]:
    """The ffmpeg filters for this track's tone — empty when it has none.

    `bass` and `treble` are the cookbook low/high shelves and `equalizer` is the
    cookbook peaking filter, which is the same set `BiquadFilterNode` offers.
    A band at 0 dB is left out rather than passed through at unity: it is the
    same audio either way, and a chain of three no-op biquads on every track is
    work the encoder doesn't need to do.
    """
    out: list[str] = []
    for band, gain in zip(EQ_BANDS, eq_gains(track)):
        if abs(gain) < _EQ_EPSILON:
            continue
        if band["id"] == "low":
            out.append(f"bass=g={gain:.2f}:f={band['hz']}:t=s:w=1")
        elif band["id"] == "high":
            out.append(f"treble=g={gain:.2f}:f={band['hz']}:t=s:w=1")
        else:
            out.append(f"equalizer=f={band['hz']}:t=q:w={band['q']:.2f}:g={gain:.2f}")
    return out

# A duck is a COMPRESSOR keyed off the voice, so how far the music actually
# drops depends on how loud the voice is at that instant — there is no ratio
# that means "exactly −10 dB" for every take. `duck_to` is therefore aimed at a
# nominal speech level and the compressor does the rest: further down on a
# shouted line, less on a whispered one, which is what a duck is for.
_DUCK_THRESHOLD = 0.03      # ≈ −30 dBFS — quiet enough that any real speech opens it
_DUCK_NOMINAL_DB = 12.0     # how far a normal voice sits above that threshold
_DUCK_MAX_RATIO = 20.0      # sidechaincompress's own ceiling
_DUCK_ATTACK_MS = 20        # fast enough not to swallow the first word
_DUCK_RELEASE_MS = 400      # slow enough not to pump between words
# Every chain in a ducked graph is pinned to one format. sidechaincompress needs
# its two inputs to match, and a 44.1kHz music bed keyed off a 48kHz voiceover is
# the ordinary case, not an exotic one.
_DUCK_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def track_play_ms(track: dict, total_ms: int = 0) -> int:
    """How long this track is HEARD for, in milliseconds.

    Its trim if it has one, otherwise whatever is left of the file after
    `offset_ms` — and never longer than the video, because the export is cut at
    `total_ms` and a fade placed past that is a fade nobody hears.

    A track whose `duration_ms` never reached us (the browser measures it, so a
    project saved by something else may not carry one) is assumed to play to the
    end of the video. That is the only assumption available without an ffprobe,
    and it is the right one: it is what an untrimmed track does.
    """
    trim = track.get("trim_ms")
    if trim:
        play = max(0, int(trim))
    else:
        duration = int(track.get("duration_ms") or 0)
        play = max(0, duration - max(0, int(track.get("offset_ms") or 0))) if duration else 0
    if play <= 0:
        play = max(0, int(total_ms or 0))
    if total_ms:
        play = min(play, int(total_ms))
    return play


def fade_window(track: dict, total_ms: int = 0) -> tuple[int, int, int]:
    """(fade_in_ms, fade_out_start_ms, fade_out_ms) for one track.

    The fade OUT is placed against the moment the track stops being heard, not
    against the length of the file — that is what makes a fade land on the end
    of the music whether the music was trimmed, or cut short by the video.

    Two fades longer than the track itself would cross each other and cancel, so
    they are scaled down together, keeping their ratio. Same rule as a
    transition never eating more than half a picture, and for the same reason.
    """
    play = track_play_ms(track, total_ms)
    fade_in = max(0, int(track.get("fade_in_ms") or 0))
    fade_out = max(0, int(track.get("fade_out_ms") or 0))
    if play <= 0 or (not fade_in and not fade_out):
        return 0, play, 0
    fade_in, fade_out = min(fade_in, play), min(fade_out, play)
    if fade_in + fade_out > play:
        scale = play / (fade_in + fade_out)
        fade_in, fade_out = int(fade_in * scale), int(fade_out * scale)
    return fade_in, max(0, play - fade_out), fade_out


def duck_ratio(duck_to: float) -> float:
    """The compressor ratio that pulls a track down to roughly `duck_to`.

    Gain reduction is `(level − threshold) × (1 − 1/ratio)`, so at the nominal
    speech level above this ratio delivers the requested depth. Deeper than the
    nominal headroom allows is simply the hardest ratio there is.
    """
    gain = max(0.001, min(1.0, float(duck_to)))
    if gain >= 1.0:
        return 1.0
    wanted_db = -20 * math.log10(gain)
    share = wanted_db / _DUCK_NOMINAL_DB
    if share >= 1.0:
        return _DUCK_MAX_RATIO
    return max(1.0, min(_DUCK_MAX_RATIO, 1.0 / (1.0 - share)))


def _duck_pairs(tracks: list[dict]) -> list[tuple[int, int]]:
    """(ducked index, voice index) for every track that ducks under a voice.

    ⚠ WHICH TRACK IS THE VOICE IS STATED, NEVER GUESSED. `duck_target` names it
    outright; failing that it is the first track whose `role` is "voice". With
    neither, nothing ducks — "the other one" is the wrong answer the first time
    someone lays two music beds, and a mix that quietly ducks the wrong track is
    harder to diagnose than one that doesn't duck at all.
    """
    voices = [i for i, t in enumerate(tracks) if (t.get("role") or "") == "voice"]
    by_id = {t.get("upload_id"): i for i, t in enumerate(tracks) if t.get("upload_id")}
    sources: set[int] = set()  # filled below, to reject two-tier ducking

    pairs: list[tuple[int, int]] = []
    for i, track in enumerate(tracks):
        if float(track.get("duck_to", 1.0) or 1.0) >= 1.0:
            continue
        target = track.get("duck_target") or ""
        voice = by_id.get(target) if target in by_id else (voices[0] if voices else None)
        if voice is None or voice == i:
            continue
        pairs.append((i, voice))
        sources.add(voice)

    # A track cannot be both ducked and the thing something else ducks under:
    # the sidechain would then be keyed off a signal that is itself moving, and
    # nobody has ever asked for that. The duck ON it is what gets dropped.
    kept = [(i, v) for (i, v) in pairs if i not in sources]
    for i, _v in pairs:
        if i in sources:
            logger.warning(
                "[animatic] track %d is a duck source, so its own duck is ignored", i
            )
    return kept


def audio_graph(tracks: list[dict], total_ms: int = 0) -> tuple[list[str], str] | None:
    """The audio half of the `filter_complex`, or None if plain mapping will do.

    Returns (parts, out_label): the filter chains, and the pad the encoder should
    map as its audio. The caller adds the video part — the two only share a graph
    so that ffmpeg never has to reconcile a simple `-vf` with a complex one.

    None means "nothing here needs a graph", which is one track at its recorded
    level with no shape on it — the path every animatic took before fades and
    ducking existed, and the one that is already proven.
    """
    if not tracks:
        return None
    volumes = [float(t.get("volume", 1.0) or 0.0) for t in tracks]
    windows = [fade_window(t, total_ms) for t in tracks]
    tones = [eq_chain(t) for t in tracks]
    ducks = _duck_pairs(tracks)
    plain = (
        len(tracks) == 1
        and abs(volumes[0] - 1.0) <= 1e-3
        and not windows[0][0]
        and not windows[0][2]
        and not tones[0]
        and not ducks
    )
    if plain:
        return None

    parts: list[str] = []
    for i, track in enumerate(tracks):
        # ⚠ TONE BEFORE LEVEL, and both before the fades. A shelf is a filter with
        # gain in it, so running it after the fader would make the same EQ boost a
        # different amount of headroom at every volume — and running it after a
        # fade would put a filter's ringing on top of a ramp that is meant to
        # reach silence. It is also the order the preview's graph is wired in.
        chain = [*tones[i], f"volume={volumes[i]:.3f}"]
        fade_in, out_at, fade_out = windows[i]
        if fade_in:
            chain.append(f"afade=t=in:st=0:d={fade_in / 1000:.3f}")
        if fade_out:
            chain.append(f"afade=t=out:st={out_at / 1000:.3f}:d={fade_out / 1000:.3f}")
        if ducks:
            chain.append(_DUCK_FORMAT)
        parts.append(f"[{i + 1}:a]" + ",".join(chain) + f"[a{i}]")

    labels = [f"[a{i}]" for i in range(len(tracks))]

    # A voice that something ducks under is needed TWICE — once as itself in the
    # mix, once as the key. `asplit` is the only way to use a pad twice.
    taps: dict[int, int] = {}
    for _i, voice in ducks:
        taps[voice] = taps.get(voice, 0) + 1
    for voice, count in taps.items():
        pads = f"[v{voice}m]" + "".join(f"[v{voice}s{k}]" for k in range(count))
        parts.append(f"{labels[voice]}asplit={count + 1}{pads}")
        labels[voice] = f"[v{voice}m]"

    used: dict[int, int] = {}
    for i, voice in ducks:
        k = used.get(voice, 0)
        used[voice] = k + 1
        ratio = duck_ratio(tracks[i].get("duck_to", 1.0))
        parts.append(
            f"{labels[i]}[v{voice}s{k}]sidechaincompress="
            f"threshold={_DUCK_THRESHOLD}:ratio={ratio:.2f}"
            f":attack={_DUCK_ATTACK_MS}:release={_DUCK_RELEASE_MS}[d{i}]"
        )
        labels[i] = f"[d{i}]"

    if len(labels) == 1:
        return parts, labels[0]
    # `normalize=0` is the important bit: amix divides every input by the number
    # of inputs by default, so a voiceover mixed over music would come out at
    # half the level the user set. We want the levels the user chose, not an
    # automatic average.
    parts.append(
        "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0[aout]"
    )
    return parts, "[aout]"


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
    transitions: list[dict] | None = None,
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
        frames: the CLIPS, in play order. Every one carries `duration_ms` (its
            length on the TIMELINE) and `label`; what else it carries depends on
            its `kind`:
              "image" (the default) — `path`, one still, held.
              "video" — `video_path` plus the source window `in_ms` / `out_ms`
                  and `speed`. The file is torn into stills before anything is
                  drawn; see `source_window` and video_frames.py.
              "color" — `color`, a flat card. No file, so it can never be
                  missing and is never skipped.
            A clip whose file is missing is SKIPPED (a panel may have been
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
        transitions: [{"id", "after_frame_id", "kind", "duration_ms"}] — what
            happens ON a cut. BOUNDARY-LOCAL: the blend straddles the cut,
            taking d/2 from the tail of one picture and the head of the next, so
            the encoded video is exactly as long as it would be without them.
            One naming a frame that isn't there, or the last frame, is inert.
        audio_tracks: [{"path", "offset_ms", "trim_ms", "volume", …}] laid under
            the sequence and MIXED together — music under a voiceover is the
            usual pair. `offset_ms` is how far into that file playback starts;
            `volume` is 1.0 for as-recorded. `fade_in_ms` / `fade_out_ms` shape
            each end, and `duck_to` + `role`/`duck_target` pull a bed down under
            the voice — all of that is `audio_graph`'s business. A track whose
            file is missing is skipped.
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
    # A clip is dropped for a missing FILE, and each kind has a different file
    # (or none): an image clip has its still, a video clip its source video, and
    # a colour card nothing at all, so it can never be missing.
    from animatic_render import clip_kind

    usable: list[dict] = []
    skipped: list[int] = []
    for i, frame in enumerate(frames):
        kind = clip_kind(frame)
        if kind == "color":
            usable.append(frame)
            continue
        path = frame.get("video_path") if kind == "video" else frame.get("path")
        if not path or not os.path.isfile(path):
            logger.warning(
                "[animatic %s] %s clip %d has no file (%s) — skipped", job_id, kind, i, path
            )
            skipped.append(i)
            continue
        usable.append(frame)

    if not usable:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise AnimaticError(
            "None of the frames could be read — their images may have been deleted."
        )

    # Which planner: see this module's docstring. `is_animated` errs toward True
    # — being wrong the other way would silently drop every animation from the
    # MP4 while the preview showed it, which is the one failure mode that would
    # make the editor untrustworthy.
    project = {
        "frames": usable,
        "texts": texts or [],
        "shapes": shapes or [],
        "overlays": overlays or [],
        "transitions": transitions or [],
    }
    animated = is_animated(project)
    if animated:
        segments, total_ms = plan_animated_segments(
            usable, texts or [], end_ms, shapes or [], overlays or [], fps,
            transitions or [],
        )
        distinct = len({s["signature"] for s in segments})
        if distinct > MAX_RENDERED_STILLS:
            shutil.rmtree(build_dir, ignore_errors=True)
            raise AnimaticError(
                f"This animatic would need {distinct:,} rendered frames, which is more "
                f"than the {MAX_RENDERED_STILLS:,} an export can hold. Lower the frame "
                "rate, shorten the timeline, or remove some of the animation."
            )
        logger.info(
            "[animatic %s] animated: %d sample(s) at %dfps → %d distinct still(s)",
            job_id, len(segments), fps, distinct,
        )
    else:
        segments, total_ms = plan_segments(
            usable, texts or [], end_ms, shapes or [], overlays or []
        )

    # --- 1b. Tear any video clips into stills ------------------------------
    # Pillow cannot decode video, so every video clip becomes numbered PNGs
    # before the renderer sees it. Only the SOURCE RANGE each clip actually
    # shows is extracted, and the result is cached by content — so a second
    # export of an unchanged project skips this step entirely.
    #
    # ⚠ This happens AFTER the planner has run and BEFORE anything is drawn: the
    # planner is what decides which source moments will be asked for, and the
    # renderer is what asks for them.
    videos = [f for f in usable if clip_kind(f) == "video"]
    if videos:
        import video_frames

        cache_root = os.path.join(output_dir, "_animatics", job_id, "_stills")
        for n, clip in enumerate(videos):
            if _cancelled():
                shutil.rmtree(build_dir, ignore_errors=True)
                return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}
            start_ms, span_ms = source_window(clip)
            _report(
                int(14 * n / len(videos)),
                f"Reading video {n + 1} of {len(videos)}…",
                "extracting",
            )
            info = video_frames.extract_frames(
                clip["video_path"], fps, cache_root,
                start_ms=start_ms, span_ms=span_ms,
                cancel_check=cancel_check,
            )
            if info.get("stopped"):
                shutil.rmtree(build_dir, ignore_errors=True)
                logger.info("[animatic %s] export STOPPED while reading video", job_id)
                return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}
            # Hung on the clip so `_source_for` can look a moment up without
            # threading a second structure through the whole render loop.
            clip["_stills"] = info
        logger.info(
            "[animatic %s] %d video clip(s) → %d still(s) total",
            job_id, len(videos), sum(v["_stills"]["count"] for v in videos),
        )

    # --- 2. Render one PNG per segment -------------------------------------
    # A text clip can start or end part-way through a held image, so the unit of
    # rendering is a SEGMENT (a stretch where both the picture and the visible
    # text are constant), not a frame. With no text there is exactly one segment
    # per frame, so this costs nothing in the common case.
    entries: list[tuple[str, float]] = []
    rendered: dict[object, str] = {}  # cache key → filename
    total = len(segments)

    for n, segment in enumerate(segments):
        if _cancelled():
            shutil.rmtree(build_dir, ignore_errors=True)
            return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}

        frame = usable[segment["frame"]]
        # The shape ids are part of the key: without them two segments differing
        # ONLY in which shapes are up would share one rendered still, and a
        # shape would appear or vanish at the wrong moment.
        #
        # An animated segment brings its own key instead. Ids alone would be
        # wrong there — two samples one video frame apart hold exactly the same
        # clips and differ only in the values those clips resolved to, so an
        # id-based key would reuse the first still for the whole animation and
        # nothing would move.
        key = segment.get("signature") or (
            segment["frame"],
            tuple(t.get("id") for t in segment["texts"]),
            tuple(s.get("id") for s in segment.get("shapes") or ()),
            tuple(o.get("id") for o in segment.get("overlays") or ()),
        )
        name = rendered.get(key)

        if name is None:
            name = f"f{len(rendered):04d}.png"
            # Mid-transition the segment names a SECOND picture to blend with.
            # It indexes `usable` like `frame` does, so a frame dropped for a
            # missing image can't be picked up here either.
            index_b = segment.get("frame_b")
            try:
                image = render_frame(
                    # WHAT this clip is a picture of at this instant: its own
                    # still, its colour, or the extracted frame of its source
                    # video covering `source_ms`. See `_source_for`.
                    _source_for(frame, segment.get("source_ms")),
                    size,
                    fit=fit,
                    background=background,
                    label=frame.get("label", "") if show_labels else "",
                    texts=segment["texts"],
                    shapes=segment.get("shapes") or [],
                    overlays=segment.get("overlays") or [],
                    # ⚠ `or _static_transform(frame)`, and the same for the
                    # look. `plan_segments` — the planner a project with no
                    # animation gets — produces neither, so a STORED zoom or a
                    # STORED grade would be dropped from the MP4 while the
                    # monitor showed it. Falling back to the clip's own values
                    # makes the two agree whichever planner ran.
                    transform=segment.get("transform") or _static_transform(frame),
                    look=segment.get("look") or _resolved_look(frame),
                    picture_b=(
                        _source_for(usable[index_b], segment.get("source_ms_b"))
                        if index_b is not None
                        else None
                    ),
                    transform_b=segment.get("transform_b"),
                    transition=segment.get("transition"),
                    mix=segment.get("mix") or 0.0,
                    look_b=segment.get("look_b"),
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

        # ⚠ The floor is ONE VIDEO FRAME, not 0.1s as it used to be. A segment
        # shorter than 1/fps cannot be shown at all, so that is the smallest
        # meaningful duration — but at 24fps an animated segment IS 1/24s, and
        # the old floor would have stretched every one of them to 100ms, making
        # the export run 2.4× long. It was also quietly wrong before this: a
        # 40–99ms segment (which `plan_segments` can produce) was already being
        # padded out to 100ms and lengthening the video.
        entries.append((name, max(1.0 / fps, segment["duration_ms"] / 1000)))
        # Preparing frames is the first 55% — it's real work on big images. With
        # video clips the first 15 went on tearing them into stills, so this
        # picks up from there; with none it is the 0–55 it has always been.
        base = 15 if videos else 0
        _report(
            base + int((55 - base) * (n + 1) / total),
            f"Preparing frame {n + 1} of {total}",
            "frames",
        )

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
    # Levels, fades and ducking are one graph, built (and unit-tested) in
    # `audio_graph`. None back from it means nothing on this mix needs a filter
    # at all — one track at its recorded level, the plain path, unchanged.
    graph = audio_graph(tracks, total_ms)

    if graph is None:
        cmd += ["-vf", f"fps={fps}", "-map", "0:v:0"]
        if has_audio:
            cmd += ["-map", "1:a:0"]
    else:
        # Video goes through the same graph so ffmpeg never has to reconcile a
        # simple `-vf` with a complex one.
        audio_parts, out_label = graph
        parts = [f"[0:v]fps={fps}[vout]", *audio_parts]
        cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", out_label]

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
        # Which planner ran. Worth reporting: an animated export renders far more
        # stills and takes correspondingly longer, and when someone asks why an
        # export that used to take 20 seconds now takes three minutes, this is
        # the answer.
        "animated": animated,
        "still_count": len(rendered),
        "text_count": len([t for t in (texts or []) if (t.get("text") or "").strip()]),
        "shape_count": len(shapes or []),
        "overlay_count": len(overlays or []),
        "transition_count": len(transitions or []),
        # How many clips were video, and how many stills came out of them.
        # Worth reporting for the same reason `animated` is: it is the answer to
        # "why did this export take so much longer than the last one".
        "video_clip_count": len(videos),
        "extracted_still_count": sum(v["_stills"]["count"] for v in videos),
        "skipped_frames": skipped,
        "width": size[0],
        "height": size[1],
        "fps": fps,
        "has_audio": has_audio,
        "audio_track_count": len(tracks),
        "size_bytes": size_bytes,
    }
