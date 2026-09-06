"""The caption LOOK shelf, proved to be type this app can actually set.

`client/src/animatic/text_styles.js` is the same bargain `text_presets.js` makes
about keyframes, one layer up: a style is a bag of ORDINARY CAPTION FIELDS and
nothing else. The browser resolves it, the clip wears it, the exporter draws it,
and **the server has no vocabulary of styles at all** — a captions run is handed
plain fields and stamps them onto every line it writes.

That design is what makes twenty-two looks cost no server work. It also puts the
whole risk in five places, and this file is one check per place:

  1. **A field the SERVER refuses.** Every style, resolved, is fed through the
     real `AnimaticTextClip`. A `wrap` of 0.05 or a `stroke_px` of 30 is not "the
     caption looked wrong", it is a project that will not save.

  2. **A field the RENDERERS do not draw.** A style may only set fields
     `draw_texts` and `captionStyle` both honour. A typo'd field name would be
     stored, saved, and silently ignored by both — a look that does nothing.

  3. **Leftovers from the last style.** Applying a style writes EVERY field in
     the list, so switching from a 132px title to a plain subtitle cannot leave
     the subtitle at 132px. A style that only wrote what it cared about is a
     picker where choosing something changes only some of it.

  4. **The two whitelists drifting apart.** `STYLE_FIELDS` exists twice — in
     `text_styles.js` and in `captions.py` — and they are compared here element
     for element, the same way the font list is. The Python one is a SECURITY
     boundary as well: without it, `style` on a request body is a way to write
     `text`, `start_ms` or `layer_id` onto every clip a PAID run produces.

  5. **▯▯▯ burnt into the MP4.** Anton has no Devanagari. A style naming it,
     applied to a Hindi subtitle, is the exact failure RULEBOOK E145/E146 exist
     to prevent — so the font goes through `bestFontForText` on the way in, on
     both sides.

Nothing here spends AI quota and nothing needs a key. Needs `node` on PATH — the
same one `npm run build` uses.

    python tests/caption_style_check.py
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
from PIL import Image

import animatic
import animatic_fonts
import captions as captions_mod
from server.schemas import AnimaticTextClip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANIM = os.path.join(ROOT, "client", "src", "animatic")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# ⚠ THREE CAPTIONS, AND THE TWO AWKWARD ONES ARE THE POINT. A style applied to an
# English caption proves the fields are legal; applied to a HINDI one it proves
# the font resolution is real, which is the only part of this that can burn empty
# boxes into a customer's video. The third already wears a completely different
# look, which is what finds a style that leaves the last one's leftovers behind.
CAPTIONS = {
    "plain": {
        "id": "tx1", "text": "A line of dialogue", "start_ms": 0,
        "duration_ms": 3000, "place": "flow", "position": "bottom",
    },
    "hindi": {
        "id": "tx2", "text": "शिव जी की ये कहानी", "start_ms": 0,
        "duration_ms": 3000, "place": "flow", "position": "bottom",
    },
    "dressed": {
        # Already wearing the loudest look in the shelf, at a hand-set angle and
        # position that a style must NOT touch.
        "id": "tx3", "text": "Already styled", "start_ms": 500,
        "duration_ms": 2500, "place": "free", "x": 0.2, "y": 0.3,
        "scale": 2.4, "rotation": 12.0,
        "font": "bangers", "size_px": 132, "color": "#ffe14d",
        "backdrop": "box", "backdrop_color": "#c81e3a", "backdrop_opacity": 1.0,
        "stroke_px": 12, "shadow": 0.09, "text_case": "upper",
        "letter_spacing": 0.32, "line_height": 1.02, "wrap": 0.58,
        "keyframes": {"opacity": [{"t": 0, "v": 0.0}, {"t": 400, "v": 1.0}]},
    },
}

HARNESS = """
import {
  STYLE_FIELDS, TEXT_STYLES, TEXT_STYLE_CATEGORIES,
  applyTextStyle, resolveTextStyle, styleFromClip,
} from "%(styles)s";

const captions = JSON.parse(process.argv[2]);
const out = {
  fieldNames: Object.keys(STYLE_FIELDS),
  fallbacks: STYLE_FIELDS,
  categories: TEXT_STYLE_CATEGORIES.map((c) => c.id),
  meta: TEXT_STYLES.map((s) => ({
    id: s.id, label: s.label, category: s.category || "", hint: s.hint || "",
  })),
  resolved: {},
  applied: {},
  roundTrip: {},
};
for (const s of TEXT_STYLES) {
  out.resolved[s.id] = resolveTextStyle(s.id);
  out.applied[s.id] = {};
  for (const [name, clip] of Object.entries(captions)) {
    out.applied[s.id][name] = applyTextStyle(clip, s.id);
  }
  // A look taken OFF a clip and put back on must be the same look.
  const worn = { ...captions.plain, ...applyTextStyle(captions.plain, s.id) };
  out.roundTrip[s.id] = styleFromClip(worn);
}
process.stdout.write(JSON.stringify(out));
"""


def run_node() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — the style table cannot be read.")
        print("  This test is the only thing keeping the two STYLE_FIELDS lists")
        print("  in step; a skip here is a real gap, not a pass.")
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="styles_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {"styles": _file_url(os.path.join(ANIM, "text_styles.js"))})
        proc = subprocess.run(
            ["node", path, json.dumps(CAPTIONS)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:3000])
            print("  text_styles.js could not be evaluated (see above).")
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _file_url(path: str) -> str:
    from pathlib import Path

    return Path(path).resolve().as_uri()


# ---------------------------------------------------------------------------
print("The style table")
data = run_node()
META = {s["id"]: s for s in data["meta"]}

check("styles load", len(META) > 0, "(none came back)")
check("no duplicate ids", len(META) == len(data["meta"]),
      f"({len(data['meta'])} entries, {len(META)} distinct)")
check("every style has a label and a one-line hint",
      all(s["label"] and s["hint"] for s in data["meta"]),
      "(a style with no hint is a button nobody can guess)")
# ⚠ `subtitle` IS THE WAY BACK, and it has to be first for the same reason
# `none` is first in the animation shelf: the entry that undoes the others is the
# one somebody reaches for in a hurry.
check("`subtitle` exists and is offered first",
      data["meta"][0]["id"] == "subtitle",
      f"(first is {data['meta'][0]['id']!r})")
unfiled = [s["id"] for s in data["meta"] if s["category"] not in data["categories"]]
check("every style is filed on a real shelf", not unfiled, f"(stray: {unfiled})")
print(f"\n  {len(META)} looks across {len(data['categories'])} shelves")

# ---------------------------------------------------------------------------
print("\n1. Every look is one the server will take")
bad = []
for style_id, per_clip in data["applied"].items():
    for clip_name, patch in per_clip.items():
        try:
            AnimaticTextClip(**{**CAPTIONS[clip_name], **patch})
        except Exception as exc:  # noqa: BLE001 — the message is the report
            bad.append(f"{style_id} on {clip_name}: {exc}"[:200])
check("every style validates on every fixture", not bad,
      "\n       " + "\n       ".join(bad[:6]) if bad else "")

# ---------------------------------------------------------------------------
print("\n2. A style only sets fields both renderers actually draw")
# ⚠ AGAINST THE REAL MODEL'S FIELD LIST. A style field that is not a caption
# field would be stored by nobody and drawn by nobody — a look that does nothing,
# reported as "the style isn't working".
clip_fields = set(AnimaticTextClip.model_fields)
stray = [f for f in data["fieldNames"] if f not in clip_fields]
check("every style field is a real caption field", not stray, f"(stray: {stray})")

# ⚠ AND THE FIELDS A STYLE MAY *NOT* TOUCH. This is the rule the whole shelf
# hangs on: restyling forty subtitles has to change how they look and leave every
# one of them exactly where it is, at the length it is, saying what it says.
FORBIDDEN = (
    "text", "id", "layer_id", "group_id", "start_ms", "duration_ms",
    "position", "place", "x", "y", "scale", "rotation", "opacity", "keyframes",
)
overreach = [f for f in FORBIDDEN if f in data["fieldNames"]]
check("a style touches nothing about place, time, words or animation",
      not overreach, f"(claims: {overreach})")

moved = []
for style_id, per_clip in data["applied"].items():
    for clip_name, patch in per_clip.items():
        for field in FORBIDDEN:
            if field in patch:
                moved.append(f"{style_id} on {clip_name} wrote {field}")
check("…and no style writes one in practice either", not moved,
      "\n       " + "\n       ".join(sorted(set(moved))[:6]) if moved else "")

# ---------------------------------------------------------------------------
print("\n3. A style leaves no leftovers from the one before it")
# The `dressed` fixture wears the loudest look in the shelf. Applying a quiet
# style to it must overwrite EVERY field, or the quiet style inherits a 132px
# font and a red box from whatever was there before.
missing = []
for style_id, per_clip in data["applied"].items():
    for clip_name, patch in per_clip.items():
        absent = [f for f in data["fieldNames"] if f not in patch]
        if absent:
            missing.append(f"{style_id} on {clip_name}: never set {absent}")
check("applying a style writes every field in the list", not missing,
      "\n       " + "\n       ".join(missing[:4]) if missing else "")

worn = {**CAPTIONS["dressed"], **data["applied"]["minimal"]["dressed"]}
check("a quiet style really does undress a loud caption",
      worn["size_px"] == 0 and worn["stroke_px"] == 0 and worn["backdrop"] == "plain"
      and worn["text_case"] == "none" and worn["letter_spacing"] == 0,
      f"(got size_px={worn['size_px']}, stroke={worn['stroke_px']}, "
      f"backdrop={worn['backdrop']!r}, case={worn['text_case']!r})")
check("…and leaves its place, angle, zoom and animation alone",
      worn["x"] == 0.2 and worn["rotation"] == 12.0 and worn["scale"] == 2.4
      and worn["place"] == "free" and worn["keyframes"],
      f"(x={worn['x']}, rotation={worn['rotation']}, scale={worn['scale']})")

# A look taken off a clip and put back on is the same look — which is what
# "Save look" does, so if this is wrong a saved style is not what you saved.
drifted = []
for style_id, back in data["roundTrip"].items():
    for field, value in data["resolved"][style_id].items():
        # The font is resolved against the clip's own words on the way in, so a
        # style naming a face that cannot draw the fixture legitimately comes
        # back as a different one. Everything else must survive the round trip.
        if field == "font":
            continue
        if back.get(field) != value:
            drifted.append(f"{style_id}.{field}: {value!r} → {back.get(field)!r}")
check("a look saved off a clip is the look that was applied to it", not drifted,
      "\n       " + "\n       ".join(drifted[:6]) if drifted else "")

# ---------------------------------------------------------------------------
print("\n4. The two whitelists are one list")
# ⚠ COMPARED, NOT ASSUMED. `captions.STYLE_FIELDS` is what a captions run will
# honour off the wire and `STYLE_FIELDS` in `text_styles.js` is what the browser
# sends; a field in one and not the other is a look that applies in the inspector
# and silently does not apply to a paid transcription, or the reverse.
js_fields = list(data["fieldNames"])
py_fields = list(captions_mod.STYLE_FIELDS)
check("`STYLE_FIELDS` matches, element for element",
      sorted(js_fields) == sorted(py_fields),
      f"(only in JS: {sorted(set(js_fields) - set(py_fields))}; "
      f"only in Python: {sorted(set(py_fields) - set(js_fields))})")

# ⚠ AND IT IS A SECURITY BOUNDARY, NOT A TIDINESS ONE. `style` is a request body
# on a PAID route; without the filter it is a way to write over the transcript.
dirty = {
    "color": "#ff0000",          # legal, must survive
    "text": "hijacked",          # must not
    "start_ms": 999999,          # must not
    "duration_ms": 1,            # must not
    "id": "cap-evil",            # must not
    "layer_id": "someone-elses",  # must not
    "place": "free",             # must not
    "keyframes": {"opacity": []},  # must not
    "not_a_field": 1,            # must not
}
cleaned = captions_mod.clean_style(dirty)
check("a style may set the look", cleaned.get("color") == "#ff0000")
check("…and may not reach the words, the timing, the id or the lane",
      set(cleaned) == {"color"}, f"(let through: {sorted(cleaned)})")
check("a style that is not an object at all is simply nothing",
      captions_mod.clean_style(None) == {} and captions_mod.clean_style("nope") == {})

# The filter lives in `caption_clips`, not at each route, so a third caller
# cannot be added without it.
clips = captions_mod.caption_clips(
    [{"text": "hello", "start_ms": 0, "end_ms": 1000}],
    layer_id="captions",
    style=dirty,
)
check("`caption_clips` filters the style itself", len(clips) == 1)
check("…so a hostile style cannot rewrite what was transcribed",
      clips[0]["text"] == "hello" and clips[0]["start_ms"] == 0
      and clips[0]["layer_id"] == "captions" and clips[0]["id"].startswith("cap"),
      f"(got {clips[0]['text']!r}, {clips[0]['start_ms']}, {clips[0]['layer_id']!r})")
check("…while the look it asked for is honoured", clips[0]["color"] == "#ff0000")

# And the default is still exactly the subtitle this pass has always written.
plain_clip = captions_mod.caption_clips(
    [{"text": "hello", "start_ms": 0, "end_ms": 1000}], layer_id="captions"
)[0]
check("a run with no style writes the caption it always wrote",
      (plain_clip["color"], plain_clip["backdrop"], plain_clip["size"],
       plain_clip["position"], plain_clip["place"])
      == ("#ffffff", "scrim", "small", "bottom", "flow"),
      f"(got {plain_clip['color']!r}, {plain_clip['backdrop']!r}, {plain_clip['size']!r})")
# ⚠ AND THE `subtitle` STYLE MUST REPRODUCE IT, because it is the way back from
# every other look in the shelf. If these two ever disagree, "put it back" puts
# something else.
sub = data["resolved"]["subtitle"]
check("the `subtitle` style IS that caption",
      all(plain_clip[f] == sub[f] for f in ("color", "backdrop", "size") if f in sub),
      f"(style says {sub.get('color')!r}/{sub.get('backdrop')!r}/{sub.get('size')!r})")

# ---------------------------------------------------------------------------
print("\n5. No style can burn empty boxes into the video")
# ⚠ THE FAILURE THIS EXISTS FOR: Anton has no Devanagari, and a shelf full of
# display faces applied to a Hindi transcript is ▯▯▯ in a video somebody paid
# for. Both sides resolve the face against the caption's OWN words.
boxes = []
for style_id, per_clip in data["applied"].items():
    for clip_name, patch in per_clip.items():
        text = CAPTIONS[clip_name]["text"]
        chosen = patch.get("font", "")
        missing_scripts = animatic_fonts.missing_scripts(text, chosen)
        if missing_scripts:
            boxes.append(f"{style_id} on {clip_name}: {chosen} cannot draw {missing_scripts}")
check("every style resolves to a face that can draw the caption", not boxes,
      "\n       " + "\n       ".join(sorted(set(boxes))[:6]) if boxes else "")

# ⚠ AND IT KEEPS THE STYLE'S OWN CHOICE WHEREVER IT FITS, or the shelf would be
# twenty-two ways to get Inter.
kept = sum(
    1 for s in data["meta"]
    if data["applied"][s["id"]]["plain"]["font"] == data["resolved"][s["id"]]["font"]
)
check("…and an English caption gets the face the style asked for",
      kept == len(data["meta"]),
      f"({kept} of {len(data['meta'])} kept their own font)")

# The server does the same job for a run it transcribed, per line, which is the
# path a real Hindi film takes.
hindi = captions_mod.caption_clips(
    [{"text": "शिव जी की ये कहानी", "start_ms": 0, "end_ms": 1000}],
    layer_id="captions",
    style={"font": "anton", "color": "#ffe14d"},
)[0]
check("a captions run does the same, per line",
      not animatic_fonts.missing_scripts(hindi["text"], hindi["font"]),
      f"(chose {hindi['font']!r})")
check("…while keeping the rest of the look it was given",
      hindi["color"] == "#ffe14d")

# ---------------------------------------------------------------------------
print("\n6. Every look actually draws")
# The last thing that can be wrong when everything above is right: a style whose
# numbers are all legal and whose caption is invisible — white on white, a
# backdrop at zero, a font size that rounds to nothing. Rendered, and counted.
BG = (128, 128, 128)


def drawn(fields, text="Hamburgefonstiv"):
    """What one style puts on a frame: how many pixels, and how tall a band.

    ⚠ TWO NUMBERS, BECAUSE AREA ALONE CANNOT ANSWER "IS THIS BIGGER". A subtitle
    on a scrim paints a bar the full width of its text, so a small caption with a
    backdrop covers more PIXELS than a large one with none — comparing areas
    would call a 36px subtitle bigger than a 132px title and be right about the
    ink and wrong about the type. The band's HEIGHT is the size of the caption.
    """
    canvas = Image.new("RGB", (960, 540), BG)
    animatic.draw_texts(canvas, [{
        "id": "t1", "text": text, "start_ms": 0, "duration_ms": 1000,
        "position": "middle", "place": "flow", "opacity": 1.0,
        **{k: v for k, v in fields.items() if v is not None or k != "backdrop_opacity"},
    }])
    mask = np.abs(np.asarray(canvas).astype(int) - np.array(BG)).sum(axis=2) > 24
    rows = np.nonzero(mask.any(axis=1))[0]
    return int(mask.sum()), (int(rows.max() - rows.min() + 1) if len(rows) else 0)


faint = []
for style_id, fields in data["resolved"].items():
    covered, tall = drawn(fields)
    if covered < 400 or tall < 8:
        faint.append(f"{style_id}: {covered}px over {tall} rows")
check("every style puts something visible on the frame", not faint,
      "\n       " + "\n       ".join(faint[:6]) if faint else "")


def type_height(style_id):
    """How tall the LETTERS are, with the furniture taken off.

    ⚠ THE BACKDROP AND THE OUTLINE ARE STRIPPED FIRST, or this measures the wrong
    thing twice over: a subtitle's scrim is a bar as tall as its line box and a
    punch style's 12px outline adds two of itself to every glyph, so a band
    measured with them on compares a small caption's furniture against a big
    caption's letters. Everything about the FACE — which font, at what size, at
    what leading — is left exactly as the style set it.

    ⚠ AND THE CASE IS NORMALISED, which is subtler than the other two and cost a
    red run to find. Capitals have no descenders, so "HAMBURG" inks a band about
    three quarters as tall as "Hamburgefonstiv" at the SAME font size — enough to
    make a 72px shouting style measure smaller than a 36px sentence-case one. The
    case is part of a style's look and stays in every other check here; it is
    just not part of how BIG the type is.
    """
    fields = {**data["resolved"][style_id], "backdrop": "plain", "stroke_px": 0,
              "shadow": 0, "text_case": "none"}
    return drawn(fields)[1]


# A title style has to be bigger than a subtitle style. Not a matter of taste:
# they are on different shelves precisely because they are different sizes, and a
# `size_px` typo is invisible in every check above this one.
sub_tall = type_height("subtitle")
title_tall = type_height("title-condensed")
check("a title style really is bigger than a subtitle style",
      title_tall > sub_tall * 2, f"(subtitle {sub_tall} rows, title {title_tall} rows)")
# …and the kicker, which is the smallest thing in the shelf, really is small.
kicker_tall = type_height("kicker")
check("…and a kicker really is smaller than the title it sits above",
      kicker_tall < title_tall / 2, f"(kicker {kicker_tall} rows, title {title_tall} rows)")
# The reels shelf is loud by definition: every one of its looks has to be bigger
# than a subtitle, or it is on the wrong shelf.
quiet = [
    s["id"] for s in data["meta"]
    if s["category"] == "shorts" and type_height(s["id"]) <= sub_tall * 1.5
]
check("every shorts look is bigger than a subtitle", not quiet, f"(too small: {quiet})")

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print(f"All good — {len(META)} caption looks, and the server honours every one.")
