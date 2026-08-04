"""
auth.py — Authentication routes and the current-user dependency.

Routes (mounted under /auth):
    POST /auth/register   Create an account (email + password) → returns a token
    POST /auth/login      Exchange email + password for a JWT access token
    GET  /auth/me         Return the authenticated user's profile

Protect any endpoint by adding `user = Depends(get_current_user)`.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from . import security, users

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Bearer scheme — clients send: Authorization: Bearer <token>
_bearer = HTTPBearer(auto_error=True)


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
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = security.decode_access_token(creds.credentials)
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

    return CurrentUser(email=user["email"])


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
