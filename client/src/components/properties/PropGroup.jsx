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

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

// =========================================================================
// SCRUBBING — drag sideways on a number (or on its LABEL) to change it
// =========================================================================
//
// WHY. Every number in this pane was type-only: click into the box, select what
// is there, type a new one, tab out — for a value you want to feel your way to,
// like a position or a rotation. Every editor this pane is modelled on (Premiere
// and After Effects, whose Effect Controls this whole file copies) makes the
// number itself a drag handle, and it is the single control that makes a
// properties pane feel like an editor rather than a form. Asked for as "when my
// mouse crouser go on scal name or value box so user do drag … add in all
// properties panel".
//
// ⚠ IT IS WIRED ONCE, HERE, AND EVERY ROW IN EVERY PANE GETS IT. There are ~40
// `NumField`s across seven panes; a scrub added at the call sites would be ~40
// chances to forget one, and the rows that got missed would be indistinguishable
// from broken. `NumField` and `PropSlider` are the only two things a number is
// ever drawn with, so wiring those two is wiring all of them.
//
// THE FOUR RULES, each of which is a bug if you break it:
//
//   1. A PRESS IS STILL A CLICK. Nothing happens until the pointer has moved
//      `DEAD_ZONE` px, so clicking into the box to TYPE still works — which is
//      the behaviour that must not regress, because typing an exact number is
//      what this control was before.
//   2. THE DRAG IS MEASURED FROM WHERE IT STARTED, not accumulated frame by
//      frame. Accumulating drifts, and worse, it fights the caller's clamp: drag
//      past the maximum and back and an accumulating scrub never returns.
//   3. IT WRITES THROUGH THE ROW'S OWN `onChange`, as `{target: {value}}`.
//      `NumField` promises the caller gets the raw DOM event and keeps its own
//      parsing and clamping — see its docstring — and a scrub that bypassed that
//      would need a second copy of every clamp in the app.
//   4. ONE DRAG IS ONE UNDO. The pointer-down/up bracket from `useUndoStack` is
//      taken from context rather than passed down through forty call sites; a
//      scrub without it would push an undo entry per mouse-move.

/**
 * The undo stack's drag bracket, so a scrub anywhere in a pane coalesces into a
 * single history entry. Provided once by the editor; `null` outside it, which is
 * why every use is optional-chained — a pane rendered in a test or a storybook
 * still scrubs, it just does not coalesce.
 */
export const ScrubGesture = createContext(null);

/**
 * How a row's LABEL reaches the control beside it.
 *
 * The label lives in the left column and knows nothing about the value; the
 * `NumField` lives in the right column and knows everything about it. `PropRow`
 * puts a ref in between: the field writes its scrub-starter in, the label calls
 * it. That is what makes "drag the word Rotation" work without every caller
 * describing its value twice.
 */
const RowScrub = createContext(null);

// How far the pointer must travel before a press becomes a drag. Small enough
// that a deliberate drag feels immediate, large enough that a click that
// wobbles by a pixel still lands in the box.
const DEAD_ZONE = 3;
// The whole range of a BOUNDED property should take about this far to cross —
// so opacity, rotation and a 0–2 radius all feel like the same control even
// though their numbers are nothing alike.
const SWEEP_PX = 250;
// …and an UNBOUNDED one has no range to divide up, so it moves one step per this
// many pixels. 3px per 1% is roughly a screen-width for a full frame's travel.
const PX_PER_STEP = 3;

const toNumber = (value, fallback) => {
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : fallback;
};

/**
 * Returns an `onPointerDown` that scrubs `value` and reports through `onChange`.
 *
 * `min`/`max`/`step` are the input's own attributes, so a row describes its
 * range once and both typing and dragging obey it.
 */
function useScrub({ value, step, min, max, onChange, disabled }) {
  const gesture = useContext(ScrubGesture);
  // Read through a ref so the returned handler is STABLE. It is written into
  // `RowScrub` on every render, and a handler that changed identity every time
  // would make that write meaningless — and re-subscribing on each keystroke
  // while a drag was live would drop the drag.
  const latest = useRef(null);
  latest.current = { value, step, min, max, onChange, gesture, disabled };

  return useCallback((event) => {
    const now = latest.current;
    // Left button only: a right-click is a context menu and a middle-click is a
    // paste on Linux, and hijacking either is worse than not scrubbing.
    if (now.disabled || typeof now.onChange !== "function" || event.button !== 0) return;
    const start = toNumber(now.value, NaN);
    if (!Number.isFinite(start)) return;

    const stepSize = Math.abs(toNumber(now.step, 1)) || 1;
    const lo = toNumber(now.min, -Infinity);
    const hi = toNumber(now.max, Infinity);
    // ⚠ THE READOUT IS ROUNDED TO THE STEP, or a 0.05 step lands on
    // 0.35000000000000003 and the box shows it. The step's own decimals are the
    // only honest precision the row has.
    const decimals = (String(stepSize).split(".")[1] || "").length;
    // A bounded property is swept; an unbounded one is stepped. See the two
    // constants above.
    const span = hi - lo;
    const pxPerStep = Number.isFinite(span) && span > 0
      ? Math.max(2, SWEEP_PX / (span / stepSize))
      : PX_PER_STEP;

    const originX = event.clientX;
    let live = false;

    const move = (moved) => {
      const dx = moved.clientX - originX;
      if (!live) {
        if (Math.abs(dx) < DEAD_ZONE) return;
        live = true;
        // Whatever the first few pixels selected inside the box, drop it — a
        // scrub that leaves half the number highlighted looks like a bug even
        // though the value is right.
        window.getSelection?.()?.removeAllRanges?.();
        document.body.classList.add("an-scrubbing");
        now.gesture?.onPointerDown?.();
      }
      // Shift is coarse and Alt is fine, the way they are everywhere else.
      const speed = moved.shiftKey ? 10 : moved.altKey ? 0.1 : 1;
      const next = Math.min(hi, Math.max(lo, start + (dx / pxPerStep) * stepSize * speed));
      now.onChange({ target: { value: next.toFixed(decimals) } });
    };

    // ⚠ ENDED ON THE WINDOW, for the same reason `gestureProps` is: a pointer
    // released outside the box it started in never delivers a pointerup to that
    // box, and a scrub that is never ended follows the mouse for ever.
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      document.body.classList.remove("an-scrubbing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }, []);
}

/** Hand this row's scrub to its label. Called by whatever draws the number. */
function useRowScrub(begin) {
  const slot = useContext(RowScrub);
  // No dependency array on purpose: `begin` is stable, but the SLOT is what
  // matters and a row may mount its field after its label.
  useEffect(() => {
    if (!slot) return undefined;
    slot.current = begin;
    return () => {
      if (slot.current === begin) slot.current = null;
    };
  });
}

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
  // Filled in by the `NumField` in this row's controls, if it has one — see
  // `RowScrub`. Stays null on a row whose control is a colour, a select or a
  // strip of buttons, and the label is then an ordinary label.
  const scrub = useRef(null);
  const [scrubbable, setScrubbable] = useState(false);
  // Whether the label should ADVERTISE itself as draggable is a render-time
  // question and the ref is filled in an effect, so it is mirrored into state on
  // the pass after the field mounts. One extra render per row, once.
  useEffect(() => {
    setScrubbable(!!scrub.current);
  });

  return (
    <div className={`an-row ${full ? "full" : ""} ${noteOpen ? "note-on" : ""}`}>
      {label != null && (
        <span
          className={`an-row-label ${scrubbable ? "an-scrub" : ""}`}
          title={scrubbable ? `${title ? `${title}

` : ""}Drag sideways to change it` : title}
          onPointerDown={(event) => scrub.current?.(event)}
        >
          {label}
        </span>
      )}
      <div className="an-row-ctl">
        <RowScrub.Provider value={scrub}>{children}</RowScrub.Provider>
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
  // ⚠ THE BOX IS A DRAG HANDLE AS WELL AS A TEXT FIELD, and it stays a text
  // field: nothing happens until the pointer has moved, so clicking in to type
  // is untouched. See the SCRUBBING block at the top of this file.
  const begin = useScrub(input);
  useRowScrub(begin);

  // ---------------------------------------------------------------------
  // ⚠ WHILE YOU ARE TYPING, THE BOX SHOWS WHAT YOU TYPED
  // ---------------------------------------------------------------------
  // Every row here is CONTROLLED and most of them are DERIVED — `Position X`
  // shows `x × 100`, `Scale` shows a ratio of two other fields — so what the
  // box displays is the round trip of what you typed, not what you typed. That
  // is fine while the number round-trips. It stops being fine the moment a
  // clamp is involved, and then it is silently, badly broken:
  //
  //     Scale, typing "200" one digit at a time — "2" writes 2%, the width
  //     clamps up to its 2% floor, the box re-renders as "8", the next
  //     keystroke appends to THAT, and you end up at 800%.
  //
  // Caught by `tests/editor_scrub_check.py` typing into the new Scale row, but
  // it was already true of `Width` (type a leading 0 and watch it become 2).
  // So the typed text is held locally for exactly as long as the box has focus
  // and is handed back to the value on blur. `onChange` still fires on every
  // keystroke — the picture must keep up with typing — this only decides what
  // the box SHOWS while it is yours.
  const [typed, setTyped] = useState(null);
  const shown = typed ?? input.value;

  return (
    <label className="an-num">
      {tag ? <span className="an-num-tag">{tag}</span> : null}
      <input
        type="number"
        className="an-scrub"
        {...input}
        value={shown}
        onChange={(event) => {
          setTyped(event.target.value);
          input.onChange?.(event);
        }}
        // Hand the box back to the document: on blur, and on Enter, which is
        // how you say "done" without moving the mouse.
        onBlur={(event) => {
          setTyped(null);
          input.onBlur?.(event);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") setTyped(null);
          input.onKeyDown?.(event);
        }}
        // A scrub is the document talking, not you — so it takes the box back.
        onPointerDown={(event) => {
          setTyped(null);
          begin(event);
        }}
      />
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
  // The TRACK is already draggable; this is for the label, so a slider answers
  // to the same gesture as every number in the pane. It also gives a slider the
  // fine control a track 6rem wide cannot: Alt-drag steps a tenth at a time.
  const begin = useScrub(input);

  return (
    <div className={`an-row ${noteOpen ? "note-on" : ""}`}>
      <span
        className="an-row-label an-scrub"
        title={`${title ? `${title}

` : ""}Drag sideways to change it`}
        onPointerDown={begin}
      >
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
