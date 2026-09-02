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

4. **The stills are drawn ACROSS PROCESSES, and the plan comes first.** Which
   stills exist and what each is called is decided in one pass; drawing them is
   a second pass that can happen in any order, because no still depends on any
   other. That split is the whole reason a pool is safe here: a parallel export
   and a serial one write the same files under the same names and therefore
   encode to the same bytes, which `tests/export_perf_check.py` asserts by
   hashing both. See `_render_all_stills` — and `_detached_main` for why this
   does not require every caller to guard its entry point.

5. **An export is not always an MP4.** `container` is 'mp4', 'gif' or 'png';
   the presets in `export_presets.py` are what choose one. A PNG never reaches
   ffmpeg at all — the composite Pillow just made IS the file — which is what
   makes a poster frame provably the same picture the video shows.

Nothing here spends AI quota — an animatic is images, timing and audio.
"""

import logging
import math
import multiprocessing
import os
import re
import shutil
import subprocess
import threading

from PIL import Image, ImageDraw, ImageFont

import animatic_fonts
import export_presets
from animatic_effects import apply_effects, apply_mask, blend_onto
from animatic_transitions import apply_matte
from animatic_render import (
    DEFAULT_BLEND,
    DEFAULT_MASK,
    FRAME_DEFAULTS,
    frame_spans,
    is_animated,
    # WHAT DRAWS OVER WHAT — one z-scale for every visual row, so a row can be
    # dragged up or down the timeline. ⚠ TWIN of client/src/animatic/lane_order.js.
    layer_runs,
    ordered_layers,
    place_picture,
    resolve_look,
    scene_at,
    scene_signature,
    box_size,
    text_backdrop,
    text_place,
    transition_matte,
    value_at,
)

logger = logging.getLogger(__name__)

# An animated export renders one still per DISTINCT picture. Identical
# consecutive frames share one file, so a project that only moves here and there
# costs little — but a long timeline at a high frame rate genuinely is tens of
# thousands of PNGs, and filling the disk mid-export is a worse failure than
# refusing up front with a number the user can act on.
MAX_RENDERED_STILLS = 20_000

# --- Rendering the stills in parallel ---------------------------------------
# Compositing a still is pure CPU on one picture and knows nothing about the
# stills either side of it, so the loop is embarrassingly parallel. It was
# single-threaded until Phase 8, which on a 200-segment project meant one core
# busy and the rest idle for minutes.
#
# Below this many DISTINCT stills the pool is not worth starting: on Windows
# every worker is a fresh interpreter that has to import Pillow, which costs
# more than a few dozen composites.
_POOL_MIN_STILLS = 48
# `ANIMATIC_EXPORT_WORKERS=1` forces the old serial loop — the escape hatch for
# a machine where the pool misbehaves, and what the parity half of
# `tests/export_perf_check.py` sets to get a serial export to compare against.
_ENV_WORKERS = "ANIMATIC_EXPORT_WORKERS"
# One core is left for the parent (which is draining results and writing
# progress) and for the rest of the server, which is still serving requests.
_MAX_WORKERS = 8


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


def _clamp(value, low: float, high: float, fallback: float) -> float:
    """A caption's number, or `fallback` when the clip hasn't got one.

    ⚠ MISSING AND UNREADABLE BOTH FALL BACK, and that is the whole point: every
    field added to a caption after it shipped is absent from every animatic
    saved before, so "not there" has to mean "the number this code used to
    hard-code" — not 0, which for a line height or a wrap width is not a
    picture. Pydantic already validates the range on the way in; this is the
    same guard for the export path, which is also fed raw dicts by the tests.
    """
    if value is None:
        return fallback
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return fallback


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
# ⚠ BOTH ARE DEFAULTS NOW, not constants: a caption carries its own `wrap` and
# `line_height`, and these are what a clip that hasn't got one is drawn at —
# i.e. every caption saved before those fields existed. `_LINE_SPACING`
# multiplies (ascent + descent); the browser's `line-height` multiplies the FONT
# SIZE, which is a different number, and `line_ratio` on the font list is what
# converts between them. See `animatic_fonts.py`.
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


def _text_px(height: int, size_name: str, size_px: float = 0.0) -> int:
    """The font size, in pixels, a caption of this size gets on this frame.

    Its own function because `stroke_px`, `shadow`, `letter_spacing` and the
    backdrop's corners and padding are all quoted as fractions of it — and
    because the browser computes the same number as `calc(100cqh / <divisor>)`,
    so there must be exactly one formula here to match against.

    `size_px` OVERRIDES the S/M/L preset when it is set. It is quoted at 1080p
    and scaled by the real frame height, exactly like `stroke_px`, so a title
    set at 120 is the same fraction of the frame at 720p and at 4K. 0 means "use
    the preset", which is every caption written before the field existed.
    """
    if size_px and size_px > 0:
        return max(8, int(round(size_px * height / _TEXT_REFERENCE_HEIGHT)))
    return max(14, int(height / _TEXT_DIVISOR.get(size_name, _TEXT_DIVISOR["medium"])))


# The cases a caption can be set in. ⚠ TWIN of `TEXT_CASES` in
# `TextProperties.jsx`, and each entry is the CSS `text-transform` it has to
# match: "upper" is `uppercase`, "title" is `capitalize`. Applied BEFORE the
# text is wrapped, because the browser wraps the transformed glyphs too — do it
# after and a line that fits here breaks there.
_TEXT_CASES = ("none", "upper", "lower", "title")


def _apply_case(text: str, case: str | None) -> str:
    """`text` in the case this caption is set in. Mirrors `text-transform`.

    "title" is CSS `capitalize`, which upper-cases the first letter of every
    word and LEAVES THE REST ALONE — deliberately not `str.title()`, which also
    lower-cases the rest and would turn "NASA" into "Nasa" in the MP4 while the
    monitor kept it shouting.
    """
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return re.sub(r"(^|\s)(\S)", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


def _text_font(height: int, size_name: str, font_id: str | None = None, size_px: float = 0.0):
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
    px = _text_px(height, size_name, size_px)
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
        # The case is applied HERE, before the wrap — see `_apply_case`. Every
        # measurement below is therefore made on the glyphs that get drawn.
        text = _apply_case(text, clip.get("text_case"))
        size_name = clip.get("size", "medium")
        size_px = float(clip.get("size_px") or 0.0)
        # ⚠ A CAPTION MAY BE ZOOMED, AND THE WRAP IS MEASURED AT ITS RESTING SIZE.
        # `scale` is an animatable text property (see `ANIMATABLE` in
        # animatic_render.py) and it multiplies the FONT — glyphs, leading,
        # padding, outline and the backdrop box all follow, because every one of
        # them is already a fraction of `px`. But the LINE BREAKS are taken with
        # the resting font on purpose: re-wrapping as it grows would throw a word
        # onto a new line part way through the move and back again on the way
        # out. The browser gets the same result from a CSS transform, which
        # scales the laid-out block and cannot reflow it — see `captionStyle`.
        scale = float(clip.get("scale") or 1.0)
        if not (0.05 <= scale <= 20.0):
            scale = 1.0
        rest_font = _text_font(height, size_name, clip.get("font"), size_px)
        font = (
            rest_font
            if abs(scale - 1.0) < 1e-6
            else _text_font(height, size_name, clip.get("font"),
                            _text_px(height, size_name, size_px) * scale)
        )
        # Letter spacing, the shadow offset and the backdrop's corners and
        # padding are fractions of the FONT SIZE, so they scale with the frame
        # like everything else here and `em` in the browser is the same number
        # with no conversion. Taken from `_text_px` rather than off the font
        # object, because the last-resort bitmap face has no size to read.
        px = _text_px(height, size_name, size_px) * scale
        spacing = float(clip.get("letter_spacing") or 0.0) * px
        # How wide this caption may get before it wraps, as a fraction of the
        # frame. Per-clip now rather than the one `_TEXT_WIDTH` for everything,
        # which is still what an untouched caption asks for.
        max_width = width * _clamp(clip.get("wrap"), 0.1, 1.0, _TEXT_WIDTH)
        # ⚠ THE RESTING FONT AND THE RESTING SPACING — see the note above. Wrapping
        # with the scaled ones is what makes a zoom re-flow.
        lines = _wrap_text(
            draw, text, rest_font,
            max_width,
            float(clip.get("letter_spacing") or 0.0) * _text_px(height, size_name, size_px),
        )
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * _clamp(clip.get("line_height"), 0.6, 3.0, _LINE_SPACING))
        # ⚠ THE 6px FLOOR SCALES WITH THE MULTIPLIER. It is there so a tiny
        # caption still gets a backdrop you can see; left as a hard floor it
        # would also mean "padding: 0" quietly wasn't, on exactly the small text
        # someone asks for it on. At the default 1.0 this is the original
        # `max(6, line_h * 0.28)`.
        pad_mult = _clamp(clip.get("backdrop_pad"), 0.0, 4.0, 1.0)
        pad = int(max(6 * min(pad_mult, 1.0), line_h * 0.28 * pad_mult))
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
                # The automatic outline the "none" backdrop draws, measured at
                # the DEFAULT leading. ⚠ It used to be `line_h // 14`, which was
                # the same thing until leading became a setting — after that,
                # opening up the line spacing quietly thickened the outline, and
                # an outline is a property of the FACE, not of the gap between
                # lines. (The browser has always agreed: `.bd-none` is a flat
                # 0.055em of the font size.)
                "auto_stroke": max(2, int((ascent + descent) * _LINE_SPACING) // 14),
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
    # ⚠ FOLDED, not read raw — see `text_backdrop`. A kind this build doesn't
    # know becomes a scrim on BOTH sides rather than silently drawing nothing
    # here and something in the monitor.
    backdrop = text_backdrop(clip)
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
    # "Outline only" puts the text straight on the art, which for a pale
    # storyboard thumbnail can be white-on-white — so it always gets a dark
    # outline in that mode. An explicit stroke overrides it rather than adding
    # to it: two outlines is one outline.
    # ⚠ "plain" IS NOT "none" AND MUST NOT GET THIS. It is the setting for
    # exactly the caption that wants no furniture at all — no bar, no outline,
    # just the letters — and an automatic outline is furniture. It falls through
    # to the `else` and draws bare glyphs, unreadable art and all.
    if backdrop == "none" and stroke <= 0:
        stroke, stroke_ink = block["auto_stroke"], (0, 0, 0)
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
    #
    # ⚠ THE √2 IS WHAT MAKES THE ANGLE FREE WITHOUT MOVING OLD CAPTIONS. Before
    # there was an angle the offset was (shadow, shadow) — one `shadow` down AND
    # one right — so the DISTANCE it was cast at was always `shadow · √2`. That
    # distance is what the angle now rotates, which means 45° (the default, and
    # what every caption saved before this carries) lands back on exactly
    # (shadow, shadow), and every other angle throws it just as far.
    shadow_dist = float(clip.get("shadow") or 0.0) * block["px"] * math.sqrt(2)
    shadow_rad = math.radians(_clamp(clip.get("shadow_angle"), 0.0, 360.0, 45.0))
    shadow_dx = shadow_dist * math.cos(shadow_rad)
    shadow_dy = shadow_dist * math.sin(shadow_rad)
    shadow_ink = _parse_colour(clip.get("shadow_color", "#000000"))
    shadow_alpha = int(round(_clamp(clip.get("shadow_opacity"), 0.0, 1.0, _SHADOW_ALPHA / 255) * 255))

    if backdrop in ("scrim", "box"):
        # The KIND still chooses the strength unless the clip names one — a
        # scrim is a bar you read through, a box is one you don't.
        default_alpha = 140 if backdrop == "scrim" else 225
        explicit = clip.get("backdrop_opacity")
        alpha = (
            default_alpha
            if explicit is None
            else int(round(_clamp(explicit, 0.0, 1.0, 1.0) * 255))
        )
        fill = _parse_colour(clip.get("backdrop_color", "#000000"))
        # ⚠ THE CORNER IS IN `em` ON BOTH SIDES NOW. It used to be `pad * 0.6`
        # here and a flat `0.25em` in the stylesheet, which at 1080p is 10px
        # against 13 — a caption whose corners were visibly rounder in the
        # monitor than in the MP4. One number, one meaning: a quarter of the
        # font size, which is what the browser was already drawing.
        # ⚠ CLAMPED TO HALF THE SHORTER SIDE. Pillow refuses a radius bigger
        # than that ("radius should be less than or equal to half the smallest
        # side"), and 2em on a one-line caption is bigger than that — an export
        # must not die because someone dragged the corners all the way round.
        # A fully-clamped radius is a stadium, which is what "as round as it
        # goes" should look like anyway.
        radius = _clamp(clip.get("backdrop_radius"), 0.0, 2.0, 0.25) * block["px"]
        radius = max(0, int(min(radius, box_w / 2, block["height"] / 2)))
        draw.rounded_rectangle(
            [box_x, top, box_x + box_w, top + block["height"]],
            radius=radius,
            fill=(*fill, _a(alpha)),
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
        if shadow_dist > 0 and shadow_alpha > 0:
            # Cast from the OUTLINED glyph, not the bare one, or a stroked
            # caption's shadow reads as a second, thinner caption behind it.
            _draw_line(
                draw, (tx + shadow_dx, ty + shadow_dy), line, font, spacing,
                fill=(*shadow_ink, _a(shadow_alpha)), **stroke_kwargs,
            )
        _draw_line(
            draw, (tx, ty), line, font, spacing, fill=(*ink, _a(255)), **stroke_kwargs
        )
        ty += line_h


# --- Shapes -----------------------------------------------------------------
# Each shape is points on the UNIT SQUARE (0–1), scaled into its box — so one
# list draws at 25% of a 720p frame and at 25% of a 4K one.
#
# ⚠ THIS IS THE EXPORTER'S COPY OF A TABLE THE BROWSER ALSO HOLDS, in
# `client/src/animatic/shape_points.js` — the preview clips a div with these
# points and the monitor fans triangles from them. The duplication is forced (the
# exporter runs with no JS at all), but it is no longer unguarded:
# `tests/shape_points_check.py` loads the JS module under node and compares every
# point of every shape against this file. The builders below are that module's
# functions, mirrored line for line; change one, change both, and let the test
# say whether you got it right.
#
# ⚠ EVERY SHAPE IS STAR-SHAPED ABOUT ITS CENTRE, and that is a requirement rather
# than a coincidence: the Program monitor triangulates with a fan anchored at
# (0.5, 0.5), so an outline the centre cannot see all of draws correctly HERE and
# wrongly THERE. The same test proves it for every entry. It is also why there is
# no ring and no crescent — a hole cannot be one fan.
#
# 'ellipse' has no point list: Pillow draws a true one (see `draw_shapes`).
_TAU = math.pi * 2


def _r6(v: float) -> float:
    """Round to six decimals — the SAME expression the JS side uses.

    ⚠ NOT `round(v, 6)`. Python rounds halves to even and JavaScript's
    `Math.round` rounds them up, and this table is compared across the two
    languages point by point. `floor(v * 1e6 + 0.5)` means one thing in both.
    """
    return math.floor(v * 1_000_000 + 0.5) / 1_000_000


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop repeated points, including a last one that repeats the first.

    Arcs that meet land on the same coordinate twice, and a zero-length edge is a
    degenerate triangle in the monitor's fan and a wasted point in a clip-path.
    """
    out: list[tuple[float, float]] = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out


def _fit(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Stretch a point list so its bounding box IS the unit square."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    sx = 1.0 / (x1 - x0) if x1 > x0 else 1.0
    sy = 1.0 / (y1 - y0) if y1 > y0 else 1.0
    return _dedupe([(_r6((x - x0) * sx), _r6((y - y0) * sy)) for x, y in points])


def _fit_centred(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Scale about the CENTRE instead, keeping (0.5, 0.5) where it is.

    ⚠ THE FAN-SAFE FIT, and the concave shapes need it. A five-petal flower is not
    symmetric top-to-bottom, so stretching its BOX to the square slides the middle
    of the flower off centre — and for a shape with deep valleys, a centre that
    has drifted into a valley wall is exactly the fan that draws wrong.
    """
    ex = max(abs(x - 0.5) for x, _ in points)
    ey = max(abs(y - 0.5) for _, y in points)
    sx = 0.5 / ex if ex > 0 else 1.0
    sy = 0.5 / ey if ey > 0 else 1.0
    return _dedupe([(_r6(0.5 + (x - 0.5) * sx), _r6(0.5 + (y - 0.5) * sy)) for x, y in points])


def _polar(steps, radius, phase=-math.pi / 2):
    """`steps` points around the centre, at whatever `radius(t, i)` returns.

    `t` runs 0→1 once round and `i` is the step, because a star wants the INDEX
    (odd steps are its inner points) while a flower wants the angle. Radius is a
    fraction of half the box, so 1 touches the edge; the phase starts at the TOP,
    like the pentagon that was here first.
    """
    out = []
    for i in range(steps):
        t = i / steps
        a = phase + t * _TAU
        r = radius(t, i)
        out.append((0.5 + math.cos(a) * 0.5 * r, 0.5 + math.sin(a) * 0.5 * r))
    return out


def _poly(n, phase=-math.pi / 2):
    """A regular n-gon. Convex, so the plain box fit is safe."""
    return _fit(_polar(n, lambda t, i: 1.0, phase))


def _star_poly(n, inner, phase=-math.pi / 2):
    """n tips at the edge, n valleys at `inner`. Concave → centred fit."""
    return _fit_centred(_polar(n * 2, lambda t, i: 1.0 if i % 2 == 0 else inner, phase))


def _flower(n, inner, sharp=0.55, per=24):
    """n petals: `inner` is how deep the valleys cut, `sharp` how pointed."""
    return _fit_centred(
        _polar(n * per, lambda t, i: inner + (1 - inner) * abs(math.cos(math.pi * n * t)) ** sharp)
    )


def _scallop(n, depth=0.12, per=16):
    """A circle with a bitten edge — n shallow scallops."""
    return _fit_centred(
        _polar(n * per, lambda t, i: 1 - depth * (1 - abs(math.cos(math.pi * n * t))))
    )


def _cog(teeth, inner=0.72, duty=0.55):
    """A gear: square teeth rather than points, so it reads as machined.

    ⚠ ITS TOOTH FLANKS ARE RADIAL, so the monitor's fan triangle on those edges is
    DEGENERATE — zero area, drawn as nothing, correct on purpose. It is the one
    shape whose fan-safety margin is zero rather than positive.
    """
    out = []
    step = 1.0 / teeth
    for i in range(teeth):
        base = i * step
        for t, r in (
            (base, 1.0),
            (base + step * duty, 1.0),
            (base + step * duty, inner),
            (base + step, inner),
        ):
            a = -math.pi / 2 + t * _TAU
            out.append((0.5 + math.cos(a) * 0.5 * r, 0.5 + math.sin(a) * 0.5 * r))
    return _fit_centred(out)


def _blob():
    """A soft pebble. Whole harmonics only, or the loop would not close."""
    def radius(t, i):
        a = t * _TAU
        return (
            1
            - 0.1 * math.sin(a * 3 + 0.7)
            - 0.06 * math.sin(a * 5 + 2.1)
            - 0.03 * math.sin(a * 2)
        )

    return _fit_centred(_polar(96, radius))


def _arc(cx, cy, rx, ry, a0, a1, steps):
    """`steps` segments of an ellipse arc, endpoints included."""
    out = []
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * i / steps
        out.append((cx + math.cos(a) * rx, cy + math.sin(a) * ry))
    return out


def _quad(p0, p1, p2, steps):
    """A quadratic curve, sampled — the heart's and the shield's flanks."""
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append((
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ))
    return out


def _rounded(r, steps=8):
    """A box with corners of radius `r`, as a fraction of the box."""
    k = min(r, 0.5)
    pts = list(_arc(k, k, k, k, math.pi, math.pi * 1.5, steps))
    pts += _arc(1 - k, k, k, k, math.pi * 1.5, _TAU, steps)
    pts += _arc(1 - k, 1 - k, k, k, 0, math.pi / 2, steps)
    pts += _arc(k, 1 - k, k, k, math.pi / 2, math.pi, steps)
    return _fit(pts)


def _arch(steps=16):
    """A doorway: semicircular top, straight sides, flat foot."""
    return _fit(list(_arc(0.5, 0.5, 0.5, 0.5, math.pi, _TAU, steps)) + [(1.0, 1.0), (0.0, 1.0)])


def _half_circle(steps=32):
    """A dome — the top half of the box's ellipse, flat side down."""
    return _fit(list(_arc(0.5, 1.0, 0.5, 1.0, math.pi, _TAU, steps)) + [(1.0, 1.0)])


def _quarter_circle(steps=24):
    """A quarter round, its corner at bottom-left."""
    return _fit([(0.0, 1.0)] + list(_arc(0.0, 1.0, 1.0, 1.0, -math.pi / 2, 0, steps)))


def _pac(steps=40, mouth=50.0):
    """A disc with a wedge taken out of it.

    ⚠ ITS APEX IS THE FAN'S CENTRE — the one shape whose outline touches (0.5,
    0.5) rather than surrounding it. Still star-shaped (the apex sees
    everything), which is why a mouth is possible here and a ring is not.
    """
    a0 = math.radians(mouth / 2)
    return _fit([(0.5, 0.5)] + list(_arc(0.5, 0.5, 0.5, 0.5, a0, _TAU - a0, steps)))


def _heart(steps=14):
    """Two lobes and two flanks meeting at a point.

    Built from where the lobes INTERSECT rather than from a formula, so the notch
    is a real vertex at a known height instead of wherever a polar curve happened
    to dip — which is what decides whether the centre can see it.
    """
    r, cy, flank = 0.32, 0.30, 0.62
    rx = 1.0 - r
    dx = rx - 0.5
    dy = math.sqrt(max(r * r - dx * dx, 0.0))
    notch = math.atan2(-dy, -dx)
    foot = (rx + math.cos(flank) * r, cy + math.sin(flank) * r)
    pts = list(_arc(rx, cy, r, r, notch, flank, steps))
    pts += _quad(foot, (0.86, 0.80), (0.5, 1.0), steps)
    pts += _quad((0.5, 1.0), (0.14, 0.80), (1 - foot[0], foot[1]), steps)
    pts += _arc(1 - rx, cy, r, r, math.pi - flank, _TAU + math.atan2(-dy, dx), steps)
    return _fit(pts)


def _drop(steps=32):
    """A teardrop: point up, weight at the bottom."""
    return _fit(
        [(0.5, 0.0)] + list(_arc(0.5, 0.66, 0.5, 0.34, -math.pi * 0.36, math.pi * 1.36, steps))
    )


def _leaf(steps=18):
    """Two curves meeting at opposite corners of the box."""
    pts = list(_quad((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), steps))
    pts += _quad((1.0, 1.0), (0.0, 1.0), (0.0, 0.0), steps)
    return _fit(pts)


def _shield(steps=14):
    """Flat shoulders, flanks that fall to a point."""
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 0.42)]
    pts += _quad((1.0, 0.42), (0.96, 0.88), (0.5, 1.0), steps)
    pts += _quad((0.5, 1.0), (0.04, 0.88), (0.0, 0.42), steps)
    return _fit(pts)


def _plus(arm=0.32):
    """A plus. `arm` is the bar's thickness as a fraction of the box."""
    a, b = arm, 1 - arm
    return [
        (a, 0.0), (b, 0.0), (b, a), (1.0, a), (1.0, b), (b, b),
        (b, 1.0), (a, 1.0), (a, b), (0.0, b), (0.0, a), (a, a),
    ]


def _cross(arm=0.3):
    """The same plus, turned 45° and re-fitted — an ✕ rather than a ✚."""
    c = math.cos(math.pi / 4)
    s = math.sin(math.pi / 4)
    return _fit([
        (0.5 + (x - 0.5) * c - (y - 0.5) * s, 0.5 + (x - 0.5) * s + (y - 0.5) * c)
        for x, y in _plus(arm)
    ])


def _arrow(head=0.58, stem=0.22):
    """An arrow, pointing up.

    ⚠ THE HEAD REACHES PAST THE MIDDLE ON PURPOSE. With a shallow head the box's
    centre sits in the STEM, and from there the barb tips are hidden behind the
    head's underside — not star-shaped, and a monitor that draws the barbs filled
    in. A head that contains the centre makes the whole outline visible from it.
    """
    a, b = 0.5 - stem, 0.5 + stem
    return [(0.5, 0.0), (1.0, head), (b, head), (b, 1.0), (a, 1.0), (a, head), (0.0, head)]


def _bubble(r=0.22, steps=8):
    """A speech bubble: rounded box, tail bottom-left."""
    box = 0.78  # the body's foot; the tail lives below it
    pts = list(_arc(r, r, r, r, math.pi, math.pi * 1.5, steps))
    pts += _arc(1 - r, r, r, r, math.pi * 1.5, _TAU, steps)
    pts += _arc(1 - r, box - r, r, r, 0, math.pi / 2, steps)
    pts += [(0.46, box), (0.30, 1.0), (0.30, box)]
    pts += _arc(r, box - r, r, r, math.pi / 2, math.pi, steps)
    return _fit(pts)


def _trapezoid(inset=0.22):
    return [(inset, 0.0), (1 - inset, 0.0), (1.0, 1.0), (0.0, 1.0)]


def _parallelogram(slant=0.24):
    return [(slant, 0.0), (1.0, 0.0), (1 - slant, 1.0), (0.0, 1.0)]


def _kite():
    return [(0.5, 0.0), (1.0, 0.36), (0.5, 1.0), (0.0, 0.36)]


# ⚠ THE FIRST FOUR SHAPES ARE FROZEN, and written out rather than built. Every
# project saved before the library grew stores `kind: "pentagon"` or `"star"`, and
# a pentagon that changed shape on load would silently redraw somebody's finished
# animatic. The builders above would give a slightly different (better-centred)
# pentagon — so the old one stays exactly as it was and only the new shapes get
# the fit.
_LEGACY_SHAPES: dict[str, list[tuple[float, float]]] = {
    "rect": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
    "pentagon": [(0.5, 0.0), (1.0, 0.38), (0.82, 1.0), (0.18, 1.0), (0.0, 0.38)],
    "star": [
        (0.5, 0.0), (0.61, 0.35), (0.98, 0.35), (0.68, 0.57),
        (0.79, 0.91), (0.5, 0.7), (0.21, 0.91), (0.32, 0.57),
        (0.02, 0.35), (0.39, 0.35),
    ],
}

# ⚠ IDS ARE STORED IN SAVED PROJECTS. Renaming one is a data migration, not a
# rename — a clip whose kind no longer resolves falls back to a plain box.
_SHAPE_POINTS: dict[str, list[tuple[float, float]]] = {
    **_LEGACY_SHAPES,
    # Basic
    "round_rect": _rounded(0.22),
    "triangle": _poly(3),
    "diamond": _poly(4),
    "half_circle": _half_circle(),
    "quarter_circle": _quarter_circle(),
    "arch": _arch(),
    # Polygons
    "hexagon": _poly(6, 0.0),
    "heptagon": _poly(7),
    "octagon": _poly(8, math.pi / 8),
    "decagon": _poly(10),
    "trapezoid": _trapezoid(),
    "parallelogram": _parallelogram(),
    "kite": _kite(),
    # Stars & bursts
    "star4": _star_poly(4, 0.28),
    "star6": _star_poly(6, 0.55),
    "star8": _star_poly(8, 0.6),
    "burst12": _star_poly(12, 0.62),
    "starburst": _star_poly(16, 0.4),
    "sunburst": _star_poly(24, 0.72),
    "seal": _star_poly(20, 0.86),
    # Flowers & blobs
    "flower5": _flower(5, 0.3),
    "flower6": _flower(6, 0.32),
    "flower8": _flower(8, 0.38),
    "clover": _flower(4, 0.18, 0.75),
    "quatrefoil": _flower(4, 0.66, 1),
    "blob": _blob(),
    "scallop": _scallop(12, 0.12),
    "cog": _cog(10),
    # Symbols
    "heart": _heart(),
    "drop": _drop(),
    "leaf": _leaf(),
    "shield": _shield(),
    "plus": _plus(),
    "cross": _cross(),
    "arrow": _arrow(),
    "pac": _pac(),
    "bubble": _bubble(),
}

# EVERY KIND THE EDITOR CAN SEND, in the picker's own order — ⚠ the same order as
# `SHAPE_KINDS` in `client/src/animatic/shape_points.js`, which is what
# `tests/shape_points_check.py` compares. 'ellipse' is in this list and NOT in the
# table above: Pillow draws a true ellipse for it.
SHAPE_KINDS = (
    "rect", "round_rect", "ellipse", "triangle", "diamond",
    "half_circle", "quarter_circle", "arch",
    "pentagon", "hexagon", "heptagon", "octagon", "decagon",
    "trapezoid", "parallelogram", "kite",
    "star", "star4", "star6", "star8", "burst12", "starburst", "sunburst", "seal",
    "flower5", "flower6", "flower8", "clover", "quatrefoil", "blob", "scallop", "cog",
    "heart", "drop", "leaf", "shield", "plus", "cross", "arrow", "pac", "bubble",
)


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
        # ⚠ THROUGH `box_size`, NOT OFF `w`/`h` — the box is w/h AFTER `scale`,
        # and the monitor's `shapeFan` reads it through the twin of this call.
        # One of the two left reading the raw field is a shape that is the wrong
        # size in the MP4 and the right size on screen.
        frac_w, frac_h = box_size(shape)
        box_w = max(1, int(round(frac_w * width)))
        box_h = max(1, int(round(frac_h * height)))
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


def _has_alpha(im: Image.Image) -> bool:
    """Does this file actually carry transparency? Mirrors `_keeps_alpha` in
    `server/animatics.py`, which decides whether any is STORED in the first
    place. A palette PNG hides its transparency in `info`, not in its mode."""
    if im.mode in ("RGBA", "LA"):
        return True
    return im.mode == "P" and "transparency" in im.info


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
        # Through `box_size` for the same reason `draw_shapes` is — an overlay
        # is placed with the identical box and carries the identical `scale`.
        frac_w, frac_h = box_size(item)
        box_w = max(1, min(int(round(frac_w * width)), width * 3))
        box_h = max(1, min(int(round(frac_h * height)), height * 3))

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


def _ground(size: tuple[int, int], background: str) -> Image.Image:
    """The empty frame every picture track is stacked onto — the bar colour.

    ⚠ THIS IS ALSO WHAT A MOMENT WITH NO PICTURE LOOKS LIKE, and that moment can
    now happen: clips are placed freely on their tracks (`frame_spans`), so a
    track can have a gap in it and a gap on the bottom track with nothing above it
    is a frame of pure backdrop. Before tracks the sequence had no holes, so the
    planners were free to SKIP such a moment — and skipping one now would make the
    encoded video shorter than the timeline and pull the audio out of sync.
    """
    return Image.new("RGB", size, _parse_colour(background))


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
        # ⚠ **A CUT-OUT PNG KEEPS ITS TRANSPARENCY NOW, AND THAT REVERSES THE
        # NOTE THAT USED TO BE HERE.** It read: *still `convert("RGB")`, not
        # RGBA … honouring it now would silently change every animatic that has
        # ever used a cut-out still.* The caution was right and the premise is
        # gone: BOTH upload paths flattened to RGB before writing the file
        # (`_keeps_alpha` in `server/animatics.py`), so no picture stored before
        # that fix has any alpha left to honour — nothing existing can change.
        # What the old behaviour cost was the ordinary case: a logo with a
        # transparent background arrived as a black card, and on a row above the
        # film that card is a lid over the whole picture (E57 again, wearing a
        # different hat). A frame with no alpha is still opened as RGB, so the
        # cheap path stays cheap.
        im = im.convert("RGBA" if _has_alpha(im) else "RGB")
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
    base: Image.Image,
    source: dict | str,
    size: tuple[int, int],
    fit: str,
    transform: dict | None,
    look: dict | None = None,
) -> Image.Image:
    """One clip's picture composited ONTO `base` — layer, then blend mode.

    The blend happens HERE rather than in `_picture_layer` because it is a
    question about two things: this picture and whatever is under it.

    ⚠ IT TAKES THE CANVAS UNDERNEATH IT NOW, rather than making one out of the bar
    colour. That is what makes a stack of picture TRACKS work: under the bottom
    track there is only the backdrop (so "multiply" on a base clip darkens it
    toward the bars, byte for byte what this always produced), and under an upper
    track there is the picture below — so a clip on track 1 with a chroma key or a
    faded edge reveals track 0 through it, which is what a track above another one
    means everywhere else.
    """
    layer = _picture_layer(source, size, fit, transform, look)
    return blend_onto(base, layer, (look or {}).get("blend") or DEFAULT_BLEND)


def _veiled(canvas: Image.Image, colour: str, factor: float) -> Image.Image:
    """A finished RGB frame laid over with a flat colour — how a dip goes out.

    ⚠ A VEIL, NOT A FADE OF THE PICTURE'S OWN OPACITY, which is what a dip used
    to be. Over real numbers the two are the same arithmetic *while the colour
    is the backdrop* — fading a picture toward the bar colour and laying the bar
    colour over it land on the same pixel — so a dip that names no colour is the
    dip that always shipped. They part company the moment the colour is
    something else: only the veil also covers the LETTERBOX BARS, and without
    that a dip to red would snap the bars to red at both edges of the window,
    which are exactly the two moments a transition has to be invisible at.

    (The one thing that is not bit-exact: the veil rounds to 8 bits a second
    time, so a keyed or feathered edge can land a single level away from what
    the fade produced. Nothing sees that; `tests/transition_check.py` measures a
    dip by whether the middle of the window goes dark and comes back.)
    """
    factor = max(0.0, min(1.0, float(factor)))
    if factor <= 0.0:
        return canvas
    veil = Image.new("RGB", canvas.size, _parse_colour(colour))
    return Image.blend(canvas, veil, factor)


def _slide_offsets(
    size: tuple[int, int], direction: str | None, m: float
) -> tuple[tuple[int, int], tuple[int, int]]:
    """How far each of a slide's two pictures has travelled, in pixels.

    ⚠ TWIN of `slideOffsets` in `ProgramCanvas.jsx`. BOTH pictures move — that
    is what separates a push from a cover — and they are always exactly one
    frame apart, so the background shows through neither. "left" is the default
    and the behaviour that already shipped.
    """
    width, height = size
    if direction == "right":
        offset = int(round(width * m))
        return (offset, 0), (offset - width, 0)
    if direction == "up":
        offset = int(round(height * m))
        return (0, -offset), (0, height - offset)
    if direction == "down":
        offset = int(round(height * m))
        return (0, offset), (0, offset - height)
    offset = int(round(width * m))
    return (-offset, 0), (width - offset, 0)


def _transition_canvas(
    base: Image.Image,
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
    params: dict | None = None,
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
    m = max(0.0, min(1.0, float(mix)))
    blend_b = (look_b or {}).get("blend") or DEFAULT_BLEND
    # Already filled in by `transition_params`, so nothing below has to ask
    # whether a key exists. Empty is legal and means every default — which is
    # what a caller written before transitions took parameters passes.
    p = params or {}

    if kind == "dip":
        # NOT a two-picture blend: the shot goes out through a colour and the
        # next one comes up from it, so only ever ONE picture is on screen. That
        # is what makes a dip read as a beat rather than a cross-fade.
        near = m < 0.5
        # ⚠ THE VEIL COVERS EVERYTHING UNDERNEATH, tracks below included. It has
        # to: a dip is a full-frame beat, it already covers the letterbox bars for
        # the same reason, and the monitor draws it as a full-frame quad over the
        # composite so far (`ProgramCanvas`). Anything narrower would be a dip that
        # blacked out one track while another stayed lit.
        return _veiled(
            _picture_canvas(
                base,
                source if near else source_b,
                size,
                fit,
                transform if near else transform_b,
                look if near else look_b,
            ),
            # "" means the bar colour, the same empty-is-inherit rule `lut.name`
            # follows — and the colour a dip has always gone out through.
            p.get("color") or background,
            1.0 - abs(2 * m - 1),
        )

    # Each picture is graded and masked ON ITS OWN before the two meet. A
    # dissolve between an ungraded shot and a graded one then dissolves the two
    # LOOKS, which is what every NLE shows — grading the blended result instead
    # would make one clip's LUT bleed onto its neighbour.
    b_layer = _picture_layer(source_b, size, fit, transform_b, look_b)

    if kind == "slide":
        # A push: the outgoing picture is driven off the frame while the incoming
        # one comes in behind it. Both move, which is what separates a push from
        # a cover — and the background shows through neither, because together
        # they are always exactly one frame across.
        a_shift, b_shift = _slide_offsets(size, p.get("direction"), m)
        a_layer = _picture_layer(source, size, fit, transform, look)
        out = blend_onto(base, _shifted(a_layer, *a_shift), (look or {}).get("blend") or DEFAULT_BLEND)
        return blend_onto(out, _shifted(b_layer, *b_shift), blend_b)

    base = _picture_canvas(base, source, size, fit, transform, look)

    # ⚠ EVERY REVEAL IS ONE LINE: a shape multiplied into the ARRIVING
    # picture's alpha, exactly as `apply_mask` above multiplied the clip's own
    # mask into it. A wipe, an iris, a clock and a chequerboard differ only in
    # the field `matte_coverage` evaluates — see `animatic_transitions.py`.
    #
    # ⚠ AND IT IS STILL A COMPOSITE, not a blend, so everything the docstring
    # above promises still holds: the arriving picture keeps its blend mode, its
    # chroma key and its own mask THROUGH the transition, because none of the
    # three ever learns that a matte was applied after it.
    matte = transition_matte(kind)
    if matte != "none":
        return blend_onto(base, apply_matte(b_layer, matte, p, m), blend_b)

    # dissolve, and anything unrecognised — but `transition_window` has already
    # folded unknown kinds down to this one, so both sides fall back together.
    #
    # ⚠ NOT re-expressed as "the constant matte", though it is one. `apply_matte`
    # rounds on the way back to 8 bits and `_faded_layer` TRUNCATES, so routing a
    # dissolve through it would move every blended pixel by up to one level for
    # no gain — and a dissolve is the one transition that has shipped since the
    # beginning.
    return blend_onto(base, _faded_layer(b_layer, m), blend_b)


def _faded_layer(layer: Image.Image, factor: float) -> Image.Image:
    """A copy of an RGBA layer with its alpha scaled — a dissolve's other half."""
    factor = max(0.0, min(1.0, float(factor)))
    if factor >= 1.0:
        return layer
    out = layer.copy()
    out.putalpha(out.getchannel("A").point(lambda a: int(a * factor)))
    return out


def _shifted(layer: Image.Image, dx: int, dy: int = 0) -> Image.Image:
    """An RGBA layer moved on its own full-frame canvas — for a slide.

    `dy` defaults to 0, which is every caller written while a slide could only
    go sideways.
    """
    if dx == 0 and dy == 0:
        return layer
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(layer, (dx, dy))
    return out


def _draw_track(
    canvas: Image.Image,
    picture: dict,
    size: tuple[int, int],
    fit: str,
    background: str,
) -> Image.Image:
    """ONE picture track, composited onto everything already drawn.

    `picture` is one entry of the stack `render_frame` is given — the same shape
    `scene_at` puts in `scene["pictures"]`, with the sources resolved to files by
    `build_animatic`:

        {"source", "transform", "look",                     the clip
         "picture_b", "transform_b", "look_b",              the one arriving
         "transition", "mix", "transition_params"}           and how

    With no second picture it is a plain composite; with one it is the transition,
    which is track-local (`transition_window`) and therefore drawn here, inside
    one track, rather than once for the whole frame.
    """
    if picture.get("picture_b") and picture.get("transition"):
        return _transition_canvas(
            canvas,
            picture.get("source"),
            picture.get("picture_b"),
            size,
            fit,
            background,
            picture.get("transform"),
            picture.get("transform_b"),
            picture.get("transition"),
            float(picture.get("mix") or 0.0),
            picture.get("look"),
            picture.get("look_b"),
            picture.get("transition_params"),
        )
    return _picture_canvas(
        canvas, picture.get("source"), size, fit,
        picture.get("transform"), picture.get("look"),
    )


def render_frame(
    source: dict | str | None = None,
    size: tuple[int, int] = (1920, 1080),
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
    transition_params: dict | None = None,
    pictures: list[dict] | None = None,
    layers: list[dict] | None = None,
) -> Image.Image:
    """One video frame: THE STACK OF PICTURE TRACKS plus every layer over it.

    ⚠ `pictures` IS THE PICTURE, AND IT IS A LIST — one entry per picture track
    with something on it, BOTTOM TRACK FIRST, each in the shape `_draw_track`
    documents. That is what `scene_at` resolves and what both planners carry. An
    EMPTY list is legal and draws the letterbox colour: a track can have a gap in
    it now, and a moment with nothing on any track IS the backdrop.

    ⚠ The single-picture keyword form (`source`, `transform`, `picture_b`, …) is
    kept and means a one-entry stack. Every caller written before tracks passes
    it, `tests/effects_check.py` pins its bytes, and one picture on the bottom
    track composited onto the bar colour is exactly what it always produced —
    which is why the goldens still hold.

    `look` / `look_b` are the resolved effects, mask and blend mode. Absent —
    which is every animatic written before them — nothing is graded.

    ⚠ `layers` IS THE DRAW ORDER, and it is the reason a row can be dragged up and
    down the timeline at all. It is `scene_at`'s list — `{"kind", "index"}` per
    visible clip, bottom of the stack first, indexing `pictures` / `shapes` /
    `overlays` / `texts`. Passing None means the order this function had written
    into it before rows could be restacked: every picture, then the shapes, then
    the overlays, then the text. That is also exactly what `ordered_layers`
    produces for a project with no saved order, so the two paths agree — the
    fallback is there for the keyword-form callers and the goldens, not as a
    second opinion.

    ⚠ A CAPTION IS NO LONGER GUARANTEED TO BE ON TOP, and that is the point of the
    feature: text is a row like any other and can be dropped under a picture, in
    which case the picture covers it. What IS still guaranteed to be last is the
    shot LABEL — it is chrome, not part of the film.
    """
    if pictures is None:
        pictures = (
            []
            if source is None
            else [
                {
                    "source": source,
                    "transform": transform,
                    "look": look,
                    "picture_b": picture_b,
                    "transform_b": transform_b,
                    "transition": transition,
                    "mix": mix,
                    "look_b": look_b,
                    "transition_params": transition_params,
                }
            ]
        )
    canvas = _ground(size, background)
    # ⚠ ONE WALK, IN THE ORDER THE STACK SAYS. This used to be four fixed
    # sections — every picture, then the shapes, then the overlays, then the text
    # — and that sequence being written HERE is precisely what made a row
    # unmovable outside its own kind. `layer_runs` folds neighbouring clips of one
    # kind back into a single call, which matters for text: `draw_texts` measures
    # every caption sharing a zone and stacks them down it, so handing it one at a
    # time would pile two subtitles on top of each other.
    runs = layer_runs(layers) if layers is not None else None
    if runs is None:
        runs = (
            [{"kind": "picture", "indices": [i]} for i in range(len(pictures))]
            + ([{"kind": "shape", "indices": list(range(len(shapes or [])))}] if shapes else [])
            + ([{"kind": "overlay", "indices": list(range(len(overlays or [])))}] if overlays else [])
            + ([{"kind": "text", "indices": list(range(len(texts or [])))}] if texts else [])
        )
    for run in runs:
        kind = run["kind"]
        picked = run["indices"]
        if kind == "picture":
            for i in picked:
                if 0 <= i < len(pictures):
                    canvas = _draw_track(canvas, pictures[i], size, fit, background)
        elif kind == "shape":
            group = [(shapes or [])[i] for i in picked if 0 <= i < len(shapes or [])]
            if group:
                draw_shapes(canvas, group)
        elif kind == "overlay":
            group = [(overlays or [])[i] for i in picked if 0 <= i < len(overlays or [])]
            if group:
                # ⚠ REBOUND, not drawn in place. An overlay with a blend mode
                # cannot be pasted — the mode is a function of the pixels
                # underneath it — so the composite comes back as a new image.
                # Every other layer is still drawn in place, which is why only
                # this one is an assignment.
                canvas = draw_overlays(canvas, group)
        elif kind == "text":
            group = [(texts or [])[i] for i in picked if 0 <= i < len(texts or [])]
            if group:
                draw_texts(canvas, group)
    # The shot label goes on LAST, always, whatever the stack says: it is the
    # editor's own annotation rather than part of the film, and a label hidden
    # under a picture would read as a bug in the export.
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
    lane_order: list[str] | None = None,
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

    ⚠ WHERE EACH PICTURE SITS COMES FROM `frame_spans`, NOT FROM A SUM WRITTEN
    HERE. It used to be its own running total, which was a second opinion about
    the timeline and is now flatly wrong: clips are placed by `start_ms` on
    numbered TRACKS, so "add up the clips before it" answers a question nobody is
    asking. One evaluator, three callers (this, the sampling planner, the monitor).

    ⚠ AND A SEGMENT CARRIES A STACK, NOT ONE PICTURE — `pictures`, bottom track
    first. An EMPTY stack is a real segment and must be emitted: a track can have
    a gap in it, and skipping that moment (which is what the old
    `frame_index is None: continue` did) would make the encoded video shorter than
    the timeline and pull the audio out of sync from the first gap onward.

    Returns (segments, total_ms) where each segment is
    {"pictures": [{"frame": index into `frames`}…], "start_ms", "duration_ms",
    "texts": [clip…], "shapes": [shape…], "overlays": [picture…]}.
    """
    from animatic_render import _stack_at

    spans, total_ms = frame_spans(frames, end_ms)
    if not spans:
        return [], 0

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
    for span in spans:
        cuts.add(span["start"])
        cuts.add(span["end"])
    for start, end, _ in clips + figures + pictures:
        cuts.add(start)
        cuts.add(end)
    ordered = sorted(c for c in cuts if 0 <= c <= total_ms)

    segments: list[dict] = []
    for a, b in zip(ordered, ordered[1:]):
        if b - a <= 0:
            continue
        stack = _stack_at(spans, a)
        seg_texts = [clip for (s, e, clip) in clips if s <= a < e]
        seg_shapes = [shape for (s, e, shape) in figures if s <= a < e]
        seg_overlays = [pic for (s, e, pic) in pictures if s <= a < e]
        segments.append(
            {
                # The stack at this instant, bottom track first — the same
                # evaluator `scene_at` uses, so the fast path and the sampling
                # path cannot disagree about which pictures are up.
                "pictures": [{"frame": s["index"]} for s in stack],
                "start_ms": a,
                "duration_ms": b - a,
                "texts": seg_texts,
                "shapes": seg_shapes,
                "overlays": seg_overlays,
                # ⚠ WHAT DRAWS OVER WHAT, worked out by the SAME function
                # `scene_at` uses, from the same four lists in the same indexing.
                # The fast path never builds a scene, so it has to ask directly —
                # and asking here rather than in the renderer is what keeps the two
                # planners' segments the same shape.
                #
                # ⚠ THE PICTURES ARE HANDED OVER AS `{"track"}` ONLY, because a
                # track number is the whole of what a picture contributes to the
                # ORDER (its rank is that number when nothing has been restacked).
                # The segment's own `pictures` carry a frame index instead, and the
                # indices line up because both are built from `stack`.
                "layers": ordered_layers(
                    {"settings": {"lane_order": lane_order or []}},
                    [{"track": sp["track"]} for sp in stack],
                    seg_shapes,
                    seg_overlays,
                    seg_texts,
                ),
            }
        )

    # A boundary landing exactly on a frame edge can leave a sliver too short for
    # ffmpeg to show; fold anything under 40ms into its neighbour — but only into
    # one showing the same pictures, or the fold would show the wrong shot.
    def _stack_of(segment: dict) -> tuple:
        return tuple(item["frame"] for item in segment["pictures"])

    merged: list[dict] = []
    for segment in segments:
        if (
            merged
            and segment["duration_ms"] < 40
            and _stack_of(merged[-1]) == _stack_of(segment)
        ):
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
    lane_order: list[str] | None = None,
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
    renderer downstream cannot tell which planner ran — with one addition per
    segment, `signature` (the render cache key), and several per PICTURE in the
    stack: `transform` (the resolved pan/zoom/fade), `source_ms` and `look`, plus
    `frame_b` / `transform_b` / `look_b` / `transition` / `transition_params` /
    `mix` / `source_ms_b` on a picture that is mid-transition.

    Note that transitions cannot change `total_ms`: they are boundary-local, so
    the sampling grid below is the same one an untransitioned project gets.
    """
    project = {
        "frames": frames,
        "texts": texts or [],
        "shapes": shapes or [],
        "overlays": overlays or [],
        "transitions": transitions or [],
        # ⚠ `settings` IS IN HERE FOR ONE FIELD: `lane_order`, which is what
        # `scene_at` needs to rank the rows. It was absent entirely before, which
        # was harmless while the draw order was hard-coded and is not any more —
        # a scene built without it would export the default stack while the
        # monitor drew the restacked one.
        "settings": {"lane_order": lane_order or []},
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
        # ⚠ NO `continue` ON AN EMPTY STACK. A moment with nothing on any track is
        # a frame of backdrop and has to be encoded, or the video comes out
        # shorter than the timeline and the audio drifts from the first gap on.
        # It used to be impossible; free placement makes it ordinary.
        stack: list[dict] = []
        for item in scene["pictures"]:
            picture = item["frame"]
            entry = {
                "frame": picture["index"],
                "transform": _transform_of(picture),
                # WHICH MOMENT OF THE SOURCE FILE this sample shows, for a video
                # clip; None for a still or a colour card. Carried rather than
                # recomputed downstream so there is exactly one place that answers
                # it — `source_at` — and the still the export draws is provably
                # the one the preview drew.
                "source_ms": picture.get("source_ms"),
                # The grade, resolved at this instant. Carried for the same reason
                # `source_ms` is: one place answers "what does this clip look like
                # now", and it is the place the monitor asked too.
                "look": _look_of(picture),
            }
            # Mid-transition: the second picture rides along on this TRACK's
            # entry, because a transition is track-local — see `transition_window`.
            arriving = item["frame_b"]
            if arriving is not None:
                entry["frame_b"] = arriving["index"]
                entry["transform_b"] = _transform_of(arriving)
                entry["transition"] = item["transition"]
                # ⚠ AND ITS PARAMETERS. They are already inside `signature`, so
                # leaving them off here would not reuse the wrong still — it would
                # render the RIGHT number of stills and draw every one of them in
                # the default direction, so the monitor wiped upwards and the MP4
                # wiped right. Two places carry a transition to the renderer and
                # this is the first; the other is `build_animatic`'s task args.
                entry["transition_params"] = item["transition_params"]
                entry["mix"] = item["mix"]
                entry["source_ms_b"] = arriving.get("source_ms")
                entry["look_b"] = _look_of(arriving)
            stack.append(entry)
        segments.append(
            {
                "pictures": stack,
                "start_ms": start,
                "duration_ms": end - start,
                "texts": scene["texts"],
                "shapes": scene["shapes"],
                "overlays": scene["overlays"],
                # Straight off the scene: this planner HAS one, so there is
                # nothing to work out a second time. ⚠ The order is inside
                # `signature` too (as `stack_key`), or a re-export after a restack
                # would come back as the previous export's stills.
                "layers": scene["layers"],
                "signature": scene_signature(scene),
            }
        )
    return segments, total_ms


def _segment_at(segments: list[dict], at_ms: float, total_ms: int) -> list[dict]:
    """Just the segment showing at `at_ms` — a still export's whole plan.

    Returned as a LIST because that is what the renderer downstream takes, and
    a still that went through a different code path than the video would be a
    poster frame nobody could trust.

    A moment past the end clamps to the last segment rather than returning
    nothing: the playhead can sit on the very end of the timeline, and the frame
    you are looking at there is the last one.
    """
    if not segments:
        return []
    at = max(0.0, min(float(at_ms or 0), max(0.0, total_ms - 1)))
    for segment in segments:
        if segment["start_ms"] <= at < segment["start_ms"] + segment["duration_ms"]:
            return [segment]
    return [segments[-1]]


def _still_layer(usable: list[dict], item: dict, fit: str) -> dict:
    """One entry of a segment's stack, with its sources resolved to files.

    The bridge between the planners (which name pictures by INDEX) and
    `render_frame` (which wants something it can open). Both planners come through
    here so the fast path and the sampling path hand the renderer the same shape.

    ⚠ `or _static_transform(clip)`, and the same for the look. `plan_segments` —
    the planner a project with no animation gets — resolves neither, so a STORED
    zoom or a STORED grade would be dropped from the MP4 while the monitor showed
    it. Falling back to the clip's own values is what makes the two agree
    whichever planner ran.
    """
    clip = usable[item["frame"]]
    out = {
        "source": _source_for(clip, item.get("source_ms")),
        "transform": item.get("transform") or _static_transform(clip),
        "look": item.get("look") or _resolved_look(clip),
    }
    # Mid-transition this track names a SECOND picture to blend with. It indexes
    # `usable` like `frame` does, so a clip dropped for a missing image can't be
    # picked up here either.
    index_b = item.get("frame_b")
    if index_b is not None:
        out.update(
            {
                "picture_b": _source_for(usable[index_b], item.get("source_ms_b")),
                "transform_b": item.get("transform_b"),
                "transition": item.get("transition"),
                "mix": item.get("mix") or 0.0,
                "look_b": item.get("look_b"),
                # The second of the two places — see `plan_animated_segments`.
                "transition_params": item.get("transition_params") or {},
            }
        )
    return out


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
# ⚠ TWIN FILE: `client/src/animatic/audio_mix.js`. `track_play_ms`, `fade_window`,
# `curve_gain` and `EQ_BANDS` are written twice, in the same shape and clamps,
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


def track_start_ms(track: dict) -> int:
    """Where on the TIMELINE this clip begins. 0 for anything that predates the
    razor being able to cut audio, which is what makes those projects mix
    exactly as they always did.

    ⚠ TWIN of `trackStartMs` in `client/src/animatic/audio_mix.js`.
    """
    return max(0, int(track.get("start_ms") or 0))


def track_play_ms(track: dict, total_ms: int = 0) -> int:
    """How long this track is HEARD for, in milliseconds.

    Its trim if it has one, otherwise whatever is left of the file after
    `offset_ms` — and never longer than the room the video leaves AFTER the clip
    starts, because the export is cut at `total_ms` and a fade placed past that
    is a fade nobody hears.

    A track whose `duration_ms` never reached us (the browser measures it, so a
    project saved by something else may not carry one) is assumed to play to the
    end of the video. That is the only assumption available without an ffprobe,
    and it is the right one: it is what an untrimmed track does.
    """
    start = track_start_ms(track)
    # What is left of the video once this clip has waited its turn. A clip
    # sitting entirely past the end of the video is heard for nothing at all.
    room = max(0, int(total_ms) - start) if total_ms else 0
    trim = track.get("trim_ms")
    if trim:
        play = max(0, int(trim))
    else:
        duration = int(track.get("duration_ms") or 0)
        play = max(0, duration - max(0, int(track.get("offset_ms") or 0))) if duration else 0
    if play <= 0:
        play = room
    if total_ms:
        play = min(play, room)
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


# --- The shape of a fade ----------------------------------------------------
# THREE CURVES, and they are Premiere's three Audio Transitions → Crossfade
# entries: Constant Gain is a straight line, Constant Power a quarter sine,
# Exponential Fade a decade curve. A crossfade between two clips is the outgoing
# one's fade OUT overlapping the incoming one's fade IN, and this graph already
# mixes whatever overlaps — so the feature is a curve per END of a clip and
# nothing else. `acrossfade` is deliberately NOT used: it concatenates two
# streams and would shorten the timeline, which is the same objection that made
# picture transitions boundary-local.
#
# ⚠ `afade` IS THE IMPLEMENTATION, not one of two. The editor's `curve_gain`
# twin exists to PREDICT what these curves do so the preview matches; the export
# runs the real filter. That is why the mapping below is the only place a curve
# name is turned into anything, and why `tests/audio_mix_check.py` measures the
# encoded audio rather than trusting either formula.
#
# ⚠ TWIN of `FADE_CURVES` / `FADE_CURVE_INFO` in `client/src/animatic/audio_mix.js`.
FADE_CURVES = ("linear", "power", "exponential")
# ⚠ "linear" → "tri" IS `afade`'S OWN DEFAULT, so every project that predates
# this field encodes byte-for-byte the graph it always did.
FADE_FF_CURVE = {"linear": "tri", "power": "qsin", "exponential": "exp"}


def fade_curve(track: dict, side: str) -> str:
    """The curve on one END of one clip, folded to "linear" if it is not one.

    Folded rather than rejected — the same rule `transition_kind` follows. A
    project written by a newer client naming a curve this build has never heard
    of still renders, at the shape every project used to have.
    """
    raw = str(track.get("fade_in_curve" if side == "in" else "fade_out_curve") or "")
    return raw if raw in FADE_CURVES else "linear"


def curve_gain(curve: str, x: float) -> float:
    """The gain a curve gives at `x`: 0 is silence, 1 is full level.

    ⚠ TRANSCRIBED FROM `fade_gain()` in libavfilter/af_afade.c, at afade's
    default silence=0 / unity=1. Nothing here is a fit or an approximation of the
    filter — it IS the filter's arithmetic, which is the only way the editor's
    preview can be checked against the encode instead of merely resembling it.

    ⚠ BOTH ENDS READ THE SAME CURVE, x running towards 1 at full level: a fade
    out is this read backwards. Which is also why constant power holds a
    crossfade up — sin(x·π/2) against sin((1−x)·π/2) sums to unity in POWER,
    where constant gain sums to unity in AMPLITUDE and leaves a −3 dB scoop.

    ⚠ TWIN of `curveGain` in `client/src/animatic/audio_mix.js`.
    """
    t = max(0.0, min(1.0, float(x)))
    if curve == "power":
        return math.sin(t * math.pi / 2.0)
    if curve == "exponential":
        # −11.5129… is 5·ln(0.1): a decade curve bottoming out at −100 dB.
        return math.exp(-11.512925464970227 * (1.0 - t))
    return t


def fade_gain_at(track: dict, ms: float, total_ms: int = 0) -> float:
    """What the whole fade envelope is worth at `ms` in TRACK time.

    Nothing in the export calls this — ffmpeg does the fading. It exists so the
    twin can be checked at the level that matters (the GAIN, not just where the
    ramp starts), because two implementations of `fade_window` that agree to the
    millisecond can still be ramping along different curves.

    ⚠ TWIN of `fadeGainAt` in `client/src/animatic/audio_mix.js`.
    """
    fade_in, out_at, fade_out = fade_window(track, total_ms)
    gain = 1.0
    if fade_in > 0 and ms < fade_in:
        gain = curve_gain(fade_curve(track, "in"), max(0.0, ms) / fade_in)
    if fade_out > 0 and ms > out_at:
        gain = min(gain, curve_gain(fade_curve(track, "out"), 1.0 - (ms - out_at) / fade_out))
    return max(0.0, min(1.0, gain))


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
    # ⚠ Keyed by the CLIP's id, falling back to the upload. `duck_target` names
    # one entry in this list, and since the razor can cut a file into several
    # clips the upload is no longer unique — two halves of one voiceover would
    # both answer to the same key and the second would win at random. In every
    # project that predates the razor `id` IS the upload, so the fallback keeps
    # those resolving exactly as they did.
    by_id = {
        (t.get("id") or t.get("upload_id")): i
        for i, t in enumerate(tracks)
        if (t.get("id") or t.get("upload_id"))
    }
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
    starts = [track_start_ms(t) for t in tracks]
    ducks = _duck_pairs(tracks)
    plain = (
        len(tracks) == 1
        and abs(volumes[0] - 1.0) <= 1e-3
        and not windows[0][0]
        and not windows[0][2]
        and not tones[0]
        # A clip that does not start at the head of the video needs `adelay`,
        # and there is nowhere but a graph to put it. Without this the plain
        # path would map the input straight through and the piece you cut out of
        # the middle of a take would be heard from the first frame instead.
        and not starts[0]
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
        # ⚠ THE CURVE IS STATED, NEVER DEFAULTED — the same rule as the EQ's
        # widths above, and for the same reason: `tri` happens to be `afade`'s
        # default today, and a graph that only says what it wants when it wants
        # something unusual is a graph you cannot read to find out what it does.
        if fade_in:
            chain.append(
                f"afade=t=in:st=0:d={fade_in / 1000:.3f}"
                f":curve={FADE_FF_CURVE[fade_curve(track, 'in')]}"
            )
        if fade_out:
            chain.append(
                f"afade=t=out:st={out_at / 1000:.3f}:d={fade_out / 1000:.3f}"
                f":curve={FADE_FF_CURVE[fade_curve(track, 'out')]}"
            )
        # ⚠ AFTER THE FADES, NEVER BEFORE. Both `afade` windows are measured from
        # the start of the CLIP — that is what makes a fade travel with a trim —
        # so delaying first would push the clip along the timeline and leave its
        # ramps behind at the head of the video. `all=1` because without it
        # adelay silences every channel it wasn't given a delay for, which turns
        # a stereo bed into a left-channel-only one.
        if starts[i]:
            chain.append(f"adelay=delays={starts[i]}:all=1")
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


# ---------------------------------------------------------------------------
# Rendering the stills — the parallel half
# ---------------------------------------------------------------------------
def export_workers(still_count: int) -> int:
    """How many processes to render `still_count` stills with. 1 = serial.

    ⚠ THE ANSWER IS OFTEN 1, AND THAT IS THE POINT. A pool that is started for
    twelve stills is slower than the loop it replaced, so the threshold is
    checked before the machine is: a short export must not get slower because a
    long one got faster.
    """
    raw = (os.environ.get(_ENV_WORKERS) or "").strip()
    if raw:
        try:
            forced = max(1, int(raw))
        except ValueError:
            logger.warning("%s=%r is not a number — ignored", _ENV_WORKERS, raw)
        else:
            return 1 if forced <= 1 else min(forced, still_count)
    if still_count < _POOL_MIN_STILLS:
        return 1
    return max(1, min(_MAX_WORKERS, (os.cpu_count() or 1) - 1, still_count))


def _render_still(task: dict) -> dict:
    """Composite ONE still and write it. Runs in a worker process.

    ⚠ MODULE LEVEL, AND EVERY ARGUMENT IS PLAIN DATA — paths, dicts, lists,
    numbers. That is not style, it is the contract: Windows has no fork, so a
    worker is a fresh interpreter that receives its argument by PICKLE and its
    function by NAME. A closure could not be sent, and a Pillow image passed in
    or out would be the whole picture down a pipe twice per still, which is
    slower than rendering it single-threaded.
    `_source_for` has therefore already been called in the parent: what arrives
    here is the resolved `{"path": …}` / `{"color": …}`, so a worker never has
    to know that `video_frames` exists.

    Returns a verdict rather than raising, because an exception crossing a pool
    boundary loses the one thing the caller needs — WHICH still it was.
    """
    try:
        image = render_frame(size=tuple(task["size"]), **task["args"])
        image.save(task["out"], "PNG")
    except AnimaticError as e:
        # The export-killing kind: a source with no pixels. Flagged rather than
        # swallowed, so the parent re-raises it exactly as the serial loop did.
        return {"index": task["index"], "ok": False, "fatal": True, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — one bad file, not a dead export
        return {"index": task["index"], "ok": False, "fatal": False, "error": f"{e}"}
    return {"index": task["index"], "ok": True}


def _detached_main():
    """A context manager that hides `__main__` while a Pool is being started.

    ⚠ THIS IS THE "SPAWN-SAFE ENTRY POINT" PROBLEM, SOLVED ONCE, HERE.

    Windows has no fork, so every worker is a fresh interpreter — and before it
    runs anything of ours, `multiprocessing` tries to reconstruct the parent's
    `__main__` module in it. When the parent was started as a plain script
    (`python tests/export_perf_check.py`, `python animatic.py …`) that means
    RE-EXECUTING that script inside every worker: the classic fork bomb, and the
    reason the textbook advice is "guard your entry point with
    `if __name__ == '__main__'`".

    Requiring that guard of every present and future caller is a rule that will
    be broken by someone who has no idea this module started a pool. So instead
    the parent's `__main__` is swapped for an empty stub whose spec is named
    `__main__` for exactly as long as the pool is being created. multiprocessing
    reads the name, hands the child `init_main_from_name="__main__"`, and the
    child's own `_fixup_main_from_name` returns immediately without importing or
    running anything. Workers reach `_render_still` by importing `animatic` off
    the inherited `sys.path`, which is all they ever needed.

    The swap lasts microseconds and is undone in a `finally`, so nothing else in
    the process can observe it.
    """
    import contextlib
    import importlib.machinery
    import sys
    import types

    @contextlib.contextmanager
    def _swap():
        real = sys.modules.get("__main__")
        stub = types.ModuleType("__main__")
        stub.__spec__ = importlib.machinery.ModuleSpec("__main__", None)
        sys.modules["__main__"] = stub
        try:
            yield
        finally:
            if real is not None:
                sys.modules["__main__"] = real
            else:  # pragma: no cover — an interpreter with no __main__ at all
                sys.modules.pop("__main__", None)

    return _swap()


def _render_all_stills(tasks: list[dict], cancelled, report) -> dict:
    """Render every task, in parallel when there are enough of them.

    Returns {"stopped", "failed"} — `failed` is the set of task indexes whose
    still could not be written, whose segments the caller then drops from the
    cut. Raises AnimaticError for the fatal kind, exactly where the serial loop
    used to raise it.

    ⚠ CANCELLATION IS THE PARENT'S JOB. A worker cannot answer "has the user
    pressed stop" — the flag lives in the job store, in the server process — so
    the check stays here, between results, and the pool is terminated when it
    trips. That is what keeps stop working mid-batch: results arrive one at a
    time, so the longest a stop can take is one still.
    """
    total = len(tasks)
    workers = export_workers(total)
    failed: set[int] = set()
    done = 0

    def _finish(result: dict) -> None:
        """Book one verdict in. Raises for the fatal kind."""
        nonlocal done
        done += 1
        if not result["ok"]:
            if result.get("fatal"):
                raise AnimaticError(result["error"])
            failed.add(result["index"])
            logger.warning(
                "[animatic] still %d could not be rendered (%s) — skipped",
                result["index"], result.get("error"),
            )
        report(done, total)

    if workers <= 1:
        logger.info("[animatic] rendering %d still(s) serially", total)
        for task in tasks:
            if cancelled():
                return {"stopped": True, "failed": failed}
            _finish(_render_still(task))
        return {"stopped": False, "failed": failed}

    logger.info("[animatic] rendering %d still(s) across %d worker(s)", total, workers)
    # `spawn`, stated rather than inherited: it is what Windows does anyway, and
    # saying so means this behaves identically on Linux instead of quietly
    # forking a process that has half a FastAPI app and an ffmpeg pipe in it.
    ctx = multiprocessing.get_context("spawn")
    # Small chunks so a stop is felt quickly and a slow still doesn't leave one
    # worker holding a long tail while the others idle.
    chunksize = max(1, min(8, total // (workers * 4) or 1))
    try:
        # See `_detached_main` — this is what makes the pool safe to start from
        # a plain script as well as from the server.
        with _detached_main():
            pool = ctx.Pool(processes=workers)
    except Exception:  # noqa: BLE001 — no pool is a slow export, not a failed one
        logger.warning(
            "[animatic] could not start a worker pool — rendering serially", exc_info=True
        )
        for task in tasks:
            if cancelled():
                return {"stopped": True, "failed": failed}
            _finish(_render_still(task))
        return {"stopped": False, "failed": failed}

    try:
        for result in pool.imap_unordered(_render_still, tasks, chunksize=chunksize):
            _finish(result)
            if cancelled():
                pool.terminate()
                return {"stopped": True, "failed": failed}
        pool.close()
        pool.join()
    finally:
        # Runs on the fatal-error path too: an AnimaticError out of `_finish`
        # must not leave eight interpreters composing stills for an export that
        # has already failed.
        pool.terminate()
    return {"stopped": False, "failed": failed}


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
    container: str = "mp4",
    still_ms: int = 0,
    output_dir: str = "output",
    # WHAT ORDER THE TIMELINE'S ROWS ARE STACKED IN, top of the stack first —
    # `AnimaticSettings.lane_order`. ⚠ EMPTY MEANS THE ORDER THIS EXPORTER ALWAYS
    # USED (pictures by track, then shapes, then overlay pictures, then text), so
    # every project that predates the restack gesture encodes byte for byte what it
    # did before. See `lane_rank` in animatic_render.py.
    lane_order: list[str] | None = None,
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
        audio_tracks: [{"path", "start_ms", "offset_ms", "trim_ms", "volume", …}]
            laid under the sequence and MIXED together — music under a voiceover
            is the usual pair. `start_ms` is where the CLIP sits on the timeline
            and `offset_ms` is how far into the FILE it starts reading (the
            razor sets both when it cuts one in half); `volume` is 1.0 for
            as-recorded. `fade_in_ms` / `fade_out_ms` shape each end, and
            `duck_to` + `role`/`duck_target` pull a bed down under the voice —
            all of that is `audio_graph`'s business. A track whose file is
            missing is skipped.
        container: what FILE to write — 'mp4' (H.264 + AAC, what every export
            has always been), 'gif' (silent, looping, palette-quantised) or
            'png' (ONE frame, at `still_ms`). See `export_presets.py`; anything
            unrecognised falls back to mp4 rather than failing.
        still_ms: which moment a 'png' export is of. Ignored by the other two.
            Only the stills that moment needs are rendered, so a poster frame
            costs one composite rather than a whole export.
        progress_cb: called with {"percent", "message", "stage"}.
        cancel_check: called between frames and during encoding; True stops.

    Returns a summary dict; `stopped` is True if the user cancelled.
    """
    if not frames:
        raise AnimaticError("This project has no frames yet — add some images first.")

    container = export_presets.normalise_container(container)
    # A PNG never reaches ffmpeg — Pillow already has the finished frame, and
    # writing it out IS the export — so a still must not fail for want of an
    # encoder. Video clips still need one, and `video_frames` raises the same
    # advice if it comes to that.
    exe = ffmpeg_exe() if container != "png" else ""  # fail early, before any work
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
            transitions or [], lane_order or [],
        )
        distinct = len({s["signature"] for s in segments})
        if distinct > MAX_RENDERED_STILLS:
            shutil.rmtree(build_dir, ignore_errors=True)
            raise AnimaticError(
                f"This project would need {distinct:,} rendered frames, which is more "
                f"than the {MAX_RENDERED_STILLS:,} an export can hold. Lower the frame "
                "rate, shorten the timeline, or remove some of the animation."
            )
        logger.info(
            "[animatic %s] animated: %d sample(s) at %dfps → %d distinct still(s)",
            job_id, len(segments), fps, distinct,
        )
    else:
        segments, total_ms = plan_segments(
            usable, texts or [], end_ms, shapes or [], overlays or [], lane_order or []
        )

    # --- 1a. A still is ONE segment ----------------------------------------
    # Everything above this line is unchanged: a poster frame is planned by the
    # same planner as the video it is a frame OF, which is the only way it can
    # be provably the same picture. Then all but one sample is thrown away.
    if container == "png":
        segments = _segment_at(segments, still_ms, total_ms)
        if not segments:
            shutil.rmtree(build_dir, ignore_errors=True)
            raise AnimaticError("There is nothing on the timeline at that moment.")

    # --- 1b. Tear any video clips into stills ------------------------------
    # Pillow cannot decode video, so every video clip becomes numbered PNGs
    # before the renderer sees it. Only the SOURCE RANGE each clip actually
    # shows is extracted, and the result is cached by content — so a second
    # export of an unchanged project skips this step entirely.
    #
    # ⚠ This happens AFTER the planner has run and BEFORE anything is drawn: the
    # planner is what decides which source moments will be asked for, and the
    # renderer is what asks for them.
    # ⚠ THE CLIPS THE PLAN ACTUALLY NAMES, not every video clip in the project.
    # For an ordinary export those are the same list (every clip is on screen at
    # some point). For a STILL they are not — one segment names one or two
    # pictures — and extracting a two-minute clip to write a single poster frame
    # would be the whole export's cost for one PNG.
    wanted_clips = {p["frame"] for s in segments for p in s["pictures"]}
    wanted_clips |= {
        p["frame_b"]
        for s in segments
        for p in s["pictures"]
        if p.get("frame_b") is not None
    }
    videos = [
        usable[i] for i in sorted(wanted_clips) if clip_kind(usable[i]) == "video"
    ]
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

    # --- 2. Render one PNG per DISTINCT still ------------------------------
    # A text clip can start or end part-way through a held image, so the unit of
    # rendering is a SEGMENT (a stretch where both the picture and the visible
    # text are constant), not a frame. With no text there is exactly one segment
    # per frame, so this costs nothing in the common case.
    #
    # ⚠ PLANNED, THEN RENDERED — one pass to decide what the distinct stills are
    # and what each is called, a second to actually draw them. It used to be one
    # pass, and splitting it is what lets the drawing go across processes: the
    # names no longer depend on which still finishes first. Both halves of the
    # split are deterministic, so a parallel export and a serial one write the
    # same files under the same names and encode to the same bytes — which is
    # the first thing `tests/export_perf_check.py` asserts.
    tasks: list[dict] = []          # one per distinct still, in first-appearance order
    names: dict[object, str] = {}   # cache key → filename
    plan: list[tuple[object, float]] = []  # per segment: (key, seconds on screen)

    for segment in segments:
        if _cancelled():
            shutil.rmtree(build_dir, ignore_errors=True)
            return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}

        # The TOPMOST picture is the one whose shot label is drawn: a label names
        # the shot you can see, and on a stack of tracks that is the one on top.
        # No pictures at all — a gap on every track — means no label, which is
        # right: there is no shot there to name.
        top = usable[segment["pictures"][-1]["frame"]] if segment["pictures"] else None
        # The shape ids are part of the key: without them two segments differing
        # ONLY in which shapes are up would share one rendered still, and a
        # shape would appear or vanish at the wrong moment.
        #
        # ⚠ AND SO IS EVERY PICTURE IN THE STACK, not just the top one. Two
        # moments showing the same top picture over DIFFERENT lower tracks are two
        # different frames; keying on one of them would reuse the other's still.
        #
        # An animated segment brings its own key instead. Ids alone would be
        # wrong there — two samples one video frame apart hold exactly the same
        # clips and differ only in the values those clips resolved to, so an
        # id-based key would reuse the first still for the whole animation and
        # nothing would move.
        key = segment.get("signature") or (
            tuple(item["frame"] for item in segment["pictures"]),
            tuple(t.get("id") for t in segment["texts"]),
            tuple(s.get("id") for s in segment.get("shapes") or ()),
            tuple(o.get("id") for o in segment.get("overlays") or ()),
        )
        if key not in names:
            names[key] = f"f{len(names):04d}.png"
            tasks.append(
                {
                    "index": len(tasks),
                    "key": key,
                    "out": os.path.join(build_dir, names[key]),
                    "size": size,
                    # ⚠ PLAIN DATA ONLY — this dict is pickled to a worker
                    # process. See `_render_still`.
                    "args": {
                        # ⚠ THE STACK, bottom track first, with every source
                        # resolved to a FILE. What a clip is a picture of at this
                        # instant — its own still, its colour, or the extracted
                        # frame of its source video covering `source_ms` — is
                        # worked out HERE, in the parent, because this is the only
                        # side that knows about `_stills`. See `_source_for`.
                        "pictures": [
                            _still_layer(
                                usable, item, fit=fit,
                            )
                            for item in segment["pictures"]
                        ],
                        "fit": fit,
                        "background": background,
                        "label": (top.get("label", "") if (top and show_labels) else ""),
                        "texts": segment["texts"],
                        "shapes": segment.get("shapes") or [],
                        "overlays": segment.get("overlays") or [],
                        # ⚠ THE DRAW ORDER TRAVELS WITH THE FRAME. Both planners
                        # put it on the segment and it indexes the four lists
                        # above, so the worker draws the stack the monitor drew
                        # without knowing anything about rows or ranks.
                        "layers": segment.get("layers"),
                    },
                }
            )
        # ⚠ The floor is ONE VIDEO FRAME, not 0.1s as it used to be. A segment
        # shorter than 1/fps cannot be shown at all, so that is the smallest
        # meaningful duration — but at 24fps an animated segment IS 1/24s, and
        # the old floor would have stretched every one of them to 100ms, making
        # the export run 2.4× long. It was also quietly wrong before this: a
        # 40–99ms segment (which `plan_segments` can produce) was already being
        # padded out to 100ms and lengthening the video.
        plan.append((key, max(1.0 / fps, segment["duration_ms"] / 1000)))

    # Preparing frames is the first 55% — it's real work on big images. With
    # video clips the first 15 went on tearing them into stills, so this picks
    # up from there; with none it is the 0–55 it has always been.
    base = 15 if videos else 0

    def _still_progress(done: int, count: int) -> None:
        _report(
            base + int((55 - base) * done / max(1, count)),
            f"Preparing frame {done} of {count}",
            "frames",
        )

    outcome = _render_all_stills(tasks, _cancelled, _still_progress)
    if outcome["stopped"]:
        shutil.rmtree(build_dir, ignore_errors=True)
        logger.info("[animatic %s] export STOPPED while rendering stills", job_id)
        return {"stopped": True, "video": None, "frame_count": 0, "duration_ms": 0}

    # A still that could not be drawn takes its segments out of the cut with it
    # — exactly what the old `continue` did, one pass later.
    lost = {tasks[i]["key"] for i in outcome["failed"]}
    rendered = {key: name for key, name in names.items() if key not in lost}
    entries: list[tuple[str, float]] = [
        (rendered[key], seconds) for key, seconds in plan if key in rendered
    ]

    if not entries:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise AnimaticError(
            "None of the frames could be read — their images may have been deleted."
        )

    total_ms = int(round(sum(sec for _, sec in entries) * 1000))
    out_path = os.path.join(out_root, export_presets.output_name(container))

    # --- 2a. A still needs no encoder --------------------------------------
    # The composite is already on disk: the one thing an encode could add here
    # is a way for the poster frame to disagree with the video it came from.
    if container == "png":
        shutil.move(os.path.join(build_dir, entries[0][0]), out_path)
        shutil.rmtree(build_dir, ignore_errors=True)
        _report(100, "Done")
        size_bytes = os.path.getsize(out_path)
        logger.info(
            "[animatic %s] wrote a still at %dms → %s (%.0f kB)",
            job_id, still_ms, out_path, size_bytes / 1024,
        )
        return _summary(
            out_path, container, size_bytes, size, fps,
            # A still is one frame, so it has no duration and no sound. Saying
            # `duration_ms: 0` rather than the timeline's length is what stops
            # the editor offering to play a PNG.
            duration_ms=0, segment_count=1, still_count=1, animated=animated,
            frames=usable, texts=texts, shapes=shapes, overlays=overlays,
            transitions=transitions, videos=videos, skipped=skipped, tracks=[],
        )

    list_path = os.path.join(build_dir, "list.txt")
    _write_concat_list(list_path, entries)

    # --- 2b. Encode --------------------------------------------------------
    tmp_path = os.path.join(build_dir, f"out.{export_presets.CONTAINER_EXT[container]}")

    # Tracks whose file has gone are dropped rather than failing the export.
    # A GIF has no audio stream at all, so its tracks are dropped HERE rather
    # than being mixed into a graph whose output nothing could map.
    tracks = [
        t for t in (audio_tracks or [])
        if t.get("path") and os.path.isfile(t["path"])
    ] if container != "gif" else []
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
    graph = audio_graph(tracks, total_ms) if container != "gif" else None

    if container == "gif":
        # ⚠ A PALETTE, IN ONE PASS. A GIF is 256 colours, and ffmpeg's DEFAULT
        # palette is a fixed web-safe one that turns any gradient — every sky,
        # every dissolve — into bands. `palettegen` reads the actual frames and
        # `paletteuse` maps them onto what it found, which is the difference
        # between a usable GIF and an obviously broken one. `stats_mode=diff`
        # weights the palette toward what MOVES, which for an animatic is the
        # picture rather than the letterbox bars.
        # `split` is what makes it one pass: the same decoded stream feeds the
        # palette generator and the mapper, so the stills are not read twice.
        cmd += [
            "-an",
            "-filter_complex",
            f"fps={fps},split[gs][gm];[gs]palettegen=stats_mode=diff[gp];"
            f"[gm][gp]paletteuse=dither=bayer:bayer_scale=3",
            "-loop", "0",  # 0 means forever, which is what a GIF is for
        ]
    elif graph is None:
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
    if container != "gif":
        cmd += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(_CRF.get((quality or "high").lower(), _CRF["high"])),
            "-pix_fmt", "yuv420p",  # required for playback in browsers / QuickTime
            "-r", str(fps),
            "-movflags", "+faststart",
        ]
    cmd += [
        # The frames decide the length: a short audio file must not truncate the
        # video (which is what -shortest would do), and a long one must not run on.
        "-t", f"{total_ms / 1000:.3f}",
        "-progress", "pipe:1",
        tmp_path,
    ]

    _report(58, "Encoding video…")
    logger.info(
        "[animatic %s] encoding %d frame(s) in %d segment(s), %.1fs, %dx%d @%dfps as %s%s",
        job_id, len(usable), len(entries), total_ms / 1000, size[0], size[1], fps,
        container,
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
    return _summary(
        out_path, container, size_bytes, size, fps,
        duration_ms=total_ms, segment_count=len(entries), still_count=len(rendered),
        animated=animated, frames=usable, texts=texts, shapes=shapes,
        overlays=overlays, transitions=transitions, videos=videos,
        skipped=skipped, tracks=tracks,
    )


def _summary(
    out_path: str,
    container: str,
    size_bytes: int,
    size: tuple[int, int],
    fps: int,
    *,
    duration_ms: int,
    segment_count: int,
    still_count: int,
    animated: bool,
    frames: list[dict],
    texts,
    shapes,
    overlays,
    transitions,
    videos: list[dict],
    skipped: list[int],
    tracks: list[dict],
) -> dict:
    """What an export tells its caller about itself.

    One function because there are two exits now — the encoder's and the still's
    — and the job store reads these keys by name. A summary written twice is a
    summary where one copy quietly loses a field, which is the same mistake the
    export payload in `server/animatics.py` made for three phases.
    """
    return {
        "stopped": False,
        "video": out_path,
        # What was actually written. The download route needs it to serve the
        # right file with the right type, and the editor needs it to know
        # whether it has a video to play or a picture to show.
        "container": container,
        "duration_ms": duration_ms,
        # Pictures in the finished cut — NOT the number of segments encoded, which
        # is higher whenever text starts or ends part-way through a held image.
        "frame_count": len(frames),
        "segment_count": segment_count,
        # Which planner ran. Worth reporting: an animated export renders far more
        # stills and takes correspondingly longer, and when someone asks why an
        # export that used to take 20 seconds now takes three minutes, this is
        # the answer.
        "animated": animated,
        "still_count": still_count,
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
        "has_audio": bool(tracks),
        "audio_track_count": len(tracks),
        "size_bytes": size_bytes,
    }
