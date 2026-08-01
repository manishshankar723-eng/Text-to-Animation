// Shapes.jsx — the shape layer's vocabulary, in one place.
//
// A shape is drawn TWICE: here in the browser with CSS, and again by Pillow when
// the video is encoded. The two must agree, so both read from the same list of
// points on the unit square.
//
// ⚠ THE SAME POINTS ARE IN `animatic.py` as `_SHAPE_POINTS`. Change a polygon
// here and change it there, or the preview stops being what gets exported.
const POINTS = {
  rect: [[0, 0], [1, 0], [1, 1], [0, 1]],
  pentagon: [[0.5, 0], [1, 0.38], [0.82, 1], [0.18, 1], [0, 0.38]],
  star: [
    [0.5, 0], [0.61, 0.35], [0.98, 0.35], [0.68, 0.57],
    [0.79, 0.91], [0.5, 0.7], [0.21, 0.91], [0.32, 0.57],
    [0.02, 0.35], [0.39, 0.35],
  ],
};

// Ellipse has no point list — it's a border-radius, and Pillow draws it as an
// ellipse. Everything else is a clip-path built from the table above.
export function shapeCss(kind) {
  if (kind === "ellipse") return { borderRadius: "50%" };
  const points = POINTS[kind] || POINTS.rect;
  if (kind === "rect") return {}; // a plain box needs no clipping
  return {
    clipPath: `polygon(${points.map(([x, y]) => `${x * 100}% ${y * 100}%`).join(", ")})`,
  };
}

export const SHAPE_KINDS = [
  { id: "rect", label: "Square" },
  { id: "ellipse", label: "Circle" },
  { id: "pentagon", label: "Pentagon" },
  { id: "star", label: "Star" },
];

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

// The picker: click a shape, get that shape on the timeline at the playhead.
export default function ShapeGallery({ onAdd, disabled = false }) {
  return (
    <div className="shape-gallery">
      {SHAPE_KINDS.map((s) => (
        <button
          key={s.id}
          type="button"
          className="shape-tile"
          disabled={disabled}
          onClick={() => onAdd(s.id)}
          title={`Add a ${s.label.toLowerCase()} at the playhead`}
        >
          <ShapeSwatch kind={s.id} color="#ffffff" className="shape-tile-art" />
          <span className="tiny muted">{s.label}</span>
        </button>
      ))}
    </div>
  );
}
