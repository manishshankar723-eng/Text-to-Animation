"""The generated FX overlays — proved to be effects rather than coloured rectangles.

`fx_overlays.py` draws a light leak, film grain or a glitch from nothing with
numpy and writes it as an ordinary MP4. It becomes an ordinary video upload on an
ordinary picture row with an ordinary `blend` mode, so sixteen "wow" effects cost
**zero renderer changes** — the monitor already plays video clips and already
blends them, and `blend_onto` already does the same arithmetic for the export.

That design moves the entire risk into four places, and this file is one section
per place:

  1. **THE NEUTRAL VALUE.** Every blend mode has a value that changes nothing —
     BLACK for `screen`, MID GREY for `overlay`, WHITE for `multiply` — and an
     overlay is only an effect because most of it sits exactly there. Get it
     wrong by a few counts and the overlay does not add a light leak, it tints
     the entire film. This is the one failure that looks fine in a thumbnail and
     is obvious the moment anybody watches the export.

  2. **IT HAS TO MOVE.** A "light leak" that is the same picture on every frame
     is a gel taped to the lens. Nothing in the app would report it; the clip
     plays, the blend works, and the effect is simply dead.

  3. **THE TWO CATALOGUES.** `fx_overlays.py` makes the pictures and
     `client/src/animatic/fx_overlays.js` draws the shelf. An id, a label, a
     blend or a default length in one and not the other is a button that 400s, or
     an effect nobody can reach.

  4. **THE FILE ITSELF.** It has to be a real, readable MP4 of the right size and
     length, and small enough that a project full of them is still a project.

Nothing here spends AI quota and nothing needs a key. Needs `node` on PATH and an
ffmpeg (the bundled `imageio-ffmpeg` is enough).

    python tests/fx_overlay_check.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

import fx_overlays
import video_frames

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX_JS = os.path.join(ROOT, "client", "src", "animatic", "fx_overlays.js")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ⚠ THE VALUE THAT CHANGES NOTHING, PER MODE. Read off `_blend_rgb` in
# `animatic_effects.py` rather than remembered: screen is `1-(1-b)(1-l)` so l=0 is
# neutral; overlay splits on the BASE and passes it through at l=0.5; multiply is
# `b*l` so l=1 is neutral.
NEUTRAL = {"screen": 0, "overlay": 128, "multiply": 255}

# Small, but not so small that the soft overlays are floored by `MIN_DETAIL_PX` —
# at 240×135 an eighth is 30px, which the floor lifts to 32 and changes what is
# being measured. Big enough to be honest, small enough to run in seconds.
W, H = 480, 270


# ---------------------------------------------------------------------------
print("The catalogue")
py = fx_overlays.catalogue()
check("overlays load", len(py) > 0, "(none)")
check("no duplicate ids", len({o["id"] for o in py}) == len(py))
check("every overlay has a label and a note",
      all(o["label"] and o["note"] for o in py),
      "(a tile with no note is a button nobody can guess)")
check("every blend is one the renderers actually have",
      all(o["blend"] in NEUTRAL for o in py),
      f"(strays: {sorted({o['blend'] for o in py} - set(NEUTRAL))})")
shelf_ids = {c["id"] for c in fx_overlays.CATEGORIES}
unfiled = [o["id"] for o in py if o["category"] not in shelf_ids]
check("every overlay is filed on a real shelf", not unfiled, f"(stray: {unfiled})")
check("every overlay has a maker and every maker an overlay",
      set(fx_overlays._MAKERS) == {o["id"] for o in py})
print(f"\n  {len(py)} overlays across {len(fx_overlays.CATEGORIES)} shelves")

# ---------------------------------------------------------------------------
print("\n3. The two catalogues are one catalogue")
# (Checked before the pixels because it is the cheap one, and because a mismatch
# here explains any surprise below.)
HARNESS = """
import { OVERLAYS, OVERLAY_CATEGORIES } from "%(fx)s";
process.stdout.write(JSON.stringify({
  overlays: OVERLAYS.map((o) => ({
    id: o.id, label: o.label, category: o.category, blend: o.blend,
    seconds: o.seconds, note: o.note,
  })),
  categories: OVERLAY_CATEGORIES.map((c) => ({ id: c.id, label: c.label, note: c.note })),
}));
"""


def run_node() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — the browser's catalogue cannot be read.")
        print("  This is the only thing keeping the shelf and the generator in")
        print("  step; a skip here is a real gap, not a pass.")
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="fxjs_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        from pathlib import Path

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {"fx": Path(FX_JS).resolve().as_uri()})
        proc = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


js = run_node()
FIELDS = ("id", "label", "category", "blend", "seconds", "note")
py_rows = [{f: o[f] for f in FIELDS} for o in py]
js_rows = [{f: o[f] for f in FIELDS} for o in js["overlays"]]
check("the same overlays, in the same order, field for field",
      py_rows == js_rows,
      "\n       " + "\n       ".join(
          f"{a.get('id')}: {a} != {b}" for a, b in zip(py_rows, js_rows) if a != b
      )[:600] or f"({len(py_rows)} vs {len(js_rows)})")
py_cats = [{k: c[k] for k in ("id", "label", "note")} for c in fx_overlays.CATEGORIES]
check("…and the same shelves", py_cats == js["categories"],
      f"({py_cats} vs {js['categories']})")

# ---------------------------------------------------------------------------
print("\n1. Every overlay sits on its blend mode's neutral value")
# ⚠ MEASURED ON THE FRAMES THEMSELVES, not on the encoded file: h.264 is lossy
# and would blur a real few-count offset into the noise floor of its own
# quantisation. The maker is the thing that can be wrong.
off_neutral = []
too_busy = []
for entry in py:
    maker = fx_overlays._MAKERS[entry["id"]]
    fx_overlays._RUN.clear()
    rng = np.random.default_rng(11)
    n = 24
    frames = np.stack([maker(W, H, i, n, rng).astype(np.int16) for i in range(n)])
    want = NEUTRAL[entry["blend"]]
    vals, counts = np.unique(frames, return_counts=True)
    mode = int(vals[counts.argmax()])
    if abs(mode - want) > 2:
        off_neutral.append(
            f"{entry['id']} ({entry['blend']}): commonest value {mode}, neutral is {want}"
        )
    # ⚠ AND MOST OF THE FRAME HAS TO BE AT IT. A vignette that darkened the whole
    # picture, or grain centred on the right value but swinging ±80, would pass
    # the test above and still be a filter rather than an overlay.
    #
    # ⚠ `flash` IS THE ONE DELIBERATE EXCEPTION, AND IT IS NAMED RATHER THAN
    # EXCUSED BY A LOOSER THRESHOLD. It is one white pulse across the WHOLE
    # frame — covering everything is the entire effect — so a rule that most of
    # the picture stays neutral is a rule it is right to break. Loosening the
    # number for everybody instead would have let the other fifteen drift.
    if entry["id"] == "flash":
        continue
    near = float((np.abs(frames - want) <= 24).mean())
    if near < 0.35:
        too_busy.append(f"{entry['id']}: only {near * 100:.0f}% of the picture is neutral")
fx_overlays._RUN.clear()

check("the commonest pixel in every overlay IS its neutral value", not off_neutral,
      "\n       " + "\n       ".join(off_neutral[:6]) if off_neutral else "")
check("…and most of the frame is at or near it", not too_busy,
      "\n       " + "\n       ".join(too_busy[:6]) if too_busy else "")

# ---------------------------------------------------------------------------
print("\n2. Every overlay actually moves")
# ⚠ MEASURED AS THE BIGGEST CHANGE ANY PIXEL MAKES, not as the mean. Sparkle,
# dust and snow are sparse by design: a frame that is 99% black moves its mean by
# a fraction even when every particle in it has moved, so a mean-based threshold
# would either pass a frozen leak or fail a working snowfall. A single pixel
# going from black to bright IS the motion.
still = []
weak = []
for entry in py:
    maker = fx_overlays._MAKERS[entry["id"]]
    fx_overlays._RUN.clear()
    rng = np.random.default_rng(11)
    n = 48
    frames = [maker(W, H, i, n, rng).astype(np.int16) for i in range(n)]
    # Several pairs, because some of these are periodic: `vignette` and `god-rays`
    # breathe on a full cycle, so frame 0 and frame 24 are legitimately identical
    # and a two-sample test would call them dead.
    idx = [int(n * k / 8) for k in range(8)]
    biggest = max(
        int(np.abs(frames[a] - frames[b]).max()) for a in idx for b in idx if a < b
    )
    changed = max(
        float((np.abs(frames[a] - frames[b]) > 8).mean()) for a in idx for b in idx if a < b
    )
    if biggest < 12:
        still.append(f"{entry['id']}: biggest change {biggest}")
    elif changed < 0.0005:
        weak.append(f"{entry['id']}: only {changed * 100:.3f}% of pixels ever change")
fx_overlays._RUN.clear()

check("no overlay is the same picture on every frame", not still,
      "\n       " + "\n       ".join(still[:6]) if still else "")
check("…and enough of the frame changes to be seen", not weak,
      "\n       " + "\n       ".join(weak[:6]) if weak else "")

# ⚠ TWO RUNS OF ONE OVERLAY ARE TWO DIFFERENT PICTURES unless a seed is given.
# The reason is not novelty for its own sake: two light leaks on two cuts of one
# film that are identical frame for frame read as a mistake, and that is exactly
# what a bought pack of twelve gives you on the thirteenth cut.
def first_frame(kind, seed=None):
    fx_overlays._RUN.clear()
    frame = fx_overlays._MAKERS[kind](W, H, 3, 24, np.random.default_rng(seed))
    fx_overlays._RUN.clear()
    return frame


check("two light leaks are not the same light leak",
      not np.array_equal(first_frame("light-leak-warm"), first_frame("light-leak-warm")))
check("…but a seeded one repeats exactly, so this test can exist",
      np.array_equal(first_frame("bokeh", 5), first_frame("bokeh", 5)))

# ---------------------------------------------------------------------------
print("\n4. The file is a real video of the right shape")
work = tempfile.mkdtemp(prefix="fxfiles_")
try:
    # ⚠ EVERY KIND IS ENCODED, not a sample. The maker and the encoder are
    # separate failures — an odd dimension, a generator that yields the wrong
    # dtype or a frame of the wrong shape all pass every check above and die in
    # ffmpeg — and "the button 500s" is not something to find in production.
    sizes = {}
    bad_file, bad_length, bad_blend = [], [], []
    for entry in py:
        path = os.path.join(work, f"{entry['id']}.mp4")
        info = fx_overlays.render(
            entry["id"], path, width=W, height=H, seconds=1.0, fps=12, seed=4
        )
        if not os.path.isfile(path) or os.path.getsize(path) < 1000:
            bad_file.append(entry["id"])
            continue
        sizes[entry["id"]] = os.path.getsize(path)
        if info["blend"] != entry["blend"]:
            bad_blend.append(f"{entry['id']}: {info['blend']} != {entry['blend']}")
        # `probe_duration` is the SAME measurer the upload route uses, so what
        # this asserts is the number the client will actually be given.
        measured = video_frames.probe_duration(path)
        if measured and abs(measured - info["duration_ms"]) > 200:
            bad_length.append(f"{entry['id']}: says {info['duration_ms']}ms, is {measured}ms")

    check("every overlay encodes to a file", not bad_file, f"(failed: {bad_file})")
    check("every one reports the blend its catalogue entry names", not bad_blend,
          "\n       " + "\n       ".join(bad_blend[:4]) if bad_blend else "")
    check("…and is as long as it says it is", not bad_length,
          "\n       " + "\n       ".join(bad_length[:4]) if bad_length else "")

    # ⚠ AN ODD FRAME SIZE IS A FAILED ENCODE AT THE VERY END OF THE WORK. yuv420p
    # halves both axes, so 1919 wide is "width not divisible by 2" — after every
    # frame has already been computed. `render` rounds down; this is what says so.
    odd = fx_overlays.render(
        "flash", os.path.join(work, "odd.mp4"), width=W + 1, height=H + 1,
        seconds=0.5, fps=12, seed=1,
    )
    check("an odd frame size is rounded rather than refused",
          odd["width"] % 2 == 0 and odd["height"] % 2 == 0,
          f"({odd['width']}x{odd['height']})")

    # A length outside the allowed range is clamped, not honoured: a minute of 4K
    # noise is a 300MB file nobody asked for, and zero seconds is not a clip.
    long_one = fx_overlays.render(
        "flash", os.path.join(work, "long.mp4"), width=W, height=H,
        seconds=999, fps=12, seed=1,
    )
    short_one = fx_overlays.render(
        "flash", os.path.join(work, "short.mp4"), width=W, height=H,
        seconds=0, fps=12, seed=1,
    )
    check("a silly length is clamped at both ends",
          long_one["duration_ms"] <= fx_overlays.MAX_SECONDS * 1000
          and short_one["duration_ms"] >= fx_overlays.MIN_SECONDS * 1000 - 1,
          f"(999s → {long_one['duration_ms']}ms, 0s → {short_one['duration_ms']}ms)")

    # An id this build has not got must be refused by NAME rather than crashing
    # with a KeyError, because the shelf and the generator are twins and a
    # mismatch means one of them has moved.
    try:
        fx_overlays.render("not-a-thing", os.path.join(work, "x.mp4"), width=W, height=H)
        check("an unknown overlay is refused", False, "(it rendered something)")
    except Exception as exc:  # noqa: BLE001
        check("an unknown overlay is refused by name",
              "not-a-thing" in str(exc), f"(said {exc!r})")

    # ⚠ SIZE, MEASURED AT 1080p RATHER THAN AT THE TEST'S SIZE, because that is
    # what a customer's project actually stores and the noisy ones are the whole
    # reason `detail` and `crf` are per-entry. Grain at full resolution was 56 MB;
    # this is the check that stops it going back.
    heavy = os.path.join(work, "grain-1080.mp4")
    fx_overlays.render("grain", heavy, width=1920, height=1080, seconds=4, fps=24, seed=2)
    mb = os.path.getsize(heavy) / 1_048_576
    check("four seconds of 1080p grain is a sane file size", mb < 12, f"({mb:.1f} MB)")
finally:
    shutil.rmtree(work, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\n5. Through the real compositor, it behaves like the effect it claims to be")
# ⚠ THE ONLY SECTION THAT PROVES THE POINT. Everything above says the pictures
# are well-formed; this puts each one through `blend_onto` — the exporter's OWN
# arithmetic, not a copy of it — and asks whether the shot underneath came out
# changed in the direction the effect promises. A leak that darkened the picture,
# a grain pass that lifted the blacks, or a vignette that brightened the corners
# would pass every other check in this file.
from PIL import Image  # noqa: E402 — after the heavy sections on purpose

from animatic_effects import blend_onto  # noqa: E402

BASE = 110  # a mid-dark grey: room to be lifted AND room to be crushed


def composited(kind, frames=(2, 6, 10, 14, 18, 22)):
    """What the plate looks like after this overlay, at several moments.

    ⚠ SEVERAL, NOT ONE, BECAUSE SOME OF THESE FIRE IN BURSTS. `glitch` is
    deliberately empty on roughly two frames in three — damage that never stops
    reads as a broken export rather than as an effect — so a single-frame sample
    called it dead. `film-burn` and `flash` have the same shape for the opposite
    reason: they peak and are gone. The direction checks below take the STRONGEST
    moment for "did it do anything" and EVERY moment for "did it ever go the
    wrong way", which is the only pairing that is fair to a burst.

    ⚠ AND ONE `rng` ACROSS THE WHOLE RUN, WHICH IS HOW `render` DRIVES IT. Seeding
    afresh per frame — the obvious way to write this — hands every frame the SAME
    first random number, so `glitch`, whose bursts are decided by exactly that
    draw, was either firing on all six frames or on none. It reported "changed
    nothing at all" for a generator that was working perfectly. A test that does
    not drive the code the way production drives it is testing something else.
    """
    entry = fx_overlays.BY_ID[kind]
    base = Image.new("RGB", (W, H), (BASE, BASE, BASE))
    maker = fx_overlays._MAKERS[kind]
    wanted = set(frames)
    n = max(frames) + 2
    out = []
    fx_overlays._RUN.clear()
    rng = np.random.default_rng(9)
    for i in range(n):
        layer = maker(W, H, i, n, rng)
        if i not in wanted:
            continue
        blended = blend_onto(base, Image.fromarray(layer, "RGB").convert("RGBA"), entry["blend"])
        out.append(np.asarray(blended).astype(np.int16) - BASE)
    fx_overlays._RUN.clear()
    return out


wrong_way = []
for entry in py:
    deltas = composited(entry["id"])
    lo = min(int(d.min()) for d in deltas)
    hi = max(int(d.max()) for d in deltas)
    if entry["blend"] == "screen":
        # Screen can only ever add light. A single pixel darker than the plate,
        # at any moment, is arithmetic that has gone the wrong way.
        if lo < -1:
            wrong_way.append(f"{entry['id']} (screen) DARKENED by {-lo}")
        elif hi < 3:
            wrong_way.append(f"{entry['id']} (screen) changed nothing at all")
    elif entry["blend"] == "multiply":
        # Multiply can only ever remove light.
        if hi > 1:
            wrong_way.append(f"{entry['id']} (multiply) BRIGHTENED by {hi}")
        elif lo > -3:
            wrong_way.append(f"{entry['id']} (multiply) changed nothing at all")
    else:
        # Overlay goes both ways, which is the point of it — but it must do BOTH,
        # or the texture is really a brightener with extra steps.
        if hi < 3 or lo > -3:
            wrong_way.append(f"{entry['id']} (overlay) only moved one way ({lo}…{hi})")
check("every overlay moves the picture the way its blend mode can", not wrong_way,
      "\n       " + "\n       ".join(wrong_way[:6]) if wrong_way else "")

# ⚠ AND THE NEUTRAL PART REALLY IS NEUTRAL AFTER COMPOSITING, which is the
# strongest form of section 1: not "the numbers look right" but "the shot came
# back untouched everywhere the effect was not". A vignette must leave the middle
# of the frame exactly as it found it; a grain pass must not shift the average.
mid = (slice(H // 2 - 8, H // 2 + 8), slice(W // 2 - 8, W // 2 + 8))
untouched = float(max(np.abs(d[mid]).max() for d in composited("vignette")))
check("a vignette leaves the middle of the shot alone",
      untouched <= 2, f"(moved the centre by {untouched:.0f})")
grain_shift = float(max(abs(d.mean()) for d in composited("grain")))
check("film grain textures the shot without re-exposing it",
      grain_shift < 3.0, f"(mean moved by {grain_shift:.2f})")

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print(f"All good — {len(py)} overlays, every one an effect rather than a rectangle.")
