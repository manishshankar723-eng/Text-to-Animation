// PaneSplitter.jsx — the draggable gap between two panes.
//
// It IS the gap: the workspace grid used to put 0.55rem of empty space between
// its panes, and this sits in exactly that space at exactly that width, so
// nothing moved to make room for it. What used to be a dead margin is now the
// handle, which is how every NLE does this — you reach for the seam, not for a
// widget beside it.
//
// One component drives both axes; they differ only in which coordinate is read
// and which way the value goes. `sign` says which: the divider on the LEFT of a
// pane makes it wider as the pointer travels right (+1), the one on its RIGHT
// makes it narrower (-1).
//
// The parent owns the number. This reports what the drag is asking for, already
// clamped, and never stores anything.
import { useRef, useState } from "react";

export default function PaneSplitter({
  // "vertical" is a divider BETWEEN COLUMNS (drag it sideways); "horizontal"
  // divides rows. Named for the seam, the way `aria-orientation` names it.
  orientation = "vertical",
  value,
  min,
  max,
  sign = 1,
  // One key press. 16px is a visible nudge without being a jump.
  step = 16,
  onChange,
  onReset,
  label,
  className = "",
}) {
  const dragRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const axis = orientation === "vertical" ? "clientX" : "clientY";
  const ask = (v) => onChange?.(Math.min(max, Math.max(min, v)));

  function begin(e) {
    if (e.button !== 0) return;
    // Stops the press selecting the text in the panes either side of it.
    e.preventDefault();
    // ⚠ THE SIZE IS READ ONCE, HERE, and every move is measured from it. Adding
    // up per-move deltas drifts the moment a drag hits `min` or `max`: the
    // pointer keeps travelling while the value cannot, and coming back the pane
    // starts moving again from wherever the pointer happens to be.
    dragRef.current = { from: e[axis], start: value };
    setDragging(true);

    const move = (ev) => {
      const d = dragRef.current;
      if (!d) return;
      ask(d.start + (ev[axis] - d.from) * sign);
    };
    const up = () => {
      dragRef.current = null;
      setDragging(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    // On the WINDOW, not the handle: the pointer leaves an 8px strip instantly,
    // and the drag has to keep working when it does.
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function key(e) {
    const less = orientation === "vertical" ? "ArrowLeft" : "ArrowUp";
    const more = orientation === "vertical" ? "ArrowRight" : "ArrowDown";
    if (e.key === less) ask(value - step * sign);
    else if (e.key === more) ask(value + step * sign);
    else if (e.key === "Home") onReset?.();
    else return;
    e.preventDefault();
  }

  return (
    <div
      className={`an-split an-split-${orientation} ${dragging ? "dragging" : ""} ${className}`}
      onPointerDown={begin}
      onDoubleClick={() => onReset?.()}
      onKeyDown={key}
      role="separator"
      tabIndex={0}
      aria-orientation={orientation}
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      title={`${label} — drag to resize, double-click to reset`}
    >
      {/* Drawn only on hover, focus or drag: a permanent line between every
          pane is chrome, and there are three of them. */}
      <span className="an-split-grip" />
    </div>
  );
}
