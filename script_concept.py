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
import re

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


# ---------------------------------------------------------------------------
# Short-form: the one place where scene ORDER is not a matter of taste
# ---------------------------------------------------------------------------
# ⚠ FOUND IN TESTING, ON A REAL BRIEF. Asked for "30 sec viral shots/reel
# script" for Ganesh Chaturthi, the concept came back opening on a close-up of
# hands painting an idol's eyes and saving the finished, blazing idol for scene
# SEVEN. Beautiful, and wrong for the thing that was asked for: in a feed, the
# first frame is what decides whether anyone sees the second one, and the best
# image arriving at second 26 arrives for nobody.
#
# ⚠ AND THE MODEL CANNOT BE EXPECTED TO NOTICE ON ITS OWN. "Reel" tells it the
# LENGTH; nothing in the general instruction tells it that length changes the
# ORDER. So the word is spotted here, in plain Python, and the rule is stated
# outright rather than hoped for.
#
# "short film" is deliberately EXCLUDED — a short film is five to twenty minutes
# and opens however it likes. It is the word "short" doing double duty, and
# treating those two the same would put a hook rule on a narrative film.
_SHORT_FORM_RE = re.compile(
    r"\b(reels?|shorts|tik\s*tok|viral|instagram|insta|snapchat|"
    r"scroll[- ]?stopping|short[- ]?form|youtube\s+short)\b",
    re.IGNORECASE,
)
_NOT_SHORT_FORM_RE = re.compile(r"\bshort\s+(film|movie|story)\b", re.IGNORECASE)


def is_short_form(text: str) -> bool:
    """Does this brief describe something that plays in a FEED?

    Keywords only, and deliberately not the aspect ratio: 9:16 is what the user
    picked for a frame, and inferring intent from it would apply a hook rule to
    a vertical film that never asked for one. What they typed is evidence; what
    they picked on a chip row is not.
    """
    body = (text or "")
    if _NOT_SHORT_FORM_RE.search(body):
        return False
    return bool(_SHORT_FORM_RE.search(body))


_SHORT_FORM_RULE = (
    "⚠ THIS IS SHORT-FORM — a reel, a short, something that plays inside a "
    "feed. That changes the ORDER of the scenes, not just the length:\n"
    "- THE FIRST KEY SCENE IS THE HOOK. It must be the single strongest, most "
    "striking image in the whole film. The finished thing, the transformation, "
    "the face, the moment everything is building to. NOT the preparation, NOT "
    "an establishing shot, NOT a slow build.\n"
    "- Do not save the best image for the end. In a feed there is no end for "
    "somebody who scrolled away in the first second.\n"
    "- ⚠ BUT THE HOOK MUST BE OF THIS FILM. Open on its best real moment, "
    "never on something unrelated and eye-catching. A hook that lies is worse "
    "than a slow open.\n"
    "- After the hook, the rest still has to earn it: build back through the "
    "story and land the ending.\n"
    "- Keep the scenes short and plentiful — this cuts every second or two, so "
    "lean towards the upper end of the scene count."
)


# ---------------------------------------------------------------------------
# A tight runtime is not a feed, and it gets its own, smaller rule
# ---------------------------------------------------------------------------
# ⚠ THE SAME GANESH BRIEF, THE OTHER FAULT. Forty seconds, six key scenes, and
# scene one was a child laying marigolds around an empty puja stall. There is
# nothing wrong with the image — it is simply the run-up, and forty seconds
# does not have a run-up in it. `is_short_form()` never fired, because the
# brief said none of reel / shorts / viral, so nothing in the prompt knew the
# film was short at all.
#
# ⚠ THIS IS NOT THE HOOK RULE AND MUST NOT GROW INTO IT. Short-form REORDERS
# the film — strongest image first, whatever that costs the build — because in
# a feed there is no second chance. A forty-second film still tells its story
# in order; it just cannot afford a warm-up before the story starts. So this
# rule moves the OPENING only and leaves the rest of the arc alone. The two are
# mutually exclusive in `develop()`: the feed rule is the stronger claim.
#
# ⚠ AND THE TRIGGER IS AGAIN WHAT THE USER TYPED — plus the default we would
# use had they typed nothing, which for both kinds is already under a minute.
# Never the aspect chip, for the reason `is_short_form()`'s comment gives.
TIGHT_RUNTIME_SECONDS = 90

_DURATION_RE = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*(seconds?|secs?|s\b|minutes?|mins?|m\b)",
    re.IGNORECASE,
)


def stated_seconds(text: str) -> int:
    """The runtime the user asked for in their own words, or 0 for none.

    ⚠ The SMALLEST plausible duration wins, not the first one read. A brief
    saying "30 second ad for our 5 minute onboarding call" is a thirty-second
    film; taking the first match left to right would make it a five-minute one
    and switch this rule off on exactly the brief that needs it.
    """
    best = 0
    for value, unit in _DURATION_RE.findall(text or ""):
        try:
            n = float(value)
        except ValueError:
            continue
        secs = int(round(n * 60)) if unit.lower().startswith("m") else int(round(n))
        if secs < MIN_SECONDS or secs > MAX_SECONDS:
            continue
        if best == 0 or secs < best:
            best = secs
    return best


_TIGHT_RUNTIME_RULE = (
    "⚠ THE RUNTIME IS TIGHT — about {seconds} seconds. There is not enough "
    "film here for a warm-up:\n"
    "- THE FIRST KEY SCENE IS ALREADY THE FILM, not the run-up to it. "
    "Preparing, arriving, setting out the things that will matter later — "
    "those are the second scene at the earliest.\n"
    "- If your opening scene exists mainly to lead into a stronger moment "
    "further down the list, OPEN ON THE STRONGER MOMENT instead.\n"
    "- ⚠ The order of the REST of the film does not change. This is not a feed "
    "and you are not reordering the story — it still builds and it still lands "
    "its ending. Only the opening moves.\n"
    "- One exception, and only one: when the build IS the point of the film, "
    "keep it, and make the opening the most striking part of that build."
)


# ---------------------------------------------------------------------------
# The arc that was written and the arc that was shown were not the same arc
# ---------------------------------------------------------------------------
# ⚠ FOUND IN TESTING, ON A HINGLISH GANESH CHATURTHI BRIEF. The story
# direction read "… -> Bhaavnaatmak Visarjan -> Aashirwad bana rehta hai" and
# the six key scenes stopped at the visarjan. The RESOLUTION — the blessing
# that stays behind after the idol has gone, which is the entire reason the
# film is warm and not sad — was written into the direction and then never
# given a scene. The film would have ended on the loss.
#
# ⚠ AND NOTHING CAUGHT IT, because the two fields were only ever asked for
# separately. The instruction below says what a story_direction is and what a
# key_scene is; until this block it never once said the second has to COVER
# the first. Six beats, six scenes — and the counts matching is precisely what
# hid a beat falling off the end.
#
# The same concept showed the other half of this: a film entirely about Ganesh
# ji in which the idol is carried, touched and prayed to — and never once seen
# on its own. A subject that only ever appears incidentally is never shown.
#
# ⚠ AND THE THIRD BULLET CAME FROM THE RE-RUN OF THE VERY SAME BRIEF. The
# first three rules held — the arc landed, every beat had a scene, Ganesh ji
# finally filled a frame — and the visarjan had quietly gone. The film was now
# anticipation, arrival, devotion, shared joy, blessing: five pleasant beats
# and nothing that costs anything. ⚠ **A concept gets SHORTER by dropping the
# hard beat, because the hard beat is the one that is least comfortable to
# keep** — and it is the only one an audience actually feels. The user's own
# words: "jo part zaroori hai, emotion yahi sab dekhne se aata hai."
_SCENE_LIST_RULES = (
    "WHAT MAKES THE SCENE LIST RIGHT\n"
    "- ⚠ THE LAST KEY SCENE IS THE LAST BEAT OF THE STORY DIRECTION. Whatever "
    "the arrow chain ends on, the scene list has to END on it too. A direction "
    "reading '… -> the goodbye -> what stays behind' whose scenes stop at the "
    "goodbye has thrown the resolution away: the film ends on the loss and the "
    "point of it is never seen. Read your own story_direction back before you "
    "answer and check that its final beat is on screen.\n"
    "- COVER EVERY BEAT, NOT ONLY THE ENDING. Every step of the arrow chain "
    "needs at least one scene of its own. If a beat has no scene, either give "
    "it one or take that beat out of the direction — the two fields describe "
    "one film and must not disagree.\n"
    "- ⚠ SHOW WHAT THE FILM IS ABOUT, ALONE, AT LEAST ONCE. If it is about a "
    "product, a place, a person or an object, one scene must be that subject "
    "clearly seen and nothing else — not held, not touched, not glimpsed past "
    "a shoulder, not behind a wider moment. A film whose subject only ever "
    "appears incidentally never actually shows it.\n"
    "- ⚠ DO NOT SMOOTH THE HARD BEAT AWAY. Where a story has a difficult "
    "moment — a goodbye, a letting go, a loss, the thing that costs something "
    "— that moment is where the film's feeling comes from, and a scene list "
    "made only of pleasant ones has nothing in it to feel. Keep it, put it "
    "late, and let the resolution land after it rather than instead of "
    "it.\n\n"
)


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
    "the goal, the length, the tone, the setting, the characters they named — "
    "and THE EVENTS THEY NAMED. Never quietly improve, replace or contradict "
    "any of it.\n"
    "- ⚠ A MOMENT THEY ASKED FOR IS NOT OPTIONAL, AND DROPPING ONE IS THE "
    "EASIEST MISTAKE HERE TO MAKE — it does not feel like contradicting them, "
    "it feels like tightening. If their material names a specific event — a "
    "farewell, a first day, a visarjan, an unboxing — it gets a beat in the "
    "story direction AND a scene of its own. Make the film shorter by making "
    "scenes tighter, never by deleting one of theirs.\n"
    "- If they gave an IDEA, their premise stays the premise. You are "
    "developing THEIR story, not swapping it for a better one you thought of.\n"
    "- Invent only what is missing and genuinely needed to make it filmable.\n\n"

    + _SCENE_LIST_RULES

    + "THE FIELDS\n"
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

    # ⚠ LAST, AND ON PURPOSE. When it applies, this rule OVERRULES the natural
    # order of a story, and the final thing a model reads before answering is
    # the rule it holds to most reliably — the same reason
    # `plan_agent.write_script` puts the language block at the end.
    short_form = is_short_form(body)
    # ⚠ MUTUALLY EXCLUSIVE, AND THE FEED RULE WINS. Both move the opening; the
    # short-form one also reorders everything behind it, and stacking "only the
    # opening moves" underneath "put the best image first" is two instructions
    # arguing with each other in front of the model.
    planned = stated_seconds(body) or DEFAULT_SECONDS[kind]
    tight = not short_form and planned <= TIGHT_RUNTIME_SECONDS
    if tight:
        ask += ["", _TIGHT_RUNTIME_RULE.format(seconds=planned)]
    if short_form:
        ask += ["", _SHORT_FORM_RULE]

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
        "[concept] %r from %d chars of %s%s, %d scene(s), %ds — %s",
        concept["title"], len(body), kind,
        " (short-form)" if short_form
        else (" (tight %ds)" % planned if tight else ""),
        len(concept["key_scenes"]), concept["duration_seconds"], describe(usage),
    )
    return {"concept": concept, "usage": usage.as_dict()}


# The arrow chain the concept card shows, split back into its beats. Covers
# every arrow the model has actually written here, the unicode ones included.
_ARROW_RE = re.compile("\\s*(?:-+>|=+>|\u2192|\u2013>|\u2014>)\\s*")


def final_beat(story_direction: str) -> str:
    """The last step of the arc, or "" when there is no chain to read.

    ⚠ THIS IS THE DETERMINISTIC HALF OF THE ENDING FIX. `_SCENE_LIST_RULES`
    ASKS the model to put the final beat on the card, and a prompt is a
    request. This reads the beat straight out of the approved text and hands it
    to the writer as a requirement, so the film lands its ending even when the
    card the user approved never grew a scene for it.
    """
    beats = [b.strip() for b in _ARROW_RE.split(story_direction or "") if b.strip()]
    return beats[-1] if len(beats) >= 2 else ""


# Words that say nothing about whether a beat is already on the list. The
# Hinglish ones are here because the concept is written in the user's own
# language, and "hai" / "ka" / "ke" appear in very nearly every line of it.
_STOPWORDS = frozenset(
    """a an the and or of in on at to for with from by is are was were be been
    it its this that these those he she they we you as but so if then than
    hai hain ho hota hoti hote ka ki ke ko se me mein aur ya par jo wo ye yeh
    ek bhi hi na nahi kar karta karte karti raha rahi rahe""".split()
)


def _covered_by(beat: str, scenes: list) -> bool:
    """Is this closing beat already somewhere at the end of the scene list?

    ⚠ DELIBERATELY GENEROUS, and that direction is the safe one. A false
    "covered" costs one line of instruction the model would most likely have
    followed anyway; a false "missing" appends a demand to a scene list that
    already ends correctly, and a brief arguing with itself is the worse
    failure. Only the last two scenes are read — a beat mentioned in the middle
    of the film is not the same as a film that ends on it.
    """
    words = {w for w in re.findall("\\w+", (beat or "").lower())
             if len(w) > 2 and w not in _STOPWORDS}
    if not words:
        return True
    for scene in list(scenes)[-2:]:
        if words & set(re.findall("\\w+", (scene or "").lower())):
            return True
    return False


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

    # ⚠ THE LAST BEAT OF THE ARC IS STATED SEPARATELY, BECAUSE IT IS THE ONE
    # THE SCENE LIST DROPS. Reported on a Ganesh Chaturthi concept whose
    # direction ended "-> Aashirwad bana rehta hai" while its scenes ended at
    # the visarjan: the resolution was there in the approved text and would
    # have been filmed by nobody. A scene list is a list of moments; the arc
    # says where the film STOPS, and those are not the same claim.
    ending = final_beat(direction)
    if ending and not _covered_by(ending, scenes):
        lines += [
            "",
            f"⚠ THE FILM ENDS ON THIS, and the ending is not optional: {ending}",
            "The key scenes above stop before it. Write that closing beat "
            "anyway — the last thing on screen is the last step of the STORY "
            "DIRECTION, not the last key scene.",
        ]

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
