"""
llm_json.py — ONE METHOD, AND IT IS THE ONLY WAY THE DIRECTOR TALKS TO A MODEL.

    complete_json(request) -> dict

That is the whole surface. A caller hands over a system instruction, a prompt, a
JSON Schema and a purpose; it gets back parsed JSON or raises. Nothing else about
the provider — not a client, not a `types.GenerateContentConfig`, not a model id
— is visible to anything that imports this.

---------------------------------------------------------------------------
⚠ WHY A SEAM AT ALL, WHEN EVERY OTHER MODULE CALLS `google-genai` DIRECTLY
---------------------------------------------------------------------------
`script_breakdown.py`, `plan_agent.py` and `panel_sequence.py` each build their
own client and their own config, and that is fine for them: they are one call
each, and the call IS the feature. The Director is different in two ways that
both argue for a seam.

  1. IT IS TWO CALLS THAT MUST AGREE. The analyse call reads the film and the
     polish call writes the edit from what analyse said. Determinism has to hold
     across BOTH or the second is deciding from a story it half-remembers, so the
     sampling settings have to be one object read once, not two copies of the
     same four lines that drift.

  2. IT IS THE ONLY MODULE A TEST CAN'T AFFORD TO CALL FOR REAL. A shot breakdown
     test can be skipped when there are no credentials. The Director's language
     rule and its determinism rule are the two things Phase 2 exists to get
     right, and they are properties of what is SENT and what is DONE with what
     comes back — neither needs a live model, and neither should wait for one.
     `use_adapter()` swaps the whole provider for a function in one line, which
     is what `tests/director_language_check.py` and
     `tests/director_determinism_check.py` do.

---------------------------------------------------------------------------
⚠ `DIRECTOR_PROVIDER` IS ITS OWN SWITCH — that is the house rule, not a habit.
---------------------------------------------------------------------------
Every capability in this app switches independently (see the provider table in
AGENTS.md): images can run on Vertex while video runs on the Gemini API. The
Director joins that table with its own name, and falls back to `TEXT_PROVIDER`
when it is unset, so an existing `.env` keeps working untouched.

    DIRECTOR_PROVIDER = vertex | gemini | stub     (default: TEXT_PROVIDER)
    DIRECTOR_MODEL    = a model id                 (default: the text model)
    DIRECTOR_TEMPERATURE / DIRECTOR_TOP_P / DIRECTOR_SEED

⚠ `stub` IS A REAL, SUPPORTED VALUE and not a test artefact. It answers every
call from a JSON file named by `DIRECTOR_STUB_PATH`, which is how you drive the
whole workflow — popup, preview, run, revert — on a laptop with no credentials
and no quota, and how a screenshot of the panel gets taken without spending.

---------------------------------------------------------------------------
⚠ GREEDY, SEEDED, AND THE SAME NUMBERS FOR BOTH CALLS.
---------------------------------------------------------------------------
An edit plan is a CONSIDERED ANSWER, not a lottery ticket: the same board must
give the same film twice, or "read it again" stops being a comparison and starts
being a re-roll. Temperature 0, top-p 1, fixed seed — the same settings, and the
same reasoning, as the shot breakdown. The two caveats there apply here word for
word and are worth repeating because they are the honest limit of this claim:

  · No Gemini endpoint promises bit-exact reproducibility. Serving-side batching
    means even temperature 0 can differ occasionally. This makes runs COMPARABLE,
    not identical, and the test asserts what CAN be asserted — that the request
    is identical and the sampling is greedy — never that two live calls matched.
  · `gemini-2.5-flash` is a rolling ALIAS. Pin `DIRECTOR_MODEL` to a dated
    snapshot if you need plans to stay comparable across weeks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Same policy as the breakdown: three tries, doubling from 4s.
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 4

# ⚠ THE DEFAULTS ARE GREEDY. See the header — this is a considered answer.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 1.0
DEFAULT_SEED = 42

SUPPORTED_PROVIDERS = ("vertex", "gemini", "stub")


class LLMJsonError(Exception):
    """A JSON completion could not be produced. Carries a human-readable reason.

    ⚠ IT IS A REASON, NOT A STACK. Everything that raises this is one HTTP hop
    from a user reading it in a dialog, so "the model returned invalid JSON after
    3 tries" is the message and the traceback goes to the log.
    """


class _Retry(Exception):
    """Internal signal: try the call again (malformed JSON, a 429)."""


# ---------------------------------------------------------------------------
# The request — one frozen object, because it is also the determinism claim
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class JsonRequest:
    """Everything one call needs, and nothing about how it is made.

    ⚠ `fingerprint()` IS WHAT "THE SAME BRIEF TWICE" MEANS. Two runs are the same
    run when the bytes sent are the same bytes, and that is a claim a test can
    make without a network. It covers the schema as well as the prose, because a
    schema change silently changes the answer just as much as a prompt change
    does.
    """

    system: str
    prompt: str
    schema: dict
    purpose: str = "completion"
    # Per-call overrides. Left empty the module-level sampling applies, which is
    # what both Director calls use — see the header on why they must agree.
    sampling: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        """A stable digest of exactly what will be sent."""
        blob = json.dumps(
            {
                "system": self.system,
                "prompt": self.prompt,
                "schema": self.schema,
                "sampling": {**sampling(), **self.sampling},
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------
def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def resolve_provider(provider: str | None = None) -> str:
    """Effective provider: explicit arg > DIRECTOR_PROVIDER > TEXT_PROVIDER > vertex."""
    p = (provider or _env("DIRECTOR_PROVIDER") or _env("TEXT_PROVIDER") or "vertex").lower()
    if p not in SUPPORTED_PROVIDERS:
        raise LLMJsonError(
            f"Unknown DIRECTOR_PROVIDER '{p}'. Use one of {SUPPORTED_PROVIDERS}."
        )
    return p


def model_id(provider: str | None = None) -> str:
    """The model this provider will be asked for.

    ⚠ THE TEXT MODEL IS THE FALLBACK, NOT A SECOND DEFAULT. `script_breakdown`
    owns what "the text model" is for this build; naming a different string here
    would give the Director a model nobody chose the day that one is pinned.
    """
    resolved = resolve_provider(provider)
    override = _env("DIRECTOR_MODEL")
    if override:
        return override
    if resolved == "stub":
        return "stub"
    from script_breakdown import text_model_id

    return text_model_id(resolved)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[llm_json] %s=%r is not a number — using %s.", name, raw, default)
        return default


def sampling() -> dict:
    """The decoding settings, as a plain dict — greedy and seeded by default.

    Returned as data rather than applied inside the adapter so a test can assert
    on it and so both Director calls demonstrably share one answer.
    """
    out: dict = {
        "temperature": _env_float("DIRECTOR_TEMPERATURE", DEFAULT_TEMPERATURE),
        "top_p": _env_float("DIRECTOR_TOP_P", DEFAULT_TOP_P),
    }
    raw_seed = _env("DIRECTOR_SEED") or str(DEFAULT_SEED)
    if raw_seed.lower() not in ("", "none", "off", "random"):
        try:
            out["seed"] = int(raw_seed)
        except ValueError:
            logger.warning("[llm_json] DIRECTOR_SEED=%r is not an integer — ignoring.", raw_seed)
    return out


def is_greedy(settings: dict | None = None) -> bool:
    """Is this decoding actually greedy? What the determinism test asks."""
    s = settings if settings is not None else sampling()
    return float(s.get("temperature", 1)) == 0.0 and "seed" in s


# ---------------------------------------------------------------------------
# JSON Schema → the SDK's own schema type
# ---------------------------------------------------------------------------
# ⚠ THE CALLER WRITES PLAIN JSON SCHEMA AND NEVER IMPORTS `google.genai.types`.
# That is the seam doing its job: `director.py` describes the shape it wants in
# a dict, and swapping the provider cannot ask it to rewrite those descriptions.
# Recent google-genai builds accept a dict directly, older ones do not, and the
# difference is a 500 in production rather than a warning — so it is converted
# here, explicitly, and the conversion is small enough to read.
_JSON_TO_GENAI = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
}


def _to_genai_schema(node: dict):
    from google.genai import types

    kind = _JSON_TO_GENAI.get((node.get("type") or "string").lower(), "STRING")
    kwargs: dict = {"type": kind}
    if node.get("description"):
        kwargs["description"] = node["description"]
    if node.get("enum"):
        kwargs["enum"] = [str(v) for v in node["enum"]]
    if kind == "OBJECT":
        props = node.get("properties") or {}
        kwargs["properties"] = {k: _to_genai_schema(v) for k, v in props.items()}
        required = [k for k in (node.get("required") or []) if k in props]
        if required:
            kwargs["required"] = required
    if kind == "ARRAY":
        kwargs["items"] = _to_genai_schema(node.get("items") or {"type": "string"})
    return types.Schema(**kwargs)


# ---------------------------------------------------------------------------
# The adapters
# ---------------------------------------------------------------------------
# An adapter is ONE FUNCTION: `(JsonRequest) -> str`, the raw text the model
# produced. Parsing, retrying and reporting are this module's job and are
# identical whoever answered, which is the point of writing them once.
_override = None


def use_adapter(fn):
    """Replace the provider with `fn` for the rest of the process. Returns the old one.

    ⚠ FOR TESTS AND FOR `stub`, AND IT IS PROCESS-WIDE ON PURPOSE. A per-call
    injection parameter would let production code pass one, and the moment that
    is possible the seam stops being a seam.
    """
    global _override
    previous = _override
    _override = fn
    return previous


def _stub_adapter(request: JsonRequest) -> str:
    """Answer from a file. `DIRECTOR_STUB_PATH` names it; keys are purposes.

    The file is either one JSON object per purpose —
    `{"analyse": {...}, "polish": {...}}` — or a single object used for every
    call. Missing means an empty object, which every caller here already treats
    as "the model said nothing usable".
    """
    path = _env("DIRECTOR_STUB_PATH")
    if not path or not os.path.exists(path):
        logger.warning("[llm_json] DIRECTOR_PROVIDER=stub but DIRECTOR_STUB_PATH is not a file.")
        return "{}"
    with open(path, "r", encoding="utf-8") as fh:
        blob = json.load(fh)
    if isinstance(blob, dict) and request.purpose in blob:
        blob = blob[request.purpose]
    return json.dumps(blob)


def _google_adapter(request: JsonRequest) -> str:
    """Vertex AI or the Gemini Developer API, through `google-genai`."""
    from google.genai import types

    from script_breakdown import get_client

    provider = resolve_provider()
    client = get_client(provider)
    settings = {**sampling(), **request.sampling}

    # Older SDKs don't carry every generation field. Drop what this one doesn't
    # know rather than fail the call on a kwarg — same trade `_sampling_kwargs`
    # makes in script_breakdown.py, and the same warning so a build that has
    # quietly stopped being reproducible says so.
    supported = types.GenerateContentConfig.model_fields
    unknown = [k for k in settings if k not in supported]
    if unknown:
        logger.warning(
            "[llm_json] google-genai does not support %s — upgrade the SDK for "
            "reproducible plans.", ", ".join(unknown),
        )
    settings = {k: v for k, v in settings.items() if k in supported}

    response = client.models.generate_content(
        model=model_id(provider),
        contents=[request.prompt],
        config=types.GenerateContentConfig(
            system_instruction=request.system,
            response_mime_type="application/json",
            response_schema=_to_genai_schema(request.schema),
            **settings,
        ),
    )
    return getattr(response, "text", "") or ""


def _adapter():
    if _override is not None:
        return _override
    if resolve_provider() == "stub":
        return _stub_adapter
    return _google_adapter


# ---------------------------------------------------------------------------
# THE ONE METHOD
# ---------------------------------------------------------------------------
def complete_json(request: JsonRequest) -> dict:
    """Ask for JSON matching `request.schema`. Returns the parsed object.

    ⚠ IT RETURNS A DICT OR RAISES — it never returns a half-parsed thing, and it
    never returns text. Everything downstream of this treats what it gets as
    untrusted DATA (see `validatePlan` on the client and `director.enforce_language`
    here), but it does not also have to wonder whether it is JSON.

    Raises:
        LLMJsonError: with a reason written for a human.
    """
    adapter = _adapter()
    last_reason = f"The {request.purpose} call failed for an unknown reason."

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[llm_json] %s (provider=%s, model=%s, attempt %d/%d, fp=%s)…",
                request.purpose, resolve_provider(), model_id(),
                attempt, MAX_RETRIES, request.fingerprint()[:8],
            )
            payload = adapter(request)
            if not (payload or "").strip():
                last_reason = (
                    f"The model returned nothing for the {request.purpose} call "
                    "(it may have been blocked by a safety filter)."
                )
                raise _Retry(last_reason)
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError as e:
                last_reason = f"The model returned invalid JSON for {request.purpose} ({e})."
                logger.warning("[llm_json] %s Retrying…", last_reason)
                raise _Retry(last_reason)
            if not isinstance(parsed, dict):
                # A bare list where an object was asked for is a shape error, not
                # a parse error, and retrying is the right answer to both.
                last_reason = (
                    f"The {request.purpose} call returned a "
                    f"{type(parsed).__name__}, not an object."
                )
                raise _Retry(last_reason)
            return parsed

        except _Retry:
            if attempt < MAX_RETRIES:
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue
            raise LLMJsonError(last_reason)
        except LLMJsonError:
            raise
        except Exception as e:  # noqa: BLE001 — surface a clear reason
            error = str(e)
            if "429" in error or "RESOURCE_EXHAUSTED" in error:
                last_reason = "Rate limited / quota exhausted on the text API (HTTP 429)."
            else:
                last_reason = f"Text API error during {request.purpose}: {error}"
                logger.error("[llm_json] %s", last_reason)
            if attempt < MAX_RETRIES:
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue

    raise LLMJsonError(last_reason)
