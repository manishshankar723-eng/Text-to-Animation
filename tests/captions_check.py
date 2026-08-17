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

clips = captions.caption_clips(lines)
check("every generated clip parses as an ordinary caption",
      all(AnimaticTextClip(**c).text for c in clips))
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
# 5. Voiceover timing — driven through a stub `speak`
# ---------------------------------------------------------------------------
print("\nVoiceover timing — tts.synthesise_timed\n")

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

LINES = [
    {"text": "Short one.", "start_ms": 0},
    # 120 characters at 60ms is 7.2 seconds, into a shot that is 2s long: this
    # line CANNOT have the start time the next one wants, and the next one must
    # move rather than be spoken over the top of it.
    {"text": "A very much longer speech that will comfortably outrun the shot "
             "it belongs to, which is the whole point of this line.", "start_ms": 2000},
    {"text": "And finally.", "start_ms": 4000},
]

wav, timings = tts.synthesise_timed(LINES)
check("every line is read", len(timings) == len(LINES))
check("a line that fits gets exactly the time it asked for",
      timings[0]["start_ms"] == 0)
check("a line still gets its slot when the one before it fits",
      timings[1]["start_ms"] == 2000)
check("A LINE IS NEVER SPOKEN OVER THE ONE BEFORE IT",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(timings, timings[1:])),
      f"\n    {[(t['start_ms'], t['end_ms']) for t in timings]}")
check("an overrunning line pushes the next one later rather than being cut",
      timings[2]["start_ms"] > 4000,
      f"(asked for 4000, got {timings[2]['start_ms']})")
check("the returned timings describe the audio that was actually made",
      wav_duration_ms(wav) >= timings[-1]["end_ms"],
      f"(wav {wav_duration_ms(wav)}ms, last line ends {timings[-1]['end_ms']}ms)")
check("each line's length matches what was spoken",
      timings[0]["end_ms"] - timings[0]["start_ms"] == len(LINES[0]["text"]) * MS_PER_CHAR)

# Those timings become the captions, and they must survive the same rules.
vo_lines = captions.tidy_lines(timings)
check("voiceover timings make captions that never overlap",
      all(a["end_ms"] <= b["start_ms"] for a, b in zip(vo_lines, vo_lines[1:])))

check("an unknown voice folds down to the default rather than failing a paid run",
      tts.resolve_voice("Gandalf") == tts.DEFAULT_VOICE)
check("a known voice is matched case-insensitively", tts.resolve_voice("kore") == "Kore")
vo_quote = tts.estimate(LINES)
check("the voiceover estimate counts the characters it will send",
      vo_quote["characters"] == sum(len(l["text"]) for l in LINES) and vo_quote["usd"] > 0)
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
