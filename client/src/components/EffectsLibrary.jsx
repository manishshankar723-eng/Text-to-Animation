// EffectsLibrary.jsx — the Effects tab in the Media pane: a folder tree you
// drag from.
//
// It sits beside Media and Shapes for the reason those two are separate at all:
// this is a LIBRARY you take from, not a list of what this animatic contains.
// The Media tab answers "what footage is in this film", the Shapes tab and this
// one answer "what can I add to it".
//
// ⚠ IT HOLDS NO PROJECT STATE AND WRITES NOTHING. A drag carries a payload the
// timeline reads (`fxPayload`), and a click calls `onAdd` — the editor decides
// what either one means, exactly as `ShapeGallery` does. That is what lets the
// same tile drop onto a picture, a cut or nothing at all without this file
// knowing any of the rules.
//
// ⚠ TWO ENTRIES GO ON THE CLIPBOARD PER DRAG, and the empty one is not
// redundant: `getData` is blank during `dragover` in every browser, so a lane
// can only tell what is coming from the TYPE LIST. Without the marker the
// picture rows could not light up (and the audio rows could not refuse) until
// the drop had already happened. See `dragKind` in `Timeline.jsx`.

import { useState } from "react";

import { FX_LIBRARY, fxMarkerType, fxPayload } from "../animatic/fx_library.js";
import { InfoDot } from "./properties/PropGroup.jsx";

// Open/closed, remembered for the life of the tab and shared by every mount —
// the same trick `PropGroup` uses, and for the same reason: switching to Media
// and back must not fold up the folder you were working out of. Module level
// rather than lifted into the editor because it is a view preference, not
// project state, and nothing outside this file has any use for it.
const OPEN = new Map();

// The top-level folders start open and the sections inside them start shut: one
// screen that shows you what KINDS of thing exist, rather than a wall of every
// entry, which is the shape a tree is for.
function initiallyOpen(id, depth) {
  if (OPEN.has(id)) return OPEN.get(id);
  return depth === 0;
}

function Twist({ open }) {
  // The same ▸ as every collapsible section in the Properties pane, rotated by
  // CSS — a second disclosure idiom on one screen is a second thing to learn.
  return (
    <span className={`fx-twist ${open ? "on" : ""}`} aria-hidden="true">
      ▸
    </span>
  );
}

function Folder({ id, label, note, count, depth, children }) {
  const [open, setOpen] = useState(() => initiallyOpen(id, depth));
  const toggle = () => {
    const next = !open;
    OPEN.set(id, next);
    setOpen(next);
  };
  return (
    <div className={`fx-folder ${open ? "open" : ""}`} data-depth={depth}>
      <button
        type="button"
        className="fx-row fx-folder-row"
        onClick={toggle}
        aria-expanded={open}
        title={note || (open ? `Hide ${label}` : `Show ${label}`)}
      >
        <Twist open={open} />
        <span className="fx-folder-ico" aria-hidden="true">
          {/* A folder, drawn rather than iconised: this is the one place in the
              editor with a tree, so a shared <Icon> entry would have exactly
              one caller. */}
          <svg viewBox="0 0 16 16" width="13" height="13">
            <path
              d="M1.5 3.5h4l1.2 1.6h7.8v7.4H1.5z"
              fill="currentColor"
              opacity="0.35"
            />
            <path
              d="M1.5 3.5h4l1.2 1.6h7.8v7.4H1.5z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="fx-row-name">{label}</span>
        {count != null && <span className="fx-count">{count}</span>}
      </button>
      {open && <div className="fx-folder-body">{children}</div>}
    </div>
  );
}

/**
 * One draggable entry.
 *
 * Draggable AND clickable, deliberately. Drag is the gesture this was asked
 * for and the one that says WHERE it lands; click is the keyboard-and-touch
 * path to the same thing, and it targets whatever the playhead is on. Leaving
 * click out would make the whole library unreachable without a mouse.
 */
function Entry({ entry, onAdd }) {
  const [noteOpen, setNoteOpen] = useState(false);
  return (
    <div className={`fx-entry-wrap ${noteOpen ? "note-on" : ""}`}>
    <button
      type="button"
      className={`fx-row fx-entry fx-entry-${entry.type}`}
      draggable
      onDragStart={(e) => {
        // COPY, not move: the library keeps its tile. `allowedEffect` in the
        // timeline reads this back, and a drop whose effect is not in the
        // drag's `effectAllowed` is filtered out by the browser silently.
        e.dataTransfer.effectAllowed = "copy";
        e.dataTransfer.setData(
          "application/x-anim-asset",
          JSON.stringify(fxPayload(entry))
        );
        // WHICH marker, because the rows that take a crossfade are not the rows
        // that take an effect — see `fxMarkerType`.
        e.dataTransfer.setData(fxMarkerType(entry), "");
      }}
      onClick={() => onAdd?.(entry)}
      title={
        entry.note
          ? `${entry.label} — ${entry.note}\nDrag it onto the timeline, or click to add it at the playhead.`
          : entry.label
      }
    >
      {/* ⚠ THE ARROW IS THE ICON, not an extra column. Four wipes whose names
          differ by one word at the end are four rows you have to READ; the
          glyph is what makes the one you want findable at a glance, and a
          preset with no direction keeps the plain dot. `aria-hidden` because
          the label already says "up" in words — an arrow read aloud is noise. */}
      <span
        className={`fx-entry-ico fx-ico-${entry.type} ${entry.glyph ? "fx-ico-glyph" : ""}`}
        aria-hidden="true"
      >
        {entry.glyph}
      </span>
      <span className="fx-row-name">{entry.label}</span>
    </button>
      {/* ⚠ THE DESCRIPTION IS BEHIND THE ⓘ, not printed beside the name — the
          standing instruction for this whole editor, and this tree was the last
          place still ignoring it (user-reported). Thirty-two entries each
          carrying a sentence made a folder you had to READ rather than scan,
          and the sentences were the wider half of every row: the one word you
          are actually looking for ("Blinds up") was the part being truncated.
          The SAME ⓘ as the Properties pane, imported rather than redrawn, so it
          reads as one convention rather than as two circles that resemble each
          other. */}
      {entry.note ? (
        <InfoDot open={noteOpen} onToggle={() => setNoteOpen((was) => !was)} />
      ) : null}
      {noteOpen && entry.note ? (
        <p className="an-note fx-entry-note">{entry.note}</p>
      ) : null}
    </div>
  );
}

export default function EffectsLibrary({ onAdd }) {
  return (
    <div className="fx-lib">
      {FX_LIBRARY.map((shelf) => (
        <Folder
          key={shelf.id}
          id={shelf.id}
          label={shelf.label}
          note={shelf.note}
          count={shelf.sections.reduce((n, s) => n + s.items.length, 0)}
          depth={0}
        >
          {shelf.sections.map((section) => (
            <Folder
              key={section.id}
              id={section.id}
              label={section.label}
              count={section.items.length}
              depth={1}
            >
              {section.items.map((entry) => (
                <Entry key={`${entry.type}:${entry.id}`} entry={entry} onAdd={onAdd} />
              ))}
            </Folder>
          ))}
        </Folder>
      ))}
    </div>
  );
}
