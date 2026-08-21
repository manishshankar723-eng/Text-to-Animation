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

import { TRANSITION_PARAMS, transitionAt } from "./transitions.js";

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
  // ⚠ APPENDED, NEVER INSERTED. An effect reaches the shader as its INDEX in
  // this table (`fxIndex`), so putting a new kind in the middle would silently
  // re-number every kind after it and a saved project would come back graded by
  // the wrong effect. New ones go on the end.
  //
  // These six are all POINT-WISE: every one is a function of a single pixel and
  // nothing else. That is not a coincidence, it is the admission price — the
  // monitor grades in one fragment shader pass with no neighbourhood available,
  // so blur, sharpen and grain cannot join this list without a second pass and
  // an answer to "at which resolution", which the export and the preview do not
  // share. They stay out until that is settled.
  exposure: { stops: 0 },
  gamma: { gamma: 1 },
  temperature: { temperature: 0, tint: 0 },
  hue: { degrees: 0 },
  sepia: { amount: 1 },
  posterize: { levels: 8 },
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
 * THE THREE KINDS OF ROW A CLIP CAN LIVE ON, in compositing order bottom-first.
 *
 * ⚠ THIS IS THE `kind` ON A PICTURE ROW'S `AnimaticLayer` RECORD, and the reason
 * there are three rather than one is that the user asked for the storyboard to
 * keep its own rows: "i want you add Storybord Layer seprately … and user then
 * next user want genearte shortyborad image to video footage from VEO 3 model in
 * editor then video genarte and come in Storyboad video layer Sepratlty".
 *
 * ⚠ THERE IS NO `stills` ROW ANY MORE. An uploaded picture is an OVERLAY on the
 * "Images" lane now, not a full-frame card in the cut — asked for directly:
 * "remove still layer … when user uplaod media or layer so image shoul come in
 * image layer not sitll layer". A Stills row sat ABOVE the storyboard rows, so
 * dropping one photo in blanked out the first seconds of the board; the Images
 * lane composites it over the cut instead, which is what "i see this good" was
 * about. `belongsOnImageLane` is the one place that decision is written down.
 *
 * ⚠ THE ORDER MATTERS — it is the order a freshly built stack is laid out in, so
 * a Veo render draws OVER the panel it was made from (which is what lets 👁 on
 * the render row show the board again underneath) and footage you dropped in
 * draws over both.
 */
export const ROW_KINDS = ["board_image", "board_video", "video"];

/** Is this a row that holds clips from `frames` — as opposed to text/shape/audio? */
export const isCutRow = (kind) => ROW_KINDS.includes(kind);

/**
 * A ROW KIND OFF AN OLD PROJECT, READ AS ONE OF THE THREE THAT ARE LEFT.
 *
 * ⚠ THE ONLY MIGRATION `stills` NEEDS, and it is deliberately a read-time one
 * rather than a rewrite: an animatic saved while Stills rows existed still has
 * `kind: "stills"` layer records and still has its photos in the cut, and both
 * must go on playing and exporting EXACTLY as they did. A plain video row is
 * what those clips already sit on as far as the exporter is concerned (it reads
 * `track`, a number, and nothing else), so reading the record as one changes the
 * label in the gutter and nothing else at all.
 */
export const rowKindOrLegacy = (kind) =>
  isCutRow(kind) ? kind : kind === "stills" ? "video" : "";

/**
 * WHICH KINDS OF FILE EACH ROW ACCEPTS, in `kindOf` / `laneTakes` words.
 *
 * ⚠ IT LIVES HERE, BESIDE THE KINDS, because two very separate places ask it:
 * the timeline decides whether to light a row up as a drop target, and the editor
 * decides what the file dialog offers and what a drop is allowed to do. Written
 * out twice they would drift, and the drift would read as "the row accepted my
 * file and then refused it".
 *
 * ⚠ THE TWO BOARD ROWS TAKE NOTHING, and that is deliberate rather than an
 * omission. A storyboard row is filled by the import and a Veo row by ✨ Animate;
 * an uploaded file on either is the mixing the strict rows exist to stop.
 *
 * ⚠ THE VIDEO ROW TAKES BOTH, AND IT IS THE ONLY ROW THAT TAKES A PICTURE — it
 * has always held footage and full-frame stills alike, which is exactly why the
 * ＋ Add layer menu offers no "Stills" beside it. What went with the Stills row
 * is the row that got CREATED for you behind your back: an upload with no row
 * named goes to the overlay "Images" lane now (`belongsOnImageLane`), and a
 * picture only enters the cut when you aim it at this row yourself.
 */
export const ROW_TAKES = {
  board_image: [],
  board_video: [],
  video: ["video", "image"],
};

/**
 * WHICH KIND OF ROW THIS CLIP BELONGS ON — the strict-rows rule, in one place.
 *
 * ⚠ IT IS DERIVED, NOT STORED, and that is deliberate: every part of the answer
 * is already on the clip. A board reference (`src.storyboard_id`) says the clip
 * came from a storyboard and `clipKind` says whether it is footage yet, so the
 * three rows fall out of two questions and there is no fourth field that can
 * disagree with them. It also means `attachVeoClip` moving a render onto its own
 * row needs no migration: an animated panel KEEPS its `storyboard_id` (see the
 * note in `attachVeoClip`), so it reads as `board_video` the moment it is video.
 *
 * ⚠ A COLOUR CARD IS A STILL. No file behind it, but it is full-frame, it takes
 * up time, and a row of its own for two black slugs would be noise.
 *
 * ⚠ PRESENTATION AND PERMISSION ONLY — nothing here changes what is drawn or
 * exported. The export reads a clip's `track` NUMBER and nothing else, so a clip
 * sitting on a row of the wrong kind (which every project saved before these
 * rows existed may have) still plays exactly as it did. Strictness governs what
 * you can DO next; it is not a rule that rejects work already done.
 */
export function clipRowKind(frame) {
  return cardRowKind(clipKind(frame), !!frame?.src?.storyboard_id);
}

/**
 * THE SAME QUESTION, ASKED WITH THE TWO THINGS A DRAG CAN ANSWER MID-FLIGHT.
 *
 * ⚠ IT EXISTS BECAUSE A MEDIA-LIBRARY CARD IS NOT A FILE, and `ROW_TAKES` only
 * ever knew about files. Both board rows take NO file (that is the point of them
 * — they are filled by the import and by ✨ Animate), and applying that same
 * table to a library card said "a Veo render may not go on the Storyboard video
 * row", which is absurd: the card came OFF that row. Reported as "i delete veo
 * video clip in timeline … then i select Veo video clip and drang and drop on
 * same storyboard video layer but i can't drop in Storyboad layer but i drop in
 * Video layer".
 *
 * ⚠ AND IT IS THE BODY `clipRowKind` NOW DELEGATES TO, so the row a card lands on
 * and the row the clip made from it belongs on are one derivation. Two copies is
 * how a drop lands somewhere the very next drag refuses to move it away from.
 *
 * @param kind      a clip/asset kind — "image" | "video" | "color"
 * @param fromBoard did this come out of a storyboard? (`src.storyboard_id` on a
 *                  clip or a card; the `application/x-anim-board` marker on a
 *                  drag, which is all a lane can read during `dragover`)
 */
export function cardRowKind(kind, fromBoard) {
  const video = clipKind({ kind }) === "video";
  if (fromBoard) return video ? "board_video" : "board_image";
  // ⚠ A PLAIN PICTURE ANSWERS "video" NOW, and that is not a mistake: this
  // question is "which row in the CUT does this clip sit on", and since the
  // Stills row went there is only one row left that is neither the board's nor
  // the renders'. It is asked of clips that are ALREADY in the cut — every photo
  // of every animatic saved before this change — and answering with a row kind
  // that no longer exists would strand them: unnameable by `dominantRowKind` and
  // unmovable by `laneMoveTarget`, which compares this against the row's kind.
  // Where a NEW picture goes is a different question, and `belongsOnImageLane`
  // is the one that answers it.
  return "video";
}

/**
 * DOES A PICTURE YOU ARE ADDING BELONG ON THE OVERLAY "Images" LANE?
 *
 * ⚠ THE ROUTING RULE FOR EVERY WAY IN — the Media pane's ＋ and its drop card, a
 * card's own ＋, a double-click on a card — so the four cannot come to disagree
 * about where an upload lands. Asked for as "when user uplaod media or layer so
 * image shoul come in image layer not sitll layer".
 *
 * ⚠ A BOARD PANEL IS NOT ONE, however much of a picture it is: it belongs to the
 * storyboard rows, which is the whole point of them being separate. Nor is a
 * COLOUR CARD — it has no file, it is full-frame, and it takes up time in the
 * cut, which is the opposite of what an overlay does.
 *
 * @param kind      a clip/asset kind — "image" | "video" | "color"
 * @param fromBoard did this come out of a storyboard?
 */
export const belongsOnImageLane = (kind, fromBoard) =>
  !fromBoard && (kind || "image") === "image";

/** Is this one of the two rows the storyboard owns? */
export const isBoardRow = (rowKind) =>
  rowKind === "board_image" || rowKind === "board_video";

/**
 * IS THIS A VEO RENDER? — asked of a CLIP or of a MEDIA-LIBRARY CARD alike.
 *
 * ⚠ IT IS `cardRowKind` AND NOT A NEW FIELD, and that is the whole point. A
 * paid render is already identified in this codebase exactly once, by the two
 * facts on the clip itself: it came out of a storyboard (`src.storyboard_id`,
 * kept underneath the video source — see `attachVeoClip`) and it is footage now.
 * That pair is `board_video`, which is what draws these bars pastel purple
 * (`.tl-bar.is-veo`) and what pins them to the Storyboard video row. A second
 * definition — a `from_veo` flag, or a lookup into the server's `veo_clips` —
 * would be a second opinion that can disagree with the colour on screen, and it
 * would need a migration for every project rendered before the day it was added.
 * This needs none: it is derived from what is already saved.
 *
 * ⚠ AND IT CANNOT CATCH AN UPLOAD BY MISTAKE. `ROW_TAKES` gives both board rows
 * an empty list, so no dropped file ever acquires a `storyboard_id`; the only
 * things that carry one are the import (stills) and ✨ Animate (footage).
 *
 * @param item a picture clip from `frames`, or an asset from the Media library.
 *             Both carry `kind` and `src`, which is all this asks for.
 */
export function isVeoRender(item) {
  if (!item) return false;
  return cardRowKind(item.kind || "image", !!item.src?.storyboard_id) === "board_video";
}

/**
 * The kind of row a set of clips MOSTLY belongs on, for naming a row that no
 * record names — every row of every animatic saved before the records existed.
 *
 * ⚠ "MOSTLY", because a legacy row is allowed to hold a mix: it was built when a
 * picture row took anything. The row is called after what is actually on it,
 * which is the honest label and the one the ▶⇧ split button then acts on. Ties
 * go to `ROW_KINDS` order, so an empty row is a plain video row — what a new
 * animatic opens with.
 */
export function dominantRowKind(clips) {
  const tally = new Map();
  for (const clip of clips || []) {
    const kind = clipRowKind(clip);
    tally.set(kind, (tally.get(kind) || 0) + 1);
  }
  let best = "video";
  let bestN = 0;
  for (const kind of ROW_KINDS) {
    const n = tally.get(kind) || 0;
    if (n > bestN) {
      bestN = n;
      best = kind;
    }
  }
  return best;
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
 * WHICH PICTURE TRACK a clip is on. 0 is the base track — the bottom of the
 * stack, and where every clip written before tracks existed lives.
 *
 * ⚠ A HIGHER NUMBER IS DRAWN OVER A LOWER ONE, which is what a track above
 * another one means in every editor. A gap on an upper track therefore shows
 * whatever is on the track below it, and a moment with nothing on any track
 * shows the letterbox colour — see `sceneAt`.
 *
 * ⚠ TWIN of `frame_track` in `animatic_render.py`.
 */
export function frameTrack(frame) {
  const n = Math.trunc(Number(frame?.track) || 0);
  return n > 0 ? n : 0;
}

/**
 * Where each frame sits — ONE SPAN PER FRAME, IN LIST ORDER.
 *
 * ⚠ A PICTURE IS PLACED BY `start_ms` ON ITS OWN TRACK, not by adding up the
 * clips before it. That is the whole of the multi-track change. The picture rows
 * used to be one sequence laid end to end, so trimming any clip moved every clip
 * after it — including the ones on the row above, which is what made "when i do
 * video trim so i see my image layer conetnt move" true by construction
 * (user-reported). With a start of its own a clip moves when you move it and at
 * no other time.
 *
 * ⚠ A MISSING `start_ms` MEANS "AFTER THE LAST CLIP ON MY TRACK", and that is the
 * compatibility hinge. Every animatic written before this carries no starts at
 * all and sits on one track, so this rule lays it out exactly as the old running
 * total did — the same number at every cut. It is also what makes "add these
 * pictures to the end of the sequence" a write of nothing rather than arithmetic.
 *
 * ⚠ THE SPANS STAY PARALLEL TO `frames`: `spans[i]` is the i-th frame's and
 * carries `index` too. `transitionWindow` and `server/animatics.py` both index it
 * that way, so sorting it into play order here would break them.
 *
 * `endMs` HOLDS THE LAST PICTURE out to that moment — what makes an export cover
 * a music bed which outlasts the pictures instead of stopping dead on the final
 * image. With several tracks "the last picture" is the one that ENDS LAST, and
 * the topmost of those if two tie: that is the picture you can see.
 */
export function frameSpans(frames, endMs = null) {
  const list = frames || [];
  const spans = [];
  // Where the next clip with no start of its own goes, per track.
  const clock = new Map();
  for (let i = 0; i < list.length; i++) {
    const frame = list[i];
    const track = frameTrack(frame);
    const length = Math.max(100, Number(frame.duration_ms) || 2000);
    const at = Number(frame.start_ms);
    const start =
      frame.start_ms === null || frame.start_ms === undefined || !Number.isFinite(at)
        ? clock.get(track) || 0
        : Math.max(0, Math.round(at));
    spans.push({ start, end: start + length, index: i, track });
    clock.set(track, Math.max(clock.get(track) || 0, start + length));
  }
  let totalMs = 0;
  for (const span of spans) totalMs = Math.max(totalMs, span.end);
  if (spans.length && endMs && endMs > totalMs) {
    let last = null;
    for (const span of spans) {
      if (!last || span.end > last.end || (span.end === last.end && span.track > last.track)) {
        last = span;
      }
    }
    last.end = endMs;
    totalMs = endMs;
  }
  return { spans, totalMs };
}

/**
 * The span showing on each track at `t`, BOTTOM TRACK FIRST — which is the
 * compositing order, and the order `sceneAt` returns its pictures in.
 *
 * ⚠ ONE CLIP PER TRACK, AND THE LATER ONE WINS WHERE TWO OVERLAP. Free placement
 * makes an overlap possible where a butt-jointed sequence could not, and this is
 * the only tie-break a person can predict: whichever starts later is the one you
 * just put there. The timeline marks a clash (`.tl-bar.clash`) so it can be seen
 * and fixed rather than silently deciding the picture.
 *
 * ⚠ TWIN of `_stack_at` in `animatic_render.py`.
 */
export function stackAt(spans, t) {
  const byTrack = new Map();
  for (const span of spans) {
    if (t < span.start || t >= span.end) continue;
    const held = byTrack.get(span.track);
    if (
      !held ||
      span.start > held.start ||
      (span.start === held.start && span.index > held.index)
    ) {
      byTrack.set(span.track, span);
    }
  }
  return [...byTrack.keys()].sort((a, b) => a - b).map((track) => byTrack.get(track));
}

/** Every picture track the project uses, lowest first. Always includes 0. */
export function pictureTracks(frames) {
  const seen = new Set([0]);
  for (const frame of frames || []) seen.add(frameTrack(frame));
  return [...seen].sort((a, b) => a - b);
}

/**
 * WHICH BOARD SHOT A CLIP IS OF — the pair that survives ✨ Animate.
 *
 * ⚠ NOT `assetKey`. That one keys a render by its UPLOAD (its `src.kind` is
 * "video" by then), which is the right answer for the library and the wrong one
 * here: what this has to match is the render to the PANEL it was made from, and
 * the only thing the two still share is the board reference `attachVeoClip`
 * copies over. `frame` is in the key because a key pose and its panel share a
 * `storyboard_id` and an `index` — without it a render of pose 7 would pair with
 * the panel sitting under it.
 */
function shotKey(src) {
  if (!src?.storyboard_id || src.index === null || src.index === undefined) return "";
  return `${src.storyboard_id}:${src.index}:${src.frame ?? ""}`;
}

/**
 * PUSH THE STORYBOARD PANELS ALONG SO THE RENDERS ABOVE THEM DON'T PILE UP.
 *
 * A Veo render starts where its panel starts (`attachVeoClip`) but is as long as
 * Veo was ASKED for — 4s of footage over a 2s hold is the ordinary case, not an
 * edge one. Left alone, the second render then begins under the first one's tail
 * and the two bars overlap on the Storyboard video row:
 *
 *     video   [ Shot 1 ····· ]                     ⟵ before
 *     video       [ Shot 2 ····· ]                    (Shot 2 buried in Shot 1)
 *     image   [S1][S2][S3][S4]
 *
 *     video   [ Shot 1 ····· ][ Shot 2 ····· ]     ⟵ after
 *     image   [S1]            [S2]            [S3][S4]
 *
 * Reported as "my second shot 2 video overlap on shot1 video so this fuction not
 * good for user … automatic image all move like this so my Video and image clear
 * view so user not confuse waht happen in timeline".
 *
 * ⚠ THE PANEL IS WHAT MOVES, NOT THE RENDER. The render's place is the panel's,
 * so making room by sliding renders would only move the collision; the space a
 * 4-second take needs has to come from the row underneath it. A panel with a
 * render of its own therefore stays put and the ones AFTER it are pushed clear
 * of that take's end — which is the same "everything after it moves too" ripple
 * `insertPictures` performs, run against the video row's lengths instead of the
 * panels' own.
 *
 * ⚠ FORWARD ONLY, AND NEVER PAST WHERE A CLIP ALREADY IS (`Math.max`). This is
 * not a re-lay of the row: a panel that already sits clear of everything before
 * it does not move, so a gap the user opened by hand survives, and deleting a
 * render later leaves the spread it made rather than yanking every panel back
 * under a bar that is no longer there. The rule the whole picture track keeps —
 * a clip moves when you move it — is bent exactly once, for the ripple the user
 * asked for, and in one direction.
 *
 * ⚠ A RENDER MOVES BY ITS PANEL'S DELTA, not to its panel's start. Snapping it
 * would undo a nudge the user gave it; carrying the delta keeps a render lined
 * up with the panel it belongs to however it was placed. Whatever the render's
 * offset, its END is what the next panel clears.
 *
 * ⚠ PER BOARD TRACK. An animatic may hold a second "Storyboard images" row, and
 * a row's ripple is its own — the clock is kept per track, exactly as
 * `frameSpans` keeps its own.
 *
 * Returns a NEW list when anything moved and the SAME list when nothing did, so
 * a caller can tell whether this was an edit worth saying out loud.
 */
export function spreadPanelsForRenders(frames) {
  const list = frames || [];
  const { spans } = frameSpans(list);

  // Every render, by the shot it was made from, in list order — a panel that has
  // been animated twice ("Render again with Veo") has two, and must clear both.
  const rendersOf = new Map();
  for (let i = 0; i < list.length; i++) {
    if (!isVeoRender(list[i])) continue;
    const key = shotKey(list[i].src);
    if (!key) continue;
    if (!rendersOf.has(key)) rendersOf.set(key, []);
    rendersOf.get(key).push(i);
  }
  if (!rendersOf.size) return list;

  // The panels in the order they PLAY, not the order they are stored: a drag on
  // the timeline moves a clip without touching the list, so list order says
  // nothing about which shot comes first.
  const panels = [];
  for (let i = 0; i < list.length; i++) {
    if (clipRowKind(list[i]) === "board_image") panels.push(i);
  }
  panels.sort((a, b) => spans[a].start - spans[b].start || a - b);

  const moved = new Map(); // frame index -> its new start
  const paired = new Set(); // renders already spoken for
  const clock = new Map(); // track -> the first moment free on it
  for (const i of panels) {
    const span = spans[i];
    const start = Math.max(span.start, clock.get(span.track) || 0);
    const delta = start - span.start;
    if (delta) moved.set(i, start);
    let free = start + (span.end - span.start);
    // ⚠ ONLY THE FIRST UNPAIRED RENDER OF A SHOT PER PANEL. A duplicated panel
    // shares its `src` with the original, and pairing by key alone would give the
    // copy the same take — every panel after it pushed clear of a render that is
    // not over it.
    for (const j of rendersOf.get(shotKey(list[i].src)) || []) {
      if (paired.has(j)) continue;
      paired.add(j);
      if (delta) moved.set(j, spans[j].start + delta);
      free = Math.max(free, spans[j].start + delta + (spans[j].end - spans[j].start));
    }
    clock.set(span.track, free);
  }
  if (!moved.size) return list;
  return list.map((f, i) => (moved.has(i) ? { ...f, start_ms: moved.get(i) } : f));
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
 * ⚠ `pictures` IS THE PICTURE, AND IT IS A STACK — one entry per picture track
 * that has something on it at `t`, BOTTOM TRACK FIRST. Every renderer must walk
 * it; nothing may read `frame` and think it has drawn the film. An EMPTY stack is
 * legal and means the letterbox colour: with clips placed freely a track can have
 * a gap in it, and a gap on the bottom track with nothing above it is a moment
 * where the picture is the backdrop. (Before tracks the sequence had no holes, so
 * "no picture" only happened past the end.)
 *
 * ⚠ `frame` / `frame_b` / `mix` / `transition` / `transition_params` ARE THE
 * TOPMOST ENTRY, DERIVED — never computed a second way. They are what the
 * Properties pane and the transport mean by "the clip at the playhead", which is
 * a different question from "what is on screen"; keeping them is also what let
 * every caller that only ever wanted that one clip stay as it was. On a project
 * with one picture track — which is every animatic written before tracks — the
 * stack has exactly one entry and these are it, so nothing about such a project
 * resolves differently than it did.
 *
 * ON A TRANSITION a track has two pictures: `frame` is the OUTGOING one for the
 * whole window (see the note in `transitions.js` for why that, and not the one
 * `frameSpans` would pick, for the half past the cut), `frame_b` is the picture
 * arriving, and `mix` says how far through we are. Off a transition `frame_b` is
 * null, `mix` is 0 and `transition` is null.
 */
export function sceneAt(project, tMs, endMs = null) {
  const frames = project.frames || [];
  const { spans, totalMs } = frameSpans(frames, endMs);
  const t = Math.max(0, Number(tMs) || 0);

  // ⚠ ONE PASS PER TRACK, bottom to top, and each track resolves its own
  // transition. A transition is track-local (`transitionWindow`), so asking the
  // project once and applying the answer to every track would put one track's
  // dissolve on another's picture.
  const pictures = stackAt(spans, t).map((span) => {
    let frame = pictureAt(frames, spans, span.index, t);
    // The transition overrides which picture is "the frame", because for the half
    // of the window past the cut the answer is the one on its way OUT. Both
    // pictures are resolved outside their own span here — keys hold at the ends
    // rather than extrapolating, so neither flies off screen.
    const active = transitionAt(project, t, spans, span.track);
    let frameB = null;
    if (active) {
      frame = pictureAt(frames, spans, active.fromIndex, t);
      frameB = pictureAt(frames, spans, active.toIndex, t);
    }
    return {
      track: span.track,
      frame,
      frame_b: frameB,
      mix: active ? active.mix : 0,
      transition: active ? active.kind : null,
      // ⚠ ALWAYS AN OBJECT, empty off a transition rather than absent — see the
      // note on the scene's own copy of this field below.
      transition_params: active ? active.params : {},
    };
  });
  // The topmost track's, derived. See the docstring: this is "the clip at the
  // playhead", not "the picture", and it is never worked out a second way.
  const top = pictures.length ? pictures[pictures.length - 1] : null;
  const frame = top ? top.frame : null;
  const frameB = top ? top.frame_b : null;
  const active = top && top.transition ? top : null;

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
    // ⚠ THE PICTURE, bottom track first. Renderers walk this; `frame` below is
    // the topmost entry and answers a different question. See the docstring.
    pictures,
    frame,
    // The transition, flattened onto the scene: the second picture, how far
    // through the blend we are, and which blend. Null / 0 / null off a cut.
    frame_b: frameB,
    mix: active ? active.mix : 0,
    transition: active ? active.transition : null,
    // ⚠ ALWAYS AN OBJECT, empty off a transition rather than absent. A missing
    // key here is `undefined`, which JSON drops, and the resolved scene would
    // then be a different SHAPE from the Python side — which is exactly how
    // `tests/render_parity.py` ends up comparing two things it thinks are equal
    // for the wrong reason. Same rule `place` and `blend` follow.
    transition_params: active ? active.transition_params : {},
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
  // ⚠ `mix` MUST be in the key. Without it every video frame of a dissolve
  // resolves to one signature, the exporter renders a single still and reuses
  // it for the whole blend, and the transition SNAPS instead of blending —
  // exactly the reuse bug this key already guards against for keyframes.
  // Added only when there IS a second picture, so a project with no transitions
  // produces byte-for-byte the signature it produced before they existed.
  //
  // ⚠ AND SO MUST ITS PARAMETERS, for the same reason once more: a wipe
  // travelling left and one travelling right resolve to the same two pictures at
  // the same `mix` and differ ONLY here. Leaving them out would render one still
  // per `mix` value and reuse it across directions — and worse, a project whose
  // only edit was the direction would hit the cache from the previous export and
  // come back unchanged.
  //
  // Only NON-DEFAULT parameters are written, so a plain wipe or dissolve signs
  // byte-for-byte what it signed before parameters existed.
  const paramExtra = (kind, params) => {
    const defaults = TRANSITION_PARAMS[kind] || {};
    const bits = Object.keys(defaults)
      .sort()
      .map((name) => [name, params[name] ?? defaults[name]])
      .filter(([name, value]) => value !== defaults[name])
      .map(([name, value]) => `${name}=${typeof value === "string" ? value : n(value)}`);
    return bits.length ? `:p${bits.join(",")}` : "";
  };
  // ⚠ EVERY TRACK, bottom first — not just the topmost. Two moments that differ
  // only in what an upper track is showing are two different frames, and signing
  // one of them would make the exporter reuse the other's still.
  //
  // With ONE track this writes byte-for-byte the string it always wrote (an `f…`
  // part, then an `x…` part on a transition), so a project that predates tracks
  // hits the render cache from its previous export exactly as before.
  if (!(scene.pictures || []).length) parts.push("f-");
  for (const picture of scene.pictures || []) {
    const f = picture.frame;
    if (!f) continue;
    parts.push(
      `f${f.index}:${n(f.scale)}:${n(f.x)}:${n(f.y)}:${n(f.opacity)}` +
        clipExtra(f) +
        lookExtra(f)
    );
    const b = picture.frame_b;
    if (!b) continue;
    parts.push(
      `x${picture.transition}:${n(picture.mix)}` +
        paramExtra(picture.transition, picture.transition_params || {}) +
        `:b${b.index}:${n(b.scale)}:${n(b.x)}:${n(b.y)}:${n(b.opacity)}` +
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
