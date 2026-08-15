"""Checks for the two ways a key-pose sequence stops being the shot it came from.

Both were reported on the same 8-pose run of one establishing wide — "SHOT 1: A
wide shot establishes Kabir's cramped, sunlit bedroom… showing a simple bed with
a quilt", immediately before "SHOT 2: A close-up shows Kabir lying fast asleep":

 1. POSE 1 WAS NOT THE PANEL. It was generated like every other pose, so the
    first picture in the zip was a fresh interpretation of a shot the user had
    already approved on the board — different bedding, different figure, the
    lot. No prompt fixes that; pose 1 is now the panel file, copied.
 2. THE SHOT OUTRAN ITS DESCRIPTION. Handed one sentence with no written action
    and a hard demand for movement in every drawing, the planner invented one:
    by pose 8 Kabir had woken up and was sitting on the edge of the bed — while
    the NEXT panel on the board still showed him fast asleep.

The offline half runs on stubs and always works. The `--live` half spends one
small TEXT call (no images) to check what the planner actually returns now.

    python tests/key_pose_scope_check.py [--live]
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

import panel_sequence as ps
from storyboard_pipeline import story_context_for

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


# The reported board, as far as it matters here.
BOARD = [
    {"index": 0, "scene_number": 1, "camera": "wide shot", "characters": ["Kabir"],
     "location": "Kabir's bedroom", "description":
     "A wide shot establishes Kabir's cramped, sunlit bedroom in a middle-class "
     "Indian home, showing a simple bed with a quilt."},
    {"index": 1, "scene_number": 1, "camera": "close-up", "characters": ["Kabir"],
     "description":
     "A close-up shows Kabir lying fast asleep under his quilt, his face peaceful."},
]

# What "the shot moved on without permission" looks like in a pose line. Not a
# guard in production — the model is told not to and mostly obeys — but the one
# thing that must never appear in a plan for a shot whose next panel is the same
# man still asleep.
STORY_ADVANCING = (
    "wakes", "wakes up", "woke", "awake", "sits up", "sit up", "sitting up",
    "stands", "standing up", "gets up", "out of bed", "feet on the floor",
    "swings his legs", "opens his eyes", "eyes open",
)


def scope_breaks(lines) -> list[str]:
    out = []
    for i, line in enumerate(lines):
        low = str(line).lower()
        for phrase in STORY_ADVANCING:
            if phrase in low:
                out.append(f"pose {i + 1}: …{phrase}…")
                break
    return out


# ---------------------------------------------------------------------------
print("\nThe planner is told where the shot sits")
# ---------------------------------------------------------------------------
ctx = story_context_for(BOARD, BOARD[0])
check("story context is found for a panel by its index", isinstance(ctx, dict))
flow = ps._flow_lines(ctx)
check("the NEXT shot's description reaches the planner", "fast asleep" in flow)
check("…and is fenced off as somewhere the shot may not go",
      "must not happen in yours" in flow)
check("the planner is told which shot of how many this is", "shot 1 of 2" in flow)
check("a board-less caller degrades quietly rather than crashing",
      story_context_for(None, BOARD[0]), None)

# The rules that stop a held shot from inventing an action have to survive
# whatever else gets rewritten in the system prompt.
for rule in ("STAY INSIDE THE SHOT AS WRITTEN",
             "A SHOT WHERE NOTHING HAPPENS IS STILL A SHOT",
             "MATCH THE MOVEMENT TO THE FRAMING",
             "Pose 1 IS the panel itself"):
    check(f"system prompt still carries: {rule}", rule in ps._SYSTEM)


# ---------------------------------------------------------------------------
print("\nThe plan always opens on the panel, and carries an invariant")
# ---------------------------------------------------------------------------
def fake_plan(poses, hold="", count=8):
    """Run plan_beats' cleanup over a canned model reply, with no network."""
    import json

    class _Resp:
        text = json.dumps({
            "poses": [{"frame": i * 6, "pose": p} for i, p in enumerate(poses)],
            "hold": hold,
        })

    class _Models:
        def generate_content(self, **kw):
            return _Resp()

    class _Client:
        models = _Models()

    import script_breakdown as sb
    real = sb.get_client
    sb.get_client = lambda provider=None: _Client()
    try:
        return ps.plan_beats("Kabir asleep in bed", 2, count)
    finally:
        sb.get_client = real


# A planner that ignores the rule and writes its own opening pose must not be
# able to leave the strip describing pose 1 as something the picture is not —
# pose 1 is a copy of the panel, so its line is pinned to say so.
beats, hold = fake_plan(
    ["A wide view of a tidy bedroom with a made bed"] + [f"beat {i}" for i in range(2, 9)],
    hold="Kabir stays asleep; he never wakes.",
)
check("pose 1's line is pinned to the panel", beats[0]["pose"], ps.OPENING_POSE)
check("pose 1 lands on frame 0", beats[0]["frame"], 0)
check("the rest of the plan is left alone", beats[1]["pose"], "beat 2")
check("the invariant comes back with the plan", hold, "Kabir stays asleep; he never wakes.")

# A model that returns no invariant is not a licence to draw without one.
_, fallback = fake_plan([f"beat {i}" for i in range(1, 9)], hold="")
check("a missing invariant falls back to a real rule", bool(fallback.strip()))
check("…and that rule is 'nothing the description does not name'",
      "does not name" in fallback)


# ---------------------------------------------------------------------------
print("\nPose 1 is the panel COPIED — never generated")
# ---------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="keypose_check_")
try:
    board_dir = os.path.join(tmp, "_storyboards", "job1")
    os.makedirs(board_dir, exist_ok=True)          # variant 0 == the board root
    panel = Image.new("RGB", (1280, 720), (17, 34, 51))
    panel.paste(Image.new("RGB", (300, 200), (220, 40, 40)), (40, 40))
    panel.save(os.path.join(board_dir, "panel_00.png"))

    drawn: list[int] = []

    def fake_generate_frame(beat, n, *a, **kw):
        drawn.append(n)
        if not str(kw.get("hold") or "").strip():
            failures.append(f"pose {n + 1} was drawn without the shot's invariant")
        # Coloured, or the production greyscale guard correctly retries it.
        return Image.new("RGB", (1280, 720), (180, 60, 40))

    real_generate, real_plan = ps.generate_frame, ps.plan_beats
    ps.generate_frame = fake_generate_frame
    ps.plan_beats = lambda **kw: (
        [{"frame": i * 6, "pose": f"beat {i + 1}"} for i in range(kw["count"])],
        "Kabir stays asleep under the quilt; he never wakes or sits up.",
    )
    try:
        out = ps.run_panel_sequence(
            "job1", dict(BOARD[0]), 2, output_dir=tmp, variant=0,
            board_panels=BOARD, aspect_ratio="16:9",
        )
        check("pose 1 costs no image call", 0 not in drawn)
        check("every other pose is drawn", drawn, list(range(1, 8)))
        check("all 8 poses end up on disk", out["frames"], 8)
        check("no holes", out["missing"], [])
        check("the invariant is stored with the sequence", "never wakes" in out.get("hold", ""))

        opening = Image.open(ps.frame_path(board_dir, 0, 0)).convert("RGB")
        check("pose 1 is the panel's own pixels (drawing)",
              opening.getpixel((100, 100)), (220, 40, 40))
        check("pose 1 is the panel's own pixels (ground)",
              opening.getpixel((900, 600)), (17, 34, 51))

        # PREVIEW still buys two real drawings: the free panel must not eat half
        # the budget, or the preview stops answering "did the character move?".
        shutil.rmtree(os.path.join(board_dir, "seq"))
        drawn.clear()
        out = ps.run_panel_sequence(
            "job1", dict(BOARD[0]), 2, output_dir=tmp, variant=0,
            board_panels=BOARD, aspect_ratio="16:9", limit=ps.PREVIEW_POSES,
        )
        check("a preview still buys PREVIEW_POSES real drawings",
              drawn, list(range(1, ps.PREVIEW_POSES + 1)))
        check("…plus the free panel on the front", out["frames"], ps.PREVIEW_POSES + 1)
    finally:
        ps.generate_frame, ps.plan_beats = real_generate, real_plan
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\nThe invariant reaches the image prompt, and outranks the movement push")
# ---------------------------------------------------------------------------
import gemini_client as gc

seen: dict = {}


class _CapturingModels:
    def generate_content(self, model, contents, config):
        seen["prompt"] = contents[0]
        raise RuntimeError("captured")


class _CapturingClient:
    models = _CapturingModels()


real_client, real_retries = gc.get_client, gc.MAX_RETRIES
gc.get_client, gc.MAX_RETRIES = (lambda provider=None: _CapturingClient()), 1
try:
    HOLD = "Kabir stays asleep under the quilt; he never wakes, sits up or leaves the bed."
    try:
        ps.generate_frame(
            {"frame": 18, "pose": "His head eases a fraction left into the pillow"},
            3, 8, 48,
            panel={"index": 0, "characters": ["Kabir"], "camera": "wide shot"},
            panel_image=Image.new("RGB", (1280, 720), (17, 34, 51)),
            style="rough_sketch", aspect_ratio="16:9", hold=HOLD,
        )
    except Exception:
        pass  # the stub always raises; the prompt is what we came for
finally:
    gc.get_client, gc.MAX_RETRIES = real_client, real_retries

prompt = seen.get("prompt", "")
check("the invariant is in the drawing prompt", "never wakes" in prompt)
check("…flagged as the thing that must not change", "WHAT MUST NOT CHANGE" in prompt)
check("…and it outranks the 'body has MOVED' push",
      "This overrides everything above about movement" in prompt)
check("the drawing is told not to invent an action",
      "Do not add any action the pose line does not name" in prompt)
check("the movement follows the POSE, not a fixed body part",
      "The part of the body named below" in prompt)
if "WHAT MUST NOT CHANGE" in prompt and "is a failed drawing" in prompt:
    check("the fence comes after the push, so it reads as the last word",
          prompt.index("WHAT MUST NOT CHANGE") > prompt.index("is a failed drawing"))


# ---------------------------------------------------------------------------
if "--live" in sys.argv:
    print("\nLIVE: what the real planner returns for the reported shot "
          "(one text call, no images)")
    beats, hold = ps.plan_beats(
        description=BOARD[0]["description"], duration_seconds=2, count=8,
        camera=BOARD[0]["camera"], location=BOARD[0]["location"],
        story_context=story_context_for(BOARD, BOARD[0]),
    )
    for i, b in enumerate(beats):
        print(f"      {i + 1}. (f{b['frame']:>3}) {b['pose']}")
    print(f"      HOLD: {hold}")
    breaks = scope_breaks(b["pose"] for b in beats)
    check("no pose wakes Kabir up — the next shot still has him asleep",
          breaks, [])
    check("the invariant names the state that has to hold",
          any(w in hold.lower() for w in ("asleep", "sleeping")))
else:
    print("\n(skipping the live planner check — pass --live to spend one text call)")


print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All key-pose scope checks passed.")
