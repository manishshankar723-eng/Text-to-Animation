// Timeline.jsx — three layers, one shared clock:
//
//   🖼 Images — one bar per frame, right edge draggable to change the hold
//   T  Text   — caption clips with their OWN start and length: drag the body to
//               move, the right edge to stretch. A clip may start part-way
//               through a held image or run across a cut.
//   ♪  Audio  — the waveform, so a clip edge can be dragged onto a beat
//
// Everything is measured in milliseconds — the same unit the exporter uses — so
// what you line up here is what gets encoded. Layer names live in a fixed gutter
// on the left; only the tracks scroll, so the labels never leave the screen.
import { useEffect, useRef, useState } from "react";
import Waveform from "./Waveform.jsx";
import Icon from "./Icon.jsx";

const MIN_MS = 100; // shortest hold / clip the backend accepts

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
  texts = [],
  totalMs,
  // How much time the timeline SHOWS. Longer than `totalMs` whenever the audio
  // outlasts the frames — otherwise the ruler stopped at the last picture and
  // you couldn't scrub into the rest of your track.
  spanMs,
  timeMs,
  pxPerSec,
  selectedId,
  selectedTextId,
  // The audio LAYERS: one lane each, mixed on export. `audioUrls` is keyed by
  // upload_id so a lane can draw its own waveform.
  audioTracks = [],
  audioUrls = {},
  maxAudioTracks = 4,
  onToggleMute,
  selectedTrackId,
  onSelectTrack,
  onSelect,
  onSelectText,
  onSeek,
  onResize,
  onTextChange,
  // Every layer carries the same ＋ in its gutter row, so "add to this layer"
  // is one gesture wherever you are on the timeline.
  onAddImages,
  onAddText,
  onAddAudio,
  onAddLayer,
  onRemoveTrack,
}) {
  const trackRef = useRef(null);
  // While an edge or a clip is being dragged we show a DRAFT, so things move
  // with the pointer without writing to the project on every mouse event.
  const [draft, setDraft] = useState(null); // { id, durationMs }
  const [textDraft, setTextDraft] = useState(null); // { id, startMs, durationMs }
  const dragRef = useRef(null);

  // Everything horizontal is measured against the SPAN, not the video length.
  const span = Math.max(totalMs, spanMs || 0);
  const width = Math.max(240, (span / 1000) * pxPerSec);
  const step = tickStep(pxPerSec);
  const ticks = [];
  for (let s = 0; s <= span / 1000; s += step) ticks.push(s);
  // NB: no "video ends" marker is drawn. The timeline still SPANS the audio —
  // that's what lets the playhead reach the end of a long track — but the line
  // and the hatching over the waveform were visual noise on the surface you
  // actually work on. The header already reports it ("audio 0:59 — video ends
  // early"), and so does the transport clock past that point.

  const durationOf = (f) => (draft && draft.id === f.id ? draft.durationMs : f.duration_ms);
  const clipBox = (c) =>
    textDraft && textDraft.id === c.id
      ? { start: textDraft.startMs, duration: textDraft.durationMs }
      : { start: c.start_ms, duration: c.duration_ms };

  // --- Seeking ------------------------------------------------------------
  function msFromEvent(e) {
    const rect = trackRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
    // Clamped to the span as well as the rect: belt and braces against the
    // element ever being wider than the time it represents again.
    return Math.min(span, Math.round((x / pxPerSec) * 1000));
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

  // --- Frame edge dragging ------------------------------------------------
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

  // --- Text clip move / resize -------------------------------------------
  function startTextDrag(e, clip, mode) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    onSelectText(clip.id);
    dragRef.current = {
      id: clip.id,
      mode,
      startX: e.clientX,
      startMs: clip.start_ms,
      durationMs: clip.duration_ms,
    };
    setTextDraft({ id: clip.id, startMs: clip.start_ms, durationMs: clip.duration_ms });
  }

  useEffect(() => {
    if (!textDraft) return;
    function move(e) {
      const d = dragRef.current;
      if (!d) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      const snap = (v) => Math.round(v / 100) * 100;
      if (d.mode === "move") {
        // Clamped at 0 so a clip can't be dragged off the front of the video.
        setTextDraft({
          id: d.id,
          startMs: Math.max(0, snap(d.startMs + deltaMs)),
          durationMs: d.durationMs,
        });
      } else {
        setTextDraft({
          id: d.id,
          startMs: d.startMs,
          durationMs: Math.max(MIN_MS, snap(d.durationMs + deltaMs)),
        });
      }
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setTextDraft((current) => {
        if (
          d &&
          current &&
          (current.startMs !== d.startMs || current.durationMs !== d.durationMs)
        ) {
          onTextChange(d.id, {
            start_ms: current.startMs,
            duration_ms: current.durationMs,
          });
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
  }, [textDraft, pxPerSec, onTextChange]);

  const playheadX = Math.max(0, Math.min(width, (timeMs / 1000) * pxPerSec));

  return (
    <div className="tl-wrap">
      {/* Layer names. Outside the scroller, so they stay put. */}
      <div className="tl-gutter">
        <div className="tl-gutter-ruler" />
        <div className="tl-gutter-row tl-gutter-images" title="Your frames, in order">
          <span className="tl-layer-ico">🖼</span> Images
          <button
            type="button"
            className="tl-layer-add"
            onClick={onAddImages}
            title="Add images to the end of the sequence"
          >
            ＋
          </button>
        </div>
        <div className="tl-gutter-row tl-gutter-text" title="On-screen text, timed on its own">
          <span className="tl-layer-ico">T</span> Text
          <button
            type="button"
            className="tl-layer-add"
            onClick={onAddText}
            title="Add a text clip at the playhead"
          >
            ＋
          </button>
        </div>
        {/* One row per audio track — music and a voiceover are separate layers,
            each with its own waveform and volume. */}
        {(audioTracks.length ? audioTracks : [null]).map((track, i) => (
          <div
            key={track ? track.upload_id : "empty"}
            className={`tl-gutter-row tl-gutter-audio ${
              track && selectedTrackId === track.upload_id ? "sel" : ""
            }`}
            title={track ? track.filename : "No audio yet"}
            onClick={() => track && onSelectTrack(track.upload_id)}
          >
            <span className="tl-layer-ico">♪</span>
            <span className="tl-layer-name">
              {track ? track.filename : "Audio"}
            </span>
            {track && (
              <>
                <button
                  type="button"
                  className={`tl-layer-mute ${track.muted ? "on" : ""}`}
                  onClick={() => onToggleMute(track.upload_id)}
                  title={track.muted ? "Unmute this track" : "Mute this track"}
                >
                  {track.muted ? "🔇" : "🔊"}
                </button>
                <button
                  type="button"
                  className="tl-layer-del"
                  onClick={() => onRemoveTrack(track.upload_id)}
                  title={`Remove ${track.filename}`}
                >
                  <Icon name="close" />
                </button>
              </>
            )}
          </div>
        ))}

        {/* Adds a whole LAYER rather than a clip. Audio is the only kind that
            can be stacked today; the menu is where video/overlay layers go when
            they exist. */}
        <button
          type="button"
          className="tl-add-layer"
          onClick={onAddLayer}
          title="Add a layer — pick what kind"
        >
          ＋ Add layer
        </button>
      </div>

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

          {/* Image layer. */}
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
                  <span className="tl-bar-label">{w > 34 ? f.label || i + 1 : ""}</span>
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

          {/* Text layer — absolutely positioned, because a clip's start is
              independent of the frames above it. */}
          <div className="tl-texts" onPointerDown={startSeek}>
            {texts.map((c) => {
              const { start, duration } = clipBox(c);
              const left = (start / 1000) * pxPerSec;
              const w = Math.max(6, (duration / 1000) * pxPerSec);
              const overruns = start + duration > totalMs;
              return (
                <div
                  key={c.id}
                  className={[
                    "tl-text",
                    selectedTextId === c.id ? "sel" : "",
                    overruns ? "over-end" : "",
                  ].join(" ")}
                  style={{ left, width: w }}
                  onPointerDown={(e) => startTextDrag(e, c, "move")}
                  title={
                    (c.text || "Empty text") +
                    ` — ${(start / 1000).toFixed(1)}s for ${(duration / 1000).toFixed(1)}s` +
                    (overruns ? " (runs past the end of the video)" : "")
                  }
                >
                  <span className="tl-text-label">{c.text || "empty"}</span>
                  <span
                    className="tl-handle"
                    onPointerDown={(e) => startTextDrag(e, c, "resize")}
                    title="Drag to change how long this text stays on screen"
                  />
                </div>
              );
            })}
            {!texts.length && (
              <button
                type="button"
                className="tl-track-empty tl-track-add"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={onAddText}
              >
                T No text yet — click to caption the shot at the playhead
              </button>
            )}
          </div>

          {/* Audio layers — one lane per track, mixed together on export. */}
          {audioTracks.length ? (
            audioTracks.map((track) => (
              <div
                key={track.upload_id}
                className={`tl-audio ${track.muted ? "muted" : ""} ${
                  selectedTrackId === track.upload_id ? "sel" : ""
                }`}
                onPointerDown={(e) => {
                  onSelectTrack(track.upload_id);
                  startSeek(e);
                }}
              >
                {audioUrls[track.upload_id] ? (
                  <Waveform
                    audioUrl={audioUrls[track.upload_id]}
                    width={width}
                    /* Matches --tl-track-h less the track's borders, so a
                       waveform fills its lane exactly like the other tracks. */
                    height={38}
                    totalMs={totalMs}
                    offsetMs={track.offset_ms || 0}
                  />
                ) : (
                  <div className="tl-track-empty">Loading {track.filename}…</div>
                )}
              </div>
            ))
          ) : (
            <div className="tl-audio" onPointerDown={startSeek}>
              {/* The empty band is the obvious place to reach for, so it opens
                  the picker itself. Nothing is lost: with no waveform there is
                  nothing here to scrub against, and the ruler still scrubs. */}
              <button
                type="button"
                className="tl-track-empty tl-track-add"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={onAddAudio}
              >
                ♪ No audio yet — click to add an MP3 to time against
              </button>
            </div>
          )}

          <div className="tl-playhead" style={{ left: playheadX }}>
            <span className="tl-playhead-grip" onPointerDown={startSeek} />
          </div>
        </div>
      </div>
    </div>
  );
}
