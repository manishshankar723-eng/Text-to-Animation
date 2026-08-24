"""Contract checks for usage counters and limits (admin panel, Phase 5).

Same arrangement as the other panel suites: every store points at a fresh
temporary directory before `server.config` is imported, so this needs no
MongoDB, no network and no AI quota. The model calls that would spend money are
never reached — every assertion here is about a request being REFUSED, or about
arithmetic.

What it actually guards, in rough order of how much it would hurt to get wrong:

  A LIMIT REFUSES BEFORE THE SPEND, NOT AFTER. The guard is a dependency beside
  `require_feature`, so an over-quota request never reaches the model. Checking
  afterwards bills the customer for the call that told them they were over.

  TWO KINDS OF LIMIT, AND THEY ARE NOT THE SAME SHAPE. `projects` accumulates
  over a month; `shots_per_project` describes ONE request and never
  accumulates. Conflating them turns "9 shots per project" into "9 shots ever".

  MISSING MEANS UNLIMITED, NOT ZERO. A tier that does not mention a limit must
  not be read as allowing none of it.

  COUNTED AFTER, GUARDED BEFORE. A refused request is never counted; a request
  that succeeds always is.

  THE SINK IS `ai_usage`, NOT A SECOND COUNTER.

    python tests/usage_check.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.mkdtemp(prefix="usage_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
for name, fn in [
    ("USERS", "users"), ("DRAFTS", "drafts"), ("EVENTS", "events"),
    ("FEATURES", "features"), ("TIERS", "tiers"), ("OFFERS", "offers"),
    ("SUBSCRIPTIONS", "subs"), ("USAGE", "usage"), ("JOBS", "jobs"),
]:
    os.environ[f"API_LOCAL_{name}_PATH"] = os.path.join(_TMP, f"{fn}.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "usage-check-not-a-real-secret"
for var in ("FEATURE", "TIER", "OFFER"):
    os.environ[f"API_{var}_CACHE_TTL_S"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from server import usage as usage_mod, users as users_mod  # noqa: E402
from server.main import app  # noqa: E402

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


def make_plan(token, title="x"):
    return client.post("/plans", json={"title": title}, headers=bearer(token))


BOSS = register("boss@example.com")
CUST = register("cust@example.com")
# ⚠ A SEPARATE ACCOUNT FOR THE PER-REQUEST CAPS, and working out why is the most
# useful thing this file found: `require_quota("projects")` is a DEPENDENCY, so
# it runs before the route body — and therefore before the shot cap inside it.
# An account that is already out of projects gets refused by the wrong rule with
# the same 402, so a test using it would pass without checking what it claims to.
# This one always has project headroom, so the cap under test is the one that
# fires.
CAPS = register("caps@example.com")
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)


# ===========================================================================
print("\n--- missing means unlimited, not zero ---")
# ===========================================================================
# ⚠ THE READING THAT WOULD BREAK EVERY EXISTING ACCOUNT. A tier that simply does
# not mention `image_generations` must not be read as allowing none of them.
check("an unmentioned limit is unlimited",
      usage_mod.limit_of("cust@example.com", "nothing_like_this"), None)
check("…and a null limit is too",
      usage_mod.limit_of("cust@example.com", "image_generations"), 50)
# Trial says image_generations: 50; Pro says None, which is unlimited.
users_mod.set_tier("cust@example.com", "pro")
check("an explicit null is unlimited",
      usage_mod.limit_of("cust@example.com", "image_generations"), None)
allowed, used, limit = usage_mod.check("cust@example.com", "image_generations", 9999)
check("…so a huge request is allowed", allowed, True)
check("…with no limit reported", limit, None)
users_mod.set_tier("cust@example.com", "trial")


# ===========================================================================
print("\n--- the project counter ---")
# ===========================================================================
# Trial allows 2 projects.
check("the trial limit is read from the tier",
      usage_mod.limit_of("cust@example.com", "projects"), 2)
check("nothing is used yet", usage_mod.counters("cust@example.com")["projects"], 0)

check("the first project is allowed", make_plan(CUST, "one").status_code, 201)
check("…and counted", usage_mod.counters("cust@example.com")["projects"], 1)
check("the second is allowed", make_plan(CUST, "two").status_code, 201)
check("…and counted", usage_mod.counters("cust@example.com")["projects"], 2)

# ⚠ REFUSED BEFORE THE WORK, WITH A CODE THAT MEANS WHAT HAPPENED. 402 Payment
# Required is the honest one: they are authenticated, allowed the feature, and
# simply out of allowance.
r = make_plan(CUST, "three")
check("the third is refused", r.status_code, 402)
check("…saying how many they have used", "2 of your 2" in r.json()["detail"], True)
# ⚠ A REFUSED REQUEST IS NOT COUNTED. Counting first would charge people for
# work they did not get.
check("…and the refusal is not itself counted",
      usage_mod.counters("cust@example.com")["projects"], 2)

# Upgrading lifts it immediately — no re-login, no cache to wait out.
users_mod.set_tier("cust@example.com", "starter")  # 5 projects
check("upgrading lets them through again", make_plan(CUST, "four").status_code, 201)
check("…and keeps counting from where they were",
      usage_mod.counters("cust@example.com")["projects"], 3)
users_mod.set_tier("cust@example.com", "trial")
check("downgrading refuses again", make_plan(CUST, "five").status_code, 402)


# ===========================================================================
print("\n--- admins are never throttled ---")
# ===========================================================================
# ⚠ THE SAME RULE THE FEATURE GATES FOLLOW: you cannot test what you are selling
# if the panel's own account is throttled.
for i in range(4):
    r = make_plan(BOSS, f"admin-{i}")
    if r.status_code != 201:
        break
check("an admin is not stopped by the trial limit", r.status_code, 201)
check("…even well past it",
      usage_mod.counters("boss@example.com")["projects"] > 2, True)


# ===========================================================================
print("\n--- per-request caps are NOT counters ---")
# ===========================================================================
# ⚠ "9 shots per project" describes ONE board. Accumulating it would turn it
# into "9 shots ever", which is a different product.
check("the trial cap is 9 shots", usage_mod.limit_of("caps@example.com", "shots_per_project"), 9)
check("a request of 9 is within it",
      usage_mod.cap_exceeded("caps@example.com", "shots_per_project", 9), None)
check("a request of 10 is not",
      usage_mod.cap_exceeded("caps@example.com", "shots_per_project", 10), 9)
# …and asking twice does not accumulate.
check("asking again does not accumulate",
      usage_mod.cap_exceeded("caps@example.com", "shots_per_project", 9), None)
check("…and again", usage_mod.cap_exceeded("caps@example.com", "shots_per_project", 9), None)
check("an admin is never capped",
      usage_mod.cap_exceeded("boss@example.com", "shots_per_project", 500), None)


def shot(i):
    return {"scene_number": 1, "shot_number": i + 1, "description": f"Shot {i}"}


r = client.post("/storyboards", json={"shots": [shot(i) for i in range(12)], "style": "sketch"},
                headers=bearer(CAPS))
# ⚠ REFUSED BEFORE A JOB IS CREATED OR A DRAFT PROMOTED, so a refusal leaves
# nothing half-made behind.
check("a board over the shot cap is refused", r.status_code, 402)
check("…naming both numbers",
      "9 shots" in r.json()["detail"] and "12" in r.json()["detail"], True)
check("…and nothing was counted for it",
      usage_mod.counters("caps@example.com")["projects"], 0)

# The script-length cap, checked BEFORE the model call — a breakdown spends
# quota, so an over-length script must be refused before it is paid for.
users_mod.set_tier("caps@example.com", "starter")  # story_pages: 10
long_script = "INT. ROOM - DAY\n" + ("Something happens here. " * 900)
pages = round(len(long_script) / usage_mod.PAGE_CHARS)
r = client.post("/storyboards/breakdown", json={"script": long_script}, headers=bearer(CAPS))
check(f"a ~{pages}-page script is refused on a 10-page plan", r.status_code, 402)
check("…saying about how long it was", "pages" in r.json()["detail"], True)
r = client.post("/storyboards/breakdown", json={"script": "INT. ROOM - DAY\nShe waits."},
                headers=bearer(CAPS))
check("…while a short one gets past the cap", r.status_code != 402, True)
users_mod.set_tier("caps@example.com", "trial")


# ===========================================================================
print("\n--- the ai_usage sink ---")
# ===========================================================================
# ⚠ NOT A SECOND COUNTER. `Usage` already counts additively with retries
# included; this only folds it into a month.
from ai_usage import Usage  # noqa: E402

before = usage_mod.counters("cust@example.com")["text_tokens"]
usage_mod.record_tokens("cust@example.com", Usage(input=1000, output=500, calls=1,
                                                  model="gemini-2.5-flash"))
after = usage_mod.counters("cust@example.com")
check("tokens land in the month's row", after["text_tokens"] - before, 1500)
check("…and an advisory cost with them", after["cost_usd_est"] > 0, True)

usage_mod.record_tokens("cust@example.com", Usage(input=10, output=10, calls=1, unpriced=True))
after2 = usage_mod.counters("cust@example.com")
check("an unpriced model still counts its tokens",
      after2["text_tokens"] - after["text_tokens"], 20)
# ⚠ `cost_usd()` RETURNS None WHEN IT CANNOT BE STATED HONESTLY, and adding
# nothing is right — a zero would read as "that call was free".
check("…and adds nothing to the cost rather than guessing zero",
      after2["cost_usd_est"], after["cost_usd_est"])


# ===========================================================================
print("\n--- what the customer and the panel can see ---")
# ===========================================================================
r = client.get("/billing/me", headers=bearer(CUST))
check("a customer can read their own usage", r.status_code, 200)
u = r.json()["usage"]
# ⚠ A LIMIT SOMEBODY CANNOT CHECK IS ONE THEY DISCOVER BY BEING REFUSED
# MID-TASK, which is the worst moment to learn about it.
check("…with the counters", u["counters"]["projects"], 3)
check("…and the limits beside them", u["limits"]["projects"], 2)
check("…for a named period", len(u["period"]), 7)
check("…and the caps that aren't counters", u["limits"]["shots_per_project"], 9)
# ⚠ NAMED SEPARATELY so nobody mistakes them for something enforced.
check("…and what is NOT enforced is listed apart",
      set(u["not_enforced"]), {"watermark", "commercial_use"})

ent = client.get("/auth/me/entitlements", headers=bearer(CUST)).json()
check("usage rides along on the boot call", ent["usage"]["counters"]["projects"], 3)

detail = client.get("/admin/users/cust@example.com", headers=bearer(BOSS)).json()
check("the panel sees it too", detail["usage"]["counters"]["projects"], 3)
check("…with the limits", detail["usage"]["limits"]["projects"], 2)
# The View-as screen is a different caller of the SAME resolver — the fields it
# renders from must be present.
check("the view-as data is on the detail", bool(detail["feature_states"]), True)
check("…with labels to draw", bool(detail["feature_meta"]), True)


# ===========================================================================
print("\n--- counters never take a request down ---")
# ===========================================================================
# ⚠ A FAILURE READS AS ZERO, WHICH IS THE GENEROUS DIRECTION: an unreachable
# counter store must not lock a paying customer out of work they are entitled
# to. It can undercount during an outage; it must not refuse.
broken = usage_mod._local_load
usage_mod._local_load = lambda: (_ for _ in ()).throw(RuntimeError("store is gone"))
try:
    check("an unreadable counter reads as zero",
          usage_mod.counters("cust@example.com")["projects"], 0)
    check("…so the guard lets the request through",
          make_plan(CUST, "during-outage").status_code, 201)
finally:
    usage_mod._local_load = broken

# And incrementing must never raise into a request that has already succeeded.
usage_mod._local_save = lambda data: (_ for _ in ()).throw(RuntimeError("disk is full"))
try:
    usage_mod.increment("cust@example.com", "projects")
    check("a failed increment does not raise", True)
finally:
    import importlib

    importlib.reload(usage_mod)


shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All usage/limit checks passed.")
