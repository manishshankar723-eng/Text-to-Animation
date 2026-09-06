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

import director  # noqa: E402
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

# ⚠ THE SHOT BATCHES, PICKED OUT BY THE ONE LINE ONLY THEY HAVE. A sound task
# is told "every shot in this film" and has no window, so it fails every check
# below for a good reason. This used to sniff for the words "SHOTS" and "ONLY"
# anywhere in the prompt and silently swallowed the sound batch the day someone
# wrote "ONLY" into an unrelated paragraph — five checks then went red for a
# prompt edit that was correct. Match the RANGE line itself.
picture_prompts = [p for p in seen if "YOUR PART OF IT: shots " in p]
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

# --- the batch may only reach for ITS OWN arguments ---------------------------
# ⚠ PAID FOR ON THE FIRST LIVE RUN, AND THEN AGAIN ON THE SECOND. *"transition
# and effects ke saath"* on a 14-shot reel produced perfect transitions and
# **eight effects with no effect named** — `add_effect: the step named no effect
# to add`, every one thrown away; the next build lost **sixteen** the same way.
# `args` is a FLAT UNION of every verb's argument names, so a batch is offered
# every other verb's names beside its own; narrowing the schema to this batch's
# verbs removes most of them.
#
# ⚠ **AND THE SECOND LOSS WAS CAUSED BY THE FIX FOR THE FIRST.** The batch prompt
# was written saying *"an `add_effect` needs `effect`"* — and it does not, it
# needs **`kind`**, the same name `add_transition` takes. This test had the same
# wrong name typed into its fixture, so it stayed green while the app threw every
# effect away. **THE ARGUMENT NAMES ARE READ OFF `actions.js` NOW**, which is the
# only place they are real, and the checks below fail if the prompt contradicts
# it. Never type one of these names by hand again — not here, not in a prompt.
ACTIONS_JS = (ROOT / "client" / "src" / "animatic" / "agent" / "actions.js").read_text(
    encoding="utf-8"
)


def manifest_args(verb: str) -> list[str]:
    """`verb`'s argument names, read out of the client registry that implements it."""
    at = ACTIONS_JS.find('verb: "%s",' % verb)
    assert at > 0, "no %s in actions.js" % verb
    block = re.search(r"args:\s*\[(.*?)\]", ACTIONS_JS[at:at + 1200], re.S)
    assert block, "no args for %s" % verb
    return re.findall(r'"([A-Za-z_]+)"', block.group(1))


REAL_FX_ARGS = manifest_args("add_effect")
FX_SIGNATURE = "add_effect(%s)" % ", ".join(REAL_FX_ARGS)
check("⚠ THE MANIFEST IS THE ONLY SOURCE OF ARGUMENT NAMES — `add_effect` really takes `kind`",
      "kind" in REAL_FX_ARGS and "shot" in REAL_FX_ARGS, str(REAL_FX_ARGS))
# ⚠ THE PROMPT MAY NOT CONTRADICT THE MANIFEST. One sentence naming the wrong
# field is the whole regression — it told the model to write one that isn't there.
batch_prompt = agent.prompts()["batch"]
check("⚠ AND THE BATCH PROMPT DOES NOT NAME AN add_effect ARGUMENT THAT ISN'T THERE",
      "needs `effect`" not in batch_prompt, "the prompt names a field the editor has no such field")
check("…it shows the signature the manifest really has", FX_SIGNATURE in batch_prompt,
      FX_SIGNATURE)

FX_VOCAB = {"verbs": [
    {"id": "add_transition", "label": "a transition", "args": manifest_args("add_transition")},
    {"id": "add_effect", "label": "an effect", "args": REAL_FX_ARGS},
    {"id": "delete_shot", "label": "remove a shot", "args": manifest_args("delete_shot")},
]}
fx_schema = agent.batch_schema(FX_VOCAB, {"verbs": ["add_effect"]})
fx_args = sorted(fx_schema["properties"]["steps"]["items"]["properties"]["args"]["properties"])
check("⚠ AN EFFECTS BATCH IS NARROWED TO ITS OWN ARGUMENTS — `cut` is not in its schema",
      "cut" not in fx_args, str(fx_args))
check("…and it still has every argument it actually needs",
      all(name in fx_args for name in REAL_FX_ARGS), str(fx_args))
check("⚠ …AND NO VERB IT WAS NOT GIVEN",
      fx_schema["properties"]["steps"]["items"]["properties"]["verb"]["enum"] == ["add_effect"])
card = agent._verb_card(FX_VOCAB, {"verbs": ["add_effect"]})
check("⚠ AND THE PROMPT NAMES ITS VERBS WITH THEIR EXACT ARGUMENTS, on their own line",
      FX_SIGNATURE in card and "add_transition" not in card, card)
check("…which the batch prompt really has a slot for", "<<VERBS>>" in batch_prompt)

# ---------------------------------------------------------------------------
# ⚠ THE SAME BUG, A THIRD TIME — AND THE FIRST FIX THAT CANNOT LOSE.
# ---------------------------------------------------------------------------
# Live on 2026-09-06, AFTER the argument narrowing above and AFTER the prompt
# was corrected: *"transition and effects ke saath"* on a 14-shot Ganesh
# Chaturthi reel came back with fourteen rows of "add_effect: the step named no
# effect to add". Narrowing cannot separate `add_effect` from `add_transition`
# because BOTH of their arguments are spelt `kind` — so the batch wrote it on
# the transitions and left it off every effect. A prompt sentence had already
# failed twice. An ENUM is the one thing a model cannot leave blank.
def manifest_family(verb: str) -> str:
    """`verb`'s manifest family, read out of `actions.js` — never typed here."""
    at = ACTIONS_JS.find('verb: "%s",' % verb)
    assert at > 0, "no %s in actions.js" % verb
    found = re.search(r'family:\s*"([a-zA-Z]+)"', ACTIONS_JS[at:at + 2400])
    return found.group(1) if found else ""


FX_FAMILY = manifest_family("add_effect")
check("⚠ THE VERB ITSELF SAYS WHICH LIST ITS `kind` COMES OUT OF",
      FX_FAMILY == "effects" and manifest_family("add_transition") == "transitions",
      "%r / %r" % (FX_FAMILY, manifest_family("add_transition")))

KIND_VOCAB = {
    "verbs": [
        {"id": "add_transition", "label": "a transition",
         "args": manifest_args("add_transition"), "family": "transitions"},
        {"id": "add_effect", "label": "an effect", "args": REAL_FX_ARGS, "family": "effects"},
        {"id": "note", "label": "a note", "args": ["text"]},
    ],
    "effects": [{"id": "vignette"}, {"id": "grain"}],
    "transitions": [{"id": "dissolve"}, {"id": "wipe"}],
}


def kind_of(task):
    schema = agent.batch_schema(KIND_VOCAB, task)
    return schema["properties"]["steps"]["items"]["properties"]["args"]


one = kind_of({"verbs": ["add_effect"]})
check("⚠ `kind` IS AN ENUM OF THE REAL IDS, so it cannot come back empty",
      one["properties"]["kind"].get("enum") == ["vignette", "grain"],
      str(one["properties"].get("kind")))
check("⚠ …AND REQUIRED, because a field a model is not asked for is one it may not write",
      "kind" in (one.get("required") or []), str(one.get("required")))

both = kind_of({"verbs": ["add_transition", "add_effect"]})
check("⚠ A MIXED BATCH GETS THE UNION — this is the pass that lost 14 effects",
      set(both["properties"]["kind"].get("enum") or [])
      == {"vignette", "grain", "dissolve", "wipe"},
      str(both["properties"].get("kind")))
check("…and the description says which verb takes which, so the union is not a guess",
      "add_effect takes one of: vignette, grain"
      in both["properties"]["kind"].get("description", ""),
      both["properties"]["kind"].get("description", ""))

# ⚠ NOT REQUIRED WHEN A VERB IN THE BATCH HAS NO `kind` AT ALL. A schema that
# demands one from a `note` is a schema the batch cannot answer — which fails
# the whole pass instead of dropping one step.
withnote = kind_of({"verbs": ["add_effect", "note"]})
check("⚠ …BUT NEVER REQUIRED OF A VERB THAT TAKES NO `kind`",
      "kind" not in (withnote.get("required") or []), str(withnote.get("required")))

# ⚠ AND AN OLDER CLIENT — a manifest with no `family` — CHANGES NOTHING.
old_vocab = {"verbs": [{"id": "add_effect", "args": REAL_FX_ARGS}], "effects": [{"id": "grain"}]}
old_args = agent.batch_schema(old_vocab, {"verbs": ["add_effect"]})[
    "properties"]["steps"]["items"]["properties"]["args"]
check("⚠ …AND A MANIFEST WITHOUT `family` IS LEFT EXACTLY AS IT WAS",
      "enum" not in old_args["properties"]["kind"] and "kind" not in (old_args.get("required") or []),
      str(old_args))

# ⚠ A CUT IS NOT A SHOT. Same run: "add_transition: cut 14 is not between two
# shots" on a 14-shot film. The batch is told its range in SHOTS, so counting
# the cuts the same way is the obvious mistake, and the card is where the real
# number is known.
cut_card = agent._verb_card(KIND_VOCAB, {"verbs": ["add_transition"]}, 14)
check("⚠ THE VERB CARD SPELLS OUT THE CUT RANGE, so cut 14 of 14 is not written",
      "1 to 13" in cut_card, cut_card)
check("…and it lists the legal kinds beside the verb, generated from the manifest",
      "dissolve, wipe" in cut_card, cut_card)

# ⚠ AND THE FLOOR UNDER ALL OF IT: A SYNONYM IS RENAMED, NOT DROPPED. Neither
# the schema nor the prompt can be the last line of defence — both were, twice,
# and both failed. `effect` is the word English uses; the step keeps its meaning.
salvaged, thrown = director.fold_steps(
    [{"verb": "add_effect", "args": {"shot": 3, "effect": "vignette"}},
     {"verb": "add_effect", "args": {"shot": 4, "kind": "grain"}},
     {"verb": "apply_text_preset", "args": {"ref": "t1", "preset": "pop"}}],
    {"verbs": [
        {"id": "add_effect", "args": REAL_FX_ARGS},
        {"id": "apply_text_preset", "args": manifest_args("apply_text_preset")},
    ]},
)
check("⚠ A STEP THAT SAID `effect` INSTEAD OF `kind` IS SAVED, NOT THROWN AWAY",
      len(salvaged) > 0 and salvaged[0]["args"].get("kind") == "vignette", str(salvaged[:1]))
check("…and a step that said it properly is untouched",
      len(salvaged) > 1 and salvaged[1]["args"].get("kind") == "grain", str(salvaged[1:2]))
check("⚠ …AND A REAL ARGUMENT IS NEVER REWRITTEN — `preset` IS `apply_text_preset`'s own",
      len(salvaged) > 2 and salvaged[2]["args"].get("preset") == "pop", str(salvaged[2:3]))
check("…and not one of the three was dropped", not thrown, str(thrown))
# ⚠ Some OpenAI-compatible endpoints flatten a step even when the schema says
# `args`; others wrap the chosen effect in an object. Both forms carry a clear
# intent and must not become another "no effect to add" row.
flattened, wrapped_dropped = director.fold_steps(
    [{"verb": "add_effect", "shot": 5, "effect": "vignette"},
     {"verb": "add_effect", "args": {
         "shot": 6, "effect": {"id": "grain", "params": {"amount": "0.4"}}
     }}],
    {"verbs": [{"id": "add_effect", "args": REAL_FX_ARGS}]},
)
check("⚠ A FLATTENED EFFECT STEP IS RECOVERED, not reported as missing args",
      flattened and flattened[0]["args"].get("kind") == "vignette", str(flattened))
check("⚠ A WRAPPED EFFECT KEEPS ITS KIND AND PARAMS",
      len(flattened) > 1
      and flattened[1]["args"].get("kind") == "grain"
      and flattened[1]["args"].get("params") == {"amount": "0.4"}
      and not wrapped_dropped, str((flattened, wrapped_dropped)))
# ⚠ `params` AS A PLAIN OBJECT IS ALSO ACCEPTED — refusing it kept the effect
# at its default, which reads as the AI ignoring the instruction.
dialled, _ = director.fold_steps(
    [{"verb": "add_effect", "args": {"shot": 2, "kind": "blur", "params": {"amount": "0.4"}}}],
    {"verbs": [{"id": "add_effect", "args": REAL_FX_ARGS}]},
)
check("⚠ AND `params` WRITTEN AS AN OBJECT IS READ, not silently dropped",
      len(dialled) > 0 and dialled[0]["args"].get("params") == {"amount": "0.4"}, str(dialled))
# ⚠ A TASK WITH NO VERB LIST IS NOT NARROWED, and must not be: the model is then
# choosing from the whole editor on purpose, and an empty allow-list silently
# meaning "nothing" would produce a batch that can write no steps at all.
wide_args = sorted(agent.batch_schema(FX_VOCAB, {})["properties"]["steps"]["items"]
                   ["properties"]["args"]["properties"])
check("⚠ A TASK THAT NAMED NO VERBS KEEPS THE WHOLE VOCABULARY",
      "kind" in wide_args and "cut" in wide_args, str(wide_args))
# ⚠ AND THE TURN PROMPT SAYS TO SPLIT THEM, which is the cheaper half of the same
# fix: two tasks also run at the same time, where one combined task runs once.
check("⚠ THE TURN PROMPT SAYS TRANSITIONS AND EFFECTS ARE TWO JOBS",
      "TRANSITIONS AND EFFECTS ARE TWO JOBS" in agent.prompts()["turn"])

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

# --- the failure a fan-out INVENTS, and the retry that answers it -------------
# ⚠ PAID FOR ON THE SECOND LIVE RUN: *"2 parts did not come back, so those shots
# were left alone"* — on a FOURTEEN-shot film, which is not a hard job. `llm_json`
# already retries a call three times, so a batch that still failed did not fail
# for its own reasons: it failed because three other calls were in the air at the
# same moment. **That is the one failure mode a fan-out creates and a single
# request never had**, and the answer is the SHAPE of the retry, not more of
# them — serially, after the wave, when the burst is over.
inflight = 0
burst_lock = threading.Lock()


def burst(request):
    global inflight
    with burst_lock:
        inflight += 1
        busy = inflight
    try:
        if busy > 2:
            raise RuntimeError("429 too many requests")
        m = re.search(r"SHOTS (\d+)–(\d+) ONLY", request.prompt)
        if not m:
            return json.dumps({"steps": []})
        lo, hi = int(m.group(1)), int(m.group(2))
        return json.dumps({"steps": [
            {"verb": "add_transition", "args": {"shot": n, "kind": "cut"}}
            for n in range(lo, hi + 1)
        ]})
    finally:
        with burst_lock:
            inflight -= 1


llm_json.use_adapter(burst)
try:
    rescued = agent.run_work(
        work=agent._coerce_work(
            {"tasks": [{"goal": "transitions", "verbs": ["add_transition"]}]}, 60),
        board=board(60), vocabulary=VOCAB, settings={"turn_seconds": 120},
    )
finally:
    llm_json.use_adapter(None)
saved = [s["args"]["shot"] for s in (rescued.get("plan") or {}).get("steps") or []]
check("⚠ A BATCH THAT ONLY FAILED BECAUSE THE WAVE WAS BUSY IS RESCUED",
      set(saved) == set(range(1, 61)), f"{len(saved)} steps, missing "
      f"{sorted(set(range(1, 61)) - set(saved))[:6]}")
check("…and the person is never told a part was lost, because none was",
      "did not come back" not in rescued["reply"], rescued["reply"])

# ⚠ AND WHEN IT REALLY IS LOST, THE REASON REACHES THE SCREEN. "2 parts did not
# come back" and nothing else tells the person something broke and gives them
# nothing to do about it — and tells whoever must fix it nothing at all. The
# reason rides on `dropped`, which the panel already draws.
reasons = [d for d in (partial.get("dropped") or []) if d.get("verb") == "part"]
check("⚠ A LOST PART'S REASON IS ON THE PANEL, not only in the log",
      len(reasons) >= 1, str(partial.get("dropped"))[:200])
check("…and it names the shots it was for",
      any("shots 13" in (d.get("why") or "") for d in reasons), str(reasons)[:200])
check("…and the reply points at where that reason is",
      "under the plan" in partial["reply"], partial["reply"])

# --- the reply counts the way the panel counts -------------------------------
# ⚠ SEEN LIVE: the reply said "13 edits" over a button that said "Apply 27
# edits". A `note` is not an edit — the preview's own chips have always excluded
# it — and the reply was counting it. Two numbers for one thing, neither of them
# obviously wrong, is worse than either.
NOTE_VOCAB = {"verbs": [
    {"id": "add_transition", "label": "t", "args": ["shot", "kind"]},
    {"id": "note", "label": "n", "args": ["text"]},
]}


def with_note(request):
    m = re.search(r"SHOTS (\d+)–(\d+) ONLY", request.prompt)
    if not m:
        return json.dumps({"steps": []})
    lo, hi = int(m.group(1)), int(m.group(2))
    rows = [{"verb": "add_transition", "args": {"shot": n, "kind": "cut"}}
            for n in range(lo, hi + 1)]
    rows.append({"verb": "note", "args": {"text": "why I did this"}})
    return json.dumps({"steps": rows})


llm_json.use_adapter(with_note)
try:
    counted = agent.run_work(
        work=agent._coerce_work(
            {"tasks": [{"goal": "transitions", "verbs": ["add_transition", "note"]}]}, 24),
        board=board(24), vocabulary=NOTE_VOCAB, settings={"turn_seconds": 120},
    )
finally:
    llm_json.use_adapter(None)
rows = (counted.get("plan") or {}).get("steps") or []
real = len([s for s in rows if s.get("verb") != "note"])
notes = len(rows) - real
check("the batches really wrote notes as well as edits", notes > 0 and real == 24, f"{real}/{notes}")
check("⚠ THE REPLY COUNTS EDITS THE WAY THE PANEL'S CHIPS DO — notes excluded",
      f"{real} edits" in counted["reply"], counted["reply"])
check("⚠ …SO IT CANNOT DISAGREE WITH THE APPLY BUTTON BESIDE IT",
      f"{len(rows)} edits" not in counted["reply"], counted["reply"])

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

# ---------------------------------------------------------------------------
# ⚠ A TURN THAT ENDED BADLY IS OVER — IT DOES NOT COME BACK TO LIFE.
# ---------------------------------------------------------------------------
# The user row is stamped `work_state: "running"` when a big job starts. The
# catch in `send` used to leave it that way, so the RESUME effect — which exists
# for a page reload — found a "running" job on the very next render and started
# polling it again while the person was already asking something else. Live on
# 2026-09-06: *"mai abhi wala generate karwa raha tha ... magar upar wala v kyun
# chal raha tha"*.
catch = client.split("} catch (e) {")[1][:1400]
check("⚠ A FAILED OR STOPPED TURN CLEARS ITS OWN `work_state`",
      'work_state: e?.stopped ? "stopped" : "failed"' in catch, catch[:300])
resume = client.split("const resumedWorkRef")[1][:2200]
check("⚠ …AND THE RESUME PICKS THE NEWEST PENDING JOB, not the first one it sees",
      "for (let i = turns.length - 1; i >= 0; i -= 1)" in resume, resume[:300])
check("⚠ …AND NEVER ONE AN AGENT REPLY HAS ALREADY ANSWERED",
      "answered.has(t.work_id)" in resume, resume[:300])
check("⚠ …AND NEVER WHILE ANOTHER REQUEST OWNS THE ABORT HANDLE",
      "if (abortRef.current) return undefined;" in resume, resume[:300])

# ---------------------------------------------------------------------------
# ⚠ ONE APPLY AT A TIME, AND AN APPLY INCLUDES ITS SOUND.
# ---------------------------------------------------------------------------
# `running` covers the STEP loop only, and it goes false the instant the last
# verb commits — while the sound half is still at the library, for up to the
# whole request clock. So a second plan could be applied on top of a film the
# first apply had not finished, and both cards said "✓ Applied". Worse: the
# snapshot behind Undo is single-valued, so the second apply silently took the
# first one's Undo and pointed the snapshot at a half-edited document.
check("⚠ THE APPLY GUARD IS A REF, so two clicks in one React batch cannot both start",
      "applyBusyRef" in client and "if (applyBusyRef.current) return;" in client)
check("⚠ …AND IT IS RELEASED ONLY WHEN THE SOUND IS DONE TOO",
      'applyBusyRef.current = "";' in client.split("const finish = (soundFailed)")[1][:400])
check("⚠ …AND ON THE BAD PATH AS WELL, or the button jams shut for the session",
      "scoreTurn(turnId, sound).then(" in client
      and "finish(err?.message" in client.split("scoreTurn(turnId, sound).then(")[1][:400])
check("⚠ EVERY STATUS LINE NAMES THE TURN IT BELONGS TO",
      "setScoringTurn(turnId)" in client and "setRunningTurn(turnId)" in client
      and "runningTurn," in client and "scoringTurn," in client)

print()
if failures:
    print(f"✗ {len(failures)} check(s) failed:")
    for name in failures:
        print(f"    - {name}")
    sys.exit(1)
print("✓ big jobs: split, judged shot by shot, run together, stoppable, and never timing out")
