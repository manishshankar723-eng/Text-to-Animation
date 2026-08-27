"""A SHOT CARD THAT IS ONLY A PICTURE AND A SENTENCE IS NOT A STORYBOARD.

    "Storyboard ko simple image grid mat banao … User ko image dekh kar aur
     metadata padh kar shot samajh aana chahiye."

A board already carried the framing, the location, the cast and what is spoken.
Two things a director reads off every real board were missing: HOW THE CAMERA
MOVES, and HOW LONG THE SHOT IS.

⚠ AND THE SECOND ONE IS NOT DECORATION. Added up, it answers the question behind
most briefs — "is my 30-second ad actually 30 seconds?" — and it is what the
animatic step needs to know before it draws key poses. Before this, that step
opened at a flat 4 seconds for every shot on every board, guessing at a number
the breakdown could have told it.

⚠ WHAT WAS DELIBERATELY *NOT* ADDED. The spec asked for thirteen fields per
shot, including a separate "shot type" beside "camera". `camera` ALREADY IS the
shot type — it holds "wide establishing", "close-up", "over-the-shoulder" — so a
second field would have been the same fact in two boxes that could disagree. It
is relabelled "Shot type" on the card instead. Expression and lighting stay
inside the description, where the image model reads them: every extra field is
one more thing the model can get wrong and one more box on a card people have to
scan.

What this file pins
-------------------
1. ⚠ NEITHER NEW FIELD REACHES AN IMAGE PROMPT. A still frame cannot show a
   camera move or a length; asking for one gets motion blur, speed lines or a
   little arrow drawn INTO the panel — the same class of artefact the
   anti-collage rules exist to stop. They travel exactly as `dialogue` does.
2. THE LENGTH IS CLAMPED. A model asked for a number will occasionally answer
   300, and one 300-second "shot" makes the runtime nonsense.
3. THEY SURVIVE THE WHOLE ROUTE — breakdown → shots → panels → job → card, PDF
   and animatic.
4. "static" IS NOT PRINTED. It is the answer for most shots; the absence of a
   move is what it means.
5. ONE RUNTIME FORMATTER. A film that reads "82s" in one place and "1m 22s" in
   another reads as two different numbers.

Run:
    python tests/shot_metadata_check.py
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
import storyboard_pdf as spdf  # noqa: E402

# ---------------------------------------------------------------------------
print("\n[1] the breakdown asks for them, and cleans what comes back")

props = sb._breakdown_schema().properties["shots"].items.properties
check("the shot schema carries both", "movement" in props and "duration_seconds" in props)
check("⚠ …and NOT a second 'shot type' beside camera — `camera` already IS the "
      "shot type, and the same fact in two boxes is two boxes that disagree",
      "shot_type" not in props
      and "the shot type / angle" in sb._PROMPT_TEMPLATE)
check("the prompt asks for a move in two or three words, and names 'static' as "
      "the usual answer",
      "how the camera MOVES" in sb._PROMPT_TEMPLATE
      and "'static'" in sb._PROMPT_TEMPLATE)
check("…and gives a real rule for the length rather than leaving it to taste",
      "one second " in sb._PROMPT_TEMPLATE
      and "do not pad" in sb._PROMPT_TEMPLATE)

check("⚠ the length is CLAMPED both ways — a model asked for a number will "
      "answer 300 sooner or later, and one 300-second shot makes the runtime "
      "nonsense",
      sb._coerce_seconds(300) == sb.MAX_SHOT_SECONDS
      and sb._coerce_seconds(0) == sb.DEFAULT_SHOT_SECONDS
      and sb._coerce_seconds(-4) == sb.DEFAULT_SHOT_SECONDS)
check("junk and nothing both fall back to the default, not to a crash",
      sb._coerce_seconds(None) == sb.DEFAULT_SHOT_SECONDS
      and sb._coerce_seconds("soon") == sb.DEFAULT_SHOT_SECONDS
      and sb._coerce_seconds("5") == 5)
check("a shot is one moment, so the ceiling is a shot's ceiling, not a film's",
      sb.MAX_SHOT_SECONDS <= 60)

shot = sb._coerce_shots([{
    "scene_number": 1, "shot_number": 1,
    "description": "Anna closes the laptop.",
    "camera": "medium", "movement": "  slow push-in ", "duration_seconds": 4,
}])[0]
check("both survive coercion, trimmed",
      shot["movement"] == "slow push-in" and shot["duration_seconds"] == 4)
check("a shot the model said nothing about still gets usable values",
      sb._coerce_shots([{"description": "A man walks in."}])[0]["duration_seconds"]
      == sb.DEFAULT_SHOT_SECONDS)

# ---------------------------------------------------------------------------
print("\n[2] ⚠ NEITHER OF THEM GOES ANYWHERE NEAR AN IMAGE PROMPT")

pipeline = read("storyboard_pipeline.py")
breakdown = read("script_breakdown.py")

# `gemini_client.generate_storyboard_panel` is where the picture's words are
# assembled — it already takes `camera`, and would take these two if anybody
# ever passed them.
gemini = read("gemini_client.py")
panel_fn = gemini.split("def generate_storyboard_panel")[1].split("\ndef ")[0]

signature = panel_fn.split(")")[0]
check("⚠ the image call cannot even take them — neither is a parameter of "
      "generate_storyboard_panel, which is where a panel's words are assembled",
      "movement" not in signature and "duration" not in signature)
check("…and nothing anywhere hands them to it",
      "movement=" not in pipeline and "duration_seconds=" not in pipeline
      and "movement=" not in gemini and "duration_seconds=" not in gemini)
# ⚠ NOTE FOR THE NEXT PERSON: the word "movement" DOES appear inside that
# function, and it is not this field. It is about how much the BODY of a
# character moves within the frame — nothing to do with the camera. Grepping the
# bare word here gives a false positive; that is why these two checks look at
# the signature and the call sites instead.
check("the reason is written down where the next person will look",
      "NEVER REACH AN IMAGE PROMPT" in breakdown
      and "CARRIED, NOT PROMPTED" in pipeline)
check("…and the ban is stated in the breakdown's schema too, beside the field",
      "DIRECTOR'S METADATA, NOT PROMPT MATERIAL" in breakdown)
check("⚠ …and it is the same arrangement `dialogue` already has, which is the "
      "precedent being followed rather than a new rule",
      "NOT part of the image prompt" in pipeline)

# ---------------------------------------------------------------------------
print("\n[3] they survive the whole route")

check("the pipeline copies them onto the panel",
      '"movement": shot.get("movement", "") or ""' in pipeline
      and '"duration_seconds": int(shot.get("duration_seconds") or 0)' in pipeline)
common = read("server", "common.py")
check("…and so does the panel rebuilt from shots when one was never drawn",
      '"movement": s.get("movement", "") or ""' in common
      and '"duration_seconds": int(s.get("duration_seconds") or 0)' in common)
schemas = read("server", "schemas.py")
shot_model = schemas.split("class Shot(BaseModel)")[1].split("class ")[0]
check("the wire model carries them, and bounds the seconds",
      "movement: str" in shot_model
      and "duration_seconds: int = Field(3, ge=0, le=30)" in shot_model)
check("⚠ …and the model says WHY they are there and not in the prompt, so the "
      "next person does not 'fix' it by adding them",
      "DIRECTOR'S METADATA" in shot_model and "still panel cannot show" in shot_model)
main = read("server", "main.py")
check("⚠ the library card still drops every panel field it does not read — a "
      "new key that isn't on that list ships the whole board in every card",
      '"movement", "duration_seconds",' in main)
check("a hand-inserted panel starts with them too, so the shape never varies",
      '"movement": "",' in main and '"duration_seconds": 0,' in main)

# ---------------------------------------------------------------------------
print("\n[4] what a person actually sees")

pdf_src = read("storyboard_pdf.py")
check("the PDF prints framing, move and length as ONE row",
      "def _shot_line(panel" in pdf_src
      and '_meta_row(draw, x, ty, "Camera", _shot_line(p)' in pdf_src)
check("⚠ …one row and not three, because META_H reserves the space under every "
      "panel and two more rows push the cast chips off a dense page",
      "ONE ROW AND NOT THREE" in pdf_src)
check("⚠ 'static' is not printed — it is the answer for most shots, and the "
      "absence of a move is what it means",
      spdf._shot_line({"camera": "wide", "movement": "static", "duration_seconds": 4})
      == "wide · 4s"
      and spdf._shot_line({"camera": "close-up", "movement": "slow push-in",
                           "duration_seconds": 3})
      == "close-up · slow push-in · 3s")
check("a shot with none of it prints nothing at all, not an empty label",
      spdf._shot_line({}) == "")
check("junk seconds don't crash the PDF",
      spdf._shot_line({"camera": "medium", "duration_seconds": "x"}) == "medium")

review = read("client", "src", "components", "ScriptToStoryboard.jsx")
board = read("client", "src", "components", "StoryboardBoard.jsx")

check("the shot card can edit both",
      "updateShot(i, { movement: e.target.value })" in review
      and "duration_seconds: Number(e.target.value) || 0" in review)
check("…and the camera field is relabelled 'Shot type', which is what it holds",
      "<label>Shot type</label>" in review)
check("⚠ THE RUNTIME IS ON THE REVIEW STEP — the one number that is about the "
      "FILM, and the answer to 'is my 30-second ad actually 30 seconds?'",
      "function totalSeconds()" in review and "formatRuntime(totalSeconds())" in review)
check("the board prints the same slug line under each panel",
      "function shotLine(p)" in board and "board-shotline" in board)
check("…and the same total at the top",
      "boardSeconds > 0" in board and "formatRuntime(boardSeconds)" in board)
check("⚠ ONE formatter, exported and shared — '82s' here and '1m 22s' there "
      "reads as two different numbers",
      "export function formatRuntime(seconds)" in review
      and 'import { formatRuntime } from "./ScriptToStoryboard.jsx";' in board)
check("the board's slug line hides 'static' exactly as the PDF does",
      '"static", "none", "no movement", "still"' in board)
check("a board from before this change shows no runtime rather than 0s",
      "boardSeconds > 0 ?" in board)

# ---------------------------------------------------------------------------
print("\n[5] ⚠ the length is USED, not just displayed")

strip = read("client", "src", "components", "PanelSequenceStrip.jsx")
check("the animatic step opens at the shot's own planned length",
      "plannedSeconds = 0," in strip
      and "useState(plannedSeconds > 0 ? plannedSeconds : 4)" in strip)
check("…and the board hands it down",
      "plannedSeconds={Number(p.duration_seconds) || 0}" in board)
check("⚠ …with the old flat 4 kept as the fallback, so a board made before this "
      "behaves exactly as it did",
      ": 4)" in strip and "falls back to the old 4" in strip)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)

print("A shot now says how it moves and how long it lasts — and the film says "
      "how long IT is.")
