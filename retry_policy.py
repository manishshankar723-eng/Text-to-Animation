"""
retry_policy.py — When to retry a Google AI call, and how long to wait.

Extracted from gemini_client.py so the video backend (video_client.py) obeys the
SAME rules rather than growing a second, drifting copy. The policy was tuned
against real quota failures — see the notes on each constant before changing it.

Nothing here talks to the network: these are pure functions over an exception.
"""

import os
import random
import re

# Total attempts per call. Quota (429) errors get the full budget because a
# per-minute quota REFILLS — waiting through it is the whole point.
MAX_RETRIES = max(1, int(os.environ.get("IMAGE_MAX_RETRIES", "5")))
INITIAL_BACKOFF_SECONDS = 3  # generic transient ladder: 3s, 6s, 12s, 24s…
# Quota errors need a per-minute refill, so they wait longer between tries.
QUOTA_BACKOFF_SECONDS = 15   # 15s, 30s, then capped — catches a minute refill
QUOTA_BACKOFF_CAP = 50.0

# ---------------------------------------------------------------------------
# Error classification — only retry what can actually succeed on a retry
# ---------------------------------------------------------------------------
RETRYABLE_MARKERS = (
    "429", "resource_exhausted", "rate limit", "quota",
    "500", "internal error",
    "503", "unavailable", "overloaded",
    "504", "deadline", "timeout",
)
# Permanent: retrying burns time and never succeeds.
PERMANENT_MARKERS = (
    "400", "invalid argument", "invalid_argument",
    "401", "403", "permission denied", "unauthenticated",
    "404", "not found",
    "safety", "blocked", "prohibited_content",
)

QUOTA_MARKERS = ("429", "resource_exhausted", "quota", "rate limit")


def is_retryable(error: Exception) -> bool:
    """True if re-issuing this request has a real chance of succeeding."""
    text = str(error).lower()
    if any(m in text for m in PERMANENT_MARKERS):
        return False
    return any(m in text for m in RETRYABLE_MARKERS)


def is_quota_error(error: Exception) -> bool:
    """True for a rate/quota error (429 / RESOURCE_EXHAUSTED) specifically.

    These REFILL over time, so they deserve longer, more patient backoff than a
    one-off 500/503 blip.
    """
    return any(m in str(error).lower() for m in QUOTA_MARKERS)


def retry_after_seconds(error: Exception) -> float | None:
    """Honour a server-provided Retry-After / retryDelay hint when present."""
    match = re.search(r"retry[- _]?(?:after|delay)\D{0,10}(\d+(?:\.\d+)?)", str(error), re.I)
    if match:
        try:
            return min(float(match.group(1)), 120.0)
        except ValueError:
            return None
    return None


def backoff_delay(attempt: int, error: Exception | None = None) -> float:
    """Backoff with jitter. Quota errors wait longer (they refill per-minute);
    a server Retry-After/retryDelay hint always wins when present."""
    if error is not None:
        hinted = retry_after_seconds(error)
        if hinted is not None:
            return hinted
        if is_quota_error(error):
            base = min(QUOTA_BACKOFF_SECONDS * (2 ** (attempt - 1)), QUOTA_BACKOFF_CAP)
            return base * random.uniform(0.8, 1.2)
    base = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
    return base * random.uniform(0.5, 1.5)
