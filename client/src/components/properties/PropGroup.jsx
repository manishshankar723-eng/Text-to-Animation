// PropGroup.jsx — the layout every Properties pane is built from.
//
// WHY THIS FILE EXISTS. Each pane used to lay itself out by hand: a flex row per
// property, a label that was sometimes `.an-prop-label` (5.2rem wide) and
// sometimes the `<span>` inside `.an-tp-field` (as wide as its own text), and a
// ⏱ tucked in wherever it fitted. Nothing lined up with anything below it, so a
// pane of fifteen properties read as fifteen unrelated little forms — the exact
// complaint that produced this file.
//
// The fix is Premiere's Effect Controls, which is the grammar every editor
// already knows:
//
//   ▾ GROUP           a named, collapsible section — Clip, Source, Motion, Look
//     label  value ⏱  one property per row, on a two-column grid
//
// THE TWO RULES THAT MAKE IT LOOK LIKE ONE PANE. Break either and the alignment
// goes, which is the whole point of this file:
//
//   1. One property = one `PropRow`. Never two animatable properties on a row —
//      each needs its own ⏱, and a row with two of them cannot line its values
//      up with the row above. It also keeps the pane in step with the timeline,
//      which draws ONE DIAMOND ROW PER ANIMATED PROPERTY in `ANIMATABLE` order.
//   2. Rows stay in `ANIMATABLE` order (scale, x, y, opacity for a frame). The
//      timeline's rows are that order, and the two panes are meant to read top
//      to bottom as the same list. See `renderKeys` in `Timeline.jsx`.
//
// Everything here is presentational — no state beyond which groups are open, no
// knowledge of what a clip is — so panes keep writing through their own handlers
// and can never disagree with the document.

import { useState } from "react";

// Which groups are open, remembered for the tab rather than for the selection.
// Module-level ON PURPOSE: collapsing "Look" because you are working on timing
// should stay collapsed when you click the next clip, or collapsing it is not
// worth doing. It is deliberately NOT persisted to the project — it is a view
// preference, and it must never end up in an exported animatic.
const OPEN = new Map();

/**
 * A named, collapsible section of the pane.
 *
 * @param id         stable key for the open/closed memory ("frame:motion").
 *                   Must be unique across panes — two groups sharing an id
 *                   would collapse together.
 * @param title      the section name. A NOUN, capitalised, no trailing colon.
 * @param hint       one short line under the title, for the thing about this
 *                   section that isn't obvious. Optional, and usually skipped.
 * @param count      a number shown as a pill after the title (effects: 3).
 * @param actions    buttons for the header — rendered OUTSIDE the toggle, since
 *                   a <button> may not contain another one.
 * @param tone       "fx" draws the effect-card treatment instead of a section.
 */
export function PropGroup({
  id,
  title,
  hint,
  count,
  actions,
  tone = "",
  defaultOpen = true,
  children,
}) {
  const [open, setOpen] = useState(() => OPEN.get(id) ?? defaultOpen);

  const toggle = () =>
    setOpen((was) => {
      OPEN.set(id, !was);
      return !was;
    });

  return (
    <section className={`an-grp ${tone ? `an-grp-${tone}` : ""} ${open ? "open" : ""}`}>
      <div className="an-grp-head">
        <button
          type="button"
          className="an-grp-toggle"
          onClick={toggle}
          aria-expanded={open}
          title={open ? `Hide ${title}` : `Show ${title}`}
        >
          <span className="an-grp-twist" aria-hidden="true">
            ▸
          </span>
          <span className="an-grp-title">{title}</span>
          {count != null && <span className="an-grp-count">{count}</span>}
          {hint && <span className="an-grp-hint">{hint}</span>}
        </button>
        {actions ? <span className="an-grp-actions">{actions}</span> : null}
      </div>
      {/* Unmounted rather than hidden, so a collapsed group costs nothing —
          this pane re-renders on every playhead move. */}
      {open && <div className="an-grp-body">{children}</div>}
    </section>
  );
}

/**
 * One property: a label in the left column, its controls in the right.
 *
 * The ⏱ goes in `children` as the LAST item — it is pushed to the right edge by
 * `margin-left:auto`, and wraps under the value on a narrow pane instead of
 * squeezing it.
 *
 * @param label  the property name. Nothing else in the left column, ever: a
 *               value or a unit in there is what breaks the alignment.
 * @param title  the tooltip — where the long explanation goes.
 * @param hint   a line of prose under the row, spanning both columns.
 * @param full   controls take the whole width and the label sits above them.
 *               For sliders that need the room, and for buttons.
 */
export function PropRow({ label, title, hint, full = false, children }) {
  return (
    <div className={`an-row ${full ? "full" : ""}`}>
      {label != null && (
        <span className="an-row-label" title={title}>
          {label}
        </span>
      )}
      <div className="an-row-ctl">{children}</div>
      {hint ? <p className="an-row-hint">{hint}</p> : null}
    </div>
  );
}

/**
 * A number, its unit, and optionally a one-letter tag in front of it.
 *
 * Presentational only: `onChange` gets the raw DOM event, so every caller keeps
 * the parsing and clamping it already had — this box has no opinion about what
 * a legal value is, and adding one here would put a clamp in two places.
 */
export function NumField({ tag, unit, ...input }) {
  return (
    <label className="an-num">
      {tag ? <span className="an-num-tag">{tag}</span> : null}
      <input type="number" {...input} />
      {unit ? <span className="an-num-unit">{unit}</span> : null}
    </label>
  );
}

/**
 * A slider with its value read out beside it, sized to the row like every other
 * control. `kf` is the ⏱ node, which sits after the readout.
 */
export function PropSlider({ label, title, readout, kf, hint, ...input }) {
  return (
    <div className="an-row">
      <span className="an-row-label" title={title}>
        {label}
      </span>
      <div className="an-row-ctl">
        <input type="range" {...input} />
        <span className="an-num-read">{readout}</span>
        {kf}
      </div>
      {hint ? <p className="an-row-hint">{hint}</p> : null}
    </div>
  );
}

/**
 * A line of prose in the pane. `tone` is "" for an explanation, "warn" for
 * "this is probably not what you meant" — never red, because nothing this pane
 * can say is an error.
 */
export function PropNote({ tone = "", children }) {
  return <p className={`an-note ${tone ? `an-note-${tone}` : ""}`}>{children}</p>;
}
