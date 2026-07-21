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


class UserProfile(BaseModel):
    email: EmailStr
    created_at: str | None = None
    disabled: bool = False


class CurrentUser(BaseModel):
    """The authenticated principal passed to protected endpoints."""

    email: EmailStr


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
    return UserProfile(
        email=user["email"],
        created_at=user.get("created_at"),
        disabled=user.get("disabled", False),
    )


@router.delete("/me", status_code=204)
def delete_me(current: CurrentUser = Depends(get_current_user)):
    """Permanently delete the authenticated user's own account."""
    deleted = users.delete_user(current.email)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    logger.info("Deleted user account: %s", current.email)
    return None
