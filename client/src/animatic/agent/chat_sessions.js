// chat_sessions.js — THE RULES BEHIND THE ✨ AI EDITOR'S MANY CHATS.
//
// ---------------------------------------------------------------------------
// ⚠ THE RULES ARE HERE, THE NETWORK AND THE STATE ARE IN `useChatSessions.js`.
// ---------------------------------------------------------------------------
// The same split `panel_box.js` makes against `EditorChat.jsx`, for the same
// reason: naming a chat, remembering which one was open and reading the browser's
// old single transcript are three small rules that would otherwise be re-derived
// inside three effects, and the third copy is always the one that rots.
//
// ---------------------------------------------------------------------------
// ⚠ WHAT IS ON THE SERVER AND WHAT IS IN THIS BROWSER — AND WHY THE LINE IS THERE.
// ---------------------------------------------------------------------------
//   SERVER   the chats themselves: titles and transcripts, keyed by (owner,
//            project). They are the work, so they follow the account, not the
//            machine. `server/chat_sessions.py`.
//   BROWSER  which chat was open, and a mirror of that one chat's turns.
//            "Where was I" is a fact about THIS screen and belongs nowhere else;
//            the mirror is so a reopened panel paints instantly and survives a
//            dead network, and it is a CACHE — the server is the truth.
//
// ⚠ AND EVERY READ IS WRAPPED. `localStorage` throws outright in a locked-down
// browser, and there is no `window` at all under `react-dom/server`. Both must
// give the defaults back rather than take the panel down with them.

/** Versioned. v1 was the single-transcript-per-project store this replaces. */
const ACTIVE_PREFIX = "aniwala.editorChatOpen.v2.";
const MIRROR_PREFIX = "aniwala.editorChatMirror.v2.";

/**
 * ⚠ THE STORE THIS FEATURE REPLACED, AND IT IS READ EXACTLY ONCE PER PROJECT.
 * Every user of this app already has conversations sitting under these keys.
 * Shipping without reading them would have looked, to the person who typed
 * them, exactly like the new feature deleted their chat history.
 */
const LEGACY_PREFIX = "aniwala.editorChat.v1.";

/**
 * A RUNAWAY GUARD ON THE MIRROR, AND NOTHING ELSE.
 *
 * ⚠ HOW MANY TURNS A CHAT ACTUALLY KEEPS IS THE OPERATOR'S NUMBER, NOT THIS ONE.
 * It is `chat_history_keep` in the admin panel and the SERVER applies it on every
 * save — asked for outright: *"isme admin panel mai v daalo, mai limit set kar
 * dunga — mai jitna daalun wahi hona chahiye"*. A browser that trimmed to its own
 * constant first would make that number mean nothing above 60, silently, which is
 * the whole failure the setting was created to end.
 *
 * ⚠ SO THIS EQUALS THE ADMIN FIELD'S MAXIMUM, AND THE TWO MUST MOVE TOGETHER —
 * `LIMITS["chat_history_keep"]["max"]` in `server/chat_settings.py`. Raising that
 * without raising this puts the trim back in the browser. Pinned by
 * `tests/chat_sessions_check.py` §11, which reads both files.
 */
export const MAX_KEPT = 400;

/** How long the panel sits on a change before writing it up. */
export const SAVE_DEBOUNCE_MS = 800;

/** What a chat is called before anyone has said anything in it. */
export const UNTITLED = "New chat";

/**
 * Is this project out of room for another chat?
 *
 * ⚠ THE ＋ BUTTON HAS TO ASK THIS *BEFORE* IT OPENS A BLANK PANEL, and the
 * first version did not — which is how a full project answered ＋ with a
 * cheerful empty "New chat" that could not be saved. The refusal only arrived
 * once a whole message had been typed and the autosave came back 409. Reported
 * from a live deployment with the ceiling set to 1: *"maine admin panel mai ek
 * likha, to yaha pe new chat open hua — kya ye sahi hai?"*. It was not.
 *
 * ⚠ AND `sessions.length >= limit` IS NOT THE WHOLE ANSWER. The server sweeps
 * chats **nobody ever typed in** before it refuses (`drop_one_unused`), so a
 * project whose rows include an empty one still has room — that empty chat is
 * what makes room. Answering "full" there would disable ＋ on a project the
 * server would happily have taken another chat for.
 *
 * ⚠ `limit` OF 0 IS "NO CEILING", NEVER "NO ROOM". Same rule as the server's.
 */
export function isFull(sessions, limit) {
  if (!limit || limit <= 0) return false;
  const rows = sessions || [];
  if (rows.length < limit) return false;
  return !rows.some((r) => !r || !r.turn_count);
}

// ===========================================================================
// Naming a chat
// ===========================================================================
/**
 * A chat's title, taken from the first thing the PERSON said in it.
 *
 * ⚠ THE FIRST USER LINE, NEVER THE AGENT'S. The agent's opening sentence is
 * about the film ("A devotional family film for Ganesh Chaturthi…") and reads
 * beautifully — and it is the same kind of sentence in every chat about that
 * project, so a list of them is a list of near-identical rows. What tells two
 * conversations apart is what the person came to do.
 *
 * ⚠ AND IT IS CUT ON A WORD, NOT AT A CHARACTER. A row is one line in a narrow
 * panel; "add sound effects and backgro" is a title that has to be read twice.
 */
export function titleFor(turns, max = 48) {
  const first = (turns || []).find((t) => t && t.role === "user" && (t.text || "").trim());
  if (!first) return "";
  const line = String(first.text).replace(/\s+/g, " ").trim();
  if (line.length <= max) return line;
  const cut = line.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trim()}…`;
}

/** What the header and the 🕘 list actually draw for one chat. */
export function labelFor(row) {
  return (row && row.title ? String(row.title).trim() : "") || UNTITLED;
}

/**
 * "now", "12m", "3h", "5d", or a date once it is older than a week.
 *
 * ⚠ IT NEVER SAYS "0m". A chat saved four seconds ago reading "0m ago" looks
 * like a bug in the clock; under a minute it is simply "now".
 */
export function agoLabel(iso, now = Date.now()) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const secs = Math.max(0, Math.round((now - then) / 1000));
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  try {
    return new Date(then).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
    });
  } catch {
    return `${days}d`;
  }
}

// ===========================================================================
// What survives a refresh
// ===========================================================================
/**
 * ⚠ THE CHAT IS ALSO THE AI WORK JOURNAL. A transcript without its plan is not
 * a saved edit: after a refresh the person would have to pay for the same model
 * call again. Keep the complete proposal and its apply checkpoint alongside
 * the words. `plan_signature` is the document signature the proposal was read
 * from; `useChatSessions` uses it to decide whether an unapplied plan is still
 * safe to run. A changed film keeps the plan visible but correctly withholds
 * Apply, rather than silently applying old shot numbers.
 */
export function toStore(turns) {
  return (turns || []).slice(-MAX_KEPT).map((t) => ({
    id: t.id,
    role: t.role,
    kind: t.kind,
    text: t.text,
    // Kept because it reads as part of the conversation — "I asked, you chose".
    ask: t.kind === "ask" ? t.ask : undefined,
    chosen: t.chosen,
    plan: t.plan,
    plan_signature: t.plan_signature,
    sound: t.sound,
    passes: t.passes,
    drops: t.drops,
    log: t.log,
    apply_refs: t.apply_refs,
    // Applied state is remembered as a fact; unapplied state remains a reusable
    // proposal until `restoreTurns` proves the document changed.
    applied: t.applied,
    steps: t.applied ? t.steps : undefined,
    apply_state: t.apply_state,
    reverted: t.reverted,
    // What the sound pass actually managed — a fact about the film, not a button.
    soundReport: t.soundReport,
    // Offers remain part of the same saved AI work record, so the person can
    // inspect exactly what the model proposed without paying for it again.
    work_id: t.work_id,
    work: t.work,
    work_state: t.work_state,
    work_progress: t.work_progress,
    work_error: t.work_error,
    stale: t.stale,
  }));
}

/** Compact, deterministic key for a project document signature. */
export function signatureKey(value) {
  const text = String(value || "");
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `${text.length}:${(hash >>> 0).toString(36)}`;
}

/**
 * Rehydrate saved AI work against the document currently open in the editor.
 * An unapplied plan is reusable only when it was produced from this exact
 * document. Applied plans remain historical facts and never become stale just
 * because the document moved on afterwards.
 */
export function restoreTurns(turns, projectSignature) {
  return (turns || []).map((t) => {
    if (t?.kind !== "plan" || t.applied) return t;
    const safe = Boolean(
      t.plan && projectSignature && t.plan_signature &&
      (t.plan_signature === projectSignature ||
        t.plan_signature === signatureKey(projectSignature))
    );
    return { ...t, stale: !safe };
  });
}

const safeParse = (raw) => {
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
};

/** Which chat was open in THIS browser, for this project. `""` when none. */
export function readOpen(jobId) {
  if (!jobId) return "";
  try {
    return localStorage.getItem(ACTIVE_PREFIX + jobId) || "";
  } catch {
    return "";
  }
}

export function writeOpen(jobId, sessionId) {
  if (!jobId) return;
  try {
    if (sessionId) localStorage.setItem(ACTIVE_PREFIX + jobId, sessionId);
    else localStorage.removeItem(ACTIVE_PREFIX + jobId);
  } catch {
    // A remembered position is a nicety, exactly as it is in `panel_box.js`.
  }
}

/**
 * The offline mirror of ONE chat. ⚠ ONE, NOT ALL OF THEM — a browser holding a
 * copy of forty conversations per project is how a 5MB quota gets spent on a
 * cache nobody reads, and every chat but the open one is a click away from the
 * server anyway.
 */
export function readMirror(jobId, sessionId) {
  if (!jobId || !sessionId) return null;
  try {
    const raw = localStorage.getItem(`${MIRROR_PREFIX}${jobId}.${sessionId}`);
    return raw ? safeParse(raw) : null;
  } catch {
    return null;
  }
}

export function writeMirror(jobId, sessionId, turns) {
  if (!jobId || !sessionId) return;
  try {
    localStorage.setItem(
      `${MIRROR_PREFIX}${jobId}.${sessionId}`,
      JSON.stringify(toStore(turns))
    );
  } catch {
    // Full or blocked. The conversation still works and is still going to the
    // server; only the instant repaint is lost. Not worth a message.
  }
}

export function forgetMirror(jobId, sessionId) {
  if (!jobId || !sessionId) return;
  try {
    localStorage.removeItem(`${MIRROR_PREFIX}${jobId}.${sessionId}`);
  } catch {
    /* nothing to forget */
  }
}

/**
 * ⚠ SWEEP THE MIRRORS OF CHATS THAT NO LONGER EXIST. Without this, deleting a
 * chat leaves its copy in the browser for ever — the store grows monotonically
 * and nothing ever reads the orphans. Walked backwards because removing a key
 * shifts every index after it, which is the bug that makes a sweep skip half of
 * what it was written to remove.
 */
export function sweepMirrors(jobId, keepIds) {
  if (!jobId) return 0;
  const keep = new Set(keepIds || []);
  const prefix = `${MIRROR_PREFIX}${jobId}.`;
  let removed = 0;
  try {
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(prefix)) continue;
      if (keep.has(key.slice(prefix.length))) continue;
      localStorage.removeItem(key);
      removed += 1;
    }
  } catch {
    // Blocked or absent. Nothing to sweep, and nothing that needs saying.
  }
  return removed;
}

// ===========================================================================
// The one-time rescue of the old single transcript
// ===========================================================================
/**
 * The conversation this project had under the OLD store, or `null`.
 *
 * ⚠ IT IS NOT DELETED HERE. `forgetLegacy` is a separate call the caller makes
 * only once the rescued turns have actually been written to the server — a read
 * that deleted as it went would lose the conversation to any failure between
 * the two, which is precisely the window a network is most likely to be in.
 */
export function readLegacy(jobId) {
  if (!jobId) return null;
  try {
    const raw = localStorage.getItem(LEGACY_PREFIX + jobId);
    const rows = raw ? safeParse(raw) : null;
    return rows && rows.length ? rows : null;
  } catch {
    return null;
  }
}

export function forgetLegacy(jobId) {
  if (!jobId) return;
  try {
    localStorage.removeItem(LEGACY_PREFIX + jobId);
  } catch {
    /* nothing to forget */
  }
}
