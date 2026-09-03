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

---------------------------------------------------------------------------
⚠ AND SO IS EVERY OTHER CAPABILITY THAT ASKS FOR A NAME — see `CAPABILITIES`.
---------------------------------------------------------------------------
A `JsonRequest` may carry a `capability`, and a capability listed there gets its
own three settings under its own prefix, each falling back to the shared text
ones. The editor's chat is the first:

    CHAT_PROVIDER   = vertex | gemini | stub  (default: its key, then TEXT_PROVIDER)
    CHAT_MODEL      = a model id              (default: the text model)
    GEMINI_KEY_CHAT = a Developer API key     (default: GEMINI_API_KEY)

⚠ WHY THE CHAT NEEDED ITS OWN: a chat turn is a per-MESSAGE cost carried by
somebody's subscription and a breakdown is a per-RENDER cost, and billed to one
key the two cannot be told apart. It is also insulation. A GCP project with its
billing switched off answers 403 CONSUMER_INVALID to every Vertex call, and on
one shared switch that takes the whole app quiet at once — rather than the one
workflow whose project actually lapsed.

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

import contextvars
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

# ---------------------------------------------------------------------------
# ⚠ HOW LONG A PLAN TAKES IS DECIDED BY THESE TWO NUMBERS, AND THEY WERE BOTH
# UNSET UNTIL 2026-08-24. Everything below is MEASURED against
# `gemini-2.5-flash` on Vertex, greedy decoding, real analyse+polish requests
# built by `director.py`, on flat boards with no descriptions — the hard case,
# and the one users actually have (`boardFrom` sends no panel wording).
#
#   whole `direct()` call      8 shots    24 shots   48 shots
#   thinking AUTOMATIC, no cap  24s / 150s FAILED     133s        (not measured)
#   thinking 512  + cap         15s        15s / 0 STEPS  21s
#   thinking 1024 + cap         27s        27s        42s
#   thinking 2048 + cap         22s        34s        42s
#   thinking 3584 + cap         —          —          61s
#
# THINKING. 2.5-class models think by default with an AUTOMATIC budget, and that
# budget is where the wall clock went: two calls on a 24-shot board spent 133
# SECONDS, and on the 8-shot board that was reported the polish call ran past
# 135s and came back `504 DEADLINE_EXCEEDED`. A fixed budget bounds it.
#
# ⚠ 1024 RATHER THAN LESS, AND THE REASON IS THE 24-SHOT ROW. At 512 the polish
# call ran out of room to think and returned a plan with NO STEPS IN IT — fast,
# and worthless. 1024 was never empty at 8, 24 or 48 shots. Above it the extra
# budget buys time, not steps.
# ⚠ AND NEVER 0. With thinking off entirely the polish call loses the thread and
# generates until something stops it: 8,192 tokens of unparseable JSON against
# the cap, and 65,536 tokens / 238 SECONDS without one.
#
# THE OUTPUT CAP is what makes that survivable rather than fatal. Left unset the
# ceiling is the model's own — 65,536 tokens here — so a run-away costs four
# minutes and a timeout instead of a truncated answer the repair path can have
# another go at (which is exactly what it did on the 24-shot automatic run: cap
# hit, warning logged, repair asked, plan returned). 12,288 is roomy: polish
# spends ~70 tokens per step, so it covers a 48-shot board planned to the guard
# rails and still bounds the worst case to under a minute.
#
# ⚠ BOTH TRAVEL IN `sampling()` SO THEY ARE IN THE FINGERPRINT. They change the
# answer as surely as the prompt does; a determinism claim that did not cover
# them would be a claim about the wrong bytes.
DEFAULT_THINKING_TOKENS = 1024
DEFAULT_MAX_OUTPUT_TOKENS = 12288

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

# ⚠ ONE CAPABILITY, ONE ENV PREFIX, AND THE PREFIX IS THE WHOLE MAPPING. A name
# in here buys `<PREFIX>_PROVIDER`, `<PREFIX>_MODEL` and `GEMINI_KEY_<PREFIX>`
# with no further code; a name NOT in here resolves exactly as it did before any
# of this existed, which is what keeps the Director and the breakdown untouched.
CAPABILITIES = {
    "chat": "CHAT",
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

# THE WALL CLOCK ONE `complete_json` IS ALLOWED, RETRIES AND BACKOFF INCLUDED.
#
# ⚠ THIS EXISTS BECAUSE THE CEILING ABOVE IS NOT A BUDGET. Three attempts of 180s
# with 4s + 8s of backoff is nine and a half minutes for ONE call, and the plan
# route makes two of them — while the browser gives up at 120s (`api.js`) and the
# user is looking at a spinner. What they got was "The server didn't respond
# within 120s. It may be stuck (a database it needs can do this)", which is a
# true sentence about the wrong component: nothing was stuck, the model was
# simply still being asked. So a call now stops when its budget is spent and
# says what actually happened, and the browser waits long enough for two of them
# (see `DIRECTOR_PLAN_TIMEOUT_MS`).
#
# ⚠ IT IS PER CALL, NOT PER REQUEST, and 2 × this has to stay comfortably inside
# the browser's patience. Raise `DIRECTOR_BUDGET_SECONDS` and you must raise that
# too, or the tab will abort a request the server is still (correctly) serving.
DEFAULT_BUDGET_SECONDS = 135

# ⚠ AND THE CHAT'S OWN, BECAUSE 135 IS LONGER THAN THE BROWSER WILL WAIT FOR ONE.
# The rule above — "2 × the budget has to stay inside the browser's patience" —
# was written for the Director, whose tab waits 300s (`PLAN_TIMEOUT_MS`). The ✨
# AI Editor's tab waits NINETY (`CHAT_TURN_TIMEOUT_MS`, mirroring
# `API_CHAT_TURN_TIMEOUT_S`), because a chat message that has not answered in a
# minute and a half reads as broken. Sharing the Director's 135 meant every chat
# turn slower than 90s died the same way: the browser aborted a call the server
# was still correctly serving, the turn was billed and counted, and what the user
# saw was "The server didn't respond within 90s. It may be stuck (a database it
# needs can do this)" — a true sentence about the wrong component, and reported
# with a screenshot of exactly that over three identical unanswered messages.
#
# ⚠ IT MUST STAY COMFORTABLY UNDER THE BROWSER'S. The twenty seconds of headroom
# is not slack — it is the prompt build, a look's pictures coming up the wire,
# and the response going back down. Raise this and you must raise BOTH
# `CHAT_TURN_TIMEOUT_MS` and `API_CHAT_TURN_TIMEOUT_S`, in that order, or the tab
# goes back to aborting turns the server is about to answer.
#
# ⚠ ONE CALL, NOT TWO. A chat turn is a single `complete_json`; the Director's
# route makes two, which is the other half of why its budget is the bigger one.
CAPABILITY_BUDGET_SECONDS = {"chat": 70.0}

# No attempt is worth starting with less than this left on the clock: the answer
# could not arrive in time, and asking for it costs money on a paid endpoint.
MIN_ATTEMPT_SECONDS = 15

# How much of a broken answer is quoted back in the repair call: enough to mend a
# truncated object, not so much that the repair costs more than the original.
MAX_REPAIR_CHARS = 8000


# ---------------------------------------------------------------------------
# THE CLOCK
# ---------------------------------------------------------------------------
# ⚠ A CONTEXTVAR, NOT AN ARGUMENT, because the thing that needs it is the
# ADAPTER and the thing that owns it is `complete_json` two frames up — and in
# between sits the adapter signature, which is the seam the whole provider story
# is built on (`use_adapter`, the fake transport in the tests). Threading a
# deadline through it would make every adapter, real or fake, take a parameter
# that only one of them can honour.
#
# ⚠ AND A CONTEXTVAR IS SAFE HERE: FastAPI runs a sync route in a worker thread
# with a COPY of the context, so two plans being written at once cannot read each
# other's clock.
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "llm_json_deadline", default=None
)


def call_timeout() -> float:
    """How long THIS attempt may take: the ceiling, or what is left, whichever is less.

    ⚠ THE ADAPTERS BOTH ASK, AND UNTIL NOW ONLY ONE OF THEM DID ANYTHING. The
    OpenAI path passed `DIRECTOR_TIMEOUT_SECONDS` to `requests`; the Google path
    — which is the DEFAULT provider — passed nothing at all, so a wedged Vertex
    connection hung the worker thread for ever and the env var silently meant
    nothing on the path almost everyone is on.
    """
    ceiling = _env_float("DIRECTOR_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    end = _deadline.get()
    if end is None:
        return ceiling
    return max(MIN_ATTEMPT_SECONDS, min(ceiling, end - time.monotonic()))


def _time_left() -> float:
    """Seconds until this call's budget is gone. `inf` when nothing set one."""
    end = _deadline.get()
    return float("inf") if end is None else end - time.monotonic()


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
    # Which capability is asking — a key of `CAPABILITIES`, or "" for the shared
    # text settings. ⚠ NOT `purpose`, THOUGH IT IS TEMPTING: `purpose` is prose
    # written for a log line and an error message ("editor chat"), and keying env
    # vars off a sentence means rewording one breaks the credentials. And ⚠ NOT
    # IN `fingerprint()`: the same brief answered on two keys is the same brief.
    capability: str = ""
    # Pictures to look at, `({mime, data: bytes},)`, oldest-shot first. Empty on
    # every call in this app except a LOOK — see `editor_chat_agent.chat`.
    #
    # ⚠ **A TUPLE OF BYTES, NOT PATHS AND NOT URLS.** This module makes one
    # outbound call and it is to a model; giving it a path would make it a file
    # reader, and giving it a url would make it a fetcher — either one is a
    # second way for this seam to fail and neither is testable without a disk or
    # a network. The caller that has the pictures hands them over.
    #
    # ⚠ **AND THEY ARE IN THE FINGERPRINT, BY DIGEST.** A picture changes the
    # answer as surely as a sentence does, so "the same brief twice" has to mean
    # the same pictures too — but the bytes themselves must not go into the
    # digest input, because that would hash a megabyte to compare two calls.
    images: tuple = ()

    def fingerprint(self) -> str:
        """A stable digest of exactly what will be sent."""
        blob = json.dumps(
            {
                "system": self.system,
                "prompt": self.prompt,
                "schema": self.schema,
                "sampling": {**sampling(), **self.sampling},
                # One short digest per picture, in order — see `images`.
                "images": [
                    hashlib.sha256(bytes(row.get("data") or b"")).hexdigest()[:16]
                    for row in self.images
                    if isinstance(row, dict)
                ],
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


def capability_prefix(capability: str = "") -> str:
    """The env prefix this capability owns, or "" when it owns none."""
    return CAPABILITIES.get((capability or "").strip().lower(), "")


def capability_key_env(capability: str = "") -> str:
    """The env var holding this capability's own Gemini key, or "" for none."""
    prefix = capability_prefix(capability)
    return f"GEMINI_KEY_{prefix}" if prefix else ""


def _capability_provider(capability: str) -> str:
    """What this capability's OWN settings say the provider is, or "".

    ⚠ THE KEY IS ALSO A SWITCH, AND IT HAS TO BE. The alternative is a `.env`
    where pasting a Developer API key next to a capability's name changes nothing
    until you also remember a second line — which is exactly the shape of the
    bug that reads as "I set the key and it STILL says 403". A capability holding
    its own Gemini key is a capability somebody deliberately pointed at the
    Developer API, so that is where it goes.

    ⚠ `<PREFIX>_PROVIDER` STILL WINS OVER THE KEY, so `CHAT_PROVIDER=vertex`
    moves the chat back without anybody having to delete a key they want again.
    """
    prefix = capability_prefix(capability)
    if not prefix:
        return ""
    named = _env(f"{prefix}_PROVIDER")
    if named:
        return named
    return "gemini" if _env(capability_key_env(capability)) else ""


def resolve_provider(provider: str | None = None, *, capability: str = "") -> str:
    """Effective provider, best source first:

    explicit arg > `<CAP>_PROVIDER` > the capability's own key > DIRECTOR_PROVIDER
    > TEXT_PROVIDER > vertex.
    """
    picked = provider or _capability_provider(capability)
    p = (picked or _env("DIRECTOR_PROVIDER") or _env("TEXT_PROVIDER") or "vertex").lower()
    p = PROVIDER_ALIASES.get(p, p)
    if p not in SUPPORTED_PROVIDERS:
        prefix = capability_prefix(capability)
        named = f"{prefix}_PROVIDER" if prefix else "DIRECTOR_PROVIDER"
        raise LLMJsonError(
            f"Unknown {named} '{p}'. Use one of {SUPPORTED_PROVIDERS}."
        )
    return p


def model_id(provider: str | None = None, *, capability: str = "") -> str:
    """The model this provider will be asked for.

    ⚠ THE TEXT MODEL IS THE FALLBACK, NOT A SECOND DEFAULT. `script_breakdown`
    owns what "the text model" is for this build; naming a different string here
    would give the Director a model nobody chose the day that one is pinned.
    """
    resolved = resolve_provider(provider, capability=capability)
    prefix = capability_prefix(capability)
    # ⚠ THE CAPABILITY'S OWN MODEL OUTRANKS `DIRECTOR_MODEL`, and the Director's
    # still applies when it has none — the same fallback shape as the provider.
    override = (_env(f"{prefix}_MODEL") if prefix else "") or _env("DIRECTOR_MODEL")
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


def budget_seconds(capability: str = "") -> tuple[float, str]:
    """`(seconds, the env var that set them)` for one `complete_json` call.

    Best source first: `<CAP>_BUDGET_SECONDS` > `DIRECTOR_BUDGET_SECONDS` >
    the capability's own default > `DEFAULT_BUDGET_SECONDS`.

    ⚠ THE CAPABILITY'S DEFAULT IS A CEILING, NOT A FALLBACK. `DIRECTOR_BUDGET_
    SECONDS` is the knob an operator reaches for when the Director is timing out,
    and on a shared deployment it is usually raised — but raising it must never
    push the chat past the browser that is waiting for it. So a capability with a
    ceiling gets the SMALLER of the two, and `<CAP>_BUDGET_SECONDS` is the one
    way to say "no, really, let this one run longer" (raise the tab's patience
    to match; see `CAPABILITY_BUDGET_SECONDS`).

    ⚠ THE NAME COMES BACK WITH THE NUMBER because the sentence the user reads
    when the clock runs out names the var they have to change, and naming the
    Director's on a chat turn sends them to the wrong line of the `.env`.
    """
    prefix = capability_prefix(capability)
    own = f"{prefix}_BUDGET_SECONDS" if prefix else ""
    if own and _env(own):
        return _env_float(own, DEFAULT_BUDGET_SECONDS), own
    ceiling = CAPABILITY_BUDGET_SECONDS.get((capability or "").strip().lower())
    shared = _env_float("DIRECTOR_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS)
    if ceiling is None:
        return shared, "DIRECTOR_BUDGET_SECONDS"
    return (shared, "DIRECTOR_BUDGET_SECONDS") if shared < ceiling else (ceiling, own)


def sampling() -> dict:
    """The decoding settings, as a plain dict — greedy and seeded by default.

    Returned as data rather than applied inside the adapter so a test can assert
    on it and so both Director calls demonstrably share one answer.
    """
    out: dict = {
        "temperature": _env_float("DIRECTOR_TEMPERATURE", DEFAULT_TEMPERATURE),
        "top_p": _env_float("DIRECTOR_TOP_P", DEFAULT_TOP_P),
        # ⚠ A REAL `GenerateContentConfig` FIELD, so it reaches Google without
        # any help from the adapter. See the note over the defaults for what
        # leaving it unset actually cost.
        "max_output_tokens": int(_env_float("DIRECTOR_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)),
        # ⚠ AND THIS ONE IS NOT — `thinking_config` is, and it is an object. The
        # adapter converts it; it is carried here so it lands in `fingerprint()`
        # with everything else that decides the answer. -1 is the provider's own
        # automatic budget; 0 is off and is a bad idea (see above).
        "thinking_tokens": int(_env_float("DIRECTOR_THINKING_TOKENS", DEFAULT_THINKING_TOKENS)),
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
def schema_mode(provider: str | None = None, *, capability: str = "") -> str:
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
    resolved = resolve_provider(provider, capability=capability)
    return "native" if resolved in NATIVE_SCHEMA_PROVIDERS else "prompt"


def schema_in_prompt(provider: str | None = None, *, capability: str = "") -> bool:
    """Does the schema have to be described in the message itself?"""
    return schema_mode(provider, capability=capability) == "prompt"


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

    # ⚠ THE CAPABILITY IS READ OFF THE REQUEST, NOT OFF THE ENVIRONMENT. This is
    # the one place that knows WHICH call is being made, and resolving it here
    # rather than at import time is what lets the chat sit on the Developer API
    # in the same process that plans a board on Vertex.
    provider = resolve_provider(capability=request.capability)
    client = get_client(provider, key_env=capability_key_env(request.capability))
    settings = {**sampling(), **request.sampling}

    # ⚠ THE ONE SETTING THAT IS NOT A CONFIG FIELD. It travels in `sampling()`
    # because it changes the answer and therefore belongs in the fingerprint;
    # `GenerateContentConfig` wants a `ThinkingConfig` object instead, so it is
    # taken out here rather than being reported as a field the SDK "does not
    # support" by the check below.
    thinking = settings.pop("thinking_tokens", None)

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
    if not schema_in_prompt(provider, capability=request.capability):
        config["response_schema"] = _to_genai_schema(request.schema)

    # ⚠ THE CALL THAT COULD HANG FOR EVER NOW CANNOT. `google-genai` has no
    # timeout of its own, so a Vertex connection that accepted the request and
    # then went quiet held this worker thread until the process was restarted —
    # and `DIRECTOR_TIMEOUT_SECONDS`, which the OpenAI path has always honoured,
    # meant nothing whatsoever on the DEFAULT provider. What the user saw was the
    # browser giving up at 120s with a message about a database.
    # ⚠ MILLISECONDS. `HttpOptions.timeout` is documented in ms and the rest of
    # this module is in seconds; getting that wrong by 1000× is a timeout of
    # 0.18s, which fails every call.
    # ⚠ A SMALL, FIXED THINKING BUDGET. Automatic thinking doubled the wall clock
    # of both Director calls for no better plan; NO thinking makes the polish
    # call run away. See the table over `DEFAULT_THINKING_TOKENS`. A negative
    # value means "leave it to the provider", which is what unset used to do.
    if thinking is not None and thinking >= 0 and "thinking_config" in supported:
        config["thinking_config"] = types.ThinkingConfig(thinking_budget=int(thinking))

    if "http_options" in supported:
        config["http_options"] = types.HttpOptions(timeout=int(call_timeout() * 1000))
    else:
        # Same trade as the sampling fields above: an SDK too old to be told is
        # not a reason to refuse the call, but it IS worth saying out loud,
        # because on that build this whole guard is decorative.
        logger.warning(
            "[llm_json] google-genai is too old to take an http timeout — a wedged "
            "connection will hang this request. Upgrade the SDK."
        )

    # ⚠ THE PICTURES GO BEFORE THE PROMPT, and the order is not cosmetic. The
    # prompt ends by asking for one turn of JSON; parts that arrive AFTER that
    # instruction read as an afterthought, and a model handed "answer now" and
    # then eight stills answers about the last one. Shown the film first, the
    # question is about the film.
    #
    # ⚠ AND A PICTURE THIS SDK CANNOT TAKE IS DROPPED, NOT FATAL. `from_bytes`
    # is the one part of this call that depends on the SDK version, and a look
    # that arrives as a text-only answer is a worse answer — not a broken one.
    parts = []
    for row in request.images or ():
        try:
            parts.append(types.Part.from_bytes(
                data=bytes(row["data"]), mime_type=str(row.get("mime") or "image/png"),
            ))
        except Exception as e:  # noqa: BLE001 — a look without pictures still answers
            logger.warning(
                "[llm_json] a picture could not be attached to the %s call (%s) — "
                "asking without it.", request.purpose, e,
            )
    parts.append(request.prompt)
    if len(parts) > 1:
        logger.info(
            "[llm_json] %s is LOOKING at %d picture(s).", request.purpose, len(parts) - 1
        )

    response = client.models.generate_content(
        model=model_id(provider, capability=request.capability),
        contents=parts,
        config=types.GenerateContentConfig(**config),
    )
    # ⚠ AN ANSWER CUT OFF BY THE CAP IS WORTH SAYING OUT LOUD. The text comes
    # back truncated, `_read_json` reports "it would not parse", and without this
    # line the log blames the model's JSON for what is really a budget: the plan
    # was longer than `DIRECTOR_MAX_OUTPUT_TOKENS` allows, or the model was
    # running away and the cap did its job.
    for candidate in getattr(response, "candidates", None) or []:
        if str(getattr(candidate, "finish_reason", "")).endswith("MAX_TOKENS"):
            logger.warning(
                "[llm_json] The %s answer hit the output cap (%s tokens) and is "
                "truncated. Raise DIRECTOR_MAX_OUTPUT_TOKENS for a very large "
                "board; if it happens on a small one the model is running away.",
                request.purpose, settings.get("max_output_tokens"),
            )
        break
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
    # ⚠ SAID OUT LOUD RATHER THAN SENT BADLY. Every endpoint this reaches spells
    # an image part differently (or cannot take one), and a picture posted in the
    # wrong shape is a 400 that reads like a broken model name. A look that comes
    # back as a text answer is honest; the log says why it was thinner.
    if request.images:
        logger.warning(
            "[llm_json] %s carried %d picture(s) and this provider is reached over "
            "plain /chat/completions — they were NOT sent. Point the capability at "
            "vertex or gemini for a call that can see.",
            request.purpose, len(request.images),
        )
    settings = {**sampling(), **request.sampling}

    body: dict = {
        "model": model_id(capability=request.capability),
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.prompt},
        ],
        "temperature": settings.get("temperature", DEFAULT_TEMPERATURE),
        "top_p": settings.get("top_p", DEFAULT_TOP_P),
        # The same guard against a run-away, in this wire format's spelling.
        # ⚠ `thinking_tokens` HAS NO EQUIVALENT HERE and is deliberately dropped:
        # every endpoint this reaches spells reasoning differently (or not at
        # all), and inventing a field for them would break the ones that reject
        # unknown keys.
        "max_tokens": int(settings.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)),
    }
    if "seed" in settings:
        body["seed"] = settings["seed"]
    if (_env("DIRECTOR_JSON_MODE") or "on").lower() not in ("off", "false", "0", "no"):
        body["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key

    # The ceiling, or whatever is left of this call's budget — see `call_timeout`.
    response = requests.post(
        base + "/chat/completions", json=body, headers=headers, timeout=call_timeout()
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


def _adapter(capability: str = ""):
    """Which adapter answers this capability's calls.

    ⚠ IT TAKES THE CAPABILITY BECAUSE THE ADAPTER IS A CHOICE OF WIRE FORMAT,
    not of vendor. `vertex` and `gemini` share `_google_adapter`, so reading the
    global switch here was harmless right up until a capability could name
    `openai_compatible` on its own — at which point it would have been sent down
    the Google path with somebody else's model id on it.
    """
    if _override is not None:
        return _override
    provider = resolve_provider(capability=capability)
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
    adapter = _adapter(request.capability)
    # ⚠ THE REQUEST THAT GOES OUT MAY NOT BE THE ONE THAT CAME IN. On a provider
    # that cannot be handed a schema, `sent` carries the schema in its prompt —
    # and everything after this line, the fingerprint in the log included, is
    # about what was ACTUALLY sent.
    sent = as_prompt_schema(request) if schema_in_prompt(capability=request.capability) else request
    last_reason = f"The {request.purpose} call failed for an unknown reason."
    # ⚠ ONE REPAIR PER CALL, NOT ONE PER ATTEMPT. Three transport retries each
    # asking for a mend would be six paid calls to learn one thing.
    repaired = False
    # ⚠ THE CLOCK STARTS HERE AND IT IS THE WHOLE CALL'S, retries and backoff
    # included. Without it three attempts could run for nine minutes against a
    # browser that stops listening after two — see `DEFAULT_BUDGET_SECONDS`.
    # ⚠ AND IT IS THE CAPABILITY'S CLOCK, NOT ALWAYS THE DIRECTOR'S. A chat turn
    # gets less than a plan does, because the tab waiting for it waits less. See
    # `CAPABILITY_BUDGET_SECONDS`.
    budget, budget_env = budget_seconds(request.capability)
    clock = _deadline.set(time.monotonic() + budget)

    try:
        return _attempts(adapter, request, sent, last_reason, repaired, budget, budget_env)
    finally:
        _deadline.reset(clock)


def _attempts(adapter, request, sent, last_reason, repaired, budget, budget_env) -> dict:
    """The retry loop. Split out only so the clock above is set and reset once."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[llm_json] %s (provider=%s, model=%s, schema=%s, attempt %d/%d, fp=%s)…",
                request.purpose,
                resolve_provider(capability=request.capability),
                model_id(capability=request.capability),
                schema_mode(capability=request.capability),
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
            if attempt < MAX_RETRIES and _worth_retrying(attempt, request, budget):
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue
            raise LLMJsonError(_with_clock(last_reason, budget, budget_env))
        except LLMJsonError:
            raise
        except Exception as e:  # noqa: BLE001 — surface a clear reason
            error = str(e)
            if "429" in error or "RESOURCE_EXHAUSTED" in error:
                last_reason = "Rate limited / quota exhausted on the text API (HTTP 429)."
            else:
                last_reason = f"Text API error during {request.purpose}: {error}"
                logger.error("[llm_json] %s", last_reason)
            if attempt < MAX_RETRIES and _worth_retrying(attempt, request, budget):
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue
            break

    raise LLMJsonError(_with_clock(last_reason, budget, budget_env))


def _worth_retrying(attempt: int, request: JsonRequest, budget: float) -> bool:
    """Is there time left for the backoff AND an attempt that could finish?

    ⚠ A RETRY THAT CANNOT ARRIVE IN TIME IS A PAID CALL FOR NOTHING. Sleeping
    eight seconds and then asking a model for a plan the browser has already
    stopped waiting for costs money and produces an answer nobody reads, so the
    loop stops early and says the budget ran out — which is the difference
    between "the model is slow" and "something is stuck", the two things the user
    is actually trying to tell apart.
    """
    backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
    if _time_left() - backoff >= MIN_ATTEMPT_SECONDS:
        return True
    logger.warning(
        "[llm_json] %s: %.0fs budget spent after %d attempt(s) — not trying again.",
        request.purpose, budget, attempt,
    )
    return False


def _with_clock(reason: str, budget: float, budget_env: str = "DIRECTOR_BUDGET_SECONDS") -> str:
    """The reason, plus the clock when the clock is why we stopped.

    The panel prints this verbatim under "The AI pass didn't run", so it has to
    say which of the two things happened in words a person can act on.
    """
    if _time_left() > MIN_ATTEMPT_SECONDS:
        return reason
    return (
        f"{reason} It ran out of time — {budget:.0f}s is all one call gets "
        f"({budget_env or 'DIRECTOR_BUDGET_SECONDS'}). A model this slow needs a "
        "bigger budget, or a smaller board."
    )
