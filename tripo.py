"""
tripo.py — Submit character part views to Tripo.ai for 3D model generation.

⚠️  UNVERIFIED: this client follows Tripo's public v2 OpenAPI docs but has not
    been tested against the live API. Adjust endpoints/fields if Tripo rejects
    a request. Meshy (meshy.py) is the tested path.

Flow: Submit up to 4 view image URLs → poll task → return model download URLs.

API: https://api.tripo3d.ai/v2/openapi
Auth: Bearer token from TRIPO_API_KEY env var, or passed in by the user.
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TRIPO_API_BASE = "https://api.tripo3d.ai/v2/openapi"
POLL_INTERVAL_SECONDS = 15
MAX_POLL_MINUTES = 30

# Tripo multiview order is front, left, back, right. We only have four
# turnaround views (front, left, three_quarter, back) in this pipeline order:
# [front, left, three_quarter, back]. We map them best-effort onto Tripo's slots.
_TRIPO_SLOT_ORDER = ["front", "left", "back", "right"]


def _get_headers(api_key: str | None = None) -> dict:
    key = api_key or os.environ.get("TRIPO_API_KEY")
    if not key:
        raise ValueError(
            "Tripo API key not found. Set TRIPO_API_KEY in your .env file "
            "or pass it directly."
        )
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def submit_to_tripo(
    part_name: str,
    image_urls: list[str],
    api_key: str | None = None,
) -> str | None:
    """Submit view images to Tripo multiview-to-3D. Returns a task id or None."""
    headers = _get_headers(api_key)

    # Build file objects from public URLs. Pad/truncate to 4 slots.
    files = []
    for i in range(4):
        url = image_urls[i] if i < len(image_urls) else ""
        files.append({"type": "png", "url": url} if url else None)

    payload = {"type": "multiview_to_model", "files": files}

    logger.info("[%s] Submitting %d images to Tripo…", part_name, len(image_urls))
    try:
        resp = requests.post(
            f"{TRIPO_API_BASE}/task", json=payload, headers=headers, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        # Tripo wraps results as {"code": 0, "data": {"task_id": "..."}}
        task_id = (data.get("data") or {}).get("task_id") or data.get("result")
        logger.info("[%s] Tripo task submitted: %s", part_name, task_id)
        return task_id
    except requests.RequestException as e:
        logger.error("[%s] Tripo submission failed: %s", part_name, str(e))
        return None


def poll_tripo_task(
    task_id: str, part_name: str = "unknown", api_key: str | None = None
) -> dict | None:
    """Poll a Tripo task until done; return {task_id, status, model_urls} or None."""
    headers = _get_headers(api_key)
    max_polls = int((MAX_POLL_MINUTES * 60) / POLL_INTERVAL_SECONDS)

    logger.info("[%s] Polling Tripo task %s (max %d min)…", part_name, task_id, MAX_POLL_MINUTES)
    for i in range(1, max_polls + 1):
        try:
            resp = requests.get(
                f"{TRIPO_API_BASE}/task/{task_id}", headers=headers, timeout=30
            )
            resp.raise_for_status()
            data = (resp.json() or {}).get("data", {})
            status = str(data.get("status", "unknown")).lower()
            progress = data.get("progress", 0)

            if status in ("success", "succeeded"):
                output = data.get("output", {}) or {}
                # Collect any URL-looking outputs (pbr_model / model / base_model).
                model_urls = {
                    k: v for k, v in output.items()
                    if isinstance(v, str) and v.startswith("http")
                }
                logger.info("[%s] Tripo SUCCEEDED. Models: %s", part_name, model_urls)
                return {"task_id": task_id, "status": "SUCCEEDED", "model_urls": model_urls}

            if status in ("failed", "cancelled", "banned", "expired", "error"):
                logger.error("[%s] Tripo task ended: %s", part_name, status)
                return None

            logger.info("[%s] Tripo %s — %s%% (poll %d/%d)", part_name, status, progress, i, max_polls)
            time.sleep(POLL_INTERVAL_SECONDS)
        except requests.RequestException as e:
            logger.error("[%s] Tripo poll error: %s", part_name, str(e))
            time.sleep(POLL_INTERVAL_SECONDS)

    logger.error("[%s] Tripo task timed out after %d minutes.", part_name, MAX_POLL_MINUTES)
    return None


def submit_and_wait(
    part_name: str,
    image_urls: list[str],
    target_format: str = "glb",  # kept for signature parity with meshy
    api_key: str | None = None,
) -> dict | None:
    """Submit + poll in one call. Returns {task_id, status, model_urls} or None."""
    task_id = submit_to_tripo(part_name, image_urls, api_key)
    if not task_id:
        return None
    return poll_tripo_task(task_id, part_name, api_key)
