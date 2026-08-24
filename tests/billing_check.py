"""Contract checks for the tiers (admin panel, Phase 3).

Same arrangement as `admin_check.py` and `features_check.py`: every store points
at a fresh temporary directory before `server.config` is imported, so this needs
no MongoDB, no network and no AI quota.

What it actually guards, in rough order of how much it would hurt to get wrong:

  MONEY IS INTEGER MINOR UNITS, ALWAYS. 2800 is $28.00. A float arriving at the
  store is REFUSED rather than rounded — quietly storing 28.5 as 28 cents is a
  hundredfold pricing error that nobody notices until an invoice.

  ONE STATEMENT OF WHAT A TIER INCLUDES. A tier does not carry a feature list;
  each FEATURE names the tier it needs, and "what's in Pro" is derived. The two
  therefore cannot disagree — and the test proves the derivation moves when the
  feature moves, not when a second list is edited.

  A LOCKED FEATURE IS VISIBLE. Something above your tier is drawn WITH A LOCK,
  because a feature nobody can see is a feature nobody upgrades for. That is a
  different answer from "hidden" and from "soon", and all three are asserted.

  RANK IS THE LADDER, NOT PRICE. Dropping Pro to $19 for a weekend must not
  reorder it below Starter and silently change what every `min_tier` means.

  ARCHIVE, NEVER DELETE, AND NEVER THE DEFAULT. A tier somebody is on has to
  keep resolving; the tier every account falls back to cannot be archived at all.

    python tests/billing_check.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.mkdtemp(prefix="billing_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
for name, fn in [
    ("USERS", "users"), ("DRAFTS", "drafts"), ("EVENTS", "events"),
    ("FEATURES", "features"), ("TIERS", "tiers"), ("JOBS", "jobs"),
]:
    os.environ[f"API_LOCAL_{name}_PATH"] = os.path.join(_TMP, f"{fn}.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "billing-check-not-a-real-secret"
# No caching in the test — see the same note in features_check.py.
os.environ["API_FEATURE_CACHE_TTL_S"] = "0"
os.environ["API_TIER_CACHE_TTL_S"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from server import billing as bill, features as feat, users as users_mod  # noqa: E402
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


def patch_tier(tid, body, token=None):
    return client.patch(f"/admin/tiers/{tid}", json=body, headers=bearer(token or BOSS))


def set_min_tier(key, tier):
    r = client.post(f"/admin/features/{key}/min-tier", json={"tier": tier}, headers=bearer(BOSS))
    assert r.status_code == 200, r.text
    return r


BOSS = register("boss@example.com")
CUST = register("cust@example.com")
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)


# ===========================================================================
print("\n--- the price list is public ---")
# ===========================================================================
# ⚠ NO TOKEN. A price list is public by nature; requiring a session to read one
# would force a landing page to keep a second, parallel copy of the prices —
# which is the duplication this whole phase removes.
r = client.get("/billing/tiers")
check("anyone can read the price list", r.status_code, 200)
body = r.json()
check("…and it has the four seeded tiers", len(body["tiers"]), 4)
check("…in ladder order",
      [t["id"] for t in body["tiers"]], ["trial", "starter", "pro", "production"])
check("…priced in minor units", body["tiers"][1]["monthly"], 2800)
check("…with the struck-through price", body["tiers"][1]["compare_at"], 6900)
check("…and a currency to render them in", body["currency"], "USD")
# Which tier the CALLER is on is a different question, answered elsewhere.
check("…and it says nothing about who is asking", "current" in r.text, False)


# ===========================================================================
print("\n--- money is integers, in minor units ---")
# ===========================================================================
r = patch_tier("starter", {"monthly": 3500})
check("a price can be changed", r.status_code, 200)
check("…and it takes", r.json()["monthly"], 3500)
check("…and reaches the public list",
      client.get("/billing/tiers").json()["tiers"][1]["monthly"], 3500)

# ⚠ THE ONE THAT MATTERS. A float here means the caller is thinking in dollars,
# and rounding 28.5 to 28 cents would be a hundredfold error nobody sees until
# an invoice. Pydantic refuses it before the store is even reached.
r = patch_tier("starter", {"monthly": 28.5})
check("a fractional price is refused, not rounded", r.status_code, 422)
r = patch_tier("starter", {"monthly": -100})
check("a negative price is refused", r.status_code, 422)
check("…and none of that changed the stored price",
      bill.all_tiers()["starter"]["monthly"], 3500)
patch_tier("starter", {"monthly": 2800})


# ===========================================================================
print("\n--- rank is the ladder, price is not ---")
# ===========================================================================
check("the ladder is ranked", bill.rank_of("pro") > bill.rank_of("starter"))
check("meets() compares ranks", bill.meets("pro", "starter"), True)
check("…in the right direction", bill.meets("starter", "pro"), False)
check("…and no requirement always passes", bill.meets("trial", None), True)
check("an unknown tier falls back to the default's rank",
      bill.rank_of("nonexistent"), bill.rank_of(bill.DEFAULT_TIER))

# ⚠ A SALE MUST NOT REORDER THE LADDER. Pro at £19 is still above Starter, or
# every `min_tier` in the app silently changes meaning for the weekend.
patch_tier("pro", {"monthly": 1900})
check("dropping Pro below Starter's price…", bill.all_tiers()["pro"]["monthly"] < 2800)
check("…does NOT move it down the ladder", bill.meets("pro", "starter"), True)
patch_tier("pro", {"monthly": 6900})


# ===========================================================================
print("\n--- one statement of what a tier includes ---")
# ===========================================================================
# ⚠ A TIER STORES NO FEATURE LIST. Each FEATURE names the tier it needs, and
# "what's in Pro" is derived by asking them — which is why the two can never
# disagree. Moving the requirement moves the derivation, with nothing else edited.
before = set(bill.includes("trial"))
check("everything is in the free tier to begin with", len(before), 12)

set_min_tier("cap.veo-render", "pro")
check("Veo leaves the free tier", "cap.veo-render" in bill.includes("trial"), False)
check("…and Starter", "cap.veo-render" in bill.includes("starter"), False)
check("…and appears in Pro", "cap.veo-render" in bill.includes("pro"), True)
check("…and everything above it", "cap.veo-render" in bill.includes("production"), True)

r = client.get("/admin/tiers", headers=bearer(BOSS))
pro = next(t for t in r.json()["tiers"] if t["id"] == "pro")
check("the pricing screen shows the derived list",
      any(f["key"] == "cap.veo-render" for f in pro["includes"]), True)
check("…with a readable label, not a key",
      next(f["label"] for f in pro["includes"] if f["key"] == "cap.veo-render"),
      "Veo video renders")


# ===========================================================================
print("\n--- a locked feature is VISIBLE, and it refuses ---")
# ===========================================================================
state = feat.resolve("cust@example.com")["cap.veo-render"]
# ⚠ THREE DIFFERENT ANSWERS, AND THEY MUST NOT COLLAPSE INTO ONE BOOLEAN:
#   hidden → not drawn at all
#   soon   → drawn, not for sale at any price
#   tier   → drawn WITH A LOCK, one upgrade away
check("a customer below the tier can't use it", state["on"], False)
check("…but still SEES it — nobody upgrades for what they can't see",
      state["visible"], True)
check("…and the reason is the tier", state["source"], "tier")
check("…which is named, so the upgrade page knows what to sell",
      state["min_tier"], "pro")

from server.jobs import get_store  # noqa: E402
from server.schemas import JobKind  # noqa: E402

animatic = get_store().create("A", kind=JobKind.ANIMATIC, owner="cust@example.com")
r = client.post(f"/animatics/{animatic.job_id}/animate", json={}, headers=bearer(CUST))
check("…and the route refuses", r.status_code, 403)

# Buy it, and it opens — no other change.
users_mod.set_tier("cust@example.com", "pro")
check("moving them to Pro unlocks it", feat.is_on("cust@example.com", "cap.veo-render"), True)
users_mod.set_tier("cust@example.com", "trial")

# ⚠ ADMINS PASS THE TIER GATE, same as the rollout gates — you cannot price
# something you are not allowed to look at.
check("an admin is never priced out", feat.is_on("boss@example.com", "cap.veo-render"), True)

# ⚠ AND AN OVERRIDE STILL WINS. It is the highest-precedence answer, so it can
# hand one customer a feature above their tier without moving them onto it.
client.post(
    "/admin/users/cust@example.com/override",
    json={"key": "cap.veo-render", "value": True},
    headers=bearer(BOSS),
)
check("an override beats the tier gate", feat.is_on("cust@example.com", "cap.veo-render"), True)
client.post(
    "/admin/users/cust@example.com/override",
    json={"key": "cap.veo-render", "value": None},
    headers=bearer(BOSS),
)

# The rollout has to be able to say no BEFORE the tier does: something still
# being staged is not for sale at any price.
r = client.patch(
    "/admin/features/cap.veo-render",
    json={"rollout": {"mode": "admins", "emails": [], "percent": 100}},
    headers=bearer(BOSS),
)
state = feat.resolve("cust@example.com")["cap.veo-render"]
check("a staged feature is refused by the ROLLOUT, not the tier",
      state["source"], "admins-only")
check("…and is not drawn as an upsell", state["visible"], False)
client.patch(
    "/admin/features/cap.veo-render",
    json={"rollout": {"mode": "all", "emails": [], "percent": 100}},
    headers=bearer(BOSS),
)

# A locked WORKFLOW reaches the sidebar wearing its lock.
set_min_tier("workflow.animatics-to-video", "starter")
ent = client.get("/auth/me/entitlements", headers=bearer(CUST)).json()
row = next((w for w in ent["workflows"] if w["id"] == "animatics-to-video"), None)
check("a locked workflow stays in the rail", row is not None)
check("…flagged as locked", row and row["locked"], True)
check("…naming the tier that unlocks it", row and row["min_tier"], "starter")
check("…and the entitlements say which tier they're on", ent["tier"], "trial")
set_min_tier("workflow.animatics-to-video", "")
set_min_tier("cap.veo-render", "")


# ===========================================================================
print("\n--- who is on what ---")
# ===========================================================================
check("an account with no tier field is on the default",
      bill.tier_of("cust@example.com"), bill.DEFAULT_TIER)

r = client.post("/admin/users/cust@example.com/tier", json={"tier": "pro"}, headers=bearer(BOSS))
check("an admin can move somebody", r.status_code, 200)
check("…and the detail says so", r.json()["tier"], "pro")
check("…by name", r.json()["tier_name"], "Pro Unlimited")
check("…and it is recorded",
      client.get("/admin/events?type=admin.user_tier_changed", headers=bearer(BOSS))
      .json()["events"][0]["meta"],
      {"was": "trial", "now": "pro"})

r = client.post("/admin/users/cust@example.com/tier", json={"tier": "nope"}, headers=bearer(BOSS))
check("an unknown tier is refused", r.status_code, 404)
r = client.post("/admin/users/boss@example.com/tier", json={"tier": "pro"}, headers=bearer(BOSS))
# ⚠ An administrator upgrading their OWN account is the one change here with an
# obvious motive to be done quietly.
check("an admin can't upgrade themselves", r.status_code, 400)

check("the pricing screen counts subscribers",
      next(t for t in client.get("/admin/tiers", headers=bearer(BOSS)).json()["tiers"]
           if t["id"] == "pro")["subscribers"], 1)
# ⚠ ABSENT MEANS THE DEFAULT TIER, NOT "no tier". Reporting 0 for Trial would
# make the pricing screen look like nobody had ever signed up.
check("…and counts everyone without a tier as Trial",
      next(t for t in client.get("/admin/tiers", headers=bearer(BOSS)).json()["tiers"]
           if t["id"] == "trial")["subscribers"], 1)

# A tier that stops existing must not leave an account unrankable.
users_mod.set_tier("cust@example.com", "a-tier-that-was-renamed-away")
check("a stale tier id falls back to the default",
      bill.tier_of("cust@example.com"), bill.DEFAULT_TIER)
users_mod.set_tier("cust@example.com", "trial")


# ===========================================================================
print("\n--- archive, never delete ---")
# ===========================================================================
r = patch_tier("production", {"archived": True})
check("a tier can be archived", r.status_code, 200)
check("…and leaves the public price list",
      [t["id"] for t in client.get("/billing/tiers").json()["tiers"]].count("production"), 0)
check("…while still resolving for anyone on it", bill.rank_of("production") > 0)
check("…and the admin screen still lists it",
      any(t["id"] == "production" for t in
          client.get("/admin/tiers", headers=bearer(BOSS)).json()["tiers"]), True)
patch_tier("production", {"archived": False})

# ⚠ EVERY ACCOUNT WITHOUT A TIER FALLS BACK TO THIS ONE. Archiving it would
# leave most of the userbase pointing at a tier the pricing page refuses to show.
r = patch_tier("trial", {"archived": True})
check("the DEFAULT tier can't be archived", r.status_code, 400)
check("…with a sentence that says why", "can't be archived" in r.json()["detail"])

r = patch_tier("starter", {"visible": False})
check("a tier can be hidden from the page without archiving it", r.status_code, 200)
check("…and it goes",
      [t["id"] for t in client.get("/billing/tiers").json()["tiers"]].count("starter"), 0)
patch_tier("starter", {"visible": True})


# ===========================================================================
print("\n--- the editor ---")
# ===========================================================================
check("a customer cannot read the tier admin",
      client.get("/admin/tiers", headers=bearer(CUST)).status_code, 404)
check("…nor write one",
      patch_tier("starter", {"monthly": 1}, token=CUST).status_code, 404)
check("…and the stored price is untouched", bill.all_tiers()["starter"]["monthly"], 2800)

check("an unknown tier is 404", patch_tier("nope", {"monthly": 100}).status_code, 404)
check("an empty change is refused", patch_tier("starter", {}).status_code, 400)

r = patch_tier("starter", {"name": "Creator", "blurb": "For solo creators."})
check("copy can be edited", r.json()["name"], "Creator")
check("…and reaches the public list",
      client.get("/billing/tiers").json()["tiers"][1]["name"], "Creator")
check("…and is recorded with the administrator",
      client.get("/admin/events?type=admin.tier_changed", headers=bearer(BOSS))
      .json()["events"][0]["actor"], "boss@example.com")
patch_tier("starter", {"name": "Starter", "blurb": "For creators. Ideal for short clips, commercials or short films."})

# ⚠ A PRICE CHANGE IS ABOUT THE SITE, NOT ABOUT ONE ACCOUNT — so it carries no
# `email`, the same as a feature change.
check("a price change is not filed against a customer",
      client.get("/admin/events?type=admin.tier_changed", headers=bearer(BOSS))
      .json()["events"][0]["email"], None)


# ===========================================================================
print("\n--- failing open ---")
# ===========================================================================
# A pricing page that renders nothing is a shop with the lights off.
broken = bill._read_stored
bill._read_stored = lambda: (_ for _ in ()).throw(RuntimeError("Mongo is gone"))
try:
    bill._bump()
    got = bill.all_tiers()
    check("an unreachable store still returns the catalogue", len(got), 4)
    check("…and the public price list still renders",
          len(client.get("/billing/tiers").json()["tiers"]), 4)
finally:
    bill._read_stored = broken
    bill._bump()


shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All billing/tier checks passed.")
