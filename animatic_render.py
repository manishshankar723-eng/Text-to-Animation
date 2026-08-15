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
    "shape": ("x", "y", "w", "h", "opacity", "rotation"),
    "overlay": ("x", "y", "w", "h", "opacity", "rotation"),
    "text": ("opacity",),
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

# --- Transitions ------------------------------------------------------------
# ⚠ The twin of `client/src/animatic/transitions.js`. Read that file's header
# first: it explains the one decision everything else follows from — a
# transition is BOUNDARY-LOCAL. It blends over the tail of the outgoing picture
# and the head of the incoming one, d/2 either side of the cut, and takes
# nothing away from either. The timeline is exactly as long with transitions as
# without, so `frame_spans`, every cut position, ripple and rolling trims and
# any caption timed against a cut all keep working untouched.
TRANSITION_KINDS = ("dissolve", "dip", "wipe", "slide")
DEFAULT_TRANSITION_MS = 600
MIN_TRANSITION_MS = 100
MAX_TRANSITION_MS = 10_000


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


def _resolve(clip: dict, kind: str, t_rel: float) -> dict:
    out = dict(clip)
    defaults = FRAME_DEFAULTS if kind == "frame" else DEFAULTS
    for prop in ANIMATABLE.get(kind, ()):
        out[prop] = value_at(clip, prop, t_rel, defaults[prop])
    return out


def frame_spans(frames: list[dict], end_ms: int | None = None) -> tuple[list[dict], int]:
    """Where each frame sits, in play order. Mirrors `frameSpans` in scene.js.

    `end_ms` extends the LAST picture, which is what makes an export cover a
    music bed that outlasts the pictures instead of stopping dead on the final
    image. `plan_segments` has always done this; the rule lives here now so the
    preview holds the last frame too.
    """
    spans: list[dict] = []
    clock = 0
    for i, frame in enumerate(frames):
        length = max(100, int(frame.get("duration_ms") or 2000))
        spans.append({"start": clock, "end": clock + length, "index": i})
        clock += length
    if spans and end_ms and end_ms > clock:
        spans[-1]["end"] = end_ms
        clock = end_ms
    return spans, clock


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
    if from_index is None or from_index + 1 >= len(spans):
        return None

    a, b = spans[from_index], spans[from_index + 1]
    shorter = min(a["end"] - a["start"], b["end"] - b["start"])
    duration_ms = max(
        MIN_TRANSITION_MS,
        min(
            int(round(float(transition.get("duration_ms") or DEFAULT_TRANSITION_MS))),
            MAX_TRANSITION_MS,
            shorter,
        ),
    )
    kind = transition.get("kind")
    cut = a["end"]
    return {
        "id": transition.get("id"),
        # An unknown kind falls back HERE rather than in each renderer, so the
        # preview and the export can't fall back differently. Same rule as ease.
        "kind": kind if kind in TRANSITION_KINDS else "dissolve",
        "from_index": from_index,
        "to_index": from_index + 1,
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


def transition_at(project: dict, t_ms: float, spans: list[dict]) -> dict | None:
    """The transition covering `t_ms`, and how far through it we are.

    `mix` runs 0 → 1 across the whole window: 0 is "all outgoing picture", 1 is
    "all incoming". Half-open at both ends like every other visibility test
    here. Two transitions written onto the same cut is a project that shouldn't
    exist (the editor replaces rather than appends); the first one wins.
    """
    frames = project.get("frames") or []
    t = float(t_ms or 0)
    for transition in project.get("transitions") or []:
        window = transition_window(frames, spans, transition)
        if window is None:
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
    """One frame, resolved at `t` and stamped with where it sits in the sequence."""
    span = spans[index]
    picture = _resolve(frames[index], "frame", t - span["start"])
    picture["index"] = index
    picture["start_ms"] = span["start"]
    picture["end_ms"] = span["end"]
    return picture


def scene_at(project: dict, t_ms: float, end_ms: int | None = None) -> dict:
    """What the viewer sees at `t_ms`. The twin of `sceneAt` in scene.js.

    Returns resolved clips — every animatable property already interpolated —
    in the order they are composited, bottom to top:

        picture (× 2 during a transition) → shapes → overlay pictures → text

    ON A TRANSITION there are two pictures: `frame` is the OUTGOING one for the
    WHOLE window — including the half past the cut, where `frame_spans` would
    say the incoming picture is up — `frame_b` is the picture arriving, and
    `mix` says how far through we are. See the note in transitions.js for why
    that way round: it is what makes `mix` run 0 → 1 without doubling back, and
    the only reading under which a wipe or a slide has a direction.

    Off a transition `frame_b` is None, `mix` is 0.0 and `transition` is None —
    which is every animatic that existed before this, resolving exactly as it
    always did.
    """
    frames = project.get("frames") or []
    spans, total_ms = frame_spans(frames, end_ms)
    t = max(0.0, float(t_ms or 0))

    span = next((s for s in spans if s["start"] <= t < s["end"]), None)
    frame = None
    if span is not None:
        frame = _picture_at(frames, spans, span["index"], t)

    # A transition overrides which picture is "the frame", because for the half
    # of the window past the cut the answer is the one on its way OUT. Both
    # pictures are resolved outside their own span here — keys hold at the ends
    # rather than extrapolating, so neither flies off screen.
    active = transition_at(project, t, spans) if frame is not None else None
    frame_b = None
    if active is not None:
        frame = _picture_at(frames, spans, active["from_index"], t)
        frame_b = _picture_at(frames, spans, active["to_index"], t)

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
        "frame": frame,
        # The transition, flattened onto the scene: the second picture, how far
        # through the blend we are, and which blend. None / 0.0 / None off a cut.
        "frame_b": frame_b,
        "mix": active["mix"] if active is not None else 0.0,
        "transition": active["kind"] if active is not None else None,
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
    groups = (
        ("frame", project.get("frames")),
        ("text", project.get("texts")),
        ("shape", project.get("shapes")),
        ("overlay", project.get("overlays")),
    )
    for kind, clips in groups:
        for clip in clips or []:
            keyframes = clip.get("keyframes") or {}
            for prop in ANIMATABLE.get(kind, ()):
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

    parts: list[str] = []
    f = scene.get("frame")
    if f:
        parts.append(f"f{f['index']}:{n(f['scale'])}:{n(f['x'])}:{n(f['y'])}:{n(f['opacity'])}")
    else:
        parts.append("f-")
    # ⚠ `mix` MUST be in the key. Without it every video frame of a dissolve
    # resolves to one signature, the exporter renders a single still and reuses
    # it for the whole blend, and the transition SNAPS instead of blending —
    # exactly the reuse bug this key already guards against for keyframes.
    # Added only when there IS a second picture, so a project with no
    # transitions produces the signature it produced before they existed.
    b = scene.get("frame_b")
    if b:
        parts.append(
            f"x{scene.get('transition')}:{n(scene.get('mix'))}"
            f":b{b['index']}:{n(b['scale'])}:{n(b['x'])}:{n(b['y'])}:{n(b['opacity'])}"
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
        )
    for c in scene["texts"]:
        parts.append(f"t{c.get('id')}:{n(c['opacity'])}")
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
