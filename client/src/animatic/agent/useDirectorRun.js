// useDirectorRun.js — THE PHASE MACHINE. What the 🎬 button is wired to.
//
//   closed ──open──▶ planning ──▶ preview ──run──▶ running ──▶ done
//                        │            │              │  ▲
//                        │            └──cancel──▶ closed
//                        │                        pause│  │resume
//                        │                             ▼  │
//                        └────────nothing to do───▶  paused
//                                                      │stop
//                                                      ▼
//                                                   stopped
//
// ---------------------------------------------------------------------------
// ⚠ ONE STEP PER TICK, AND THAT IS NOT DECORATION.
// ---------------------------------------------------------------------------
// The obvious way to run 61 steps is a loop. It cannot work here and the reason
// is React: every verb calls a `setState`, and the NEXT verb has to see the
// result — `add_transition` reads back the record it just made to set its
// length, `set_effect_param` reads the chain `add_effect` just appended to. In
// one synchronous loop all 61 steps would read the document as it was before any
// of them ran, and roughly half of them would quietly do nothing.
//
// So each step is scheduled after the previous one has COMMITTED, and the
// read-model is fetched fresh through `readCtx()` at the top of every step
// rather than captured once. The delay that falls out of this is the thing the
// user sees as the Director working through the film, which is worth having on
// its own — but it is not why it is there.
//
// ---------------------------------------------------------------------------
// ⚠ REVERT IS ONE SNAPSHOT, NOT 61 UNDOS.
// ---------------------------------------------------------------------------
// The document before the run is kept and handed back to `applySnapshot` — the
// same function Ctrl+Z uses. Walking the undo stack backwards 61 times would
// depend on every verb having pushed exactly one entry, which is not true and
// was never going to be: `add_text` is two edits, and the stack coalesces edits
// that land within half a second of each other (see `useUndoStack`). One
// snapshot is exact, and it is exact regardless of how the stack behaved.
//
// ⚠ AND ORDINARY CTRL+Z STILL WORKS AFTERWARDS. The run is not bracketed as a
// gesture, so the user can walk back through the Director's edits one at a time
// if they only want to lose the last few — Revert is the big hammer, not the
// only one.

import { useCallback, useEffect, useRef, useState } from "react";

import { capabilities } from "./capabilities.js";
import { describeStep, ACTIONS } from "./actions.js";
import { applyGuardrails, housePlan } from "./house_style.js";
import { emptyPlan, planTotals, validatePlan } from "./plan_schema.js";

/**
 * How long between steps.
 *
 * ⚠ NOT ZERO, and not for the animation. A `setTimeout(0)` fires before React
 * has painted, so a step would still be reading the document one edit behind.
 * 90ms is comfortably past a commit on a timeline of this size and is slow
 * enough to watch — 61 steps take about six seconds, which reads as work being
 * done rather than as a hang.
 */
const STEP_MS = 90;

export default function useDirectorRun({ readCtx, api, applySnapshot, docRef, onNotice }) {
  const [phase, setPhase] = useState("closed");
  const [plan, setPlan] = useState(emptyPlan);
  const [dropped, setDropped] = useState([]);
  const [trimmed, setTrimmed] = useState([]);
  const [log, setLog] = useState([]);
  const [index, setIndex] = useState(0);

  // The document as it was before the run — what Revert puts back.
  const snapshotRef = useRef(null);
  const [canRevert, setCanRevert] = useState(false);

  // ⚠ REFS, NOT STATE, and every one of them for the same reason: the step timer
  // is a closure created when the effect ran, and state it captured is state
  // from that render. `refs` in particular MUST survive the whole run — it is
  // how `add_text` tells `apply_text_preset` which clip it made.
  const refsRef = useRef({});
  const timerRef = useRef(null);
  const stepsRef = useRef([]);

  const clearTimer = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  useEffect(() => clearTimer, []);

  // ------------------------------------------------------------------- plan
  /**
   * Read the timeline and write a plan. Free, and nothing is touched.
   *
   * Phase 0 has one planner and it is the rules. The seam for the model is here
   * and nowhere else: `housePlan` is swapped for the call that returns the AI's
   * plan, and every line below this one — validation, the fence, the preview,
   * the runner — is already what runs a plan from anywhere.
   */
  const buildPlan = useCallback(
    (options = {}) => {
      setPhase("planning");
      const caps = capabilities();
      const ctx = { ...readCtx(), caps };
      const raw = housePlan(ctx, options);
      const checked = validatePlan(raw, caps, ctx);
      const fenced = applyGuardrails(checked.plan, ctx);
      setPlan(fenced.plan);
      setDropped(checked.dropped);
      setTrimmed(fenced.trimmed);
      setLog([]);
      setIndex(0);
      setPhase("preview");
      return { plan: fenced.plan, dropped: checked.dropped, trimmed: fenced.trimmed };
    },
    [readCtx]
  );

  const open = useCallback(() => buildPlan(), [buildPlan]);

  const close = useCallback(() => {
    clearTimer();
    setPhase("closed");
  }, []);

  // -------------------------------------------------------------------- run
  const start = useCallback(() => {
    if (!plan.steps.length) return;
    // ⚠ THE SNAPSHOT IS TAKEN HERE, not when the panel opened. Between opening
    // the preview and pressing Run the user can still edit — and reverting to
    // the document as it was before they did would throw away work the Director
    // never touched.
    snapshotRef.current = docRef.current;
    setCanRevert(false);
    refsRef.current = {};
    stepsRef.current = plan.steps;
    setLog([]);
    setIndex(0);
    setPhase("running");
  }, [plan, docRef]);

  const pause = useCallback(() => {
    clearTimer();
    setPhase((p) => (p === "running" ? "paused" : p));
  }, []);

  const resume = useCallback(() => {
    setPhase((p) => (p === "paused" ? "running" : p));
  }, []);

  const stop = useCallback(() => {
    clearTimer();
    setPhase((p) => (p === "running" || p === "paused" ? "stopped" : p));
    setCanRevert(true);
    if (onNotice) onNotice("Stopped. What it had already done is still on the timeline — Revert puts it all back.");
  }, [onNotice]);

  const revert = useCallback(() => {
    const snapshot = snapshotRef.current;
    if (!snapshot) return;
    applySnapshot(snapshot);
    setCanRevert(false);
    setPhase("preview");
    setLog([]);
    setIndex(0);
    if (onNotice) onNotice("Reverted — the timeline is exactly as it was before the Director ran.");
  }, [applySnapshot, onNotice]);

  /**
   * ONE STEP. Called by the timer effect below and by `stepOnce`.
   *
   * ⚠ A STEP THAT THROWS IS LOGGED AND THE RUN CARRIES ON. Same trade the
   * validator makes for an unknown kind, one level down: a verb that fell over
   * on one shot is one shot untreated, and stopping there would leave the film
   * half-edited with no explanation of which half.
   */
  const runStep = useCallback(
    (at) => {
      const step = stepsRef.current[at];
      if (!step) return;
      const caps = capabilities();
      const ctx = { ...readCtx(), caps };
      const action = ACTIONS[step.verb];
      const line = { id: step.id, verb: step.verb, text: describeStep(step, ctx) };
      try {
        action.run({ api, args: step.args, ctx, refs: refsRef.current });
        setLog((rows) => [...rows, { ...line, state: step.verb === "note" ? "note" : "done" }]);
      } catch (err) {
        setLog((rows) => [...rows, { ...line, state: "failed", why: err.message }]);
      }
    },
    [api, readCtx]
  );

  useEffect(() => {
    if (phase !== "running") return undefined;
    if (index >= stepsRef.current.length) {
      setPhase("done");
      setCanRevert(true);
      if (onNotice) {
        onNotice(
          `The Director made ${stepsRef.current.filter((s) => s.verb !== "note").length} edits. ` +
            "Nothing was spent — Revert puts it all back."
        );
      }
      return undefined;
    }
    runStep(index);
    timerRef.current = setTimeout(() => setIndex((i) => i + 1), STEP_MS);
    return clearTimer;
  }, [phase, index, runStep, onNotice]);

  /** One step by hand, from the panel's ▸ button while paused. */
  const stepOnce = useCallback(() => {
    if (phase !== "paused") return;
    if (index >= stepsRef.current.length) return;
    snapshotRef.current = snapshotRef.current || docRef.current;
    runStep(index);
    setIndex((i) => i + 1);
    setCanRevert(true);
  }, [phase, index, runStep, docRef]);

  return {
    phase,
    plan,
    totals: planTotals(plan),
    dropped,
    trimmed,
    log,
    index,
    canRevert,
    open,
    close,
    buildPlan,
    start,
    pause,
    resume,
    stop,
    stepOnce,
    revert,
  };
}
