"""
auth.py — Authentication routes and the current-user dependency.

Routes (mounted under /auth):
    POST /auth/register   Create an account (email + password) → returns a token
    POST /auth/login      Exchange email + password for a JWT access token
    GET  /auth/me         Return the authenticated user's profile

Protect any endpoint by adding `user = Depends(get_current_user)`.
"""

import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from . import security, users

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Bearer scheme — clients send: Authorization: Bearer <token>
_bearer = HTTPBearer(auto_error=True)

# ---------------------------------------------------------------------------
# The resolved-user cache
# ---------------------------------------------------------------------------
# WHY THIS EXISTS: `get_current_user` runs on EVERY request, and its user lookup
# is a remote Mongo Atlas `find_one`. Opening a sixty-panel animatic fires one
# project request plus a thumbnail request per frame plus a blob per video and
# audio track — a hundred-odd requests, each previously paying its own Atlas
# round trip just to learn the same unchanged fact. The burst is what made
# opening a project feel slow; collapsing it to one lookup is the single largest
# win available without changing the auth model.
#
# ⚠ SUCCESSES ONLY, AND KEYED ON THE RAW TOKEN. A rejected token is never
# cached, so an expired or forged one is re-validated every time and can never
# be served from here. The token string is the key rather than the email so that
# a token which stops decoding stops being found, full stop.
#
# The TTL is the staleness budget for exactly one fact: `disabled`. Thirty
# seconds means a disabled account is locked out within half a minute, which is
# the same order as the tab's own polling, while still folding an entire
# project-open burst into a single lookup.
_USER_CACHE_TTL_S = 30.0
_USER_CACHE_MAX = 512
_user_cache: dict[str, tuple[float, "CurrentUser"]] = {}
_user_cache_lock = threading.Lock()


def _cached_user(token: str) -> "CurrentUser | None":
    """The user this token resolved to recently, or None to go and look."""
    now = time.monotonic()
    with _user_cache_lock:
        hit = _user_cache.get(token)
        if hit is None:
            return None
        expires_at, user = hit
        if expires_at <= now:
            # Drop it here rather than leaving it for the prune: an expired
            # entry that stays readable is a lock-out that never lands.
            _user_cache.pop(token, None)
            return None
        return user


def _remember_user(token: str, user: "CurrentUser") -> None:
    """Cache a SUCCESSFUL resolution, pruning expired entries on the way."""
    now = time.monotonic()
    with _user_cache_lock:
        if len(_user_cache) >= _USER_CACHE_MAX:
            for key in [k for k, (exp, _) in _user_cache.items() if exp <= now]:
                _user_cache.pop(key, None)
            # Still full means every entry is live — a genuinely busy server,
            # not a leak. Clearing beats growing without bound; the cost is one
            # extra lookup per token, which is where we started.
            if len(_user_cache) >= _USER_CACHE_MAX:
                _user_cache.clear()
        _user_cache[token] = (now + _USER_CACHE_TTL_S, user)


def forget_cached_email(email: str) -> None:
    """Drop every cached token belonging to one account, immediately.

    ⚠ CALL THIS WHENEVER AN ACCOUNT STOPS BEING VALID. The cache is keyed by
    token, so a deleted account's tokens would otherwise go on being accepted
    for the rest of the TTL — the account is gone from the store and the API
    still answers to it. The scan is over at most `_USER_CACHE_MAX` entries and
    happens on a path that already writes to the database.
    """
    with _user_cache_lock:
        for key in [k for k, (_, u) in _user_cache.items() if u.email == email]:
            _user_cache.pop(key, None)


def forget_cached_users() -> None:
    """Drop every cached resolution. For tests, and for any future admin path
    that disables an account and wants the lock-out to be immediate."""
    with _user_cache_lock:
        _user_cache.clear()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: EmailStr


# What a profile holds, and why each field earns its place. Kept deliberately
# small: a field nobody reads is a field that goes stale and lies.
#
#   Identity      — who you are. `full_name` is the real name; `display_name` is
#                   what the UI shows (people often want to be "Manish", not
#                   "Manish Shankar"). `email` is the LOGIN, so it is read-only
#                   here — changing it is an account migration, not an edit.
#   Work          — this is a studio tool used by teams; `company` and `role`
#                   are what make a shared board attributable to a person.
#   Creative      — the storyboard form asks for style / aspect / genre EVERY
#                   time. Holding the usual answers here lets the form arrive
#                   pre-filled. This is the part that actually saves clicks.
#   Locale        — `timezone` so "created 2 hours ago" means the user's hours.
#   Read-only     — `created_at`, `disabled`: shown, never edited by the user.
#
# Deliberately NOT here: avatar uploads (a file-storage feature, and the initial
# already works), phone numbers and addresses (this app has no use for them —
# collecting personal data nothing reads is a liability, not a feature), and
# plan/credits, which belong to billing rather than to the person.
class UserProfile(BaseModel):
    """The authenticated user's profile, as returned by GET /auth/me."""

    email: EmailStr
    # --- identity ---
    full_name: str = ""
    display_name: str = ""
    # --- work ---
    company: str = ""
    role: str = ""
    # --- creative defaults (prefill the storyboard form) ---
    default_style: str = ""
    default_aspect_ratio: str = ""
    default_genre: str = ""
    # --- locale ---
    timezone: str = ""
    # --- read-only ---
    created_at: str | None = None
    disabled: bool = False


# Fields a user may edit about themselves. Anything absent is left alone, so a
# form that only touches one section can't blank the others.
class UserProfileUpdate(BaseModel):
    """Body for PATCH /auth/me."""

    full_name: str | None = Field(None, max_length=120)
    display_name: str | None = Field(None, max_length=60)
    company: str | None = Field(None, max_length=120)
    role: str | None = Field(None, max_length=60)
    default_style: str | None = Field(None, max_length=60)
    default_aspect_ratio: str | None = Field(None, max_length=20)
    default_genre: str | None = Field(None, max_length=60)
    timezone: str | None = Field(None, max_length=60)


class PasswordChangeRequest(BaseModel):
    """Body for POST /auth/me/password.

    The CURRENT password is required even though the caller already holds a
    valid token: a token can be a borrowed laptop, and a password change locks
    the real owner out.
    """

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class CurrentUser(BaseModel):
    """The authenticated principal passed to protected endpoints."""

    email: EmailStr


class ApiKeyRequest(BaseModel):
    provider: str = Field(..., description="3D provider: 'meshy' or 'tripo'.")
    api_key: str = Field(..., min_length=8, max_length=256)


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------
def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> CurrentUser:
    """Validate the bearer token and return the current user.

    Defined as a sync `def` so FastAPI runs it (and its blocking Mongo lookup)
    in a worker thread rather than on the event loop.

    A successful resolution is cached for `_USER_CACHE_TTL_S` — see the cache
    block above for why, and for what that costs.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = creds.credentials
    cached = _cached_user(token)
    if cached is not None:
        return cached

    try:
        payload = security.decode_access_token(token)
    except security.TokenError:
        raise unauthorized

    email = payload.get("sub")
    if not email:
        raise unauthorized

    user = users.get_user_by_email(email)
    if user is None:
        raise unauthorized
    if user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled."
        )

    current = CurrentUser(email=user["email"])
    _remember_user(token, current)
    return current


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest):
    """Create a new account and return an access token."""
    password_hash = security.hash_password(req.password)
    try:
        user = users.create_user(req.email, password_hash)
    except users.DuplicateUser:
        raise HTTPException(status_code=409, detail="Email already registered.")

    token = security.create_access_token(subject=user["email"])
    logger.info("Registered new user: %s", user["email"])
    return TokenResponse(access_token=token, email=user["email"])


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    """Exchange email + password for a JWT access token."""
    user = users.get_user_by_email(req.email)
    if user is None or not security.verify_password(req.password, user["password_hash"]):
        # Same message for both cases — don't reveal which emails exist.
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if user.get("disabled"):
        raise HTTPException(status_code=403, detail="Account is disabled.")

    token = security.create_access_token(subject=user["email"])
    return TokenResponse(access_token=token, email=user["email"])


@router.get("/me", response_model=UserProfile)
def me(current: CurrentUser = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    user = users.get_user_by_email(current.email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return _to_profile(user)


def _to_profile(user: dict) -> UserProfile:
    """User document → UserProfile. Missing fields become "" rather than null,
    so the client can bind them straight to inputs without null-checking each."""
    return UserProfile(
        email=user["email"],
        full_name=user.get("full_name") or "",
        display_name=user.get("display_name") or "",
        company=user.get("company") or "",
        role=user.get("role") or "",
        default_style=user.get("default_style") or "",
        default_aspect_ratio=user.get("default_aspect_ratio") or "",
        default_genre=user.get("default_genre") or "",
        timezone=user.get("timezone") or "",
        created_at=user.get("created_at"),
        disabled=user.get("disabled", False),
    )


@router.patch("/me", response_model=UserProfile)
def update_me(
    body: UserProfileUpdate,
    current: CurrentUser = Depends(get_current_user),
):
    """Update the caller's own profile.

    Partial: only the fields present in the body are written, so the identity
    form and the creative-defaults form can save independently. Values are
    trimmed; the email is NOT editable here (it is the login).
    """
    fields = {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in body.model_dump(exclude_unset=True).items()
    }
    if not users.update_profile(current.email, fields):
        raise HTTPException(status_code=404, detail="User not found.")
    user = users.get_user_by_email(current.email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    logger.info("Updated profile for %s (%s)", current.email, ", ".join(sorted(fields)) or "no fields")
    return _to_profile(user)


@router.post("/me/password", status_code=204)
def change_password(
    body: PasswordChangeRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Change the caller's password, verifying the current one first.

    Holding a valid token isn't enough: an unattended session must not be able
    to lock the real owner out of their account.
    """
    user = users.get_user_by_email(current.email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if not security.verify_password(body.current_password, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Your current password is incorrect.")
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=400, detail="The new password must be different from the current one."
        )
    users.update_password(current.email, security.hash_password(body.new_password))
    logger.info("Password changed for %s", current.email)
    return None


@router.delete("/me", status_code=204)
def delete_me(current: CurrentUser = Depends(get_current_user)):
    """Permanently delete the authenticated user's own account."""
    deleted = users.delete_user(current.email)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    # The account is gone from the store; its tokens must stop working NOW, not
    # when the resolved-user cache happens to expire.
    forget_cached_email(current.email)
    logger.info("Deleted user account: %s", current.email)
    return None


# ---------------------------------------------------------------------------
# 3D provider API keys (saved on the user profile)
# ---------------------------------------------------------------------------
@router.get("/me/api-keys")
def list_api_keys(current: CurrentUser = Depends(get_current_user)) -> dict:
    """Return which providers have a saved key, e.g. {"meshy": true}.

    Never returns the raw keys — only whether each provider is configured.
    """
    return users.get_saved_providers(current.email)


@router.put("/me/api-keys", status_code=204)
def save_api_key(req: ApiKeyRequest, current: CurrentUser = Depends(get_current_user)):
    """Save (or replace) a 3D provider API key on the user's profile."""
    users.set_api_key(current.email, req.provider.strip().lower(), req.api_key.strip())
    return None


@router.delete("/me/api-keys/{provider}", status_code=204)
def delete_api_key(provider: str, current: CurrentUser = Depends(get_current_user)):
    """Remove a saved provider key from the user's profile."""
    users.delete_api_key(current.email, provider.strip().lower())
    return None
