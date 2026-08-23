"""THE DIRECTOR'S HANDS — every verb resolves, every legal word is accepted, and
every illegal one is DROPPED rather than thrown.

    python tests/director_actions_check.py

⚠ THE NEGATIVE HALF IS THE POINT OF THIS FILE. That a `dissolve` is accepted is
worth one line; that a `swirl` leaves the cut straight and the run carrying on is
worth the other forty. A model will propose a kind this build has never heard of
— that is not a hypothetical, it is what models do — and there are exactly two
ways to handle it: stop the run, or drop the step. Forty-seven good edits and one
plain cut is a usable film. A plan that died on step 12 is not, and the user
cannot tell from looking at the timeline which half happened.

So this drives `validateStep` and `validatePlan` through every wrong shape a plan
can take — an unknown verb, an unknown kind, a shot that does not exist, a cut
that is not between two shots, a parameter belonging to a different transition, a
ref nothing creates — and asserts on the REASON each one comes back with, because
a drop the user cannot read is only marginally better than a crash.

⚠ AND IT RUNS EVERY VERB. `ACTIONS` is a table of functions; a verb whose `run`
was never called is a verb that could be misspelling an api name and nothing
would say so until it ran against a real timeline. Each one is executed here
against a recording stub, and what it CALLED is checked against what it DECLARED
in `needs` — in both directions:

  · a verb may not call an api function it did not declare, and
  · `ACTION_API` may not name a function no verb calls (that half lives in
    `tests/editor_director_check.py`, which asserts the real editor supplies
    every name in the list).

Neither test alone is enough: this one passes against a typo the editor never
sees, and that one passes against a verb that is never invoked.

Needs node. Nothing here touches a browser, a backend or a dollar.
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

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ⚠ THE FIXTURE IS FOUR SHOTS OF DIFFERENT LENGTHS, and every one of those words
# matters. FOUR because a cap expressed as a share needs enough clips to have a
# share of; DIFFERENT because `house_style` reads rhythm off the spread and a
# flat timeline is a legitimate but useless input to plan against.
HARNESS = """
import { ACTIONS, ACTION_API, VERBS, describeStep, validateStep } from %(actions)r;
import { capabilities } from %(caps)r;
import { validatePlan, planTotals, defaultInclude } from %(schema)r;

const caps = capabilities();

const frames = [
  { id: "f1", duration_ms: 2000, label: "Shot 1" },
  { id: "f2", duration_ms: 6000, label: "Shot 2" },
  { id: "f3", duration_ms: 2000, label: "Shot 3" },
  { id: "f4", duration_ms: 3000, label: "Shot 4" },
];
const starts = [0, 2000, 8000, 10000];
const ctx = {
  frames,
  starts,
  texts: [],
  shapes: [],
  transitions: [{ id: "x1", after_frame_id: "f1", kind: "dissolve", duration_ms: 600 }],
  overlays: [],
  audioTracks: [{ id: "a1", upload_id: "u1", filename: "s.wav", duration_ms: 9000 }],
  totalMs: 13000,
  caps,
};

const V = (verb, args) => validateStep({ verb, args }, caps, ctx);

// ---------------------------------------------------------------- the stub
// Records every call and answers the two questions the runner asks back: what
// id did `addText` make, and which clips share a lane with this one.
function makeStub() {
  const calls = [];
  const stub = {};
  for (const name of ACTION_API) {
    stub[name] = (...args) => {
      calls.push({ name, args });
      if (name === "addText") return "made-text";
      if (name === "addShape") return "made-shape";
      if (name === "laneSiblings") return [ctx.audioTracks[0]];
      return undefined;
    };
  }
  return { stub, calls };
}

// Valid arguments for every verb, so each one can actually be RUN. A verb
// missing from here is reported rather than skipped — see `unexercised` below.
const GOOD = {
  note: { text: "reading the rhythm" },
  seek: { ms: 1500 },
  select_shot: { shot: 2 },
  set_shot_duration: { shot: 2, ms: 3000 },
  set_all_durations: { ms: 2500 },
  set_shot_transform: { shot: 1, scale: 1.2, x: 0.5, y: 0.5, opacity: 1 },
  push_in: { shot: 2, from: 1, to: 1.1, ease: "ease-in-out" },
  clear_shot_motion: { shot: 2 },
  add_transition: { cut: 1, kind: "dissolve", ms: 700 },
  set_transition_duration: { cut: 1, ms: 800 },
  remove_transition: { cut: 1 },
  add_effect: { shot: 1, kind: "brightness", params: { amount: 1.2 } },
  set_effect_param: { shot: 1, index: 0, param: "amount", value: 1.4 },
  remove_effect: { shot: 1, index: 0 },
  add_text: { shot: 2, text: "2049", ref: "t1", position: "bottom" },
  set_text: { ref: "t1", size: "large" },
  apply_text_preset: { ref: "t1", preset: "rise" },
  remove_text: { ref: "t1" },
  add_shape: { shot: 3, kind: "rect", ref: "s1", x: 0.4, y: 0.4 },
  set_shape: { ref: "s1", opacity: 0.8 },
  remove_shape: { ref: "s1" },
  add_layer: { kind: "text", name: "Titles" },
  set_track_fade: { track: 0, inMs: 400, outMs: 900, inCurve: "power" },
  set_track_volume: { track: 0, volume: 0.6 },
  add_crossfade: { track: 0, curve: "linear", ms: 3000 },
};

// Run each verb and record what it touched. The ctx it is handed already carries
// the effect `set_effect_param` and `remove_effect` need to find, and the clips
// `set_text` / `set_shape` address through their refs.
const ran = {};
const unexercised = [];
for (const verb of VERBS) {
  if (!(verb in GOOD)) { unexercised.push(verb); continue; }
  const checked = V(verb, GOOD[verb]);
  if (!checked.ok) { ran[verb] = { validated: false, why: checked.why }; continue; }
  const { stub, calls } = makeStub();
  const runCtx = {
    ...ctx,
    frames: frames.map((f, i) =>
      i === 0 ? { ...f, effects: [{ id: "fx1", kind: "brightness", params: { amount: 1 } }] } : f
    ),
    texts: [{ id: "made-text", text: "2049", start_ms: 2000, duration_ms: 6000 }],
    shapes: [{ id: "made-shape", kind: "rect", start_ms: 8000, duration_ms: 2000 }],
  };
  const refs = { t1: "made-text", s1: "made-shape" };
  let threw = "";
  try {
    ACTIONS[verb].run({ api: stub, args: checked.args, ctx: runCtx, refs });
  } catch (err) {
    threw = String(err && err.message ? err.message : err);
  }
  ran[verb] = {
    validated: true,
    threw,
    called: calls.map((c) => c.name),
    needs: ACTIONS[verb].needs,
    describes: describeStep({ verb, args: checked.args }, runCtx),
    refs,
  };
}

// -------------------------------------------------- every legal word is legal
const accepts = {
  transitions: caps.transitions.map((t) => ({
    id: t.id, ok: V("add_transition", { cut: 1, kind: t.id }).ok,
  })),
  effects: caps.effects.map((e) => ({
    id: e.id, ok: V("add_effect", { shot: 1, kind: e.id }).ok,
  })),
  shapes: caps.shapes.map((s) => ({
    id: s.id, ok: V("add_shape", { shot: 1, kind: s.id }).ok,
  })),
  crossfades: caps.audioTransitions.map((c) => ({
    id: c.id, ok: V("add_crossfade", { track: 0, curve: c.id, ms: 1000 }).ok,
  })),
  presets: caps.text.presets.map((p) => ({
    id: p.id, ok: V("apply_text_preset", { ref: "t1", preset: p.id }).ok,
  })),
};

// ------------------------------------------------- every illegal one is dropped
const rejects = {
  unknownVerb: V("swirl_it", {}),
  unknownTransition: V("add_transition", { cut: 1, kind: "swirl" }),
  unknownEffect: V("add_effect", { shot: 1, kind: "blur" }),
  unknownShape: V("add_shape", { shot: 1, kind: "ring" }),
  unknownPreset: V("apply_text_preset", { ref: "t1", preset: "explode" }),
  unknownCrossfade: V("add_crossfade", { track: 0, curve: "swoosh", ms: 10 }),
  shotTooHigh: V("set_shot_duration", { shot: 99, ms: 1000 }),
  shotZero: V("set_shot_duration", { shot: 0, ms: 1000 }),
  cutZero: V("add_transition", { cut: 0, kind: "dissolve" }),
  cutPastEnd: V("add_transition", { cut: 4, kind: "dissolve" }),
  emptyText: V("add_text", { shot: 1, text: "   " }),
  noTrack: V("set_track_volume", { track: 7, volume: 1 }),
  pushNowhere: V("push_in", { shot: 1, from: 1, to: 1 }),
  badLayer: V("add_layer", { kind: "particles" }),
  junkArgs: V("set_shot_duration", { shot: "two", ms: "long" }),
  nothingToChange: V("set_text", { ref: "t1" }),
};

// ⚠ A PARAMETER FROM ANOTHER TRANSITION IS DROPPED AND THE TRANSITION SURVIVES.
// `transitionParams` fills in every default on the way out, so a wipe whose
// direction the model got wrong is still a wipe — and a wipe is what was asked
// for. Refusing the whole step would trade a right transition with a default
// direction for no transition at all.
const params = {
  goodDirection: V("add_transition", { cut: 1, kind: "wipe", params: { direction: "up" } }),
  badDirection: V("add_transition", { cut: 1, kind: "wipe", params: { direction: "sideways" } }),
  foreignParam: V("add_transition", { cut: 1, kind: "dissolve", params: { direction: "up" } }),
  clampedMs: V("add_transition", { cut: 1, kind: "dissolve", ms: 999999 }),
  clampedEffect: V("add_effect", { shot: 1, kind: "posterize", params: { levels: "eight" } }),
};

// ----------------------------------------------------- a caption fits its shot
// Shot 2 runs 2000 → 8000. A plan asking for a caption from 0 for 30s must come
// back inside that window plus the overhang, not outside it.
const fitted = (() => {
  const checked = V("add_text", { shot: 2, text: "hi", ref: "t9", startMs: 0, durationMs: 30000 });
  if (!checked.ok) return { ok: false, why: checked.why };
  const { stub, calls } = makeStub();
  ACTIONS.add_text.run({ api: stub, args: checked.args, ctx, refs: {} });
  const patch = (calls.find((c) => c.name === "patchText") || { args: [null, {}] }).args[1];
  return { ok: true, start: patch.start_ms, length: patch.duration_ms };
})();

// ------------------------------------------------------------ the whole plan
const plans = {
  // A ref used before anything creates it: dropped in the PREVIEW, which is the
  // only place the user can see it. At run time `byRef` finds nothing and the
  // step is a silent no-op that reported success.
  forwardRef: validatePlan(
    { steps: [
      { verb: "apply_text_preset", args: { ref: "t1", preset: "fade" } },
      { verb: "add_text", args: { shot: 1, text: "hello", ref: "t1" } },
    ] }, caps, ctx),
  backwardRef: validatePlan(
    { steps: [
      { verb: "add_text", args: { shot: 1, text: "hello", ref: "t1" } },
      { verb: "apply_text_preset", args: { ref: "t1", preset: "fade" } },
    ] }, caps, ctx),
  switchedOff: validatePlan(
    { include: { ...defaultInclude(), effects: false },
      steps: [
        { verb: "add_effect", args: { shot: 1, kind: "brightness" } },
        { verb: "set_shot_duration", args: { shot: 1, ms: 1000 } },
      ] }, caps, ctx),
  junk: validatePlan("not a plan at all", caps, ctx),
  mixed: validatePlan(
    { summary: "  a story  ", mood: "tense", steps: [
      { verb: "add_transition", args: { cut: 1, kind: "dissolve" } },
      { verb: "add_transition", args: { cut: 9, kind: "dissolve" } },
      { verb: "nonsense", args: {} },
      { verb: "add_text", args: { shot: 1, text: "title", ref: "a" } },
      { verb: "add_shape", args: { shot: 1, kind: "rect", ref: "b" } },
    ] }, caps, ctx),
};

process.stdout.write(JSON.stringify({
  verbs: VERBS,
  api: ACTION_API,
  shape: VERBS.map((v) => ({
    verb: v,
    hasRun: typeof ACTIONS[v].run === "function",
    hasValidate: typeof ACTIONS[v].validate === "function",
    hasDescribe: typeof ACTIONS[v].describe === "function",
    label: ACTIONS[v].label,
    needs: ACTIONS[v].needs,
    verbMatches: ACTIONS[v].verb === v,
  })),
  ran, unexercised, accepts, rejects, params, fitted,
  plans: Object.fromEntries(Object.entries(plans).map(([k, r]) => [k, {
    steps: r.plan.steps.map((s) => ({ id: s.id, verb: s.verb, args: s.args })),
    summary: r.plan.summary,
    dropped: r.dropped,
    totals: planTotals(r.plan),
  }])),
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="director_")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {
                "actions": (AGENT / "actions.js").as_uri(),
                "caps": (AGENT / "capabilities.js").as_uri(),
                "schema": (AGENT / "plan_schema.js").as_uri(),
            })
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


def main():
    data = run_node()
    if data is None:
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        return 2

    verbs = data["verbs"]
    api = set(data["api"])

    print("\nThe registry is a table of real functions\n")
    check("there are verbs at all", len(verbs) >= 20, f"{len(verbs)} verbs")
    for row in data["shape"]:
        bad = [k for k in ("hasRun", "hasValidate", "hasDescribe") if not row[k]]
        check(f"{row['verb']} has run/validate/describe", not bad, str(bad))
    check("every verb's key matches its own `verb` field",
          all(r["verbMatches"] for r in data["shape"]),
          str([r["verb"] for r in data["shape"] if not r["verbMatches"]]))
    check("every verb has a label for the log",
          all(r["label"] for r in data["shape"]),
          str([r["verb"] for r in data["shape"] if not r["label"]]))

    unknown_needs = {
        r["verb"]: [n for n in r["needs"] if n not in api] for r in data["shape"]
    }
    unknown_needs = {k: v for k, v in unknown_needs.items() if v}
    check("⚠ no verb declares an api function that is not in ACTION_API",
          not unknown_needs, json.dumps(unknown_needs))

    print("\n⚠ EVERY VERB WAS ACTUALLY RUN — and touched only what it declared\n")
    check("no verb was left unexercised by this test",
          not data["unexercised"], str(data["unexercised"]))
    for verb in verbs:
        row = data["ran"].get(verb)
        if row is None:
            continue
        if not row["validated"]:
            check(f"{verb} accepts its own good arguments", False, row["why"])
            continue
        check(f"{verb} runs without throwing", not row["threw"], row["threw"])
        stray = [n for n in row["called"] if n not in row["needs"]]
        check(f"{verb} called only what it declares in `needs`", not stray, str(stray))
        check(f"{verb} describes itself in one line", bool(row["describes"]))

    # ⚠ A VERB THAT DECLARES A NEED AND NEVER CALLS IT is not a failure — the
    # branch may simply not have been taken by these arguments (`add_transition`
    # only calls `patchTransition` when a length is given, and it is here). What
    # IS worth asserting is that the verbs which exist to call something called
    # something: a `run` that is silently empty is the failure this catches.
    silent = [
        v for v in verbs
        if data["ran"].get(v, {}).get("validated")
        and data["ran"][v]["needs"]
        and not data["ran"][v]["called"]
    ]
    check("no verb with needs ran without calling anything", not silent, str(silent))

    print("\nEvery word this build renders is a word the Director may use\n")
    for family, rows in data["accepts"].items():
        bad = [r["id"] for r in rows if not r["ok"]]
        check(f"every {family[:-1] if family.endswith('s') else family} kind is accepted "
              f"({len(rows)} of them)", not bad, str(bad))

    print("\n⚠ AND EVERY ILLEGAL ONE IS DROPPED WITH A REASON, NEVER THROWN\n")
    for name, result in data["rejects"].items():
        check(f"{name} is refused", result["ok"] is False, json.dumps(result))
        check(f"...and says why", bool(result.get("why")), json.dumps(result))

    print("\nA parameter the kind does not take is dropped — the kind survives\n")
    params = data["params"]
    check("a wipe keeps a direction it understands",
          params["goodDirection"]["ok"]
          and params["goodDirection"]["args"]["params"].get("direction") == "up",
          json.dumps(params["goodDirection"]))
    check("⚠ a wipe with a NONSENSE direction is still a wipe",
          params["badDirection"]["ok"]
          and params["badDirection"]["args"]["kind"] == "wipe"
          and "direction" not in params["badDirection"]["args"]["params"],
          json.dumps(params["badDirection"]))
    check("a dissolve does not keep a wipe's parameter",
          params["foreignParam"]["ok"]
          and not params["foreignParam"]["args"]["params"],
          json.dumps(params["foreignParam"]))
    check("a wild transition length is clamped, not refused",
          params["clampedMs"]["ok"] and params["clampedMs"]["args"]["ms"] <= 10000,
          json.dumps(params["clampedMs"]))
    check("a non-numeric effect parameter is dropped and the effect stands",
          params["clampedEffect"]["ok"]
          and "levels" not in params["clampedEffect"]["args"]["params"],
          json.dumps(params["clampedEffect"]))

    print("\n⚠ A CAPTION IS CLAMPED INTO THE SHOT IT BELONGS TO\n")
    fitted = data["fitted"]
    check("a caption asking for 0 → 30s on shot 2 was accepted", fitted["ok"], json.dumps(fitted))
    if fitted["ok"]:
        # Shot 2 is 2000 → 8000, and the house allows 400ms of overhang.
        check("...and starts no earlier than its shot", fitted["start"] >= 2000,
              f"start={fitted['start']}")
        check("...and ends no more than 400ms past it",
              fitted["start"] + fitted["length"] <= 8400,
              f"ends at {fitted['start'] + fitted['length']}")

    print("\nA whole plan comes through the same door\n")
    plans = data["plans"]
    check("⚠ a ref used before anything creates it is dropped",
          [s["verb"] for s in plans["forwardRef"]["steps"]] == ["add_text"],
          json.dumps(plans["forwardRef"]["steps"]))
    check("...with a reason naming the ref",
          any("t1" in d["why"] for d in plans["forwardRef"]["dropped"]),
          json.dumps(plans["forwardRef"]["dropped"]))
    check("the same two steps in the right order both survive",
          len(plans["backwardRef"]["steps"]) == 2,
          json.dumps(plans["backwardRef"]["steps"]))
    check("a treatment whose tick box is off is dropped",
          [s["verb"] for s in plans["switchedOff"]["steps"]] == ["set_shot_duration"],
          json.dumps(plans["switchedOff"]["steps"]))
    check("...and re-timing is NOT governed by the effects box",
          any(s["verb"] == "set_shot_duration" for s in plans["switchedOff"]["steps"]))
    check("junk instead of a plan is an empty plan, not a crash",
          plans["junk"]["steps"] == [], json.dumps(plans["junk"]))
    check("a mixed plan keeps the good steps and drops the rest",
          len(plans["mixed"]["steps"]) == 3 and len(plans["mixed"]["dropped"]) == 2,
          json.dumps(plans["mixed"]))
    check("the summary is trimmed on the way in",
          plans["mixed"]["summary"] == "a story", repr(plans["mixed"]["summary"]))
    check("⚠ totals are counted off the SURVIVING steps",
          plans["mixed"]["totals"]["transitions"] == 1
          and plans["mixed"]["totals"]["texts"] == 1
          and plans["mixed"]["totals"]["shapes"] == 1,
          json.dumps(plans["mixed"]["totals"]))
    check("every surviving step has a unique id for the rail",
          len({s["id"] for s in plans["mixed"]["steps"]}) == len(plans["mixed"]["steps"]),
          json.dumps([s["id"] for s in plans["mixed"]["steps"]]))

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for name in failures:
            print("  -", name)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
