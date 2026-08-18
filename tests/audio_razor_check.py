"""THE RAZOR ON AUDIO: cutting a clip in two, and trimming what is left.

Until this, an audio track was pinned to the head of the video and the only
edits available were pulling its two ends in — so the one edit anybody actually
wants, taking a pause out of the middle of a take, was impossible. A clip now
carries `start_ms` (where it sits on the timeline) as well as `offset_ms` (how
far into the file it reads), and the razor sets BOTH on the second half.

That pair is the whole feature and it is also the whole risk: set one without
the other and the audio jumps at the cut, which is a mistake that looks like a
rendering bug rather than like arithmetic. So the arithmetic is checked here,
and every case is stated as the two halves it must produce.

⚠ THIS IS JS-ONLY, on purpose. `animatic/audio_clips.js` has no Python twin and
needs none: the server RENDERS a mix, it never edits one, so nothing there has
to know what two halves of a cut look like. The same split as `keyframes.js` —
reading is mirrored in Python, writing is not. What Python DOES have to agree
about (where the clip sits, how long it is heard, where its fades land) is
checked in `audio_mix_check.py`.

    python tests/audio_razor_check.py

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


# A four-second take, untrimmed, at the head of the timeline. Every case below
# starts from this or from something explicitly derived from it.
TAKE = {
    "id": "c1",
    "upload_id": "a1",
    "filename": "take.wav",
    "duration_ms": 4000,
    "start_ms": 0,
    "offset_ms": 0,
    "trim_ms": None,
    "volume": 0.8,
    "muted": False,
    "fade_in_ms": 300,
    "fade_out_ms": 500,
    "eq_low": 3.0,
    "role": "voice",
    # Grouped, so the cut's effect on a group is covered by the cases below.
    "group_id": "gA",
}

HARNESS = """
import {
  audioEndMs,
  clipAliveAt,
  clipAt,
  clipId,
  clipRoomMs,
  laneClips,
  MIN_CLIP_MS,
  splitClip,
  trimClipStart,
} from "%(clips)s";

const take = JSON.parse(process.argv[2]);
const out = {};

// --- A plain cut, half way through -----------------------------------------
out.halves = splitClip(take, 2000, "c2");

// --- A cut on a clip that is ALREADY a piece of something -------------------
// The second half of one cut, cut again. This is the case that catches an
// offset applied from the wrong origin: the numbers have to compound.
const second = splitClip(take, 1000, "c2")[1];
out.second = second;
out.again = splitClip(second, 2500, "c3");

// --- Refusals ---------------------------------------------------------------
out.tooEarly = splitClip(take, 40, "cx");
out.tooLate = splitClip(take, 3980, "cx");
out.exactlyAtTheFloor = splitClip(take, MIN_CLIP_MS, "cx") !== null;
out.nothing = splitClip(null, 1000, "cx");

// --- Identity ---------------------------------------------------------------
out.ids = [
  clipId(take),
  clipId({ upload_id: "u9" }),          // no id: falls back to the upload
  clipId({ id: "", upload_id: "u9" }),  // blank id: same fallback
  clipId(null),
];

// --- Which clip is under a time ---------------------------------------------
const [head, tail] = out.halves;
out.alive = [
  clipAliveAt(head, 0),
  clipAliveAt(head, 1999),
  clipAliveAt(head, 2000),   // half-open: the cut belongs to the TAIL
  clipAliveAt(tail, 2000),
  clipAliveAt(tail, 4000),
];
out.at = [
  clipAt([head, tail], 500)?.id,
  clipAt([head, tail], 2500)?.id,
  clipAt([head, tail], 9000)?.id ?? null,
];

// --- The gap: delete the middle piece ---------------------------------------
// Cut at 1s and 3s, throw the middle away, and what is left is 0–1s and 3–4s
// with nothing in between. This is the edit the whole feature exists for.
const [a, rest] = splitClip(take, 1000, "cA");
const [middle, b] = splitClip(rest, 3000, "cB");
out.gap = { a: [a.start_ms, a.trim_ms], b: [b.start_ms, b.offset_ms, b.trim_ms] };
out.middleWasThere = [middle.start_ms, middle.offset_ms, middle.trim_ms];
out.endAfterDeleting = audioEndMs([a, b]);

// --- Trimming the head of a piece -------------------------------------------
out.trimIn = trimClipStart(tail, 2600);
// Back past the head of the FILE is refused: the tail reads from 2000ms in, so
// it can be pulled back exactly 2000ms and no further.
out.trimWayBack = trimClipStart(tail, -9000);
// And never so far right that nothing is left.
out.trimWayForward = trimClipStart(tail, 99000);

// --- How much file is left ---------------------------------------------------
out.room = [
  clipRoomMs(take),
  clipRoomMs(tail),
  clipRoomMs({ duration_ms: 0 }) === Infinity,
];

// --- Lane grouping ----------------------------------------------------------
out.lane = laneClips(
  [
    { id: "z", layer_id: "", start_ms: 3000 },
    { id: "y", layer_id: "L1", start_ms: 0 },
    { id: "x", layer_id: "", start_ms: 500 },
  ],
  ""
).map((c) => c.id);

console.log(JSON.stringify(out));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="razor_")
    try:
        src = HARNESS % {"clips": (ROOT / "client/src/animatic/audio_clips.js").as_uri()}
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness, json.dumps(TAKE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:600])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


LABELS = [
    "a cut leaves two halves that add up to the original",
    "the head keeps its id, so the selection survives the cut",
    "the tail starts at the cut on the TIMELINE and in the FILE",
    "the fade at each end stays on the end it was on",
    "everything about the sound is carried to both halves",
    "…but the new piece is NOT left in the old piece's group",
    "cutting a piece again compounds the offsets",
    "a cut too close to either edge is refused",
    "…and exactly at the floor is allowed",
    "a clip is known by its id, falling back to its upload",
    "the cut belongs to the second half, not to both",
    "the clip under a time is found, and none past the end",
    "deleting the middle piece leaves a real gap",
    "trimming the head moves three numbers together",
    "…and stops at the head of the file",
    "…and never eats the whole clip",
    "the room left in the file is what is after the offset",
    "a lane's clips come back in play order",
]

browser = run_node()
if browser is None:
    print("\nThe razor's arithmetic")
    for label in LABELS:
        skip(label, "node not available")
else:
    print("\nCutting one clip into two")
    head, tail = browser["halves"]
    check(
        LABELS[0],
        head["trim_ms"] == 2000 and tail["trim_ms"] == 2000,
        f"{head['trim_ms']} + {tail['trim_ms']} of 4000",
    )
    check(
        LABELS[1],
        head["id"] == "c1" and tail["id"] == "c2",
        f"{head['id']} / {tail['id']}",
    )
    check(
        # ⚠ THE ONE THAT MATTERS. Both, or the audio jumps at the cut: the tail
        # has to wait 2000ms on the timeline AND skip 2000ms of the file.
        LABELS[2],
        tail["start_ms"] == 2000 and tail["offset_ms"] == 2000,
        f"start {tail['start_ms']}, offset {tail['offset_ms']}",
    )
    check(
        LABELS[3],
        head["fade_in_ms"] == 300
        and head["fade_out_ms"] == 0
        and tail["fade_in_ms"] == 0
        and tail["fade_out_ms"] == 500,
        f"head {head['fade_in_ms']}/{head['fade_out_ms']}, "
        f"tail {tail['fade_in_ms']}/{tail['fade_out_ms']}",
    )
    check(
        LABELS[4],
        all(
            h["volume"] == 0.8 and h["eq_low"] == 3.0 and h["role"] == "voice"
            and h["upload_id"] == "a1" and h["filename"] == "take.wav"
            for h in (head, tail)
        ),
    )
    # ⚠ The one thing a cut does NOT carry over. Inheriting the group would mean
    # deleting the middle piece deletes every clip grouped with it — i.e. the
    # razor could no longer take a pause out of a grouped clip at all. The head
    # keeps the group, exactly as it keeps its id.
    check(
        LABELS[5],
        head.get("group_id") == "gA" and tail.get("group_id") == "",
        f"head {head.get('group_id')!r}, tail {tail.get('group_id')!r}",
    )

    print("\nCutting a piece that is already a piece")
    second = browser["second"]
    mid, last = browser["again"]
    check(
        LABELS[6],
        # `second` is 1000→4000 of the file, sitting at 1000. Cut it at 2500:
        # the new tail waits 2500 on the timeline and reads from 2500 in.
        second["start_ms"] == 1000
        and second["offset_ms"] == 1000
        and mid["trim_ms"] == 1500
        and last["start_ms"] == 2500
        and last["offset_ms"] == 2500
        and last["trim_ms"] == 1500,
        f"{mid['trim_ms']} then start {last['start_ms']} / offset {last['offset_ms']}"
        f" / {last['trim_ms']}",
    )

    print("\nWhat the razor refuses")
    check(
        LABELS[7],
        browser["tooEarly"] is None
        and browser["tooLate"] is None
        and browser["nothing"] is None,
    )
    check(LABELS[8], browser["exactlyAtTheFloor"] is True)

    print("\nIdentity and hit testing")
    check(
        LABELS[9],
        browser["ids"] == ["c1", "u9", "u9", ""],
        browser["ids"],
    )
    check(
        LABELS[10],
        browser["alive"] == [True, True, False, True, False],
        browser["alive"],
    )
    check(
        LABELS[11],
        browser["at"] == ["c1", "c2", None],
        browser["at"],
    )

    print("\nTaking a gap out of the middle — the edit this all exists for")
    gap = browser["gap"]
    check(
        LABELS[12],
        # 0→1s stays where it is; 3→4s sits at 3s and reads from 3s in. The
        # second no longer plays what was between them, which is the point.
        gap["a"] == [0, 1000]
        and gap["b"] == [3000, 3000, 1000]
        and browser["middleWasThere"] == [1000, 1000, 2000]
        and browser["endAfterDeleting"] == 4000,
        f"a {gap['a']}, b {gap['b']}, end {browser['endAfterDeleting']}",
    )

    print("\nTrimming the head of a piece")
    trim = browser["trimIn"]
    check(
        LABELS[13],
        # The tail sits at 2000 reading from 2000. Pull its head to 2600 and all
        # three move by 600: later on the timeline, later in the file, shorter.
        trim == {"start_ms": 2600, "offset_ms": 2600, "trim_ms": 1400},
        trim,
    )
    check(
        LABELS[14],
        browser["trimWayBack"] == {"start_ms": 0, "offset_ms": 0, "trim_ms": 4000},
        browser["trimWayBack"],
    )
    check(
        LABELS[15],
        browser["trimWayForward"]["trim_ms"] == 100,
        browser["trimWayForward"],
    )

    print("\nHousekeeping")
    check(
        LABELS[16],
        browser["room"] == [4000, 2000, True],
        browser["room"],
    )
    check(LABELS[17], browser["lane"] == ["x", "z"], browser["lane"])

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
print("The razor cuts audio where it is told, and both halves know where they are.")
