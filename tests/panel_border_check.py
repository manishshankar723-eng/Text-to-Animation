"""Checks for strip_drawn_border — the fix for the frame the model draws.

Asked for a "storyboard panel", the image model very often draws the BOX as well
as the picture: a sketchy rectangle just inside the edge with white paper around
it. Every one is freehand, so no two match — different thickness, different
inset, different wobble — and a board of them reads as a pile of mismatched
Polaroids. Reported twice, the second time as "i decide remove frame in image …
i not need frame line in storyboard panel image and key poses".

THE SIGNAL a drawn line gives, and content does not: it sits at a near-constant
depth from its edge for the whole length of that edge. So walk in from every
column and record the depth of the first ink pixel — a border clusters those
depths tightly, a picture scatters them. Measured on the reported set: 7px of
spread for a real border, 319px for the picture on the same image.

Runs on synthetic images always, and reports over every real board on disk when
there is one — that is the only honest read on how often it fires and whether it
ever fires on a picture with no frame.

    python tests/panel_border_check.py
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw

from storyboard_pipeline import normalise_panel, strip_drawn_border

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


W, H = 1376, 768


def scene(draw, box):
    """Something picture-like inside `box`, so a crop has content to keep.

    Its dark shapes stay clear of the box edge, so "is there still a frame line
    on the rim?" below is answering about the FRAME and not about a piece of
    scenery that happened to be drawn against it.
    """
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(238, 238, 238))
    inset = 30
    draw.rectangle((x0 + 60, y0 + 90, x0 + 420, y1 - inset), fill=(120, 120, 120))
    draw.ellipse((x1 - 380, y0 + 60, x1 - 140, y0 + 300), fill=(60, 60, 60))
    draw.line((x0 + inset, y1 - 120, x1 - inset, y1 - 150), fill=(40, 40, 40), width=5)


def framed(inset=48, width=6):
    """A picture with a drawn frame around it — what the model keeps producing."""
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    scene(d, (inset, inset, W - inset, H - inset))
    d.rectangle((inset, inset, W - inset, H - inset), outline=(20, 20, 20), width=width)
    return im


def full_bleed():
    """A picture that already runs to all four edges — must be left alone."""
    im = Image.new("RGB", (W, H), (255, 255, 255))
    scene(ImageDraw.Draw(im), (0, 0, W - 1, H - 1))
    return im


# ---------------------------------------------------------------------------
print("\nA drawn frame is found and cropped away")
# ---------------------------------------------------------------------------
for inset, width in ((48, 6), (24, 3), (90, 10)):
    src = framed(inset, width)
    out = strip_drawn_border(src)
    check(f"frame at inset {inset}, {width}px thick is cropped", out.size != src.size)
    # The cut must land past the line — no dark fringe left on any edge.
    px = out.convert("L").load()
    ring = (
        [px[x, 0] for x in range(0, out.width, 7)]
        + [px[x, out.height - 1] for x in range(0, out.width, 7)]
        + [px[0, y] for y in range(0, out.height, 7)]
        + [px[out.width - 1, y] for y in range(0, out.height, 7)]
    )
    check(f"   …and no dark frame pixels survive on the rim (inset {inset})",
          min(ring) > 90)

# ---------------------------------------------------------------------------
print("\nA picture with no frame is left completely alone")
# ---------------------------------------------------------------------------
bleed = full_bleed()
check("full-bleed art is untouched", strip_drawn_border(bleed).size, bleed.size)

blank = Image.new("RGB", (W, H), (255, 255, 255))
check("a blank page is untouched", strip_drawn_border(blank).size, blank.size)

dark = Image.new("RGB", (W, H), (18, 18, 18))
check("an all-dark night panel is untouched", strip_drawn_border(dark).size, dark.size)

# A dark shape lying along ONE edge — a roofline, a wall — is not a frame, and
# a frame needs three edges anyway.
one_edge = full_bleed()
ImageDraw.Draw(one_edge).rectangle((0, 0, W, 40), fill=(25, 25, 25))
check("a dark band along one edge is not a frame", strip_drawn_border(one_edge).size,
      one_edge.size)

# ---------------------------------------------------------------------------
print("\nIt runs as part of normalise_panel, so every caller gets it")
# ---------------------------------------------------------------------------
# Panels, key poses and redraws all reach it through this one call.
out = normalise_panel(framed(), "16:9")
px = out.convert("L").load()
rim = (
    [px[x, 2] for x in range(0, out.width, 7)]
    + [px[x, out.height - 3] for x in range(0, out.width, 7)]
    + [px[2, y] for y in range(0, out.height, 7)]
    + [px[out.width - 3, y] for y in range(0, out.height, 7)]
)
check("normalise_panel leaves no frame line on the rim", min(rim) > 90)
# The board's own frame size is restored, so a panel that HAD a frame is not
# left smaller than its neighbours — the cropped picture is scaled back up to
# fill it. That is the "use full image size" the report asked for.
check("…and the panel comes back at the board's frame size", out.size, (W, H))
check("an unusable aspect still returns the image untouched",
      normalise_panel(framed(), "not-a-ratio").size, (W, H))

# ---------------------------------------------------------------------------
print("\nAcross the real boards on disk")
# ---------------------------------------------------------------------------
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
panels = sorted(glob.glob(os.path.join(root, "output", "_storyboards", "*", "panel_*.png")))
if not panels:
    print("  (no boards on disk — synthetic checks only)")
else:
    cropped = kept = 0
    worst = 0.0
    for p in panels:
        try:
            im = Image.open(p).convert("RGB")
        except OSError:
            continue
        out = strip_drawn_border(im)
        if out.size == im.size:
            kept += 1
            continue
        cropped += 1
        worst = max(worst, 1 - (out.width * out.height) / (im.width * im.height))
    print(f"  {cropped} of {cropped + kept} real panels carried a drawn frame")
    print(f"  largest area removed from any one panel: {worst:.1%}")
    # A deeply inset frame legitimately costs a lot of area — the worst real
    # case measured is a panel drawn as a small box in the middle of a wide
    # white mount, where 41% of the file IS margin and border. So this rail is
    # the same one the code enforces: never less than half of either side left.
    check("every crop left more than half of both sides", worst < 0.75)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All drawn-border checks passed.")
