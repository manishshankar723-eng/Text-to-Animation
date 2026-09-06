"""
admin.py — the "/admin" router: who signed up, who signed in, and who they are.

People, their history, and the switchboard that decides what they can see.

⚠ THE RULES LIVE IN `features.py`, NOT HERE. This module is the panel's API — it
reads and writes the registry, and `features.resolve` is the single place that
decides anything. Tiers, prices and offers (Phases 3-4) get their own module
again, so this one keeps meaning "people, and the switches over them".

Routes (mounted under /admin, every one of them behind `require_admin`):
    GET    /admin/overview            dashboard tiles + the signup chart
    GET    /admin/users               the user table: search, filter, page
    GET    /admin/users/{email}       one account: profile, work, history, access
    POST   /admin/users/{email}/disabled   lock / unlock
    POST   /admin/users/{email}/role       grant / revoke administrator
    POST   /admin/users/{email}/note       the private note on an account
    POST   /admin/users/{email}/override   force one feature on/off for them
    POST   /admin/users/{email}/tier       move them onto a billing tier
    DELETE /admin/users/{email}       delete the account
    GET    /admin/features            the hide / launch switchboard
    PATCH  /admin/features/{key}      hide, launch, stage, rename, reorder one
    POST   /admin/features/{key}/min-tier  which tier unlocks it
    GET    /admin/tiers               the price list, with derived contents
    PATCH  /admin/tiers/{id}          change a price, its copy, its rank
    GET    /admin/offers              sales and coupons
    POST   /admin/offers              create one
    PATCH  /admin/offers/{id}         change or deactivate one
    GET    /admin/subscriptions       who purchased what, and for how much
    POST   /admin/subscriptions       record one by hand (no payment is taken)
    POST   /admin/subscriptions/{id}/cancel   end one now
    GET    /admin/events              the activity log, filterable
    GET    /admin/meta                what the filters should offer

THE THREE RULES THIS FILE IS BUILT ON
-------------------------------------
1. **The guard asks the database, not the token.** See `require_admin`.
2. **An administrator cannot act on themselves.** Every mutation below refuses
   when the target is the caller. That is not paternalism — it is the only thing
   standing between a mis-click and a site with no administrators left, and the
   actions a person legitimately wants to take on their OWN account (change the
   password, delete it) already exist on `/auth/me`.
3. **Every mutation is recorded, with the actor.** `events.record(...,
   actor=…)`. An admin panel whose changes leave no trace is worse than no
   panel, because it invites exactly the changes nobody wants to own.

⚠ RULE 2 HAS EXACTLY ONE EXEMPTION, AND IT IS NAMED AT ITS ROUTE:
`POST /users/{email}/override` may target the caller. Giving yourself an
override is how an administrator looks at a feature that is hidden from the
site, and unlike every other action here it cannot lock anybody out of anything.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
# ⚠ THE ONE PLACE IN THIS FILE THAT LEAVES THE EVENT LOOP. `upload_showcase_media`
# is `async def` (it awaits the upload), so anything slow it calls DIRECTLY blocks
# every other request in the process - and grabbing a poster shells out to ffmpeg
# against a file that may be 96MB. The Pillow work beside it is milliseconds and
# stays inline; this one does not.
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import (
    auth,
    banners,
    billing,
    branding,
    chat_settings,
    config,
    events,
    features,
    landing,
    offers,
    showcase,
    subscriptions,
    usage,
    users,
)
from .auth import CurrentUser, get_current_user
from .jobs import get_store
from .schemas import JobKind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------
def require_admin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Allow only administrators through. Use as `Depends(require_admin)`.

    ⚠ THE ROLE IS READ FROM THE DATABASE ON EVERY CALL, NOT FROM THE TOKEN, and
    that is the whole design of this dependency. `ACCESS_TOKEN_EXPIRE_MINUTES`
    is 1440 — a role baked into a JWT would go on being an administrator for a
    day after the role was taken away, and revocation that takes a day is not
    revocation. `get_current_user` has already authenticated the caller and paid
    for a lookup out of its own cache; this is one more `find_one` on a route
    nobody hits in a loop.

    ⚠ IT RETURNS 404, NOT 403, TO A NON-ADMIN. A 403 confirms that /admin exists
    and that the caller simply lacks the role, which is a map of the site handed
    to anyone with an account. There is nothing at that address as far as an
    ordinary user is concerned, and that is what the response says.
    """
    if not users.is_admin(current.email):
        logger.warning("Non-admin hit an /admin route: %s", current.email)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return current


def _target(email: str, actor: CurrentUser) -> dict:
    """Resolve the `{email}` path parameter, refusing self-targeting.

    Rule 2 in the module docstring lives here so no route can forget it.
    """
    key = (email or "").strip().lower()
    if key == actor.email.strip().lower():
        raise HTTPException(
            status_code=400,
            detail=(
                "You can't apply an administrator action to your own account. "
                "Use your profile page for changes to your own login."
            ),
        )
    user = users.get_user_by_email(key)
    if user is None:
        raise HTTPException(status_code=404, detail=f"No such user: {email}")
    return user


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Schemas — kept in this module, the way auth.py keeps its own. `schemas.py`
# holds the shapes the WORKFLOWS share; nothing outside this router speaks these.
# ---------------------------------------------------------------------------
class AdminUserRow(BaseModel):
    """One row of the user table. A summary — see AdminUserDetail for the rest."""

    email: str
    display_name: str = ""
    full_name: str = ""
    company: str = ""
    account_role: str = "user"
    disabled: bool = False
    created_at: str | None = None
    last_login_at: str | None = None
    login_count: int = 0
    tier: str = ""
    # ⚠ ABSENT, NOT ZERO, when the count was not asked for. The table renders a
    # dash for None and "0 projects" for 0, and those are different facts —
    # see the `with_counts` parameter on the listing route.
    projects: int | None = None


class AdminUserList(BaseModel):
    users: list[AdminUserRow]
    # The number matching the FILTER, not the number on this page — the pager
    # needs to know how many there are behind it.
    total: int
    limit: int
    skip: int


class AdminUserDetail(BaseModel):
    """One account, everything an administrator may see about it."""

    user: AdminUserRow
    # Read-only profile extras that don't earn a column in the table.
    default_style: str = ""
    default_aspect_ratio: str = ""
    default_genre: str = ""
    timezone: str = ""
    role_title: str = ""
    admin_note: str = ""
    # What they have made, by workflow, and their recent history.
    jobs_by_kind: dict[str, int] = Field(default_factory=dict)
    recent_events: list[dict] = Field(default_factory=list)
    # True when the role is pinned by ADMIN_EMAILS and the panel cannot change
    # it. The UI shows a lock rather than a control that would silently no-op.
    role_locked: bool = False
    # Every feature and how it resolves FOR THIS ACCOUNT, each with the reason:
    # "from the rollout", "overridden here", "hidden for everyone". ⚠ THE REASON
    # IS THE POINT — "off" with no explanation is an unanswerable support ticket,
    # and this panel is where that ticket gets answered.
    feature_states: dict[str, dict] = Field(default_factory=dict)
    feature_meta: list[dict] = Field(default_factory=list)
    # Which tier they are on, and the whole ladder so the panel can offer a
    # move without a second request.
    tier: str = ""
    tier_name: str = ""
    tier_expires_at: str | None = None
    tiers: list[dict] = Field(default_factory=list)
    subscriptions: list[dict] = Field(default_factory=list)
    # What they have used this month, against what their tier allows. ⚠ THE
    # LIMITS COME BACK WITH IT so the panel never has to look up the tier
    # separately and risk pairing this month's usage with last month's plan.
    usage: dict = Field(default_factory=dict)
    # ⚠ THE SERVER DECIDES THIS, not the client comparing two strings. Every
    # mutation below refuses when the target is the caller (see `_target`), and
    # the panel needs to know that BEFORE drawing controls that would only fail
    # when pressed. Normalising an address is the server's job anyway — the
    # client holds whatever the user typed at the login box.
    is_self: bool = False


class DisabledRequest(BaseModel):
    disabled: bool


class RoleRequest(BaseModel):
    account_role: str = Field(..., pattern="^(user|admin)$")


class NoteRequest(BaseModel):
    note: str = Field("", max_length=2000)


class OverviewResponse(BaseModel):
    users_total: int
    users_disabled: int
    users_admin: int
    signups_today: int
    signups_7d: int
    signups_30d: int
    signed_in_7d: int
    logins_7d: int
    failed_logins_7d: int
    jobs_by_kind: dict[str, int] = Field(default_factory=dict)
    jobs_total: int = 0
    users_by_tier: list[dict] = Field(default_factory=list)
    subscriptions_active: int = 0
    recorded_monthly: int = 0
    currency: str = "USD"
    signups_daily: list[dict] = Field(default_factory=list)
    recent_events: list[dict] = Field(default_factory=list)
    # Honest reporting of where the numbers came from, shown in the panel's
    # footer. An operator looking at a dashboard deserves to know it is reading
    # a local JSON file rather than the production database.
    stores: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get("/overview", response_model=OverviewResponse)
def overview(admin: CurrentUser = Depends(require_admin)):
    """The dashboard tiles.

    ⚠ SIGNUPS COME FROM THE `users` COLLECTION, NOT FROM THE EVENT LOG, and the
    difference matters on the day this ships. Every account that existed before
    today has no `user.registered` event and never will, so counting events
    would report a business that began this morning. `created_at` has been on
    every user document since the beginning, so counting THAT is retroactive and
    correct. Sign-ins are the other way round — nothing recorded them before
    now, so those tiles genuinely do start from zero and the panel says so.
    """
    day = _iso_days_ago(1)
    week = _iso_days_ago(7)
    month = _iso_days_ago(30)

    try:
        jobs_by_kind = get_store().count_by_kind()
    except NotImplementedError:
        # A custom store that predates count_by_kind. Say nothing rather than
        # guess — an empty dict renders as "—", a zero would be a claim.
        jobs_by_kind = {}
    except Exception as e:  # noqa: BLE001 — a tile must not take the page down
        logger.warning("Could not count jobs for the admin overview: %s", e)
        jobs_by_kind = {}

    return OverviewResponse(
        users_total=users.count_users(),
        users_disabled=users.count_users(disabled=True),
        users_admin=users.count_users(role=users.ROLE_ADMIN),
        signups_today=users.count_users(created_since=day),
        signups_7d=users.count_users(created_since=week),
        signups_30d=users.count_users(created_since=month),
        signed_in_7d=events.distinct_emails_since(week, [events.TYPE_LOGIN]),
        logins_7d=events.count_since(week, [events.TYPE_LOGIN]),
        failed_logins_7d=events.count_since(week, [events.TYPE_LOGIN_FAILED]),
        jobs_by_kind=jobs_by_kind,
        jobs_total=sum(jobs_by_kind.values()),
        subscriptions_active=subscriptions.count(subscriptions.STATUS_ACTIVE),
        recorded_monthly=subscriptions.recurring_revenue()["monthly"],
        currency=config.BILLING_CURRENCY,
        users_by_tier=[
            {
                "id": t["id"],
                "name": t["name"],
                "monthly": t.get("monthly", 0),
                "users": users.count_users_on_tier(t["id"]),
            }
            for t in sorted(billing.all_tiers().values(), key=lambda t: t["rank"])
            if not t.get("archived")
        ],
        signups_daily=events.daily_counts(30, [events.TYPE_REGISTERED]),
        recent_events=events.list_events(12),
        stores={
            "users": config.USER_STORE,
            "jobs": config.JOB_STORE,
            "events": "local" if config.USER_STORE == "local" else "mongo",
            # The one configuration mistake that would make every token forgeable.
            # An admin panel is exactly where that belongs on screen.
            "jwt_secret_is_dev": config.JWT_SECRET_IS_DEV,
        },
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@router.get("/users", response_model=AdminUserList)
def list_users(
    admin: CurrentUser = Depends(require_admin),
    search: str = Query("", max_length=120),
    role: str | None = Query(None, pattern="^(user|admin)$"),
    disabled: bool | None = None,
    sort: str = Query("created_at"),
    desc: bool = True,
    limit: int = Query(50, ge=1, le=config.ADMIN_MAX_PAGE),
    skip: int = Query(0, ge=0),
    with_counts: bool = Query(
        False,
        description=(
            "Also return each row's project count. ⚠ ONE QUERY PER ROW — off by "
            "default so the table stays one round trip, and turned on by a "
            "button rather than by scrolling."
        ),
    ),
):
    """The user table: search, filter, sort, page."""
    rows = users.list_users(
        limit=limit, skip=skip, search=search, role=role,
        disabled=disabled, sort=sort, desc=desc,
    )
    total = users.count_users(search=search, role=role, disabled=disabled)

    out = []
    for row in rows:
        model = AdminUserRow(**_row_fields(row))
        if with_counts:
            model.projects = _project_count(row["email"])
        out.append(model)

    return AdminUserList(users=out, total=total, limit=limit, skip=skip)


def _row_fields(row: dict) -> dict:
    """User document → the table row's fields, with every default filled in.

    Written out rather than splatted because a user document holds fields this
    model does not declare (`password_hash` is stripped upstream, but
    `default_genre` and friends are not) and Pydantic would reject them.
    """
    return {
        "email": row.get("email") or "",
        "display_name": row.get("display_name") or "",
        "full_name": row.get("full_name") or "",
        "company": row.get("company") or "",
        "account_role": row.get(users.ROLE_FIELD) or users.ROLE_USER,
        "disabled": bool(row.get("disabled")),
        "created_at": row.get("created_at"),
        "last_login_at": row.get("last_login_at"),
        "login_count": int(row.get("login_count") or 0),
        # Read through `tier_of` rather than straight off the document, so an
        # account holding a tier id that has since been renamed away shows the
        # fallback instead of a dead string.
        "tier": billing.tier_of(row),
    }


def _project_count(email: str) -> int | None:
    """How many jobs this account owns, or None when the store cannot say."""
    try:
        return sum(get_store().count_by_kind(owner=email).values())
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not count jobs for %s: %s", email, e)
        return None


@router.get("/users/{email}", response_model=AdminUserDetail)
def get_user(email: str, admin: CurrentUser = Depends(require_admin)):
    """One account in full.

    ⚠ THE CALLER MAY LOOK AT THEMSELVES HERE. `_target` is not used, because
    reading is not acting — an administrator inspecting their own row is
    ordinary, and refusing it would only make the table's rows behave
    inconsistently depending on which one you clicked.
    """
    key = (email or "").strip().lower()
    user = users.get_user_by_email(key)
    if user is None:
        raise HTTPException(status_code=404, detail=f"No such user: {email}")

    try:
        jobs_by_kind = get_store().count_by_kind(owner=key)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not count jobs for %s: %s", key, e)
        jobs_by_kind = {}

    return AdminUserDetail(
        user=AdminUserRow(**_row_fields(user), projects=sum(jobs_by_kind.values())),
        default_style=user.get("default_style") or "",
        default_aspect_ratio=user.get("default_aspect_ratio") or "",
        default_genre=user.get("default_genre") or "",
        timezone=user.get("timezone") or "",
        # The person's JOB TITLE. Named apart from `account_role` here for the
        # same reason it is stored apart — see users.PROFILE_FIELDS.
        role_title=user.get("role") or "",
        admin_note=user.get("admin_note") or "",
        jobs_by_kind=jobs_by_kind,
        recent_events=events.list_events(30, email=key),
        feature_states=features.resolve(key),
        # The labels and grouping, sent alongside rather than merged into the
        # states: the resolver answers about ACCESS, the catalogue says what the
        # thing is called, and keeping them apart is what lets the same resolver
        # feed the sidebar, the guards and this panel.
        feature_meta=[
            {"key": f["key"], "label": f["label"], "icon": f["icon"], "group": f["group"],
             "order": f["order"], "status": f["status"]}
            for f in sorted(
                features.all_features().values(),
                key=lambda f: (f["group"], f["order"], f["key"]),
            )
        ],
        role_locked=key in config.ADMIN_EMAILS,
        tier=billing.tier_of(user),
        tier_expires_at=user.get("tier_expires_at"),
        subscriptions=subscriptions.list_subscriptions(10, email=key),
        usage=usage.summary(key),
        tier_name=(billing.all_tiers().get(billing.tier_of(user)) or {}).get("name", ""),
        tiers=[
            {"id": t["id"], "name": t["name"], "rank": t["rank"], "archived": bool(t.get("archived"))}
            for t in sorted(billing.all_tiers().values(), key=lambda t: t["rank"])
        ],
        is_self=key == admin.email.strip().lower(),
    )


@router.post("/users/{email}/disabled", response_model=AdminUserRow)
def set_disabled(
    email: str,
    body: DisabledRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
):
    """Lock or unlock an account."""
    user = _target(email, admin)
    key = user["email"]
    users.set_disabled(key, body.disabled)

    # ⚠ WITHOUT THIS THE LOCK-OUT IS UP TO THIRTY SECONDS LATE. `get_current_user`
    # caches a resolved user against its token, so a disabled account goes on
    # being served from that cache until the TTL runs out. `auth.py` has carried
    # this function since before there was an admin panel, for this exact call.
    auth.forget_cached_email(key)

    events.record(
        events.TYPE_ADMIN_USER_DISABLED if body.disabled else events.TYPE_ADMIN_USER_ENABLED,
        key,
        actor=admin.email,
        **events.request_context(request),
    )
    logger.info(
        "%s %s account %s", admin.email, "disabled" if body.disabled else "enabled", key
    )
    return AdminUserRow(**_row_fields(users.get_user_by_email(key)))


@router.post("/users/{email}/role", response_model=AdminUserRow)
def set_role(
    email: str,
    body: RoleRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
):
    """Grant or revoke the administrator role."""
    user = _target(email, admin)
    key = user["email"]

    # ⚠ REFUSED RATHER THAN SILENTLY IGNORED. `role_of` treats ADMIN_EMAILS as a
    # floor the document cannot lower, so writing "user" onto one of these
    # accounts would save happily and change nothing — a control that reports
    # success and does nothing is worse than one that says no.
    if key in config.ADMIN_EMAILS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{key} is pinned as an administrator by the ADMIN_EMAILS "
                f"environment variable, so its role can't be changed here. "
                f"Remove it from ADMIN_EMAILS and restart the API first."
            ),
        )

    was = users.role_of(user)
    users.set_role(key, body.account_role)
    # A demotion has to land NOW for the same reason a lock-out does.
    auth.forget_cached_email(key)

    events.record(
        events.TYPE_ADMIN_ROLE_CHANGED,
        key,
        actor=admin.email,
        was=was,
        now=body.account_role,
        **events.request_context(request),
    )
    logger.info("%s changed %s role: %s → %s", admin.email, key, was, body.account_role)
    return AdminUserRow(**_row_fields(users.get_user_by_email(key)))


@router.post("/users/{email}/note", response_model=AdminUserRow)
def set_note(
    email: str,
    body: NoteRequest,
    admin: CurrentUser = Depends(require_admin),
):
    """Save the private administrator note on an account.

    Never returned by `/auth/me`, so the person it is about cannot read it.
    ⚠ The note is NOT put in the event's meta — a note can hold something
    sensitive about a customer, and the activity log is a different, longer-lived
    surface. The event records only that one was saved.
    """
    user = _target(email, admin)
    key = user["email"]
    users.set_note(key, body.note.strip())
    events.record(events.TYPE_ADMIN_NOTE_SAVED, key, actor=admin.email)
    return AdminUserRow(**_row_fields(users.get_user_by_email(key)))


@router.delete("/users/{email}", status_code=204)
def delete_user(
    email: str,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
):
    """Delete an account.

    ⚠ THE ACCOUNT ONLY. Their jobs, boards and animatics stay in the job store,
    orphaned but intact — deleting a customer's work is a separate, much larger
    decision (it is what a GDPR erasure means, and it is not reversible), and
    quietly bundling it into a button labelled Delete is how an admin panel
    destroys something nobody meant to destroy. Phase 2 can add an explicit
    "delete their work too" once there is a way to preview what that removes.
    """
    user = _target(email, admin)
    key = user["email"]
    users.delete_user(key)
    auth.forget_cached_email(key)
    events.record(
        events.TYPE_ADMIN_USER_DELETED,
        key,
        actor=admin.email,
        **events.request_context(request),
    )
    logger.info("%s deleted account %s", admin.email, key)
    return None


# ---------------------------------------------------------------------------
# Features — the hide / launch switchboard
# ---------------------------------------------------------------------------
class RolloutModel(BaseModel):
    mode: str = Field(features.ROLLOUT_ALL, pattern="^(all|admins|allowlist|percent)$")
    emails: list[str] = Field(default_factory=list, max_length=500)
    percent: int = Field(100, ge=0, le=100)


class FeatureUpdate(BaseModel):
    """Every field optional — a screen that edits one control saves one field."""

    label: str | None = Field(None, max_length=80)
    icon: str | None = Field(None, max_length=8)
    note: str | None = Field(None, max_length=300)
    order: int | None = Field(None, ge=0, le=999)
    status: str | None = Field(None, pattern="^(live|soon|hidden)$")
    rollout: RolloutModel | None = None


class OverrideRequest(BaseModel):
    key: str = Field(..., max_length=80)
    # ⚠ THREE STATES, NOT TWO. `null` CLEARS the override and hands the account
    # back to the rollout rule; `false` is an explicit denial that survives the
    # rule changing. Collapsing them would make "remove this exception" and
    # "ban this customer from it" the same button.
    value: bool | None = None


@router.get("/features")
def list_features(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Every feature and its current setting, for the switchboard.

    Read FRESH, never from the cache: this is the screen an administrator has
    open while changing things, and showing them a value up to
    `FEATURE_CACHE_TTL_S` old is showing them their own edit not taking effect.
    """
    feats = features.all_features(fresh=True)
    return {
        "features": sorted(feats.values(), key=lambda f: (f["group"], f["order"], f["key"])),
        "statuses": list(features.STATUSES),
        "rollout_modes": list(features.ROLLOUT_MODES),
        "groups": [features.GROUP_WORKFLOW, features.GROUP_CAPABILITY],
    }


@router.patch("/features/{key}")
def update_feature(
    key: str,
    body: FeatureUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Change one feature: hide it, launch it, stage it, rename it, reorder it."""
    if key not in features.all_features(fresh=True):
        raise HTTPException(status_code=404, detail=f"No such feature: {key}")

    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    was = features.all_features().get(key, {})
    try:
        saved = features.save_feature(key, fields, actor=admin.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    events.record(
        events.TYPE_ADMIN_FEATURE_CHANGED,
        actor=admin.email,
        feature=key,
        # Only what actually moved, and only the two fields worth reading back
        # in a log six weeks later. A diff of the whole document would bury them.
        was_status=was.get("status"),
        now_status=saved.get("status"),
        fields=sorted(fields),
        **events.request_context(request),
    )
    logger.info(
        "%s changed feature %s (%s): %s → %s",
        admin.email, key, ", ".join(sorted(fields)), was.get("status"), saved.get("status"),
    )
    return saved


@router.post("/users/{email}/override", response_model=AdminUserDetail)
def set_override(
    email: str,
    body: OverrideRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
):
    """Force one feature on or off for one account — or clear that force.

    ⚠ SELF-TARGETING IS ALLOWED HERE, and it is the ONE administrator action
    that is. Giving yourself an override is how you look at a feature that is
    hidden from the site, which is the whole reason the kill switch can be
    reopened per-account at all. It cannot lock anybody out of anything — the
    worst it does is switch off a feature for the person who pressed it, which
    they can undo from the same control.
    """
    key = (email or "").strip().lower()
    user = users.get_user_by_email(key)
    if user is None:
        raise HTTPException(status_code=404, detail=f"No such user: {email}")
    if body.key not in features.all_features():
        raise HTTPException(status_code=404, detail=f"No such feature: {body.key}")

    users.set_override(key, body.key, body.value)
    events.record(
        events.TYPE_ADMIN_OVERRIDE_SET,
        key,
        actor=admin.email,
        feature=body.key,
        value=body.value,
        **events.request_context(request),
    )
    return get_user(email, admin)


# ---------------------------------------------------------------------------
# Pricing — the tiers
# ---------------------------------------------------------------------------
class BulletModel(BaseModel):
    text: str = Field(..., max_length=120)
    ok: bool = True
    strong: bool = False


class TierUpdate(BaseModel):
    """Every field optional. ⚠ PRICES ARE INTEGER MINOR UNITS — $28.00 is 2800.

    Declared as `int` rather than `float` on purpose: a float here means the
    caller is thinking in dollars, and quietly rounding 28.5 to 28 cents is a
    hundredfold pricing error nobody notices until an invoice.
    """

    name: str | None = Field(None, max_length=60)
    blurb: str | None = Field(None, max_length=240)
    rank: int | None = Field(None, ge=0, le=999)
    monthly: int | None = Field(None, ge=0, le=100_000_00)
    yearly: int | None = Field(None, ge=0, le=100_000_00)
    compare_at: int | None = Field(None, ge=0, le=100_000_00)
    badge: str | None = Field(None, max_length=30)
    highlight: bool | None = None
    bullets: list[BulletModel] | None = Field(None, max_length=12)
    limits: dict | None = None
    visible: bool | None = None
    archived: bool | None = None


class UserTierRequest(BaseModel):
    tier: str = Field(..., max_length=40)


@router.get("/tiers")
def list_tiers(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Every tier — archived ones included — and what each one unlocks.

    ⚠ `includes` IS DERIVED, NOT STORED. A tier does not carry a list of
    features; each FEATURE names the tier it needs (`min_tier`), and this asks
    them. That is the whole reason the two can never disagree — see the note at
    the top of `billing.py`. The editor shows it beside the marketing bullets so
    that copy promising something the flags don't grant is visible on screen.
    """
    tiers = sorted(billing.all_tiers(fresh=True).values(), key=lambda t: t["rank"])
    feats = features.all_features()
    return {
        "tiers": [
            {
                **t,
                "includes": [
                    {"key": k, "label": (feats.get(k) or {}).get("label") or k}
                    for k in billing.includes(t["id"])
                ],
                "subscribers": users.count_users_on_tier(t["id"]),
            }
            for t in tiers
        ],
        "currency": config.BILLING_CURRENCY,
        "default_tier": billing.DEFAULT_TIER,
        # So the Features screen's `min_tier` picker and this one agree about
        # what tiers exist without the client assembling its own list.
        "tier_ids": [t["id"] for t in tiers if not t.get("archived")],
    }


@router.patch("/tiers/{tier_id}")
def update_tier(
    tier_id: str,
    body: TierUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Change one tier: its price, its copy, its position on the ladder."""
    if tier_id not in billing.all_tiers(fresh=True):
        raise HTTPException(status_code=404, detail=f"No such tier: {tier_id}")

    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    was = billing.all_tiers().get(tier_id, {})
    try:
        saved = billing.save_tier(tier_id, fields, actor=admin.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    events.record(
        events.TYPE_ADMIN_TIER_CHANGED,
        actor=admin.email,
        tier=tier_id,
        # The money is what anybody reads this row back for.
        was_monthly=was.get("monthly"),
        now_monthly=saved.get("monthly"),
        was_yearly=was.get("yearly"),
        now_yearly=saved.get("yearly"),
        fields=sorted(fields),
        **events.request_context(request),
    )
    logger.info(
        "%s changed tier %s (%s)", admin.email, tier_id, ", ".join(sorted(fields))
    )
    return saved


@router.post("/features/{key}/min-tier")
def set_min_tier(
    key: str,
    body: UserTierRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Set (or clear, with an empty string) the tier a feature requires.

    ⚠ THIS IS THE ONLY PLACE THE "WHAT'S IN PRO" QUESTION IS ANSWERED. It lives
    on the FEATURE rather than as a list on the tier so that there is one
    statement of it, not two that can drift. The pricing screen shows the
    derived result; this is what it derives from.
    """
    if key not in features.all_features(fresh=True):
        raise HTTPException(status_code=404, detail=f"No such feature: {key}")
    tier = (body.tier or "").strip().lower()
    if tier and tier not in billing.all_tiers():
        raise HTTPException(status_code=404, detail=f"No such tier: {tier}")

    was = (features.all_features().get(key) or {}).get("min_tier")
    saved = features.save_feature(key, {"min_tier": tier or None}, actor=admin.email)
    events.record(
        events.TYPE_ADMIN_FEATURE_CHANGED,
        actor=admin.email,
        feature=key,
        fields=["min_tier"],
        was_min_tier=was,
        now_min_tier=tier or None,
        **events.request_context(request),
    )
    return saved


@router.post("/users/{email}/tier", response_model=AdminUserDetail)
def set_user_tier(
    email: str,
    body: UserTierRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
):
    """Move one account onto a tier.

    ⚠ THIS IS NOT A PAYMENT, AND THE PANEL SAYS SO. It records which tier
    somebody is on; taking money and remembering that it was taken is Phase 4.
    Marking a customer as paid by hand is not a stopgap — it is how early sales
    actually close, and it makes "who is on Pro" answerable weeks before a
    payment provider is integrated.

    ⚠ SELF-TARGETING IS REFUSED, like every other account action: an
    administrator upgrading their own account is the one change here that has an
    obvious motive to be done quietly.
    """
    user = _target(email, admin)
    key = user["email"]
    tier = (body.tier or "").strip().lower()
    if tier not in billing.all_tiers():
        raise HTTPException(status_code=404, detail=f"No such tier: {tier}")

    was = billing.tier_of(user)
    users.set_tier(key, tier)
    # A tier change moves what they can reach, and `get_current_user` caches the
    # resolved user for 30s — same reasoning as disabling an account.
    auth.forget_cached_email(key)

    events.record(
        events.TYPE_ADMIN_USER_TIER_CHANGED,
        key,
        actor=admin.email,
        was=was,
        now=tier,
        **events.request_context(request),
    )
    logger.info("%s moved %s: %s → %s", admin.email, key, was, tier)
    return get_user(email, admin)


# ---------------------------------------------------------------------------
# Offers — a site-wide sale, or a coupon
# ---------------------------------------------------------------------------
class OfferBody(BaseModel):
    """⚠ `code` DISTINGUISHES THE TWO KINDS: absent/empty is a SALE (applies to
    everyone automatically); present is a COUPON (applies to nobody until typed).
    It is settable only on create — see `offers.save_offer`."""

    code: str | None = Field(None, max_length=40)
    label: str = Field("", max_length=80)
    kind: str = Field(offers.KIND_PERCENT, pattern="^(percent|amount)$")
    value: int = Field(0, ge=0, le=100_000_00)
    applies_to: list[str] = Field(default_factory=list, max_length=20)
    period: str = Field(offers.PERIOD_BOTH, pattern="^(monthly|yearly|both)$")
    starts_at: str | None = None
    ends_at: str | None = None
    active: bool = True
    max_redemptions: int | None = Field(None, ge=1, le=1_000_000)
    banner: str = Field("", max_length=160)
    # ⚠ DEFAULTS TO TRUE, DELIBERATELY. An offer nobody is shown is a discount
    # that only exists in this panel; somebody creating one has, by default,
    # decided customers should hear about it. Untick it for a code you intend to
    # email to one person — it still works when typed, it is just not printed.
    promoted: bool = True
    # --- The pop-up on Explore. ⚠ ALL OPTIONAL, and the card draws without any
    # of them: an offer that predates these fields still pops up, headed by its
    # own label and summary. `popup_lines` arrives as a list; `offers._clean`
    # trims it, drops the blanks and caps it. ---
    popup: bool = True
    popup_title: str = Field("", max_length=80)
    popup_lines: list[str] = Field(default_factory=list, max_length=8)
    popup_note: str = Field("", max_length=200)
    popup_cta: str = Field("", max_length=40)


class OfferUpdate(BaseModel):
    label: str | None = Field(None, max_length=80)
    kind: str | None = Field(None, pattern="^(percent|amount)$")
    value: int | None = Field(None, ge=0, le=100_000_00)
    applies_to: list[str] | None = Field(None, max_length=20)
    period: str | None = Field(None, pattern="^(monthly|yearly|both)$")
    starts_at: str | None = None
    ends_at: str | None = None
    active: bool | None = None
    max_redemptions: int | None = Field(None, ge=1, le=1_000_000)
    banner: str | None = Field(None, max_length=160)
    promoted: bool | None = None
    popup: bool | None = None
    popup_title: str | None = Field(None, max_length=80)
    popup_lines: list[str] | None = Field(None, max_length=8)
    popup_note: str | None = Field(None, max_length=200)
    popup_cta: str | None = Field(None, max_length=40)


def _offer_row(offer: dict) -> dict:
    """One offer plus the facts the table needs that aren't stored.

    ⚠ `promoted` IS RESOLVED HERE, NOT PASSED THROUGH. The stored row may not
    carry the key at all (every offer predates it), and `offers.is_promoted`
    reads that absence as YES — so the checkbox in the panel must be fed the
    same answer the pricing page is, or the two disagree about a row nobody
    edited.
    """
    return {
        **offer,
        "live": offers.is_live(offer),
        "summary": offers.summary(offer),
        "is_sale": not offer.get("code"),
        "promoted": offers.is_promoted(offer),
        # Resolved for the same reason `promoted` is: the stored row need not
        # carry the key, and absence reads as YES.
        "popup": offers.is_popup(offer),
    }


@router.get("/offers")
def list_offers(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Every sale and coupon, newest first."""
    rows = [_offer_row(o) for o in offers.all_offers(fresh=True)]
    return {
        "offers": rows,
        "kinds": list(offers.KINDS),
        "periods": list(offers.PERIODS),
        "tier_ids": [
            {"id": t["id"], "name": t["name"]}
            for t in sorted(billing.all_tiers().values(), key=lambda t: t["rank"])
            if not t.get("archived")
        ],
        "currency": config.BILLING_CURRENCY,
    }


@router.post("/offers", status_code=201)
def create_offer(
    body: OfferBody, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Create a sale (no code) or a coupon (with one)."""
    try:
        offer = offers.create_offer(body.model_dump(), actor=admin.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events.record(
        events.TYPE_ADMIN_OFFER_CHANGED,
        actor=admin.email,
        offer=offer["id"],
        code=offer.get("code"),
        action="created",
        summary=offers.summary(offer),
        **events.request_context(request),
    )
    logger.info("%s created offer %s (%s)", admin.email, offer["id"], offer.get("code") or "sale")
    return _offer_row(offer)


@router.patch("/offers/{offer_id}")
def update_offer(
    offer_id: str,
    body: OfferUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Change an offer, or switch it off.

    ⚠ THE CODE IS NOT EDITABLE. A code that has been printed on a card or sent
    in an email is out in the world; renaming it would silently break every
    place it was written down. Deactivate it and make another.
    """
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    try:
        offer = offers.save_offer(offer_id, fields, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such offer: {offer_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events.record(
        events.TYPE_ADMIN_OFFER_CHANGED,
        actor=admin.email,
        offer=offer_id,
        code=offer.get("code"),
        action="updated",
        fields=sorted(fields),
        **events.request_context(request),
    )
    return _offer_row(offer)


# ---------------------------------------------------------------------------
# Branding — what the app is CALLED and what its mark looks like
# ---------------------------------------------------------------------------
# ⚠ THE WRITE IS HERE, THE READ IS PUBLIC AND LIVES IN `server/branding.py`.
# Same split as features (panel writes `/admin/features`, the world reads
# `/public/workflows`) and for the same reason: a logged-OUT visitor on the
# landing page needs the name and the mark, and must not need a token to get
# them. Everything below is behind `require_admin` like every other route in
# this file.
class BrandingBody(BaseModel):
    """What the Brand screen may change. ⚠ EVERY FIELD IS OPTIONAL so a PATCH
    that changes one of them does not have to resend the others —
    `exclude_unset` below is what makes that true, and it is why saving a colour
    cannot blank a name.

    ⚠ THE COLOURS ARE NOT PATTERN-VALIDATED HERE, ON PURPOSE.
    `branding.clean_hex` coerces rather than refusing (read its docstring); a
    `pattern=` on this model would turn a half-typed `#ab` into a 422 in the
    middle of somebody dragging a colour picker."""

    name: str | None = Field(None, max_length=200)
    theme_id: str | None = Field(None, max_length=32)
    accent: str | None = Field(None, max_length=9)
    ground: str | None = Field(None, max_length=9)


def _branding_row() -> dict:
    """What the panel is shown: the public answer plus the facts only an
    administrator needs — who changed it, when, and what the built-in default
    was, so "put it back" is a visible option rather than a guess.

    ⚠ `logos` SAYS WHICH SLOT IS *ITS OWN* AND WHICH IS BORROWED, and the panel
    needs both. `public_payload` has already resolved the fallback, so a
    deployment with one upload sends the same URL twice — without `own` the Brand
    screen would draw a Remove button beside a logo that slot does not have, and
    pressing it would do nothing.
    """
    row = branding.get_branding(fresh=True)
    return {
        **branding.public_payload(row),
        "logos": {
            slot: {
                "own": bool(row.get(branding.slot_field(slot))),
                "stamp": branding.resolve_slot(row, slot),
            }
            for slot in branding.SLOTS
        },
        "slots": list(branding.SLOTS),
        "has_logo": any(row.get(branding.slot_field(s)) for s in branding.SLOTS),
        "default_name": branding.DEFAULT_NAME,
        # What "put it back" means for the colours - same shape as
        # `default_name`, and the same reason: a reset the panel can offer
        # honestly beats an administrator guessing the shipped hexes.
        "default_theme": {
            "theme_id": branding.DEFAULT_THEME_ID,
            "accent": branding.DEFAULT_ACCENT,
            "ground": branding.DEFAULT_GROUND,
        },
        "name_max": branding.NAME_MAX_CHARS,
        "logo_max_px": branding.LOGO_MAX_PX,
        "allowed_types": list(branding.ALLOWED_LOGO_TYPES),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


@router.get("/branding")
def get_branding(admin: CurrentUser = Depends(require_admin)) -> dict:
    """The app's current name and mark."""
    return _branding_row()


@router.patch("/branding")
def update_branding(
    body: BrandingBody, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Rename the app, or repaint it. Lands on every screen at once — see
    `branding.py` for the name and `client/src/palette.js` for the colours."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    was = branding.get_branding(fresh=True).get("name") or ""
    row = branding.save_branding(fields, actor=admin.email)

    # ⚠ THE ENTRY SAYS WHICH OF THE TWO HAPPENED. A repaint and a rename come
    # through the same route, and an activity feed reading "renamed" beside an
    # unchanged name is the kind of line that costs somebody an afternoon.
    renamed = "name" in fields and row.get("name") != was
    repainted = any(k in fields for k in ("theme_id", "accent", "ground"))
    action = ("renamed" if renamed and not repainted
              else "repainted" if repainted and not renamed
              else "changed")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action=action,
        was=was,
        now=row.get("name"),
        theme=row.get("theme_id"),
        accent=row.get("accent"),
        ground=row.get("ground"),
        **events.request_context(request),
    )
    if renamed:
        logger.info("%s renamed the app: %r -> %r", admin.email, was, row.get("name"))
    if repainted:
        logger.info(
            "%s repainted the app: theme=%s accent=%s ground=%s",
            admin.email, row.get("theme_id"), row.get("accent"), row.get("ground"),
        )
    return _branding_row()


@router.post("/branding/logo/{slot}")
async def upload_branding_logo(
    slot: str,
    request: Request,
    image: UploadFile = File(..., description="The app logo, ideally a transparent PNG."),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Put an uploaded logo in one theme's slot — `dark` or `light`.

    ⚠ TWO SLOTS BECAUSE A LOGO IS A FLAT PICTURE. The drawn mark re-colours
    itself (`currentColor`); an uploaded white wordmark disappears into the light
    theme, which is exactly what happened to the first one. Either slot fills in
    for the other, so ONE upload is still a complete answer — see `branding.py`.

    ⚠ THIS IS NOT `POST /brand/logo`, WHICH IS A CUSTOMER'S LOGO FOR A BOARD.
    That one is per-account, owner-scoped and composited into panels; this one is
    the APP'S OWN mark and is served to anonymous visitors. They share nothing but
    the word "logo".
    """
    if slot not in branding.SLOTS:
        raise HTTPException(
            status_code=404, detail=f"No such logo slot: {slot!r}."
        )
    if image.content_type not in branding.ALLOWED_LOGO_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type {image.content_type!r}. "
            f"Allowed: {', '.join(branding.ALLOWED_LOGO_TYPES)}.",
        )
    contents = await image.read()
    if len(contents) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Logo too large ({len(contents)} bytes). Max is {config.MAX_UPLOAD_BYTES}.",
        )
    try:
        png = branding.normalise_logo(contents)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    row = branding.save_logo(png, slot=slot, actor=admin.email)
    stamp = row.get(branding.slot_field(slot))
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="logo_uploaded",
        slot=slot,
        stamp=stamp,
        **events.request_context(request),
    )
    logger.info("%s uploaded a new %s-mode app logo (%s)", admin.email, slot, stamp)
    return _branding_row()


@router.delete("/branding/logo/{slot}")
def delete_branding_logo(
    slot: str, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Empty one theme's slot.

    ⚠ THAT THEME THEN BORROWS THE OTHER SLOT rather than going bare; only
    clearing BOTH brings back the mark the app ships with
    (`client/src/components/Logo.jsx`). The panel says so on the card.
    """
    if slot not in branding.SLOTS:
        raise HTTPException(status_code=404, detail=f"No such logo slot: {slot!r}.")
    branding.clear_logo(slot=slot, actor=admin.email)
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="logo_removed",
        slot=slot,
        **events.request_context(request),
    )
    logger.info("%s removed the %s-mode app logo", admin.email, slot)
    return _branding_row()


# ---------------------------------------------------------------------------
# Explore banners — the billboards, their words and their pictures
# ---------------------------------------------------------------------------
class BannerBody(BaseModel):
    """A new billboard. ⚠ ONLY `title` IS REQUIRED — a card with a heading and
    nothing else is a legitimate card, and every other field has a sensible
    absence: no button, no picture, no kicker."""

    slot: str = Field(banners.SLOT_HERO, pattern="^(hero|side)$")
    kicker: str = Field("", max_length=banners.KICKER_MAX)
    title: str = Field(..., min_length=1, max_length=banners.TITLE_MAX)
    body: str = Field("", max_length=banners.BODY_MAX)
    cta_label: str = Field("", max_length=banners.CTA_MAX)
    # A workflow id the shell knows, or an http(s) address. Validated in
    # `banners._clean` — see the note on `_TARGET_RE` there.
    cta_target: str = Field("", max_length=320)
    rank: int = Field(0, ge=0, le=999)
    active: bool = True


class BannerUpdate(BaseModel):
    """⚠ EVERY FIELD OPTIONAL, and the route sends `exclude_unset` — so a panel
    that only flips `active` does not have to resend the whole card, and a field
    added later cannot be blanked by an older client."""

    slot: str | None = Field(None, pattern="^(hero|side)$")
    kicker: str | None = Field(None, max_length=banners.KICKER_MAX)
    title: str | None = Field(None, min_length=1, max_length=banners.TITLE_MAX)
    body: str | None = Field(None, max_length=banners.BODY_MAX)
    cta_label: str | None = Field(None, max_length=banners.CTA_MAX)
    cta_target: str | None = Field(None, max_length=320)
    rank: int | None = Field(None, ge=0, le=999)
    active: bool | None = None


def _banner_row(row: dict) -> dict:
    """One banner plus the facts the panel needs that the customer is not told.

    ⚠ `active` IS RESOLVED HERE, not passed through — a row written before the
    field existed carries no key, and `all_banners` reads that absence as live.
    The panel's switch has to be fed the same answer the page is.
    """
    return {
        **banners.public_banner(row),
        "active": bool(row.get("active", True)),
        "rank": int(row.get("rank") or 0),
        "has_image": bool(row.get("image_id")),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


@router.get("/banners")
def list_banners(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Every billboard, live or not, with the limits the form has to respect."""
    return {
        "banners": [_banner_row(b) for b in banners.all_banners(fresh=True)],
        "slots": list(banners.SLOTS),
        "max_per_slot": banners.MAX_PER_SLOT,
        "image_max_px": banners.IMAGE_MAX_PX,
        "allowed_types": list(banners.ALLOWED_IMAGE_TYPES),
        "limits": {
            "kicker": banners.KICKER_MAX,
            "title": banners.TITLE_MAX,
            "body": banners.BODY_MAX,
            "cta_label": banners.CTA_MAX,
        },
    }


@router.post("/banners", status_code=201)
def create_banner(
    body: BannerBody, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    try:
        row = banners.create_banner(body.model_dump(), actor=admin.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="banner_created",
        banner=row.get("id"),
        slot=row.get("slot"),
        **events.request_context(request),
    )
    logger.info("%s created a %s banner (%s)", admin.email, row.get("slot"), row.get("id"))
    return _banner_row(row)


@router.patch("/banners/{banner_id}")
def update_banner(
    banner_id: str,
    body: BannerUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    try:
        row = banners.save_banner(banner_id, fields, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such banner.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="banner_changed",
        banner=banner_id,
        fields=sorted(fields),
        **events.request_context(request),
    )
    return _banner_row(row)


@router.delete("/banners/{banner_id}")
def remove_banner(
    banner_id: str, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """⚠ DELETE IS NOT HIDE, and the panel offers both. Hiding keeps the words
    for the next campaign; deleting throws away the picture with them."""
    try:
        banners.delete_banner(banner_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such banner.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="banner_deleted",
        banner=banner_id,
        **events.request_context(request),
    )
    logger.info("%s deleted banner %s", admin.email, banner_id)
    return {"ok": True}


@router.post("/banners/{banner_id}/image")
async def upload_banner_image(
    banner_id: str,
    request: Request,
    image: UploadFile = File(..., description="The banner picture."),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Put a picture on a billboard. Replaces whatever was there.

    ⚠ THE SAME THREE CHECKS THE APP LOGO GETS — type, size, then Pillow — in the
    same order, because the cheapest refusal should come first and Pillow is the
    only one that has to read the bytes.
    """
    if image.content_type not in banners.ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type {image.content_type!r}. "
            f"Allowed: {', '.join(banners.ALLOWED_IMAGE_TYPES)}.",
        )
    contents = await image.read()
    if len(contents) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large ({len(contents)} bytes). "
            f"Max is {config.MAX_UPLOAD_BYTES}.",
        )
    try:
        webp = banners.normalise_image(contents)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        row = banners.save_image(banner_id, webp, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such banner.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="banner_image_uploaded",
        banner=banner_id,
        **events.request_context(request),
    )
    logger.info("%s put a picture on banner %s", admin.email, banner_id)
    return _banner_row(row)


@router.delete("/banners/{banner_id}/image")
def delete_banner_image(
    banner_id: str, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Back to no picture — the card draws the workflow glyph instead."""
    try:
        row = banners.clear_image(banner_id, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such banner.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="banner_image_removed",
        banner=banner_id,
        **events.request_context(request),
    )
    return _banner_row(row)


# ---------------------------------------------------------------------------
# Landing hero art — ONE PICTURE PER WORKFLOW, in the four tiles of the hero
# ---------------------------------------------------------------------------
# ⚠ NO CREATE AND NO DELETE ROUTE, AND THAT IS THE WHOLE DIFFERENCE FROM THE
# BANNER ROUTES ABOVE. A banner is a row an administrator invents; a hero tile
# belongs to a WORKFLOW, so the list is the catalogue in `features.py` and the
# only writes are "put a picture on this workflow" and "take it off again".
# That is what makes a seventh workflow need no code: it appears in this list the
# moment it is in the catalogue.
#
# ⚠ THE ROW SAYS WHETHER THE PICTURE IS ACTUALLY ON THE PAGE, and it has to.
# Visibility lives in the Features tab, not here, so an operator who uploads a
# picture to a hidden workflow would otherwise see a perfectly good thumbnail and
# no tile on the landing page, with nothing on this screen explaining why.
def _landing_row(workflow: dict, art: dict, position: int) -> dict:
    """One workflow as the Landing tab sees it: its picture and its real fate."""
    row = art.get(workflow["id"]) or {}
    status = workflow.get("status") or features.STATUS_LIVE
    hidden = status == features.STATUS_HIDDEN
    return {
        "id": workflow["id"],
        "label": workflow.get("label") or workflow["id"],
        "icon": workflow.get("icon") or "•",
        "status": status,
        # ⚠ TWO SEPARATE FACTS, NOT ONE. `on_page` is "a visitor sees this tile";
        # `in_hero` is "it is one of the first four". A workflow can be live,
        # carry a picture, and still not be drawn because it is fifth — and an
        # operator staring at a picture that is not on the page needs to be told
        # WHICH of those two reasons it is.
        "on_page": not hidden,
        "in_hero": (not hidden) and position < landing.HERO_TILES,
        "image_url": landing.image_url(row),
        "has_image": bool(row.get("image_id")),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


@router.get("/landing/art")
def list_landing_art(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Every workflow, its hero picture and whether that picture is on the page."""
    workflows = landing.known_workflows()
    art = landing.all_art(fresh=True)
    # `position` counts only the ones a visitor is shown, because that is what
    # the hero slices — a hidden workflow does not use up one of the four tiles.
    rows = []
    shown = 0
    for workflow in workflows:
        rows.append(_landing_row(workflow, art, shown))
        if (workflow.get("status") or features.STATUS_LIVE) != features.STATUS_HIDDEN:
            shown += 1
    return {
        "workflows": rows,
        "hero_tiles": landing.HERO_TILES,
        "image_max_px": landing.IMAGE_MAX_PX,
        "allowed_types": list(landing.ALLOWED_IMAGE_TYPES),
    }


@router.post("/landing/art/{workflow_id}/image")
async def upload_landing_image(
    workflow_id: str,
    request: Request,
    image: UploadFile = File(..., description="The hero tile for this workflow."),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Put a picture in this workflow's hero tile. Replaces whatever was there.

    ⚠ THE SAME THREE CHECKS THE APP LOGO AND THE BANNERS GET — type, size, then
    Pillow — in the same order, because the cheapest refusal should come first
    and Pillow is the only one that has to read the bytes.
    """
    if image.content_type not in landing.ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type {image.content_type!r}. "
            f"Allowed: {', '.join(landing.ALLOWED_IMAGE_TYPES)}.",
        )
    contents = await image.read()
    if len(contents) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large ({len(contents)} bytes). "
            f"Max is {config.MAX_UPLOAD_BYTES}.",
        )
    try:
        webp = landing.normalise_image(contents)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        landing.save_image(workflow_id, webp, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such workflow.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="landing_art_uploaded",
        workflow=workflow_id,
        **events.request_context(request),
    )
    logger.info("%s put a hero picture on %s", admin.email, workflow_id)
    return _one_landing_row(workflow_id)


@router.delete("/landing/art/{workflow_id}/image")
def delete_landing_image(
    workflow_id: str, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Back to no picture — the hero draws this workflow's built-in tile again."""
    try:
        landing.clear_image(workflow_id, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such workflow.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="landing_art_removed",
        workflow=workflow_id,
        **events.request_context(request),
    )
    return _one_landing_row(workflow_id)


def _one_landing_row(workflow_id: str) -> dict:
    """The row for ONE workflow, answered after a write.

    ⚠ IT RECOMPUTES THE POSITION rather than guessing it, because `in_hero`
    depends on how many VISIBLE workflows sit above this one — a fact the upload
    route has no business working out for itself.
    """
    workflows = landing.known_workflows()
    art = landing.all_art(fresh=True)
    shown = 0
    for workflow in workflows:
        if workflow["id"] == workflow_id:
            return _landing_row(workflow, art, shown)
        if (workflow.get("status") or features.STATUS_LIVE) != features.STATUS_HIDDEN:
            shown += 1
    raise HTTPException(status_code=404, detail="No such workflow.")


# ---------------------------------------------------------------------------
# Explore showcase — the picture-and-video wall on the PUBLIC page
# ---------------------------------------------------------------------------
# ⚠ THE SAME SHAPE AS THE BANNER ROUTES ABOVE, deliberately: create the row,
# then put a file on it. The difference is that a file here may be a VIDEO, so
# there are two upload routes with two size limits and two allow-lists — see the
# note on `SHOWCASE_MAX_VIDEO_BYTES` in config.py for why one limit would not do.
class ShowcaseBody(BaseModel):
    """A new wall item. ⚠ ONLY `title` IS REQUIRED — everything else has a
    sensible absence, and the media arrives on its own route afterwards."""

    title: str = Field(..., min_length=1, max_length=showcase.TITLE_MAX)
    blurb: str = Field("", max_length=showcase.BLURB_MAX)
    # A workflow id the shell knows, so the viewer can offer "Use this workflow".
    # Validated in `showcase._clean` — empty means "no tag".
    workflow: str = Field("", max_length=64)
    aspect: str = Field(showcase.DEFAULT_ASPECT, max_length=8)
    rank: int = Field(0, ge=0, le=999)
    active: bool = True


class ShowcaseUpdate(BaseModel):
    """⚠ EVERY FIELD OPTIONAL, and the route sends `exclude_unset` — so a panel
    that only flips `active` does not have to resend the whole item, and a field
    added later cannot be blanked by an older client."""

    title: str | None = Field(None, min_length=1, max_length=showcase.TITLE_MAX)
    blurb: str | None = Field(None, max_length=showcase.BLURB_MAX)
    workflow: str | None = Field(None, max_length=64)
    aspect: str | None = Field(None, max_length=8)
    rank: int | None = Field(None, ge=0, le=999)
    active: bool | None = None


def _showcase_row(row: dict) -> dict:
    """One item plus the facts the panel needs that the customer is not told.

    ⚠ `active` IS RESOLVED HERE, not passed through — a row written before the
    field existed carries no key, and `all_items` reads that absence as live.
    The panel's switch has to be fed the same answer the page is.

    ⚠ AND `live` IS NOT `active`. An item can be switched on and still not be on
    the page because nothing has been uploaded to it yet; the panel says so out
    loud rather than leaving somebody hunting for a card that is not there.
    """
    return {
        **showcase.public_item(row),
        "active": bool(row.get("active", True)),
        "rank": int(row.get("rank") or 0),
        "has_media": bool(row.get("media_id")),
        "has_poster": bool(row.get("poster_id")),
        "live": bool(row.get("active", True) and row.get("media_id")),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


@router.get("/showcase")
def list_showcase(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Every wall item, live or not, with the limits the form has to respect."""
    return {
        "items": [_showcase_row(i) for i in showcase.all_items(fresh=True)],
        "max_public": showcase.MAX_PUBLIC,
        "aspects": list(showcase.ASPECTS),
        "image_max_px": showcase.IMAGE_MAX_PX,
        "allowed_image_types": list(showcase.ALLOWED_IMAGE_TYPES),
        "allowed_video_types": list(showcase.ALLOWED_VIDEO_TYPES),
        "max_video_bytes": config.SHOWCASE_MAX_VIDEO_BYTES,
        "max_image_bytes": config.MAX_UPLOAD_BYTES,
        "limits": {"title": showcase.TITLE_MAX, "blurb": showcase.BLURB_MAX},
    }


@router.post("/showcase", status_code=201)
def create_showcase(
    body: ShowcaseBody, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    try:
        row = showcase.create_item(body.model_dump(), actor=admin.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="showcase_created",
        item=row.get("id"),
        **events.request_context(request),
    )
    logger.info("%s created showcase item %s", admin.email, row.get("id"))
    return _showcase_row(row)


@router.patch("/showcase/{item_id}")
def update_showcase(
    item_id: str,
    body: ShowcaseUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    try:
        row = showcase.save_item(item_id, fields, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such showcase item.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="showcase_changed",
        item=item_id,
        fields=sorted(fields),
        **events.request_context(request),
    )
    return _showcase_row(row)


@router.delete("/showcase/{item_id}")
def remove_showcase(
    item_id: str, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """⚠ DELETE IS NOT HIDE, and the panel offers both. Hiding keeps the words
    and the file for the next campaign; deleting throws the video away."""
    try:
        showcase.delete_item(item_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such showcase item.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="showcase_deleted",
        item=item_id,
        **events.request_context(request),
    )
    logger.info("%s deleted showcase item %s", admin.email, item_id)
    return {"ok": True}


@router.post("/showcase/{item_id}/media")
async def upload_showcase_media(
    item_id: str,
    request: Request,
    media: UploadFile = File(..., description="The picture or the clip."),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Put a picture OR a video on a wall item. Replaces whatever was there.

    ⚠ ONE ROUTE FOR BOTH, and the CONTENT TYPE decides which. Two routes would
    mean the panel had to know in advance which kind of file somebody was about
    to pick — and the file picker is the thing that knows that, not the form.

    ⚠ THE SIZE LIMIT FOLLOWS THE KIND. A picture gets `MAX_UPLOAD_BYTES` like
    every other image in the app; a clip gets `SHOWCASE_MAX_VIDEO_BYTES`, which
    is deliberately much larger. Checking the cheap thing first — the type, then
    the length, then Pillow — is the same order the banner and logo routes use.
    """
    content_type = media.content_type or ""
    if content_type in showcase.ALLOWED_IMAGE_TYPES:
        kind = showcase.KIND_IMAGE
        cap = config.MAX_UPLOAD_BYTES
    elif content_type in showcase.ALLOWED_VIDEO_TYPES:
        kind = showcase.KIND_VIDEO
        cap = config.SHOWCASE_MAX_VIDEO_BYTES
    else:
        allowed = ", ".join(
            showcase.ALLOWED_IMAGE_TYPES + showcase.ALLOWED_VIDEO_TYPES
        )
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {content_type!r}. Allowed: {allowed}.",
        )

    contents = await media.read()
    if len(contents) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(contents)} bytes). Max is {cap}.",
        )

    aspect = ""
    blob = contents
    if kind == showcase.KIND_IMAGE:
        try:
            blob, aspect = showcase.normalise_image(contents)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ⚠ THE CLIP'S OWN FIRST FRAME, TAKEN HERE, BECAUSE THE BYTES ARE IN HAND.
    # Reported as "no thumbnail show in my upload video": the wall drew a bare
    # glyph because a video's still was a SECOND upload nobody knew to make. It
    # is grabbed now - see `showcase.poster_from_video` for why ffmpeg can do
    # this even though `ffprobe` is absent, and why a black frame is refused.
    #
    # ⚠ IT RUNS OFF THE EVENT LOOP. This handler is `async def`; ffmpeg against
    # a file this size would otherwise stall every other request in the process.
    #
    # ⚠ AND IT FAILS SOFT, ALWAYS. A missing thumbnail is a worse-looking card;
    # a failed upload is lost work. Whatever goes wrong in there, the clip that
    # was just stored stays stored.
    grabbed = None
    if kind == showcase.KIND_VIDEO:
        try:
            grabbed = await run_in_threadpool(
                showcase.poster_from_video, contents, content_type
            )
        except Exception:  # noqa: BLE001 - never fail an upload over a thumbnail
            logger.exception("showcase: poster grab failed for %s (ignored)", item_id)

    try:
        row = showcase.save_media(
            item_id, blob, kind, content_type=content_type, actor=admin.email
        )
        # ⚠ THE MEASURED RATIO WINS OVER THE TYPED ONE, for both kinds now.
        # Pillow read a picture's real shape; the grabbed frame IS the video's,
        # so neither has to keep the dropdown's guess. Leaving the guess on the
        # row is what cropped a portrait phone clip into a landscape slot.
        if not aspect and grabbed:
            aspect = grabbed[1]
        if aspect:
            row = showcase.save_item(item_id, {"aspect": aspect}, actor=admin.email)

        # ⚠ ONLY INTO AN EMPTY SLOT. A still an administrator chose by hand is a
        # better picture than frame one and must survive a re-upload of the clip;
        # `save_media` has already cleared the poster in the one case where the
        # old one is certainly wrong (a picture replacing a video).
        if grabbed and not row.get("poster_id"):
            row = showcase.save_poster(item_id, grabbed[0], actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such showcase item.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="showcase_media_uploaded",
        item=item_id,
        kind=kind,
        **events.request_context(request),
    )
    logger.info("%s put a %s on showcase item %s", admin.email, kind, item_id)
    return _showcase_row(row)


@router.post("/showcase/{item_id}/poster")
async def upload_showcase_poster(
    item_id: str,
    request: Request,
    image: UploadFile = File(..., description="The still shown before play."),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """The frame a video's card shows before anybody presses play.

    ⚠ THIS IS THE OVERRIDE NOW, NOT THE ONLY WAY IN. It used to carry a note
    saying the server could not pull frame one out of a clip because an
    `imageio-ffmpeg` install has no `ffprobe` - which was true and irrelevant,
    since extracting a frame is ffmpeg's job, not ffprobe's. `/media` grabs a
    still automatically on upload.

    ⚠ SO THE ROUTE STAYED, AND IT HAD TO. Frame one is a good default and a bad
    poster: the shot that sells the film is rarely the one it opens on, and a
    clip whose every probe came back black gets no poster at all. This is where
    a person overrules the machine, and what it writes is never overwritten by a
    later grab.
    """
    if image.content_type not in showcase.ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type {image.content_type!r}. "
            f"Allowed: {', '.join(showcase.ALLOWED_IMAGE_TYPES)}.",
        )
    contents = await image.read()
    if len(contents) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large ({len(contents)} bytes). "
            f"Max is {config.MAX_UPLOAD_BYTES}.",
        )
    try:
        webp, _aspect = showcase.normalise_image(contents)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        row = showcase.save_poster(item_id, webp, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such showcase item.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="showcase_poster_uploaded",
        item=item_id,
        **events.request_context(request),
    )
    return _showcase_row(row)


@router.delete("/showcase/{item_id}/poster")
def delete_showcase_poster(
    item_id: str, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Back to no still — the card draws the workflow glyph instead."""
    try:
        row = showcase.clear_poster(item_id, actor=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such showcase item.")
    events.record(
        events.TYPE_ADMIN_BRANDING_CHANGED,
        actor=admin.email,
        action="showcase_poster_removed",
        item=item_id,
        **events.request_context(request),
    )
    return _showcase_row(row)



# ---------------------------------------------------------------------------
# Subscriptions — who purchased what
# ---------------------------------------------------------------------------
class SubscriptionBody(BaseModel):
    """⚠ NO `amount` FIELD. The price is worked out on the SERVER from the tier
    and the offer, so what is recorded is what the pricing page would have
    quoted — a client-supplied amount is a number nobody checked."""

    email: str = Field(..., max_length=200)
    tier: str = Field(..., max_length=40)
    period: str = Field("monthly", pattern="^(monthly|yearly)$")
    months: int = Field(0, ge=0, le=120)
    code: str | None = Field(None, max_length=40)
    note: str = Field("", max_length=500)
    provider_ref: str = Field("", max_length=120)


@router.get("/subscriptions")
def list_subscriptions(
    admin: CurrentUser = Depends(require_admin),
    email: str | None = Query(None, max_length=200),
    status: str | None = Query(None, pattern="^(active|cancelled)$"),
    tier: str | None = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=config.ADMIN_MAX_PAGE),
    skip: int = Query(0, ge=0),
) -> dict:
    """Who purchased what, newest first."""
    rows = subscriptions.list_subscriptions(
        limit, skip, email=email, status=status, tier_id=tier
    )
    names = {t["id"]: t["name"] for t in billing.all_tiers().values()}
    revenue = subscriptions.recurring_revenue()
    return {
        "subscriptions": [{**r, "tier_name": names.get(r.get("tier_id"), r.get("tier_id"))} for r in rows],
        "total": subscriptions.count(status),
        "active": subscriptions.count(subscriptions.STATUS_ACTIVE),
        # ⚠ "RECORDED", NOT "EARNED". Nothing here has taken a payment; these are
        # the amounts an administrator typed in. Every surface must say so.
        "recorded_monthly": revenue["monthly"],
        "currency": revenue["currency"],
    }


@router.post("/subscriptions", status_code=201)
def create_subscription(
    body: SubscriptionBody,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Record a subscription by hand, and put the account on its tier.

    ⚠ THIS TAKES NO MONEY. It is the bookkeeping entry for a bank transfer or an
    invoice that has already been settled elsewhere — which is genuinely how
    early sales close, and it makes "who purchased" answerable weeks before a
    payment provider exists. Phase 6's webhooks write the same record with a
    different `source`, and no screen changes.

    ⚠ THE PRICE IS FROZEN ONTO THE RECORD HERE. `billing` is asked what the tier
    costs TODAY; from this moment the subscription carries that number and later
    edits to the tier cannot re-price this customer.
    """
    user = users.get_user_by_email(body.email)
    if user is None:
        raise HTTPException(status_code=404, detail=f"No such user: {body.email}")
    tier = billing.all_tiers().get(body.tier.strip().lower())
    if tier is None or tier.get("archived"):
        raise HTTPException(status_code=404, detail=f"No such tier: {body.tier}")

    price = tier.get("yearly" if body.period == "yearly" else "monthly", 0)

    # The offer, if one was quoted. A code that has expired between the quote and
    # this entry is refused rather than silently ignored — the customer was
    # promised a discount, and recording the full price instead is a wrong number
    # in the ledger.
    offer = None
    discount = 0
    if body.code:
        offer = offers.by_code(body.code)
        if not offer or not offers.is_live(offer) or not offers.applies_to(
            offer, tier["id"], body.period
        ):
            raise HTTPException(
                status_code=400,
                detail=f"The code {body.code} isn't valid for {tier['name']} ({body.period}).",
            )
        discount = offers.discount_on(offer, price)

    sub = subscriptions.record(
        user["email"],
        tier["id"],
        body.period,
        amount=max(0, price - discount),
        currency=config.BILLING_CURRENCY,
        months=body.months,
        offer_code=offer.get("code") if offer else None,
        offer_id=offer.get("id") if offer else None,
        discount=discount,
        note=body.note,
        provider_ref=body.provider_ref,
        actor=admin.email,
    )
    if offer:
        offers.redeem(offer["id"])

    users.set_tier(user["email"], tier["id"])
    users.set_tier_expiry(user["email"], sub["current_period_end"])
    # What they can reach has just changed, and `get_current_user` caches a
    # resolved user for 30s — same reasoning as disabling an account.
    auth.forget_cached_email(user["email"])

    events.record(
        events.TYPE_SUBSCRIPTION_STARTED,
        user["email"],
        actor=admin.email,
        tier=tier["id"],
        period=body.period,
        amount=sub["amount"],
        code=sub["offer_code"],
        **events.request_context(request),
    )
    logger.info(
        "%s recorded %s on %s (%s, %d)",
        admin.email, user["email"], tier["id"], body.period, sub["amount"],
    )
    return sub


@router.post("/subscriptions/{sub_id}/cancel")
def cancel_subscription(
    sub_id: str, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """End a subscription and drop the account back to the free tier NOW.

    ⚠ IT ENDS IMMEDIATELY, AND THE PANEL SAYS SO. "Cancel at period end" is the
    usual behaviour, and it needs something to run at that moment — this app has
    no scheduler. Offering a button that promises a future action nothing will
    perform is worse than a button that is honest about being immediate. To let
    somebody keep access until their period ends, simply leave the subscription
    alone: `tier_expires_at` already lapses on its own.
    """
    sub = subscriptions.cancel(sub_id, actor=admin.email)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"No such subscription: {sub_id}")

    email = sub.get("email") or ""
    # Only if this WAS the subscription behind their tier. Cancelling an old
    # record must not knock somebody off a newer one they are still paying for.
    still = subscriptions.active_for(email)
    if still is None:
        users.set_tier(email, billing.DEFAULT_TIER)
        users.set_tier_expiry(email, None)
    else:
        users.set_tier(email, still["tier_id"])
        users.set_tier_expiry(email, still["current_period_end"])
    auth.forget_cached_email(email)

    events.record(
        events.TYPE_SUBSCRIPTION_CANCELLED,
        email,
        actor=admin.email,
        tier=sub.get("tier_id"),
        **events.request_context(request),
    )
    logger.info("%s cancelled subscription %s (%s)", admin.email, sub_id, email)
    return sub


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------
@router.get("/events")
def list_events(
    admin: CurrentUser = Depends(require_admin),
    limit: int = Query(50, ge=1, le=config.ADMIN_MAX_PAGE),
    type: list[str] | None = Query(None),
    email: str | None = Query(None, max_length=200),
    days: int | None = Query(None, ge=1, le=365),
) -> dict:
    """The activity log, newest first.

    `type` may be repeated (`?type=user.login&type=user.registered`).
    """
    return {
        "events": events.list_events(
            limit,
            types=type,
            email=email,
            since=_iso_days_ago(days) if days else None,
        )
    }


@router.get("/meta")
def meta(admin: CurrentUser = Depends(require_admin)) -> dict:
    """What the panel's filters should offer, and who is asking.

    The client could hard-code the type list, and then it would be a second
    place to edit every time one is added. It reads it from here instead.
    """
    return {
        "you": admin.email,
        "event_types": list(events.KNOWN_TYPES),
        "job_kinds": [k.value for k in JobKind],
        "roles": list(users.ROLES),
        "max_page": config.ADMIN_MAX_PAGE,
    }


# ===========================================================================
# The ✨ AI Editor chat  (/admin/chat)
# ===========================================================================
# ⚠ **THREE OWNERS BEHIND ONE SCREEN, AND THE SCREEN DOES NOT OWN ANY OF THEM.**
# An operator thinks "the chat settings" is one page, so it is drawn as one page
# — but what it edits lives in three stores that each already had a reason to
# exist, and the Chat tab writes THROUGH them rather than keeping copies:
#
#     is it on, and for whom?      →  features.py, `cap.editor-chat`
#                                     (edited on the Features tab too; same row)
#     how many turns does a
#     tier get per month?          →  billing.py, that tier's `limits.chat_turns`
#                                     (edited on the Pricing tab too; same field)
#     how does it behave?          →  chat_settings.py
#
# The second one is the one that would have been easiest to get wrong. A
# `turn_limits` map in `chat_settings` would have been fewer lines and would have
# been a number the pricing page did not know about — and the first customer to
# hit it would have been reading a limit nobody advertised. See the rule at the
# top of `server/usage.py`.
class ChatSettingsBody(BaseModel):
    """How the chat behaves. Every field optional — a PATCH sends what changed.

    ⚠ NO VALIDATION HERE BEYOND TYPES. `chat_settings.clean()` clamps the numbers
    and checks the dock against its own list, because those bounds are the store's
    and a second copy in this file would be a second opinion about what is legal.
    """

    dock: str | None = None
    model: str | None = Field(None, max_length=120)
    planner_model: str | None = Field(None, max_length=120)
    transcript_keep: int | None = None
    max_turns_per_session: int | None = None
    shot_detail_limit: int | None = None
    # The model-call clock is owned by the Chat tab. Keep it in the request
    # model as well as in `chat_settings.EDITABLE`; otherwise Pydantic silently
    # drops the field and the panel reloads the old value after every save.
    turn_seconds: int | None = None
    # How solid the panel is drawn, as a percentage. Clamped by the store.
    opacity: int | None = None
    # How far the film underneath it is blurred, in px. Clamped by the store.
    blur: int | None = None
    # ⚠ WHAT A PROJECT KEEPS — the SAVED conversations, not the prompt. Asked for
    # outright: *"isme admin panel mai v daalo, mai limit set kar dunga, mai jitna
    # daalun wahi hona chahiye"*. They were `API_MAX_CHAT_*` environment variables
    # for half a day, which meant only somebody with a shell could change them.
    # Enforced in `server/editor_chat.py`; bounds are the store's, as above.
    # 0 chats means NO limit, not "fall back to the default".
    max_chats_per_project: int | None = None
    chat_history_keep: int | None = None
    max_chat_chars: int | None = None
    ask_on_spend: bool | None = None
    ask_on_destructive: bool | None = None
    allow_paid_passes: bool | None = None
    greeting: str | None = Field(None, max_length=240)


class ChatLimitsBody(BaseModel):
    """`{tier_id: turns or null}` — the monthly message count each tier gets.

    ⚠ `null` IS UNLIMITED, NOT ZERO, which is `usage.limit_of`'s rule for every
    counter in the app. Zero is a real and different answer: a tier that may not
    use the chat at all.
    """

    limits: dict[str, int | None] = Field(default_factory=dict)


def _chat_row() -> dict:
    """Everything the Chat tab draws, read from the three real owners."""
    feature_key = "cap.editor-chat"
    feature = (features.all_features() or {}).get(feature_key) or {}
    tiers = billing.all_tiers(fresh=True) or {}
    return {
        **chat_settings.admin_payload(),
        # ⚠ THE FEATURE ROW IS SHOWN, NOT EDITED HERE. The Chat tab draws its
        # status and links across; the Features tab is where it is changed. Two
        # editors for one row is how two screens end up disagreeing about which
        # one saved last.
        "feature": {
            "key": feature_key,
            "label": feature.get("label") or "AI Editor (the chat)",
            "status": feature.get("status") or "live",
            "rollout": feature.get("rollout") or {},
            "min_tier": feature.get("min_tier"),
        },
        # The turn allowance per tier, in ladder order so the screen reads like
        # the pricing page does.
        "tiers": [
            {
                "id": t.get("id"),
                "name": t.get("name") or t.get("id"),
                "rank": t.get("rank") or 0,
                "archived": bool(t.get("archived")),
                "turns": (t.get("limits") or {}).get("chat_turns"),
            }
            for t in sorted(tiers.values(), key=lambda r: r.get("rank") or 0)
        ],
    }


@router.get("/chat")
def get_chat(admin: CurrentUser = Depends(require_admin)) -> dict:
    """How the AI Editor chat is configured, from all three owners."""
    return _chat_row()


@router.patch("/chat")
def update_chat(
    body: ChatSettingsBody, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Change how the chat behaves. Lands on the next turn — no redeploy."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    chat_settings.save_settings(fields, actor=admin.email)
    events.record(
        events.TYPE_ADMIN_CHAT_CHANGED,
        actor=admin.email,
        action="settings",
        fields=sorted(fields),
        **events.request_context(request),
    )
    return _chat_row()


@router.patch("/chat/limits")
def update_chat_limits(
    body: ChatLimitsBody, request: Request, admin: CurrentUser = Depends(require_admin)
) -> dict:
    """Set each tier's monthly message allowance.

    ⚠ **IT WRITES THE TIER, NOT A CHAT-LOCAL TABLE.** `billing.save_tier` is the
    one door onto a tier's `limits`, so this change shows up on the Pricing tab,
    in `/billing/tiers`, on the pricing card and in `usage.check` — all at once,
    because there is only one number. A tier id that does not exist is IGNORED
    rather than refused: the screen may be a moment behind an archive, and losing
    the other four edits over it would be the worse answer.
    """
    known = billing.all_tiers(fresh=True) or {}
    touched = []
    for tier_id, value in (body.limits or {}).items():
        tier = known.get(tier_id)
        if not tier:
            logger.warning("[admin] chat limit for unknown tier %r — ignored.", tier_id)
            continue
        limits = dict(tier.get("limits") or {})
        # ⚠ `None` STAYS `None`. Coercing it to 0 would turn "unlimited" into
        # "banned" — the single most expensive typo available on this screen.
        limits["chat_turns"] = None if value is None else max(0, int(value))
        if (tier.get("limits") or {}).get("chat_turns") == limits["chat_turns"]:
            continue
        billing.save_tier(tier_id, {"limits": limits}, actor=admin.email)
        touched.append(tier_id)

    if touched:
        events.record(
            events.TYPE_ADMIN_CHAT_CHANGED,
            actor=admin.email,
            action="limits",
            tiers=touched,
            **events.request_context(request),
        )
    return _chat_row()
