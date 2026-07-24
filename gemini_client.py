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
import random
import re
import threading
import time
import logging
from contextlib import contextmanager

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


# ---------------------------------------------------------------------------
# Throttling — ONE governor for every image call in the process
# ---------------------------------------------------------------------------
# Every path (background storyboard jobs AND the synchronous cast/props/retry
# endpoints, which run on FastAPI's own threadpool) funnels through this module,
# so capping here bounds real API pressure regardless of who calls.
#   IMAGE_MAX_CONCURRENCY — simultaneous in-flight image requests
#   IMAGE_RPM             — requests per minute (0 disables the rate limit)
MAX_CONCURRENCY = max(1, int(os.environ.get("IMAGE_MAX_CONCURRENCY", "6")))
IMAGE_RPM = max(0, int(os.environ.get("IMAGE_RPM", "120")))


class _TokenBucket:
    """Thread-safe token bucket: at most `rpm` acquisitions per rolling minute."""

    def __init__(self, rpm: int):
        self.rate = rpm / 60.0 if rpm > 0 else 0.0  # tokens per second
        self.capacity = float(max(rpm, 1))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available (no-op when the limit is disabled)."""
        if self.rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            time.sleep(min(wait, 5.0))


_semaphore = threading.BoundedSemaphore(MAX_CONCURRENCY)
_bucket = _TokenBucket(IMAGE_RPM)


@contextmanager
def _throttle():
    """Hold a concurrency slot and a rate-limit token for one API call."""
    _bucket.acquire()
    _semaphore.acquire()
    try:
        yield
    finally:
        _semaphore.release()


# ---------------------------------------------------------------------------
# Error classification — only retry what can actually succeed on a retry
# ---------------------------------------------------------------------------
_RETRYABLE_MARKERS = (
    "429", "resource_exhausted", "rate limit", "quota",
    "500", "internal error",
    "503", "unavailable", "overloaded",
    "504", "deadline", "timeout",
)
# Permanent: retrying burns time and never succeeds.
_PERMANENT_MARKERS = (
    "400", "invalid argument", "invalid_argument",
    "401", "403", "permission denied", "unauthenticated",
    "404", "not found",
    "safety", "blocked", "prohibited_content",
)


def _is_retryable(error: Exception) -> bool:
    """True if re-issuing this request has a real chance of succeeding."""
    text = str(error).lower()
    if any(m in text for m in _PERMANENT_MARKERS):
        return False
    return any(m in text for m in _RETRYABLE_MARKERS)


def _retry_after_seconds(error: Exception) -> float | None:
    """Honour a server-provided Retry-After / retryDelay hint when present."""
    match = re.search(r"retry[- _]?(?:after|delay)\D{0,10}(\d+(?:\.\d+)?)", str(error), re.I)
    if match:
        try:
            return min(float(match.group(1)), 120.0)
        except ValueError:
            return None
    return None


def _backoff_delay(attempt: int, error: Exception | None = None) -> float:
    """Exponential backoff with jitter (so parallel workers don't retry in lockstep)."""
    if error is not None:
        hinted = _retry_after_seconds(error)
        if hinted is not None:
            return hinted
    base = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
    return base * random.uniform(0.5, 1.5)


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

            with _throttle():
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

            # Permanent (bad request / auth / safety) — a retry can't fix it.
            if not _is_retryable(e):
                logger.error("[%s] permanent error, not retrying: %s", part_name, error_str)
                return None

            if attempt < MAX_RETRIES:
                delay = _backoff_delay(attempt, e)
                logger.warning(
                    "[%s] transient error (attempt %d/%d), retrying in %.1fs: %s",
                    part_name, attempt, MAX_RETRIES, delay, error_str,
                )
                time.sleep(delay)
                continue
            logger.error("[%s] All %d attempts failed: %s", part_name, MAX_RETRIES, error_str)
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


# Asset references (Stage B2) — a clean, reusable image of a prop or a
# background/location that gets fed into every panel the asset appears in, so
# the object / set stays consistent across the storyboard.
_ASSET_PROP_PROMPT_TEMPLATE = (
    "Generate a single clean product-style reference image of ONE object on a "
    "pure white background: {description}. Centered, front view, full object "
    "visible, even studio lighting, no shadows on the background, no text, no "
    "extra objects, no people. This is a design reference sheet for the object."
)
_ASSET_BACKGROUND_PROMPT_TEMPLATE = (
    "Generate a single clean establishing background/location reference image "
    "(no people, no characters): {description}. Wide empty set with clear, "
    "consistent layout, lighting and colour palette an artist could match in "
    "later panels. No text, no watermark, no on-screen labels."
)


class ReferenceGenerationError(Exception):
    """Raised when a character reference image cannot be generated.

    Carries a human-readable reason so the API can surface the ACTUAL cause
    (API error / empty response / bad image) instead of a generic message.
    """


def _extract_block_reason(response) -> str | None:
    """Pull an explicit block / finish reason out of a genai response, if any.

    Returns a short string like "prompt blocked: SAFETY" or "finish: RECITATION",
    or None if the response simply had no image without an explicit reason.
    """
    try:
        feedback = getattr(response, "prompt_feedback", None)
        block = getattr(feedback, "block_reason", None) if feedback else None
        if block:
            return f"prompt blocked: {block}"

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish = getattr(candidates[0], "finish_reason", None)
            # STOP is normal; anything else on an image-less response is telling.
            if finish and str(finish).upper() not in ("STOP", "FINISH_REASON_STOP", "1"):
                return f"finish reason: {finish}"
    except Exception:  # noqa: BLE001 — diagnostics must never mask the real error
        return None
    return None


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


# ---------------------------------------------------------------------------
# Storyboard panel generation (Script → Storyboard, Stage D)
# ---------------------------------------------------------------------------

# Per-style art direction appended to every panel prompt.
_STORYBOARD_STYLE_PROMPTS = {
    # Current UI style ids (match client STYLES / MORE_STYLES).
    "comic": "bold colourful comic-book panel, strong ink outlines, dramatic cel shading",
    "cinematic": "photorealistic cinematic film still, natural lighting, shallow depth of field, filmic colour grade",
    "soft-pencil": "soft graphite pencil storyboard sketch, light hand-drawn shading, clean paper texture",
    "animation-3d": "polished Pixar-style 3D animated render, soft global illumination, expressive characters",
    "watercolor": "soft watercolour painting, gentle washes of colour, visible paper texture, loose edges",
    "photo-commercial": "clean commercial photography look, bright even lighting, crisp product-style framing",
    "charcoal": "expressive black-and-white charcoal drawing, rich smudged shadows, bold strokes",
    "dark-anime": "moody dark anime style, cinematic anime lighting, detailed line art, high contrast",
    "flat-vector": "flat vector illustration, clean geometric shapes, bold solid colours, minimal shading",
    "noir": "high-contrast black-and-white film-noir look, deep shadows, dramatic low-key lighting",
    "stick-figure": "simple black-and-white stick-figure storyboard, minimal line drawing on a white background",
    "graphic-novel": "gritty graphic-novel panel, inked linework, muted cinematic colour palette",
    "custom": "clean illustrated storyboard panel",
    # Back-compat aliases for older style ids.
    "sketch": "black-and-white rough pencil storyboard sketch, loose gestural lines, minimal shading",
    "comics": "bold colourful comic-book panel, strong ink outlines, dramatic cel shading",
    "realistic": "photorealistic cinematic film still, natural lighting, shallow depth of field",
    "3d-animation": "polished Pixar-style 3D animated render, soft global illumination",
}

# Aspect-ratio phrasing (the image is also post-cropped to the exact ratio).
_STORYBOARD_ASPECT_HINTS = {
    "21:9": "ultra-wide 21:9 cinemascope framing",
    "16:9": "wide 16:9 cinematic framing",
    "9:16": "tall vertical 9:16 mobile framing",
    "2:3": "portrait 2:3 comic-page framing",
    "1:1": "square 1:1 framing",
}


def generate_storyboard_panel(
    description: str,
    style: str = "custom",
    aspect_ratio: str = "16:9",
    characters: list[str] | None = None,
    location: str = "",
    camera: str = "",
    reference_images: list[Image.Image] | None = None,
    asset_reference_images: list[Image.Image] | None = None,
    composition_reference_image: "Image.Image | None" = None,
    provider: str | None = None,
) -> Image.Image | None:
    """Generate ONE storyboard panel image from a shot description.

    Uses the IMAGE backend (IMAGE_PROVIDER / `provider` arg) — panels are images.
    Style + aspect + camera + location are woven into the prompt. Optional
    `reference_images` (character references) and `asset_reference_images`
    (prop / background references) are passed alongside so the depicted
    characters, key props and backgrounds stay visually consistent across panels
    (Stage B). `composition_reference_image` is the existing render of THIS shot —
    used when re-styling a board so the new panel keeps the same staging and only
    the art style changes. Returns a PIL image, or None if the model returned
    nothing (e.g. safety filter).
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)

    # Known style id → its art direction. An unknown, non-empty value is treated
    # as the user's own freeform style text ("Add Your Own Style").
    style_txt = _STORYBOARD_STYLE_PROMPTS.get(style)
    if not style_txt:
        style_txt = (style or "").strip() or _STORYBOARD_STYLE_PROMPTS["custom"]
    aspect_txt = _STORYBOARD_ASPECT_HINTS.get(aspect_ratio, "wide 16:9 cinematic framing")

    parts = [f"A single storyboard panel: {style_txt}.", f"{aspect_txt}."]
    if camera:
        parts.append(f"Camera: {camera}.")
    if location:
        parts.append(f"Location: {location}.")
    if characters:
        parts.append("Characters present: " + ", ".join(characters) + ".")
    if reference_images:
        parts.append(
            "Use the provided character reference image(s) to keep each "
            "character's face, hair, body and clothing consistent — redraw them "
            "in this panel's art style and pose, do not copy the reference framing."
        )
    if asset_reference_images:
        parts.append(
            "The remaining reference image(s) show key props / objects and the "
            "background/location for this scene — keep their shape, colour and "
            "design consistent with these references, redrawn in this panel's art "
            "style and framing."
        )
    if composition_reference_image is not None:
        parts.append(
            "The LAST reference image is the existing version of THIS exact panel. "
            "Keep its composition, camera angle, staging and the positions of "
            "everything in frame the same — ONLY re-render it in the new art style "
            "described above. Do not change the layout or what is happening."
        )
    parts.append(f"Scene: {description}")
    parts.append("Single frame. No text, captions, speech bubbles, borders or watermarks.")
    # Keep borderline (mild-conflict) shots from tripping the safety filter.
    parts.append(
        "Non-graphic, family-friendly storyboard illustration suitable for a "
        "general audience; depict any conflict in a light, cartoonish way."
    )
    prompt = " ".join(parts)

    # Prompt first, then character refs, prop/background refs, then (last) the
    # existing panel as a composition reference for re-styling.
    contents = [prompt, *(reference_images or []), *(asset_reference_images or [])]
    if composition_reference_image is not None:
        contents.append(composition_reference_image)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[panel] Generating storyboard panel (provider=%s, model=%s, char_refs=%d, asset_refs=%d, composition=%s, attempt %d/%d)…",
                provider, model_id, len(reference_images or []),
                len(asset_reference_images or []), composition_reference_image is not None,
                attempt, MAX_RETRIES,
            )
            with _throttle():
                response = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                )

            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        image = Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
                        if image.width >= 256 and image.height >= 256:
                            logger.info("[panel] Got panel (%dx%d)", image.width, image.height)
                            return image
                        logger.warning("[panel] Panel too small (%dx%d), retrying…", image.width, image.height)
                        continue

            logger.warning("[panel] Empty response (content filter likely). Prompt: %.80s…", prompt)
            return None

        except Exception as e:  # noqa: BLE001
            # Only retry what a retry can fix; a safety block / bad request never
            # succeeds, so fail fast instead of burning the backoff budget.
            if not _is_retryable(e):
                logger.error("[panel] permanent error, not retrying: %s", e)
                return None
            if attempt >= MAX_RETRIES:
                logger.error("[panel] call failed after %d attempts: %s", MAX_RETRIES, e)
                return None
            delay = _backoff_delay(attempt, e)
            logger.warning("[panel] transient error (attempt %d/%d), retrying in %.1fs: %s",
                           attempt, MAX_RETRIES, delay, e)
            time.sleep(delay)
            continue

    return None


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

    # Track the most specific reason so the caller can report the ACTUAL cause.
    last_reason = "Unknown error generating the reference image."

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[reference] Generating character reference (provider=%s, model=%s, attempt %d/%d)...",
                provider, model_id, attempt, MAX_RETRIES,
            )

            with _throttle():
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
                            last_reason = (
                                f"The model returned an unexpected image ({image.width}×"
                                f"{image.height}px), not a single centered character."
                            )
                            logger.warning("[reference] %s Retrying…", last_reason)
                            continue

            # No image part. Check for an explicit block/finish reason to be precise.
            reason = _extract_block_reason(response)
            if reason:
                last_reason = (
                    f"The provider blocked or returned no image ({reason}). "
                    "Try rephrasing the description."
                )
            else:
                last_reason = (
                    "The model returned a response with no image (it may have replied "
                    "with text only, or the safety filter blocked it). Try rephrasing."
                )
            logger.warning("[reference] %s", last_reason)
            # An empty/blocked response won't change on identical retry — stop early.
            raise ReferenceGenerationError(last_reason)

        except ReferenceGenerationError:
            raise
        except Exception as e:
            error_str = str(e)
            last_reason = f"Image API error: {error_str}"

            # Permanent failures (bad request / auth / safety) never succeed on a
            # retry — surface them immediately instead of waiting out the backoff.
            if not _is_retryable(e):
                logger.error("[reference] permanent error, not retrying: %s", error_str)
                raise ReferenceGenerationError(last_reason)

            if attempt < MAX_RETRIES:
                delay = _backoff_delay(attempt, e)
                logger.warning(
                    "[reference] transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt, MAX_RETRIES, delay, error_str,
                )
                time.sleep(delay)
                continue
            logger.error("[reference] Gemini call failed: %s", error_str)

    logger.error("[reference] All attempts failed: %s", last_reason)
    raise ReferenceGenerationError(last_reason)


def generate_asset_reference(
    description: str,
    category: str = "prop",
    provider: str | None = None,
) -> Image.Image | None:
    """Generate a reference image for a prop or a background/location.

    Mirrors ``generate_character_reference`` but for the storyboard's non-character
    assets (Stage B2). A ``prop`` renders as one clean object on white; a
    ``background`` renders as an empty establishing plate. The returned image is
    fed into every panel the asset appears in, so it stays consistent.

    Args:
        description: Free-form asset description, e.g.
                     "a worn brown leather slipper" or "a cramped sunlit bedroom".
        category: "prop" (default) or "background".
        provider: "vertex" or "gemini". Defaults to IMAGE_PROVIDER env.

    Returns:
        PIL Image of the asset, or raises ReferenceGenerationError on failure.
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)
    cat = (category or "prop").strip().lower()
    template = (
        _ASSET_BACKGROUND_PROMPT_TEMPLATE
        if cat == "background"
        else _ASSET_PROP_PROMPT_TEMPLATE
    )
    prompt = template.format(description=description)

    last_reason = "Unknown error generating the asset reference image."

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[asset-ref] Generating %s reference (provider=%s, model=%s, attempt %d/%d)...",
                cat, provider, model_id, attempt, MAX_RETRIES,
            )

            with _throttle():
                response = client.models.generate_content(
                    model=model_id,
                    contents=[prompt],
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                )

            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        image = Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
                        if _is_valid_reference(image):
                            logger.info(
                                "[asset-ref] Got valid %s reference (%dx%d)",
                                cat, image.width, image.height,
                            )
                            return image
                        last_reason = (
                            f"The model returned an unexpected image ({image.width}×"
                            f"{image.height}px)."
                        )
                        logger.warning("[asset-ref] %s Retrying…", last_reason)
                        continue

            reason = _extract_block_reason(response)
            last_reason = (
                f"The provider blocked or returned no image ({reason}). Try rephrasing."
                if reason
                else "The model returned no image (safety filter likely). Try rephrasing."
            )
            logger.warning("[asset-ref] %s", last_reason)
            # An empty/blocked response won't change on identical retry — stop early.
            raise ReferenceGenerationError(last_reason)

        except ReferenceGenerationError:
            raise
        except Exception as e:  # noqa: BLE001
            error_str = str(e)
            last_reason = f"Image API error: {error_str}"
            if not _is_retryable(e):
                logger.error("[asset-ref] permanent error, not retrying: %s", error_str)
                raise ReferenceGenerationError(last_reason)
            if attempt < MAX_RETRIES:
                delay = _backoff_delay(attempt, e)
                logger.warning("[asset-ref] transient error, retrying in %.1fs: %s", delay, error_str)
                time.sleep(delay)
                continue
            logger.error("[asset-ref] Gemini call failed: %s", error_str)

    logger.error("[asset-ref] All attempts failed: %s", last_reason)
    raise ReferenceGenerationError(last_reason)

