"""tts_providers_check.py — THE VOICEOVER CAN LEAVE GOOGLE, AND NOTHING ELSE NOTICES.

    python tests/tts_providers_check.py   (no backend, no network, no dollar)

WHY THIS FILE EXISTS. The captions were the first capability pointed at a
non-Google backend and `deepgram_captions_check.py` is the guard for that. The
voiceover is the second, and it is a harder change, because a transcript is DATA
and speech is BYTES ON A CLOCK: every duration, every caption and every stretched
shot downstream is arithmetic over one assumed audio format. So the properties
under test are not "Sarvam works". They are:

    1. **Whatever backend answers, the bytes are the same bytes.** One rate, one
       width, one channel count — and audio that is not that is REFUSED rather
       than laid down 9% slow with nothing on screen to say so.
    2. **The stage direction is Google's alone.** `prompt_for` wraps a line in
       "Read this line as an elderly man:" for Gemini, which steers it. Sent to
       Sarvam or Deepgram that sentence is READ OUT LOUD, in a paid run, by the
       narrator. Section 4 is that claim and it is the most important one here.
    3. **A saved sheet survives a switch.** "Kore" is a Google voice; a board
       voiced last week and re-read today on Sarvam must not fail, and must not
       silently throw away the casting the user did.
    4. **A run that cannot work is refused BEFORE it spends.** Aura has no Hindi
       and Bulbul has no Spanish. A voiceover is one call PER LINE, so finding
       that out on line 1 of 40 is a half-finished paid run.

⚠ **NO NETWORK.** `requests.post` is replaced for the whole run, in both vendor
modules. What this file cannot tell you is whether a real key works or whether an
API's response shape has changed under us — that costs a call, and belongs in a
live run.

⚠ **AND IT CONTROLS THE ENVIRONMENT RATHER THAN READING YOURS.** Importing these
modules runs `load_dotenv()`, so a developer with a real `SARVAM_API_KEY` would
otherwise get different answers from the same file.
"""

import io
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import base64  # noqa: E402
import json  # noqa: E402

import deepgram  # noqa: E402
import sarvam  # noqa: E402
import tts  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


CONTROLLED = (
    "VOICE_PROVIDER", "TEXT_PROVIDER", "CAPTION_PROVIDER",
    "SARVAM_API_KEY", "SARVAM_MODEL", "SARVAM_LANGUAGE", "SARVAM_USD_PER_1K",
    "SARVAM_CAST",
    "DEEPGRAM_API_KEY", "DEEPGRAM_TTS_MODEL", "DEEPGRAM_TTS_USD_PER_1K",
    "DEEPGRAM_TTS_LANGUAGE",
    "GEMINI_KEY_VOICE", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_TTS_MODEL",
    "API_VOICEOVER_USD_PER_1K",
)


def env(**values) -> None:
    for name in CONTROLLED:
        os.environ.pop(name, None)
    for name, value in values.items():
        os.environ[name] = value


# --- The fake transport -----------------------------------------------------
# ⚠ ONE PLACE THE REQUEST IS RECORDED, so every section can assert on what was
# actually sent rather than on what the code looks like it sends.
sent: dict = {}


def wav_of(pcm: bytes, rate: int = 24_000, width: int = 2, channels: int = 1) -> bytes:
    """A WAV in whatever shape the test wants — including a wrong one."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


# 1000ms of "speech": 48,000 bytes = 24,000 frames × 2 bytes at 24 kHz. The byte
# count IS the duration here exactly as it is in the app — see `tts._ms_of_bytes`.
SPEECH = b"\x01\x02" * 24_000


class FakeResponse:
    def __init__(self, payload, status_code=200, raw: bytes | None = None):
        self._payload = payload
        self.status_code = status_code
        self.content = raw if raw is not None else b""
        self.text = payload if isinstance(payload, str) else json.dumps(payload or {})

    def json(self):
        if isinstance(self._payload, str) or self._payload is None:
            raise ValueError("not json")
        return self._payload


_next: list = []


def fake_post(url, **kwargs):
    sent.clear()
    sent.update(url=url, **kwargs)
    return _next.pop(0) if _next else FakeResponse({}, 500)


sarvam.requests.post = fake_post
deepgram.requests.post = fake_post
# ⚠ NO REAL WAITING. The retry ladder is `retry_policy`'s and is tested there;
# here it must not turn one 429 into fifteen real seconds.
sarvam.time.sleep = lambda _s: None
deepgram.time.sleep = lambda _s: None


def answer_with(payload, status_code=200, raw: bytes | None = None, times: int = 1):
    _next.clear()
    for _ in range(times):
        _next.append(FakeResponse(payload, status_code, raw))


def sarvam_ok(pcm: bytes = SPEECH, **wav_kwargs):
    answer_with({"request_id": "r1", "audios": [
        base64.b64encode(wav_of(pcm, **wav_kwargs)).decode()
    ]})


def deepgram_ok(pcm: bytes = SPEECH, **wav_kwargs):
    answer_with(None, 200, raw=wav_of(pcm, **wav_kwargs))


# ---------------------------------------------------------------------------
print("\n1 · The switch — one line in .env moves the voiceover, and nothing else\n")
# ---------------------------------------------------------------------------
env()
check("with nothing set the voiceover is still on Vertex, exactly as before",
      tts.resolve_provider() == "vertex")

env(TEXT_PROVIDER="gemini")
check("⚠ it still inherits TEXT_PROVIDER — an upgrade must not re-point it",
      tts.resolve_provider() == "gemini")

env(VOICE_PROVIDER="sarvam")
check("VOICE_PROVIDER=sarvam is what moves it", tts.resolve_provider() == "sarvam")

env(VOICE_PROVIDER="deepgram")
check("VOICE_PROVIDER=deepgram too", tts.resolve_provider() == "deepgram")

env(SARVAM_API_KEY="sk-test", DEEPGRAM_API_KEY="dg-test")
check("⚠ NEITHER VENDOR KEY IS A SWITCH — a vendor sells more than one thing",
      tts.resolve_provider() == "vertex")

env(GEMINI_KEY_VOICE="g-test")
check("…but a key that names the CAPABILITY still is one",
      tts.resolve_provider() == "gemini")

env(VOICE_PROVIDER="elevenlabs")
try:
    tts.resolve_provider()
    check("an unknown provider is refused by name", False)
except tts.VoiceoverError as exc:
    check("an unknown provider is refused by name, not attempted",
          "elevenlabs" in str(exc) and "VOICE_PROVIDER" in str(exc))

# ---------------------------------------------------------------------------
print("\n2 · The bytes — every backend answers in the one format this app measures\n")
# ---------------------------------------------------------------------------
env()
check("the three modules agree on rate, width and channels",
      (sarvam.SAMPLE_RATE, sarvam.SAMPLE_WIDTH, sarvam.CHANNELS)
      == (deepgram.TTS_SAMPLE_RATE, deepgram.TTS_SAMPLE_WIDTH, deepgram.TTS_CHANNELS)
      == (tts.SAMPLE_RATE, tts.SAMPLE_WIDTH, tts.CHANNELS))

env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
sarvam_ok()
pcm = tts.speak("Namaste.", language="hindi")
check("Sarvam's WAV comes back as raw PCM, header gone", pcm == SPEECH)
check("…and the byte count IS the duration, with no decoder",
      tts.pcm_duration_ms(pcm) == 1000)
check("⚠ the run asks for the house sample rate rather than hoping for it",
      sent["json"]["speech_sample_rate"] == tts.SAMPLE_RATE)

sarvam_ok(rate=22_050)
try:
    tts.speak("Namaste.", language="hindi")
    check("⚠ AUDIO AT THE WRONG RATE IS REFUSED, not laid down 9% slow", False)
except tts.VoiceoverError as exc:
    check("⚠ AUDIO AT THE WRONG RATE IS REFUSED, not laid down 9% slow",
          "22050" in str(exc) and "resample" in str(exc))

sarvam_ok(channels=2, pcm=SPEECH * 2)
try:
    tts.speak("Namaste.", language="hindi")
    check("…and stereo is refused too, for the same arithmetic", False)
except tts.VoiceoverError as exc:
    check("…and stereo is refused too, for the same arithmetic", "2ch" in str(exc))

env(VOICE_PROVIDER="deepgram", DEEPGRAM_API_KEY="dg-test")
deepgram_ok()
pcm = tts.speak("Hello there.", language="english")
check("Aura's WAV comes back as the same raw PCM", pcm == SPEECH)
check("⚠ …because the request asks for linear16, not the MP3 default",
      sent["params"]["encoding"] == "linear16"
      and sent["params"]["sample_rate"] == str(tts.SAMPLE_RATE)
      and sent["params"]["container"] == "wav")

deepgram_ok(rate=16_000)
try:
    tts.speak("Hello there.", language="english")
    check("…and a 16 kHz answer is refused there as well", False)
except tts.VoiceoverError as exc:
    check("…and a 16 kHz answer is refused there as well", "16000" in str(exc))

# ---------------------------------------------------------------------------
print("\n3 · The cast — the picker belongs to whichever backend is switched on\n")
# ---------------------------------------------------------------------------
env()
check("on Google the cast is Google's, unchanged", tts.cast() == tts.CAST)
check("…and its default voice is still Kore", tts.default_voice() == "Kore")

env(VOICE_PROVIDER="sarvam")
check("on Sarvam the picker offers Sarvam's speakers",
      all(v["name"] in sarvam.voices() for v in tts.cast()))
check("⚠ and NOT one Google name — the API has never heard of them",
      not ({v["name"] for v in tts.cast()} & set(tts.VOICES)))

env(VOICE_PROVIDER="deepgram")
check("on Deepgram the picker offers Aura voices",
      all(v["name"].startswith("aura-") for v in tts.cast()))

# ⚠ ON THIS BACKEND THE VOICE NAME *IS* THE LANGUAGE — there is no language
# parameter to get wrong, only a voice. So the cast is per language, and an
# English voice reaching a German run is the whole failure mode.
LANGS = {"english": "en", "spanish": "es", "german": "de", "french": "fr",
         "dutch": "nl", "italian": "it", "japanese": "ja"}
for name, code in LANGS.items():
    voices = {v["name"] for v in tts.cast(language=name)}
    check(f"[{name}] every voice offered really speaks it (-{code})",
          bool(voices) and all(v.endswith(f"-{code}") for v in voices))
    check(f"[{name}] …and every persona casts one of them",
          all(p["voice"] in voices for p in tts.personas(language=name).values()))
check("⚠ the seven casts are seven different sets of voices",
      len({tuple(v["name"] for v in tts.cast(language=n)) for n in LANGS}) == 7)
check("⚠ AN ENGLISH VOICE ON A GERMAN FILM IS RE-CAST, NOT SENT",
      tts.voice_for({"voice": "aura-2-hera-en"}, language="german")
      == deepgram.tts_voice_for_persona("woman", "german"))
check("…and the persona it stood for is what survives the move",
      tts.voice_for({"voice": "aura-2-atlas-en"}, language="italian")
      == deepgram.tts_voice_for_persona("grandfather", "italian"))

env(VOICE_PROVIDER="deepgram", DEEPGRAM_TTS_MODEL="aura-2-zeus-en")
check("a default voice named in .env is used when it fits the film",
      tts.tts_model_id(language="english") == "aura-2-zeus-en")
check("⚠ …and IGNORED when it does not — an English default must not read Japanese",
      tts.tts_model_id(language="japanese").endswith("-ja"))

for provider in ("vertex", "sarvam", "deepgram"):
    env(VOICE_PROVIDER=provider)
    check(f"[{provider}] the persona list is the SAME KEYS whatever reads it",
          sorted(tts.personas()) == sorted(tts.PERSONAS))
    check(f"[{provider}] …and every persona casts a voice this backend has",
          all(p["voice"] in {v["name"] for v in tts.cast()}
              for p in tts.personas().values()))
    check(f"[{provider}] …so the picker can never offer a name the run is refused for",
          all(tts.resolve_voice(v["name"]) == v["name"] for v in tts.cast()))

env(VOICE_PROVIDER="sarvam")
check("⚠ A SAVED SHEET SURVIVES THE SWITCH — 'Kore' is translated, not dropped",
      tts.voice_for({"voice": "Kore"}) == sarvam.voice_for_persona("woman"))
check("…through the PERSONA it was cast for, so the casting is kept",
      tts.voice_for({"voice": "Algenib"}) == sarvam.voice_for_persona("grandfather"))
check("…and a name from nowhere still folds to the default rather than failing",
      tts.voice_for({"voice": "Gandalf"}) == sarvam.default_voice())
check("the line's own voice still wins over its persona",
      tts.voice_for({"voice": "mani", "persona": "girl"}) == "mani")
check("⚠ …and a line with no persona still keeps the dialog's own choice",
      tts.voice_for({}, "ishita") == "ishita")

env(VOICE_PROVIDER="sarvam", SARVAM_MODEL="bulbul:v2")
check("⚠ the v2 cast is not the v3 cast — a v3 name would be a 400",
      {v["name"] for v in tts.cast()} == set(sarvam.voices("bulbul:v2")))
check("…and a v3 name folds down rather than being sent to v2",
      tts.resolve_voice("ishita") == sarvam.default_voice("bulbul:v2"))

# ---------------------------------------------------------------------------
print("\n4 · ⚠ THE STAGE DIRECTION IS GOOGLE'S ALONE (the one that costs a film)\n")
# ---------------------------------------------------------------------------
GRANDPA = {"text": "Sit with me a while.", "persona": "grandfather"}

env()
check("on Google the direction is prepended — it is how the model is steered",
      "elderly man" in tts.prompt_for(GRANDPA))
check("…and the words themselves are quoted, so the direction is not read out",
      tts.prompt_for(GRANDPA).endswith('"Sit with me a while."'))

for provider in ("sarvam", "deepgram"):
    env(VOICE_PROVIDER=provider)
    check(f"⚠ [{provider}] THE BARE LINE IS SENT — a direction would be READ ALOUD",
          tts.prompt_for(GRANDPA) == "Sit with me a while.")
    check(f"[{provider}] …and `direction_for` says so, so nothing prints it either",
          tts.direction_for("grandfather") == "")

env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
sarvam_ok()
tts.speak_lines([GRANDPA], language="hindi")
check("⚠ …and the bytes on the wire carry no instruction at all",
      sent["json"]["text"] == "Sit with me a while.")
check("the age reaches the model as CASTING instead — the right speaker…",
      sent["json"]["speaker"] == sarvam.voice_for_persona("grandfather"))
check("…and as PACE, the one delivery control this model has",
      sent["json"]["pace"] < 1.0)

# ---------------------------------------------------------------------------
print("\n5 · The quote prices the backend that will actually run\n")
# ---------------------------------------------------------------------------
LINES = [GRANDPA, {"text": "Not today, dadaji.", "persona": "boy"}]

env()
google = tts.estimate(LINES)
env(VOICE_PROVIDER="sarvam")
indian = tts.estimate(LINES, language="hinglish")
env(VOICE_PROVIDER="deepgram")
aura = tts.estimate(LINES, language="english")

check("⚠ Google is quoted MORE CHARACTERS — it is sent the directions too",
      google["characters"] > indian["characters"])
check("…and the other two are quoted exactly the words that will be spoken",
      indian["characters"] == sum(len(l["text"]) for l in LINES))
check("the three prices are three different numbers",
      len({google["usd"], indian["usd"], aura["usd"]}) == 3)
check("⚠ the model shown names the backend AND, on Sarvam, the language",
      indian["model"] == "bulbul:v3 (hi-IN)")
env(VOICE_PROVIDER="sarvam")
check("…so a film quoted in the wrong language is visible before the money",
      tts.estimate(LINES, language="tamil")["model"] == "bulbul:v3 (ta-IN)")
check("⚠ WHO BILLS COMES DOWN WITH THE PRICE — the dialog used to always say Google",
      (google["biller"], indian["biller"], aura["biller"])
      == ("Google", "Sarvam", "Deepgram"))
check("the spend guard is the same guard on every backend",
      google["limit_characters"] == indian["limit_characters"] == tts.MAX_CHARACTERS)
env(VOICE_PROVIDER="sarvam", SARVAM_USD_PER_1K="0.10")
check("the rate is env-overridable, so a price correction needs no deploy",
      tts.estimate([{"text": "x" * 1000}])["usd"] == 0.10)

# ---------------------------------------------------------------------------
print("\n6 · ⚠ A run that cannot work is refused BEFORE it spends\n")
# ---------------------------------------------------------------------------
env(VOICE_PROVIDER="deepgram", DEEPGRAM_API_KEY="dg-test")
check("Aura reads English", deepgram.tts_speaks("english"))
check("…and the other six it is cast for",
      all(deepgram.tts_speaks(x) for x in
          ("spanish", "german", "french", "dutch", "italian", "japanese")))
check("…including the endonyms and the codes people actually type",
      all(deepgram.tts_speaks(x) for x in ("español", "deutsch", "日本語", "it-it")))
check("⚠ Aura does not read Hindi, and does not pretend to",
      not deepgram.tts_speaks("hindi"))
check("…nor Tamil, Bengali, Korean or anything else it has no voices for",
      not any(deepgram.tts_speaks(x) for x in ("tamil", "bengali", "korean", "polish")))
try:
    tts.preflight(language="Hindi")
    check("…so a Hindi board is refused here, not on line 1 of 40", False)
except tts.VoiceoverError as exc:
    check("…so a Hindi board is refused here, not on line 1 of 40",
          "sarvam" in str(exc).lower() and "VOICE_PROVIDER" in str(exc))

env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
check("Bulbul reads Hindi", sarvam.speaks("hindi"))
check("⚠ …and HINGLISH, which is the whole reason it is here",
      sarvam.language_code("hinglish") == "hi-IN")
check("…and Tamil, Bengali, Marathi and the rest",
      all(sarvam.speaks(x) for x in ("tamil", "bengali", "marathi", "telugu")))
check("⚠ but NOT Spanish — an Indic model reading Spanish is a wasted run",
      not sarvam.speaks("spanish"))
try:
    tts.preflight(language="Spanish")
    check("…so that is refused up front too", False)
except tts.VoiceoverError as exc:
    check("…so that is refused up front too", "VOICE_PROVIDER=gemini" in str(exc))
check("a blank language is a real answer, not a refusal — hi-IN by default",
      sarvam.language_code("") == "hi-IN")
env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test", SARVAM_LANGUAGE="en-IN")
check("…and the deployment can say what blank means",
      sarvam.language_code("") == "en-IN")

env(VOICE_PROVIDER="sarvam")
try:
    tts.preflight(language="hindi")
    check("no key is refused for free, naming the variable to set", False)
except tts.VoiceoverError as exc:
    check("no key is refused for free, naming the variable to set",
          "SARVAM_API_KEY" in str(exc) and "VOICE_PROVIDER=gemini" in str(exc))

env(VOICE_PROVIDER="deepgram")
try:
    tts.preflight(language="english")
    check("…same on the other one", False)
except tts.VoiceoverError as exc:
    check("…same on the other one", "DEEPGRAM_API_KEY" in str(exc))

# ---------------------------------------------------------------------------
print("\n7 · What actually goes on the wire\n")
# ---------------------------------------------------------------------------
env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
sarvam_ok()
tts.speak("Aaj hum baat karenge.", voice="mani", language="hinglish")
check("the key travels in Sarvam's own header, not as a bearer token",
      sent["headers"]["api-subscription-key"] == "sk-test")
check("⚠ the language is NEVER omitted — it decides pronunciation",
      sent["json"]["language_code"] == "hi-IN")
check("the model and the speaker are both named", sent["json"]["model"] == "bulbul:v3"
      and sent["json"]["speaker"] == "mani")
check("⚠ v2-only fields are not sent to v3, which rejects them",
      "enable_preprocessing" not in sent["json"] and "pitch" not in sent["json"])

env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test", SARVAM_MODEL="bulbul:v2")
sarvam_ok()
tts.speak("Aaj hum baat karenge.", language="hindi")
check("…and v2 IS sent the one it needs to read numbers and English",
      sent["json"].get("enable_preprocessing") is True)

env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
# The older API spelled the field `target_language_code`. A 400 that complains
# about it is retried once under the other spelling rather than failing a run
# over a rename nobody deployed.
_next.clear()
_next.append(FakeResponse({"error": {"message": "extra fields not permitted: language_code"}}, 400))
_next.append(FakeResponse({"audios": [base64.b64encode(wav_of(SPEECH)).decode()]}, 200))
pcm = tts.speak("Namaste.", language="hindi")
check("⚠ a 400 about the language FIELD is healed, not thrown at the user",
      pcm == SPEECH and sent["json"].get("target_language_code") == "hi-IN")

env(VOICE_PROVIDER="deepgram", DEEPGRAM_API_KEY="dg-test")
deepgram_ok()
tts.speak("Hello there.", voice="aura-2-zeus-en", language="english")
check("Aura is a token in the Authorization header, like the listening half",
      sent["headers"]["Authorization"] == "Token dg-test")
check("⚠ the VOICE IS THE MODEL here — there is no speaker parameter",
      sent["params"]["model"] == "aura-2-zeus-en")
check("…and the text is the whole body", sent["json"] == {"text": "Hello there."})

# ---------------------------------------------------------------------------
print("\n8 · Failures say which line to change, and only retry what can succeed\n")
# ---------------------------------------------------------------------------
env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
for status, wanted in ((401, "SARVAM_API_KEY"), (402, "VOICE_PROVIDER=gemini"),
                       (400, "SARVAM_MODEL")):
    answer_with({"error": {"message": "no"}}, status, times=6)
    try:
        tts.speak("Namaste.", language="hindi")
        check(f"⚠ HTTP {status} names what to do", False)
    except tts.VoiceoverError as exc:
        check(f"⚠ HTTP {status} names what to do — {wanted}", wanted in str(exc))

answer_with({"error": {"message": "slow down"}}, 429, times=1)
_next.append(FakeResponse({"audios": [base64.b64encode(wav_of(SPEECH)).decode()]}, 200))
check("⚠ a 429 is WAITED THROUGH — a voiceover is one call per line, and a "
      "board half-read is a board half-paid-for",
      tts.speak("Namaste.", language="hindi") == SPEECH)

answer_with({"error": {"message": "nope"}}, 401, times=5)
try:
    tts.speak("Namaste.", language="hindi")
    check("…while a 401 is not retried at all — waiting never fixes a bad key", False)
except tts.VoiceoverError:
    check("…while a 401 is not retried at all — waiting never fixes a bad key",
          len(_next) == 4)

env(VOICE_PROVIDER="deepgram", DEEPGRAM_API_KEY="dg-test")
for status, wanted in ((401, "DEEPGRAM_API_KEY"), (402, "VOICE_PROVIDER=gemini"),
                       (413, "characters per request"), (400, "DEEPGRAM_TTS_MODEL")):
    answer_with({"err_msg": "no"}, status, times=6)
    try:
        tts.speak("Hello there.", language="english")
        check(f"⚠ HTTP {status} names what to do", False)
    except tts.VoiceoverError as exc:
        check(f"⚠ HTTP {status} names what to do — {wanted}", wanted in str(exc))

check("⚠ the route only ever sees VoiceoverError, whichever backend ran",
      issubclass(tts.VoiceoverError, Exception)
      and not issubclass(sarvam.SarvamError, tts.VoiceoverError))

# ---------------------------------------------------------------------------
print("\n9 · A line longer than one request is split at the least bad seam\n")
# ---------------------------------------------------------------------------
LONG = ("Ye kahani bahut purani hai. " * 200).strip()   # ~5,400 characters
pieces = sarvam.chunks(LONG, sarvam.max_chars("bulbul:v3"))
check("a long line becomes several requests rather than one 400",
      len(pieces) > 1 and all(len(p) <= 2500 for p in pieces))
check("⚠ …cut at sentence ends, so the seam is a breath and not a syllable",
      all(p.endswith(".") for p in pieces))
check("nothing is lost in the split",
      sum(len(p.replace(" ", "")) for p in pieces) == len(LONG.replace(" ", "")))
check("a short line is still exactly one request",
      sarvam.chunks("Namaste.", 2500) == ["Namaste."])
check("…and an empty one is no request at all", sarvam.chunks("   ", 2500) == [])
check("the Devanagari danda counts as a sentence end",
      len(sarvam.chunks("क ख ग। " * 100, 60)) > 1)
check("Aura splits the same way, at its own smaller limit",
      all(len(p) <= deepgram.TTS_MAX_CHARS
          for p in deepgram.tts_chunks("Hello there. " * 400)))

env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
answer_with({"audios": [base64.b64encode(wav_of(SPEECH)).decode()]}, 200, times=3)
long_pcm = tts.speak("Ek. " * 900, language="hindi")
check("⚠ …and the pieces are concatenated as PCM, so the duration still adds up",
      tts.pcm_duration_ms(long_pcm) == 2000 and len(_next) == 1)

# ---------------------------------------------------------------------------
print("\n10 · The lines a shot speaks are still one blob on one clock\n")
# ---------------------------------------------------------------------------
env(VOICE_PROVIDER="sarvam", SARVAM_API_KEY="sk-test")
answer_with({"audios": [base64.b64encode(wav_of(SPEECH)).decode()]}, 200, times=4)
pcm, spans = tts.speak_lines(
    [{"text": "Pehli baat.", "persona": "man"},
     {"text": "Doosri baat.", "persona": "woman"}],
    language="hindi",
)
check("two lines, two spans, in order", len(spans) == 2 and spans[0]["end_ms"] <= spans[1]["start_ms"])
check("⚠ the gap between them is the house gap, whichever backend spoke",
      spans[1]["start_ms"] - spans[0]["end_ms"] == tts.GAP_MS)
check("the blob is as long as the last span says it is",
      tts.pcm_duration_ms(pcm) == spans[-1]["end_ms"])
check("⚠ the SPAN CARRIES THE WORDS, not the prompt — these become captions",
      spans[0]["text"] == "Pehli baat.")

# ---------------------------------------------------------------------------
print("\n11 · ⚠ A PROMISE THIS BACKEND CANNOT KEEP IS PRINTED, NOT HIDDEN\n")
# ---------------------------------------------------------------------------
# Google is the only one of the three with real CHILD voices and the only one
# that can be told an age. Sarvam publishes a sex per speaker and NO ages; Aura's
# youngest published tier is "young adult" and some of its languages have two
# voices in total. The run is still valid — what must not happen is a picker that
# says "Child" and quietly delivers an adult.
env()
check("on Google nothing is approximate — it has the voices it offers",
      all(tts.persona_note(k) == "" for k in tts.PERSONAS))
check("…so the dialog prints no advisory for a Google run",
      all(p.get("note") == "" for p in tts.personas().values()))

env(VOICE_PROVIDER="sarvam")
check("⚠ on Sarvam 'child' SAYS it is the nearest adult voice, not a child",
      "no ages" in tts.persona_note("child"))
check("…and so do 'boy', 'girl', 'grandfather' and 'grandmother'",
      all(tts.persona_note(k) for k in ("boy", "girl", "grandfather", "grandmother")))
check("…while the parts it CAN cast say nothing at all",
      all(tts.persona_note(k) == "" for k in ("man", "woman", "young_man", "narrator")))
check("the note travels with the picker, keyed to the persona",
      tts.personas()["child"]["note"] == tts.persona_note("child"))

env(VOICE_PROVIDER="deepgram")
check("⚠ on Aura 'child' says there are no child voices — in every language",
      all("child voices" in tts.persona_note("child", language=n) for n in LANGS))
check("⚠ French says the harder truth: two voices for the whole cast",
      "two voices" in tts.persona_note("girl", language="french"))
check("…and a language with real mature voices does not apologise for them",
      tts.persona_note("grandmother", language="english") == "")

# --- correcting the casting by ear, without a deploy ------------------------
env(VOICE_PROVIDER="sarvam")
check("every curated Sarvam speaker is one the API actually accepts",
      all(v["name"] in sarvam.speakers("bulbul:v3") for v in sarvam.cast("bulbul:v3")))
check("…and the same for v2, whose roster is a different seven",
      all(v["name"] in sarvam.speakers("bulbul:v2") for v in sarvam.cast("bulbul:v2")))
check("the roster is the SDK's 44 names split by model (37 + 7), not the 11 we cast",
      len(sarvam.speakers("bulbul:v3")) == 37
      and len(sarvam.speakers("bulbul:v2")) == 7
      and not (set(sarvam.speakers("bulbul:v3")) & set(sarvam.speakers("bulbul:v2"))))

env(VOICE_PROVIDER="sarvam", SARVAM_CAST="grandfather:anand,child:shruti@1.2")
check("⚠ SARVAM_CAST re-casts a persona without touching the code",
      tts.voice_for_persona("grandfather") == "anand"
      and tts.voice_for_persona("child") == "shruti")
check("…and the PICKER shows the override too, not just the run",
      {v["name"] for v in tts.cast()} >= {"anand", "shruti"})
check("⚠ …and an overridden row stops apologising — somebody has chosen now",
      tts.persona_note("grandfather") == "" and tts.persona_note("child") == "")
check("a pace can travel with it, clamped to what the model accepts",
      sarvam.entry_for(persona="child")["pace"] == 1.2)
check("…and the parts it did not name are left exactly as they were",
      tts.voice_for_persona("woman") == "ishita")

env(VOICE_PROVIDER="sarvam", SARVAM_CAST="grandfather:gandalf,child:,,,junk")
check("⚠ A TYPO IN AN OPTIONAL TUNING LINE MUST NOT BE ABLE TO STOP A PAID RUN",
      tts.voice_for_persona("grandfather") == "ratan")
check("…every unusable entry is dropped, not half-applied",
      tts.voice_for_persona("child") == "suhani")

env(VOICE_PROVIDER="sarvam", SARVAM_MODEL="bulbul:v2", SARVAM_CAST="man:mani")
check("⚠ an override naming a speaker THIS MODEL lacks is ignored, not sent",
      tts.voice_for_persona("man") == "abhilash")

# ---------------------------------------------------------------------------
print("\n12 · …and it reaches the dialog, which is the only place it is any use\n")
# ---------------------------------------------------------------------------
# A note nobody can see is a comment. This walks the last two hops: the route's
# own summariser, and the browser source that prints it.
env(VOICE_PROVIDER="deepgram")
from server import animatics as _routes  # noqa: E402
from server.schemas import AnimaticDialogueSheet, PersonaOption  # noqa: E402

_cast = tts.personas("deepgram", "english")
SHEET_LINES = [
    {"text": "Dadaji, chalo.", "persona": "child"},
    {"text": "Aa raha hoon.", "persona": "boy"},
    {"text": "Baith jao.", "persona": "man"},
    {"text": "   ", "persona": "child"},          # empty: priced at nothing, counted as nothing
]
advisory = _routes._casting_advisory(SHEET_LINES, _cast)
check("⚠ the dialog is told, in one sentence, which lines will not match their part",
      "child voices" in advisory)
check("…counted from THIS sheet, so an empty line is not one of them",
      advisory.count("1 line") == 2 and "3 line" not in advisory)
check("⚠ the same reason twice is ONE fact with a count, not two lines (E155)",
      _routes._casting_advisory(
          [{"text": "a", "persona": "child"}, {"text": "b", "persona": "child"}], _cast
      ).count("2 lines") == 1)
check("a film with nobody young in it hears nothing about child voices",
      _routes._casting_advisory([{"text": "Baith jao.", "persona": "man"}], _cast) == "")
check("…and a Google run is silent whatever the cast",
      _routes._casting_advisory(SHEET_LINES, tts.personas("vertex")) == "")

check("the sheet has somewhere to put all of it",
      {"provider", "engine", "warning", "advisory"} <= set(AnimaticDialogueSheet.model_fields))
check("…and each persona carries its own note down with it",
      "note" in PersonaOption.model_fields)

with io.open("client/src/components/AnimaticEditor.jsx", encoding="utf-8") as fh:
    _jsx = fh.read()
check("⚠ THE BROWSER ACTUALLY PRINTS THE HARD WARNING — a refusal nobody sees is a broken button",
      "speechSheet.warning" in _jsx and "speechSheet?.warning" in _jsx)
check("…and disables the button that would price a run the server will refuse",
      "!!speechSheet?.warning" in _jsx)
check("…and prints the soft advisory, which is NOT an error and must not read as one",
      "speechSheet.advisory" in _jsx and "an-prop-warn\">⚠ {speechSheet.advisory}" not in _jsx)
check("…and the per-line note beside the line it applies to",
      "persona?.note" in _jsx)
check("⚠ …and the default voice is re-picked for the active backend, not left as 'Kore'",
      "names.includes(speechVoice)" in _jsx)
# ⚠ THE AUDIO CONFIRM ONLY. The Veo and reframe dialogs elsewhere in this file
# still say "Google bills the actual amount" and are still RIGHT to: video and
# text have no non-Google backend. Asserting over the whole file would either
# fail on those or force them to lie the other way, so the window is the block
# that prices a captions/voiceover run.
_audio_confirm = _jsx[_jsx.index("speechConfirm.estimate.usd"):][:2500]
check("⚠ …and the audio price names whoever actually bills, not always Google",
      "speechConfirm.estimate.biller" in _audio_confirm
      and "Google bills the actual" not in _audio_confirm)
check("…while the Veo dialogs, which really are billed by Google, still say so",
      _jsx.count("Google bills the actual") == 3)

env()
print()
if failures:
    print(f"FAILED: {len(failures)}")
    for name in failures:
        print("  · " + name)
    sys.exit(1)
print(
    f"All voiceover-provider checks passed ({len(CONTROLLED)} env vars controlled, "
    "3 backends, no network, no key spent)."
)
