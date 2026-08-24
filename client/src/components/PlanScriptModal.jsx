import { useEffect, useState } from "react";
import Icon from "./Icon.jsx";

// PlanScriptModal — one written script, and everywhere it can go next.
//
// Reuses `modal-overlay` / `export-modal` / `modal-close` wholesale rather than
// growing a second modal shell: this is the same kind of object as the export
// preview (a big scrollable document with a pinned action footer), so it should
// be the same furniture. `script-modal` only adds what is genuinely different —
// the screenplay layout in the body.
//
// THE FOUR WAYS OUT, and why all four exist:
//   Copy   — into a script the creator is already writing somewhere else.
//   TXT    — the exact bytes the storyboard breakdown reads. Edit it in a text
//            editor, paste it back, and nothing is lost in translation.
//   DOCX   — the readable one, for a client or whoever is holding the camera.
//   Open in Script to Storyboard — the actual pipeline handoff.

// Runtime chip tone. A script that reads 30% over what was asked for is a
// problem the creator should see before they shoot it, not after.
const OVER_RUN_TOLERANCE = 1.3;

function formatTokens(n) {
  if (!n) return "0";
  return n >= 10000 ? `${Math.round(n / 1000)}k` : n.toLocaleString();
}

// The cost is an ESTIMATE and every surface that prints it has to say so.
// `cost_usd` is null — not 0 — when it can't be stated honestly, so a nullish
// check here is the difference between "we don't know" and "it was free".
export function usageLine(usage) {
  if (!usage || !usage.total) return "";
  const bits = [
    `${formatTokens(usage.input)} in`,
    `${formatTokens(usage.output)} out`,
  ];
  if (usage.thinking) bits.push(`${formatTokens(usage.thinking)} thinking`);
  const cost =
    usage.cost_usd == null
      ? ""
      : ` · ~$${usage.cost_usd < 0.01 ? usage.cost_usd.toFixed(4) : usage.cost_usd.toFixed(2)} est.`;
  return `${formatTokens(usage.total)} tokens (${bits.join(" · ")})${cost}`;
}

export default function PlanScriptModal({
  script,
  onClose,
  onDownload,
  onSendToStoryboard,
  onDelete,
  busy,
}) {
  const [copied, setCopied] = useState(false);

  // Escape closes, matching the export preview. Declared before the early
  // return so the hook order can't change between renders.
  useEffect(() => {
    if (!script) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [script, onClose]);

  // Reset the "Copied" flash when a different script is opened, or reopening
  // one would show a stale tick.
  useEffect(() => setCopied(false), [script?.id]);

  if (!script) return null;

  const scenes = script.scenes || [];
  const cast = script.characters || [];
  const notes = script.notes || [];
  const target = script.seconds || 0;
  const estimate = script.estimated_seconds || 0;
  const overRunning = target > 0 && estimate > target * OVER_RUN_TOLERANCE;

  async function copy() {
    try {
      await navigator.clipboard.writeText(script.text || "");
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // A denied clipboard permission is not an error worth a red banner —
      // the download buttons are right there.
      setCopied(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="card export-modal script-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <button className="modal-close" onClick={onClose} title="Close" aria-label="Close">
          <Icon name="close" />
        </button>

        <header className="export-modal-head">
          <h2>{script.title}</h2>
          {script.logline && <p className="script-logline">{script.logline}</p>}

          <div className="script-chips">
            <span className="plan-chip">
              {scenes.length} scene{scenes.length === 1 ? "" : "s"}
            </span>
            {/* Target vs. what it actually reads at. Both, always — one number
                alone can't tell you whether the script is the right length. */}
            {target > 0 && (
              <span className={`plan-chip ${overRunning ? "script-chip-over" : ""}`}>
                {estimate ? `~${estimate}s` : "—"} of {target}s
              </span>
            )}
            {script.spoken_words > 0 && (
              <span className="plan-chip">{script.spoken_words} spoken words</span>
            )}
            {/* The writer's OWN read of what it wrote. It labels the script; it
                never restricted it. */}
            {script.rating && (
              <span className={`plan-chip script-rating-${script.rating}`}>
                {script.rating}
              </span>
            )}
            {script.item_slot && <span className="plan-chip">{script.item_slot}</span>}
          </div>

          {overRunning && (
            <p className="muted tiny script-over-note">
              This reads longer than you asked for. Ask again with a shorter
              target, or cut a scene.
            </p>
          )}
        </header>

        <div className="export-modal-body">
          <div className="script-doc">
            {cast.length > 0 && (
              <section className="script-cast">
                <h3>Cast</h3>
                <ul>
                  {cast.map((c) => (
                    <li key={c.name}>
                      <strong>{c.name}</strong>
                      {c.description ? ` — ${c.description}` : ""}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {scenes.map((scene) => (
              <section className="script-scene" key={scene.number}>
                <h3 className="script-slug">
                  {scene.number}. {scene.heading}
                </h3>
                {(scene.beats || []).map((beat, i) => {
                  if (beat.type === "dialogue" || beat.type === "vo") {
                    return (
                      <div className="script-speech" key={i}>
                        <span className="script-speaker">
                          {beat.character}
                          {beat.type === "vo" ? " (V.O.)" : ""}
                        </span>
                        <p className="script-line">{beat.text}</p>
                      </div>
                    );
                  }
                  if (beat.type === "text") {
                    return (
                      <p className="script-onscreen" key={i}>
                        <span className="plan-label">On screen</span>
                        {beat.text}
                      </p>
                    );
                  }
                  return (
                    <p className="script-action" key={i}>
                      {beat.text}
                    </p>
                  );
                })}
              </section>
            ))}

            {script.cta && (
              <section className="script-scene">
                <h3 className="script-slug">Call to action</h3>
                <p className="script-action">{script.cta}</p>
              </section>
            )}

            {notes.length > 0 && (
              <section className="script-cast">
                <h3>Production notes</h3>
                <ul>
                  {notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </div>

        <footer className="export-modal-foot">
          {/* What this one script cost, next to the buttons that might make
              another one. */}
          <span className="tiny muted">{usageLine(script.usage) || " "}</span>
          <div className="export-modal-actions">
            <button
              className="btn ghost script-danger"
              onClick={onDelete}
              disabled={busy}
              title="Delete this script"
            >
              <Icon name="trash" />
            </button>
            <button className="btn" onClick={copy} disabled={busy}>
              <Icon name="copy" /> {copied ? "Copied" : "Copy"}
            </button>
            <button className="btn" onClick={() => onDownload("txt")} disabled={busy}>
              <Icon name="download" /> TXT
            </button>
            <button className="btn" onClick={() => onDownload("docx")} disabled={busy}>
              <Icon name="download" /> DOCX
            </button>
            <button className="btn primary" onClick={onSendToStoryboard} disabled={busy}>
              {busy ? (
                <>
                  <span className="spinner-inline" /> Sending…
                </>
              ) : (
                <>
                  <Icon name="play" /> Open in Script to Storyboard
                </>
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
