"""chat_provider_check.py — THE ✨ AI EDITOR CHAT RUNS ON ITS OWN KEY, AND ONLY IT DOES.

    python tests/chat_provider_check.py     (no backend, no network, no dollar)

WHY THIS FILE EXISTS. On 2026-09-03 the GCP project's billing was switched off.
Vertex AI then answered every single call with

    403 PERMISSION_DENIED … 'reason': 'CONSUMER_INVALID'

and the editor's chat panel printed that raw JSON at the user. Nothing was wrong
with the chat: one shared switch (`TEXT_PROVIDER`) pointed every text capability
in the app at one lapsed project, so the cheapest conversational call in the
product died alongside the expensive render pipeline that actually needed it.

So the chat was given its own three settings — `CHAT_PROVIDER`, `CHAT_MODEL`,
`GEMINI_KEY_CHAT` — and this file is the guard on the two claims that matter:

  1. THE CHAT FOLLOWS ITS OWN KEY. Pasting `GEMINI_KEY_CHAT` into `.env` moves
     the chat to the Gemini Developer API by itself. ⚠ THE KEY HAS TO BE THE
     SWITCH: a key that changes nothing until you also remember a second line is
     exactly the shape of "I set the key and it STILL says 403".

  2. NOTHING ELSE MOVED. The Director and the shot breakdown must resolve
     EXACTLY as they did before any of this existed. A per-capability override
     that quietly re-pointed the whole app would have traded one outage for a
     more confusing one.

⚠ **THE KEY IS PART OF THE CLIENT CACHE KEY, AND SECTION 4 IS THE ONLY PLACE
THAT IS PROVABLE.** `script_breakdown.get_client` used to cache one client per
provider. Two capabilities on `gemini` with two different keys would then share
whichever client was built first — so the chat would silently bill the media key,
or the reverse. It is a billing bug that looks like nothing at all, because both
keys work and every call succeeds.

⚠ **NO NETWORK AND NO REAL KEY.** `genai.Client(api_key=…)` does not call
anything on construction, so a fake string is enough to prove which env var was
read. What this file cannot tell you is whether a key is VALID — that costs a
call, and `tests/editor_chat_check.py` plus a live turn are where that belongs.

⚠ **AND IT CONTROLS THE ENVIRONMENT RATHER THAN READING YOURS.** Importing
`script_breakdown` runs `load_dotenv()`, so the developer's own `.env` is in
`os.environ` by the time the first assertion runs. Every variable this file
depends on is therefore set or deleted explicitly below — otherwise the suite
would pass or fail according to whose laptop it ran on.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import llm_json  # noqa: E402
import script_breakdown  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


# Every variable that can change an answer here, owned by this file for the rest
# of the run. ⚠ THE CLIENT CACHE IS EMPTIED TOO: it is module state, and a client
# built from the developer's real key before this file started would be handed
# back to section 4 as if it had proved something.
CONTROLLED = (
    "TEXT_PROVIDER", "DIRECTOR_PROVIDER", "DIRECTOR_MODEL",
    "CHAT_PROVIDER", "CHAT_MODEL",
    "GEMINI_KEY_CHAT", "GEMINI_KEY_MEDIA", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "GEMINI_TEXT_MODEL", "VERTEX_TEXT_MODEL",
)


def env(**values) -> None:
    """Set exactly these, clear every other controlled variable."""
    for name in CONTROLLED:
        os.environ.pop(name, None)
    for name, value in values.items():
        os.environ[name] = value
    script_breakdown._clients.clear()


# ===========================================================================
print("\n1 · A shared .env, untouched — the chat is wherever the app is\n")

env(TEXT_PROVIDER="vertex")
check("the chat is on Vertex when nothing names it",
      llm_json.resolve_provider(capability="chat") == "vertex")
check("…and so is everything else", llm_json.resolve_provider() == "vertex")
check("a capability with no prefix has no key of its own",
      llm_json.capability_key_env("media") == "")
check("…and neither has an empty one", llm_json.capability_key_env("") == "")


# ===========================================================================
print("\n2 · The key IS the switch — one line in .env moves the chat\n")

env(TEXT_PROVIDER="vertex", GEMINI_KEY_CHAT="fake-chat-key")
check("the chat's own key names its env var",
      llm_json.capability_key_env("chat") == "GEMINI_KEY_CHAT")
check("⚠ the chat moved to the Developer API on the key alone",
      llm_json.resolve_provider(capability="chat") == "gemini",
      llm_json.resolve_provider(capability="chat"))

# ⚠ THE WHOLE POINT OF THE EXERCISE. If this line ever goes red, an outage in one
# capability is an outage in all of them again.
check("⚠ …and the Director did NOT move with it",
      llm_json.resolve_provider() == "vertex")
check("⚠ …nor did the shot breakdown",
      script_breakdown.text_provider() == "vertex")


# ===========================================================================
print("\n3 · CHAT_PROVIDER outranks the key, so a rollback deletes nothing\n")

env(TEXT_PROVIDER="vertex", GEMINI_KEY_CHAT="fake-chat-key", CHAT_PROVIDER="vertex")
check("CHAT_PROVIDER=vertex wins over a key that is still there",
      llm_json.resolve_provider(capability="chat") == "vertex")

env(TEXT_PROVIDER="gemini", GEMINI_API_KEY="fake-shared-key", CHAT_PROVIDER="stub")
check("a capability may name stub while the app is live",
      llm_json.resolve_provider(capability="chat") == "stub")
check("…and TEXT_PROVIDER still decides the rest",
      llm_json.resolve_provider() == "gemini")

env(TEXT_PROVIDER="vertex", CHAT_PROVIDER="nonsense")
try:
    llm_json.resolve_provider(capability="chat")
    check("a typo in CHAT_PROVIDER is refused", False, "no error raised")
except llm_json.LLMJsonError as e:
    # ⚠ THE MESSAGE MUST NAME THE VARIABLE THE READER ACTUALLY SET. Told
    # "unknown DIRECTOR_PROVIDER", they go and read a line they never touched.
    check("a typo in CHAT_PROVIDER is refused, and names CHAT_PROVIDER",
          "CHAT_PROVIDER" in str(e), str(e))


# ===========================================================================
print("\n4 · Two capabilities on gemini are two clients, not one\n")

env(GEMINI_KEY_CHAT="fake-chat-key", GEMINI_API_KEY="fake-shared-key")
check("the chat's key is found first when it has one",
      script_breakdown._gemini_key("GEMINI_KEY_CHAT")[1] == "GEMINI_KEY_CHAT")
check("…and the shared key is the fallback for everyone else",
      script_breakdown._gemini_key()[1] == "GEMINI_API_KEY")

chat_client = script_breakdown.get_client("gemini", key_env="GEMINI_KEY_CHAT")
shared_client = script_breakdown.get_client("gemini")
check("⚠ two keys build two clients — the cache is keyed on both",
      chat_client is not shared_client)
check("…and asking twice still reuses one",
      script_breakdown.get_client("gemini", key_env="GEMINI_KEY_CHAT") is chat_client)

env(GEMINI_API_KEY="fake-shared-key")
check("a capability with no key of its own falls back rather than failing",
      script_breakdown._gemini_key("GEMINI_KEY_CHAT")[1] == "GEMINI_API_KEY")

env(TEXT_PROVIDER="gemini")
try:
    script_breakdown.get_client("gemini", key_env="GEMINI_KEY_CHAT")
    check("no key at all is an error a person can act on", False, "no error raised")
except RuntimeError as e:
    # The old message named only GEMINI_API_KEY, which sent the reader to the
    # wrong line the moment a capability had a key of its own.
    check("no key at all is an error naming every var that would have worked",
          "GEMINI_KEY_CHAT" in str(e) and "GEMINI_API_KEY" in str(e), str(e))


# ===========================================================================
print("\n5 · The model: the chat's own, then the Director's, then the text one\n")

# ⚠ WHY THE TWO BACKENDS DEFAULT DIFFERENTLY. A Developer API key minted today
# is refused by gemini-2.5-flash — "no longer available to new users" — as an
# HTTP 404, which reads like a wrong model name rather than an aged default.
env(TEXT_PROVIDER="vertex", GEMINI_KEY_CHAT="fake-chat-key")
check("the chat gets the Developer API's default, not Vertex's",
      llm_json.model_id(capability="chat") == script_breakdown.DEFAULT_GEMINI_TEXT_MODEL,
      llm_json.model_id(capability="chat"))
check("…and the Director still gets Vertex's",
      llm_json.model_id() == script_breakdown.DEFAULT_TEXT_MODEL,
      llm_json.model_id())
check("the two defaults really are different strings",
      script_breakdown.DEFAULT_GEMINI_TEXT_MODEL != script_breakdown.DEFAULT_TEXT_MODEL)

env(TEXT_PROVIDER="vertex", GEMINI_KEY_CHAT="fake-chat-key", CHAT_MODEL="pinned-chat-model")
check("CHAT_MODEL pins the chat", llm_json.model_id(capability="chat") == "pinned-chat-model")
check("…and leaves the Director alone", llm_json.model_id() != "pinned-chat-model")

env(TEXT_PROVIDER="vertex", GEMINI_KEY_CHAT="fake-chat-key", DIRECTOR_MODEL="pinned-director-model")
check("DIRECTOR_MODEL still applies when the chat pins nothing",
      llm_json.model_id(capability="chat") == "pinned-director-model")

env(TEXT_PROVIDER="vertex", GEMINI_KEY_CHAT="fake-chat-key",
    CHAT_MODEL="pinned-chat-model", DIRECTOR_MODEL="pinned-director-model")
check("…but the chat's own pin outranks it",
      llm_json.model_id(capability="chat") == "pinned-chat-model")


# ===========================================================================
print("\n6 · The request carries the capability, and the machinery reads it\n")

env(TEXT_PROVIDER="vertex", GEMINI_KEY_CHAT="fake-chat-key")

import editor_chat_agent  # noqa: E402

check("the agent's capability is a key of the table",
      editor_chat_agent.CAPABILITY in llm_json.CAPABILITIES,
      f"{editor_chat_agent.CAPABILITY!r} vs {sorted(llm_json.CAPABILITIES)}")

base = dict(system="s", prompt="p", schema={"type": "object"})
plain = llm_json.JsonRequest(**base)
chatty = llm_json.JsonRequest(**base, capability=editor_chat_agent.CAPABILITY)
check("a request with no capability is the shared settings", plain.capability == "")

# ⚠ THE SAME BRIEF ANSWERED ON TWO KEYS IS THE SAME BRIEF. `fingerprint()` is the
# determinism claim — it covers the bytes SENT, and whose credentials carried
# them is not one of those bytes. Folding the capability in would have made
# `tests/director_determinism_check.py` fail for a reason about billing.
check("⚠ the fingerprint ignores it — credentials are not part of the brief",
      plain.fingerprint() == chatty.fingerprint())

check("the schema still travels natively for the chat",
      llm_json.schema_in_prompt(capability="chat") is False)
check("the chat picks the Google adapter, not the OpenAI one",
      llm_json._adapter("chat") is llm_json._google_adapter)

# ⚠ AND THE ADAPTER CHOICE HAS TO FOLLOW THE CAPABILITY TOO. `vertex` and
# `gemini` share `_google_adapter`, so reading the global switch here was
# harmless right up until a capability could name a different WIRE FORMAT.
env(TEXT_PROVIDER="vertex", CHAT_PROVIDER="openai_compatible")
check("⚠ a capability that names another wire format gets that adapter",
      llm_json._adapter("chat") is llm_json._openai_adapter)
check("…while the Director stays on Google",
      llm_json._adapter() is llm_json._google_adapter)

env(TEXT_PROVIDER="vertex", CHAT_PROVIDER="stub")
check("stub is reachable per capability as well",
      llm_json._adapter("chat") is llm_json._stub_adapter)


# ===========================================================================
print("\n7 · The router reports the backend that actually answered\n")

server_src = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "server", "editor_chat.py"),
    encoding="utf-8",
).read()

# ⚠ A BARE `resolve_provider()` HERE IS A BUG THAT ONLY SHOWS IN A BUG REPORT.
# The turn runs on the chat's settings and the response would have named the
# app's — a debugging trail pointing at the one backend not involved.
check("the turn's provider is resolved WITH the capability",
      "resolve_provider(capability=CAPABILITY)" in server_src)
check("…and so is the model it reports",
      "model_id(capability=CAPABILITY)" in server_src)
check("…and the name comes from the agent, not a second copy of the string",
      "from editor_chat_agent import CAPABILITY" in server_src)


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures
              else f"All chat-provider checks passed ({len(CONTROLLED)} env vars controlled, "
                   "no network, no key spent)."))
sys.exit(1 if failures else 0)
