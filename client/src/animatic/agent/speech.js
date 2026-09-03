// speech.js — WHERE THE TALKING IS, AND WHERE IT IS NOT.
//
// ---------------------------------------------------------------------------
// ⚠ THIS IS THE CLIENT TWIN OF `captions.spans_from_envelope`.
// ---------------------------------------------------------------------------
// ⚠ **TWINNED IN `captions.py`** — the same rule the transitions, the effects and
// the shape kinds all carry. Every constant below is that file's, by name and by
// value, and a change to one is a change owed to the other:
//
//     ENVELOPE_WINDOW_MS   NOISE_FLOOR_MULTIPLE   SOUND_PEAK_SHARE
//     MAX_THRESHOLD_SHARE  MIN_SILENCE_MS         MIN_SPEECH_MS
//     MIN_SOUND_SHARE
//
// `tests/editor_chat_check.py` reads both files and asserts they agree, so the
// twin cannot drift silently — which is the only thing that makes a second copy
// of an algorithm defensible.
//
// ---------------------------------------------------------------------------
// ⚠ AND IT IS FREE. THAT IS THE WHOLE REASON IT IS HERE AND NOT ON THE SERVER.
// ---------------------------------------------------------------------------
// The editor already decodes every audio upload once and keeps its energy
// envelope (`beats.js` → `useAudioAnalysis`), because the timeline draws
// waveforms and snaps clip edges to beats. So "where is the dead air" is a
// question this browser can already answer, for nothing: no upload, no ffmpeg,
// no model call, no quota. A server route would have re-decoded a file the
// browser had already decoded, to compute a number it already had.
//
// ⚠ **IT IS NOT A TRANSCRIPT AND CANNOT FIND A WORD.** An envelope knows loud
// from quiet; it does not know "umm" from "and". Removing a filler WORD needs
// word-level timings, which this app's caption pipeline does not produce — see
// `fillerLines` below for the half of that problem which IS solvable, and
// `AGENTS.md` for the half that is not.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE — no React, no DOM, no editor import.
// ---------------------------------------------------------------------------
// Same rule as `actions.js`, `capabilities.js` and `chat_turn.js`: a test loads
// it under node.

/** ⚠ TWINNED IN `captions.py`. One bucket of the envelope, and an edge's resolution. */
export const ENVELOPE_WINDOW_MS = 20;

/** ⚠ TWINNED IN `captions.py`. The threshold is derived from the track, never fixed. */
export const NOISE_FLOOR_MULTIPLE = 2.5;
export const SOUND_PEAK_SHARE = 0.04;
export const MAX_THRESHOLD_SHARE = 0.2;

/**
 * ⚠ TWINNED IN `captions.py`. How long a quiet patch lasts before it is a PAUSE
 * rather than the gap inside a word. 200ms is longer than any stop consonant and
 * shorter than any real pause between sentences.
 */
export const MIN_SILENCE_MS = 200;

/**
 * ⚠ TWINNED IN `captions.py`. A blip this short between two silences is a click,
 * a breath or a door — not a line of dialogue.
 */
export const MIN_SPEECH_MS = 120;

/**
 * ⚠ TWINNED IN `captions.py`. Below this share of the file being sound, the
 * measurement is not believed at all.
 *
 * A track that measures 88% silence is a detection that went wrong — a noise
 * floor above the threshold, a format decoded oddly — and acting on it would
 * mean proposing to cut most of somebody's voiceover. The answer to a
 * measurement you do not trust is to say so, not to use it carefully.
 */
export const MIN_SOUND_SHARE = 0.12;

/**
 * A pause worth telling the model about.
 *
 * ⚠ NOT `MIN_SILENCE_MS`. That one separates a pause from a consonant, which is
 * the right line for laying captions on a wave and far too fine for an edit
 * decision: every sentence in a normal reading has 300ms of air after it, and a
 * chat that offers to remove forty of those has offered to make the film
 * unlistenable. This is the line at which a gap reads as DEAD AIR to a person.
 */
export const DEAD_AIR_MS = 900;

/**
 * A loudness envelope → the runs of SOUND in it.
 *
 * ⚠ THE TWO CLEAN-UPS, IN THIS ORDER, AND FOR DIFFERENT REASONS — the same two
 * `captions.spans_from_envelope` applies:
 *   1. a gap under `MIN_SILENCE_MS` is the stop inside a word, so the runs
 *      either side of it are ONE run;
 *   2. a run under `MIN_SPEECH_MS` is a click or a breath, and is dropped.
 *
 * @param envelope  per-bucket loudness (`Float32Array` or array), from `beats.js`
 * @param windowMs  how long one bucket is. `beats.js` hops by samples, so its
 *                  own `hopMs` is passed in rather than assumed.
 * @returns `[{start_ms, end_ms}]`
 */
export function spansFromEnvelope(envelope, windowMs = ENVELOPE_WINDOW_MS) {
  const env = envelope && envelope.length ? Array.from(envelope) : [];
  if (!env.length) return [];
  const loudest = Math.max(...env);
  if (!(loudest > 0)) return [];

  // The noise floor: the quietest tenth of the track. Real silence puts this at
  // ~0 and hiss puts it at the hiss — but on a track with no pauses at all it
  // lands in the middle of the speech, which is why the result is capped.
  const ordered = [...env].sort((a, b) => a - b);
  const floor = ordered[Math.max(0, Math.floor(ordered.length * 0.1) - 1)] || 0;
  const threshold = Math.min(
    Math.max(floor * NOISE_FLOOR_MULTIPLE, loudest * SOUND_PEAK_SHARE),
    loudest * MAX_THRESHOLD_SHARE
  );

  const runs = [];
  for (let i = 0; i < env.length; i++) {
    if (env[i] < threshold) continue;
    const at = Math.round(i * windowMs);
    const last = runs[runs.length - 1];
    // Rule 1, applied as the runs are built.
    if (last && at - last[1] <= MIN_SILENCE_MS) last[1] = at + windowMs;
    else runs.push([at, at + windowMs]);
  }
  return runs
    .filter(([a, b]) => b - a >= MIN_SPEECH_MS) // rule 2
    .map(([start, end]) => ({ start_ms: Math.round(start), end_ms: Math.round(end) }));
}

/**
 * WHERE THE DEAD AIR IS, on one track.
 *
 * ⚠ IT REPORTS `trusted: false` RATHER THAN GUESSING. Below `MIN_SOUND_SHARE`
 * the envelope is not describing speech, and the honest thing for the chat to
 * say is "I can't tell where the pauses are on this track" — not to propose
 * cutting four fifths of it.
 *
 * ⚠ AND THE LEADING GAP COUNTS. Air before the first word is the commonest dead
 * air there is, and it is the easiest to remove; a pass that only looked BETWEEN
 * runs would miss the one everybody notices.
 *
 * @returns `{trusted, spans, gaps, speechMs, silentMs, totalMs}` —
 *          `gaps` are `[{start_ms, end_ms, ms}]`, longest first.
 */
export function deadAir({ envelope, hopMs, durationMs } = {}) {
  const total = Math.max(0, Math.round(Number(durationMs) || 0));
  const spans = spansFromEnvelope(envelope, hopMs || ENVELOPE_WINDOW_MS);
  const speechMs = spans.reduce((n, s) => n + (s.end_ms - s.start_ms), 0);
  const empty = { trusted: false, spans: [], gaps: [], speechMs: 0, silentMs: 0, totalMs: total };
  if (!total || !spans.length) return empty;
  if (speechMs / total < MIN_SOUND_SHARE) return { ...empty, spans, speechMs };

  const gaps = [];
  let at = 0;
  for (const span of spans) {
    if (span.start_ms - at >= DEAD_AIR_MS) {
      gaps.push({ start_ms: at, end_ms: span.start_ms, ms: span.start_ms - at });
    }
    at = Math.max(at, span.end_ms);
  }
  // ⚠ AND THE TRAILING GAP TOO — a film that holds four seconds after the last
  // word is the other half of the same complaint.
  if (total - at >= DEAD_AIR_MS) {
    gaps.push({ start_ms: at, end_ms: total, ms: total - at });
  }

  return {
    trusted: true,
    spans,
    gaps: gaps.sort((a, b) => b.ms - a.ms),
    speechMs,
    silentMs: Math.max(0, total - speechMs),
    totalMs: total,
  };
}

/**
 * THE FILLER WORDS, AND WHY THIS LIST IS SHORT.
 *
 * ⚠ IT IS NOT A DICTIONARY AND MUST NOT BECOME ONE. Every word here is one that
 * carries no meaning in ANY sentence it appears in — that is the test. "like",
 * "so", "right" and "well" are all filler half the time and load-bearing the
 * other half ("a shot like this", "so it ends"), and a list that removed them
 * would edit the meaning out of somebody's script. A false positive here is a
 * word deleted from a film.
 *
 * ⚠ HINGLISH IS IN IT ON PURPOSE. This app's users write and speak Hinglish —
 * see RULEBOOK A8 — and a filler list that only knows English would find nothing
 * in half the voiceovers this editor is actually given.
 */
export const FILLER_WORDS = [
  // English
  "um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "mmm",
  // Hinglish / Hindi, in Latin letters as the users write them
  "matlab", "yaani", "haan", "arre", "achha", "toh",
];

const FILLER_SET = new Set(FILLER_WORDS);

/** A caption's words, lower-cased and stripped of punctuation. */
function wordsOf(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]+/gu, " ")
    .split(/\s+/)
    .filter(Boolean);
}

/**
 * CAPTION LINES THAT ARE MOSTLY FILLER.
 *
 * ⚠ **THIS REMOVES A CAPTION, NOT A SOUND, AND THE DIFFERENCE IS THE WHOLE
 * LIMITATION.** Cutting the word "umm" out of the AUDIO needs to know where that
 * word starts and stops, and this app's caption pipeline produces LINE timings,
 * not word timings (`captions.align_lines` lays a whole line onto a run of
 * sound). So what is offered here is honest and partial: a caption box that says
 * nothing gets taken off the screen, and the audio is untouched.
 *
 * ⚠ **AND THE BAR IS "MOSTLY", NOT "CONTAINS".** A line reading "umm, I think we
 * should go" is a real line with a stumble at the front; deleting it would
 * delete the sentence. Only a line that is filler and almost nothing else is
 * worth removing, which is what `share` is for.
 *
 * @param clips  caption clips, `[{id, text, start_ms, duration_ms}]`
 * @param share  how much of the line must be filler. 0.6 = most of it.
 * @returns `[{id, text, start_ms, filler, words}]`
 */
export function fillerLines(clips, share = 0.6) {
  const out = [];
  for (const clip of clips || []) {
    const words = wordsOf(clip && clip.text);
    if (!words.length) continue;
    const filler = words.filter((w) => FILLER_SET.has(w));
    if (!filler.length) continue;
    // ⚠ A ONE-WORD LINE THAT IS FILLER IS ALWAYS FILLER, whatever the share
    // arithmetic says about it — and it is the commonest shape of all.
    if (words.length > 1 && filler.length / words.length < share) continue;
    out.push({
      id: clip.id,
      text: String(clip.text || ""),
      start_ms: Math.max(0, Math.round(Number(clip.start_ms) || 0)),
      filler: filler.length,
      words: words.length,
    });
  }
  return out;
}

/** `1500` → `1.5s`. Times read as seconds everywhere a person sees them. */
const secs = (ms) => `${(Math.max(0, Number(ms) || 0) / 1000).toFixed(1)}s`;

/**
 * THE SOUND SECTION OF THE BOARD DIGEST — what the model is told about the audio.
 *
 * ⚠ **A SUMMARY, NOT THE SPANS.** A three-minute voiceover has four hundred
 * runs of sound in it, and every one of them in a prompt is a prompt nobody can
 * afford on every turn. What an edit decision actually needs is: is there speech,
 * how much of the film is silence, and WHERE are the few gaps long enough to
 * notice. Six lines answer that.
 *
 * ⚠ **AND IT SAYS WHEN IT DOES NOT KNOW.** An untrusted measurement is reported
 * as unmeasurable rather than omitted — omitting it would leave the model to
 * assume there is no dead air, which is a different and worse answer.
 *
 * @returns "" when there is nothing worth saying, so the caller can skip the block
 */
export function speechDigest({ tracks, fillers } = {}) {
  const rows = (tracks || []).filter(Boolean);
  const lines = [];

  for (const track of rows) {
    const name = track.name || "audio";
    if (!track.trusted) {
      lines.push(
        `- ${name}: the quiet and loud parts could not be told apart on this ` +
          "track, so do not offer to tighten it."
      );
      continue;
    }
    lines.push(
      `- ${name}: ${secs(track.speechMs)} of speech in ${secs(track.totalMs)} ` +
        `(${Math.round((track.silentMs / (track.totalMs || 1)) * 100)}% silence)`
    );
    // ⚠ THE FIVE LONGEST, NOT ALL OF THEM. A reading has dozens of gaps and the
    // ones worth an edit are the few that stand out.
    for (const gap of (track.gaps || []).slice(0, 5)) {
      lines.push(`  · ${secs(gap.ms)} of dead air from ${secs(gap.start_ms)}`);
    }
    if ((track.gaps || []).length > 5) {
      lines.push(`  · and ${track.gaps.length - 5} shorter gap(s)`);
    }
  }

  for (const row of fillers || []) {
    lines.push(
      `- Caption at ${secs(row.start_ms)} is filler: “${row.text}” ` +
        "(removing it takes the words off the screen, not out of the audio)"
    );
  }

  if (!lines.length) return "";
  return ["SOUND ON THIS TIMELINE:", ...lines].join("\n");
}
