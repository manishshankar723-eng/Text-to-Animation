"""
billing.py — the tiers: what they cost, what they include, and who is on one.

⚠ "TIERS", NEVER "PLANS", ANYWHERE IN THIS CODEBASE. `server/plans.py`, the
`/plans` route and `JobKind.PLAN` have meant *Plan & Script* — the
content-planning workflow — since long before there was any billing here. This
module, its collection and its routes are all `tiers` for that one reason, and
the name is worth the small awkwardness every time.

THE DECISION THAT SHAPES THIS FILE
----------------------------------
⚠ **A TIER DOES NOT LIST WHAT IT INCLUDES. A FEATURE SAYS WHICH TIER IT NEEDS.**

The obvious design is an `entitlements: {feature_key: bool}` map on each tier.
It is also two places to answer one question — the tier says Pro has Veo, the
feature says Veo needs Starter, and the day they disagree there is no way to
tell which is the bug. So `features.min_tier` is the ONLY statement of what
unlocks what, and "everything in Pro" is DERIVED by asking every feature. The
pricing editor shows that derived list, read-only, beside the marketing copy —
so drift between the two is visible on screen rather than discovered by a
customer who paid for something they cannot use.

What a tier therefore holds: a price, a rank, some marketing copy, and limits.

MONEY
-----
⚠ **INTEGERS, IN MINOR UNITS (cents), ALWAYS.** `2800` is $28.00. No float ever
touches a price in this app. `BILLING_CURRENCY` says which currency those
integers are; it converts nothing.

⚠ **`rank` IS THE ORDER, NOT `price`.** A tier's position in the ladder has to
survive a sale — dropping Pro to $19 for a weekend must not silently reorder it
below Starter and change what every `min_tier` in the app means.

⚠ **ARCHIVE, NEVER DELETE.** A tier somebody is subscribed to has to keep
resolving, or their account cannot be priced or rendered. `archived` hides it
from the pricing page and leaves it working for the people already on it.

Backend follows the USER STORE (`API_USER_STORE`), like features, drafts and
events.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import config, users
from .auth import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

_lock = threading.Lock()

# The tier every account is on until something says otherwise. ⚠ IT MUST ALWAYS
# EXIST AND MUST NEVER BE ARCHIVED — `tier_of` falls back to it for every
# account that has no tier field, which today is all of them.
DEFAULT_TIER = "trial"


# ===========================================================================
# The catalogue
# ===========================================================================
# ⚠ SEEDED FROM `client/src/components/PricingModal.jsx`'s `PLANS` CONSTANT —
# the same names, blurbs, prices, badges and bullets that have been on the
# pricing page all along, moved into the database without changing a number.
# This phase makes the prices EDITABLE; it does not re-price anything.
#
# The modal's dollars become minor units here ($28 → 2800). `was` becomes
# `compare_at`, which is the struck-through price beside a sale.
_CATALOG = [
    {
        "id": "trial",
        "name": "Trial",
        "blurb": "Explore the studio for free — bring a script to life in minutes.",
        "rank": 0,
        "monthly": 0,
        "yearly": 0,
        "compare_at": 0,
        "badge": "",
        "highlight": False,
        "bullets": [
            {"text": "2 projects", "ok": True},
            {"text": "9 shots per project", "ok": True},
            {"text": "50 image generations", "ok": True},
            {"text": "Export with watermark", "ok": True},
            {"text": "No commercial use", "ok": False},
        ],
        "limits": {"projects": 2, "shots_per_project": 9, "image_generations": 50,
                   "watermark": True, "commercial_use": False},
    },
    {
        "id": "starter",
        "name": "Starter",
        "blurb": "For creators. Ideal for short clips, commercials or short films.",
        "rank": 10,
        "monthly": 2800,
        "yearly": 2100,
        "compare_at": 6900,
        "badge": "Most Popular",
        "highlight": True,
        "bullets": [
            {"text": "5 projects per month", "ok": True, "strong": True},
            {"text": "Stories up to 10 pages", "ok": True},
            {"text": "Unlimited image generations", "ok": True},
            {"text": "Commercial use", "ok": True},
            {"text": "Export to various formats", "ok": True},
        ],
        "limits": {"projects": 5, "story_pages": 10, "image_generations": None,
                   "watermark": False, "commercial_use": True},
    },
    {
        "id": "pro",
        "name": "Pro Unlimited",
        "blurb": "For professionals and agencies — ad campaigns and longer films.",
        "rank": 20,
        "monthly": 6900,
        "yearly": 5300,
        "compare_at": 14900,
        "badge": "Best Value",
        "highlight": False,
        "bullets": [
            {"text": "Unlimited projects", "ok": True, "strong": True},
            {"text": "Stories up to 30 pages", "ok": True, "strong": True},
            {"text": "Unlimited image generations", "ok": True},
            {"text": "Commercial use", "ok": True},
            {"text": "Export to various formats", "ok": True},
        ],
        "limits": {"projects": None, "story_pages": 30, "image_generations": None,
                   "watermark": False, "commercial_use": True},
    },
    {
        "id": "production",
        "name": "Production Unlimited",
        "blurb": "For film pros — features or series, regardless of screenplay length.",
        "rank": 30,
        "monthly": 17900,
        "yearly": 13500,
        "compare_at": 39900,
        "badge": "",
        "highlight": False,
        "bullets": [
            {"text": "Unlimited projects", "ok": True},
            {"text": "Unlimited story length", "ok": True, "strong": True},
            {"text": "Unlimited image generations", "ok": True},
            {"text": "Commercial use", "ok": True},
            {"text": "Export to various formats", "ok": True},
        ],
        "limits": {"projects": None, "story_pages": None, "image_generations": None,
                   "watermark": False, "commercial_use": True},
    },
]

_DEFAULTS = {
    "badge": "",
    "highlight": False,
    "compare_at": 0,
    "archived": False,
    "visible": True,
    "bullets": [],
    # ⚠ NOT ENFORCED YET. Phase 5 wires these to `usage_counters` and starts
    # refusing work. Until then they are what the pricing card SAYS, and the
    # editor labels them as such — a limit nothing checks is marketing copy,
    # and calling it anything else invites somebody to rely on it.
    "limits": {},
    "updated_at": None,
    "updated_by": None,
}

# What an administrator may write. `id` and `rank` are identity and ladder
# position — `rank` is editable, `id` is not: it is the value stored on every
# subscriber's account.
EDITABLE = frozenset({
    "name", "blurb", "rank", "monthly", "yearly", "compare_at",
    "badge", "highlight", "bullets", "limits", "visible", "archived",
})


def _catalog() -> dict:
    return {t["id"]: {**_DEFAULTS, **t} for t in _CATALOG}


# ===========================================================================
# Storage — the same two-backend shape as features.py
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_TIERS_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local tier store at %s is unreadable — using defaults.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


_collection = None


def get_collection():
    global _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        from .mongo import get_db

        col = get_db()[config.TIERS_COLLECTION]
        try:
            col.create_index("id", unique=True)
        except Exception as e:  # noqa: BLE001 — an index is an optimisation
            logger.warning("Could not index tiers (%s).", e)
        _collection = col
        logger.info(
            "MongoDB tier store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.TIERS_COLLECTION,
        )
        return _collection


_cache: dict | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def all_tiers(fresh: bool = False) -> dict:
    """`{id: tier}` with the catalogue merged in. Never raises; fails open.

    Same contract as `features.all_features` — an unreachable store serves the
    last good answer, then the built-in catalogue. A pricing page that renders
    nothing is a shop with the lights off.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            if _cache is not None and now - _cache_at < config.TIER_CACHE_TTL_S:
                return _cache

    merged = _catalog()
    try:
        for tid, doc in _read_stored().items():
            base = merged.get(tid) or {**_DEFAULTS, "id": tid, "name": tid, "rank": 999}
            merged[tid] = {**base, **{k: v for k, v in doc.items() if v is not None}}
    except Exception as e:  # noqa: BLE001 — fail open
        with _cache_lock:
            if _cache is not None:
                logger.warning("Tier store unreachable (%s) — serving last known good.", e)
                return _cache
        logger.warning("Tier store unreachable (%s) — serving built-in defaults.", e)
        return merged

    with _cache_lock:
        _cache = merged
        _cache_at = now
    return merged


def _read_stored() -> dict:
    if _use_local():
        with _lock:
            return _local_load()
    return {d["id"]: {k: v for k, v in d.items() if k != "_id"} for d in get_collection().find()}


def save_tier(tier_id: str, fields: dict, actor: str = "") -> dict:
    """Write an administrator's changes onto one tier. Returns the merged row."""
    clean = {k: v for k, v in (fields or {}).items() if k in EDITABLE}
    for money in ("monthly", "yearly", "compare_at"):
        if money in clean:
            clean[money] = _minor_units(clean[money], money)
    if clean.get("archived") and tier_id == DEFAULT_TIER:
        # ⚠ EVERY ACCOUNT WITHOUT A TIER FALLS BACK TO THIS ONE. Archiving it
        # would leave most of the userbase pointing at a tier the pricing page
        # refuses to show and `tier_of` cannot rank.
        raise ValueError(
            f"'{DEFAULT_TIER}' is the tier every new account starts on and can't "
            f"be archived. Change what it includes instead."
        )
    clean["id"] = tier_id
    clean["updated_at"] = datetime.now(timezone.utc).isoformat()
    clean["updated_by"] = (actor or "").strip().lower() or None

    if _use_local():
        with _lock:
            data = _local_load()
            data[tier_id] = {**data.get(tier_id, {}), **clean}
            _local_save(data)
    else:
        get_collection().update_one({"id": tier_id}, {"$set": clean}, upsert=True)

    _bump()
    return all_tiers(fresh=True).get(tier_id, {})


def _minor_units(value, field: str) -> int:
    """Coerce a price to a non-negative integer number of minor units.

    ⚠ IT REFUSES A FLOAT RATHER THAN ROUNDING ONE. `28.5` arriving here means the
    caller is thinking in dollars and something upstream has forgotten to
    multiply — silently storing 28 cents would be a hundredfold pricing error
    that nobody notices until an invoice.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{field} must be a whole number of minor units (cents), not {value!r}. "
            f"$28.00 is 2800."
        )
    if value < 0:
        raise ValueError(f"{field} can't be negative.")
    return value


# ===========================================================================
# Who is on what
# ===========================================================================
def tier_of(user_or_email) -> str:
    """The tier id for an account. Never raises; unknown → the default tier.

    Accepts a user document (when the caller already has one — the resolver
    does) or an address. ⚠ AN UNRECOGNISED OR ARCHIVED-AWAY TIER FALLS BACK TO
    THE DEFAULT rather than to nothing: a stored value that no longer names a
    real tier must not leave an account unrankable.

    ⚠ **EXPIRY IS CHECKED HERE, LAZILY, BECAUSE THERE IS NO SCHEDULER IN THIS
    APP.** A paid tier carries `tier_expires_at` on the user document; when that
    moment passes, this returns the default tier — no cron, no job, nothing to
    forget to run. The field is on the document the caller has already read, so
    it costs no extra query, and access ends on the minute rather than whenever
    somebody remembers to sweep.

    ⚠ AN ABSENT `tier_expires_at` MEANS "DOES NOT EXPIRE", not "expired". A tier
    granted by hand with no end date has to keep working.
    """
    try:
        user = (
            user_or_email
            if isinstance(user_or_email, dict)
            else (users.get_user_by_email(user_or_email) or {})
        )
        tid = (user.get("tier") or "").strip().lower()
        if tid not in all_tiers():
            return DEFAULT_TIER
        expires = user.get("tier_expires_at")
        if expires and expires < datetime.now(timezone.utc).isoformat():
            return DEFAULT_TIER
        return tid
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not resolve tier for %r: %s", user_or_email, e)
        return DEFAULT_TIER


def rank_of(tier_id: str) -> int:
    """Where a tier sits on the ladder. Unknown → the default tier's rank."""
    tiers = all_tiers()
    tier = tiers.get((tier_id or "").strip().lower())
    if tier is None:
        tier = tiers.get(DEFAULT_TIER) or {}
    return int(tier.get("rank") or 0)


def meets(tier_id: str, min_tier: str | None) -> bool:
    """Whether `tier_id` is at least `min_tier`. No requirement → always true."""
    if not min_tier:
        return True
    return rank_of(tier_id) >= rank_of(min_tier)


def includes(tier_id: str) -> list[str]:
    """The feature keys this tier unlocks — DERIVED, never stored.

    ⚠ THIS IS THE WHOLE POINT OF THE DESIGN (see the module docstring). "What is
    in Pro" is a question about the FEATURES, and asking them is what makes it
    impossible for the tier and the feature to disagree about the answer.
    """
    from . import features  # local: features imports nothing from here

    return sorted(
        key
        for key, feat in features.all_features().items()
        if meets(tier_id, feat.get("min_tier"))
    )


# ===========================================================================
# Routes
# ===========================================================================
@router.get("/tiers")
def list_tiers() -> dict:
    """The price list. PUBLIC — no token needed.

    ⚠ DELIBERATELY UNAUTHENTICATED. A price list is public by nature; requiring
    a session to read one would mean a landing page could not show prices
    without inventing a second, parallel copy of them, which is exactly the
    duplication this whole phase removes. Nothing here is per-account: WHICH
    tier the caller is on comes from `/auth/me/entitlements`.

    ⚠ THE SALE IS APPLIED HERE, ON THE SERVER, NOT IN THE BROWSER. The discount
    arithmetic decides what somebody is charged; a copy of it in JavaScript is a
    second answer that can disagree with the first, and the one people would
    believe is the one on screen.

    ⚠ `offers` IS THE OTHER HALF OF THE ANSWER, AND WITHOUT IT A COUPON IS
    INVISIBLE. A sale reaches a customer as a changed number on a card, so it
    needs no announcement. A COUPON CHANGES NOTHING UNTIL IT IS TYPED — so a
    coupon nobody is shown is a discount that exists only in the admin panel.
    This list is what the pricing page prints as offer cards; it carries only the
    promoted ones (`offers.promoted_offers`), so a code emailed to one customer
    stays private.
    """
    from . import offers

    out = []
    for t in sorted(all_tiers().values(), key=lambda t: t["rank"]):
        if t.get("archived") or not t.get("visible", True):
            continue
        out.append(_public(t, with_sale=True))

    promos = offers.promoted_offers()

    # The banner above the cards — the first live offer that bothered to write
    # one. ⚠ SALES ARE ASKED FIRST, THEN PROMOTED COUPONS. A sale is already
    # changing every price on the screen, so its own sentence is the one that
    # explains what the customer is looking at; a coupon's banner is an extra.
    banner = ""
    for want_sale in (True, False):
        for o in offers.all_offers():
            if bool(o.get("code")) is want_sale:
                continue
            if not o.get("banner") or not offers.is_live(o):
                continue
            if not want_sale and not offers.is_promoted(o):
                continue
            banner = o["banner"]
            break
        if banner:
            break

    return {
        "tiers": out,
        "currency": config.BILLING_CURRENCY,
        "banner": banner,
        "offers": promos,
    }


def _public(tier: dict, with_sale: bool = False) -> dict:
    """One tier as the pricing page needs it. `limits` is included because the
    card quotes them; `updated_by` is not — that is an internal fact.

    ⚠ WHEN A SALE APPLIES, IT DRIVES `compare_at` — IT DOES NOT INVENT A SECOND
    OLD PRICE. The card has always drawn one struck-through number from that
    field; a sale makes the tier's NORMAL price that number and puts the
    discounted price in its place. The tier's own `compare_at` is the evergreen
    anchor used when nothing is on sale, and the two are never added together.
    """
    from . import offers

    monthly = tier.get("monthly", 0)
    yearly = tier.get("yearly", 0)
    compare = tier.get("compare_at", 0)
    sale_label = ""

    if with_sale:
        sale_m = offers.live_sale(tier["id"], "monthly")
        sale_y = offers.live_sale(tier["id"], "yearly")
        if sale_m or sale_y:
            # Whichever anchor is higher is the honest one: a tier with a
            # standing "was $69" must not appear to get CHEAPER to compare
            # against just because a sale started.
            compare = max(compare, monthly)
            sale_label = offers.summary(sale_m or sale_y)
        if sale_m:
            monthly = offers.apply_to_price(monthly, sale_m)
        if sale_y:
            yearly = offers.apply_to_price(yearly, sale_y)

    return {
        "id": tier["id"],
        "name": tier.get("name") or tier["id"],
        "blurb": tier.get("blurb") or "",
        "rank": tier.get("rank", 0),
        "monthly": monthly,
        "yearly": yearly,
        "compare_at": compare,
        "sale": sale_label,
        "badge": tier.get("badge") or "",
        "highlight": bool(tier.get("highlight")),
        "bullets": tier.get("bullets") or [],
        "limits": tier.get("limits") or {},
    }


class CouponRequest(BaseModel):
    code: str = Field(..., max_length=40)
    tier: str = Field(..., max_length=40)
    period: str = Field("monthly", pattern="^(monthly|yearly)$")


@router.post("/coupon")
def check_coupon(
    body: CouponRequest, current: CurrentUser = Depends(get_current_user)
) -> dict:
    """What would this code do to this tier? Signed in only.

    ⚠ IT NEVER SAYS WHY A CODE FAILED BEYOND "not valid". Expired, fully
    redeemed, wrong tier and never-existed all answer the same — otherwise this
    route is an oracle for enumerating which codes exist and when a sale ends.

    ⚠ AND IT REDEEMS NOTHING. Checking a code is not using one; the count moves
    when a subscription is actually recorded against it.
    """
    from . import offers

    tier = all_tiers().get((body.tier or "").strip().lower())
    if tier is None:
        raise HTTPException(status_code=404, detail="No such plan.")

    offer = offers.by_code(body.code)
    if (
        not offer
        or not offers.is_live(offer)
        or not offers.applies_to(offer, tier["id"], body.period)
    ):
        return {"valid": False, "detail": "That code isn't valid."}

    price = tier.get("yearly" if body.period == "yearly" else "monthly", 0)
    off = offers.discount_on(offer, price)
    return {
        "valid": True,
        "code": offer["code"],
        "label": offer.get("label") or offers.summary(offer),
        "discount": off,
        "was": price,
        "now": max(0, price - off),
        "currency": config.BILLING_CURRENCY,
    }


@router.get("/me")
def my_tier(current: CurrentUser = Depends(get_current_user)) -> dict:
    """Which tier the caller is on, what it unlocks, and what they've used.

    ⚠ A CUSTOMER CAN SEE THEIR OWN COUNTERS. A limit somebody cannot check is a
    limit they discover by being refused mid-task, which is the worst possible
    moment to learn about it.
    """
    from . import usage

    tid = tier_of(current.email)
    tier = all_tiers().get(tid) or {}
    return {
        "tier": tid,
        "name": tier.get("name") or tid,
        "rank": tier.get("rank", 0),
        "limits": tier.get("limits") or {},
        "includes": includes(tid),
        "usage": usage.summary(current.email),
    }
