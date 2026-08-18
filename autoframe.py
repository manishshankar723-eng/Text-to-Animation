"""autoframe.py — where the subject is, and therefore where to put the camera.

Reframing a 16:9 board to 9:16 by hand is forty shots of dragging a picture
about until the person is in the middle of it. This does that pass: one vision
call per shot says WHERE THE SUBJECT IS, and pure arithmetic turns that into the
`scale` / `x` / `y` the editor already has.

⚠ THE MODEL IS ASKED FOR THE SUBJECT, NEVER FOR THE CROP. That split is the
whole design, and it is not a stylistic preference:

  * A model asked for "a 9:16 crop box" returns a box whose aspect is roughly
    9:16 — roughly is the operative word, and a crop that is 0.58 instead of
    0.5625 is a reframe that is subtly wrong on every shot of the board.
  * Aspect arithmetic, clamping to the edges of the picture, and "as tight as
    possible while still containing the subject" are things code is exact at and
    a language model is approximate at.
  * A subject box is checkable. `crop_box` PROVABLY contains the box it was
    given (see its docstring), so "did the person survive the reframe" is a
    property of this file rather than a property of that afternoon's weights.

⚠ AND IT WRITES ORDINARY KEYFRAMABLE PROPERTIES, NOT A CROP. `frame_transform`
returns exactly `{scale, x, y}` — the three values `AnimaticFrame` has carried
since the scene model landed, which the monitor and `animatic_render.place_picture`
already agree about. So an auto-reframed shot is a shot somebody could have
panned by hand: it keyframes, it undoes, it exports through the path that was
already there. There is no "crop" concept anywhere in this codebase and this
file is not the place to introduce one — a second way of saying where a picture
sits is a second thing for the preview and the export to disagree about.

FOUR PARTS, AND ONLY ONE OF THEM COSTS ANYTHING:

    detect_subject()  — the model call. One image in, one box out. The only
                        thing here that spends quota.
    crop_box()        — pure. The tightest box of the TARGET aspect that
                        contains the subject and lies inside the picture.
    frame_transform() — pure. That box as `scale`/`x`/`y`. The twin of the
                        arithmetic in `animatic_render.place_picture`; if you
                        change one, change both.
    estimate()        — free. What a run would cost, for the confirm dialog.

`tests/autoframe_check.py` drives the pure three against a stub detector, so the
geometry is proven without a key and without spending anything.
"""

from __future__ import annotations

import json
import logging
import os

from google.genai import types

logger = logging.getLogger(__name__)


class AutoframeError(Exception):
    """Anything that stops a reframe pass, phrased for the person who asked."""


# --- Spend guards -----------------------------------------------------------
# One run's ceiling, in frames. A vision call per shot is cheap; forty of them
# by accident on a board somebody meant to look at is not. This is a SPEND
# guard, not a technical one.
MAX_FRAMES = int(os.environ.get("API_MAX_REFRAME_FRAMES", "60"))

# Advisory rate, in US dollars per image, for the estimate. A storyboard panel
# at the sizes this app produces is a few hundred input tokens of image plus a
# two-line JSON answer. Like every other price in this codebase it is a LIST
# price we quote, not a bill we issue — only Google bills.
USD_PER_FRAME = float(os.environ.get("API_REFRAME_USD_PER_FRAME", "0.0004"))

# --- Framing rules ----------------------------------------------------------
# Breathing room left around the subject, as a fraction of the subject's own
# size, added to each side. A box drawn tight to a head and then filled to the
# frame is a portrait cropped at the hairline; this is what stops the reframe
# looking like a mugshot. Dropped rather than honoured when the shot is too
# tight to afford it — see `crop_box`.
SUBJECT_PAD = 0.14

# The most this pass will ever punch in, expressed as the least of the source
# HEIGHT the crop must keep. A model that reports a distant figure as a small
# box would otherwise produce a 6× blow-up of a storyboard panel, which is a
# picture made of pixels rather than a shot. Clamped BELOW the available height
# when the target is wider than the source, where there is no choice.
MIN_CROP_HEIGHT = 0.45

# What `AnimaticFrame` will accept. Mirrored here rather than imported so this
# module stays free of the server package — the parity test imports it alone.
SCALE_RANGE = (0.1, 10.0)
POSITION_RANGE = (-2.0, 3.0)


# ---------------------------------------------------------------------------
# Aspect ratios
# ---------------------------------------------------------------------------
def aspect_value(aspect) -> float:
    """"9:16" (or 0.5625, or (9, 16)) as width ÷ height.

    Accepts the string the project stores, the number the arithmetic wants, and
    the pair a caller with pixel sizes already has, because all three turn up.
    """
    if isinstance(aspect, (tuple, list)) and len(aspect) == 2:
        w, h = float(aspect[0]), float(aspect[1])
        if w <= 0 or h <= 0:
            raise AutoframeError(f"'{aspect}' is not a usable aspect ratio.")
        return w / h
    if isinstance(aspect, (int, float)):
        if float(aspect) <= 0:
            raise AutoframeError(f"'{aspect}' is not a usable aspect ratio.")
        return float(aspect)
    text = str(aspect or "").strip().replace("x", ":").replace("/", ":")
    try:
        if ":" in text:
            w, h = text.split(":", 1)
            return aspect_value((float(w), float(h)))
        return aspect_value(float(text))
    except (TypeError, ValueError):
        raise AutoframeError(f"'{aspect}' is not a usable aspect ratio.") from None


# ---------------------------------------------------------------------------
# The one part that spends: where is the subject?
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You are a cinematographer re-framing a storyboard shot for a different "
    "screen shape. You are shown ONE drawing. You return the ONE rectangle that "
    "must stay in frame: the subject of the shot and nothing else.\n"
    "Rules:\n"
    "- The subject is what the shot is ABOUT. Usually a person; their face and "
    "  upper body if they are close, their whole figure if they are far away.\n"
    "- If two characters are talking, the rectangle covers BOTH of them — a "
    "  reframe that cuts one out of a two-shot has changed what the shot is.\n"
    "- If nobody is in the drawing, the rectangle covers the thing the eye goes "
    "  to: the object being looked at, the sign being read, the landmark.\n"
    "- Include what the subject needs to READ — a raised hand, the object being "
    "  held — and nothing more. Do not return the whole picture out of caution: "
    "  a rectangle covering everything says the shot cannot be reframed at all.\n"
    "- x and y are the rectangle's TOP-LEFT corner as a fraction of the picture "
    "  (0,0 is the top-left of the drawing); w and h are its size as a fraction. "
    "  All four are between 0 and 1, and x+w and y+h must not exceed 1.\n"
    "- `subject` is two or three words naming what you boxed, for the person "
    "  checking your work: 'Kabir, seated', 'the temple doorway'."
)


def _subject_schema() -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        required=["x", "y", "w", "h", "subject"],
        properties={
            "x": types.Schema(type=types.Type.NUMBER),
            "y": types.Schema(type=types.Type.NUMBER),
            "w": types.Schema(type=types.Type.NUMBER),
            "h": types.Schema(type=types.Type.NUMBER),
            "subject": types.Schema(type=types.Type.STRING),
        },
    )


def coerce_subject(raw) -> dict:
    """One model answer into a usable box, clamped inside the picture.

    Forgiving, then strict: a box that runs off the edge is pulled back rather
    than refused (the model is describing a real thing it can see, and being 3%
    over the edge is a rounding error, not a hallucination), but a box with no
    area is an answer this pass cannot use and says so.
    """
    if not isinstance(raw, dict):
        raise AutoframeError("The model returned something that isn't a subject box.")
    try:
        x = float(raw.get("x") or 0.0)
        y = float(raw.get("y") or 0.0)
        w = float(raw.get("w") or 0.0)
        h = float(raw.get("h") or 0.0)
    except (TypeError, ValueError):
        raise AutoframeError("The model's subject box wasn't made of numbers.") from None

    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.0), 1.0 - x)
    h = min(max(h, 0.0), 1.0 - y)
    if w <= 0.0 or h <= 0.0:
        raise AutoframeError("The model returned an empty subject box for that shot.")
    return {
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "subject": str(raw.get("subject") or "").strip(),
    }


def detect_subject(path: str, *, provider: str | None = None, hint: str = "") -> dict:
    """SPENDS QUOTA. Look at one picture and return the box that must stay in frame.

    Returns `{x, y, w, h, subject}` in fractions of the picture, top-left origin.
    `hint` is the shot's own description when we have it — the model is looking
    at a rough grey sketch, and being told it is a wide of a sleeping man is the
    difference between boxing the man and boxing the bed.

    Separate from every pure function below so the geometry can be tested
    without a key, and so a re-crop after an edit never re-bills.
    """
    import script_breakdown

    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise AutoframeError("That shot's picture has gone missing.") from exc
    if not data:
        raise AutoframeError("That shot's picture is empty.")

    client = script_breakdown.get_client(provider)
    model_id = script_breakdown.text_model_id(provider)
    prompt = "Box the subject of this shot."
    if str(hint or "").strip():
        prompt += f"\nThe shot is described as: {str(hint).strip()}"

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=[types.Part.from_bytes(data=data, mime_type="image/png"), prompt],
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                response_mime_type="application/json",
                response_schema=_subject_schema(),
                temperature=0.0,
            ),
        )
    except Exception as exc:  # the SDK raises a wide family of transport errors
        raise AutoframeError(f"The reframe call failed: {exc}") from exc

    payload = getattr(response, "text", None)
    if not payload:
        raise AutoframeError(
            "The model returned nothing for that shot — it may have been blocked."
        )
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AutoframeError(f"The model returned invalid JSON ({exc}).") from exc

    box = coerce_subject(raw)
    logger.info(
        "[autoframe] %s → subject %r at (%.2f, %.2f) %.2f×%.2f",
        os.path.basename(path), box["subject"], box["x"], box["y"], box["w"], box["h"],
    )
    return box


# ---------------------------------------------------------------------------
# Pure: the subject box → the crop that frames it
# ---------------------------------------------------------------------------
def crop_box(
    subject: dict,
    source_aspect,
    target_aspect,
    *,
    pad: float = SUBJECT_PAD,
    min_height: float = MIN_CROP_HEIGHT,
) -> dict:
    """The tightest box of the TARGET aspect that frames this subject.

    Returns `{x, y, w, h, fits, subject}` in fractions of the SOURCE picture,
    top-left origin, the same coordinates `subject` came in as.

    Three things are true of the answer, in this order of priority:

    1. **It is exactly the target aspect.** Not approximately — this is the
       reason the model is asked for a subject rather than a crop.
    2. **It lies inside the source picture.** A crop running off the edge is
       transparent pixels in the export.
    3. **It contains the subject.** Provable rather than hoped for: the box is
       made no smaller than the subject in either dimension, and then its centre
       is CLAMPED into the picture rather than moved freely — and a clamp can
       only ever move the crop's centre TOWARDS the subject's own side of the
       picture, never past it. `fits` is False in the one case where (3) has to
       give way to (1) and (2): a subject so large that no box of the target
       shape can hold it, which is a shot that genuinely cannot be reframed.

    The padding is the first thing dropped when the shot is too tight to afford
    it — breathing room is a nicety and keeping the subject is not.

    ⚠ The arithmetic runs in SOURCE-HEIGHT UNITS (the picture is `source_aspect`
    wide and 1.0 tall) rather than in fractions of each axis, because an aspect
    ratio compares the two axes and fractions of different axes are not
    comparable. Converting back on the way out is the last two lines.
    """
    a_s = aspect_value(source_aspect)
    a_t = aspect_value(target_aspect)

    sx = float(subject["x"]) * a_s
    sw = float(subject["w"]) * a_s
    sy = float(subject["y"])
    sh = float(subject["h"])

    # The biggest box of the target shape that fits in the picture at all. Every
    # ceiling below is this one; for a target WIDER than the source it is a band
    # across the middle and there is no room to pad or to punch in.
    h_max = min(1.0, a_s / a_t)

    def _needed(box_w: float, box_h: float) -> float:
        """The crop HEIGHT that holds a box this size at the target aspect."""
        return max(box_h, box_w / a_t)

    pad = max(0.0, float(pad))
    h_c = _needed(sw * (1.0 + 2.0 * pad), sh * (1.0 + 2.0 * pad))
    fits = True
    if h_c > h_max:
        # Too tight to pad. Try the subject exactly as it was given.
        h_c = _needed(sw, sh)
        if h_c > h_max:
            # No box of this shape holds this subject. Take the whole of what is
            # available and say so — a caller may reasonably decide not to write
            # a reframe it was told is impossible.
            h_c = h_max
            fits = False

    # The punch-in ceiling, applied only where there is room for it: on a target
    # wider than the source, `h_max` is already the whole answer.
    h_c = min(max(h_c, min(min_height, h_max)), h_max)
    w_c = h_c * a_t

    # Centre on the subject, then push the box back inside the picture. Clamping
    # is what keeps rule 2, and it cannot break rule 3 — see the docstring.
    cx = min(max(sx + sw / 2.0, w_c / 2.0), a_s - w_c / 2.0)
    cy = min(max(sy + sh / 2.0, h_c / 2.0), 1.0 - h_c / 2.0)

    return {
        "x": (cx - w_c / 2.0) / a_s,
        "y": cy - h_c / 2.0,
        "w": w_c / a_s,
        "h": h_c,
        "fits": fits,
        "subject": str(subject.get("subject") or ""),
    }


# ---------------------------------------------------------------------------
# Pure: the crop → the three properties a frame already has
# ---------------------------------------------------------------------------
def frame_transform(
    crop: dict,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    *,
    fit: str = "contain",
) -> dict:
    """That crop as `{scale, x, y}` — ordinary `AnimaticFrame` properties.

    ⚠ TWIN OF `animatic_render.place_picture` (and of `placePicture` in
    `client/src/animatic/gl/compositor.js`, which is the same arithmetic again).
    It reads:

        base   = min(tw/sw, th/sh)          # "contain"; max() for "cover"
        factor = base × scale               # the source is drawn at sw×factor
        left   = tw × x − drawn_width  / 2  # x/y are the picture's CENTRE
        top    = th × y − drawn_height / 2

    This inverts it. Making the crop fill the canvas needs
    `factor = tw / (crop_w × sw)`, so `scale = factor / base`; and putting the
    crop's centre on the canvas centre gives the x/y below. Change `place_picture`
    and this is one of the three places that has to move with it.

    The returned values are CLAMPED to what `AnimaticFrame` accepts, so the
    caller can write them straight onto a clip — as plain values or as keyframes,
    which is the same thing to everything downstream.
    """
    sw, sh = (max(1, int(source_size[0])), max(1, int(source_size[1])))
    tw, th = (max(1, int(target_size[0])), max(1, int(target_size[1])))

    base = max(tw / sw, th / sh) if fit == "cover" else min(tw / sw, th / sh)

    crop_w = max(1e-6, float(crop["w"]))
    crop_h = max(1e-6, float(crop["h"]))
    # Both terms are equal when the crop is exactly the target aspect, which
    # `crop_box` guarantees; `max` is what keeps a hand-written crop covering.
    factor = max(tw / (crop_w * sw), th / (crop_h * sh))

    ccx = float(crop["x"]) + crop_w / 2.0
    ccy = float(crop["y"]) + crop_h / 2.0

    scale = factor / base
    x = 0.5 + (sw * factor / tw) * (0.5 - ccx)
    y = 0.5 + (sh * factor / th) * (0.5 - ccy)

    return {
        "scale": round(min(max(scale, SCALE_RANGE[0]), SCALE_RANGE[1]), 4),
        "x": round(min(max(x, POSITION_RANGE[0]), POSITION_RANGE[1]), 4),
        "y": round(min(max(y, POSITION_RANGE[0]), POSITION_RANGE[1]), 4),
    }


def reframe_values(
    subject: dict,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    *,
    fit: str = "contain",
    pad: float = SUBJECT_PAD,
) -> dict:
    """The whole pure pass: a subject box in, `{scale, x, y, crop, fits}` out.

    The source ASPECT is taken from `source_size`, so a caller that has the
    pixels never has to state the ratio twice and get it wrong once.
    """
    sw, sh = (max(1, int(source_size[0])), max(1, int(source_size[1])))
    tw, th = (max(1, int(target_size[0])), max(1, int(target_size[1])))
    crop = crop_box(subject, sw / sh, tw / th, pad=pad)
    out = frame_transform(crop, (sw, sh), (tw, th), fit=fit)
    out["crop"] = {k: round(crop[k], 4) for k in ("x", "y", "w", "h")}
    out["fits"] = crop["fits"]
    out["subject"] = crop["subject"]
    return out


# ---------------------------------------------------------------------------
# Pure: writing those three values onto a clip that may already be moving
# ---------------------------------------------------------------------------
def apply_to_frame(frame: dict, values: dict) -> dict:
    """The patch that re-frames one clip — including one that is ANIMATED.

    Returns `{scale, x, y}` and, when the clip has keys on any of them, a
    rewritten `keyframes` map. Everything in it is an ordinary `AnimaticFrame`
    property that both renderers already resolve; there is no new field and no
    new code path in the exporter.

    ⚠ A KEYFRAMED SHOT IS THE CASE THAT MATTERS, not the exception. A Ken Burns
    push is the single most common thing anyone does to a held storyboard panel,
    and it lives entirely in keys on `scale`/`x`/`y`. Writing a static value
    under those keys would be a reframe that does nothing at all — the keys win
    at every instant — so the keys are MOVED with it:

        scale_k → scale_k × r          where r = scale_new ÷ scale_old
        x_k     → x_new + (x_k − x_old) × r

    The ratio on the pan is not decoration. `x` is a fraction of the CANVAS, so
    the same gesture across the same part of the picture is a smaller number
    once the picture is drawn bigger; multiplying the offset by `r` is what
    keeps a push that crossed the subject's face still crossing their face
    after the reframe, instead of flying off the edge of a picture that is now
    three times the size.

    The clip's own value at the moment of the reframe is the ANCHOR (`x_old`),
    which is why this is a delta rather than a replacement: the shot keeps its
    move and changes where the move happens.
    """
    keys = frame.get("keyframes") or {}
    old = {
        "scale": float(frame.get("scale") if frame.get("scale") is not None else 1.0),
        "x": float(frame.get("x") if frame.get("x") is not None else 0.5),
        "y": float(frame.get("y") if frame.get("y") is not None else 0.5),
    }
    patch: dict = {
        "scale": values["scale"],
        "x": values["x"],
        "y": values["y"],
    }

    moving = {p for p in ("scale", "x", "y") if keys.get(p)}
    if not moving:
        return patch

    ratio = values["scale"] / max(1e-6, old["scale"])
    limits = {"scale": SCALE_RANGE, "x": POSITION_RANGE, "y": POSITION_RANGE}
    # ⚠ `t` / `v`, not `t_ms` / `value` — `AnimaticKeyframe`'s field names, which
    # are what `animatic_render.value_at` and `scene.js` both read. Writing the
    # long names produces a keyframe list that validates as neither and silently
    # resolves to the clip's base value.
    rewritten = {p: [dict(k) for k in (keys.get(p) or [])] for p in keys}
    for prop in moving:
        lo, hi = limits[prop]
        for key in rewritten[prop]:
            try:
                was = float(key.get("v"))
            except (TypeError, ValueError):
                continue
            now = was * ratio if prop == "scale" else values[prop] + (was - old[prop]) * ratio
            key["v"] = round(min(max(now, lo), hi), 4)
    patch["keyframes"] = rewritten
    return patch


# ---------------------------------------------------------------------------
# Free: what a run would cost
# ---------------------------------------------------------------------------
def estimate(frame_count: int) -> dict:
    """FREE. What reframing this many shots would cost, for the confirm dialog.

    Computed from the same number the run is capped by, so the quote can never
    describe a different job from the one the button does.
    """
    import script_breakdown

    n = max(0, int(frame_count))
    return {
        "frames": n,
        "usd": round(n * USD_PER_FRAME, 6),
        "model": script_breakdown.text_model_id(),
        "over_limit": n > MAX_FRAMES,
        "limit_frames": MAX_FRAMES,
    }
