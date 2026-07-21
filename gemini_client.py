"""
gemini_client.py — Wrapper for Gemini image generation.

Sends a reference image + prompt to a Gemini image model and returns a 2×2
turnaround sheet (PIL Image).

TWO BACKENDS — switch freely to match your spend/quota:

    IMAGE_PROVIDER=vertex   (default)
        Vertex AI. Auth via Application Default Credentials
        (gcloud auth application-default login). Uses GOOGLE_CLOUD_PROJECT +
        GOOGLE_CLOUD_LOCATION (MUST be "global" — us-central1 404s this model).

    IMAGE_PROVIDER=gemini
        Gemini Developer API. Auth via GEMINI_API_KEY (or GOOGLE_API_KEY).
        Simpler auth, separate billing/quota.

The provider can be set globally (env), per CLI run (--provider), or per API
job (the `provider` form field). Model IDs are overridable per provider via
VERTEX_IMAGE_MODEL / GEMINI_IMAGE_MODEL.
"""

import io
import os
import time
import logging

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MODEL_ID = "gemini-3.1-flash-image"
DEFAULT_PROJECT = "project-cf56be07-4f9e-45d4-9f4"
SUPPORTED_PROVIDERS = ("vertex", "gemini")
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 5  # doubles each retry: 5s, 10s, 20s


def _resolve_provider(provider: str | None = None) -> str:
    """Resolve the effective provider: explicit arg > IMAGE_PROVIDER env > 'vertex'."""
    p = (provider or os.environ.get("IMAGE_PROVIDER", "vertex")).strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown IMAGE_PROVIDER '{p}'. Use one of {SUPPORTED_PROVIDERS}."
        )
    return p


def _model_id(provider: str) -> str:
    """Model id for the given provider (env-overridable, shared default)."""
    if provider == "gemini":
        return os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_MODEL_ID)
    return os.environ.get("VERTEX_IMAGE_MODEL", DEFAULT_MODEL_ID)


def _create_client(provider: str):
    """Create a genai Client for the given provider."""
    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "IMAGE_PROVIDER=gemini requires GEMINI_API_KEY (or GOOGLE_API_KEY) "
                "to be set in your .env."
            )
        client = genai.Client(api_key=api_key)
        logger.info("genai client created (provider=gemini Developer API)")
        return client

    # provider == "vertex"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    client = genai.Client(vertexai=True, project=project, location=location)
    logger.info(
        "genai client created (provider=vertex, project=%s, location=%s)",
        project, location,
    )
    return client


# One cached client per provider, so both backends can be used in the same
# process (e.g. different API jobs) without re-authing on every call.
_clients: dict[str, "genai.Client"] = {}


def get_client(provider: str | None = None):
    """Return the cached genai client for the resolved provider."""
    provider = _resolve_provider(provider)
    if provider not in _clients:
        _clients[provider] = _create_client(provider)
    return _clients[provider]


def _is_valid_sheet(image: Image.Image) -> bool:
    """
    Basic sanity check: a 2×2 turnaround sheet should be roughly square
    or wider-than-tall, and at least 512px on each side.
    """
    w, h = image.size
    if w < 512 or h < 512:
        return False
    # Aspect ratio between 0.6 and 2.2 is acceptable for a 2×2 grid (e.g. 1408×768 = 1.83)
    ratio = w / h
    if ratio < 0.6 or ratio > 2.2:
        return False
    return True


def generate_turnaround_sheet(
    reference_image: Image.Image,
    prompt: str,
    part_name: str = "unknown",
    provider: str | None = None,
) -> Image.Image | None:
    """
    Send a reference image + prompt to Gemini and get back a 2×2 turnaround sheet.

    Args:
        reference_image: PIL Image to use as reference (the uploaded character
                         photo for fullbody, or the fullbody sheet for other parts).
        prompt: The text prompt describing what to generate.
        part_name: Name of the part (for logging only).
        provider: "vertex" or "gemini". Defaults to IMAGE_PROVIDER env (or "vertex").

    Returns:
        PIL Image of the 2×2 sheet, or None if generation failed
        (e.g. content filter blocked it).
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[%s] Calling Gemini (provider=%s, model=%s, attempt %d/%d)...",
                part_name, provider, model_id, attempt, MAX_RETRIES,
            )

            response = client.models.generate_content(
                model=model_id,
                contents=[prompt, reference_image],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                ),
            )

            # --- Extract image from response ---
            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        image = Image.open(io.BytesIO(part.inline_data.data))
                        image = image.convert("RGB")

                        # Validate it looks like a 2×2 sheet
                        if _is_valid_sheet(image):
                            logger.info(
                                "[%s] Got valid sheet (%dx%d)", part_name, image.width, image.height
                            )
                            return image
                        else:
                            logger.warning(
                                "[%s] Sheet looks wrong (%dx%d), retrying...",
                                part_name, image.width, image.height,
                            )
                            # Don't count this as a rate-limit retry, but do retry
                            continue

            # If we get here, response was empty (content filter likely triggered)
            logger.warning(
                "[%s] Gemini returned EMPTY response (content filter likely triggered). "
                "Try making the prompt more modest (e.g. 'athletic t-shirt and knee-length shorts').",
                part_name,
            )
            return None

        except Exception as e:
            error_str = str(e)

            # 429 RESOURCE_EXHAUSTED — retry with exponential backoff
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "[%s] Rate limited (429). Waiting %ds before retry %d/%d...",
                    part_name, backoff, attempt, MAX_RETRIES,
                )
                time.sleep(backoff)
                continue

            # Other errors — log and give up
            logger.error("[%s] Gemini call failed: %s", part_name, error_str)
            if attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.info("[%s] Retrying in %ds...", part_name, backoff)
                time.sleep(backoff)
                continue
            else:
                logger.error("[%s] All %d attempts failed.", part_name, MAX_RETRIES)
                return None

    # Should not reach here, but just in case
    logger.error("[%s] Exhausted all retries.", part_name)
    return None


# ---------------------------------------------------------------------------
# Reference image generation (Step 0)
# ---------------------------------------------------------------------------

# System-level wrapper that steers Gemini toward producing a clean T-pose
# character reference image suitable for the turnaround pipeline.
_REFERENCE_PROMPT_TEMPLATE = (
    "Generate a 3D animated Pixar-style character in T-pose (arms extended "
    "straight out to the sides, palms facing down) on a pure white background. "
    "Full body, front view, standing upright, clean studio lighting, no shadows "
    "on the background. The character should be centered in the frame with head "
    "to toe visible. Character description: {description}"
)


def _is_valid_reference(image: Image.Image) -> bool:
    """
    Sanity check for a single character reference image.

    Expects a portrait-ish or square image (a single character, not a grid).
    """
    w, h = image.size
    if w < 256 or h < 256:
        return False
    # Should be roughly portrait or square — NOT a wide 2×2 grid.
    ratio = w / h
    if ratio > 2.0 or ratio < 0.3:
        return False
    return True


def generate_character_reference(
    description: str,
    provider: str | None = None,
) -> Image.Image | None:
    """
    Generate a single T-pose character reference image from a text description.

    This is "Step 0" of the pipeline — it produces an image like kamla.jpg that
    can then be fed into the full turnaround generation pipeline.

    Args:
        description: Free-form character description, e.g.
                     "An Indian woman in a red saree, age 30, medium brown skin".
        provider: "vertex" or "gemini". Defaults to IMAGE_PROVIDER env (or "vertex").

    Returns:
        PIL Image of the character in T-pose on white background,
        or None if generation failed.
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)
    prompt = _REFERENCE_PROMPT_TEMPLATE.format(description=description)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[reference] Generating character reference (provider=%s, model=%s, attempt %d/%d)...",
                provider, model_id, attempt, MAX_RETRIES,
            )

            response = client.models.generate_content(
                model=model_id,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                ),
            )

            # --- Extract image from response ---
            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        image = Image.open(io.BytesIO(part.inline_data.data))
                        image = image.convert("RGB")

                        if _is_valid_reference(image):
                            logger.info(
                                "[reference] Got valid reference image (%dx%d)",
                                image.width, image.height,
                            )
                            return image
                        else:
                            logger.warning(
                                "[reference] Image looks wrong (%dx%d), retrying...",
                                image.width, image.height,
                            )
                            continue

            # Empty response (content filter)
            logger.warning(
                "[reference] Gemini returned EMPTY response (content filter likely triggered). "
                "Try adjusting the character description.",
            )
            return None

        except Exception as e:
            error_str = str(e)

            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "[reference] Rate limited (429). Waiting %ds before retry %d/%d...",
                    backoff, attempt, MAX_RETRIES,
                )
                time.sleep(backoff)
                continue

            logger.error("[reference] Gemini call failed: %s", error_str)
            if attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.info("[reference] Retrying in %ds...", backoff)
                time.sleep(backoff)
                continue
            else:
                logger.error("[reference] All %d attempts failed.", MAX_RETRIES)
                return None

    logger.error("[reference] Exhausted all retries.")
    return None

