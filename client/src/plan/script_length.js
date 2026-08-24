// script_length.js — how long a calendar row's script should be.
//
// A calendar row already says how long the video is: that is what its `format`
// field IS ("YouTube Short (45s)", "Long-form (8-10 min)"). Asking the creator
// to restate it in a dropdown next to every one of thirty-six cards would be
// asking them to retype what they are looking at, so the button reads it here.
//
// ⚠ THIS IS A DEFAULT, NOT A CONSTRAINT. The number is sent to the server as a
// plain integer and the "Write a script" box can ask for any length; nothing
// downstream knows or cares that this function chose it.
//
// Pure — no React, no DOM — so node can import it and tests/plan_script_check.py
// can drive it directly. Same reason lane_order.js is pure. Keep it that way.

// Formats that name no duration at all. Ordered longest-match-first is not
// needed here because the tests are mutually exclusive in practice, but the
// duration patterns above them always win — an explicit "45s" beats the word
// "short" in the same string.
const SHORT_FORMS = /short|reel|tiktok|vertical/;
const LONG_FORMS = /long/;
const STREAM_FORMS = /livestream|stream|podcast/;

// The least wrong answer for a format nobody wrote a duration into.
export const DEFAULT_SECONDS = 60;

export function secondsFromFormat(format) {
  const text = String(format || "").toLowerCase();

  // A RANGE FIRST — "8-10 min" must not be read as "8 min" by the single-value
  // pattern below it, which is what happens if the order is flipped. Takes the
  // midpoint, because a creator who wrote a range meant "about here".
  const range = text.match(/(\d+)\s*[-–—]\s*(\d+)\s*m/);
  if (range) return Math.round(((+range[1] + +range[2]) / 2) * 60);

  const minutes = text.match(/(\d+(?:\.\d+)?)\s*m(?:in|ins|inute|inutes)?\b/);
  if (minutes) return Math.round(parseFloat(minutes[1]) * 60);

  const seconds = text.match(/(\d+)\s*s(?:ec|ecs|econd|econds)?\b/);
  if (seconds) return +seconds[1];

  if (SHORT_FORMS.test(text)) return 45;
  if (LONG_FORMS.test(text)) return 480;
  if (STREAM_FORMS.test(text)) return 900;
  return DEFAULT_SECONDS;
}

// "45s", "1m 30s", "8m". Used on the write button so it says which length it
// is about to spend on, rather than deciding silently.
export function formatRuntime(seconds) {
  const n = Number(seconds) || 0;
  if (n < 60) return `${n}s`;
  const m = Math.floor(n / 60);
  const s = n % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}
