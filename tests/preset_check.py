"""Every animation preset, proved to be a thing this app can actually render.

`client/src/animatic/text_presets.js` and `client/src/animatic/motion_presets.js`
are keyframe MACROS: each one writes keys onto properties the scene model
already animates, and then gets out of the way. That design is what makes a
preset free — no renderer changes, no exporter changes, no new evaluator — but
it moves the whole risk to one place: **a preset can write keys that are
perfectly good JavaScript and completely unrenderable.**

There are exactly five ways for that to happen, and this file is one check per
way:

  1. **A value the SERVER refuses.** Every key value and every resting value is
     fed through the real Pydantic model. A `scale` of 12 on a caption is fine;
     on a picture it is a 422 on the next autosave, which is not "the animation
     looked wrong", it is a project that will not save.

  2. **An easing curve neither renderer has.** `EASINGS` is a short closed list
     twinned in `animatic_render.py`. A typo'd ease folds to `linear` in both —
     but silently, in two places, and the preset would simply not be the curve
     it says it is.

  3. **A preset that MOVES without asking for free placement.** In flow
     placement a caption's x/y are resolved and then ignored, so a slide would
     animate nothing and the monitor would be lying about the export. Every
     preset that writes x or y must set `place: "free"`; every preset that does
     NOT must leave placement alone, or picking "Pop" would quietly tear a
     subtitle out of its zone.

  4. **A move the EXPORTER renders as one still.** `scene_signature` is the
     render cache key: two moments that sign the same get one rendered frame
     between them. A caption whose only animation is `scale` signed identically
     at every moment of its life — so it moved in the monitor and sat dead still
     in the MP4. Checked here for every preset, since a third of them animate
     nothing else.

  5. **A caption that cannot be TURNED the way the monitor turns it.** Rotation
     is the one property in all of this that needed real code on both sides, so
     the drawing itself is checked: the layer path and the direct path must be
     the same picture at 0°, and a turned caption must stay anchored where CSS
     anchors it.

Nothing here spends AI quota and nothing needs a key. Needs `node` on PATH for
everything but check 5 — the same one `npm run build` uses.

    python tests/preset_check.py
"""

import json

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

import animatic
from animatic_render import ANIMATABLE, EASINGS, scene_at, scene_signature
from server.schemas import AnimaticFrame, AnimaticTextClip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANIM = os.path.join(ROOT, "client", "src", "animatic")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixtures
# ---------------------------------------------------------------------------
# ⚠ THREE CAPTIONS, NOT ONE, AND THE AWKWARD TWO ARE THE POINT. A preset applied
# to a default caption proves almost nothing: every preset works outwards from
# the clip's resting values, so the cases that break bounds are the caption
# somebody already styled to 300% and the one already hung at an angle. The
# short clip is the third: its beats have to be squeezed to fit rather than
# landing past its own end.
CAPTIONS = {
    "plain": {
        "id": "tx-plain", "text": "A caption", "start_ms": 0, "duration_ms": 3000,
        "place": "flow", "position": "bottom",
    },
    "styled": {
        # 300% and 8° already, and free-placed off to one side.
        "id": "tx-styled", "text": "A big angled title", "start_ms": 0,
        "duration_ms": 4000, "place": "free", "x": 0.22, "y": 0.3,
        "scale": 3.0, "rotation": 8.0,
    },
    "short": {
        "id": "tx-short", "text": "Tiny", "start_ms": 0, "duration_ms": 300,
        "place": "flow", "position": "top",
    },
}

# ⚠ `src` IS AN OBJECT, NOT A PATH. `AnimaticFrame.src` is an
# `AnimaticFrameSource` — where the picture CAME FROM — and a bare filename is a
# validation error rather than a frame. Written out here because the whole point
# of check 1 is that these fixtures go through the real model.
def _src(upload_id):
    return {"kind": "upload", "upload_id": upload_id}


FRAMES = {
    "plain": {"id": "fr-plain", "src": _src("a"), "duration_ms": 3000},
    # ⚠ ALREADY AT 8× — the case that finds a move whose multiplier would take a
    # picture past `AnimaticFrame.scale`'s ceiling of 10 and fail the save.
    "zoomed": {"id": "fr-zoomed", "src": _src("b"), "duration_ms": 5000,
               "scale": 8.0, "x": 0.4, "y": 0.6},
    "short": {"id": "fr-short", "src": _src("c"), "duration_ms": 250},
}

HARNESS = """
import { TEXT_PRESETS, applyTextPreset } from "%(text)s";
import { MOTION_PRESETS, applyMotionPreset } from "%(motion)s";

const [captions, frames] = JSON.parse(process.argv[2]);
const out = { text: {}, motion: {}, textMeta: [], motionMeta: [] };

for (const p of TEXT_PRESETS) {
  out.textMeta.push({ id: p.id, label: p.label, category: p.category || "",
                      hint: p.hint || "", moves: Boolean(p.moves) });
  out.text[p.id] = {};
  for (const [name, clip] of Object.entries(captions)) {
    out.text[p.id][name] = applyTextPreset(clip, p.id);
  }
}
for (const p of MOTION_PRESETS) {
  out.motionMeta.push({ id: p.id, label: p.label, category: p.category || "",
                        hint: p.hint || "" });
  out.motion[p.id] = {};
  for (const [name, clip] of Object.entries(frames)) {
    out.motion[p.id][name] = applyMotionPreset(clip, p.id);
  }
}
process.stdout.write(JSON.stringify(out));
"""


def run_node() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — the preset tables cannot be read.")
        print("  This test is the only thing standing between a preset and an")
        print("  animation the exporter cannot render; a skip is a real gap.")
        sys.exit(2)

    tmp = tempfile.mkdtemp(prefix="presets_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {
                "text": _file_url(os.path.join(ANIM, "text_presets.js")),
                "motion": _file_url(os.path.join(ANIM, "motion_presets.js")),
            })
        proc = subprocess.run(
            ["node", path, json.dumps([CAPTIONS, FRAMES])],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:3000])
            print("  the preset modules could not be evaluated (see above).")
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _file_url(path: str) -> str:
    from pathlib import Path

    return Path(path).resolve().as_uri()


# ---------------------------------------------------------------------------
print("The preset tables")
data = run_node()
TEXT_META = {p["id"]: p for p in data["textMeta"]}
MOTION_META = {p["id"]: p for p in data["motionMeta"]}

check("text presets load", len(TEXT_META) > 0, "(none came back)")
check("motion presets load", len(MOTION_META) > 0, "(none came back)")
check(
    "no duplicate text ids",
    len(TEXT_META) == len(data["textMeta"]),
    f"({len(data['textMeta'])} entries, {len(TEXT_META)} distinct)",
)
check(
    "no duplicate motion ids",
    len(MOTION_META) == len(data["motionMeta"]),
    f"({len(data['motionMeta'])} entries, {len(MOTION_META)} distinct)",
)

# ⚠ THE FIVE ORIGINAL IDS ARE A PUBLIC CONTRACT. The AI editor names a preset by
# id in its plans and validates it against `caps.text.presets`, so a saved plan
# naming one of these is a step that must still run. Renaming one is breaking
# somebody's plan; this is what says so out loud.
LEGACY = ["none", "fade", "rise", "drop", "slide"]
check(
    "the five original text preset ids still exist",
    all(i in TEXT_META for i in LEGACY),
    f"(missing {[i for i in LEGACY if i not in TEXT_META]})",
)
check(
    "…and are still the first five offered",
    [p["id"] for p in data["textMeta"][:5]] == LEGACY,
    f"(got {[p['id'] for p in data['textMeta'][:5]]})",
)
check(
    "every preset has a label and a one-line hint",
    all(p["label"] and p["hint"] for p in data["textMeta"] + data["motionMeta"]),
    "(a preset with no hint is a button nobody can guess)",
)

print(f"\n  {len(TEXT_META)} text presets, {len(MOTION_META)} picture moves")

# ---------------------------------------------------------------------------
print("\n1. Every value is one the server will take")
# ⚠ THROUGH THE REAL PYDANTIC MODEL, not through a copy of its bounds. A bound
# that moves in `schemas.py` and not in `preset_util.js` is exactly the drift
# this is here to catch, and comparing against a second copy of the numbers
# would move in step with the wrong one.
bad_bounds = []
for kind, table, model, fixtures in (
    ("text", data["text"], AnimaticTextClip, CAPTIONS),
    ("motion", data["motion"], AnimaticFrame, FRAMES),
):
    for preset_id, per_clip in table.items():
        for clip_name, patch in per_clip.items():
            merged = {**fixtures[clip_name], **patch}
            try:
                model(**merged)
            except Exception as exc:  # noqa: BLE001 — the message is the report
                bad_bounds.append(f"{kind}:{preset_id} on {clip_name}: {exc}"[:220])
check(
    "every preset's resting values validate",
    not bad_bounds,
    "\n       " + "\n       ".join(bad_bounds[:6]) if bad_bounds else "",
)

bad_keys = []
for kind, table, model, fixtures in (
    ("text", data["text"], AnimaticTextClip, CAPTIONS),
    ("motion", data["motion"], AnimaticFrame, FRAMES),
):
    bounds = {
        f.alias or name: f
        for name, f in model.model_fields.items()
    }
    for preset_id, per_clip in table.items():
        for clip_name, patch in per_clip.items():
            for prop, keys in (patch.get("keyframes") or {}).items():
                # A key's VALUE lands in the clip's own field the moment the
                # playhead reaches it, so it has to satisfy that field's bounds
                # exactly as the resting value does. Proved by building a clip
                # whose stored value IS the key's value.
                for key in keys:
                    try:
                        model(**{**fixtures[clip_name], prop: key["v"]})
                    except Exception:  # noqa: BLE001
                        bad_keys.append(
                            f"{kind}:{preset_id} on {clip_name}: {prop}={key['v']}"
                        )
check(
    "every KEY value validates too",
    not bad_keys,
    "\n       " + "\n       ".join(sorted(set(bad_keys))[:6]) if bad_keys else "",
)

# ---------------------------------------------------------------------------
print("\n2. Every key is a key both renderers understand")
bad_ease, unsorted, out_of_clip, empty = [], [], [], []
for kind, table, fixtures in (
    ("text", data["text"], CAPTIONS),
    ("motion", data["motion"], FRAMES),
):
    animatable = set(ANIMATABLE["text" if kind == "text" else "frame"])
    for preset_id, per_clip in table.items():
        for clip_name, patch in per_clip.items():
            length = fixtures[clip_name]["duration_ms"]
            for prop, keys in (patch.get("keyframes") or {}).items():
                where = f"{kind}:{preset_id} on {clip_name} [{prop}]"
                if prop not in animatable:
                    bad_ease.append(f"{where}: not an animatable property")
                if not keys:
                    empty.append(where)
                for key in keys:
                    if key.get("ease") not in EASINGS:
                        bad_ease.append(f"{where}: ease {key.get('ease')!r}")
                times = [k["t"] for k in keys]
                if times != sorted(times) or len(times) != len(set(times)):
                    unsorted.append(f"{where}: {times}")
                if times and (times[0] < 0 or times[-1] > length):
                    out_of_clip.append(f"{where}: {times[0]}…{times[-1]} of {length}")

check("every property written is animatable, every ease is known", not bad_ease,
      "\n       " + "\n       ".join(sorted(set(bad_ease))[:6]) if bad_ease else "")
check("keys are in time order with no duplicate times", not unsorted,
      "\n       " + "\n       ".join(unsorted[:4]) if unsorted else "")
# ⚠ A KEY PAST THE END OF THE CLIP IS AN ANIMATION NOBODY EVER SEES. It is not
# an error the renderers report — they interpolate quite happily — so the only
# way it shows up is somebody saying "the bounce doesn't finish".
check("no key lands outside its own clip", not out_of_clip,
      "\n       " + "\n       ".join(out_of_clip[:4]) if out_of_clip else "")
check("no preset writes an empty track", not empty,
      "\n       " + "\n       ".join(empty[:4]) if empty else "")

# ---------------------------------------------------------------------------
print("\n3. A preset that moves asks for free placement — and only then")
wrong_place = []
for preset_id, per_clip in data["text"].items():
    moves = TEXT_META[preset_id]["moves"]
    for clip_name, patch in per_clip.items():
        tracks = set((patch.get("keyframes") or {}).keys())
        travels = bool(tracks & {"x", "y"})
        if travels != moves:
            wrong_place.append(
                f"{preset_id} on {clip_name}: moves={moves} but writes {sorted(tracks)}"
            )
        # The flow fixtures are the ones that can be torn out of their zone.
        asked_free = patch.get("place") == "free"
        started_flow = CAPTIONS[clip_name].get("place", "flow") != "free"
        if travels and started_flow and not asked_free:
            wrong_place.append(f"{preset_id} on {clip_name}: moves but stays in flow")
        if not travels and asked_free:
            wrong_place.append(f"{preset_id} on {clip_name}: took a still caption out of its zone")
check(
    "`moves` matches what the preset actually writes, both ways",
    not wrong_place,
    "\n       " + "\n       ".join(sorted(set(wrong_place))[:6]) if wrong_place else "",
)

# ⚠ THE HALF OF THIS THAT PAYS FOR ITSELF: a preset that animates only scale,
# rotation or opacity can be dropped onto a whole run of generated captions
# WITHOUT moving one of them off its zone. That is the difference between a
# preset library for titles and one that can style a subtitle track, so it is
# worth a number rather than a hope.
still_ones = [p for p in data["textMeta"] if not p["moves"]]
check(
    "most text presets are safe on stacked subtitles",
    len(still_ones) >= len(TEXT_META) * 0.5,
    f"(only {len(still_ones)} of {len(TEXT_META)} leave placement alone)",
)

# ---------------------------------------------------------------------------
print("\n4. The exporter renders every move as a MOVE, not as one still")
# ⚠ THIS IS THE CHECK THAT WOULD HAVE CAUGHT THE `scale` BUG. `scene_signature`
# is the render cache key: `build_animatic` renders ONE still per distinct
# signature. A caption whose only animation was `scale` signed the same string at
# every moment of its life, so the MP4 held a single frame while the monitor
# animated. Every preset is now asked the same question: does the film's
# signature actually change while you are running?
frozen = []
for kind, table, fixtures in (
    ("text", data["text"], CAPTIONS),
    ("motion", data["motion"], FRAMES),
):
    for preset_id, per_clip in table.items():
        if preset_id == "none":
            continue
        for clip_name, patch in per_clip.items():
            clip = {**fixtures[clip_name], **patch}
            length = clip["duration_ms"]
            if kind == "text":
                project = {
                    "frames": [{"id": "bg", "src": _src("bg"), "duration_ms": length}],
                    "texts": [clip],
                }
            else:
                project = {"frames": [clip], "texts": []}
            seen = {
                scene_signature(scene_at(project, t, length))
                for t in range(0, length, max(1, length // 24))
            }
            if len(seen) < 2:
                frozen.append(f"{kind}:{preset_id} on {clip_name}")
check(
    "every preset changes the render cache key while it runs",
    not frozen,
    "\n       " + "\n       ".join(frozen[:8]) if frozen else "",
)

# And the regression itself, stated as the smallest case that shows it.
zoom_only = {
    "id": "z", "text": "zoom only", "start_ms": 0, "duration_ms": 1000,
    "place": "flow", "scale": 1.04,
    "keyframes": {"scale": [{"t": 0, "v": 1.0, "ease": "linear"},
                            {"t": 1000, "v": 1.04, "ease": "linear"}]},
}
turn_only = {
    "id": "r", "text": "turn only", "start_ms": 0, "duration_ms": 1000,
    "place": "flow",
    "keyframes": {"rotation": [{"t": 0, "v": -10.0, "ease": "linear"},
                               {"t": 1000, "v": 0.0, "ease": "linear"}]},
}
for name, clip in (("a caption that only zooms", zoom_only), ("…and one that only turns", turn_only)):
    proj = {"frames": [{"id": "bg", "src": _src("bg"), "duration_ms": 1000}], "texts": [clip]}
    sigs = {scene_signature(scene_at(proj, t, 1000)) for t in (0, 250, 500, 750, 999)}
    check(f"{name} is not cached as one frame", len(sigs) >= 4, f"(signed {len(sigs)} ways)")

# ⚠ AND THE OTHER HALF OF THAT FIX: a caption that has NEVER been zoomed or
# turned must sign byte-for-byte what it signed before any of this existed, or
# every project in the database re-renders on its next export for no reason.
old = {"id": "c", "text": "hi", "start_ms": 0, "duration_ms": 2000, "place": "flow"}
sig = scene_signature(scene_at(
    {"frames": [{"id": "a", "src": _src("a"), "duration_ms": 2000}], "texts": [old]}, 500, 2000
))
check(
    "an ordinary caption signs exactly what it always did",
    ":s" not in sig and ":r" not in sig,
    f"(got {sig!r})",
)

# ---------------------------------------------------------------------------
print("\n5. A turned caption is drawn where the monitor draws it")
# The one property in all of this that needed real code on both sides. Checked
# on the ink itself, because "the maths is right" and "the picture is right" have
# been different answers before.
BASE = dict(id="t1", text="Turn me", size_px=90, backdrop="box",
            color="#ffffff", stroke_px=4, shadow=0.08, opacity=1.0)


def render(**over):
    canvas = Image.new("RGB", (960, 540), (20, 20, 20))
    animatic.draw_texts(canvas, [dict(BASE, **over)])
    return np.asarray(canvas).astype(int)


def ink(img):
    """Where the caption actually is: the centre of every pixel that isn't ground."""
    mask = np.abs(img - np.array([20, 20, 20])).sum(axis=2) > 30
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean()), int(mask.sum())


free = dict(place="free", x=0.5, y=0.5)
# ⚠ 0° GOES DOWN THE OLD CODE PATH AND ANY OTHER ANGLE GOES ONTO A LAYER, so the
# two have to be the same picture where they meet or every caption in the app
# would change appearance the moment somebody nudged the angle off zero.
flat = render(**free, rotation=0)
almost = render(**free, rotation=0.0001)
diff = np.abs(flat - almost)
check(
    "the layer path and the direct path are one picture at 0°",
    diff.max() <= 12 and float(diff.mean()) < 0.2,
    f"(max {diff.max()}, mean {diff.mean():.3f})",
)

centres = {r: ink(render(**free, rotation=r)) for r in (0, 15, 45, 90, 180, -30)}
drift = max(
    max(abs(c[0] - 480.0), abs(c[1] - 270.0)) for c in centres.values() if c
)
# ⚠ A FREE CAPTION TURNS ABOUT ITS OWN CENTRE, which is what x/y MEAN and what
# `transform-origin: center center` gives it in the monitor. Turning about
# anything else is a caption that also slides — in the MP4 only.
check(
    "a free caption stays put as it turns",
    drift <= 2.0,
    f"(centre wandered {drift:.1f}px; {centres})",
)
check(
    "…and there is still a caption there at every angle",
    all(c and c[2] > 1000 for c in centres.values()),
    f"({ {r: (c[2] if c else 0) for r, c in centres.items()} })",
)


def flow_edges(rotation):
    canvas = Image.new("RGB", (960, 540), (20, 20, 20))
    animatic.draw_texts(canvas, [dict(BASE, place="flow", position="bottom", rotation=rotation)])
    mask = np.abs(np.asarray(canvas).astype(int) - np.array([20, 20, 20])).sum(axis=2) > 30
    ys = np.nonzero(mask)[0]
    return (int(ys.min()), int(ys.max())) if len(ys) else None


# A flow caption is anchored where its zone anchors it — `transform-origin:
# center bottom` for the bottom zone — so a small turn pivots about the bottom
# of the block and cannot lift the whole caption up the frame.
straight, tilted = flow_edges(0), flow_edges(10)
check(
    "a bottom-zone caption turns about its bottom edge",
    straight and tilted and abs(tilted[1] - straight[1]) < straight[1] * 0.12,
    f"(straight {straight}, tilted {tilted})",
)

# ⚠ AND THE ANGLE GOES THE WAY EVERY OTHER `rotation` IN THIS APP GOES:
# POSITIVE IS CLOCKWISE, like CSS, like `draw_shapes`. Told by where the ink
# lands, not by reading the sign off the code that would be wrong.
tall = dict(place="free", x=0.5, y=0.5, text="I", size_px=200, backdrop="plain",
            stroke_px=0, shadow=0)
right = ink(render(**{**BASE, **tall}, rotation=30))
left = ink(render(**{**BASE, **tall}, rotation=-30))
check(
    "a positive angle turns clockwise",
    right and left and right[0] > left[0],
    f"(+30° ink at x={right[0] if right else None}, −30° at x={left[0] if left else None})",
)

# ---------------------------------------------------------------------------
print("\n6. The moves are the moves they say they are")
# A pan must be held oversize, or it drags an empty edge into shot — the one
# failure in this file that is invisible on a square test image and obvious on a
# real storyboard panel.
thin_pans = []
for preset_id, per_clip in data["motion"].items():
    if not (preset_id.startswith("pan-") or preset_id.startswith("kb-")):
        continue
    for clip_name, patch in per_clip.items():
        keys = (patch.get("keyframes") or {}).get("scale") or []
        base = FRAMES[clip_name].get("scale", 1.0)
        if not keys or min(k["v"] for k in keys) <= base * 1.001:
            thin_pans.append(f"{preset_id} on {clip_name}")
check(
    "every pan is held oversize for the whole move",
    not thin_pans,
    "\n       " + "\n       ".join(thin_pans[:6]) if thin_pans else "",
)

# ⚠ AND IT MAY NOT TRAVEL FURTHER THAN THE OVERSCAN IT BOUGHT. At scale s the
# picture overhangs the frame by (s−1)/2 each side; a centre that moves further
# than that shows the edge. Measured against the TIGHTEST scale the move reaches,
# because that is the moment it has least room.
over_travelled = []
for preset_id, per_clip in data["motion"].items():
    for clip_name, patch in per_clip.items():
        tracks = patch.get("keyframes") or {}
        scales = [k["v"] for k in tracks.get("scale") or []] or [
            FRAMES[clip_name].get("scale", 1.0)
        ]
        tightest = min(scales)
        room = (tightest - 1.0) / 2.0
        for prop in ("x", "y"):
            for key in tracks.get(prop) or []:
                if abs(key["v"] - 0.5) > room + 1e-6:
                    over_travelled.append(
                        f"{preset_id} on {clip_name}: {prop}={key['v']:.4f} "
                        f"with only {room:.4f} of room at scale {tightest:.3f}"
                    )
check(
    "no move pans further than its overscan allows",
    not over_travelled,
    "\n       " + "\n       ".join(sorted(set(over_travelled))[:6]) if over_travelled else "",
)

# ---------------------------------------------------------------------------
print("\n7. Applying a preset twice is the same as applying it once")
# ⚠ BECAUSE EVERY PRESET WORKS OUTWARDS FROM THE CLIP'S RESTING VALUES, and the
# resting values are written back by the patch. If a preset read its starting
# point from wherever the LAST one left the clip instead, applying two in a row
# would walk a caption up the frame or shrink it twice — which is exactly what a
# person does while choosing between them.
IDEM = """
import { TEXT_PRESETS, applyTextPreset } from "%(text)s";
import { MOTION_PRESETS, applyMotionPreset } from "%(motion)s";
const [captions, frames] = JSON.parse(process.argv[2]);
const out = [];
const drift = (a, b) => ["x", "y", "scale", "rotation", "opacity"]
  .filter((p) => p in a && p in b && Math.abs(a[p] - b[p]) > 1e-9);
for (const p of TEXT_PRESETS) {
  for (const [name, clip] of Object.entries(captions)) {
    const first = applyTextPreset(clip, p.id);
    const second = applyTextPreset({ ...clip, ...first }, p.id);
    const moved = drift(first, second);
    if (moved.length) out.push(`text:${p.id} on ${name} drifted on ${moved.join()}`);
  }
}
for (const p of MOTION_PRESETS) {
  for (const [name, clip] of Object.entries(frames)) {
    const first = applyMotionPreset(clip, p.id);
    const second = applyMotionPreset({ ...clip, ...first }, p.id);
    const moved = drift(first, second);
    if (moved.length) out.push(`motion:${p.id} on ${name} drifted on ${moved.join()}`);
  }
}
process.stdout.write(JSON.stringify(out));
"""


def run_idem() -> list:
    tmp = tempfile.mkdtemp(prefix="presets_idem_")
    try:
        path = os.path.join(tmp, "idem.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(IDEM % {
                "text": _file_url(os.path.join(ANIM, "text_presets.js")),
                "motion": _file_url(os.path.join(ANIM, "motion_presets.js")),
            })
        proc = subprocess.run(
            ["node", path, json.dumps([CAPTIONS, FRAMES])],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ⚠ EXCEPT FOR THE MOVES THAT DELIBERATELY END SOMEWHERE ELSE. "Slow push" rests
# at 104% because that is where its move finishes, so applying it to its own
# result rests at 108% — correctly. Those are named rather than excused, so a
# preset that drifts by ACCIDENT still fails.
ENDS_ELSEWHERE = {
    "text:push", "text:glide",
    "motion:push-in", "motion:push-in-fast", "motion:push-in-big",
    "motion:pan-left", "motion:pan-right", "motion:pan-up", "motion:pan-down",
    "motion:kb-left", "motion:kb-right", "motion:kb-up", "motion:kb-down",
    "motion:shake", "motion:shake-hard", "motion:breathe", "motion:drift",
    "motion:handheld",
}
drifted = [
    line for line in run_idem()
    if line.split(" on ")[0] not in ENDS_ELSEWHERE
]
check(
    "applying a preset to its own result changes nothing it shouldn't",
    not drifted,
    "\n       " + "\n       ".join(drifted[:8]) if drifted else "",
)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print(f"All good — {len(TEXT_META)} text presets and {len(MOTION_META)} picture moves.")
