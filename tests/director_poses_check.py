"""🖼 ANIMATIC IMAGES IS A 🎬 TICK BOX — and the two must never quote one pass at
two numbers, nor buy a drawing nobody will ever see.

    python tests/director_poses_check.py

⚠ THIS IS A TEST ABOUT QUOTA. Phase C2 buys FOUR DRAWINGS PER SECOND of film out
of the account's image allowance — a 32-second animatic is 128 pictures, a
three-minute one is over seven hundred — and unlike the Veo pass there is no
dollar figure on screen to make a user cautious. So the count the button says is
the only guard there is, and it has to be exactly the count that gets drawn.

Five things are checked, in order of how much they matter:

  1. ⚠ A KEY POSE IS NOT A SHOT, AND `shotRow` HAS TO DROP IT. This is the one
     that would have been silently catastrophic: a pose run is four clips per
     SECOND, so an 8-panel film blocked out hands the Director 136 "shots". It
     does not merely inflate the count the way a Veo take does — it drowns it,
     and every rule downstream that counts shots (the median hold, "shot 61",
     the preview table) reads a film that does not exist. It was already wrong
     before this feature: anyone who pressed 🖼 and then 🎬 hit it.
  2. ⚠ A SHOT VEO IS RENDERING IS NOT BLOCKED OUT. `board_video` sits ABOVE
     `board_poses`, so drawings under a take are pictures nobody sees, bought
     with quota, on a run that also paid for the footage. Dropped BY NAME, with
     the reason, rather than by a count that quietly shrank.
  3. ⚠ "ALREADY DRAWN" CHANGES THE PRICE AND NOT THE WORK. A shot whose poses
     are on the storyboard still goes through the queue — the drawings have to
     be LAID on this timeline — but it is not charged for again. `toDraw`, never
     `drawings`, is the number that may sit next to the word "images".
  4. THE TALLY'S SHOT COUNT IS CALLED `count`. Every caller spreads it over an
     object holding the shot LIST under `shots`; a collision there replaces an
     array with a number and nothing throws — the pass simply finds no work.
  5. IS IT DUE, AND WHY NOT. Three different noes, three different sentences,
     because the panel prints them verbatim under the tick box.

Needs node. Nothing is generated, nothing is called, and this suite spends
neither money nor one image of quota.
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
AGENT = ROOT / "client/src/animatic/agent"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


HARNESS = """
import { POSES_KEY, isPose, poseTally, poseWork, posesDue } from "__POSES__";
import { shotRow, isTake } from "__VEO__";

/**
 * A picture row as the editor actually builds one: the board's panels, then any
 * Veo takes (`attachVeoClip` appends), then any key poses (`placePoses` appends).
 * Both of the last two are on ROWS of their own, so neither ever sits between
 * two panels in the list.
 */
function timeline(lengths, { takes = [], blocked = [] } = {}) {
  const frames = [];
  const starts = [];
  let at = 0;
  lengths.forEach((ms, i) => {
    frames.push({
      id: `f${i + 1}`,
      kind: "image",
      duration_ms: ms,
      label: `Shot ${i + 1}`,
      src: { kind: "panel", storyboard_id: "b1", index: i },
    });
    starts.push(at);
    at += ms;
  });
  for (const n of takes) {
    frames.push({
      id: `t${n}`,
      kind: "video",
      duration_ms: 8000,
      label: `Shot ${n}`,
      src: { kind: "video", storyboard_id: "b1", index: n - 1, upload_id: `up${n}` },
    });
    starts.push(starts[n - 1]);
  }
  // FOUR DRAWINGS PER SECOND, which is the number that makes this dangerous.
  for (const n of blocked) {
    const count = Math.round((lengths[n - 1] / 1000) * 4);
    for (let k = 0; k < count; k += 1) {
      frames.push({
        id: `p${n}_${k}`,
        kind: "image",
        duration_ms: Math.round(lengths[n - 1] / count),
        label: `Shot ${n} - ${k + 1}`,
        src: { kind: "pose", storyboard_id: "b1", index: n - 1, frame: k },
      });
      starts.push(starts[n - 1] + k * Math.round(lengths[n - 1] / count));
    }
  }
  return { frames, starts };
}

/** The editor's `posesShots` shape, which is what `readPoses` hands over. */
function shot(n, holdMs, have = 0) {
  const seconds = Math.max(2, Math.round(holdMs / 1000 / 2) * 2);
  return {
    frameId: `f${n}`,
    boardId: "b1",
    index: n - 1,
    label: `Shot ${n}`,
    startMs: 0,
    holdMs,
    seconds,
    poses: seconds * 4,
    have,
  };
}

const out = {};
out.key = POSES_KEY;

// ------------------------------------------ 1. a key pose is not a shot
out.isPose = {
  pose: isPose({ kind: "image", src: { kind: "pose", storyboard_id: "b1" } }),
  panel: isPose({ kind: "image", src: { kind: "panel", storyboard_id: "b1" } }),
  take: isPose({ kind: "video", src: { kind: "video", storyboard_id: "b1" } }),
  dropped: isPose({ kind: "image", src: { kind: "upload", upload_id: "u1" } }),
  nothing: isPose(null),
};

// An 8-panel film, every shot blocked out. 8 panels + 4/sec of 32s = 136 clips.
const lengths = [4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000];
const heavy = timeline(lengths, { blocked: [1, 2, 3, 4, 5, 6, 7, 8] });
const heavyRow = shotRow(heavy.frames, heavy.starts);
out.drown = {
  raw: heavy.frames.length,
  counted: heavyRow.frames.length,
  ids: heavyRow.frames.map((f) => f.id),
  starts: heavyRow.starts,
};

// BOTH kinds of derived picture at once, which is the real state of a film that
// has been through 🎬 twice.
const both = timeline([2000, 3000, 4000], { takes: [1], blocked: [3] });
const bothRow = shotRow(both.frames, both.starts);
out.both = {
  raw: both.frames.length,
  counted: bothRow.frames.length,
  ids: bothRow.frames.map((f) => f.id),
  // ⚠ `starts` IS FILTERED AT THE SAME INDICES, never recomputed.
  starts: bothRow.starts,
  takes: both.frames.filter(isTake).length,
  poses: both.frames.filter(isPose).length,
};

// A film with neither comes back as the SAME arrays, so a caller can tell.
const plain = timeline([2000, 2000]);
const plainRow = shotRow(plain.frames, plain.starts);
out.identity =
  plainRow.frames === plain.frames && plainRow.starts === plain.starts;

// ------------------------------------- 2. the shots Veo is taking are dropped
const work = poseWork({
  shots: [shot(1, 2000), shot(2, 4000), shot(3, 2000)],
  rendered: new Set(["f2"]),
});
out.work = {
  kept: work.shots.map((s) => s.frameId),
  skipped: work.skipped.map((s) => ({ label: s.label, why: s.why })),
};
out.workNone = poseWork({ shots: [shot(1, 2000)] }).shots.length;
out.workAll = poseWork({
  shots: [shot(1, 2000), shot(2, 2000)],
  rendered: ["f1", "f2"],
}).shots.length;
// An array is accepted as well as a Set — the runner builds one of each.
out.workArray = poseWork({ shots: [shot(1, 2000)], rendered: ["f1"] }).shots.length;

// -------------------------------- 3 and 4. the tally, and what it is called
const tally = poseTally([shot(1, 2000), shot(2, 4000, 8), shot(3, 6000)]);
out.tally = tally;
out.tallyKeys = Object.keys(tally).sort();
out.tallyEmpty = poseTally([]);
// ⚠ ALREADY DRAWN CANNOT PUSH THE PRICE BELOW ZERO, however odd the board's
// answer is — a shot with more drawings on the board than the hold now asks for
// is an ordinary thing after the shot has been shortened.
out.tallyOver = poseTally([shot(1, 2000, 99)]).toDraw;
// THE COLLISION THIS NAMING EXISTS TO PREVENT.
const spread = { ...poseWork({ shots: [shot(1, 2000), shot(2, 2000)] }), ...tally };
out.spread = { shotsIsArray: Array.isArray(spread.shots), count: spread.count };

// --------------------------------------------------- 5. is it due, and why not
out.due = {
  on: posesDue({ poses: true }, [shot(1, 2000)]),
  off: posesDue({ poses: false }, [shot(1, 2000)]),
  nothing: posesDue({ poses: true }, []),
  bare: posesDue(undefined, [shot(1, 2000)]),
};

process.stdout.write(JSON.stringify(out));
"""


def run_node():
    work = tempfile.mkdtemp(prefix="dir-poses-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS.replace("__POSES__", (AGENT / "poses_pass.js").as_uri()).replace(
                    "__VEO__", (AGENT / "veo_pass.js").as_uri()
                )
            )
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


def main():
    data = run_node()
    if data is None:
        print("  node is not on PATH, or poses_pass.js would not load — nothing checked.")
        return 2

    check("the include flag is `poses`", data["key"] == "poses", data["key"])

    print("\n⚠ A KEY POSE IS NOT A SHOT — a pose run is FOUR CLIPS PER SECOND, so\n"
          "  a blocked-out film does not merely inflate the shot count, it drowns it\n")
    p = data["isPose"]
    check("a `pose` clip is one", p["pose"] is True)
    check("...a board PANEL is not", p["panel"] is False)
    check("...a Veo take is not (that is `isTake`'s job)", p["take"] is False)
    check("...an uploaded still is not", p["dropped"] is False)
    check("...and nothing at all is not", p["nothing"] is False)

    d = data["drown"]
    check("⚠ 8 PANELS BLOCKED OUT IS 136 CLIPS ON THE PICTURE LIST",
          d["raw"] == 136, str(d["raw"]))
    check("⚠ ...AND THE DIRECTOR COUNTS EIGHT SHOTS, NOT 136 — without this the\n"
          "       median hold, `shotIndex` and the preview table are all about a\n"
          "       film that does not exist",
          d["counted"] == 8, str(d["counted"]))
    check("...and they are the eight PANELS, in film order",
          d["ids"] == [f"f{i}" for i in range(1, 9)], json.dumps(d["ids"][:3]))
    check("...with their own starts, filtered at the same indices",
          d["starts"] == [0, 4000, 8000, 12000, 16000, 20000, 24000, 28000],
          json.dumps(d["starts"]))

    b = data["both"]
    check("⚠ A FILM WITH BOTH A TAKE AND A POSE RUN LOSES BOTH", b["counted"] == 3,
          f"{b['raw']} raw, {b['takes']} takes, {b['poses']} poses")
    check("...and keeps exactly the three panels", b["ids"] == ["f1", "f2", "f3"],
          json.dumps(b["ids"]))
    check("...and their starts are the panels' own", b["starts"] == [0, 2000, 5000],
          json.dumps(b["starts"]))
    check("⚠ A FILM WITH NEITHER IS UNTOUCHED — the SAME arrays come back, so a\n"
          "       caller can tell whether this did anything",
          data["identity"] is True)

    print("\n⚠ A SHOT VEO IS RENDERING IS NOT BLOCKED OUT — `board_video` sits above\n"
          "  `board_poses`, so those drawings are quota spent on pictures nobody sees\n")
    w = data["work"]
    check("⚠ THE RENDERED SHOT IS DROPPED", w["kept"] == ["f1", "f3"],
          json.dumps(w["kept"]))
    check("⚠ ...BY NAME, not by a count that quietly shrank",
          len(w["skipped"]) == 1 and w["skipped"][0]["label"] == "Shot 2",
          json.dumps(w["skipped"]))
    check("...and the reason says the take sits over the drawings",
          "take" in w["skipped"][0]["why"] and "Veo" in w["skipped"][0]["why"],
          w["skipped"][0]["why"])
    check("nothing rendered means nothing dropped", data["workNone"] == 1,
          str(data["workNone"]))
    check("everything rendered means nothing left to draw", data["workAll"] == 0,
          str(data["workAll"]))
    check("a plain array is accepted as well as a Set", data["workArray"] == 0,
          str(data["workArray"]))

    print("\n⚠ ALREADY DRAWN CHANGES THE PRICE AND NOT THE WORK — the pass resumes\n"
          "  onto the storyboard, so those drawings are not paid for twice\n")
    t = data["tally"]
    check("2s + 4s + 6s at four a second is 8 + 16 + 24 = 48 drawings",
          t["drawings"] == 48, str(t["drawings"]))
    check("eight of them are already on the board", t["already"] == 8, str(t["already"]))
    check("⚠ SO `toDraw` IS 40, AND THAT IS THE ONLY NUMBER THE BUTTON MAY SAY",
          t["toDraw"] == 40, str(t["toDraw"]))
    check("...the shots are still all three — 'already drawn' is not a skip",
          t["count"] == 3, str(t["count"]))
    check("⚠ THE SHOT COUNT IS `count`, NOT `shots` — a collision there would\n"
          "       replace an array of shots with a number and nothing would throw",
          data["tallyKeys"] == ["already", "count", "drawings", "toDraw"],
          json.dumps(data["tallyKeys"]))
    check("...which is exactly what spreading it over the work does NOT do",
          data["spread"]["shotsIsArray"] is True and data["spread"]["count"] == 3,
          json.dumps(data["spread"]))
    check("an empty film tallies to zeroes, not to NaN",
          data["tallyEmpty"] == {"count": 0, "drawings": 0, "already": 0, "toDraw": 0},
          json.dumps(data["tallyEmpty"]))
    check("⚠ MORE ON THE BOARD THAN THE HOLD NOW ASKS FOR IS 0 TO DRAW, never a\n"
          "       negative — an ordinary state after a shot has been shortened",
          data["tallyOver"] == 0, str(data["tallyOver"]))

    print("\nIS IT DUE, AND IF NOT, WHY NOT — the panel prints these verbatim\n")
    due = data["due"]
    check("⚠ TICKED, WITH SHOTS TO DRAW, IT IS DUE", due["on"]["due"] is True)
    check("un-ticked it is not, and says so rather than saying there is nothing",
          due["off"]["due"] is False and "switched off" in due["off"]["why"],
          due["off"]["why"])
    check("⚠ ...AND NOTHING LEFT TO BLOCK OUT IS A DIFFERENT SENTENCE — this is\n"
          "       what a user sees when Veo took every shot",
          due["nothing"]["due"] is False and "switched off" not in due["nothing"]["why"],
          due["nothing"]["why"])
    check("...which says a key pose needs a panel behind it",
          "panel" in due["nothing"]["why"], due["nothing"]["why"])
    check("no include object at all is treated as ticked, like the other passes",
          due["bare"]["due"] is True)

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for name in failures:
            print("  -", name)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
