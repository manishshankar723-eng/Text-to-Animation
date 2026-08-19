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

import { useEffect, useState } from "react";

// Which groups are open, remembered for the tab rather than for the selection.
// Module-level ON PURPOSE: collapsing "Look" because you are working on timing
// should stay collapsed when you click the next clip, or collapsing it is not
// worth doing. It is deliberately NOT persisted to the project — it is a view
// preference, and it must never end up in an exported animatic.
const OPEN = new Map();

// Every mounted group's setter, by id — what lets `openGroup()` below reach a
// section that is already on screen.
const WATCHERS = new Map();

/**
 * REVEAL A SECTION, because something just landed in it.
 *
 * ⚠ THE ONE THING A COLLAPSIBLE PANE OWES YOU. A closed section is a promise
 * that nothing you can't see is changing — and the moment an ADD control sits
 * outside the sections it fills, that promise breaks: uploading a video with
 * Frames folded shut moved a count from 31 to 32 and showed nothing else,
 * reported as "I upload a video but it's not in the media panel". It was; it was
 * in the drawer. So anything that puts content into a section opens it.
 *
 * Works whether or not the group is mounted: the memory is written either way,
 * so a section on the tab you are not looking at is already open when you get
 * there. It only ever OPENS — nothing here may close a section for you, which
 * would be the same surprise pointing the other way.
 */
export function openGroup(id) {
  OPEN.set(id, true);
  WATCHERS.get(id)?.forEach((setOpen) => setOpen(true));
}

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
 * @param info       prose behind an ⓘ at the right of the HEADER, for a note
 *                   that is about the whole section and so has no one row to
 *                   sit on. Prefer `PropRow`'s own `info`: a note attached to
 *                   the property it describes is a note you find when you need
 *                   it. Rendered in the header so it is reachable with the
 *                   section shut.
 * @param tone       "fx" draws the effect-card treatment instead of a section.
 */
export function PropGroup({
  id,
  title,
  hint,
  count,
  actions,
  info,
  tone = "",
  defaultOpen = true,
  children,
}) {
  const [open, setOpen] = useState(() => OPEN.get(id) ?? defaultOpen);
  const [noteOpen, setNoteOpen] = useState(false);

  const toggle = () =>
    setOpen((was) => {
      OPEN.set(id, !was);
      return !was;
    });

  // Subscribe this instance to `openGroup(id)`. Keyed by id and not by
  // instance, so two panes showing the same section both open.
  useEffect(() => {
    let subs = WATCHERS.get(id);
    if (!subs) WATCHERS.set(id, (subs = new Set()));
    subs.add(setOpen);
    return () => {
      subs.delete(setOpen);
      if (!subs.size) WATCHERS.delete(id);
    };
  }, [id]);

  return (
    <section
      className={`an-grp ${tone ? `an-grp-${tone}` : ""} ${open ? "open" : ""} ${
        noteOpen ? "note-on" : ""
      }`}
    >
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
        {(actions || info) && (
          <span className="an-grp-actions">
            {actions}
            {/* Last in the cluster, on the same right-hand edge every row's ⓘ
                sits on — one column of them down the whole pane. */}
            {info ? (
              <InfoDot open={noteOpen} onToggle={() => setNoteOpen((was) => !was)} />
            ) : null}
          </span>
        )}
      </div>
      {/* Outside the body, so a shut section can still be asked what it is for. */}
      {info ? <p className="an-note an-note-pop an-grp-note">{info}</p> : null}
      {/* Unmounted rather than hidden, so a collapsed group costs nothing —
          this pane re-renders on every playhead move. */}
      {open && <div className="an-grp-body">{children}</div>}
    </section>
  );
}

/**
 * ↺ — PUT THIS PROPERTY BACK THE WAY IT CAME.
 *
 * WHY IT EXISTS ON EVERY ROW. Until this, undoing one setting meant remembering
 * what its default had been and typing it back in; the only escape hatches were
 * a handful of hand-written "Reset motion" / "Flat" buttons on the groups that
 * someone had happened to need one on. Everything else you could change, you
 * could not un-change without knowing the number.
 *
 * ⚠ ALWAYS RENDERED, NEVER CONDITIONAL, and that is deliberate even though it
 * costs a faint disabled button on most rows. Two reasons, and the second is the
 * one that decided it:
 *
 *   1. a control that appears only once you have touched the property is a
 *      control you have to discover twice;
 *   2. because it is always there, its STATE is information — a lit ↺ down the
 *      pane is the list of things you have changed on this clip, which is the
 *      question you are actually asking when you go looking for a reset.
 *
 * `changed` decides which of the two it is. It is passed in rather than worked
 * out here because only the caller knows what this property's default is, and
 * putting a table of defaults in a layout file is how the pane and the document
 * start disagreeing about them.
 *
 * @param onReset  what to write. Called with no arguments.
 * @param changed  is the value away from its default right now?
 * @param title    what it goes back TO ("100%", "flat", "the whole file").
 */
export function ResetButton({ onReset, changed = false, title }) {
  return (
    <button
      type="button"
      className={`an-kf-btn an-reset ${changed ? "on" : ""}`}
      disabled={!changed}
      onClick={onReset}
      title={
        changed
          ? `Reset${title ? ` to ${title}` : ""}`
          : `Already at its default${title ? ` (${title})` : ""}`
      }
      aria-label="Reset this property"
    >
      ↺
    </button>
  );
}

/**
 * ⓘ — THE EXPLANATION FOR THIS PROPERTY, FOLDED UNTIL YOU ASK FOR IT.
 *
 * It lives ON THE ROW, in the cluster at the right-hand edge with ⏱ and ↺, and
 * never on a line of its own. That is the whole point: the teaching prose used
 * to be printed under the group as a paragraph, so a pane of five sections
 * carried five grey blocks and the properties people came to change were the
 * shortest thing on screen. Given a line of its own the ICON has the same fault
 * in miniature — every note pushes the next property down, and the column of
 * ↺'s that tells you what you have changed stops being a column.
 *
 * Hover shows it. Click PINS it open, which is what makes it work on a touch
 * screen and what lets you read a long note without holding the pointer still.
 *
 * The prose opens IN FLOW under the row (`.an-note-pop` spans both columns, like
 * `.an-row-hint`) rather than floating over the pane: `.an-grp` is
 * `overflow: hidden` for its corners and `.an-pane-body` scrolls, so a popover
 * would be clipped by one or the other.
 */
/**
 * ⓘ — the explanation, on ask.
 *
 * ⚠ EXPORTED, because it is not only a Properties control any more. The Effects
 * library's rows carry one too: every entry there used to print its whole
 * description beside its name, which is the same fault this exists to fix, one
 * pane over (user-reported, twice). One component rather than a second circle
 * that is nearly the same size — the ⓘ has to be the SAME thing everywhere or
 * it stops reading as a convention and starts reading as decoration.
 */
export function InfoDot({ open, onToggle }) {
  return (
    <button
      type="button"
      className="an-note-i"
      aria-expanded={open}
      onClick={onToggle}
      title={open ? "Hide this explanation" : "What this does"}
      aria-label="What this does"
    >
      i
    </button>
  );
}

/**
 * One property: a label in the left column, its controls in the right.
 *
 * The ⏱ goes in `children` as the LAST item — it is pushed to the right edge by
 * `margin-left:auto`, and wraps under the value on a narrow pane instead of
 * squeezing it. The ↺ goes AFTER it, at the very end of the row, so the two
 * always sit in the same order however many controls the row has.
 *
 * @param label   the property name. Nothing else in the left column, ever: a
 *                value or a unit in there is what breaks the alignment.
 * @param title   the tooltip — where the long explanation goes.
 * @param hint    a line of prose under the row, spanning both columns. ALWAYS
 *                shown — for the one thing about this row you cannot leave to a
 *                tooltip. If it is teaching prose, it wants `info` instead.
 * @param info    the same prose, behind the ⓘ in the row's right-hand cluster.
 *                This is where an explanation belongs; `hint` is for a fact the
 *                user must not be able to miss.
 * @param full    controls take the whole width and the label sits above them.
 *                For sliders that need the room, and for buttons.
 * @param reset   () => void — put this property back to its default. Omit only
 *                on rows that HAVE no default: a read-out, a button, a pile of
 *                presets. Every row with a value should pass one.
 * @param changed is that value away from its default right now?
 * @param resetTo what it goes back to, for the tooltip.
 */
export function PropRow({
  label,
  title,
  hint,
  info,
  full = false,
  reset,
  changed = false,
  resetTo,
  children,
}) {
  // Pinned open by a click. Per-row, so two notes can be open at once — which is
  // what you want when you are comparing two properties, and there is nothing
  // here that a second open note can break.
  const [noteOpen, setNoteOpen] = useState(false);

  return (
    <div className={`an-row ${full ? "full" : ""} ${noteOpen ? "note-on" : ""}`}>
      {label != null && (
        <span className="an-row-label" title={title}>
          {label}
        </span>
      )}
      <div className="an-row-ctl">
        {children}
        {/* ⚠ ⓘ BEFORE ↺, ALWAYS. The reset is the last thing on every row — that
            is what makes a column of them readable as "everything I have
            changed" — so the note tucks in beside it rather than past it. */}
        {info ? <InfoDot open={noteOpen} onToggle={() => setNoteOpen((was) => !was)} /> : null}
        {reset ? <ResetButton onReset={reset} changed={changed} title={resetTo} /> : null}
      </div>
      {info ? <p className="an-note an-note-pop">{info}</p> : null}
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
export function PropSlider({
  label,
  title,
  readout,
  kf,
  hint,
  info,
  reset,
  changed = false,
  resetTo,
  ...input
}) {
  const [noteOpen, setNoteOpen] = useState(false);

  return (
    <div className={`an-row ${noteOpen ? "note-on" : ""}`}>
      <span className="an-row-label" title={title}>
        {label}
      </span>
      <div className="an-row-ctl">
        <input type="range" {...input} />
        <span className="an-num-read">{readout}</span>
        {kf}
        {info ? <InfoDot open={noteOpen} onToggle={() => setNoteOpen((was) => !was)} /> : null}
        {reset ? <ResetButton onReset={reset} changed={changed} title={resetTo} /> : null}
      </div>
      {info ? <p className="an-note an-note-pop">{info}</p> : null}
      {hint ? <p className="an-row-hint">{hint}</p> : null}
    </div>
  );
}

/**
 * A line of prose in the pane, in plain sight.
 *
 * ⚠ THIS IS FOR WARNINGS. Teaching prose goes behind the ⓘ — `info` on the
 * `PropRow` it is about, or on the `PropGroup` if it is about all of them.
 *
 * The difference is what KIND of sentence it is:
 *
 *   info    "100% is the file as recorded…". True forever, and useful exactly
 *           once. Behind the ⓘ, because printed in full it out-shouts the
 *           controls — a pane of five sections carried five grey paragraphs and
 *           the properties people came to change were the shortest thing on
 *           screen.
 *   warn    "this clip runs past the end of the video". Conditional, about the
 *           state you are in RIGHT NOW, and only rendered when it is true — so
 *           it stays where you cannot miss it. ⚠ Never fold one behind an ⓘ: a
 *           notice you have to go looking for is a notice nobody reads.
 *
 * `tone` defaults to "" so an older call still renders — as plain prose, which
 * is what it always was.
 */
export function PropNote({ tone = "", children }) {
  return <p className={`an-note ${tone ? `an-note-${tone}` : ""}`}>{children}</p>;
}
