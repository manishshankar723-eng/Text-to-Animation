// FrameStrip.jsx — the row of frames: thumbnail, hold time, reorder, delete.
//
// This is where a hold is TYPED ("image 1 = 2 sec, image 5 = 5 sec"); the
// timeline below is where it's dragged. Both write the same milliseconds, so
// it doesn't matter which one you reach for.
//
// A "frame" is a CLIP now — a still, a piece of video, or a flat colour card —
// so a card has to say WHICH at a glance. The thumbnail alone can't: a video's
// poster is just a picture, and a colour card has no picture at all.
import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { frameOrigin } from "../animatic/scene.js";

const MIN_MS = 100;
const MAX_MS = 600000;

// How much SOURCE footage a video clip reads through, in seconds.
//
// Deliberately NOT the same number as the hold in the foot below: that one is
// how long the clip sits on the timeline, this one is how much of the file gets
// played inside it. They differ exactly when `speed` isn't 1, which is the case
// where showing only one of them is misleading.
function sourceSpanSeconds(clip) {
  const speed = Number(clip.speed) > 0 ? Number(clip.speed) : 1;
  const covered = (Number(clip.duration_ms) || 0) * speed;
  const inMs = Math.max(0, Number(clip.in_ms) || 0);
  const out = clip.out_ms;
  // Past the out point the clip holds its last frame, so it can never read more
  // source than the trim allows however long it is held.
  const limit = out === null || out === undefined ? covered : Math.max(0, Number(out) - inMs);
  return Math.min(covered, limit) / 1000;
}

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
  // "icon" (thumbnails in a grid) or "list" (a compact row each) — see
  // animatic/media_view.js. ⚠ CSS ONLY, and it must stay that way: both views
  // are the SAME cards in the SAME order, so a drag means the same thing in
  // either and nothing has to be re-tested twice. Ignored unless `vertical`.
  view = "icon",
  // The Media pane supplies its own single "add assets" control, so the strip's
  // own add button and trailing add-card would be duplicates of it.
  showAdd = true,
  // ⚠ OFF WHEN SOMETHING ELSE IS ALREADY NAMING THIS LIST. The Media pane puts
  // the strip inside a collapsible "Frames" section whose header carries the
  // name and the count, so the strip's own head would be the same two things
  // again, one line below. The file input stays mounted either way — it is what
  // the add-card and `onAddFiles` use, not part of the heading.
  heading = true,
  // ⚠ WHERE EACH CLIP SITS IN THE WHOLE SEQUENCE, when `frames` is only a SLICE
  // of it — the Media pane shows the picture track in three sections (storyboard
  // panels, video, images) and hands each one its own subset.
  //
  // Everything a position means has to keep meaning it: the badge is the clip's
  // number IN THE VIDEO, not its number in this section, and a reorder or a drop
  // moves it within the SEQUENCE. So every index that leaves this component goes
  // through here first. Omitted — the whole strip — it is the identity, and this
  // file behaves exactly as it did.
  indexOf,
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

  // This section's index i → the sequence's index. The single door every number
  // going OUT of here has to walk through; see `indexOf` above.
  const seq = (i) =>
    indexOf && frames[i] ? indexOf(frames[i], i) : i;
  // …and the one for a position PAST the last card (an append), which has no
  // clip to ask about: the end of the sequence, or of this section.
  const seqEnd = () => (indexOf && frames.length ? seq(frames.length - 1) + 1 : frames.length);

  function handleDrop(e, index) {
    e.preventDefault();
    const at = index >= frames.length ? seqEnd() : seq(index);
    if (e.dataTransfer?.files?.length) {
      onAddFiles(sortFiles(e.dataTransfer.files), at);
    } else if (dragIndex !== null && dragIndex !== index) {
      onReorder(seq(dragIndex), at);
    }
    setDragIndex(null);
    setOverIndex(null);
  }

  return (
    <div
      className={`fs-wrap ${vertical ? "fs-vertical" : ""} ${
        vertical ? `fs-view-${view === "list" ? "list" : "icon"}` : ""
      }`}
    >
      {/* Hidden, and OUTSIDE the head on purpose: the head is optional, the
          picker is not — `fileRef` is what the add-card at the end of the strip
          opens. */}
      <input
        ref={fileRef}
        type="file"
        // Video too: `onAddFiles` is the editor's one-way-in `addAssets`, which
        // routes by kind, so refusing video HERE would be the picker
        // disagreeing with the drop target beside it.
        accept="image/*,video/*"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onAddFiles(sortFiles(e.target.files));
          e.target.value = "";
        }}
      />

      {heading && (
      <div className="fs-head">
        <h3 className="fs-title">
          Frames <span className="muted">({frames.length})</span>
        </h3>
        <div className="fs-head-actions">
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
                "＋ Add media"
              )}
            </button>
          )}
        </div>
      </div>
      )}

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
            /* ⚠ TWO THINGS ARE ON THE CLIPBOARD, and the second one is not
               data. `application/x-anim-asset` is the payload; the empty
               `…-image` / `…-video` marker beside it is there so a timeline
               lane can tell WHAT is being dragged during `dragover`, where
               `getData` is blank by design in every browser and only the type
               list can be read (see `dragKind` in Timeline.jsx). Without the
               marker a lane could not refuse a drop until after it happened.
               `dragIndex` still drives reordering INSIDE the strip — this adds
               a second place the same card can be dropped, it does not replace
               the first. */
            onDragStart={(e) => {
              setDragIndex(i);
              const kind = frameOrigin(f) === "video" ? "video" : "image";
              e.dataTransfer.effectAllowed = "move";
              e.dataTransfer.setData(
                "application/x-anim-asset",
                JSON.stringify({ kind: "frame", id: f.id })
              );
              e.dataTransfer.setData(`application/x-anim-${kind}`, "");
            }}
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
              {/* A COLOUR CARD HAS NO FILE, so it must never wait for one. It
                  shows itself instead — without this it sat on the loading
                  spinner for ever, because the url it was given can only 404. */}
              {(f.kind || "image") === "color" ? (
                <span
                  className="fs-swatch"
                  style={{ background: f.color || "#000000" }}
                />
              ) : urls[f.id] ? (
                <img src={urls[f.id]} alt={f.label || `Frame ${seq(i) + 1}`} />
              ) : (
                <span className="fs-thumb-wait" />
              )}
              {/* THE NUMBER IS ITS PLACE IN THE VIDEO, which is why it goes
                  through `seq`: in a section that lists only the video clips,
                  "1" for the thirty-second one would be a lie about the only
                  thing this badge is for. */}
              <span className="fs-num">{seq(i) + 1}</span>
              {/* What this clip IS, when it isn't the ordinary case. A video's
                  poster looks exactly like a still, so the badge is the only
                  thing separating them — and it carries the number the foot
                  below can't show: how much FOOTAGE runs inside the hold. */}
              {(f.kind || "image") === "video" && (
                <span className="fs-kind" title="Video clip">
                  ▶ {sourceSpanSeconds(f).toFixed(1)}s
                  {Number(f.speed) && Number(f.speed) !== 1
                    ? ` · ${Number(f.speed)}×`
                    : ""}
                </span>
              )}
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
                  <Icon name="copy" />
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
                  <Icon name="close" />
                </button>
              </span>
            </div>
            {/* Always present, even unnamed. In list view the name IS the row —
                a card that dropped its line there would be the only one an
                inch shorter than its neighbours, and the one you can't read. */}
            <div className="fs-label" title={f.label || ""}>
              {f.label || `Frame ${seq(i) + 1}`}
            </div>
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
              Add media
              <span className="muted"> or drop it here</span>
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
