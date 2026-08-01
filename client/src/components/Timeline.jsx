// Timeline.jsx — four layers, one shared clock:
//
//   🖼 Images — one bar per frame, right edge draggable to change the hold
//   T  Text   — caption clips with their OWN start and length: drag the body to
//               move, the right edge to stretch. A clip may start part-way
//               through a held image or run across a cut.
//   ◆  Shapes — the same kind of free-floating clip as text, for the shapes
//               drawn over the picture. Moved and stretched identically, which
//               is why both go through ONE drag implementation below.
//   ♪  Audio  — the waveform, so a clip edge can be dragged onto a beat
//
// Everything is measured in milliseconds — the same unit the exporter uses — so
// what you line up here is what gets encoded. Layer names live in a fixed gutter
// on the left; only the tracks scroll, so the labels never leave the screen.
import { useEffect, useRef, useState } from "react";
import Waveform from "./Waveform.jsx";
import Icon from "./Icon.jsx";
import { shapeCss } from "./Shapes.jsx";

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
  shapes = [],
  totalMs,
  // How much time the timeline SHOWS. Longer than `totalMs` whenever the audio
  // outlasts the frames — otherwise the ruler stopped at the last picture and
  // you couldn't scrub into the rest of your track.
  spanMs,
  timeMs,
  pxPerSec,
  selectedId,
  selectedTextId,
  selectedShapeId,
  // The audio LAYERS: one lane each, mixed on export. `audioUrls` is keyed by
  // upload_id so a lane can draw its own waveform.
  audioTracks = [],
  audioUrls = {},
  maxAudioTracks = 4,
  onToggleMute,
  onTrimTrack,
  selectedTrackId,
  onSelectTrack,
  onSelect,
  onSelectText,
  onSelectShape,
  onSeek,
  onResize,
  onTextChange,
  onShapeChange,
  onAddShape,
  // Every layer carries the same ＋ in its gutter row, so "add to this layer"
  // is one gesture wherever you are on the timeline.
  onAddImages,
  onAddText,
  onAddAudio,
  onAddLayer,
  onRemoveTrack,
  // The active tool (V/C/B/N/H/Z) changes what a click and an edge-drag DO.
  tool = "select",
  snapping = true,
  onSplitAt,
  onZoomAt,
  // Mark in / out, drawn as a band on the ruler. Null = not marked.
  markIn = null,
  markOut = null,
}) {
  // ⚠ Every drag below writes its result from `dragRef.current.latest`, NOT from
  // inside a `setDraft(current => …)` updater. React runs updater functions
  // during the render phase, so calling the parent's onChange in one is a
  // setState-in-render — it logs "Cannot update a component while rendering a
  // different component", and in StrictMode the updater runs twice, firing the
  // parent write twice. Keep the pattern: remember the value while moving, write
  // it on pointerup.
  const trackRef = useRef(null);
  const scrollRef = useRef(null); // the horizontal scroller, for the Hand tool
  // While an edge or a clip is being dragged we show a DRAFT, so things move
  // with the pointer without writing to the project on every mouse event.
  const [draft, setDraft] = useState(null); // { id, durationMs }
  // Text and shape clips behave identically, so ONE draft covers both; `kind`
  // says which list the change is written back to when the pointer comes up.
  const [clipDraft, setClipDraft] = useState(null); // { kind, id, startMs, durationMs }
  const [audioDraft, setAudioDraft] = useState(null); // { id, lengthMs }
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
  // How long a track plays: its trim, else the rest of the file after the offset.
  const trackLength = (a) => {
    if (audioDraft && audioDraft.id === a.upload_id) return audioDraft.lengthMs;
    const rest = Math.max(0, (a.duration_ms || 0) - (a.offset_ms || 0));
    return a.trim_ms ? Math.min(a.trim_ms, rest || a.trim_ms) : rest;
  };
  const clipBox = (c) =>
    clipDraft && clipDraft.id === c.id
      ? { start: clipDraft.startMs, duration: clipDraft.durationMs }
      : { start: c.start_ms, duration: c.duration_ms };

  // --- Snapping -----------------------------------------------------------
  // The times a dragged edge is drawn to: every cut, the playhead, the marks
  // and both ends. Within SNAP_PX of one, the edge takes it exactly — which is
  // the whole point, since "roughly on the cut" is what you were trying to avoid.
  const SNAP_PX = 8;
  const snapTargets = () => {
    const points = [0, span, timeMs];
    let clock = 0;
    for (const f of frames) {
      points.push(clock);
      clock += f.duration_ms || 0;
    }
    points.push(clock);
    for (const c of texts) points.push(c.start_ms, c.start_ms + c.duration_ms);
    for (const s of shapes) points.push(s.start_ms, s.start_ms + s.duration_ms);
    if (markIn !== null) points.push(markIn);
    if (markOut !== null) points.push(markOut);
    return points;
  };

  // `snapMs` is applied to a candidate time; with snapping off it only rounds
  // to the 100ms grid, which is what dragging always did.
  function snapMs(value, exclude = []) {
    const grid = Math.round(value / 100) * 100;
    if (!snapping) return grid;
    const within = (SNAP_PX / pxPerSec) * 1000;
    let best = null;
    let bestGap = within;
    for (const point of snapTargets()) {
      if (exclude.includes(point)) continue;
      const gap = Math.abs(point - value);
      if (gap <= bestGap) {
        best = point;
        bestGap = gap;
      }
    }
    return best === null ? grid : Math.round(best);
  }

  // --- Seeking ------------------------------------------------------------
  function msFromEvent(e) {
    const rect = trackRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
    // Clamped to the span as well as the rect: belt and braces against the
    // element ever being wider than the time it represents again.
    return Math.min(span, Math.round((x / pxPerSec) * 1000));
  }

  // What a plain click on a lane does depends on the tool. Only the selection
  // and edit tools scrub; the razor cuts, the zoom zooms, the hand scrolls.
  function startSeek(e) {
    if (e.button !== 0) return;
    if (tool === "razor") {
      e.preventDefault();
      onSplitAt?.(msFromEvent(e));
      return;
    }
    if (tool === "zoom") {
      e.preventDefault();
      onZoomAt?.(e.altKey ? -1 : 1);
      return;
    }
    if (tool === "hand") {
      e.preventDefault();
      const scroller = scrollRef.current;
      if (!scroller) return;
      const startX = e.clientX;
      const from = scroller.scrollLeft;
      const move = (ev) => {
        scroller.scrollLeft = from - (ev.clientX - startX);
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      return;
    }
    onSeek(msFromEvent(e));
    const move = (ev) => onSeek(msFromEvent(ev));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // --- Frame edge dragging (the edit point between two pictures) -----------
  // ROLLING (N) vs RIPPLE (B, and the default): a ripple moves the cut and
  // everything after it slides — the video gets longer or shorter. A rolling
  // edit gives to one frame exactly what it takes from the next, so the cut
  // moves but the video stays EXACTLY as long. That's the whole distinction,
  // and it's why rolling refuses at the last frame: there is nothing to absorb it.
  function startResize(e, frame, index) {
    e.stopPropagation();
    e.preventDefault();
    const next = frames[index + 1];
    const rolling = tool === "rolling" && Boolean(next);
    dragRef.current = {
      id: frame.id,
      startX: e.clientX,
      startMs: frame.duration_ms,
      // Where this cut sits, so the drag can snap against the other cuts.
      edgeMs: frames.slice(0, index + 1).reduce((sum, f) => sum + (f.duration_ms || 0), 0),
      rolling,
      nextId: rolling ? next.id : null,
      nextMs: rolling ? next.duration_ms : 0,
    };
    setDraft({ id: frame.id, durationMs: frame.duration_ms });
  }

  useEffect(() => {
    if (!draft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      // The cut is snapped (to another cut, the playhead, a clip edge), then
      // turned back into a duration — snapping a LENGTH would line the edge up
      // with nothing. Its own position is excluded or it would stick to itself.
      const cut = snapMs(d.edgeMs + deltaMs, [d.edgeMs]);
      let next = Math.max(MIN_MS, cut - (d.edgeMs - d.startMs));
      if (d.rolling) {
        // Never eat the whole of the next frame.
        next = Math.min(next, d.startMs + d.nextMs - MIN_MS);
      }
      d.latest = next;
      setDraft({ id: d.id, durationMs: next });
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setDraft(null);
      // No `latest` = pressed and released without moving, so nothing changed.
      if (d && d.latest !== undefined && d.latest !== d.startMs) {
        onResize(d.id, d.latest);
        if (d.rolling) {
          onResize(d.nextId, Math.max(MIN_MS, d.nextMs - (d.latest - d.startMs)));
        }
      }
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [draft, pxPerSec, onResize, tool]);

  // --- Free-floating clip move / resize (text AND shapes) -----------------
  // Both layers are the same thing on the timeline: a box with its own start
  // and length. One implementation, `kind` deciding where the result is written.
  function startClipDrag(e, clip, mode, kind) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    (kind === "shape" ? onSelectShape : onSelectText)(clip.id);
    dragRef.current = {
      kind,
      id: clip.id,
      mode,
      startX: e.clientX,
      startMs: clip.start_ms,
      durationMs: clip.duration_ms,
    };
    setClipDraft({ kind, id: clip.id, startMs: clip.start_ms, durationMs: clip.duration_ms });
  }

  useEffect(() => {
    if (!clipDraft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      // Both edges of the clip being dragged are excluded from the snap
      // targets, or it would stick to where it already is.
      const own = [d.startMs, d.startMs + d.durationMs];
      const next =
        d.mode === "move"
          ? {
              kind: d.kind,
              id: d.id,
              // Clamped at 0 so a clip can't be dragged off the front of the video.
              startMs: Math.max(0, snapMs(d.startMs + deltaMs, own)),
              durationMs: d.durationMs,
            }
          : {
              kind: d.kind,
              id: d.id,
              startMs: d.startMs,
              // The clip's END is what snaps to a cut; its length follows.
              durationMs: Math.max(
                MIN_MS,
                snapMs(d.startMs + d.durationMs + deltaMs, own) - d.startMs
              ),
            };
      d.latest = next;
      setClipDraft(next);
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setClipDraft(null);
      const moved =
        d?.latest &&
        (d.latest.startMs !== d.startMs || d.latest.durationMs !== d.durationMs);
      if (moved) {
        const write = d.kind === "shape" ? onShapeChange : onTextChange;
        write(d.id, { start_ms: d.latest.startMs, duration_ms: d.latest.durationMs });
      }
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [clipDraft, pxPerSec, onTextChange, onShapeChange]);

  function startAudioTrim(e, track) {
    e.stopPropagation();
    e.preventDefault();
    dragRef.current = { id: track.upload_id, startX: e.clientX, startMs: trackLength(track) };
    setAudioDraft({ id: track.upload_id, lengthMs: trackLength(track) });
  }

  useEffect(() => {
    if (!audioDraft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      const next = Math.max(MIN_MS, Math.round((d.startMs + deltaMs) / 100) * 100);
      d.latest = next;
      setAudioDraft({ id: d.id, lengthMs: next });
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setAudioDraft(null);
      if (d && d.latest !== undefined && d.latest !== d.startMs) {
        onTrimTrack(d.id, d.latest);
      }
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [audioDraft, pxPerSec, onTrimTrack]);

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
        <div
          className="tl-gutter-row tl-gutter-shapes"
          title="Shapes drawn over the picture, timed on their own"
        >
          <span className="tl-layer-ico">◆</span> Shapes
          <button
            type="button"
            className="tl-layer-add"
            onClick={onAddShape}
            title="Add a shape at the playhead"
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

      <div className="tl-scroll" ref={scrollRef}>
        <div className={`tl-inner tool-${tool}`} style={{ width }}>
          {/* Ruler — click or drag anywhere on it to scrub. */}
          <div className="tl-ruler" ref={trackRef} onPointerDown={startSeek}>
            {ticks.map((s) => (
              <span key={s} className="tl-tick" style={{ left: s * pxPerSec }}>
                {formatTime(s * 1000)}
              </span>
            ))}
            {/* The marked range (I / O). It bounds PLAYBACK — the export is
                still the whole timeline — so it is drawn on the ruler, which is
                the playback surface, and not over the clips. */}
            {(markIn !== null || markOut !== null) && (
              <span
                className="tl-marks"
                style={{
                  left: ((markIn ?? 0) / 1000) * pxPerSec,
                  width:
                    (((markOut ?? span) - (markIn ?? 0)) / 1000) * pxPerSec,
                }}
                title={`Plays ${formatTime(markIn ?? 0)} → ${formatTime(markOut ?? span)}`}
              />
            )}
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
                  onPointerDown={(e) => {
                    // The razor cuts wherever it is clicked ON the picture —
                    // that is the tool's whole behaviour, and it must beat
                    // "select this frame".
                    if (tool === "razor") {
                      e.preventDefault();
                      e.stopPropagation();
                      const rect = e.currentTarget.getBoundingClientRect();
                      const into = ((e.clientX - rect.left) / pxPerSec) * 1000;
                      onSplitAt?.(
                        frames
                          .slice(0, i)
                          .reduce((sum, x) => sum + (x.duration_ms || 0), 0) + into
                      );
                      return;
                    }
                    onSelect(f.id);
                  }}
                  title={`${f.label || `Frame ${i + 1}`} — ${(ms / 1000).toFixed(1)}s`}
                >
                  <span className="tl-bar-label">{w > 34 ? f.label || i + 1 : ""}</span>
                  {w > 56 && <span className="tl-bar-secs">{(ms / 1000).toFixed(1)}s</span>}
                  <span
                    className="tl-handle"
                    onPointerDown={(e) => startResize(e, f, i)}
                    title={
                      tool === "rolling"
                        ? "Rolling edit — drag the cut; the next frame absorbs it and the video stays the same length"
                        : "Drag to change how long this frame is held"
                    }
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
                  onPointerDown={(e) => startClipDrag(e, c, "move", "text")}
                  title={
                    (c.text || "Empty text") +
                    ` — ${(start / 1000).toFixed(1)}s for ${(duration / 1000).toFixed(1)}s` +
                    (overruns ? " (runs past the end of the video)" : "")
                  }
                >
                  <span className="tl-text-label">{c.text || "empty"}</span>
                  <span
                    className="tl-handle"
                    onPointerDown={(e) => startClipDrag(e, c, "resize", "text")}
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

          {/* Shape layer — the same free-floating clips as the text above, and
              dragged by the same code. Each clip carries a swatch of the shape
              in its own colour, so a lane of six is still readable. */}
          <div className="tl-shapes" onPointerDown={startSeek}>
            {shapes.map((s) => {
              const { start, duration } = clipBox(s);
              const left = (start / 1000) * pxPerSec;
              const w = Math.max(6, (duration / 1000) * pxPerSec);
              const overruns = start + duration > totalMs;
              return (
                <div
                  key={s.id}
                  className={[
                    "tl-shape",
                    selectedShapeId === s.id ? "sel" : "",
                    overruns ? "over-end" : "",
                  ].join(" ")}
                  style={{ left, width: w }}
                  onPointerDown={(e) => startClipDrag(e, s, "move", "shape")}
                  title={
                    `${s.kind} — ${(start / 1000).toFixed(1)}s for ${(duration / 1000).toFixed(1)}s` +
                    (overruns ? " (runs past the end of the video)" : "")
                  }
                >
                  <span
                    className="tl-shape-swatch"
                    style={{ background: s.color, ...shapeCss(s.kind) }}
                  />
                  {w > 44 && <span className="tl-shape-label">{s.kind}</span>}
                  <span
                    className="tl-handle"
                    onPointerDown={(e) => startClipDrag(e, s, "resize", "shape")}
                    title="Drag to change how long this shape stays on screen"
                  />
                </div>
              );
            })}
            {!shapes.length && (
              <button
                type="button"
                className="tl-track-empty tl-track-add"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={onAddShape}
              >
                ◆ No shapes yet — click to add one
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
                  // A CLIP, not a full-width band: it is as wide as the track
                  // actually plays, and its right edge drags to trim.
                  <div
                    className="tl-audio-clip"
                    style={{ width: (trackLength(track) / 1000) * pxPerSec }}
                  >
                    <Waveform
                      audioUrl={audioUrls[track.upload_id]}
                      width={Math.max(1, (trackLength(track) / 1000) * pxPerSec)}
                      /* Matches --tl-track-h less the track's borders, so a
                         waveform fills its lane exactly like the other tracks. */
                      height={38}
                      totalMs={trackLength(track)}
                      offsetMs={track.offset_ms || 0}
                    />
                    <span
                      className="tl-handle"
                      onPointerDown={(e) => startAudioTrim(e, track)}
                      title="Drag to trim how much of this track plays"
                    />
                  </div>
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
