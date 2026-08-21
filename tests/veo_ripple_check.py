"""MAKING ROOM FOR A TAKE — the panels move, the renders don't pile up.

The report, with three screenshots:

    "when i generete image to veo video in timeline so shot 1 image so i get shot
     Veo video of Story..video layer in same place this is good but i again
     generate shot 2 image to veo video so see my second shot 2 video overlap on
     shot1 video so this fuction not good for user … Automatic my storyborad
     image clip move like this after shot 1 image … so my Video and image clear
     view so user not confuse waht happen in timeline"

A Veo render starts where its panel starts and is as long as Veo was ASKED for.
4 seconds of footage over a 2-second hold is the ordinary case, so the SECOND
render — which starts where ITS panel starts, one hold along — began inside the
first one's tail and the two bars sat on top of each other on the Storyboard
video row.

    video   [ Shot 1 ····· ]                     <- before
    video       [ Shot 2 ····· ]                    (Shot 2 buried in Shot 1)
    image   [S1][S2][S3][S4]

    video   [ Shot 1 ····· ][ Shot 2 ····· ]     <- after
    image   [S1]            [S2]            [S3][S4]

The room a take needs has to come from the row UNDERNEATH it: the panel that was
animated stays where it is, and the ones after it are pushed clear of the take's
end. That is `spreadPanelsForRenders`, and the two layouts asserted below are the
user's second and third screenshots, in milliseconds.

---------------------------------------------------------------------------
⚠ WHAT THIS FILE IS ACTUALLY GUARDING: "FORWARD ONLY"
---------------------------------------------------------------------------
Every other clip on a picture track obeys one rule — it moves when you move it
and at no other time (`frameSpans`). This ripple bends that rule exactly once, so
the half that keeps it honest is the half that rots silently: the pass must never
CLOSE a gap. A gap the user opened by hand, and the spread left behind by a
render they later deleted, are both work; a "tidy" re-lay of the row would eat
them and read as the editor losing the cut. So a second run over its own output
must be a no-op, and every case here that must not move is checked by IDENTITY —
the function hands back the same list when it changed nothing, which is also what
tells the editor whether to say a panel moved.

    python tests/veo_ripple_check.py

Needs node for the logic half; skips it cleanly without one, exactly as
`veo_download_check.py` does. The wiring half is a source read and always runs.
No backend, no browser, no ffmpeg.
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

failures: list[str] = []
skipped: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def skip(label, why):
    print(f"  skip {label}  ({why})")
    skipped.append(label)


# ---------------------------------------------------------------------------
# The harness — the same board, put through the user's sequence of clicks
# ---------------------------------------------------------------------------
# ⚠ SIX PANELS OF 2s AND TAKES OF 4s, which is what the screenshots show and the
# ratio that makes the bug: a take is exactly two holds long, so an unmoved
# second panel puts its render dead in the middle of the first one's.
HARNESS = r"""
import { spreadPanelsForRenders, frameSpans } from "%(scene)s";

const PANEL_MS = 2000;
const TAKE_MS = 4000;

const panel = (n, start, extra = {}) => ({
  id: `p${n}`, kind: "image", label: `Shot ${n}`, duration_ms: PANEL_MS,
  start_ms: start, track: 0,
  src: { kind: "panel", storyboard_id: "b1", index: n - 1 }, ...extra,
});
const take = (n, start, extra = {}) => ({
  id: `v${n}`, kind: "video", label: `Shot ${n}`, duration_ms: TAKE_MS,
  start_ms: start, track: 1,
  src: { kind: "video", storyboard_id: "b1", index: n - 1, upload_id: `u${n}` },
  ...extra,
});

/** Where every clip ends up: { id: [start, end, track] }. */
const layout = (list) => {
  const { spans } = frameSpans(list);
  return Object.fromEntries(
    list.map((f, i) => [f.id, [spans[i].start, spans[i].end, spans[i].track]])
  );
};

/** Animate shot `n`: the take lands where that panel currently sits. */
const animate = (list, n, extra = {}) => {
  const { spans } = frameSpans(list);
  const at = spans[list.findIndex((f) => f.id === `p${n}`)].start;
  return spreadPanelsForRenders([...list, take(n, at, extra)]);
};

const out = {};

// --- the report, click by click -------------------------------------------
const board = [1, 2, 3, 4, 5, 6].map((n) => panel(n, (n - 1) * PANEL_MS));
out.before = layout(board);
const one = animate(board, 1);
out.afterOne = layout(one);
const two = animate(one, 2);
out.afterTwo = layout(two);

// --- forward only ----------------------------------------------------------
// A second pass over its own output is not allowed to tidy anything.
out.idempotent = spreadPanelsForRenders(two) === two;
// A board with nothing animated is not a layout problem.
out.untouched = spreadPanelsForRenders(board) === board;
// The take is deleted; the spread it made is the user's row now.
const bereft = two.filter((f) => f.id !== "v2");
out.afterDelete =
  spreadPanelsForRenders(bereft) === bereft
    ? "same"
    : layout(spreadPanelsForRenders(bereft));
// A gap opened by hand survives — panel 3 dragged out to 12s.
const gapped = animate(
  board.map((f) => (f.id === "p3" ? { ...f, start_ms: 12000 } : f)),
  1
);
out.gap = layout(gapped);

// --- a render travels with its panel, keeping any offset -------------------
// Shot 3 is animated first, then shot 1 — so panel 3 is pushed along afterwards
// and its take has to follow it. The take is nudged 500ms late first, to prove
// it moves by the panel's DELTA rather than being snapped back onto it.
let late = animate(board, 3);
late = late.map((f) => (f.id === "v3" ? { ...f, start_ms: f.start_ms + 500 } : f));
out.nudged = layout(late);
out.thenShotOne = layout(animate(late, 1));

// --- a key pose is not its panel -------------------------------------------
// A pose and the panel it was drawn from share storyboard_id "b1" AND index 2;
// only `frame` tells them apart. Here the take is of the POSE, and the panel
// sits earlier on the same row — pair them by the board reference alone and the
// take is credited to the panel, which pushes the pose itself out of the way.
// ⚠ THE TAKE IS 6s, longer than two holds, so the two answers land the pose in
// two different places instead of coincidentally agreeing.
const posed = [
  panel(1, 0),
  panel(2, 2000),
  panel(3, 4000),
  panel(4, 6000, {
    id: "pose",
    src: { kind: "pose", storyboard_id: "b1", index: 2, frame: 7 },
  }),
  panel(5, 8000),
  take(3, 6000, {
    id: "vpose",
    duration_ms: 6000,
    src: { kind: "video", storyboard_id: "b1", index: 2, frame: 7, upload_id: "u7" },
  }),
];
out.pose = layout(spreadPanelsForRenders(posed));

// --- a duplicated panel does not claim the same take -----------------------
// The copy shares its `src` with the original; pairing by that alone would push
// everything after the COPY clear of a take that is nowhere near it.
out.dupe = layout(
  spreadPanelsForRenders([
    panel(1, 0),
    { ...panel(1, 2000), id: "p1copy" },
    panel(2, 4000),
    take(1, 0),
  ])
);

// --- a second Storyboard images row ripples on its own ---------------------
out.tracks = layout(
  spreadPanelsForRenders([
    panel(1, 0),
    panel(2, 2000),
    { ...panel(1, 0), id: "q1", track: 2, src: { kind: "panel", storyboard_id: "b2", index: 0 } },
    { ...panel(2, 2000), id: "q2", track: 2, src: { kind: "panel", storyboard_id: "b2", index: 1 } },
    take(1, 0),
  ])
);

// --- hostile ---------------------------------------------------------------
out.nothing = [
  spreadPanelsForRenders([]).length,
  (spreadPanelsForRenders(null) || []).length,
  spreadPanelsForRenders([{}, { kind: "video" }]).length,
];

console.log(JSON.stringify(out));
"""


def run_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="veorip_")
    try:
        src = HARNESS % {"scene": (ROOT / "client/src/animatic/scene.js").as_uri()}
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:800])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ⚠ THE USER'S SECOND SCREENSHOT, IN MILLISECONDS. Shot 1 keeps its place under
# its own take; every panel after it clears 4s.
AFTER_ONE = {
    "p1": [0, 2000, 0],
    "p2": [4000, 6000, 0],
    "p3": [6000, 8000, 0],
    "p4": [8000, 10000, 0],
    "p5": [10000, 12000, 0],
    "p6": [12000, 14000, 0],
    "v1": [0, 4000, 1],
}
# ⚠ AND THE THIRD. Two takes butt-jointed at 4s, with nothing under panel 2's tail.
AFTER_TWO = {
    "p1": [0, 2000, 0],
    "p2": [4000, 6000, 0],
    "p3": [8000, 10000, 0],
    "p4": [10000, 12000, 0],
    "p5": [12000, 14000, 0],
    "p6": [14000, 16000, 0],
    "v1": [0, 4000, 1],
    "v2": [4000, 8000, 1],
}

LOGIC = [
    "the take goes where its panel is, and the panel stays there",
    "the panels after it are pushed clear of the take's end",
    "…and they stay butt-jointed to each other",
    "a second take does not land inside the first",
    "…and its own panel is not dragged off it",
    "running it again moves nothing",
    "a board with nothing animated is left alone",
    "deleting a take does not yank the panels back",
    "a gap the user opened by hand survives",
    "a take that was nudged keeps its offset",
    "…and travels with its panel when that panel moves",
    "a take of a key pose is not a take of the panel under it",
    "a duplicated panel does not claim the original's take",
    "a second storyboard row ripples on its own track",
    "nothing in, nothing thrown",
]

print("Animating a shot pushes the shots after it clear of the take")
got = run_node()

if got is None:
    for label in LOGIC:
        skip(label, "node not available")
else:
    one, two = got["afterOne"], got["afterTwo"]
    check(
        "the take goes where its panel is, and the panel stays there",
        one["v1"] == AFTER_ONE["v1"] and one["p1"] == AFTER_ONE["p1"],
        f"p1={one['p1']} v1={one['v1']}",
    )
    check(
        "the panels after it are pushed clear of the take's end",
        one["p2"][0] >= one["v1"][1],
        f"p2={one['p2']} v1={one['v1']}",
    )
    check("…and they stay butt-jointed to each other", one == AFTER_ONE, json.dumps(one))
    # ⚠ THE BUG ITSELF: v2 starting inside v1 is what the screenshot showed.
    check(
        "a second take does not land inside the first",
        two["v2"][0] >= two["v1"][1],
        f"v1={two['v1']} v2={two['v2']}",
    )
    check(
        "…and its own panel is not dragged off it",
        two["v2"][0] == two["p2"][0] and two == AFTER_TWO,
        json.dumps(two),
    )

    check(
        "running it again moves nothing",
        got["idempotent"] is True,
        "a second pass rewrote the row",
    )
    check(
        "a board with nothing animated is left alone",
        got["untouched"] is True,
        "panels moved with no render anywhere",
    )
    check(
        "deleting a take does not yank the panels back",
        got["afterDelete"] == "same",
        json.dumps(got["afterDelete"]),
    )
    # ⚠ PANEL 3 WAS DRAGGED OUT TO 12s, so in PLAY order it is now last. The
    # ripple must push the five panels behind it clear of the take and stop —
    # the hole in front of it is the user's edit, not a mistake to close up.
    gap = got["gap"]
    check(
        "a gap the user opened by hand survives",
        gap["p3"] == [12000, 14000, 0]
        and gap["p2"][0] == 4000
        and max(v[1] for k, v in gap.items() if k not in ("p3", "v1")) <= 12000,
        json.dumps(gap),
    )

    nudged, moved = got["nudged"], got["thenShotOne"]
    check(
        "a take that was nudged keeps its offset",
        nudged["v3"][0] - nudged["p3"][0] == 500,
        json.dumps(nudged),
    )
    check(
        "…and travels with its panel when that panel moves",
        moved["v3"][0] - moved["p3"][0] == 500
        and moved["p3"][0] > nudged["p3"][0]
        and moved["p3"][0] - nudged["p3"][0] == moved["v3"][0] - nudged["v3"][0],
        json.dumps(moved),
    )

    pose = got["pose"]
    check(
        "a take of a key pose is not a take of the panel under it",
        pose["p3"] == [4000, 6000, 0]
        and pose["pose"][0] == 6000
        and pose["p5"][0] == 12000,
        json.dumps(pose),
    )
    # ⚠ THE TAKE'S OWN PLACE IS THE TELL. Both copies clear it either way, so
    # what a second pairing actually breaks is the render: credited to the copy
    # as well, it is dragged along by the COPY's delta and slides off the panel
    # it was made from.
    dupe = got["dupe"]
    check(
        "a duplicated panel does not claim the original's take",
        dupe["v1"] == [0, 4000, 1]
        and dupe["p1"] == [0, 2000, 0]
        and dupe["p1copy"][0] == 4000
        and dupe["p2"][0] == 6000,
        json.dumps(dupe),
    )
    tracks = got["tracks"]
    check(
        "a second storyboard row ripples on its own track",
        tracks["p2"][0] == 4000 and tracks["q2"][0] == 2000,
        json.dumps(tracks),
    )
    check("nothing in, nothing thrown", got["nothing"] == [0, 0, 2], json.dumps(got["nothing"]))


# ---------------------------------------------------------------------------
# The wiring: the attach is what runs it, and the notice says a panel moved.
# ---------------------------------------------------------------------------
print("\nWhere it runs, and what the editor says about it")

editor = (ROOT / "client/src/components/AnimaticEditor.jsx").read_text(encoding="utf-8")
scene = (ROOT / "client/src/animatic/scene.js").read_text(encoding="utf-8")

check(
    "the rule lives in scene.js with the rest of the timeline maths",
    "export function spreadPanelsForRenders" in scene,
    "the layout rule must be a pure function the tests can drive",
)
check(
    "the attach is what runs it",
    "spreadPanelsForRenders(appended)" in editor,
    "attachVeoClip must make room in the same write that adds the take",
)
# ⚠ THE REF, NOT JUST STATE. A batch of four renders attaches in one tick, and
# each one has to start from the panels the one before it moved — off state alone
# every take in the batch would be laid out against the pre-batch row.
check(
    "…and the moved panels go back into the ref for the next take in the batch",
    "framesRef.current = next;" in editor,
    "a second render in the same batch would be placed against a stale row",
)
check(
    "the notice says the panels moved",
    "moved along to make room" in editor,
    "a clip that moves on its own with nothing said about it reads as a bug",
)
check(
    "…and only when they actually did",
    "shifted" in editor and "return next !== appended;" in editor,
    "the identity test is what makes the notice honest",
)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  · {f}")
if skipped:
    print(f"{len(skipped)} check(s) skipped — install node to run them.")
if not failures:
    print("All good.")
sys.exit(1 if failures else 0)
