"""
director.py — THE BRAIN. A board goes in, an edit plan comes out.

    brief  →  ANALYSE (read the film)  →  POLISH (write the edit)  →  plan

---------------------------------------------------------------------------
⚠ IT WRITES A PLAN. IT DOES NOT EDIT ANYTHING, AND IT CANNOT.
---------------------------------------------------------------------------
Nothing in this file touches a timeline, a clip or a file. It produces the same
`EditPlan` shape `house_style.housePlan` produces — a list of `{verb, args}` —
and that plan then goes through exactly the doors the deterministic one goes
through, on the client, in this order:

    validatePlan  →  applyGuardrails  →  useDirectorRun

which is why Phase 2 is a plan writer and not a rewrite of the runner. Every
safety property Phase 0 established holds here for free: an unknown kind is
dropped, a step over the house cap is trimmed, a verb calls the editor function
a person's own button calls. See `client/src/animatic/agent/`.

⚠ SO THE MODEL IS NOT TRUSTED, AND IT IS NOT ASKED TO BE. What this module adds
on top of that is the checking a language model needs and a rules engine does
not: fold the arguments down to the ones the named verb actually takes, and hold
the words it wrote to the language the project is in. Both are DROPS with a
stated reason, never exceptions — the same rule the client validator states.

---------------------------------------------------------------------------
⚠ TWO CALLS, AND THE FIRST ONE IS NOT ALLOWED TO SEE THE VOCABULARY.
---------------------------------------------------------------------------
The analyse call is handed the film and asked what it is: the logline, the mood,
where one scene ends and the next begins, what each shot DOES. It is given no
verb list, no transition names and no caps, and that omission is the design.
Shown the vocabulary, a model starts planning immediately and reads the film on
the way past — you get thirty dissolves and a summary that would fit any board.

The polish call is handed that reading plus the manifest and writes the steps.
Its transitions land on the scene boundaries the first call found, which is the
thing Phase 0 could only approximate: `house_style` dissolves after a shot held
1.5× the median, and says in its own header that this is a PROXY for "is this a
scene boundary". This is the real question being asked at last.

---------------------------------------------------------------------------
⚠ THE VOCABULARY COMES FROM THE CLIENT, AND THAT IS ON PURPOSE.
---------------------------------------------------------------------------
`capabilities()` in `agent/capabilities.js` derives the manifest from the tables
the RENDERERS read — `TRANSITION_KINDS`, `EFFECT_KINDS`, `SHAPE_KINDS`,
`TEXT_PRESETS`, and now `ACTIONS` itself. That is the only honest answer to "what
can this build do", and it is JavaScript. Rebuilding it here from the Python
twins (`animatic_transitions.py`, `animatic_effects.py`) would produce a second
answer that is right today and wrong the first time a kind is added on one side
— and wrong in the direction that hurts, because the model would keep proposing
what the validator then drops. So the browser sends its manifest with the
request, and this module treats it as the definition of the language.

---------------------------------------------------------------------------
⚠ GREEDY AND SEEDED. Same brief twice, same plan twice.
---------------------------------------------------------------------------
Everything here is deterministic given the model's answers: the brief is built
in a fixed order, the folding is order-preserving, and the sampling is
temperature 0 with a fixed seed (see `llm_json.sampling`). `tests/
director_determinism_check.py` asserts the REQUEST is byte-identical across two
runs, which is the strongest claim anyone can honestly make — no Gemini endpoint
promises bit-exact decoding, so the test never asserts two live calls matched.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import yaml

from llm_json import JsonRequest, LLMJsonError, complete_json

logger = logging.getLogger(__name__)

# Where the three prompt blocks live. Same file, same env var, as every other
# prompt in this app — see the header of the `director:` block in prompts.yaml
# for why an editable prompt file is worth one odd neighbour.
PROMPTS_PATH = os.environ.get("API_CONFIG_PATH", "prompts.yaml")

# How much of a board is worth sending. A 120-shot animatic with a paragraph per
# shot is a large prompt and a slow call, and the tail of it adds nothing the
# model could not infer — but the SHOT COUNT is never truncated, only the prose,
# because a plan written against a board it was shown 60 shots of would put its
# ending in the middle.
MAX_DESCRIPTION_CHARS = 240
MAX_DIALOGUE_CHARS = 400
MAX_BRIEF_CHARS = 2000
# A plan longer than this is not an edit, it is a carpet. The client's fence
# trims by budget anyway; this stops a runaway generation costing tokens.
MAX_STEPS = 240


class DirectorError(Exception):
    """A plan could not be written. Carries a reason written for a human."""


# ---------------------------------------------------------------------------
# LANGUAGE
# ---------------------------------------------------------------------------
# ⚠ THE TABLE IS `plan_agent.LANGUAGES` AND THERE IS NOT A SECOND ONE. Plan &
# Script already solved "what does the user mean by hinglish", including the part
# everybody gets wrong — left to itself a model writes Devanagari and calls it
# Hinglish, when what Indian creators actually publish is Hindi in LATIN script.
# That paragraph is hard-won and it belongs to one table.
#
# What IS new here is the SCRIPT each language is written in, because on-screen
# text is the one place the difference is enforceable rather than advisory: a
# caption in the wrong script is not a stylistic miss, it is a caption the
# audience cannot read, and it is detectable from the codepoints.
SCRIPTS = {
    "english": "latin",
    "hinglish": "latin",
    "hindi": "devanagari",
}

# The codepoint blocks we can actually name. A language whose script is not in
# here is NOT policed — "Tamil" is a legitimate free-text answer (see
# `plan_agent.language_instruction`), and refusing text we cannot classify would
# turn an unknown language into a broken one.
_SCRIPT_RANGES = {
    "devanagari": (0x0900, 0x097F),
    "bengali": (0x0980, 0x09FF),
    "gurmukhi": (0x0A00, 0x0A7F),
    "gujarati": (0x0A80, 0x0AFF),
    "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F),
    "kannada": (0x0C80, 0x0CFF),
    "malayalam": (0x0D00, 0x0D7F),
    "arabic": (0x0600, 0x06FF),
    "hebrew": (0x0590, 0x05FF),
    "cyrillic": (0x0400, 0x04FF),
    "greek": (0x0370, 0x03FF),
    "thai": (0x0E00, 0x0E7F),
    "han": (0x4E00, 0x9FFF),
    "hiragana": (0x3040, 0x309F),
    "katakana": (0x30A0, 0x30FF),
    "hangul": (0xAC00, 0xD7AF),
}


def scripts_in(text: str) -> set[str]:
    """Which named scripts appear in `text`. Latin is implied, never listed."""
    found: set[str] = set()
    for ch in text or "":
        code = ord(ch)
        for name, (low, high) in _SCRIPT_RANGES.items():
            if low <= code <= high:
                found.add(name)
                break
    return found


def script_of(language: str) -> str:
    """The script a language is written in, or "" when we cannot say."""
    return SCRIPTS.get((language or "").strip().lower(), "")


def in_script(text: str, script: str) -> bool:
    """Is `text` written in `script`?

    ⚠ "LATIN" MEANS "NOTHING WE RECOGNISE AS NOT-LATIN", not "only ASCII".
    Accents, punctuation, emoji and digits are all fine in a Latin caption; a
    Devanagari word is not. Asked for a script we do not have a range for, the
    answer is yes — see `_SCRIPT_RANGES` on why an unknown language must not
    become a broken one.
    """
    if not script:
        return True
    present = scripts_in(text)
    if script == "latin":
        return not present
    if script not in _SCRIPT_RANGES:
        return True
    return script in present or not (text or "").strip()


def language_instruction(language: str) -> str:
    """The language block appended to BOTH calls.

    ⚠ IT IS THREE RULES, AND THE THIRD IS THE ONE THAT SURPRISES PEOPLE.

      1. On-screen text is written in the project's language, in that language's
         own script. This is what the audience reads.
      2. Everything the app reads as DATA stays English — verbs, kinds, presets,
         easings. Same exception `plan_agent` carves out for `goal` and `effort`,
         and for the same reason: they are identifiers, not prose.
      3. ⚠ A VEO MOTION PROMPT STAYS IN ENGLISH — the camera and movement
         language ("slow dolly in", "handheld, whip pan left") — while the
         DIALOGUE quoted inside it takes the target language, because that is
         what the model is being asked to have the character SAY. Veo's prompt
         adherence is measurably better in English, and a Hinglish instruction to
         "dheere se camera paas jao" is a worse camera move, not a more
         authentic one. The line is: instructions English, performance local.
    """
    key = (language or "").strip()
    if not key:
        return (
            "LANGUAGE: none was set for this project. Write on-screen text in the "
            "language the dialogue and labels are already in; if that is unclear, "
            "write it in English."
        )

    from plan_agent import LANGUAGES

    described = LANGUAGES.get(key.lower(), key)
    script = script_of(key)
    script_line = ""
    if script == "latin":
        script_line = (
            " Write it in LATIN (Roman) script — a caption in another script is "
            "one this audience cannot read."
        )
    elif script:
        script_line = f" Write it in its own {script.title()} script."

    return (
        f"LANGUAGE OF THIS FILM: {described}\n"
        f"- ON-SCREEN TEXT — titles, cards, labels, anything the audience reads "
        f"in frame — is written in that language.{script_line}\n"
        "- YOUR OWN NOTES, the summary, the mood and every beat description are "
        "for the person editing, and they read English. Write those in English.\n"
        "- ⚠ VEO MOTION PROMPTS ARE WRITTEN IN ENGLISH — camera, movement, pace, "
        "framing — because that is the language those models follow best. The "
        "DIALOGUE quoted inside a motion prompt is the exception and takes the "
        "film's language, exactly as it would be spoken.\n"
        "- Verbs, transition kinds, effect kinds, preset names and easings are "
        "identifiers this app reads as data. They are ALWAYS the English strings "
        "given to you, in every language."
    )


# ---------------------------------------------------------------------------
# SPEECH — what the analyse call is told about the dialogue it is looking at
# ---------------------------------------------------------------------------
def speech_instruction(brief: dict) -> str:
    """Told to the ANALYSE call, and it depends on one thing: is the board silent?

    ⚠ THE SAME FIELD MEANS TWO DIFFERENT THINGS, AND THE PROMPT HAS TO SAY WHICH.
    `shots[].dialogue` in the reading is what feeds two passes downstream — the
    dialogue quoted inside a Veo prompt, and (Phase 3) the SCRIPT the voiceover
    reads aloud. On a board that already has dialogue the only correct value is
    the board's own line, copied: a model asked what is said in a shot that
    already says something will paraphrase it, and a paraphrase of the user's own
    words read aloud in their own film is the worst thing this system can do.

    On a board with NO dialogue anywhere — which is most animatics built from
    uploaded stills, and every board whose breakdown never wrote any — that field
    is the only chance the film has of ever having a voice. So there, and only
    there, the model is asked to WRITE the lines.

    ⚠ AND IT IS ALLOWED TO WRITE NOTHING. A montage, a title sequence, a
    landscape film: inventing chatter for those is worse than silence, and a
    model given a field to fill will fill it unless told it may not.
    """
    speaking = [s for s in (brief.get("shots") or []) if (s.get("dialogue") or "").strip()]
    if speaking:
        return (
            "DIALOGUE: this board already carries its own dialogue, and it is in the "
            "material above.\n"
            "- For every shot that has a line, `dialogue` is that line COPIED EXACTLY. "
            "Do not translate it, tidy it, shorten it or improve it. It is the "
            "writer's own wording and it may be read aloud verbatim.\n"
            "- For a shot with no line, `dialogue` is \"\". Do not fill the silence."
        )
    return (
        "DIALOGUE: this board has NONE — not one shot carries a spoken line.\n"
        "- So `dialogue` is yours to WRITE: the line this shot would carry, in the "
        "film's language, as it would actually be spoken. It may be read aloud by a "
        "speech model and it may be laid over the picture as a caption, so write "
        "speech and not description — no camera directions, no stage business, no "
        "speaker labels unless a name is genuinely being said.\n"
        "- Keep each line short enough to be SPOKEN while its shot is on screen. A "
        "shot that holds 2s cannot carry three sentences, and stretching the picture "
        "to fit them changes the film's shape.\n"
        "- ⚠ AND WRITE NOTHING WHERE NOTHING SHOULD BE SAID. A montage, a landscape, "
        "a title sequence, a held reaction: \"\" is the right answer and a common one. "
        "A film narrated wall to wall because the field existed is a worse film."
    )


# ---------------------------------------------------------------------------
# SOUND — the cues phases D and E fetch, written by the call that read the film
# ---------------------------------------------------------------------------
# ⚠ A CUE IS A SEARCH TERM, AND THE PROMPT HAS TO SAY SO IN THOSE WORDS. Asked
# "what does this shot sound like" a model writes prose — "the dull thud of the
# door closing on everything he was" — which is a lovely sentence and zero
# results in a stock library. What the pass can actually use is what a sound
# editor types into a search box: two to five concrete words naming the SOURCE.
#
# ⚠ AND IT IS ENGLISH, ALWAYS, for the same reason a Veo motion prompt is: the
# catalogue is indexed in English, so a Hindi cue is not a more authentic cue, it
# is an empty result set. `enforce_sound_language` drops one that comes back in
# another script rather than sending it to be searched for.
#
# ⚠ AND MOST SHOTS GET NOTHING. This is the same restraint rule the system block
# states about dissolves and titles, and it needs restating here because a field
# per shot is an invitation to fill every one. A film with a sound effect on all
# forty-eight shots is not a sound design, it is a cartoon — and it also spends
# the whole shared library budget on one press (see `MAX_SFX_SOUNDS`).
def sound_instruction() -> str:
    """Told to the ANALYSE call. Does not depend on the board, unlike `SPEECH`."""
    return (
        "SOUND: two fields, and both are SEARCH TERMS FOR A STOCK SOUND LIBRARY "
        "rather than descriptions.\n"
        "- ⚠ ONE TO THREE WORDS. NEVER MORE. THIS IS THE RULE THAT DECIDES WHETHER "
        "THE FILM GETS ANY SOUND AT ALL. The library matches EVERY word you write, "
        "so each extra word can only cut the results down — and a fourth adjective "
        "cuts them to nothing. \"door slam\" finds hundreds of doors; \"heavy "
        "wooden door slams shut\" finds zero, and that shot ends up silent. Write "
        "what you would type into a search box when you are in a hurry: the SOURCE "
        "of the sound, and at most one word describing it.\n"
        "- `shots[].sfx`: the one sound this shot needs. GOOD: \"door slam\", "
        "\"footsteps gravel\", \"city traffic\", \"piano note\", \"wind chimes\". "
        "BAD: \"the dull thud of the door closing\" (a sentence), \"melancholy\" (a "
        "feeling, not a sound), \"light feather rustle\" (three words, and two of "
        "them are describing the first — \"feather rustle\" is the searchable "
        "part).\n"
        "- ⚠ AND MOST SHOTS GET \"\". A sound effect earns its place the same way a "
        "dissolve does: by being rare. Cue the few moments that actually make a "
        "NOISE in the story — an impact, an arrival, a door, a phone, a change of "
        "place — and leave the rest silent. If more than about a quarter of your "
        "shots carry a cue, you are scoring a cartoon. Repeating one cue's exact "
        "wording across several shots is GOOD and costs nothing: it is the same "
        "recording, which is what one room sounds like.\n"
        "- `music`: ONE bed for the whole film — `query`, `mood`, and `why`. ⚠ THE "
        "SAME ONE-TO-THREE-WORD RULE, and it is broken here more often than "
        "anywhere else, because a score is easy to describe and hard to name. "
        "GOOD: \"ambient piano\", \"soft strings\", \"lo-fi beat\", \"orchestral "
        "swell\". BAD: \"ambient peaceful piano underscore\" — four words, and it "
        "finds nothing at all, so the film plays dry. The MOOD goes in `mood`, "
        "where it is read by a person; `query` is only for the search box.\n"
        "- One bed, not one per scene: a change of music is the strongest "
        "punctuation a film has and it is not yours to make. `query` is \"\" for a "
        "film that should play dry, which is the right answer for a dialogue scene "
        "or a documentary cut.\n"
        "- ⚠ BOTH FIELDS ARE ENGLISH IN EVERY LANGUAGE, whatever the film is in. "
        "They are searched for in an English-indexed catalogue, exactly like the "
        "`motion` prompts, and a cue in another script simply finds nothing."
    )


# ---------------------------------------------------------------------------
# THE PROMPTS
# ---------------------------------------------------------------------------
_prompt_cache: dict | None = None


def prompts(reload: bool = False) -> dict:
    """The three `director:` blocks out of prompts.yaml.

    Cached, because a plan is two calls and re-reading the file between them
    would let a mid-run edit give the polish call a different brief from the one
    the analyse call answered.
    """
    global _prompt_cache
    if _prompt_cache is not None and not reload:
        return _prompt_cache
    try:
        with open(PROMPTS_PATH, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    except OSError as e:
        raise DirectorError(f"Could not read {PROMPTS_PATH} ({e}).") from None
    block = config.get("director") or {}
    missing = [k for k in ("system", "analyse", "polish") if not (block.get(k) or "").strip()]
    if missing:
        raise DirectorError(
            f"{PROMPTS_PATH} is missing the director prompt block(s): {', '.join(missing)}."
        )
    _prompt_cache = {k: str(block[k]).strip() for k in ("system", "analyse", "polish")}
    return _prompt_cache


def _fill(template: str, values: dict[str, str]) -> str:
    """`<<TOKEN>>` substitution. NOT `str.format` — the prompts contain braces."""
    out = template
    for name, value in values.items():
        out = out.replace(f"<<{name}>>", value)
    return out


def _json(value: Any) -> str:
    """Compact, key-sorted JSON — the same bytes for the same board, every time."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)


# ---------------------------------------------------------------------------
# THE BRIEF — the board as the model sees it
# ---------------------------------------------------------------------------
def _clip(text: Any, limit: int) -> str:
    out = str(text or "").strip()
    if len(out) <= limit:
        return out
    return out[: limit - 1].rstrip() + "…"


def build_brief(board: dict, brief_text: str = "", language: str = "") -> dict:
    """Read a board payload into the object both calls are given.

    ⚠ EVERY SHOT IS INCLUDED, however long the film. Only the prose per shot is
    clipped — a plan written against a board it saw two thirds of would put the
    ending in the middle, which is the one mistake that makes the whole pass
    useless rather than imperfect.

    ⚠ AND IT IS BUILT IN A FIXED ORDER. This object is hashed as part of the
    determinism claim, so nothing here may depend on dict ordering, on a set, or
    on anything the caller happened to send twice.
    """
    raw_shots = board.get("shots") or []
    shots = []
    for i, shot in enumerate(raw_shots):
        item = shot if isinstance(shot, dict) else {}
        ms = int(item.get("ms") or item.get("duration_ms") or 0)
        entry = {
            "shot": i + 1,
            "holds_ms": max(0, ms),
            "label": _clip(item.get("label"), 120),
            "description": _clip(item.get("description"), MAX_DESCRIPTION_CHARS),
            "dialogue": _clip(item.get("dialogue"), MAX_DIALOGUE_CHARS),
        }
        shots.append(entry)

    total = sum(s["holds_ms"] for s in shots)
    existing = board.get("existing") or {}
    return {
        "title": _clip(board.get("title"), 120),
        "aspect_ratio": str(board.get("aspect_ratio") or "16:9"),
        "fps": int(board.get("fps") or 24),
        "shot_count": len(shots),
        "total_ms": int(board.get("total_ms") or total),
        "language": (language or "").strip(),
        "brief": _clip(brief_text, MAX_BRIEF_CHARS),
        # What is ALREADY on the timeline. A Director that proposes a dissolve on
        # a cut that has one is proposing a replacement without knowing it, and a
        # second title over a title the user wrote is the edit they will
        # remember. Counts, not contents — the plan addresses shots and cuts.
        "already_on_it": {
            "transitions_on_cuts": sorted(int(c) for c in (existing.get("transitionCuts") or [])),
            "text_clips": int(existing.get("texts") or 0),
            "shapes": int(existing.get("shapes") or 0),
            "audio_tracks": int(existing.get("audioTracks") or 0),
        },
        "shots": shots,
    }


# ---------------------------------------------------------------------------
# THE TWO SCHEMAS
# ---------------------------------------------------------------------------
def analyse_schema() -> dict:
    """What the reading looks like. Plain JSON Schema — see `llm_json`."""
    return {
        "type": "object",
        "properties": {
            "logline": {"type": "string", "description": "One sentence: what the film is about."},
            "mood": {"type": "string", "description": "One or two words."},
            "genre": {"type": "string"},
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_shot": {"type": "integer"},
                        "end_shot": {"type": "integer"},
                        "title": {"type": "string"},
                        "why": {"type": "string", "description": "What makes this a boundary."},
                    },
                    "required": ["start_shot", "end_shot", "title"],
                },
            },
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "shot": {"type": "integer"},
                        "beat": {"type": "string", "description": "What this shot DOES."},
                        "emphasis": {"type": "string", "enum": ["low", "normal", "high"]},
                        "motion": {
                            "type": "string",
                            "description": "Camera and subject movement, as a video-model prompt. English.",
                        },
                        "dialogue": {
                            "type": "string",
                            "description": "The words spoken in this shot, in the film's language.",
                        },
                        "sfx": {
                            "type": "string",
                            "description": (
                                "A stock-library SEARCH TERM for the one sound this shot "
                                "needs, or \"\" for silence. English, 2-5 words."
                            ),
                        },
                    },
                    "required": ["shot", "beat"],
                },
            },
            # ⚠ ONE BED FOR THE FILM, NOT ONE PER SCENE. See `sound_instruction`.
            "music": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "A stock-library search term for the score, English, or \"\" "
                            "for a film that should play dry."
                        ),
                    },
                    "mood": {"type": "string", "description": "What the bed is doing. English."},
                    "why": {"type": "string", "description": "Why this bed, in a few words."},
                },
            },
            "title_card": {"type": "string"},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["logline", "mood", "shots"],
    }


# ⚠ THE ARGUMENT TYPES, AND THIS IS THE ONE TABLE THAT IS NOT DERIVED.
# The NAMES come from the manifest the client sends (`verbVocab()` reads them off
# `ACTIONS`); only what each name is made of is written here, because JSON Schema
# needs a type and JavaScript does not declare one. It is a short list and it
# fails safe: a name missing from it is sent as a string, and every validator on
# the other side coerces its own arguments anyway (`num`, `int`, `ms`, `frac`).
_ARG_TYPES = {
    # counts and indices
    "shot": "integer", "cut": "integer", "track": "integer", "index": "integer",
    # times, always milliseconds
    "ms": "integer", "startMs": "integer", "durationMs": "integer",
    "inMs": "integer", "outMs": "integer",
    # fractions and factors
    "from": "number", "to": "number", "scale": "number", "x": "number", "y": "number",
    "w": "number", "h": "number", "opacity": "number", "rotation": "number",
    "volume": "number", "value": "number",
    # words
    "kind": "string", "ref": "string", "text": "string", "preset": "string",
    "ease": "string", "position": "string", "align": "string", "size": "string",
    "backdrop": "string", "place": "string", "color": "string", "name": "string",
    "param": "string", "curve": "string", "inCurve": "string", "outCurve": "string",
}


def plan_schema(vocabulary: dict) -> dict:
    """The edit plan's shape, built from the manifest the client sent.

    ⚠ `args` IS ONE FLAT OBJECT WITH EVERY ARGUMENT ON IT, not a union per verb,
    because a schema language with no unions is what we have. The prompt tells
    the model to send only what it means; `fold_steps` then throws away anything
    the named verb does not take, so `preset` on an `add_transition` never
    reaches the client. What neither can catch is a value the model MEANT for
    that verb and got wrong — and that is the client validator's job, which is
    where it belongs.

    ⚠ `params` IS A LIST OF PAIRS, and the value is a STRING. Transition and
    effect parameters are per-kind and cannot be typed here; every validator on
    the other side already coerces (`num(value)` reads "0.5" as 0.5, `str(value)`
    reads a LUT name as itself), so a string pair loses nothing and is
    expressible.
    """
    verbs = [v.get("id") for v in (vocabulary.get("verbs") or []) if v.get("id")]
    names = sorted({name for v in (vocabulary.get("verbs") or []) for name in (v.get("args") or [])})
    props: dict = {}
    for name in names:
        if name == "params":
            props["params"] = {
                "type": "array",
                "description": "Only the parameter names this kind takes.",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["name", "value"],
                },
            }
            continue
        props[name] = {"type": _ARG_TYPES.get(name, "string")}

    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence: what this edit does."},
            "mood": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "verb": {"type": "string", "enum": verbs} if verbs else {"type": "string"},
                        "note": {"type": "string", "description": "Why, in a few words. English."},
                        "args": {"type": "object", "properties": props},
                    },
                    "required": ["verb"],
                },
            },
        },
        "required": ["summary", "steps"],
    }


# ---------------------------------------------------------------------------
# FOLDING — what comes back, read into steps
# ---------------------------------------------------------------------------
def fold_steps(raw: Any, vocabulary: dict) -> tuple[list[dict], list[dict]]:
    """Read the model's steps into `{verb, args, note}`. Returns `(steps, dropped)`.

    ⚠ IT DROPS, IT NEVER RAISES — the rule this whole feature is built on. A step
    naming a verb that does not exist, or carrying nothing at all, is left out
    with a reason the panel shows under the table; the other forty-seven still
    make a film.

    ⚠ AND IT FILTERS BY VERB. `args` arrives as one flat object because the
    schema has no unions (see `plan_schema`), so a model that filled in a field
    belonging to another verb would otherwise send it on to the client, where
    that verb's validator would read it as a real instruction. `preset` on an
    `add_transition` is nonsense; `x: 0` on an `add_text` is a caption pinned to
    the left edge of the frame, which is worse, because it looks deliberate.
    """
    known = {v.get("id"): set(v.get("args") or []) for v in (vocabulary.get("verbs") or [])}
    steps: list[dict] = []
    dropped: list[dict] = []
    items = raw if isinstance(raw, list) else []

    for index, item in enumerate(items):
        if len(steps) >= MAX_STEPS:
            dropped.append({"index": index, "verb": "(any)", "why": f"past the {MAX_STEPS}-step ceiling"})
            break
        step = item if isinstance(item, dict) else {}
        verb = str(step.get("verb") or "").strip()
        if verb not in known:
            dropped.append({"index": index, "verb": verb or "(none)", "why": f"there is no “{verb}” verb"})
            continue
        allowed = known[verb]
        raw_args = step.get("args") if isinstance(step.get("args"), dict) else {}
        args: dict = {}
        for name in sorted(raw_args):
            if name not in allowed:
                continue
            value = raw_args[name]
            if name == "params":
                folded = {}
                for pair in value if isinstance(value, list) else []:
                    if isinstance(pair, dict) and str(pair.get("name") or "").strip():
                        folded[str(pair["name"]).strip()] = pair.get("value")
                if folded:
                    args["params"] = folded
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            args[name] = value
        if not args:
            dropped.append({"index": index, "verb": verb, "why": "no arguments this verb understands"})
            continue
        steps.append({"verb": verb, "args": args, "note": str(step.get("note") or "").strip()})

    return steps, dropped


# ---------------------------------------------------------------------------
# THE LANGUAGE FENCE
# ---------------------------------------------------------------------------
# ⚠ THE ONLY RULE IN THIS FILE THAT REMOVES SOMETHING FOR WHAT IT SAYS RATHER
# THAN FOR WHAT IT IS. Everything else here is shape checking. This is content —
# and it is here rather than in the client validator because it is the one
# content rule that has a right answer: a caption in a script the audience cannot
# read is not a style choice, and neither is a Devanagari camera instruction to a
# model that follows English.
_TEXT_ARGS = ("text",)


def enforce_language(steps: list[dict], language: str) -> tuple[list[dict], list[dict]]:
    """Hold on-screen text to the project's script. Returns `(kept, dropped)`.

    Only the words the AUDIENCE reads are policed — `add_text` and `set_text`.
    A `note` is written for the person editing and is English by instruction; a
    `ref` is an identifier. Neither is the audience's problem.
    """
    script = script_of(language)
    if not script:
        return steps, []
    kept: list[dict] = []
    dropped: list[dict] = []
    for index, step in enumerate(steps):
        if step["verb"] not in ("add_text", "set_text"):
            kept.append(step)
            continue
        bad = False
        for name in _TEXT_ARGS:
            value = step["args"].get(name)
            if isinstance(value, str) and value.strip() and not in_script(value, script):
                found = ", ".join(sorted(scripts_in(value))) or "another script"
                dropped.append(
                    {
                        "index": index,
                        "verb": step["verb"],
                        "why": f"“{value[:40]}” is {found}, and this film is written in {language}",
                    }
                )
                bad = True
                break
        if not bad:
            kept.append(step)
    return kept, dropped


def enforce_motion_language(shots: list[dict]) -> tuple[list[dict], list[dict]]:
    """Veo motion prompts stay in English. Returns `(shots, dropped)`.

    ⚠ THE PROMPT IS DROPPED, THE DIALOGUE IS KEPT. They are two different things
    living in one entry: the motion is an INSTRUCTION to a video model, which
    follows English best, and the dialogue is a PERFORMANCE, which has to be in
    the language the character speaks. So a shot whose motion prompt came back in
    Devanagari loses its motion prompt and keeps its line — the Phase 4 render
    then falls back to the board's own prompt, which is what it does for any shot
    the Director had nothing to say about.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for shot in shots:
        motion = str(shot.get("motion") or "")
        if motion.strip() and not in_script(motion, "latin"):
            found = ", ".join(sorted(scripts_in(motion))) or "a non-Latin script"
            dropped.append(
                {
                    "index": int(shot.get("shot") or 0),
                    "verb": "veo_prompt",
                    "why": f"the motion prompt for shot {shot.get('shot')} is {found} — Veo prompts are written in English",
                }
            )
            shot = {**shot, "motion": ""}
        kept.append(shot)
    return kept, dropped


def enforce_sound_language(analysis: dict) -> tuple[dict, list[dict]]:
    """Sound cues stay in English. Returns `(analysis, dropped)`.

    ⚠ THE SAME RULE AS `enforce_motion_language` AND FOR A HARDER REASON. A Veo
    prompt in Devanagari is a WORSE camera move; a sound cue in Devanagari is
    NOTHING AT ALL — it is typed into a catalogue indexed in English, so it
    returns zero results and the shot ends up silent with no explanation on
    screen. Dropping it here means the preview says "this cue was dropped, and
    why" before a single request is spent looking for it.

    ⚠ AND A DROPPED CUE IS A SILENT SHOT, NEVER A FAILED RUN. Exactly the trade
    the whole file makes: the other nine cues still land.
    """
    dropped: list[dict] = []

    shots: list[dict] = []
    for shot in analysis.get("shots") or []:
        cue = str(shot.get("sfx") or "")
        if cue.strip() and not in_script(cue, "latin"):
            found = ", ".join(sorted(scripts_in(cue))) or "a non-Latin script"
            dropped.append(
                {
                    "index": int(shot.get("shot") or 0),
                    "verb": "sfx_cue",
                    "why": (
                        f"the sound cue for shot {shot.get('shot')} is {found} — the sound "
                        "library is searched in English, so it would find nothing"
                    ),
                }
            )
            shot = {**shot, "sfx": ""}
        shots.append(shot)

    music = dict(analysis.get("music") or {})
    query = str(music.get("query") or "")
    if query.strip() and not in_script(query, "latin"):
        found = ", ".join(sorted(scripts_in(query))) or "a non-Latin script"
        dropped.append(
            {
                "index": 0,
                "verb": "music_cue",
                "why": (
                    f"the music cue is {found} — the sound library is searched in "
                    "English, so it would find nothing"
                ),
            }
        )
        music["query"] = ""

    return {**analysis, "shots": shots, "music": music}, dropped


# ---------------------------------------------------------------------------
# THE TWO CALLS
# ---------------------------------------------------------------------------
def analyse_request(brief: dict) -> JsonRequest:
    """The analyse call, built and not sent. Public so a test can hash it."""
    block = prompts()
    prompt = _fill(
        block["analyse"],
        {
            "BOARD": _json(brief),
            "BRIEF": brief.get("brief") or "(nothing — read the board itself)",
            "LANGUAGE": language_instruction(brief.get("language") or ""),
            # ⚠ DERIVED FROM THE BOARD, NOT FROM A FLAG THE CALLER SENDS. Whether
            # this film already has dialogue is a fact about the material, and
            # the one thing that decides whether the model is copying a script or
            # writing one. See `speech_instruction`.
            "SPEECH": speech_instruction(brief),
            # ⚠ CONSTANT, UNLIKE `SPEECH`. What a film should sound like does not
            # depend on what the board already carries — there is no "the board
            # already has sound effects" case, because a board carries pictures
            # and words and nothing else. See `sound_instruction`.
            "SOUND": sound_instruction(),
        },
    )
    return JsonRequest(
        system=block["system"], prompt=prompt, schema=analyse_schema(), purpose="analyse"
    )


def polish_request(brief: dict, analysis: dict, vocabulary: dict, include: dict) -> JsonRequest:
    """The polish call, built and not sent. Public so a test can hash it."""
    block = prompts()
    caps = vocabulary.get("caps") or {}
    prompt = _fill(
        block["polish"],
        {
            "BOARD": _json(brief),
            "ANALYSIS": _json(analysis),
            "BRIEF": brief.get("brief") or "(nothing — read the board itself)",
            "LANGUAGE": language_instruction(brief.get("language") or ""),
            "VOCABULARY": _json(_vocabulary_for_prompt(vocabulary)),
            "CAPS": _json(caps),
            "INCLUDE": _json({k: bool(v) for k, v in sorted((include or {}).items())}),
        },
    )
    return JsonRequest(
        system=block["system"], prompt=prompt, schema=plan_schema(vocabulary), purpose="polish"
    )


def _vocabulary_for_prompt(vocabulary: dict) -> dict:
    """The manifest, trimmed to what a planner needs to read.

    ⚠ TRIMMED, NOT REWRITTEN. Every id that survives here came off the manifest
    the client derived; nothing is added and no id is renamed. What goes is bulk
    the model cannot act on — the 41 shapes keep their labels but not their
    categories, the effects keep their parameter NAMES but not the defaults,
    because a default it repeats back is a step that changes nothing.
    """
    def ids(table, extra=()):
        out = []
        for entry in vocabulary.get(table) or []:
            if not isinstance(entry, dict):
                continue
            row = {"id": entry.get("id"), "label": entry.get("label") or entry.get("id")}
            if entry.get("note"):
                row["note"] = entry["note"]
            for key in extra:
                value = entry.get(key)
                if isinstance(value, dict):
                    value = sorted(value.keys())
                if value:
                    row[key] = value
            out.append(row)
        return out

    text = vocabulary.get("text") or {}
    return {
        "verbs": vocabulary.get("verbs") or [],
        "transitions": ids("transitions", ("params", "directions")),
        "transition_ms": vocabulary.get("transitionDurationMs") or {},
        "effects": ids("effects", ("params",)),
        "shapes": ids("shapes"),
        "audio_transitions": ids("audioTransitions"),
        "text": {
            "presets": [
                {"id": p.get("id"), "label": p.get("label"), "hint": p.get("hint") or ""}
                for p in (text.get("presets") or [])
                if isinstance(p, dict)
            ],
            "positions": text.get("positions") or [],
            "places": text.get("places") or [],
            "backdrops": text.get("backdrops") or [],
            "sizes": text.get("sizes") or [],
            "aligns": text.get("aligns") or [],
        },
        "easings": vocabulary.get("easings") or [],
    }


def analyse(brief: dict) -> dict:
    """Call one. Raises DirectorError with a readable reason."""
    try:
        raw = complete_json(analyse_request(brief))
    except LLMJsonError as e:
        raise DirectorError(str(e)) from None
    return _coerce_analysis(raw, brief)


def _coerce_analysis(raw: dict, brief: dict) -> dict:
    """Read the reading. Anything unusable becomes empty, never an exception."""
    count = int(brief.get("shot_count") or 0)

    scenes = []
    for item in raw.get("scenes") or []:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start_shot"))
            end = int(item.get("end_shot"))
        except (TypeError, ValueError):
            continue
        # A scene outside the film is a reading of a board we did not send.
        if start < 1 or end < start or end > max(count, 1):
            continue
        scenes.append(
            {
                "start_shot": start,
                "end_shot": end,
                "title": _clip(item.get("title"), 80),
                "why": _clip(item.get("why"), 200),
            }
        )
    scenes.sort(key=lambda s: (s["start_shot"], s["end_shot"]))

    shots = []
    seen = set()
    for item in raw.get("shots") or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("shot"))
        except (TypeError, ValueError):
            continue
        if number < 1 or number > count or number in seen:
            continue
        seen.add(number)
        emphasis = str(item.get("emphasis") or "normal").strip().lower()
        shots.append(
            {
                "shot": number,
                "beat": _clip(item.get("beat"), 160),
                "emphasis": emphasis if emphasis in ("low", "normal", "high") else "normal",
                "motion": _clip(item.get("motion"), 400),
                "dialogue": _clip(item.get("dialogue"), MAX_DIALOGUE_CHARS),
                # ⚠ CLIPPED HARD, BECAUSE IT IS A QUERY AND NOT PROSE. 80
                # characters is already four times what a usable search term
                # needs; a model that wrote a sentence here gets its sentence
                # searched for, finds nothing, and the shot is silent. The
                # prompt asks for 2-5 words and this is the fence behind it.
                "sfx": _clip(item.get("sfx"), 80),
            }
        )
    shots.sort(key=lambda s: s["shot"])

    music_raw = raw.get("music") if isinstance(raw.get("music"), dict) else {}
    music = {
        "query": _clip(music_raw.get("query"), 80),
        "mood": _clip(music_raw.get("mood"), 60),
        "why": _clip(music_raw.get("why"), 160),
    }

    return {
        "logline": _clip(raw.get("logline"), 300),
        "mood": _clip(raw.get("mood"), 40),
        "genre": _clip(raw.get("genre"), 60),
        "scenes": scenes,
        "shots": shots,
        # ⚠ ALWAYS PRESENT, EVEN WHEN EMPTY. `sound_pass.musicCue` reads
        # `analysis.music.query` and an absent object would make "the model said
        # nothing" and "the reading is from before this field existed"
        # indistinguishable — both silent, one of them a bug.
        "music": music,
        "title_card": _clip(raw.get("title_card"), 80),
        "notes": [_clip(n, 200) for n in (raw.get("notes") or []) if str(n or "").strip()][:12],
    }


def polish(brief: dict, analysis: dict, vocabulary: dict, include: dict) -> dict:
    """Call two. Raises DirectorError with a readable reason."""
    try:
        raw = complete_json(polish_request(brief, analysis, vocabulary, include))
    except LLMJsonError as e:
        raise DirectorError(str(e)) from None
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------------------
# THE WHOLE PASS
# ---------------------------------------------------------------------------
def direct(
    board: dict,
    vocabulary: dict,
    include: dict | None = None,
    language: str = "",
    brief_text: str = "",
) -> dict:
    """Read a board and write a plan.

    Returns:
        {
          "plan":     {version, summary, mood, language, include, steps},
          "analysis": the reading — logline, mood, scenes, per-shot beats,
          "veo":      [{shot, prompt, dialogue}] — the motion prompts, for the
                      pass that spends. NOTHING HERE SPENDS ANYTHING.
          "sfx":      [{shot, query}] — a stock-library search term per shot that
                      should make a noise. Phase D fetches these.
          "music":    {query, mood, why} — ONE bed for the film, or an empty
                      query for a film that should play dry. Phase E fetches it.
          "dropped":  [{index, verb, why}] — every step thrown away and why,
          "notes":    what the reading wanted the editor to know,
        }

    ⚠ `veo` IS WRITTEN NOW AND SPENT LATER, and that is deliberate. The motion
    prompt for a shot is a STORY decision — it belongs to the reading, beside the
    beat and the emphasis, and asking for it in a separate call at render time
    would ask a model that has not read the film. It costs nothing to carry: it
    is text on a plan the user reads before anything is rendered.

    Raises:
        DirectorError: with a reason written for a human.
    """
    if not (vocabulary.get("verbs") or []):
        raise DirectorError(
            "The editor sent no capability manifest, so there is no vocabulary to "
            "plan in. This is a wiring fault, not a bad board."
        )

    include = {k: bool(v) for k, v in (include or {}).items()}
    brief = build_brief(board, brief_text=brief_text, language=language)
    if brief["shot_count"] < 1:
        raise DirectorError("There is nothing on the timeline to edit yet.")

    analysis = analyse(brief)
    shots, motion_dropped = enforce_motion_language(analysis.get("shots") or [])
    analysis = {**analysis, "shots": shots}
    # ⚠ THE SOUND CUES GO THROUGH THE SAME GATE, AND FOR A HARDER REASON — a cue
    # in the wrong script finds nothing at all rather than merely working less
    # well. See `enforce_sound_language`.
    analysis, sound_dropped = enforce_sound_language(analysis)

    written = polish(brief, analysis, vocabulary, include)
    steps, dropped = fold_steps(written.get("steps"), vocabulary)
    steps, language_dropped = enforce_language(steps, language)

    plan = {
        "version": 1,
        "summary": _clip(written.get("summary"), 300) or analysis.get("logline", ""),
        "mood": _clip(written.get("mood"), 40) or analysis.get("mood", ""),
        "language": (language or "").strip(),
        "include": include,
        "steps": steps,
    }
    sfx = [
        {"shot": s["shot"], "query": (s.get("sfx") or "").strip()}
        for s in analysis.get("shots") or []
        if (s.get("sfx") or "").strip()
    ]
    logger.info(
        "[director] %d shots → %d scene(s), %d step(s) kept, %d dropped (%d for language); "
        "%d sfx cue(s), music %s.",
        brief["shot_count"], len(analysis.get("scenes") or []), len(steps),
        len(dropped) + len(language_dropped) + len(motion_dropped) + len(sound_dropped),
        len(language_dropped), len(sfx),
        "yes" if ((analysis.get("music") or {}).get("query") or "").strip() else "no",
    )
    return {
        "plan": plan,
        "analysis": analysis,
        "veo": [
            {"shot": s["shot"], "prompt": s.get("motion") or "", "dialogue": s.get("dialogue") or ""}
            for s in analysis.get("shots") or []
            if (s.get("motion") or "").strip()
        ],
        # ⚠ WRITTEN NOW AND FETCHED LATER, exactly like `veo` above and for the
        # same reason: what a moment should sound like is a STORY decision that
        # belongs beside the beat, and asking a second model at placement time
        # would ask one that has not read the film. Nothing here fetches anything
        # — these are search terms on a plan the user reads first.
        "sfx": sfx,
        "music": analysis.get("music") or {},
        "dropped": dropped + language_dropped + motion_dropped + sound_dropped,
        "notes": analysis.get("notes") or [],
    }
