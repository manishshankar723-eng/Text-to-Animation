"""
worker.py — Runs pipeline jobs off the request thread.

A single ThreadPoolExecutor executes jobs so HTTP requests return immediately
with a job_id. Each job updates the JobStore as it moves through
queued → running → succeeded/failed.

The pipeline itself (run_pipeline) is synchronous and I/O-bound (network calls
to Gemini/Meshy/GCS), so a thread pool is a good fit — no async rewrite needed.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from . import config
from .jobs import get_store

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(
    max_workers=config.MAX_WORKERS, thread_name_prefix="pipeline"
)


def shutdown():
    """Stop accepting new jobs and let running ones finish."""
    _executor.shutdown(wait=False)


def submit_generate_job(job_id: str, pipeline_kwargs: dict):
    """Enqueue a full pipeline run identified by an existing job record."""
    _executor.submit(_run_generate, job_id, pipeline_kwargs)


def submit_meshy_job(job_id: str, part_urls: dict, api_key: str | None, provider: str = "meshy"):
    """Enqueue a standalone 3D submission (Meshy or Tripo) for generated parts."""
    _executor.submit(_run_meshy, job_id, part_urls, api_key, provider)


# ---------------------------------------------------------------------------
# Job bodies (run inside worker threads)
# ---------------------------------------------------------------------------
def _run_generate(job_id: str, pipeline_kwargs: dict):
    # Imported here so importing the API package never triggers the heavy
    # pipeline import chain (Gemini/Vertex client, etc.).
    from pipeline import run_pipeline

    store = get_store()
    store.mark_running(job_id)
    logger.info("[job %s] pipeline started: %s", job_id, pipeline_kwargs.get("character_name"))

    def _progress(update: dict):
        """Persist live progress (and partial URLs) so clients can poll it."""
        partial_urls = update.pop("urls", None)
        fields = {"progress": update}
        # Surface parts as they finish so the gallery fills in one-by-one.
        if partial_urls:
            fields["result"] = {"urls": partial_urls}
        try:
            store.update(job_id, **fields)
        except Exception:  # noqa: BLE001 — progress writes must not kill the job
            logger.debug("[job %s] progress update failed (ignored)", job_id, exc_info=True)

    try:
        result = run_pipeline(**pipeline_kwargs, progress_cb=_progress)

        if isinstance(result, dict) and "error" in result:
            store.mark_failed(job_id, str(result["error"]))
            logger.error("[job %s] pipeline reported error: %s", job_id, result["error"])
            return

        store.mark_succeeded(job_id, result)
        logger.info("[job %s] pipeline succeeded.", job_id)

    except Exception as e:  # noqa: BLE001 — record any failure on the job
        store.mark_failed(job_id, f"{type(e).__name__}: {e}")
        logger.exception("[job %s] pipeline crashed.", job_id)


def _run_meshy(job_id: str, part_urls: dict, api_key: str | None, provider: str = "meshy"):
    # Pick the 3D backend. Meshy is tested; Tripo is unverified.
    if provider == "tripo":
        from tripo import submit_and_wait
    else:
        from meshy import submit_and_wait

    store = get_store()
    store.mark_running(job_id)
    logger.info("[job %s] %s submission started for parts: %s", job_id, provider, list(part_urls))

    results: dict = {}
    errors: list[str] = []

    try:
        for part_name, view_urls in part_urls.items():
            result = submit_and_wait(part_name, view_urls, api_key=api_key)
            if result:
                results[part_name] = result
            else:
                errors.append(part_name)

        if results:
            summary = {"meshy": results}
            if errors:
                summary["failed_parts"] = errors
            store.mark_succeeded(job_id, summary)
            logger.info("[job %s] Meshy done. ok=%s failed=%s", job_id, list(results), errors)
        else:
            store.mark_failed(job_id, f"Meshy failed for all parts: {errors}")

    except Exception as e:  # noqa: BLE001
        store.mark_failed(job_id, f"{type(e).__name__}: {e}")
        logger.exception("[job %s] Meshy submission crashed.", job_id)
