"""Contract checks for Plan & Script — the conversational content planner.

The model call is STUBBED, so this spends no AI quota and needs no network
beyond MongoDB. It covers the parts that would actually hurt: transcript
persistence, owner isolation, the "never invent channel data" rule, export
integrity, and that a failed reply doesn't corrupt the conversation.

    python tests/plan_check.py
"""

import io
import os
import sys
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE ANY `server.*`
# IMPORT.** `server/config.py` reads the environment once, at import time, so
# without this line the suite boots against the developer's real `.env` — it
# registers its test accounts in the production database and spends real monthly
# quota, and then fails when billing refuses it. G13; see `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("plan_check_")

from fastapi.testclient import TestClient

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


# --- Stub the agent BEFORE the app imports it lazily -------------------------
import plan_agent

FAKE_PLAN = {
    "summary": "Lean into Purana explainers.",
    "pillars": [{"name": "Purana explainers", "why": "Best performer"}],
    "items": [
        {"slot": "Week 1 · Tue", "title": "The accidental worshipper",
         "hook": "He never meant to pray.", "format": "Short (45s)",
         "pillar": "Purana explainers", "outline": ["Hook", "Tree", "Reveal"],
         "keywords": ["shiva", "purana"], "cta": "Follow", "goal": "reach",
         "effort": "low"},
        {"slot": "Week 1 · Fri", "title": "Why Sawan Mondays matter",
         "hook": "Everyone fasts. Nobody knows why.", "format": "Long-form",
         "pillar": "Purana explainers", "outline": ["Question", "History"],
         "keywords": ["sawan"], "cta": "Comment", "goal": "engagement",
         "effort": "high"},
    ],
    "assumptions": ["Assumed you edit your own videos"],
    "months": 3,
    "cadence": "2 per week",
}

chat_calls: list[dict] = []
fail_next_chat = {"on": False}


FAKE_QUESTIONS = [
    {
        "id": "q1",
        "header": "Cadence",
        "question": "How often can you realistically publish?",
        "options": [
            {"label": "2 per week (Recommended)", "description": "One long, one short."},
            {"label": "1 per week", "description": "Sustainable solo."},
        ],
    }
]


def fake_chat(messages, channel_context=""):
    chat_calls.append({"messages": list(messages), "context": channel_context})
    if fail_next_chat["on"]:
        raise plan_agent.PlanError("simulated model failure")
    return {
        "reply": f"Reply #{len(chat_calls)} (ctx={'yes' if channel_context else 'no'})",
        "questions": [dict(q) for q in FAKE_QUESTIONS],
    }


generate_calls: list[dict] = []


def fake_generate(messages, months=1, cadence="", channel_context="", language=""):
    generate_calls.append({"months": months, "cadence": cadence, "language": language})
    return {
        **FAKE_PLAN,
        "months": months,
        "cadence": cadence or "2 per week",
        "language": language or "english",
    }


plan_agent.chat = fake_chat
plan_agent.generate_plan = fake_generate

# --- Stub the channel PAGE READ too ------------------------------------------
# research_channel() falls back to Gemini's url_context tool when there's no
# YouTube API key, which is a real model call over the network. This suite is
# about the plan lifecycle, so that is stubbed out: it kept the run neither
# free nor deterministic (it depends on a third party's page being reachable).
import youtube_research

youtube_research._read_with_gemini = lambda url: None

from server import config
from server.jobs import get_store
from server.main import app
from server.mongo import get_db
from server.schemas import JobKind

client = TestClient(app)
store = get_store()
emails: list[str] = []
made: list[str] = []


def new_user():
    email = f"_plan_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "plan-pass-12345"})
    assert r.status_code == 201, r.text
    emails.append(email)
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


email, auth = new_user()

print("\n[1] create a session")
r = client.post("/plans", headers=auth, json={})
check("POST /plans -> 201", r.status_code, 201)
pid = r.json()["job_id"]
made.append(pid)
check("starts with no messages", r.json()["messages"], [])
check("starts with no plan", r.json()["plan"], {})
job = store.get(pid)
check("stored as a PLAN job", job.kind, JobKind.PLAN)
check("owned by the caller", job.owner, email)

print("\n[2] a plan does NOT show up in other workflows' libraries")
check("storyboard library unaffected",
      [b for b in client.get("/storyboards", headers=auth).json() if b["job_id"] == pid], [])
check("plan library has it",
      any(p["job_id"] == pid for p in client.get("/plans", headers=auth).json()), True)

print("\n[3] chat persists BOTH sides of the conversation")
r = client.post(f"/plans/{pid}/chat", headers=auth,
                json={"message": "I run a mythology channel, plan me 3 months"})
check("chat -> 200", r.status_code, 200)
msgs = r.json()["messages"]
check("two messages stored", len(msgs), 2)
check("first is the user's", msgs[0]["role"], "user")
check("second is the agent's", msgs[1]["role"], "agent")
check("timestamps recorded", bool(msgs[0]["at"] and msgs[1]["at"]), True)
# The title is a NAME, not a transcript of the opening line: filler stripped,
# a few words about the subject. "I run a mythology channel, plan me 3 months"
# must not land on a card as a whole sentence.
title = r.json()["title"]
check("auto-title names the subject", title, "Mythology channel")
check("auto-title is short", len(title) <= 42, True)
check("auto-title drops the request filler", title.lower().startswith("i run"), False)

from server.plans import MAX_TITLE_CHARS, _short_title

for opening, want_max in [
    ("I run a YouTube channel about mythology. Plan my next 3 months.", MAX_TITLE_CHARS),
    ("Hi, can you please plan my next 6 months of shorts for my cooking channel?", MAX_TITLE_CHARS),
    ("A really extremely long opening sentence that keeps going and going forever", MAX_TITLE_CHARS),
]:
    got = _short_title(opening)
    check(f"'{opening[:28]}…' -> short", len(got) <= want_max + 1, True)
check("empty message still yields a name", _short_title(""), "Untitled plan")

print("\n[3b] clickable questions ride along on the agent's turn")
msgs = r.json()["messages"]
agent_turn = msgs[-1]
check("agent turn carries questions", len(agent_turn["questions"]), 1)
check("question has a tab header", agent_turn["questions"][0]["header"], "Cadence")
check("options came through", len(agent_turn["questions"][0]["options"]), 2)
check("option has a description",
      bool(agent_turn["questions"][0]["options"][0]["description"]), True)
check("user turns carry none", msgs[-2]["questions"], [])
check("questions persist across a reload",
      len(client.get(f"/plans/{pid}", headers=auth).json()["messages"][-1]["questions"]), 1)

# The model's shape is never trusted directly — malformed questions must be
# dropped rather than rendered as a broken panel.
from plan_agent import MAX_OPTIONS, MAX_QUESTIONS, _coerce_questions

check("a question with one option is dropped",
      _coerce_questions([{"question": "Pick", "options": [{"label": "only"}]}]), [])
check("a question with no text is dropped",
      _coerce_questions([{"question": "  ", "options": [{"label": "a"}, {"label": "b"}]}]), [])
check("duplicate options are deduped",
      len(_coerce_questions([{"question": "Q", "options": [
          {"label": "Same"}, {"label": "same"}, {"label": "Other"}]}])[0]["options"]), 2)
check("too many questions are capped",
      len(_coerce_questions([{"question": f"Q{i}", "options": [{"label": "a"}, {"label": "b"}]}
                             for i in range(9)])), MAX_QUESTIONS)
check("too many options are capped",
      len(_coerce_questions([{"question": "Q", "options": [
          {"label": f"opt{i}"} for i in range(9)]}])[0]["options"]), MAX_OPTIONS)
check("bare strings work as options",
      len(_coerce_questions([{"question": "Q", "options": ["one", "two"]}])[0]["options"]), 2)
check("junk input yields no questions", _coerce_questions("not a list"), [])

r = client.post(f"/plans/{pid}/chat", headers=auth, json={"message": "Twice a week"})
check("history grows", len(r.json()["messages"]), 4)
check("agent received the whole transcript", len(chat_calls[-1]["messages"]), 3)

print("\n[4] a failed reply must not corrupt the transcript")
before = len(client.get(f"/plans/{pid}", headers=auth).json()["messages"])
fail_next_chat["on"] = True
r = client.post(f"/plans/{pid}/chat", headers=auth, json={"message": "this will fail"})
fail_next_chat["on"] = False
check("failure reported as 502", r.status_code, 502)
after = client.get(f"/plans/{pid}", headers=auth).json()["messages"]
check("unanswered message NOT saved", len(after), before)
check("last message is still the agent's", after[-1]["role"], "agent")

print("\n[5] channel research NEVER invents data — whatever the source")
from youtube_research import as_context

# Every source is checked here rather than only the one this machine happens to
# have configured: the whole point is that NO path may produce a number it
# didn't see.
no_data = {"available": False, "reason": "nothing worked"}
ctx = as_context(no_data)
check("no data: agent told it has none", "NO CHANNEL DATA IS AVAILABLE" in ctx, True)
check("no data: forbidden from estimating", "must NOT state or estimate" in ctx, True)

page_read = {
    "available": True, "source": "gemini_url_context", "title": "MSK Bhakti Sagar",
    "description": "Devotional stories and bhajans.",
    "recent_videos": [{"title": "Hanuman Chalisa"}, {"title": "Shiv katha"}],
}
ctx = as_context(page_read)
check("page read: titles handed over", "Hanuman Chalisa" in ctx, True)
check("page read: says the titles are real", "read directly from the channel page" in ctx, True)
check("page read: forbids inventing counts", "do not state, estimate or imply any" in ctx, True)
check("page read: claims no subscriber figure", "Subscribers:" not in ctx, True)

api_read = {
    "available": True, "source": "youtube_api", "title": "MSK Bhakti Sagar",
    "subscribers": 12345, "total_views": 999, "video_count": 42,
    "recent_videos": [], "top_videos": [],
}
ctx = as_context(api_read)
check("api read: exact figures included", "12,345" in ctx, True)
check("api read: marked as real", "fetched from the YouTube Data API" in ctx, True)

# The live endpoint. It may reach the page (no key needed) or not, depending on
# the network — both are valid, so assert the INVARIANT rather than the outcome.
r = client.post(f"/plans/{pid}/channel", headers=auth,
                json={"url": "https://youtube.com/@somechannel"})
check("channel endpoint -> 200 either way", r.status_code, 200)
ch = r.json()["channel"]
check("result records its source or its reason",
      bool(ch.get("source") or ch.get("reason")), True)
if ch.get("available"):
    print(f"      (live read via {ch.get('source')})")
    check("a page read never carries a subscriber count",
          ch.get("source") != "gemini_url_context" or "subscribers" not in ch, True)
else:
    print(f"      (not reachable: {ch.get('reason', '')[:60]})")

print("\n[6] channel link parsing")
from youtube_research import parse_channel_ref

check("@handle url", parse_channel_ref("https://youtube.com/@mychan"), {"handle": "mychan"})
check("channel id url", parse_channel_ref("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"),
      {"channel_id": "UCabcdefghijklmnopqrstuv"})
check("legacy /c/ url", parse_channel_ref("youtube.com/c/OldName"), {"search": "OldName"})
check("bare @handle", parse_channel_ref("@justme"), {"handle": "justme"})
check("bare channel id", parse_channel_ref("UCabcdefghijklmnopqrstuv"),
      {"channel_id": "UCabcdefghijklmnopqrstuv"})
check("empty -> None", parse_channel_ref(""), None)

print("\n[7] generate a calendar")
r = client.post(f"/plans/{pid}/generate", headers=auth, json={"months": 3, "cadence": "2 per week"})
check("generate -> 200", r.status_code, 200)
plan = r.json()["plan"]
check("items returned", len(plan["items"]), 2)
check("months carried through", plan["months"], 3)
check("assumptions surfaced", len(plan["assumptions"]), 1)
check("plan persisted", len(store.get(pid).params["plan"]["items"]), 2)

print("\n[7b] the plan is written in the language the user picks")
from plan_agent import DEFAULT_LANGUAGE, LANGUAGES, language_instruction

r = client.post(f"/plans/{pid}/generate", headers=auth,
                json={"months": 1, "cadence": "2 per week", "language": "hinglish"})
check("generate with a language -> 200", r.status_code, 200)
check("language reached the agent", generate_calls[-1]["language"], "hinglish")
check("language stored on the plan", r.json()["plan"]["language"], "hinglish")

r = client.post(f"/plans/{pid}/generate", headers=auth,
                json={"months": 1, "cadence": "2 per week", "language": "Tamil"})
check("a typed language passes through", generate_calls[-1]["language"], "Tamil")

r = client.post(f"/plans/{pid}/generate", headers=auth, json={"months": 1})
check("no language -> defaults to english", r.json()["plan"]["language"], DEFAULT_LANGUAGE)

# The instruction is what actually makes the model comply, so assert its content
# rather than just that a string came back.
hing = language_instruction("hinglish")
check("hinglish demands LATIN script", "LATIN" in hing, True)
check("hinglish rules out Devanagari", "NOT Devanagari" in hing, True)
check("hindi asks for Devanagari", "Devanagari" in language_instruction("hindi"), True)
check("an unknown language is used verbatim", "Bhojpuri" in language_instruction("Bhojpuri"), True)
check("empty language adds no instruction", language_instruction(""), "")
for field in ("goal", "effort"):
    check(f"`{field}` is protected from translation", field in hing, True)
check("three presets offered", sorted(LANGUAGES), ["english", "hindi", "hinglish"])

print("\n[8] exports")
for fmt, is_zip in (("xlsx", True), ("docx", True), ("csv", False)):
    r = client.get(f"/plans/{pid}/export", headers=auth, params={"format": fmt})
    check(f"{fmt} -> 200", r.status_code, 200)
    check(f"{fmt} has content", len(r.content) > 300, True)
    check(f"{fmt} filename offered",
          "attachment" in r.headers.get("content-disposition", ""), True)
    if is_zip:
        check(f"{fmt} is a valid office file", zipfile.is_zipfile(io.BytesIO(r.content)), True)
    else:
        check("csv contains a title", b"accidental worshipper" in r.content, True)
check("bad format rejected",
      client.get(f"/plans/{pid}/export", headers=auth, params={"format": "pdf"}).status_code, 422)

print("\n[9] export before generating is refused, not empty")
r2 = client.post("/plans", headers=auth, json={"title": "Empty one"})
empty_id = r2.json()["job_id"]
made.append(empty_id)
check("export -> 409", client.get(f"/plans/{empty_id}/export", headers=auth).status_code, 409)

print("\n[10] rename / delete")
check("rename -> 200",
      client.patch(f"/plans/{pid}", headers=auth, json={"title": "Q1 mythology"}).status_code, 200)
check("name applied", client.get(f"/plans/{pid}", headers=auth).json()["title"], "Q1 mythology")
check("blank name rejected",
      client.patch(f"/plans/{pid}", headers=auth, json={"title": "   "}).status_code, 400)
check("delete -> 204", client.delete(f"/plans/{empty_id}", headers=auth).status_code, 204)
check("it is gone", store.get(empty_id), None)
made.remove(empty_id)

print("\n[11] plans are private to their owner")
other_email, other_auth = new_user()
check("stranger's library is empty", client.get("/plans", headers=other_auth).json(), [])
check("stranger cannot read mine", client.get(f"/plans/{pid}", headers=other_auth).status_code, 404)
check("stranger cannot chat into mine",
      client.post(f"/plans/{pid}/chat", headers=other_auth, json={"message": "hi"}).status_code, 404)
check("stranger cannot export mine",
      client.get(f"/plans/{pid}/export", headers=other_auth).status_code, 404)
check("stranger cannot delete mine",
      client.delete(f"/plans/{pid}", headers=other_auth).status_code, 404)
check("mine survived", client.get(f"/plans/{pid}", headers=auth).status_code, 200)

print("\n[12] auth required")
check("list -> 401", client.get("/plans").status_code, 401)
check("create -> 401", client.post("/plans", json={}).status_code, 401)
check("chat -> 401", client.post(f"/plans/{pid}/chat", json={"message": "x"}).status_code, 401)
check("export -> 401", client.get(f"/plans/{pid}/export").status_code, 401)

print("\n[13] cleanup")
removed = sum(1 for jid in made if store.delete(jid))
db = get_db()
db[config.USERS_COLLECTION].delete_many({"email": {"$in": emails}})
print(f"  removed {removed} session(s)")
check("no plans left for the test users",
      len(store.list(limit=200, owner=email, kinds=[JobKind.PLAN])), 0)

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All Plan & Script checks passed.")
