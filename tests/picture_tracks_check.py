"""THE PICTURE IS A STACK OF TRACKS, AND THE EXPORT COMPOSITES IT.

The report this exists for:

    "when i do video trim so i see my image layer conetnt move like snip and same
     with image when i trim image so my video layer content move. i want user
     move independaly each asstes/conetnt in layer"

It was true by construction. `frames` was ONE sequence laid end to end: a clip's
place was the sum of the clips before it, so changing any clip's length moved
every clip after it — including the ones drawn on the row above, because the two
picture rows were that same sequence filtered by where each clip came from. A
picture carries `track` and `start_ms` now (`frame_spans`), which makes the rows
real and independent, and this file is the export side of that.

`tests/render_parity.py` proves the two EVALUATORS agree about a stack — which
clips are up at t, on which track, resolved to what. What it cannot see is whether
the exporter DRAWS them: the planners have to carry a stack rather than one
picture, the renderer has to composite bottom-to-top, and a gap has to encode as a
frame of backdrop rather than as no frame at all. Those are the four things here.

---------------------------------------------------------------------------
⚠ EVERY CLIP IS A COLOUR CARD, ON PURPOSE
---------------------------------------------------------------------------
A colour card needs no file (`_source_for` answers it from the clip itself), so
this whole file runs with no uploads, no board and no ffmpeg — and the pixel it
draws is a value the test can name. Compositing is exactly what has to be
measured, and "the centre pixel is the colour of the clip that should be on top"
is the shortest true statement of it.

    python tests/picture_tracks_check.py

The encoded half at the end needs ffmpeg; without it that section says so and the
rest still proves the composite.
"""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import animatic
from animatic import (
    build_animatic,
    ffmpeg_available,
    plan_animated_segments,
    plan_segments,
    render_frame,
    _still_layer,
)
from animatic_render import frame_spans, scene_at

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


SIZE = (160, 90)
BG = "#101010"
RED = "#c81e1e"
BLUE = "#1e3cc8"
GREEN = "#1ec83c"


def card(cid, start, dur, track, colour):
    return {
        "id": cid,
        "kind": "color",
        "color": colour,
        "start_ms": start,
        "duration_ms": dur,
        "track": track,
        "label": cid,
    }


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------
# track 0:  [====== red 0–3000 ======]        [== green 4000–5000 ==]
# track 1:            [== blue 1000–2000 ==]
#
# So: red alone, then BLUE OVER RED, then red again, then A HOLE ON EVERY TRACK
# (3000–4000), then green. Four different answers, one of which used to be
# impossible to express and is the one the planners had to learn to encode.
PROJECT = {
    "frames": [
        card("red", 0, 3000, 0, RED),
        card("green", 4000, 1000, 0, GREEN),
        card("blue", 1000, 1000, 1, BLUE),
    ],
    "texts": [],
    "shapes": [],
    "overlays": [],
    "transitions": [],
}
TOTAL = 5000


def centre_of(image):
    """The middle pixel, as #rrggbb — what "which clip is on top" reduces to."""
    r, g, b = image.convert("RGB").getpixel((SIZE[0] // 2, SIZE[1] // 2))
    return f"#{r:02x}{g:02x}{b:02x}"


def draw(segment):
    """One planned segment, rendered exactly as `build_animatic` renders it."""
    return render_frame(
        size=SIZE,
        background=BG,
        pictures=[
            _still_layer(PROJECT["frames"], item, fit="contain")
            for item in segment["pictures"]
        ],
        texts=segment.get("texts") or [],
        shapes=segment.get("shapes") or [],
        overlays=segment.get("overlays") or [],
    )


def segment_at(segments, ms):
    for s in segments:
        if s["start_ms"] <= ms < s["start_ms"] + s["duration_ms"]:
            return s
    return None


# ---------------------------------------------------------------------------
print("Where the clips sit")
spans, total = frame_spans(PROJECT["frames"])
check("the timeline is as long as the last clip ENDS, not the sum of the tracks",
      total == TOTAL, f"got {total}")
check("each clip keeps the start it was given",
      [(s["track"], s["start"], s["end"]) for s in spans]
      == [(0, 0, 3000), (0, 4000, 5000), (1, 1000, 2000)],
      str([(s["track"], s["start"], s["end"]) for s in spans]))

# ⚠ THE POINT OF THE WHOLE CHANGE, in one assertion: shortening a clip on one
# track moves NOTHING on any other. Under the old model every clip after it moved.
trimmed = {
    **PROJECT,
    "frames": [
        {**PROJECT["frames"][0], "duration_ms": 1500},
        PROJECT["frames"][1],
        PROJECT["frames"][2],
    ],
}
after, _ = frame_spans(trimmed["frames"])
check("⚠ TRIMMING ONE CLIP MOVES NO OTHER CLIP, on its track or any other",
      [(s["start"], s["end"]) for s in after][1:] == [(4000, 5000), (1000, 2000)],
      str([(s["start"], s["end"]) for s in after]))

# ---------------------------------------------------------------------------
print("\nThe fast planner — a project where nothing moves")
segs, seg_total = plan_segments(PROJECT["frames"], [], None, [], [])
check("it covers the timeline and no more", seg_total == TOTAL, f"got {seg_total}")
check("the segments are contiguous, with no hole in TIME",
      all(
          a["start_ms"] + a["duration_ms"] == b["start_ms"]
          for a, b in zip(segs, segs[1:])
      )
      and segs[0]["start_ms"] == 0
      and segs[-1]["start_ms"] + segs[-1]["duration_ms"] == TOTAL,
      str([(s["start_ms"], s["duration_ms"]) for s in segs]))

# ⚠ THE ONE THE OLD PLANNER GOT WRONG BY DESIGN: it skipped a moment with no
# picture (`frame_index is None: continue`), which was unreachable while the
# sequence had no holes. Skipping it now would make the encoded video SHORTER
# than the timeline and pull the audio out of sync from the first gap onward.
hole = segment_at(segs, 3500)
check("a moment with nothing on any track IS a segment, not a skipped hole",
      hole is not None and hole["pictures"] == [],
      "the gap was skipped" if hole is None else str(hole["pictures"]))

stacked = segment_at(segs, 1500)
check("where two tracks are up, the segment names BOTH, bottom first",
      stacked is not None
      and [PROJECT["frames"][i["frame"]]["id"] for i in stacked["pictures"]]
      == ["red", "blue"],
      str(stacked and [i["frame"] for i in stacked["pictures"]]))

# ---------------------------------------------------------------------------
print("\nWhat the exporter actually DRAWS")
check("one track up → that clip's colour", centre_of(draw(segment_at(segs, 500))) == RED,
      centre_of(draw(segment_at(segs, 500))))
check("⚠ TWO TRACKS UP → THE HIGHER ONE, composited over the lower",
      centre_of(draw(segment_at(segs, 1500))) == BLUE,
      centre_of(draw(segment_at(segs, 1500))))
check("…and the lower one is back the moment the upper clip ends",
      centre_of(draw(segment_at(segs, 2500))) == RED,
      centre_of(draw(segment_at(segs, 2500))))
check("a gap on every track → the letterbox colour, not black and not a hold",
      centre_of(draw(segment_at(segs, 3500))) == BG,
      centre_of(draw(segment_at(segs, 3500))))
check("and the clip after the gap draws itself",
      centre_of(draw(segment_at(segs, 4500))) == GREEN,
      centre_of(draw(segment_at(segs, 4500))))

# ⚠ A GAP ON AN UPPER TRACK MUST REVEAL THE TRACK BELOW, which is the half of
# "higher draws over lower" that an opaque stack would hide. Same fixture read at
# a moment where only track 0 is up — asserted separately because it is a
# different rule from "the higher one wins".
check("a gap on the upper track shows the track underneath it",
      centre_of(draw(segment_at(segs, 2999))) == RED,
      centre_of(draw(segment_at(segs, 2999))))

# ---------------------------------------------------------------------------
print("\nThe sampling planner — the same answers, per video frame")
# A keyframe on the upper clip forces it (`is_animated`), which is the path a
# project with any animation in it takes.
moving = {
    **PROJECT,
    "frames": [
        PROJECT["frames"][0],
        PROJECT["frames"][1],
        {**PROJECT["frames"][2],
         "keyframes": {"opacity": [{"t": 0, "v": 1.0}, {"t": 1000, "v": 1.0}]}},
    ],
}
msegs, mtotal = plan_animated_segments(moving["frames"], [], None, [], [], 24, [])
check("it covers the same timeline", mtotal == TOTAL, f"got {mtotal}")
check("every sample carries a stack, and the gap's is empty",
      segment_at(msegs, 3500)["pictures"] == []
      and len(segment_at(msegs, 1500)["pictures"]) == 2,
      str(segment_at(msegs, 1500)["pictures"]))
check("the two planners draw the same picture at the same moment",
      centre_of(draw(segment_at(msegs, 1500))) == BLUE
      and centre_of(draw(segment_at(msegs, 3500))) == BG)
check("a sample with no picture still has a render key, and it is its own",
      segment_at(msegs, 3500)["signature"] != segment_at(msegs, 1500)["signature"])

# ---------------------------------------------------------------------------
print("\nThe monitor's evaluator says the same thing")
# `scene_at` is what the Program monitor draws from; if it disagreed with the
# planners the preview would lie about the export. (render_parity.py compares it
# against scene.js; this compares it against what the EXPORTER planned.)
for ms, want in ((500, ["red"]), (1500, ["red", "blue"]), (3500, []), (4500, ["green"])):
    got = [PROJECT["frames"][p["frame"]["index"]]["id"] for p in scene_at(PROJECT, ms)["pictures"]]
    check(f"at {ms}ms the monitor has {want or 'no picture'}", got == want, str(got))

# ---------------------------------------------------------------------------
print("\nThe encoded file")
if not ffmpeg_available():
    print("  ffmpeg is not available — the encoded half cannot run.")
    print("  Everything above is proved; that the MP4 is the right LENGTH is not.")
else:
    work = tempfile.mkdtemp(prefix="tracks_")
    try:
        # ⚠ THE LENGTH IS THE ASSERTION. A gap that the planner skipped would come
        # back as a shorter file — silently, and with everything after it a second
        # early against the audio. That is the failure this section exists for.
        out = build_animatic(
            "tracks-test",
            PROJECT["frames"],
            aspect_ratio="16:9",
            resolution=90,
            fps=12,
            background=BG,
            output_dir=work,
        )
        check("it encoded", bool(out.get("video")), str(out)[:200])
        check("⚠ AND IT IS AS LONG AS THE TIMELINE — the gap was encoded, not skipped",
              abs(out.get("duration_ms", 0) - TOTAL) <= 100,
              f"{out.get('duration_ms')}ms of {TOTAL}ms")
        # ⚠ ONE STILL PER DISTINCT STACK, which is what the render cache is for
        # and what makes the multi-track key earn its keep. Four of them here: red
        # alone, blue OVER red, the backdrop, green — and red again at 2000–3000 is
        # the same picture as red alone, so it is drawn once and reused. Read from
        # `still_count`; `frame_count` is how many CLIPS the project has.
        check("it rendered one still per distinct picture, reusing the repeat",
              out.get("still_count", 0) == 4, str(out.get("still_count")))
        check("…and there are more segments than stills, because one was reused",
              out.get("segment_count", 0) > out.get("still_count", 0),
              f"{out.get('segment_count')} segment(s), {out.get('still_count')} still(s)")
    finally:
        shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The picture is a stack of independent tracks, and the export draws it that way.")
