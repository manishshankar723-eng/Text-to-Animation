// veo_pass.js — PHASE C: THE FOOTAGE. Chunked, resumable, and it spends the most.
//
// ---------------------------------------------------------------------------
// ⚠ THIS IS THE MOST EXPENSIVE THING THE 🎬 BUTTON CAN DO, BY AN ORDER OF
// MAGNITUDE.
// ---------------------------------------------------------------------------
// Phase B reads a dozen lines aloud for cents. Phase C renders every shot in the
// film with Veo, and a 48-shot board at the project's default settings is
// roughly $46. So every rule in this file is about one of three things: knowing
// the price before it is paid, never paying it twice, and being able to stop.
//
//   · THE QUOTE IS THE SUM OF THE PASSES, never a second calculation. See
//     `_quote_veo_shots` in `server/director.py` — the number under the table and
//     the numbers on the rail are the same arithmetic, and `director_chunk_check`
//     asserts they add up exactly.
//   · A SHOT THAT ALREADY HAS A TAKE IS NOT RENDERED. It is skipped, said out
//     loud, and it costs nothing. `_animate_targets` refuses it server-side too,
//     so this is the polite half of a rule that is enforced anyway.
//   · A RUN THAT DIED HALFWAY RESUMES FROM WHAT IS ON THE SERVER, not from
//     anything the browser remembered. See `outstanding` below.
//
// ---------------------------------------------------------------------------
// ⚠ IT RUNS IN PASSES OF `MAX_VIDEO_BATCH`, AND THE STOP IS BETWEEN THEM.
// ---------------------------------------------------------------------------
// `POST /animatics/{id}/animate` caps a submission at `config.MAX_VIDEO_BATCH`
// (12) — a spend guard, not a technical one — so a 48-shot film is four
// submissions, not one. That shape is not a workaround; it is the only place in
// the whole run where stopping is honest:
//
//     pass 1  [12 shots]  --submitted, paid, polled to the end-->  STOP HERE?
//     pass 2  [12 shots]  --submitted, paid, polled to the end-->  STOP HERE?
//     pass 3  [12 shots]  ...
//
// ⚠ THERE IS NO STOPPING MID-PASS AND THE BUTTON MUST NOT PRETEND THERE IS. Once
// a submission has gone, twelve renders are being billed whatever the browser
// does next — closing the tab does not un-spend them, and a "Stop" that dropped
// the poll would only lose the clips the user had already bought. So Stop is
// read BETWEEN passes and nowhere else, and while a pass is in flight the panel
// says what it is actually able to do: nothing but wait.
//
// ---------------------------------------------------------------------------
// ⚠ THE LENGTH POLICY: THE TAKE COVERS THE HOLD, AND THE SHOT GROWS TO IT.
// ---------------------------------------------------------------------------
// Veo renders 4, 6 or 8 seconds — a fixed menu, not a number. A shot holding
// 2.4s therefore cannot have a take its own length, and something has to give.
// The rule here is the one the editor already keeps in two other places:
//
//     hold 2.4s  ->  ask for 4s  ->  the shot GROWS to 4.0s
//     hold 5.0s  ->  ask for 6s  ->  the shot GROWS to 6.0s
//     hold 9.3s  ->  ask for 8s  ->  the shot KEEPS 9.3s, still tail at the end
//
// `spreadPanelsForRenders` says it in its own ⚠ ("the panel takes the take's
// length … it only ever grows") and `_lay_out_speech` does the same thing for a
// spoken line. A third answer here — trimming the take back to the hold — would
// be cheaper to reason about and would throw away footage that has been paid
// for, which is the one thing this feature must never do quietly.
//
// ⚠ AND THE SMALLEST LENGTH THAT COVERS, NOT THE NEAREST. Rounding a 4.6s hold
// down to a 4s take leaves six tenths of a second of the DRAWING at the end of
// the shot — a pop back to a storyboard panel, mid-scene, that reads as a bug.
// Covering costs more on some shots and it is the only choice that never does
// that.
//
// ⚠ WHICH IS WHY PHASE C RUNS AFTER PHASE B. The length is chosen from the hold,
// and the voiceover rewrites the holds: a shot stretched to 9.3s to carry its
// line wants the 8-second take, and the same shot priced before the pass would
// have been given 4. Sound, then footage, then ONE re-anchor over both — see
// `useDirectorRun`'s header.
//
// ---------------------------------------------------------------------------
// ⚠ A TAKE IS NOT A SHOT, AND THE WHOLE FILE TURNS ON THAT SENTENCE.
// ---------------------------------------------------------------------------
// `attachVeoClip` appends the finished take to `frames` as an ordinary clip on
// the `board_video` row. So the moment phase C lands, the Director's read-model
// holds 48 panels AND 48 takes — and every rule downstream that counts shots
// would be reading a 96-shot film that does not exist. `housePlan` would take
// the median of a list half of which is footage; `shotIndex` would accept "shot
// 61"; the preview table would list every panel twice.
//
// `shotRow` is the answer and it is applied at the editor's own `readCtx`, so
// there is exactly ONE definition of what the Director means by a shot. It also
// fixes the same bug one run earlier than phase C: a 🎬 run on a project the
// user had already animated by hand was reading its own takes as shots.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE. No React, no fetch, no editor import.
// ---------------------------------------------------------------------------
// Same rule as the other five modules here. `tests/director_chunk_check.py` and
// `tests/director_resume_check.py` import this under node and drive the chunking,
// the pricing shape and the whole resume decision with no browser, no backend
// and no dollar. The runner owns the async — the submission, the poll, the
// hard stop — and this owns every decision it leads to.

import { shiftsOf } from "./voice_pass.js";
// ⚠ ONE DIRECTION ONLY: `poses_pass.js` imports nothing from here. `shotRow`
// is the film as the Director counts it and a KEY POSE is no more a shot than
// a take is — see `isPose` for what goes wrong when it is counted as one.
import { isPose } from "./poses_pass.js";

/** The include flag phase C answers to. One name, and it is in `INCLUDE_KEYS`. */
export const VEO_KEY = "veo";

/**
 * The lengths Veo will actually render, shortest first.
 *
 * ⚠ A MENU, NOT A RANGE, and `RenderSettings.duration_seconds` says so on the
 * server ("4, 6 or 8"). Asking for 5 is not a slightly-wrong request, it is a
 * rejected one — which would be a paid failure if it ever reached the API.
 */
export const VEO_LENGTHS = [4, 6, 8];

/**
 * How far past a hold a take may fall short before the still shows through.
 *
 * ⚠ NOT ZERO, for the same reason `MOVED_MS` is not zero one file over: holds
 * are whole milliseconds that came out of a layout pass, so a shot the user set
 * to "four seconds" can be 4001ms. Rounding that up to a six-second take would
 * cost half as much again for one millisecond nobody can see.
 */
const COVER_SLACK_MS = 60;

/**
 * IS THIS CLIP A VEO TAKE RATHER THAN A SHOT?
 *
 * ⚠ THE SAME DERIVATION `isVeoRender` MAKES IN `scene.js`, restated here in two
 * lines rather than imported, because every module in this folder has to load
 * under bare node — `scene.js` is the editor's own geometry and drags a great
 * deal of the app in behind it. The rule it restates is one sentence: a clip
 * that came off the storyboard AND is video is a render of a panel.
 *
 * If `scene.js` ever changes what `board_video` means, this changes with it —
 * `tests/director_chunk_check.py` pins the two together against real clip shapes.
 */
export function isTake(frame) {
  if (!frame) return false;
  return Boolean(frame.src && frame.src.storyboard_id) && (frame.kind || "image") === "video";
}

/**
 * IS THIS CLIP SOMETHING THE DIRECTOR MADE *OF* A SHOT, RATHER THAN A SHOT?
 *
 * ⚠ TWO KINDS OF DERIVED PICTURE SIT OVER THE BOARD'S STILLS, and neither is a
 * shot: a Veo TAKE on `board_video` (`isTake`) and a KEY POSE on `board_poses`
 * (`isPose`). Both are clips on the picture list, both reference the panel they
 * came from, and both would be counted as shots by anything that simply reads
 * `frames`.
 *
 * ⚠ THE POSE HALF WAS MISSING UNTIL 🖼 ANIMATIC IMAGES BECAME A 🎬 PASS, and
 * it was already wrong before that — a user who pressed 🖼 and then 🎬 was
 * handing the Director eight panels and a hundred and twenty-eight drawings.
 * A pose run is FOUR CLIPS PER SECOND, so it does not merely inflate the count
 * the way a take does, it drowns it.
 */
function isDerived(frame) {
  return isTake(frame) || isPose(frame);
}

/**
 * THE FILM AS THE DIRECTOR COUNTS IT — the shots, with the takes and the key
 * poses taken out.
 *
 * ⚠ `starts` IS FILTERED AT THE SAME INDICES, never recomputed. The editor's
 * `starts` come from `frameSpans`, which knows about tracks, explicit
 * `start_ms`, and clips that were dragged; re-deriving them from the filtered
 * list would be a second layout engine that disagrees with the timeline the user
 * is looking at the moment anything is out of list order.
 *
 * Returns the SAME arrays when nothing was filtered, so a caller can tell
 * whether this did anything.
 */
export function shotRow(frames, starts) {
  const list = frames || [];
  const at = starts || [];
  if (!list.some(isDerived)) return { frames: list, starts: at };
  const keptFrames = [];
  const keptStarts = [];
  list.forEach((frame, i) => {
    if (isDerived(frame)) return;
    keptFrames.push(frame);
    keptStarts.push(at[i] ?? 0);
  });
  return { frames: keptFrames, starts: keptStarts };
}

/**
 * THE LENGTH TO ASK VEO FOR, given what the shot holds.
 *
 * The smallest of `VEO_LENGTHS` that covers the hold; the longest when nothing
 * does. See the header for why covering rather than rounding, and why the
 * overshoot is the shot's problem rather than the take's.
 */
export function coverSeconds(holdMs) {
  const ms = Math.max(0, Number(holdMs) || 0);
  for (const seconds of VEO_LENGTHS) {
    if (seconds * 1000 + COVER_SLACK_MS >= ms) return seconds;
  }
  return VEO_LENGTHS[VEO_LENGTHS.length - 1];
}

/**
 * ⚠ THE FLOOR ON SLOWING A TAKE DOWN TO COVER ITS SHOT.
 *
 * Below this the footage stops reading as footage and starts reading as
 * slow-motion nobody asked for — and a shot holding more than twice its take is
 * not a rounding problem, it is a hold the take was never meant to fill.
 */
const MIN_FIT_SPEED = 0.5;

/** Under this a re-fit is not worth writing: a 2% change nobody can see. */
const FIT_SLACK_MS = 150;

/**
 * HOW TO PLAY A TAKE THAT IS SHORTER THAN THE SHOT IT LANDS ON.
 *
 * ⚠ THIS IS THE 8-SECOND-TAKE-ON-A-9.8-SECOND-SHOT CASE, AND IT IS NOT RARE.
 * `coverSeconds` asks for the shortest take that COVERS the hold — but Veo's
 * menu stops at 8 seconds, and the voiceover routinely stretches a shot past
 * that to cover the line spoken over it. So the take runs out, the clip freezes
 * on its last frame, and the film sits on a still for the rest of the shot while
 * the dialogue carries on. Reported as "my clip is 9.8s but the video generated
 * is 8s — adjust the speed of the video according to the voiceover".
 *
 * The fix is the one a person would make by hand: play the take slower so it
 * lasts exactly as long as the shot. `speed` widens the SOURCE window read
 * inside a fixed timeline length (see `sourceAt` in `scene.js`), so a take of
 * `takeMs` covering a hold of `holdMs` runs at `takeMs / holdMs` — 0.82 for the
 * case above, which is imperceptible.
 *
 * Returns `{ speed, durationMs, why }`, or null when the take should be left
 * exactly as it is — which is most of the time:
 *
 *   · the take already covers the hold (the ordinary case — the SHOT grows to
 *     the take instead, which is `spreadPanelsForRenders`' job, not this one)
 *   · the difference is under `FIT_SLACK_MS` and nobody could see it
 *   · the hold is more than twice the take, where slowing it down far enough
 *     would look worse than the freeze it is replacing
 *
 * Pure, and deliberately not in the editor: `tests/veo_speed_fit_check.py`
 * drives it under node with no browser.
 */
export function fitTakeToHold(takeMs, holdMs) {
  const take = Math.round(Number(takeMs) || 0);
  const hold = Math.round(Number(holdMs) || 0);
  if (take <= 0 || hold <= 0) return null;
  if (hold <= take + FIT_SLACK_MS) return null;
  const speed = take / hold;
  if (speed < MIN_FIT_SPEED) return null;
  return {
    // Rounded to four places: `AnimaticFrame.speed` is a float the user can see
    // and edit in the Properties pane, and 0.8163265306122449 in a number box is
    // a value nobody would ever have typed.
    speed: Math.round(speed * 10000) / 10000,
    durationMs: hold,
    why: `the take is ${(take / 1000).toFixed(1)}s over a ${(hold / 1000).toFixed(1)}s shot`,
  };
}

/**
 * THE MOTION PROMPTS THE FREE PLANNER RENDERS FROM — the board's own words.
 *
 * ⚠ THIS IS THE ANSWER TO "I TICKED VEO ON THE RHYTHM PLAN AND NOTHING CAME
 * BACK". `housePlan` writes no words — that is its whole discipline, see the
 * header of `house_style.js` — so it wrote no motion prompts either, and phase C
 * had nothing to render: the tick box was live, the panel said the plan was
 * free, Run applied two camera moves and no footage was ever asked for. Reported
 * as "i uncheck all keep only veo check mark and generate so video generated but
 * not come in layer".
 *
 * ⚠ AND IT STILL INVENTS NOTHING. The prompt is the DESCRIPTION THE SHOT WAS
 * DRAWN FROM, read off the storyboard by `GET /animatics/{id}/panels` — the same
 * sentence ✨ Animate opens its prompt box on (`boardDraftPrompt`), so the two
 * ways of animating one panel ask Veo for the same thing. Arithmetic chooses
 * WHICH shots and HOW LONG; the board says what is in them.
 *
 * ⚠ A SHOT WITH NO WORDING IS LEFT PROMPTLESS ON PURPOSE, not dropped silently.
 * `veoShots` refuses a blank prompt — Veo bills for one exactly as it bills for
 * a good one — and prints "shot 3 has no motion prompt" under the table, which
 * is the only way a user learns that an uploaded still is not a board shot.
 * Dropping it here would leave them with a shorter list and no reason for it.
 *
 * ⚠ TAKES AND COLOUR CARDS ARE NOT OFFERED AT ALL. A take is already footage
 * (`isTake`) and a colour card has nothing to animate; neither is a missing
 * prompt, so neither earns a line of explanation.
 *
 * @param frames the SHOT ROW (see `shotRow`)
 * @param said   `[{ frame_id, description }]` from the panels read, or nothing
 * @returns `[{ shot, prompt, dialogue }]` — the shape `veoShots` reads
 *
 * Pure: `tests/director_house_veo_check.py` drives it under node.
 */
export function housePrompts(frames, said) {
  const wording = new Map(
    (said || []).filter((row) => row && row.frame_id).map((row) => [row.frame_id, row])
  );
  const out = [];
  (frames || []).forEach((frame, i) => {
    // ⚠ A KEY POSE IS REFUSED HERE TOO, not only by `shotRow`. This is normally
    // handed a row that has already been filtered — but it is exported, the free
    // planner calls it, and a drawing quoted as a shot to animate is money.
    if (!frame || isTake(frame) || isPose(frame)) return;
    const kind = (frame.src && frame.src.kind) || "";
    if (kind === "video" || kind === "color") return;
    const board = String((wording.get(frame.id) || {}).description || "").trim();
    // The wording a GENERATED in-between shot carries on itself. It has no panel
    // to be read off the board, and `src.prompt` is what it was drawn from —
    // the same fallback `openAnimate` makes for the same clip.
    const own = String((frame.src && frame.src.prompt) || "").trim();
    out.push({ shot: i + 1, prompt: board || own, dialogue: "" });
  });
  return out;
}

/**
 * WHAT PHASE C WOULD RENDER — the motion prompts, resolved onto real clips.
 *
 * @param veo    `[{ shot, prompt, dialogue }]` from the plan response
 * @param frames the SHOT ROW (see `shotRow`), after phase B has re-laid it
 * @param done   frame ids that already have a paid, ready take
 * @returns {{ shots, skipped }} — `skipped` is `[{ shot, why }]`, shown in the panel
 *
 * ⚠ EVERY REFUSAL THE SERVER WOULD MAKE IS MADE HERE FIRST, WITH THE REASON ON
 * SCREEN. `_animate_targets` drops a promptless frame, a frame that is not
 * findable and a frame that already has a clip — silently, because it is
 * building a work list rather than explaining itself. A user who was shown a
 * price for 48 shots and got 41 renders needs to know which seven and why, and
 * needs to know it BEFORE the button, which is the only moment the number still
 * means anything.
 */
export function veoShots({ veo, frames, done } = {}) {
  const list = frames || [];
  const paid = done instanceof Set ? done : new Set(done || []);
  const shots = [];
  const skipped = [];

  for (const item of veo || []) {
    const n = Number(item && item.shot);
    const prompt = String((item && item.prompt) || "").trim();
    const frame = list[n - 1];
    if (!frame) {
      skipped.push({ shot: n, why: `there is no shot ${n} to render` });
      continue;
    }
    if (!prompt) {
      // ⚠ REFUSED, NEVER RENDERED FROM THE DIALOGUE ALONE. Veo bills a failure
      // exactly as it bills a success, so a submission that buys nothing is the
      // one thing worth being pedantic about.
      skipped.push({ shot: n, why: `shot ${n} has no motion prompt, and Veo bills for a blank one` });
      continue;
    }
    if (isTake(frame)) {
      skipped.push({ shot: n, why: `shot ${n} is already a rendered take, not a picture to animate` });
      continue;
    }
    if (paid.has(frame.id)) {
      // ⚠ NOT A LOSS, AND WORDED SO. This is the resume case and the
      // "I already animated this one by hand" case, and both are money SAVED.
      skipped.push({
        shot: n,
        why: `shot ${n} already has a take you have paid for — it is kept, not re-rendered`,
      });
      continue;
    }
    const holdMs = Math.max(0, Number(frame.duration_ms) || 0);
    shots.push({
      shot: n,
      frame_id: frame.id,
      label: frame.label || `Shot ${n}`,
      prompt,
      dialogue: String((item && item.dialogue) || ""),
      seconds: coverSeconds(holdMs),
      hold_ms: holdMs,
    });
  }

  // ⚠ IN FILM ORDER, WHATEVER ORDER THE MODEL WROTE THEM IN. The passes are
  // submitted in this order and the user watches them land, so a plan that
  // listed shot 30 before shot 4 would render the film out of order and read as
  // the Director working at random.
  shots.sort((a, b) => a.shot - b.shot);
  return { shots, skipped };
}

/**
 * THE SUBMISSIONS. `MAX_VIDEO_BATCH` shots each, in film order.
 *
 * ⚠ THE BATCH SIZE COMES FROM THE SERVER (`GET /director/config`), never from a
 * constant here. It is `config.MAX_VIDEO_BATCH` and it is a SPEND guard an
 * operator is expected to change per deployment — a browser that hard-coded 12
 * would submit 12 to a server that had been set to 6 and take a 413 on every
 * pass, having already shown the user a plan in four parts.
 */
export function chunkPasses(shots, batch) {
  const size = Math.max(1, Math.floor(Number(batch) || 1));
  const list = shots || [];
  const passes = [];
  for (let at = 0; at < list.length; at += size) passes.push(list.slice(at, at + size));
  return passes;
}

/**
 * IS THERE A RENDER PASS TO RUN AT ALL?
 *
 * Returns a reason rather than a boolean, exactly as `speechDue` does and for
 * the same reason: each way of answering "no" is a different thing to tell the
 * user, and the panel prints it verbatim under the tick box.
 */
export function veoDue(include, shots) {
  if (include && include[VEO_KEY] === false) {
    return { due: false, why: "Veo renders are switched off for this run." };
  }
  if (!shots || !shots.length) {
    return { due: false, why: "There are no motion prompts to render." };
  }
  return { due: true, why: "" };
}

/**
 * WHAT IS LEFT TO DO — read off the SERVER's clip records, not off memory.
 *
 * ⚠ THIS IS THE WHOLE RESUME, AND THE TRUTH IS `veo_clips`. The `director_run`
 * record says what the run INTENDED; the clip records say what was actually paid
 * for. A browser that crashed, a laptop that slept, a tab closed at pass three —
 * none of them can be trusted to remember how far they got, and all of them can
 * read the server. So "have I already bought this shot?" is answered by "is
 * there a ready clip against its frame", which is the same question
 * `_animate_targets` asks before it agrees to spend.
 *
 * ⚠ A FAILED RENDER IS NOT RETRIED, AND THAT IS DELIBERATE. Veo bills a failure
 * as it bills a success, so a shot that failed once has already cost money and
 * may well fail again for the same reason — a prompt it will not accept, a
 * picture it cannot read. Retrying it automatically on every resume is a loop
 * that spends. It is reported instead, and "Render again" is a button the user
 * presses on purpose.
 *
 * ⚠ AND A CLIP STILL `queued` OR `rendering` IS COUNTED AS NEITHER. If the
 * server is still working it will finish on its own and the poll will see it; if
 * the process died it is a record with nothing behind it. The two are told apart
 * by the JOB, not by the record — see `resumeDirectorVeo` in the editor, which
 * reads a RUNNING job as "the pass is still in flight, wait" and anything else
 * as "the pass is gone, submit it again".
 *
 * @param runShots the `shots` off the `director_run` record
 * @param clips    `veo_clips` as the server has them now
 * @returns {{ done, failed, inFlight, todo, paidUsd }}
 */
export function outstanding(runShots, clips) {
  const rows = clips || [];
  const ready = new Set();
  const failed = new Map();
  const flying = new Set();
  let paidUsd = 0;
  for (const clip of rows) {
    if (!clip || !clip.frame_id) continue;
    if (clip.status === "ready" && clip.upload_id) {
      ready.add(clip.frame_id);
      paidUsd += Number(clip.cost_usd) || 0;
    } else if (clip.status === "failed") {
      failed.set(clip.frame_id, clip.error || "The render failed.");
    } else if (clip.status === "queued" || clip.status === "rendering") {
      flying.add(clip.frame_id);
    }
  }

  const done = [];
  const lost = [];
  const inFlight = [];
  const todo = [];
  for (const shot of runShots || []) {
    // ⚠ READY WINS OVER EVERYTHING. A shot rendered twice — a failure and then a
    // success, or a "render again" — has two records, and the one that matters
    // is the one with a file behind it.
    if (ready.has(shot.frame_id)) done.push(shot);
    else if (flying.has(shot.frame_id)) inFlight.push(shot);
    else if (failed.has(shot.frame_id)) lost.push({ ...shot, why: failed.get(shot.frame_id) });
    else todo.push(shot);
  }
  return { done, failed: lost, inFlight, todo, paidUsd: Math.round(paidUsd * 100) / 100 };
}

/**
 * WHICH PASS STRETCHED WHICH SHOT — so the re-anchor can say so in words.
 *
 * Both paid passes grow shots, for different reasons, and a plan step dropped
 * because of one must not be explained by the other. `reanchor` prints these
 * reasons verbatim under the preview table, and "the voiceover stretched shot 9"
 * on a film with no voiceover in it is the kind of wrong sentence that makes a
 * user distrust every other sentence in the panel.
 *
 * @returns `{ [shot]: "voiceover" | "veo" | "both" }`, shots that grew only
 */
export function growthCauses(before, afterSpeech, afterVeo) {
  const spoke = shiftsOf(before || [], afterSpeech || []).grew;
  const shot = shiftsOf(afterSpeech || [], afterVeo || []).grew;
  const causes = {};
  for (const n of spoke) causes[n] = "voiceover";
  for (const n of shot) causes[n] = causes[n] ? "both" : "veo";
  return causes;
}
