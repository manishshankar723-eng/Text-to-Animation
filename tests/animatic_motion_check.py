"""The exported MP4 must actually MOVE — and the ones that never moved must not change.

`tests/render_parity.py` proves the two scene evaluators agree about numbers.
This proves the numbers reach the video: it encodes real MP4s with ffmpeg,
decodes frames back out, and measures them.

Two halves, and the second matters as much as the first:

1. **A keyframed project moves.** Decoded frames at different times differ, and
   they differ in the direction the keyframes asked for.
2. **A project with no keyframes is untouched.** Every animatic that exists today
   is in this category, so the new planner must never run for them and the old
   one must produce what it always produced — same segment count, same length.

    python tests/animatic_motion_check.py

Needs ffmpeg (imageio-ffmpeg provides one; `GET /health` reports it).
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw

from animatic import (
    build_animatic,
    ffmpeg_available,
    ffmpeg_exe,
    plan_animated_segments,
    plan_segments,
    run_ffmpeg,
)

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


if not ffmpeg_available():
    print("ffmpeg is not available — this test cannot run.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="motion_")


def source_image(path, size=(800, 450)):
    """A picture with a dark square in the MIDDLE, covering a fifth of each side.

    Centred on purpose. A zoom here is about the centre of the canvas — the
    source's own centre stays pinned and everything else spreads outward — so a
    mark in a corner is carried off the edge as the picture grows, and measuring
    it would say the subject got SMALLER. That is correct behaviour and a
    thoroughly misleading thing to assert against. A centred mark grows with the
    zoom, which is the property this test is actually about.

    A flat image would survive any transform looking identical and the test
    would pass whether or not the transform ran at all.
    """
    w, h = size
    im = Image.new("RGB", size, (240, 240, 240))
    ImageDraw.Draw(im).rectangle(
        (int(w * 0.4), int(h * 0.4), int(w * 0.6), int(h * 0.6)), fill=(10, 10, 10)
    )
    im.save(path, "PNG")
    return path


def decode(video, at_seconds, out_png):
    """Pull one frame out of the encoded video, so the assertions are on the
    real file rather than on what the renderer believed it wrote."""
    cmd = [
        ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{at_seconds:.3f}", "-i", video, "-frames:v", "1", out_png,
    ]
    run_ffmpeg(cmd, 1000, None, None)
    return Image.open(out_png).convert("RGB")


def difference(a, b):
    """Mean absolute difference per channel, 0–255. Two identical frames are 0."""
    pa, pb = a.load(), b.load()
    w, h = a.size
    step = max(1, w // 60)
    total = count = 0
    for x in range(0, w, step):
        for y in range(0, h, step):
            ra, ga, ba = pa[x, y]
            rb, gb, bb = pb[x, y]
            total += abs(ra - rb) + abs(ga - gb) + abs(ba - bb)
            count += 3
    return total / max(1, count)


try:
    src = source_image(os.path.join(work, "src.png"))

    # ---------------------------------------------------------------- static
    print("\nA project with no keyframes — must behave exactly as it always did")
    static_frames = [
        {"id": "a", "path": src, "duration_ms": 1000, "label": "A"},
        {"id": "b", "path": src, "duration_ms": 1000, "label": "B"},
    ]
    segs, total = plan_segments(static_frames, [], None, [], [])
    check("one segment per picture, nothing else", len(segs) == 2, f"(got {len(segs)})")
    check("length is the sum of the holds", total == 2000, f"(got {total})")

    still = build_animatic(
        "static", static_frames, output_dir=os.path.join(work, "out"),
        resolution=360, fps=24, aspect_ratio="16:9",
    )
    check("the export reports itself as NOT animated", still["animated"] is False)
    # Two pictures, so two stills — NOT one per video frame, which is the whole
    # point of keeping the old planner for projects that don't move. (They share
    # a source file here, but a frame's index is part of the cache key, because
    # two frames pointing at one image can still carry different labels.)
    check("it rendered one still per picture, not one per video frame",
          still["still_count"] == 2, f"(got {still['still_count']})")
    check("the video is the length of the timeline",
          abs(still["duration_ms"] - 2000) <= 50, f"(got {still['duration_ms']}ms)")

    a0 = decode(still["video"], 0.2, os.path.join(work, "s0.png"))
    a1 = decode(still["video"], 1.5, os.path.join(work, "s1.png"))
    check("nothing moves between its two pictures (they share one image)",
          difference(a0, a1) < 1.0, f"(diff {difference(a0, a1):.2f})")

    # -------------------------------------------------------------- animated
    print("\nA keyframed project — must actually move")
    # A push from 1.0 to 2.0 over the whole two seconds. The mark is off-centre,
    # so a real zoom moves it toward the edge; a transform that silently did
    # nothing would leave it exactly where it started.
    moving_frames = [
        {
            "id": "a", "path": src, "duration_ms": 2000, "label": "A",
            "keyframes": {"scale": [{"t": 0, "v": 1.0}, {"t": 2000, "v": 2.0}]},
        }
    ]
    segs2, total2 = plan_animated_segments(moving_frames, [], None, [], [], 24)
    check("sampled once per video frame", len(segs2) == 48, f"(got {len(segs2)})")
    check("the samples cover exactly the timeline, no more",
          abs((segs2[-1]["start_ms"] + segs2[-1]["duration_ms"]) - 2000) < 0.001)
    check("every sample is a distinct picture, so none is wrongly reused",
          len({s["signature"] for s in segs2}) == 48)

    moved = build_animatic(
        "moving", moving_frames, output_dir=os.path.join(work, "out"),
        resolution=360, fps=24, aspect_ratio="16:9",
    )
    check("the export reports itself as animated", moved["animated"] is True)
    check("it rendered a still per sampled frame", moved["still_count"] == 48,
          f"(got {moved['still_count']})")
    # THE REGRESSION THIS EXISTS FOR: the per-entry duration floor used to be
    # 0.1s. At 24fps each sample is 1/24s, so every one would have been stretched
    # to 100ms and a 2s animatic would have encoded as 4.8s.
    check("the video is 2s, not stretched by the segment-duration floor",
          abs(moved["duration_ms"] - 2000) <= 50, f"(got {moved['duration_ms']}ms)")

    b0 = decode(moved["video"], 0.1, os.path.join(work, "m0.png"))
    b1 = decode(moved["video"], 1.0, os.path.join(work, "m1.png"))
    b2 = decode(moved["video"], 1.9, os.path.join(work, "m2.png"))
    d01, d12, d02 = difference(b0, b1), difference(b1, b2), difference(b0, b2)
    check("the picture changes across the push", d02 > 5.0, f"(diff {d02:.2f})")
    check("it changes CONTINUOUSLY, not in one jump",
          d01 > 2.0 and d12 > 2.0, f"(early {d01:.2f}, late {d12:.2f})")
    check("the end differs from the start more than either half does",
          d02 > d01 and d02 > d12, f"({d02:.2f} vs {d01:.2f}/{d12:.2f})")

    # A zoom must GROW the mark, not merely change the pixels. The dark square
    # covers more of the frame at 2× than at 1×; if the transform were being
    # dropped, or applied backwards, this is what would catch it.
    def dark_fraction(im):
        px = im.load()
        w, h = im.size
        return sum(
            1
            for x in range(0, w, 3)
            for y in range(0, h, 3)
            if sum(px[x, y]) < 200
        ) / len(range(0, w, 3)) / len(range(0, h, 3))

    check("the zoom makes the subject bigger, not smaller",
          dark_fraction(b2) > dark_fraction(b0) * 1.3,
          f"(start {dark_fraction(b0):.3f} → end {dark_fraction(b2):.3f})")

    # ------------------------------------------------------------- fade path
    print("\nA keyframed fade — the other renderer branch")
    fading = [
        {
            "id": "a", "path": src, "duration_ms": 1000,
            "keyframes": {"opacity": [{"t": 0, "v": 1.0}, {"t": 1000, "v": 0.0}]},
        }
    ]
    faded = build_animatic(
        "fading", fading, output_dir=os.path.join(work, "out"),
        resolution=360, fps=24, aspect_ratio="16:9", background="#000000",
    )
    f0 = decode(faded["video"], 0.05, os.path.join(work, "f0.png"))
    f1 = decode(faded["video"], 0.95, os.path.join(work, "f1.png"))
    check("a fade to 0 ends darker than it started",
          sum(f1.resize((1, 1)).getpixel((0, 0))) < sum(f0.resize((1, 1)).getpixel((0, 0))),
          f"(start {f0.resize((1,1)).getpixel((0,0))} → end {f1.resize((1,1)).getpixel((0,0))})")

    # -------------------------------------------------------- text fade path
    print("\nA keyframed CAPTION — the text renderer's opacity")
    # A caption over a plain picture, fading from invisible to full. The picture
    # itself never moves, so anything that changes in the bottom strip of the
    # frame is the caption and nothing else.
    plain = source_image(os.path.join(work, "plain.png"))
    captioned = build_animatic(
        "caption",
        [{"id": "a", "path": plain, "duration_ms": 1000}],
        texts=[
            {
                "id": "t1", "text": "Fading in", "start_ms": 0, "duration_ms": 1000,
                "position": "bottom", "align": "center", "size": "large",
                "color": "#ffffff", "backdrop": "scrim", "opacity": 1.0,
                "keyframes": {"opacity": [{"t": 0, "v": 0.0}, {"t": 1000, "v": 1.0}]},
            }
        ],
        output_dir=os.path.join(work, "out"),
        resolution=360, fps=24, aspect_ratio="16:9",
    )
    check("a keyframed caption makes the export animated", captioned["animated"] is True)

    def bottom_strip(im):
        w, h = im.size
        return im.crop((0, int(h * 0.75), w, h))

    c0 = bottom_strip(decode(captioned["video"], 0.05, os.path.join(work, "c0.png")))
    c1 = bottom_strip(decode(captioned["video"], 0.95, os.path.join(work, "c1.png")))
    check("the caption is not there at the start but is at the end",
          difference(c0, c1) > 4.0, f"(diff {difference(c0, c1):.2f})")
    # The scrim is dark, so the strip DARKENS as the caption arrives. This is
    # what separates "the caption faded in" from "something changed down there".
    def mean(im):
        px = im.resize((1, 1)).getpixel((0, 0))
        return sum(px) / 3
    check("the strip darkens as the scrim fades up",
          mean(c1) < mean(c0), f"(start {mean(c0):.1f} → end {mean(c1):.1f})")

    # ------------------------------------------ caption zoom and turn (Phase 1)
    print("\nA caption that only POPS AND TURNS — the preset shelves' whole point")
    # ⚠ THIS IS THE END-TO-END VERSION OF THE `scene_signature` BUG. `scale` was
    # animatable on a caption from Phase 5 and was missing from the render cache
    # key, so a caption animating only its size resolved to one signature for its
    # whole life and `build_animatic` rendered ONE still and held it: the monitor
    # popped, the MP4 sat dead still. `tests/preset_check.py` proves the key now
    # changes; this proves the FILE does. It is deliberately a FLOW caption with
    # no opacity and no position keys at all — anything else would move the
    # picture for some other reason and hide the fault all over again.
    popping = build_animatic(
        "popping",
        [{"id": "a", "path": plain, "duration_ms": 1000}],
        texts=[
            {
                "id": "t1", "text": "POP", "start_ms": 0, "duration_ms": 1000,
                "position": "middle", "align": "center", "size": "large",
                # ⚠ A BIG CAPTION ON PURPOSE. At the "large" preset a three
                # letter word on a 360p frame is about 25px tall and covers well
                # under a percent of the band, so a frame-average would move by a
                # fraction either way whether the animation ran or not — a test
                # that passes for the wrong reason as easily as the right one.
                # 120px at 1080p is a title, and a title growing four times over
                # is a difference no threshold has to be argued about.
                "size_px": 120,
                "color": "#ffffff", "backdrop": "box", "opacity": 1.0,
                "scale": 1.0, "rotation": 0.0,
                "keyframes": {
                    "scale": [
                        {"t": 0, "v": 0.4, "ease": "ease-out"},
                        {"t": 1000, "v": 1.6, "ease": "linear"},
                    ],
                    "rotation": [
                        {"t": 0, "v": -25.0, "ease": "ease-out"},
                        {"t": 1000, "v": 25.0, "ease": "linear"},
                    ],
                },
            }
        ],
        output_dir=os.path.join(work, "out"),
        resolution=360, fps=24, aspect_ratio="16:9",
    )
    check("a caption that only zooms and turns makes the export animated",
          popping["animated"] is True)

    def middle_band(im):
        w, h = im.size
        return im.crop((0, int(h * 0.3), w, int(h * 0.7)))

    p0 = middle_band(decode(popping["video"], 0.05, os.path.join(work, "p0.png")))
    p1 = middle_band(decode(popping["video"], 0.95, os.path.join(work, "p1.png")))

    def ink(im):
        """How much of the band the caption covers.

        ⚠ COUNTED, NOT AVERAGED. A caption is a small, very dark object on a
        pale picture: growing it fourfold moves the band's MEAN by well under a
        point, which is a threshold nobody could defend, while the number of
        pixels it covers roughly quadruples. The question is "did it get
        bigger", so the measure is area.
        """
        px = im.convert("RGB").load()
        w, h = im.size
        return sum(
            1
            for x in range(0, w, 2)
            for y in range(0, h, 2)
            if sum(px[x, y]) < 240  # the box backdrop is near-black
        )

    # ⚠ THE NUMBER THAT WOULD HAVE BEEN EQUAL. Before the cache key learned about
    # `scale`, both of these frames were the SAME rendered still — the exporter
    # could not tell the two moments apart — so these two counts were identical
    # and the failure had no other symptom anywhere.
    check("…and the two ends of it are genuinely different pictures",
          difference(p0, p1) > 0.15, f"(diff {difference(p0, p1):.2f})")
    # A caption running 40% → 160% covers roughly sixteen times the area. Asking
    # for twice is the loosest assertion that still cannot pass on a still frame.
    check("the caption really is bigger at the end than at the start",
          ink(p1) > ink(p0) * 2, f"(start {ink(p0)}px → end {ink(p1)}px)")

finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Static exports are unchanged; keyframed exports move.")
