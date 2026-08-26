// The AI half of Script → Storyboard's script panel: the conversation, and the
// composer that sits directly under the script box.
//
// The box used to offer two ways in — type it, or upload a file — both of which
// assume a script already exists. This is the third: talk to an assistant, in
// place, and have what it writes appear in the box above.
//
// ⚠ IT IS NOT A TAB. It was one for about an hour, and that was wrong: "Paste
// script" and "Ask AI" were the same job behind a switch, so writing with the
// assistant meant flipping to another tab to see what you had, and flipping back
// to change a line. They are ONE panel now — script on top, conversation under
// it, one composer at the bottom — and a generated script lands in the box
// itself rather than behind a button.
//
// ⚠ IT IS A GENERAL CHAT, NOT A SCRIPT BUTTON. Ask it anything; it answers.
// That is deliberate — the moment a chat refuses ordinary questions people stop
// trusting it with the ones it IS for. The specialisation lives in the system
// prompt (see script_agent.py), not in a gate here.
//
// ⚠ THE TRANSCRIPT IS THIS BROWSER'S, and it is the only copy. The server keeps
// nothing (server/script_chat.py says why), so the conversation is written to
// localStorage on every change and read back on mount. That is enough to
// survive a refresh, a tab close and a navigation away from the workflow — it
// is NOT synced to another device, and clearing site data clears it.
//
// ⚠ AND IT IS ONE CHAT PER STORYBOARD, NOT ONE PER BROWSER. The first build kept
// a single global transcript, so starting a second board carried the first one's
// conversation over — the assistant would still be holding the last film in mind
// while being asked about a new one, and the user would scroll up into someone
// else's story. The transcript is keyed by a SESSION id instead, and
// `resetScriptChat()` (called from the workflow's own reset) retires it.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { usageLine } from "./PlanScriptModal.jsx";

// Versioned prefixes, so a later change to the message shape can bump them and
// ignore the old store instead of trying to migrate a chat log.
//
// ⚠ THE SESSION ID IS IN localStorage, NOT IN REACT STATE. It has to outlive a
// refresh: the whole point of storing the transcript is that the chat is still
// there afterwards, and a session id regenerated on every mount would orphan the
// very messages it is meant to find.
const STORE_PREFIX = "aniwala.scriptChat.v2.";
const SESSION_KEY = "aniwala.scriptChatSession.v1";
// The pre-session global key, from the build before chats were scoped to a
// storyboard. Dropped on first use below so an early tester's stray transcript
// doesn't sit in storage forever with nothing able to read it.
const LEGACY_KEY = "aniwala.scriptChat.v1";

const newId = () =>
  Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

/** This storyboard's chat id, minted on first use. Safe to call anywhere. */
export function currentScriptChatSession() {
  try {
    localStorage.removeItem(LEGACY_KEY); // no-op once it's gone
    const existing = localStorage.getItem(SESSION_KEY);
    if (existing) return existing;
    const fresh = newId();
    localStorage.setItem(SESSION_KEY, fresh);
    return fresh;
  } catch {
    // Storage blocked (private mode). A session id that only lives for this
    // page load still gives a working chat — it just won't survive a refresh.
    return newId();
  }
}

/**
 * Retire the current conversation and start a new one. Returns the new id.
 *
 * ⚠ CALLED FROM `resetWorkflow()`, WHICH IS THE ONLY HONEST DEFINITION OF "a
 * different storyboard" this form has: there is no board id until a breakdown
 * exists, and by then the script — the thing the chat is about — is already
 * written. So the boundary is the moment the form is emptied for a new one.
 *
 * It sweeps EVERY stored transcript rather than just the outgoing one, so a key
 * orphaned by a crash or a storage error can't accumulate. There is only ever
 * meant to be one.
 */
export function resetScriptChat() {
  const fresh = newId();
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key && key.startsWith(STORE_PREFIX)) {
        localStorage.removeItem(key);
      }
    }
    localStorage.setItem(SESSION_KEY, fresh);
  } catch {
    // Nothing was stored, so there is nothing to clear — the new id is enough.
  }
  return fresh;
}

// A transcript is cheap to keep but not free to send: every turn re-posts the
// whole thing. The server trims again on its side; this keeps the stored copy
// (and the DOM) from growing without limit in a very long session.
const MAX_KEPT = 40;

// Openers, split by whether there is already something in the script box.
// Handing someone "make it shorter" when the box is empty is a dead button.
const STARTERS_EMPTY = [
  "Write a 60-second reel script about a chai stall at midnight.",
  "I have an idea for a short film — help me turn it into a script.",
  "Suggest 5 story ideas for a 30-second ad about a running shoe.",
  "What makes a good hook in the first 3 seconds?",
];
const STARTERS_WITH_SCRIPT = [
  "Make my script shorter and punchier.",
  "Read my script and tell me what's weak about it.",
  "Rewrite my script for a 30-second version.",
  "Add a stronger ending to my script.",
];

function loadStored(sessionId) {
  try {
    const raw = localStorage.getItem(STORE_PREFIX + sessionId);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // A corrupt or unreadable store is not worth an error on screen — the chat
    // simply starts empty, which is a state it has to handle anyway.
    return [];
  }
}

export default function ScriptChat({
  sessionId,
  script,
  title,
  genre,
  style,
  aspect,
  onApplyScript,
}) {
  // ⚠ SEEDED FROM STORAGE ONCE. The parent passes `sessionId` as React's `key`
  // too, so a new storyboard remounts this component and re-seeds from the new
  // (empty) session rather than trying to swap transcripts under a live chat.
  const [messages, setMessages] = useState(() => loadStored(sessionId));
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  // Which returned scripts are expanded. Ids are the message index, which is
  // stable because messages are only ever appended or cleared wholesale.
  const [expanded, setExpanded] = useState({});
  const logRef = useRef(null);

  // Persist on every change. Small enough to write synchronously — a chat turn
  // is a keystroke-free event, unlike the timeline's drag loop.
  useEffect(() => {
    try {
      localStorage.setItem(
        STORE_PREFIX + sessionId,
        JSON.stringify(messages.slice(-MAX_KEPT))
      );
    } catch {
      // Quota full or storage blocked (private mode). The chat still works for
      // this session; only the "survives a refresh" part is lost, and telling
      // the user about it mid-conversation helps nobody.
    }
  }, [messages, sessionId]);

  // Follow the conversation down as it grows, including while a reply is being
  // waited for — the "Thinking…" bubble is the thing worth seeing.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  async function send(text) {
    const message = (text ?? draft).trim();
    if (!message || sending) return;

    // The user's line goes up immediately — waiting for the server to echo it
    // makes the box feel broken on a slow reply.
    const next = [...messages, { role: "user", text: message }];
    setMessages(next);
    setDraft("");
    setErr("");
    setSending(true);

    try {
      const res = await api.scriptChat({
        messages: next.slice(-MAX_KEPT),
        genre,
        style,
        aspectRatio: aspect,
        title,
        currentScript: script,
      });
      setMessages((cur) => [
        ...cur,
        {
          role: "agent",
          text: res?.reply || "",
          script: res?.script || "",
          scriptTitle: res?.title || "",
          usage: res?.usage || null,
        },
      ]);
      // ⚠ A NEW SCRIPT GOES STRAIGHT INTO THE BOX. This used to be a "Use this
      // script" button under the reply, from when the chat was its own tab and
      // the box was somewhere else. Now that they are one panel, a button that
      // moves text from the bottom of the panel to the top of the same panel is
      // a step with nothing in it. The parent keeps the previous text and shows
      // an Undo, which is what makes replacing safe rather than asking first.
      if (res?.script) onApplyScript(res.script, res.title || "");
    } catch (e) {
      // ⚠ THE USER'S MESSAGE IS ROLLED BACK when the reply fails, the same rule
      // the planner follows: leaving it in the log means the next turn re-sends
      // a question that was never answered, as if it had been.
      setMessages((cur) => cur.slice(0, -1));
      setDraft(message);
      setErr(e?.message || "The assistant didn't answer. Try again.");
    } finally {
      setSending(false);
    }
  }

  function clearChat() {
    if (messages.length && !window.confirm("Clear this conversation?")) return;
    setMessages([]);
    setExpanded({});
    setErr("");
  }

  // Starters are for the empty state ONLY, and they are chips rather than a
  // stack of full sentences: this row now sits under a script box in the same
  // panel, so it has to stay out of the way of the thing people came here for.
  const starters = script.trim() ? STARTERS_WITH_SCRIPT : STARTERS_EMPTY;
  const idle = messages.length === 0 && !sending;

  return (
    <div className="sc-chat">
      {/* The conversation, only once there IS one. An empty log framed like a
          panel is a hole in the form. */}
      {!idle && (
        <div className="sc-chat-log" ref={logRef}>
          {messages.map((m, i) => (
            <div
              key={i}
              className={`sc-msg ${m.role === "user" ? "is-user" : "is-agent"}`}
            >
              {m.text && <div className="sc-msg-text">{m.text}</div>}

              {/* The script itself is already in the box above — this is the
                  receipt, so it stays collapsed. "Put it back" is for going
                  back to an earlier draft after a rewrite went the wrong way. */}
              {m.script && (
                <div className="sc-script-card">
                  <div className="sc-script-head">
                    <span className="sc-script-title">
                      ✨ {m.scriptTitle || "Script"}
                    </span>
                    <span className="tiny muted">
                      {m.script.split("\n").filter(Boolean).length} lines · in the
                      box above
                    </span>
                  </div>
                  {expanded[i] && (
                    <pre className="sc-script-body">{m.script}</pre>
                  )}
                  <div className="sc-script-actions">
                    <button
                      type="button"
                      className="btn ghost small"
                      onClick={() =>
                        setExpanded((cur) => ({ ...cur, [i]: !cur[i] }))
                      }
                    >
                      {expanded[i] ? "Hide" : "Show"}
                    </button>
                    <button
                      type="button"
                      className="btn ghost small"
                      onClick={() => onApplyScript(m.script, m.scriptTitle)}
                    >
                      Put this one back
                    </button>
                    <button
                      type="button"
                      className="btn ghost small"
                      onClick={() => navigator.clipboard?.writeText(m.script)}
                    >
                      Copy
                    </button>
                  </div>
                </div>
              )}

              {/* What this turn cost. Advisory — see usageLine. */}
              {m.usage?.total > 0 && (
                <span className="tiny muted sc-msg-usage">{usageLine(m.usage)}</span>
              )}
            </div>
          ))}

          {sending && (
            <div className="sc-msg is-agent">
              <div className="sc-msg-text muted">
                <span className="spinner-inline" /> Thinking…
              </div>
            </div>
          )}
        </div>
      )}

      {err && <div className="error sc-chat-error">{err}</div>}

      {/* The composer. One box with the button INSIDE it, so the script box
          above and this read as two parts of one panel rather than two
          controls that happen to be near each other. */}
      <div className={`sc-composer ${sending ? "is-busy" : ""}`}>
        <textarea
          className="sc-composer-input"
          rows={2}
          value={draft}
          disabled={sending}
          placeholder={
            script.trim()
              ? "Ask AI to change this script — or ask anything else…"
              : "Ask AI to write your script — or ask anything else…"
          }
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter is a newline — the same rule as the
            // planner's chat box, and what every chat app has trained people on.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="sc-composer-foot">
          <span className="tiny muted sc-composer-hint">
            {sending
              ? "Writing…"
              : messages.length > 0
                ? "Enter to send · Shift+Enter for a new line"
                : "Answers come back in whatever language you write in"}
          </span>
          <button
            type="button"
            className="btn primary small sc-generate"
            onClick={() => send()}
            disabled={sending || !draft.trim()}
          >
            {sending ? (
              <>
                <span className="spinner-inline" /> Working…
              </>
            ) : (
              <>Generate ✨</>
            )}
          </button>
        </div>
      </div>

      {/* Openers, and a way out of the conversation. Never both: once there are
          messages the starters are noise, and until there are, "Clear chat" has
          nothing to clear. */}
      {idle ? (
        <div className="sc-starters">
          {starters.map((s) => (
            <button
              key={s}
              type="button"
              className="sc-starter"
              onClick={() => send(s)}
            >
              {s}
            </button>
          ))}
        </div>
      ) : (
        <div className="sc-chat-foot">
          <button type="button" className="btn ghost small" onClick={clearChat}>
            Clear chat
          </button>
          <span className="tiny muted">
            This chat belongs to the storyboard you're making now, and is saved in
            this browser only.
          </span>
        </div>
      )}
    </div>
  );
}
