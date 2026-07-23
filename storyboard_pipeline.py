"""
storyboard_pipeline.py — Script → Storyboard, Stage D: generate panels.

Given a reviewed shot list + a style + an aspect ratio, generate ONE image per
shot, centre-crop it to the exact aspect ratio, save it locally, and report
progress after each panel (so the client's board fills in one-by-one).

Synchronous + I/O-bound (Gemini image calls) — run it in the worker thread pool,
never on the FastAPI event loop. Mirrors pipeline.py's progress-callback shape.
"""

import logging
import os

from PIL import Image

logger = logging.getLogger(__name__)


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


def _load_character_refs(character_ref_paths: dict | None) -> dict:
    """Load character reference images once, keyed by lowercased name."""
    refs: dict[str, "Image.Image"] = {}
    for name, path in (character_ref_paths or {}).items():
        try:
            refs[name.strip().lower()] = Image.open(path).convert("RGB")
        except (OSError, AttributeError):
            logger.warning("[storyboard] couldn't load character ref for %s: %s", name, path)
    return refs


def regenerate_panel(
    job_id: str,
    panel: dict,
    style: str = "custom",
    aspect_ratio: str = "16:9",
    output_dir: str = "output",
    character_ref_paths: dict | None = None,
    provider: str | None = None,
) -> dict:
    """Re-generate ONE panel (used by the Retry button). Returns the updated panel."""
    from gemini_client import generate_storyboard_panel

    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    os.makedirs(board_dir, exist_ok=True)
    char_refs = _load_character_refs(character_ref_paths)

    i = panel["index"]
    shot_refs = []
    for name in panel.get("characters", []) or []:
        ref = char_refs.get(str(name).strip().lower())
        if ref is not None and ref not in shot_refs:
            shot_refs.append(ref)
        if len(shot_refs) >= 3:
            break

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
            reference_images=shot_refs or None,
            provider=provider,
        )

    if image is not None:
        image = _crop_to_aspect(image, aspect_ratio)
        image.save(os.path.join(board_dir, f"panel_{i:02d}.png"), "PNG")
        updated["url"] = f"/storyboards/{job_id}/panel/{i}"
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
    progress_cb=None,
) -> dict:
    """Generate a storyboard panel for each shot.

    Args:
        job_id: owning job id (used for the output folder + panel URLs).
        shots: list of shot dicts {scene_number, shot_number, description,
               characters[], location, camera}.
        style / aspect_ratio: chosen on the input page.
        output_dir: base output directory.
        provider: image backend ("vertex" | "gemini"); defaults to IMAGE_PROVIDER.
        character_ref_paths: {character_name: image_path} — reference images fed
            into every panel the character appears in (Stage B consistency).
        progress_cb: optional callable(update: dict) for live progress. Receives
            {percent, stage, message, current, total, panels(partial list)}.

    Returns:
        {style, aspect_ratio, count, panels: [{index, scene_number, shot_number,
         description, characters, location, camera, url, failed}]}.
    """
    from gemini_client import generate_storyboard_panel

    total = len(shots)
    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    os.makedirs(board_dir, exist_ok=True)

    char_refs = _load_character_refs(character_ref_paths)
    # Cap references per panel so a crowd scene doesn't overload the request.
    MAX_REFS_PER_PANEL = 3

    panels: list[dict] = []

    def _emit(percent, message, extra=None):
        if not progress_cb:
            return
        update = {
            "percent": percent,
            "stage": "generating",
            "message": message,
            "current": len(panels),
            "total": total,
            "panels": list(panels),  # partial list so the board fills in
        }
        if extra:
            update.update(extra)
        try:
            progress_cb(update)
        except Exception:  # noqa: BLE001 — progress must never kill the run
            logger.debug("[storyboard %s] progress cb failed (ignored)", job_id, exc_info=True)

    logger.info("[storyboard %s] generating %d panels (style=%s, aspect=%s)", job_id, total, style, aspect_ratio)
    _emit(2, f"Starting {total} panels…")

    for i, shot in enumerate(shots):
        description = str(shot.get("description", "")).strip()
        panel = {
            "index": i,
            "scene_number": shot.get("scene_number", 1),
            "shot_number": shot.get("shot_number", i + 1),
            "description": description,
            "characters": shot.get("characters", []) or [],
            "location": shot.get("location", "") or "",
            "camera": shot.get("camera", "") or "",
            "url": None,
            "failed": False,
        }

        _emit(
            int(2 + (i / max(total, 1)) * 96),
            f"Drawing panel {i + 1} of {total}…",
        )

        # Gather reference images for the characters in THIS shot.
        shot_refs = []
        for name in panel["characters"]:
            ref = char_refs.get(str(name).strip().lower())
            if ref is not None and ref not in shot_refs:
                shot_refs.append(ref)
            if len(shot_refs) >= MAX_REFS_PER_PANEL:
                break

        image = None
        if description:
            image = generate_storyboard_panel(
                description=description,
                style=style,
                aspect_ratio=aspect_ratio,
                characters=panel["characters"],
                location=panel["location"],
                camera=panel["camera"],
                reference_images=shot_refs or None,
                provider=provider,
            )

        if image is not None:
            image = _crop_to_aspect(image, aspect_ratio)
            path = os.path.join(board_dir, f"panel_{i:02d}.png")
            image.save(path, "PNG")
            panel["url"] = f"/storyboards/{job_id}/panel/{i}"
            logger.info("[storyboard %s] panel %d/%d done", job_id, i + 1, total)
        else:
            panel["failed"] = True
            logger.warning("[storyboard %s] panel %d/%d FAILED (no image)", job_id, i + 1, total)

        panels.append(panel)

    ok = sum(1 for p in panels if not p["failed"])
    _emit(100, f"Done — {ok}/{total} panels generated.")
    logger.info("[storyboard %s] complete: %d/%d ok", job_id, ok, total)

    return {
        "style": style,
        "aspect_ratio": aspect_ratio,
        "count": total,
        "ok_count": ok,
        "panels": panels,
    }
