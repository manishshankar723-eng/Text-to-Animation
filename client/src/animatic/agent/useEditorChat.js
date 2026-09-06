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
// ⚠ THIS HOOK NO LONGER OWNS THE TRANSCRIPT. `useChatSessions` DOES.
// ---------------------------------------------------------------------------
// `{turns, setTurns}` arrive on `store` and are used here exactly as the
// `useState` they replaced. That inversion is what made MANY conversations per
// project possible: while this hook owned the transcript there could only ever
// be one of them, because "the conversation" and "the agent" were one object.
//
// The route is still stateless (see `server/editor_chat.py`) — the whole
// conversation still rides up on every message. What changed is where it is kept
// between messages: one `localStorage` key per project became a row per chat on
// the server, keyed by (owner, project). See `useChatSessions.js`.
//
// ⚠ NOTHING ELSE IN HERE MOVED. Every `setTurns` below is the same call it was;
// the difference is that the setter now writes into a chat that has a name, a
// list and a home.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as api from "../../api.js";
import { ACTIONS, describeStep } from "./actions.js";
import { capabilities } from "./capabilities.js";
import { MAX_LOOK_SHOTS, answerText, normaliseTurn, wireMessages } from "./chat_turn.js";
import { boardFrom } from "./useDirectorRun.js";
import { signatureKey } from "./chat_sessions.js";
// ⚠ THE DIRECTOR'S OWN SOUND PASS, USED AS IT IS. Cueing, budgeting,
// placement and the ducking under speech are all decided in there; this hook
// only supplies the cues in the shape `sfxCues` already reads. A second
// placement path would be a second answer to "where does a whoosh go".
import {
  musicCue,
  musicPlacement,
  sfxCues,
  sfxPlacements,
  soundRoom,
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

/**
 * HOW BIG A PICTURE A LOOK SENDS — its long edge, in pixels.
 *
 * ⚠ **THE SAME PROXY THE MONITOR ASKS FOR, AND FOR THE SAME REASON.** A model
 * deciding which of twelve shots is dull does not need a 1920px PNG of each; it
 * needs to see the shot. 512 is comfortably enough to read a face, a caption or
 * an empty frame, and it keeps a twelve-shot look to a few hundred kilobytes of
 * upload instead of tens of megabytes — which is the difference between a look
 * that answers in seconds and one the user cancels.
 */
const LOOK_MAX_EDGE = 512;

/**
 * HOW LONG A BIG JOB MAY RUN BEFORE THE PANEL STOPS BELIEVING IN IT.
 *
 * ⚠ A CEILING, NOT A TIMEOUT — nothing is cut off here. The server already
 * reports a run whose process restarted as `lost`, so the only case left is a
 * record that quietly stops being updated, and a loop with no end is not a
 * feature: it is a spinner somebody watches for an hour. Half an hour is far
 * past any honest fan-out of `MAX_WORK_BATCHES` at the operator's own clock.
 */
const WORK_MAX_WAIT_MS = 30 * 60 * 1000;

/**
 * `setTimeout` as a promise, that a Stop can cut short.
 *
 * ⚠ THE LISTENER IS REMOVED WHEN THE WAIT ENDS NORMALLY. A poll loop adds one
 * of these per tick, and an `abort` listener left on the controller for every
 * tick of a twenty-minute job is a leak that grows with exactly the runs it
 * matters on.
 */
function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(Object.assign(new Error("Stopped."), { stopped: true }));
      return;
    }
    const done = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    };
    const onAbort = () => {
      done();
      reject(Object.assign(new Error("Stopped."), { stopped: true }));
    };
    const timer = setTimeout(() => {
      done();
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * A blob as bare base64 — no `data:` prefix, because the wire carries the mime
 * type in its own field and the server calls `b64decode` on this.
 *
 * ⚠ `FileReader` RATHER THAN A BYTE LOOP. `btoa(String.fromCharCode(...bytes))`
 * is the obvious version and it blows the argument limit on a picture this size,
 * which fails as a RangeError on the big stills and works on the small ones —
 * the worst shape of bug to find later.
 */
function toBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read the picture"));
    reader.onload = () => {
      const url = String(reader.result || "");
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.readAsDataURL(blob);
  });
}

const newId = () =>
  Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

/**
 * THE ✨ AI EDITOR CHAT.
 *
 * @param {string}   animaticId    the project — the chat is keyed to it. NULL on
 *                                 a blank project nothing has been done to yet;
 *                                 `ensureId` is what turns it into a real one.
 * @param {object}   store         `useChatSessions` — THE CONVERSATION ITSELF.
 *                                 `{turns, setTurns, clearActive}` is all this
 *                                 hook takes from it; which chat those turns
 *                                 belong to, and when they are written down, is
 *                                 that hook's business and not this one's.
 * @param {function} ensureId      creates the project on demand and answers its
 *                                 id — a chat turn is the user doing something,
 *                                 so it may be the thing that creates it
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
  projectSignature = "",
  store,
  ensureId,
  readCtx,
  api: editorApi,
  applySnapshot,
  docRef,
  onNotice,
  language = "",
  // ⚠ THE SOUND PASS IS NOT A VERB AND CANNOT BE ONE — every verb is
  // synchronous, and finding a sound is a round trip to a stock library. So it
  // runs AFTER the steps, which is also the only correct order: a cue lands on a
  // moment, and the steps move moments — an inserted shot ripples its row, and
  // re-timing a shot moves that shot's own end. (⚠ NOT "everything after it":
  // `set_shot_duration` leaves the clips after it exactly where they are — see
  // the note in `chat_turn.js`.) Same reasoning, same order, as the Director's
  // phases D and E.
  buildSoundtrack,
  placeSoundtrack,
  openPaidDoor,
}) {
  // ⚠ BORROWED, NOT OWNED — see the header. `useChatSessions` holds the
  // conversation because it is the thing that knows which conversation this is.
  const { turns, setTurns } = store;
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
  const runTotalRef = useRef(0);
  const [runIndex, setRunIndex] = useState(-1);
  const [running, setRunning] = useState(false);
  // Which turn's plan can currently be put back. Only ever the newest applied
  // one — see the header.
  const [revertable, setRevertable] = useState("");
  const timerRef = useRef(null);
  // What the sound half of the current apply is doing, for the one line the
  // bubble shows while it happens. `""` is "not scoring".
  const [scoring, setScoring] = useState("");
  // ⚠ **WHOSE** APPLY IS RUNNING, AND WHOSE SOUND IS BEING SEARCHED. Both used
  // to be bare booleans/strings, and a bare flag has no owner — so the panel
  // drew the SAME "⏳ Finding 15 sounds…" line under EVERY applied plan in the
  // scrollback, and a second plan's card said "Making the edit…" while a
  // different turn was mid-run. Reported live on 2026-09-06 as *"dono applied ka
  // aa raha hai... upar wala kyun chal raha tha"*: one apply was in flight, two
  // cards claimed it. A status line has to name the turn it belongs to.
  const [runningTurn, setRunningTurn] = useState("");
  const [scoringTurn, setScoringTurn] = useState("");
  // ⚠ THE SYNCHRONOUS HALF OF THE SAME GUARD. `running` and `scoring` are state,
  // so two clicks in one React batch would both read the OLD value and both
  // start. This ref flips inside `apply` itself and is cleared only when the
  // whole apply — steps AND sound — is finally over.
  const applyBusyRef = useRef("");
  // ⚠ DECLARED ABOVE `send`, WHICH SETS IT (RULEBOOK G6). What the panel says
  // while the model is looking at the pictures — the model's own line about why
  // it needed to see, so a wait nobody asked for at least explains itself.
  const [looking, setLooking] = useState("");
  // ⚠ A BIG JOB IN FLIGHT, OR `null`. Not a second kind of "sending": `sending`
  // stays true throughout, because from the person's point of view they are
  // still waiting for their message to be answered. This is only the DETAIL —
  // what is being done and how much of it is left — which a spinner alone cannot
  // say, and which is the whole reason a job exists rather than a longer wait.
  const [work, setWork] = useState(null);
  // The id of the run being polled, readable from `stop` without re-rendering.
  const workRef = useRef(null);
  // ⚠ HOW LONG THIS TURN HAS BEEN GOING, IN WHOLE SECONDS, and it exists because
  // a bare "Thinking…" is indistinguishable from a hang. Paid for live: a turn
  // that was working perfectly took longer than the tab's patience, the spinner
  // said the same word for a minute and a half, and the user sent the SAME
  // MESSAGE THREE TIMES before the timeout finally spoke — three turns billed
  // for one question. A number that moves is the difference between a wait and a
  // fault, and it is the cheapest thing on this screen.
  const [elapsed, setElapsed] = useState(0);
  // The in-flight turn's abort handle, or null. ⚠ A REF, NOT STATE: `stop` must
  // reach the request that is running NOW, and a re-render's copy would be one
  // turn behind.
  const abortRef = useRef(null);

  // ⚠ ONE INTERVAL, ONLY WHILE A TURN IS IN FLIGHT, and it is cleared on the way
  // out — a second-hand left running behind a closed panel is a render a second
  // for ever. It counts from zero on every send, so the number is this turn's
  // wait and not the session's.
  useEffect(() => {
    if (!sending) {
      setElapsed(0);
      return undefined;
    }
    const started = Date.now();
    setElapsed(0);
    const tick = setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => clearInterval(tick);
  }, [sending]);

  // ⚠ THUNKS, LIKE THE DIRECTOR'S. The hook holds these for the length of a run
  // and the editor rebuilds its callbacks on every render; a captured copy would
  // be editing the film as it was when the panel mounted.
  const readCtxRef = useRef(readCtx);
  readCtxRef.current = readCtx;
  const ensureIdRef = useRef(ensureId);
  ensureIdRef.current = ensureId;
  const editorApiRef = useRef(editorApi);
  editorApiRef.current = editorApi;
  // ⚠ THUNKS FOR THE SAME REASON. `placeSoundtrack` closes over the audio tracks
  // and layers as they were when it was made, so a captured copy would lay the
  // sound onto the film as it stood before the steps ran.
  const soundRef = useRef({ buildSoundtrack, placeSoundtrack });
  soundRef.current = { buildSoundtrack, placeSoundtrack };

  // ------------------------------------------------------------- persistence
  // Transcript and AI work are written by `useChatSessions`. This hook keeps
  // only ephemeral controls (spinner, scoring and in-memory Undo) here; a
  // switched chat must not inherit those controls from the previous one.
  useEffect(() => {
    // The chat on screen changed — a different one was opened, ＋ was pressed,
    // or the project itself changed. Nothing this hook was holding still applies.
    setRevertable("");
    setError("");
    snapshotRef.current = null;
  }, [animaticId, store.activeId]);

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

  // ⚠ A THUNK, LIKE THE TWO SOUND ONES ABOVE, and for the same reason: the
  // editor's door openers close over state this hook has no business holding.
  const doorRef = useRef(openPaidDoor);
  doorRef.current = openPaidDoor;

  /**
   * Open the priced door an offer names. ⚠ SPENDS NOTHING ITSELF — it opens the
   * dialog that ✨ Animate / 🎙 Voiceover / 🖼 Animatic images already open, and
   * that dialog is what asks the server for a price and refuses an account whose
   * plan does not cover it. This hook must never learn either of those jobs.
   */
  const openPass = useCallback((door, shot) => {
    const open = doorRef.current;
    if (typeof open === "function") open(door, shot);
  }, []);

  /**
   * THE PICTURES OF A LOOK, fetched at monitor size and base64'd.
   *
   * ⚠ **PROXIED DOWN, NOT SENT FULL SIZE.** `?w=` is the same server-side proxy
   * the editor already asks for to draw its own monitor, so a look posts a few
   * hundred pixels per shot rather than a 1920px PNG each. Twelve of those would
   * be several megabytes of upload and a token bill to match, for a question
   * about which shot is boring.
   *
   * ⚠ **A PICTURE THAT WILL NOT LOAD IS SKIPPED, NOT THROWN.** One clip whose
   * file has gone must not cost the user the whole answer — the model is told
   * which shots it IS seeing, so a short look is still a true one.
   */
  const grabPictures = useCallback(async (shots) => {
    const frames = readCtxRef.current().frames || [];
    const out = [];
    for (const n of (shots || []).slice(0, MAX_LOOK_SHOTS)) {
      const frame = frames[n - 1];
      if (!frame?.url) continue;
      try {
        const blob = await api.fetchAnimaticMediaBlob(frame.url, LOOK_MAX_EDGE);
        out.push({ shot: n, mime: blob.type || "image/png", data: await toBase64(blob) });
      } catch {
        /* one missing still is not a failed turn — see above */
      }
    }
    return out;
  }, []);

  const post = useCallback(
    async (history, look = []) => {
      const ctx = readCtxRef.current();
      const caps = capabilities();
      const keep = config?.transcript_keep || 20;
      // ⚠ THE PROJECT MAY NOT EXIST YET. A blank editor holds nothing until the
      // first real action, and asking the chat to do something is one — see
      // `ensureProject` in AnimaticEditor.jsx.
      const projectId = animaticId || (await ensureIdRef.current?.());
      const answer = await api.editorChatTurn(projectId, {
        messages: wireMessages(history, keep),
        board: { ...boardFrom(ctx), sound: soundDigest(ctx) },
        capabilities: caps,
        language,
        look,
        // ⚠ THE SAME CONTROLLER FOR BOTH LEGS OF A LOOK. A Stop pressed while
        // the pictures are being read has to drop the second call too, and a
        // fresh controller per leg would leave the slowest one running.
        signal: abortRef.current?.signal,
        // ⚠ THE TAB WAITS AS LONG AS THE OPERATOR SAID THE MODEL MAY TAKE, plus
        // the wire. Derived on the server from the admin panel's `turn_seconds`
        // — see `wire_wait_seconds`. 0 while the config is still loading, which
        // `editorChatTurn` reads as "use the shipped floor".
        timeoutMs: config?.turn_timeout_ms || 0,
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

  /**
   * WATCH A BIG JOB UNTIL IT LANDS, AND RETURN THE TURN IT PRODUCED.
   *
   * ⚠ **POLLING, NOT A SOCKET, AND THAT IS THE POINT.** The whole reason a big
   * message became a job is that no connection can be relied on to stay open for
   * minutes; replacing one long-lived connection with another would put the bug
   * back with a different name. Each poll is a request that answers in
   * milliseconds and is complete in itself, so a dropped wifi costs one tick.
   *
   * ⚠ **AND A STOP HERE IS NOT AN ABORT.** `stop` asks the SERVER to stop and
   * lets this loop run on, because the job answers with whatever it had written
   * by then and that is a real plan the person can apply. Aborting the poll
   * instead would throw away forty finished shots to honour a click that meant
   * "that's enough", not "undo it".
   */
  const watchWork = useCallback(
    async (workId, brief, signal) => {
      const every = Math.max(1000, Number(config?.work_poll_ms) || 1500);
      const tasks = (brief?.tasks || []).map((t) => t.goal).filter(Boolean);
      const started = Date.now();
      workRef.current = workId;
      setWork({ id: workId, done: 0, total: 0, percent: 0, message: "", tasks });
      try {
        for (;;) {
          const s = await api.editorChatWork(workId);
          setWork({
            id: workId,
            done: s.done || 0,
            total: s.total || 0,
            percent: s.percent || 0,
            message: s.message || "",
            tasks,
          });
          if (s.state === "done") return s.turn || {};
          if (s.state === "failed" || s.state === "lost") {
            throw new Error(s.error || "That job did not finish.");
          }
          // ⚠ A CEILING, BECAUSE A LOOP WITH NO END IS NOT A FEATURE. The server
          // already reports a restarted run as `lost`, so this only catches the
          // case where the record itself stops being updated — and half an hour
          // is far past any honest fan-out of `MAX_WORK_BATCHES`.
          if (Date.now() - started > WORK_MAX_WAIT_MS) {
            throw new Error(
              "That job has been running for half an hour, which it should never do. " +
                "Nothing has changed on your timeline — ask again, or ask for less at once."
            );
          }
          await pause(every, signal);
        }
      } finally {
        workRef.current = null;
        setWork(null);
      }
    },
    [config]
  );

  // A PAGE RELOAD MUST NOT TURN A RUNNING, ALREADY-PAID JOB INTO A SECOND
  // MODEL CALL. The user turn is the durable job receipt; on the next visit we
  // continue polling it and append the same ordinary answer when it lands.
  const resumedWorkRef = useRef("");
  const watchWorkRef = useRef(watchWork);
  watchWorkRef.current = watchWork;
  useEffect(() => {
    if (!animaticId || sending || workRef.current) return undefined;
    // ⚠ NEVER WHILE SOMETHING ELSE OWNS THE ABORT HANDLE. This effect assigns
    // `abortRef.current`, so resuming on top of a live turn would hand Stop the
    // wrong request and leave the real one unstoppable.
    if (abortRef.current) return undefined;
    // ⚠ **THE NEWEST ONE, AND ONLY IF NOTHING HAS ANSWERED IT.** `find` took the
    // FIRST pending row, which on a conversation that had already moved on is an
    // OLD question — so the panel would go back and re-run a job the person had
    // given up on, while their current one was still being written. And a row
    // that already has its agent reply is not pending at all; it is a stamp that
    // was never cleared. Both were live on 2026-09-06.
    const answered = new Set(
      turns.filter((t) => t?.role === "agent" && t.work_id).map((t) => t.work_id)
    );
    let pending = null;
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      const t = turns[i];
      if (t?.role === "user" && t.work_id && t.work_state === "running" && !answered.has(t.work_id)) {
        pending = t;
        break;
      }
    }
    if (!pending) return undefined;
    const key = `${store.activeId}:${pending.work_id}`;
    if (resumedWorkRef.current === key) return undefined;
    resumedWorkRef.current = key;

    const controller = new AbortController();
    abortRef.current = controller;
    setSending(true);
    watchWorkRef.current(pending.work_id, pending.work, controller.signal)
      .then((finished) => {
        const ctx = readCtxRef.current();
        const caps = capabilities();
        const normalised = normaliseTurn(finished, caps, ctx);
        const turn = normalised.turn;
        const agentId = newId();
        setTurns((rows) => {
          const already = rows.some(
            (t) => t?.role === "agent" && t.work_id === pending.work_id
          );
          const updated = rows.map((t) =>
            t.id === pending.id
              ? {
                  ...t,
                  work_state: "done",
                  work_progress: { done: finished.done, total: finished.total },
                }
              : t
          );
          if (already) return updated;
          return [
            ...updated,
            {
              id: agentId,
              role: "agent",
              kind: turn.kind,
              text: turn.reply,
              ask: turn.ask,
              plan: turn.plan,
              plan_signature: turn.plan ? signatureKey(projectSignature) : undefined,
              sound: turn.sound,
              passes: turn.passes,
              work_id: pending.work_id,
              work: pending.work,
              work_state: "done",
              drops: [
                ...(finished.dropped || []).map((d) => ({
                  what: "step",
                  why: typeof d === "string" ? d : d?.why || "dropped",
                })),
                ...normalised.drops,
              ],
              steps: turn.plan ? turn.plan.steps.length : 0,
            },
          ];
        });
      })
      .catch((e) => {
        if (e?.name === "AbortError") return;
        setTurns((rows) =>
          rows.map((t) =>
            t.id === pending.id
              ? { ...t, work_state: "failed", work_error: e?.message || "That job did not finish." }
              : t
          )
        );
        setError(e?.message || "That saved AI job did not finish.");
      })
      .finally(() => {
        abortRef.current = null;
        setSending(false);
      });

    return () => controller.abort();
  }, [animaticId, store.activeId, turns]);

  const send = useCallback(
    async (text) => {
      const message = String(text || "").trim();
      if (!message || sending) return;
      setError("");

      // ⚠ THE SAME SENTENCE TWICE IN A ROW IS ONE QUESTION, NOT TWO. When a turn
      // fails the user's message deliberately stays on screen (see the catch
      // below) — and what people then do is type it again, which is how the
      // reported screenshot ended up with three identical bubbles and no answer.
      // Every one of them was re-posted on every later turn, so a retry made the
      // prompt bigger and the model slower at exactly the moment it was already
      // too slow. A retry REPLACES the unanswered attempt instead of stacking on
      // it. ⚠ ONLY WHEN NOTHING ANSWERED IT: the same request made again after a
      // reply is a real second ask ("bigger", "again"), and must be kept.
      const last = turns[turns.length - 1];
      const repeat = last && last.role === "user" && last.text === message;
      const kept = repeat ? turns.slice(0, -1) : turns;

      const mine = { id: newId(), role: "user", kind: "text", text: message };
      const history = [...kept, mine];
      setTurns(history);
      setSending(true);
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        let { answer, turn, drops } = await post(history);

        // ⚠ ONE LOOK, THEN THE ANSWER — never a second one. The model may say it
        // has to SEE the shots before it can answer; the browser fetches them
        // and asks the SAME question again with them attached. Two laps of this
        // would be a loop that spends money on each one, so it happens exactly
        // once and the server refuses a look on the call that carries pictures.
        if (turn.kind === "look" && turn.look) {
          setLooking(turn.look.why || "Looking at your shots…");
          try {
            const pictures = await grabPictures(turn.look.shots);
            if (pictures.length) {
              ({ answer, turn, drops } = await post(history, pictures));
            } else {
              // ⚠ SAID, NOT SWALLOWED. Nothing could be loaded, so the honest
              // reply is that it cannot see them — not a second blind guess
              // dressed up as an answer.
              turn = {
                kind: "answer",
                reply:
                  "I couldn't load the pictures for those shots, so I can't tell you " +
                  "what's in them. Tell me what you're after and I'll work from the " +
                  "labels and the timing.",
              };
            }
          } finally {
            setLooking("");
          }
        }

        // ⚠ A BIG MESSAGE IS NOT ANSWERED YET — IT IS RUNNING. The server took the
        // brief, started a job and came straight back, so what is in hand is an
        // id and a progress bar rather than a plan. When it lands, what comes
        // back is an ORDINARY turn and goes through the very same
        // `normaliseTurn` as everything else: a big job is not a second kind of
        // edit with a second set of rules. See `server/editor_chat_work.py`.
        if (answer.work_id) {
          setTurns((rows) =>
            rows.map((t) =>
              t.id === mine.id
                ? {
                    ...t,
                    work_id: answer.work_id,
                    work: answer.work || null,
                    work_state: "running",
                  }
                : t
            )
          );
          const finished = await watchWork(answer.work_id, answer.work, controller.signal);
          const ctx = readCtxRef.current();
          ({ turn, drops } = normaliseTurn(finished, capabilities(), ctx));
          // The server counts the batches' drops too; keep both.
          answer = { ...answer, dropped: finished.dropped || [] };
          setTurns((rows) =>
            rows.map((t) =>
              t.id === mine.id
                ? {
                    ...t,
                    work_state: "done",
                    work_progress: { done: finished.done, total: finished.total },
                  }
                : t
            )
          );
        }

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
            plan_signature: turn.plan ? signatureKey(projectSignature) : undefined,
            // ⚠ `sound` AND `passes` ARE PAYLOADS, NOT DECORATION, AND BOTH WERE
            // BEING LEFT BEHIND HERE. `normaliseTurn` returns five things a turn
            // can carry — `plan`, `sound`, `ask`, `passes`, `look` — and this
            // object was copying three of them field by field, which is the same
            // shape of bug as a rebuilt `JsonRequest` dropping its capability
            // (RULEBOOK E124): nothing throws, the field is simply not there.
            //
            // ⚠ WHAT IT COST, both seen live on 2026-09-05:
            //   · `sound` — the panel counts cues into the Apply button
            //     (`turn.sound?.sfx`), so fourteen cues and a music bed drew
            //     **"Apply 0 edits · Nothing has changed yet"**. Worse than the
            //     label: `apply()` starts `if ((!steps.length && !turn?.sound))
            //     return`, so with a sound-only turn the button did LITERALLY
            //     NOTHING when pressed. Sound has never once reached the timeline
            //     through this panel.
            //   · `passes` — `EditorChat.jsx` draws the paid-door buttons from
            //     `turn.passes`, so ✨ Animate / 🎙 Voiceover / 🖼 Animatic images
            //     never appeared, however clearly the chat offered them.
            //
            // ⚠ AND `toStore` (now in `chat_sessions.js`) ALREADY READ
            // `t.passes`, which is the tell: the
            // save projection was written for a field the live row never had, so
            // the two halves disagreed and neither one failed out loud.
            sound: turn.sound,
            passes: turn.passes,
            work_id: answer.work_id,
            work: answer.work,
            work_state: answer.work_id ? "done" : undefined,
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
        // ⚠ **A TURN THAT ENDED BADLY MUST NOT STAY MARKED "running".** The user
        // row is stamped `work_state: "running"` the moment a big job starts, and
        // this catch used to leave it that way — so the resume effect above,
        // which exists for a page RELOAD, found a "running" job on the very next
        // render and started polling it again behind the person's back. They had
        // already given up and typed a new message, and the old one came back to
        // life beside it. Reported live on 2026-09-06 as *"upar wala v kyun chal
        // raha tha"*. A job the user stopped, or one whose wait blew up, is over.
        setTurns((rows) =>
          rows.map((t) =>
            t.id === mine.id && t.work_state === "running"
              ? {
                  ...t,
                  work_state: e?.stopped ? "stopped" : "failed",
                  work_error: e?.stopped ? "" : e?.message || "That job did not finish.",
                }
              : t
          )
        );
        // ⚠ A STOP IS NOT AN ERROR. They pressed the button; telling them in red
        // that something went wrong would be the panel arguing with them. What
        // they are owed instead is the one fact they cannot see: the server was
        // already asked, so this turn is spent whether or not they read it.
        if (e?.stopped) {
          setError(
            "Stopped waiting. The message had already gone to the AI, so it still " +
              "counts as one of your turns — send it again if you want the answer."
          );
        } else {
          // ⚠ THE USER'S MESSAGE STAYS ON SCREEN. Rolling the transcript back to
          // before it would lose what they typed along with the error, and a chat
          // that eats your sentence when the network blinks is a chat people stop
          // trusting with long ones.
          setError(e?.message || "The AI Editor could not be reached.");
        }
      } finally {
        abortRef.current = null;
        setSending(false);
        setLooking("");
      }
    },
    [grabPictures, post, projectSignature, sending, turns]
  );

  /**
   * STOP WAITING FOR THE TURN THAT IS IN FLIGHT.
   *
   * ⚠ IT STOPS THE WAIT, NOT THE WORK. The request is genuinely aborted — the
   * browser drops the connection and `sending` goes false at once — but the
   * server has already been asked, and it will finish the call and count the
   * turn. There is no cancel on the other side of that door and pretending there
   * were would be the more expensive lie: somebody would press Stop to save
   * money. The line `send` writes says so in as many words.
   */
  const stop = useCallback(() => {
    // ⚠ TWO DIFFERENT STOPS, AND TELLING THEM APART IS THE WHOLE OF THIS
    // FUNCTION. An ordinary turn is one request the server cannot be called off,
    // so all Stop can do is quit waiting for it — the wait ends, the turn is
    // still spent, and the line `send` writes says so. A BIG JOB is the opposite:
    // the server genuinely can stop, most of the spend is in batches that have
    // not started, and everything already written is a real plan. So this asks
    // the server and KEEPS WATCHING, rather than aborting and throwing away the
    // work the person has already paid for.
    const running = workRef.current;
    if (running) {
      api.editorChatWorkStop(running).catch(() => {
        /* the poll below reports the real state; a failed stop is not an error
           to shout about, it just means the job finishes on its own. */
      });
      return;
    }
    abortRef.current?.abort();
  }, []);

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
      if ((!steps.length && !turn?.sound) || turn?.applied || turn?.stale) return;

      // ⚠ **ONE APPLY AT A TIME, AND "AN APPLY" INCLUDES ITS SOUND.** This used
      // to test `running` alone — the STEP loop — which goes false the instant
      // the last verb commits, while the sound half is still off at the library
      // for up to the whole request clock. So the composer came back to life,
      // a second plan was asked for and applied ON TOP of a film the first apply
      // had not finished editing, and both cards ended up saying "✓ Applied".
      // Worse than the label: `snapshotRef` and `revertable` are single-valued,
      // so the second apply silently took the first one's Undo away and pointed
      // the snapshot at a half-edited document. Seen live on 2026-09-06.
      //
      // ⚠ THE REF, NOT THE STATE, IS WHAT MAKES THIS SAFE — see `applyBusyRef`.
      if (applyBusyRef.current) return;
      applyBusyRef.current = turnId;

      // ⚠ TAKEN HERE, NOT WHEN THE PLAN ARRIVED. Between reading a plan and
      // pressing Apply the user can still edit, and reverting to a document from
      // before their edits would throw away work this feature never touched.
      snapshotRef.current = docRef?.current || null;
      const completed = new Set(
        (turn.log || [])
          .filter((line) => line.state === "done" || line.state === "note")
          .map((line) => line.id)
      );
      // RESUME ONLY THE UNCOMMITTED STEPS. Created elements keep their ids in
      // `apply_refs`, so a dependent set_text/set_effect_param can continue
      // after a reload without creating a second copy of the earlier element.
      stepsRef.current = steps.filter((step) => !completed.has(step.id));
      runTotalRef.current = steps.filter((step) => step.verb !== "note").length;
      refsRef.current = { ...(turn.apply_refs || {}) };
      runTurnRef.current = turnId;
      soundToScoreRef.current = turn?.sound || null;
      setTurns((rows) =>
        rows.map((t) =>
          t.id === turnId ? { ...t, apply_state: "running", reverted: false } : t
        )
      );
      setRunningTurn(turnId);
      setRunning(true);
      setRunIndex(0);
    },
    [docRef, turns]
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
      const total = runTotalRef.current;
      const sound = soundToScoreRef.current;
      soundToScoreRef.current = null;
      setRunning(false);
      setRunningTurn("");
      setRunIndex(-1);
      setTurns((rows) =>
        rows.map((t) =>
          t.id === turnId
            ? { ...t, applied: true, apply_state: "done", steps: total }
            : t
        )
      );
      // ⚠ ONLY THE NEWEST APPLIED PLAN KEEPS ITS UNDO. See the header.
      setRevertable(turnId);

      // ⚠ THE SOUND IS PART OF THE SAME UNDO. `snapshotRef` was taken before the
      // first step, and `placeSoundtrack` writes into the same document — so one
      // Revert takes the whole thing back, lanes and all.
      // ⚠ THE APPLY IS NOT OVER UNTIL THIS RUNS. `applyBusyRef` is released HERE
      // and nowhere else, because the sound half is part of the same apply and
      // part of the same Undo — letting a second one start before this point is
      // exactly the bug the ref exists to stop. See `apply`.
      const finish = (soundFailed) => {
        applyBusyRef.current = "";
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
        // ⚠ AND IT MUST RUN ON THE BAD PATH TOO. A rejection here with no
        // handler would leave `applyBusyRef` set for ever and the Apply button
        // dead for the rest of the session — a guard that jams shut is worse
        // than the race it was added to stop.
        scoreTurn(turnId, sound).then(
          finish,
          (err) => finish(err?.message || "the sound could not be added")
        );
      } else {
        finish("");
      }
      return undefined;
    }

    const line = runStep(runIndex);
    if (line) {
      setTurns((rows) =>
        rows.map((t) =>
            t.id === turnId
              ? {
                  ...t,
                  log: [...(t.log || []), line],
                  apply_refs: { ...refsRef.current },
                }
              : t
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
      // ⚠ THE PROJECT'S OWN ROOM, NOT A HOUSE NUMBER. A fourteen-shot board was
      // told four times that "one pass fetches at most 10 different sounds" —
      // about a project with room for thirty-four more files. See `soundRoom`.
      room: soundRoom({ audioTracks: ctx.audioTracks, music: Boolean(sound.music) }),
    });
    const bed = sound.music ? musicCue({ analysis: { music: sound.music } }) : null;
    const payload = soundtrackRequest({ sounds: cued.sounds, music: bed });
    if (!payload) return "";

    const asked = (cued.sounds || []).length + (bed ? 1 : 0);
    // ⚠ THE LINE AND THE TURN IT BELONGS TO ARE SET TOGETHER, ALWAYS. The panel
    // draws the spinner only under `scoringTurn`'s own card — see `EditorChat`.
    setScoringTurn(turnId);
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
      setScoringTurn("");
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
    // ⚠ THROUGH THE STORE, NOT PAST IT. Emptying `turns` here alone would leave
    // the SAVED chat exactly as it was, so the conversation would come straight
    // back on the next refresh — a Clear button that clears nothing.
    store.clearActive();
    setError("");
    setRevertable("");
    snapshotRef.current = null;
  }, [store]);

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
    stop,
    // Whole seconds this turn has been waiting, `0` when nothing is in flight.
    elapsed,
    // `{id, done, total, percent, message, tasks}` while a big job runs, else null.
    work,
    choose,
    apply,
    // Opens the priced door an offer names. Spends nothing — see `openPass`.
    openPass,
    revert,
    clear,
    running,
    scoring,
    // ⚠ WHOSE run and WHOSE sound search — the panel keys every status line off
    // these, so a line can only ever appear under the card that earned it.
    runningTurn,
    scoringTurn,
    // The model's own "why" while a look is in flight, or "".
    looking,
    revertable,
    // What the composer is allowed to do right now, and why not when it is not.
    blocked: overQuota
      ? `You've used all ${quota.limit} AI Editor messages this month. Upgrade for more, or wait until next month.`
      : overSession
        ? "This conversation has gone on long enough — press ＋ for a new chat."
        : "",
  };
}
