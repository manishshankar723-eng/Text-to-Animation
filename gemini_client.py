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

import hashlib
import io
import os
import re
import threading
import time
import logging
from contextlib import contextmanager

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

import retry_policy

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MODEL_ID = "gemini-3.1-flash-image"
DEFAULT_PROJECT = "project-cf56be07-4f9e-45d4-9f4"
SUPPORTED_PROVIDERS = ("vertex", "gemini")
# Attempt budget and backoff ladder — see retry_policy.py for why they are what
# they are. Re-exported here because callers and tests read them off this module.
MAX_RETRIES = retry_policy.MAX_RETRIES
INITIAL_BACKOFF_SECONDS = retry_policy.INITIAL_BACKOFF_SECONDS
QUOTA_BACKOFF_SECONDS = retry_policy.QUOTA_BACKOFF_SECONDS
QUOTA_BACKOFF_CAP = retry_policy.QUOTA_BACKOFF_CAP


# ---------------------------------------------------------------------------
# Throttling — ONE governor for every image call in the process
# ---------------------------------------------------------------------------
# Every path (background storyboard jobs AND the synchronous cast/props/retry
# endpoints, which run on FastAPI's own threadpool) funnels through this module,
# so capping here bounds real API pressure regardless of who calls.
#   IMAGE_MAX_CONCURRENCY — simultaneous in-flight image requests
#   IMAGE_RPM             — requests per minute (0 disables the rate limit)
# Lowered from 6/120: firing many image calls at once is what blew a per-minute
# quota all in one go (whole boards failing together). Fewer in flight + gentler
# pacing spreads the load so the quota can keep up.
MAX_CONCURRENCY = max(1, int(os.environ.get("IMAGE_MAX_CONCURRENCY", "2")))
# Lowered from 60. Sixty was never a measured number, and a real 42-panel board
# produced **79 quota rejections in twenty minutes** against it: the limiter
# waved requests through at a rate the image model does not grant, every one
# came back 429, and each then sat in exponential backoff (12s, 27s, 48s, 56s)
# until it gave up after five attempts. Five panels failed outright and the
# board crawled. This is a STARTING point only — the real ceiling is discovered
# at runtime by the adaptive bucket below, so being a little low costs a little
# ramp-up time and being too high costs failed panels.
IMAGE_RPM = max(0, int(os.environ.get("IMAGE_RPM", "12")))
# Never throttle below this, however many 429s arrive — at some point the quota
# is simply gone for the period and crawling makes no difference.
MIN_RPM = 3.0


class _TokenBucket:
    """Thread-safe token bucket that FINDS the quota instead of guessing it.

    A fixed requests-per-minute number is always wrong for somebody: quotas
    differ by project, model, region and time of day, and the only setting that
    is right is the one the API is currently willing to serve. So this is AIMD,
    the same additive-increase / multiplicative-decrease that TCP uses:

      * a quota rejection HALVES the rate (down to MIN_RPM) — back off hard and
        immediately, because every further request is making it worse;
      * sustained success creeps the rate back up toward the configured ceiling
        and never past it.

    The effect is that a board settles at whatever rate the project actually
    allows, within a minute or so, without anyone tuning an env var.
    """

    def __init__(self, rpm: int, burst: int = 2):
        self.max_rate = rpm / 60.0 if rpm > 0 else 0.0  # tokens per second
        self.rate = self.max_rate
        self.capacity = float(max(rpm, 1))
        # Start with only a SMALL burst, not a full minute's worth of tokens —
        # otherwise the first N calls fire instantly with no pacing at all (which
        # is exactly what let a whole board hit the quota at once).
        self._tokens = float(min(self.capacity, max(1, burst)))
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available (no-op when the limit is disabled)."""
        if self.max_rate <= 0:
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
                wait = (1.0 - self._tokens) / max(self.rate, 1e-6)
            time.sleep(min(wait, 5.0))

    def penalise(self) -> float:
        """A quota rejection: halve the rate. Returns the new rpm."""
        if self.max_rate <= 0:
            return 0.0
        with self._lock:
            floor = MIN_RPM / 60.0
            self.rate = max(floor, self.rate / 2.0)
            # Drop any banked tokens too, or the next few calls ignore the new
            # rate entirely and walk straight back into the quota.
            self._tokens = 0.0
            return self.rate * 60.0

    def reward(self) -> None:
        """A success: creep back toward the configured ceiling."""
        if self.max_rate <= 0 or self.rate >= self.max_rate:
            return
        with self._lock:
            # +1 rpm per success. Slow enough not to re-trip the quota straight
            # after recovering, quick enough to matter over a 40-panel board.
            self.rate = min(self.max_rate, self.rate + 1.0 / 60.0)


_semaphore = threading.BoundedSemaphore(MAX_CONCURRENCY)
_bucket = _TokenBucket(IMAGE_RPM)


def note_quota_error() -> None:
    """Tell the shared limiter that the API just refused us for quota."""
    rpm = _bucket.penalise()
    if rpm:
        logger.warning(
            "[throttle] quota rejection — image rate reduced to %.1f req/min "
            "for this process; it will climb back as calls start succeeding.", rpm,
        )


def note_success() -> None:
    """Tell the shared limiter that a call went through."""
    _bucket.reward()


@contextmanager
def _throttle():
    """Hold a concurrency slot and a rate-limit token for one API call, and
    feed the outcome back into the rate.

    The feedback lives HERE rather than at the four call sites because every
    image call in the process already goes through this one context manager —
    putting it anywhere else would mean four copies to keep in step, and the
    one that got missed would be the one hammering the quota.
    """
    _bucket.acquire()
    _semaphore.acquire()
    try:
        yield
    except Exception as e:  # noqa: BLE001 — classify, then re-raise untouched
        if _is_quota_error(e):
            note_quota_error()
        raise
    else:
        note_success()
    finally:
        _semaphore.release()


# ---------------------------------------------------------------------------
# Error classification — only retry what can actually succeed on a retry
# ---------------------------------------------------------------------------
# The policy itself lives in retry_policy.py so the VIDEO backend obeys the same
# rules instead of keeping a second copy that drifts. These aliases keep the
# call sites in this file unchanged.
_is_retryable = retry_policy.is_retryable
_is_quota_error = retry_policy.is_quota_error
_retry_after_seconds = retry_policy.retry_after_seconds
_backoff_delay = retry_policy.backoff_delay


# ---------------------------------------------------------------------------
# Image determinism — seeding
# ---------------------------------------------------------------------------
# MEASURED, 2026-08-03, gemini-3.1-flash-image on Vertex — read this before
# relying on it:
#   - The API ACCEPTS `seed` and it does influence the output. Two back-to-back
#     calls with seed=42 returned a PIXEL-IDENTICAL image (max channel diff 0);
#     seed=999 returned a clearly different one (max diff 223).
#   - It is NOT a reproducibility guarantee. The same experiment on storyboard
#     panel requests — seed verified identical and present in the config —
#     returned DIFFERENT images both times. The runs that reproduced were
#     back-to-back; the ones that didn't were spread out by quota backoff.
#     Best guess: you reproduce while you land on the same serving replica.
# So: seeding is a best-effort improvement, not a promise. Do not tell users a
# board will redraw identically.
#
# When testing this, compare PIXELS, never PNG bytes — the encoder is not
# byte-stable, so two identical pictures hash differently and a bytes comparison
# reports "not reproducible" when it is.
#
# The seed is derived from WHAT IS BEING DRAWN, not fixed globally. One constant
# seed for every call would quietly break every Retry button in the app: they
# resend an identical request, so they would get back the identical picture
# forever. Deriving it per-request means different panels still differ naturally
# (different prompt → different seed) while a re-run of the same board
# reproduces.
#
#   variation=0     (default) reproducible — same request, same picture
#   variation=None  send no seed; the backend varies it. This is what the
#                   Retry/regenerate paths pass to guarantee a NEW picture.
#   variation=n     a different, still-reproducible picture from one request.
#
# A `redraw` counter is mixed in as well, incremented ONLY when an image came
# back and was REJECTED (wrong shape / too small). That one has to change the
# seed, or a rejected image would be redrawn identically until the retries ran
# out. It deliberately does NOT count transport failures: a 429 is not the
# picture's fault, and counting those would mean any quota blip silently
# produced a different board. (Learned the hard way — the first version keyed on
# the retry attempt number and a single 429 broke reproducibility.)
#
# IMAGE_SEED sets the base (any integer) or "none"/"off" to disable seeding and
# restore the previous always-varying behaviour.
DEFAULT_IMAGE_SEED = 42
_SEED_MODULUS = 2**31 - 1  # keep the value in signed-32-bit range


def _seed_for(*parts: object, variation: int | None = 0) -> int | None:
    """Stable seed for one image request, or None to let the backend vary it."""
    base = (os.environ.get("IMAGE_SEED") or str(DEFAULT_IMAGE_SEED)).strip()
    if variation is None or base.lower() in ("", "none", "off", "random"):
        return None
    material = "|".join([base, str(variation), *(str(p) for p in parts)])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % _SEED_MODULUS


def _image_config(seed: int | None, **extra) -> types.GenerateContentConfig:
    """Image-generation config, carrying `seed` only when we have one to send."""
    kwargs: dict = {"response_modalities": ["IMAGE"], **extra}
    if seed is not None:
        if "seed" in types.GenerateContentConfig.model_fields:
            kwargs["seed"] = seed
        else:  # older google-genai — degrade to the old varying behaviour
            logger.warning(
                "google-genai has no `seed` field; image generation will not be "
                "reproducible. Upgrade the SDK."
            )
    return types.GenerateContentConfig(**kwargs)


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
    variation: int | None = 0,
) -> Image.Image | None:
    """
    Send a reference image + prompt to Gemini and get back a 2×2 turnaround sheet.

    Args:
        reference_image: PIL Image to use as reference (the uploaded character
                         photo for fullbody, or the fullbody sheet for other parts).
        prompt: The text prompt describing what to generate.
        part_name: Name of the part (for logging only).
        provider: "vertex" or "gemini". Defaults to IMAGE_PROVIDER env (or "vertex").
        variation: seeding control — 0 reproduces the same sheet for the same
                   prompt, None lets the backend vary it (what regeneration
                   passes), n gives a different reproducible sheet. See _seed_for.

    Returns:
        PIL Image of the 2×2 sheet, or None if generation failed
        (e.g. content filter blocked it).
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)

    redraw = 0  # bumped only when a returned sheet is REJECTED — see _seed_for

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
                    config=_image_config(
                        _seed_for(prompt, part_name, redraw, variation=variation)
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
                            redraw += 1  # new seed next time, or we redraw this exact sheet
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

# ---------------------------------------------------------------------------
# World / cultural context — prefixed onto EVERY image prompt
# ---------------------------------------------------------------------------
# Image models default to Western/European faces, clothing and architecture
# unless told otherwise, which is how a Shiva Purana script produced a white
# hunter. The script breakdown reads the story's region, period, culture and
# people (script_breakdown.WORLD_FIELDS) and that block is carried into every
# generation — character references, prop and background references, and each
# panel — through this one builder, so all four can never drift apart.
_WORLD_LABELS = {
    "setting": "Setting",
    "culture": "Cultural / religious tradition",
    "ethnicity": "The people of this world look like",
    "wardrobe": "Clothing",
    "environment": "Architecture, landscape and objects",
    "notes": "Also important",
}
# The instruction that does the actual work — naming the culture is not enough,
# the model has to be told not to fall back on its default.
_WORLD_RULE = (
    "Everyone and everything drawn must belong authentically to this world: "
    "correct skin tones, facial features, hair, clothing, architecture, props "
    "and iconography for this region, period and tradition. Do NOT default to "
    "Western/European faces, dress or settings, and do not mix in other cultures."
)


def build_world_context(world: dict | None) -> str:
    """One paragraph of cultural/period art direction, or "" when unknown.

    Args:
        world: {setting, culture, ethnicity, wardrobe, environment, notes} from
            the script breakdown (any subset; empty values are skipped).

    Returns:
        Prompt text to prefix onto an image prompt, or "" if nothing is known —
        in which case the prompt is left exactly as it was before this existed.
    """
    if not isinstance(world, dict):
        return ""
    lines = [
        f"{label}: {str(world.get(field) or '').strip()}."
        for field, label in _WORLD_LABELS.items()
        if str(world.get(field) or "").strip()
    ]
    if not lines:
        return ""
    return "WORLD OF THIS STORY — " + " ".join(lines) + " " + _WORLD_RULE


# ---------------------------------------------------------------------------
# The written continuity bible
#
# A storyboard is not a set of illustrations, it is ONE film cut into pictures,
# and the thing that makes it read as one film is that the same person looks the
# same in every panel. Reference IMAGES do that job when they exist — but the
# default style (rough sketch) skips the cast step on purpose, so most boards
# have none, and a panel handed the bare name "Lead Thug" draws a different man
# every time. That was the reported bug.
#
# The breakdown has ALREADY written a visual description of every character and
# every locked prop / set, for free, as text. Putting those words into each
# panel prompt is the cheapest consistency there is: no extra image call, no
# extra second of wall clock, nothing to set up.
# ---------------------------------------------------------------------------
_CAST_RULE = (
    "These are the SAME people in every panel of this film. Draw each one "
    "exactly as described — same face, same hair, same build, same clothing, "
    "same age — no matter the camera angle or how close the shot is. A close-up "
    "must be recognisably the same person as the wide shot before it. Never "
    "invent a different-looking person for a name listed here, and never swap "
    "two characters' looks."
)
_SET_RULE = (
    "These props and places recur across the film and must look the SAME every "
    "time: same shape, same colour, same layout, same lighting."
)


# Words that carry no identity on their own. "The Lead Thug" and "Thug Leader"
# are the same man; "Thug 1" and "Thug 2" are not, which is why digits are kept.
_NAME_NOISE = frozenset(
    ("the", "a", "an", "lead", "leader", "main", "young", "old", "elder", "mr",
     "mrs", "ms", "miss", "sri", "shri", "smt")
)
_NAME_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _norm_name(name: str) -> str:
    """A name reduced to something two spellings of it can agree on."""
    return " ".join(t for t in _NAME_SPLIT_RE.split(str(name).lower()) if t)


def _name_tokens(name: str) -> frozenset:
    return frozenset(t for t in _norm_name(name).split() if t not in _NAME_NOISE)


def resolve_name(name: str, mapping: dict | None):
    """The entry in `mapping` that `name` refers to, or None.

    Exact (normalised) match first. Failing that, the entry sharing the most
    identifying words — so a shot's "Lead Thug" finds the cast's "Thug Leader".
    A TIE is treated as no match: with "Thug 1" and "Thug 2" both half-matching,
    guessing would put the wrong face in the panel, which is the very bug this
    exists to prevent.
    """
    if not isinstance(mapping, dict) or not mapping:
        return None
    by_norm = {_norm_name(k): v for k, v in mapping.items()}
    key = _norm_name(name)
    if key in by_norm:
        return by_norm[key]

    wanted = _name_tokens(name)
    if not wanted:
        return None
    scored: list[tuple[int, str]] = []
    for k in mapping:
        overlap = len(wanted & _name_tokens(k))
        if overlap:
            scored.append((overlap, _norm_name(k)))
    if not scored:
        return None
    best = max(s[0] for s in scored)
    winners = {k for score, k in scored if score == best}
    if len(winners) != 1:
        return None  # ambiguous — better no description than the wrong one
    return by_norm[winners.pop()]


def _bible_lines(names, bible: dict | None, limit: int = 8) -> list[str]:
    """"Name: description." for each name that the bible knows about.

    Matching is by NORMALISED name (see `resolve_name`), because a shot says
    "Lead Thug" where the cast list says "Thug Leader" often enough that exact
    matching quietly drops the description and the model is back to inventing.
    """
    lines, seen = [], set()
    for raw in names or []:
        name = str(raw).strip()
        key = _norm_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        desc = str((resolve_name(name, bible) or "")).strip().rstrip(".")
        if desc:
            lines.append(f"{name} — {desc}.")
        if len(lines) >= limit:
            break
    return lines


def build_cast_context(characters, character_bible: dict | None) -> str:
    """The cast of THIS panel, described, or "" when nothing is known.

    Only the characters in this shot: handing the model the whole cast of a
    twelve-shot film invites it to draw people who are not in the scene.
    """
    lines = _bible_lines(characters, character_bible)
    if not lines:
        return ""
    return "THE CAST IN THIS PANEL — " + " ".join(lines) + " " + _CAST_RULE


def build_set_context(asset_bible: dict | None) -> str:
    """The locked props / sets for this panel, described, or ""."""
    if not isinstance(asset_bible, dict) or not asset_bible:
        return ""
    lines = [
        f"{name} — {str(desc).strip().rstrip('.')}."
        for name, desc in list(asset_bible.items())[:6]
        if str(desc or "").strip()
    ]
    if not lines:
        return ""
    return "PROPS AND PLACES IN THIS PANEL — " + " ".join(lines) + " " + _SET_RULE


def build_flow_context(story_context: dict | None) -> str:
    """Where this panel sits in the story — context ONLY, never drawn.

    This is the difference between a panel and a SHOT. Told what happened a
    second ago and what happens a second later, the model draws a moment that
    continues the action — the character is still in the same room, wearing the
    same clothes, mid-way through the same movement. Told nothing, it draws a
    fresh, self-contained illustration of a sentence, which is exactly why a
    finished board plays like twelve unrelated pictures instead of a film.
    """
    if not isinstance(story_context, dict):
        return ""
    previous = str(story_context.get("previous") or "").strip()
    following = str(story_context.get("next") or "").strip()
    shot_no = story_context.get("shot_number")
    of = story_context.get("of")
    scene_no = story_context.get("scene_number")
    same_scene = bool(story_context.get("previous_same_scene"))

    bits: list[str] = []
    if shot_no and of:
        where = f"This is shot {shot_no} of {of} in the film"
        if scene_no:
            where += f", scene {scene_no}"
        bits.append(where + ".")
    if previous:
        bits.append(f"The shot immediately BEFORE this one showed: {previous}")
        if same_scene:
            bits.append(
                "That was the same scene, moments ago — the same characters in "
                "the same place, wearing the same clothes, in the same light. "
                "This panel continues that action from where it got to."
            )
    if following:
        bits.append(f"The shot immediately AFTER this one shows: {following}")
    if not bits:
        return ""
    return (
        "STORY CONTINUITY (context only — draw ONLY this panel's moment, never "
        "the ones either side of it) — " + " ".join(bits) + " Draw this as one "
        "frame of a continuous film, not as a standalone illustration."
    )


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
    "comic": "bold colourful comic-book artwork, strong ink outlines, dramatic cel shading",
    "cinematic": "photorealistic cinematic film still, natural lighting, shallow depth of field, filmic colour grade",
    "soft-pencil": "soft graphite pencil storyboard sketch, light hand-drawn shading, clean paper texture",
    "animation-3d": "polished Pixar-style 3D animated render, soft global illumination, expressive characters",
    "watercolor": "soft watercolour painting, gentle washes of colour, visible paper texture, loose edges",
    "photo-commercial": "clean commercial photography look, bright even lighting, crisp product-style framing",
    "charcoal": "expressive black-and-white charcoal drawing, rich smudged shadows, bold strokes",
    "dark-anime": "moody dark anime style, cinematic anime lighting, detailed line art, high contrast",
    # The default. A real animation-department thumbnail: drawn to communicate
    # staging, not to be admired. Deliberately verbose — this style has to fight
    # the model's pull toward rendering, colour and detail, and it's the one
    # style whose whole value is that there's nothing in it to get wrong.
    "rough-sketch": (
        "a rough hand-drawn animation storyboard thumbnail on white paper, "
        "greyscale only — black pencil or marker linework with flat grey marker "
        "tones in TWO OR THREE values at most, no gradients, no rendering, no "
        "texture, no cross-hatching; loose confident tapered strokes with visible "
        "construction lines and honest wobble, as if drawn quickly by hand; "
        "simple shapes and clear readable silhouettes, expressive exaggerated "
        "poses, faces suggested with a few marks rather than detailed; only the "
        "background elements the shot actually needs, everything else left as "
        "blank paper; absolutely no colour, no photorealism, no fine detail, no "
        "digital polish"
    ),
    "flat-vector": "flat vector illustration, clean geometric shapes, bold solid colours, minimal shading",
    "noir": "high-contrast black-and-white film-noir look, deep shadows, dramatic low-key lighting",
    "stick-figure": "simple black-and-white stick-figure storyboard, minimal line drawing on a white background",
    "graphic-novel": "gritty graphic-novel artwork, inked linework, muted cinematic colour palette",
    "custom": "clean illustrated storyboard artwork",
    # Back-compat aliases for older style ids.
    "sketch": "black-and-white rough pencil storyboard sketch, loose gestural lines, minimal shading",
    "comics": "bold colourful comic-book artwork, strong ink outlines, dramatic cel shading",
    "realistic": "photorealistic cinematic film still, natural lighting, shallow depth of field",
    "3d-animation": "polished Pixar-style 3D animated render, soft global illumination",
}

# THE STYLES THAT ARE NOT ALLOWED TO HAVE COLOUR.
#
# Their prompts already say so — "greyscale only", "absolutely no colour",
# "black-and-white" — and the model ignores it often enough to ruin a board.
# Measured on one real 15-panel rough-sketch board: panels 1 and 4 came back as
# fully coloured illustrations while the rest were grey, and inside ONE shot's
# 8-pose flipbook a single pose came back in colour. That is the user's
# "colours change" report, and no amount of prompt wording fixes it reliably.
#
# So it is enforced in code instead: for these styles the picture is desaturated
# after generation. Free, instant, and it cannot fail. See
# storyboard_pipeline.conform_to_style().
GREYSCALE_STYLES = frozenset(
    ("rough-sketch", "sketch", "soft-pencil", "charcoal", "noir", "stick-figure")
)


def is_greyscale_style(style: str) -> bool:
    """Does this style forbid colour? Unknown / freeform styles do not."""
    return (style or "").strip().lower() in GREYSCALE_STYLES


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
    composition_purpose: str = "restyle",
    shot_invariant: str = "",
    scene_reference_image: "Image.Image | None" = None,
    provider: str | None = None,
    world: dict | None = None,
    variation: int | None = 0,
    character_bible: dict | None = None,
    asset_bible: dict | None = None,
    story_context: dict | None = None,
) -> Image.Image | None:
    """Generate ONE storyboard panel image from a shot description.

    Uses the IMAGE backend (IMAGE_PROVIDER / `provider` arg) — panels are images.
    Style + aspect + camera + location are woven into the prompt. Optional
    `reference_images` (character references) and `asset_reference_images`
    (prop / background references) are passed alongside so the depicted
    characters, key props and backgrounds stay visually consistent across panels
    (Stage B). `composition_reference_image` is an existing render of THIS shot;
    `composition_purpose` says what to do with it and MUST be set correctly:
    "restyle" keeps the staging AND the action and changes only the art style,
    while "repose" keeps the staging and the style and CHANGES the action. They
    are opposite instructions — see the comment at the branch below for the bug
    that sending the wrong one causes. `shot_invariant` is the fence around a
    "repose": one sentence naming what stays true in every drawing of the shot,
    so "the body has physically moved" cannot be read as licence to invent a new
    action. `world` is the script's region/period/culture block —
    see build_world_context(); it keeps the people and places in the panel true
    to the story instead of the model's Western default. Returns a PIL image, or
    None if the model returned nothing (e.g. safety filter).

    THE THREE CONTINUITY CHANNELS (why a board looks like ONE film):

    1. `character_bible` / `asset_bible` — {name: visual description} from the
       script breakdown. This is the WRITTEN continuity, and it is the one that
       works even when there are no reference images at all, which is the normal
       case for the rough-sketch style (it skips the cast step by design). A
       panel that is handed only the NAME "Lead Thug" invents a new face every
       single time; handed the description, it draws the same man. Costs nothing
       — the breakdown already produced these words.
    2. `scene_reference_image` — a panel from EARLIER IN THE SAME SCENE, used as
       a look reference, never as a composition. Anchored on one fixed picture
       per scene rather than chained panel→panel, for the reason spelled out in
       panel_sequence's docstring: chaining compounds drift.
    3. `story_context` — {previous, next, scene_number, shot_number, of} — what
       happens either side of this panel. NOT drawn. It is what turns a bag of
       illustrations into consecutive shots of one continuous action.
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

    # "A single storyboard PANEL" is what taught the model to draw the BOX. In
    # comics and storyboard training data a panel IS a bordered rectangle, so
    # asking for one gets you a sketchy frame just inside the edge with white
    # paper around it — freehand, so no two on a board ever match. Asking for
    # the IMAGE instead, and saying full-bleed in the same breath, removes the
    # cue at its source. `strip_drawn_border()` cleans up what still gets drawn.
    parts = [
        f"A single full-bleed storyboard IMAGE — the photograph-like picture "
        f"itself with NO border, NO frame and NO box drawn around it: {style_txt}.",
        f"{aspect_txt}.",
    ]
    # The story's world comes BEFORE the shot detail, so the model has the
    # culture in hand while it reads what to draw.
    world_txt = build_world_context(world)
    if world_txt:
        parts.append(world_txt)
    # The written continuity bible, BEFORE the shot description. These people
    # and places are fixed for the whole film; the shot only says what they are
    # doing right now.
    cast_txt = build_cast_context(characters, character_bible)
    if cast_txt:
        parts.append(cast_txt)
    else:
        if characters:
            parts.append("Characters present: " + ", ".join(characters) + ".")
    set_txt = build_set_context(asset_bible)
    if set_txt:
        parts.append(set_txt)
    flow_txt = build_flow_context(story_context)
    if flow_txt:
        parts.append(flow_txt)
    if camera:
        parts.append(f"Camera: {camera}.")
    if location:
        parts.append(f"Location: {location}.")
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
    if scene_reference_image is not None:
        parts.append(
            "One of the reference images is ANOTHER PANEL FROM THIS SAME SCENE, "
            "already drawn. Match it: the same people with the same faces and "
            "clothes, the same room and furniture, the same time of day and "
            "light, the same drawing style, line weight and grey values. Do NOT "
            "copy its composition — this is a different moment from a different "
            "camera position. It shows you what this scene LOOKS like, not what "
            "to draw."
        )
    if composition_reference_image is not None:
        # WHY the reference is here decides what to say about it, and getting
        # this wrong is not a nuance — it is the difference between a flipbook
        # and eight copies of one drawing. The re-style wording below ends with
        # "do not change what is happening", which is correct for a re-style and
        # catastrophic for a key pose: sent with a pose instruction it wins,
        # because it is the more absolute of the two, and the model returns the
        # same picture with the shading nudged. That shipped, and the user's
        # 8-pose strip came back with the head frozen to within 3 pixels.
        if composition_purpose == "repose":
            parts.append(
                "The LAST reference image is THE SAME SHOT, one moment earlier. "
                "Keep from it: the camera position and framing, the character's "
                "face and design, the clothing, the background and every object "
                "in it, the drawing style, the line weight and THE EXACT SAME "
                "TONAL VALUES — the same greys, the same palette, the same "
                "darkness of ink. "
                "CHANGE from it: the POSE. This is a later moment and the body "
                # Was "the head, neck and shoulders must sit in a visibly
                # DIFFERENT position" — right for a close-up and wrong for a
                # wide, where it asks for the one movement too small to read at
                # that size and pushes the model to enlarge the action until it
                # does. The pose line below already names the part that moves.
                "has physically moved. The part of the body named below must sit "
                "in a visibly DIFFERENT position — turned, tilted, raised, "
                "dropped, leaned or shifted exactly as described, and far enough "
                "to read at this shot's framing. Someone flipping between the "
                "reference and your drawing must see a body that MOVED, not the "
                "same drawing re-rendered. Re-drawing the reference pose, or "
                "changing only the facial expression, is a failed drawing."
            )
            # HOW FAR THAT MOVEMENT MAY GO. Everything above is a push, and a
            # push with no limit is how a shot of a sleeping man became a shot
            # of him waking up: told the body must visibly move and given no
            # boundary, the model picks the biggest movement the scene affords.
            # This is the boundary, and it comes last so it reads as the final
            # word on the paragraph above.
            if str(shot_invariant or "").strip():
                parts.append(
                    "WHAT MUST NOT CHANGE, in this drawing and every other "
                    "drawing of this shot: " + str(shot_invariant).strip() + " "
                    "This overrides everything above about movement. The body "
                    "moves ONLY as far as the moment described below, and never "
                    "out of the state named here — if the two ever pull against "
                    "each other, this one wins and the movement gets smaller. "
                    "Never draw an action that is not described below."
                )
        else:
            parts.append(
                "The LAST reference image is the existing version of THIS exact panel. "
                "Keep its composition, camera angle, staging and the positions of "
                "everything in frame the same — ONLY re-render it in the new art style "
                "described above. Do not change the layout or what is happening."
            )
    parts.append(f"Scene: {description}")
    parts.append("Single frame. No text, captions, speech bubbles, borders or watermarks.")
    # Asked for one panel, the model otherwise oscillates between drawing edge to
    # edge and dropping a small sketch onto a big blank page — measured at 64% to
    # 96% frame coverage across one board, which is what made a finished board
    # look like a jumble of sizes. normalise_panel() cleans up what still slips
    # through; this reduces how often it has to.
    # Said twice, in different words, and last — because the first version of
    # this ("no drawn frame or paper edge around the picture") was buried mid-
    # prompt and lost to the word "panel" above it. Reported twice.
    parts.append(
        "ABSOLUTELY NO BORDER. Do not draw a frame, a box, a rectangle, a ruled "
        "or sketched outline, a panel edge, a paper edge, a mount, a vignette or "
        "any kind of line around the picture. There must be no white or blank "
        "margin between the artwork and the edge of the image, and no "
        "letterboxing. The artwork BLEEDS off all four sides: the scene is cut "
        "off by the edge of the image itself, exactly as a photograph or a film "
        "frame is, and every corner of the image is scene. If you are about to "
        "draw the outline of the panel, do not — draw only what is inside it."
    )
    # THE FACE IS THE SHOT. A storyboard exists to show who is doing what and
    # what they feel, and a close-up whose face is half-swallowed by a school
    # bench communicates neither — reported ("shot 17 face and school banch
    # marge face hide of banch"). The model is happy to run a desk edge straight
    # through someone's chin because it is composing a picture, not staging a
    # shot; this tells it which of the two it is doing.
    parts.append(
        "STAGING: keep every character's face completely visible and "
        "unobstructed. Nothing in the foreground — a desk, a bench, a table, a "
        "chair back, a prop — may cross, cover or merge with anyone's face; put "
        "such things below the chin or out of the way, and leave clear space "
        "between a face and the edge of the frame. Never crop a head at the top "
        "of the frame in a close-up. Faces must read instantly as faces."
    )
    # Keep borderline (mild-conflict) shots from tripping the safety filter.
    parts.append(
        "Non-graphic, family-friendly storyboard illustration suitable for a "
        "general audience; depict any conflict in a light, cartoonish way."
    )
    prompt = " ".join(parts)

    # Prompt first, then character refs, prop/background refs, the scene's look
    # anchor, then (last) the existing panel as a composition reference for
    # re-styling. The order matches how the prompt above refers to them.
    contents = [prompt, *(reference_images or []), *(asset_reference_images or [])]
    if scene_reference_image is not None:
        contents.append(scene_reference_image)
    if composition_reference_image is not None:
        contents.append(composition_reference_image)

    redraw = 0  # bumped only when a returned panel is REJECTED — see _seed_for

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[panel] Generating storyboard panel (provider=%s, model=%s, char_refs=%d, asset_refs=%d, cast_bible=%d, scene_anchor=%s, composition=%s, attempt %d/%d)…",
                provider, model_id, len(reference_images or []),
                len(asset_reference_images or []), len(_bible_lines(characters, character_bible)),
                scene_reference_image is not None, composition_reference_image is not None,
                attempt, MAX_RETRIES,
            )
            with _throttle():
                response = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=_image_config(
                        _seed_for(prompt, redraw, variation=variation)
                    ),
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
                        redraw += 1  # new seed next time, or we redraw this exact panel
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
    world: dict | None = None,
    variation: int | None = 0,
) -> Image.Image | None:
    """
    Generate a single T-pose character reference image from a text description.

    This is "Step 0" of the pipeline — it produces an image like kamla.jpg that
    can then be fed into the full turnaround generation pipeline.

    Args:
        description: Free-form character description, e.g.
                     "An Indian woman in a red saree, age 30, medium brown skin".
        provider: "vertex" or "gemini". Defaults to IMAGE_PROVIDER env (or "vertex").
        world: The script's region/period/culture block (see build_world_context).
               Without it the model draws its default — which is how an ancient
               Indian hunter came back looking Western.

    Returns:
        PIL Image of the character in T-pose on white background,
        or None if generation failed.
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)
    world_txt = build_world_context(world)
    prompt = _REFERENCE_PROMPT_TEMPLATE.format(description=description)
    if world_txt:
        prompt = f"{world_txt} {prompt}"

    # Track the most specific reason so the caller can report the ACTUAL cause.
    last_reason = "Unknown error generating the reference image."
    redraw = 0  # bumped only when a returned image is REJECTED — see _seed_for

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
                    config=_image_config(
                        _seed_for(prompt, redraw, variation=variation)
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
                            redraw += 1  # new seed, or we redraw this exact image
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
    world: dict | None = None,
    variation: int | None = 0,
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
        world: The script's region/period/culture block (see build_world_context)
               — a hut, a cooking pot and a temple all differ by culture.

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
    world_txt = build_world_context(world)
    if world_txt:
        prompt = f"{world_txt} {prompt}"

    last_reason = "Unknown error generating the asset reference image."
    redraw = 0  # bumped only when a returned image is REJECTED — see _seed_for

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
                    config=_image_config(
                        _seed_for(prompt, cat, redraw, variation=variation)
                    ),
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
                        redraw += 1  # new seed, or we redraw this exact image
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

