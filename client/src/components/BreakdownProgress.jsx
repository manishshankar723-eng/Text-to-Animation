// Inline progress state shown INSIDE the form card while the (single-call)
// script breakdown runs — a gold progress ring with a step label.
//
// The ring is an HONEST wait indicator, not a fake timer:
//   • While the real API call is in flight (`done` = false) it fills at a steady
//     pace but HOLDS at 90% — it must never claim 100% before the work is done.
//   • When the call returns (`done` = true) it races the rest of the way to
//     100%, and only THEN calls `onDone`, which advances to Review.
// So the number, the ring and the label always reach 100% together, every time,
// regardless of how long the call actually took.
import { useEffect, useRef, useState } from "react";

// Each step owns a slice of the bar, so the label changes exactly as the ring
// passes that mark. `until` is the upper % bound of the slice.
const STEPS = [
  { until: 20, label: "Reading your story…" },
  { until: 40, label: "Aligning it with the genre…" },
  { until: 60, label: "Identifying the characters…" },
  { until: 80, label: "Mapping out each scene…" },
  { until: 100, label: "Creating the scene breakdown…" },
];

// Writing the script from an APPROVED concept is the same shape of wait — one
// call, no progress signal — so it wears the same ring rather than a second,
// slightly-different spinner invented for it. Only the words change.
export const SCRIPT_STEPS = [
  { until: 25, label: "Building the story structure…" },
  { until: 50, label: "Breaking it into scenes…" },
  { until: 75, label: "Writing the action and dialogue…" },
  { until: 100, label: "Formatting it for the breakdown…" },
];

function stepFor(pct, steps) {
  const i = steps.findIndex((s) => pct < s.until);
  return i === -1 ? steps.length - 1 : i;
}

// SVG ring geometry.
const SIZE = 150;
const STROKE = 10;
const R = (SIZE - STROKE) / 2;
const CIRC = 2 * Math.PI * R;

// The breakdown is a single AI call with NO real progress signal, so the % is an
// estimate. The motion that reads best for an unknown wait: ONE calm, constant
// speed for the whole climb (no rush at the start — that's what looked "stuck"),
// then a quick, even finish once the work is actually done (speeding up at the
// end signals completion, which feels right).
//
//   • Not done: fill at a steady FILL_RATE up to SOFT_CAP — a single, even pace.
//   • Past SOFT_CAP (only if the call runs longer than ~the expected time): a
//     gentle creep to HARD_CAP so it never freezes, but never reaches 100.
//   • Done: sweep from wherever it is to 100 at FINISH_RATE, then hand off.
const SOFT_CAP = 96;       // one even speed carries the ring almost all the way
const HARD_CAP = 99;       // ...only outliers reach the slow tail beyond SOFT_CAP
const FILL_RATE = 6.5;     // %/sec — calm and even; 0→96% over ~15s
const CRAWL_RATE = 0.6;    // %/sec past SOFT_CAP (rare; keeps it from freezing)
const FINISH_RATE = 34;    // %/sec once done — a quick, satisfying completion
const HOLD_AT_100_MS = 300; // let the eye register 100% before leaving
const LONG_WAIT_MS = 16000; // after this, reassure that a long wait is normal

export default function BreakdownProgress({
  done = false,
  onDone,
  // Both default to the breakdown's own wording, so every existing call site
  // reads exactly as it did before this became reusable.
  steps = STEPS,
  title = "Generating your scene breakdown",
  readyLabel = "Scene breakdown ready!",
  slowLabel = "Still working — longer scripts take a little more time…",
}) {
  const [pct, setPct] = useState(0);
  const [slow, setSlow] = useState(false); // long wait → reassurance sub-line
  const progress = useRef(0);
  // Read the latest done/onDone inside the rAF loop without restarting it.
  const doneRef = useRef(done);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    doneRef.current = done;
  }, [done]);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useEffect(() => {
    // Reassure on a genuinely long wait (cancelled the moment work completes).
    const t = setTimeout(() => {
      if (!doneRef.current) setSlow(true);
    }, LONG_WAIT_MS);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    let raf;
    let fired = false;
    let last = performance.now();

    const tick = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000); // clamp after a tab stall
      last = now;

      const p = progress.current;
      let cap;
      let rate;
      if (doneRef.current) {
        cap = 100;
        rate = FINISH_RATE;
      } else if (p < SOFT_CAP) {
        cap = SOFT_CAP;
        rate = FILL_RATE;
      } else {
        cap = HARD_CAP; // keep creeping so it never looks frozen
        rate = CRAWL_RATE;
      }
      progress.current = Math.min(cap, p + rate * dt);
      setPct(progress.current);

      if (doneRef.current && progress.current >= 100 && !fired) {
        fired = true;
        // Show a full ring for a beat, then hand off to Review.
        setTimeout(() => onDoneRef.current?.(), HOLD_AT_100_MS);
        return; // stop the loop; component is about to unmount
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const stepIdx = stepFor(pct, steps);
  const atFull = pct >= 99.5;
  const offset = CIRC * (1 - pct / 100);

  return (
    <div className="card bp-inline">
      <div className="bp-ring" style={{ width: SIZE, height: SIZE }}>
        <svg width={SIZE} height={SIZE} className="bp-ring-svg">
          <circle
            className="bp-ring-track"
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            strokeWidth={STROKE}
          />
          <circle
            className="bp-ring-arc"
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            strokeWidth={STROKE}
            strokeDasharray={CIRC}
            strokeDashoffset={offset}
          />
        </svg>
        <div className="bp-ring-inner">
          <span className="bp-ring-pct">{Math.round(pct)}%</span>
          <span className="bp-ring-label">{atFull ? "ready" : "building"}</span>
        </div>
      </div>

      <h3 className="bp-inline-title">{title}</h3>
      <p
        className="bp-inline-step"
        key={atFull ? "done" : slow && !done ? "slow" : stepIdx}
      >
        {atFull
          ? readyLabel
          : slow && !done
            ? slowLabel
            : steps[stepIdx].label}
      </p>

      <div className="bp-dots">
        {steps.map((_, i) => (
          <span
            key={i}
            className={`bp-dot2 ${atFull || i === stepIdx ? "on" : ""}`}
          />
        ))}
      </div>
    </div>
  );
}
