// poses_pass.js — PHASE C2: THE FLIPBOOK. 🖼 Animatic images, as a tick box.
//
// ---------------------------------------------------------------------------
// ⚠ THIS IS NOT A SECOND ANIMATIC-IMAGES FEATURE. IT IS THE SAME ONE, TICKED.
// ---------------------------------------------------------------------------
// 🖼 Animatic images already exists in the timeline's tool row: press it and
// every storyboard shot on the timeline is blocked out as key poses, four
// drawings per second of its own length, onto a row of their own. Asked for
// again as a checkbox on 🎬 Make Video —
//
//   "mai chahta hun ki Animatic image buttun function make video butun mai v
//    rahe automatic … check box tik kar ke generet karne ka type so agar kisi
//    user ko Animatics image chahiye to choose … waise hi aa jaye jaise veo
//    video aata hi"
//
// — so it is wired in exactly as phase C (Veo) was: the DECISIONS live here,
// pure and testable, and the queue that spends stays where it already is, in
// `AnimaticEditor.blockOutPoses`. There is one implementation of "block a shot
// out as key poses" in this app and this file does not become a second one.
//
// ---------------------------------------------------------------------------
// ⚠ IT RUNS LAST OF THE PAID PASSES, AND THE ORDER IS THE PRODUCT.
// ---------------------------------------------------------------------------
//   B  the voiceover   STRETCHES the shot that carries a line.
//   C  the Veo pass    GROWS a shot to the length of the take over it.
//   C2 this            reads the FINAL hold of every shot and buys drawings
//                      against it — four per second — then spreads them across
//                      that shot's real span.
//
// How many drawings a shot gets is a function of how long it holds, and both
// passes above rewrite the holds. Blocked out before them, a 2-second shot buys
// its eight drawings and is then stretched to 9.3s to carry a line — eight
// drawings, each held for more than a second, which is a slideshow rather than a
// flipbook. Blocked out after them, the same shot buys thirty-six. So this goes
// last, immediately before the free re-anchor.
//
// ---------------------------------------------------------------------------
// ⚠ A SHOT THAT IS BEING RENDERED WITH VEO IS NOT BLOCKED OUT, AND IT SAYS SO.
// ---------------------------------------------------------------------------
// A Veo take lands on `board_video`, which sits ABOVE `board_poses` — see
// `ROW_KINDS` in the editor. So key poses drawn under a take are drawings
// nobody will ever see, bought with the user's image quota, on a run where they
// also paid for the footage. Both boxes ticked is a perfectly reasonable thing
// to ask for on a MIXED film (render the four shots that move, block the rest
// out), and it is the shot-by-shot overlap that is waste — so the overlap is
// what is dropped, by name, with the reason on screen. See `poseWork`.
//
// ⚠ AND "ALREADY DRAWN" IS NOT A SKIP. A shot whose poses are already on the
// storyboard still goes through the queue: `submit_sequence_run(resume=True)`
// draws nothing new and charges nothing, and the pass still has to LAY the
// drawings onto this timeline. Dropping it here would leave a shot the panel
// promised and the row never received. What "already drawn" changes is the
// PRICE, and only the price — `poseTally` counts it out of `toDraw`.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE. No React, no fetch, no editor import.
// ---------------------------------------------------------------------------
// Same rule as the other passes in this folder. `tests/director_poses_check.py`
// imports it under bare node and drives every decision in it with no browser, no
// backend and not one image generated.

/** The include flag phase C2 answers to. One name, and it is in `INCLUDE_KEYS`. */
export const POSES_KEY = "poses";

/**
 * IS THIS CLIP A KEY POSE RATHER THAN A SHOT?
 *
 * ⚠ THE SAME DERIVATION THE EDITOR MAKES (`clipRowKind` asks `src.kind ===
 * "pose"`), restated here in two lines rather than imported, for the reason
 * `isTake` states one file over: every module in this folder has to load under
 * bare node, and `scene.js` drags the whole editor in behind it.
 *
 * ⚠ AND `shotRow` READS IT, WHICH IS THE WHOLE POINT OF IT BEING HERE. A
 * 2-second shot blocked out is EIGHT more clips on the picture list, so a film
 * of eight panels that has been through this pass hands the Director 136 shots
 * unless they are filtered out — `housePlan` would take the median of a list
 * that is 94% drawings, `shotIndex` would accept "shot 120", and the preview
 * table would draw a row per drawing. Exactly the bug `isTake` was written for,
 * one row further down the stack.
 */
export function isPose(frame) {
  if (!frame) return false;
  return Boolean(frame.src) && frame.src.kind === "pose";
}

/**
 * WHAT THE PASS WOULD BUY — the shot list, with the overlap taken out.
 *
 * @param shots    `[{ frameId, label, holdMs, seconds, poses, have }]` — the
 *                 editor's own `posesShots`, priced by one free read per shot.
 *                 This module does not decide WHICH clips are board panels or
 *                 how many drawings a length is worth; the editor does, once,
 *                 and `KEY_POSES_PER_SECOND` lives there beside its server twin.
 * @param rendered frame ids that carry a Veo take, or are about to get one on
 *                 this very run. See the header.
 * @returns {{ shots, skipped }} — `skipped` is `[{ label, why }]`, printed in
 *          the panel under the tick box.
 */
export function poseWork({ shots, rendered } = {}) {
  const covered = rendered instanceof Set ? rendered : new Set(rendered || []);
  const keep = [];
  const skipped = [];
  for (const shot of shots || []) {
    if (!shot || !shot.frameId) continue;
    if (covered.has(shot.frameId)) {
      skipped.push({
        label: shot.label || shot.frameId,
        why:
          `${shot.label || "this shot"} is being rendered with Veo, and the take ` +
          "sits over the drawings — blocking it out would buy pictures nobody sees",
      });
      continue;
    }
    keep.push(shot);
  }
  return { shots: keep, skipped };
}

/**
 * THE NUMBERS THE PANEL AND THE BUTTON BOTH SAY.
 *
 * ⚠ `toDraw` IS THE ONE THAT IS SPENT, and it is the only number that may ever
 * appear next to the word "images" on the Run button. `drawings` is what the
 * film is worth in key poses; `already` is what the storyboard has and will not
 * charge for a second time. Quoting `drawings` would price a bill nobody is
 * going to be sent — the same lie the 🖼 dialog was careful not to tell, so the
 * arithmetic is shared with it rather than written out twice.
 *
 * ⚠ THE SHOT COUNT IS CALLED `count`, NOT `shots`, AND THAT IS DELIBERATE.
 * Every caller of this spreads it over an object that already holds the shot
 * LIST under the name `shots` — `{ ...work, ...poseTally(work.shots) }` — and a
 * key collision there would silently replace an array of shots with the number
 * 8. Nothing downstream would throw; the panel would simply render nothing and
 * the pass would find no work.
 */
export function poseTally(shots) {
  const list = shots || [];
  let drawings = 0;
  let already = 0;
  for (const shot of list) {
    drawings += Math.max(0, Number(shot && shot.poses) || 0);
    already += Math.max(0, Number(shot && shot.have) || 0);
  }
  return {
    count: list.length,
    drawings,
    already,
    toDraw: Math.max(0, drawings - already),
  };
}

/**
 * IS THERE A BLOCKING-OUT PASS TO RUN AT ALL?
 *
 * Returns a reason rather than a boolean, exactly as `veoDue` and `speechDue`
 * do and for the same reason: each way of answering "no" is a different thing to
 * tell the user, and the panel prints it verbatim under the tick box.
 */
export function posesDue(include, shots) {
  if (include && include[POSES_KEY] === false) {
    return { due: false, why: "Animatic images are switched off for this run." };
  }
  if (!shots || !shots.length) {
    return {
      due: false,
      why:
        "There are no storyboard shots left to block out — key poses are drawings " +
        "OF a panel, so there has to be a panel behind them.",
    };
  }
  return { due: true, why: "" };
}
