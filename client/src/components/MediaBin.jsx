// MediaBin.jsx — the MEDIA LIBRARY's cards. What you have, not where it plays.
//
// ⚠ IT IS `FrameStrip`'S MARKUP AND CSS, DELIBERATELY, DOWN TO THE CLASS NAMES.
// The two lists sit in the same pane and must be the same object to the eye —
// same tile, same thumbnail, same ✕, same icon/list toggle. What differs is
// entirely semantic, and that is why this is a second component rather than a
// flag on the first:
//
//   · a card here is a SOURCE, not a clip, so there is no place in the sequence
//     to reorder and no hold to type. `FrameStrip` is built around both — `seq`,
//     `indexOf`, `onReorder`, `DurationInput` — and every one of them would have
//     to be switched off, which is a component doing two jobs badly.
//   · dragging one out COPIES: it makes a new clip on the row you drop it on. A
//     `FrameStrip` drag MOVES the clip that is already there. Same gesture, two
//     meanings, so they carry two payloads (`kind: "asset"` vs `kind: "frame"`)
//     and `dropAsset` tells them apart by that and nothing else.
//   · the length shown is the SOURCE's natural length and is read-only. It is
//     printed, not typed — trimming belongs to a clip.
//   · a card can be RENAMED and a clip cannot. The name belongs to the source —
//     see `onRename` and the right-click menu at the bottom of this file.
//
// See `animatic/assets.js` for what an asset is and why the library exists.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { assetOrigin } from "../animatic/assets.js";
import { isVeoRender } from "../animatic/scene.js";

/** A source's natural length, or "" when the server could not measure it. */
function naturalLength(asset) {
  const ms = Number(asset?.duration_ms) || 0;
  if (ms <= 0) return "";
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Which Media section a card is listed under — the four `assetOrigin` names. */
const SECTION_NAME = {
  board: "Storyboard Frames",
  video: "Video",
  image: "Images",
  audio: "Audio",
};

/**
 * WHAT THE PROPERTIES VIEW PRINTS — `[label, value]` pairs, in reading order.
 *
 * ⚠ IT READS THE CARD AND NOTHING ELSE. Every line is a field the client already
 * holds, so opening it costs no request and it cannot show something staler than
 * the card beside it. There is no width, height or codec on this list for exactly
 * that reason: nothing in the client knows them — the server does not measure
 * them, and an `imageio-ffmpeg` install ships no `ffprobe` to ask — and a
 * dimensions row reading "—" on every card would be furniture.
 */
function assetFacts(asset, used) {
  const kind = asset?.kind || "image";
  const src = asset?.src || {};
  const rows = [
    [
      "Kind",
      kind === "color"
        ? "Colour card"
        : kind === "audio"
          ? "Audio file"
          : kind === "video"
            ? isVeoRender(asset)
              ? "Video — Veo render"
              : "Video file"
            : "Still image",
    ],
    ["Section", SECTION_NAME[assetOrigin(asset)] || "Media"],
    ["Length", naturalLength(asset) || "not measured"],
    ["In the cut", used ? `${used} clip${used === 1 ? "" : "s"}` : "not used"],
  ];
  if (kind === "color") {
    rows.push(["Colour", (asset?.color || "#000000").toUpperCase()]);
  } else if (src.kind === "panel" || src.kind === "pose") {
    // ONE-BASED, like every other panel number on screen. The stored index counts
    // from zero and printing it raw makes the first shot read as shot 0.
    rows.push(["From", `Storyboard panel ${Number(src.index ?? 0) + 1}`]);
  }
  const file = kind === "audio" ? asset?.upload_id || "" : src.upload_id || "";
  if (file) rows.push(["File id", file]);
  // ⚠ THE CREDIT, AND IT IS ONLY EVER ON A CARD THAT ACTUALLY OWES ONE. A sound
  // taken from the Sounds tab arrives with a printable line (CC BY: the author
  // must be named in the finished video; CC0: where it came from, for the
  // record). Everything the user made or uploaded themselves has "" and gets no
  // row — a "Credit: —" on every photo would be furniture, and worse, it would
  // make the rows that DO matter unremarkable. This properties view is where the
  // obligation is readable months after the search that found it was closed.
  if (asset?.attribution) rows.push(["Credit", asset.attribution]);
  return rows;
}

/**
 * @param assets    the cards to draw — one section's worth
 * @param urls      asset id → object URL of its thumbnail
 * @param usedKeys  which assets currently have a clip on the timeline, by id.
 *                  ⚠ NOT to grey anything out: a card with no clip is the whole
 *                  point of the library. It drives the ✕'s wording, so "this
 *                  also removes 2 clips" is said before it happens rather than
 *                  discovered afterwards.
 * @param onPlace   double-click / ↵ — put this on the timeline without a drag.
 *                  A drag is the gesture, but it cannot be the ONLY gesture:
 *                  there is no keyboard path through a drag, and a long library
 *                  scrolled away from the row you want is a drag that cannot be
 *                  completed.
 * @param onDelete  the ✕. Removes the asset AND any clip made from it — asked
 *                  for directly ("when user cilck x buttun so clip in media
 *                  panel so direct delele fuction no dropdwon delete and cancel
 *                  option not need here"), so there is no confirm step.
 * @param onDownload SAVE THE FILE TO DISK. ⚠ DRAWN ON A VEO RENDER AND ON NOTHING
 *                  ELSE — see the button below for why, and `isVeoRender` in
 *                  `scene.js` for what counts as one. Optional: without it no card
 *                  offers it, which is what every other list of these cards wants.
 * @param onRename  COMMIT A NEW NAME for one source — `(asset, name) => void`.
 *                  Asked for as "user go and double click on clip text so uver
 *                  get rename option … or user right click of moue on clip so
 *                  user get dropdown panel so rename of each clip in media kepp
 *                  both fuction", so it is on BOTH gestures and they are one
 *                  handler. Optional: without it the name is plain text again and
 *                  the menu drops the line.
 *                  ⚠ THE COMPONENT NEVER WRITES THE CARD. It holds the DRAFT
 *                  while it is being typed and hands over a trimmed name on ↵ or
 *                  blur; the document belongs to the editor, as it does for every
 *                  other handler here.
 * @param onSelectClips select every clip made from this source, so "which shots
 *                  is this in?" is answerable from the library. Optional, and the
 *                  line is DISABLED rather than hidden when the count is 0 — an
 *                  unused card is the normal state of a library, and a menu that
 *                  changes shape card to card is harder to learn than a greyed
 *                  line that says why.
 */
export default function MediaBin({
  assets,
  urls,
  usedCount,
  view = "icon",
  onPlace,
  onDelete,
  onDownload,
  onRename,
  onSelectClips,
}) {
  // WHICH CARD'S MENU IS OPEN: `{ id, dx, dy, props }`.
  //
  // ⚠ `dx` / `dy` ARE THE POINTER'S OFFSET INSIDE THE CARD, NOT ITS PLACE ON THE
  // SCREEN. The menu is `position: fixed`, so it needs viewport coordinates — but
  // storing them is what made the menu a thing that goes stale: the Media pane
  // scrolls, and a box pinned to where the pointer WAS then floats over a
  // different card. Kept as an offset, the same anchor still resolves after any
  // scroll, and `placeMenu` re-resolves it. See its note.
  const [menu, setMenu] = useState(null);
  // The card being renamed, and the name as it is being typed. ⚠ ONE OF EACH FOR
  // THE WHOLE LIST: two open inputs would fight over the focus, which is the same
  // reason `AnimaticLibrary` keys its rename by card rather than by project.
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");
  const menuRef = useRef(null);

  // The card the menu is about — and null when that card has since been deleted
  // or has left this section, which is how the menu shuts itself instead of
  // offering a Rename of nothing. Same trick as `clipMenuClip` in Timeline.jsx.
  const menuAsset = menu ? assets.find((a) => a.id === menu.id) || null : null;
  const menuUsed = menuAsset ? usedCount?.(menuAsset) || 0 : 0;

  /**
   * PUT THE MENU WHERE ITS CARD IS, and keep it on screen.
   *
   * ⚠ IT IS `position: fixed` AND NOT A CHILD OF THE CARD, and that is forced.
   * The Media pane scrolls and clips, so a box rendered inside a card would be
   * cut off at the pane's edge and a card low in a long library would open a menu
   * that is mostly not there. The timeline's clip menu answers the same problem
   * by measuring into `.tl-cols`, the one ancestor there that clips nothing; this
   * pane has no such ancestor, so the viewport is it.
   *
   * ⚠ THE ANCHOR IS RE-READ FROM THE CARD EVERY TIME, WHICH IS WHY IT SURVIVES A
   * SCROLL — and the first version of this closed on any scroll instead, which
   * was wrong twice over. It was fragile: focusing the first line makes the pane
   * emit a scroll of its own (observed in `editor_media_bin_check`), so the menu
   * shut in the same frame it opened. And it was unnecessary: an offset into the
   * card resolves again after any scroll, so following the card is both simpler
   * and what a menu pointing at something should do.
   *
   * ⚠ IT FLIPS ABOUT THE POINTER rather than sliding along the screen edge, so
   * the pointer stays on a corner of the menu and the next press is a short one.
   *
   * @param closeIfGone shut the menu when its card has scrolled out of the
   *        window — a menu pointing at something you cannot see is worse than no
   *        menu. Only the scroll handler asks for this: doing it from the layout
   *        effect would race the very first paint, where the card may not be
   *        measurable yet.
   */
  function placeMenu(closeIfGone) {
    const el = menuRef.current;
    if (!menu || !el) return;
    // ⚠ A QUOTED ATTRIBUTE VALUE, NOT `CSS.escape` — the same note the timeline's
    // clip menu carries: that function escapes for an IDENTIFIER, which is wrong
    // inside quotes, where any character but the quote is taken literally.
    const card = document.querySelector(`[data-asset="${menu.id}"]`);
    if (!card) return;
    const box = card.getBoundingClientRect();
    if (closeIfGone && (box.bottom < 0 || box.top > window.innerHeight)) {
      setMenu(null);
      return;
    }
    const pad = 6;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const px = box.left + menu.dx;
    const py = box.top + menu.dy;
    const x = px + w + pad > window.innerWidth ? px - w : px;
    const y = py + h + pad > window.innerHeight ? py - h : py;
    el.style.left = `${Math.max(pad, Math.min(x, window.innerWidth - w - pad))}px`;
    el.style.top = `${Math.max(pad, Math.min(y, window.innerHeight - h - pad))}px`;
  }

  /* THE MENU DISMISSES THE TWO WAYS EVERY MENU IN THIS EDITOR DOES — Escape and
     a press outside it — and it FOLLOWS its card on a scroll rather than closing.
     Capture phase for the scroll, because it is the pane that scrolls and not the
     window, and a scroll event does not bubble. A RESIZE does close it: the whole
     pane re-flows, and a grid that has changed how many cards fit per row has
     moved the card out from under the pointer in a way no offset can track. */
  useEffect(() => {
    if (!menu) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setMenu(null);
    };
    const onDown = (e) => {
      if (!e.target.closest?.(".fs-card-menu")) setMenu(null);
    };
    const onScroll = () => placeMenu(true);
    const onResize = () => setMenu(null);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [menu]);

  /* ⚠ ON EVERY RENDER, WITH NO DEPENDENCY LIST, because the box CHANGES SIZE:
     switching to Properties makes it taller, and a menu opened near the bottom of
     the screen would grow off it. Re-placing after every render keeps it whole. */
  useLayoutEffect(() => {
    placeMenu(false);
  });

  /** Open the name for typing, with the old one in it and selected. */
  function startRename(asset) {
    if (!onRename || !asset?.id) return;
    setMenu(null);
    setDraft(asset.label || "");
    setEditing(asset.id);
  }

  /**
   * TAKE THE TYPED NAME, or drop it.
   *
   * ⚠ AN EMPTY NAME IS A CANCEL, NOT A BLANK NAME. A card with no label falls
   * back to "Untitled" / "Panel" for its caption, so writing "" would look
   * exactly like the rename having failed. Same rule `AnimaticLibrary.saveRename`
   * follows — and this is the BLUR path too, so clicking away can never wipe a
   * name that was already there.
   */
  function commitRename(asset) {
    const next = draft.replace(/\s+/g, " ").trim();
    setEditing(null);
    if (!next || next === (asset?.label || "")) return;
    onRename?.(asset, next);
  }

  return (
    <div className={`fs-wrap fs-vertical fs-view-${view === "list" ? "list" : "icon"}`}>
      <div className="fs-row">
        {assets.map((asset) => {
          const kind = asset.kind || "image";
          const used = usedCount?.(asset) || 0;
          const length = naturalLength(asset);
          const naming = editing === asset.id;
          return (
            <div
              key={asset.id}
              className={`fs-card fs-bin-card${menu?.id === asset.id ? " menu-open" : ""}`}
              /* WHAT THE MENU ANCHORS TO. `placeMenu` re-reads this card's box on
                 every render and every scroll, so the menu tracks it instead of
                 going stale where the pointer happened to be. */
              data-asset={asset.id}
              title={
                `${asset.label || "Untitled"}\n` +
                (used
                  ? `${used} clip${used === 1 ? "" : "s"} on the timeline`
                  : "Not on the timeline") +
                "\nDrag onto a row, or double-click to add it at the playhead." +
                (onRename
                  ? "\nDouble-click its NAME to rename it; right-click for more."
                  : "")
              }
              /* ⚠ NOT DRAGGABLE WHILE THE NAME IS BEING TYPED. A text field inside
                 a draggable element cannot be selected with the mouse — the drag
                 wins the gesture — so a rename would be a field you could only
                 edit from the end. Switching it off for the one card being renamed
                 costs nothing: it is not a card you are dragging at that moment. */
              draggable={!naming}
              /* ⚠ TWO ENTRIES ON THE CLIPBOARD, exactly as `FrameStrip` does it.
                 The payload says WHAT is being dragged; the empty
                 `…-image` / `…-video` / `…-audio` marker beside it is what lets a
                 lane refuse the drop mid-drag, where `getData` reads blank in
                 every browser and only the type list is visible (`dragKind` in
                 Timeline.jsx). Without the marker no row could light up.
                 ⚠ AND THE PAYLOAD KIND IS "asset", NOT "frame". That one word is
                 the difference between "move the clip that exists" and "make a
                 new clip from this source" — see `dropAsset`. */
              onDragStart={(e) => {
                const marker =
                  kind === "audio" ? "audio" : kind === "video" ? "video" : "image";
                e.dataTransfer.effectAllowed = "copy";
                e.dataTransfer.setData(
                  "application/x-anim-asset",
                  JSON.stringify({ kind: "asset", id: asset.id })
                );
                e.dataTransfer.setData(`application/x-anim-${marker}`, "");
                /* ⚠ AND A THIRD MARKER FOR A CARD OUT OF A STORYBOARD, for the
                   same reason as the second: the KIND alone cannot say which row
                   this belongs on. A Veo render and a file someone dropped in are
                   both `video`, and they go on different rows — so where a card
                   CAME FROM has to be on the clipboard too, or a lane cannot tell
                   them apart until after the drop. `cardRowKind(kind, true)` is
                   what reads it (`laneTakes` in Timeline.jsx). */
                if (assetOrigin(asset) === "board") {
                  e.dataTransfer.setData("application/x-anim-board", "");
                }
              }}
              onDoubleClick={() => onPlace?.(asset)}
              /* RIGHT-CLICK — THE COMMANDS THE CARD ALREADY HAS, PLUS THE ONES
                 THERE IS NO ROOM FOR ON IT. Asked for exactly there: "user right
                 click of moue on clip so user get dropdown panel so rename of each
                 clip in media kepp both fuction and add in drop down like more
                 option".
                 ⚠ IT OPENS ON EVERY CARD, which is the OPPOSITE of the timeline's
                 rule (`clipMenuOffers` returns null for a bar with nothing to
                 offer, and the browser's own menu stays). The difference is that
                 every card here HAS something: Rename, ＋, Properties and ✕ are on
                 all four kinds, so there is no such thing as an empty menu in this
                 pane and never a box of greyed-out lines.
                 ⚠ AND IT DOES NOT OPEN OVER THE NAME FIELD. While a rename is in
                 progress the browser's own cut/copy/paste menu is the useful one,
                 and it is the only way to paste a name in. */
              onContextMenu={(e) => {
                if (naming) return;
                e.preventDefault();
                e.stopPropagation();
                // ⚠ IT OPENS, IT DOES NOT TOGGLE. The outside-press listener has
                // already fired on this same gesture's `pointerdown` and shut
                // whatever was open, so a toggle here would read the pending
                // `null` and re-open on every second right-click of the same card.
                // ⚠ AND IT STORES THE POINTER AS AN OFFSET INTO THE CARD, not as
                // a place on the screen — see the note on `menu` above.
                const box = e.currentTarget.getBoundingClientRect();
                setMenu({
                  id: asset.id,
                  dx: e.clientX - box.left,
                  dy: e.clientY - box.top,
                  props: false,
                });
              }}
            >
              <div className="fs-thumb">
                {/* A COLOUR CARD HAS NO FILE, so it shows itself rather than
                    waiting for a picture that is never coming — the same rule
                    `FrameStrip` follows, and for the same reason. An AUDIO card
                    has no picture either, but it does have a name, so it gets a
                    glyph instead of a permanent spinner. */}
                {kind === "color" ? (
                  <span className="fs-swatch" style={{ background: asset.color || "#000" }} />
                ) : kind === "audio" ? (
                  <span className="fs-bin-audio">♪</span>
                ) : urls[asset.id] ? (
                  <img src={urls[asset.id]} alt={asset.label || "Media"} />
                ) : (
                  <span className="fs-thumb-wait" />
                )}
                {/* HOW MANY CLIPS USE IT, where `FrameStrip` puts a clip's place
                    in the sequence. A library card has no place in the sequence —
                    it may have several, or none — so the badge answers the
                    question this pane is actually asked: "is this in the cut?" */}
                <span className={`fs-num ${used ? "" : "fs-num-unused"}`}>
                  {used ? `×${used}` : "–"}
                </span>
                {kind === "video" && (
                  <span className="fs-kind" title="Video file">
                    {length ? `▶ ${length}` : "▶"}
                  </span>
                )}
              </div>

              <div className="fs-foot">
                {/* PRINTED, NOT TYPED. This is the source's own length; the hold
                    on the timeline is a property of a clip and is edited there. */}
                <span className="fs-dur">
                  <span className="fs-dur-static">{length || "—"}</span>
                </span>
                <span className="fs-tools">
                  {/* ⬇ — AND ONLY ON A VEO RENDER.
                      ⚠ IT IS NOT AN OMISSION THAT THE OTHER CARDS LACK IT. Every
                      other source in a project is already somewhere else: an
                      upload came off this machine, a panel is still on the board,
                      a colour card has no file at all. A render exists ONLY here
                      and costs money to make again, and the reason it was asked
                      for says exactly that — "if user want delete project so user
                      first download veo gneereted video in midea panel". Deleting
                      the project must stop being the thing that destroys it.

                      ⚠ IT GOES FIRST IN THE ROW, AND THAT IS A LAYOUT RULE RATHER
                      THAN A PREFERENCE. `.fs-tools` is the right-hand child of a
                      `space-between` foot, so it grows LEFTWARD off the card's right
                      edge and an extra
                      button in the MIDDLE pushes ＋ one slot left on the cards that
                      have one — and a library is a COLUMN of cards, so ＋ then sits
                      in two different places down the same list. Reported exactly
                      that way: "keep download icon first because not match uper
                      clip in icon see". At the FRONT it costs nothing: ＋ and ✕
                      stay in the same two columns on every card, and the only
                      thing that varies is whether a third icon hangs off to the
                      left of them, which is what a card having MORE to offer
                      should look like. */}
                  {onDownload && isVeoRender(asset) && (
                    <button
                      type="button"
                      className="fs-tool"
                      title={`Save “${asset.label || "this render"}” to your computer — it is a Veo render, so this is the only copy`}
                      aria-label="Download this Veo video"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDownload(asset);
                      }}
                    >
                      <Icon name="download" />
                    </button>
                  )}
                  <button
                    type="button"
                    className="fs-tool"
                    title="Add this to the timeline at the playhead"
                    onClick={(e) => {
                      e.stopPropagation();
                      onPlace?.(asset);
                    }}
                  >
                    ＋
                  </button>
                  <button
                    type="button"
                    className="fs-tool danger"
                    title={
                      used
                        ? `Remove from Media — also deletes ${used} clip${
                            used === 1 ? "" : "s"
                          } using it`
                        : "Remove from Media"
                    }
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete?.(asset);
                    }}
                  >
                    <Icon name="close" />
                  </button>
                </span>
              </div>

              {/* THE NAME — AND, WHERE THERE IS SOMEWHERE TO WRITE IT, THE FIELD
                  THAT EDITS IT. Double-click ON THE TEXT renames; a double-click
                  anywhere else on the card still places it at the playhead, which
                  is what the `stopPropagation` below is protecting.
                  ⚠ THE TWO GESTURES ARE BOTH DOUBLE-CLICKS ON PURPOSE — it is
                  what was asked for ("double click on clip text so uver get rename
                  option"), and it is what every file list on both desktops does:
                  the NAME is the part of a tile that renames. */}
              {naming ? (
                <input
                  className="fs-name-input"
                  value={draft}
                  /* ⚠ `focus({ preventScroll: true })` AND `select()`, NOT
                     `autoFocus`. Plain autofocus lets the browser scroll the field
                     into view, and the scrolling ancestor here is the Media pane —
                     so renaming a card would jump the list under the pointer. The
                     `activeElement` guard is what stops the text being re-selected
                     on every keystroke, since a ref callback runs every render. */
                  ref={(el) => {
                    if (el && document.activeElement !== el) {
                      el.focus({ preventScroll: true });
                      el.select();
                    }
                  }}
                  maxLength={120}
                  aria-label="Name of this media"
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => commitRename(asset)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      commitRename(asset);
                    } else if (e.key === "Escape") {
                      e.preventDefault();
                      setEditing(null);
                    }
                  }}
                  /* The card under it drags, places and opens a menu; while the
                     name is being typed the field owns all three. (The editor's
                     own shortcuts already stand aside for an INPUT — see the
                     `typing` guard on the keydown handler in AnimaticEditor.) */
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                  onDoubleClick={(e) => e.stopPropagation()}
                  onDragStart={(e) => e.stopPropagation()}
                />
              ) : (
                <div
                  className={`fs-label${onRename ? " fs-label-edit" : ""}`}
                  title={
                    onRename
                      ? // ⚠ NO LEADING BLANK LINE ON AN UNNAMED CARD. Most cards
                        // in a fresh library have no label at all, and
                        // `"" + "\n" + hint` renders as a tooltip that opens with
                        // an empty row.
                        `${asset.label ? `${asset.label}\n` : ""}Double-click to rename`
                      : asset.label || ""
                  }
                  onDoubleClick={
                    onRename
                      ? (e) => {
                          e.stopPropagation();
                          startRename(asset);
                        }
                      : undefined
                  }
                >
                  {asset.label || (assetOrigin(asset) === "board" ? "Panel" : "Untitled")}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ⬇ ONE CARD'S MENU, AT THE POINTER.
          ⚠ IT BORROWS `.tl-layer-menu`'S SURFACE AND ITS ROWS, exactly as the
          timeline's clip menu and the settings menu do. Four popovers in one
          editor that looked different would read as four unrelated mechanisms;
          `.fs-card-menu` overrides the POSITION and nothing else — see its note in
          animatic-editor.css for why it must also restate its width.
          ⚠ AND IT IS A SIBLING OF THE CARDS, NOT A CHILD OF ONE: the pane clips
          and scrolls, so a menu inside a card would be cut off at its edge. */}
      {menuAsset && (
        <div
          className="tl-layer-menu fs-card-menu"
          ref={menuRef}
          role="menu"
          aria-label={`${menuAsset.label || "Media"} — actions`}
          /* The card under it drags and places on a double-click, and the pane
             under that scrolls, so the menu stops everything it is handed. */
          onClick={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          onContextMenu={(e) => e.preventDefault()}
        >
          {/* WHICH CARD THIS IS ABOUT. The menu opens at the pointer, which is not
              always over the card once it has been flipped or clamped, and
              "Rename" on its own would not say what of. */}
          <span className="tl-clip-menu-of">{menuAsset.label || "Untitled"}</span>

          {menu.props ? (
            <>
              {/* ⚠ THE PROPERTIES REPLACE THE MENU RATHER THAN OPENING A DIALOG.
                  This is four or five short lines about the card just clicked; a
                  modal for it would dim the pane and put the answer a long way from
                  the question, which is the same mistake the Add-layer picker was
                  reported for. ‹ Back returns to the commands, so one right-click
                  still reaches everything. */}
              <dl className="fs-card-props">
                {assetFacts(menuAsset, menuUsed).map(([label, value]) => (
                  <div className="fs-card-prop" key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              <button
                type="button"
                role="menuitem"
                className="tl-layer-menu-opt"
                ref={(el) => el?.focus({ preventScroll: true })}
                onClick={() => setMenu((m) => (m ? { ...m, props: false } : m))}
                title="Back to the commands"
              >
                <span className="tl-layer-menu-ico">‹</span>
                Back
              </button>
            </>
          ) : (
            <>
              {/* RENAME FIRST — it is the command this menu was asked for, and the
                  only one with no other door in besides the double-click. */}
              {onRename && (
                <button
                  type="button"
                  role="menuitem"
                  className="tl-layer-menu-opt"
                  /* ⚠ FOCUSED BECAUSE IT IS THE FIRST LINE, and only one line in a
                     menu may ask for the keyboard: two `ref` focus calls fight over
                     it and the winner is whichever ran last, which is not the line
                     at the top. */
                  ref={(el) => el?.focus({ preventScroll: true })}
                  onClick={() => startRename(menuAsset)}
                  title="Give this source a name of your own — the clips already cut from it that still carry the old one follow it"
                >
                  <span className="tl-layer-menu-ico">
                    <Icon name="pencil" />
                  </span>
                  Rename
                </button>
              )}
              <button
                type="button"
                role="menuitem"
                className="tl-layer-menu-opt"
                ref={onRename ? undefined : (el) => el?.focus({ preventScroll: true })}
                onClick={() => {
                  setMenu(null);
                  onPlace?.(menuAsset);
                }}
                title="Add this to the timeline at the playhead — the same as the card's ＋"
              >
                <span className="tl-layer-menu-ico">＋</span>
                Add to timeline
              </button>
              {/* WHERE IS IT IN THE CUT? ⚠ DISABLED AND NOT HIDDEN when the count
                  is 0: an unused card is the normal, healthy state of a library,
                  and a line that vanishes reads as a menu that changes shape for no
                  reason. Greyed out with the count in the name answers the question
                  without being pressed. */}
              {onSelectClips && (
                <button
                  type="button"
                  role="menuitem"
                  className="tl-layer-menu-opt"
                  disabled={!menuUsed}
                  onClick={() => {
                    setMenu(null);
                    onSelectClips(menuAsset);
                  }}
                  title={
                    menuUsed
                      ? "Select every clip on the timeline that plays this source"
                      : "Nothing on the timeline uses this source yet"
                  }
                >
                  <span className="tl-layer-menu-ico">
                    <Icon name="select" />
                  </span>
                  {menuUsed
                    ? `Select its ${menuUsed} clip${menuUsed === 1 ? "" : "s"}`
                    : "Select its clips"}
                </button>
              )}
              {/* ⚠ THE SAME GATE AS THE CARD'S ⬇, deliberately: a menu line
                  offering to download a storyboard panel would promise a file that
                  is not this editor's to give. See the button above. */}
              {onDownload && isVeoRender(menuAsset) && (
                <button
                  type="button"
                  role="menuitem"
                  className="tl-layer-menu-opt"
                  onClick={() => {
                    setMenu(null);
                    onDownload(menuAsset);
                  }}
                  title="Save this Veo render to your computer — deleting the project will not lose it then"
                >
                  <span className="tl-layer-menu-ico">
                    <Icon name="download" />
                  </span>
                  Download
                </button>
              )}
              <button
                type="button"
                role="menuitem"
                className="tl-layer-menu-opt"
                onClick={() => setMenu((m) => (m ? { ...m, props: true } : m))}
                title="What this source is, how long it runs, and how much of the cut uses it"
              >
                <span className="tl-layer-menu-ico">
                  <Icon name="card" />
                </span>
                Properties
              </button>
              {/* ⚠ NO CONFIRM STEP HERE EITHER, and it is the card's ✕ that sets
                  the rule: "no dropdwon delete and cancel option not need here".
                  The count is in the title, before the press, which is where the
                  warning went. */}
              <button
                type="button"
                role="menuitem"
                className="tl-layer-menu-opt danger"
                onClick={() => {
                  setMenu(null);
                  onDelete?.(menuAsset);
                }}
                title={
                  menuUsed
                    ? `Remove from Media — also deletes ${menuUsed} clip${
                        menuUsed === 1 ? "" : "s"
                      } using it`
                    : "Remove from Media"
                }
              >
                <span className="tl-layer-menu-ico">
                  <Icon name="trash" />
                </span>
                Remove from Media
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
