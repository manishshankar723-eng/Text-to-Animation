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

from animatic_render import is_animated, scene_at, scene_signature

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
        {
            "id": "fr1",
            "duration_ms": 1500,
            "label": "Shot 1",
            "keyframes": {
                "scale": [
                    {"t": 0, "v": 1.0, "ease": "ease-in-out"},
                    {"t": 1500, "v": 1.25},
                ],
                "x": [{"t": 0, "v": 0.5}, {"t": 1500, "v": 0.42}],
            },
        },
        # No keyframes at all — the common case, and it must stay identical to
        # what the project did before any of this existed.
        {"id": "fr2", "duration_ms": 2500, "label": "Shot 2"},
        # A lone key. One key is not an animation; the value simply holds.
        {"id": "fr3", "duration_ms": 900, "keyframes": {"opacity": [{"t": 200, "v": 0.5}]}},
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
            "keyframes": {"w": [{"t": -500, "v": 0.1}, {"t": 4000, "v": 0.9}]},
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
        {"id": "tr1", "after_frame_id": "fr2", "kind": "dissolve", "duration_ms": 600},
        # CLAMPED: 5s asked for, but fr3 is only 900ms long, so both evaluators
        # must cut it to 900 — and to the SAME 900, or the windows differ.
        # Anchored to the LAST frame, so it is also the inert case: there is
        # nothing to cut to, and it must be skipped rather than crash.
        {"id": "tr2", "after_frame_id": "fr3", "kind": "wipe", "duration_ms": 5000},
        # Names a frame that isn't in the project at all — the "you deleted the
        # frame this was on" case, which must be silently inert on both sides.
        {"id": "tr3", "after_frame_id": "gone", "kind": "slide", "duration_ms": 400},
    ],
}

# Every cut boundary, either side of every boundary, the ends, and a spread of
# ordinary moments in between. A half-open visibility rule (start <= t < end) is
# what makes a cut land on exactly one picture; times ON a boundary are where an
# implementation that got that wrong shows itself.
TIMES = [
    0, 1, 250, 499, 500, 501, 600, 999, 1000, 1001, 1499, 1500, 1501,
    1999, 2000, 2001, 2500, 3000, 3499, 3500,
    # The transition on the 4000ms cut runs 3700–4300. Both edges, either side
    # of both edges, the midpoint, and the cut itself — a transition is where
    # two implementations disagree about rounding if they are going to.
    3699, 3700, 3701, 3850, 3999, 4000, 4001, 4150, 4299, 4300, 4301,
    4500, 4899, 4900, 5200, 8000,
]
END_MS = 6000  # longer than the pictures: the last frame must be HELD out to here


# ---------------------------------------------------------------------------
# Run both sides
# ---------------------------------------------------------------------------
HARNESS = """
import { sceneAt, isAnimated, sceneSignature } from %(scene)s;
const { project, times, endMs } = JSON.parse(process.argv[2]);
const out = times.map((t) => {
  const s = sceneAt(project, t, endMs);
  return { scene: s, signature: sceneSignature(s) };
});
process.stdout.write(JSON.stringify({ frames: out, animated: isAnimated(project) }));
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
        payload = json.dumps({"project": PROJECT, "times": TIMES, "endMs": END_MS})
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

still = {"frames": [dict(f, keyframes={}) for f in PROJECT["frames"]],
         "texts": [dict(t, keyframes={}) for t in PROJECT["texts"]],
         "shapes": [dict(s, keyframes={}) for s in PROJECT["shapes"]],
         "overlays": [dict(o, keyframes={}) for o in PROJECT["overlays"]]}
check("a project with the keyframes stripped is NOT animated", not is_animated(still))

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
      scene_at(PROJECT, 5500, END_MS)["frame"]["index"] == 2)
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
      all(scene_at(PROJECT, t, END_MS)["frame_b"] is None for t in (4700, 4899, 5200)))
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

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    print("\nThe preview and the export now disagree. Fix before shipping anything.")
    sys.exit(1)
print("Preview and export agree on every sampled moment.")
