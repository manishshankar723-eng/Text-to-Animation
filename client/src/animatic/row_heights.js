// row_heights.js — how tall each timeline row is, and the memory of what you dragged.
//
// One record: `{ [laneKey]: rem }`. A row that has never been dragged is not in
// it and takes the vertical zoom's height (`trackH` in `Timeline.jsx`), which is
// what every row did before this existed.
//
// ⚠ IT IS STORED BECAUSE THE PANES ARE. The seam that resizes a row is the same
// `PaneSplitter` the workspace's three seams use, and it was asked for in those
// words — "like my Four panel move program, media, properties and time". Those
// three sizes survive a reload (`pane_layout.js`); a row height that did not
// would be the same gesture with half the promise, and the difference would show
// up on the first refresh.
//
// ⚠ IN REM, NOT PX, WHICH IS THE OPPOSITE OF `pane_layout.js` — on purpose. A
// pane is dragged against the window and stored in the pixels the drag produced;
// a timeline row is one of a stack that all scale together with the browser's
// font size (see the ⚠ note on `.tl-wrap`), and storing px would make a row you
// sized to fit four keyframe rows stop fitting them the moment the text size
// changed.
//
// ⚠ AND IT IS NOT PER PROJECT. Keyed by LANE KEY alone, so "I like my Text row
// tall" follows you from film to film — which is what it is: a preference about
// how you read a timeline, not a property of one edit. The structural rows
// ("text:", "frames:0", "shape:") have the same key in every project, which is
// what makes that work; a row keyed by a LAYER id is keyed by a random string, so
// the worst a stale record can do is describe a row that no longer exists, and
// nothing reads it.
//
// ⚠ NOT IN THE DOCUMENT, AND THEREFORE NOT IN THE UNDO STACK. A row height is
// how you are looking at the film. In the document it would be an edit: Ctrl+Z
// after a resize would undo the resize instead of the cut you were working on,
// and every drag would mark the project dirty and trigger an autosave.

const KEY = "cas_animatic_rows";

// What a drag may ask for, in rem. The floor still fits a waveform; the ceiling
// still leaves room for another row on a laptop screen. ⚠ THE SAME TWO NUMBERS
// `Timeline.jsx` clamps the live drag with (`MIN_TRACK_H` / `MAX_TRACK_H`) —
// re-checked here because what comes OUT of storage was written by an older
// build, and a record from a version with a taller ceiling must not be able to
// open the timeline in a state this one would refuse to produce.
const MIN_REM = 1.5;
const MAX_REM = 6;

const isHeight = (v) => typeof v === "number" && Number.isFinite(v);

/** Every stored height, clamped, with anything unreadable dropped. */
export function getRowHeights() {
  let raw;
  try {
    raw = JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    // Unreadable or storage disabled — every row takes the vertical zoom's
    // height, which is a working timeline.
    return {};
  }
  if (!raw || typeof raw !== "object") return {};
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    if (typeof key !== "string" || !key) continue;
    if (!isHeight(value)) continue;
    out[key] = Math.min(MAX_REM, Math.max(MIN_REM, value));
  }
  return out;
}

/**
 * Write the whole record. Rounded to two places: a row height is a number a
 * person dragged, and 2.5999999999999996 in storage is a number nobody chose.
 */
export function saveRowHeights(heights) {
  try {
    const out = {};
    for (const [key, value] of Object.entries(heights || {})) {
      if (!isHeight(value)) continue;
      out[key] = Math.round(Math.min(MAX_REM, Math.max(MIN_REM, value)) * 100) / 100;
    }
    localStorage.setItem(KEY, JSON.stringify(out));
  } catch {
    // Not worth a message: the rows are still the height you dragged them to.
  }
}
