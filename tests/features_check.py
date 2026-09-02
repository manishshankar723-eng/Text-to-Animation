"""Contract checks for the feature registry (admin panel, Phase 2).

Same arrangement as `admin_check.py`: every store points at a fresh temporary
directory before `server.config` is imported, so this needs no MongoDB, no
network and no AI quota.

What it actually guards, in rough order of how much it would hurt to get wrong:

  HIDING A BUTTON IS NOT HIDING A FEATURE. The sidebar reading the registry is
  cosmetic — anybody can call the route directly. Every `require_feature` guard
  is exercised against a real request here, because a switch that only changes
  what is DRAWN is not a switch at all.

  GATING CREATES AND SPENDS, NEVER READS. Turning a workflow off must not make a
  customer's existing work unreachable or un-exportable. Listing, opening and
  EXPORTING a plan stay open with the workflow hidden; creating one does not.

  PRECEDENCE. hidden → override → rollout → soon → live, in that order, with the
  two cases that are easy to get backwards asserted explicitly: an override
  reopens a hidden feature, and an admin does NOT bypass "hidden".

  FAILING OPEN. An unreachable feature store must serve the last known good
  answer, and failing that the built-in defaults — never an empty map, which
  would blank every sidebar in the app at once.

  DETERMINISTIC BUCKETING. A percentage rollout must put the same account on the
  same side of the line every time, or a user flickers in and out of a feature
  between two requests on one page.

    python tests/features_check.py
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE, NOT THE FIVE THIS SUITE THINKS IT TOUCHES.** It used to name
# users / drafts / events / features / jobs and stop — and `API_LOCAL_USAGE_PATH`
# then fell back to its default, which is the **git-tracked `.local_usage.json`
# in the repo root**. Caught by running the whole `tests/` folder and then
# reading `git status`: one row for `cust@example.com` written into the
# developer's own counters, by a green suite, silently. That is G13's exact
# wording — "getting a variable's NAME wrong is silent" — and the answer to it
# is to stop naming them one at a time. See `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("features_check_")
os.environ["JWT_SECRET"] = "features-check-not-a-real-secret"
# ⚠ NO CACHE IN THE TEST. The TTL is there for OTHER worker processes; in one
# process a write bumps the cache immediately. Zero makes every read fresh so a
# failure here is a bug in the rules, never a stale read racing the assertion.
os.environ["API_FEATURE_CACHE_TTL_S"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from server import features as feat, users as users_mod  # noqa: E402
from server.main import app  # noqa: E402
from server.jobs import get_store  # noqa: E402
from server.schemas import JobKind  # noqa: E402

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def register(email):
    return client.post(
        "/auth/register", json={"email": email, "password": "password123"}
    ).json()["access_token"]


def set_status(key, status):
    r = client.patch(f"/admin/features/{key}", json={"status": status}, headers=bearer(BOSS))
    assert r.status_code == 200, r.text


def set_rollout(key, **rollout):
    body = {"rollout": {"mode": "all", "emails": [], "percent": 100, **rollout}}
    r = client.patch(f"/admin/features/{key}", json=body, headers=bearer(BOSS))
    assert r.status_code == 200, r.text


BOSS = register("boss@example.com")
CUST = register("cust@example.com")
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)
# ⚠ ON A TIER WITH NO PROJECT LIMIT, ON PURPOSE. Phase 5 made the free tier
# refuse a third project, and this file creates four while testing something
# else entirely — a quota refusal here would look like a FEATURE failure and
# send the next person hunting in the wrong module. This suite is about what is
# switched on, not about how much of it you may have; `usage_check.py` owns
# that question.
users_mod.set_tier("cust@example.com", "pro")


# ===========================================================================
print("\n--- it ships with everything on ---")
# ===========================================================================
# The whole phase adds machinery, not a change in what anybody can do. A
# deployment that never opens the panel must behave exactly as it did before.
r = client.get("/auth/me/entitlements", headers=bearer(CUST))
check("entitlements answers", r.status_code, 200)
ent = r.json()
check("every feature is on by default", all(ent["features"].values()))
check("all six workflows are visible", len(ent["workflows"]), 6)
# ⚠ THE ORDER IS THE OWNER'S CHOICE, and the client's fallback array has to
# match it or a database hiccup silently reorders somebody's sidebar.
check(
    "the order matches the client's fallback",
    [w["id"] for w in ent["workflows"]],
    ["plan-and-script", "text-to-image", "script-to-storyboard",
     "create-animatic-image", "animatics-to-video", "storyboard-to-animatics"],
)
check("the historical id is preserved", "animatics-to-video" in ent["features"].keys() or
      any(w["id"] == "animatics-to-video" for w in ent["workflows"]))


# ===========================================================================
print("\n--- hiding a workflow: the rail AND the routes ---")
# ===========================================================================
set_status("workflow.plan-and-script", "hidden")

ent = client.get("/auth/me/entitlements", headers=bearer(CUST)).json()
check("it leaves the sidebar", [w["id"] for w in ent["workflows"]].count("plan-and-script"), 0)
check("…and the other five stay", len(ent["workflows"]), 5)

# ⚠ THE POINT OF THE WHOLE PHASE. Hiding the button is cosmetic; this is the
# switch actually being thrown.
r = client.post("/plans", json={"title": "x"}, headers=bearer(CUST))
check("creating one is refused", r.status_code, 403)
check("…with a sentence, not a code", "Plan & Script" in r.json()["detail"])

# Make a session with the workflow ON, then hide it and check the reads.
set_status("workflow.plan-and-script", "live")
plan_id = client.post("/plans", json={"title": "Kept"}, headers=bearer(CUST)).json()["job_id"]
set_status("workflow.plan-and-script", "hidden")

# ⚠ GATING CREATES AND SPENDS, NEVER READS. Switching a workflow off is a
# product decision; it is not a reason to lock somebody out of work they have
# already made and may need to get out of the app.
check("listing existing sessions still works",
      client.get("/plans", headers=bearer(CUST)).status_code, 200)
check("opening one still works",
      client.get(f"/plans/{plan_id}", headers=bearer(CUST)).status_code, 200)
check("renaming one still works",
      client.patch(f"/plans/{plan_id}", json={"title": "Renamed"}, headers=bearer(CUST)).status_code, 200)
check("but spending quota in it is refused",
      client.post(f"/plans/{plan_id}/chat", json={"message": "hi"}, headers=bearer(CUST)).status_code, 403)
check("and deleting is still allowed",
      client.delete(f"/plans/{plan_id}", headers=bearer(CUST)).status_code, 204)

set_status("workflow.plan-and-script", "live")
check("switching it back on reopens it",
      client.post("/plans", json={"title": "y"}, headers=bearer(CUST)).status_code, 201)


# ===========================================================================
print("\n--- 'soon': drawn with a badge, and it still refuses ---")
# ===========================================================================
set_status("workflow.script-to-storyboard", "soon")
ent = client.get("/auth/me/entitlements", headers=bearer(CUST)).json()
row = next((w for w in ent["workflows"] if w["id"] == "script-to-storyboard"), None)
check("it is STILL in the sidebar", row is not None)
check("…carrying the badge", row and row["status"], "soon")
# ⚠ VISIBLE AND OFF ARE DIFFERENT ANSWERS, which is why the resolver returns
# both. One boolean here is what made the old placeholder navigate to a blank
# page — see the note restored at the top of App.jsx.
check("…but not usable", ent["features"]["workflow.script-to-storyboard"], False)
r = client.post("/storyboards/breakdown", json={"script": "INT. DAY"}, headers=bearer(CUST))
check("…and the route refuses", r.status_code, 403)
check("…saying 'not yet', not 'not enabled'", "yet" in r.json()["detail"])
set_status("workflow.script-to-storyboard", "live")


# ===========================================================================
print("\n--- capabilities: the expensive things, one at a time ---")
# ===========================================================================
animatic = get_store().create("A", kind=JobKind.ANIMATIC, owner="cust@example.com")

set_status("cap.veo-render", "hidden")
r = client.post(f"/animatics/{animatic.job_id}/animate", json={"frame_ids": []}, headers=bearer(CUST))
check("Veo refuses when switched off", r.status_code, 403)
# ⚠ THE GUARD RUNS BEFORE THE BODY IS EVEN LOOKED AT, which is what makes it a
# real spend control: a malformed request to a switched-off feature must not be
# able to get far enough to cost anything.
r = client.post(f"/animatics/{animatic.job_id}/animate", json={"nonsense": 1}, headers=bearer(CUST))
check("…before validating the body", r.status_code, 403)
set_status("cap.veo-render", "live")

set_status("cap.tts-voiceover", "hidden")
check("voiceover refuses",
      client.post(f"/animatics/{animatic.job_id}/voiceover", json={}, headers=bearer(CUST)).status_code, 403)
check("…while captions, a DIFFERENT switch, are untouched",
      client.post(f"/animatics/{animatic.job_id}/captions", json={}, headers=bearer(CUST)).status_code != 403,
      True)
set_status("cap.tts-voiceover", "live")

set_status("cap.image-generate", "hidden")
check("drawing a reference refuses",
      client.post("/characters/reference", json={"description": "a knight"}, headers=bearer(CUST)).status_code, 403)
check("…and so does the storyboard panel redraw",
      client.post("/storyboards/x/regenerate-panel", json={"index": 0}, headers=bearer(CUST)).status_code, 403)
set_status("cap.image-generate", "live")


# ===========================================================================
print("\n--- precedence ---")
# ===========================================================================
set_status("cap.captions", "hidden")
check("hidden means off", feat.is_on("cust@example.com", "cap.captions"), False)

# ⚠ AN OVERRIDE IS THE ONLY THING THAT REOPENS A HIDDEN FEATURE, and that is
# how an administrator looks at something switched off for the site.
r = client.post(
    "/admin/users/cust@example.com/override",
    json={"key": "cap.captions", "value": True},
    headers=bearer(BOSS),
)
check("an override can reopen it", r.status_code, 200)
check("…and it takes", feat.is_on("cust@example.com", "cap.captions"), True)
check("…and the panel says why",
      r.json()["feature_states"]["cap.captions"]["source"], "override")

# ⚠ THE ONE THAT IS EASY TO GET BACKWARDS. Admins bypass the ROLLOUT gates so
# they can look at what they are staging — but "hidden" is the switch that means
# everyone, and an admin who wants past it gives themselves an override.
check("an admin does NOT bypass hidden", feat.is_on("boss@example.com", "cap.captions"), False)

client.post(
    "/admin/users/cust@example.com/override",
    json={"key": "cap.captions", "value": None},
    headers=bearer(BOSS),
)
check("clearing the override hands it back to the rule",
      feat.is_on("cust@example.com", "cap.captions"), False)
set_status("cap.captions", "live")

# An explicit `false` is not the same as no opinion: it survives the rule
# changing underneath it.
client.post(
    "/admin/users/cust@example.com/override",
    json={"key": "cap.captions", "value": False},
    headers=bearer(BOSS),
)
check("an explicit deny outlives the feature going live",
      feat.is_on("cust@example.com", "cap.captions"), False)
check("…while everyone else has it", feat.is_on("boss@example.com", "cap.captions"), True)
client.post(
    "/admin/users/cust@example.com/override",
    json={"key": "cap.captions", "value": None},
    headers=bearer(BOSS),
)


# ===========================================================================
print("\n--- rollout rules ---")
# ===========================================================================
set_rollout("cap.director", mode="admins")
check("admins-only keeps a customer out", feat.is_on("cust@example.com", "cap.director"), False)
check("…and lets an admin in", feat.is_on("boss@example.com", "cap.director"), True)

set_rollout("cap.director", mode="allowlist", emails=["CUST@example.com"])
# The address is normalised on the way in, or a capital letter silently excludes
# the person it was meant to include.
check("an allow-list matches case-insensitively", feat.is_on("cust@example.com", "cap.director"), True)
check("…and excludes everyone else", feat.is_on("nobody@example.com", "cap.director"), False)
check("…while an admin can still see what they are staging",
      feat.is_on("boss@example.com", "cap.director"), True)

set_rollout("cap.director", mode="percent", percent=0)
check("0% is nobody", feat.is_on("cust@example.com", "cap.director"), False)
set_rollout("cap.director", mode="percent", percent=100)
check("100% is everybody", feat.is_on("cust@example.com", "cap.director"), True)

# ⚠ HASHED, NOT RANDOM. Random would flip a user in and out between two requests
# on one page.
set_rollout("cap.director", mode="percent", percent=50)
first = feat.is_on("cust@example.com", "cap.director")
check("a percentage is stable across reads",
      all(feat.is_on("cust@example.com", "cap.director") == first for _ in range(20)))
# …and salted with the key, so a 10% rollout of five features doesn't land on
# the same unlucky tenth of the userbase every time.
buckets = {
    key: feat._in_percent("someone@example.com", key, 50)
    for key in ("cap.a", "cap.b", "cap.c", "cap.d", "cap.e", "cap.f")
}
check("…and different features bucket differently", len(set(buckets.values())), 2)
set_rollout("cap.director", mode="all")


# ===========================================================================
print("\n--- failing open ---")
# ===========================================================================
# ⚠ AN EMPTY FEATURE MAP IS EVERY SIDEBAR IN THE APP GOING BLANK AT ONCE, which
# is a worse outage than whatever caused it. Simulate the store being gone.
broken = feat._read_stored
feat._read_stored = lambda: (_ for _ in ()).throw(RuntimeError("Mongo is gone"))
try:
    feat._bump()  # drop the cache so the next read has nothing to fall back on
    got = feat.all_features()
    check("an unreachable store still returns the catalogue", len(got), 12)
    check("…all of it live", {f["status"] for f in got.values()}, {"live"})
    check("…and a guard lets the request through",
          feat.is_on("cust@example.com", "cap.veo-render"), True)
    # An unknown key is ON too: a guard naming a feature that was never added to
    # the catalogue must not silently close a working route.
    check("an unknown feature key is open, not closed",
          feat.is_on("cust@example.com", "cap.does-not-exist"), True)
finally:
    feat._read_stored = broken
    feat._bump()


# ===========================================================================
print("\n--- the switchboard itself ---")
# ===========================================================================
check("a customer cannot read it",
      client.get("/admin/features", headers=bearer(CUST)).status_code, 404)
r = client.get("/admin/features", headers=bearer(BOSS))
check("an admin can", r.status_code, 200)
check("…and it lists every feature", len(r.json()["features"]), 12)

r = client.patch("/admin/features/cap.veo-render", json={"status": "nonsense"}, headers=bearer(BOSS))
check("an unknown status is rejected", r.status_code, 422)
r = client.patch("/admin/features/nope.nope", json={"status": "live"}, headers=bearer(BOSS))
check("an unknown feature is 404", r.status_code, 404)
r = client.patch("/admin/features/cap.veo-render", json={}, headers=bearer(BOSS))
check("an empty change is refused", r.status_code, 400)

r = client.patch(
    "/admin/features/cap.veo-render",
    json={"rollout": {"mode": "percent", "percent": 500}},
    headers=bearer(BOSS),
)
check("an out-of-range percentage is rejected by the schema", r.status_code, 422)

# Renaming and reordering — the two edits that are not about access at all.
r = client.patch(
    "/admin/features/workflow.text-to-image",
    json={"label": "Character Sheets", "order": 0},
    headers=bearer(BOSS),
)
check("a workflow can be renamed", r.json()["label"], "Character Sheets")
ent = client.get("/auth/me/entitlements", headers=bearer(CUST)).json()
check("…and the new name reaches the sidebar",
      ent["workflows"][0]["label"], "Character Sheets")
check("…in the new order", ent["workflows"][0]["id"], "text-to-image")
client.patch(
    "/admin/features/workflow.text-to-image",
    json={"label": "Text to Turnaround Image", "order": 1},
    headers=bearer(BOSS),
)

# Every change is recorded, with the administrator who made it.
r = client.get("/admin/events?type=admin.feature_changed&limit=5", headers=bearer(BOSS))
rows = r.json()["events"]
check("feature changes are recorded", len(rows) > 0)
check("…naming the administrator", rows[0]["actor"], "boss@example.com")
check("…and the feature", rows[0]["meta"]["feature"], "workflow.text-to-image")
# ⚠ A SITE-WIDE CHANGE HAS NO `email` — it happened to everyone, not to one
# account, and pretending otherwise would file it under whoever made it.
check("…and it is not filed against a customer", rows[0]["email"], None)


# ===========================================================================
print("\n--- the guard is not the only lock ---")
# ===========================================================================
# Signed out, a switched-off feature must still answer "who are you" first —
# `require_feature` depends on `get_current_user`, so authentication comes first
# and an anonymous caller never learns which features exist.
set_status("cap.veo-render", "hidden")
r = client.post(f"/animatics/{animatic.job_id}/animate", json={})
check("an anonymous caller gets 401/403, never a feature name",
      r.status_code in (401, 403), True)
check("…and no feature label leaks", "Veo" in r.text, False)
set_status("cap.veo-render", "live")


shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All feature registry checks passed.")
