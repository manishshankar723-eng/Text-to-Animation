"""A MALICIOUS PLAN IS NEUTRALISED, AND NOTHING THROWS.

    python tests/director_plan_check.py

⚠ THIS IS THE TEST PHASE 2 EXISTS FOR. Until now the only thing writing plans was
`house_style.housePlan` — deterministic, ours, and in principle trustworthy. From
Phase 2 a language model writes them, and a language model will eventually
return `kind: "swirl"`, `cut: -3`, four hundred shapes, a caption ten seconds
long over a two-second shot, and a step that restyles a clip nothing created. Not
because it is adversarial: because it is a language model.

So the plan below is written by hand to be as bad as a plan can be, and the claim
is not "most of it is caught". The claim is:

  1. EVERY ILLEGAL STEP IS DROPPED WITH A REASON. Not silently ignored — the
     reason is what the panel shows under the table, and it is what tells the
     user their thirty-step plan became four.
  2. EVERY LEGAL STEP SURVIVES. A fence that also eats the good steps is a fence
     nobody leaves switched on. The two counts are asserted together on purpose.
  3. NOTHING RAISES. Not the folding, not the validation, not the fence, not the
     run. A plan that dies on step 12 leaves a timeline half-edited with no
     explanation of which half — the single worst outcome this feature has.
  4. A VALUE THAT IS MERELY OUT OF RANGE IS CLAMPED, NOT DROPPED. A 40-second
     dissolve becomes the longest legal dissolve, because "the model meant a long
     dissolve" is a reading that produces a film and "drop it" is one that
     produces a straight cut.

⚠ IT CHECKS BOTH SIDES OF THE LINE, and they are different defences. `director.py`
folds a returned step down to the arguments its verb actually takes — that is
what stops `preset: "rise"` riding along on an `add_transition` — and the client's
`validatePlan` / `applyGuardrails` are the door every plan comes through however
it was written. Either alone would pass this file's easier half and fail its
harder one.

Needs node for the client half. Nothing here touches a browser, a backend, a
model or a dollar.
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


# ---------------------------------------------------------------------------
# THE PLAN. Every step here is deliberate; the comment says what it attacks.
# ---------------------------------------------------------------------------
MALICIOUS = [
    # --- things that do not exist ------------------------------------------
    {"verb": "delete_project", "args": {"confirm": True}},          # no such verb
    {"verb": "add_transition", "args": {"cut": 2, "kind": "swirl"}},  # no such transition
    {"verb": "add_effect", "args": {"shot": 2, "kind": "deepfry"}},   # no such effect
    {"verb": "add_shape", "args": {"shot": 2, "kind": "pentaflower", "ref": "bad1"}},
    # --- times and indices outside the film --------------------------------
    {"verb": "add_transition", "args": {"cut": -3, "kind": "dissolve"}},
    {"verb": "add_transition", "args": {"cut": 0, "kind": "dissolve"}},
    {"verb": "add_transition", "args": {"cut": 8, "kind": "dissolve"}},   # == frames.length
    {"verb": "add_transition", "args": {"cut": 900, "kind": "dissolve"}},
    {"verb": "set_shot_duration", "args": {"shot": 61, "ms": 2000}},
    {"verb": "set_shot_duration", "args": {"shot": 0, "ms": 2000}},
    {"verb": "set_shot_duration", "args": {"shot": -1, "ms": 2000}},
    {"verb": "set_track_volume", "args": {"track": 99, "volume": 1}},
    # --- values that are merely absurd: these are CLAMPED, not dropped -----
    {"verb": "add_transition", "args": {"cut": 1, "kind": "dissolve", "ms": 40000}},
    {"verb": "set_shot_duration", "args": {"shot": 1, "ms": -5000}},
    {"verb": "set_shot_transform", "args": {"shot": 1, "scale": 900, "opacity": 12}},
    {"verb": "push_in", "args": {"shot": 2, "from": -40, "to": 400}},
    # --- a forward reference: styles a clip nothing has created ------------
    {"verb": "set_text", "args": {"ref": "never_made", "size": "large"}},
    {"verb": "remove_shape", "args": {"ref": "also_never_made"}},
    # --- a caption longer than the shot it belongs to -----------------------
    {"verb": "add_text", "args": {"shot": 1, "text": "OVERRUN", "ref": "long",
                                  "durationMs": 60000}},
    # --- and the good ones, which must all survive --------------------------
    {"verb": "note", "args": {"text": "Two scenes, cutting on the boundary."}},
    {"verb": "add_transition", "args": {"cut": 4, "kind": "dissolve", "ms": 700}},
    {"verb": "add_text", "args": {"shot": 1, "text": "NIGHT SHIFT", "ref": "t1",
                                  "position": "middle", "size": "large"}},
    {"verb": "apply_text_preset", "args": {"ref": "t1", "preset": "fade"}},
    {"verb": "add_effect", "args": {"shot": 3, "kind": "brightness"}},
    # ⚠ LAST, AND THAT IS NOT COSMETIC. `t1` has to already exist for the PRESET
    # to be the thing that fails: put this above the `add_text` that creates it
    # and it is dropped as a forward reference, the reason says "t1", and the
    # assertion below silently stops testing what it claims to.
    {"verb": "apply_text_preset", "args": {"ref": "t1", "preset": "explode"}},
]

# Four hundred shapes. The house budget for a 16-second film is a small handful,
# so this is the cap doing the work rather than the validator — a shape on shot 3
# is a perfectly legal step, and the four hundredth one is not.
FOUR_HUNDRED_SHAPES = [
    {"verb": "add_shape", "args": {"shot": (i % 8) + 1, "kind": "rect", "ref": f"s{i}"}}
    for i in range(400)
]

# The five steps above that are legal, by verb, in plan order.
GOOD = [
    "add_transition",   # the 40s one — clamped, not dropped
    "set_shot_duration",  # the negative one — clamped to MIN_CLIP_MS
    "set_shot_transform",
    "push_in",
    "add_text",         # the 60s caption — the clip is cut to its shot at run time
    "note",
    "add_transition",
    "add_text",
    "apply_text_preset",
    "add_effect",
]


HARNESS = """
import { capabilities, HOUSE_CAPS } from "__CAPS__";
import { MAX_TRANSITION_MS, MIN_TRANSITION_MS } from "__TRANSITIONS__";
import { applyGuardrails } from "__HOUSE__";
import { validatePlan } from "__SCHEMA__";
import { ACTIONS } from "__ACTIONS__";
import { readFileSync } from "node:fs";

const caps = capabilities();
// ⚠ THE PLAN COMES OFF DISK, NOT OFF `argv`. Four hundred shapes is 30 kB of
// JSON and Windows refuses a command line that long — "[WinError 206] The
// filename or extension is too long", which reads like a path bug and is not.
const plan = JSON.parse(readFileSync(process.argv[2], "utf-8"));

/** Eight shots, 2s each — a small, ordinary film. */
const frames = [];
const starts = [];
for (let i = 0; i < 8; i += 1) {
  frames.push({ id: `f${i + 1}`, duration_ms: 2000, label: `Shot ${i + 1}` });
  starts.push(i * 2000);
}
const ctx = { frames, starts, texts: [], shapes: [], transitions: [], overlays: [],
              audioTracks: [], totalMs: 16000, caps,
              readTransitions: () => [] };

// ⚠ NOTHING HERE IS IN A TRY/CATCH, and that is the assertion. If any of the
// three doors throws on a bad plan, node exits non-zero and the test says so —
// which is exactly the failure that would otherwise show up as a half-edited
// timeline in front of a user.
const checked = validatePlan(plan, caps, ctx);
const fenced = applyGuardrails(checked.plan, ctx);

// ⚠ AND THE SURVIVORS ARE RUN, against a recording stub. Validation proves the
// arguments are legal; running proves a legal step does not then blow up inside
// the editor — the two are different claims and only the second covers
// `shotWindow`, which is where a caption longer than its shot is actually cut
// back down.
const calls = [];
const api = new Proxy({}, {
  get: (_t, name) => (...args) => {
    calls.push({ name: String(name), args });
    if (name === "addText" || name === "addShape") return `made_${calls.length}`;
    if (name === "laneSiblings") return [];
    return undefined;
  },
});
const refs = {};
let threw = "";
for (const step of fenced.plan.steps) {
  try {
    ACTIONS[step.verb].run({ api, args: step.args, ctx, refs });
  } catch (err) {
    threw = `${step.verb}: ${err.message}`;
    break;
  }
}

process.stdout.write(JSON.stringify({
  caps: HOUSE_CAPS,
  transitionMs: { min: MIN_TRANSITION_MS, max: MAX_TRANSITION_MS },
  dropped: checked.dropped,
  trimmed: fenced.trimmed,
  kept: fenced.plan.steps.map((s) => ({ verb: s.verb, args: s.args })),
  calls,
  threw,
}));
"""


def run_node(plan) -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="director_plan_")
    try:
        plan_path = os.path.join(work, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS.replace("__CAPS__", (AGENT / "capabilities.js").as_uri())
                .replace("__HOUSE__", (AGENT / "house_style.js").as_uri())
                .replace("__SCHEMA__", (AGENT / "plan_schema.js").as_uri())
                .replace("__ACTIONS__", (AGENT / "actions.js").as_uri())
                .replace("__TRANSITIONS__", (AGENT.parent / "transitions.js").as_uri())
            )
        proc = subprocess.run(
            ["node", harness, plan_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1500])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# THE SERVER SIDE — folding, which is the defence the client cannot make
# ---------------------------------------------------------------------------
def check_folding(caps: dict) -> None:
    import director

    print("\n⚠ THE FOLD — an argument the verb does not take never leaves the server\n")

    raw = [
        # `preset` belongs to `apply_text_preset`; on a transition it is noise the
        # flat schema made possible (see `plan_schema` in director.py) and the
        # fold is what stops it reaching the client at all.
        {"verb": "add_transition", "args": {"cut": 1, "kind": "dissolve", "preset": "rise",
                                            "text": "hello", "volume": 3}},
        # ⚠ THE ONE THAT MATTERS MOST. A spurious `x: 0` on a caption is not
        # noise — it pins the text to the left edge of the frame, and it looks
        # deliberate. It survives only if the verb declares it, which `add_text`
        # does, so this asserts the OPPOSITE case: `x` on a verb without it.
        {"verb": "set_shot_duration", "args": {"shot": 1, "ms": 2000, "x": 0, "ref": "nope"}},
        {"verb": "nonsense_verb", "args": {"shot": 1}},
        {"verb": "push_in", "args": {}},
        {"verb": "add_effect", "args": {"shot": 1, "kind": "contrast",
                                        "params": [{"name": "amount", "value": "1.4"},
                                                   {"name": "", "value": "x"}]}},
    ]
    steps, dropped = director.fold_steps(raw, caps)
    by_verb = {s["verb"]: s["args"] for s in steps}

    check("a verb that does not exist is dropped with a reason",
          any(d["verb"] == "nonsense_verb" and "no" in d["why"] for d in dropped),
          json.dumps(dropped))
    check("a step with no argument its verb understands is dropped",
          any(d["verb"] == "push_in" for d in dropped), json.dumps(dropped))
    check("⚠ an argument belonging to ANOTHER verb is folded away",
          "add_transition" in by_verb
          and set(by_verb["add_transition"]) == {"cut", "kind"},
          json.dumps(by_verb.get("add_transition")))
    check("⚠ ...including the ones that would look deliberate (`x`, `ref`)",
          "set_shot_duration" in by_verb
          and set(by_verb["set_shot_duration"]) == {"shot", "ms"},
          json.dumps(by_verb.get("set_shot_duration")))
    check("`params` pairs become a map, and a nameless pair is left out",
          by_verb.get("add_effect", {}).get("params") == {"amount": "1.4"},
          json.dumps(by_verb.get("add_effect")))
    check("nothing raised — the fold reports, it never throws",
          isinstance(steps, list) and isinstance(dropped, list))

    # ⚠ AND THE CEILING HOLDS. `MAX_STEPS` is not a house cap (that is the
    # client's fence, and it is per-treatment) — it is the wall against a
    # generation that ran away, and it has to hold before the payload is built.
    runaway = [{"verb": "note", "args": {"text": f"line {i}"}} for i in range(director.MAX_STEPS + 50)]
    kept, cut = director.fold_steps(runaway, caps)
    check(f"a runaway generation stops at MAX_STEPS ({director.MAX_STEPS})",
          len(kept) == director.MAX_STEPS and len(cut) == 1, f"{len(kept)} kept")


def main():
    print("\n⚠ A HAND-WRITTEN MALICIOUS PLAN, THROUGH BOTH DOORS\n")

    plan = {"version": 1, "summary": "hostile", "steps": MALICIOUS + FOUR_HUNDRED_SHAPES}
    data = run_node(plan)
    if data is None:
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        return 1

    caps = data["caps"]
    dropped = data["dropped"]
    trimmed = data["trimmed"]
    kept = data["kept"]
    kept_verbs = [s["verb"] for s in kept]
    why = {d["verb"]: d["why"] for d in dropped}

    # ----------------------------------------------------------- nothing throws
    check("⚠ NOTHING THREW — validation, the fence and every surviving step ran",
          not data["threw"], data["threw"])

    # ------------------------------------------------------- what does not exist
    print("\n⚠ A KIND THIS BUILD CANNOT RENDER IS DROPPED, WITH THE REASON SHOWN\n")
    check("an unknown VERB is dropped", "delete_project" in why, json.dumps(why))
    check("...and the reason names it",
          "delete_project" in (why.get("delete_project") or ""), why.get("delete_project"))
    check("an unknown TRANSITION is dropped",
          any("swirl" in d["why"] for d in dropped), json.dumps(dropped[:4]))
    check("an unknown EFFECT is dropped", any("deepfry" in d["why"] for d in dropped))
    check("an unknown SHAPE is dropped", any("pentaflower" in d["why"] for d in dropped))
    check("an unknown TEXT PRESET is dropped", any("explode" in d["why"] for d in dropped))

    # ------------------------------------------------------ times out of the film
    print("\n⚠ A TIME OR AN INDEX OUTSIDE THE FILM IS DROPPED\n")
    bad_cuts = [d for d in dropped if d["verb"] == "add_transition" and "cut" in d["why"]]
    check("negative, zero, last-shot and 900th cuts are all refused",
          len(bad_cuts) == 4, json.dumps(bad_cuts))
    check("⚠ `cut` EQUAL TO THE SHOT COUNT IS REFUSED — there is no cut after the last shot",
          any("cut 8" in d["why"] for d in bad_cuts), json.dumps(bad_cuts))
    bad_shots = [d for d in dropped if d["verb"] == "set_shot_duration"]
    check("shot 61, shot 0 and shot -1 are all refused on an 8-shot film",
          len(bad_shots) == 3, json.dumps(bad_shots))
    check("an audio track that is not there is refused",
          any(d["verb"] == "set_track_volume" for d in dropped), json.dumps(dropped))

    # ------------------------------------------------------- forward references
    print("\n⚠ A STEP ADDRESSING SOMETHING NO EARLIER STEP CREATED IS DROPPED\n")
    check("a `ref` nothing creates is refused BEFORE the run, not silently at it",
          any(d["verb"] == "set_text" and "never_made" in d["why"] for d in dropped),
          json.dumps([d for d in dropped if d["verb"] == "set_text"]))
    check("...for every verb that takes one",
          any(d["verb"] == "remove_shape" for d in dropped))

    # ------------------------------------------------------------- 400 shapes
    print("\n⚠ FOUR HUNDRED SHAPES ON A SIXTEEN-SECOND FILM\n")
    shapes_kept = kept_verbs.count("add_shape")
    budget = max(1, round((16000 / 60000) * caps["SHAPES_PER_MINUTE"]))
    check(f"the house budget holds: {shapes_kept} kept, not 400",
          shapes_kept == budget, f"kept {shapes_kept}, budget {budget}")
    check("...and every one over it is REPORTED, not silently absent",
          len([t for t in trimmed if t["verb"] == "add_shape"]) == 400 - shapes_kept,
          f"{len([t for t in trimmed if t['verb'] == 'add_shape'])} trimmed")
    check("⚠ THE OVERFLOW IS CUT FROM THE END — the plan's own order is its priority",
          all(t["index"] > max((i for i, s in enumerate(kept) if s["verb"] == "add_shape"),
                               default=-1)
              for t in trimmed if t["verb"] == "add_shape"))

    # ------------------------------------------------- absurd but legal values
    print("\n⚠ A VALUE MERELY OUT OF RANGE IS CLAMPED, NOT DROPPED\n")
    from_plan = {s["verb"]: s["args"] for s in kept}
    dissolve = next((s for s in kept if s["verb"] == "add_transition"), {})
    ceiling = data["transitionMs"]["max"]
    check(f"a 40-SECOND dissolve becomes the longest legal one ({ceiling}ms), and still happens",
          dissolve.get("args", {}).get("ms") == ceiling, json.dumps(dissolve))
    check("a NEGATIVE shot length becomes the shortest legal one",
          from_plan.get("set_shot_duration", {}).get("ms") == caps["MIN_CLIP_MS"],
          json.dumps(from_plan.get("set_shot_duration")))
    check("scale 900 and opacity 12 are clamped into their ranges",
          from_plan.get("set_shot_transform", {}).get("patch", {}).get("scale", 0) <= 8
          and from_plan.get("set_shot_transform", {}).get("patch", {}).get("opacity", 0) <= 1,
          json.dumps(from_plan.get("set_shot_transform")))
    check("a push from -40 to 400 is clamped and still a push",
          0.5 <= from_plan.get("push_in", {}).get("from", 0) <= 4
          and 0.5 <= from_plan.get("push_in", {}).get("to", 0) <= 4,
          json.dumps(from_plan.get("push_in")))

    # ------------------------------------------- a caption longer than its shot
    print("\n⚠ A CAPTION MAY NOT OUTLIVE THE SHOT IT BELONGS TO\n")
    patches = [c for c in data["calls"] if c["name"] == "patchText"]
    overrun = next((c for c in patches if c["args"][1].get("text") == "OVERRUN"), None)
    check("the 60-SECOND caption over a 2s shot was written to the timeline at all",
          overrun is not None, json.dumps(patches)[:400])
    if overrun:
        length = overrun["args"][1].get("duration_ms", 0)
        limit = 2000 + caps["TEXT_OVERHANG_MS"]
        check(f"⚠ ...and it was cut to its shot plus the {caps['TEXT_OVERHANG_MS']}ms of slack",
              length <= limit, f"{length}ms, limit {limit}ms")
        check("...which is slack, not zero — a title may fade across the cut",
              caps["TEXT_OVERHANG_MS"] > 0)

    # ----------------------------------------------------- the good ones live
    print("\n⚠ AND EVERY LEGAL STEP SURVIVED — a fence that eats good steps is off by lunchtime\n")
    non_shape = [v for v in kept_verbs if v != "add_shape"]
    check("all ten legal steps are still there, in order",
          non_shape == GOOD, json.dumps(non_shape))
    check("the title's preset survived with it (its `ref` was really created)",
          "apply_text_preset" in kept_verbs)
    check("the effect on shot 3 survived",
          any(s["verb"] == "add_effect" and s["args"]["shot"] == 3 for s in kept))

    # ---------------------------------------------------------- the server side
    caps_manifest = json.loads(
        subprocess.run(
            ["node", "-e",
             "import('%s').then(m=>process.stdout.write(JSON.stringify(m.capabilities())))"
             % (AGENT / "capabilities.js").as_uri()],
            capture_output=True, text=True, encoding="utf-8",
        ).stdout
    )
    check_folding(caps_manifest)

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
