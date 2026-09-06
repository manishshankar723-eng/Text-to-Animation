"""The animatic SCENE MODEL, server side — what the frame looks like at time t.

⚠ THIS MODULE IS A TWIN of `client/src/animatic/scene.js`. Read that file's
docstring first: it explains why the scene model exists at all. Every rule here
— the easing curves, the half-open visibility test, keyframe times being
relative to the clip, the six-decimal rounding, holding rather than
extrapolating outside the first and last key — is duplicated on purpose so that
the Program monitor and the exported MP4 agree about the picture.

They are not kept in step by hand. `tests/render_parity.py` evaluates the same
fixture through both and fails on any difference. If you change one side, change
the other and run that test.

The transition half (`transition_window` / `transition_at`) is a twin too — of
`client/src/animatic/transitions.js`, which carries the design note explaining
why a transition is boundary-local and costs the timeline nothing.

`animatic.py` owns the ENCODE (ffmpeg, concat lists, audio mixing) and the
drawing primitives (`draw_texts`, `draw_shapes`, `draw_overlays`). This module
owns only the question "what values do those primitives get at time t", plus the
one piece of drawing that is new here: a frame's own pan/zoom, which has to
happen while the picture is being fitted onto the canvas rather than afterwards.
"""

from __future__ import annotations

import logging

from PIL import Image

logger = logging.getLogger(__name__)

# Six decimal places, matching PRECISION in scene.js. Two languages doing the
# same float maths drift in the last bits; without a shared rounding rule the
# parity test would fail on noise instead of on a real disagreement.
PRECISION = 6

EASINGS = ("linear", "hold", "ease-in", "ease-out", "ease-in-out")

# Which properties each kind of clip can animate. A property that is not listed
# is read straight off the clip and never interpolated, which is how `color`,
# `text` and `kind` stay themselves. Mirrors ANIMATABLE in scene.js.
ANIMATABLE: dict[str, tuple[str, ...]] = {
    "frame": ("scale", "x", "y", "opacity"),
    # ⚠ `scale` SITS WHERE THE PANE PUTS IT — third, above Width and Height,
    # which are the two it multiplies. This list is the order the pane lists its
    # rows in AND the order the timeline draws its diamond rows in, and those
    # two being the same list is the point. Inserting in the MIDDLE is safe
    # because the timeline draws a row only for a property that actually HAS
    # keys — a project saved before `scale` existed has none.
    "shape": ("x", "y", "scale", "w", "h", "opacity", "rotation"),
    "overlay": ("x", "y", "scale", "w", "h", "opacity", "rotation"),
    # A caption gained x/y in Phase 5. They are what the in/out presets in
    # `client/src/animatic/text_presets.js` animate — a title that slides up
    # into place is two keys on `y`, not a second animation system — and they
    # only mean anything when the clip is placed FREE. See `text_place`.
    # ⚠ AND `scale` SINCE 2026-08-24 — a zoom of the whole caption, drawn by
    # `draw_texts` as a font built at the scaled size over lines wrapped at the
    # resting one, so the wrap cannot shift mid-move. The browser does the same
    # thing with a CSS transform; see `ANIMATABLE.text` in `scene.js`.
    # ⚠ AND `rotation` SINCE PHASE 1 OF THE PRESET WORK — degrees CLOCKWISE, like
    # a shape's and like CSS `rotate()`, turning the caption about the SAME
    # anchor `scale` grows about (one CSS `transform-origin` serves both, so they
    # cannot be given different ones). `draw_texts` honours it by drawing the
    # measured block onto its own RGBA layer and turning that; see the ⚠ block
    # above `_rotate_about` in `animatic.py`.
    "text": ("opacity", "x", "y", "scale", "rotation"),
}

DEFAULTS = {
    "scale": 1.0,
    "x": 0.5,
    "y": 0.5,
    "w": 0.25,
    "h": 0.25,
    "opacity": 1.0,
    "rotation": 0.0,
}

# A frame's pan/zoom is expressed around the CENTRE of the picture, like every
# other geometry in this project, so x/y of 0.5 means "centred".
FRAME_DEFAULTS = {"scale": 1.0, "x": 0.5, "y": 0.5, "opacity": 1.0}

# A caption's own defaults. `y` is 0.85 rather than 0.5 because the thing a
# caption usually is, is a subtitle — so switching one to free placement puts it
# where it already was instead of jumping to the middle of the shot.
# ⚠ These are also the field defaults on `AnimaticTextClip`; the three must
# agree or a clip saved by the server resolves differently from one that never
# went through it.
# ⚠ `scale` IS 1.0 HERE FOR EVERY CAPTION EVER WRITTEN. `_resolve` reads this
# table BY PROPERTY NAME for each entry in `ANIMATABLE["text"]`, so adding a
# property to that tuple without adding its resting value here is a KeyError
# on the first caption in the film, not a quiet fallback.
# ⚠ AND `rotation` IS 0.0 HERE FOR EVERY CAPTION EVER WRITTEN, for exactly that
# reason — it was added to ANIMATABLE["text"] above and a row here is not
# optional. Mirrors TEXT_DEFAULTS in `scene.js`, where the same omission would
# resolve to `undefined` instead of raising, which is the two halves failing
# differently and therefore worse than either.
TEXT_DEFAULTS = {"x": 0.5, "y": 0.85, "opacity": 1.0, "scale": 1.0, "rotation": 0.0}

# Which table a kind's fallbacks come from. A kind that isn't listed uses the
# shared one, which is every kind that existed before frames and captions needed
# their own. Mirrors DEFAULTS_BY_KIND in scene.js.
DEFAULTS_BY_KIND = {"frame": FRAME_DEFAULTS, "text": TEXT_DEFAULTS}

# HOW a caption is positioned — and the reason x/y could be added without
# changing a single existing animatic.
#
#   "flow" — the original behaviour, and the default: the clip is dropped into
#            its `position` zone (top / middle / bottom) and captions sharing a
#            zone STACK, so two of them never land on top of each other. x/y
#            are resolved but unused.
#   "free" — the clip's centre is x/y, as a fraction of the frame, exactly like
#            a shape or an overlay. Nothing stacks; you placed it.
#
# Not animatable, for the same reason a clip's `kind` is not: half way between
# two layout algorithms is not a picture.
TEXT_PLACES = ("flow", "free")


def text_place(clip: dict) -> str:
    """How this caption is positioned. Mirrors `textPlace` in scene.js.

    An unrecognised value folds down to "flow" HERE rather than in each
    renderer, so the preview and the export cannot fold differently — the same
    rule `clip_kind`, `ease` and the transition kinds already follow.
    """
    place = (clip or {}).get("place") or "flow"
    return place if place in TEXT_PLACES else "flow"


def box_size(clip: dict) -> tuple[float, float]:
    """How big a shape or an overlay actually draws — w/h after `scale`.

    ⚠ EVERY PLACE THAT DRAWS ONE OF THESE BOXES MUST GO THROUGH HERE. There are
    five: `shapeFan` and `overlayRect` in the compositor, the two DOM hit-boxes
    in `AnimaticEditor`, and `draw_shapes` / `draw_overlays` in `animatic.py`. A
    single one left reading `clip["w"]` raw is a shape that is the wrong size in
    exactly one of the monitor, the handle you grab it by, and the MP4 — the
    worst shape that bug can take, because two of the three still agree.

    WHY `scale` EXISTS at all when w/h are already the size: because it is
    KEYFRAMABLE AS ONE PROPERTY. "Make this pop in" is one curve on `scale`;
    done with w and h it is two curves that must be kept identical by hand for
    ever, and the moment they drift the shape squashes as it grows.

    ⚠ TWIN of `boxSize` in `client/src/animatic/scene.js`.
    """
    clip = clip or {}
    # ⚠ ABSENT, None AND UNREADABLE ALL MEAN 1, NEVER 0 — see the twin for why.
    # `clip.get("scale", 1.0) or 0.0` was the first attempt and it is exactly
    # wrong: None became 0.0, so a clip a client wrote with a null scale drew as
    # nothing here while the monitor drew it full size.
    raw = clip.get("scale")
    if raw is None:
        scale = 1.0
    else:
        try:
            scale = abs(float(raw))
        except (TypeError, ValueError):
            scale = 1.0
    def side(key: str) -> float:
        try:
            return abs(float(clip.get(key, DEFAULTS[key]))) * scale
        except (TypeError, ValueError):
            return float(DEFAULTS[key]) * scale
    return side("w"), side("h")


# HOW a caption is kept readable over the art. Four kinds, and the last two are
# NOT the same thing — which is the whole reason "plain" exists:
#
#   "scrim" — a translucent bar behind the block. The default, and what a
#             caption dropped on a busy storyboard thumbnail needs.
#   "box"   — the same bar, nearly solid.
#   "none"  — no bar, but the glyphs get an AUTOMATIC dark outline, because
#             white text with nothing behind it is invisible on pale art. This
#             is the "Outline only" the picker has always offered.
#   "plain" — nothing whatsoever: no bar, no outline, just the letters in the
#             colour they are set in. ⚠ It is the one setting here that can make
#             a caption genuinely unreadable, and that is the point — a title
#             over a dark shot wants no furniture at all, and until this the
#             only way to get it was an outline you could see.
#
# Not animatable: half way between a box and no box is not a picture.
# ⚠ TWIN of `TEXT_BACKDROPS` in `client/src/animatic/scene.js`.
TEXT_BACKDROPS = ("scrim", "box", "none", "plain")


def text_backdrop(clip: dict) -> str:
    """How this caption is kept readable. Mirrors `textBackdrop` in scene.js.

    An unrecognised value folds down to "scrim" HERE rather than in each
    renderer, for the same reason `text_place` folds here — the preview and the
    export must not fold differently. It folds to the BACKDROP, never to
    "plain": a value nobody understands should leave the caption readable.
    """
    backdrop = (clip or {}).get("backdrop") or "scrim"
    return backdrop if backdrop in TEXT_BACKDROPS else "scrim"


# --- The LOOK: effects, mask, blend -----------------------------------------
# ⚠ Mirrors the same block in scene.js, and the pixel maths is a THIRD file:
# `animatic_effects.py` on this side, `client/src/animatic/gl/shaders/` on the
# other. This module owns only WHICH VALUES those get at time t.
#
# A look belongs to the two clip kinds that are PICTURES — a frame and an
# overlay. Shapes and captions are drawn as vector and text above the finished
# composite and have no pixels of their own to grade, so they carry no look and
# `_resolve` never gives them one.
LOOK_KINDS = ("frame", "overlay")

# Each effect's parameters and the value each falls back to. A parameter with a
# NUMBER for a default is animatable; one with a string is read straight off the
# clip and never interpolated, exactly as `text`, `color` and `kind` are —
# interpolating "which LUT" or "which colour is the screen" is meaningless.
EFFECT_PARAMS: dict[str, dict] = {
    "brightness": {"amount": 1.0},
    "contrast": {"amount": 1.0},
    "saturation": {"amount": 1.0},
    "lut": {"name": "", "amount": 1.0},
    "chroma": {
        "color": "#00ff00",
        "similarity": 0.35,
        "smoothness": 0.08,
        "spill": 0.0,
    },
    # ⚠ APPENDED, NEVER INSERTED. An effect reaches the shader as its INDEX in
    # this table, so putting a new kind in the middle would silently re-number
    # every kind after it and a saved project would come back graded by the
    # wrong effect. New ones go on the end.
    #
    # All six are POINT-WISE — a function of a single pixel and nothing else.
    # That is the admission price: the monitor grades in one fragment shader
    # pass with no neighbourhood available, so blur, sharpen and grain cannot
    # join this list without a second pass and an answer to "at which
    # resolution", which the preview and the export do not share.
    "exposure": {"stops": 0.0},
    "gamma": {"gamma": 1.0},
    "temperature": {"temperature": 0.0, "tint": 0.0},
    "hue": {"degrees": 0.0},
    "sepia": {"amount": 1.0},
    "posterize": {"levels": 8.0},
}
EFFECT_KINDS = tuple(EFFECT_PARAMS)

# The mask is ONE region per clip, in frame coordinates, `x`/`y` its centre —
# the same convention as a shape, an overlay and a picture's pan. "none" is the
# default and means the clip is not masked at all, which is every animatic that
# existed before this.
MASK_KINDS = ("none", "rect", "ellipse")
DEFAULT_MASK = {
    "kind": "none",
    "x": 0.5,
    "y": 0.5,
    "w": 0.5,
    "h": 0.5,
    "feather": 0.1,
    "invert": False,
}
# Which of a mask's fields can be keyframed. `kind` and `invert` cannot, for the
# same reason a clip's `kind` cannot: a half-inverted mask is not a picture.
MASK_ANIMATABLE = ("x", "y", "w", "h", "feather")

BLEND_MODES = (
    "normal",
    "multiply",
    "screen",
    "overlay",
    "add",
    "darken",
    "lighten",
)
DEFAULT_BLEND = "normal"

# How an effect's or a mask's parameter is named as a keyframe track. Flat
# strings, so `keyframes` stays exactly the dict-of-lists it has always been and
# every existing keyframe operation, timeline row and undo entry works on a
# graded clip with no changes at all.
#
#   "fx:<effect id>:<param>"   e.g. "fx:e3:amount"
#   "mask:<field>"             e.g. "mask:x"
#
# Keyed by the effect's OWN id rather than its position, so re-ordering the
# chain carries each effect's animation with it.
FX_PREFIX = "fx:"
MASK_PREFIX = "mask:"

# --- Clip kinds -------------------------------------------------------------
# ⚠ Mirrors CLIP_KINDS in scene.js. What a frame IS, rather than how it moves:
#
#   "image" — one still, held. Every animatic written before this was all of
#             these, and an absent `kind` reads as one, which is what makes an
#             old project open unchanged.
#   "video" — a range of a video file, played. `in_ms`/`out_ms` say WHICH range
#             and `speed` how fast, but the clip's place on the timeline is
#             still `duration_ms` — see `source_at` for why that way round.
#   "color" — a solid colour. No file at all: a slug, a flash, a hold.
CLIP_KINDS = ("image", "video", "color")
DEFAULT_SPEED = 1.0
MIN_SPEED = 0.1
MAX_SPEED = 10.0
# The colour a `color` clip falls back to. Black, so a clip whose colour has
# been lost reads as an intentional gap rather than a bright accident.
DEFAULT_CLIP_COLOR = "#000000"

# ⚠ NONE of `kind`, `in_ms`, `out_ms`, `speed` or `color` is animatable, and
# that is deliberate. They are read straight off the clip and never
# interpolated, exactly as `text` and the shape `kind` are. Keyframing a source
# range would mean "which part of the file am I in" changing
# non-monotonically, which no NLE offers and which would make `source_at`
# multi-valued.


def clip_kind(clip: dict) -> str:
    """What this clip is made of. Mirrors `clipKind` in scene.js.

    An unrecognised kind folds down to "image" HERE rather than in each
    renderer, so the preview and the export cannot fall back differently — the
    same rule `ease` and the transition kinds already follow. It is also what
    lets a project written by a newer client still open and still play.
    """
    kind = (clip or {}).get("kind") or "image"
    return kind if kind in CLIP_KINDS else "image"


def source_at(clip: dict, t_rel: float) -> float | None:
    """WHICH MOMENT OF THE SOURCE FILE a video clip is showing, in ms.

    None for anything that isn't video — a still and a colour card have no
    source time, and that is the value both languages report.

    ⚠ THE ONE DECISION THIS PHASE RESTS ON: `duration_ms` remains the clip's
    length ON THE TIMELINE, and `speed` widens or narrows the SOURCE WINDOW
    consumed inside it. Two seconds of timeline at speed 2 shows four seconds of
    footage; the clip does not get shorter, and NOTHING ELSE ON THE TIMELINE
    MOVES when the speed changes.

    The alternative — speed re-timing the clip — would shift every later cut,
    every caption timed against one, and every transition anchored to one, which
    is the same class of problem boundary-local transitions were designed to
    avoid. `frame_spans` is built on `duration_ms` and stays untouched.

    Past `out_ms` the clip HOLDS its last source frame rather than running on
    into footage the user trimmed off. That is the same rule keyframes follow
    outside their first and last key, and it is what stops a clip stretched
    longer than its source going black.
    """
    if clip_kind(clip) != "video":
        return None
    in_ms = max(0.0, float(clip.get("in_ms") or 0))
    speed = float(clip.get("speed") or DEFAULT_SPEED)
    if speed <= 0:
        speed = DEFAULT_SPEED
    at = in_ms + max(0.0, float(t_rel)) * speed
    out = clip.get("out_ms")
    if out is not None:
        # `out_ms` is EXCLUSIVE, like every other end in this project, so the
        # last moment actually shown is one millisecond inside it.
        at = min(at, max(in_ms, float(out) - 1))
    return _round(at)


# --- Transitions ------------------------------------------------------------
# ⚠ The twin of `client/src/animatic/transitions.js`. Read that file's header
# first: it explains the one decision everything else follows from — a
# transition is BOUNDARY-LOCAL. It blends over the tail of the outgoing picture
# and the head of the incoming one, d/2 either side of the cut, and takes
# nothing away from either. The timeline is exactly as long with transitions as
# without, so `frame_spans`, every cut position, ripple and rolling trims and
# any caption timed against a cut all keep working untouched.
TRANSITION_KINDS = (
    "dissolve",
    "dip",
    "wipe",
    "slide",
    # The matte-driven reveals. Every one is the SAME code path as a wipe — a
    # shape multiplied into the arriving picture's alpha — and they are separate
    # kinds rather than one kind with a `shape` parameter because the Effects
    # library files them as things you drag onto a cut.
    "diagonal",
    "split",
    "radial",
    "diamond",
    "box",
    "angular",
    "blinds",
    "checker",
)
DEFAULT_TRANSITION_MS = 600
MIN_TRANSITION_MS = 100
MAX_TRANSITION_MS = 10_000

# Each transition's parameters and the value each falls back to. ⚠ TWIN of
# `TRANSITION_PARAMS` in transitions.js — read that file's note for what the two
# defaults MEAN; the short version is that both reproduce the behaviour that
# already shipped, so no existing animatic changes.
#
# A parameter with a STRING default is read straight off the transition and
# never interpolated, which is all of them so far — "half way between left and
# up" is not a direction. A numeric one would be animatable in the same breath.
TRANSITION_PARAMS: dict[str, dict] = {
    "dissolve": {},
    "dip": {"color": ""},
    "wipe": {"direction": "right", "softness": 0.0},
    "slide": {"direction": "left"},
    "diagonal": {"direction": "right", "softness": 0.0},
    "split": {"direction": "right", "softness": 0.0},
    "radial": {"softness": 0.0},
    "diamond": {"softness": 0.0},
    "box": {"softness": 0.0},
    "angular": {"softness": 0.0},
    "blinds": {"direction": "right", "count": 6.0, "softness": 0.0},
    "checker": {"count": 6.0, "softness": 0.0},
}

# Which way a wipe's edge sweeps, or a slide's pictures travel.
TRANSITION_DIRECTIONS = ("left", "right", "up", "down")

# The parameters whose value is an ENUM rather than free text. An unrecognised
# value folds down to the default HERE, in the resolver, so the preview and the
# export cannot fold differently — the rule `kind` and `ease` already follow.
TRANSITION_PARAM_CHOICES: dict[str, tuple] = {"direction": TRANSITION_DIRECTIONS}

# The same rule for the NUMERIC parameters: clamped HERE so a softness of -3 or
# a count of 0 cannot mean one thing in the monitor and another in the export.
# Keyed by parameter NAME, because `softness` means the same thing on every kind
# that offers it. ⚠ TWIN of `TRANSITION_PARAM_RANGE` in transitions.js.
TRANSITION_PARAM_RANGE: dict[str, tuple] = {
    "softness": (0.0, 1.0),
    "count": (2.0, 64.0),
}

# ---------------------------------------------------------------------------
# MATTES — the shape a REVEAL transition uncovers the arriving shot through
# ---------------------------------------------------------------------------
# ⚠ A REVEAL IS A SECOND MASK ON THE INCOMING PICTURE, not a compositing stage.
# The full reasoning is at the top of `client/src/animatic/gl/shaders/mattes.js`
# and repeated in `animatic_transitions.py`; the short version is that a wipe at
# 50% and a mask are the same operation, so a matte is multiplied into the
# arriving picture's alpha right next to the existing mask multiply. Blend
# modes, chroma keys and per-clip masks all keep working, and the exporter needs
# no second compositing path.
#
# ⚠ "none" IS INDEX 0 and means no matte, matching `MASK_KINDS`. The shader
# tests `kind == 0` as its early out, so the ORDER of this tuple is load-bearing
# and is compared against the shader by `tests/effects_parity_check.py`.
#
# ⚠ TWIN of `MATTE_KINDS` in transitions.js.
MATTE_KINDS = (
    "none",
    "linear",
    "diagonal",
    "split",
    "radial",
    "diamond",
    "box",
    "angular",
    "blinds",
    "checker",
)

# Which matte each kind reveals through. A kind NOT in here does not use one:
# `dissolve` fades, `dip` veils and `slide` moves the geometry, and none of the
# three is a reveal. `wipe` maps to `linear` rather than being renamed, because
# "wipe" is the name it already has in every saved project.
#
# ⚠ TWIN of `TRANSITION_MATTE` in transitions.js.
TRANSITION_MATTE: dict[str, str] = {
    "wipe": "linear",
    "diagonal": "diagonal",
    "split": "split",
    "radial": "radial",
    "diamond": "diamond",
    "box": "box",
    "angular": "angular",
    "blinds": "blinds",
    "checker": "checker",
}


def transition_matte(kind: str) -> str:
    """The matte a resolved kind draws through, or "none". Mirrors `transitionMatte`."""
    return TRANSITION_MATTE.get(kind or "", "none")


def transition_kind(transition: dict) -> str:
    """An unrecognised kind is a dissolve. Mirrors `transitionKind`."""
    kind = (transition or {}).get("kind")
    return kind if kind in TRANSITION_KINDS else "dissolve"


def transition_params(transition: dict) -> dict:
    """One transition's parameters with every default filled in.

    Mirrors `transitionParams`. Resolved against the FOLDED kind, not the stored
    one, so a transition whose kind this build has never heard of gets a
    dissolve's parameters rather than a newer kind's.
    """
    defaults = TRANSITION_PARAMS.get(transition_kind(transition), {})
    stored = (transition or {}).get("params") or {}
    out: dict = {}
    for name, fallback in defaults.items():
        value = stored.get(name)
        choices = TRANSITION_PARAM_CHOICES.get(name)
        if isinstance(fallback, str):
            if choices is not None:
                out[name] = value if value in choices else fallback
            else:
                out[name] = value if isinstance(value, str) else fallback
        else:
            try:
                num = float(value)
            except (TypeError, ValueError):
                num = float(fallback)
            # Clamped but NOT rounded: `scene_signature` already formats every
            # number to the shared precision, and rounding here as well would be
            # a second place for the two languages to round differently.
            low_high = TRANSITION_PARAM_RANGE.get(name)
            if low_high is not None:
                num = min(low_high[1], max(low_high[0], num))
            out[name] = num
    return out


def _round(value: float) -> float:
    return round(float(value), PRECISION)


def ease(kind: str, u: float) -> float:
    """The easing curves, identical to `ease()` in scene.js."""
    if kind == "hold":
        # A step: the value does not move until the NEXT keyframe is reached.
        return 0.0
    if kind == "ease-in":
        return u * u * u
    if kind == "ease-out":
        return 1 - (1 - u) ** 3
    if kind == "ease-in-out":
        return 4 * u * u * u if u < 0.5 else 1 - ((-2 * u + 2) ** 3) / 2
    return u


def value_at(clip: dict, prop: str, t_rel: float, fallback: float) -> float:
    """Resolve one property of one clip at `t_rel` ms into that clip.

    Keyframe times are RELATIVE to the clip's own start, which is what lets a
    clip be dragged along the timeline without its animation sliding out from
    under it. Outside the first and last key the value HOLDS — extrapolating
    would send a clip flying off screen the moment it is trimmed longer.
    """
    base = clip.get(prop)
    if base is None:
        base = fallback

    track = (clip.get("keyframes") or {}).get(prop)
    if not isinstance(track, list) or not track:
        return base

    keys = sorted(track, key=lambda k: k.get("t") or 0)
    if len(keys) == 1:
        return _round(keys[0].get("v", base))

    first, last = keys[0], keys[-1]
    if t_rel <= (first.get("t") or 0):
        return _round(first.get("v", base))
    if t_rel >= (last.get("t") or 0):
        return _round(last.get("v", base))

    for a, b in zip(keys, keys[1:]):
        at = a.get("t") or 0
        bt = b.get("t") or 0
        if t_rel < at or t_rel >= bt:
            continue
        span = bt - at
        if span <= 0:
            return _round(b.get("v", base))
        u = (t_rel - at) / span
        av = float(a.get("v", base))
        bv = float(b.get("v", base))
        return _round(av + (bv - av) * ease(a.get("ease") or "linear", u))

    return _round(last.get("v", base))


# ---------------------------------------------------------------------------
# The look: which effect parameters exist, and what they are at time t
# ---------------------------------------------------------------------------
def effect_key(effect: dict, index: int) -> str:
    """The id a keyframe track names this effect by. Mirrors `effectKey`.

    Falls back to the position when an effect carries no id, so a chain written
    by hand still resolves rather than collapsing every effect onto one key.
    """
    return str((effect or {}).get("id") or index)


def effect_params(effect: dict) -> dict:
    """One effect's parameters with every default filled in.

    Used on both a STORED effect (where a parameter may be missing because the
    project predates it) and a RESOLVED one (where they are all present), so
    `animatic_effects.py` never has to ask whether a key exists.
    """
    kind = (effect or {}).get("kind")
    defaults = EFFECT_PARAMS.get(kind, {})
    stored = (effect or {}).get("params") or {}
    out = {}
    for name, fallback in defaults.items():
        value = stored.get(name)
        if isinstance(fallback, str):
            out[name] = str(value) if isinstance(value, str) else fallback
        else:
            try:
                out[name] = float(value)
            except (TypeError, ValueError):
                out[name] = float(fallback)
    return out


def look_props(clip: dict) -> list[str]:
    """Every keyframable property this clip's LOOK adds, by track name.

    Dynamic, unlike `ANIMATABLE`, because it depends on which effects the clip
    is carrying — which is the whole reason effect parameters are addressed by a
    flat string rather than by a fixed list.
    """
    props: list[str] = []
    for index, effect in enumerate(clip.get("effects") or []):
        kind = (effect or {}).get("kind")
        if kind not in EFFECT_KINDS:
            continue
        key = effect_key(effect, index)
        for name, fallback in EFFECT_PARAMS[kind].items():
            if not isinstance(fallback, str):
                props.append(f"{FX_PREFIX}{key}:{name}")
    mask = clip.get("mask") or {}
    if (mask.get("kind") or "none") in MASK_KINDS and (mask.get("kind") or "none") != "none":
        props.extend(f"{MASK_PREFIX}{name}" for name in MASK_ANIMATABLE)
    return props


def resolve_look(clip: dict, t_rel: float) -> dict:
    """The clip's effects, mask and blend mode at `t_rel`. Mirrors `resolveLook`.

    Returns the three fields ALWAYS, even when the clip carries none of them —
    an empty chain, a mask of kind "none" and "normal". Leaving them off when
    absent would make the resolved scene a different SHAPE on the two sides
    (`undefined` in JS is dropped by JSON, a missing key in Python is not) and
    `tests/render_parity.py` would then be comparing two things it thinks are
    equal for the wrong reason.

    An effect whose kind this build has never heard of is DROPPED here rather
    than passed on — the same fold-down `clip_kind` and `ease` do, in the same
    place, so the preview and the export skip it together. It stays in the saved
    project untouched and works again in a build that knows it.
    """
    effects = []
    for index, effect in enumerate(clip.get("effects") or []):
        kind = (effect or {}).get("kind")
        if kind not in EFFECT_KINDS:
            continue
        key = effect_key(effect, index)
        stored = effect_params(effect)
        params = {}
        for name, fallback in EFFECT_PARAMS[kind].items():
            if isinstance(fallback, str):
                params[name] = stored[name]
            else:
                params[name] = value_at(
                    clip, f"{FX_PREFIX}{key}:{name}", t_rel, stored[name]
                )
        effects.append({"id": key, "kind": kind, "params": params})

    stored_mask = clip.get("mask") or {}
    kind = stored_mask.get("kind") or "none"
    if kind not in MASK_KINDS:
        kind = "none"
    mask = {"kind": kind, "invert": bool(stored_mask.get("invert"))}
    for name in MASK_ANIMATABLE:
        base = stored_mask.get(name)
        try:
            base = float(base)
        except (TypeError, ValueError):
            base = float(DEFAULT_MASK[name])
        mask[name] = (
            value_at(clip, f"{MASK_PREFIX}{name}", t_rel, base)
            if kind != "none"
            else base
        )

    blend = clip.get("blend") or DEFAULT_BLEND
    return {
        "effects": effects,
        "mask": mask,
        "blend": blend if blend in BLEND_MODES else DEFAULT_BLEND,
    }


def _resolve(clip: dict, kind: str, t_rel: float) -> dict:
    out = dict(clip)
    defaults = DEFAULTS_BY_KIND.get(kind, DEFAULTS)
    for prop in ANIMATABLE.get(kind, ()):
        out[prop] = value_at(clip, prop, t_rel, defaults[prop])
    if kind in LOOK_KINDS:
        out.update(resolve_look(clip, t_rel))
    # ⚠ Set EXPLICITLY, for the same reason `kind` and `color` are on a picture:
    # a caption that never chose a placement would be a missing key here and
    # `undefined` in JS (which JSON drops), so `tests/render_parity.py` would be
    # comparing two different shapes and passing for the wrong reason.
    if kind == "text":
        out["place"] = text_place(clip)
    return out


def frame_track(frame: dict) -> int:
    """Which PICTURE TRACK a clip is on. Mirrors `frameTrack` in scene.js.

    0 is the base track — the bottom of the stack, and where every clip written
    before tracks existed lives. A higher number is drawn OVER a lower one, so a
    gap on an upper track shows whatever is on the track below it.
    """
    try:
        n = int(float((frame or {}).get("track") or 0))
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


# ---------------------------------------------------------------------------
# WHAT DRAWS OVER WHAT — one z-scale for every visual row.
# ⚠ TWIN of `client/src/animatic/lane_order.js`. The monitor reads the same order
# this does or the preview lies about the film; pinned name for name in
# `tests/lane_reorder_check.py`.
# ---------------------------------------------------------------------------
# ⚠ THESE FOUR NUMBERS ARE THE OLD HARD-CODED ORDER, WRITTEN DOWN AS DATA. The
# renderers used to draw pictures, then shapes, then overlay pictures, then text,
# and nothing could reach that sequence — so a row could be restacked among its
# own kind and nowhere else. A picture row's rank is its TRACK NUMBER (0…15, which
# is why the cap matters: a picture row must never out-rank a shape row), then
# shapes at 100, overlays at 200, text at 300. Change one and you change what
# every animatic saved before `lane_order` looks like.
PICTURE_RANK_CAP = 15  # `AnimaticFrame.track`'s own cap — a track's rank IS its number
_FALLBACK_RANK = {"shape": 100, "image": 200, "text": 300}


def clip_lane_token(kind: str, clip: dict) -> str:
    """The row one clip sits on, as a token. Mirrors `clipLaneToken` in scene.js.

    ⚠ "overlay" IS THE SCENE'S WORD AND "image" IS THE ROW'S — one is the clip
    kind the renderers branch on, the other is the lane kind `lane_order`,
    `hidden_lanes` and `locked_lanes` all use. Folding them here is what stops a
    second spelling of one row appearing in any of those lists.
    """
    if kind in ("picture", "frame", "frames"):
        return f"frames:{frame_track(clip or {})}"
    lane = "image" if kind == "overlay" else kind
    return f"{lane}:{(clip or {}).get('layer_id') or ''}"


def default_lane_rank(token: str) -> int:
    """Where a row sits when the saved order has never heard of it."""
    if not isinstance(token, str) or not token:
        return _FALLBACK_RANK["text"]
    if token.startswith("frames:"):
        try:
            n = int(float(token[len("frames:"):]))
        except (TypeError, ValueError):
            return 0
        return max(0, min(PICTURE_RANK_CAP, n))
    kind = token.split(":", 1)[0]
    # ⚠ AN UNKNOWN KIND RANKS AS TEXT, i.e. on top — the same rule an unknown
    # transition, effect or shape kind follows here. A row a newer client invented
    # draws over the film instead of vanishing under it, which is the failure you
    # can see and therefore report.
    return _FALLBACK_RANK.get(kind, _FALLBACK_RANK["text"])


def lane_rank(token: str, order: list[str] | None) -> int:
    """A row's rank — higher draws later, i.e. over. Mirrors `laneRank`.

    ⚠ AN EMPTY `order` REPRODUCES THE OLD SEQUENCE EXACTLY, and that is the whole
    migration: every animatic saved before the restack gesture existed has no
    saved order, falls through to `default_lane_rank`, and sorts into pictures →
    shapes → overlays → text.

    ⚠ UNLISTED MEANS ON TOP. A row added after a restack is not in the list, and
    the only rules simple enough to be identical in three renderers are "above
    everything listed" and "below everything listed" — below would hide a new row
    behind the pictures. It is also why the CAPTIONS row needs no special case
    anywhere: it is never written into the list (it cannot be dragged), so it is
    always unlisted, so it is always on top.
    """
    list_ = order or []
    try:
        i = list_.index(token)
    except ValueError:
        return len(list_) + default_lane_rank(token)
    return len(list_) - 1 - i


def bottom_picture_track(tracks: list[int], order: list[str] | None) -> int | None:
    """Which picture track is physically the bottom of the stack right now.

    Mirrors `bottomPictureTrack` in lane_order.js — read that docstring, it
    carries the bug report and the reasoning. `tracks` is every picture track
    number in use (typically `picture_tracks(frames)`, which always includes 0).
    `None` for an empty list, which a real project never passes.

    ⚠ WITH NO SAVED ORDER THIS IS ALWAYS 0 — track 0's rank is its own number,
    which is the lowest any picture track can have. So a project nobody has
    restacked is blanked on exactly the track it always was.
    """
    best: int | None = None
    best_rank = None
    for track in tracks or []:
        rank = lane_rank(f"frames:{track}", order)
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = track
    return best


def stack_key(order: list[str] | None) -> str:
    """Is this project restacked at all? Mirrors `stackKey`. "" when it is not."""
    list_ = order or []
    return "|".join(list_) if list_ else ""


def ordered_layers(
    project: dict,
    pictures: list[dict],
    shapes: list[dict],
    overlays: list[dict],
    texts: list[dict],
) -> list[dict]:
    """The draw order of one moment, bottom first. Mirrors `orderedLayers`.

    `{"kind", "index", "z"}` per visible clip, where `index` points into the
    scene's list of that kind. Pointers rather than copies: two copies of one clip
    in one scene is two things to keep in step.

    ⚠ ONE ENTRY PER CLIP, NOT PER ROW, and that is what makes the migration exact.
    Clips of one kind on one row tie on `z`, the sort is stable, and the tie-break
    is the clip's place in its own array — so with no saved order this comes out as
    the four old sequences, one after another, each in its original array order.
    Grouping by ROW would have reordered clips WITHIN a kind whenever two rows'
    clips interleave in the array: a silent restack of a project nobody touched.
    """
    order = ((project.get("settings") or {}).get("lane_order")) or []
    rows: list[tuple[int, int, str, int]] = []
    for kind, clips in (
        ("picture", pictures),
        ("shape", shapes),
        ("overlay", overlays),
        ("text", texts),
    ):
        for index, clip in enumerate(clips or []):
            z = lane_rank(clip_lane_token(kind, clip), order)
            rows.append((z, len(rows), kind, index))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [{"kind": kind, "index": index, "z": z} for z, _seq, kind, index in rows]


def layer_runs(layers: list[dict]) -> list[dict]:
    """The draw order as RUNS — adjacent entries of one kind folded together.

    Mirrors `layerRuns` in scene.js. `[{"kind", "indices"}]`, bottom first.

    ⚠ RUNS, NOT KINDS. `draw_texts` measures every caption sharing a zone and
    stacks them down it, so it needs them in ONE call or two subtitles land on
    each other — but only captions that are actually ADJACENT in the stack may be
    grouped, because a text row with a picture row above it is behind that picture
    and cannot be painted in the same pass as a text row in front of it. With no
    saved order every text row is adjacent, so this yields one text run and the
    single call the exporter always made.

    ⚠ A PICTURE IS ALWAYS ITS OWN RUN — two tracks are two composites with their
    own transitions, effects and blends.
    """
    runs: list[dict] = []
    for item in layers or []:
        if runs and runs[-1]["kind"] == item["kind"] and item["kind"] != "picture":
            runs[-1]["indices"].append(item["index"])
        else:
            runs.append({"kind": item["kind"], "indices": [item["index"]]})
    return runs


def frame_spans(frames: list[dict], end_ms: int | None = None) -> tuple[list[dict], int]:
    """Where each frame sits — ONE SPAN PER FRAME, IN LIST ORDER.

    Mirrors `frameSpans` in scene.js; read that docstring, it carries the design.
    The three rules that matter here:

    ⚠ A PICTURE IS PLACED BY `start_ms` ON ITS OWN TRACK, not by adding up the
    clips before it. That is the whole of the multi-track change — a clip moves
    when you move it and at no other time.

    ⚠ A MISSING `start_ms` MEANS "AFTER THE LAST CLIP ON MY TRACK". Every animatic
    written before this carries no starts at all and sits on one track, so that
    rule lays it out exactly as the old running total did.

    ⚠ THE SPANS STAY PARALLEL TO `frames` — `spans[i]` is the i-th frame's — and
    `server/animatics.py` indexes it that way.

    `end_ms` holds the picture that ENDS LAST out to that moment (the topmost of
    those if two tie), which is what makes an export cover a music bed that
    outlasts the pictures instead of stopping dead on the final image.
    """
    spans: list[dict] = []
    clock: dict[int, int] = {}
    for i, frame in enumerate(frames):
        track = frame_track(frame)
        length = max(100, int(frame.get("duration_ms") or 2000))
        at = frame.get("start_ms")
        if at is None:
            start = clock.get(track, 0)
        else:
            try:
                start = max(0, int(round(float(at))))
            except (TypeError, ValueError):
                start = clock.get(track, 0)
        spans.append({"start": start, "end": start + length, "index": i, "track": track})
        clock[track] = max(clock.get(track, 0), start + length)
    total = max((s["end"] for s in spans), default=0)
    if spans and end_ms and end_ms > total:
        last = max(spans, key=lambda s: (s["end"], s["track"]))
        last["end"] = end_ms
        total = end_ms
    return spans, total


def _stack_at(spans: list[dict], t: float) -> list[dict]:
    """The span showing on each track at `t`, BOTTOM TRACK FIRST.

    Mirrors `stackAt` in scene.js. ⚠ ONE CLIP PER TRACK, AND THE LATER ONE WINS
    where two overlap — free placement makes an overlap possible where a
    butt-jointed sequence could not, and "whichever starts later is the one you
    just put there" is the only tie-break a person can predict.
    """
    by_track: dict[int, dict] = {}
    for span in spans:
        if not (span["start"] <= t < span["end"]):
            continue
        held = by_track.get(span["track"])
        if (
            held is None
            or span["start"] > held["start"]
            or (span["start"] == held["start"] and span["index"] > held["index"])
        ):
            by_track[span["track"]] = span
    return [by_track[k] for k in sorted(by_track)]


def picture_tracks(frames: list[dict]) -> list[int]:
    """Every picture track the project uses, lowest first. Always includes 0."""
    seen = {0}
    for frame in frames or []:
        seen.add(frame_track(frame))
    return sorted(seen)


def transition_window(frames: list[dict], spans: list[dict], transition: dict) -> dict | None:
    """Where one transition sits, or None if it can't be placed.

    Mirrors `transitionWindow` in transitions.js. None covers every way a
    transition can be INERT rather than wrong: it names a frame that has been
    deleted, or it hangs off the last frame, where there is nothing to cut to.
    Those are left in the project rather than treated as errors.

    ⚠ THE CLAMP IS WHAT KEEPS `transition_at` SINGLE-VALUED. A transition is
    capped at the SHORTER of the two holds it joins, so each half-window is at
    most half of the shorter picture. Two transitions either side of one frame
    can meet in the middle but can never overlap, and no moment is ever inside
    two of them.
    """
    after_id = (transition or {}).get("after_frame_id")
    if not after_id:
        return None
    from_index = next(
        (i for i, f in enumerate(frames) if f.get("id") == after_id), None
    )
    if from_index is None or from_index >= len(spans):
        return None

    a = spans[from_index]
    # ⚠ THE NEXT CLIP ON THE SAME TRACK, AND IT HAS TO BUTT UP AGAINST THIS ONE.
    # It used to be `spans[from_index + 1]`, which was exact while the picture
    # track was one sequence laid end to end and is wrong twice over now that
    # clips are placed freely on tracks: the next clip in the LIST may be on
    # another track, and two clips can be neighbours without touching. A
    # TRANSITION HAPPENS ON A CUT and there is no edit point in a gap — so no cut,
    # no transition. Inert rather than wrong, exactly like one on the last clip.
    b = None
    for span in spans:
        if span["index"] == a["index"] or span["track"] != a["track"]:
            continue
        if span["start"] != a["end"]:
            continue
        if b is None or span["index"] < b["index"]:
            b = span
    if b is None:
        return None
    shorter = min(a["end"] - a["start"], b["end"] - b["start"])
    duration_ms = max(
        MIN_TRANSITION_MS,
        min(
            int(round(float(transition.get("duration_ms") or DEFAULT_TRANSITION_MS))),
            MAX_TRANSITION_MS,
            shorter,
        ),
    )
    cut = a["end"]
    return {
        "id": transition.get("id"),
        # Which track this cut is on — `scene_at` resolves one track at a time and
        # must not put this track's dissolve over another track's picture.
        "track": a["track"],
        # An unknown kind falls back HERE rather than in each renderer, so the
        # preview and the export can't fall back differently. Same rule as ease.
        "kind": transition_kind(transition),
        # Resolved here too, and for the same reason: ONE place decides what a
        # half-written transition means, and both renderers read the answer.
        "params": transition_params(transition),
        "from_index": a["index"],
        "to_index": b["index"],
        "cut_ms": cut,
        "duration_ms": duration_ms,
        "start_ms": cut - duration_ms / 2,
        "end_ms": cut + duration_ms / 2,
    }


def transition_windows(project: dict, spans: list[dict]) -> list[dict]:
    """Every placeable transition, in project order."""
    frames = project.get("frames") or []
    out = []
    for transition in project.get("transitions") or []:
        window = transition_window(frames, spans, transition)
        if window is not None:
            out.append(window)
    return out


def transition_at(
    project: dict, t_ms: float, spans: list[dict], track: int | None = None
) -> dict | None:
    """The transition covering `t_ms`, and how far through it we are.

    `mix` runs 0 → 1 across the whole window: 0 is "all outgoing picture", 1 is
    "all incoming". Half-open at both ends like every other visibility test
    here. Two transitions written onto the same cut is a project that shouldn't
    exist (the editor replaces rather than appends); the first one wins.

    `track` narrows it to the cuts on one picture track, which is what `scene_at`
    asks: a transition belongs to an edit point, an edit point belongs to a track,
    and a dissolve on the track above must not be drawn over the one below.
    """
    frames = project.get("frames") or []
    t = float(t_ms or 0)
    for transition in project.get("transitions") or []:
        window = transition_window(frames, spans, transition)
        if window is None:
            continue
        if track is not None and window["track"] != track:
            continue
        if t < window["start_ms"] or t >= window["end_ms"]:
            continue
        return {
            **window,
            "mix": _round((t - window["start_ms"]) / window["duration_ms"]),
        }
    return None


def _alive(clip: dict, t: float) -> bool:
    """On screen from its start UP TO BUT NOT INCLUDING its end.

    The same half-open rule decides which frame is showing, so a cut lands on
    exactly one picture and never on two.
    """
    start = max(0, int(clip.get("start_ms") or 0))
    end = start + max(100, int(clip.get("duration_ms") or 0))
    return start <= t < end


def _picture_at(frames: list[dict], spans: list[dict], index: int, t: float) -> dict:
    """One frame, resolved at `t` and stamped with where it sits in the sequence.

    `kind`, `color` and `source_ms` are set EXPLICITLY rather than left to ride
    along on the clip. A clip that carries none of them would otherwise be
    absent on the Python side and `undefined` on the JS side, which JSON drops —
    and the parity test would then be comparing two different shapes and passing
    for the wrong reason.
    """
    clip = frames[index]
    span = spans[index]
    t_rel = t - span["start"]
    picture = _resolve(clip, "frame", t_rel)
    picture["index"] = index
    picture["start_ms"] = span["start"]
    picture["end_ms"] = span["end"]
    picture["kind"] = clip_kind(clip)
    picture["color"] = clip.get("color") or DEFAULT_CLIP_COLOR
    # Which frame of the source file is on screen. None for a still or a colour
    # card — they have no source time — and the single most important number
    # this phase added, because it is what makes a video clip a moving picture
    # rather than one still stretched over its whole hold.
    picture["source_ms"] = source_at(clip, t_rel)
    return picture


def scene_at(project: dict, t_ms: float, end_ms: int | None = None) -> dict:
    """What the viewer sees at `t_ms`. The twin of `sceneAt` in scene.js.

    Returns resolved clips — every animatable property already interpolated.

    ⚠ `layers` IS THE DRAW ORDER AND THE ONLY THING A RENDERER MAY WALK. It used
    to be a sentence here instead — "picture (× 2 during a transition) → shapes →
    overlay pictures → text" — hard-coded three times over (here, in `sceneAt`, in
    `render_frame`), which is why a row could only be restacked among its own kind.
    Every visual row has a RANK now (`settings.lane_order`, read by `lane_rank`)
    and this list is the result of sorting by it, bottom of the stack first.
    ⚠ WITH NO SAVED ORDER IT IS THAT OLD SENTENCE, clip for clip.

    ⚠ `pictures` IS THE PICTURE, AND IT IS A STACK — one entry per picture track
    that has something on it at `t`, BOTTOM TRACK FIRST. Every renderer must walk
    it; nothing may read `frame` and think it has drawn the film. An EMPTY stack
    is legal and means the letterbox colour: clips are placed freely now, so a
    track can have a gap in it, and a gap on the bottom track with nothing above
    it is a moment where the picture IS the backdrop.

    ⚠ `frame` / `frame_b` / `mix` / `transition` / `transition_params` ARE THE
    TOPMOST ENTRY, DERIVED — never computed a second way. They answer "which clip
    is at the playhead", which is a different question from "what is on screen".
    On a project with one picture track — every animatic written before tracks —
    the stack has exactly one entry and these are it.

    ON A TRANSITION a track has two pictures: `frame` is the OUTGOING one for the
    WHOLE window — including the half past the cut, where `frame_spans` would
    say the incoming picture is up — `frame_b` is the picture arriving, and
    `mix` says how far through we are. See the note in transitions.js for why
    that way round: it is what makes `mix` run 0 → 1 without doubling back, and
    the only reading under which a wipe or a slide has a direction.
    """
    frames = project.get("frames") or []
    spans, total_ms = frame_spans(frames, end_ms)
    t = max(0.0, float(t_ms or 0))

    # ⚠ ONE PASS PER TRACK, bottom to top, and each track resolves its OWN
    # transition. A transition is track-local (`transition_window`), so asking the
    # project once and applying the answer to every track would put one track's
    # dissolve on another's picture.
    pictures: list[dict] = []
    for span in _stack_at(spans, t):
        frame = _picture_at(frames, spans, span["index"], t)
        # The transition overrides which picture is "the frame", because for the
        # half of the window past the cut the answer is the one on its way OUT.
        # Both pictures are resolved outside their own span here — keys hold at
        # the ends rather than extrapolating, so neither flies off screen.
        active = transition_at(project, t, spans, span["track"])
        frame_b = None
        if active is not None:
            frame = _picture_at(frames, spans, active["from_index"], t)
            frame_b = _picture_at(frames, spans, active["to_index"], t)
        pictures.append(
            {
                "track": span["track"],
                "frame": frame,
                "frame_b": frame_b,
                "mix": active["mix"] if active is not None else 0.0,
                "transition": active["kind"] if active is not None else None,
                # ⚠ ALWAYS A DICT, empty off a transition rather than absent — see
                # the note on the scene's own copy of this field below.
                "transition_params": active["params"] if active is not None else {},
            }
        )
    # The topmost track's, derived. See the docstring: this is "the clip at the
    # playhead", not "the picture", and it is never worked out a second way.
    top = pictures[-1] if pictures else None
    frame = top["frame"] if top is not None else None
    frame_b = top["frame_b"] if top is not None else None
    active = top if (top is not None and top["transition"]) else None

    # An empty caption is skipped HERE, so the preview and the exporter skip it
    # for the same reason in the same place.
    texts = [
        c
        for c in (
            _resolve(c, "text", t - (c.get("start_ms") or 0))
            for c in (project.get("texts") or [])
            if (c.get("text") or "").strip() and _alive(c, t)
        )
        if c["opacity"] > 0
    ]

    # Opacity is tested AFTER resolving, not before: a shape keyframed from 0 to
    # 1 is invisible at its first frame and visible later, and dropping it up
    # front (which is what the exporter used to do) would delete the whole fade.
    shapes = [
        s
        for s in (
            _resolve(s, "shape", t - (s.get("start_ms") or 0))
            for s in (project.get("shapes") or [])
            if _alive(s, t)
        )
        if s["opacity"] > 0
    ]

    overlays = [
        o
        for o in (
            _resolve(o, "overlay", t - (o.get("start_ms") or 0))
            for o in (project.get("overlays") or [])
            if _alive(o, t)
        )
        if o["opacity"] > 0
    ]

    return {
        "t_ms": t,
        "total_ms": total_ms,
        # ⚠ THE DRAW ORDER, BOTTOM FIRST — the one thing a renderer may walk. See
        # `ordered_layers`; the four lists below still hold the clips, and every
        # entry of this one points into one of them.
        "layers": ordered_layers(project, pictures, shapes, overlays, texts),
        # Empty unless this project has been restacked — see `stack_key`. It rides
        # on the scene because `scene_signature` is the render cache key and cannot
        # see the project.
        "stack_key": stack_key(((project.get("settings") or {}).get("lane_order"))),
        # ⚠ THE PICTURE, bottom track first. Renderers walk `layers`; `frame` below
        # is the topmost entry and answers a different question. See the docstring.
        "pictures": pictures,
        "frame": frame,
        # The transition, flattened onto the scene: the second picture, how far
        # through the blend we are, and which blend. None / 0.0 / None off a cut.
        "frame_b": frame_b,
        "mix": active["mix"] if active is not None else 0.0,
        "transition": active["transition"] if active is not None else None,
        # ⚠ ALWAYS A DICT, empty off a transition rather than absent. A missing
        # key here is `undefined` in JS, which JSON drops, and the resolved scene
        # would then be a different SHAPE on the two sides — which is exactly how
        # `tests/render_parity.py` ends up comparing two things it thinks are
        # equal for the wrong reason. Same rule `place` and `blend` follow.
        "transition_params": (
            active["transition_params"] if active is not None else {}
        ),
        "shapes": shapes,
        "overlays": overlays,
        "texts": texts,
    }


def is_animated(project: dict) -> bool:
    """Does anything in this project MOVE? Mirrors `isAnimated` in scene.js.

    This decides how the export is encoded. False → the timeline can be cut into
    stretches where nothing changes and rendered as one still per stretch, which
    is what has always happened and is very fast. True → the frame has to be
    drawn at every video frame.

    Getting this wrong in the "false" direction would silently drop every
    animation from the MP4, so it errs toward True: any track with more than one
    key counts, even one whose keys happen to share a value.
    """
    # A transition is continuous by definition — every video frame of the blend
    # is a different picture. One anywhere in the project forces the sampling
    # planner, and this is checked FIRST because it is the cheapest answer.
    if project.get("transitions"):
        return True
    # A VIDEO CLIP is continuous for exactly the same reason, and this is the
    # one that would be most expensive to get wrong: `plan_segments` renders one
    # still per stretch where the picture holds, so a video planned that way
    # would export as a single FROZEN frame held for the whole clip while the
    # preview played it. `speed` is checked too, per the phase's own rule, even
    # though it only means anything on a video.
    for clip in project.get("frames") or []:
        if clip_kind(clip) == "video":
            return True
        if abs(float(clip.get("speed") or DEFAULT_SPEED) - 1.0) > 1e-9:
            return True
    groups = (
        ("frame", project.get("frames")),
        ("text", project.get("texts")),
        ("shape", project.get("shapes")),
        ("overlay", project.get("overlays")),
    )
    for kind, clips in groups:
        for clip in clips or []:
            keyframes = clip.get("keyframes") or {}
            # ⚠ `look_props` is why this loop is not just `ANIMATABLE`. A grade
            # that RAMPS — a mask sweeping across the shot, a LUT dialling in —
            # is continuous in exactly the way a Ken Burns push is, so it has to
            # force the sampling planner too. Miss it and `plan_segments` would
            # render one still for the whole stretch and the MP4 would show the
            # grade frozen at its first value while the monitor animated it.
            props = ANIMATABLE.get(kind, ())
            if kind in LOOK_KINDS:
                props = (*props, *look_props(clip))
            for prop in props:
                track = keyframes.get(prop)
                if isinstance(track, list) and len(track) > 1:
                    return True
    return False


def scene_signature(scene: dict) -> str:
    """A stable identity for a rendered frame — the exporter's cache key.

    Two moments with the same signature are the same picture, so the still is
    rendered once and reused. With no keyframes anywhere this collapses to the
    ids of what is visible, which is exactly the key `build_animatic` already
    used, so a project that doesn't move costs no more to export than before.
    """
    # ⚠ Numbers are formatted EXPLICITLY, never interpolated raw. Python prints
    # 1.0 where JS prints 1, so signatures that were meant to be identical
    # weren't — the parity test caught it on its first run. A fixed 6-place
    # format is the same string in both languages.
    def n(v) -> str:
        return f"{float(v or 0):.{PRECISION}f}"

    # ⚠ A VIDEO CLIP'S `source_ms` MUST BE IN THE KEY, for precisely the reason
    # `mix` must be: without it every sampled moment of a clip resolves to one
    # signature, the exporter renders a single still and reuses it for the whole
    # clip, and the video plays as a FREEZE FRAME. Appended only when the clip
    # isn't a plain image, so a project made of stills signs byte-for-byte what
    # it signed before clips existed.
    def clip_extra(picture: dict) -> str:
        kind = picture.get("kind") or "image"
        if kind == "image":
            return ""
        return f":k{kind}:{n(picture.get('source_ms'))}:{picture.get('color') or ''}"

    # ⚠ A LOOK THAT MOVES MUST BE IN THE KEY, for the third time and the same
    # reason as `mix` and `source_ms`: two samples of a mask sweeping across a
    # held picture resolve to the same clip at the same transform and differ
    # ONLY here, so leaving it out would render one still, reuse it for the
    # whole sweep, and freeze the grade while the monitor animated it.
    #
    # Appended only when there IS something to say — an empty chain, an unset
    # mask and "normal" contribute nothing — so every project that predates
    # effects signs byte-for-byte what it signed before.
    def look_extra(picture: dict) -> str:
        bits: list[str] = []
        for effect in picture.get("effects") or []:
            params = effect.get("params") or {}
            values = ",".join(
                f"{key}={value if isinstance(value, str) else n(value)}"
                for key, value in sorted(params.items())
            )
            bits.append(f"{effect.get('kind')}[{values}]")
        mask = picture.get("mask") or {}
        if (mask.get("kind") or "none") != "none":
            bits.append(
                f"m{mask['kind']}:{n(mask['x'])}:{n(mask['y'])}:{n(mask['w'])}"
                f":{n(mask['h'])}:{n(mask['feather'])}:{1 if mask.get('invert') else 0}"
            )
        blend = picture.get("blend") or DEFAULT_BLEND
        if blend != DEFAULT_BLEND:
            bits.append(f"b{blend}")
        return (":L" + "+".join(bits)) if bits else ""

    parts: list[str] = []
    # ⚠ `mix` MUST be in the key. Without it every video frame of a dissolve
    # resolves to one signature, the exporter renders a single still and reuses
    # it for the whole blend, and the transition SNAPS instead of blending —
    # exactly the reuse bug this key already guards against for keyframes.
    # Added only when there IS a second picture, so a project with no
    # transitions produces the signature it produced before they existed.
    #
    # ⚠ AND SO MUST ITS PARAMETERS, for the same reason once more: a wipe
    # travelling left and one travelling right resolve to the same two pictures
    # at the same `mix` and differ ONLY here. Leaving them out would render one
    # still per `mix` value and reuse it across directions — and worse, a project
    # whose only edit was the direction would hit the cache from the previous
    # export and come back unchanged.
    #
    # Only NON-DEFAULT parameters are written, so a plain wipe or dissolve signs
    # byte-for-byte what it signed before parameters existed.
    def param_extra(kind: str, params: dict) -> str:
        defaults = TRANSITION_PARAMS.get(kind or "", {})
        bits = [
            f"{name}={value if isinstance(value, str) else n(value)}"
            for name in sorted(defaults)
            for value in (params.get(name, defaults[name]),)
            if value != defaults[name]
        ]
        return (":p" + ",".join(bits)) if bits else ""

    # ⚠ EVERY TRACK, bottom first — not just the topmost. Two moments that differ
    # only in what an upper track is showing are two different frames, and signing
    # one of them would make the exporter reuse the other's still.
    #
    # With ONE track this writes byte-for-byte the string it always wrote (an `f…`
    # part, then an `x…` part on a transition), so a project that predates tracks
    # hits the render cache from its previous export exactly as before.
    if not (scene.get("pictures") or []):
        parts.append("f-")
    for picture in scene.get("pictures") or []:
        f = picture.get("frame")
        if not f:
            continue
        parts.append(
            f"f{f['index']}:{n(f['scale'])}:{n(f['x'])}:{n(f['y'])}:{n(f['opacity'])}"
            + clip_extra(f)
            + look_extra(f)
        )
        b = picture.get("frame_b")
        if not b:
            continue
        parts.append(
            f"x{picture.get('transition')}:{n(picture.get('mix'))}"
            + param_extra(
                picture.get("transition"), picture.get("transition_params") or {}
            )
            + f":b{b['index']}:{n(b['scale'])}:{n(b['x'])}:{n(b['y'])}:{n(b['opacity'])}"
            + clip_extra(b)
            + look_extra(b)
        )
    for s in scene["shapes"]:
        parts.append(
            f"s{s.get('id')}:{n(s['x'])}:{n(s['y'])}:{n(s['w'])}:{n(s['h'])}"
            f":{n(s['opacity'])}:{n(s['rotation'])}"
        )
    for o in scene["overlays"]:
        parts.append(
            f"o{o.get('id')}:{n(o['x'])}:{n(o['y'])}:{n(o['w'])}:{n(o['h'])}"
            f":{n(o['opacity'])}:{n(o['rotation'])}"
            + look_extra(o)
        )
    # ⚠ A CAPTION THAT MOVES MUST BE IN THE KEY, for the fourth time and the
    # same reason as `mix`, `source_ms` and the look: a title sliding up the
    # frame resolves to the same clip at the same opacity and differs ONLY in
    # `y`, so leaving it out would render one still, reuse it for the whole
    # slide, and the caption would sit dead still in the MP4 while the monitor
    # moved it.
    #
    # Appended only in FREE placement, where x/y are the values actually drawn.
    # In flow placement they are resolved but unused, so a project of ordinary
    # stacked subtitles signs byte-for-byte what it signed before this existed.
    #
    # ⚠ AND SO MUST A CAPTION THAT ZOOMS OR TURNS — WHICH IT DID NOT, AND THAT
    # WAS A BUG WITH NOTHING ON SCREEN TO SHOW FOR IT. `scale` became animatable
    # on a caption in Phase 5 and was never added here, so a title that only
    # pushes in (which is exactly what `captionPush` in `agent/actions.js` writes
    # onto every caption the AI editor lays down) resolved to the same clip at
    # the same opacity at every moment of its life, signed one key, and
    # `build_animatic` rendered ONE still and reused it for the whole clip. The
    # monitor pushed in; the MP4 sat dead still. `rotation` would have arrived
    # with the same hole, and every zoom, pop, bounce, tilt and swing preset in
    # `text_presets.js` would have shipped broken in the export on day one.
    #
    # ⚠ APPENDED ONLY WHEN THEY ARE OFF THEIR RESTING VALUE, the same trick x/y
    # use above and for the same reason: a caption that has never been zoomed or
    # turned — which is every caption in every animatic saved before this — signs
    # byte-for-byte what it signed before, so no existing project re-renders.
    for c in scene["texts"]:
        extra = f":{n(c['x'])}:{n(c['y'])}" if c.get("place") == "free" else ""
        if abs(float(c.get("scale", 1.0)) - 1.0) > 1e-9:
            extra += f":s{n(c['scale'])}"
        if abs(float(c.get("rotation", 0.0))) > 1e-9:
            extra += f":r{n(c['rotation'])}"
        parts.append(f"t{c.get('id')}:{n(c['opacity'])}{extra}")
    # ⚠ WHICH ORDER THE ROWS ARE STACKED IN MUST BE IN THE KEY, for the fifth time
    # and the same reason as `mix`, `source_ms`, the look and a moving caption —
    # with one difference that makes it worse. This key is compared ACROSS exports,
    # so a restack that did not change it would come back as the PREVIOUS export's
    # stills in the PREVIOUS order: the one edit you made would be the one thing
    # missing from the file, with nothing on screen to suggest why.
    #
    # Empty for a project with no saved order — every animatic that predates the
    # gesture — so those sign byte-for-byte what they always signed.
    if scene.get("stack_key"):
        parts.append(f"z{scene['stack_key']}")
    return "|".join(parts)


# ---------------------------------------------------------------------------
# The one piece of drawing that belongs here
# ---------------------------------------------------------------------------
def place_picture(
    im: Image.Image,
    size: tuple[int, int],
    fit: str = "contain",
    scale: float = 1.0,
    x: float = 0.5,
    y: float = 0.5,
) -> tuple[Image.Image, int, int]:
    """Fit one source picture onto the video frame, with its own pan and zoom.

    `fit` is the existing behaviour and is unchanged when scale/x/y are at their
    defaults: "contain" letterboxes (nothing is lost — a storyboard frame you
    cropped is a frame you can't read), "cover" scales up and centre-crops.

    `scale` then multiplies that, and `x`/`y` say where the picture's CENTRE
    sits as a fraction of the canvas — 0.5, 0.5 being centred. Together they are
    a pan/zoom over a still, which is the move that makes a held storyboard
    panel look like a shot rather than a slide.

    Returns (resized image, left, top) rather than pasting, so the caller keeps
    control of the canvas and the compositing order.
    """
    target_w, target_h = size
    sw, sh = im.size
    base = (
        max(target_w / sw, target_h / sh)
        if fit == "cover"
        else min(target_w / sw, target_h / sh)
    )
    factor = base * max(0.01, float(scale or 1.0))
    new = im.resize(
        (max(1, int(round(sw * factor))), max(1, int(round(sh * factor)))),
        Image.LANCZOS,
    )
    # x/y are the CENTRE of the picture, so the offset is measured from there.
    left = int(round(target_w * float(x) - new.width / 2))
    top = int(round(target_h * float(y) - new.height / 2))
    return new, left, top
