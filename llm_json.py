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

---------------------------------------------------------------------------
⚠ NOT EVERY MODEL CAN BE HANDED A SCHEMA. SO THE SCHEMA CAN GO IN THE PROMPT.
---------------------------------------------------------------------------
Vertex and the Gemini API take a `response_schema` and enforce it server-side.
Most other endpoints do not: the best they offer is "reply in JSON", and plenty
offer nothing at all. That is ONE SWITCH here, not a second module:

    DIRECTOR_STRUCTURED_OUTPUT = auto | native | prompt      (default: auto)

`prompt` writes the schema into the message as a shape sketch (`schema_prose`)
and then does the two things a promise in a prompt needs and a server-side
schema does not:

  · EXTRACT BEFORE PARSE. `extract_json` throws away a ```json fence, a "Here is
    the plan:" and anything after the closing brace — by walking the braces with
    string-awareness rather than by regex, because a caption containing `}` is
    data, not the end of the object.
  · ONE REPAIR RETRY. A model that sent something unparseable is handed back its
    OWN output plus the parse error and asked again — ONCE. Once, because the
    second repair of the same answer is a model that cannot do this task, and
    finding that out is the point of the exercise rather than a cost to hide.
    ⚠ The repair is counted SEPARATELY from the three transport retries and
    fires at most once per `complete_json`, so the worst case is 4 calls, not 6.

`auto` is `native` on Vertex, the Gemini API and `stub`; `prompt` everywhere
else. So nothing about the Google path changes unless you change it — and the
prompt path can be pointed AT Google (`DIRECTOR_STRUCTURED_OUTPUT=prompt`) to
prove it works using credentials you already have.

---------------------------------------------------------------------------
⚠ `openai_compatible` IS A WIRE FORMAT, NOT A SECOND SDK. Approved 2026-08-23.
---------------------------------------------------------------------------
AGENTS.md forbids a non-Google model SDK without asking. This is not one. It is
`POST {DIRECTOR_BASE_URL}/chat/completions` over `requests`, which has been in
`requirements.txt` since the first commit — NOTHING WAS ADDED TO IT. That one
wire format reaches OpenAI, Ollama, vLLM, llama.cpp, LM Studio, Groq, Together
and OpenRouter, and anything sitting behind an OpenAI-shaped proxy.

    DIRECTOR_PROVIDER = openai_compatible          (alias: openai)
    DIRECTOR_BASE_URL = https://api.openai.com/v1 | http://localhost:11434/v1 | …
    DIRECTOR_API_KEY  = whatever that endpoint wants (a local one wants nothing)
    DIRECTOR_MODEL    = ⚠ REQUIRED here. The fallback is the TEXT model's id and
                        that is a Google one, which this endpoint has never
                        heard of — so it is an error you get before the call,
                        not a 404 you get after it.

⚠ AND THE ANSWER IS STILL NOT TRUSTED, WHICH IS WHY THIS IS A DAY'S WORK AND NOT
A REWRITE. Everything downstream already treats what comes back as untrusted
data: `fold_steps` drops a verb that does not exist, `validatePlan` drops a step
that cannot land, `applyGuardrails` trims a plan that treats too much. Swapping
the model swaps who writes the plan. It does not move one inch of the fence.
`tests/director_contract_check.py` is how you find out whether a given model
clears it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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

SUPPORTED_PROVIDERS = ("vertex", "gemini", "openai_compatible", "stub")

# Spellings people actually type. `openai` is the obvious one to reach for and it
# is not wrong — the wire format IS OpenAI's — but the canonical name says the
# true thing: what is supported is the SHAPE of the endpoint, not the vendor.
PROVIDER_ALIASES = {
    "openai": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "oai": "openai_compatible",
    "ollama": "openai_compatible",
    "local": "openai_compatible",
}

# ⚠ WHO ENFORCES A JSON SCHEMA SERVER-SIDE. Everything not on this list is given
# the schema in words instead — see the header. `stub` is here because it answers
# from a file: it is already exactly the shape it was going to be, and padding
# its prompt would only make the fixture's fingerprint drift.
NATIVE_SCHEMA_PROVIDERS = ("vertex", "gemini", "stub")

# The default endpoint for `openai_compatible` — the one default in this module
# that names a company. Every other endpoint is a DIRECTOR_BASE_URL you set.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

# One completion's ceiling. A polish call on a 60-shot board is a large prompt on
# a small local model, so this is generous rather than snappy.
DEFAULT_TIMEOUT_SECONDS = 180

# How much of a broken answer is quoted back in the repair call: enough to mend a
# truncated object, not so much that the repair costs more than the original.
MAX_REPAIR_CHARS = 8000


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
    p = PROVIDER_ALIASES.get(p, p)
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
    if resolved == "openai_compatible":
        # ⚠ NO FALLBACK, ON PURPOSE. The fallback below is the TEXT model, whose
        # id is a Google one; sending it to someone else's endpoint buys a 404
        # after the request instead of a sentence before it.
        raise LLMJsonError(
            "DIRECTOR_PROVIDER=openai_compatible needs DIRECTOR_MODEL set — there "
            "is no default model for an endpoint this app does not own."
        )
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
# THE SCHEMA, IN WORDS — for every model that cannot be handed one
# ---------------------------------------------------------------------------
def schema_mode(provider: str | None = None) -> str:
    """`native` or `prompt` — where the schema travels on this provider.

    ⚠ IT IS A MODE, NOT A CAPABILITY PROBE. Nothing here asks an endpoint what it
    supports: a probe is a call, a call is a delay and a bill, and the answer
    would be cached wrongly the first time somebody puts a proxy in front. You
    say which it is, and `auto` reads it off the provider name.
    """
    raw = (_env("DIRECTOR_STRUCTURED_OUTPUT") or "auto").lower()
    if raw in ("native", "on", "true", "1", "yes"):
        return "native"
    if raw in ("prompt", "off", "false", "0", "no", "text"):
        return "prompt"
    if raw not in ("auto", ""):
        logger.warning(
            "[llm_json] DIRECTOR_STRUCTURED_OUTPUT=%r is not auto/native/prompt "
            "— treating it as auto.", raw,
        )
    return "native" if resolve_provider(provider) in NATIVE_SCHEMA_PROVIDERS else "prompt"


def schema_in_prompt(provider: str | None = None) -> bool:
    """Does the schema have to be described in the message itself?"""
    return schema_mode(provider) == "prompt"


def _scalar_sketch(node: dict) -> str:
    """One value, as the placeholder a model reads: `<integer>`, `<"a" | "b">`."""
    if node.get("enum"):
        return "<" + " | ".join('"%s"' % v for v in node["enum"]) + ">"
    kind = (node.get("type") or "string").lower()
    if kind == "boolean":
        return "<true | false>"
    return "<%s>" % kind


def _sketch_note(node: dict, required: bool) -> str:
    """The `//` comment after a line: whether it is required, and what it means."""
    bits = []
    if required:
        bits.append("required")
    if node.get("description"):
        bits.append(str(node["description"]).strip())
    return ("   // " + ". ".join(bits)) if bits else ""


def _sketch_lines(node: dict, indent: int, label: str = "", required: bool = False,
                  tail: str = "") -> list[str]:
    """One node of the schema, as lines of a JSON-shaped sketch.

    ⚠ INSERTION ORDER, NOT SORTED. The schema is written by hand in `director.py`
    in the order a person would read it, and this sketch becomes part of the
    prompt — so it has to be stable run to run (it is) without being alphabetised
    into nonsense (`args` before `verb`).
    """
    pad = "  " * indent
    head = ('%s"%s": ' % (pad, label)) if label else pad
    kind = (node.get("type") or "string").lower()

    if kind == "object":
        props = node.get("properties") or {}
        req = set(node.get("required") or [])
        if not props:
            return ["%s{ }%s%s" % (head, tail, _sketch_note(node, required))]
        out = ["%s{%s" % (head, _sketch_note(node, required))]
        names = list(props)
        for i, name in enumerate(names):
            child = props[name] if isinstance(props[name], dict) else {}
            out += _sketch_lines(
                child, indent + 1, name, name in req,
                "," if i < len(names) - 1 else "",
            )
        out.append("%s}%s" % (pad, tail))
        return out

    if kind == "array":
        item = node.get("items") if isinstance(node.get("items"), dict) else {"type": "string"}
        out = ["%s[%s" % (head, _sketch_note(node, required))]
        out += _sketch_lines(item, indent + 1)
        out.append("%s]%s" % (pad, tail))
        return out

    return ["%s%s%s%s" % (head, _scalar_sketch(node), tail, _sketch_note(node, required))]


def schema_prose(schema: dict) -> str:
    """A JSON Schema as the shape sketch that goes in a prompt. Public: tests read it."""
    return "\n".join(_sketch_lines(schema or {}, 0))


# ⚠ THE THREE RULES UNDER THE SKETCH ARE THE THREE THINGS THAT REALLY DO GET
# DROPPED further down — a missing required key, a value off the enum, an
# invented key. Telling the model what the reader DOES with each is worth more
# than telling it to be careful.
_CONTRACT = """RETURN ONE JSON OBJECT AND NOTHING ELSE. No sentence before it, no
commentary after it, no markdown fence. It has to be exactly this shape — the
angle brackets say what a value IS and are not part of it, and a `//` comment is
a note to you, never a key:

{sketch}

  · Every key marked `required` has to be there.
  · Where a value lists choices, use one of them EXACTLY as spelled. A value
    that is not on the list is thrown away by the reader at this end.
  · Do not invent keys that are not listed. Those are thrown away too."""


def as_prompt_schema(request: JsonRequest) -> JsonRequest:
    """The same call with the schema written into the prompt — a NEW request.

    ⚠ IT RETURNS A REQUEST RATHER THAN A STRING, so the determinism claim covers
    the mode for free: the bytes that go out are the bytes `fingerprint()` reads,
    and moving a model from native to prompt changes that fingerprint because it
    really is a different call.
    """
    if not request.schema:
        return request
    return JsonRequest(
        system=request.system,
        prompt=request.prompt.rstrip() + "\n\n" + _CONTRACT.format(
            sketch=schema_prose(request.schema)
        ) + "\n",
        schema=request.schema,
        purpose=request.purpose,
        sampling=request.sampling,
    )


# ---------------------------------------------------------------------------
# READING BACK WHAT A MODEL WITHOUT A SCHEMA SENDS
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"```[a-zA-Z]*\s*\n?(.*?)```", re.DOTALL)


def extract_json(payload: str) -> str:
    """The JSON object out of whatever the model wrapped it in. Public: tests read it.

    ⚠ IT WALKS THE BRACES, IT DOES NOT REGEX THEM. A caption reading `see you }`
    is data, and "everything up to the last brace" gets the wrong answer on
    exactly the input this feature exists for — a plan full of the user's own
    words. So: take the first fenced block that holds an object, then scan from
    the first `{` counting depth, knowing when you are inside a string and when a
    quote is escaped.

    A truncated object comes back truncated rather than blank, because
    `json.loads` then says WHERE it ran out, and that sentence is what the repair
    call hands back to the model.
    """
    text = (payload or "").strip()
    if not text:
        return ""
    for block in _FENCE.findall(text):
        if "{" in block:
            text = block.strip()
            break
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _read_json(payload: str) -> tuple[dict | None, str]:
    """`(object, "")` or `(None, why)`. ⚠ The `why` is written for a MODEL to read."""
    text = extract_json(payload)
    if not text.strip():
        return None, "there was no JSON object in it at all"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return None, "it would not parse: %s" % e
    if not isinstance(parsed, dict):
        # A bare list where an object was asked for is a shape error, not a parse
        # error, and asking again is the right answer to both.
        return None, "it was a %s, not a JSON object" % type(parsed).__name__
    return parsed, ""


def _repair_request(request: JsonRequest, payload: str, reason: str) -> JsonRequest:
    """Hand the model its own broken answer and the parse error. ONCE — see the header.

    ⚠ IT QUOTES THE ANSWER BACK RATHER THAN RE-ASKING THE QUESTION. Re-asking
    costs the whole board again and buys a second first draft; quoting costs the
    broken text and buys it mended — and on a truncated object the model has its
    own half-finished plan in front of it to finish.

    ⚠ AND IT ALWAYS CARRIES THE SKETCH, even on a native-schema provider, because
    a call that got this far has already demonstrated the schema was not enforced.
    """
    sketch = as_prompt_schema(request).prompt if request.schema else request.prompt
    broken = (payload or "").strip()
    if len(broken) > MAX_REPAIR_CHARS:
        broken = broken[:MAX_REPAIR_CHARS] + "\n…[the rest of your answer is cut off here]"
    prompt = (
        "Your last answer could not be read, so nothing you wrote has been used.\n\n"
        "WHAT WENT WRONG: %s\n\n"
        "THIS IS EXACTLY WHAT YOU SENT:\n"
        "-----\n"
        "%s\n"
        "-----\n\n"
        "Send that same answer again as ONE valid JSON object and nothing else — "
        "the same decisions, the same content, mended. Close every brace and "
        "bracket, quote every key, and do not wrap it in a code fence or explain "
        "it.\n\n"
        "For reference, the shape it has to be and the question it answers:\n\n"
        "%s" % (reason, broken, sketch)
    )
    # ⚠ THE PURPOSE IS UNCHANGED. `stub` answers BY purpose, and a repair under a
    # new name would hand the stub the whole file instead of the analyse block.
    return JsonRequest(
        system=request.system,
        prompt=prompt,
        schema=request.schema,
        purpose=request.purpose,
        sampling=request.sampling,
    )


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

    # ⚠ `DIRECTOR_STRUCTURED_OUTPUT=prompt` REALLY DOES DROP THE SCHEMA HERE, and
    # that is the whole point of letting you set it on Google: it turns the model
    # you already have credentials for into a stand-in for one that cannot be
    # handed a schema. Leave `response_schema` in and the prompt path would be
    # tested with a safety net under it, which is not a test of anything.
    config: dict = {
        "system_instruction": request.system,
        "response_mime_type": "application/json",
        **settings,
    }
    if not schema_in_prompt(provider):
        config["response_schema"] = _to_genai_schema(request.schema)

    response = client.models.generate_content(
        model=model_id(provider),
        contents=[request.prompt],
        config=types.GenerateContentConfig(**config),
    )
    return getattr(response, "text", "") or ""


def _openai_adapter(request: JsonRequest) -> str:
    """Any OpenAI-shaped `/chat/completions` endpoint, over plain `requests`.

    ⚠ NO SDK. See the header — `requests` was already here, and this is one POST
    with two messages on it. What that buys is every endpoint that speaks this
    format: OpenAI itself, Ollama, vLLM, llama.cpp, LM Studio, Groq, Together,
    OpenRouter, and whatever a company puts its own proxy in front of.

    ⚠ `json_object` IS ASKED FOR AND IS NOT A SCHEMA. It makes prose-around-the-
    JSON much rarer; it says nothing about the SHAPE, so the sketch still goes in
    the prompt. Endpoints that reject the field are a real thing — the error
    below quotes the body so you can see it, and `DIRECTOR_JSON_MODE=off` turns
    it off without touching anything else.
    """
    import requests

    base = (_env("DIRECTOR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    key = _env("DIRECTOR_API_KEY")
    settings = {**sampling(), **request.sampling}

    body: dict = {
        "model": model_id(),
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.prompt},
        ],
        "temperature": settings.get("temperature", DEFAULT_TEMPERATURE),
        "top_p": settings.get("top_p", DEFAULT_TOP_P),
    }
    if "seed" in settings:
        body["seed"] = settings["seed"]
    if (_env("DIRECTOR_JSON_MODE") or "on").lower() not in ("off", "false", "0", "no"):
        body["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key

    timeout = _env_float("DIRECTOR_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    response = requests.post(
        base + "/chat/completions", json=body, headers=headers, timeout=timeout
    )
    if response.status_code >= 400:
        # ⚠ THE BODY IS IN THE MESSAGE. Half the endpoints this reaches are
        # somebody's own deployment, and "400" on its own sends the reader to the
        # wrong place — the body says whether it was the model name, the key, or
        # `response_format`.
        raise LLMJsonError(
            "%s said HTTP %d for the %s call: %s"
            % (base, response.status_code, request.purpose, (response.text or "")[:600])
        )
    try:
        payload = response.json()
        return (payload["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMJsonError(
            "%s answered in a shape this app does not recognise (%s). It has to be "
            "OpenAI-compatible `/chat/completions`." % (base, e)
        ) from None


def _adapter():
    if _override is not None:
        return _override
    provider = resolve_provider()
    if provider == "stub":
        return _stub_adapter
    if provider == "openai_compatible":
        return _openai_adapter
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
    # ⚠ THE REQUEST THAT GOES OUT MAY NOT BE THE ONE THAT CAME IN. On a provider
    # that cannot be handed a schema, `sent` carries the schema in its prompt —
    # and everything after this line, the fingerprint in the log included, is
    # about what was ACTUALLY sent.
    sent = as_prompt_schema(request) if schema_in_prompt() else request
    last_reason = f"The {request.purpose} call failed for an unknown reason."
    # ⚠ ONE REPAIR PER CALL, NOT ONE PER ATTEMPT. Three transport retries each
    # asking for a mend would be six paid calls to learn one thing.
    repaired = False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[llm_json] %s (provider=%s, model=%s, schema=%s, attempt %d/%d, fp=%s)…",
                request.purpose, resolve_provider(), model_id(), schema_mode(),
                attempt, MAX_RETRIES, sent.fingerprint()[:8],
            )
            payload = adapter(sent)
            if not (payload or "").strip():
                # ⚠ NOTHING IS NOT REPAIRABLE. An empty answer is a safety block
                # or a dead endpoint, and quoting nothing back at a model is a
                # paid call that cannot succeed.
                last_reason = (
                    f"The model returned nothing for the {request.purpose} call "
                    "(it may have been blocked by a safety filter)."
                )
                raise _Retry(last_reason)

            parsed, why = _read_json(payload)

            if parsed is None and not repaired:
                repaired = True
                logger.warning(
                    "[llm_json] The %s answer could not be read — %s. Asking it to "
                    "mend its own answer, once.", request.purpose, why,
                )
                mended = adapter(_repair_request(sent, payload, why))
                parsed, why = _read_json(mended)
                if parsed is not None:
                    logger.info("[llm_json] The repair worked; the %s call stands.", request.purpose)

            if parsed is None:
                last_reason = (
                    f"The model returned unusable JSON for the {request.purpose} "
                    f"call — {why}."
                )
                logger.warning("[llm_json] %s Retrying…", last_reason)
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
