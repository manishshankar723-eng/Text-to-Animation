// useDirectorRun.js — THE PHASE MACHINE. What the 🎬 button is wired to.
//
//   closed ──open──▶ brief ──read──▶ planning ──▶ preview ──run──▶ speaking
//                      │                 │           │                  │
//                      └──────cancel─────┴───────────┴──▶ closed        ▼
//                                                                   rendering
//                                                                        │
//                                                                        ▼
//                                                                   anchoring
//                                                                        │
//                                                                        ▼
//                                                          done ◀── running
//                                                             │  ▲
//                                                        pause│  │resume
//                                                             ▼  │
//                                                          paused
//                                                             │stop
//                                                             ▼
//                                                          stopped
//
// ---------------------------------------------------------------------------
// ⚠ THE TWO PAID PASSES BOTH RUN BEFORE THE STEPS, AND THE ORDER IS THE PRODUCT.
// ---------------------------------------------------------------------------
//   `speaking`   PHASE B — the voiceover. STRETCHES the shot that owns a line
//                and pushes every shot after it along, server side.
//   `rendering`  PHASE C — the Veo pass, in submissions of `MAX_VIDEO_BATCH`.
//                GROWS a shot to the length of the take rendered over it.
//   `anchoring`  FREE. Re-reads the film both of them left behind and re-anchors
//                the plan onto it, ONCE, over both.
//
// A plan applied before either would be a plan whose every timing decision is
// about a film that no longer exists — and the failure is invisible: the run
// reports 24 successful edits, all of them on the wrong moments. So the money
// goes first, the document is re-read, and the plan is re-anchored before a
// single step runs. The reasoning and all the arithmetic are in `voice_pass.js`
// and `veo_pass.js`; the async is here.
//
// ⚠ AND C FOLLOWS B RATHER THAN THE OTHER WAY ROUND, because the take's LENGTH
// is chosen from the shot's hold and the voiceover rewrites the holds. A shot
// stretched to 9.3s to carry its line wants the 8-second take; the same shot
// priced before the pass would have been given 4 and would end on a drawing.
//
// ---------------------------------------------------------------------------
// ⚠ PHASE C IS THE ONE PLACE STOPPING IS NOT HONEST, AND THE MACHINE SAYS SO.
// ---------------------------------------------------------------------------
// A submission of twelve renders is billed the moment it leaves; nothing the
// browser does afterwards un-spends it. So Stop is read BETWEEN passes and
// nowhere else — `stopRef` is checked at the top of each one — and while a pass
// is in flight the panel offers exactly what it can honestly do: waiting, and a
// Stop that takes effect when this pass lands. See `veo_pass.js`.
//
// ---------------------------------------------------------------------------
// ⚠ AND A RUN THAT DIES HALFWAY CAN BE PICKED UP, FROM THE SERVER.
// ---------------------------------------------------------------------------
// `POST /director/{id}/veo/start` writes down what the pass MEANT to render
// before the first submission goes, and `veo_clips` records what was actually
// paid for. `resumeVeo` reads both and renders the difference — so a refresh, a
// crash or a closed laptop costs the passes that had not gone yet and not one
// clip more. ⚠ IT FINISHES THE FOOTAGE, NOT THE EDIT: the plan lives in this
// browser and died with it, which the panel says out loud rather than pretending
// a resumed run is the same run.
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
//
// ---------------------------------------------------------------------------
// ⚠ THE MODEL PLUGS IN AT ONE PLACE, AND THIS IS IT: `buildPlan`.
// ---------------------------------------------------------------------------
// Phase 2 added a second planner beside the rules one, and NOTHING BELOW
// `buildPlan` KNOWS WHICH ANSWERED. A plan is a plan: it goes through
// `validatePlan` → `applyGuardrails` → the runner whether a language model or a
// hundred lines of arithmetic wrote it, which is exactly what Phase 0's header
// promised the seam would be.
//
// ⚠ AND `housePlan` DID NOT GO AWAY. A failed call, a backend that is not
// running, a laptop on a train — all of them fall back to the deterministic
// planner and SAY SO in the preview, rather than showing an error where a plan
// should be. A pass that reads the rhythm is worth having when the pass that
// reads the story cannot be had.
//
// ⚠ RE-COSTING A TICK BOX DOES NOT CALL THE MODEL AGAIN. The raw plan is kept
// and re-validated against the new include flags, which is a pure function of
// what is already in memory. Un-ticking "Effects" to see what the film looks
// like without them must be instant and must be free — if it cost a call, and a
// wait, nobody would ever try it.

import { useCallback, useEffect, useRef, useState } from "react";

import { capabilities } from "./capabilities.js";
import { describeStep, ACTIONS, ACTION_API } from "./actions.js";
import { applyGuardrails, housePlan } from "./house_style.js";
import { defaultInclude, emptyPlan, planTotals, validatePlan } from "./plan_schema.js";
import { reanchor, scriptFor, shiftsOf, speechDue, spokenWords } from "./voice_pass.js";
import {
  chunkPasses,
  growthCauses,
  outstanding,
  shotRow,
  veoDue,
  veoShots,
} from "./veo_pass.js";

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

/** An empty quote. The shape `server/director.py` returns for the Veo pass. */
const NO_COST = { shots: 0, seconds: 0, usd: 0, tier: "", resolution: "" };

/** No script yet, and the shape `scriptFor` returns. */
const NO_SCRIPT = { lines: [], written: false, skipped: [] };

/** Nothing to render, and the shape `veoShots` returns. */
const NO_SHOOT = { shots: [], skipped: [] };

/** No quote yet. The shape `POST /director/{id}/veo/quote` returns. */
const NO_QUOTE = { batch: 0, passes: [], total: NO_COST };

/**
 * THE FILM AS THE DIRECTOR COUNTS IT, out of anything the server hands back.
 *
 * ⚠ A TAKE IS NOT A SHOT. `absorbSpeech` and the project read both return the
 * WHOLE picture row — panels and the takes sitting over them — while the
 * editor's `readCtx` has already had the takes filtered out of it. Comparing one
 * against the other would report every take as a shot that appeared from
 * nowhere. See `shotRow` in `veo_pass.js`.
 */
const shotsOf = (frames) => shotRow(frames || [], []).frames;

export default function useDirectorRun({
  readCtx,
  api,
  applySnapshot,
  docRef,
  onNotice,
  // ⚠ OPTIONAL, AND THE WHOLE FEATURE WORKS WITHOUT THEM. Handed no `askModel`,
  // this is Phase 0 exactly: the rules planner, no network, no spend. That is
  // what `tests/director_guardrails_check.py` exercises and what the editor
  // falls back to the moment a call fails.
  askModel,
  language = "",
  // ⚠ PHASE B, AND BOTH ARE OPTIONAL FOR THE SAME REASON `askModel` IS. Handed
  // neither, this is Phase 0 exactly: the plan runs, nothing is called and
  // nothing is spent. `tests/director_guardrails_check.py` and the whole
  // rules-only path never supply them.
  //
  //   `readScript`  FREE, calls no model: the board's dialogue sheet, so the
  //                 preview can show what would be read before it is priced.
  //   `speak`       SPENDS. Runs `/voiceover` to completion, re-reads the
  //                 project, and resolves with the picture row that came back —
  //                 which is what the re-anchor is computed from.
  readScript,
  speak,
  // ⚠ PHASE C, AND ALL SIX ARE OPTIONAL FOR THE SAME REASON `askModel` IS.
  // Handed none of them, this is Phase 3 exactly: the plan runs, the sound is
  // read and nothing is rendered. `tests/director_guardrails_check.py` and the
  // whole rules-only path never supply them.
  //
  //   `quoteVeo`   FREE. Prices a shot list pass by pass, so the preview can
  //                say what Run would cost BEFORE it is pressed.
  //   `startVeo`   FREE. Opens the resumable record, before a penny moves.
  //   `renderPass` SPENDS. Submits ONE pass of `MAX_VIDEO_BATCH`, polls it to
  //                the end, attaches what came back and resolves with the
  //                picture row the editor now holds.
  //   `endVeo`     FREE. Closes the record — done / stopped / failed.
  //   `veoRender`  the project's `RenderSettings`. The Director overrides only
  //                `duration_seconds`, per shot, and leaves tier, resolution and
  //                sound exactly where the user set them.
  //   `pendingVeo` `{ run, clips }` off the loaded project when a pass was
  //                interrupted, else null. This is the whole resume offer.
  quoteVeo,
  startVeo,
  renderPass,
  endVeo,
  veoRender,
  pendingVeo = null,
  // The server's own `veo_clips`, so the preview can say which shots are already
  // paid for and skip them rather than quoting for them twice.
  veoClips = [],
}) {
  const [phase, setPhase] = useState("closed");
  // ⚠ AND AS A REF, for `stop` alone. Stop means two different things depending
  // on which phase it is pressed in, and a `useCallback` that closed over
  // `phase` would be reading the phase as it was when the handler was made.
  const phaseRef = useRef("closed");
  phaseRef.current = phase;
  const [plan, setPlan] = useState(emptyPlan);
  const [dropped, setDropped] = useState([]);
  const [trimmed, setTrimmed] = useState([]);
  const [log, setLog] = useState([]);
  const [index, setIndex] = useState(0);

  // ---------------------------------------------------------------- the brief
  // What the user tells the Director before it reads. Both are held here rather
  // than in the panel because "Read it again" re-sends them, and a dialog that
  // forgot the sentence you typed the first time is a dialog you type into once.
  const [brief, setBrief] = useState("");
  const [tongue, setTongue] = useState(language || "");
  useEffect(() => setTongue(language || ""), [language]);

  // ⚠ WHO WROTE THE PLAN, AND SAID OUT LOUD. "ai" | "house" — the preview prints
  // it, because a rules plan and a story plan are different products and a user
  // comparing two runs has to know which they are looking at.
  const [source, setSource] = useState("house");
  const [why, setWhy] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [veo, setVeo] = useState([]);
  const [cost, setCost] = useState(NO_COST);
  const [include, setIncludeState] = useState(defaultInclude);

  // ------------------------------------------------------------- phase B
  // ⚠ THE SCRIPT IS READ AT PREVIEW TIME, NOT AT RUN TIME, and reading it is
  // free. The Run button has to be able to say "and reads 12 lines aloud" BEFORE
  // it is pressed — a button that spends without having said what on is the one
  // thing every other paid path in this editor refuses to be.
  const [script, setScript] = useState(NO_SCRIPT);
  // ⚠ AND AS A REF. `recost` re-prices phase C off the script, and it runs from
  // a click handler whose closure is one render old.
  const scriptRef = useRef(NO_SCRIPT);
  scriptRef.current = script;
  // What phase B is doing, while it does it: `{ stage, message, lines }`.
  const [speech, setSpeech] = useState(null);
  // The picture row as it stood when Run was pressed. The re-anchor is the
  // difference between this and the row the passes left behind, so it is
  // captured before the first call and read after the last one.
  const beforeRef = useRef([]);
  // ⚠ AND THE ROW BETWEEN THE TWO PAID PASSES, which exists for one reason: a
  // dropped step has to name the pass that actually moved its shot. `before` vs
  // `afterSpeech` is the voiceover's doing; `afterSpeech` vs now is Veo's. See
  // `growthCauses`.
  const speechRowRef = useRef([]);

  // ------------------------------------------------------------- phase C
  // What would be rendered, and what it would cost. Both are worked out at
  // PREVIEW time for the same reason the script is: the Run button has to be
  // able to say "· $34.56 of footage" before it is pressed.
  const [shoot, setShoot] = useState(NO_SHOOT);
  const [quote, setQuote] = useState(NO_QUOTE);
  // ⚠ THE CLIP RECORDS, AS A REF. `loadShoot` asks which shots are already paid
  // for, and it is called from inside a promise chain — a value captured at the
  // render that started the chain would be the list as it was before the pass
  // that just finished wrote to it.
  const veoClipsRef = useRef([]);
  veoClipsRef.current = veoClips || [];
  // What phase C is doing, while it does it: `{ stage, message, pass, passes }`.
  // Named for the thing it is making, not for `RenderSettings` — `veoRender` is
  // one line up and the two are easy to confuse to nobody's benefit.
  const [footage, setFootage] = useState(null);
  // ⚠ THE HARD STOP, AND IT IS A REF BECAUSE THE LOOP IS A CLOSURE. Set by
  // `stop()` while a pass is in flight and read at the TOP of the next one —
  // never mid-pass, because a submission that has gone is billed whatever
  // happens next. See the header.
  const stopRef = useRef(false);
  // The run record `startVeo` opened, so `endVeo` can close the right one.
  const runRef = useRef(null);
  // The shots as the RUN resolved them, which is not always what the preview
  // priced: phase B can stretch a shot up a size. See `resolveShoot`.
  const shootRef = useRef([]);

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
  // ⚠ THE PLAN AS IT ARRIVED, before validation and before the fence. Kept so a
  // tick box can re-cost without another model call — see the header.
  const rawRef = useRef(null);
  // ⚠ THE MOTION PROMPTS, FOR THE SAME REASON. Un-ticking a box re-prices the
  // Veo pass off what is already in memory; nothing about that may cost a call.
  const veoRef = useRef([]);
  // ⚠ DID THIS RUN SPEND ANYTHING? Only phase B can set it, and only the notices
  // read it. Phase 0's "nothing was spent" is still true of most runs and must
  // keep being said on those — a blanket warning teaches the user to ignore it.
  const spentRef = useRef(false);

  const clearTimer = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  useEffect(() => clearTimer, []);

  // ------------------------------------------------------------------- plan
  /**
   * Read a raw plan into the checked, fenced one the preview shows.
   *
   * Pure, and the ONLY path from a raw plan to a shown plan — the rules planner
   * and the model both come through here, which is what makes "the fence has no
   * gate in it" true rather than aspirational.
   */
  const adopt = useCallback(
    (raw, nextInclude) => {
      const caps = capabilities();
      const ctx = { ...readCtx(), caps };
      const withFlags = { ...raw, include: nextInclude };
      const checked = validatePlan(withFlags, caps, ctx);
      const fenced = applyGuardrails(checked.plan, ctx);
      setPlan(fenced.plan);
      setDropped(checked.dropped);
      setTrimmed(fenced.trimmed);
      setLog([]);
      setIndex(0);
      return { plan: fenced.plan, dropped: checked.dropped, trimmed: fenced.trimmed };
    },
    [readCtx]
  );

  /**
   * WHAT PHASE B WOULD READ ALOUD. Free, and it calls no model.
   *
   * ⚠ ASKED FOR ONCE PER PLAN, and asked for even when Voiceover is un-ticked,
   * because the tick box has to be able to say what ticking it would DO. The
   * board's sheet is a read of the storyboard plus a keyword guess at who each
   * speaker is; nothing about it is worth hiding behind a switch.
   *
   * ⚠ AND A FAILURE HERE IS NOT AN ERROR. No sheet means no script means phase B
   * is not due, which is the same state as a board with no dialogue in it — the
   * plan still runs, and it runs silently.
   */
  const loadScript = useCallback(
    async (reading) => {
      const frames = readCtx().frames || [];
      let next = scriptFor({ sheet: [], analysis: reading, frames });
      if (readScript) {
        try {
          const sheet = await readScript();
          next = scriptFor({ sheet: sheet?.lines || [], analysis: reading, frames });
        } catch {
          /* no sheet is not an error — see above */
        }
      }
      setScript(next);
      // ⚠ RETURNED AS WELL AS SET, because phase C's quote depends on it: a shot
      // that will be spoken over is priced at the longest take, and reading that
      // off the `script` STATE would read the script from before this call.
      return next;
    },
    [readCtx, readScript]
  );

  /**
   * WHICH SHOTS PHASE B WILL BE READING OVER — the shots phase C must quote high.
   *
   * Empty when the voiceover is un-ticked: nothing will stretch, so nothing has
   * to be quoted for a stretch that is not coming.
   */
  const spokenOver = useCallback(
    (nextInclude, nextScript) =>
      nextInclude && nextInclude.voiceover === false
        ? new Set()
        : new Set(((nextScript && nextScript.lines) || []).map((l) => l.frame_id).filter(Boolean)),
    []
  );

  /**
   * WHAT PHASE C WOULD RENDER, AND WHAT IT WOULD COST. Free, and calls no model.
   *
   * ⚠ ASKED FOR ONCE PER PLAN, and asked for even when Veo is un-ticked, because
   * the tick box has to be able to say what ticking it would DO. It is the most
   * expensive box in this panel by an order of magnitude and it is the only one
   * that starts un-ticked; a price that appears only after you have already
   * agreed to pay it is not a price.
   *
   * ⚠ A SHOT THAT WILL BE SPOKEN OVER IS PRICED AT THE LONGEST TAKE. The
   * voiceover runs FIRST and stretches the shot each line sits on, and the take
   * for a stretched shot is a bigger one — so a quote read off today's holds
   * would be a quote for a film phase B is about to replace. Rather than tell
   * the user a number that can only go up, those shots are priced at 8 seconds
   * and the panel says so: the run then costs the same or LESS than the button
   * said, which is the only direction a surprise may go.
   *
   * ⚠ AND A FAILURE HERE IS NOT AN ERROR. No quote means the box cannot be
   * priced, so it cannot honestly be offered — `veoDue` says why and the plan
   * still runs, without footage.
   */
  const loadShoot = useCallback(
    async (prompts, spoken) => {
      const frames = readCtx().frames || [];
      const done = new Set(
        (veoClipsRef.current || [])
          .filter((c) => c && c.status === "ready" && c.upload_id && c.frame_id)
          .map((c) => c.frame_id)
      );
      const next = veoShots({ veo: prompts || [], frames, done });
      // ⚠ THE ASSUMPTION IS MARKED ON THE SHOT, not applied silently, so the
      // panel can list which shots it is quoting high and why.
      const priced = next.shots.map((row) =>
        spoken && spoken.has(row.frame_id) && row.seconds !== 8
          ? { ...row, seconds: 8, assumed: true }
          : row
      );
      setShoot({ shots: priced, skipped: next.skipped });
      if (!quoteVeo || !priced.length) {
        setQuote(NO_QUOTE);
        return;
      }
      try {
        setQuote(await quoteVeo({ shots: priced, render: veoRender }));
      } catch {
        setQuote(NO_QUOTE);
      }
    },
    [quoteVeo, readCtx, veoRender]
  );

  /**
   * ⚠ THE HOUSE PLANNER IS RE-RUN, THE MODEL'S PLAN IS RE-READ, and the
   * difference matters. `housePlan` takes the include flags as INPUT — turning
   * Transitions off changes which steps it writes, and re-running it costs
   * nothing. A model's plan cannot be re-asked for free, so its raw steps are
   * kept and simply re-validated: `validatePlan` drops the ones whose governor
   * is off, which is the same answer without the call.
   */
  const recost = useCallback(
    (nextInclude) => {
      setIncludeState(nextInclude);
      const raw =
        rawRef.current && rawRef.current.source === "ai"
          ? rawRef.current.plan
          : housePlan({ ...readCtx(), caps: capabilities() }, { include: nextInclude });
      // ⚠ UN-TICKING VOICEOVER RE-PRICES THE VEO PASS, and that is not an
      // accident of ordering: the two boxes interact. With the sound on, every
      // spoken shot is quoted at the longest take because phase B may stretch
      // it; with the sound off, nothing will, and those shots drop back to what
      // their holds actually ask for. The price on screen has to move when the
      // box that changes it moves, or the preview is not a preview.
      loadShoot(veoRef.current, spokenOver(nextInclude, scriptRef.current));
      return adopt(raw, nextInclude);
    },
    [adopt, loadShoot, readCtx, spokenOver]
  );

  const setInclude = useCallback(
    (key, on) => recost({ ...include, [key]: Boolean(on) }),
    [include, recost]
  );

  /**
   * THE RULES PLANNER. Free, offline, deterministic — and the fallback.
   */
  const buildHousePlan = useCallback(
    (options = {}) => {
      const nextInclude = { ...include, ...(options.include || {}) };
      const raw = housePlan({ ...readCtx(), caps: capabilities() }, { include: nextInclude });
      rawRef.current = { source: "house", plan: raw };
      setSource("house");
      setAnalysis(null);
      setVeo([]);
      setCost(NO_COST);
      setIncludeState(nextInclude);
      const out = adopt(raw, nextInclude);
      setPhase("preview");
      // ⚠ THE RULES PLANNER WRITES NO WORDS, so the only script it can ever have
      // is the BOARD's own. `scriptFor` is handed no reading and falls back to
      // nothing, which is the honest answer: "Just the rhythm" does not invent
      // dialogue any more than it invents titles.
      //
      // ⚠ AND IT WRITES NO MOTION PROMPTS EITHER, so phase C has nothing to
      // render and the Veo box says exactly that. Arithmetic can tell which
      // shots were HELD; it cannot tell what should happen inside one.
      veoRef.current = [];
      loadScript(null).then((next) => loadShoot([], spokenOver(nextInclude, next)));
      return out;
    },
    [adopt, include, loadScript, loadShoot, readCtx, spokenOver]
  );

  /**
   * THE MODEL. Two calls on the server; this end sends the board and the
   * vocabulary and reads back a plan.
   *
   * ⚠ THE MANIFEST GOES WITH THE REQUEST. `capabilities()` is derived from the
   * renderers' own tables, so it is the only honest statement of what this build
   * can do — and it is JavaScript. Sending it means the model is told the truth
   * about THIS build rather than a server-side guess about it.
   *
   * ⚠ A FAILURE IS NOT AN ERROR SCREEN. It falls through to the rules planner
   * and the preview says which one it is looking at, because a film cut on
   * rhythm is worth having when the backend is down.
   */
  const buildAiPlan = useCallback(
    async (options = {}) => {
      const nextInclude = { ...include, ...(options.include || {}) };
      setPhase("planning");
      setWhy("");
      const ctx = readCtx();
      try {
        const answer = await askModel({
          board: boardFrom(ctx),
          capabilities: capabilities(),
          include: nextInclude,
          language: options.language ?? tongue,
          brief: options.brief ?? brief,
        });
        const raw = answer?.plan || {};
        if (!Array.isArray(raw.steps) || !raw.steps.length) {
          throw new Error("the model came back with no steps");
        }
        rawRef.current = { source: "ai", plan: raw };
        setSource("ai");
        setAnalysis(answer.analysis || null);
        setVeo(answer.veo || []);
        setCost(answer.cost || NO_COST);
        setIncludeState(nextInclude);
        // ⚠ WHAT THE SERVER ALREADY THREW AWAY IS SHOWN TOO. `director.py` drops
        // a step whose verb does not exist and a caption in the wrong script
        // before the browser sees it; those reasons belong in the same list as
        // the ones the client validator produces, or the count under the table
        // is a lie about how much of the model's plan survived.
        const out = adopt(raw, nextInclude);
        setDropped([...(answer.dropped || []), ...out.dropped]);
        setPhase("preview");
        // ⚠ THE READING IS WHAT MAKES A SILENT BOARD SPEAKABLE. The analyse call
        // returns a `dialogue` line per shot because it had to know what is said
        // in order to write the Veo prompts — so on a board with no dialogue of
        // its own, that is the script, and it costs nothing extra to use.
        //
        // ⚠ AND THE SHOOT IS RESOLVED AFTER IT, not beside it. Phase C's quote
        // depends on which shots phase B will stretch, so it cannot be worked
        // out until the script is known.
        veoRef.current = answer.veo || [];
        loadScript(answer.analysis || null).then((next) =>
          loadShoot(veoRef.current, spokenOver(nextInclude, next))
        );
        return out;
      } catch (err) {
        const out = buildHousePlan({ include: nextInclude });
        setWhy(err?.message || "the AI pass could not be reached");
        return out;
      }
    },
    [
      adopt,
      askModel,
      brief,
      buildHousePlan,
      include,
      loadScript,
      loadShoot,
      readCtx,
      spokenOver,
      tongue,
    ]
  );

  /**
   * Read the timeline and write a plan. Nothing is touched, either way.
   *
   * `{ ai: true }` asks the model; anything else is the rules. The panel picks —
   * see the two buttons on the brief.
   */
  const buildPlan = useCallback(
    (options = {}) =>
      options.ai && askModel ? buildAiPlan(options) : buildHousePlan(options),
    [askModel, buildAiPlan, buildHousePlan]
  );

  /** 🎬 opens on the BRIEF, not on a plan — popup one asks before it reads. */
  const open = useCallback(() => {
    rawRef.current = null;
    setPlan(emptyPlan());
    setDropped([]);
    setTrimmed([]);
    setLog([]);
    setIndex(0);
    setWhy("");
    setAnalysis(null);
    setVeo([]);
    setCost(NO_COST);
    setIncludeState(defaultInclude());
    setScript(NO_SCRIPT);
    setSpeech(null);
    veoRef.current = [];
    shootRef.current = [];
    runRef.current = null;
    stopRef.current = false;
    resumingRef.current = null;
    setShoot(NO_SHOOT);
    setQuote(NO_QUOTE);
    setFootage(null);
    setPhase("brief");
  }, []);

  const close = useCallback(() => {
    clearTimer();
    setPhase("closed");
  }, []);

  // -------------------------------------------------------------------- run
  /** Is there a sound pass to run, and if not, why not. Read by the panel too. */
  const due = speechDue(include, script);
  const willSpeak = Boolean(speak) && due.due;
  /** The same question for phase C, and the same contract: a reason, not a flag. */
  const shootDue = veoDue(include, shoot.shots);
  const willRender = Boolean(renderPass) && shootDue.due;
  // ⚠ AS A REF, because the phase-B effect decides where to go next from inside
  // a promise chain that started a minute ago. A value captured then would be
  // "was Veo ticked when the voiceover began", which is the same question — but
  // only by accident, and only until someone makes the tick box live mid-run.
  const willRenderRef = useRef(false);
  willRenderRef.current = willRender;

  const start = useCallback(() => {
    if (!plan.steps.length && !willSpeak && !willRender) return;
    // ⚠ THE SNAPSHOT IS TAKEN HERE, not when the panel opened. Between opening
    // the preview and pressing Run the user can still edit — and reverting to
    // the document as it was before they did would throw away work the Director
    // never touched.
    //
    // ⚠ AND IT IS TAKEN BEFORE PHASE B, which is what makes Revert able to undo
    // a voiceover's ripple: the take, its captions and the whole re-laid picture
    // row are all document state, so putting the document back puts all three
    // back. What it cannot undo is the SPEND — that is said out loud in the
    // notice, because a Revert the user reads as a refund is a Revert that lies.
    snapshotRef.current = docRef.current;
    beforeRef.current = readCtx().frames || [];
    // Nothing has moved yet, so the row "after phase B" starts as the row before
    // it. On a run with no voiceover it stays that way, and `growthCauses` then
    // correctly blames every stretch on Veo.
    speechRowRef.current = beforeRef.current;
    stopRef.current = false;
    runRef.current = null;
    shootRef.current = shoot.shots;
    setCanRevert(false);
    refsRef.current = {};
    stepsRef.current = plan.steps;
    setLog([]);
    setIndex(0);
    setSpeech(null);
    setFootage(null);
    // ⚠ THE PAID PASSES FIRST, ALWAYS, AND B BEFORE C. See the header for why
    // neither may follow the steps and why C may not precede B.
    setPhase(willSpeak ? "speaking" : willRender ? "rendering" : "running");
  }, [plan, docRef, readCtx, shoot, willSpeak, willRender]);

  const pause = useCallback(() => {
    clearTimer();
    setPhase((p) => (p === "running" ? "paused" : p));
  }, []);

  const resume = useCallback(() => {
    setPhase((p) => (p === "paused" ? "running" : p));
  }, []);

  const stop = useCallback(() => {
    clearTimer();
    // ⚠ MID-RENDER, STOP IS A REQUEST AND NOT AN ACT, and the difference is the
    // whole reason it is written out here. A submission of twelve renders is
    // billed the moment it leaves; dropping the poll would lose the clips
    // without saving the money. So the flag is raised, the pass in flight is
    // seen through, and the next one never goes — which is exactly what the
    // button now says it will do. See `veo_pass.js`.
    if (phaseRef.current === "rendering") {
      stopRef.current = true;
      setFootage((was) =>
        was ? { ...was, stopping: true } : { stage: "rendering", stopping: true }
      );
      if (onNotice) {
        onNotice(
          "Stopping after this pass. The renders already submitted are paid for either " +
            "way, so they are being waited for rather than thrown away."
        );
      }
      return;
    }
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
    if (onNotice) {
      // ⚠ REVERT IS NOT A REFUND, AND THE NOTICE REFUSES TO IMPLY ONE. The
      // timeline goes back exactly — the take, its captions, the re-laid picture
      // row and the rendered footage are all document state, so one snapshot
      // undoes all of it. The SPEND is not undone, and a user who reads "Revert
      // puts it all back" after a paid run is a user who runs it twice.
      onNotice(
        spentRef.current
          ? "Reverted — the timeline is exactly as it was before the Director ran. " +
              "What was paid for is still paid for: running again reads the dialogue " +
              "and renders the footage a second time."
          : "Reverted — the timeline is exactly as it was before the Director ran."
      );
    }
  }, [applySnapshot, onNotice]);

  // ----------------------------------------------------------- THE RESUME
  /**
   * ⚠ IS THIS RUN FINISHING AN OLD PASS RATHER THAN MAKING A NEW FILM? The shots
   * it still owes, or null. Read by the phase-C effect, which skips the
   * re-anchor and the steps when it is set — there are none of either.
   */
  const resumingRef = useRef(null);

  /**
   * WHAT AN INTERRUPTED PASS STILL OWES — free, and read off the server.
   *
   * ⚠ THE RECORD SAYS WHAT WAS INTENDED, THE CLIPS SAY WHAT WAS PAID FOR, and
   * this is the difference. A shot with a ready clip is money already spent and
   * is never re-submitted; a shot whose render FAILED is reported rather than
   * retried, because Veo bills a failure exactly as it bills a success and an
   * automatic retry on every reopen is a loop that spends. See `outstanding`.
   */
  const pending =
    pendingVeo && pendingVeo.run && (pendingVeo.run.status || "") === "running"
      ? outstanding(pendingVeo.run.shots || [], pendingVeo.clips || [])
      : null;

  /**
   * FINISH IT. Renders only what the interrupted pass never submitted.
   *
   * ⚠ IT FINISHES THE FOOTAGE, NOT THE EDIT, and the panel says so rather than
   * letting the user believe otherwise. A plan is written in this browser and
   * never persisted — deliberately, because it is a preview of an edit the user
   * has to be able to read and re-cost before agreeing to it, and a half-applied
   * plan restored from a server is neither. So what survives a crash is the
   * thing that cost money: the takes. The edit can simply be asked for again,
   * for free, once they are on the timeline.
   */
  const resumeVeo = useCallback(() => {
    if (!pending || !pending.todo.length || !renderPass) return;
    snapshotRef.current = docRef.current;
    beforeRef.current = readCtx().frames || [];
    speechRowRef.current = beforeRef.current;
    stopRef.current = false;
    resumingRef.current = pending.todo;
    shootRef.current = pending.todo;
    stepsRef.current = [];
    setPlan(emptyPlan());
    setLog([]);
    setIndex(0);
    setSpeech(null);
    setFootage(null);
    setCanRevert(false);
    setPhase("rendering");
  }, [docRef, pending, readCtx, renderPass]);

  // ------------------------------------------------------------- PHASE B
  /**
   * HAS THE EDITOR ACTUALLY GOT THE NEW PICTURE ROW YET?
   *
   * Polls the read-model until it matches the row the pass returned. Compared by
   * id AND length, because the ids never change across the pass — only the
   * lengths do — so a comparison on ids alone would report "settled" against the
   * document the plan already knows about.
   *
   * Two seconds is far longer than a React commit and short enough that a build
   * where this never settles carries on rather than hanging: re-anchoring
   * against a stale read is bad, and a Director that stops dead is worse.
   */
  const settled = useCallback(
    async (after) => {
      const want = (after || []).map((f) => `${f.id}:${f.duration_ms}`).join("|");
      for (let tries = 0; tries < 40; tries += 1) {
        const now = (readCtx().frames || []).map((f) => `${f.id}:${f.duration_ms}`).join("|");
        if (now === want) return true;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((done) => setTimeout(done, 50));
      }
      return false;
    },
    [readCtx]
  );

  /**
   * THE SOUND. See the header: this runs BEFORE the steps, because it moves the
   * pictures the steps are about to make decisions about.
   *
   * ⚠ IT NO LONGER RE-ANCHORS — `anchoring` does, after the LAST paid pass. Phase
   * C moves the same pictures again, so re-anchoring here would re-ask the rules
   * planner about a film that is one render pass away from changing, and
   * re-validate the model's steps against a document about to be re-laid.
   *
   * ⚠ ONE EFFECT, KEYED ON THE PHASE ALONE. Exactly the reason written over the
   * Veo and speech polls in `AnimaticEditor.jsx`: an effect that restarts on
   * what its own call writes cancels itself mid-flight, and by then the pass has
   * been paid for. Nothing in this dependency list changes while it runs.
   */
  useEffect(() => {
    if (phase !== "speaking") return undefined;
    let alive = true;
    setSpeech({ stage: "reading", message: `Reading ${script.lines.length} line${script.lines.length === 1 ? "" : "s"} aloud…` });

    (async () => {
      let after = null;
      let failed = "";
      try {
        const result = await speak({
          lines: script.lines,
          // ⚠ NOT AN OPTION, AND THE ⚠ AT THE TOP OF `voice_pass.js` IS WHY. With
          // `fit_shots` off a long line simply runs over the next four shots,
          // which is the bug that pass was built to fix — and this is the one
          // caller that can afford it, because it re-anchors afterwards.
          fitShots: true,
          // The spoken lines land as captions at the times they were ACTUALLY
          // read, which is the only source of caption timings anywhere in this
          // app that is measured rather than predicted.
          addCaptions: include.captions !== false,
        });
        after = result?.frames || null;
      } catch (err) {
        failed = err?.message || "the voiceover pass could not be reached";
      }
      if (!alive) return;

      if (after) {
        spentRef.current = true;
        setCanRevert(true);
        // ⚠ AND NOW WAIT FOR THE COMMIT. `speak` resolves the moment the editor
        // has been HANDED the server's answer — but a `setState` from an async
        // continuation is SCHEDULED, not applied, so `readCtx()` on the next
        // line would still be the film as it was a minute ago. Re-anchoring
        // against that read produces a plan for the film that has just stopped
        // existing, which is the exact failure this whole phase is here to
        // prevent, one level down. It is the same reason every step in the run
        // below is scheduled 90ms after the last one rather than looped.
        //
        // ⚠ AND IT IS A REAL BUG THIS CAUGHT, not a precaution: without it the
        // dissolves landed on the pre-voiceover cuts, every step reported
        // success, and `tests/editor_director_check.py` was the only thing that
        // noticed. Do not "simplify" it back to a straight read.
        await settled(shotsOf(after));
      }

      // ⚠ THE ROW PHASE B LEFT BEHIND, KEPT. The re-anchor happens once, after
      // BOTH paid passes — but a step dropped because its shot was stretched has
      // to name the pass that stretched it, and this is the only moment at which
      // the voiceover's half of that is knowable. See `growthCauses`.
      speechRowRef.current = shotsOf(after || readCtx().frames || []);

      setSpeech({
        stage: failed ? "failed" : "done",
        message: failed
          ? `The voiceover didn’t run (${failed}) — the edit is being applied to the film as it stands.`
          : `${script.lines.length} line${script.lines.length === 1 ? "" : "s"} read.`,
        lines: script.lines.length,
      });
      // ⚠ NO RE-ANCHOR HERE ANY MORE. It moved to `anchoring`, which runs after
      // the LAST paid pass rather than after this one — re-anchoring twice would
      // re-ask the rules planner against a film phase C is about to change again,
      // and re-validate the model's steps against a document that is one pass
      // stale. One pass of arithmetic over both is the whole point of the phase.
      setPhase(willRenderRef.current ? "rendering" : "anchoring");
    })();

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // ------------------------------------------------------------- PHASE C
  /**
   * THE FOOTAGE, PASS BY PASS. This is the expensive one.
   *
   * ⚠ ONE EFFECT, KEYED ON THE PHASE ALONE — the same rule the speech effect
   * above and the two polls in `AnimaticEditor.jsx` follow, and here it is worth
   * money rather than merely worth correctness: an effect that restarted on
   * something its own passes write would tear down the loop between submissions,
   * leaving twelve renders paid for and nothing waiting to attach them.
   *
   * ⚠ THE ORDER INSIDE IT IS RECORD → SUBMIT → POLL → NEXT, and the record comes
   * first on purpose. A run written down after the money moved would be missing
   * exactly the runs that need it: the ones that die on pass one.
   */
  useEffect(() => {
    if (phase !== "rendering") return undefined;
    let alive = true;

    (async () => {
      // ⚠ RE-RESOLVED AGAINST THE FILM PHASE B LEFT BEHIND, never taken from the
      // preview. The take's length is chosen from the shot's hold and the
      // voiceover has just rewritten the holds — which is the entire reason C
      // follows B. The preview quoted every spoken shot at the longest take, so
      // this can only ever come out the same or CHEAPER than the button said.
      const ctx = readCtx();
      const paid = new Set(
        (veoClipsRef.current || [])
          .filter((c) => c && c.status === "ready" && c.upload_id && c.frame_id)
          .map((c) => c.frame_id)
      );
      const fresh = resumingRef.current
        ? { shots: resumingRef.current, skipped: [] }
        : veoShots({ veo: veoRef.current, frames: ctx.frames || [], done: paid });
      shootRef.current = fresh.shots;

      if (!fresh.shots.length) {
        setFootage({
          stage: "done",
          message: "Every shot with a motion prompt already has a take — nothing was rendered.",
        });
        setPhase(resumingRef.current ? "done" : "anchoring");
        return;
      }

      // ⚠ NOTHING IS SPENT UNTIL THE RECORD IS WRITTEN. If this call fails there
      // is no way to pick the run up again, and a Veo pass that cannot be
      // resumed is precisely the thing this phase was built to stop being. So a
      // failure here skips the footage rather than rendering it unprotected —
      // the edit still runs, and the panel says why there is no film under it.
      let run = null;
      try {
        run = startVeo ? await startVeo({ shots: fresh.shots, render: veoRender }) : null;
        runRef.current = run;
      } catch (err) {
        if (!alive) return;
        setFootage({
          stage: "failed",
          message:
            `The render pass could not be opened (${err?.message || "the server did not answer"}), ` +
            "so nothing was submitted. Nothing has been spent.",
        });
        setPhase(resumingRef.current ? "done" : "anchoring");
        return;
      }

      const batch = (run && run.batch) || quote.batch || fresh.shots.length;
      const passes = chunkPasses(fresh.shots, batch);
      const spend = (run && run.quoted_usd) || 0;
      let failed = "";
      let made = 0;
      let doneShots = 0;

      for (let at = 0; at < passes.length; at += 1) {
        // ⚠ THE HARD STOP, AND IT IS READ HERE AND NOWHERE ELSE. Between passes
        // is the only moment at which stopping saves anything: the pass before
        // it is already billed and the pass after it has not been asked for.
        if (stopRef.current) break;
        const pass = passes[at];
        setFootage({
          stage: "rendering",
          pass: at + 1,
          passes: passes.length,
          shots: pass.length,
          done: doneShots,
          total: fresh.shots.length,
          usd: spend,
          message:
            `Rendering pass ${at + 1} of ${passes.length} — ${pass.length} shot` +
            `${pass.length === 1 ? "" : "s"} with Veo. This takes a couple of minutes.`,
          stopping: stopRef.current,
        });
        try {
          // eslint-disable-next-line no-await-in-loop
          await renderPass({
            shots: pass,
            render: veoRender,
            onProgress: (text) =>
              setFootage((was) => (was ? { ...was, detail: text } : was)),
          });
          // ⚠ SET AFTER THE FIRST PASS LANDS, NOT BEFORE IT. From this moment
          // the notices stop saying "nothing was spent", and Revert stops being
          // able to claim it puts everything back.
          spentRef.current = true;
          setCanRevert(true);
          made += 1;
          doneShots += pass.length;
        } catch (err) {
          failed = err?.message || "the render pass could not be reached";
          break;
        }
      }

      const stopped = stopRef.current && made < passes.length;
      // ⚠ CLOSED WHATEVER HAPPENED, including on the way out of a failure —
      // a record left saying "running" is a project that offers to resume a run
      // which has already been abandoned, every time it is opened.
      if (endVeo && run && run.id) {
        try {
          await endVeo({
            runId: run.id,
            status: failed ? "failed" : stopped ? "stopped" : "done",
            error: failed,
          });
        } catch {
          /* a lost status write is a stale resume offer, never a lost clip */
        }
      }
      if (!alive) return;

      const left = fresh.shots.length - doneShots;
      setFootage({
        stage: failed ? "failed" : stopped ? "stopped" : "done",
        pass: made,
        passes: passes.length,
        done: doneShots,
        total: fresh.shots.length,
        message: failed
          ? `The render stopped after ${made} pass${made === 1 ? "" : "es"} (${failed}). ` +
            `${doneShots} shot${doneShots === 1 ? "" : "s"} were rendered and are on the timeline; ` +
            `${left} ${left === 1 ? "was" : "were"} never submitted, so ${left === 1 ? "it" : "they"} cost nothing.`
          : stopped
            ? `Stopped after pass ${made} of ${passes.length}. ${doneShots} shot` +
              `${doneShots === 1 ? "" : "s"} were rendered; the remaining ${left} ` +
              "were never submitted and cost nothing. Reopen 🎬 to finish them."
            : `${doneShots} shot${doneShots === 1 ? "" : "s"} rendered in ${made} pass` +
              `${made === 1 ? "" : "es"}. The takes are on the Storyboard video row above their stills.`,
      });
      // ⚠ A RESUMED RUN ENDS HERE. It is finishing the FOOTAGE of a run whose
      // plan died with the browser that wrote it, so there are no steps to
      // re-anchor and nothing to apply — see `resumeVeo`.
      //
      // ⚠ WHICH MEANS IT HAS TO SAY SO ITSELF. The end-of-run notice is written
      // by the STEP effect, and a resume never reaches it — so without this the
      // last thing on screen is whatever the load left there, and a paid render
      // finishes in silence.
      if (resumingRef.current && onNotice) {
        onNotice(
          failed
            ? `The render stopped: ${failed}. What it had bought is on the timeline, ` +
                "and reopening 🎬 offers the rest."
            : stopped
              ? `Stopped. ${doneShots} more shot${doneShots === 1 ? "" : "s"} were rendered; ` +
                "reopen 🎬 to finish the rest."
              : "The interrupted render is finished — the takes are on the timeline. " +
                "The edit itself was never saved, so 🎬 has to be asked for it again."
        );
      }
      setPhase(resumingRef.current ? "done" : "anchoring");
    })();

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // ---------------------------------------------------------- THE RE-ANCHOR
  /**
   * THE FILM CAME BACK DIFFERENT. RE-ASK THE PLAN ABOUT THIS ONE.
   *
   * ⚠ ONCE, OVER BOTH PAID PASSES. Every timing decision in the plan was made
   * about the film as it stood when Run was pressed; phase B stretched the shots
   * that carry a line and phase C grew the shots the takes are longer than. This
   * is the first moment at which the document on screen is the document the plan
   * is about, and it is free: `validatePlan` re-resolves every target,
   * `applyGuardrails` re-computes every window and every per-minute budget off
   * the film's new length, and the rules planner is simply RE-ASKED, because its
   * entire input is the shot lengths and they have just been rewritten twice.
   *
   * ⚠ IT RUNS EVEN WHEN NOTHING MOVED, and costs nothing when nothing did:
   * `shiftsOf` reports an empty set, `reanchor` drops nothing, and the plan that
   * comes out the far side is the plan that went in. A conditional here would be
   * a second place that decides whether the film changed.
   */
  useEffect(() => {
    if (phase !== "anchoring") return undefined;

    // ⚠ RE-READ, THEN RE-ANCHOR, AND IN THAT ORDER. `readCtx()` reads the
    // editor's live refs, which now hold the server's picture row and the takes
    // that have been attached over it.
    const ctx = { ...readCtx(), caps: capabilities() };
    const now = ctx.frames || [];
    const shifts = shiftsOf(beforeRef.current, now);
    const anchored = reanchor({
      source: rawRef.current?.source || "house",
      raw: rawRef.current?.plan || { steps: [] },
      ctx,
      include,
      shifts,
      // Only when the pass wrote the captions. With Captions un-ticked those
      // words are not on screen, so a title using them is not a duplicate.
      spoken: include.captions !== false ? spokenWords(script.lines) : new Set(),
      // ⚠ WHICH PASS MOVED WHICH SHOT, so a dropped step names the one that
      // actually did it. See `growthCauses`.
      causes: growthCauses(beforeRef.current, speechRowRef.current, now),
    });
    rawRef.current = { source: rawRef.current?.source || "house", plan: anchored.raw };
    const out = adopt(anchored.raw, include);
    stepsRef.current = out.plan.steps;
    setDropped((rows) => [...rows, ...anchored.dropped]);
    if (shifts.anyGrew) {
      setSpeech((was) => ({
        stage: was?.stage || "done",
        lines: was?.lines || 0,
        message:
          `${was?.message ? `${was.message} ` : ""}${shifts.grew.size} shot` +
          `${shifts.grew.size === 1 ? "" : "s"} grew to cover what is laid over ` +
          `${shifts.grew.size === 1 ? "it" : "them"} — the edit has been re-anchored ` +
          "onto the film that came back.",
      }));
    }
    setPhase("running");
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

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
        const edits = stepsRef.current.filter((s) => s.verb !== "note").length;
        // ⚠ THREE SENTENCES, AND WHICH ONE IS TRUE IS NOT A DETAIL. "Nothing was
        // spent" after a $34 render pass is the single worst thing this feature
        // could say, and "Revert puts it all back" after one is a sentence that
        // gets the run pressed twice. So the notice is built from what actually
        // happened: `spentRef` is only ever set by a paid pass that landed.
        onNotice(
          resumingRef.current
            ? "The interrupted render is finished — the takes are on the timeline. " +
                "The edit itself was never saved, so 🎬 has to be asked for it again."
            : spentRef.current
              ? `The Director spent on this film and made ${edits} edits around what it bought. ` +
                  "Revert puts the timeline back — the voiceover and the renders have been paid for."
              : `The Director made ${edits} edits. Nothing was spent — Revert puts it all back.`
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
    /**
     * ⚠ THE CONTRACT, CHECKED IN THE BROWSER. `ACTION_API` names every editor
     * function a verb may call; this is the ones the editor did not supply.
     *
     * It is returned, and the panel DISPLAYS it, rather than being an assertion
     * or a console warning — because that is what makes it testable from
     * outside. `tests/editor_director_check.py` asserts the panel shows no such
     * message, which is the only way to prove the real editor satisfies the list
     * for verbs a given run never happens to reach. A `console.warn` would pass
     * that test while broken.
     *
     * Non-empty means the build is wrong, not the plan — a name was renamed on
     * one side of the line and not the other.
     */
    missingApi: ACTION_API.filter((name) => typeof api[name] !== "function"),
    totals: planTotals(plan),
    dropped,
    trimmed,
    log,
    index,
    canRevert,
    // the brief (popup one)
    brief,
    setBrief,
    language: tongue,
    setLanguage: setTongue,
    hasModel: Boolean(askModel),
    // what the last plan was and where it came from
    source,
    why,
    analysis,
    veo,
    cost,
    include,
    setInclude,
    // ------------------------------------------------------------- phase B
    /** `{ lines, written, skipped }` — what would be read, and who wrote it. */
    script,
    /** `{ stage, message, lines }` while the sound pass runs, else null. */
    speech,
    /** Will pressing Run spend? The panel's price line and its label read this. */
    willSpeak,
    /** Why it will not, when it will not — printed verbatim under the tick box. */
    speechWhy: due.why,
    // ------------------------------------------------------------- phase C
    /** `{ shots, skipped }` — what would be rendered, and what was left out. */
    shoot,
    /** `{ batch, passes, total }` — the quote, broken into its submissions. */
    quote,
    /** `{ stage, pass, passes, message, … }` while the footage runs, else null. */
    footage,
    /** Will pressing Run render? The price line and the button label read this. */
    willRender,
    /** Why it will not, when it will not — printed verbatim under the tick box. */
    renderWhy: shootDue.why,
    /**
     * `{ done, failed, inFlight, todo, paidUsd }` when a pass was interrupted and
     * can be picked up, else null. The brief popup's resume offer is this.
     */
    pending,
    /** ⚠ FINISHES THE FOOTAGE, NOT THE EDIT. See `resumeVeo`. */
    resumeVeo,
    /** Is this run finishing an old pass rather than applying a plan? */
    resuming: Boolean(resumingRef.current),
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

/**
 * The timeline, as the Director is told it.
 *
 * ⚠ IT IS WORDS AND NUMBERS, NEVER PIXELS. The model is given each shot's label,
 * its description, its dialogue and how long it holds — and no picture. That is
 * a limit worth stating in the prompt (the system block says "you cannot see the
 * pictures") rather than papering over: a plan that claims to know what is in
 * frame is a plan inventing it.
 *
 * ⚠ AND IT SAYS WHAT IS ALREADY THERE. Counts, not contents: a Director that
 * proposes a dissolve on a cut that has one is proposing a replacement without
 * knowing it, and a second title over the user's own title is the edit they
 * will remember.
 */
export function boardFrom(ctx) {
  const frames = ctx.frames || [];
  return {
    title: ctx.title || "",
    aspect_ratio: ctx.aspectRatio || "",
    fps: ctx.fps || 24,
    total_ms: ctx.totalMs || 0,
    shots: frames.map((frame, i) => ({
      label: frame.label || "",
      ms: frame.duration_ms || 0,
      description: frame.description || frame.prompt || "",
      dialogue: dialogueOf(frame),
    })),
    existing: {
      // Which CUTS carry a transition, in the plan's own 1-based numbering: a
      // record sits after a frame, and cut `n` is the gap after shot `n`.
      transitionCuts: (ctx.transitions || [])
        .map((t) => frames.findIndex((f) => f.id === t.after_frame_id) + 1)
        .filter((n) => n > 0)
        .sort((a, b) => a - b),
      texts: (ctx.texts || []).length,
      shapes: (ctx.shapes || []).length,
      audioTracks: (ctx.audioTracks || []).length,
    },
  };
}

/** A frame's spoken line, however this build happens to be carrying it. */
function dialogueOf(frame) {
  const lines = frame.dialogue;
  if (typeof lines === "string") return lines;
  if (!Array.isArray(lines)) return "";
  return lines
    .map((row) => (typeof row === "string" ? row : `${row?.character ? `${row.character}: ` : ""}${row?.line || ""}`))
    .filter(Boolean)
    .join(" / ");
}
