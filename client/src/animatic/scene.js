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
  // A caption gained `x`/`y` in Phase 5. They are what the in/out presets in
  // `text_presets.js` animate — a title that slides up into place is two keys
  // on `y`, not a new animation system — and they only mean anything when the
  // clip is placed FREE. See `textPlace`.
  text: ["opacity", "x", "y"],
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

/**
 * A caption's own defaults. `y` is 0.85 rather than 0.5 because the thing a
 * caption usually is, is a subtitle — so switching one to free placement puts
 * it where it already was instead of jumping to the middle of the shot.
 * ⚠ These are also the field defaults on `AnimaticTextClip`; the three must
 * agree or a clip saved by the server resolves differently from one that never
 * went through it.
 */
export const TEXT_DEFAULTS = { x: 0.5, y: 0.85, opacity: 1 };

/**
 * HOW a caption is positioned — and the reason `x`/`y` could be added without
 * changing a single existing animatic.
 *
 *   flow — the original behaviour, and the default: the clip is dropped into
 *          its `position` zone (top / middle / bottom) and captions sharing a
 *          zone STACK so two of them never land on top of each other. `x`/`y`
 *          are resolved but unused.
 *   free — the clip's centre is `x`/`y`, as a fraction of the frame, exactly
 *          like a shape or an overlay. Nothing stacks; you placed it.
 *
 * Not animatable, for the same reason a clip's `kind` is not: half way between
 * two layout algorithms is not a picture. An unrecognised value folds down to
 * "flow" HERE so the preview and the export cannot fold differently.
 *
 * ⚠ TWIN of `text_place` in `animatic_render.py`.
 */
export const TEXT_PLACES = ["flow", "free"];

export function textPlace(clip) {
  const place = clip?.place || "flow";
  return TEXT_PLACES.includes(place) ? place : "flow";
}

// Which table a kind's fallbacks come from. A `kind` that isn't listed uses the
// shared one, which is every kind that existed before frames and captions
// needed their own.
const DEFAULTS_BY_KIND = { frame: FRAME_DEFAULTS, text: TEXT_DEFAULTS };

// ---------------------------------------------------------------------------
// The look: effects, mask, blend
// ---------------------------------------------------------------------------
/**
 * ⚠ TWIN of the same block in `animatic_render.py`. The pixel maths is a THIRD
 * file on each side — `gl/shaders/` here, `animatic_effects.py` there — and
 * those two are compared by `tests/effects_parity_check.py` with a TOLERANCE, because
 * WebGL and Pillow will never be byte-identical. This file owns only the
 * question "what values does the grade have at time t", which CAN be identical
 * and is checked exactly by `tests/render_parity.py`.
 *
 * A look belongs to the two clip kinds that are PICTURES — a frame and an
 * overlay. A shape is vector and a caption is text; both are drawn above the
 * finished composite and have no pixels of their own to grade.
 */
export const LOOK_KINDS = ["frame", "overlay"];

/**
 * Each effect's parameters and the value each falls back to.
 *
 * A parameter with a NUMBER for a default is animatable. One with a string is
 * read straight off the clip and never interpolated — exactly as `text`,
 * `color` and `kind` are, because "half way between two LUTs" is not a picture.
 */
export const EFFECT_PARAMS = {
  brightness: { amount: 1 },
  contrast: { amount: 1 },
  saturation: { amount: 1 },
  lut: { name: "", amount: 1 },
  chroma: { color: "#00ff00", similarity: 0.35, smoothness: 0.08, spill: 0 },
};
export const EFFECT_KINDS = Object.keys(EFFECT_PARAMS);

// One region per clip, in FRAME coordinates, `x`/`y` its centre — the same
// convention as a shape, an overlay and a picture's pan, because it is dragged
// with the same handles. "none" is the default: no mask at all, which is every
// animatic that existed before this.
export const MASK_KINDS = ["none", "rect", "ellipse"];
export const DEFAULT_MASK = {
  kind: "none",
  x: 0.5,
  y: 0.5,
  w: 0.5,
  h: 0.5,
  feather: 0.1,
  invert: false,
};
// `kind` and `invert` are not keyframable, for the same reason a clip's `kind`
// is not: a half-inverted mask is not a picture.
export const MASK_ANIMATABLE = ["x", "y", "w", "h", "feather"];

export const BLEND_MODES = [
  "normal",
  "multiply",
  "screen",
  "overlay",
  "add",
  "darken",
  "lighten",
];
export const DEFAULT_BLEND = "normal";

/**
 * How an effect's or a mask's parameter is named as a keyframe track.
 *
 *   "fx:<effect id>:<param>"   e.g. "fx:e3:amount"
 *   "mask:<field>"             e.g. "mask:x"
 *
 * FLAT STRINGS on purpose. `keyframes` stays exactly the dict-of-lists it has
 * always been, so every keyframe operation, every timeline diamond row and
 * every undo entry works on a graded clip without a single change — which is
 * the whole reason the parameters are not nested under the effect they belong
 * to. Keyed by the effect's own id rather than its position, so re-ordering the
 * chain carries each effect's animation along with it.
 */
export const FX_PREFIX = "fx:";
export const MASK_PREFIX = "mask:";

/** The id a keyframe track names this effect by. Mirrors `effect_key`. */
export function effectKey(effect, index) {
  return String(effect?.id || index);
}

/** One effect's parameters with every default filled in. */
export function effectParams(effect) {
  const defaults = EFFECT_PARAMS[effect?.kind] || {};
  const stored = effect?.params || {};
  const out = {};
  for (const [name, fallback] of Object.entries(defaults)) {
    const value = stored[name];
    if (typeof fallback === "string") {
      out[name] = typeof value === "string" ? value : fallback;
    } else {
      const num = Number(value);
      out[name] = Number.isFinite(num) ? num : fallback;
    }
  }
  return out;
}

/**
 * Every keyframable property this clip's LOOK adds, by track name.
 *
 * Dynamic, unlike `ANIMATABLE`, because it depends on which effects the clip is
 * carrying — which is exactly why an effect parameter is addressed by a flat
 * string rather than by a fixed list.
 */
export function lookProps(clip) {
  const props = [];
  (clip?.effects || []).forEach((effect, index) => {
    const kind = effect?.kind;
    if (!EFFECT_KINDS.includes(kind)) return;
    const key = effectKey(effect, index);
    for (const [name, fallback] of Object.entries(EFFECT_PARAMS[kind])) {
      if (typeof fallback !== "string") props.push(`${FX_PREFIX}${key}:${name}`);
    }
  });
  const maskKind = clip?.mask?.kind || "none";
  if (MASK_KINDS.includes(maskKind) && maskKind !== "none") {
    for (const name of MASK_ANIMATABLE) props.push(`${MASK_PREFIX}${name}`);
  }
  return props;
}

/**
 * Read a look track name back apart, or null if it isn't one.
 *
 * ⚠ EDITOR-SIDE ONLY — `animatic_render.py` has no counterpart and needs none.
 * The server RENDERS an animation; it never edits one, so nothing there ever
 * has to go from a track name back to the effect it belongs to. Same split as
 * `keyframes.js`: reading is mirrored in Python, writing is not.
 *
 * The parameter is taken as everything after the SECOND colon so an effect id
 * containing one can still be addressed, rather than silently animating a
 * property called half of it.
 */
export function lookPropParts(prop) {
  if (typeof prop !== "string") return null;
  if (prop.startsWith(MASK_PREFIX)) {
    const param = prop.slice(MASK_PREFIX.length);
    return MASK_ANIMATABLE.includes(param) ? { on: "mask", param } : null;
  }
  if (!prop.startsWith(FX_PREFIX)) return null;
  const rest = prop.slice(FX_PREFIX.length);
  const split = rest.lastIndexOf(":");
  if (split <= 0) return null;
  return { on: "fx", id: rest.slice(0, split), param: rest.slice(split + 1) };
}

/** Which effect on this clip a track name refers to, as [index, effect]. */
function findEffect(clip, id) {
  const list = clip?.effects || [];
  const index = list.findIndex((effect, i) => effectKey(effect, i) === id);
  return index < 0 ? [-1, null] : [index, list[index]];
}

/**
 * What a look property is STORED as, ignoring any animation on it.
 *
 * This is the fallback `enableProp` needs: turning the stopwatch on has to mark
 * the value you can see as the value at this instant, and for an effect
 * parameter that value lives inside the effect rather than on the clip.
 */
export function lookValueOf(clip, prop) {
  const parts = lookPropParts(prop);
  if (!parts) return undefined;
  if (parts.on === "mask") {
    const value = Number(clip?.mask?.[parts.param]);
    return Number.isFinite(value) ? value : DEFAULT_MASK[parts.param];
  }
  const [, effect] = findEffect(clip, parts.id);
  return effect ? effectParams(effect)[parts.param] : undefined;
}

/**
 * A patch writing one look property back where it lives.
 *
 * ⚠ WHY THIS EXISTS AT ALL: a keyframe track is a flat string ("fx:e3:amount"),
 * but the value it animates is nested inside the effect. Writing the resolved
 * value back as a flat key would put `{"fx:e3:amount": 0.8}` on the clip, which
 * `AnimaticFrame` has no field for and Pydantic drops on the next save — so
 * switching the stopwatch OFF would appear to work and then silently lose the
 * value. Every write of a look property goes through here.
 */
export function setLookValue(clip, prop, value) {
  const parts = lookPropParts(prop);
  if (!parts) return {};
  if (parts.on === "mask") {
    return { mask: { ...DEFAULT_MASK, ...(clip?.mask || {}), [parts.param]: value } };
  }
  const [index, effect] = findEffect(clip, parts.id);
  if (index < 0) return {};
  const effects = [...(clip.effects || [])];
  effects[index] = {
    ...effect,
    params: { ...effectParams(effect), ...(effect.params || {}), [parts.param]: value },
  };
  return { effects };
}

/**
 * The clip's effects, mask and blend mode at `tRel`. Mirrors `resolve_look`.
 *
 * Returns all three ALWAYS, even on a clip that carries none of them — an empty
 * chain, a mask of kind "none", "normal". Leaving them off when absent would
 * make the resolved scene a different SHAPE on the two sides (`undefined` here
 * is dropped by JSON; a missing key in Python is not) and `render_parity.py`
 * would be comparing two things it only THINKS are equal.
 *
 * An effect whose kind this build has never heard of is DROPPED here rather
 * than passed on — the same fold-down `clipKind` and `ease` do, in the same
 * place, so the preview and the export skip it together. It stays in the saved
 * project untouched and works again in a build that knows it.
 */
export function resolveLook(clip, tRel) {
  const effects = [];
  (clip?.effects || []).forEach((effect, index) => {
    const kind = effect?.kind;
    if (!EFFECT_KINDS.includes(kind)) return;
    const key = effectKey(effect, index);
    const stored = effectParams(effect);
    const params = {};
    for (const [name, fallback] of Object.entries(EFFECT_PARAMS[kind])) {
      params[name] =
        typeof fallback === "string"
          ? stored[name]
          : valueAt(clip, `${FX_PREFIX}${key}:${name}`, tRel, stored[name]);
    }
    effects.push({ id: key, kind, params });
  });

  const storedMask = clip?.mask || {};
  let kind = storedMask.kind || "none";
  if (!MASK_KINDS.includes(kind)) kind = "none";
  const mask = { kind, invert: Boolean(storedMask.invert) };
  for (const name of MASK_ANIMATABLE) {
    const num = Number(storedMask[name]);
    const base = Number.isFinite(num) ? num : DEFAULT_MASK[name];
    mask[name] =
      kind === "none" ? base : valueAt(clip, `${MASK_PREFIX}${name}`, tRel, base);
  }

  const blend = clip?.blend || DEFAULT_BLEND;
  return {
    effects,
    mask,
    blend: BLEND_MODES.includes(blend) ? blend : DEFAULT_BLEND,
  };
}

// ---------------------------------------------------------------------------
// Clips
// ---------------------------------------------------------------------------
/**
 * A "frame" is a CLIP now, and this is what it can be made of.
 *
 *   image — one still, held. Every animatic written before this is entirely
 *           these, which is why it is the default for a clip that doesn't say.
 *   video — a piece of a video file. `in_ms`/`out_ms` are the SOURCE window and
 *           `speed` how fast it is read through; see `sourceAt`.
 *   color — a flat colour card. No file at all: a slug, a blackout, a hold.
 *
 * ⚠ TWIN of CLIP_KINDS in `animatic_render.py`.
 */
export const CLIP_KINDS = ["image", "video", "color"];
export const DEFAULT_CLIP_COLOR = "#000000";
export const DEFAULT_SPEED = 1;

/**
 * What this clip is made of.
 *
 * An unrecognised kind folds down to "image" HERE rather than in each renderer,
 * so the preview and the export cannot fall back differently — the same rule
 * `ease` and the transition kinds already follow, and what lets a project
 * written by a newer client still open and still play.
 */
export function clipKind(clip) {
  const kind = clip?.kind || "image";
  return CLIP_KINDS.includes(kind) ? kind : "image";
}

/**
 * WHERE A CLIP CAME FROM — "board" | "video" | "image".
 *
 * ⚠ ORIGIN, NOT KIND, and everything about how the picture track is PRESENTED
 * hangs off the difference. The Media pane lists the sequence in three sections
 * and the timeline draws it as two rows, and the question all of them are asking
 * is "where did this come from?", never "what is it now" — because animating a
 * storyboard shot with Veo turns it into a video clip (`attachVeoClip`), and a
 * board shot must not walk out of Storyboard Frames, or off the row its
 * neighbours are on, because it learned to move. So the board reference is tested
 * FIRST and the video test only catches files the user dropped in.
 *
 * A colour card counts as an image: no file, but it is a still you made by hand,
 * and a section of its own for two black slugs would be noise.
 *
 * ⚠ PRESENTATION ONLY. Nothing here changes what is drawn or exported — `frames`
 * is one sequence played in order and stays that way. See `clipKind` for the
 * question the RENDERERS ask.
 */
export function frameOrigin(frame) {
  if (frame?.src?.storyboard_id) return "board";
  if (clipKind(frame) === "video") return "video";
  return "image";
}

/**
 * WHICH MOMENT OF THE SOURCE FILE a video clip is showing, in ms.
 *
 * Null for anything that isn't video — a still and a colour card have no source
 * time, and that is the value both languages report.
 *
 * ⚠ THE ONE DECISION THIS PHASE RESTS ON: `duration_ms` remains the clip's
 * length ON THE TIMELINE, and `speed` widens or narrows the SOURCE WINDOW
 * consumed inside it. Two seconds of timeline at speed 2 shows four seconds of
 * footage; the clip does not get shorter, and NOTHING ELSE ON THE TIMELINE
 * MOVES when the speed changes.
 *
 * The alternative — speed re-timing the clip — would shift every later cut,
 * every caption timed against one and every transition anchored to one, which is
 * the same class of problem boundary-local transitions were designed to avoid.
 * `frameSpans` is built on `duration_ms` and stays untouched.
 *
 * Past `out_ms` the clip HOLDS its last source frame rather than running on into
 * footage the user trimmed off — the same rule keyframes follow outside their
 * first and last key, and what stops a clip stretched longer than its source
 * going black.
 */
export function sourceAt(clip, tRel) {
  if (clipKind(clip) !== "video") return null;
  const inMs = Math.max(0, Number(clip.in_ms) || 0);
  let speed = Number(clip.speed);
  if (!Number.isFinite(speed) || speed <= 0) speed = DEFAULT_SPEED;
  let at = inMs + Math.max(0, Number(tRel) || 0) * speed;
  const out = clip.out_ms;
  if (out !== null && out !== undefined) {
    // `out_ms` is EXCLUSIVE, like every other end in this project, so the last
    // moment actually shown is one millisecond inside it.
    at = Math.min(at, Math.max(inMs, Number(out) - 1));
  }
  return round(at);
}

/** The value a property falls back to, by the kind of clip carrying it. */
export function defaultFor(kind, prop) {
  return (DEFAULTS_BY_KIND[kind] || DEFAULTS)[prop];
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
  const defaults = DEFAULTS_BY_KIND[kind] || DEFAULTS;
  for (const prop of ANIMATABLE[kind] || []) {
    out[prop] = valueAt(clip, prop, tRel, defaults[prop]);
  }
  if (LOOK_KINDS.includes(kind)) Object.assign(out, resolveLook(clip, tRel));
  // ⚠ Set EXPLICITLY, for the same reason `kind` and `color` are on a picture:
  // a caption that never chose a placement would be `undefined` here (which
  // JSON drops) and a missing key on the Python side, so `render_parity.py`
  // would be comparing two different shapes and passing for the wrong reason.
  if (kind === "text") out.place = textPlace(clip);
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
  const clip = frames[index];
  const span = spans[index];
  const tRel = t - span.start;
  return {
    ...resolve(clip, "frame", tRel),
    index,
    start_ms: span.start,
    end_ms: span.end,
    // ⚠ Set EXPLICITLY, never left to ride along on the clip. A clip carrying
    // none of these would be `undefined` here (which JSON drops) and absent on
    // the Python side — so the parity test would be comparing two different
    // shapes and passing for the wrong reason.
    kind: clipKind(clip),
    color: clip.color || DEFAULT_CLIP_COLOR,
    // Which moment of the source file is on screen. Null for a still or a
    // colour card, and the single most important number this phase added — it
    // is what makes a video clip a moving picture rather than one still
    // stretched over its whole hold.
    source_ms: sourceAt(clip, tRel),
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
  // A VIDEO CLIP is continuous for exactly the same reason, and this is the one
  // that would be most expensive to get wrong: `plan_segments` renders one still
  // per stretch where the picture holds, so a video planned that way would
  // export as a single FROZEN frame held for the whole clip while the preview
  // played it. `speed` is checked too, per the phase's own rule, even though it
  // only means anything on a video.
  for (const clip of project.frames || []) {
    if (clipKind(clip) === "video") return true;
    const speed = Number(clip.speed);
    if (Number.isFinite(speed) && Math.abs(speed - DEFAULT_SPEED) > 1e-9) return true;
  }
  const groups = [
    ["frame", project.frames],
    ["text", project.texts],
    ["shape", project.shapes],
    ["overlay", project.overlays],
  ];
  for (const [kind, clips] of groups) {
    for (const clip of clips || []) {
      // ⚠ `lookProps` is why this is not just `ANIMATABLE`. A grade that RAMPS
      // — a mask sweeping across the shot, a LUT dialling in — is continuous in
      // exactly the way a Ken Burns push is, so it has to force the sampling
      // planner too. Miss it and `plan_segments` renders one still for the
      // whole stretch: the MP4 shows the grade frozen at its first value while
      // the monitor animates it.
      const props = LOOK_KINDS.includes(kind)
        ? [...(ANIMATABLE[kind] || []), ...lookProps(clip)]
        : ANIMATABLE[kind] || [];
      for (const prop of props) {
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
  // ⚠ A VIDEO CLIP'S `source_ms` MUST BE IN THE KEY, for precisely the reason
  // `mix` must be: without it every sampled moment of a clip resolves to one
  // signature, the exporter renders a single still and reuses it for the whole
  // clip, and the video plays as a FREEZE FRAME. Appended only when the clip
  // isn't a plain image, so a project made of stills signs byte-for-byte what it
  // signed before clips existed.
  const clipExtra = (picture) => {
    const kind = picture.kind || "image";
    if (kind === "image") return "";
    return `:k${kind}:${n(picture.source_ms)}:${picture.color || ""}`;
  };
  // ⚠ A LOOK THAT MOVES MUST BE IN THE KEY, for the third time and the same
  // reason as `mix` and `source_ms`: two samples of a mask sweeping across a
  // held picture resolve to the same clip at the same transform and differ ONLY
  // here, so leaving it out would render one still, reuse it for the whole
  // sweep, and freeze the grade while the monitor animated it.
  //
  // Appended only when there IS something to say — an empty chain, an unset
  // mask and "normal" contribute nothing — so a project that predates effects
  // signs byte-for-byte what it signed before.
  const lookExtra = (picture) => {
    const bits = [];
    for (const effect of picture.effects || []) {
      const params = effect.params || {};
      const values = Object.keys(params)
        .sort()
        .map((key) => {
          const value = params[key];
          return `${key}=${typeof value === "string" ? value : n(value)}`;
        })
        .join(",");
      bits.push(`${effect.kind}[${values}]`);
    }
    const mask = picture.mask || {};
    if ((mask.kind || "none") !== "none") {
      bits.push(
        `m${mask.kind}:${n(mask.x)}:${n(mask.y)}:${n(mask.w)}:${n(mask.h)}` +
          `:${n(mask.feather)}:${mask.invert ? 1 : 0}`
      );
    }
    const blend = picture.blend || DEFAULT_BLEND;
    if (blend !== DEFAULT_BLEND) bits.push(`b${blend}`);
    return bits.length ? `:L${bits.join("+")}` : "";
  };
  const parts = [];
  if (scene.frame) {
    const f = scene.frame;
    parts.push(
      `f${f.index}:${n(f.scale)}:${n(f.x)}:${n(f.y)}:${n(f.opacity)}` +
        clipExtra(f) +
        lookExtra(f)
    );
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
      `x${scene.transition}:${n(scene.mix)}:b${b.index}:${n(b.scale)}:${n(b.x)}:${n(b.y)}:${n(b.opacity)}` +
        clipExtra(b) +
        lookExtra(b)
    );
  }
  for (const s of scene.shapes) {
    parts.push(
      `s${s.id}:${n(s.x)}:${n(s.y)}:${n(s.w)}:${n(s.h)}:${n(s.opacity)}:${n(s.rotation)}`
    );
  }
  for (const o of scene.overlays) {
    parts.push(
      `o${o.id}:${n(o.x)}:${n(o.y)}:${n(o.w)}:${n(o.h)}:${n(o.opacity)}:${n(o.rotation)}` +
        lookExtra(o)
    );
  }
  // ⚠ A CAPTION THAT MOVES MUST BE IN THE KEY, for the fourth time and the same
  // reason as `mix`, `source_ms` and the look: a title sliding up the frame
  // resolves to the same clip at the same opacity and differs ONLY in `y`, so
  // leaving it out would render one still, reuse it for the whole slide, and
  // the caption would sit dead still in the MP4 while the monitor moved it.
  //
  // Appended only in FREE placement, where x/y are the values actually drawn.
  // In flow placement they are resolved but unused, so a project of ordinary
  // stacked subtitles signs byte-for-byte what it signed before this existed.
  for (const c of scene.texts) {
    const extra = c.place === "free" ? `:${n(c.x)}:${n(c.y)}` : "";
    parts.push(`t${c.id}:${n(c.opacity)}${extra}`);
  }
  return parts.join("|");
}
