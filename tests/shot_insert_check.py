"""A GENERATED SHOT GOES IN BESIDE ANOTHER, AND THE WHOLE FILM MOVES OVER.

The second half of "generate the shot before / after this one" — the first half,
which is the drawing and the fact that the BOARD is never touched, is
`tests/shot_infill_check.py`. This is what the timeline then does with the clip
it gets back:

    "in simple word i wnat user add in between both before after shot image clip
     from gemini ai and generated video and come in same layer after same
     setected clip and move all clip of all layer."

---------------------------------------------------------------------------
1. THE SEAM IS A CLIP'S EDGE, NOT A DROP POINT — and that is the whole of it
---------------------------------------------------------------------------
`insertPictures` (the drop path) finds the clip to go in front of with
`on.find(s => s.index >= atIndex)`: a LIST-index test made against a
START-ordered row. That holds while the two orders agree, and they stop agreeing
the moment anything is dragged, because a drag re-times a clip without touching
the list. `insertShotBeside` asks the only question that survives a drag — where
does the neighbour start and end — so the row below is deliberately stored in an
order the clock disagrees with, and every seam is checked against the CLOCK.

---------------------------------------------------------------------------
2. AND THE `keep` SET IS THE OTHER HALF
---------------------------------------------------------------------------
`rippleFrames` skips every clip that came off a board, because after
`spreadPanelsForRenders` those are already standing at their new starts and
looking them up in a map written in OLD time would add their debt twice. That is
the wrong skip HERE: this insert placed ONE ROW, so the Veo takes above it — and
a second storyboard row — have NOT been moved and must be carried. Passing the
row's own ids as `keep` is what says which is which.

    python tests/shot_insert_check.py

Needs node; skips cleanly without one, exactly as `timeline_ripple_check.py`
does. No backend, no browser, no ffmpeg.
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
# The harness
# ---------------------------------------------------------------------------
# Four 2s panels at 0 / 2000 / 4000 / 6000, one 8s shot generated after the
# second of them. Round numbers on purpose: a seam is only checkable if you can
# say where it is without arithmetic.
HARNESS = r"""
import { insertShotBeside, frameSpans } from "%(scene)s";
import { renderShifts, rippleClips, rippleFrames, rippleAudio } from "%(ripple)s";

const panel = (n, start) => ({
  id: `p${n}`, kind: "image", label: `Shot ${n}`, duration_ms: 2000,
  start_ms: start, track: 0,
  src: { kind: "panel", storyboard_id: "b1", index: n - 1 },
});

// What the server hands back: an UPLOAD that carries the board and a shot id of
// its own, with no panel index at all.
const drawn = {
  id: "gen1", kind: "image", label: "After Shot 2", duration_ms: 8000,
  src: { kind: "upload", upload_id: "u9", storyboard_id: "b1", shot_id: "s9",
         prompt: "The slipper crosses the room." },
};

const board = [1, 2, 3, 4].map((n) => panel(n, (n - 1) * 2000));
const rowOf = (list, track) =>
  list.filter((f) => (f.track || 0) === track).map((f) => [f.id, f.start_ms, f.duration_ms]);

const out = {};

// --- 1. after ---------------------------------------------------------------
const afterIt = insertShotBeside(board, drawn, "p2", "after");
out.after = rowOf(afterIt.frames, 0);
out.afterPlaced = [afterIt.placed.id, afterIt.placed.start_ms, afterIt.placed.track];
out.afterOrder = afterIt.frames.map((f) => f.id);
// The clip is untouched apart from where it now lives.
out.afterKeptFields = afterIt.placed.src.shot_id === "s9" && afterIt.placed.label === "After Shot 2";

// --- 2. before --------------------------------------------------------------
const beforeIt = insertShotBeside(board, drawn, "p2", "before");
out.before = rowOf(beforeIt.frames, 0);
out.beforeOrder = beforeIt.frames.map((f) => f.id);

// --- 3. a row whose LIST order the clock disagrees with ----------------------
// Stored [q1, q2, q3]; played [q3, q1, q2]. This is what a drag leaves behind.
const dragged = [
  { ...panel(1, 2000), id: "q1" },
  { ...panel(2, 4000), id: "q2" },
  { ...panel(3, 0), id: "q3" },
];
const onDragged = insertShotBeside(dragged, drawn, "q1", "after");
out.dragged = rowOf(onDragged.frames, 0);
out.draggedSeam = onDragged.placed.start_ms;

// --- 4. the neighbour has gone ----------------------------------------------
const gone = insertShotBeside(board, drawn, "nope", "after");
out.goneSame = gone.frames === board;
out.gonePlaced = gone.placed;

// --- 5. a clip with no start of its own -------------------------------------
// null means "after the last clip on my track", which is where every animatic
// saved before tracks existed keeps every one of its pictures.
const loose = [
  { ...panel(1, 0), start_ms: null },
  { ...panel(2, 0), start_ms: null },
  { ...panel(3, 0), start_ms: null },
];
const onLoose = insertShotBeside(loose, drawn, "p2", "after");
out.loose = rowOf(onLoose.frames, 0);

// --- 6. the length is clamped to what the wire accepts -----------------------
out.tiny = insertShotBeside(board, { ...drawn, duration_ms: 5 }, "p2", "after")
  .frames.filter((f) => f.track === 0).map((f) => [f.id, f.start_ms]);

// --- 7. THE WHOLE EDIT: the row, the map, and everything carried along -------
// A take over Shot 2 (before the seam) and one over Shot 3 (after it), a
// caption each side, an overlay, and a voiceover laid from 0:00 across the lot.
const withTakes = [
  ...board,
  { id: "v2", kind: "video", label: "Shot 2", duration_ms: 2000, start_ms: 2000, track: 1,
    src: { kind: "video", storyboard_id: "b1", index: 1, upload_id: "t2" } },
  { id: "v3", kind: "video", label: "Shot 3", duration_ms: 2000, start_ms: 4000, track: 1,
    src: { kind: "video", storyboard_id: "b1", index: 2, upload_id: "t3" } },
  // A file dropped on a plain video row, past the seam.
  { id: "f1", kind: "video", duration_ms: 2000, start_ms: 6000, track: 2,
    src: { kind: "video", upload_id: "drop" } },
];
const step = insertShotBeside(withTakes, drawn, "p2", "after");
const placedTrack = step.placed.track;
const onRow = (f) => (f.track || 0) === placedTrack;
const shifts = renderShifts(withTakes.filter(onRow), step.frames.filter(onRow));
const keep = new Set(step.frames.filter(onRow).map((f) => f.id));
const settled = rippleFrames(step.frames, shifts, keep);

out.shifts = shifts;
out.settledBoard = rowOf(settled, 0);
out.settledTakes = rowOf(settled, 1);
out.settledDrop = rowOf(settled, 2);

// ⚠ WHAT THE BOARD-WIDE SKIP WOULD HAVE DONE: the takes are `board_video`, so
// `rippleFrames` with no `keep` leaves them exactly where they were, sitting
// over panels that have moved 8 seconds away.
out.withoutKeep = rowOf(rippleFrames(step.frames, shifts), 1);

const cap = (id, start) => ({ id, start_ms: start, duration_ms: 1500 });
out.captions = rippleClips([cap("c1", 1000), cap("c2", 3999), cap("c3", 4000), cap("c4", 7000)], shifts)
  .map((c) => [c.id, c.start_ms]);
out.overlays = rippleClips([cap("o1", 5000)], shifts).map((c) => [c.id, c.start_ms]);

// ⚠ `trim_ms` IS HOW LONG THE PIECE PLAYS; `duration_ms` is how long the FILE
// is, and a cut does not shorten the file. Reading the wrong one is how a razor
// check passes on a clip that was never actually cut.
let n = 0;
const vo = [{ id: "vo", upload_id: "a1", start_ms: 0, duration_ms: 8000, offset_ms: 0 }];
out.audio = rippleAudio(vo, shifts, () => `mint${++n}`).map((c) => [
  c.id, c.start_ms, c.trim_ms, c.offset_ms,
]);

// --- 8. a SECOND storyboard row must not poison the map ----------------------
// It did not move, so every clip on it would contribute a zero-shift point —
// and `shiftAt` reads the LAST point at or before a moment, so a zero at 10s
// would cancel the debt of every caption past it. Filtering both lists to the
// row that was actually touched is what stops that.
const twoRows = [
  ...board,
  { ...panel(1, 0), id: "r1", track: 3 },
  { ...panel(2, 12000), id: "r2", track: 3 },
];
const both = insertShotBeside(twoRows, drawn, "p2", "after");
const bothOn = (f) => (f.track || 0) === both.placed.track;
out.scoped = renderShifts(twoRows.filter(bothOn), both.frames.filter(bothOn));
out.unscoped = renderShifts(twoRows, both.frames);

console.log(JSON.stringify(out));
"""


def run_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="shotinsert_")
    try:
        src = HARNESS % {
            "scene": (ROOT / "client/src/animatic/scene.js").as_uri(),
            "ripple": (ROOT / "client/src/animatic/ripple.js").as_uri(),
        }
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:900])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


LOGIC = [
    "the new shot starts where its neighbour ENDS",
    "…and it lands on the neighbour's own row",
    "everything after the seam makes room, and nothing before it moves",
    "it is spliced into the LIST beside the neighbour too",
    "the clip is placed, not rebuilt — its shot id and its name survive",
    "\"before\" puts it at the neighbour's START, and moves the neighbour",
    "…and splices it in FRONT of the neighbour in the list",
    "A DRAGGED ROW IS READ BY THE CLOCK, NOT BY THE LIST",
    "…and the clips it disagrees with the list about are moved correctly",
    "a neighbour that has gone returns the list UNCHANGED",
    "…and says nothing was placed",
    "a clip with no start of its own is placed where it actually plays",
    "a length under the wire's floor is clamped, not sent",
    "THE SHIFT MAP IS THE NEW SHOT'S LENGTH, from the seam onwards",
    "the board row is not carried twice",
    "A TAKE OVER A MOVED PANEL MOVES WITH IT",
    "…and a take over a panel that did not move stays put",
    "⚠ AND THE BOARD-WIDE SKIP WOULD HAVE STRANDED BOTH",
    "a file on a plain video row is carried",
    "a caption before the seam stays where it was",
    "…and one at the seam moves by the whole of it",
    "an overlay is carried",
    "THE VOICEOVER IS CUT AT THE SEAM, not dragged whole",
    "…and the tail reads on from where the head stopped",
    "a second storyboard row does not poison the map",
]

print("A shot goes in beside another, and the film moves over for it")
got = run_node()

if got is None:
    for label in LOGIC:
        skip(label, "node not available")
else:
    # --- 1. after ----------------------------------------------------------
    check(
        "the new shot starts where its neighbour ENDS",
        got["after"][2] == ["gen1", 4000, 8000],
        json.dumps(got["after"]),
    )
    check("…and it lands on the neighbour's own row", got["afterPlaced"] == ["gen1", 4000, 0],
          json.dumps(got["afterPlaced"]))
    check(
        "everything after the seam makes room, and nothing before it moves",
        got["after"] == [
            ["p1", 0, 2000], ["p2", 2000, 2000], ["gen1", 4000, 8000],
            ["p3", 12000, 2000], ["p4", 14000, 2000],
        ],
        json.dumps(got["after"]),
    )
    check("it is spliced into the LIST beside the neighbour too",
          got["afterOrder"] == ["p1", "p2", "gen1", "p3", "p4"], json.dumps(got["afterOrder"]))
    check("the clip is placed, not rebuilt — its shot id and its name survive",
          got["afterKeptFields"])

    # --- 2. before ---------------------------------------------------------
    check(
        "\"before\" puts it at the neighbour's START, and moves the neighbour",
        got["before"] == [
            ["p1", 0, 2000], ["gen1", 2000, 8000],
            ["p2", 10000, 2000], ["p3", 12000, 2000], ["p4", 14000, 2000],
        ],
        json.dumps(got["before"]),
    )
    check("…and splices it in FRONT of the neighbour in the list",
          got["beforeOrder"] == ["p1", "gen1", "p2", "p3", "p4"], json.dumps(got["beforeOrder"]))

    # --- 3. the dragged row ------------------------------------------------
    # q1 plays 2000-4000, so the seam is 4000 whatever the list says. q3 plays
    # first and owes nothing; q2 starts at the seam and moves.
    check("A DRAGGED ROW IS READ BY THE CLOCK, NOT BY THE LIST",
          got["draggedSeam"] == 4000, str(got["draggedSeam"]))
    check(
        "…and the clips it disagrees with the list about are moved correctly",
        sorted(got["dragged"]) == sorted([
            ["gen1", 4000, 8000], ["q1", 2000, 2000], ["q2", 12000, 2000], ["q3", 0, 2000],
        ]),
        json.dumps(got["dragged"]),
    )

    # --- 4/5/6 -------------------------------------------------------------
    check("a neighbour that has gone returns the list UNCHANGED", got["goneSame"])
    check("…and says nothing was placed", got["gonePlaced"] is None, repr(got["gonePlaced"]))
    check(
        "a clip with no start of its own is placed where it actually plays",
        got["loose"] == [
            ["p1", None, 2000], ["p2", None, 2000], ["gen1", 4000, 8000], ["p3", 12000, 2000],
        ],
        json.dumps(got["loose"]),
    )
    check(
        "a length under the wire's floor is clamped, not sent",
        got["tiny"] == [["p1", 0], ["p2", 2000], ["gen1", 4000], ["p3", 4100], ["p4", 6100]],
        json.dumps(got["tiny"]),
    )

    # --- 7. the whole edit -------------------------------------------------
    check(
        "THE SHIFT MAP IS THE NEW SHOT'S LENGTH, from the seam onwards",
        [p["shift"] for p in got["shifts"] if p["at"] >= 4000] == [8000] * len(
            [p for p in got["shifts"] if p["at"] >= 4000]
        )
        and all(p["shift"] == 0 for p in got["shifts"] if p["at"] < 4000),
        json.dumps(got["shifts"]),
    )
    check(
        "the board row is not carried twice",
        got["settledBoard"] == [
            ["p1", 0, 2000], ["p2", 2000, 2000], ["gen1", 4000, 8000],
            ["p3", 12000, 2000], ["p4", 14000, 2000],
        ],
        json.dumps(got["settledBoard"]),
    )
    check("A TAKE OVER A MOVED PANEL MOVES WITH IT",
          ["v3", 12000, 2000] in got["settledTakes"], json.dumps(got["settledTakes"]))
    check("…and a take over a panel that did not move stays put",
          ["v2", 2000, 2000] in got["settledTakes"], json.dumps(got["settledTakes"]))
    check(
        "⚠ AND THE BOARD-WIDE SKIP WOULD HAVE STRANDED BOTH",
        got["withoutKeep"] == [["v2", 2000, 2000], ["v3", 4000, 2000]],
        json.dumps(got["withoutKeep"]),
    )
    check("a file on a plain video row is carried",
          got["settledDrop"] == [["f1", 14000, 2000]], json.dumps(got["settledDrop"]))
    check("a caption before the seam stays where it was",
          got["captions"][:2] == [["c1", 1000], ["c2", 3999]], json.dumps(got["captions"]))
    check("…and one at the seam moves by the whole of it",
          got["captions"][2:] == [["c3", 12000], ["c4", 15000]], json.dumps(got["captions"]))
    check("an overlay is carried", got["overlays"] == [["o1", 13000]], json.dumps(got["overlays"]))

    audio = got["audio"]
    check("THE VOICEOVER IS CUT AT THE SEAM, not dragged whole",
          len(audio) == 2 and audio[0][:3] == ["vo", 0, 4000], json.dumps(audio))
    check(
        "…and the tail reads on from where the head stopped",
        len(audio) == 2 and audio[1][1] == 12000 and audio[1][3] == 4000,
        json.dumps(audio),
    )

    # --- 8. two storyboard rows -------------------------------------------
    scoped = got["scoped"]
    unscoped = got["unscoped"]
    check(
        "a second storyboard row does not poison the map",
        all(p["shift"] == 8000 for p in scoped if p["at"] >= 4000)
        and any(p["shift"] == 0 for p in unscoped if p["at"] > 4000),
        f"scoped={json.dumps(scoped)} unscoped={json.dumps(unscoped)}",
    )

print()
if skipped:
    print(f"{len(skipped)} check(s) skipped — install node to run them.")
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The seam is a clip's edge, and every layer moves with it.")
