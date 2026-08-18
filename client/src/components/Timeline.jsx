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
//   ♪  Audio   — one or more CLIPS, each with a waveform, its beats, a grip at
//                every edge, and a fade grip at each top corner
//
// Text, shape and overlay clips are the same object on a timeline, so they
// share `clipLane` and ONE drag implementation.
//
// ⚠ AN AUDIO LANE HOLDS SEVERAL CLIPS NOW, and that is what let the razor cut
// audio in the middle. It used to hold exactly one track pinned to time zero,
// so the only edits available were pulling its two ends in — which is precisely
// why a pause in the middle of a take could not be taken out. A clip carries
// `start_ms` (where it sits) as well as `offset_ms` (how far into the file it
// reads), the razor sets both on the second half, and the piece between two
// cuts can then be deleted or dragged somewhere else. `animatic/audio_clips.js`
// owns that arithmetic; this file only draws it and reports the gesture.
//
// ⚠ MORE THAN ONE THING CAN BE SELECTED, on any lane or across all of them.
// Three gestures reach the same list (`animatic/selection.js`), and the timeline
// only ever REPORTS them — the editor owns the list:
//
//   drag the empty part of a lane → a rubber band; everything it touches is
//                                   selected (shift extends). A press that does
//                                   NOT travel still scrubs, as it always did.
//   shift-click a clip            → in if it was out, out if it was in
//   double-click a lane's label   → everything on that row, on screen or not
//
// Every selectable thing carries `data-sel="kind:id"` and the band hit-tests
// those nodes, so it needs no copy of each lane's geometry. Dragging any clip in
// a selection moves the WHOLE selection by that clip's snapped delta.
//
// Everything is measured in milliseconds — the same unit the exporter uses — so
// what you line up here is what gets encoded. Layer names live in a fixed gutter
// on the left; only the tracks scroll, so the labels never leave the screen.
//
// SCROLLING is Premiere's, not the browser's: a bar under the tracks and one
// down the right-hand side, each with a round grip at both ends that ZOOMS
// rather than scrolls (see ZoomScrollbar.jsx). The native scrollbars are hidden
// — wheel and trackpad still work, they just don't draw anything — and the two
// bars read the scroller's real geometry back out of the DOM, so nothing here
// has to predict what the browser is going to lay out.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import ZoomScrollbar from "./ZoomScrollbar.jsx";
import Waveform from "./Waveform.jsx";
import { fadeWindow, trackPlayMs } from "../animatic/audio_mix.js";
import { clipId, clipRoomMs, MIN_CLIP_MS } from "../animatic/audio_clips.js";
import {
  boxesOverlap,
  boxFromCorners,
  dragged,
  keySet,
  parseKey,
  selKey,
} from "../animatic/selection.js";
import { keysOf } from "../animatic/keyframes.js";
import { clamp } from "../animatic/util.js";
import { ANIMATABLE, frameOrigin, frameSpans } from "../animatic/scene.js";
import {
  MAX_TRANSITION_MS,
  MIN_TRANSITION_MS,
  transitionWindow,
} from "../animatic/transitions.js";
import Icon from "./Icon.jsx";
import { shapeCss } from "./Shapes.jsx";

const MIN_MS = 100; // shortest hold / clip the backend accepts

// How tall a lane is drawn, in rem — the VERTICAL bar's grips change it, which
// is what "zoom" means on an axis whose content is a stack of tracks rather
// than a stretch of time. The default matches `--tl-track-h` in the stylesheet;
// the floor still fits a waveform, the ceiling still fits on a laptop screen.
const DEFAULT_TRACK_H = 2.6;
const MIN_TRACK_H = 1.5;
const MAX_TRACK_H = 6;

// A lane kind → the name that kind goes by in the scene model, so a lane can
// ask which properties it is allowed to animate. Only the spelling differs.
const KEY_KIND = { frames: "frame", text: "text", shape: "shape", image: "overlay" };

// Per-lane chrome. Keyed by lane kind, so adding a kind is one entry here and
// one branch in `renderLane` — not another copy of the whole gutter.
//
// ⚠ A LANE MAY OVERRIDE ANY OF THE THREE with its own `icon` / `hint` / `add`.
// The captions lane is the one that does: it is an ordinary text lane in every
// way that matters to this file, and the only thing it needs is to say what it
// is in the gutter. A `kind` of its own would have meant a fourth branch in
// `renderLane` drawing exactly the same clips.
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

/**
 * Does this picture row draw that clip?
 *
 * The picture track is ONE sequence drawn as two rows — the stills and the
 * footage — and `lane.only` is which. The split is by ORIGIN (`frameOrigin`), not
 * by kind: by kind, every board shot that has been animated with Veo would jump
 * to the footage row and cut the board's sequence into islands. A row with no
 * `only` is the whole track, which is what every other workspace and every
 * project without footage draws.
 */
function laneShows(lane, frame) {
  if (!lane.only) return true;
  return (lane.only === "video") === (frameOrigin(frame) === "video");
}

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
  // upload_id → the decoded analysis (`animatic/beats.js`). Only `beats` is
  // read here — the times, in FILE ms, that the ticks are drawn at and that a
  // dragged edge snaps to.
  audioAnalyses = {},
  // (clip ids[], muted) — the gutter's speaker, which mutes the whole LANE:
  // after a cut a lane is several clips of one track, and muting "the track"
  // has to mean all of them.
  onToggleMute,
  // (clip id, patch) — every edit to one audio clip goes through here: a trim
  // at either end, a move along the timeline, a fade grip. ONE handler, because
  // trimming from the left writes three fields at once and a second entry point
  // for it would be a second place to get that wrong.
  onTrackChange,
  selectedTrackId,
  onSelectTrack,
  onSelect,
  onSelectText,
  onSelectShape,
  onSelectOverlay,
  // --- More than one thing at a time --------------------------------------
  // `selection` is the whole list (`animatic/selection.js`); the six
  // `selected*Id` props above are still the PRIMARY — what the Properties pane
  // is describing — and are what a single click sets. Everything drawn as
  // selected is drawn from `selection`, so one clip and forty look the same.
  selection = [],
  // (items, { add }) — the marquee finished, or a lane label was double-clicked.
  // `add` means shift was held: extend rather than replace.
  onSelectMany,
  // (kind, id) — shift-click on one clip: in if it was out, out if it was in.
  onToggleSelect,
  // (deltaMs) — a whole selection dragged along the timeline at once. Sent on
  // pointerup only, so a forty-clip move is one write and one undo.
  onMoveSelection,
  // The earliest start in the selection: how far LEFT a group move may go before
  // its first clip would be pushed off the front of the video. Given rather than
  // worked out here so the drag you see and the write that follows it stop at
  // exactly the same place — read `selectionFloorMs` in AnimaticEditor.jsx.
  selectionFloorMs = 0,
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
  // ✕ on a DEFAULT row — the row itself is structural and stays, so this empties
  // it. Separate from `onRemoveLayer` because they are different promises: one
  // takes the row away, the other leaves you a row to put things back on.
  onClearLane,
  // The eye. `(lane) => void`, and the editor decides what "off" means for that
  // row — see `toggleLaneHidden`. The lane carries the current state (`hidden`)
  // and its own token (`vis`); this file never works either out.
  onToggleHidden,
  // The active tool (V/C/B/N/H/Z) changes what a click and an edge-drag DO.
  tool = "select",
  snapping = true,
  onSplitAt,
  // (clip id, ms) — the razor on an AUDIO clip. Separate from `onSplitAt`,
  // which cuts the picture sequence: they are different lists and a cut in one
  // must never touch the other.
  onSplitAudioAt,
  onZoomAt,
  // Continuous zoom, for the scrollbar's grips: they ask for an exact
  // pixels-per-second, not for the next step up. The ＋/− buttons still step.
  onSetPxPerSec,
  minPxPerSec = 2,
  maxPxPerSec = 600,
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
  const scrollRef = useRef(null); // the scroller — both axes live in this one box
  const innerRef = useRef(null); // the content box the marquee is drawn inside
  const gutterRef = useRef(null); // the labels, moved by hand when it scrolls
  // While an edge or a clip is being dragged we show a DRAFT, so things move
  // with the pointer without writing to the project on every mouse event.
  const [draft, setDraft] = useState(null); // { id, durationMs }
  // Text and shape clips behave identically, so ONE draft covers both; `kind`
  // says which list the change is written back to when the pointer comes up.
  const [clipDraft, setClipDraft] = useState(null); // { kind, id, startMs, durationMs }
  // One audio clip being dragged. `mode` says by which edge — "move" slides the
  // whole clip, "end" pulls the right edge, "start" pulls the LEFT one, which is
  // the only drag here that changes three numbers at once (see `startAudioDrag`).
  const [audioDraft, setAudioDraft] = useState(null);
  // A fade grip being dragged along the top of an audio clip. { id, side, ms }
  const [fadeDraft, setFadeDraft] = useState(null);
  // A keyframe diamond being dragged along its clip. { kind, id, from, to }
  const [keyDraft, setKeyDraft] = useState(null);
  // A transition badge being widened. { id, durationMs }
  const [trDraft, setTrDraft] = useState(null);
  // The rubber band, while it is being dragged. { box (content px), keys (Set) }
  const [marquee, setMarquee] = useState(null);
  const dragRef = useRef(null);

  // Everything currently selected, as `"kind:id"`. One Set answers "is this
  // drawn selected" for every lane, so a picture, a caption and a piece of audio
  // are asked the same question in the same way.
  const selectedKeys = keySet(selection);
  const isSel = (kind, id) => selectedKeys.has(selKey(kind, id));
  // While the band is out, what it is currently over is shown as selected too —
  // otherwise you are dragging a rectangle and guessing what it has caught.
  const inBand = (kind, id) => Boolean(marquee?.keys.has(selKey(kind, id)));

  // Everything horizontal is measured against the SPAN, not the video length.
  const span = Math.max(totalMs, spanMs || 0);
  const width = Math.max(240, (span / 1000) * pxPerSec);

  // --- What the scroller is showing ---------------------------------------
  // The two bars draw a thumb out of these six numbers, and they are READ from
  // the DOM rather than worked out from the props: how much fits on screen is
  // the browser's business, and guessing it is how a scrollbar ends up claiming
  // you can see more of the timeline than you can.
  //
  // The ruler is `position: sticky`, so it is inside the scrollable content but
  // permanently occupies the top of the viewport. Both the content height and
  // the visible height therefore have its height taken off them, or the
  // vertical thumb would be short by one ruler and never reach the bottom.
  const [viewBox, setViewBox] = useState({ vw: 0, vh: 0, cw: 0, ch: 0, sl: 0, st: 0 });
  // Set by the vertical grips. Applied as `--tl-track-h`, which the gutter rows
  // and the tracks BOTH measure themselves against, so the two columns stay in
  // step at any height — that is the whole reason the variable exists.
  const [trackH, setTrackH] = useState(DEFAULT_TRACK_H);

  const readView = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    // The gutter sits OUTSIDE the scroller so the labels never slide away
    // sideways. The price is that it has to be moved by hand when the lanes
    // scroll DOWN — miss this and every label ends up beside the wrong track.
    if (gutterRef.current) {
      gutterRef.current.style.transform = `translateY(${-el.scrollTop}px)`;
    }
    const rulerH = trackRef.current?.offsetHeight || 0;
    const next = {
      vw: el.clientWidth,
      vh: Math.max(0, el.clientHeight - rulerH),
      cw: el.scrollWidth,
      ch: Math.max(0, el.scrollHeight - rulerH),
      sl: el.scrollLeft,
      st: el.scrollTop,
    };
    // Only on a real change: this runs on every scroll event, and a setState
    // that changes nothing still re-renders the whole timeline.
    setViewBox((prev) =>
      prev.vw === next.vw &&
      prev.vh === next.vh &&
      prev.cw === next.cw &&
      prev.ch === next.ch &&
      prev.sl === next.sl &&
      prev.st === next.st
        ? prev
        : next
    );
  }, []);

  // The pane is resizable (the workspace grid is fluid, and `~` maximizes it),
  // so the viewport's size changes without anything here being told.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(readView);
    ro.observe(el);
    return () => ro.disconnect();
  }, [readView]);

  // …and it changes when the content does: a new layer, a longer audio track, a
  // different zoom. Layout effect, so the bars are never a frame out of date.
  useLayoutEffect(readView, [readView, width, lanes.length, trackH, frames.length]);

  // --- Zooming from the bars ----------------------------------------------
  // A grip asks for a WINDOW — "show me from here to here" — as fractions of
  // the whole timeline. Fractions, not pixels, because they survive the zoom
  // that is about to be applied: the timeline still spans the same amount of
  // time afterwards, it is only drawn at a different scale.
  //
  // The new scroll position can't be set until that scale has been laid out, so
  // it is parked in a ref and applied in the layout effect below.
  const wantLeft = useRef(null);
  const wantTop = useRef(null);

  function zoomX(a, b) {
    const el = scrollRef.current;
    if (!el || !onSetPxPerSec) return;
    const seconds = Math.max(0.05, (b - a) * (span / 1000));
    const next = Math.min(maxPxPerSec, Math.max(minPxPerSec, el.clientWidth / seconds));
    if (Math.abs(next - pxPerSec) < 0.01) {
      // Already against the zoom stop. Pan to where the grip is pointing rather
      // than let the gesture do nothing at all.
      el.scrollLeft = a * el.scrollWidth;
      readView();
      return;
    }
    wantLeft.current = a;
    onSetPxPerSec(next);
  }

  function zoomY(a, b) {
    const el = scrollRef.current;
    if (!el) return;
    const rulerH = trackRef.current?.offsetHeight || 0;
    const viewH = Math.max(1, el.clientHeight - rulerH);
    const contentH = Math.max(1, el.scrollHeight - rulerH);
    // The gaps between lanes don't scale, so this is an approximation — but it
    // is applied live while the pointer is down, so it converges on what you
    // asked for as you drag.
    const wanted = Math.max(0.02, b - a) * contentH;
    const next = Math.min(
      MAX_TRACK_H,
      Math.max(MIN_TRACK_H, (trackH * viewH) / wanted)
    );
    if (Math.abs(next - trackH) < 0.005) {
      el.scrollTop = a * contentH;
      readView();
      return;
    }
    wantTop.current = a;
    setTrackH(next);
  }

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (wantLeft.current !== null) {
      el.scrollLeft = wantLeft.current * el.scrollWidth;
      wantLeft.current = null;
    }
    if (wantTop.current !== null) {
      const rulerH = trackRef.current?.offsetHeight || 0;
      el.scrollTop = wantTop.current * Math.max(1, el.scrollHeight - rulerH);
      wantTop.current = null;
    }
    readView();
  }, [pxPerSec, trackH, readView]);

  function panX(px) {
    const el = scrollRef.current;
    if (el) el.scrollLeft = px;
  }
  function panY(px) {
    const el = scrollRef.current;
    if (el) el.scrollTop = px;
  }
  const step = tickStep(pxPerSec);
  const ticks = [];
  for (let s = 0; s <= span / 1000; s += step) ticks.push(s);
  // NB: no "video ends" marker is drawn. The timeline still SPANS the audio —
  // that's what lets the playhead reach the end of a long track — but the line
  // and the hatching over the waveform were visual noise on the surface you
  // actually work on. The header already reports it ("audio 0:59 — video ends
  // early"), and so does the transport clock past that point.

  const durationOf = (f) => (draft && draft.id === f.id ? draft.durationMs : f.duration_ms);
  // The three numbers that place ONE audio clip, each showing the draft while
  // that clip is being dragged. Measured with NO total on purpose: the timeline
  // spans whatever the audio needs, so clamping a clip to the span here would
  // make it shrink as you dragged it toward the end.
  const trackLength = (a) =>
    audioDraft && audioDraft.id === clipId(a) ? audioDraft.lengthMs : trackPlayMs(a);
  const trackStart = (a) => {
    const id = clipId(a);
    if (audioDraft && audioDraft.id === id) return audioDraft.startMs;
    // Every other selected clip travels with the one being dragged, by the same
    // snapped delta — the picture-lane rule, applied to sound.
    if (audioDraft?.group && audioDraft.deltaMs && isSel("audio", id)) {
      return Math.max(0, Math.max(0, a.start_ms || 0) + audioDraft.deltaMs);
    }
    return Math.max(0, a.start_ms || 0);
  };
  const trackOffset = (a) =>
    audioDraft && audioDraft.id === clipId(a)
      ? audioDraft.offsetMs
      : Math.max(0, a.offset_ms || 0);
  // Where a text / shape / overlay clip is DRAWN. Three cases and the third is
  // the one that makes a group move look like one: the clip being dragged shows
  // its draft, every OTHER selected clip shows itself shifted by the same
  // delta, and everything else shows what is saved.
  const clipBox = (c, kind) => {
    if (clipDraft && clipDraft.id === c.id) {
      return { start: clipDraft.startMs, duration: clipDraft.durationMs };
    }
    if (clipDraft?.group && clipDraft.deltaMs && isSel(kind, c.id)) {
      return {
        start: Math.max(0, c.start_ms + clipDraft.deltaMs),
        duration: c.duration_ms,
      };
    }
    return { start: c.start_ms, duration: c.duration_ms };
  };

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
    // And every beat that has been found under the timeline. Drawing the ticks
    // without this would be decoration: "roughly on the beat" is precisely what
    // you were trying to avoid, and it is what you get by eye at any zoom.
    //
    // ⚠ Beats and clip edges are pushed in TIMELINE time, which since a clip can
    // sit anywhere means adding its start. Before that they were the same
    // number, and forgetting the shift makes every beat on a moved clip snap to
    // a point that is nowhere near the tick you can see.
    for (const lane of lanes) {
      if (lane.kind !== "audio") continue;
      for (const track of lane.tracks || []) {
        const start = trackStart(track);
        const length = trackLength(track);
        // The two ends of the clip itself: cutting a gap out means dragging the
        // next piece back onto the end of the last one, and by eye you always
        // leave a few milliseconds of silence behind.
        points.push(start, start + length);
        for (const beat of beatsOn(track, length)) points.push(start + beat.ms);
      }
    }
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

  // --- The rubber band ------------------------------------------------------
  // ⚠ EVERY CLIP CARRIES `data-sel="kind:id"`, and the marquee hit-test is a
  // query over those rather than a second copy of the timeline's geometry. Each
  // lane already knows how to place its own clips — a frame from a running
  // total, a caption from `start_ms`, an audio clip from `start_ms + offset` —
  // and writing that arithmetic out again here to work out what a rectangle
  // covers would be four more places for it to drift. The browser has already
  // laid the clips out; ask it where they are.
  function hitsIn(box) {
    const nodes = innerRef.current?.querySelectorAll("[data-sel]") || [];
    const items = [];
    for (const node of nodes) {
      const r = node.getBoundingClientRect();
      if (!boxesOverlap(box, { left: r.left, top: r.top, right: r.right, bottom: r.bottom })) {
        continue;
      }
      const item = parseKey(node.dataset.sel);
      if (item) items.push(item);
    }
    return items;
  }

  /**
   * Press on the empty part of a lane: a CLICK still scrubs, a DRAG selects.
   *
   * Deciding between the two on the way rather than up front is what lets one
   * gesture do both, and it is the same trick a keyframe diamond and an audio
   * clip already use. It matters here because dragging a lane used to scrub and
   * some of that muscle memory is worth keeping: a press that never travels
   * more than a few pixels is still a scrub, and the ruler still scrubs by drag
   * whatever happens. Past the slop it is a marquee, which is where every other
   * editor puts it.
   */
  function startMarquee(e) {
    const inner = innerRef.current;
    if (!inner) return false;
    const origin = { x: e.clientX, y: e.clientY };
    // Shift means "add to what is already selected", so a second sweep can pick
    // up a lane the first one missed.
    const add = e.shiftKey;
    let live = false;

    const rectOf = (ev) => boxFromCorners(origin.x, origin.y, ev.clientX, ev.clientY);
    // Client coordinates for the hit-test, content coordinates for the box we
    // draw — the band is a child of the scrolling content, so it has to be
    // measured against that content and not against the window.
    const toContent = (box) => {
      const r = inner.getBoundingClientRect();
      return {
        left: box.left - r.left,
        top: box.top - r.top,
        width: box.right - box.left,
        height: box.bottom - box.top,
      };
    };

    const move = (ev) => {
      if (!live && !dragged(origin.x, origin.y, ev.clientX, ev.clientY)) return;
      live = true;
      const box = rectOf(ev);
      setMarquee({
        box: toContent(box),
        keys: keySet(hitsIn(box)),
      });
    };
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setMarquee(null);
      if (!live) {
        // Never travelled: this was a click on the lane, which has always
        // meant "put the playhead here".
        onSeek(msFromEvent(ev));
        return;
      }
      onSelectMany?.(hitsIn(rectOf(ev)), { add });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return true;
  }

  // What a press does depends on the tool, and the three that aren't about
  // selecting mean the same thing wherever they land — so they are answered
  // once, here, for both the ruler and the lanes.
  function toolPress(e) {
    if (tool === "razor") {
      e.preventDefault();
      onSplitAt?.(msFromEvent(e));
      return true;
    }
    if (tool === "zoom") {
      e.preventDefault();
      onZoomAt?.(e.altKey ? -1 : 1);
      return true;
    }
    if (tool === "hand") {
      e.preventDefault();
      const scroller = scrollRef.current;
      if (!scroller) return true;
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
      return true;
    }
    return false;
  }

  /**
   * The RULER, and the playhead's grip: press and drag to scrub.
   *
   * ⚠ This is the surface that scrubs by dragging, and it must stay one — the
   * lanes below it now answer a drag with a rubber band (see `startLanePress`),
   * so if this ever stopped scrubbing there would be nowhere left to scrub from.
   */
  function startSeek(e) {
    if (e.button !== 0) return;
    if (toolPress(e)) return;
    onSeek(msFromEvent(e));
    const move = (ev) => onSeek(msFromEvent(ev));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  /**
   * A LANE's own background — everything not on a clip. Click scrubs, drag
   * selects.
   *
   * A press only reaches here when it landed on nothing: every clip stops the
   * event at itself, because a press on a clip is about that clip.
   */
  function startLanePress(e) {
    if (e.button !== 0) return;
    if (toolPress(e)) return;
    // ⚠ Or the browser starts a TEXT SELECTION under the band: the lanes are full
    // of labels, and a drag across them would leave half the timeline
    // highlighted in blue on top of the rectangle you were drawing.
    e.preventDefault();
    startMarquee(e);
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

  // The lane kinds and the SELECTION kinds are spelled differently in one place
  // only — a lane of pictures over the video is `image`, the thing on it is an
  // `overlay` — so the translation lives here rather than at four call sites.
  const SEL_KIND = { frames: "frame", text: "text", shape: "shape", image: "overlay" };

  // --- Free-floating clip move / resize (text AND shapes) -----------------
  // Both layers are the same thing on the timeline: a box with its own start
  // and length. One implementation, `kind` deciding where the result is written.
  function startClipDrag(e, clip, mode, kind) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const selKind = SEL_KIND[kind];
    // ⚠ SHIFT (or Ctrl) TOGGLES AND DRAGS NOTHING. Extending a selection is a
    // click, not a gesture: starting a move as well would nudge every clip in
    // the selection by however far the pointer drifted while you were aiming.
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      onToggleSelect?.(selKind, clip.id);
      return;
    }
    const select =
      kind === "shape" ? onSelectShape : kind === "image" ? onSelectOverlay : onSelectText;
    // A clip already IN the selection keeps it — that is what makes dragging a
    // selection possible at all. Pressing an unselected clip selects just it,
    // which is what a press on anything has always done.
    const inSelection = isSel(selKind, clip.id);
    if (!inSelection) select(clip.id);
    dragRef.current = {
      kind,
      id: clip.id,
      mode,
      startX: e.clientX,
      startMs: clip.start_ms,
      durationMs: clip.duration_ms,
      // Only a MOVE carries the rest of the selection. Dragging one clip's edge
      // is about that clip's length; stretching forty at once is not an edit
      // anybody means, and would need forty different clamps.
      group: mode === "move" && inSelection && selection.length > 1,
    };
    setClipDraft({
      kind,
      id: clip.id,
      startMs: clip.start_ms,
      durationMs: clip.duration_ms,
      deltaMs: 0,
      group: mode === "move" && inSelection && selection.length > 1,
    });
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
              // Clamped so a clip can't be dragged off the front of the video —
              // and on a GROUP move the wall is wherever the earliest clip in the
              // selection would hit 0:00, so the others keep their spacing
              // instead of piling up against it.
              startMs: Math.max(
                d.group ? d.startMs - selectionFloorMs : 0,
                snapMs(d.startMs + deltaMs, own)
              ),
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
      // ⚠ THE WHOLE SELECTION MOVES BY WHAT THE DRAGGED CLIP ACTUALLY MOVED —
      // its snapped delta, not the raw pointer travel. The clip under the
      // pointer is the one lining up with a cut; taking the delta from it is
      // what keeps the others' spacing exactly as it was, which is the one thing
      // a group move must never change.
      next.group = d.group;
      next.deltaMs = next.startMs - d.startMs;
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
      // ⚠ A CLICK ON A CLIP IN A SELECTION NARROWS THE SELECTION TO IT. The
      // press deliberately did NOT re-select (that is what lets a selection be
      // dragged), so without this there would be no way back to one clip except
      // by clicking empty space first — and clicking a clip would look broken.
      if (!moved && d?.group) {
        const select =
          d.kind === "shape"
            ? onSelectShape
            : d.kind === "image"
              ? onSelectOverlay
              : onSelectText;
        select(d.id);
        return;
      }
      if (moved) {
        // A group move is ONE call, not one per clip: forty separate writes
        // would be forty renders and forty steps to undo through.
        if (d.latest.group) {
          onMoveSelection?.(d.latest.deltaMs);
          return;
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipDraft, pxPerSec, onTextChange, onShapeChange, onOverlayChange, onMoveSelection]);

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

  /**
   * The badge on every internal cut — one per edit point, transition or not.
   *
   * `shows` is the picture row's filter (see the "frames" branch of `renderLane`):
   * a cut is drawn by whichever row holds the picture it comes AFTER, so the two
   * rows of one sequence divide the cuts between them instead of both drawing all
   * of them. Omitted — one row showing everything — every cut is drawn.
   */
  function renderTransitions(shows = null) {
    // Built from the DRAFTED durations, not the saved ones, so a badge travels
    // with the cut while its frame's edge is being dragged instead of sitting
    // where the cut used to be until the pointer comes up.
    const drafted = frames.map((f) => ({ ...f, duration_ms: durationOf(f) }));
    const { spans } = frameSpans(drafted);
    const out = [];
    for (let i = 0; i < spans.length - 1; i++) {
      if (shows && !shows(frames[i])) continue;
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

  // --- Dragging an audio clip ----------------------------------------------
  // Three gestures on one clip, and they are genuinely different edits:
  //
  //   move  — the clip goes somewhere else on the timeline. `start_ms` only:
  //           the same sound, later. This is what closes the gap a cut leaves.
  //   end   — the right edge. `trim_ms` only, exactly as it always did, and
  //           never past the end of the file (`clipRoomMs`).
  //   start — the LEFT edge, and the one that needs care: the clip starts later
  //           on the timeline, later in the FILE, and is shorter, all by the
  //           same amount. Move any two of those three and the audio slides
  //           under the edge you are dragging, which looks like the whole file
  //           shifting rather than an edge being trimmed.
  function startAudioDrag(e, track, mode) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const id = clipId(track);
    // As on a picture clip: one already in the selection keeps it, so a whole
    // selection can be dragged; pressing an unselected one selects just it.
    const inSelection = isSel("audio", id);
    if (!inSelection) onSelectTrack(id);
    const state = {
      id,
      mode,
      startMs: trackStart(track),
      lengthMs: trackLength(track),
      offsetMs: trackOffset(track),
      // Only a move takes the others along — trimming an edge is about this
      // clip's own window.
      group: mode === "move" && inSelection && selection.length > 1,
      deltaMs: 0,
    };
    dragRef.current = {
      ...state,
      audio: true,
      startX: e.clientX,
      // Where the press landed, so a press that never moves can SCRUB there.
      // The waveform is the surface you time pictures against, and clicking it
      // has always put the playhead where you clicked — a clip that only ever
      // moved would have taken that away. Decided on pointerup rather than up
      // front, exactly like a keyframe diamond (see `startKeyDrag`).
      pressMs: msFromEvent(e),
      // How much of the file is left beyond this clip — where the right edge
      // stops. Infinity when the file's length never reached us.
      roomMs: clipRoomMs(track),
    };
    setAudioDraft(state);
  }

  useEffect(() => {
    if (!audioDraft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d?.audio) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      // The clip's own two edges are excluded from the snap targets, or it
      // would stick to where it already is — the rule every drag here follows.
      const own = [d.startMs, d.startMs + d.lengthMs];
      let next;
      if (d.mode === "move") {
        next = {
          id: d.id,
          mode: d.mode,
          // The same wall as a picture clip's: on a group move it is where the
          // earliest clip in the selection reaches 0:00, not where this one does.
          startMs: Math.max(
            d.group ? d.startMs - selectionFloorMs : 0,
            snapMs(d.startMs + deltaMs, own)
          ),
          lengthMs: d.lengthMs,
          offsetMs: d.offsetMs,
        };
      } else if (d.mode === "start") {
        // Snapped in TIMELINE time — the left edge is what you are lining up
        // with a cut — and then turned back into how far the edge moved.
        const wanted = snapMs(d.startMs + deltaMs, own) - d.startMs;
        const shift = Math.max(
          -d.offsetMs, // never back beyond the head of the file
          Math.min(d.lengthMs - MIN_CLIP_MS, Math.round(wanted))
        );
        next = {
          id: d.id,
          mode: d.mode,
          startMs: Math.max(0, d.startMs + shift),
          lengthMs: d.lengthMs - shift,
          offsetMs: d.offsetMs + shift,
        };
      } else {
        const wanted = snapMs(d.startMs + d.lengthMs + deltaMs, own) - d.startMs;
        next = {
          id: d.id,
          mode: d.mode,
          startMs: d.startMs,
          lengthMs: Math.max(MIN_CLIP_MS, Math.min(d.roomMs, Math.round(wanted))),
          offsetMs: d.offsetMs,
        };
      }
      // What the rest of the selection travels by, when there is one — the
      // dragged clip's SNAPPED movement, so the spacing between the pieces is
      // exactly what it was. A trim moves nothing but itself.
      next.group = d.group && d.mode === "move";
      next.deltaMs = next.startMs - d.startMs;
      d.latest = next;
      setAudioDraft(next);
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setAudioDraft(null);
      if (!d?.audio) return;
      const moved =
        d.latest !== undefined &&
        (d.latest.startMs !== d.startMs ||
          d.latest.lengthMs !== d.lengthMs ||
          d.latest.offsetMs !== d.offsetMs);
      if (!moved) {
        // Pressed and released without moving. On the clip body that means
        // "scrub here"; on an edge grip it means nothing at all.
        if (d.mode === "move") {
          onSeek(d.pressMs);
          // …and, if it was one of several, the selection narrows to it — the
          // same rule the picture clips follow, and for the same reason.
          if (d.group) onSelectTrack(d.id);
        }
        return;
      }
      // Only the fields the gesture actually meant. Writing all three on a
      // plain move would stamp a `trim_ms` onto a clip that never had one, and
      // "plays the whole file" would silently become "plays this many ms".
      if (d.latest.group) onMoveSelection?.(d.latest.deltaMs);
      else if (d.mode === "move") onTrackChange(d.id, { start_ms: d.latest.startMs });
      else if (d.mode === "end") onTrackChange(d.id, { trim_ms: d.latest.lengthMs });
      else
        onTrackChange(d.id, {
          start_ms: d.latest.startMs,
          offset_ms: d.latest.offsetMs,
          trim_ms: d.latest.lengthMs,
        });
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [audioDraft, pxPerSec, onTrackChange]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Fades ---------------------------------------------------------------
  // A grip at each top corner of an audio clip, dragged inward. The wedge it
  // leaves behind is the ramp, drawn from `fadeWindow` — the same function the
  // exporter's twin uses to place `afade`, so the shape on the clip is the
  // shape in the MP4 rather than a decoration that means roughly that.
  function fadeOf(track, lengthMs) {
    const drag = fadeDraft && fadeDraft.id === clipId(track) ? fadeDraft : null;
    return fadeWindow({
      // Drawn against the length ON SCREEN, so a fade keeps its place while the
      // clip's right edge is being dragged.
      trim_ms: Math.max(MIN_MS, lengthMs),
      fade_in_ms: drag && drag.side === "in" ? drag.ms : track.fade_in_ms || 0,
      fade_out_ms: drag && drag.side === "out" ? drag.ms : track.fade_out_ms || 0,
    });
  }

  function startFadeDrag(e, track, side, lengthMs) {
    e.stopPropagation();
    e.preventDefault();
    // ⚠ SELECTS THE CLIP TOO. The grip stops the event reaching the clip body,
    // so without this, grabbing a fade left the Properties pane describing
    // whatever was selected before — you would be shaping one clip's ramp while
    // reading another clip's numbers.
    onSelectTrack(clipId(track));
    const window_ = fadeOf(track, lengthMs);
    const from = side === "in" ? window_.inMs : window_.outMs;
    dragRef.current = {
      id: clipId(track),
      side,
      startX: e.clientX,
      startMs: from,
      // A fade can reach the far end of the clip but not past the other fade —
      // two that crossed would cancel each other, which is why `fadeWindow`
      // scales them rather than letting it happen.
      maxMs: Math.max(0, lengthMs - (side === "in" ? window_.outMs : window_.inMs)),
    };
    setFadeDraft({ id: track.upload_id, side, ms: from });
  }

  useEffect(() => {
    if (!fadeDraft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d) return;
      // The right-hand grip is dragged LEFT to lengthen its fade, so its delta
      // is negated. Everything else about the two is identical.
      const delta = ((e.clientX - d.startX) / pxPerSec) * 1000 * (d.side === "in" ? 1 : -1);
      const next = Math.max(0, Math.min(d.maxMs, Math.round((d.startMs + delta) / 50) * 50));
      d.latest = next;
      setFadeDraft({ id: d.id, side: d.side, ms: next });
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setFadeDraft(null);
      if (d && d.latest !== undefined && d.latest !== d.startMs) {
        onTrackChange?.(d.id, { [d.side === "in" ? "fade_in_ms" : "fade_out_ms"]: d.latest });
      }
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [fadeDraft, pxPerSec, onTrackChange]);

  // --- Beats ---------------------------------------------------------------
  // Onsets arrive in FILE time; a clip shows the file from `offset_ms`, so this
  // is the same shift the waveform above them makes. Ticks closer together than
  // a few pixels are dropped rather than drawn as a smear — at a zoomed-out
  // view a four-minute track's beats are hundreds of DOM nodes nobody can see.
  function beatsOn(track, lengthMs) {
    // Keyed by the UPLOAD, not the clip: the analysis is of the FILE, and two
    // clips cut from one track are two windows onto the same list of onsets.
    const found = audioAnalyses[track.upload_id]?.beats;
    if (!found || !found.length || lengthMs <= 0) return [];
    const offset = trackOffset(track);
    const out = [];
    let lastX = -99;
    for (const at of found) {
      const ms = at - offset;
      if (ms < 0) continue;
      if (ms > lengthMs) break;
      const x = (ms / 1000) * pxPerSec;
      if (x - lastX < 4) continue;
      lastX = x;
      out.push({ ms, x });
      if (out.length >= 400) break;
    }
    return out;
  }

  const playheadX = Math.max(0, Math.min(width, (timeMs / 1000) * pxPerSec));

  /**
   * How much is on one row — the number the ✕'s tooltip promises to delete.
   *
   * Counted HERE and not on the lane, because a lane is built in the editor from
   * `layers` alone: giving it a count would mean rebuilding every lane on every
   * keystroke in a caption. This file already holds the clips.
   */
  function laneCount(lane) {
    if (lane.kind === "audio") return (lane.tracks || []).length;
    if (lane.kind === "frames") return frames.filter((f) => laneShows(lane, f)).length;
    const of = (list) => list.filter((c) => (c.layer_id || "") === lane.layerId).length;
    if (lane.kind === "text") return of(texts);
    if (lane.kind === "shape") return of(shapes);
    if (lane.kind === "image") return of(overlays);
    return 0;
  }

  /** Everything on one lane, whether or not it is on screen. */
  function selectLane(lane) {
    const of = (list) => list.filter((c) => (c.layer_id || "") === lane.layerId);
    let items = [];
    // ⚠ WHAT THIS ROW SHOWS, not the whole sequence — the picture track is drawn
    // as two rows (`only`), and "select everything on this row" that reached
    // across both would be the one gesture that can't tell them apart.
    if (lane.kind === "frames") {
      items = frames
        .filter((f) => laneShows(lane, f))
        .map((f) => ({ kind: "frame", id: f.id }));
    }
    else if (lane.kind === "text") items = of(texts).map((c) => ({ kind: "text", id: c.id }));
    else if (lane.kind === "shape") items = of(shapes).map((s) => ({ kind: "shape", id: s.id }));
    else if (lane.kind === "image") {
      items = of(overlays).map((o) => ({ kind: "overlay", id: o.id }));
    } else if (lane.kind === "audio") {
      items = (lane.tracks || []).map((t) => ({ kind: "audio", id: clipId(t) }));
    }
    onSelectMany?.(items, { add: false, lane: lane.name });
  }

  // --- One lane ------------------------------------------------------------
  // Every row on the timeline goes through here, picked out of `lanes`. Text,
  // shapes and overlay pictures are the same free-floating clip drawn three
  // ways, so they share `clipLane` and one drag implementation.
  function clipLane(lane, items, className, body, emptyLabel) {
    return (
      <div
        key={lane.key}
        className={`tl-lane ${className} ${lane.hidden ? "off" : ""}`}
        onPointerDown={startLanePress}
        data-lane={lane.key}
      >
        {items.map((item) => {
          const kind = SEL_KIND[lane.kind];
          const { start, duration } = clipBox(item, kind);
          const left = (start / 1000) * pxPerSec;
          const w = Math.max(6, (duration / 1000) * pxPerSec);
          const overruns = start + duration > totalMs;
          const selected = isSel(kind, item.id);
          const grouped = Boolean(item.group_id);
          return (
            <div
              key={item.id}
              /* ⚠ THE MARQUEE FINDS CLIPS BY THIS ATTRIBUTE. Every selectable
                 thing on the timeline carries `data-sel`, and the rubber band
                 hit-tests those nodes rather than re-deriving where each lane
                 puts its clips — see `hitsIn`. */
              data-sel={selKey(kind, item.id)}
              className={[
                `tl-${lane.kind === "image" ? "overlay" : lane.kind}`,
                selected ? "sel" : "",
                inBand(kind, item.id) ? "banded" : "",
                grouped ? "grp" : "",
                overruns ? "over-end" : "",
              ].join(" ")}
              style={{ left, width: w }}
              onPointerDown={(e) => startClipDrag(e, item, "move", lane.kind)}
              title={
                `${body.title(item)} — ${(start / 1000).toFixed(1)}s for ${(duration / 1000).toFixed(1)}s` +
                (overruns ? " (runs past the end of the video)" : "") +
                (grouped ? " · grouped — selecting it selects the others" : "") +
                "\nShift-click to add it to the selection; drag the empty part of a lane to select several."
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
      // ⚠ PLACED BY TIME, NOT BY FLOW, and this is load-bearing. The bars used
      // to be a flex row that added themselves up, so anything that made ONE bar
      // wider than the time it represents — a `min-width`, or a padding that a
      // border-box cannot shrink past — pushed every picture after it to the
      // right. The lane then no longer lined up with the ruler above it, it grew
      // wider than the timeline's own width, and the last shots ran off the end
      // of the pane where no amount of scrolling could reach them: you had to
      // zoom out to see your own sequence.
      //
      // With each bar at an absolute `left` taken from the running total, a
      // minimum width can only ever overlap its neighbour by a pixel or two —
      // it can no longer move it. The picture lane and the clock cannot drift.
      //
      // ⚠ THE CLOCK RUNS OVER THE WHOLE SEQUENCE, WHATEVER THE ROW SHOWS. The
      // picture track is drawn as two rows — the stills and the footage (`only`,
      // set in the editor's `lanes`) — and they are ONE track: the clips play in
      // order, so a bar's place is decided by every clip before it and not by the
      // ones this row happens to draw. Advance the clock first, skip second. Skip
      // first and the row would pack its bars up against the start and claim the
      // footage plays at 0:00.
      let clock = 0;
      const shows = (f) => laneShows(lane, f);
      return (
        <div
          className={`tl-lane tl-bars ${lane.hidden ? "off" : ""}`}
          key={lane.key}
          onPointerDown={startLanePress}
        >
          {frames.map((f, i) => {
            const ms = durationOf(f);
            const w = (ms / 1000) * pxPerSec;
            const left = (clock / 1000) * pxPerSec;
            clock += ms;
            if (!shows(f)) return null;
            return (
              <div
                key={f.id}
                data-sel={selKey("frame", f.id)}
                className={[
                  "tl-bar",
                  isSel("frame", f.id) ? "sel" : "",
                  inBand("frame", f.id) ? "banded" : "",
                ].join(" ")}
                style={{ left, width: w }}
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
                  // A press on a picture is about that picture — it must not
                  // also start a rubber band on the lane underneath.
                  e.stopPropagation();
                  if (e.shiftKey || e.ctrlKey || e.metaKey) {
                    onToggleSelect?.("frame", f.id);
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
              point between two pictures, not to either of them.
              ⚠ ON THE ROW THAT OWNS THE OUTGOING PICTURE, so a cut is drawn once
              and on the row you would reach for it. Every cut on both rows would
              put a ＋ where nothing joins, and a dissolve you could drag from two
              places. */}
          {renderTransitions(shows)}
          {/* The empty state belongs to the row that can be filled from nothing.
              The Video row only exists when there IS footage. */}
          {!frames.length && !lane.only && (
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
        lane.empty || "T No text yet — click to caption the shot at the playhead"
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

    // Audio: one positioned clip per piece of the track, or an empty band.
    const clips = lane.tracks || [];
    if (!clips.length) {
      return (
        <div className="tl-lane tl-audio" key={lane.key} onPointerDown={startLanePress}>
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
      <div key={lane.key} className="tl-lane tl-audio" onPointerDown={startLanePress}>
        {clips.map((track) => {
          const id = clipId(track);
          if (!audioUrls[track.upload_id]) {
            return (
              <div
                key={id}
                className="tl-audio-clip loading"
                style={{ left: (trackStart(track) / 1000) * pxPerSec, width: 160 }}
              >
                <span className="tl-track-empty">Loading {track.filename}…</span>
              </div>
            );
          }
          const lengthMs = trackLength(track);
          const left = (trackStart(track) / 1000) * pxPerSec;
          const clipW = Math.max(1, (lengthMs / 1000) * pxPerSec);
          const fade = fadeOf(track, lengthMs);
          const inW = (fade.inMs / 1000) * pxPerSec;
          const outW = (fade.outMs / 1000) * pxPerSec;
          return (
            <div
              key={id}
              data-sel={selKey("audio", id)}
              className={[
                "tl-audio-clip",
                track.muted ? "muted" : "",
                isSel("audio", id) ? "sel" : "",
                inBand("audio", id) ? "banded" : "",
                track.group_id ? "grp" : "",
                audioDraft && audioDraft.id === id ? "dragging" : "",
              ].join(" ")}
              style={{ left, width: clipW }}
              title={`${track.filename} — ${(trackStart(track) / 1000).toFixed(1)}s for ${(lengthMs / 1000).toFixed(1)}s. Drag to move it; the razor (C) cuts it where you click. Shift-click to add it to the selection.`}
              onPointerDown={(e) => {
                // ⚠ THE RAZOR BEATS THE DRAG, and it has to: with the tool
                // selected, a press on a clip means "cut here", and starting a
                // move instead would nudge the clip a pixel and cut nothing.
                if (tool === "razor") {
                  e.preventDefault();
                  e.stopPropagation();
                  onSplitAudioAt?.(id, msFromEvent(e));
                  return;
                }
                // Only the selection tool moves a clip. The others (hand, zoom)
                // still mean what they mean everywhere else on the timeline.
                if (tool !== "select" && tool !== "ripple" && tool !== "rolling") return;
                if (e.shiftKey || e.ctrlKey || e.metaKey) {
                  e.preventDefault();
                  e.stopPropagation();
                  onToggleSelect?.("audio", id);
                  return;
                }
                startAudioDrag(e, track, "move");
              }}
            >
              <Waveform
                audioUrl={audioUrls[track.upload_id]}
                width={clipW}
                /* Matches --tl-track-h less the track's borders, so a waveform
                   fills its lane exactly like the other tracks. A canvas can't be
                   sized in rem, so the current track height is converted here —
                   which also means the waveform grows when the vertical scroll
                   bar's grips make the tracks taller. */
                height={Math.max(12, Math.round(trackH * 16) - 4)}
                totalMs={lengthMs}
                offsetMs={trackOffset(track)}
              />
              {/* Beats, under the waveform they were found in. */}
              {beatsOn(track, lengthMs).map((beat) => (
                <span key={beat.ms} className="tl-beat" style={{ left: beat.x }} />
              ))}
              {/* The two ramps, and the grips that set them. The wedge is
                  drawn even at zero so the grip has something to sit on —
                  which is how you discover the handle is there at all. */}
              <span className="tl-fade tl-fade-in" style={{ width: inW }} />
              <span className="tl-fade tl-fade-out" style={{ width: outW }} />
              {/* Kept inside the clip, which has to clip its waveform: a grip
                  centred on a fade of zero would be half off the left edge
                  and entirely off the right one — so the handle you need in
                  order to MAKE a fade would be the one you couldn't grab. */}
              <span
                className="tl-fade-grip in"
                style={{ left: clamp(inW - 5, 0, Math.max(0, clipW - 10)) }}
                onPointerDown={(e) => startFadeDrag(e, track, "in", lengthMs)}
                title={`Fade in — ${(fade.inMs / 1000).toFixed(1)}s. Drag to change it.`}
              />
              <span
                className="tl-fade-grip out"
                style={{ left: clamp(clipW - outW - 5, 0, Math.max(0, clipW - 10)) }}
                onPointerDown={(e) => startFadeDrag(e, track, "out", lengthMs)}
                title={`Fade out — ${(fade.outMs / 1000).toFixed(1)}s. Drag to change it.`}
              />
              {/* A grip at BOTH ends now. The left one is what lets you tidy up
                  the head of a piece the razor left behind, without dragging
                  the whole clip and losing its place. */}
              <span
                className="tl-handle tl-handle-l"
                onPointerDown={(e) => startAudioDrag(e, track, "start")}
                title="Drag to trim the head of this clip — it stays where it is on the timeline"
              />
              <span
                className="tl-handle"
                onPointerDown={(e) => startAudioDrag(e, track, "end")}
                title="Drag to trim how much of this clip plays"
              />
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="tl-wrap" style={{ "--tl-track-h": `${trackH}rem` }}>
      {/* Adds a blank LANE, not a clip — what goes on it is chosen afterwards,
          with that lane's own ＋.

          ABOVE the stack rather than under it. Below, it sat past
          the last lane — off the bottom of a project with a few layers, so the
          way to add one was reachable only by scrolling to the end of what you
          already had, and it moved every time you added something.

          ⚠ IT IS OUTSIDE `.tl-cols` ON PURPOSE, and must stay there. The gutter
          labels and the tracks line up because both columns start at the same
          y — anything added INSIDE the gutter would push its labels down while
          the tracks stayed put, which is the misalignment the LANES block in
          animatic-lanes.css warns about. Here it shifts both columns equally,
          so they cannot drift apart. Its width matches the gutter, which is
          what makes it read as the head of the layer column. */}
      <div className="tl-head">
        <button
          type="button"
          className="tl-add-layer"
          onClick={onAddLayer}
          title="Add an empty layer — pick what kind"
        >
          ＋ Add layer
        </button>
      </div>

      <div className="tl-cols">
        {/* Layer names. Outside the scroller, so they stay put.
            Generated from the SAME `lanes` list as the tracks, so a label can
            never end up beside the wrong lane — which is exactly what happened
            when the two were written out separately and matched by position. */}
        <div
          className="tl-gutter"
          /* The labels are not a scroller themselves, so a wheel over them
             would otherwise do nothing at all — it is handed to the tracks,
             which is the only thing it could have meant. */
          onWheel={(e) => {
            const el = scrollRef.current;
            if (el) el.scrollTop += e.deltaY;
          }}
        >
          {/* Lines the labels up with the ruler, which is pinned to the top of
              the scroller. Outside the clipped part, so it stays there. */}
          <div className="tl-gutter-ruler" />
          {/* Everything below the ruler spacer scrolls DOWN with the tracks —
              `readView` moves it — and is clipped here rather than overflowing
              the pane. */}
          <div className="tl-gutter-clip">
            <div className="tl-gutter-rows" ref={gutterRef}>
            {lanes.map((lane) => {
              // A lane is one TRACK, which since the razor may be several
              // clips. The gutter speaks for all of them: its speaker mutes the
              // whole row and its ✕ removes the whole row, because "this track"
              // is what the label names — the pieces are an edit inside it.
              const clips = lane.kind === "audio" ? lane.tracks || [] : [];
              const ids = clips.map(clipId);
              const muted = clips.length > 0 && clips.every((t) => t.muted);
              const selected = ids.includes(selectedTrackId);
              // A DEFAULT row can only be emptied, never removed — see
              // `clearLane` in AnimaticEditor. `count` is what its ✕ would take.
              const clearable = !lane.removable && lane.kind !== "audio";
              const count = clearable ? laneCount(lane) : 0;
              return (
                <div
                  key={lane.key}
                  className={`tl-gutter-row tl-lane-row tl-gutter-${lane.kind} ${
                    selected ? "sel" : ""
                  } ${lane.hidden ? "off" : ""}`}
                  title={
                    (lane.hint || LANE_HINT[lane.kind]) +
                    "\nDouble-click to select everything on this row."
                  }
                  onClick={() => ids.length && onSelectTrack(ids[0])}
                  /* ⚠ THE SHORTEST WAY TO "SELECT ALL OF THESE AND DELETE THEM",
                     which is what a row of forty auto-captions needs. A marquee
                     can do it too, but only if the whole row fits on screen —
                     this cannot miss the clips that are scrolled off the end. */
                  onDoubleClick={() => selectLane(lane)}
                >
                  <span className="tl-layer-ico">{lane.icon || LANE_ICON[lane.kind]}</span>
                  <span className="tl-layer-name">{lane.name}</span>
                  {/* TURN THE ROW OFF. Audio has had its 🔇 since there were
                      tracks to mute, and every other row had nothing — so the way
                      to check a shot without its captions was to delete them.
                      This is that speaker for the rows you can SEE, and it means
                      the same thing: the row stays exactly where it is, and
                      nothing on it is drawn — in the monitor OR in the export.
                      ⚠ IT MUST AFFECT THE EXPORT. An eye that only dimmed the
                      preview would be a switch that lies at the one moment it
                      matters. See `hidden_lanes` in server/schemas.py.
                      Audio keeps the speaker instead: two controls for one idea
                      on the same row would be a choice nobody asked for. */}
                  {lane.kind === "audio"
                    ? clips.length > 0 && (
                        <button
                          type="button"
                          className={`tl-layer-mute ${muted ? "on" : ""}`}
                          onClick={() => onToggleMute(ids, !muted)}
                          title={muted ? "Unmute this track" : "Mute this track"}
                        >
                          {muted ? "🔇" : "🔊"}
                        </button>
                      )
                    : lane.vis && (
                        <button
                          type="button"
                          className={`tl-layer-mute ${lane.hidden ? "on" : ""}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onToggleHidden?.(lane);
                          }}
                          title={
                            lane.hidden
                              ? `Show ${lane.name} again — it is left out of the video while it is off`
                              : `Hide ${lane.name} — it stays on the timeline but is left out of the monitor and the video`
                          }
                          aria-pressed={!!lane.hidden}
                        >
                          <Icon name={lane.hidden ? "eye-off" : "eye"} />
                        </button>
                      )}
                  <button
                    type="button"
                    className="tl-layer-add"
                    onClick={() => onAddToLane(lane)}
                    title={lane.add || LANE_ADD[lane.kind]}
                  >
                    ＋
                  </button>
                  {/* ✕ ON EVERY ROW THAT HAS ANYTHING ON IT, which the default
                      rows did not: text, shapes, images and video could only be
                      emptied clip by clip, or with a marquee that misses whatever
                      is scrolled off the end. What it does differs by what the
                      row IS, and the tooltip says which:
                        · a lane you added      — the row goes, contents and all
                        · an audio file         — that track goes
                        · a default row         — the row stays (it is structural)
                                                  and everything on it is deleted
                      A default row with nothing on it has no ✕: there would be
                      nothing for it to do. */}
                  {(lane.removable || (clips.length > 0 && !lane.layerId) || count > 0) && (
                    <button
                      type="button"
                      className="tl-layer-del"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (lane.removable) onRemoveLayer(lane.layerId);
                        else if (clips.length) onRemoveTrack(ids);
                        else onClearLane?.(lane);
                      }}
                      title={
                        lane.removable
                          ? `Remove ${lane.name}`
                          : clips.length
                            ? `Remove ${clips[0].filename}`
                            : `Delete everything on ${lane.name} (${count})`
                      }
                    >
                      <Icon name="close" />
                    </button>
                  )}
                </div>
              );
            })}
            </div>
          </div>
        </div>

        <div className="tl-scroll" ref={scrollRef} onScroll={readView}>
          <div className={`tl-inner tool-${tool}`} style={{ width }} ref={innerRef}>
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

            {/* The rubber band. Drawn inside the scrolling content and never
                catching the pointer itself — it is a picture of the gesture,
                not part of it. */}
            {marquee && (
              <div
                className="tl-marquee"
                style={{
                  left: marquee.box.left,
                  top: marquee.box.top,
                  width: marquee.box.width,
                  height: marquee.box.height,
                }}
              >
                <span className="tl-marquee-count">{marquee.keys.size}</span>
              </div>
            )}

            <div className="tl-playhead" style={{ left: playheadX }}>
              <span className="tl-playhead-grip" onPointerDown={startSeek} />
            </div>
          </div>
        </div>

        {/* Down the right-hand side: the same bar stood on its end. It scrolls
            the stack of lanes, and its grips make every track taller or
            shorter — the vertical equivalent of zooming, since a lane's height
            is the only thing there is to zoom on this axis. */}
        <ZoomScrollbar
          orientation="vertical"
          total={viewBox.ch}
          view={viewBox.vh}
          pos={viewBox.st}
          onPan={panY}
          onZoom={zoomY}
          label="Tracks"
        />
      </div>

      {/* Along the bottom, under the labels as well as the tracks — it moves
          the whole timeline, not just the part of it beside the gutter. */}
      <ZoomScrollbar
        orientation="horizontal"
        total={viewBox.cw}
        view={viewBox.vw}
        pos={viewBox.sl}
        onPan={panX}
        onZoom={zoomX}
        label="Timeline"
      />
    </div>
  );
}
