"""NOTHING GETS DRAWN FROM AN IDEA UNTIL SOMEBODY HAS SEEN WHAT WE MADE OF IT.

`script_intake.py` (Phase 2) can tell a brief or an idea from a script. Knowing
is not enough on its own — something still has to turn

    "Create a 30 second ad for an AI meeting assistant. Audience is busy
     professionals. Show how it saves time after meetings. Feel premium."

into a film, and that turning is INVENTION: who is on screen, what goes wrong,
how it ends. Before this stage the app made all of it in silence and the user
first met those decisions as twenty finished drawings they had already paid for.

    "AI ko approval ke bina storyboard generate nahi karna hai when the input
     is a brief/concept that requires creative interpretation."

What this file pins
-------------------
1. THE CONCEPT IS NOT THE SCRIPT. `script_concept` develops a direction and is
   told, in words, not to write dialogue or scene headings. ⚠ The approved
   concept goes through `plan_agent.write_script()` — never straight to shots —
   because the review step, `ScriptPanel` and every shot card's "FROM YOUR
   SCRIPT · LINE 12" need a real script to point at.
2. THE GATE DOES NOT FAIL OPEN, AND THAT IS THE OPPOSITE OF THE INTAKE. A dead
   classifier must not block a storyboard; a dead concept step MUST, because
   falling through it means building the film nobody approved.
3. WHAT THE USER SAID IS FIXED. The model is told not to replace their premise,
   their product, their audience or their stated length with something better.
4. THE APPROVED VERSION IS THE ONE THAT COUNTS. Every field on the card is
   editable, so `concept_to_brief` treats what comes back as an instruction —
   and carries the user's original words along for the details a concept has no
   field for.
5. AN EMPTY SCRIPT NEVER REACHES THE BREAKDOWN. That would be read as a blank
   story and come back as an invented one — this failure arriving by the back
   door.

Run:
    python tests/script_concept_check.py
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


import script_concept as sc  # noqa: E402 — after sys.path is set

mod = read("script_concept.py")

# ---------------------------------------------------------------------------
print("\n[1] ⚠ the concept is a DIRECTION, not a script")

check("the developer is told outright not to write the script",
      "YOU DO NOT WRITE THE SCRIPT" in sc._SYSTEM_INSTRUCTION)
check("…no dialogue, no scene headings, no shot list, no camera directions",
      all(p in sc._SYSTEM_INSTRUCTION
          for p in ("No dialogue.", "No scene headings.", "No shot ",
                    "No camera directions.")))
check("one concept, never a menu of options — they can edit it instead",
      "ONE CONCEPT, NOT A MENU" in sc._SYSTEM_INSTRUCTION)
check("the card is six fields and no more, so it reads in half a minute",
      set(sc._schema().properties) == {
          "title", "premise", "story_direction", "key_scenes",
          "duration_seconds", "visual_direction"})
check("key scenes are VISIBLE moments, not notes to a director",
      "can be seen" in sc._SYSTEM_INSTRUCTION
      and "establish her frustration" in sc._SYSTEM_INSTRUCTION)
check("the module says why a concept can't go straight to shots",
      "FROM YOUR SCRIPT · LINE 12" in mod and "write_script()" in mod)

# ---------------------------------------------------------------------------
print("\n[2] ⚠ what the user actually said is FIXED")

check("the model is told never to override what they stated",
      "WHAT YOU MUST NOT OVERRIDE" in sc._SYSTEM_INSTRUCTION
      and "product, the audience, the goal, the length, the tone"
      in sc._SYSTEM_INSTRUCTION)
check("…and that an idea's premise stays THEIR premise",
      "not swapping it for a better one" in sc._SYSTEM_INSTRUCTION)
check("it only invents what is missing and needed",
      "Invent only what is missing" in sc._SYSTEM_INSTRUCTION)
check("the form's own choices ride along, so the concept can't contradict a "
      "genre or a frame the user already picked",
      "- Genre chosen on the form:" in sc._form_context(genre="Commercial")
      and "9:16" in sc._form_context(aspect_ratio="9:16"))
check("…and a vertical frame is told to stage tight, not wide",
      "phone-first" in sc._form_context(aspect_ratio="9:16")
      and "phone-first" not in sc._form_context(aspect_ratio="16:9"))
check("'default' genre is not a genre", sc._form_context(genre="default") == "")
check("nothing chosen says nothing at all", sc._form_context() == "")
check("every field comes back in the user's own language, Hinglish included",
      "Hinglish" in sc._SYSTEM_INSTRUCTION
      and "do not switch to Devanagari" in sc._SYSTEM_INSTRUCTION)

# ---------------------------------------------------------------------------
print("\n[2b] ⚠ short-form: the HOOK comes first, and that is not a matter of taste")

# ⚠ FOUND IN LIVE TESTING, ON THIS EXACT BRIEF. The concept came back opening on
# a close-up of hands painting an idol's eyes and saved the finished, blazing
# idol for scene SEVEN. Beautiful — and wrong for a reel, where the first frame
# is what decides whether anybody sees the second one, so the best image arriving
# at second 26 arrives for nobody.
REEL_BRIEF = ("give me 30 sec Virl shots/reel script of upcoming festivel besed "
              "on ganesh chaturthi")

check("the reported brief is recognised as short-form", sc.is_short_form(REEL_BRIEF))
check("…and so are the other words people actually type",
      all(sc.is_short_form(t) for t in (
          "Make an instagram reel about our new app",
          "a viral video for diwali",
          "YouTube Shorts idea about street food",
          "TikTok for a bakery",
          "a scroll-stopping ad for a gym")))
check("⚠ 'SHORT FILM' IS NOT SHORT-FORM. A short film is five to twenty minutes "
      "and opens however it likes — it is the word 'short' doing double duty, "
      "and a hook rule on a narrative film would be wrong",
      not sc.is_short_form("A short film about a courier in Mumbai")
      and not sc.is_short_form("write a short story about a lost dog"))
check("an ordinary ad brief is left alone",
      not sc.is_short_form(
          "Create a 30 second advertisement for an AI meeting assistant.")
      and not sc.is_short_form("A man wakes up and everyone has disappeared"))

check("the rule says the first scene IS the hook, in as many words",
      "THE FIRST KEY SCENE IS THE HOOK" in sc._SHORT_FORM_RULE
      and "strongest, most" in sc._SHORT_FORM_RULE)
check("…and names what it must NOT be, since that is what it kept doing",
      "NOT the preparation" in sc._SHORT_FORM_RULE
      and "NOT a slow build" in sc._SHORT_FORM_RULE)
check("⚠ …and forbids the obvious failure mode of a hook rule — opening on "
      "something eye-catching that has nothing to do with the film",
      "THE HOOK MUST BE OF THIS FILM" in sc._SHORT_FORM_RULE
      and "A hook that lies is worse" in sc._SHORT_FORM_RULE)
check("…and still requires the rest of the film to land",
      "land the ending" in sc._SHORT_FORM_RULE)

mod_src = read("script_concept.py")
check("⚠ the rule is appended LAST, because it overrules the natural order of a "
      "story and the last thing a model reads is what it holds to",
      "LAST, AND ON PURPOSE" in mod_src
      and mod_src.index("ask += [\"\", _SHORT_FORM_RULE]")
      < mod_src.index("Give between {MIN_KEY_SCENES}"))
check("⚠ …and the trigger is what the user TYPED, never the aspect ratio they "
      "clicked — 9:16 is a frame, not a statement of intent",
      "never the aspect ratio" in mod_src or "not the aspect ratio" in mod_src)
check("a long-form brief never sees the rule at all",
      "if short_form:" in mod_src)

# ---------------------------------------------------------------------------
print("\n[3] the concept is cleaned before it is shown")

messy = sc._coerce(
    {
        "title": "  The Empty City  ",
        "premise": "A man wakes to an empty city.",
        "key_scenes": ["He wakes.", "  ", "", "Empty streets.", None],
        "duration_seconds": "0",
        "visual_direction": " still, cold ",
    },
    "idea",
)
check("blank scenes are dropped, not shown as empty rows",
      messy["key_scenes"] == ["He wakes.", "Empty streets."])
check("a missing runtime falls back to the kind's default, not to zero",
      messy["duration_seconds"] == sc.DEFAULT_SECONDS["idea"])
check("a brief defaults shorter than an idea — a brief is nearly always an ad",
      sc.DEFAULT_SECONDS["brief"] < sc.DEFAULT_SECONDS["idea"])
check("a runaway runtime is clamped both ways",
      sc._coerce({"duration_seconds": 99999}, "idea")["duration_seconds"]
      == sc.MAX_SECONDS
      and sc._coerce({"duration_seconds": 1}, "idea")["duration_seconds"]
      == sc.MIN_SECONDS)
check("junk in the number is not a crash",
      sc._coerce({"duration_seconds": "soon"}, "brief")["duration_seconds"]
      == sc.DEFAULT_SECONDS["brief"])
check("the scene list is capped — a concept is not a treatment",
      len(sc._coerce({"key_scenes": [f"s{i}" for i in range(40)]}, "idea")
          ["key_scenes"]) == sc.MAX_KEY_SCENES)
check("⚠ a half-read concept is an ERROR, not something shown as whole — the "
      "user would approve it and the missing half would be invented downstream",
      "NO SALVAGE ATTEMPT HERE" in mod and "raise ScriptConceptError" in mod)
check("an empty box never reaches the model",
      "There's nothing here yet" in mod)
check("a pasted essay is clipped before it is sent",
      sc.MAX_SOURCE_CHARS <= 10000 and "truncated" in mod)
check("the read is deterministic — the same brief gets the same concept",
      "_sampling_kwargs()" in mod)

# ---------------------------------------------------------------------------
print("\n[4] ⚠ the APPROVED version is the instruction, edits and all")

approved = {
    "title": "Your Meeting, Already Done",
    "premise": "A professional leaves a chaotic meeting and the notes write themselves.",
    "story_direction": "Meeting -> chaos -> AI writes it up -> she leaves on time",
    "key_scenes": ["The meeting overruns.", "", "She closes the laptop."],
    "duration_seconds": 30,
    "visual_direction": "premium, modern",
}
brief = sc.concept_to_brief(approved, source="Make a 30s ad for Lickyeat.")

check("the writer is told this was APPROVED and must be followed",
      "APPROVED by the user" in brief
      and "do not replace the story" in brief)
check("every field reaches the writer",
      "Your Meeting, Already Done" in brief
      and "notes write themselves" in brief
      and "she leaves on time" in brief
      and "premium, modern" in brief)
check("the scenes go in order, numbered, and the blank one is dropped",
      "1. The meeting overruns." in brief
      and "2. She closes the laptop." in brief
      and "3." not in brief)
check("⚠ the user's ORIGINAL words ride along — a concept has no field for a "
      "product name or a required line, and losing them shows up as a script "
      "that quietly forgot half the ask",
      "Lickyeat" in brief)
check("…and they are explicitly ranked below the approved concept",
      "Nothing here may contradict the approved concept" in brief)
check("a concept with no source still produces a usable brief",
      "Your Meeting, Already Done" in sc.concept_to_brief(approved))
check("the approved runtime is what the script is written to",
      sc.concept_seconds(approved) == 30
      and sc.concept_seconds({}) == 60
      and sc.concept_seconds({"duration_seconds": "x"}) == 60)

# ---------------------------------------------------------------------------
print("\n[5] the routes: a GATE, not a helper")

route = read("server", "script_concept.py")
main = read("server", "main.py")
schemas = read("server", "schemas.py")
intake_route = read("server", "script_intake.py")

check("both routes are wired into the app",
      "from .script_concept import router as script_concept_router" in main
      and "app.include_router(script_concept_router)" in main)
check("…behind the same feature gate as the workflow they belong to",
      route.count('require_feature("workflow.script-to-storyboard")') == 2)
check("⚠ THIS ONE RAISES, and the intake does not — a dead classifier must "
      "not block a storyboard, but a dead concept step MUST, because falling "
      "through it builds the film nobody approved",
      "raise HTTPException" in route
      and "raise HTTPException" not in intake_route)
check("the approved concept is written out by plan_agent.write_script",
      "from plan_agent import ScriptError, write_script" in route
      and "write_script(" in route)
check("…at the runtime the user approved",
      "seconds=seconds" in route and "concept_seconds(concept)" in route)
check("⚠ an EMPTY script never reaches the breakdown — it would be read as a "
      "blank story and come back as an invented one",
      "The script came back empty" in route)
check("the concept's own title wins over the writer's second opinion",
      'concept.get("title") or script.get("title")' in route)
check("every model call is recorded against the account",
      "usage_counters.record_tokens" in route)
check("the concept travels in BOTH directions, because the card is editable",
      "class StoryConcept" in schemas
      and "concept: StoryConcept" in schemas)
check("…and the runtime is bounded on the wire too",
      "duration_seconds: int = Field(60, ge=5, le=600)" in schemas)

# ---------------------------------------------------------------------------
print("\n[6] the screen: read it, change it, then approve it")

api = read("client", "src", "api.js")
ui = read("client", "src", "components", "ScriptToStoryboard.jsx")

check("the client has both calls",
      "export function developConcept(" in api
      and "export function conceptToScript(" in api)
check("⚠ …and knows they do NOT fail open, unlike intakeScript",
      "THESE TWO DO NOT FAIL OPEN" in api)
check("a brief or an idea opens the gate instead of the breakdown",
      'if (kind === "brief" || kind === "idea")' in ui
      and "api.developConcept(text" in ui
      and 'setStep("concept")' in ui)
check("⚠ …and a failure there does NOT fall through to breaking the raw brief "
      "down as a script",
      "NO FALLING THROUGH TO THE BREAKDOWN HERE" in ui)
check("the concept step exists and is its own screen",
      'if (step === "concept" && concept)' in ui)
check("every field on the card is editable",
      all(f in ui for f in ("updateConcept({ title:",
                            "updateConcept({ premise:",
                            "updateConcept({ story_direction:",
                            "updateConcept({ visual_direction:",
                            "updateKeyScene(i, e.target.value)")))
check("…scenes can be added and removed, because they ARE the panels",
      "＋ Add a scene" in ui and "key_scenes: scenes.filter(" in ui)
check("approving writes the script FIRST and breaks down that text",
      "api.conceptToScript(concept" in ui
      and "startBreakdown(res.script, res.seconds)" in ui)
check("⚠ …AND THE APPROVED LENGTH GOES WITH IT. `concept_seconds()` reads 30 "
      "off the card and the writer is told to write 30 seconds of words — and "
      "the breakdown, told no target, boarded them as 29 shots and 1m 04s. "
      "Every extra panel is a drawing that was paid for",
      "startBreakdown(res.script, res.seconds)" in ui
      and "seconds," in ui.split("api.breakdownScript(text, {")[1].split("})")[0]
      and "seconds: seconds || null" in api
      and "seconds=body.seconds" in read("server", "main.py"))
check("⚠ …and the user's own words stay in the box, not overwritten by the "
      "script we generated from them",
      "THE BOX KEEPS THE USER'S OWN WORDS" in ui)
check("the approved title names the board unless the user typed one",
      "if (res.title && !title.trim()) setTitle(res.title);" in ui)
check("an emptied concept cannot be approved",
      "function conceptReady()" in ui and "disabled={!conceptReady()}" in ui)
check("the long write wears the same ring as the breakdown, not a new spinner "
      "— and since the third round of reports it is literally the SAME ring, "
      "one element with its words swapping under it",
      "SCRIPT_STEPS" in ui
      and 'title={writing ? "Writing your script" : undefined}' in ui
      and "steps={writing ? SCRIPT_STEPS : undefined}" in ui)
check("…and that ring is the existing component, parameterised",
      "export const SCRIPT_STEPS" in read(
          "client", "src", "components", "BreakdownProgress.jsx"))

# ---------------------------------------------------------------------------
# \u26a0 ONE BAR FOR THE WHOLE WAIT, AND IT NEVER STOPS MOVING.
#
# Reported mid-test: "progress bar ruka hua hai … ye pehle complete ho gaya, fir
# kuch time pe open ho — jaise hi 100% ho, mera next page khulna chahiye."
#
# Two faults wearing one complaint:
#   1. IT FROZE. The fill was a flat 6.5%/sec to 96, a crawl to 99, and then a
#      dead stop. A 45-second breakdown left a motionless "99%" on screen for
#      half a minute, and 99% does not read as "working" — it reads as stuck.
#   2. IT FINISHED TWICE. Approving runs write_script() AND THEN the breakdown,
#      and each drove its own ring 0\u2192100. The user watched a bar complete and
#      then watched a second one start from zero.
bp = read("client", "src", "components", "BreakdownProgress.jsx")

check("⚠ ONE RING, MOUNTED ONCE, ACROSS BOTH CALLS. Two elements or two "
      "`key`s are two instances, and the second starts its own climb from "
      "zero — reported twice over: a bar finishing and a new one starting, "
      "then 'kabhi fast kabhi slow' when they were given half the bar each",
      'key="script"' not in ui and 'key="breakdown"' not in ui
      and "floor=" not in ui and "ceiling=" not in ui
      and "SCRIPT_PHASE_END" not in bp)
check("…and a call that is NOT the last hands off WHERE IT STANDS, so there is "
      "no sprint to a phase ceiling and no seam to see",
      "final={!writing}" in ui
      and "!finalRef.current && !firedRef.current" in bp)
check("⚠ the fill DECELERATES instead of hitting a wall — the rate is set by "
      "the distance still to go, so it is still creeping a minute in",
      "APPROACH_SECONDS" in bp
      and "(SOFT_TARGET - p) / APPROACH_SECONDS" in bp
      and "SOFT_CAP" not in bp and "HARD_CAP" not in bp)
check("…and it never claims to be finished before the work is",
      "SOFT_TARGET = 96" in bp and "Math.min(SOFT_TARGET, p +" in bp)
check("⚠ THE RING PAINTS 100 BEFORE IT HANDS OVER. Firing on the frame the "
      "number lands means React never renders it — reported as 'laga 100 gaya "
      "hi nahi aur open ho gaya'",
      "const SHOW_100_MS = 220;" in bp
      and "setTimeout(() => onDoneRef.current?.(), SHOW_100_MS)" in bp)
check("the finish is one fixed sweep from wherever it is, worked out once",
      "finishRate.current === null" in bp and "FINISH_SECONDS" in bp)
check("a new storyboard starts with no concept",
      "setConcept(null);" in ui and 'setConceptSource("");' in ui)
check("the screen says why it is asking",
      "Is this the right direction?" in ui
      and "nothing is drawn until you approve it" in ui)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)

print("A brief becomes a concept, the concept becomes a script, and the user "
      "says yes in between.")
