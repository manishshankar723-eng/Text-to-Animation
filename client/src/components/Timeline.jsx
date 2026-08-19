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
// ⚠ A MOVE DRAG HAS A VERTICAL HALF: drag a clip onto ANOTHER ROW of the same
// kind and it goes there, at the time you dragged it to. Captions, shapes,
// overlay pictures and audio clips all take it. The row under the pointer is
// found by asking the DOM — every lane carries `data-lane` — for the same reason
// the marquee does: the browser has already laid the rows out, and a second copy
// of their vertical geometry here would be wrong for the whole of a vertical
// zoom. What a row MEANS to a clip is the editor's business, so this file reports
// the row (`onMoveToLane`) and writes no ids of its own.
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
import { fadeCurve, fadeWindow, trackPlayMs } from "../animatic/audio_mix.js";
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
import {
  ANIMATABLE,
  clipKind,
  DEFAULT_SPEED,
  frameOrigin,
  frameSpans,
} from "../animatic/scene.js";
import {
  MAX_TRANSITION_MS,
  MIN_TRANSITION_MS,
  transitionWindow,
} from "../animatic/transitions.js";
import { trimKeyframesHead, trimTimedClipStart } from "../animatic/razor.js";
import Icon from "./Icon.jsx";
import { shapeCss } from "./Shapes.jsx";

const MIN_MS = 100; // shortest hold / clip the backend accepts
// Narrower than this and a clip has no room for a grip at each end: two 8px
// strips plus something left in the middle to press for "select" and drag for
// "move". Below it only the TAIL grip is drawn — which is exactly how every clip
// behaved before the head one existed, so a bar too small to trim from both ends
// is never a bar you can no longer grab at all.
const BOTH_GRIPS_MIN_PX = 24;

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

export function formatTime(ms) {
  const total = Math.max(0, ms) / 1000;
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// The frame rate the ruler counts in when the project hasn't said. Only the
// ruler reads it: every duration on this bar is still milliseconds, because that
// is what the clips are stored in, and rounding them to frames here would be a
// second opinion about where a cut is.
const DEFAULT_FPS = 24;

/**
 * The ladder of tick spacings, in FRAMES, coarsest last.
 *
 * ⚠ THE SUB-SECOND STEPS ARE THE DIVISORS OF fps, and that is the whole reason
 * this is computed rather than a constant list. A ruler stepping every 10 frames
 * at 24fps would label 0, 10, 20, then 30 — which is 1:06, so the run of labels
 * stops rolling over to `:00` at the next second and the bar reads as though the
 * clock were wrong. Every divisor divides the second exactly, so it cannot.
 *
 * Above a second the steps are the familiar 1, 2, 5, 10, 15, 30, 60… seconds,
 * expressed in frames so one comparison covers both halves of the ladder.
 */
function tickSteps(fps) {
  const divisors = [];
  for (let n = 1; n < fps; n += 1) if (fps % n === 0) divisors.push(n);
  return [...divisors, ...[1, 2, 5, 10, 15, 30, 60, 120, 300, 600].map((s) => s * fps)];
}

// A labelled tick needs room for HH:MM:SS:FF without touching its neighbour; a
// bare one only needs to be distinguishable from the next.
const MAJOR_MIN_PX = 78;
const MINOR_MIN_PX = 7;

/**
 * The ticks to draw, IN THE VISIBLE WINDOW ONLY.
 *
 * ⚠ CULLED, AND THAT IS NOT AN OPTIMISATION — it is what makes the ruler usable.
 * The ruler is `position: sticky` and re-renders on every scrub, so at full zoom
 * a 70-second cut asked the browser to lay out 1,681 nodes sixty times a second.
 * `from` / `to` are PIXELS (the scroller's window, grown by one label's width so
 * a tick never pops in at the edge), and only the ticks inside them are built.
 *
 * A MAJOR tick carries the timecode; the minors between it are bare. The minor
 * step is the coarsest exact division of the major that still leaves room, so
 * the two ladders can never drift out of phase with each other.
 */
function rulerTicks(pxPerSec, spanMs, fps, from, to) {
  const perFrame = pxPerSec / fps;
  const steps = tickSteps(fps);
  const major = steps.find((s) => s * perFrame >= MAJOR_MIN_PX) ?? steps[steps.length - 1];
  let minor = major;
  for (const by of [10, 8, 6, 5, 4, 3, 2]) {
    if (major % by === 0 && (major / by) * perFrame >= MINOR_MIN_PX) {
      minor = major / by;
      break;
    }
  }
  const last = Math.ceil((spanMs / 1000) * fps);
  const first = Math.max(0, Math.floor(from / perFrame / minor) * minor);
  const out = [];
  for (let n = first; n <= last && n * perFrame <= to; n += minor) {
    out.push({ n, x: n * perFrame, major: n % major === 0 });
  }
  return out;
}

/** One frame number as HH:MM:SS:FF — what a labelled tick says. */
function timecodeOfFrame(n, fps) {
  const pad = (v) => String(v).padStart(2, "0");
  const secs = Math.floor(n / fps);
  return `${pad(Math.floor(secs / 3600))}:${pad(Math.floor(secs / 60) % 60)}:${pad(
    secs % 60
  )}:${pad(n % fps)}`;
}

/** The same, from milliseconds — for anything outside this file that wants it. */
export function formatTimecode(ms, fps = DEFAULT_FPS) {
  const rate = Math.max(1, Math.round(fps) || DEFAULT_FPS);
  return timecodeOfFrame(Math.max(0, Math.round((Math.max(0, ms) / 1000) * rate)), rate);
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
  // (kind, id) — a click on a clip's ƒx badge. The editor selects that clip and
  // opens the Effects section of the Properties pane, which is where a chain is
  // MANAGED; the timeline only ever says "this one has effects on it".
  onManageEffects,
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
  // The project's frame rate — what the ruler's timecode counts in. Only the
  // ruler reads it; every duration on this bar is still milliseconds, because
  // that is what the clips are stored in and rounding them to frames here would
  // be a second opinion about where a cut is.
  fps = DEFAULT_FPS,
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
  // (id, patch) — a frame edit that is NOT just its length. The head trim of the
  // FIRST picture is the one caller: it writes `duration_ms` AND `in_ms`
  // together, because skipping the front of a video clip has to move the source
  // window as well or the trim throws away timeline and keeps the same footage.
  // Falls back to `onResize` when it isn't given, so the length still lands.
  onFrameChange,
  onTextChange,
  onShapeChange,
  onOverlayChange,
  /**
   * A clip was dragged onto ANOTHER ROW: `(kind, id, lane, patch)`.
   *
   * ⚠ THE ROW, NOT A LAYER ID, and that is deliberate. For a caption, a shape
   * or an overlay picture a row IS a `layer_id` and this file could have written
   * one — but an audio row grouped by FILE has no id to write, so "put it on
   * that row" can only be answered where the document is. Same division as
   * `onDropAsset`: the timeline works out WHICH ROW and WHAT TIME, the editor
   * works out what that means (see `moveClipToLane` in AnimaticEditor.jsx).
   *
   * Optional: without it a move stays on its own row and only the time is
   * written, which is what every move did before this existed.
   */
  onMoveToLane,
  // Re-time one keyframe: (kind, clipId, fromT, toT), both times relative to
  // the clip. The editor owns `moveKey`; the timeline only reports the gesture.
  onKeyMove,
  // "put something on THIS lane" — the lane decides what that means.
  onAddToLane,
  onRemoveLayer,
  // Every layer carries the same ＋ in its gutter row, so "add to this layer"
  // is one gesture wherever you are on the timeline.
  onAddLayer,
  // Something was dragged out of the Media pane (or off the desktop) and
  // dropped ON A LANE: `({ lane, atMs, asset, files })`. The timeline works out
  // WHERE — which row, and what time under the pointer — and the editor works
  // out WHAT that means for the thing being dropped, because only it knows what
  // an asset is. Optional: without it no lane accepts a drop and nothing about
  // the timeline changes.
  onDropAsset,
  // The editor's OTHER "make something" buttons — Text, Colour card, Voiceover
  // — handed in as a node and rendered beside ＋ Add layer.
  // ⚠ THEY ARE THE EDITOR'S, NOT THE TIMELINE'S: what they make and what they
  // cost is the editor's business (the voiceover one spends quota), so this
  // file only gives them a place to stand. They used to sit at the far right of
  // the pane head, a bar's width away from the only other control that adds
  // anything — asked for as "one place where all the add buttons are".
  addTools = null,
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
  /**
   * THE RAZOR, and there is exactly one of it: `(kind, id, ms)`.
   *
   * ⚠ IT NAMES THE CLIP IT LANDED ON. It used to be two callbacks and the
   * picture one was answered by `toolPress` — which runs for the RULER and for
   * the empty part of EVERY lane — so clicking the razor anywhere at all cut
   * the picture sequence. Clicking in the time ruler cut the image clip
   * (user-reported), and clicking an empty stretch of the shapes row did too.
   * A cut is a thing you do TO A CLIP, so the clip is now identified at the
   * press and the editor cuts that one and nothing else.
   *
   * `kind` is null where the razor landed on no clip at all; the editor says so
   * rather than guessing which layer was meant.
   */
  onRazor,
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
  // The ruler's ticks. ⚠ ONLY THE ONES IN THE VISIBLE WINDOW: the bar is sticky
  // and re-renders on every scrub, and at full zoom a 70-second cut is 1,681 of
  // them. The window is grown by one label's width either way so a tick never
  // pops into existence at the edge. See `rulerTicks`.
  const rulerFps = Math.max(1, Math.round(fps) || DEFAULT_FPS);
  const ticks = rulerTicks(
    pxPerSec,
    span,
    rulerFps,
    viewBox.vw ? viewBox.sl - MAJOR_MIN_PX : 0,
    viewBox.vw ? viewBox.sl + viewBox.vw + MAJOR_MIN_PX : width
  );
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

  // --- Dropping an asset onto a lane ----------------------------------------
  // Drag a picture out of the Media pane onto the Images row, an audio track
  // onto its lane, a file off the desktop onto either — and it lands AT THE
  // TIME UNDER THE POINTER, snapped like every other drag on this timeline.
  //
  // ⚠ THE KIND HAS TO BE READABLE DURING `dragover`, and `getData` is
  // deliberately blank until the drop in every browser — only the TYPE LIST is
  // exposed while the drag is in flight. So the drag source stamps an empty
  // marker type per kind (`application/x-anim-image` and friends) beside the
  // JSON payload, and this reads the marker. Without that trick a lane could
  // not know whether to accept until it was too late to say so.
  const [dropAt, setDropAt] = useState(null);
  // ⚠ "fx" AND "afx" ARE TWO KINDS, because the rows that take one do not take
  // the other: an effect or a video transition belongs to the picture, a
  // crossfade to the audio. One shared marker would light every row up for every
  // drag and refuse half of them after the drop — which is the "no entry" cursor
  // arriving one gesture too late. See `fxMarkerType` in `fx_library.js`.
  const DRAG_KINDS = ["image", "video", "audio", "shape", "fx", "afx"];

  function dragKind(e) {
    const types = Array.from(e.dataTransfer?.types || []);
    const marked = DRAG_KINDS.find((k) => types.includes(`application/x-anim-${k}`));
    if (marked) return marked;
    // A drop from the desktop. The editor routes those by file type, so any
    // lane that takes assets takes files too.
    return types.includes("Files") ? "files" : null;
  }

  /**
   * WHICH ROWS TAKE WHAT.
   *
   * ⚠ A CLIP CANNOT CHANGE WHAT IT IS by being dropped somewhere else. The
   * picture track is drawn as two rows filtered by origin (`laneShows`), so a
   * video clip belongs on Video and a still on Images — dropping one on the
   * other row is refused, not silently converted. Text and shape rows are not
   * drop targets at all: nothing in the Media pane is a caption or a rectangle.
   */
  function laneTakes(lane, kind) {
    if (!kind) return false;
    // ⚠ AN EFFECT AND A TRANSITION SHARE ONE MARKER, because a lane cannot tell
    // them apart mid-drag — the payload is unreadable until the drop. So the
    // rows that take EITHER say yes to both, and `dropAsset` is what refuses a
    // transition dropped on an overlay row (a transition belongs to a cut in
    // the sequence, and an image layer has no cuts).
    //
    // Only the rows that carry PICTURES take them at all: those are the two
    // clip kinds with pixels to grade, which is the same `LOOK_KINDS` rule the
    // scene model and both renderers already follow. A caption or a rectangle
    // is drawn above the finished composite and has nothing to key out.
    if (kind === "fx") return lane.kind === "frames" || lane.kind === "image";
    // A CROSSFADE IS SHAPED ONTO AUDIO CLIPS, so only audio rows take one — and
    // unlike "fx" there is nothing left for `dropAsset` to refuse afterwards,
    // because every audio row means the same thing to one.
    if (kind === "afx") return lane.kind === "audio";
    if (lane.kind === "frames") {
      if (kind === "files") return true;
      return lane.only === "video" ? kind === "video" : kind === "image";
    }
    // An IMAGE LAYER is a picture composited over the video, not a place in the
    // sequence — so it takes a still and makes a copy of it up there. ⚠ It
    // refused stills at first and that read as a broken row (user-reported,
    // with a screenshot of the red ring): two rows say "image", and the one
    // named for the layer you made yourself is the obvious place to drop a
    // picture on. Video is still refused — an overlay is a picture.
    if (lane.kind === "image") return kind === "files" || kind === "image";
    if (lane.kind === "audio") return kind === "files" || kind === "audio";
    // A shape row takes a shape out of the picker, or one already on the
    // timeline being moved. Files mean nothing here — a shape has no file.
    if (lane.kind === "shape") return kind === "shape";
    return false;
  }

  function allowedEffect(e) {
    const allowed = e.dataTransfer?.effectAllowed;
    if (allowed === "copy" || allowed === "copyLink") return "copy";
    if (allowed === "move" || allowed === "linkMove") return "move";
    // "all", "copyMove", "uninitialized" (an OS file drop) — copy is in all of
    // them, so it is the one answer that is never refused.
    return "copy";
  }

  /** The four handlers every droppable lane spreads. */
  function dropProps(lane) {
    if (!onDropAsset) return {};
    const over = (e) => {
      const kind = dragKind(e);
      if (!kind) return;
      const ok = laneTakes(lane, kind);
      // ⚠ ONLY AN ACCEPTING LANE CALLS preventDefault. That is what tells the
      // browser a drop may happen here — leaving it off is what gives a lane
      // that refuses the "no entry" cursor, for free and in the reader's own
      // platform style.
      if (ok) e.preventDefault();
      // ⚠ IT MUST BE AN EFFECT THE SOURCE ALLOWS. A drop whose `dropEffect` is
      // not in the drag's `effectAllowed` is filtered out by the browser and
      // never fires — silently. The picker's tiles are a COPY (the gallery
      // keeps its shape), a clip being re-timed is a MOVE, and a file drop
      // arrives as "all", so the answer is read off the drag rather than
      // guessed per lane.
      if (e.dataTransfer) e.dataTransfer.dropEffect = ok ? allowedEffect(e) : "none";
      const ms = snapMs(msFromEvent(e));
      // Both fx kinds land on a CLIP rather than at a moment, so both light the
      // bar up instead of drawing the drop line — see `dropOnto`.
      const fx = kind === "fx" || kind === "afx";
      setDropAt((d) =>
        d && d.key === lane.key && d.ms === ms && d.ok === ok && d.fx === fx
          ? d
          : { key: lane.key, ms, ok, fx }
      );
    };
    return {
      onDragEnter: over,
      onDragOver: over,
      onDragLeave: (e) => {
        // Moving between two clips INSIDE the lane fires a leave for the lane;
        // only a pointer that has actually left it should clear the marker.
        if (e.currentTarget.contains(e.relatedTarget)) return;
        setDropAt((d) => (d && d.key === lane.key ? null : d));
      },
      onDrop: (e) => {
        const kind = dragKind(e);
        setDropAt(null);
        if (!laneTakes(lane, kind)) return;
        e.preventDefault();
        let asset = null;
        try {
          const raw = e.dataTransfer.getData("application/x-anim-asset");
          asset = raw ? JSON.parse(raw) : null;
        } catch {
          asset = null;
        }
        onDropAsset({
          lane,
          atMs: snapMs(msFromEvent(e)),
          asset,
          files: Array.from(e.dataTransfer.files || []),
        });
      },
    };
  }

  /** The lane's own class and the line showing where the drop would land. */
  function dropClass(lane) {
    if (!dropAt || dropAt.key !== lane.key) return "";
    return dropAt.ok ? "drop-ok" : "drop-no";
  }
  function dropMark(lane) {
    if (!dropAt || dropAt.key !== lane.key || !dropAt.ok) return null;
    return (
      <span className="tl-drop-line" style={{ left: (dropAt.ms / 1000) * pxPerSec }}>
        <span className="tl-drop-time">{formatTime(dropAt.ms)}</span>
      </span>
    );
  }
  /**
   * ⚠ IS THIS THE CLIP AN EFFECT WOULD LAND ON?
   *
   * A dropped ASSET lands at a MOMENT, which is what the drop line says. A
   * dropped EFFECT lands on a CLIP, and a line between two pictures would be a
   * straight lie about which one is about to be graded — the one question the
   * drag has to answer before you let go. So the bar itself lights up instead.
   *
   * Which of the two the marker means cannot be read mid-drag, so `dropAt.fx`
   * carries it over from `dragKind`.
   */
  function dropOnto(lane, start, duration) {
    if (!dropAt || !dropAt.fx || !dropAt.ok || dropAt.key !== lane.key) return false;
    return dropAt.ms >= start && dropAt.ms < start + duration;
  }

  /**
   * The ƒx badge — "this picture is graded, and here is the way in".
   *
   * ⚠ IT IS A COUNT, NOT A LIST. Which effects, in which order, at what values
   * is the Properties pane's job and needs a pane's worth of room; at eight
   * pixels of bar the timeline can only usefully say THAT there are some. The
   * click is the whole point of drawing it: an effect chain had no
   * representation on the timeline at all, so a clip you had graded looked
   * exactly like one you hadn't.
   */
  function fxBadge(kind, clip, w) {
    const n = (clip?.effects || []).length;
    if (!n || w < 34 || !onManageEffects) return null;
    return (
      <button
        type="button"
        className="tl-fx"
        title={`${n} effect${n === 1 ? "" : "s"} on this clip — click to manage them in Properties`}
        /* The bar underneath selects on pointerdown and can start a drag from
           it. Both are right for the bar and wrong for a button sitting on it,
           so the press stops here and the click does the work. */
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          onManageEffects(kind, clip.id);
        }}
      >
        fx{n > 1 ? n : ""}
      </button>
    );
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

  /**
   * What a press does depends on the tool. Hand and zoom mean the same thing
   * wherever they land, so they are answered once, here, for both the ruler and
   * the lanes.
   *
   * ⚠ THE RAZOR IS NOT IN HERE ANY MORE, and taking it out is the fix. This
   * function runs for the RULER and for the empty part of EVERY lane, so a blade
   * answered here cut the picture sequence wherever you clicked — including in
   * the seconds row, where no clip is drawn at all (user-reported: "I click in the
   * seconds row and my image clip got cut"). A cut is a thing you do TO A CLIP,
   * so it is answered by the clips instead — see `razorPress`.
   */
  function toolPress(e) {
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
    // ⚠ THE ONE PRESS HANDLER ON THIS BAR THAT USED TO SKIP THIS, and it showed:
    // a drag on the ruler or the playhead grip started a native TEXT SELECTION,
    // so the track names, the clip labels and the empty-lane prompts were left
    // highlighted blue behind the playhead (user-reported). `.tl-wrap` is
    // `user-select: none` wholesale as well — every drag on this bar means
    // something and there are no inputs on it — so this is the belt to that
    // braces.
    e.preventDefault();
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
  /**
   * The razor, on whatever is under the pointer. `true` if it consumed the
   * press, which every caller uses as its first line.
   *
   * ⚠ ONE FUNCTION FOR EVERY LANE, because "the razor cuts what you clicked" has
   * to be one sentence in the code as well as one sentence to the user. It is
   * called from the clip bodies AND from the drag starters — a press on a trim
   * handle or a fade grip is still a press on that clip, and under the blade it
   * must cut rather than resize. (The CSS also takes those grips out of the
   * pointer's way while the razor is up, so this is the belt to that braces:
   * neither one alone should be trusted with "the razor never resizes".)
   */
  function razorPress(e, kind, id) {
    if (tool !== "razor") return false;
    e.preventDefault();
    e.stopPropagation();
    onRazor?.(kind, id, msFromEvent(e));
    return true;
  }

  function startLanePress(e) {
    if (e.button !== 0) return;
    // ⚠ THE EMPTY PART OF A LANE IS NOT A CLIP. Answered here rather than in
    // `toolPress` so that it can say "nothing there" instead of cutting
    // something on another row — and so the ruler, which shares `toolPress`,
    // goes on scrubbing while the razor is up, exactly as it does in Premiere.
    if (razorPress(e, null, null)) return;
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
    // The edit point between two pictures, and the blade beats it too.
    if (razorPress(e, "frame", frame.id)) return;
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

  /**
   * The head of the FIRST picture — the one edge on the whole sequence that is
   * not a cut.
   *
   * ⚠ IT IS THE ONLY PICTURE EDGE THAT TRIMS THE CLIP ITSELF. Every other head
   * grip on this row drags the cut in front of it (`startResize` on the frame
   * before), because that is the edit at that edge. The first picture has no
   * frame before it and no cut to move: its head is 0:00, and the only thing you
   * can do there is start LATER INTO the clip — the ripple trim-in every NLE
   * does — with everything after it moving up.
   *
   * ⚠ AND FOR A VIDEO CLIP THAT MEANS MOVING `in_ms`, NOT JUST SHORTENING IT.
   * `sourceAt` reads `in_ms + t * speed`, so skipping `head` ms of TIMELINE has
   * to skip `head * speed` of FILE or the picture at 0:00 does not change and
   * all you did was throw away the end of the shot. `out_ms` is an absolute
   * position in the source and stays exactly where it is.
   *
   * The travel is bounded up front, in timeline ms, so the edge stops at
   * whichever wall comes first instead of quietly hitting one and going on:
   *   · trimming IN  — the clip's own floor, and the last moment of source there
   *     is to show (`out_ms`).
   *   · trimming OUT — however much footage sits BEFORE `in_ms`. Nothing, for a
   *     still: it has no source to give back, so its head only goes one way.
   */
  function startHeadTrim(e, frame) {
    if (razorPress(e, "frame", frame.id)) return;
    e.stopPropagation();
    e.preventDefault();
    const video = clipKind(frame) === "video";
    const inMs = Math.max(0, Number(frame.in_ms) || 0);
    let speed = Number(frame.speed);
    if (!Number.isFinite(speed) || speed <= 0) speed = DEFAULT_SPEED;
    const outMs = frame.out_ms;
    // `out_ms` is EXCLUSIVE, so the last moment actually shown is one ms inside
    // it — the same reading `sourceAt` takes.
    const sourceRoom =
      video && outMs !== null && outMs !== undefined
        ? Math.floor((Number(outMs) - 1 - inMs) / speed)
        : Infinity;
    dragRef.current = {
      id: frame.id,
      startX: e.clientX,
      startMs: frame.duration_ms,
      // The head of the film. Named for the snap, which excludes it so the edge
      // cannot stick to where it already is.
      edgeMs: 0,
      head: true,
      // ⚠ THE FRAME ITSELF, because its KEYFRAMES have to be re-timed with it —
      // a Ken Burns push is stored relative to the frame's own start, so cutting
      // the head off and leaving the keys where they are slides the move out of
      // step with the footage it was matched to. Read as it was when the drag
      // began, which is the only version that means anything here.
      frame,
      // No rolling at the head of the film: rolling gives to one side of a CUT
      // what it takes from the other, and there is no other side here.
      rolling: false,
      nextId: null,
      nextMs: 0,
      video,
      inMs,
      speed,
      // ⚠ BOTH BOUNDS STRADDLE 0, always, so "did not move" can never come back
      // as an edit — `clamp` returns its low bound when they cross.
      minHead: video ? -Math.floor(inMs / speed) : 0,
      maxHead: Math.max(0, Math.min(frame.duration_ms - MIN_MS, sourceRoom)),
    };
    setDraft({ id: frame.id, durationMs: frame.duration_ms });
  }

  useEffect(() => {
    if (!draft) return undefined;
    function move(e) {
      const d = dragRef.current;
      if (!d) return;
      const deltaMs = ((e.clientX - d.startX) / pxPerSec) * 1000;
      if (d.head) {
        // ⚠ WHAT SNAPS IS THE CLIP'S FAR EDGE, not the one under the pointer.
        // The head of the first picture is pinned to 0:00 and cannot move — it
        // is the START OF THE FILM — so trimming into it moves the FIRST CUT
        // instead, and that is the edge with something to line up against: the
        // audio does NOT ripple, so the cut can be pulled onto a beat.
        const cut = snapMs(d.startMs - deltaMs, [d.startMs]);
        const head = clamp(d.startMs - cut, d.minHead, d.maxHead);
        const next = Math.max(MIN_MS, Math.round(d.startMs - head));
        d.latest = next;
        setDraft({ id: d.id, durationMs: next });
        return;
      }
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
        if (d.head) {
          // The two fields travel TOGETHER in one patch. Written apart they
          // would be two renders and two steps to undo through, and a project
          // saved between them would have a clip that lost its head without
          // losing the footage in it.
          const head = d.startMs - d.latest;
          const patch = { duration_ms: d.latest };
          if (d.video) patch.in_ms = Math.max(0, Math.round(d.inMs + head * d.speed));
          // Same surgery a caption's head trim gets, and for the same reason —
          // see `trimKeyframesHead`. Null for a frame that animates nothing,
          // which is most of them, and null means "write no field".
          const keyframes = trimKeyframesHead(d.frame, head);
          if (keyframes) patch.keyframes = keyframes;
          if (onFrameChange) onFrameChange(d.id, patch);
          else onResize(d.id, patch.duration_ms);
          return;
        }
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
  }, [draft, pxPerSec, onResize, onFrameChange, tool]);

  // The lane kinds and the SELECTION kinds are spelled differently in one place
  // only — a lane of pictures over the video is `image`, the thing on it is an
  // `overlay` — so the translation lives here rather than at four call sites.
  const SEL_KIND = { frames: "frame", text: "text", shape: "shape", image: "overlay" };

  // --- Dragging a clip onto ANOTHER ROW ------------------------------------
  // A clip's row used to be decided once, when it was made: the only way to
  // change it was to drag the thing out of the Media pane again, which for a
  // caption or an overlay picture was no way at all, and for a piece of audio
  // was refused outright. So a MOVE drag has a vertical half now — let go over
  // another row of the same kind and the clip goes there, at the time it was
  // dragged to. Reported as "I can't move some audio part to the other audio
  // layer's blank area", and the same was true of every other kind of row.
  //
  // ⚠ THE PICTURE ROWS ARE NOT IN THIS LIST, and it is the one rule here worth
  // stating twice. `frames` is ONE sequence drawn as two rows filtered by ORIGIN
  // (`laneShows`), so "which row" is not something a picture carries — it is read
  // back off the clip. A still cannot become footage by being dropped somewhere
  // else, which is exactly what `laneTakes` already says about a drop out of the
  // Media pane.
  const CROSS_LANE_KINDS = ["text", "shape", "image", "audio"];

  /**
   * The row under the pointer, or null.
   *
   * ⚠ ASKS THE DOM, exactly as the marquee does (`hitsIn`). Every lane carries
   * `data-lane` and the browser has already laid the rows out; deriving the row
   * from an index and `--tl-track-h` would be a second copy of the timeline's
   * vertical geometry, and it would be wrong for the whole of a vertical zoom.
   *
   * The rows are separated by `--tl-row-gap`, so each box is grown a few pixels
   * either way: a pointer in the crack between two rows means the nearer one,
   * not "nowhere".
   */
  function laneAtPoint(clientY) {
    const nodes = innerRef.current?.querySelectorAll("[data-lane]") || [];
    for (const node of nodes) {
      const r = node.getBoundingClientRect();
      if (clientY >= r.top - 3 && clientY <= r.bottom + 3) {
        return lanes.find((l) => l.key === node.dataset.lane) || null;
      }
    }
    return null;
  }

  /**
   * Which row a clip dragged off `fromKey` would land on, as a lane KEY — null
   * for "the one it is already on", which is what every ordinary move reports
   * and the only answer that leaves the clip's row alone.
   */
  function laneMoveTarget(fromKey, clientY) {
    const to = laneAtPoint(clientY);
    if (!to || to.key === fromKey) return null;
    if (!CROSS_LANE_KINDS.includes(to.kind)) return null;
    const from = lanes.find((l) => l.key === fromKey);
    // A caption cannot land on a shapes row: the two rows draw different things
    // and a clip does not change what it is by being dropped somewhere else.
    if (!from || from.kind !== to.kind) return null;
    return to.key;
  }

  /** Is this the row a clip is about to be dropped onto? */
  function laneIsTarget(lane) {
    const key = clipDraft?.toKey || audioDraft?.toKey || null;
    return Boolean(key) && key === lane.key;
  }

  /**
   * The clip's outline on the row it is about to land on.
   *
   * ⚠ THE BAR ITSELF STAYS WHERE IT IS. A clip is a CHILD of its own lane, so
   * moving it to the row under the pointer mid-drag would mean re-parenting a
   * node the pointer is captured on — the drag would end. An outline on the
   * destination and the original dimmed (`lifting`) says the same thing and
   * survives the gesture.
   */
  function laneGhost(lane) {
    if (!laneIsTarget(lane)) return null;
    const d = clipDraft?.toKey === lane.key ? clipDraft : audioDraft;
    const lengthMs = d.durationMs ?? d.lengthMs ?? 0;
    return (
      <span
        className="tl-ghost"
        style={{
          left: (d.startMs / 1000) * pxPerSec,
          width: Math.max(6, (lengthMs / 1000) * pxPerSec),
        }}
      >
        <span className="tl-ghost-time">{formatTime(d.startMs)}</span>
      </span>
    );
  }

  // --- Free-floating clip move / trim (text AND shapes) -------------------
  // Both layers are the same thing on the timeline: a box with its own start
  // and length. One implementation, `kind` deciding where the result is written.
  //
  // THREE MODES, and the third is the one the grips used to be missing:
  //   "move"        — the whole clip slides; the selection comes with it.
  //   "resize"      — the TAIL grip. The end moves, the start stays.
  //   "trim-start"  — the HEAD grip. The start moves, THE END STAYS PUT. Not a
  //                   move and not a resize: it changes both numbers, and it is
  //                   the only one of the three that has to re-time the clip's
  //                   keyframes (`trimTimedClipStart`).
  //
  // ⚠ IT TAKES THE LANE, NOT JUST `lane.kind`. A move can now change WHICH ROW
  // the clip is on, so the drag has to know the row it started from — a kind
  // alone cannot tell one text row from another.
  function startClipDrag(e, clip, mode, lane) {
    if (e.button !== 0) return;
    const kind = lane.kind;
    // A caption, a shape or an overlay picture — and this is BOTH the body and
    // the trim handle, since both come through here.
    if (razorPress(e, SEL_KIND[kind], clip.id)) return;
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
      // The row it started on, so the vertical half of the drag has something to
      // compare against — see `laneMoveTarget`.
      fromKey: lane.key,
      startX: e.clientX,
      startMs: clip.start_ms,
      durationMs: clip.duration_ms,
      // ⚠ THE CLIP ITSELF, not just its two numbers, because a head trim has to
      // read its KEYFRAMES to re-time them — and it reads them as they were when
      // the drag began, which is the only version that means anything here.
      clip,
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
      // The row it would land on, once the pointer has left its own. Null until
      // then, which is every drag that stays where it started.
      toKey: null,
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
      const end = d.startMs + d.durationMs;
      const next =
        d.mode === "trim-start"
          ? (() => {
              // The clip's START is what snaps to a cut; its END is nailed down,
              // so the length is whatever is left between them. Clamped at 0:00
              // on one side and MIN_MS short of its own end on the other.
              const startMs = clamp(snapMs(d.startMs + deltaMs, own), 0, end - MIN_MS);
              return { kind: d.kind, id: d.id, startMs, durationMs: end - startMs };
            })()
          : d.mode === "move"
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
      // ⚠ THE VERTICAL HALF, AND ONLY A PLAIN MOVE HAS ONE. A trim is about this
      // clip's own length, and a GROUP move can span kinds — "put these forty
      // things on that one row" is not an edit with a single meaning, so a
      // multi-clip drag stays on its rows and only travels in time.
      next.toKey =
        d.mode === "move" && !d.group ? laneMoveTarget(d.fromKey, e.clientY) : null;
      d.latest = next;
      setClipDraft(next);
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setClipDraft(null);
      // ⚠ CHANGING ROW COUNTS AS HAVING MOVED even when the time did not. Drag a
      // caption straight up onto the next text row and every number about it is
      // the one it had; without this the gesture would be read as a click and
      // narrow the selection instead of moving the clip.
      const relaned = Boolean(d?.latest?.toKey);
      const moved =
        relaned ||
        (d?.latest &&
          (d.latest.startMs !== d.startMs || d.latest.durationMs !== d.durationMs));
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
        // ⚠ A HEAD TRIM IS NOT A `{ start_ms, duration_ms }` WRITE. It also
        // re-times the clip's keyframes — see `trimTimedClipStart`, which is
        // where the "a key is planted at the new head" rule lives, and which
        // returns null when the drag came back to where it started.
        if (d.mode === "trim-start") {
          const patch = trimTimedClipStart(d.clip, d.latest.startMs);
          if (patch) write(d.id, patch);
          return;
        }
        // Landed on another row: the EDITOR is told which row, because what a
        // row means to a clip is the document's business and not this file's.
        const to = relaned ? lanes.find((l) => l.key === d.latest.toKey) : null;
        if (to && onMoveToLane) {
          onMoveToLane(SEL_KIND[d.kind], d.id, to, {
            start_ms: d.latest.startMs,
            duration_ms: d.latest.durationMs,
          });
          return;
        }
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
  }, [
    clipDraft,
    pxPerSec,
    onTextChange,
    onShapeChange,
    onOverlayChange,
    onMoveSelection,
    onMoveToLane,
    lanes,
  ]);

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
  // ⚠ IT TAKES THE LANE TOO. A move can change WHICH ROW the clip sits on now,
  // and the rows are named per file — a kind alone could not tell them apart.
  function startAudioDrag(e, track, mode, lane) {
    if (e.button !== 0) return;
    if (razorPress(e, "audio", clipId(track))) return;
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
      // The row it started on, so a drag off it has something to compare
      // against — see `laneMoveTarget`. Null on the draft until it leaves.
      fromKey: lane?.key ?? null,
      toKey: null,
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
      next.fromKey = d.fromKey;
      // The vertical half — the same rule the picture clips follow: only a plain
      // single-clip move can change row. THIS is the gesture that was missing:
      // an audio clip could be slid along its own row and nowhere else, so a
      // piece cut out of one take could not be put on another row.
      next.toKey =
        d.mode === "move" && !d.group && d.fromKey
          ? laneMoveTarget(d.fromKey, e.clientY)
          : null;
      d.latest = next;
      setAudioDraft(next);
    }
    function up() {
      const d = dragRef.current;
      dragRef.current = null;
      setAudioDraft(null);
      if (!d?.audio) return;
      // Changing row counts as having moved even when the time did not — drag a
      // clip straight up onto the row above and every number about it is the one
      // it had. Without this the gesture would be read as a press and scrub.
      const relaned = Boolean(d.latest?.toKey);
      const moved =
        relaned ||
        (d.latest !== undefined &&
          (d.latest.startMs !== d.startMs ||
            d.latest.lengthMs !== d.lengthMs ||
            d.latest.offsetMs !== d.offsetMs));
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
      // Landed on another row. The editor is told WHICH ROW rather than a layer
      // id, because an audio row may be one grouped by FILE — which has no id to
      // write — and turning that into a real destination is the document's job.
      // See `moveClipToLane` in AnimaticEditor.jsx.
      const to = relaned ? lanes.find((l) => l.key === d.latest.toKey) : null;
      if (to && onMoveToLane) {
        onMoveToLane("audio", d.id, to, { start_ms: d.latest.startMs });
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioDraft, pxPerSec, onTrackChange, onMoveToLane, lanes]);

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
        className={[
          "tl-lane",
          className,
          lane.hidden ? "off" : "",
          dropClass(lane),
          laneIsTarget(lane) ? "drop-lane" : "",
        ].join(" ")}
        onPointerDown={startLanePress}
        data-lane={lane.key}
        {...dropProps(lane)}
      >
        {dropMark(lane)}
        {laneGhost(lane)}
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
                dropOnto(lane, start, duration) ? "drop-onto" : "",
                // On its way to another row: dimmed here, outlined there.
                clipDraft?.toKey && clipDraft.id === item.id ? "lifting" : "",
              ].join(" ")}
              style={{ left, width: w }}
              onPointerDown={(e) => startClipDrag(e, item, "move", lane)}
              title={
                `${body.title(item)} — ${(start / 1000).toFixed(1)}s for ${(duration / 1000).toFixed(1)}s` +
                (overruns ? " (runs past the end of the video)" : "") +
                (grouped ? " · grouped — selecting it selects the others" : "") +
                "\nDrag it up or down onto another " +
                (lane.kind === "image" ? "picture" : lane.kind) +
                " layer to move it there." +
                "\nShift-click to add it to the selection; drag the empty part of a lane to select several."
              }
            >
              {body.render(item, w)}
              {fxBadge(kind, item, w)}
              {/* Keys are drawn ON the clip, because that is where they live —
                  their times are relative to it, so dragging the clip carries
                  them along and the diamonds move with it for free.
                  ONE DIAMOND PER INSTANT, not per property: a Ken Burns push
                  keyframes `scale` and `x` together, and two diamonds a pixel
                  apart would be unclickable. Which property is which is the
                  Properties pane's job. */}
              {renderKeys(item, lane.kind, w, start)}
              {/* A GRIP AT BOTH ENDS, which is what the audio clips have had all
                  along. The head one moves the clip's start and leaves its end
                  alone — the edit you want when a caption comes in too early,
                  and one there was no gesture for: the tail grip changes the
                  length, and a move changes neither. */}
              {w >= BOTH_GRIPS_MIN_PX && (
                <span
                  className="tl-handle tl-handle-l"
                  onPointerDown={(e) => startClipDrag(e, item, "trim-start", lane)}
                  title="Drag to trim the head — it comes in later and still ends where it did"
                />
              )}
              <span
                className="tl-handle"
                onPointerDown={(e) => startClipDrag(e, item, "resize", lane)}
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
          className={`tl-lane tl-bars ${lane.hidden ? "off" : ""} ${dropClass(lane)}`}
          key={lane.key}
          data-lane={lane.key}
          onPointerDown={startLanePress}
          {...dropProps(lane)}
        >
          {dropMark(lane)}
          {frames.map((f, i) => {
            const ms = durationOf(f);
            const w = (ms / 1000) * pxPerSec;
            // Named, because the drop highlight needs the same number the bar is
            // drawn at — re-deriving it from `starts` would be the one place
            // that disagreed once a clip was mid-drag.
            const start = clock;
            const left = (start / 1000) * pxPerSec;
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
                  dropOnto(lane, start, ms) ? "drop-onto" : "",
                ].join(" ")}
                style={{ left, width: w }}
                onPointerDown={(e) => {
                  // The razor cuts wherever it is clicked ON the picture —
                  // that is the tool's whole behaviour, and it must beat
                  // "select this frame". ⚠ It NAMES the frame now: the time
                  // alone was what let a press somewhere else cut this one.
                  if (razorPress(e, "frame", f.id)) return;
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
                {fxBadge("frame", f, w)}
                {/* A frame's own keys — a Ken Burns push lives here, so this is
                    the lane where they matter most. Times are relative to the
                    frame, so they ride along when its hold is re-timed. */}
                {renderKeys(
                  f,
                  "frames",
                  w,
                  frames.slice(0, i).reduce((sum, x) => sum + (x.duration_ms || 0), 0)
                )}
                {/* THE HEAD GRIP, and on a picture it is THE CUT BEFORE THIS
                    ONE — so it drags the PREVIOUS frame's hold, which is the
                    same edit point its own tail grip is.
                    ⚠ A PICTURE HAS NO START OF ITS OWN. Its start is the sum of
                    every hold before it, so "trim this one's head" cannot mean
                    what it means on a caption: shortening the clip itself would
                    ripple everything left and move its far edge, not this one,
                    which is precisely what the tail grip already does. Moving
                    the cut is the only edit that puts THIS edge under the
                    pointer — and it obeys ripple / rolling (B / N) for free,
                    because it is `startResize` on the frame before.
                    ⚠ THE FIRST PICTURE IS THE EXCEPTION, and it took a second
                    edit rather than no grip at all: there is no cut in front of
                    it, so its head grip trims INTO the clip — `startHeadTrim`,
                    which moves a video clip's `in_ms` with the length. The rule
                    both halves obey is "the head grip edits whatever is at this
                    clip's head": a cut where there is one, the start of the film
                    where there isn't. */}
                {w >= BOTH_GRIPS_MIN_PX && (
                  <span
                    className="tl-handle tl-handle-l"
                    onPointerDown={(e) =>
                      i > 0 ? startResize(e, frames[i - 1], i - 1) : startHeadTrim(e, f)
                    }
                    title={
                      i === 0
                        ? clipKind(f) === "video"
                          ? "Drag to trim the head of this shot — it starts later into the footage and everything after it moves up"
                          : "Drag to trim the head of this shot — everything after it moves up"
                        : tool === "rolling"
                          ? "Rolling edit — drag the cut at the head of this shot; the shot before absorbs it and the video stays the same length"
                          : "Drag the cut at the head of this shot — the shot before it is held longer or shorter"
                    }
                  />
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
        <div
          className={`tl-lane tl-audio ${dropClass(lane)} ${
            laneIsTarget(lane) ? "drop-lane" : ""
          }`}
          key={lane.key}
          data-lane={lane.key}
          onPointerDown={startLanePress}
          {...dropProps(lane)}
        >
          {dropMark(lane)}
          {laneGhost(lane)}
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
        className={`tl-lane tl-audio ${dropClass(lane)} ${
          laneIsTarget(lane) ? "drop-lane" : ""
        }`}
        data-lane={lane.key}
        onPointerDown={startLanePress}
        {...dropProps(lane)}
      >
        {dropMark(lane)}
        {laneGhost(lane)}
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
                // A crossfade being dragged over lands on THIS clip, so the bar
                // lights up rather than a line being drawn between two of them —
                // the same reasoning as an effect over a picture.
                dropOnto(lane, trackStart(track), lengthMs) ? "drop-onto" : "",
                // On its way to another row: dimmed here, outlined there.
                audioDraft?.toKey && audioDraft.id === id ? "lifting" : "",
              ].join(" ")}
              style={{ left, width: clipW }}
              title={`${track.filename} — ${(trackStart(track) / 1000).toFixed(1)}s for ${(lengthMs / 1000).toFixed(1)}s. Drag to move it along the timeline, or up and down onto another audio layer to move it there. The razor (C) cuts it where you click; shift-click adds it to the selection.`}
              onPointerDown={(e) => {
                // ⚠ THE RAZOR BEATS THE DRAG, and it has to: with the tool
                // selected, a press on a clip means "cut here", and starting a
                // move instead would nudge the clip a pixel and cut nothing.
                // ⚠ It NAMES the clip now: the time alone was what let a press
                // somewhere else cut this one.
                if (razorPress(e, "audio", id)) return;
                // Only the selection tool moves a clip. The others (hand, zoom)
                // still mean what they mean everywhere else on the timeline.
                if (tool !== "select" && tool !== "ripple" && tool !== "rolling") return;
                if (e.shiftKey || e.ctrlKey || e.metaKey) {
                  e.preventDefault();
                  e.stopPropagation();
                  onToggleSelect?.("audio", id);
                  return;
                }
                startAudioDrag(e, track, "move", lane);
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
                  which is how you discover the handle is there at all.

                  ⚠ AND EACH ONE CARRIES ITS CURVE. The veil's gradient is the
                  shape of the GAIN, so leaving all three curves drawn as the
                  straight line would make a constant-power crossfade look
                  exactly like the constant-gain one it exists to replace —
                  and the shape on the clip is the only place you can see which
                  of the three you dropped without opening Properties. */}
              <span
                className={`tl-fade tl-fade-in tl-curve-${fadeCurve(track, "in")}`}
                style={{ width: inW }}
              />
              <span
                className={`tl-fade tl-fade-out tl-curve-${fadeCurve(track, "out")}`}
                style={{ width: outW }}
              />
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
                onPointerDown={(e) => startAudioDrag(e, track, "start", lane)}
                title="Drag to trim the head of this clip — it stays where it is on the timeline"
              />
              <span
                className="tl-handle"
                onPointerDown={(e) => startAudioDrag(e, track, "end", lane)}
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
      {/* ⚠ THE HEAD IS A BAR OF TWO HALVES NOW: ＋ Add layer over the gutter, and
          the editor's own make-something buttons beside it. They used to sit at
          the far right of the pane head, a bar's width from the only other
          control that adds anything — asked for as "one place where all the add
          buttons are". What they MAKE is still the editor's business; this file
          only gives them somewhere to stand (`addTools`). */}
      <div className="tl-headbar">
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
        {addTools && <div className="tl-add-tools">{addTools}</div>}
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
            {/* Ruler — click or drag anywhere on it to scrub. It reads in
                HH:MM:SS:FF: a taller labelled tick with bare minor ones between,
                and ONLY the ticks in the visible window (see `rulerTicks`). */}
            <div className="tl-ruler" ref={trackRef} onPointerDown={startSeek}>
              {ticks.map((t) => (
                <span
                  key={t.n}
                  className={`tl-tick${t.major ? " tl-tick-major" : ""}`}
                  style={{ left: t.x }}
                >
                  {t.major && (
                    <i className="tl-tick-label">{timecodeOfFrame(t.n, rulerFps)}</i>
                  )}
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
