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
 * HOW LONG A HOLD HAS TO BE BEFORE THE CUT GOES THROUGH BLACK RATHER THAN
 * THROUGH THE PICTURE.
 *
 * A dissolve says "a moment passed"; a dip says "that scene is over". Both are
 * read off the same number — how long the outgoing shot was held against the
 * median — because that is the only evidence a timeline carries about where a
 * scene ends, and the ladder between them is what stops every treated cut in the
 * film being the same gesture.
 */
const SCENE_BREAK = 2.2;

/**
 * HOW OFTEN A TREATED CUT IS SOMETHING OTHER THAN A DISSOLVE.
 *
 * ⚠ THIS FILE USED TO PLACE `dissolve` AND `dip` AND NOTHING ELSE, and the note
 * here argued for it: "a treatment nobody chose is the first thing the user
 * deletes". That argument is right about VARIETY FOR ITS OWN SAKE and wrong
 * about what an editor's timeline actually looks like — reported off a 27-shot
 * cut where all eleven treated cuts came out as the same dissolve: "mai dekha
 * hi transition bas dissolve hi lagaya gaya, transition alag alag use hona
 * chahiye video editor ke hisab se soch kar".
 *
 * So the answer is the same shape as `stillMove`'s one section down — the rule
 * that replaced a four-way rotation of camera moves with a WEIGHTED pattern —
 * and for the same reason. A cut that is different every time reads as a tour of
 * the library; a cut that is never different reads as a preset. What an editor
 * does is keep one gesture as the house one and spend the others rarely:
 *
 *     treated cut   1    2    3    4      5    6     7    8      9    10
 *     treatment     dis  dis  dis  slide  dis  wipe  dis  slide  dis  dis
 *
 *   · A DISSOLVE is the default and carries most of the film. It is the cut that
 *     says "a moment passed" and does not draw attention to itself.
 *   · A DIP still overrides everything on a hold long enough to read as the end
 *     of a scene — that is a decision about the FILM, and it outranks a pattern
 *     about the cuts.
 *   · A SLIDE every `SLIDE_EVERY` treated cuts: one shot shoulders the next out
 *     of frame, which is what an editor reaches for between two shots of equal
 *     weight that are not a scene change.
 *   · A WIPE every `WIPE_EVERY`, and no oftener, because it is the most graphic
 *     gesture here. The direction ALTERNATES between them and the second one is
 *     an angled edge (`diagonal`), so the two nearest wipes in a film are never
 *     the same gesture twice — the rule the pans already keep.
 *
 * Indexed by the treated cut's POSITION, so it stays deterministic; see the file
 * header.
 */
const SLIDE_EVERY = 4;
const WIPE_EVERY = 6;

/**
 * A MOVING EDGE IS QUICKER THAN A DISSOLVE, and it has to be. A cross-fade is
 * read as a soft join and can take a beat over it; an edge travelling across the
 * frame is a gesture the eye follows, and one held for 1.4s stops being
 * punctuation and becomes an event of its own. Same 100ms quantisation as the
 * dissolve length, and floored so it stays visible.
 */
function quickly(ms) {
  return Math.max(300, Math.round((ms * 0.6) / 100) * 100);
}

/**
 * WHAT A TREATED CUT LOOKS LIKE, from the hold in front of it AND its position
 * in the run of treated cuts.
 *
 * ⚠ THE LENGTH USED TO SATURATE, AND THAT IS WHY EVERY DISSOLVE WAS 1.2s. It was
 * `hold × 0.25` clamped to 400–1200, so anything held past 4.8s hit the ceiling
 * — on a film whose long shots run 6s and 9.8s, both came out at exactly 1200ms
 * and the plan read as one gesture repeated. Reported as "you keep the same
 * dissolve". This curve reaches its ceiling at a hold of 2.75× the median
 * instead, so two different pauses get two different lengths, and it is
 * quantised to 100ms so the number reads as a decision rather than as
 * arithmetic showing through.
 *
 * ⚠ EVERY KIND IS CHECKED AGAINST THE CAPS BEFORE IT IS ASKED FOR, and falls
 * back towards the dissolve when the build does not render it. The caps table is
 * derived from `TRANSITIONS` every time it is asked for (see `capabilities.js`),
 * so a treatment removed from the renderer must not be able to make this planner
 * propose a step the validator then drops — the preview would list a film that
 * cannot be made, which is the one thing the cap note over `housePlan` says must
 * never happen.
 *
 * @param ratio how long the outgoing shot held, over the median
 * @param caps  the capability manifest
 * @param at    this cut's index among the TREATED cuts, 0-based
 */
function treatmentFor(ratio, caps, at = 0) {
  const kinds = new Set(
    ((caps && caps.transitions) || []).map((t) => (typeof t === "string" ? t : t.id))
  );
  const ms = Math.min(1400, Math.max(400, Math.round((300 + 400 * ratio) / 100) * 100));
  const dissolve = { kind: "dissolve", ms, params: {} };
  // A hold long enough to read as the end of a scene goes out through black
  // whatever the pattern says. See `SCENE_BREAK`.
  if (ratio >= SCENE_BREAK && kinds.has("dip")) return { kind: "dip", ms, params: {} };

  const n = at + 1;
  // ⚠ THE WIPE IS TESTED FIRST, so a cut that is both (12, 24, …) wipes rather
  // than slides — the rarer gesture wins the collision, exactly as the pan does
  // in `stillMove`, or the wipes thin out to nothing on a long film for no
  // reason the user could ever see.
  if (n % WIPE_EVERY === 0) {
    const second = (n / WIPE_EVERY) % 2 === 0;
    const kind = second && kinds.has("diagonal") ? "diagonal" : "wipe";
    if (kinds.has(kind)) {
      return { kind, ms: quickly(ms), params: { direction: second ? "left" : "right" } };
    }
  }
  if (n % SLIDE_EVERY === 0 && kinds.has("slide")) {
    return {
      kind: "slide",
      ms: quickly(ms),
      params: { direction: (n / SLIDE_EVERY) % 2 ? "left" : "right" },
    };
  }
  return dissolve;
}

/** What the note says a treatment was chosen FOR. Read by a person, not by code. */
const TREATMENT_WHY = {
  dip: " — long enough to read as the end of a scene",
  slide: " — the next shot pushes it out of frame",
  wipe: " — an edge travels across",
  diagonal: " — an angled edge, the other way from the last one",
};

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
 * ⚠ AND WHEN NOTHING IS BEING RENDERED, EVERY DRAWING MOVES INSTEAD.
 *
 * The two rules above are about a film that is going to become FOOTAGE: a move
 * is emphasis, so most shots must be locked off or it stops meaning anything.
 * With Veo un-ticked there is no footage coming and the stills are the finished
 * film — and then the same restraint produces a slideshow with a push on three
 * shots out of fourteen and nothing on the other eleven, which is what was
 * reported ("add zoom in / zoom out / left / right on all the images when Veo is
 * not selected"). A rostrum camera never sat still over artwork, and this is
 * the same job.
 *
 * ⚠ BUT IT IS NOT A FOUR-WAY ROTATION, AND THAT WAS THE BUG. It used to be
 * `["zoom_in", "pan_right", "zoom_out", "pan_left"]` indexed by position, which
 * put a PAN on every other shot — and a rostrum camera does not do that. A pan
 * drags the frame sideways across a drawing that was composed to be looked at
 * straight on, so half a film of them reads as the picture sliding about;
 * reported as "you scale keyframe and left right, not good looking … keep only
 * the most used, zoom in, and some clips zoom out, and very few left and right".
 *
 * So the pattern is WEIGHTED rather than rotated, and the weights are the ones
 * that were asked for:
 *
 *     shot   1  2  3  4  5  6  7  8  9 10 11 12
 *     move   in in out in ←  out in in out  →  in in
 *
 *   · ZOOM IN is the default and carries most of the film.
 *   · ZOOM OUT every `PULL_EVERY` shots, so the film breathes both ways.
 *   · A PAN every `PAN_EVERY` shots and no oftener — and the direction
 *     ALTERNATES between them, so the two nearest pans in the film are never the
 *     same gesture twice ("left in 5, so right in 10").
 *
 * Two zoom-ins in a row is deliberate: they are the shot that does not draw
 * attention to itself, and `moveAmount` already gives two neighbouring pushes
 * different distances because the shots are different lengths. Indexed by
 * position, so it stays deterministic — see the file header.
 */
const PULL_EVERY = 3;
const PAN_EVERY = 5;

function stillMove(at) {
  const n = at + 1;
  // ⚠ THE PAN IS TESTED FIRST, so a shot that is both (15, 30, …) pans rather
  // than pulls back — the rarer gesture wins the collision, or the pans thin out
  // to nothing on a long film for no reason the user could ever see.
  if (n % PAN_EVERY === 0) return (n / PAN_EVERY) % 2 ? "pan_left" : "pan_right";
  if (n % PULL_EVERY === 0) return "zoom_out";
  return "zoom_in";
}

/**
 * How big a move a shot of this length can carry, as a multiple of the house one.
 *
 * ⚠ A SHORT SHOT GETS A SMALLER MOVE RATHER THAN NO MOVE. The original rule
 * skipped anything under `MOVING_SHOT` because a full push across 1.2s reads as
 * a wobble — true, and the fix is to travel less far, not to leave the shot dead
 * while the ones on either side of it move. Clamped both ends so a 12-second
 * hold does not slowly crawl across the whole picture either.
 */
function moveAmount(lengthMs, median) {
  const ratio = (Number(lengthMs) || 0) / Math.max(1, median);
  return Math.round(Math.max(0.6, Math.min(1.4, ratio)) * 100) / 100;
}

/**
 * Can this clip carry a Ken Burns move at all?
 *
 * ⚠ IT NAMES WHAT CANNOT, RATHER THAN WHAT CAN, and that is deliberate. Two
 * kinds of clip have nothing to gain from a move: FOOTAGE, which moves by itself
 * — scaling and panning a Veo take on top of the motion already inside it is two
 * cameras fighting — and a COLOUR CARD, which `placePicture` ignores the
 * transform of anyway because a zoomed flat colour is the same flat colour.
 * Everything else is a drawing.
 *
 * Written this way round because a positive list silently excludes whatever it
 * has not heard of: a frame carrying no `src` at all (every animatic saved
 * before sources were recorded, and every fixture) would stop moving, which is a
 * feature that quietly does nothing on exactly the projects nobody tests with.
 */
function isStill(frame) {
  const kind = (frame && frame.src && frame.src.kind) || "";
  return kind !== "video" && kind !== "color";
}

/**
 * HOW MANY CUTS MAY BE TREATED — and it depends on what the film is made of.
 *
 * ⚠ ONE FUNCTION, READ BY THE PLANNER AND BY THE FENCE. They each had their own
 * copy of this arithmetic, and the moment the planner started placing a
 * transition on every OTHER cut (`include.veo === false`) the fence's 35% share
 * would have trimmed four of them back to two — a preview showing a film that
 * could not be made, which is the one thing the ⚠ note over the planner's cap
 * says must never happen.
 *
 * With Veo ON a transition is emphasis, and 35% of the cuts is the house limit
 * it has always been. With Veo OFF the stills ARE the film and the pattern the
 * user asked for is alternate cuts, so the ceiling is half of them — which
 * `ceil` makes exactly the number "every other cut from cut 1" produces.
 */
export function transitionBudget(shots, include) {
  const cuts = Math.max(0, (Number(shots) || 0) - 1);
  if (include && include.veo === false) return Math.max(1, Math.ceil(cuts / 2));
  return Math.max(1, Math.floor(cuts * HOUSE_CAPS.TRANSITION_CUT_SHARE));
}

/**
 * A MOVE ON EVERY DRAWING THAT HASN'T GOT ONE — the rule, as a step generator.
 *
 * ⚠ IT IS EXPORTED BECAUSE THE RULE IS NOT THE RULES PLANNER'S. "Nothing is
 * being rendered, so the stills ARE the film and a still that never moves is a
 * slide" is true of every plan on screen, and the model's plan is a plan on
 * screen. Left inside `housePlan` it only applied to the free door: a user who
 * pressed "Read my film" with Veo un-ticked got the model's three tasteful
 * push-ins and six dead shots between them, which is precisely what was reported
 * — "i told you set scale in all clip" — with a screenshot of a nine-shot plan
 * carrying three moves. `adopt` now runs it over whichever plan it is holding,
 * so the tick box means the same thing whoever wrote the film.
 *
 * ⚠ AND IT FILLS GAPS RATHER THAN OVERWRITING. A shot the plan already has an
 * opinion about is left alone — including `clear_shot_motion`, which is a
 * deliberate "hold this one". `skip` is the set of shot NUMBERS already spoken
 * for; everything else that is a drawing gets `stillMove`'s weighting, indexed
 * by its position in the film so the pattern reads the same either way: a pan
 * every 5th shot whether the model wrote that shot's neighbour or this did.
 *
 * @param frames the shot row
 * @param skip   `Set` of 1-based shot numbers the plan already moves
 */
export function stillMoveSteps(frames, skip) {
  const list = frames || [];
  const median = medianDuration(list);
  const taken = skip instanceof Set ? skip : new Set(skip || []);
  const steps = [];
  let at = 0;
  list.forEach((frame, i) => {
    if (!isStill(frame)) return;
    // ⚠ THE COUNTER ADVANCES FOR A SKIPPED SHOT TOO. It is a position in the
    // FILM, not in the list of shots this happens to be writing — stopping the
    // clock on the shots the model moved would slide every pan after them onto
    // a different shot than the one the pattern says.
    const kind = stillMove(at);
    at += 1;
    if (taken.has(i + 1)) return;
    steps.push({
      verb: "add_shot_motion",
      args: {
        shot: i + 1,
        kind,
        amount: moveAmount(Math.max(HOUSE_CAPS.MIN_CLIP_MS, Number(frame?.duration_ms) || 0), median),
        ease: "ease-in-out",
      },
      note: "nothing is being rendered, so the drawing moves",
    });
  });
  return steps;
}

/**
 * THE VERBS THAT ARE AN OPINION ABOUT HOW A SHOT MOVES.
 *
 * `clear_shot_motion` is in the list on purpose: "hold this shot still" is an
 * opinion, and the filler is for shots nobody has one about.
 */
const MOTION_VERBS = new Set(["add_shot_motion", "push_in", "clear_shot_motion"]);

/**
 * THE SAME RULE, APPLIED TO A PLAN SOMEBODY ELSE WROTE. Returns a new plan.
 *
 * A no-op unless the Veo box is OFF — with footage coming, a move is emphasis
 * and most shots have to stay locked off or it stops meaning anything, which is
 * the argument written over `STILL_CYCLE`'s replacement above.
 *
 * ⚠ IT RUNS BEFORE `validatePlan`, NOT AFTER THE FENCE, so what it adds goes
 * through exactly the same door as everything else: a step naming a shot that
 * does not exist is dropped with a reason, and the preview table is the film
 * that gets made. See `adopt` in `useDirectorRun.js`.
 */
export function fillStillMoves(plan, ctx) {
  const source = plan && typeof plan === "object" ? plan : {};
  if (!source.include || source.include.veo !== false) return source;
  const steps = Array.isArray(source.steps) ? source.steps : [];
  const taken = new Set();
  for (const step of steps) {
    if (!step || !MOTION_VERBS.has(step.verb)) continue;
    const shot = Number(step.args && step.args.shot);
    if (Number.isFinite(shot)) taken.add(shot);
  }
  const added = stillMoveSteps((ctx && ctx.frames) || [], taken);
  if (!added.length) return source;
  return { ...source, steps: [...steps, ...added] };
}

/**
 * THE VERBS THAT ARE AN OPINION ABOUT WHERE THE CUTS BREATHE.
 *
 * `remove_transition` is in the list on purpose, for the same reason
 * `clear_shot_motion` is in `MOTION_VERBS`: "make this a straight cut" is an
 * opinion, and the filler is only for a plan that has none at all.
 */
const TRANSITION_VERBS = new Set(["add_transition", "remove_transition", "set_transition_duration"]);

/**
 * A FILM WITH NO TRANSITIONS IN THE PLAN GETS THE HOUSE'S RHYTHM. Returns a new plan.
 *
 * ⚠ THIS IS `fillStillMoves`' TWIN AND IT IS HERE FOR THE SAME REASON, reported
 * the same way. A model handed eight shots of exactly four seconds, no
 * descriptions and no dialogue reads the whole thing as ONE scene — correctly —
 * and the polish prompt says a dissolve is earned on a SCENE BOUNDARY. One scene
 * has no boundaries, so it wrote zero transitions, and the run made eight hard
 * cuts. Reported as "se not transition add … without transiition and music not
 * complate video".
 *
 * ⚠ AND THE ANSWER IS NOT TO ARGUE WITH THE PROMPT. "Restraint is the craft" is
 * right and it stays; what was missing is the same thing the moves were missing
 * — a HOUSE RULE about the film, applied on the one path every plan takes, for
 * the shots the plan has no opinion about. `fillStillMoves`' header already
 * makes the argument: press Run with Veo un-ticked and the stills ARE the
 * finished film, so a film of eight hard cuts is a slideshow.
 *
 * ⚠ IT RE-ASKS `housePlan` RATHER THAN RE-IMPLEMENTING THE RULE, which is the
 * whole reason this is five lines. The alternating-cut rule, the budget, the
 * never-two-in-a-row spacing and the dissolve/dip choice are all decided in one
 * place and were argued for over three rounds of user reports; a second copy
 * here would be a second answer that drifts. The rules planner is pure,
 * deterministic and free, so asking it costs nothing.
 *
 * ⚠ ONLY WHEN THE PLAN SAYS NOTHING AT ALL. One `add_transition` anywhere and
 * this is a no-op — a model that placed two dissolves has an opinion about the
 * rhythm, and filling in around it would be overruling a decision rather than
 * supplying a missing one.
 *
 * ⚠ AND IT RUNS BEFORE `validatePlan`, like its twin, so what it adds goes
 * through exactly the same door: a dissolve across a gap is dropped with a
 * reason and the preview table is the film that gets made. See `adopt` in
 * `useDirectorRun.js`.
 */
export function fillAlternateTransitions(plan, ctx) {
  const source = plan && typeof plan === "object" ? plan : {};
  const include = source.include;
  if (!include || include.transitions === false) return source;
  const steps = Array.isArray(source.steps) ? source.steps : [];
  if (steps.some((step) => step && TRANSITION_VERBS.has(step.verb))) return source;
  const house = housePlan(ctx, { include });
  const added = (house.steps || [])
    .filter((step) => step.verb === "add_transition")
    .map((step) => ({
      ...step,
      // ⚠ RE-NOTED, because the note is read by a person deciding whether to
      // trust the plan. The house planner's own wording ("shot 3 holds 4.0s") is
      // an argument about rhythm; here the argument is "the AI had no opinion
      // about the cuts, so the house filled them in", and saying the first would
      // hide which planner actually made this decision.
      note: "the plan left every cut straight, so the house rhythm filled them in",
    }));
  if (!added.length) return source;
  return { ...source, steps: [...steps, ...added] };
}

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
    const budget = transitionBudget(frames.length, include);
    const candidates = [];
    // ⚠ WITH VEO OFF, EVERY OTHER CUT IS TREATED — asked for three times ("i
    // told you set alternate", "you do transition on alternate clip") and this
    // is the rule that answers it. The old one only made a cut a CANDIDATE when
    // the shot before it was a long hold, which is right for a film about to
    // become footage and wrong for one made of stills: on a board where every
    // shot is the same length there are no long holds, so there were no
    // candidates, so a film of eight shots got ZERO transitions and the user
    // watched eight hard cuts.
    //
    // ⚠ FROM CUT 1, SO THE FIRST CUT IS TREATED. Alternating from cut 2 leaves
    // the opening cut bare, which reads as the effect starting late rather than
    // as a pattern.
    if (include.veo === false) {
      for (let cut = 1; cut < frames.length; cut += 2) {
        candidates.push({ cut, before: lengthOf(frames[cut - 1]), alternating: true });
      }
    } else {
      for (let cut = 1; cut < frames.length; cut += 1) {
        const before = lengthOf(frames[cut - 1]);
        if (before >= median * LONG_SHOT) candidates.push({ cut, before });
      }
    }
    // The longest holds win the budget — those are the pauses most worth
    // marking. Ties break towards the EARLIER cut so the choice is stable.
    // ⚠ AND THEY ALTERNATE — NEVER TWO TREATED CUTS IN A ROW. The budget alone
    // put them wherever the longest holds happened to be, and on a board where
    // two held shots sit side by side that came out as a transition on BOTH
    // sides of one shot: it fades up and starts fading out again before it has
    // been on screen whole, and the row of markers reads as a cluster rather
    // than as a rhythm. Reported as "i told you set alternate … and you add like
    // this", with a screenshot of the shot-3/shot-4/shot-5 run.
    //
    // ⚠ IT IS THE SAME RULE THE MOVES ALREADY KEEP one section down ("never
    // twice in a row"), for the same reason: a treatment only reads as one while
    // the cut on either side of it is plain. Greedy over the longest holds, so
    // when two candidates collide the more deserving one wins the cut.
    // ⚠ THE ALTERNATING LIST IS TAKEN IN FILM ORDER, NOT SORTED BY HOLD. Sorting
    // it would still produce the right NUMBER of transitions and put them
    // wherever the longest shots happened to be — which is the clustering this
    // rule exists to replace. The emphasis list (Veo on) is still greedy over
    // the longest holds, because there the point IS which shot is longest.
    const ordered =
      include.veo === false
        ? candidates
        : candidates.sort((a, b) => b.before - a.before || a.cut - b.cut);
    const taken = [];
    for (const row of ordered) {
      if (taken.length >= budget) break;
      if (taken.some((t) => Math.abs(t.cut - row.cut) < 2)) continue;
      taken.push(row);
    }
    taken
      .sort((a, b) => a.cut - b.cut)
      // ⚠ `at` IS THE POSITION AMONG THE TREATED CUTS, NOT AMONG ALL OF THEM,
      // and it is counted after the sort so it runs in FILM order. Indexing the
      // pattern by the raw cut number would put a slide wherever the arithmetic
      // of "every 4th cut" happened to coincide with a treated one — which on an
      // alternating plan is every other treated cut, and on an emphasis plan is
      // none of them.
      .forEach(({ cut, before }, at) => {
        // A longer hold gets a longer transition, a hold long enough to be a
        // scene break gets a different KIND of one, and the ordinary cuts follow
        // the house pattern. See `treatmentFor`.
        const ratio = before / median;
        const { kind, ms, params } = treatmentFor(ratio, ctx && ctx.caps, at);
        steps.push({
          verb: "add_transition",
          args: { cut, kind, ms, ...(Object.keys(params || {}).length ? { params } : {}) },
          note: `shot ${cut} holds ${(before / 1000).toFixed(1)}s${TREATMENT_WHY[kind] || ""}`,
        });
      });
  }

  // ------------------------------------------------------------------ moves
  // ⚠ TWO DIFFERENT FILMS, AND THE TICK BOX SAYS WHICH ONE THIS IS. With Veo on,
  // most of these shots are about to become footage and a move is EMPHASIS — so
  // it goes on the few shots held long enough to earn one. With Veo off the
  // stills ARE the film, and a still that never moves is a slide. See
  // `STILL_CYCLE`.
  if (include.veo === false) {
    steps.push(...stillMoveSteps(frames));
  } else {
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
  }

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
  // ⚠ THE SAME FUNCTION THE PLANNER USES. See `transitionBudget`: a plan that
  // alternates its transitions is house-legal, and a fence with its own idea of
  // the ceiling would trim half of them out from under the preview.
  const cutBudget = transitionBudget(frames.length, plan.include);
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
      if (cutsUsed.size >= cutBudget) {
        drop(`${cutsUsed.size} cuts are treated already — the house limit is ${cutBudget}`);
        return;
      }
      // ⚠ AND NOT ON THE CUT NEXT DOOR TO ONE. A shot with a transition on both
      // sides is never fully on screen — it fades up and begins fading out — and
      // a run of treated cuts reads as a cluster rather than as a rhythm. The
      // rules planner already places them alternating (see `housePlan`); this is
      // the same rule for a plan the MODEL wrote, which is where the clustered
      // ones the user photographed came from.
      if (cutsUsed.has(step.args.cut - 1) || cutsUsed.has(step.args.cut + 1)) {
        drop(
          `the cut after shot ${step.args.cut} touches one that is already ` +
            "treated — transitions alternate, so the shot between them is not " +
            "dissolving at both ends"
        );
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
