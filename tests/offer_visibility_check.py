"""Contract checks for whether a customer can SEE an offer (admin panel, Phase 4).

Same arrangement as `sales_check.py`, which guards the discount *arithmetic*:
every store points at a fresh temporary directory before `server.config` is
imported, so this needs no MongoDB, no network and no AI quota. This suite
guards the other half — a discount that works perfectly and reaches nobody.

What it actually guards, in rough order of how much it would hurt to get wrong:

  A COUPON NOBODY IS SHOWN IS A DISCOUNT THAT ONLY EXISTS IN THE ADMIN PANEL.
  A sale reaches a customer as a changed number on a plan card, so it announces
  itself. A coupon changes nothing until it is typed — so unless the pricing
  page is told about it, an administrator can create "20% off everything", see
  it listed as live in the panel, and no customer will ever encounter it. That
  was the bug; `GET /billing/tiers` → `offers` is the fix.

  SHOWN AND WORKING ARE TWO DIFFERENT SWITCHES. `active` is whether the discount
  applies; `promoted` is whether anybody is told. A hidden coupon must STILL
  WORK when typed — that is the code you email to one customer — and a switched
  off one must do neither.

  AN ABSENT `promoted` READS AS TRUE. Every offer in every store predates the
  field. Reading a missing key as "hidden" would silently un-advertise offers
  that were already running.

  THE PUBLIC PAYLOAD IS AN ALLOW-LIST, NOT A DELETE-LIST. The stored row carries
  `created_by`, `updated_by` and the raw redemption count. A public route that
  spread the row and popped three keys would leak the fourth one somebody adds
  next month, so the field list is asserted EXACTLY.

  NOTHING EXPIRED, EXHAUSTED OR SWITCHED OFF IS EVER ADVERTISED. An offer on the
  page that the checkout would refuse is a promise broken in front of a customer.

  IT STILL FAILS CLOSED. An unreachable offer store advertises nothing, for the
  same reason `offers.all_offers` returns nothing: inventing a discount gives
  money away.

    python tests/offer_visibility_check.py
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.mkdtemp(prefix="offer_visibility_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
for name, fn in [
    ("USERS", "users"), ("DRAFTS", "drafts"), ("EVENTS", "events"),
    ("FEATURES", "features"), ("TIERS", "tiers"), ("OFFERS", "offers"),
    ("SUBSCRIPTIONS", "subs"), ("JOBS", "jobs"),
]:
    os.environ[f"API_LOCAL_{name}_PATH"] = os.path.join(_TMP, f"{fn}.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "offer-visibility-check-not-a-real-secret"
for var in ("FEATURE", "TIER", "OFFER"):
    os.environ[f"API_{var}_CACHE_TTL_S"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from server import offers as off, users as users_mod  # noqa: E402
from server.main import app  # noqa: E402

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)
OFFERS_PATH = Path(os.environ["API_LOCAL_OFFERS_PATH"])


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


def kill(offer_id):
    client.patch(f"/admin/offers/{offer_id}", json={"active": False}, headers=bearer(BOSS))


def advertised():
    """The offers the public pricing page is currently printing."""
    return client.get("/billing/tiers").json()["offers"]


def codes_on_page():
    return {o["code"] for o in advertised()}


BOSS = register("boss@example.com")
CUST = register("cust@example.com")
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)


# ===========================================================================
print("\n--- A COUPON REACHES THE PRICING PAGE ---")
# ===========================================================================
# ⚠ THE WHOLE POINT OF THIS SUITE. Creating a coupon in the panel used to change
# nothing a customer could ever see.
launch = make_offer(code="LAUNCH50", kind="percent", value=20, label="Launch week")
check("a new coupon is promoted by default", launch["promoted"], True)
check("…and the panel says it is live", launch["live"], True)

page = advertised()
check("…and the pricing page advertises exactly it", len(page), 1)
card = page[0]
check("…with its code on the card", card["code"], "LAUNCH50")
check("…and the discount in words", card["summary"], "20% off")
check("…and its name", card["label"], "Launch week")
check("…marked as a coupon, not a sale", card["is_sale"], False)
# ⚠ AN EMPTY `applies_to` MEANS EVERY PLAN. The card renders that as "every
# plan"; sending [] and reading it as "no plans" would print a discount on
# nothing.
check("…covering every plan", card["applies_to"], [])
check("…on either period", card["period"], "both")
check("…with no redemption cap", card["remaining"], None)

# ⚠ PUBLIC MEANS PUBLIC. A price list needs no session, and neither does the
# offer printed beside it — otherwise a logged-out landing page cannot show one.
check("the offer list needs no token",
      "LAUNCH50" in {o["code"] for o in client.get("/billing/tiers").json()["offers"]},
      True)


# ===========================================================================
print("\n--- the public payload is an ALLOW-LIST ---")
# ===========================================================================
# ⚠ ASSERTED EXACTLY, NOT BY ABSENCE OF THREE NAMES. A test that only checked
# `created_by` is missing would pass the day somebody spreads the stored row.
check("the card carries exactly the fields chosen for it",
      sorted(card.keys()),
      sorted(["id", "code", "label", "summary", "kind", "value", "period",
              "applies_to", "ends_at", "banner", "is_sale", "remaining"]))
check("…so who created it does not leak", "created_by" in card, False)
check("…nor how many have been redeemed", "redeemed" in card, False)
check("…nor the internal on/off switch", "active" in card, False)


# ===========================================================================
print("\n--- HIDDEN IS NOT THE SAME AS SWITCHED OFF ---")
# ===========================================================================
private = make_offer(code="FRIEND10", kind="percent", value=10, promoted=False)
check("a coupon can be created hidden", private["promoted"], False)
check("…and it is not on the pricing page", "FRIEND10" in codes_on_page(), False)

# ⚠ AND IT STILL WORKS. This is the entire reason the two switches are separate:
# a code emailed to one customer must be usable by them and invisible to
# everybody else.
r = client.post("/billing/coupon",
                json={"code": "FRIEND10", "tier": "starter", "period": "monthly"},
                headers=bearer(CUST))
check("…but it still works when typed", r.json()["valid"], True)
check("…and takes the right amount off", r.json()["discount"], 280)

# The reverse: switched OFF must be both invisible and refused.
kill(private["id"])
check("switching it off refuses it too",
      client.post("/billing/coupon",
                  json={"code": "FRIEND10", "tier": "starter", "period": "monthly"},
                  headers=bearer(CUST)).json()["valid"],
      False)

# Hiding a promoted coupon takes it off the page and leaves it working.
client.patch(f"/admin/offers/{launch['id']}", json={"promoted": False},
             headers=bearer(BOSS))
check("hiding a live coupon clears the page", advertised(), [])
check("…while the code keeps working",
      client.post("/billing/coupon",
                  json={"code": "LAUNCH50", "tier": "starter", "period": "monthly"},
                  headers=bearer(CUST)).json()["valid"],
      True)
client.patch(f"/admin/offers/{launch['id']}", json={"promoted": True},
             headers=bearer(BOSS))
check("…and showing it again puts it back", codes_on_page(), {"LAUNCH50"})


# ===========================================================================
print("\n--- AN OFFER THAT PREDATES THE FLAG IS STILL ADVERTISED ---")
# ===========================================================================
# ⚠ EVERY OFFER IN EVERY STORE PREDATES `promoted`. Reading a missing key as
# "hidden" would silently un-advertise the discounts that were already running,
# which is the failure this whole change exists to end — reintroduced by the fix.
raw = json.loads(OFFERS_PATH.read_text(encoding="utf-8"))
del raw[launch["id"]]["promoted"]
OFFERS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")
off._bump()
check("a row with no `promoted` key reads as promoted",
      off.is_promoted(off.get_offer(launch["id"])), True)
check("…so it is still on the pricing page", codes_on_page(), {"LAUNCH50"})
check("…and the panel's checkbox agrees",
      next(o for o in client.get("/admin/offers", headers=bearer(BOSS)).json()["offers"]
           if o["id"] == launch["id"])["promoted"],
      True)


# ===========================================================================
print("\n--- NOTHING BROKEN IS EVER ADVERTISED ---")
# ===========================================================================
# ⚠ THREE SEPARATE REASONS AN OFFER STOPS WORKING, and an offer card is a promise
# the checkout has to keep. All three are asserted, because a caller that checked
# `active` and forgot the dates puts an expired sign in the window.
expired = make_offer(code="OVER", kind="percent", value=30, ends_at=iso(-1))
check("an expired coupon is not advertised", "OVER" in codes_on_page(), False)

future = make_offer(code="SOON", kind="percent", value=30, starts_at=iso(3))
check("one that has not started is not advertised", "SOON" in codes_on_page(), False)

capped = make_offer(code="FIRST10", kind="percent", value=15, max_redemptions=2)
check("a capped coupon advertises how many are left",
      next(o for o in advertised() if o["code"] == "FIRST10")["remaining"], 2)
off.redeem(capped["id"])
check("…and the count comes down as they go",
      next(o for o in advertised() if o["code"] == "FIRST10")["remaining"], 1)
off.redeem(capped["id"])
check("…and a fully redeemed one leaves the page",
      "FIRST10" in codes_on_page(), False)

off_switch = make_offer(code="PAUSED", kind="percent", value=30)
kill(off_switch["id"])
check("a switched off coupon is not advertised", "PAUSED" in codes_on_page(), False)


# ===========================================================================
print("\n--- a sale gets a card too, and it says it needs no action ---")
# ===========================================================================
sale = make_offer(kind="percent", value=25, label="Spring sale",
                  banner="Spring sale — 25% off everything")
cards = advertised()
sale_card = next(o for o in cards if o["is_sale"])
check("a sale is advertised as a sale", sale_card["is_sale"], True)
# ⚠ NO CODE MEANS NO BOX TO TYPE INTO. The card renders "already applied to the
# prices below" instead of an Apply button, and it can only do that if the empty
# string reaches it rather than None.
check("…with an empty code, never a null", sale_card["code"], "")
check("…and its own name", sale_card["label"], "Spring sale")

# ⚠ DEEPEST DISCOUNT FIRST. If two things are on offer, the one worth reading
# about is the bigger one — and an order that depends on which was typed first is
# not an order anybody chose.
check("the deepest discount is the first card", cards[0]["summary"], "25% off")

# ⚠ HIDING A SALE HIDES THE CARD, NOT THE DISCOUNT. A sale changes every price
# whether or not it is advertised; `live_sale` never asks about `promoted`.
starter_before = next(t for t in client.get("/billing/tiers").json()["tiers"]
                      if t["id"] == "starter")["monthly"]
client.patch(f"/admin/offers/{sale['id']}", json={"promoted": False}, headers=bearer(BOSS))
r = client.get("/billing/tiers").json()
check("hiding a sale removes its card",
      any(o["is_sale"] for o in r["offers"]), False)
check("…but the price stays discounted",
      next(t for t in r["tiers"] if t["id"] == "starter")["monthly"], starter_before)


# ===========================================================================
print("\n--- the banner ---")
# ===========================================================================
# ⚠ SALES ARE ASKED FIRST, THEN PROMOTED COUPONS. A sale is already changing
# every price on the screen, so its sentence is the one that explains what the
# customer is looking at.
client.patch(f"/admin/offers/{sale['id']}", json={"promoted": True}, headers=bearer(BOSS))
client.patch(f"/admin/offers/{launch['id']}",
             json={"banner": "Use LAUNCH50 for 20% off"}, headers=bearer(BOSS))
check("a live sale's banner wins",
      client.get("/billing/tiers").json()["banner"], "Spring sale — 25% off everything")
kill(sale["id"])
check("…and a promoted coupon's banner is used when there is no sale",
      client.get("/billing/tiers").json()["banner"], "Use LAUNCH50 for 20% off")
# ⚠ A HIDDEN COUPON'S BANNER IS ALSO HIDDEN. Otherwise "Use FRIEND10" appears
# above the plans for a code that was meant for one person.
client.patch(f"/admin/offers/{launch['id']}", json={"promoted": False},
             headers=bearer(BOSS))
check("a hidden coupon's banner is not printed",
      client.get("/billing/tiers").json()["banner"], "")
client.patch(f"/admin/offers/{launch['id']}", json={"promoted": True},
             headers=bearer(BOSS))


# ===========================================================================
print("\n--- a fixed-amount discount carries its currency symbol ---")
# ===========================================================================
# ⚠ IT USED TO RENDER "5 off". Beside a percentage discount, a bare number next
# to a price reads as five percent — the one place ambiguity costs money.
fiver = make_offer(code="FIVEOFF", kind="amount", value=500)
check("an amount discount says what currency it is in", fiver["summary"], "$5 off")
check("…on the public card too",
      next(o for o in advertised() if o["code"] == "FIVEOFF")["summary"], "$5 off")
check("a percentage is untouched", off.summary({"kind": "percent", "value": 20}), "20% off")
kill(fiver["id"])


# ===========================================================================
print("\n--- it still fails CLOSED ---")
# ===========================================================================
# ⚠ THE ONE STORE IN THE PANEL THAT FAILS CLOSED. Tiers fail OPEN because a
# pricing page with no prices is a shop with the lights off; an offer that cannot
# be read is a discount nobody is currently entitled to, and advertising one
# gives money away.
broken = off._local_load
off._local_load = lambda: (_ for _ in ()).throw(RuntimeError("Mongo is gone"))
try:
    off._bump()
    check("an unreachable offer store advertises nothing", advertised(), [])
    check("…and no banner", client.get("/billing/tiers").json()["banner"], "")
    check("…while the price list still renders",
          len(client.get("/billing/tiers").json()["tiers"]), 4)
finally:
    off._local_load = broken
    off._bump()
check("…and it comes back when the store does", codes_on_page(), {"LAUNCH50"})


shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All offer-visibility checks passed.")
