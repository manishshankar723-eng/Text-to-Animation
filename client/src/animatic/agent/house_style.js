// house_style.js — THE EDITOR WITH NO AI IN IT, and the fence every plan clears.
//
// Two things live here and they are deliberately not the same thing:
//
//   `housePlan(ctx)`        writes a plan from rules alone. No model, no network,
//                           nothing spent. This is what the 🎬 button runs in
//                           Phase 0, and it stays afterwards as the fallback for
//                           a run whose model call failed.
//
//   `applyGuardrails(plan)` trims ANY plan down to what the house will allow,
//                           whoever wrote it. The deterministic one goes through
//                           it too — not because it needs to, but because a fence
//                           only one path is checked against is a fence with a
//                           gate in it.
//
// ---------------------------------------------------------------------------
// ⚠ THE DETERMINISTIC EDITOR DOES NOT WRITE WORDS, AND THAT IS THE POINT.
// ---------------------------------------------------------------------------
// It places transitions and camera moves — both of which are decisions about
// RHYTHM, and rhythm is legible in the numbers a timeline already has: how long
// each shot holds, and how that compares to its neighbours. It adds no titles,
// no captions and no arrows, because there is no rule that produces the right
// words, and text invented by arithmetic would be the first thing the user saw
// and the first thing they deleted. Words are what the model is for, in Phase 2.
//
// So what Phase 0 ships is a cut, not a treatment. That is worth having on its
// own — an animatic where every held shot breathes and the long pauses dissolve
// instead of snapping is meaningfully better than one where nothing does — and
// it is worth having FIRST, because everything downstream runs through the same
// action registry and the same fence.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE, AND IT IS DETERMINISTIC.
// ---------------------------------------------------------------------------
// No React, no clock, no `Math.random`. The same project produces the same plan
// every time, which is what makes `tests/director_guardrails_check.py` able to
// assert on the plan itself rather than on statistics about it — and what makes
// "run it again" a thing the user can do to compare a change they made.

import { HOUSE_CAPS } from "./capabilities.js";
import { ACTIONS } from "./actions.js";
import { defaultInclude, governingKey } from "./plan_schema.js";

/** The middle shot length, which is what "long" and "short" are measured against. */
function medianDuration(frames) {
  const lengths = frames
    .map((f) => Math.max(HOUSE_CAPS.MIN_CLIP_MS, Number(f?.duration_ms) || 0))
    .sort((a, b) => a - b);
  if (!lengths.length) return 2000;
  const mid = Math.floor(lengths.length / 2);
  return lengths.length % 2 ? lengths[mid] : Math.round((lengths[mid - 1] + lengths[mid]) / 2);
}

/**
 * WHICH CUTS BREATHE.
 *
 * A dissolve says "time passed". The one thing a timeline knows without being
 * told is which shots were HELD, and a held shot followed by a cut is where a
 * scene most often ends — so the rule is: dissolve after a shot that runs at
 * least `LONG_SHOT` times the median, cut everywhere else.
 *
 * ⚠ IT IS A PROXY AND IT IS NOT ASHAMED OF ONE. The real question is "is this a
 * scene boundary", which needs the script — that is Phase 2's job. What matters
 * here is that the proxy fails SAFE: a cut it gets wrong is a dissolve on an
 * ordinary edit, which is a mild, conventional, one-click-undoable mistake, and
 * never the reverse.
 */
const LONG_SHOT = 1.5;

/**
 * WHICH SHOTS MOVE.
 *
 * A push-in on a long shot; nothing on a short one, because a 1.2s shot with a
 * move on it reads as a wobble rather than a push. Two rules on top:
 *
 *   · NEVER TWICE IN A ROW. Consecutive moves stop reading as emphasis and start
 *     reading as a camera that will not sit still.
 *   · NEVER MORE THAN A THIRD OF THE FILM. Same argument as the effects cap: a
 *     move only means something while most shots are locked off.
 */
const MOVING_SHOT = 1.4;
const PUSH_TO = 1.07;
const MOVE_SHARE = 1 / 3;

/**
 * The plan the rules produce. Raw — it goes through `validatePlan` like any
 * other, which is what proves the deterministic path cannot skip the door.
 *
 * @param ctx      the read-model: `{ frames, starts, totalMs }`
 * @param options  `{ include }` — the tick boxes from the preview
 */
export function housePlan(ctx, options = {}) {
  const frames = (ctx && ctx.frames) || [];
  const include = { ...defaultInclude(), ...(options.include || {}) };
  const steps = [];

  if (frames.length < 1) {
    return {
      version: 1,
      summary: "",
      mood: "",
      include,
      steps: [{ verb: "note", args: { text: "Nothing on the timeline to edit yet." } }],
    };
  }

  const median = medianDuration(frames);
  const lengthOf = (f) => Math.max(HOUSE_CAPS.MIN_CLIP_MS, Number(f?.duration_ms) || 0);

  steps.push({
    verb: "note",
    args: {
      text:
        `${frames.length} shot${frames.length === 1 ? "" : "s"}, middle length ` +
        `${(median / 1000).toFixed(1)}s. Cutting on rhythm: the held shots breathe, the rest cut.`,
    },
  });

  // ------------------------------------------------------------- transitions
  if (include.transitions) {
    // ⚠ CAPPED BEFORE THE FENCE SEES IT, not instead of. The cap is applied here
    // so the plan the user READS is already house-legal — a preview listing 30
    // dissolves that the fence then silently trims to 16 is a preview of a
    // different film from the one that gets made.
    const budget = Math.max(1, Math.floor((frames.length - 1) * HOUSE_CAPS.TRANSITION_CUT_SHARE));
    const candidates = [];
    for (let cut = 1; cut < frames.length; cut += 1) {
      const before = lengthOf(frames[cut - 1]);
      if (before >= median * LONG_SHOT) candidates.push({ cut, before });
    }
    // The longest holds win the budget — those are the pauses most worth
    // marking. Ties break towards the EARLIER cut so the choice is stable.
    candidates
      .sort((a, b) => b.before - a.before || a.cut - b.cut)
      .slice(0, budget)
      .sort((a, b) => a.cut - b.cut)
      .forEach(({ cut, before }) => {
        steps.push({
          verb: "add_transition",
          args: {
            cut,
            kind: "dissolve",
            // A longer hold gets a longer dissolve, within the sane range. A
            // fixed 600ms on a five-second hold reads as a glitch.
            ms: Math.min(1200, Math.max(400, Math.round(before * 0.25))),
          },
          note: `shot ${cut} holds ${(before / 1000).toFixed(1)}s`,
        });
      });
  }

  // ------------------------------------------------------------------ moves
  const moveBudget = Math.floor(frames.length * MOVE_SHARE);
  let moved = 0;
  let lastMoved = -2;
  frames.forEach((frame, i) => {
    if (moved >= moveBudget) return;
    if (i - lastMoved < 2) return;
    if (lengthOf(frame) < median * MOVING_SHOT) return;
    steps.push({
      verb: "push_in",
      args: { shot: i + 1, from: 1, to: PUSH_TO, ease: "ease-in-out" },
      note: `shot ${i + 1} is held long enough to move on`,
    });
    moved += 1;
    lastMoved = i;
  });

  if (steps.length === 1) {
    steps.push({
      verb: "note",
      args: {
        text:
          "Every shot is about the same length, so there is no rhythm to read — " +
          "left as straight cuts. Vary the holds, or use the AI pass, for more than this.",
      },
    });
  }

  return {
    version: 1,
    summary: "",
    mood: "",
    include,
    steps,
  };
}

/**
 * TRIM A PLAN TO WHAT THE HOUSE ALLOWS. Returns `{ plan, trimmed }`.
 *
 * ⚠ IT RUNS ON A VALIDATED PLAN, so every step here already has somewhere to
 * land and legal arguments. What is left is the question validation cannot ask,
 * because it is about the plan as a WHOLE rather than about any step in it: not
 * "is this effect real" but "is this the ninth effect on a five-shot animatic".
 *
 * ⚠ OVERFLOW IS DROPPED FROM THE END, and the order is the plan's own. A planner
 * puts its most deliberate work first — the establishing title before the
 * decorative arrow — so keeping the head of the list keeps the choices it cared
 * most about. It is also the only rule that is stable: dropping "the least
 * important" would need a judgement this file has no way to make.
 */
export function applyGuardrails(plan, ctx, options = {}) {
  const caps = { ...HOUSE_CAPS, ...(options.caps || {}) };
  const frames = (ctx && ctx.frames) || [];
  const minutes = Math.max(1 / 60, (Number(ctx?.totalMs) || 0) / 60000);
  const trimmed = [];
  const kept = [];

  const effectsOn = new Map(); // shot number → how many effects it carries
  const cutsUsed = new Set();
  let effectShots = 0;
  let shapes = 0;
  let texts = 0;

  const effectShotBudget = Math.max(1, Math.floor(frames.length * caps.EFFECT_CLIP_SHARE));
  const transitionBudget = Math.max(1, Math.floor(Math.max(0, frames.length - 1) * caps.TRANSITION_CUT_SHARE));
  const shapeBudget = Math.max(1, Math.round(minutes * caps.SHAPES_PER_MINUTE));
  const textBudget = Math.max(1, Math.round(minutes * caps.TEXTS_PER_MINUTE));

  // Refs that survived. A step addressing a ref whose creator was trimmed has
  // nothing to act on, so it goes with it — otherwise the rail would report a
  // restyle of a caption that was never added.
  const alive = new Set();

  (plan?.steps || []).forEach((step, index) => {
    const drop = (why) => trimmed.push({ index, verb: step.verb, why });
    const action = ACTIONS[step.verb] || {};
    const ref = step.args?.ref;

    if (ref && !action.creates && !alive.has(ref)) {
      drop(`“${ref}” was trimmed, so this has nothing to change`);
      return;
    }

    if (step.verb === "add_effect") {
      const shot = step.args.shot;
      const already = effectsOn.get(shot) || 0;
      if (already >= caps.EFFECTS_PER_CLIP) {
        drop(`shot ${shot} already carries ${already} effect${already === 1 ? "" : "s"}`);
        return;
      }
      if (already === 0 && effectShots >= effectShotBudget) {
        drop(
          `${effectShots} of ${frames.length} shots are graded already — ` +
            `the house limit is ${Math.round(caps.EFFECT_CLIP_SHARE * 100)}%`
        );
        return;
      }
      if (already === 0) effectShots += 1;
      effectsOn.set(shot, already + 1);
    }

    if (step.verb === "add_transition") {
      if (cutsUsed.has(step.args.cut)) {
        drop(`there is already a transition on the cut after shot ${step.args.cut}`);
        return;
      }
      if (cutsUsed.size >= transitionBudget) {
        drop(`${cutsUsed.size} cuts are treated already — the house limit is ${transitionBudget}`);
        return;
      }
      cutsUsed.add(step.args.cut);
    }

    if (step.verb === "add_shape") {
      if (shapes >= shapeBudget) {
        drop(`${shapes} shapes in ${minutes.toFixed(1)} minutes is the house limit`);
        return;
      }
      shapes += 1;
    }

    if (step.verb === "add_text") {
      if (texts >= textBudget) {
        drop(`${texts} text clips in ${minutes.toFixed(1)} minutes is the house limit`);
        return;
      }
      texts += 1;
    }

    // A treatment whose tick box is off should never have reached here —
    // `validatePlan` drops those — but the fence says so itself rather than
    // trusting the order the two are called in.
    const governor = governingKey(step.verb);
    if (governor && plan?.include && plan.include[governor] === false) {
      drop(`${governor} are switched off for this run`);
      return;
    }

    if (action.creates && ref) alive.add(ref);
    kept.push(step);
  });

  return { plan: { ...plan, steps: kept }, trimmed };
}
