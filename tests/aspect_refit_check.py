"""CHANGING THE SHAPE OF THE FILM: what has to be carried, and what must not be.

Reported from the editor: switch a 16:9 animatic to 9:16 and "video stretch",
then "shapes not look resize". Two different faults with one trigger.

  1. **The picture stretched** because the monitor never redrew. The canvas
     sizes its backing store inside its draw effect, and no dependency of that
     effect changed when only the frame's SHAPE did — so the browser scaled the
     old pixels into the new box until some unrelated edit happened to run the
     effect again. That one is a React dependency and a ResizeObserver, in
     `ProgramCanvas.jsx`; it cannot be checked here, and it has not been checked
     in a browser either.

  2. **The shapes distorted**, and that one is arithmetic, which is what this
     file pins. A shape's `w`/`h` are fractions of the FRAME's width and height
     — the model here and in `animatic.draw_shapes` — so the same two numbers
     draw a different rectangle in a different frame, and a square star comes
     out a tall lozenge. `refitBox` in `client/src/animatic/aspects.js` carries
     the numbers over at the moment the ratio changes.

What "carried over" has to mean, and each is a check below:

  · the box keeps its PROPORTION — that is the whole complaint;
  · it keeps its apparent SIZE, both frames being measured against their short
    edge, exactly as `resolve_size()` does — so a star doesn't quietly grow;
  · it ROUND-TRIPS, because comparing two shapes means flipping between them
    and flipping back must not walk the numbers anywhere;
  · a box too big for the new frame is scaled down WHOLE, never cropped;
  · pictures are NOT carried — `placePicture` re-fits them from the source on
    the next draw, and adjusting them here would double the correction.

⚠ THE PIXEL SIZES ARE CHECKED AGAINST THE SERVER'S OWN `resolve_size`, not
against a second copy of the rule written here: a client that carried a shape
into a frame the exporter measures differently would be right about a video
nobody is going to render.

    python tests/aspect_refit_check.py

Needs `node`, like `selection_check.py`. Without it the JS checks are reported
as SKIPPED, which is a gap rather than a pass.
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

import animatic

ROOT = Path(__file__).resolve().parent.parent
failures: list[str] = []
skipped: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def skip(label, why):
    print(f"  skip {label}  ({why})")
    skipped.append(label)


# The star from the report: roughly square in a 16:9 frame — 0.29×1920 = 557px
# by 0.53×1080 = 572px — which is exactly the case that came back a lozenge.
STAR = {"kind": "star", "x": 0.78, "y": 0.47, "w": 0.29, "h": 0.53}

HARNESS = """
import { aspectNumber, frameSizeFor, refitBox } from "%(aspects)s";

const star = JSON.parse(process.argv[2]);
const out = {};

out.toTall = refitBox(star, "16:9", "9:16");
out.roundTrip = refitBox(out.toTall, "9:16", "16:9");
out.same = refitBox(star, "16:9", "16:9");
out.toSquare = refitBox(star, "16:9", "1:1");

// Nearly as wide as a 16:9 frame — wider than a 9:16 frame IS.
out.tooWide = refitBox({ w: 0.9, h: 0.2 }, "16:9", "9:16");

// A missing box takes the same defaults the renderer does rather than NaN.
out.empty = refitBox({}, "16:9", "9:16");

// The sizes the editor prints, for the server to agree with.
out.sizes = {
  "16:9": frameSizeFor("16:9", 1080),
  "9:16": frameSizeFor("9:16", 1080),
  "1:1": frameSizeFor("1:1", 1080),
  "4:5": frameSizeFor("4:5", 1080),
  "4:3": frameSizeFor("4:3", 1080),
  "3:4": frameSizeFor("3:4", 1080),
  "21:9": frameSizeFor("21:9", 1080),
  "9:16@720": frameSizeFor("9:16", 720),
  "16:9@2160": frameSizeFor("16:9", 2160),
  // Not in the table: derived, and the server derives it too.
  "5:2": frameSizeFor("5:2", 1080),
};
out.numbers = [aspectNumber("16:9"), aspectNumber("9:16"), aspectNumber("rubbish")];

console.log(JSON.stringify(out));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="aspect_")
    try:
        src = HARNESS % {"aspects": (ROOT / "client/src/animatic/aspects.js").as_uri()}
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness, json.dumps(STAR)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:600])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


LABELS = [
    "a shape keeps its proportions when the frame reshapes",
    "…and its size, measured the way the exporter measures",
    "…and comes back exactly if you change your mind",
    "changing to the shape it already is changes nothing",
    "a box too wide for the new frame shrinks whole, not cropped",
    "a shape with no size set is carried, not turned into NaN",
    "the editor's frame sizes are the server's frame sizes",
    "an unlisted ratio is derived the same way on both sides",
    "a ratio that isn't one falls back to 16:9 rather than to nothing",
]


def px(box, aspect):
    """The box in real pixels, using the SERVER's size for that shape."""
    w, h = animatic.resolve_size(aspect)
    return box["w"] * w, box["h"] * h


js = run_node()

print("\nCarrying a shape from one frame to another")
if js is None:
    for label in LABELS[:6]:
        skip(label, "node not available")
else:
    wide = px(STAR, "16:9")
    tall = px(js["toTall"], "9:16")
    # Proportion: the ratio of the drawn box's own sides, before and after.
    check(
        LABELS[0],
        abs((wide[0] / wide[1]) - (tall[0] / tall[1])) < 0.01,
        f"{wide[0]:.0f}×{wide[1]:.0f} → {tall[0]:.0f}×{tall[1]:.0f}",
    )
    check(
        LABELS[1],
        abs(wide[0] - tall[0]) < 2 and abs(wide[1] - tall[1]) < 2,
        f"{wide[0]:.0f}×{wide[1]:.0f} → {tall[0]:.0f}×{tall[1]:.0f}",
    )
    check(
        LABELS[2],
        abs(js["roundTrip"]["w"] - STAR["w"]) < 1e-4
        and abs(js["roundTrip"]["h"] - STAR["h"]) < 1e-4,
        str(js["roundTrip"]),
    )
    check(
        LABELS[3],
        abs(js["same"]["w"] - STAR["w"]) < 1e-9 and abs(js["same"]["h"] - STAR["h"]) < 1e-9,
        str(js["same"]),
    )
    # 0.9 of a 1920-wide frame is 1728px; a 9:16 frame is 1080 across, so it
    # cannot be kept. What matters is that it FITS and is still the same shape.
    too = js["tooWide"]
    before = 0.9 * 1920 / (0.2 * 1080)
    after = (too["w"] * 1080) / (too["h"] * 1920)
    check(
        LABELS[4],
        too["w"] <= 1.0 and too["h"] <= 1.0 and abs(before - after) < 0.01,
        str(too),
    )
    check(
        LABELS[5],
        js["empty"]["w"] > 0 and js["empty"]["h"] > 0,
        str(js["empty"]),
    )

print("\nThe two sides agree about how big a frame is")
if js is None:
    for label in LABELS[6:]:
        skip(label, "node not available")
else:
    mismatched = []
    for key, aspect, resolution in [
        ("16:9", "16:9", 1080),
        ("9:16", "9:16", 1080),
        ("1:1", "1:1", 1080),
        ("4:5", "4:5", 1080),
        ("4:3", "4:3", 1080),
        ("3:4", "3:4", 1080),
        ("21:9", "21:9", 1080),
        ("9:16@720", "9:16", 720),
        ("16:9@2160", "16:9", 2160),
    ]:
        server = list(animatic.resolve_size(aspect, resolution))
        if js["sizes"][key] != server:
            mismatched.append(f"{key}: editor {js['sizes'][key]} vs server {server}")
    check(LABELS[6], not mismatched, "; ".join(mismatched))
    check(
        LABELS[7],
        js["sizes"]["5:2"] == list(animatic.resolve_size("5:2", 1080)),
        f"editor {js['sizes']['5:2']} vs server {list(animatic.resolve_size('5:2', 1080))}",
    )
    check(
        LABELS[8],
        abs(js["numbers"][2] - 16 / 9) < 1e-9,
        str(js["numbers"]),
    )

print("\nWhat is deliberately NOT carried")
# A picture is re-fitted from its SOURCE against the new frame every draw, so
# there is nothing stored to carry — and carrying it would be a second
# correction on top of the one `place_picture` already makes. Proved by asking
# the exporter to place the same 16:9 still in both frames: contain fits it to
# the frame both times, with no help from anything the editor stored.
from PIL import Image

from animatic_render import place_picture

still = Image.new("RGB", (1920, 1080), (200, 200, 200))
placed_wide, _, _ = place_picture(
    still, animatic.resolve_size("16:9"), "contain", 1.0, 0.5, 0.5
)
placed_tall, _, _ = place_picture(
    still, animatic.resolve_size("9:16"), "contain", 1.0, 0.5, 0.5
)
check(
    "a picture re-fits itself, so the editor stores no correction to carry",
    placed_wide.size == (1920, 1080) and placed_tall.size == (1080, 608),
    f"{placed_wide.size} / {placed_tall.size}",
)

print()
if skipped:
    print(f"{len(skipped)} check(s) SKIPPED — node is not on PATH, so the")
    print("carrying-over arithmetic was not exercised at all. A gap, not a pass.")
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
if skipped:
    sys.exit(2)
print("A shape keeps its shape when the film changes shape.")
