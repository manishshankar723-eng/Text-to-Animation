"""A TAKE SHORTER THAN ITS SHOT IS SLOWED TO FILL IT, not left to freeze.

    python tests/veo_speed_fit_check.py

⚠ THE CASE THIS IS ABOUT IS ORDINARY, NOT EXCEPTIONAL. `coverSeconds` asks Veo
for the shortest take that COVERS the hold — but Veo's menu stops at 8 seconds
and the voiceover routinely stretches a shot past that to cover the line spoken
over it. So an 8-second take lands on a 9.8-second shot, runs out, and the film
sits on a frozen frame for the last 1.8 seconds while the dialogue carries on.
Reported as "my clip is 9.8s but the video generated is 8s — adjust the speed of
the video according to the voiceover".

`fitTakeToHold` is the arithmetic, and this drives it under node with no browser.
Four things are checked:

  1. THE FIT ITSELF. 8s over 9.8s plays at 0.8163, and the clip is as long as the
     SHOT rather than as long as the take.
  2. WHAT IT REFUSES, which is most of what it is asked. A take that already
     covers its shot is left alone — the SHOT grows to the take in that
     direction, which is `spreadPanelsForRenders`' job and not this one. A
     difference nobody could see is not worth a write. And a hold more than
     twice its take is not slowed at all, because slowing it that far looks
     worse than the freeze it would be replacing.
  3. THE ARITHMETIC IS THE ONE `sourceAt` USES. `speed` widens the SOURCE window
     read inside a fixed timeline length, so playing `speed × duration` of source
     over the shot has to land exactly on the take's own length — otherwise the
     clip either freezes early or is cut off, which are the two bugs this is
     between.
  4. THE EDITOR ACTUALLY CALLS IT. A pure function nothing is wired to is the
     failure mode this whole file exists to catch, so the JSX is read.

Needs node. Spends nothing and calls nothing.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"
EDITOR = ROOT / "client/src/components/AnimaticEditor.jsx"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


HARNESS = """
import { fitTakeToHold } from "__VEO__";

const ask = (take, hold) => ({ take, hold, got: fitTakeToHold(take, hold) });

process.stdout.write(JSON.stringify({
  // The reported case: the voiceover stretched the shot to 9.8s and Veo's
  // longest take is 8s.
  reported: ask(8000, 9800),
  // A 4s take on a 6s hold — the same shape, one size down.
  smaller: ask(4000, 6000),
  // Right on the floor and just past it: 2x is the last fit, 2.1x is refused.
  atFloor: ask(4000, 8000),
  pastFloor: ask(4000, 8400),
  // The ordinary direction: the take covers the hold, so nothing to do here.
  covers: ask(8000, 6000),
  exact: ask(6000, 6000),
  // Too small to see.
  hair: ask(8000, 8100),
  // Nothing to reason about.
  noTake: ask(0, 9800),
  noHold: ask(8000, 0),
  junk: ask(null, undefined),
}));
"""


def run_node():
    work = tempfile.mkdtemp(prefix="veo-fit-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS.replace("__VEO__", (AGENT / "veo_pass.js").as_uri()))
        proc = subprocess.run(
            ["node", harness],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1500])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    data = run_node()
    if data is None:
        print("  node is not on PATH, or veo_pass.js would not load — nothing checked.")
        return 1

    print("\nTHE FIT — an 8s take over a 9.8s shot\n")
    got = data["reported"]["got"]
    check("a take shorter than its shot is slowed rather than frozen", bool(got))
    if got:
        check("...to 0.8163, which is 8.0 / 9.8", got["speed"] == 0.8163, str(got))
        check("⚠ AND THE CLIP IS AS LONG AS THE SHOT, not as long as the take",
              got["durationMs"] == 9800, str(got))
        check("...and it says why, in the lengths the user can see on the timeline",
              "8.0s" in got["why"] and "9.8s" in got["why"], got["why"])
        # ⚠ THE IDENTITY THAT MATTERS. `sourceAt` reads `in + tRel * speed`, so
        # over the whole shot the source consumed must be exactly the take. A
        # rounding error either way is a clip that freezes early or is cut off.
        used = got["speed"] * got["durationMs"]
        check("⚠ PLAYING THE SHOT CONSUMES EXACTLY THE TAKE — `sourceAt`'s own sum",
              abs(used - 8000) <= 2, f"consumed {used:.1f}ms of an 8000ms take")

    smaller = data["smaller"]["got"]
    check("the same holds one size down — a 4s take over 6s runs at 0.6667",
          smaller and smaller["speed"] == 0.6667, str(smaller))

    print("\nWHAT IT REFUSES, WHICH IS MOST OF WHAT IT IS ASKED\n")
    check("⚠ A TAKE THAT ALREADY COVERS ITS SHOT IS LEFT ALONE — the SHOT grows"
          " to the take in that direction, which is not this function's job",
          data["covers"]["got"] is None, str(data["covers"]["got"]))
    check("...and one that matches exactly is not touched either",
          data["exact"]["got"] is None, str(data["exact"]["got"]))
    check("a difference nobody could see is not worth a write",
          data["hair"]["got"] is None, str(data["hair"]["got"]))
    check("⚠ A HOLD MORE THAN TWICE ITS TAKE IS NOT SLOWED AT ALL — that far"
          " down it reads as slow motion nobody asked for",
          data["pastFloor"]["got"] is None, str(data["pastFloor"]["got"]))
    check("...but exactly twice is still fitted, at the floor",
          data["atFloor"]["got"] and data["atFloor"]["got"]["speed"] == 0.5,
          str(data["atFloor"]["got"]))
    check("no take, no hold and junk are all None rather than a crash",
          all(data[k]["got"] is None for k in ("noTake", "noHold", "junk")))

    print("\nTHE EDITOR IS WIRED TO IT — a pure function nothing calls is the bug\n")
    src = EDITOR.read_text(encoding="utf-8", errors="replace")
    check("`fitTakeToHold` is imported from the Veo pass",
          re.search(r"import\s*\{[^}]*fitTakeToHold[^}]*\}\s*from\s*[\"'][^\"']*veo_pass",
                    src) is not None)
    attach = src.split("const attachVeoClip", 1)
    check("...and it is `attachVeoClip` that calls it — the one path a take"
          " reaches the timeline by",
          len(attach) > 1 and "fitTakeToHold(" in attach[1][:3000])
    check("...and what it decides is written onto the clip as `speed`",
          len(attach) > 1 and re.search(r"speed:\s*fit\.speed", attach[1][:3000]) is not None)

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("A take that cannot cover its shot is slowed to fit it, and nothing else is.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
