// row_heights.js — how tall each timeline row is, and the memory of what you dragged.
//
// One record PER PROJECT: `{ [projectId]: { at, rows: { [laneKey]: rem } } }`. A
// row that has never been dragged is not in its project's `rows` and takes the
// vertical zoom's height (`trackH` in `Timeline.jsx`), which is what every row
// did before this existed.
//
// ⚠ PER PROJECT, WHICH IT WAS NOT AT FIRST — AND THAT WAS A BUG YOU COULD SEE ON
// THE FIRST SCREEN. The record used to be keyed by LANE KEY ALONE, on the theory
// that "I like my Text row tall" is a preference about how you read a timeline
// rather than a property of one edit. It reads well and it is wrong, because a
// lane key is NOT unique to a project: the structural rows are `text:`,
// `shape:`, `image:`, `frames:0`, `audio:` in every project there has ever been,
// and an IMPORTED project's lanes are worse — `interchange.py` names them
// `_import_text_0`, `_import_shape_0`, … deterministically, so every import
// produces the SAME ids as the last one. One accidental drag on a seam (it is a
// 9px strip under every gutter row, right where a row-restack drag starts) was
// therefore enough to make EVERY project opened afterwards — and every fresh
// import — come up with some rows tall and some short, with nothing on screen to
// explain it and no drag of the user's behind it. Reported exactly that way:
// "jab mai editor open karta hun to layer chota bara kyun rahta hai jab user
// layer ka size badla hi nahi… first time mai ek look rahna chahiye".
//
// So: a project nobody has dragged anything in has NO record, and therefore
// every row the same height. The moment a seam is dragged, that row's height is
// remembered — for THAT project, and it survives a reload.
//
// ⚠ IT IS STILL STORED, BECAUSE THE PANES ARE. The seam that resizes a row is the
// same `PaneSplitter` the workspace's three seams use, and it was asked for in
// those words — "like my Four panel move program, media, properties and time".
// Those three sizes survive a reload (`pane_layout.js`); a row height that did
// not would be the same gesture with half the promise.
//
// ⚠ IN REM, NOT PX, WHICH IS THE OPPOSITE OF `pane_layout.js` — on purpose. A
// pane is dragged against the window and stored in the pixels the drag produced;
// a timeline row is one of a stack that all scale together with the browser's
// font size (see the ⚠ note on `.tl-wrap`), and storing px would make a row you
// sized to fit four keyframe rows stop fitting them the moment the text size
// changed.
//
// ⚠ NOT IN THE DOCUMENT, AND THEREFORE NOT IN THE UNDO STACK. A row height is
// how you are looking at the film. In the document it would be an edit: Ctrl+Z
// after a resize would undo the resize instead of the cut you were working on,
// and every drag would mark the project dirty and trigger an autosave.

// ⚠ A NEW KEY, AND THE OLD ONE IS DELETED ON SIGHT. The old record is a flat
// `{ laneKey: rem }` with no project in it, so there is no honest way to decide
// which project its heights belonged to — and leaving it in place would leave
// the bug in place for everyone who already has one. Removing it is the fix
// arriving: the next timeline that opens is uniform, exactly as asked.
const KEY = "cas_animatic_rows2";
const LEGACY_KEY = "cas_animatic_rows";

// How many projects' worth of rows to keep. Beyond this the oldest are dropped:
// a row height is a convenience, and an unbounded record would grow one entry
// per project opened for the life of the browser profile.
const MAX_PROJECTS = 24;

// What a drag may ask for, in rem. The floor still fits a waveform; the ceiling
// still leaves room for another row on a laptop screen. ⚠ THE SAME TWO NUMBERS
// `Timeline.jsx` clamps the live drag with (`MIN_TRACK_H` / `MAX_TRACK_H`) —
// re-checked here because what comes OUT of storage was written by an older
// build, and a record from a version with a taller ceiling must not be able to
// open the timeline in a state this one would refuse to produce.
const MIN_REM = 1.5;
const MAX_REM = 6;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/**
 * One project's rows, clamped, with anything unreadable dropped. Rounded to two
 * places: a row height is a number a person dragged, and 2.5999999999999996 in
 * storage is a number nobody chose.
 */
function cleanRows(rows) {
  const out = {};
  if (!rows || typeof rows !== "object") return out;
  for (const [key, value] of Object.entries(rows)) {
    if (typeof key !== "string" || !key) continue;
    if (!isNum(value)) continue;
    out[key] = Math.round(Math.min(MAX_REM, Math.max(MIN_REM, value)) * 100) / 100;
  }
  return out;
}

/** Every project's record, cleaned. `{}` if storage is unreadable or disabled. */
function readAll() {
  try {
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    // Storage disabled — nothing to clear and nothing to read either.
  }
  let raw;
  try {
    raw = JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    // Unreadable or storage disabled — every row takes the vertical zoom's
    // height, which is a working timeline.
    return {};
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out = {};
  for (const [id, entry] of Object.entries(raw)) {
    if (typeof id !== "string" || !id) continue;
    if (!entry || typeof entry !== "object") continue;
    const rows = cleanRows(entry.rows);
    // A project with nothing left after cleaning is a project with no record.
    if (!Object.keys(rows).length) continue;
    out[id] = { at: isNum(entry.at) ? entry.at : 0, rows };
  }
  return out;
}

/**
 * The stored heights for ONE project — `{ [laneKey]: rem }`, empty for a project
 * nobody has dragged a row in, which is every project the first time it opens.
 */
export function getRowHeights(projectId) {
  if (!projectId || typeof projectId !== "string") return {};
  const entry = readAll()[projectId];
  return entry ? entry.rows : {};
}

/**
 * Write one project's rows, leaving every other project's alone.
 *
 * ⚠ AN EMPTY RECORD DELETES THE PROJECT'S ENTRY rather than storing `{}`. Reset
 * every row you dragged (double-click each seam) and the project is back to
 * having never been touched — which is what the user sees, so it should be what
 * storage says too.
 */
export function saveRowHeights(projectId, heights) {
  if (!projectId || typeof projectId !== "string") return;
  try {
    const all = readAll();
    const rows = cleanRows(heights);
    if (Object.keys(rows).length) all[projectId] = { at: Date.now(), rows };
    else delete all[projectId];
    // Newest first, then trimmed — so the projects you are actually working in
    // are the ones that survive the cap.
    const kept = Object.entries(all)
      .sort((a, b) => (b[1].at || 0) - (a[1].at || 0))
      .slice(0, MAX_PROJECTS);
    localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(kept)));
  } catch {
    // Not worth a message: the rows are still the height you dragged them to.
  }
}
