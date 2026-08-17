// audio_mix.js — what a track sounds like at a given moment: its tone, its
// fader, its fades, and the duck it sits under.
//
// ⚠ TWIN FILE: `animatic.py` (`track_play_ms`, `fade_window`, `duck_ratio`,
// `EQ_BANDS`).
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
 * How long a track is HEARD for, in milliseconds.
 *
 * Its trim if it has one, otherwise what is left of the file after `offset_ms`
 * — and never longer than the video, because the export is cut there and a fade
 * placed past that is a fade nobody hears.
 */
export function trackPlayMs(track, totalMs = 0) {
  const trim = Math.round(track.trim_ms || 0);
  let play;
  if (trim > 0) {
    play = trim;
  } else {
    const duration = Math.round(track.duration_ms || 0);
    play = duration ? Math.max(0, duration - Math.max(0, Math.round(track.offset_ms || 0))) : 0;
  }
  if (play <= 0) play = Math.max(0, Math.round(totalMs || 0));
  if (totalMs) play = Math.min(play, Math.round(totalMs));
  return play;
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
 * The fade's gain at `ms` — 0 → 1 over the fade in, 1 → 0 over the fade out.
 *
 * Linear, because ffmpeg's `afade` is linear by default (`curve=tri`). Both
 * sides ramping on a straight line is the whole reason not to pick a nicer one.
 */
export function fadeGainAt(track, ms, totalMs = 0) {
  const { inMs, outAtMs, outMs } = fadeWindow(track, totalMs);
  let gain = 1;
  if (inMs > 0 && ms < inMs) gain = Math.max(0, ms) / inMs;
  if (outMs > 0 && ms > outAtMs) gain = Math.min(gain, Math.max(0, 1 - (ms - outAtMs) / outMs));
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
