// TransitionProperties.jsx — a transition's settings: which one, and how long.
//
// One of the six Properties panes. They are presentational and hold no state of
// their own: the editor decides what is selected and hands the pane the clip
// plus the handlers that write to it. Laid out with the primitives in
// `PropGroup.jsx`, like every other pane.

import Icon from "../Icon.jsx";
import {
  DEFAULT_TRANSITION_MS,
  MAX_TRANSITION_MS,
  MIN_TRANSITION_MS,
  TRANSITIONS,
} from "../../animatic/transitions.js";
import { PropGroup, PropRow, NumField, PropNote } from "./PropGroup.jsx";
import { clamp } from "../../animatic/util.js";

// A transition's settings: which one, and how long. Deliberately short — there
// is nothing else to say about a cut treatment, and the alternative (direction
// pickers, easing, a preview strip) is a lot of surface for four effects.
export default function TransitionProperties({ transition, frames, onChange, onDelete, onClose }) {
  const i = frames.findIndex((f) => f.id === transition.after_frame_id);
  const from = frames[i];
  const to = frames[i + 1];
  // What the renderer will actually use. A transition is capped at the SHORTER
  // of the two holds it joins, so it can never eat more than half of either —
  // and saying so here is better than silently ignoring the number typed in.
  const shorter = Math.min(from?.duration_ms ?? Infinity, to?.duration_ms ?? Infinity);
  const effective = Math.max(
    MIN_TRANSITION_MS,
    Math.min(transition.duration_ms, MAX_TRANSITION_MS, shorter)
  );
  const clamped = effective !== transition.duration_ms;

  return (
    <div className="an-props">
      <div className="an-prop-ident">
        <div className="an-prop-ident-text">
          <span className="an-prop-kind">Transition</span>
          <span className="an-prop-name">
            {from ? from.label || `Frame ${i + 1}` : "—"} →{" "}
            {to ? to.label || `Frame ${i + 2}` : "—"}
          </span>
          <span className="an-prop-sub">On the cut between these two shots</span>
        </div>
      </div>

      <PropGroup id="transition:kind" title="Treatment">
        <PropRow full>
          <span className="an-set-chips">
            {TRANSITIONS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`opt-chip ${transition.kind === t.id ? "active" : ""}`}
                onClick={() => onChange(transition.id, { kind: t.id })}
              >
                {t.label}
                <span className="opt-chip-note">{t.note}</span>
              </button>
            ))}
          </span>
        </PropRow>

        <PropRow
          label="Lasts"
          title="How long the blend takes"
          reset={() => onChange(transition.id, { duration_ms: DEFAULT_TRANSITION_MS })}
          changed={transition.duration_ms !== DEFAULT_TRANSITION_MS}
          resetTo={`${DEFAULT_TRANSITION_MS / 1000}s`}
        >
          <NumField
            unit="s"
            step="0.1"
            min={MIN_TRANSITION_MS / 1000}
            max={MAX_TRANSITION_MS / 1000}
            value={(transition.duration_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(transition.id, {
                duration_ms: clamp(
                  Math.round(parseFloat(e.target.value || 0) * 1000),
                  MIN_TRANSITION_MS,
                  MAX_TRANSITION_MS
                ),
              })
            }
          />
        </PropRow>

        {clamped && (
          <PropNote tone="warn">
            Trimmed to {(effective / 1000).toFixed(1)}s — a transition can't be
            longer than the shorter of the two shots it joins.
          </PropNote>
        )}

        {/* The one thing about this design worth stating outright, because every
            other editor works the other way and people expect their cut to move. */}
        <PropNote>
          The blend straddles the cut, taking half from the end of the first shot
          and half from the start of the second — so the video stays exactly as
          long, and nothing else on the timeline moves.
        </PropNote>
      </PropGroup>

      <div className="an-prop-actions">
        <button
          type="button"
          className="btn small danger-btn"
          onClick={() => onDelete(transition.id)}
        >
          <Icon name="close" /> Remove
        </button>
        <button type="button" className="btn small ghost" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
