// selection.js — MORE THAN ONE THING CAN BE SELECTED, and what follows from it.
//
// ⚠ EDITOR-SIDE ONLY. `animatic.py` has no counterpart and needs none: a
// selection is not part of the project, it is what you are pointing at right
// now. Nothing here is saved except `group_id`, which is — see below. The same
// split as `keyframes.js` and `audio_clips.js`.
//
// WHY THIS FILE EXISTS. The editor used to hold six "the selected X" ids and
// exactly one of them could be set, so every operation was one clip at a time:
// deleting forty auto-captions meant forty clicks and forty presses of Delete.
// A selection is now a LIST, and one list rather than six ids is what makes
// "delete these", "move these" and "group these" one implementation instead of
// five that drift.
//
// AN ITEM IS `{ kind, id }`, and `kind` is one of `KINDS` below. Two things make
// that the right shape:
//
//   · a selection SPANS LANES — a marquee across the timeline picks up pictures,
//     captions and a piece of audio together, and they live in different lists;
//   · an id is only unique WITHIN its list, so an id on its own cannot say what
//     it is. `selKey` flattens the pair into one string for Set lookups, which
//     is what the timeline draws from.
//
// GROUPS ARE THE ONE PART THAT IS SAVED. `group_id` is an ordinary field on a
// text clip, a shape, an overlay or an audio clip (see `server/schemas.py`), and
// its whole meaning is "select one of us and you have selected all of us". It is
// deliberately NOT a container object holding its members: a container has to be
// kept in step with every delete, split and undo in the app, while a shared
// string on the clips themselves cannot go stale — delete a member and the group
// is simply the members that are left.

/** The kinds of thing that can be selected, and the list each one lives in. */
export const KINDS = ["frame", "text", "shape", "overlay", "audio"];

/**
 * Which kinds can be MOVED along the timeline as a selection.
 *
 * ⚠ Not `frame`. The picture sequence is a flow — a frame has no `start_ms`, it
 * starts where the one before it ended — so "move this picture 2 seconds later"
 * is not an edit that exists here; you re-time the hold or reorder the strip. A
 * marquee may still select pictures (to delete them, or to see what is in
 * range); a drag simply leaves them where they are.
 */
export const MOVABLE = ["text", "shape", "overlay", "audio"];

/** The kinds that can carry a `group_id`. Same list, and for the same reason. */
export const GROUPABLE = MOVABLE;

/** One item flattened to a string, for Sets and React keys. */
export function selKey(kind, id) {
  return `${kind}:${id}`;
}

/** `"text:abc"` back into `{ kind, id }`. An id may contain a colon; the kind
 *  may not, so only the FIRST separator counts. */
export function parseKey(key) {
  const at = String(key || "").indexOf(":");
  if (at < 0) return null;
  return { kind: key.slice(0, at), id: key.slice(at + 1) };
}

/** Every item as a Set of keys — what the timeline asks "is this one selected". */
export function keySet(selection) {
  return new Set((selection || []).map((item) => selKey(item.kind, item.id)));
}

export function hasItem(selection, kind, id) {
  const key = selKey(kind, id);
  return (selection || []).some((item) => selKey(item.kind, item.id) === key);
}

/**
 * Add these items, or take them out if they are all already in.
 *
 * Shift-clicking a selected clip must REMOVE it — that is the whole point of a
 * toggle — but shift-clicking one member of a group whose other members are
 * selected must not: the answer for a set is "in only if every one of them is
 * in", which makes a group toggle as one thing, like it behaves everywhere else.
 */
export function toggleItems(selection, items) {
  const list = selection || [];
  const adding = (items || []).filter(Boolean);
  if (!adding.length) return list;
  const present = adding.every((item) => hasItem(list, item.kind, item.id));
  if (present) {
    const drop = keySet(adding);
    return list.filter((item) => !drop.has(selKey(item.kind, item.id)));
  }
  const have = keySet(list);
  return [...list, ...adding.filter((item) => !have.has(selKey(item.kind, item.id)))];
}

/** The same items with any duplicate removed, first occurrence winning. */
export function uniqueItems(items) {
  const seen = new Set();
  const out = [];
  for (const item of items || []) {
    if (!item) continue;
    const key = selKey(item.kind, item.id);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

/**
 * The group id on a clip, or "" for one that isn't in a group.
 *
 * Anything falsy is "not grouped": a project saved before groups existed has no
 * field at all, and it must read as ungrouped rather than as one enormous group
 * of everything with `undefined` on it.
 */
export function groupOf(clip) {
  return String(clip?.group_id || "");
}

/**
 * One item expanded to every item grouped WITH it.
 *
 * `pools` is `{ text, shape, overlay, audio }`, each a list of clips carrying
 * `id` and `group_id`. ⚠ The audio pool must be keyed by CLIP id (`clipId`),
 * not by upload — after a cut one file is several clips and only the ones
 * actually in the group belong in the selection.
 *
 * A group can span kinds on purpose: a caption and the shape behind it are
 * exactly the pair you want moving together.
 */
export function expandGroup(item, pools) {
  if (!item) return [];
  const clip = (pools?.[item.kind] || []).find((c) => c.id === item.id);
  const group = groupOf(clip);
  if (!group) return [item];
  const out = [];
  for (const kind of GROUPABLE) {
    for (const other of pools?.[kind] || []) {
      if (groupOf(other) === group) out.push({ kind, id: other.id });
    }
  }
  // The clicked item first: it is the one the Properties pane describes, and a
  // group whose members are listed in list order would otherwise show whichever
  // clip happens to be first in `texts`.
  return uniqueItems([item, ...out]);
}

/** Every item in the selection expanded to its whole group. */
export function expandSelection(selection, pools) {
  return uniqueItems(
    (selection || []).flatMap((item) => expandGroup(item, pools))
  );
}

/** How many of each kind — what the Selection pane reports. */
export function countByKind(selection) {
  const out = {};
  for (const item of selection || []) out[item.kind] = (out[item.kind] || 0) + 1;
  return out;
}

// What each kind is called when there are one of it, and when there are several.
const NAMES = {
  frame: ["picture", "pictures"],
  text: ["text clip", "text clips"],
  shape: ["shape", "shapes"],
  overlay: ["picture layer", "picture layers"],
  audio: ["audio clip", "audio clips"],
};

/** "3 text clips and 1 picture" — the selection in words, for a pane or a notice. */
export function selectionLabel(selection) {
  const counts = countByKind(selection);
  const parts = KINDS.filter((kind) => counts[kind]).map((kind) => {
    const n = counts[kind];
    return `${n} ${NAMES[kind][n === 1 ? 0 : 1]}`;
  });
  if (!parts.length) return "nothing";
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/**
 * Do two boxes touch? `{ left, top, right, bottom }`, in any one coordinate
 * space as long as it is the same one.
 *
 * TOUCHING, not containing, and that is the convention every editor uses: a
 * marquee has to catch a clip whose two ends are off screen, and requiring the
 * whole clip inside the rubber band would make a long one impossible to pick up
 * without zooming out first.
 */
export function boxesOverlap(a, b) {
  return !(
    a.right <= b.left ||
    a.left >= b.right ||
    a.bottom <= b.top ||
    a.top >= b.bottom
  );
}

/** Two corners into a normalised box, whichever way the drag went. */
export function boxFromCorners(x1, y1, x2, y2) {
  return {
    left: Math.min(x1, x2),
    top: Math.min(y1, y2),
    right: Math.max(x1, x2),
    bottom: Math.max(y1, y2),
  };
}

/** How far a drag has travelled — the slop that tells a marquee from a click. */
export function dragged(x1, y1, x2, y2, slop = 4) {
  return Math.abs(x2 - x1) > slop || Math.abs(y2 - y1) > slop;
}
