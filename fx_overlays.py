"""fx_overlays.py — the light leaks, grain and glitches, GENERATED rather than bought.

⚠ THE WHOLE POINT OF THIS FILE IS WHAT IT IS *NOT*: it is not a renderer, not a
shader, and not a new kind of clip. Every overlay it makes is an ordinary **MP4
stored as an ordinary video upload**, dropped on an ordinary picture row with an
ordinary `blend` mode. The Program monitor already plays video clips and already
blends them (`ProgramCanvas.jsx` → `gl/compositor.js`); `blend_onto` in
`animatic_effects.py` already does the same arithmetic for the export. So a
library of sixteen "wow" effects cost **zero renderer changes on either side**,
and every one of them is scrubbable, trimmable, fade-able and deletable like any
other clip the moment it exists.

That is not a shortcut, it is how these effects are made everywhere. A light leak
in Premiere is a video file on a Screen layer. This app just draws its own.

---------------------------------------------------------------------------
⚠ GENERATED, NOT DOWNLOADED — AND THAT IS A LICENSING DECISION, NOT A TECHNICAL
ONE.
---------------------------------------------------------------------------
The ordinary way to ship this is to buy a pack of stock overlays, which costs
money and drags a licence into the product that has to be honoured by every
customer who exports a video. Everything in here is `numpy` and `Pillow` — no
download, no third-party asset, no attribution, nothing to renew, and nothing
that can be revoked. It also means an overlay is generated at the EXACT frame
size and length the project needs, so nothing is ever scaled or looped to fit.

---------------------------------------------------------------------------
⚠ EACH OVERLAY IS DRAWN AT ITS OWN RESOLUTION, AND THAT ONE NUMBER IS MOST OF
THE COST.
---------------------------------------------------------------------------
A light leak is a few blurred blobs: computing it at 1920×1080 is sixty times the
arithmetic for a picture nobody can tell from the same thing computed at 240×135
and enlarged. Grain is the opposite — the sharpness IS the effect — but not
*fully* the opposite: at full size it is 56 MB of incompressible noise and nine
seconds of work, and at a half it is the same effect in a fraction of both. So
`detail` is a FRACTION per entry (⅛ for the soft ones, ½ for the textures, 1 for
sparse particles) rather than a flag, and `crf` rides beside it because noise and
gradients want opposite things from an encoder. Both numbers were measured; the
sizes are in the Work Log.

---------------------------------------------------------------------------
⚠ THE BLEND MODE IS PART OF THE EFFECT, NOT A SETTING THE USER MUST GUESS.
---------------------------------------------------------------------------
A light leak on "normal" is an opaque rectangle of orange over the shot; the same
file on "screen" is a light leak. So every entry names its own blend and the
route hands it back with the clip, which is what makes dropping one a single
gesture instead of a gesture plus a lookup. The neutral value differs per mode
and each generator is built around its own:
  · screen   — BLACK is neutral (nothing added). Draw light on black.
  · overlay  — MID GREY is neutral. Draw darker and lighter than 128.
  · multiply — WHITE is neutral. Draw the shadow, leave the rest white.
Get that wrong and the overlay tints the whole shot instead of marking it.

⚠ TWIN FILE: `client/src/animatic/fx_overlays.js` holds the same catalogue —
ids, labels, notes, blends and default lengths — because the browser draws the
shelf and this module makes the pictures. `tests/fx_overlay_check.py` compares
them entry for entry by running the JS under node, the same way the font list and
the caption styles are compared. **The PIXELS live only here**; the browser never
computes one.
"""

from __future__ import annotations

import math
import subprocess
import tempfile

import numpy as np
from PIL import Image

from animatic import AnimaticError, ffmpeg_exe

# The smallest a computed frame may get, whatever `detail` asks for — below this
# even a blur has visible steps once it is enlarged.
MIN_DETAIL_PX = 32

# ⚠ `detail` IS A FRACTION OF THE FRAME, NOT A FLAG, AND IT IS THE SINGLE MOST
# IMPORTANT NUMBER IN THIS FILE. It began as a boolean — "soft things may be
# drawn small" — and that was too blunt in both directions. A light leak is happy
# at an eighth; grain drawn FULL SIZE is 56 MB of incompressible noise taking
# nine seconds, and the same grain at a HALF is the same effect four times
# faster in a fraction of the space — arguably better, because slightly coarser
# grain reads more like film and less like sensor noise. Measured, not guessed.
#
# ⚠ AND `crf` GOES WITH IT. Noise is incompressible by definition, so spending
# crf 18 on it buys nothing anybody can see and costs tens of megabytes; the soft
# overlays are the opposite and band visibly on `screen` if the quality drops.
# One knob per entry rather than one number for the whole file.
DEFAULT_CRF = 18

# What a generated overlay is, unless the caller says otherwise. ⚠ FOUR SECONDS,
# because these are almost always dropped on ONE cut rather than run under a whole
# film — and a clip you have to trim every time is a worse default than one you
# occasionally stretch. `MAX_SECONDS` is a guard on the route, not a design view:
# a minute of 4K noise is a 300MB file nobody asked for.
DEFAULT_SECONDS = 4.0
MAX_SECONDS = 30.0
MIN_SECONDS = 0.5

DEFAULT_FPS = 24


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------
# ⚠ APPENDED, NEVER RE-ORDERED, AND AN ID IS FOREVER. A generated overlay becomes
# an ordinary upload the moment it is made, so a renamed id cannot corrupt an
# existing project — but the browser's shelf, the route and this table are
# addressed by it, and a saved plan or a test naming one should keep working.
#
# `blend` is the mode the clip is created with; `detail` is the fraction of the
# frame it is computed at before being enlarged; `crf` is its own quality knob
# (see above); `seconds` is what the shelf offers by default.
OVERLAYS: list[dict] = [
    # --- Light ------------------------------------------------------------
    {
        "id": "light-leak-warm", "label": "Light leak — warm", "category": "light",
        "note": "Orange and gold bleeding in from the edge, drifting.",
        "blend": "screen", "detail": 0.125, "seconds": 5.0,
    },
    {
        "id": "light-leak-cool", "label": "Light leak — cool", "category": "light",
        "note": "The same idea in blue and magenta. Night, neon, a screen's glow.",
        "blend": "screen", "detail": 0.125, "seconds": 5.0,
    },
    {
        "id": "light-sweep", "label": "Light sweep", "category": "light",
        "note": "A soft bar of light travelling across the frame once.",
        "blend": "screen", "detail": 0.125, "seconds": 2.0,
    },
    {
        "id": "god-rays", "label": "Sun rays", "category": "light",
        "note": "Angled shafts from a corner, breathing slowly.",
        "blend": "screen", "detail": 0.125, "seconds": 6.0,
    },
    {
        "id": "film-burn", "label": "Film burn", "category": "light",
        "note": "A flare blooms out of one corner and burns away. Good ON a cut.",
        "blend": "screen", "detail": 0.125, "seconds": 1.6,
    },
    {
        "id": "flash", "label": "Flash", "category": "light",
        "note": "One white pulse. A camera, a hit, a beat.",
        "blend": "screen", "detail": 0.125, "seconds": 0.6,
    },

    # --- Particles ---------------------------------------------------------
    {
        "id": "bokeh", "label": "Bokeh", "category": "particles",
        "note": "Out-of-focus circles of light drifting past.",
        "blend": "screen", "detail": 0.125, "seconds": 6.0,
    },
    {
        "id": "dust-motes", "label": "Dust motes", "category": "particles",
        "note": "Specks floating in a sunbeam. Very quiet, very expensive-looking.",
        "blend": "screen", "detail": 1.0, "seconds": 6.0,
    },
    {
        "id": "sparkle", "label": "Sparkle", "category": "particles",
        "note": "Small stars that twinkle on and off. Festive, magical.",
        "blend": "screen", "detail": 1.0, "seconds": 4.0,
    },
    {
        "id": "snow", "label": "Snow", "category": "particles",
        "note": "Flakes drifting down and sideways.",
        "blend": "screen", "detail": 1.0, "seconds": 6.0,
    },
    {
        "id": "rain", "label": "Rain", "category": "particles",
        "note": "Angled streaks falling fast.",
        "blend": "screen", "detail": 1.0, "seconds": 5.0,
    },

    # --- Texture -----------------------------------------------------------
    {
        "id": "grain", "label": "Film grain", "category": "texture",
        "note": "Fine moving grain over everything. Takes the digital edge off.",
        "blend": "overlay", "detail": 0.5, "crf": 28, "seconds": 4.0,
    },
    {
        "id": "old-film", "label": "Old film", "category": "texture",
        "note": "Scratches, specks and a flicker in the exposure.",
        "blend": "overlay", "detail": 0.5, "crf": 28, "seconds": 5.0,
    },
    {
        "id": "vhs", "label": "VHS", "category": "texture",
        "note": "Scanlines and bands that tear sideways.",
        "blend": "overlay", "detail": 0.5, "crf": 28, "seconds": 4.0,
    },
    {
        "id": "vignette", "label": "Vignette", "category": "texture",
        "note": "Darkens the corners and breathes. Pulls the eye to the middle.",
        "blend": "multiply", "detail": 0.125, "seconds": 6.0,
    },

    # --- Glitch ------------------------------------------------------------
    {
        "id": "glitch", "label": "Glitch", "category": "glitch",
        "note": "Torn bands of red and cyan, in bursts. For a hard cut.",
        "blend": "screen", "detail": 1.0, "seconds": 2.0,
    },
]

OVERLAY_IDS = [o["id"] for o in OVERLAYS]
BY_ID = {o["id"]: o for o in OVERLAYS}

# The shelves the browser draws. ⚠ A VIEW, not the truth — an overlay whose
# category names none of these still appears, under "Other", the same rule
# `fx_library.js` and the preset shelves keep.
CATEGORIES = [
    {"id": "light", "label": "Light", "note": "Leaks, flares and sweeps. Drop on 'screen'."},
    {"id": "particles", "label": "In the air", "note": "Things drifting between the lens and the shot."},
    {"id": "texture", "label": "Texture", "note": "Grain, scratches and scanlines over everything."},
    {"id": "glitch", "label": "Glitch", "note": "Digital damage, in bursts."},
]


def catalogue() -> list[dict]:
    """The shelf, as the browser and the tests want it."""
    return [dict(o) for o in OVERLAYS]


# ---------------------------------------------------------------------------
# Generating one
# ---------------------------------------------------------------------------
def render(
    kind: str,
    path: str,
    *,
    width: int,
    height: int,
    seconds: float | None = None,
    fps: int = DEFAULT_FPS,
    seed: int | None = None,
) -> dict:
    """Write one overlay to `path` as an MP4. Returns `{duration_ms, blend, …}`.

    ⚠ EVERY RUN OF THE SAME KIND IS A DIFFERENT PICTURE unless `seed` is given.
    Two light leaks on two cuts of one film that are the same file frame for frame
    read as a mistake — which is exactly what a bought pack of twelve leaks gives
    you on the thirteenth cut. A generator has no such ceiling, so it does not
    pretend to one; `seed` exists for the tests, which need the same picture twice.
    """
    entry = BY_ID.get(kind)
    if not entry:
        raise AnimaticError(f"There is no “{kind}” overlay in this build.")

    width = max(16, int(width))
    height = max(16, int(height))
    # ⚠ EVEN DIMENSIONS OR H.264 REFUSES THE FILE. yuv420p halves both axes, so an
    # odd width is "width not divisible by 2" and a failed encode at the very end
    # of the work — after every frame has already been computed.
    width -= width % 2
    height -= height % 2

    span = float(seconds if seconds is not None else entry["seconds"])
    span = max(MIN_SECONDS, min(MAX_SECONDS, span))
    fps = max(1, min(60, int(fps or DEFAULT_FPS)))
    count = max(1, int(round(span * fps)))
    rng = np.random.default_rng(seed)

    maker = _MAKERS[kind]
    detail = float(entry.get("detail", 1.0))
    if detail >= 0.999:
        frames = (maker(width, height, i, count, rng) for i in range(count))
    else:
        # Computed small and enlarged — see `detail` in the header. The small
        # frame keeps the frame's ASPECT, or a leak drawn on a square and
        # stretched to 16:9 comes out as ovals nobody drew.
        small_h = max(MIN_DETAIL_PX, int(round(height * detail)))
        small_w = max(MIN_DETAIL_PX, int(round(small_h * width / height)))
        small_w -= small_w % 2
        small_h -= small_h % 2
        frames = (
            _enlarge(maker(small_w, small_h, i, count, rng), width, height)
            for i in range(count)
        )

    _encode(frames, path, width=width, height=height, fps=fps,
            crf=int(entry.get("crf", DEFAULT_CRF)))
    return {
        "kind": kind,
        "blend": entry["blend"],
        "duration_ms": int(round(count * 1000 / fps)),
        "width": width,
        "height": height,
        "fps": fps,
    }


def _enlarge(small: np.ndarray, width: int, height: int) -> np.ndarray:
    """Blow a small soft frame up to the real one. Bilinear, because these are
    blurs already and a sharper filter would only ring on their edges."""
    return np.asarray(
        Image.fromarray(small, "RGB").resize((width, height), Image.BILINEAR)
    )


def _encode(frames, path: str, *, width: int, height: int, fps: int,
            crf: int = DEFAULT_CRF) -> None:
    """Pipe raw RGB frames straight into ffmpeg.

    ⚠ NOT `animatic.run_ffmpeg`, AND THE REASON IS STRUCTURAL RATHER THAN
    STYLISTIC. That runner exists to drive ffmpeg over files it already has, with
    progress scraped from `-progress pipe:1` and a cancel check; it never writes
    to ffmpeg's stdin, and teaching it to would mean interleaving a write loop
    with its two drain threads for one caller. This writes frames as they are
    computed — no temporary PNGs, no second pass over a hundred 1080p images —
    which is most of the reason an overlay takes a couple of seconds.

    ⚠ AND stdout/stderr ARE DRAINED TO A FILE, not to a pipe nobody reads. ffmpeg
    is chatty; a full pipe buffer with nothing reading it is a deadlock, and the
    symptom is a generation that simply never returns.
    """
    exe = ffmpeg_exe()
    cmd = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        # ⚠ THE QUALITY IS PER OVERLAY, NOT PER FILE. A leak composited on
        # "screen" shows its own banding far more readily than a picture does, so
        # it gets crf 18; noise is incompressible and crf 18 on it buys nothing
        # visible for tens of megabytes, so grain and its relatives get 28. See
        # `crf` in the catalogue.
        "-crf", str(max(0, min(51, int(crf)))), "-preset", "veryfast",
        "-movflags", "+faststart",
        path,
    ]
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            for frame in frames:
                proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
        except BrokenPipeError:
            # ffmpeg died early — its own message is in the log and is far more
            # useful than "broken pipe", so fall through to the return code.
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
        code = proc.wait()
        if code != 0:
            log.seek(0)
            detail = log.read().decode("utf-8", "replace").strip()[-600:]
            raise AnimaticError(f"The overlay could not be encoded. {detail}")


# ---------------------------------------------------------------------------
# The shared arithmetic
# ---------------------------------------------------------------------------
def _grid(w: int, h: int):
    """Normalised x/y planes, 0–1, shaped (h, w). Cached per size: every maker
    asks for the same two arrays on every frame of a run."""
    key = (w, h)
    got = _GRID_CACHE.get(key)
    if got is None:
        ys, xs = np.mgrid[0:h, 0:w]
        got = (xs / max(1, w - 1), ys / max(1, h - 1))
        _GRID_CACHE[key] = got
    return got


_GRID_CACHE: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}


def _blob(w: int, h: int, cx: float, cy: float, radius: float, aspect: float = 1.0):
    """One soft round falloff, 0–1, centred at (cx, cy) in 0–1 coordinates.

    Squared falloff rather than a true gaussian: it reaches zero at the radius
    instead of trailing off for ever, so a leak has an edge somewhere and two of
    them do not fog the whole frame.
    """
    xs, ys = _grid(w, h)
    dx = (xs - cx) / max(1e-6, radius * aspect)
    dy = (ys - cy) / max(1e-6, radius)
    d = dx * dx + dy * dy
    return np.clip(1.0 - d, 0.0, 1.0) ** 2


def _black(w: int, h: int) -> np.ndarray:
    """A frame that is NEUTRAL on `screen` — nothing added to the shot."""
    return np.zeros((h, w, 3), dtype=np.float32)


def _grey(w: int, h: int) -> np.ndarray:
    """A frame that is NEUTRAL on `overlay` — 0.5 leaves the picture alone."""
    return np.full((h, w, 3), 0.5, dtype=np.float32)


def _white(w: int, h: int) -> np.ndarray:
    """A frame that is NEUTRAL on `multiply` — white changes nothing."""
    return np.ones((h, w, 3), dtype=np.float32)


def _out(rgb: np.ndarray) -> np.ndarray:
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _ease_in_out(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


def _pulse(u: float, peak: float = 0.25) -> float:
    """0 → 1 → 0 across `u`, peaking at `peak`. The shape of a flare."""
    if u <= 0.0 or u >= 1.0:
        return 0.0
    if u < peak:
        return _ease_in_out(u / peak)
    return _ease_in_out(1.0 - (u - peak) / max(1e-6, 1.0 - peak))


# ---------------------------------------------------------------------------
# The makers. Each returns ONE uint8 RGB frame.
# ---------------------------------------------------------------------------
def _leak(w, h, i, n, rng, warm=True):
    u = i / max(1, n)
    out = _black(w, h)
    # ⚠ THE SEED IS DRAWN ONCE PER RUN, NOT PER FRAME. `rng` is called only on
    # frame 0 and the parameters are cached on the generator's own state, or the
    # blobs would jump to new places every twenty-fourth of a second instead of
    # drifting.
    lamps = _once(rng, "leak", lambda: [
        {
            # Near an edge, because that is where light gets in.
            "x": float(rng.choice([rng.uniform(-0.15, 0.25), rng.uniform(0.75, 1.15)])),
            "y": float(rng.uniform(-0.1, 1.1)),
            "r": float(rng.uniform(0.35, 0.75)),
            "speed": float(rng.uniform(0.4, 1.1)),
            "phase": float(rng.uniform(0, math.tau)),
            "tint": rng.integers(0, 3),
        }
        for _ in range(4)
    ])
    # ⚠ THE TWO SETS ARE ENERGY-MATCHED, WHICH IS NOT THE SAME AS "BOTH LOOK
    # NICE". A cool tint reaches for green and blue together, so the obvious
    # choices — (0.25, 0.5, 1.0) and friends — sum to noticeably more light than
    # the warm ones and the cool leak came out visibly the heavier of the two
    # over the same shot. Caught by the coverage check in
    # `tests/fx_overlay_check.py` rather than by eye: the warm leak left 40% of
    # the frame untouched and the cool one 34%, for two entries whose notes claim
    # to be "the same idea" in another colour. Scaled to match.
    warm_tints = ((1.0, 0.45, 0.12), (1.0, 0.75, 0.25), (0.95, 0.2, 0.25))
    cool_tints = ((0.22, 0.42, 0.85), (0.58, 0.26, 0.85), (0.18, 0.68, 0.78))
    tints = warm_tints if warm else cool_tints
    for lamp in lamps:
        drift = math.sin(u * math.tau * lamp["speed"] + lamp["phase"])
        strength = 0.35 + 0.35 * (0.5 + 0.5 * math.cos(u * math.tau * lamp["speed"] * 0.7 + lamp["phase"]))
        field = _blob(w, h, lamp["x"] + drift * 0.08, lamp["y"] + drift * 0.05, lamp["r"], 1.3)
        tint = tints[int(lamp["tint"])]
        for c in range(3):
            out[:, :, c] += field * strength * tint[c]
    return _out(out)


def _light_leak_warm(w, h, i, n, rng):
    return _leak(w, h, i, n, rng, warm=True)


def _light_leak_cool(w, h, i, n, rng):
    return _leak(w, h, i, n, rng, warm=False)


def _light_sweep(w, h, i, n, rng):
    u = i / max(1, n - 1) if n > 1 else 1.0
    xs, ys = _grid(w, h)
    # An angled bar: distance from a moving line, softened. It starts and ends
    # fully off the frame, so the sweep has no visible pop at either end.
    angle = _once(rng, "sweep_angle", lambda: float(rng.uniform(-0.45, 0.45)))
    pos = -0.4 + u * 1.8
    band = np.abs((xs + ys * angle) - pos)
    field = np.clip(1.0 - band / 0.16, 0.0, 1.0) ** 2
    out = _black(w, h)
    for c, tint in enumerate((1.0, 0.96, 0.88)):
        out[:, :, c] = field * 0.75 * tint
    return _out(out)


def _god_rays(w, h, i, n, rng):
    u = i / max(1, n)
    xs, ys = _grid(w, h)
    corner = _once(rng, "rays_corner", lambda: (
        float(rng.choice([0.05, 0.95])), float(rng.uniform(-0.1, 0.2))
    ))
    # Angle from the source, banded into shafts. `breathe` moves the bands
    # slowly rather than spinning them, which is what light through leaves does.
    ang = np.arctan2(ys - corner[1], xs - corner[0])
    breathe = math.sin(u * math.tau) * 0.06
    shafts = 0.5 + 0.5 * np.cos(ang * 9.0 + breathe * 9.0)
    shafts = shafts ** 3
    # Fading with distance, or the rays are as strong in the far corner as at
    # the window.
    dist = np.sqrt((xs - corner[0]) ** 2 + (ys - corner[1]) ** 2)
    falloff = np.clip(1.0 - dist / 1.15, 0.0, 1.0) ** 1.5
    field = shafts * falloff * (0.45 + 0.1 * math.sin(u * math.tau))
    out = _black(w, h)
    for c, tint in enumerate((1.0, 0.93, 0.75)):
        out[:, :, c] = field * tint
    return _out(out)


def _film_burn(w, h, i, n, rng):
    u = i / max(1, n - 1) if n > 1 else 1.0
    corner = _once(rng, "burn_corner", lambda: (
        float(rng.choice([0.0, 1.0])), float(rng.uniform(0.15, 0.85))
    ))
    amp = _pulse(u, 0.3)
    # The flare grows as it brightens — a burn spreads, it does not just get
    # lighter — and a white core rides inside the orange.
    body = _blob(w, h, corner[0], corner[1], 0.35 + 0.7 * amp, 1.2) * amp
    core = _blob(w, h, corner[0], corner[1], 0.12 + 0.35 * amp, 1.2) * amp
    out = _black(w, h)
    for c, tint in enumerate((1.0, 0.55, 0.18)):
        out[:, :, c] = body * 1.15 * tint + core * 0.85
    return _out(out)


def _flash(w, h, i, n, rng):  # noqa: ARG001 — every maker takes the same five
    u = i / max(1, n - 1) if n > 1 else 1.0
    # ⚠ PEAKS ALMOST IMMEDIATELY AND FALLS AWAY. A symmetric flash reads as a
    # fade up and down; a real one is instant and then decays.
    amp = _pulse(u, 0.12)
    out = _black(w, h)
    out[:, :, :] = amp * 0.92
    return _out(out)


def _bokeh(w, h, i, n, rng):
    u = i / max(1, n)
    discs = _once(rng, "bokeh", lambda: [
        {
            "x": float(rng.uniform(-0.1, 1.1)),
            "y": float(rng.uniform(-0.1, 1.1)),
            "r": float(rng.uniform(0.04, 0.16)),
            "dx": float(rng.uniform(-0.06, 0.06)),
            "dy": float(rng.uniform(-0.05, 0.02)),
            "a": float(rng.uniform(0.18, 0.5)),
            "phase": float(rng.uniform(0, math.tau)),
            "tint": int(rng.integers(0, 3)),
        }
        for _ in range(18)
    ])
    tints = ((1.0, 0.92, 0.75), (0.8, 0.9, 1.0), (1.0, 0.8, 0.9))
    out = _black(w, h)
    for d in discs:
        x = (d["x"] + d["dx"] * u) % 1.2 - 0.1
        y = (d["y"] + d["dy"] * u) % 1.2 - 0.1
        twinkle = 0.75 + 0.25 * math.sin(u * math.tau * 1.5 + d["phase"])
        field = _blob(w, h, x, y, d["r"])
        tint = tints[d["tint"]]
        for c in range(3):
            out[:, :, c] += field * d["a"] * twinkle * tint[c]
    return _out(out)


def _points(w, h, i, n, rng, key, count, spec, tint=(1.0, 1.0, 1.0)):
    """Shared body for the sharp particle overlays — dust, sparkle, snow.

    Drawn by INDEXING rather than by compositing a field per particle: two
    hundred `_blob` calls on a 1080p frame is two hundred full-frame arrays, and
    these are small bright points rather than soft washes.
    """
    parts = _once(rng, key, lambda: spec(rng))
    u = i / max(1, n)
    out = _black(w, h)
    for p in parts:
        x, y, size, bright = p["at"](u)
        if bright <= 0.001:
            continue
        px, py = int(x * w), int(y * h)
        rad = max(1, int(size * h))
        x0, x1 = max(0, px - rad), min(w, px + rad + 1)
        y0, y1 = max(0, py - rad), min(h, py + rad + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        ys, xs = np.mgrid[y0:y1, x0:x1]
        d = ((xs - px) ** 2 + (ys - py) ** 2) / max(1.0, float(rad * rad))
        field = np.clip(1.0 - d, 0.0, 1.0) ** 2 * bright
        for c in range(3):
            out[y0:y1, x0:x1, c] += field * tint[c]
    return out


def _dust_motes(w, h, i, n, rng):
    def spec(r):
        out = []
        for _ in range(90):
            x0, y0 = float(r.uniform(0, 1)), float(r.uniform(0, 1))
            dx, dy = float(r.uniform(-0.05, 0.05)), float(r.uniform(-0.06, -0.01))
            size = float(r.uniform(0.0015, 0.005))
            phase = float(r.uniform(0, math.tau))
            base = float(r.uniform(0.25, 0.7))
            out.append({"at": lambda u, x0=x0, y0=y0, dx=dx, dy=dy, size=size,
                        phase=phase, base=base: (
                (x0 + dx * u + 0.01 * math.sin(u * math.tau + phase)) % 1.0,
                (y0 + dy * u) % 1.0,
                size,
                base * (0.5 + 0.5 * math.sin(u * math.tau * 2 + phase)),
            )})
        return out

    return _out(_points(w, h, i, n, rng, "dust", 90, spec, (1.0, 0.97, 0.88)))


def _sparkle(w, h, i, n, rng):
    def spec(r):
        out = []
        for _ in range(55):
            x0, y0 = float(r.uniform(0, 1)), float(r.uniform(0, 1))
            size = float(r.uniform(0.004, 0.012))
            phase = float(r.uniform(0, math.tau))
            rate = float(r.uniform(2.0, 5.0))
            # ⚠ THE POWER IS WHAT MAKES IT A TWINKLE. A plain sine is a field of
            # points all breathing together; raising it makes each one dark most
            # of the time and briefly very bright, which is what a sparkle is.
            out.append({"at": lambda u, x0=x0, y0=y0, size=size, phase=phase, rate=rate: (
                x0, y0, size,
                max(0.0, math.sin(u * math.tau * rate + phase)) ** 6,
            )})
        return out

    return _out(_points(w, h, i, n, rng, "sparkle", 55, spec, (1.0, 0.98, 0.9)))


def _snow(w, h, i, n, rng):
    def spec(r):
        out = []
        for _ in range(140):
            x0, y0 = float(r.uniform(0, 1)), float(r.uniform(0, 1))
            fall = float(r.uniform(0.35, 0.9))
            sway = float(r.uniform(0.01, 0.04))
            size = float(r.uniform(0.002, 0.007))
            phase = float(r.uniform(0, math.tau))
            bright = float(r.uniform(0.4, 0.9))
            out.append({"at": lambda u, x0=x0, y0=y0, fall=fall, sway=sway,
                        size=size, phase=phase, bright=bright: (
                (x0 + sway * math.sin(u * math.tau * 1.5 + phase)) % 1.0,
                (y0 + fall * u) % 1.0,
                size,
                bright,
            )})
        return out

    return _out(_points(w, h, i, n, rng, "snow", 140, spec))


def _rain(w, h, i, n, rng):
    u = i / max(1, n)
    drops = _once(rng, "rain", lambda: [
        {
            "x": float(rng.uniform(-0.1, 1.1)),
            "y": float(rng.uniform(0, 1)),
            "len": float(rng.uniform(0.05, 0.14)),
            "speed": float(rng.uniform(2.0, 3.4)),
            "a": float(rng.uniform(0.15, 0.4)),
        }
        for _ in range(120)
    ])
    slant = 0.18
    out = _black(w, h)
    for d in drops:
        y = (d["y"] + d["speed"] * u) % 1.15 - 0.1
        # A streak is a short line, drawn as a handful of points down its length.
        steps = max(2, int(d["len"] * h / 3))
        for s in range(steps):
            t = s / max(1, steps - 1)
            px = int((d["x"] + slant * d["len"] * t) * w)
            py = int((y + d["len"] * t) * h)
            if 0 <= px < w and 0 <= py < h:
                out[py, px, :] += d["a"] * (1.0 - t * 0.6)
    return _out(out)


def _grain(w, h, i, n, rng):
    # ⚠ MONOCHROME, AND CENTRED ON THE NEUTRAL VALUE FOR `overlay`. Coloured
    # noise reads as a broken sensor rather than as film, and noise that is not
    # centred on 0.5 lifts or crushes the whole shot as well as texturing it.
    noise = rng.normal(0.0, 0.055, size=(h, w)).astype(np.float32)
    out = _grey(w, h)
    out += noise[:, :, None]
    return _out(out)


def _old_film(w, h, i, n, rng):
    out = _grey(w, h)
    # A slow flicker in the exposure — the single most recognisable thing about
    # projected film, and one number.
    out += float(rng.normal(0.0, 0.012))
    # Vertical scratches: a few, each lasting a handful of frames rather than one,
    # or they strobe instead of scratching.
    for _ in range(int(rng.integers(0, 4))):
        x = int(rng.integers(0, max(1, w)))
        width_px = int(rng.integers(1, max(2, w // 400 + 2)))
        depth = float(rng.uniform(0.08, 0.3)) * (1 if rng.random() < 0.35 else -1)
        out[:, x:x + width_px, :] += depth
    # Specks and hairs.
    for _ in range(int(rng.integers(2, 14))):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        r = int(rng.integers(1, max(2, h // 220 + 2)))
        out[max(0, y - r):y + r, max(0, x - r):x + r, :] -= float(rng.uniform(0.1, 0.35))
    # Fine grain under all of it, because old film has that too.
    out += rng.normal(0.0, 0.035, size=(h, w)).astype(np.float32)[:, :, None]
    return _out(out)


def _vhs(w, h, i, n, rng):
    out = _grey(w, h)
    # Scanlines: every other row, slowly rolling, so the pattern moves the way a
    # mistimed field does instead of sitting still like a screen door.
    roll = int((i * 1.7) % 2)
    out[roll::2, :, :] -= 0.06
    # Torn bands: a few rows shifted sideways and tinted.
    for _ in range(int(rng.integers(0, 4))):
        y = int(rng.integers(0, h))
        band = int(rng.integers(2, max(3, h // 30)))
        y1 = min(h, y + band)
        shift = int(rng.integers(-w // 40, w // 40 + 1))
        out[y:y1, :, :] = np.roll(out[y:y1, :, :], shift, axis=1)
        out[y:y1, :, 0] += 0.05
        out[y:y1, :, 2] += 0.04
    out += rng.normal(0.0, 0.02, size=(h, w)).astype(np.float32)[:, :, None]
    return _out(out)


def _vignette(w, h, i, n, rng):
    u = i / max(1, n)
    xs, ys = _grid(w, h)
    # Measured from the centre in FRAME units, so the darkening is round on a
    # square and elliptical on 16:9 — which is what a lens actually does.
    d = np.sqrt((xs - 0.5) ** 2 + (ys - 0.5) ** 2) / 0.7071
    breathe = 0.62 + 0.03 * math.sin(u * math.tau)
    field = np.clip((d - breathe) / max(1e-6, 1.0 - breathe), 0.0, 1.0) ** 1.6
    out = _white(w, h)
    out -= field[:, :, None] * 0.62
    return _out(out)


def _glitch(w, h, i, n, rng):
    u = i / max(1, n)
    out = _black(w, h)
    # ⚠ IN BURSTS, NOT CONTINUOUSLY. Damage that never stops reads as a broken
    # export; damage that comes and goes reads as an effect. Roughly a third of
    # the frames carry anything at all.
    if rng.random() > 0.34 + 0.3 * abs(math.sin(u * math.tau * 3)):
        return _out(out)
    for _ in range(int(rng.integers(1, 6))):
        y = int(rng.integers(0, h))
        band = int(rng.integers(2, max(3, h // 18)))
        y1 = min(h, y + band)
        # A torn band is red one way and cyan the other — the RGB split that
        # says "digital" more than any amount of noise does.
        off = int(rng.integers(3, max(4, w // 30)))
        strength = float(rng.uniform(0.25, 0.7))
        out[y:y1, off:, 0] += strength
        out[y:y1, :-off, 1] += strength * 0.5
        out[y:y1, :-off, 2] += strength
    return _out(out)


# ⚠ ONE ENTRY PER `OVERLAYS` ROW, CHECKED AT IMPORT. A catalogue entry with no
# maker is a button that 500s; a maker with no entry is dead code nobody can
# reach. Both are caught here rather than by the first person to press it.
_MAKERS = {
    "light-leak-warm": _light_leak_warm,
    "light-leak-cool": _light_leak_cool,
    "light-sweep": _light_sweep,
    "god-rays": _god_rays,
    "film-burn": _film_burn,
    "flash": _flash,
    "bokeh": _bokeh,
    "dust-motes": _dust_motes,
    "sparkle": _sparkle,
    "snow": _snow,
    "rain": _rain,
    "grain": _grain,
    "old-film": _old_film,
    "vhs": _vhs,
    "vignette": _vignette,
    "glitch": _glitch,
}

_missing = [o["id"] for o in OVERLAYS if o["id"] not in _MAKERS]
_stray = [k for k in _MAKERS if k not in BY_ID]
if _missing or _stray:  # pragma: no cover — an import-time contract
    raise RuntimeError(
        f"fx_overlays catalogue and makers disagree: missing {_missing}, stray {_stray}"
    )


# ---------------------------------------------------------------------------
# Per-run scratch state
# ---------------------------------------------------------------------------
# ⚠ WHY THIS EXISTS: a maker is called once per FRAME, and the things that make
# an overlay coherent — where the lamps are, which corner burns, how many drops
# — are chosen once per RUN. Drawing them from `rng` inside the maker would give
# every frame a new set and the result would boil rather than drift. `render`
# clears it before each run, so two overlays never share a set of lamps.
_RUN: dict[str, object] = {}


def _once(rng, key: str, make):
    got = _RUN.get(key)
    if got is None:
        got = make()
        _RUN[key] = got
    return got


_render_inner = render


def render(kind: str, path: str, **kwargs) -> dict:  # noqa: F811 — wraps the real one
    """`render`, with the per-run scratch state cleared around it.

    ⚠ A WRAPPER RATHER THAN A LINE INSIDE, so there is no path out of a
    generation — including an exception — that leaves one run's lamps behind for
    the next. Two light leaks that came out identical because the second reused
    the first's parameters is exactly the bug this shape prevents.
    """
    _RUN.clear()
    try:
        return _render_inner(kind, path, **kwargs)
    finally:
        _RUN.clear()
