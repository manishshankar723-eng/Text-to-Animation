// The "Ask AI" tab of the Script → Storyboard form's script box.
//
// The box used to offer two ways in — type it, or upload a file — both of which
// assume a script already exists. This is the third: talk to an assistant, in
// place, and put what it writes straight into the box.
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
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { usageLine } from "./PlanScriptModal.jsx";

// One key, versioned, so a later change to the message shape can bump it and
// ignore the old store instead of trying to migrate a chat log.
const STORE_KEY = "aniwala.scriptChat.v1";

// A transcript is cheap to keep but not free to send: every turn re-posts the
// whole thing. The server trims again on its side; this keeps the stored copy
// (and the DOM) from growing without limit in a very long session.
const MAX_KEPT = 40;

// How many lines of a returned script are shown before "Show all". Eight is
// about a scene — enough to tell whether it's the right story, short enough
// that the reply above it stays on screen.
const PREVIEW_LINES = 8;

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

function loadStored() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // A corrupt or unreadable store is not worth an error on screen — the chat
    // simply starts empty, which is a state it has to handle anyway.
    return [];
  }
}

export default function ScriptChat({
  script,
  title,
  genre,
  style,
  aspect,
  onUseScript,
}) {
  const [messages, setMessages] = useState(loadStored);
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
      localStorage.setItem(STORE_KEY, JSON.stringify(messages.slice(-MAX_KEPT)));
    } catch {
      // Quota full or storage blocked (private mode). The chat still works for
      // this session; only the "survives a refresh" part is lost, and telling
      // the user about it mid-conversation helps nobody.
    }
  }, [messages]);

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

  function useScript(text, scriptTitle) {
    if (
      script.trim() &&
      !window.confirm(
        "Replace what's in the script box with this script? Your current text will be lost."
      )
    ) {
      return;
    }
    onUseScript(text, scriptTitle);
  }

  function clearChat() {
    if (messages.length && !window.confirm("Clear this conversation?")) return;
    setMessages([]);
    setExpanded({});
    setErr("");
  }

  const starters = script.trim() ? STARTERS_WITH_SCRIPT : STARTERS_EMPTY;

  return (
    <div className="sc-chat">
      <div className="sc-chat-log" ref={logRef}>
        {messages.length === 0 && !sending && (
          <div className="sc-starters">
            <p className="muted">
              Ask anything, or get a script written for you. Reply comes back in
              whatever language you write in.
            </p>
            {starters.map((s) => (
              <button
                key={s}
                type="button"
                className="btn small sc-starter"
                onClick={() => send(s)}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            className={`sc-msg ${m.role === "user" ? "is-user" : "is-agent"}`}
          >
            <span className="sc-msg-who">{m.role === "user" ? "You" : "AI"}</span>
            {m.text && <div className="sc-msg-text">{m.text}</div>}

            {/* A returned script is NOT chat — it is the thing the form wants.
                So it gets its own block with the one button that matters. */}
            {m.script && (
              <div className="sc-script-card">
                <div className="sc-script-head">
                  <span className="sc-script-title">
                    {m.scriptTitle || "Script"}
                  </span>
                  <span className="tiny muted">
                    {m.script.split("\n").filter(Boolean).length} lines
                  </span>
                </div>
                <pre className="sc-script-body">
                  {expanded[i]
                    ? m.script
                    : m.script.split("\n").slice(0, PREVIEW_LINES).join("\n")}
                </pre>
                <div className="sc-script-actions">
                  <button
                    type="button"
                    className="btn primary small"
                    onClick={() => useScript(m.script, m.scriptTitle)}
                  >
                    Use this script
                  </button>
                  {m.script.split("\n").length > PREVIEW_LINES && (
                    <button
                      type="button"
                      className="btn ghost small"
                      onClick={() =>
                        setExpanded((cur) => ({ ...cur, [i]: !cur[i] }))
                      }
                    >
                      {expanded[i] ? "Show less" : "Show all"}
                    </button>
                  )}
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
            <span className="sc-msg-who">AI</span>
            <div className="sc-msg-text muted">
              <span className="spinner-inline" /> Thinking…
            </div>
          </div>
        )}
      </div>

      {err && <div className="error sc-chat-error">{err}</div>}

      <div className="sc-chat-input">
        <textarea
          className="prompt-textarea"
          rows={2}
          value={draft}
          placeholder="Ask for a script, an idea, a rewrite — or anything else…"
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
        <button
          type="button"
          className="btn primary"
          onClick={() => send()}
          disabled={sending || !draft.trim()}
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>

      {messages.length > 0 && (
        <div className="sc-chat-foot">
          <button type="button" className="btn ghost small" onClick={clearChat}>
            Clear chat
          </button>
          <span className="tiny muted">
            Saved in this browser only — it isn't part of the storyboard.
          </span>
        </div>
      )}
    </div>
  );
}
