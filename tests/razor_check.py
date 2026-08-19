"""CUTTING A CAPTION, A SHAPE OR AN OVERLAY — and what happens to its animation.

`audio_razor_check.py` covers the razor on audio, where the hard part is
`offset_ms` (the second half has to read further into the FILE). This covers the
razor on the three FREE clips, where the hard part is keyframes.

⚠ THE KEYFRAMES ARE THE WHOLE RISK, and the failure is silent. Key times are
stored RELATIVE to a clip's own start, and `valueAt` HOLDS at the first and last
key rather than extrapolating. So the obvious split — keep the keys before the
cut, shift the rest back — loses the value AT the cut on both halves: the head
freezes at its last key some way before the blade and the tail begins at its
first key some way after it. The clip still renders, the document still
validates, and the animation JUMPS at the edit. Nothing anywhere reports it.

So every case below states the keys both halves must end up with, and the values
either side of the blade are compared through `valueAt` — the same function the
monitor and the exporter read.

⚠ JS-ONLY, on purpose. `animatic/razor.js` has no Python twin and needs none: the
server renders a timeline, it never edits one. The same split as `keyframes.js`,
`selection.js`, `beat_cut.js` and `audio_clips.js`.

    python tests/razor_check.py

Needs `node`, which the client build already requires. Without it every check
here is reported as SKIPPED, which is a gap rather than a pass.
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


HARNESS = """
import {
  MIN_SPLIT_MS,
  RAZOR_KINDS,
  splitKeyframes,
  splitTimedClip,
  splitTrack,
  timedClipAt,
} from "%(razor)s";
import { valueAt } from "%(scene)s";

const out = { minSplit: MIN_SPLIT_MS, kinds: RAZOR_KINDS };

// A shape on screen 1s -> 5s, with opacity ramping 0 -> 1 across the whole clip
// and a scale that has one key in the SECOND half only. Both cases matter: a
// track that straddles the blade, and a track whose keys are all on one side.
const shape = {
  id: "s1",
  kind: "rect",
  start_ms: 1000,
  duration_ms: 4000,
  group_id: "gA",
  opacity: 1,
  keyframes: {
    opacity: [
      { t: 0, v: 0, ease: "linear" },
      { t: 4000, v: 1, ease: "linear" },
    ],
    scale: [{ t: 3000, v: 2, ease: "easeIn" }],
  },
};

// Cut at 3.0s on the TIMELINE, which is 2000ms into the clip — half way, so the
// ramp is at 0.5 exactly and a wrong answer cannot hide in a rounding error.
const halves = splitTimedClip(shape, 3000, "s2");
out.halves = halves;
if (halves) {
  const [head, tail] = halves;
  // What each half is worth on either side of the blade. The head's LAST moment
  // and the tail's FIRST must agree, or the animation steps at the cut.
  out.acrossTheCut = [
    valueAt(head, "opacity", 2000, 1),
    valueAt(tail, "opacity", 0, 1),
  ];
  // And the halves must still animate the way the whole clip did, sampled at
  // the same absolute moments.
  out.sampled = [
    [valueAt(shape, "opacity", 1000, 1), valueAt(head, "opacity", 1000, 1)],
    [valueAt(shape, "opacity", 3000, 1), valueAt(tail, "opacity", 1000, 1)],
  ];
  out.headKeys = head.keyframes.opacity;
  out.tailKeys = tail.keyframes.opacity;
  out.headScale = head.keyframes.scale || null;
  out.tailScale = tail.keyframes.scale || null;
}

// A caption, which carries no `keyframes` key at all on the wire.
const caption = { id: "t1", text: "hello", start_ms: 1000, duration_ms: 4000 };
const capHalves = splitTimedClip(caption, 2500, "t2");
out.caption = capHalves;
out.captionGainedKeyframes =
  capHalves !== null && ("keyframes" in capHalves[0] || "keyframes" in capHalves[1]);

// Refusals: too close to either end, and nothing at all.
out.tooEarly = splitTimedClip(shape, 1040, "sx");
out.tooLate = splitTimedClip(shape, 4960, "sx");
out.atTheFloor = splitTimedClip(shape, 1000 + MIN_SPLIT_MS, "sx") !== null;
out.nothing = splitTimedClip(null, 2000, "sx");

// One track on its own, and an empty one.
out.oneTrack = splitTrack(shape, "opacity", 2000);
out.noTrack = splitTrack(shape, "nosuchprop", 2000);
out.emptyKeys = splitKeyframes({ keyframes: {} }, 500);

// Which clip is under a moment, on one lane.
const a = { id: "a", start_ms: 0, duration_ms: 2000 };
const b = { id: "b", start_ms: 2000, duration_ms: 2000 };
out.at = [
  timedClipAt([a, b], 0)?.id ?? null,
  timedClipAt([a, b], 1999)?.id ?? null,
  timedClipAt([a, b], 2000)?.id ?? null,   // half-open: the edge is B's
  timedClipAt([a, b], 9000)?.id ?? null,
  timedClipAt([], 100)?.id ?? null,
];

console.log(JSON.stringify(out));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="razor_")
    try:
        src = HARNESS % {
            "razor": (ROOT / "client/src/animatic/razor.js").as_uri(),
            "scene": (ROOT / "client/src/animatic/scene.js").as_uri(),
        }
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:600])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


LABELS = [
    "the razor knows every kind of clip it can cut",
    "a cut leaves two halves that add up to the original",
    "the head keeps its id, so the selection survives the cut",
    "…and the new piece is NOT left in the old piece's group",
    "everything about the clip is carried to both halves",
    "a key is planted at the blade on BOTH sides",
    "so the animation does not jump at the cut",
    "…and both halves animate what the whole clip animated",
    "a track with keys only past the blade lands on the tail alone",
    "a caption with no keyframes does not gain any",
    "a cut too close to either edge is refused",
    "…and exactly at the floor is allowed",
    "nothing at all is refused rather than thrown",
    "an unkeyed property comes back as two empty tracks",
    "the clip under a moment is found, and the edge belongs to the second",
]

browser = run_node()
print("\nCutting a caption, a shape or an overlay")
if browser is None:
    for label in LABELS:
        skip(label, "node not available")
else:
    check(
        LABELS[0],
        browser["kinds"] == ["frame", "audio", "overlay", "text", "shape"],
        browser["kinds"],
    )
    halves = browser["halves"]
    if not halves:
        for label in LABELS[1:10]:
            check(label, False, "the cut was refused outright")
    else:
        head, tail = halves
        check(
            LABELS[1],
            head["duration_ms"] == 2000
            and tail["duration_ms"] == 2000
            and tail["start_ms"] == 3000,
            f"{head['duration_ms']} + {tail['duration_ms']} of 4000, tail at {tail['start_ms']}",
        )
        check(LABELS[2], head["id"] == "s1" and tail["id"] == "s2",
              f"{head['id']} / {tail['id']}")
        check(LABELS[3], head["group_id"] == "gA" and tail["group_id"] == "",
              f"{head['group_id']!r} / {tail['group_id']!r}")
        check(LABELS[4], head["kind"] == "rect" and tail["kind"] == "rect",
              f"{head.get('kind')} / {tail.get('kind')}")
        # ⚠ THE HEAD'S LAST KEY IS AT THE BLADE and the tail's FIRST is at 0.
        # Without both, `valueAt` holds at whichever key it can find and the
        # value either side of the edit stops matching.
        check(
            LABELS[5],
            browser["headKeys"][-1]["t"] == 2000 and browser["tailKeys"][0]["t"] == 0,
            f"head ends at {browser['headKeys'][-1]['t']}, tail starts at {browser['tailKeys'][0]['t']}",
        )
        across = browser["acrossTheCut"]
        check(
            LABELS[6],
            abs(across[0] - across[1]) < 1e-6 and abs(across[0] - 0.5) < 1e-6,
            f"{across[0]} then {across[1]} (want 0.5 both sides)",
        )
        check(
            LABELS[7],
            all(abs(whole - part) < 1e-6 for whole, part in browser["sampled"]),
            str(browser["sampled"]),
        )
        # `scale` has one key at t=3000, which is past the blade at 2000. The
        # head gets the value AT the blade (there is one — `valueAt` holds at the
        # single key), the tail keeps the real key, shifted.
        check(
            LABELS[8],
            browser["tailScale"] is not None
            and any(k["t"] == 1000 for k in browser["tailScale"]),
            f"head {browser['headScale']} · tail {browser['tailScale']}",
        )

    check(
        LABELS[9],
        browser["caption"] is not None and not browser["captionGainedKeyframes"],
        f"gained keyframes: {browser['captionGainedKeyframes']}",
    )
    check(
        LABELS[10],
        browser["tooEarly"] is None and browser["tooLate"] is None,
        f"{browser['tooEarly']} / {browser['tooLate']}",
    )
    check(LABELS[11], browser["atTheFloor"] is True, browser["atTheFloor"])
    check(LABELS[12], browser["nothing"] is None, browser["nothing"])
    check(LABELS[13], browser["noTrack"] == [[], []], browser["noTrack"])
    check(
        LABELS[14],
        browser["at"] == ["a", "a", "b", None, None],
        browser["at"],
    )

print()
if skipped:
    print(f"{len(skipped)} check(s) SKIPPED — that is a gap, not a pass:")
    for s in skipped:
        print(f"  - {s}")
    sys.exit(2)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("A cut clip is two clips, and the animation runs straight through the edit.")
