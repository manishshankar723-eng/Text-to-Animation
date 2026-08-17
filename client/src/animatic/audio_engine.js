// audio_engine.js — the preview's mixer: every <audio> element goes through a
// WebAudio chain instead of straight to the speakers.
//
// WHY THIS EXISTS. An <audio> element gives you exactly two controls, `volume`
// and `muted`, and `volume` is clamped to 1. That was enough while a track had
// only a fader; it is not enough now:
//
//   • a track set to 150% previewed at 100% and exported at 150% — the pane had
//     to apologise for it in prose, which is a preview lying with a footnote;
//   • an EQ cannot be done to an element at all. Three fixed bands and three
//     BiquadFilterNodes are the same three filters the exporter builds, so the
//     tone you dial in is the tone that gets encoded.
//
// ⚠ THE ELEMENT IS STILL THE CLOCK. This changes where the SOUND goes, not
// where the time comes from: `useTimelineTransport` still reads `currentTime`
// off the first playing element, because a MediaElementSource plays its element
// — it does not replace it. Nothing about the master clock moved.
//
// The chain, in order, and it is the exporter's order:
//
//     <audio> → low shelf → mid peak → high shelf → gain → destination
//                └── EQ_BANDS ──┘        └─ fader × fade × duck
//
// ⚠ EVERYTHING HERE FAILS SOFT. `createMediaElementSource` can be called only
// once per element and throws on a second attempt; an AudioContext can be
// refused outright. Any failure falls back to `el.volume`, which is what the
// editor did before this file existed — a browser that won't give us a graph
// should lose the EQ, not the sound.

import { EQ_BANDS } from "./audio_mix.js";
import { clamp } from "./util.js";

let ctx = null;
let unavailable = false;
// el → { source, bands: [BiquadFilterNode…], gain }. Weak, because an element
// that React unmounts must not be kept alive by its own mixer strip.
const chains = new WeakMap();

/** The shared context, made on first use. Null if the browser won't give one. */
function context() {
  if (ctx || unavailable) return ctx;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) {
    unavailable = true;
    return null;
  }
  try {
    ctx = new Ctx();
  } catch {
    unavailable = true;
  }
  return ctx;
}

/**
 * Let sound out — call it from the gesture that starts playback.
 *
 * A context created before the user has interacted with the page starts
 * `suspended`, and a suspended context is silence: the element plays, the
 * playhead moves, and nothing comes out. Resuming from the click that started
 * playback is the one place it is guaranteed to be allowed.
 */
export function resumeAudio() {
  const c = context();
  if (c && c.state === "suspended") c.resume?.().catch(() => {});
}

/** This element's mixer strip, built on first sight. Null = no graph. */
function chainFor(el) {
  const found = chains.get(el);
  if (found) return found;
  const c = context();
  if (!c) return null;
  try {
    const source = c.createMediaElementSource(el);
    const bands = EQ_BANDS.map((band) => {
      const node = c.createBiquadFilter();
      node.type = band.type;
      node.frequency.value = band.hz;
      // Q is meaningless on a shelf in WebAudio (its shelves are slope-1
      // cookbook shelves, which is what the exporter's `t=s:w=1` states) — it
      // is set anyway so the peaking band and the table stay in step.
      node.Q.value = band.q;
      node.gain.value = 0;
      return node;
    });
    const gain = c.createGain();
    let node = source;
    for (const band of bands) {
      node.connect(band);
      node = band;
    }
    node.connect(gain);
    gain.connect(c.destination);
    // ⚠ The element's own volume is taken OUT of the picture: it applies before
    // the graph, so leaving it anywhere but 1 would put the fader in two places
    // and cap the result at 1 again. Mute stays on the element, where it is one
    // flag rather than a state to remember.
    el.volume = 1;
    const strip = { source, bands, gain };
    chains.set(el, strip);
    return strip;
  } catch {
    // Already routed by something else, or the browser refused. Either way the
    // element still plays; it just gets the old, clamped control.
    return null;
  }
}

/**
 * Put one track's level and tone on one element.
 *
 * @param el    the <audio>
 * @param gain  the finished linear gain — fader × fade × duck. NOT clamped to
 *              1: that cap is the thing this file exists to remove.
 * @param eq    the three band gains in dB, in `EQ_BANDS` order.
 */
export function applyTrackAudio(el, gain, eq) {
  const strip = chainFor(el);
  if (!strip) {
    // No graph: the old behaviour exactly, cap and all, and the EQ silently not
    // previewed. Silent ON PURPOSE — the only ways to get here are a browser
    // with no WebAudio at all and an element something else has already routed,
    // and a banner about neither would be noise on every session that is fine.
    el.volume = clamp(gain, 0, 1);
    return;
  }
  // `.value` rather than a ramp: a ramp would make what you hear lag what the
  // playhead says, and this is called every animation frame anyway, so the
  // result is already a smooth line rather than a step.
  strip.gain.gain.value = Math.max(0, gain);
  for (let i = 0; i < strip.bands.length; i++) {
    const want = eq && eq[i] ? eq[i] : 0;
    if (strip.bands[i].gain.value !== want) strip.bands[i].gain.value = want;
  }
}
