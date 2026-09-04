// panel_box.js — WHERE THE ✨ AI Editor PANEL SITS AND HOW BIG IT IS.
//
// ---------------------------------------------------------------------------
// ⚠ THE MATHS IS HERE, THE POINTER HANDLING IS IN THE PANEL. On purpose.
// ---------------------------------------------------------------------------
// A drag is four numbers and a clamp. Left inside the component those four
// numbers get re-derived in the move handler, in the resize handler and again on
// window resize — three copies of one rule, which is how a window ends up
// half off the bottom of the screen after somebody rotates a tablet.
//
// ---------------------------------------------------------------------------
// ⚠ THE PANEL IS ALWAYS FULLY ON SCREEN. THAT IS THE WHOLE POINT OF `clampBox`.
// ---------------------------------------------------------------------------
// The obvious version keeps "enough of it visible" — a title bar's worth — and
// lets the rest hang off the edge. That is what a desktop window manager does,
// and it is wrong here: this window has no taskbar to get it back from. Once its
// Send button is past the right edge there is no way to reach the button that
// would move it. So it is clamped whole, and a viewport that shrinks below the
// window pulls the window in with it.
//
// ⚠ AND EVERY READ IS WRAPPED. `localStorage` throws outright in a locked-down
// browser, and `window` does not exist at all in `editor_chat_render_check.py`,
// which renders this panel with `react-dom/server`. Both must give the defaults
// back rather than take the panel down with them.

/** Small enough to be out of the way, big enough to read a plan in. */
export const MIN_W = 300;
export const MIN_H = 260;
export const DEFAULT_W = 380;
export const DEFAULT_H = 560;

/** The docked column, in px. The CSS default is `clamp(320px, 26vw, 420px)`. */
export const MIN_DOCK_W = 300;
export const MAX_DOCK_W = 760;

/** ⚠ THE SAME BREAKPOINT `editor-chat.css` USES. Under it both docks become one
 *  full-width sheet, and a floating window has nothing to float over — so the
 *  panel stops applying any of this and lets the stylesheet win. */
export const NARROW_W = 820;

const BOX_KEY = "aniwala.editorChatBox.v1";
const WIDTH_KEY = "aniwala.editorChatWidth.v1";

/** The browser window, or a sane pretend one when there isn't a browser. */
export function viewport() {
  try {
    return {
      w: window.innerWidth || 1280,
      h: window.innerHeight || 800,
    };
  } catch {
    // No DOM at all — server render, or a test harness. See the header.
    return { w: 1280, h: 800 };
  }
}

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

/** Where a window opens when nobody has moved one yet: top right, off the edge. */
export function defaultBox(vp = viewport()) {
  const w = Math.min(DEFAULT_W, Math.max(MIN_W, vp.w - 32));
  const h = Math.min(DEFAULT_H, Math.max(MIN_H, vp.h - 32));
  return { x: Math.max(16, vp.w - w - 24), y: 72, w, h };
}

/**
 * A box made legal for this viewport. ⚠ SIZE FIRST, THEN POSITION — a width
 * clamped after the x it was used to compute leaves a gap on the right that
 * grows every time the window is resized.
 */
export function clampBox(box, vp = viewport()) {
  const src = box || {};
  const w = clamp(Number(src.w) || DEFAULT_W, MIN_W, Math.max(MIN_W, vp.w - 16));
  const h = clamp(Number(src.h) || DEFAULT_H, MIN_H, Math.max(MIN_H, vp.h - 16));
  const x = clamp(Number(src.x) || 0, 8, Math.max(8, vp.w - w - 8));
  const y = clamp(Number(src.y) || 0, 8, Math.max(8, vp.h - h - 8));
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) };
}

/** The docked column's width, made legal. Never wider than most of the screen. */
export function clampWidth(width, vp = viewport()) {
  const max = Math.min(MAX_DOCK_W, Math.max(MIN_DOCK_W, vp.w - 160));
  return Math.round(clamp(Number(width) || MIN_DOCK_W, MIN_DOCK_W, max));
}

export function readBox() {
  try {
    const raw = JSON.parse(localStorage.getItem(BOX_KEY) || "null");
    if (raw && typeof raw === "object") return clampBox(raw);
  } catch {
    // Blocked, absent or corrupt. A remembered position is a nicety.
  }
  return defaultBox();
}

export function writeBox(box) {
  try {
    localStorage.setItem(BOX_KEY, JSON.stringify(box));
  } catch {
    // The move still applies for this page load.
  }
}

/** `null` means "never resized" — the panel keeps the width the CSS gives it. */
export function readWidth() {
  try {
    const raw = localStorage.getItem(WIDTH_KEY);
    if (raw === null || raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? clampWidth(n) : null;
  } catch {
    return null;
  }
}

export function writeWidth(width) {
  try {
    if (width === null) localStorage.removeItem(WIDTH_KEY);
    else localStorage.setItem(WIDTH_KEY, String(width));
  } catch {
    // As above.
  }
}

export function forgetBox() {
  try {
    localStorage.removeItem(BOX_KEY);
  } catch {
    /* nothing to forget */
  }
}
