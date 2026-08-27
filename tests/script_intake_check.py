"""AN IDEA IS NOT A SCRIPT, AND THE APP HAS TO SAY SO BEFORE IT DRAWS ANYTHING.

The form has one box. Until `script_intake.py` every box of text was posted to
the breakdown as though it were a script, so:

    "A man wakes up and discovers that everyone in the city has disappeared."

came back as a film with scenes, a cast, locations and dialogue nobody wrote —
invented in silence, drawn, and charged for. The user's instruction was that the
product should work the other way round:

    "user AI se chat nahi kar raha hai, user AI ko material de raha hai …
     AI ko automatically samajhna chahiye ki user ne kya provide kiya hai"

⚠ AND THE USER MUST NOT BE MADE TO LABEL THEIR OWN INPUT. A "Script or Idea?"
toggle is not a fix: most people cannot say whether what they pasted is a brief
or a concept, and the one-box form exists precisely to stop asking.

What this file pins
-------------------
1. THE FREE READER IS RIGHT ABOUT THE CASES IT CLAIMS. `sniff()` costs nothing
   and runs on every board, so a wrong "script" here is the original bug back
   again, with no model in the loop to catch it. ⚠ Its false-positive trap is a
   CLIENT BRIEF: those are written in "LABEL: value" lines that look exactly
   like dialogue.
2. SILENCE IS ALLOWED. `sniff()` returning None is correct and cheap — it costs
   one small model call. Nothing here demands it recognise everything.
3. THE ASYMMETRY IS WRITTEN DOWN. The model is told, in words, that guessing
   "idea" is the cheap mistake and guessing "script" is the expensive one, and
   every failure path in the module lands on "idea", never on "script".
4. THE ROUTE IS WIRED, GATED AND COUNTED — and it does NOT raise, because the
   client's answer to an error is to carry on into the breakdown.
5. THE FORM FAILS OPEN. A dead classifier must never stop somebody making a
   storyboard.

Run:
    python tests/script_intake_check.py
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


import script_intake as si  # noqa: E402 — after sys.path is set

# ---------------------------------------------------------------------------
print("\n[1] the free reader recognises a real script")

SLUG_SCRIPT = """INT. COFFEE SHOP - NIGHT

John enters the coffee shop. He looks around and notices Sarah sitting alone at
the corner table.
John walks toward her, slowly, and then stops two steps away.
JOHN: I did not think you would come.
SARAH: I almost did not."""

AD_SCRIPT = """VOICEOVER: Tired of writing meeting notes?
ANNA: Every single evening, the same thing.
VOICEOVER: Not any more. Our assistant listens, and writes the summary for you.
ANNA: I got my evenings back.
VOICEOVER: Try it free today and see what happens to your week."""

OUR_OWN_FORMAT = """THE LAST DELIVERY

LOGLINE: A courier refuses to hand over the last parcel of her round.

CAST
MEERA - 30s, courier, permanently in a hurry.

SCENE 1. INT. WATCH SHOP - NIGHT
Meera puts the parcel on the counter and does not let go of it.
MEERA: You said it would be ready."""

check("a scene heading is a script, for free", si.sniff(SLUG_SCRIPT) == "script")
check("⚠ …and so is an ad script with NO scene headings at all, which is the "
      "most common thing this product is pointed at",
      si.sniff(AD_SCRIPT) == "script")
check("…and the layout our own writer emits (plan_agent.write_script)",
      si.sniff(OUR_OWN_FORMAT) == "script")
check("a lower-case slug still counts — people type 'Int.' too",
      si.sniff(SLUG_SCRIPT.replace("INT.", "Int.")) == "script")
check("no model was involved in any of that",
      si.intake(SLUG_SCRIPT)["decided_by"] == "sniff"
      and si.intake(SLUG_SCRIPT)["usage"] == {})

# ---------------------------------------------------------------------------
print("\n[2] ⚠ the free reader does NOT mistake a brief for a script")

CLIENT_BRIEF = """BRIEF: Create a 30 second advertisement for an AI meeting assistant.
TARGET AUDIENCE: busy professionals who sit in four calls a day, every day.
GOAL: show how the AI saves time after meetings and gives people their evenings back.
TONE: premium and modern, never cluttered."""

PROSE_BRIEF = """Create a 30 second advertisement for an AI meeting assistant.
The target audience is busy professionals. The video should show how the AI
saves time after meetings. The style should feel premium and modern."""

check("⚠ a brief written in 'LABEL: value' lines is NOT fast-pathed as a "
      "script — those lines look exactly like dialogue and this is the whole "
      "reason _NOT_SPEAKERS exists",
      si.sniff(CLIENT_BRIEF) is None)
check("…and a brief written in plain prose isn't either",
      si.sniff(PROSE_BRIEF) is None)
check("the label list actually covers what a brief calls its lines",
      {"BRIEF", "TARGET AUDIENCE", "GOAL", "TONE", "AUDIENCE", "DURATION"}
      <= si._NOT_SPEAKERS)
check("a one-line premise is not a script", si.sniff(
    "A man wakes up and discovers that everyone in the city has disappeared."
) is None)
check("a wish with no subject is not a script",
      si.sniff("I want to make something emotional.") is None)
check("⚠ …and none of those were answered for free — a None means ASK, and "
      "asking is what stops the app inventing a film",
      all(si.sniff(t) is None for t in (CLIENT_BRIEF, PROSE_BRIEF)))

# ---------------------------------------------------------------------------
print("\n[3] an empty box is answered for free, and is not an error")

check("nothing typed reads as 'empty'", si.sniff("") == "empty")
check("…and so does whitespace", si.sniff("   \n\t ") == "empty")
check("intake() agrees, without a model",
      si.intake("  ")["kind"] == "empty"
      and si.intake("  ")["decided_by"] == "sniff")
check("classify() short-circuits on empty too, so a stray call costs nothing",
      si.classify("")["kind"] == "empty")

# ---------------------------------------------------------------------------
print("\n[4] ⚠ the tie goes to 'idea' — the cheap mistake, never the expensive one")

mod = read("script_intake.py")
check("the module says which mistake is worse, and why",
      "TIE GOES TO `idea`" in mod and "invent" in mod)
check("⚠ the MODEL is told as well — a classifier with no stated asymmetry "
      "splits the difference",
      "ANSWER idea (or brief)" in si._SYSTEM_INSTRUCTION
      and "Choose the cheap mistake." in si._SYSTEM_INSTRUCTION)
check("…and told that prose about a story is an idea however detailed it is",
      "Prose ABOUT a story is an idea" in si._SYSTEM_INSTRUCTION)
check("an unparseable reply lands on 'idea', NOT on 'script'",
      "treating the text as an idea" in mod and 'kind = "idea"' in mod)
check("an unknown kind from the model lands on 'idea' too",
      "if kind not in KINDS:" in mod)
check("the five kinds are the five the client branches on",
      si.KINDS == ("script", "brief", "idea", "vague", "empty"))

check("⚠ the reader is told it must not write anything — it names the text, "
      "it does not improve it",
      "YOU DO NOT WRITE" in si._SYSTEM_INSTRUCTION)
check("a script gets no critique attached to it",
      'if kind == "script":' in mod and "reason = \"\"" in mod)
check("only 'vague' gets a question, and only ONE",
      'if kind != "vague":' in mod
      and "ONE question" in si._SYSTEM_INSTRUCTION
      and "never two questions" in si._SYSTEM_INSTRUCTION.lower())
check("the reply comes back in the user's own language, Hinglish included",
      "Hinglish" in si._SYSTEM_INSTRUCTION
      and "do not switch to Devanagari" in si._SYSTEM_INSTRUCTION)

# ---------------------------------------------------------------------------
print("\n[5] the call is small, and it is capped")

check("the schema is three short strings and nothing else — this runs in "
      "front of every board",
      set(si._schema().properties) == {"kind", "reason", "question"})
check("…and the model must answer with one of the five kinds",
      list(si._schema().properties["kind"].enum) == list(si.KINDS))
check("a pasted feature film is clipped before it is sent",
      si.MAX_INTAKE_CHARS <= 8000 and "truncated" in mod)
check("the read is deterministic — the same paste gets the same verdict",
      "_sampling_kwargs()" in mod)

# ---------------------------------------------------------------------------
print("\n[6] the route: wired, gated, counted — and it does not raise")

route = read("server", "script_intake.py")
main = read("server", "main.py")
schemas = read("server", "schemas.py")

check("the router is included in the app",
      "from .script_intake import router as script_intake_router" in main
      and "app.include_router(script_intake_router)" in main)
check("it is behind the SAME feature gate as the workflow it belongs to",
      'require_feature("workflow.script-to-storyboard")' in route)
check("⚠ every model call is recorded against the account, or the monthly "
      "total on screen quietly stops being true",
      "usage_counters.record_tokens" in route)
check("…and the free path records nothing, because it spent nothing",
      "if usage:" in route)
check("⚠ the route NEVER raises — the client's answer to an error is to carry "
      "on into the breakdown, so a 502 would only be a box to dismiss",
      "raise HTTPException" not in route
      and 'ScriptIntakeResponse(kind="idea", decided_by="error")' in route)
_req_body = schemas.split("class ScriptIntakeRequest")[1].split("class ")[0]
_req_fields = [ln.strip() for ln in _req_body.splitlines()
               if ": " in ln and "Field(" in ln]
check("⚠ the request carries the text and NOTHING else — there is no `kind` "
      "coming in, because the browser does not know either and must never be "
      "made to guess",
      "class ScriptIntakeRequest" in schemas
      and len(_req_fields) == 1
      and _req_fields[0].startswith("text: str = Field("))
check("the response says which reader answered, so the free path can be "
      "seen paying for itself",
      "decided_by" in schemas.split("class ScriptIntakeResponse")[1].split("class ")[0])

# ---------------------------------------------------------------------------
print("\n[7] the form: intake first, and it fails OPEN")

api = read("client", "src", "api.js")
ui = read("client", "src", "components", "ScriptToStoryboard.jsx")

check("the client has the call", "export function intakeScript(text)" in api
      and '"/script-intake"' in api)
check("Create storyboard reads the box before it breaks anything down",
      "await api.intakeScript(text)" in ui
      # The second argument is the approved runtime, and it is optional
      # precisely because a PASTED script has no agreed length — this path
      # still calls it with the text alone. See tests/shot_density_check.py.
      and "async function startBreakdown(text, seconds = null)" in ui)
check("a script goes straight through, untouched",
      'if (kind !== "script")' in ui and "startBreakdown(text);" in ui)
check("⚠ …AND A DEAD CLASSIFIER DOES NOT BLOCK A STORYBOARD. On any error the "
      "form carries on exactly as it did before this step existed",
      "FAIL OPEN" in ui and 'verdict = { kind: "script" };' in ui)
check("an empty box is answered without a request at all",
      'setIntake({ kind: "empty"' in ui)
check("all five kinds are branched on somewhere in the form",
      all(f'"{k}"' in ui for k in ("script", "empty", "vague", "brief", "idea")))
check("⚠ a brief or an idea goes to the APPROVAL GATE, not to the breakdown — "
      "Phase 3 replaced the interim 'Build it anyway' button with a concept "
      "the user reads and approves",
      'if (kind === "brief" || kind === "idea")' in ui
      and 'setStep("concept")' in ui
      # the panel has no buttons left at all — approving is the only way on
      and "sts-intake-actions" not in ui)
check("…so the panel under the box is only 'empty' and 'vague' now, the two "
      "kinds whose answer is typing rather than approving",
      "ONLY 'empty' AND 'vague' LAND HERE NOW" in ui)
check("reading the box does not throw the form off screen",
      "const [reading, setReading] = useState(false)" in ui
      and "Reading what you gave us…" in ui)
check("editing the script clears a verdict about text that no longer exists",
      "if (intake) setIntake(null);" in ui)
check("a new storyboard starts with no verdict",
      ui.count("setIntake(null);") >= 3)
check("the box now says it takes more than a script",
      "Your script, brief or idea" in ui
      and "Paste your script, brief, story or idea" in ui)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)

print("The app reads what it was handed before it draws anything.")
