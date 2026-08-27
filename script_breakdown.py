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
import re
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
# Raised from 60 when the breakdown started splitting actions into their beats
# (wind-up / action / impact / reaction, each its own shot). That division is
# what makes a panel animatable, and it roughly doubles the shot count, so the
# old ceiling would have silently truncated ordinary scripts. Still a ceiling: a
# shot is an image, and 120 images is already an expensive click.
MAX_SHOTS = 120

# ---------------------------------------------------------------------------
# Sampling — the breakdown is EXTRACTION, not invention
# ---------------------------------------------------------------------------
# Reading a script into shots has a right answer, so the same script should give
# the same shot list twice. Temperature 0 with a fixed seed is as close to that
# as this API gets. Two caveats worth knowing:
#   - Gemini exposes no bit-exact reproducibility. Serving-side batching means
#     even temperature 0 can differ occasionally. This makes runs COMPARABLE,
#     not identical.
#   - `gemini-2.5-flash` is a rolling ALIAS. If you need runs to stay comparable
#     across weeks, pin VERTEX_TEXT_MODEL / GEMINI_TEXT_MODEL to a dated
#     snapshot — the sampling settings below can't hold a moving model still.
# Raise TEXT_TEMPERATURE if you deliberately want varied readings of one script.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 1.0
DEFAULT_SEED = 42


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


def text_provider(provider: str | None = None) -> str:
    """The effective text provider. Public because `captions.py` and `tts.py`
    have to resolve it to the SAME answer this module does — two modules each
    reading TEXT_PROVIDER their own way is how a workflow ends up half on Vertex
    and half on the Developer API with only one set of credentials configured.
    """
    return _resolve_provider(provider)


def text_model_id(provider: str | None = None) -> str:
    """The text model id for `provider`, for the same reason as above."""
    return _model_id(_resolve_provider(provider))


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to `default` when unset or junk."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[breakdown] %s=%r is not a number — using %s.", name, raw, default)
        return default


def _sampling_kwargs() -> dict:
    """Determinism settings for the breakdown call (see DEFAULT_TEMPERATURE).

    Set TEXT_SEED to "none"/"off" to let the backend pick a seed — i.e. to get a
    different reading of the same script on each run.
    """
    kwargs: dict = {
        "temperature": _env_float("TEXT_TEMPERATURE", DEFAULT_TEMPERATURE),
        "top_p": _env_float("TEXT_TOP_P", DEFAULT_TOP_P),
    }

    raw_seed = (os.environ.get("TEXT_SEED") or str(DEFAULT_SEED)).strip()
    if raw_seed.lower() not in ("", "none", "off", "random"):
        try:
            kwargs["seed"] = int(raw_seed)
        except ValueError:
            logger.warning("[breakdown] TEXT_SEED=%r is not an integer — ignoring.", raw_seed)

    # Older google-genai builds don't carry every generation field. Drop what
    # this SDK doesn't know rather than fail the whole breakdown on a kwarg.
    supported = types.GenerateContentConfig.model_fields
    dropped = [k for k in kwargs if k not in supported]
    if dropped:
        logger.warning(
            "[breakdown] google-genai does not support %s — upgrade the SDK for "
            "reproducible runs.", ", ".join(dropped),
        )
    return {k: v for k, v in kwargs.items() if k in supported}


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
# ⚠ THE SYSTEM PROMPT IS IN THREE PIECES BECAUSE ONE RULE IN IT HAS TO SWAP.
# Everything here was written for RAW PROSE — a user pasting a story — where
# the job really is to break long sentences down into beats. Since the concept
# gate landed, most scripts arrive from `plan_agent.write_script()` via
# `script_to_text()`, which ALREADY writes ONE BEAT PER LINE. Told to split a
# beat that is already a beat, the model splits it again: three script lines
# about a face, the light on it and the flowers before it came back as three
# almost identical close-ups, all drawn, all paid for. Reported. So the density
# rule lives in its own block and `_system_instruction()` picks the one that
# suits the script in hand — see `_is_beat_script`.
_SYSTEM_HEAD = (
    "You are a professional film storyboard supervisor. You read a script and "
    "break it into a clear, ordered SHOT LIST for a storyboard artist. Each shot "
    "is ONE storyboard panel: a single moment we can draw. Keep descriptions "
    "concrete and visual (what the camera sees), not internal thoughts. Infer a "
    "sensible location and camera angle when the script doesn't state them. Do "
    "NOT invent major plot the script doesn't imply. Split long actions into "
    "multiple shots when the visual clearly changes.\n\n"
    # THE SHOT LIST IS A FILM, NOT A LIST OF PICTURES. Without this the model
    # returns twelve self-contained illustrations of twelve sentences: each is
    # fine on its own and the board does not play. Every downstream stage —
    # the panel art, the key poses, the animatic — inherits whatever flow this
    # list has, so it has to be here.
    "THE SHOTS MUST PLAY AS ONE CONTINUOUS FILM, in order, the way a flipbook "
    "does. Read your own list back as if it were being projected:\n"
    "- Each shot picks up exactly where the one before it left off. If someone "
    "is reaching for a door in shot 4, shot 5 starts with their hand on it — "
    "not with them already outside.\n"
    "- Nothing may change between consecutive shots of the same scene except "
    "what the story actually changes: same clothes, same light, same time of "
    "day, same objects in the same places.\n"
    "- VARY THE FRAMING between neighbouring shots — a wide, then a medium, "
    "then a close-up of the thing that matters. Two identical framings back to "
    "back read as a mistake; cutting from a close-up straight to another "
    "close-up of someone else needs a reason.\n"
    "- Keep screen direction consistent: someone moving left-to-right keeps "
    "moving left-to-right until the story turns them round.\n"
    "- Spend shots where the story turns and skim the rest. A beat that matters "
    "gets its own close-up; a journey can be one shot.\n\n"
    # THE RULE THAT MAKES A PANEL ANIMATABLE.
    #
    # Every panel is later handed to a key-pose generator that draws the shot's
    # motion STARTING FROM THAT PANEL. So a panel that depicts the middle or the
    # end of a movement has nothing to animate: given "a slipper is seen mid-air,
    # flying towards camera", the flipbook opens with the slipper already in
    # flight and there is no throw. Reported exactly that way by the user, who
    # wanted the slipper in the thrower's HAND first.
    #
    # An action therefore has to be broken into its beats, each its own shot,
    # and each shot opened at the instant BEFORE its movement — the wind-up, not
    # the follow-through.
    "EVERY SHOT MUST OPEN AT THE START OF ITS OWN ACTION. This is the most "
    "important rule here, because each panel is later animated FORWARD from the "
    "moment it draws:\n"
    "- Draw the instant BEFORE the movement happens — the wind-up, the hand "
    "still holding the object, the mouth about to open, the weight already "
    "shifting. Never open a shot on the middle or the end of a movement.\n"
)

# --- HOW FINELY TO CUT. The two blocks below are alternatives, never both. ---
#
# PROSE: the original rule, and still the right one for a pasted story. Nobody
# has divided that into beats yet, so the breakdown has to.
_SYS_DENSITY_PROSE = (
    "- A thrown object is the clearest case. WRONG: one shot of 'the slipper "
    "flies through the air'. RIGHT: the thrower with the slipper raised in his "
    "hand, ready to throw → the slipper in flight → the moment it strikes → the "
    "victim's reaction. Four shots, each starting at its own beginning.\n"
    "- SPLIT AN ACTION INTO ITS BEATS and give each beat its own shot: the "
    "preparation, the action itself, the impact or result, and the reaction of "
    "whoever it happened to. A cause and its effect are never one panel.\n"
    "- Prefer MORE, SMALLER shots over fewer busy ones. Each shot is one clear "
    "physical beat that a person could act out in about a second. If your "
    "description needs the word 'then', or 'as', or 'while', it is two shots.\n"
)

# BEATS: the script came out of `plan_agent.script_to_text()`, where ONE LINE IS
# ALREADY ONE BEAT. Dividing it a second time is the bug this block exists to
# stop, so the pressure runs the other way — merge, do not split.
_SYS_DENSITY_BEATS = (
    "- A thrown object still shows the rule: given a line about a slipper being "
    "thrown, draw the thrower with it raised in his hand, ready — not the "
    "slipper already in flight.\n"
    "- THIS SCRIPT IS ALREADY DIVIDED INTO BEATS. Every line under a scene "
    "heading was written to BE one panel, so ONE LINE IS ONE SHOT. Do NOT "
    "divide a line again into a wind-up, an action and a reaction: the writer "
    "already made that division, and making it twice hands back three "
    "near-identical panels of one moment.\n"
    "- Prefer FEWER, WHOLE shots. Split one line into two only when it plainly "
    "holds two DIFFERENT pictures — a different place, a different person, or "
    "a cut the line itself asks for. Two lines describing the same picture are "
    "ONE shot.\n"
    "- READ YOUR LIST BACK FOR REPEATS BEFORE YOU ANSWER. If two shots would "
    "show the same subject at the same framing — two close-ups of one face, "
    "two wides of one room — they are one shot. Merge them.\n"
)

_SYSTEM_TAIL = (
    "- A shot that shows only a result, with no visible cause and nobody "
    "reacting, is a mistake — add the shot before it.\n"
    # Even with the rule above, a board came back going: Kabir asleep → wide of
    # the room → SLIPPER ALREADY IN FLIGHT. The thrower was never on screen.
    # Reported: "Madanlal in the doorway panel missing before shot 3". Stating
    # the causal rule as a hard test the model can apply to its own list is what
    # catches it, because "open at the start of the action" is too abstract to
    # self-check against.
    "- NOTHING MOVES ON ITS OWN. Before any shot where something is already in "
    "motion — an object in flight, a door swinging, a hand entering frame — "
    "there MUST be an earlier shot showing the PERSON who set it moving, doing "
    "so. Check your finished list: for every moving thing, point at the shot "
    "that started it. If you cannot, you have skipped a shot; insert it.\n"
    "- The person who causes something must be ON SCREEN causing it. A slipper "
    "cannot fly until we have seen who threw it; a voice cannot shout from "
    "off-camera in a shot that is about the shouting.\n\n"
    # POSTURE. A board showed Madanlal telling off a STANDING Kabir, and two
    # shots later Kabir was flat on his back asleep in the same scene. Nothing
    # in either sentence said which he was, so the artist chose freely each
    # time. Reported: "shot 6 kabir look stand on bed but see shot 8 so he is
    # sleeping how is posible".
    "SAY WHAT EVERY CHARACTER'S BODY IS DOING, IN EVERY SHOT. Not their mood — "
    "their posture and position:\n"
    "- 'lying on his back asleep under the quilt', 'sitting up in bed', "
    "'standing in the doorway', 'half out of bed with one foot on the floor'. "
    "The artist has only your sentence; if it does not say, they will guess, "
    "and they will guess differently in the next shot.\n"
    "- POSTURE CARRIES FORWARD. A character who is lying down stays lying down "
    "until a shot SHOWS them getting up. Never let someone be asleep in one "
    "shot and on their feet in the next with no shot in between where they "
    "rise. Read your list back and check each person's body from shot to shot "
    "the way you check the location.\n\n"
    # BACKGROUND EXTRAS. A classroom full of students in the wide shot, then the
    # same classroom with nobody in it two shots later. Reported: "shot 11 kabir
    # and frnd look but background student missing not consistance same in
    # shot 16".
    "BACKGROUND PEOPLE ARE CONTINUITY TOO. If a scene contains other people — a "
    "classroom of students, a crowd, a family in the room — say so in EVERY "
    "shot of that scene, however tight the framing: 'behind them, the rest of "
    "the class at their desks'. A room that held thirty students in the wide "
    "shot still holds them in the close-up, and a background that empties "
    "between two shots of one scene is the most obvious continuity error there "
    "is.\n\n"
    "You also identify the story's WORLD — its region, period, culture and "
    "religious tradition — and make sure the people, clothing, buildings and "
    "objects you describe belong to THAT world. An artist reading your breakdown "
    "must never have to guess someone's ethnicity or dress. Judge this from the "
    "script's names, places, deities, festivals, food and language: a story about "
    "Lubdhaka and the Shiva Purana is ancient India and its people are Indian, "
    "just as a story about Kenji in Kyoto is Japanese. Never fall back on a "
    "generic Western/European default."
)

# The prose reading, kept under its old name because it is what a pasted story
# gets and because several checks read it.
_SYSTEM_INSTRUCTION = _SYSTEM_HEAD + _SYS_DENSITY_PROSE + _SYSTEM_TAIL


def _system_instruction(beats: bool) -> str:
    """The system prompt, carrying the density rule this script's shape needs."""
    return (
        _SYSTEM_HEAD
        + (_SYS_DENSITY_BEATS if beats else _SYS_DENSITY_PROSE)
        + _SYSTEM_TAIL
    )


# ---------------------------------------------------------------------------
# HOW MANY SHOTS — the prompt half of the same decision
# ---------------------------------------------------------------------------
_DENSITY_PROSE = (
    "Return between 1 and {max_shots} shots, in reading order. Err on the side "
    "of MORE shots: one clear physical beat each, opened at the start of its "
    "own action. A single sentence of script that contains a wind-up, an action "
    "and a reaction is three or four shots, not one.\n"
)

_DENSITY_BEATS = (
    "Return between 1 and {max_shots} shots, in reading order.\n"
    "⚠ THIS SCRIPT IS ALREADY BROKEN INTO BEATS — ONE LINE IS ONE SHOT. It "
    "was written for this breakdown: a 'SCENE n.' heading starts a scene, and "
    "every line under it is ONE beat, already sized to be ONE panel.\n"
    "- START FROM ONE SHOT PER ACTION LINE, and depart from that only where a "
    "line plainly holds two different pictures. Never re-split a line that is "
    "already a single beat.\n"
    "- Three lines circling one thing — a face, the light on that face, the "
    "flowers in front of it — are ONE shot, not three. That exact case came "
    "back as three almost identical close-ups of one idol.\n"
    "- 'A hand picks up the brush and paints the eye' is ONE shot. The word "
    "'and' is not a cut.\n"
)

# ⚠ A LINE OF SPEECH IS NOT A PICTURE, and nothing in the prompt used to say
# so. "NARRATOR (V.O.): The spirit of Ganesh Utsav awakens." became scene 1's
# fourth panel, with a fourth drawing of the same idol invented to carry it.
# Reported. The shape the board already handles correctly is the line living in
# a neighbouring shot's `dialogue`, which is where this sends it.
_SPEECH_RULE = (
    "⚠ A LINE OF SPEECH IS NOT A SHOT. Lines written as 'NAME: …' or "
    "'NAME (V.O.): …' are what the audience HEARS. They never get a panel of "
    "their own.\n"
    "- Put such a line in the `dialogue` of the shot it plays OVER — the "
    "action shot it belongs to, normally the one immediately before or after "
    "it in the script. A voice-over runs over a picture that is already on "
    "screen; it does not stop the film for a portrait of the speaker.\n"
    # ⚠ THE FIRST LIVE RUN OF THIS RULE CAUGHT ITSELF OUT. The end card came
    # back as a real shot — correct, an end card IS a shot — but its
    # description read "…with the text 'Celebrate Ganesh Chaturthi. May His
    # blessings light your path.' superimposed on screen." The description IS
    # the image prompt, and `gemini_client._SINGLE_FRAME_RULE` tells the image
    # model in the same breath: "No text, captions, speech bubbles, borders or
    # watermarks." Asking for both gets a frame of misspelt gibberish — image
    # models cannot letter. So the panel may exist; its WORDS may not be drawn.
    "- ⚠ 'ON SCREEN:' TEXT IS DIFFERENT, AND IT IS NEVER WRITTEN INTO THE "
    "PICTURE. An end card or a title card MAY be a shot of its own — that is a "
    "real shot of the film. But `description` is handed straight to an image "
    "model that is told to draw NO text, NO captions and NO lettering, so "
    "describe only what is PHOTOGRAPHED there ('a wide of the room, the idol "
    "glowing on the altar') and NEVER quote the words, never say "
    "'superimposed', 'with the text', 'the words appear' or 'a caption "
    "reads'.\n"
    "- The words themselves go in `dialogue`, as one entry with the character "
    "'ON SCREEN' and the line exactly as the script wrote it. That is the one "
    "field the board, the PDF and the animatic read and no image prompt ever "
    "does, so the text survives to the screen without an image model trying to "
    "spell it.\n"
    "- A (V.O.) speaker is BY DEFINITION not in frame. Never put them in that "
    "shot's `characters`, and never invent a picture of them, a narrator "
    "figure or a microphone.\n"
    "- NEVER REPEAT A PICTURE IN ORDER TO CARRY A LINE. If the only thing you "
    "can think of to draw for a line is a shot you have already drawn, that is "
    "the proof the line belongs to an existing shot — attach it there.\n"
    "- The title line, 'LOGLINE:' and the CAST block at the top of the script "
    "are not shots either. They describe the film; they are not in it.\n"
)

_PROMPT_TEMPLATE = (
    "Break the following script into a storyboard shot list, a short cast list, "
    "AND an asset list.\n"
    "{density}"
    "{budget}"
    "{speech}"
    "For each shot provide:\n"
    "  - scene_number: which SCENE this shot belongs to, starting at 1. A scene is "
    "one continuous piece of action in ONE place at ONE time. Start a NEW scene "
    "(next number) whenever the location changes, whenever time jumps (night → "
    "morning, 'later that day', 'years passed'), or whenever the story moves to a "
    "clearly separate beat. Returning to an earlier location LATER is a new scene "
    "number, not a repeat of the old one. Scene numbers only ever go up, and every "
    "shot in the same scene shares the same number. Do NOT put a whole script in "
    "scene 1 — a story that moves from a forest by day, to a tree at night, to the "
    "next morning is THREE scenes. Only use a single scene when the script genuinely "
    "never leaves one place or time.\n"
    "  - shot_number: this shot's position WITHIN its scene, restarting at 1 in "
    "each new scene (so scene 2's first shot is shot_number 1)\n"
    "  - description: one vivid sentence describing what we SEE in this panel. "
    "⚠ THIS SENTENCE IS THE IMAGE PROMPT — it is handed to an image model that "
    "is separately told to draw no text, no captions and no lettering of any "
    "kind. So it describes the PICTURE and nothing else: never any words to be "
    "written into the frame, no 'with the text …', no 'superimposed', no "
    "caption, no title, no subtitle, no logo wording. "
    "Write it as the NEXT shot of a film that is already running, and open it at "
    "the START of this shot's action (see the rule above). "
    "Name the characters by their cast names every time — never 'he', 'the "
    "man' or 'a woman', because the artist drawing this panel sees only this "
    "sentence and has to know exactly who is in it.\n"
    # The artist draws EXACTLY the sentence and nothing else. "Madanlal points
    # an angry finger at Kabir's bed" produced a panel with Madanlal pointing at
    # an empty bed — Kabir was nowhere in it, because the sentence never said he
    # was there. Reported. Naming a person's BED, chair or door is not naming
    # the person.
    "    EVERY PERSON VISIBLE IN THE FRAME MUST BE NAMED IN THIS SENTENCE, "
    "and what each of them is doing must be stated. That includes the person "
    "being spoken to, shouted at, pointed at, looked at or reacted to — if they "
    "are in shot, name them and say what they are doing ('Madanlal stands in the "
    "doorway pointing at Kabir, who is sitting up in bed rubbing his cheek'). "
    "Naming somebody's BED, chair, door or belongings does NOT put that person "
    "in the picture: an artist given 'pointing at Kabir's bed' draws an empty "
    "bed. If a character is meant to be off-camera, simply do not mention them.\n"
    "  - script_excerpt: the EXACT sentence(s) from the script THIS shot is drawn "
    "from, copied VERBATIM — same words, same spelling, no paraphrasing, no "
    "summarising, nothing added. Quote ONLY the part that becomes this one panel: "
    "usually a single sentence or clause, never the whole paragraph and never the "
    "whole scene. Each shot must quote a DIFFERENT passage, and the quotes must "
    "move FORWARD through the script in shot order — if two shots would carry the "
    "same text, you have split the wrong sentence. This is shown to the writer to "
    "point at the exact words that became this panel, so a short exact quote is "
    "far better than a long or rewritten one.\n"
    "  - characters: EVERY character visible in this shot, by cast name. This "
    "must match the description: anyone your description mentions as being in "
    "frame — including someone asleep, in the background, seen from behind, or "
    "only partly in shot ('Kabir's sleeping form', 'Madanlal's hand') — belongs "
    "in this list. It is what tells the artist who to keep looking consistent, "
    "so a person named in the sentence but missing here gets redrawn as someone "
    "else. Empty ONLY when the frame genuinely contains no people\n"
    # NOTE: this template goes through str.format, so the literal braces below
    # are DOUBLED. A single {character, line} raises KeyError at format time.
    "  - dialogue: the lines SPOKEN in this shot, in the order they are said, "
    "each as {{character, line}}. Use the character's name as it appears in the "
    "cast list. Copy the spoken words VERBATIM when the script quotes them. When "
    "the script reports speech instead of quoting it ('he declares that the "
    "armour will make them kings'), write the line as the character actually "
    "SAYS it — first person, present tense, addressed to the person in the "
    "scene ('This armour will make us kings') — never the narrator's third "
    "person ('they will be kings'). Stay as close to the script's own words as "
    "you can while doing that. Return an "
    "EMPTY list whenever nothing is spoken in this shot — a silent establishing "
    "shot, an action beat, a reaction. NEVER invent dialogue the script does not "
    "contain, never turn narration or a description of the scene into a spoken "
    "line, and never repeat the same line in two shots. A '(V.O.)' or "
    "'NAME:' line from the script belongs HERE, in the shot it plays over — "
    "never as a shot of its own.\n"
    "  - assets: list of asset names visible in the shot — the key recurring "
    "props/objects AND the background/location — using the SAME names as the "
    "asset list below (empty if none)\n"
    "  - location: where the shot takes place\n"
    "  - camera: the shot type / angle, e.g. 'wide establishing', 'close-up', "
    "'over-the-shoulder', 'medium two-shot'\n"
    "  - movement: how the camera MOVES during the shot, two or three words: "
    "'static', 'slow push-in', 'pan left', 'handheld follow', 'tilt up', "
    "'crane down'. Most shots are 'static' and that is the right answer for "
    "them. Use a move where the ACTION asks for one and only there — someone "
    "walking through a space is a follow or a track; a realisation or a "
    "reaction landing is a slow push-in; something noticed above or below the "
    "eyeline is a tilt; a place being taken in is a slow pan. Never decorate a "
    "still moment with a move it does not need.\n"
    "  - duration_seconds: how long this shot is on screen, a whole number of "
    "seconds. A held reaction is 1-2; a line of dialogue is roughly one second "
    "for every three words spoken; an establishing wide is 2-4. Keep the "
    "film's total honest — do not pad.\n"
    "Also return `world`: the story's visual world, read from the script itself "
    "(names, places, deities, festivals, food, language). Every field is a short "
    "phrase an artist can draw from:\n"
    "  - setting: place AND period, e.g. 'Ancient India, Puranic era — forest, "
    "village and stone temple'\n"
    "  - culture: the cultural / religious tradition the story sits in, e.g. "
    "'Hindu (Shaivite) mythology, Shiva Purana'\n"
    "  - ethnicity: what the PEOPLE of this world look like — regional origin, "
    "skin tone, hair and features, e.g. 'South Asian (Indian) — warm brown skin, "
    "black hair, dark eyes'. Be specific; this is used to draw them.\n"
    "  - wardrobe: the clothing of this world, e.g. 'handwoven dhoti, "
    "angavastram, simple tribal hunter's gear, rudraksha beads'\n"
    "  - environment: architecture, landscape and everyday objects of this world\n"
    "  - notes: any other visual detail that must be right (iconography, rituals, "
    "symbols, colours)\n"
    "If the script genuinely gives no cultural signal, say so plainly in `setting` "
    "rather than inventing one.\n"
    "  - country: the country/market this film is FOR. Read it off whatever the "
    "script actually shows — a named city, town or landmark; a currency; a "
    "festival or holiday; a phone-number or address format; the characters' "
    "names; the food they eat; the script/alphabet or language the dialogue is "
    "written in; a named local brand, vehicle or institution. Any ONE of those "
    "is enough. ⚠ BUT LEAVE IT EMPTY IF THE SCRIPT SHOWS NONE OF THEM. An empty "
    "answer is correct and useful; a guess puts the wrong money and the wrong "
    "signage on every screen in the film, which is worse than none. Do not "
    "reason from the genre, the product or the fact that most scripts are "
    "American — that is the mistake this field exists to prevent.\n"
    "  - language: the language the audience reads on screen, on the same terms "
    "— only if the script shows it. Leave empty if unsure.\n"
    "Also return `characters`: every NAMED character in the script, each with a "
    "concise VISUAL description (age, build, hair, clothing, distinguishing "
    "features) an artist could draw consistently. EVERY character description "
    "MUST state their ethnicity/regional appearance and period-correct clothing, "
    "consistent with `world` — write 'a lean South Asian hunter with weathered "
    "brown skin, black hair tied back, in a coarse cotton dhoti', never just 'a "
    "lean hunter in simple attire'. Use the SAME name spelling in "
    "both the shots and the cast list.\n"
    "Also return `assets`: the KEY visual elements that must look the SAME every "
    "time they reappear, so the storyboard stays consistent. Include two kinds:\n"
    "  - category 'prop': a specific recurring object that matters to the story "
    "(e.g. a particular slipper, a wooden rolling pin, a phone, a car). Only list "
    "objects that appear in MORE THAN ONE shot or are visually important — skip "
    "generic background clutter.\n"
    "  - category 'background': each distinct location/set the story revisits "
    "(e.g. 'Kabir's bedroom', 'kitchen doorway').\n"
    "Each asset has: name (short, reusable), category ('prop' or 'background'), "
    "and a concise VISUAL description an artist could draw consistently — "
    "period- and region-correct for `world` (a hut, a temple, a cooking pot and a "
    "weapon all differ by culture). Use the "
    "SAME asset name in both the shots' `assets` and this list.\n\n"
    "SCRIPT:\n{script}"
)


# ---------------------------------------------------------------------------
# WHAT SHAPE IS THIS SCRIPT IN?
# ---------------------------------------------------------------------------
# ⚠ THE ANSWER DECIDES HOW FINELY THE BREAKDOWN CUTS, so it is worked out here
# rather than guessed by the model. Two kinds of text arrive at this module and
# they need opposite treatment:
#
#   PROSE — the user pasted a story. Nothing has been divided into beats, so the
#   breakdown must do it: wind-up, action, impact, reaction.
#
#   BEATS — the text came out of `plan_agent.script_to_text()`, which writes ONE
#   BEAT PER LINE under `SCENE n.` headings precisely so a shot's quote can land
#   on exactly one panel. Cutting that again is the duplicate-panel bug.
#
# The test is the fingerprint `script_to_text` leaves, not a guess about tone: a
# `SCENE n.` heading plus one of the other blocks it writes (CAST, LOGLINE:,
# CALL TO ACTION:, ON SCREEN:, a `(V.O.):` line). A pasted screenplay that
# happens to have scene headings but none of those falls back to the SHAPE
# itself — short, mostly one-sentence lines — and to prose if it is neither,
# which is the safe answer because prose is what this module always did.
_SCENE_HEADING_RE = re.compile(r"^\s*SCENE\s+\d+\s*[.:]", re.M | re.I)
_SCRIPT_MARKER_RE = re.compile(
    r"^\s*(?:CAST\s*$|LOGLINE:|CALL TO ACTION:|ON SCREEN:)|\(V\.O\.\)\s*:",
    re.M | re.I,
)
# A beat is one thing that happens. Longer than this, or carrying more than one
# full stop, and the line is a paragraph however it was headed.
_BEAT_LINE_CHARS = 200
_BEAT_LINE_SHARE = 0.7


def _is_beat_script(script_text: str) -> bool:
    """True when the script is already written one beat per line."""
    text = script_text or ""
    if not _SCENE_HEADING_RE.search(text):
        return False
    if _SCRIPT_MARKER_RE.search(text):
        return True

    body = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not _SCENE_HEADING_RE.match(line)
    ]
    if len(body) < 3:
        return False
    short = sum(
        1
        for line in body
        if len(line) <= _BEAT_LINE_CHARS
        and (line.count(".") + line.count("!") + line.count("?")) <= 1
    )
    return short / len(body) >= _BEAT_LINE_SHARE


# ---------------------------------------------------------------------------
# HOW LONG THE FILM IS MEANT TO BE
# ---------------------------------------------------------------------------
# ⚠ THE BREAKDOWN USED TO BE THE ONE STAGE THAT DIDN'T KNOW. The user approves a
# 30-second concept, `script_concept.concept_seconds()` reads 30 off it and
# `plan_agent.write_script()` is told to write 30 seconds of it — and then the
# number stopped there. A board came back 29 shots long and 1m 04s, more than
# twice the film that was approved. Reported. Nothing downstream can repair
# that: every extra panel is a drawing that was paid for.
#
# A shot holds the screen two to three seconds on average, so the length IS a
# shot count, and both are stated — a total to add up to, and a range to stay
# inside. The prompt's own ceiling is pulled down to match, with slack, so the
# model is never asked to choose between the budget and the end of the story.
_AVERAGE_SHOT_SECONDS = 2.5
_MIN_TARGET_SECONDS = 5
_MAX_TARGET_SECONDS = 3600


def _duration_budget(seconds) -> tuple[str, int | None]:
    """(prompt block, shot ceiling) for a film of known length; ("", None) if not.

    ⚠ THE CEILING IS PROMPT TEXT, NOT A TRUNCATION. Cutting the returned list to
    it would delete the END of the story, which is the one failure worse than a
    board that runs long — see `_coerce_shots`. So the budget is argued, and the
    model is told in as many words to MERGE rather than trim.
    """
    try:
        secs = int(seconds or 0)
    except (TypeError, ValueError):
        return "", None
    if secs <= 0:
        return "", None
    secs = max(_MIN_TARGET_SECONDS, min(secs, _MAX_TARGET_SECONDS))

    low = max(1, round(secs * 0.9))
    high = round(secs * 1.1)
    target = max(2, round(secs / _AVERAGE_SHOT_SECONDS))
    lo_shots = max(2, round(secs / 4))
    hi_shots = max(lo_shots + 1, round(secs / 2))
    ceiling = max(hi_shots + 2, round(secs / 1.5))

    block = (
        f"\u26a0 THIS FILM IS {secs} SECONDS LONG. That is the length the user "
        f"approved, and it is a hard target rather than a suggestion:\n"
        f"- Your shots' `duration_seconds` must ADD UP to about {secs} seconds "
        f"\u2014 between {low} and {high}. Add them up yourself before you answer, "
        f"and if the total is wrong, fix the list.\n"
        f"- At the two to three seconds an average shot holds the screen, that is "
        f"roughly {target} shots for the WHOLE script. Stay inside "
        f"{lo_shots}\u2013{hi_shots} shots. A {secs}-second film with {hi_shots * 2} "
        f"shots is not this film \u2014 it is this film cut into pieces too small to "
        f"read.\n"
        f"- IF YOU ARE RUNNING LONG, MERGE \u2014 NEVER TRIM. Every part of the "
        f"script must still be on the board, the ending included. Running long "
        f"means you split moments that did not need splitting, so join them back "
        f"up.\n"
    )
    return block, ceiling



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
                        "script_excerpt": types.Schema(type=types.Type.STRING),
                        "characters": types.Schema(
                            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)
                        ),
                        "dialogue": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                required=["line"],
                                properties={
                                    "character": types.Schema(type=types.Type.STRING),
                                    "line": types.Schema(type=types.Type.STRING),
                                },
                            ),
                        ),
                        "assets": types.Schema(
                            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)
                        ),
                        "location": types.Schema(type=types.Type.STRING),
                        "camera": types.Schema(type=types.Type.STRING),
                        # ⚠ DIRECTOR'S METADATA, NOT PROMPT MATERIAL — see the
                        # note in _coerce_shots and in storyboard_pipeline where
                        # the panel is built.
                        "movement": types.Schema(type=types.Type.STRING),
                        "duration_seconds": types.Schema(type=types.Type.INTEGER),
                    },
                ),
            ),
            "world": types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "setting": types.Schema(type=types.Type.STRING),
                    "culture": types.Schema(type=types.Type.STRING),
                    "ethnicity": types.Schema(type=types.Type.STRING),
                    "wardrobe": types.Schema(type=types.Type.STRING),
                    "environment": types.Schema(type=types.Type.STRING),
                    "notes": types.Schema(type=types.Type.STRING),
                    # The market, guessed from the script — the LOWEST-priority
                    # of the three layers (see market.resolve). Left blank
                    # unless the script actually says; a guess here would be the
                    # very "American by default" behaviour this is fixing.
                    "country": types.Schema(type=types.Type.STRING),
                    "language": types.Schema(type=types.Type.STRING),
                },
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
            "assets": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["name"],
                    properties={
                        "name": types.Schema(type=types.Type.STRING),
                        "category": types.Schema(type=types.Type.STRING),
                        "description": types.Schema(type=types.Type.STRING),
                    },
                ),
            ),
        },
    )


# ---------------------------------------------------------------------------
# Script traceability — tie each shot back to the lines it came from
# ---------------------------------------------------------------------------
# The model is asked to quote the script verbatim, but models paraphrase. So a
# quote is never trusted on its own: it is matched back against the real script
# and REPLACED by the actual lines found there. If it can't be located, the shot
# simply carries no script line — a blank box is honest, an invented "your
# script says…" is not.
_WS_RE = re.compile(r"\s+")
# A quote longer than this is a runaway (the model handing back half the script)
# — it gets trimmed at a word boundary rather than swallowing the shot card.
MAX_EXCERPT_CHARS = 420


def _norm(text: str) -> str:
    """Whitespace- and case-normalised text, for matching only."""
    return _WS_RE.sub(" ", text or "").strip().lower()


def _flatten_script(text: str) -> tuple[str, list[int]]:
    """Normalised one-line script + a per-character map back to ORIGINAL offsets.

    Mapping to character offsets (not line numbers) is what lets a shot quote ONE
    sentence out of a long paragraph. Mapping to lines meant every shot in a
    single-paragraph script resolved to the same whole line — five shots showing
    identical text.
    """
    flat: list[str] = []
    origin: list[int] = []
    in_space = True  # skips leading whitespace
    for i, ch in enumerate(text or ""):
        if ch.isspace():
            if not in_space:
                flat.append(" ")
                origin.append(i)
                in_space = True
            continue
        flat.append(ch.lower())
        origin.append(i)
        in_space = False
    # Drop a trailing separator space so spans can't end past the last word.
    if flat and flat[-1] == " ":
        flat.pop()
        origin.pop()
    return "".join(flat), origin


def _find_span(flat: str, excerpt: str, since: int = 0) -> tuple[int, int, str] | None:
    """Character span of `excerpt` within `flat`, or None if it isn't really there.

    Returns `(start, end, kind)` where kind is "exact" (the model quoted the
    script word for word) or "fuzzy" (only the head and tail of the quote were
    findable — the model paraphrased the middle, and the span was widened to the
    real text between them).

    Exact match first. Failing that, anchor on the longest word-PREFIX that does
    appear (models drift at the tail of a quote more than at its head) and
    stretch to the longest word-SUFFIX still findable after it. The result is
    rejected unless it covers at least half the quote, so a single coincidental
    phrase can't pass as a match.

    That fuzzy path is a real leniency: half a quote can be paraphrase and still
    resolve. The kind is reported back so a caller can tell a verbatim quote from
    a reconstructed one instead of both looking equally solid on a shot card.

    `since` is where the PREVIOUS shot's quote ended. Shots run in reading order,
    so a match at or after that point is preferred; we only fall back to a global
    search when the text genuinely doesn't appear again later.
    """
    if not excerpt or not flat:
        return None

    def _exact(frm: int) -> int:
        return flat.find(excerpt, frm)

    for frm in (since, 0) if since else (0,):
        pos = _exact(frm)
        if pos >= 0:
            return pos, pos + len(excerpt), "exact"

    words = excerpt.split(" ")
    if len(words) < 5:  # too short to anchor safely
        return None

    for frm in (since, 0) if since else (0,):
        start, head_len = -1, 0
        for k in range(len(words), 3, -1):
            probe = " ".join(words[:k])
            p = flat.find(probe, frm)
            if p >= 0:
                start, head_len = p, len(probe)
                break
        if start < 0:
            continue

        end = start + head_len
        for k in range(len(words), 3, -1):
            probe = " ".join(words[-k:])
            p = flat.find(probe, start)
            if p >= 0:
                end = max(end, p + len(probe))
                break
        if (end - start) >= len(excerpt) * 0.5:
            return start, end, "fuzzy"
    return None


def _attach_script_lines(shots: list[dict], script_text: str) -> None:
    """Resolve each shot's quoted excerpt to the real script text, in place.

    Sets `script_line` to EXACTLY the matched passage as it appears in the script
    — the sentence(s) that became this shot, not the whole line containing them,
    so shots from one long paragraph each get their own text. Also sets the
    1-based `script_line_start` / `script_line_end` for display, and
    `script_line_match` to "exact" or "fuzzy" so a paraphrased quote that only
    half-matched is distinguishable from one the model really copied.
    Unlocatable quotes leave the fields blank.
    """
    text = script_text or ""
    flat, origin = _flatten_script(text)
    cursor = 0  # shots run in reading order; prefer matches after the last one

    for shot in shots:
        excerpt = _norm(shot.pop("script_excerpt", ""))
        span = _find_span(flat, excerpt, since=cursor)
        if not span:
            continue
        start_flat, end_flat, kind = span
        start = origin[start_flat]
        end = origin[min(end_flat, len(origin)) - 1] + 1
        passage = text[start:end].strip()
        if not passage:
            continue
        if len(passage) > MAX_EXCERPT_CHARS:  # runaway quote — trim at a word
            passage = passage[:MAX_EXCERPT_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        shot["script_line"] = passage
        shot["script_line_start"] = text.count("\n", 0, start) + 1
        shot["script_line_end"] = text.count("\n", 0, end - 1) + 1
        shot["script_line_match"] = kind
        cursor = end_flat


# Spoken lines per shot. A panel is one drawable moment, so a shot carrying more
# than a few lines is the model merging a whole conversation into one picture —
# the extras are dropped rather than allowed to bury the panel.
MAX_DIALOGUE_PER_SHOT = 6
MAX_DIALOGUE_CHARS = 300


# A shot is one moment. ⚠ THE CEILING IS THE POINT: a model asked for a length
# will occasionally answer 300, and a single 300-second "shot" quietly makes the
# runtime on the review step nonsense — which is the one number people will
# actually trust this field for.
MIN_SHOT_SECONDS = 1
MAX_SHOT_SECONDS = 30
DEFAULT_SHOT_SECONDS = 3


def _coerce_seconds(raw) -> int:
    """A shot's on-screen length, clamped. Junk or nothing → the default."""
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SHOT_SECONDS
    if seconds <= 0:
        return DEFAULT_SHOT_SECONDS
    return max(MIN_SHOT_SECONDS, min(seconds, MAX_SHOT_SECONDS))


def _coerce_dialogue(raw) -> list[dict]:
    """Normalise a shot's spoken lines to [{character, line}, …].

    A shot with nothing spoken in it returns an EMPTY list, and every consumer
    (review card, board caption, PDF) shows nothing at all for it — an empty
    "Dialogue" heading on a silent establishing shot is noise, not information.
    Entries with no `line` are dropped: a speaker with no words isn't dialogue.
    """
    if isinstance(raw, dict):  # a lone {character, line} object
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            # A bare string: keep it as an unattributed line rather than lose it.
            item = {"line": item}
        if not isinstance(item, dict):
            continue
        line = str(item.get("line", "") or "").strip()
        if not line:
            continue
        if len(line) > MAX_DIALOGUE_CHARS:
            line = line[:MAX_DIALOGUE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        out.append(
            {
                "character": str(item.get("character", "") or "").strip(),
                "line": line,
            }
        )
        if len(out) >= MAX_DIALOGUE_PER_SHOT:
            break
    return out


def _coerce_shots(raw) -> list[dict]:
    """Validate/normalise the model's JSON into a clean list of shot dicts."""
    if not isinstance(raw, list):
        raise ScriptBreakdownError("The model did not return a list of shots.")

    # Truncation loses the END of the story, so it is never silent. Beat-level
    # splitting makes a long script far more likely to reach the ceiling than it
    # used to be, and a board that just stops two-thirds of the way through with
    # no explanation is the worst way to find that out.
    if len(raw) > MAX_SHOTS:
        logger.warning(
            "[breakdown] the model returned %d shots; keeping the first %d. "
            "THE END OF THE SCRIPT IS NOT IN THIS BOARD — split the script and "
            "run it in parts.", len(raw), MAX_SHOTS,
        )

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
        assets = item.get("assets") or []
        if not isinstance(assets, list):
            assets = [str(assets)]
        shots.append(
            {
                "scene_number": int(item.get("scene_number", 1) or 1),
                "shot_number": int(item.get("shot_number", i) or i),
                "description": desc,
                "characters": [str(c).strip() for c in chars if str(c).strip()],
                # Empty whenever nothing is spoken in this shot — see _coerce_dialogue.
                "dialogue": _coerce_dialogue(item.get("dialogue")),
                "assets": [str(a).strip() for a in assets if str(a).strip()],
                "location": str(item.get("location", "")).strip(),
                "camera": str(item.get("camera", "")).strip(),
                # ⚠ THESE TWO NEVER REACH AN IMAGE PROMPT. A still panel cannot
                # show a camera move or a length; asking for one gets motion
                # blur, speed lines or a little arrow drawn INTO the frame —
                # the same class of artefact the anti-collage rules exist to
                # stop. They are for the shot card, the PDF and the animatic
                # step, where motion and timing are real. Exactly the
                # arrangement `dialogue` already has, for the same reason.
                "movement": str(item.get("movement", "")).strip(),
                "duration_seconds": _coerce_seconds(item.get("duration_seconds")),
                # Filled in by _attach_script_lines once the quote below has been
                # checked against the real script.
                "script_line": "",
                "script_line_start": None,
                "script_line_end": None,
                # "exact" | "fuzzy" | "" — how well the quote matched. See
                # _find_span; "" means it was never found in the script at all.
                "script_line_match": "",
                "script_excerpt": str(item.get("script_excerpt", "")).strip(),
            }
        )

    if not shots:
        raise ScriptBreakdownError(
            "No usable shots were produced from this script. Try a longer or "
            "clearer script."
        )
    return shots


def _normalise_scenes(shots: list[dict]) -> None:
    """Fix up scene / shot numbering in place, deterministically.

    The model is asked to divide the script into scenes, but it can still hand
    back something unusable — most often EVERY shot in scene 1. Three repairs,
    none of which invent information:

    1. Scenes are renumbered 1..N by RUN: the number increments each time the
       model's value changes. Screenplay convention is that coming back to an
       earlier location later is a new scene, so [1,1,2,2,1] → [1,1,2,2,3] and
       scene numbers never go backwards or leave gaps.
    2. If that still leaves the whole board in one scene BUT the shots name two
       or more different locations, the scenes are re-derived from consecutive
       runs of `location` — a change of place is the one scene boundary we can
       infer from the data with confidence. A single location stays one scene.
    3. `shot_number` is rewritten as the shot's position within its scene, so it
       always agrees with the scene numbering above it.
    """
    if not shots:
        return

    def _by_runs(key) -> int:
        """Number consecutive runs of `key(shot)` 1..N. Returns the last number."""
        scene = 0
        previous = object()  # sentinel: never equal to a real key
        for shot in shots:
            current = key(shot)
            if current != previous:
                scene += 1
                previous = current
            shot["scene_number"] = scene
        return scene

    last = _by_runs(lambda s: s.get("scene_number", 1))

    if last == 1:
        places = [str(s.get("location", "")).strip().lower() for s in shots]
        if len({p for p in places if p}) > 1:
            logger.info(
                "[breakdown] Model put every shot in scene 1 — deriving scenes "
                "from %d distinct locations instead.", len({p for p in places if p})
            )
            _by_runs(lambda s: str(s.get("location", "")).strip().lower())

    # Shot numbers restart inside each scene.
    position, current_scene = 0, None
    for shot in shots:
        if shot["scene_number"] != current_scene:
            current_scene, position = shot["scene_number"], 0
        position += 1
        shot["shot_number"] = position


# Possessives that put the OWNER in the picture. "Kabir's face" cannot be in
# frame without Kabir; "Kabir's bed" easily can. That distinction is the whole
# heuristic below, and it is why this is a word list rather than a grammar rule.
_BODY_PARTS = frozenset(
    ("face", "cheek", "eyes", "eye", "hand", "hands", "arm", "arms", "head",
     "hair", "mouth", "shoulder", "shoulders", "back", "chest", "leg", "legs",
     "foot", "feet", "finger", "fingers", "body", "fist", "nose", "ear", "ears",
     "chin", "brow", "forehead", "neck", "knee", "lap", "expression", "gaze",
     "form", "figure", "silhouette", "profile")
)


def _add_characters_named_in_descriptions(shots: list[dict], characters: list[dict]) -> None:
    """Put people the description shows in frame into the shot's `characters`.

    The list is what drives reference images and the written bible, so a person
    the sentence clearly puts on screen but the model forgot to list is drawn
    from nothing and comes back as a different person. Observed: "The slipper is
    mid-air, flying directly towards Kabir's face" with an EMPTY character list.

    Deliberately conservative, because adding somebody who is NOT in frame is
    the worse error — it invites the artist to draw them. A bare mention counts;
    a possessive counts only for a body part (see _BODY_PARTS), so "Kabir's
    cheek" adds Kabir and "Kabir's bedroom" does not.
    """
    names = [str(c.get("name", "")).strip() for c in characters or []]
    names = [n for n in names if n]
    if not names:
        return

    for shot in shots:
        desc = str(shot.get("description", "") or "")
        if not desc:
            continue
        present = {str(n).strip().lower() for n in (shot.get("characters") or [])}
        for name in names:
            if name.lower() in present:
                continue
            # Whole word only: "Ram" must not match "Rama".
            for m in re.finditer(rf"\b{re.escape(name)}\b(’s|'s)?", desc, re.IGNORECASE):
                if not m.group(1):
                    break  # a bare mention — they are in the shot
                # The next FEW words, not just one: descriptions modify the noun
                # ("Kabir's sleeping form", "Kabir's left hand", "Kabir's badly
                # bruised cheek"), and checking only the adjacent word missed
                # every one of those. Three is enough for the adjectives that
                # occur and short enough that "Kabir's bedroom floor by the bed"
                # still, correctly, finds nothing.
                nxt = [w.strip(".,;:!?'\"").lower() for w in desc[m.end():].split()[:3]]
                if any(w in _BODY_PARTS for w in nxt):
                    break  # "Kabir's face" — Kabir is in the shot
            else:
                continue
            shot.setdefault("characters", []).append(name)
            present.add(name.lower())
            logger.info(
                "[breakdown] shot %s: '%s' is in the description but was not "
                "listed — added, so they keep their look.",
                shot.get("shot_number"), name,
            )


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


# The story's visual world. Every field is optional text; the order here is the
# order it reads in a prompt and in the UI.
WORLD_FIELDS = ("setting", "culture", "ethnicity", "wardrobe", "environment", "notes")

# ⚠ THE BREAKDOWN'S MARKET GUESS IS THE WEAKEST OF THREE LAYERS, and it is kept
# separate from WORLD_FIELDS because it is a different kind of claim: the world
# is what the story IS, the market is who the film is FOR. The user's account
# default and this board's form both outrank it — see market.resolve(). Only
# `country` and `language` are guessable; currency and units are looked up from
# the country rather than asked for, so the model cannot invent a mismatch.
GUESSED_MARKET_FIELDS = ("country", "language")


def _coerce_world(raw) -> dict:
    """Normalise the world block to {field: str} over the fields we recognise.

    Market fields are carried through when present and simply absent when not,
    so the server can tell "the script said nothing" (no key) apart from a
    positive answer — the difference between falling back to the account
    default and overriding it with an empty string.
    """
    if not isinstance(raw, dict):
        return {}
    out = {f: str(raw.get(f, "") or "").strip() for f in WORLD_FIELDS}
    for f in GUESSED_MARKET_FIELDS:
        value = str(raw.get(f, "") or "").strip()
        if value:
            out[f] = value
    return out


# Categories we recognise for a locked asset. Anything else → "prop".
_ASSET_CATEGORIES = ("prop", "background")


def _coerce_assets(raw) -> list[dict]:
    """Normalise the asset list; dedupe by name (case-insensitive).

    Each asset = {name, category ('prop'|'background'), description}. An unknown
    or missing category falls back to 'prop' (a specific object).
    """
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
        category = str(item.get("category", "")).strip().lower()
        if category not in _ASSET_CATEGORIES:
            category = "prop"
        out.append(
            {
                "name": name,
                "category": category,
                "description": str(item.get("description", "")).strip(),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Grounding report — what, if anything, the model made up
# ---------------------------------------------------------------------------
# The quote check above protects ONE field (`script_line`). Everything else the
# model returns was taken on trust: a shot's `description` is what actually gets
# drawn, and the cast / asset lists drive reference generation. This section
# checks those against the script too and reports what doesn't line up.
#
# It REPORTS rather than deletes. A weak overlap score is evidence, not proof —
# a description legitimately adds framing words the script never used — so the
# call on whether a shot is wrong stays with the person reviewing the board.
#
# Deliberately NOT checked: whether `dialogue` lines are verbatim. The prompt
# asks the model to turn reported speech into spoken first person ("he declares
# the armour will make them kings" → "This armour will make us kings"), so
# rewording there is the feature working, not a hallucination. What IS checked
# is who the line is attributed to.

# Words a storyboard description legitimately contains that no script would —
# camera, framing and rendering vocabulary. Counting these as "grounded" would
# flatter every shot; counting them as invented would condemn every shot.
_CRAFT_WORDS = frozenset(
    """
    shot close closeup close-up wide medium long establishing extreme angle
    camera frame framing foreground background midground left right centre center
    over shoulder point view pov pan tilt zoom track dolly crane aerial overhead
    high low eye level dutch reverse two-shot insert cutaway silhouette profile
    focus blurred sharp light lighting lit shadow shadows dark bright dim glow
    warm cool colour color tone contrast composition scene panel visible seen
    shows showing sits stands stood standing sitting looks looking holds holding
    face faces facing behind front near beside across toward towards
    """.split()
)

# Ordinary English that carries no evidence either way.
_STOPWORDS = frozenset(
    """
    the and that with from into onto they them their there this these those
    have has had been being was were will would could should must about above
    after again against because before below between both down during each few
    more most other some such than then they very what when where which while
    who whom why your yours himself herself itself themselves what's
    """.split()
)

_WORD_RE = re.compile(r"[a-z']{3,}")

# Below this share of its content words appearing anywhere in the script, a
# description is flagged for review. Deliberately low: descriptions rephrase.
MIN_DESCRIPTION_OVERLAP = 0.30


def _content_words(text: str) -> set[str]:
    """Meaningful lowercase words in `text` — no stopwords, no craft vocabulary."""
    words = set(_WORD_RE.findall(_norm(text)))
    return {w for w in words if w not in _STOPWORDS and w not in _CRAFT_WORDS}


def _describes_script(description: str, script_words: set[str]) -> float:
    """Share of a description's content words that occur anywhere in the script.

    A blunt lexical measure, not comprehension: it catches a shot built out of
    nouns the script never contains (an invented character, an invented place),
    and stays quiet about rephrasing. Returns 1.0 for a description with no
    content words at all, so pure camera directions aren't flagged.
    """
    words = _content_words(description)
    if not words:
        return 1.0
    return len(words & script_words) / len(words)


def _in_script(name: str, flat_script: str) -> bool:
    """True when every substantial token of `name` appears in the script.

    Substring rather than whole-word matching, so a possessive or inflected
    mention ("Lubdhaka's bow") still grounds the name "Lubdhaka".
    """
    tokens = [t for t in _WORD_RE.findall(_norm(name)) if t not in _STOPWORDS]
    if not tokens:
        return True  # nothing checkable — don't manufacture a warning
    return all(t in flat_script for t in tokens)


def build_grounding_report(
    shots: list[dict],
    characters: list[dict],
    assets: list[dict],
    script_text: str,
) -> dict:
    """Measure how much of the breakdown is actually supported by the script.

    Returns counts plus the specific items that look invented. `warnings` is a
    short human-readable list suitable for showing to the writer; everything
    else is there for logging and debugging.
    """
    flat_script, _ = _flatten_script(script_text or "")
    script_words = _content_words(script_text or "")

    exact = sum(1 for s in shots if s.get("script_line_match") == "exact")
    fuzzy = sum(1 for s in shots if s.get("script_line_match") == "fuzzy")
    missing = len(shots) - exact - fuzzy

    weak: list[dict] = []
    for shot in shots:
        overlap = _describes_script(shot.get("description", ""), script_words)
        if overlap < MIN_DESCRIPTION_OVERLAP:
            weak.append(
                {
                    "scene_number": shot.get("scene_number"),
                    "shot_number": shot.get("shot_number"),
                    "overlap": round(overlap, 2),
                }
            )

    cast = {c["name"].lower() for c in characters}
    listed_assets = {a["name"].lower() for a in assets}

    unknown_characters = sorted(
        c["name"] for c in characters if not _in_script(c["name"], flat_script)
    )
    unknown_assets = sorted(
        a["name"] for a in assets if not _in_script(a["name"], flat_script)
    )

    # Names used inside a shot that never made it into the cast/asset list. The
    # prompt asks for the SAME spelling in both, so a mismatch means either an
    # invented name or a spelling drift — both break reference lookup at Stage B.
    uncast: set[str] = set()
    unlisted: set[str] = set()
    speakers: set[str] = set()
    for shot in shots:
        uncast.update(n for n in shot.get("characters", []) if n.lower() not in cast)
        unlisted.update(n for n in shot.get("assets", []) if n.lower() not in listed_assets)
        speakers.update(
            d["character"]
            for d in shot.get("dialogue", [])
            if d.get("character") and d["character"].lower() not in cast
        )

    report = {
        "shots_total": len(shots),
        "quotes_exact": exact,
        "quotes_fuzzy": fuzzy,
        "quotes_missing": missing,
        "quote_rate": round((exact + fuzzy) / len(shots), 3) if shots else 0.0,
        "weak_descriptions": weak,
        "unknown_characters": unknown_characters,
        "unknown_assets": unknown_assets,
        "uncast_shot_characters": sorted(uncast),
        "unlisted_shot_assets": sorted(unlisted),
        "uncast_speakers": sorted(speakers),
        "warnings": [],
    }

    warnings: list[str] = []
    if missing:
        warnings.append(
            f"{missing} of {len(shots)} shots quote text that isn't in the script — "
            f"those panels show no script line."
        )
    if fuzzy:
        warnings.append(
            f"{fuzzy} shot(s) only partly matched the script; their quote was "
            f"reconstructed from the surrounding text."
        )
    if weak:
        warnings.append(
            f"{len(weak)} shot description(s) share little wording with the script "
            f"and may contain invented detail."
        )
    if unknown_characters:
        warnings.append(
            "Cast not found in the script: " + ", ".join(unknown_characters) + "."
        )
    if unknown_assets:
        warnings.append(
            "Assets not found in the script: " + ", ".join(unknown_assets) + "."
        )
    if uncast:
        warnings.append(
            "Characters used in shots but missing from the cast list: "
            + ", ".join(sorted(uncast)) + "."
        )
    if unlisted:
        warnings.append(
            "Assets used in shots but missing from the asset list: "
            + ", ".join(sorted(unlisted)) + "."
        )
    if speakers:
        warnings.append(
            "Dialogue attributed to non-cast speakers: " + ", ".join(sorted(speakers)) + "."
        )
    report["warnings"] = warnings
    return report


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def break_down_script(
    script_text: str,
    provider: str | None = None,
    max_shots: int = MAX_SHOTS,
    genre: str | None = None,
    brand_name: str | None = None,
    seconds: int | None = None,
    beats: bool | None = None,
) -> dict:
    """Break a raw script into a storyboard shot list + a cast list.

    Args:
        script_text: The raw script / story text to parse.
        provider: "vertex" or "gemini". Defaults to TEXT_PROVIDER env (or "vertex").
        max_shots: Upper bound on the number of shots to return.
        genre: Optional genre — shapes the tone / pacing of the breakdown.
        brand_name: The product's real name, so a writer's "[Your App Name]"
            never survives into a shot description.
        seconds: HOW LONG THE FINISHED FILM IS MEANT TO BE. The number the
            user approved on the concept card. Without it the breakdown has no
            idea whether it is boarding a 15-second ad or a 3-minute short, and
            a 30-second concept came back as a 1m 04s, 29-shot board. Pass it
            whenever it is known; None means "no target", which is honest for a
            script the user simply pasted.
        beats: Whether the script is ALREADY written one beat per line. None
            (the default) works it out with `_is_beat_script`; pass True/False
            only where the caller knows better than the fingerprint.

    Returns:
        {"shots": [{scene_number, shot_number, description, characters[],
        dialogue[], assets[], location, camera, script_line, script_line_start,
        script_line_end}, …],
        "characters": [{name, description}, …],
        "assets": [{name, category, description}, …],
        "world": {setting, culture, ethnicity, wardrobe, environment, notes},
        "grounding": {…}}.

        `grounding` is the hallucination report — how many quotes matched the
        script exactly vs. were reconstructed vs. weren't found, which shot
        descriptions share almost no wording with the script, and which cast /
        asset names don't appear in it. See build_grounding_report. Its
        `warnings` list is written for a human; the rest is for logs.

        `world` is the story's region/period/culture read from the script. It is
        prefixed onto EVERY image prompt (cast, props, backgrounds, panels) so a
        Shiva Purana script draws Indian characters rather than the image model's
        Western default — see gemini_client.build_world_context().

        `script_line` is the VERBATIM script text this shot was drawn from, with
        its 1-based line range — empty when the model's quote couldn't be found
        in the script. `script_line_match` says how it matched ("exact",
        "fuzzy", or "" when it wasn't found), so a reconstructed quote is
        distinguishable from one the model really copied.

        `dialogue` is the lines spoken in that shot as [{character, line}, …],
        and is EMPTY for a shot where nobody speaks. It is shown to the user on
        the review card, the board and the PDF, but is deliberately NOT fed into
        the image prompt — image models draw the words as speech bubbles.

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

    # ⚠ TWO DECISIONS BEFORE THE PROMPT IS BUILT, AND THEY ARE THE SAME ONE:
    # how finely to cut. A script that is already one beat per line must not be
    # cut again, and a film with a known length has a shot count it cannot
    # exceed. Together they are what stops a 30-second concept coming back as
    # 29 panels, a third of them the same picture.
    beat_script = _is_beat_script(text) if beats is None else bool(beats)
    budget, ceiling = _duration_budget(seconds)
    if ceiling:
        capped = max(1, min(capped, ceiling))
    logger.info(
        "[breakdown] script reads as %s; target %s, ceiling %d shots.",
        "BEATS (one line = one shot)" if beat_script else "prose",
        f"{int(seconds)}s" if seconds else "none given",
        capped,
    )

    density = (_DENSITY_BEATS if beat_script else _DENSITY_PROSE).format(
        max_shots=capped
    )
    prompt = _PROMPT_TEMPLATE.format(
        density=density, budget=budget, speech=_SPEECH_RULE, script=text
    )
    if genre and genre.strip():
        prompt = (
            f"Genre: {genre.strip()}. Shape the tone, pacing and shot choices to "
            f"fit this genre.\n\n" + prompt
        )
    # ⚠ THE BRAND'S REAL NAME, BECAUSE A PLACEHOLDER SURVIVES ALL THE WAY TO THE
    # SCREEN. One reported film went out with "That's why [Your App Name] is
    # built for speed" burnt into its captions — the writer's placeholder,
    # copied faithfully into a shot description and then read aloud. If we know
    # the name, the breakdown has no excuse to keep the brackets.
    if brand_name and brand_name.strip():
        prompt = (
            f'The product this film advertises is called "{brand_name.strip()}". '
            f"Wherever the script uses a placeholder for it — [Your App Name], "
            f"[Brand], YOUR_APP or similar — write the real name instead. Never "
            f"carry a bracketed placeholder into a shot description or a line of "
            f"dialogue.\n\n" + prompt
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
                    system_instruction=_system_instruction(beat_script),
                    response_mime_type="application/json",
                    response_schema=_breakdown_schema(),
                    **_sampling_kwargs(),
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

            # Tolerate either an object {shots, characters, assets} or a bare list.
            shots_raw = raw.get("shots") if isinstance(raw, dict) else raw
            chars_raw = raw.get("characters") if isinstance(raw, dict) else []
            assets_raw = raw.get("assets") if isinstance(raw, dict) else []
            world = _coerce_world(raw.get("world") if isinstance(raw, dict) else None)
            shots = _coerce_shots(shots_raw)
            # Sequential scenes + per-scene shot numbers, whatever the model did.
            _normalise_scenes(shots)
            # Tie each shot back to the lines it came from (drops quotes that
            # aren't actually in the script).
            _attach_script_lines(shots, text)
            characters = _coerce_characters(chars_raw)
            # A person the description puts in frame but leaves out of
            # `characters` gets no reference and no bible entry, and is redrawn
            # as a stranger. The prompt asks for this; this makes it true.
            _add_characters_named_in_descriptions(shots, characters)
            assets = _coerce_assets(assets_raw)
            grounding = build_grounding_report(shots, characters, assets, text)
            traced = sum(1 for s in shots if s.get("script_line"))
            spoken = sum(1 for s in shots if s.get("dialogue"))
            scenes = shots[-1]["scene_number"] if shots else 0
            logger.info(
                "[breakdown] Produced %d shots in %d scene(s) (%d traced to "
                "script lines, %d with dialogue), %d characters, %d assets. "
                "World: %s / %s",
                len(shots), scenes, traced, spoken, len(characters), len(assets),
                world.get("culture") or "—", world.get("ethnicity") or "—",
            )
            logger.info(
                "[breakdown] Grounding: quotes %d exact / %d fuzzy / %d missing "
                "(rate %.0f%%), %d weak description(s).",
                grounding["quotes_exact"], grounding["quotes_fuzzy"],
                grounding["quotes_missing"], grounding["quote_rate"] * 100,
                len(grounding["weak_descriptions"]),
            )
            for warning in grounding["warnings"]:
                logger.warning("[breakdown] %s", warning)
            return {
                "shots": shots,
                "characters": characters,
                "assets": assets,
                "world": world,
                "grounding": grounding,
            }

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


# ---------------------------------------------------------------------------
# ONE SHOT THAT ISN'T IN THE SCRIPT — the timeline's "generate a shot between"
# ---------------------------------------------------------------------------
# ⚠ THE OPPOSITE JOB TO THE BREAKDOWN ABOVE, and that is why it has its own
# sampling. `break_down_script` is EXTRACTION — the same script must give the
# same shot list twice, hence temperature 0 and a fixed seed. This is INVENTION:
# the shot being asked for is not in the script at all, and the button that asks
# for it is pressed again when the first answer isn't right. A deterministic
# suggestion would return the identical sentence every press, which reads as the
# button being broken.
_INFILL_SYSTEM = (
    "You are a professional film storyboard supervisor. You are given two "
    "CONSECUTIVE shots of a film and asked for the ONE shot that belongs "
    "between them. Answer with the shot description only — one or two "
    "sentences of what the camera SEES, present tense, concrete and visual. "
    "No shot number, no label, no camera jargon heading, no quotation marks, "
    "no dialogue, and never any words that are to be drawn INTO the picture."
)

_INFILL_TEMPERATURE = 0.9

# The furniture a chat model puts around a one-line answer: a shot heading, a
# bullet, a number, a pair of quotes. Every one of them would be typed straight
# into an image prompt and drawn — a model handed "1." draws a numeral in the
# corner of the picture — so it comes off here.
_SHOT_LINE_FURNITURE = re.compile(
    r"^\s*(?:shot\s*\d+\s*[:.\-—]\s*|[-*•]\s*|\d+[.)]\s*)+", re.I
)


def tidy_shot_line(text: str) -> str:
    """One shot description, with the chat furniture taken off the front.

    ⚠ PUBLIC AND SEPARATE FROM THE CALL, so it can be checked without spending
    anything — see `tests/shot_infill_check.py`. The prefixes stack ("Shot 4: -
    a low angle…"), hence the trailing `+`.
    """
    return _SHOT_LINE_FURNITURE.sub("", str(text or "")).strip().strip('"').strip()


def suggest_shot_between(
    previous: str = "",
    following: str = "",
    outline: list[str] | None = None,
    notes: str = "",
    title: str = "",
    provider: str | None = None,
) -> str:
    """Write the shot that belongs BETWEEN two shots. Returns a description.

    `previous` and `following` are the shots either side — either may be empty,
    which is the honest description of "generate a shot before the first one" or
    "after the last one", and the prompt says which case it is rather than
    pretending there is a neighbour.

    `outline` is the surrounding stretch of the film in play order, so the
    suggestion is written against the story's shape rather than against two
    sentences in isolation — the same reasoning behind `story_context` on the
    image side (see gemini_client.build_flow_context).

    `notes` is whatever the user has already typed. It is STEERING, not a
    replacement: the model is told to honour it and still write a shot that fits
    between the two.

    Raises `ScriptBreakdownError` with a readable reason — this is reached from
    a route and the failure the caller wants is the one the user needs to read.
    """
    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)

    lines: list[str] = []
    if title.strip():
        lines.append(f"Film: {title.strip()}")
    if outline:
        lines.append("The stretch of the film this sits in, in order:")
        lines.extend(f"  {n + 1}. {shot}" for n, shot in enumerate(outline))
        lines.append("")

    if previous.strip() and following.strip():
        lines.append(f"The shot BEFORE the gap: {previous.strip()}")
        lines.append(f"The shot AFTER the gap: {following.strip()}")
        lines.append(
            "Write the ONE shot that goes in the gap. It must follow on from the "
            "first and lead into the second — a beat that is currently missing "
            "between them, not a restatement of either."
        )
    elif following.strip():
        lines.append(f"The film currently OPENS on this shot: {following.strip()}")
        lines.append(
            "Write the ONE shot that should come immediately BEFORE it — the beat "
            "that sets it up."
        )
    elif previous.strip():
        lines.append(f"The film currently ENDS on this shot: {previous.strip()}")
        lines.append(
            "Write the ONE shot that should come immediately AFTER it — the beat "
            "that follows from it."
        )
    else:
        # Neither neighbour said anything about itself. Rare (a clip with no
        # board wording and no label), and a refusal here would be worse than a
        # generic opening shot the user can rewrite.
        lines.append(
            "There is no wording for the shots either side. Write one "
            "establishing shot that could sit anywhere in this film."
        )

    if notes.strip():
        lines.append("")
        lines.append(
            f"The director has already written this much, and it must be honoured "
            f"in your answer: {notes.strip()}"
        )

    prompt = "\n".join(lines)

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=[prompt],
            config=types.GenerateContentConfig(
                system_instruction=_INFILL_SYSTEM,
                temperature=_env_float("TEXT_INFILL_TEMPERATURE", _INFILL_TEMPERATURE),
            ),
        )
    except Exception as e:  # noqa: BLE001 — surface a clear reason
        logger.error("[infill] shot suggestion failed: %s", e)
        raise ScriptBreakdownError(f"Text API error: {e}") from None

    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise ScriptBreakdownError(
            "The model returned nothing (it may have been blocked by a safety "
            "filter). Try describing the shot yourself."
        )
    return tidy_shot_line(text)
