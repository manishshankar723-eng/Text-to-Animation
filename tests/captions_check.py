"""The text engine, the caption timing rules, and the font list — pinned.

Four things are checked here, and each one guards a different way Phase 5 can
lie to the user:

  1. **The font list is the same on both sides.** `animatic_fonts.py` and
     `client/src/animatic/fonts.js` are a twin pair like the scene model, and if
     they drift the browser draws a caption in one face and the exporter burns
     in another. Element for element, plus every file actually being on disk.

  2. **The type renders the way it says it does.** Golden-ish checks on the ink
     itself: a stroke widens the glyph footprint, a shadow offsets it down and
     right, letter spacing widens the block, and a different font is a different
     width. Measured as bounding boxes rather than pixel hashes — the exact
     rasterisation is Pillow's business and changes between versions, but "the
     outline made it wider" is true forever.

  3. **Captions are timed and never overlap.** Driven through a STUB
     transcriber: the timing rules in `captions.tidy_lines` are pure, free and
     where every "the subtitles are on top of each other" bug actually lives, so
     they are worth far more test coverage than the model call is. Lines must
     land within ±200ms of where they were said.

  4. **A voiceover's returned timings are the ones that happened.** Driven
     through a stub `speak`, because the real one costs money and a test that
     spends quota is a test nobody runs.

Nothing here spends AI quota and nothing needs a key. Needs `node` on PATH for
check 1, the same one `npm run build` uses.

    python tests/captions_check.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

import animatic
import animatic_fonts
import animatic_render
import captions
import tts
from server.schemas import AnimaticTextClip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_JS = os.path.join(ROOT, "client", "src", "animatic", "fonts.js")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# 1. The font list, on both sides
# ---------------------------------------------------------------------------
print("The font list — animatic_fonts.py vs client/src/animatic/fonts.js\n")

HARNESS = """
import { FONTS, DEFAULT_FONT } from %(fonts)s;
process.stdout.write(JSON.stringify({ fonts: FONTS, default: DEFAULT_FONT }));
"""


def _file_url(path: str) -> str:
    from pathlib import Path

    return Path(path).resolve().as_uri()


def read_js_fonts() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — cannot compare the two font lists.")
        print("  A skip here is a real gap: it is the only thing stopping the")
        print("  browser and the exporter setting a caption in different faces.")
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="fonts_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {"fonts": json.dumps(_file_url(FONTS_JS))})
        proc = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            print("  fonts.js could not be evaluated (see above).")
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


js = read_js_fonts()
py_fonts = [dict(f) for f in animatic_fonts.FONTS]
check("the two font lists are identical, element for element",
      js["fonts"] == py_fonts,
      f"\n    js: {js['fonts']}\n    py: {py_fonts}")
check("both default to the same font",
      js["default"] == animatic_fonts.DEFAULT_FONT,
      f"(js={js['default']} py={animatic_fonts.DEFAULT_FONT})")
check("the default is one of the fonts in the list",
      animatic_fonts.DEFAULT_FONT in animatic_fonts.FONT_IDS)
missing = [f["file"] for f in animatic_fonts.FONTS if not animatic_fonts.font_path(f["id"])]
check("every font named in the list is actually on disk", not missing, f"missing: {missing}")
# The CSS family is namespaced on purpose: a bare "Inter" would resolve to the
# user's system copy, which is exactly the divergence bundling prevents.
check("no font is registered under its own family name",
      all(f["family"] != f["label"] for f in animatic_fonts.FONTS))
check("an unknown font id folds down to the default rather than failing",
      animatic_fonts.font_entry("no-such-font")["id"] == animatic_fonts.DEFAULT_FONT)
# `line_ratio` is the ONE number on the list that is a fact about the .ttf
# rather than a name for it, so it is the one that can go stale — swap a font
# file for a differently-proportioned cut and the browser goes on spacing lines
# by the old face's metrics. Re-measured here rather than trusted.
from PIL import ImageFont  # noqa: E402  (only this check needs it)

drift = [
    (f["id"], f["line_ratio"], sum(ImageFont.truetype(animatic_fonts.font_path(f["id"]), 100).getmetrics()) / 100)
    for f in animatic_fonts.FONTS
    if animatic_fonts.font_path(f["id"])
]
check("every font's line_ratio is what its file actually measures",
      all(abs(declared - measured) <= 0.02 for _, declared, measured in drift),
      "".join(f"\n    {i}: list says {d}, file says {m}" for i, d, m in drift
              if abs(d - m) > 0.02))


# ---------------------------------------------------------------------------
# 2. The type — what the ink actually does
# ---------------------------------------------------------------------------
print("\nThe text engine — stroke, shadow, letter spacing, font, placement\n")

# ⚠ A FULL 1080p FRAME, not a small one. `stroke_px` is quoted in pixels at
# 1080p and scaled by the real frame height, and the font size is an INTEGER
# division of that height — so at 540 the two round differently and a test
# measuring "is it the same at twice the size" fails on the rounding rather than
# on the behaviour. Measure at the size the number is quoted at.
W, H = 1920, 1080
BG = (100, 100, 100)


def render(**fields) -> Image.Image:
    """One caption on a flat grey frame. Grey so that black ink (an outline, a
    shadow) is as visible as white ink — on black or white one of the two would
    be invisible and the measurement would silently test nothing."""
    clip = {
        "id": "t1",
        "text": "HAMBURG",
        "start_ms": 0,
        "duration_ms": 1000,
        "position": "middle",
        "align": "center",
        "size": "medium",
        "color": "#ffffff",
        "backdrop": "none",
        "opacity": 1.0,
        "place": "flow",
        "x": 0.5,
        "y": 0.85,
        "font": "inter",
        "stroke_px": 0.0,
        "stroke_color": "#000000",
        "shadow": 0.0,
        "letter_spacing": 0.0,
    }
    clip.update(fields)
    canvas = Image.new("RGB", (W, H), BG)
    animatic.draw_texts(canvas, [clip])
    return canvas


def ink(image: Image.Image):
    """The bounding box of everything that isn't the background, as
    (left, top, right, bottom), plus how many pixels it covers."""
    background = Image.new("RGB", image.size, BG)
    from PIL import ImageChops

    diff = ImageChops.difference(image, background).convert("L")
    box = diff.getbbox()
    count = sum(1 for p in diff.tobytes() if p > 8)
    return box, count


base_box, base_count = ink(render())
check("a caption draws something at all", base_box is not None and base_count > 200,
      f"(box={base_box}, px={base_count})")

# --- Stroke ---
stroke_box, stroke_count = ink(render(stroke_px=10, stroke_color="#000000"))
check("a stroke widens the glyph footprint",
      stroke_box[2] - stroke_box[0] > (base_box[2] - base_box[0]) + 6,
      f"(base {base_box[2] - base_box[0]}px wide, stroked {stroke_box[2] - stroke_box[0]}px)")
check("a stroke also makes it taller",
      stroke_box[3] - stroke_box[1] > (base_box[3] - base_box[1]) + 6)
check("a stroke covers more ink than no stroke", stroke_count > base_count)
# Quoted in pixels AT 1080p, so the same project outlines identically at any
# resolution — the number is scaled, never used raw.
tall = Image.new("RGB", (W * 2, H * 2), BG)
animatic.draw_texts(tall, [{
    "id": "t1", "text": "HAMBURG", "start_ms": 0, "duration_ms": 1000,
    "position": "middle", "size": "medium", "backdrop": "none", "color": "#ffffff",
    "opacity": 1.0, "stroke_px": 10, "stroke_color": "#000000",
}])
tall_box, _ = ink(tall.resize((W, H), Image.LANCZOS))
check("the stroke scales with the frame, so it is the same at any resolution",
      abs((tall_box[2] - tall_box[0]) - (stroke_box[2] - stroke_box[0])) <= 6,
      f"(540p {stroke_box[2] - stroke_box[0]}px, 1080p-halved {tall_box[2] - tall_box[0]}px)")

# --- Shadow ---
shadow_box, _ = ink(render(shadow=0.15))
check("a shadow extends the ink down and to the right",
      shadow_box[2] > base_box[2] and shadow_box[3] > base_box[3],
      f"(base {base_box}, shadow {shadow_box})")
check("...and not up or to the left — a drop shadow has a direction",
      shadow_box[0] >= base_box[0] - 1 and shadow_box[1] >= base_box[1] - 1,
      f"(base {base_box}, shadow {shadow_box})")

# --- Letter spacing ---
spaced_box, _ = ink(render(letter_spacing=0.25))
check("letter spacing widens the block",
      spaced_box[2] - spaced_box[0] > (base_box[2] - base_box[0]) + 20,
      f"(base {base_box[2] - base_box[0]}px, spaced {spaced_box[2] - spaced_box[0]}px)")
check("...without making it taller — it is spacing, not size",
      abs((spaced_box[3] - spaced_box[1]) - (base_box[3] - base_box[1])) <= 3)
tight_box, _ = ink(render(letter_spacing=-0.05))
check("negative letter spacing tightens it",
      tight_box[2] - tight_box[0] < base_box[2] - base_box[0])

# --- Font ---
courier_box, _ = ink(render(font="courier"))
bebas_box, _ = ink(render(font="bebas"))
check("a different font is a different block",
      (courier_box[2] - courier_box[0]) != (base_box[2] - base_box[0])
      and (bebas_box[2] - bebas_box[0]) != (base_box[2] - base_box[0]))
check("a font id this build doesn't know still renders, in the default",
      ink(render(font="wingdings-3000"))[0] == base_box)

# --- Placement ---
# The flow layout puts a `bottom` caption near the bottom and stacks; free
# placement puts its CENTRE at x/y. Both are asserted because "the caption moved
# when I dragged it" is the entire feature.
free_box, _ = ink(render(place="free", x=0.25, y=0.25))
centre_x = (free_box[0] + free_box[2]) / 2
centre_y = (free_box[1] + free_box[3]) / 2
check("a free caption's centre lands at its x/y",
      abs(centre_x - W * 0.25) < W * 0.03 and abs(centre_y - H * 0.25) < H * 0.05,
      f"(centre {centre_x:.0f},{centre_y:.0f} wanted {W * 0.25:.0f},{H * 0.25:.0f})")
flow_box, _ = ink(render(place="flow", position="bottom", x=0.25, y=0.25))
check("a flow caption ignores x/y and sits in its zone",
      abs((flow_box[0] + flow_box[2]) / 2 - W / 2) < W * 0.03 and flow_box[3] > H * 0.8,
      f"(box {flow_box})")

# Two captions in one zone must stack rather than land on each other — the rule
# free placement had to be added WITHOUT breaking.
stack = Image.new("RGB", (W, H), BG)
animatic.draw_texts(stack, [
    {"id": "a", "text": "First line", "start_ms": 0, "duration_ms": 1000,
     "position": "bottom", "backdrop": "box", "color": "#ffffff", "opacity": 1.0},
    {"id": "b", "text": "Second line", "start_ms": 0, "duration_ms": 1000,
     "position": "bottom", "backdrop": "box", "color": "#ffffff", "opacity": 1.0},
])
one = Image.new("RGB", (W, H), BG)
animatic.draw_texts(one, [
    {"id": "a", "text": "First line", "start_ms": 0, "duration_ms": 1000,
     "position": "bottom", "backdrop": "box", "color": "#ffffff", "opacity": 1.0},
])
check("two captions sharing a zone stack instead of overlapping",
      ink(stack)[0][1] < ink(one)[0][1] - 10,
      f"(two start at y={ink(stack)[0][1]}, one at y={ink(one)[0][1]})")


# ---------------------------------------------------------------------------
# 2b. The type, part two — size, leading, case, wrap, and the two inks
# ---------------------------------------------------------------------------
print("\nThe text engine — exact size, leading, case, wrap, backdrop and shadow ink\n")

# --- An exact font size ---
# Quoted at 1080p and scaled by the frame, exactly like `stroke_px` — so this is
# the same three assertions the stroke gets.
big_box, _ = ink(render(size_px=100))
check("an exact size overrides the S/M/L preset",
      (big_box[3] - big_box[1]) > (base_box[3] - base_box[1]) + 20,
      f"(preset {base_box[3] - base_box[1]}px tall, 100px {big_box[3] - big_box[1]})")
check("size_px=0 hands the size back to the preset",
      ink(render(size_px=0))[0] == base_box)
check("...and a large preset is still larger than a small one",
      (ink(render(size="large"))[0][3] - ink(render(size="large"))[0][1])
      > (ink(render(size="small"))[0][3] - ink(render(size="small"))[0][1]))
tall_px = Image.new("RGB", (W * 2, H * 2), BG)
animatic.draw_texts(tall_px, [{
    "id": "t1", "text": "HAMBURG", "start_ms": 0, "duration_ms": 1000,
    "position": "middle", "backdrop": "none", "color": "#ffffff", "opacity": 1.0,
    "size_px": 100,
}])
tall_px_box, _ = ink(tall_px.resize((W, H), Image.LANCZOS))
check("an exact size scales with the frame, so it is the same at any resolution",
      abs((tall_px_box[2] - tall_px_box[0]) - (big_box[2] - big_box[0])) <= 6,
      f"(1080p {big_box[2] - big_box[0]}px, 2160p-halved {tall_px_box[2] - tall_px_box[0]}px)")

# --- Leading ---
# Measured on TWO lines, because leading is the gap BETWEEN them: on one line
# there is nothing for it to change, which is the whole point of the second
# assertion here.
two = dict(text="ONE\nTWO")
lead_base, _ = ink(render(**two))
lead_wide, _ = ink(render(**two, line_height=2.0))
check("line spacing pushes the lines apart",
      (lead_wide[3] - lead_wide[1]) > (lead_base[3] - lead_base[1]) + 15,
      f"(1.28 {lead_base[3] - lead_base[1]}px tall, 2.0 {lead_wide[3] - lead_wide[1]})")
check("...without making the glyphs wider — it is spacing, not size",
      abs((lead_wide[2] - lead_wide[0]) - (lead_base[2] - lead_base[0])) <= 2)
check("a caption with no line spacing set is drawn at 1.28, as it always was",
      ink(render(**two, line_height=1.28))[0] == lead_base)

# --- Case ---
lower_box, _ = ink(render(text="hamburg"))
check("upper case really is drawn in capitals",
      ink(render(text="hamburg", text_case="upper"))[0] == base_box,
      "(lower-cased text set in upper case must match the same word typed in caps)")
check("lower case really is drawn in lower case",
      ink(render(text="HAMBURG", text_case="lower"))[0] == lower_box)
check("'none' leaves the typed text exactly as it is",
      ink(render(text="hamburg", text_case="none"))[0] == lower_box)
check("title case capitalises each word without lower-casing the rest",
      animatic._apply_case("a NASA film", "title") == "A NASA Film",
      f"(got {animatic._apply_case('a NASA film', 'title')!r})")

# --- Wrap width ---
LONG = "The quick brown fox jumps over the lazy dog and keeps on running"
wide_box, _ = ink(render(text=LONG))
narrow_box, _ = ink(render(text=LONG, wrap=0.3))
check("a narrower wrap breaks the text sooner, so the block is taller",
      (narrow_box[3] - narrow_box[1]) > (wide_box[3] - wide_box[1]) + 20,
      f"(0.86 {wide_box[3] - wide_box[1]}px tall, 0.30 {narrow_box[3] - narrow_box[1]})")
check("...and narrower",
      (narrow_box[2] - narrow_box[0]) < (wide_box[2] - wide_box[0]) - 20)
check("the wrap width is a fraction of the FRAME",
      (narrow_box[2] - narrow_box[0]) <= W * 0.31,
      f"(block {narrow_box[2] - narrow_box[0]}px on a {W}px frame)")

# --- The backdrop's own ink ---
# Sampled JUST INSIDE THE LEFT EDGE at the block's vertical centre, which is in
# the padding: away from the glyphs (so the reading is the backdrop and nothing
# else) and away from the rounded corners (so it is there at all).
def backdrop_pixel(image):
    box = ink(image)[0]
    return image.getpixel((box[0] + 3, (box[1] + box[3]) // 2))


solid = render(backdrop="box")
check("a backdrop is drawn in the colour it is given",
      (lambda p: p[0] > 150 and p[1] < 90)(
          backdrop_pixel(render(backdrop="box", backdrop_color="#ff0000"))),
      f"(edge pixel {backdrop_pixel(render(backdrop='box', backdrop_color='#ff0000'))})")
solid_count = ink(solid)[1]
clear_count = ink(render(backdrop="box", backdrop_opacity=0.0))[1]
check("a backdrop at 0% is not drawn at all",
      clear_count < solid_count / 3,
      f"(solid box {solid_count}px of ink, 0% {clear_count})")
scrim_px = backdrop_pixel(render(backdrop="scrim"))
box_px = backdrop_pixel(solid)
check("with no opacity of its own, a scrim is lighter than a box",
      scrim_px[0] > box_px[0], f"(scrim {scrim_px}, box {box_px})")
check("an explicit opacity overrides what the kind is worth",
      backdrop_pixel(render(backdrop="box", backdrop_opacity=0.55)) == scrim_px)
square = ink(render(backdrop="box", backdrop_radius=0.0))[1]
round_ = ink(render(backdrop="box", backdrop_radius=0.9))[1]
check("rounder corners take a bite out of the backdrop", round_ < square,
      f"(square {square}px, rounded {round_}px)")
check("a radius too big for the block is clamped rather than raising",
      ink(render(backdrop="box", backdrop_radius=2.0))[1] > 0)
roomy = ink(render(backdrop="box", backdrop_pad=2.5))[0]
snug = ink(render(backdrop="box", backdrop_pad=0.0))[0]
check("padding is the room around the text inside the backdrop",
      (roomy[3] - roomy[1]) > (snug[3] - snug[1]) + 20,
      f"(0× {snug[3] - snug[1]}px tall, 2.5× {roomy[3] - roomy[1]})")

# --- "Just the letters" vs "Outline only" ---
# These two are the whole point of the `plain` kind, and they are easy to get
# wrong in a way nothing else would catch: both draw no bar, so a test that only
# looked for a backdrop would pass on either.
plain_box, plain_count = ink(render(backdrop="plain"))
outline_box, outline_count = ink(render(backdrop="none"))
check("'plain' draws no backdrop", ink(render(backdrop="plain"))[1] < solid_count / 3)
check("'plain' draws NO automatic outline either — that is what it is for",
      plain_count < outline_count,
      f"(outline only {outline_count}px of ink, just the letters {plain_count})")
check("...so the letters are narrower than the outlined ones",
      (plain_box[2] - plain_box[0]) < (outline_box[2] - outline_box[0]),
      f"(outlined {outline_box[2] - outline_box[0]}px, plain {plain_box[2] - plain_box[0]})")
check("an outline you ASKED for still draws on 'plain'",
      (lambda b: b[2] - b[0])(ink(render(backdrop="plain", stroke_px=10))[0])
      > (plain_box[2] - plain_box[0]) + 6,
      "(only the AUTOMATIC outline is gone)")
check("a backdrop kind this build doesn't know folds down to a scrim, not to nothing",
      ink(render(backdrop="hologram"))[1] > solid_count / 3,
      "(an unreadable caption is a worse fold than an ugly one)")
check("...and both sides fold it the same way",
      animatic_render.text_backdrop({"backdrop": "hologram"}) == "scrim"
      and animatic_render.text_backdrop({"backdrop": "plain"}) == "plain"
      and animatic_render.text_backdrop({}) == "scrim")

# CHOOSING "Just the letters" TURNS OFF ALL THREE PIECES OF FURNITURE, not one.
# The regression this guards is a real report: the backdrop was switched off,
# the clip still carried `shadow: 0.06`, and the dark edge that leaves round
# every glyph reads as an outline — i.e. as the control not working. Run through
# node against the REAL scene.js, because the rule lives there precisely so it
# can be checked rather than only read.
BACKDROP_HARNESS = """
import { backdropPatch, textBackdrop, backdropHasFill, TEXT_BACKDROPS } from %(mod)s;
process.stdout.write(JSON.stringify({
  kinds: TEXT_BACKDROPS,
  plain: backdropPatch("plain"),
  scrim: backdropPatch("scrim"),
  none: backdropPatch("none"),
  junk: backdropPatch("hologram"),
  folds: TEXT_BACKDROPS.concat(["hologram", ""]).map((b) => textBackdrop({ backdrop: b })),
  fills: TEXT_BACKDROPS.map((b) => backdropHasFill({ backdrop: b })),
}));
"""
SCENE_JS = os.path.join(ROOT, "client", "src", "animatic", "scene.js")


def read_js_backdrops() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — the pane's backdrop rule was NOT checked.")
        return {}
    tmp = tempfile.mkdtemp(prefix="backdrop_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(BACKDROP_HARNESS % {"mod": json.dumps(_file_url(SCENE_JS))})
        proc = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            return {}
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


js_bd = read_js_backdrops()
if js_bd:
    check("the browser knows the same four backdrop kinds the exporter does",
          js_bd["kinds"] == list(animatic_render.TEXT_BACKDROPS),
          f"(js {js_bd['kinds']}, py {list(animatic_render.TEXT_BACKDROPS)})")
    check("...and folds every one of them, plus junk, the same way",
          js_bd["folds"] == [animatic_render.text_backdrop({"backdrop": b})
                             for b in list(animatic_render.TEXT_BACKDROPS) + ["hologram", ""]],
          f"(js {js_bd['folds']})")
    check("only 'scrim' and 'box' paint a fill",
          js_bd["fills"] == [True, True, False, False], f"({js_bd['fills']})")
    check("CHOOSING 'Just the letters' ALSO CLEARS THE OUTLINE AND THE SHADOW",
          js_bd["plain"] == {"backdrop": "plain", "stroke_px": 0, "shadow": 0},
          f"(wrote {js_bd['plain']} — three fields draw dark furniture, "
          f"so 'nothing at all' has to turn off all three)")
    check("...and choosing any other kind touches nothing but the backdrop",
          js_bd["scrim"] == {"backdrop": "scrim"} and js_bd["none"] == {"backdrop": "none"},
          f"(scrim {js_bd['scrim']}, none {js_bd['none']})")
    check("...and an unknown kind is written as a scrim, not stored as junk",
          js_bd["junk"] == {"backdrop": "scrim"}, f"({js_bd['junk']})")

# --- The shadow's ink and direction ---
check("a shadow at 0% strength is not drawn",
      ink(render(shadow=0.15, shadow_opacity=0.0))[0] == base_box)
up_left, _ = ink(render(shadow=0.15, shadow_angle=225))
check("225° throws the shadow up and to the left",
      up_left[0] < base_box[0] and up_left[1] < base_box[1],
      f"(base {base_box}, 225° {up_left})")
check("...and the default 45° still throws it down and right, as it always did",
      ink(render(shadow=0.15, shadow_angle=45))[0] == shadow_box)
check("the distance is the same whichever way it falls",
      abs((base_box[0] - up_left[0]) - (shadow_box[2] - base_box[2])) <= 2,
      f"(45° reaches {shadow_box[2] - base_box[2]}px right, "
      f"225° {base_box[0] - up_left[0]}px left)")
tinted = render(text="HAMBURG", size_px=140, shadow=0.3,
                shadow_color="#ff0000", shadow_opacity=1.0)
raw = tinted.tobytes()
reds = sum(1 for i in range(0, len(raw), 3)
           if raw[i] > 150 and raw[i + 1] < 90 and raw[i + 2] < 90)
check("a shadow is cast in the colour it is given", reds > 200, f"({reds} red pixels)")


# ---------------------------------------------------------------------------
# 3. An old animatic opens unchanged
# ---------------------------------------------------------------------------
print("\nBackwards compatibility — an animatic saved before Phase 5\n")

OLD = {
    "id": "old1",
    "text": "A caption from before any of this",
    "start_ms": 0,
    "duration_ms": 2000,
    "position": "bottom",
    "align": "center",
    "size": "medium",
    "color": "#ffffff",
    "backdrop": "scrim",
    "opacity": 1.0,
}
old = AnimaticTextClip(**OLD)
check("an old-shaped caption still parses", old.text == OLD["text"])
check("...and picks up defaults that reproduce exactly what it drew",
      (old.font, old.place, old.stroke_px, old.shadow, old.letter_spacing)
      == ("inter", "flow", 0.0, 0.0, 0.0))
check("...with the subtitle position as its free-placement default",
      (old.x, old.y) == (0.5, 0.85))
check("...and the same for everything the type gained after that",
      (old.size_px, old.line_height, old.text_case, old.wrap,
       old.backdrop_color, old.backdrop_opacity, old.backdrop_radius, old.backdrop_pad,
       old.shadow_color, old.shadow_opacity, old.shadow_angle)
      == (0.0, 1.28, "none", 0.86, "#000000", None, 0.25, 1.0, "#000000", 0.55, 45.0),
      "(a default that is not what the drawing code used to hard-code would "
      "change every animatic in the library the day it shipped)")
old_render = Image.new("RGB", (W, H), BG)
animatic.draw_texts(old_render, [OLD])
new_render = Image.new("RGB", (W, H), BG)
animatic.draw_texts(new_render, [old.model_dump()])
check("an old clip and the same clip with the new defaults draw the same picture",
      old_render.tobytes() == new_render.tobytes())


# ---------------------------------------------------------------------------
# 4. Caption timing — driven through a stub transcriber
# ---------------------------------------------------------------------------
print("\nCaption timing — captions.tidy_lines\n")


def kept_starts(drawn: list, measured: list) -> bool:
    """Did every measured line keep the start it was measured at?

    ⚠ A SUBSEQUENCE, NOT AN EQUAL LIST, because `tidy_lines` SPLITS. Since the
    word cap (`captions.MAX_WORDS`) every sentence long enough to read comes back
    as several captions, so there are more of them out than in — and comparing
    the two lists element by element compares piece 1 of line 2 against line 2.
    What the rule actually promises is that a measured start is never pushed
    late, and the piece that carries it is the FIRST piece of its line. So: every
    measured start must appear among the drawn starts, in order.
    """
    want = [l["start_ms"] for l in measured]
    got = [l["start_ms"] for l in drawn]
    at = 0
    for start in want:
        while at < len(got) and got[at] != start:
            at += 1
        if at >= len(got):
            return False
        at += 1
    return True

# What was actually said, and when. The stub below returns a plausibly MESSY
# version of this — the kind of thing a model really returns — and the rules
# have to recover timing within ±200ms of the truth without ever overlapping.
TRUTH = [
    {"start_ms": 500, "end_ms": 2200, "text": "Hello — is anyone there?"},
    {"start_ms": 2400, "end_ms": 3000, "text": "No."},
    {"start_ms": 3100, "end_ms": 8600, "text":
     "Then I shall have to do this entirely on my own, which is exactly what I "
     "told everyone would happen if nobody bothered to turn up on time."},
    {"start_ms": 9000, "end_ms": 9400, "text": "Fine."},
]


def stub_transcribe():
    """A model's answer to TRUTH: out of order, one overlap, one flash.

    Every defect here is one that real transcripts have — this is what the tidy
    pass exists for, and testing it against clean input would prove nothing.
    """
    return [
        {"start_ms": 2400, "end_ms": 3000, "text": "No."},
        {"start_ms": 500, "end_ms": 2200, "text": "Hello — is anyone there?"},
        {"start_ms": 9000, "end_ms": 9100, "text": "Fine."},  # a 100ms flash
        {"start_ms": 2900, "end_ms": 8600, "text": TRUTH[2]["text"]},  # overlaps "No."
    ]


lines = captions.tidy_lines(stub_transcribe(), total_ms=12000)
check("every line comes back", len(lines) >= len(TRUTH), f"(got {len(lines)})")
check("the lines are in time order",
      all(a["start_ms"] <= b["start_ms"] for a, b in zip(lines, lines[1:])))
check("NO TWO CAPTIONS ARE EVER ON SCREEN AT ONCE",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(lines, lines[1:])),
      f"\n    {[(l['start_ms'], l['end_ms']) for l in lines]}")
# ⚠ MIN_LINE_MS IS BEST-EFFORT, AND SUBORDINATE TO NEVER OVERLAPPING. A short
# line whose neighbour starts immediately afterwards has nowhere to grow into,
# and stretching it anyway would put two subtitles on screen at once — which is
# the failure this whole pass exists to prevent. So the rule is: long enough to
# read, OR up against the next line.
check("every caption is long enough to read, or up against the next one",
      all(
          line["end_ms"] - line["start_ms"] >= captions.MIN_LINE_MS - 1
          or (i + 1 < len(lines)
              and lines[i + 1]["start_ms"] - line["end_ms"] <= captions.GAP_MS)
          for i, line in enumerate(lines)
      ),
      f"\n    {[l['end_ms'] - l['start_ms'] for l in lines]}")

# ±200ms against the truth. The long line is SPLIT, so it is matched by its
# first piece — which is the moment the sentence starts being said.
for spoken in TRUTH:
    head = spoken["text"].split()[0]
    got = next((l for l in lines if l["text"].startswith(head)), None)
    check(f"'{head}…' lands within 200ms of when it was said",
          got is not None and abs(got["start_ms"] - spoken["start_ms"]) <= 200,
          f"(wanted {spoken['start_ms']}, got {got['start_ms'] if got else None})")

check("a line too long to read in one go is split",
      len(lines) > len(TRUTH))
check("no caption is wider than the split limit",
      all(len(l["text"]) <= captions.MAX_CHARS for l in lines),
      f"(longest {max(len(l['text']) for l in lines)})")
check("splitting shares the time out — the pieces are consecutive and gapless-ish",
      all(l["end_ms"] > l["start_ms"] for l in lines))

# The transcript is relative to the FILE; the clips are relative to the
# TIMELINE. This is the shift that is silently right on a track starting at zero.
shifted = captions.tidy_lines(stub_transcribe(), total_ms=12000, offset_ms=-400)
check("a track that skips its first 400ms moves every caption 400ms earlier",
      shifted[0]["start_ms"] == lines[0]["start_ms"] - 400,
      f"({lines[0]['start_ms']} → {shifted[0]['start_ms']})")

clipped = captions.tidy_lines(stub_transcribe(), total_ms=3000)
check("a caption past the end of the video is cut, not left hanging",
      all(l["end_ms"] <= 3000 for l in clipped))
check("captions.tidy_lines survives an empty transcript", captions.tidy_lines([]) == [])

clips = captions.caption_clips(lines, layer_id=captions.CAPTION_LAYER_ID)
check("every generated clip parses as an ordinary caption",
      all(AnimaticTextClip(**c).text for c in clips))
check("...and lands on the captions LANE, not on the text the user typed",
      all(c["layer_id"] == captions.CAPTION_LAYER_ID for c in clips))
check("...and is marked as generated ONLY by its id, so a re-run replaces them",
      all(c["id"].startswith(captions.CAPTION_ID_PREFIX) for c in clips))
check("generated ids are unique", len({c["id"] for c in clips}) == len(clips))
check("a generated caption is a SUBTITLE, not a title card",
      all(c["position"] == "bottom" and c["backdrop"] == "scrim" for c in clips))

# The estimate is free and is what the confirm dialog shows. It must be computed
# from the DURATION THE BROWSER MEASURED — there is no ffprobe here.
quote = captions.estimate(90_000)
check("the estimate prices by the audio's length", quote["seconds"] == 90.0 and quote["usd"] > 0)
check("an over-long track is flagged rather than quietly accepted",
      captions.estimate(int(captions.MAX_AUDIO_SECONDS * 1000) + 60_000)["over_limit"])


# ---------------------------------------------------------------------------
# 4b. Captions THROUGH THE CUTS — captions.clip_lines
# ---------------------------------------------------------------------------
# ⚠ THE PART THAT WAS WRONG, and it was wrong in the way that is hardest to see:
# the captions looked fine on an uncut track. The model transcribes the FILE; the
# timeline holds CLIPS cut out of it. Cut the pause out of the middle of a take
# and every word after the cut is heard EARLIER than the transcript says, while
# the words in the pause are not heard at all.
print("\nCaptions through the razor — captions.clip_lines\n")

# One recording, three things said, one second apart.
SAID = [
    {"start_ms": 1000, "end_ms": 2000, "text": "First thing."},
    {"start_ms": 3000, "end_ms": 4000, "text": "Second thing."},
    {"start_ms": 5000, "end_ms": 6000, "text": "Third thing."},
]

# The whole file, laid at 0:00 and uncut: the identity case. Nothing may move.
whole = captions.clip_lines(SAID, [{"start_ms": 0, "offset_ms": 0, "play_ms": 7000}])
check("an uncut track at 0:00 leaves every caption exactly where it was said",
      [(l["start_ms"], l["end_ms"], l["text"]) for l in whole]
      == [(l["start_ms"], l["end_ms"], l["text"]) for l in SAID],
      f"\n    {whole}")

# The same file dropped at 0:10 on the timeline.
moved = captions.clip_lines(SAID, [{"start_ms": 10_000, "offset_ms": 0, "play_ms": 7000}])
check("a track moved down the timeline takes its captions with it",
      [l["start_ms"] for l in moved] == [11_000, 13_000, 15_000],
      f"({[l['start_ms'] for l in moved]})")

# THE CUT-OUT MIDDLE. Two clips: 0–2.5s of the file at 0:00, then the file from
# 4.5s butted straight onto it — the second thing said has been cut out, and the
# third is now heard 2 seconds earlier than it was recorded.
CUT = [
    {"start_ms": 0, "offset_ms": 0, "play_ms": 2500},
    {"start_ms": 2500, "offset_ms": 4500, "play_ms": 2500},
]
cut = captions.clip_lines(SAID, CUT)
check("a line whose audio was CUT OUT gets no caption at all",
      not any("Second" in l["text"] for l in cut),
      f"\n    {[l['text'] for l in cut]}")
check("...and the lines that survive are timed where they are now HEARD",
      [(l["start_ms"], l["text"]) for l in cut] == [(1000, "First thing."), (3000, "Third thing.")],
      f"\n    {[(l['start_ms'], l['text']) for l in cut]}")

# THE HEAD OF THE FILE TRIMMED OFF — the case that was silently right before,
# because it needs only one of the two shifts.
head = captions.clip_lines(SAID, [{"start_ms": 0, "offset_ms": 2500, "play_ms": 4000}])
check("trimming the head of a track moves the rest of the captions earlier",
      [(l["start_ms"], l["text"]) for l in head] == [(500, "Second thing."), (2500, "Third thing.")],
      f"\n    {[(l['start_ms'], l['text']) for l in head]}")
check("...and drops what was said before the trim", len(head) == 2)

# THE TAIL TRIMMED OFF.
tail = captions.clip_lines(SAID, [{"start_ms": 0, "offset_ms": 0, "play_ms": 4200}])
check("trimming the tail of a track drops the captions past the new end",
      [l["text"] for l in tail] == ["First thing.", "Second thing."],
      f"\n    {[l['text'] for l in tail]}")

# A CUT THROUGH THE MIDDLE OF A SENTENCE. Only the words actually heard on each
# side may be written — the rest of the line was cut out with its audio.
LONG = [{"start_ms": 0, "end_ms": 4000,
         "text": "one two three four five six seven eight"}]
halves = captions.clip_lines(LONG, [
    {"start_ms": 0, "offset_ms": 0, "play_ms": 2000},
    {"start_ms": 9000, "offset_ms": 2000, "play_ms": 2000},
])
check("a line cut in two is written as two, one per piece", len(halves) == 2,
      f"\n    {halves}")
check("...each carrying only the words heard in that piece",
      halves[0]["text"].startswith("one") and halves[0]["text"].endswith("four")
      and halves[1]["text"].startswith("five") and halves[1]["text"].endswith("eight"),
      f"\n    {[h['text'] for h in halves]}")
check("...and no word is written twice",
      sorted((halves[0]["text"] + " " + halves[1]["text"]).split())
      == sorted(LONG[0]["text"].split()),
      f"\n    {[h['text'] for h in halves]}")
check("...with the second half timed where its clip actually plays",
      halves[1]["start_ms"] == 9000, f"({halves[1]['start_ms']})")

# A cut that leaves a few milliseconds of a line behind is not a subtitle.
sliver = captions.clip_lines(SAID, [
    {"start_ms": 0, "offset_ms": 0, "play_ms": 1050},   # 50ms of "First thing."
    {"start_ms": 1000, "offset_ms": 3000, "play_ms": 1000},
])
check("a sliver of a line left behind by a cut is dropped, not flashed",
      [l["text"] for l in sliver] == ["Second thing."],
      f"\n    {[l['text'] for l in sliver]}")
# …but a line that is genuinely short and NOT cut is never dropped by that rule.
short = captions.clip_lines(
    [{"start_ms": 0, "end_ms": 90, "text": "No."}],
    [{"start_ms": 0, "offset_ms": 0, "play_ms": 5000}],
)
check("a genuinely short line that was not cut still gets its caption",
      [l["text"] for l in short] == ["No."])

check("clip_lines survives a track with no clips left", captions.clip_lines(SAID, []) == [])
check("clip_lines survives an empty transcript", captions.clip_lines([], CUT) == [])
check("captions come back in timeline order, whatever order the clips are in",
      [l["start_ms"] for l in captions.clip_lines(SAID, list(reversed(CUT)))] == [1000, 3000])

# The rules that make a transcript safe to draw still apply on top, and the
# result of the two together is what actually gets written.
drawn = captions.tidy_lines(cut, total_ms=10_000)
check("cut captions still never overlap once tidied",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(drawn, drawn[1:])),
      f"\n    {[(l['start_ms'], l['end_ms']) for l in drawn]}")


# ---------------------------------------------------------------------------
# 4c. Captions ON THE WAVEFORM — speech_spans / align_lines
# ---------------------------------------------------------------------------
# ⚠ THE USER-REPORTED BUG, and it is the one the other two sections cannot see:
# "the caption generates fine but it shows after the voiceover has said it."
# `clip_lines` and `tidy_lines` were both correct — they were faithfully placing
# times that were wrong to begin with. The model's WORDS are excellent and its
# TIMES are a guess, so the times are recomputed against the sound MEASURED in
# the file: the same waveform drawn on the timeline the user is checking against.
#
# Driven through a STUB ENVELOPE, so the whole thing is proven with no ffmpeg on
# the box and nothing spent. `peak_envelope` (the ffmpeg half) is exercised
# against a real generated WAV at the bottom of this section when ffmpeg is
# there, and skipped with a note when it isn't.
print("\nCaptions on the waveform — captions.spans_from_envelope / align_lines\n")

# --- The measurement ----------------------------------------------------------
# An envelope is one PEAK per 20ms window, 0…1 — the same quantity
# `beats.js::peaksOf` draws the timeline's waveform from, which is the whole
# point: a run of sound found here is a block of sound the user can SEE.
W = captions.ENVELOPE_WINDOW_MS


def make_envelope(blocks, level=0.8, floor=0.0):
    """`[(is_sound, ms), …]` → an envelope. `floor` is the track's noise floor,
    so a track with hiss in its silences can be built as easily as a clean one."""
    out = []
    for sound, ms in blocks:
        out.extend([level if sound else floor] * max(1, round(ms / W)))
    return out


# 0–2s sound, 2–3s silence, 3–6s sound, 6–7s silence, 7–8s sound.
ENV = make_envelope([(1, 2000), (0, 1000), (1, 3000), (0, 1000), (1, 1000)])
spans = captions.spans_from_envelope(ENV)
check("the blocks of sound in the envelope become the runs of sound",
      [(s["start_ms"], s["end_ms"]) for s in spans] == [(0, 2000), (3000, 6000), (7000, 8000)],
      f"\n    {spans}")
check("a track with no silence at all is one run of sound",
      [(s["start_ms"], s["end_ms"]) for s in
       captions.spans_from_envelope(make_envelope([(1, 8000)]))] == [(0, 8000)])
check("a track that starts with silence doesn't get a run of sound before it",
      [(s["start_ms"], s["end_ms"]) for s in
       captions.spans_from_envelope(make_envelope([(0, 1240), (1, 6760)]))]
      == [(1240, 8000)])
check("a track that is entirely silent has no runs at all",
      captions.spans_from_envelope([0.0] * 400) == [])
check("spans_from_envelope survives an empty envelope",
      captions.spans_from_envelope([]) == [])

# ⚠ THE THRESHOLD IS RELATIVE TO THE TRACK, and this is the check that proves it.
# The same shape of audio with a noise floor at 0.05 — a compressed upload, a
# room mic — must give the SAME runs. A fixed dBFS threshold (what
# `silencedetect` uses, and what this replaced) hears that floor as speech and
# returns one run covering everything.
NOISY = make_envelope([(1, 2000), (0, 1000), (1, 3000), (0, 1000), (1, 1000)],
                      level=0.8, floor=0.05)
check("A TRACK WITH A NOISE FLOOR GIVES THE SAME RUNS AS A CLEAN ONE",
      [(s["start_ms"], s["end_ms"]) for s in captions.spans_from_envelope(NOISY)]
      == [(0, 2000), (3000, 6000), (7000, 8000)],
      f"\n    {captions.spans_from_envelope(NOISY)}")
# …and a quietly-spoken passage is still speech, not silence.
SOFT = make_envelope([(1, 2000), (0, 1000), (1, 3000)], level=0.12)
check("a quietly spoken track is not mistaken for silence",
      [(s["start_ms"], s["end_ms"]) for s in captions.spans_from_envelope(SOFT)]
      == [(0, 2000), (3000, 6000)],
      f"\n    {captions.spans_from_envelope(SOFT)}")

# The two clean-ups, and they pull in opposite directions.
STOPPED = make_envelope([(1, 1000), (0, 60), (1, 1000)])
check("a gap too short to be a pause is the stop inside a word, not a boundary",
      [(s["start_ms"], s["end_ms"]) for s in captions.spans_from_envelope(STOPPED)]
      == [(0, 2060)],
      f"\n    {captions.spans_from_envelope(STOPPED)}")
CLICK = make_envelope([(1, 2000), (0, 1000), (1, 60), (0, 1000), (1, 2000)])
check("a blip of sound too short to be speech is not a run",
      [(s["start_ms"], s["end_ms"]) for s in captions.spans_from_envelope(CLICK)]
      == [(0, 2000), (4060, 6060)],
      f"\n    {captions.spans_from_envelope(CLICK)}")

# --- The alignment ------------------------------------------------------------
# THREE THINGS SAID, each in its own run of sound, with character counts in the
# same 2:3:1 ratio as the runs are long. A perfect alignment therefore has ONE
# answer that can be written down — which is what makes this a real test rather
# than a plausibility check.
SPOKEN = [
    {"start_ms": 0, "end_ms": 2000, "text": "Perfectly sized line"},          # 20 chars
    {"start_ms": 3000, "end_ms": 6000, "text": "A slightly longer line of text"},  # 30
    {"start_ms": 7000, "end_ms": 8000, "text": "Last bits."},                  # 10
]
check("the fixture really is proportional, or the check below proves nothing",
      [len(l["text"]) for l in SPOKEN] == [20, 30, 10])

# What the model hands back: the right words, and times that are late and
# invented — a pause it did not hear, a sentence it thought ran on. This is the
# shape of the real complaint, exaggerated so a failure is unmistakable.
DRIFTED = [
    {"start_ms": 900, "end_ms": 2600, "text": SPOKEN[0]["text"]},
    {"start_ms": 4200, "end_ms": 6400, "text": SPOKEN[1]["text"]},
    {"start_ms": 7900, "end_ms": 8000, "text": SPOKEN[2]["text"]},
]

aligned = captions.align_lines(DRIFTED, spans, total_ms=8000)
check("every word the model heard is still there, in order",
      [l["text"] for l in aligned] == [l["text"] for l in SPOKEN],
      f"\n    {[l['text'] for l in aligned]}")
check("EVERY CAPTION LANDS ON THE SOUND IT BELONGS TO, not where the model guessed",
      [(l["start_ms"], l["end_ms"]) for l in aligned]
      == [(l["start_ms"], l["end_ms"]) for l in SPOKEN],
      f"\n    got    {[(l['start_ms'], l['end_ms']) for l in aligned]}"
      f"\n    wanted {[(l['start_ms'], l['end_ms']) for l in SPOKEN]}")
check("NO CAPTION IS LATE — none starts after the words were actually said",
      all(a["start_ms"] <= s["start_ms"] + 1 for a, s in zip(aligned, SPOKEN)),
      f"\n    {[a['start_ms'] - s['start_ms'] for a, s in zip(aligned, SPOKEN)]}")
# The reported symptom in its own words: the voiceover plays, and the caption
# turns up afterwards. Measured against the drifted input it replaces.
check("...and it is an improvement on the model's own times, line for line",
      all(abs(a["start_ms"] - s["start_ms"]) <= abs(d["start_ms"] - s["start_ms"])
          for a, d, s in zip(aligned, DRIFTED, SPOKEN)))

# --- THE TWO INVARIANTS THE USER ACTUALLY JUDGES THIS BY ----------------------
# ⚠ THE SECOND REPORT, in the user's own words: *"there is blank space, the
# caption starts blank — I want each wave's start to the end to be the caption
# box, not placed before the voiceover wave."* Sharing the time out globally and
# nudging edges toward a nearby run (the first attempt) still left a box opening
# in a silence whenever the nudge could not reach that far. Dealing the lines
# into the runs and filling each run exactly is what makes these two true by
# construction rather than by luck, so they are checked on EVERY fixture below,
# not just the tidy one.
def in_a_run(ms, runs):
    return any(r["start_ms"] <= ms < r["end_ms"] for r in runs)


def covers_runs(placed, runs):
    """Every run of sound is opened by a caption and closed by one."""
    return all(
        any(l["start_ms"] == r["start_ms"] for l in placed)
        and any(l["end_ms"] >= r["end_ms"] for l in placed)
        for r in runs
    )


check("NO CAPTION STARTS IN A SILENCE — no box opens with blank space",
      all(in_a_run(l["start_ms"], spans) for l in aligned),
      f"\n    starts {[l['start_ms'] for l in aligned]} in {spans}")
check("EVERY RUN OF SOUND IS COVERED FROM ITS FIRST MS TO ITS LAST",
      covers_runs(aligned, spans),
      f"\n    {[(l['start_ms'], l['end_ms']) for l in aligned]} over {spans}")
check("a caption ends when its sound stops, not when the next sound starts",
      aligned[0]["end_ms"] == 2000, f"({aligned[0]['end_ms']})")

# --- MORE LINES THAN RUNS: the ordinary case, and the one that used to drift ---
# Nine short lines over three runs of sound. The lines cannot each own a run, so
# they share — and the first line of each run must still open exactly on it.
MANY = [
    {"start_ms": i * 700, "end_ms": i * 700 + 600, "text": f"Sentence number {i} here"}
    for i in range(9)
]
many = captions.align_lines(MANY, spans, total_ms=8000)
check("with more lines than runs, every line still lands inside sound",
      all(in_a_run(l["start_ms"], spans) for l in many),
      f"\n    {[l['start_ms'] for l in many]}")
check("...and each run is still opened and closed exactly", covers_runs(many, spans),
      f"\n    {[(l['start_ms'], l['end_ms']) for l in many]}")
check("...and no line is lost", len(many) == len(MANY), f"(got {len(many)})")
check("...and they stay in the order they were said",
      [l["text"] for l in many] == [l["text"] for l in MANY])
check("...and the boxes butt up inside a run, leaving the gaps at the SILENCES",
      all(a["end_ms"] == b["start_ms"] or not in_a_run(a["end_ms"], spans)
          for a, b in zip(many, many[1:])),
      f"\n    {[(l['start_ms'], l['end_ms']) for l in many]}")

# --- FEWER LINES THAN RUNS: sound with no line of its own ---------------------
# One long line over three runs. The sound must not be left bare, and the
# caption must still not start before its wave.
FEW = [{"start_ms": 0, "end_ms": 8000, "text": "One long unbroken sentence"}]
few = captions.align_lines(FEW, spans, total_ms=8000)
check("a run with no line of its own is HELD by the caption already on screen",
      len(few) == 1 and few[0]["start_ms"] == 0 and few[0]["end_ms"] == 8000,
      f"\n    {few}")

# --- A LINE THAT STARTS LATE IN THE FILE --------------------------------------
# Nothing is said for the first four seconds. The caption must open on the sound
# at 4s — not at 0, which is the "box before the wave" failure in its purest form.
LATE_SPANS = captions.spans_from_envelope(make_envelope([(0, 4000), (1, 4000)]))
late = captions.align_lines(
    [{"start_ms": 0, "end_ms": 2000, "text": "Said only at the end"}],
    LATE_SPANS, total_ms=8000,
)
check("A TRACK THAT IS SILENT AT THE HEAD GETS NO CAPTION OVER THE SILENCE",
      late[0]["start_ms"] == 4000 and late[0]["end_ms"] == 8000,
      f"\n    {late} over {LATE_SPANS}")

# --- It declines to guess -----------------------------------------------------
# ⚠ THE FALLBACK IS THE OLD BEHAVIOUR. A measurement that fails must leave the
# captions exactly where they were, never somewhere worse.
check("with nothing measured, the model's own times come back untouched",
      [(l["start_ms"], l["end_ms"]) for l in captions.align_lines(DRIFTED, [], total_ms=8000)]
      == [(l["start_ms"], l["end_ms"]) for l in DRIFTED])
tiny = [{"start_ms": 0, "end_ms": 300}]
check("a measurement that found almost no sound is disbelieved, not obeyed",
      [(l["start_ms"], l["end_ms"]) for l in captions.align_lines(DRIFTED, tiny, total_ms=8000)]
      == [(l["start_ms"], l["end_ms"]) for l in DRIFTED],
      "(0.3s of sound in an 8s track is a failed detection, not a quiet track)")
check("align_lines survives an empty transcript", captions.align_lines([], spans) == [])
check("align_lines survives a transcript of blank lines",
      captions.align_lines([{"start_ms": 0, "end_ms": 1, "text": "  "}], spans) == [])
check("no aligned caption runs past the end of the file",
      all(l["end_ms"] <= 8000 for l in aligned))

# And the result still has to survive the rules that make it safe to draw.
drawn = captions.tidy_lines(aligned, total_ms=8000)
check("aligned captions still never overlap once tidied",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(drawn, drawn[1:])),
      f"\n    {[(l['start_ms'], l['end_ms']) for l in drawn]}")
check("...and tidying does not undo the alignment by pushing them late",
      kept_starts(drawn, aligned),
      f"\n    {[l['start_ms'] for l in drawn]} vs {[l['start_ms'] for l in aligned]}")

# --- The ffmpeg half, against REAL audio --------------------------------------
# Everything above is the pure arithmetic driven by a stub envelope. This is the
# one check that the ffmpeg decode actually produces such an envelope from a real
# file — without it, the whole section could pass against a measurement that
# never works. Skipped with a note (not a pass) where there is no ffmpeg.
try:
    import animatic as _animatic

    _have_ffmpeg = _animatic.ffmpeg_available()
except Exception:  # noqa: BLE001
    _have_ffmpeg = False

if not _have_ffmpeg:
    print("  ---- no ffmpeg on PATH — the decode half of the measurement is UNCHECKED.")
    print("       The alignment arithmetic above is still proven; what is not is")
    print("       that a real file turns into an envelope at all.")
else:
    import math
    import struct

    def tone_wav(blocks, path, hz=24_000):
        """A WAV of alternating tone and digital silence: `[(is_sound, ms), …]`."""
        pcm = bytearray()
        phase = 0
        for sound, ms in blocks:
            for _ in range(int(hz * ms / 1000)):
                pcm += struct.pack(
                    "<h", int(12000 * math.sin(2 * math.pi * 220 * phase / hz)) if sound else 0
                )
                phase += 1
        with wave.open(path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(hz)
            out.writeframes(bytes(pcm))

    tmp = tempfile.mkdtemp(prefix="captions_wav_")
    try:
        probe = os.path.join(tmp, "probe.wav")
        tone_wav([(1, 2000), (0, 1000), (1, 3000), (0, 1000), (1, 1000)], probe)
        real = captions.speech_spans(probe, 8000)
        check("A REAL FILE MEASURES INTO THE RUNS OF SOUND IT ACTUALLY CONTAINS",
              [(s["start_ms"], s["end_ms"]) for s in real]
              == [(0, 2000), (3000, 6000), (7000, 8000)],
              f"\n    {real}")
        check("...and the envelope is one peak per window, in 0…1",
              (lambda e: e and len(e) > 300 and max(e) <= 1.0 and min(e) >= 0.0)(
                  captions.peak_envelope(probe)))
        check("a file that isn't audio measures as nothing, rather than raising",
              captions.speech_spans(os.path.join(tmp, "not-here.wav"), 8000) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4d. A caption is never LATE — tidy_lines rule 2
# ---------------------------------------------------------------------------
# The second half of the same report, and the half that is pure arithmetic. A
# start is WHEN THE WORD IS SAID; an end is only how long the line has been left
# up. So two captions colliding are separated by shortening the EARLIER one, and
# the later one keeps the start it was measured at. Doing it the other way round
# (which it did) delayed every caption after the first by GAP_MS for nothing.
print("\nA caption is never late — captions.tidy_lines rule 2\n")

# A transcript with no gaps in it at all — what a model returns for continuous
# narration, and the case where every line collides with the one before it.
BUTTED = [
    {"start_ms": i * 1500, "end_ms": (i + 1) * 1500,
     "text": f"Line number {i} with a fair few words in it"}
    for i in range(8)
]
butted = captions.tidy_lines(BUTTED, total_ms=20_000)
check("EVERY CAPTION KEEPS THE START IT WAS MEASURED AT",
      kept_starts(butted, BUTTED),
      f"\n    got    {[l['start_ms'] for l in butted]}"
      f"\n    wanted {[l['start_ms'] for l in BUTTED]}")
check("...and the room between them is taken off the line in front",
      all(b["start_ms"] - a["end_ms"] == captions.GAP_MS for a, b in zip(butted, butted[1:])),
      f"\n    {[b['start_ms'] - a['end_ms'] for a, b in zip(butted, butted[1:])]}")
check("...so no two are ever on screen at once",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(butted, butted[1:])))

# The exception, and the reason it is an exception: a line with nothing left to
# give is not squeezed into a blink. Two words 200ms apart — shortening the first
# would leave it below MIN_HOLD_MS, so the second moves instead.
CRAMMED = [
    {"start_ms": 0, "end_ms": 5000, "text": "A long line that is about to be interrupted"},
    {"start_ms": 200, "end_ms": 900, "text": "Interruption"},
]
crammed = captions.tidy_lines(CRAMMED, total_ms=10_000)
check("a caption that would be trimmed to a blink is not trimmed — the next one moves",
      crammed[0]["end_ms"] - crammed[0]["start_ms"] >= captions.MIN_HOLD_MS
      and crammed[1]["start_ms"] > CRAMMED[1]["start_ms"],
      f"\n    {[(l['start_ms'], l['end_ms']) for l in crammed]}")
check("...and they still don't overlap",
      crammed[0]["end_ms"] <= crammed[1]["start_ms"])

# The pieces of one split line are exactly consecutive, so every boundary in a
# split collides. None of them may drift.
LONG_ONE = [{"start_ms": 1000, "end_ms": 9000, "text": " ".join(["word"] * 60)}]
split = captions.tidy_lines(LONG_ONE, total_ms=12_000)
# ⚠ THE END IS `<=`, NOT `==`, AND THE SLACK IS EXACTLY ONE MIN_LINE_MS. Since
# the word cap (`captions.MAX_WORDS`) sixty words is twelve captions rather than
# four, and at twelve the pieces are shorter than the readability floor — so
# rule 3 grows the LAST one, which is the only piece with no neighbour in front
# of it to stop at. That is the rule working, not drift: drift is pieces walking
# later and later, and the starts below are what actually prove it isn't
# happening.
check("splitting a long line does not walk its pieces later and later",
      split[0]["start_ms"] == 1000
      and split[-1]["end_ms"] <= 9000 + captions.MIN_LINE_MS
      and all(a["start_ms"] <= b["start_ms"] for a, b in zip(split, split[1:])),
      f"\n    {[(l['start_ms'], l['end_ms']) for l in split]}")
check("...and the pieces still never overlap",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(split, split[1:])))


# ---------------------------------------------------------------------------
# 4e. The captions LANE — the same id on both sides
# ---------------------------------------------------------------------------
# The SERVER writes generated captions onto a lane the BROWSER has to recognise
# in order to draw it, name it and keep it at the top of the timeline. Two
# strings, one contract; if they drift, the captions land on a lane that does not
# exist and are invisible on the timeline while still burning into the export.
print("\nThe captions lane — captions.py vs client/src/animatic/captions.js\n")

CAPTIONS_JS = os.path.join(ROOT, "client", "src", "animatic", "captions.js")
CAPTIONS_HARNESS = """
import { CAPTION_LAYER_ID, CAPTION_LAYER_NAME, CAPTION_ID_PREFIX } from %(mod)s;
process.stdout.write(JSON.stringify({
  layer: CAPTION_LAYER_ID, name: CAPTION_LAYER_NAME, prefix: CAPTION_ID_PREFIX,
}));
"""


def read_js_captions() -> dict:
    tmp = tempfile.mkdtemp(prefix="captions_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(CAPTIONS_HARNESS % {"mod": json.dumps(_file_url(CAPTIONS_JS))})
        proc = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            print("  captions.js could not be evaluated (see above).")
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


js_captions = read_js_captions()
check("both sides name the captions lane the same thing",
      js_captions["layer"] == captions.CAPTION_LAYER_ID,
      f"(js={js_captions['layer']} py={captions.CAPTION_LAYER_ID})")
check("both sides label it the same thing",
      js_captions["name"] == captions.CAPTION_LAYER_NAME,
      f"(js={js_captions['name']} py={captions.CAPTION_LAYER_NAME})")
check("both sides know a generated caption by the same prefix",
      js_captions["prefix"] == captions.CAPTION_ID_PREFIX,
      f"(js={js_captions['prefix']} py={captions.CAPTION_ID_PREFIX})")


# ---------------------------------------------------------------------------
# 5. Voiceover speech and casting — driven through a stub `speak`
# ---------------------------------------------------------------------------
print("\nVoiceover speech — tts.speak_lines / assemble, through a stub `speak`\n")

# 60ms of speech per character, so a line's length is predictable and a
# deliberately long line can be made to overrun its shot.
MS_PER_CHAR = 60


def stub_speak(text, *, voice=None, provider=None):
    return tts.silence(len(text) * MS_PER_CHAR)


tts.speak = stub_speak  # noqa: E305 — the point of the stub

check("PCM length IS the duration, with no decoder involved",
      tts.pcm_duration_ms(tts.silence(1500)) == 1500)

def wav_duration_ms(data: bytes) -> int:
    import io

    with wave.open(io.BytesIO(data), "rb") as fh:
        return int(round(fh.getnframes() * 1000 / fh.getframerate()))


check("a WAV really contains the samples it says it does",
      wav_duration_ms(tts.wav_bytes(tts.silence(2500))) == 2500)

# ⚠ WHERE A LINE GOES IS NOT DECIDED IN THIS MODULE ANY MORE. The shot that owns
# a line is stretched to cover it and the shots after it are pushed along, which
# is one clock over the pictures and the sound together — `_lay_out_speech` in
# `server/animatics.py`, checked by `tests/voiceover_fit_check.py`. What is left
# here is what `tts` still owns alone: reading a shot's lines in order, measuring
# them exactly, and laying finished blobs where it is told.
SHOT_LINES = [
    {"text": "Short one.", "character": "RAVI"},
    {"text": "And the answer.", "character": "MAYA"},
]
pcm, spans = tts.speak_lines(SHOT_LINES)
check("every line in the shot is read", len(spans) == len(SHOT_LINES))
check("the first line starts at the top of the shot", spans[0]["start_ms"] == 0)
check("A LINE IS NEVER SPOKEN OVER THE ONE BEFORE IT",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(spans, spans[1:])),
      f"\n    {[(s['start_ms'], s['end_ms']) for s in spans]}")
check("there is a breath between them, and it is GAP_MS",
      spans[1]["start_ms"] - spans[0]["end_ms"] == tts.GAP_MS)
check("each line's length matches what was spoken",
      spans[0]["end_ms"] - spans[0]["start_ms"] == len(SHOT_LINES[0]["text"]) * MS_PER_CHAR)
check("NO TRAILING GAP — the breath after the last line is the caller's to add",
      tts.pcm_duration_ms(pcm) == spans[-1]["end_ms"])
check("the speaker rides along, for the sheet to show",
      [s["character"] for s in spans] == ["RAVI", "MAYA"])

# The blobs are then laid at the moments the caller worked out.
laid = tts.assemble([(0, pcm), (30_000, tts.silence(1000))])
check("assemble pads with silence up to each blob's own moment",
      wav_duration_ms(laid) == 31_000)
check("a piece is never mixed into the one before it — it is pushed past it",
      wav_duration_ms(tts.assemble([(0, tts.silence(5000)), (1000, tts.silence(1000))]))
      == 6000)

# Those spans become the captions, and they must survive the same rules.
vo_lines = captions.tidy_lines([dict(s) for s in spans])
check("voiceover timings make captions that never overlap",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(vo_lines, vo_lines[1:])))

print("\nVoiceover casting — who reads a line, and what they are told\n")

check("an unknown voice folds down to the default rather than failing a paid run",
      tts.resolve_voice("Gandalf") == tts.DEFAULT_VOICE)
check("a known voice is matched case-insensitively", tts.resolve_voice("kore") == "Kore")
check("every voice the cast table offers is a voice `resolve_voice` accepts",
      all(tts.resolve_voice(v) == v for v in tts.VOICES))
check("every persona casts a voice that exists",
      all(p["voice"] in tts.VOICES for p in tts.PERSONAS.values()))
check("an unknown persona folds down to 'as it comes', not to a wrong voice",
      tts.resolve_persona("wizard") == "")
check("a persona written the way a dropdown writes it still resolves",
      tts.resolve_persona("Young Man") == "young_man")

check("THE LINE'S OWN VOICE WINS — the user picked it",
      tts.voice_for({"voice": "Puck", "persona": "grandmother"}, "Kore") == "Puck")
check("...then the persona's casting",
      tts.voice_for({"persona": "grandmother"}, "Kore") == tts.PERSONAS["grandmother"]["voice"])
check("...and a line with NO persona keeps the dialog's own choice",
      tts.voice_for({}, "Puck") == "Puck")

check("A PERSONA IS WHAT CARRIES AN AGE AND A SEX TO THE MODEL",
      "elderly man" in tts.prompt_for({"text": "Sit down.", "persona": "grandfather"}))
check("...and the words themselves are quoted, so the direction is not read out",
      tts.prompt_for({"text": "Sit down.", "persona": "grandfather"}).endswith('"Sit down."'))
check("no persona means no direction — the line is sent as it is",
      tts.prompt_for({"text": "Sit down."}) == "Sit down.")
check("an empty line sends nothing at all", tts.prompt_for({"text": "   "}) == "")

check("an elderly man on the cast sheet is cast as a grandfather",
      tts.persona_from("Dadaji", "an elderly Brahmin priest, age 72") == "grandfather")
check("a nine-year-old girl as a girl",
      tts.persona_from("Priya", "a girl, 9 years old") == "girl")
check("AN AGE IN YEARS BEATS AN ADJECTIVE — the sheet says both, often",
      tts.persona_from("Ravi", "a young man, aged 68") == "grandfather")
check("a narrator is named by the part, not by a description",
      tts.persona_from("NARRATOR", "") == "narrator")
check("AND IT DECLINES TO GUESS A SEX THE BOARD NEVER GAVE",
      tts.persona_from("Kabir", "a lean hunter from the hills") == "")
check("'woman' is not read as 'man'", tts.persona_from("Asha", "a woman of forty") == "woman")

LINES = [{"text": t, "persona": p} for t, p in (
    ("Short one.", ""),
    ("A very much longer speech that will comfortably outrun the shot "
     "it belongs to, which is the whole point of this line.", "man"),
    ("And finally.", ""),
)]
vo_quote = tts.estimate(LINES)
check("THE ESTIMATE PRICES THE PROMPTS, NOT THE BARE LINES — a direction is sent too",
      vo_quote["characters"] == sum(len(tts.prompt_for(l)) for l in LINES)
      and vo_quote["characters"] > sum(len(l["text"]) for l in LINES))
check("...and it is priced at something", vo_quote["usd"] > 0)
check("too much dialogue is flagged rather than quietly accepted",
      tts.estimate([{"text": "x" * (tts.MAX_CHARACTERS + 1)}])["over_limit"])
check("an empty line list is priced at nothing",
      tts.estimate([])["usd"] == 0 and tts.estimate([])["lines"] == 0)


print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The type, the fonts, the caption timing and the voiceover timing all hold.")
