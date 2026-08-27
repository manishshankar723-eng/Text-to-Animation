"""A 30-SECOND CONCEPT CAME BACK AS A 29-SHOT, 1m 04s BOARD.

Live test, 27 Aug 2026. The user approved a 30-second Ganesh Utsav concept. What
was drawn — and paid for — was twenty-nine panels, about ten of them the same
picture twice. Four separate holes, all of them the same hole:

B1  THE BREAKDOWN WAS STILL BEING TOLD TO SPLIT. `_PROMPT_TEMPLATE` said "Err on
    the side of MORE shots … is three or four shots, not one." That was correct
    while users pasted PROSE. Since the concept gate landed, scripts arrive from
    `plan_agent.write_script()` → `script_to_text()`, which already writes ONE
    BEAT PER LINE. Told to break a beat that is already a beat, the model broke
    it again: scene 1's three lines — the idol's face, the golden light on it,
    the flowers before it — came back as three almost identical close-ups plus a
    fourth. Scene 4, three different people doing three different things, was
    boarded correctly, which is the proof the model could do it and the
    instruction was what was wrong.

B2  NOBODY TOLD THE BREAKDOWN HOW LONG THE FILM WAS. `concept_seconds()` reads
    30 off the approved card, `write_script()` is told to write 30 seconds of
    words — and the number stopped there. `break_down_script()` had no `seconds`
    parameter at all, so it boarded a 50-word voice-over at whatever density it
    liked.

B3  A VOICE-OVER LINE GOT ITS OWN PANEL. "NARRATOR (V.O.): The spirit of Ganesh
    Utsav awakens." became scene 1's shot 4, with a fourth drawing of the same
    idol invented to carry it. Nothing in the prompt said a line of speech is not
    a picture. The shape that already works — the line living in a neighbouring
    shot's `dialogue` — is what the prompt now asks for.

B4  THE BOX THE USER EDITS WAS THE CLIPPED ONE. The review card's "Image prompt"
    was a fixed 64px with its own scrollbar; the read-only "FROM YOUR SCRIPT" box
    directly above it showed every word. Read-only whole, editable clipped.

⚠ WHY THE BUDGET IS ARGUED AND NOT CLAMPED. Truncating the returned list to the
shot budget would delete the END of the story, which is worse than a board that
runs long — see the same reasoning on `MAX_SHOTS` in `_coerce_shots`. So the
model is given the total to add up to, the shot range to sit in, and one blunt
instruction: if you are long, MERGE, never trim. The review step's runtime chip
turns amber when it did not listen, which is the last look before panels are
drawn and money is spent.

Run:
    python tests/shot_density_check.py
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


import script_breakdown as sb  # noqa: E402 — after sys.path is set

# A script in exactly the shape `plan_agent.script_to_text()` writes.
BEAT_SCRIPT = """GANESH UTSAV

LOGLINE: A city wakes for the festival.

CAST
ARTISAN — a weathered sculptor with clay under his nails

SCENE 1. INT. WORKSHOP - DAWN
The unfinished clay face of Ganesh sits in shadow.
Golden light creeps across it.
Marigold flowers lie heaped before it.
NARRATOR (V.O.): The spirit of Ganesh Utsav awakens.

SCENE 2. INT. WORKSHOP - DAY
The artisan's hand picks up a fine brush and paints the eye.

CALL TO ACTION: Experience the divine joy.
"""

PROSE = (
    "Once upon a time in a great forest there lived a hunter named Lubdhaka, who "
    "was poor and often hungry, and one evening he climbed a bel tree to escape a "
    "tiger and stayed awake all night, dropping leaves onto a lingam below "
    "without ever knowing what he had done. By morning Shiva himself stood before "
    "him, and the hunter fell to his knees in the wet grass, not understanding "
    "why he of all people had been chosen."
)

# ---------------------------------------------------------------------------
print("\n[1] B1 — the breakdown knows whether the script is already in beats")

check("a script written by the script writer is recognised",
      sb._is_beat_script(BEAT_SCRIPT) is True)
check("⚠ …and pasted prose is NOT. Prose is what this module always assumed, so "
      "it stays the fallback: a wrong 'beats' verdict would UNDER-cut a story "
      "nobody has divided yet",
      sb._is_beat_script(PROSE) is False
      and sb._is_beat_script("") is False)
check("scene headings alone are not enough — a pasted screenplay of real "
      "paragraphs still gets the prose rule",
      sb._is_beat_script(
          "SCENE 1. EXT. STREET - DAY\n"
          + ("He walks. Then he stops. Then he turns and sees her. " * 6) + "\n"
          + ("She waits. Then she smiles. Then she looks away. " * 6) + "\n"
          + ("Traffic passes. Then it thins. Then it stops. " * 6) + "\n"
      ) is False)

check("the two density rules are alternatives, never both",
      "Err on the side " in sb._DENSITY_PROSE
      and "Err on the side " not in sb._DENSITY_BEATS
      and "ONE LINE IS ONE SHOT" in sb._DENSITY_BEATS)
check("⚠ the beat rule pushes the OTHER WAY — merge, not split. This is the "
      "whole fix: the model is not being asked to try harder at the same job, "
      "it is being told the job is different",
      "Never re-split a line that is" in sb._DENSITY_BEATS
      and "are ONE shot, not three" in sb._DENSITY_BEATS
      and "'and' is not a cut" in sb._DENSITY_BEATS)
check("…and the system prompt's own splitting rule swaps with it, or it would "
      "contradict the prompt below it",
      "SPLIT AN ACTION INTO ITS BEATS" in sb._system_instruction(False)
      and "SPLIT AN ACTION INTO ITS BEATS" not in sb._system_instruction(True)
      and "ALREADY DIVIDED INTO BEATS" in sb._system_instruction(True))
check("the rules that are about CONTINUITY, not density, survive in both — "
      "nothing moves on its own either way",
      all("NOTHING MOVES ON ITS OWN" in s and "POSTURE CARRIES FORWARD" in s
          for s in (sb._system_instruction(True), sb._system_instruction(False))))
check("the old constant still means the prose reading, for the checks that "
      "read it",
      sb._SYSTEM_INSTRUCTION == sb._system_instruction(False))

# ---------------------------------------------------------------------------
print("\n[2] B2 — the film's length reaches the breakdown, and is a shot count")

check("`break_down_script` takes it at all — it did not exist before",
      "seconds: int | None = None" in read("script_breakdown.py"))

block, ceiling = sb._duration_budget(30)
check("30 seconds becomes a total to hit and a range to sit in",
      "30 SECONDS LONG" in block
      and "between 27 and 33" in block
      and "8–15 shots" in block)
check("⚠ …and a ceiling well under the 29 shots that were actually drawn",
      ceiling == 20)
check("a longer film gets a proportionally larger budget, not a fixed one",
      sb._duration_budget(60)[1] == 40 and sb._duration_budget(15)[1] == 10)
check("⚠ MERGE, NEVER TRIM. Truncating to the budget would delete the END of "
      "the story — the one failure worse than running long",
      "MERGE" in block and "NEVER TRIM" in block
      and "the ending included" in block)
check("no length given means no budget at all, rather than an invented one — a "
      "pasted script has agreed no runtime with anybody",
      sb._duration_budget(None) == ("", None)
      and sb._duration_budget(0) == ("", None)
      and sb._duration_budget("later") == ("", None))

srv = read("server", "main.py")
schemas = read("server", "schemas.py")
api = read("client", "src", "api.js")
ui = read("client", "src", "components", "ScriptToStoryboard.jsx")
check("⚠ THE WHOLE CHAIN CARRIES IT, or it is worth nothing: concept card → "
      "script writer → breakdown",
      "seconds: int | None = Field(" in schemas
      and "seconds=body.seconds" in srv
      and "seconds: seconds || null" in api
      and "startBreakdown(res.script, res.seconds)" in ui)
check("…and the pasted-script path still calls it with no length, which is the "
      "honest answer there",
      "async function startBreakdown(text, seconds = null)" in ui
      and "startBreakdown(text);" in ui)
check("the review step shows the approved length beside the real one, and says "
      "so when the board overshoots",
      "function overRunning()" in ui
      and "targetSeconds * 1.2" in ui
      and "chip-warn" in ui
      and ".review-summary .chip-warn" in read("client", "src", "styles", "storyboard.css"))

# ---------------------------------------------------------------------------
print("\n[3] B3 — a line of speech is not a picture")

check("the prompt says so in as many words",
      "A LINE OF SPEECH IS NOT A SHOT" in sb._SPEECH_RULE
      and "(V.O.)" in sb._SPEECH_RULE)
check("⚠ …and says WHERE it goes instead, because 'do not' on its own leaves "
      "the model nowhere to put the line",
      "in the `dialogue` of the shot it plays OVER" in sb._SPEECH_RULE)
check("a (V.O.) speaker is not added to the shot's cast, and no narrator is "
      "invented to look at",
      "not in frame" in sb._SPEECH_RULE
      and "narrator figure" in sb._SPEECH_RULE)
check("⚠ the exact failure is named: a repeated picture to carry a line is the "
      "proof the line belonged to a shot that already existed",
      "NEVER REPEAT A PICTURE IN ORDER TO CARRY A LINE" in sb._SPEECH_RULE)
# ⚠ CAUGHT ON THE FIRST LIVE RUN OF THIS VERY RULE. The end card came back as a
# real shot — correct — but its description read "…with the text 'Celebrate
# Ganesh Chaturthi…' superimposed on screen", while `_SINGLE_FRAME_RULE` in
# gemini_client tells the image model "No text, captions, speech bubbles". Both
# at once is a frame of misspelt gibberish, paid for.
check("⚠ 'ON SCREEN:' text is NEVER written into the picture — `description` IS "
      "the image prompt, and the image model is told in the same breath to "
      "draw no lettering at all",
      "NEVER WRITTEN INTO THE PICTURE" in sb._SPEECH_RULE
      and "'superimposed'" in sb._SPEECH_RULE
      and "'with the text'" in sb._SPEECH_RULE)
check("…but an end card is still ALLOWED to be a shot — it is a real shot of "
      "the film; only its words may not be drawn",
      "MAY be a shot of its own" in sb._SPEECH_RULE)
check("…and the words are not lost: they go to `dialogue`, the one field no "
      "image prompt ever reads",
      "the character 'ON SCREEN'" in sb._SPEECH_RULE)
check("the warning is repeated where `description` is actually written, "
      "because that is where the model makes the mistake",
      "THIS SENTENCE IS THE IMAGE PROMPT" in sb._PROMPT_TEMPLATE
      and "no 'with the text" in sb._PROMPT_TEMPLATE)
check("…and it matches what the image side actually forbids",
      "No text, captions, speech " in read("gemini_client.py"))

check("the script's own furniture is not boardable either",
      "'LOGLINE:'" in sb._SPEECH_RULE and "CAST block" in sb._SPEECH_RULE)
check("the `dialogue` field points back at the same rule, where the model is "
      "actually filling it in",
      "belongs HERE, in the shot it plays over" in sb._PROMPT_TEMPLATE)

# ---------------------------------------------------------------------------
print("\n[4] the assembled prompt is one document, not three loose blocks")

density = sb._DENSITY_BEATS.format(max_shots=20)
budget, _ = sb._duration_budget(30)
prompt = sb._PROMPT_TEMPLATE.format(
    density=density, budget=budget, speech=sb._SPEECH_RULE, script=BEAT_SCRIPT
)
check("every slot is filled and nothing is left unformatted",
      "{" not in prompt.replace("{character, line}", "")
      and "ONE LINE IS ONE SHOT" in prompt
      and "30 SECONDS LONG" in prompt
      and "A LINE OF SPEECH IS NOT A SHOT" in prompt)
check("⚠ the doubled braces survive — `{character, line}` is the dialogue shape "
      "the model is asked for, and a single brace here is a KeyError at "
      "format time",
      "{character, line}" in prompt)
check("the prose path formats too, and carries no budget when none was given",
      "THIS FILM IS" not in sb._PROMPT_TEMPLATE.format(
          density=sb._DENSITY_PROSE.format(max_shots=120),
          budget="", speech=sb._SPEECH_RULE, script=PROSE,
      ))

# ---------------------------------------------------------------------------
print("\n[5] B4 — the box you EDIT is no longer the clipped one")

grow = read("client", "src", "components", "GrowTextarea.jsx")
css = read("client", "src", "styles", "storyboard.css")

check("the shot description is a growing box now, not a fixed textarea",
      "<GrowTextarea" in ui
      and 'import GrowTextarea from "./GrowTextarea.jsx"' in ui)
check("⚠ the height is MEASURED, borders included — `scrollHeight` alone is "
      "short by the border and `overflow: hidden` then eats the last line",
      "el.style.height = \"auto\"" in grow
      and "el.scrollHeight + el.offsetHeight - el.clientHeight" in grow)
check("…and `height: auto` comes first, or the box could grow but never shrink",
      grow.index('el.style.height = "auto"')
      < grow.index("el.scrollHeight + el.offsetHeight"))
check("the stylesheet stops the two things that fight a computed height: the "
      "drag handle and the inner scrollbar",
      "resize: none;" in css.split(".shot-desc {")[1].split("}")[0]
      and "overflow: hidden;" in css.split(".shot-desc {")[1].split("}")[0])
check("…and states the line-height, because that is what the height is "
      "computed from — the same arrangement `.admin-quiet-field` has",
      "line-height:" in css.split(".shot-desc {")[1].split("}")[0]
      and "line-height:" in read("client", "src", "styles", "admin.css")
      .split(".admin-quiet-field {")[1].split("}")[0])
check("⚠ it does NOT commit on Enter. `GrowText` in admin/fields.jsx does, "
      "because every caller there is editing ONE line of copy; a shot "
      "description is a paragraph and a swallowed newline would be a bug",
      "e.preventDefault()" not in grow
      and "e.preventDefault()" in read("client", "src", "admin", "fields.jsx"))

# ⚠ THE SAME BOX EXISTS ON TWO MORE STEPS AND HAD THE SAME FAULT. Seen on the
# live cast page: "…a salwar kameez or lehenga choli). Her face is innocent and
# expressive. She" — cut off mid-sentence behind a scrollbar. And this is the
# text that DRAWS the character, so it is exactly the wrong one to hide.
cast_ui = read("client", "src", "components", "StoryboardCast.jsx")
assets_ui = read("client", "src", "components", "StoryboardAssets.jsx")
check("the cast step's description box grows too — it is the text the "
      "character is drawn FROM",
      "<GrowTextarea" in cast_ui
      and 'import GrowTextarea from "./GrowTextarea.jsx"' in cast_ui)
check("…and the props step's, which shares the very same class",
      "<GrowTextarea" in assets_ui
      and 'import GrowTextarea from "./GrowTextarea.jsx"' in assets_ui)
check("…and `.cast-desc` stops fighting it, exactly as `.shot-desc` does",
      all(rule in css.split(".cast-desc {")[1].split("}")[0]
          for rule in ("resize: none;", "overflow: hidden;", "line-height:")))

# ---------------------------------------------------------------------------
print("\n[6] WHO IS WORTH A REFERENCE — the count, not a guess")
# A board came back with a full character sheet for an artisan who appears ONLY
# as a pair of hands in one close-up. Reported twice. ⚠ Detecting "hands only"
# from the wording would be a guess; the number of shots someone is in is a
# FACT, it is already known here, and it answers the same question — this step
# is optional, so the user can skip the cheap ones themselves.
check("computeCast counts the shots each character appears in",
      "shotCount: 1," in ui and "existing.shotCount += 1;" in ui)
check("⚠ …and a name listed twice in ONE shot still counts once — the key is "
      "lower-cased BEFORE the de-duplication, or 'Ananya' and 'ANANYA' in one "
      "list would read as two appearances",
      "inThisShot.set(name.toLowerCase(), name)" in ui)
check("the cast card shows it beside the name, with the reasoning on hover "
      "rather than in the layout",
      "cast-shots" in cast_ui
      and "shot{ch.shotCount === 1" in cast_ui
      and "may not be worth it here" in cast_ui)
check("…and the one-shot case is marked, because that is the one worth "
      "looking at twice before spending an image",
      ".cast-shots.one {" in css)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("The script is cut once, at the length that was approved, and every panel "
      "is a picture.")
