"""Auto-captions — an audio track in, timed caption clips out.

The animatic already holds a voiceover or a scratch recording on its audio
layer. This turns that into `AnimaticTextClip`s that appear when the words are
said, which is the only kind of caption anybody actually wants to make by hand
and never does.

⚠ THIS MODULE SPENDS AI QUOTA — it is the first thing in the animatic editor
other than ✨ Animate that does. It therefore follows the same discipline, and
for the same reason (see the 2026-08-07 Work Log entry):

    1. `estimate()` is FREE and is what fills the confirm dialog, so the price
       is on screen before the button that spends it.
    2. `MAX_AUDIO_SECONDS` caps one run. This is a SPEND guard, not a technical
       one — the model would happily read an hour.
    3. The estimate and the run are computed from the same numbers, so the
       quote can never drift from the work.

TWO HALVES, AND ONLY ONE OF THEM COSTS ANYTHING:

    transcribe()   — the model call. One request, audio in, lines out.
    tidy_lines()   — pure. Overlaps removed, minimum durations enforced, long
                     lines split into readable ones with their times shared out.
                     No network, no quota, fully testable, and where every bug
                     that shows up as "the subtitles are on top of each other"
                     actually lives.

`tests/captions_check.py` drives the second half against a stub transcriber, so
the timing rules are proven without a key and without spending anything.
"""

from __future__ import annotations

import json
import logging
import os

from google.genai import types

import script_breakdown

logger = logging.getLogger(__name__)

# --- Spend guards -----------------------------------------------------------
# One run's ceiling, in seconds of audio. Generous for a scene (20 minutes) and
# nowhere near enough to transcribe a feature by accident.
MAX_AUDIO_SECONDS = float(os.environ.get("API_MAX_CAPTION_SECONDS", "1200"))

# Advisory rate, in US dollars per second of audio, for the estimate. Gemini
# bills audio input as tokens (~32 tokens/second at the time of writing) and
# output text is a rounding error beside it. Like every other price in this
# codebase this is a LIST price we quote, not a bill we issue — only Google
# bills, and the number is labelled advisory in the UI.
USD_PER_AUDIO_SECOND = float(os.environ.get("API_CAPTION_USD_PER_SECOND", "0.000012"))

# --- Timing rules -----------------------------------------------------------
# A caption shorter than this is a flash nobody can read. Enforced by extending
# the line, never by dropping it — a missing subtitle is worse than a fast one.
# It is only ever extended TO this, never beyond, so a line followed by thirty
# seconds of silence still leaves when it stopped being spoken.
MIN_LINE_MS = 800
# Left between two consecutive captions so they read as two, not as one that
# changed. Small: a real gap between sentences is usually much larger, and this
# only bites when the speaker doesn't pause.
GAP_MS = 60
# Longest a single caption line gets before it is split in two. Two lines of ~42
# characters is the broadcast subtitle convention and it is the convention
# because it is what a person can read in one glance.
MAX_CHARS = 84

# Every clip this module makes is named `cap…`, and that prefix is the ONLY
# record that a caption was generated rather than typed. It is what lets a
# second run replace the first instead of doubling every subtitle, and it is
# deliberately not a field on the clip: a generated caption must be an ordinary
# caption in every other respect, or half the inspector stops applying to it.
CAPTION_ID_PREFIX = "cap"


class CaptionError(Exception):
    """Raised when an audio track can't be turned into captions.

    Carries a human-readable reason so the API can say what actually went wrong
    rather than "transcription failed".
    """


# ---------------------------------------------------------------------------
# The estimate — FREE, and shown before anything is spent
# ---------------------------------------------------------------------------
def estimate(duration_ms: int) -> dict:
    """What captioning this much audio should cost. Advisory; spends nothing.

    `duration_ms` comes from the audio TRACK, which the browser measured with
    `decodeAudioData` when the file was uploaded. ⚠ There is no ffprobe on an
    imageio-ffmpeg install, so the caller is the only thing that knows how long
    a sound file is — see `video_assemble.py`. Do not "improve" this by
    measuring the file here.
    """
    seconds = max(0.0, float(duration_ms or 0) / 1000.0)
    return {
        "seconds": round(seconds, 1),
        "usd": round(seconds * USD_PER_AUDIO_SECOND, 4),
        "model": script_breakdown.text_model_id(),
        "over_limit": seconds > MAX_AUDIO_SECONDS,
        "limit_seconds": MAX_AUDIO_SECONDS,
    }


# ---------------------------------------------------------------------------
# The model call — the half that costs money
# ---------------------------------------------------------------------------
_SYSTEM_INSTRUCTION = (
    "You are a subtitle editor. You listen to an audio track and write the "
    "SUBTITLES for it, in the order they are spoken.\n"
    "- Transcribe only what is actually said. Never invent, summarise or "
    "translate, and never describe sounds, music or silence.\n"
    "- One entry per spoken sentence or clause, broken where a person would "
    "naturally break a subtitle.\n"
    "- start_ms and end_ms are milliseconds from the START of the audio. They "
    "must increase, and entries must not overlap.\n"
    "- end_ms is when the speaker stops saying that line, not when the next "
    "one begins.\n"
    "- Keep the speaker's own words, punctuation and capitalisation. Do not "
    "add speaker names unless they are spoken.\n"
    "- If nothing is said, return an empty list rather than a guess."
)

# Audio formats the model accepts, by the extension we stored the upload under.
# Anything else is refused BEFORE the call rather than after, because a rejected
# request is billed exactly like a successful one.
MIME_TYPES = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}


def mime_type_for(path: str) -> str | None:
    """The mime type to send this file as, or None if it isn't audio we know."""
    return MIME_TYPES.get(os.path.splitext(path or "")[1].lower())


def _schema() -> types.Schema:
    """The response shape. Asked for as a SCHEMA rather than described in the
    prompt, so a malformed answer is impossible rather than merely unlikely."""
    return types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(
            type=types.Type.OBJECT,
            required=["start_ms", "end_ms", "text"],
            properties={
                "start_ms": types.Schema(type=types.Type.INTEGER),
                "end_ms": types.Schema(type=types.Type.INTEGER),
                "text": types.Schema(type=types.Type.STRING),
            },
        ),
    )


def transcribe(path: str, *, language: str = "", provider: str | None = None) -> list[dict]:
    """SPENDS QUOTA. Listen to one audio file and return its spoken lines.

    Returns raw model output, ordered but not yet tidied: `tidy_lines` is what
    makes it safe to draw. Kept separate so the timing rules can be tested
    without a key — and because a re-tidy after an edit must not re-bill.
    """
    mime = mime_type_for(path)
    if not mime:
        raise CaptionError(
            "That audio format can't be captioned. Use MP3, WAV, M4A, AAC, OGG "
            "or FLAC."
        )
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise CaptionError("The audio file for that track has gone missing.") from exc
    if not data:
        raise CaptionError("That audio track is empty.")

    client = script_breakdown.get_client(provider)
    model_id = script_breakdown.text_model_id(provider)
    prompt = "Write the subtitles for this audio."
    if language.strip():
        prompt += f" The speech is in {language.strip()}; transcribe it in that language."

    logger.info("[captions] transcribing %s (%.1f KB) with %s", path, len(data) / 1024, model_id)
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=[
                types.Part.from_bytes(data=data, mime_type=mime),
                prompt,
            ],
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=_schema(),
                temperature=0.0,
            ),
        )
    except Exception as exc:  # the SDK raises a wide family of transport errors
        raise CaptionError(f"The transcription call failed: {exc}") from exc

    payload = getattr(response, "text", None)
    if not payload:
        raise CaptionError(
            "The model returned nothing for that audio — it may have been "
            "blocked, or the track may contain no speech."
        )
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CaptionError(f"The model returned invalid JSON ({exc}).") from exc
    if not isinstance(raw, list):
        raise CaptionError("The model returned something that isn't a list of lines.")

    lines = [
        line
        for line in (_coerce_line(item) for item in raw)
        if line is not None
    ]
    logger.info("[captions] %d line(s) transcribed from %s", len(lines), os.path.basename(path))
    return lines


def _coerce_line(item) -> dict | None:
    """One model entry into a line, or None if it isn't usable.

    Forgiving on purpose: a single malformed entry in a forty-line transcript
    should cost that line, not the whole (already paid for) run.
    """
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or "").strip()
    if not text:
        return None
    try:
        start = int(float(item.get("start_ms") or 0))
        end = int(float(item.get("end_ms") or 0))
    except (TypeError, ValueError):
        return None
    return {"start_ms": max(0, start), "end_ms": max(0, end), "text": text}


# ---------------------------------------------------------------------------
# The timing rules — pure, free, and where the bugs live
# ---------------------------------------------------------------------------
def tidy_lines(
    lines: list[dict],
    *,
    total_ms: int | None = None,
    offset_ms: int = 0,
    max_chars: int = MAX_CHARS,
) -> list[dict]:
    """Make a transcript SAFE TO DRAW: in order, non-overlapping, readable.

    Four rules, in this order, and the order matters:

      1. **Split first.** A 30-word line is split into readable ones and its
         time shared out by character count, so the pieces are timed before
         anything else reasons about their neighbours.
      2. **Order, and never overlap.** A line starting before the one in front
         of it ended is pushed to just after it. Overlapping subtitles are the
         single most common failure of an auto-caption pass, and they look like
         a bug in the editor rather than in the transcript.
      3. **Long enough to read**, but never at the cost of rule 2: a line is
         extended toward MIN_LINE_MS only into the gap that is actually there.
      4. **Inside the video.** A line past the end is cut, and one that ends up
         with no room at all is dropped — a zero-length caption is a flash.

    `offset_ms` is where the audio track SITS on the timeline. Transcript times
    are relative to the file; clips are absolute. Forgetting this is what makes
    captions correct on a track that starts at zero and wrong on every other.
    """
    out: list[dict] = []
    for line in sorted(lines, key=lambda l: (l.get("start_ms") or 0, l.get("end_ms") or 0)):
        out.extend(_split_line(line, max_chars))

    tidied: list[dict] = []
    for line in out:
        start = max(0, int(line["start_ms"]) + offset_ms)
        end = max(start + 1, int(line["end_ms"]) + offset_ms)
        if tidied and start < tidied[-1]["end_ms"] + GAP_MS:
            start = tidied[-1]["end_ms"] + GAP_MS
            end = max(end, start + 1)
        tidied.append({"start_ms": start, "end_ms": end, "text": line["text"]})

    # Rule 3, and it runs AFTER every start is final — a line may only grow into
    # the space actually in front of it, and that space isn't known until the
    # pass above has finished pushing starts apart. `max(end, ...)` on the
    # ceiling is what stops this SHORTENING a line that was legitimately long
    # and simply ran up against its neighbour: rule 3 may only ever extend.
    for i, line in enumerate(tidied):
        target = max(line["end_ms"], line["start_ms"] + MIN_LINE_MS)
        if i + 1 < len(tidied):
            target = min(target, max(line["end_ms"], tidied[i + 1]["start_ms"] - GAP_MS))
        line["end_ms"] = target

    if total_ms:
        clipped = []
        for line in tidied:
            if line["start_ms"] >= total_ms:
                continue
            line["end_ms"] = min(line["end_ms"], total_ms)
            if line["end_ms"] - line["start_ms"] < 1:
                continue
            clipped.append(line)
        tidied = clipped
    return tidied


def _split_line(line: dict, max_chars: int) -> list[dict]:
    """One long line into several readable ones, sharing out its time.

    Split at word boundaries, and the time each piece gets is PROPORTIONAL TO
    ITS CHARACTER COUNT rather than an equal share — speech takes about as long
    as it is long, and an equal share puts a two-word piece on screen for as
    long as a twelve-word one.
    """
    text = (line.get("text") or "").strip()
    start = int(line.get("start_ms") or 0)
    end = max(start + 1, int(line.get("end_ms") or 0))
    if len(text) <= max_chars:
        return [{"start_ms": start, "end_ms": end, "text": text}]

    pieces: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and len(trial) > max_chars:
            pieces.append(current)
            current = word
        else:
            current = trial
    if current:
        pieces.append(current)
    if len(pieces) <= 1:
        return [{"start_ms": start, "end_ms": end, "text": text}]

    span = end - start
    total_chars = sum(len(p) for p in pieces) or 1
    out: list[dict] = []
    clock = start
    for i, piece in enumerate(pieces):
        share = span * len(piece) / total_chars
        piece_end = end if i == len(pieces) - 1 else int(round(clock + share))
        out.append({"start_ms": int(clock), "end_ms": max(int(clock) + 1, piece_end), "text": piece})
        clock = piece_end
    return out


# ---------------------------------------------------------------------------
# Lines → caption clips
# ---------------------------------------------------------------------------
def caption_clips(lines: list[dict], *, layer_id: str = "", style: dict | None = None) -> list[dict]:
    """Timed lines into `AnimaticTextClip` dicts, ready to save.

    The style defaults are a SUBTITLE, not a title card: bottom of frame, small,
    on a scrim. They are ordinary clip fields, so every one of them can be
    changed afterwards in the inspector and each clip is an ordinary caption
    from the moment it exists — there is no such thing as a "caption clip" that
    behaves differently from one you typed.
    """
    base = {
        "layer_id": layer_id,
        "position": "bottom",
        "align": "center",
        "size": "small",
        "color": "#ffffff",
        "backdrop": "scrim",
        "font": "inter",
        "place": "flow",
        **(style or {}),
    }
    clips = []
    for i, line in enumerate(lines):
        duration = max(100, int(line["end_ms"]) - int(line["start_ms"]))
        clips.append({
            **base,
            # PREFIXED, so a second captions run can find and replace its own
            # clips instead of piling a second copy of every subtitle on top of
            # the first — the server drops every `cap…` clip before adding
            # these. Random after that, so two runs can never collide.
            "id": f"{CAPTION_ID_PREFIX}{i:04d}{os.urandom(3).hex()}",
            "text": line["text"],
            "start_ms": int(line["start_ms"]),
            "duration_ms": duration,
        })
    return clips
