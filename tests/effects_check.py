"""The LOOK, pinned to exact numbers on the Python side.

`tests/effects_parity_check.py` is the other half of this: it compares Pillow against
the WebGL shaders with a TOLERANCE, because those two can never be byte
identical. A tolerance test alone is not enough — two implementations can drift
together and still agree with each other — so this file asserts what each effect
must produce for a known input, to the last integer.

Everything here is arithmetic on tiny images. Nothing spends quota, nothing
touches ffmpeg, and it runs in well under a second.

    python tests/effects_check.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

import animatic
from animatic_effects import (
    LUMA,
    LutError,
    apply_effects,
    apply_mask,
    blend_onto,
    list_luts,
    parse_cube,
)
from animatic_render import MASK_ANIMATABLE, is_animated, resolve_look

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def solid(rgba, size=(4, 4)):
    return Image.new("RGBA", size, rgba)


def px(image, at=(0, 0)):
    return image.getpixel(at)


def fx(kind, **params):
    """One effect, with every parameter it doesn't name left at its default."""
    return [{"id": "e", "kind": kind, "params": params}]


def graded(rgba, kind, **params):
    return px(apply_effects(solid(rgba), fx(kind, **params)))


# ---------------------------------------------------------------------------
print("Colour\n")
# ---------------------------------------------------------------------------
# A multiply, plain and exact. 100 × 1.5 = 150.
check("brightness multiplies", graded((100, 100, 100, 255), "brightness", amount=1.5)
      == (150, 150, 150, 255))
check("brightness clips at white rather than wrapping",
      graded((200, 200, 200, 255), "brightness", amount=3.0) == (255, 255, 255, 255))
check("brightness 1.0 changes nothing",
      graded((37, 211, 90, 255), "brightness", amount=1.0) == (37, 211, 90, 255))

# ⚠ CONTRAST PIVOTS ON MID GREY, not on the image's own mean — which is what
# `ImageEnhance.Contrast` does and what a fragment shader cannot know. This
# assertion is the reason that decision is visible rather than buried: a
# mid-grey pixel must not move whatever the amount, and it is exactly what
# would change if somebody "fixed" this back to ImageEnhance.
# ±1 because 8-bit has no exact mid grey: 128 is 0.50196, and 2.5× that 0.196%
# offset is a whole code value. The point being pinned is that the pivot does
# not MOVE with the picture, which a mean-based contrast would.
check("contrast leaves mid grey where it is, at any amount",
      abs(graded((128, 128, 128, 255), "contrast", amount=2.5)[0] - 128) <= 1,
      f"(got {graded((128, 128, 128, 255), 'contrast', amount=2.5)[0]})")
check("contrast is symmetric about that pivot",
      graded((88, 88, 88, 255), "contrast", amount=2.0)[0] + 1
      >= 255 - graded((168, 168, 168, 255), "contrast", amount=2.0)[0],
      f"({graded((88, 88, 88, 255), 'contrast', amount=2.0)[0]} / "
      f"{graded((168, 168, 168, 255), 'contrast', amount=2.0)[0]})")
# 0.863 → (0.863 - 0.5) × 2 + 0.5 = 1.23, clipped
check("contrast 2 sends a light grey to white",
      graded((220, 220, 220, 255), "contrast", amount=2.0) == (255, 255, 255, 255))
check("contrast 0 flattens everything to mid grey",
      graded((10, 240, 90, 255), "contrast", amount=0.0) == (128, 128, 128, 255))

# Saturation 0 is a greyscale with the ITU-R 601 weights — the same three
# numbers `Image.convert("L")` uses, which is what makes the two agree.
r, g, b = 200, 100, 50
expected = round((r * LUMA[0] + g * LUMA[1] + b * LUMA[2]))
check("saturation 0 greys with the ITU-R 601 luma weights",
      graded((r, g, b, 255), "saturation", amount=0.0) == (expected,) * 3 + (255,),
      f"(expected {expected})")
check("that agrees with Pillow's own greyscale conversion",
      solid((r, g, b, 255)).convert("RGB").convert("L").getpixel((0, 0)) == expected)
check("saturation 1.0 changes nothing",
      graded((r, g, b, 255), "saturation", amount=1.0) == (r, g, b, 255))
# grey = 0.299×160 + 0.587×120 + 0.114×120 = 131.96; each channel is pushed to
# twice its distance from it, so 160 → 188 and 120 → 108.
check("saturation above 1 pushes colour apart, it does not clip to primaries",
      graded((160, 120, 120, 255), "saturation", amount=2.0)[:3] == (188, 108, 108),
      f"(got {graded((160, 120, 120, 255), 'saturation', amount=2.0)[:3]})")

# ---------------------------------------------------------------------------
print("\nPoint-wise grades\n")
# ---------------------------------------------------------------------------
# ⚠ EVERY ONE OF THESE HAS A NO-OP VALUE, and it is checked first each time.
# Six effects landed at once; an effect whose "off" is not exactly off is the
# failure that hides, because a picture that is 1% warm still looks like a
# picture — it just quietly disagrees with the export.
P = (80, 140, 200, 255)


def _grey(c):
    return round(0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])


# Stops, so +1 doubles and -1 halves. 80×2 = 160; 140 and 200 clip at white.
check("exposure 0 stops changes nothing", graded(P, "exposure", stops=0.0) == P)
check("exposure +1 stop doubles the light",
      graded(P, "exposure", stops=1.0)[:3] == (160, 255, 255),
      f"(got {graded(P, 'exposure', stops=1.0)[:3]})")
check("exposure -1 stop halves it",
      graded(P, "exposure", stops=-1.0)[:3] == (40, 70, 100),
      f"(got {graded(P, 'exposure', stops=-1.0)[:3]})")

# A power curve of 1/gamma, so ABOVE 1 lifts. 80/255 = 0.3137, ^(1/2.2) = 0.594,
# ×255 = 151.
check("gamma 1.0 changes nothing", graded(P, "gamma", gamma=1.0) == P)
check("gamma above 1 lifts the shadows",
      graded(P, "gamma", gamma=2.2)[:3] == (151, 194, 228),
      f"(got {graded(P, 'gamma', gamma=2.2)[:3]})")
check("gamma below 1 crushes them",
      graded(P, "gamma", gamma=0.5)[:3] == (25, 77, 157),
      f"(got {graded(P, 'gamma', gamma=0.5)[:3]})")
# The clamp, not the curve. Gamma 0 is a divide by zero on one side and a NaN on
# the other, and both reach the file as a frame nobody asked for.
check("gamma 0 is clamped rather than dividing by zero",
      graded(P, "gamma", gamma=0.0)[:3] == graded(P, "gamma", gamma=0.01)[:3])

# +0.2 on red and -0.2 on blue per unit; tint moves green on its own.
check("temperature and tint at 0 change nothing",
      graded(P, "temperature", temperature=0.0, tint=0.0) == P)
check("warming pushes red up and blue down by the same amount",
      graded(P, "temperature", temperature=0.5, tint=0.0)[:3] == (106, 140, 174),
      f"(got {graded(P, 'temperature', temperature=0.5, tint=0.0)[:3]})")
check("tint moves GREEN and leaves the red-blue axis alone",
      graded(P, "temperature", temperature=0.0, tint=-0.25)[:3] == (80, 127, 200),
      f"(got {graded(P, 'temperature', temperature=0.0, tint=-0.25)[:3]})")

# Rotating the chroma plane about the luma axis.
check("hue 0 changes nothing", graded(P, "hue", degrees=0.0) == P)
check("hue 360 is also nothing", graded(P, "hue", degrees=360.0) == P)
check("hue 120 is a real rotation",
      graded(P, "hue", degrees=120.0)[:3] == (119, 156, 18),
      f"(got {graded(P, 'hue', degrees=120.0)[:3]})")
# ⚠ THE WHOLE POINT OF GOING THROUGH YIQ rather than the 709 SVG hueRotate
# matrix: Y is exactly LUMA here, so a rotation cannot change how bright the
# pixel is. Within a level, which is all eight bits can promise.
check("a hue rotation leaves the luma where it was",
      all(abs(_grey(graded(P, "hue", degrees=d)[:3]) - _grey(P[:3])) <= 1
          for d in (30.0, 90.0, 120.0, 200.0, -75.0)),
      f"({[_grey(graded(P, 'hue', degrees=d)[:3]) for d in (30.0, 90.0, 120.0, 200.0, -75.0)]}"
      f" vs {_grey(P[:3])})")

check("sepia at 0 changes nothing", graded(P, "sepia", amount=0.0) == P)
check("sepia at 1 is the matrix, not a grey with a tint laid over it",
      graded(P, "sepia", amount=1.0)[:3] == (177, 158, 123),
      f"(got {graded(P, 'sepia', amount=1.0)[:3]})")
check("and it is warm — red above green above blue, always",
      (lambda c: c[0] > c[1] > c[2])(graded(P, "sepia", amount=1.0)[:3]))

# BOTH ENDS INCLUDED, which is what makes the control read as "how many bands"
# rather than "how dark": 2 bands is black and white, not black and mid grey.
check("posterize to 2 bands gives pure black and pure white only",
      graded(P, "posterize", levels=2.0)[:3] == (0, 255, 255),
      f"(got {graded(P, 'posterize', levels=2.0)[:3]})")
check("4 bands are evenly spaced, 255/3 apart",
      graded(P, "posterize", levels=4.0)[:3] == (85, 170, 170),
      f"(got {graded(P, 'posterize', levels=4.0)[:3]})")
check("black and white survive any number of bands",
      all(graded((0, 0, 0, 255), "posterize", levels=n)[:3] == (0, 0, 0)
          and graded((255, 255, 255, 255), "posterize", levels=n)[:3] == (255, 255, 255)
          for n in (2.0, 3.0, 8.0, 32.0)))
check("one band is clamped to two rather than dividing by zero",
      graded(P, "posterize", levels=1.0)[:3] == graded(P, "posterize", levels=2.0)[:3])

# ---------------------------------------------------------------------------
print("\nLUTs\n")
# ---------------------------------------------------------------------------
check("the built-in LUTs are on disk", "identity" in list_luts(),
      f"(found {list_luts()})")

sample = (200, 100, 50, 255)
check("an identity .cube is a no-op",
      graded(sample, "lut", name="identity", amount=1.0) == sample)
check("a LUT at amount 0 is a no-op whatever the table says",
      graded(sample, "lut", name="noir", amount=0.0) == sample)

# The classic .cube bug: the table is written with RED CHANGING FASTEST, and an
# identity LUT looks perfect whichever way round you read it. This one does not.
# It ignores green and blue entirely and returns the RED input as grey, so a red
# pixel must come out white and a blue one black. Read with the axes swapped,
# both answers invert.
tmp = tempfile.mkdtemp(prefix="lut_")
red_only = os.path.join(tmp, "redonly.cube")
SIZE = 5
lines = [f"LUT_3D_SIZE {SIZE}"]
for bi in range(SIZE):
    for gi in range(SIZE):
        for ri in range(SIZE):
            v = ri / (SIZE - 1)
            lines.append(f"{v:.6f} {v:.6f} {v:.6f}")
with open(red_only, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

size, table = parse_cube(open(red_only, encoding="utf-8").read())
check("a .cube parses to size³ triples", size == SIZE and len(table) == SIZE**3 * 3)

# Read it through the same path the exporter uses, by dropping it in as a
# built-in for the length of this check.
import animatic_effects

animatic_effects._LUT_CACHE["_redonly"] = (size, table)
check("the table is read with RED changing fastest",
      graded((255, 0, 0, 255), "lut", name="_redonly", amount=1.0)[:3] == (255, 255, 255)
      and graded((0, 0, 255, 255), "lut", name="_redonly", amount=1.0)[:3] == (0, 0, 0),
      f"(red → {graded((255, 0, 0, 255), 'lut', name='_redonly', amount=1.0)[:3]})")
check("amount mixes the graded picture back over the original",
      graded((255, 0, 0, 255), "lut", name="_redonly", amount=0.5)[1:3] == (128, 128))

# A LUT that isn't there is a NO-OP, never an error: losing a whole render over
# a file somebody deleted from `luts/` would be the wrong trade.
check("a LUT name that doesn't exist grades nothing and does not raise",
      graded(sample, "lut", name="no_such_lut", amount=1.0) == sample)
check("a path-traversing LUT name is refused rather than opened",
      graded(sample, "lut", name="../../etc/passwd", amount=1.0) == sample)

for bad, why in (
    ("1.0 1.0 1.0\n", "no LUT_3D_SIZE"),
    ("LUT_3D_SIZE 3\n1.0 1.0 1.0\n", "too few entries"),
    ("LUT_1D_SIZE 16\n", "a 1D LUT"),
):
    try:
        parse_cube(bad)
        check(f"a .cube with {why} is refused", False, "(it parsed)")
    except LutError:
        check(f"a .cube with {why} is refused", True)

# ---------------------------------------------------------------------------
print("\nChroma key\n")
# ---------------------------------------------------------------------------
green = apply_effects(solid((0, 255, 0, 255)), fx("chroma", color="#00ff00"))
check("pure green keyed on pure green comes out fully transparent",
      px(green)[3] == 0, f"(alpha {px(green)[3]})")
red = apply_effects(solid((255, 0, 0, 255)), fx("chroma", color="#00ff00"))
check("a colour nowhere near the key is left fully opaque",
      px(red)[3] == 255 and px(red)[:3] == (255, 0, 0))
# The key is measured in CHROMA ONLY, so a badly lit screen — the same hue at a
# different brightness — still keys. Keying in RGB is how you get a black rim
# round the subject, and this is the assertion that stops someone "simplifying"
# it back to an RGB distance.
dark = apply_effects(solid((0, 90, 0, 255)), fx("chroma", color="#00ff00"))
check("a DARKER shade of the key colour still keys out",
      px(dark)[3] == 0, f"(alpha {px(dark)[3]})")
check("widening `similarity` keys more, not less",
      px(apply_effects(solid((90, 200, 90, 255)), fx("chroma", similarity=0.9)))[3]
      < px(apply_effects(solid((90, 200, 90, 255)), fx("chroma", similarity=0.02)))[3])
# Spill only touches pixels the key is already biting, so a fully-kept pixel is
# untouched no matter how hard spill is pushed.
check("spill leaves a pixel the key doesn't touch alone",
      px(apply_effects(solid((255, 0, 0, 255)), fx("chroma", spill=1.0)))[:3] == (255, 0, 0))
spilled = px(apply_effects(solid((120, 210, 120, 255)), fx("chroma", spill=1.0)))
plain = px(apply_effects(solid((120, 210, 120, 255)), fx("chroma", spill=0.0)))
check("spill desaturates a pixel the key is partly biting",
      abs(spilled[0] - spilled[1]) < abs(plain[0] - plain[1]),
      f"({spilled[:3]} vs {plain[:3]})")

# ---------------------------------------------------------------------------
print("\nMasks\n")
# ---------------------------------------------------------------------------
W = H = 64
opaque = solid((255, 255, 255, 255), (W, H))


def masked(**mask):
    return apply_mask(opaque, {"kind": "rect", "x": 0.5, "y": 0.5, "w": 0.5,
                               "h": 0.5, "feather": 0.0, "invert": False, **mask})


centre = masked()
check("inside a hard mask is fully opaque", px(centre, (32, 32))[3] == 255)
check("outside it is fully transparent", px(centre, (2, 2))[3] == 0)
check("no mask at all is not a copy — the image comes back untouched",
      apply_mask(opaque, {"kind": "none"}) is opaque)
check("an unknown mask kind is treated as no mask",
      apply_mask(opaque, {"kind": "triangle"}) is opaque)

soft = masked(feather=0.6)
edge = [px(soft, (x, 32))[3] for x in range(0, 32)]
check("a feathered edge rises smoothly toward the centre",
      edge == sorted(edge) and edge[0] == 0 and edge[-1] == 255,
      f"({edge[::6]})")
check("feathering does not move the middle of the mask", px(soft, (32, 32))[3] == 255)

flipped = masked(invert=True)
check("invert swaps inside for outside",
      px(flipped, (32, 32))[3] == 0 and px(flipped, (2, 2))[3] == 255)

ellipse = apply_mask(opaque, {"kind": "ellipse", "x": 0.5, "y": 0.5, "w": 1.0,
                              "h": 1.0, "feather": 0.0, "invert": False})
check("an ellipse mask cuts the corners a rect keeps",
      px(ellipse, (32, 32))[3] == 255 and px(ellipse, (1, 1))[3] == 0)

# The mask multiplies the alpha it finds rather than replacing it, which is what
# lets a chroma key and a mask be used on the same clip without either one
# undoing the other.
keyed = apply_effects(solid((0, 255, 0, 255), (W, H)), fx("chroma"))
check("a mask multiplies existing alpha rather than replacing it",
      px(apply_mask(keyed, {"kind": "rect", "x": 0.5, "y": 0.5, "w": 1.0, "h": 1.0,
                            "feather": 0.0, "invert": False}), (32, 32))[3] == 0)

# ---------------------------------------------------------------------------
print("\nBlend modes\n")
# ---------------------------------------------------------------------------
half = 128  # 0.50196 — the nearest 8-bit value to a half


def blended(mode, base=half, layer=half, alpha=255):
    return blend_onto(
        Image.new("RGB", (2, 2), (base,) * 3),
        solid((layer,) * 3 + (alpha,), (2, 2)),
        mode,
    ).getpixel((0, 0))[0]


check("multiply: 0.5 × 0.5 = 0.25", blended("multiply") == 64, f"(got {blended('multiply')})")
check("screen: 1 - 0.5 × 0.5 = 0.75", blended("screen") == 192, f"(got {blended('screen')})")
check("add clips at white", blended("add", 200, 200) == 255)
check("darken takes the lower", blended("darken", 200, 60) == 60)
check("lighten takes the higher", blended("lighten", 200, 60) == 200)
# Overlay splits on the BASE, not the layer — the other way round is HARD LIGHT,
# a different mode, and the two are constantly confused. These two are chosen so
# the answer differs between them: on a dark base white DOUBLES the base
# (multiply side, 60 → 120); on a light base black lifts rather than crushes
# (screen side, 200 → 145). Split on the layer instead and both invert.
check("overlay multiplies where the BASE is dark", blended("overlay", 60, 255) == 120,
      f"(got {blended('overlay', 60, 255)})")
check("overlay screens where the BASE is light", blended("overlay", 200, 0) == 145,
      f"(got {blended('overlay', 200, 0)})")
check("normal is simply the layer", blended("normal", 200, 60) == 60)
check("an unrecognised mode falls back to normal", blended("kaleidoscope", 200, 60) == 60)

# THE RULE EVERY MODE OBEYS: the alpha is the mix. It is what makes a blend mode
# compose with opacity, a chroma key and a feathered mask without any of the
# four knowing about the others.
check("a fully transparent layer changes nothing, whatever its mode",
      all(blended(mode, 200, 20, alpha=0) == 200
          for mode in ("multiply", "screen", "overlay", "add", "darken", "lighten")))
check("a half-transparent multiply lands half way",
      blended("multiply", 200, 0, alpha=128) == 100,
      f"(got {blended('multiply', 200, 0, alpha=128)})")

# ---------------------------------------------------------------------------
print("\nThe chain, and what it does to a whole frame\n")
# ---------------------------------------------------------------------------
check("effects run in the order they are written",
      px(apply_effects(solid((200, 100, 50, 255)),
                       [{"id": "a", "kind": "saturation", "params": {"amount": 0}},
                        {"id": "b", "kind": "brightness", "params": {"amount": 2}}]))
      != px(apply_effects(solid((200, 100, 50, 255)),
                          [{"id": "b", "kind": "brightness", "params": {"amount": 2}},
                           {"id": "a", "kind": "saturation", "params": {"amount": 0}}])))
check("an effect kind this build doesn't know is skipped, not refused",
      px(apply_effects(solid(sample), [{"id": "z", "kind": "kaleidoscope"}])) == sample)
check("an empty chain is a no-op", px(apply_effects(solid(sample), [])) == sample)

tmp_dir = tempfile.mkdtemp(prefix="effects_")
art = os.path.join(tmp_dir, "art.png")
Image.new("RGB", (400, 300), (200, 100, 50)).save(art)
SIZE_PX = (320, 180)

baseline = animatic.render_frame({"path": art}, SIZE_PX)
check("a clip with no look renders byte-for-byte what it did before effects existed",
      animatic.render_frame({"path": art}, SIZE_PX, look=resolve_look({}, 0)).tobytes()
      == baseline.tobytes())

# ⚠ THE GRADE STOPS AT THE PICTURE. A letterboxed shot must not have its bars
# graded — they are the bar colour, not part of the film — which is why the
# effect chain runs on the source before `place_picture` fits it.
noir = animatic.render_frame(
    {"path": art}, SIZE_PX,
    look=resolve_look({"effects": [{"id": "e", "kind": "saturation",
                                    "params": {"amount": 0.0}}]}, 0),
)
check("the grade reaches the picture", noir.getpixel((160, 90)) == (124, 124, 124))
check("and stops at the letterbox bars", noir.getpixel((2, 2)) == (0, 0, 0))

# A mask is in FRAME coordinates, so what it cuts away shows the backdrop.
spot = animatic.render_frame(
    {"path": art}, SIZE_PX,
    look=resolve_look({"mask": {"kind": "ellipse", "x": 0.5, "y": 0.5,
                                "w": 0.3, "h": 0.3, "feather": 0.05}}, 0),
)
check("a mask cuts the picture back to the bar colour",
      spot.getpixel((160, 90)) == (200, 100, 50) and spot.getpixel((10, 90)) == (0, 0, 0))

# A colour card takes a look too — it is a picture like any other, which is what
# makes "a red card, multiplied" a usable grade rather than a special case.
card = animatic.render_frame(
    {"color": "#808080"}, SIZE_PX,
    look=resolve_look({"effects": [{"id": "e", "kind": "brightness",
                                    "params": {"amount": 0.5}}]}, 0),
)
check("a colour card is graded like any other picture", card.getpixel((10, 10)) == (64, 64, 64))

# ---------------------------------------------------------------------------
print("\nWhat a moving grade costs the export\n")
# ---------------------------------------------------------------------------
# Rule: a CONTINUOUS effect forces per-frame rendering, and the bill has to be
# checkable against MAX_RENDERED_STILLS before an export fills the disk.
ramp = {
    "frames": [{
        "id": "a", "duration_ms": 2000, "path": art,
        "effects": [{"id": "e", "kind": "brightness", "params": {"amount": 1.0}}],
        "keyframes": {"fx:e:amount": [{"t": 0, "v": 0.4}, {"t": 2000, "v": 1.6}]},
    }],
}
check("a ramping grade makes the project animated", is_animated(ramp))
segments, total = animatic.plan_animated_segments(ramp["frames"], [], fps=24)
distinct = len({s["signature"] for s in segments})
check("and is planned as one still per sampled frame, not one for the clip",
      total == 2000 and distinct == len(segments) == 48,
      f"(total={total} samples={len(segments)} distinct={distinct})")
check("which is well inside the still budget the export refuses past",
      distinct < animatic.MAX_RENDERED_STILLS)
# The other half of the same rule: a STATIC grade costs nothing extra, because
# the picture still holds.
still = {"frames": [dict(ramp["frames"][0], keyframes={})]}
check("a static grade is still one still for the whole clip",
      not is_animated(still)
      and len(animatic.plan_segments(still["frames"], [])[0]) == 1)

# ⚠ THE FAST PLANNER'S TWO HOLES, both of which were real and both of which look
# identical from outside: the export quietly renders something the monitor
# doesn't show. `plan_segments` produces no transform and no look of its own, so
# whatever is read off the clip instead has to be read the way `scene_at` reads
# it — through `value_at`, not straight off the field.
lone = {
    "id": "f", "duration_ms": 1000, "path": art, "kind": "image",
    "effects": [{"id": "e", "kind": "saturation", "params": {"amount": 1.0}}],
    # ONE key on each. One key is not an animation — `is_animated` needs two —
    # so this clip takes the FAST planner while the monitor honours both keys
    # everywhere. Read the stored values instead and the two disagree.
    "keyframes": {"fx:e:amount": [{"t": 400, "v": 0.0}],
                  "scale": [{"t": 400, "v": 2.0}]},
}
check("a lone keyframe does NOT make the project animated (so this is the fast path)",
      not is_animated({"frames": [lone]}))
check("the fast planner still honours a lone key on an effect parameter",
      animatic._resolved_look(lone)["effects"][0]["params"]["amount"] == 0.0,
      f"(got {animatic._resolved_look(lone)['effects'][0]['params']['amount']})")
check("and a lone key on the transform",
      animatic._static_transform(lone)["scale"] == 2.0,
      f"(got {animatic._static_transform(lone)['scale']})")
# The bug this one guards is the plainest of the three: a stored zoom with no
# keyframes at all exported at 1.0 for three phases while the monitor showed it.
check("a STORED transform reaches the export with no keyframes involved",
      animatic._static_transform({"scale": 1.5, "y": 0.3})["scale"] == 1.5
      and animatic._static_transform({"scale": 1.5, "y": 0.3})["y"] == 0.3)
# `_look_of` must NOT resolve — everything reaching it has been resolved once
# already, and a second pass at t=0 would freeze an animated grade at its first
# key. Asserted by handing it a clip that still carries its tracks.
resolved = {"effects": [{"id": "e", "kind": "brightness", "params": {"amount": 1.7}}],
            "mask": {"kind": "none"}, "blend": "screen",
            "keyframes": {"fx:e:amount": [{"t": 0, "v": 0.2}, {"t": 900, "v": 1.9}]}}
check("_look_of reads an already-resolved look rather than resolving it again",
      animatic._look_of(resolved)["effects"][0]["params"]["amount"] == 1.7
      and animatic._look_of(resolved)["blend"] == "screen")

# ---------------------------------------------------------------------------
print("\nProjects saved before any of this existed\n")
# ---------------------------------------------------------------------------
from server.schemas import AnimaticFrame, AnimaticOverlay  # noqa: E402

old_frame = AnimaticFrame(
    **{"id": "f1", "src": {"kind": "panel", "storyboard_id": "b", "index": 0},
       "duration_ms": 2000, "label": "Shot 1"}
)
check("an animatic saved before effects existed still parses",
      old_frame.effects == [] and old_frame.blend == "normal"
      and old_frame.mask.kind == "none")
old_overlay = AnimaticOverlay(**{"id": "o1", "upload_id": "abc123"})
check("so does an overlay", old_overlay.effects == [] and old_overlay.blend == "normal")
check("and the defaults resolve to a look that changes nothing",
      resolve_look(old_frame.model_dump(), 0)
      == {"effects": [], "blend": "normal",
          "mask": {"kind": "none", "invert": False,
                   **{k: getattr(old_frame.mask, k) for k in MASK_ANIMATABLE}}})
# The export payload is a MODEL DUMP now, not a hand-written dict — which is
# what stopped `id`, `keyframes` and the transform silently going missing. This
# asserts the fields the exporter actually reads are all present.
dumped = old_frame.model_dump(exclude={"url", "src"})
check("the exported clip carries everything the scene model reads",
      {"id", "keyframes", "scale", "x", "y", "opacity", "effects", "mask", "blend"}
      <= set(dumped), f"(missing {{'id','keyframes',…}} - {set(dumped)})")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Every effect produces the number it is supposed to.")
