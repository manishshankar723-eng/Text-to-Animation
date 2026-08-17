"""A VIDEO CLIP on the animatic timeline must export the frames it previewed.

`tests/render_parity.py` proves the two scene evaluators agree about which
moment of a source file is on screen. `tests/animatic_motion_check.py` proves
keyframed numbers reach the MP4. This proves the last link: that the moment the
scene model names actually comes out of the encoder.

The method is the only one worth trusting here — a REAL source video whose every
frame says which second it is, encoded with ffmpeg, put on a timeline, exported,
decoded again, and matched frame for frame. Nothing is stubbed. A clip that
silently exported as one frozen still, or half a second early, or read its
source at the wrong rate, fails here and cannot fail quietly:

1. **Untrimmed** — output second k is source second k.
2. **Trimmed** — `in_ms` moves the first frame to the right part of the source.
3. **Sped up** — `speed: 2` covers twice as much source in the same timeline.
4. **A colour card** — the third clip kind, which has no file at all.
5. **An image-only project is untouched** — every animatic that exists today is
   one of these, so the video path must not have changed anything about them.
6. **Extraction is cached** — the second export re-uses the stills.

    python tests/video_clip_check.py

Needs ffmpeg (imageio-ffmpeg provides one; `GET /health` reports it).
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

import video_frames
from animatic import build_animatic, ffmpeg_available, ffmpeg_exe, run_ffmpeg, source_window

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


if not ffmpeg_available():
    print("ffmpeg is not available — this test cannot run.")
    print("Every assertion here is about what comes out of the encoder, so a")
    print("skip is a real gap, not a pass.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="vclip_")

# ---------------------------------------------------------------------------
# A source video that says what time it is
# ---------------------------------------------------------------------------
# One saturated, well-separated colour per SECOND of source. Colour rather than
# a drawn number because it survives H.264 and can be read with one pixel
# lookup — and because two colours being confused for each other is exactly the
# failure this test exists to catch, they are chosen far apart in RGB.
SECOND_COLOURS = [
    (220, 30, 30),    # 0 red
    (30, 200, 30),    # 1 green
    (40, 60, 230),    # 2 blue
    (230, 220, 40),   # 3 yellow
    (230, 40, 220),   # 4 magenta
    (40, 220, 220),   # 5 cyan
    (240, 140, 20),   # 6 orange
    (150, 60, 200),   # 7 purple
]
SOURCE_SECONDS = len(SECOND_COLOURS)
SOURCE_FPS = 12
# 16:9, matching the export's frame shape, so "contain" fills it exactly and
# there are no letterbox bars to accidentally sample.
SOURCE_SIZE = (640, 360)


def build_source(path: str) -> str:
    """Encode a video whose colour names the second it is at."""
    frames_dir = os.path.join(work, "srcframes")
    os.makedirs(frames_dir, exist_ok=True)
    n = 0
    for second, colour in enumerate(SECOND_COLOURS):
        for _ in range(SOURCE_FPS):
            n += 1
            Image.new("RGB", SOURCE_SIZE, colour).save(
                os.path.join(frames_dir, f"{n:05d}.png"), "PNG"
            )
    cmd = [
        ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-framerate", str(SOURCE_FPS),
        "-i", os.path.join(frames_dir, "%05d.png"),
        "-c:v", "libx264",
        # Lossless-ish: the whole test reads colours back, and a low bit rate
        # would blur the one-frame transitions between them into each other.
        "-crf", "10", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(SOURCE_FPS),
        path,
    ]
    run_ffmpeg(cmd, SOURCE_SECONDS * 1000)
    return path


def nearest_second(pixel) -> int:
    """Which of the source's colours this pixel is — i.e. which source second."""
    r, g, b = pixel[:3]
    best, best_d = -1, None
    for i, (cr, cg, cb) in enumerate(SECOND_COLOURS):
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def read_seconds(video_path: str, at_ms: list[int], tag: str) -> list[int]:
    """Decode `video_path` and say which SOURCE second each moment shows.

    Sampled at the middle of each output second on purpose: H.264 smears the
    single frame either side of a colour change, and a sample landing exactly on
    a boundary would read whichever way the encoder happened to round.
    """
    out = os.path.join(work, f"decode_{tag}")
    info = video_frames.extract_frames(video_path, SOURCE_FPS, out, start_ms=0)
    read = []
    for ms in at_ms:
        still = video_frames.frame_path(info, ms)
        with Image.open(still) as im:
            read.append(nearest_second(im.convert("RGB").getpixel((im.width // 2, im.height // 2))))
    return read


def export(job: str, frames: list[dict], **kw) -> dict:
    """Encode one project, with the settings every case here shares."""
    return build_animatic(
        job, frames,
        aspect_ratio="16:9", resolution=360, fps=SOURCE_FPS, quality="high",
        output_dir=os.path.join(work, "out"),
        **kw,
    )


print("Video clips on the animatic timeline\n")
print("Building a source video whose colour names the second…")
SOURCE = build_source(os.path.join(work, "source.mp4"))
check("the source video encoded", os.path.isfile(SOURCE))

measured = video_frames.probe_duration(SOURCE)
check("probe_duration measures it without ffprobe",
      abs(measured - SOURCE_SECONDS * 1000) <= 200, f"(got {measured}ms)")


def video_clip(**kw) -> dict:
    """One video clip, with the fields the exporter resolves for it."""
    return {
        "kind": "video", "video_path": SOURCE, "label": "",
        "in_ms": 0, "out_ms": None, "speed": 1.0, "color": "#000000",
        "path": None, **kw,
    }


# ---------------------------------------------------------------------------
# 1. Untrimmed — output second k is source second k
# ---------------------------------------------------------------------------
print("\nA whole video clip, untrimmed")
result = export("untrimmed", [video_clip(duration_ms=SOURCE_SECONDS * 1000)])
check("it reports itself as animated (a video is never a held still)",
      result["animated"] is True)
check("it counted the clip as video", result["video_clip_count"] == 1)
check("the export is the length of the clip",
      abs(result["duration_ms"] - SOURCE_SECONDS * 1000) <= 100,
      f"({result['duration_ms']}ms)")

mid_of_each = [s * 1000 + 500 for s in range(SOURCE_SECONDS)]
got = read_seconds(result["video"], mid_of_each, "untrimmed")
check("every output second shows the matching source second",
      got == list(range(SOURCE_SECONDS)), f"(read {got})")

# THE regression this whole phase turns on: without `source_ms` in the render
# cache key every sample of the clip signs identically, one still is rendered
# and reused, and the video exports as a FREEZE FRAME while the preview plays.
check("the clip is not one frozen still repeated",
      len(set(got)) == SOURCE_SECONDS, f"(only {len(set(got))} distinct picture(s))")
check("it rendered a still per distinct moment, not one for the whole clip",
      result["still_count"] > SOURCE_SECONDS, f"(still_count={result['still_count']})")

# ---------------------------------------------------------------------------
# 2. Trimmed — in_ms decides which source frame the clip OPENS on
# ---------------------------------------------------------------------------
print("\nTrimmed with an in point")
result = export("trimmed", [video_clip(duration_ms=2000, in_ms=3000)])
got = read_seconds(result["video"], [500, 1500], "trimmed")
check("a clip trimmed to 3s in opens on source second 3",
      got == [3, 4], f"(read {got})")

# And an out point CLAMPS rather than running on into trimmed-off footage.
result = export("trimmed_out", [video_clip(duration_ms=3000, in_ms=1000, out_ms=3000)])
got = read_seconds(result["video"], [500, 1500, 2500], "trimmed_out")
check("past the out point the clip HOLDS its last source frame",
      got == [1, 2, 2], f"(read {got})")

# ---------------------------------------------------------------------------
# 3. Speed — the decision of the phase, measured
# ---------------------------------------------------------------------------
print("\nSped up")
result = export("fast", [video_clip(duration_ms=4000, in_ms=0, speed=2.0)])
check("speed does not change how long the clip is on the timeline",
      abs(result["duration_ms"] - 4000) <= 100, f"({result['duration_ms']}ms)")
got = read_seconds(result["video"], [250, 1250, 2250, 3250], "fast")
# 4s of timeline at speed 2 reads 8s of source, so each output second covers two
# source seconds: the samples land in source seconds 0, 2, 4 and 6.
check("speed 2 covers twice as much source in the same timeline",
      got == [0, 2, 4, 6], f"(read {got})")

result = export("slow", [video_clip(duration_ms=4000, in_ms=0, speed=0.5)])
got = read_seconds(result["video"], [500, 1500, 2500, 3500], "slow")
# At half speed 4s of timeline reads only 2s of source.
check("speed 0.5 covers half as much source in the same timeline",
      got == [0, 0, 1, 1], f"(read {got})")

# ---------------------------------------------------------------------------
# 4. A colour card — the clip kind with no file behind it
# ---------------------------------------------------------------------------
print("\nA colour card")
result = export(
    "card",
    [{"kind": "color", "color": "#c8501e", "duration_ms": 1000, "label": "",
      "path": None, "video_path": None, "in_ms": 0, "out_ms": None, "speed": 1.0}],
)
out = os.path.join(work, "decode_card")
info = video_frames.extract_frames(result["video"], SOURCE_FPS, out, start_ms=0)
with Image.open(video_frames.frame_path(info, 500)) as im:
    pixel = im.convert("RGB").getpixel((im.width // 2, im.height // 2))
check("a colour card exports as that colour, edge to edge",
      all(abs(a - b) < 14 for a, b in zip(pixel, (0xC8, 0x50, 0x1E))), f"(got {pixel})")

# ---------------------------------------------------------------------------
# 5. Three kinds on ONE timeline — the "done when" of the phase
# ---------------------------------------------------------------------------
print("\nAn image, a video and a colour card on one timeline")
still = os.path.join(work, "still.png")
Image.new("RGB", SOURCE_SIZE, (12, 12, 12)).save(still, "PNG")
result = export("mixed", [
    {"kind": "image", "path": still, "duration_ms": 1000, "label": "",
     "video_path": None, "in_ms": 0, "out_ms": None, "speed": 1.0, "color": "#000000"},
    video_clip(duration_ms=2000, in_ms=4000),
    {"kind": "color", "color": "#20c060", "duration_ms": 1000, "label": "",
     "path": None, "video_path": None, "in_ms": 0, "out_ms": None, "speed": 1.0},
])
check("all three clips survived the export", result["frame_count"] == 3)
check("the mixed timeline is the sum of its clips",
      abs(result["duration_ms"] - 4000) <= 100, f"({result['duration_ms']}ms)")
out = os.path.join(work, "decode_mixed")
info = video_frames.extract_frames(result["video"], SOURCE_FPS, out, start_ms=0)
reads = []
for ms in (500, 1500, 2500, 3500):
    with Image.open(video_frames.frame_path(info, ms)) as im:
        reads.append(im.convert("RGB").getpixel((im.width // 2, im.height // 2)))
check("the still is first", all(v < 45 for v in reads[0]), f"({reads[0]})")
check("the video plays in the middle, at its trimmed in point",
      nearest_second(reads[1]) == 4 and nearest_second(reads[2]) == 5,
      f"({nearest_second(reads[1])} then {nearest_second(reads[2])})")
check("the colour card is last",
      all(abs(a - b) < 20 for a, b in zip(reads[3], (0x20, 0xC0, 0x60))), f"({reads[3]})")

# ---------------------------------------------------------------------------
# 6. An image-only project is untouched
# ---------------------------------------------------------------------------
# Every animatic that exists today is one of these. The cheap planner must still
# run for them, and nothing about the video path may have reached them.
print("\nAn image-only project — must be exactly what it always was")
result = export("stills", [
    {"path": still, "duration_ms": 1000, "label": "one"},
    {"path": still, "duration_ms": 1000, "label": "two"},
])
check("a project of stills is still NOT animated", result["animated"] is False)
check("no video clips were counted", result["video_clip_count"] == 0)
check("nothing was extracted", result["extracted_still_count"] == 0)
# Two pictures → two rendered stills. The number that matters is that it is not
# 24 (two seconds at 12fps), which is what it would be if a video clip anywhere
# in the codebase had pushed these onto the per-frame planner.
check("one still per picture, not one per video frame", result["still_count"] == 2,
      f"(still_count={result['still_count']}, per-frame would be {2 * SOURCE_FPS})")
check("it is still two seconds long",
      abs(result["duration_ms"] - 2000) <= 60, f"({result['duration_ms']}ms)")
# Frames carrying NONE of the new fields at all — the shape a caller written
# before this phase passes. It must not raise, and it must not become animated.
result = export("legacy", [{"path": still, "duration_ms": 1500}])
check("a frame with no clip fields at all still exports",
      result["animated"] is False and abs(result["duration_ms"] - 1500) <= 60)

# ---------------------------------------------------------------------------
# 7. Extraction is cached
# ---------------------------------------------------------------------------
print("\nExtraction is cached, so a second export is cheaper")
cache = os.path.join(work, "cache_probe")
first_start = time.perf_counter()
one = video_frames.extract_frames(SOURCE, SOURCE_FPS, cache, start_ms=0, span_ms=4000)
first_s = time.perf_counter() - first_start
second_start = time.perf_counter()
two = video_frames.extract_frames(SOURCE, SOURCE_FPS, cache, start_ms=0, span_ms=4000)
second_s = time.perf_counter() - second_start
check("the first extraction decodes", one["cached"] is False and one["count"] > 0)
check("the second is served from the cache", two["cached"] is True)
check("and returns the same stills", two["count"] == one["count"] and two["dir"] == one["dir"])
check("the cached call is faster", second_s < first_s, f"({first_s:.2f}s → {second_s:.2f}s)")
print(f"       first {first_s:.2f}s · cached {second_s:.2f}s · {one['count']} still(s)")

# A DIFFERENT range of the same file is its own extraction — sharing one would
# serve the first clip's stills for the second clip's timeline, which reads as a
# rendering bug rather than a caching one.
other = video_frames.extract_frames(SOURCE, SOURCE_FPS, cache, start_ms=4000, span_ms=2000)
check("a different source range is a different extraction",
      other["dir"] != one["dir"] and other["cached"] is False)

# ---------------------------------------------------------------------------
# 8. The window the exporter extracts is the window it then asks for
# ---------------------------------------------------------------------------
# These two being out of step is how you extract three seconds and look up the
# fourth — a clip that plays and then freezes part way through.
print("\nThe extracted range matches the range the scene model asks for")
start, span = source_window(video_clip(duration_ms=2000, in_ms=1000, speed=2.0))
check("speed widens the window that gets extracted",
      start == 1000 and abs(span - 4000) <= 100, f"(start={start} span={span})")
start, span = source_window(video_clip(duration_ms=8000, in_ms=0, out_ms=2000))
check("an out point narrows it, however long the clip is held",
      start == 0 and span <= 2100, f"(start={start} span={span})")

shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Video clips export the frames the scene model named.")
