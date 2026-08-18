// ZoomScrollbar.jsx — the bar along the bottom and down the side of the
// timeline. It is NOT a plain scrollbar, which is the whole point.
//
// The thumb shows which slice of the timeline the viewport is looking at, and
// the round grip on each END resizes that slice. Since the viewport's size in
// pixels never changes, resizing the slice is the same thing as zooming: drag
// the right grip left and the stretch between the two grips is blown up to fill
// the pane. That is how Premiere's scrollbars work, and it is why you can frame
// a section of the edit with one gesture instead of hunting for a zoom level
// and then scrolling to find your place again.
//
//   · drag the middle       → pan (ordinary scrolling)
//   · drag either grip      → zoom, with the OPPOSITE end pinned
//   · click the empty track → jump a page towards the click
//   · double-click the bar  → zoom to fit the whole thing
//
// One component drives both axes, because the two differ only in which
// properties they read — collected in `AXIS` so there is no second copy of the
// drag maths to keep in step.
import { useRef, useState } from "react";

const AXIS = {
  horizontal: { client: "clientX", rect: "width", start: "left", size: "width" },
  vertical: { client: "clientY", rect: "height", start: "top", size: "height" },
};

// The narrowest the thumb is allowed to be DRAWN. A thumb proportional to the
// view can be a pixel wide on a long timeline, and a one-pixel thumb carrying
// two grips is not grabbable.
const MIN_THUMB_PX = 30;
// …and the smallest slice a grip drag may ask for, as a fraction of the whole.
// The real limit is the zoom clamp the parent applies; this only stops the two
// grips from crossing over each other mid-drag.
const MIN_FRAC = 0.004;

const clamp01 = (v) => Math.min(1, Math.max(0, v));

/**
 * @param orientation "horizontal" | "vertical"
 * @param total    the content's full length in px at the current zoom
 * @param view     how much of it fits in the pane, in px
 * @param pos      how far it is scrolled, in px
 * @param onPan    (nextPos) — the pointer moved the thumb bodily
 * @param onZoom   (startFrac, endFrac) — the window the grips are asking for,
 *                 as fractions of the whole content. Fractions, NOT pixels,
 *                 because they survive the zoom the parent is about to apply:
 *                 the timeline still spans the same amount of TIME afterwards.
 */
export default function ZoomScrollbar({
  orientation = "horizontal",
  total,
  view,
  pos,
  onPan,
  onZoom,
  label,
}) {
  const ax = AXIS[orientation] || AXIS.horizontal;
  const barRef = useRef(null);
  const dragRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  const span = Math.max(1, total);
  // Where the thumb sits, in fractions of the whole content.
  const a = clamp01(pos / span);
  const b = clamp01((pos + view) / span);
  // Everything already fits: the thumb fills the bar and only the grips do
  // anything (they still zoom IN, which is worth having).
  const whole = view >= span - 1;
  const from = whole ? 0 : a;
  const to = whole ? 1 : Math.max(b, a + MIN_FRAC);

  const barLen = () => barRef.current?.getBoundingClientRect()[ax.rect] || 1;

  function begin(e, mode) {
    if (e.button !== 0) return;
    // Beat the lane underneath: a press on the bar must never scrub or select.
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = { mode, from: e[ax.client], a: from, b: to, len: barLen() };
    setDragging(true);

    const move = (ev) => {
      const d = dragRef.current;
      if (!d) return;
      // In bar fractions, so it can be compared with `a` and `b` directly.
      const delta = (ev[ax.client] - d.from) / d.len;
      if (d.mode === "pan") {
        const width = d.b - d.a;
        onPan?.(clamp01(Math.min(1 - width, d.a + delta)) * span);
        return;
      }
      if (d.mode === "start") {
        onZoom?.(clamp01(Math.min(d.b - MIN_FRAC, d.a + delta)), d.b);
        return;
      }
      onZoom?.(d.a, clamp01(Math.max(d.a + MIN_FRAC, d.b + delta)));
    };
    const up = () => {
      dragRef.current = null;
      setDragging(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // A press on the empty part of the track pages towards it, the one behaviour
  // every scrollbar has had since scrollbars existed.
  function page(e) {
    if (e.button !== 0 || whole) return;
    const rect = barRef.current.getBoundingClientRect();
    const at = (e[ax.client] - rect[ax.start]) / (rect[ax.rect] || 1);
    const width = to - from;
    const next = at < from ? from - width : from + width;
    onPan?.(clamp01(Math.min(1 - width, next)) * span);
  }

  return (
    <div
      ref={barRef}
      className={`tl-sbar tl-sbar-${orientation} ${dragging ? "dragging" : ""}`}
      onPointerDown={page}
      onDoubleClick={() => onZoom?.(0, 1)}
      role="presentation"
      aria-label={label}
    >
      <div
        className="tl-sbar-thumb"
        style={{
          // ⚠ CLAMPED SO THE THUMB CANNOT HANG OFF THE END. When the proportional
          // size is below the minimum the thumb is DRAWN bigger than it is, and
          // at full scroll that surplus hung past the end of the track: the
          // scroll had reached the end while the bar still looked like it had
          // somewhere left to go, which reads as content you cannot get to. The
          // position is a fraction of the bar, the minimum is in pixels, so the
          // clamp has to be a `min()` of the two units.
          [ax.start]: `min(${from * 100}%, 100% - max(${MIN_THUMB_PX}px, ${(to - from) * 100}%))`,
          [ax.size]: `max(${MIN_THUMB_PX}px, ${(to - from) * 100}%)`,
        }}
        onPointerDown={(e) => begin(e, "pan")}
        title={
          label
            ? `${label} — drag to move, drag an end to zoom, double-click to fit`
            : undefined
        }
      >
        <span
          className="tl-sbar-grip start"
          onPointerDown={(e) => begin(e, "start")}
          title="Drag to zoom — this end moves, the other stays put"
        />
        <span
          className="tl-sbar-grip end"
          onPointerDown={(e) => begin(e, "end")}
          title="Drag to zoom — this end moves, the other stays put"
        />
      </div>
    </div>
  );
}
