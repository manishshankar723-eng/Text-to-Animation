"""
storyboard_pipeline.py — Script → Storyboard, Stage D: generate panels.

Given a reviewed shot list + a style + an aspect ratio, generate ONE image per
shot, centre-crop it to the exact aspect ratio, save it locally, and report
progress after each panel (so the client's board fills in one-by-one).

Synchronous + I/O-bound (Gemini image calls) — run it in the worker thread pool,
never on the FastAPI event loop. Mirrors pipeline.py's progress-callback shape.

Panels are rendered CONCURRENTLY (STORYBOARD_PANEL_CONCURRENCY). Real API pressure
is bounded by the shared throttle in gemini_client, so this pool only controls how
many panels are in flight locally.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

logger = logging.getLogger(__name__)

# How many panels to draw at once. The gemini_client throttle
# (IMAGE_MAX_CONCURRENCY / IMAGE_RPM) is the real ceiling.
PANEL_CONCURRENCY = max(1, int(os.environ.get("STORYBOARD_PANEL_CONCURRENCY", "2")))

# Stop / cancel. The registry is shared with the character pipeline (cancel.py);
# re-exported here because callers already import these names from this module.
# For a board: every panel not yet started is skipped, and the 1–2 already
# talking to the image API finish, because an in-flight call can't be un-sent.
from cancel import clear_cancel, is_cancelled, request_cancel  # noqa: E402,F401


def _crop_to_aspect(image: "Image.Image", aspect_ratio: str) -> "Image.Image":
    """Centre-crop an image to the given W:H ratio (e.g. '16:9')."""
    try:
        w_ratio, h_ratio = (float(x) for x in aspect_ratio.split(":"))
        if w_ratio <= 0 or h_ratio <= 0:
            return image
    except (ValueError, AttributeError):
        return image

    target = w_ratio / h_ratio
    w, h = image.size
    current = w / h
    if abs(current - target) < 0.01:
        return image

    if current > target:
        # Too wide → trim the sides.
        new_w = int(round(h * target))
        left = (w - new_w) // 2
        return image.crop((left, 0, left + new_w, h))
    # Too tall → trim top/bottom.
    new_h = int(round(w / target))
    top = (h - new_h) // 2
    return image.crop((0, top, w, top + new_h))


def _load_refs(ref_paths: dict | None, kind: str = "reference") -> dict:
    """Load reference images once, keyed by lowercased name.

    Used for both character refs and asset (prop/background) refs — the loading
    is identical, only the log label differs.
    """
    refs: dict[str, "Image.Image"] = {}
    for name, path in (ref_paths or {}).items():
        try:
            refs[name.strip().lower()] = Image.open(path).convert("RGB")
        except (OSError, AttributeError):
            logger.warning("[storyboard] couldn't load %s ref for %s: %s", kind, name, path)
    return refs


def _load_character_refs(character_ref_paths: dict | None) -> dict:
    """Load character reference images once, keyed by lowercased name."""
    return _load_refs(character_ref_paths, "character")


def _variant_dir(board_dir: str, variant: int) -> str:
    """Panel folder for a style variant (variant 0 = the board root, for compat)."""
    return board_dir if not variant else os.path.join(board_dir, f"v{variant}")


def _panel_url(job_id: str, i: int, variant: int) -> str:
    """Serve URL for a panel, tagged with its variant so the client caches per-style."""
    suffix = f"?v={variant}" if variant else ""
    return f"/storyboards/{job_id}/panel/{i}{suffix}"


def _load_composition_ref(composition_ref_dir: str | None, i: int) -> "Image.Image | None":
    """Load an existing panel to feed as a composition reference when re-styling."""
    if not composition_ref_dir:
        return None
    path = os.path.join(composition_ref_dir, f"panel_{i:02d}.png")
    try:
        return Image.open(path).convert("RGB")
    except (OSError, AttributeError):
        return None


def _gather_refs(names, ref_map: dict, cap: int) -> list:
    """Collect up to `cap` reference images for the given names.

    Deduped by name (not by image value — two visually similar assets are still
    distinct references).
    """
    out = []
    seen: set[str] = set()
    for name in names or []:
        key = str(name).strip().lower()
        if not key or key in seen:
            continue
        ref = ref_map.get(key)
        if ref is None:
            continue
        seen.add(key)
        out.append(ref)
        if len(out) >= cap:
            break
    return out


def regenerate_panel(
    job_id: str,
    panel: dict,
    style: str = "custom",
    aspect_ratio: str = "16:9",
    output_dir: str = "output",
    character_ref_paths: dict | None = None,
    asset_ref_paths: dict | None = None,
    variant: int = 0,
    provider: str | None = None,
    world: dict | None = None,
) -> dict:
    """Re-generate ONE panel (used by the Retry button). Returns the updated panel.

    `variant` targets the active style variant's subfolder + URL. `world` is the
    script's region/period/culture, so a redrawn panel matches the rest of the
    board instead of reverting to the model's default look.
    """
    from gemini_client import generate_storyboard_panel

    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    write_dir = _variant_dir(board_dir, variant)
    os.makedirs(write_dir, exist_ok=True)
    char_refs = _load_character_refs(character_ref_paths)
    asset_refs = _load_refs(asset_ref_paths, "asset")

    i = panel["index"]
    shot_char_refs = _gather_refs(panel.get("characters", []), char_refs, 3)
    shot_asset_refs = _gather_refs(panel.get("assets", []), asset_refs, 3)

    updated = dict(panel)
    description = str(panel.get("description", "")).strip()
    image = None
    if description:
        image = generate_storyboard_panel(
            description=description,
            style=style,
            aspect_ratio=aspect_ratio,
            characters=panel.get("characters", []) or [],
            location=panel.get("location", "") or "",
            camera=panel.get("camera", "") or "",
            reference_images=shot_char_refs or None,
            asset_reference_images=shot_asset_refs or None,
            provider=provider,
            world=world,
        )

    if image is not None:
        image = _crop_to_aspect(image, aspect_ratio)
        image.save(os.path.join(write_dir, f"panel_{i:02d}.png"), "PNG")
        updated["url"] = _panel_url(job_id, i, variant)
        updated["failed"] = False
    else:
        updated["url"] = None
        updated["failed"] = True
    return updated


def run_storyboard(
    job_id: str,
    shots: list[dict],
    style: str = "custom",
    aspect_ratio: str = "16:9",
    output_dir: str = "output",
    provider: str | None = None,
    character_ref_paths: dict | None = None,
    asset_ref_paths: dict | None = None,
    variant: int = 0,
    composition_ref_dir: str | None = None,
    world: dict | None = None,
    progress_cb=None,
) -> dict:
    """Generate a storyboard panel for each shot.

    `variant` writes panels into a per-style subfolder (0 = the board root) and
    tags their URLs so the client caches each style separately.
    `composition_ref_dir`, when set, feeds the matching existing panel as a
    composition reference so a re-style keeps the same staging (only the art
    style changes).

    Args:
        job_id: owning job id (used for the output folder + panel URLs).
        shots: list of shot dicts {scene_number, shot_number, description,
               characters[], assets[], location, camera}.
        style / aspect_ratio: chosen on the input page.
        output_dir: base output directory.
        provider: image backend ("vertex" | "gemini"); defaults to IMAGE_PROVIDER.
        character_ref_paths: {character_name: image_path} — reference images fed
            into every panel the character appears in (Stage B consistency).
        asset_ref_paths: {asset_name: image_path} — prop/background reference
            images fed into every panel the asset appears in (Stage B2 consistency).
        world: the script's region/period/culture block, prefixed onto every
            panel prompt so the whole board stays true to the story's world
            (see gemini_client.build_world_context).
        progress_cb: optional callable(update: dict) for live progress. Receives
            {percent, stage, message, current, total, panels(partial list)}.

    Returns:
        {style, aspect_ratio, count, panels: [{index, scene_number, shot_number,
         description, characters, location, camera, url, failed}]}.
    """
    from gemini_client import generate_storyboard_panel

    total = len(shots)
    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    write_dir = _variant_dir(board_dir, variant)
    os.makedirs(write_dir, exist_ok=True)

    char_refs = _load_character_refs(character_ref_paths)
    asset_refs = _load_refs(asset_ref_paths, "asset")
    # Cap references per panel so a crowd scene doesn't overload the request.
    MAX_REFS_PER_PANEL = 3

    # Build every panel up front so the board can show correct shot numbers and a
    # skeleton for each one still rendering (url=None, failed=False → skeleton).
    panels: list[dict] = [
        {
            "index": i,
            "scene_number": shot.get("scene_number", 1),
            "shot_number": shot.get("shot_number", i + 1),
            "description": str(shot.get("description", "")).strip(),
            "characters": shot.get("characters", []) or [],
            "assets": shot.get("assets", []) or [],
            "location": shot.get("location", "") or "",
            "camera": shot.get("camera", "") or "",
            "url": None,
            "failed": False,
        }
        for i, shot in enumerate(shots)
    ]

    state_lock = threading.Lock()
    done = 0

    def _emit(percent, message, extra=None):
        if not progress_cb:
            return
        with state_lock:
            snapshot = [dict(p) for p in panels]
            current = done
        update = {
            "percent": percent,
            "stage": "generating",
            "message": message,
            "current": current,
            "total": total,
            "panels": snapshot,  # full-length; pending ones render as skeletons
        }
        if extra:
            update.update(extra)
        try:
            progress_cb(update)
        except Exception:  # noqa: BLE001 — progress must never kill the run
            logger.debug("[storyboard %s] progress cb failed (ignored)", job_id, exc_info=True)

    def _render(i: int) -> None:
        """Draw ONE panel and record the outcome (runs in the panel pool)."""
        # Every queued panel checks here first, so a stop costs nothing beyond
        # the calls already in flight. Skipped panels stay url=None/failed=False
        # — the board then offers "Generate this panel" for each of them.
        if is_cancelled(job_id):
            return
        panel = panels[i]
        description = panel["description"]
        # Gather reference images for the characters + assets in THIS shot.
        shot_char_refs = _gather_refs(panel["characters"], char_refs, MAX_REFS_PER_PANEL)
        shot_asset_refs = _gather_refs(panel["assets"], asset_refs, MAX_REFS_PER_PANEL)

        image = None
        if description:
            image = generate_storyboard_panel(
                description=description,
                style=style,
                aspect_ratio=aspect_ratio,
                characters=panel["characters"],
                location=panel["location"],
                camera=panel["camera"],
                reference_images=shot_char_refs or None,
                asset_reference_images=shot_asset_refs or None,
                composition_reference_image=_load_composition_ref(composition_ref_dir, i),
                provider=provider,
                world=world,
            )

        if image is not None:
            image = _crop_to_aspect(image, aspect_ratio)
            image.save(os.path.join(write_dir, f"panel_{i:02d}.png"), "PNG")
            with state_lock:
                panel["url"] = _panel_url(job_id, i, variant)
            logger.info("[storyboard %s] panel %d/%d done (variant %d)", job_id, i + 1, total, variant)
        else:
            with state_lock:
                panel["failed"] = True
            logger.warning("[storyboard %s] panel %d/%d FAILED (no image)", job_id, i + 1, total)

    logger.info(
        "[storyboard %s] generating %d panels (style=%s, aspect=%s, concurrency=%d)",
        job_id, total, style, aspect_ratio, PANEL_CONCURRENCY,
    )
    # A stop flag left over from a previous run must not kill this one.
    clear_cancel(job_id)
    _emit(2, f"Starting {total} panels…")

    # Render panels concurrently. Actual API pressure is bounded by the shared
    # throttle in gemini_client, so this pool only controls local fan-out.
    if total:
        with ThreadPoolExecutor(
            max_workers=min(PANEL_CONCURRENCY, total), thread_name_prefix="panel"
        ) as pool:
            futures = {pool.submit(_render, i): i for i in range(total)}
            for future in as_completed(futures):
                i = futures[future]
                try:
                    future.result()
                except Exception:  # noqa: BLE001 — one panel must not kill the board
                    with state_lock:
                        panels[i]["failed"] = True
                    logger.exception("[storyboard %s] panel %d crashed", job_id, i + 1)
                with state_lock:
                    done += 1
                    completed = done
                _emit(
                    int(2 + (completed / max(total, 1)) * 96),
                    "Stopping — finishing the panels already started…"
                    if is_cancelled(job_id)
                    else f"Drawing panels… {completed} of {total} done",
                )

    stopped = is_cancelled(job_id)
    clear_cancel(job_id)
    ok = sum(1 for p in panels if p.get("url") and not p["failed"])
    drawn = sum(1 for p in panels if p.get("url") or p["failed"])
    if stopped:
        _emit(100, f"Stopped by you — {ok} of {total} panels drawn.")
        logger.info("[storyboard %s] STOPPED: %d/%d drawn (%d ok)", job_id, drawn, total, ok)
    else:
        _emit(100, f"Done — {ok}/{total} panels generated.")
        logger.info("[storyboard %s] complete: %d/%d ok", job_id, ok, total)

    return {
        "style": style,
        "aspect_ratio": aspect_ratio,
        "count": total,
        "ok_count": ok,
        "variant": variant,
        "panels": panels,
        # The run ended early because the user stopped it — the board says so
        # rather than pretending a half-drawn board is a finished one.
        "stopped": stopped,
    }
