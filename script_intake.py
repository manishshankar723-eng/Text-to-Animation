"""
script_intake.py — WHAT DID THE USER ACTUALLY GIVE US?

Stage 0 of Script → Storyboard, in front of `script_breakdown.py`.

Why it exists
-------------
The form takes one box of text and, until this module, sent every one of them
into the breakdown as though it were a script. That is right for a script and
quietly wrong for everything else: a single line —

    "A man wakes up and discovers everyone in the city has disappeared."

— came back as a twenty-panel film with characters, locations and dialogue
nobody wrote, all of it invented, all of it paid for, and none of it shown to
the user before the images were drawn.

⚠ **THE USER MUST NOT BE ASKED TO CLASSIFY THEIR OWN INPUT.** A "Script or
Idea?" toggle looks like it solves this and does not: most people cannot say
whether what they pasted is technically a brief or a concept, and being made to
choose is the friction the one-box form exists to remove. The product promise is
"give us whatever you have" — so working out what it is, is OUR job.

Two readers, cheapest first
---------------------------
    sniff(text)     Pure Python, no model, no cost, no latency. Recognises the
                    thing we can recognise for certain — a slug line, a run of
                    'NAME: line' speech — and says nothing at all when unsure.

    classify(text)  One small structured model call, and ONLY when sniff shrugs.

`intake()` runs them in that order. Most boards start from a real script, and a
real script never reaches the model here — it goes straight to the breakdown,
same as before this module existed.

⚠ **THE TIE GOES TO `idea`, NEVER TO `script`.** The two mistakes are not
equally bad. Calling an idea a script makes the app invent a whole film in
silence and charge for the panels — the exact bug this fixes. Calling a script
an idea costs one extra confirmation click. The system instruction says so in as
many words, because a model asked to "classify" with no stated asymmetry will
split the difference.

What this module does NOT do
----------------------------
It does not write, expand, rewrite, summarise or judge anything. It reads the
text and names it. Developing an idea into a concept and getting that approved
is the next stage, and turning an approved concept into a real script is
`plan_agent.write_script()`, which already exists and already emits the exact
layout `script_breakdown.py` reads.

Backend, retries and token accounting are `plan_agent`'s, imported rather than
re-implemented — same reason as `script_agent.py`, which is this module's
sibling in every respect.

Spends TEXT quota only, and often none at all.
"""

import json
import logging
import re

from google.genai import types

from ai_usage import describe, merge
from plan_agent import PlanError, _call, _to_contents
from script_breakdown import _sampling_kwargs

logger = logging.getLogger(__name__)


class ScriptIntakeError(PlanError):
    """Raised when the text can't be read at all. Carries a readable reason."""


# The five answers, and what each one means to the caller:
#
#   script  — a shootable sequence. Straight to the breakdown, untouched.
#   brief   — requirements for a film that does not exist yet (product,
#             audience, duration, tone). Needs a concept, and approval.
#   idea    — a premise or a story told in prose. Needs a concept, and approval.
#   vague   — a wish with no subject. Needs ONE question answered.
#   empty   — nothing was typed. Free; the model never sees this one.
KINDS = ("script", "brief", "idea", "vague", "empty")

# How much of the text the model reads. Classification needs the opening, not
# the whole film — and anything long enough for this cap to bite has almost
# certainly been recognised by `sniff` already and never got here.
MAX_INTAKE_CHARS = 6000


# ---------------------------------------------------------------------------
# The free reader
# ---------------------------------------------------------------------------
# A scene heading: "INT. COFFEE SHOP - NIGHT", "EXT. STREET - DAWN", and our own
# writer's "SCENE 1. INT. WATCH SHOP - NIGHT". The trailing [./] is what keeps
# "Internal review" and "Interesting:" out of it.
_SLUG_RE = re.compile(
    r"^[ \t]*(?:SCENE[ \t]+\d+[.:]?[ \t]*)?(?:INT|EXT|INT[.]?/EXT)[./]",
    re.IGNORECASE | re.MULTILINE,
)

# Speech: "MEERA: You said it would be ready.", "ARJUN (V.O.): Some things…".
_SPEECH_RE = re.compile(
    r"^[ \t]*([A-Z][A-Z0-9'’\-. ]{0,28}?)[ \t]*(?:\([^)\n]{1,24}\))?[ \t]*:[ \t]+\S",
    re.MULTILINE,
)

# ⚠ A BRIEF IS ALSO WRITTEN IN "LABEL: value" LINES, and without this list a
# three-line client brief reads as three lines of dialogue and gets fast-pathed
# into the breakdown as a script — the precise failure this module exists to
# stop. These are the labels a brief uses and a character is never called.
_NOT_SPEAKERS = {
    "AUDIENCE", "BRAND", "BRIEF", "BUDGET", "CTA", "CALL TO ACTION", "CLIENT",
    "CONCEPT", "DELIVERABLE", "DELIVERABLES", "DURATION", "FORMAT", "GOAL",
    "GOALS", "IDEA", "KEY MESSAGE", "LENGTH", "MESSAGE", "MOOD", "NOTE",
    "NOTES", "OBJECTIVE", "OUTPUT", "PLATFORM", "PREMISE", "PRODUCT",
    "REFERENCE", "REFERENCES", "REQUIREMENT", "REQUIREMENTS", "SUMMARY",
    "TARGET", "TARGET AUDIENCE", "TASK", "TONE", "USP", "VISUAL STYLE",
}

# Below this a text is too short to be recognised as anything with confidence,
# however script-shaped one of its lines looks. Two words and a colon is not a
# screenplay.
_MIN_SCRIPT_WORDS = 30
# Speech alone (no scene headings) has to clear a higher bar, because "LABEL:
# value" lines are what a brief looks like too. ⚠ 40 and not 60: a 20-second ad
# script — the single most common thing this product is pointed at — is about
# forty-five words of voiceover with no scene headings anywhere, and a sniff
# that misses it sends every one of them to the model for no reason.
_MIN_SPEECH_LINES = 3
_MIN_SPEECH_WORDS = 40


def _speech_lines(text: str) -> int:
    """Count lines that are really dialogue, not a brief's labels."""
    hits = 0
    for m in _SPEECH_RE.finditer(text):
        name = m.group(1).strip().rstrip(".").strip()
        if name and name.upper() not in _NOT_SPEAKERS:
            hits += 1
    return hits


def sniff(text: str) -> str | None:
    """What we can tell for free, or None when a model has to look.

    Returns "empty" for nothing at all, "script" when the layout says so
    beyond reasonable doubt, and None for everything else — including plenty of
    real scripts. ⚠ **None is the safe answer and silence is cheap**: it costs
    one small model call. A wrong "script" costs a whole invented film.
    """
    body = (text or "").strip()
    if not body:
        return "empty"

    words = len(body.split())
    if words < _MIN_SCRIPT_WORDS:
        # Short enough that the model should read it — a one-line idea and a
        # one-line scene description are told apart by meaning, not by layout.
        return None

    if _SLUG_RE.search(body):
        return "script"
    if _speech_lines(body) >= _MIN_SPEECH_LINES and words >= _MIN_SPEECH_WORDS:
        return "script"
    return None


# ---------------------------------------------------------------------------
# The model reader
# ---------------------------------------------------------------------------
_SYSTEM_INSTRUCTION = (
    "You are the intake reader for Aniwala AI Studio's 'Script to Storyboard' "
    "page. A person has pasted ONE piece of text into a box and pressed Create "
    "Storyboard. Your only job is to say WHAT THAT TEXT IS.\n\n"

    "⚠ YOU DO NOT WRITE, EXPAND, REWRITE, SUMMARISE, IMPROVE, JUDGE OR "
    "CONTINUE THE TEXT. You read it and name it. Nothing you return is ever "
    "shown as creative work.\n\n"

    "THE FIVE ANSWERS\n"
    "- script: a shootable sequence. Scenes, action lines, dialogue, or beats "
    "laid out in order — something a camera could follow line by line. It "
    "counts even when short, unformatted, or missing scene headings, as long as "
    "the events are actually WRITTEN OUT in order rather than described.\n"
    "- brief: requirements for a film that does not exist yet. Talks about the "
    "product, the audience, the goal, the duration or the tone, and asks for a "
    "video — but tells no story. 'Create a 30 second ad for an AI meeting "
    "assistant, audience is busy professionals, feel premium.'\n"
    "- idea: a story told in prose rather than written as a script. A premise, "
    "a logline, a paragraph of what happens, or one described scene. 'A man "
    "wakes up and discovers everyone in the city has disappeared.'\n"
    "- vague: a wish with no subject. There is not enough here to make anything "
    "specific. 'I want to make something emotional.' 'Make me a video.'\n"
    "- empty: nothing usable at all.\n\n"

    "⚠ THE MOST IMPORTANT RULE — WHEN YOU ARE UNSURE BETWEEN script AND "
    "ANYTHING ELSE, ANSWER idea (or brief). These two mistakes do NOT cost the "
    "same. Calling an idea a 'script' makes this app invent an entire film — "
    "characters, locations and dialogue nobody wrote — and draw it without ever "
    "asking. Calling a script an 'idea' costs the user one extra click of "
    "confirmation. Choose the cheap mistake.\n"
    "Say 'script' only when the events are written out. Prose ABOUT a story is "
    "an idea, however long and however detailed it is.\n\n"

    "WHAT TO PUT IN EACH FIELD\n"
    "- kind: exactly one of script, brief, idea, vague, empty.\n"
    "- reason: ONE short sentence saying what you saw, addressed to the user "
    "('This is a premise, not a written scene.'). Never an apology, never "
    "advice, never a compliment. Leave it empty when kind is 'script' — nobody "
    "needs to be told their script is a script.\n"
    "- question: ONE question, and only when kind is 'vague'. The single most "
    "useful thing to know next. Empty for every other kind. Never a list, never "
    "two questions joined by 'and'.\n\n"

    "LANGUAGE\n"
    "Write reason and question in the SAME language and the same script the "
    "user wrote in. If they wrote Hinglish (Hindi in Latin letters), answer in "
    "Hinglish in Latin letters — do not switch to Devanagari and do not switch "
    "to English. Plain text only: no markdown, no bold, no quotes around the "
    "whole sentence."
)

# ⚠ THE ASSISTANT MUST NOT INTRODUCE ITSELF BY A NAME THE APP NO LONGER USES.
# The product is renameable from the admin panel now (`server/branding.py`), and
# this brief tells the model where it is standing — so an owner who renames the
# app and then asks the assistant "what are you?" would otherwise be told the old
# name, in their own product, by their own product.
#
# ⚠ A REPLACE, NOT A `format()`. The brief is full of literal braces (JSON
# examples, shot templates), so a format string would either blow up or need
# every one of them doubled — one escaping mistake away from a mangled prompt.
#
# ⚠ AND IT STAYS A PLAIN STRING CONSTANT ABOVE. The prompt checks in `tests/`
# assert on phrases inside `_SYSTEM_INSTRUCTION` directly, and a brief that could
# only be read by calling something would put those out of reach.
_BUILT_IN_APP = "Aniwala AI Studio"


def _system_instruction() -> str:
    """The brief, wearing whatever the app is currently CALLED.

    Falls back to the built-in name on any failure — a naming lookup must never
    be the reason a chat turn fails.
    """
    try:
        from server import branding

        name = branding.get_branding().get("name") or _BUILT_IN_APP
    except Exception:  # noqa: BLE001 — cosmetic; see the docstring
        return _SYSTEM_INSTRUCTION
    if name == _BUILT_IN_APP:
        return _SYSTEM_INSTRUCTION
    return _SYSTEM_INSTRUCTION.replace(_BUILT_IN_APP, name)



def _schema() -> types.Schema:
    """`{kind, reason, question}` — three short strings and nothing else.

    Deliberately tiny. This call runs in front of EVERY storyboard that isn't
    recognised for free, so it is sized to be a fast read rather than a piece of
    work: no concept, no outline, no rewrite. Developing the idea comes later,
    once the user has seen what we think they gave us.
    """
    return types.Schema(
        type=types.Type.OBJECT,
        required=["kind"],
        properties={
            "kind": types.Schema(
                type=types.Type.STRING,
                enum=list(KINDS),
                description="What the pasted text is.",
            ),
            "reason": types.Schema(
                type=types.Type.STRING,
                description=(
                    "One short sentence, in the user's own language, saying "
                    "what you saw. Empty when kind is 'script'."
                ),
            ),
            "question": types.Schema(
                type=types.Type.STRING,
                description=(
                    "One question, in the user's own language, and ONLY when "
                    "kind is 'vague'. Empty otherwise."
                ),
            ),
        },
    )


def classify(text: str) -> dict:
    """Read the text with the model. Prefer `intake()`, which is free first.

    Returns {"kind", "reason", "question", "decided_by": "model", "usage"}.
    """
    body = (text or "").strip()
    if not body:
        return {
            "kind": "empty",
            "reason": "",
            "question": "",
            "decided_by": "sniff",
            "usage": {},
        }

    clipped = body[:MAX_INTAKE_CHARS]
    prompt = (
        "Read the text below and say what it is.\n\n"
        "--- THE USER'S TEXT ---\n"
        + clipped
        + ("\n… (truncated)" if len(body) > MAX_INTAKE_CHARS else "")
        + "\n--- END ---"
    )

    config = types.GenerateContentConfig(
        system_instruction=_system_instruction(),
        response_mime_type="application/json",
        response_schema=_schema(),
        **_sampling_kwargs(),
    )
    spent: list = []
    payload = _call(
        _to_contents([{"role": "user", "text": prompt}]),
        config,
        "reading what the user pasted",
        spent,
    )
    usage = merge(*spent)

    try:
        raw = json.loads(payload) or {}
    except json.JSONDecodeError:
        # ⚠ FAIL TOWARDS THE SAFE ANSWER, NOT TOWARDS THE OLD BEHAVIOUR. A
        # broken wrapper tells us nothing about the text, and "assume script"
        # is the assumption that invents films. One confirmation click is the
        # correct cost of not knowing.
        logger.warning("[intake] reply wasn't valid JSON; treating the text as an idea")
        raw = {}

    kind = str(raw.get("kind", "") or "").strip().lower()
    if kind not in KINDS:
        kind = "idea"
    reason = str(raw.get("reason", "") or "").strip()
    question = str(raw.get("question", "") or "").strip()

    # The schema allows these; the product does not. A "reason" under a script
    # is a critique nobody asked for, and a question under anything else is a
    # dead end with no answer box beside it.
    if kind == "script":
        reason = ""
    if kind != "vague":
        question = ""

    logger.info(
        "[intake] %d chars read as %r — %s", len(body), kind, describe(usage)
    )
    return {
        "kind": kind,
        "reason": reason,
        "question": question,
        "decided_by": "model",
        "usage": usage.as_dict(),
    }


def intake(text: str) -> dict:
    """What is this text? Free reader first, model only when it shrugs.

    Returns {"kind", "reason", "question", "decided_by", "usage"} where
    `decided_by` is "sniff" (no model, no cost) or "model".
    """
    free = sniff(text)
    if free:
        logger.info("[intake] %r decided for free", free)
        return {
            "kind": free,
            "reason": "",
            "question": "",
            "decided_by": "sniff",
            "usage": {},
        }
    return classify(text)
