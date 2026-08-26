"""
features.py — what an account may SEE and USE, and the one place that decides it.

THE WHOLE POINT OF THIS FILE IS THAT THERE IS ONLY ONE OF IT. Before it, the
sidebar's workflow list was a hard-coded array in `Sidebar.jsx` and hiding
anything meant a code edit and a redeploy. The temptation now is three systems —
one for the nav, one for the pricing page, one for the expensive routes — which
is three places to disagree about whether a customer has Veo. Instead: ONE
resolver, and the sidebar, `/auth/me/entitlements`, every `require_feature`
guard and the admin panel are all just callers of it.

PRECEDENCE, HIGHEST WINS
------------------------
    1. status "hidden"          off for everyone — the kill switch
    2. a per-user override      the "give this one customer early access" lever
    3. rollout rule             all / admins / allow-list / percent
    4. min_tier                 visible, locked — the upsell
    5. status "soon"            visible, not usable — the roadmap teaser
    6. status "live"            on

⚠ TWO ANSWERS, NOT ONE: `visible` AND `on`. A "soon" workflow is drawn in the
sidebar with a badge and refuses to run; a hidden one is not drawn at all; one
above the caller's tier is drawn WITH A LOCK, because a feature nobody can see
is a feature nobody upgrades for. One boolean cannot say any of that, and
squashing them was how the old `status: "soon"` placeholder ended up navigating
to a blank page.

⚠ `min_tier` SITS AFTER THE ROLLOUT, NOT BEFORE IT. Staging and selling are
different questions: something still being rolled out to 10% of accounts is not
yet for sale at any price, so the rollout has to be able to say no first.

⚠ ADMINS BYPASS THE ROLLOUT GATES AND THE TIER GATE, BUT NOT THE KILL SWITCH. Somebody staging a
feature to an allow-list has to be able to look at it, so `admins`, `allowlist`
and `percent` all pass for an administrator. "hidden" does NOT — that is the
switch you throw when something is broken, and it has to mean everyone. An admin
who needs to see a hidden feature gives themselves an explicit override, which
is recorded against their account like any other.

⚠ IT FAILS OPEN, AND THAT IS DELIBERATE. If the database is unreachable this
serves the last good answer it had, and failing that the built-in catalogue
below — never an empty map. An empty map is every sidebar in the app going blank
at once, which is a worse outage than the one that caused it.

Backend follows the USER STORE (`API_USER_STORE`), like drafts and events.
"""

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from . import config, users
from .auth import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["features"])

_lock = threading.Lock()

STATUS_LIVE = "live"
STATUS_SOON = "soon"
STATUS_HIDDEN = "hidden"
STATUSES = (STATUS_LIVE, STATUS_SOON, STATUS_HIDDEN)

GROUP_WORKFLOW = "workflow"
GROUP_CAPABILITY = "capability"

ROLLOUT_ALL = "all"
ROLLOUT_ADMINS = "admins"
ROLLOUT_ALLOWLIST = "allowlist"
ROLLOUT_PERCENT = "percent"
ROLLOUT_MODES = (ROLLOUT_ALL, ROLLOUT_ADMINS, ROLLOUT_ALLOWLIST, ROLLOUT_PERCENT)


# ===========================================================================
# The catalogue
# ===========================================================================
# ⚠ THE WORKFLOW ENTRIES ARE A COPY OF `client/src/components/Sidebar.jsx`'s
# `WORKFLOWS` — SAME IDS, SAME LABELS, SAME ICONS, SAME ORDER. That is not
# duplication for its own sake: the array in the client is now the FALLBACK for
# when this endpoint cannot be reached, so the two have to start out identical
# or a database hiccup silently reorders somebody's sidebar.
#
# ⚠ AND THE ORDER IS THE OWNER'S CHOICE, deliberately not pipeline order — the
# same warning that array carries. Don't "fix" it here either. It is editable
# from the admin panel now, which is the point.
#
# ⚠ `animatics-to-video` KEEPS ITS HISTORICAL ID though it is shown as "Image to
# AI Video", because App.jsx sets that id when one workflow hands off to another.
# A key is a database value; a label is a label.
_WORKFLOWS = [
    ("plan-and-script", "Plan & Script", "🗓️"),
    ("text-to-image", "Text to Turnaround Image", "🖼️"),
    ("script-to-storyboard", "Script to Storyboard", "📝"),
    ("create-animatic-image", "Image to Animatic Image", "🖼️"),
    ("animatics-to-video", "Image to AI Video", "🎞️"),
    ("storyboard-to-animatics", "Video Editor", "🎬"),
]

# The capabilities — the expensive or optional things INSIDE the workflows. Each
# one is named after what it costs the operator, because that is the question
# being asked when somebody comes here to switch one off.
_CAPABILITIES = [
    ("veo-render", "Veo video renders", "🎥", "Billed per second of output. The most expensive thing in the app."),
    ("image-generate", "Image generation", "🖼️", "Every drawn panel, shot and reference."),
    ("tts-voiceover", "Voiceover (text to speech)", "🗣️", "Reads a board's dialogue aloud."),
    ("captions", "Automatic captions", "💬", "Transcribes an audio track into caption clips."),
    ("director", "🎬 Make Video (the auto-editor)", "🎬", "Two text calls that write an edit plan."),
    ("3d-meshy", "3D models (Meshy / Tripo)", "🧊", "Third-party 3D generation from a character's views."),
]


def _catalog() -> dict:
    """The built-in defaults: every feature live, visible to everyone.

    ⚠ SHIPPING WITH EVERYTHING ON IS THE POINT. This phase adds the machinery,
    not a change in what anybody can do — so a deployment that never opens the
    admin panel behaves exactly as it did before, and `require_feature` is a
    no-op until somebody deliberately flips something.
    """
    out = {}
    for i, (wid, label, icon) in enumerate(_WORKFLOWS):
        key = f"workflow.{wid}"
        out[key] = _defaults(key, label, icon, GROUP_WORKFLOW, i, "")
    for i, (cid, label, icon, note) in enumerate(_CAPABILITIES):
        key = f"cap.{cid}"
        out[key] = _defaults(key, label, icon, GROUP_CAPABILITY, i, note)
    return out


def _defaults(key, label, icon, group, order, note) -> dict:
    return {
        "key": key,
        "label": label,
        "icon": icon,
        "group": group,
        "note": note,
        "order": order,
        "status": STATUS_LIVE,
        "rollout": {"mode": ROLLOUT_ALL, "emails": [], "percent": 100},
        # Phase 3 reads this. Stored now so the shape does not change under an
        # existing collection later; the resolver ignores it until tiers exist.
        "min_tier": None,
        "updated_at": None,
        "updated_by": None,
    }


# What an administrator may write. An allow-list, for the same reason
# `users.PROFILE_FIELDS` is one: `key` and `group` are identity, not settings.
EDITABLE = frozenset({"label", "icon", "note", "order", "status", "rollout", "min_tier"})


# ===========================================================================
# Storage
# ===========================================================================
def _use_local() -> bool:
    return config.USER_STORE == "local"


def _local_path() -> Path:
    return Path(config.LOCAL_FEATURES_PATH)


def _local_load() -> dict:
    path = _local_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Local feature store at %s is unreadable — using defaults.", path)
        return {}


def _local_save(data: dict) -> None:
    _local_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


_collection = None


def get_collection():
    """Return the features collection, connecting on first use."""
    global _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        from .mongo import get_db

        col = get_db()[config.FEATURES_COLLECTION]
        try:
            col.create_index("key", unique=True)
        except Exception as e:  # noqa: BLE001 — an index is an optimisation
            logger.warning("Could not index features (%s).", e)
        _collection = col
        logger.info(
            "MongoDB feature store ready (db=%s, collection=%s)",
            config.MONGODB_DB, config.FEATURES_COLLECTION,
        )
        return _collection


# ===========================================================================
# The cache
# ===========================================================================
# ⚠ SEE THE MODULE DOCSTRING FOR WHY THIS FAILS OPEN. `_cache` holds the last
# answer that was actually read; it is only ever replaced by a SUCCESSFUL read,
# so a database that goes away leaves the previous truth in place rather than
# replacing it with nothing.
_cache: dict | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def _bump() -> None:
    """Drop the cache so the next read goes to the database.

    Called after every write. In a single-process deployment that makes a flag
    change instant; with several workers the others pick it up within
    `FEATURE_CACHE_TTL_S`, which is what that setting is sized for.
    """
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def all_features(fresh: bool = False) -> dict:
    """`{key: feature}` for every feature, defaults merged in. Never raises.

    ⚠ THE MERGE MATTERS. A stored document is layered ON TOP of the catalogue
    entry, so a feature added to `_CAPABILITIES` in a later release appears
    immediately with its defaults instead of being invisible until somebody
    writes a row for it — and a stored row that is missing a field added later
    still loads.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            if _cache is not None and now - _cache_at < config.FEATURE_CACHE_TTL_S:
                return _cache

    merged = _catalog()
    try:
        stored = _read_stored()
        for key, doc in stored.items():
            base = merged.get(key)
            if base is None:
                # A row for a feature the code no longer knows about. Kept and
                # shown rather than dropped: an operator who renamed a key wants
                # to SEE the orphan, not to have it silently vanish.
                merged[key] = {**_defaults(key, key, "•", GROUP_CAPABILITY, 999, ""), **doc}
            else:
                merged[key] = {**base, **{k: v for k, v in doc.items() if v is not None}}
    except Exception as e:  # noqa: BLE001 — fail open, see the docstring
        with _cache_lock:
            if _cache is not None:
                logger.warning("Feature store unreachable (%s) — serving the last known good.", e)
                return _cache
        logger.warning("Feature store unreachable (%s) — serving built-in defaults.", e)
        return merged

    with _cache_lock:
        _cache = merged
        _cache_at = now
    return merged


def _read_stored() -> dict:
    if _use_local():
        with _lock:
            return _local_load()
    return {d["key"]: {k: v for k, v in d.items() if k != "_id"} for d in get_collection().find()}


def save_feature(key: str, fields: dict, actor: str = "") -> dict:
    """Write an administrator's changes onto one feature. Returns the merged row."""
    clean = {k: v for k, v in (fields or {}).items() if k in EDITABLE}
    if "status" in clean and clean["status"] not in STATUSES:
        raise ValueError(f"Unknown status: {clean['status']!r}")
    if "rollout" in clean:
        clean["rollout"] = _clean_rollout(clean["rollout"])
    clean["key"] = key
    clean["updated_at"] = datetime.now(timezone.utc).isoformat()
    clean["updated_by"] = (actor or "").strip().lower() or None

    if _use_local():
        with _lock:
            data = _local_load()
            data[key] = {**data.get(key, {}), **clean}
            _local_save(data)
    else:
        get_collection().update_one({"key": key}, {"$set": clean}, upsert=True)

    _bump()
    return all_features(fresh=True).get(key, {})


def _clean_rollout(rollout: dict) -> dict:
    """Normalise a rollout rule, so nothing downstream has to be defensive."""
    mode = (rollout or {}).get("mode")
    if mode not in ROLLOUT_MODES:
        raise ValueError(f"Unknown rollout mode: {mode!r}")
    emails = [
        (e or "").strip().lower()
        for e in (rollout.get("emails") or [])
        if (e or "").strip()
    ]
    percent = rollout.get("percent")
    try:
        percent = max(0, min(100, int(percent)))
    except (TypeError, ValueError):
        percent = 100
    return {"mode": mode, "emails": emails, "percent": percent}


# ===========================================================================
# The resolver
# ===========================================================================
def _who(email: str) -> tuple[bool, dict, str]:
    """`(is_admin, overrides, tier)` from ONE read of the user document.

    ⚠ IT IS ONE LOOKUP ON PURPOSE, AND THE TIER IS WHY IT MATTERS TWICE OVER.
    `users.is_admin`, `users.get_overrides` and `billing.tier_of` each open the
    user record, and `resolve()` runs the rules over TWELVE features — so
    reading per-question, per-feature would be three dozen Atlas round trips to
    learn three fields off one document. Everything the rules need is fetched
    here, once, and passed down.
    """
    from . import billing

    try:
        user = users.get_user_by_email(email) or {}
    except Exception as e:  # noqa: BLE001 — fail open; see the module docstring
        logger.warning("Could not read %s while resolving features: %s", email, e)
        return False, {}, billing.DEFAULT_TIER
    raw = user.get("feature_overrides") or {}
    return (
        users.role_of(user) == users.ROLE_ADMIN,
        {k: bool(v) for k, v in raw.items() if isinstance(v, bool)},
        billing.tier_of(user),
    )


def resolve(email: str, *, is_admin: bool | None = None) -> dict:
    """`{key: {on, visible, status, source}}` for one account.

    `source` is why the answer is what it is, and it exists for the admin
    panel's user detail: "off" with no reason is an unanswerable support ticket.
    """
    resolved_admin, overrides, tier = _who(email)
    if is_admin is None:
        is_admin = resolved_admin

    out = {}
    for key, feat in all_features().items():
        out[key] = _resolve_one(feat, email, is_admin, overrides.get(key), tier)
    return out


def _resolve_one(feat: dict, email: str, is_admin: bool, override, tier: str = "") -> dict:
    status = feat.get("status") or STATUS_LIVE

    # 1. The kill switch, before anything else — including an override that says
    #    yes. Only an explicit per-user override can reopen it (checked next),
    #    which is how an admin tests something that is off for the site.
    if status == STATUS_HIDDEN and override is not True:
        return _answer(False, False, status, "hidden")

    # 2. A per-user override beats every rule below it, in both directions.
    if override is True:
        return _answer(True, True, status, "override")
    if override is False:
        return _answer(False, False, status, "override")

    # 3. The rollout gate. Admins pass it — see the module docstring.
    passed, why = _rollout_passes(feat, email, is_admin)
    if not passed:
        return _answer(False, False, status, why)

    # 4. Priced out. VISIBLE and off — a locked row is what an upgrade page is
    #    for, and hiding it instead means nobody ever discovers what they are
    #    missing. Admins pass, for the same reason they pass the rollout.
    min_tier = feat.get("min_tier")
    if min_tier and not is_admin:
        # `tier` was read once by `_who`; this is arithmetic on a cached table,
        # not another lookup.
        from . import billing  # local: billing imports features, not the reverse

        if not billing.meets(tier, min_tier):
            return _answer(False, True, status, "tier", min_tier=min_tier)

    # 5/6. Visible either way; "soon" is the one that is drawn and refuses.
    if status == STATUS_SOON:
        return _answer(False, True, status, "soon")
    return _answer(True, True, status, why)


def _answer(on: bool, visible: bool, status: str, source: str, min_tier=None) -> dict:
    # `min_tier` rides along only when it is the REASON — the sidebar draws a
    # lock from it and the upgrade screen names the tier to buy, and neither
    # should have to go and look the requirement up separately.
    return {
        "on": on, "visible": visible, "status": status,
        "source": source, "min_tier": min_tier,
    }


def _rollout_passes(feat: dict, email: str, is_admin: bool) -> tuple[bool, str]:
    rollout = feat.get("rollout") or {}
    mode = rollout.get("mode") or ROLLOUT_ALL
    if mode == ROLLOUT_ALL:
        return True, "all"
    if is_admin:
        # Staging a feature to a few people is useless if the person staging it
        # cannot look at it. "hidden" is the switch that means everyone.
        return True, "admin"
    if mode == ROLLOUT_ADMINS:
        return False, "admins-only"
    if mode == ROLLOUT_ALLOWLIST:
        return ((email or "").strip().lower() in (rollout.get("emails") or [])), "allowlist"
    if mode == ROLLOUT_PERCENT:
        return _in_percent(email, feat.get("key") or "", rollout.get("percent") or 0), "percent"
    return True, "all"


def _in_percent(email: str, key: str, percent: int) -> bool:
    """Deterministic bucketing.

    ⚠ HASHED, NOT RANDOM, and salted with the feature KEY. Random would flip a
    user in and out between two requests on the same page. Salting with the key
    stops a 10% rollout of five different features landing on the same unlucky
    tenth of the userbase every time.
    """
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    digest = hashlib.sha256(f"{key}:{(email or '').strip().lower()}".encode()).hexdigest()
    return int(digest[:8], 16) % 100 < percent


def explain(email: str, key: str) -> dict:
    """The whole answer for ONE feature and one account — not just the boolean.

    ⚠ THE GUARD NEEDS THE `source`, NOT ONLY THE VERDICT. "Off" and "off
    because it is on a plan you are not on" are the same refusal and two
    completely different sentences, and only the second one tells the customer
    what to do about it. `is_on` is this function with the answer thrown away.

    Never raises, and fails OPEN — see the module docstring.
    """
    try:
        feat = all_features().get(key)
        if feat is None:
            # An unknown key is ON. A guard naming a feature that was never
            # added to the catalogue must not silently close a working route —
            # the typo should be found in the panel, not by a customer.
            logger.warning("require_feature named an unknown feature: %s", key)
            return _answer(True, True, STATUS_LIVE, "unknown")
        is_admin, overrides, tier = _who(email)
        return _resolve_one(feat, email, is_admin, overrides.get(key), tier)
    except Exception as e:  # noqa: BLE001 — fail OPEN, see the module docstring
        logger.warning("Could not resolve feature %s for %s: %s", key, email, e)
        return _answer(True, True, STATUS_LIVE, "error")


def is_on(email: str, key: str) -> bool:
    """Whether one feature is usable by one account. Never raises."""
    return explain(email, key)["on"]


# ===========================================================================
# The guard
# ===========================================================================
def require_feature(key: str):
    """A FastAPI dependency that refuses when this feature is off for the caller.

    ⚠ HIDING A BUTTON IS NOT HIDING A FEATURE. The sidebar reading the same
    registry is cosmetic — anybody can call the route directly. THIS is what
    actually turns something off, which is why it goes on every route that
    creates work or spends money.

    ⚠ IT GATES CREATING AND SPENDING, NEVER READING. A workflow switched off
    stops new work; it must not make a customer's existing boards unreachable or
    un-exportable. Turning a feature off is a product decision, not a reason to
    lock somebody out of what they already made.
    """

    def guard(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        state = explain(current.email, key)
        if not state["on"]:
            feat = all_features().get(key) or {}
            label = feat.get("label") or key
            # 403 and a sentence a support agent can act on. The customer is
            # authenticated and known — this is not "who are you", it is "not
            # on your account", and saying which is the difference between one
            # email and four.
            raise HTTPException(status_code=403, detail=refusal(label, state))
        return current

    return guard


def refusal(label: str, state: dict) -> str:
    """The sentence for one refusal. ⚠ THE ONLY PLACE THIS WORDING LIVES.

    The guard raises it as a 403 detail and `/auth/me/entitlements` ships the
    same string to the browser, so the greyed-out button and the error the
    customer would have got by pressing it say exactly the same thing. Two
    wordings for one refusal is how a support agent ends up unable to tell which
    rule fired.
    """
    if state.get("source") == "tier" and state.get("min_tier"):
        # ⚠ NAMES THE PLAN, because "not enabled" leaves a paying customer with
        # nowhere to go. This is the one refusal with an action attached.
        return f"{label} is part of the {_tier_name(state['min_tier'])} plan."
    if state.get("status") == STATUS_SOON:
        return f"{label} isn't available on your account yet."
    return f"{label} isn't enabled for your account."


def _tier_name(tier_id: str) -> str:
    """The tier's display name, falling back to its id. Never raises."""
    try:
        from . import billing

        return (billing.all_tiers().get(tier_id) or {}).get("name") or tier_id
    except Exception as e:  # noqa: BLE001 — a refusal must not fail to be worded
        logger.warning("Could not name tier %s: %s", tier_id, e)
        return tier_id


# ===========================================================================
# Routes
# ===========================================================================
@router.get("/public/workflows")
def public_workflows() -> dict:
    """The workflow list for somebody who has NOT signed in — the landing page.

    ⚠ THIS EXISTS BECAUSE THE MARKETING PAGE WAS LYING BY A DAY. The rail reads
    `/auth/me/entitlements` and is correct the moment an administrator flips a
    switch; the landing page kept a hand-written copy of the same list, so
    hiding a workflow left it advertised to every stranger who visited, and
    launching one left it unmentioned. Same fix as the price list: this page
    already fetches `/billing/tiers` without a token, and for the same reason —
    what you SELL is public by nature.

    ⚠ NO AUTHENTICATION, AND THEREFORE NO ACCOUNT. It answers for a visitor with
    no email, no tier and no overrides, which is exactly right for a shop
    window: `resolve("")` applies the kill switch and the rollout rules, and an
    allow-list or a percentage rollout simply does not match. That is the
    conservative answer — something still being staged to 10% of accounts is not
    something to put on the front page.

    ⚠ IT LISTS WHAT IS *VISIBLE*, NOT WHAT IS *ON*, so a workflow gated behind a
    paid plan still appears — with `locked` set. A feature nobody can see is a
    feature nobody upgrades for, and that rule has to hold on the way IN as well
    as inside the app.

    ⚠ AND IT LEAKS NOTHING AN ANONYMOUS VISITOR SHOULD NOT HAVE: the label, the
    icon and the status are what the page prints. No counts, no emails, no
    rollout percentages, no per-account state — there is no account.

    Fails OPEN like everything else in this module: on any error the browser is
    told nothing and keeps its built-in list, which is a stale page rather than
    an empty one.
    """
    feats = all_features()
    resolved = resolve("", is_admin=False)

    workflows = []
    for key, state in resolved.items():
        feat = feats.get(key) or {}
        if feat.get("group") != GROUP_WORKFLOW or not state["visible"]:
            continue
        workflows.append(
            {
                "id": key.split(".", 1)[1],
                "label": feat.get("label") or key,
                "icon": feat.get("icon") or "\u2022",
                "status": state["status"],
                "locked": state["source"] == "tier",
            }
        )
    workflows.sort(key=lambda w: (_order_of(feats, w["id"]), w["label"]))
    return {"workflows": workflows}


@router.get("/auth/me/entitlements")
def my_entitlements(current: CurrentUser = Depends(get_current_user)) -> dict:
    """What THIS account may see and use — the one call the client makes on boot.

    ⚠ IT RETURNS A PRE-SHAPED `workflows` LIST as well as the raw map. The
    sidebar wants "the rows to draw, in order"; making the browser filter, sort
    and re-label the map would be the nav-order logic living in two places
    again, which is the thing this whole module exists to end.
    """
    is_admin = users.is_admin(current.email)
    resolved = resolve(current.email, is_admin=is_admin)
    feats = all_features()

    workflows = []
    for key, state in resolved.items():
        feat = feats.get(key) or {}
        if feat.get("group") != GROUP_WORKFLOW or not state["visible"]:
            continue
        workflows.append(
            {
                # The sidebar's nav id, which is the key without its prefix.
                "id": key.split(".", 1)[1],
                "label": feat.get("label") or key,
                "icon": feat.get("icon") or "•",
                # "live" | "soon" — the badge, and whether the page runs.
                "status": state["status"],
                # Visible but not usable BECAUSE OF THE TIER — the rail draws a
                # lock and the page offers the upgrade. Distinct from "soon",
                # which is not for sale at any price.
                "locked": state["source"] == "tier",
                "min_tier": state.get("min_tier"),
            }
        )
    workflows.sort(key=lambda w: (_order_of(feats, w["id"]), w["label"]))

    # ⚠ THE SAME SHAPE FOR THE CAPABILITIES, AND FOR THE SAME REASON. A
    # capability is not a page, so nothing navigates to it — it is the ✨
    # Animate button, the 🎙 Voiceover button, the 3D popup. The browser has to
    # draw those in three states (gone / locked / on) and it cannot work out
    # which from a bare boolean, so the pre-shaped answer carries the label to
    # print, the reason to show and the tier to sell.
    capabilities = []
    for key, state in resolved.items():
        feat = feats.get(key) or {}
        if feat.get("group") != GROUP_CAPABILITY:
            continue
        # ⚠ INVISIBLE IS OMITTED, exactly as a hidden workflow is. A control the
        # customer must not see at all is one the browser is never told about —
        # "draw it disabled" is the answer for locked, not for hidden.
        if not state["visible"]:
            continue
        label = feat.get("label") or key
        capabilities.append(
            {
                "id": key.split(".", 1)[1],
                "key": key,
                "label": label,
                "icon": feat.get("icon") or "•",
                "note": feat.get("note") or "",
                "on": state["on"],
                "status": state["status"],
                # Visible, off, and one purchase away — the button wears a lock
                # and offers the upgrade instead of erroring when pressed.
                "locked": state["source"] == "tier",
                "min_tier": state.get("min_tier"),
                # ⚠ THE SAME SENTENCE THE 403 WOULD HAVE CARRIED. See `refusal`.
                "reason": "" if state["on"] else refusal(label, state),
            }
        )
    capabilities.sort(key=lambda c: ((feats.get(c["key"]) or {}).get("order", 999), c["label"]))

    from . import billing

    tier_id = billing.tier_of(current.email)
    tier = billing.all_tiers().get(tier_id) or {}
    # (One extra read for the label and limits, on a route called once per page
    # load — not on the per-request path the guards use.)

    return {
        "features": {k: v["on"] for k, v in resolved.items()},
        "states": resolved,
        "workflows": workflows,
        "capabilities": capabilities,
        "account_role": users.ROLE_ADMIN if is_admin else users.ROLE_USER,
        # What they are on, so the pricing modal can mark the current card and
        # Home can stop saying "Free plan" to somebody paying for Pro.
        "tier": tier_id,
        "tier_name": tier.get("name") or tier_id,
        "limits": tier.get("limits") or {},
        # Rides along on the call the app already makes at boot, so Home can
        # show "1 of 2 projects used" without a second request.
        "usage": _usage_summary(current.email),
    }


def _usage_summary(email: str) -> dict:
    """Never let a counter read take down the entitlements call."""
    try:
        from . import usage

        return usage.summary(email)
    except Exception as e:  # noqa: BLE001 — the sidebar matters more than a tally
        logger.warning("Could not summarise usage for %s: %s", email, e)
        return {}


def _order_of(feats: dict, workflow_id: str) -> int:
    return (feats.get(f"workflow.{workflow_id}") or {}).get("order", 999)
