// Timeline.jsx — as many lanes as the project has, one shared clock.
//
// The lanes are NOT hard-coded here: the editor builds one `lanes` list and
// both the gutter labels and the tracks are generated from it, so a label can
// never end up beside the wrong lane. Kinds:
//
//   🖼 Images  — the picture sequence: one bar per frame, right edge draggable
//   🖼 Image   — an OVERLAY lane: pictures composited over the video
//   T  Text    — caption clips with their OWN start and length
//   ◆  Shapes  — boxes/circles/stars, likewise free-floating
//   ♪  Audio   — the waveform, so a clip edge can be dragged onto a beat
//
// Text, shape and overlay clips are the same object on a timeline, so they
// share `clipLane` and ONE drag implementation.
//
// Everything is measured in milliseconds — the same unit the exporter uses — so
// what you line up here is what gets encoded. Layer names live in a fixed gutter
// on the left; only the tracks scroll, so the labels never leave the screen.
import { useEffect, useRef, useState } from "react";
import Waveform from "./Waveform.jsx";
import { keysOf } from "../animatic/keyframes.js";
import { ANIMATABLE, frameSpans } from "../animatic/scene.js";
import {
  MAX_TRANSITION_MS,
  MIN_TRANSITION_MS,
  transitionWindow,
} from "../animatic/transitions.js";
import Icon from "./Icon.jsx";
import { shapeCss } from "./Shapes.jsx";

const MIN_MS = 100; // shortest hold / clip the backend accepts

// A lane kind → the name that kind goes by in the scene model, so a lane can
// ask which properties it is allowed to animate. Only the spelling differs.
const KEY_KIND = { frames: "frame", text: "text", shape: "shape", image: "overlay" };

// Per-lane chrome. Keyed by lane kind, so adding a kind is one entry here and
// one branch in `renderLane` — not another copy of the whole gutter.
const LANE_ICON = { frames: "🖼", image: "🖼", text: "T", shape: "◆", audio: "♪" };
const LANE_HINT = {
  frames: "Your frames, in order — this is the video",
  image: "Pictures composited OVER the video, timed on their own",
  text: "On-screen text, timed on its own",
  shape: "Shapes drawn over the picture, timed on their own",
  audio: "An audio track, mixed on export",
};
const LANE_ADD = {
  frames: "Add images to the end of the sequence",
  image: "Add a picture to this layer",
  text: "Add a text clip at the playhead",
  shape: "Add a shape at the playhead",
  audio: "Add an MP3 to this track",
};

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
  overlays = [],
  overlayUrls = {},
  // What happens on the cuts. Drawn as a badge straddling the edit point,
  // because that is exactly where a boundary-local transition happens.
  transitions = [],
  selectedTransitionId,
  onSelectTransition,
  onAddTransition,
  onTransitionChange,
  // Every row on the timeline, top to bottom, built by the editor. The gutter
  // and the tracks both render from this one list.
  lanes = [],
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
  selectedOverlayId,
  // `audioUrls` is keyed by upload_id so a lane can draw its own waveform.
  audioUrls = {},
  onToggleMute,
  onTrimTrack,
  selectedTrackId,
  onSelectTrack,
  onSelect,
  onSelectText,
  onSelectShape,
  onSelectOverlay,
  onSeek,
  onResize,
  onTextChange,
  onShapeChange,
  onOverlayChange,
  // Re-time one keyframe: (kind, clipId, fromT, toT), both times relative to
  // the clip. The editor owns `moveKey`; the timeline only reports the gesture.
  onKeyMove,
  // "put something on THIS lane" — the lane decides what that means.
  onAddToLane,
  onRemoveLayer,
  // Every layer carries the same ＋ in its gutter row, so "add to this layer"
  // is one gesture wherever you are on the timeline.
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
  // A keyframe diamond being dragged along its clip. { kind, id, from, to }
  const [keyDraft, setKeyDraft] = useState(null);
  // A transition badge being widened. { id, durationMs }
  const [trDraft, setTrDraft] = useState(null);
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
    for (const o of overlays) points.push(o.start_ms, o.start_ms + o.duration_ms);
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
    const select =
      kind === "shape" ? onSelectShape : kind === "image" ? onSelectOverlay : onSelectText;
    select(clip.id);
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

  // --- Keyframe diamonds --------------------------------------------------
  /**
   * One implementation for both lanes, because a key on a frame and a key on a
   * caption are the same thing — the frames lane just computes its clip start
   * from the running total instead of reading `start_ms`.
   *
   * A press that doesn't move SEEKS to the key; a press that does DRAGS it.
   * Deciding between them on pointerup rather than up front is what lets one
   * diamond do both without a modifier, and it is why a 3px slop exists: a
   * mouse always moves a pixel or two on the way to a click.
   *
   * A diamond now belongs to ONE property — the row it sits on — so a plain
   * drag re-times only that property. SHIFT-drag moves every property keyed at
   * that instant, which is how a Ken Burns push (`scale` and `x` keyed
   * together) is kept in step when you want to slide the whole move.
   */
  function startKeyDrag(e, item, t, clipStartMs, kind, prop) {
    if (e.button !== 0) return;
    // Beat the clip's own move-drag. Without this, aiming at a key nudges the
    // whole clip sideways and keys become impossible to hit accurately.
    e.stopPropagation();
    e.preventDefault();
    dragRef.current = {
      key: true,
      kind,
      id: item.id,
      prop,
      all: e.shiftKey,
      from: t,
      clipStartMs,
      startX: e.clientX,
      // Its own time is excluded from the snap targets, or it would stick to
      // where it already is — the same rule the clip edges follow.
      own: [clipStartMs + t],
    };
    setKeyDraft({ kind, id: item.id, prop, from: t, to: t });
  }

  useEffect(() => {
    if (!keyDraft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d?.key) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      // Snapping is done in TIMELINE time and converted back, so a key snaps to
      // cuts, the playhead and the marks like everything else — then stored
      // relative to its clip, which is where key times live.
      const absolute = snapMs(d.clipStartMs + d.from + deltaMs, d.own);
      const next = Math.round(absolute - d.clipStartMs);
      d.latest = next;
      setKeyDraft({ kind: d.kind, id: d.id, prop: d.prop, from: d.from, to: next });
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setKeyDraft(null);
      if (!d?.key) return;
      const moved = d.latest !== undefined && Math.abs(d.latest - d.from) > 3;
      if (moved) onKeyMove?.(d.kind, d.id, d.from, d.latest, d.prop, d.all);
      else onSeek(d.clipStartMs + d.from);
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [keyDraft, pxPerSec, onKeyMove, onSeek]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * The diamonds for one clip. Shared by the frames lane and the clip lanes.
   *
   * ⚠ ONE ROW PER ANIMATED PROPERTY, stacked down the clip — NOT one merged
   * diamond per instant, which is what this used to draw. Merging was chosen so
   * that a Ken Burns push (`scale` and `x` keyed together) wouldn't stack two
   * diamonds a pixel apart, but it hid the thing you most need to know: how
   * many keys are here, and on WHAT. Two keys at one instant looked exactly
   * like one, and a key next to a transition badge vanished into it.
   *
   * Rows follow `ANIMATABLE` order, which is the order the Properties pane
   * lists the same controls in — so the top row is the top control (Zoom, then
   * X, Y, Opacity for a frame) and the two panes read as one thing. Only
   * properties that ARE animated get a row, so an ordinary clip draws nothing
   * and a single fade draws one row, not four.
   */
  function renderKeys(item, kind, w, clipStartMs) {
    const animated = (ANIMATABLE[KEY_KIND[kind]] || []).filter(
      (prop) => keysOf(item, prop).length > 0
    );
    return animated.flatMap((prop, row) =>
      keysOf(item, prop).map((key) => {
        const t = key.t ?? 0;
        const dragging =
          keyDraft &&
          keyDraft.id === item.id &&
          keyDraft.prop === prop &&
          keyDraft.from === t;
        const shown = dragging ? keyDraft.to : t;
        const at = (shown / 1000) * pxPerSec;
        // A key can sit outside its clip — the value simply holds there — but
        // drawing it beyond the bar would put it on top of the neighbour.
        if (at < -4 || at > w + 4) return null;
        return (
          <span
            key={`${prop}:${t}`}
            className={`tl-key k-${prop} ${dragging ? "dragging" : ""}`}
            style={{
              left: at,
              top: `calc(var(--tl-key-top) + ${row} * var(--tl-key-row))`,
            }}
            title={
              `${prop} key at ${(shown / 1000).toFixed(2)}s into this clip — ` +
              "click to go there, drag to re-time it, " +
              "shift-drag to move every property keyed at this instant"
            }
            onPointerDown={(e) => startKeyDrag(e, item, t, clipStartMs, kind, prop)}
          />
        );
      })
    );
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
        const write =
          d.kind === "shape"
            ? onShapeChange
            : d.kind === "image"
              ? onOverlayChange
              : onTextChange;
        write(d.id, { start_ms: d.latest.startMs, duration_ms: d.latest.durationMs });
      }
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [clipDraft, pxPerSec, onTextChange, onShapeChange, onOverlayChange]);

  // --- Transitions on the cuts --------------------------------------------
  // A transition is BOUNDARY-LOCAL: it straddles the edit point, taking half
  // its length from the tail of one picture and half from the head of the next,
  // and moves nothing. So it is drawn centred on the cut — which is both what
  // it looks like and, unusually, exactly what it does.
  //
  // Dragging the handle grows it from the CENTRE, so `duration` changes by
  // twice the pointer's travel. Clamped to the shorter of the two holds,
  // because that is what the renderer will honour — a badge wider than the
  // transition it draws would be a lie about the video.
  function startTransitionDrag(e, transition, win) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    onSelectTransition?.(transition.id);
    dragRef.current = {
      transition: true,
      id: transition.id,
      startX: e.clientX,
      startMs: win.durationMs,
      maxMs: win.maxMs,
    };
    setTrDraft({ id: transition.id, durationMs: win.durationMs });
  }

  useEffect(() => {
    if (!trDraft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d?.transition) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      // ×2: the handle is on one edge but the badge grows from its centre, so
      // the pointer's travel is only half of what the length changes by.
      const snapped = Math.round((d.startMs + deltaMs * 2) / 50) * 50;
      d.latest = Math.max(MIN_TRANSITION_MS, Math.min(d.maxMs, snapped));
      setTrDraft({ id: d.id, durationMs: d.latest });
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setTrDraft(null);
      if (d?.transition && d.latest !== undefined && d.latest !== d.startMs) {
        onTransitionChange?.(d.id, { duration_ms: d.latest });
      }
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [trDraft, pxPerSec, onTransitionChange]);

  /** The badge on every internal cut — one per edit point, transition or not. */
  function renderTransitions() {
    // Built from the DRAFTED durations, not the saved ones, so a badge travels
    // with the cut while its frame's edge is being dragged instead of sitting
    // where the cut used to be until the pointer comes up.
    const drafted = frames.map((f) => ({ ...f, duration_ms: durationOf(f) }));
    const { spans } = frameSpans(drafted);
    const out = [];
    for (let i = 0; i < spans.length - 1; i++) {
      const cut = spans[i].end;
      const at = (cut / 1000) * pxPerSec;
      const transition = transitions.find((t) => t.after_frame_id === frames[i].id);

      if (!transition) {
        // An empty cut carries a ＋ so a transition can be added where it goes,
        // rather than from a menu somewhere else. It is faint until hovered —
        // a row of buttons down every edit point would drown the pictures.
        out.push(
          <button
            key={`add:${frames[i].id}`}
            type="button"
            className="tl-transition tl-tr-add"
            style={{ left: at }}
            title="Add a transition on this cut"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onAddTransition?.(frames[i].id)}
          >
            ＋
          </button>
        );
        continue;
      }

      const win = transitionWindow(drafted, spans, transition);
      if (!win) continue;
      const dragging = trDraft && trDraft.id === transition.id;
      // The cap the renderer applies, so the handle stops where the effect does.
      const maxMs = Math.min(
        MAX_TRANSITION_MS,
        Math.min(spans[i].end - spans[i].start, spans[i + 1].end - spans[i + 1].start)
      );
      const durationMs = dragging ? trDraft.durationMs : win.durationMs;
      const width = Math.max(10, (durationMs / 1000) * pxPerSec);
      out.push(
        <button
          key={transition.id}
          type="button"
          className={[
            "tl-transition",
            `tr-${win.kind}`,
            selectedTransitionId === transition.id ? "sel" : "",
            dragging ? "dragging" : "",
          ].join(" ")}
          style={{ left: at - width / 2, width }}
          title={`${win.kind} · ${(durationMs / 1000).toFixed(1)}s across this cut — drag the edge to change it`}
          onPointerDown={(e) => {
            e.stopPropagation();
            onSelectTransition?.(transition.id);
          }}
        >
          <span className="tl-tr-mark" />
          <span
            className="tl-tr-handle"
            title="Drag to change how long the transition lasts"
            onPointerDown={(e) => startTransitionDrag(e, transition, { ...win, maxMs })}
          />
        </button>
      );
    }
    return out;
  }

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

  // --- One lane ------------------------------------------------------------
  // Every row on the timeline goes through here, picked out of `lanes`. Text,
  // shapes and overlay pictures are the same free-floating clip drawn three
  // ways, so they share `clipLane` and one drag implementation.
  function clipLane(lane, items, className, body, emptyLabel) {
    return (
      <div
        key={lane.key}
        className={`tl-lane ${className}`}
        onPointerDown={startSeek}
        data-lane={lane.key}
      >
        {items.map((item) => {
          const { start, duration } = clipBox(item);
          const left = (start / 1000) * pxPerSec;
          const w = Math.max(6, (duration / 1000) * pxPerSec);
          const overruns = start + duration > totalMs;
          const selected =
            (lane.kind === "text" && selectedTextId === item.id) ||
            (lane.kind === "shape" && selectedShapeId === item.id) ||
            (lane.kind === "image" && selectedOverlayId === item.id);
          return (
            <div
              key={item.id}
              className={[
                `tl-${lane.kind === "image" ? "overlay" : lane.kind}`,
                selected ? "sel" : "",
                overruns ? "over-end" : "",
              ].join(" ")}
              style={{ left, width: w }}
              onPointerDown={(e) => startClipDrag(e, item, "move", lane.kind)}
              title={
                `${body.title(item)} — ${(start / 1000).toFixed(1)}s for ${(duration / 1000).toFixed(1)}s` +
                (overruns ? " (runs past the end of the video)" : "")
              }
            >
              {body.render(item, w)}
              {/* Keys are drawn ON the clip, because that is where they live —
                  their times are relative to it, so dragging the clip carries
                  them along and the diamonds move with it for free.
                  ONE DIAMOND PER INSTANT, not per property: a Ken Burns push
                  keyframes `scale` and `x` together, and two diamonds a pixel
                  apart would be unclickable. Which property is which is the
                  Properties pane's job. */}
              {renderKeys(item, lane.kind, w, start)}
              <span
                className="tl-handle"
                onPointerDown={(e) => startClipDrag(e, item, "resize", lane.kind)}
                title="Drag to change how long this stays on screen"
              />
            </div>
          );
        })}
        {!items.length && (
          <button
            type="button"
            className="tl-track-empty tl-track-add"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onAddToLane(lane)}
          >
            {emptyLabel}
          </button>
        )}
      </div>
    );
  }

  function renderLane(lane) {
    if (lane.kind === "frames") {
      return (
        <div className="tl-lane tl-bars" key={lane.key}>
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
                {/* A frame's own keys — a Ken Burns push lives here, so this is
                    the lane where they matter most. Times are relative to the
                    frame, so they ride along when its hold is re-timed. */}
                {renderKeys(
                  f,
                  "frames",
                  w,
                  frames.slice(0, i).reduce((sum, x) => sum + (x.duration_ms || 0), 0)
                )}
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
          {/* The cuts, drawn OVER the bars: a transition belongs to the edit
              point between two pictures, not to either of them. */}
          {renderTransitions()}
          {!frames.length && (
            <button
              type="button"
              className="tl-track-empty tl-track-add"
              onClick={() => onAddToLane(lane)}
            >
              🖼 No pictures yet — click to add some
            </button>
          )}
        </div>
      );
    }

    if (lane.kind === "text") {
      return clipLane(
        lane,
        texts.filter((c) => (c.layer_id || "") === lane.layerId),
        "tl-texts",
        {
          title: (c) => c.text || "Empty text",
          render: (c) => <span className="tl-text-label">{c.text || "empty"}</span>,
        },
        "T No text yet — click to caption the shot at the playhead"
      );
    }

    if (lane.kind === "shape") {
      return clipLane(
        lane,
        shapes.filter((s) => (s.layer_id || "") === lane.layerId),
        "tl-shapes",
        {
          title: (s) => s.kind,
          render: (s, w) => (
            <>
              <span
                className="tl-shape-swatch"
                style={{ background: s.color, ...shapeCss(s.kind) }}
              />
              {w > 44 && <span className="tl-shape-label">{s.kind}</span>}
            </>
          ),
        },
        "◆ No shapes yet — click to add one"
      );
    }

    if (lane.kind === "image") {
      return clipLane(
        lane,
        overlays.filter((o) => (o.layer_id || "") === lane.layerId),
        "tl-overlays",
        {
          title: () => "Picture",
          render: (o, w) => (
            <>
              {overlayUrls[o.upload_id] ? (
                <img className="tl-overlay-thumb" src={overlayUrls[o.upload_id]} alt="" />
              ) : (
                <span className="tl-overlay-thumb" />
              )}
              {w > 52 && <span className="tl-shape-label">picture</span>}
            </>
          ),
        },
        "🖼 Empty layer — click to add a picture over the video"
      );
    }

    // Audio: a waveform as wide as the track actually plays, or an empty band.
    const track = lane.track;
    if (!track) {
      return (
        <div className="tl-lane tl-audio" key={lane.key} onPointerDown={startSeek}>
          <button
            type="button"
            className="tl-track-empty tl-track-add"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onAddToLane(lane)}
          >
            ♪ No audio yet — click to add an MP3 to time against
          </button>
        </div>
      );
    }
    return (
      <div
        key={lane.key}
        className={`tl-lane tl-audio ${track.muted ? "muted" : ""} ${
          selectedTrackId === track.upload_id ? "sel" : ""
        }`}
        onPointerDown={(e) => {
          onSelectTrack(track.upload_id);
          startSeek(e);
        }}
      >
        {audioUrls[track.upload_id] ? (
          <div
            className="tl-audio-clip"
            style={{ width: (trackLength(track) / 1000) * pxPerSec }}
          >
            <Waveform
              audioUrl={audioUrls[track.upload_id]}
              width={Math.max(1, (trackLength(track) / 1000) * pxPerSec)}
              /* Matches --tl-track-h less the track's borders, so a waveform
                 fills its lane exactly like the other tracks. */
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
    );
  }

  return (
    <div className="tl-wrap">
      {/* Layer names. Outside the scroller, so they stay put.
          Generated from the SAME `lanes` list as the tracks, so a label can
          never end up beside the wrong lane — which is exactly what happened
          when the two were written out separately and matched by position. */}
      <div className="tl-gutter">
        <div className="tl-gutter-ruler" />
        {lanes.map((lane) => {
          const selected =
            lane.kind === "audio" && lane.track && selectedTrackId === lane.track.upload_id;
          return (
            <div
              key={lane.key}
              className={`tl-gutter-row tl-lane-row tl-gutter-${lane.kind} ${selected ? "sel" : ""}`}
              title={LANE_HINT[lane.kind]}
              onClick={() => lane.track && onSelectTrack(lane.track.upload_id)}
            >
              <span className="tl-layer-ico">{LANE_ICON[lane.kind]}</span>
              <span className="tl-layer-name">{lane.name}</span>
              {lane.kind === "audio" && lane.track && (
                <button
                  type="button"
                  className={`tl-layer-mute ${lane.track.muted ? "on" : ""}`}
                  onClick={() => onToggleMute(lane.track.upload_id)}
                  title={lane.track.muted ? "Unmute this track" : "Mute this track"}
                >
                  {lane.track.muted ? "🔇" : "🔊"}
                </button>
              )}
              <button
                type="button"
                className="tl-layer-add"
                onClick={() => onAddToLane(lane)}
                title={LANE_ADD[lane.kind]}
              >
                ＋
              </button>
              {/* A lane the user made can be removed; the default ones and the
                  picture sequence cannot — there would be nothing left. */}
              {(lane.removable || (lane.kind === "audio" && lane.track && !lane.layerId)) && (
                <button
                  type="button"
                  className="tl-layer-del"
                  onClick={() =>
                    lane.removable
                      ? onRemoveLayer(lane.layerId)
                      : onRemoveTrack(lane.track.upload_id)
                  }
                  title={lane.removable ? `Remove ${lane.name}` : `Remove ${lane.track.filename}`}
                >
                  <Icon name="close" />
                </button>
              )}
            </div>
          );
        })}

        {/* Adds a blank LANE, not a clip. What goes on it is chosen afterwards,
            with that lane's own ＋. */}
        <button
          type="button"
          className="tl-add-layer"
          onClick={onAddLayer}
          title="Add an empty layer — pick what kind"
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

          {lanes.map((lane) => renderLane(lane))}

          <div className="tl-playhead" style={{ left: playheadX }}>
            <span className="tl-playhead-grip" onPointerDown={startSeek} />
          </div>
        </div>
      </div>
    </div>
  );
}
