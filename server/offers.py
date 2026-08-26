"""
offers.py — discounts: a site-wide sale, or a coupon somebody types.

⚠ ONE ROW SHAPE, TWO DIFFERENT THINGS, AND THE DIFFERENCE IS ONE FIELD.

    code = None   → a SALE. Applies to everyone automatically, changes the price
                    on the pricing page, and can put a banner above it.
    code = "..."  → a COUPON. Applies to nobody until it is typed, and is
                    counted when it is redeemed.

They share a collection because they are the same arithmetic over the same
fields and an admin wants one list of "what is currently discounted". They get
separate halves of the admin screen because nobody thinks about them together.

⚠ **A SALE DRIVES `compare_at`; IT DOES NOT INVENT A SECOND OLD PRICE.** The
pricing card has always drawn a struck-through price from `tier.compare_at`. When
a sale is live, the tier's NORMAL price becomes that struck-through number and
the discounted price takes its place — so the card keeps one idea of what the old
price was. The tier's own `compare_at` is the evergreen anchor used when no sale
is running; it is not added on top.

⚠ **DISCOUNTS ARE COMPUTED IN MINOR UNITS AND ROUNDED ONCE, DOWNWARD.** A
percentage of an integer number of cents is not an integer. Rounding down means
the customer is never charged a cent more than the sign said, which is the only
direction that is safe to be wrong in.

⚠ **`promoted` IS WHETHER A CUSTOMER IS *TOLD* ABOUT IT, AND IT IS A SEPARATE
QUESTION FROM WHETHER IT WORKS.** A coupon that is live but not promoted still
works when it is typed and appears nowhere — that is a code you email to one
customer. A coupon that is promoted is printed on the pricing page as an offer
card with its code on it, which is the only way somebody who was never emailed
can use it. ⚠ **AN ABSENT `promoted` READS AS TRUE** (`is_promoted`): every
offer predates the field, and an offer an administrator went to the trouble of
creating is one they meant people to see. Hiding one is a single click in the
panel; a discount nobody can find is the failure this flag exists to end.

⚠ **NOTHING HERE TAKES MONEY.** An offer changes a displayed price and is
recorded against a subscription when one is entered by hand. Charging a card is
Phase 6.

Backend follows the USER STORE (`API_USER_STORE`), like every other panel store.
"""

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

KIND_PERCENT = "percent"
KIND_AMOUNT = "amount"
KINDS = (KIND_PERCENT, KIND_AMOUNT)

PERIOD_MONTHLY = "monthly"
PERIOD_YEARLY = "yearly"
PERIOD_BOTH = "both"
PERIODS = (PERIOD_MONTHLY, PERIOD_YEARLY, PERIOD_BOTH)

# A coupon code people type. Upper-cased and stripped on the way in, so
# "launch50", " LAUNCH50 " and "Launch50" are one code — nobody types a coupon
# the way it was written down.
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,31}$")

EDITABLE = frozenset({
    "label", "kind", "value", "applies_to", "period",
    "starts_at", "ends_at", "active", "max_redemptions", "banner",
    "promoted",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def normalize_code(code: str | None) -> str | None:
    """`"  launch50 "` → `"LAUNCH50"`. Empty/None → None, meaning a sale."""
    clean = (code or "").strip().upper()
    return clean or None


# ===========================================================================
# Storage
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_OFFERS_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local offer store at %s is unreadable — treating as empty.", path)
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

        col = get_db()[config.OFFERS_COLLECTION]
        try:
            col.create_index("id", unique=True)
            # ⚠ SPARSE, and it has to be: every SALE has `code: null`, and a
            # plain unique index would let exactly one of them exist.
            col.create_index("code", unique=True, sparse=True)
        except Exception as e:  # noqa: BLE001 — indexes are an optimisation
            logger.warning("Could not index offers (%s).", e)
        _collection = col
        logger.info(
            "MongoDB offer store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.OFFERS_COLLECTION,
        )
        return _collection


_cache: list | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def all_offers(fresh: bool = False) -> list:
    """Every offer, newest first. Never raises.

    ⚠ FAILS **CLOSED**, AND IT IS THE ONE STORE IN THE PANEL THAT DOES. Features
    and tiers fail open because the alternative is a blank app — serving a stale
    price list beats serving none. An unreachable OFFER store is the opposite: an
    offer that cannot be read is a discount nobody is currently entitled to, and
    inventing one would give money away. No offers is the safe answer.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            if _cache is not None and now - _cache_at < config.OFFER_CACHE_TTL_S:
                return _cache
    try:
        if _use_local():
            with _lock:
                rows = list(_local_load().values())
        else:
            rows = [
                {k: v for k, v in d.items() if k != "_id"} for d in get_collection().find()
            ]
    except Exception as e:  # noqa: BLE001 — see the docstring: fail CLOSED
        logger.warning("Offer store unreachable (%s) — no discounts will apply.", e)
        return []

    rows.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    with _cache_lock:
        _cache = rows
        _cache_at = now
    return rows


def get_offer(offer_id: str) -> dict | None:
    return next((o for o in all_offers() if o.get("id") == offer_id), None)


def by_code(code: str) -> dict | None:
    """Find a coupon by the code somebody typed. Sales are never returned here."""
    wanted = normalize_code(code)
    if not wanted:
        return None
    return next((o for o in all_offers(fresh=True) if o.get("code") == wanted), None)


def create_offer(fields: dict, actor: str = "") -> dict:
    """Create a sale (no code) or a coupon (with one)."""
    clean = _clean(fields)
    code = normalize_code(fields.get("code"))
    if code:
        if not _CODE_RE.match(code):
            raise ValueError(
                "A code must be 2-32 characters: letters, digits, - and _ only."
            )
        if by_code(code):
            raise ValueError(f"The code {code} is already in use.")

    row = {
        "id": uuid.uuid4().hex[:12],
        "code": code,
        "created_at": _now_iso(),
        "created_by": (actor or "").strip().lower() or None,
        "redeemed": 0,
        **clean,
    }
    row.setdefault("active", True)
    row.setdefault("promoted", True)

    if _use_local():
        with _lock:
            data = _local_load()
            data[row["id"]] = row
            _local_save(data)
    else:
        get_collection().insert_one(dict(row))
    _bump()
    return row


def save_offer(offer_id: str, fields: dict, actor: str = "") -> dict:
    """Change an offer. ⚠ THE CODE IS NOT EDITABLE — see `EDITABLE`.

    A code that has been printed on a card or sent in an email is out in the
    world; renaming it would silently break every place it was written down.
    Deactivate it and make another.
    """
    clean = _clean(fields)
    clean["updated_at"] = _now_iso()
    clean["updated_by"] = (actor or "").strip().lower() or None

    if _use_local():
        with _lock:
            data = _local_load()
            if offer_id not in data:
                raise KeyError(offer_id)
            data[offer_id].update(clean)
            _local_save(data)
    else:
        if get_collection().update_one({"id": offer_id}, {"$set": clean}).matched_count == 0:
            raise KeyError(offer_id)
    _bump()
    return get_offer(offer_id) or {}


def redeem(offer_id: str) -> None:
    """Count one redemption. Never raises — the sale already happened."""
    try:
        if _use_local():
            with _lock:
                data = _local_load()
                if offer_id in data:
                    data[offer_id]["redeemed"] = int(data[offer_id].get("redeemed") or 0) + 1
                    _local_save(data)
        else:
            get_collection().update_one({"id": offer_id}, {"$inc": {"redeemed": 1}})
        _bump()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not count a redemption of %s: %s", offer_id, e)


def _clean(fields: dict) -> dict:
    out = {k: v for k, v in (fields or {}).items() if k in EDITABLE}
    if "kind" in out and out["kind"] not in KINDS:
        raise ValueError(f"Unknown discount kind: {out['kind']!r}")
    if "period" in out and out["period"] not in PERIODS:
        raise ValueError(f"Unknown period: {out['period']!r}")
    if "value" in out:
        value = out["value"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("A discount value must be a whole non-negative number.")
        if out.get("kind") == KIND_PERCENT and value > 100:
            raise ValueError("A percentage discount can't be more than 100.")
    if "applies_to" in out:
        out["applies_to"] = [str(t).strip().lower() for t in (out["applies_to"] or []) if t]
    if out.get("starts_at") and out.get("ends_at") and out["starts_at"] > out["ends_at"]:
        raise ValueError("The offer would end before it started.")
    return out


# ===========================================================================
# When an offer counts
# ===========================================================================
def is_live(offer: dict, at: str | None = None) -> bool:
    """Whether this offer is in force right now.

    ⚠ THREE SEPARATE REASONS AN OFFER MIGHT NOT APPLY, and they are deliberately
    all checked here rather than by each caller: switched off, outside its dates,
    or fully redeemed. A caller that forgets one gives away money.
    """
    if not offer or not offer.get("active", True):
        return False
    now = at or _now_iso()
    if offer.get("starts_at") and now < offer["starts_at"]:
        return False
    if offer.get("ends_at") and now > offer["ends_at"]:
        return False
    cap = offer.get("max_redemptions")
    if cap and int(offer.get("redeemed") or 0) >= int(cap):
        return False
    return True


def applies_to(offer: dict, tier_id: str, period: str) -> bool:
    """Whether this offer covers this tier on this billing period."""
    tiers = offer.get("applies_to") or []
    # ⚠ AN EMPTY LIST MEANS EVERY TIER, not "no tiers". "20% off" with nothing
    # ticked is what somebody means by a site-wide sale, and reading it the
    # other way would make such an offer silently do nothing.
    if tiers and tier_id not in tiers:
        return False
    want = offer.get("period") or PERIOD_BOTH
    return want in (PERIOD_BOTH, period)


def live_sale(tier_id: str, period: str) -> dict | None:
    """The automatic sale in force for this tier/period, if any.

    ⚠ ONE SALE, NOT A STACK. If two overlap, the DEEPEST discount wins rather
    than both applying — compounding two sales is never what was meant, and
    "whichever is better for the customer" is the only tie-break that cannot be
    accused of short-changing anybody.
    """
    candidates = [
        o
        for o in all_offers()
        if not o.get("code") and is_live(o) and applies_to(o, tier_id, period)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda o: discount_on(o, 100_000_00))


def discount_on(offer: dict, price_minor: int) -> int:
    """How much this offer takes off `price_minor`. Never more than the price.

    ⚠ ROUNDED DOWN, ONCE. A percentage of an integer number of cents is not an
    integer; rounding the DISCOUNT down means the customer never pays a cent more
    than the sign promised, which is the only direction it is safe to be wrong in.
    """
    if not offer or price_minor <= 0:
        return 0
    kind = offer.get("kind")
    value = int(offer.get("value") or 0)
    if kind == KIND_PERCENT:
        off = (price_minor * value) // 100
    elif kind == KIND_AMOUNT:
        off = value
    else:
        return 0
    return max(0, min(off, price_minor))


def apply_to_price(price_minor: int, offer: dict | None) -> int:
    """The price after this offer. Never negative."""
    return max(0, price_minor - discount_on(offer, price_minor))


# The symbol for the one currency this deployment prices in. ⚠ NOT A
# CONVERSION TABLE — `config.BILLING_CURRENCY` is the only currency any price is
# stored in, so this maps that one code to the glyph a person reads.
_SYMBOLS = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}


def summary(offer: dict) -> str:
    """"20% off" / "$5 off" — one phrase, for a banner or a table cell.

    ⚠ THE AMOUNT KIND CARRIES ITS SYMBOL. It used to render "5 off", which
    beside a percentage discount reads as five percent — a bare number next to
    a price is the one place ambiguity costs money.
    """
    if offer.get("kind") == KIND_PERCENT:
        return f"{offer.get('value', 0)}% off"
    major = (offer.get("value") or 0) / 100
    symbol = _SYMBOLS.get(config.BILLING_CURRENCY, "")
    return f"{symbol}{major:g} off" if symbol else f"{major:g} {config.BILLING_CURRENCY} off"


def is_promoted(offer: dict) -> bool:
    """Whether this offer is advertised on the pricing page.

    ⚠ ABSENT MEANS YES — see the module docstring. Only an explicit `False`
    hides an offer, so an administrator who never heard of this flag still gets
    the behaviour they expected when they created a discount.
    """
    return bool(offer.get("promoted", True))


def public_offer(offer: dict) -> dict:
    """One offer as the PRICING PAGE may see it. Public — no token needed.

    ⚠ THIS IS AN ALLOW-LIST, NOT A DELETE-LIST. The stored row carries
    `created_by`, `updated_by` and the raw redemption count; a public route that
    spread the row and popped three keys would leak the fourth one somebody adds
    later. Every field here was chosen to be on a poster.

    ⚠ `remaining` IS SENT, `redeemed` IS NOT. "34 left" is the scarcity a
    customer is entitled to; "1,266 people already used this" is a business
    metric, and the two are the same two numbers seen from different sides.
    """
    cap = offer.get("max_redemptions")
    remaining = max(0, int(cap) - int(offer.get("redeemed") or 0)) if cap else None
    return {
        "id": offer.get("id") or "",
        # A SALE HAS NO CODE and must not be given an empty box to type into.
        "code": offer.get("code") or "",
        "label": offer.get("label") or "",
        "summary": summary(offer),
        "kind": offer.get("kind") or KIND_PERCENT,
        "value": int(offer.get("value") or 0),
        "period": offer.get("period") or PERIOD_BOTH,
        "applies_to": list(offer.get("applies_to") or []),
        "ends_at": offer.get("ends_at") or "",
        "banner": offer.get("banner") or "",
        "is_sale": not offer.get("code"),
        "remaining": remaining,
    }


def promoted_offers() -> list[dict]:
    """The offers the pricing page should advertise, deepest discount first.

    ⚠ LIVE **AND** PROMOTED, BOTH CHECKED. `is_live` is "does this work";
    `is_promoted` is "do we say so". An expired offer still on the page is a
    promise the checkout would break.
    """
    rows = [o for o in all_offers() if is_live(o) and is_promoted(o)]
    # Deepest discount at the front: if two are on offer, the one worth reading
    # about is the bigger one, and a card order that depends on which was typed
    # first is not an order anybody chose.
    rows.sort(key=lambda o: discount_on(o, 100_000_00), reverse=True)
    return [public_offer(o) for o in rows]
