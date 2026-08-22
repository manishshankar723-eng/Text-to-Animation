// LibraryFolder.jsx — the collapsible folder the Media pane's libraries are
// built out of.
//
// It came out of `EffectsLibrary.jsx` the moment the Shapes tab needed folders
// too ("cetegory like folder so user uderstand shapes"). Copying the disclosure
// row into a second file would have been two folders that resemble each other
// rather than one idiom — the twist would drift, the counts would sit at
// different sizes, and the two tabs beside each other would quietly stop looking
// like the same pane.
//
// ⚠ THE CLASS NAMES ARE STILL `fx-*`. They are the TREE's classes, not the
// effects tab's, and renaming them would touch every rule in
// `animatic-tools.css` for no change on screen. Read `fx-` here as "folder", not
// as "effect".
//
// ⚠ IT HOLDS NO PROJECT STATE. Which folders are open is a view preference and
// lives in the module-level map below, exactly as `PropGroup`'s does.

import { useState } from "react";

// Open/closed, remembered for the life of the tab and shared by every mount:
// switching to Media and back must not fold up the folder you were working out
// of. ⚠ ONE MAP FOR BOTH LIBRARIES, which is safe because the ids cannot collide:
// the effects tree's come from `FX_LIBRARY` ("video-effects", "dissolve", …) and
// the shape groups' are all prefixed `shapes:` (`SHAPE_CATEGORIES`). A third
// caller must keep that up, or two folders open as one.
const OPEN = new Map();

/**
 * The top-level folders start open and the sections inside them start shut: one
 * screen that shows you what KINDS of thing exist, rather than a wall of every
 * entry, which is the shape a tree is for.
 *
 * ⚠ `fallback` OVERRIDES THAT FOR A CALLER THAT CANNOT AFFORD IT. The shape
 * groups are grids of tiles, not one-line rows: five of them open at once is
 * three screens of scrolling before you have picked anything, so the Shapes tab
 * opens only its first. A folder the user has touched is remembered either way —
 * the map wins over both defaults.
 */
export function initiallyOpen(id, depth, fallback) {
  if (OPEN.has(id)) return OPEN.get(id);
  return fallback === undefined ? depth === 0 : fallback;
}

export function Twist({ open }) {
  // The same ▸ as every collapsible section in the Properties pane, rotated by
  // CSS — a second disclosure idiom on one screen is a second thing to learn.
  return (
    <span className={`fx-twist ${open ? "on" : ""}`} aria-hidden="true">
      ▸
    </span>
  );
}

export default function LibraryFolder({
  id,
  label,
  note,
  count,
  depth = 0,
  defaultOpen,
  children,
}) {
  const [open, setOpen] = useState(() => initiallyOpen(id, depth, defaultOpen));
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
          {/* A folder, drawn rather than iconised: the two libraries are the only
              trees in the editor, so a shared <Icon> entry would have exactly
              two callers and one more indirection. */}
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
