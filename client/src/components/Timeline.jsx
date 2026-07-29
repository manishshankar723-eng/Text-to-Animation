// Timeline.jsx — the proportional track: frame bars, the audio waveform under
// them, a ruler and a draggable playhead.
//
// Two ways to change a hold live here (typing the number is the third, on the
// frame strip): drag a bar's right edge, or drag the playhead onto a beat and
// read the time. Everything is measured in milliseconds — the same unit the
// exporter uses — so what you line up here is what gets encoded.
import { useEffect, useRef, useState } from "react";
import Waveform from "./Waveform.jsx";

const MIN_MS = 100; // shortest hold the backend accepts

// Ruler spacing: the first step that leaves at least ~70px between labels.
const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300];

function tickStep(pxPerSec) {
  return TICK_STEPS.find((s) => s * pxPerSec >= 70) || TICK_STEPS[TICK_STEPS.length - 1];
}

export function formatTime(ms) {
  const total = Math.max(0, ms) / 1000;
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function Timeline({
  frames,
  totalMs,
  timeMs,
  pxPerSec,
  selectedId,
  audioUrl,
  audioOffsetMs = 0,
  onSelect,
  onSeek,
  onResize,
}) {
  const trackRef = useRef(null);
  // While a bar edge is being dragged we show a DRAFT duration, so the bars move
  // with the pointer without writing to the project on every mouse event.
  const [draft, setDraft] = useState(null); // { id, durationMs }
  const dragRef = useRef(null);

  const width = Math.max(240, (totalMs / 1000) * pxPerSec);
  const step = tickStep(pxPerSec);
  const ticks = [];
  for (let s = 0; s <= totalMs / 1000; s += step) ticks.push(s);

  const durationOf = (f) => (draft && draft.id === f.id ? draft.durationMs : f.duration_ms);

  // --- Seeking ------------------------------------------------------------
  function msFromEvent(e) {
    const rect = trackRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
    return Math.round((x / pxPerSec) * 1000);
  }

  function startSeek(e) {
    if (e.button !== 0) return;
    onSeek(msFromEvent(e));
    const move = (ev) => onSeek(msFromEvent(ev));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // --- Edge dragging ------------------------------------------------------
  function startResize(e, frame) {
    e.stopPropagation();
    e.preventDefault();
    dragRef.current = { id: frame.id, startX: e.clientX, startMs: frame.duration_ms };
    setDraft({ id: frame.id, durationMs: frame.duration_ms });
  }

  useEffect(() => {
    if (!draft) return;
    function move(e) {
      const d = dragRef.current;
      if (!d) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      // Snap to 100ms so a dragged hold is still a number a person can reason
      // about ("2.4s", not "2.437s").
      const next = Math.max(MIN_MS, Math.round((d.startMs + deltaMs) / 100) * 100);
      setDraft({ id: d.id, durationMs: next });
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setDraft((current) => {
        if (d && current && current.durationMs !== d.startMs) {
          onResize(d.id, current.durationMs);
        }
        return null;
      });
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [draft, pxPerSec, onResize]);

  const playheadX = Math.max(0, Math.min(width, (timeMs / 1000) * pxPerSec));

  return (
    <div className="tl-scroll">
      <div className="tl-inner" style={{ width }}>
        {/* Ruler — click or drag anywhere on it to scrub. */}
        <div className="tl-ruler" ref={trackRef} onPointerDown={startSeek}>
          {ticks.map((s) => (
            <span key={s} className="tl-tick" style={{ left: s * pxPerSec }}>
              {formatTime(s * 1000)}
            </span>
          ))}
        </div>

        {/* Frame bars. */}
        <div className="tl-bars">
          {frames.map((f, i) => {
            const ms = durationOf(f);
            const w = (ms / 1000) * pxPerSec;
            return (
              <div
                key={f.id}
                className={`tl-bar ${selectedId === f.id ? "sel" : ""}`}
                style={{ width: w }}
                onPointerDown={() => onSelect(f.id)}
                title={`${f.label || `Frame ${i + 1}`} — ${(ms / 1000).toFixed(1)}s`}
              >
                <span className="tl-bar-label">
                  {w > 34 ? f.label || i + 1 : ""}
                </span>
                {w > 56 && <span className="tl-bar-secs">{(ms / 1000).toFixed(1)}s</span>}
                <span
                  className="tl-handle"
                  onPointerDown={(e) => startResize(e, f)}
                  title="Drag to change how long this frame is held"
                />
              </div>
            );
          })}
        </div>

        {/* Audio. */}
        <div className="tl-audio" onPointerDown={startSeek}>
          {audioUrl ? (
            <Waveform
              audioUrl={audioUrl}
              width={width}
              height={48}
              totalMs={totalMs}
              offsetMs={audioOffsetMs}
            />
          ) : (
            <div className="tl-audio-empty">♪ No audio yet — add an MP3 to time against it</div>
          )}
        </div>

        <div className="tl-playhead" style={{ left: playheadX }}>
          <span className="tl-playhead-grip" onPointerDown={startSeek} />
        </div>
      </div>
    </div>
  );
}
