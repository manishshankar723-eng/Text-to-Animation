// Shapes.jsx — the shape layer's vocabulary ON SCREEN: the swatch, the picker
// and the CSS that clips them.
//
// ⚠ THE GEOMETRY IS NOT HERE ANY MORE. It moved to
// `../animatic/shape_points.js` when the library went from four shapes to
// forty-one: the same table was written out in this file AND in
// `gl/compositor.js`, each copy carrying a comment apologising for the other, and
// a fifth shape would have been a third place to get it wrong. This file now
// answers one question — how a shape looks in the DOM — and the module answers
// what a shape IS. The exporter's copy (`_SHAPE_POINTS` in `animatic.py`) is
// still separate and is now held to it by `tests/shape_points_check.py`.

import LibraryFolder from "./LibraryFolder.jsx";
import {
  SHAPE_CATEGORIES,
  SHAPE_KINDS,
  SHAPE_POINTS,
  shapeLabel,
} from "../animatic/shape_points.js";

// Re-exported because everything that draws a shape already imports from this
// file, and a component asking two modules for "the shapes" is how a picker ends
// up offering a kind the swatch cannot draw.
export { SHAPE_CATEGORIES, SHAPE_KINDS, shapeLabel };

// A clip-path string per kind, built once. Some of these outlines are two hundred
// points (a flower's petals are sampled, not hand-placed), and the Shapes tab
// alone mounts forty-one swatches — rebuilding every string on every render is
// work with a known answer.
const CLIP = new Map();

/**
 * The CSS that makes a div that shape.
 *
 * `ellipse` is the one kind with no point list: a border-radius is a TRUE circle,
 * where a polygon can only approximate one — the same exception Pillow and the
 * monitor make (see `shapeOutline`). A plain box needs no clipping at all, and an
 * unknown kind falls back to one, so an old project carrying a shape this build
 * has never heard of still draws something.
 */
export function shapeCss(kind) {
  if (kind === "ellipse") return { borderRadius: "50%" };
  if (!SHAPE_POINTS[kind] || kind === "rect") return {};
  if (!CLIP.has(kind)) {
    const points = SHAPE_POINTS[kind];
    CLIP.set(
      kind,
      `polygon(${points.map(([x, y]) => `${x * 100}% ${y * 100}%`).join(", ")})`
    );
  }
  return { clipPath: CLIP.get(kind) };
}

// The colour a new shape arrives in. Deliberately not the app's gold: a shape is
// drawn ON the picture, and the gold is the UI's own colour — a gold shape over
// a storyboard panel reads as part of the editor rather than part of the film.
export const DEFAULT_SHAPE_COLOR = "#c2185b";

export function ShapeSwatch({ kind, color = "currentColor", className = "" }) {
  return (
    <span
      className={`shape-swatch ${className}`.trim()}
      style={{ background: color, ...shapeCss(kind) }}
    />
  );
}

/**
 * One tile: click to add at the playhead, drag to say where instead.
 *
 * ⚠ TWO ENTRIES ON THE CLIPBOARD, and the `…-shape` one is an empty MARKER, not
 * data: a timeline lane has to know what is coming during `dragover`, where
 * `getData` reads blank in every browser. See `dragKind` in Timeline.jsx.
 */
function ShapeTile({ kind, label, onAdd, disabled }) {
  return (
    <button
      type="button"
      className="shape-tile"
      disabled={disabled}
      onClick={() => onAdd(kind)}
      draggable={!disabled}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "copy";
        e.dataTransfer.setData(
          "application/x-anim-asset",
          JSON.stringify({ kind: "shape", id: kind })
        );
        e.dataTransfer.setData("application/x-anim-shape", "");
      }}
      title={`Add a ${label.toLowerCase()} at the playhead — or drag it onto a shape row`}
    >
      <ShapeSwatch kind={kind} color="#ffffff" className="shape-tile-art" />
      <span className="tiny muted shape-tile-name">{label}</span>
    </button>
  );
}

/**
 * The picker: folders of tiles.
 *
 * ⚠ IT IS A TREE OF GRIDS, not the flat grid it used to be. Four tiles were a row
 * you took in at a glance; forty-one are a wall, and a wall is not a bigger
 * library, it is one you stop reading — which is exactly what was asked for
 * ("cetegory like folder so user uderstand shapes"). The folder row is the SAME
 * component the Effects tab beside it uses (`LibraryFolder`), so the two tabs
 * read as one pane rather than as two people's work.
 *
 * ⚠ AND ONLY THE FIRST GROUP IS OPEN. A tile grid is taller than an effects row,
 * so five open folders is three screens of scrolling before you have picked
 * anything. Whatever you open is remembered for the session.
 */
export default function ShapeGallery({ onAdd, disabled = false }) {
  return (
    <div className="shape-lib">
      {SHAPE_CATEGORIES.map((group, i) => (
        <LibraryFolder
          key={group.id}
          id={group.id}
          label={group.label}
          note={group.note}
          count={group.kinds.length}
          defaultOpen={i === 0}
        >
          <div className="shape-gallery">
            {group.kinds.map((s) => (
              <ShapeTile
                key={s.id}
                kind={s.id}
                label={s.label}
                onAdd={onAdd}
                disabled={disabled}
              />
            ))}
          </div>
        </LibraryFolder>
      ))}
    </div>
  );
}
