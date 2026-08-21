"""✨ ANIMATE OPENS ON THE BOARD'S OWN WORDS — description AND spoken lines.

The report this was written for:

    "when i generte animate with Veo so user get this pop up So i want this time
     user see prompt too so user control prompt if user want add some prompt and
     dialouge like generted in last Storyboard panel"

The dialog used to prefill its prompt box with the FRAME'S LABEL — "Shot 1",
which is a name and not a prompt — while the panel that clip was drawn from
already carried a sentence describing the shot and a list of who says what in
it. Both were one owner-checked read away, and the user was retyping them.

⚠ NOTHING NEW WAS ROUTED. `GET /animatics/{id}/frames/{frame_id}/panel` already
answered "what does the board say about this clip" for the redraw pane, and it
already returned `description` / `camera` / `location`. All this feature added
server-side is `dialogue` on the same response, so what is asserted here is:

  1. a board-backed frame reports the panel's description AND its spoken lines
  2. an EMPTY line is dropped — an empty quotation in a Veo prompt is worse
     than no quotation, and dropping it in the UI would mean two places knowing
     the rule (`_dialogue_lines` already drops them for the voiceover)
  3. a silent shot reports `[]`, so the dialog draws no dialogue block at all
  4. a frame that is NOT a board panel still answers, with a reason and no
     wording — the dialog falls back to the label and shows no board block
  5. another account cannot read a panel through a crafted board id

⚠ VEO IS NEVER CALLED and nothing is rendered: every call here is a free GET.

    python tests/animate_prompt_draft_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

from server.jobs import get_store
from server.main import app
from server.schemas import JobKind, JobStatus

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)
store = get_store()


def register() -> dict:
    email = f"_draft_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "draft-pass-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email


auth, email = register()
other_auth, _ = register()

print(f"\nstore: {type(store).__name__}\n")

# --- A board with three panels: one spoken, one silent, one with a blank line -
# Written straight into the store because the breakdown that normally produces
# these is a paid AI call. The SHAPE is what matters and it is the shape
# `storyboard_pipeline` writes (see its `"dialogue": _dialogue_of(shot)`).
PANELS = [
    {
        "index": 0,
        "description": "Ravi lowers the lamp and looks towards the door.",
        "camera": "Medium close-up",
        "location": "Bedroom, night",
        "dialogue": [
            {"character": "RAVI", "line": "Who is there?"},
            # ⚠ NO WORDS. A speaker with nothing to say is not dialogue, and it
            # must never reach a prompt as an empty pair of quotes.
            {"character": "MAYA", "line": "   "},
            {"character": "", "line": "Footsteps on the landing."},
        ],
    },
    {
        "index": 1,
        "description": "The corridor, empty.",
        "camera": "Wide",
        "location": "Landing",
        "dialogue": [],
    },
    {
        "index": 2,
        "description": "",
        "camera": "",
        "location": "",
        "dialogue": [{"character": "MAYA", "line": "Go back to sleep."}],
    },
]
board = store.create(
    character_name="The Landing",
    kind=JobKind.STORYBOARD,
    owner=email,
    params={"shots": []},
)
store.update(board.job_id, status=JobStatus.SUCCEEDED, result={"panels": PANELS})

# --- An animatic whose frames point at those panels, plus one upload ---------
frames = [
    {"id": "fr0", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 0},
     "duration_ms": 2000, "label": "Shot 1"},
    {"id": "fr1", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 1},
     "duration_ms": 2000, "label": "Shot 2"},
    {"id": "fr2", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 2},
     "duration_ms": 2000, "label": "Shot 3"},
    {"id": "fr3", "src": {"kind": "upload", "upload_id": uuid.uuid4().hex[:12]},
     "duration_ms": 2000, "label": "Shot 4"},
]
r = client.post("/animatics", headers=auth, json={"title": "Prompt draft", "frames": frames})
assert r.status_code == 201, r.text
job_id = r.json()["job_id"]


def panel_of(frame_id, headers=auth):
    return client.get(f"/animatics/{job_id}/frames/{frame_id}/panel", headers=headers)


# ---------------------------------------------------------------------------
print("[1] a board frame opens on the PANEL'S wording, not the frame's label")
r = panel_of("fr0")
check("-> 200", r.status_code, 200)
info = r.json()
check("it is linked to the board", info["storyboard_id"], board.job_id)
check("the description is the draft prompt",
      info["description"], "Ravi lowers the lamp and looks towards the door.")
check("and it is NOT the frame label", info["description"] == "Shot 1", False)
check("the board is named, so the dialog can say where the words came from",
      info["title"], "The Landing")

# ---------------------------------------------------------------------------
print("\n[2] the shot's SPOKEN LINES come with it")
lines = info["dialogue"]
check("two lines survive, not three", len(lines), 2)
check("the attributed line keeps its speaker", lines[0], {"character": "RAVI", "line": "Who is there?"})
check("a line with no words is DROPPED",
      any(not d["line"].strip() for d in lines), False)
check("an unattributed line still carries its words",
      lines[1]["line"], "Footsteps on the landing.")
check("...with an empty speaker, for the UI to name", lines[1]["character"], "")

# ---------------------------------------------------------------------------
print("\n[3] a SILENT shot reports no dialogue at all")
r = panel_of("fr1")
check("-> 200", r.status_code, 200)
check("dialogue is empty", r.json()["dialogue"], [])
check("but the description is still there", r.json()["description"], "The corridor, empty.")

# ---------------------------------------------------------------------------
print("\n[4] a shot with lines but NO description still hands the lines over")
r = panel_of("fr2")
check("-> 200", r.status_code, 200)
check("no draft prompt", r.json()["description"], "")
check("the dialogue is still offered", len(r.json()["dialogue"]), 1)

# ---------------------------------------------------------------------------
print("\n[5] a frame that is not a board panel answers with a REASON, not a 404")
r = panel_of("fr3")
check("-> 200", r.status_code, 200)
info = r.json()
check("no board", info["storyboard_id"], None)
check("no wording to draft from", info["description"], "")
check("no dialogue", info["dialogue"], [])
check("and it says why", "uploaded" in info["reason"])

# ---------------------------------------------------------------------------
print("\n[6] another account cannot read this board's words")
r = panel_of("fr0", headers=other_auth)
check("someone else's animatic -> 404", r.status_code, 404)

# A crafted frame pointing at a board that account does not own must not read it
# either — the owner check is on the BOARD, separately from the animatic.
stolen = client.post("/animatics", headers=other_auth, json={
    "title": "Crafted",
    "frames": [{"id": "fx", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 0},
                "duration_ms": 2000, "label": "Shot 1"}],
})
assert stolen.status_code == 201, stolen.text
stolen_id = stolen.json()["job_id"]
r = client.get(f"/animatics/{stolen_id}/frames/fx/panel", headers=other_auth)
check("a crafted board id -> 200 with nothing", r.status_code, 200)
check("no description leaks", r.json()["description"], "")
check("no dialogue leaks", r.json()["dialogue"], [])
check("and it says the board is unavailable", "no longer available" in r.json()["reason"])

client.delete(f"/animatics/{stolen_id}", headers=other_auth)
client.delete(f"/animatics/{job_id}", headers=auth)
store.delete(board.job_id)

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("✨ Animate opens on the board's own description and its spoken lines.")
