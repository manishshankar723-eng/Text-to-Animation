// beats.js — where the beats are, and the one decode everything else reads from.
//
// Timing a cut to the music is the reason audio is under the timeline at all,
// and "roughly on the beat" is exactly what you were trying to avoid — so the
// beats are DRAWN on the waveform and are snap targets like every cut and the
// playhead. Nothing is sent to the server and no library is added: the file is
// already decoded in the browser to draw the waveform, and the onsets fall out
// of the same pass.
//
// THE DECODE IS SHARED AND CACHED BY URL. It used to live inside `Waveform.jsx`,
// which was fine while the peaks were the only thing anyone wanted; the duck
// preview and the beat markers want the same samples, and decoding a
// multi-megabyte MP3 three times is both slow and three chances to disagree
// about how long the file is.
//
// The pure half — `energyEnvelope` and `onsetsFromEnvelope` — takes plain
// arrays and touches no browser API, which is what lets `tests/audio_mix_check.py`
// run it through node against a click track at a known BPM.

// Peaks are computed ONCE at this resolution and re-bucketed for whatever width
// the canvas happens to be, so zooming redraws instantly instead of decoding a
// multi-megabyte MP3 again.
const PEAK_BUCKETS = 4000;

// The envelope's step, in samples. 256 is ~5.8ms at 44.1kHz: fine enough that a
// marker lands on the transient rather than near it, coarse enough that a
// five-minute track is fifty thousand numbers rather than thirteen million.
export const HOP = 256;

// An onset has to be this much louder than the local average to count, and two
// of them cannot land closer together than this. 120ms is 500 BPM — faster than
// any beat anyone cuts to, so it only ever rejects one transient counted twice.
const ONSET_DELTA = 1.35;
const ONSET_MIN_GAP_MS = 120;
// How much history the local average is taken over. Long enough to ride out a
// loud passage, short enough to follow a track that changes.
const ONSET_WINDOW_MS = 350;

/**
 * RMS per hop over a mono mix of `channels`.
 *
 * Plain arrays in, Float32Array out — no AudioBuffer, so this runs under node.
 */
export function energyEnvelope(channels, length, hop = HOP) {
  const frames = Math.max(0, Math.floor(length / hop));
  const out = new Float32Array(frames);
  for (let f = 0; f < frames; f++) {
    const start = f * hop;
    let sum = 0;
    for (let j = 0; j < hop; j++) {
      let mixed = 0;
      for (const data of channels) mixed += data[start + j] || 0;
      mixed /= channels.length || 1;
      sum += mixed * mixed;
    }
    out[f] = Math.sqrt(sum / hop);
  }
  return out;
}

/**
 * Onset times in milliseconds, from an energy envelope.
 *
 * Energy-based, not spectral: an FFT would find the beat inside a sustained
 * chord, but everything a storyboard is cut to has a transient on the beat, and
 * this needs no library and no worker. The rule is the standard one — a
 * half-wave rectified rise, a peak that leads its own neighbours, and enough
 * over the local average to be a hit rather than a swell.
 */
export function onsetsFromEnvelope(env, hopMs) {
  if (!env || env.length < 3 || !hopMs) return [];
  // Rise only: the fall at the end of a note is not an onset. The first frame
  // is measured against SILENCE, because a track that starts on the downbeat
  // has an onset at zero and there is no earlier frame to see it rise from.
  const flux = new Float32Array(env.length);
  flux[0] = env[0];
  for (let i = 1; i < env.length; i++) flux[i] = Math.max(0, env[i] - env[i - 1]);

  const window = Math.max(3, Math.round(ONSET_WINDOW_MS / hopMs));
  const minGap = Math.max(1, Math.round(ONSET_MIN_GAP_MS / hopMs));
  // A floor under the threshold, so a silent passage doesn't turn its own noise
  // into a beat: 5% of the loudest rise in the whole file.
  let loudest = 0;
  for (let i = 0; i < flux.length; i++) if (flux[i] > loudest) loudest = flux[i];
  const floor = loudest * 0.05;
  if (loudest <= 0) return [];

  const out = [];
  let last = -minGap - 1;
  for (let i = 0; i < flux.length; i++) {
    const v = flux[i];
    // A peak leads its own neighbours; the ends have only one to lead.
    if (v <= floor) continue;
    if (i > 0 && v < flux[i - 1]) continue;
    if (i < flux.length - 1 && v < flux[i + 1]) continue;
    let sum = 0;
    let n = 0;
    for (let j = Math.max(0, i - window); j <= Math.min(flux.length - 1, i + window); j++) {
      sum += flux[j];
      n++;
    }
    if (v < (sum / n) * ONSET_DELTA) continue;
    if (i - last < minGap) continue;
    last = i;
    // The hop this peak falls in STARTED one hop earlier — the rise is measured
    // between two frames, and the transient is at the beginning of the second.
    out.push(Math.round(i * hopMs));
  }
  return out;
}

/** The waveform's peaks, at the resolution the canvas re-buckets from. */
function peaksOf(channels, length) {
  const peaks = new Float32Array(PEAK_BUCKETS);
  const per = Math.max(1, Math.floor(length / PEAK_BUCKETS));
  for (let i = 0; i < PEAK_BUCKETS; i++) {
    const start = i * per;
    let peak = 0;
    for (let j = 0; j < per; j += 4) {
      // Step by 4: at this bucket size the extra samples don't change the drawn
      // shape, and it keeps a 10-minute file responsive.
      for (const data of channels) {
        const v = Math.abs(data[start + j] || 0);
        if (v > peak) peak = v;
      }
    }
    peaks[i] = peak;
  }
  return peaks;
}

// url → the promise of its analysis. The promise rather than the result, so two
// components asking during the same tick share one decode instead of starting
// two. A blob url is revoked when its track goes, and the entry goes with it.
const cache = new Map();

/**
 * Decode one audio file once: `{ peaks, envelope, hopMs, durationMs, beats }`.
 *
 * `beats` are in FILE time — the same clock `offset_ms` is measured on — so a
 * caller drawing them on a trimmed clip subtracts the offset, exactly as the
 * waveform does.
 */
export function analyseAudio(url) {
  if (!url) return Promise.resolve(null);
  const hit = cache.get(url);
  if (hit) return hit;
  const job = decode(url).catch((e) => {
    // A file that won't decode is not an error banner — the track still plays
    // and still exports; it just has no waveform and no markers. Dropping it
    // from the cache means a later attempt can succeed.
    cache.delete(url);
    throw e;
  });
  cache.set(url, job);
  return job;
}

/** Forget one file's analysis (its blob url is being revoked). */
export function forgetAudio(url) {
  cache.delete(url);
}

async function decode(url) {
  const res = await fetch(url);
  const bytes = await res.arrayBuffer();
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  const ctx = new Ctx();
  try {
    const buffer = await ctx.decodeAudioData(bytes);
    const channels = [];
    for (let c = 0; c < buffer.numberOfChannels; c++) channels.push(buffer.getChannelData(c));
    const hopMs = (HOP / buffer.sampleRate) * 1000;
    const envelope = energyEnvelope(channels, buffer.length, HOP);
    return {
      peaks: peaksOf(channels, buffer.length),
      envelope,
      hopMs,
      durationMs: buffer.duration * 1000,
      beats: onsetsFromEnvelope(envelope, hopMs),
    };
  } finally {
    // Chrome caps how many AudioContexts a page may hold; a decode-only context
    // that is never closed will eventually stop new ones from being created.
    ctx.close?.();
  }
}
