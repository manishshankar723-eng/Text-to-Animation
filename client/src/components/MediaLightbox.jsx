// MediaLightbox — the full-screen viewer for one showcase item: a picture, or a
// video that actually plays.
//
// ⚠ WHY THIS IS NOT `ImageLightbox` WITH A FLAG. That component is four lines
// and is opened by the board, the cast page and the key-pose flipbook; a video
// element inside it would put `pause()` and `currentTime` into a viewer whose
// entire job is one `<img>`. This one is the SALES viewer, and it does three
// things that one must never do: it plays, it steps through a set, and it
// carries a button that asks somebody to sign in.
//
// ⚠ IT WEARS `ImageLightbox`'s OWN CLASSES ON PURPOSE — `.lightbox-overlay`,
// `.lightbox-figure`, `.lightbox-close`, `.lightbox-nav`, `.lightbox-count` are
// all already in `lightbox.css` with the fix that made the controls readable on
// near-white storyboard paper. A second set that merely RESEMBLED them is
// exactly the mismatch this repo keeps paying for.
//
// ⚠ THE VIDEO IS `controls` + `autoPlay` + `playsInline`, AND MUTED IS NOT SET.
// Clicking a card is a deliberate act — the visitor asked for this clip — so it
// is allowed to have sound. Browsers refuse to autoplay an unmuted video, and
// the refusal is the RIGHT outcome: `controls` means the play button is already
// under the pointer, so a blocked autoplay costs one click and never a silent
// film nobody realised had audio.
import { useEffect, useRef } from "react";

/**
 * @param {object} item — one row from `GET /public/showcase`: `{id, title,
 *   blurb, workflow, kind, media_url, poster_url}`, with the URLs already made
 *   absolute by the caller. `null` draws nothing.
 * @param {function} onClose
 * @param {function} [onStep] — `(-1 | 1) => void`. Absent hides the arrows.
 * @param {string}  [count] — "3 / 12", printed under the picture.
 * @param {function} [onUse] — the "Make one like this" button. On the public
 *   page this is the SIGN-IN GATE, not a navigation: see `Explore.jsx`.
 * @param {string}  [useLabel]
 */
export default function MediaLightbox({
  item,
  onClose,
  onStep,
  count = "",
  onUse,
  useLabel = "Try this workflow",
}) {
  // ⚠ THE HANDLER IS RE-BOUND WHENEVER THE CALLBACKS CHANGE, not bound once on
  // mount. Bound once, `onStep` would be captured from the first render and the
  // arrow keys would keep stepping from whichever item was open when the viewer
  // was created — which is the classic "the arrows work once" bug.
  useEffect(() => {
    if (!item) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onClose?.();
      // ⚠ ARROWS ARE IGNORED WHILE THE VIDEO HAS FOCUS. Left/right are SEEK on
      // a focused `<video>`, and stealing them would make a clip impossible to
      // scrub with the keyboard.
      const onPlayer = document.activeElement?.tagName === "VIDEO";
      if (onPlayer) return;
      if (e.key === "ArrowLeft") onStep?.(-1);
      if (e.key === "ArrowRight") onStep?.(1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, onClose, onStep]);

  // ⚠ THE PREVIOUS CLIP IS STOPPED WHEN THE VIEWER MOVES ON. React reuses the
  // same `<video>` node when only its `src` changes, and a browser that is
  // mid-download does not always drop the old audio track — so stepping from
  // one film to the next could leave you listening to both. The `key` below
  // forces a fresh element; this pauses the outgoing one first.
  const videoRef = useRef(null);
  useEffect(() => {
    const el = videoRef.current;
    return () => {
      try {
        el?.pause();
      } catch {
        // A node already torn down by React. Nothing to stop.
      }
    };
  }, [item?.id]);

  if (!item) return null;
  const isVideo = item.kind === "video";

  return (
    <div className="lightbox-overlay" onClick={onClose}>
      {/* Wrapper shrinks to the media so the ✕ sits on its corner, not in the
          far corner of the screen — same as ImageLightbox. */}
      <div
        className="lightbox-figure xp-view-figure"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={item.title || "Showcase"}
      >
        <button
          type="button"
          className="lightbox-close"
          onClick={onClose}
          title="Close"
          aria-label="Close"
        >
          ✕
        </button>

        {onStep && (
          <>
            <button
              type="button"
              className="lightbox-nav prev"
              onClick={() => onStep(-1)}
              title="Previous"
              aria-label="Previous"
            >
              ‹
            </button>
            <button
              type="button"
              className="lightbox-nav next"
              onClick={() => onStep(1)}
              title="Next"
              aria-label="Next"
            >
              ›
            </button>
          </>
        )}

        {isVideo ? (
          <video
            /* ⚠ KEYED BY THE ITEM. Without this React keeps ONE element and
               swaps its `src`, and a `<video>` whose src changes underneath it
               keeps the old buffer, the old currentTime and sometimes the old
               audio. A new key is a new player, every time. */
            key={item.id}
            ref={videoRef}
            className="lightbox-img xp-view-video"
            src={item.media_url}
            poster={item.poster_url || undefined}
            controls
            autoPlay
            playsInline
            /* The clip is the product demo, not a background loop — it ends
               when it ends, and the arrows are right there. */
            preload="metadata"
          />
        ) : (
          <img
            className="lightbox-img"
            src={item.media_url}
            alt={item.title || ""}
          />
        )}

        {/* ⚠ THE CAPTION AND THE BUTTON SIT INSIDE THE FIGURE, over the bottom
            of the media — not under it. The figure is capped at 92vh, so a bar
            below it would push a tall 9:16 clip off the top of the screen. */}
        {(item.title || item.blurb || onUse) && (
          <div className="xp-view-bar">
            <span className="xp-view-text">
              {item.title && (
                <span className="xp-view-title">{item.title}</span>
              )}
              {item.blurb && <span className="xp-view-blurb">{item.blurb}</span>}
            </span>
            {onUse && (
              <button
                type="button"
                className="btn primary xp-view-cta"
                onClick={() => onUse(item)}
              >
                {useLabel} →
              </button>
            )}
          </div>
        )}

        {count && <span className="lightbox-count xp-view-count">{count}</span>}
      </div>
    </div>
  );
}
