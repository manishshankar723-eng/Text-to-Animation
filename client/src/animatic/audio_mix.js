// audio_mix.js — what a track sounds like at a given moment: its tone, its
// fader, its fades, and the duck it sits under.
//
// ⚠ TWIN FILE: `animatic.py` (`track_play_ms`, `fade_window`, `curve_gain`,
// `duck_ratio`, `EQ_BANDS`).
// The editor previews the mix by setting each <audio> element's volume every
// animation frame; the exporter builds an ffmpeg graph. Those are two completely
// different machines, so the ONE thing that must not drift is where the ramps
// are and how deep they go — a track that fades out over the last two seconds in
// the monitor and over the last four in the MP4 is the audio version of a
// preview that lies. `tests/audio_mix_check.py` runs `fadeWindow` through node
// and compares it against `fade_window` window for window.
//
// The duck is the honest exception and is documented as one. The export runs a
// real sidechain compressor over the voice; this runs the SAME compressor law
// (rms detection, downward compression, attack/release) over the envelope
// `beats.js` decoded, at that envelope's resolution. Close, not sample-exact.

import { clamp } from "./util.js";

// --- Tone -------------------------------------------------------------------
// THREE FIXED BANDS, and each one is exactly one `BiquadFilterNode` — which is
// exactly one ffmpeg filter on the other side (`bass` / `equalizer` / `treble`,
// the same RBJ cookbook shapes). That correspondence is the whole reason the EQ
// is three fixed bands rather than parametric: a frequency and a Q stored on the
// project would be two more numbers that have to mean the same thing in two
// filter implementations.
//
// ⚠ `q: 1` IS LOAD-BEARING ON THE SHELVES. A WebAudio shelf is a cookbook shelf
// with a SLOPE of 1; ffmpeg's `bass`/`treble` default to a Q of 0.5, which is a
// different filter. The exporter states `t=s:w=1` for exactly this reason.
export const EQ_BANDS = [
  { id: "low", field: "eq_low", type: "lowshelf", hz: 120, q: 1.0, label: "Low" },
  { id: "mid", field: "eq_mid", type: "peaking", hz: 1000, q: 1.0, label: "Mid" },
  { id: "high", field: "eq_high", type: "highshelf", hz: 6000, q: 1.0, label: "High" },
];
// Below this a band counts as untouched — the same epsilon the exporter uses to
// decide whether to build the filter at all.
export const EQ_EPSILON = 0.05;

/** The three band gains in dB, in `EQ_BANDS` order. */
export function eqGains(track) {
  return EQ_BANDS.map((band) => Number(track[band.field]) || 0);
}

/** Is anything actually being done to this track's tone? */
export function hasEq(track) {
  return eqGains(track).some((g) => Math.abs(g) >= EQ_EPSILON);
}

// The duck's shape. These four are the numbers `animatic.py` passes to
// sidechaincompress, so the preview's compressor is the export's compressor.
export const DUCK_THRESHOLD = 0.03; // ≈ −30 dBFS
export const DUCK_NOMINAL_DB = 12.0;
export const DUCK_MAX_RATIO = 20.0;
export const DUCK_ATTACK_MS = 20;
export const DUCK_RELEASE_MS = 400;

/**
 * Where on the TIMELINE this clip begins.
 *
 * 0 for anything that predates the razor being able to cut audio, which is what
 * makes those projects mix exactly as they always did.
 *
 * ⚠ TWIN of `track_start_ms` in `animatic.py`.
 */
export function trackStartMs(track) {
  return Math.max(0, Math.round(track?.start_ms || 0));
}

/**
 * How long a track is HEARD for, in milliseconds.
 *
 * Its trim if it has one, otherwise what is left of the file after `offset_ms`
 * — and never longer than the room the video leaves AFTER the clip starts,
 * because the export is cut there and a fade placed past that is a fade nobody
 * hears.
 */
export function trackPlayMs(track, totalMs = 0) {
  const start = trackStartMs(track);
  // What is left of the video once this clip has waited its turn. A clip
  // sitting entirely past the end of the video is heard for nothing at all.
  const room = totalMs ? Math.max(0, Math.round(totalMs) - start) : 0;
  const trim = Math.round(track.trim_ms || 0);
  let play;
  if (trim > 0) {
    play = trim;
  } else {
    const duration = Math.round(track.duration_ms || 0);
    play = duration ? Math.max(0, duration - Math.max(0, Math.round(track.offset_ms || 0))) : 0;
  }
  if (play <= 0) play = room;
  if (totalMs) play = Math.min(play, room);
  return play;
}

/**
 * `{ startMs, endMs, playMs }` — the stretch of TIMELINE this clip occupies.
 *
 * Editor-side convenience; the exporter has no counterpart and needs none,
 * because ffmpeg is told where the clip goes with one `adelay` rather than
 * asked "is it audible at t". Everything that has to know whether a clip is
 * under the playhead — the mixer, the razor, the drag — reads it from here, so
 * there is one answer rather than four.
 */
export function trackWindow(track, totalMs = 0) {
  const startMs = trackStartMs(track);
  const playMs = trackPlayMs(track, totalMs);
  return { startMs, endMs: startMs + playMs, playMs };
}

// --- The shape of a fade ----------------------------------------------------
// WHAT A CROSSFADE IS, and why it is a parameter rather than an object.
//
// Premiere files three things under Audio Transitions → Crossfade, and they are
// three CURVES, not three mechanisms: Constant Gain ramps the gain on a straight
// line, Constant Power on a quarter sine, Exponential Fade on a decade curve.
// Nothing else about them differs. And a crossfade between two clips is just
// the outgoing one's fade OUT overlapping the incoming one's fade IN — which
// this editor already had, because audio clips are placed absolutely and the
// exporter already mixes whatever overlaps. So the whole feature is a curve on
// each end of a clip; see `crossfadePatch` in `audio_clips.js` for the gesture
// that sets both ends of a cut at once.
//
// ⚠ THE FORMULAE ARE FFMPEG'S, TRANSCRIBED AND NOT INVENTED. Each curve is a
// `curve=` on `afade`, and `afade` is what actually shapes the exported audio —
// so a nicer-looking curve here would be a preview that lies about the MP4.
// They are `fade_gain()` in libavfilter/af_afade.c at afade's default
// silence=0 / unity=1.
//
// ⚠ "linear" IS THE DEFAULT EVERYWHERE, because it IS what already shipped:
// every fade in every existing project is `afade`'s default `curve=tri`. A
// project that has never heard of this field therefore mixes exactly as it
// always did, and nothing needed migrating.
//
// ⚠ TWIN of `FADE_CURVES` and `FADE_FF_CURVE` in `animatic.py`.
export const FADE_CURVES = ["linear", "power", "exponential"];

/**
 * What each curve is CALLED — Premiere's three names, because they are the ones
 * an editor arrives already looking for.
 *
 * The note is what the name doesn't say. "Constant gain" and "constant power"
 * are the same two words to anybody who has not already been told the
 * difference, so each note says what you HEAR instead.
 *
 * `ff` is the `afade` curve it becomes on export, kept here so the reader can
 * see the correspondence without opening the Python. `tests/audio_mix_check.py`
 * asserts it against `FADE_FF_CURVE` rather than trusting the comment.
 */
export const FADE_CURVE_INFO = {
  linear: {
    label: "Constant Gain",
    note: "A straight line — dips through the middle of a crossfade",
    ff: "tri",
  },
  power: {
    label: "Constant Power",
    note: "Holds the level through a crossfade — usually the one you want",
    ff: "qsin",
  },
  exponential: {
    label: "Exponential Fade",
    note: "Holds on, then drops away late — a long tail",
    ff: "exp",
  },
};

/**
 * The curve on ONE END of one clip, folded to "linear" if it is not one.
 *
 * Folded rather than validated, the same rule `transitionKind` follows: a
 * project written by a newer client naming a curve this build has never heard
 * of still opens and still plays, at the shape every project used to have.
 *
 * ⚠ TWIN of `fade_curve` in `animatic.py`.
 */
export function fadeCurve(track, side) {
  const raw = String(track?.[side === "in" ? "fade_in_curve" : "fade_out_curve"] || "");
  return FADE_CURVES.includes(raw) ? raw : "linear";
}

/**
 * The gain a curve gives at `x`, where x is 0 at silence and 1 at full level.
 *
 * ⚠ BOTH ENDS READ THE SAME CURVE, with x running towards 1 at full level — a
 * fade out is this function read backwards, not a second formula. That is also
 * exactly why constant power holds a crossfade up: sin(x·π/2) coming in against
 * sin((1−x)·π/2) going out sums to unity in POWER, which is what the name is
 * claiming. Constant gain sums to unity in AMPLITUDE instead, so two of them
 * crossing leave an audible −3 dB scoop in the middle — the dip its note warns
 * about, and the reason the other two curves exist at all.
 *
 * ⚠ TWIN of `curve_gain` in `animatic.py`.
 */
export function curveGain(curve, x) {
  const t = clamp(x, 0, 1);
  switch (curve) {
    case "power":
      return Math.sin((t * Math.PI) / 2);
    case "exponential":
      // −11.5129… is 5·ln(0.1): a decade curve bottoming out at −100 dB, which
      // is ffmpeg's `exp` to the digit. It is STEEPER than Premiere's fade of
      // the same name — matching the encoder we can measure beats matching an
      // editor we cannot, because only one of the two ends up in the MP4.
      return Math.exp(-11.512925464970227 * (1 - t));
    default:
      return t;
  }
}

/**
 * `{ inMs, outAtMs, outMs }` — the two ramps, in TRACK time.
 *
 * Two fades longer than the track would cross and cancel, so they are scaled
 * down together, keeping their ratio. Same rule as a transition never eating
 * more than half a picture.
 */
export function fadeWindow(track, totalMs = 0) {
  const play = trackPlayMs(track, totalMs);
  let inMs = Math.max(0, Math.round(track.fade_in_ms || 0));
  let outMs = Math.max(0, Math.round(track.fade_out_ms || 0));
  if (play <= 0 || (!inMs && !outMs)) return { inMs: 0, outAtMs: play, outMs: 0 };
  inMs = Math.min(inMs, play);
  outMs = Math.min(outMs, play);
  if (inMs + outMs > play) {
    const scale = play / (inMs + outMs);
    // TRUNCATED, like Python's int() — the two halves have to agree to the
    // millisecond or the parity check is measuring rounding, not the rule.
    inMs = Math.trunc(inMs * scale);
    outMs = Math.trunc(outMs * scale);
  }
  return { inMs, outAtMs: Math.max(0, play - outMs), outMs };
}

/**
 * The fade's gain at `ms` — 0 → 1 over the fade in, 1 → 0 over the fade out,
 * along whichever curve each END of the clip carries.
 *
 * ⚠ THE TWO ENDS ARE READ SEPARATELY, and they have to be: a crossfade sets one
 * end of one clip and the opposite end of its neighbour, so a single curve per
 * clip would make the second crossfade you added change the shape of the first.
 *
 * `min` of the two is belt and braces — `fadeWindow` has already scaled them so
 * they cannot overlap — and it is kept because the alternative is one multiply
 * that silently squares the gain in the case that is supposed to be impossible.
 */
export function fadeGainAt(track, ms, totalMs = 0) {
  const { inMs, outAtMs, outMs } = fadeWindow(track, totalMs);
  let gain = 1;
  if (inMs > 0 && ms < inMs) {
    gain = curveGain(fadeCurve(track, "in"), Math.max(0, ms) / inMs);
  }
  if (outMs > 0 && ms > outAtMs) {
    gain = Math.min(gain, curveGain(fadeCurve(track, "out"), 1 - (ms - outAtMs) / outMs));
  }
  return clamp(gain, 0, 1);
}

/** The compressor ratio that pulls a track down to roughly `duckTo`. */
export function duckRatio(duckTo) {
  const gain = clamp(Number(duckTo) || 1, 0.001, 1);
  if (gain >= 1) return 1;
  const wantedDb = -20 * Math.log10(gain);
  const share = wantedDb / DUCK_NOMINAL_DB;
  if (share >= 1) return DUCK_MAX_RATIO;
  return clamp(1 / (1 - share), 1, DUCK_MAX_RATIO);
}

/**
 * A gain per envelope hop: 1 where the voice is quiet, down to about `duckTo`
 * where it is talking.
 *
 * `voiceEnv` is the RMS envelope `beats.js` decoded, `hopMs` its resolution.
 * The law is sidechaincompress's: reduce by `(level − threshold) × (1 − 1/ratio)`
 * in dB while the key is above the threshold, then smooth with a one-pole
 * attack and release so the duck opens fast and closes slowly.
 */
export function duckEnvelope(voiceEnv, hopMs, duckTo) {
  const ratio = duckRatio(duckTo);
  const out = new Float32Array(voiceEnv.length);
  if (ratio <= 1) return out.fill(1);
  const slope = 1 - 1 / ratio;
  // One-pole coefficients: how much of the way to the target one hop moves.
  const aStep = 1 - Math.exp(-hopMs / Math.max(1, DUCK_ATTACK_MS));
  const rStep = 1 - Math.exp(-hopMs / Math.max(1, DUCK_RELEASE_MS));
  let gain = 1;
  for (let i = 0; i < voiceEnv.length; i++) {
    const level = Math.max(1e-6, voiceEnv[i]);
    let target = 1;
    if (level > DUCK_THRESHOLD) {
      const overDb = 20 * Math.log10(level / DUCK_THRESHOLD);
      target = Math.pow(10, (-overDb * slope) / 20);
    }
    // Down is the attack, up is the release — a compressor is not symmetric.
    gain += (target - gain) * (target < gain ? aStep : rStep);
    out[i] = clamp(gain, 0, 1);
  }
  return out;
}

/** Read an envelope at a time in milliseconds. 1 when there isn't one. */
export function envelopeAt(env, hopMs, ms) {
  if (!env || !env.length || !hopMs) return 1;
  const i = Math.round(ms / hopMs);
  if (i < 0) return env[0];
  if (i >= env.length) return env[env.length - 1];
  return env[i];
}
