"""
deepgram.py — TRANSCRIPTION: an audio file in, timed spoken lines out.

A provider client, like `freesound.py`, `meshy.py` and `tripo.py`: it talks to
somebody else's HTTP API and knows nothing about FastAPI, jobs or the timeline.
The module that decides WHEN to call it is `captions.py`; the route that files
the result into a project is in `server/animatics.py`.

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

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LISTEN_URL = "https://api.deepgram.com/v1/listen"

# ⚠ THE KEY IS NAMED FOR THE VENDOR, NOT FOR THE CAPABILITY, AND IT IS THEREFORE
# NOT A SWITCH. `GEMINI_KEY_CAPTION` moves the captions on its own because the
# name says both who is paid AND what for. `DEEPGRAM_API_KEY` says only who — and
# Deepgram sells speech-to-text AND text-to-speech, so a key that quietly claimed
# every capability it could serve would move the VOICEOVER too the day an Aura
# adapter lands. Nobody asked for that. Say `CAPTION_PROVIDER=deepgram`.
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
