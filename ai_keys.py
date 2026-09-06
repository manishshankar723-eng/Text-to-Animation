"""ai_keys.py — WHICH KEY PAYS FOR WHICH KIND OF WORK. One table, six names.

Six things in this app cost money at somebody else's meter, and until now four of
them were billed through ONE key. `GEMINI_API_KEY` drew the panels, rendered the
Veo clips, broke down the script, read the dialogue aloud and wrote the
subtitles — so "what did the storyboards cost this month" was a question nobody
could answer, and a key that ran out took every one of them down together.

    image     the drawings — cast sheets, props, storyboard panels, redraws
    video     Veo. ⚠ THE ONLY PER-SECOND METER IN THE APP
    text      the words — breakdown, plan, script, Director, board audit, reframe
    chat      the ✨ AI Editor's conversation (per MESSAGE, on someone's plan)
    voice     the voiceover — dialogue read aloud (per character)
    caption   auto-captions — audio transcribed (per audio second)

Each owns two env vars, and the prefix is the whole mapping:

    <PREFIX>_PROVIDER    vertex | gemini      which backend answers
    GEMINI_KEY_<PREFIX>  a Developer API key  who pays, when that backend is gemini

---------------------------------------------------------------------------
⚠ THE KEY IS ALSO THE SWITCH.
---------------------------------------------------------------------------
Setting `GEMINI_KEY_VOICE` moves the voiceover to the Developer API on its own.
This is not a convenience — it is the fix for a specific shape of bug, which
`script_breakdown._gemini_key` already states and this module now enforces for
every capability: a `.env` where pasting a key next to a capability's name
changes NOTHING until you also remember a second line reads, from the outside,
as "I set the key and it STILL says 403".

`<PREFIX>_PROVIDER` still wins, so `VOICE_PROVIDER=vertex` moves it back without
anybody having to delete a key they want again.

---------------------------------------------------------------------------
⚠ THIS MODULE IMPORTS NOTHING, AND THAT IS LOAD-BEARING.
---------------------------------------------------------------------------
`script_breakdown` imports `gemini_client`; `captions`, `tts` and `autoframe`
import `script_breakdown`; `llm_json` imports `script_breakdown` at call time.
The table has to be readable from every one of those, and from `video_client`,
which sits outside that chain entirely. Anything this file imported from the app
would close a cycle somewhere in that graph. It reads `os.environ` and nothing
else — keep it that way.

⚠ AND IT RESOLVES, IT DOES NOT CONNECT. No `genai.Client` is built here. Each
client factory still owns its own construction, its own cache and its own error
message; this only answers "which provider, and whose key". A second place that
built clients would be a second place that could cache one under the wrong key,
which is the billing bug `script_breakdown.get_client`'s cache key exists to
prevent.

---------------------------------------------------------------------------
⚠ WHAT IS DELIBERATELY *NOT* HERE: AUTOMATIC FALLBACK.
---------------------------------------------------------------------------
When a free provider's quota runs out, this does NOT quietly move the work onto
the shared paid key. It could — that is three lines — and it is the wrong three
lines: a free tier expires silently, so the next thing that happens is weeks of
spend nobody chose, discovered on an invoice. Running out is reported, in words
that name the env var to change, and a person changes it. See `MISSING_KEY_HINT`.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
# ⚠ ONE CAPABILITY, ONE ENV PREFIX, AND THE PREFIX IS THE WHOLE MAPPING. A name
# in here buys `<PREFIX>_PROVIDER` and `GEMINI_KEY_<PREFIX>` with no further
# code. A name NOT in here resolves exactly as it did before this file existed.
#
# ⚠ THE THREE OLD NAMES ARE THE ONES ALREADY IN PEOPLE'S `.env`. `IMAGE_PROVIDER`,
# `TEXT_PROVIDER` and `VIDEO_PROVIDER` predate this table by months and are
# spelled here EXACTLY as they always were, so an existing deployment keeps
# resolving the way it does today and only the new `GEMINI_KEY_*` names are new.
# Renaming any of them to something tidier would be a silent outage on upgrade.
CAPABILITIES: dict[str, str] = {
    "image": "IMAGE",
    "video": "VIDEO",
    "text": "TEXT",
    "chat": "CHAT",
    "voice": "VOICE",
    "caption": "CAPTION",
}

# The shared key, and the fallback every capability lands on when it holds no key
# of its own. `GOOGLE_API_KEY` is the older spelling and stays supported.
SHARED_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def _env(name: str) -> str:
    """`os.environ[name]`, trimmed, or "" — and BLANK COUNTS AS UNSET.

    ⚠ `IMAGE_PROVIDER=` ON ITS OWN LINE USED TO BE A CRASH. `os.environ.get(name,
    "vertex")` returns the empty string for a variable that is present and empty,
    which then failed the supported-provider check with "Unknown IMAGE_PROVIDER
    ''" — a true sentence that reads as a typo in a value nobody typed. A key
    commented out by deleting its value is the ordinary way people switch a
    provider off, so it has to mean "unset", not "invalid".
    """
    return (os.environ.get(name) or "").strip()


def prefix(capability: str = "") -> str:
    """The env prefix this capability owns, or "" when it owns none."""
    return CAPABILITIES.get((capability or "").strip().lower(), "")


def key_env(capability: str = "") -> str:
    """The env var holding this capability's own Gemini key, or "" for none."""
    p = prefix(capability)
    return f"GEMINI_KEY_{p}" if p else ""


def provider_env(capability: str = "") -> str:
    """The env var naming this capability's backend, or "" for none."""
    p = prefix(capability)
    return f"{p}_PROVIDER" if p else ""


def key_envs(capability: str = "") -> tuple[str, ...]:
    """Every env var a Developer API key is looked for in, BEST FIRST.

    The capability's own key wins and the shared one is the fallback, which is
    what lets a deployment that sets only `GEMINI_API_KEY` behave exactly as it
    always did.
    """
    own = key_env(capability)
    return ((own,) if own else ()) + SHARED_KEY_ENVS


def own_provider(capability: str = "") -> str:
    """What this capability's OWN settings say the provider is, or "".

    `<PREFIX>_PROVIDER` first, then the key as a switch — see the header. Returns
    "" when the capability says nothing, so the caller can fall through to
    whatever chain it had before (`TEXT_PROVIDER`, then `vertex`).
    """
    if not prefix(capability):
        return ""
    named = _env(provider_env(capability))
    if named:
        return named.lower()
    return "gemini" if _env(key_env(capability)) else ""


def resolve_provider(
    capability: str = "",
    explicit: str | None = None,
    *,
    fallback: tuple[str, ...] = (),
    default: str = "vertex",
) -> str:
    """The backend that will answer for `capability`, best source first:

        explicit arg  >  <PREFIX>_PROVIDER  >  its own key  >  fallback  >  default

    `fallback` names further env vars to consult, in order — that is how the
    voiceover and the captions inherit `TEXT_PROVIDER` on a deployment that has
    not given them settings of their own.

    ⚠ IT DOES NOT VALIDATE. Each caller has its own `SUPPORTED_PROVIDERS` — the
    text side knows `openai_compatible` and the image side does not — so the
    name is returned as written and rejected where that list lives. A check here
    would have to be the union of every module's, which is the same as no check.
    """
    picked = (explicit or "").strip().lower() or own_provider(capability)
    if not picked:
        for name in fallback:
            picked = _env(name).lower()
            if picked:
                break
    return picked or default


def gemini_key(capability: str = "") -> tuple[str, str]:
    """The Developer API key for `capability`, and THE NAME IT CAME FROM.

    ⚠ THE NAME COMES BACK TOO, AND IT IS THE NAME THAT GETS LOGGED. Which of
    three env vars actually answered is the first thing anyone debugging a 403
    wants to know, and it is the one thing the key itself must never be printed
    to show.
    """
    for name in key_envs(capability):
        value = _env(name)
        if value:
            return value, name
    return "", ""


def missing_key_hint(capability: str = "") -> str:
    """The sentence shown when a `gemini` backend has no key to use.

    ⚠ IT NAMES THE VARIABLE TO EDIT, because that is the whole point of splitting
    the keys: a capability that has run out is one line away from being pointed
    somewhere else, and an error that does not say which line sends the reader to
    grep the codebase instead.
    """
    names = " or ".join(key_envs(capability))
    switch = provider_env(capability)
    hint = f"The Gemini Developer API needs a key: set {names} in your .env."
    if switch:
        hint += f" (Or set {switch}=vertex to use Vertex AI instead.)"
    return hint
