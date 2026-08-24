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

    write_script()  — takes ONE row of that calendar (or a free-text ask) and
                      writes the actual shooting script for it. This is the
                      "& Script" half of the workflow's name, and until it
                      existed the plan stopped at a list of titles.

⚠ **write_script's OUTPUT FORMAT IS A CONTRACT WITH `script_breakdown.py`**, not
a styling choice. The script it writes is fed straight into the storyboard
breakdown, which quotes `script_excerpt` VERBATIM out of it, matches character
names against its own cast list, and reads scene boundaries off the headings.
So the writer is told the same things the breakdown reads: one physical beat per
sentence, every visible person named (never "he" or "the man"), speech as
`NAME: line`, a new scene heading on every change of place or time. See
`_SCRIPT_INSTRUCTION` — every rule there exists because the breakdown looks for
it. Change one and `tests/plan_script_check.py` will tell you.

Backend mirrors script_breakdown.py exactly: TEXT_PROVIDER switches between
Vertex AI and the Gemini Developer API, same retry/backoff, same greedy
sampling — a plan is a considered answer, not a lottery, so asking twice for the
same brief should give the same schedule.

Every call reports its TOKENS (see ai_usage.py). The counts ride back on the
same return value as the content, including the tokens spent on failed retries,
so a session total is the sum of what actually happened rather than a separate
number that drifts from it.

Not a storyboard step: this spends TEXT quota only, never image quota.
"""

import json
import logging
import os
import time

from dotenv import load_dotenv
from google.genai import types

from ai_usage import Usage, describe, merge, usage_from

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


def _block_reason(response) -> str:
    """Why the provider returned nothing, in its OWN words where it gave any.

    "The model returned an empty response" is the least useful thing we can say:
    it is indistinguishable from a network hiccup, and it sent people rephrasing
    a prompt that was never the problem. The provider does say which category
    tripped and whether it was the prompt or the answer — so say that, and let
    the caller decide whether it is worth a retry.
    """
    feedback = getattr(response, "prompt_feedback", None)
    blocked = getattr(feedback, "block_reason", None)
    if blocked:
        detail = getattr(feedback, "block_reason_message", "") or ""
        name = getattr(blocked, "name", None) or str(blocked)
        return f"The provider blocked the request ({name}). {detail}".strip()

    for candidate in getattr(response, "candidates", None) or []:
        finish = getattr(candidate, "finish_reason", None)
        name = getattr(finish, "name", None) or (str(finish) if finish else "")
        if name and name.upper() not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
            if name.upper() == "MAX_TOKENS":
                return (
                    "The answer hit the output limit before it finished. Ask for "
                    "something shorter, or raise the limit."
                )
            return f"The provider stopped the answer early ({name})."

    return "The model returned an empty response."


def _call(contents, config, what: str, usage_out: list | None = None) -> str:
    """One text call with the shared retry/backoff policy. Returns the text.

    `usage_out`, when given, has this call's `Usage` appended to it — including
    the usage of FAILED attempts, because a retry is billed too and a token
    count that quietly omits three failures is not a token count. The caller
    sums the list; see ai_usage.merge.
    """
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
            if usage_out is not None:
                usage_out.append(usage_from(response, model_id))
            text = getattr(response, "text", None)
            if not text:
                last_reason = _block_reason(response)
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
        {"reply": str, "questions": [{id, header, question, options[]}],
         "usage": {…}}

        `questions` is what the UI renders as clickable answers; it is empty
        whenever the agent isn't asking anything. A malformed or empty question
        list is never fatal — the user can always just type.

        `usage` is this turn's token count (see ai_usage.Usage.as_dict).
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
    spent: list[Usage] = []
    payload = _call(_to_contents(convo), config, "replying", spent)
    usage = merge(*spent)

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        # Structured output failed but the model still said something useful —
        # keep the words rather than failing the turn over its wrapper.
        logger.warning("[plan] reply wasn't valid JSON; using it as plain text")
        return {"reply": payload.strip(), "questions": [], "usage": usage.as_dict()}

    reply = str((raw or {}).get("reply", "")).strip()
    questions = _coerce_questions((raw or {}).get("questions"))
    if not reply and not questions:
        raise PlanError("The model returned an empty reply. Try rephrasing.")

    logger.info(
        "[plan] reply: %d chars, %d question(s) over %d message(s) — %s",
        len(reply), len(questions), len(convo), describe(usage),
    )
    return {"reply": reply, "questions": questions, "usage": usage.as_dict()}


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

    spent: list[Usage] = []
    payload = _call(contents, config, f"planning {months} month(s)", spent)
    usage = merge(*spent)
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
        # What THIS calendar cost. Kept on the plan rather than only added to
        # the session total, so regenerating shows the price of the regenerate.
        "usage": usage.as_dict(),
    }
    logger.info(
        "[plan] produced %d item(s) across %d month(s) in %s, %d pillar(s), "
        "%d assumption(s) — %s",
        len(items), months, plan["language"], len(plan["pillars"]),
        len(plan["assumptions"]), describe(usage),
    )
    return plan


# ===========================================================================
# The "& Script" half — writing the actual script for one upload
# ===========================================================================
# A calendar of titles is half a product. This turns ONE row of it (or a
# free-text ask that never went through the calendar at all) into a script that
# can be shot, read aloud, or handed straight to the storyboard breakdown.
#
# ⚠ EVERYTHING IN THIS SECTION IS SHAPED BY WHAT `script_breakdown.py` READS.
# The script produced here is the INPUT to Stage A of Script → Storyboard, and
# that stage is not a lenient reader: it copies `script_excerpt` VERBATIM out of
# this text, matches every person named in a shot against its own cast list, and
# reads scene boundaries off the headings. A script written as a blog post, with
# pronouns and paragraph-long sentences, breaks all three QUIETLY — the board
# still generates, it is just wrong, and nobody finds out until the panels come
# back with the wrong people in them.
#
# So the rules below are not a house style. Each is the WRITING side of a rule
# the breakdown already enforces on the READING side, and they are asserted as a
# pair in tests/plan_script_check.py. Before editing one, go and read its twin.

# How long a script is, in words, for a given duration. 145 words per minute is
# an unhurried spoken pace — fast enough for short-form, slow enough that the
# on-screen action has somewhere to happen. Action lines are not spoken, so the
# model is given a word budget for dialogue/VO only and told action is on top.
WORDS_PER_MINUTE = 145
MIN_SCRIPT_SECONDS = 10
MAX_SCRIPT_SECONDS = 3600  # an hour; past that this is not one upload

# Ceilings, for the same reason MAX_SHOTS exists in the breakdown: a runaway
# generation is a bill, not a feature.
MAX_SCENES = 40
MAX_BEATS_PER_SCENE = 80
MAX_CAST = 20

BEAT_KINDS = ("action", "dialogue", "vo", "text")

# What the script is rated, in the writer's own assessment. RECORDED, NOT
# ENFORCED: it labels what came back and lets the UI show it, which is more
# useful to a creator than a filter that silently rewrites their brief.
RATINGS = ("general", "teen", "mature")


class ScriptError(PlanError):
    """A script couldn't be written.

    Subclasses PlanError so anything already catching PlanError keeps working,
    while the route can still answer differently when a script fails.
    """


# ---------------------------------------------------------------------------
# What this writer will and won't do
# ---------------------------------------------------------------------------
# The complaint that produced this block: the agent was refusing, softening or
# bolting disclaimers onto ordinary creative briefs — a crime story, a horror
# short, a mythological war, a film about addiction. That is a real cost, not a
# cosmetic one. Drama is made of the things that go wrong, so a writing tool
# that flinches at conflict cannot write drama; it writes the toothless version,
# and the creator either fights it every time or stops using it.
#
# The stance is therefore stated EXPLICITLY here rather than left to whatever
# the model defaults to, and the line is drawn where it actually belongs — at
# material that does harm once it leaves the page, not at subject matter that is
# merely dark. It is in one named constant so it can be read, argued with and
# changed by a person, instead of being smeared across a dozen prompt strings.
_SCRIPT_STANCE = (
    "WHAT YOU WRITE, AND WHAT YOU DO NOT REFUSE:\n"
    "You are writing fiction and non-fiction for a professional creator. Stories "
    "are made of trouble. Write the brief you are given, at the intensity it "
    "asks for:\n"
    "- Violence, crime, war, revenge, horror, death and cruelty are ordinary "
    "dramatic material. A murder mystery needs a murder; a war film needs the "
    "war; a mythological epic contains what the myth contains.\n"
    "- So do addiction, abuse, grief, poverty, mental illness, betrayal, "
    "corruption, and the parts of religion, caste, politics and history that "
    "people argue about. Write them honestly and specifically, from inside the "
    "characters — not as a cautionary poster.\n"
    "- Morally repellent characters get to be repellent, and to be persuasive. "
    "A villain who is obviously wrong is not a villain, he is a lecture.\n"
    "- Sex and desire belong in stories. Write them the way a film does — "
    "attraction, tension, what is said and not said, a cut before the explicit "
    "— rather than refusing the subject or writing pornography.\n"
    "- Strong language where the character would use it.\n"
    "NEVER add a content warning, a disclaimer, a moral epilogue, or a remark "
    "about your own comfort. NEVER quietly soften a brief into a milder story "
    "and hand that back as though it were what was asked for. If the creator "
    "asked for a bleak ending, do not give them a hopeful one.\n"
    "The ONLY things you decline — briefly, with no lecture, offering the "
    "nearest story you CAN write: material that works as real instructions for "
    "serious harm regardless of the scene wrapped around it (a usable synthesis "
    "route, a working weapon or explosive build, functioning malicious code); "
    "sexual content involving anyone under 18; and content built to harass, "
    "defame or sexualise a REAL, identifiable, private person. A named public "
    "figure in satire, biography or historical drama is fine.\n"
)

_SCRIPT_SYSTEM = (
    "You are a professional screenwriter working for a video creator. You are "
    "handed one item from their content calendar — or a single-line ask — and "
    "you write the script that actually gets shot.\n\n"
    + _SCRIPT_STANCE
    + "\nHOW YOU WRITE:\n"
    "- The first three seconds decide whether the rest is watched. Open on the "
    "hook, already in motion — never on a greeting, a channel intro or a "
    "throat-clear.\n"
    "- Show it happening. A script is what the camera sees and what people say, "
    "not an essay narrated over stock footage.\n"
    "- Cut every line that does not earn its second.\n"
    "- End on the call to action the creator asked for: one line, in the voice "
    "of the piece."
)

# The format rules. Every one of them is here because the breakdown reads it.
_SCRIPT_INSTRUCTION = (
    "Write the script now.\n\n"
    "LENGTH: about {seconds} seconds on screen — roughly {words} words of "
    "spoken dialogue and voice-over TOTAL, across the whole script. Action "
    "lines are not spoken and do not count towards that. Come in close to it: a "
    "45-second script carrying 600 words of dialogue is not a 45-second "
    "script.\n\n"
    "STRUCTURE — this script is read by another program that turns it into a "
    "storyboard, so these are requirements, not preferences:\n"
    "1. SCENES. Start a new scene whenever the PLACE or the TIME changes. Each "
    "scene's `heading` is a standard slug line: INT. or EXT., the location, a "
    "dash, then the time of day — 'INT. KABIR'S BEDROOM - MORNING'. Coming back "
    "to a location later is a NEW scene, not the old one continued.\n"
    "2. ONE BEAT PER LINE. Every `action` beat is ONE thing that happens, "
    "written as one plain sentence in the present tense. If your sentence needs "
    "'then', 'as' or 'while', it is two beats — split it. A wind-up, the action "
    "and the reaction to it are three beats, not one.\n"
    "3. OPEN EACH BEAT AT ITS START. Write the instant BEFORE the movement — "
    "the hand still holding the cup, the mouth about to open. Never write a "
    "beat that begins in the middle of a motion, and never show a thing already "
    "moving without an earlier beat showing the person who set it moving.\n"
    "4. NAME EVERY PERSON, EVERY TIME. In every action beat, write each visible "
    "character's NAME — never 'he', 'she', 'they', 'the man', 'the woman'. The "
    "program reading this sees one sentence at a time and cannot resolve a "
    "pronoun. Naming somebody's chair, bed or door does NOT put that person in "
    "the shot: if they are in frame, name them and say what their body is doing "
    "('Kabir sits up in bed, rubbing his cheek').\n"
    "5. SPELL EVERY NAME THE SAME WAY everywhere — in the cast list, in the "
    "action, and on the dialogue beats. One character, one spelling.\n"
    "6. POSTURE CARRIES. Somebody lying down stays lying down until a beat "
    "shows them getting up. Say what each body is doing, not what each person "
    "is feeling.\n"
    "7. BACKGROUND PEOPLE ARE CONTINUITY. If a scene holds a crowd, a class or "
    "a family in the room, keep saying so — a room that empties between two "
    "beats of one scene is the most obvious error there is.\n\n"
    "BEAT TYPES — every beat carries a `type`:\n"
    "  'action'   — what the camera sees. `character` is empty.\n"
    "  'dialogue' — someone speaking on camera. `character` is their cast name; "
    "`text` is exactly the words they say, with no name prefix inside it.\n"
    "  'vo'       — voice-over / narration. `character` is the speaker's name, "
    "or 'NARRATOR'.\n"
    "  'text'     — words appearing ON SCREEN (a title card, a caption, a "
    "statistic). `character` is empty.\n\n"
    "ALSO RETURN:\n"
    "  - `title`: the title this gets published under.\n"
    "  - `logline`: one sentence — what happens, and why anyone should care.\n"
    "  - `characters`: every named person, each with a VISUAL description an "
    "artist could draw consistently — age, build, hair, clothing, and their "
    "regional appearance and period-correct dress. Write 'a lean South Asian "
    "hunter with weathered brown skin, black hair tied back, in a coarse cotton "
    "dhoti', never 'a lean hunter in simple attire'. A narrator who is never "
    "seen still belongs in the list, described as 'voice only'.\n"
    "  - `cta`: the one line the viewer is asked to act on at the end.\n"
    "  - `rating`: your own read of what you wrote — 'general', 'teen' or "
    "'mature'. This LABELS the script; it does not restrict it. Rate honestly "
    "rather than defensively.\n"
    "  - `notes`: anything the creator needs in order to shoot this — a prop "
    "that has to exist, a location, a fact worth checking. Empty list if there "
    "is nothing.\n"
)


def _script_schema() -> types.Schema:
    """Structured-output schema for one script.

    Structured rather than free text for the same reason the calendar is: this
    one answer is rendered as a document, exported three ways, AND flattened
    into the exact plain-text shape the breakdown reads. Parsing all of that
    back out of prose would mean three parsers that disagree with each other.
    """
    return types.Schema(
        type=types.Type.OBJECT,
        required=["title", "scenes"],
        properties={
            "title": types.Schema(type=types.Type.STRING),
            "logline": types.Schema(type=types.Type.STRING),
            "cta": types.Schema(type=types.Type.STRING),
            "rating": types.Schema(type=types.Type.STRING),
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
            "scenes": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["heading", "beats"],
                    properties={
                        "heading": types.Schema(type=types.Type.STRING),
                        "beats": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                required=["type", "text"],
                                properties={
                                    "type": types.Schema(type=types.Type.STRING),
                                    "character": types.Schema(type=types.Type.STRING),
                                    "text": types.Schema(type=types.Type.STRING),
                                },
                            ),
                        ),
                    },
                ),
            ),
            "notes": types.Schema(
                type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)
            ),
        },
    )


# The four categories Gemini lets a caller set a threshold on, and the
# thresholds it accepts. Spelled out rather than read off the SDK because the
# SDK does not validate either one — see the note in _safety_settings.
_SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
_SAFETY_THRESHOLDS = frozenset(
    {
        "BLOCK_NONE",
        "BLOCK_ONLY_HIGH",
        "BLOCK_MEDIUM_AND_ABOVE",
        "BLOCK_LOW_AND_ABOVE",
        "HARM_BLOCK_THRESHOLD_UNSPECIFIED",
    }
)


def _safety_settings() -> list | None:
    """Provider-side safety thresholds for the script call.

    ⚠ THIS IS A SUPPORTED API PARAMETER, NOT A TRICK. Gemini exposes a threshold
    per harm category precisely because the right setting depends on what is
    being built, and a fiction-writing tool is the textbook case for loosening
    it: the default trips on ordinary dramatic material — a fistfight, a threat,
    a character who drinks — and a script that comes back EMPTY WITH NO REASON
    is the single most-reported failure of a tool like this. (`_block_reason`
    above is the other half of that fix: when the provider does refuse, the user
    is now told which category tripped instead of "empty response".)

    BLOCK_ONLY_HIGH leaves the provider's own hard floor in place. It does not
    turn safety off; it stops the middle of the range from eating drama. The
    editorial line this tool actually draws is `_SCRIPT_STANCE`, written where a
    human can read it.

    Override with AI_SAFETY_THRESHOLD (BLOCK_NONE / BLOCK_ONLY_HIGH /
    BLOCK_MEDIUM_AND_ABOVE / BLOCK_LOW_AND_ABOVE, or OFF to send nothing and
    take the provider default). Returns None when this SDK build has no
    SafetySetting type, so an older google-genai degrades to the default rather
    than failing every script call on an unknown kwarg.
    """
    wanted = (os.environ.get("AI_SAFETY_THRESHOLD") or "BLOCK_ONLY_HIGH").strip().upper()
    if wanted in ("OFF", "DEFAULT", ""):
        return None

    # ⚠ VALIDATED HERE, NOT BY THE SDK. `SafetySetting` accepts any string and
    # coerces it into a new enum member, so a typo in .env sails through
    # construction and is rejected by the API instead — turning one bad env var
    # into every script call failing at request time, with an error that names
    # the API rather than the setting. Caught by the test, which is why the list
    # is spelled out rather than trusted to the SDK.
    if wanted not in _SAFETY_THRESHOLDS:
        logger.warning(
            "[script] AI_SAFETY_THRESHOLD=%r is not one of %s — using provider defaults.",
            wanted, ", ".join(sorted(_SAFETY_THRESHOLDS)),
        )
        return None

    setting = getattr(types, "SafetySetting", None)
    if setting is None:
        logger.info("[script] this google-genai has no SafetySetting — using provider defaults.")
        return None

    try:
        return [setting(category=c, threshold=wanted) for c in _SAFETY_CATEGORIES]
    except Exception as e:  # noqa: BLE001 — a bad setting must not kill the call
        logger.warning(
            "[script] SafetySetting rejected %r (%s) — using provider defaults.", wanted, e
        )
        return None


# ---------------------------------------------------------------------------
# Normalising the script that comes back
# ---------------------------------------------------------------------------
def _coerce_beats(raw) -> list[dict]:
    """Clean one scene's beats.

    An unknown `type` reads as action — the safest default, because a
    mislabelled dialogue line rendered as action is still readable, while a
    dropped beat is a hole in the story that nothing downstream can recover.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for b in raw[:MAX_BEATS_PER_SCENE]:
        if isinstance(b, str):
            b = {"type": "action", "text": b}
        if not isinstance(b, dict):
            continue
        text = str(b.get("text", "")).strip()
        if not text:
            continue
        kind = str(b.get("type", "")).strip().lower()
        if kind not in BEAT_KINDS:
            kind = "action"
        character = str(b.get("character", "")).strip()
        # A spoken beat with nobody speaking it cannot be voiced or attributed,
        # and the breakdown would drop the line entirely. Name it rather than
        # lose it.
        if kind in ("dialogue", "vo") and not character:
            character = "NARRATOR" if kind == "vo" else "SPEAKER"
        if kind in ("action", "text"):
            character = ""
        out.append({"type": kind, "character": character, "text": text})
    return out


def _coerce_scenes(raw) -> list[dict]:
    """Validate/normalise the scene list, numbering it ourselves.

    The number is assigned here rather than taken from the model because it is
    what the flattened text prints and what the breakdown counts scenes by; a
    model that repeats or skips a number would produce a script whose scene 3
    appears twice.
    """
    if not isinstance(raw, list):
        raise ScriptError("The model did not return a list of scenes.")
    out: list[dict] = []
    for i, s in enumerate(raw[:MAX_SCENES], start=1):
        if not isinstance(s, dict):
            continue
        beats = _coerce_beats(s.get("beats"))
        if not beats:
            continue  # a scene with nothing in it is not a scene
        heading = str(s.get("heading", "")).strip() or f"SCENE {i}"
        out.append({"number": len(out) + 1, "heading": heading, "beats": beats})
    if not out:
        raise ScriptError(
            "No usable scenes came back. Try describing the video in a bit more "
            "detail, or ask again."
        )
    return out


def _coerce_cast(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for c in raw:
        if isinstance(c, str):
            c = {"name": c}
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "description": str(c.get("description", "")).strip()})
    return out[:MAX_CAST]


def script_to_text(script: dict) -> str:
    """The script as PLAIN TEXT, in the shape `script_breakdown.py` reads best.

    ⚠ THIS IS THE HANDOFF, AND IT IS ONE FUNCTION ON PURPOSE. Every route out of
    this workflow — the "Open in Script to Storyboard" button, the .txt export,
    the copy button, the on-screen preview — goes through here, so what the user
    reads, what they download and what the breakdown parses are the same bytes.
    Three call sites each building their own string is exactly how an exported
    file and a generated board end up being subtly different documents.

    Why this layout, line by line:
      - The CAST block comes first because the breakdown builds its own cast
        list and matches each shot's `characters` against it. Handing it the
        names and the appearances up front is what keeps one character looking
        like themselves across forty panels.
      - Scene headings are slug lines alone on a line with blank lines either
        side — what the breakdown reads scene boundaries off.
      - ONE BEAT PER LINE, so a shot's `script_excerpt` can quote a whole line
        verbatim and land on exactly one panel.
      - Dialogue is `NAME: line`, the form the breakdown's dialogue extraction
        already expects, with `(V.O.)` marking a speaker who is not in frame.
    """
    parts: list[str] = []

    title = str(script.get("title", "")).strip()
    if title:
        parts.append(title.upper())

    logline = str(script.get("logline", "")).strip()
    if logline:
        parts.append(f"LOGLINE: {logline}")

    cast = script.get("characters") or []
    if cast:
        block = ["CAST"]
        for c in cast:
            name = str(c.get("name", "")).strip()
            desc = str(c.get("description", "")).strip()
            if not name:
                continue
            block.append(f"{name} — {desc}" if desc else name)
        parts.append("\n".join(block))

    for scene in script.get("scenes") or []:
        heading = str(scene.get("heading", "")).strip()
        block = [f"SCENE {scene.get('number', 1)}. {heading}".rstrip(". ")]
        for beat in scene.get("beats") or []:
            kind = beat.get("type")
            text = str(beat.get("text", "")).strip()
            name = str(beat.get("character", "")).strip()
            if kind == "dialogue":
                block.append(f"{name.upper()}: {text}")
            elif kind == "vo":
                # (V.O.) is the standard mark. The breakdown reads the line as
                # speech either way; what this adds is telling the artist not to
                # expect the speaker in frame.
                block.append(f"{name.upper()} (V.O.): {text}")
            elif kind == "text":
                block.append(f"ON SCREEN: {text}")
            else:
                block.append(text)
        parts.append("\n".join(block))

    cta = str(script.get("cta", "")).strip()
    if cta:
        parts.append(f"CALL TO ACTION: {cta}")

    return "\n\n".join(parts).strip() + "\n"


def spoken_words(script: dict) -> int:
    """How many words are actually SAID.

    What the runtime estimate is built on, and the honest check on whether a
    45-second script is a 45-second script. Action lines are excluded because
    nobody reads them aloud.
    """
    total = 0
    for scene in script.get("scenes") or []:
        for beat in scene.get("beats") or []:
            if beat.get("type") in ("dialogue", "vo"):
                total += len(str(beat.get("text", "")).split())
    return total


def _item_brief(item: dict) -> str:
    """One calendar row, written out as a brief for the writer.

    The row already holds a title, a hook, an outline, a format, a pillar and a
    CTA — everything a brief needs. Asking the user to supply it again would be
    asking them to retype what they are looking at.
    """
    lines = []
    for label, key in (
        ("Title", "title"),
        ("Format", "format"),
        ("Content pillar", "pillar"),
        ("Publishing slot", "slot"),
        ("Goal", "goal"),
    ):
        value = str(item.get(key, "")).strip()
        if value:
            lines.append(f"{label}: {value}")
    hook = str(item.get("hook", "")).strip()
    if hook:
        lines.append(
            f"Hook — the creator already wrote this, so open with it, in these "
            f"words or better: {hook}"
        )
    outline = item.get("outline") or []
    if outline:
        lines.append("Outline the creator wants followed, in order:")
        lines.extend(f"  {i}. {beat}" for i, beat in enumerate(outline, 1))
    cta = str(item.get("cta", "")).strip()
    if cta:
        lines.append(f"Call to action to end on: {cta}")
    keywords = item.get("keywords") or []
    if keywords:
        lines.append(f"Search terms it should cover naturally: {', '.join(keywords)}")
    return "\n".join(lines)


def write_script(
    messages: list[dict] | None = None,
    item: dict | None = None,
    brief: str = "",
    seconds: int = 60,
    language: str | None = None,
    channel_context: str = "",
    notes: str = "",
) -> dict:
    """Write the script for one video.

    Args:
        messages: the planning conversation, used as BACKGROUND — who this
            creator is, who they are talking to, what has worked before.
            Optional: a script can be asked for on its own.
        item: one row of the generated calendar, when this script is for a
            planned upload. Its title/hook/outline/CTA become the brief.
        brief: a free-text ask, for a script that is not on the calendar ("a
            3-minute horror short about a lift that stops on floor 7").
            Required when `item` is None.
        seconds: target runtime.
        language: what to WRITE it in — see LANGUAGES. It matters most for
            dialogue, which is performed exactly as written.
        channel_context: researched channel facts, if any.
        notes: anything extra the creator typed for this one script.

    Returns:
        {title, logline, characters[], scenes[], cta, rating, notes[],
         text, spoken_words, estimated_seconds, seconds, language, usage{}}

        `text` is the script flattened for `script_breakdown.py` — see
        script_to_text. It is built HERE rather than in the browser so the
        export, the clipboard and the storyboard all carry identical bytes.

    Raises:
        ScriptError: with a human-readable reason on any failure, including the
            provider's own words when it refused.
    """
    item = item if isinstance(item, dict) else None
    brief = (brief or "").strip()
    if not item and not brief:
        raise ScriptError("Pick an upload from the calendar, or describe the video you want.")

    seconds = max(MIN_SCRIPT_SECONDS, min(int(seconds or 60), MAX_SCRIPT_SECONDS))
    words = max(20, round(seconds / 60 * WORDS_PER_MINUTE))

    system = _SCRIPT_SYSTEM
    if channel_context.strip():
        system += (
            "\n\nREAL DATA about this creator's channel. Use it, never contradict "
            "it, and never invent figures it does not contain.\n\n"
            + channel_context.strip()
        )

    ask = ["THE BRIEF:"]
    if item:
        ask.append(_item_brief(item))
    if brief:
        ask.append(f"What the creator asked for: {brief}")
    if notes.strip():
        ask.append(f"Extra notes for this script: {notes.strip()}")

    instruction = (
        "\n".join(ask)
        + "\n\n"
        + _SCRIPT_INSTRUCTION.format(seconds=seconds, words=words)
        # Language goes LAST for the same reason it does in generate_plan: the
        # final thing the model reads before answering is the rule it holds to
        # most reliably.
        + language_instruction(language)
    )

    # The planning conversation is CONTEXT, not the request. Passing it as prior
    # turns — rather than pasting a transcript into the prompt — is what lets
    # the writer use "you said your audience is 18-24 and hates intros" without
    # the transcript competing with the instruction for the model's attention.
    contents = _to_contents(messages or []) + [
        types.Content(role="user", parts=[types.Part(text=instruction)])
    ]

    config_kwargs = dict(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=_script_schema(),
        **_sampling_kwargs(),
    )
    safety = _safety_settings()
    if safety:
        config_kwargs["safety_settings"] = safety
    config = types.GenerateContentConfig(**config_kwargs)

    spent: list[Usage] = []
    try:
        payload = _call(contents, config, f"writing a {seconds}s script", spent)
    except PlanError as e:
        # Re-raised as a ScriptError so the route can answer differently, but
        # the reason passes through UNCHANGED — a provider block is the one
        # message the user most needs to read verbatim.
        raise ScriptError(str(e)) from e
    usage = merge(*spent)

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as e:
        raise ScriptError(f"The model returned invalid JSON ({e}).")
    if not isinstance(raw, dict):
        raise ScriptError("The model did not return a script object.")

    rating = str(raw.get("rating", "")).strip().lower()
    script = {
        "title": (
            str(raw.get("title", "")).strip()
            or str((item or {}).get("title", "")).strip()
            or "Untitled script"
        ),
        "logline": str(raw.get("logline", "")).strip(),
        "characters": _coerce_cast(raw.get("characters")),
        "scenes": _coerce_scenes(raw.get("scenes")),
        "cta": str(raw.get("cta", "")).strip(),
        # Blanked rather than kept when it isn't one of ours, for the same
        # reason a bogus `goal` is: a made-up rating would colour a chip wrong.
        "rating": rating if rating in RATINGS else "",
        "notes": _clean_list(raw.get("notes"), 10),
        "seconds": seconds,
        "language": (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE,
        "usage": usage.as_dict(),
    }
    script["text"] = script_to_text(script)
    script["spoken_words"] = spoken_words(script)
    # Advisory, and shown next to the target so an over-long script is visible
    # BEFORE it gets recorded rather than after.
    script["estimated_seconds"] = round(script["spoken_words"] / WORDS_PER_MINUTE * 60)

    logger.info(
        "[script] %r — %d scene(s), %d beat(s), %d cast, %d spoken words "
        "(~%ds against %ds asked) in %s — %s",
        script["title"], len(script["scenes"]),
        sum(len(s["beats"]) for s in script["scenes"]), len(script["characters"]),
        script["spoken_words"], script["estimated_seconds"], seconds,
        script["language"], describe(usage),
    )
    return script
