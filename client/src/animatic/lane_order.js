// lane_order.js — WHAT ORDER THE TIMELINE'S ROWS ARE IN, and therefore what
// draws over what.
//
// The report this was written for, twice:
//
//     "i want move layer up - down in timline only those layer: Text, shapes,
//      Image, Video, Story..images, and Story..video   audio and Caption not
//      move okay"
//     "i check shapes layer move only other shapes layer, text layer only move
//      other texts layer … i want these all layer move up down each other …
//      because i want video layer move up Image and shapes and shapes down
//      video lie this all move"
//
// ⚠ ONE Z-SCALE FOR THE WHOLE VISUAL STACK. There used to be three separate
// answers to "what draws over what": a picture clip's `track` NUMBER ordered the
// picture rows, and then the renderers drew the four kinds in a fixed sequence —
// pictures, then shapes, then overlay pictures, then text — which no gesture
// could reach. So a row could be moved among its own kind and nowhere else.
// A row now has a RANK, every visual row is on the same scale, and the sequence
// the renderers draw in comes out of that scale rather than being written into
// them.
//
// ⚠ THE FALLBACK *IS* THE OLD ORDER, AND THAT IS THE WHOLE MIGRATION. With no
// saved order at all — every animatic in existence before today — `laneRank`
// returns the numbers below, which sort into pictures (by track) → shapes →
// overlays → text: exactly the sequence the three renderers had hard-coded. So
// nothing about an existing project resolves, previews or exports differently,
// and there is no migration pass to write. `tests/lane_reorder_check.py` asserts
// it rather than trusting it.
//
// ⚠ A PICTURE ROW'S `track` IS NOT ITS Z ANY MORE — it is which ROW a clip is
// on, and nothing else. This is the one thing to hold on to when reading the rest
// of the editor: an earlier build restacked picture rows by RENUMBERING them
// (swapping `track` on every clip), which cannot express "the Video row above the
// Images row" at all, because Images is not a picture track. Dragging a row now
// writes ONE list — `settings.lane_order` — and no clip is touched.
//
// ⚠ WHAT IS DELIBERATELY NOT HERE: the POLICY about locks and notices, and the
// LANE LIST itself. Both need the document, so both stay in AnimaticEditor.jsx.
// This file answers "what is this row's rank?" and "what is the order after this
// drag?" and cannot say no to anything.
//
// ⚠ TWIN of the rank half of `animatic_render.py` (`default_lane_rank`,
// `lane_rank`). The export reads the same order the monitor does or the preview
// lies about the film — pinned name for name in `tests/lane_reorder_check.py`.

import { CAPTION_LAYER_ID } from "./captions.js";

/**
 * WHICH KINDS OF ROW CAN BE RESTACKED — the user's own list.
 *
 * ⚠ THEY ALL MOVE AGAINST EACH OTHER NOW, not within their own kind. That was
 * the correction: "i check shapes layer move only other shapes layer … i want
 * these all layer move up down each other". A text row can sit under a picture
 * row, a picture row over an overlay row, a shape row anywhere.
 *
 * ⚠ AUDIO IS ABSENT BECAUSE THERE IS NOTHING FOR IT TO MEAN. Audio rows are
 * MIXED, not stacked: two tracks at the same moment are added together, so no
 * order of them produces a different film. A drag that changed nothing but a
 * label's place on screen would be a control that lies about being an edit.
 */
export const MOVABLE_LANE_KINDS = ["text", "shape", "image", "frames"];

/**
 * THE FALLBACK SCALE — where a row sits when the saved order has never heard of
 * it. Higher draws later, i.e. on top.
 *
 * ⚠ THESE FOUR NUMBERS ARE THE OLD HARD-CODED ORDER, WRITTEN DOWN AS DATA. A
 * picture row's rank is its track number (0…15, so the cap matters: a picture row
 * must never out-rank a shape row), then shapes at 100, overlay pictures at 200,
 * text at 300. Change one and you have changed what every animatic that predates
 * `lane_order` looks like.
 */
export const PICTURE_RANK_CAP = 15; // MAX_PICTURE_TRACK — a track's rank IS its number
const FALLBACK_RANK = { shape: 100, image: 200, text: 300 };

/**
 * THE ONE NAME A ROW GOES BY — `"<kind>:<layer id>"`, or `"frames:<n>"` for
 * picture track n. "" for a row that has no stable name (audio: a loose row is
 * keyed by the FILE it holds, which changes when a clip is dragged into it).
 *
 * ⚠ THE SAME VOCABULARY `hidden_lanes` AND `locked_lanes` ALREADY SPEAK, and
 * deliberately so: three lists that name rows should name them the same way, and
 * the server can rebuild any of these tokens from a clip's own fields
 * (`_lane_hidden` in server/animatics.py). ⚠ IT IS ALSO WHY THE ORDER SURVIVES
 * everything except deleting the row — emptying it, renaming it, hiding it and
 * locking it all leave the token alone.
 */
export function laneTokenFor(kind, layerId = "", track = null) {
  if (kind === "audio") return "";
  if (kind === "frames") return `frames:${Math.trunc(Number(track) || 0)}`;
  return `${kind}:${layerId || ""}`;
}

/** The token for a CLIP, by which scene list it came out of. */
export function clipLaneToken(kind, clip) {
  if (kind === "picture" || kind === "frame" || kind === "frames") {
    return `frames:${Math.trunc(Number(clip?.track) || 0)}`;
  }
  // ⚠ "overlay" IS THE SCENE'S WORD AND "image" IS THE ROW'S. One is the clip
  // kind the renderers branch on, the other is the lane kind the gutter and both
  // token lists use. Folding them here is what stops a second spelling of the
  // same row appearing in `lane_order`.
  const lane = kind === "overlay" ? "image" : kind;
  return `${lane}:${clip?.layer_id || ""}`;
}

/** Where a row sits when the saved order does not mention it. */
export function defaultLaneRank(token) {
  if (typeof token !== "string" || !token) return FALLBACK_RANK.text;
  if (token.startsWith("frames:")) {
    const n = Math.trunc(Number(token.slice("frames:".length)));
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(PICTURE_RANK_CAP, n));
  }
  const kind = token.slice(0, token.indexOf(":"));
  // ⚠ AN UNKNOWN KIND RANKS AS TEXT, i.e. on top — the same rule every other
  // unknown in this codebase follows (an unrecognised transition, effect or shape
  // is folded to something drawable rather than dropped). A row a newer build
  // invented draws over the film instead of vanishing under it, which is the
  // failure you can see and therefore fix.
  return FALLBACK_RANK[kind] ?? FALLBACK_RANK.text;
}

/**
 * A ROW'S RANK — higher draws later, i.e. over. THE function; everything about
 * the stack is downstream of it.
 *
 * `order` is `settings.lane_order`: the movable visual rows, TOP OF THE STACK
 * FIRST. A row it names gets a rank from its place in it; a row it does not name
 * gets `order.length + defaultLaneRank(token)`, which puts it ABOVE everything
 * the list ranks.
 *
 * ⚠ UNLISTED MEANS ON TOP, and it has to be one side or the other. A row added
 * AFTER a restack is not in the saved list, and the only two rules that are
 * simple enough to be identical in three renderers are "above everything listed"
 * and "below everything listed". Below would hide a new row behind the pictures
 * — a row you added, cannot see, and have no reason to suspect is under the film.
 * Above is visible, and one drag puts it where it belongs (a drag rewrites the
 * WHOLE list, so it stops being unlisted the first time anything moves).
 *
 * ⚠ AND IT IS WHY THE CAPTIONS ROW NEEDS NO SPECIAL CASE ANYWHERE. Captions are
 * never written into `lane_order` (they cannot be dragged — "Caption not move
 * okay"), so they are always unlisted, so they are always on top: which is where
 * the gutter pins them and where every NLE puts a subtitle track. With no saved
 * order they rank 300 alongside the other text rows and sign byte-for-byte what
 * they always did.
 */
export function laneRank(token, order) {
  const list = order || [];
  const i = list.indexOf(token);
  if (i >= 0) return list.length - 1 - i;
  return list.length + defaultLaneRank(token);
}

/** Can this row be dragged up or down at all? */
export function laneMovable(lane) {
  if (!lane) return false;
  if (lane.layerId === CAPTION_LAYER_ID || lane.key === CAPTION_LAYER_ID) return false;
  return MOVABLE_LANE_KINDS.includes(lane.kind);
}

/**
 * MOVE ONE ITEM OF A LIST TO ANOTHER INDEX — remove, then insert.
 *
 * ⚠ INSERT-AT-THE-TARGET, NOT SWAP-WITH-THE-TARGET, and the difference shows the
 * moment a row is dragged more than one place: a swap would leave the rows
 * BETWEEN the two where they were and drop the target on the far side of them,
 * which is not what a line drawn between two rows promised. Removing first is
 * also what makes the two directions symmetrical — drag row 2 onto row 4 and it
 * lands last of the three, drag row 4 onto row 2 and it lands first.
 *
 * Out-of-range or no-op drags return a COPY, so a caller can never accidentally
 * mutate the list it was handed.
 */
export function moveInList(list, from, to) {
  const out = [...(list || [])];
  if (from === to) return out;
  if (!Number.isInteger(from) || !Number.isInteger(to)) return out;
  if (from < 0 || to < 0 || from >= out.length || to >= out.length) return out;
  const [item] = out.splice(from, 1);
  out.splice(to, 0, item);
  return out;
}

/**
 * THE SAVED ORDER AFTER ONE DRAG — the complete stack, top first.
 *
 * `stackTopFirst` is every movable visual row as the gutter currently shows it
 * (the editor builds it by ranking, so it already accounts for the saved order
 * AND for rows the saved order has never heard of). `fromToken` is the row picked
 * up, `toToken` the row whose place it was dropped on.
 *
 * ⚠ IT RETURNS THE WHOLE LIST, NOT A PATCH. Writing only the two rows that moved
 * would leave every other row unlisted and therefore ON TOP of them — the drag
 * would appear to move everything else instead. Rewriting the lot also means a
 * project's stored order is complete the first time anything is dragged, so the
 * unlisted rule above stays the transient case it is meant to be.
 */
export function restack(stackTopFirst, fromToken, toToken) {
  const stack = [...(stackTopFirst || [])];
  return moveInList(stack, stack.indexOf(fromToken), stack.indexOf(toToken));
}

/** The lane KIND a token names — "frames" for any picture row. */
function kindOfToken(token) {
  if (typeof token !== "string" || !token) return "";
  if (token.startsWith("frames:")) return "frames";
  const at = token.indexOf(":");
  return at < 0 ? "" : token.slice(0, at);
}

/**
 * GIVE A BRAND-NEW ROW ITS PLACE IN THE SAVED ORDER.
 *
 * ⚠ WITHOUT THIS A ROW ADDED AFTER A RESTACK APPEARS AT THE VERY TOP OF THE
 * STACK — over the captions, over everything. That is `laneRank`'s unlisted rule
 * doing exactly what it says, and as a FALLBACK it is the right one (a new row
 * you cannot see is worse than one in the wrong place). As the behaviour of the
 * ＋ Add layer button it is simply wrong: add a picture row and it lands on top
 * of the film.
 *
 * ⚠ IT SEATS THE ROW WITH ITS OWN KIND, which is where the derived order always
 * put it: a new Text row appears with the Text rows, a new picture row with the
 * picture rows. Among its own kind it goes by derived rank, so picture track 2
 * lands between 3 and 1 rather than at either end; the overlay kinds all tie on
 * derived rank, so a new one goes BELOW the existing rows of its kind, which is
 * where "Text 2" has always been drawn relative to "Text".
 *
 * ⚠ AND IT DOES NOTHING AT ALL TO AN EMPTY ORDER. An empty `lane_order` means
 * "the order this editor has always produced" and every row is placed by the
 * fallback — writing one token into it would promote that row and demote every
 * other, which is the bug this function exists to prevent, inverted.
 */
export function seatLane(order, token) {
  const list = [...(order || [])];
  if (!list.length || !token || list.includes(token)) return list;
  const rank = defaultLaneRank(token);
  const kin = list.filter((t) => kindOfToken(t) === kindOfToken(token));
  if (kin.length) {
    // The first row of this kind that belongs BELOW the new one — for the overlay
    // kinds there is none (they all tie), so it goes under the last of them.
    const below = kin.find((t) => defaultLaneRank(t) < rank);
    const at = below ? list.indexOf(below) : list.indexOf(kin[kin.length - 1]) + 1;
    list.splice(at, 0, token);
    return list;
  }
  // No row of this kind is listed: fall back to the derived scale against the
  // kinds that ARE there, so a first picture row still lands under the shapes.
  const at = list.findIndex((t) => defaultLaneRank(t) < rank);
  if (at < 0) list.push(token);
  else list.splice(at, 0, token);
  return list;
}

/**
 * TAKE A DELETED ROW OUT OF THE SAVED ORDER.
 *
 * ⚠ IT IS NOT HOUSEKEEPING. A dead token left in the list is harmless for the
 * rows that remain — ranks are only ever compared with each other — right up
 * until a row is created with the SAME token, which for a picture row means any
 * reused track number. That row would silently inherit the deleted row's place
 * in the stack, months later, with nothing on screen to explain it.
 */
export function unseatLane(order, token) {
  const list = order || [];
  if (!token || !list.includes(token)) return list;
  return list.filter((t) => t !== token);
}

/**
 * SORT ANYTHING THAT CARRIES A LANE TOKEN INTO STACK ORDER — bottom first, which
 * is the order every renderer draws in.
 *
 * ⚠ STABLE, AND EXPLICITLY SO. Rows that tie — which is every row of one kind
 * when there is no saved order — must come out in the order they went in, because
 * that is what reproduces the old behaviour exactly: two captions in one zone
 * stack in the order the `texts` ARRAY has them, and a sort that reshuffled ties
 * would restack them. The index is carried and compared by hand rather than
 * trusting the engine, and because `Infinity - Infinity` is NaN — a NaN
 * comparator does not tie, it puts the sort into undefined behaviour.
 */
export function sortByRank(items, order, tokenOf) {
  return (items || [])
    .map((item, i) => ({ item, i, r: laneRank(tokenOf(item), order) }))
    .sort((a, b) => (a.r === b.r ? a.i - b.i : a.r - b.r))
    .map((d) => d.item);
}

/**
 * IS THIS PROJECT RESTACKED AT ALL? — one string, for the render cache.
 *
 * ⚠ IT HAS TO REACH `sceneSignature`, and this is the cheapest honest way. That
 * signature is the export's render-cache key and it is compared ACROSS exports
 * ("a project whose only edit was the direction would hit the cache from the
 * previous export"), so a restack that did not change it would come back as the
 * previous export's stills in the previous order. Empty for a project with no
 * saved order — every animatic that predates this — so those sign byte-for-byte
 * what they always signed.
 */
export function stackKey(order) {
  const list = order || [];
  return list.length ? list.join("|") : "";
}
