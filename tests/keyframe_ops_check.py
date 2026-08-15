"""The keyframe EDITING operations — the half `render_parity.py` doesn't cover.

`client/src/animatic/keyframes.js` is the only part of the keyframe system with
no Python twin: the server renders animations, it never edits them. So it gets
its own test rather than a parity check, driven through node the same way.

The rules asserted here are the ones a person notices immediately when they are
wrong — every one of them is a "the picture jumped when I touched that" bug:

  * turning the stopwatch ON must not change the picture
  * turning it OFF must keep what is on screen, not snap back
  * adding a key at the playhead must not change the picture either
  * re-typing a value must not silently reset an easing curve the user chose
  * two keys must never end up at the same instant

    python tests/keyframe_ops_check.py

Needs `node` on PATH.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
ANIMATIC_DIR = ROOT / "client" / "src" / "animatic"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# The whole suite runs inside node, because that is where the code lives. Each
# case reports a plain {label, got, want} so failures read the same as any other
# check in this directory.
HARNESS = """
import {
  enableProp, disableProp, setKey, removeKey, setKeyEase, moveKey, moveKeysAt,
  keysOf, keyAt, neighbourKey, animatedProps, isAnimatedProp,
} from %(kf)s;
import { valueAt, ANIMATABLE } from %(scene)s;

const out = [];
const say = (label, got, want) => out.push({ label, got, want });
// A clip is edited by merging the returned patch, exactly as the editor does.
const apply = (clip, patch) => ({ ...clip, ...patch });

const shape = {
  id: "s1", start_ms: 1000, duration_ms: 2000,
  x: 0.25, y: 0.5, w: 0.3, h: 0.3, opacity: 0.8, rotation: 30,
};

// --- the stopwatch --------------------------------------------------------
let c = apply(shape, enableProp(shape, "x", 500, 0.5));
say("enable puts exactly one key", keysOf(c, "x").length, 1);
say("enable puts it at the playhead", keysOf(c, "x")[0].t, 500);
say("enable keeps the value that was there", valueAt(c, "x", 500, 0.5), 0.25);
say("enable does not change the value anywhere else",
    valueAt(c, "x", 1900, 0.5), 0.25);
say("the property now reads as animated", isAnimatedProp(c, "x"), true);

// Animate it for real, then switch the stopwatch off half way along.
c = apply(c, setKey(c, "x", 1500, 0.75));
say("a second key makes it move", valueAt(c, "x", 1000, 0.5), 0.5);
const frozen = apply(c, disableProp(c, "x", 1000, 0.5));
say("disable removes every key", keysOf(frozen, "x").length, 0);
// THE RULE: it keeps what was ON SCREEN, not the value the clip started with.
say("disable freezes the value on screen, it does not snap back", frozen.x, 0.5);

// --- the diamond ----------------------------------------------------------
const mid = apply(c, setKey(c, "x", 1000, valueAt(c, "x", 1000, 0.5)));
say("adding a key mid-animation does not move the value",
    valueAt(mid, "x", 1000, 0.5), 0.5);
say("adding a key mid-animation leaves the ends alone",
    [valueAt(mid, "x", 500, 0.5), valueAt(mid, "x", 1500, 0.5)].join(","), "0.25,0.75");
say("three keys now", keysOf(mid, "x").length, 3);
const pulled = apply(mid, removeKey(mid, "x", 1000));
say("removing it puts the count back", keysOf(pulled, "x").length, 2);
say("removing the last key stops the animation",
    isAnimatedProp(apply(apply(pulled, removeKey(pulled, "x", 500)), removeKey(apply(pulled, removeKey(pulled, "x", 500)), "x", 1500)), "x"),
    false);

// A key within the tolerance of the playhead is the SAME key, not a new one.
const near = apply(c, setKey(c, "x", 508, 0.9));
say("a key within tolerance is edited, not duplicated", keysOf(near, "x").length, 2);
say("and it keeps its original time", keysOf(near, "x")[0].t, 500);
say("but it does take the new value", keysOf(near, "x")[0].v, 0.9);

// --- easing ---------------------------------------------------------------
let eased = apply(c, setKeyEase(c, "x", 500, "ease-in-out"));
say("the curve is set on the key you are standing on",
    keysOf(eased, "x")[0].ease, "ease-in-out");
eased = apply(eased, setKey(eased, "x", 500, 0.3));
// THE RULE: re-typing a value must not throw away a curve that was chosen.
say("re-typing a value keeps the chosen curve", keysOf(eased, "x")[0].ease, "ease-in-out");
say("a NEW key still defaults to linear",
    keysOf(apply(eased, setKey(eased, "x", 1200, 0.6)), "x").find((k) => k.t === 1200).ease,
    "linear");

// --- moving a key ---------------------------------------------------------
const moved = apply(c, moveKey(c, "x", 500, 1200));
say("a moved key lands where it was dropped",
    keysOf(moved, "x").map((k) => k.t).join(","), "1200,1500");
say("moving does not change the value", keysOf(moved, "x")[0].v, 0.25);
// Dropping one key onto another must REPLACE it — two keys at one instant make
// the result depend on sort order, which is not something a user can reason about.
const collided = apply(c, moveKey(c, "x", 500, 1500));
say("dropping a key onto another leaves ONE key there",
    keysOf(collided, "x").length, 1);
say("moving a key that isn't there does nothing", moveKey(c, "x", 9999, 0), null);

// --- navigation -----------------------------------------------------------
say("next key from before the first", neighbourKey(c, "x", 0, 1).t, 500);
say("previous key from after the last", neighbourKey(c, "x", 9999, -1).t, 1500);
say("no next key past the end", neighbourKey(c, "x", 9999, 1), null);
say("standing ON a key finds it", keyAt(c, "x", 500).t, 500);
say("standing between keys finds none", keyAt(c, "x", 900), null);

// --- dragging a diamond ---------------------------------------------------
// A diamond is an INSTANT, and a Ken Burns push keys `scale` and `x` at the
// same one. Dragging it must carry BOTH — moving only the property that
// happened to be looked at first would pull the push apart, and the picture
// would start zooming and panning at different moments for no visible reason.
const push = {
  id: "f1",
  keyframes: {
    scale: [{ t: 0, v: 1, ease: "ease-in-out" }, { t: 2000, v: 2 }],
    x: [{ t: 0, v: 0.5 }, { t: 2000, v: 0.3 }],
  },
};
const dragged = apply(push, moveKeysAt(push, 0, 400));
say("dragging a diamond moves EVERY property keyed at that instant",
    [keysOf(dragged, "scale")[0].t, keysOf(dragged, "x")[0].t].join(","), "400,400");
say("it leaves the other keys where they were",
    [keysOf(dragged, "scale")[1].t, keysOf(dragged, "x")[1].t].join(","), "2000,2000");
say("dragging preserves each property's own value",
    [keysOf(dragged, "scale")[0].v, keysOf(dragged, "x")[0].v].join(","), "1,0.5");
say("dragging preserves the easing curve", keysOf(dragged, "scale")[0].ease, "ease-in-out");
say("dragging where there is no key does nothing", moveKeysAt(push, 900, 100), null);
// A property keyed at only ONE of the two instants must not be dragged along
// just because it shares the clip.
const mixed = {
  id: "f2",
  keyframes: { a: [{ t: 0, v: 0 }, { t: 500, v: 1 }], b: [{ t: 500, v: 9 }] },
};
const partly = apply(mixed, moveKeysAt(mixed, 0, 200));
say("a property with no key at that instant is left alone",
    keysOf(partly, "b").map((k) => k.t).join(","), "500");
say("and the one that did have a key moved",
    keysOf(partly, "a").map((k) => k.t).join(","), "200,500");

// --- the timeline's diamond ROWS ------------------------------------------
// The timeline draws ONE ROW PER ANIMATED PROPERTY, in `ANIMATABLE` order —
// which is the order the Properties pane lists the same controls in, so the
// two panes read as one thing. It used to merge every property into a single
// diamond per instant, and that hid exactly what you need to see: two keys at
// one instant looked identical to one.
const multi = apply(apply(c, enableProp(c, "opacity", 500, 1)), {});
const both = { ...multi, keyframes: { ...multi.keyframes,
  opacity: [{ t: 500, v: 0 }, { t: 900, v: 1 }] } };
const rows = ANIMATABLE.shape.filter((p) => keysOf(both, p).length > 0);
say("a row per animated property, in the Properties pane's order",
    rows.join(","), "x,opacity");
say("properties keyed at the SAME instant stay on separate rows",
    [keysOf(both, "x")[0].t, keysOf(both, "opacity")[0].t].join(","), "500,500");
say("an un-animated property gets no row at all",
    ANIMATABLE.shape.filter((p) => keysOf(both, p).length > 0).includes("rotation"), false);
say("every animated property is accounted for",
    animatedProps(both).sort().join(","), "opacity,x");

// --- untouched clips ------------------------------------------------------
say("a clip with no keyframes has no rows", animatedProps(shape).length, 0);
say("and reads as not animated", isAnimatedProp(shape, "x"), false);
say("and resolves to its stored value", valueAt(shape, "x", 500, 0.5), 0.25);

process.stdout.write(JSON.stringify(out));
"""


def run() -> list:
    if not shutil.which("node"):
        print("  node is not on PATH — these operations cannot be checked.")
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="kfops_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        src = HARNESS % {
            "kf": json.dumps((ANIMATIC_DIR / "keyframes.js").as_uri()),
            "scene": json.dumps((ANIMATIC_DIR / "scene.js").as_uri()),
        }
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


print("Keyframe editing operations — client/src/animatic/keyframes.js\n")
for case in run():
    got, want = case["got"], case["want"]
    # JS has one number type; 1 and 1.0 are the same value and not a difference.
    same = (
        abs(got - want) < 1e-9
        if isinstance(got, (int, float)) and isinstance(want, (int, float))
        and not isinstance(got, bool) and not isinstance(want, bool)
        else got == want
    )
    check(case["label"], same, f"(got {got!r}, want {want!r})")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Keyframe editing behaves.")
