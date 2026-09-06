// useChatSessions.js — MANY CHATS PER PROJECT, AND THE CONVERSATION THEY HOLD.
//
// ---------------------------------------------------------------------------
// ⚠ THIS HOOK OWNS `turns`. `useEditorChat` USED TO, AND NOW BORROWS THEM.
// ---------------------------------------------------------------------------
// That inversion is the whole design. `useEditorChat` is the agent — it sends a
// message, applies a plan, scores the sound. WHERE the conversation lives, which
// one is open and when it is written down are not its business, and while they
// were, there could only ever be one conversation per project. Asked for
// outright: *"user new chat bana kar alag alag baat kar sake … aur sab chat save
// hona chahiye, user jo karwaya hai usko us project mai dekh sake fir baad mai —
// project by project save karna"*.
//
// So this hook hands `{turns, setTurns}` down and `useEditorChat` uses them
// exactly as it used its own `useState`. Nothing else in that hook changed.
//
// ---------------------------------------------------------------------------
// ⚠ A CHAT IS NOT CREATED UNTIL SOMEBODY SPEAKS IN IT.
// ---------------------------------------------------------------------------
// ＋ does NOT post anything: it empties the panel and forgets the open id. The
// autosave creates the chat when the first turn lands. Pressing ＋ five times in
// a row therefore leaves nothing behind — the alternative is five untitled empty
// rows in a list whose whole job is to be scannable, and it is the same rule the
// editor itself learned about blank projects (RULEBOOK E118).
//
// ---------------------------------------------------------------------------
// ⚠ THE PROJECT MAY NOT EXIST YET, AND THE FIRST MESSAGE IS WHAT CREATES IT.
// ---------------------------------------------------------------------------
// `animaticId` goes null → real MID-CONVERSATION. Reloading the list on that
// transition would wipe the exchange that created the project, so the transition
// is caught and the turns already in memory are pushed up as the project's first
// chat instead. Same trap, same answer, as the old single-transcript store.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as api from "../../api.js";
import {
  SAVE_DEBOUNCE_MS,
  forgetLegacy,
  isFull,
  forgetMirror,
  readLegacy,
  readMirror,
  readOpen,
  restoreTurns,
  sweepMirrors,
  titleFor,
  toStore,
  writeMirror,
  writeOpen,
} from "./chat_sessions.js";

/** One place, so the two call sites that summarise a chat cannot disagree. */
function rowFor(sessionId, turns, title) {
  return {
    session_id: sessionId,
    title: title || "",
    turn_count: (turns || []).filter((t) => t && t.role === "user").length,
    created_at: "",
    updated_at: new Date().toISOString(),
  };
}

/**
 * THE ✨ AI EDITOR'S CHATS, FOR ONE PROJECT.
 *
 * @param {string}  animaticId  the project, or null on a draft nothing has been
 *                              done to yet — see the header.
 * @param {boolean} enabled     whether the panel has ever been opened. ⚠ THE LIST
 *                              IS NOT FETCHED UNTIL IT IS. Every project load
 *                              would otherwise spend a request on conversations
 *                              for a panel nobody touched — including on accounts
 *                              the chat is switched off for. It is LATCHED by the
 *                              caller, so shutting the panel does not throw the
 *                              list away and re-fetch it on the next open.
 */
export default function useChatSessions({ animaticId, enabled = true, projectSignature = "" }) {
  const [sessions, setSessions] = useState([]);
  const [limit, setLimit] = useState(0);
  const [activeId, setActiveId] = useState("");
  const [turns, setTurns] = useState([]);
  const [listing, setListing] = useState(false);
  const [opening, setOpening] = useState(false);
  // ⚠ ONE LINE, AND IT IS NEVER FATAL. A chat store that is unreachable must
  // leave the panel usable — the conversation still works, it just is not being
  // written down, and the footer says exactly that instead of a modal.
  const [error, setError] = useState("");

  // The drag-free refs the effects need: a value closed over at save time is the
  // conversation as it was when the timer started, one turn behind.
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const activeRef = useRef(activeId);
  activeRef.current = activeId;
  // Read by `newChat`, which is a callback and must see the list as it is NOW.
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const limitRef = useRef(limit);
  limitRef.current = limit;
  const jobRef = useRef(animaticId);
  const projectSignatureRef = useRef(projectSignature);
  projectSignatureRef.current = projectSignature;
  // What has actually been written up, so the autosave can tell a real change
  // from a re-render. Compared as JSON because a turn is replaced, not mutated.
  const savedRef = useRef("");
  // ⚠ DECLARED ABOVE THE AUTOSAVE THAT READS IT (RULEBOOK G6). Chats the person
  // has named by hand — once a name is chosen, the first line of the chat must
  // never come back and overwrite it.
  const renamedRef = useRef(new Set());
  // ⚠ ONE WRITE IN FLIGHT AT A TIME, AND THE NEWEST ONE WINS. Two overlapping
  // PUTs can land in either order, and the older body arriving second is a chat
  // that silently loses its last message. The chain is what stops that.
  const chainRef = useRef(Promise.resolve());
  // ⚠ DECLARED ABOVE THE EFFECT THAT READS IT (RULEBOOK G6). Which project's
  // chats are currently on screen — `undefined` until the first fetch. See the
  // long note in that effect for why this is not just the previous `animaticId`.
  const loadedRef = useRef(undefined);
  // ⚠ WHICH CONVERSATION THE PANEL IS ON, AS A COUNTER — bumped by ＋, by
  // opening another chat and by deleting one. It is not the same question as
  // `activeId`: a brand-new chat and the one before it are BOTH `""` until the
  // first save lands, so an id cannot tell them apart. Without it, pressing ＋
  // while a create is in flight adopts the id that comes back and yanks the
  // person into the chat they just left, with an empty log on screen.
  const epochRef = useRef(0);
  // Which fetch of a transcript is the newest. ⚠ A TOKEN, NOT "IS THIS STILL THE
  // OPEN CHAT" — pressing ＋ mid-fetch leaves no open chat at all, and a spinner
  // keyed on that question would then never be cleared by anyone.
  const loadTokenRef = useRef(0);

  const setRow = useCallback((sessionId, patch) => {
    setSessions((rows) => {
      const found = rows.some((r) => r.session_id === sessionId);
      const next = found
        ? rows.map((r) => (r.session_id === sessionId ? { ...r, ...patch } : r))
        : [{ ...rowFor(sessionId, [], ""), ...patch }, ...rows];
      return [...next].sort((a, b) =>
        String(b.updated_at || "").localeCompare(String(a.updated_at || ""))
      );
    });
  }, []);

  // ------------------------------------------------------------- the listing
  const refresh = useCallback(async (jobId) => {
    const id = jobId || jobRef.current;
    if (!id) return [];
    try {
      const out = await api.editorChatSessions(id);
      const rows = Array.isArray(out?.sessions) ? out.sessions : [];
      setSessions(rows);
      setLimit(out?.limit || 0);
      setError("");
      // Anything deleted elsewhere leaves a mirror behind; this is where it goes.
      sweepMirrors(id, rows.map((r) => r.session_id));
      return rows;
    } catch (e) {
      setError(e?.message || "Could not read this project's chats.");
      return [];
    }
  }, []);

  /** Fetch one chat's transcript. The mirror paints first; the server corrects. */
  const loadTurns = useCallback(async (jobId, sessionId) => {
    loadTokenRef.current += 1;
    const token = loadTokenRef.current;
    const mirrored = restoreTurns(
      readMirror(jobId, sessionId),
      projectSignatureRef.current
    );
    if (mirrored) {
      setTurns(mirrored);
      savedRef.current = JSON.stringify(toStore(mirrored));
    } else {
      setTurns([]);
      savedRef.current = "[]";
    }
    setOpening(true);
    try {
      const row = await api.editorChatSession(jobId, sessionId);
      // ⚠ ONLY IF THIS IS STILL THE CHAT ON SCREEN. A slow fetch answering after
      // the user has clicked another row would paste the wrong conversation into
      // an open panel, which is the worst possible way to lose someone's place.
      if (jobRef.current !== jobId || activeRef.current !== sessionId) return;
      const rows = restoreTurns(
        Array.isArray(row?.turns) ? row.turns : [],
        projectSignatureRef.current
      );
      setTurns(rows);
      savedRef.current = JSON.stringify(toStore(rows));
      writeMirror(jobId, sessionId, rows);
    } catch (e) {
      // The mirror is already on screen. Say why it might be behind, and stop.
      if (jobRef.current === jobId && activeRef.current === sessionId) {
        setError(e?.message || "Could not open that chat.");
      }
    } finally {
      // ⚠ ONLY THE NEWEST FETCH CLEARS IT. Two overlapping opens and the first
      // one's `finally` would clear the second one's, leaving a panel that says
      // it has finished opening while it is still fetching.
      if (loadTokenRef.current === token) setOpening(false);
    }
  }, []);

  // ------------------------------------------------- opening one project's chats
  useEffect(() => {
    if (!enabled) return undefined;
    const previous = jobRef.current;
    jobRef.current = animaticId;

    // ⚠ "WHOSE CHATS ARE ON SCREEN" IS ITS OWN REF, NOT "WHAT WAS THE LAST id".
    // Those two are the same thing only while the panel has always been enabled,
    // and it is not: the list is fetched lazily, so the FIRST run of this effect
    // usually arrives with `previous` already equal to `animaticId` — and a guard
    // written as `previous === animaticId` would take that as "nothing changed"
    // and never load anything at all.
    if (loadedRef.current === animaticId) return undefined;

    // ⚠ THE FIRST ID A DRAFT GETS IS NOT A PROJECT SWITCH. A blank editor mints
    // its project at the first action — including a chat turn — so this fires
    // MID-CONVERSATION. Loading the list here would wipe the very exchange that
    // created the project; the autosave below files those turns instead. It is
    // the TURNS ALREADY IN MEMORY that tell the two cases apart: an empty panel
    // arriving at a real project is somebody opening it for the first time.
    const draftBecameProject = !previous && animaticId && turnsRef.current.length > 0;
    loadedRef.current = animaticId;
    if (draftBecameProject) {
      refresh(animaticId);
      return undefined;
    }

    setSessions([]);
    setActiveId("");
    setTurns([]);
    setError("");
    savedRef.current = "[]";
    if (!animaticId) return undefined;

    let alive = true;
    setListing(true);
    (async () => {
      let rows = await refresh(animaticId);

      // ⚠ THE OLD SINGLE TRANSCRIPT IS RESCUED HERE, ONCE. Every user of this
      // app has a conversation sitting under the v1 key. Shipping without this
      // would have looked, to the person who typed it, exactly like the new
      // feature deleted their chat history.
      if (alive && !rows.length) {
        const legacy = readLegacy(animaticId);
        if (legacy) {
          try {
            const made = await api.editorChatSessionCreate(animaticId, {
              title: titleFor(legacy),
              turns: toStore(legacy),
            });
            // ⚠ ONLY ONCE IT IS ACTUALLY ON THE SERVER. Deleting as we read
            // would lose the conversation to any failure in between, and a
            // network is at its most likely to fail exactly there.
            forgetLegacy(animaticId);
            if (made?.session_id) rows = await refresh(animaticId);
          } catch {
            // Left where it is, and tried again next time the panel opens.
          }
        }
      }
      if (!alive || jobRef.current !== animaticId) return;

      const remembered = readOpen(animaticId);
      const pick =
        (remembered && rows.some((r) => r.session_id === remembered) && remembered) ||
        (rows[0] ? rows[0].session_id : "");
      setActiveId(pick);
      activeRef.current = pick;
      if (pick) await loadTurns(animaticId, pick);
    })().finally(() => {
      if (alive) setListing(false);
    });

    return () => {
      alive = false;
    };
  }, [animaticId, enabled, refresh, loadTurns]);

  // -------------------------------------------------------------- the autosave
  // ⚠ DEBOUNCED, AND ONLY ON A REAL CHANGE. The panel re-renders on every tick
  // of the elapsed counter while a turn is in flight; a save keyed on renders
  // rather than on content would be a PUT a second, for the whole of a look.
  useEffect(() => {
    const jobId = animaticId;
    if (!jobId) return undefined;
    const body = toStore(turns);
    const encoded = JSON.stringify(body);
    if (encoded === savedRef.current) return undefined;
    // Nothing has been said yet and there is no chat to empty — nothing to file.
    if (!body.length && !activeRef.current) return undefined;

    const timer = setTimeout(() => {
      const sessionId = activeRef.current;
      const epoch = epochRef.current;
      const title = titleFor(turns);
      chainRef.current = chainRef.current
        .catch(() => {})
        .then(async () => {
          if (jobRef.current !== jobId) return;
          if (!sessionId) {
            // ⚠ THE CHAT IS BORN HERE, NOT AT THE ＋ BUTTON. See the header.
            const made = await api.editorChatSessionCreate(jobId, {
              title,
              turns: body,
            });
            const sid = made?.session_id || "";
            if (!sid || jobRef.current !== jobId) return;
            // ⚠ ONLY WHEN THE PANEL IS STILL ON THE CONVERSATION THESE TURNS CAME
            // FROM. ＋, or opening another chat, while the create was in flight
            // means the person has moved on — the chat is still filed (that is the
            // whole point), it just must not be reopened underneath them.
            if (epochRef.current === epoch && !activeRef.current) {
              setActiveId(sid);
              activeRef.current = sid;
              writeOpen(jobId, sid);
            }
            savedRef.current = encoded;
            writeMirror(jobId, sid, body);
            setRow(sid, { ...rowFor(sid, body, title), created_at: made?.created_at || "" });
            setError("");
            return;
          }
          await api.editorChatSessionSave(jobId, sessionId, {
            turns: body,
            // ⚠ THE TITLE ONLY EVER GOES UP WHILE IT IS STILL AUTOMATIC. Once a
            // person has renamed a chat, the first line of it must never come
            // back and overwrite the name they chose.
            ...(title && !renamedRef.current.has(sessionId) ? { title } : {}),
          });
          savedRef.current = encoded;
          writeMirror(jobId, sessionId, body);
          setRow(sessionId, {
            turn_count: body.filter((t) => t.role === "user").length,
            updated_at: new Date().toISOString(),
            ...(title && !renamedRef.current.has(sessionId) ? { title } : {}),
          });
          setError("");
        })
        .catch((e) => {
          setError(e?.message || "This chat is not being saved right now.");
        });
    }, SAVE_DEBOUNCE_MS);

    // The mirror is written straight away — it is what makes a reopened panel
    // paint instantly, and it costs nothing.
    if (activeRef.current) writeMirror(jobId, activeRef.current, body);

    return () => clearTimeout(timer);
  }, [animaticId, turns, setRow]);

  // ------------------------------------------------------------------ actions
  /** ＋ — an empty panel. Nothing is posted until the first message. */
  const newChat = useCallback(() => {
    // ⚠ REFUSED HERE, NOT AT THE AUTOSAVE. A full project used to answer ＋ with
    // a cheerful empty panel, and the refusal only arrived once a whole message
    // had been typed and the create came back 409. The button is disabled too;
    // this is the guard behind it, because a keyboard can still reach it.
    if (isFull(sessionsRef.current, limitRef.current)) return;
    // Already sitting in a fresh unsaved chat: pressing ＋ again does nothing,
    // rather than clearing what has not been written down yet.
    if (!turnsRef.current.length && !activeRef.current) return;
    epochRef.current += 1;
    setActiveId("");
    activeRef.current = "";
    setTurns([]);
    savedRef.current = "[]";
    setError("");
    writeOpen(animaticId, "");
  }, [animaticId]);

  const open = useCallback(
    (sessionId) => {
      if (!animaticId || !sessionId || sessionId === activeRef.current) return;
      epochRef.current += 1;
      setActiveId(sessionId);
      activeRef.current = sessionId;
      writeOpen(animaticId, sessionId);
      setError("");
      loadTurns(animaticId, sessionId);
    },
    [animaticId, loadTurns]
  );

  const rename = useCallback(
    (sessionId, title) => {
      const clean = String(title || "").trim();
      if (!animaticId || !sessionId) return;
      renamedRef.current.add(sessionId);
      setRow(sessionId, { title: clean });
      chainRef.current = chainRef.current
        .catch(() => {})
        .then(() => api.editorChatSessionSave(animaticId, sessionId, { title: clean }))
        .catch((e) => setError(e?.message || "Could not rename that chat."));
    },
    [animaticId, setRow]
  );

  const remove = useCallback(
    (sessionId) => {
      if (!animaticId || !sessionId) return;
      const rest = sessions.filter((r) => r.session_id !== sessionId);
      setSessions(rest);
      forgetMirror(animaticId, sessionId);
      renamedRef.current.delete(sessionId);
      if (sessionId === activeRef.current) {
        epochRef.current += 1;
        // ⚠ LAND SOMEWHERE REAL. Deleting the open chat and leaving the panel
        // pointed at a chat that is gone is how an autosave resurrects it.
        const next = rest[0] ? rest[0].session_id : "";
        setActiveId(next);
        activeRef.current = next;
        writeOpen(animaticId, next);
        setTurns([]);
        savedRef.current = "[]";
        if (next) loadTurns(animaticId, next);
      }
      chainRef.current = chainRef.current
        .catch(() => {})
        .then(() => api.editorChatSessionDelete(animaticId, sessionId))
        .catch((e) => setError(e?.message || "Could not delete that chat."));
    },
    [animaticId, sessions, loadTurns]
  );

  /** Clear chat — this conversation becomes new again, name and all. */
  const clearActive = useCallback(() => {
    const sessionId = activeRef.current;
    setTurns([]);
    setError("");
    if (!animaticId || !sessionId) {
      savedRef.current = "[]";
      return;
    }
    renamedRef.current.delete(sessionId);
    setRow(sessionId, { title: "", turn_count: 0 });
    writeMirror(animaticId, sessionId, []);
    savedRef.current = "[]";
    chainRef.current = chainRef.current
      .catch(() => {})
      .then(() => api.editorChatSessionSave(animaticId, sessionId, { title: "", turns: [] }))
      .catch((e) => setError(e?.message || "Could not clear that chat."));
  }, [animaticId, setRow]);

  const active = useMemo(
    () => sessions.find((r) => r.session_id === activeId) || null,
    [sessions, activeId]
  );

  return {
    // What `useEditorChat` borrows.
    turns,
    setTurns,
    // What the panel's session bar draws.
    sessions,
    active,
    activeId,
    limit,
    // ⚠ NO ROOM FOR ANOTHER CHAT — what the ＋ button is disabled on. See
    // `isFull`: it is not simply "as many rows as the ceiling", because the
    // server sweeps chats nobody ever typed in before it refuses.
    full: isFull(sessions, limit),
    listing,
    opening,
    error,
    // ⚠ TRUE ONLY WHEN THE PROJECT EXISTS. Until it does there is nothing to
    // hang a chat off, so the bar draws itself but files nothing — the first
    // message creates the project AND the chat, in that order.
    saves: Boolean(animaticId),
    newChat,
    open,
    rename,
    remove,
    clearActive,
    refresh,
  };
}
