"""The LOOK of a clip — colour, LUT, chroma key, masks and blend modes.

⚠ THIS MODULE HAS A TWIN, and for once it is not another evaluator: it is a set
of GLSL fragment shaders in `client/src/animatic/gl/shaders/`. Every function
here has a counterpart there, written to the same formula in the same order, so
the Program monitor and the exported MP4 show the same grade.

They CANNOT be compared byte for byte. WebGL and Pillow use different
rasterisers, different float precision and different resampling, so a pixel diff
would fail forever and be switched off within a month. `tests/effects_parity_check.py`
compares them with a tolerance instead (mean |Δ| < 3/255, no pixel off by more
than 12/255) and `tests/effects_check.py` pins the Python side to exact golden
values, so a formula that drifts is caught on one side or the other.

WHERE EACH THING APPLIES, because it is not the same place for all three:

    effects   to the LAYER'S OWN PIXELS, before it is placed on the frame. A
              letterboxed picture must not have its bars colour-graded, and an
              overlay's grade must ride along when the overlay is moved.
    mask      in FRAME COORDINATES, after placing. A vignette or a spotlight is
              a region of the picture you are making, not of the file you fed
              in — and that is the reading under which a mask can be keyframed
              to sweep across a shot.
    blend     between the finished layer and everything already composited
              under it. Normal for every clip that doesn't say otherwise, which
              is every animatic that existed before this.

WHY NOT `ImageEnhance`: `ImageEnhance.Contrast` blends toward the MEAN
brightness of the particular image, which a fragment shader cannot know — the
preview and the export would diverge on every picture, by an amount that depends
on the picture. All three colour operations are therefore plain numpy with a
fixed pivot. `ImageEnhance.Brightness` and `.Color` happen to agree with what is
here (`Color` uses the same ITU-R 601 luma weights); `Contrast` does not, and
matching the shader matters more than matching Pillow's convenience wrapper.

The LUT is the exception: `PIL.ImageFilter.Color3DLUT` is real, is trilinear,
and is exactly what the shader does with a tiled 2D texture — so the .cube file
is the single source of truth and both sides simply read it.
"""

from __future__ import annotations

import logging
import os
import re

import numpy as np
from PIL import Image, ImageFilter

from animatic_render import (
    BLEND_MODES,
    DEFAULT_BLEND,
    DEFAULT_MASK,
    EFFECT_KINDS,
    MASK_KINDS,
    effect_params,
)

logger = logging.getLogger(__name__)

# ITU-R 601-2 luma. The same three numbers are in `saturation.js` and are what
# `Image.convert("L")` uses, which is why saturation 0 here and a greyscale
# conversion agree to the last bit.
LUMA = (0.299, 0.587, 0.114)

# Where the built-in .cube files live. A LUT is named, never inlined: the client
# and the exporter both read the same file, so there is no third copy of the
# numbers to drift.
LUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "luts")
# A .cube may declare any size; anything larger than this is a file we should
# not be loading into a browser texture either, so it is refused on both sides.
MAX_LUT_SIZE = 64


class LutError(ValueError):
    """A .cube file that cannot be read as a 3D LUT."""


# ---------------------------------------------------------------------------
# Small shared helpers — each one has a GLSL counterpart of the same name
# ---------------------------------------------------------------------------
def parse_colour(value: str, fallback: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    """'#rrggbb' → (r, g, b) in 0–1. Anything unreadable is `fallback`."""
    s = (value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return fallback
    try:
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return fallback


def smoothstep(edge0: float, edge1: float, x):
    """GLSL's `smoothstep`, to the letter.

    Written out rather than approximated because it is the only soft edge in
    this module: the chroma key's tolerance ramp and every feathered mask are
    both this curve, so a different S-curve here would show up as a halo the
    preview doesn't have.
    """
    span = edge1 - edge0
    if abs(span) < 1e-9:
        # Degenerate range: a step. GLSL is undefined here; both sides pick the
        # same answer explicitly rather than dividing by zero differently.
        return np.where(np.asarray(x) < edge0, 0.0, 1.0).astype(np.float32)
    t = np.clip((np.asarray(x, dtype=np.float32) - edge0) / span, 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _luma(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel luma, keeping the trailing axis so it broadcasts back over RGB."""
    return (
        rgb[..., 0:1] * LUMA[0] + rgb[..., 1:2] * LUMA[1] + rgb[..., 2:3] * LUMA[2]
    ).astype(np.float32)


def _as_float(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """An RGBA image as (rgb 0–1, alpha 0–1), both float32."""
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    return arr[..., :3].copy(), arr[..., 3:4].copy()


def _as_image(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    """(rgb, alpha) back to an RGBA image, rounded the way the GPU rounds."""
    out = np.concatenate(
        [np.clip(rgb, 0.0, 1.0), np.clip(alpha, 0.0, 1.0)], axis=-1
    )
    return Image.fromarray(np.round(out * 255.0).astype(np.uint8), "RGBA")


# ---------------------------------------------------------------------------
# LUTs
# ---------------------------------------------------------------------------
_LUT_CACHE: dict[str, tuple[int, list[float]]] = {}
_LUT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def lut_path(name: str) -> str | None:
    """The file behind a built-in LUT name, or None if there isn't one.

    The name is matched against a strict pattern before it is joined to a path,
    for the same reason `_image_path` does it server-side: a LUT name arrives
    inside a saved project and a project can be edited by hand.
    """
    if not name or not _LUT_NAME_RE.match(name):
        return None
    path = os.path.join(LUT_DIR, f"{name}.cube")
    return path if os.path.isfile(path) else None


def list_luts() -> list[str]:
    """Every built-in LUT, by name. The editor's dropdown is built from this."""
    if not os.path.isdir(LUT_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(LUT_DIR)
        if f.lower().endswith(".cube")
    )


def parse_cube(text: str) -> tuple[int, list[float]]:
    """A .cube file's text → (size, table), the table being size³ RGB triples.

    ⚠ TWIN of `parseCube` in `client/src/animatic/gl/lut.js`. Only the subset
    every grading tool actually writes is understood — LUT_3D_SIZE, comments,
    TITLE, DOMAIN_MIN/MAX — because a 1D LUT and a non-unit domain are two
    different features, and half-implementing them would silently grade wrong
    rather than refuse.

    Values are listed with RED CHANGING FASTEST, which is the one thing about
    the format that is easy to get backwards and impossible to see in an
    identity LUT. `tests/effects_check.py` grades a red-only ramp to catch it.
    """
    size = 0
    rows: list[float] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()[0].upper()
        if head == "LUT_3D_SIZE":
            try:
                size = int(line.split()[1])
            except (IndexError, ValueError) as e:
                raise LutError("LUT_3D_SIZE is not a number.") from e
            continue
        if head in ("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_1D_SIZE"):
            if head == "LUT_1D_SIZE":
                raise LutError("This is a 1D LUT; only 3D .cube files are supported.")
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            rows.extend(float(p) for p in parts)
        except ValueError:
            continue

    if size <= 1:
        raise LutError("The file has no usable LUT_3D_SIZE.")
    if size > MAX_LUT_SIZE:
        raise LutError(f"LUT_3D_SIZE {size} is larger than the {MAX_LUT_SIZE} limit.")
    if len(rows) != size**3 * 3:
        raise LutError(
            f"Expected {size ** 3} entries for size {size}, found {len(rows) // 3}."
        )
    return size, rows


def load_lut(name: str) -> tuple[int, list[float]] | None:
    """A built-in LUT by name, parsed and cached. None if it isn't there.

    A missing or broken LUT is a NO-OP, never an error: the animatic still
    exports, it simply exports ungraded. Losing a whole render over a file that
    was deleted from `luts/` would be the wrong trade.
    """
    if name in _LUT_CACHE:
        return _LUT_CACHE[name]
    path = lut_path(name)
    if not path:
        logger.warning("[effects] no LUT named '%s' — the grade is skipped", name)
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            table = parse_cube(fh.read())
    except (OSError, LutError) as e:
        logger.warning("[effects] LUT '%s' could not be read (%s) — skipped", name, e)
        return None
    _LUT_CACHE[name] = table
    return table


# ---------------------------------------------------------------------------
# The effects themselves. One function per kind, one shader chunk per function.
# ---------------------------------------------------------------------------
def _brightness(rgb: np.ndarray, params: dict) -> np.ndarray:
    # A plain multiply, exactly `ImageEnhance.Brightness`. 1.0 is unchanged.
    return rgb * float(params["amount"])


def _contrast(rgb: np.ndarray, params: dict) -> np.ndarray:
    # Pivoted on MID GREY, not on the image's own mean — see the module header.
    amount = float(params["amount"])
    return (rgb - 0.5) * amount + 0.5


def _saturation(rgb: np.ndarray, params: dict) -> np.ndarray:
    # Toward (or past) the ITU-R 601 grey. 0 is greyscale, >1 pushes colour.
    amount = float(params["amount"])
    grey = _luma(rgb)
    return grey + (rgb - grey) * amount


def _lut(rgb: np.ndarray, params: dict) -> np.ndarray:
    """Grade through a 3D LUT, mixed back by `amount`.

    Pillow's `Color3DLUT` is used rather than a hand-rolled trilinear lookup
    because it IS the trilinear lookup, in C, and the shader does the same
    interpolation on a tiled texture. The interesting part is the mix: `amount`
    lets a strong look be dialled back, which is how a LUT is used in practice
    and what makes it keyframable.
    """
    table = load_lut(str(params.get("name") or ""))
    amount = float(params["amount"])
    if table is None or amount <= 0:
        return rgb
    size, values = table
    graded = Image.fromarray(
        np.round(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8), "RGB"
    ).filter(ImageFilter.Color3DLUT(size, values, channels=3))
    out = np.asarray(graded, dtype=np.float32) / 255.0
    if amount >= 1:
        return out
    return rgb + (out - rgb) * amount


def _chroma(rgb: np.ndarray, alpha: np.ndarray, params: dict):
    """Key out one colour, and optionally pull its spill off what's left.

    The distance is measured in CHROMA ONLY (Cb/Cr), never in RGB: a green
    screen is lit unevenly, so brightness has to be allowed to vary while the
    hue is what identifies it. Keying in RGB is the classic way to get a subject
    with a hard black rim.

    `similarity` is where the key starts to bite and `smoothness` how wide the
    ramp is, which is the pair every keyer offers under one name or another.
    """
    key = np.array(parse_colour(str(params.get("color") or "#00ff00"), (0.0, 1.0, 0.0)),
                   dtype=np.float32)
    key_y = float(key[0] * LUMA[0] + key[1] * LUMA[1] + key[2] * LUMA[2])
    key_cb = (key[2] - key_y) * 0.5643
    key_cr = (key[0] - key_y) * 0.7132

    y = _luma(rgb)
    cb = (rgb[..., 2:3] - y) * 0.5643
    cr = (rgb[..., 0:1] - y) * 0.7132
    distance = np.sqrt((cb - key_cb) ** 2 + (cr - key_cr) ** 2)

    similarity = max(0.0, float(params["similarity"]))
    smoothness = max(1e-4, float(params["smoothness"]))
    keep = smoothstep(similarity, similarity + smoothness, distance)

    spill = float(params["spill"])
    if spill > 0:
        # Only where the key is biting: (1 - keep) is exactly "how much of this
        # pixel the key thinks is screen", so the desaturation fades out with it
        # instead of flattening the whole picture.
        pull = np.clip(spill * (1.0 - keep), 0.0, 1.0)
        rgb = rgb + (y - rgb) * pull
    return rgb, alpha * keep


_EFFECTS = {
    "brightness": _brightness,
    "contrast": _contrast,
    "saturation": _saturation,
    "lut": _lut,
}


def apply_effects(image: Image.Image, effects: list[dict] | None) -> Image.Image:
    """Run a clip's effect chain over one RGBA layer, in order.

    `effects` is the RESOLVED list off `scene_at` — every parameter already
    interpolated to this instant — so nothing here knows about keyframes.

    Order is the user's order, and it matters: a LUT after a saturation pull is
    a different picture from a saturation pull after a LUT. An unrecognised kind
    is skipped rather than refused, the same forgiveness `ease` and `clip_kind`
    already give, so a project written by a newer client still renders.
    """
    if not effects:
        return image.convert("RGBA")
    rgb, alpha = _as_float(image)
    for effect in effects:
        kind = (effect or {}).get("kind")
        if kind not in EFFECT_KINDS:
            continue
        params = effect_params(effect)
        if kind == "chroma":
            rgb, alpha = _chroma(rgb, alpha, params)
        else:
            rgb = _EFFECTS[kind](rgb, params)
        # Clamped BETWEEN steps, not only at the end. A shader writes to an
        # 8-bit target between passes and so clamps too; letting a value ride
        # at 1.4 into the next effect would give the export headroom the
        # preview does not have.
        rgb = np.clip(rgb, 0.0, 1.0)
    return _as_image(rgb, alpha)


# ---------------------------------------------------------------------------
# Masks
# ---------------------------------------------------------------------------
def mask_coverage(mask: dict | None, size: tuple[int, int]) -> np.ndarray | None:
    """How much of each pixel of the FRAME the mask lets through, or None.

    None means "no mask" and is the answer for every clip that doesn't carry
    one, so the caller can skip the work entirely rather than multiply by ones.

    Geometry is in fractions of the frame, `x`/`y` the CENTRE — the same
    convention as a shape, an overlay and a picture's pan, because it is placed
    with the same handles. The ellipse is therefore an ellipse in FRACTION
    space: on a 16:9 frame it is wider than it is tall, exactly as `.an-shape`
    with `border-radius: 50%` is.
    """
    if not mask:
        return None
    kind = mask.get("kind") or "none"
    if kind not in MASK_KINDS or kind == "none":
        return None

    width, height = size
    half_w = max(1e-4, abs(float(mask.get("w", DEFAULT_MASK["w"]))) / 2.0)
    half_h = max(1e-4, abs(float(mask.get("h", DEFAULT_MASK["h"]))) / 2.0)
    cx = float(mask.get("x", DEFAULT_MASK["x"]))
    cy = float(mask.get("y", DEFAULT_MASK["y"]))
    feather = max(1e-3, float(mask.get("feather", DEFAULT_MASK["feather"])))

    # Pixel CENTRES, so the mask sits in the same place at every resolution —
    # sampling at the corner shifts a feathered edge by half a pixel, which is
    # visible at 360p and would put the two sides out by more than the parity
    # tolerance allows.
    xs = (np.arange(width, dtype=np.float32) + 0.5) / width - cx
    ys = (np.arange(height, dtype=np.float32) + 0.5) / height - cy
    dx = xs[None, :] / half_w
    dy = ys[:, None] / half_h

    if kind == "ellipse":
        distance = np.sqrt(dx * dx + dy * dy)
    else:  # rect
        distance = np.maximum(np.abs(dx), np.abs(dy))

    # 1 inside, 0 outside, feathered across the edge. `feather` is expressed in
    # the same normalised units as the distance, so it scales with the mask
    # rather than with the frame — a small mask gets a proportionally small
    # softness, which is what looks right when one is animated.
    coverage = 1.0 - smoothstep(1.0 - feather, 1.0 + feather, distance)
    if mask.get("invert"):
        coverage = 1.0 - coverage
    return coverage[..., None].astype(np.float32)


def apply_mask(image: Image.Image, mask: dict | None) -> Image.Image:
    """Multiply a full-frame RGBA layer's alpha by the mask. No mask, no copy."""
    coverage = mask_coverage(mask, image.size)
    if coverage is None:
        return image
    rgb, alpha = _as_float(image)
    return _as_image(rgb, alpha * coverage)


# ---------------------------------------------------------------------------
# Blend modes
# ---------------------------------------------------------------------------
def _blend_rgb(base: np.ndarray, layer: np.ndarray, mode: str) -> np.ndarray:
    if mode == "multiply":
        return base * layer
    if mode == "screen":
        return 1.0 - (1.0 - base) * (1.0 - layer)
    if mode == "add":
        return np.minimum(1.0, base + layer)
    if mode == "darken":
        return np.minimum(base, layer)
    if mode == "lighten":
        return np.maximum(base, layer)
    if mode == "overlay":
        # Screen where the BASE is light, multiply where it is dark. Split on
        # the base (not the layer) — the other way round is "hard light", a
        # different mode, and the two are constantly confused.
        return np.where(
            base <= 0.5,
            2.0 * base * layer,
            1.0 - 2.0 * (1.0 - base) * (1.0 - layer),
        )
    return layer  # normal, and anything unrecognised


def blend_onto(base: Image.Image, layer: Image.Image, mode: str = DEFAULT_BLEND) -> Image.Image:
    """Composite one finished RGBA layer onto an RGB canvas.

    ⚠ THE ALPHA IS THE MIX, ALWAYS. Every mode is `base + (blend(base, layer) -
    base) * alpha`, so a fully transparent layer changes nothing whatever its
    mode, and a fully opaque one is the blend outright. That single rule is what
    makes a blend mode compose with opacity, with a chroma key and with a
    feathered mask without any of them needing to know about the others — and it
    is what `blend.js` does, term for term.

    "normal" short-circuits to Pillow's own paste, which is both faster and
    byte-identical to what this module did before blend modes existed.
    """
    mode = mode if mode in BLEND_MODES else DEFAULT_BLEND
    if mode == "normal":
        out = base.convert("RGB")
        out.paste(layer, (0, 0), layer)
        return out

    base_rgb = np.asarray(base.convert("RGB"), dtype=np.float32) / 255.0
    layer_rgb, alpha = _as_float(layer)
    blended = np.clip(_blend_rgb(base_rgb, layer_rgb, mode), 0.0, 1.0)
    out = base_rgb + (blended - base_rgb) * alpha
    return Image.fromarray(
        np.round(np.clip(out, 0.0, 1.0) * 255.0).astype(np.uint8), "RGB"
    )
