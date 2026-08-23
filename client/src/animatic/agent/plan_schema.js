// plan_schema.js — WHAT AN `EditPlan` IS, and the door every plan comes through.
//
// A plan is the ONE thing that crosses the line between "something wrote this"
// and "the timeline changed". In Phase 0 the writer is `house_style.js`, which
// is deterministic and could in principle be trusted; from Phase 1 it is a
// language model, which cannot. The validator is written for the second case
// from the start, because a door added later is a door with code already walking
// past it.
//
//   {
//     version: 1,
//     summary:  "A lone engineer builds a machine that outgrows him."
//     mood:     "tense",
//     language: "hinglish",
//     include:  { transitions: true, effects: true, text: true, shapes: true },
//     steps: [
//       { id: "s1", verb: "add_transition", args: { cut: 1, kind: "dissolve" } },
//       { id: "s2", verb: "add_text", args: { shot: 1, text: "2049", ref: "t1" } },
//     ],
//   }
//
// ---------------------------------------------------------------------------
// ⚠ VALIDATION RETURNS A PLAN AND A LIST OF DROPS. IT NEVER THROWS.
// ---------------------------------------------------------------------------
// The user is shown both — the plan in the preview table, the drops underneath
// it as "3 steps couldn't be used". That is the honest report, and it is what
// makes a half-understood plan safe to run: what survived is exactly what will
// happen, and what didn't is on screen next to it rather than in a console.
//
// ---------------------------------------------------------------------------
// ⚠ THE PLAN IS CHECKED AGAINST THE PROJECT, NOT JUST AGAINST ITSELF.
// ---------------------------------------------------------------------------
// "Shot 61" is a perfectly well-formed step and a nonsense one on a 48-shot
// animatic. So `validatePlan` takes the read-model as well as the capability
// manifest, and every verb's own validator resolves its targets against the real
// sequence — see `shotIndex` in `actions.js`. A plan that validates is a plan
// whose every step has somewhere to land.

import { ACTIONS, validateStep } from "./actions.js";

/** The shape's version. Bumped only when an OLD plan would be read wrongly. */
export const PLAN_VERSION = 1;

/**
 * What a run is allowed to touch, and what the two popups tick.
 *
 * ⚠ THESE ARE THE FREE ONES. `veo` and `voiceover` are named here because the
 * preview lists them in the same tick-box column, but nothing in Phase 0 reads
 * them — no verb in the registry spends money, and there is deliberately no way
 * for one to. When Phase 4 adds the spending path it adds it beside these, and
 * the reason the flags live together is so the total the user is shown and the
 * work that actually happens are read off ONE object.
 */
export const INCLUDE_KEYS = [
  "transitions",
  "effects",
  "text",
  "shapes",
  "captions",
  "voiceover",
  "veo",
];

/** Everything on, which is what the preview opens with. */
export function defaultInclude() {
  return Object.fromEntries(INCLUDE_KEYS.map((key) => [key, true]));
}

/**
 * Which include-flag each verb answers to.
 *
 * A verb with no entry is always allowed: `note`, `seek` and the timing verbs
 * are the cut itself, not a treatment laid on top of it, and un-ticking
 * "Effects" must not stop the Director re-timing a shot.
 */
const GOVERNED_BY = {
  add_transition: "transitions",
  set_transition_duration: "transitions",
  remove_transition: "transitions",
  add_effect: "effects",
  set_effect_param: "effects",
  remove_effect: "effects",
  add_text: "text",
  set_text: "text",
  apply_text_preset: "text",
  remove_text: "text",
  add_shape: "shapes",
  set_shape: "shapes",
  remove_shape: "shapes",
};

export function governingKey(verb) {
  return GOVERNED_BY[verb] || "";
}

const text = (value, fallback = "") =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;

/**
 * Read a raw plan into a checked one.
 *
 * @param raw   whatever the planner produced — parsed JSON, or nothing useful
 * @param caps  the manifest from `capabilities()`
 * @param ctx   the read-model: `{ frames, starts, texts, shapes, transitions,
 *              audioTracks, totalMs, caps }`
 * @returns {{ plan, dropped }} — `dropped` is `[{ index, verb, why }]`
 */
export function validatePlan(raw, caps, ctx) {
  const dropped = [];
  const source = raw && typeof raw === "object" ? raw : {};

  const include = { ...defaultInclude() };
  for (const key of INCLUDE_KEYS) {
    if (typeof source.include?.[key] === "boolean") include[key] = source.include[key];
  }

  const steps = [];
  // ⚠ REFS ARE TRACKED AS THE PLAN IS READ, not as it runs, so a step that
  // addresses a clip no earlier step created is dropped in the PREVIEW rather
  // than silently doing nothing halfway through. Forward references are the
  // failure a generated plan makes most often, and they are invisible at run
  // time — `byRef` simply finds nothing and the step is a no-op that reported
  // success.
  const promised = new Set();

  const list = Array.isArray(source.steps) ? source.steps : [];
  list.forEach((step, index) => {
    const verb = text(step?.verb);
    const action = ACTIONS[verb];
    if (!action) {
      dropped.push({ index, verb: verb || "(none)", why: `there is no “${verb}” verb` });
      return;
    }
    const governor = governingKey(verb);
    if (governor && !include[governor]) {
      dropped.push({ index, verb, why: `${governor} are switched off for this run` });
      return;
    }
    const ref = text(step?.args?.ref);
    if (ref && !action.creates && !promised.has(ref)) {
      dropped.push({ index, verb, why: `nothing in this plan creates “${ref}”` });
      return;
    }
    const checked = validateStep({ verb, args: step?.args }, caps, ctx);
    if (!checked.ok) {
      dropped.push({ index, verb, why: checked.why });
      return;
    }
    if (action.creates && checked.args.ref) promised.add(checked.args.ref);
    steps.push({
      // The step's own id, for the rail's React keys and for reporting which
      // step a run stopped on. Generated rather than taken from the plan: a
      // model that repeats an id would give two rows the same key.
      id: `p${index + 1}`,
      verb,
      args: checked.args,
      note: text(step?.note),
    });
  });

  return {
    plan: {
      version: PLAN_VERSION,
      summary: text(source.summary),
      mood: text(source.mood),
      language: text(source.language),
      include,
      steps,
    },
    dropped,
  };
}

/**
 * What a plan adds up to — the counts under the preview's table.
 *
 * ⚠ COUNTED FROM THE STEPS, NOT CARRIED ON THE PLAN. A planner that reported its
 * own totals would eventually report totals for the steps it wrote rather than
 * the ones that survived validation, and the number under the table is the one
 * the user checks the table against.
 */
export function planTotals(plan) {
  const steps = plan?.steps || [];
  const count = (...verbs) => steps.filter((s) => verbs.includes(s.verb)).length;
  return {
    steps: steps.length,
    transitions: count("add_transition"),
    effects: count("add_effect"),
    texts: count("add_text"),
    shapes: count("add_shape"),
    moves: count("push_in"),
    retimes: count("set_shot_duration", "set_all_durations"),
  };
}

/** An empty, valid plan — what the panel holds before anything has been read. */
export function emptyPlan() {
  return {
    version: PLAN_VERSION,
    summary: "",
    mood: "",
    language: "",
    include: defaultInclude(),
    steps: [],
  };
}
