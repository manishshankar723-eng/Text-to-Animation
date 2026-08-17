"""Dialogue → a spoken voiceover track, timed to the shots it belongs to.

The board already knows who says what and in which shot; the animatic already
knows where that shot sits on the timeline. So a scratch voiceover does not need
to be recorded, or hand-synced: every line's target time is data we already
hold, and this module reads the lines aloud and lays them at those times.

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

# The prebuilt voices offered in the picker. A short list on purpose: a wall of
# thirty names is a worse choice than six, and these six cover the range
# (bright / upbeat / firm / informative / excitable / breezy).
VOICES = ("Kore", "Puck", "Charon", "Zephyr", "Fenrir", "Aoede")
DEFAULT_VOICE = "Kore"

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


# ---------------------------------------------------------------------------
# The estimate — FREE, and shown before anything is spent
# ---------------------------------------------------------------------------
def estimate(lines: list[dict]) -> dict:
    """What reading these lines aloud should cost. Advisory; spends nothing.

    Counts the SAME characters `synthesise_timed` would send, so the number on
    the confirm dialog is the number the run is priced from.
    """
    texts = [str((line or {}).get("text") or "").strip() for line in (lines or [])]
    texts = [t for t in texts if t]
    characters = sum(len(t) for t in texts)
    return {
        "lines": len(texts),
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
    """SPENDS QUOTA. Read one line aloud. Returns raw PCM (see the constants).

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
# Lines → one track, and the timings that come free with it
# ---------------------------------------------------------------------------
def synthesise_timed(
    lines: list[dict],
    *,
    voice: str | None = None,
    provider: str | None = None,
    progress_cb=None,
) -> tuple[bytes, list[dict]]:
    """SPENDS QUOTA. Read every line and lay them out at their target times.

    `lines` is `[{"text": …, "start_ms": …, "voice": …?}]` — `start_ms` being
    where the line WANTS to be, which for dialogue is the start of the shot it
    belongs to. Returns the finished WAV and the timings the lines ACTUALLY got.

    ⚠ THE TIMINGS COME BACK BECAUSE A LINE CANNOT ALWAYS HAVE WHAT IT ASKED FOR.
    A shot may hold for two seconds and its line take three; the next line then
    starts late rather than being spoken over the top of it. Returning what
    happened — rather than echoing what was requested — is what lets the caller
    build captions that match the audio instead of captions that match a plan.
    The alternative (cutting the speech to fit) makes a voiceover that is
    correct on the timeline and unlistenable.

    A per-line voice is honoured, so two characters can be read by two voices in
    one pass; `voice` is the default for lines that don't name one.
    """
    usable = [
        {
            "text": str((line or {}).get("text") or "").strip(),
            "start_ms": max(0, int((line or {}).get("start_ms") or 0)),
            "voice": (line or {}).get("voice"),
        }
        for line in (lines or [])
    ]
    usable = [line for line in usable if line["text"]]
    if not usable:
        raise VoiceoverError("None of those lines have anything to say.")

    characters = sum(len(line["text"]) for line in usable)
    if characters > MAX_CHARACTERS:
        raise VoiceoverError(
            f"That is {characters:,} characters of dialogue; the limit for one "
            f"run is {MAX_CHARACTERS:,}. Do it in passes — this is a spend "
            "guard, not a technical one."
        )

    usable.sort(key=lambda line: line["start_ms"])
    track = bytearray()
    timings: list[dict] = []
    clock = 0
    for i, line in enumerate(usable):
        if progress_cb:
            progress_cb(i, len(usable), line["text"])
        pcm = speak(line["text"], voice=line["voice"] or voice, provider=provider)
        at = max(clock, line["start_ms"])
        track += silence(at - clock)
        track += pcm
        duration = pcm_duration_ms(pcm)
        timings.append({
            "start_ms": at,
            "end_ms": at + duration,
            "text": line["text"],
        })
        clock = at + duration + GAP_MS
        track += silence(GAP_MS)

    logger.info(
        "[tts] %d line(s) read as %s (%.1fs of audio)",
        len(usable), resolve_voice(voice), pcm_duration_ms(bytes(track)) / 1000,
    )
    return wav_bytes(bytes(track)), timings
