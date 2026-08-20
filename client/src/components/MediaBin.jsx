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
//
// See `animatic/assets.js` for what an asset is and why the library exists.
import Icon from "./Icon.jsx";
import { assetOrigin } from "../animatic/assets.js";

/** A source's natural length, or "" when the server could not measure it. */
function naturalLength(asset) {
  const ms = Number(asset?.duration_ms) || 0;
  if (ms <= 0) return "";
  return `${(ms / 1000).toFixed(1)}s`;
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
 */
export default function MediaBin({
  assets,
  urls,
  usedCount,
  view = "icon",
  onPlace,
  onDelete,
}) {
  return (
    <div className={`fs-wrap fs-vertical fs-view-${view === "list" ? "list" : "icon"}`}>
      <div className="fs-row">
        {assets.map((asset) => {
          const kind = asset.kind || "image";
          const used = usedCount?.(asset) || 0;
          const length = naturalLength(asset);
          return (
            <div
              key={asset.id}
              className="fs-card fs-bin-card"
              title={
                `${asset.label || "Untitled"}\n` +
                (used
                  ? `${used} clip${used === 1 ? "" : "s"} on the timeline`
                  : "Not on the timeline") +
                "\nDrag onto a row, or double-click to add it at the playhead."
              }
              draggable
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

              <div className="fs-label" title={asset.label || ""}>
                {asset.label || (assetOrigin(asset) === "board" ? "Panel" : "Untitled")}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
