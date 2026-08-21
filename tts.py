"""Dialogue → a spoken voiceover track, timed to the shots it belongs to.

The board already knows who says what and in which shot; the animatic already
knows where that shot sits on the timeline. So a scratch voiceover does not need
to be recorded, or hand-synced: every line's target time is data we already
hold, and this module reads the lines aloud so the caller can lay them there.

⚠ WHERE A LINE GOES IS NOT DECIDED HERE ANY MORE. The shot a line belongs to is
STRETCHED to cover it and the shots after it are pushed along, and that is one
clock over the pictures and the sound together — it lives with the frames, in
`_lay_out_speech` (`server/animatics.py`). This module speaks, measures, and
lays finished blobs where it is told. See the note above `speak_lines`.

⚠ THIS MODULE SPENDS AI QUOTA, and follows the same discipline as ✨ Animate and
`captions.py`: `estimate()` is free and is shown before the button that spends,
`MAX_CHARACTERS` caps one run, and the estimate is computed from the same
characters the run will send so the quote cannot drift from the work.

⚠ AND IT IS THE ONE PLACE IN THIS CODEBASE THAT KNOWS HOW LONG A SOUND IS
WITHOUT BEING TOLD. There is no ffprobe on an imageio-ffmpeg install (see
`video_assemble.py`), which is why every other audio duration here comes from
the caller. Generated speech is the exception because it arrives as RAW PCM at a
known rate: the number of bytes IS the duration, exactly, with no decoder. That
is what makes the returned line timings trustworthy enough to become captions.
"""

from __future__ import annotations

import io
import logging
import os
import re
import wave

from google.genai import types

import script_breakdown

logger = logging.getLogger(__name__)

# What the TTS models return: signed 16-bit little-endian PCM, mono, 24kHz.
# Not negotiable and not detected — it is the documented output format, and the
# duration arithmetic below depends on it being exactly this.
SAMPLE_RATE = 24_000
SAMPLE_WIDTH = 2
CHANNELS = 1

DEFAULT_TTS_MODEL = "gemini-2.5-flash-preview-tts"

# --- The cast: who can read a line, and who they sound like ------------------
# ⚠ THIS IS A CASTING TABLE, NOT A LIST OF NAMES, and that is the change. Six
# names in one dropdown is the right shape for "read the whole board in one
# voice" and the wrong shape for what was asked — "add character name too … with
# gender men/women, boy/girl, child and grand father like so gemini understand
# what charcetr age and gender … and set each dialouge voice artist". So a line
# carries a PERSONA (who is speaking), the persona casts a voice AND writes the
# stage direction the model is given, and the user can override either one.
#
# Still curated rather than the model's full thirty: every entry here is a part
# an animatic actually needs, and a wall of names is still a worse picker than a
# short one. `tone` is Google's own one-word description, shown beside the name
# so a voice means something before it has been heard.
CAST = (
    {"name": "Leda", "persona": "child", "tone": "Youthful"},
    {"name": "Puck", "persona": "boy", "tone": "Upbeat"},
    {"name": "Autonoe", "persona": "girl", "tone": "Bright"},
    {"name": "Fenrir", "persona": "young_man", "tone": "Excitable"},
    {"name": "Aoede", "persona": "young_woman", "tone": "Breezy"},
    {"name": "Charon", "persona": "man", "tone": "Informative"},
    {"name": "Orus", "persona": "man", "tone": "Firm"},
    {"name": "Kore", "persona": "woman", "tone": "Firm"},
    {"name": "Zephyr", "persona": "woman", "tone": "Bright"},
    {"name": "Algenib", "persona": "grandfather", "tone": "Gravelly"},
    {"name": "Gacrux", "persona": "grandmother", "tone": "Mature"},
    {"name": "Rasalgethi", "persona": "narrator", "tone": "Informative"},
)

VOICES = tuple(entry["name"] for entry in CAST)
# ⚠ UNCHANGED, and it has to be: it is the default on every stored request and
# on the two API bodies, so moving it would re-voice work already done.
DEFAULT_VOICE = "Kore"

# --- The personas: what the model is TOLD about the speaker ------------------
# ⚠ `direction` IS THE ONLY WAY AN AGE OR A SEX REACHES THE MODEL. A voice name
# is a timbre — it is not a character, and picking "Puck" does not tell the model
# it is reading a child. The direction is prepended to the line (see
# `prompt_for`), which is the documented way to steer this model's delivery.
#
# Deliberately short and plain. It is a stage direction, not a performance note:
# a long one starts getting read out loud.
#
# "" is a real entry — "say it as it comes", no direction at all — because a
# narrator's line and a line whose speaker the breakdown could not attribute are
# both better unsteered than steered wrongly.
PERSONAS: dict[str, dict] = {
    "": {"label": "As it comes", "voice": DEFAULT_VOICE, "direction": ""},
    "child": {"label": "Child", "voice": "Leda", "direction": "a small child"},
    "boy": {"label": "Boy", "voice": "Puck", "direction": "a young boy, bright and eager"},
    "girl": {"label": "Girl", "voice": "Autonoe", "direction": "a young girl, bright and quick"},
    "young_man": {
        "label": "Young man", "voice": "Fenrir",
        "direction": "a young man, quick and full of energy",
    },
    "young_woman": {
        "label": "Young woman", "voice": "Aoede",
        "direction": "a young woman, warm and easy",
    },
    "man": {"label": "Man", "voice": "Charon", "direction": "a grown man, low and steady"},
    "woman": {"label": "Woman", "voice": "Kore", "direction": "a grown woman, clear and steady"},
    "grandfather": {
        "label": "Grandfather", "voice": "Algenib",
        "direction": "an elderly man, gravelly and unhurried",
    },
    "grandmother": {
        "label": "Grandmother", "voice": "Gacrux",
        "direction": "an elderly woman, gentle and unhurried",
    },
    "narrator": {
        "label": "Narrator", "voice": "Rasalgethi",
        "direction": "a narrator reading over the scene, even and unhurried",
    },
}

# --- Spend guards -----------------------------------------------------------
# Characters per run. A scene of dialogue is a few thousand; a whole script is
# not something to read aloud by accident.
MAX_CHARACTERS = int(os.environ.get("API_MAX_VOICEOVER_CHARS", "8000"))
# Advisory list price per 1,000 characters, for the estimate. As everywhere else
# here: we quote, only Google bills, and the UI says so.
USD_PER_1K_CHARS = float(os.environ.get("API_VOICEOVER_USD_PER_1K", "0.012"))

# Silence left between two spoken lines when they would otherwise butt up
# against each other. Speech with no gap between lines sounds like one run-on
# sentence; this is the shortest pause that still reads as a new line.
GAP_MS = 220


class VoiceoverError(Exception):
    """Raised when dialogue can't be turned into a voiceover.

    Carries a human-readable reason so the API can say what went wrong.
    """


def tts_model_id() -> str:
    """The TTS model to use. Overridable, like every other model id here."""
    return os.environ.get("GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)


def resolve_voice(voice: str | None) -> str:
    """A known voice name, or the default. Unknown names fold down rather than
    failing: a request that would only ever produce a PAID error is refused
    before it is sent, and a voice name is not worth losing a run over."""
    name = (voice or "").strip()
    for known in VOICES:
        if known.lower() == name.lower():
            return known
    return DEFAULT_VOICE


def resolve_persona(persona: str | None) -> str:
    """A known persona key, or "" — which is a real answer, not a failure.

    Folds down for the same reason `resolve_voice` does: a persona arrives from
    a dropdown in the browser, and a stale one must cost a stage direction, not
    a paid run.
    """
    key = (persona or "").strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in PERSONAS and key else ""


def voice_for_persona(persona: str | None) -> str:
    """The voice this project casts for that kind of speaker."""
    return PERSONAS[resolve_persona(persona)]["voice"]


def direction_for(persona: str | None) -> str:
    """How the model is told to read for that kind of speaker. "" = plainly."""
    return PERSONAS[resolve_persona(persona)]["direction"]


def voice_for(line: dict, default: str | None = None) -> str:
    """WHICH VOICE READS THIS LINE, in the one order that can't surprise anyone.

    The line's own voice wins (the user picked it in the dialogue sheet), then
    the persona's casting, then the run's default. ⚠ THE PERSONA MUST NOT MASK
    THE RUN DEFAULT when there is no persona: `voice_for_persona("")` answers
    `DEFAULT_VOICE`, so asking it unconditionally would quietly re-cast every
    unattributed line as Kore however the picker at the top of the dialog was set.
    """
    named = str((line or {}).get("voice") or "").strip()
    if named:
        return resolve_voice(named)
    persona = resolve_persona((line or {}).get("persona"))
    if persona:
        return PERSONAS[persona]["voice"]
    return resolve_voice(default)


# --- Guessing a persona from the board's own words --------------------------
# ⚠ FREE, KEYWORD-ONLY, AND ALWAYS OVERRIDABLE. The obvious alternative is to
# ask a model who each character is, and it is the wrong trade twice over: it
# turns opening a dialog into a billable call, and it is not more right than this
# about a cast sheet that says "an elderly Brahmin priest, age 70". This reads
# the words the breakdown already wrote and offers an answer the user can change
# in one click, which is the whole design of the dialogue sheet.
#
# ⚠ AND IT DECLINES TO GUESS A SEX. "" is returned wherever the board does not
# say — the line is then read plainly rather than confidently in the wrong voice.
# The one exception is a child, because "child" is a persona of its own.
_MALE_WORDS = (
    "man", "men", "boy", "boys", "male", "father", "dad", "papa", "son", "brother",
    "uncle", "grandfather", "grandpa", "grandad", "granddad", "husband", "king",
    "prince", "sir", "mr", "he", "his", "himself", "gentleman", "lad", "guy",
    "monk", "priest", "dadaji", "nana", "chacha", "bhai", "beta",
)
_FEMALE_WORDS = (
    "woman", "women", "girl", "girls", "female", "mother", "mom", "mum", "maa",
    "sister", "aunt", "aunty", "grandmother", "grandma", "granny", "wife", "queen",
    "princess", "madam", "mrs", "ms", "miss", "she", "her", "herself", "lady",
    "didi", "behen", "nani", "dadi", "amma", "bahu",
)
_OLD_WORDS = (
    "old", "elderly", "aged", "ageing", "aging", "elder", "ancient", "veteran",
    "grandfather", "grandmother", "grandpa", "grandma", "granny", "grandad",
    "granddad", "dadaji", "dadi", "nana", "nani", "white-haired", "grey-haired",
    "gray-haired", "wrinkled", "frail", "sage",
)
_CHILD_WORDS = (
    "child", "children", "kid", "kids", "toddler", "infant", "baby", "boy", "boys",
    "girl", "girls", "schoolboy", "schoolgirl", "little",
)
_YOUNG_WORDS = (
    "young", "teenage", "teenager", "teen", "adolescent", "youth", "twenties",
    "college", "student", "youthful",
)
# "age 30", "aged 30", "30 years old", "30-year-old" — the shapes a cast sheet
# actually writes. A number is stronger evidence than an adjective, so it wins.
_AGE_RE = re.compile(r"\bage[d]?\s*[:\-]?\s*(\d{1,3})\b|\b(\d{1,3})[\s-]*year", re.I)


def _hits(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", text))


def persona_from(name: str = "", description: str = "") -> str:
    """Who this character sounds like, from what the board says about them.

    Returns a `PERSONAS` key, or "" when the board does not say enough. Reads the
    cast sheet's description AND the character's own name, because a board that
    skipped the cast step still calls them "GRANDFATHER" or "LITTLE BOY".
    """
    text = f"{name or ''} {description or ''}".lower()
    if not text.strip():
        return ""
    if re.search(r"\b(narrator|narration|voice[\s-]?over|v\.?o\.?|announcer)\b", text):
        return "narrator"

    male, female = _hits(text, _MALE_WORDS), _hits(text, _FEMALE_WORDS)
    sex = "m" if male > female else "f" if female > male else ""

    age = ""
    years = _AGE_RE.search(text)
    if years:
        n = int(years.group(1) or years.group(2) or 0)
        age = "child" if n < 13 else "young" if n < 25 else "adult" if n < 60 else "old"
    elif _hits(text, _OLD_WORDS):
        age = "old"
    elif _hits(text, _CHILD_WORDS):
        age = "child"
    elif _hits(text, _YOUNG_WORDS):
        age = "young"

    if age == "child":
        return "boy" if sex == "m" else "girl" if sex == "f" else "child"
    if not sex:
        return ""
    if age == "old":
        return "grandfather" if sex == "m" else "grandmother"
    if age == "young":
        return "young_man" if sex == "m" else "young_woman"
    return "man" if sex == "m" else "woman"


def prompt_for(line: dict) -> str:
    """EXACTLY what one line sends to the model — its direction and its words.

    ⚠ THE DIRECTION IS PART OF THE PROMPT, SO IT IS PART OF THE PRICE. `estimate`
    counts this string rather than the bare line, which is what keeps the number
    on the confirm dialog the price of the thing the button then does — the rule
    the whole of this module is built around.

    The shape (direction, colon, the line in quotes) is the one Google documents
    for steering this model, and the quotes are what keep the direction OUT of
    the audio: it reads what is quoted and treats the rest as instruction.
    """
    text = str((line or {}).get("text") or "").strip()
    if not text:
        return ""
    direction = direction_for((line or {}).get("persona"))
    if not direction:
        return text
    return f'Read this line as {direction}:\n"{text}"'


# ---------------------------------------------------------------------------
# The estimate — FREE, and shown before anything is spent
# ---------------------------------------------------------------------------
def estimate(lines: list[dict]) -> dict:
    """What reading these lines aloud should cost. Advisory; spends nothing.

    ⚠ COUNTS THE PROMPTS, NOT THE LINES — `prompt_for` is what the run sends, and
    a line with a persona sends its stage direction too. Counting the bare line
    would quote less than the run costs, which is the one direction an advisory
    price must never be wrong in.
    """
    prompts = [prompt_for(line) for line in (lines or [])]
    prompts = [p for p in prompts if p]
    characters = sum(len(p) for p in prompts)
    return {
        "lines": len(prompts),
        "characters": characters,
        "usd": round(characters / 1000.0 * USD_PER_1K_CHARS, 4),
        "model": tts_model_id(),
        "over_limit": characters > MAX_CHARACTERS,
        "limit_characters": MAX_CHARACTERS,
    }


# ---------------------------------------------------------------------------
# The model call — the half that costs money
# ---------------------------------------------------------------------------
def speak(text: str, *, voice: str | None = None, provider: str | None = None) -> bytes:
    """SPENDS QUOTA. Read one PROMPT aloud. Returns raw PCM (see the constants).

    ⚠ A PROMPT, NOT A LINE: `prompt_for` may have wrapped the words in a stage
    direction, and this is handed the finished thing. Building it here instead
    would put the price and the payload in two different functions, which is the
    one way the estimate can drift from the run.

    Raw PCM rather than a container, because the caller is about to lay several
    of these end to end at known offsets — and concatenating containers means
    decoding them again, with a decoder this install does not have.
    """
    line = (text or "").strip()
    if not line:
        raise VoiceoverError("There is nothing to read aloud.")

    client = script_breakdown.get_client(provider)
    model_id = tts_model_id()
    name = resolve_voice(voice)
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=[line],
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=name)
                    )
                ),
            ),
        )
    except Exception as exc:  # the SDK raises a wide family of transport errors
        raise VoiceoverError(f"The voiceover call failed: {exc}") from exc

    data = _audio_of(response)
    if not data:
        raise VoiceoverError(
            "The model returned no audio for that line — it may have been "
            "blocked by a safety filter."
        )
    return data


def _audio_of(response) -> bytes:
    """The PCM out of a genai response, or b"" if there isn't any.

    Walks the parts rather than assuming part[0]: a TTS response can carry a
    text part alongside the audio, and indexing blindly would hand back a
    string of prose to be written into a .wav header.
    """
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if data:
                return bytes(data)
    return b""


# ---------------------------------------------------------------------------
# PCM arithmetic — the part that needs no decoder
# ---------------------------------------------------------------------------
def pcm_duration_ms(pcm: bytes) -> int:
    """How long this PCM lasts. Exact, from the byte count alone."""
    frames = len(pcm) // (SAMPLE_WIDTH * CHANNELS)
    return int(round(frames * 1000 / SAMPLE_RATE))


def silence(duration_ms: int) -> bytes:
    """`duration_ms` of nothing, in the same PCM format."""
    frames = max(0, int(round(duration_ms * SAMPLE_RATE / 1000)))
    return b"\x00" * (frames * SAMPLE_WIDTH * CHANNELS)


def wav_bytes(pcm: bytes) -> bytes:
    """Wrap PCM in a WAV container — the only encoding step here.

    A WAV because ffmpeg reads it without question on the export path, the
    browser can play it back for scrubbing, and writing one needs nothing but
    the standard library.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Lines → sound, and the timings that come free with it
# ---------------------------------------------------------------------------
# ⚠ THERE USED TO BE ONE FUNCTION HERE (`synthesise_timed`) THAT DID BOTH THE
# SPEAKING AND THE LAYOUT, AND IT HAD TO BE SPLIT. It advanced its own clock by
# `line + GAP`, which is the right rule for audio and the wrong one for the
# timeline: the shot a line belongs to has a length of its own, and the caller
# now stretches that shot to cover the line and pushes the shots after it along
# (`_lay_out_speech` in `server/animatics.py`). Two clocks — one here for the
# sound, one there for the pictures — drift apart the moment a shot holds LONGER
# than its line, and a line then starts several seconds before the shot it
# belongs to appears. Reported as "caption and voicerover goes overlap other
# image shots".
#
# So there is exactly ONE clock now, it lives with the frames, and this module
# answers the two questions it can answer alone: how long is this shot's speech,
# and how do I lay finished blobs of it on one track.
def speak_lines(
    lines: list[dict],
    *,
    voice: str | None = None,
    provider: str | None = None,
    progress_cb=None,
) -> tuple[bytes, list[dict]]:
    """SPENDS QUOTA. Read ONE SHOT's lines, back to back, into a single blob.

    `lines` is `[{"text": …, "persona": …?, "voice": …?, "character": …?}]` — no
    times, because a shot's lines are read in order and the shot's own place on
    the timeline is the caller's business.

    Returns `(pcm, spans)` where each span is `{"start_ms", "end_ms", "text",
    "character"}` RELATIVE TO THE BLOB. The caller adds the shot's start to get
    timeline time, which is what the captions are built from.

    ⚠ THE GAP GOES *BETWEEN* LINES AND NEVER AFTER THE LAST ONE. A trailing gap
    would be silence the shot then has to hold for, on every shot, for nothing —
    the breath before the NEXT shot's line is the caller's to add (`GAP_MS`),
    once, where it can see whether there is a next line at all.
    """
    track = bytearray()
    spans: list[dict] = []
    for line in lines or []:
        prompt = prompt_for(line)
        if not prompt:
            continue
        if progress_cb:
            progress_cb(line)
        if track:
            track += silence(GAP_MS)
        at = pcm_duration_ms(bytes(track))
        pcm = speak(prompt, voice=voice_for(line, voice), provider=provider)
        track += pcm
        spans.append({
            "start_ms": at,
            "end_ms": at + pcm_duration_ms(pcm),
            # ⚠ THE WORDS, NOT THE PROMPT. These spans become captions, and a
            # caption reading "Read this line as a young boy:" is the stage
            # direction on screen.
            "text": str((line or {}).get("text") or "").strip(),
            "character": str((line or {}).get("character") or "").strip(),
        })
    return bytes(track), spans


def assemble(pieces: list[tuple[int, bytes]]) -> bytes:
    """Lay each blob of speech at its own moment on one track, as a WAV.

    `pieces` is `[(start_ms, pcm), …]` — where the caller decided each shot's
    speech goes. Padding is silence, and the arithmetic is exact because the
    byte count IS the duration (see the module header).

    ⚠ A PIECE IS NEVER MIXED INTO THE ONE BEFORE IT. If two overlap the later one
    is pushed to the end of the earlier rather than summed: this is one voice
    reading in turn, and two lines on top of each other is not a mix, it is a
    mistake nobody can edit their way out of. The caller's layout is what makes
    that branch unreachable; it is here so that a bug up there is audible as a
    late line rather than as a garbled one.
    """
    track = bytearray()
    for at, pcm in sorted(pieces or [], key=lambda piece: piece[0]):
        if not pcm:
            continue
        clock = pcm_duration_ms(bytes(track))
        if at > clock:
            track += silence(at - clock)
        track += pcm
    return wav_bytes(bytes(track))
