"""cancel.py — "stop this run" flags, shared by both workflows.

Both pipelines are long, sequential and expensive: a character run draws a
turnaround sheet per part, a storyboard draws an image per shot. When the first
outputs come back wrong the user needs to stop SPENDING generations, so each
pipeline checks these flags at its own natural boundary and skips whatever it
hasn't started yet.

What stopping can and cannot do: an HTTP request already in flight cannot be
un-sent, so the call in progress finishes. Everything after it is skipped.

Flags live in this process, next to the worker pool that reads them — so a
multi-process deployment would need to move them to the job store instead.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cancelled: set[str] = set()


def request_cancel(job_id: str) -> None:
    """Ask a running job to stop after the work already in flight."""
    if not job_id:
        return
    with _lock:
        _cancelled.add(job_id)
    logger.info("[job %s] STOP requested", job_id)


def is_cancelled(job_id: str) -> bool:
    """True while a stop is pending for this job."""
    if not job_id:
        return False
    with _lock:
        return job_id in _cancelled


def clear_cancel(job_id: str) -> None:
    """Drop the flag so the NEXT run of this job isn't killed by a stale one."""
    if not job_id:
        return
    with _lock:
        _cancelled.discard(job_id)
