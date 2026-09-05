/**
 * TRANSITIONS — what happens ON a cut, and it costs the timeline nothing.
 *
 * ⚠ THIS FILE HAS A TWIN: the `transition_*` half of `animatic_render.py`. Same
 * rules, two languages, so the Program monitor and the exported MP4 agree about
 * the picture. `tests/render_parity.py` evaluates a fixture through both and
 * fails on any difference; `tests/transition_check.py` proves the numbers reach
 * the video. Change one side, change the other, run both.
 *
 * ---------------------------------------------------------------------------
 * THE DESIGN DECISION, and it is the whole reason this is small
 * ---------------------------------------------------------------------------
 * A dissolve needs two pictures on screen at once, and there are two ways to
 * pay for that:
 *
 *   OVERLAPPING (CapCut's) — the transition eats duration/2 from each side, so
 *     the timeline gets SHORTER. That breaks `frameSpans`, every cut position,
 *     ripple and rolling trims, and any caption timed against a cut.
 *
 *   BOUNDARY-LOCAL (this) — the blend happens over the TAIL of the outgoing
 *     picture and the HEAD of the incoming one, d/2 either side of the cut.
 *     Nothing moves. Total length is unchanged, every existing timing rule
 *     survives, and no downstream code needed re-verifying.
 *
 * Boundary-local it is. A held still has no "extra" frames to give up anyway —
 * the pictures either side simply spend their last and first moments mixing.
 *
 * ---------------------------------------------------------------------------
 * WHICH PICTURE `sceneAt` CALLS "the frame" DURING A TRANSITION
 * ---------------------------------------------------------------------------
 * The outgoing one, for the WHOLE window — including the half that sits past
 * the cut, where `frameSpans` would say the incoming picture is up.
 *
 * ---------------------------------------------------------------------------
 * A TRANSITION IS TRACK-LOCAL, AND NEEDS A REAL CUT
 * ---------------------------------------------------------------------------
 * `after_frame_id` names the OUTGOING clip; the incoming one is the next clip on
 * THE SAME PICTURE TRACK whose start is exactly this one's end. Before tracks the
 * picture was one sequence with no holes in it, so "the next clip in the list"
 * meant the same thing and that is what this used to read. It no longer does:
 * clips are placed freely now (`frameSpans`), so the next clip in the list may be
 * on another track, and two clips can sit side by side without touching. A
 * transition with no cut under it is INERT — see `transitionWindow`.
 *
 * That is deliberate. It makes `mix` mean "how far through the transition"
 * (0 → 1, never doubling back) and `frame_b` mean "the picture arriving", which
 * is the only reading under which a wipe or a slide has a DIRECTION. With the
 * pair the other way round for the second half, a renderer would have to work
 * out which of the two was incoming before it could draw an edge travelling the
 * right way. Outside a transition window nothing changes: the half-open rule
 * still puts a cut on exactly one picture.
 */

// A clip's keyframe times are relative to its own start, so during a transition
// both pictures are resolved OUTSIDE their own span — the outgoing one past its
// end, the incoming one before its start. `valueAt` holds at the first and last
// key rather than extrapolating, so that is well defined and stays put.

/**
 * ---------------------------------------------------------------------------
 * PARAMETERS — the same shape as an effect's, and for the same reason
 * ---------------------------------------------------------------------------
 * A transition's `params` is a free dict on the wire (`AnimaticTransition` in
 * `server/schemas.py`), filled in from this table every time it is read. A
 * project saved before a parameter existed therefore picks up that parameter's
 * DEFAULT rather than failing validation — exactly the rule `EFFECT_PARAMS`
 * follows, and the reason neither needed a migration.
 *
 * A default that is a STRING is read straight off the transition and never
 * interpolated, which is every parameter there is so far: "half way between
 * left and up" is not a direction. If a numeric one is ever added,
 * `transitionParams` already coerces it as a number and `sceneSignature`
 * already formats it as one.
 *
 * `direction` MEANS THE DIRECTION OF TRAVEL — which way the wipe's edge sweeps,
 * which way the two pictures of a slide move. One definition for both, but the
 * DEFAULTS differ, because they are the behaviour that already shipped: a
 * wipe's edge has always travelled rightwards, and a slide has always pushed
 * the outgoing picture off to the left. Reproducing those exactly is what lets
 * this land without changing a single existing animatic.
 *
 * `color` on a dip defaults to "" and that means THE BAR COLOUR — the same
 * empty-string-is-inherit convention `lut.name` uses. A dip has always gone out
 * through `settings.background`, so "" reproduces it; naming a colour dips
 * through that one instead, letterbox bars and all.
 *
 * ⚠ TWIN of `TRANSITION_PARAMS` in `animatic_render.py`.
 */
export const TRANSITION_PARAMS = {
  dissolve: {},
  dip: { color: "" },
  wipe: { direction: "right", softness: 0 },
  slide: { direction: "left" },
  diagonal: { direction: "right", softness: 0 },
  split: { direction: "right", softness: 0 },
  radial: { softness: 0 },
  diamond: { softness: 0 },
  box: { softness: 0 },
  angular: { softness: 0 },
  blinds: { direction: "right", count: 6, softness: 0 },
  checker: { count: 6, softness: 0 },
};

// Which way a wipe's edge sweeps, or a slide's pictures travel.
export const TRANSITION_DIRECTIONS = ["left", "right", "up", "down"];

/**
 * ---------------------------------------------------------------------------
 * MATTES — the shape a REVEAL transition uncovers the arriving shot through
 * ---------------------------------------------------------------------------
 * ⚠ A REVEAL IS A SECOND MASK ON THE INCOMING PICTURE, not a compositing stage.
 * The full reasoning is at the top of `gl/shaders/mattes.js`; the short version
 * is that a wipe at 50% and a mask are the same operation — "show this picture
 * where <condition>" — so a matte is multiplied into the arriving picture's
 * alpha right next to the existing mask multiply. That keeps composite-over,
 * blend modes, chroma keys and per-clip masks all working, and costs no new
 * shader program and no extra framebuffers.
 *
 * ⚠ "none" IS INDEX 0 and means no matte, matching `MASK_KINDS` — the shader
 * tests `kind == 0` as its early out, so the order of this list is load-bearing
 * and is checked against the shader by `tests/effects_parity_check.py`.
 *
 * ⚠ TWIN of `MATTE_KINDS` in `animatic_render.py`.
 */
export const MATTE_KINDS = [
  "none",
  "linear",
  "diagonal",
  "split",
  "radial",
  "diamond",
  "box",
  "angular",
  "blinds",
  "checker",
];

/**
 * Which matte each kind reveals through. A kind that is NOT in here does not use
 * one — `dissolve` fades, `dip` veils and `slide` moves the geometry, and none
 * of those three is a reveal.
 *
 * `wipe` maps to `linear` rather than being called that, because "wipe" is the
 * name it has always had in saved projects and renaming it would need a
 * migration to buy nothing.
 *
 * ⚠ TWIN of `TRANSITION_MATTE` in `animatic_render.py`.
 */
export const TRANSITION_MATTE = {
  wipe: "linear",
  diagonal: "diagonal",
  split: "split",
  radial: "radial",
  diamond: "diamond",
  box: "box",
  angular: "angular",
  blinds: "blinds",
  checker: "checker",
};

/** The matte a resolved kind draws through, or "none". */
export function transitionMatte(kind) {
  return TRANSITION_MATTE[kind] || "none";
}

// The parameters whose value is an ENUM rather than free text. Anything not on
// the list folds down to the default HERE, in the resolver, so the preview and
// the export can't fold differently — the same rule `kind` and `ease` follow.
// Keyed by parameter NAME because `direction` means the same thing on every
// kind that offers it, which is the only reason one table can serve them all.
export const TRANSITION_PARAM_CHOICES = { direction: TRANSITION_DIRECTIONS };

// The same rule for the NUMERIC parameters: clamped HERE, in the resolver, so a
// softness of -3 or a count of 0 cannot mean one thing in the monitor and
// another in the export. Keyed by parameter NAME for the reason the choices
// table is — `softness` means the same thing on every kind that offers it.
//
// ⚠ AND SO THE SIGNATURE STAYS HONEST. `sceneSignature` writes the RESOLVED
// value, so a project carrying an out-of-range number signs as the number that
// will actually be drawn rather than as the one nobody will ever see.
//
// ⚠ TWIN of `TRANSITION_PARAM_RANGE` in `animatic_render.py`.
export const TRANSITION_PARAM_RANGE = {
  softness: [0, 1],
  count: [2, 64],
};

/**
 * ---------------------------------------------------------------------------
 * FAMILIES — how the TREATMENT ROW is grouped, and nothing else
 * ---------------------------------------------------------------------------
 * ⚠ PRESENTATION, NOT MODEL, so this is deliberately NOT twinned in Python.
 * `animatic_render.py` already carries no `label` and no `note` for the same
 * reason: which chips sit under which heading cannot change a single pixel, and
 * a renderer that read it would be reading something no renderer needs.
 *
 * It exists because the row went from FOUR chips to TWELVE when the matte
 * reveals landed, and twelve flat chips is not a row you read, it is a row you
 * scan. Grouping them is what keeps "which treatment is this" a glance.
 *
 * ⚠ THIS IS A SECOND, DIFFERENT GROUPING OF THE SAME TWELVE KINDS from the one
 * in `fx_library.js`, and that is on purpose rather than drift. They answer two
 * questions. The library answers "what can I add", where a dip belongs under
 * Dissolve because *Dip to Black* is what an editor goes looking for. This
 * answers "what is this cut doing", where a dip is its own thing — the only
 * treatment that puts NO second picture on screen. Filing them identically
 * would make one of the two wrong.
 */
export const TRANSITION_FAMILIES = [
  { id: "fade", label: "Fade" },
  { id: "wipe", label: "Wipe" },
  { id: "shape", label: "Shape" },
  { id: "slide", label: "Slide" },
  { id: "dip", label: "Dip" },
];

// `params` is derived from the table rather than written out again: the two
// drifting apart would mean a control the renderer ignores, or a parameter with
// no control, and neither would show up as an error anywhere.
//
// `family` is the heading each chip sits under. A kind whose family is not in
// `TRANSITION_FAMILIES` still draws — see `transitionsByFamily` — because a
// treatment nobody filed should be visible and ugly, never invisible, which is
// the rule `fx_library.js` follows for exactly the same reason.
// ⚠ `note` IS WHAT IT DOES. `when` IS WHAT IT MEANS, AND IT IS FOR THE AI.
//
// The chips in the pane show `note` — "Cross-fade", "An edge travels across" —
// because a person picking one by hand can see the picture and only needs
// reminding which is which. The AI cannot see anything, and a list of twelve
// mechanisms with no editorial meaning attached has exactly one safe answer:
// dissolve, every time.
//
// Which is precisely what shipped. Asked to treat a fourteen-shot Ganesh
// Chaturthi reel, the ✨ AI Editor came back with "Dissolve on the cut after
// shot 1 … Dissolve on the cut after shot 13" — thirteen identical dissolves.
// Reported from the screen: *"Dissolve on the cut hi use kar raha hai … in do
// shot ke bich mai konsa badhiyan transition rahega waisa set kare"*. It was not
// being lazy; nothing had ever told it what a wipe is FOR.
//
// ⚠ SO `when` LIVES ON THE TRANSITION, NOT IN THE PROMPT. A thirteenth kind
// added to this list without one would be a treatment the AI can render and can
// never knowingly choose — the same silent half-wiring E125 is about. Add a
// kind, write its `when`; `tests/transition_choice_check.py` fails until you do.
export const TRANSITIONS = [
  {
    id: "dissolve",
    family: "fade",
    label: "Dissolve",
    note: "Cross-fade",
    when: "Time passes, or two shots are one thought. The soft, invisible one — and it only reads as \"time passed\" because most cuts around it are straight.",
    params: Object.keys(TRANSITION_PARAMS.dissolve),
  },
  {
    id: "dip",
    family: "dip",
    label: "Dip",
    note: "Out through a colour",
    when: "A chapter ends. Through BLACK for a real break — a new scene, a new day, the end. Through WHITE for a flash, a memory, a burst of light, a photograph being taken.",
    params: Object.keys(TRANSITION_PARAMS.dip),
  },
  {
    id: "wipe",
    family: "wipe",
    label: "Wipe",
    note: "An edge travels across",
    when: "A deliberate move somewhere new, and it says so out loud. Energy and intent: ads, promos, reels, montages, a before-and-after.",
    params: Object.keys(TRANSITION_PARAMS.wipe),
  },
  {
    id: "slide",
    family: "slide",
    label: "Slide",
    note: "The next shot pushes in",
    when: "The next shot physically pushes the last one out. Quick and literal — a list, a product line-up, a step-by-step, a reel that must not lose its pace.",
    params: Object.keys(TRANSITION_PARAMS.slide),
  },
  // The matte-driven reveals. Every one of these is the SAME code path as a
  // wipe — a shape multiplied into the arriving picture's alpha — and they are
  // separate kinds rather than one kind with a `shape` parameter because the
  // Effects library files them as things you drag, and "drag Reveal, then find
  // the shape chip in another pane" is two gestures for what should be one.
  {
    id: "diagonal",
    family: "wipe",
    label: "Diagonal",
    note: "An angled edge, in from a corner",
    when: "A wipe with attitude. Sport, action, an upbeat promo, a music cut.",
    params: Object.keys(TRANSITION_PARAMS.diagonal),
  },
  {
    id: "split",
    family: "shape",
    label: "Split",
    note: "Barn doors open from the middle",
    when: "Barn doors opening. A reveal — a curtain going up on whatever is behind it.",
    params: Object.keys(TRANSITION_PARAMS.split),
  },
  {
    id: "radial",
    family: "shape",
    label: "Iris",
    note: "A circle opens from the centre",
    when: "An old-film iris. Nostalgia, a storybook, a children's film, the end of a joke.",
    params: Object.keys(TRANSITION_PARAMS.radial),
  },
  {
    id: "diamond",
    family: "shape",
    label: "Diamond",
    note: "An iris on its point",
    when: "A decorative iris. An ornamental or festive film — and used once, not as a house style.",
    params: Object.keys(TRANSITION_PARAMS.diamond),
  },
  {
    id: "box",
    family: "shape",
    label: "Box",
    note: "A rectangle opens from the centre",
    when: "A framed reveal. A product, a title card, a screen inside a screen.",
    params: Object.keys(TRANSITION_PARAMS.box),
  },
  {
    id: "angular",
    family: "shape",
    label: "Clock",
    note: "A hand sweeps round from twelve",
    when: "Time itself, drawn as a clock hand. A countdown, a schedule, a day passing, a deadline.",
    params: Object.keys(TRANSITION_PARAMS.angular),
  },
  {
    id: "blinds",
    family: "wipe",
    label: "Blinds",
    note: "Bands wipe together",
    when: "A brisk graphic break. Corporate, tech, a data segment, a section divider.",
    params: Object.keys(TRANSITION_PARAMS.blinds),
  },
  {
    id: "checker",
    family: "wipe",
    label: "Checker",
    note: "A chequerboard, in two passes",
    when: "Playful and retro. A kids' film, a game, a light-hearted vlog.",
    params: Object.keys(TRANSITION_PARAMS.checker),
  },
];

export const TRANSITION_KINDS = TRANSITIONS.map((t) => t.id);

/**
 * The treatments grouped for the pane: `[{ id, label, items }]`, families in
 * `TRANSITION_FAMILIES` order and the kinds inside each in `TRANSITIONS` order.
 *
 * ⚠ NOTHING IS EVER DROPPED. A kind whose `family` names no heading is collected
 * into a trailing "Other" group rather than vanishing, so adding a treatment to
 * `TRANSITION_PARAMS` and forgetting to file it leaves a chip you can see and
 * click — not a renderer feature with no way to reach it. An empty family is
 * omitted, so removing the last shape removes the heading with it.
 */
export function transitionsByFamily() {
  const groups = TRANSITION_FAMILIES.map((f) => ({
    id: f.id,
    label: f.label,
    items: TRANSITIONS.filter((t) => t.family === f.id),
  })).filter((g) => g.items.length > 0);

  const filed = new Set(TRANSITION_FAMILIES.map((f) => f.id));
  const loose = TRANSITIONS.filter((t) => !filed.has(t.family));
  if (loose.length) groups.push({ id: "other", label: "Other", items: loose });
  return groups;
}

/** An unrecognised kind is a dissolve. Folded HERE so nothing downstream folds. */
export function transitionKind(transition) {
  const kind = (transition || {}).kind;
  return TRANSITION_KINDS.includes(kind) ? kind : "dissolve";
}

/**
 * One transition's parameters with every default filled in. Mirrors
 * `transition_params`.
 *
 * Resolved against the FOLDED kind, not the stored one, so a transition whose
 * kind this build has never heard of gets a dissolve's parameters rather than a
 * newer kind's — the same picture both renderers will draw.
 */
export function transitionParams(transition) {
  const defaults = TRANSITION_PARAMS[transitionKind(transition)] || {};
  const stored = (transition || {}).params || {};
  const out = {};
  for (const [name, fallback] of Object.entries(defaults)) {
    const value = stored[name];
    const choices = TRANSITION_PARAM_CHOICES[name];
    if (typeof fallback === "string") {
      if (choices) out[name] = choices.includes(value) ? value : fallback;
      else out[name] = typeof value === "string" ? value : fallback;
    } else {
      const num = Number(value);
      const range = TRANSITION_PARAM_RANGE[name];
      let n = Number.isFinite(num) ? num : fallback;
      // Clamped but NOT rounded: `sceneSignature` already formats every number
      // to the shared precision, and rounding here as well would be a second
      // place for the two languages to round differently.
      if (range) n = Math.min(range[1], Math.max(range[0], n));
      out[name] = n;
    }
  }
  return out;
}

// Long enough to read as a transition rather than a soft cut, short enough to
// fit inside the 2s hold a frame gets by default.
export const DEFAULT_TRANSITION_MS = 600;
// A transition shorter than this is a cut with extra steps.
export const MIN_TRANSITION_MS = 100;
export const MAX_TRANSITION_MS = 10000;

/**
 * Where one transition actually sits, or null if it can't be placed.
 *
 * Null covers every way a transition can be inert rather than wrong: it names a
 * frame that has been deleted, or it hangs off the LAST frame, where there is
 * nothing to cut to. Those are left in the project rather than treated as
 * errors — deleting the frame after a transition shouldn't silently delete the
 * transition too, and re-adding a frame brings it back.
 *
 * ⚠ THE CLAMP IS WHAT KEEPS `transitionAt` SINGLE-VALUED. A transition is
 * capped at the SHORTER of the two holds it joins, so each half-window is at
 * most half of the shorter picture. Two transitions either side of one frame
 * can therefore meet in the middle but can never overlap, and no moment is ever
 * inside two of them.
 *
 * `spans` comes from `frameSpans` — passed in rather than computed here, so
 * this module never has to import `scene.js` and the two can't form a cycle.
 */
export function transitionWindow(frames, spans, transition) {
  const afterId = transition?.after_frame_id;
  if (!afterId) return null;
  const from = frames.findIndex((f) => f.id === afterId);
  if (from < 0 || from >= spans.length) return null;

  const a = spans[from];
  // ⚠ THE NEXT CLIP ON THE SAME TRACK, AND IT HAS TO BUTT UP AGAINST THIS ONE.
  // It used to be `spans[from + 1]` — the next clip in the LIST — which was
  // exact while the picture track was one sequence laid end to end and is wrong
  // twice over now that clips are placed freely on tracks: the next clip in the
  // list may be on another track, and two clips can be neighbours on the
  // timeline without touching. A TRANSITION IS A THING THAT HAPPENS ON A CUT, and
  // there is no edit point in a gap — so no cut, no transition. Inert rather than
  // wrong, exactly like one hanging off the last clip: leave it in the project,
  // and closing the gap brings it back.
  let b = null;
  for (const span of spans) {
    if (span.index === a.index || span.track !== a.track) continue;
    if (span.start !== a.end) continue;
    if (!b || span.index < b.index) b = span;
  }
  if (!b) return null;
  const shorter = Math.min(a.end - a.start, b.end - b.start);
  const durationMs = Math.max(
    MIN_TRANSITION_MS,
    Math.min(
      Math.round(Number(transition.duration_ms) || DEFAULT_TRANSITION_MS),
      MAX_TRANSITION_MS,
      shorter
    )
  );
  const cut = a.end;
  return {
    id: transition.id,
    // Which track this cut is on — `sceneAt` resolves one track at a time and
    // must not put this track's dissolve over another track's picture.
    track: a.track,
    // An unknown kind falls back HERE rather than in each renderer, so the
    // preview and the export can't fall back differently. Same rule as `ease`.
    kind: transitionKind(transition),
    // Resolved here too, and for the same reason: ONE place decides what a
    // half-written transition means, and both renderers read the answer.
    params: transitionParams(transition),
    fromIndex: a.index,
    toIndex: b.index,
    cutMs: cut,
    durationMs,
    startMs: cut - durationMs / 2,
    endMs: cut + durationMs / 2,
  };
}

/** Every placeable transition, in project order — what the timeline draws. */
export function transitionWindows(project, spans) {
  const frames = project.frames || [];
  return (project.transitions || [])
    .map((t) => transitionWindow(frames, spans, t))
    .filter(Boolean);
}

/**
 * The transition covering `tMs`, with how far through it we are, or null.
 *
 * `mix` runs 0 → 1 across the whole window: 0 is "all outgoing picture", 1 is
 * "all incoming". Half-open at both ends like every other visibility test here,
 * so the instant a window ends belongs to whatever comes next.
 *
 * Two transitions written onto the SAME cut is a project that shouldn't exist
 * (the editor replaces rather than appends); the first one wins.
 *
 * `track` narrows it to the cuts on one picture track, which is what `sceneAt`
 * asks: a transition belongs to an edit point, an edit point belongs to a track,
 * and a dissolve on the track above must not be drawn over the one below. Null
 * means "any track" and is what the timeline passes when it is drawing badges.
 */
export function transitionAt(project, tMs, spans, track = null) {
  const frames = project.frames || [];
  const t = Number(tMs) || 0;
  for (const transition of project.transitions || []) {
    const win = transitionWindow(frames, spans, transition);
    if (!win) continue;
    if (track !== null && win.track !== track) continue;
    if (t < win.startMs || t >= win.endMs) continue;
    return { ...win, mix: round6((t - win.startMs) / win.durationMs) };
  }
  return null;
}

// Six places, matching PRECISION in scene.js — mix is compared against the
// Python side to that many digits, and it is part of the render cache key.
function round6(n) {
  return Math.round(n * 1e6) / 1e6;
}
