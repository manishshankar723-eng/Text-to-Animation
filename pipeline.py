"""
pipeline.py — Orchestrates the full character asset generation pipeline.

Stages:
1. Fullbody turnaround sheet (Gemini call with uploaded reference image)
2. Per-part turnaround sheets (Gemini calls with fullbody sheet as reference)
3. Split each sheet into 4 views
4. Post-process each view (clean white + auto-crop + normalize)
5. Save to local + GCS
6. Create zip + optional Meshy submission
"""

import logging
import os

import yaml
from PIL import Image

from gemini_client import generate_turnaround_sheet
from splitter import split_sheet
from postprocess import clean_and_normalize_group
from storage import save_character_assets, create_zip
from meshy import submit_and_wait

logger = logging.getLogger(__name__)


def load_prompts(config_path: str = "prompts.yaml") -> dict:
    """Load the prompt configuration from YAML."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_prompts(
    config: dict,
    template_name: str | None,
    character_vars: dict | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Resolve the final prompts and slot renames for a character.

    Args:
        config: Full YAML config dict.
        template_name: e.g. "saree" or None for defaults.
        character_vars: e.g. {"age": "30", "gender": "female", "skin_tone": "medium brown"}
                        If None, uses template defaults.

    Returns:
        (prompts_dict, slot_renames_dict)
        prompts_dict: {part_name: final_prompt_string}
        slot_renames_dict: {slot_name: output_name} e.g. {"jacket": "saree"}
    """
    defaults = config.get("defaults", {})
    slot_renames = {}

    # Start with default prompts
    prompts = dict(defaults)

    # Apply template overrides if specified
    if template_name and template_name in config.get("templates", {}):
        template = config["templates"][template_name]

        # Get slot renames
        slot_renames = template.get("slot_renames", {})

        # Override prompts from template
        template_prompts = template.get("prompts", {})
        for part, prompt in template_prompts.items():
            prompts[part] = prompt

        # Use template's character defaults if no explicit vars given
        if character_vars is None:
            character_vars = template.get("character_defaults", {})

    # If still no character vars, use the "default" template's defaults
    if character_vars is None:
        default_template = config.get("templates", {}).get("default", {})
        character_vars = default_template.get("character_defaults", {
            "age": "25", "gender": "male", "skin_tone": "medium brown"
        })

    # Fill in {age}, {gender}, {skin_tone} placeholders in character-specific prompts
    for part in ["fullbody", "hair", "face"]:
        if part in prompts:
            prompts[part] = prompts[part].format(**character_vars)

    return prompts, slot_renames


def run_pipeline(
    character_name: str,
    reference_image_path: str,
    template_name: str | None = None,
    skip_parts: list[str] | None = None,
    only_parts: list[str] | None = None,
    meshy_parts: list[str] | None = None,
    local_only: bool = False,
    config_path: str = "prompts.yaml",
    output_dir: str = "output",
    character_vars: dict | None = None,
    provider: str | None = None,
    progress_cb=None,
) -> dict:
    """
    Run the full pipeline for one character.

    Args:
        character_name: e.g. "kamla"
        reference_image_path: Path to the uploaded reference image.
        template_name: e.g. "saree" or None for defaults.
        skip_parts: Parts to skip (e.g. ["goggles", "headphone"]).
        only_parts: If set, run ONLY these parts (for cheap testing).
        meshy_parts: Parts to submit to Meshy for 3D generation.
        local_only: If True, skip GCS upload.
        config_path: Path to prompts.yaml.
        output_dir: Local output base directory.
        character_vars: Override character variables (age, gender, skin_tone).
        provider: Image backend — "vertex" or "gemini". Defaults to the
                  IMAGE_PROVIDER env var (or "vertex").

    Returns:
        Dict with results: {part_name: {view_name: url_or_path}}, zip path, meshy results.
    """
    # --- Load config & resolve prompts ---
    config = load_prompts(config_path)
    prompts, slot_renames = _resolve_prompts(config, template_name, character_vars)
    parts_order = config.get("parts_order", list(prompts.keys()))

    logger.info("Image provider: %s", provider or os.environ.get("IMAGE_PROVIDER", "vertex"))

    # --- Filter parts ---
    if only_parts:
        # --parts flag: run ONLY these parts (always ensure fullbody is first for reference)
        parts_to_run = ["fullbody"] + [p for p in only_parts if p != "fullbody"]
    else:
        parts_to_run = [p for p in parts_order if p not in (skip_parts or [])]

    logger.info("Character: %s | Template: %s | Parts: %s",
                character_name, template_name or "default", parts_to_run)

    # --- Load reference image ---
    reference_image = Image.open(reference_image_path).convert("RGB")
    logger.info("Loaded reference image: %s (%dx%d)",
                reference_image_path, reference_image.width, reference_image.height)

    upload_gcs = not local_only
    total_parts = len(parts_to_run)

    # Clear any stale assets from a previous run of the same character so the
    # gallery, zip and disk all reflect ONLY this run's output.
    char_dir = os.path.join(output_dir, character_name)
    if os.path.isdir(char_dir):
        for fname in os.listdir(char_dir):
            if fname.endswith(".png"):
                try:
                    os.remove(os.path.join(char_dir, fname))
                except OSError:
                    logger.debug("Could not remove stale file: %s", fname)

    def _report(**kw):
        """Emit a progress update to the caller's callback (never raises)."""
        if progress_cb is None:
            return
        try:
            progress_cb(kw)
        except Exception:  # noqa: BLE001 — progress must never break the run
            logger.debug("progress_cb raised (ignored)", exc_info=True)

    _report(
        percent=2, stage="starting", current_part=None,
        message="Preparing character pipeline…",
        done_parts=[], total_parts=total_parts, urls={},
    )

    # =====================================================================
    # STAGES 1–5 — one part at a time: generate → split → clean → save.
    # Each part is fully produced and saved before moving on, so the client
    # can preview parts appearing one-by-one (fullbody → face → hair → …).
    # =====================================================================
    processed_views = {}  # {output_name: {view_name: PIL.Image}}
    urls = {}             # accumulated per-part URLs (grows as parts finish)
    fullbody_sheet = None
    done_parts = []
    failed_parts = []     # parts the model failed to produce (surfaced to the UI)

    # Generation is the bulk of the wall-clock time — budget 6..88% across parts.
    GEN_START, GEN_END = 6, 88

    for idx, part in enumerate(parts_to_run):
        if part not in prompts:
            logger.warning("No prompt found for part '%s', skipping.", part)
            continue

        prompt = prompts[part]

        if part == "fullbody":
            ref = reference_image  # Stage 1: uploaded reference
        else:
            if fullbody_sheet is None:
                logger.error("Fullbody sheet not available — cannot generate '%s'", part)
                continue
            ref = fullbody_sheet   # Stage 2: fullbody sheet as reference

        pct_before = GEN_START + int((GEN_END - GEN_START) * idx / max(total_parts, 1))
        _report(
            percent=pct_before, stage="generating", current_part=part,
            message=f"Generating {part} turnaround ({idx + 1}/{total_parts})…",
            done_parts=list(done_parts), total_parts=total_parts, urls=dict(urls),
        )

        sheet = generate_turnaround_sheet(ref, prompt, part_name=part, provider=provider)
        if sheet is None:
            logger.warning("[%s] Generation failed — skipping this part.", part)
            # Don't silently drop it — surface it so the user can regenerate.
            if not (only_parts and part == "fullbody" and "fullbody" not in only_parts):
                out_name = slot_renames.get(part, part)
                if out_name not in failed_parts:
                    failed_parts.append(out_name)
            continue

        if part == "fullbody":
            fullbody_sheet = sheet
            # Persist the raw fullbody sheet so single-part regeneration can reuse it.
            raw_path = os.path.join(output_dir, character_name, "_fullbody_sheet.png")
            os.makedirs(os.path.dirname(raw_path), exist_ok=True)
            sheet.save(raw_path, "PNG")
            logger.info("Fullbody sheet ready — reused as reference for later parts.")

        # If only_parts was used and fullbody was added just for reference, don't save it.
        if only_parts and part == "fullbody" and "fullbody" not in only_parts:
            logger.info("Skipping save of fullbody (used only as reference).")
            continue

        _report(
            percent=pct_before, stage="processing", current_part=part,
            message=f"Splitting & cleaning {part} views…",
            done_parts=list(done_parts), total_parts=total_parts, urls=dict(urls),
        )

        # Split into 4 views + post-process them together (shared scale so the
        # subject is the same size across all four views).
        cleaned_views = clean_and_normalize_group(split_sheet(sheet))
        output_name = slot_renames.get(part, part)
        processed_views[output_name] = cleaned_views

        # Save just this part (local + optional GCS) and merge its URLs.
        part_urls = save_character_assets(
            character_name, {output_name: cleaned_views}, output_dir, upload_gcs
        )
        urls[output_name] = part_urls[output_name]
        done_parts.append(output_name)
        logger.info("[%s] Done → '%s' (%d/%d parts)", part, output_name, len(done_parts), total_parts)

        pct_after = GEN_START + int((GEN_END - GEN_START) * (idx + 1) / max(total_parts, 1))
        _report(
            percent=pct_after, stage="part_done", current_part=output_name,
            message=f"{output_name} ready ({len(done_parts)}/{total_parts})",
            done_parts=list(done_parts), total_parts=total_parts, urls=dict(urls),
        )

    if not processed_views:
        logger.error("No sheets were generated. Aborting.")
        return {"error": "No sheets generated"}

    # =====================================================================
    # STAGE 6 — Zip + optional Meshy
    # =====================================================================
    _report(
        percent=92, stage="saving", current_part=None,
        message="Finalizing & creating download zip…",
        done_parts=list(done_parts), total_parts=total_parts, urls=dict(urls),
    )
    zip_result = create_zip(character_name, output_dir, upload_gcs)
    logger.info("Zip created: %s", zip_result)

    # --- Meshy submission ---
    meshy_results = {}
    if meshy_parts and not local_only:
        for part in meshy_parts:
            # Resolve slot rename for lookup
            output_name = slot_renames.get(part, part)
            if output_name not in urls:
                logger.warning("[%s] Not in generated assets — skipping Meshy.", output_name)
                continue

            # Collect 4 view URLs for this part
            view_urls = [
                urls[output_name].get("front", ""),
                urls[output_name].get("left", ""),
                urls[output_name].get("three_quarter", ""),
                urls[output_name].get("back", ""),
            ]
            view_urls = [u for u in view_urls if u]  # filter empty

            if len(view_urls) < 4:
                logger.warning("[%s] Only %d views available, Meshy needs 4.", output_name, len(view_urls))
                continue

            result = submit_and_wait(output_name, view_urls)
            if result:
                meshy_results[output_name] = result

    # --- Final summary ---
    summary = {
        "character": character_name,
        "template": template_name or "default",
        "parts_generated": list(processed_views.keys()),
        "failed_parts": failed_parts,
        "prompts": prompts,
        "urls": urls,
        "zip": zip_result,
        "meshy": meshy_results,
    }

    logger.info("Pipeline complete for '%s'. Generated %d parts.",
                character_name, len(processed_views))

    _report(
        percent=100, stage="done", current_part=None,
        message=f"Complete — {len(processed_views)} parts generated.",
        done_parts=list(processed_views.keys()), total_parts=total_parts, urls=dict(urls),
    )

    return summary


def regenerate_single_part(
    character_name: str,
    reference_image_path: str,
    part_name: str,
    custom_prompt: str | None = None,
    template_name: str | None = None,
    local_only: bool = False,
    output_dir: str = "output",
    provider: str | None = None,
    existing_result: dict | None = None,
) -> dict:
    """
    Regenerate a single part for a character without re-running the entire pipeline.

    Args:
        character_name: e.g. "Vivaan"
        reference_image_path: Path to the character reference image.
        part_name: e.g. "face", "fullbody", "hair"
        custom_prompt: Optional override prompt for this part.
        template_name: Character template name.
        local_only: Skip GCS upload if True.
        output_dir: Base local directory.
        provider: Image provider ("vertex" or "gemini").
        existing_result: The existing pipeline summary dict to update.

    Returns:
        Updated pipeline summary dict.
    """
    config = load_prompts("prompts.yaml")
    prompts, slot_renames = _resolve_prompts(config, template_name, None)

    # Use custom prompt if given, else resolved prompt from YAML
    final_prompt = custom_prompt if custom_prompt else prompts.get(part_name)
    if not final_prompt:
        raise ValueError(f"No prompt available for part '{part_name}'")

    # Load reference image
    ref_image = Image.open(reference_image_path).convert("RGB")

    # For non-fullbody parts, try to use fullbody sheet if available, else reference image
    if part_name != "fullbody":
        fullbody_sheet_path = os.path.join(output_dir, character_name, "_fullbody_sheet.png")
        if os.path.exists(fullbody_sheet_path):
            try:
                ref_image = Image.open(fullbody_sheet_path).convert("RGB")
            except Exception:
                pass

    logger.info("[%s] Regenerating single part with prompt: %s", part_name, final_prompt)
    sheet = generate_turnaround_sheet(ref_image, final_prompt, part_name=part_name, provider=provider)

    if sheet is None:
        raise RuntimeError(f"Failed to generate turnaround sheet for '{part_name}'")

    # Save fullbody raw sheet if fullbody was regenerated
    if part_name == "fullbody":
        raw_sheet_path = os.path.join(output_dir, character_name, "_fullbody_sheet.png")
        os.makedirs(os.path.dirname(raw_sheet_path), exist_ok=True)
        sheet.save(raw_sheet_path, "PNG")

    # Split into 4 views + clean/normalize together (shared scale).
    cleaned_views = clean_and_normalize_group(split_sheet(sheet))

    output_name = slot_renames.get(part_name, part_name)
    processed_views = {output_name: cleaned_views}

    # Save views and update zip
    upload_gcs = not local_only
    updated_urls = save_character_assets(
        character_name, processed_views, output_dir, upload_gcs
    )
    zip_result = create_zip(character_name, output_dir, upload_gcs)

    # Merge into existing result dict
    result = existing_result or {
        "character": character_name,
        "template": template_name or "default",
        "parts_generated": [],
        "prompts": {},
        "urls": {},
    }

    if "prompts" not in result:
        result["prompts"] = {}
    result["prompts"][part_name] = final_prompt

    if "urls" not in result:
        result["urls"] = {}
    result["urls"][output_name] = updated_urls[output_name]

    if output_name not in result.get("parts_generated", []):
        result.setdefault("parts_generated", []).append(output_name)

    # It succeeded now — clear it from the failed list if it was there.
    if output_name in result.get("failed_parts", []):
        result["failed_parts"] = [p for p in result["failed_parts"] if p != output_name]

    result["zip"] = zip_result
    return result


def regenerate_single_view(
    character_name: str,
    reference_image_path: str,
    part_name: str,
    view_name: str,
    custom_prompt: str | None = None,
    template_name: str | None = None,
    local_only: bool = False,
    output_dir: str = "output",
    provider: str | None = None,
    existing_result: dict | None = None,
) -> dict:
    """
    Regenerate ONE view (front/left/three_quarter/back) of a single part.

    Generates a fresh turnaround sheet for the part, then replaces ONLY the
    requested view image — the other three views are left untouched. Useful when
    a single panel came out wrong (e.g. two characters in one view).

    Returns the updated pipeline summary dict.
    """
    valid_views = {"front", "left", "three_quarter", "back"}
    if view_name not in valid_views:
        raise ValueError(f"Invalid view '{view_name}'. Must be one of {sorted(valid_views)}.")

    config = load_prompts("prompts.yaml")
    prompts, slot_renames = _resolve_prompts(config, template_name, None)

    final_prompt = custom_prompt if custom_prompt else prompts.get(part_name)
    if not final_prompt:
        raise ValueError(f"No prompt available for part '{part_name}'")

    ref_image = Image.open(reference_image_path).convert("RGB")
    if part_name != "fullbody":
        fullbody_sheet_path = os.path.join(output_dir, character_name, "_fullbody_sheet.png")
        if os.path.exists(fullbody_sheet_path):
            try:
                ref_image = Image.open(fullbody_sheet_path).convert("RGB")
            except Exception:
                pass

    logger.info("[%s/%s] Regenerating single view.", part_name, view_name)
    sheet = generate_turnaround_sheet(ref_image, final_prompt, part_name=part_name, provider=provider)
    if sheet is None:
        raise RuntimeError(f"Failed to generate turnaround sheet for '{part_name}'")

    cleaned_views = clean_and_normalize_group(split_sheet(sheet))
    if view_name not in cleaned_views:
        raise RuntimeError(f"View '{view_name}' not produced for '{part_name}'.")

    output_name = slot_renames.get(part_name, part_name)

    # Save ONLY the requested view (overwrites that one file).
    upload_gcs = not local_only
    saved = save_character_assets(
        character_name, {output_name: {view_name: cleaned_views[view_name]}}, output_dir, upload_gcs
    )
    zip_result = create_zip(character_name, output_dir, upload_gcs)

    result = existing_result or {
        "character": character_name,
        "template": template_name or "default",
        "parts_generated": [],
        "prompts": {},
        "urls": {},
    }
    result.setdefault("urls", {}).setdefault(output_name, {})
    result["urls"][output_name][view_name] = saved[output_name][view_name]
    result["zip"] = zip_result
    return result
