// dialog_move.js — every dialog in the app can be dragged out of the way.
//
// ⚠ THIS EXISTS BECAUSE THE DIALOGS STOPPED CLOSING ON A BACKDROP CLICK. That
// was reported from a real Premiere import, many steps in: *"galti se mera mouse
// pop up se bahar screen ke click hua mera popup cut gaya … mera mehnat bekar ho
// gaya"*. A dialog holds work that is saved nowhere — a read that took minutes,
// a half-typed name, the folder list the report just printed — and one stray
// click threw all of it away. So no `.modal-overlay` in this app carries an
// `onClick` any more (RULEBOOK **E65**, pinned by `tests/dialog_frame_check.py`).
//
// ⚠ AND A DIALOG THAT CANNOT BE DISMISSED MUST BE MOVEABLE, or it simply sits on
// top of the timeline the user is trying to check it against with no way to look
// behind it. That is this file: ONE implementation for thirty dialogs rather
// than thirty copies of the same twenty lines — and every dialog written after
// today gets it without being told, which is the half a per-dialog hook could
// not do.
//
// ⚠ IT IS INSTALLED ONCE, FROM `App.jsx`, AND IT LISTENS ON `document`. Nothing
// in a dialog's own JSX says "I can be dragged", so this comment and the
// RULEBOOK row are where that fact lives. `soon-upgrade.css` carries the same
// warning beside `.modal-overlay`, which is the block anyone changing a dialog's
// surface actually opens.
//
// ⚠ THE HANDLE IS THE HEADING, NOT THE WHOLE CARD. A card-wide grab would eat
// text selection, and a paragraph in a dialog is often the thing being read out
// to somebody. Headings only (`h1`–`h4`), plus the card's own padding.

// How much of the card must stay on screen, in pixels. ⚠ NOT ZERO: a card
// dragged fully past an edge is unreachable, ✕ and all, and a dialog that no
// longer closes on the backdrop has no other way out.
const EDGE = 72;

// Where the drag started, or null when nothing is being dragged.
let from = null;

// The offset lives on the DOM node (`_dlgX`/`_dlgY`) rather than in a module
// variable. ⚠ THAT IS WHAT MAKES "IT OPENS IN THE MIDDLE AGAIN" FREE: a dialog
// is unmounted when it closes, so the next one is a NEW element with no offset
// on it. A module-level position would come back where it was last shoved,
// which reads as broken.
function offsetOf(card) {
  return { x: Number(card._dlgX) || 0, y: Number(card._dlgY) || 0 };
}

function place(card, x, y) {
  card._dlgX = x;
  card._dlgY = y;
  // ⚠ `transform`, NOT `left`/`top`. The overlay centres its card with
  // `place-items: center`, so the card has no position of its own to change —
  // and a transform costs no layout, which matters on the editor's dialogs,
  // where a re-layout mid-drag would drag the timeline behind it too.
  card.style.transform = x || y ? `translate(${x}px, ${y}px)` : "";
}

// The card is the overlay's own child that the pointer went down inside — found
// by walking up rather than by class, because not every dialog's box is a
// `.card` (`DirectorPanel`, the picture pickers) and this must not care.
function cardFor(overlay, target) {
  let el = target;
  while (el && el.parentElement !== overlay) el = el.parentElement;
  return el;
}

function onPointerDown(e) {
  if (e.button !== 0 || from) return;
  const t = e.target;
  if (!t || typeof t.closest !== "function") return;
  const overlay = t.closest(".modal-overlay");
  if (!overlay) return;
  const card = cardFor(overlay, t);
  if (!card) return;
  // ⚠ ANYTHING THE USER COULD BE OPERATING IS NOT A HANDLE. A heading with a
  // close button in it is the ordinary shape of a title bar, and a drag that
  // swallowed that click would break the only way out of the dialog.
  if (t.closest("button, a, input, select, textarea, label, [contenteditable]")) return;
  const heading = t.closest("h1, h2, h3, h4");
  if (t !== card && !(heading && card.contains(heading))) return;

  const at = offsetOf(card);
  const r = card.getBoundingClientRect();
  from = {
    card,
    id: e.pointerId,
    px: e.clientX,
    py: e.clientY,
    x: at.x,
    y: at.y,
    // Where the card WOULD sit with no offset. The clamp is measured from
    // there, not from wherever it has been dragged to so far.
    left: r.left - at.x,
    top: r.top - at.y,
    w: r.width,
    h: r.height,
  };
  // ⚠ POINTER CAPTURE, not window listeners: the moves keep arriving even when
  // the pointer runs off the card — or off the window — so a fast drag cannot
  // leave the dialog stuck to the cursor.
  try {
    card.setPointerCapture(e.pointerId);
  } catch {
    // Some browsers refuse capture on a detached node; the document listeners
    // below still carry the drag.
  }
  // Otherwise the drag paints the heading blue instead of moving the card.
  e.preventDefault();
}

function onPointerMove(e) {
  if (!from || e.pointerId !== from.id) return;
  const x = Math.min(
    Math.max(from.x + e.clientX - from.px, EDGE - from.left - from.w),
    window.innerWidth - EDGE - from.left,
  );
  const y = Math.min(
    // Never above the top edge: the heading IS the handle, so it has to stay
    // grabbable to drag the card back down.
    Math.max(from.y + e.clientY - from.py, -from.top),
    window.innerHeight - EDGE - from.top,
  );
  place(from.card, x, y);
}

function onPointerUp(e) {
  if (!from || e.pointerId !== from.id) return;
  try {
    from.card.releasePointerCapture(e.pointerId);
  } catch {
    // The pointer can already be gone (a tab switch mid-drag); nothing to do.
  }
  from = null;
}

// ⚠ THE ONLY THING THAT TELLS THE USER, BESIDES THE CURSOR. Helper text in this
// app goes in the element's own `title` (RULEBOOK E4) — and a hint written into
// thirty dialogs by hand is a hint twenty-nine of them would be missing by
// Christmas, so it is stamped on the first hover instead. A heading that already
// carries a `title` keeps its own words.
const HINT = "Drag to move this window";

function onPointerOver(e) {
  const t = e.target;
  if (!t || typeof t.closest !== "function" || !t.closest(".modal-overlay")) return;
  const heading = t.closest("h1, h2, h3, h4");
  if (heading && !heading.title) heading.title = HINT;
}

let installed = false;

export function installDialogMove() {
  if (installed || typeof document === "undefined") return;
  installed = true;
  document.addEventListener("pointerover", onPointerOver, true);
  document.addEventListener("pointerdown", onPointerDown, true);
  document.addEventListener("pointermove", onPointerMove, true);
  document.addEventListener("pointerup", onPointerUp, true);
  document.addEventListener("pointercancel", onPointerUp, true);
}
