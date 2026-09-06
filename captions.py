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

FIVE PARTS, AND ONLY ONE OF THEM COSTS ANYTHING:

    transcribe()   — the model call. One request, audio in, lines out. ⚠ Its
                     WORDS are excellent and its TIMES are a guess; everything
                     below exists because of that asymmetry.
    speech_spans() — free, no model. Where the sound actually is in the file,
                     measured by ffmpeg. The waveform the user is looking at,
                     as numbers.
    align_lines()  — pure. The model's words laid onto the measured sound, which
                     is what makes a caption appear when the word is SAID rather
                     than when the model thought it was. Read its docstring
                     before touching caption timing.
    clip_lines()   — pure. The transcript is of the FILE; the timeline holds
                     CLIPS cut out of that file. This is what puts the words
                     where they are actually heard, and drops the ones that were
                     cut out.
    tidy_lines()   — pure. Overlaps removed, minimum durations enforced, long
                     lines split into readable ones with their times shared out.
                     No network, no quota, fully testable, and where every bug
                     that shows up as "the subtitles are on top of each other"
                     actually lives.

`tests/captions_check.py` drives the pure ones against a stub transcriber and a
stub ffmpeg log, so the timing rules are proven without a key, without ffmpeg and
without spending anything.
"""

from __future__ import annotations

import array
import bisect
import json
import logging
import os
import subprocess
import sys

from google.genai import types

import ai_keys
import animatic_fonts
import deepgram
import script_breakdown

logger = logging.getLogger(__name__)

# --- Whose bill this lands on -----------------------------------------------
# ⚠ CAPTIONS ARE THEIR OWN CAPABILITY, AND THE REASON IS THE PRICE SHAPE. This is
# billed per SECOND OF AUDIO while the breakdown beside it is billed per RENDER,
# and on one key the two cannot be told apart. It is also the capability most
# worth pointing somewhere else: a dedicated transcription backend is an order of
# magnitude cheaper per audio-second than a general text model, and swapping it
# has to be a line in `.env` rather than an edit here.
#
# `CAPTION_PROVIDER` and `GEMINI_KEY_CAPTION`, falling back to `TEXT_PROVIDER`
# so a deployment that has never heard of either keeps working. See `ai_keys`.
CAPABILITY = "caption"

# ⚠ `deepgram` IS HERE AND NOT IN THE TEXT LIST, and that asymmetry is the point.
# Every other capability in this app is "which Google backend"; this one can leave
# Google entirely, because transcription is a solved commodity with real
# competition and because a speech engine MEASURES word timings where a language
# model INFERS them. `vertex`/`gemini` still resolve through `script_breakdown`;
# `deepgram` goes to `deepgram.py` and never touches a genai client.
SUPPORTED_PROVIDERS = ("vertex", "gemini", "deepgram")


def resolve_provider(provider: str | None = None) -> str:
    """The backend that will transcribe: explicit > CAPTION_* > TEXT_PROVIDER > vertex.

    ⚠ `DEEPGRAM_API_KEY` IS NOT A SWITCH — say `CAPTION_PROVIDER=deepgram`. A
    vendor-named key says who is paid but not what for, and Deepgram sells TTS
    as well, so a key that moved a capability on its own would move the voiceover
    too the day an Aura adapter lands. See `deepgram.API_KEY_ENV`.
    """
    p = ai_keys.resolve_provider(CAPABILITY, provider, fallback=("TEXT_PROVIDER",))
    if p not in SUPPORTED_PROVIDERS:
        raise CaptionError(
            f"Unknown CAPTION_PROVIDER '{p}'. Use one of {SUPPORTED_PROVIDERS}."
        )
    return p


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
#
# ⚠ IT COMES OUT OF THE EARLIER LINE'S END, NEVER OFF THE NEXT LINE'S START —
# see `tidy_lines` rule 2. Taking it off the start delayed every caption after
# the first by this much, which is a caption arriving after its own word.
GAP_MS = 60

# The shortest a caption may be TRIMMED to in order to make room for the next
# one. A line that would have to go below this is left alone and the next one is
# pushed instead: the alternative is a subtitle that blinks. Comfortably under
# MIN_LINE_MS, because this is the squeezed case, not the normal one.
MIN_HOLD_MS = 400
# Longest a single caption line gets before it is split in two. Two lines of ~42
# characters is the broadcast subtitle convention and it is the convention
# because it is what a person can read in one glance.
MAX_CHARS = 84

# ⚠ AND THE CAP THAT ACTUALLY BITES, BECAUSE `MAX_CHARS` ALONE DID NOT. Eighty-four
# characters is a legal subtitle and still a bad one: "We won't just be kings, we
# will be gods, and no middle-class trash will ever stand" is 81 characters, so it
# came through whole — one wall of text sitting under the picture for six seconds
# while the line was read. Reported against the program monitor as the caption
# being too long.
#
# A caption should turn over with the speech, so the cap that matters is WORDS.
# Five is the ceiling and the splitter balances under it — sixteen words become
# four captions of four, never five-five-five-one — so nothing ever flashes a
# single orphaned word.
#
# ⚠ IT IS THE DEFAULT FOR EVERY CALLER, not a switch the voiceover turns on. A
# caption written from a transcript and a caption written from a voiceover are
# the same object doing the same job, and a rule about how much a person can read
# at a glance cannot be true of one and false of the other.
MAX_WORDS = 5

# Where a caption would rather end than in the middle of a clause. Checked one
# word either side of the balanced target, so "…we will be gods," closes a
# caption instead of leaving "gods," to open the next one.
_CLAUSE_END = (",", ".", "?", "!", ";", ":", "…", "—")

# The shortest piece of a CUT line worth keeping. A razor cut through the middle
# of a word leaves a few milliseconds of that line audible on the far side; a
# caption for it would be one word flashing on screen for no reason. Lines that
# were not cut are never dropped, however short they are — "No." is a real
# subtitle and this rule must not reach it.
MIN_PIECE_MS = 150

# --- Aligning the words to the sound ----------------------------------------
# ⚠ THE ANSWER TO "THE CAPTIONS DON'T LINE UP WITH THE WAVEFORM". See
# `align_lines`. These are the knobs of the measurement it runs on.

# ⚠ THE ENVELOPE IS MEASURED THE WAY THE BROWSER DRAWS IT: peak amplitude per
# short window. `client/src/animatic/beats.js::peaksOf` buckets the decoded
# samples by peak to draw the waveform, and this measures the same quantity — so
# a "run of sound" here IS a visible block on the timeline. Measuring anything
# else (ffmpeg's `silencedetect`, an RMS average) puts the caption boxes on
# edges the user cannot see, which is the whole complaint restated.
ENVELOPE_HZ = 8000       # plenty for an amplitude envelope; speech isn't being kept
ENVELOPE_WINDOW_MS = 20  # one bucket of the envelope, and the resolution of an edge

# ⚠ THE THRESHOLD IS RELATIVE TO THE TRACK'S OWN LEVEL, not a fixed dBFS. A
# fixed one (what `silencedetect` does) is wrong in both directions: it hears
# room tone on a noisy upload as speech, and misses a quietly-spoken word on a
# clean one. Two floors, and sound has to clear BOTH:
#   * a multiple of the track's own noise floor, which is what separates speech
#     from hiss on a track that has hiss;
#   * a share of the track's loudest peak, which is roughly where the drawn
#     waveform stops looking flat — the line the eye is actually using.
NOISE_FLOOR_MULTIPLE = 2.5
SOUND_PEAK_SHARE = float(os.environ.get("API_CAPTION_SOUND_SHARE", "0.04"))

# ⚠ AND THE FLOOR IS CAPPED, WHICH IS NOT A DETAIL. "The quietest tenth of the
# track" is only a NOISE floor on a track that has quiet in it; on continuous
# narration with no pause long enough to reach into that tenth, the quietest
# tenth is SPEECH, and a threshold above it measures the whole track as silent —
# no runs, no alignment, and captions quietly falling back to the model's guess
# on exactly the tracks this feature is for. So the threshold may never rise
# above this share of the loudest peak, whatever the floor says.
MAX_THRESHOLD_SHARE = 0.2

# How long a quiet patch has to last before it is a PAUSE rather than the gap
# inside a word. 200ms is longer than any stop consonant and shorter than any
# real pause between sentences.
MIN_SILENCE_MS = int(os.environ.get("API_CAPTION_MIN_SILENCE_MS", "200"))

# A blip of sound this short between two silences is a click, a breath or a door
# — not a line of dialogue, and letting it hold a caption's worth of words would
# squeeze that line into a few milliseconds.
MIN_SPEECH_MS = 120

# Below this share of the file being sound, the measurement is not believed and
# the model's own times are used instead. A voiceover that is 88% silence is a
# detection that went wrong (a noise floor above the threshold, a format ffmpeg
# read oddly), and squeezing every caption into the remaining 12% would be far
# worse than the drift being fixed. ⚠ THE FALLBACK IS THE OLD BEHAVIOUR, so a
# failed measurement can only ever leave captions no worse than they were.
MIN_SOUND_SHARE = 0.12

# Escape hatch. Alignment is on because unaligned captions are the bug; this is
# here so a support answer never has to be "wait for a release".
ALIGN_TO_AUDIO = os.environ.get("API_CAPTION_ALIGN", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# Every clip this module makes is named `cap…`, and that prefix is the ONLY
# record that a caption was generated rather than typed. It is what lets a
# second run replace the first instead of doubling every subtitle, and it is
# deliberately not a field on the clip: a generated caption must be an ordinary
# caption in every other respect, or half the inspector stops applying to it.
CAPTION_ID_PREFIX = "cap"

# --- Where generated captions live -----------------------------------------
# ⚠ A LANE OF THEIR OWN, at the top of the timeline, and this id is how both
# halves of the app agree which lane that is.
#
# WHY IT IS FIXED RATHER THAN GENERATED. It has to be recognisable to the editor
# (which draws that lane first, and labels it) and stable across runs (so a
# second pass lands on the same row instead of adding another one) — and the
# server is the thing that writes it, so it cannot be a random id the browser
# made up. A user's own lanes keep their random ids; this one name is reserved.
#
# WHY A SEPARATE LANE AT ALL. Generated captions used to be dropped onto the
# default text lane, on top of whatever the user had typed there — the two piled
# into one row, overlapping, and a re-run could not tell them apart on screen.
# The lane is the separation: your text stays where you put it, the captions
# arrive above it, and deleting the lane deletes the pass rather than your work.
#
# ⚠ TWIN of `CAPTION_LAYER_ID` / `CAPTION_LAYER_NAME` in
# `client/src/animatic/captions.js`, compared by `tests/captions_check.py`.
CAPTION_LAYER_ID = "captions"
CAPTION_LAYER_NAME = "Captions"


class CaptionError(Exception):
    """Raised when an audio track can't be turned into captions.

    Carries a human-readable reason so the API can say what actually went wrong
    rather than "transcription failed".
    """


# ---------------------------------------------------------------------------
# The estimate — FREE, and shown before anything is spent
# ---------------------------------------------------------------------------
def estimate(duration_ms: int, *, language: str = "") -> dict:
    """What captioning this much audio should cost. Advisory; spends nothing.

    `duration_ms` comes from the audio TRACK, which the browser measured with
    `decodeAudioData` when the file was uploaded. ⚠ There is no ffprobe on an
    imageio-ffmpeg install, so the caller is the only thing that knows how long
    a sound file is — see `video_assemble.py`. Do not "improve" this by
    measuring the file here.

    ⚠ IT PRICES THE BACKEND THAT WILL ACTUALLY RUN, and the two are not close:
    Deepgram Nova-3 is ~$0.0043/minute where we quote Gemini audio at
    ~$0.0007/minute. A dialog that quoted one and spent the other would be worse
    than no dialog, because the number would look checked.

    ⚠ AND `language` IS TAKEN, NOT ASSUMED, because Deepgram charges more for
    `multi` than for a single named language — and the default when nobody says
    is `multi`, which is the DEARER of the two. Quoting the cheaper one and
    spending the dearer is the single direction an advisory price must never be
    wrong in; `tts.estimate` states the same rule for the same reason.
    """
    seconds = max(0.0, float(duration_ms or 0) / 1000.0)
    provider = resolve_provider()
    if provider == "deepgram":
        usd = seconds / 60.0 * deepgram.usd_per_minute(language)
        model = f"{deepgram.model_id()} ({deepgram.language_code(language)})"
    else:
        usd = seconds * USD_PER_AUDIO_SECOND
        model = script_breakdown.text_model_id(provider)
    return {
        "seconds": round(seconds, 1),
        "usd": round(usd, 4),
        "model": model,
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

    provider = resolve_provider(provider)

    # ⚠ THE DISPATCH IS HERE AND THE SHAPE IS THE SAME ON BOTH SIDES. Whichever
    # backend answers, what comes back is `[{start_ms, end_ms, text}, …]`
    # untidied, so `tidy_lines` and everything after it stays provider-blind.
    # A second tidying path per backend is how the two would drift apart.
    if provider == "deepgram":
        try:
            return deepgram.transcribe(data, mime_type=mime, language=language)
        except deepgram.DeepgramError as exc:
            # Re-raised as the error the route already knows how to show. The
            # message is Deepgram's own and already names the line to change.
            raise CaptionError(str(exc)) from exc

    client = script_breakdown.get_client(provider, key_env=ai_keys.key_env(CAPABILITY))
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
# Where the sound actually is — free, no model, no decoder of our own
# ---------------------------------------------------------------------------
def peak_envelope(path: str, *, timeout: float = 600.0) -> list[float]:
    """The track's loudness over time: one peak per `ENVELOPE_WINDOW_MS`, 0…1.

    ⚠ THIS IS THE WAVEFORM THE USER IS LOOKING AT. `beats.js::peaksOf` draws the
    timeline's waveform by bucketing the decoded samples and keeping the PEAK of
    each bucket; this measures the same quantity from the same audio, so a block
    of sound found here is a block of sound the user can see. Measuring something
    else — an RMS average, ffmpeg's fixed-threshold `silencedetect` — puts the
    caption boxes on edges that are not the ones the eye is checking.

    ffmpeg decodes to raw mono s16 on stdout; there is no container, no header
    and no decoder of our own, so this works on the `imageio-ffmpeg` install
    that has no ffprobe (see `video_assemble.py`). Returns `[]` on any failure —
    which means "don't align", not "no sound".
    """
    try:
        from animatic import ffmpeg_exe
    except Exception:  # noqa: BLE001 — no ffmpeg is a fallback, not an error
        return []
    try:
        proc = subprocess.run(
            [
                ffmpeg_exe(), "-hide_banner", "-nostats", "-loglevel", "error",
                "-i", path,
                "-vn",  # an MP3's cover art is a video stream; it has nothing to say
                "-ac", "1", "-ar", str(ENVELOPE_HZ),
                "-f", "s16le", "-",
            ],
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001 — every failure means "use the model's times"
        logger.warning("[captions] could not measure %s for alignment: %s", path, exc)
        return []
    if proc.returncode != 0 or not proc.stdout:
        logger.warning(
            "[captions] ffmpeg gave no samples for %s — captions will use the "
            "model's own times.", os.path.basename(path),
        )
        return []

    samples = array.array("h")
    # Whole frames only: a truncated final byte pair would be read as a sample.
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()  # `s16le` is little-endian whatever this machine is

    per = max(1, int(ENVELOPE_HZ * ENVELOPE_WINDOW_MS / 1000))
    # `max`/`min` over a slice run in C — a Python loop over every sample would
    # make a twenty-minute track take longer to measure than to transcribe.
    return [
        max(max(window), -min(window)) / 32768.0
        for window in (samples[i:i + per] for i in range(0, len(samples), per))
        if window
    ]


def spans_from_envelope(envelope: list[float], window_ms: int = ENVELOPE_WINDOW_MS) -> list[dict]:
    """A loudness envelope → the runs of SOUND in it. Pure, and where the edges
    of every caption box ultimately come from.

    ⚠ THE THRESHOLD IS DERIVED FROM THE TRACK, NOT FIXED. See
    `NOISE_FLOOR_MULTIPLE` / `SOUND_PEAK_SHARE`: a fixed dBFS line hears room
    tone as speech on one upload and misses a soft word on the next, and both
    mistakes land on screen as a caption box that does not sit on its wave.

    Then two clean-ups, in this order and for different reasons:

      1. **A gap shorter than `MIN_SILENCE_MS` is not a pause** — it is the stop
         inside a word — so the runs either side of it are ONE run. Without this
         every "t" and "k" would split a sentence into fragments and each
         fragment would try to hold a caption.
      2. **A run shorter than `MIN_SPEECH_MS` is not speech** — it is a click, a
         breath, a door. Dropped, so it cannot claim a line's worth of words.
    """
    if not envelope:
        return []
    loudest = max(envelope)
    if loudest <= 0:
        return []
    # The noise floor: the quietest tenth of the track. A track with real silence
    # in it puts this at ~0, and one with hiss puts it at the hiss — but on a
    # track with no pauses it lands in the middle of the speech, which is why the
    # result is capped. See `MAX_THRESHOLD_SHARE`.
    ordered = sorted(envelope)
    floor = ordered[max(0, int(len(ordered) * 0.1) - 1)]
    threshold = min(
        max(floor * NOISE_FLOOR_MULTIPLE, loudest * SOUND_PEAK_SHARE),
        loudest * MAX_THRESHOLD_SHARE,
    )

    runs: list[list[int]] = []
    for i, level in enumerate(envelope):
        if level < threshold:
            continue
        at = i * window_ms
        # Rule 1, applied as the runs are built: a quiet patch too short to be a
        # pause never becomes a boundary in the first place.
        if runs and at - runs[-1][1] <= MIN_SILENCE_MS:
            runs[-1][1] = at + window_ms
        else:
            runs.append([at, at + window_ms])
    return [
        {"start_ms": start, "end_ms": end}
        for start, end in runs
        if end - start >= MIN_SPEECH_MS  # rule 2
    ]


def speech_spans(path: str, total_ms: int, *, timeout: float = 600.0) -> list[dict]:
    """The stretches of `path` that have sound in them. FREE — no model, no quota.

    ⚠ THIS IS THE WAVEFORM, AS NUMBERS, and it is the whole reason the captions
    can be made to line up with it: the thing the user checks a caption against
    is the picture of the sound, so the answer has to be computed from the sound
    and not from a second opinion about it.

    `total_ms` is the file's length, which the CALLER knows and this does not
    measure, exactly as `estimate()` does — it is only used to trim a measurement
    that overruns it. Returns `[]` on any failure, and `[]` means "don't align"
    rather than "no speech" — see `align_lines`.
    """
    if total_ms <= 0:
        return []
    spans = spans_from_envelope(peak_envelope(path, timeout=timeout))
    spans = [
        {"start_ms": s["start_ms"], "end_ms": min(s["end_ms"], total_ms)}
        for s in spans
        if s["start_ms"] < total_ms
    ]
    spans = [s for s in spans if s["end_ms"] - s["start_ms"] >= MIN_SPEECH_MS]
    logger.info(
        "[captions] %d run(s) of sound in %s (%.0f%% of %.1fs)",
        len(spans), os.path.basename(path),
        100 * sum(s["end_ms"] - s["start_ms"] for s in spans) / max(1, total_ms),
        total_ms / 1000,
    )
    return spans


# ---------------------------------------------------------------------------
# The words onto the sound — pure, free, and the fix for "captions lag"
# ---------------------------------------------------------------------------
def align_lines(lines: list[dict], spans: list[dict], *, total_ms: int = 0) -> list[dict]:
    """Re-time a transcript against the sound that was MEASURED in the file.

    ⚠ THIS IS THE FUNCTION THAT MAKES CAPTIONS MATCH A VOICEOVER, and it exists
    because of one asymmetry in what the model gives us: **its words are
    excellent and its times are a guess.** It is a language model listening, not
    a forced aligner, and it returns plausible round numbers — good enough to
    look right in a list, and visibly wrong the moment they are drawn against a
    waveform. That is the reported bug in full: the caption text is right, and it
    appears after the sentence has already been spoken.

    So the two signals are used for what each is actually good at:

      * the MODEL says what is said, and in what ORDER;
      * `speech_spans` says WHEN there is sound, exactly.

    ⚠ THE LINES ARE DEALT INTO THE RUNS OF SOUND, AND EACH RUN IS THEN FILLED
    EXACTLY. This is the second attempt at this function and the difference is
    the whole point of it, so do not "simplify" it back. The first version
    shared the speaking time out globally and nudged the edges toward a run if
    one happened to be within a few hundred milliseconds — which still left a
    caption box starting in a SILENCE, before its own wave, whenever the nudge
    could not reach. That is a box that visibly begins with blank space, and it
    is the thing the eye catches first.

    So the mapping is structural instead of approximate:

      1. **Each line is dealt to a run** — the one holding the midpoint of its
         share of the speaking time. Shares are proportional to CHARACTER COUNT,
         the same measure `_split_line` and `_slice_words` use, because speech
         takes about as long as it is long. The dealing is monotone, so the
         words stay in the order they were said.
      2. **Each run is then filled by the lines dealt to it**, sharing that run's
         own duration by character count. The FIRST line of a run starts exactly
         where the sound starts and the LAST ends exactly where it stops.

    Which gives the two invariants the whole feature is judged by:

      * **No caption ever starts in a silence**, so no box begins with blank
        space and none is drawn before the wave it belongs to.
      * **A run of sound is covered from its first millisecond to its last**, so
        the row of caption boxes has the shape of the waveform under it.

    Silence is never shared out — it is not speaking time — so a pause between
    sentences pushes no caption late; it is simply where one box ends and the
    next has not started.

    ⚠ IT DECLINES TO GUESS. With no spans, too little sound (`MIN_SOUND_SHARE`)
    or nothing to say, the lines come back UNTOUCHED — the model's own times, the
    behaviour before this existed. A measurement that went wrong can therefore
    only ever leave the captions where they already were.

    In and out are both FILE time, so this belongs BEFORE `clip_lines`: the
    razor's arithmetic is unchanged and unaware of it.
    """
    usable = [
        {
            "start_ms": max(0, int(line.get("start_ms") or 0)),
            "end_ms": max(0, int(line.get("end_ms") or 0)),
            "text": str(line.get("text") or "").strip(),
        }
        for line in (lines or [])
    ]
    usable = [line for line in usable if line["text"]]
    if not usable:
        return []
    usable.sort(key=lambda line: (line["start_ms"], line["end_ms"]))

    runs = [
        (int(s.get("start_ms") or 0), int(s.get("end_ms") or 0))
        for s in (spans or [])
        if int(s.get("end_ms") or 0) > int(s.get("start_ms") or 0)
    ]
    runs.sort()
    speaking = sum(end - start for start, end in runs)
    # Nothing measured, or so little measured that believing it would squeeze the
    # whole script into a corner of the file. Either way: leave it alone.
    if not runs or speaking <= 0:
        return usable
    if total_ms > 0 and speaking < MIN_SOUND_SHARE * total_ms:
        logger.warning(
            "[captions] only %.0f%% of the track measured as sound — keeping the "
            "model's own times rather than aligning to a bad measurement.",
            100 * speaking / total_ms,
        )
        return usable

    weights = [max(1, len(line["text"])) for line in usable]
    spoken_chars = sum(weights)

    # --- 1. Deal each line to the run of sound its middle falls in ------------
    # `opens[k]` is where run k begins in SPEAKING time (silence removed), which
    # is the clock the character shares are measured on.
    opens: list[int] = []
    clock = 0
    for start, end in runs:
        opens.append(clock)
        clock += end - start

    dealt: list[list[int]] = [[] for _ in runs]
    at = 0
    for i, weight in enumerate(weights):
        middle = (at + weight / 2) / spoken_chars * speaking
        # Monotone in `i`, because `middle` is: the words keep their order.
        k = bisect.bisect_right(opens, middle) - 1
        dealt[min(max(k, 0), len(runs) - 1)].append(i)
        at += weight

    # --- 2. Fill each run with the lines dealt to it --------------------------
    out: list[dict | None] = [None] * len(usable)
    held: int | None = None   # the last line placed, and so the one still on screen
    waiting: int | None = None  # sound before ANY line was placed — see below
    for (run_from, run_to), members in zip(runs, dealt):
        if not members:
            # ⚠ SOUND WITH NO LINE OF ITS OWN, which happens whenever there are
            # fewer lines than runs — one long sentence read across two pauses,
            # or a run too short to win a midpoint. It must not be left bare:
            # bare sound is a stretch of voiceover with no caption at all.
            if held is not None:
                # Something is on screen — it HOLDS. A subtitle that stays up
                # over a pause is ordinary; one that starts before its own wave
                # is the bug being fixed here, so holding is the safe direction.
                out[held]["end_ms"] = max(out[held]["end_ms"], run_to)
            elif waiting is None:
                # Nothing has been placed yet, so there is nothing to hold it.
                # The first line to be placed reaches BACK to here instead —
                # still a start on sound, never one in the silence between.
                waiting = run_from
            continue
        span = run_to - run_from
        share_of = sum(weights[i] for i in members)
        # ⚠ The reach-back applies to the FIRST line's start ONLY, and the shares
        # are still measured across the run itself. Widening the run instead
        # would put the boundaries BETWEEN these lines inside the silence being
        # reached over — which is the very thing this function exists to stop.
        edge = run_from if waiting is None else waiting
        waiting = None
        for n, i in enumerate(members):
            # The LAST line of a run ends where the sound stops, exactly — never
            # at whatever the arithmetic rounded to.
            end = run_to if n == len(members) - 1 else int(round(
                run_from + span * sum(weights[m] for m in members[: n + 1]) / share_of
            ))
            out[i] = {
                "start_ms": int(edge),
                "end_ms": max(int(edge) + 1, end),
                "text": usable[i]["text"],
            }
            edge = out[i]["end_ms"]
        held = members[-1]

    placed = [line for line in out if line is not None]
    if total_ms > 0:
        for line in placed:
            line["start_ms"] = min(line["start_ms"], max(0, total_ms - 1))
            line["end_ms"] = max(line["start_ms"] + 1, min(line["end_ms"], total_ms))
    return placed


# ---------------------------------------------------------------------------
# Through the cuts — a transcript of a FILE onto a timeline of CLIPS
# ---------------------------------------------------------------------------
def clip_lines(lines: list[dict], windows: list[dict]) -> list[dict]:
    """Transcript lines (FILE time) → timeline lines, THROUGH THE RAZOR.

    ⚠ THIS IS THE FUNCTION THAT MAKES CAPTIONS MATCH A CUT TRACK, and it exists
    because the two sides speak different time. The model listened to the WHOLE
    FILE, so its times are "this many ms into the recording". The timeline holds
    CLIPS cut out of that file: each one plays a window of it (`offset_ms` for
    `play_ms`) at its own place on the timeline (`start_ms`). Take the head off a
    take, cut the pause out of the middle, trim the tail — and every one of those
    edits moves, or deletes, some of what was said.

    Without this, a captions run on a cut track wrote the transcript out end to
    end from one clip's offset: every caption after the first cut sat later than
    the words it belonged to, and the ones for audio the user had CUT OUT were
    written anyway, over the top of everything else.

    `windows` is one dict per clip cut from this file:

        start_ms   where the clip sits on the TIMELINE
        offset_ms  how far into the FILE it starts reading
        play_ms    how much of the file it plays from there

    so the clip is audible over file `[offset, offset + play)` and the shift from
    file time to timeline time is `start - offset`. Both halves of that matter:
    they pull in opposite directions and using one alone is the bug this
    replaced.

    THREE THINGS HAPPEN TO A LINE, and which one depends on where the cuts fell:

      1. **Inside one clip** — it comes through with its text intact, moved by
         that clip's shift. This is every line on an uncut track.
      2. **Cut in half** — the audible part comes through with the WORDS THAT
         WERE ACTUALLY SAID IN IT, shared out by character count the same way
         `_split_line` shares out time. The words in the removed part are gone,
         because the audio for them is gone.
      3. **Entirely inside a removed stretch** — dropped. Nothing said it.

    A line spanning a cut appears once per clip it survives in, which is right:
    the take was split, so the subtitle is too. Lines come back in timeline
    order; `tidy_lines` is still what makes them safe to draw.
    """
    out: list[dict] = []
    for window in windows or []:
        start = max(0, int(window.get("start_ms") or 0))
        offset = max(0, int(window.get("offset_ms") or 0))
        play = max(0, int(window.get("play_ms") or 0))
        if play <= 0:
            continue
        # The stretch of FILE this clip plays, and the shift onto the timeline.
        file_from, file_to = offset, offset + play
        shift = start - offset

        for line in lines or []:
            said_from = max(0, int(line.get("start_ms") or 0))
            said_to = max(said_from + 1, int(line.get("end_ms") or 0))
            heard_from = max(said_from, file_from)
            heard_to = min(said_to, file_to)
            if heard_to <= heard_from:
                continue  # said in audio that isn't on the timeline
            cut = heard_from > said_from or heard_to < said_to
            if cut and heard_to - heard_from < MIN_PIECE_MS:
                continue  # a sliver left behind by a cut, not a subtitle
            span = said_to - said_from
            text = (
                str(line.get("text") or "").strip()
                if not cut
                else _slice_words(
                    str(line.get("text") or ""),
                    (heard_from - said_from) / span,
                    (heard_to - said_from) / span,
                )
            )
            if not text:
                continue
            out.append({
                "start_ms": max(0, heard_from + shift),
                "end_ms": max(0, heard_to + shift),
                "text": text,
            })
    out.sort(key=lambda line: (line["start_ms"], line["end_ms"]))
    return out


def _slice_words(text: str, frac_a: float, frac_b: float) -> str:
    """The words of `text` lying between two fractions of the way through it.

    Weighted by CHARACTER COUNT, not by word count, and for the same reason
    `_split_line` shares out time that way: speech takes about as long as it is
    long, so the half-way point through a sentence's duration is the half-way
    point through its characters, not through its word list.

    A word is kept when its MIDDLE falls inside the span, so a word straddling
    the cut goes to whichever side has more of it — never to both, and never to
    neither. If nothing lands inside (a span narrower than one word) the nearest
    word is kept: the caller has already decided this piece is long enough to be
    worth a caption, so returning nothing here would silently drop it.
    """
    words = (text or "").split()
    if not words:
        return ""
    # Characters plus the single space between words — the same measure the
    # fractions were computed from.
    total = sum(len(w) for w in words) + max(0, len(words) - 1)
    if total <= 0:
        return ""
    centres = []
    at = 0
    for word in words:
        centres.append((at + len(word) / 2) / total)
        at += len(word) + 1

    kept = [w for w, centre in zip(words, centres) if frac_a <= centre <= frac_b]
    if kept:
        return " ".join(kept)
    middle = (frac_a + frac_b) / 2
    nearest = min(range(len(words)), key=lambda i: abs(centres[i] - middle))
    return words[nearest]


# ---------------------------------------------------------------------------
# The timing rules — pure, free, and where the bugs live
# ---------------------------------------------------------------------------
def tidy_lines(
    lines: list[dict],
    *,
    total_ms: int | None = None,
    offset_ms: int = 0,
    max_chars: int = MAX_CHARS,
    max_words: int = MAX_WORDS,
) -> list[dict]:
    """Make a transcript SAFE TO DRAW: in order, non-overlapping, readable.

    Four rules, in this order, and the order matters:

      1. **Split first.** A 30-word line is split into readable ones and its
         time shared out by character count, so the pieces are timed before
         anything else reasons about their neighbours. ⚠ Split under BOTH caps —
         `max_words` is the one a reader feels and `max_chars` is the backstop;
         see `MAX_WORDS` for why the character cap alone was not enough.
      2. **Order, and never overlap — BY SHORTENING THE LINE IN FRONT.** Two
         captions colliding are separated by pulling the EARLIER one's end back,
         not by pushing the later one's start forward. ⚠ Which way round this
         goes is the difference between a caption that arrives with its word and
         one that arrives after it: a start is WHEN THE WORD IS SAID and is the
         only number here that is evidence, while an end is merely how long the
         line has been left up. Pushing starts (what this did) delayed every
         caption after the first by `GAP_MS`, permanently and for nothing. The
         earlier line is only shortened as far as `MIN_HOLD_MS`; a line that
         would go below that keeps its length and the later one moves after all,
         because a subtitle that blinks is worse than one that is late.
         Overlapping subtitles are the single most common failure of an
         auto-caption pass, and they look like a bug in the editor rather than
         in the transcript.
      3. **Long enough to read**, but never at the cost of rule 2: a line is
         extended toward MIN_LINE_MS only into the gap that is actually there.
      4. **Inside the video.** A line past the end is cut, and one that ends up
         with no room at all is dropped — a zero-length caption is a flash.

    `offset_ms` shifts every line by a fixed amount — the whole-file case of
    "transcript times are relative to the FILE, clip times to the TIMELINE". ⚠ It
    is NOT what the captions pass uses any more: a track can be cut into several
    clips, each with its own shift, and no single number covers that. `clip_lines`
    does it per clip and hands the result here already absolute (so `offset_ms`
    stays 0). This parameter is kept for a caller that really does have one
    uncut window and its own shift already worked out.
    """
    out: list[dict] = []
    for line in sorted(lines, key=lambda l: (l.get("start_ms") or 0, l.get("end_ms") or 0)):
        out.extend(_split_line(line, max_chars, max_words))

    tidied: list[dict] = []
    for line in out:
        start = max(0, int(line["start_ms"]) + offset_ms)
        end = max(start + 1, int(line["end_ms"]) + offset_ms)
        if tidied and start < tidied[-1]["end_ms"] + GAP_MS:
            previous = tidied[-1]
            # Rule 2, and read its note above before swapping these two branches.
            # FIRST TRY: take the room out of the line already on screen, so this
            # one keeps the start it was measured at.
            trimmed = start - GAP_MS
            if trimmed - previous["start_ms"] >= MIN_HOLD_MS:
                previous["end_ms"] = trimmed
            else:
                # It has nothing left to give — only now is this line delayed.
                start = previous["end_ms"] + GAP_MS
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
        # ⚠ AND A SPLIT PIECE IS ALLOWED TO OUTLIVE ITS OWN SENTENCE, by up to
        # the readability floor and no more. Since the word cap the LAST piece of
        # a line is often under `MIN_LINE_MS` — "up on time." is half a second —
        # and capping it at the moment the sentence stopped being spoken buys
        # nothing and costs a subtitle nobody can read. The overrun is bounded by
        # the rule above (`start + MIN_LINE_MS`, never the whole gap in front),
        # so a line followed by thirty seconds of silence still leaves when it
        # has been read rather than sitting there.
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


def _pack_chars(text: str, max_chars: int) -> list[str]:
    """Greedy word packing to a character ceiling. The backstop cap."""
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
    return pieces


def _pack_words(words: list[str], max_words: int) -> list[list[str]]:
    """Split a line into BALANCED runs of at most `max_words` words.

    ⚠ BALANCED, NOT GREEDY, and that is the whole of it. Packing greedily to the
    ceiling leaves the remainder as the last piece — sixteen words at a cap of
    five is 5, 5, 5, 1, and that last caption is one word flashing on screen for
    a fifth of a second. Deciding HOW MANY pieces first and then sharing the
    words between them gives 4, 4, 4, 4, and no piece is ever an orphan.

    ⚠ AND IT BREAKS AT A CLAUSE WHERE ONE IS GOING SPARE. If a word one either
    side of the balanced target ends a clause, the piece ends there instead — so
    "We won't just be kings," closes a caption rather than trailing "kings," onto
    the front of the next one. The order of the search is the target, then one
    word LONG, then one word short: a comma most often falls just past an even
    share, and refusing to reach for it was what left the first caption reading
    "We won't just be". Growing is still capped at `max_words`, and neither
    direction is taken if it would leave a one-word tail behind it — tidying a
    break must not manufacture the orphan the balancing just avoided.
    """
    if not words:
        return []
    count = -(-len(words) // max_words)          # ceil: how many pieces are needed
    per = -(-len(words) // count)                # ...and an even share between them
    out: list[list[str]] = []
    at = 0
    while at < len(words):
        left_now = len(words) - at
        take = min(per, left_now)
        if take > 1 and at + take < len(words):
            for end in (take, take + 1, take - 1):
                if end < 1 or end > min(left_now, max_words):
                    continue
                left = len(words) - (at + end)
                if left and left < 2:
                    continue
                if words[at + end - 1].endswith(_CLAUSE_END):
                    take = end
                    break
        out.append(words[at:at + take])
        at += take
    return out


def _split_line(line: dict, max_chars: int, max_words: int = MAX_WORDS) -> list[dict]:
    """One long line into several readable ones, sharing out its time.

    Split at word boundaries under two caps — `max_words` first (the one a
    reader actually feels; see its note) and `max_chars` as the backstop for a
    run of very long words — and the time each piece gets is PROPORTIONAL TO ITS
    CHARACTER COUNT rather than an equal share, because speech takes about as
    long as it is long and an equal share puts a two-word piece on screen for as
    long as a twelve-word one.
    """
    text = (line.get("text") or "").strip()
    start = int(line.get("start_ms") or 0)
    end = max(start + 1, int(line.get("end_ms") or 0))
    words = text.split()
    if len(text) <= max_chars and not (max_words and len(words) > max_words):
        return [{"start_ms": start, "end_ms": end, "text": text}]

    runs = _pack_words(words, max_words) if max_words else [words]
    pieces: list[str] = []
    for run in runs:
        # The char cap applies INSIDE a run, so five very long words still fit.
        pieces.extend(_pack_chars(" ".join(run), max_chars))
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

    `layer_id` is which LANE they land on, and every generated caption belongs on
    `CAPTION_LAYER_ID` — read that constant's comment for why they get a lane of
    their own rather than being piled onto the text you typed.
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
            # ⚠ PER LINE, NOT PER RUN, AND THE MOST IMPORTANT FIELD HERE.
            # Subtitles are transcribed from the VOICEOVER, so they are in
            # whatever language the film was spoken in — and this used to hand
            # every one of them to Inter, which has no Devanagari, no Gurmukhi,
            # no Arabic and no Han in it. A Hindi film's subtitles came out as a
            # row of empty boxes, burnt into the MP4, with nobody having chosen
            # a font at any point. `best_font_for_text` keeps `base["font"]`
            # whenever it fits, so an English film and a style someone set by
            # hand are both left exactly as they were.
            "font": animatic_fonts.best_font_for_text(
                str(line.get("text") or ""), str(base.get("font") or "")
            ),
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
