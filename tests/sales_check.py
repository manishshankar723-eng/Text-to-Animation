"""Contract checks for offers and subscriptions (admin panel, Phase 4).

Same arrangement as the other three panel suites: every store points at a fresh
temporary directory before `server.config` is imported, so this needs no
MongoDB, no network and no AI quota.

What it actually guards, in rough order of how much it would hurt to get wrong:

  THE PRICE IS FROZEN AT PURCHASE TIME. A tier's price is what a NEW customer
  would be quoted; a subscriber pays what they agreed to. Editing a price in the
  panel must not re-price anybody — this is the single most expensive bug the
  feature could have, because nobody notices until the invoices go out.

  EXPIRY WITHOUT A SCHEDULER. There is no cron in this app. `tier_expires_at`
  is compared to the clock on every read, so access ends on time with nothing
  needing to run.

  A SALE DRIVES `compare_at`, IT DOES NOT INVENT A SECOND OLD PRICE — and two
  overlapping sales do not compound.

  DISCOUNT ARITHMETIC IN MINOR UNITS, ROUNDED DOWN ONCE, so a customer is never
  charged a cent more than the sign said.

  THE COUPON CHECK IS NOT AN ORACLE. Every rejection reads the same, and
  checking a code redeems nothing.

    python tests/sales_check.py
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.mkdtemp(prefix="sales_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
for name, fn in [
    ("USERS", "users"), ("DRAFTS", "drafts"), ("EVENTS", "events"),
    ("FEATURES", "features"), ("TIERS", "tiers"), ("OFFERS", "offers"),
    ("SUBSCRIPTIONS", "subs"), ("JOBS", "jobs"),
]:
    os.environ[f"API_LOCAL_{name}_PATH"] = os.path.join(_TMP, f"{fn}.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "sales-check-not-a-real-secret"
for var in ("FEATURE", "TIER", "OFFER"):
    os.environ[f"API_{var}_CACHE_TTL_S"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from server import (  # noqa: E402
    billing as bill,
    offers as off,
    subscriptions as subs_mod,
    users as users_mod,
)
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


def iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def make_offer(**body):
    r = client.post("/admin/offers", json=body, headers=bearer(BOSS))
    assert r.status_code == 201, r.text
    return r.json()


BOSS = register("boss@example.com")
CUST = register("cust@example.com")
register("two@example.com")
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)


# ===========================================================================
print("\n--- recording a payment ---")
# ===========================================================================
r = client.post(
    "/admin/subscriptions",
    json={"email": "cust@example.com", "tier": "starter", "period": "monthly",
          "note": "Bank transfer #1182"},
    headers=bearer(BOSS),
)
check("a payment can be recorded", r.status_code, 201)
sub = r.json()
# ⚠ THE CLIENT SENDS NO AMOUNT. The server prices it from the tier, so what
# lands in the ledger is what the pricing page would have quoted rather than a
# number the browser made up.
check("…priced from the tier by the server", sub["amount"], 2800)
check("…in minor units", isinstance(sub["amount"], int), True)
check("…and it puts them on the plan", bill.tier_of("cust@example.com"), "starter")
check("…with an end date", bool(sub["current_period_end"]), True)
check("…and it is recorded against the customer",
      client.get("/admin/events?type=subscription.started", headers=bearer(BOSS))
      .json()["events"][0]["email"], "cust@example.com")
check("…naming the administrator who entered it",
      client.get("/admin/events?type=subscription.started", headers=bearer(BOSS))
      .json()["events"][0]["actor"], "boss@example.com")

r = client.post("/admin/subscriptions",
                json={"email": "nobody@example.com", "tier": "starter", "period": "monthly"},
                headers=bearer(BOSS))
check("an unknown customer is refused", r.status_code, 404)
r = client.post("/admin/subscriptions",
                json={"email": "cust@example.com", "tier": "nope", "period": "monthly"},
                headers=bearer(BOSS))
check("an unknown tier is refused", r.status_code, 404)


# ===========================================================================
print("\n--- THE PRICE IS FROZEN AT PURCHASE TIME ---")
# ===========================================================================
# ⚠ THE MOST EXPENSIVE BUG THIS FEATURE COULD HAVE. A tier's price is what a NEW
# customer would be quoted; a subscriber pays what they agreed to.
client.patch("/admin/tiers/starter", json={"monthly": 9900}, headers=bearer(BOSS))
check("the tier's price changed", bill.all_tiers()["starter"]["monthly"], 9900)
stored = subs_mod.get(sub["id"])
check("…and the subscriber still pays what they agreed", stored["amount"], 2800)
check("…so the recorded total is unchanged too",
      subs_mod.recurring_revenue()["monthly"], 2800)
client.patch("/admin/tiers/starter", json={"monthly": 2800}, headers=bearer(BOSS))


# ===========================================================================
print("\n--- expiry, with no scheduler ---")
# ===========================================================================
check("an active subscriber is on their tier", bill.tier_of("cust@example.com"), "starter")

# ⚠ NOTHING RUNS. `tier_expires_at` is compared to the clock on every read, off
# the document the caller was already loading — so access ends on the minute
# without a cron that can silently stop working.
users_mod.set_tier_expiry("cust@example.com", iso(-1))
check("a lapsed tier drops to the free one", bill.tier_of("cust@example.com"), "trial")
users_mod.set_tier_expiry("cust@example.com", iso(30))
check("…and comes back when it hasn't lapsed", bill.tier_of("cust@example.com"), "starter")
# An absent expiry means "does not expire", not "expired" — a tier granted by
# hand with no end date has to keep working.
users_mod.set_tier_expiry("cust@example.com", None)
check("no expiry set means it never lapses", bill.tier_of("cust@example.com"), "starter")
users_mod.set_tier_expiry("cust@example.com", sub["current_period_end"])


# ===========================================================================
print("\n--- cancelling ---")
# ===========================================================================
r = client.post(f"/admin/subscriptions/{sub['id']}/cancel", headers=bearer(BOSS))
check("a subscription can be cancelled", r.status_code, 200)
check("…and it says so", r.json()["status"], "cancelled")
# ⚠ IMMEDIATE, AND THE PANEL SAYS SO. "Cancel at period end" needs something to
# run at that moment, and this app has no scheduler; a button promising a future
# action nothing will perform is worse than an honest immediate one.
check("…and they drop to the free tier now", bill.tier_of("cust@example.com"), "trial")
check("…and it is recorded",
      client.get("/admin/events?type=subscription.cancelled", headers=bearer(BOSS))
      .json()["events"][0]["email"], "cust@example.com")
check("cancelling an unknown one is 404",
      client.post("/admin/subscriptions/nope/cancel", headers=bearer(BOSS)).status_code, 404)

# ⚠ CANCELLING AN OLD RECORD MUST NOT KNOCK SOMEBODY OFF A NEWER ONE.
old = client.post("/admin/subscriptions",
                  json={"email": "two@example.com", "tier": "starter", "period": "monthly"},
                  headers=bearer(BOSS)).json()
new = client.post("/admin/subscriptions",
                  json={"email": "two@example.com", "tier": "pro", "period": "monthly"},
                  headers=bearer(BOSS)).json()
check("a second subscription moves them up", bill.tier_of("two@example.com"), "pro")
client.post(f"/admin/subscriptions/{old['id']}/cancel", headers=bearer(BOSS))
check("cancelling the OLD one leaves the new one standing",
      bill.tier_of("two@example.com"), "pro")
client.post(f"/admin/subscriptions/{new['id']}/cancel", headers=bearer(BOSS))
check("…and cancelling the last one frees them", bill.tier_of("two@example.com"), "trial")


# ===========================================================================
print("\n--- a sale ---")
# ===========================================================================
sale = make_offer(kind="percent", value=25, label="Launch week",
                  banner="Launch week — 25% off")
check("a sale has no code", sale["code"], None)
check("…and is live", sale["live"], True)

r = client.get("/billing/tiers")
starter = next(t for t in r.json()["tiers"] if t["id"] == "starter")
# ⚠ THE SALE DRIVES `compare_at`; IT DOES NOT INVENT A SECOND OLD PRICE. The
# card has always drawn one struck-through number from that field.
check("the sale discounts the price", starter["monthly"], 2100)
check("…and strikes through the higher anchor", starter["compare_at"], 6900)
check("…and labels itself", starter["sale"], "25% off")
check("…and the banner reaches the page", r.json()["banner"], "Launch week — 25% off")
check("the yearly price is discounted too", starter["yearly"], 1575)

# ⚠ TWO OVERLAPPING SALES DO NOT COMPOUND — the deeper one wins, because
# stacking them is never what was meant and "better for the customer" is the
# only tie-break that cannot be accused of short-changing anybody.
deeper = make_offer(kind="percent", value=40, label="Deeper")
starter = next(t for t in client.get("/billing/tiers").json()["tiers"] if t["id"] == "starter")
check("two sales do not stack — the deeper one wins", starter["monthly"], 1680)
client.patch(f"/admin/offers/{deeper['id']}", json={"active": False}, headers=bearer(BOSS))

# Restricted to one tier, and to one period.
client.patch(f"/admin/offers/{sale['id']}",
             json={"applies_to": ["pro"], "period": "yearly"}, headers=bearer(BOSS))
tiers = {t["id"]: t for t in client.get("/billing/tiers").json()["tiers"]}
check("a restricted sale leaves other tiers alone", tiers["starter"]["monthly"], 2800)
check("…and the wrong period alone", tiers["pro"]["monthly"], 6900)
check("…while discounting the right one", tiers["pro"]["yearly"], 3975)
client.patch(f"/admin/offers/{sale['id']}", json={"active": False}, headers=bearer(BOSS))
check("switching it off restores the price",
      next(t for t in client.get("/billing/tiers").json()["tiers"]
           if t["id"] == "starter")["monthly"], 2800)


# ===========================================================================
print("\n--- discount arithmetic ---")
# ===========================================================================
percent = {"kind": "percent", "value": 33}
# ⚠ ROUNDED DOWN, ONCE. 33% of 999 cents is 329.67; taking 329 off means the
# customer never pays a cent more than the sign promised.
check("a percentage rounds the discount down", off.discount_on(percent, 999), 329)
check("…so the price rounds in the customer's favour",
      off.apply_to_price(999, percent), 670)
check("a fixed amount is taken as given",
      off.discount_on({"kind": "amount", "value": 500}, 999), 500)
# ⚠ NEVER BELOW ZERO. A £10 coupon on a £5 plan is free, not a refund.
check("a discount bigger than the price is capped",
      off.apply_to_price(500, {"kind": "amount", "value": 9999}), 0)
check("…and never goes negative",
      off.discount_on({"kind": "amount", "value": 9999}, 500), 500)
check("100% is free", off.apply_to_price(2800, {"kind": "percent", "value": 100}), 0)
r = client.post("/admin/offers", json={"kind": "percent", "value": 150}, headers=bearer(BOSS))
# ⚠ 400, NOT 422, AND THAT IS THE RIGHT CODE. "150" is a perfectly good value for
# a FIXED-AMOUNT discount; it is only nonsense as a percentage. A Pydantic field
# cannot validate itself against a sibling, so the ceiling on the schema is the
# one both kinds share and `offers._clean` makes the cross-check — which is a
# refusal about the meaning of the request, not about its shape.
check("more than 100% is refused", r.status_code, 400)
check("…with a sentence that says why", "100" in r.json()["detail"])
# ⚠ AND IT IS SWITCHED OFF THE MOMENT IT IS PROVEN. A created offer with no code
# is a LIVE SALE — leaving it on would quietly discount every price the rest of
# this file asserts, which is exactly the trap a real administrator falls into.
r = client.post("/admin/offers", json={"kind": "amount", "value": 150},
                headers=bearer(BOSS))
check("…while 150 is fine as a fixed amount", r.status_code, 201)
client.patch(f"/admin/offers/{r.json()['id']}", json={"active": False}, headers=bearer(BOSS))


# ===========================================================================
print("\n--- coupons ---")
# ===========================================================================
coupon = make_offer(code="launch50", kind="percent", value=50, label="Half price")
# Codes are normalised — nobody types a coupon the way it was written down.
check("a code is upper-cased", coupon["code"], "LAUNCH50")
check("…and found case-insensitively", off.by_code("  Launch50 ")["id"], coupon["id"])

r = client.post("/billing/coupon",
                json={"code": "launch50", "tier": "starter", "period": "monthly"},
                headers=bearer(CUST))
check("a customer can check a code", r.status_code, 200)
check("…and is told the discount", r.json()["discount"], 1400)
check("…and the new price", r.json()["now"], 1400)

# ⚠ CHECKING IS NOT USING. The count moves when a subscription is recorded.
check("checking redeems nothing", off.get_offer(coupon["id"])["redeemed"], 0)

# ⚠ NOT AN ORACLE. Every rejection reads the same, whatever the reason —
# otherwise this route enumerates which codes exist and when a sale ends.
for bad in ["NOPE", "LAUNCH51"]:
    r = client.post("/billing/coupon",
                    json={"code": bad, "tier": "starter", "period": "monthly"},
                    headers=bearer(CUST))
    check(f"a wrong code ({bad}) is refused the same way", r.json()["valid"], False)
    check("…with nothing to learn from", r.json()["detail"], "That code isn't valid.")

# A coupon does NOT change the public price list — it applies to one person.
check("a coupon does not discount the public price list",
      next(t for t in client.get("/billing/tiers").json()["tiers"]
           if t["id"] == "starter")["monthly"], 2800)

# Recording against it redeems it, and freezes the discounted amount.
r = client.post("/admin/subscriptions",
                json={"email": "cust@example.com", "tier": "starter",
                      "period": "monthly", "code": "LAUNCH50"},
                headers=bearer(BOSS))
check("a subscription can use a code", r.status_code, 201)
check("…and the frozen amount is the discounted one", r.json()["amount"], 1400)
check("…with the discount kept beside it", r.json()["discount"], 1400)
check("…and the code stamped on the record", r.json()["offer_code"], "LAUNCH50")
check("…and now it is redeemed", off.get_offer(coupon["id"])["redeemed"], 1)

# ⚠ AN EXPIRED CODE IS REFUSED, NOT SILENTLY IGNORED. The customer was promised
# a discount; recording the full price instead is a wrong number in the ledger.
client.patch(f"/admin/offers/{coupon['id']}", json={"ends_at": iso(-1)}, headers=bearer(BOSS))
r = client.post("/admin/subscriptions",
                json={"email": "two@example.com", "tier": "starter",
                      "period": "monthly", "code": "LAUNCH50"},
                headers=bearer(BOSS))
check("an expired code refuses the whole entry", r.status_code, 400)
check("…rather than quietly charging full price", subs_mod.active_for("two@example.com"), None)
client.patch(f"/admin/offers/{coupon['id']}", json={"ends_at": None}, headers=bearer(BOSS))

# A redemption cap.
capped = make_offer(code="ONCE", kind="amount", value=100, max_redemptions=1)
client.post("/admin/subscriptions",
            json={"email": "two@example.com", "tier": "starter",
                  "period": "monthly", "code": "ONCE"},
            headers=bearer(BOSS))
check("a capped code works once", off.get_offer(capped["id"])["redeemed"], 1)
check("…and is dead afterwards", off.is_live(off.get_offer(capped["id"])), False)

r = client.post("/admin/offers", json={"code": "LAUNCH50", "kind": "percent", "value": 10},
                headers=bearer(BOSS))
check("a duplicate code is refused", r.status_code, 400)
r = client.post("/admin/offers", json={"code": "no spaces!", "kind": "percent", "value": 10},
                headers=bearer(BOSS))
check("a malformed code is refused", r.status_code, 400)

# ⚠ THE CODE IS NOT EDITABLE. One that has been printed or emailed is out in the
# world; renaming it would silently break every place it was written down.
client.patch(f"/admin/offers/{coupon['id']}", json={"label": "Renamed"}, headers=bearer(BOSS))
check("the code survives an edit", off.get_offer(coupon["id"])["code"], "LAUNCH50")


# ===========================================================================
print("\n--- dates and validity ---")
# ===========================================================================
future = make_offer(kind="percent", value=10, starts_at=iso(7))
check("an offer that hasn't started isn't live", future["live"], False)
check("…and doesn't discount anything",
      next(t for t in client.get("/billing/tiers").json()["tiers"]
           if t["id"] == "starter")["monthly"], 2800)
r = client.post("/admin/offers",
                json={"kind": "percent", "value": 10, "starts_at": iso(9), "ends_at": iso(2)},
                headers=bearer(BOSS))
check("an offer that ends before it starts is refused", r.status_code, 400)

# An empty `applies_to` means EVERY tier, not none — "20% off" with nothing
# ticked is what somebody means by a site-wide sale.
check("an empty tier list means every tier",
      off.applies_to({"applies_to": [], "period": "both"}, "pro", "monthly"), True)


# ===========================================================================
print("\n--- the guard, and the ledger ---")
# ===========================================================================
for method, path in [("GET", "/admin/offers"), ("GET", "/admin/subscriptions")]:
    check(f"a customer cannot read {path}",
          client.request(method, path, headers=bearer(CUST)).status_code, 404)
check("…nor record a payment",
      client.post("/admin/subscriptions",
                  json={"email": "cust@example.com", "tier": "pro", "period": "monthly"},
                  headers=bearer(CUST)).status_code, 404)

r = client.get("/admin/subscriptions", headers=bearer(BOSS))
check("the ledger lists them", r.status_code, 200)
check("…with the tier's readable name",
      any(s["tier_name"] == "Starter" for s in r.json()["subscriptions"]), True)
check("…and a recorded total", isinstance(r.json()["recorded_monthly"], int), True)
r = client.get("/admin/subscriptions?status=active", headers=bearer(BOSS))
check("…filterable by status",
      {s["status"] for s in r.json()["subscriptions"]}, {"active"})

detail = client.get("/admin/users/cust@example.com", headers=bearer(BOSS)).json()
check("an account's own subscriptions are on its page",
      len(detail["subscriptions"]) >= 2, True)
check("…and its expiry is shown", bool(detail["tier_expires_at"]), True)


# ===========================================================================
print("\n--- failing closed ---")
# ===========================================================================
# ⚠ THE ONE STORE IN THE PANEL THAT FAILS CLOSED. Features and tiers fail OPEN
# because the alternative is a blank app. An unreachable OFFER store is the
# opposite: a discount nobody can verify is money given away.
sale2 = make_offer(kind="percent", value=50)
check("the sale is discounting",
      next(t for t in client.get("/billing/tiers").json()["tiers"]
           if t["id"] == "starter")["monthly"], 1400)
broken = off._local_load
off._local_load = lambda: (_ for _ in ()).throw(RuntimeError("Mongo is gone"))
try:
    off._bump()
    check("an unreachable offer store applies NO discount",
          next(t for t in client.get("/billing/tiers").json()["tiers"]
               if t["id"] == "starter")["monthly"], 2800)
    check("…and the price list still renders",
          len(client.get("/billing/tiers").json()["tiers"]), 4)
finally:
    off._local_load = broken
    off._bump()
check("…and it comes back", off.is_live(off.get_offer(sale2["id"])), True)


shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All offer/subscription checks passed.")
