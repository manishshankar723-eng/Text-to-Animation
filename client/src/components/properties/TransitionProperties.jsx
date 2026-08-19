// TransitionProperties.jsx — a transition's settings: which one, which way
// it travels or which colour it goes out through, and how long.
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
  TRANSITION_DIRECTIONS,
  TRANSITION_PARAMS,
  TRANSITION_PARAM_RANGE,
  transitionKind,
  transitionParams,
  transitionsByFamily,
} from "../../animatic/transitions.js";
import { PropGroup, PropRow, NumField, PropNote, PropSlider } from "./PropGroup.jsx";
import { clamp } from "../../animatic/util.js";

// The arrow is the control and the word is only its title, because a direction
// is a picture: → reads faster than "rightwards" and four of them fit on one
// row, which a column of words would not.
const DIRECTIONS = {
  left: { glyph: "\u2190", word: "leftwards" },
  right: { glyph: "\u2192", word: "rightwards" },
  up: { glyph: "\u2191", word: "upwards" },
  down: { glyph: "\u2193", word: "downwards" },
};

// Computed once: the descriptor is static, so regrouping it on every render of
// the pane would be work that can never produce a different answer.
const FAMILIES = transitionsByFamily();

// A transition's settings: which one, how it behaves, and how long.
//
// ⚠ WHICH CONTROLS APPEAR IS DECIDED BY `TRANSITION_PARAMS`, not by a list
// here. A kind that offers no parameter shows no row, and a parameter added to
// the table without a control here would be invisible rather than silently
// ignored — which is why the table is the thing both this pane and the two
// renderers read.
export default function TransitionProperties({
  transition,
  frames,
  background = "#000000",
  onChange,
  onDelete,
  onClose,
}) {
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

  // Resolved, so every parameter is present with its default filled in even on
  // a transition saved before that parameter existed.
  const kind = transitionKind(transition);
  const offers = TRANSITION_PARAMS[kind] || {};
  const params = transitionParams(transition);
  // ⚠ THE RESOLVED VALUES GO IN UNDERNEATH THE STORED ONES. Writing `{ [name]:
  // value }` alone would drop every other parameter of the kind; writing only
  // the stored dict would drop the ones that were never written. Same merge the
  // effects panel does, for the same reason.
  const setParam = (name, value) =>
    onChange(transition.id, {
      params: { ...params, ...(transition.params || {}), [name]: value },
    });

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
        {/* ⚠ GROUPED BY FAMILY, and the grouping comes from the descriptor in
            `transitions.js` rather than from a list here — the same rule the
            rows below follow, where `TRANSITION_PARAMS` decides which controls
            appear. A treatment added to the model and filed nowhere lands in
            "Other" rather than vanishing.

            This is a heading and a row of chips, NOT a `PropGroup` each: the
            families are five labels, and five collapsible sections to open
            before you can see twelve chips is worse than the flat row it
            replaced. `PropRow full` and `opt-chip` are the primitives the pane
            already uses. */}
        {FAMILIES.map((family) => (
          <PropRow full key={family.id}>
            <span className="an-set-family">{family.label}</span>
            <span className="an-set-chips">
              {family.items.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  /* The FOLDED kind, not the stored one, so the chip that lights
                     up is the treatment the monitor is actually drawing. */
                  className={`opt-chip ${kind === t.id ? "active" : ""}`}
                  onClick={() => onChange(transition.id, { kind: t.id })}
                >
                  {t.label}
                  <span className="opt-chip-note">{t.note}</span>
                </button>
              ))}
            </span>
          </PropRow>
        ))}

        {"direction" in offers && (
          <PropRow
            label="Travels"
            title="Which way it moves across the frame"
            reset={() => setParam("direction", offers.direction)}
            changed={params.direction !== offers.direction}
            resetTo={DIRECTIONS[offers.direction].word}
            info={
              kind === "slide"
                ? "The way BOTH shots travel. Pick ← and the outgoing shot is pushed off to the left while the arriving one follows it in from the right."
                : kind === "split"
                  ? "The AXIS the doors open along. ← and → both split the frame down the middle; ↑ and ↓ both split it across."
                  : "The way the EDGE travels. Pick → and the edge starts at the left and sweeps right, uncovering the arriving shot behind it."
            }
          >
            <span className="an-set-chips">
              {TRANSITION_DIRECTIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  title={DIRECTIONS[d].word}
                  className={`opt-chip ${params.direction === d ? "active" : ""}`}
                  onClick={() => setParam("direction", d)}
                >
                  {DIRECTIONS[d].glyph}
                </button>
              ))}
            </span>
          </PropRow>
        )}

        {"color" in offers && (
          <PropRow
            label="Through"
            title="The colour the shot goes out through"
            reset={() => setParam("color", offers.color)}
            changed={params.color !== offers.color}
            resetTo="the bar colour"
            hint={
              params.color
                ? null
                : "The bar colour, so the dip and the letterbox match."
            }
          >
            <input
              type="color"
              className="an-colour"
              value={params.color || background || "#000000"}
              onChange={(e) => setParam("color", e.target.value)}
            />
          </PropRow>
        )}

        {"count" in offers && (
          <PropRow
            label={kind === "blinds" ? "Bands" : "Squares"}
            title="How many the frame is divided into"
            reset={() => setParam("count", offers.count)}
            changed={params.count !== offers.count}
            resetTo={String(offers.count)}
            info="Across the frame, not down it — on a 16:9 frame a chequerboard of 6 is 6 wide and however many rows that makes."
          >
            <NumField
              step="1"
              min={TRANSITION_PARAM_RANGE.count[0]}
              max={TRANSITION_PARAM_RANGE.count[1]}
              value={params.count}
              onChange={(e) =>
                setParam(
                  "count",
                  clamp(
                    Math.round(parseFloat(e.target.value) || offers.count),
                    TRANSITION_PARAM_RANGE.count[0],
                    TRANSITION_PARAM_RANGE.count[1]
                  )
                )
              }
            />
          </PropRow>
        )}

        {"softness" in offers && (
          <PropSlider
            label="Edge"
            title="How soft the edge of the reveal is"
            min={TRANSITION_PARAM_RANGE.softness[0]}
            max={TRANSITION_PARAM_RANGE.softness[1]}
            step="0.01"
            value={params.softness}
            readout={params.softness > 0 ? `${Math.round(params.softness * 100)}%` : "hard"}
            reset={() => setParam("softness", offers.softness)}
            changed={params.softness !== offers.softness}
            resetTo="hard"
            /* Worth saying outright, because "0 is a hard edge" is the one
               thing about this control that is not visible from the slider:
               every shape defaults to the edge it has always had. */
            info="0 is a hard edge, which is what a wipe has always been. Anything above it feathers the reveal — and the feather is taken out of the transition's own time, so it is still exactly invisible at both ends of the window."
            onChange={(e) => setParam("softness", parseFloat(e.target.value))}
          />
        )}

        <PropRow
          label="Lasts"
          title="How long the blend takes"
          reset={() => onChange(transition.id, { duration_ms: DEFAULT_TRANSITION_MS })}
          changed={transition.duration_ms !== DEFAULT_TRANSITION_MS}
          resetTo={`${DEFAULT_TRANSITION_MS / 1000}s`}
          /* The one thing about this design worth stating outright, because
             every other editor works the other way and people expect their cut
             to move. */
          info="The blend straddles the cut, taking half from the end of the first shot and half from the start of the second — so the video stays exactly as long, and nothing else on the timeline moves."
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
