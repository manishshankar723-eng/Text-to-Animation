"""THE ASSISTANT PLANS. THE USER APPLIES. NOTHING ELSE SPENDS.

Phase 1 took the chat OFF the Script → Storyboard form: on that screen the user
is handing over material, not talking to anything, and "Ask AI" had no referent.
This is where it comes back — beside a finished board, where there is a specific
thing on screen and "add a close-up before shot 5" is a sentence no form field
can take.

    "Ask AI storyboard generate hone ke BAAD introduce karo … Yahan Ask AI ka
     meaning clear hai: existing storyboard ko modify/refine karo."

⚠ AND THE DANGEROUS PART IS NOT THE PROMPT, IT IS THE MONEY. Redrawing a panel
is an image. An assistant that acts on its own could spend forty of them from
one typed sentence, and the user would find out afterwards. So:

    ask  →  a PLAN  →  the list on screen, with the redraw count  →  Apply

What this file pins
-------------------
1. THE ROUTE CANNOT DRAW. It returns intentions. Every edit still goes through
   the endpoints the board's own buttons use, which already carry
   `cap.image-generate`.
2. THE PLAN IS BOUNDED AND VALIDATED. Out-of-range shots, empty edits and blank
   inserts are DROPPED, never repaired — a guessed action is an edit the user
   did not ask for on a picture they will pay to redraw. Eight actions a turn,
   hard.
3. IT SAYS NO. Three verbs and no others; reorder, restyle, dialogue and export
   are refused in words, with the button that does do them.
4. ⚠ SHOT NUMBERS ARE 1-BASED ON THE WIRE, because that is what is printed on
   the board and what the user says. The conversion to indices happens once, at
   the boundary.
5. ⚠ APPLY ORDER IS DESCENDING BY INDEX. Insert and delete renumber everything
   after them, so a plan computed against one snapshot is only safe applied from
   the highest index down.

Run:
    python tests/board_ask_check.py
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


import board_agent as ba  # noqa: E402 — after sys.path is set

mod = read("board_agent.py")

PANELS = [
    {"index": 0, "scene_number": 1, "description": "Wide of the office at dusk.", "camera": "wide"},
    {"index": 1, "scene_number": 1, "description": "Anna closes her laptop.", "camera": "medium"},
    {"index": 2, "scene_number": 2, "description": "The empty corridor.", "camera": ""},
]

# ---------------------------------------------------------------------------
print("\n[1] ⚠ it is an EDITOR, and it says no to everything else")

check("the model is told the film already exists",
      "YOU ARE AN EDITOR, NOT A WRITER" in ba._SYSTEM_INSTRUCTION)
check("three verbs and no more",
      ba.ACTIONS == ("edit", "insert", "delete")
      and list(ba._schema().properties["actions"].items.properties["action"].enum)
      == ["edit", "insert", "delete"])
check("⚠ …and it is told to refuse the rest IN WORDS, naming the real button "
      "for each",
      "SAY NO TO EVERYTHING ELSE" in ba._SYSTEM_INSTRUCTION
      and "'Restyle all' button" in ba._SYSTEM_INSTRUCTION
      and "'Download PDF' button" in ba._SYSTEM_INSTRUCTION
      and "Reorder or move a shot" in ba._SYSTEM_INSTRUCTION
      and "edited on the shot list" in ba._SYSTEM_INSTRUCTION)
check("⚠ …AND IT IS FORBIDDEN TO INVENT ONE. Asked to reorder, the live model "
      "refused correctly and then offered drag-and-drop, which does not exist "
      "anywhere on this board — a confident instruction for a button that "
      "isn't there is worse than a flat no",
      "NEVER INVENT A WAY TO DO SOMETHING" in ba._SYSTEM_INSTRUCTION
      and "no drag-and-drop" in ba._SYSTEM_INSTRUCTION
      and "no 'Export' button" in ba._SYSTEM_INSTRUCTION)
check("⚠ …and it writes an inserted shot's description ITSELF rather than "
      "bouncing the question back — the surrounding shots say who and where",
      "WRITE THE DESCRIPTION YOURSELF" in ba._SYSTEM_INSTRUCTION)
check("an empty action list with a reply is a NORMAL answer, not a failure",
      "often correct, answer" in mod
      and "is a NORMAL answer" in read("server", "schemas.py"))
check("⚠ it is told to touch the fewest shots that answer the request — every "
      "edit is a picture the user pays to redraw",
      "TOUCH THE FEWEST SHOTS" in ba._SYSTEM_INSTRUCTION
      and "costs the user money" in ba._SYSTEM_INSTRUCTION)
check("⚠ …and that a vague request on a big board is a QUESTION, not forty "
      "redraws",
      "do\nNOT rewrite forty shots" in ba._SYSTEM_INSTRUCTION
      or "NOT rewrite forty shots" in ba._SYSTEM_INSTRUCTION)
check("an edit rewrites the description IN FULL, so a 'make it a close-up' "
      "cannot quietly drop the coat and the rain",
      "IN FULL, not a diff" in ba._SYSTEM_INSTRUCTION)
check("the reply comes back in the user's own language, Hinglish included",
      "Hinglish" in ba._SYSTEM_INSTRUCTION
      and "not Devanagari" in ba._SYSTEM_INSTRUCTION)

# ---------------------------------------------------------------------------
print("\n[2] ⚠ the selection is what makes “this one” a sentence")

ctx_panel = ba.board_context(PANELS, selection={"kind": "panel", "shot": 2})
ctx_scene = ba.board_context(PANELS, selection={"kind": "scene", "scene": 2})
ctx_none = ba.board_context(PANELS, selection={"kind": "none"})

check("a selected shot is stated, and named as the default referent",
      "SELECTED: shot 2." in ctx_panel
      and "they\nmean this one" in ctx_panel.replace("  ", " ") or "mean this one" in ctx_panel)
check("a selected scene limits the request to that scene, and only that",
      "every shot in scene 2" in ctx_scene and "and only those" in ctx_scene)
check("⚠ NOTHING SELECTED IS ALSO INFORMATION — it is the case that stops a "
      "vague sentence redrawing the whole board, so it is said out loud",
      "SELECTED: nothing." in ctx_none
      and "ask which shots they mean rather" in ctx_none)
check("a selection pointing off the end of the board is ignored, not obeyed",
      "SELECTED: shot" not in ba.board_context(
          PANELS, selection={"kind": "panel", "shot": 99}))
check("⚠ an unreadable selection falls through to 'nothing', never to "
      "silence — a junk value must not end up SAFER to send than no value",
      "SELECTED: nothing." in ba.board_context(
          PANELS, selection={"kind": "panel", "shot": "x"})
      and "SELECTED: nothing." in ba.board_context(
          PANELS, selection={"kind": "panel", "shot": 99})
      and "SELECTED: nothing." in ba.board_context(PANELS, selection=None))

# ---------------------------------------------------------------------------
print("\n[3] the board the model reads: numbered as printed, and bounded")

check("shots are numbered from 1, exactly as they are on screen",
      "Shot 1 (scene 1) [wide]:" in ctx_none and "Shot 3 (scene 2):" in ctx_none)
check("…and it is told the number that appends at the end",
      "Add at the end with shot 4." in ctx_none)
check("the camera rides along when the shot has one",
      "[wide]" in ctx_none)
check("the style is stated AND fenced off — restyling is not one of the verbs",
      "do not change it here" in ba.board_context(PANELS, style="comic"))
check("a long description is trimmed, not sent whole",
      ba.MAX_DESC_CHARS <= 200
      and "…" in ba.board_context(
          [{"description": "x " * 400}], selection=None))
check("a 120-panel board can't become a novel on every turn",
      ba.MAX_BOARD_CHARS <= 32000 and "board truncated" in mod)
check("the read is deterministic — the same sentence plans the same edits",
      "_sampling_kwargs()" in mod)

# ---------------------------------------------------------------------------
print("\n[4] ⚠ the plan is validated by REJECTION, never by repair")

raw = [
    {"action": "edit", "shot": 2, "description": "Anna closes her laptop, lit from below."},
    {"action": "delete", "shot": 3},
    {"action": "insert", "shot": 4, "description": "The lift doors close on an empty lobby."},
    {"action": "edit", "shot": 99, "description": "off the end"},
    {"action": "delete", "shot": 0},
    {"action": "edit", "shot": 1},
    {"action": "insert", "shot": 2},
    {"action": "explode", "shot": 1},
    {"action": "edit", "shot": "two", "description": "junk"},
    "not a dict",
]
out = ba._coerce_actions(raw, len(PANELS))
kinds = [(a["action"], a["shot"]) for a in out]

check("the good three survive",
      kinds == [("edit", 2), ("delete", 3), ("insert", 4)])
check("⚠ 1-based on the wire becomes 0-based for the endpoints, once, here",
      out[0]["index"] == 1 and out[1]["index"] == 2 and out[2]["index"] == 3)
check("insert may address one past the last shot (append); edit and delete "
      "may not",
      any(a["action"] == "insert" and a["shot"] == 4 for a in out)
      and not any(a["shot"] == 99 for a in out))
check("shot 0 does not exist and is dropped, not clamped to 1",
      not any(a["shot"] == 0 for a in out))
check("⚠ an edit that changes nothing is DROPPED — it would still cost an image",
      not any(a["action"] == "edit" and a["shot"] == 1 for a in out))
check("⚠ …and a blank insert is dropped: that is what the board's own ＋ button "
      "makes, and from a sentence it is a failed instruction",
      not any(a["action"] == "insert" and a["shot"] == 2 for a in out))
check("an invented verb is dropped", not any(a["action"] == "explode" for a in out))
check("junk types are survived, not crashed on", len(out) == 3)
check("an edit that only moves the camera is kept — that is a real change",
      len(ba._coerce_actions(
          [{"action": "edit", "shot": 1, "camera": "low angle"}], 3)) == 1)
check("⚠ EIGHT ACTIONS A TURN, HARD. Past that the user is describing a "
      "different film and should say so on the shot list, where it is free",
      ba.MAX_ACTIONS == 8
      and len(ba._coerce_actions(
          [{"action": "delete", "shot": 1} for _ in range(30)], 40)) == 8)
check("every action says whether it costs an image, for the Apply button",
      all("draws" in a for a in out)
      and out[0]["draws"] is True
      and out[1]["draws"] is False)
check("⚠ a reply we could not parse falls back to WORDS, never to actions — "
      "inventing edits from a broken wrapper spends money on a guess",
      "FALL BACK TO WORDS, NEVER TO ACTIONS" in mod)

# ---------------------------------------------------------------------------
print("\n[5] the route: it plans, and it cannot draw")

main = read("server", "main.py")
schemas = read("server", "schemas.py")
route = main.split('@app.post("/storyboards/{job_id}/ask"')[1].split("@app.")[0]
# ⚠ THE DOCSTRING NAMES EVERY THING THIS ROUTE MUST NOT DO, so the checks below
# would read its own explanation as evidence against it. Strip it, then look at
# the code.
_parts = route.split('"""')
code = _parts[0] + ("".join(_parts[2:]) if len(_parts) > 2 else "")

check("the route exists and is gated on the workflow",
      '@app.post("/storyboards/{job_id}/ask"' in main
      and "require_feature('workflow.script-to-storyboard')" in code)
check("⚠ …and NOT on cap.image-generate, because asking costs no image — that "
      "gate is on the routes that actually apply",
      "cap.image-generate" not in code)
check("⚠ THE ROUTE CHANGES NOTHING. No store write, no regenerate, no insert",
      "get_store().update" not in code
      and "_regenerate_board_panel" not in code
      and "insert" not in code.lower()
      and "delete" not in code.lower())
check("⚠ the panels come from the JOB, not from the request — planning against "
      "what the tab last drew would edit the wrong pictures",
      "_variants_of(job.result" in code
      and "panels" not in schemas.split("class BoardAskRequest")[1].split("class ")[0])
check("a board still generating is refused — its shot numbers are still moving",
      "Wait for the board to finish first" in code)
check("every model call is recorded against the account",
      "usage_counters.record_tokens" in code)
check("the wire carries both numbers: index for the endpoints, shot for the "
      "sentence on screen",
      "class BoardAction" in schemas
      and "index: int = Field(..., ge=0)" in schemas
      and "shot: int = Field(..., ge=1)" in schemas)
check("the selection is 1-based on the wire too",
      "class BoardSelection" in schemas
      and 'pattern="^(panel|scene|none)$"' in schemas)

# ---------------------------------------------------------------------------
print("\n[6] the screen: a plan, a count, and only then a spend")

api = read("client", "src", "api.js")
board = read("client", "src", "components", "StoryboardBoard.jsx")
panel = read("client", "src", "components", "BoardAssistant.jsx")

check("the client has the call, and knows it changes nothing",
      "export function askAboutBoard(" in api
      and "THIS CALL CHANGES NOTHING" in api)
check("⚠ the redraw COUNT is on the Apply button — the only place the cost of "
      "a sentence is stated before it is charged",
      "function applyLabel(actions)" in panel
      and "redraw${draws === 1" in panel)
check("nothing is applied until the button is pressed",
      "onApply(msg.actions)" in panel and "applied: false" in panel)
check("⚠ applying one plan retires the others — insert and delete renumber the "
      "board, so an older plan's shot numbers now point somewhere else",
      "EVERY OTHER PLAN IN THE LOG IS NOW STALE" in panel
      and "stale: true" in panel)
check("a failed turn rolls the user's message back out of the log",
      "THE USER'S MESSAGE IS ROLLED BACK" in panel)
check("it says what it cannot do BEFORE it is asked",
      "I can't reorder" in panel)

check("⚠ THE APPLY ORDER IS DESCENDING BY INDEX, or a plan computed against "
      "one snapshot edits the wrong pictures once the first insert renumbers",
      "DESCENDING INDEX ORDER" in board
      and "b.index - a.index" in board)
check("…and an edit lands before an insert at the same index, or it would "
      "redraw the blank panel the insert just put there",
      "{ edit: 0, delete: 1, insert: 2 }" in board)
check("an inserted shot is DRAWN, not left blank — the user asked for a "
      "picture, in words",
      "api.insertStoryboardPanel(jobId, a.index" in board
      and "api.regenerateStoryboardPanel(jobId, a.index, {" in board)
check("⚠ an edit only sends the fields that changed, or an empty string would "
      "wipe a description nobody asked to lose",
      "if (a.description) overrides.description" in board
      and "if (a.camera) overrides.camera" in board)
check("the selection is dropped after applying — the indices moved",
      'setSelection({ kind: "none" });\n    await reloadBoard();' in board)
check("the shot number and the scene tag are the selectors, not the picture — "
      "clicking the image still opens the lightbox",
      "THE NUMBER IS THE SELECTOR, NOT THE PICTURE" in board
      and "toggleSelectPanel(p.index + 1)" in board
      and "toggleSelectScene(p.scene_number)" in board)
check("selecting the same thing twice clears it",
      'cur.kind === "panel" && cur.shot === shot ? { kind: "none" }' in board)
check("⚠ the assistant is off in sequenceMode and while the board is still "
      "drawing",
      "const canAssist = !sequenceMode && !running && panels.length > 0;" in board)
check("…and can be hidden and brought back, because a board is wide",
      "Hide the assistant" in board and "board-ai-show" in board)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)

print("The assistant proposes, the user disposes, and the money stays behind "
      "the button.")
