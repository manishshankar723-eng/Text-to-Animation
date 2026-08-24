"""
subscriptions.py — who is paying for what, and what they agreed to pay.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
⚠ **THE PRICE IS COPIED ONTO THE SUBSCRIPTION AT PURCHASE TIME, NEVER READ BACK
OFF THE TIER.** A tier's `monthly` is what a NEW customer would be quoted today.
A subscriber pays what they agreed to. Without this, editing a price in the admin
panel silently re-prices every existing customer — which is the single most
expensive bug this whole feature could have, because nobody notices until the
invoices go out.

The same applies to the offer: `offer_code` and `discount` are stamped onto the
record. Ending a sale must not retroactively un-discount the people who bought
during it.

WHAT THIS IS AND IS NOT
-----------------------
⚠ **NOTHING HERE TAKES MONEY.** `source: "manual"` means an administrator typed
it in after a bank transfer or an invoice — which is genuinely how early sales
close, and it makes "who purchased" answerable weeks before a payment provider
is integrated. Phase 6 adds webhooks that write the SAME records with
`source: "razorpay"` and a `provider_ref`, and no screen has to change.

⚠ **EXPIRY IS LAZY, NOT SCHEDULED.** There is no cron in this app. A
subscription carries `current_period_end`, and `users.tier_expires_at` is
stamped alongside it — `billing.tier_of` compares that to the clock on every
read, so access ends on time without anything having to run. See the note there.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"
STATUSES = (STATUS_ACTIVE, STATUS_CANCELLED)

SOURCE_MANUAL = "manual"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# ===========================================================================
# Storage
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_SUBSCRIPTIONS_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local subscription store at %s is unreadable.", path)
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

        col = get_db()[config.SUBSCRIPTIONS_COLLECTION]
        try:
            col.create_index("id", unique=True)
            col.create_index([("email", 1), ("started_at", -1)])
            col.create_index([("status", 1), ("started_at", -1)])
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not index subscriptions (%s).", e)
        _collection = col
        logger.info(
            "MongoDB subscription store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.SUBSCRIPTIONS_COLLECTION,
        )
        return _collection


# ===========================================================================
# Writing
# ===========================================================================
def record(
    email: str,
    tier_id: str,
    period: str,
    *,
    amount: int,
    currency: str,
    months: int = 0,
    offer_code: str | None = None,
    offer_id: str | None = None,
    discount: int = 0,
    note: str = "",
    source: str = SOURCE_MANUAL,
    provider_ref: str = "",
    actor: str = "",
) -> dict:
    """Write one subscription. Returns the stored record.

    ⚠ `amount` IS WHAT THEY ACTUALLY AGREED TO PAY, per period, in minor units,
    AFTER any discount — passed in by the caller rather than looked up here, so
    that this record can never drift from the number that was quoted. `discount`
    is kept beside it only so the panel can show what was taken off.
    """
    period_months = months or (12 if period == "yearly" else 1)
    started = _now()
    row = {
        "id": uuid.uuid4().hex[:12],
        "email": (email or "").strip().lower(),
        "tier_id": (tier_id or "").strip().lower(),
        "period": period,
        # --- frozen at purchase time; see the module docstring ---
        "amount": int(amount),
        "currency": currency,
        "offer_code": offer_code,
        "offer_id": offer_id,
        "discount": int(discount or 0),
        # --- lifecycle ---
        "status": STATUS_ACTIVE,
        "started_at": started.isoformat(),
        "current_period_end": (started + timedelta(days=30 * period_months)).isoformat(),
        "cancelled_at": None,
        # --- provenance ---
        "source": source,
        "provider_ref": provider_ref or "",
        "note": (note or "").strip(),
        "created_by": (actor or "").strip().lower() or None,
    }

    if _use_local():
        with _lock:
            data = _local_load()
            data[row["id"]] = row
            _local_save(data)
    else:
        get_collection().insert_one(dict(row))
    return row


def cancel(sub_id: str, actor: str = "") -> dict | None:
    """End a subscription now. Returns the updated record, or None if unknown."""
    fields = {
        "status": STATUS_CANCELLED,
        "cancelled_at": _now_iso(),
        "cancelled_by": (actor or "").strip().lower() or None,
    }
    if _use_local():
        with _lock:
            data = _local_load()
            if sub_id not in data:
                return None
            data[sub_id].update(fields)
            _local_save(data)
            return data[sub_id]
    if get_collection().update_one({"id": sub_id}, {"$set": fields}).matched_count == 0:
        return None
    return get(sub_id)


# ===========================================================================
# Reading
# ===========================================================================
def get(sub_id: str) -> dict | None:
    if _use_local():
        with _lock:
            return _local_load().get(sub_id)
    doc = get_collection().find_one({"id": sub_id}, {"_id": 0})
    return doc


def list_subscriptions(
    limit: int = 50,
    skip: int = 0,
    *,
    email: str | None = None,
    status: str | None = None,
    tier_id: str | None = None,
) -> list[dict]:
    """Subscriptions, newest first."""
    limit = max(1, min(limit, config.ADMIN_MAX_PAGE))
    try:
        if _use_local():
            with _lock:
                rows = list(_local_load().values())
            rows = [r for r in rows if _matches(r, email, status, tier_id)]
            rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)
            return rows[skip : skip + limit]

        query = {}
        if email:
            query["email"] = email.strip().lower()
        if status:
            query["status"] = status
        if tier_id:
            query["tier_id"] = tier_id
        cursor = (
            get_collection()
            .find(query, {"_id": 0})
            .sort("started_at", -1)
            .skip(max(0, skip))
            .limit(limit)
        )
        return list(cursor)
    except Exception as e:  # noqa: BLE001 — a panel screen, not a transaction
        logger.warning("Could not list subscriptions: %s", e)
        return []


def _matches(row: dict, email, status, tier_id) -> bool:
    if email and row.get("email") != email.strip().lower():
        return False
    if status and row.get("status") != status:
        return False
    if tier_id and row.get("tier_id") != tier_id:
        return False
    return True


def active_for(email: str) -> dict | None:
    """The newest ACTIVE subscription for an account, if any."""
    rows = list_subscriptions(1, email=email, status=STATUS_ACTIVE)
    return rows[0] if rows else None


def count(status: str | None = None) -> int:
    try:
        if _use_local():
            with _lock:
                rows = list(_local_load().values())
            return sum(1 for r in rows if not status or r.get("status") == status)
        return get_collection().count_documents({"status": status} if status else {})
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not count subscriptions: %s", e)
        return 0


def recurring_revenue() -> dict:
    """Active subscriptions, normalised to a monthly figure.

    ⚠ IT IS "RECORDED", NOT "EARNED", AND EVERY SURFACE MUST SAY SO. Nothing in
    this app has taken a payment — these are the amounts an administrator typed
    in. Calling it MRR without that qualification turns a bookkeeping note into a
    revenue claim.

    ⚠ A YEARLY SUBSCRIPTION'S `amount` IS ALREADY PER MONTH. That is how the
    pricing page quotes it ("$21 monthly, billed yearly"), so it is what gets
    stored — no division here, and none wanted.
    """
    monthly = 0
    currency = config.BILLING_CURRENCY
    rows: list[dict] = []
    try:
        if _use_local():
            with _lock:
                rows = [
                    r for r in _local_load().values() if r.get("status") == STATUS_ACTIVE
                ]
        else:
            rows = list(get_collection().find({"status": STATUS_ACTIVE}, {"_id": 0}))
        for row in rows:
            monthly += int(row.get("amount") or 0)
            currency = row.get("currency") or currency
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not total subscriptions: %s", e)
    return {"monthly": monthly, "currency": currency, "count": len(rows)}
