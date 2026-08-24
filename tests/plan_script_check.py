"""Contract checks for the "& Script" half of Plan & Script.

The model call is STUBBED, so this spends no AI quota and needs no network
beyond MongoDB — same arrangement as plan_check.py.

What it actually guards, in rough order of how much it would hurt to get wrong:

  THE FORMAT CONTRACT WITH `script_breakdown.py`. The script this workflow
  writes is the INPUT to the storyboard breakdown, and the breakdown quotes
  lines out of it VERBATIM. So the flattened text is not checked for "looking
  like a script" — every action beat is fed through the breakdown's own
  `_flatten_script` / `_find_span` and must be locatable in the text, which is
  exactly what a shot's `script_excerpt` has to do at run time. That pair is the
  reason this file exists.

  TOKEN ACCOUNTING. The session total must equal the sum of the calls that
  actually happened. A total that drifts from its parts is worse than no total,
  because it is believed.

  THE HANDOFF. "Open in Script to Storyboard" must land the SAME bytes in the
  script draft that the .txt export produces — one format, not two.

  The editorial stance and the safety threshold, asserted as configuration
  rather than by generating anything.

    python tests/plan_script_check.py
"""

import io
import json
import os
import subprocess
import sys
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# Stub the model BEFORE the app imports it lazily
# ---------------------------------------------------------------------------
import plan_agent

# A script shaped exactly the way the real writer is instructed to shape it:
# one beat per line, every visible person NAMED (no pronouns), speech carried
# on its own beat. The assertions below lean on that, so if this fixture drifts
# from _SCRIPT_INSTRUCTION the tests stop testing the real thing.
FAKE_SCENES = [
    {
        "number": 1,
        "heading": "INT. KABIR'S BEDROOM - MORNING",
        "beats": [
            {"type": "action", "character": "",
             "text": "Kabir lies on his back asleep under a heavy quilt."},
            {"type": "action", "character": "",
             "text": "Madanlal stands in the doorway with a rubber slipper raised in his right hand."},
            {"type": "dialogue", "character": "Madanlal", "text": "Seven o'clock ho gaya!"},
            {"type": "action", "character": "",
             "text": "Kabir sits up in bed, rubbing his cheek."},
            {"type": "vo", "character": "NARRATOR", "text": "Every morning starts the same way."},
            {"type": "text", "character": "", "text": "DAY 41"},
        ],
    },
    {
        "number": 2,
        "heading": "EXT. VILLAGE LANE - LATER",
        "beats": [
            {"type": "action", "character": "",
             "text": "Kabir runs down the lane with his shoes in one hand."},
            {"type": "dialogue", "character": "Kabir", "text": "I am not late, I am early for tomorrow."},
        ],
    },
]

FAKE_USAGE = {
    "input": 1200, "output": 800, "thinking": 150, "cached": 0,
    "total": 2000, "calls": 1, "model": "gemini-2.5-flash", "cost_usd": 0.00236,
}

script_calls: list[dict] = []
fail_next_script = {"reason": ""}


def fake_write_script(messages=None, item=None, brief="", seconds=60,
                      language=None, channel_context="", notes=""):
    script_calls.append({
        "item": item, "brief": brief, "seconds": seconds,
        "language": language, "context": channel_context, "notes": notes,
        "messages": list(messages or []),
    })
    if fail_next_script["reason"]:
        raise plan_agent.ScriptError(fail_next_script["reason"])

    script = {
        "title": (item or {}).get("title") or (brief[:40] or "Untitled script"),
        "logline": "A boy oversleeps once too often.",
        "characters": [
            {"name": "Kabir", "description": "a lean 14-year-old South Asian boy in a faded blue vest"},
            {"name": "Madanlal", "description": "his father, 50s, greying moustache, white kurta"},
        ],
        "scenes": [dict(s, beats=[dict(b) for b in s["beats"]]) for s in FAKE_SCENES],
        "cta": "Follow for part two.",
        "rating": "teen",
        "notes": ["A rubber slipper has to exist on set."],
        "seconds": seconds,
        "language": language or "english",
        "usage": dict(FAKE_USAGE),
    }
    # THROUGH THE REAL FLATTENER, never a hand-written string — the point of
    # these tests is the format that function produces.
    script["text"] = plan_agent.script_to_text(script)
    script["spoken_words"] = plan_agent.spoken_words(script)
    script["estimated_seconds"] = round(
        script["spoken_words"] / plan_agent.WORDS_PER_MINUTE * 60
    )
    return script


CHAT_USAGE = {"input": 300, "output": 120, "thinking": 0, "cached": 0,
              "total": 420, "calls": 1, "model": "gemini-2.5-flash", "cost_usd": 0.00039}
PLAN_USAGE = {"input": 900, "output": 2200, "thinking": 400, "cached": 0,
              "total": 3100, "calls": 1, "model": "gemini-2.5-flash", "cost_usd": 0.00577}

FAKE_ITEMS = [
    {"slot": "Week 1 · Tue", "title": "The accidental worshipper",
     "hook": "He never meant to pray.", "format": "YouTube Short (45s)",
     "pillar": "Purana explainers", "outline": ["Hook", "Tree", "Reveal"],
     "keywords": ["shiva"], "cta": "Follow", "goal": "reach", "effort": "low"},
    {"slot": "Week 1 · Fri", "title": "Why Sawan Mondays matter",
     "hook": "Everyone fasts.", "format": "Long-form (8-10 min)",
     "pillar": "Purana explainers", "outline": ["Question"],
     "keywords": ["sawan"], "cta": "Comment", "goal": "engagement", "effort": "high"},
]


def fake_chat(messages, channel_context=""):
    return {"reply": "Tell me more.", "questions": [], "usage": dict(CHAT_USAGE)}


def fake_generate(messages, months=1, cadence="", channel_context="", language=""):
    return {
        "summary": "Lean into Purana explainers.",
        "pillars": [{"name": "Purana explainers", "why": "Best performer"}],
        "items": [dict(i) for i in FAKE_ITEMS],
        "assumptions": [],
        "months": months,
        "cadence": cadence or "2 per week",
        "language": language or "english",
        "usage": dict(PLAN_USAGE),
    }


plan_agent.chat = fake_chat
plan_agent.generate_plan = fake_generate
_real_write_script = plan_agent.write_script
plan_agent.write_script = fake_write_script

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
    email = f"_pscript_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "script-pass-12345"})
    assert r.status_code == 201, r.text
    emails.append(email)
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


email, auth = new_user()

print("\n[1] a session with a calendar to write scripts from")
pid = client.post("/plans", headers=auth, json={}).json()["job_id"]
made.append(pid)
client.post(f"/plans/{pid}/chat", headers=auth, json={"message": "I run a mythology channel"})
r = client.post(f"/plans/{pid}/generate", headers=auth,
                json={"months": 1, "cadence": "2 per week", "language": "hinglish"})
check("generate -> 200", r.status_code, 200)
check("two calendar items", len(r.json()["plan"]["items"]), 2)
check("session starts with no scripts", r.json()["scripts"], [])

print("\n[2] write the script for a calendar row")
r = client.post(f"/plans/{pid}/script", headers=auth, json={"item_index": 0, "seconds": 45})
check("POST /script -> 200", r.status_code, 200)
detail = r.json()
check("one script stored", len(detail["scripts"]), 1)
s0 = detail["scripts"][0]
check("titled from the calendar row", s0["title"], "The accidental worshipper")
check("remembers which row it is for", s0["item_index"], 0)
check("carries the row's slot", s0["item_slot"], "Week 1 · Tue")
check("two scenes", len(s0["scenes"]), 2)
check("has flattened text", bool(s0["text"]), True)
# The calendar was written in Hinglish, so its scripts default to Hinglish —
# a script for a Hinglish plan silently arriving in English is the bug this
# assertion exists for.
check("inherits the plan's language", script_calls[-1]["language"], "hinglish")
check("the row itself reached the writer", script_calls[-1]["item"]["title"],
      "The accidental worshipper")
check("the conversation reached the writer as context",
      len(script_calls[-1]["messages"]) > 0, True)

print("\n[3] the FORMAT CONTRACT with script_breakdown")
# The rules in _SCRIPT_INSTRUCTION exist because the breakdown reads them.
# Assert them against the flattened text the breakdown will actually receive.
import script_breakdown

text = s0["text"]
check("cast block present, before the scenes",
      text.index("CAST") < text.index("SCENE 1"), True)
check("both cast names in the cast block",
      all(n in text.split("SCENE 1")[0] for n in ("Kabir", "Madanlal")), True)
check("scene headings are slug lines", "SCENE 1. INT. KABIR'S BEDROOM - MORNING" in text, True)
check("second scene numbered and headed", "SCENE 2. EXT. VILLAGE LANE - LATER" in text, True)
check("dialogue is NAME: line", "MADANLAL: Seven o'clock ho gaya!" in text, True)
check("voice-over is marked", "NARRATOR (V.O.): Every morning starts the same way." in text, True)
check("on-screen text is labelled", "ON SCREEN: DAY 41" in text, True)
check("call to action travels", "CALL TO ACTION: Follow for part two." in text, True)

# ⚠ THE REAL ONE, and it drives the breakdown's own PUBLIC path rather than
# poking at a helper: `_attach_script_lines` is what runs on every real
# breakdown, and a shot whose quote it cannot resolve gets anchored to nothing
# — a panel silently detached from the words that produced it.
#
# So: pretend the breakdown quoted each action beat as that shot's
# `script_excerpt` (which is exactly what it is instructed to do) and require
# every one of them to come back as an EXACT match, in forward order. "fuzzy"
# is not good enough here — fuzzy means the writer and the reader disagree
# about the words, and both of them are ours.
shots = [
    {"script_excerpt": beat["text"]}
    for scene in s0["scenes"]
    for beat in scene["beats"]
    if beat["type"] == "action"
]
script_breakdown._attach_script_lines(shots, text)
check("every action beat resolves to a passage of the script",
      [i for i, sh in enumerate(shots) if not sh.get("script_line")], [])
check("and every one of them resolves EXACTLY, not fuzzily",
      [sh.get("script_line_match") for sh in shots],
      ["exact"] * len(shots))
check("the resolved passage is the beat itself, not the paragraph round it",
      [sh["script_line"] for sh in shots],
      [beat["text"] for scene in s0["scenes"] for beat in scene["beats"]
       if beat["type"] == "action"])
# Forward order matters: _attach_script_lines carries a cursor, so two shots
# quoting the same words would collapse onto one line.
starts = [sh["script_line_start"] for sh in shots]
check("the quotes move forward through the script", starts, sorted(starts))
check("no two shots landed on the same line", len(set(starts)), len(starts))
# Spoken lines have to survive the round trip too — the breakdown reads speech
# off `NAME: line`, and a script whose dialogue it can't see becomes a silent film.
spoken = [
    {"script_excerpt": beat["text"]}
    for scene in s0["scenes"]
    for beat in scene["beats"]
    if beat["type"] == "dialogue"
]
script_breakdown._attach_script_lines(spoken, text)
check("dialogue is findable in the script too",
      [sh.get("script_line_match") for sh in spoken], ["exact"] * len(spoken))

# One beat per line is what makes a quote land on exactly ONE panel.
scene_block = text.split("SCENE 1.")[1].split("SCENE 2.")[0]
body = [ln for ln in scene_block.splitlines() if ln.strip()][1:]
check("one beat per line in scene 1", len(body), 6)

# No pronoun-only action lines: the breakdown sees one sentence at a time.
action_lines = [
    b["text"] for s in s0["scenes"] for b in s["beats"] if b["type"] == "action"
]
check("every action beat names a character",
      all(any(n in ln for n in ("Kabir", "Madanlal")) for ln in action_lines), True)

print("\n[4] a script that was never on the calendar")
r = client.post(f"/plans/{pid}/script", headers=auth,
                json={"brief": "a 3-minute horror short about a lift that stops on floor 7",
                      "seconds": 180})
check("brief -> 200", r.status_code, 200)
detail = r.json()
check("now two scripts", len(detail["scripts"]), 2)
check("newest first", detail["scripts"][0]["item_index"], None)
check("the brief is kept", detail["scripts"][0]["brief"].startswith("a 3-minute horror"), True)
check("length passed through", script_calls[-1]["seconds"], 180)
brief_id = detail["scripts"][0]["id"]
item_id = s0["id"]
check("ids are distinct", brief_id != item_id, True)

print("\n[5] the two ways it can be asked for wrongly")
check("neither an item nor a brief -> 400",
      client.post(f"/plans/{pid}/script", headers=auth, json={}).status_code, 400)
# A tab left open across a regenerate must not write a script for an upload
# that no longer exists at that index.
check("an item index past the end of the plan -> 409",
      client.post(f"/plans/{pid}/script", headers=auth,
                  json={"item_index": 99}).status_code, 409)

print("\n[6] a failed write leaves the session alone")
before = len(client.get(f"/plans/{pid}", headers=auth).json()["scripts"])
fail_next_script["reason"] = "The provider blocked the request (SAFETY)."
r = client.post(f"/plans/{pid}/script", headers=auth, json={"item_index": 1})
check("failure -> 502", r.status_code, 502)
# The provider's OWN words, not "empty response" — the whole point of
# _block_reason. A user who cannot see why cannot do anything about it.
check("the provider's reason is passed through verbatim",
      "SAFETY" in r.json()["detail"], True)
fail_next_script["reason"] = ""
check("no half-written script was stored",
      len(client.get(f"/plans/{pid}", headers=auth).json()["scripts"]), before)

print("\n[7] token accounting adds up")
detail = client.get(f"/plans/{pid}", headers=auth).json()
usage = detail["usage"]
# 1 chat + 1 generate + 2 scripts. Anything that spends and forgets to record
# makes this fail, which is the only reason the number on screen is believable.
expected_total = CHAT_USAGE["total"] + PLAN_USAGE["total"] + FAKE_USAGE["total"] * 2
expected_calls = 4
check("session total is the sum of the calls", usage["total"], expected_total)
check("call count is the sum", usage["calls"], expected_calls)
check("input adds up", usage["input"],
      CHAT_USAGE["input"] + PLAN_USAGE["input"] + FAKE_USAGE["input"] * 2)
check("thinking adds up", usage["thinking"],
      CHAT_USAGE["thinking"] + PLAN_USAGE["thinking"] + FAKE_USAGE["thinking"] * 2)
check("total is input + output", usage["total"], usage["input"] + usage["output"])
check("a cost is estimated", usage["cost_usd"] is not None, True)
check("the plan keeps its OWN cost too", detail["plan"]["usage"]["total"], PLAN_USAGE["total"])
check("each script keeps its own cost", detail["scripts"][0]["usage"]["total"], FAKE_USAGE["total"])
summary = [p for p in client.get("/plans", headers=auth).json() if p["job_id"] == pid][0]
check("the library card carries the token total", summary["tokens"], expected_total)
check("the library card counts the scripts", summary["script_count"], 2)

print("\n[8] mixed models cannot claim one model's price")
from ai_usage import Usage, merge

mixed = merge(
    {"input": 10, "output": 5, "total": 15, "calls": 1, "model": "gemini-2.5-flash"},
    {"input": 10, "output": 5, "total": 15, "calls": 1, "model": "gemini-2.5-pro"},
).as_dict()
check("a two-model total reports no cost rather than a wrong one",
      mixed["cost_usd"], None)
check("but the tokens still add up", mixed["total"], 30)
check("an unpriced model counts tokens and omits the cost",
      Usage(input=10, output=5, calls=1, model="some-future-model",
            unpriced=True).cost_usd(), None)
check("a missing usage record reads as zero, never as an exception",
      merge(None, {}).as_dict()["total"], 0)

print("\n[9] export: .txt is the SAME bytes the breakdown reads")
r = client.get(f"/plans/{pid}/scripts/{item_id}/export?format=txt", headers=auth)
check("txt export -> 200", r.status_code, 200)
check("named after the script",
      "The accidental worshipper.txt" in r.headers.get("content-disposition", ""), True)
exported = r.content.decode("utf-8-sig")
check("the exported text IS the stored text", exported.strip(), s0["text"].strip())
check("BOM present so Word and Notepad open Devanagari cleanly",
      r.content[:3], b"\xef\xbb\xbf")

r = client.get(f"/plans/{pid}/scripts/{item_id}/export?format=docx", headers=auth)
check("docx export -> 200", r.status_code, 200)
check("docx is a valid zip",
      zipfile.ZipFile(io.BytesIO(r.content)).testzip() is None, True)
from docx import Document

doc = Document(io.BytesIO(r.content))
paras = [p.text for p in doc.paragraphs]
check("docx opens back with the title", paras[0], "The accidental worshipper")
check("docx sets speech under the speaker", "MADANLAL" in paras, True)
check("an unknown export format is rejected",
      client.get(f"/plans/{pid}/scripts/{item_id}/export?format=pdf",
                 headers=auth).status_code, 422)
check("an unknown script id -> 404",
      client.get(f"/plans/{pid}/scripts/nope/export?format=txt",
                 headers=auth).status_code, 404)

print("\n[10] the handoff into Script to Storyboard")
# Start from a draft with something in it, so the overwrite is observable.
client.put("/scripts/draft", headers=auth, json={"text": "an older draft", "title": "old"})
r = client.post(f"/plans/{pid}/scripts/{item_id}/to-draft", headers=auth)
check("to-draft -> 200", r.status_code, 200)
draft = client.get("/scripts/draft", headers=auth).json()
# ⚠ THE ONE THAT MATTERS: the storyboard reads the draft, so the draft must
# hold what the .txt export holds — one format, not two.
check("the draft holds exactly the exported script", draft["text"].strip(), exported.strip())
check("the draft is titled after the script", draft["title"], "The accidental worshipper")
check("to-draft on an unknown script -> 404",
      client.post(f"/plans/{pid}/scripts/nope/to-draft", headers=auth).status_code, 404)

print("\n[11] deleting a script")
r = client.delete(f"/plans/{pid}/scripts/{brief_id}", headers=auth)
check("delete -> 200", r.status_code, 200)
check("one script left", len(r.json()["scripts"]), 1)
check("the right one survived", r.json()["scripts"][0]["id"], item_id)
# Those tokens were spent. A total that shrinks when you tidy up is a lie.
check("the token total does NOT shrink", r.json()["usage"]["total"], expected_total)
check("deleting it twice -> 404",
      client.delete(f"/plans/{pid}/scripts/{brief_id}", headers=auth).status_code, 404)

print("\n[12] owner isolation and auth")
other_email, other_auth = new_user()
check("a stranger cannot list my scripts",
      client.get(f"/plans/{pid}", headers=other_auth).status_code, 404)
check("a stranger cannot write into my session",
      client.post(f"/plans/{pid}/script", headers=other_auth,
                  json={"brief": "x"}).status_code, 404)
check("a stranger cannot export my script",
      client.get(f"/plans/{pid}/scripts/{item_id}/export?format=txt",
                 headers=other_auth).status_code, 404)
check("a stranger cannot load my script into their draft",
      client.post(f"/plans/{pid}/scripts/{item_id}/to-draft",
                  headers=other_auth).status_code, 404)
check("a stranger cannot delete my script",
      client.delete(f"/plans/{pid}/scripts/{item_id}", headers=other_auth).status_code, 404)
check("their draft was not touched",
      client.get("/scripts/draft", headers=other_auth).json()["text"], "")
check("write -> 401 with no token",
      client.post(f"/plans/{pid}/script", json={"brief": "x"}).status_code, 401)
check("export -> 401 with no token",
      client.get(f"/plans/{pid}/scripts/{item_id}/export?format=txt").status_code, 401)
check("to-draft -> 401 with no token",
      client.post(f"/plans/{pid}/scripts/{item_id}/to-draft").status_code, 401)

print("\n[13] the editorial stance is CONFIGURATION, not an accident")
# These assert what the tool is set up to do, without generating anything. The
# stance is a product decision and belongs somewhere a reviewer can find it.
stance = plan_agent._SCRIPT_STANCE
check("the stance is part of the script system prompt",
      stance in plan_agent._SCRIPT_SYSTEM, True)
check("it forbids unsolicited content warnings",
      "NEVER add a content warning" in stance, True)
check("it forbids silently softening a brief",
      "soften a brief" in stance, True)
for must_allow in ("Violence", "addiction", "villain", "Sex and desire"):
    check(f"dark material named as writable: {must_allow}", must_allow in stance, True)
# And the limits are still stated, so "we removed the refusals" is not the same
# sentence as "we removed all of them".
for limit in ("under 18", "synthesis route", "identifiable, private person"):
    check(f"the limit is still stated: {limit}", limit in stance, True)

print("\n[14] the provider safety threshold")
saved = os.environ.get("AI_SAFETY_THRESHOLD")
try:
    os.environ.pop("AI_SAFETY_THRESHOLD", None)
    settings = plan_agent._safety_settings()
    check("four harm categories are configured", len(settings or []), 4)
    check("at BLOCK_ONLY_HIGH by default",
          all(str(s.threshold).endswith("BLOCK_ONLY_HIGH") for s in settings), True)

    os.environ["AI_SAFETY_THRESHOLD"] = "OFF"
    check("OFF sends nothing and takes the provider default",
          plan_agent._safety_settings(), None)

    os.environ["AI_SAFETY_THRESHOLD"] = "BLOCK_NONE"
    check("an operator can lower it further",
          all(str(s.threshold).endswith("BLOCK_NONE")
              for s in plan_agent._safety_settings()), True)

    os.environ["AI_SAFETY_THRESHOLD"] = "NONSENSE"
    check("junk falls back to the provider default rather than failing the call",
          plan_agent._safety_settings(), None)
finally:
    os.environ.pop("AI_SAFETY_THRESHOLD", None)
    if saved is not None:
        os.environ["AI_SAFETY_THRESHOLD"] = saved

print("\n[15] the flattener and the writer's guards (real code, no model)")
# The real write_script was swapped out for the stub above, but its docstring
# names the flattener as the one handoff — assert that promise is still there,
# since the whole of section [10] rests on it.
check("write_script documents script_to_text as the handoff",
      "script_to_text" in (_real_write_script.__doc__ or ""), True)
try:
    plan_agent._coerce_scenes([])
    check("an empty scene list raises rather than producing an empty script", False)
except plan_agent.ScriptError:
    check("an empty scene list raises rather than producing an empty script", True)
beats = plan_agent._coerce_beats([
    {"type": "chorus", "text": "unknown type"},
    {"type": "dialogue", "character": "", "text": "who said this?"},
    {"type": "action", "character": "Kabir", "text": "action carries no speaker"},
    {"type": "action", "text": "   "},
    "a bare string",
])
check("an unknown beat type degrades to action rather than being dropped",
      beats[0]["type"], "action")
check("an unattributed spoken line is named rather than lost",
      beats[1]["character"], "SPEAKER")
check("an action beat never carries a speaker", beats[2]["character"], "")
check("a blank beat is dropped", len(beats), 4)
check("a bare string becomes an action beat", beats[3]["type"], "action")
check("spoken_words counts speech only, not action",
      plan_agent.spoken_words({"scenes": FAKE_SCENES}),
      len("Seven o'clock ho gaya!".split())
      + len("Every morning starts the same way.".split())
      + len("I am not late, I am early for tomorrow.".split()))

print("\n[16] the length read off a calendar row's format (node drives the JS)")
# Pure module, so the browser's answer is checked directly rather than
# reimplemented here — the same arrangement lane_reorder_check.py uses.
CASES = [
    ["YouTube Short (45s)", 45],
    ["Long-form (8-10 min)", 540],
    ["Instagram Reel", 45],
    ["Livestream", 900],
    ["3 min explainer", 180],
    ["Carousel", 60],
    ["", 60],
    ["90s vertical", 90],
]
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mod = os.path.join(root, "client", "src", "plan", "script_length.js").replace("\\", "/")
node_src = (
    f"import {{ secondsFromFormat, formatRuntime }} from 'file:///{mod}';\n"
    f"const cases = {json.dumps(CASES)};\n"
    "console.log(JSON.stringify({\n"
    "  lengths: cases.map(([f]) => secondsFromFormat(f)),\n"
    "  runtimes: [45, 90, 540, 900].map(formatRuntime),\n"
    "}));\n"
)
tmp = os.path.join(root, "_script_length_probe.mjs")
try:
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(node_src)
    out = subprocess.run([("node.exe" if os.name == "nt" else "node"), tmp],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        check(f"node ran the length module ({out.stderr.strip()[:120]})", False)
    else:
        got = json.loads(out.stdout.strip().splitlines()[-1])
        check("every format reads to the right length",
              got["lengths"], [want for _f, want in CASES])
        # The range case is the one that breaks if the patterns are reordered:
        # "8-10 min" must be the midpoint, not 8 minutes.
        check("a range is read as its midpoint, not its first number",
              got["lengths"][1], 540)
        check("runtimes render for humans",
              got["runtimes"], ["45s", "1m 30s", "9m", "15m"])
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

print("\n[17] cleanup")
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
print("All Plan & Script — script checks passed.")
