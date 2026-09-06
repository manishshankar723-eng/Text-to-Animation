"""ai_keys_check.py — SIX KINDS OF SPEND, SIX KEYS, AND NONE OF THEM MOVES THE OTHERS.

    python tests/ai_keys_check.py     (no backend, no network, no dollar)

WHY THIS FILE EXISTS. `tests/chat_provider_check.py` proved the ✨ AI Editor chat
could be billed apart from everything else. It was the first capability to get
that and for months it was the only one: the drawings, Veo, the breakdown, the
voiceover and the captions all still came out of `GEMINI_API_KEY`, so

  * "what did the storyboards cost this month" had no answer, and
  * a key that ran out took all five down at once.

`.env.example` had advertised a `GEMINI_KEY_MEDIA` since August. NOTHING READ IT.
Pasting a key into it changed nothing at all, silently — which is worse than not
offering the line, and is the specific failure this file now guards against for
every capability at once.

⚠ **"THE KEY IS THE SWITCH" IS THE PROPERTY UNDER TEST, NOT A CONVENIENCE.** A
key that needs a second line before it does anything is indistinguishable, from
the outside, from a key that is being ignored — and the reader's next move is to
doubt the key rather than to look for the missing line. Every section below that
says "moved on the key alone" is guarding that.

⚠ **AND ISOLATION IS THE OTHER HALF.** Every "did NOT move" line is the real
point of the exercise. A per-capability override that quietly re-pointed its
neighbours would trade one outage for a more confusing one, and would make the
per-capability bills it exists to produce untrue.

⚠ **NO NETWORK AND NO REAL KEY.** `genai.Client(api_key=…)` does not call
anything on construction, so a fake string is enough to prove which env var was
read. What this cannot tell you is whether a key is VALID — that costs a call.

⚠ **AND IT CONTROLS THE ENVIRONMENT RATHER THAN READING YOURS.** Importing these
modules runs `load_dotenv()`, so the developer's own `.env` is in `os.environ` by
the time the first assertion runs. Every variable this file depends on is set or
deleted explicitly below — otherwise the suite would pass or fail according to
whose laptop it ran on.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ai_keys  # noqa: E402
import captions  # noqa: E402
import gemini_client  # noqa: E402
import llm_json  # noqa: E402
import script_breakdown  # noqa: E402
import tts  # noqa: E402
import video_client  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


# Every variable that can change an answer here, owned by this file for the rest
# of the run. ⚠ THE CLIENT CACHES ARE EMPTIED TOO: they are module state, and a
# client built from the developer's real key before this file started would be
# handed back to section 5 as if it had proved something.
CONTROLLED = (
    "IMAGE_PROVIDER", "VIDEO_PROVIDER", "TEXT_PROVIDER",
    "CHAT_PROVIDER", "VOICE_PROVIDER", "CAPTION_PROVIDER",
    "DIRECTOR_PROVIDER", "DIRECTOR_MODEL", "CHAT_MODEL",
    "GEMINI_KEY_IMAGE", "GEMINI_KEY_VIDEO", "GEMINI_KEY_TEXT",
    "GEMINI_KEY_CHAT", "GEMINI_KEY_VOICE", "GEMINI_KEY_CAPTION",
    "GEMINI_API_KEY", "GOOGLE_API_KEY",
)

CAPS = ("image", "video", "text", "chat", "voice", "caption")


def env(**values) -> None:
    """Set exactly these, clear every other controlled variable."""
    for name in CONTROLLED:
        os.environ.pop(name, None)
    for name, value in values.items():
        os.environ[name] = value
    script_breakdown._clients.clear()
    gemini_client._clients.clear()
    video_client._clients.clear()


# ===========================================================================
print("\n1 · Every capability owns two env vars, and the prefix is the mapping\n")

for cap in CAPS:
    p = ai_keys.prefix(cap)
    check(f"{cap:<8} → {p}_PROVIDER / GEMINI_KEY_{p}",
          ai_keys.provider_env(cap) == f"{p}_PROVIDER"
          and ai_keys.key_env(cap) == f"GEMINI_KEY_{p}")

# ⚠ THE THREE OLD NAMES ARE THE ONES ALREADY IN PEOPLE'S .env. Renaming any of
# them to something tidier would be a silent outage on upgrade — the app would
# read a variable nobody has set and fall back to its default.
check("⚠ IMAGE_PROVIDER is still spelled IMAGE_PROVIDER",
      ai_keys.provider_env("image") == "IMAGE_PROVIDER")
check("⚠ …TEXT_PROVIDER still TEXT_PROVIDER",
      ai_keys.provider_env("text") == "TEXT_PROVIDER")
check("⚠ …and VIDEO_PROVIDER still VIDEO_PROVIDER",
      ai_keys.provider_env("video") == "VIDEO_PROVIDER")

check("a name that is not a capability owns nothing",
      ai_keys.key_env("media") == "" and ai_keys.provider_env("media") == "")
check("…and neither does an empty one",
      ai_keys.key_env("") == "" and ai_keys.provider_env("") == "")

# ⚠ ONE TABLE, NOT TWO. `llm_json` used to keep its own copy of this mapping;
# the picture and video clients cannot import that module, so a second copy was
# the drift waiting to happen.
check("⚠ llm_json reads the same table rather than a copy of it",
      llm_json.CAPABILITIES is ai_keys.CAPABILITIES)
check("…so the chat still resolves its own key through it",
      llm_json.capability_key_env("chat") == "GEMINI_KEY_CHAT")


# ===========================================================================
print("\n2 · A shared .env, untouched — everything is wherever the app is\n")

env(TEXT_PROVIDER="vertex", IMAGE_PROVIDER="vertex", GEMINI_API_KEY="fake-shared")
check("the drawings are on Vertex", gemini_client._resolve_provider() == "vertex")
check("Veo is on Vertex", video_client._resolve_provider() == "vertex")
check("the breakdown is on Vertex", script_breakdown.text_provider() == "vertex")
check("the voiceover is on Vertex", tts.resolve_provider() == "vertex")
check("the captions are on Vertex", captions.resolve_provider() == "vertex")
check("the chat is on Vertex", llm_json.resolve_provider(capability="chat") == "vertex")

# ⚠ THE FALLBACK THAT KEEPS AN OLD DEPLOYMENT WORKING. Neither the voiceover nor
# the captions had a switch of their own until now; both rode TEXT_PROVIDER, and
# both must keep doing exactly that when nobody has said otherwise.
env(TEXT_PROVIDER="gemini", GEMINI_API_KEY="fake-shared")
check("⚠ the voiceover still inherits TEXT_PROVIDER when it says nothing",
      tts.resolve_provider() == "gemini")
check("⚠ …and so do the captions",
      captions.resolve_provider() == "gemini")


# ===========================================================================
print("\n3 · The key IS the switch — one line in .env moves ONE capability\n")

MOVES = {
    "image":   lambda: gemini_client._resolve_provider(),
    "video":   lambda: video_client._resolve_provider(),
    "text":    lambda: script_breakdown.text_provider(),
    "voice":   lambda: tts.resolve_provider(),
    "caption": lambda: captions.resolve_provider(),
    "chat":    lambda: llm_json.resolve_provider(capability="chat"),
}
# ⚠ NOTHING IS PINNED HERE, AND THAT IS THE POINT OF THE SECTION. An earlier
# draft of this file set every `<CAP>_PROVIDER=vertex` first and then wondered
# why no key moved anything — which is section 4's property arriving early:
# the named provider OUTRANKS the key. "The key is the switch" is a claim about
# a `.env` that has NOT named a provider for that capability, so this one names
# none and leans on the `vertex` default instead.
BASE: dict[str, str] = {}

for cap, read in MOVES.items():
    env(**{**BASE, ai_keys.key_env(cap): f"fake-{cap}-key"})
    check(f"⚠ {cap} moved to the Developer API on its key alone",
          read() == "gemini", read())
    # ⚠ THE WHOLE POINT OF THE EXERCISE. If any of these goes red, an outage in
    # one capability is an outage in all of them again.
    strays = [other for other, r in MOVES.items() if other != cap and r() != "vertex"]
    check(f"⚠ …and nothing else moved with it ({cap})",
          not strays, f"also moved: {strays}")


# ===========================================================================
print("\n4 · <CAP>_PROVIDER outranks the key, so a rollback deletes nothing\n")

for cap, read in MOVES.items():
    env(**{
        **BASE,
        ai_keys.key_env(cap): f"fake-{cap}-key",
        ai_keys.provider_env(cap): "vertex",
    })
    check(f"{ai_keys.provider_env(cap)}=vertex wins over a key still sitting there",
          read() == "vertex", read())

# ⚠ BLANK COUNTS AS UNSET. `IMAGE_PROVIDER=` on its own line used to raise
# "Unknown IMAGE_PROVIDER ''" — a true sentence about a value nobody typed.
# Emptying a value is how people switch a setting off; it has to mean "unset".
env(IMAGE_PROVIDER="", TEXT_PROVIDER="", VIDEO_PROVIDER="", GEMINI_API_KEY="fake-shared")
try:
    check("⚠ a blank IMAGE_PROVIDER means unset, not invalid",
          gemini_client._resolve_provider() == "vertex")
    check("⚠ …and a blank VIDEO_PROVIDER too",
          video_client._resolve_provider() == "vertex")
    check("⚠ …and a blank TEXT_PROVIDER too",
          script_breakdown.text_provider() == "vertex")
except ValueError as exc:
    check("⚠ a blank provider line means unset, not invalid", False, str(exc))

# A typo is still a typo, and must still name the variable the reader set.
env(IMAGE_PROVIDER="nonsense")
try:
    gemini_client._resolve_provider()
    check("a typo in IMAGE_PROVIDER is refused", False, "no error raised")
except ValueError as exc:
    check("a typo in IMAGE_PROVIDER is refused, and names IMAGE_PROVIDER",
          "IMAGE_PROVIDER" in str(exc), str(exc))


# ===========================================================================
print("\n5 · Six keys are six clients, and the shared key is the fallback\n")

env(
    TEXT_PROVIDER="gemini",
    GEMINI_KEY_TEXT="fake-text-key",
    GEMINI_KEY_CHAT="fake-chat-key",
    GEMINI_KEY_VOICE="fake-voice-key",
    GEMINI_KEY_CAPTION="fake-caption-key",
    GEMINI_API_KEY="fake-shared",
)
built = {
    "text": script_breakdown.get_client("gemini"),
    "chat": script_breakdown.get_client("gemini", key_env="GEMINI_KEY_CHAT"),
    "voice": script_breakdown.get_client("gemini", key_env="GEMINI_KEY_VOICE"),
    "caption": script_breakdown.get_client("gemini", key_env="GEMINI_KEY_CAPTION"),
}
check("⚠ four keys build four clients — the cache is keyed on the key",
      len({id(c) for c in built.values()}) == 4)
check("…and asking twice still reuses one",
      script_breakdown.get_client("gemini", key_env="GEMINI_KEY_VOICE") is built["voice"])

# ⚠ NO key_env MEANS TEXT, NOT "THE SHARED KEY". Everything that reaches the text
# client factory without naming a capability IS text work — the breakdown, the
# planner, the audit, the reframe pass, the research, and the Director.
check("⚠ the text family reads GEMINI_KEY_TEXT when it names no capability",
      script_breakdown._gemini_key(ai_keys.key_env("text"))[1] == "GEMINI_KEY_TEXT")

for cap in CAPS:
    env(GEMINI_API_KEY="fake-shared")
    got = ai_keys.gemini_key(cap)
    check(f"{cap:<8} with no key of its own falls back to the shared one",
          got[1] == "GEMINI_API_KEY")

env(GOOGLE_API_KEY="fake-older-spelling")
check("the older GOOGLE_API_KEY spelling still answers",
      ai_keys.gemini_key("image")[1] == "GOOGLE_API_KEY")

env(GEMINI_KEY_IMAGE="fake-image-key", GEMINI_API_KEY="fake-shared")
check("⚠ a capability's own key beats the shared one",
      ai_keys.gemini_key("image")[1] == "GEMINI_KEY_IMAGE")
check("…while its neighbour still gets the shared one",
      ai_keys.gemini_key("video")[1] == "GEMINI_API_KEY")


# ===========================================================================
print("\n6 · Running out says which line to change\n")

# ⚠ THE ERROR NAMES THE VARIABLE TO EDIT, because that is the whole point of
# splitting the keys: a capability that has run out is one line away from being
# pointed somewhere else, and an error that does not say which line sends the
# reader to grep the codebase instead.
env(IMAGE_PROVIDER="gemini")
try:
    gemini_client.get_client()
    check("no key at all is an error a person can act on", False, "no error raised")
except RuntimeError as exc:
    check("the drawings' error names GEMINI_KEY_IMAGE, the shared key AND the switch",
          all(s in str(exc) for s in
              ("GEMINI_KEY_IMAGE", "GEMINI_API_KEY", "IMAGE_PROVIDER")), str(exc))

env(VIDEO_PROVIDER="gemini")
try:
    video_client.get_client()
    check("Veo with no key is an error a person can act on", False, "no error raised")
except video_client.VideoGenerationError as exc:
    check("Veo's error names GEMINI_KEY_VIDEO, the shared key AND the switch",
          all(s in str(exc) for s in
              ("GEMINI_KEY_VIDEO", "GEMINI_API_KEY", "VIDEO_PROVIDER")), str(exc))
    # The subscription sentence is the one people actually need — a Google AI Pro
    # plan is not API access, and that misunderstanding predates all of this.
    check("…and still says a Pro subscription is not API access",
          "subscription" in str(exc).lower(), str(exc))

env(TEXT_PROVIDER="gemini")
try:
    script_breakdown.get_client()
    check("the breakdown with no key is an error", False, "no error raised")
except RuntimeError as exc:
    check("the breakdown's error names GEMINI_KEY_TEXT and the shared key",
          "GEMINI_KEY_TEXT" in str(exc) and "GEMINI_API_KEY" in str(exc), str(exc))


# ===========================================================================
print()
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for f in failures:
        print("  · " + f)
    sys.exit(1)
print(
    f"All ai-keys checks passed ({len(CONTROLLED)} env vars controlled, "
    f"{len(CAPS)} capabilities, no network, no key spent)."
)
