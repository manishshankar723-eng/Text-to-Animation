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

---------------------------------------------------------------------------
⚠ THREE BACKENDS CAN READ A LINE NOW, AND THE FILM'S LANGUAGE PICKS ONE.
---------------------------------------------------------------------------
    gemini / vertex   Google's TTS. Speaks anything, has real CHILD voices, and
                      is the only one that takes a STAGE DIRECTION (see
                      `prompt_for`). The default, and the fallback for a film in
                      a language the other two do not read.
    sarvam            Bulbul — eleven Indian languages, billed in rupees, and
                      the only one that reads HINGLISH (Hindi in Latin script)
                      as one sentence instead of as bad English. `sarvam.py`.
    deepgram          Aura-2 — English, Spanish, German, French, Dutch, Italian
                      and Japanese, each with ITS OWN voices, and chosen for the
                      $200 of free credit shared with the captions. No Indian
                      language at all. `deepgram.py`.

⚠ AND NONE OF THE THREE IS COMPLETE, SO THE GAPS ARE PRINTED RATHER THAN HIDDEN.
Google is the only one with real CHILD voices and the only one that can be told
an age; Sarvam publishes no ages at all; Aura's youngest tier is "young adult"
and some of its languages have two voices in total. `persona_note()` is the
sentence that says which promise this backend cannot keep for this part, and the
🎙 dialog prints it beside the line — free, before the button that spends.

⚠ WHAT MAKES THAT SAFE IS THAT EVERY BACKEND ANSWERS IN THE SAME BYTES. Each one
is asked for signed 16-bit PCM, mono, at `SAMPLE_RATE`, and each REFUSES anything
else rather than handing back audio at another rate — because every duration,
every caption and every stretched shot below is byte arithmetic over that one
assumed format. `_assert_house_format` checks the three modules agree at import.

⚠ AND TWO THINGS ARE PROVIDER-SHAPED, SO NOTHING ELSE HAS TO BE:

  · THE CAST. "Kore" is a Google voice, "ishita" is a Sarvam speaker, and
    neither exists for the other. `PERSONAS` — "grandfather", "girl" — is the
    provider-blind casting layer the browser and the stored dialogue sheet
    speak, and `cast()` / `personas()` answer for whichever backend is switched
    on. A voice saved under one provider and run under another is TRANSLATED
    through its persona rather than failing (`voice_for`).
  · THE PROMPT. Google is *told* "read this as an elderly man" and obeys; the
    other two would READ THAT SENTENCE ALOUD. So `prompt_for` is provider-aware,
    and because the estimate counts exactly what the run sends, so is `estimate`.
"""

from __future__ import annotations

import io
import logging
import os
import re
import wave

from google.genai import types

import ai_keys
import deepgram
import sarvam
import script_breakdown

logger = logging.getLogger(__name__)

# --- Whose bill this lands on -----------------------------------------------
# ⚠ THE VOICEOVER IS ITS OWN CAPABILITY, AND IT IS THE ONE MOST LIKELY TO LEAVE
# GOOGLE. It is billed per CHARACTER, and which backend reads a line best is a
# question about the FILM'S LANGUAGE rather than about this app: an Indic-language
# board is served by a backend trained on Indic speech, and an English one by
# whichever is cheapest that week. So the switch has to be a line in `.env`.
#
# `VOICE_PROVIDER` and `GEMINI_KEY_VOICE`, falling back to `TEXT_PROVIDER` so a
# deployment that has never heard of either keeps working. See `ai_keys`.
CAPABILITY = "voice"

# ⚠ `sarvam` AND `deepgram` ARE HERE AND NOT IN THE TEXT LIST, the same asymmetry
# `captions.SUPPORTED_PROVIDERS` carries and for the same reason: most of this app
# is "which Google backend", and this capability can leave Google entirely because
# reading a line aloud is a commodity with real competition — and because the
# thing that decides WHICH backend is right is the FILM'S LANGUAGE, not anything
# about this app. `vertex`/`gemini` resolve through `script_breakdown`; the other
# two go to their own vendor module and never touch a genai client.
SUPPORTED_PROVIDERS = ("vertex", "gemini", "sarvam", "deepgram")

# The backends that are NOT Google, by name. Everything that has to ask "is this
# a genai call or an HTTP call" asks this rather than listing names again.
VENDORS = {"sarvam": sarvam, "deepgram": deepgram}


def resolve_provider(provider: str | None = None) -> str:
    """The backend that will speak: explicit > VOICE_* > TEXT_PROVIDER > vertex.

    ⚠ NEITHER `SARVAM_API_KEY` NOR `DEEPGRAM_API_KEY` IS A SWITCH — say
    `VOICE_PROVIDER=sarvam`. A vendor-named key says who is paid but not what
    for, and both of those vendors sell transcription as well, so a key that
    moved a capability on its own would move the CAPTIONS too. Same rule
    `captions.resolve_provider` states from the other side.
    """
    p = ai_keys.resolve_provider(CAPABILITY, provider, fallback=("TEXT_PROVIDER",))
    if p not in SUPPORTED_PROVIDERS:
        raise VoiceoverError(
            f"Unknown VOICE_PROVIDER '{p}'. Use one of {SUPPORTED_PROVIDERS}."
        )
    return p


# What the TTS models return: signed 16-bit little-endian PCM, mono, 24kHz.
# Not negotiable and not detected — it is the documented output format, and the
# duration arithmetic below depends on it being exactly this.
SAMPLE_RATE = 24_000
SAMPLE_WIDTH = 2
CHANNELS = 1


def _assert_house_format() -> None:
    """⚠ THE THREE BACKENDS MUST AGREE ON THE BYTES, AND THIS IS WHERE WE FIND OUT.

    Every duration in this module is `bytes ÷ (rate × width × channels)`, so a
    vendor client that asked its API for 22,050 Hz would make every caption and
    every stretched shot wrong by 9% with nothing on screen to say so. Each
    client refuses audio it did not ask for; this checks that what they ask for
    is what this module measures. At import, because a mismatch is a deployment
    mistake and not a runtime one.
    """
    house = (SAMPLE_RATE, SAMPLE_WIDTH, CHANNELS)
    theirs = {
        "sarvam": (sarvam.SAMPLE_RATE, sarvam.SAMPLE_WIDTH, sarvam.CHANNELS),
        "deepgram": (
            deepgram.TTS_SAMPLE_RATE, deepgram.TTS_SAMPLE_WIDTH, deepgram.TTS_CHANNELS,
        ),
    }
    for name, shape in theirs.items():
        if shape != house:
            raise RuntimeError(
                f"{name} speaks {shape} but tts.py measures {house}. Every "
                "duration, caption and shot length here is byte arithmetic over "
                "one format — fix the vendor module, do not relax this."
            )


_assert_house_format()

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
# here: we quote, only the backend bills, and the UI says so.
USD_PER_1K_CHARS = float(os.environ.get("API_VOICEOVER_USD_PER_1K", "0.012"))


def usd_per_1k_chars(provider: str | None = None) -> float:
    """The advisory rate THIS backend charges, USD per 1,000 characters.

    ⚠ THE QUOTE HAS TO FOLLOW THE SWITCH. Sarvam is about three times Gemini's
    list rate and Deepgram about two and a half — a price built from one
    constant would understate the run by that much the moment `.env` moved, and
    an advisory number is only worth showing while it describes the button
    beside it.
    """
    vendor = VENDORS.get(resolve_provider(provider))
    if vendor is sarvam:
        return sarvam.usd_per_1k_chars()
    if vendor is deepgram:
        return deepgram.tts_usd_per_1k_chars()
    return USD_PER_1K_CHARS


def biller(provider: str | None = None) -> str:
    """WHO ACTUALLY SENDS THE BILL, for the sentence under the price.

    ⚠ THE CONFIRM DIALOG USED TO SAY "GOOGLE BILLS THE ACTUAL AMOUNT" WHATEVER
    WAS SWITCHED ON. It is a small lie that gets expensive: somebody watching a
    Google invoice for a run Sarvam charged for concludes the estimate is
    fiction. The name comes down with the estimate now.
    """
    return {"sarvam": "Sarvam", "deepgram": "Deepgram"}.get(
        resolve_provider(provider), "Google"
    )

# Silence left between two spoken lines when they would otherwise butt up
# against each other. Speech with no gap between lines sounds like one run-on
# sentence; this is the shortest pause that still reads as a new line.
GAP_MS = 220


class VoiceoverError(Exception):
    """Raised when dialogue can't be turned into a voiceover.

    Carries a human-readable reason so the API can say what went wrong.
    """


def tts_model_id(provider: str | None = None, language: str = "") -> str:
    """WHAT WILL ACTUALLY READ THE LINE, as a label for the estimate.

    ⚠ THE LANGUAGE IS PART OF THE NAME FOR SARVAM, and deliberately: it is the
    setting most likely to be wrong and least likely to be noticed, so the
    confirm dialog says "bulbul:v3 (hi-IN)" and a Tamil film quoted as `hi-IN`
    is visible BEFORE the money is spent. Same argument as
    `captions.estimate`'s "nova-3 (multi)".
    """
    picked = resolve_provider(provider)
    if picked == "sarvam":
        return f"{sarvam.model_id()} ({sarvam.language_code(language) or 'unsupported'})"
    if picked == "deepgram":
        return deepgram.tts_model_id(language)
    return os.environ.get("GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)


# ---------------------------------------------------------------------------
# The cast, per backend — one list, wherever it is asked from
# ---------------------------------------------------------------------------
def cast(provider: str | None = None, language: str = "") -> tuple[dict, ...]:
    """The voices this backend offers: `[{name, tone, persona}, …]`.

    ⚠ THE BROWSER'S PICKER IS FILLED FROM HERE (through `GET /dialogue`), so it
    cannot offer a name the run would be refused for. That was already the rule
    when there was one backend — "the voice list used to be six names typed into
    the JSX" — and a second backend is exactly the situation it was written for.

    ⚠ AND THE LANGUAGE IS PART OF THE QUESTION ON DEEPGRAM, because there the
    voice NAME carries the language (`aura-2-thalia-en` vs `aura-2-sirio-es`)
    and there is no language parameter at all. A picker that offered the English
    cast for a German film would be offering eleven voices that read German
    words with English phonetics — and be charged for it.
    """
    picked = resolve_provider(provider)
    if picked == "sarvam":
        return sarvam.cast()
    if picked == "deepgram":
        return deepgram.tts_cast(language)
    return CAST


def default_voice(provider: str | None = None, language: str = "") -> str:
    """Who reads a line nothing has cast, on this backend, in this language."""
    picked = resolve_provider(provider)
    if picked == "sarvam":
        return sarvam.default_voice()
    if picked == "deepgram":
        return deepgram.tts_default_voice(language)
    return DEFAULT_VOICE


def personas(provider: str | None = None, language: str = "") -> dict[str, dict]:
    """`PERSONAS`, cast for this backend: key → {label, voice, direction, note}.

    ⚠ THE KEYS AND LABELS NEVER CHANGE AND THE VOICE ALWAYS DOES. A stored
    dialogue sheet holds `persona: "grandfather"`, and that has to keep meaning
    the same thing whichever backend is switched on tomorrow — the persona is
    the provider-blind layer, and the voice is the provider's answer to it.

    ⚠ AND `direction` IS EMPTY FOR EVERY BACKEND BUT GOOGLE, because it is not
    sent to them. The dialog prints it as "Read as an elderly man" — the one
    visible sign that an age and a sex reached the model — and printing it over
    a backend that never receives it would be a caption for something that did
    not happen. On Sarvam and Deepgram the age is carried by the CASTING (and,
    on Sarvam, by pace), which is what the voice column already shows.
    """
    picked = resolve_provider(provider)
    if picked not in VENDORS:
        return {key: {**entry, "note": ""} for key, entry in PERSONAS.items()}
    return {
        key: {
            "label": entry["label"],
            "voice": voice_for_persona(key, picked, language),
            "direction": "",
            # ⚠ WHAT THIS BACKEND CANNOT ACTUALLY DELIVER FOR THAT PART. "" when
            # it can. Neither Aura nor Bulbul publishes a child voice, and
            # several of Aura's languages have two or three voices in total, so
            # a persona often lands on the nearest thing — and the dialog says
            # so, beside the line, before the money. See `persona_note`.
            "note": persona_note(key, picked, language),
        }
        for key, entry in PERSONAS.items()
    }


def persona_note(
    persona: str | None, provider: str | None = None, language: str = ""
) -> str:
    """⚠ THE PROMISE THIS BACKEND CANNOT KEEP FOR THAT PART. "" when it can.

    Google is the only one of the three with real child voices, and the only one
    that can be TOLD an age; Sarvam publishes no ages at all and Deepgram's
    youngest tier is "young adult". So a "child" line on either of those is
    read by the nearest adult, and this is the sentence that says so — printed
    in the 🎙 dialog beside the line, where it is still free to change the voice,
    set `SARVAM_CAST`, or switch to `VOICE_PROVIDER=gemini`.

    ⚠ IT IS NOT AN ERROR AND MUST NOT BECOME ONE. The run is perfectly valid;
    the user simply deserves to know what they are buying before they buy it.
    """
    picked = resolve_provider(provider)
    key = resolve_persona(persona)
    if picked == "sarvam":
        return sarvam.persona_note(key)
    if picked == "deepgram":
        return deepgram.tts_persona_note(key, language)
    return ""


def resolve_voice(
    voice: str | None, provider: str | None = None, language: str = ""
) -> str:
    """A voice name THIS backend has FOR THIS FILM, or its default.

    Unknown names fold down rather than failing: a request that would only ever
    produce a PAID error is refused before it is sent, and a voice name is not
    worth losing a run over. ⚠ ON DEEPGRAM A VOICE FROM ANOTHER LANGUAGE COUNTS
    AS UNKNOWN — `aura-2-thalia-en` on a Japanese board is exactly the mistake
    that must not reach the API.
    """
    picked = resolve_provider(provider)
    if picked == "sarvam":
        return sarvam.resolve_voice(voice)
    if picked == "deepgram":
        return deepgram.tts_resolve_voice(voice, language)
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


def voice_for_persona(
    persona: str | None, provider: str | None = None, language: str = ""
) -> str:
    """The voice this project casts for that kind of speaker, on this backend."""
    picked = resolve_provider(provider)
    key = resolve_persona(persona)
    if picked == "sarvam":
        return sarvam.voice_for_persona(key)
    if picked == "deepgram":
        return deepgram.tts_voice_for_persona(key, language)
    return PERSONAS[key]["voice"]


def direction_for(persona: str | None, provider: str | None = None) -> str:
    """How the model is TOLD to read for that kind of speaker. "" = plainly.

    ⚠ ALWAYS "" OFF GOOGLE. The other backends take no instruction — they would
    read one out loud — so there is nothing to tell them and nothing to charge
    for. See `prompt_for`, which is the only caller that matters.
    """
    if resolve_provider(provider) in VENDORS:
        return ""
    return PERSONAS[resolve_persona(persona)]["direction"]


def _persona_of_voice(name: str) -> str:
    """WHOSE PART THIS VOICE WAS CAST FOR, on whichever backend owns the name.

    ⚠ THIS IS THE TRANSLATION BETWEEN BACKENDS, and it exists because a dialogue
    sheet is SAVED. A board voiced last week says `voice: "Kore"`; the same board
    read today with `VOICE_PROVIDER=sarvam` must not fail, must not silently drop
    the casting the user did, and must not send "Kore" to an API that has never
    heard of it. So a name that is not this backend's is looked up in the others,
    and the PERSONA it stood for — "woman" — is cast again here.
    """
    key = (name or "").strip().lower()
    if not key:
        return ""
    tables = [CAST, sarvam.cast("bulbul:v3"), sarvam.cast("bulbul:v2")]
    # ⚠ EVERY LANGUAGE OF AURA'S, not just this film's: the point of this lookup
    # is a name that does NOT belong to the run's own cast, and an English voice
    # on a German board is precisely that case.
    tables += [deepgram.tts_cast(code) for code in deepgram.tts_languages()]
    for table in tables:
        for entry in table:
            if str(entry["name"]).lower() == key:
                return entry["persona"]
    return ""


def voice_for(
    line: dict,
    default: str | None = None,
    provider: str | None = None,
    language: str = "",
) -> str:
    """WHICH VOICE READS THIS LINE, in the one order that can't surprise anyone.

    The line's own voice wins (the user picked it in the dialogue sheet), then
    the persona's casting, then the run's default. ⚠ THE PERSONA MUST NOT MASK
    THE RUN DEFAULT when there is no persona: `voice_for_persona("")` answers the
    backend's own default, so asking it unconditionally would quietly re-cast
    every unattributed line however the picker at the top of the dialog was set.

    ⚠ AND A VOICE FROM ANOTHER BACKEND IS TRANSLATED, NOT DROPPED — see
    `_persona_of_voice`. Folding it straight to the default would throw away a
    casting decision the user made and paid attention to, which is worse than
    the nearest equivalent voice.
    """
    picked = resolve_provider(provider)
    named = str((line or {}).get("voice") or "").strip()
    if named:
        if named.lower() in {v.lower() for v in _names(picked, language)}:
            return resolve_voice(named, picked, language)
        borrowed = _persona_of_voice(named)
        if borrowed:
            return voice_for_persona(borrowed, picked, language)
    persona = resolve_persona((line or {}).get("persona"))
    if persona:
        return voice_for_persona(persona, picked, language)
    # The run's default gets the same treatment — it can be a foreign name too,
    # because it comes from the same saved request the lines do. One level only:
    # the recursive call carries no default of its own.
    if str(default or "").strip():
        return voice_for({"voice": default}, provider=picked, language=language)
    return default_voice(picked, language)


def _names(provider: str | None = None, language: str = "") -> tuple[str, ...]:
    """Every voice name this backend answers to for this film."""
    return tuple(entry["name"] for entry in cast(provider, language))


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


def prompt_for(line: dict, provider: str | None = None) -> str:
    """EXACTLY what one line sends to the backend — its direction and its words.

    ⚠ THE DIRECTION IS PART OF THE PROMPT, SO IT IS PART OF THE PRICE. `estimate`
    counts this string rather than the bare line, which is what keeps the number
    on the confirm dialog the price of the thing the button then does — the rule
    the whole of this module is built around.

    The shape (direction, colon, the line in quotes) is the one Google documents
    for steering this model, and the quotes are what keep the direction OUT of
    the audio: it reads what is quoted and treats the rest as instruction.

    ⚠ AND OFF GOOGLE THERE IS NO DIRECTION AT ALL — the bare line goes. Sarvam
    and Deepgram take no instruction, they take TEXT, so a stage direction sent
    to either is a sentence the film's narrator says out loud ("Read this line as
    an elderly man") in a paid run. `direction_for` answers "" for them, which
    makes this one function right for all three; the alternative is a second
    prompt builder per backend, and then a second thing for `estimate` to get
    wrong.
    """
    text = str((line or {}).get("text") or "").strip()
    if not text:
        return ""
    direction = direction_for((line or {}).get("persona"), provider)
    if not direction:
        return text
    return f'Read this line as {direction}:\n"{text}"'


# ---------------------------------------------------------------------------
# Can this run happen at all? — FREE, and asked BEFORE the button spends
# ---------------------------------------------------------------------------
def preflight(*, provider: str | None = None, language: str = "") -> None:
    """Raise `VoiceoverError` if this backend cannot read this film. Spends nothing.

    ⚠ THE POINT IS TO FAIL BEFORE THE MONEY, NOT DURING IT. A voiceover is one
    call PER LINE, so "Aura does not speak Hindi" discovered on line 1 of 40 is
    a run that has already moved shots and written a track; discovered here it
    is a sentence in the dialog naming the `.env` line to change. Every message
    this can raise names that line — see each vendor's own hint.

    Called by the route before the job is queued, and again inside `speak` for
    anything that reaches it another way.
    """
    picked = resolve_provider(provider)
    if picked == "sarvam":
        if not sarvam.configured():
            raise VoiceoverError(sarvam.missing_key_hint())
        if not sarvam.speaks(language):
            raise VoiceoverError(
                f"Sarvam's Bulbul does not speak {language.strip() or 'that language'}. "
                "It reads Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, "
                "Odia, Punjabi, Tamil, Telugu and Indian English. Set "
                "VOICE_PROVIDER=gemini in your .env and restart to read this "
                "film on Gemini instead."
            )
        return
    if picked == "deepgram":
        if not deepgram.configured():
            raise VoiceoverError(deepgram.tts_missing_key_hint())
        if not deepgram.tts_speaks(language):
            raise VoiceoverError(
                f"Deepgram's Aura voices do not speak {language.strip()}. They "
                "read English, Spanish, German, French, Dutch, Italian and "
                "Japanese. For an Indian language set VOICE_PROVIDER=sarvam in "
                "your .env, or VOICE_PROVIDER=gemini for anything else, and "
                "restart."
            )
        return
    if picked == "gemini" and not ai_keys.gemini_key(CAPABILITY)[0]:
        raise VoiceoverError(ai_keys.missing_key_hint(CAPABILITY))


# ---------------------------------------------------------------------------
# The estimate — FREE, and shown before anything is spent
# ---------------------------------------------------------------------------
def estimate(
    lines: list[dict], *, provider: str | None = None, language: str = ""
) -> dict:
    """What reading these lines aloud should cost. Advisory; spends nothing.

    ⚠ COUNTS THE PROMPTS, NOT THE LINES — `prompt_for` is what the run sends, and
    a line with a persona sends its stage direction too. Counting the bare line
    would quote less than the run costs, which is the one direction an advisory
    price must never be wrong in.

    ⚠ AND IT COUNTS THEM FOR THE BACKEND THAT WILL ANSWER. The same sheet is a
    different number of characters on Google (which is sent the directions) than
    on Sarvam (which is not), at a rate three times different — so provider and
    language travel with the quote, exactly as they do with the run.
    """
    picked = resolve_provider(provider)
    prompts = [prompt_for(line, picked) for line in (lines or [])]
    prompts = [p for p in prompts if p]
    characters = sum(len(p) for p in prompts)
    return {
        "lines": len(prompts),
        "characters": characters,
        "usd": round(characters / 1000.0 * usd_per_1k_chars(picked), 4),
        "model": tts_model_id(picked, language),
        "provider": picked,
        "biller": biller(picked),
        "over_limit": characters > MAX_CHARACTERS,
        "limit_characters": MAX_CHARACTERS,
    }


# ---------------------------------------------------------------------------
# The model call — the half that costs money
# ---------------------------------------------------------------------------
def speak(
    text: str,
    *,
    voice: str | None = None,
    provider: str | None = None,
    language: str = "",
    persona: str | None = None,
) -> bytes:
    """SPENDS QUOTA. Read one PROMPT aloud. Returns raw PCM (see the constants).

    ⚠ A PROMPT, NOT A LINE: `prompt_for` may have wrapped the words in a stage
    direction, and this is handed the finished thing. Building it here instead
    would put the price and the payload in two different functions, which is the
    one way the estimate can drift from the run.

    Raw PCM rather than a container, because the caller is about to lay several
    of these end to end at known offsets — and concatenating containers means
    decoding them again, with a decoder this install does not have.

    ⚠ THE DISPATCH IS HERE AND THE BYTES ARE THE SAME ON EVERY BRANCH — the same
    shape `captions.transcribe` uses for the same reason. Whichever backend
    answers, what comes back is PCM in the house format, so `speak_lines`,
    `lay_track`, the shot fitting and the captions never learn there is more than
    one. A second timing path per backend is how the two would drift apart.

    `persona` is read only where it changes DELIVERY rather than casting (Sarvam's
    pace). The voice itself is already settled by `voice_for` before we get here.
    """
    line = (text or "").strip()
    if not line:
        raise VoiceoverError("There is nothing to read aloud.")

    picked = resolve_provider(provider)
    if picked in VENDORS:
        preflight(provider=picked, language=language)
        try:
            if picked == "sarvam":
                return sarvam.speak(
                    line,
                    voice=resolve_voice(voice, picked),
                    persona=resolve_persona(persona),
                    language=language,
                )
            return deepgram.speak(
                line, voice=resolve_voice(voice, picked, language), language=language
            )
        except (sarvam.SarvamError, deepgram.DeepgramError) as exc:
            # Re-raised as the error the route already knows how to show. The
            # message is the vendor's own and already names the line to change.
            raise VoiceoverError(str(exc)) from exc

    client = script_breakdown.get_client(
        picked, key_env=ai_keys.key_env(CAPABILITY)
    )
    model_id = tts_model_id(picked)
    name = resolve_voice(voice, picked)
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
def _ms_of_bytes(size: int) -> int:
    """How long `size` bytes of this PCM format lasts.

    Split out of `pcm_duration_ms` so a caller holding a growing buffer can ask
    without copying it — `lay_track` measures its own track on every piece, and
    `bytes(bytearray)` on a few minutes of speech is megabytes of copy per line.
    """
    frames = max(0, size) // (SAMPLE_WIDTH * CHANNELS)
    return int(round(frames * 1000 / SAMPLE_RATE))


def pcm_duration_ms(pcm: bytes) -> int:
    """How long this PCM lasts. Exact, from the byte count alone."""
    return _ms_of_bytes(len(pcm))


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
    language: str = "",
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
    picked = resolve_provider(provider)
    track = bytearray()
    spans: list[dict] = []
    for line in lines or []:
        prompt = prompt_for(line, picked)
        if not prompt:
            continue
        if progress_cb:
            progress_cb(line)
        if track:
            track += silence(GAP_MS)
        at = pcm_duration_ms(bytes(track))
        pcm = speak(
            prompt,
            voice=voice_for(line, voice, picked, language),
            provider=picked,
            language=language,
            persona=(line or {}).get("persona"),
        )
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


def lay_track(pieces: list[tuple[int, bytes]]) -> tuple[bytes, list[dict]]:
    """Lay each blob of speech at its own moment on one track, as a WAV, AND say
    where each one landed.

    Returns `(wav, windows)` where each window is `{"start_ms", "duration_ms"}`
    — the stretch of the finished file that piece occupies, silence excluded.

    `pieces` is `[(start_ms, pcm), …]` — where the caller decided each shot's
    speech goes. Padding is silence, and the arithmetic is exact because the
    byte count IS the duration (see the module header).

    ⚠ THE WINDOWS COME OUT OF THE SAME WALK THAT WRITES THE BYTES, and that is
    the whole reason this function exists rather than a second one that works
    the placement out again. The caller lays ONE CLIP PER WINDOW on the
    timeline, so a window that disagreed with the audio by a single millisecond
    would be a clip whose waveform starts just outside it — visible, and
    unfixable by hand. One walk, one answer.

    ⚠ A PIECE IS NEVER MIXED INTO THE ONE BEFORE IT. If two overlap the later one
    is pushed to the end of the earlier rather than summed: this is one voice
    reading in turn, and two lines on top of each other is not a mix, it is a
    mistake nobody can edit their way out of. The caller's layout is what makes
    that branch unreachable; it is here so that a bug up there is audible as a
    late line rather than as a garbled one. ⚠ The window follows the bytes when
    it does, so a pushed piece is still drawn where it is actually heard.
    """
    track = bytearray()
    windows: list[dict] = []
    for at, pcm in sorted(pieces or [], key=lambda piece: piece[0]):
        if not pcm:
            continue
        clock = _ms_of_bytes(len(track))
        if at > clock:
            track += silence(at - clock)
        start = _ms_of_bytes(len(track))
        track += pcm
        windows.append({
            "start_ms": start,
            "duration_ms": _ms_of_bytes(len(track)) - start,
        })
    return wav_bytes(bytes(track)), windows


def assemble(pieces: list[tuple[int, bytes]]) -> bytes:
    """`lay_track`'s WAV, for a caller that does not care where the pieces are."""
    return lay_track(pieces)[0]
