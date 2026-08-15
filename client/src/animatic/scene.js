/**
 * The animatic SCENE MODEL — what the frame looks like at one moment in time.
 *
 * This is the single answer to "what is on screen at t?", and it is deliberately
 * a pure function over the project: no React, no DOM, no urls, no canvas. The
 * Program monitor renders what it returns, and `animatic_render.py` mirrors it
 * value for value so the exported MP4 shows the same picture.
 *
 * ⚠ THIS FILE HAS A TWIN: `animatic_render.py` (`scene_at`). They must agree, or
 * the preview lies about the export. That is not a hypothetical — the shape
 * polygons already live in two files (`_SHAPE_POINTS` / `POINTS`) and are kept
 * in step by hand. Here the pair is checked instead: `tests/render_parity.py`
 * evaluates a fixture through BOTH and fails on any difference. Change one side,
 * run that test.
 *
 * Why a scene model at all: until now the exporter cut the timeline into
 * stretches where nothing changes and rendered ONE still per stretch, while the
 * preview picked visible clips out of three separate `useMemo`s. Both worked
 * only because nothing moved. A keyframed property moves, so both sides now ask
 * the same evaluator for resolved values at a given time.
 */

import { transitionAt } from "./transitions.js";

// Rounding is part of the contract. Two languages doing the same float maths
// drift in the last bits; the parity test would then fail on noise rather than
// on a real disagreement. Six places is far finer than a pixel at 4K.
const PRECISION = 6;

function round(n) {
  const f = 10 ** PRECISION;
  return Math.round(n * f) / f;
}

// ---------------------------------------------------------------------------
// Easing
// ---------------------------------------------------------------------------
// Deliberately a short, closed list. Every one of these is trivial to express
// identically in Python; a bezier editor can be added later as a fifth kind
// carrying its own control points, without disturbing these.
export const EASINGS = ["linear", "hold", "ease-in", "ease-out", "ease-in-out"];

export function ease(kind, u) {
  switch (kind) {
    case "hold":
      // A step: the value does not move until the NEXT keyframe is reached.
      return 0;
    case "ease-in":
      return u * u * u;
    case "ease-out":
      return 1 - (1 - u) ** 3;
    case "ease-in-out":
      return u < 0.5 ? 4 * u * u * u : 1 - (-2 * u + 2) ** 3 / 2;
    default:
      return u;
  }
}

/**
 * Which properties each kind of clip can animate.
 *
 * A property NOT in this list is read straight off the clip and never
 * interpolated, which is how `color`, `text` and `kind` stay themselves. Adding
 * a property here is all it takes to make it keyframable — as long as the twin
 * in `animatic_render.py` gains it too.
 */
export const ANIMATABLE = {
  frame: ["scale", "x", "y", "opacity"],
  shape: ["x", "y", "w", "h", "opacity", "rotation"],
  overlay: ["x", "y", "w", "h", "opacity", "rotation"],
  text: ["opacity"],
};

// The value a property falls back to when the clip doesn't carry it. A frame's
// transform defaults to identity, so a project with no keyframes renders exactly
// as it did before any of this existed.
export const DEFAULTS = {
  scale: 1,
  x: 0.5,
  y: 0.5,
  w: 0.25,
  h: 0.25,
  opacity: 1,
  rotation: 0,
};

// A frame's pan/zoom is expressed around the CENTRE of the picture, like every
// other geometry in this project, so `x`/`y` of 0.5 means "centred".
export const FRAME_DEFAULTS = { scale: 1, x: 0.5, y: 0.5, opacity: 1 };

/** The value a property falls back to, by the kind of clip carrying it. */
export function defaultFor(kind, prop) {
  return (kind === "frame" ? FRAME_DEFAULTS : DEFAULTS)[prop];
}

/**
 * Resolve one property of one clip at `tRel` ms into that clip.
 *
 * Keyframe times are stored RELATIVE to the clip's own start. That is what lets
 * a clip be dragged along the timeline without its animation sliding out from
 * under it — the alternative (absolute times) has to be rewritten on every move
 * and is wrong the moment a drag is interrupted.
 */
export function valueAt(clip, prop, tRel, fallback) {
  const base = clip[prop] ?? fallback;
  const track = clip.keyframes?.[prop];
  if (!Array.isArray(track) || track.length === 0) return base;

  const keys = [...track].sort((a, b) => (a.t ?? 0) - (b.t ?? 0));
  if (keys.length === 1) return round(Number(keys[0].v ?? base));

  const first = keys[0];
  const last = keys[keys.length - 1];
  // Before the first key and after the last, the value HOLDS. Extrapolating
  // would send a clip flying off screen the moment it is trimmed longer.
  if (tRel <= (first.t ?? 0)) return round(Number(first.v ?? base));
  if (tRel >= (last.t ?? 0)) return round(Number(last.v ?? base));

  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    const at = a.t ?? 0;
    const bt = b.t ?? 0;
    if (tRel < at || tRel >= bt) continue;
    const span = bt - at;
    if (span <= 0) return round(Number(b.v ?? base));
    const u = (tRel - at) / span;
    const av = Number(a.v ?? base);
    const bv = Number(b.v ?? base);
    return round(av + (bv - av) * ease(a.ease || "linear", u));
  }
  return round(Number(last.v ?? base));
}

function resolve(clip, kind, tRel) {
  const out = { ...clip };
  const defaults = kind === "frame" ? FRAME_DEFAULTS : DEFAULTS;
  for (const prop of ANIMATABLE[kind] || []) {
    out[prop] = valueAt(clip, prop, tRel, defaults[prop]);
  }
  return out;
}

// ---------------------------------------------------------------------------
// The timeline
// ---------------------------------------------------------------------------
/**
 * Where each frame sits, in play order. One hold per frame, back to back.
 *
 * `endMs` extends the LAST picture — that is what makes an export cover a music
 * bed which outlasts the pictures instead of stopping dead on the final image.
 * `plan_segments` has always done this; it lives here now so the preview holds
 * the last frame over a long audio track too, which it previously did not.
 */
export function frameSpans(frames, endMs = null) {
  const spans = [];
  let clock = 0;
  for (let i = 0; i < frames.length; i++) {
    const length = Math.max(100, Number(frames[i].duration_ms) || 2000);
    spans.push({ start: clock, end: clock + length, index: i });
    clock += length;
  }
  if (spans.length && endMs && endMs > clock) {
    spans[spans.length - 1].end = endMs;
    clock = endMs;
  }
  return { spans, totalMs: clock };
}

// A clip is on screen from its start UP TO BUT NOT INCLUDING its end. The same
// half-open rule decides which frame is showing, so a cut lands on exactly one
// picture and never on two.
function alive(clip, t) {
  const start = Math.max(0, Number(clip.start_ms) || 0);
  const end = start + Math.max(100, Number(clip.duration_ms) || 0);
  return t >= start && t < end;
}

/** One frame, resolved at `t` and stamped with where it sits in the sequence. */
function pictureAt(frames, spans, index, t) {
  const span = spans[index];
  return {
    ...resolve(frames[index], "frame", t - span.start),
    index,
    start_ms: span.start,
    end_ms: span.end,
  };
}

/**
 * What the viewer sees at `tMs`.
 *
 * `project` is the saved shape: { frames, texts, shapes, overlays, transitions,
 * settings }. Returns resolved clips — every animatable property already
 * interpolated — in the order they are composited, bottom to top:
 *
 *     picture (× 2 during a transition) → shapes → overlay pictures → text
 *
 * A shape sits under the overlays because a shape is usually a highlight ON the
 * art, while an overlay is a picture element that belongs above it; text is last
 * so a caption is always readable. `render_frame` stacks them the same way.
 *
 * ON A TRANSITION there are two pictures: `frame` is the OUTGOING one for the
 * whole window (see the note in `transitions.js` for why that, and not the one
 * `frameSpans` would pick, for the half past the cut), `frame_b` is the picture
 * arriving, and `mix` says how far through we are. Off a transition `frame_b` is
 * null, `mix` is 0 and `transition` is null — which is every animatic that
 * existed before this, resolving exactly as it always did.
 */
export function sceneAt(project, tMs, endMs = null) {
  const frames = project.frames || [];
  const { spans, totalMs } = frameSpans(frames, endMs);
  const t = Math.max(0, Number(tMs) || 0);

  const span = spans.find((s) => t >= s.start && t < s.end) || null;
  let frame = span == null ? null : pictureAt(frames, spans, span.index, t);

  // A transition overrides which picture is "the frame", because for the half
  // of the window past the cut the answer is the one that is on its way OUT.
  // Both pictures are resolved outside their own span here — keys hold at the
  // ends rather than extrapolating, so neither flies off screen.
  const active = frame == null ? null : transitionAt(project, t, spans);
  let frameB = null;
  if (active) {
    frame = pictureAt(frames, spans, active.fromIndex, t);
    frameB = pictureAt(frames, spans, active.toIndex, t);
  }

  // An empty caption is skipped HERE, which means the preview and the exporter
  // skip it for the same reason in the same place — an unfinished caption never
  // burns a blank bar into the video.
  const texts = (project.texts || [])
    .filter((c) => (c.text || "").trim() && alive(c, t))
    .map((c) => resolve(c, "text", t - (c.start_ms || 0)))
    .filter((c) => c.opacity > 0);

  // Opacity is tested AFTER resolving, not before: a shape keyframed from 0 to 1
  // is invisible at its first frame and visible later, and dropping it up front
  // (which is what the exporter used to do) would delete the whole fade.
  const shapes = (project.shapes || [])
    .filter((s) => alive(s, t))
    .map((s) => resolve(s, "shape", t - (s.start_ms || 0)))
    .filter((s) => s.opacity > 0);

  const overlays = (project.overlays || [])
    .filter((o) => alive(o, t))
    .map((o) => resolve(o, "overlay", t - (o.start_ms || 0)))
    .filter((o) => o.opacity > 0);

  return {
    t_ms: t,
    total_ms: totalMs,
    frame,
    // The transition, flattened onto the scene: the second picture, how far
    // through the blend we are, and which blend. Null / 0 / null off a cut.
    frame_b: frameB,
    mix: active ? active.mix : 0,
    transition: active ? active.kind : null,
    shapes,
    overlays,
    texts,
  };
}

/**
 * Does anything in this project MOVE?
 *
 * This is the question that decides how the export is encoded. No → the timeline
 * can be cut into stretches where nothing changes and rendered as one still per
 * stretch, which is what has always happened and is very fast. Yes → the frame
 * has to be drawn at every video frame. Getting this wrong in the "no" direction
 * would silently drop every animation from the MP4, so it errs toward true:
 * ANY keyframe track with more than one key counts, even one whose keys happen
 * to share a value.
 */
export function isAnimated(project) {
  // A transition is continuous by definition — every video frame of the blend
  // is a different picture. One anywhere in the project forces the sampling
  // planner, and this is checked FIRST because it is the cheapest answer.
  if ((project.transitions || []).length > 0) return true;
  const groups = [
    ["frame", project.frames],
    ["text", project.texts],
    ["shape", project.shapes],
    ["overlay", project.overlays],
  ];
  for (const [kind, clips] of groups) {
    for (const clip of clips || []) {
      for (const prop of ANIMATABLE[kind] || []) {
        const track = clip.keyframes?.[prop];
        if (Array.isArray(track) && track.length > 1) return true;
      }
    }
  }
  return false;
}

/**
 * A stable identity for a rendered frame, used as the exporter's cache key.
 *
 * Two moments with the same signature are the same picture, so the still is
 * rendered once and reused. With no keyframes anywhere this collapses to the
 * ids of what's visible — exactly the key `build_animatic` already used — so a
 * project that doesn't move costs no more to export than it did before.
 */
export function sceneSignature(scene) {
  // ⚠ Numbers are formatted EXPLICITLY, never interpolated raw. JS has one
  // number type and prints 1.0 as "1" where Python prints "1.0" — signatures
  // that were meant to be identical then weren't, which the parity test caught
  // on its first run. A fixed 6-place format is the same string in both.
  const n = (v) => Number(v ?? 0).toFixed(PRECISION);
  const parts = [];
  if (scene.frame) {
    const f = scene.frame;
    parts.push(`f${f.index}:${n(f.scale)}:${n(f.x)}:${n(f.y)}:${n(f.opacity)}`);
  } else {
    parts.push("f-");
  }
  // ⚠ `mix` MUST be in the key. Without it every video frame of a dissolve
  // resolves to one signature, the exporter renders a single still and reuses
  // it for the whole blend, and the transition SNAPS instead of blending —
  // exactly the reuse bug this key already guards against for keyframes.
  // Added only when there IS a second picture, so a project with no transitions
  // produces byte-for-byte the signature it produced before they existed.
  if (scene.frame_b) {
    const b = scene.frame_b;
    parts.push(
      `x${scene.transition}:${n(scene.mix)}:b${b.index}:${n(b.scale)}:${n(b.x)}:${n(b.y)}:${n(b.opacity)}`
    );
  }
  for (const s of scene.shapes) {
    parts.push(
      `s${s.id}:${n(s.x)}:${n(s.y)}:${n(s.w)}:${n(s.h)}:${n(s.opacity)}:${n(s.rotation)}`
    );
  }
  for (const o of scene.overlays) {
    parts.push(
      `o${o.id}:${n(o.x)}:${n(o.y)}:${n(o.w)}:${n(o.h)}:${n(o.opacity)}:${n(o.rotation)}`
    );
  }
  for (const c of scene.texts) {
    parts.push(`t${c.id}:${n(c.opacity)}`);
  }
  return parts.join("|");
}
