"""Phase 8: exports go WIDE, and the presets produce what they claim.

Three things were added to the exporter and each one can fail silently, which is
why they are all checked against real encoded files rather than against a plan:

1. **The still-render loop runs across processes.** The danger is not that it
   fails — it is that it succeeds and produces a DIFFERENT video. So a parallel
   export and a serial one are run over the same project and their MP4s are
   compared byte for byte. Anything that made rendering order matter (names
   assigned as stills finish, a shared cache, a race on the build directory)
   shows up here as two different hashes.
2. **Stop still stops.** A worker cannot see the cancel flag — it lives in the
   server process — so the check happens in the parent between results. The test
   trips the flag once a handful of stills exist on disk and asserts the export
   gave up with most of them still unrendered, and wrote nothing.
3. **A preset produces the file it names.** Each one is exported and the RESULT
   is measured: the size out of ffmpeg's own banner (there is no ffprobe on an
   imageio-ffmpeg install — see video_frames.py), and the PNG out of Pillow.

Plus the twin: `export_presets.py` and `client/src/animatic/export_presets.js`
are compared field for field by running the JS through node, because the dialog
promises a size and a frame rate before anything is encoded.

    python tests/export_perf_check.py

Needs ffmpeg (imageio-ffmpeg provides one; `GET /health` reports it). The node
half is skipped, loudly, where there is no node.
"""

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw

import export_presets
from animatic import build_animatic, export_workers, ffmpeg_available, ffmpeg_exe

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


if not ffmpeg_available():
    print("ffmpeg is not available — this test cannot run.")
    print("Every assertion here is about a file that came out of the encoder, so")
    print("a skip is a real gap, not a pass.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="exportperf_")
out_dir = os.path.join(work, "out")


def source_png(name: str, colour: tuple[int, int, int]) -> str:
    """A picture with enough detail in it to cost something to resize."""
    path = os.path.join(work, name)
    im = Image.new("RGB", (1600, 900), colour)
    pen = ImageDraw.Draw(im)
    for i in range(0, 1600, 24):
        pen.line([(i, 0), (i - 300, 900)], fill=((i * 7) % 256, 40, 200 - (i % 200)), width=3)
    im.save(path)
    return path


# ---------------------------------------------------------------------------
# How big / how fast, without an ffprobe
# ---------------------------------------------------------------------------
def media_info(path: str) -> dict:
    """{"width", "height", "fps"} read out of ffmpeg's own banner.

    The same trick `video_frames.probe_duration` uses and for the same reason:
    `imageio-ffmpeg` ships one binary and it is not ffprobe. `-t 0` means the
    banner is printed without a frame being decoded.
    """
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", path, "-t", "0", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    text = proc.stderr or ""
    import re

    size = re.search(r",\s(\d{2,5})x(\d{2,5})[\s,]", text)
    rate = re.search(r"(\d+(?:\.\d+)?)\s+fps", text)
    return {
        "width": int(size.group(1)) if size else 0,
        "height": int(size.group(2)) if size else 0,
        "fps": float(rate.group(1)) if rate else 0.0,
    }


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 1. The preset table is the same table on both sides
# ---------------------------------------------------------------------------
print("\nThe preset table matches its JS twin")

NODE_HARNESS = """
import { PRESETS, applyPreset, matchPreset, normaliseContainer } from "%s";
const out = {
  presets: PRESETS,
  applied: applyPreset("tiktok", { aspect_ratio: "16:9", background: "#123456", fps: 24 }),
  matched: matchPreset({ aspect_ratio: "9:16", resolution: 1080, fps: 30, quality: "high",
                         container: "mp4" }),
  custom: matchPreset({ aspect_ratio: "9:16", resolution: 720, fps: 30, quality: "high",
                        container: "mp4" }),
  unknown: normaliseContainer("webm"),
};
process.stdout.write(JSON.stringify(out));
"""


def run_node() -> dict | None:
    """Run the browser's own module under node, or None if there is no node."""
    if not shutil.which("node"):
        return None
    # `as_uri()`, not a bare path: node's ESM loader takes a URL, and on Windows
    # `c:\…` is read as a protocol called "c". Same fix as audio_mix_check.py.
    from pathlib import Path

    module = Path(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ) / "client" / "src" / "animatic" / "export_presets.js"
    harness = os.path.join(work, "preset_harness.mjs")
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write(NODE_HARNESS % module.as_uri())
    proc = subprocess.run(
        ["node", harness], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )
    if proc.returncode != 0:
        print("    node said:", (proc.stderr or "").strip()[:400])
        return None
    return json.loads(proc.stdout)


browser = run_node()
if browser is None:
    print("  SKIP  node is not available — the twin could not be compared.")
    print("        The two tables are the ONE thing here that cannot be checked")
    print("        from Python alone; treat this as a gap, not a pass.")
else:
    js = browser["presets"]
    check("the same presets, in the same order",
          [p["id"] for p in js] == [p["id"] for p in export_presets.PRESETS],
          f"({[p['id'] for p in js]})")
    same = True
    for a, b in zip(js, export_presets.PRESETS):
        for field in (*export_presets.SETTING_FIELDS, "audio", "label", "hint"):
            if (field in a) != (field in b) or (field in a and a[field] != b[field]):
                same = False
                print(f"       {b['id']}.{field}: js={a.get(field)!r} py={b.get(field)!r}")
    check("every field of every preset agrees", same)
    check("applying one gives the same settings both sides",
          browser["applied"] == export_presets.apply(
              "tiktok", {"aspect_ratio": "16:9", "background": "#123456", "fps": 24}),
          f"(js={browser['applied']})")
    check("and matching is its exact inverse on both",
          browser["matched"] == "tiktok" and browser["custom"] == "",
          f"(matched={browser['matched']!r} custom={browser['custom']!r})")
    check("an unknown container falls back to mp4 on both",
          browser["unknown"] == export_presets.normalise_container("webm") == "mp4")

# The Python half on its own: applying a preset must not disturb what it does
# not state, which is the whole reason a GIF export doesn't reshape the film.
kept = export_presets.apply("gif", {"aspect_ratio": "21:9", "background": "#ff0000",
                                    "fit": "cover", "include_audio": True})
check("a preset leaves the fields it doesn't state alone",
      kept["aspect_ratio"] == "21:9" and kept["background"] == "#ff0000"
      and kept["fit"] == "cover")
check("a silent container turns the audio flag off rather than ignoring it",
      kept["include_audio"] is False)

# ---------------------------------------------------------------------------
# 2. Parallel and serial produce the SAME MP4
# ---------------------------------------------------------------------------
# The project is deliberately one where nothing can be reused: a caption fading
# across the whole timeline makes every sampled moment a distinct picture, so
# 9 seconds at 24fps is 216 stills that all have to be drawn.
print("\nA parallel export and a serial one are the same video")

src_a = source_png("a.png", (20, 60, 130))
src_b = source_png("b.png", (150, 40, 40))
BIG_FRAMES = [
    {"id": f"f{i}", "path": src_a if i % 2 == 0 else src_b,
     "duration_ms": 1000, "label": f"Shot {i + 1}"}
    for i in range(9)
]
BIG_TEXTS = [
    {
        "id": "t1", "text": "A caption that fades all the way across",
        "start_ms": 0, "duration_ms": 9000, "position": "bottom", "size": "medium",
        # Two keys, so `is_animated` is True and every sample resolves to a
        # different opacity — which is what makes all 216 stills distinct.
        "keyframes": {"opacity": [{"t": 0, "v": 0.0}, {"t": 9000, "v": 1.0}]},
    }
]
EXPORT_ARGS = dict(
    texts=BIG_TEXTS, aspect_ratio="16:9", resolution=720, fps=24, quality="medium",
)

os.environ["ANIMATIC_EXPORT_WORKERS"] = "1"
serial_start = time.perf_counter()
serial = build_animatic("serial", list(BIG_FRAMES), output_dir=out_dir, **EXPORT_ARGS)
serial_s = time.perf_counter() - serial_start

os.environ.pop("ANIMATIC_EXPORT_WORKERS", None)
planned = export_workers(serial["still_count"])
parallel_start = time.perf_counter()
parallel = build_animatic("parallel", list(BIG_FRAMES), output_dir=out_dir, **EXPORT_ARGS)
parallel_s = time.perf_counter() - parallel_start

check("the project really is a big one", serial["still_count"] >= 200,
      f"(only {serial['still_count']} distinct stills)")
check("both exports encoded", bool(serial["video"]) and bool(parallel["video"]))
check("the same number of stills was drawn either way",
      serial["still_count"] == parallel["still_count"],
      f"({serial['still_count']} vs {parallel['still_count']})")
check("the same number of segments was encoded",
      serial["segment_count"] == parallel["segment_count"])
check("THE MP4s ARE BYTE-IDENTICAL", sha256(serial["video"]) == sha256(parallel["video"]),
      f"({sha256(serial['video'])[:16]} vs {sha256(parallel['video'])[:16]})")

print(f"       {serial['still_count']} stills · serial {serial_s:.1f}s "
      f"· {planned} worker(s) {parallel_s:.1f}s")
if planned <= 1:
    print("  SKIP  this machine plans a single worker, so there is no speed-up to")
    print("        measure. The parity check above is the half that matters.")
else:
    check("and the parallel one is faster", parallel_s < serial_s,
          f"({serial_s:.1f}s → {parallel_s:.1f}s)")

# ---------------------------------------------------------------------------
# 3. Stop still stops, mid-batch
# ---------------------------------------------------------------------------
print("\nStop stops an export part-way through the stills")

build_dir = os.path.join(out_dir, "_animatics", "stopped", "_build")
seen: list[int] = []


def stop_after_a_few() -> bool:
    """True once a handful of stills exist — i.e. from INSIDE the render batch.

    Counting files rather than calls is what makes this a mid-BATCH stop: the
    flag cannot trip during planning, because nothing has been drawn yet.
    """
    drawn = len(glob.glob(os.path.join(build_dir, "f*.png")))
    seen.append(drawn)
    return drawn >= 8


stopped = build_animatic(
    "stopped", list(BIG_FRAMES), output_dir=out_dir,
    cancel_check=stop_after_a_few, **EXPORT_ARGS,
)
check("the export reports that it stopped", stopped.get("stopped") is True)
check("and wrote no video", not stopped.get("video"))
check("it had started drawing before it gave up", max(seen or [0]) >= 8,
      f"(peak {max(seen or [0])} stills on disk)")
check("but nowhere near all of it", max(seen or [0]) < serial["still_count"],
      f"(peak {max(seen or [0])} of {serial['still_count']})")
check("and the half-finished build was cleaned up", not os.path.isdir(build_dir))
check("no MP4 was left behind",
      not os.path.isfile(os.path.join(out_dir, "_animatics", "stopped", "animatic.mp4")))

# ---------------------------------------------------------------------------
# 4. Every preset produces the file it claims
# ---------------------------------------------------------------------------
# Short and small, because the ASSERTION is about shape and rate, not about
# spending a minute proving ffmpeg can count.
print("\nEach preset produces the resolution and frame rate it names")

SMALL_FRAMES = [
    {"id": "s1", "path": src_a, "duration_ms": 600, "label": "One"},
    {"id": "s2", "path": src_b, "duration_ms": 600, "label": "Two"},
]

for row in export_presets.PRESETS:
    settings = export_presets.apply(row["id"], {
        "aspect_ratio": "16:9", "resolution": 1080, "fps": 24, "quality": "high",
        "container": "mp4", "background": "#000000", "fit": "contain",
    })
    result = build_animatic(
        f"preset_{row['id']}", list(SMALL_FRAMES), output_dir=out_dir,
        aspect_ratio=settings["aspect_ratio"], resolution=settings["resolution"],
        fps=settings["fps"], quality=settings["quality"],
        container=settings["container"], still_ms=700,
    )
    container = settings["container"]
    path = result["video"]
    wrote = os.path.isfile(path or "")
    check(f"{row['label']}: wrote a .{export_presets.CONTAINER_EXT[container]}",
          wrote and path.endswith(f".{export_presets.CONTAINER_EXT[container]}"),
          f"({path})")
    if not wrote:
        continue

    # The size the preset claims, computed the way the dialog computes it.
    from animatic import resolve_size

    want_w, want_h = resolve_size(settings["aspect_ratio"], settings["resolution"])
    if container == "png":
        with Image.open(path) as im:
            got_w, got_h = im.size
        got_fps = None
    else:
        info = media_info(path)
        got_w, got_h, got_fps = info["width"], info["height"], info["fps"]

    check(f"{row['label']}: {want_w}×{want_h} as claimed",
          (got_w, got_h) == (want_w, want_h), f"(got {got_w}×{got_h})")
    check(f"{row['label']}: the summary agrees with the file",
          (result["width"], result["height"]) == (got_w, got_h),
          f"(summary {result['width']}×{result['height']})")
    if got_fps is not None:
        check(f"{row['label']}: {settings['fps']} fps as claimed",
              abs(got_fps - settings["fps"]) < 0.6, f"(got {got_fps})")
    check(f"{row['label']}: the summary says which container it is",
          result.get("container") == container, f"(got {result.get('container')!r})")

# A still is a PICTURE, not a one-frame film: it must say it has no duration, or
# the editor will offer to play a PNG.
still_result = build_animatic(
    "still_probe", list(SMALL_FRAMES), output_dir=out_dir,
    aspect_ratio="16:9", resolution=720, fps=24, container="png", still_ms=700,
)
check("a still reports no duration and no sound",
      still_result["duration_ms"] == 0 and still_result["has_audio"] is False)
check("a still is one still, whatever the timeline holds",
      still_result["still_count"] == 1, f"({still_result['still_count']})")

# ⚠ THE MOMENT IS THE MOMENT ASKED FOR. `still_ms=700` lands in the SECOND clip
# (the first is held 0–600ms), so a still taken from the head of the timeline
# instead — the easy bug — comes back as the wrong picture entirely.
early = build_animatic(
    "still_early", list(SMALL_FRAMES), output_dir=out_dir,
    aspect_ratio="16:9", resolution=720, fps=24, container="png", still_ms=100,
)
with Image.open(still_result["video"]) as late_im, Image.open(early["video"]) as early_im:
    different = late_im.convert("RGB").tobytes() != early_im.convert("RGB").tobytes()
check("a still at 700ms is the second shot, not the first", different)

# ---------------------------------------------------------------------------
# 5. Preview proxies are smaller, cached, and never the export's business
# ---------------------------------------------------------------------------
print("\nPreview proxies")

import proxies

proxy_cache = os.path.join(work, "proxies")
small = proxies.proxy_for(src_a, proxy_cache, 960)
check("a big picture gets a proxy", small != src_a and os.path.isfile(small))
with Image.open(src_a) as full, Image.open(small) as prox:
    check("its long edge is what was asked for", max(prox.size) == 960,
          f"(got {prox.size})")
    check("the shape is unchanged, so nothing moves in the monitor",
          abs(prox.width / prox.height - full.width / full.height) < 0.002,
          f"({full.size} → {prox.size})")
with Image.open(src_a) as full, Image.open(small) as prox:
    pixels_full = full.width * full.height
    pixels_prox = prox.width * prox.height
# ⚠ PIXELS ARE THE GUARANTEE, BYTES ARE THE USUAL CASE. A quarter of the decoded
# bitmap is a quarter of the memory the editor holds per clip, and that holds for
# every picture. The FILE is a different question: `src_a` here is synthetic line
# art, and resampling hard edges into anti-aliased ones can encode LARGER as PNG.
# So the byte win is asserted below on a photographic-looking source, which is
# what a storyboard panel actually is.
check("it holds far fewer pixels, so far less memory per clip",
      pixels_prox <= pixels_full / 2,
      f"({pixels_full:,} → {pixels_prox:,} px)")
# The rung is a CAP on the long edge, so how much is saved depends on how big the
# source was: a 1920px panel (the export's own long edge) drops to a quarter of
# its bitmap, this 1600px one to 36%. Stated rather than asserted as "a quarter",
# because that number is only true of one source size.
print(f"       {pixels_full:,} px → {pixels_prox:,} px "
      f"({100 * pixels_prox / pixels_full:.0f}% of the decoded bitmap)")

import random

random.seed(11)
photo = os.path.join(work, "photo.png")
noise = Image.new("RGB", (1600, 900))
noise.putdata([
    (random.randint(60, 210), random.randint(40, 190), random.randint(30, 170))
    for _ in range(1600 * 900)
])
noise.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(3)).save(photo)
photo_proxy = proxies.proxy_for(photo, proxy_cache, 960)
check("and a real, shaded picture is smaller on the wire too",
      os.path.getsize(photo_proxy) < os.path.getsize(photo),
      f"({os.path.getsize(photo):,} → {os.path.getsize(photo_proxy):,} bytes)")

again = proxies.proxy_for(src_a, proxy_cache, 960)
check("asking twice returns the one cached file", again == small)
check("an off-ladder size lands on a rung both callers share",
      proxies.proxy_for(src_a, proxy_cache, 700) == small,
      "(700 should round up to the same 960 file)")

# A REDRAWN panel keeps its path — this is the bug `_frame_version` exists to
# stop one layer up, and a proxy cache keyed on the path alone would reintroduce
# it by serving the old drawing for ever.
time.sleep(0.01)
with Image.open(src_b) as replacement:
    replacement.save(src_a)
redrawn = proxies.proxy_for(src_a, proxy_cache, 960)
check("REDRAWING THE SOURCE MOVES THE PROXY", redrawn != small,
      "(same path, new pixels — the cache must not serve the old one)")

tiny = os.path.join(work, "tiny.png")
Image.new("RGB", (320, 180), (9, 9, 9)).save(tiny)
check("a picture already smaller than the rung is served as itself",
      proxies.proxy_for(tiny, proxy_cache, 960) == tiny)
check("a missing file is served as itself rather than raising",
      proxies.proxy_for(os.path.join(work, "gone.png"), proxy_cache, 960)
      == os.path.join(work, "gone.png"))

os.environ["ANIMATIC_PROXY_EDGE"] = "0"
check("ANIMATIC_PROXY_EDGE=0 turns the whole feature off",
      proxies.proxy_edge(960) == 0 and proxies.proxy_for(src_b, proxy_cache, 960) == src_b)
os.environ.pop("ANIMATIC_PROXY_EDGE", None)

shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Exports go wide, stop stops, and every preset produces what it names.")
