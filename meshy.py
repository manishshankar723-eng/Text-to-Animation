"""
meshy.py — Submit character part views to Meshy.ai for 3D model generation.

Flow: Submit 4 view images → Poll until done → Return download link.

API: POST https://api.meshy.ai/openapi/v1/multi-image-to-3d
Auth: Bearer token from MESHY_API_KEY env var (Phase 1 CLI).
      In Phase 2/3, the user provides their own key via the frontend.

Note: Meshy API-created models auto-delete after ~3 days and appear
      in the API area, not the normal workspace.
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MESHY_API_BASE = "https://api.meshy.ai/openapi/v1"
POLL_INTERVAL_SECONDS = 15
MAX_POLL_MINUTES = 30


def _get_headers(api_key: str | None = None) -> dict:
    """Build auth headers. Uses provided key or falls back to env var."""
    key = api_key or os.environ.get("MESHY_API_KEY")
    if not key:
        raise ValueError(
            "Meshy API key not found. Set MESHY_API_KEY in your .env file "
            "or pass it directly."
        )
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def submit_to_meshy(
    part_name: str,
    image_urls: list[str],
    target_format: str = "fbx",
    api_key: str | None = None,
) -> str | None:
    """
    Submit 4 view images of a part to Meshy Multi-Image-to-3D.

    Args:
        part_name: Name of the part (for logging), e.g. "hair"
        image_urls: List of 4 public URLs (front, left, three_quarter, back)
        target_format: Output format (default "fbx")
        api_key: Optional Meshy API key. Falls back to MESHY_API_KEY env var.

    Returns:
        Meshy task ID, or None if submission failed.
    """
    headers = _get_headers(api_key)

    payload = {
        "image_urls": image_urls,
        "target_formats": [target_format],
        "should_texture": True,
    }

    logger.info("[%s] Submitting %d images to Meshy...", part_name, len(image_urls))

    try:
        resp = requests.post(
            f"{MESHY_API_BASE}/multi-image-to-3d",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        task_id = data.get("result")
        logger.info("[%s] Meshy task submitted: %s", part_name, task_id)
        return task_id

    except requests.RequestException as e:
        logger.error("[%s] Meshy submission failed: %s", part_name, str(e))
        return None


def poll_meshy_task(
    task_id: str,
    part_name: str = "unknown",
    api_key: str | None = None,
) -> dict | None:
    """
    Poll a Meshy task until it completes, then return the result with download URLs.

    Args:
        task_id: The Meshy task ID from submit_to_meshy().
        part_name: Name of the part (for logging).
        api_key: Optional Meshy API key.

    Returns:
        Dict with task result including model_urls, or None if failed/timed out.
    """
    headers = _get_headers(api_key)
    max_polls = int((MAX_POLL_MINUTES * 60) / POLL_INTERVAL_SECONDS)

    logger.info("[%s] Polling Meshy task %s (max %d min)...", part_name, task_id, MAX_POLL_MINUTES)

    for i in range(1, max_polls + 1):
        try:
            resp = requests.get(
                f"{MESHY_API_BASE}/multi-image-to-3d/{task_id}",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "UNKNOWN")
            progress = data.get("progress", 0)

            if status == "SUCCEEDED":
                model_urls = data.get("model_urls", {})
                logger.info("[%s] Meshy task SUCCEEDED! Model URLs: %s", part_name, model_urls)
                return {
                    "task_id": task_id,
                    "status": status,
                    "model_urls": model_urls,
                }

            elif status == "FAILED":
                error_msg = data.get("task_error", {}).get("message", "Unknown error")
                logger.error("[%s] Meshy task FAILED: %s", part_name, error_msg)
                return None

            elif status in ("PENDING", "IN_PROGRESS"):
                logger.info(
                    "[%s] Meshy task %s — progress: %d%% (poll %d/%d)",
                    part_name, status, progress, i, max_polls,
                )
                time.sleep(POLL_INTERVAL_SECONDS)

            else:
                logger.warning("[%s] Unknown Meshy status: %s", part_name, status)
                time.sleep(POLL_INTERVAL_SECONDS)

        except requests.RequestException as e:
            logger.error("[%s] Meshy poll error: %s", part_name, str(e))
            time.sleep(POLL_INTERVAL_SECONDS)

    logger.error("[%s] Meshy task timed out after %d minutes.", part_name, MAX_POLL_MINUTES)
    return None


def submit_and_wait(
    part_name: str,
    image_urls: list[str],
    target_format: str = "fbx",
    api_key: str | None = None,
) -> dict | None:
    """
    Convenience: submit + poll in one call.

    Returns:
        Dict with task_id, status, model_urls — or None if anything failed.
    """
    task_id = submit_to_meshy(part_name, image_urls, target_format, api_key)
    if not task_id:
        return None

    return poll_meshy_task(task_id, part_name, api_key)
