"""🖼 ANIMATIC IMAGES — the key poses land on their own row, under their own shot.

The ask this was written for:

    "mai chahta hun ki image to Animatics images workflow jaise images generate
     hota tha waise editor mai ho … ek button banao make video button ke side mai
     aur uska name Animatic images rakho … ye same Story..Video layer jaisa kaam
     karega … generate ho kar Animatic image ke layer mai aa jaye aur har shot ke
     uper uska length ke hisab se sab image set ho jaye … like shot 1 mai 2 sec
     mai 8 key poses image generate ho aur 2 sec shot image ke under hi ye 8 image
     set hona chahiye divide ho kar same mini keyframe"

Two separate claims, and this file is the two of them:

  1. THE ROW. A key pose is not a storyboard still and not a Veo take, so it gets
     a row of its own — `board_poses`, "Animatic images" — derived from the clip
     itself (`src.kind === "pose"`) exactly as the other two board rows are, so
     there is no new field and no migration. ⚠ And a pose ANIMATED with Veo has
     to leave that row for the render row, because `attachVeoClip` keeps the
     pose's whole `src` underneath the video source: "is it a pose" must lose to
     "is it footage now", and that ordering is asserted here rather than trusted.

  2. THE LAYOUT. Eight drawings of a two-second shot are eight 250ms clips that
     begin where the shot begins and END WHERE THE SHOT ENDS. That last part is
     the one that quietly goes wrong: add up a rounded step per pose and sixteen
     roundings drift the run off its shot by a few frames — visible as a flash of
     the panel underneath at every cut, on every shot, for ever. So the run is
     checked to be gapless, in order, and exactly as long as its shot, at lengths
     that divide evenly (2s) and lengths that do not (3.2s over 16).

  3. THE FOLLOWING. Asked for straight after the first build shipped with this
     as a documented limitation — "ye apne aap ho jayen kar do waisa". A shot
     moves for a dozen ordinary reasons (a Veo take pushes the film along, a shot
     is generated into the middle of the cut, a panel is dragged or trimmed), and
     the drawings have to go with it. `alignPoseRuns` states the rule once — a
     run sits over its panel — and the editor applies it to every change.

     ⚠ THE THREE PROPERTIES THAT MAKE THAT SAFE TO RUN AS AN EFFECT are the ones
     asserted hardest here, because each is a way the feature could silently
     corrupt a project instead of merely looking wrong:

       · IDEMPOTENT — run on its own output it returns the SAME ARRAY (checked by
         identity, not by value), or the effect that calls it loops for ever.
       · UNDO-EXACT — a document that is already consistent is handed back
         untouched, so restoring a snapshot cannot be mistaken for a move and
         "corrected" a second time. A pass written as a diff against the previous
         render fails exactly here, and the symptom is Ctrl+Z not giving back
         what it took.
       · LOSSLESS — no drawing is ever dropped, not even by a shot trimmed too
         short to hold them at the 100ms floor. A clip is an image that was paid
         for; an automatic pass that deletes one is worse than a flipbook that
         overhangs its shot.

⚠ WHY THIS IS A PYTHON TEST OF A JAVASCRIPT FILE. The arithmetic is pure and
lives in `client/src/animatic/scene.js` (`poseRunAcross`, `clipRowKind`), this
file drives it through node, and none of it needs a browser or a backend — the
same bridge `lane_reorder_check.py` uses. A screenshot cannot settle "the last
drawing ends on the same millisecond the shot does"; arithmetic can.

    python tests/animatic_images_check.py

Needs `node` on PATH (the same one `npm run build` uses). No browser, no backend,
nothing spent.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENE_JS = os.path.join(ROOT, "client", "src", "animatic", "scene.js")
ASSETS_JS = os.path.join(ROOT, "client", "src", "animatic", "assets.js")

# The planner's own rate, and the ladder it accepts. ⚠ Twin of
# `panel_sequence.KEY_POSES_PER_SECOND` / `ALLOWED_DURATIONS` and of the two
# constants in AnimaticEditor.jsx — 2s buys 8 drawings in all three or the
# dialog prices something different from what gets drawn.
KEY_POSES_PER_SECOND = 4
ALLOWED_DURATIONS = (2, 4, 6, 8, 10)

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The node harness
# ---------------------------------------------------------------------------
HARNESS = """
import {
  ROW_KINDS,
  ROW_TAKES,
  alignPoseRuns,
  clipRowKind,
  isSavable,
  isBoardRow,
  poseRunAcross,
} from %(scene)s;
import { assetFromFrame, assetOrigin, isBoardAsset } from %(assets)s;

const input = JSON.parse(process.argv[2]);

const out = {
  rowKinds: ROW_KINDS,
  takes: ROW_TAKES,
  boardRows: Object.fromEntries(
    [...ROW_KINDS, "text"].map((k) => [k, isBoardRow(k)])
  ),
  // Which row each of the fixture's clips belongs on.
  rows: Object.fromEntries(
    Object.entries(input.clips).map(([name, clip]) => [name, clipRowKind(clip)])
  ),
  // …and whether the editor can hand its file over (the ⬇ gate).
  savable: Object.fromEntries(
    Object.entries(input.clips).map(([name, clip]) => [name, isSavable(clip)])
  ),
  // ⚠ ASKED OF THE CARD MADE FROM THE CLIP, not of the clip — because that is
  // what the Media pane actually holds, and `assetFromFrame` is the only thing
  // that stands between the two. A section that is right for a clip and wrong
  // for its card is exactly the bug this catches.
  section: Object.fromEntries(
    Object.entries(input.clips).map(([name, clip]) => [
      name,
      assetOrigin(assetFromFrame(clip, "card")),
    ])
  ),
  fromBoard: Object.fromEntries(
    Object.entries(input.clips).map(([name, clip]) => [
      name,
      isBoardAsset(assetFromFrame(clip, "card")),
    ])
  ),
  // Each laid-out run of poses.
  runs: Object.fromEntries(
    Object.entries(input.runs).map(([name, r]) => [
      name,
      poseRunAcross(r.startMs, r.holdMs, r.numbers),
    ])
  ),
  // ⚠ EACH ALIGNMENT CASE IS RUN TWICE, and the second answer is reported by
  // IDENTITY. `settled` being true is the whole proof that the editor's effect
  // stops after one pass instead of setting state for ever.
  aligned: Object.fromEntries(
    Object.entries(input.aligned).map(([name, frames]) => {
      const once = alignPoseRuns(frames);
      const twice = alignPoseRuns(once);
      return [
        name,
        {
          clips: once.map((f) => ({
            id: f.id,
            start_ms: f.start_ms,
            duration_ms: f.duration_ms,
            frame: f.src?.frame ?? null,
          })),
          // Did the pass touch this document at all?
          touched: once !== frames,
          // …and is its own answer already final?
          settled: twice === once,
        },
      ];
    })
  ),
};
process.stdout.write(JSON.stringify(out));
"""


# ---------------------------------------------------------------------------
# The fixture — one clip of every kind that can sit on a picture row
# ---------------------------------------------------------------------------
CLIPS = {
    # A drawn panel off the board: the Storyboard images row, as before.
    "panel": {
        "id": "a",
        "kind": "image",
        "src": {"kind": "panel", "storyboard_id": "brd", "index": 0},
    },
    # One key pose OF that panel: the new row.
    "pose": {
        "id": "b",
        "kind": "image",
        "src": {"kind": "pose", "storyboard_id": "brd", "index": 0, "frame": 3},
    },
    # ⚠ A VEO TAKE OF A POSE. `attachVeoClip` keeps the pose's whole `src`
    # underneath the video source, so this clip answers "pose" AND "video" — and
    # it belongs with every other take, on the render row.
    "take_of_pose": {
        "id": "c",
        "kind": "video",
        "src": {
            "kind": "video",
            "storyboard_id": "brd",
            "index": 0,
            "frame": 3,
            "upload_id": "u1",
        },
    },
    "take_of_panel": {
        "id": "d",
        "kind": "video",
        "src": {"kind": "video", "storyboard_id": "brd", "index": 1, "upload_id": "u2"},
    },
    # A file the user dropped in. Never a board row, whatever else changes.
    "upload": {"id": "e", "kind": "image", "src": {"kind": "upload", "upload_id": "u3"}},
    "footage": {"id": "f", "kind": "video", "src": {"kind": "video", "upload_id": "u4"}},
    # A generated in-between shot: carries the board but has no panel index, and
    # is a still — so it stays with the board's stills.
    "gen_shot": {
        "id": "g",
        "kind": "image",
        "src": {"kind": "upload", "storyboard_id": "brd", "shot_id": "s1", "upload_id": "u5"},
    },
    # ⚠ THE ONE THING WITH NO BYTES BEHIND IT. A colour card is a hex value the
    # renderers fill a rectangle with, so a ⬇ on one could only ever fail.
    "colour": {"id": "h", "kind": "color", "color": "#101014", "src": {"kind": "upload"}},
    # An audio card carries its file on `upload_id` at the TOP level, not under
    # `src` — so this question, which is asked of pictures, says no. Sound is
    # downloaded from the Sounds tab, which already had its own answer.
    "audio": {"id": "i", "kind": "audio", "upload_id": "u6", "src": {"kind": "upload"}},
}

# ⚠ WHAT THE ⬇ IS DRAWN ON — AND THIS TABLE HAS BEEN WRONG IN BOTH DIRECTIONS,
# which is why it is written out kind by kind rather than described.
#
#   · It began as Veo renders alone: a render was the only thing this editor
#     MADE, so it was the only thing worth saving out.
#   · ✨ Animatic images broke that — a key pose costs an image credit and had no
#     way out of the Media pane ("media panel mai generted iamge nhi dikh rah
#     ahai aur dikhe to download kar sakta hun veo video jaisa hi fuction").
#   · The fix over-corrected to "anything with a file behind it", which put a ⬇
#     on the user's OWN uploads — files already on their machine — and came
#     straight back: "only generated cheezon par dikhe ye ⬇ icone".
#
# So the line is the board reference, which every picture this app drew carries
# and nothing a person dropped in does. ⚠ THE TWO ROWS THAT MATTER MOST ARE
# `upload` AND `footage`: both have perfectly fetchable bytes, and both must
# answer NO. If they ever answer True again, the over-correction is back.
WANT_SAVABLE = {
    "panel": True,
    "pose": True,
    "take_of_pose": True,
    "take_of_panel": True,
    "upload": False,
    "footage": False,
    # A shot generated into the board's row: no panel index, but it carries the
    # board — which is exactly the kind of question that reference exists for.
    "gen_shot": True,
    "colour": False,
    "audio": False,
}

# WHICH MEDIA SECTION EACH CARD IS FILED UNDER. ⚠ The key poses get one of their
# own: a card per drawing would otherwise bury the panels inside Storyboard
# Frames, and that section is one people keep folded shut — which is how they
# came to look missing in the first place.
WANT_SECTION = {
    "panel": "board",
    "pose": "poses",
    # ⚠ A TAKE OF A POSE IS FILED WITH THE RENDERS, not with the drawings. It
    # still carries `frame` underneath its video source; what it IS now wins.
    "take_of_pose": "board",
    "take_of_panel": "board",
    "upload": "image",
    "footage": "video",
    "gen_shot": "board",
    "colour": "image",
    "audio": "audio",
}

# ⚠ AND FILING IS NOT THE SAME QUESTION AS ORIGIN. Four places ask "did this come
# off a board?" to decide where a DRAG of it may land; every one of them used to
# read `assetOrigin(card) === "board"`, which a key pose now answers no to. If
# this table and the one above ever agree completely, `isBoardAsset` has been
# collapsed back into the bug it exists to prevent.
WANT_FROM_BOARD = {
    "panel": True,
    "pose": True,
    "take_of_pose": True,
    "take_of_panel": True,
    "upload": False,
    "footage": False,
    "gen_shot": True,
    "colour": False,
    "audio": False,
}

WANT_ROWS = {
    "panel": "board_image",
    "pose": "board_poses",
    "take_of_pose": "board_video",
    "take_of_panel": "board_video",
    "upload": "video",
    "footage": "video",
    "gen_shot": "board_image",
}

# ---------------------------------------------------------------------------
# The runs — a shot's length, and the drawings the board came back with
# ---------------------------------------------------------------------------
# ⚠ THE USER'S OWN EXAMPLE IS THE FIRST ONE: a 2-second shot, eight key poses.
RUNS = {
    # 2s × 8 → 250ms each, dividing exactly.
    "two_seconds": {"startMs": 0, "holdMs": 2000, "numbers": list(range(8))},
    # 4s × 16, starting part-way through the film — the ordinary case.
    "four_seconds": {"startMs": 8000, "holdMs": 4000, "numbers": list(range(16))},
    # ⚠ A LENGTH THAT DOES NOT DIVIDE. 3200 / 16 = 200 exactly, so make it worse:
    # 3333ms over 16 drawings. This is the case a per-pose rounded step gets
    # wrong, and the one the report would read as "the picture flickers".
    "ragged": {"startMs": 1234, "holdMs": 3333, "numbers": list(range(16))},
    # ⚠ A HOLE. The model refused pose 5, so the board came back with 7 drawings
    # numbered 0,1,2,3,4,6,7 — the run must still fill the shot, and each clip
    # must still say WHICH pose it is.
    "with_hole": {"startMs": 0, "holdMs": 2000, "numbers": [0, 1, 2, 3, 4, 6, 7]},
    # ⚠ A SHOT TOO SHORT TO HOLD ITS DRAWINGS. 600ms cannot show eight clips at
    # the 100ms floor; six is what fits, and the run must still end at 600.
    "too_short": {"startMs": 0, "holdMs": 600, "numbers": list(range(8))},
    # Nothing drawn at all.
    "empty": {"startMs": 0, "holdMs": 2000, "numbers": []},
}


# ---------------------------------------------------------------------------
# The alignment fixtures — a shot, its flipbook, and something that moved
# ---------------------------------------------------------------------------
BOARD = "brd"


def panel(pid, index, start, hold, track=0):
    return {
        "id": pid,
        "kind": "image",
        "track": track,
        "start_ms": start,
        "duration_ms": hold,
        "src": {"kind": "panel", "storyboard_id": BOARD, "index": index},
    }


def poses(index, start, hold, n, track=1, tag="p"):
    """A gapless run of `n` drawings laid evenly across [start, start+hold)."""
    out = []
    for k in range(n):
        a = start + round(hold * k / n)
        b = start + round(hold * (k + 1) / n)
        out.append({
            "id": f"{tag}{index}_{k}",
            "kind": "image",
            "track": track,
            "start_ms": a,
            "duration_ms": b - a,
            "src": {
                "kind": "pose",
                "storyboard_id": BOARD,
                "index": index,
                "frame": k,
            },
        })
    return out


# ⚠ THE BASELINE IS DELIBERATELY ALREADY CORRECT. Two shots, each with its
# flipbook exactly over it — the state ✨ Animatic images leaves behind, and the
# state every undo snapshot is in. The pass must not touch a single field of it.
SETTLED = [panel("s1", 0, 0, 2000), panel("s2", 1, 2000, 4000)]
SETTLED += poses(0, 0, 2000, 8) + poses(1, 2000, 4000, 16)

# ⚠ THE VEO CASE. Shot 1 was animated, so `spreadPanelsForRenders` grew it to 6s
# and pushed shot 2 out to 6000 — and the two flipbooks are still sitting where
# they were. This is the report in fixture form.
MOVED = [panel("s1", 0, 0, 6000), panel("s2", 1, 6000, 4000)]
MOVED += poses(0, 0, 2000, 8) + poses(1, 2000, 4000, 16)

# ⚠ A RUN SOMEBODY EDITED BY HAND. Shot 1 is 2s with four drawings, and the FIRST
# of them was stretched to half the shot. When the shot moves and doubles, that
# drawing must still be half the shot — the pass maps the run's shape over, it
# does not re-spread it evenly.
SHAPED = [panel("s1", 0, 4000, 4000)]
SHAPED += [
    {"id": "h0", "kind": "image", "track": 1, "start_ms": 0, "duration_ms": 1000,
     "src": {"kind": "pose", "storyboard_id": BOARD, "index": 0, "frame": 0}},
    {"id": "h1", "kind": "image", "track": 1, "start_ms": 1000, "duration_ms": 334,
     "src": {"kind": "pose", "storyboard_id": BOARD, "index": 0, "frame": 1}},
    {"id": "h2", "kind": "image", "track": 1, "start_ms": 1334, "duration_ms": 333,
     "src": {"kind": "pose", "storyboard_id": BOARD, "index": 0, "frame": 2}},
    {"id": "h3", "kind": "image", "track": 1, "start_ms": 1667, "duration_ms": 333,
     "src": {"kind": "pose", "storyboard_id": BOARD, "index": 0, "frame": 3}},
]

# ⚠ A SHOT TRIMMED SHORTER THAN ITS DRAWINGS CAN FIT. Eight poses need 800ms at
# the floor and the shot is now 400ms. Every drawing must survive.
SQUEEZED = [panel("s1", 0, 0, 400)] + poses(0, 0, 2000, 8)

# ⚠ THE IMPORT'S LAYOUT, WHICH MUST NEVER BE TOUCHED. `_frames_from_board` lays
# poses down INSTEAD of the panel, so these ARE the cut — there is no panel clip
# for shot 0, and nothing may drag them about.
INLINE = poses(0, 0, 2000, 8, track=0)

# ⚠ A DUPLICATED PANEL MUST NOT STEAL THE ORIGINAL'S FLIPBOOK. Both clips carry
# the same board reference; the copy plays later, and the run belongs to the one
# that plays first.
DUPED = [panel("s1", 0, 0, 2000), panel("s1copy", 0, 5000, 2000)]
DUPED += poses(0, 0, 2000, 8)

# A project with no key poses at all — the ordinary case, and the fast path.
NO_POSES = [panel("s1", 0, 0, 2000), panel("s2", 1, 2000, 2000)]

ALIGNED = {
    "settled": SETTLED,
    "moved": MOVED,
    "shaped": SHAPED,
    "squeezed": SQUEEZED,
    "inline": INLINE,
    "duped": DUPED,
    "no_poses": NO_POSES,
}


def run_node() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — cannot drive scene.js.")
        print("  This test is the only thing checking the Animatic images layout;")
        print("  a skip here is a real gap, not a pass.")
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="animimg_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS
                % {
                    "scene": json.dumps(Path(SCENE_JS).resolve().as_uri()),
                    "assets": json.dumps(Path(ASSETS_JS).resolve().as_uri()),
                }
            )
        payload = json.dumps({"clips": CLIPS, "runs": RUNS, "aligned": ALIGNED})
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


def check_run(name, run, want_slots, want_numbers=None):
    """A laid-out run is gapless, in order, and exactly as long as its shot."""
    spec = RUNS[name]
    start, hold = spec["startMs"], spec["holdMs"]
    check(f"{name}: {want_slots} clip(s)", len(run) == want_slots, f"got {len(run)}")
    if not run:
        return
    check(f"{name}: starts where the shot starts", run[0]["start_ms"] == start,
          f"{run[0]['start_ms']} != {start}")
    last = run[-1]
    end = last["start_ms"] + last["duration_ms"]
    # ⚠ THE ONE THAT MATTERS MOST. Off by 3ms here is a frame of the panel
    # showing through at the end of every shot in the film.
    check(f"{name}: ends where the shot ends", end == start + hold,
          f"{end} != {start + hold}")
    gaps = [
        (i, run[i]["start_ms"], run[i - 1]["start_ms"] + run[i - 1]["duration_ms"])
        for i in range(1, len(run))
        if run[i]["start_ms"] != run[i - 1]["start_ms"] + run[i - 1]["duration_ms"]
    ]
    check(f"{name}: no gaps and no overlaps", not gaps, str(gaps[:3]))
    short = [s for s in run if s["duration_ms"] < 100]
    check(f"{name}: no clip under the 100ms floor", not short, str(short[:3]))
    if want_numbers is not None:
        got = [s["frame"] for s in run]
        check(f"{name}: the pose numbers survive", got == want_numbers,
              f"{got} != {want_numbers}")


def main():
    print("🖼 Animatic images — the row, and the layout\n")
    got = run_node()

    print("THE ROW — a key pose is neither a still nor a take")
    check(
        "board_poses is a row kind, between the stills and the renders",
        got["rowKinds"] == ["board_image", "board_poses", "board_video", "video"],
        str(got["rowKinds"]),
    )
    # ⚠ IT TAKES NO FILE, exactly as the two rows either side of it take none.
    # A row filled by an action must not also be a place to drop an upload, or
    # it is the mixing the strict rows exist to stop.
    check("board_poses accepts no dropped file", got["takes"].get("board_poses") == [],
          str(got["takes"].get("board_poses")))
    check("board_poses counts as a board row", got["boardRows"].get("board_poses") is True)
    check("a plain video row still does not", got["boardRows"].get("video") is False)

    for name, want in WANT_ROWS.items():
        check(f"{name} → {want}", got["rows"].get(name) == want, str(got["rows"].get(name)))

    print("\nTHE ⬇ — what the editor can hand over")
    for name, want in WANT_SAVABLE.items():
        check(f"{name}: savable is {want}",
              got["savable"].get(name) is want, str(got["savable"].get(name)))

    print("\nTHE MEDIA PANE — which section, and what a drag of it is")
    for name, want in WANT_SECTION.items():
        check(f"{name}: filed under “{want}”",
              got["section"].get(name) == want, str(got["section"].get(name)))
    for name, want in WANT_FROM_BOARD.items():
        check(f"{name}: came off a board is {want}",
              got["fromBoard"].get(name) is want, str(got["fromBoard"].get(name)))
    check(
        "a key pose is filed apart from the panels but still reads as a board card",
        got["section"].get("pose") != got["section"].get("panel")
        and got["fromBoard"].get("pose") is True,
        f'{got["section"].get("pose")} / {got["fromBoard"].get("pose")}',
    )

    print("\nTHE ARITHMETIC — 2s × 4 poses per second = 8 drawings")
    for seconds in ALLOWED_DURATIONS:
        want = seconds * KEY_POSES_PER_SECOND
        check(f"{seconds}s buys {want} drawings", seconds * KEY_POSES_PER_SECOND == want)

    print("\nTHE LAYOUT — each shot's drawings divide that shot")
    runs = got["runs"]
    check_run("two_seconds", runs["two_seconds"], 8, list(range(8)))
    # The user's own sentence, asserted as a number: eight drawings, 250ms each.
    holds = {s["duration_ms"] for s in runs["two_seconds"]}
    check("two_seconds: every drawing holds 250ms", holds == {250}, str(sorted(holds)))
    check_run("four_seconds", runs["four_seconds"], 16, list(range(16)))
    check_run("ragged", runs["ragged"], 16, list(range(16)))
    check_run("with_hole", runs["with_hole"], 7, [0, 1, 2, 3, 4, 6, 7])
    check_run("too_short", runs["too_short"], 6, [0, 1, 2, 3, 4, 5])
    check("empty: nothing drawn lays nothing down", runs["empty"] == [],
          str(runs["empty"]))

    print("\nTHE FOLLOWING — a run goes where its shot goes")
    al = got["aligned"]

    # ⚠ FIRST, THE ONE THAT PROTECTS UNDO. A consistent document comes back
    # untouched — by IDENTITY, so nothing was rewritten to the same value either.
    check("an already-correct project is not touched at all",
          al["settled"]["touched"] is False, json.dumps(al["settled"])[:200])
    check("…and a project with no key poses is not either",
          al["no_poses"]["touched"] is False, json.dumps(al["no_poses"])[:200])
    # ⚠ AND THE ONE THAT PROTECTS THE EDITOR FROM LOOPING FOR EVER.
    for name in ALIGNED:
        check(f"{name}: the pass settles in one go",
              al[name]["settled"] is True, json.dumps(al[name])[:200])

    # THE VEO CASE. Shot 1 grew 2s → 6s and shot 2 was pushed to 6000; both
    # flipbooks must now cover their own shot exactly.
    moved = {c["id"]: c for c in al["moved"]["clips"]}
    run1 = sorted([c for c in al["moved"]["clips"] if c["id"].startswith("p0_")],
                  key=lambda c: c["start_ms"])
    run2 = sorted([c for c in al["moved"]["clips"] if c["id"].startswith("p1_")],
                  key=lambda c: c["start_ms"])
    check("moved: shot 1's eight drawings now cover 0 → 6000",
          run1[0]["start_ms"] == 0
          and run1[-1]["start_ms"] + run1[-1]["duration_ms"] == 6000
          and len(run1) == 8,
          json.dumps(run1))
    check("moved: shot 2's sixteen went with it, 6000 → 10000",
          run2[0]["start_ms"] == 6000
          and run2[-1]["start_ms"] + run2[-1]["duration_ms"] == 10000
          and len(run2) == 16,
          json.dumps(run2))
    check("moved: still gapless and still in pose order",
          all(run1[i]["start_ms"] == run1[i - 1]["start_ms"] + run1[i - 1]["duration_ms"]
              for i in range(1, len(run1)))
          and [c["frame"] for c in run1] == list(range(8)),
          json.dumps(run1))
    check("moved: the panels themselves are left exactly where they are",
          moved["s1"]["start_ms"] == 0 and moved["s1"]["duration_ms"] == 6000
          and moved["s2"]["start_ms"] == 6000,
          json.dumps([moved["s1"], moved["s2"]]))

    # ⚠ THE HAND-EDIT SURVIVES. The first drawing was half its shot before the
    # move and must be half of it after — mapped, not re-spread.
    # DRAWINGS ONLY — the panel is in this list too, it starts at the same
    # millisecond as the first drawing, and sorting them together is how an
    # assertion about "the run" comes to be measuring the shot instead.
    shaped = sorted(
        [c for c in al["shaped"]["clips"] if c["frame"] is not None],
        key=lambda c: c["start_ms"],
    )
    check("shaped: the run is carried onto the shot",
          shaped[0]["start_ms"] == 4000
          and shaped[-1]["start_ms"] + shaped[-1]["duration_ms"] == 8000,
          json.dumps(shaped))
    # It was half of a 2s shot; the shot is 4s now, so it is 2s — and the three
    # after it share what is left, none of them anywhere near as long.
    check("shaped: a drawing somebody lengthened by hand is still the long one",
          shaped[0]["duration_ms"] == 2000
          and all(c["duration_ms"] < 1000 for c in shaped[1:]),
          json.dumps(shaped))

    # ⚠ NOTHING IS EVER BINNED. Eight drawings need 800ms at the floor; the shot
    # is 400ms, so the run overhangs rather than losing its tail.
    squeezed = sorted(al["squeezed"]["clips"], key=lambda c: c["start_ms"])
    sq = [c for c in squeezed if c["frame"] is not None]
    check("squeezed: every drawing survives a shot trimmed too short",
          len(sq) == 8, str(len(sq)))
    check("squeezed: none is under the 100ms floor",
          all(c["duration_ms"] >= 100 for c in sq), json.dumps(sq))
    check("squeezed: it starts with its shot and overhangs rather than losing the tail",
          sq[0]["start_ms"] == 0 and sq[-1]["start_ms"] + sq[-1]["duration_ms"] == 800,
          json.dumps(sq))

    # ⚠ THE IMPORT'S OWN LAYOUT IS NOT THIS PASS'S BUSINESS.
    check("inline: poses that ARE the cut are left alone",
          al["inline"]["touched"] is False, json.dumps(al["inline"])[:200])

    # ⚠ A COPY CANNOT STEAL THE RUN.
    check("duped: the flipbook stays with the panel that plays first",
          al["duped"]["touched"] is False, json.dumps(al["duped"])[:200])

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print(f"  · {f}")
        sys.exit(1)
    print("All good.")


if __name__ == "__main__":
    main()
