"""TRANSITION MATTES — the shape a reveal uncovers the arriving shot through.

⚠ THIS MODULE HAS A TWIN: `client/src/animatic/gl/shaders/mattes.js`. Every
function here has a counterpart there, written to the same formula in the same
order, so the Program monitor and the exported MP4 reveal the same shape. Read
that file's header for the design; this one repeats only what a reader of the
Python side needs.

⚠ A REVEAL TRANSITION IS A SECOND MASK ON THE INCOMING PICTURE, not a
compositing stage. That is the whole reason this module is small:

    a wipe at 50%  =  show the incoming picture where x < half the width
    a mask         =  show this picture where it is inside the region

Identical operation. So a matte is multiplied into the arriving picture's ALPHA
in `_transition_canvas`, one line away from where `apply_mask` already does the
same thing, and it is driven by the transition's progress instead of by
keyframes. What that buys is everything `_transition_canvas` already documents:
the incoming picture stays COMPOSITED OVER the outgoing one, so a clip's blend
mode, chroma key and per-clip mask all keep working through a transition. A
`mix(from, to)` stage — how gl-transitions does it — would have thrown all four
away.

WHERE THIS SITS relative to its two neighbours, because all three are twinned:

    animatic_render.py      the MODEL: which kinds exist, what parameters they
                            take, which matte each one draws through
    animatic_transitions.py this file — the matte's PIXELS
    animatic_effects.py     the look's pixels (effects, masks, blend modes)

⚠ SAMPLED AT PIXEL CENTRES, exactly as `mask_coverage` is, so a matte sits in
the same place at every export resolution. It is also what keeps every field
strictly BELOW 1, which is what lets progress 1 cover the whole frame with no
corner pixel left behind.

⚠ THE FIELDS ARE IN FRACTION SPACE, again as the mask is: an iris on a 16:9
frame is an ellipse in fractions and so comes out wider than it is tall, the
same way an ellipse mask and a `border-radius: 50%` shape already do. Making
this one aspect-correct while those two are not would be the surprising choice,
not the consistent one.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from animatic_effects import _as_float, _as_image, smoothstep
from animatic_render import (
    MATTE_KINDS,
    TRANSITION_DIRECTIONS,
    TRANSITION_PARAMS,
)

# Named rather than compared as strings in each field, so a direction added to
# `TRANSITION_DIRECTIONS` is a name here and not a silent fall-through.
_LEFT, _RIGHT, _UP, _DOWN = TRANSITION_DIRECTIONS


def _fract(x: np.ndarray) -> np.ndarray:
    """GLSL's `fract`. Written out because numpy has no single name for it."""
    return x - np.floor(x)


# ---------------------------------------------------------------------------
# The fields. Each returns a distance in 0–1: 0 where the matte opens FIRST, 1
# where it opens LAST. Progress then sweeps a threshold across it, which is what
# keeps every shape three lines long and softness a single shared line.
# ---------------------------------------------------------------------------
def _linear(px, py, direction, count):
    """A hard edge travelling across the frame — what a wipe has always been.

    `direction` is the way the EDGE TRAVELS — the same meaning the parameter has
    always had, so "right" starts at the left and sweeps rightwards. This
    replaced `_wipe_box` in animatic.py, which cropped the arriving picture to
    an integer pixel box; the edge now lands wherever a pixel CENTRE crosses the
    threshold, which is at most one column from where the box put it and is what
    makes it agree with the shader by construction rather than by coincidence.
    """
    if direction == _LEFT:
        return 1.0 - px
    if direction == _UP:
        return 1.0 - py
    if direction == _DOWN:
        return py
    return px


def _diagonal(px, py, direction, count):
    """The same edge held at 45 degrees, so it arrives from a CORNER.

    Halved because the two axes sum to 2 at the far corner and every field has
    to land in 0–1 for the shared threshold to mean the same thing.
    """
    if direction == _LEFT:
        return ((1.0 - px) + (1.0 - py)) * 0.5
    if direction == _UP:
        return (px + (1.0 - py)) * 0.5
    if direction == _DOWN:
        return ((1.0 - px) + py) * 0.5
    return (px + py) * 0.5


def _split(px, py, direction, count):
    """Barn doors: the arriving shot opens from the CENTRE LINE outwards."""
    c = py if direction in (_UP, _DOWN) else px
    return np.abs(c - 0.5) * 2.0


def _radial(px, py, direction, count):
    """A circle opening from the centre.

    Divided by the half-diagonal so the field reaches 1 in the CORNERS rather
    than at the edges — otherwise the last stretch of the window would have
    nothing left to uncover but the four corners.
    """
    return np.sqrt((px - 0.5) ** 2 + (py - 0.5) ** 2) / 0.70710678


def _diamond(px, py, direction, count):
    """The same iris under the Manhattan metric: a square on its point."""
    return np.abs(px - 0.5) + np.abs(py - 0.5)


def _box(px, py, direction, count):
    """And under the Chebyshev metric: a rectangle opening from the centre."""
    return np.maximum(np.abs(px - 0.5), np.abs(py - 0.5)) * 2.0


def _angular(px, py, direction, count):
    """A clock hand sweeping from twelve, clockwise.

    The y argument is flipped because frame space runs y DOWN, and the wrap is
    what turns arctan2's (-pi, pi] into a field running 0 at twelve the whole
    way round instead of jumping at nine o'clock.
    """
    a = np.arctan2(px - 0.5, 0.5 - py)
    return np.where(a < 0.0, a + 2.0 * np.pi, a) / (2.0 * np.pi)


def _blinds(px, py, direction, count):
    """`count` bands, each running its own little wipe at the same moment.

    `fract` is the whole trick: the band index is discarded and only the
    position WITHIN a band survives, so one field drives all of them.
    """
    c = py if direction in (_UP, _DOWN) else px
    if direction in (_LEFT, _UP):
        c = 1.0 - c
    return _fract(c * max(count, 1.0))


def _checker(px, py, direction, count):
    """Cells of one parity go in the FIRST half of the window, the rest second.

    Compressed into halves rather than run together, because two interleaved
    grids arriving at once is just a soft-edged wipe with extra arithmetic.
    """
    n = max(count, 1.0)
    parity = np.mod(np.floor(px * n) + np.floor(py * n), 2.0)
    return (parity + _fract(px * n)) * 0.5


# ⚠ KEYED BY THE NAMES IN `MATTE_KINDS`, and "none" is deliberately absent: it
# is the early-out in `matte_coverage`, exactly as `kind == 0` is in the shader.
_FIELDS = {
    "linear": _linear,
    "diagonal": _diagonal,
    "split": _split,
    "radial": _radial,
    "diamond": _diamond,
    "box": _box,
    "angular": _angular,
    "blinds": _blinds,
    "checker": _checker,
}


def matte_coverage(
    matte: str | None,
    params: dict | None,
    progress: float,
    size: tuple[int, int],
) -> np.ndarray | None:
    """How much of the ARRIVING picture each pixel shows, or None for no matte.

    None means "not a reveal" and is the answer for a dissolve, a dip and a
    slide, so the caller skips the work entirely rather than multiplying by a
    frame full of ones. Mirrors `matteCoverage`.

    ⚠ THE THRESHOLD TRAVELS FURTHER THAN 0–1, by the feather either side. That
    is what makes the matte exactly EMPTY at progress 0 and exactly FULL at
    progress 1 AT EVERY SOFTNESS. A transition has to be invisible at its own
    two ends — that is what lets it straddle a cut without anything appearing to
    jump — and ramping the threshold from 0 to 1 instead would leave half a
    feather showing at both ends, so the shot would jump on the frame either
    side of every soft transition.
    """
    if not matte or matte == "none" or matte not in _FIELDS:
        return None
    if matte not in MATTE_KINDS:  # a name the model no longer lists
        return None

    p = params or {}
    softness = max(0.0, float(p.get("softness", 0.0)))
    count = float(p.get("count", 0.0))
    direction = p.get("direction")
    m = max(0.0, min(1.0, float(progress)))

    width, height = size
    # Pixel CENTRES, the same as `mask_coverage`, so the matte sits in the same
    # place at every resolution — and so every field stays strictly below 1,
    # which is what lets the threshold at progress 1 cover the last corner.
    xs = ((np.arange(width, dtype=np.float32) + 0.5) / width)[None, :]
    ys = ((np.arange(height, dtype=np.float32) + 0.5) / height)[:, None]

    field = np.broadcast_to(
        np.asarray(_FIELDS[matte](xs, ys, direction, count), dtype=np.float32),
        (height, width),
    )

    edge = m * (1.0 + 2.0 * softness) - softness
    if softness <= 0.0:
        # A hard edge is a COMPARISON, not a degenerate smoothstep: GLSL's
        # smoothstep divides by (edge1 - edge0) and is undefined when they are
        # equal, so guessing an answer here would be guessing the shader's.
        coverage = (field < edge).astype(np.float32)
    else:
        coverage = 1.0 - smoothstep(edge - softness, edge + softness, field)
    return coverage[..., None].astype(np.float32)


def apply_matte(
    image: Image.Image,
    matte: str | None,
    params: dict | None,
    progress: float,
) -> Image.Image:
    """Multiply a full-frame RGBA layer's alpha by the matte. No matte, no copy.

    The same shape as `apply_mask`, and that is the point: a transition matte
    and a clip's own mask are the same operation applied one after the other,
    the clip's first and the transition's last.
    """
    coverage = matte_coverage(matte, params, progress, image.size)
    if coverage is None:
        return image
    rgb, alpha = _as_float(image)
    return _as_image(rgb, alpha * coverage)


def matte_defaults(kind: str) -> dict:
    """A kind's parameter defaults — a convenience for callers and tests."""
    return dict(TRANSITION_PARAMS.get(kind, {}))
