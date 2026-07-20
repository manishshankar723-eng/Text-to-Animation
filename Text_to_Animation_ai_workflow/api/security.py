"""
security.py — Password hashing and JWT creation/verification.

Passwords are hashed with bcrypt (no plaintext ever stored).
Access tokens are signed JWTs (HS256 by default).
"""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from . import config

logger = logging.getLogger(__name__)

# Resolve the signing secret. Fall back to an insecure dev key if unset so
# local runs work, but flag it loudly — never rely on this in production.
_DEV_SECRET = "dev-insecure-change-me-in-production"
if config.JWT_SECRET:
    _SECRET = config.JWT_SECRET
else:
    _SECRET = _DEV_SECRET
    config.JWT_SECRET_IS_DEV = True
    logger.warning(
        "JWT_SECRET is not set — using an INSECURE dev secret. "
        "Set JWT_SECRET in your .env before deploying."
    )

# bcrypt only hashes the first 72 bytes of a password.
_BCRYPT_MAX_BYTES = 72


class TokenError(Exception):
    """Raised when a JWT is missing, expired, or invalid."""


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def _truncate(password: str) -> bytes:
    """Encode + truncate to bcrypt's 72-byte limit."""
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Return a bcrypt hash (str) for the given plaintext password."""
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(_truncate(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(subject: str, extra_claims: dict | None = None) -> str:
    """Create a signed JWT for `subject` (the user's email/id).

    Returns the encoded token string.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _SECRET, algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode + validate a JWT. Raises TokenError if invalid/expired."""
    try:
        return jwt.decode(token, _SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("Token has expired.") from e
    except jwt.PyJWTError as e:
        raise TokenError("Invalid token.") from e
