// shape_points.js — WHAT EVERY SHAPE IS, as points on the unit square.
//
// A shape is drawn THREE times: as a CSS `clip-path` in the pane (`Shapes.jsx`),
// as a triangle fan in the Program monitor (`gl/compositor.js`), and as a Pillow
// polygon when the video is encoded (`_SHAPE_POINTS` in `animatic.py`). The three
// must agree or the preview stops being what gets exported.
//
// ⚠ THIS FILE IS NOW THE ONLY COPY ON THE JS SIDE. The table used to be written
// out twice — once here-ish in `Shapes.jsx` and once in `compositor.js` — with a
// comment in each apologising for the other. Both read this module now, so a new
// shape is added in ONE place in the browser.
//
// ⚠ THE PYTHON SIDE IS STILL A SECOND COPY, and it has to be: the exporter runs
// with no JS at all. What keeps it honest is `tests/shape_points_check.py`, which
// loads THIS module under node and compares every point against `animatic.py` —
// so the drift the old comments merely regretted is now a failing test. The
// builders below are mirrored there line for line; change one, change both.
//
// ⚠ AND IT IS PURE — no React, no DOM — for the same reason `assets.js` and
// `scene.js` are: a test has to be able to import it under node.
//
// ─────────────────────────────────────────────────────────────────────────────
// TWO RULES EVERY SHAPE HERE OBEYS
//
//  1. IT FILLS ITS BOX, or is centred in it. The points are fractions of the
//     shape's own w/h box, so the same list draws at 25% of a 720p frame and at
//     25% of a 4K one.
//
//  2. IT IS STAR-SHAPED ABOUT (0.5, 0.5) — every point of the outline can be
//     reached from the centre in a straight line without leaving the shape. This
//     is not a style rule, it is what makes the monitor able to draw it: the
//     compositor triangulates with a FAN anchored at the centre (`shapeFan`), and
//     a shape that breaks this draws as a mess that still looks vaguely like the
//     shape — the worst kind of wrong. `tests/shape_points_check.py` proves it
//     for every entry, which is the only reason a shape can safely be added by
//     eye.
//
// That second rule is why there is no ring, no crescent and no checkerboard here:
// a hole or a second island cannot be one fan, and faking one with a keyhole
// polygon would draw correctly in CSS and wrongly in the monitor.
// ─────────────────────────────────────────────────────────────────────────────

const TAU = Math.PI * 2;

// The same rounding on both sides of the fence. ⚠ NOT `Math.round`, and not
// Python's `round()` either: those two disagree on exact halves and on negative
// numbers, and this table is compared across the two languages point by point.
// `floor(v * 1e6 + 0.5)` is one expression that means the same thing in both.
const r6 = (v) => Math.floor(v * 1e6 + 0.5) / 1e6;

// Consecutive duplicates are dropped, and so is a last point that repeats the
// first. Arcs that meet (a rounded corner whose radius is half the box, the two
// halves of a heart) land on the same coordinate twice, and a zero-length edge is
// a degenerate triangle in the fan and a wasted point in every clip-path.
function dedupe(points) {
  const out = [];
  for (const p of points) {
    const last = out[out.length - 1];
    if (!last || Math.abs(p[0] - last[0]) > 1e-9 || Math.abs(p[1] - last[1]) > 1e-9) {
      out.push(p);
    }
  }
  const first = out[0];
  const last = out[out.length - 1];
  if (out.length > 1 && Math.abs(first[0] - last[0]) < 1e-9 && Math.abs(first[1] - last[1]) < 1e-9) {
    out.pop();
  }
  return out;
}

/** Stretch a point list so its bounding box IS the unit square. */
function fit(points) {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  const sx = x1 > x0 ? 1 / (x1 - x0) : 1;
  const sy = y1 > y0 ? 1 / (y1 - y0) : 1;
  return dedupe(points.map(([x, y]) => [r6((x - x0) * sx), r6((y - y0) * sy)]));
}

/**
 * Scale about the CENTRE instead, keeping (0.5, 0.5) where it is.
 *
 * ⚠ THIS IS THE FAN-SAFE FIT, and the concave shapes need it. A flower with five
 * petals is not symmetric top-to-bottom, so stretching its BOX to the square
 * slides the centre of the flower off (0.5, 0.5) — and for a shape with deep
 * valleys, a centre that has drifted into a valley wall is exactly the fan that
 * draws wrong. Scaling about the centre costs a little empty box on one axis and
 * buys a guarantee.
 */
function fitCentred(points) {
  const ex = Math.max(...points.map(([x]) => Math.abs(x - 0.5)));
  const ey = Math.max(...points.map(([, y]) => Math.abs(y - 0.5)));
  const sx = ex > 0 ? 0.5 / ex : 1;
  const sy = ey > 0 ? 0.5 / ey : 1;
  return dedupe(points.map(([x, y]) => [r6(0.5 + (x - 0.5) * sx), r6(0.5 + (y - 0.5) * sy)]));
}

/**
 * `steps` points around the centre, at whatever radius `radius(t, i)` returns.
 *
 * `t` runs 0→1 once round and `i` is the step, because a star wants the INDEX
 * (odd steps are the inner points) while a flower wants the angle. Radius is a
 * fraction of half the box, so 1 touches the edge. Phase starts at the TOP, like
 * every polygon anyone draws by hand — and like the pentagon that was here first.
 */
function polar(steps, radius, phase = -Math.PI / 2) {
  const out = [];
  for (let i = 0; i < steps; i++) {
    const t = i / steps;
    const a = phase + t * TAU;
    const r = radius(t, i);
    out.push([0.5 + Math.cos(a) * 0.5 * r, 0.5 + Math.sin(a) * 0.5 * r]);
  }
  return out;
}

/** A regular n-gon. Convex, so the plain box fit is safe. */
const poly = (n, phase = -Math.PI / 2) => fit(polar(n, () => 1, phase));

/** n tips at the edge, n valleys at `inner`. Concave → centred fit. */
const starPoly = (n, inner, phase = -Math.PI / 2) =>
  fitCentred(polar(n * 2, (t, i) => (i % 2 === 0 ? 1 : inner), phase));

/**
 * n petals. `inner` is how far in the valleys cut, `sharp` how pointed the petal
 * is where it meets the next one (below 1 = a rounded petal, above = a spike).
 */
const flower = (n, inner, sharp = 0.55, per = 24) =>
  fitCentred(
    polar(n * per, (t) => inner + (1 - inner) * Math.abs(Math.cos(Math.PI * n * t)) ** sharp)
  );

/** A circle with a bitten edge — n shallow scallops of depth `depth`. */
const scallop = (n, depth = 0.12, per = 16) =>
  fitCentred(polar(n * per, (t) => 1 - depth * (1 - Math.abs(Math.cos(Math.PI * n * t)))));

/**
 * A gear: square teeth rather than points, so it reads as machined.
 *
 * ⚠ ITS TOOTH FLANKS ARE RADIAL, which means the fan triangle on those two edges
 * is DEGENERATE — zero area, drawn as nothing, correct by accident and on
 * purpose. It is the one shape whose fan-safety margin is zero rather than
 * positive, and `shape_points_check.py` allows exactly that much.
 */
function cog(teeth, inner = 0.72, duty = 0.55) {
  const out = [];
  const step = 1 / teeth;
  for (let i = 0; i < teeth; i++) {
    const base = i * step;
    const ring = [
      [base, 1],
      [base + step * duty, 1],
      [base + step * duty, inner],
      [base + step, inner],
    ];
    for (const [t, r] of ring) {
      const a = -Math.PI / 2 + t * TAU;
      out.push([0.5 + Math.cos(a) * 0.5 * r, 0.5 + Math.sin(a) * 0.5 * r]);
    }
  }
  return fitCentred(out);
}

/** A soft irregular pebble. Whole harmonics only, or the loop wouldn't close. */
function blob() {
  return fitCentred(
    polar(96, (t) => {
      const a = t * TAU;
      return (
        1 -
        0.1 * Math.sin(a * 3 + 0.7) -
        0.06 * Math.sin(a * 5 + 2.1) -
        0.03 * Math.sin(a * 2)
      );
    })
  );
}

/** `steps` segments of an ellipse arc, endpoints included. */
function arc(cx, cy, rx, ry, a0, a1, steps) {
  const out = [];
  for (let i = 0; i <= steps; i++) {
    const a = a0 + ((a1 - a0) * i) / steps;
    out.push([cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]);
  }
  return out;
}

/** A quadratic curve, sampled. What gives the heart and the shield their flanks. */
function quad(p0, p1, p2, steps) {
  const out = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const u = 1 - t;
    out.push([
      u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
      u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
    ]);
  }
  return out;
}

/** A box with corners of radius `r` (as a fraction of the box). */
function rounded(r, steps = 8) {
  const k = Math.min(r, 0.5);
  return fit([
    ...arc(k, k, k, k, Math.PI, Math.PI * 1.5, steps),
    ...arc(1 - k, k, k, k, Math.PI * 1.5, TAU, steps),
    ...arc(1 - k, 1 - k, k, k, 0, Math.PI / 2, steps),
    ...arc(k, 1 - k, k, k, Math.PI / 2, Math.PI, steps),
  ]);
}

/** A doorway: semicircular top, straight sides, flat foot. */
const arch = (steps = 16) =>
  fit([...arc(0.5, 0.5, 0.5, 0.5, Math.PI, TAU, steps), [1, 1], [0, 1]]);

/** A dome — the top half of the box's ellipse, flat side down. */
const halfCircle = (steps = 32) => fit([...arc(0.5, 1, 0.5, 1, Math.PI, TAU, steps), [1, 1]]);

/** A quarter round, its corner at bottom-left. */
const quarterCircle = (steps = 24) => fit([[0, 1], ...arc(0, 1, 1, 1, -Math.PI / 2, 0, steps)]);

/**
 * A disc with a wedge taken out of it.
 *
 * ⚠ ITS APEX IS THE FAN'S CENTRE — the one shape whose outline touches (0.5,
 * 0.5) rather than surrounding it. That is still star-shaped (the apex sees
 * everything), which is why a mouth is possible here and a ring is not.
 */
function pac(steps = 40, mouth = 50) {
  const a0 = (mouth / 2) * (Math.PI / 180);
  return fit([[0.5, 0.5], ...arc(0.5, 0.5, 0.5, 0.5, a0, TAU - a0, steps)]);
}

/**
 * A heart: two lobes and two flanks meeting at a point.
 *
 * Built from the lobes' INTERSECTION rather than from a formula, so the notch is
 * a real vertex at a known height instead of wherever a polar curve happened to
 * dip — which is what decides whether the centre can see it.
 */
function heart(steps = 14) {
  const r = 0.32;
  const cy = 0.3;
  const flank = 0.62; // where the lobe hands over to the flank curve
  const rx = 1 - r; // right lobe's centre
  const dx = rx - 0.5;
  const dy = Math.sqrt(Math.max(r * r - dx * dx, 0));
  const notch = Math.atan2(-dy, -dx);
  const foot = [rx + Math.cos(flank) * r, cy + Math.sin(flank) * r];
  return fit([
    ...arc(rx, cy, r, r, notch, flank, steps),
    ...quad(foot, [0.86, 0.8], [0.5, 1], steps),
    ...quad([0.5, 1], [0.14, 0.8], [1 - foot[0], foot[1]], steps),
    ...arc(1 - rx, cy, r, r, Math.PI - flank, TAU + Math.atan2(-dy, dx), steps),
  ]);
}

/** A teardrop: point up, weight at the bottom. */
const drop = (steps = 32) =>
  fit([[0.5, 0], ...arc(0.5, 0.66, 0.5, 0.34, -Math.PI * 0.36, Math.PI * 1.36, steps)]);

/** A leaf — two curves meeting at opposite corners of the box. */
const leaf = (steps = 18) =>
  fit([...quad([0, 0], [1, 0], [1, 1], steps), ...quad([1, 1], [0, 1], [0, 0], steps)]);

/** A shield: flat shoulders, flanks that fall to a point. */
const shield = (steps = 14) =>
  fit([
    [0, 0],
    [1, 0],
    [1, 0.42],
    ...quad([1, 0.42], [0.96, 0.88], [0.5, 1], steps),
    ...quad([0.5, 1], [0.04, 0.88], [0, 0.42], steps),
  ]);

/** A plus. `arm` is how thick the bar is, as a fraction of the box. */
function plus(arm = 0.32) {
  const a = arm;
  const b = 1 - arm;
  return [
    [a, 0], [b, 0], [b, a], [1, a], [1, b], [b, b],
    [b, 1], [a, 1], [a, b], [0, b], [0, a], [a, a],
  ];
}

/** The same plus, turned 45° and re-fitted — an ✕ rather than a ✚. */
function cross(arm = 0.3) {
  const c = Math.cos(Math.PI / 4);
  const s = Math.sin(Math.PI / 4);
  return fit(
    plus(arm).map(([x, y]) => [
      0.5 + (x - 0.5) * c - (y - 0.5) * s,
      0.5 + (x - 0.5) * s + (y - 0.5) * c,
    ])
  );
}

/**
 * An arrow, pointing up.
 *
 * ⚠ THE HEAD REACHES PAST THE MIDDLE ON PURPOSE (`head` > 0.5). With a shallow
 * head the box's centre sits in the STEM, and from there the barb tips are hidden
 * behind the head's underside — not star-shaped, and a monitor that draws the
 * barbs filled in. A head that contains the centre makes the whole outline
 * visible from it.
 */
function arrow(head = 0.58, stem = 0.22) {
  const a = 0.5 - stem;
  const b = 0.5 + stem;
  return [[0.5, 0], [1, head], [b, head], [b, 1], [a, 1], [a, head], [0, head]];
}

/** A speech bubble: rounded box, tail bottom-left. */
function bubble(r = 0.22, steps = 8) {
  const box = 0.78; // the body's foot; the tail lives below it
  return fit([
    ...arc(r, r, r, r, Math.PI, Math.PI * 1.5, steps),
    ...arc(1 - r, r, r, r, Math.PI * 1.5, TAU, steps),
    ...arc(1 - r, box - r, r, r, 0, Math.PI / 2, steps),
    [0.46, box], [0.3, 1], [0.3, box],
    ...arc(r, box - r, r, r, Math.PI / 2, Math.PI, steps),
  ]);
}

const trapezoid = (inset = 0.22) => [[inset, 0], [1 - inset, 0], [1, 1], [0, 1]];
const parallelogram = (slant = 0.24) => [[slant, 0], [1, 0], [1 - slant, 1], [0, 1]];
const kite = () => [[0.5, 0], [1, 0.36], [0.5, 1], [0, 0.36]];

/**
 * WHERE THE FIRST FOUR SHAPES LIVE, and why they are written out rather than
 * built.
 *
 * ⚠ THESE POINTS ARE FROZEN. Every project saved before the library grew stores
 * `kind: "pentagon"` or `"star"`, and a pentagon that changed shape on load would
 * silently redraw somebody's finished animatic. The builders above would produce
 * a slightly different (better-centred) pentagon — so the old one stays hand-
 * written, exactly as it was, and the new shapes are the ones that get the fit.
 */
const LEGACY = {
  rect: [[0, 0], [1, 0], [1, 1], [0, 1]],
  pentagon: [[0.5, 0], [1, 0.38], [0.82, 1], [0.18, 1], [0, 0.38]],
  star: [
    [0.5, 0], [0.61, 0.35], [0.98, 0.35], [0.68, 0.57],
    [0.79, 0.91], [0.5, 0.7], [0.21, 0.91], [0.32, 0.57],
    [0.02, 0.35], [0.39, 0.35],
  ],
};

/**
 * EVERY SHAPE, BY ID.
 *
 * ⚠ `ellipse` IS NOT HERE. It is a border-radius in CSS, a true ellipse to
 * Pillow and a 96-segment fan in the monitor — three exact circles rather than
 * one shared approximation. `shapeOutline` is where that exception lives.
 *
 * ⚠ IDS ARE STORED IN SAVED PROJECTS. Renaming one is a data migration, not a
 * rename: a clip whose kind no longer resolves falls back to a plain box.
 */
export const SHAPE_POINTS = {
  ...LEGACY,

  // Basic
  round_rect: rounded(0.22),
  triangle: poly(3),
  diamond: poly(4),
  half_circle: halfCircle(),
  quarter_circle: quarterCircle(),
  arch: arch(),

  // Polygons
  hexagon: poly(6, 0),
  heptagon: poly(7),
  octagon: poly(8, Math.PI / 8),
  decagon: poly(10),
  trapezoid: trapezoid(),
  parallelogram: parallelogram(),
  kite: kite(),

  // Stars & bursts
  star4: starPoly(4, 0.28),
  star6: starPoly(6, 0.55),
  star8: starPoly(8, 0.6),
  burst12: starPoly(12, 0.62),
  starburst: starPoly(16, 0.4),
  sunburst: starPoly(24, 0.72),
  seal: starPoly(20, 0.86),

  // Flowers & blobs
  flower5: flower(5, 0.3),
  flower6: flower(6, 0.32),
  flower8: flower(8, 0.38),
  clover: flower(4, 0.18, 0.75),
  quatrefoil: flower(4, 0.66, 1),
  blob: blob(),
  scallop: scallop(12, 0.12),
  cog: cog(10),

  // Symbols
  heart: heart(),
  drop: drop(),
  leaf: leaf(),
  shield: shield(),
  plus: plus(),
  cross: cross(),
  arrow: arrow(),
  pac: pac(),
  bubble: bubble(),
};

/**
 * THE FOLDERS THE PICKER SHOWS — and the reason the shapes are grouped at all.
 *
 * Four tiles were a row you took in at a glance. Forty-one are a wall, and a wall
 * is not a bigger library, it is a library you stop reading ("cetegory like
 * folder so user uderstand shapes"). The grouping is by WHAT YOU CAME FOR — a
 * frame, a badge, a marker — rather than by how many sides the polygon has,
 * because "I need something to circle a face with" is the question the pane is
 * actually being asked.
 *
 * ⚠ THE ORDER OF THIS LIST IS THE ORDER ON SCREEN, and `SHAPE_KINDS` is flattened
 * from it — so the Properties picker and the Media tab cannot fall out of step.
 * Basic comes first, and `rect` first inside it, because that is the shape the
 * keyboard-and-hurry path wants.
 */
export const SHAPE_CATEGORIES = [
  {
    id: "shapes:basic",
    label: "Basic",
    note: "The everyday four, plus the round-cornered box and the domes.",
    kinds: [
      { id: "rect", label: "Square" },
      { id: "round_rect", label: "Rounded square" },
      { id: "ellipse", label: "Circle" },
      { id: "triangle", label: "Triangle" },
      { id: "diamond", label: "Diamond" },
      { id: "half_circle", label: "Half circle" },
      { id: "quarter_circle", label: "Quarter circle" },
      { id: "arch", label: "Arch" },
    ],
  },
  {
    id: "shapes:polygon",
    label: "Polygons",
    note: "Straight-edged frames — five sides up to ten, and the slanted boxes.",
    kinds: [
      { id: "pentagon", label: "Pentagon" },
      { id: "hexagon", label: "Hexagon" },
      { id: "heptagon", label: "Heptagon" },
      { id: "octagon", label: "Octagon" },
      { id: "decagon", label: "Decagon" },
      { id: "trapezoid", label: "Trapezoid" },
      { id: "parallelogram", label: "Parallelogram" },
      { id: "kite", label: "Kite" },
    ],
  },
  {
    id: "shapes:star",
    label: "Stars & bursts",
    note: "Points and spikes — for a sting, a price flash or a badge.",
    kinds: [
      { id: "star", label: "Star" },
      { id: "star4", label: "Sparkle" },
      { id: "star6", label: "Six-point star" },
      { id: "star8", label: "Eight-point star" },
      { id: "burst12", label: "Burst" },
      { id: "starburst", label: "Starburst" },
      { id: "sunburst", label: "Sunburst" },
      { id: "seal", label: "Badge" },
    ],
  },
  {
    id: "shapes:flower",
    label: "Flowers & blobs",
    note: "Round and soft — petals, pebbles and a scalloped edge.",
    kinds: [
      { id: "flower5", label: "Flower" },
      { id: "flower6", label: "Six-petal flower" },
      { id: "flower8", label: "Eight-petal flower" },
      { id: "clover", label: "Clover" },
      { id: "quatrefoil", label: "Quatrefoil" },
      { id: "blob", label: "Blob" },
      { id: "scallop", label: "Scalloped circle" },
      { id: "cog", label: "Gear" },
    ],
  },
  {
    id: "shapes:symbol",
    label: "Symbols",
    note: "Shapes that mean something — a marker, a pointer, a caption tail.",
    kinds: [
      { id: "heart", label: "Heart" },
      { id: "drop", label: "Teardrop" },
      { id: "leaf", label: "Leaf" },
      { id: "shield", label: "Shield" },
      { id: "plus", label: "Cross" },
      { id: "cross", label: "X" },
      { id: "arrow", label: "Arrow" },
      { id: "pac", label: "Pac" },
      { id: "bubble", label: "Speech bubble" },
    ],
  },
];

/**
 * The same shapes as one flat list — what a `<select>`, a swatch row or a lookup
 * of "what is this clip called" reads. ⚠ DERIVED, never edited: a shape that is
 * in one list and not the other is a tile you can add and then cannot name.
 */
export const SHAPE_KINDS = SHAPE_CATEGORIES.flatMap((group) =>
  group.kinds.map((kind) => ({ ...kind, group: group.id }))
);

/** The label a saved clip shows in the pane. Falls back to the raw id. */
export function shapeLabel(kind) {
  return SHAPE_KINDS.find((k) => k.id === kind)?.label || kind;
}

// How finely a circle is faked when a real one is not available (the monitor's
// fan). 96 segments is under a third of a pixel of error on a 1080-tall frame.
export const ELLIPSE_SEGMENTS = 96;

/**
 * A shape's outline, INCLUDING the ellipse — which is sampled here rather than
 * living in the table, because CSS and Pillow both draw a true one and only the
 * fan needs points.
 *
 * An unknown kind folds to a plain box in all three renderers, so an old project
 * carrying a shape this build has never heard of draws as something rather than
 * as nothing.
 */
export function shapeOutline(kind) {
  if (kind === "ellipse") {
    const out = [];
    for (let i = 0; i < ELLIPSE_SEGMENTS; i++) {
      const a = (i / ELLIPSE_SEGMENTS) * TAU;
      out.push([0.5 + Math.cos(a) / 2, 0.5 + Math.sin(a) / 2]);
    }
    return out;
  }
  return SHAPE_POINTS[kind] || SHAPE_POINTS.rect;
}
