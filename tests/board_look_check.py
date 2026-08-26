"""THE BOARD IS DRAWN IN THE STYLE THAT WAS PICKED — cast sheet included.

One report, with five screenshots of a real 28-panel mobile-app promo:

    "storyboard mai ek promotion video mobile app ka bana raha tha to jab set
     your cast page mai gaya aur ai se character image generate karwaya to woh
     cartoon character ka image dene laga jabki maine visual mai cinematic
     choose kiya tha … shot 2 ka character cartoon generate hua aur shot 3 mai
     do do phone dikh raha hai, only haath mai phone hona chahiye"

Three separate faults, and the first one causes the second.

---------------------------------------------------------------------------
1. THE CAST SHEET WAS ALWAYS A PIXAR CARTOON
---------------------------------------------------------------------------
`_REFERENCE_PROMPT_TEMPLATE` opened with the words "Generate a 3D animated
Pixar-style character in T-pose", and `generate_character_reference()` had no
style argument to override it with — nor did the route, nor the client. So a
board set to Cinematic got cartoons on the cast page.

⚠ AND A CAST SHEET IS NOT A PASSPORT PHOTO. It is fed into every panel that
character appears in as a look reference, and a picture beats a sentence: the
cartoon sheet out-voted the panel's own "photorealistic cinematic film still"
line. That is why the reported board came back in TWO MEDIUMS — shots whose
characters had a sheet were 3D cartoons, shots whose characters had none were
photoreal. Same film, same style setting, two looks.

---------------------------------------------------------------------------
2. GENRE NEVER REACHED A PICTURE
---------------------------------------------------------------------------
Genre was collected on the form, stored on the job, and spent entirely on the
TEXT breakdown's tone and pacing. `generate_storyboard_panel()` had no genre
parameter at all, so Documentary and Commercial drew identically-lit frames.

⚠ GENRE IS NOT STYLE, AND THE PROMPT HAS TO SAY SO. Style is what the picture
is made of; genre is how the moment is lit and staged. "Documentary in
charcoal" is a real answer, so the genre block carries an explicit rule that it
must not overrule the medium.

---------------------------------------------------------------------------
3. TWO PHONES IN A ONE-PHONE SHOT
---------------------------------------------------------------------------
"She holds her smartphone, frowning at the screen which displays a food
delivery app" came back as a woman holding a phone AND a second, much larger
phone floating beside her with the app on it. The prompt said "Single frame",
which the model honoured — it did not read a floating device inset as a second
FRAME. Advertising layouts are everywhere in the training data, so any shot
involving a screen or a product pulls toward a composite unless the ban names
the specific shapes it takes.

Run:
    python tests/board_look_check.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(label)


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


import gemini_client as g  # noqa: E402 — after sys.path is set

# ---------------------------------------------------------------------------
print("\n[1] the cast sheet is drawn in the board's medium")

cinematic = g.build_reference_prompt("a young professional in business casual", "cinematic")
pixar = g.build_reference_prompt("a young professional in business casual", "animation-3d")
sketch = g.build_reference_prompt("a young professional in business casual", "rough-sketch")
neutral = g.build_reference_prompt("a young professional in business casual", "")

check("⚠ a Cinematic board's sheet is a PHOTOGRAPH of a real person",
      "REAL human being" in cinematic and "photographed" in cinematic)
check("…and says outright that it is not a cartoon or a render",
      "NOT a cartoon" in cinematic and "NOT a 3D render" in cinematic)
check("⚠ …and the word Pixar appears NOWHERE in it",
      "pixar" not in cinematic.lower())
check("a 3D board still gets the Pixar sheet it always got",
      "Pixar" in pixar)
check("a greyscale style gets a line-and-tone MODEL SHEET, not a thumbnail",
      "MODEL SHEET" in sketch and "no colour anywhere" in sketch)
check("⚠ …drawn PROPERLY, because a face 'suggested with a few marks' is "
      "useless as the thing every later panel matches against",
      "DRAWN\nPROPERLY" in sketch or "DRAWN PROPERLY" in sketch)
check("no style at all falls back to neutral wording, NOT to a cartoon",
      "pixar" not in neutral.lower() and "Character description:" in neutral)

# The pose is what makes a sheet usable as a reference, so it survives every
# medium — that is the half that must NOT vary.
check("every medium keeps the T-pose, the white ground and the single figure",
      all("T-pose" in p and "pure-white background" in p and "ONE figure" in p
          for p in (cinematic, pixar, sketch, neutral)))
check("…and every medium bans props, text and a second view on the sheet",
      all("no text" in p and "no second view" in p
          for p in (cinematic, pixar, sketch, neutral)))

# ---------------------------------------------------------------------------
print("\n[2] the style reaches the sheet — route, client and all")

check("generate_character_reference() takes a style",
      "style" in g.generate_character_reference.__code__.co_varnames)
schemas = read("server", "schemas.py")
check("ReferenceRequest carries it over the wire",
      "class ReferenceRequest" in schemas
      and "style: str = Field(" in schemas.split("class ReferenceRequest")[1].split("class ")[0])
main = read("server", "main.py")
check("…and the route passes it to the generator",
      "style=body.style," in main)
check("⚠ …and the sheet gets the same colour enforcement the panels get, "
      "because a coloured sheet re-colours the whole board",
      "conform_to_style(image, body.style)" in main)
api = read("client", "src", "api.js")
check("the client sends it",
      "export function generateReference(prompt, world, style," in api
      and "if (style) body.style = style;" in api)
cast_ui = read("client", "src", "components", "StoryboardCast.jsx")
check("…the cast page forwards it to the call",
      "api.generateReference(prompt, world, style" in cast_ui)
workflow = read("client", "src", "components", "ScriptToStoryboard.jsx")
check("⚠ …and the workflow hands down the board's REAL style, custom included",
      "style={effectiveStyle()}" in workflow)

# ---------------------------------------------------------------------------
print("\n[3] genre is art direction now, not just a library label")

doc = g.build_genre_context("Documentary")
commercial = g.build_genre_context("Commercial")
check("Documentary and Commercial are not the same instruction",
      doc and commercial and doc != commercial)
check("…Documentary asks for available light and unposed moments",
      "available light" in doc and "unposed" in doc)
check("…Commercial asks for the polished, aspirational frame",
      "aspirational" in commercial)
check("⚠ genre says out loud that it must NOT change the art style",
      "must NOT change the art style" in doc)
check("Default means no genre bias at all",
      g.build_genre_context("") == "" and g.build_genre_context("default") == "")
check("⚠ an id and its label land on the SAME entry — the form sends labels, "
      "older saved jobs hold ids",
      g.build_genre_context("music-video") == g.build_genre_context("Music Video")
      and g.build_genre_context("sci-fi") == g.build_genre_context("Science Fiction"))
check("a typed custom genre is passed through, not dropped",
      "Bollywood masala" in g.build_genre_context("Bollywood masala"))
check("generate_storyboard_panel() takes a genre",
      "genre" in g.generate_storyboard_panel.__code__.co_varnames)

pipeline = read("storyboard_pipeline.py")
check("…and all three pipeline entries pass it on",
      pipeline.count("genre=genre,") == 3)
check("⚠ a RE-STYLE keeps the board's genre, not the request's — relighting "
      "the whole film is not what 'change the art style' means",
      '"genre": job.params.get("genre") or "",' in main)
check("a single-panel redraw is lit like its neighbours",
      'genre=(job.params or {}).get("genre") or "",' in read("server", "common.py"))
check("…and so is a shot inserted between two others",
      'genre=params.get("genre") or "",' in read("server", "animatics.py"))

# ---------------------------------------------------------------------------
print("\n[4] one moment, one camera, one phone")

rule = g._SINGLE_FRAME_RULE
check("the collage shapes are named, not left to 'single frame'",
      all(w in rule for w in ("collage", "split screen", "inset",
                              "picture-in-picture")))
check("⚠ …including the exact one that was reported: a floating enlarged "
      "screen beside the subject",
      "floating enlarged screen" in rule and "mockup" in rule)
check("⚠ …and duplicate objects are banned by name, because the bug was TWO "
      "phones in a one-phone shot",
      "exactly ONE phone" in rule and "second enlarged copy" in rule)
check("the old no-text/no-border promises are still kept",
      "No text" in rule and "borders or watermarks" in rule)
check("…and the panel prompt actually uses the rule",
      "parts.append(_SINGLE_FRAME_RULE)" in read("gemini_client.py"))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The cast is drawn in the film's medium, the genre lights it, and there "
      "is one phone in the shot.")
