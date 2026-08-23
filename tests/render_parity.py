"""The animatic scene model is written TWICE. This proves the two agree.

`client/src/animatic/scene.js` decides what the Program monitor shows;
`animatic_render.py` decides what the exported MP4 shows. They are the same
rules in two languages, and the only thing standing between them and silent
divergence is this file. Without it the preview eventually lies about the
export, which is the single worst failure an editor can have — you cannot trust
anything you see.

⚠ THE COMPARISON IS ON NUMBERS, NOT PIXELS. Canvas and Pillow will never produce
identical bytes (different rasterisers, different text metrics, different
resampling), so a pixel diff would fail forever and get switched off. What CAN
be identical, and is what actually drifts in practice, is the resolved scene:
which clips are on screen at t and what every animated property has been
interpolated to. That is what is checked here, to six decimal places.

The fixture is deliberately nasty: keys given out of order, a single lone key,
a track whose keys sit outside the clip, every easing curve, a shape that fades
from fully transparent, and times landing exactly on cut boundaries. Those are
the cases where two implementations part company.

    python tests/render_parity.py

Needs `node` on PATH (the same one `npm run build` uses).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from animatic_render import (
    MATTE_KINDS,
    TRANSITION_KINDS,
    frame_spans,
    is_animated,
    scene_at,
    scene_signature,
    transition_matte,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENE_JS = os.path.join(ROOT, "client", "src", "animatic", "scene.js")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------
# Three pictures of different lengths, so cut boundaries fall at 1500 / 4000 /
# 4900. Several of the sample times below land exactly on one.
PROJECT = {
    "frames": [
        # A Ken Burns push: the picture zooms 1.0 → 1.25 while drifting left.
        # Keys are RELATIVE to this frame's own start, which is the rule most
        # likely to be got wrong on one side.
        # It also carries a GRADE THAT MOVES (Phase 4): a LUT dialling in over
        # the push and a mask closing down on it. Both are addressed by the flat
        # track names `fx:<id>:<param>` and `mask:<field>`, which is the one
        # piece of the keyframe design the two languages could parse
        # differently — an effect id containing a colon, a missing id, a
        # parameter that is a string. All three are exercised in this fixture.
        {
            "id": "fr1",
            "duration_ms": 1500,
            "label": "Shot 1",
            "effects": [
                {"id": "e1", "kind": "lut", "params": {"name": "noir", "amount": 0.0}},
                # A parameter LEFT OUT: `smoothness` and `spill` must arrive at
                # their defaults on both sides rather than as null or 0.
                {"id": "e2", "kind": "chroma", "params": {"color": "#12ff34", "similarity": 0.4}},
                # An effect this build has never heard of. Both evaluators must
                # DROP it from the resolved scene — the same forgiveness `ease`
                # and `clip_kind` get — while leaving it in the project.
                {"id": "e3", "kind": "kaleidoscope", "params": {"amount": 3}},
                # NO ID. Both sides must fall back to the effect's position, and
                # to the SAME position — the raw index, counted before the
                # unknown kind above is dropped, or the two disagree by one.
                {"kind": "brightness", "params": {"amount": 1.1}},
            ],
            "mask": {"kind": "ellipse", "x": 0.5, "y": 0.5, "w": 0.9, "h": 0.9,
                     "feather": 0.2, "invert": False},
            "blend": "screen",
            "keyframes": {
                "scale": [
                    {"t": 0, "v": 1.0, "ease": "ease-in-out"},
                    {"t": 1500, "v": 1.25},
                ],
                "x": [{"t": 0, "v": 0.5}, {"t": 1500, "v": 0.42}],
                "fx:e1:amount": [
                    {"t": 0, "v": 0.0, "ease": "ease-in"},
                    {"t": 1500, "v": 1.0},
                ],
                "mask:w": [{"t": 0, "v": 0.9}, {"t": 1500, "v": 0.35}],
                # A track naming the effect at index 3, which carries no id.
                "fx:3:amount": [{"t": 0, "v": 1.1}, {"t": 1500, "v": 0.7}],
            },
        },
        # No keyframes at all — the common case, and it must stay identical to
        # what the project did before any of this existed.
        {"id": "fr2", "duration_ms": 2500, "label": "Shot 2"},
        # A lone key. One key is not an animation; the value simply holds.
        {"id": "fr3", "duration_ms": 900, "keyframes": {"opacity": [{"t": 200, "v": 0.5}]}},
        # --- Clips (Phase 3) ------------------------------------------------
        # A VIDEO CLIP AT DOUBLE SPEED, with both ends of its source window set.
        # 1600ms of timeline at speed 2 reads 3200ms of source starting at 500,
        # i.e. 500 → 3700 — but `out_ms` is 3000, so the last 350ms of the clip
        # HOLD source frame 2999. Both the ramp and the hold are sampled below.
        # This is the one clip in the fixture where the two languages have a
        # brand-new number to disagree about.
        {
            "id": "fr4",
            "duration_ms": 1600,
            "label": "A take",
            "kind": "video",
            "src": {"kind": "video", "upload_id": "v1"},
            "in_ms": 500,
            "out_ms": 3000,
            "speed": 2.0,
            # Keyframed AS WELL, because a video clip is still a picture on the
            # canvas: the source range and the pan/zoom are independent, and
            # resolving one must not disturb the other.
            "keyframes": {"scale": [{"t": 0, "v": 1.0}, {"t": 1600, "v": 1.4}]},
        },
        # A COLOUR CARD. No file, no source time — `source_ms` must be null on
        # both sides, and `color` must survive rather than defaulting to black.
        {"id": "fr5", "duration_ms": 600, "kind": "color", "color": "#204060"},
        # A clip whose kind this build has never heard of. Both evaluators must
        # fold it down to "image" — the same forgiveness `ease` and the
        # transition kinds get, and what lets a newer client's project open here.
        {"id": "fr6", "duration_ms": 500, "kind": "hologram", "speed": 3.0},
    ],
    "texts": [
        {
            "id": "tx1",
            "text": "A caption that fades in",
            "start_ms": 0,
            "duration_ms": 2000,
            "position": "bottom",
            "keyframes": {
                "opacity": [
                    {"t": 0, "v": 0.0, "ease": "ease-out"},
                    {"t": 600, "v": 1.0},
                    {"t": 2000, "v": 0.0},
                ]
            },
        },
        # Blank text is skipped on both sides — an unfinished caption must never
        # burn a blank bar into the video.
        {"id": "tx2", "text": "   ", "start_ms": 0, "duration_ms": 4000},
        {"id": "tx3", "text": "Static caption", "start_ms": 2000, "duration_ms": 1500},
        # --- Text (Phase 5) --------------------------------------------------
        # A FREE-PLACED title that slides up into frame while it fades in —
        # which is what `text_presets.js` writes, and the reason a caption
        # gained x/y at all. Both keys run on a curve, so this is also where the
        # two languages would part company on a caption that MOVES.
        {
            "id": "tx4",
            "text": "A title that arrives",
            "start_ms": 1000,
            "duration_ms": 2000,
            "place": "free",
            "x": 0.5,
            "y": 0.6,
            "font": "anton",
            "stroke_px": 4,
            "shadow": 0.06,
            "letter_spacing": 0.08,
            "keyframes": {
                "y": [{"t": 0, "v": 0.68, "ease": "ease-out"}, {"t": 500, "v": 0.6}],
                "opacity": [{"t": 0, "v": 0.0}, {"t": 500, "v": 1.0}],
            },
        },
        # A placement this build has never heard of. Both evaluators must fold
        # it down to "flow" — the same forgiveness `ease` and `clip_kind` get.
        {"id": "tx5", "text": "Odd placement", "start_ms": 5000, "duration_ms": 800,
         "place": "orbit", "x": 0.1, "y": 0.1},
    ],
    "shapes": [
        # Keys given OUT OF ORDER on purpose, and every easing curve exercised.
        {
            "id": "sh1",
            "kind": "rect",
            "start_ms": 500,
            "duration_ms": 3000,
            "x": 0.5,
            "y": 0.5,
            "w": 0.25,
            "h": 0.25,
            "color": "#c2185b",
            "opacity": 1.0,
            "rotation": 0.0,
            "keyframes": {
                "x": [
                    {"t": 3000, "v": 0.8, "ease": "linear"},
                    {"t": 0, "v": 0.2, "ease": "ease-in"},
                    {"t": 1500, "v": 0.5, "ease": "ease-out"},
                ],
                "rotation": [
                    {"t": 0, "v": 0, "ease": "hold"},
                    {"t": 1000, "v": 90, "ease": "ease-in-out"},
                    {"t": 3000, "v": 360},
                ],
                # ⚠ `scale` IS THE SEVENTH ANIMATABLE PROPERTY A SHAPE HAS, and
                # the newest, so it is the one most likely to be resolved by one
                # side and not the other — which is exactly what this file
                # exists to catch. Keyed on a curve, and crossing 1.0 both ways,
                # so a side that silently defaulted it would read 1.0 at every
                # sample and be caught at the first.
                "scale": [
                    {"t": 0, "v": 0.2, "ease": "ease-out"},
                    {"t": 1500, "v": 1.8, "ease": "ease-in"},
                    {"t": 3000, "v": 1.0},
                ],
            },
        },
        # Starts fully transparent. Dropping a zero-opacity clip BEFORE
        # resolving (which the old exporter did) would delete this entire fade,
        # so this shape is the regression guard for that bug.
        {
            "id": "sh2",
            "kind": "ellipse",
            "start_ms": 0,
            "duration_ms": 2000,
            "x": 0.3,
            "y": 0.3,
            "w": 0.2,
            "h": 0.2,
            "color": "#ffffff",
            "opacity": 0.0,
            "rotation": 0.0,
            "keyframes": {"opacity": [{"t": 0, "v": 0.0}, {"t": 1000, "v": 1.0}]},
        },
        # Statically invisible: no keyframes, opacity 0. Must be absent from the
        # scene at every time, on both sides.
        {
            "id": "sh3",
            "kind": "star",
            "start_ms": 0,
            "duration_ms": 5000,
            "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1,
            "color": "#000000", "opacity": 0.0, "rotation": 0.0,
        },
    ],
    "overlays": [
        # Keys sitting OUTSIDE the clip's own length. The value must HOLD at the
        # ends rather than extrapolate — extrapolating sends a clip off screen
        # the moment it is trimmed longer.
        {
            "id": "ov1",
            "upload_id": "u1",
            "start_ms": 1000,
            "duration_ms": 2000,
            "x": 0.5, "y": 0.5, "w": 0.3, "h": 0.3,
            "opacity": 1.0, "rotation": 0.0,
            # An overlay carries the SAME look a frame does — it is the other
            # clip that is a picture — and this is where a blend mode earns its
            # keep, since an overlay is the one thing that sits ON something.
            "effects": [
                {"id": "g1", "kind": "saturation", "params": {"amount": 1.6}},
            ],
            "blend": "multiply",
            "keyframes": {
                "w": [{"t": -500, "v": 0.1}, {"t": 4000, "v": 0.9}],
                "fx:g1:amount": [{"t": 0, "v": 1.6}, {"t": 2000, "v": 0.0}],
            },
        },
        # An overlay carrying NO look at all: the common case, and the one that
        # proves an empty chain, an unset mask and "normal" resolve to the same
        # SHAPE on both sides rather than to `undefined` on one and a missing
        # key on the other.
        {
            "id": "ov2",
            "upload_id": "u2",
            "start_ms": 0,
            "duration_ms": 1200,
            "x": 0.2, "y": 0.8, "w": 0.15, "h": 0.15,
            "opacity": 1.0, "rotation": 0.0,
        },
    ],
    "transitions": [
        # On the SECOND cut (4000ms), deliberately not the first: the half-open
        # rule is asserted below at 1499/1500, and a transition there would
        # (correctly) hold the outgoing picture past the cut and make that
        # assertion test something else.
        #
        # 600ms straddling 4000 → the window is 3700–4300, and 3999 / 4000 /
        # 4001 are all sampled, so both sides have to agree about the moment the
        # picture underneath changes as well as about the blend either side.
        # Carries a parameter its kind does not offer — a dissolve has none. Both
        # sides must resolve it to an EMPTY dict: parameters are scoped to the
        # KIND, so a direction left behind by switching a wipe to a dissolve is
        # ignored by both renderers. It stays in the saved project (and comes
        # back if you switch the kind back), exactly as an unknown effect does.
        {"id": "tr1", "after_frame_id": "fr2", "kind": "dissolve", "duration_ms": 600,
         "params": {"direction": "up"}},
        # Anchored to the LAST frame — the inert case: there is nothing to cut
        # to, so it must be skipped rather than crash.
        {"id": "tr2", "after_frame_id": "fr6", "kind": "wipe", "duration_ms": 5000},
        # Names a frame that isn't in the project at all — the "you deleted the
        # frame this was on" case, which must be silently inert on both sides.
        {"id": "tr3", "after_frame_id": "gone", "kind": "slide", "duration_ms": 400},
        # CLAMPED: 5s asked for, but the two holds it joins are 600 and 500, so
        # both evaluators must cut it to 500 — and to the SAME 500, or the
        # windows differ. It also lands ON a colour card, which is the cheapest
        # proof that a transition does not care what kind its neighbours are.
        # And it carries PARAMETERS: a non-default direction, which both sides
        # must keep, next to a parameter belonging to a different kind, which
        # both must drop. Getting either wrong shows up as the preview and the
        # export wiping opposite ways.
        {"id": "tr4", "after_frame_id": "fr5", "kind": "wipe", "duration_ms": 5000,
         "params": {"direction": "up", "color": "#ff0000"}},
    ],
}

# Where every clip sits, for reading the assertions below:
#   fr1 0–1500 · fr2 1500–4000 · fr3 4000–4900 · fr4 (video) 4900–6500
#   fr5 (colour) 6500–7100 · fr6 7100–7600, then held to END_MS

# Every cut boundary, either side of every boundary, the ends, and a spread of
# ordinary moments in between. A half-open visibility rule (start <= t < end) is
# what makes a cut land on exactly one picture; times ON a boundary are where an
# implementation that got that wrong shows itself.
TIMES = [
    0, 1, 250, 499, 500, 501, 600, 999, 1000, 1001,
    # A free-placed title sliding up over its first 500ms (tx4, 1000–3000).
    1050, 1150, 1250, 1350,
    1499, 1500, 1501,
    1999, 2000, 2001, 2500, 3000, 3499, 3500,
    # The transition on the 4000ms cut runs 3700–4300. Both edges, either side
    # of both edges, the midpoint, and the cut itself — a transition is where
    # two implementations disagree about rounding if they are going to.
    3699, 3700, 3701, 3850, 3999, 4000, 4001, 4150, 4299, 4300, 4301,
    4500, 4899,
    # The VIDEO clip, 4900–6500 at speed 2 out of a source window 500→3000.
    # Its source time ramps 500 → 3000 over the first 1250ms and then HOLDS at
    # 2999 for the remaining 350: both halves are sampled, and so is the moment
    # the hold begins, which is where a clamp written two different ways parts
    # company.
    4900, 4901, 5000, 5525, 6149, 6150, 6151, 6300, 6499,
    # The COLOUR card, and the clamped wipe onto it (6850–7350).
    6500, 6700, 6849, 6850, 7000, 7099, 7100, 7101, 7349, 7350, 7351,
    # The last clip, and past the end of every picture.
    7400, 7599, 7600, 8000, 8599,
]
# Longer than the pictures (which run to 7600): the last frame must be HELD out
# to here, which is what makes an export cover a music bed that outlasts them.
END_MS = 8600


# ---------------------------------------------------------------------------
# The MULTI-TRACK fixture — the shape the picture track took on when it stopped
# being one sequence
# ---------------------------------------------------------------------------
# ⚠ EVERY WAY THE NEW PLACEMENT CAN GO WRONG, in one project:
#   · a GAP on the base track (1500–2000), which the old model could not express
#     at all and which must resolve to NO PICTURE rather than to a stretched
#     neighbour;
#   · a clip on track 1 sitting OVER that gap and over a clip below it, so the
#     stack has to come back bottom-first and the topmost has to be the one
#     `frame` names;
#   · a clip with `start_ms` LEFT OUT on each track, which means "after the last
#     one on my track" and is the whole compatibility hinge;
#   · an OVERLAP on one track (t2 starts inside t1), where the later clip wins;
#   · a TRANSITION on each track, one of which does NOT have a cut under it (its
#     neighbour is across a gap) and must therefore be inert.
PICTURE_TRACKS = {
    "frames": [
        # --- track 0 -------------------------------------------------------
        {"id": "b1", "duration_ms": 1500, "start_ms": 0, "track": 0},
        # ⚠ A 500ms HOLE (1500–2000). Track 1 has run out by 1800, so 1800–2000 is
        # a moment with NOTHING ON ANY TRACK — the case the old model could not
        # express and the one both evaluators have to answer the same way.
        {"id": "b2", "duration_ms": 1000, "start_ms": 2000, "track": 0},
        # butts up against b2 → a real cut at 3000, so a transition can live there
        {"id": "b3", "duration_ms": 1000, "start_ms": 3000, "track": 0,
         "keyframes": {"opacity": [{"t": 0, "v": 0.25}, {"t": 1000, "v": 1.0}]}},
        # no start: after the last clip on track 0 → 4000
        {"id": "b4", "duration_ms": 800, "track": 0},
        # --- track 1, drawn OVER track 0 -----------------------------------
        {"id": "t1", "duration_ms": 1200, "start_ms": 0, "track": 1,
         "keyframes": {"scale": [{"t": 0, "v": 1.0}, {"t": 1200, "v": 1.5}]}},
        # ⚠ STARTS INSIDE t1 — an overlap on one track. The later clip wins.
        {"id": "t2", "duration_ms": 400, "start_ms": 800, "track": 1},
        # no start on this track either → after the last of them, at 1200
        {"id": "t3", "duration_ms": 600, "track": 1},
    ],
    "texts": [],
    "shapes": [],
    "overlays": [],
    "transitions": [
        # ON A REAL CUT (b2 ends where b3 starts) — this one plays.
        {"id": "x1", "after_frame_id": "b2", "kind": "dissolve", "duration_ms": 400},
        # ACROSS A GAP (b1 ends at 1500, nothing on track 0 starts there) — inert.
        {"id": "x2", "after_frame_id": "b1", "kind": "wipe", "duration_ms": 400},
        # ON A CUT ON THE TRACK ABOVE (t2 ends at 1200, t3 starts there).
        {"id": "x3", "after_frame_id": "t2", "kind": "dissolve", "duration_ms": 300},
    ],
}
# Sampled around every boundary above: both edges of the hole, the overlap, all
# three transition windows, and past the end of each track.
TRACK_TIMES = [
    0, 799, 800, 801, 1049, 1050, 1199, 1200, 1201, 1349, 1350, 1499,
    1500, 1501, 1699, 1700, 1799, 1800, 1801, 1999, 2000, 2001, 2799, 2800,
    2801, 2999, 3000, 3001, 3199, 3200, 3999, 4000, 4001, 4799, 4800, 5000,
]


# ---------------------------------------------------------------------------
# Run both sides
# ---------------------------------------------------------------------------
HARNESS = """
import { sceneAt, isAnimated, sceneSignature, frameSpans } from %(scene)s;
const { project, times, endMs, tracks, trackTimes } = JSON.parse(process.argv[2]);
const out = times.map((t) => {
  const s = sceneAt(project, t, endMs);
  return { scene: s, signature: sceneSignature(s) };
});
// The MULTI-TRACK fixture, through the same bridge — see PICTURE_TRACKS below.
const stacked = trackTimes.map((t) => {
  const s = sceneAt(tracks, t, null);
  return { scene: s, signature: sceneSignature(s) };
});
process.stdout.write(JSON.stringify({
  frames: out,
  animated: isAnimated(project),
  stacked,
  trackSpans: frameSpans(tracks.frames).spans,
}));
"""


def run_node() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — cannot compare the two evaluators.")
        print("  This test is the only thing keeping preview and export in step;")
        print("  a skip here is a real gap, not a pass.")
        sys.exit(2)

    tmp = tempfile.mkdtemp(prefix="parity_")
    try:
        # .mjs so node reads it as ESM wherever it is written, independent of
        # the nearest package.json.
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {"scene": json.dumps(_file_url(SCENE_JS))})
        payload = json.dumps(
            {
                "project": PROJECT,
                "times": TIMES,
                "endMs": END_MS,
                "tracks": PICTURE_TRACKS,
                "trackTimes": TRACK_TIMES,
            }
        )
        proc = subprocess.run(
            ["node", path, payload],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            print("  scene.js could not be evaluated (see above).")
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _file_url(path: str) -> str:
    from pathlib import Path

    return Path(path).resolve().as_uri()


def normalise(value):
    """Make the two sides comparable without hiding a real difference.

    JS has one number type, so 1.0 arrives as 1 and `Math.round` gives an int
    where Python gives a float. Ints and floats of equal value are folded
    together here — that is a language artefact, not a disagreement. Nothing
    else is touched: a differing VALUE still differs.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if isinstance(value, dict):
        return {k: normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalise(v) for v in value]
    return value


def first_difference(want, got, path="scene"):
    """Where exactly the two sides part company.

    "the scenes differ" is useless when a scene is four lists of clips; the
    point of failing is to be told which property of which clip, so print the
    first leaf that disagrees and stop.
    """
    if isinstance(want, dict) and isinstance(got, dict):
        for key in sorted(set(want) | set(got)):
            if key not in want:
                return f"{path}.{key}: missing on the JS side (py has {got[key]!r})"
            if key not in got:
                return f"{path}.{key}: missing on the Python side (js has {want[key]!r})"
            if want[key] != got[key]:
                return first_difference(want[key], got[key], f"{path}.{key}")
        return ""
    if isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            return f"{path}: js has {len(want)} item(s), py has {len(got)}"
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:
                return first_difference(a, b, f"{path}[{i}]")
        return ""
    return f"{path}: js={want!r} py={got!r}"


print("Scene model parity — client/src/animatic/scene.js vs animatic_render.py\n")

js = run_node()

# --- Does anything move? --------------------------------------------------
py_animated = is_animated(PROJECT)
check("isAnimated agrees", js["animated"] == py_animated,
      f"(js={js['animated']} py={py_animated})")
check("the fixture IS animated (or the rest of this test proves nothing)", py_animated)

# Stripping the keyframes is no longer enough to make this fixture hold still:
# a VIDEO CLIP moves by itself, and so does a speed other than 1. Both have to
# go too, which is the point — see the dedicated checks further down.
still = {"frames": [dict(f, keyframes={}, kind="image", speed=1.0) for f in PROJECT["frames"]],
         "texts": [dict(t, keyframes={}) for t in PROJECT["texts"]],
         "shapes": [dict(s, keyframes={}) for s in PROJECT["shapes"]],
         "overlays": [dict(o, keyframes={}) for o in PROJECT["overlays"]]}
check("a project with the keyframes and the clips stripped is NOT animated",
      not is_animated(still))

# --- The scene at every sampled time --------------------------------------
mismatches = 0
first_detail = ""
for i, t in enumerate(TIMES):
    want = normalise(js["frames"][i]["scene"])
    got = normalise(scene_at(PROJECT, t, END_MS))
    if want != got:
        mismatches += 1
        if not first_detail:
            first_detail = f"at t={t}ms — {first_difference(want, got)}"
    want_sig = js["frames"][i]["signature"]
    got_sig = scene_signature(scene_at(PROJECT, t, END_MS))
    if want_sig != got_sig:
        mismatches += 1
        if not first_detail:
            first_detail = f"signature at t={t}ms:\n    js: {want_sig}\n    py: {got_sig}"

check(f"the resolved scene matches at all {len(TIMES)} sampled times",
      mismatches == 0, f"\n    {first_detail}")

# --- Rules worth asserting outright, not just "both sides do the same" ----
# Two implementations can agree and both be wrong. These pin the behaviour.
s0 = scene_at(PROJECT, 0, END_MS)
check("blank captions never reach the scene",
      all(c["id"] != "tx2" for c in s0["texts"]))
check("a statically invisible shape never reaches the scene",
      all(s["id"] != "sh3" for s in scene_at(PROJECT, 1200, END_MS)["shapes"]))
check("a shape keyframed UP from opacity 0 does reach the scene",
      any(s["id"] == "sh2" for s in scene_at(PROJECT, 500, END_MS)["shapes"]))
check("at t=0 that shape is still invisible, so it is absent",
      all(s["id"] != "sh2" for s in s0["shapes"]))

# The half-open rule: 1500ms is the FIRST moment of frame 2, not the last of 1.
check("a cut boundary belongs to the incoming picture",
      scene_at(PROJECT, 1499, END_MS)["frame"]["index"] == 0
      and scene_at(PROJECT, 1500, END_MS)["frame"]["index"] == 1)

# The last picture is held out to end_ms so a long music bed is covered.
check("the last picture is held past the end of the frames",
      scene_at(PROJECT, 8000, END_MS)["frame"]["index"] == 5)
check("total_ms is the held length, not the sum of the frames",
      scene_at(PROJECT, 0, END_MS)["total_ms"] == END_MS)

# Holding rather than extrapolating outside the first and last key.
ov_early = scene_at(PROJECT, 1000, END_MS)["overlays"][0]
check("a key before the clip starts holds its value, it does not extrapolate",
      ov_early["w"] > 0.1, f"(w={ov_early['w']})")

# A lone key is not an animation.
fr3 = scene_at(PROJECT, 4500, END_MS)["frame"]
check("a single keyframe holds its value everywhere", fr3["opacity"] == 0.5)

# --- Transitions ----------------------------------------------------------
# The picture-level rules, pinned outright rather than left to "both sides do
# the same thing" — two implementations can agree and both be wrong.
outside = scene_at(PROJECT, 3699, END_MS)
check("outside a transition there is no second picture",
      outside["frame_b"] is None and outside["mix"] == 0 and outside["transition"] is None)
check("a cut with no transition on it is still a straight cut",
      scene_at(PROJECT, 1500, END_MS)["frame_b"] is None)

first = scene_at(PROJECT, 3700, END_MS)
mid = scene_at(PROJECT, 4000, END_MS)
last = scene_at(PROJECT, 4299, END_MS)
check("the transition starts BEFORE the cut, on the tail of the outgoing shot",
      first["frame_b"] is not None and first["frame"]["index"] == 1)
check("it names the picture arriving as the second one",
      first["frame_b"]["index"] == 2)
check("mix runs 0 → 1 across the window",
      first["mix"] == 0.0 and 0.49 < mid["mix"] < 0.51 and last["mix"] > 0.99,
      f"({first['mix']} / {mid['mix']} / {last['mix']})")
# THE POINT OF BOUNDARY-LOCAL: past the cut the OUTGOING picture is still the
# one named, because it is still on screen. Naming the incoming one there would
# make `mix` double back and leave a wipe with no direction.
check("past the cut the outgoing picture is still the frame, still blending",
      mid["frame"]["index"] == 1 and mid["frame_b"]["index"] == 2)
check("the moment the window ends, the incoming picture is simply up",
      scene_at(PROJECT, 4300, END_MS)["frame"]["index"] == 2
      and scene_at(PROJECT, 4300, END_MS)["frame_b"] is None)
# A transition hanging off the LAST frame, or naming a frame that has been
# deleted, is inert — skipped, not an error, and not a second window.
check("a transition on the last frame is inert",
      all(scene_at(PROJECT, t, END_MS)["frame_b"] is None for t in (7400, 7599, 8200)))
check("a transition naming a frame that isn't there is inert",
      scene_at(PROJECT, 2000, END_MS)["frame_b"] is None)
# Transitions cost nothing in length: that is the entire design decision.
check("a transition does not change how long the timeline is",
      scene_at(PROJECT, 0, END_MS)["total_ms"]
      == scene_at({**PROJECT, "transitions": []}, 0, END_MS)["total_ms"])
check("any transition at all forces the per-frame planner",
      is_animated({"frames": [{"id": "a", "duration_ms": 1000}, {"id": "b", "duration_ms": 1000}],
                   "transitions": [{"id": "t", "after_frame_id": "a"}]}))
# The signature is the export's render cache key. Two moments in one dissolve
# resolve to the same clips and differ ONLY in the mix, so leaving it out of the
# key would render one still and reuse it for the whole blend — the transition
# would snap instead of blending.
check("the render key changes as the blend progresses",
      len({scene_signature(scene_at(PROJECT, t, END_MS)) for t in (3750, 3900, 4100, 4250)}) == 4)
check("a moment off a transition signs exactly as it did before they existed",
      scene_signature(scene_at(PROJECT, 1000, END_MS))
      == scene_signature(scene_at({**PROJECT, "transitions": []}, 1000, END_MS)))

# --- Transition parameters -------------------------------------------------
# A transition's `params` is resolved the way an effect's is: every parameter
# the KIND offers, filled in from `TRANSITION_PARAMS`, and nothing else. The
# fixture's tr4 is a wipe travelling "up" that also carries a dip's `color`, and
# tr1 is a dissolve carrying a wipe's `direction` — so both halves of that rule
# are compared across the two languages, not just asserted here.
tr4 = scene_at(PROJECT, 7000, END_MS)
check("a transition's parameters reach the scene, resolved",
      tr4["transition_params"] == {"direction": "up", "softness": 0.0},
      f"({tr4['transition_params']})")
check("a parameter the kind does not offer is dropped",
      "color" not in tr4["transition_params"])
check("a kind that offers none resolves an EMPTY dict, not a missing key",
      scene_at(PROJECT, 4000, END_MS)["transition_params"] == {})
check("and off a transition it is empty too, rather than absent",
      scene_at(PROJECT, 3699, END_MS)["transition_params"] == {})


def _wipe(params=None):
    """The fixture with ONE wipe on the 4000ms cut, so 4000 is mid-window."""
    t = {"id": "t", "after_frame_id": "fr2", "kind": "wipe", "duration_ms": 600}
    if params is not None:
        t["params"] = params
    return {**PROJECT, "transitions": [t]}


# Forgiveness, the same rule an unrecognised `kind` and `ease` get: fold down to
# the default rather than refuse, so a project written by a newer client still
# opens and still plays here.
check("a direction this build has never heard of folds down to the default",
      scene_at(_wipe({"direction": "sideways"}), 4000, END_MS)["transition_params"]
      == {"direction": "right", "softness": 0.0})
# The NUMERIC half of the same forgiveness rule. A number out of range is
# clamped in the RESOLVER, on both sides, rather than in each renderer — so a
# monitor and an exporter cannot clamp it differently, and `scene_signature`
# signs the value that will actually be drawn.
check("a softness out of range is clamped, not refused",
      scene_at(_wipe({"softness": 4}), 4000, END_MS)["transition_params"]["softness"] == 1.0)
check("and one that isn't a number at all falls back to the default",
      scene_at(_wipe({"softness": "very"}), 4000, END_MS)["transition_params"]["softness"] == 0.0)
# ⚠ THE RENDER KEY HAS TO TELL TWO SOFTNESSES APART, for the same reason it has
# to tell two directions apart: they resolve to the same two pictures at the
# same mix and differ only here.
check("the render key tells two softnesses apart",
      scene_signature(scene_at(_wipe({"softness": 0.3}), 4000, END_MS))
      != scene_signature(scene_at(_wipe(), 4000, END_MS)))
check("a wipe at its defaults signs as it did before softness existed",
      scene_signature(scene_at(_wipe({"softness": 0}), 4000, END_MS))
      == scene_signature(scene_at(_wipe(), 4000, END_MS)))

# --- The matte model -------------------------------------------------------
# A REVEAL IS A MASK ON THE ARRIVING PICTURE, so the thing that has to be
# twinned is which matte each kind draws through and in what ORDER the kinds are
# listed — the shader turns a matte name into an integer by its index, so a list
# that drifted by one would silently draw a clock where the project says iris.
# The JS side of both tables is compared by `run_node` below; these are the
# invariants that hold on either side alone.
check("every transition kind resolves a matte, even if it is none",
      all(isinstance(transition_matte(k), str) for k in TRANSITION_KINDS))
check("every matte a kind names is one the renderer has",
      all(transition_matte(k) in MATTE_KINDS for k in TRANSITION_KINDS))
check("none is index 0 — the shader tests kind == 0 as its early out",
      MATTE_KINDS[0] == "none")
check("the three that are not reveals draw through no matte",
      [transition_matte(k) for k in ("dissolve", "dip", "slide")] == ["none"] * 3)
check("and every other kind does draw through one",
      all(transition_matte(k) != "none"
          for k in TRANSITION_KINDS if k not in ("dissolve", "dip", "slide")))
# ⚠ THE RENDER KEY HAS TO TELL TWO DIRECTIONS APART. Two wipes at the same `mix`
# resolve to the same two pictures at the same transforms and differ ONLY in the
# parameter, so leaving it out of the key would let a re-export reuse the stills
# from the last one and come back with the direction unchanged.
check("the render key tells two directions apart",
      scene_signature(scene_at(_wipe({"direction": "up"}), 4000, END_MS))
      != scene_signature(scene_at(_wipe(), 4000, END_MS)))
# The other half: only NON-DEFAULT parameters are written into the key, so a
# transition nobody has touched signs what it signed before parameters existed.
check("a transition at its default direction signs as it did before params",
      scene_signature(scene_at(_wipe({"direction": "right"}), 4000, END_MS))
      == scene_signature(scene_at(_wipe(), 4000, END_MS)))

# --- Clips: image / video / colour ----------------------------------------
# The scene model's job for a video clip is to answer ONE new question — which
# moment of the source file is on screen — and these pin the answer rather than
# leaving it to "both sides do the same thing".
img = scene_at(PROJECT, 1000, END_MS)["frame"]
check("a plain still reports kind 'image' and no source time",
      img["kind"] == "image" and img["source_ms"] is None)

# fr4 starts at 4900, in_ms 500, speed 2. At the very first instant the source
# time is exactly `in_ms` — an off-by-one here shows as the clip starting a
# frame late, which is the classic trim bug.
check("a video clip opens on exactly its in point",
      scene_at(PROJECT, 4900, END_MS)["frame"]["source_ms"] == 500)
check("a video clip reports kind 'video'",
      scene_at(PROJECT, 4900, END_MS)["frame"]["kind"] == "video")
# 100ms of timeline at speed 2 is 200ms of source: 500 + 200 = 700.
check("speed 2 covers twice as much source as timeline",
      scene_at(PROJECT, 5000, END_MS)["frame"]["source_ms"] == 700,
      f"(got {scene_at(PROJECT, 5000, END_MS)['frame']['source_ms']})")
# 1250ms in, the source reaches out_ms (500 + 1250*2 = 3000) and stops there.
held_a = scene_at(PROJECT, 6150, END_MS)["frame"]["source_ms"]
held_b = scene_at(PROJECT, 6499, END_MS)["frame"]["source_ms"]
check("past the out point the clip HOLDS its last source frame",
      held_a == 2999 and held_b == 2999, f"({held_a} then {held_b})")

# THE DECISION OF THE PHASE, asserted directly: speed changes what the clip
# SHOWS, never where anything sits. Doubling every speed must leave the spans,
# and therefore every later cut, exactly where they were.
faster = {**PROJECT, "frames": [dict(f, speed=float(f.get("speed", 1)) * 2)
                                for f in PROJECT["frames"]]}
check("changing speed does not move a single cut",
      [(scene_at(faster, t, END_MS)["frame"] or {}).get("index")
       for t in TIMES]
      == [(scene_at(PROJECT, t, END_MS)["frame"] or {}).get("index") for t in TIMES])
check("changing speed does not change the timeline's length",
      scene_at(faster, 0, END_MS)["total_ms"] == scene_at(PROJECT, 0, END_MS)["total_ms"])

# A colour card is a clip with no file behind it at all.
card = scene_at(PROJECT, 6700, END_MS)["frame"]
check("a colour card reports its colour and no source time",
      card["kind"] == "color" and card["color"] == "#204060" and card["source_ms"] is None)

# Forgiveness, matching `ease` and the transition kinds: a kind this build has
# never heard of is an image, not an error.
check("an unrecognised clip kind folds down to 'image'",
      scene_at(PROJECT, 7500, END_MS)["frame"]["kind"] == "image")

# is_animated decides the EXPORT PLANNER, and getting it wrong in the "false"
# direction would render a video clip as one frozen still held for its whole
# length while the preview played it. Both triggers are asserted alone.
check("a video clip alone forces the per-frame planner",
      is_animated({"frames": [{"id": "v", "duration_ms": 1000, "kind": "video"}]}))
check("a speed other than 1 alone forces the per-frame planner",
      is_animated({"frames": [{"id": "v", "duration_ms": 1000, "speed": 2.0}]}))
check("a project of plain stills is still NOT animated",
      not is_animated({"frames": [{"id": "a", "duration_ms": 1000, "kind": "image"},
                                  {"id": "b", "duration_ms": 1000}]}))

# The render cache key. Two moments of one video clip resolve to the same clip
# and the same transform, differing ONLY in the source frame — so leaving
# `source_ms` out of the signature would render one still and reuse it for the
# whole clip, and the video would play as a freeze frame. Exactly the bug `mix`
# already guards against for transitions.
check("the render key changes as a video clip plays",
      len({scene_signature(scene_at(PROJECT, t, END_MS))
           for t in (4900, 5000, 5100, 5200)}) == 4)
# …and stops changing once the clip is holding its last frame, which is what
# makes a held tail cost ONE rendered still instead of hundreds. Checked on a
# clip of its own: fr4 also has a `scale` keyframe running the whole way, so its
# signature keeps changing after the source stops — correctly, and for a
# different reason than the one under test here.
tail = {"frames": [{"id": "v", "duration_ms": 2000, "kind": "video",
                    "in_ms": 0, "out_ms": 500, "speed": 1.0}]}
check("a held tail collapses to a single render key",
      len({scene_signature(scene_at(tail, t)) for t in (900, 1200, 1800)}) == 1)
check("a project of stills signs exactly as it did before clips existed",
      scene_signature(scene_at({"frames": [{"id": "a", "duration_ms": 2000}]}, 500))
      == "f0:1.000000:0.500000:0.500000:1.000000")

# --- The look: effects, mask, blend ---------------------------------------
# The scene model's job for a grade is the same as for a transform: say what
# every parameter IS at time t. These pin the answers rather than leaving them
# to "both sides do the same thing", because two implementations can agree and
# both be wrong.
look = scene_at(PROJECT, 750, END_MS)["frame"]
check("a clip's effects, mask and blend all reach the scene",
      "effects" in look and "mask" in look and look["blend"] == "screen")
check("an effect kind this build doesn't know is dropped from the scene",
      all(e["kind"] != "kaleidoscope" for e in look["effects"]),
      f"({[e['kind'] for e in look['effects']]})")
check("the effects that ARE known survive, in the order they were written",
      [e["kind"] for e in look["effects"]] == ["lut", "chroma", "brightness"])
# fx:e1:amount runs 0 → 1 over 1500ms on an ease-in curve, so at the halfway
# point it is BELOW 0.5 — which is the cheapest proof the curve is being read
# and not just the endpoints.
mid_lut = look["effects"][0]["params"]["amount"]
check("a keyframed effect parameter is interpolated on its own curve",
      0 < mid_lut < 0.5, f"(amount={mid_lut})")
check("a string parameter is never interpolated — it is read straight off",
      look["effects"][0]["params"]["name"] == "noir"
      and look["effects"][1]["params"]["color"] == "#12ff34")
check("a parameter left out of a saved project arrives at its default",
      look["effects"][1]["params"]["smoothness"] == 0.08
      and look["effects"][1]["params"]["spill"] == 0.0)
# The effect with no id is at raw index 3 — counted BEFORE the unknown kind is
# dropped. Off by one here and the track would animate the wrong effect.
check("an effect with no id is keyed by its position in the WRITTEN chain",
      look["effects"][2]["id"] == "3" and look["effects"][2]["params"]["amount"] < 1.1,
      f"(id={look['effects'][2]['id']}, amount={look['effects'][2]['params']['amount']})")
check("a keyframed mask field is interpolated, the rest are read off",
      0.35 < look["mask"]["w"] < 0.9 and look["mask"]["h"] == 0.9
      and look["mask"]["kind"] == "ellipse" and look["mask"]["invert"] is False)

# The shape of the resolved look on a clip that carries none. This is the check
# that would have caught `undefined` on the JS side being dropped by JSON while
# Python reported a real dict — the two would then differ without either being
# wrong, and the whole comparison would be meaningless.
bare = scene_at(PROJECT, 2500, END_MS)["frame"]
check("a clip with no look still resolves one, in the same shape",
      bare["effects"] == [] and bare["blend"] == "normal"
      and bare["mask"]["kind"] == "none")
plain_ov = [o for o in scene_at(PROJECT, 500, END_MS)["overlays"] if o["id"] == "ov2"]
check("so does an overlay with no look", len(plain_ov) == 1
      and plain_ov[0]["effects"] == [] and plain_ov[0]["blend"] == "normal")

# A grade that RAMPS is continuous in exactly the way a Ken Burns push is, so it
# has to force the sampling planner. Getting this wrong in the "false" direction
# would export the grade frozen at its first value while the monitor animated it
# — the same class of failure a video clip planned as a still is.
check("a keyframed effect parameter alone forces the per-frame planner",
      is_animated({"frames": [{"id": "a", "duration_ms": 1000,
                               "effects": [{"id": "x", "kind": "brightness"}],
                               "keyframes": {"fx:x:amount": [{"t": 0, "v": 1},
                                                             {"t": 500, "v": 2}]}}]}))
check("a keyframed mask alone forces the per-frame planner",
      is_animated({"frames": [{"id": "a", "duration_ms": 1000,
                               "mask": {"kind": "rect"},
                               "keyframes": {"mask:x": [{"t": 0, "v": 0.2},
                                                        {"t": 500, "v": 0.8}]}}]}))
check("a STATIC grade does NOT force it — one still can carry a fixed look",
      not is_animated({"frames": [{"id": "a", "duration_ms": 1000, "blend": "multiply",
                                   "mask": {"kind": "rect"},
                                   "effects": [{"id": "x", "kind": "lut",
                                                "params": {"name": "noir"}}]}]}))

# The render cache key again, for the third thing that can move a picture
# without moving a clip. Two samples of the mask closing on fr1 resolve to the
# same clip at the same transform and differ ONLY in the look.
check("the render key changes as a grade moves",
      len({scene_signature(scene_at(PROJECT, t, END_MS)) for t in (100, 500, 900, 1300)}) == 4)
# (that a clip with NO look signs byte-for-byte what it always did is asserted
# further down, by the same check that has guarded it since clips arrived)

# --- Text: placement and the properties that move it ----------------------
# A caption gained x/y in Phase 5, and they only mean anything in FREE
# placement. Both halves of that are pinned here rather than left to "both sides
# do the same thing", because two implementations can agree and both be wrong.
def _text(t, text_id):
    return next(c for c in scene_at(PROJECT, t, END_MS)["texts"] if c["id"] == text_id)


check("every caption resolves a placement, even one that never chose",
      _text(2500, "tx3")["place"] == "flow")
check("an unrecognised placement folds down to flow",
      _text(5200, "tx5")["place"] == "flow", f"(got {_text(5200, 'tx5')['place']!r})")
# tx4 runs 1000–3000 with y easing 0.68 → 0.6 over its first 500ms.
check("a free caption's y is interpolated on its own curve",
      0.6 < _text(1200, "tx4")["y"] < 0.68, f"(y={_text(1200, 'tx4')['y']})")
check("and holds once past its last key",
      _text(2000, "tx4")["y"] == 0.6 and _text(2900, "tx4")["y"] == 0.6)
check("a flow caption still resolves x/y — it just doesn't use them",
      "x" in _text(2500, "tx3") and "y" in _text(2500, "tx3"))
check("an untouched caption's y is the subtitle default, not the centre",
      _text(2500, "tx3")["y"] == 0.85)

# The render cache key, for the FOURTH thing that can move a picture without
# moving a clip. Two samples of tx4 sliding up resolve to the same clip and
# differ only in `y`, so leaving it out would freeze the title in the MP4.
check("the render key changes as a free caption moves",
      len({scene_signature(scene_at(PROJECT, t, END_MS))
           for t in (1050, 1150, 1250, 1350)}) == 4)
# …and a project of ordinary stacked subtitles must sign byte-for-byte what it
# signed before free placement existed, or every old export's cache is invalid.
check("a flow caption signs exactly as it did before free placement existed",
      scene_signature(scene_at({"frames": [{"id": "a", "duration_ms": 2000}], "texts": [
          {"id": "c", "text": "hi", "start_ms": 0, "duration_ms": 2000}]}, 500))
      == "f0:1.000000:0.500000:0.500000:1.000000|tc:1.000000")

# `is_animated` picks the export planner, so a caption that only moves through
# x/y has to force it — miss this and the title sits dead still in the MP4 while
# the monitor slides it, which is the same class of failure a video clip planned
# as a still is.
check("a keyframed caption position alone forces the per-frame planner",
      is_animated({"frames": [{"id": "a", "duration_ms": 1000}],
                   "texts": [{"id": "c", "text": "hi", "start_ms": 0, "duration_ms": 900,
                              "place": "free",
                              "keyframes": {"y": [{"t": 0, "v": 0.9}, {"t": 500, "v": 0.5}]}}]}))
check("a still caption does NOT force it",
      not is_animated({"frames": [{"id": "a", "duration_ms": 1000}],
                       "texts": [{"id": "c", "text": "hi", "start_ms": 0,
                                  "duration_ms": 900, "place": "free", "y": 0.5}]}))

# An un-keyframed project must resolve to exactly its stored values — this is
# the "nothing changed for existing animatics" guarantee.
plain = scene_at({"frames": [{"id": "a", "duration_ms": 2000}], "shapes": [
    {"id": "s", "start_ms": 0, "duration_ms": 2000, "x": 0.25, "y": 0.75,
     "w": 0.4, "h": 0.1, "opacity": 0.6, "rotation": 45}]}, 1000)
check("an un-keyframed clip resolves to its stored values untouched",
      (plain["shapes"][0]["x"], plain["shapes"][0]["rotation"],
       plain["shapes"][0]["opacity"]) == (0.25, 45, 0.6))
check("an un-keyframed frame resolves to an identity transform",
      (plain["frame"]["scale"], plain["frame"]["x"], plain["frame"]["y"]) == (1.0, 0.5, 0.5))

# ---------------------------------------------------------------------------
print("\nThe PICTURE TRACKS — a stack, its gaps, and a transition per track")
# ⚠ THE WHOLE SCENE IS COMPARED, `pictures` included, so this covers the stacking
# order, every resolved value on every track, and the render key.
stack_bad = 0
stack_detail = ""
for i, t in enumerate(TRACK_TIMES):
    want = normalise(js["stacked"][i]["scene"])
    got = normalise(scene_at(PICTURE_TRACKS, t, None))
    if want != got:
        stack_bad += 1
        if not stack_detail:
            stack_detail = f"at t={t}ms — {first_difference(want, got)}"
    want_sig = js["stacked"][i]["signature"]
    got_sig = scene_signature(scene_at(PICTURE_TRACKS, t, None))
    if want_sig != got_sig:
        stack_bad += 1
        if not stack_detail:
            stack_detail = f"signature at t={t}ms:\n    js: {want_sig}\n    py: {got_sig}"
check(f"both evaluators agree at all {len(TRACK_TIMES)} sampled moments",
      stack_bad == 0, stack_detail)

check("and they place every clip identically",
      normalise(js["trackSpans"]) == normalise(frame_spans(PICTURE_TRACKS["frames"])[0]),
      str(frame_spans(PICTURE_TRACKS["frames"])[0]))

# The rules the fixture exists to pin, stated as assertions of their own so a
# failure names the RULE rather than "the scenes differ at 1500ms".
at = lambda t: scene_at(PICTURE_TRACKS, t, None)  # noqa: E731

spans_of = frame_spans(PICTURE_TRACKS["frames"])[0]
check("a clip with no start_ms lands after the last one on ITS track",
      [s for s in spans_of if s["index"] == 3][0]["start"] == 4000)
check("…and the one on the track above lands after ITS track's last clip, not that one",
      [s for s in spans_of if s["index"] == 6][0]["start"] == 1200)

check("a moment with nothing on ANY track resolves to NO picture",
      at(1900)["pictures"] == [] and at(1900)["frame"] is None)
check("…and a gap on the base track alone still shows the track above it",
      [p["frame"]["index"] for p in at(1700)["pictures"]] == [6])

check("the stack comes back BOTTOM TRACK FIRST",
      [p["track"] for p in at(1000)["pictures"]] == [0, 1])
check("…and `frame` is the topmost of them",
      at(1000)["frame"]["index"] == 5)

check("where two clips overlap on one track, the LATER one wins",
      [p["frame"]["index"] for p in at(1000)["pictures"] if p["track"] == 1] == [5])

check("a transition on a real cut plays",
      at(2900)["pictures"][0]["transition"] == "dissolve")
check("…and it is the OUTGOING clip that `frame` names, for the whole window",
      at(3100)["pictures"][0]["frame"]["index"] == 1)
check("a transition ACROSS A GAP is inert — there is no edit point in a hole",
      all(p["transition"] != "wipe"
          for t in (1300, 1400, 1500, 1600, 1700) for p in at(t)["pictures"]))
check("a transition on the track ABOVE plays on that track and no other",
      [(p["track"], p["transition"]) for p in at(1150)["pictures"]]
      == [(0, None), (1, "dissolve")])

check("the render key names every track, so two stacks are two stills",
      scene_signature(at(1000)) != scene_signature(at(1400)))
check("…and a moment with no picture at all signs as none",
      scene_signature(at(1900)).startswith("f-"))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    print("\nThe preview and the export now disagree. Fix before shipping anything.")
    sys.exit(1)
print("Preview and export agree on every sampled moment.")
