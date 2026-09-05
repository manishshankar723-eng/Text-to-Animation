"""
editor_chat_work_check.py — the ✨ AI Editor's BIG jobs: the fan-out, and the job.

    "agar mai 3 ke jagah 5/10 kuchh kaam karwaye jaise full editing to ek baar
     mai kaise legega … mai chahta hun ai time le magar user jo bola hai woh
     kare aur jaldi v kare … progress dikha kar, kabhi timeout nahi"

⚠ **NOT ONE MODEL IS CALLED AND NOT A PENNY IS SPENT.** Every call goes through
`llm_json.use_adapter`, which is the same seam the Director's own tests use. The
fake adapter is deliberately BADLY BEHAVED — it writes steps outside the range it
was given, it is slow, and one of its batches fails — because the three things
this design must survive are a model that overshoots its slice, a job that takes
minutes, and one part of it dying without taking the rest with it.

⚠ **AND THE ONE THING THIS FILE WATCHES HARDEST IS THAT SPLITTING THE WORK DID
NOT SPLIT THE JUDGEMENT.** The operator's objection is the reason the design is
shaped the way it is:

    "1 niyam likhne do to har clip pe dissolve hi laga dega na — magar mujhe to
     chahiye ki story ke hisab se do shot ke bich ko samajh kar jaha jo jaruri
     hai waise lage"

Exactly right, and it is why nothing here compresses sixty decisions into one
rule. Each batch is handed real shot descriptions and decides cut by cut; the
only thing that changed is how many cuts one call is asked about. §2 asserts that
the descriptions really reach the batch and that the craft prompt travels with
them — a fan-out that sent bare shot numbers would be fast and worthless.
"""

import io
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ⚠ THE CONSOLE IS cp1252 ON WINDOWS AND THIS FILE IS FULL OF ⚠. Without
# this the whole suite EXITS 1 on a `⚠` — a green run that reports itself as a
# failure, which is worse than a red one. `director_timeout_check.py` line 61
# does the same thing for the same reason.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ AN IN-PROCESS STORE AND A TEMP USAGE FILE, SET BEFORE THE IMPORTS THAT READ
# THEM. RULEBOOK G13 was paid for by a test that wrote eight real projects into
# the developer's own output folder and spent their live quota.
os.environ["API_JOB_STORE"] = "memory"
os.environ.setdefault("API_LOCAL_USAGE_PATH", str(ROOT / ".pytest-usage.json"))

import editor_chat_agent as agent  # noqa: E402
import llm_json  # noqa: E402

failures: list[str] = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


VOCAB = {"verbs": [
    {"id": "add_transition", "args": ["shot", "kind", "params"]},
    {"id": "delete_shot", "args": ["shot"]},
]}


def board(n, described=True):
    return {
        "film": {"title": "Ganesh Chaturthi at home", "genre": "devotional family film"},
        "shots": [
            {
                "label": f"Shot {i}",
                "description": f"the family lights the {i}th lamp" if described else "",
                "location": "living room" if described else "",
            }
            for i in range(1, n + 1)
        ],
    }


# ===========================================================================
print("\n1 · THE SPLIT — decided before anything is called, so it can be counted\n")
# ===========================================================================
work = agent._coerce_work({"tasks": [
    {"goal": "choose the right transition for each cut", "verbs": ["add_transition"]},
    {"goal": "sound effects and one music bed", "sound": True},
]}, 60)

check("a brief with two jobs survives cleaning", work and len(work["tasks"]) == 2)
units = agent.work_batches(work, 60)
picture = [u for u in units if not u["task"].get("sound")]
sound = [u for u in units if u["task"].get("sound")]
check("⚠ THE PICTURE JOB IS CUT INTO BATCHES OF AT MOST BATCH_SHOTS",
      picture and all(u["last"] - u["first"] + 1 <= agent.BATCH_SHOTS for u in picture),
      str([(u["first"], u["last"]) for u in picture]))
check("…and between them they cover every shot exactly once",
      sorted(n for u in picture for n in range(u["first"], u["last"] + 1)) == list(range(1, 61)))
# ⚠ SOUND IS ONE UNIT ON PURPOSE. Its answer is two or three words a shot, so the
# length that forces the split is not there — and one call keeps the music bed a
# single decision about the film instead of five batches each choosing a bed.
check("⚠ A SOUND JOB IS ONE CALL, NOT A DOZEN", len(sound) == 1, str(sound))
check("…covering the whole film", sound and (sound[0]["first"], sound[0]["last"]) == (1, 60))

# ⚠ THE CEILING IS A MONEY GUARD. Every batch is a paid call, so an unbounded
# split turns one sentence into a bill nobody approved.
big = agent._coerce_work(
    {"tasks": [{"goal": f"job {i}", "verbs": ["add_transition"]} for i in range(4)]}, 400
)
check("⚠ A FAN-OUT BIGGER THAN MAX_WORK_BATCHES IS REFUSED, NOT HALF-RUN",
      len(agent.work_batches(big, 400)) > agent.MAX_WORK_BATCHES)
try:
    agent.run_work(work=big, board=board(400), vocabulary=VOCAB, settings={})
    refused = ""
except agent.EditorChatError as e:
    refused = str(e)
check("…and the refusal says how to make it smaller",
      "one kind of change at a time" in refused or "range of shots" in refused, refused)
check("⚠ AT MOST MAX_WORK_TASKS JOBS SURVIVE CLEANING",
      len(agent._coerce_work(
          {"tasks": [{"goal": f"j{i}"} for i in range(agent.MAX_WORK_TASKS + 3)]}, 10
      )["tasks"]) == agent.MAX_WORK_TASKS)
check("⚠ A TASK WITH NO GOAL IS DROPPED — the goal is all the batch is told",
      agent._coerce_work({"tasks": [{"verbs": ["add_transition"]}]}, 10) is None)
check("⚠ A SHOT RANGE FROM A MODEL IS CLAMPED TO THE REAL FILM",
      agent._coerce_work(
          {"tasks": [{"goal": "g", "first_shot": -5, "last_shot": 9999}]}, 12
      )["tasks"][0] | {} != {} and
      agent._coerce_work(
          {"tasks": [{"goal": "g", "first_shot": -5, "last_shot": 9999}]}, 12
      )["tasks"][0]["first_shot"] == 1 and
      agent._coerce_work(
          {"tasks": [{"goal": "g", "first_shot": -5, "last_shot": 9999}]}, 12
      )["tasks"][0]["last_shot"] == 12)

# ===========================================================================
print("\n2 · THE JUDGEMENT IS NOT COMPRESSED — this is the whole objection\n")
# ===========================================================================
seen: list[str] = []
seen_lock = threading.Lock()


def spy(request):
    with seen_lock:
        seen.append(request.prompt)
    m = re.search(r"SHOTS (\d+)–(\d+) ONLY", request.prompt)
    if not m:
        return json.dumps({"steps": [], "sound": {
            "sfx": [{"shot": 1, "query": "temple bell"}],
            "music": {"query": "sitar flute bhajan", "mood": "devotional"},
        }})
    lo, hi = int(m.group(1)), int(m.group(2))
    # ⚠ DELIBERATELY ONE STEP TOO FAR. The batch can SEE the overlap shot; a
    # model that writes for it would have that cut written twice, by two passes
    # running at the same moment. §3 asserts the merge refuses it.
    return json.dumps({"steps": [
        {"verb": "add_transition", "args": {"shot": n, "kind": "dissolve"}}
        for n in range(lo, hi + 2)
    ]})


llm_json.use_adapter(spy)
try:
    out = agent.run_work(work=work, board=board(60), vocabulary=VOCAB,
                         settings={"turn_seconds": 120})
finally:
    llm_json.use_adapter(None)

picture_prompts = [p for p in seen if "SHOTS" in p and "ONLY" in p]
check("every batch was really called", len(seen) == len(units), f"{len(seen)} of {len(units)}")
check("⚠ A BATCH IS GIVEN THE SHOTS' OWN DESCRIPTIONS, not just their numbers",
      all("lights the" in p for p in picture_prompts))
check("⚠ …AND WHAT THE FILM IS, so a devotional board is not scored as a vlog",
      all("devotional family film" in p for p in picture_prompts))
check("⚠ …AND THE BRIEF'S OWN WORDS, which are all it is told of the conversation",
      all("right transition for each cut" in p for p in picture_prompts))
check("⚠ …AND IT IS TOLD TO JUDGE EACH SHOT ON ITS OWN, not to apply one answer",
      all("JUDGE EVERY SHOT ON ITS OWN" in p for p in picture_prompts))
check("⚠ …AND THAT A PLAIN CUT IS A REAL ANSWER — the anti-dissolve rule survives",
      all("leave that cut plain" in p for p in picture_prompts))
# ⚠ THE CRAFT LIVES IN THE SYSTEM PROMPT, and a batch that ran without it would
# be fast and tasteless. Splitting the work must not split the taste.
check("⚠ A BATCH RUNS ON THE SAME SYSTEM PROMPT AS A CONVERSATION",
      agent.prompts()["batch"] and agent.prompts()["system"])

edges = [p for p in picture_prompts if "context only, not yours" in p]
check("⚠ A BATCH SEES ONE SHOT PAST ITS EDGE — a cut is about TWO shots",
      len(edges) == len(picture_prompts), f"{len(edges)} of {len(picture_prompts)}")

# ===========================================================================
print("\n3 · THE MERGE — one plan, in order, with nothing written twice\n")
# ===========================================================================
steps = (out.get("plan") or {}).get("steps") or []
at = [s["args"]["shot"] for s in steps]
check("the answer is an ORDINARY plan, not a new kind of thing", out["kind"] == "plan")
check("⚠ EVERY SHOT IS COVERED", set(at) == set(range(1, 61)), f"{len(at)} steps")
check("⚠ AND NOT ONE OF THEM TWICE — the model overshot and the merge refused it",
      len(at) == len(set(at)), f"{len(at)} steps, {len(set(at))} distinct")
check("…and the refusals are reported, not silent",
      any("outside this pass's range" in (d.get("why") or "") for d in out["dropped"]))
check("⚠ SORTED BY SHOT — the batches finish in whatever order they finish in",
      at == sorted(at))
check("⚠ ONE MUSIC BED FOR THE FILM, not one per batch",
      isinstance(out["sound"].get("music"), dict))
check("the sound cues arrive too", len(out["sound"]["sfx"]) >= 1)
# ⚠ `asked_for_all` MUST BE FALSE. These steps were written shot by shot; the
# client's guardrails EXPAND a plan that carries this flag, which would apply
# every step to every shot a second time.
check("⚠ `asked_for_all` IS FALSE — these steps are already per-shot",
      (out.get("plan") or {}).get("asked_for_all") is False)
check("the reply is counts, not adjectives",
      "60 edits" in out["reply"] and "passes" in out["reply"], out["reply"])

# ===========================================================================
print("\n4 · IT RUNS AT THE SAME TIME, AND THAT IS THE SPEED\n")
# ===========================================================================
live = 0
peak = 0
plock = threading.Lock()


def slow(request):
    global live, peak
    with plock:
        live += 1
        peak = max(peak, live)
    time.sleep(0.25)
    with plock:
        live -= 1
    m = re.search(r"SHOTS (\d+)–(\d+) ONLY", request.prompt)
    if not m:
        return json.dumps({"steps": []})
    lo, hi = int(m.group(1)), int(m.group(2))
    return json.dumps({"steps": [
        {"verb": "add_transition", "args": {"shot": n, "kind": "cut"}} for n in range(lo, hi + 1)
    ]})


wide = agent._coerce_work(
    {"tasks": [{"goal": "transitions", "verbs": ["add_transition"]}]}, 96)
n_units = len(agent.work_batches(wide, 96))
llm_json.use_adapter(slow)
started = time.monotonic()
try:
    agent.run_work(work=wide, board=board(96), vocabulary=VOCAB, settings={"turn_seconds": 120})
finally:
    llm_json.use_adapter(None)
took = time.monotonic() - started
serial = 0.25 * n_units
check(f"⚠ {n_units} BATCHES RAN {agent.MAX_PARALLEL_CALLS} AT A TIME",
      peak > 1 and peak <= agent.MAX_PARALLEL_CALLS, f"peak {peak}")
check("⚠ …SO THE JOB IS SHORTER THAN DOING THEM ONE AFTER ANOTHER",
      took < serial * 0.75, f"{took:.2f}s vs {serial:.2f}s serial")
# ⚠ POLITE, NOT GREEDY. Every provider rate limits, and forty simultaneous calls
# is a 429 storm that fails the whole job to save a few seconds.
check("⚠ …AND NEVER MORE AT ONCE THAN THE PROVIDER WAS PROMISED",
      peak <= agent.MAX_PARALLEL_CALLS, f"peak {peak}")

# ===========================================================================
print("\n5 · ONE PART FAILING IS NOT THE JOB FAILING\n")
# ===========================================================================
# ⚠ THE SAME BATCH FAILS EVERY TIME, NOT THE SECOND CALL. `llm_json` retries a
# transport failure three times, so failing "call number two" is a batch that
# quietly succeeds on its retry — which is the RIGHT behaviour and made the first
# version of this test assert against a bug that is not there. To exercise the
# path where a part is really lost, one part has to be really lost.
def flaky(request):
    if "SHOTS 13–24 ONLY" in request.prompt:
        raise RuntimeError("the model was busy")
    m = re.search(r"SHOTS (\d+)–(\d+) ONLY", request.prompt)
    if not m:
        return json.dumps({"steps": []})
    lo, hi = int(m.group(1)), int(m.group(2))
    return json.dumps({"steps": [
        {"verb": "add_transition", "args": {"shot": n, "kind": "cut"}} for n in range(lo, hi + 1)
    ]})


llm_json.use_adapter(flaky)
try:
    partial = agent.run_work(
        work=agent._coerce_work(
            {"tasks": [{"goal": "transitions", "verbs": ["add_transition"]}]}, 48),
        board=board(48), vocabulary=VOCAB, settings={"turn_seconds": 120},
    )
finally:
    llm_json.use_adapter(None)
kept = [s["args"]["shot"] for s in (partial.get("plan") or {}).get("steps") or []]
check("⚠ THE BATCHES THAT WORKED STILL COME BACK AS A PLAN", len(kept) > 0, str(len(kept)))
# ⚠ AND THE SHOTS THE LOST BATCH OWNED ARE SIMPLY ABSENT — not guessed at by
# a neighbour, and not silently filled with something plausible.
check("⚠ …AND THE LOST BATCH'S SHOTS ARE LEFT ALONE, not invented",
      not (set(range(13, 25)) & set(kept)) and len(kept) == 36, str(sorted(set(kept))[:5]))
check("⚠ …AND THE REPLY SAYS WHAT WAS MISSED, rather than quietly doing less",
      "did not come back" in partial["reply"], partial["reply"])
check("…and says those shots were left alone",
      "left alone" in partial["reply"], partial["reply"])

# ===========================================================================
print("\n6 · A SMALL JOB IS STILL A PLAIN TURN — no bar, no job, no extra call\n")
# ===========================================================================
# ⚠ THE FAST PATH MUST STAY FAST. A fan-out over eight steps spends an extra
# planning call to be SLOWER, and puts a progress bar in front of work that
# finishes before it is drawn.
small = agent._read_turn(
    {"kind": "plan", "reply": "done",
     "plan": {"steps": [{"verb": "add_transition", "args": {"shot": 2, "kind": "dissolve"}}]}},
    VOCAB, 30,
)
check("an ordinary plan is still `plan`", small["kind"] == "plan")
check("⚠ …AND CARRIES NO WORK BRIEF AT ALL", small["work"] is None)
brief = agent._read_turn(
    {"kind": "work", "reply": "on it",
     "work": {"tasks": [{"goal": "transitions"}]}}, VOCAB, 30)
check("a big message reads as `work`", brief["kind"] == "work")
check("⚠ …AND DRAWS NO APPLY BUTTON: there is nothing to apply until it has run",
      brief["plan"] is None and brief["sound"] is None)
# ⚠ A MODEL THAT SENDS BOTH IS HEDGING, and the two readings are not equal: real
# steps are work it has already DONE, and running the fan-out too would do that
# work a second time and charge for it.
both = agent._read_turn(
    {"kind": "plan", "reply": "x",
     "plan": {"steps": [{"verb": "add_transition", "args": {"shot": 1, "kind": "cut"}}]},
     "work": {"tasks": [{"goal": "transitions"}]}}, VOCAB, 30)
check("⚠ STEPS AND A BRIEF TOGETHER: the steps win unless the model asked for work",
      both["kind"] == "plan" and both["work"] is None)
check("⚠ …AND `kind: work` MAKES THE BRIEF WIN, because it said so",
      agent._read_turn(
          {"kind": "work", "reply": "x",
           "plan": {"steps": [{"verb": "add_transition", "args": {"shot": 1, "kind": "cut"}}]},
           "work": {"tasks": [{"goal": "t"}]}}, VOCAB, 30)["kind"] == "work")

# ===========================================================================
print("\n7 · THE JOB — it returns at once, reports progress, and can be stopped\n")
# ===========================================================================
from server import editor_chat_work as chat_work  # noqa: E402
from server.jobs import get_store  # noqa: E402

gate = threading.Event()


def held(request):
    # Every batch waits for the test to let it go, so "it came back before the
    # work did" is a fact rather than a race.
    gate.wait(10)
    m = re.search(r"SHOTS (\d+)–(\d+) ONLY", request.prompt)
    if not m:
        return json.dumps({"steps": []})
    lo, hi = int(m.group(1)), int(m.group(2))
    return json.dumps({"steps": [
        {"verb": "add_transition", "args": {"shot": n, "kind": "cut"}} for n in range(lo, hi + 1)
    ]})


llm_json.use_adapter(held)
try:
    job_work = agent._coerce_work(
        {"tasks": [{"goal": "transitions", "verbs": ["add_transition"]}]}, 60)
    t0 = time.monotonic()
    wid = chat_work.start(animatic_id="anim-1", owner="me@example.com", work=job_work,
                          board=board(60), vocabulary=VOCAB, settings={"turn_seconds": 120})
    handed_back = time.monotonic() - t0
    check("⚠ THE REQUEST COMES BACK AT ONCE — this is what cannot time out",
          handed_back < 1.0, f"{handed_back:.2f}s")
    row = get_store().get(wid)
    check("…and the record exists the instant the id does, so a poll cannot 404",
          row is not None and row.kind.value == "editor_chat")
    check("…already RUNNING, with the part count known before any call landed",
          row.status.value == "running" and (row.progress or {}).get("total_parts") == 5,
          str(row.progress))
    check("…and this process knows it is really alive", chat_work.is_live(wid))
    gate.set()
    for _ in range(200):
        row = get_store().get(wid)
        if row.status.value in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    check("the job finishes", row.status.value == "succeeded", row.status.value)
    check("⚠ …AND ITS ANSWER IS AN ORDINARY TURN, for the same normaliseTurn",
          isinstance(row.result, dict) and row.result.get("kind") == "plan"
          and "reply" in row.result and "plan" in row.result)
    check("…covering the whole film",
          {s["args"]["shot"] for s in row.result["plan"]["steps"]} == set(range(1, 61)))
    check("…and progress finished at 100", (row.progress or {}).get("percent") == 100)
finally:
    llm_json.use_adapter(None)

# --- Stop --------------------------------------------------------------------
stop_gate = threading.Event()
started_batches = {"n": 0}
slock = threading.Lock()


def stoppable(request):
    with slock:
        started_batches["n"] += 1
    stop_gate.wait(10)
    m = re.search(r"SHOTS (\d+)–(\d+) ONLY", request.prompt)
    lo, hi = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
    return json.dumps({"steps": [
        {"verb": "add_transition", "args": {"shot": n, "kind": "cut"}} for n in range(lo, hi + 1)
    ]})


llm_json.use_adapter(stoppable)
try:
    stop_work = agent._coerce_work(
        {"tasks": [{"goal": "transitions", "verbs": ["add_transition"]}]}, 240)
    total_units = len(agent.work_batches(stop_work, 240))
    wid2 = chat_work.start(animatic_id="anim-2", owner="me@example.com", work=stop_work,
                           board=board(240), vocabulary=VOCAB, settings={"turn_seconds": 120})
    for _ in range(100):
        if started_batches["n"] >= agent.MAX_PARALLEL_CALLS:
            break
        time.sleep(0.05)
    chat_work.stop(wid2)
    stop_gate.set()
    for _ in range(300):
        row2 = get_store().get(wid2)
        if row2.status.value in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    check("⚠ A STOPPED JOB STILL FINISHES — it is not left running for ever",
          row2.status.value == "succeeded", row2.status.value)
    check("⚠ …AND STOPS THE SPEND: most batches were never started",
          started_batches["n"] < total_units,
          f"{started_batches['n']} of {total_units} batches called")
    check("⚠ …AND WHAT WAS WRITTEN STILL COMES BACK AS AN APPLICABLE PLAN",
          len((row2.result.get("plan") or {}).get("steps") or []) > 0)
    check("…and the reply says so, so nobody thinks their work was thrown away",
          "Stopped" in row2.result["reply"] and "apply it" in row2.result["reply"],
          row2.result["reply"])
    check("…and the turn is marked stopped, for the panel", row2.result.get("stopped") is True)
finally:
    llm_json.use_adapter(None)

# ⚠ A STOP FLAG LEFT BEHIND WOULD REFUSE THE FIRST BATCH OF WHOEVER GETS THAT ID
# NEXT — the store hands out ids, and a stale flag is a job that stops itself.
import cancel  # noqa: E402
check("⚠ THE STOP FLAG IS CLEARED WHEN THE JOB ENDS", not cancel.is_cancelled(wid2))

# ===========================================================================
print("\n8 · THE FAILURES THAT ARE NOT ERRORS — a restart, and somebody else's job\n")
# ===========================================================================
# ⚠ A RECORD LEFT RUNNING WITH NO RUNNER IS A SERVER THAT RESTARTED MID-FLIGHT,
# and it will never finish. A bar that never moves again is the worst of the
# three things the panel could say, so the state has a name of its own.
store = get_store()
orphan = store.create(character_name="orphan", kind=row.kind, owner="me@example.com",
                      params={"animatic_id": "anim-9"})
store.update(orphan.job_id, status=row.status.__class__("running"),
             progress={"percent": 20, "done_parts": 1, "total_parts": 5, "message": "…"})
check("⚠ A RUN THIS PROCESS HAS NEVER SEEN IS `lost`, NOT `running`",
      not chat_work.known(orphan.job_id))

src = io.open(ROOT / "server" / "editor_chat.py", encoding="utf-8").read()
check("…and the route really has a name for that state", '"lost"' in src or "state=\"lost\"" in src)
check("⚠ AND OWNERSHIP IS CHECKED ON THE RECORD BEFORE ANY OF IT IS RETURNED",
      "owner != (current.email" in src)
check("⚠ …AS A 404, so a job id cannot be confirmed by guessing at it",
      "No such job." in src)
check("⚠ NEITHER WATCHING NOR STOPPING SPENDS A TURN",
      "usage_counters.increment" not in src.split("def _work_status")[1])

client = io.open(ROOT / "client" / "src" / "animatic" / "agent" / "useEditorChat.js",
                 encoding="utf-8").read()
check("⚠ THE PANEL POLLS RATHER THAN HOLDING A CONNECTION OPEN",
      "editorChatWork(" in client and "pause(" in client)
check("⚠ …AND STOP ON A JOB ASKS THE SERVER instead of aborting the watch",
      "editorChatWorkStop" in client)
check("⚠ …AND KEEPS WATCHING AFTERWARDS, so the partial plan is not thrown away",
      "return;" in client.split("editorChatWorkStop")[1][:400])
check("⚠ THE FINISHED JOB GOES THROUGH THE SAME normaliseTurn AS EVERY TURN",
      "normaliseTurn(finished" in client)

print()
if failures:
    print(f"✗ {len(failures)} check(s) failed:")
    for name in failures:
        print(f"    - {name}")
    sys.exit(1)
print("✓ big jobs: split, judged shot by shot, run together, stoppable, and never timing out")
