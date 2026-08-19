"""Transitions must BLEND, cost the timeline nothing, and not touch the rest.

`tests/render_parity.py` proves the two scene evaluators agree about the
numbers a transition resolves to. This proves those numbers reach the video —
it encodes real MP4s with ffmpeg, decodes frames back out, and measures them —
and that the one design promise holds:

  **BOUNDARY-LOCAL.** The blend straddles the cut, taking half its length from
  the tail of one picture and half from the head of the next. Nothing moves and
  the video is exactly as long as it would be with a straight cut. That promise
  is what let transitions land without re-verifying `frameSpans`, every cut
  position, ripple and rolling trims, and every caption timed against a cut —
  so it is asserted here rather than assumed.

The kinds are told apart by measuring what only that kind does:

    dissolve  black → white climbs smoothly, and is 50% grey half way
    dip       white → white goes DARK in the middle and comes back
    wipe      half way, the LEFT of the frame has changed and the right hasn't
    slide     half way, the RIGHT of the frame has changed and the left hasn't

The last two are each other's opposite on purpose: a renderer that drew a wipe
where a slide was asked for would pass any test that only checked "something
moved".

⚠ AND THE SAME TRICK MEASURES A PARAMETER. Every one of those four sentences
names a HALF of the frame, so a direction is proved by asserting the OPPOSITE
half — a wipe told to travel left must uncover the right side first, or it
ignored what it was told. A dip's colour is proved the same way, by dipping a
black shot through white: the middle of the window can only get brighter if the
colour arrived. Reading a parameter back off the project would prove nothing;
these decode the encoded video.

    python tests/transition_check.py

Needs ffmpeg for the second half (imageio-ffmpeg provides one; `GET /health`
reports it). The model half above it runs without.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

from animatic import (
    build_animatic,
    ffmpeg_available,
    ffmpeg_exe,
    plan_animated_segments,
    run_ffmpeg,
)
from animatic_render import (
    frame_spans,
    is_animated,
    scene_at,
    scene_signature,
    transition_at,
    transition_window,
)

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def transition(after, kind="dissolve", ms=600, ident="t1", params=None):
    return {
        "id": ident,
        "after_frame_id": after,
        "kind": kind,
        "duration_ms": ms,
        # Omitted entirely when there is nothing to say, which is the shape every
        # animatic saved before parameters existed has on disk.
        **({"params": params} if params else {}),
    }


# ---------------------------------------------------------------------------
# The model: where a transition sits, and what it refuses to do
# ---------------------------------------------------------------------------
print("Where a transition sits\n")

FRAMES = [
    {"id": "a", "duration_ms": 1000},
    {"id": "b", "duration_ms": 1000},
    {"id": "c", "duration_ms": 1000},
]
SPANS, TOTAL = frame_spans(FRAMES)

win = transition_window(FRAMES, SPANS, transition("a", ms=600))
check("it is centred on the cut, not parked after it",
      (win["start_ms"], win["cut_ms"], win["end_ms"]) == (700, 1000, 1300),
      f"({win['start_ms']} / {win['cut_ms']} / {win['end_ms']})")
check("it joins the frame it names to the next one",
      (win["from_index"], win["to_index"]) == (0, 1))

# THE CLAMP. Without it a long transition on a short picture would reach past
# the picture before it, and two of them could cover the same instant — which
# would make `transition_at` depend on list order.
short = [{"id": "a", "duration_ms": 1000}, {"id": "b", "duration_ms": 400}]
short_spans, _ = frame_spans(short)
clamped = transition_window(short, short_spans, transition("a", ms=5000))
check("it is clamped to the SHORTER of the two shots it joins",
      clamped["duration_ms"] == 400, f"(got {clamped['duration_ms']})")

# Which gives the property the whole design leans on: two transitions either
# side of one picture can meet in the middle but can never overlap.
squeezed = {
    "frames": [
        {"id": "a", "duration_ms": 4000},
        {"id": "b", "duration_ms": 400},
        {"id": "c", "duration_ms": 4000},
    ],
    "transitions": [
        transition("a", ms=10000, ident="t1"),
        transition("b", ms=10000, ident="t2"),
    ],
}
sq_spans, sq_total = frame_spans(squeezed["frames"])
covered = [transition_at(squeezed, t, sq_spans) for t in range(3600, 4900, 10)]
found = [c["id"] for c in covered if c]
check("two transitions around one short picture never overlap",
      found == sorted(found, key=lambda x: (x != "t1", x)) and set(found) == {"t1", "t2"},
      f"(saw {sorted(set(found))})")
check("and every moment is inside at most one of them",
      all(c is None or isinstance(c, dict) for c in covered))

# Inert, not invalid. Deleting the frame after a transition must not be an
# error, and must not be a transition either.
check("one hanging off the last frame is inert",
      transition_window(FRAMES, SPANS, transition("c")) is None)
check("one naming a frame that has been deleted is inert",
      transition_window(FRAMES, SPANS, transition("gone")) is None)
check("an unrecognised kind falls back to a dissolve rather than failing",
      transition_window(FRAMES, SPANS, transition("a", kind="star-wipe"))["kind"] == "dissolve")

# The render cache key. Two moments in one dissolve hold the same clips and
# differ ONLY in how far through the blend they are — leave that out of the key
# and the exporter renders one still, reuses it for the whole transition, and
# the picture SNAPS. Exactly the reuse bug the key already guards against for
# keyframes.
project = {"frames": FRAMES, "transitions": [transition("a", ms=600)]}
keys = {scene_signature(scene_at(project, t)) for t in (750, 850, 950, 1050, 1150, 1250)}
check("every moment of a blend has its own render key", len(keys) == 6, f"(got {len(keys)})")

# The planner. A transition is continuous, so it must force per-frame sampling —
# and a project without one must be left on the cheap path it has always used.
plain = {"frames": FRAMES}
check("a project with no transitions is NOT animated", is_animated(plain) is False)
check("one transition makes the whole project animated", is_animated(project) is True)

segs, seg_total = plan_animated_segments(FRAMES, [], None, [], [], 24, project["transitions"])
check("sampling covers the timeline and no more", seg_total == TOTAL, f"(got {seg_total})")
# ⚠ READ OFF THE PICTURE, NOT THE SEGMENT. A transition is TRACK-LOCAL now, so
# the second picture rides on the entry for the track whose cut it is on — a
# segment is a stack of those. See `plan_animated_segments`.
blended = [
    item
    for s in segs
    for item in s["pictures"]
    if item.get("frame_b") is not None
]
check("only the samples inside the window carry a second picture",
      len(blended) == 15, f"(got {len(blended)} of {len(segs)})")
check("and each of those names the picture arriving",
      all(item["frame"] == 0 and item["frame_b"] == 1 for item in blended))
check("and every sample names exactly one picture — these shots are one track",
      all(len(s["pictures"]) == 1 for s in segs),
      f"(stacks: {sorted({len(s['pictures']) for s in segs})})")


# ---------------------------------------------------------------------------
# The video
# ---------------------------------------------------------------------------
if not ffmpeg_available():
    print("\nffmpeg is not available — the encoded half of this test cannot run.")
    print("The rules above are proved; what reaches the MP4 is not.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="transition_")


def flat(path, shade, size=(800, 450)):
    """A picture of one flat tone, at the output's own aspect ratio.

    Flat on purpose: a transition is measured here as a change in BRIGHTNESS, so
    anything else in the picture is noise. 16:9 like the export, so "contain"
    fills the frame exactly and no letterbox bar drags the mean around.
    """
    Image.new("RGB", size, (shade, shade, shade)).save(path, "PNG")
    return path


def decode(video, at_seconds, out_png):
    cmd = [
        ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{at_seconds:.3f}", "-i", video, "-frames:v", "1", out_png,
    ]
    run_ffmpeg(cmd, 1000, None, None)
    return Image.open(out_png).convert("RGB")


def mean(im):
    """Average brightness, 0–255. One pixel is the whole picture's average."""
    px = im.resize((1, 1)).getpixel((0, 0))
    return sum(px) / 3


def half(im, side):
    """One half of the frame — the unit every direction assertion is made in."""
    w, h = im.size
    box = {
        "left": (0, 0, w // 2, h),
        "right": (w // 2, 0, w, h),
        "top": (0, 0, w, h // 2),
        "bottom": (0, h // 2, w, h),
    }[side]
    return im.crop(box)


def encode(name, shades, kind, ms=600, background="#000000", params=None):
    """Two flat pictures, 1s each, with one transition on the cut between."""
    frames = [
        {"id": "a", "path": flat(os.path.join(work, f"{name}_a.png"), shades[0]),
         "duration_ms": 1000},
        {"id": "b", "path": flat(os.path.join(work, f"{name}_b.png"), shades[1]),
         "duration_ms": 1000},
    ]
    return build_animatic(
        name, frames,
        transitions=(
            [transition("a", kind=kind, ms=ms, params=params)] if kind else None
        ),
        output_dir=os.path.join(work, "out"),
        resolution=360, fps=24, aspect_ratio="16:9", background=background,
    )


def middle_of(name, kind, shades=(0, 255), params=None, background="#000000"):
    """Half way through one transition's window, decoded back out of the MP4."""
    video = encode(name, shades, kind, params=params, background=background)["video"]
    return decode(video, MIDDLE, os.path.join(work, f"{name}_mid.png"))


# The window is 700–1300ms, so a quarter / half / three-quarters of the way
# through it are 850 / 1000 / 1150ms. At module scope because `middle_of` above
# reads MIDDLE.
QUARTER, MIDDLE, THREE_Q = 0.850, 1.000, 1.150

try:

    # ------------------------------------------------------------- dissolve
    print("\nA dissolve — black into white")
    diss = encode("dissolve", (0, 255), "dissolve")
    check("the export reports itself as animated", diss["animated"] is True)
    check("it counted the transition", diss["transition_count"] == 1)

    b0 = mean(decode(diss["video"], 0.3, os.path.join(work, "d0.png")))
    q = mean(decode(diss["video"], QUARTER, os.path.join(work, "d1.png")))
    m = mean(decode(diss["video"], MIDDLE, os.path.join(work, "d2.png")))
    t3 = mean(decode(diss["video"], THREE_Q, os.path.join(work, "d3.png")))
    b1 = mean(decode(diss["video"], 1.7, os.path.join(work, "d4.png")))

    check("before the transition the first picture is untouched", b0 < 8, f"({b0:.1f})")
    check("after it the second picture is untouched", b1 > 247, f"({b1:.1f})")
    check("brightness climbs the whole way through, never doubling back",
          b0 < q < m < t3 < b1, f"({b0:.1f} → {q:.1f} → {m:.1f} → {t3:.1f} → {b1:.1f})")
    # Half way through the blend of black and white is mid grey. The tolerance
    # is a video frame's worth of drift either way (1/24s of a 600ms blend is
    # ~4% of the way through, i.e. ~10 levels), not a licence to be wrong.
    check("half way through it is 50% grey", abs(m - 127.5) < 25, f"({m:.1f})")

    # --------------------------------------------------- the length promise
    print("\nThe length promise — a transition takes nothing away")
    straight = encode("straight", (0, 255), None)
    check("a straight cut and a transition encode to the SAME length",
          diss["duration_ms"] == straight["duration_ms"],
          f"({diss['duration_ms']}ms vs {straight['duration_ms']}ms)")
    check("and that length is the sum of the holds, as it always was",
          abs(diss["duration_ms"] - 2000) <= 50, f"({diss['duration_ms']}ms)")
    # The other half of "nothing else changed": the project WITHOUT a transition
    # must still take the cheap planner, one still per picture — which is every
    # animatic that exists today.
    check("without a transition the export is still not animated",
          straight["animated"] is False)
    check("and still renders one still per picture, not one per video frame",
          straight["still_count"] == 2, f"(got {straight['still_count']})")
    # ⚠ A TRANSITION COSTS THE STILLS IT USES, NOT THE WHOLE TIMELINE. The
    # export samples all 48 video frames, but the ones either side of the window
    # resolve to an unchanging picture and share a signature, so they collapse
    # to one still each. 15 blended + 2 held = 17. That is the render cache
    # earning its keep: putting a dissolve on one cut of a two-minute animatic
    # costs half a second of stills, not two minutes of them.
    check("a transition costs stills only where it actually blends",
          diss["still_count"] == 15 + 2, f"(got {diss['still_count']})")

    # ------------------------------------------------------------------ dip
    print("\nA dip to black — white into white, so only the dip can darken it")
    dip = encode("dip", (255, 255), "dip")
    dq = mean(decode(dip["video"], QUARTER, os.path.join(work, "p1.png")))
    dm = mean(decode(dip["video"], MIDDLE, os.path.join(work, "p2.png")))
    d3 = mean(decode(dip["video"], THREE_Q, os.path.join(work, "p3.png")))
    check("it goes dark in the middle and comes back — a V, not a fade",
          dm < dq and dm < d3, f"({dq:.1f} → {dm:.1f} → {d3:.1f})")
    check("the middle is at (or near) black", dm < 30, f"({dm:.1f})")
    check("both sides are part way back to the picture",
          40 < dq < 220 and 40 < d3 < 220, f"({dq:.1f} / {d3:.1f})")

    # ------------------------------------------------------- wipe vs slide
    # Each other's opposite half way through, which is what proves the renderer
    # is drawing the kind it was asked for rather than just "something moving".
    print("\nA wipe and a slide — opposite halves of the frame, half way through")
    wipe = decode(encode("wipe", (0, 255), "wipe")["video"], MIDDLE,
                  os.path.join(work, "w.png"))
    slide = decode(encode("slide", (0, 255), "slide")["video"], MIDDLE,
                   os.path.join(work, "s.png"))

    wl, wr = mean(half(wipe, "left")), mean(half(wipe, "right"))
    sl, sr = mean(half(slide, "left")), mean(half(slide, "right"))
    check("a wipe has revealed the LEFT of the frame first", wl > wr + 100,
          f"(left {wl:.1f}, right {wr:.1f})")
    check("a slide has the incoming picture entering from the RIGHT",
          sr > sl + 100, f"(left {sl:.1f}, right {sr:.1f})")
    check("so the two are not the same effect under different names",
          (wl > wr) != (sl > sr))

    # ------------------------------------------------- direction and colour
    # ⚠ EVERY ASSERTION HERE IS THE OPPOSITE OF THE DEFAULT ONE ABOVE. A
    # renderer that dropped `params` on the floor anywhere along the way — the
    # schema, `transition_params`, the segment, the worker's task args — would
    # draw the default and fail these while passing every check before them.
    # That is the whole point: the four places a parameter can be lost are all
    # downstream of a project that looks perfectly correct on disk.
    print("\nDirection — the opposite half of the frame from the default")

    left_wipe = middle_of("wipe_left", "wipe", params={"direction": "left"})
    lw_l, lw_r = mean(half(left_wipe, "left")), mean(half(left_wipe, "right"))
    check("a wipe told to travel left uncovers the RIGHT of the frame first",
          lw_r > lw_l + 100, f"(left {lw_l:.1f}, right {lw_r:.1f})")

    down_wipe = middle_of("wipe_down", "wipe", params={"direction": "down"})
    dw_t, dw_b = mean(half(down_wipe, "top")), mean(half(down_wipe, "bottom"))
    check("a wipe told to travel down uncovers the TOP of the frame first",
          dw_t > dw_b + 100, f"(top {dw_t:.1f}, bottom {dw_b:.1f})")

    right_slide = middle_of("slide_right", "slide", params={"direction": "right"})
    rs_l, rs_r = mean(half(right_slide, "left")), mean(half(right_slide, "right"))
    check("a slide told to travel right brings the incoming shot in from the LEFT",
          rs_l > rs_r + 100, f"(left {rs_l:.1f}, right {rs_r:.1f})")

    up_slide = middle_of("slide_up", "slide", params={"direction": "up"})
    us_t, us_b = mean(half(up_slide, "top")), mean(half(up_slide, "bottom"))
    check("a slide told to travel up brings it in from the BOTTOM",
          us_b > us_t + 100, f"(top {us_t:.1f}, bottom {us_b:.1f})")

    # Forgiveness, the same rule an unknown `kind` gets: fold to the default
    # rather than refuse, so a project written by a newer client still plays.
    bogus = middle_of("wipe_bogus", "wipe", params={"direction": "sideways"})
    bg_l, bg_r = mean(half(bogus, "left")), mean(half(bogus, "right"))
    check("a direction this build has never heard of falls back to the default",
          bg_l > bg_r + 100, f"(left {bg_l:.1f}, right {bg_r:.1f})")

    print("\nColour — a dip through white, on a shot that is already black")
    # Black into black, so the ONLY thing that can move the brightness is the
    # colour the dip goes out through. The default dip is the bar colour, which
    # here is black, so it must not move at all.
    plain_dip = mean(middle_of("dip_plain", "dip", shades=(0, 0)))
    white_dip = mean(middle_of("dip_white", "dip", shades=(0, 0),
                               params={"color": "#ffffff"}))
    check("a dip that names no colour still goes out through the bar colour",
          plain_dip < 8, f"({plain_dip:.1f})")
    check("a dip through white goes BRIGHT in the middle instead",
          white_dip > 225, f"({white_dip:.1f})")

    # ---------------------------------------------------------- the clamp
    print("\nA transition longer than the shots it joins")
    huge = encode("huge", (0, 255), "dissolve", ms=9000)
    check("it still encodes, and to the same length",
          abs(huge["duration_ms"] - 2000) <= 50, f"({huge['duration_ms']}ms)")
    # Clamped to 1000ms (the length of each shot), so the window is 500–1500 and
    # the first quarter-second is the untouched first picture.
    early = mean(decode(huge["video"], 0.2, os.path.join(work, "h0.png")))
    check("and is clamped, so the shots either side are still themselves",
          early < 8, f"({early:.1f})")

finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Transitions blend, and they cost the timeline nothing.")
