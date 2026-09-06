"""The ✨ AI Editor's saved chats — the store and its five routes.

⚠ **THIS ONE TOUCHES NOTHING REAL.** Every store is pointed at a fresh temporary
directory BEFORE `server.config` is imported — local JSON for accounts and chats,
an in-memory job store — so it needs no MongoDB, no network, no AI quota and no
model. Same bootstrap as `admin_check.py`.

What it guards, in rough order of how much it would hurt to get wrong:

  SOMEBODY ELSE'S CONVERSATION. Chats are keyed by (owner, project) in the STORE
  as well as checked in the ROUTE. Two locks, and the store's is the one that
  still holds if a route is added later without the check. A stranger asking for
  a chat by its real id must get nothing.

  RENAMING MUST NOT DELETE THE TRANSCRIPT. `None` means "leave it alone" and
  `[]` means "it is empty", all the way from the request body to the document.
  Collapsing those two with `or` is the single most expensive bug this feature
  could have, and it is invisible until somebody renames a long chat.

  `created_at` IS WRITTEN ONCE. An upsert that re-stamps it on every autosave
  makes every chat look new and shuffles a list that is ordered by time.

  THE CEILING IS A CEILING, NOT A BIN. Going over it sweeps chats nobody ever
  typed in, and then refuses out loud. It never deletes a conversation to make
  room.

  A DELETED PROJECT TAKES ITS CHATS. Otherwise they sit there for ever with
  nothing pointing at them.

  AND THE LIST CARRIES NO TRANSCRIPTS. Forty chats of sixty turns is megabytes
  to draw a dozen titles.

  §11: THE LIMITS ARE THE ADMIN PANEL'S AND ITS NUMBER IS THE NUMBER. How many
  chats, how many turns are kept and how big one may get were environment
  variables for half a day — *"isme admin panel mai v daalo, mai limit set kar
  dunga, mai jitna daalun wahi hona chahiye"*. Every check there moves the setting
  and then proves the ROUTE changed with it; reading the store back would only
  prove the store can hold a number. **0 chats means NO limit**, not "fall back to
  40" — the same trap `opacity: 0` fell into on this router. And it reads the
  BROWSER's own cap out of `chat_sessions.js`, because a browser that trims lower
  than the admin's maximum makes the operator's number silently meaningless.

    python tests/chat_sessions_check.py
"""

import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.mkdtemp(prefix="chat_sessions_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_LOCAL_CHAT_SESSIONS_PATH"] = os.path.join(_TMP, "chats.json")
os.environ["API_LOCAL_USAGE_PATH"] = os.path.join(_TMP, "usage.json")
os.environ["API_LOCAL_CHAT_SETTINGS_PATH"] = os.path.join(_TMP, "chat_settings.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "chat-sessions-check-not-a-real-secret"

from fastapi.testclient import TestClient  # noqa: E402

from server import chat_sessions, chat_settings, users as users_mod  # noqa: E402
from server.jobs import get_store  # noqa: E402
from server.main import app  # noqa: E402
from server.schemas import JobKind  # noqa: E402

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)

# ⚠ THE LIMITS ARE THE ADMIN PANEL'S NOW, NOT ENVIRONMENT VARIABLES. They were
# `API_MAX_CHAT_*` for half a day, and this suite set them the same way — which
# would now be testing three variables nothing reads. Asked for outright: *"isme
# admin panel mai v daalo, mai limit set kar dunga, mai jitna daalun wahi hona
# chahiye"*. Small numbers on purpose: the ceiling has to be reachable without
# writing forty conversations.
SIZE_CAP = chat_settings.LIMITS["max_chat_chars"]["min"]  # the smallest legal one
chat_settings.save_settings(
    {"max_chats_per_project": 3, "max_chat_chars": SIZE_CAP, "chat_history_keep": 10},
    actor="test",
)


def account(email):
    r = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def project(owner, name="Film"):
    """An animatic straight into the store — this suite is about the chats, and
    going through `POST /animatics` would drag a feature flag and a quota in."""
    return get_store().create(name, kind=JobKind.ANIMATIC, owner=owner).job_id


def turns(*texts):
    """A conversation in the browser's own turn shape — the server never looks
    inside one, which is exactly why the test sends realistic ones."""
    rows = []
    for i, t in enumerate(texts):
        rows.append({"id": f"u{i}", "role": "user", "kind": "text", "text": t})
        rows.append({"id": f"a{i}", "role": "agent", "kind": "answer", "text": "ok"})
    return rows


ME = "me@example.com"
YOU = "you@example.com"
mine = account(ME)
yours = account(YOU)
film = project(ME)
other_film = project(ME, "Second film")


# ===========================================================================
print("\n1 · An empty project is a valid state, not a 404\n")
# ===========================================================================
r = client.get(f"/editor-chat/{film}/sessions", headers=mine)
check("listing a project with no chats answers 200", r.status_code, 200)
check("…with no chats", r.json()["sessions"], [])
check("…and it says what the ceiling is", r.json()["limit"], 3)


# ===========================================================================
print("\n2 · A chat is created, saved, read back and renamed\n")
# ===========================================================================
r = client.post(
    f"/editor-chat/{film}/sessions",
    headers=mine,
    json={"title": "Sound pass", "turns": turns("add sound effects")},
)
check("creating a chat answers 200", r.status_code, 200)
made = r.json()
sid = made["session_id"]
check("…and the id comes from the SERVER", bool(sid) and len(sid) >= 8)
check("…carrying the turns it was given", len(made["turns"]), 2)
check("…and a created_at", bool(made["created_at"]))

r = client.get(f"/editor-chat/{film}/sessions/{sid}", headers=mine)
check("reading it back answers 200", r.status_code, 200)
check("…with the same transcript", r.json()["turns"], made["turns"])

work_turns = [
    {"id": "u-work", "role": "user", "kind": "text", "text": "add a dissolve"},
    {
        "id": "a-work",
        "role": "agent",
        "kind": "plan",
        "text": "Saved plan",
        "plan": {"version": 1, "steps": [{"id": "s1", "verb": "note", "args": {}}]},
        "plan_signature": "12:abc",
        "apply_state": "running",
        "log": [{"id": "s1", "state": "done"}],
        "apply_refs": {"title": "txt-1"},
    },
]
r = client.put(
    f"/editor-chat/{film}/sessions/{sid}",
    headers=mine,
    json={"turns": work_turns},
)
check("the saved AI work answers 200", r.status_code, 200)
check("the plan survives the write", r.json()["turns"], work_turns)
check("the apply checkpoint survives too", r.json()["turns"][1]["apply_refs"], {"title": "txt-1"})

r = client.put(
    f"/editor-chat/{film}/sessions/{sid}",
    headers=mine,
    json={"turns": turns("add sound effects", "and a music bed")},
)
check("the autosave answers 200", r.status_code, 200)
check("…and the conversation grew", len(r.json()["turns"]), 4)

# ⚠ THE CHECK THIS FILE EXISTS FOR.
r = client.put(f"/editor-chat/{film}/sessions/{sid}", headers=mine, json={"title": "Sound"})
check("renaming answers 200", r.status_code, 200)
check("…the name changed", r.json()["title"], "Sound")
check("⚠ …AND THE TRANSCRIPT SURVIVED THE RENAME", len(r.json()["turns"]), 4)

# The mirror image: an autosave carrying no title must not blank the name.
r = client.put(
    f"/editor-chat/{film}/sessions/{sid}",
    headers=mine,
    json={"turns": turns("a", "b", "c")},
)
check("⚠ …and an autosave with no title keeps the name", r.json()["title"], "Sound")
check("…while the turns are replaced", len(r.json()["turns"]), 6)

check(
    "⚠ created_at is written ONCE, not re-stamped on every save",
    r.json()["created_at"],
    made["created_at"],
)
check("…and updated_at moved", r.json()["updated_at"] >= made["updated_at"])


# ===========================================================================
print("\n3 · The list is a list — titles, counts, and NO transcripts\n")
# ===========================================================================
r = client.get(f"/editor-chat/{film}/sessions", headers=mine)
rows = r.json()["sessions"]
check("one chat is listed", len(rows), 1)
check("…named", rows[0]["title"], "Sound")
check(
    "…counting what the PERSON said, not the agent's replies",
    rows[0]["turn_count"],
    3,
)
check("⚠ …and carrying no transcript at all", "turns" not in rows[0])


# ===========================================================================
print("\n4 · One project's chats are not another's\n")
# ===========================================================================
client.post(f"/editor-chat/{other_film}/sessions", headers=mine, json={"turns": turns("hi")})
check(
    "the second film has its own one chat",
    len(client.get(f"/editor-chat/{other_film}/sessions", headers=mine).json()["sessions"]),
    1,
)
check(
    "…and the first still has only its own",
    len(client.get(f"/editor-chat/{film}/sessions", headers=mine).json()["sessions"]),
    1,
)


# ===========================================================================
print("\n5 · ⚠ SOMEBODY ELSE'S CONVERSATION IS NOT READABLE\n")
# ===========================================================================
# Not "hard to find" — not readable, with the real project id and the real chat
# id in hand, which is the only threat model worth testing.
check("a stranger cannot list it", client.get(f"/editor-chat/{film}/sessions", headers=yours).status_code, 404)
check("…cannot read one", client.get(f"/editor-chat/{film}/sessions/{sid}", headers=yours).status_code, 404)
check("…cannot write one", client.put(f"/editor-chat/{film}/sessions/{sid}", headers=yours, json={"title": "mine now"}).status_code, 404)
check("…cannot delete one", client.delete(f"/editor-chat/{film}/sessions/{sid}", headers=yours).status_code, 404)
check("…and cannot start one", client.post(f"/editor-chat/{film}/sessions", headers=yours, json={}).status_code, 404)
check("…the title is untouched", client.get(f"/editor-chat/{film}/sessions/{sid}", headers=mine).json()["title"], "Sound")

# ⚠ THE STORE'S OWN LOCK, TESTED WITHOUT A ROUTE. This is the one that still
# holds if a route is added later that forgets to check.
check(
    "the STORE refuses another owner too, not just the route",
    chat_sessions.get_session(YOU, film, sid),
    None,
)
check("…and lists nothing for them", chat_sessions.list_sessions(YOU, film), [])


# ===========================================================================
print("\n6 · The ceiling refuses out loud — it does not bin a conversation\n")
# ===========================================================================
# One real chat exists on `film`. Fill to the ceiling of three.
a = client.post(f"/editor-chat/{film}/sessions", headers=mine, json={"turns": turns("second")}).json()
b = client.post(f"/editor-chat/{film}/sessions", headers=mine, json={"turns": turns("third")}).json()
check("three chats now", chat_sessions.count_sessions(ME, film), 3)

r = client.post(f"/editor-chat/{film}/sessions", headers=mine, json={"turns": turns("fourth")})
check("a fourth is refused", r.status_code, 409)
check("…and says how to make room", "Delete one" in r.json()["detail"])
check("⚠ …AND NOTHING WAS DELETED TO MAKE ROOM", chat_sessions.count_sessions(ME, film), 3)
check("…the oldest conversation is still there", client.get(f"/editor-chat/{film}/sessions/{sid}", headers=mine).status_code, 200)

# An EMPTY chat is different: nobody ever typed in it, so it is swept.
client.delete(f"/editor-chat/{film}/sessions/{b['session_id']}", headers=mine)
empty = client.post(f"/editor-chat/{film}/sessions", headers=mine, json={}).json()
check("an empty chat fills the third slot", chat_sessions.count_sessions(ME, film), 3)
r = client.post(f"/editor-chat/{film}/sessions", headers=mine, json={"turns": turns("fourth")})
check("…and a new one sweeps it rather than refusing", r.status_code, 200)
check("…still three", chat_sessions.count_sessions(ME, film), 3)
check(
    "…and the one swept was the EMPTY one",
    client.get(f"/editor-chat/{film}/sessions/{empty['session_id']}", headers=mine).status_code,
    404,
)
check(
    "…while the conversations are untouched",
    client.get(f"/editor-chat/{film}/sessions/{sid}", headers=mine).status_code,
    200,
)


# ===========================================================================
print("\n7 · A conversation too big to store is refused, not truncated\n")
# ===========================================================================
# ⚠ MEASURED AS IT WILL BE STORED. Deliberately past the operator's number by a
# wide margin, so this cannot start passing because a field name got shorter.
huge = [
    {"id": f"x{i}", "role": "user", "kind": "text", "text": "x" * 500}
    for i in range(SIZE_CAP // 200)
]
r = client.put(f"/editor-chat/{film}/sessions/{sid}", headers=mine, json={"turns": huge})
check("an oversized autosave answers 413", r.status_code, 413)
check("…and says what the limit is", f"{SIZE_CAP:,}" in r.json()["detail"])
check(
    "⚠ …and the stored chat is EXACTLY as it was",
    len(client.get(f"/editor-chat/{film}/sessions/{sid}", headers=mine).json()["turns"]),
    6,
)


# ===========================================================================
print("\n8 · Deleting\n")
# ===========================================================================
r = client.delete(f"/editor-chat/{film}/sessions/{a['session_id']}", headers=mine)
check("deleting answers 204", r.status_code, 204)
check(
    "…it is gone",
    client.get(f"/editor-chat/{film}/sessions/{a['session_id']}", headers=mine).status_code,
    404,
)
# ⚠ A DELETE THAT 404s ON THE SECOND CLICK IS A DELETE THAT LOOKS BROKEN.
check(
    "…and deleting it again is still 204",
    client.delete(f"/editor-chat/{film}/sessions/{a['session_id']}", headers=mine).status_code,
    204,
)


# ===========================================================================
print("\n9 · ⚠ A DELETED PROJECT TAKES ITS CHATS WITH IT\n")
# ===========================================================================
before = chat_sessions.count_sessions(ME, film)
check("the film still has chats", before > 0)
r = client.delete(f"/animatics/{film}", headers=mine)
check("deleting the project answers 204", r.status_code, 204)
check("⚠ …and every chat about it went with it", chat_sessions.count_sessions(ME, film), 0)
check(
    "…while the OTHER film's chat is untouched",
    chat_sessions.count_sessions(ME, other_film),
    1,
)


# ===========================================================================
print("\n10 · The routes are not behind the feature switch or the quota\n")
# ===========================================================================
# ⚠ TURNING THE CHAT OFF MUST NOT MAKE SOMEBODY'S SAVED CONVERSATIONS
# UNREADABLE. A feature you lose access to should stop producing new work, not
# eat the old — and reading back what you already paid for is not a second
# charge. Proved by reading the route's own dependencies rather than by trying
# to flip a flag, which is what the panel would be doing anyway.
from server import editor_chat as ec_mod  # noqa: E402

gated = set()
for route in ec_mod.router.routes:
    deps = repr(getattr(route, "dependant", "")) + repr(getattr(route, "dependencies", ""))
    if "require_feature" in deps or "require_quota" in deps:
        gated.add(route.path)
check("the paid turn IS gated", "/editor-chat/{job_id}/turn" in gated)
check("…and the filing cabinet is not", "/editor-chat/{job_id}/sessions" not in gated)
check("…nor is reading one chat", "/editor-chat/{job_id}/sessions/{session_id}" not in gated)


# ===========================================================================
print("\n11 · ⚠ THE LIMITS ARE THE ADMIN PANEL'S, AND ITS NUMBER IS THE NUMBER\n")
# ===========================================================================
# Asked for outright: *"isme admin panel mai v daalo, mai limit set kar dunga —
# mai jitna daalun wahi hona chahiye, mai handle kar lunga"*. They were
# `API_MAX_CHAT_*` environment variables for half a day, which meant only
# somebody with a shell could change them. Everything below moves the setting and
# then proves the ROUTE changed with it — reading the store back would only prove
# the store can hold a number.
site = project(ME, "Limits")

chat_settings.save_settings({"max_chats_per_project": 1}, actor="test")
check(
    "the panel is told the operator's ceiling",
    client.get(f"/editor-chat/{site}/sessions", headers=mine).json()["limit"],
    1,
)
first = client.post(f"/editor-chat/{site}/sessions", headers=mine, json={"turns": turns("one")})
check("…one chat fits", first.status_code, 200)
r = client.post(f"/editor-chat/{site}/sessions", headers=mine, json={"turns": turns("two")})
check("…and a second is refused at ONE, not at the old default", r.status_code, 409)

chat_settings.save_settings({"max_chats_per_project": 2}, actor="test")
r = client.post(f"/editor-chat/{site}/sessions", headers=mine, json={"turns": turns("two")})
check("raising it takes effect immediately", r.status_code, 200)

# ⚠ 0 IS "NO LIMIT", NOT "FALL BACK TO THE DEFAULT". An operator who does not want
# a ceiling has to be able to say so, and this is how they say it. A `0` read as
# falsey and replaced by 40 is the exact bug `opacity: 0` had on this same router.
chat_settings.save_settings({"max_chats_per_project": 0}, actor="test")
check(
    "⚠ zero means NO limit, and the panel is told so",
    client.get(f"/editor-chat/{site}/sessions", headers=mine).json()["limit"],
    0,
)
made = [
    client.post(f"/editor-chat/{site}/sessions", headers=mine, json={"turns": turns(f"n{i}")})
    for i in range(4)
]
check("…and it stops refusing", [m.status_code for m in made], [200, 200, 200, 200])
check("…six chats in a project with no ceiling", chat_settings.get_settings()["max_chats_per_project"], 0)

# --- how many turns are KEPT ------------------------------------------------
# ⚠ THE SMALLEST LEGAL SETTING, READ OFF THE STORE'S OWN BOUNDS — a number typed
# here that is below the minimum is CLAMPED, so a hand-picked 6 would be testing
# the clamp rather than the trim.
KEEP = chat_settings.LIMITS["chat_history_keep"]["min"]
chat_settings.save_settings({"max_chats_per_project": 40, "chat_history_keep": KEEP}, actor="test")
long_talk = turns(*"abcdefgh")  # 16 turns — comfortably over KEEP
kept = client.post(
    f"/editor-chat/{site}/sessions", headers=mine, json={"turns": long_talk}
).json()
check("⚠ a long conversation is trimmed to the operator's number", len(kept["turns"]), KEEP)
# ⚠ THE OLDEST GO, NOT THE NEWEST. It is the only end that can be dropped without
# the conversation stopping making sense.
check("…and it is the NEWEST that survive", kept["turns"][-1], long_talk[-1])
check("…the oldest are the ones gone", kept["turns"][0], long_talk[-KEEP])

r = client.put(
    f"/editor-chat/{site}/sessions/{kept['session_id']}", headers=mine, json={"turns": long_talk}
)
check("…the autosave trims the same way", len(r.json()["turns"]), KEEP)

# ⚠ AND A RENAME STILL SENDS NO TURNS. `_trim(None)` must stay `None`, or the
# trim becomes the thing that deletes a transcript — the very bug §2 guards.
r = client.put(
    f"/editor-chat/{site}/sessions/{kept['session_id']}", headers=mine, json={"title": "Kept"}
)
check("⚠ …and trimming did NOT turn a rename into a wipe", len(r.json()["turns"]), KEEP)
check("…the rename landed", r.json()["title"], "Kept")

chat_settings.save_settings({"chat_history_keep": 20}, actor="test")
r = client.put(
    f"/editor-chat/{site}/sessions/{kept['session_id']}", headers=mine, json={"turns": long_talk}
)
check("raising the keep takes effect immediately too", len(r.json()["turns"]), len(long_talk))

# --- the browser must not trim first ---------------------------------------
# ⚠ IF THE BROWSER'S OWN CAP IS LOWER THAN THE ADMIN'S MAXIMUM, THE OPERATOR'S
# NUMBER SILENTLY STOPS MEANING ANYTHING ABOVE IT — the turns are gone before the
# server ever sees them. The two constants live in different languages in
# different files, so this reads both rather than trusting a comment.
_panel = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "client", "src", "animatic", "agent", "chat_sessions.js",
)
with open(_panel, encoding="utf-8") as fh:
    _js = fh.read()
_m = re.search(r"export const MAX_KEPT\s*=\s*(\d+)", _js)
check("the browser's cap is readable", bool(_m))
check(
    "⚠ …and it is NOT below the admin field's maximum",
    int(_m.group(1)) >= chat_settings.LIMITS["chat_history_keep"]["max"] if _m else False,
    True,
)

# --- and the panel can actually set them ------------------------------------
# ⚠ A FIELD THE STORE ACCEPTS BUT THE ROUTE DROPS IS A SETTINGS SCREEN THAT LIES.
# `ChatSettingsBody` is a Pydantic model, so an unlisted key is silently ignored
# rather than rejected — which is exactly how this fails quietly.
admin = account("boss@example.com")
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)
r = client.patch(
    "/admin/chat",
    headers=admin,
    json={"max_chats_per_project": 7, "chat_history_keep": 15, "max_chat_chars": 50000},
)
check("the admin panel saves all three", r.status_code, 200)
saved = chat_settings.get_settings(fresh=True)
check("…chats per project", saved["max_chats_per_project"], 7)
check("…messages kept per chat", saved["chat_history_keep"], 15)
check("…size of one chat", saved["max_chat_chars"], 50000)
# ⚠ THE BOUNDS TRAVEL WITH THE VALUES, so the number input cannot disagree with
# what the store will accept. Same rule the opacity slider follows.
payload = r.json()
for _field in ("max_chats_per_project", "chat_history_keep", "max_chat_chars"):
    check(f"…and the screen is told the bounds for {_field}",
          bool((payload.get("limits") or {}).get(_field)))

# ⚠ OUT OF BOUNDS IS CLAMPED, NOT STORED. The screen prints min–max beside every
# field, so this is the backstop for a value that never went through the screen.
client.patch("/admin/chat", headers=admin, json={"max_chats_per_project": 99999})
check(
    "a number past the maximum is clamped to it",
    chat_settings.get_settings(fresh=True)["max_chats_per_project"],
    chat_settings.LIMITS["max_chats_per_project"]["max"],
)


# ===========================================================================
print()
shutil.rmtree(_TMP, ignore_errors=True)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("✓ chats are saved per project, per owner, and nothing eats a conversation.")
