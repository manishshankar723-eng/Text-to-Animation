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
_resume_effect = ui.split("await api.getStoryboardDraft();")[1].split("}, []);")[0]

check("the mount effect no longer moves the user anywhere",
      'setStep("review")' not in _resume_effect and "setShots(" not in _resume_effect)
# ⚠ WHAT THAT CALL IS STILL FOR. It no longer feeds a banner — the offer lives
# in the library ROW now — but the AWAIT is what sets `draftHydrated`, and that
# flag is the only thing stopping the autosave from PATCHing an empty shot list
# over a good draft before the server has answered. Deleting the call would
# quietly reintroduce that.
check("…but it still gates the autosave, or a mount would save [] over a draft",
      "draftHydrated.current = true;" in _resume_effect)
check("…and the autosave refuses to run until it has",
      "if (!draftHydrated.current || !draftJobId) return;" in ui)
check("Resume is what loads it, and it is the only thing that does",
      "function resumeDraft(record)" in ui and ui.count('setStep("review");') >= 1
      and 'setStep("review");' in ui.split("function resumeDraft(record)")[1].split("\n  }")[0])
# ⚠ ONE DOOR, NOT THREE. The draft briefly had a dashboard strip AND a banner on
# this form AND the library row — three places for one record. *"Script to
# Storyboard se bhi hata do, only recent mein hi rakho."* The row is the one
# place, so `resumeDraft` is only ever called with the record the row holds.
check("⚠ the form no longer repeats the offer as a banner of its own",
      "sts-draft-offer" not in ui and "draftOffer" not in ui
      and "Unfinished storyboard" not in ui)
check("…and it resumes only what it is handed, with no second source to fall "
      "back to",
      "const d = record;" in ui.split("function resumeDraft(record)")[1][:200])
check("…and the banner's dead stylesheet went with it — a live rule is how the "
      "next agent puts the second offer back",
      "sts-draft-offer" not in open(
          os.path.join(ROOT_DIR, "client", "src", "styles", "storyboard.css"),
          encoding="utf-8").read())

print("\n[14] ⚠ THE OFFER LIVES IN ONE PLACE: THE STORYBOARDS PAGE")
# It used to be offered on the DASHBOARD as well, which is what the previous
# version of this section pinned. The user asked for the opposite, pointing at
# the page they had built for exactly this: *"maine recent kyun banaya hai jab
# yahan pe mera resume dikh hi nahi raha hai … home page se bhi hatao, bas ek
# jagah."* So the strip moved to "Your Storyboards", above Recent Storyboards,
# and the dashboard copy — along with the whole `autoResumeDraft` flag that
# carried the click across two components — is gone rather than left dangling.
#
# ⚠ IT IS STILL NOT A ROW IN THE LIST, AND THAT IS NOT AN OVERSIGHT. A draft has
# no panels, so it is not a board; listing it beside finished ones would be a
# lie with a thumbnail on it, and section [3] above pins that a DRAFT must never
# reach `GET /storyboards`. It sits ABOVE the list, in its own strip.
home = open(os.path.join(ROOT_DIR, "client", "src", "components", "Home.jsx"),
            encoding="utf-8").read()
app = open(os.path.join(ROOT_DIR, "client", "src", "App.jsx"),
           encoding="utf-8").read()
lib = open(os.path.join(ROOT_DIR, "client", "src", "components",
                        "StoryboardLibrary.jsx"), encoding="utf-8").read()
sb_css = open(os.path.join(ROOT_DIR, "client", "src", "styles",
                           "storyboard.css"), encoding="utf-8").read()
sb_lib_css = open(os.path.join(ROOT_DIR, "client", "src", "styles",
                               "storyboard-library.css"), encoding="utf-8").read()

check("the storyboards page asks for the drafts itself",
      ".listStoryboardDrafts()" in lib)
check("…and only offers ones that actually have shots in them",
      "(d.shots || []).length" in lib and "setDrafts(" in lib)
check("the row says what it is and how big, so a stale board is recognisable "
      "before it is picked up",
      "renderDraftRow" in lib
      and "{shotCount} shot" in lib
      and "Not drawn yet" in lib)
# ⚠ IN THE LIST, LEADING IT — asked for after a first attempt put it in its own
# strip above: *"mai yeh nahi lagane bola tha … dance video ke upar hi aa jaye
# jaise sab dikh rahe hai, so user samajh jayega ki mera pehla work resume wala
# bhi hai aur completed work bhi."* Prepended CLIENT-SIDE: the server still
# excludes drafts from GET /storyboards, which section [3] pins.
check("⚠ they are the FIRST rows of the list, above the finished boards",
      lib.index("{draftsShown.map(renderDraftRow)}")
      < lib.index("{shown.map(renderBoard)}"))
check("⚠ …and they count, or a user whose only projects are unfinished would "
      "be told they have none — `total: 0` draws the empty state, not rows",
      # ⚠ MATCHED AS TERMS, NOT AS ONE LINE. This pinned the whole expression
      # verbatim and broke the day an unapproved CONCEPT became a row in the
      # same list and added a third term to the sum — a rename-shaped failure
      # on a screen that was working perfectly.
      "boards.length +" in lib
      and "(onResume ? drafts.length : 0)" in lib
      and "total={listTotal}" in lib and "shown={shownTotal}" in lib)
check("⚠ …and they NEVER pretend to have a picture — there are no panels yet, "
      "so the thumbnail is a note glyph, not a cover",
      "lib-draft-glyph" in lib and "cover={<span" in lib
      and "lib-draft-glyph" in sb_lib_css)
# ⚠ EACH ROW OWNS ITS OWN CONFIRM STRIP. With several drafts on screen a
# single shared row id would open every one of their confirm strips at once,
# and "Discard" would then be a question about which project exactly?
check("⚠ discarding ASKS FIRST, per row — the breakdown behind those shots was "
      "paid for and there is no undo",
      "const draftRowId = (jobId) => `draft:${jobId}`;" in lib
      and "setConfirmId(uid)" in lib
      and "api.discardStoryboardDraft(draft.job_id)" in lib
      and "cannot be undone" in lib)
check("⚠ …and a draft's row id can never collide with a board's job_id, which "
      "a bare id risks the moment a draft is promoted",
      "`draft:${jobId}`" in lib and "DRAFT_ROW_ID" not in lib)
check("⚠ …and they are only offered where they can actually be resumed — the "
      "animatic library renders this same component over COPIES",
      "const canResume = Boolean(onResume);" in lib
      and "if (!canResume) return;" in lib
      and "draftsShown =" in lib)
# ⚠ The fetch keys off a BOOLEAN, not the callback. `onResume` is an inline
# arrow in the caller, so its identity changes on every parent render —
# depending on it would re-request the draft each time the workflow re-rendered.
check("…and the draft is fetched once, not on every render of the workflow",
      "}, [canResume]);" in lib)
check("Resume hands the workflow the very record the row is showing",
      "onResume(draft)" in lib
      and "onResume={(draft) => resumeDraft(draft)}" in ui)
check("⚠ the dashboard no longer carries a second copy of the offer",
      "getStoryboardDraft" not in home
      and "Continue where you left off" not in home
      and "home-draft" not in home)
check("⚠ …and the cross-component flag that drove it went with it, rather than "
      "being left dangling",
      "autoResumeDraft" not in app and "autoResumeDraft" not in ui
      and "setResumeDraft" not in app)
check("⚠ the strip that briefly stood above the list is gone, stylesheet "
      "included — a live rule is how the next agent brings it back",
      "draft-strip" not in lib and "draft-strip" not in sb_css)

# The unfinished row has no panels, so its thumbnail is a note glyph rather
# than a cover - asked for by name ("text note jaisa icon dikha dena").
check("⚠ the unfinished row never pretends to have a picture — a note glyph, "
      "not a cover",
      "lib-draft-glyph" in lib and "cover={<span" in lib
      and "lib-draft-glyph" in sb_lib_css)

print("\n[14c] ⚠ A SESSION WITH SHOTS BUT NO DRAFT SAVES NOTHING")
# THE BUG, in the user's own test: they drew a character reference, went to
# Home, came back and pressed Resume — and an UNRELATED project opened. The
# server was checked: no record of their board existed anywhere, in any state.
# Only a BREAKDOWN minted a draft (it had just spent money, so it wrote the
# result down). DUPLICATE deliberately skips the breakdown — that is its whole
# point, reuse the shots rather than pay again — so it reached the review step
# with no draft, and the autosave is keyed on having one. Every edit, and every
# reference image PAID FOR on the cast and props steps, lived only in the
# browser. Resume could only offer what the server actually had.
#
# ⚠ POST /storyboards/draft CALLS NO MODEL AND SPENDS NO QUOTA. The shots
# already exist; this only gives them somewhere to live. Do not let a breakdown
# creep into it.
r = client.post("/storyboards/draft", headers=auth, json={
    "shots": FAKE["shots"], "title": "Rescued Session", "script": SCRIPT,
    "style": "sketch", "aspect_ratio": "9:16", "genre": "mythology",
    "characters": FAKE["characters"], "assets": FAKE["assets"],
    "world": FAKE["world"],
})
check("POST -> 201", r.status_code, 201)
rescued = r.json()["job_id"]
made.append(rescued)
check("it is a real DRAFT job", store.get(rescued).status, JobStatus.DRAFT)
check("owned by the caller", store.get(rescued).owner, email)
check("the shots are stored", len(store.get(rescued).params["shots"]), 2)
check("…and so is everything the review step shows",
      (store.get(rescued).params["world"]["culture"],
       store.get(rescued).params["aspect_ratio"],
       len(store.get(rescued).params["assets"])),
      ("Hindu", "9:16", 1))
check("titled from what was passed, not from the script",
      store.get(rescued).character_name, "Rescued Session")
# ⚠ IT MUST BEHAVE LIKE ANY OTHER DRAFT FROM HERE ON — saveable, and out of
# the library until it is generated. A half-saved rescue is not a rescue.
r = client.patch(f"/storyboards/draft/{rescued}", headers=auth,
                 json={"character_refs": {"ananya": "ref-xyz"},
                       "character_takes": {"ananya": [{"reference_id": "ref-xyz"}]}})
check("the rescued draft autosaves like any other", r.status_code, 200)
check("⚠ …including the references that cost money",
      r.json()["character_refs"]["ananya"], "ref-xyz")
check("…and their takes", len(r.json()["character_takes"]["ananya"]), 1)
check("⚠ it stays OUT of the library, like every draft",
      [b for b in client.get("/storyboards", headers=auth).json()
       if b["job_id"] == rescued], [])
check("a shot list is required — an empty session is nothing to save",
      client.post("/storyboards/draft", headers=auth, json={"shots": []}).status_code,
      422)
check("auth required",
      client.post("/storyboards/draft", json={"shots": FAKE["shots"]}).status_code, 401)
# The client half: the review step notices it has no draft and asks for one.
check("the workflow creates one when it is reviewing unsaved shots",
      "api.createStoryboardDraft({" in ui
      and 'if (step !== "review" || draftJobId || jobId) return;' in ui)
check("⚠ …but never for a board that is already saved as a board, which would "
      "be a second record of one storyboard",
      "|| jobId) return;" in ui)
check("⚠ …and never twice — StrictMode mounts twice in development, and a ref "
      "is the only guard that survives that",
      "const creatingDraft = useRef(false);" in ui
      and "creatingDraft.current = true;" in ui)

print("\n[14d] ⚠ EVERY UNFINISHED BOARD IS REACHABLE, NOT JUST THE NEWEST")
# `GET /storyboards/draft` answers "the most recent one", and it was the only
# way in. This account was found holding TWO unfinished boards with the older
# one unreachable by any means: the library drew one row and always drew the
# same one. The user hit it from the other side — Resume opened a project they
# were not working on, because it was the newest and theirs was not saved.
#
# ⚠ THE SINGULAR ENDPOINT STAYS. It is a different question, still asked by
# the workflow on mount, and sections [1], [4], [7] and [9] pin its meaning.
r = client.post("/storyboards/draft", headers=auth, json={
    "shots": FAKE["shots"], "title": "Older Unfinished", "script": SCRIPT,
})
older = r.json()["job_id"]; made.append(older)
r = client.post("/storyboards/draft", headers=auth, json={
    "shots": FAKE["shots"], "title": "Newer Unfinished", "script": SCRIPT,
})
newer = r.json()["job_id"]; made.append(newer)

r = client.get("/storyboards/drafts", headers=auth)
check("GET /storyboards/drafts -> 200", r.status_code, 200)
ids = [d["job_id"] for d in r.json()]
check("⚠ BOTH unfinished boards come back, not only the newest",
      sorted([i for i in ids if i in (older, newer)]), sorted([older, newer]))
check("newest first, so the list reads like the library it sits in",
      ids.index(newer) < ids.index(older))
check("each carries what its row has to draw",
      all(("shots" in d and "title" in d and "updated_at" in d) for d in r.json()))
# ⚠ The singular endpoint still means what it always meant.
check("…while GET /storyboards/draft still answers with the NEWEST one",
      client.get("/storyboards/draft", headers=auth).json()["job_id"], newer)
check("⚠ drafts are STILL absent from the library — a draft has no panels",
      [b for b in client.get("/storyboards", headers=auth).json()
       if b["job_id"] in (older, newer)], [])
check("owner-scoped, like every other draft route",
      client.get("/storyboards/drafts", headers=other).json(), [])
check("auth required", client.get("/storyboards/drafts").status_code, 401)
# Discarding one must leave the other alone — with several rows on screen that
# is the difference between tidying up and losing a project.
check("discarding one leaves the other standing",
      client.delete(f"/storyboards/draft/{older}", headers=auth).status_code, 204)
check("…and it is the right one that survived",
      [d["job_id"] for d in client.get("/storyboards/drafts", headers=auth).json()
       if d["job_id"] in (older, newer)], [newer])

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
