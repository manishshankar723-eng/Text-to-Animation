// FrameStrip.jsx — the row of frames: thumbnail, hold time, reorder, delete.
//
// This is where a hold is TYPED ("image 1 = 2 sec, image 5 = 5 sec"); the
// timeline below is where it's dragged. Both write the same milliseconds, so
// it doesn't matter which one you reach for.
import { useEffect, useRef, useState } from "react";

const MIN_MS = 100;
const MAX_MS = 600000;

// A drop of many files is sorted by filename, so `01.png … 12.png` lands in the
// order the user named them rather than whatever order the OS handed over.
export function sortFiles(files) {
  return Array.from(files).sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" })
  );
}

function DurationInput({ ms, onCommit }) {
  const [text, setText] = useState((ms / 1000).toFixed(1));
  const [editing, setEditing] = useState(false);

  // Follow the project when the value changes elsewhere (a timeline drag, or
  // "fit to audio") — but never while the field is being typed into.
  useEffect(() => {
    if (!editing) setText((ms / 1000).toFixed(1));
  }, [ms, editing]);

  function commit() {
    setEditing(false);
    const seconds = parseFloat(text.replace(",", "."));
    if (!Number.isFinite(seconds)) {
      setText((ms / 1000).toFixed(1));
      return;
    }
    const next = Math.min(MAX_MS, Math.max(MIN_MS, Math.round(seconds * 1000)));
    setText((next / 1000).toFixed(1));
    if (next !== ms) onCommit(next);
  }

  return (
    <span className="fs-dur">
      <input
        className="fs-dur-input"
        value={text}
        inputMode="decimal"
        onFocus={(e) => {
          setEditing(true);
          e.target.select();
        }}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.target.blur();
          if (e.key === "Escape") {
            setText((ms / 1000).toFixed(1));
            setEditing(false);
            e.target.blur();
          }
        }}
        aria-label="Seconds this frame is held"
      />
      <span className="fs-dur-unit">s</span>
    </span>
  );
}

export default function FrameStrip({
  frames,
  urls,
  selectedId,
  uploading,
  // `vertical` is the Media pane in the editor's workspace layout: the same
  // cards, stacked in a column instead of a scrolling row. Same markup on
  // purpose — the drag-to-reorder and duration handling shouldn't fork.
  vertical = false,
  // The Media pane supplies its own single "add assets" control, so the strip's
  // own add button and trailing add-card would be duplicates of it.
  showAdd = true,
  onSelect,
  onReorder,
  onDuration,
  onDelete,
  onDuplicate,
  onAddFiles,
}) {
  const fileRef = useRef(null);
  const [dragIndex, setDragIndex] = useState(null);
  const [overIndex, setOverIndex] = useState(null);

  function handleDrop(e, index) {
    e.preventDefault();
    if (e.dataTransfer?.files?.length) {
      onAddFiles(sortFiles(e.dataTransfer.files), index);
    } else if (dragIndex !== null && dragIndex !== index) {
      onReorder(dragIndex, index);
    }
    setDragIndex(null);
    setOverIndex(null);
  }

  return (
    <div className={`fs-wrap ${vertical ? "fs-vertical" : ""}`}>
      <div className="fs-head">
        <h3 className="fs-title">
          Frames <span className="muted">({frames.length})</span>
        </h3>
        <div className="fs-head-actions">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) onAddFiles(sortFiles(e.target.files));
              e.target.value = "";
            }}
          />
          {showAdd && (
            <button
              type="button"
              className="btn small"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              {uploading ? (
                <>
                  <span className="spinner-inline" /> Uploading…
                </>
              ) : (
                "＋ Add images"
              )}
            </button>
          )}
        </div>
      </div>

      <div
        className="fs-row"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => handleDrop(e, frames.length)}
      >
        {frames.map((f, i) => (
          <div
            key={f.id}
            className={[
              "fs-card",
              selectedId === f.id ? "sel" : "",
              overIndex === i ? "over" : "",
            ].join(" ")}
            draggable
            onDragStart={() => setDragIndex(i)}
            onDragEnd={() => {
              setDragIndex(null);
              setOverIndex(null);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setOverIndex(i);
            }}
            onDragLeave={() => setOverIndex((o) => (o === i ? null : o))}
            onDrop={(e) => handleDrop(e, i)}
            onClick={() => onSelect(f.id)}
          >
            <div className="fs-thumb">
              {urls[f.id] ? (
                <img src={urls[f.id]} alt={f.label || `Frame ${i + 1}`} />
              ) : (
                <span className="fs-thumb-wait" />
              )}
              <span className="fs-num">{i + 1}</span>
            </div>

            <div className="fs-foot">
              <DurationInput ms={f.duration_ms} onCommit={(ms) => onDuration(f.id, ms)} />
              <span className="fs-tools">
                <button
                  type="button"
                  className="fs-tool"
                  title="Duplicate this frame"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDuplicate(f.id);
                  }}
                >
                  ⧉
                </button>
                <button
                  type="button"
                  className="fs-tool danger"
                  title="Remove this frame"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(f.id);
                  }}
                >
                  ✕
                </button>
              </span>
            </div>
            {f.label && <div className="fs-label">{f.label}</div>}
          </div>
        ))}

        {showAdd && (
          <button
            type="button"
            className="fs-card fs-add"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            <span className="fs-add-plus">＋</span>
            <span className="fs-add-text">
              Add images
              <span className="muted"> or drop them here</span>
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
