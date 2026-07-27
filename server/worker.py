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
from .schemas import JobStatus

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


def submit_regenerate_job(job_id: str, mode: str, kwargs: dict):
    """Enqueue an async regeneration of a single part ('part') or view ('view').

    Runs off-request so a long (~30–60s) image call can't be killed by a
    connection drop / server restart — the client just polls the same job.
    """
    _executor.submit(_run_regenerate, job_id, mode, kwargs)


def submit_storyboard_job(job_id: str, kwargs: dict):
    """Enqueue storyboard panel generation (Script → Storyboard, Stage D)."""
    _executor.submit(_run_storyboard, job_id, kwargs)


def submit_restyle_job(job_id: str, kwargs: dict):
    """Enqueue a re-style of the whole board into a NEW style variant."""
    _executor.submit(_run_restyle, job_id, kwargs)


# ---------------------------------------------------------------------------
# Job bodies (run inside worker threads)
# ---------------------------------------------------------------------------
def _run_generate(job_id: str, pipeline_kwargs: dict):
    # Imported here so importing the API package never triggers the heavy
    # pipeline import chain (Gemini/Vertex client, etc.).
    from cancel import clear_cancel, is_cancelled
    from pipeline import run_pipeline

    store = get_store()
    store.mark_running(job_id)
    # A stop flag left over from an earlier run of this job must not kill this one.
    clear_cancel(job_id)
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
        result = run_pipeline(
            **pipeline_kwargs,
            progress_cb=_progress,
            cancel_check=lambda: is_cancelled(job_id),
        )

        if isinstance(result, dict) and "error" in result:
            store.mark_failed(job_id, str(result["error"]))
            logger.error("[job %s] pipeline reported error: %s", job_id, result["error"])
            return

        # A stopped run is still a finished JOB — the parts it did generate are
        # real and downloadable. `result["stopped"]` is what the UI reads to say
        # "you stopped this" instead of claiming the character is complete.
        store.mark_succeeded(job_id, result)
        logger.info(
            "[job %s] pipeline %s.",
            job_id, "STOPPED by user" if result.get("stopped") else "succeeded",
        )

    except Exception as e:  # noqa: BLE001 — record any failure on the job
        store.mark_failed(job_id, f"{type(e).__name__}: {e}")
        logger.exception("[job %s] pipeline crashed.", job_id)
    finally:
        clear_cancel(job_id)


def _run_storyboard(job_id: str, kwargs: dict):
    """Generate storyboard panels off-request, updating the SAME job.

    Streams partial panels into job.result as each one finishes so the client's
    board fills in one-by-one (mirrors _run_generate's partial-urls pattern).
    """
    from storyboard_pipeline import run_storyboard

    store = get_store()
    store.mark_running(job_id)
    logger.info("[job %s] storyboard started (%d shots)", job_id, len(kwargs.get("shots") or []))

    def _progress(update: dict):
        partial_panels = update.pop("panels", None)
        fields = {"progress": update}
        if partial_panels is not None:
            # Merge with the meta so the client always has style/aspect too.
            fields["result"] = {
                "panels": partial_panels,
                "style": kwargs.get("style"),
                "aspect_ratio": kwargs.get("aspect_ratio"),
                "count": len(kwargs.get("shots") or []),
            }
        try:
            store.update(job_id, **fields)
        except Exception:  # noqa: BLE001 — progress writes must not kill the job
            logger.debug("[job %s] storyboard progress update failed (ignored)", job_id, exc_info=True)

    try:
        result = run_storyboard(job_id=job_id, progress_cb=_progress, **kwargs)
        # A stopped run is still a finished JOB — the panels it did draw are real
        # and downloadable. `result["stopped"]` is what the board reads to say
        # "you stopped this" instead of claiming the board is complete.
        store.mark_succeeded(job_id, result)
        logger.info(
            "[job %s] storyboard %s (%s/%s ok).",
            job_id, "STOPPED by user" if result.get("stopped") else "succeeded",
            result.get("ok_count"), result.get("count"),
        )
    except Exception as e:  # noqa: BLE001
        store.mark_failed(job_id, f"{type(e).__name__}: {e}")
        logger.exception("[job %s] storyboard crashed.", job_id)


def _run_restyle(job_id: str, kwargs: dict):
    """Re-draw the whole board in a NEW style, stored as a new style VARIANT.

    Keeps every existing variant intact (so the user can switch back) and streams
    the new variant's panels in as they render. `existing_variants` are the prior
    variants; the new one is appended at index `variant` and made active.
    """
    from storyboard_pipeline import run_storyboard

    store = get_store()
    store.mark_running(job_id)
    existing = kwargs.pop("existing_variants", [])
    variant = kwargs["variant"]
    new_style = kwargs["style"]
    aspect = kwargs.get("aspect_ratio")
    count = len(kwargs.get("shots") or [])
    logger.info("[job %s] restyle started → variant %d (style=%s)", job_id, variant, new_style)

    def _compose(panels: list, stopped: bool = False) -> dict:
        ok = sum(1 for p in panels if not p.get("failed"))
        variants = list(existing) + [
            {"style": new_style, "panels": panels, "ok_count": ok}
        ]
        return {
            "variants": variants,
            "active_variant": variant,
            "style": new_style,
            "aspect_ratio": aspect,
            "count": count,
            "ok_count": ok,
            "panels": panels,
            # Carried up so a stopped re-style says so, same as a stopped board.
            "stopped": stopped,
        }

    def _progress(update: dict):
        partial = update.pop("panels", None)
        fields = {"progress": update}
        if partial is not None:
            fields["result"] = _compose(partial)
        try:
            store.update(job_id, **fields)
        except Exception:  # noqa: BLE001 — progress writes must not kill the job
            logger.debug("[job %s] restyle progress update failed (ignored)", job_id, exc_info=True)

    try:
        result = run_storyboard(job_id=job_id, progress_cb=_progress, **kwargs)
        store.mark_succeeded(job_id, _compose(result["panels"], result.get("stopped", False)))
        logger.info(
            "[job %s] restyle %s (variant %d).",
            job_id, "STOPPED by user" if result.get("stopped") else "succeeded", variant,
        )
    except Exception as e:  # noqa: BLE001
        store.mark_failed(job_id, f"{type(e).__name__}: {e}")
        logger.exception("[job %s] restyle crashed.", job_id)


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


def _run_regenerate(job_id: str, mode: str, kwargs: dict):
    """Regenerate one part/view in the background and update the SAME job.

    The parent job was already flipped to RUNNING by the endpoint. On success we
    replace its result; on failure we keep the existing (good) assets and attach
    a `regen_error` so the UI can show what went wrong — the job stays SUCCEEDED.
    """
    from pipeline import regenerate_single_part, regenerate_single_view

    store = get_store()
    old_result = dict(kwargs.get("existing_result") or {})
    label = kwargs.get("part_name") or "part"
    view = kwargs.get("view_name")
    tag = f"{label}/{view}" if view else label
    logger.info("[job %s] regenerate (%s) started for %s", job_id, mode, tag)

    try:
        if mode == "view":
            result = regenerate_single_view(**kwargs)
        else:
            result = regenerate_single_part(**kwargs)
        result.pop("regen_error", None)  # clear any stale error
        store.update(job_id, status=JobStatus.SUCCEEDED, result=result, progress=None, error=None)
        logger.info("[job %s] regenerate (%s) succeeded for %s", job_id, mode, tag)
    except Exception as e:  # noqa: BLE001
        logger.exception("[job %s] regenerate (%s) failed for %s", job_id, mode, tag)
        old_result["regen_error"] = f"{tag}: {e}"
        # Keep the job usable with its previous assets.
        store.update(job_id, status=JobStatus.SUCCEEDED, result=old_result, progress=None)
