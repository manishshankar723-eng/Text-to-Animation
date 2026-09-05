"""PRESS PLAY AND THE PICTURES MOVE — even when the sound is still loading.

⚠ THE FAILURE THIS PINS HAS NO ERROR AND NO STACK. Every line of the transport
looks right; the playhead simply will not leave 0.

`useTimelineTransport` uses the audio as its master clock, on purpose: the
pictures are placed by reading the playing element's `currentTime` every
animation frame, so a preview can never drift away from its own soundtrack. The
element it picked was "the first one that is not paused". But `el.play()` marks
an element unpaused IMMEDIATELY — the promise it returns has not settled, the
browser has decoded nothing, and `currentTime` is still 0. So the tick read the
time off an element that was not playing yet and got the same answer on every
frame: the playhead pinned to that clip's start.

Reported from the screen the first day eleven sounds landed on a timeline:

    "mai play dala raha tha 0 frame pe to play nhi ho raha tha, but jab timeline
     ka cursor thoda aage se play kiya 2/3 sec se to hone laga"

At 0 the music bed and the first sound effect both start from cold, so there is
nothing decoded to read; two seconds in, an element is already running and the
transport works. And it could not happen before the sound passes existed — a
film with no audio has no master clock and has always run off the wall clock,
which is exactly the fallback that was missing here.

Four properties, and each one is a way the transport used to lie:

  1. A COLD ELEMENT IS NOT A CLOCK. `readyState` below HAVE_CURRENT_DATA means
     "I have decoded nothing", however unpaused it says it is.
  2. A DECODED ONE IS. The moment it can answer, it is the clock again — the
     handover has to work in both directions or playback would run on the wall
     clock forever after one slow start.
  3. A STALL IS CAUGHT. An element that keeps answering the same timestamp is
     buffering; past `CLOCK_STALL_MS` the wall clock takes over.
  4. …AND IT IS GIVEN BACK the instant the timestamp moves again.

    python tests/transport_clock_check.py

Needs node. Nothing here touches a browser, a backend or a model.
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


HARNESS = """
import { CLOCK_STALL_MS, clockRead } from "%(mix)s";

const out = { stall: CLOCK_STALL_MS, steps: [] };
// A fake <audio>: exactly the three properties the clock reads.
const el = { readyState: 0, currentTime: 0 };
let seen = null;
let now = 0;

/** One animation frame. */
function frame(label, ms) {
  now += ms;
  const read = clockRead(el, seen, now);
  seen = read.seen;
  out.steps.push({ label, usable: read.usable, at: el.currentTime, readyState: el.readyState });
}

// ⚠ THE REPORTED SEQUENCE, FRAME BY FRAME.
// `play()` has been called: unpaused, but nothing decoded and time still 0.
frame("cold, just after play()", 16);
frame("still loading", 16);
frame("still loading", 16);
// The browser has data now, and time starts to move.
el.readyState = 4;
el.currentTime = 0.016;
frame("decoded — the clock starts", 16);
el.currentTime = 0.032;
frame("running", 16);
// It buffers mid-play: unpaused, decoded, and stuck on one timestamp.
frame("stalled 16ms", 16);
frame("stalled 200ms", 184);
frame("stalled past the limit", 200);
// …and it recovers.
el.currentTime = 0.05;
frame("moving again", 16);

// A clip whose element has no time at all (never mounted, or NaN).
out.broken = clockRead({ readyState: 4, currentTime: NaN }, null, 1000).usable;
out.missing = clockRead(null, null, 1000).usable;
process.stdout.write(JSON.stringify(out));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="transport-clock-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {"mix": (ROOT / "client/src/animatic/audio_mix.js").as_uri()})
        proc = subprocess.run(
            ["node", harness],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1000])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("\nPRESS PLAY AND THE PICTURES MOVE\n" + "=" * 70)
print(
    "\n⚠ AN UNPAUSED ELEMENT IS NOT A PLAYING ONE. `play()` returns before the\n"
    "  browser has decoded anything, and the transport read the time off it.\n"
)

data = run_node()
if not data:
    print("  skip every check  (node not available)")
    skipped.append("all")
else:
    steps = data["steps"]
    cold = steps[:3]
    check(
        "a cold element is never the clock — the wall clock drives instead",
        all(not s["usable"] for s in cold),
        json.dumps(cold),
    )
    check(
        "…and that is what would have frozen the playhead at 0",
        all(s["at"] == 0 for s in cold),
        json.dumps(cold),
    )
    check(
        "the moment it has decoded, it IS the clock again",
        steps[3]["usable"] and steps[4]["usable"],
        json.dumps(steps[3:5]),
    )
    check(
        "a brief stall is tolerated — correcting every hiccup is audible",
        steps[5]["usable"] and steps[6]["usable"],
        json.dumps(steps[5:7]),
    )
    check(
        f"…but past {data['stall']}ms on one timestamp it stops being the clock",
        not steps[7]["usable"],
        json.dumps(steps[7]),
    )
    check(
        "…and it is handed back the instant the timestamp moves again",
        steps[8]["usable"],
        json.dumps(steps[8]),
    )
    check(
        "an element with no usable time is refused rather than trusted",
        not data["broken"] and not data["missing"],
        json.dumps([data["broken"], data["missing"]]),
    )

print("\n" + "-" * 70)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
else:
    print("All checks passed.")
if skipped:
    print("Skipped — install node to run them.")
sys.exit(1 if failures else 0)
