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
 * ⚠ TWO OF THESE ARE NOT FREE, AND THEY ARE THE LAST TWO. Every other key here
 * governs verbs, and a verb is an edit the editor already knows how to make.
 * Phase 3 wired the existing `/voiceover` pass in beside them and Phase 4 the
 * Veo pass; both SPEND and both MOVE PICTURES, so the flags are the same shape
 * as the others and the panel treats them as anything but (see `PASS_GOVERNORS`
 * below, and the price on the Run button).
 *
 * ⚠ AND `veo` IS THE EXPENSIVE ONE BY AN ORDER OF MAGNITUDE — a voiceover is
 * cents, a 48-shot render is tens of dollars — which is why it is the one flag
 * in this list that does NOT default to on. See `defaultInclude`.
 *
 * The reason the flags live together is so the total the user is shown and the
 * work that actually happens are read off ONE object.
 */
export const INCLUDE_KEYS = [
  "transitions",
  "effects",
  "text",
  "shapes",
  // ⚠ TWO MORE PASSES, AND THEY ARE FREE — which is why they sit here among the
  // free flags rather than beside `voiceover` and `veo`. Phase D fetches the
  // sound effects the reading cued and phase E the music bed; both take files
  // from Freesound, which spends the deployment's shared REQUEST budget and not
  // a penny of anybody's money. See `sound_pass.js`, and `PAID_PASSES` below for
  // the line the panel actually draws.
  "sfx",
  "music",
  "captions",
  "voiceover",
  "veo",
  // ⚠ AND THE THIRD SPENDING PASS — 🖼 Animatic images, blocking every board
  // shot out as key poses. It spends the account's IMAGE quota rather than
  // dollars, which is why it has a count on the Run button where the other two
  // have a price; it is a spend all the same, so it sits down here with them and
  // it starts un-ticked. See `poses_pass.js`.
  "poses",
];

/**
 * THE FLAGS THAT START OFF, AND THEY ARE OFF BECAUSE OF WHAT THEY COST.
 *
 * ⚠ A DEFAULT-ON TICK BOX IS A BOX MOST PEOPLE NEVER LOOK AT, and the first
 * time anyone looked at one of these it would be on an invoice. `veo` renders
 * every shot in the film — a 48-shot board runs to tens of dollars — and
 * `poses` buys four drawings per second of film out of the account's image
 * quota, which on the same board is over a thousand pictures. Both are things a
 * person asks for on purpose.
 *
 * ⚠ THE VOICEOVER IS NOT IN THIS LIST, AND THAT IS NOT AN OVERSIGHT. It is
 * cents, it is what makes an animatic watchable, and it writes the captions.
 */
const OFF_BY_DEFAULT = ["veo", "poses"];

/**
 * What the preview opens with: everything on except the two big spenders.
 *
 * So the Director writes the motion prompts, prices them, shows the price — and
 * waits to be asked.
 */
export function defaultInclude() {
  return Object.fromEntries(
    INCLUDE_KEYS.map((key) => [key, !OFF_BY_DEFAULT.includes(key)])
  );
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

/**
 * The include flags governing a PASS rather than a verb.
 *
 * ⚠ A PASS IS NOT A VERB, AND IT MUST NOT BECOME ONE. A verb is synchronous, it
 * runs inside one React commit, it spends nothing and it calls a function a
 * person's own button calls. Phase B is a server call that takes a minute, costs
 * money and re-lays the picture row underneath the steps that follow it — every
 * one of those is a reason it cannot be a row in `ACTIONS`. So it is a phase of
 * the RUNNER, and this is how its flag reaches the same tick-box column as the
 * verbs': by being declared, not by being inferred from a registry it is
 * deliberately not in. See `voice_pass.js`.
 */
const PASS_GOVERNORS = ["voiceover", "veo", "sfx", "music", "poses"];

/**
 * AND WHICH OF THE PASSES SPEND. This is the line the panel draws.
 *
 * ⚠ "IS IT A PASS" AND "DOES IT SPEND" ARE TWO DIFFERENT QUESTIONS, and until
 * phase D they had the same answer, so `paidKeys` was allowed to read
 * `PASS_GOVERNORS` directly. The sound passes broke that: they are passes in
 * every structural sense — a server call, a wait, a thing that is not a verb —
 * and they spend NOTHING. Listing them under "These two spend" would be the panel
 * telling the user a run costs money when it does not, which is the same class of
 * lie as the reverse and gets the box un-ticked out of caution.
 *
 * So the two questions get two tables. `PASS_GOVERNORS` decides whether a flag is
 * OFFERED at all (a pass reaches the tick-box column by being declared, since it
 * is deliberately not in `ACTIONS`); this decides which ROW it is offered in.
 */
/**
 * ⚠ "SPENDS" IS THE TEST, NOT "IS BILLED IN DOLLARS". 🖼 Animatic images joined
 * this list without a price: it draws four pictures per second of film out of
 * the account's IMAGE quota, which is a spend a user has to agree to in exactly
 * the way the other two are — and putting it in the free row beside Transitions
 * would be the panel calling a thousand generated images an edit. What it gets
 * on the Run button is a COUNT rather than a figure in dollars; see the button.
 */
const PAID_PASSES = ["voiceover", "veo", "poses"];

/**
 * The include flags that actually change something in THIS build — the tick
 * boxes the preview shows.
 *
 * ⚠ DERIVED, so the column cannot offer a switch that does nothing. `captions`
 * is in `INCLUDE_KEYS` because the flags are read off ONE object and phase B
 * reads it when it writes its subtitles — but no VERB answers to it and it is
 * not a pass of its own, so it is not offered here. A tick box that changes
 * nothing when you click it is worse than an absent one: it teaches the user the
 * whole panel is decorative.
 *
 * ⚠ `veo` JOINED THE LIST IN PHASE 4 and `voiceover` in Phase 3, both by being
 * DECLARED in `PASS_GOVERNORS` rather than inferred from a registry they are
 * deliberately not in. That is the whole mechanism by which a PASS reaches the
 * same column as a verb without becoming one.
 */
export function governedKeys() {
  const used = new Set([...Object.values(GOVERNED_BY), ...PASS_GOVERNORS]);
  return INCLUDE_KEYS.filter((key) => used.has(key));
}

/**
 * THE SAME LIST, SPLIT BY WHETHER TICKING IT COSTS MONEY.
 *
 * ⚠ THEY ARE TWO DIFFERENT KINDS OF SWITCH AND THE PANEL HAD BEEN DRAWING THEM
 * AS ONE ROW. Un-ticking "Effects" changes what the edit looks like; un-ticking
 * "Voiceover" cancels a pass that would have read the film aloud AND written the
 * captions under it. A user who meant to un-tick the render and un-ticked the
 * voiceover instead gets a run with no sound and no subtitles and no clue why —
 * reported as "i check mark all only i not select veo render … not come
 * voiceover and caption", with a screenshot in which Voiceover is plainly off
 * and `Veo renders` had wrapped onto its own line right underneath it.
 *
 * Derived from the same two tables as `governedKeys`, so a flag cannot appear in
 * one row and not the other.
 */
export function paidKeys() {
  return INCLUDE_KEYS.filter((key) => PAID_PASSES.includes(key));
}

export function freeKeys() {
  return governedKeys().filter((key) => !PAID_PASSES.includes(key));
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

  // ⚠ DID THE PERSON ASK FOR ALL OF THEM, IN SO MANY WORDS? Set by the planner
  // and read by `applyGuardrails`, which lifts every house SHARE when it is on.
  // It is deliberately not part of `include`: those flags are what the user
  // ticks in the preview, and this is a fact about what they typed.
  const askedForAll = source.asked_for_all === true || source.askedForAll === true;

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
      askedForAll,
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
    // ⚠ BOTH MOVE VERBS. `add_shot_motion` is the Ken Burns pass that runs when
    // nothing is being rendered (see `house_style.js`); counting only `push_in`
    // reported "0 moves" under a plan whose every shot moves, on the one kind of
    // run where the moves ARE the treatment.
    moves: count("push_in", "add_shot_motion"),
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
