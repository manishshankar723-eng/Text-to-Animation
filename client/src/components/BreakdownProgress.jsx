// Inline progress state shown INSIDE the form card while the script breakdown
// (and, before it, the script writer) runs — a gold progress ring with a step
// label.
//
// ⚠ ONE RING, ONE CLIMB, FOR THE WHOLE WAIT. Approving a concept runs TWO calls
// back to back — `write_script()` then the breakdown — and the ring must not
// notice. It is mounted ONCE and its props change under it; the number only
// ever goes up, and it reaches 100 exactly once, when the review step is ready.
//
// THREE ROUNDS OF REPORTS SHAPED THE MOTION, AND EACH ONE UNDID PART OF THE
// LAST. Worth reading before touching the constants:
//
//   1. "ye stuck lag raha hai" — the ORIGINAL fill was flat to a soft cap of 96,
//      crawled to 99, and STOPPED DEAD. A 45-second call left a motionless
//      "99%" on screen for half a minute. 99% does not read as working; it
//      reads as finished-and-broken.
//
//   2. "progress bar pehle 100% ho jaye fir kuch time pe open ho" — each of the
//      two calls drove its OWN ring from 0 to 100, so the user watched a bar
//      finish and then watched a second one start from zero.
//
//   3. "kabhi fast kabhi slow … last mein laga 100 gaya hi nahi aur open ho
//      gaya" — the fix for (2) gave each call its own SLICE of the bar (0-50,
//      50-100). That was worse in a way that is obvious in hindsight: a ring
//      approaching 50 crawls at half the speed of one approaching 100, and each
//      slice ENDED with a half-second sprint to its own ceiling. Slow, jump,
//      slow, jump. And the sprint at the end handed off on the same frame it
//      touched 100 — before React had painted it — so 100 was never actually
//      seen.
//
// WHAT IT DOES NOW, and why each part is the answer to one of those:
//
//   • ONE curve for the whole wait, with no per-phase ceilings. The rate is
//     proportional to the distance still to go, so it is quick off the mark and
//     decelerates smoothly for ever — never a wall (1), never a slice boundary
//     to jump across (3).
//   • A call finishing mid-wait hands off WHERE IT IS (`final={false}`). No
//     sprint, no reset, no visible seam — the next call just carries on from the
//     same number (2, 3).
//   • Only the LAST call sweeps to 100, and the ring PAINTS 100 before handing
//     over (3). The hold is a fifth of a second: long enough to see the number
//     land, short enough that nobody is waiting on it.
import { useEffect, useRef, useState } from "react";

// Each step owns a slice of the bar, so the label changes exactly as the ring
// passes that mark. `until` is the upper % bound of the slice. Both sets are
// written against the whole 0-100 climb, so swapping one for the other
// mid-wait changes the words without moving the ring.
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

// ⚠ AN EXPONENTIAL APPROACH, NOT A CONSTANT CLIMB. The work behind this ring is
// a model call with no progress signal at all, so any percentage is an estimate
// — and the honest way to draw an estimate is one that slows as it gets less
// sure. Rate is proportional to the distance still to go: quick off the mark,
// unhurried in the middle, still creeping a minute in, and never hitting a wall.
//
// Roughly: ~18% at 5s, 44% at 15s, 65% at 25s, 81% at 40s, 90% at 60s.
const APPROACH_SECONDS = 22;
// Where the climb aims while the work is still running. Held back from 100 on
// purpose, and by enough to be visible: parking on "99%" reads as finished, and
// the closing sweep needs somewhere to sweep FROM.
const SOFT_TARGET = 96;
// The closing sweep takes about this long FROM WHEREVER IT IS — the rate is
// worked out ONCE, on the frame the work lands, and then held. Recomputing it
// every frame makes it an exponential decay instead, which drags a fast call
// out to a second and a half of watching a bar fill after the work is done.
const FINISH_SECONDS = 0.45;
const MIN_FINISH_RATE = 12; // %/sec — a very short sweep still has to be seen
// ⚠ THE RING PAINTS 100 BEFORE IT HANDS OVER. Firing on the same frame the
// number reaches 100 means React never renders it — reported as "laga 100 gaya
// hi nahi aur open ho gaya". A fifth of a second is the completion, not a wait.
const SHOW_100_MS = 220;
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
  // ⚠ IS `done` THE END OF THE WHOLE WAIT, OR JUST OF THIS CALL? False means
  // another call follows: hand off immediately, leave the ring where it is, and
  // keep climbing. Only the final call earns the sweep to 100.
  final = true,
}) {
  const [pct, setPct] = useState(0);
  const [slow, setSlow] = useState(false); // long wait → reassurance sub-line
  const progress = useRef(0);
  // Read the latest props inside the rAF loop without restarting it.
  const doneRef = useRef(done);
  const finalRef = useRef(final);
  const onDoneRef = useRef(onDone);
  // Whether THIS call has already been handed off. Cleared when `done` goes
  // back to false, which is what starting the next call looks like from here.
  const firedRef = useRef(false);
  const finishRate = useRef(null);

  useEffect(() => {
    doneRef.current = done;
    if (!done) {
      firedRef.current = false;
      finishRate.current = null;
      setSlow(false); // a new call gets its own patience before we apologise
    }
  }, [done]);
  useEffect(() => {
    finalRef.current = final;
  }, [final]);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useEffect(() => {
    // Reassure on a genuinely long wait (cancelled the moment work completes).
    const t = setTimeout(() => {
      if (!doneRef.current) setSlow(true);
    }, LONG_WAIT_MS);
    return () => clearTimeout(t);
  }, [done]);

  useEffect(() => {
    let raf;
    let handOff;
    let last = performance.now();

    const tick = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000); // clamp after a tab stall
      last = now;
      const p = progress.current;

      // ⚠ A CALL THAT IS NOT THE LAST ONE HANDS OFF WHERE IT STANDS. No sprint
      // to a phase ceiling: the next call carries on from this same number, so
      // there is no seam to see. This is the whole of complaint (3).
      if (doneRef.current && !finalRef.current && !firedRef.current) {
        firedRef.current = true;
        onDoneRef.current?.();
      }

      if (doneRef.current && finalRef.current) {
        if (finishRate.current === null) {
          finishRate.current = Math.max(
            MIN_FINISH_RATE,
            (100 - p) / FINISH_SECONDS
          );
        }
        progress.current = Math.min(100, p + finishRate.current * dt);
      } else {
        // Approach a target short of 100. The rate falls with the distance, so
        // this decelerates for ever and never actually arrives.
        const rate = Math.max(0, (SOFT_TARGET - p) / APPROACH_SECONDS);
        progress.current = Math.min(SOFT_TARGET, p + rate * dt);
      }
      setPct(progress.current);

      if (
        doneRef.current &&
        finalRef.current &&
        progress.current >= 99.99 &&
        !firedRef.current
      ) {
        firedRef.current = true;
        // Let the ring RENDER 100 before the screen changes under it.
        handOff = setTimeout(() => onDoneRef.current?.(), SHOW_100_MS);
        return; // stop the loop; this ring's work is over
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(handOff);
    };
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
            strokeDasharray={CIRC}
            strokeDashoffset={offset}
            strokeWidth={STROKE}
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
