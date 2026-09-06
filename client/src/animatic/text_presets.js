/**
 * Text in/out animations — as KEYFRAME MACROS, and nothing else.
 *
 * ⚠ READ `preset_util.js` FIRST. The rule that makes this file cheap, the reason
 * there are no new easing curves, and the reason every preset animates relative
 * to the clip's own resting values all live in that header. This file only says
 * WHICH animations are worth offering and what to call them.
 *
 * ---------------------------------------------------------------------------
 * ⚠ THE FIVE ORIGINAL IDS ARE SPOKEN FOR: none, fade, rise, drop, slide.
 * ---------------------------------------------------------------------------
 * They are the first five entries, they still write exactly the keys they wrote
 * before this file grew, and they must keep their ids for one reason that is not
 * about taste: the AI editor names a preset BY ID in its plans (`text_preset` in
 * `agent/actions.js`, validated against `caps.text.presets` in
 * `agent/capabilities.js`), and a saved plan naming an id this build no longer
 * has is a step that fails on replay. New presets are APPENDED. Renaming one is
 * breaking somebody's plan; deleting one is worse.
 *
 * ---------------------------------------------------------------------------
 * ⚠ `moves` IS NOT DECORATION — IT IS WHAT MAKES THE PRESET VISIBLE AT ALL.
 * ---------------------------------------------------------------------------
 * In FLOW placement a caption is stacked into its zone and x/y are resolved but
 * unused (see `textPlace` in `scene.js`), so a preset that slides would animate
 * nothing and the monitor would be lying about the export. A preset that touches
 * x or y therefore switches the clip to `place: "free"` at the position it is
 * already occupying, and says so with `moves: true`.
 *
 * ⚠ AND THE CONVERSE IS THE USEFUL HALF: `scale`, `rotation` and `opacity` all
 * work in FLOW placement, because the browser applies them as a transform about
 * the caption's zone anchor and `draw_texts` turns the measured block about the
 * same point. So every Pop, Spin, Zoom and Emphasis preset in here can be
 * dropped straight onto ordinary stacked subtitles — including a whole run of
 * generated captions — without moving a single one of them off its zone. That is
 * the difference between a preset library for TITLES and one that can also style
 * a subtitle track, and it is why those categories are the big ones.
 */

import { TEXT_DEFAULTS, textPlace } from "./scene.js";
import {
  applyPreset,
  arriveTrack,
  beatsFor,
  fadeTrack,
  mergeTracks,
  numberOr,
  settleTrack,
  wobbleTrack,
} from "./preset_util.js";

/** How long the in and out beats are, unless the caller says otherwise. */
export const DEFAULT_IN_MS = 400;
export const DEFAULT_OUT_MS = 400;
// A caption has to be readable between its two beats. If in + out would eat the
// whole clip they are squeezed to two fifths each, leaving a fifth of it held.
const MAX_BEAT_SHARE = 0.4;

/**
 * The distance a sliding preset travels, as a fraction of the frame.
 *
 * Small on purpose: a title that flies in from off-screen is a different (and
 * much worse) effect than one that settles into place. 7% of the frame reads as
 * movement at any size without the caption ever being somewhere it isn't meant
 * to be. The louder presets multiply it rather than inventing their own number,
 * so "how far does a slide go" has one answer to change.
 */
const TRAVEL = 0.07;

/**
 * The shelves the picker draws, in order.
 *
 * ⚠ A VIEW, NOT THE TRUTH — the same rule `fx_library.js` keeps about its
 * folders. `TEXT_PRESETS` below is the list; a preset whose `category` names no
 * shelf here still appears, under "Other", because a preset nobody filed should
 * be visible and ugly rather than invisible.
 */
export const TEXT_PRESET_CATEGORIES = [
  { id: "basic", label: "Basic", note: "The quiet ones. Safe on any caption." },
  { id: "pop", label: "Pop & bounce", note: "Arrives with a snap. Works on stacked subtitles." },
  { id: "zoom", label: "Zoom", note: "Comes in bigger or smaller than it lands." },
  { id: "slide", label: "Slide & travel", note: "Moves across the frame. Switches to free placement." },
  { id: "spin", label: "Spin & tilt", note: "Turns into place. Works on stacked subtitles." },
  { id: "impact", label: "Impact", note: "Hits hard, then settles. For a beat or a punchline." },
  { id: "loop", label: "Emphasis", note: "Keeps moving the whole time it is on screen." },
];

/**
 * The presets, in the order they are offered.
 *
 * `moves` says the preset animates position and therefore needs free placement.
 * `build` returns the keyframe tracks it owns, given the clip's RESTING values
 * and the two beat lengths — it never touches a track it does not own, and it
 * works outwards from the resting values rather than from 1 / 0 / centre.
 */
export const TEXT_PRESETS = [
  // --- Basic. The original five, unchanged. -------------------------------
  {
    id: "none",
    label: "None",
    category: "basic",
    hint: "Remove any in/out animation and hold the caption still.",
    moves: false,
    build: () => ({}),
  },
  {
    id: "fade",
    label: "Fade",
    category: "basic",
    hint: "Fades up, holds, fades away.",
    moves: false,
    build: ({ inMs, outMs, durationMs }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
    }),
  },
  {
    id: "rise",
    label: "Rise",
    category: "basic",
    hint: "Drifts up into place as it fades in.",
    moves: true,
    build: ({ inMs, outMs, durationMs, y }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      y: arriveTrack(y + TRAVEL, y, inMs),
    }),
  },
  {
    id: "drop",
    label: "Drop",
    category: "basic",
    hint: "Settles down into place as it fades in.",
    moves: true,
    build: ({ inMs, outMs, durationMs, y }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      y: arriveTrack(y - TRAVEL, y, inMs),
    }),
  },
  {
    id: "slide",
    label: "Slide in",
    category: "basic",
    hint: "Slides in from the left as it fades in.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      x: arriveTrack(x - TRAVEL * 1.5, x, inMs),
    }),
  },
  {
    id: "fade-slow",
    label: "Slow fade",
    category: "basic",
    hint: "A long, soft fade at both ends. For a mood, not a message.",
    moves: false,
    build: ({ inMs, outMs, durationMs }) => ({
      // Twice the beat, still inside the readable-hold budget because the beats
      // were clamped to two fifths each before they got here.
      opacity: fadeTrack(
        Math.min(inMs * 2, Math.floor(durationMs * MAX_BEAT_SHARE)),
        Math.min(outMs * 2, Math.floor(durationMs * MAX_BEAT_SHARE)),
        durationMs
      ),
    }),
  },
  {
    id: "flash",
    label: "Flash on",
    category: "basic",
    hint: "Snaps on in a frame or two, then fades away at the end.",
    moves: false,
    build: ({ outMs, durationMs }) => ({
      opacity: fadeTrack(Math.min(80, Math.floor(durationMs * 0.1)), outMs, durationMs),
    }),
  },

  // --- Pop & bounce. Scale only, so they work on stacked subtitles. -------
  {
    id: "pop",
    label: "Pop",
    category: "pop",
    hint: "Snaps up past its size and settles. The everyday caption pop.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.5), outMs, durationMs),
      scale: settleTrack(scale * 0.6, [[0.6, scale * 1.06], [1, scale]], inMs),
    }),
  },
  {
    id: "pop-soft",
    label: "Soft pop",
    category: "pop",
    hint: "The same idea, without the overshoot. Quiet enough for subtitles.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      scale: arriveTrack(scale * 0.88, scale, inMs),
    }),
  },
  {
    id: "bounce",
    label: "Bounce",
    category: "pop",
    hint: "Overshoots twice before it settles. Loud, and meant to be.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.35), outMs, durationMs),
      scale: settleTrack(
        scale * 0.3,
        [[0.45, scale * 1.16], [0.68, scale * 0.94], [0.86, scale * 1.04], [1, scale]],
        inMs
      ),
    }),
  },
  {
    id: "spring",
    label: "Spring",
    category: "pop",
    hint: "One overshoot and a small settle. Bounce, with manners.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.4), outMs, durationMs),
      scale: settleTrack(
        scale * 0.7,
        [[0.55, scale * 1.11], [0.8, scale * 0.97], [1, scale]],
        inMs
      ),
    }),
  },
  {
    id: "squash",
    label: "Squash",
    category: "pop",
    hint: "Arrives too big, squashes under its own weight, settles.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.3), outMs, durationMs),
      scale: settleTrack(
        scale * 1.35,
        [[0.5, scale * 0.9], [0.78, scale * 1.04], [1, scale]],
        inMs
      ),
    }),
  },
  {
    id: "punch",
    label: "Punch",
    category: "pop",
    hint: "Hits full size from way out, fast. A word, not a sentence.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      // ⚠ THE FADE IS SHORTER THAN THE MOVE ON PURPOSE. A punch that is still
      // half transparent when it lands reads as a fade, not as a hit.
      opacity: fadeTrack(Math.round(inMs * 0.25), outMs, durationMs),
      scale: settleTrack(scale * 1.9, [[0.7, scale * 0.98], [1, scale]], Math.round(inMs * 0.7)),
    }),
  },

  // --- Zoom. Also scale-only, and also safe on flow captions. -------------
  {
    id: "zoom-in",
    label: "Zoom in",
    category: "zoom",
    hint: "Grows into place from small. Even, no overshoot.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      scale: arriveTrack(scale * 0.5, scale, inMs),
    }),
  },
  {
    id: "zoom-out",
    label: "Zoom out",
    category: "zoom",
    hint: "Comes in oversized and shrinks to fit. Good on a title card.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      scale: arriveTrack(scale * 1.6, scale, inMs),
    }),
  },
  {
    id: "push",
    label: "Slow push",
    category: "zoom",
    hint: "Creeps towards the viewer the whole time it is up. Stops it looking pasted on.",
    moves: false,
    // ⚠ THE SAME 4% AND THE SAME REASONING AS `captionPush` in
    // `agent/actions.js`, which is what the AI editor puts on every caption it
    // writes. A caption is READ, and more than a few per cent makes the eye
    // chase the words instead of finishing the sentence. Offered as a preset so
    // a person can ask for the house move by hand.
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      scale: [
        { t: 0, v: scale, ease: "ease-in-out" },
        { t: durationMs, v: scale * 1.04, ease: "linear" },
      ],
    }),
    // Where the move ENDS, which is what the resting value has to be.
    rest: ({ scale }) => ({ scale: scale * 1.04 }),
  },
  {
    id: "pull",
    label: "Slow pull",
    category: "zoom",
    hint: "Drifts back away the whole time it is up. The push, reversed.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      scale: [
        { t: 0, v: scale * 1.04, ease: "ease-in-out" },
        { t: durationMs, v: scale, ease: "linear" },
      ],
    }),
  },

  // --- Slide & travel. Every one of these needs free placement. ----------
  ...directional(),
  {
    id: "fly-in",
    label: "Fly in",
    category: "slide",
    hint: "Comes in from off to the left, large, and lands.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x, scale }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.4), outMs, durationMs),
      x: arriveTrack(x - TRAVEL * 6, x, inMs),
      scale: arriveTrack(scale * 1.25, scale, inMs),
    }),
  },
  {
    id: "corner-in",
    label: "Corner in",
    category: "slide",
    hint: "Travels in from the bottom-left corner.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x, y }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      x: arriveTrack(x - TRAVEL * 2, x, inMs),
      y: arriveTrack(y + TRAVEL * 2, y, inMs),
    }),
  },
  {
    id: "glide",
    label: "Glide",
    category: "slide",
    hint: "Drifts slowly sideways the whole time it is on screen.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      x: [
        { t: 0, v: x - TRAVEL * 0.5, ease: "linear" },
        { t: durationMs, v: x + TRAVEL * 0.5, ease: "linear" },
      ],
    }),
    rest: ({ x }) => ({ x: x + TRAVEL * 0.5 }),
  },

  // --- Spin & tilt. Rotation, so they are safe on flow captions too. -----
  {
    id: "spin",
    label: "Spin",
    category: "spin",
    hint: "Turns half a circle into place as it grows. A logo sting.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale, rotation }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.4), outMs, durationMs),
      rotation: arriveTrack(rotation - 180, rotation, inMs),
      scale: arriveTrack(scale * 0.4, scale, inMs),
    }),
  },
  {
    id: "spin-small",
    label: "Quarter turn",
    category: "spin",
    hint: "A short turn into place. Reads as movement, not as a stunt.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale, rotation }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      rotation: arriveTrack(rotation - 25, rotation, inMs),
      scale: arriveTrack(scale * 0.9, scale, inMs),
    }),
  },
  {
    id: "tilt",
    label: "Tilt in",
    category: "spin",
    hint: "Leans in from one side and straightens up.",
    moves: false,
    build: ({ inMs, outMs, durationMs, rotation }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      rotation: arriveTrack(rotation + 9, rotation, inMs),
    }),
  },
  {
    id: "swing",
    label: "Swing",
    category: "spin",
    hint: "Swings past straight twice before it settles.",
    moves: false,
    build: ({ inMs, outMs, durationMs, rotation }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.4), outMs, durationMs),
      rotation: settleTrack(
        rotation - 14,
        [[0.45, rotation + 8], [0.72, rotation - 3], [1, rotation]],
        inMs
      ),
    }),
  },
  {
    id: "turn",
    label: "Turn in",
    category: "spin",
    hint: "Comes round from edge-on. The flat cousin of a 3D flip.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale, rotation }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.5), outMs, durationMs),
      rotation: arriveTrack(rotation - 90, rotation, inMs),
      // ⚠ THE SCALE IS WHAT SELLS IT. A flat turn on its own is a windscreen
      // wiper; coming in small at the same time reads as something rotating
      // towards you, which is the effect people mean by "flip".
      scale: arriveTrack(scale * 0.55, scale, inMs),
    }),
  },
  {
    id: "roll-in",
    label: "Roll in",
    category: "spin",
    hint: "Rolls in from the left, turning as it travels.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x, rotation }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.4), outMs, durationMs),
      x: arriveTrack(x - TRAVEL * 3, x, inMs),
      rotation: arriveTrack(rotation - 120, rotation, inMs),
    }),
  },

  // --- Impact. Short, violent, then still. --------------------------------
  {
    id: "shake",
    label: "Shake",
    category: "impact",
    hint: "Rattles sideways as it lands, then holds still.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x }) => ({
      opacity: fadeTrack(Math.round(inMs * 0.3), outMs, durationMs),
      x: wobbleTrack(x, TRAVEL * 0.35, 3, Math.min(durationMs, inMs * 1.6), { decay: 0.88 }),
    }),
  },
  {
    id: "shake-hard",
    label: "Hard shake",
    category: "impact",
    hint: "The same, three times as wide and in both directions.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x, y }) => {
      const beat = Math.min(durationMs, inMs * 1.8);
      return {
        opacity: fadeTrack(Math.round(inMs * 0.25), outMs, durationMs),
        x: wobbleTrack(x, TRAVEL, 4, beat, { decay: 0.86 }),
        y: wobbleTrack(y, TRAVEL * 0.4, 3, beat, { decay: 0.86 }),
      };
    },
  },
  {
    id: "stomp",
    label: "Stomp",
    category: "impact",
    hint: "Slams down from huge and shakes the frame it lands in.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x, scale }) => {
      const hit = Math.round(inMs * 0.55);
      return {
        opacity: fadeTrack(Math.round(inMs * 0.2), outMs, durationMs),
        scale: settleTrack(scale * 2.2, [[0.75, scale * 0.95], [1, scale]], hit),
        // ⚠ THE SHAKE STARTS WHERE THE DROP LANDS, not at zero — a caption that
        // is already rattling on its way down has not hit anything yet.
        x: wobbleTrack(x, TRAVEL * 0.5, 2.5, Math.min(durationMs - hit, inMs), {
          decay: 0.8,
          start: hit,
        }),
      };
    },
  },
  {
    id: "kick",
    label: "Kick up",
    category: "impact",
    hint: "Kicked up from below and caught. Short and sharp.",
    moves: true,
    build: ({ inMs, outMs, durationMs, y, scale }) => {
      const beat = Math.round(inMs * 0.6);
      return {
        opacity: fadeTrack(Math.round(inMs * 0.25), outMs, durationMs),
        y: settleTrack(y + TRAVEL * 1.2, [[0.7, y - TRAVEL * 0.15], [1, y]], beat),
        scale: settleTrack(scale * 1.2, [[0.7, scale * 0.98], [1, scale]], beat),
      };
    },
  },
  {
    id: "jitter",
    label: "Jitter",
    category: "impact",
    hint: "Never quite still. A nervous, hand-held feel for the whole clip.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x, y, rotation }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      // Three different cycle counts on purpose: matched ones would trace a neat
      // diagonal line, which reads as a slide rather than as jitter.
      x: wobbleTrack(x, 0.006, Math.max(4, durationMs / 320), durationMs),
      y: wobbleTrack(y, 0.005, Math.max(4, durationMs / 260), durationMs),
      rotation: wobbleTrack(rotation, 0.7, Math.max(3, durationMs / 420), durationMs),
    }),
  },

  // --- Emphasis. Runs the whole length of the clip. -----------------------
  {
    id: "pulse",
    label: "Pulse",
    category: "loop",
    hint: "Grows and shrinks gently, over and over.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      scale: wobbleTrack(scale, scale * 0.05, Math.max(1, durationMs / 900), durationMs),
    }),
  },
  {
    id: "breathe",
    label: "Breathe",
    category: "loop",
    hint: "A much slower, much smaller pulse. Barely there, on purpose.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      scale: wobbleTrack(scale, scale * 0.022, Math.max(1, durationMs / 2200), durationMs),
    }),
  },
  {
    id: "heartbeat",
    label: "Heartbeat",
    category: "loop",
    hint: "Two quick beats, a rest, and again.",
    moves: false,
    build: ({ inMs, outMs, durationMs, scale }) => {
      // One beat is two thumps close together and then a gap — which is a shape
      // no sine makes, so it is written out as the keys it is.
      const cycle = 1000;
      const keys = [];
      for (let at = 0; at <= durationMs; at += cycle) {
        keys.push(
          { t: at, v: scale, ease: "ease-out" },
          { t: at + 90, v: scale * 1.09, ease: "ease-in-out" },
          { t: at + 190, v: scale, ease: "ease-out" },
          { t: at + 280, v: scale * 1.05, ease: "ease-in-out" },
          { t: at + 390, v: scale, ease: "linear" }
        );
      }
      return {
        opacity: fadeTrack(inMs, outMs, durationMs),
        scale: mergeTracks(keys.filter((k) => k.t <= durationMs)),
      };
    },
  },
  {
    id: "sway",
    label: "Sway",
    category: "loop",
    hint: "Rocks slowly from side to side. A hanging sign.",
    moves: false,
    build: ({ inMs, outMs, durationMs, rotation }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      rotation: wobbleTrack(rotation, 3, Math.max(1, durationMs / 1800), durationMs),
    }),
  },
  {
    id: "wiggle",
    label: "Wiggle",
    category: "loop",
    hint: "A fast, small waggle. Playful — and tiring if it runs long.",
    moves: false,
    build: ({ inMs, outMs, durationMs, rotation }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      rotation: wobbleTrack(rotation, 5, Math.max(2, durationMs / 500), durationMs),
    }),
  },
  {
    id: "float",
    label: "Float",
    category: "loop",
    hint: "Rises and falls a hair's breadth, the whole time.",
    moves: true,
    build: ({ inMs, outMs, durationMs, y }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      y: wobbleTrack(y, 0.012, Math.max(1, durationMs / 2400), durationMs),
    }),
  },
  {
    id: "throb",
    label: "Throb",
    category: "loop",
    hint: "Dips in brightness over and over, without ever going out.",
    moves: false,
    build: ({ inMs, outMs, durationMs }) => {
      // ⚠ NEVER BELOW ~0.55, AND NEVER TO ZERO. A caption that blinks out is a
      // caption somebody is trying to read during the half of the cycle it is
      // not there. This dips; it does not flash.
      const pulse = wobbleTrack(0.78, 0.22, Math.max(1, durationMs / 800), durationMs);
      return {
        opacity: mergeTracks(fadeTrack(inMs, outMs, durationMs), pulse.filter(
          (k) => k.t > inMs && k.t < durationMs - outMs
        )),
      };
    },
  },
];

/**
 * The four straight slides, one per direction, in a fixed order.
 *
 * ⚠ DERIVED rather than four hand-written rows, the same reasoning
 * `fx_library.js` gives for its directional transitions: four near-identical
 * entries kept in step by hand is four chances to typo one of the offsets, and
 * nobody would notice until a caption slid the wrong way in an export.
 *
 * ⚠ "SLIDE UP" IS WHERE THE CAPTION GOES, which is the opposite of the naming
 * rule `motionKeys` uses for a camera pan — a caption is a thing being moved,
 * not a camera being aimed, and every editor names it this way round.
 *
 * `slide` and `rise`/`drop` above already cover in-from-left and the two
 * vertical ones at the small `TRAVEL`; these are the LOUD versions, at three
 * times the distance, which is what a "slide" preset means in a reel.
 */
function directional() {
  const dirs = [
    { id: "up", label: "Slide up", prop: "y", sign: 1, word: "below" },
    { id: "down", label: "Slide down", prop: "y", sign: -1, word: "above" },
    { id: "left", label: "Slide left", prop: "x", sign: 1, word: "the right" },
    { id: "right", label: "Slide right", prop: "x", sign: -1, word: "the left" },
  ];
  const out = [];
  for (const d of dirs) {
    out.push({
      id: `slide-${d.id}`,
      label: d.label,
      category: "slide",
      hint: `Travels in from ${d.word} and stops.`,
      moves: true,
      build: (ctx) => ({
        opacity: fadeTrack(ctx.inMs, ctx.outMs, ctx.durationMs),
        [d.prop]: arriveTrack(ctx[d.prop] + d.sign * TRAVEL * 3, ctx[d.prop], ctx.inMs),
      }),
    });
    out.push({
      id: `whip-${d.id}`,
      label: `Whip ${d.id}`,
      category: "slide",
      hint: `Snaps in from ${d.word} in a fraction of the time, and overshoots.`,
      moves: true,
      build: (ctx) => {
        const rest = ctx[d.prop];
        const beat = Math.round(ctx.inMs * 0.55);
        return {
          opacity: fadeTrack(Math.round(ctx.inMs * 0.3), ctx.outMs, ctx.durationMs),
          [d.prop]: settleTrack(
            rest + d.sign * TRAVEL * 5,
            [[0.72, rest - d.sign * TRAVEL * 0.5], [1, rest]],
            beat
          ),
        };
      },
    });
  }
  return out;
}

export const TEXT_PRESET_IDS = TEXT_PRESETS.map((p) => p.id);

/**
 * Every property a preset is allowed to write.
 *
 * ⚠ APPLYING A PRESET CLEARS ALL FIVE, INCLUDING THE ONES THAT PRESET DOES NOT
 * TOUCH — see `applyPreset` in `preset_util.js` for why (a caption that carries
 * on spinning after you chose "Fade" is what a picker feels broken like).
 *
 * ⚠ AND THAT IS A REAL CHANGE FOR `scale`, WHICH THIS LIST DID NOT USED TO
 * INCLUDE. `add_text` in `agent/actions.js` puts a slow 4% push on every caption
 * the AI editor writes (`captionPush`), and that push lives on `scale`. Applying
 * a preset by hand afterwards now REPLACES it rather than running alongside it,
 * which is the only thing a single track can do — the two cannot both own it,
 * and the preset is the more specific instruction because somebody asked for it
 * by name. "Slow push" is in the Zoom shelf for anyone who wants the house move
 * back.
 */
const OWNED = ["opacity", "x", "y", "scale", "rotation"];

/**
 * Apply a preset to a caption. Returns a PATCH — the object to hand to the
 * editor's `onChange(id, patch)` — exactly like every function in
 * `keyframes.js`, so the undo stack treats it as one ordinary edit.
 *
 * The beats are clamped against the clip's own length, so applying a preset to
 * a caption shorter than the beats gives a fast animation rather than one whose
 * keys sit past its end.
 */
export function applyTextPreset(clip, presetId, options = {}) {
  const preset = TEXT_PRESETS.find((p) => p.id === presetId);
  if (!preset) return {};

  const { durationMs, inMs, outMs } = beatsFor(clip, options, {
    inMs: DEFAULT_IN_MS,
    outMs: DEFAULT_OUT_MS,
    maxShare: MAX_BEAT_SHARE,
  });

  // The RESTING values — where the caption ends up, and what every movement is
  // measured from. Taken from the clip's stored fields rather than from wherever
  // an earlier preset's first key happened to leave it, so applying two presets
  // in a row doesn't walk the caption up the frame or shrink it twice.
  const ctx = {
    inMs,
    outMs,
    durationMs,
    x: numberOr(clip?.x, TEXT_DEFAULTS.x),
    y: numberOr(clip?.y, TEXT_DEFAULTS.y),
    scale: numberOr(clip?.scale, TEXT_DEFAULTS.scale),
    rotation: numberOr(clip?.rotation, TEXT_DEFAULTS.rotation),
  };

  const tracks = preset.build(ctx);
  // What the clip rests at afterwards. Everything goes back to where it was,
  // EXCEPT for a preset whose move deliberately ends somewhere else — "Slow
  // push" finishes at 104% and has to rest there, or it snaps back the moment
  // its last key passes. `opacity` is restored to 1 for that same reason: a
  // preset that ends on a fade-out would otherwise leave the stored value at 0
  // and the caption would be invisible the moment the preset is removed again.
  const rest = {
    opacity: 1,
    x: ctx.x,
    y: ctx.y,
    scale: ctx.scale,
    rotation: ctx.rotation,
    ...(preset.rest ? preset.rest(ctx) : null),
  };

  const patch = applyPreset(clip, tracks, OWNED, rest);
  // A moving preset needs free placement or it animates nothing. Switching HERE
  // rather than making the user do it first is what keeps a preset one click,
  // and it lands the caption where it already was — see TEXT_DEFAULTS.
  if (preset.moves && textPlace(clip) !== "free") patch.place = "free";
  return patch;
}
