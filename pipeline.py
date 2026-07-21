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
from postprocess import clean_and_normalize
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

    # =====================================================================
    # STAGE 1 & 2 — Generate turnaround sheets
    # =====================================================================
    sheets = {}       # {part_name: PIL.Image} — raw 2×2 sheets
    fullbody_sheet = None

    for part in parts_to_run:
        if part not in prompts:
            logger.warning("No prompt found for part '%s', skipping.", part)
            continue

        prompt = prompts[part]

        if part == "fullbody":
            # Stage 1: use the uploaded reference image
            ref = reference_image
        else:
            # Stage 2: use the fullbody sheet as reference
            if fullbody_sheet is None:
                logger.error("Fullbody sheet not available — cannot generate '%s'", part)
                continue
            ref = fullbody_sheet

        sheet = generate_turnaround_sheet(ref, prompt, part_name=part, provider=provider)

        if sheet is None:
            logger.warning("[%s] Generation failed — skipping this part.", part)
            continue

        sheets[part] = sheet

        if part == "fullbody":
            fullbody_sheet = sheet
            logger.info("Fullbody sheet ready — will use as reference for remaining parts.")

    if not sheets:
        logger.error("No sheets were generated. Aborting.")
        return {"error": "No sheets generated"}

    # =====================================================================
    # STAGE 3 & 4 — Split + Post-process
    # =====================================================================
    processed_views = {}  # {output_name: {view_name: PIL.Image}}

    for part, sheet in sheets.items():
        # Split into 4 views
        views = split_sheet(sheet)

        # Post-process each view
        cleaned_views = {}
        for view_name, view_image in views.items():
            cleaned_views[view_name] = clean_and_normalize(view_image)

        # Apply slot renames for output filenames
        output_name = slot_renames.get(part, part)

        # If only_parts was used and fullbody was added just for reference, skip saving it
        if only_parts and part == "fullbody" and "fullbody" not in only_parts:
            logger.info("Skipping save of fullbody (was only used as reference).")
            continue

        processed_views[output_name] = cleaned_views
        logger.info("[%s] Split + post-processed → '%s' (4 views)", part, output_name)

    # =====================================================================
    # STAGE 5 — Save locally + GCS
    # =====================================================================
    upload_gcs = not local_only
    urls = save_character_assets(
        character_name, processed_views, output_dir, upload_gcs
    )

    # =====================================================================
    # STAGE 6 — Zip + optional Meshy
    # =====================================================================
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
        "urls": urls,
        "zip": zip_result,
        "meshy": meshy_results,
    }

    logger.info("Pipeline complete for '%s'. Generated %d parts.",
                character_name, len(processed_views))

    return summary
