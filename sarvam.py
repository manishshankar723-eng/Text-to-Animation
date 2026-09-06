"""
sarvam.py — THE VOICEOVER, READ BY A MODEL THAT WAS BUILT FOR INDIAN SPEECH.

A provider client, like `deepgram.py`, `freesound.py`, `meshy.py` and `tripo.py`:
it talks to somebody else's HTTP API and knows nothing about FastAPI, jobs or the
timeline. The module that decides WHEN to call it is `tts.py`; the route that
files the result into a project is in `server/animatics.py`.

⚠ IT RETURNS THE SAME BYTES `tts.speak` HAS ALWAYS RETURNED — signed 16-bit
little-endian PCM, mono, 24 kHz, no container. Everything downstream of that
(the gap between lines, the shot fitting, the captions built from the timings,
the WAV written at the end) counts BYTES to know how long a sound is, and does
not own a decoder. A backend that handed back an MP3 would break every one of
those sums, so this one asks Sarvam for a WAV at exactly that rate and unwraps
it here — see `_pcm_of_wav`, which REFUSES anything that is not the house format
rather than letting a 22 kHz file quietly play 9% slow.

---------------------------------------------------------------------------
⚠ WHY THIS EXISTS, WHEN GEMINI ALREADY SPEAKS.
---------------------------------------------------------------------------
Not price — this is the DEARER of the two on list (₹30 per 10,000 characters for
bulbul:v3, about $0.036/1k, against the ~$0.012/1k we quote for Gemini TTS).
Two reasons, and both are about the films this app is actually pointed at:

  1. **Hinglish.** This app defines Hinglish as Hindi words in LATIN script —
     *"Shiv ji ki ye kahani aapne kabhi nahi suni hogi"* (`plan_agent.LANGUAGES`).
     A general TTS model reads that as English and produces something between an
     accent and a joke. Bulbul is trained on exactly this code-mixed input; it is
     the one thing it is better at than anything else on the market.
  2. **Eleven Indian languages, billed in rupees, by an Indian company.** Hindi,
     Bengali, Tamil, Telugu, Gujarati, Kannada, Malayalam, Marathi, Punjabi,
     Odia and Indian English. ₹100 of free credit gets a few tens of thousands
     of characters — enough to hear a whole board read aloud before deciding.

---------------------------------------------------------------------------
⚠ THE LANGUAGE PARAMETER IS THE DANGEROUS ONE. READ THIS BEFORE CHANGING IT.
---------------------------------------------------------------------------
`language_code` is REQUIRED by the API and it decides PRONUNCIATION, so this is
the same shape of trap `deepgram.py` documents for captions, pointing the other
way: sent Tamil text under `hi-IN` the API does not say "that isn't Hindi" — it
reads Tamil letters with Hindi rules and hands back a paid, confident, wrong
performance.

So:

  · A language this model does not speak is REFUSED, by name, before anything is
    sent (`language_code` returns "" and `speak` raises). A Spanish board read by
    an Indic model is not a degraded result, it is a wasted run — and the error
    says which `.env` line moves it to Gemini instead.
  · A BLANK language (nobody has set one on the project) becomes `SARVAM_LANGUAGE`,
    default `hi-IN`. Choosing this provider at all is the statement "this film is
    in an Indian language", and Hindi is the one it is most often pointed at.
  · **Hinglish maps to `hi-IN`, deliberately.** It is Hindi spoken with English
    words in it, which is what this model is for. ⚠ Note the asymmetry with
    `deepgram.py`, where Hinglish is the case that backend CANNOT do: writing
    romanised Hindi is impossible there, reading it is what this is best at.

---------------------------------------------------------------------------
⚠ THE SPEAKER LIST BELONGS TO THE MODEL, NOT TO THIS APP.
---------------------------------------------------------------------------
`bulbul:v3` and `bulbul:v2` have COMPLETELY DIFFERENT speaker names, and a v2
name sent to v3 is a 400 on a run somebody was waiting for. So the cast is a
table per model and `cast()` answers for whichever `SARVAM_MODEL` names — the
dialogue sheet in the browser is filled from that same function, so the picker
can never offer a voice the run cannot use.

⚠ AND SARVAM PUBLISHES A SEX FOR EACH SPEAKER AND NOTHING ELSE — no ages. The
personas this app casts for ("grandfather", "child") therefore land on the right
SEX and a GUESS at the rest, nudged by `pace`, which is the one delivery control
v3 exposes. Two things follow, and both matter more than the guess itself:

  · **Every guessed row says it is a guess** (`approx`), `tts.personas()` carries
    that up, and the 🎙 dialog prints it beside the line before anything is
    spent. A promise silently broken is worse than one declined out loud.
  · **The casting is `.env`-overridable** — `SARVAM_CAST=grandfather:anand,
    child:shruti` — because "which of the 44 voices sounds like the grandfather
    in THIS film" is a question only somebody listening can answer, and needing
    a deploy per opinion is the wrong shape for it. Overrides are checked
    against the model's real roster (`ALL_SPEAKERS`, taken from Sarvam's own
    generated SDK) and a bad one is logged and ignored, never fatal.

---------------------------------------------------------------------------
⚠ THE REQUEST SHAPE IS TAKEN FROM SARVAM'S OWN SDK, NOT FROM PROSE.
---------------------------------------------------------------------------
The language field is spelled **`language_code`**, and that is not a reading of
the documentation — the published `sarvamai` package (0.1.32,
`text_to_speech/raw_client.py`) posts exactly:

    {"text", "language_code", "speaker", "pitch", "pace", "loudness",
     "speech_sample_rate", "enable_preprocessing", "model",
     "output_audio_codec", "temperature", "dict_id", "enable_cached_responses"}

That file is generated from the same API definition the server validates
against, which makes it the one source that cannot disagree with the endpoint.
The eleven language codes and the two model ids below come from the same place.
⚠ **The one-shot retry under the older `target_language_code` spelling is kept
anyway** — it costs nothing on the working path, and older Sarvam material still
shows that name, so an account served by an older gateway degrades to a second
request instead of to a failed run.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import os
import re
import time
import wave

import requests
from dotenv import load_dotenv

import retry_policy

load_dotenv()

logger = logging.getLogger(__name__)

TTS_URL = "https://api.sarvam.ai/text-to-speech"

# ⚠ THE KEY IS NAMED FOR THE VENDOR, NOT FOR THE CAPABILITY, AND IT IS THEREFORE
# NOT A SWITCH — the same rule `deepgram.API_KEY_ENV` states. Sarvam sells speech
# recognition as well as speech synthesis, so a key that claimed every capability
# it could serve would move the CAPTIONS the day a Saaras adapter lands. Say
# `VOICE_PROVIDER=sarvam`.
API_KEY_ENV = "SARVAM_API_KEY"

# Overridable because a model id is the thing that ages first. v3 is current;
# v2 is still served and still has its own speakers (see the tables below).
DEFAULT_MODEL = "bulbul:v3"

# --- The house audio format -------------------------------------------------
# ⚠ THESE MUST MATCH `tts.SAMPLE_RATE` / `SAMPLE_WIDTH` / `CHANNELS`, and `tts`
# asserts that they do at import. They are repeated here rather than imported
# because `tts` imports THIS module — the dependency only runs one way.
SAMPLE_RATE = 24_000
SAMPLE_WIDTH = 2
CHANNELS = 1

# What one request may carry, per model, in characters. ⚠ THE MODEL DECIDES, and
# an unknown model gets the SMALLER number: splitting a line that would have fit
# costs one extra call, sending one that does not fit costs the whole run.
MAX_CHARS = {"bulbul:v3": 2500, "bulbul:v2": 1500}
SAFE_CHARS = 1500

# A line of dialogue is a second or two of audio; this is a ceiling on a hung
# socket, not a target.
TIMEOUT_SECONDS = 120

# Advisory list price, USD per 1,000 characters. bulbul:v3 beta pricing is ₹30
# per 10,000 characters — about $0.036/1k at ₹83 to the dollar. ⚠ ADVISORY, like
# every other price in this codebase: we quote, only Sarvam bills, and the UI
# says so. Read at CALL time so a correction is a `.env` line and a restart.
DEFAULT_USD_PER_1K_CHARS = 0.036

# The language nothing has named. See the header: choosing this provider is
# itself the statement that the film is in an Indian language.
DEFAULT_LANGUAGE = "hi-IN"


class SarvamError(RuntimeError):
    """Anything that stops us speaking — no key, a refusal, a timeout."""


# ---------------------------------------------------------------------------
# The language map
# ---------------------------------------------------------------------------
# The app stores a film's language as FREE TEXT ("Hindi", "Hinglish", "Tamil") —
# see `AnimaticSettings.language` and `plan_agent.LANGUAGES`. Sarvam wants a
# BCP-47 code with a region. This is the join, and it is deliberately generous:
# the value is lowercased, trimmed, and matched on the ENGLISH NAME, the ENDONYM
# where people actually type one, and the code itself.
#
# ⚠ ONLY THE ELEVEN BULBUL ACTUALLY SPEAKS ARE IN HERE. Adding "bhojpuri": "bho"
# would send a code the API rejects; leaving it out is what makes `language_code`
# answer "" and the run refuse with a sentence naming the line to change.
_LANGUAGE_CODES = {
    "hindi": "hi-IN", "हिन्दी": "hi-IN", "hi": "hi-IN", "hi-in": "hi-IN",
    "bengali": "bn-IN", "bangla": "bn-IN", "বাংলা": "bn-IN", "bn": "bn-IN", "bn-in": "bn-IN",
    "gujarati": "gu-IN", "ગુજરાતી": "gu-IN", "gu": "gu-IN", "gu-in": "gu-IN",
    "kannada": "kn-IN", "ಕನ್ನಡ": "kn-IN", "kn": "kn-IN", "kn-in": "kn-IN",
    "malayalam": "ml-IN", "മലയാളം": "ml-IN", "ml": "ml-IN", "ml-in": "ml-IN",
    "marathi": "mr-IN", "मराठी": "mr-IN", "mr": "mr-IN", "mr-in": "mr-IN",
    "odia": "od-IN", "oriya": "od-IN", "ଓଡ଼ିଆ": "od-IN", "od": "od-IN", "or": "od-IN",
    "punjabi": "pa-IN", "panjabi": "pa-IN", "ਪੰਜਾਬੀ": "pa-IN", "pa": "pa-IN", "pa-in": "pa-IN",
    "tamil": "ta-IN", "தமிழ்": "ta-IN", "ta": "ta-IN", "ta-in": "ta-IN",
    "telugu": "te-IN", "తెలుగు": "te-IN", "te": "te-IN", "te-in": "te-IN",
    "english": "en-IN", "en": "en-IN", "en-in": "en-IN", "en-us": "en-IN",
    "indian english": "en-IN",
    # ⚠ HINGLISH IS HINDI HERE, AND THAT IS THE WHOLE POINT OF THIS BACKEND. It
    # is Hindi with English words in it, written in Latin script; bulbul reads
    # that as one sentence rather than as two languages fighting.
    "hinglish": "hi-IN", "hindi-english": "hi-IN", "hindustani": "hi-IN",
}


def language_code(language: str = "") -> str:
    """A Sarvam language code for this film, or "" — WHICH MEANS REFUSE.

    ⚠ "" IS NOT "LET THE MODEL DECIDE". The API requires the field, and a wrong
    one is a paid, confident mispronunciation (see the header). A language this
    model does not speak has to stop the run, not soften into a default — so ""
    is returned and `speak` raises with the `.env` line that fixes it.

    A BLANK language is different from an unknown one: nobody has said, so the
    deployment's own default answers.
    """
    key = (language or "").strip().lower()
    if not key:
        return (os.environ.get("SARVAM_LANGUAGE") or "").strip() or DEFAULT_LANGUAGE
    return _LANGUAGE_CODES.get(key, "")


def speaks(language: str = "") -> bool:
    """Can this backend read that language at all? Free, and asked BEFORE the run."""
    return bool(language_code(language))


# ---------------------------------------------------------------------------
# The cast — one table per model, because the names are not shared
# ---------------------------------------------------------------------------
# ⚠ `persona` IS THE JOIN TO THE REST OF THE APP. `tts.PERSONAS` is the
# provider-blind list the dialogue sheet shows ("Grandfather", "Girl"); the
# `persona` on each row here is what casts it for THIS backend. A persona with
# no row falls back to `""`'s voice rather than failing.
#
# ⚠ `pace` IS THE ONLY DELIVERY CONTROL v3 HAS (0.5–2.0; pitch and loudness are
# v2-only), and it is used sparingly and only where age is the point: an elderly
# speaker unhurried, a child a little quick.
#
# ⚠ SARVAM PUBLISHES A SEX PER SPEAKER AND NOTHING ELSE — NO AGES — AND THIS
# TABLE SAYS SO RATHER THAN PRETENDING. The sex is right on every row because
# that IS published; the age is a guess wherever a persona asks for one, and
# every such row carries an `approx` sentence that `tts.personas()` sends up to
# the 🎙 dialog. A picker that promised "Grandfather" and delivered an ordinary
# adult would be a worse lie than one that says which promises it is keeping.
#
# ⚠ AND BECAUSE IT IS A GUESS, IT IS OVERRIDABLE WITHOUT TOUCHING THIS FILE —
# see `SARVAM_CAST` below. The right answer here is "whichever of the 44 voices
# sounds most like the grandfather in YOUR film", and that is a question only
# somebody listening can answer. Changing code to answer it would be a deploy
# per opinion.
_V3_CAST = (
    {"name": "priya", "persona": "", "tone": "Female · natural, top quality", "pace": 1.0},
    {"name": "suhani", "persona": "child", "tone": "Female · light", "pace": 1.06,
     "approx": "Sarvam publishes no ages — this is a light adult voice read a little quick"},
    {"name": "sunny", "persona": "boy", "tone": "Male · light", "pace": 1.06,
     "approx": "Sarvam publishes no ages — this is an adult male voice read a little quick"},
    {"name": "kavya", "persona": "girl", "tone": "Female · bright", "pace": 1.06,
     "approx": "Sarvam publishes no ages — this is an adult female voice read a little quick"},
    {"name": "aditya", "persona": "young_man", "tone": "Male", "pace": 1.0},
    {"name": "shreya", "persona": "young_woman", "tone": "Female", "pace": 1.0},
    {"name": "mani", "persona": "man", "tone": "Male · clearest of the set", "pace": 1.0},
    {"name": "ishita", "persona": "woman", "tone": "Female · conversational", "pace": 1.0},
    {"name": "ratan", "persona": "grandfather", "tone": "Male · unhurried", "pace": 0.9,
     "approx": "Sarvam publishes no ages — an adult male voice slowed to read older"},
    {"name": "roopa", "persona": "grandmother", "tone": "Female · unhurried", "pace": 0.9,
     "approx": "Sarvam publishes no ages — an adult female voice slowed to read older"},
    {"name": "shubh", "persona": "narrator", "tone": "Male · deep, dramatic", "pace": 0.95},
)

# v2's speakers. Four women and three men in TOTAL, so several personas share a
# voice here — which is the truth about that model, and better than offering a
# name it will reject.
_V2_CAST = (
    {"name": "anushka", "persona": "", "tone": "Female", "pace": 1.0},
    {"name": "vidya", "persona": "child", "tone": "Female · light", "pace": 1.08,
     "approx": "v2 has seven voices and no child among them — a light adult, read quick"},
    {"name": "hitesh", "persona": "boy", "tone": "Male · light", "pace": 1.08,
     "approx": "v2 has seven voices and no child among them — an adult male, read quick"},
    {"name": "arya", "persona": "girl", "tone": "Female · bright", "pace": 1.08,
     "approx": "v2 has seven voices and no child among them — an adult female, read quick"},
    {"name": "karun", "persona": "young_man", "tone": "Male", "pace": 1.0},
    {"name": "manisha", "persona": "young_woman", "tone": "Female", "pace": 1.0},
    {"name": "abhilash", "persona": "man", "tone": "Male", "pace": 1.0},
    {"name": "anushka", "persona": "woman", "tone": "Female", "pace": 1.0},
    {"name": "abhilash", "persona": "grandfather", "tone": "Male · unhurried", "pace": 0.85,
     "approx": "v2 has three male voices — shared with 'man', slowed to read older"},
    {"name": "vidya", "persona": "grandmother", "tone": "Female · unhurried", "pace": 0.85,
     "approx": "v2 has four female voices — shared with 'child', slowed to read older"},
    {"name": "arya", "persona": "narrator", "tone": "Female · even", "pace": 0.95},
)

_CASTS = {"bulbul:v3": _V3_CAST, "bulbul:v2": _V2_CAST}

# --- Every speaker the API will accept, per model ---------------------------
# ⚠ TAKEN FROM SARVAM'S OWN GENERATED SDK (`sarvamai` 0.1.32,
# `types/text_to_speech_speaker.py`), not from a blog post — that file is
# generated from the same API definition the server validates against, which
# makes it the one list that cannot be out of date with the endpoint.
#
# The CAST above is a curated eleven; this is the whole roster, and it exists for
# one job: to check a `SARVAM_CAST` override before a run rather than after a
# 400. The sexes are Sarvam's published ones.
_V3_FEMALE = (
    "ritu", "priya", "neha", "pooja", "simran", "kavya", "ishita", "shreya",
    "roopa", "tanya", "shruti", "suhani", "kavitha", "rupali",
)
_V3_MALE = (
    "shubh", "aditya", "rahul", "rohan", "amit", "dev", "ratan", "varun",
    "manan", "sumit", "kabir", "aayan", "ashutosh", "advait", "anand", "tarun",
    "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham",
)
_V2_FEMALE = ("anushka", "manisha", "vidya", "arya")
_V2_MALE = ("abhilash", "karun", "hitesh")

ALL_SPEAKERS = {
    "bulbul:v3": tuple(sorted(_V3_FEMALE + _V3_MALE)),
    "bulbul:v2": tuple(sorted(_V2_FEMALE + _V2_MALE)),
}


def speakers(model: str = "") -> tuple[str, ...]:
    """Every speaker name this model accepts — the roster, not the cast."""
    return ALL_SPEAKERS.get((model or model_id()).strip().lower(), ALL_SPEAKERS["bulbul:v3"])


# --- Correcting the casting by ear, from `.env` -----------------------------
# ⚠ THE AGE GUESS ABOVE IS THE ONE THING IN THIS MODULE A PERSON CAN HEAR AND
# THE CODE CANNOT. `SARVAM_CAST` re-casts any persona without a deploy:
#
#     SARVAM_CAST=grandfather:anand,child:shruti,narrator:mani@0.9
#
# `persona:speaker` or `persona:speaker@pace`. Unknown personas and speakers the
# model does not have are LOGGED AND IGNORED, never fatal — a typo in an
# optional tuning line must not be able to stop a paid run, and the curated cast
# is a working answer for every persona already.
_CAST_OVERRIDE_SPLIT = re.compile(r"[,\n;]+")


def _cast_overrides(model: str) -> dict[str, dict]:
    """`SARVAM_CAST` parsed, checked against this model's roster, or {}."""
    raw = (os.environ.get("SARVAM_CAST") or "").strip()
    if not raw:
        return {}
    known = set(speakers(model))
    out: dict[str, dict] = {}
    for item in _CAST_OVERRIDE_SPLIT.split(raw):
        item = item.strip()
        if not item:
            continue
        persona, _, rest = item.partition(":")
        speaker, _, pace = rest.partition("@")
        persona, speaker = persona.strip().lower(), speaker.strip().lower()
        if not speaker:
            logger.warning("[sarvam] SARVAM_CAST entry %r has no speaker — ignored", item)
            continue
        if speaker not in known:
            logger.warning(
                "[sarvam] SARVAM_CAST names %r, which %s does not have — ignored",
                speaker, model,
            )
            continue
        entry = {"name": speaker}
        if pace.strip():
            try:
                entry["pace"] = max(0.5, min(2.0, float(pace)))
            except ValueError:
                logger.warning("[sarvam] SARVAM_CAST pace %r is not a number — ignored", pace)
        # ⚠ AN OVERRIDDEN ROW LOSES ITS `approx` WARNING, and that is right: the
        # sentence said "this is our guess", and somebody has now chosen.
        out[persona] = entry
    return out


def model_id() -> str:
    """The Bulbul model to ask for. Overridable — a model id ages first."""
    return (os.environ.get("SARVAM_MODEL") or "").strip() or DEFAULT_MODEL


def cast(model: str = "") -> tuple[dict, ...]:
    """This model's speakers, in the order the picker should show them,
    WITH `SARVAM_CAST` APPLIED.

    ⚠ ONE PLACE A SPEAKER NAME EXISTS. The browser's picker is filled from this
    through `tts.cast`, so it can never offer a name the run would be 400'd for
    — and because the override is folded in HERE rather than at the call site,
    the picker shows the same casting the run will use. An override applied only
    where the audio is made is an override the dialog lies about.

    An unknown model id answers with v3's table — the current generation is a
    better guess than an empty dropdown.
    """
    picked = (model or model_id()).strip().lower()
    table = _CASTS.get(picked, _V3_CAST)
    overrides = _cast_overrides(picked)
    if not overrides:
        return table
    return tuple(
        {**row, **overrides[row["persona"]], "approx": ""}
        if row["persona"] in overrides else row
        for row in table
    )


def voices(model: str = "") -> tuple[str, ...]:
    """Just the names, deduplicated, in cast order."""
    seen: list[str] = []
    for entry in cast(model):
        if entry["name"] not in seen:
            seen.append(entry["name"])
    return tuple(seen)


def default_voice(model: str = "") -> str:
    """Who reads a line nothing has cast. The `""` persona's speaker."""
    return cast(model)[0]["name"]


def resolve_voice(voice: str | None, model: str = "") -> str:
    """A speaker this model actually has, or the default.

    Unknown names fold down rather than failing, for the same reason
    `tts.resolve_voice` does: a stale name in a stored request must cost a
    timbre, not a paid run. ⚠ AND THIS IS WHERE A v2 NAME LANDS when the model
    was switched under an old project — it is not this model's, so it folds.
    """
    name = (voice or "").strip().lower()
    for known in voices(model):
        if known == name:
            return known
    return default_voice(model)


def entry_for(voice: str | None = None, persona: str | None = None, model: str = "") -> dict:
    """The cast row that will read this line — by name first, then by persona.

    Returns the `""` row when neither is known, which is a real answer: a line
    nobody has cast is read by the default speaker at ordinary pace.
    """
    table = cast(model)
    name = (voice or "").strip().lower()
    if name:
        for entry in table:
            if entry["name"] == name:
                return entry
    key = (persona or "").strip().lower()
    if key:
        for entry in table:
            if entry["persona"] == key:
                return entry
    return table[0]


def voice_for_persona(persona: str | None = None, model: str = "") -> str:
    """The speaker this project casts for that kind of character."""
    return entry_for(persona=persona, model=model)["name"]


def persona_note(persona: str | None = None, model: str = "") -> str:
    """⚠ WHAT THIS CASTING CANNOT ACTUALLY PROMISE. "" when there is nothing to say.

    Sarvam publishes a sex per speaker and no ages, so "grandfather" and "child"
    are the nearest voice plus a pace nudge rather than the thing itself. This
    carries that up to the 🎙 dialog, where the user can override the voice for
    the line, set `SARVAM_CAST` for the whole project, or switch to
    `VOICE_PROVIDER=gemini`, which has real child voices. ⚠ A row somebody has
    already overridden says nothing — the choice has been made.
    """
    return str(entry_for(persona=persona, model=model).get("approx") or "")


# ---------------------------------------------------------------------------
# The key and the price
# ---------------------------------------------------------------------------
def api_key() -> str:
    """The Sarvam key, or "" when none is set."""
    return (os.environ.get(API_KEY_ENV) or "").strip()


def configured() -> bool:
    """Is the backend switched on? ⚠ Never answers WITH the key."""
    return bool(api_key())


def usd_per_1k_chars() -> float:
    """The advisory rate a run is quoted at, USD per 1,000 characters."""
    raw = (os.environ.get("SARVAM_USD_PER_1K") or "").strip()
    if not raw:
        return DEFAULT_USD_PER_1K_CHARS
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "[sarvam] SARVAM_USD_PER_1K=%r is not a number — using %s",
            raw, DEFAULT_USD_PER_1K_CHARS,
        )
        return DEFAULT_USD_PER_1K_CHARS


def missing_key_hint() -> str:
    """The sentence shown when this backend is chosen and has no key.

    ⚠ IT NAMES THE VARIABLE TO EDIT — the same rule `ai_keys.missing_key_hint`
    follows, and the reason the providers were split at all.
    """
    return (
        f"VOICE_PROVIDER=sarvam needs a key: set {API_KEY_ENV} in your .env "
        "(from https://dashboard.sarvam.ai/). Or set VOICE_PROVIDER=gemini to "
        "read the dialogue on Gemini instead."
    )


# ---------------------------------------------------------------------------
# Splitting a long line — free, and done before anything is sent
# ---------------------------------------------------------------------------
def max_chars(model: str = "") -> int:
    """What one request may carry for this model."""
    return MAX_CHARS.get((model or model_id()).strip().lower(), SAFE_CHARS)


# Sentence ends, including the Devanagari danda — a Hindi line does not end in a
# full stop and splitting on "." alone would never cut one.
_SENTENCE_END = re.compile(r"(?<=[.!?।॥])\s+")


def chunks(text: str, limit: int) -> list[str]:
    """One line split into pieces this API will accept, at the least bad seam.

    ⚠ SENTENCES FIRST, THEN WORDS, THEN A HARD CUT. A seam mid-word is audible;
    a seam mid-sentence is a small extra breath. The pieces are spoken back to
    back with no gap between them (`tts.speak` concatenates the PCM), so a
    sentence seam is very nearly inaudible and a word seam is not.

    A line short enough to send comes back as one piece and this costs nothing.
    """
    body = (text or "").strip()
    if len(body) <= limit:
        return [body] if body else []

    out: list[str] = []
    piece = ""
    for sentence in _SENTENCE_END.split(body):
        if not sentence:
            continue
        if len(piece) + len(sentence) + 1 <= limit:
            piece = f"{piece} {sentence}".strip()
            continue
        if piece:
            out.append(piece)
            piece = ""
        while len(sentence) > limit:
            # No sentence end to use, so break on the last space that fits — and
            # only cut a word when there isn't one.
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        piece = sentence
    if piece:
        out.append(piece)
    return [p for p in out if p]


# ---------------------------------------------------------------------------
# The call — the half that costs money
# ---------------------------------------------------------------------------
def speak(
    text: str,
    *,
    voice: str | None = None,
    persona: str | None = None,
    language: str = "",
) -> bytes:
    """SPENDS QUOTA. Read one line aloud. Returns raw PCM in the house format.

    `persona` is read only for its `pace` — the voice it would cast is already
    settled by `tts.voice_for` before we get here, and casting it twice in two
    places is how the picker and the run start disagreeing.

    ⚠ A LINE TOO LONG FOR ONE REQUEST IS SENT AS SEVERAL AND CONCATENATED. The
    pieces are PCM at one fixed rate, so joining them is `+` and needs no
    decoder — the same fact the whole timing model in `tts.py` rests on.
    """
    body = (text or "").strip()
    if not body:
        raise SarvamError("There is nothing to read aloud.")

    key = api_key()
    if not key:
        raise SarvamError(missing_key_hint())

    code = language_code(language)
    if not code:
        raise SarvamError(
            f"Sarvam's Bulbul does not speak {language.strip() or 'that language'}. "
            "It reads Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, "
            "Odia, Punjabi, Tamil, Telugu and Indian English. Set "
            "VOICE_PROVIDER=gemini in your .env to read this film on Gemini "
            "instead, or correct the project's language."
        )

    model = model_id()
    entry = entry_for(voice=voice, persona=persona, model=model)
    speaker = entry["name"]
    pace = float(entry.get("pace") or 1.0)

    pieces = chunks(body, max_chars(model))
    logger.info(
        "[sarvam] speaking %d character(s) as %s (model=%s, language=%s, %d request(s))",
        len(body), speaker, model, code, len(pieces),
    )
    pcm = bytearray()
    for piece in pieces:
        pcm += _speak_once(piece, key=key, model=model, speaker=speaker, pace=pace, code=code)
    if not pcm:
        raise SarvamError("Sarvam returned no audio for that line.")
    return bytes(pcm)


def _speak_once(
    text: str, *, key: str, model: str, speaker: str, pace: float, code: str
) -> bytes:
    """ONE request, retried on the failures a retry can actually fix.

    ⚠ RETRIES EXIST HERE AND NOT IN `deepgram.transcribe` FOR A REASON. A
    transcription is one call, and a person can press the button again. A
    voiceover is one call PER LINE — forty of them on a board — so a single 429
    halfway through would throw away everything already spoken AND already paid
    for. The ladder is `retry_policy`'s, the same one the image and video
    backends wait on, so there is one tuned answer to "how long is a 429".
    """
    payload = {
        "text": text,
        "language_code": code,
        "model": model,
        "speaker": speaker,
        "speech_sample_rate": SAMPLE_RATE,
        "pace": round(pace, 2),
    }
    # ⚠ v2-ONLY FIELDS. `enable_preprocessing` is what makes v2 read numbers and
    # embedded English properly; v3 does it without being asked and REJECTS the
    # field. Sending both to both is a 400 on whichever model is not ours.
    if model == "bulbul:v2":
        payload["enable_preprocessing"] = True

    last: Exception | None = None
    for attempt in range(1, retry_policy.MAX_RETRIES + 1):
        try:
            res = requests.post(
                TTS_URL,
                json=payload,
                headers={
                    "api-subscription-key": key,
                    "Content-Type": "application/json",
                },
                timeout=TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            last = SarvamError(
                "Sarvam did not answer in time. Try again, or read this film on "
                "Gemini with VOICE_PROVIDER=gemini."
            )
            logger.warning("[sarvam] timeout on attempt %d/%d", attempt, retry_policy.MAX_RETRIES)
        except requests.RequestException as exc:
            last = SarvamError(f"Could not reach Sarvam: {exc}")
            logger.warning("[sarvam] transport error on attempt %d: %s", attempt, exc)
        else:
            if res.status_code < 400:
                return _pcm_of_wav(_audio_of(res))
            # ⚠ THE ONE SHAPE CHANGE THIS ADAPTER HEALS ITSELF THROUGH, AND IT IS
            # A SAFETY NET RATHER THAN A GUESS. `language_code` is what Sarvam's
            # own generated SDK posts (see the header), so the first attempt is
            # the right one; older material spells it `target_language_code`, and
            # an account still served by that gateway should cost a second
            # request rather than a failed run. Never more than once.
            if res.status_code == 400 and _is_language_field_error(res) and "language_code" in payload:
                logger.info("[sarvam] retrying with the older `target_language_code` field")
                payload["target_language_code"] = payload.pop("language_code")
                continue
            error = _error_for(res)
            if not _retryable(res.status_code) or attempt == retry_policy.MAX_RETRIES:
                raise error
            last = error
            logger.warning(
                "[sarvam] %s on attempt %d/%d", res.status_code, attempt, retry_policy.MAX_RETRIES
            )
        if attempt == retry_policy.MAX_RETRIES:
            break
        time.sleep(retry_policy.backoff_delay(attempt, last))
    raise last or SarvamError("Sarvam could not be reached.")


def _retryable(status: int) -> bool:
    """Only the codes a second attempt can actually turn into a success.

    429 is here because a per-minute rate limit REFILLS; 402 (out of credit) is
    not, because waiting does not buy more.
    """
    return status == 429 or 500 <= status < 600


def _is_language_field_error(res) -> bool:
    """Does this 400 complain about the language field specifically?"""
    text = (_detail(res) or "").lower()
    return "language" in text and ("field" in text or "required" in text or "extra" in text
                                   or "permitted" in text or "unexpected" in text)


def _detail(res) -> str:
    """Whatever Sarvam said went wrong, in one short string."""
    try:
        body = res.json()
    except ValueError:
        return (res.text or "")[:200]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "")[:300]
        return str(
            body.get("message") or body.get("detail") or body.get("error") or ""
        )[:300]
    return str(body)[:200]


def _error_for(res) -> SarvamError:
    """Turn Sarvam's HTTP codes into sentences a person can act on.

    ⚠ THE ERROR HAS TO NAME THE LINE TO CHANGE. That is the whole point of
    splitting the providers: a capability that has run out is one `.env` line
    away from being pointed somewhere else, and "403 Forbidden" on its own sends
    the reader to grep the codebase instead.
    """
    detail = _detail(res)
    if res.status_code in (401, 403):
        return SarvamError(
            f"Sarvam refused the key ({API_KEY_ENV}). Check it at "
            f"https://dashboard.sarvam.ai/ — or set VOICE_PROVIDER=gemini to read "
            f"the dialogue on Gemini instead. [{res.status_code}] {detail}"
        )
    if res.status_code in (402, 429):
        return SarvamError(
            "Sarvam's free credit or rate limit is used up. Top it up at "
            "https://dashboard.sarvam.ai/, or set VOICE_PROVIDER=gemini in your "
            f".env and restart to read the dialogue on Gemini. [{res.status_code}] {detail}"
        )
    if res.status_code in (400, 422):
        return SarvamError(
            "Sarvam refused that request — usually a speaker name this model "
            f"does not have (check SARVAM_MODEL, currently {model_id()}) or a "
            f"line longer than it accepts. [{res.status_code}] {detail}"
        )
    return SarvamError(f"Sarvam failed [{res.status_code}] {detail}")


def _audio_of(res) -> bytes:
    """The first audio out of a Sarvam response, decoded from base64."""
    try:
        body = res.json()
    except ValueError as exc:
        raise SarvamError("Sarvam returned something that isn't JSON.") from exc
    audios = (body or {}).get("audios") if isinstance(body, dict) else None
    if not audios or not isinstance(audios, list):
        raise SarvamError("Sarvam's answer carried no audio.")
    try:
        return base64.b64decode(audios[0] or "", validate=False)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise SarvamError("Sarvam's audio could not be decoded.") from exc


def _pcm_of_wav(data: bytes) -> bytes:
    """The samples out of a WAV, REFUSING anything that is not the house format.

    ⚠ THIS CHECK IS THE POINT OF THE FUNCTION, not a formality. Everything
    downstream measures a sound by COUNTING ITS BYTES at one assumed rate
    (`tts._ms_of_bytes`), so a 22,050 Hz file accepted here would play about 9%
    slow, and — far worse — every caption and every stretched shot built from
    those numbers would be wrong by that much, with nothing on screen to say so.
    There is no resampler on this install to fix it with (`audioop` is gone in
    this Python), so the honest move is to refuse and name the setting.
    """
    if not data:
        raise SarvamError("Sarvam returned an empty audio file.")
    try:
        with wave.open(io.BytesIO(data), "rb") as src:
            channels, width, rate = src.getnchannels(), src.getsampwidth(), src.getframerate()
            pcm = src.readframes(src.getnframes())
    except (wave.Error, EOFError) as exc:
        raise SarvamError(
            "Sarvam's audio was not the WAV this app asked for. Check "
            "SARVAM_MODEL, and leave the output codec unset."
        ) from exc
    if (channels, width, rate) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE):
        raise SarvamError(
            f"Sarvam returned {rate} Hz / {channels}ch audio; this app lays "
            f"voiceover at {SAMPLE_RATE} Hz mono and cannot resample. That is "
            "usually a SARVAM_MODEL that ignores speech_sample_rate."
        )
    return pcm
