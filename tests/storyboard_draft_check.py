"""Contract checks for the storyboard DRAFT lifecycle.

The review step used to live only in the browser: a refresh threw away the
reviewed shot list, cast, world edits and generated references — all of it
downstream of a breakdown that had already cost AI quota. A breakdown now
persists immediately as a DRAFT job, and Generate promotes that same record
into the board.

The breakdown call is STUBBED, so this spends no quota and needs no network
beyond MongoDB. Everything it creates is deleted afterwards.

    python tests/storyboard_draft_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient

from server import config
from server.jobs import get_store
from server.main import app
from server.schemas import JobKind, JobStatus

failures: list[str] = []
made: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


# --- Stub the paid breakdown -------------------------------------------------
SCRIPT = (
    "Lubdhaka the hunter climbed the bilva tree at dusk and waited for the deer.\n"
    "Through the long cold night he plucked the leaves and let them fall.\n"
)
FAKE = {
    "shots": [
        {"scene_number": 1, "shot_number": 1, "description": "A hunter climbs a tree at dusk.",
         "characters": ["Lubdhaka"], "dialogue": [], "assets": ["bilva tree"],
         "location": "forest", "camera": "wide establishing",
         "script_line": "", "script_line_start": None, "script_line_end": None,
         "script_line_match": ""},
        {"scene_number": 1, "shot_number": 2, "description": "Leaves fall through the night.",
         "characters": [], "dialogue": [], "assets": ["bilva tree"],
         "location": "forest", "camera": "close-up",
         "script_line": "", "script_line_start": None, "script_line_end": None,
         "script_line_match": ""},
    ],
    "characters": [{"name": "Lubdhaka", "description": "a lean South Asian hunter"}],
    "assets": [{"name": "bilva tree", "category": "background", "description": "a broad tree"}],
    "world": {"setting": "Ancient India", "culture": "Hindu", "ethnicity": "South Asian",
              "wardrobe": "dhoti", "environment": "forest", "notes": ""},
    "grounding": {"shots_total": 2, "quotes_exact": 2, "quote_rate": 1.0, "warnings": []},
}

import script_breakdown
script_breakdown.break_down_script = lambda *a, **k: dict(FAKE)
# main.py imports it lazily inside the handler, so patching the module is enough.

# --- Stub the WORKER too -----------------------------------------------------
# POST /storyboards enqueues real panel generation. This suite is about the
# draft lifecycle, not drawing, and an unstubbed run burns image quota (it did,
# once — that is why this is here). Record the submission and draw nothing.
from server import worker
submitted: list[str] = []
worker.submit_storyboard_job = lambda job_id, kwargs: submitted.append(job_id)

client = TestClient(app)
email = f"_draft_{uuid.uuid4().hex[:10]}@example.com"
r = client.post("/auth/register", json={"email": email, "password": "draft-pass-12345"})
assert r.status_code == 201, r.text
auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
store = get_store()

print(f"\nstore: {type(store).__name__}\n")

print("[1] no draft yet")
r = client.get("/storyboards/draft", headers=auth)
check("GET -> 200 (never 404s)", r.status_code, 200)
check("job_id is null", r.json()["job_id"], None)

print("\n[2] breakdown auto-creates a DRAFT")
r = client.post("/storyboards/breakdown", headers=auth,
                json={"script": SCRIPT, "style": "sketch", "aspect_ratio": "16:9"})
check("breakdown -> 200", r.status_code, 200)
draft_id = r.json().get("draft_job_id")
check("returned a draft_job_id", bool(draft_id))
made.append(draft_id)
job = store.get(draft_id)
check("job exists in the store", job is not None)
check("status is DRAFT", job.status, JobStatus.DRAFT)
check("kind is STORYBOARD", job.kind, JobKind.STORYBOARD)
check("owned by the caller", job.owner, email)
check("shots persisted", len(job.params["shots"]), 2)
check("script persisted", job.params["script"].startswith("Lubdhaka"))
check("titled from the script", job.character_name, "Lubdhaka the hunter climbed")

print("\n[3] a DRAFT must NOT appear in the library")
r = client.get("/storyboards", headers=auth)
check("library -> 200", r.status_code, 200)
check("library is empty", [b for b in r.json() if b["job_id"] == draft_id], [])

print("\n[4] resume it")
r = client.get("/storyboards/draft", headers=auth)
check("resume finds it", r.json()["job_id"], draft_id)
check("shots come back", len(r.json()["shots"]), 2)
check("world comes back", r.json()["world"]["culture"], "Hindu")
check("cast comes back", r.json()["characters"][0]["name"], "Lubdhaka")

print("\n[5] review edits are saved (this is the work that used to be lost)")
edited = list(FAKE["shots"])
edited[0] = {**edited[0], "description": "EDITED: the hunter climbs at dusk."}
r = client.patch(f"/storyboards/draft/{draft_id}", headers=auth,
                 json={"shots": edited, "title": "Shiva Purana"})
check("PATCH -> 200", r.status_code, 200)
check("edit persisted", r.json()["shots"][0]["description"].startswith("EDITED:"))
check("title persisted", r.json()["title"], "Shiva Purana")
check("count kept in step with shots", store.get(draft_id).params["count"], 2)

print("\n[6] partial PATCH must not wipe other fields")
r = client.patch(f"/storyboards/draft/{draft_id}", headers=auth,
                 json={"character_refs": {"Lubdhaka": "ref-abc-123"}})
check("PATCH -> 200", r.status_code, 200)
check("refs saved", r.json()["character_refs"]["Lubdhaka"], "ref-abc-123")
check("shots NOT wiped by a refs-only save", r.json()["shots"][0]["description"].startswith("EDITED:"))
check("title NOT wiped", r.json()["title"], "Shiva Purana")
check("world NOT wiped", r.json()["world"]["culture"], "Hindu")

print("\n[7] drafts are owner-scoped")
r2 = client.post("/auth/register", json={"email": f"_o_{uuid.uuid4().hex[:8]}@x.dev",
                                         "password": "other-pass-12345"})
other = {"Authorization": f"Bearer {r2.json()['access_token']}"}
other_email = r2.json().get("email") or "other"
check("stranger sees no draft", client.get("/storyboards/draft", headers=other).json()["job_id"], None)
check("stranger cannot PATCH mine",
      client.patch(f"/storyboards/draft/{draft_id}", headers=other, json={"title": "hijack"}).status_code, 404)
check("stranger cannot DELETE mine",
      client.delete(f"/storyboards/draft/{draft_id}", headers=other).status_code, 404)
check("my title survived the attempts", client.get("/storyboards/draft", headers=auth).json()["title"], "Shiva Purana")

print("\n[8] auth required")
check("GET without token -> 401", client.get("/storyboards/draft").status_code, 401)
check("PATCH without token -> 401",
      client.patch(f"/storyboards/draft/{draft_id}", json={"title": "x"}).status_code, 401)

print("\n[9] Generate PROMOTES the draft — no second record")
before = len([j for j in store.list(limit=200, owner=email, kinds=[JobKind.STORYBOARD])])
r = client.post("/storyboards", headers=auth, json={
    "shots": edited, "style": "sketch", "aspect_ratio": "16:9",
    "title": "Shiva Purana", "script": SCRIPT, "draft_job_id": draft_id,
})
check("create -> 202", r.status_code, 202)
check("SAME job id (promoted, not duplicated)", r.json()["job_id"], draft_id)
after = [j for j in store.list(limit=200, owner=email, kinds=[JobKind.STORYBOARD])]
check("no extra job created", len(after), before)
promoted = store.get(draft_id)
check("no longer a draft", promoted.status != JobStatus.DRAFT, True)
check("no draft left to resume", client.get("/storyboards/draft", headers=auth).json()["job_id"], None)
check("now visible in the library",
      any(b["job_id"] == draft_id for b in client.get("/storyboards", headers=auth).json()), True)
check("generation was enqueued for that same job", submitted[-1], draft_id)

print("\n[10] a promoted board is no longer PATCHable as a draft")
check("PATCH -> 409", client.patch(f"/storyboards/draft/{draft_id}", headers=auth,
                                   json={"title": "nope"}).status_code, 409)

print("\n[11] a bad draft_job_id degrades to creating a new job")
r = client.post("/storyboards", headers=auth, json={
    "shots": edited, "style": "sketch", "aspect_ratio": "16:9",
    "title": "Fallback", "script": SCRIPT, "draft_job_id": "does-not-exist",
})
check("still succeeds", r.status_code, 202)
check("made a different job", r.json()["job_id"] != draft_id, True)
made.append(r.json()["job_id"])

print("\n[12] discard a draft")
r = client.post("/storyboards/breakdown", headers=auth, json={"script": SCRIPT})
d2 = r.json()["draft_job_id"]
made.append(d2)
check("DELETE -> 204", client.delete(f"/storyboards/draft/{d2}", headers=auth).status_code, 204)
check("it is gone", store.get(d2), None)

print("\n[13] \u26a0 THE DRAFT IS OFFERED, NOT OPENED")
# The resume effect used to hydrate itself and `setStep("review")` on EVERY
# mount. Right after a refresh, wrong every other time: switching to Plan &
# Script and back unmounts this component, so returning to the workflow re-ran
# it and dropped the user inside a review step they had walked out of — showing
# a board from an earlier session with nothing on screen saying where it came
# from. Reported: "mai abhi aage nhi dawaya tha, mai back aaya tha aur Start
# over button bhi nhi dabaya."
#
# \u26a0 AND "ONLY ON THE FIRST MOUNT" IS NOT THE FIX — StrictMode mounts twice in
# development, so a first-mount-wins flag is spent by a mount nobody saw and the
# built app then behaves differently from `npm run dev`.
_sts = os.path.join(ROOT_DIR, "client", "src", "components", "ScriptToStoryboard.jsx")
with open(_sts, encoding="utf-8") as _fh:
    ui = _fh.read()
_resume_effect = ui.split("const d = await api.getStoryboardDraft();")[1].split("}, []);")[0]

check("the resume effect no longer moves the user anywhere",
      'setStep("review")' not in _resume_effect and "setShots(" not in _resume_effect)
check("\u2026it only records that a draft exists", "setDraftOffer(d)" in _resume_effect)
check("a draft with no shots is not offered — there is no board in it",
      "!(d.shots || []).length" in _resume_effect)
check("the banner carries the shot count, so a stale board is recognisable "
      "before it is opened",
      "sts-draft-offer" in ui and "Unfinished storyboard" in ui
      and "draftOffer.shots || []).length" in ui)
check("\u2026and never sits on top of work already in this session",
      "{draftOffer && !shots.length && (" in ui)
check("Resume is what loads it, and it is the only thing that does",
      "function resumeDraft()" in ui and ui.count('setStep("review");') >= 1
      and 'setStep("review");' in ui.split("function resumeDraft()")[1].split("\n  }")[0])
check("\u26a0 Discard ASKS FIRST — the breakdown behind those shots was paid for "
      "and there is no undo",
      "function discardDraftOffer()" in ui
      and "window.confirm(" in ui.split("function discardDraftOffer()")[1].split("\n  }")[0]
      and "api.discardStoryboardDraft(d.job_id)" in ui)
check("the stylesheet has the banner", "sts-draft-offer {" in open(
    os.path.join(ROOT_DIR, "client", "src", "styles", "storyboard.css"), encoding="utf-8"
).read())

print("\n[14] ⚠ AND IT IS OFFERED ON THE FIRST PAGE TOO")
# "ye resume dikh raha hai magar recent mein kyun nahi dikh raha hai — so user
# ko first page mein hi dikh jaaye." A half-reviewed board is the most expensive
# thing an account can lose, and until now the only way back to one was to walk
# into the workflow first and find the banner there.
#
# ⚠ IT IS STILL NOT IN "RECENT WORK", AND THAT IS NOT AN OVERSIGHT. A draft has
# no panels, so it is not a board; listing it beside finished ones would be a
# lie with a thumbnail on it, and section [3] above pins that a DRAFT must never
# reach the library. It gets its own strip instead.
home = open(os.path.join(ROOT_DIR, "client", "src", "components", "Home.jsx"),
            encoding="utf-8").read()
app = open(os.path.join(ROOT_DIR, "client", "src", "App.jsx"),
           encoding="utf-8").read()

check("Home asks for the draft itself", ".getStoryboardDraft()" in home)
check("…and only offers one that actually has shots in it",
      "(d.shots || []).length) setDraft(d)" in home)
check("the strip says what it is and how big, so a stale board is recognisable "
      "before it is opened",
      "Continue where you left off" in home
      and "(draft.shots || []).length} shot" in home
      and "not yet drawn" in home)
check("⚠ it is NOT inside Recent work — a draft has no panels and is not a board",
      home.index("home-draft") < home.index("Recent work, one group per workflow"))
check("clicking it lands ON the board, not on the workflow's front door with "
      "the same offer repeated",
      "onResumeStoryboard" in app and "setResumeDraft(true)" in app
      and "autoResumeDraft={resumeDraft}" in app)
check("…and the flag is cleared once used, so coming back later opens the form "
      "as normal",
      "onDraftResumed={() => setResumeDraft(false)}" in app
      and "onDraftResumed?.();" in ui)
check("the workflow waits for the draft to ARRIVE rather than firing on mount, "
      "or the click would land on an empty form",
      "if (!autoResumeDraft || !draftOffer || shots.length) return;" in ui)
check("the strip has styling of its own",
      ".home-draft {" in open(
          os.path.join(ROOT_DIR, "client", "src", "styles", "home.css"),
          encoding="utf-8").read())

print("\n[15] cleanup")
removed = sum(1 for jid in made if jid and store.delete(jid))
print(f"  removed {removed} test job(s)")
from server.mongo import get_db
db = get_db()
db[config.USERS_COLLECTION].delete_many({"email": {"$regex": "^_draft_|^_o_"}})
check("no drafts left for the test user",
      len([j for j in store.list(limit=200, owner=email, kinds=[JobKind.STORYBOARD])]), 0)

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All storyboard-draft checks passed.")
