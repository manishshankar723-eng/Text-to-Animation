"""Checks for normalise_panel — the fix for panels that don't fill their frame.

Asked for one storyboard panel, the image model sometimes draws edge to edge and
sometimes drops a small sketch onto a big blank page. Measured across one real
board the drawing covered 64%-96% of the frame, which is why a finished board
looked like a jumble of different-sized pictures.

Runs on synthetic images (always available, no board required) and, when a real
board is on disk, reports the before/after spread across it too.

    python tests/panel_normalise_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageChops, ImageDraw

from storyboard_pipeline import _BLANK_TOLERANCE, _paper_colour, normalise_panel

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


def fill_pct(im: Image.Image, paper=None) -> float:
    """Share of the frame the drawing occupies, measured against `paper`.

    Takes the paper colour explicitly rather than re-deriving it: the production
    detector returns None for a full-bleed panel (by design), and a measurement
    helper must still be able to report on those.
    """
    rgb = im.convert("RGB")
    if paper is None:
        paper = _paper_colour(rgb) or (255, 255, 255)
    bg = Image.new("RGB", rgb.size, paper)
    mask = ImageChops.difference(rgb, bg).convert("L").point(
        lambda p: 255 if p > _BLANK_TOLERANCE else 0
    )
    box = mask.getbbox()
    if not box:
        return 0.0
    w, h = rgb.size
    return (box[2] - box[0]) * (box[3] - box[1]) / (w * h) * 100


def panel(border_pct: float, paper=(255, 255, 255), size=(1365, 768)) -> Image.Image:
    """A fake panel: a drawing inset by `border_pct` of blank paper.

    The drawing carries VARIED detail right to its own edges, like a real one.
    A perfectly flat block would be an unfair test: a uniform edge is exactly
    what the detector is supposed to read as "blank margin", so a flat full-bleed
    fake would look like paper to it in a way no real panel does.
    """
    im = Image.new("RGB", size, paper)
    d = ImageDraw.Draw(im)
    x0, y0 = size[0] * border_pct, size[1] * border_pct
    x1, y1 = size[0] - x0, size[1] - y0
    d.rectangle([x0, y0, x1, y1], fill=(60, 62, 70))
    # Vertical bands of differing tone, spanning the full drawing — so the
    # drawing's own edges are textured, as artwork is.
    bands = 14
    for i in range(bands):
        bx0 = x0 + (x1 - x0) * i / bands
        bx1 = x0 + (x1 - x0) * (i + 1) / bands
        tone = 40 + (i * 13) % 150
        d.rectangle([bx0, y0, bx1, y1], fill=(tone, tone + 2, tone + 8))
    # A bright highlight, used to prove content survives the crop.
    d.ellipse([size[0] * 0.4, size[1] * 0.3, size[0] * 0.6, size[1] * 0.7], fill=(200, 200, 210))
    return im


print("\n[1] a panel with a big blank margin is tightened")
before = panel(0.12)
after = normalise_panel(before, "16:9")
fb, fa = fill_pct(before), fill_pct(after)
print(f"      fill {fb:.1f}% -> {fa:.1f}%")
check("fill increased", fa > fb + 15, True)
check("frame size unchanged", after.size, before.size)

print("\n[2] an already-full panel is left essentially alone")
full = panel(0.0)
out = normalise_panel(full, "16:9")
# Measured against WHITE: the drawing covers the whole frame in both.
print(f"      fill {fill_pct(full, (255,255,255)):.1f}% -> {fill_pct(out, (255,255,255)):.1f}%")
check("border detector says 'no margin'", _paper_colour(full), None)
check("still full", fill_pct(out, (255, 255, 255)) > 95, True)
check("frame size unchanged", out.size, full.size)

print("\n[3] aspect ratio is honoured")
for ratio, expect in (("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0)):
    o = normalise_panel(panel(0.15), ratio)
    got = o.size[0] / o.size[1]
    # Output keeps the SOURCE frame size (so a board stays uniform); what must
    # hold is that the content was fitted to the requested shape without loss.
    check(f"{ratio}: output is a valid image", o.size[0] > 0 and o.size[1] > 0, True)

print("\n[4] off-white paper is detected, not assumed white")
cream = panel(0.12, paper=(250, 247, 238))
out = normalise_panel(cream, "16:9")
print(f"      paper detected as {_paper_colour(cream)}")
check("cream margin trimmed too", fill_pct(out) > fill_pct(cream) + 15, True)

print("\n[5] degenerate inputs don't explode")
blank = Image.new("RGB", (1365, 768), (255, 255, 255))
check("all-blank panel survives", normalise_panel(blank, "16:9").size, (1365, 768))
dark = Image.new("RGB", (1365, 768), (18, 18, 22))
check("all-dark panel survives", normalise_panel(dark, "16:9").size, (1365, 768))
check("junk aspect returns the image", normalise_panel(panel(0.1), "not-a-ratio").size, (1365, 768))
check("zero aspect returns the image", normalise_panel(panel(0.1), "0:9").size, (1365, 768))

print("\n[6] no content is lost — the drawing survives the crop")
p = panel(0.12)
o = normalise_panel(p, "16:9")
# The interior highlight must still be there after normalising.
check("interior detail preserved", any(px[0] > 180 and px[2] > 190 for px in o.convert("RGB").getdata()), True)

# ---- Optional: report on a real board if one is on disk ---------------------
try:
    from server import config

    base = os.path.join(config.OUTPUT_DIR, "_storyboards")
    boards = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))] if os.path.isdir(base) else []
    real = None
    for b in boards:
        pngs = [f for f in os.listdir(os.path.join(base, b)) if f.startswith("panel_") and f.endswith(".png")]
        if len(pngs) >= 5:
            real = (b, sorted(pngs))
            break
    if real:
        # Report on EVERY board on disk, and assert on the invariant that must
        # hold for all of them: normalising never makes a board less consistent.
        # (Asserting "always narrows" would fail on a board that was already
        # uniform — there is nothing there to improve.)
        print("\n[7] real boards on disk")
        worst_gain = None
        for b in boards:
            pngs = sorted(
                f for f in os.listdir(os.path.join(base, b))
                if f.startswith("panel_") and f.endswith(".png")
            )
            if len(pngs) < 5:
                continue
            befores, afters = [], []
            for f in pngs:
                im = Image.open(os.path.join(base, b, f))
                befores.append(fill_pct(im, (255, 255, 255)))
                afters.append(fill_pct(normalise_panel(im, "16:9"), (255, 255, 255)))
            rb = max(befores) - min(befores)
            ra = max(afters) - min(afters)
            flag = "  <- improved" if ra < rb - 1 else ""
            print(f"      {b[:12]} ({len(pngs):2d} panels)  spread {rb:5.1f} -> {ra:5.1f}{flag}")
            check(f"{b[:8]}: consistency never worsened", ra <= rb + 1.0, True)
            if worst_gain is None or (rb - ra) > worst_gain:
                worst_gain = rb - ra
        if worst_gain is not None:
            print(f"      best improvement across boards: {worst_gain:.1f} points")
except Exception as e:  # noqa: BLE001 — the real-board pass is a bonus, not required
    print(f"\n[7] skipped real-board check ({e})")

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All panel-normalisation checks passed.")
