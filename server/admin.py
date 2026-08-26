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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from . import auth, billing, config, events, features, offers, subscriptions, usage, users
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
