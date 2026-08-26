// DirectorPanel.jsx — what 🎬 Make Video opens: the plan, then the rail.
//
// ---------------------------------------------------------------------------
// ⚠ THE OVERLAY DOES NOT CLOSE THIS DIALOG, AND ESC DOES NOTHING. ON PURPOSE.
// ---------------------------------------------------------------------------
// Every other modal in this app closes on a backdrop click — `PreflightModal`,
// the ✨ Animate confirm, the voiceover panel, the rename box. This one breaks
// that, and the reason is written here so nobody "fixes" it back:
//
//   The Director's panel is the one dialog where a stray click can throw work
//   away. In Phase 0 that is a run in flight — half a timeline edited, and the
//   Revert button the only way back to where you started, sitting on the dialog
//   that just vanished. From Phase 4 it is money: this is the panel the priced
//   run is launched from, and a backdrop click that dismisses a cost estimate
//   the user was still reading is the worst possible place to be helpful.
//
// So the ✕ is the only way out, in every phase, and it is the same `.modal-close`
// glyph every other dialog here uses — the escape route has to look familiar
// even though it is the only one.
//
// ⚠ AND WHILE A RUN IS IN FLIGHT THE ✕ ASKS FIRST. Closing mid-run would leave
// the timeline half-edited with the Revert button gone, which is the one state
// this feature must not be able to reach by accident.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS TWO POPUPS IN ONE DIALOG, AND THE ORDER IS THE PRODUCT.
// ---------------------------------------------------------------------------
// POPUP ONE — THE BRIEF (`phase === "brief"`). What is this film, and what
// language is it in. It is one sentence and one dropdown and both are optional,
// because the cost of asking is a dialog the user has to get past and the value
// is enormous: a Director told "it's a horror short, the machine is the villain"
// cuts a different film from one told nothing. It also carries the two doors out
// — read it with the AI, or read the RHYTHM ONLY, which is Phase 0's planner and
// needs no backend, no key and no quota.
//
// POPUP TWO — THE PLAN (`phase === "preview"`). The reading, then the edit as a
// table by shot, then the tick boxes. ⚠ THE TICK BOXES RE-COST WITHOUT CALLING
// THE MODEL AGAIN — un-ticking Effects re-reads the plan already in memory (see
// `recost` in `useDirectorRun`), the totals move, and THE BUTTON RELABELS ITSELF
// with the new number of edits. That last part is the point: the button says
// what pressing it will do, so the film described by the table and the film the
// button makes are provably the same one.
//
// The panel itself owns no logic. Everything it shows comes off `useDirectorRun`
// — same relationship `PreflightModal` has to the board page: a dialog that
// renders a decision, not one that makes it.

import { useEffect, useState } from "react";

import { MOTION_LABEL } from "../animatic/agent/actions.js";
import { freeKeys, paidKeys } from "../animatic/agent/plan_schema.js";
import useCapability from "../useCapability.js";
import Icon from "./Icon.jsx";

// ⚠ ONLY THE TWO PAID FLAGS ARE GATED, and the free ones must never join them:
// transitions, effects, text and shapes are edits this browser makes to a
// document in memory. They call nothing and cost nothing, so a lock on them
// would be a lock with no server rule behind it — see `entitlements.js`.

/** `1200` → `1.2s`. Times are read as seconds everywhere else in this editor. */
const secs = (ms) => `${(Math.max(0, Number(ms) || 0) / 1000).toFixed(1)}s`;

/**
 * HOW LONG THE AI PASS HAS BEEN THINKING, once that stops being obvious.
 *
 * ⚠ A SPINNER IS NOT A CLOCK, AND THIS WAIT IS LONGER THAN PEOPLE EXPECT. The
 * plan is TWO model calls in one request and the browser is willing to wait five
 * minutes for them (`PLAN_TIMEOUT_MS`) — up from two, which is what used to
 * abort a perfectly healthy plan and report a stuck database. A turning circle
 * with no number beside it looks identical at eight seconds and at ninety, so
 * the second minute reads as a hang and the user reloads the tab on a call that
 * was about to answer.
 *
 * ⚠ IT APPEARS AFTER `QUIET_MS` AND NOT BEFORE. A counter on a call that
 * finishes in six seconds is a stopwatch on a button press — it makes a fast
 * thing feel measured. This is only for the wait that has already gone on long
 * enough to be worth explaining.
 */
const QUIET_MS = 8000;

function Elapsed() {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    const began = Date.now();
    const timer = setInterval(() => setMs(Date.now() - began), 1000);
    return () => clearInterval(timer);
  }, []);
  if (ms < QUIET_MS) return null;
  return (
    <span className="tiny muted dir-elapsed">
      {Math.round(ms / 1000)}s — two calls, and a long board is slow. It gives up on
      its own if the model never answers.
    </span>
  );
}

/**
 * Which column of the preview table a step belongs in.
 *
 * ⚠ THE TABLE IS BY SHOT, NOT BY STEP, and that is the whole reason it is
 * readable. A list of 61 steps is a log; a row per shot saying what happens to
 * it — how long it holds, what leads into it, what treatment it gets, what is
 * written over it — is the thing a person can actually check against the film in
 * their head. So the steps are folded onto the shots they name.
 */
const COLUMN = {
  set_shot_duration: "timing",
  set_all_durations: "timing",
  add_transition: "in",
  set_transition_duration: "in",
  remove_transition: "in",
  push_in: "look",
  add_shot_motion: "look",
  clear_shot_motion: "look",
  set_shot_transform: "look",
  add_effect: "look",
  set_effect_param: "look",
  remove_effect: "look",
  add_text: "graphics",
  set_text: "graphics",
  apply_text_preset: "graphics",
  add_shape: "graphics",
  set_shape: "graphics",
};

/**
 * Fold the plan onto its shots.
 *
 * A transition is filed against the shot it leads INTO (`cut` names the shot it
 * comes after, so the row is `cut + 1`) — because that is where the reader is
 * looking when they ask "how do we get here".
 */
function byShot(plan, frames) {
  const rows = frames.map((frame, i) => ({
    shot: i + 1,
    label: frame.label || "",
    ms: frame.duration_ms || 0,
    timing: [],
    in: [],
    look: [],
    graphics: [],
  }));
  for (const step of plan.steps) {
    const column = COLUMN[step.verb];
    if (!column) continue;
    const shot =
      step.verb === "add_transition" ||
      step.verb === "set_transition_duration" ||
      step.verb === "remove_transition"
        ? step.args.cut + 1
        : step.args.shot;
    const row = rows[shot - 1];
    if (row) row[column].push(step);
  }
  return rows;
}

/** The short phrase a step contributes to its cell. */
function cell(step) {
  switch (step.verb) {
    case "add_transition":
      return `${step.args.kind}${step.args.ms ? ` ${secs(step.args.ms)}` : ""}`;
    case "remove_transition":
      return "cut";
    case "set_transition_duration":
      return secs(step.args.ms);
    case "push_in":
      return `${step.args.to > step.args.from ? "push in" : "pull back"} ${Math.round(
        step.args.to * 100
      )}%`;
    case "add_shot_motion":
      return MOTION_LABEL[step.args.kind] || step.args.kind;
    case "clear_shot_motion":
      return "held";
    case "set_shot_transform":
      return Object.keys(step.args.patch).join("/");
    case "add_effect":
      return step.args.kind;
    case "set_effect_param":
      return `${step.args.param} ${step.args.value}`;
    case "remove_effect":
      return "effect off";
    case "add_text":
      return `“${step.args.text.length > 24 ? `${step.args.text.slice(0, 23)}…` : step.args.text}”`;
    case "apply_text_preset":
      return step.args.preset;
    case "add_shape":
      return step.args.kind;
    case "set_shot_duration":
      return secs(step.args.ms);
    case "set_all_durations":
      return `all ${secs(step.args.ms)}`;
    default:
      return step.verb;
  }
}

/** What each tick box is called. Ids come from `governedKeys()`, never from here. */
const INCLUDE_LABEL = {
  transitions: "Transitions",
  effects: "Effects",
  text: "Text",
  shapes: "Shapes",
  captions: "Captions",
  // ⚠ THEY SAY WHAT THEY DO, NOT WHICH PHASE THEY ARE. "Sound effects" and
  // "Background music" are the two things a person came here wanting; "phase D"
  // is a fact about this file. Both are in the FREE row — they take files out of
  // a stock library and spend no money at all (see `PAID_PASSES` in
  // `plan_schema.js`), and putting them beside Veo would price a run that is free.
  sfx: "Sound effects",
  music: "Background music",
  voiceover: "Voiceover",
  veo: "Veo renders",
  // ⚠ IT IS CALLED WHAT THE BUTTON IN THE TOOL ROW IS CALLED. This tick box runs
  // 🖼 Animatic images — the same pass, the same queue, the same row — and a
  // second name for it ("Key poses", "Flipbook") would read as a second feature.
  poses: "Animatic images",
};

export default function DirectorPanel({ run, frames, languages = [], onClose }) {
  const [confirmClose, setConfirmClose] = useState(false);
  // ⚠ THREE CAPABILITIES REACH THIS PANEL, AND THEY GATE THREE DIFFERENT
  // THINGS. `director` is the AI DOOR ONLY — "Just the rhythm" is the Phase 0
  // rules planner, which calls nothing and must stay pressable for an account
  // that has no AI at all. The other two are the passes that spend.
  const directorCap = useCapability("director");
  const voiceoverCap = useCapability("tts-voiceover");
  const veoCap = useCapability("veo-render");
  // ⚠ THE SAME CAPABILITY 🖼 ANIMATIC IMAGES IN THE TOOL ROW IS GATED BY, and it
  // has to be the same one: this box runs that pass, so an account that may not
  // generate images must not be offered it here as a way round the button.
  const imageCap = useCapability("image-generate");
  const capFor = { voiceover: voiceoverCap, veo: veoCap, poses: imageCap };
  const {
    phase, plan, totals, dropped, trimmed, log, index, missingApi,
    source, why, analysis, veo, cost, include, hasModel,
    script, speech, willSpeak, speechWhy,
    shoot, quote, footage, willRender, renderWhy, pending, resuming,
    // ⚠ RENAMED ON THE WAY IN. `blocking` is already this component's name for
    // "is phase C2 the thing happening", and the runner's `blocking` is the
    // PROGRESS object — which survives the phase, exactly as `footage` does, so
    // the finished sentence is still on screen afterwards. Two meanings, two names.
    poses, blocking: blockRun, willBlock, blockWhy,
    sfx, music, score, willSfx, willMusic, sfxWhy, musicWhy,
  } = run;
  // ⚠ UN-TICKED, NOT JUST GREYED. A disabled box that is still CHECKED would
  // leave `include.veo` true, and everything downstream reads that flag: the
  // amber price line, the count on the Run button, and the pass itself. The
  // user would be shown a price for footage the server is going to refuse, and
  // then pay the wait to find out. Setting it false runs the same free re-cost
  // an ordinary un-tick does, so the panel re-prices itself with the pass gone.
  const setInclude = run.setInclude;
  useEffect(() => {
    if (!voiceoverCap.on && include.voiceover !== false) setInclude("voiceover", false);
    if (!veoCap.on && include.veo !== false) setInclude("veo", false);
    if (!imageCap.on && include.poses !== false) setInclude("poses", false);
  }, [
    voiceoverCap.on,
    veoCap.on,
    imageCap.on,
    include.voiceover,
    include.veo,
    include.poses,
    setInclude,
  ]);

  // ⚠ `speaking` IS IN FLIGHT BUT NOT STEPPABLE. Phase B is one server call that
  // has already been paid for by the time it starts, so there is nothing honest
  // for Pause, Step or Stop to do while it runs — but the ✕ must still guard,
  // because closing over it takes Revert away with it.
  const speaking = phase === "speaking";
  // ⚠ `rendering` IS IN FLIGHT, COSTS THE MOST, AND IS THE ONLY PHASE WHERE STOP
  // MEANS "AFTER THIS PASS". A submission of twelve renders is billed the moment
  // it leaves, so there is nothing honest for a mid-pass Stop to do — see
  // `veo_pass.js`. `anchoring` is folded in with it because it is free, instant
  // and happens between the last render and the first step: giving it a state of
  // its own would be a flicker with a name.
  const rendering = phase === "rendering" || phase === "anchoring";
  const stepping = phase === "running" || phase === "paused";
  // ⚠ `scoring` IS IN FLIGHT AND IT IS THE ONLY IN-FLIGHT PHASE THAT COSTS
  // NOTHING. It runs AFTER the steps — the cues have to land on shots the plan
  // has finished moving — so it is the one phase where a full step rail sits
  // above a pass that is still working, which is exactly right: the edit IS done.
  const scoring = phase === "scoring";
  // ⚠ `blocking` IS IN FLIGHT AND IT SPENDS THE IMAGE QUOTA, but unlike phase C
  // its Stop is honest between every SHOT rather than every pass: a shot is one
  // storyboard run, the board's own Stop winds it up, and everything drawn so far
  // stays on the timeline. See `poses_pass.js`.
  const blocking = phase === "blocking";
  const inFlight = stepping || speaking || rendering || blocking || scoring;
  const finished = phase === "done" || phase === "stopped";
  const notes = plan.steps.filter((s) => s.verb === "note");
  const rows = byShot(plan, frames);
  const skipped = [...dropped, ...trimmed];
  // ⚠ COUNTED OFF THE PLAN, NOT OFF THE TOTALS OBJECT, and it is what the button
  // says. A `note` is a sentence the Director wrote, not an edit it will make,
  // so a plan of six notes and nothing else must read "nothing to do" — which is
  // also exactly what the Run button is disabled on.
  const edits = plan.steps.filter((s) => s.verb !== "note").length;
  // HOW FAR THE RENDER RAIL IS DRAWN. `frac` when the pass has reported one —
  // it creeps inside a shot as well as between shots — and the shot count when
  // it has not, which is every run made before the pass started reporting it.
  const renderPct = Math.max(
    0,
    Math.min(
      100,
      footage && typeof footage.frac === "number"
        ? footage.frac * 100
        : ((footage?.done || 0) / Math.max(1, footage?.total || 1)) * 100
    )
  );
  // The same arithmetic for phase C2's rail, and the same fallback: `frac` when
  // the queue has reported one, whole shots when it has not.
  const blockPct = Math.max(
    0,
    Math.min(
      100,
      blockRun && typeof blockRun.frac === "number"
        ? blockRun.frac * 100
        : ((blockRun?.done || 0) / Math.max(1, blockRun?.total || 1)) * 100
    )
  );

  function tryClose() {
    // ⚠ THE ONE PLACE THE ✕ IS NOT IMMEDIATE. See the note at the top: closing
    // over a half-finished run takes the Revert button away with it.
    //
    // ⚠ AND DURING PHASE B IT DOES NOT CLOSE AT ALL, not even on the second
    // press. The pass is a paid call already in flight, and the run has to be
    // here to receive it: the re-anchor, the steps and Revert all hang off its
    // answer. It is seconds, and the notice says so.
    // ⚠ AND DURING EITHER PAID PASS IT DOES NOT CLOSE AT ALL. Both are calls
    // already in flight and already billed, and the run has to be here to
    // receive them: the attach, the re-anchor, the steps and Revert all hang off
    // their answers. Phase C additionally has passes still to submit, and
    // closing over it would abandon them without stopping them.
    if (speaking || rendering || blocking) {
      setConfirmClose(true);
      return;
    }
    if (inFlight && !confirmClose) {
      setConfirmClose(true);
      return;
    }
    setConfirmClose(false);
    onClose();
  }

  return (
    /* ⚠ NO `onClick` ON THE OVERLAY. Read the header before adding one back. */
    <div className="modal-overlay">
      <div className="card an-name-modal dir-modal" role="dialog" aria-label="Make video">
        <button className="modal-close" onClick={tryClose} title="Close">
          ✕
        </button>

        <h2>🎬 Make Video</h2>
        <p className="muted dir-sub">
          {frames.length} shot{frames.length === 1 ? "" : "s"} · {secs(
            frames.reduce((n, f) => n + (f.duration_ms || 0), 0)
          )}
          {phase === "brief"
            ? " · Nothing here spends anything until you press Run."
            : source === "ai"
              ? " · Read by the AI. Nothing has been touched yet."
              : " · Read off the rhythm of the timeline. Nothing has been touched yet."}
        </p>

        {/* ⚠ A BROKEN BUILD SAYS SO, IN THE UI. `ACTION_API` is the list of
            editor functions the verbs are allowed to call; if the editor stopped
            supplying one, every verb that needs it would fail one at a time
            during the run and read as "the Director doesn't work". This says
            which name went missing, and it is what
            `tests/editor_director_check.py` asserts the ABSENCE of — an
            assertion that cannot be satisfied by a console warning. */}
        {missingApi.length > 0 && (
          <p className="dir-broken">
            This build is wired wrong: the editor is not supplying{" "}
            <code>{missingApi.join(", ")}</code>. Some steps will fail.
          </p>
        )}

        {confirmClose && (
          <div className="dir-warn">
            {/* ⚠ THE SOUND PASS CANNOT BE STOPPED, AND PRETENDING OTHERWISE IS
                WORSE THAN SAYING SO. It is one server call that was paid for the
                moment it started; a Stop button here would close the dialog,
                leave the pass running, and the ripple would land on a timeline
                with no Revert in sight. */}
            {speaking ? (
              <>
                <strong>The dialogue is being read.</strong> That call has already been
                made and cannot be taken back — but closing now would leave the run with
                nowhere to put its answer. Give it a moment.
                <div className="dir-warn-row">
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => setConfirmClose(false)}
                  >
                    Wait for it
                  </button>
                </div>
              </>
            ) : rendering ? (
              /* ⚠ THE ONLY STOP IN THIS PANEL THAT COSTS SOMETHING TO GET
                 WRONG, so it says exactly what it will and will not do. The
                 pass in flight is paid for whatever happens next; the passes
                 after it have not been asked for yet. Stopping saves those and
                 nothing else, and closing the dialog saves neither — it would
                 only take away the Revert button and leave the run going. */
              <>
                <strong>Veo is rendering, and it is being paid for.</strong> The pass in
                flight cannot be taken back — but the passes after it have not been
                submitted yet, and stopping now means they never will be. Closing this
                stops nothing and takes Revert away with it.
                <div className="dir-warn-row">
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => setConfirmClose(false)}
                  >
                    Keep rendering
                  </button>
                  <button
                    type="button"
                    className="btn small dir-danger"
                    onClick={() => {
                      run.stop();
                      setConfirmClose(false);
                    }}
                  >
                    Stop after this pass
                  </button>
                </div>
              </>
            ) : (
              <>
                <strong>Stop after this step?</strong> The edits it has already made stay on
                the timeline — Revert puts them back.
                <div className="dir-warn-row">
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => setConfirmClose(false)}
                  >
                    Keep going
                  </button>
                  <button
                    type="button"
                    className="btn small dir-danger"
                    onClick={() => {
                      run.stop();
                      setConfirmClose(false);
                    }}
                  >
                    Stop
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* ⚠ A PASS THAT DIED HALFWAY, OFFERED BACK BEFORE ANYTHING ELSE IS.
            `director_run` still saying "running" on a project that has just been
            opened means a refresh, a crash or a closed laptop stopped a render
            mid-flight — and every shot it had already bought is sitting on the
            server, paid for. This is the first thing in the dialog because it is
            the only thing in the dialog that is about money already spent.

            ⚠ AND IT FINISHES THE FOOTAGE, NOT THE EDIT, which it says in those
            words. The plan lived in the browser that died; the takes did not. */}
        {phase === "brief" && pending && pending.todo.length > 0 && (
          <div className="dir-resume">
            <strong>
              A render was interrupted — {pending.done.length} of{" "}
              {pending.done.length + pending.todo.length + pending.failed.length} shot
              {pending.done.length + pending.todo.length + pending.failed.length === 1
                ? ""
                : "s"}{" "}
              had already been rendered.
            </strong>
            <p className="tiny muted">
              {pending.done.length > 0 && (
                <>
                  Those {pending.done.length === 1 ? "is" : "are"} paid for
                  {pending.paidUsd > 0 ? ` (about $${pending.paidUsd.toFixed(2)})` : ""} and{" "}
                  {pending.done.length === 1 ? "is" : "are"} on the timeline — finishing
                  will not render {pending.done.length === 1 ? "it" : "them"} again.{" "}
                </>
              )}
              The remaining {pending.todo.length} {pending.todo.length === 1 ? "was" : "were"}{" "}
              never submitted and cost nothing yet.
              {pending.failed.length > 0 && (
                <>
                  {" "}
                  {pending.failed.length} failed and{" "}
                  {pending.failed.length === 1 ? "is" : "are"} not retried automatically —
                  Veo bills a failure like a success, so that is a button you press on
                  purpose.
                </>
              )}
            </p>
            <p className="tiny muted">
              ⚠ This finishes the <em>footage</em>. The edit itself was never saved — plans
              live in the browser so you can read and re-cost them before agreeing — so ask
              for one again once the takes have landed. That part is free.
            </p>
            <div className="dir-warn-row">
              <button type="button" className="btn primary" onClick={run.resumeVeo}>
                Finish the render · {pending.todo.length} shot
                {pending.todo.length === 1 ? "" : "s"}
              </button>
            </div>
          </div>
        )}

        {/* ------------------------------------------------- POPUP ONE: the brief */}
        {phase === "brief" && (
          <div className="dir-brief">
            <label className="an-exp-label" htmlFor="dir-brief-text">
              What is this film?
            </label>
            {/* ⚠ OPTIONAL, AND IT SAYS SO. The board is readable on its own —
                labels, dialogue and holds — so an empty box still produces a
                plan. One sentence changes the plan more than any other control
                in this dialog, which is why it is first and why the placeholder
                is an EXAMPLE rather than an instruction. */}
            <textarea
              id="dir-brief-text"
              className="an-prop-input dir-brief-text"
              rows={3}
              value={run.brief}
              placeholder="Optional — “a horror short; the machine is the villain; keep it cold and slow”"
              onChange={(e) => run.setBrief(e.target.value)}
            />

            <label className="an-exp-label" htmlFor="dir-lang">
              Language
            </label>
            {/* ⚠ THIS WRITES THE PROJECT'S LANGUAGE, not this run's. On-screen
                text, the voiceover and the captions all read one field on the
                film (`AnimaticSettings.language`) — three of them each guessing
                separately is how a Hinglish film ends up with an English title
                over a Hindi voiceover. Free text, not an enum: “Tamil” works.
                ⚠ AND IT DOES NOT TOUCH THE VEO PROMPTS, which stay English —
                camera language, not performance. See `director.py`. */}
            <span className="dir-lang-row">
              <select
                id="dir-lang"
                className="an-select"
                value={languages.some((l) => l.id === run.language) ? run.language : ""}
                onChange={(e) => run.setLanguage(e.target.value)}
              >
                <option value="">Whatever the board is in</option>
                {languages.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.label}
                  </option>
                ))}
                <option value="__other">Something else…</option>
              </select>
              {(run.language === "__other" ||
                (run.language && !languages.some((l) => l.id === run.language))) && (
                <input
                  className="an-prop-input"
                  value={run.language === "__other" ? "" : run.language}
                  placeholder="e.g. Tamil, Bhojpuri, Spanish"
                  onChange={(e) => run.setLanguage(e.target.value)}
                />
              )}
            </span>

            <p className="tiny muted dir-brief-note">
              On-screen text is written in this language. Veo motion prompts stay in
              English — the dialogue inside them does not.
            </p>

            {/* ⚠ SAID HERE, WHERE THE CHOICE IS MADE. The 🔒 on the button
                below explains why it cannot be pressed; this explains what the
                one beside it still does, so the answer to "my AI is off" is a
                working plan rather than a closed dialog. */}
            {!directorCap.on && directorCap.visible && (
              <p className="tiny muted dir-brief-note">
                🔒 {directorCap.reason} “Just the rhythm” still works — it reads the
                shot lengths and needs no AI at all.
              </p>
            )}
          </div>
        )}

        {phase === "planning" && (
          <p className="dir-planning">
            <span className="spinner-inline" /> Reading your film…
            <Elapsed />
          </p>
        )}

        {/* ⚠ WHY YOU ARE LOOKING AT THE RULES PLAN. A call that failed must not
            be an error screen where a plan should be — the rhythm pass is worth
            having on its own — but it must not be silent either, or the user
            reads a thin plan as the AI's opinion of their film. */}
        {phase === "preview" && why && (
          <p className="dir-fallback">
            The AI pass didn’t run ({why}), so this is the rhythm read off the
            timeline. Nothing else changed.
          </p>
        )}

        {/* --------------------------------------------------- what it read */}
        {phase === "preview" && source === "ai" && analysis && (
          <div className="dir-read dir-analysis">
            {analysis.logline && <p className="dir-logline">{analysis.logline}</p>}
            <p className="tiny muted">
              {[analysis.genre, analysis.mood && `${analysis.mood}`]
                .filter(Boolean)
                .join(" · ")}
              {analysis.scenes?.length
                ? `${analysis.genre || analysis.mood ? " · " : ""}${analysis.scenes.length} scene${
                    analysis.scenes.length === 1 ? "" : "s"
                  }: ${analysis.scenes
                    .map((s) => `${s.title || "—"} (${s.start_shot}–${s.end_shot})`)
                    .join(", ")}`
                : ""}
            </p>
            {/* What it had to assume, in its own words. Honest and often the
                most useful thing on the screen — "the board gives no location
                for shot 6" is a note about the BOARD, not about the plan. */}
            {analysis.notes?.length > 0 && (
              <ul className="dir-notes">
                {analysis.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {notes.length > 0 && (
          <div className="dir-read">
            {notes.map((step) => (
              <p key={step.id} className="dir-read-line">
                {step.args.text}
              </p>
            ))}
          </div>
        )}

        {/* --------------------------------------------------- the plan table */}
        {phase === "preview" && (
          <>
            {plan.steps.some((s) => s.verb !== "note") ? (
              <div className="dir-table-wrap">
                <table className="dir-table">
                  <thead>
                    <tr>
                      <th>Shot</th>
                      <th>Timing</th>
                      <th>In</th>
                      <th>Look</th>
                      <th>Text/Shape</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const busy =
                        row.timing.length || row.in.length || row.look.length || row.graphics.length;
                      return (
                        <tr key={row.shot} className={busy ? "" : "dir-row-quiet"}>
                          <td>{row.shot}</td>
                          <td>
                            {row.timing.length ? row.timing.map(cell).join(", ") : secs(row.ms)}
                          </td>
                          <td>{row.in.map(cell).join(", ") || "—"}</td>
                          <td>{row.look.map(cell).join(", ") || "—"}</td>
                          <td>{row.graphics.map(cell).join(", ") || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">
                Nothing to do — see above. Vary the shot lengths and run it again.
              </p>
            )}

            {/* ⚠ THE TICK BOXES ARE THE RE-COST, AND THEY ARE FREE. Un-ticking
                one re-reads the plan already in memory against the new flags —
                no model call, no wait — and everything below moves with it: the
                table loses those rows, the total under it changes, and the Run
                button relabels with the new number of edits. That is what makes
                the preview trustworthy: the film in the table and the film the
                button makes are the same film, at every setting.
                ⚠ THE LIST IS DERIVED (`freeKeys` / `paidKeys`). A flag no verb
                and no pass answers to is not offered at all — a switch that does
                nothing when clicked teaches the user the whole panel is
                decorative. */}
            <div className="dir-include">
              <span className="an-exp-label">This run may touch</span>
              {freeKeys().map((key) => (
                <label className="an-check dir-include-box" key={key}>
                  <input
                    type="checkbox"
                    checked={include[key] !== false}
                    onChange={(e) => run.setInclude(key, e.target.checked)}
                  />
                  {INCLUDE_LABEL[key] || key}
                </label>
              ))}
            </div>
            {/* ⚠ THE TWO THAT SPEND GET THEIR OWN ROW, AND IT IS GOLD. They used
                to sit in the line above between Shapes and the edge, drawn
                exactly like the free ones — and on a narrow panel "Veo renders"
                wrapped onto a second line directly underneath "Voiceover". A
                user who meant to leave the RENDER off and un-ticked the
                VOICEOVER instead gets a film with no sound and no captions and
                nothing on screen to say why, which is what was reported. These
                two are a different kind of switch: they do not change what the
                edit looks like, they cancel a pass that costs money and, in the
                voiceover's case, takes the subtitles with it. So they are
                labelled as what they are. */}
            <div className="dir-include dir-include-paid">
              {/* ⚠ IT SAID "These two spend" AND THERE ARE THREE OF THEM NOW.
                  🖼 Animatic images joined the row when it became a 🎬 pass, and a
                  heading that counts is a heading that goes wrong every time this
                  list changes — so it stops counting. */}
              <span className="an-exp-label">These spend</span>
              {paidKeys().map((key) => {
                // ⚠ THE BOX STAYS ON SCREEN WHEN IT IS LOCKED, un-ticked and
                // wearing the reason. Removing it would leave a panel that
                // simply never mentions Veo — and the customer who is paying
                // for a plan without it has no way to learn what they would
                // get. It only disappears when the capability is not visible at
                // all, which is a kill switch and has nothing to offer.
                const cap = capFor[key];
                if (cap && !cap.visible) return null;
                const off = cap && !cap.on;
                return (
                  <label
                    className={`an-check dir-include-box ${off ? "cap-off" : ""}`}
                    key={key}
                    title={off ? cap.reason : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={!off && include[key] !== false}
                      disabled={off}
                      onChange={(e) => run.setInclude(key, e.target.checked)}
                    />
                    {off ? "🔒 " : ""}
                    {INCLUDE_LABEL[key] || key}
                  </label>
                );
              })}
              {/* ⚠ SAID OUT LOUD, because "Voiceover" does not look like it owns
                  the captions and it does: phase B writes them. The one line
                  that would have answered the whole report. */}
              <span className="tiny muted dir-include-note">
                the voiceover writes the captions too
              </span>
              {/* ⚠ THE REASON IS PRINTED, NOT ONLY HOVERED. A `title` is
                  invisible on a touchscreen and invisible to anyone who does
                  not think to hover a control they have already been refused. */}
              {[voiceoverCap, veoCap, imageCap]
                .filter((c) => c.visible && !c.on)
                .map((c) => (
                  <span className="tiny muted dir-include-note" key={c.reason}>
                    🔒 {c.reason}
                  </span>
                ))}
            </div>

            {/* ------------------------------------------------- PHASE B: the sound */}
            {/* ⚠ THE SCRIPT IS SHOWN BEFORE IT IS PAID FOR, and shown whether or
                not the box is ticked — a switch has to be able to say what
                ticking it would DO. Every line, not a count: this is the one
                thing in the panel that will be HEARD, and the 🎙 dialog learned
                the hard way that a voiceover priced without its words on screen
                is a voiceover nobody trusts. */}
            {script.lines.length > 0 && (
              <details className="dir-script" open={script.written}>
                <summary>
                  {willSpeak ? "Reads" : "Would read"} {script.lines.length} line
                  {script.lines.length === 1 ? "" : "s"} aloud
                  {script.written ? " — written by the Director" : " — from your storyboard"}
                </summary>
                {/* ⚠ WHO WROTE THESE WORDS, SAID OUT LOUD. A line off the board
                    and a line the model invented are not the same object, and a
                    user who cannot tell which they are looking at will assume
                    the first. */}
                {script.written && (
                  <p className="tiny muted">
                    Your board has no dialogue, so these are lines the Director wrote while
                    reading the film. Read them before you run it — they will be spoken in
                    your film, in your language.
                  </p>
                )}
                <ul className="dir-script-lines">
                  {script.lines.map((line, i) => (
                    <li key={i}>
                      <span className="dir-script-shot">{line.shot || "—"}</span>
                      {line.character && <span className="dir-script-who">{line.character}</span>}
                      <span className="dir-script-text">{line.text}</span>
                    </li>
                  ))}
                </ul>
                {script.skipped.length > 0 && (
                  <ul className="dir-notes">
                    {script.skipped.map((row, i) => (
                      <li key={i}>{row.why}</li>
                    ))}
                  </ul>
                )}
                {/* ⚠ THE ONE THING THE TABLE ABOVE CANNOT SHOW. Every row in it
                    is timed against the film as it stands, and this pass re-times
                    the film — so the table is right about WHAT happens and cannot
                    be right about WHEN until the sound has landed. Saying so is
                    cheaper than a preview that quietly disagrees with the result. */}
                {willSpeak && (
                  <p className="tiny muted">
                    This runs first, and it moves pictures: a shot is stretched to cover the
                    line spoken over it and the shots after it slide along. The edit above is
                    then re-anchored onto the film that comes back.
                  </p>
                )}
                {!willSpeak && speechWhy && <p className="tiny muted">{speechWhy}</p>}
              </details>
            )}

            {/* ------------------------------------------- PHASE C: the footage */}
            {/* ⚠ THE MOST EXPENSIVE BOX IN THIS PANEL, AND THE ONLY ONE THAT
                STARTS UN-TICKED. Every shot, every length and the price of each
                is on screen before it is ticked, for the same reason the script
                is: a number that appears only after you have agreed to pay it is
                not a price. `shoot` is worked out whether or not the box is on,
                because a switch has to be able to say what flicking it would do. */}
            {shoot.shots.length > 0 && (
              <details className="dir-shoot" open={willRender}>
                <summary>
                  {willRender ? "Renders" : "Would render"} {shoot.shots.length} shot
                  {shoot.shots.length === 1 ? "" : "s"} with Veo
                  {quote.passes.length > 1
                    ? ` — ${quote.passes.length} passes of up to ${quote.batch}`
                    : ""}
                  {quote.total?.usd > 0 ? ` · about $${quote.total.usd.toFixed(2)}` : ""}
                </summary>
                {/* ⚠ THE LENGTH POLICY, SAID ONCE, WHERE THE LENGTHS ARE. Veo
                    renders 4, 6 or 8 seconds and nothing between, so a shot
                    holding 2.4s gets a 4s take and GROWS to it — the same rule
                    the timeline already keeps for a dropped render and for a
                    spoken line. Nobody would guess that from a table of numbers. */}
                <p className="tiny muted">
                  Veo renders 4, 6 or 8 seconds — never anything between — so each shot is
                  given the shortest take that covers its hold, and grows to match it.
                  That makes the film longer, and the edit is re-anchored onto it
                  afterwards.
                </p>
                <ul className="dir-shoot-lines">
                  {shoot.shots.map((row) => (
                    <li key={row.frame_id}>
                      <span className="dir-script-shot">{row.label}</span>
                      <span className="dir-shoot-len">
                        {row.seconds}s
                        {row.hold_ms > 0 && (
                          <span className="tiny muted"> over {secs(row.hold_ms)}</span>
                        )}
                      </span>
                      <span className="dir-script-text">{row.prompt}</span>
                    </li>
                  ))}
                </ul>
                {/* ⚠ THE ASSUMPTION, NAMED. The voiceover runs FIRST and stretches
                    the shot each line sits on, which can push a take up a size —
                    so those shots are quoted at the longest one. The run then
                    costs the SAME OR LESS than the button said, which is the only
                    direction a surprise about money may go. */}
                {willSpeak && shoot.shots.some((row) => row.assumed) && (
                  <p className="tiny muted">
                    {shoot.shots.filter((row) => row.assumed).length} of these carry a
                    spoken line. The voiceover runs first and stretches the shot it is read
                    over, so those are priced at the longest take — if the lines come back
                    shorter, the render costs less than this.
                  </p>
                )}
                {shoot.skipped.length > 0 && (
                  <ul className="dir-notes">
                    {shoot.skipped.map((row, i) => (
                      <li key={i}>{row.why}</li>
                    ))}
                  </ul>
                )}
                {!willRender && renderWhy && <p className="tiny muted">{renderWhy}</p>}
              </details>
            )}

            {/* ----------------------------------- PHASE C2: the key poses */}
            {/* ⚠ THE SECOND BOX IN THIS PANEL THAT STARTS UN-TICKED, and the only
                other one that spends without a dollar figure behind it. It buys
                FOUR DRAWINGS PER SECOND of film out of the account's image quota —
                a 32-second animatic is 128 pictures — so the count is on screen
                before the box is ticked, for exactly the reason the Veo prices
                are: a number that appears only after you have agreed to it is not
                a price. `poses` is worked out whether or not the box is on,
                because a switch has to be able to say what flicking it would do.
                ⚠ AND THE SHOTS VEO IS TAKING ARE ALREADY OUT OF IT. `poseWork`
                hands them back — a take sits over the drawings, so blocking those
                shots out buys pictures nobody sees — and the reason is printed
                under the list rather than left as a count that quietly shrank. */}
            {(poses.shots.length > 0 || poses.skipped.length > 0) && (
              <details className="dir-shoot dir-block" open={willBlock}>
                <summary>
                  {willBlock ? "Blocks" : "Would block"} {poses.shots.length} shot
                  {poses.shots.length === 1 ? "" : "s"} out as animatic images
                  {poses.toDraw > 0 ? ` — ${poses.toDraw} drawings` : ""}
                </summary>
                {/* ⚠ THE ARITHMETIC, SAID ONCE, WHERE THE NUMBERS ARE. Nobody would
                    guess four-per-second from a table, and it is the whole reason
                    a 2-second shot costs 8 and a 6-second shot costs 24. */}
                <p className="tiny muted">
                  Every storyboard shot on the timeline is drawn as key poses — four
                  drawings per second of its own length — and they land on the{" "}
                  <strong>Animatic images</strong> row, spread across the shot they
                  came from. This runs LAST, after the voiceover and the renders,
                  because those two change how long a shot holds and the hold is
                  what decides how many drawings it buys.
                </p>
                <ul className="dir-shoot-lines">
                  {poses.shots.map((row) => (
                    <li key={row.frameId}>
                      <span className="dir-script-shot">{row.label}</span>
                      <span className="dir-shoot-len">
                        {row.poses} drawings
                        {row.holdMs > 0 && (
                          <span className="tiny muted"> over {secs(row.holdMs)}</span>
                        )}
                      </span>
                      <span className="dir-script-text">
                        {row.have > 0
                          ? `${row.have} already on the storyboard — not drawn again`
                          : ""}
                      </span>
                    </li>
                  ))}
                </ul>
                {/* ⚠ WHAT IS ALREADY DRAWN IS NOT CHARGED FOR AGAIN, and saying so
                    is what stops the summary's number looking wrong. The pass
                    resumes onto the storyboard's own sequence, so a shot blocked
                    out yesterday costs nothing today — it still goes through the
                    queue, because the drawings have to be LAID on this timeline. */}
                {poses.already > 0 && (
                  <p className="tiny muted">
                    {poses.drawings} drawings in all, of which{" "}
                    <strong>{poses.already}</strong> are already on the storyboard and
                    are not paid for a second time.
                  </p>
                )}
                {poses.skipped.length > 0 && (
                  <ul className="dir-notes">
                    {poses.skipped.map((row, i) => (
                      <li key={i}>{row.why}</li>
                    ))}
                  </ul>
                )}
                {!willBlock && blockWhy && <p className="tiny muted">{blockWhy}</p>}
              </details>
            )}

            {/* ------------------------------ PHASES D AND E: the soundtrack */}
            {/* ⚠ THE CUES ARE SHOWN AS THE SEARCH TERMS THEY ARE, not dressed up
                as sounds. Nobody can audition these before the run — the pass
                takes the top CC0 result for each and lays it down — so the one
                honest thing the preview can show is exactly what will be typed
                into the library, next to the shot it will land on. A user who
                reads "shot 4 · heavy door slam" knows what they are getting and
                can un-tick the box; a user shown "🔊 Sound design ✓" does not.
                ⚠ AND IT IS FREE, WHICH THE SUMMARY SAYS BEFORE ANYTHING ELSE —
                this block sits directly under the Veo one, which is the most
                expensive thing in the app, and a reader skimming downwards will
                assume the two cost the same kind of thing unless told. */}
            {/* ⚠ AND WHEN THERE IS NOTHING CUED, THE REASON IS STILL ON SCREEN.
                The block below only exists when there are cues, so the first
                build of this feature drew two ticked boxes marked "Sound effects"
                and "Background music" over a run that added neither and said
                nothing about why — which is the worst version of a tick box: one
                that is ON and does nothing. The reasons are written by
                `sfxDue`/`musicDue` and this is the one place they can be read on
                a rules-only plan, where they are the whole story: arithmetic can
                tell which shots were HELD, it cannot tell that one of them is a
                door closing. */}
            {!sfx.cues.length && !music && (sfxWhy || musicWhy) && (
              <p className="tiny muted dir-score-none">
                {sfxWhy || musicWhy}
                {source !== "ai" && hasModel && (
                  <>
                    {" "}
                    Press <strong>Read it again</strong> and choose the AI door to get them.
                  </>
                )}
              </p>
            )}

            {(sfx.cues.length > 0 || music) && (
              <details className="dir-score-plan">
                <summary>
                  {willSfx || willMusic ? "Adds" : "Would add"}{" "}
                  {[
                    sfx.cues.length
                      ? `${sfx.cues.length} sound effect${sfx.cues.length === 1 ? "" : "s"}`
                      : "",
                    music ? "a music bed" : "",
                  ]
                    .filter(Boolean)
                    .join(" and ")}{" "}
                  — free
                </summary>
                <p className="tiny muted">
                  These are search terms, not sounds. Each one is looked up in the
                  library and the top public-domain (CC0) result is used, so nothing
                  here obliges you to credit anybody. They are laid on their own two
                  rows — Sound FX and Music — after the edit is finished, so they land
                  on the shots as they end up rather than as they are now.
                </p>
                {music && (
                  <p className="dir-score-bed">
                    <span className="dir-script-shot">Music</span>
                    <span className="dir-script-text">{music.query}</span>
                    {music.mood && <span className="dir-script-who">{music.mood}</span>}
                  </p>
                )}
                {sfx.cues.length > 0 && (
                  <ul className="dir-script-lines">
                    {sfx.cues.map((cue, i) => (
                      <li key={`${cue.frame_id}:${i}`}>
                        <span className="dir-script-shot">Shot {cue.shot}</span>
                        <span className="dir-script-text">{cue.query}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {/* ⚠ WHAT THE CUE PASS ITSELF LEFT OUT, before anything has been
                    searched for. A cue over a shot that does not exist, or one
                    past the shared library budget, is a silence the user is owed
                    an explanation for — and the explanation is cheaper here than
                    after the run, where it would compete with the result. */}
                {sfx.skipped.length > 0 && (
                  <ul className="dir-notes">
                    {sfx.skipped.map((row, i) => (
                      <li key={i}>
                        Shot {row.shot} — {row.why}
                      </li>
                    ))}
                  </ul>
                )}
                {!willSfx && sfxWhy && <p className="tiny muted">{sfxWhy}</p>}
                {!willMusic && musicWhy && <p className="tiny muted">{musicWhy}</p>}
              </details>
            )}

            <p className="dir-totals">
              {edits} edit{edits === 1 ? "" : "s"} · {totals.transitions} transition
              {totals.transitions === 1 ? "" : "s"} · {totals.moves} move
              {totals.moves === 1 ? "" : "s"} · {totals.effects} effect
              {totals.effects === 1 ? "" : "s"} · {totals.texts} text · {totals.shapes} shape
              {totals.shapes === 1 ? "" : "s"}
            </p>

            {/* ⚠ FREE, AND THE QUOTE BESIDE IT IS FOR A PASS THIS BUTTON DOES
                NOT RUN. The Director writes a Veo motion prompt per shot while
                it has the film in its head — that is a story decision and it
                belongs next to the beat — but nothing here renders one. So the
                price is shown as what it WOULD be, said in those words, rather
                than left off the screen until the day it is charged. */}
            {/* ⚠ THE PANEL STOPPED BEING FREE HERE, AND IT SAYS SO IN THE FIRST
                THREE WORDS. Phase 0's "this spends nothing" was true of every
                verb and still is; phase B is not a verb. A price line that keeps
                saying "Free" with a paid pass ticked below it is the single
                worst sentence this dialog could contain. */}
            {/* ⚠ THREE PASSES CAN SPEND NOW, SO THE SENTENCE IS BUILT RATHER THAN
                WRITTEN. 🖼 Animatic images has no dollar figure — it spends the
                account's IMAGE quota — so its clause is a count of drawings, and
                "run"/"runs" and "either"/"any of them" are picked off how many are
                actually ticked instead of assuming two. */}
            <p
              className={
                willSpeak || willRender || willBlock ? "dir-cost dir-cost-spends" : "dir-cost"
              }
            >
              {willSpeak || willRender || willBlock ? (
                <>
                  <strong>This one spends.</strong> Every edit in the table is free — the
                  cost is the {[
                    willSpeak
                      ? `${script.lines.length} line${script.lines.length === 1 ? "" : "s"} read aloud`
                      : "",
                    willRender
                      ? `${shoot.shots.length} shot${shoot.shots.length === 1 ? "" : "s"} rendered with Veo` +
                        (quote.total?.usd > 0 ? ` (about $${quote.total.usd.toFixed(2)})` : "")
                      : "",
                    willBlock
                      ? `${poses.toDraw} animatic image${poses.toDraw === 1 ? "" : "s"} drawn ` +
                        `across ${poses.shots.length} shot${poses.shots.length === 1 ? "" : "s"}`
                      : "",
                  ]
                    .filter(Boolean)
                    .join(" and the ")}
                  , which{" "}
                  {[willSpeak, willRender, willBlock].filter(Boolean).length > 1
                    ? "run"
                    : "runs"}{" "}
                  before the edit is applied. Un-tick{" "}
                  {[willSpeak, willRender, willBlock].filter(Boolean).length > 2
                    ? "any of them"
                    : [willSpeak, willRender, willBlock].filter(Boolean).length > 1
                      ? "either"
                      : "it"}{" "}
                  to run the edit alone.
                </>
              ) : (
                <>
                  <strong>Free.</strong> This plan spends nothing — every step is an edit
                  the editor already knows how to make.
                </>
              )}
              {/* ⚠ THE QUOTE FOR A PASS THIS BUTTON WILL NOT RUN, kept for the
                  case it is switched off: the Director writes a motion prompt per
                  shot while it has the film in its head whether or not anyone
                  renders them, and a price left off the screen until the day it
                  is charged is a price nobody can plan around. */}
              {!willRender && veo.length > 0 && cost?.usd > 0 && (
                <>
                  {" "}
                  It also wrote {veo.length} Veo motion prompt
                  {veo.length === 1 ? "" : "s"}; rendering those would be about $
                  {cost.usd.toFixed(2)} ({cost.tier}/{cost.resolution}), and this
                  button does not.
                </>
              )}
            </p>
          </>
        )}

        {/* -------------------------------------------------------- the rail */}
        {(inFlight || finished) && (
          <>
            {/* ⚠ PHASE B GETS ITS OWN LINE, ABOVE THE STEP RAIL, because it is
                not a step and must not look like one: it takes a minute rather
                than 90ms, it cannot be paused, and when it lands it CHANGES THE
                PLAN below it. The sentence it leaves behind afterwards says how
                many shots grew — which is the only warning the user gets that
                the table they read a moment ago has been re-anchored. */}
            {(speaking || speech) && (
              <p className={`dir-speech dir-speech-${speech?.stage || "reading"}`}>
                {speaking && <span className="spinner-inline" />}
                {speech?.message || "Reading the dialogue aloud…"}
              </p>
            )}
            {/* ⚠ PHASE C GETS ITS OWN LINE AND ITS OWN RAIL, and the rail is
                about SHOTS rather than steps. A step is 90ms; a shot is a Veo
                render and most of a minute, and showing both on one bar would
                put ninety per cent of the wait into one per cent of the width.
                ⚠ IT USED TO COUNT PASSES, which is the bug: a film that fits
                one submission has exactly one pass, so the bar was empty for
                the entire render and full the instant it was over. The pass
                reports its own progress now (`frac`), and the number beside it
                is what has actually been bought so far — which is still the
                only progress figure here that costs anything to be wrong. */}
            {(rendering || footage) && (
              <div className={`dir-shootrun dir-shootrun-${footage?.stage || "rendering"}`}>
                {/* ⚠ NO SPINNER. It said what the bar below it already says, and
                    said it WRONG: the circle turned while the rail sat empty,
                    so the two disagreed about whether anything was happening
                    and the turning one won. Reported as "the circle moves but
                    the bar doesn't". The rail is the thing that carries the
                    number, so the rail is the thing that is kept. */}
                <p className="dir-speech">{footage?.message || "Rendering with Veo…"}</p>
                {footage?.passes > 0 && (
                  <>
                    {/* ⚠ DRAWN FROM `frac`, NOT FROM THE COUNT BESIDE IT. The
                        count only moves when a shot LANDS, and on a film that
                        fits one submission that is a bar which sits empty for
                        the whole render and fills the instant it is over — the
                        bug this is the fix for. `frac` is fed by the pass's own
                        poll, so it creeps inside a shot as well as between
                        them. The COUNT stays whole shots: "3.4 of 7 rendered"
                        would be a number about nothing. */}
                    <div className="dir-rail dir-rail-passes" aria-label="Render progress">
                      <div className="dir-rail-fill" style={{ width: `${renderPct}%` }} />
                    </div>
                    <p className="dir-progress">
                      {footage.done || 0} of {footage.total || 0} shot
                      {(footage.total || 0) === 1 ? "" : "s"} rendered
                      {footage.detail ? ` — ${footage.detail}` : ""}
                    </p>
                  </>
                )}
                {/* ⚠ SAID WHILE IT IS STILL TRUE. Stop was pressed, the pass in
                    flight is being seen through because it is already billed,
                    and the next one will not go. Leaving this unsaid makes a
                    Stop that takes two minutes to visibly happen read as a
                    button that did nothing. */}
                {footage?.stopping && footage.stage === "rendering" && (
                  <p className="tiny muted">
                    Stopping when this pass lands — it is already paid for, so it is being
                    waited for rather than thrown away.
                  </p>
                )}
              </div>
            )}
            {/* ⚠ PHASE C2 GETS ITS OWN LINE AND ITS OWN RAIL, drawn exactly like
                phase C's above it and for the same reason: a shot here is a whole
                storyboard run and most of a minute, so a bar shared with the 90ms
                steps would put the entire wait into a sliver of the width. The
                rail reads `frac`, which creeps INSIDE a shot as well as between
                shots (the board's own job reports a percentage); the count beside
                it stays whole shots, because "2.4 of 7 blocked out" is a number
                about nothing. */}
            {(blocking || blockRun) && (
              <div className={`dir-shootrun dir-shootrun-${blockRun?.stage || "blocking"}`}>
                <p className="dir-speech">
                  {blockRun?.message || "Blocking the shots out as key poses…"}
                </p>
                {(blockRun?.total || 0) > 0 && (
                  <>
                    <div className="dir-rail dir-rail-passes" aria-label="Blocking-out progress">
                      <div className="dir-rail-fill" style={{ width: `${blockPct}%` }} />
                    </div>
                    <p className="dir-progress">
                      {blockRun.done || 0} of {blockRun.total || 0} shot
                      {(blockRun.total || 0) === 1 ? "" : "s"} blocked out
                      {blockRun.detail ? ` — ${blockRun.detail}` : ""}
                    </p>
                  </>
                )}
                {/* ⚠ SAID WHILE IT IS STILL TRUE, exactly as the render's Stop is.
                    The shot in flight is one storyboard run that cannot be
                    un-sent, so it is being finished and its drawings will be laid
                    down; the shots after it are never asked for. */}
                {blockRun?.stopping && blockRun.stage === "blocking" && (
                  <p className="tiny muted">
                    Stopping after this shot — it is already being drawn, so it is
                    finished and kept. The shots after it cost nothing.
                  </p>
                )}
              </div>
            )}
            {/* ⚠ PHASES D AND E GET THEIR OWN LINE, BELOW THE OTHER TWO AND ABOVE
                THE STEP RAIL, because that is the order they actually happen in —
                and this is the only pass that runs with the step rail already
                FULL. It has no bar of its own on purpose: it is one request and
                one placement, so a rail would be empty and then finished, which
                is the exact bug the render bar was fixed for. The sentence it
                leaves behind is the count of what landed AND what did not. */}
            {(scoring || score) && (
              <div className={`dir-shootrun dir-shootrun-${score?.stage || "fetching"}`}>
                <p className="dir-speech">
                  {scoring && <span className="spinner-inline" />}
                  {score?.message || "Finding the sound…"}
                </p>
                {/* ⚠ A CUE THAT FOUND NOTHING IS NAMED, not folded into a count.
                    "9 of 11 sounds added" tells the user something is missing and
                    not which shot is silent — and the shot is the only thing they
                    can act on. */}
                {(score?.missing || []).length > 0 && (
                  <ul className="dir-notes">
                    {score.missing.map((row, i) => (
                      <li key={i}>
                        Shot {row.shot} — nothing usable was found for “{row.query}”
                      </li>
                    ))}
                  </ul>
                )}
                {/* ⚠ A SOUND FOUND BY WIDENING THE SEARCH IS NAMED. The plan said
                    "light feather rustle"; if the library only had something for
                    "feather rustle", what is on the timeline is an answer to a
                    narrower question than the one on screen — and a user who
                    reads the cue and hears something else needs to be told which
                    of the two happened, not left to wonder whether the feature
                    works. */}
                {(score?.widened || []).length > 0 && (
                  <ul className="dir-notes">
                    {score.widened.map((row, i) => (
                      <li key={`w${i}`}>
                        “{row.query || row.filename}” — nothing matched it exactly, so{" "}
                        {row.relaxedTo === "any length"
                          ? "the length limit was dropped"
                          : `“${row.relaxedTo}” was searched for instead`}
                      </li>
                    ))}
                  </ul>
                )}
                {(score?.skipped || []).length > 0 && (
                  <ul className="dir-notes">
                    {score.skipped.map((row, i) => (
                      <li key={`s${i}`}>
                        {row.query ? `“${row.query}” — ` : row.shot ? `Shot ${row.shot} — ` : ""}
                        {row.why}
                      </li>
                    ))}
                  </ul>
                )}
                {score?.stopping && score.stage === "fetching" && (
                  <p className="tiny muted">
                    Stopping when this lookup lands — nothing has been spent and nothing
                    will be laid down.
                  </p>
                )}
              </div>
            )}
            {/* ⚠ THE STEP RAIL IS FOR STEPS, AND IT IS DRAWN ONLY ONCE THE STEPS
                ARE THE THING HAPPENING. That was written here as "a resumed run
                has none", which caught one case of the rule and missed the one
                every paid run hits: during phase B and phase C the edit has not
                started, `index` is 0, and the rail is a second bar sitting dead
                underneath a render bar that is moving perfectly well. Reported
                as "upper line bar move good but lower bar come in last only".
                Nothing is lost by hiding it — the sentence directly below says
                what it is waiting for ("the edit starts when the footage has
                landed"), which is more than an empty bar was saying.
                ⚠ AND IT IS THE SAME THICKNESS AS THE RENDER RAIL ABOVE IT. It
                was thinner, on the argument that the bar worth watching should
                be the bigger one; two bars of different weights stacked four
                lines apart read as two different KINDS of thing instead, which
                is not what they are — both are "how far through this is". */}
            {/* ⚠ `scoring` KEEPS THE RAIL ON SCREEN, FULL. The steps really are
                finished by then, so hiding it would take a completed bar away and
                put it back a few seconds later — which reads as the edit having
                been undone and re-done. */}
            {plan.steps.length > 0 && (stepping || scoring || finished) && (
              <div className="dir-rail" aria-label="Progress">
                <div
                  className="dir-rail-fill"
                  style={{
                    width: `${Math.min(100, (index / plan.steps.length) * 100)}%`,
                  }}
                />
              </div>
            )}
            <p className="dir-progress">
              {speaking
                ? "The edit starts when the sound has landed."
                : phase === "rendering"
                  ? "The edit starts when the footage has landed."
                  : phase === "blocking"
                    ? "The edit starts when the drawings have landed."
                    : phase === "anchoring"
                    ? "Re-anchoring the edit onto the film that came back…"
                    : scoring
                      ? "The edit is done — laying the sound on the finished film…"
                        : phase === "done"
                      ? resuming
                        ? "Done — the interrupted render is finished."
                        : `Done — ${log.filter((r) => r.state === "done").length} edits made.`
                      : phase === "stopped"
                        ? `Stopped at step ${index} of ${plan.steps.length}.`
                        : `Step ${Math.min(index + 1, plan.steps.length)} of ${plan.steps.length}${
                            phase === "paused" ? " — paused" : ""
                          }`}
            </p>
            <ol className="dir-log">
              {log.map((row, i) => (
                <li key={`${row.id}:${i}`} className={`dir-log-${row.state}`}>
                  <span className="dir-log-mark">
                    {row.state === "failed" ? "!" : row.state === "note" ? "·" : "✓"}
                  </span>
                  <span className="dir-log-text">{row.text}</span>
                  {row.why && <span className="dir-log-why">{row.why}</span>}
                </li>
              ))}
            </ol>
          </>
        )}

        {/* ------------------------------------------------ what it could not use */}
        {skipped.length > 0 && (
          <details className="dir-skipped">
            <summary>
              {skipped.length} step{skipped.length === 1 ? "" : "s"} couldn&apos;t be used
            </summary>
            <ul>
              {skipped.map((row, i) => (
                <li key={`${row.verb}:${row.index}:${i}`}>
                  <code>{row.verb}</code> — {row.why}
                </li>
              ))}
            </ul>
          </details>
        )}

        {/* ----------------------------------------------------------- buttons */}
        <div className="an-name-actions dir-actions">
          {phase === "brief" && (
            <>
              {/* ⚠ THE FREE DOOR IS A REAL BUTTON, NOT A FALLBACK YOU DISCOVER.
                  The rules planner reads the rhythm off the shot lengths, needs
                  no backend, no key and no quota, and on an animatic with uneven
                  holds it is genuinely good. Someone who does not want to send
                  their film anywhere should not have to find that out by having
                  the AI call fail. */}
              <button
                type="button"
                className="btn small"
                onClick={() => run.buildPlan()}
                title="No AI — reads the rhythm off the shot lengths"
              >
                Just the rhythm
              </button>
              {/* ⚠ THIS DOOR IS THE ONLY ONE `cap.director` GATES, and the one
                  beside it proves why that distinction matters: "Just the
                  rhythm" is the rules planner — no backend, no key, no quota,
                  nothing to refuse — so an account without the AI still has a
                  working 🎬 Make Video rather than a dialog with two dead
                  buttons. The server guards `POST /director/{id}/plan` and
                  nothing else; this matches it exactly. */}
              <button
                type="button"
                className={`btn primary ${directorCap.on ? "" : "cap-off"}`}
                disabled={!directorCap.on || !hasModel || !frames.length}
                onClick={() => run.buildPlan({ ai: true })}
                title={
                  directorCap.on
                    ? "Two text calls. Nothing is rendered and nothing is edited yet."
                    : directorCap.reason
                }
              >
                {directorCap.on ? "Read my film" : "🔒 Read my film"}
              </button>
            </>
          )}

          {phase === "preview" && (
            <>
              <button
                type="button"
                className="btn small"
                onClick={() => run.buildPlan({ ai: source === "ai" })}
              >
                Read it again
              </button>
              {/* ⚠ THE LABEL CARRIES THE COUNT, and that is the re-cost made
                  visible. Un-tick Effects and this button says a smaller number
                  before you press it — which is the only way a preview can prove
                  it is a preview of what will actually happen. */}
              <button
                type="button"
                className="btn primary"
                disabled={
                  !edits && !willSpeak && !willRender && !willBlock && !willSfx && !willMusic
                }
                onClick={run.start}
              >
                <Icon name="play" />{" "}
                {edits || willSpeak || willRender || willBlock || willSfx || willMusic
                  ? [
                      edits ? `Run this plan · ${edits} edit${edits === 1 ? "" : "s"}` : "Run this plan",
                      // ⚠ THE SPEND IS ON THE BUTTON ITSELF, IN MONEY WHERE THERE
                      // IS A NUMBER FOR IT. Un-ticking either pass takes it off
                      // before the button is pressed, which is the same promise
                      // the edit count makes: what this button says is what
                      // pressing it does.
                      willSpeak ? `${script.lines.length} spoken` : "",
                      willRender
                        ? quote.total?.usd > 0
                          ? `$${quote.total.usd.toFixed(2)} of footage`
                          : `${shoot.shots.length} rendered`
                        : "",
                      // ⚠ A COUNT OF DRAWINGS, NEVER A PRICE, because there is no
                      // dollar figure to give: this pass spends the account's image
                      // quota. `toDraw` and not `drawings` — what the storyboard
                      // already has is not charged for again, and a button quoting
                      // the bigger number would be quoting a bill nobody is sent.
                      willBlock
                        ? `${poses.toDraw} animatic image${poses.toDraw === 1 ? "" : "s"}`
                        : "",
                      // ⚠ A COUNT, NEVER A PRICE. The two segments above are
                      // about money; these two are about work that costs none, so
                      // they say what will be ADDED and stop there. Sliding a
                      // dollar figure in beside them would be the button pricing
                      // a free pass.
                      willSfx && sfx.cues.length
                        ? `${sfx.cues.length} sound effect${sfx.cues.length === 1 ? "" : "s"}`
                        : "",
                      willMusic ? "music" : "",
                    ]
                      .filter(Boolean)
                      .join(" · ")
                  : "Nothing to run"}
              </button>
            </>
          )}

          {/* ⚠ THE ONLY CONTROL PHASE C HAS, AND IT SAYS WHEN IT TAKES EFFECT.
              Pause and Step are absent for the same reason they are absent
              during the sound pass: there is nothing honest for them to do to a
              call that has already been made. Stop is different — it saves the
              passes that have NOT been submitted, which on a four-pass film is
              most of the money. See `veo_pass.js`. */}
          {/* ⚠ AND THE SOUND PASS GETS THE SAME ONE CONTROL, WITH THE MONEY
              SENTENCE TAKEN OUT. Stopping here loses nothing but the lookup in
              flight, so the button does not need to explain what is already paid
              for — it needs to say that the edit is safe, which is what a person
              pressing Stop at this point is actually worried about. */}
          {phase === "scoring" && (
            <button
              type="button"
              className="btn small dir-danger"
              disabled={Boolean(score?.stopping)}
              onClick={run.stop}
              title="The edit is already on the timeline — only the sound is skipped"
            >
              {score?.stopping ? "Stopping…" : "Skip the sound"}
            </button>
          )}

          {phase === "rendering" && (
            <button
              type="button"
              className="btn small dir-danger"
              disabled={Boolean(footage?.stopping)}
              onClick={run.stop}
              title="The pass in flight is already paid for and will be waited for"
            >
              {footage?.stopping ? "Stopping after this pass…" : "Stop after this pass"}
            </button>
          )}

          {/* ⚠ PHASE C2's ONE CONTROL, AND IT IS PER SHOT RATHER THAN PER PASS.
              That is the honest difference between this and the render's Stop: a
              shot here is a single storyboard run, so stopping saves every shot
              after it rather than every submission after it — and everything
              already drawn stays on the timeline, exactly as the 🖼 button's own
              ⏹ Stop promises. */}
          {phase === "blocking" && (
            <button
              type="button"
              className="btn small dir-danger"
              disabled={Boolean(blockRun?.stopping)}
              onClick={run.stop}
              title="The shot being drawn now is finished and kept — the rest are never asked for"
            >
              {blockRun?.stopping ? "Stopping after this shot…" : "Stop after this shot"}
            </button>
          )}

          {stepping && (
            <>
              {phase === "paused" && (
                <button type="button" className="btn small" onClick={run.stepOnce}>
                  Step ▸
                </button>
              )}
              <button
                type="button"
                className="btn small"
                onClick={phase === "paused" ? run.resume : run.pause}
              >
                {phase === "paused" ? "Resume" : "Pause"}
              </button>
              <button type="button" className="btn small dir-danger" onClick={run.stop}>
                Stop
              </button>
            </>
          )}

          {finished && (
            <>
              <button
                type="button"
                className="btn small"
                disabled={!run.canRevert}
                onClick={run.revert}
                title="Put the timeline back exactly as it was before the Director ran"
              >
                Revert it all
              </button>
              <button type="button" className="btn primary" onClick={tryClose}>
                Keep it
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
