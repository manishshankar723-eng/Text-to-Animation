import { useEffect, useState } from "react";
import Icon from "./Icon.jsx";

// PlanLanguageModal — pick the language the calendar is WRITTEN in.
//
// A creator publishes titles and hooks in the language their audience speaks,
// so an English-only planner is unusable for a Hindi channel. This asks once,
// on Generate, rather than making it a buried setting.
//
// Hinglish is offered as a first-class choice, not a curiosity: it is what most
// Indian creators actually publish — Hindi in Latin script, mixed with English.

export const LANGUAGES = [
  {
    id: "english",
    label: "English",
    native: "English",
    hint: "Titles, hooks and outlines in English.",
    sample: "The story of Shiva nobody tells you",
  },
  {
    id: "hinglish",
    label: "Hinglish",
    native: "Hindi + English",
    hint: "Hindi in Roman script, mixed with English — how most Indian creators caption.",
    sample: "Shiv ji ki ye kahani aapne kabhi nahi suni hogi",
  },
  {
    id: "hindi",
    label: "Hindi",
    native: "हिन्दी",
    hint: "Full Hindi in Devanagari script.",
    sample: "शिव जी की ये कहानी आपने कभी नहीं सुनी होगी",
  },
  {
    id: "custom",
    label: "Other language",
    native: "Tamil, Bengali, Spanish…",
    hint: "Type any language — the plan is written in it.",
    sample: "",
  },
];

export default function PlanLanguageModal({
  open,
  initial,
  // "generate" (opened from the Generate button — confirming builds the plan)
  // or "pick" (opened from the Language field — confirming just sets it).
  mode = "generate",
  busy,
  onClose,
  onConfirm,
}) {
  const [picked, setPicked] = useState(initial || "english");
  const [custom, setCustom] = useState("");

  // Reopening should offer the language this plan was last built in.
  useEffect(() => {
    if (!open) return;
    const known = LANGUAGES.some((l) => l.id === initial);
    setPicked(known ? initial : initial ? "custom" : "english");
    setCustom(known || !initial ? "" : initial);
  }, [open, initial]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const value = picked === "custom" ? custom.trim() : picked;
  const ready = Boolean(value);
  // Naming the language on the button is what makes the choice unmistakable —
  // "Generate plan" alone gave no clue which language it was about to use.
  const label = (v) => LANGUAGES.find((l) => l.id === v)?.label || v || "…";

  return (
    <div className="modal-overlay">
      <div className="card lang-modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} title="Close" aria-label="Close">
          <Icon name="close" />
        </button>

        <header className="lang-head">
          <h2>What language should the plan be in?</h2>
          <p className="muted tiny">
            Titles, hooks, outlines and calls to action are written in this
            language — they get published as written.
          </p>
        </header>

        <div className="lang-options" role="radiogroup">
          {LANGUAGES.map((l) => {
            const on = picked === l.id;
            return (
              <button
                key={l.id}
                role="radio"
                aria-checked={on}
                className={`lang-opt ${on ? "on" : ""}`}
                onClick={() => setPicked(l.id)}
              >
                <span className="pq-radio" aria-hidden="true" />
                <span className="lang-opt-text">
                  <span className="lang-opt-top">
                    <span className="lang-opt-label">{l.label}</span>
                    <span className="lang-opt-native">{l.native}</span>
                  </span>
                  <span className="lang-opt-hint">{l.hint}</span>
                  {/* A sample title says more than any description: you can see
                      at a glance what your board will actually read like. */}
                  {l.sample && <span className="lang-opt-sample">“{l.sample}”</span>}
                </span>
              </button>
            );
          })}
        </div>

        {picked === "custom" && (
          <input
            className="lang-custom"
            autoFocus
            value={custom}
            maxLength={60}
            placeholder="e.g. Tamil, Bengali, Marathi, Spanish…"
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ready && onConfirm(value)}
          />
        )}

        <footer className="lang-foot">
          <span className="tiny muted">You can regenerate in another language any time.</span>
          <div className="lang-actions">
            <button className="btn ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn primary"
              disabled={!ready || busy}
              onClick={() => onConfirm(value)}
            >
              {busy ? (
                <>
                  <span className="spinner-inline" /> Writing in {label(value)}…
                </>
              ) : mode === "generate" ? (
                `Generate in ${label(value)}`
              ) : (
                `Use ${label(value)}`
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
