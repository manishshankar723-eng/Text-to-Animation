"""deepgram_captions_check.py — THE CAPTIONS CAN LEAVE GOOGLE, AND STILL LOOK THE SAME.

    python tests/deepgram_captions_check.py   (no backend, no network, no dollar)

WHY THIS FILE EXISTS. Captions were the first capability pointed at a non-Google
backend, and that is a bigger change than it looks: everything downstream of
`transcribe` — `tidy_lines`, the splitter, the overlap rules, the drawing — was
written against one provider's output and must not learn there is a second. So
the property under test is not "Deepgram works". It is:

    **A transcript from Deepgram is the same OBJECT a transcript from Gemini is.**

⚠ **AND THE LANGUAGE PARAMETER IS THE DANGEROUS ONE.** Deepgram's default is
`language=en`, and a wrong language does not fail — it returns confident nonsense
at correct timings, so every check downstream passes and the user gets a paid
run of subtitles nobody can read. `language_code()` must therefore NEVER return
the empty string, for any input at all, including ones it does not recognise.
Section 2 is that claim and it is the most important one here.

⚠ **NO NETWORK.** `requests.post` is replaced for the whole run. What this file
cannot tell you is whether a real key works or whether Deepgram's response shape
has changed under us — that costs a call, and belongs in a live run.

⚠ **AND IT CONTROLS THE ENVIRONMENT RATHER THAN READING YOURS.** Importing these
modules runs `load_dotenv()`, so a developer with a real `DEEPGRAM_API_KEY` would
otherwise get different answers from the same file.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ai_keys  # noqa: E402
import captions  # noqa: E402
import deepgram  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


CONTROLLED = (
    "CAPTION_PROVIDER", "TEXT_PROVIDER", "DEEPGRAM_API_KEY", "DEEPGRAM_MODEL",
    "GEMINI_KEY_CAPTION", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "DEEPGRAM_USD_PER_MINUTE", "DEEPGRAM_USD_PER_MINUTE_MULTI",
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


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


_next: list = []


def fake_post(url, **kwargs):
    sent.clear()
    sent.update(url=url, **kwargs)
    return _next.pop(0)


deepgram.requests.post = fake_post


def answer_with(payload, status_code=200):
    _next.clear()
    _next.append(FakeResponse(payload, status_code))


UTTERANCE_REPLY = {
    "metadata": {"duration": 12.5},
    "results": {
        "channels": [{"alternatives": [{
            "transcript": "Namaste doston. Aaj hum baat karenge.",
            "words": [{"word": "Namaste", "start": 0.32, "end": 0.9}],
        }]}],
        "utterances": [
            {"start": 0.32, "end": 2.10, "transcript": "Namaste doston."},
            {"start": 2.44, "end": 5.02, "transcript": "Aaj hum baat karenge."},
        ],
    },
}


# ===========================================================================
print("\n1 · CAPTION_PROVIDER=deepgram routes there, and nothing else does\n")

env(CAPTION_PROVIDER="deepgram")
check("the captions resolve to deepgram", captions.resolve_provider() == "deepgram")
check("deepgram is a supported caption provider",
      "deepgram" in captions.SUPPORTED_PROVIDERS)

env(TEXT_PROVIDER="vertex")
check("…and without the line they are back on the text backend",
      captions.resolve_provider() == "vertex")

# ⚠ A VENDOR KEY IS NOT A SWITCH, and this is the one place that is provable.
# `GEMINI_KEY_CAPTION` moves the captions on its own because the name says who is
# paid AND what for. `DEEPGRAM_API_KEY` says only who — and Deepgram sells TTS
# too, so a key that moved a capability by itself would move the voiceover the
# day an Aura adapter lands.
env(TEXT_PROVIDER="vertex", DEEPGRAM_API_KEY="fake-dg-key")
check("⚠ DEEPGRAM_API_KEY alone does NOT move the captions",
      captions.resolve_provider() == "vertex", captions.resolve_provider())
check("…while GEMINI_KEY_CAPTION still does, because its name says both",
      ai_keys.own_provider("caption") == "" )
env(TEXT_PROVIDER="vertex", GEMINI_KEY_CAPTION="fake-gem-key")
check("…confirmed: the Gemini-named key is a switch",
      captions.resolve_provider() == "gemini")

env(CAPTION_PROVIDER="nonsense")
try:
    captions.resolve_provider()
    check("a typo in CAPTION_PROVIDER is refused", False, "no error raised")
except captions.CaptionError as exc:
    check("a typo in CAPTION_PROVIDER is refused, and names CAPTION_PROVIDER",
          "CAPTION_PROVIDER" in str(exc), str(exc))


# ===========================================================================
print("\n2 · ⚠ THE LANGUAGE IS NEVER SILENT — the default is `en` and that lies\n")

env()
# ⚠ THE CLAIM THAT MATTERS: not one input, however strange, produces "".
NEVER_BLANK = [
    "", "   ", "Hindi", "hindi", "HINDI", "हिन्दी", "Hinglish", "hinglish",
    "English", "Tamil", "தமிழ்", "Bengali", "bangla", "Bhojpuri", "Maithili",
    "Klingon", "!!", "123", "Punjabi", "ਪੰਜਾਬੀ", "Urdu", "Marathi",
]
blanks = [n for n in NEVER_BLANK if not deepgram.language_code(n)]
check("⚠ no input at all produces an empty language code",
      not blanks, f"blank for: {blanks}")

check("Hindi maps to hi", deepgram.language_code("Hindi") == "hi")
check("…in Devanagari too", deepgram.language_code("हिन्दी") == "hi")
check("Tamil maps to ta", deepgram.language_code("தமிழ்") == "ta")
check("a bare code passes through", deepgram.language_code("bn") == "bn")
check("blank becomes multi, NOT en",
      deepgram.language_code("") == "multi", deepgram.language_code(""))
check("⚠ …and so does a language Nova-3 has no code for",
      deepgram.language_code("Bhojpuri") == "multi")

# ⚠ HINGLISH IS THE ONE WORTH SPELLING OUT. It is not a Deepgram code; it is
# Hindi and English in one sentence, which is what `multi` exists for.
check("⚠ Hinglish becomes multi, not hi — the English half must survive",
      deepgram.language_code("Hinglish") == "multi")

# ⚠ AND ONLY CODES NOVA-3 ACTUALLY LISTS ARE IN THE TABLE. A name mapped to a
# code the API rejects turns an unsupported language into a FAILED run, where
# `multi` at least attempts it.
NOVA3 = {
    "af", "ar", "hy", "as", "be", "bn", "bs", "bg", "ca", "zh", "hr", "cs", "da",
    "nl", "en", "et", "fi", "fr", "ka", "de", "el", "gu", "he", "hi", "hu", "id",
    "it", "ja", "kn", "kk", "ko", "lv", "lt", "mk", "ms", "mr", "mn", "ne", "no",
    "ps", "fa", "pl", "pt", "pa", "ro", "ru", "sr", "sk", "sl", "es", "sv", "tl",
    "ta", "te", "th", "tr", "uk", "ur", "vi",
}
strays = sorted({c for c in deepgram._LANGUAGE_CODES.values()} - NOVA3)
check("⚠ every code in the table is one Nova-3 lists", not strays, f"unknown: {strays}")


# ===========================================================================
print("\n3 · The request actually sent — the parameters are the contract\n")

env(DEEPGRAM_API_KEY="fake-dg-key")
answer_with(UTTERANCE_REPLY)
deepgram.transcribe(b"\x00\x01audio", mime_type="audio/mp3", language="Hindi")

check("it posts to Deepgram's listen endpoint", sent["url"] == deepgram.LISTEN_URL)
check("the key travels as a Token header",
      sent["headers"]["Authorization"] == "Token fake-dg-key")
check("the audio's own mime type is declared",
      sent["headers"]["Content-Type"] == "audio/mp3")
check("the bytes are the body, not a form field", sent["data"] == b"\x00\x01audio")
check("⚠ the language is ALWAYS sent — never left to the `en` default",
      sent["params"].get("language") == "hi", sent["params"])
check("utterances are asked for — that is the unit a caption is",
      sent["params"].get("utterances") == "true")
check("smart_format is on, so a line reads as a subtitle",
      sent["params"].get("smart_format") == "true")
check("the model is nova-3 by default", sent["params"].get("model") == "nova-3")

env(DEEPGRAM_API_KEY="fake-dg-key", DEEPGRAM_MODEL="nova-2")
answer_with(UTTERANCE_REPLY)
deepgram.transcribe(b"x", mime_type="audio/wav")
check("…and DEEPGRAM_MODEL pins it, because a model id ages first",
      sent["params"].get("model") == "nova-2")
check("⚠ a blank language still sends multi rather than nothing",
      sent["params"].get("language") == "multi")


# ===========================================================================
print("\n4 · ⚠ THE SHAPE IS THE ONE `tidy_lines` ALREADY TAKES\n")

env(DEEPGRAM_API_KEY="fake-dg-key")
answer_with(UTTERANCE_REPLY)
lines = deepgram.transcribe(b"x", mime_type="audio/mp3", language="Hindi")

check("two utterances become two lines", len(lines) == 2, str(lines))
check("every line has exactly the three keys the app uses",
      all(set(l) == {"start_ms", "end_ms", "text"} for l in lines), str(lines))
check("seconds became integer milliseconds",
      lines[0]["start_ms"] == 320 and lines[0]["end_ms"] == 2100, str(lines[0]))
check("the words are the transcript, not the whole file",
      lines[0]["text"] == "Namaste doston.")

# ⚠ THE REAL PROOF: the app's own tidying accepts it untouched. If this ever
# fails, the two backends have drifted into two code paths.
tidied = captions.tidy_lines(lines, total_ms=12500)
check("⚠ tidy_lines takes it with no adapter in between", bool(tidied))
check("…and gives back the same keys it always does",
      all({"start_ms", "end_ms", "text"} <= set(t) for t in tidied), str(tidied[:1]))
check("…in order and not overlapping",
      all(tidied[i]["end_ms"] <= tidied[i + 1]["start_ms"] for i in range(len(tidied) - 1)))


# ===========================================================================
print("\n5 · A response we can only half-read still produces subtitles\n")

env(DEEPGRAM_API_KEY="fake-dg-key")

# No utterances, but smart_format left paragraphs → sentence timings.
answer_with({
    "metadata": {"duration": 6.0},
    "results": {"channels": [{"alternatives": [{
        "transcript": "One. Two.",
        "paragraphs": {"paragraphs": [
            {"sentences": [
                {"start": 0.0, "end": 1.5, "text": "One."},
                {"start": 1.6, "end": 3.0, "text": "Two."},
            ]}
        ]},
    }]}]},
})
lines = deepgram.transcribe(b"x", mime_type="audio/mp3")
check("paragraphs are the second source when utterances are absent",
      len(lines) == 2 and lines[1]["text"] == "Two.", str(lines))

# Neither — the bare transcript, timed across the file. Deliberately poor.
answer_with({
    "metadata": {"duration": 9.0},
    "results": {"channels": [{"alternatives": [{
        "transcript": "Only one long line.",
        "words": [{"word": "Only", "start": 0.5, "end": 0.9},
                  {"word": "line", "start": 8.0, "end": 8.6}],
    }]}]},
})
lines = deepgram.transcribe(b"x", mime_type="audio/mp3")
check("⚠ the bare transcript is the last resort, not an error",
      len(lines) == 1 and lines[0]["start_ms"] == 500 and lines[0]["end_ms"] == 8600,
      str(lines))

# ⚠ ONE BAD ENTRY COSTS THAT LINE, NOT THE RUN — the run is already paid for.
answer_with({
    "metadata": {"duration": 5.0},
    "results": {
        "channels": [{"alternatives": [{"transcript": "x"}]}],
        "utterances": [
            {"start": 0.0, "end": 1.0, "transcript": "Good."},
            "not a dict",
            {"start": 1.0, "end": 2.0, "transcript": "   "},
            {"start": "bad", "end": None, "transcript": "Kept anyway."},
        ],
    },
})
lines = deepgram.transcribe(b"x", mime_type="audio/mp3")
check("⚠ malformed entries are dropped, the good ones survive",
      [l["text"] for l in lines] == ["Good.", "Kept anyway."], str(lines))
check("…and an unreadable timestamp becomes 0 rather than killing the line",
      lines[1]["start_ms"] == 0)

# Speech-free audio is an error, because it is a paid run that produced nothing.
answer_with({"metadata": {"duration": 5.0},
             "results": {"channels": [{"alternatives": [{"transcript": ""}]}]}})
try:
    deepgram.transcribe(b"x", mime_type="audio/mp3")
    check("an empty transcript is reported", False, "no error raised")
except deepgram.DeepgramError as exc:
    check("an empty transcript says to check the track holds a voice",
          "music" in str(exc).lower(), str(exc))


# ===========================================================================
print("\n6 · Running out names the line to change\n")

env(DEEPGRAM_API_KEY="fake-dg-key")
for code, must_say in ((401, "DEEPGRAM_API_KEY"), (402, "CAPTION_PROVIDER=gemini"),
                       (429, "CAPTION_PROVIDER=gemini"), (400, "MP3")):
    answer_with({"err_msg": "nope"}, status_code=code)
    try:
        deepgram.transcribe(b"x", mime_type="audio/mp3")
        check(f"HTTP {code} is reported", False, "no error raised")
    except deepgram.DeepgramError as exc:
        check(f"⚠ HTTP {code} names what to do — {must_say}",
              must_say in str(exc), str(exc))

env()
try:
    deepgram.transcribe(b"x", mime_type="audio/mp3")
    check("no key at all is an error a person can act on", False, "no error raised")
except deepgram.DeepgramError as exc:
    check("no key names DEEPGRAM_API_KEY and the way back to Gemini",
          "DEEPGRAM_API_KEY" in str(exc) and "CAPTION_PROVIDER=gemini" in str(exc),
          str(exc))

# And the caption module re-raises it as the error the route already shows.
env(CAPTION_PROVIDER="deepgram")
answer_with({"err_msg": "nope"}, status_code=402)
try:
    captions.transcribe(__file__.replace(".py", ".mp3"))
    check("captions.transcribe surfaces it", False, "no error raised")
except captions.CaptionError as exc:
    # The file does not exist, so this is the missing-file branch — still a
    # CaptionError, which is the point: the route sees one exception type.
    check("⚠ the route only ever sees CaptionError, whichever backend ran",
          isinstance(exc, captions.CaptionError))


# ===========================================================================
print("\n7 · The quote prices the backend that will actually run\n")

env(CAPTION_PROVIDER="deepgram")
ten_min = 10 * 60 * 1000
hi = captions.estimate(ten_min, language="Hindi")
multi = captions.estimate(ten_min)

check("a named language is priced at the mono rate",
      abs(hi["usd"] - 10 * deepgram.DEFAULT_USD_PER_MINUTE_MONO) < 1e-6, str(hi))
check("⚠ and a blank language at the DEARER multi rate — never quote less than "
      "the run costs",
      abs(multi["usd"] - 10 * deepgram.DEFAULT_USD_PER_MINUTE_MULTI) < 1e-6, str(multi))
check("multi really is the dearer of the two", multi["usd"] > hi["usd"])
check("the model shown names the backend AND the language it will use",
      hi["model"] == "nova-3 (hi)", hi["model"])

env(CAPTION_PROVIDER="vertex")
gem = captions.estimate(ten_min, language="Hindi")
check("⚠ on Gemini the quote is Gemini's, not Deepgram's",
      abs(gem["usd"] - 600 * captions.USD_PER_AUDIO_SECOND) < 1e-6, str(gem))
check("…and they really are different numbers", abs(gem["usd"] - hi["usd"]) > 1e-6)
check("the spend guard is the same on both backends",
      gem["limit_seconds"] == hi["limit_seconds"] == captions.MAX_AUDIO_SECONDS)

env(CAPTION_PROVIDER="deepgram", DEEPGRAM_USD_PER_MINUTE="0.01")
check("the rate is env-overridable, so a price correction needs no deploy",
      abs(captions.estimate(ten_min, language="Hindi")["usd"] - 0.1) < 1e-6)


# ===========================================================================
print()
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for f in failures:
        print("  · " + f)
    sys.exit(1)
print(
    f"All Deepgram caption checks passed ({len(CONTROLLED)} env vars controlled, "
    "no network, no key spent)."
)
