"""
video_client.py — Wrapper for Veo video generation (image → video).

Takes ONE picture (a storyboard panel, or a final-art frame) plus a motion
prompt and returns an MP4. This is the module behind the "Animatics to Final
Video" workflow, and the only place in the codebase that knows Veo exists.

WHY NOT GOOGLE FLOW: Flow (labs.google/flow) is a Labs *web app*. It has no
public API, no OAuth scope and no service account — its credits are a separate
ledger from the API, and a Google AI Pro/Ultra subscription grants no API access
at all. Driving its UI with a session cookie would be against Google's terms and
would break on any redesign. Flow is a front-end over Veo, so this module calls
Veo directly and gets the same models Flow uses.

TWO BACKENDS — mirrors gemini_client.py exactly, so the switch is familiar:

    VIDEO_PROVIDER=vertex   (default)
        Vertex AI. Auth via Application Default Credentials
        (gcloud auth application-default login) + GOOGLE_CLOUD_PROJECT.
        Higher quotas; the right choice for production.

    VIDEO_PROVIDER=gemini
        Gemini Developer API. Auth via GEMINI_API_KEY (or GOOGLE_API_KEY).
        Simplest to get running; good for development.

Model ids are overridable per provider via VERTEX_VIDEO_MODEL / GEMINI_VIDEO_MODEL.

Veo is a LONG-RUNNING operation: submit, then poll for a minute or three. The
public entry point (`render_shot`) blocks until it has bytes, because it is
always called from a worker thread — the same shape as meshy.submit_and_wait.
"""

import logging
import os
import threading
import time
from contextlib import contextmanager

from dotenv import load_dotenv
from google import genai
from google.genai import types

import ai_keys
import retry_policy

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# ⚠ WHOSE BILL VEO LANDS ON, AND THE ONE WORTH SPLITTING FIRST. This is the only
# per-SECOND meter in the app: one careless "render all" is dollars, not cents,
# so it gets its own key (`GEMINI_KEY_VIDEO`) and its own switch
# (`VIDEO_PROVIDER`) ahead of anything else. See `ai_keys`.
CAPABILITY = "video"
DEFAULT_PROJECT = "project-cf56be07-4f9e-45d4-9f4"
SUPPORTED_PROVIDERS = ("vertex", "gemini")

# Model ids per provider and tier. THE TWO BACKENDS USE DIFFERENT NAMES for the
# same model, which is the trap this table exists to close:
#
#   Vertex AI              → veo-3.1-…-generate-001     (versioned)
#   Gemini Developer API   → veo-3.1-…-generate-preview (preview-suffixed)
#
# Using the Gemini names on Vertex 404s with "model not found or your project
# does not have access to it", which reads like a permissions problem and isn't.
# The Vertex ids below were read back from a live project with
# `client.models.list()` — see `available_models()`, which is what the error
# path and /final-videos/backend now use so a wrong id names the right ones.
_MODEL_IDS = {
    "vertex": {
        "lite": "veo-3.1-lite-generate-001",
        "fast": "veo-3.1-fast-generate-001",
        "standard": "veo-3.1-generate-001",
    },
    "gemini": {
        "lite": "veo-3.1-lite-generate-preview",
        "fast": "veo-3.1-fast-generate-preview",
        "standard": "veo-3.1-generate-preview",
    },
}
DEFAULT_TIER = "fast"

# Veo clips are a fixed menu, not a free-form length. Asking for anything else
# is rejected by the backend, so the UI offers exactly these.
ALLOWED_DURATIONS = (4, 6, 8)
ALLOWED_RESOLUTIONS = ("720p", "1080p")
ALLOWED_ASPECTS = ("16:9", "9:16")

# How long to wait for one clip before giving up. A render is normally 1–3
# minutes; past this something is wrong and the job should fail loudly rather
# than pin a worker thread forever.
POLL_TIMEOUT_SECONDS = int(os.environ.get("VIDEO_POLL_TIMEOUT", "900"))
POLL_INTERVAL_SECONDS = float(os.environ.get("VIDEO_POLL_INTERVAL", "10"))

# Simultaneous in-flight video renders. Veo's concurrency quota is far tighter
# than the image models' and each call is minutes long, so this stays very
# small — firing a whole board at once is how you turn a board into 20 failures.
MAX_CONCURRENCY = max(1, int(os.environ.get("VIDEO_MAX_CONCURRENCY", "2")))

_semaphore = threading.BoundedSemaphore(MAX_CONCURRENCY)


@contextmanager
def _throttle():
    """Hold a concurrency slot for one render."""
    _semaphore.acquire()
    try:
        yield
    finally:
        _semaphore.release()


class VideoGenerationError(RuntimeError):
    """A render failed for a reason worth showing the user verbatim.

    Raised for missing credentials, a rejected prompt, or an exhausted retry
    budget — messages written to be read on screen, not just in a log.
    """


# ---------------------------------------------------------------------------
# Cost — so the UI can warn BEFORE spending, not after
# ---------------------------------------------------------------------------
# USD per second of output, by model tier and resolution. Veo is billed per
# second and a board is dozens of clips, so an unwarned "render all" is an
# expensive surprise. These are list prices and drift — they drive an ESTIMATE
# the UI labels as such, never a bill.
_RATE_USD_PER_SECOND = {
    ("fast", "720p"): 0.10,
    ("fast", "1080p"): 0.12,
    ("standard", "720p"): 0.35,
    ("standard", "1080p"): 0.40,
    ("lite", "720p"): 0.03,
    ("lite", "1080p"): 0.05,
}
# Audio is generated natively and costs more per second when switched on.
_AUDIO_SURCHARGE_PER_SECOND = 0.02


def estimate_cost_usd(
    duration_seconds: int,
    resolution: str = "720p",
    tier: str = "fast",
    with_audio: bool = True,
    clips: int = 1,
) -> float:
    """Rough USD cost of `clips` renders at these settings. Estimate only."""
    rate = _RATE_USD_PER_SECOND.get((tier, resolution))
    if rate is None:
        rate = _RATE_USD_PER_SECOND[("fast", "720p")]
    if with_audio:
        rate += _AUDIO_SURCHARGE_PER_SECOND
    return round(rate * max(0, duration_seconds) * max(0, clips), 2)


# ---------------------------------------------------------------------------
# Provider resolution — same shape as gemini_client._resolve_provider
# ---------------------------------------------------------------------------
def _resolve_provider(provider: str | None = None) -> str:
    """Effective provider: explicit arg > VIDEO_PROVIDER > its key > 'vertex'.

    ⚠ `GEMINI_KEY_VIDEO` IS A SWITCH AS WELL AS A KEY — see `ai_keys`, and note
    that on this capability that cuts both ways: a key pasted here starts
    spending on the Developer API, so `VIDEO_PROVIDER` is the line to set when
    you mean to keep Veo on Vertex.
    """
    p = ai_keys.resolve_provider(CAPABILITY, provider)
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown VIDEO_PROVIDER '{p}'. Use one of {SUPPORTED_PROVIDERS}."
        )
    return p


def _model_id(provider: str, tier: str = DEFAULT_TIER) -> str:
    """Model id for this provider and tier (env-overridable).

    The env var always wins, so a new Veo release needs a .env line, not a
    deploy. Note the override is per-PROVIDER because the two backends name the
    same model differently — see `_MODEL_IDS`.
    """
    override = (
        os.environ.get("GEMINI_VIDEO_MODEL")
        if provider == "gemini"
        else os.environ.get("VERTEX_VIDEO_MODEL")
    )
    if override:
        return override
    tiers = _MODEL_IDS.get(provider, _MODEL_IDS["vertex"])
    return tiers.get(tier, tiers[DEFAULT_TIER])


def available_models(provider: str | None = None) -> list[str]:
    """Veo model ids this project can actually see, newest-looking last.

    Asking the backend beats guessing: the ids differ per provider and change
    between releases, and a wrong one fails with a 404 that reads like a
    permissions error. Returns [] if the listing can't be done — callers treat
    that as "no extra help available", never as "no models".
    """
    try:
        client = get_client(provider)
        names = []
        for model in client.models.list():
            name = (getattr(model, "name", "") or "").split("/")[-1]
            if "veo" in name.lower():
                names.append(name)
        return sorted(set(names))
    except Exception:  # noqa: BLE001 — a diagnostic must never raise
        logger.debug("could not list video models", exc_info=True)
        return []


def _create_client(provider: str):
    """Create a genai Client for the given provider."""
    if provider == "gemini":
        api_key, source = ai_keys.gemini_key(CAPABILITY)
        if not api_key:
            raise VideoGenerationError(
                "VIDEO_PROVIDER=gemini needs a key. "
                + ai_keys.missing_key_hint(CAPABILITY)
                + " Note that a Google AI Pro subscription does NOT include API "
                "access — the key is billed separately."
            )
        client = genai.Client(api_key=api_key)
        logger.info(
            "video client created (provider=gemini Developer API, key=%s)", source
        )
        return client

    # provider == "vertex"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    location = os.environ.get("GOOGLE_CLOUD_VIDEO_LOCATION", "us-central1")
    client = genai.Client(vertexai=True, project=project, location=location)
    logger.info(
        "video client created (provider=vertex, project=%s, location=%s)",
        project, location,
    )
    return client


# One cached client per provider, so both backends can be used in one process.
_clients: dict[str, "genai.Client"] = {}


def get_client(provider: str | None = None):
    """Return the cached genai client for the resolved provider."""
    provider = _resolve_provider(provider)
    if provider not in _clients:
        _clients[provider] = _create_client(provider)
    return _clients[provider]


# ---------------------------------------------------------------------------
# Building the request
# ---------------------------------------------------------------------------
def _as_image(data: bytes | None, mime_type: str = "image/png") -> types.Image | None:
    """Wrap raw bytes as a genai Image, or None when there are none."""
    if not data:
        return None
    return types.Image(image_bytes=data, mime_type=mime_type)


def _build_config(
    *,
    aspect_ratio: str,
    resolution: str,
    duration_seconds: int,
    generate_audio: bool,
    negative_prompt: str | None,
    reference_images: list[bytes] | None,
    last_frame: bytes | None,
    seed: int | None,
) -> types.GenerateVideosConfig:
    """Assemble the config, sending only fields this SDK build actually has.

    The Veo config gains fields release to release; passing an unknown one is a
    TypeError at import-time of the request rather than a graceful degrade, so
    each optional field is checked against the model before it is set. Same
    defensive idiom as gemini_client._image_config.
    """
    fields = types.GenerateVideosConfig.model_fields
    kwargs: dict = {
        "aspect_ratio": aspect_ratio,
        "number_of_videos": 1,
    }

    def _set(name: str, value):
        if value is None:
            return
        if name in fields:
            kwargs[name] = value
        else:
            logger.warning(
                "google-genai has no GenerateVideosConfig.%s; ignoring it. "
                "Upgrade the SDK if you need it.", name,
            )

    _set("resolution", resolution)
    _set("duration_seconds", duration_seconds)
    _set("generate_audio", generate_audio)
    _set("negative_prompt", negative_prompt or None)
    _set("seed", seed)

    # Character/style consistency: up to three stills that tell Veo what the
    # people and the look should be. This is what Flow calls "ingredients", and
    # it is why feeding our own generated turnaround sheets in beats describing
    # the character in words.
    if reference_images:
        refs = [
            types.VideoGenerationReferenceImage(
                image=_as_image(b),
                reference_type=types.VideoGenerationReferenceType.ASSET,
            )
            for b in reference_images[:3]
            if b
        ]
        if refs:
            _set("reference_images", refs)

    # Interpolate to a fixed end frame — panel N → panel N+1.
    _set("last_frame", _as_image(last_frame))

    return types.GenerateVideosConfig(**kwargs)


# ---------------------------------------------------------------------------
# Rendering one shot
# ---------------------------------------------------------------------------
def _extract_bytes(client, operation) -> bytes:
    """Pull the MP4 out of a finished operation.

    Vertex hands back inline bytes; the Gemini Developer API hands back a file
    reference that has to be downloaded. Both shapes land here.
    """
    response = getattr(operation, "response", None) or getattr(operation, "result", None)
    generated = getattr(response, "generated_videos", None) if response else None
    if not generated:
        raise VideoGenerationError(
            "Veo returned no video. This usually means the prompt or the source "
            "image was rejected by the safety filter — try rewording the motion "
            "prompt, or use a different frame."
        )

    video = generated[0].video
    if getattr(video, "video_bytes", None):
        return video.video_bytes

    # Gemini API path: the bytes live behind a short-lived file handle.
    data = client.files.download(file=video)
    if not data:
        raise VideoGenerationError("Veo produced a video but it could not be downloaded.")
    return data


def render_shot(
    image_bytes: bytes | None,
    prompt: str,
    *,
    text_only: bool = False,
    provider: str | None = None,
    tier: str = "fast",
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration_seconds: int = 8,
    generate_audio: bool = True,
    negative_prompt: str | None = None,
    reference_images: list[bytes] | None = None,
    last_frame: bytes | None = None,
    seed: int | None = None,
    label: str = "shot",
    progress_cb=None,
    cancel_check=None,
) -> bytes:
    """Turn a picture + a motion prompt into an MP4, and return its bytes.

    With `text_only=True` and no picture it renders from the prompt alone —
    the same call, the same poll, the same price per second.

    Blocks for the whole render (typically 1–3 minutes). Call it from a worker
    thread, never from a request handler.

    Args:
        image_bytes: the starting frame — a storyboard panel or final-art still.
                     May be None WITH `text_only=True`, which is Veo rendering
                     from the prompt alone (the Media pane's ✨ Video).
        text_only: say so on purpose when there is no starting frame. Without it
                   a missing image is an error, so a still that failed to load
                   can never turn into a paid text-to-video render by accident.
        prompt: what should MOVE. Describe motion and camera, not the picture:
                the picture is already there.
        provider: "vertex" or "gemini". Defaults to VIDEO_PROVIDER (or "vertex").
        tier: "lite" | "fast" | "standard" — price/quality. See estimate_cost_usd.
        reference_images: up to 3 stills locking character/style ("ingredients").
        last_frame: interpolate towards this still instead of a free ending.
        label: used for logging only.
        progress_cb: called with {percent, stage, message} as the render moves.
        cancel_check: called between polls; return True to abandon the render.

    Returns:
        MP4 bytes.

    Raises:
        VideoGenerationError: with a message written for the user.
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider, tier)

    if duration_seconds not in ALLOWED_DURATIONS:
        raise VideoGenerationError(
            f"Veo clips are {', '.join(str(d) for d in ALLOWED_DURATIONS)} seconds; "
            f"got {duration_seconds}."
        )
    if resolution not in ALLOWED_RESOLUTIONS:
        raise VideoGenerationError(
            f"Resolution must be one of {ALLOWED_RESOLUTIONS}; got {resolution!r}."
        )
    # ⚠ THE MISSING IMAGE IS STILL AN ERROR UNLESS THE CALLER SAID SO. Veo will
    # happily render from a prompt alone, and that is a real feature (the Media
    # pane's ✨ Video with no starting still) — but for every other caller here
    # the picture is the whole point, and a still that failed to load must never
    # become a paid text-to-video render of the motion notes. So text-only is
    # something a caller ASKS for, never something it falls into.
    if not image_bytes and not text_only:
        raise VideoGenerationError("No source image for this shot.")
    if not (prompt or "").strip():
        raise VideoGenerationError("This shot has no motion prompt — say what should move.")

    def _report(percent: int, stage: str, message: str = ""):
        if progress_cb:
            try:
                progress_cb({"percent": percent, "stage": stage, "message": message})
            except Exception:  # noqa: BLE001 — progress must never kill a render
                logger.debug("[%s] progress callback failed (ignored)", label, exc_info=True)

    config = _build_config(
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration_seconds=duration_seconds,
        generate_audio=generate_audio,
        negative_prompt=negative_prompt,
        reference_images=reference_images,
        last_frame=last_frame,
        seed=seed,
    )

    last_error: Exception | None = None

    for attempt in range(1, retry_policy.MAX_RETRIES + 1):
        if cancel_check and cancel_check():
            raise VideoGenerationError("Render stopped.")
        try:
            logger.info(
                "[%s] Veo submit (provider=%s, model=%s, %ss %s, attempt %d/%d)",
                label, provider, model_id, duration_seconds, resolution,
                attempt, retry_policy.MAX_RETRIES,
            )
            _report(5, "submitting", f"Sending {label} to Veo…")

            with _throttle():
                operation = client.models.generate_videos(
                    model=model_id,
                    prompt=prompt,
                    image=_as_image(image_bytes),
                    config=config,
                )

                # --- Poll. Veo takes minutes, so this is the bulk of the wait.
                started = time.monotonic()
                while not operation.done:
                    if cancel_check and cancel_check():
                        raise VideoGenerationError("Render stopped.")
                    elapsed = time.monotonic() - started
                    if elapsed > POLL_TIMEOUT_SECONDS:
                        raise VideoGenerationError(
                            f"Veo did not finish {label} within "
                            f"{POLL_TIMEOUT_SECONDS // 60} minutes. Nothing was "
                            f"charged for an unfinished render; try again."
                        )
                    # Creep 10→90% over the expected window so the bar moves
                    # even though the API reports no percentage of its own.
                    _report(
                        min(90, 10 + int(80 * elapsed / 180)),
                        "rendering",
                        f"Veo is rendering {label}… ({int(elapsed)}s)",
                    )
                    time.sleep(POLL_INTERVAL_SECONDS)
                    operation = client.operations.get(operation)

                if getattr(operation, "error", None):
                    raise VideoGenerationError(f"Veo failed: {operation.error}")

                _report(95, "downloading", f"Fetching {label}…")
                data = _extract_bytes(client, operation)

            logger.info("[%s] Veo ok — %.1f MB", label, len(data) / 1_048_576)
            _report(100, "done", "")
            return data

        except VideoGenerationError:
            raise  # already user-facing; retrying won't help
        except Exception as e:  # noqa: BLE001 — classify, then retry or give up
            last_error = e
            if not retry_policy.is_retryable(e):
                logger.error("[%s] Veo call failed permanently: %s", label, e)
                raise VideoGenerationError(_friendly(e)) from e
            if attempt >= retry_policy.MAX_RETRIES:
                break
            delay = retry_policy.backoff_delay(attempt, e)
            logger.warning(
                "[%s] Veo attempt %d/%d failed (%s). Retrying in %.1fs…",
                label, attempt, retry_policy.MAX_RETRIES, e, delay,
            )
            _report(5, "retrying", f"Busy — retrying {label} in {int(delay)}s…")
            time.sleep(delay)

    logger.error("[%s] Veo failed after %d attempts: %s", label, retry_policy.MAX_RETRIES, last_error)
    raise VideoGenerationError(_friendly(last_error) if last_error else "Veo render failed.")


def _friendly(error: Exception) -> str:
    """Turn a backend error into something worth putting on screen."""
    text = str(error)
    low = text.lower()
    if "quota" in low or "429" in low or "resource_exhausted" in low:
        return (
            "Veo quota exhausted. Video quota is per-minute and per-project — "
            "wait a moment and render fewer shots at once, or raise the quota "
            "on your Google Cloud project."
        )
    if "permission" in low or "403" in low or "unauthenticated" in low or "401" in low:
        return (
            "Veo rejected the credentials. Check VIDEO_PROVIDER and its key: "
            "'vertex' needs application-default credentials plus a "
            "billing-enabled GOOGLE_CLOUD_PROJECT, 'gemini' needs GEMINI_API_KEY. "
            "A Google AI Pro subscription does not grant API access."
        )
    if "404" in low or "not found" in low:
        # Don't just say "pick a valid model" — go and ask which ones ARE valid.
        # A 404 here is almost always a name that belongs to the OTHER backend
        # (Vertex uses -001, the Gemini API uses -preview), and the message
        # Google returns blames project access, which sends you hunting IAM for
        # something that is really a typo.
        models = available_models()
        if models:
            return (
                "That Veo model doesn't exist on this backend. Models this "
                f"project CAN use: {', '.join(models)}. Set VERTEX_VIDEO_MODEL "
                "(or GEMINI_VIDEO_MODEL) to one of them in your .env."
            )
        return (
            f"Veo model not available to this project ({text}). Set "
            "VERTEX_VIDEO_MODEL / GEMINI_VIDEO_MODEL to a model you have access to."
        )
    if "safety" in low or "blocked" in low or "prohibited" in low:
        return (
            "Veo blocked this shot on safety grounds. Reword the motion prompt "
            "or use a different source frame."
        )
    if "billing" in low:
        return f"Veo needs billing enabled on the project. {text}"
    return f"Veo render failed: {text}"


def verify_access(provider: str | None = None) -> dict:
    """Cheap check that this backend is usable — credentials and model id.

    Does NOT render (that costs money). Used by /health and the workflow's
    setup banner so a misconfiguration is visible before the first paid call.
    """
    try:
        provider = _resolve_provider(provider)
    except ValueError as e:
        return {"ok": False, "provider": str(provider), "error": str(e)}

    model = _model_id(provider)
    info = {"provider": provider, "model": model, "ok": False, "error": None}
    try:
        get_client(provider)
    except Exception as e:  # noqa: BLE001 — this endpoint reports, never raises
        info["error"] = _friendly(e)
        return info

    # Credentials work. Now check the MODEL, because a good key pointed at a
    # name this backend doesn't have is the failure people actually hit — and
    # it otherwise only shows up after a two-minute render.
    models = available_models(provider)
    if models and model not in models:
        info["error"] = (
            f"'{model}' isn't available here. This project can use: "
            f"{', '.join(models)}. Set VERTEX_VIDEO_MODEL (or "
            "GEMINI_VIDEO_MODEL) to one of them."
        )
        return info

    info["ok"] = True
    return info
