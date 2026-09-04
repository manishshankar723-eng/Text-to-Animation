"""THE ✨ AI EDITOR CHAT — one reply becomes one of three turns, and a malformed
one becomes a usable turn rather than an exception in somebody's conversation.

    python tests/editor_chat_check.py

⚠ **THE NEGATIVE HALF IS THE POINT OF THIS FILE**, exactly as it is in
`director_actions_check.py`. That a well-formed `ask` comes back as an ask is
worth one line; that an ask with one option, or no question, or four duplicate
labels, comes back as something the panel can still draw is worth the rest. A
model will send all four of those — that is not a hypothetical, it is what models
do — and a chat that throws on one has eaten the user's message along with it.

⚠ **AND IT PINS THE ASK TRIGGERS TO THE PROMPT.** `ASK_REASONS` in
`chat_turn.js` and the three triggers taught in the `editor_chat:` block of
`prompts.yaml` are one contract in two files: the prompt is what makes the model
ask, the constant is what the panel and the schema can name. A trigger reworded
in one and not the other is a rail that quietly stops firing, so section 5 reads
both and asserts they agree.

Sections:
    1. The three kinds, from a well-formed reply           (node)
    2. Everything a model gets wrong about an `ask`        (node)
    3. A plan is checked against the PROJECT, not itself   (node)
    4. The cut verbs, and the renumbering trap they avoid  (node)
    5. The sound that rides beside the steps               (node)
    6. Dead air and filler, measured for free              (node)
    7. The triggers in the prompt and in the code agree    (python)
    8. The server reads a turn the same way the client does (python)
    9. The admin store clamps what an operator types        (python)
   10. The JS twin of captions.py has not drifted          (python)

Needs node for 1–6. Nothing here touches a browser, a backend or a dollar.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"

# ⚠ EVERY LOCAL STORE PATH IS PINNED TO A TEMP DIR BEFORE ANY SERVER IMPORT.
# RULEBOOK G13 was paid for exactly here: `API_LOCAL_USAGE_PATH` defaults to a
# git-tracked file in the repo root, and a test run spent the developer's own
# quota. A test must never be able to write into the working tree.
_TMP = tempfile.mkdtemp(prefix="editor_chat_check_")
os.environ["API_USER_STORE"] = "local"
for _var in (
    "API_LOCAL_USAGE_PATH",
    "API_LOCAL_USERS_PATH",
    "API_LOCAL_JOBS_PATH",
    "API_LOCAL_FEATURES_PATH",
    "API_LOCAL_TIERS_PATH",
    "API_LOCAL_CHAT_SETTINGS_PATH",
):
    os.environ[_var] = os.path.join(_TMP, _var.lower() + ".json")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The node harness
# ---------------------------------------------------------------------------
# ⚠ THE FIXTURE IS FOUR SHOTS, which is the same shape `director_actions_check`
# uses and for the same reason: a plan validated against the project needs enough
# shots for "shot 9" to be wrong about.
HARNESS = """
import { normaliseTurn, answerText, wireMessages, ASK_REASONS, MAX_OPTIONS } from "<<TURN>>";
import { capabilities } from "<<CAPS>>";
import { ACTIONS, validateStep, describeStep } from "<<ACTIONS>>";
import {
  DEAD_AIR_MS, FILLER_WORDS, MIN_SILENCE_MS, MIN_SOUND_SHARE, MIN_SPEECH_MS,
  deadAir, fillerLines, spansFromEnvelope, speechDigest,
} from "<<SPEECH>>";

const caps = capabilities();
const frames = [
  { id: "f1", label: "Street", duration_ms: 3000 },
  { id: "f2", label: "Door",   duration_ms: 2000 },
  { id: "f3", label: "Room",   duration_ms: 4000 },
  { id: "f4", label: "Face",   duration_ms: 4000 },
];
const ctx = {
  frames,
  starts: [0, 3000, 5000, 9000],
  texts: [], shapes: [], overlays: [], transitions: [], audioTracks: [],
  totalMs: 13000,
  readTransitions: () => [],
};

const T = (raw) => normaliseTurn(raw, caps, ctx);
const out = {};

// ------------------------------------------------------------ 1. the kinds
out.answer = T({ kind: "answer", reply: "Forty-eight shots." });
out.ask = T({
  kind: "ask",
  reply: "",
  ask: {
    question: "What kind of music?",
    reason: "target",
    options: [
      { label: "Soft piano", note: "for the quiet scenes" },
      { label: "Dhol", note: "celebration mood" },
    ],
  },
});
out.plan = T({
  kind: "plan",
  reply: "A dissolve on each scene change.",
  plan: { summary: "Soften the cuts", steps: [
    { verb: "add_transition", args: { cut: 1, kind: "dissolve", duration_ms: 500 } },
    { verb: "add_transition", args: { cut: 2, kind: "dissolve", duration_ms: 500 } },
  ] },
});

// -------------------------------------------- 2. what a model gets wrong
// One option is a decision wearing a question's clothes.
out.oneOption = T({ kind: "ask", reply: "Adding music.",
  ask: { question: "Which?", options: [{ label: "Piano" }] } });
// No question at all.
out.noQuestion = T({ kind: "ask", reply: "Hmm.",
  ask: { question: "", options: [{ label: "A" }, { label: "B" }] } });
// Duplicate labels are two chips nobody can tell apart.
out.dupes = T({ kind: "ask",
  ask: { question: "Which?", options: [
    { label: "Piano" }, { label: "piano" }, { label: "Dhol" }] } });
// More than the panel can draw on one rail.
out.tooMany = T({ kind: "ask",
  ask: { question: "Which?", options: [
    { label: "A" }, { label: "B" }, { label: "C" }, { label: "D" },
    { label: "E" }, { label: "F" }] } });
// A model that answered with a bare option list and no `kind`.
out.inferred = T({ ask: { question: "Which?",
  options: [{ label: "A" }, { label: "B" }] } });
// Nothing usable at all.
out.empty = T({});
out.garbage = T(null);
// Option ids are ours, whatever the model called them.
out.ids = (out.dupes.turn.ask?.options || []).map((o) => o.id);

// ------------------------------- 3. a plan is checked against the PROJECT
// "Shot 9" is well-formed and nonsense on a four-shot film.
out.badShot = T({ kind: "plan", reply: "Title on nine.",
  plan: { steps: [{ verb: "add_text", args: { shot: 9, text: "Hi", ref: "t1" } }] } });
// An unknown verb.
out.badVerb = T({ kind: "plan", reply: "Doing it.",
  plan: { steps: [{ verb: "teleport", args: {} }] } });
// A kind this build cannot render leaves the cut straight.
out.badKind = T({ kind: "plan", reply: "Swirling.",
  plan: { steps: [{ verb: "add_transition", args: { cut: 1, kind: "swirl" } }] } });
// A plan whose every step died is an ANSWER, not an Apply button over nothing.
out.deadPlan = out.badVerb.turn.kind;

// ---------------------------------------------------------- the helpers
out.answerText = answerText(out.ask.turn.ask, out.ask.turn.ask.options[1]);
out.wire = wireMessages(
  [
    { role: "user", text: "one", plan: { steps: [1, 2, 3] } },
    { role: "agent", text: "two", drops: ["x"] },
    { role: "user", text: "   " },
  ],
  20
);
out.wireKeys = Object.keys(out.wire[0] || {});
out.trimmed = wireMessages(
  Array.from({ length: 40 }, (_, i) => ({ role: "user", text: `m${i}` })),
  6
).length;
out.reasons = ASK_REASONS;
out.maxOptions = MAX_OPTIONS;

// ------------------------------------------------ 5. the cut verbs (phase 3)
// ⚠ THE PROPERTY WORTH PINNING IS THE FRAME ID, not the arithmetic. A plan that
// deletes shot 3 renumbers every shot after it, so a later step saying "shot 5"
// would land on the wrong picture and report success. The structural verbs
// resolve the number ONCE, in validate, and carry an id.
const S = (verb, args) => validateStep({ verb, args }, caps, ctx);

out.cut = {
  splitOk: S("split_shot", { shot: 3, at_ms: 1500 }),
  // ⚠ A SHOT SHORT ENOUGH TO BE UNHALVABLE, which the four-shot fixture does
  // not contain: `HOUSE_CAPS.MIN_CLIP_MS` is 200, so even the 2s shot splits
  // fine. The first version of this check asserted against shot 2 and was
  // asserting nothing. 300ms is under 2×200 and is a real hold a person can make.
  splitShort: validateStep(
    { verb: "split_shot", args: { shot: 1, at_ms: 100 } },
    caps,
    { ...ctx, frames: [{ id: "tiny", label: "Blink", duration_ms: 300 }], starts: [0] }
  ),
  splitNoPoint: S("split_shot", { shot: 3, at_ms: "soon" }),
  splitBadShot: S("split_shot", { shot: 9, at_ms: 1000 }),
  trimOk: S("trim_shot", { shot: 3, by_ms: 1000 }),
  trimLonger: S("trim_shot", { shot: 3, by_ms: -1000 }),
  trimZero: S("trim_shot", { shot: 3, by_ms: 0 }),
  deleteOk: S("delete_shot", { shot: 2 }),
};
out.cut.describe = {
  split: describeStep({ verb: "split_shot", args: out.cut.splitOk.args }, ctx),
  trim: describeStep({ verb: "trim_shot", args: out.cut.trimOk.args }, ctx),
  longer: describeStep({ verb: "trim_shot", args: out.cut.trimLonger.args }, ctx),
  del: describeStep({ verb: "delete_shot", args: out.cut.deleteOk.args }, ctx),
};

// ⚠ THE LAST SHOT CANNOT BE DELETED. An empty timeline has no read-model, so the
// NEXT turn would be answering questions about a film that no longer exists.
const lone = { ...ctx, frames: [ctx.frames[0]], starts: [0] };
out.cut.deleteLast = validateStep({ verb: "delete_shot", args: { shot: 1 } }, caps, lone);

// ⚠ AND `run` MUST REFUSE A CLIP THAT HAS GONE, rather than deleting whatever
// now sits at that index. This is the failure the id exists to prevent, so it is
// worth driving rather than reasoning about.
function stubOf() {
  const calls = [];
  const api = new Proxy({}, { get: (_t, name) => (...a) => calls.push({ name, args: a }) });
  return { api, calls };
}
const gone = { ...ctx, frames: ctx.frames.filter((f) => f.id !== "f2") };
{
  const { api, calls } = stubOf();
  let threw = "";
  try {
    ACTIONS.delete_shot.run({ api, args: out.cut.deleteOk.args, ctx: gone, refs: {} });
  } catch (e) { threw = e.message; }
  out.cut.deleteGone = { threw, calls: calls.length };
}
// The happy path still calls through, by id.
{
  const { api, calls } = stubOf();
  ACTIONS.delete_shot.run({ api, args: out.cut.deleteOk.args, ctx, refs: {} });
  out.cut.deleteRan = calls;
}
// ⚠ AND THE SPLIT'S ABSOLUTE TIME IS READ AT RUN TIME. Shot 3 starts at 5000 in
// the fixture, so a cut 1.5s into it is 6500 on the film's clock — and if an
// earlier step had re-timed shot 1, it would be a different number.
{
  const { api, calls } = stubOf();
  ACTIONS.split_shot.run({ api, args: out.cut.splitOk.args, ctx, refs: {} });
  out.cut.splitRan = calls;
}

// -------------------------------------------------- 5. the sound (phase 3b)
// ⚠ SOUND IS NOT A VERB AND CANNOT BE ONE — every verb is synchronous, and
// finding a sound is a round trip to a stock library. So it rides BESIDE the
// steps and the runner fetches it after they have committed.
out.sound = {
  ok: T({
    kind: "plan",
    reply: "A bell on the temple shot, and something soft underneath.",
    plan: { steps: [{ verb: "add_transition", args: { cut: 1, kind: "dissolve" } }] },
    sound: {
      sfx: [{ shot: 2, query: "temple bell" }, { shot: 3, query: "market crowd" }],
      music: { query: "soft sitar", mood: "warm" },
    },
  }),
  // ⭐ SOUND ALONE IS A PLAN. "Put some music under it" writes no steps at all,
  // and reading that as an `answer` would draw a bubble where Apply belongs.
  only: T({
    kind: "answer",
    reply: "Music it is.",
    sound: { music: { query: "soft sitar" } },
  }),
  // A cue for a shot the film does not have.
  badShot: T({ kind: "plan", reply: "x", sound: { sfx: [{ shot: 9, query: "bell" }] } }),
  // Two cues on one shot are two files starting at the same instant.
  dupeShot: T({
    kind: "plan", reply: "x",
    sound: { sfx: [{ shot: 2, query: "bell" }, { shot: 2, query: "crowd" }] },
  }),
  // ⚠ THE BUDGET IS SHARED ACROSS THE WHOLE DEPLOYMENT — 60 requests a minute.
  // ⚠ A FOURTEEN-SHOT FILM, NOT THE FOUR-SHOT FIXTURE, and the difference is the
  // whole check. On four shots the one-cue-per-shot rule bites first and only
  // four cues ever survive — so the assertion passed while proving nothing about
  // the budget. The shared Freesound ceiling is only reachable when there are
  // more shots than distinct sounds allowed.
  overCap: normaliseTurn(
    {
      kind: "plan", reply: "x",
      sound: {
        sfx: Array.from({ length: 14 }, (_, i) => ({ shot: i + 1, query: `sound ${i}` })),
      },
    },
    caps,
    {
      ...ctx,
      frames: Array.from({ length: 14 }, (_, i) => ({
        id: `b${i + 1}`, label: `S${i + 1}`, duration_ms: 2000,
      })),
      starts: Array.from({ length: 14 }, (_, i) => i * 2000),
    }
  ),
  // Nothing usable at all is not a plan.
  empty: T({ kind: "answer", reply: "Nothing to add.", sound: { sfx: [], music: {} } }),
};

// ------------------------------------------------ 6. dead air (phase 4)
// ⚠ THE ENVELOPE IS BUILT BY HAND so the answer is arithmetic, not a recording.
// One bucket is 20ms. Loud for 500ms, silent for 2s, loud for 500ms — one gap.
const loud = 1.0;
const quiet = 0.001;
function env(...runs) {
  const out = [];
  for (const [level, ms] of runs) {
    for (let i = 0; i < Math.round(ms / 20); i++) out.push(level);
  }
  return out;
}

out.speech = {
  consts: { DEAD_AIR_MS, MIN_SILENCE_MS, MIN_SPEECH_MS, MIN_SOUND_SHARE },
  fillerWords: FILLER_WORDS,

  // Two runs of speech with two seconds of nothing between them.
  simple: deadAir({
    envelope: env([loud, 500], [quiet, 2000], [loud, 500]),
    hopMs: 20,
    durationMs: 3000,
  }),

  // ⚠ A SHORT GAP IS NOT DEAD AIR. 300ms is a breath between sentences; a chat
  // that offered to remove forty of those has offered to ruin the reading.
  breath: deadAir({
    envelope: env([loud, 500], [quiet, 300], [loud, 500]),
    hopMs: 20,
    durationMs: 1300,
  }),

  // ⚠ AIR BEFORE THE FIRST WORD IS THE COMMONEST DEAD AIR THERE IS.
  leading: deadAir({
    envelope: env([quiet, 3000], [loud, 1000]),
    hopMs: 20,
    durationMs: 4000,
  }),

  // ⚠ AND A MEASUREMENT THAT LOOKS LIKE 95% SILENCE IS NOT BELIEVED. Proposing
  // to cut most of somebody's voiceover off a bad reading is the worst outcome
  // this whole feature has available to it.
  untrusted: deadAir({
    envelope: env([loud, 200], [quiet, 9800]),
    hopMs: 20,
    durationMs: 10000,
  }),

  // A truly silent file — every bucket zero.
  silent: deadAir({ envelope: env([0, 4000]), hopMs: 20, durationMs: 4000 }),
  // ⚠ A CONSTANT HISS IS NOT SILENCE TO AN ENERGY ENVELOPE, and it never can
  // be: the threshold is derived FROM the track, so a track with no dynamic
  // range is one unbroken run of "sound". `captions.py` behaves identically
  // and for the same reason — this is pinned so the shared limit is on the
  // record rather than being rediscovered as a bug.
  hiss: deadAir({ envelope: env([quiet, 4000]), hopMs: 20, durationMs: 4000 }),
  nothing: deadAir({}),

  // The two clean-ups `spansFromEnvelope` shares with captions.py.
  joined: spansFromEnvelope(env([loud, 300], [quiet, 100], [loud, 300]), 20).length,
  blip: spansFromEnvelope(env([quiet, 500], [loud, 60], [quiet, 500]), 20).length,
};

out.filler = {
  // ⚠ "MOSTLY", NOT "CONTAINS". A stumble at the front of a real sentence is a
  // real sentence, and deleting it deletes the line.
  lines: fillerLines([
    { id: "c1", text: "Umm...", start_ms: 1000 },
    { id: "c2", text: "matlab", start_ms: 2000 },
    { id: "c3", text: "Umm, I think we should go now", start_ms: 3000 },
    { id: "c4", text: "The machine is finished.", start_ms: 4000 },
    { id: "c5", text: "haan haan", start_ms: 5000 },
  ]).map((r) => r.id),
};

out.digest = {
  full: speechDigest({
    tracks: [{ name: "vo.wav", ...out.speech.simple }],
    fillers: [{ id: "c1", text: "Umm...", start_ms: 1000 }],
  }),
  untrusted: speechDigest({ tracks: [{ name: "vo.wav", ...out.speech.untrusted }] }),
  // ⚠ NOTHING TO SAY MUST COST NOTHING TO SAY. An empty heading is tokens spent
  // on every single turn to convey that there is no audio.
  none: speechDigest({ tracks: [], fillers: [] }),
};

process.stdout.write(JSON.stringify(out));
"""


def fill(template: str, values: dict) -> str:
    """`<<TOKEN>>` substitution. ⚠ **NOT `%`-FORMATTING, AND NOT `str.format`.**

    This harness is JavaScript, and JavaScript has `%` in it — a modulo in the
    code, a percentage in a comment. Under `%`-formatting every one of those is a
    format specifier, so adding an ordinary line of JS makes the TEMPLATE fail to
    render, with a `TypeError` that points at this function and says nothing
    whatsoever about JavaScript. It cost three debugging rounds in one sitting.
    `str.format` has the same problem with `{}`, which JS has even more of.

    The same reason `director._fill` exists, and the same answer.
    """
    out = template
    for name, value in values.items():
        out = out.replace(f"<<{name}>>", value)
    return out


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="editor_chat_")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(fill(HARNESS, {
                "TURN": (AGENT / "chat_turn.js").as_uri(),
                "CAPS": (AGENT / "capabilities.js").as_uri(),
                "ACTIONS": (AGENT / "actions.js").as_uri(),
                "SPEECH": (AGENT / "speech.js").as_uri(),
            }))
        proc = subprocess.run(
            ["node", harness],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1500])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def client_checks(data: dict) -> None:
    print("\n1 · One reply becomes one of three turns\n")
    check("plain words are an answer", data["answer"]["turn"]["kind"] == "answer")
    check("the words survive", data["answer"]["turn"]["reply"] == "Forty-eight shots.")
    check("a question with options is an ask", data["ask"]["turn"]["kind"] == "ask")
    check(
        "the options survive with their notes",
        len(data["ask"]["turn"]["ask"]["options"]) == 2
        and data["ask"]["turn"]["ask"]["options"][1]["note"] == "celebration mood",
    )
    check(
        "⭐ allow_other is always true — 'if not these then what'",
        data["ask"]["turn"]["ask"]["allow_other"] is True,
    )
    check("steps make a plan", data["plan"]["turn"]["kind"] == "plan")
    check(
        "both steps survived",
        len(data["plan"]["turn"]["plan"]["steps"]) == 2,
        str(data["plan"]["drops"]),
    )

    print("\n2 · Everything a model gets wrong about an ask\n")
    check(
        "one option is not a question — it degrades to an answer",
        data["oneOption"]["turn"]["kind"] == "answer",
    )
    check(
        "…and it says why, on screen",
        any("at least" in d["why"] for d in data["oneOption"]["drops"]),
        str(data["oneOption"]["drops"]),
    )
    check(
        "a question with no words degrades too",
        data["noQuestion"]["turn"]["kind"] == "answer",
    )
    check(
        "duplicate labels are folded",
        len(data["dupes"]["turn"]["ask"]["options"]) == 2,
    )
    check(
        f"no more than {data['maxOptions']} options reach the panel",
        len(data["tooMany"]["turn"]["ask"]["options"]) == data["maxOptions"],
    )
    check(
        "…and the overflow is reported, not silent",
        any("options" in d["why"] for d in data["tooMany"]["drops"]),
    )
    check(
        "an unlabelled reply is read from its content",
        data["inferred"]["turn"]["kind"] == "ask",
    )
    check("ids are positional and ours", data["ids"] == ["o1", "o2"], str(data["ids"]))
    check(
        "an empty reply still produces a turn",
        data["empty"]["turn"]["kind"] == "answer" and bool(data["empty"]["turn"]["reply"]),
    )
    check("null does not throw", data["garbage"]["turn"]["kind"] == "answer")

    print("\n3 · A plan is checked against the project, not just itself\n")
    check(
        "shot 9 on a four-shot film is dropped",
        not data["badShot"]["turn"].get("plan"),
        str(data["badShot"]["drops"]),
    )
    check("an unknown verb is dropped", data["badVerb"]["turn"].get("plan") is None)
    check(
        "…and the reason names the verb",
        any("teleport" in d["why"] for d in data["badVerb"]["drops"]),
        str(data["badVerb"]["drops"]),
    )
    check("an unrenderable kind is dropped", data["badKind"]["turn"].get("plan") is None)
    check(
        "a plan with nothing left is an ANSWER, not an empty Apply button",
        data["deadPlan"] == "answer",
    )

    print("\n   the helpers\n")
    check(
        "a clicked option carries its question into the transcript",
        data["answerText"] == "What kind of music? — Dhol",
        data["answerText"],
    )
    check("blank messages are not sent", len(data["wire"]) == 2)
    check(
        "only role and text cross the wire",
        sorted(data["wireKeys"]) == ["role", "text"],
        str(data["wireKeys"]),
    )
    check("the transcript is trimmed from the front", data["trimmed"] == 6)

    print("\n4 · The cut verbs — and the renumbering trap they exist to avoid\n")
    cut = data["cut"]
    # ⭐ THE ONE THAT MATTERS. Without the id, "delete shot 3 then title shot 5"
    # puts the title on the wrong picture and reports success.
    check("⭐ a split resolves to a FRAME ID, not a number",
          cut["splitOk"]["ok"] and cut["splitOk"]["args"].get("frame_id") == "f3",
          str(cut["splitOk"]))
    check("⭐ …and so does a delete",
          cut["deleteOk"]["ok"] and cut["deleteOk"]["args"].get("frame_id") == "f2")
    check("a shot too short to halve is refused",
          not cut["splitShort"]["ok"], str(cut["splitShort"]))
    check("…with a reason a person can read",
          "too short" in (cut["splitShort"].get("why") or ""))
    check("a cut point that is not a number is refused", not cut["splitNoPoint"]["ok"])
    check("a shot that does not exist is refused", not cut["splitBadShot"]["ok"])

    # ⚠ RELATIVE, WHICH IS THE WHOLE REASON IT EXISTS BESIDE `set_shot_duration`.
    # Shot 3 holds 4000ms, so trimming 1000 leaves 3000 — not 1000.
    check("a trim is RELATIVE, not absolute",
          cut["trimOk"]["ok"] and cut["trimOk"]["args"]["ms"] == 3000,
          str(cut["trimOk"]))
    check("a negative trim lengthens",
          cut["trimLonger"]["ok"] and cut["trimLonger"]["args"]["ms"] == 5000,
          str(cut["trimLonger"]))
    check("trimming by nothing is refused", not cut["trimZero"]["ok"])

    check("the last shot standing cannot be deleted", not cut["deleteLast"]["ok"])
    check("…and it says why", "only shot" in (cut["deleteLast"].get("why") or ""))

    print("\n   what the person reads in the preview\n")
    check("a split names the shot and where", "Cut shot 3" in cut["describe"]["split"]
          and "1.5s" in cut["describe"]["split"], cut["describe"]["split"])
    check("a trim says how much comes off", "Trim 1.0s off shot 3" == cut["describe"]["trim"],
          cut["describe"]["trim"])
    check("…and a negative one says it HOLDS longer",
          "1.0s longer" in cut["describe"]["longer"], cut["describe"]["longer"])
    check("a delete names the shot", "Remove shot 2" in cut["describe"]["del"],
          cut["describe"]["del"])

    print("\n   and the run refuses a clip that has gone\n")
    check("⭐ deleting a clip that is no longer there THROWS",
          bool(cut["deleteGone"]["threw"]), str(cut["deleteGone"]))
    check("⭐ …and calls nothing, so nothing else is deleted in its place",
          cut["deleteGone"]["calls"] == 0)
    check("the happy path deletes BY ID",
          cut["deleteRan"] and cut["deleteRan"][0]["name"] == "deleteFrame"
          and cut["deleteRan"][0]["args"] == ["f2"], str(cut["deleteRan"]))
    # Shot 3 starts at 5000; a cut 1.5s in is 6500 on the film's clock.
    check("a split is handed the ABSOLUTE time, worked out live",
          cut["splitRan"] and cut["splitRan"][0]["name"] == "splitFrameAt"
          and cut["splitRan"][0]["args"] == [6500, "f3"], str(cut["splitRan"]))

    print("\n5 · The sound that rides beside the steps\n")
    s = data["sound"]
    check("cues and a bed survive a good turn",
          s["ok"]["turn"]["kind"] == "plan"
          and len(s["ok"]["turn"]["sound"]["sfx"]) == 2
          and s["ok"]["turn"]["sound"]["music"]["query"] == "soft sitar",
          str(s["ok"]["turn"].get("sound")))
    check("…and the steps came too", len(s["ok"]["turn"]["plan"]["steps"]) == 1)
    # ⭐ THE ONE THAT WOULD HAVE BEEN A DEAD END.
    check("⭐ sound with NO steps is still a plan, not an answer",
          s["only"]["turn"]["kind"] == "plan", str(s["only"]["turn"]))
    check("…and it carries an empty step list rather than no plan at all",
          s["only"]["turn"]["plan"]["steps"] == [], str(s["only"]["turn"].get("plan")))
    check("a cue for a shot that does not exist is dropped",
          not (s["badShot"]["turn"].get("sound") or {}).get("sfx"))
    check("…and says so on screen",
          any("no shot 9" in d["why"] for d in s["badShot"]["drops"]),
          str(s["badShot"]["drops"]))
    check("one cue per shot", len(s["dupeShot"]["turn"]["sound"]["sfx"]) == 1)
    check("…and the second is reported, not silent",
          any("already has a sound" in d["why"] for d in s["dupeShot"]["drops"]))
    # ⚠ THE PREVIEW MUST PROMISE WHAT THE PASS WILL ACTUALLY FETCH.
    check("the shared sound budget is enforced in the PREVIEW",
          len(s["overCap"]["turn"]["sound"]["sfx"]) <= 10,
          str(len(s["overCap"]["turn"]["sound"]["sfx"])))
    check("…and the refusals are counted",
          any("different sounds" in d["why"] for d in s["overCap"]["drops"]),
          str(s["overCap"]["drops"]))
    check("an empty sound block is not a plan", s["empty"]["turn"]["kind"] == "answer")

    print("\n6 · Dead air, filler, and what the model is told about sound\n")
    sp = data["speech"]
    check("two runs with 2s between them find one gap",
          len(sp["simple"]["gaps"]) == 1, str(sp["simple"]["gaps"]))
    check("…and it is measured, not guessed",
          1900 <= sp["simple"]["gaps"][0]["ms"] <= 2100, str(sp["simple"]["gaps"]))
    check("…and the track is trusted", sp["simple"]["trusted"] is True)
    # ⚠ THE LINE BETWEEN A BREATH AND DEAD AIR IS THE WHOLE JUDGEMENT HERE.
    check("a 300ms breath is NOT dead air", not sp["breath"]["gaps"],
          str(sp["breath"]["gaps"]))
    check("air before the first word counts",
          sp["leading"]["gaps"] and sp["leading"]["gaps"][0]["start_ms"] == 0,
          str(sp["leading"]["gaps"]))
    # ⭐ THE ONE THAT PREVENTS THE WORST OUTCOME AVAILABLE TO THIS FEATURE.
    check("⭐ a track that measures 95% silence is NOT trusted",
          sp["untrusted"]["trusted"] is False, str(sp["untrusted"])[:200])
    check("…and offers no gaps to cut", not sp["untrusted"]["gaps"])
    check("a truly silent file finds no speech at all",
          sp["silent"]["trusted"] is False and not sp["silent"]["spans"],
          str(sp["silent"])[:160])
    # ⚠ A SHARED LIMIT, PINNED RATHER THAN FIXED. An energy envelope cannot tell
    # constant hiss from constant speech, because its threshold is derived from
    # the track itself. `captions.py` does the same thing. What saves the user is
    # that it errs towards "this is all speech" — so nothing gets cut.
    check("constant hiss reads as sound, and so proposes NO cuts",
          not sp["hiss"]["gaps"], str(sp["hiss"]["gaps"]))
    check("no envelope at all does not throw", sp["nothing"]["trusted"] is False)
    check("a 100ms gap joins two runs into one", sp["joined"] == 1)
    check("a 60ms blip is not speech", sp["blip"] == 0)

    print("\n   filler captions — the screen, never the sound\n")
    check("a whole-line filler is found", "c1" in data["filler"]["lines"])
    check("…in Hinglish too", "c2" in data["filler"]["lines"]
          and "c5" in data["filler"]["lines"], str(data["filler"]["lines"]))
    # ⚠ A FALSE POSITIVE HERE IS A WORD DELETED FROM SOMEBODY'S FILM.
    check("⭐ a stumble at the front of a real line is LEFT ALONE",
          "c3" not in data["filler"]["lines"], str(data["filler"]["lines"]))
    check("an ordinary line is left alone", "c4" not in data["filler"]["lines"])

    print("\n   what the model is actually told\n")
    check("the digest names the track and its silence",
          "vo.wav" in data["digest"]["full"] and "%" in data["digest"]["full"],
          data["digest"]["full"])
    check("…and where the dead air is", "dead air" in data["digest"]["full"])
    check("…and says a caption removal is on screen only",
          "not out of the audio" in data["digest"]["full"])
    check("an unmeasurable track SAYS so rather than being omitted",
          "could not be told apart" in data["digest"]["untrusted"])
    check("no audio costs no tokens", data["digest"]["none"] == "")


# ---------------------------------------------------------------------------
# 4. The triggers, in the prompt and in the code
# ---------------------------------------------------------------------------
def prompt_checks(reasons: list[str]) -> None:
    print("\n7 · The ask triggers agree between the prompt and the code\n")
    import yaml

    block = (yaml.safe_load((ROOT / "prompts.yaml").read_text(encoding="utf-8")) or {}).get(
        "editor_chat"
    ) or {}
    check("prompts.yaml has an editor_chat block", bool(block))
    system = (block.get("system") or "").lower()
    check("…with a system instruction", len(system) > 500, f"{len(system)} chars")
    check("…and a turn template", bool(block.get("turn")))

    for token in ("<<BOARD>>", "<<VOCABULARY>>", "<<TRANSCRIPT>>", "<<RAILS>>"):
        check(f"the turn template fills {token}", token in (block.get("turn") or ""))

    check(
        "the three triggers are the three the code names",
        reasons == ["target", "spend", "destructive"],
        str(reasons),
    )
    for reason in reasons:
        check(f"the prompt teaches the “{reason}” trigger", reason in system)

    check("the prompt teaches sound as search terms", "search terms" in system)
    check("the prompt teaches what dead air can and cannot fix", "dead air" in system)
    check("…and that removing a filler caption leaves the audio alone",
          "leaves it in the sound" in system)

    # ⚠ THE RULE THAT IS NOT A TRIGGER, AND IS AS IMPORTANT AS THE THREE THAT
    # ARE. A bot that opens a question box on a clear instruction is a bot people
    # stop opening, so the prompt has to say so out loud.
    check(
        "the prompt also says when NOT to ask",
        "when not to ask" in system,
    )
    check(
        "the prompt forbids reporting an edit as done",
        "never report" in system or "never as a report" in system,
    )


# ---------------------------------------------------------------------------
# 5 and 6. The server side
# ---------------------------------------------------------------------------
def twin_checks() -> None:
    """⚠ `speech.js` IS A TWIN OF `captions.spans_from_envelope`, AND A TWIN THAT
    NOBODY CHECKS IS A TWIN THAT DRIFTS.

    The same rule the transitions, the effects and the shape kinds all carry in
    this repo. Every constant the two files share is read out of BOTH here — out
    of the Python by import, out of the JavaScript by reading the source — so a
    threshold retuned on one side and not the other is a failure rather than a
    caption box that quietly stops sitting on its wave.
    """
    print("\n10 · The JS twin of captions.spans_from_envelope has not drifted\n")
    import re

    import captions

    js = (ROOT / "client/src/animatic/agent/speech.js").read_text(encoding="utf-8")

    def js_const(name):
        m = re.search(rf"^export const {name} = ([0-9.]+);", js, re.M)
        return float(m.group(1)) if m else None

    for name in ("ENVELOPE_WINDOW_MS", "NOISE_FLOOR_MULTIPLE", "SOUND_PEAK_SHARE",
                 "MAX_THRESHOLD_SHARE", "MIN_SILENCE_MS", "MIN_SPEECH_MS",
                 "MIN_SOUND_SHARE"):
        theirs = getattr(captions, name, None)
        ours = js_const(name)
        check(f"{name} agrees ({theirs})", ours is not None and float(theirs) == ours,
              f"python={theirs} js={ours}")

    # ⚠ AND THE ONE THAT IS DELIBERATELY *NOT* SHARED. `DEAD_AIR_MS` has no twin:
    # captions.py separates a pause from a consonant, which is far too fine a line
    # for an edit decision. Asserted so nobody "fixes" it into agreement.
    assert js_const("DEAD_AIR_MS") is not None
    check("DEAD_AIR_MS is deliberately NOT captions.py's MIN_SILENCE_MS",
          js_const("DEAD_AIR_MS") > captions.MIN_SILENCE_MS,
          f"{js_const('DEAD_AIR_MS')} vs {captions.MIN_SILENCE_MS}")


def server_checks() -> None:
    print("\n8 · The server reads a turn the same way the client does\n")
    import editor_chat_agent as agent

    ask = agent._coerce_ask({
        "question": "Which?",
        "reason": "spend",
        "options": [{"label": "A", "note": "n"}, {"label": "B"}],
    })
    check("a good ask is read", bool(ask) and len(ask["options"]) == 2)
    check("the reason survives", ask["reason"] == "spend")
    check("one option is not an ask", agent._coerce_ask({"question": "Q", "options": [{"label": "A"}]}) is None)
    check("no question is not an ask", agent._coerce_ask({"question": "", "options": [1, 2]}) is None)
    check("garbage is not an ask", agent._coerce_ask(None) is None)
    good = agent._coerce_sound({
        "sfx": [{"shot": 2, "query": "temple bell"}, {"shot": 2, "query": "again"}],
        "music": {"query": "soft sitar", "mood": "warm"},
    })
    check("the server reads a sound block", bool(good))
    check("…one cue per shot, there too", len(good["sfx"]) == 1, str(good["sfx"]))
    check("…and one bed", good["music"]["query"] == "soft sitar")
    check("a shot number that is not a number is dropped",
          (agent._coerce_sound({"sfx": [{"shot": "two", "query": "bell"}]}) or {"sfx": []})["sfx"] == [])
    check("an empty block is None, not an empty plan",
          agent._coerce_sound({"sfx": [], "music": {}}) is None)
    check("garbage is None", agent._coerce_sound(None) is None)
    check("the reply schema offers a sound block",
          "sound" in agent.reply_schema({"verbs": []})["properties"])

    check(
        "an invented reason is dropped, not passed on",
        (agent._coerce_ask({"question": "Q", "reason": "vibes",
                            "options": [{"label": "A"}, {"label": "B"}]}) or {}).get("reason") == "",
    )

    # ⚠ THE DIGEST IS WHAT THE BILL IS MADE OF. A long film must summarise rather
    # than truncate: a digest that simply stopped at shot 60 would have the model
    # confidently discussing a film that ends there.
    long_board = {
        "title": "Long one",
        "total_ms": 600000,
        "shots": [{"label": f"S{i}", "ms": 2000, "description": "x"} for i in range(200)],
        "existing": {"transitionCuts": [], "texts": 0, "shapes": 0, "audioTracks": 0},
    }
    digest = agent.board_digest(long_board, 60)
    check("a 200-shot film is summarised, not listed", digest.count("\n") < 40, f"{digest.count(chr(10))} lines")
    check("…and it says how many were left out", "more shots" in digest)
    check("…and it still states the true total", "200 shot(s)" in digest)

    short = agent.board_digest(
        {"title": "T", "total_ms": 9000, "shots": [{"label": "A", "ms": 3000}],
         "existing": {}}, 60
    )
    check("a short film is listed shot by shot", "1. [3.0s] A" in short, short[-80:])

    rails_on = agent.rails_text({"ask_on_spend": True, "ask_on_destructive": True})
    rails_off = agent.rails_text({"ask_on_spend": False, "ask_on_destructive": False,
                                  "allow_paid_passes": True})
    check("the rails are stated when on", "ALWAYS ask before anything that spends" in rails_on)
    # ⚠ A RAIL THAT IS OFF MUST STILL BE MENTIONED, or the model keeps obeying the
    # system prompt's "ask every time" and the setting looks broken.
    check("…and their absence is stated too", "need not ask" in rails_off)
    check(
        "a deployment that forbids paid work says so",
        "do not offer to start one" in agent.rails_text({}),
    )

    vocab = {"verbs": [{"id": "add_text", "label": "Add a caption", "args": ["shot"], "creates": True}],
             "transitions": [{"id": "dissolve", "label": "Dissolve"}],
             "easings": ["linear"], "animatable": {"frame": ["scale"]}}
    trimmed = agent._vocabulary_for_prompt(vocab)
    check("the verb gloss is the registry's label", trimmed["verbs"][0]["does"] == "Add a caption")
    check("a creating verb is marked, so refs are not forward-referenced",
          trimmed["verbs"][0].get("creates") is True)
    # ⚠ THE FULL MANIFEST STILL BUILDS THE SCHEMA. What is trimmed is only the
    # prose the model READS to choose — the constraint is untouched.
    check("easings are trimmed out of the prose", "easings" not in trimmed)
    check("the schema still constrains every verb",
          "add_text" in json.dumps(agent.reply_schema(vocab)))

    print("\n9 · The admin store clamps what an operator types\n")
    from server import chat_settings as cs

    check("a bad dock falls back", cs.clean({"dock": "moon"})["dock"] == cs.DOCK_RIGHT)
    check("a real dock is kept", cs.clean({"dock": cs.DOCK_SIDE})["dock"] == cs.DOCK_SIDE)
    # ⚠ THE FLOATING WINDOW IS THE ONE THAT MOVES, and its id goes straight into
    # a CSS class (`ec-dock-float`) — so it has to be in the list AND described,
    # or the admin screen draws a radio button with no words beside it.
    check("the floating window is a real dock", cs.clean({"dock": cs.DOCK_FLOAT})["dock"] == cs.DOCK_FLOAT)
    check("…and every dock is described for the admin screen",
          all(cs.DOCK_INFO.get(d, {}).get("label") for d in cs.DOCKS))
    # ⚠ HOW SEE-THROUGH THE PANEL IS — the operator's, and clamped like every
    # other number on that screen. ⚠ **THE WHOLE RANGE IS OPEN AND THAT IS A
    # DECISION, NOT AN OVERSIGHT.** The floor was 40 for one day, picked off the
    # DARK theme; the light theme is a white panel over a near-white page, so at
    # 40 it looked solid and the report was *"white mai to ho hi nhi raha hai"*.
    # A floor set by how one theme happens to look lies about the other one.
    check("the panel ships solid", cs.defaults()["opacity"] == 100)
    check("⚠ it goes all the way to invisible — the operator judges, not the store",
          cs.clean({"opacity": 0})["opacity"] == 0)
    check("…and a negative still cannot get through", cs.clean({"opacity": -20})["opacity"] == 0)
    check("…and cannot go over solid", cs.clean({"opacity": 400})["opacity"] == 100)
    check("nonsense opacity falls back to solid", cs.clean({"opacity": "faint"})["opacity"] == 100)
    check("every value in between is kept as typed", cs.clean({"opacity": 35})["opacity"] == 35)
    # ⚠ THE BLUR IS A SLIDER BECAUSE THE CONSTANT WAS WRONG TWICE — 16px baked in
    # (invisible in the light theme), then removed (unreadable at low opacity).
    # It ships at 0, which is exactly what is on screen, so no deployment changes
    # under anyone; the operator raises it when they lower the solidity.
    check("blur ships off, so nothing changes underneath a deployment",
          cs.defaults()["blur"] == 0)
    check("a runaway blur is clamped", cs.clean({"blur": 500})["blur"] == 40)
    check("…and a negative cannot get through", cs.clean({"blur": -9})["blur"] == 0)
    check("a real blur is kept as typed", cs.clean({"blur": 16})["blur"] == 16)
    check("the admin screen is handed the blur bounds too",
          "blur" in cs.admin_payload()["limits"])
    # ⚠ THE NUMBER THAT IS THE BILL. A fat-fingered 2000 here is a prompt nobody
    # meant to send, on every turn, for every customer.
    check("a runaway transcript is clamped", cs.clean({"transcript_keep": 5000})["transcript_keep"] == 60)
    check("a negative is clamped up", cs.clean({"transcript_keep": -3})["transcript_keep"] == 4)
    check("nonsense falls back to the default", cs.clean({"transcript_keep": "many"})["transcript_keep"] == 20)
    check("unknown keys are ignored", "hack" not in cs.clean({"hack": 1}))
    check("the rails default ON", cs.defaults()["ask_on_spend"] and cs.defaults()["ask_on_destructive"])
    # ⚠ PAID PASSES DEFAULT **ON** SINCE 2026-09-03, AND THE REASON THE OLD
    # ASSERTION WAS WRONG IS WORTH KEEPING. It read "a default-on tick box is a
    # box nobody looks at, and the first time anyone looked at this one it would
    # be on an invoice" — which would be right if the flag could spend. It
    # cannot: nothing in this feature spends anything but text quota, and no
    # client code reads this flag at all. All it ever controlled was one sentence
    # in `rails_text`, and switched off that sentence was *"this deployment does
    # NOT let the chat start paid work at all — do not offer to start one"*. So
    # the chat went quiet about the three most valuable things the app does: ask
    # it for a voiceover and it changed the subject. Asked for outright: *"jo
    # free hai wo free mai hoga aur jismai paisa lagta hai usmai paisa lagega"*.
    check("paid passes default ON", cs.defaults()["allow_paid_passes"] is True)
    # ⚠ AND ON STILL MEANS "MAY OFFER", NEVER "MAY START". The wording is the
    # whole safety property here, because the model does what the rails say: told
    # it may "start" paid work it reports a render it has not begun.
    allowed = agent.rails_text({"allow_paid_passes": True})
    check("…and the rails say it may OFFER paid work", "MAY offer paid work" in allowed, allowed)
    check("…and may NOT start it", "may not start it" in allowed, allowed)
    check("…and name the door instead of a price",
          "🎬 Make Video" in allowed and "never" in allowed and "quote a price" in allowed,
          allowed)
    check("a stored row is re-cleaned on the way out",
          cs.get_settings(fresh=True)["transcript_keep"] == 20)

    payload = cs.admin_payload()
    check("the admin payload carries the bounds", "transcript_keep" in (payload.get("limits") or {}))
    check("…and names the other two owners rather than copying them",
          payload["feature_key"] == "cap.editor-chat" and payload["limit_key"] == "chat_turns")

    # ⚠ THE COUNTER'S NAME IS THE TIER'S LIMIT KEY. `server/usage.py` states the
    # rule; this is what stops the two drifting into a limit nobody advertised.
    from server import usage, billing
    check("chat_turns is a counter", "chat_turns" in usage.COUNTERS)
    seeded = [t for t in billing._CATALOG if "chat_turns" in (t.get("limits") or {})]
    check("every shipped tier seeds an allowance", len(seeded) == len(billing._CATALOG),
          f"{len(seeded)}/{len(billing._CATALOG)}")
    check("…and the top tier is unlimited (None), not zero",
          billing._CATALOG[-1]["limits"]["chat_turns"] is None)

    from server import features
    check("the chat has its own capability key", "cap.editor-chat" in features._catalog())
    check("…and it is not folded into the Director's",
          "cap.director" in features._catalog())


def main() -> int:
    data = run_node()
    if data is None:
        print("  node is not on PATH, or the agent modules would not load — sections 1–6 skipped.")
        reasons = ["target", "spend", "destructive"]
    else:
        client_checks(data)
        reasons = data["reasons"]

    prompt_checks(reasons)
    server_checks()
    twin_checks()

    print()
    if failures:
        print(f"✗ {len(failures)} check(s) failed:")
        for name in failures:
            print(f"    - {name}")
        return 1
    print("✓ all checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
