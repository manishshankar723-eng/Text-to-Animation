"""Checks that a REDRAWN key pose actually reaches the screen.

Reported as "i click generate again … i can't see any changes", and it was three
separate faults stacked on top of each other:

 1. **Regenerate resumed instead of redrawing.** Both the strip button and the
    dialog sent `resume=true`. On a finished 8/8 shot the server correctly has
    nothing missing to draw and returns "already complete" — so the click did
    nothing whatsoever. Regenerate now sends `resume=false`.
 2. **A redrawn pose kept its URL.** The client caches one object URL per path
    and never re-fetches a path it already holds, so even a pose that WAS
    redrawn kept showing the old drawing for ever — the per-pose ↻ button could
    never visibly work. Frame URLs now carry `?v=<mtime>`.
 3. **Nothing on screen changed while it worked** — covered by the client's
    `.is-redrawing` veil, which has no Python to test.

This covers 1 and 2, which are the ones with a server contract.

    python tests/key_pose_refresh_check.py
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

import panel_sequence as ps

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


PANEL = {"index": 0, "scene_number": 1, "camera": "wide shot",
         "description": "A wide shot establishes Kabir's bedroom, Kabir asleep."}

tmp = tempfile.mkdtemp(prefix="keypose_refresh_")
try:
    board_dir = os.path.join(tmp, "_storyboards", "job1")
    os.makedirs(board_dir, exist_ok=True)      # variant 0 == the board root
    Image.new("RGB", (1280, 720), (17, 34, 51)).save(
        os.path.join(board_dir, "panel_00.png")
    )

    nth = {"n": 0}

    def fake_generate_frame(beat, n, *a, **kw):
        nth["n"] += 1
        # A different picture every call, so "did it change?" is answerable.
        return Image.new("RGB", (1280, 720), (180, 60, 40 + nth["n"]))

    real_generate, real_plan = ps.generate_frame, ps.plan_beats
    ps.generate_frame = fake_generate_frame
    ps.plan_beats = lambda **kw: (
        [{"frame": i * 6, "pose": f"beat {i + 1}"} for i in range(kw["count"])],
        "Kabir stays asleep under the quilt.",
    )
    try:
        # ---------------------------------------------------------------
        print("\nA pose's URL version changes when the pose is redrawn")
        # ---------------------------------------------------------------
        ps.run_panel_sequence("job1", dict(PANEL), 2, output_dir=tmp, variant=0,
                              aspect_ratio="16:9")
        before = [ps.frame_version(board_dir, 0, n) for n in range(8)]
        check("every drawn pose has a non-zero version", all(v > 0 for v in before))
        check("a pose that does not exist versions as 0",
              ps.frame_version(board_dir, 0, 99), 0)

        # mtime_ns is fine-grained, but give the filesystem a beat regardless —
        # this is asserting the version CHANGES, not how fast it can.
        time.sleep(0.01)
        ps.run_panel_sequence("job1", dict(PANEL), 2, output_dir=tmp, variant=0,
                              aspect_ratio="16:9", redraw=[3],
                              beats=[{"frame": i * 6, "pose": f"beat {i + 1}"} for i in range(8)],
                              hold="Kabir stays asleep under the quilt.")
        after = [ps.frame_version(board_dir, 0, n) for n in range(8)]
        check("the redrawn pose gets a NEW version", after[3] != before[3])
        check("…and every other pose keeps its old one",
              [after[n] for n in range(8) if n != 3],
              [before[n] for n in range(8) if n != 3])

        # ---------------------------------------------------------------
        print("\nRegenerate redraws; resume does not")
        # ---------------------------------------------------------------
        nth["n"] = 0
        v_before = [ps.frame_version(board_dir, 0, n) for n in range(8)]

        # resume=True on a COMPLETE shot is the old "Generate again" — a no-op.
        # This is the bug: the user pressed it and nothing happened.
        out = ps.run_panel_sequence("job1", dict(PANEL), 2, output_dir=tmp,
                                    variant=0, aspect_ratio="16:9", resume=True)
        check("resume on a complete shot draws nothing", nth["n"], 0)
        check("…and leaves every version untouched",
              [ps.frame_version(board_dir, 0, n) for n in range(8)], v_before)
        check("…while still reporting the shot as complete", out["frames"], 8)

        time.sleep(0.01)
        # resume=False is Regenerate — every pose is drawn again. Pose 1 is the
        # panel copied, so it costs 7 images, but its FILE is rewritten too and
        # must re-version like the rest or the strip would show it stale.
        out = ps.run_panel_sequence("job1", dict(PANEL), 2, output_dir=tmp,
                                    variant=0, aspect_ratio="16:9", resume=False)
        v_after = [ps.frame_version(board_dir, 0, n) for n in range(8)]
        check("regenerate draws every pose but the copied first one", nth["n"], 7)
        check("…and EVERY pose re-versions, pose 1 included",
              all(v_after[n] != v_before[n] for n in range(8)))
        check("…leaving the shot complete", out["frames"], 8)
    finally:
        ps.generate_frame, ps.plan_beats = real_generate, real_plan
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All key-pose refresh checks passed.")
