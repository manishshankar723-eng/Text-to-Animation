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


class _SafeDict(dict):
    """dict for str.format_map that leaves unknown placeholders blank.

    Lets a template omit character vars (e.g. a robot has no gender/skin_tone)
    without raising KeyError — the placeholder just resolves to an empty string.
    """

    def __missing__(self, key):  # noqa: D401
        return ""


def _resolve_prompts(
    config: dict,
    template_name: str | None,
    character_vars: dict | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Resolve the final prompts and slot renames for a subject.

    Works for ANY subject type (human / robot / animal / bird / …). Character
    variables like {gender}, {age}, {skin_tone} are optional — templates that
    don't define them simply resolve those placeholders to empty strings.

    Returns:
        (prompts_dict, slot_renames_dict)
    """
    defaults = config.get("defaults", {})
    prompts = dict(defaults)
    slot_renames = {}

    template = config.get("templates", {}).get(template_name or "", {})
    if template:
        slot_renames = template.get("slot_renames", {})
        for part, prompt in template.get("prompts", {}).items():
            prompts[part] = prompt
        if character_vars is None:
            character_vars = template.get("character_defaults")

    # Fill placeholders safely across ALL prompts (missing keys → "").
    safe = _SafeDict(character_vars or {})
    prompts = {
        part: (text.format_map(safe) if isinstance(text, str) else text)
        for part, text in prompts.items()
    }
    return prompts, slot_renames


def _resolve_parts_order(config: dict, template_name: str | None) -> list[str]:
    """Return the ordered part list for a template (its own, else the global)."""
    template = config.get("templates", {}).get(template_name or "", {})
    if template.get("parts_order"):
        return list(template["parts_order"])
    return list(config.get("parts_order", []))


def _generic_part_prompt(part_name: str) -> str:
    """Fallback prompt for a custom asset that has no template prompt.

    Lets users generate arbitrary items (e.g. "mobile", "cape", "backpack") that
    aren't predefined — reproduced as an isolated object turnaround.
    """
    nice = part_name.replace("_", " ")
    return (
        f"COPY the {nice} from the reference image as an isolated single object / accessory. "
        f"If the {nice} is visible in the reference, COPY it exactly; otherwise design a matching "
        f"one that fits the character. Show ONE {nice} on a 2×2 grid in four views: top-left = front, "
        "top-right = left side, bottom-left = three-quarter angle, bottom-right = back. Keep natural, "
        "realistic proportions — do NOT stretch or distort. Isolated object only — NO person, NO hand, "
        "NO body. ABSOLUTELY NO text, letters, words, numbers, captions, labels or watermarks anywhere. "
        "NO borders, frames, outlines, grid lines, dividers or gutters — the four views sit edge-to-edge "
        "on ONE seamless pure-white (#FFFFFF) background."
    )


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
    parts_order = _resolve_parts_order(config, template_name) or list(prompts.keys())

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
    # When the user picks specific parts, fullbody is auto-added ONLY as a
    # reference — it shouldn't count toward or appear in the visible progress.
    ref_only_fullbody = bool(only_parts) and "fullbody" not in only_parts
    total_parts = len([p for p in parts_to_run if not (ref_only_fullbody and p == "fullbody")])

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

    vis_i = 0  # index over the VISIBLE parts (excludes the reference-only fullbody)
    for part in parts_to_run:
        is_ref_only = ref_only_fullbody and part == "fullbody"

        # Prompt: template prompt, or a generic fallback for custom assets.
        prompt = prompts.get(part) or _generic_part_prompt(part)

        if part == "fullbody":
            ref = reference_image  # Stage 1: uploaded reference
        else:
            if fullbody_sheet is None:
                logger.error("Fullbody sheet not available — cannot generate '%s'", part)
                continue
            ref = fullbody_sheet   # Stage 2: fullbody sheet as reference

        output_name = slot_renames.get(part, part)

        if is_ref_only:
            # Silent reference step — no visible section, no count.
            _report(
                percent=4, stage="reference", current_part=None,
                message="Preparing base reference pose…",
                done_parts=list(done_parts), total_parts=total_parts, urls=dict(urls),
            )
        else:
            pct_before = GEN_START + int((GEN_END - GEN_START) * vis_i / max(total_parts, 1))
            _report(
                percent=pct_before, stage="generating", current_part=output_name,
                message=f"Generating {output_name} turnaround ({vis_i + 1}/{total_parts})…",
                done_parts=list(done_parts), total_parts=total_parts, urls=dict(urls),
            )

        sheet = generate_turnaround_sheet(ref, prompt, part_name=part, provider=provider)
        if sheet is None:
            logger.warning("[%s] Generation failed — skipping this part.", part)
            # Surface failures (except the hidden reference) so the user can retry.
            if not is_ref_only and output_name not in failed_parts:
                failed_parts.append(output_name)
            if not is_ref_only:
                vis_i += 1
            continue

        if part == "fullbody":
            fullbody_sheet = sheet
            # Persist the raw fullbody sheet so single-part regeneration can reuse it.
            raw_path = os.path.join(output_dir, character_name, "_fullbody_sheet.png")
            os.makedirs(os.path.dirname(raw_path), exist_ok=True)
            sheet.save(raw_path, "PNG")
            logger.info("Fullbody sheet ready — reused as reference for later parts.")

        # Fullbody added only as a reference → don't save/show it.
        if is_ref_only:
            logger.info("Skipping save of fullbody (used only as reference).")
            continue

        _report(
            percent=pct_before, stage="processing", current_part=output_name,
            message=f"Splitting & cleaning {output_name} views…",
            done_parts=list(done_parts), total_parts=total_parts, urls=dict(urls),
        )

        # Split into 4 views + post-process them together (shared scale so the
        # subject is the same size across all four views).
        cleaned_views = clean_and_normalize_group(split_sheet(sheet))
        processed_views[output_name] = cleaned_views

        # Save just this part (local + optional GCS) and merge its URLs.
        part_urls = save_character_assets(
            character_name, {output_name: cleaned_views}, output_dir, upload_gcs
        )
        urls[output_name] = part_urls[output_name]
        done_parts.append(output_name)
        vis_i += 1
        logger.info("[%s] Done → '%s' (%d/%d parts)", part, output_name, len(done_parts), total_parts)

        pct_after = GEN_START + int((GEN_END - GEN_START) * vis_i / max(total_parts, 1))
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

    # Custom prompt if given, else template prompt, else a generic fallback
    # (so custom assets like "mobile" can be regenerated too).
    final_prompt = custom_prompt or prompts.get(part_name) or _generic_part_prompt(part_name)

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

    final_prompt = custom_prompt or prompts.get(part_name) or _generic_part_prompt(part_name)

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
