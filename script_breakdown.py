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
MAX_SHOTS = 60


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
_SYSTEM_INSTRUCTION = (
    "You are a professional film storyboard supervisor. You read a script and "
    "break it into a clear, ordered SHOT LIST for a storyboard artist. Each shot "
    "is ONE storyboard panel: a single moment we can draw. Keep descriptions "
    "concrete and visual (what the camera sees), not internal thoughts. Infer a "
    "sensible location and camera angle when the script doesn't state them. Do "
    "NOT invent major plot the script doesn't imply. Split long actions into "
    "multiple shots when the visual clearly changes.\n\n"
    "You also identify the story's WORLD — its region, period, culture and "
    "religious tradition — and make sure the people, clothing, buildings and "
    "objects you describe belong to THAT world. An artist reading your breakdown "
    "must never have to guess someone's ethnicity or dress. Judge this from the "
    "script's names, places, deities, festivals, food and language: a story about "
    "Lubdhaka and the Shiva Purana is ancient India and its people are Indian, "
    "just as a story about Kenji in Kyoto is Japanese. Never fall back on a "
    "generic Western/European default."
)

_PROMPT_TEMPLATE = (
    "Break the following script into a storyboard shot list, a short cast list, "
    "AND an asset list.\n"
    "Return between 1 and {max_shots} shots, in reading order.\n"
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
    "  - description: one vivid sentence describing what we SEE in this panel\n"
    "  - script_excerpt: the EXACT sentence(s) from the script THIS shot is drawn "
    "from, copied VERBATIM — same words, same spelling, no paraphrasing, no "
    "summarising, nothing added. Quote ONLY the part that becomes this one panel: "
    "usually a single sentence or clause, never the whole paragraph and never the "
    "whole scene. Each shot must quote a DIFFERENT passage, and the quotes must "
    "move FORWARD through the script in shot order — if two shots would carry the "
    "same text, you have split the wrong sentence. This is shown to the writer to "
    "point at the exact words that became this panel, so a short exact quote is "
    "far better than a long or rewritten one.\n"
    "  - characters: list of character names visible in the shot (empty if none)\n"
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
    "line, and never repeat the same line in two shots.\n"
    "  - assets: list of asset names visible in the shot — the key recurring "
    "props/objects AND the background/location — using the SAME names as the "
    "asset list below (empty if none)\n"
    "  - location: where the shot takes place\n"
    "  - camera: the shot type / angle, e.g. 'wide establishing', 'close-up', "
    "'over-the-shoulder', 'medium two-shot'\n"
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


def _find_span(flat: str, excerpt: str, since: int = 0) -> tuple[int, int] | None:
    """Character span of `excerpt` within `flat`, or None if it isn't really there.

    Exact match first. Failing that, anchor on the longest word-PREFIX that does
    appear (models drift at the tail of a quote more than at its head) and
    stretch to the longest word-SUFFIX still findable after it. The result is
    rejected unless it covers at least half the quote, so a single coincidental
    phrase can't pass as a match.

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
            return pos, pos + len(excerpt)

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
            return start, end
    return None


def _attach_script_lines(shots: list[dict], script_text: str) -> None:
    """Resolve each shot's quoted excerpt to the real script text, in place.

    Sets `script_line` to EXACTLY the matched passage as it appears in the script
    — the sentence(s) that became this shot, not the whole line containing them,
    so shots from one long paragraph each get their own text. Also sets the
    1-based `script_line_start` / `script_line_end` for display. Unlocatable
    quotes leave the fields blank.
    """
    text = script_text or ""
    flat, origin = _flatten_script(text)
    cursor = 0  # shots run in reading order; prefer matches after the last one

    for shot in shots:
        excerpt = _norm(shot.pop("script_excerpt", ""))
        span = _find_span(flat, excerpt, since=cursor)
        if not span:
            continue
        start = origin[span[0]]
        end = origin[min(span[1], len(origin)) - 1] + 1
        passage = text[start:end].strip()
        if not passage:
            continue
        if len(passage) > MAX_EXCERPT_CHARS:  # runaway quote — trim at a word
            passage = passage[:MAX_EXCERPT_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        shot["script_line"] = passage
        shot["script_line_start"] = text.count("\n", 0, start) + 1
        shot["script_line_end"] = text.count("\n", 0, end - 1) + 1
        cursor = span[1]


# Spoken lines per shot. A panel is one drawable moment, so a shot carrying more
# than a few lines is the model merging a whole conversation into one picture —
# the extras are dropped rather than allowed to bury the panel.
MAX_DIALOGUE_PER_SHOT = 6
MAX_DIALOGUE_CHARS = 300


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
                # Filled in by _attach_script_lines once the quote below has been
                # checked against the real script.
                "script_line": "",
                "script_line_start": None,
                "script_line_end": None,
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


def _coerce_world(raw) -> dict:
    """Normalise the world block to {field: str} over WORLD_FIELDS only."""
    if not isinstance(raw, dict):
        return {}
    return {f: str(raw.get(f, "") or "").strip() for f in WORLD_FIELDS}


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
# Public API
# ---------------------------------------------------------------------------
def break_down_script(
    script_text: str,
    provider: str | None = None,
    max_shots: int = MAX_SHOTS,
    genre: str | None = None,
) -> dict:
    """Break a raw script into a storyboard shot list + a cast list.

    Args:
        script_text: The raw script / story text to parse.
        provider: "vertex" or "gemini". Defaults to TEXT_PROVIDER env (or "vertex").
        max_shots: Upper bound on the number of shots to return.
        genre: Optional genre — shapes the tone / pacing of the breakdown.

    Returns:
        {"shots": [{scene_number, shot_number, description, characters[],
        dialogue[], assets[], location, camera, script_line, script_line_start,
        script_line_end}, …],
        "characters": [{name, description}, …],
        "assets": [{name, category, description}, …],
        "world": {setting, culture, ethnicity, wardrobe, environment, notes}}.

        `world` is the story's region/period/culture read from the script. It is
        prefixed onto EVERY image prompt (cast, props, backgrounds, panels) so a
        Shiva Purana script draws Indian characters rather than the image model's
        Western default — see gemini_client.build_world_context().

        `script_line` is the VERBATIM script text this shot was drawn from, with
        its 1-based line range — empty when the model's quote couldn't be found
        in the script.

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
    prompt = _PROMPT_TEMPLATE.format(max_shots=capped, script=text)
    if genre and genre.strip():
        prompt = (
            f"Genre: {genre.strip()}. Shape the tone, pacing and shot choices to "
            f"fit this genre.\n\n" + prompt
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
                    system_instruction=_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=_breakdown_schema(),
                    temperature=0.4,
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
            assets = _coerce_assets(assets_raw)
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
            return {
                "shots": shots,
                "characters": characters,
                "assets": assets,
                "world": world,
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
