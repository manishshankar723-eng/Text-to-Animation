"""
plan_agent.py — "Plan & Script": the conversational content planner.

The first step of the pipeline, before anything is drawn. The user talks to an
agent in plain language ("I run a mythology channel, plan me 3 months"), and
gets back a real content calendar they can export to Excel or Word and work
from.

Two capabilities, deliberately separate:

    chat()          — multi-turn conversation. The agent asks the questions a
                      strategist would ask (who is this for? how often can you
                      post? what has worked?) instead of guessing. This is what
                      makes the output specific rather than generic filler.

    generate_plan() — turns the conversation into a STRUCTURED calendar via
                      response_schema, so it can be rendered as a table and
                      exported. Never free text pretending to be a schedule.

Backend mirrors script_breakdown.py exactly: TEXT_PROVIDER switches between
Vertex AI and the Gemini Developer API, same retry/backoff, same greedy
sampling — a plan is a considered answer, not a lottery, so asking twice for the
same brief should give the same schedule.

Not a storyboard step: this spends TEXT quota only, never image quota.
"""

import json
import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

# The text backend, retry policy and determinism settings are already solved for
# the script breakdown — reuse them rather than growing a second set that can
# drift out of step.
from script_breakdown import (
    INITIAL_BACKOFF_SECONDS,
    MAX_RETRIES,
    _model_id,
    _resolve_provider,
    _sampling_kwargs,
    get_client,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output language
# ---------------------------------------------------------------------------
# A creator writes titles and hooks in the language their audience actually
# speaks. An English-only planner is unusable for a Hindi channel, so the plan
# is generated in whatever language they pick.
#
# Hinglish needs spelling out: left to itself the model writes Devanagari and
# calls it Hinglish, when what Indian creators actually publish is Hindi in
# LATIN script, mixed with English words.
LANGUAGES = {
    "english": "English",
    "hinglish": (
        "Hinglish — Hindi and English mixed the way Indian creators actually "
        "write titles and captions, in LATIN (Roman) script, NOT Devanagari. "
        "For example: 'Shiv ji ki ye kahani aapne kabhi nahi suni hogi'."
    ),
    "hindi": "Hindi, written in Devanagari script (देवनागरी)",
}
DEFAULT_LANGUAGE = "english"


def language_instruction(language: str | None) -> str:
    """The 'write it in this language' block appended to the plan request.

    An unknown value is treated as the user's own free-text language name, so
    'Tamil', 'Bhojpuri' or 'Spanish' all work without a code change.
    """
    key = (language or "").strip()
    if not key:
        return ""
    described = LANGUAGES.get(key.lower(), key)
    return (
        f"\n\nWRITE THE PLAN IN: {described}\n"
        "Every piece of text a human reads — title, hook, outline beats, call to "
        "action, keywords, pillar names, the summary and the assumptions — must "
        "be in that language. Titles and hooks especially: they are published as "
        "written, so they must read naturally to that audience, not like a "
        "translation.\n"
        "EXCEPTIONS, which stay exactly as specified in English because the app "
        "reads them as data, not prose: `goal` (reach/engagement/conversion/"
        "retention) and `effort` (low/medium/high)."
    )


# Guard rails. A plan longer than a year stops being a plan and starts being a
# wish; a conversation longer than this is re-sending a novel on every turn.
MAX_MONTHS = 12
MAX_ITEMS = 200
MAX_HISTORY_MESSAGES = 40
MAX_MESSAGE_CHARS = 8000


class PlanError(Exception):
    """Raised when a plan or a reply can't be produced.

    Carries a human-readable reason so the API can surface the ACTUAL cause.
    """


class _Retry(Exception):
    """Internal signal: retry the generation (e.g. malformed JSON)."""


# ---------------------------------------------------------------------------
# The agent's brief
# ---------------------------------------------------------------------------
_SYSTEM_INSTRUCTION = (
    "You are a content strategist for creators — YouTubers, short-form "
    "creators, 2D/3D artists, video editors, influencers and small businesses. "
    "You help them decide WHAT to make and WHEN, then hand them a schedule they "
    "can actually work from.\n\n"
    "How you behave:\n"
    "- ASK before you assume. If you don't know the niche, the audience, how "
    "often they can realistically publish, or what has already worked for them, "
    "ask — one or two focused questions at a time, never an interrogation. A "
    "specific plan needs specific inputs.\n"
    "- Whenever you ask something, ALSO offer it as a multiple-choice question "
    "in `questions`, so the creator can click an answer instead of typing an "
    "essay. Give 2-4 realistic, mutually exclusive options, each with one line "
    "explaining what choosing it means for their plan. Write options in THEIR "
    "language, with concrete numbers where it helps ('2 per week — one long "
    "video plus one short'), not vague labels ('moderate'). Put the option you "
    "would recommend first and mark it '(Recommended)' when you genuinely have "
    "a view. Ask at most 3 questions at once. Give every question a `header` of "
    "ONE or TWO words naming what it is about — 'Audience', 'Cadence', "
    "'Video length', 'Goal'. It is used as a tab label, so 'Question 1' is "
    "useless there.\n"
    "- Your `reply` still carries the human answer. Do NOT restate the options "
    "as a numbered list in the reply — they are shown as buttons.\n"
    "- When you have enough to plan and are not asking anything, return an "
    "EMPTY `questions` list.\n"
    "- Be concrete. 'Post more shorts' is useless. 'Tuesday: 45s short — the "
    "one Shiva Purana detail everyone gets wrong — hook in the first 2 seconds' "
    "is a plan.\n"
    "- Respect their capacity. A solo creator who can make one video a week "
    "must not be handed a daily schedule; that is how people quit.\n"
    "- Work in content PILLARS (3-5 recurring themes) so the channel builds an "
    "identity instead of a pile of unrelated uploads.\n"
    "- Be honest about what you don't know. If you have not been given real "
    "data about their channel, say the plan is based on what they've told you, "
    "and never invent view counts, subscriber numbers or past video titles.\n"
    "- Keep replies tight. This is a chat, not an essay: a few short paragraphs "
    "or a short list. Detail belongs in the plan you generate, not the chatter."
)

_PLAN_INSTRUCTION = (
    "Turn everything you know about this creator into a publishing calendar.\n"
    "Return between 1 and {max_items} items, in publishing order.\n"
    "Cover {months} month(s) at a cadence of {cadence}.\n"
    "For each item provide:\n"
    "  - slot: when it goes out, as a short human label — 'Week 1 · Tue', "
    "'Month 2 · Week 3'. Consistent across every item.\n"
    "  - title: the actual title to publish. Written to be clicked, not a "
    "topic label. No clickbait that the content doesn't pay off.\n"
    "  - hook: the first line/first 3 seconds, word for word. This is the "
    "single highest-leverage part of a short — write it, don't describe it.\n"
    "  - format: e.g. 'YouTube Short (45s)', 'Long-form (8-10 min)', "
    "'Instagram Reel', 'Carousel', 'Livestream'.\n"
    "  - pillar: which recurring theme this belongs to.\n"
    "  - outline: 2-4 short beats describing what happens, in order.\n"
    "  - keywords: 3-6 search terms / tags.\n"
    "  - cta: what the viewer is asked to do at the end.\n"
    "  - goal: 'reach' | 'engagement' | 'conversion' | 'retention'.\n"
    "  - effort: 'low' | 'medium' | 'high' — how much work it is to make. Mix "
    "them, so a heavy piece never lands the same week as another heavy piece.\n"
    "Also return `pillars`: the 3-5 recurring themes, each with a name and one "
    "line on why it earns a place.\n"
    "Also return `summary`: 2-3 sentences on the strategy — what this plan is "
    "betting on and why.\n"
    "Also return `assumptions`: anything you had to assume because it wasn't "
    "stated. Be honest here; an empty list is fine if nothing was assumed.\n"
)


# Clickable questions the agent asks alongside its reply. Bounded on purpose:
# more than 3 questions or 4 options is a form, not a conversation.
MAX_QUESTIONS = 3
MAX_OPTIONS = 4


def _chat_schema() -> types.Schema:
    """Structured chat turn: the prose reply PLUS any clickable questions.

    One call returns both, rather than a second call to "also generate some
    options" — that would double the cost of every turn and let the two drift
    apart (a reply asking one thing, buttons offering another).
    """
    return types.Schema(
        type=types.Type.OBJECT,
        required=["reply"],
        properties={
            "reply": types.Schema(type=types.Type.STRING),
            "questions": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    # `header` is REQUIRED: left optional the model skipped it
                    # and every tab read "Question 1", "Question 2".
                    required=["header", "question", "options"],
                    properties={
                        # Short tab label, e.g. "Cadence" or "Audience".
                        "header": types.Schema(type=types.Type.STRING),
                        "question": types.Schema(type=types.Type.STRING),
                        "options": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                required=["label"],
                                properties={
                                    "label": types.Schema(type=types.Type.STRING),
                                    "description": types.Schema(type=types.Type.STRING),
                                },
                            ),
                        ),
                    },
                ),
            ),
        },
    )


def _coerce_questions(raw) -> list[dict]:
    """Normalise the model's questions into clean, renderable ones.

    A question with fewer than two options isn't a choice, so it's dropped — the
    user can always just type instead.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for i, q in enumerate(raw[:MAX_QUESTIONS]):
        if not isinstance(q, dict):
            continue
        text = str(q.get("question", "")).strip()
        if not text:
            continue
        options: list[dict] = []
        seen: set[str] = set()
        for o in (q.get("options") or [])[: MAX_OPTIONS + 2]:
            if isinstance(o, str):
                o = {"label": o}
            if not isinstance(o, dict):
                continue
            label = str(o.get("label", "")).strip()
            if not label or label.lower() in seen:
                continue
            seen.add(label.lower())
            options.append(
                {"label": label, "description": str(o.get("description", "")).strip()}
            )
            if len(options) >= MAX_OPTIONS:
                break
        if len(options) < 2:
            continue
        header = str(q.get("header", "")).strip() or f"Question {i + 1}"
        out.append({"id": f"q{i + 1}", "header": header[:24], "question": text, "options": options})
    return out


def _plan_schema() -> types.Schema:
    """Structured-output schema for the calendar."""
    return types.Schema(
        type=types.Type.OBJECT,
        required=["items"],
        properties={
            "summary": types.Schema(type=types.Type.STRING),
            "pillars": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["name"],
                    properties={
                        "name": types.Schema(type=types.Type.STRING),
                        "why": types.Schema(type=types.Type.STRING),
                    },
                ),
            ),
            "items": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["title"],
                    properties={
                        "slot": types.Schema(type=types.Type.STRING),
                        "title": types.Schema(type=types.Type.STRING),
                        "hook": types.Schema(type=types.Type.STRING),
                        "format": types.Schema(type=types.Type.STRING),
                        "pillar": types.Schema(type=types.Type.STRING),
                        "outline": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                        ),
                        "keywords": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                        ),
                        "cta": types.Schema(type=types.Type.STRING),
                        "goal": types.Schema(type=types.Type.STRING),
                        "effort": types.Schema(type=types.Type.STRING),
                    },
                ),
            ),
            "assumptions": types.Schema(
                type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)
            ),
        },
    )


# ---------------------------------------------------------------------------
# Normalising what comes back
# ---------------------------------------------------------------------------
_GOALS = {"reach", "engagement", "conversion", "retention"}
_EFFORTS = {"low", "medium", "high"}


def _clean_list(raw, cap: int = 12) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = [str(x).strip() for x in raw if str(x).strip()]
    return out[:cap]


def _coerce_items(raw) -> list[dict]:
    """Validate/normalise the model's JSON into clean calendar rows."""
    if not isinstance(raw, list):
        raise PlanError("The model did not return a list of content items.")

    items: list[dict] = []
    for i, it in enumerate(raw[:MAX_ITEMS], start=1):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "")).strip()
        if not title:
            continue  # a row with no title is not a plan entry
        goal = str(it.get("goal", "")).strip().lower()
        effort = str(it.get("effort", "")).strip().lower()
        items.append(
            {
                "slot": str(it.get("slot", "")).strip() or f"Item {i}",
                "title": title,
                "hook": str(it.get("hook", "")).strip(),
                "format": str(it.get("format", "")).strip(),
                "pillar": str(it.get("pillar", "")).strip(),
                "outline": _clean_list(it.get("outline"), 8),
                "keywords": _clean_list(it.get("keywords"), 10),
                "cta": str(it.get("cta", "")).strip(),
                # Unknown values are blanked rather than kept: a bogus enum
                # would colour a chip wrong and quietly mislead.
                "goal": goal if goal in _GOALS else "",
                "effort": effort if effort in _EFFORTS else "",
            }
        )

    if not items:
        raise PlanError(
            "No usable content items came back. Try telling the agent a bit "
            "more about the channel first."
        )
    return items


def _coerce_pillars(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for p in raw:
        if isinstance(p, str):
            p = {"name": p}
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "why": str(p.get("why", "")).strip()})
    return out[:8]


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
def _to_contents(messages: list[dict]) -> list[types.Content]:
    """Our stored messages → the SDK's Content list.

    Stored form is {role: "user"|"agent", text: "..."} because that is what the
    client renders; the SDK wants "user"/"model". Kept as a translation rather
    than storing the SDK's vocabulary, so the transcript stays readable in the
    database and survives an SDK rename.
    """
    out: list[types.Content] = []
    for m in messages[-MAX_HISTORY_MESSAGES:]:
        text = str(m.get("text", "") or "").strip()
        if not text:
            continue
        role = "model" if m.get("role") in ("agent", "model", "assistant") else "user"
        out.append(
            types.Content(role=role, parts=[types.Part(text=text[:MAX_MESSAGE_CHARS])])
        )
    return out


def _call(contents, config, what: str) -> str:
    """One text call with the shared retry/backoff policy. Returns the text."""
    provider = _resolve_provider(None)
    client = get_client(provider)
    model_id = _model_id(provider)
    last_reason = f"Unknown error while {what}."

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[plan] %s (provider=%s, model=%s, attempt %d/%d)…",
                what, provider, model_id, attempt, MAX_RETRIES,
            )
            response = client.models.generate_content(
                model=model_id, contents=contents, config=config
            )
            text = getattr(response, "text", None)
            if not text:
                last_reason = (
                    "The model returned an empty response (it may have been "
                    "blocked by a safety filter). Try rephrasing."
                )
                raise _Retry(last_reason)
            return text

        except _Retry:
            if attempt < MAX_RETRIES:
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue
            raise PlanError(last_reason)
        except Exception as e:  # noqa: BLE001 — surface a clear reason
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                last_reason = "Rate limited / quota exhausted on the text API (HTTP 429)."
            else:
                last_reason = f"Text API error: {error_str}"
            logger.warning("[plan] %s", last_reason)
            if attempt < MAX_RETRIES:
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue

    raise PlanError(last_reason)


def chat(messages: list[dict], channel_context: str = "") -> dict:
    """One conversational turn. `messages` is the whole transcript so far.

    Args:
        messages: [{role: "user"|"agent", text: str}, …], oldest first, ending
            with the user's newest message.
        channel_context: real, researched facts about the user's channel (see
            youtube_research.py). Passed as system context so the agent can use
            it — and so it never has to invent numbers.

    Returns:
        {"reply": str, "questions": [{id, header, question, options[]}]}

        `questions` is what the UI renders as clickable answers; it is empty
        whenever the agent isn't asking anything. A malformed or empty question
        list is never fatal — the user can always just type.
    """
    convo = [m for m in (messages or []) if str(m.get("text", "") or "").strip()]
    if not convo:
        raise PlanError("Say something to the agent to get started.")

    system = _SYSTEM_INSTRUCTION
    if channel_context.strip():
        system += (
            "\n\nREAL DATA about this creator's channel follows. Use it, cite it "
            "when useful, and do NOT contradict it. If something isn't in here, "
            "you don't know it — ask, don't guess.\n\n" + channel_context.strip()
        )

    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=_chat_schema(),
        **_sampling_kwargs(),
    )
    payload = _call(_to_contents(convo), config, "replying")

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        # Structured output failed but the model still said something useful —
        # keep the words rather than failing the turn over its wrapper.
        logger.warning("[plan] reply wasn't valid JSON; using it as plain text")
        return {"reply": payload.strip(), "questions": []}

    reply = str((raw or {}).get("reply", "")).strip()
    questions = _coerce_questions((raw or {}).get("questions"))
    if not reply and not questions:
        raise PlanError("The model returned an empty reply. Try rephrasing.")

    logger.info(
        "[plan] reply: %d chars, %d question(s) over %d message(s)",
        len(reply), len(questions), len(convo),
    )
    return {"reply": reply, "questions": questions}


def generate_plan(
    messages: list[dict],
    months: int = 1,
    cadence: str = "2 videos per week",
    channel_context: str = "",
    language: str | None = None,
) -> dict:
    """Turn the conversation into a structured content calendar.

    Args:
        messages: the conversation so far — this is the brief.
        months: how many months to cover (1-12).
        cadence: how often they publish, in the user's own words.
        channel_context: researched channel facts, if any.
        language: what to WRITE the plan in — a key from LANGUAGES
            ("english" / "hinglish" / "hindi") or any language name the user
            typed. Empty means English.

    Returns:
        {"summary": str, "pillars": [{name, why}], "items": [{…}],
         "assumptions": [str], "months": int, "cadence": str, "language": str}

    Raises:
        PlanError: with a human-readable reason on any failure.
    """
    convo = [m for m in (messages or []) if str(m.get("text", "") or "").strip()]
    if not convo:
        raise PlanError("Talk to the agent about your channel first, then generate a plan.")

    months = max(1, min(int(months or 1), MAX_MONTHS))
    cadence = (cadence or "").strip() or "2 videos per week"

    system = _SYSTEM_INSTRUCTION
    if channel_context.strip():
        system += "\n\nREAL DATA about this creator's channel:\n" + channel_context.strip()

    instruction = _PLAN_INSTRUCTION.format(
        max_items=MAX_ITEMS, months=months, cadence=cadence
    )
    # Appended LAST so the language requirement is the final thing the model
    # reads before it answers — the most reliable position for a hard rule.
    instruction += language_instruction(language)
    contents = _to_contents(convo) + [
        types.Content(role="user", parts=[types.Part(text=instruction)])
    ]
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=_plan_schema(),
        **_sampling_kwargs(),
    )

    payload = _call(contents, config, f"planning {months} month(s)")
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as e:
        raise PlanError(f"The model returned invalid JSON ({e}).")

    items = _coerce_items(raw.get("items") if isinstance(raw, dict) else raw)
    plan = {
        "summary": str((raw or {}).get("summary", "")).strip(),
        "pillars": _coerce_pillars((raw or {}).get("pillars")),
        "items": items,
        "assumptions": _clean_list((raw or {}).get("assumptions"), 12),
        "months": months,
        "cadence": cadence,
        # Stored so reopening a plan shows what it was written in, and so
        # regenerating defaults to the same language.
        "language": (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE,
    }
    logger.info(
        "[plan] produced %d item(s) across %d month(s) in %s, %d pillar(s), "
        "%d assumption(s)",
        len(items), months, plan["language"], len(plan["pillars"]),
        len(plan["assumptions"]),
    )
    return plan
