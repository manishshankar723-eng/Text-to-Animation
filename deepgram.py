"""
deepgram.py — ONE VENDOR, TWO CAPABILITIES, ONE KEY.

    LISTEN (Nova-3)  an audio file in, timed spoken lines out  → `captions.py`
    SPEAK  (Aura-2)  a line of dialogue in, raw PCM out        → `tts.py`

A provider client, like `sarvam.py`, `freesound.py`, `meshy.py` and `tripo.py`:
it talks to somebody else's HTTP API and knows nothing about FastAPI, jobs or the
timeline. The modules that decide WHEN to call it are `captions.py` and `tts.py`;
the routes that file the results into a project are in `server/animatics.py`.

⚠ THE TWO HALVES SHARE ONE KEY AND NOTHING ELSE. They are billed differently
(per audio minute vs per character), they are switched on by different `.env`
lines (`CAPTION_PROVIDER=deepgram` and `VOICE_PROVIDER=deepgram`), and either
can be used without the other. They live in one file because `DEEPGRAM_API_KEY`
must be read in exactly one place — the note beside it explains why the key is
NOT itself a switch, and this is now the case it was written for.

--------------------------- THE LISTEN HALF ---------------------------------

⚠ IT RETURNS THE SAME SHAPE `captions.transcribe` HAS ALWAYS RETURNED —
`[{start_ms, end_ms, text}, …]`, in order, untidied. Everything downstream
(`tidy_lines`, the splitter, the overlap rules, the drawing) is provider-blind
and must stay that way: a transcript from here and a transcript from Gemini are
the same object, so the hard-won timing rules in `captions.py` apply to both
without a second code path to keep in step.

---------------------------------------------------------------------------
⚠ WHY THIS EXISTS AT ALL, WHEN GEMINI ALREADY TRANSCRIBES.
---------------------------------------------------------------------------
Two reasons, and neither is "it is cheaper per second" — it is not. Deepgram
Nova-3 batch is ~$0.0043/minute against the ~$0.0007/minute we quote for Gemini
audio input, so on the list price this is the DEARER of the two.

  1. **$200 of free credit, which does not expire.** At the batch rates below
     that is several hundred hours of audio. ⚠ **A single hour figure is
     deliberately NOT quoted here**, because published ones disagree: $200 at
     $0.0043/min is ~775 hours, while Deepgram's own marketing has said ~435 —
     the difference is which rate you are actually billed at (batch vs
     streaming, one language vs `multi`) plus any add-ons enabled. Whichever it
     is, for a product being tested "hundreds of hours before you pay anything"
     beats a lower rate you start paying on the first run.
  2. **It returns WORD-LEVEL TIMINGS as measurement, not as opinion.** A
     language model asked for `start_ms` is guessing from what it heard; a
     speech engine reports where in the waveform the word actually was. Captions
     that land on the word are the entire point of the feature, and this is the
     only one of the two that is doing arithmetic rather than inference.

---------------------------------------------------------------------------
⚠ THE LANGUAGE PARAMETER IS THE DANGEROUS ONE. READ THIS BEFORE CHANGING IT.
---------------------------------------------------------------------------
**Deepgram's default is `language=en`, and a wrong language does not fail — it
returns confident nonsense.** Sent Hindi speech with no language named, the API
does not say "this isn't English"; it hands back English words that sound a bit
like what was said, at correct timings, and every check downstream passes. That
is the worst failure shape there is: a paid run, a full transcript, and subtitles
nobody can read.

So `language_code()` NEVER returns "" and this module never omits the parameter.
A film whose language we cannot map becomes `multi` — Nova-3's code-switching
mode — because "listen for any of ten languages" is wrong far less often than
"assume English".

⚠ AND `multi` IS NOT THE SAME THING AS THIS APP'S "HINGLISH". `plan_agent.LANGUAGES`
defines Hinglish as Hindi words in LATIN script — *"Shiv ji ki ye kahani aapne
kabhi nahi suni hogi"*. Deepgram transcribes Hindi speech in DEVANAGARI
(देवनागरी) and has no romanisation option, so a Hinglish film captioned here
comes back in the wrong script for its own titles. There is no flag that fixes
this. **For a Hinglish board, `CAPTION_PROVIDER=gemini` is the right answer** —
a language model can be told which script to write in, and this cannot. That is
a real limitation of this backend, not a bug to be fixed later.

---------------------------------------------------------------------------
⚠ UTTERANCES, NOT THE BARE TRANSCRIPT.
---------------------------------------------------------------------------
`results.channels[0].alternatives[0].transcript` is ONE STRING for the whole
file — accurate and useless here, because a caption needs to know when it starts.
`utterances=true` asks the API to break the audio at natural pauses and time each
piece, which is the same unit a subtitle is. The bare transcript is kept only as
the last fallback, timed across the whole file, so a response missing utterances
degrades to one long caption rather than to nothing.
"""

from __future__ import annotations

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

LISTEN_URL = "https://api.deepgram.com/v1/listen"

# ⚠ THE KEY IS NAMED FOR THE VENDOR, NOT FOR THE CAPABILITY, AND IT IS THEREFORE
# NOT A SWITCH. `GEMINI_KEY_CAPTION` moves the captions on its own because the
# name says both who is paid AND what for. `DEEPGRAM_API_KEY` says only who — and
# Deepgram sells speech-to-text AND text-to-speech, so a key that quietly claimed
# every capability it could serve would move the VOICEOVER too.
#
# ⚠ THAT DAY HAS ARRIVED AND THE RULE HELD. The Aura half of this file is live
# below, and pasting this one key still moves NOTHING on its own: captions need
# `CAPTION_PROVIDER=deepgram`, the voiceover needs `VOICE_PROVIDER=deepgram`, and
# a deployment can want one without wanting the other.
API_KEY_ENV = "DEEPGRAM_API_KEY"

# Nova-3 is the current generation and the one the free credit is quoted
# against. Overridable because a model id is the thing that ages first.
DEFAULT_MODEL = "nova-3"

# USD per MINUTE of audio, batch (pre-recorded) Nova-3, list price at the time of
# writing. ⚠ ADVISORY, like every other price in this codebase — only Deepgram
# bills, list prices drift, and the $200 of free credit is not modelled here.
# Multilingual costs more than a single named language, which is why the estimate
# has to know which one the run will actually use.
#
# ⚠ THESE ARE DEFAULTS, AND THE ENV IS READ AT CALL TIME, NOT AT IMPORT. A
# constant built from `os.environ` while the module loads is a constant whose
# override only works if the variable was set before the process started —
# which is true in production and silently false everywhere else, including in
# a test that sets it and then asks why nothing moved. `model_id()` already
# reads at call time; these now match it.
DEFAULT_USD_PER_MINUTE_MONO = 0.0043
DEFAULT_USD_PER_MINUTE_MULTI = 0.0052

# A 20-minute file (`captions.MAX_AUDIO_SECONDS`) uploads and transcribes well
# inside this; it is a ceiling on a hung socket, not a target.
TIMEOUT_SECONDS = 600

# ⚠ THE CODE-SWITCHING MODE, AND THE DEFAULT FOR ANYTHING WE CANNOT MAP. Covers
# English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian
# and Dutch in one pass — see the header for why "wrong less often" beats the
# API's own `en` default.
MULTI = "multi"


class DeepgramError(RuntimeError):
    """Anything that stops us answering — no key, a refusal, a timeout."""


# ---------------------------------------------------------------------------
# The language map
# ---------------------------------------------------------------------------
# The app stores a film's language as FREE TEXT ("Hindi", "Tamil", "Bhojpuri") —
# see `AnimaticProject.language` and `plan_agent.LANGUAGES`. Deepgram wants a
# BCP-47 code. This is the join, and it is deliberately generous: the value is
# lowercased, trimmed, and matched on the ENGLISH NAME, the ENDONYM where people
# actually type one, and the code itself.
#
# ⚠ ONLY LANGUAGES NOVA-3 ACTUALLY LISTS ARE IN HERE. Adding "Bhojpuri": "bho"
# would send a code the API rejects, turning an unsupported language into a
# failed run instead of a `multi` attempt. An absent name is the correct answer.
_LANGUAGE_CODES = {
    # South Asia — the ones this product is most often pointed at
    "hindi": "hi", "हिन्दी": "hi", "hi": "hi",
    "bengali": "bn", "bangla": "bn", "বাংলা": "bn", "bn": "bn",
    "gujarati": "gu", "ગુજરાતી": "gu", "gu": "gu",
    "kannada": "kn", "ಕನ್ನಡ": "kn", "kn": "kn",
    "marathi": "mr", "मराठी": "mr", "mr": "mr",
    "nepali": "ne", "नेपाली": "ne", "ne": "ne",
    "punjabi": "pa", "panjabi": "pa", "ਪੰਜਾਬੀ": "pa", "pa": "pa",
    "tamil": "ta", "தமிழ்": "ta", "ta": "ta",
    "telugu": "te", "తెలుగు": "te", "te": "te",
    "urdu": "ur", "اردو": "ur", "ur": "ur",
    "assamese": "as", "অসমীয়া": "as", "as": "as",
    # Elsewhere
    "english": "en", "en": "en",
    "spanish": "es", "español": "es", "es": "es",
    "french": "fr", "français": "fr", "fr": "fr",
    "german": "de", "deutsch": "de", "de": "de",
    "dutch": "nl", "nl": "nl",
    "italian": "it", "italiano": "it", "it": "it",
    "portuguese": "pt", "português": "pt", "pt": "pt",
    "russian": "ru", "русский": "ru", "ru": "ru",
    "japanese": "ja", "日本語": "ja", "ja": "ja",
    "korean": "ko", "한국어": "ko", "ko": "ko",
    "chinese": "zh", "mandarin": "zh", "中文": "zh", "zh": "zh",
    "arabic": "ar", "العربية": "ar", "ar": "ar",
    "turkish": "tr", "tr": "tr",
    "indonesian": "id", "id": "id",
    "vietnamese": "vi", "vi": "vi",
    "thai": "th", "th": "th",
    "polish": "pl", "pl": "pl",
    "ukrainian": "uk", "uk": "uk",
    "hebrew": "he", "he": "he",
    "persian": "fa", "farsi": "fa", "fa": "fa",
    "swedish": "sv", "sv": "sv",
    "danish": "da", "da": "da",
    "norwegian": "no", "no": "no",
    "finnish": "fi", "fi": "fi",
    "greek": "el", "el": "el",
    "czech": "cs", "cs": "cs",
    "romanian": "ro", "ro": "ro",
    "hungarian": "hu", "hu": "hu",
    "malay": "ms", "ms": "ms",
    "tagalog": "tl", "filipino": "tl", "tl": "tl",
}

# ⚠ HINGLISH IS DELIBERATELY NOT IN THE TABLE ABOVE. It is not a code Deepgram
# has; it is Hindi and English in one sentence, which is exactly what `multi` is
# for. It lands here rather than on `hi` so the English half is not mangled — and
# the SCRIPT will still be Devanagari, which is the header's warning.
_MULTI_NAMES = {"hinglish", "multilingual", "multi", "mixed", "code-switching"}


def language_code(language: str = "") -> str:
    """A Deepgram language code for this film. NEVER "".

    Blank, unknown, or explicitly mixed all become `multi` — see the header for
    why silence is the one answer this must never give.
    """
    key = (language or "").strip().lower()
    if not key or key in _MULTI_NAMES:
        return MULTI
    code = _LANGUAGE_CODES.get(key)
    if code:
        return code
    # A name we do not carry ("Bhojpuri", "Maithili"). `multi` will not transcribe
    # it correctly either, but it will not confidently transcribe it as English.
    logger.info(
        "[deepgram] no Nova-3 code for language %r — falling back to %s", language, MULTI
    )
    return MULTI


def _rate(env_name: str, default: float) -> float:
    """A price from the environment, or the default if it is unset or nonsense."""
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[deepgram] %s=%r is not a number — using %s", env_name, raw, default)
        return default


def usd_per_minute(language: str = "") -> float:
    """The advisory rate this run will be quoted at, in USD per audio minute."""
    if language_code(language) == MULTI:
        return _rate("DEEPGRAM_USD_PER_MINUTE_MULTI", DEFAULT_USD_PER_MINUTE_MULTI)
    return _rate("DEEPGRAM_USD_PER_MINUTE", DEFAULT_USD_PER_MINUTE_MONO)


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------
def api_key() -> str:
    """The Deepgram key, or "" when none is set."""
    return (os.environ.get(API_KEY_ENV) or "").strip()


def configured() -> bool:
    """Is the library switched on? ⚠ Never answers WITH the key."""
    return bool(api_key())


def model_id() -> str:
    """The Nova model to ask for. Overridable — a model id ages first."""
    return (os.environ.get("DEEPGRAM_MODEL") or "").strip() or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# The call — the half that costs money
# ---------------------------------------------------------------------------
def transcribe(data: bytes, *, mime_type: str, language: str = "") -> list[dict]:
    """SPENDS QUOTA. Listen to `data` and return timed lines, untidied.

    Returns `captions.transcribe`'s shape exactly: `[{start_ms, end_ms, text}, …]`
    in the order they were spoken. Tidying, splitting and overlap-fixing belong
    to `captions.tidy_lines` and are NOT done here — a transcript from this
    backend has to be the same object a Gemini transcript is.
    """
    key = api_key()
    if not key:
        raise DeepgramError(
            f"CAPTION_PROVIDER=deepgram needs a key: set {API_KEY_ENV} in your "
            ".env. (Or set CAPTION_PROVIDER=gemini to use Gemini instead.)"
        )
    if not data:
        raise DeepgramError("That audio track is empty.")

    code = language_code(language)
    params = {
        "model": model_id(),
        # ⚠ NEVER OMITTED. The API's default is `en`; see the header.
        "language": code,
        # Punctuation and casing, so a line reads as a subtitle rather than as a
        # word list. `smart_format` implies `punctuate`.
        "smart_format": "true",
        # The unit a caption actually is — natural pauses, each one timed.
        "utterances": "true",
    }
    logger.info(
        "[deepgram] transcribing %.1f KB as %s (model=%s, language=%s)",
        len(data) / 1024, mime_type, params["model"], code,
    )
    try:
        res = requests.post(
            LISTEN_URL,
            params=params,
            headers={
                "Authorization": f"Token {key}",
                "Content-Type": mime_type or "audio/mpeg",
            },
            data=data,
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise DeepgramError(
            "Deepgram did not answer in time. The track may be very long — try "
            "captioning a shorter section."
        ) from exc
    except requests.RequestException as exc:
        raise DeepgramError(f"Could not reach Deepgram: {exc}") from exc

    _raise_for_status(res)

    try:
        payload = res.json()
    except ValueError as exc:
        raise DeepgramError("Deepgram returned something that isn't JSON.") from exc

    lines = _lines_of(payload)
    if not lines:
        raise DeepgramError(
            "Deepgram found no speech in that track. Check that the audio layer "
            "holds a voice and not only music."
        )
    billed = _billed_seconds(payload)
    logger.info(
        "[deepgram] %d line(s) from %.1fs of audio (~$%.4f)",
        len(lines), billed, billed / 60.0 * usd_per_minute(language),
    )
    return lines


def _raise_for_status(res) -> None:
    """Turn Deepgram's HTTP codes into sentences a person can act on.

    ⚠ THE ERROR HAS TO NAME THE LINE TO CHANGE. That is the whole point of
    splitting the providers: a capability that has run out is one `.env` line
    away from being pointed somewhere else, and "402 Payment Required" alone
    sends the reader to grep the codebase instead.
    """
    if res.status_code < 400:
        return
    detail = ""
    try:
        body = res.json()
        detail = str(body.get("err_msg") or body.get("message") or body.get("error") or "")
    except ValueError:
        detail = (res.text or "")[:200]

    if res.status_code in (401, 403):
        raise DeepgramError(
            f"Deepgram refused the key ({API_KEY_ENV}). Check it at "
            f"https://console.deepgram.com/ — or set CAPTION_PROVIDER=gemini to "
            f"use Gemini instead. [{res.status_code}] {detail}"
        )
    if res.status_code in (402, 429):
        raise DeepgramError(
            "Deepgram's free credit or rate limit is used up. Top it up, or set "
            "CAPTION_PROVIDER=gemini in your .env and restart to caption on "
            f"Gemini instead. [{res.status_code}] {detail}"
        )
    if res.status_code == 400:
        raise DeepgramError(
            "Deepgram could not read that audio. Check the file is one of MP3, "
            f"WAV, M4A, AAC, OGG, FLAC or WebM. [400] {detail}"
        )
    raise DeepgramError(f"Deepgram failed [{res.status_code}] {detail}")


def _billed_seconds(payload: dict) -> float:
    """How much audio Deepgram says it processed. 0.0 when it does not say."""
    try:
        return float(payload["metadata"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _lines_of(payload: dict) -> list[dict]:
    """Timed lines out of a Deepgram response, best source first.

    ⚠ THREE SOURCES, AND THE ORDER IS A QUALITY ORDER, not a preference. Each one
    is worse-timed than the one above it, and the last is barely timed at all —
    but a paid response that we could only half-read should still produce
    subtitles somebody can drag into place, not an error.
    """
    if not isinstance(payload, dict):
        return []

    # 1. Utterances — what we asked for. Already the unit a caption is.
    lines = [
        line
        for line in (_coerce(u) for u in _get(payload, "results", "utterances") or [])
        if line
    ]
    if lines:
        return lines

    alt = _first_alternative(payload)
    if not alt:
        return []

    # 2. Paragraph sentences — present when smart_format ran but utterances did
    #    not. Sentence-level timings, which is nearly as good.
    sentences = []
    for para in _get(alt, "paragraphs", "paragraphs") or []:
        if isinstance(para, dict):
            sentences.extend(para.get("sentences") or [])
    lines = [line for line in (_coerce(s) for s in sentences) if line]
    if lines:
        return lines

    # 3. The whole transcript as ONE caption, timed across the file. Deliberately
    #    poor: it is visibly one long line the user will cut up, which is a
    #    better outcome than a paid run that produced nothing.
    text = str(alt.get("transcript") or "").strip()
    if not text:
        return []
    words = alt.get("words") or []
    start = _seconds(words[0].get("start")) if words else 0.0
    end = _seconds(words[-1].get("end")) if words else _billed_seconds(payload)
    logger.warning(
        "[deepgram] no utterances or sentences in the response — falling back to "
        "one caption for the whole track."
    )
    return [{
        "start_ms": int(start * 1000),
        "end_ms": int(max(end, start) * 1000),
        "text": text,
    }]


def _first_alternative(payload: dict) -> dict | None:
    """`results.channels[0].alternatives[0]`, or None."""
    channels = _get(payload, "results", "channels") or []
    if not channels or not isinstance(channels[0], dict):
        return None
    alts = channels[0].get("alternatives") or []
    return alts[0] if alts and isinstance(alts[0], dict) else None


def _get(obj, *path):
    """Walk `path` through nested dicts, or None the moment one is missing."""
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _seconds(value) -> float:
    """A Deepgram timestamp as float seconds, or 0.0."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _coerce(item) -> dict | None:
    """One utterance or sentence into a line, or None if it isn't usable.

    Forgiving on purpose, for the same reason `captions._coerce_line` is: one
    malformed entry in a forty-line transcript should cost that line, not the
    whole run that has already been paid for.
    """
    if not isinstance(item, dict):
        return None
    text = str(item.get("transcript") or item.get("text") or "").strip()
    if not text:
        return None
    start = _seconds(item.get("start"))
    end = _seconds(item.get("end"))
    return {
        "start_ms": int(start * 1000),
        "end_ms": int(max(end, start) * 1000),
        "text": text,
    }


# ===========================================================================
# THE SPEAK HALF — Aura-2, text-to-speech
#
# ⚠ IT RETURNS THE SAME BYTES `tts.speak` HAS ALWAYS RETURNED — signed 16-bit
# little-endian PCM, mono, 24 kHz, no container. Everything downstream of that
# counts BYTES to know how long a sound is and owns no decoder, so this asks
# Aura for linear16 in a WAV at exactly that rate and unwraps it here.
#
# ⚠ WHY, GIVEN GEMINI ALREADY SPEAKS AND IS CHEAPER PER CHARACTER. Aura-2 is
# ~$0.030/1k characters against the ~$0.012/1k we quote for Gemini TTS. The
# reason is the same one the LISTEN half was chosen for: **$200 of free credit
# that does not expire**, shared between both halves. At Aura's rate that is
# millions of characters — for a product being tested, "you will not reach the
# end of it" beats a lower rate you start paying on the first line.
#
# ⚠ AND THE LANGUAGE IS WHY THIS IS NOT THE DEFAULT — AND WHY THE CAST IS A
# TABLE PER LANGUAGE. Aura-2 speaks SEVEN: English, Spanish, German, French,
# Dutch, Italian and Japanese, each with its OWN voices, because the voice name
# *is* the language here (`aura-2-thalia-en` vs `aura-2-sirio-es`) — there is no
# language parameter to get wrong, only a voice. All seven are cast below.
#
# **It does not speak Hindi**, or any other Indian language. An Indian-language
# board is REFUSED here by name and pointed at `VOICE_PROVIDER=sarvam` — reading
# Hindi with an English voice is not a degraded result, it is a paid run of
# nonsense. Same rule, opposite direction, as the Hinglish warning in the LISTEN
# half's header.
#
# ⚠ AND IT HAS NO CHILD VOICES IN ANY LANGUAGE — its youngest published tier is
# "Young Adult". That is not hidden: every cast row that cannot keep its
# persona's promise carries an `approx` sentence, `tts.personas()` sends it up,
# and the 🎙 dialog prints it beside the line before the money is spent. For a
# film with children in it, `VOICE_PROVIDER=gemini` has real child voices.
# ===========================================================================
SPEAK_URL = "https://api.deepgram.com/v1/speak"

# The house audio format. ⚠ MUST MATCH `tts.SAMPLE_RATE` / `SAMPLE_WIDTH` /
# `CHANNELS`, and `tts` asserts that it does at import. Repeated here rather
# than imported because `tts` imports THIS module.
TTS_SAMPLE_RATE = 24_000
TTS_SAMPLE_WIDTH = 2
TTS_CHANNELS = 1

# What one REST request may carry. Deepgram answers 413 above this.
TTS_MAX_CHARS = 2000

# A line of dialogue is a second or two of audio; a ceiling on a hung socket.
TTS_TIMEOUT_SECONDS = 120

# Advisory list price, USD per 1,000 characters, Aura-2 pay-as-you-go. ⚠ ADVISORY
# like every other price here, read at call time, and the $200 of free credit is
# not modelled — it is shared with transcription and only Deepgram knows what is
# left of it.
DEFAULT_TTS_USD_PER_1K_CHARS = 0.030


class DeepgramSpeechError(DeepgramError):
    """Anything that stops us SPEAKING. A `DeepgramError` so a caller that only
    wants to know "did the vendor fail" still catches one thing."""


# --- The cast, PER LANGUAGE -------------------------------------------------
# ⚠ ON THIS BACKEND THE VOICE *IS* THE LANGUAGE. There is no `language`
# parameter on `/v1/speak` — `aura-2-thalia-en` speaks English and
# `aura-2-sirio-es` speaks Spanish, and sending Spanish text to the English
# model does not fail, it reads Spanish words with English phonetics. So the
# cast is a table PER LANGUAGE, everything below takes the film's language, and
# `tts_resolve_voice` folds a voice from the wrong language down to the right
# one rather than letting it through.
#
# ⚠ `persona` IS THE JOIN TO THE REST OF THE APP — `tts.PERSONAS` is the
# provider-blind list the dialogue sheet shows ("Grandfather", "Girl") and this
# is what casts each one for THIS backend, in THIS language. `tone` is
# Deepgram's own published description (gender · age · character), shown beside
# the name so a voice means something before it has been heard.
#
# ⚠ `approx` IS A PROMISE THIS BACKEND CANNOT KEEP, WRITTEN DOWN. Aura-2
# publishes no child voices in any language — its youngest tier is "Young
# Adult" — and several languages have only two or three voices in total, so a
# persona often lands on the nearest thing rather than the right thing. Every
# such row says so, `tts.personas()` carries it up, and the 🎙 dialog prints it
# beside the line. A picker that promised "Child" and delivered an adult would
# be a worse lie than a picker that says which promises it is keeping.
#
# ⚠ AND THE SEX IS KEPT EVEN WHERE THE AGE CANNOT BE. A boy read by an adult
# man is recognisably a miscast the user can hear and fix in one click; a boy
# read by a woman is a surprise nobody predicted from the label. Predictable
# beats clever here — for a film with children in it, `VOICE_PROVIDER=gemini`
# has real child voices and the dialog says so.
#
# Curated rather than every voice Deepgram lists: each entry is a part an
# animatic needs, and a wall of forty names is a worse picker than a short one.
_TTS_CAST_EN = (
    {"name": "aura-2-thalia-en", "persona": "", "tone": "Female · clear, confident"},
    {"name": "aura-2-luna-en", "persona": "child", "tone": "Female · young adult",
     "approx": "no child voices — read by the youngest voice there is"},
    {"name": "aura-2-delia-en", "persona": "girl", "tone": "Female · young adult, cheerful",
     "approx": "no child voices — read by a young adult"},
    {"name": "aura-2-apollo-en", "persona": "boy", "tone": "Male · adult, casual",
     "approx": "no child voices — read by an adult man"},
    {"name": "aura-2-arcas-en", "persona": "young_man", "tone": "Male · natural, smooth"},
    {"name": "aura-2-cordelia-en", "persona": "young_woman", "tone": "Female · young adult, warm"},
    {"name": "aura-2-orpheus-en", "persona": "man", "tone": "Male · clear, confident"},
    {"name": "aura-2-hera-en", "persona": "woman", "tone": "Female · smooth, warm"},
    {"name": "aura-2-atlas-en", "persona": "grandfather", "tone": "Male · mature, friendly"},
    {"name": "aura-2-athena-en", "persona": "grandmother", "tone": "Female · mature, calm"},
    {"name": "aura-2-zeus-en", "persona": "narrator", "tone": "Male · deep, trustworthy"},
)

_TTS_CAST_ES = (
    {"name": "aura-2-diana-es", "persona": "", "tone": "Female · Peninsular, professional"},
    {"name": "aura-2-celeste-es", "persona": "child", "tone": "Female · Colombian, young adult",
     "approx": "no child voices — read by the youngest voice there is"},
    {"name": "aura-2-gloria-es", "persona": "girl", "tone": "Female · Colombian, young adult",
     "approx": "no child voices — read by a young adult"},
    {"name": "aura-2-luciano-es", "persona": "boy", "tone": "Male · Mexican, cheerful",
     "approx": "no child voices — read by an adult man"},
    {"name": "aura-2-aquila-es", "persona": "young_man", "tone": "Male · Latin American, casual"},
    {"name": "aura-2-selena-es", "persona": "young_woman", "tone": "Female · Latin American, friendly"},
    {"name": "aura-2-javier-es", "persona": "man", "tone": "Male · Mexican, approachable"},
    {"name": "aura-2-olivia-es", "persona": "woman", "tone": "Female · Mexican, warm"},
    {"name": "aura-2-valerio-es", "persona": "grandfather", "tone": "Male · Mexican, deep",
     "approx": "no elderly voices — read by a deep adult voice"},
    {"name": "aura-2-estrella-es", "persona": "grandmother", "tone": "Female · Mexican, mature"},
    {"name": "aura-2-sirio-es", "persona": "narrator", "tone": "Male · Mexican, baritone"},
)

_TTS_CAST_DE = (
    {"name": "aura-2-elara-de", "persona": "", "tone": "Female · calm, clear"},
    {"name": "aura-2-lara-de", "persona": "child", "tone": "Female · young adult, cheerful",
     "approx": "no child voices — read by the youngest voice there is"},
    {"name": "aura-2-aurelia-de", "persona": "girl", "tone": "Female · young adult, casual",
     "approx": "no child voices — read by a young adult"},
    {"name": "aura-2-julius-de", "persona": "boy", "tone": "Male · adult, cheerful",
     "approx": "no child voices — read by an adult man"},
    {"name": "aura-2-julius-de", "persona": "young_man", "tone": "Male · adult, engaging",
     "approx": "only two male voices in German — shared with 'boy'"},
    {"name": "aura-2-kara-de", "persona": "young_woman", "tone": "Female · young adult, warm"},
    {"name": "aura-2-fabian-de", "persona": "man", "tone": "Male · mature, professional"},
    {"name": "aura-2-viktoria-de", "persona": "woman", "tone": "Female · warm, cheerful"},
    {"name": "aura-2-fabian-de", "persona": "grandfather", "tone": "Male · mature, knowledgeable",
     "approx": "only two male voices in German — shared with 'man'"},
    {"name": "aura-2-elara-de", "persona": "grandmother", "tone": "Female · calm, patient",
     "approx": "no elderly voices — read by a calm adult voice"},
    {"name": "aura-2-fabian-de", "persona": "narrator", "tone": "Male · mature, natural"},
)

# ⚠ FRENCH HAS EXACTLY TWO VOICES, AND EVERY ROW HERE SAYS SO. Pretending
# otherwise by spreading eleven personas over two names silently would produce a
# film where the grandfather and the little boy are the same person.
_TTS_CAST_FR = (
    {"name": "aura-2-agathe-fr", "persona": "", "tone": "Female · natural, cheerful"},
    # ⚠ THESE THREE SAY BOTH TRUE THINGS AT ONCE, and they have to: "French has
    # only two voices" on its own would let a reader assume one of the two is a
    # child's. There are no child voices in ANY Aura language, and in French
    # there is also nowhere else to go.
    {"name": "aura-2-agathe-fr", "persona": "child", "tone": "Female · natural",
     "approx": "no child voices — and French has two voices in total, so every "
               "female part shares this one"},
    {"name": "aura-2-agathe-fr", "persona": "girl", "tone": "Female · natural",
     "approx": "no child voices — and French has two voices in total, so every "
               "female part shares this one"},
    {"name": "aura-2-hector-fr", "persona": "boy", "tone": "Male · expressive",
     "approx": "no child voices — and French has two voices in total, so every "
               "male part shares this one"},
    {"name": "aura-2-hector-fr", "persona": "young_man", "tone": "Male · expressive",
     "approx": "French has two voices on this backend — every male part shares one"},
    {"name": "aura-2-agathe-fr", "persona": "young_woman", "tone": "Female · friendly",
     "approx": "French has two voices on this backend — every female part shares one"},
    {"name": "aura-2-hector-fr", "persona": "man", "tone": "Male · confident, patient"},
    {"name": "aura-2-agathe-fr", "persona": "woman", "tone": "Female · charismatic"},
    {"name": "aura-2-hector-fr", "persona": "grandfather", "tone": "Male · patient",
     "approx": "French has two voices on this backend — every male part shares one"},
    {"name": "aura-2-agathe-fr", "persona": "grandmother", "tone": "Female · friendly",
     "approx": "French has two voices on this backend — every female part shares one"},
    {"name": "aura-2-hector-fr", "persona": "narrator", "tone": "Male · confident"},
)

_TTS_CAST_NL = (
    {"name": "aura-2-daphne-nl", "persona": "", "tone": "Female · calm, clear"},
    {"name": "aura-2-beatrix-nl", "persona": "child", "tone": "Female · cheerful, warm",
     "approx": "no child voices — read by an adult"},
    {"name": "aura-2-cornelia-nl", "persona": "girl", "tone": "Female · friendly, positive",
     "approx": "no child voices — read by an adult"},
    {"name": "aura-2-lars-nl", "persona": "boy", "tone": "Male · casual, comfortable",
     "approx": "no child voices — read by an adult man"},
    {"name": "aura-2-lars-nl", "persona": "young_man", "tone": "Male · casual, sincere"},
    {"name": "aura-2-hestia-nl", "persona": "young_woman", "tone": "Female · expressive, friendly"},
    {"name": "aura-2-sander-nl", "persona": "man", "tone": "Male · clear, deep"},
    {"name": "aura-2-rhea-nl", "persona": "woman", "tone": "Female · warm, smooth"},
    {"name": "aura-2-roman-nl", "persona": "grandfather", "tone": "Male · deep, patient",
     "approx": "no elderly voices — read by a deep, patient adult voice"},
    {"name": "aura-2-leda-nl", "persona": "grandmother", "tone": "Female · caring, sincere",
     "approx": "no elderly voices — read by a caring adult voice"},
    {"name": "aura-2-sander-nl", "persona": "narrator", "tone": "Male · professional, smooth"},
)

_TTS_CAST_IT = (
    {"name": "aura-2-livia-it", "persona": "", "tone": "Female · clear, expressive"},
    {"name": "aura-2-maia-it", "persona": "child", "tone": "Female · young adult, energetic",
     "approx": "no child voices — read by the youngest voice there is"},
    {"name": "aura-2-maia-it", "persona": "girl", "tone": "Female · young adult, warm",
     "approx": "no child voices — read by a young adult"},
    {"name": "aura-2-dionisio-it", "persona": "boy", "tone": "Male · positive, friendly",
     "approx": "no child voices — read by an adult man"},
    {"name": "aura-2-dionisio-it", "persona": "young_man", "tone": "Male · engaging, melodic"},
    {"name": "aura-2-melia-it", "persona": "young_woman", "tone": "Female · friendly, natural"},
    {"name": "aura-2-cesare-it", "persona": "man", "tone": "Male · clear, knowledgeable"},
    {"name": "aura-2-demetra-it", "persona": "woman", "tone": "Female · calm, patient"},
    {"name": "aura-2-flavio-it", "persona": "grandfather", "tone": "Male · deep, trustworthy",
     "approx": "no elderly voices — read by a deep adult voice"},
    {"name": "aura-2-cinzia-it", "persona": "grandmother", "tone": "Female · mature, warm"},
    {"name": "aura-2-elio-it", "persona": "narrator", "tone": "Male · calm, smooth"},
)

_TTS_CAST_JA = (
    {"name": "aura-2-izanami-ja", "persona": "", "tone": "Female · clear, polite"},
    {"name": "aura-2-uzume-ja", "persona": "child", "tone": "Female · young adult, clear",
     "approx": "no child voices — read by the youngest voice there is"},
    {"name": "aura-2-uzume-ja", "persona": "girl", "tone": "Female · young adult, approachable",
     "approx": "no child voices — read by a young adult"},
    {"name": "aura-2-ebisu-ja", "persona": "boy", "tone": "Male · young adult, natural",
     "approx": "no child voices — read by a young adult"},
    {"name": "aura-2-ebisu-ja", "persona": "young_man", "tone": "Male · young adult, sincere"},
    {"name": "aura-2-uzume-ja", "persona": "young_woman", "tone": "Female · young adult, polite"},
    {"name": "aura-2-fujin-ja", "persona": "man", "tone": "Male · confident, professional"},
    {"name": "aura-2-ama-ja", "persona": "woman", "tone": "Female · natural, confident"},
    {"name": "aura-2-fujin-ja", "persona": "grandfather", "tone": "Male · calm, knowledgeable",
     "approx": "no elderly voices — read by a calm adult voice"},
    {"name": "aura-2-izanami-ja", "persona": "grandmother", "tone": "Female · polite, knowledgeable",
     "approx": "no elderly voices — read by a polite adult voice"},
    {"name": "aura-2-fujin-ja", "persona": "narrator", "tone": "Male · smooth, professional"},
)

_TTS_CASTS = {
    "en": _TTS_CAST_EN, "es": _TTS_CAST_ES, "de": _TTS_CAST_DE,
    "fr": _TTS_CAST_FR, "nl": _TTS_CAST_NL, "it": _TTS_CAST_IT,
    "ja": _TTS_CAST_JA,
}

# The app stores a film's language as FREE TEXT ("English", "Spanish", "日本語").
# This is the join to the seven Aura-2 speaks. ⚠ A NAME NOT IN HERE IS A REFUSAL,
# not a fallback: the model would read the words with the wrong phonetics and
# charge for it. `tts.preflight` turns that into a sentence naming the `.env`
# line to change, before anything is spent.
_TTS_LANGUAGE_CODES = {
    "english": "en", "en": "en", "en-us": "en", "en-gb": "en", "en-in": "en",
    "american english": "en", "british english": "en", "indian english": "en",
    "spanish": "es", "español": "es", "espanol": "es", "es": "es",
    "es-es": "es", "es-mx": "es", "es-419": "es", "castilian": "es",
    "german": "de", "deutsch": "de", "de": "de", "de-de": "de",
    "french": "fr", "français": "fr", "francais": "fr", "fr": "fr", "fr-fr": "fr",
    "dutch": "nl", "nederlands": "nl", "nl": "nl", "nl-nl": "nl", "flemish": "nl",
    "italian": "it", "italiano": "it", "it": "it", "it-it": "it",
    "japanese": "ja", "日本語": "ja", "ja": "ja", "ja-jp": "ja", "nihongo": "ja",
}

# What a film with no language set is read as. English, because that is what the
# API itself assumes and what the default voice speaks — the one place in this
# module where the assumption is documented behaviour rather than a guess.
DEFAULT_TTS_LANGUAGE = "en"


def tts_language_code(language: str = "") -> str:
    """Which of Aura-2's languages this film is, or "" — WHICH MEANS REFUSE.

    ⚠ "" IS NOT "LET THE MODEL DECIDE". There is nothing to decide: the voice
    name carries the language, so an unmapped film would be read by an English
    voice with English phonetics and billed for. A BLANK language is different
    from an unknown one — nobody has said, and `DEEPGRAM_TTS_LANGUAGE` (default
    English) answers for that.
    """
    key = (language or "").strip().lower()
    if not key:
        return (os.environ.get("DEEPGRAM_TTS_LANGUAGE") or "").strip().lower() \
            or DEFAULT_TTS_LANGUAGE
    return _TTS_LANGUAGE_CODES.get(key, "")


def tts_speaks(language: str = "") -> bool:
    """Can this backend read that language at all? Free, asked BEFORE the run."""
    return bool(tts_language_code(language))


def tts_languages() -> tuple[str, ...]:
    """The languages this module casts for, for an error message to list."""
    return tuple(_TTS_CASTS)


def tts_cast(language: str = "") -> tuple[dict, ...]:
    """The voices this backend offers FOR THIS FILM, in picker order.

    ⚠ ONE PLACE A VOICE NAME EXISTS. The browser's picker is filled from this
    through `tts.cast`, so it cannot offer a name the run would be refused for —
    and because the table is per language, it cannot offer an English voice for
    a Spanish film either.
    """
    return _TTS_CASTS.get(tts_language_code(language) or DEFAULT_TTS_LANGUAGE, _TTS_CAST_EN)


def tts_voices(language: str = "") -> tuple[str, ...]:
    """Just the names for this language, deduplicated, in cast order."""
    seen: list[str] = []
    for entry in tts_cast(language):
        if entry["name"] not in seen:
            seen.append(entry["name"])
    return tuple(seen)


def tts_model_id(language: str = "") -> str:
    """The default voice/model for this film. Overridable — a model id ages first.

    ⚠ THE OVERRIDE IS IGNORED WHEN IT IS FOR THE WRONG LANGUAGE, and that is not
    tidiness: `DEEPGRAM_TTS_MODEL=aura-2-thalia-en` set for an English project
    would otherwise read a Spanish film in English phonetics the day somebody
    changed the project's language, silently, for as long as nobody listened.
    """
    named = (os.environ.get("DEEPGRAM_TTS_MODEL") or "").strip().lower()
    if named:
        if named in tts_voices(language):
            return named
        logger.warning(
            "[deepgram] DEEPGRAM_TTS_MODEL=%s does not speak %s — using %s instead",
            named, tts_language_code(language) or "?", tts_cast(language)[0]["name"],
        )
    return tts_cast(language)[0]["name"]


def tts_default_voice(language: str = "") -> str:
    """Who reads a line nothing has cast, in this film's language."""
    return tts_model_id(language)


def tts_resolve_voice(voice: str | None, language: str = "") -> str:
    """A voice this backend has FOR THIS LANGUAGE, or the default.

    Unknown names fold down rather than failing, for the same reason
    `tts.resolve_voice` does: a stale name in a stored request must cost a
    timbre, not a paid run. ⚠ AND A VOICE FROM ANOTHER LANGUAGE IS "UNKNOWN"
    HERE — `aura-2-thalia-en` on a German board is exactly the mistake this
    catches, and `tts.voice_for` re-casts it through the persona instead.
    """
    name = (voice or "").strip().lower()
    if name in tts_voices(language):
        return name
    return tts_default_voice(language)


def tts_entry_for(persona: str | None = None, language: str = "") -> dict:
    """The cast row for that kind of character, or the default row."""
    table = tts_cast(language)
    key = (persona or "").strip().lower()
    if key:
        for entry in table:
            if entry["persona"] == key:
                return entry
    return table[0]


def tts_voice_for_persona(persona: str | None = None, language: str = "") -> str:
    """The voice this project casts for that kind of character, in this language."""
    return tts_entry_for(persona, language)["name"]


def tts_persona_note(persona: str | None = None, language: str = "") -> str:
    """⚠ WHAT THIS BACKEND CANNOT ACTUALLY DELIVER FOR THAT PART. "" when it can.

    Aura-2 publishes no child voices and no elderly ones in most languages, and
    French has two voices in total. Rather than quietly casting an adult as a
    child, the row says so and this carries it to the dialog — the user then
    either overrides the voice, or switches to `VOICE_PROVIDER=gemini`, which
    has real child voices. A promise silently broken is worse than one declined.
    """
    return str(tts_entry_for(persona, language).get("approx") or "")


def tts_usd_per_1k_chars() -> float:
    """The advisory rate a run is quoted at, USD per 1,000 characters."""
    return _rate("DEEPGRAM_TTS_USD_PER_1K", DEFAULT_TTS_USD_PER_1K_CHARS)


def tts_missing_key_hint() -> str:
    """The sentence shown when this backend is chosen and has no key.

    ⚠ IT NAMES THE VARIABLE TO EDIT — the same rule `ai_keys.missing_key_hint`
    follows, and the reason the providers were split at all.
    """
    return (
        f"VOICE_PROVIDER=deepgram needs a key: set {API_KEY_ENV} in your .env "
        "(from https://console.deepgram.com/). Or set VOICE_PROVIDER=gemini to "
        "read the dialogue on Gemini instead."
    )


# Sentence ends, for splitting a line too long to send in one request. The
# Devanagari danda is not here on purpose — this backend refuses Indic languages
# before any splitting happens.
_TTS_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def tts_chunks(text: str, limit: int = TTS_MAX_CHARS) -> list[str]:
    """One line split into pieces this API will accept, at the least bad seam.

    ⚠ SENTENCES FIRST, THEN WORDS, THEN A HARD CUT — a seam mid-word is audible,
    a seam mid-sentence is a small extra breath. The pieces are spoken back to
    back with no gap (the PCM is concatenated), so the sentence seam very nearly
    disappears. A line short enough to send comes back as one piece.
    """
    body = (text or "").strip()
    if len(body) <= limit:
        return [body] if body else []

    out: list[str] = []
    piece = ""
    for sentence in _TTS_SENTENCE_END.split(body):
        if not sentence:
            continue
        if len(piece) + len(sentence) + 1 <= limit:
            piece = f"{piece} {sentence}".strip()
            continue
        if piece:
            out.append(piece)
            piece = ""
        while len(sentence) > limit:
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
def speak(text: str, *, voice: str | None = None, language: str = "") -> bytes:
    """SPENDS QUOTA. Read one line aloud. Returns raw PCM in the house format.

    ⚠ A LINE TOO LONG FOR ONE REQUEST IS SENT AS SEVERAL AND CONCATENATED. The
    pieces are PCM at one fixed rate, so joining them is `+` and needs no
    decoder — the fact the whole timing model in `tts.py` rests on.
    """
    body = (text or "").strip()
    if not body:
        raise DeepgramSpeechError("There is nothing to read aloud.")

    key = api_key()
    if not key:
        raise DeepgramSpeechError(tts_missing_key_hint())

    if not tts_speaks(language):
        raise DeepgramSpeechError(
            f"Deepgram's Aura voices do not speak {language.strip()}. Aura-2 "
            "reads English, Spanish, German, French, Dutch, Italian and "
            "Japanese. For an Indian language set VOICE_PROVIDER=sarvam in your "
            ".env, or VOICE_PROVIDER=gemini for anything else."
        )

    # ⚠ THE LANGUAGE PICKS THE VOICE, NOT JUST THE WORDS. A voice from another
    # language folds to this one's default here rather than being sent — see
    # `tts_resolve_voice`.
    model = tts_resolve_voice(voice, language)
    pieces = tts_chunks(body)
    logger.info(
        "[deepgram] speaking %d character(s) as %s (%s, %d request(s), ~$%.4f)",
        len(body), model, tts_language_code(language), len(pieces),
        len(body) / 1000.0 * tts_usd_per_1k_chars(),
    )
    pcm = bytearray()
    for piece in pieces:
        pcm += _speak_once(piece, key=key, model=model)
    if not pcm:
        raise DeepgramSpeechError("Deepgram returned no audio for that line.")
    return bytes(pcm)


def _speak_once(text: str, *, key: str, model: str) -> bytes:
    """ONE request, retried on the failures a retry can actually fix.

    ⚠ RETRIES EXIST HERE AND NOT IN `transcribe` FOR A REASON. A transcription is
    ONE call and a person can press the button again. A voiceover is one call PER
    LINE — forty of them on a board — so a single 429 halfway through would throw
    away everything already spoken AND already paid for. The ladder is
    `retry_policy`'s, the same one the image and video backends wait on, so there
    is one tuned answer to "how long is a 429".
    """
    params = {
        "model": model,
        # ⚠ NOT THE DEFAULTS. Deepgram answers MP3 unless told otherwise, and an
        # MP3 would need a decoder this install does not have. linear16 in a WAV
        # at the house rate is the one shape `_tts_pcm_of_wav` will accept.
        "encoding": "linear16",
        "sample_rate": str(TTS_SAMPLE_RATE),
        "container": "wav",
    }
    last: Exception | None = None
    for attempt in range(1, retry_policy.MAX_RETRIES + 1):
        try:
            res = requests.post(
                SPEAK_URL,
                params=params,
                headers={
                    "Authorization": f"Token {key}",
                    "Content-Type": "application/json",
                },
                json={"text": text},
                timeout=TTS_TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            last = DeepgramSpeechError(
                "Deepgram did not answer in time. Try again, or read this film "
                "on Gemini with VOICE_PROVIDER=gemini."
            )
            logger.warning(
                "[deepgram] speak timed out on attempt %d/%d", attempt, retry_policy.MAX_RETRIES
            )
        except requests.RequestException as exc:
            last = DeepgramSpeechError(f"Could not reach Deepgram: {exc}")
            logger.warning("[deepgram] speak transport error on attempt %d: %s", attempt, exc)
        else:
            if res.status_code < 400:
                return _tts_pcm_of_wav(res.content)
            error = _speak_error_for(res, model)
            if not _tts_retryable(res.status_code) or attempt == retry_policy.MAX_RETRIES:
                raise error
            last = error
            logger.warning(
                "[deepgram] speak %s on attempt %d/%d",
                res.status_code, attempt, retry_policy.MAX_RETRIES,
            )
        if attempt == retry_policy.MAX_RETRIES:
            break
        time.sleep(retry_policy.backoff_delay(attempt, last))
    raise last or DeepgramSpeechError("Deepgram could not be reached.")


def _tts_retryable(status: int) -> bool:
    """Only the codes a second attempt can turn into a success. 429 refills; 402
    (out of credit) does not, so it is not here."""
    return status == 429 or 500 <= status < 600


def _speak_error_for(res, model: str = "") -> DeepgramSpeechError:
    """Turn Aura's HTTP codes into sentences a person can act on.

    ⚠ THE ERROR HAS TO NAME THE LINE TO CHANGE — the same rule
    `_raise_for_status` follows for the listening half, and for the same reason.
    """
    detail = ""
    try:
        body = res.json()
        detail = str(body.get("err_msg") or body.get("message") or body.get("error") or "")
    except ValueError:
        detail = (res.text or "")[:200]

    if res.status_code in (401, 403):
        return DeepgramSpeechError(
            f"Deepgram refused the key ({API_KEY_ENV}). Check it at "
            f"https://console.deepgram.com/ — or set VOICE_PROVIDER=gemini to "
            f"read the dialogue on Gemini instead. [{res.status_code}] {detail}"
        )
    if res.status_code in (402, 429):
        return DeepgramSpeechError(
            "Deepgram's free credit or rate limit is used up. Top it up, or set "
            "VOICE_PROVIDER=gemini in your .env and restart to read the dialogue "
            f"on Gemini instead. [{res.status_code}] {detail}"
        )
    if res.status_code == 413:
        return DeepgramSpeechError(
            f"That line is longer than Deepgram accepts ({TTS_MAX_CHARS} "
            f"characters per request). [413] {detail}"
        )
    if res.status_code == 400:
        return DeepgramSpeechError(
            "Deepgram refused that request — usually a voice name it does not "
            f"have (check DEEPGRAM_TTS_MODEL; this run asked for "
            f"{model or 'its default'}). [400] {detail}"
        )
    return DeepgramSpeechError(f"Deepgram's voice failed [{res.status_code}] {detail}")


def _tts_pcm_of_wav(data: bytes) -> bytes:
    """The samples out of a WAV, REFUSING anything that is not the house format.

    ⚠ THIS CHECK IS THE POINT OF THE FUNCTION, not a formality. Everything
    downstream measures a sound by COUNTING ITS BYTES at one assumed rate
    (`tts._ms_of_bytes`), so a 16 kHz file accepted here would play half again
    too slow, and every caption and every stretched shot built from those
    numbers would be wrong by that much with nothing on screen to say so. There
    is no resampler on this install to fix it with (`audioop` is gone in this
    Python), so the honest move is to refuse and name the setting.

    ⚠ `sarvam.py` HAS THE SAME CHECK, DELIBERATELY DUPLICATED. Each vendor
    client is standalone and names its OWN env var in the error — a shared
    helper would have to say "some provider" in the one sentence whose whole job
    is to say which line to change.
    """
    if not data:
        raise DeepgramSpeechError("Deepgram returned an empty audio file.")
    try:
        with wave.open(io.BytesIO(data), "rb") as src:
            channels, width, rate = src.getnchannels(), src.getsampwidth(), src.getframerate()
            pcm = src.readframes(src.getnframes())
    except (wave.Error, EOFError) as exc:
        raise DeepgramSpeechError(
            "Deepgram's audio was not the WAV this app asked for. Leave the "
            "encoding and container settings alone."
        ) from exc
    if (channels, width, rate) != (TTS_CHANNELS, TTS_SAMPLE_WIDTH, TTS_SAMPLE_RATE):
        raise DeepgramSpeechError(
            f"Deepgram returned {rate} Hz / {channels}ch audio; this app lays "
            f"voiceover at {TTS_SAMPLE_RATE} Hz mono and cannot resample."
        )
    return pcm
