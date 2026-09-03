// useEditorChat.js — the ✨ AI Editor's brain: a conversation that can edit.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS NOT A SECOND DIRECTOR. IT IS THE SAME ONE, TALKED TO.
// ---------------------------------------------------------------------------
// Every edit this hook makes goes through `ACTIONS` in `actions.js` — the same
// registry 🎬 Make Video runs, reached through the same `api` bag the editor
// already supplies. So the one-transition-per-cut rule, the effects cap and the
// wording of every status line are obeyed here for free, and they cannot drift,
// because there is no second copy of them to drift from.
//
// What is NOT shared is `useDirectorRun` itself. That hook is a four-phase
// machine — brief, preview, run, score — built around a popup opened once and
// answered once. A conversation has no phases: any turn may be an answer, a
// question or a plan, and a plan may sit unapplied in the scrollback while three
// more messages go by. Wrapping the popup's state machine to get at its runner
// would have meant driving it through phases it was in the middle of.
//
// ---------------------------------------------------------------------------
// ⚠ ONE STEP PER TICK, AND FOR THE SAME REASON THE DIRECTOR DOES IT.
// ---------------------------------------------------------------------------
// Every verb calls a `setState` and the NEXT verb has to see the result:
// `add_transition` reads back the record it just made to set its length,
// `set_effect_param` reads the chain `add_effect` just appended to. A
// synchronous `for` loop would have all of them read the document as it was
// before any of them ran, and roughly half would quietly do nothing. See the
// long note at the top of `useDirectorRun.js` — this is the same trap and the
// same 90ms answer.
//
// ---------------------------------------------------------------------------
// ⚠ REVERT IS ONE SNAPSHOT PER APPLY, NOT ONE PER CONVERSATION.
// ---------------------------------------------------------------------------
// The document is captured the instant Apply is pressed and handed back to
// `applySnapshot` — the same function Ctrl+Z uses. It is taken per apply rather
// than per session because between two applied plans the user has almost
// certainly done something by hand, and reverting the second must not throw away
// the first, nor their own work.
//
// ⚠ AND ONLY THE NEWEST APPLIED PLAN IS REVERTABLE. Once a second plan lands,
// the first one's Undo is gone from the panel — because putting the film back to
// before plan A would also silently undo plan B, and a button that undoes more
// than it says is worse than no button. Ctrl+Z still exists for everything else.
//
// ---------------------------------------------------------------------------
// ⚠ THE TRANSCRIPT IS THIS BROWSER'S, KEYED BY PROJECT.
// ---------------------------------------------------------------------------
// The route is stateless (see `server/editor_chat.py`), so the conversation is
// written to localStorage on every change and read back on mount — enough to
// survive a refresh and a navigation away from the editor. Keyed by the project
// id and NOT global: opening a second film must not carry the first film's
// conversation over, which is the bug `ScriptChat` had to fix by minting a
// session id. Here there is a real id to key on from the first render.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as api from "../../api.js";
import { ACTIONS, describeStep } from "./actions.js";
import { capabilities } from "./capabilities.js";
import { answerText, normaliseTurn, wireMessages } from "./chat_turn.js";
import { boardFrom } from "./useDirectorRun.js";
// ⚠ THE DIRECTOR'S OWN SOUND PASS, USED AS IT IS. Cueing, budgeting,
// placement and the ducking under speech are all decided in there; this hook
// only supplies the cues in the shape `sfxCues` already reads. A second
// placement path would be a second answer to "where does a whoosh go".
import {
  musicCue,
  musicPlacement,
  sfxCues,
  sfxPlacements,
  soundtrackRequest,
} from "./sound_pass.js";
// ⚠ FREE, AND THAT IS WHY IT IS CLIENT-SIDE. The editor already decodes every
// audio upload for its waveforms; `speech.js` reads that envelope rather than
// asking a server to decode the same file again. See its header.
import { deadAir, fillerLines, speechDigest } from "./speech.js";

/**
 * How long between steps. ⚠ NOT ZERO — a `setTimeout(0)` fires before React has
 * painted, so a step would still be reading the document one edit behind. The
 * Director's own `STEP_MS`, and it must stay the same number: two features
 * applying the same registry at different speeds would be two different answers
 * to "has the commit landed yet".
 */
const STEP_MS = 90;

/** Versioned, so a later change to the turn shape can ignore the old store. */
const STORE_PREFIX = "aniwala.editorChat.v1.";

/** A runaway guard on what is kept in storage and in the DOM. */
const MAX_KEPT = 60;

const newId = () =>
  Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

function loadStored(animaticId) {
  if (!animaticId) return [];
  try {
    const raw = localStorage.getItem(STORE_PREFIX + animaticId);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // A corrupt or unreadable store is not worth an error on screen — the chat
    // starts empty, which is a state it has to handle anyway.
    return [];
  }
}

/**
 * ⚠ WHAT IS STORED IS NOT WHAT IS DRAWN. A turn in memory carries its plan, and
 * a plan carries every step's arguments — tens of kilobytes on a long one, in a
 * store shared with every other project this browser has opened. What survives a
 * refresh is the CONVERSATION; a plan that was never applied is gone, and the
 * bubble says so rather than offering an Apply button that would run against a
 * timeline the user has since edited. That last part is the real reason: a stale
 * plan is not a saving, it is a trap.
 */
function toStore(turns) {
  return turns.slice(-MAX_KEPT).map((t) => ({
    id: t.id,
    role: t.role,
    kind: t.kind,
    text: t.text,
    // Kept because it reads as part of the conversation — "I asked, you chose".
    ask: t.kind === "ask" ? t.ask : undefined,
    chosen: t.chosen,
    // A plan that WAS applied is remembered as a fact, not as a button.
    applied: t.applied,
    steps: t.applied ? t.steps : undefined,
    // What the sound pass actually managed, kept because it is a fact about
    // the film rather than a button. The cues themselves are not: they are
    // part of the plan, and a stale plan is a trap (see `stale`).
    soundReport: t.soundReport,
    stale: t.kind === "plan" ? true : undefined,
  }));
}

/**
 * THE ✨ AI EDITOR CHAT.
 *
 * @param {string}   animaticId    the project — the chat is keyed to it
 * @param {function} readCtx       the editor's live read-model (`readDirectorCtx`)
 * @param {object}   api           the editor's callbacks, named in `ACTION_API`
 * @param {function} applySnapshot restores a whole document — Ctrl+Z's own function
 * @param {object}   docRef        a ref holding the current document, for snapshots
 * @param {function} onNotice      the editor's status line
 * @param {string}   language      the project's language, when it has one
 * @param {function} buildSoundtrack  asks the server to find the cued sounds
 * @param {function} placeSoundtrack  lays what came back onto two new lanes
 */
export default function useEditorChat({
  animaticId,
  readCtx,
  api: editorApi,
  applySnapshot,
  docRef,
  onNotice,
  language = "",
  // ⚠ THE SOUND PASS IS NOT A VERB AND CANNOT BE ONE — every verb is
  // synchronous, and finding a sound is a round trip to a stock library. So it
  // runs AFTER the steps, which is also the only correct order: a cue lands on a
  // moment, and `set_shot_duration` moves every moment after it. Same reasoning,
  // same order, as the Director's phases D and E.
  buildSoundtrack,
  placeSoundtrack,
}) {
  const [turns, setTurns] = useState(() => loadStored(animaticId));
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [config, setConfig] = useState(null);
  // `{used, limit}` after the last turn. `limit === null` is unlimited.
  const [quota, setQuota] = useState({ used: 0, limit: null });

  // The apply loop's state. ⚠ A REF FOR THE STEPS AND STATE FOR THE INDEX: the
  // effect below must re-run on the index and must NOT re-run when the step list
  // is rebuilt, which is the same split `useDirectorRun` makes.
  const stepsRef = useRef([]);
  const refsRef = useRef({});
  const snapshotRef = useRef(null);
  const runTurnRef = useRef("");
  // The sound this apply owes, held until the last step has committed.
  const soundToScoreRef = useRef(null);
  const [runIndex, setRunIndex] = useState(-1);
  const [running, setRunning] = useState(false);
  // Which turn's plan can currently be put back. Only ever the newest applied
  // one — see the header.
  const [revertable, setRevertable] = useState("");
  const timerRef = useRef(null);
  // What the sound half of the current apply is doing, for the one line the
  // bubble shows while it happens. `""` is "not scoring".
  const [scoring, setScoring] = useState("");

  // ⚠ THUNKS, LIKE THE DIRECTOR'S. The hook holds these for the length of a run
  // and the editor rebuilds its callbacks on every render; a captured copy would
  // be editing the film as it was when the panel mounted.
  const readCtxRef = useRef(readCtx);
  readCtxRef.current = readCtx;
  const editorApiRef = useRef(editorApi);
  editorApiRef.current = editorApi;
  // ⚠ THUNKS FOR THE SAME REASON. `placeSoundtrack` closes over the audio tracks
  // and layers as they were when it was made, so a captured copy would lay the
  // sound onto the film as it stood before the steps ran.
  const soundRef = useRef({ buildSoundtrack, placeSoundtrack });
  soundRef.current = { buildSoundtrack, placeSoundtrack };

  // ------------------------------------------------------------- persistence
  useEffect(() => {
    setTurns(loadStored(animaticId));
    setRevertable("");
    setError("");
  }, [animaticId]);

  useEffect(() => {
    if (!animaticId) return;
    try {
      localStorage.setItem(STORE_PREFIX + animaticId, JSON.stringify(toStore(turns)));
    } catch {
      // Storage full or blocked (private mode). The conversation still works for
      // this page load; it just will not survive a refresh. Not worth a message.
    }
  }, [animaticId, turns]);

  // ------------------------------------------------------------------ config
  // ⚠ FREE, AND ASKED ONCE. It answers whether the chat is on for this account,
  // where the panel opens and what is left of the monthly allowance. A failure
  // here leaves `config` null and the panel draws its own defaults rather than
  // refusing to open — a settings call that fails must not take the feature with
  // it. Same rule as `branding` and `features` on the server.
  useEffect(() => {
    let alive = true;
    api
      .editorChatConfig()
      .then((row) => {
        if (!alive) return;
        setConfig(row);
        setQuota({ used: row.turns_used || 0, limit: row.turns_limit ?? null });
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // ---------------------------------------------------------------- the turn
  /**
   * WHAT THE MODEL IS TOLD ABOUT THE SOUND — six lines, computed here for free.
   *
   * ⚠ **A SUMMARY, NOT THE SPANS.** A three-minute reading has four hundred runs
   * of sound in it. What an edit decision needs is whether there is speech, how
   * much of the film is silence, and where the few noticeable gaps are.
   *
   * ⚠ **AND IT IS SKIPPED ENTIRELY WHEN THERE IS NO AUDIO.** An empty "SOUND ON
   * THIS TIMELINE:" heading is tokens spent to say nothing, on every turn.
   */
  const soundDigest = useCallback((ctx) => {
    const analyses = ctx.audioAnalyses || {};
    const tracks = [];
    for (const track of ctx.audioTracks || []) {
      const found = analyses[track.upload_id];
      if (!found) continue;
      tracks.push({ name: track.filename || track.name || "audio", ...deadAir(found) });
    }
    // ⚠ ONLY THE GENERATED CAPTIONS, not every text clip. A title card reading
    // "Ah." is a design decision; a caption reading "umm" is a transcription of
    // a stumble, and only the second is anybody's to remove.
    const captions = (ctx.texts || []).filter(
      (t) => t && (t.layer_id === "captions" || String(t.id || "").startsWith("cap"))
    );
    return speechDigest({ tracks, fillers: fillerLines(captions) });
  }, []);

  const post = useCallback(
    async (history) => {
      const ctx = readCtxRef.current();
      const caps = capabilities();
      const keep = config?.transcript_keep || 20;
      const answer = await api.editorChatTurn(animaticId, {
        messages: wireMessages(history, keep),
        board: { ...boardFrom(ctx), sound: soundDigest(ctx) },
        capabilities: caps,
        language,
      });
      // ⚠ NORMALISED AGAINST THE PROJECT, NOT JUST AGAINST ITSELF. "Shot 61" is a
      // well-formed step and a nonsense one on a 48-shot film, so any plan on the
      // reply is checked against the read-model that was just taken — and what
      // survives is exactly what Apply will do.
      const { turn, drops } = normaliseTurn(answer, caps, ctx);
      return { answer, turn, drops };
    },
    [animaticId, config, language, soundDigest]
  );

  const send = useCallback(
    async (text) => {
      const message = String(text || "").trim();
      if (!message || sending) return;
      setError("");

      const mine = { id: newId(), role: "user", kind: "text", text: message };
      const history = [...turns, mine];
      setTurns(history);
      setSending(true);

      try {
        const { answer, turn, drops } = await post(history);
        setQuota({ used: answer.turns_used || 0, limit: answer.turns_limit ?? null });
        setTurns((rows) => [
          ...rows,
          {
            id: newId(),
            role: "agent",
            kind: turn.kind,
            text: turn.reply,
            ask: turn.ask,
            plan: turn.plan,
            // ⚠ THE SERVER'S DROPS AND THE CLIENT'S, TOGETHER. The server drops a
            // step whose verb it cannot read; the client drops one that will not
            // land on THIS film. Two different failures, and the user is owed
            // both — "3 steps couldn't be used" under the table is the honest
            // report a half-understood plan needs.
            drops: [
              ...(answer.dropped || []).map((d) => ({
                what: "step",
                why: typeof d === "string" ? d : d?.why || "dropped",
              })),
              ...drops,
            ],
            steps: turn.plan ? turn.plan.steps.length : 0,
          },
        ]);
      } catch (e) {
        // ⚠ THE USER'S MESSAGE STAYS ON SCREEN. Rolling the transcript back to
        // before it would lose what they typed along with the error, and a chat
        // that eats your sentence when the network blinks is a chat people stop
        // trusting with long ones.
        setError(e?.message || "The AI Editor could not be reached.");
      } finally {
        setSending(false);
      }
    },
    [post, sending, turns]
  );

  /**
   * An option on an `ask`, clicked.
   *
   * ⚠ IT BECOMES AN ORDINARY USER MESSAGE. There is no "pending question" record
   * anywhere — clicking option B appends the text of option B to the transcript
   * exactly as if it had been typed, and the next turn re-posts the whole thing.
   * That is what keeps the route stateless and the scrollback readable a week
   * later. See the header of `chat_turn.js`.
   */
  const choose = useCallback(
    (turnId, option) => {
      const asked = turns.find((t) => t.id === turnId);
      if (!asked || asked.kind !== "ask" || sending) return;
      const text = answerText(asked.ask, option);
      if (!text) return;
      // Marked so the chips stop being clickable in the scrollback: an old
      // question answered twice is two different films being asked for.
      setTurns((rows) =>
        rows.map((t) => (t.id === turnId ? { ...t, chosen: option.id } : t))
      );
      send(text);
    },
    [send, sending, turns]
  );

  // --------------------------------------------------------------- the apply
  const apply = useCallback(
    (turnId) => {
      const turn = turns.find((t) => t.id === turnId);
      const steps = turn?.plan?.steps || [];
      // ⚠ SOUND ALONE IS A REAL APPLY. "Put some music under it" produces no
      // steps at all, and refusing it here would draw an Apply button that did
      // nothing — the state this whole panel is written to avoid.
      if ((!steps.length && !turn?.sound) || running) return;

      // ⚠ TAKEN HERE, NOT WHEN THE PLAN ARRIVED. Between reading a plan and
      // pressing Apply the user can still edit, and reverting to a document from
      // before their edits would throw away work this feature never touched.
      snapshotRef.current = docRef?.current || null;
      stepsRef.current = steps;
      refsRef.current = {};
      runTurnRef.current = turnId;
      soundToScoreRef.current = turn?.sound || null;
      setRunning(true);
      setRunIndex(0);
    },
    [docRef, running, turns]
  );

  /** ONE STEP. ⚠ A STEP THAT THROWS IS LOGGED AND THE RUN CARRIES ON. */
  const runStep = useCallback((at) => {
    const step = stepsRef.current[at];
    if (!step) return null;
    const caps = capabilities();
    const ctx = { ...readCtxRef.current(), caps };
    const action = ACTIONS[step.verb];
    if (!action) return { id: step.id, verb: step.verb, state: "failed", why: "unknown verb" };
    const line = { id: step.id, verb: step.verb, text: describeStep(step, ctx) };
    try {
      action.run({ api: editorApiRef.current, args: step.args, ctx, refs: refsRef.current });
      return { ...line, state: step.verb === "note" ? "note" : "done" };
    } catch (err) {
      // One verb that fell over on one shot is one shot untreated. Stopping here
      // would leave the film half-edited with no account of which half.
      return { ...line, state: "failed", why: err?.message || "failed" };
    }
  }, []);

  useEffect(() => {
    if (!running) return undefined;
    const turnId = runTurnRef.current;

    if (runIndex >= stepsRef.current.length) {
      const total = stepsRef.current.filter((s) => s.verb !== "note").length;
      const sound = soundToScoreRef.current;
      soundToScoreRef.current = null;
      setRunning(false);
      setRunIndex(-1);
      setTurns((rows) =>
        rows.map((t) => (t.id === turnId ? { ...t, applied: true, steps: total } : t))
      );
      // ⚠ ONLY THE NEWEST APPLIED PLAN KEEPS ITS UNDO. See the header.
      setRevertable(turnId);

      // ⚠ THE SOUND IS PART OF THE SAME UNDO. `snapshotRef` was taken before the
      // first step, and `placeSoundtrack` writes into the same document — so one
      // Revert takes the whole thing back, lanes and all.
      const finish = (soundFailed) => {
        if (!onNotice) return;
        const made = `${total} edit${total === 1 ? "" : "s"}`;
        onNotice(
          soundFailed
            ? `The AI Editor made ${made}, but no sound was added — ${soundFailed}.`
            : `The AI Editor made ${made}. Undo in the chat puts the film back exactly as it was.`
        );
      };
      if (sound) {
        // ⚠ NOT AWAITED INSIDE THE EFFECT. An effect cannot be async, and the
        // run is already over — what is left is a network call whose result lands
        // on a turn that is already on screen.
        scoreTurn(turnId, sound).then(finish);
      } else {
        finish("");
      }
      return undefined;
    }

    const line = runStep(runIndex);
    if (line) {
      setTurns((rows) =>
        rows.map((t) =>
          t.id === turnId ? { ...t, log: [...(t.log || []), line] } : t
        )
      );
    }
    timerRef.current = setTimeout(() => setRunIndex((i) => i + 1), STEP_MS);
    return () => clearTimeout(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, runIndex]);


  /**
   * PHASE TWO OF AN APPLY: the sound.
   *
   * ⚠ **IT RUNS AFTER THE LAST STEP, AND THAT IS NOT AN ORDERING WHIM.** A cue
   * lands on a MOMENT — "the bell as shot 4 begins" — and the steps have just
   * finished moving the shots around, so this is the first instant at which shot
   * 4's start is the number it will still be when the film is exported. The
   * Director's phases D and E sit in the same place for the same reason.
   *
   * ⚠ **IT SPENDS NO MONEY AND IT IS STILL NOT FREE.** Freesound costs nothing
   * and the deployment's whole budget is 60 requests a minute, SHARED — so one
   * impatient conversation can exhaust everybody's. The ceilings that stop that
   * are `sound_pass.js`'s own and were applied to the preview already, in
   * `normaliseSound`, so what is asked for here is what was on screen.
   *
   * ⚠ **A FAILURE HERE DOES NOT FAIL THE APPLY.** No key, no results, a 502 from
   * the library — all of them leave the film with the steps' edit intact and a
   * sentence saying there is no sound on it. Exactly what a failed voiceover does
   * in the Director.
   */
  const scoreTurn = useCallback(async (turnId, sound) => {
    const { buildSoundtrack: build, placeSoundtrack: place } = soundRef.current;
    if (!sound || !build || !place) return "";

    const ctx = readCtxRef.current();
    // ⚠ THE CUES ARE BUILT IN `sfxCues`'s OWN INPUT SHAPE rather than by hand.
    // It is the thing that knows a cue needs a frame id and a hold, that the
    // budget is spent on DISTINCT sounds, and what to say when one is refused.
    const cued = sfxCues({
      analysis: { shots: (sound.sfx || []).map((s) => ({ shot: s.shot, sfx: s.query })) },
      frames: ctx.frames || [],
      starts: ctx.starts || [],
    });
    const bed = sound.music ? musicCue({ analysis: { music: sound.music } }) : null;
    const payload = soundtrackRequest({ sounds: cued.sounds, music: bed });
    if (!payload) return "";

    const asked = (cued.sounds || []).length + (bed ? 1 : 0);
    setScoring(`Finding ${asked} sound${asked === 1 ? "" : "s"}…`);
    try {
      const answer = await build(payload);
      const items = (answer && answer.items) || [];
      const placedSfx = sfxPlacements({ cues: cued.cues, imported: items });
      const placedBed = bed
        ? musicPlacement({
            cue: bed,
            imported: items,
            totalMs: readCtxRef.current().totalMs || 0,
            // ⚠ ANY SOUND ALREADY ON THE FILM DUCKS THE BED, not only a voiceover
            // this feature added. Somebody who dropped their own narration in last
            // week is owed the same courtesy.
            underSpeech: (readCtxRef.current().audioTracks || []).length > 0,
          })
        : { clips: [], why: "" };

      if (placedSfx.clips.length || placedBed.clips.length) {
        // ⚠ ONE CALL, BOTH LANES, ONE UNDO — see `placeSoundtrack` in the editor.
        place({ sfx: placedSfx.clips, music: placedBed.clips });
      }

      const bits = [];
      if (placedSfx.clips.length) {
        bits.push(`${placedSfx.clips.length} sound effect${placedSfx.clips.length === 1 ? "" : "s"}`);
      }
      if (placedBed.clips.length) bits.push("a music bed");
      const missed = [
        ...(cued.skipped || []).map((s) => s.why),
        ...(placedSfx.missing || []).map((m) => `${m.query} — ${m.why}`),
        ...(placedBed.why ? [placedBed.why] : []),
      ];
      setTurns((rows) =>
        rows.map((t) =>
          t.id === turnId
            ? { ...t, soundReport: { added: bits, missed } }
            : t
        )
      );
      return bits.length ? "" : "no usable sound was found";
    } catch (err) {
      const why = err?.message || "the sound library could not be reached";
      setTurns((rows) =>
        rows.map((t) => (t.id === turnId ? { ...t, soundReport: { added: [], missed: [why] } } : t))
      );
      return why;
    } finally {
      setScoring("");
    }
  }, []);

  const revert = useCallback(() => {
    const snapshot = snapshotRef.current;
    if (!snapshot || !revertable) return;
    applySnapshot(snapshot);
    snapshotRef.current = null;
    setTurns((rows) =>
      rows.map((t) => (t.id === revertable ? { ...t, applied: false, reverted: true } : t))
    );
    setRevertable("");
    if (onNotice) onNotice("Put back — the film is exactly as it was before that edit.");
  }, [applySnapshot, onNotice, revertable]);

  const clear = useCallback(() => {
    setTurns([]);
    setError("");
    setRevertable("");
    snapshotRef.current = null;
    try {
      localStorage.removeItem(STORE_PREFIX + animaticId);
    } catch {
      // Nothing was stored, so there is nothing to clear.
    }
  }, [animaticId]);

  // ⚠ THE SESSION CAP COUNTS THIS BROWSER'S CONVERSATION, NOT THE MONTH. The
  // month is `quota`, enforced on the server. This one is the operator's runaway
  // guard and is honestly local: clearing the chat resets it, which is fine —
  // it exists to stop a loop, not to meter a customer.
  const sessionTurns = useMemo(() => turns.filter((t) => t.role === "user").length, [turns]);
  const sessionCap = config?.max_turns_per_session || 0;
  const overSession = sessionCap > 0 && sessionTurns >= sessionCap;
  const overQuota = quota.limit !== null && quota.used >= quota.limit;

  return {
    turns,
    sending,
    error,
    setError,
    config,
    quota,
    send,
    choose,
    apply,
    revert,
    clear,
    running,
    scoring,
    revertable,
    // What the composer is allowed to do right now, and why not when it is not.
    blocked: overQuota
      ? `You've used all ${quota.limit} AI Editor messages this month. Upgrade for more, or wait until next month.`
      : overSession
        ? "This conversation has gone on long enough — start a new one to carry on."
        : "",
  };
}
