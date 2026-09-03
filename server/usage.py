"""
usage.py — what an account has actually used this month, and what it may.

⚠ **THIS IS A SINK, NOT A SECOND COUNTER.** `ai_usage.Usage` already counts
tokens correctly — additively, with failed retries included and thinking tokens
broken out. Nothing here re-counts anything it can be handed; `record_tokens`
takes a `Usage` and adds it to a per-account, per-month row. A second way of
counting the same thing is two numbers that disagree, and no way to tell which
one is the bill.

TWO KINDS OF LIMIT, AND THEY ARE NOT THE SAME SHAPE
---------------------------------------------------
⚠ **COUNTERS** accumulate over a period and are compared to a ceiling:
`projects`, `image_generations`. They need this module's storage.

⚠ **PER-REQUEST CAPS** are a property of one request and never accumulate:
`shots_per_project`, `story_pages`. They need no storage at all — the request
already carries the number. Storing them would invent a total nobody asked for.

Conflating the two is how a "9 shots per project" limit turns into "9 shots ever".

WHAT IS DELIBERATELY NOT ENFORCED
---------------------------------
⚠ `watermark` and `commercial_use` are on every tier and are NOT read by any
guard. They are not access rules — a watermark is a change to what the exporter
DRAWS, and commercial use is a licence term that no amount of code can enforce.
Pretending otherwise by refusing an export would be both wrong and useless. They
stay as what the pricing card says, and the panel labels them as such.

THE PERIOD
----------
Calendar months in UTC, `"2026-08"`. Not rolling 30-day windows: "5 projects per
month" is what the pricing page says, and a customer who can see their own
counter needs to be able to predict when it resets. A rolling window is
unpredictable by construction.

Backend follows the USER STORE (`API_USER_STORE`), like every other panel store.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException

from . import config
from .auth import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# The counters this module keeps. ⚠ THE NAMES MATCH THE TIER'S `limits` KEYS
# EXACTLY, because the pricing card quotes those keys and enforcement that used
# different names would describe a different product from the one being sold.

# ⚠ `chat_turns` IS A COUNTER, NOT A CAP: it accumulates over the billing period
# like projects do, because what is being sold is "N messages a month". A cap
# would be "N messages per project", which is not a limit anybody asked for and
# would reset every time somebody opened a new film.
COUNTERS = ("projects", "image_generations", "chat_turns")

# Per-request caps — checked against one request, never accumulated. Same rule
# about the names.
CAPS = ("shots_per_project", "story_pages")

# ⚠ A "PAGE" NEEDS A NUMBER, AND THIS IS THE INDUSTRY ONE. Screenplay convention
# is roughly one minute of screen time per page, about 55 lines, which lands
# near 1,500 characters in a fixed-width layout. It is approximate by nature —
# so the limit is applied generously (see `cap_exceeded`) and the message says
# how long the script actually was, rather than arguing about the definition.
PAGE_CHARS = 1500


def period_now() -> str:
    """The current billing period, `"YYYY-MM"` in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(email: str, period: str) -> str:
    return f"{(email or '').strip().lower()}|{period}"


# ===========================================================================
# Storage
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_USAGE_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local usage store at %s is unreadable — treating as empty.", path)
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

        col = get_db()[config.USAGE_COLLECTION]
        try:
            col.create_index([("email", 1), ("period", 1)], unique=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not index usage counters (%s).", e)
        _collection = col
        logger.info(
            "MongoDB usage store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.USAGE_COLLECTION,
        )
        return _collection


# ===========================================================================
# Reading and writing
# ===========================================================================
def counters(email: str, period: str | None = None) -> dict:
    """This account's counters for the period. Missing → all zero.

    ⚠ NEVER RAISES, AND A FAILURE READS AS ZERO. That is the deliberately
    generous direction: an unreachable counter store must not lock a paying
    customer out of work they are entitled to. It can undercount during an
    outage; it cannot refuse.
    """
    period = period or period_now()
    blank = {"email": (email or "").strip().lower(), "period": period,
             **{c: 0 for c in COUNTERS}, "text_tokens": 0, "cost_usd_est": 0.0}
    try:
        if _use_local():
            with _lock:
                row = _local_load().get(_key(email, period))
        else:
            row = get_collection().find_one(
                {"email": blank["email"], "period": period}, {"_id": 0}
            )
        return {**blank, **(row or {})}
    except Exception as e:  # noqa: BLE001 — see the docstring
        logger.warning("Could not read usage for %s (%s): %s", email, period, e)
        return blank


def increment(email: str, field: str, by: int = 1) -> None:
    """Add to one counter. NEVER RAISES — the work has already happened.

    ⚠ COUNTED AFTER THE FACT, ON PURPOSE. The guard runs before the work and
    this runs after it, so a request that is refused is never counted and a
    request that fails halfway is. Counting first would charge people for work
    they did not get.
    """
    if by <= 0:
        return
    key = (email or "").strip().lower()
    period = period_now()
    try:
        if _use_local():
            with _lock:
                data = _local_load()
                row = data.setdefault(
                    _key(key, period), {"email": key, "period": period}
                )
                row[field] = int(row.get(field) or 0) + by
                row["updated_at"] = _now_iso()
                _local_save(data)
            return
        get_collection().update_one(
            {"email": key, "period": period},
            {"$inc": {field: by}, "$set": {"updated_at": _now_iso()}},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not count %s for %s: %s", field, email, e)


def record_tokens(email: str, usage) -> None:
    """Fold an `ai_usage.Usage` into this account's month. NEVER RAISES.

    ⚠ THE SINK THIS MODULE EXISTS FOR. `Usage` already counts additively, with
    retries included; nothing is re-derived here. The dollar figure is ADVISORY
    for the same reasons `ai_usage` gives — and `cost_usd()` returning None
    (unpriced or mixed models) adds nothing rather than guessing a zero.
    """
    try:
        total = getattr(usage, "total", 0) or 0
        if total:
            increment(email, "text_tokens", total)
        cost = usage.cost_usd() if hasattr(usage, "cost_usd") else None
        if cost:
            key = (email or "").strip().lower()
            period = period_now()
            if _use_local():
                with _lock:
                    data = _local_load()
                    row = data.setdefault(_key(key, period), {"email": key, "period": period})
                    row["cost_usd_est"] = round(float(row.get("cost_usd_est") or 0) + cost, 6)
                    _local_save(data)
            else:
                get_collection().update_one(
                    {"email": key, "period": period},
                    {"$inc": {"cost_usd_est": cost}},
                    upsert=True,
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not record token usage for %s: %s", email, e)


# ===========================================================================
# Limits
# ===========================================================================
def limits_for(email: str) -> dict:
    """The tier limits that apply to this account. Never raises."""
    from . import billing

    try:
        tier = billing.all_tiers().get(billing.tier_of(email)) or {}
        return tier.get("limits") or {}
    except Exception as e:  # noqa: BLE001 — no limits is the generous answer
        logger.warning("Could not read limits for %s: %s", email, e)
        return {}


def limit_of(email: str, field: str):
    """The ceiling on one field, or None for unlimited.

    ⚠ MISSING AND `None` BOTH MEAN UNLIMITED, and that matters: a tier that
    simply does not mention `image_generations` must not be read as allowing
    zero of them. Only a number is a limit.
    """
    value = limits_for(email).get(field)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check(email: str, field: str, needed: int = 1) -> tuple[bool, int, int | None]:
    """`(allowed, used, limit)` for a counter. `limit=None` is unlimited."""
    limit = limit_of(email, field)
    used = int(counters(email).get(field) or 0)
    if limit is None:
        return True, used, None
    return (used + needed) <= limit, used, limit


def cap_exceeded(email: str, field: str, value: int) -> int | None:
    """The cap this request breaks, or None if it is within it.

    ⚠ ADMINS ARE NEVER CAPPED — the same rule the feature gates follow. You
    cannot test what you are selling if the panel's own account is throttled.
    """
    from . import users

    if users.is_admin(email):
        return None
    cap = limit_of(email, field)
    if cap is None or value <= cap:
        return None
    return cap


def require_quota(field: str, cost: int = 1):
    """A FastAPI dependency that refuses when this account is over its limit.

    ⚠ IT SITS BESIDE `require_feature`, ON THE SAME ROUTES, AND FOR THE SAME
    REASON: a limit checked AFTER the work has been done is a limit that bills
    the customer for the call telling them they are over. Both run before the
    endpoint body does anything.

    ⚠ ADMINS ARE NEVER REFUSED, same as the feature gates.
    """

    def guard(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        from . import users

        if users.is_admin(current.email):
            return current
        allowed, used, limit = check(current.email, field, cost)
        if not allowed:
            raise HTTPException(
                status_code=402,  # Payment Required — the honest code here
                detail=(
                    f"You've used {used} of your {limit} "
                    f"{field.replace('_', ' ')} this month. "
                    f"Upgrade your plan for more, or wait until next month."
                ),
            )
        return current

    return guard


def summary(email: str) -> dict:
    """Everything the panel (and a future usage page) needs about one account."""
    from . import billing

    period = period_now()
    rows = counters(email, period)
    limits = limits_for(email)
    return {
        "period": period,
        "tier": billing.tier_of(email),
        "counters": {c: int(rows.get(c) or 0) for c in COUNTERS},
        "text_tokens": int(rows.get("text_tokens") or 0),
        # ⚠ ADVISORY, AND EVERY SURFACE MUST SAY SO — the same warning
        # `ai_usage` carries. Only Google bills, list prices drift, and a rolling
        # model alias can be re-priced under a fixed id.
        "cost_usd_est": round(float(rows.get("cost_usd_est") or 0), 4),
        "limits": {k: limits.get(k) for k in (*COUNTERS, *CAPS)},
        # Stated separately so no caller mistakes them for something enforced.
        "not_enforced": {
            k: limits.get(k) for k in ("watermark", "commercial_use") if k in limits
        },
    }
