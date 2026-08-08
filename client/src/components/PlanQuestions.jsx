import { useEffect, useMemo, useState } from "react";
import Icon from "./Icon.jsx";

// PlanQuestions — the agent's questions as things you CLICK.
//
// Typing a paragraph to answer "how often can you publish?" is the slowest part
// of planning, and the vaguest. When the agent asks, it also returns 2-4 concrete
// options; this renders them above the chat box as tabs + selectable options,
// exactly one answer per question, with "Other" for anything it didn't think of.
//
// Submitting composes the answers into a normal chat message, so the transcript
// stays readable and the agent receives them like any other reply — no special
// answer channel to keep in sync.

const OTHER = "__other__";

export default function PlanQuestions({ questions, onSubmit, onDismiss, busy }) {
  const list = useMemo(() => questions || [], [questions]);
  const [active, setActive] = useState(0);
  // { [questionId]: label | OTHER }
  const [picked, setPicked] = useState({});
  // { [questionId]: "typed text" }
  const [other, setOther] = useState({});

  // A new set of questions is a fresh form — never inherit the last one's
  // answers, which would submit stale choices the user never made.
  useEffect(() => {
    setActive(0);
    setPicked({});
    setOther({});
  }, [list]);

  if (list.length === 0) return null;

  const q = list[Math.min(active, list.length - 1)];
  const answeredCount = list.filter((x) => {
    const p = picked[x.id];
    return p && (p !== OTHER || (other[x.id] || "").trim());
  }).length;

  function choose(qid, label) {
    setPicked((p) => ({ ...p, [qid]: label }));
    // Move to the next unanswered question so a 3-question set flows without
    // hunting for the tabs.
    const idx = list.findIndex((x) => x.id === qid);
    if (label !== OTHER && idx >= 0 && idx < list.length - 1) {
      setActive(idx + 1);
    }
  }

  function submit() {
    // Only send what was actually answered. Skipping a question is allowed —
    // the agent asked, it didn't demand.
    const answers = list
      .map((x) => {
        const p = picked[x.id];
        if (!p) return null;
        const value = p === OTHER ? (other[x.id] || "").trim() : p;
        if (!value) return null;
        return { header: x.header || x.question, question: x.question, value };
      })
      .filter(Boolean);
    if (answers.length === 0) return;
    onSubmit(answers);
  }

  return (
    <div className="pq">
      <div className="pq-head">
        <div className="pq-tabs" role="tablist">
          {list.map((x, i) => {
            const done = Boolean(
              picked[x.id] && (picked[x.id] !== OTHER || (other[x.id] || "").trim())
            );
            return (
              <button
                key={x.id}
                role="tab"
                aria-selected={i === active}
                className={`pq-tab ${i === active ? "on" : ""} ${done ? "done" : ""}`}
                onClick={() => setActive(i)}
              >
                {done && <span className="pq-tick">✓</span>}
                {x.header || `Question ${i + 1}`}
              </button>
            );
          })}
        </div>
        <button
          className="pq-close"
          onClick={onDismiss}
          title="Dismiss — you can always just type your answer"
          aria-label="Dismiss questions"
        >
          <Icon name="close" />
        </button>
      </div>

      <p className="pq-question">{q.question}</p>

      <div className="pq-options" role="radiogroup">
        {(q.options || []).map((o) => {
          const on = picked[q.id] === o.label;
          return (
            <button
              key={o.label}
              role="radio"
              aria-checked={on}
              className={`pq-opt ${on ? "on" : ""}`}
              onClick={() => choose(q.id, o.label)}
            >
              <span className="pq-radio" aria-hidden="true" />
              <span className="pq-opt-text">
                <span className="pq-opt-label">{o.label}</span>
                {o.description && (
                  <span className="pq-opt-desc">{o.description}</span>
                )}
              </span>
            </button>
          );
        })}

        {/* Always offered: the agent's options are guesses, and the creator
            knows their own situation better than the list does. */}
        <button
          role="radio"
          aria-checked={picked[q.id] === OTHER}
          className={`pq-opt ${picked[q.id] === OTHER ? "on" : ""}`}
          onClick={() => choose(q.id, OTHER)}
        >
          <span className="pq-radio" aria-hidden="true" />
          <span className="pq-opt-text">
            <span className="pq-opt-label">Other</span>
            <span className="pq-opt-desc">Type your own answer</span>
          </span>
        </button>
        {picked[q.id] === OTHER && (
          <input
            className="pq-other"
            autoFocus
            value={other[q.id] || ""}
            placeholder="Your answer…"
            onChange={(e) => setOther((o) => ({ ...o, [q.id]: e.target.value }))}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        )}
      </div>

      <div className="pq-foot">
        <span className="tiny muted">
          {answeredCount} of {list.length} answered
          {list.length > 1 ? " · skip any you'd rather not answer" : ""}
        </span>
        <button
          className="btn primary small"
          onClick={submit}
          disabled={busy || answeredCount === 0}
        >
          {busy ? "Sending…" : "Submit answers"}
        </button>
      </div>
    </div>
  );
}
