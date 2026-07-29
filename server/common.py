"""
common.py — Helpers shared by more than one route module.

These lived in main.py until the animatics router needed them too; importing
them from main would have made the two modules import each other. Nothing here
knows about a specific workflow.
"""

import os

from fastapi import HTTPException

from . import config
from .auth import CurrentUser
from .jobs import get_store
from .schemas import Job


def get_owned_job(job_id: str, current: CurrentUser) -> Job:
    """Fetch a job, returning 404 if it doesn't exist OR isn't owned by the caller.

    Using 404 (not 403) for the not-owned case avoids leaking which job ids exist.
    """
    job = get_store().get(job_id)
    if job is None or job.owner != current.email:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


def board_dir(job_id: str) -> str:
    """Where a storyboard's generated panel PNGs live."""
    return os.path.join(config.OUTPUT_DIR, "_storyboards", job_id)


def variants_of(result: dict) -> tuple[list[dict], int]:
    """Return (variants, active_index) for a storyboard result.

    Older results (pre-restyle) have no `variants` list — synthesise a single
    variant 0 from the flat panels/style so every code path can treat boards
    uniformly.
    """
    result = result or {}
    variants = result.get("variants")
    if not variants:
        variants = [
            {
                "style": result.get("style"),
                "panels": result.get("panels") or [],
                "ok_count": result.get("ok_count", 0),
            }
        ]
    active = int(result.get("active_variant", 0) or 0)
    if active < 0 or active >= len(variants):
        active = 0
    return variants, active


def panel_path(storyboard_id: str, index: int, variant: int = 0) -> str:
    """Absolute-ish path to one drawn panel of a board's style variant."""
    subdir = "" if not variant else f"v{variant}"
    return os.path.join(board_dir(storyboard_id), subdir, f"panel_{index:02d}.png")
