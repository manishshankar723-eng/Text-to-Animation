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

# Video renders get their OWN pool. A Veo call holds its thread for minutes, so
# sharing the pipeline pool would let one final-video project starve every
# storyboard and character run on the server behind it.
_video_executor = ThreadPoolExecutor(
    max_workers=config.MAX_VIDEO_WORKERS, thread_name_prefix="video"
)


def shutdown():
    """Stop accepting new jobs and let running ones finish."""
    _executor.shutdown(wait=False)
    _video_executor.shutdown(wait=False)


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


def submit_animatic_export(job_id: str, kwargs: dict):
    """Enqueue an animatic MP4 export (Storyboard → Animatic)."""
    _executor.submit(_run_animatic_export, job_id, kwargs)


def submit_panel_sequence(job_id: str, kwargs: dict):
    """Enqueue a key-pose sequence for ONE storyboard panel (Image to Animatic
    Image). Runs on the pipeline pool — it is image work, like a board draw."""
    _executor.submit(_run_panel_sequence, job_id, kwargs)


def submit_shot_renders(job_id: str, shot_ids: list[str]):
    """Enqueue Veo renders for named shots of a final-video project.

    Runs on the VIDEO pool, not the pipeline pool — see _video_executor.
    """
    _video_executor.submit(_run_shot_renders, job_id, shot_ids)


def submit_final_assemble(job_id: str, kwargs: dict):
    """Enqueue the assembly of a final video's rendered clips (step 3).

    Assembly is ffmpeg, not AI, so it belongs on the ordinary pipeline pool
    alongside the animatic export it closely resembles.
    """
    _executor.submit(_run_final_assemble, job_id, kwargs)


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


def _run_animatic_export(job_id: str, kwargs: dict):
    """Encode an animatic to MP4 off-request, updating the SAME job.

    The job's status describes the export, not the project: the frames and audio
    are still there whatever happens, so a failed export leaves an editable
    animatic behind rather than a broken one.
    """
    from datetime import datetime, timezone

    from animatic import AnimaticError, build_animatic
    from cancel import clear_cancel, is_cancelled

    store = get_store()
    # A stop flag left over from an earlier export must not kill this one.
    clear_cancel(job_id)
    logger.info("[job %s] animatic export started (%d frames)", job_id, len(kwargs.get("frames") or []))

    def _progress(update: dict):
        try:
            store.update(job_id, progress=update)
        except Exception:  # noqa: BLE001 — progress writes must not kill the export
            logger.debug("[job %s] animatic progress update failed (ignored)", job_id, exc_info=True)

    try:
        summary = build_animatic(
            job_id=job_id,
            progress_cb=_progress,
            cancel_check=lambda: is_cancelled(job_id),
            **kwargs,
        )

        if summary.get("stopped"):
            # Nothing was written, so the PREVIOUS video (if any) is still the
            # truth — keep it, and drop back to whichever state that implies.
            job = store.get(job_id)
            previous = dict((job.result if job else None) or {})
            store.update(
                job_id,
                status=JobStatus.SUCCEEDED if previous.get("video") else JobStatus.QUEUED,
                result=previous or None,
                progress=None,
            )
            logger.info("[job %s] animatic export STOPPED by user", job_id)
            return

        store.update(
            job_id,
            status=JobStatus.SUCCEEDED,
            error=None,
            progress=None,
            result={
                "video": {
                    "url": f"/animatics/{job_id}/video",
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": summary.get("duration_ms", 0),
                    "frame_count": summary.get("frame_count", 0),
                    "text_count": summary.get("text_count", 0),
                    "skipped_frames": summary.get("skipped_frames") or [],
                    "width": summary.get("width"),
                    "height": summary.get("height"),
                    "fps": summary.get("fps"),
                    "has_audio": summary.get("has_audio", False),
                    "size_bytes": summary.get("size_bytes", 0),
                    # False until the project is edited again (see save_animatic).
                    "stale": False,
                }
            },
        )
        logger.info(
            "[job %s] animatic exported: %d frame(s), %.1fs",
            job_id, summary.get("frame_count", 0), summary.get("duration_ms", 0) / 1000,
        )

    except AnimaticError as e:
        # Deliberately shown verbatim — these messages are written for the user
        # (missing ffmpeg, no readable frames) and tell them what to do next.
        store.update(job_id, status=JobStatus.FAILED, error=str(e), progress=None)
        logger.error("[job %s] animatic export failed: %s", job_id, e)
    except Exception as e:  # noqa: BLE001
        store.update(job_id, status=JobStatus.FAILED, error=f"{type(e).__name__}: {e}", progress=None)
        logger.exception("[job %s] animatic export crashed.", job_id)
    finally:
        clear_cancel(job_id)


def _run_panel_sequence(job_id: str, kwargs: dict):
    """Draw one panel's key-pose sequence, updating the BOARD job as it goes.

    The board's own status carries this, exactly as a panel draw or a re-style
    does — so the board page's existing progress bar and Stop button work with
    no new plumbing. The RESULT is left alone apart from the `sequences` entry:
    the panels themselves are untouched, so a stopped or failed run leaves the
    board exactly as it was, plus whatever frames it did draw.
    """
    from cancel import clear_cancel, is_cancelled
    from panel_sequence import SequenceError, run_panel_sequence

    store = get_store()
    clear_cancel(job_id)
    index = int(kwargs.get("panel", {}).get("index", 0))
    store.mark_running(job_id)
    logger.info("[job %s] panel %d sequence started (%ss)", job_id, index, kwargs.get("duration_seconds"))

    def _progress(update: dict):
        try:
            store.update(job_id, progress=update)
        except Exception:  # noqa: BLE001 — progress writes must not kill the run
            logger.debug("[job %s] sequence progress update failed", job_id, exc_info=True)

    def _store_sequence(summary: dict):
        """Merge this panel's sequence into the board result, leaving the rest."""
        job = store.get(job_id)
        result = dict((job.result if job else None) or {})
        sequences = dict(result.get("sequences") or {})
        # Merged onto what this panel already had, not written over it: a run
        # that redraws ONE pose reports no pose plan of its own, and replacing
        # the entry wholesale would throw away the plan the sequence was built
        # from — which is what a later single-pose redraw needs to stay faithful.
        sequences[str(index)] = {**(sequences.get(str(index)) or {}), **summary}
        result["sequences"] = sequences
        return result

    try:
        summary = run_panel_sequence(
            job_id=job_id,
            progress_cb=_progress,
            cancel_check=lambda: is_cancelled(job_id),
            **kwargs,
        )
        store.update(
            job_id,
            status=JobStatus.SUCCEEDED,
            error=None,
            progress=None,
            result=_store_sequence(summary),
        )
        logger.info(
            "[job %s] panel %d sequence %s: %d/%d frames",
            job_id, index, "STOPPED" if summary.get("stopped") else "done",
            summary.get("frames", 0), summary.get("planned", 0),
        )
    except SequenceError as e:
        # Written for the user (no panel drawn yet, bad duration) — show as-is.
        store.update(job_id, status=JobStatus.SUCCEEDED, error=str(e), progress=None)
        logger.error("[job %s] panel %d sequence failed: %s", job_id, index, e)
    except Exception as e:  # noqa: BLE001
        store.update(
            job_id, status=JobStatus.SUCCEEDED,
            error=f"{type(e).__name__}: {e}", progress=None,
        )
        logger.exception("[job %s] panel %d sequence crashed.", job_id, index)
    finally:
        clear_cancel(job_id)


def _run_shot_renders(job_id: str, shot_ids: list[str]):
    """Render each named shot with Veo, one at a time, updating the SAME job.

    Shots are rendered SEQUENTIALLY on purpose. Veo's concurrency quota is small
    and each clip is billed, so firing a whole board at once is how you turn one
    project into twenty 429s — and pay for the few that got through anyway.
    Parallelism, where it is safe, is the pool's job (MAX_VIDEO_WORKERS) plus
    video_client's own semaphore.

    Every shot's outcome is written back the moment it lands, so a batch stopped
    half way keeps the clips it already paid for.
    """
    from cancel import clear_cancel, is_cancelled
    from video_client import VideoGenerationError

    from .videos import render_one_shot, update_shot

    store = get_store()
    store.mark_running(job_id)
    clear_cancel(job_id)
    total = len(shot_ids)
    logger.info("[job %s] shot renders started (%d shot(s))", job_id, total)

    done = 0
    failed = 0
    for shot_id in shot_ids:
        if is_cancelled(job_id):
            logger.info("[job %s] shot renders STOPPED by user after %d", job_id, done)
            # Shots never reached keep whatever state they had; the one in
            # flight is handled by render_one_shot's own cancel check.
            update_shot(job_id, shot_id, status="pending")
            break

        def _progress(update: dict, _sid=shot_id, _done=done):
            """Blend the shot's own progress into the batch's percentage."""
            within = max(0, min(100, int(update.get("percent", 0))))
            try:
                store.update(job_id, progress={
                    "percent": int((_done * 100 + within) / max(1, total)),
                    "stage": "rendering",
                    "message": update.get("message", ""),
                    "current_shot": _sid,
                    "done_parts": _done,
                    "total_parts": total,
                })
            except Exception:  # noqa: BLE001 — progress writes must not kill the batch
                logger.debug("[job %s] render progress update failed (ignored)", job_id, exc_info=True)

        try:
            update_shot(job_id, shot_id, status="rendering", error="")
            render_one_shot(
                job_id, shot_id,
                progress_cb=_progress,
                cancel_check=lambda: is_cancelled(job_id),
            )
            logger.info("[job %s] shot %s rendered.", job_id, shot_id)
        except VideoGenerationError as e:
            # Written for the user (quota, safety, credentials) — show verbatim.
            failed += 1
            update_shot(job_id, shot_id, status="failed", error=str(e))
            logger.error("[job %s] shot %s failed: %s", job_id, shot_id, e)
        except Exception as e:  # noqa: BLE001 — one bad shot must not kill the batch
            failed += 1
            update_shot(job_id, shot_id, status="failed", error=f"{type(e).__name__}: {e}")
            logger.exception("[job %s] shot %s crashed.", job_id, shot_id)
        done += 1

    clear_cancel(job_id)
    # The batch is finished either way: the shots that rendered are real and
    # assemblable, and the ones that didn't say why on themselves. Dropping the
    # PROJECT to failed because one shot was refused would hide the rest.
    job = store.get(job_id)
    store.update(
        job_id,
        status=JobStatus.SUCCEEDED if (job and (job.result or {}).get("video")) else JobStatus.QUEUED,
        progress=None,
    )
    logger.info("[job %s] shot renders done: %d ok, %d failed.", job_id, done - failed, failed)


def _run_final_assemble(job_id: str, kwargs: dict):
    """Join a final video's rendered clips into one MP4 (step 3).

    Mirrors _run_animatic_export: the job status describes the ASSEMBLY, so a
    failed join leaves every paid-for clip untouched and re-assemblable.
    """
    from datetime import datetime, timezone

    from animatic import AnimaticError
    from cancel import clear_cancel, is_cancelled
    from video_assemble import assemble_final_video

    store = get_store()
    clear_cancel(job_id)
    store.mark_running(job_id)
    logger.info("[job %s] final assembly started (%d clip(s))", job_id, len(kwargs.get("clips") or []))

    def _progress(update: dict):
        try:
            store.update(job_id, progress=update)
        except Exception:  # noqa: BLE001 — progress writes must not kill the assembly
            logger.debug("[job %s] assemble progress update failed (ignored)", job_id, exc_info=True)

    try:
        summary = assemble_final_video(
            job_id=job_id,
            progress_cb=_progress,
            cancel_check=lambda: is_cancelled(job_id),
            **kwargs,
        )

        if summary.get("stopped"):
            # Nothing was written, so the PREVIOUS cut (if any) is still the truth.
            job = store.get(job_id)
            previous = dict((job.result if job else None) or {})
            store.update(
                job_id,
                status=JobStatus.SUCCEEDED if previous.get("video") else JobStatus.QUEUED,
                result=previous or None,
                progress=None,
            )
            logger.info("[job %s] final assembly STOPPED by user", job_id)
            return

        store.update(
            job_id,
            status=JobStatus.SUCCEEDED,
            error=None,
            progress=None,
            result={
                "video": {
                    "url": f"/final-videos/{job_id}/video",
                    "assembled_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": summary.get("duration_ms", 0),
                    "clip_count": summary.get("clip_count", 0),
                    "skipped": summary.get("skipped") or [],
                    "width": summary.get("width"),
                    "height": summary.get("height"),
                    "fps": summary.get("fps"),
                    "size_bytes": summary.get("size_bytes", 0),
                    # False until a shot or setting changes again (see save).
                    "stale": False,
                }
            },
        )
        logger.info(
            "[job %s] final video assembled: %d clip(s), %.1fs",
            job_id, summary.get("clip_count", 0), summary.get("duration_ms", 0) / 1000,
        )

    except AnimaticError as e:
        # Covers AssembleError too — these messages are written for the user.
        store.update(job_id, status=JobStatus.FAILED, error=str(e), progress=None)
        logger.error("[job %s] final assembly failed: %s", job_id, e)
    except Exception as e:  # noqa: BLE001
        store.update(job_id, status=JobStatus.FAILED, error=f"{type(e).__name__}: {e}", progress=None)
        logger.exception("[job %s] final assembly crashed.", job_id)
    finally:
        clear_cancel(job_id)


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
