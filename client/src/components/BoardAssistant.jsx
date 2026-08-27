// "Ask AI" beside a FINISHED board — the chat that edits instead of writes.
//
// ⚠ THIS IS THE SAME ASSISTANT THAT WAS TAKEN OFF THE FIRST SCREEN, AT THE
// POINT WHERE IT MEANS SOMETHING. On the form the user is handing over material
// and "Ask AI" had no referent; here there is a specific board on screen, they
// want a specific change to it, and "add a close-up before shot 5" is a
// sentence no form field can take.
//
// ⚠ IT PLANS, THE USER APPLIES. A reply comes back with a list of intended
// edits and nothing has happened yet. The list is on screen with a count of how
// many panels would be redrawn, and Apply is what spends. Redrawing a panel is
// an image; one typed sentence must never be able to spend forty of them behind
// somebody's back.
//
// The transcript lives here and nowhere else — the route is stateless, like
// /script-chat. Wearing the same `sc-*` classes as ScriptChat on purpose: it is
// the same kind of object and a second chat that looks different reads as a
// second product.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";

// Half a line describing one planned edit, for the list the user approves.
function actionLine(a) {
  const why = (a.why || "").trim();
  if (a.action === "delete") return `Delete shot ${a.shot}${why ? ` — ${why}` : ""}`;
  if (a.action === "insert") {
    return `New shot before ${a.shot}${why ? ` — ${why}` : ""}`;
  }
  const what = [];
  if (a.description) what.push("description");
  if (a.camera) what.push("camera");
  if (a.location) what.push("location");
  return `Shot ${a.shot}: redraw with a new ${what.join(" + ") || "prompt"}${
    why ? ` — ${why}` : ""
  }`;
}

export default function BoardAssistant({
  jobId,
  // {kind:"panel", shot} | {kind:"scene", scene} | {kind:"none"} — 1-based,
  // exactly as printed under the panels.
  selection,
  onClearSelection,
  // Runs the approved actions and reloads the board. Owned by the parent
  // because insert/delete renumber every tile and only the board knows how to
  // re-read itself. Returns the number of panels actually redrawn.
  onApply,
  disabled = false,
}) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [applying, setApplying] = useState(false);
  const [err, setErr] = useState("");
  const logRef = useRef(null);

  // Keep the newest turn in view — a log that has to be scrolled to read the
  // answer reads as a log that did not answer.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  const selectionLabel =
    selection?.kind === "panel"
      ? `Shot ${selection.shot}`
      : selection?.kind === "scene"
        ? `Scene ${selection.scene}`
        : "";

  async function send() {
    const message = draft.trim();
    if (!message || sending || disabled) return;
    setErr("");
    setDraft("");
    const next = [...messages, { role: "user", text: message }];
    setMessages(next);
    setSending(true);
    try {
      const res = await api.askAboutBoard(jobId, {
        messages: next,
        selection,
      });
      setMessages((cur) => [
        ...cur,
        {
          role: "agent",
          text: res?.reply || "",
          // Planned, not done. `applied` flips once the user presses Apply, so
          // an old plan in the scrollback can't be run twice by accident.
          actions: res?.actions || [],
          applied: false,
        },
      ]);
    } catch (e) {
      // ⚠ THE USER'S MESSAGE IS ROLLED BACK when the reply fails — the same
      // rule ScriptChat and the planner follow. Leaving it in the log means the
      // next turn re-sends a question that was never answered.
      setMessages((cur) => cur.slice(0, -1));
      setDraft(message);
      setErr(e?.message || "The assistant didn't answer. Try again.");
    } finally {
      setSending(false);
    }
  }

  async function apply(i) {
    const msg = messages[i];
    if (!msg?.actions?.length || applying || disabled) return;
    setErr("");
    setApplying(true);
    try {
      await onApply(msg.actions);
      setMessages((cur) =>
        cur.map((m, idx) => (idx === i ? { ...m, applied: true } : m))
      );
      // ⚠ EVERY OTHER PLAN IN THE LOG IS NOW STALE. Insert and delete renumber
      // the board, so "shot 7" in an older reply may point at a different
      // picture than it did when it was written. Retire them rather than let
      // one be applied against shot numbers that have moved.
      setMessages((cur) =>
        cur.map((m, idx) =>
          idx !== i && m.actions?.length && !m.applied
            ? { ...m, actions: [], stale: true }
            : m
        )
      );
    } catch (e) {
      setErr(e?.message || "Couldn't apply those changes.");
    } finally {
      setApplying(false);
    }
  }

  const idle = messages.length === 0 && !sending;

  return (
    <div className="ba-panel card">
      <div className="ba-head">
        <h3 className="ba-title">✨ Ask AI</h3>
        {selectionLabel ? (
          <button
            type="button"
            className="ba-selection"
            onClick={onClearSelection}
            title="Clear the selection — the AI will ask which shots you mean"
          >
            {selectionLabel} ✕
          </button>
        ) : (
          <span className="tiny muted">Nothing selected</span>
        )}
      </div>

      {idle && (
        <div className="ba-intro">
          <p className="tiny muted">
            Your storyboard is ready. Tell me what to change:
          </p>
          <ul className="ba-examples">
            <li>Add a close-up before shot 5.</li>
            <li>Make shot 7 a low angle.</li>
            <li>Delete shot 3.</li>
          </ul>
          <p className="tiny muted">
            {/* ⚠ SAY WHAT IT CANNOT DO, HERE, BEFORE IT IS ASKED. An assistant
                that only says no after you've typed reads as broken; one that
                says so up front reads as honest. */}
            Click a shot first and you can just say “this one”. I can't reorder
            shots, restyle the board or change dialogue from here.
          </p>
        </div>
      )}

      {!idle && (
        <div className="ba-log sc-chat-log" ref={logRef}>
          {messages.map((m, i) => (
            <div
              key={i}
              className={`sc-msg ${m.role === "user" ? "is-user" : "is-agent"}`}
            >
              {m.text && <div className="sc-msg-text">{m.text}</div>}

              {/* The plan. ⚠ NOTHING HERE HAS HAPPENED YET — that is the whole
                  point of showing it. */}
              {m.actions?.length > 0 && (
                <div className="ba-plan">
                  <ul className="ba-plan-list">
                    {m.actions.map((a, k) => (
                      <li key={k} className={`ba-plan-${a.action}`}>
                        {actionLine(a)}
                      </li>
                    ))}
                  </ul>
                  <button
                    type="button"
                    className="btn primary small"
                    disabled={applying || disabled}
                    onClick={() => apply(i)}
                  >
                    {applying ? (
                      <>
                        <span className="spinner-inline" /> Applying…
                      </>
                    ) : (
                      applyLabel(m.actions)
                    )}
                  </button>
                </div>
              )}

              {m.applied && (
                <p className="tiny muted ba-applied">✓ Applied to the board</p>
              )}
              {m.stale && (
                <p className="tiny muted ba-applied">
                  Shot numbers moved — ask again if you still want this.
                </p>
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

      <div className={`sc-composer ${sending ? "is-busy" : ""}`}>
        <textarea
          className="sc-composer-input"
          rows={2}
          value={draft}
          disabled={sending || disabled}
          placeholder={
            selectionLabel
              ? `What should change about ${selectionLabel.toLowerCase()}?`
              : "What would you like to change?"
          }
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="sc-composer-foot">
          <span className="sc-composer-hint tiny muted">
            Enter to send · Shift+Enter for a new line
          </span>
          <button
            type="button"
            className="btn primary small"
            disabled={!draft.trim() || sending || disabled}
            onClick={send}
          >
            Send ✨
          </button>
        </div>
      </div>
    </div>
  );
}

/** "Apply (2 redraws)" — ⚠ the count is the point: it is the only place the
 *  cost of a sentence is stated before it is charged. */
function applyLabel(actions) {
  const draws = actions.filter((a) => a.draws).length;
  if (!draws) return `Apply (${actions.length})`;
  return `Apply · ${draws} redraw${draws === 1 ? "" : "s"}`;
}
