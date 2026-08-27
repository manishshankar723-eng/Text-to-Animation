"""
script_concept.py — WHAT WE THINK YOU MEANT, SHOWN BEFORE WE DRAW IT.

Stage 0b of Script → Storyboard, between `script_intake.py` (which names the
paste) and `plan_agent.write_script()` (which writes the real script).

Why it exists
-------------
`script_intake` can now tell a brief or an idea from a script. Knowing is not
enough on its own: something still has to turn

    "Create a 30 second ad for an AI meeting assistant. Audience is busy
     professionals. Show how it saves time after meetings. Feel premium."

into a film, and that turning is INVENTION. Somebody decides who the person on
screen is, what goes wrong, what the ending is. Before this module the app made
those decisions in silence and the user first met them as twenty finished
drawings they had already paid for.

⚠ **THE APPROVAL GATE IS THE PRODUCT DECISION HERE, NOT THE PROMPT.** A concept
is thirty seconds of reading and one click. A wrong storyboard is twenty images,
several minutes and real money. So: a brief or an idea gets a concept, the
concept gets shown, and NOTHING is drawn until the user says yes.

⚠ **AND A SCRIPT NEVER COMES THROUGH HERE AT ALL.** When the user already wrote
the thing, there is nothing to interpret and nothing to approve — asking them to
confirm our reading of their own script would be a step that exists only to
annoy. `script_intake` routes those straight to the breakdown.

Two functions, and the second one is the important one
------------------------------------------------------
    develop(text, kind)         brief/idea → a Concept: title, premise, story
                                direction, key scenes, duration, look.

    concept_to_brief(concept)   The approved (and possibly EDITED) concept,
                                flattened into the brief that
                                `plan_agent.write_script()` takes.

⚠ **THE CONCEPT IS NOT THE SCRIPT, AND THIS MODULE MUST NOT WRITE ONE.** The
storyboard's whole traceability layer — the review step, `ScriptPanel`, every
shot card's "FROM YOUR SCRIPT · LINE 12", `_attach_script_lines()` — rests on a
real script existing. A concept that jumped straight to shots would leave all of
it pointing at nothing. So the approved concept goes through
`plan_agent.write_script()`, which already emits the exact layout
`script_breakdown.py` reads, and the board is built from THAT.

Backend, retries and token accounting are `plan_agent`'s, imported rather than
re-implemented — same as `script_agent.py` and `script_intake.py`.

Spends TEXT quota only.
"""

import json
import logging

from google.genai import types

from ai_usage import describe, merge
from plan_agent import PlanError, _call, _to_contents
from script_breakdown import _sampling_kwargs

logger = logging.getLogger(__name__)


class ScriptConceptError(PlanError):
    """Raised when a concept can't be developed. Carries a readable reason."""


# The concept is shown on one screen and read in about thirty seconds. These
# bounds are what keep it that way — a "concept" that runs to two pages is a
# treatment, and nobody approves a treatment on the way to a storyboard.
MAX_SOURCE_CHARS = 8000
MAX_KEY_SCENES = 8
MIN_KEY_SCENES = 3

# What the user gets if the text names no length. A brief is nearly always an
# ad; an idea is nearly always a short. ⚠ These are only ever a DEFAULT: the
# number is on the concept card and the user can change it before approving.
DEFAULT_SECONDS = {"brief": 30, "idea": 60}
MIN_SECONDS = 10
MAX_SECONDS = 600


_SYSTEM_INSTRUCTION = (
    "You are the concept developer inside Aniwala AI Studio's 'Script to "
    "Storyboard' page. The user handed over something that is NOT a script — a "
    "brief, or an idea — and pressed Create Storyboard. Your job is to work out "
    "what film that should become, and to show it to them as ONE concept they "
    "can read in half a minute and approve.\n\n"

    "⚠ YOU DO NOT WRITE THE SCRIPT. No dialogue. No scene headings. No shot "
    "list. No camera directions. What you produce is the DIRECTION the film "
    "will take; a different step writes the script from it once the user has "
    "said yes.\n\n"

    "⚠ ONE CONCEPT, NOT A MENU. Do not offer alternatives, options A/B/C, or "
    "'you could also…'. Commit to the strongest reading of what they gave you. "
    "They can edit every field on screen before approving, so a clear, specific "
    "wrong guess is more useful to them than a vague safe one.\n\n"

    "WHAT YOU MUST NOT OVERRIDE\n"
    "- Everything the user actually stated is FIXED: the product, the audience, "
    "the goal, the length, the tone, the setting, the characters they named. "
    "Never quietly improve, replace or contradict any of it.\n"
    "- If they gave an IDEA, their premise stays the premise. You are "
    "developing THEIR story, not swapping it for a better one you thought of.\n"
    "- Invent only what is missing and genuinely needed to make it filmable.\n\n"

    "THE FIELDS\n"
    "- title: a few words. A name for the film, not a description of it.\n"
    "- premise: one or two sentences — the core idea, in plain words.\n"
    "- story_direction: the shape of the film as a short arrow chain, e.g. "
    "'Chaotic meeting ends -> notes pile up -> AI writes the summary -> "
    "organised output -> she leaves on time'. Five or six steps at most.\n"
    "- key_scenes: three to eight lines, in order. Each one is a MOMENT that "
    "can be seen — 'She closes the laptop and the room is already dark', not "
    "'establish her frustration'. No dialogue, no shot types.\n"
    "- duration_seconds: how long the film runs. Use the number the user gave "
    "if they gave one. Otherwise choose something sensible for what this is.\n"
    "- visual_direction: a short phrase for the look and feel — 'premium, "
    "modern, uncluttered'. Not a paragraph, and never a camera brand or lens.\n\n"

    "LANGUAGE\n"
    "Write every field in the SAME language and the same script the user wrote "
    "in. If they wrote Hinglish (Hindi in Latin letters), answer in Hinglish in "
    "Latin letters — do not switch to Devanagari and do not switch to English. "
    "Plain text only: no markdown, no bullets, no bold."
)


def _schema() -> types.Schema:
    """The concept, as the card on screen shows it."""
    return types.Schema(
        type=types.Type.OBJECT,
        required=["title", "premise", "key_scenes"],
        properties={
            "title": types.Schema(
                type=types.Type.STRING,
                description="A few words naming the film.",
            ),
            "premise": types.Schema(
                type=types.Type.STRING,
                description="One or two sentences: the core idea.",
            ),
            "story_direction": types.Schema(
                type=types.Type.STRING,
                description="The arc as a short arrow chain, five or six steps.",
            ),
            "key_scenes": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="Three to eight visible moments, in order.",
            ),
            "duration_seconds": types.Schema(
                type=types.Type.INTEGER,
                description="How long the film runs, in seconds.",
            ),
            "visual_direction": types.Schema(
                type=types.Type.STRING,
                description="A short phrase for the look and feel.",
            ),
        },
    )


def _form_context(genre: str = "", style: str = "", aspect_ratio: str = "") -> str:
    """What the form already says, so the concept doesn't contradict it.

    The user picked a genre, a visual style and a frame before pressing the
    button. A concept that ignores them produces the same jarring mismatch
    `script_agent.build_context` was written to prevent — a wide, crowded
    opening scene for a board the user set to 9:16.
    """
    lines: list[str] = []
    g = (genre or "").strip()
    if g and g.lower() != "default":
        lines.append(f"- Genre chosen on the form: {g}")
    if (style or "").strip():
        lines.append(f"- Visual style chosen on the form: {style.strip()}")
    ar = (aspect_ratio or "").strip()
    if ar:
        vertical = ar in ("9:16", "4:5")
        lines.append(
            f"- Frame: {ar}"
            + (
                " — vertical and phone-first. Keep the scenes tight: one or two "
                "people, close in. No wide crowd moments."
                if vertical
                else ""
            )
        )
    if not lines:
        return ""
    return (
        "WHAT THE USER ALREADY CHOSE ON THE FORM. Do not contradict any of it, "
        "and do not ask about it:\n" + "\n".join(lines)
    )


def _coerce(raw: dict, kind: str) -> dict:
    """Clean the model's JSON into the concept the card renders."""
    raw = raw if isinstance(raw, dict) else {}

    scenes = []
    for item in raw.get("key_scenes") or []:
        line = str(item or "").strip()
        if line:
            scenes.append(line)
    scenes = scenes[:MAX_KEY_SCENES]

    try:
        seconds = int(raw.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        seconds = DEFAULT_SECONDS.get(kind, 60)
    seconds = max(MIN_SECONDS, min(seconds, MAX_SECONDS))

    return {
        "title": str(raw.get("title", "") or "").strip(),
        "premise": str(raw.get("premise", "") or "").strip(),
        "story_direction": str(raw.get("story_direction", "") or "").strip(),
        "key_scenes": scenes,
        "duration_seconds": seconds,
        "visual_direction": str(raw.get("visual_direction", "") or "").strip(),
    }


def develop(
    text: str,
    kind: str = "idea",
    genre: str = "",
    style: str = "",
    aspect_ratio: str = "",
) -> dict:
    """Turn a brief or an idea into ONE concept for the user to approve.

    Args:
        text: exactly what the user pasted. Never a summary of it.
        kind: "brief" or "idea", from `script_intake`. Only changes the default
            runtime and the wording of the ask.
        genre / style / aspect_ratio: what the form already says.

    Returns:
        {"concept": {...}, "usage": {…}}

    Raises:
        ScriptConceptError: with a readable reason. ⚠ The caller must NOT fall
            back to breaking the raw text down as a script — that is precisely
            the silent invention this stage exists to stop.
    """
    body = (text or "").strip()
    if not body:
        raise ScriptConceptError("There's nothing here yet — describe what you want to make.")

    kind = kind if kind in DEFAULT_SECONDS else "idea"
    clipped = body[:MAX_SOURCE_CHARS]

    ask = [
        "The user gave us this, and it is a "
        + ("client/product BRIEF" if kind == "brief" else "story IDEA")
        + ". Develop it into one concept.",
        "",
        "--- WHAT THE USER WROTE ---",
        clipped + ("\n… (truncated)" if len(body) > MAX_SOURCE_CHARS else ""),
        "--- END ---",
    ]
    context = _form_context(genre, style, aspect_ratio)
    if context:
        ask += ["", context]
    ask += [
        "",
        f"Give between {MIN_KEY_SCENES} and {MAX_KEY_SCENES} key scenes.",
    ]

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=_schema(),
        **_sampling_kwargs(),
    )
    spent: list = []
    payload = _call(
        _to_contents([{"role": "user", "text": "\n".join(ask)}]),
        config,
        "developing the concept",
        spent,
    )
    usage = merge(*spent)

    try:
        raw = json.loads(payload) or {}
    except json.JSONDecodeError as e:
        # ⚠ NO SALVAGE ATTEMPT HERE, unlike script_agent's chat. A half-read
        # concept shown as though it were whole is worse than an error: the
        # user approves it, and the missing half gets invented downstream by
        # the very step this gate exists to supervise.
        logger.warning("[concept] reply wasn't valid JSON: %s", e)
        raise ScriptConceptError(
            "The concept came back unreadable. Try again, or add a line or two "
            "more about what you want."
        )

    concept = _coerce(raw, kind)
    if not concept["premise"] and not concept["key_scenes"]:
        raise ScriptConceptError(
            "We couldn't make a concept out of that. Try describing the story "
            "or the product in a sentence or two more."
        )

    logger.info(
        "[concept] %r from %d chars of %s, %d scene(s), %ds — %s",
        concept["title"], len(body), kind, len(concept["key_scenes"]),
        concept["duration_seconds"], describe(usage),
    )
    return {"concept": concept, "usage": usage.as_dict()}


def concept_to_brief(concept: dict, source: str = "") -> str:
    """The approved concept, written out as the brief `write_script()` takes.

    ⚠ THIS IS THE APPROVED VERSION, WHICH IS NOT NECESSARILY THE ONE WE WROTE.
    Every field on the card is editable, so what arrives here may be the user's
    own title, their own ending, their own scene list. It is treated as the
    instruction, not as a suggestion — see the wording below.

    `source` is what the user originally pasted. It rides along underneath
    because a brief carries details a concept has no field for (a product name,
    a required line, a platform) and losing them between the two steps would
    show up as a script that quietly forgot half the ask.
    """
    concept = concept if isinstance(concept, dict) else {}
    lines: list[str] = [
        "Write this exact film. The concept below was APPROVED by the user, so "
        "follow it — do not replace the story, the ending or the characters "
        "with your own.",
        "",
    ]

    title = str(concept.get("title", "") or "").strip()
    premise = str(concept.get("premise", "") or "").strip()
    direction = str(concept.get("story_direction", "") or "").strip()
    look = str(concept.get("visual_direction", "") or "").strip()
    scenes = [str(s or "").strip() for s in (concept.get("key_scenes") or [])]
    scenes = [s for s in scenes if s]

    if title:
        lines.append(f"TITLE: {title}")
    if premise:
        lines.append(f"PREMISE: {premise}")
    if direction:
        lines.append(f"STORY DIRECTION: {direction}")
    if look:
        lines.append(f"LOOK AND FEEL: {look}")
    if scenes:
        lines.append("KEY SCENES, in this order — cover every one of them:")
        lines.extend(f"  {i}. {s}" for i, s in enumerate(scenes, 1))

    src = (source or "").strip()
    if src:
        lines += [
            "",
            "The user's original words, for the details the concept has no room "
            "for (product names, required lines, platform). Nothing here may "
            "contradict the approved concept above:",
            src[:MAX_SOURCE_CHARS],
        ]

    return "\n".join(lines)


def concept_seconds(concept: dict) -> int:
    """The approved runtime, clamped. Defaults to 60 when the card had none."""
    try:
        seconds = int((concept or {}).get("duration_seconds") or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        seconds = 60
    return max(MIN_SECONDS, min(seconds, MAX_SECONDS))
