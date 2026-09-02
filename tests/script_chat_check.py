"""Contract checks for POST /script-chat — the assistant inside the script box.

What it pins down:
  - the route is owner-scoped (no token -> 401),
  - the last message must be the user's, because the route trusts it as the
    turn being answered,
  - `reply` / `script` / `title` come back as the client expects, and `script`
    stays empty on a turn that wasn't a request for one,
  - the form's state (genre, aspect, the script already in the box) actually
    reaches the agent as context — it is the difference between a reply about
    THIS board and a generic one,
  - the turn's tokens land on the account's monthly total.

The MODEL CALL IS STUBBED, so this spends no quota and needs no network beyond
MongoDB. The prompt itself is not tested here — a language model's wording is
not a contract. `build_context` is, and it is checked directly.

    python tests/script_chat_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE ANY `server.*`
# IMPORT.** `server/config.py` reads the environment once, at import time, so
# without this line the suite boots against the developer's real `.env` — it
# registers its test accounts in the production database and spends real monthly
# quota, and then fails when billing refuses it. G13; see `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("script_chat_check_")

from fastapi.testclient import TestClient

from server import config
from server.main import app

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


# --- Stub the paid call ------------------------------------------------------
# The route imports `script_agent` lazily inside the handler, so replacing the
# module's function here is enough — the same trick the breakdown checks use.
import script_agent

seen: list[dict] = []
FAKE_SCRIPT = (
    "THE LAST BUS\n\nLOGLINE: A courier misses the last bus and walks home.\n\n"
    "CAST\nMEERA - 30s, courier.\n\n"
    "SCENE 1. EXT. BUS STOP - NIGHT\nMeera watches the bus pull away.\n"
    "MEERA: Of course.\n"
)

_real_chat = script_agent.chat


def fake_chat(messages, context=""):
    seen.append({"messages": messages, "context": context})
    wants_script = "script" in (messages[-1]["text"] or "").lower()
    return {
        "reply": "Here you go." if wants_script else "Ask me anything.",
        "script": FAKE_SCRIPT if wants_script else "",
        "title": "The Last Bus" if wants_script else "",
        "usage": {"input": 100, "output": 220, "thinking": 0, "total": 320, "calls": 1},
    }


script_agent.chat = fake_chat

client = TestClient(app)
email = f"_chat_{uuid.uuid4().hex[:10]}@example.com"
r = client.post("/auth/register", json={"email": email, "password": "chat-pass-12345"})
assert r.status_code == 201, r.text
auth = {"Authorization": f"Bearer {r.json()['access_token']}"}


print("\n[1] the gate")
check("no token -> 401",
      client.post("/script-chat", json={"messages": [{"role": "user", "text": "hi"}]}).status_code,
      401)
check("empty transcript -> 422",
      client.post("/script-chat", headers=auth, json={"messages": []}).status_code, 422)
check("last message must be the user's -> 400",
      client.post("/script-chat", headers=auth, json={
          "messages": [{"role": "user", "text": "hi"},
                       {"role": "agent", "text": "hello"}]}).status_code, 400)


print("\n[2] an ordinary question")
seen.clear()
r = client.post("/script-chat", headers=auth, json={
    "messages": [{"role": "user", "text": "what makes a good hook?"}]})
check("-> 200", r.status_code, 200)
body = r.json()
check("carries a reply", bool(body["reply"]), True)
check("no script on a non-script turn", body["script"], "")
check("usage came back", body["usage"]["total"], 320)


print("\n[3] the form's state reaches the agent")
seen.clear()
r = client.post("/script-chat", headers=auth, json={
    "messages": [{"role": "user", "text": "hi"},
                 {"role": "agent", "text": "hello"},
                 {"role": "user", "text": "write me a script"}],
    "genre": "mythology",
    "aspect_ratio": "9:16",
    "current_script": "MEERA: I already wrote this bit.",
})
check("-> 200", r.status_code, 200)
ctx = seen[-1]["context"]
check("genre is in the context", "mythology" in ctx, True)
check("frame is in the context", "9:16" in ctx, True)
check("vertical gets its staging note", "phone-first" in ctx, True)
check("the box's current text is in the context", "I already wrote this bit" in ctx, True)
check("whole transcript is forwarded", len(seen[-1]["messages"]), 3)
check("roles survive the trip", seen[-1]["messages"][1]["role"], "agent")


print("\n[4] a script comes back in its OWN field")
body = r.json()
check("script is filled", body["script"].startswith("THE LAST BUS"), True)
check("title is filled", body["title"], "The Last Bus")
check("the reply does NOT repeat the script", "SCENE 1." in body["reply"], False)
check("the script is in the layout the breakdown reads",
      "\nSCENE 1. EXT. BUS STOP - NIGHT\n" in body["script"], True)


print("\n[5] the tokens land on the account")
from server import usage as usage_counters
month = usage_counters.counters(email)
# TWO turns actually reached the model above (the 400 never did), 320 each.
check("monthly text tokens counted", month.get("text_tokens", 0), 640)


print("\n[6] build_context on its own")
empty = script_agent.build_context()
check("nothing set -> empty string", empty, "")
plain = script_agent.build_context(genre="default")
check("'default' genre is not a genre", plain, "")
long_script = "x" * (script_agent.MAX_CONTEXT_SCRIPT_CHARS + 500)
clipped = script_agent.build_context(current_script=long_script)
check("a huge script is truncated, not sent whole",
      len(clipped) < script_agent.MAX_CONTEXT_SCRIPT_CHARS + 400, True)
check("and says so", "truncated" in clipped, True)


print("\n[7] cleanup")
script_agent.chat = _real_chat
from server.mongo import get_db
db = get_db()
removed = db[config.USERS_COLLECTION].delete_many({"email": {"$regex": "^_chat_"}}).deleted_count
# The counter row outlives the account it belongs to, so take it too — a run
# that only deleted the user left a month's worth of fake tokens behind.
counted = db[config.USAGE_COLLECTION].delete_many({"email": {"$regex": "^_chat_"}}).deleted_count
print(f"  removed {removed} test user(s), {counted} usage row(s)")

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All script-chat checks passed.")
