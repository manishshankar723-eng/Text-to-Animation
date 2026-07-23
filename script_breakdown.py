"""
script_breakdown.py — Stage A of the Script → Storyboard workflow.

Takes a raw script (or story text) and uses a Gemini text model to break it into
an ordered list of storyboard "shots" — one panel per shot — each with a short
visual description, the characters present, the location, and a camera angle.

TWO BACKENDS — switch freely (mirrors gemini_client.py's image backend):

    TEXT_PROVIDER=vertex   (default)
        Vertex AI. Auth via Application Default Credentials
        (gcloud auth application-default login). Uses GOOGLE_CLOUD_PROJECT +
        GOOGLE_CLOUD_LOCATION (usually "global").

    TEXT_PROVIDER=gemini
        Gemini Developer API. Auth via GEMINI_API_KEY (or GOOGLE_API_KEY).

Set it globally in .env, or pass provider="gemini"/"vertex" per call. Model ids
are overridable via VERTEX_TEXT_MODEL / GEMINI_TEXT_MODEL.
"""

import json
import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TEXT_MODEL = "gemini-2.5-flash"
DEFAULT_PROJECT = "project-cf56be07-4f9e-45d4-9f4"
SUPPORTED_PROVIDERS = ("vertex", "gemini")
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 4  # doubles each retry: 4s, 8s, 16s
# Hard cap so a huge script can't produce a runaway number of panels.
MAX_SHOTS = 60


class ScriptBreakdownError(Exception):
    """Raised when a script can't be broken down into shots.

    Carries a human-readable reason so the API can surface the ACTUAL cause.
    """


class _Retry(Exception):
    """Internal signal: retry the generation (e.g. malformed JSON)."""


# ---------------------------------------------------------------------------
# Provider resolution (independent from the image backend, same shape)
# ---------------------------------------------------------------------------
def _resolve_provider(provider: str | None = None) -> str:
    """Resolve the effective provider: explicit arg > TEXT_PROVIDER env > 'vertex'."""
    p = (provider or os.environ.get("TEXT_PROVIDER", "vertex")).strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown TEXT_PROVIDER '{p}'. Use one of {SUPPORTED_PROVIDERS}."
        )
    return p


def _model_id(provider: str) -> str:
    """Text model id for the given provider (env-overridable, shared default)."""
    if provider == "gemini":
        return os.environ.get("GEMINI_TEXT_MODEL", DEFAULT_TEXT_MODEL)
    return os.environ.get("VERTEX_TEXT_MODEL", DEFAULT_TEXT_MODEL)


def _create_client(provider: str):
    """Create a genai Client for the given provider (text generation)."""
    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "TEXT_PROVIDER=gemini requires GEMINI_API_KEY (or GOOGLE_API_KEY) "
                "to be set in your .env."
            )
        client = genai.Client(api_key=api_key)
        logger.info("genai text client created (provider=gemini Developer API)")
        return client

    # provider == "vertex"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    client = genai.Client(vertexai=True, project=project, location=location)
    logger.info(
        "genai text client created (provider=vertex, project=%s, location=%s)",
        project, location,
    )
    return client


# One cached client per provider so both backends can coexist in one process.
_clients: dict[str, "genai.Client"] = {}


def get_client(provider: str | None = None):
    """Return the cached genai client for the resolved provider."""
    provider = _resolve_provider(provider)
    if provider not in _clients:
        _clients[provider] = _create_client(provider)
    return _clients[provider]


# ---------------------------------------------------------------------------
# Prompt + response schema
# ---------------------------------------------------------------------------
_SYSTEM_INSTRUCTION = (
    "You are a professional film storyboard supervisor. You read a script and "
    "break it into a clear, ordered SHOT LIST for a storyboard artist. Each shot "
    "is ONE storyboard panel: a single moment we can draw. Keep descriptions "
    "concrete and visual (what the camera sees), not internal thoughts. Infer a "
    "sensible location and camera angle when the script doesn't state them. Do "
    "NOT invent major plot the script doesn't imply. Split long actions into "
    "multiple shots when the visual clearly changes."
)

_PROMPT_TEMPLATE = (
    "Break the following script into a storyboard shot list AND a short cast list.\n"
    "Return between 1 and {max_shots} shots, in reading order.\n"
    "For each shot provide:\n"
    "  - scene_number: which scene it belongs to (start at 1)\n"
    "  - shot_number: sequential shot index across the whole script (start at 1)\n"
    "  - description: one vivid sentence describing what we SEE in this panel\n"
    "  - characters: list of character names visible in the shot (empty if none)\n"
    "  - location: where the shot takes place\n"
    "  - camera: the shot type / angle, e.g. 'wide establishing', 'close-up', "
    "'over-the-shoulder', 'medium two-shot'\n"
    "Also return `characters`: every NAMED character in the script, each with a "
    "concise VISUAL description (age, build, hair, clothing, distinguishing "
    "features) an artist could draw consistently. Use the SAME name spelling in "
    "both the shots and the cast list.\n\n"
    "SCRIPT:\n{script}"
)


def _breakdown_schema() -> types.Schema:
    """Structured-output schema: an object with `shots` and `characters`."""
    return types.Schema(
        type=types.Type.OBJECT,
        required=["shots"],
        properties={
            "shots": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["scene_number", "shot_number", "description"],
                    properties={
                        "scene_number": types.Schema(type=types.Type.INTEGER),
                        "shot_number": types.Schema(type=types.Type.INTEGER),
                        "description": types.Schema(type=types.Type.STRING),
                        "characters": types.Schema(
                            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)
                        ),
                        "location": types.Schema(type=types.Type.STRING),
                        "camera": types.Schema(type=types.Type.STRING),
                    },
                ),
            ),
            "characters": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["name"],
                    properties={
                        "name": types.Schema(type=types.Type.STRING),
                        "description": types.Schema(type=types.Type.STRING),
                    },
                ),
            ),
        },
    )


def _coerce_shots(raw) -> list[dict]:
    """Validate/normalise the model's JSON into a clean list of shot dicts."""
    if not isinstance(raw, list):
        raise ScriptBreakdownError("The model did not return a list of shots.")

    shots: list[dict] = []
    for i, item in enumerate(raw[:MAX_SHOTS], start=1):
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description", "")).strip()
        if not desc:
            continue
        chars = item.get("characters") or []
        if not isinstance(chars, list):
            chars = [str(chars)]
        shots.append(
            {
                "scene_number": int(item.get("scene_number", 1) or 1),
                "shot_number": int(item.get("shot_number", i) or i),
                "description": desc,
                "characters": [str(c).strip() for c in chars if str(c).strip()],
                "location": str(item.get("location", "")).strip(),
                "camera": str(item.get("camera", "")).strip(),
            }
        )

    if not shots:
        raise ScriptBreakdownError(
            "No usable shots were produced from this script. Try a longer or "
            "clearer script."
        )
    return shots


def _coerce_characters(raw) -> list[dict]:
    """Normalise the cast list; dedupe by name (case-insensitive)."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "description": str(item.get("description", "")).strip()})
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def break_down_script(
    script_text: str,
    provider: str | None = None,
    max_shots: int = MAX_SHOTS,
    genre: str | None = None,
) -> dict:
    """Break a raw script into a storyboard shot list + a cast list.

    Args:
        script_text: The raw script / story text to parse.
        provider: "vertex" or "gemini". Defaults to TEXT_PROVIDER env (or "vertex").
        max_shots: Upper bound on the number of shots to return.
        genre: Optional genre — shapes the tone / pacing of the breakdown.

    Returns:
        {"shots": [{scene_number, shot_number, description, characters[], location,
        camera}, …], "characters": [{name, description}, …]}.

    Raises:
        ScriptBreakdownError: with a human-readable reason on any failure.
    """
    text = (script_text or "").strip()
    if len(text) < 20:
        raise ScriptBreakdownError(
            "The script is too short to storyboard. Paste at least a few sentences."
        )

    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)
    capped = max(1, min(int(max_shots or MAX_SHOTS), MAX_SHOTS))
    prompt = _PROMPT_TEMPLATE.format(max_shots=capped, script=text)
    if genre and genre.strip():
        prompt = (
            f"Genre: {genre.strip()}. Shape the tone, pacing and shot choices to "
            f"fit this genre.\n\n" + prompt
        )

    last_reason = "Unknown error breaking down the script."

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[breakdown] Parsing script (provider=%s, model=%s, attempt %d/%d)…",
                provider, model_id, attempt, MAX_RETRIES,
            )
            response = client.models.generate_content(
                model=model_id,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=_breakdown_schema(),
                    temperature=0.4,
                ),
            )

            payload = getattr(response, "text", None)
            if not payload:
                last_reason = (
                    "The model returned an empty response (it may have been "
                    "blocked by a safety filter). Try rephrasing the script."
                )
                logger.warning("[breakdown] %s", last_reason)
                raise ScriptBreakdownError(last_reason)

            try:
                raw = json.loads(payload)
            except json.JSONDecodeError as e:
                last_reason = f"The model returned invalid JSON ({e})."
                logger.warning("[breakdown] %s Retrying…", last_reason)
                # A retry may return valid JSON — keep trying.
                raise _Retry(last_reason)

            # Tolerate either an object {shots, characters} or a bare shots list.
            shots_raw = raw.get("shots") if isinstance(raw, dict) else raw
            chars_raw = raw.get("characters") if isinstance(raw, dict) else []
            shots = _coerce_shots(shots_raw)
            characters = _coerce_characters(chars_raw)
            logger.info(
                "[breakdown] Produced %d shots, %d characters.", len(shots), len(characters)
            )
            return {"shots": shots, "characters": characters}

        except ScriptBreakdownError:
            raise
        except _Retry:
            if attempt < MAX_RETRIES:
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue
            raise ScriptBreakdownError(last_reason)
        except Exception as e:  # noqa: BLE001 — surface a clear reason
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                last_reason = "Rate limited / quota exhausted on the text API (HTTP 429)."
                if attempt < MAX_RETRIES:
                    backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning("[breakdown] %s Waiting %ds…", last_reason, backoff)
                    time.sleep(backoff)
                    continue
            else:
                last_reason = f"Text API error: {error_str}"
                logger.error("[breakdown] call failed: %s", error_str)
                if attempt < MAX_RETRIES:
                    time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                    continue

    raise ScriptBreakdownError(last_reason)
