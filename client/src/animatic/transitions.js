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

export const TRANSITIONS = [
  { id: "dissolve", label: "Dissolve", note: "Cross-fade" },
  { id: "dip", label: "Dip to black", note: "Out through the bar colour" },
  { id: "wipe", label: "Wipe", note: "An edge travels across" },
  { id: "slide", label: "Slide", note: "The next shot pushes in" },
];

export const TRANSITION_KINDS = TRANSITIONS.map((t) => t.id);

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
  if (from < 0 || from + 1 >= spans.length) return null;

  const a = spans[from];
  const b = spans[from + 1];
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
    // An unknown kind falls back HERE rather than in each renderer, so the
    // preview and the export can't fall back differently. Same rule as `ease`.
    kind: TRANSITION_KINDS.includes(transition.kind) ? transition.kind : "dissolve",
    fromIndex: from,
    toIndex: from + 1,
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
 */
export function transitionAt(project, tMs, spans) {
  const frames = project.frames || [];
  const t = Number(tMs) || 0;
  for (const transition of project.transitions || []) {
    const win = transitionWindow(frames, spans, transition);
    if (!win) continue;
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
