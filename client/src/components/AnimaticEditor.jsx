// AnimaticEditor.jsx — the animatic screen: preview, frame strip, timeline.
//
// The important design decision is the CLOCK. Images are not advanced by a
// timer; every animation frame reads the <audio> element's currentTime and
// picks the picture whose slice of the sequence contains it. Audio is the
// master, so the pictures can never drift away from the sound — which is the
// one thing this whole feature exists to let you check. That machinery lives in
// `animatic/useTimelineTransport.js`.
//
// What is left in THIS file is the workspace: the panes, the media it fetches,
// the edits it makes to the document, and the two server jobs it can start.
// Three things that used to be here now have files of their own, and the
// reasoning that goes with them lives there rather than in this header:
//
//   animatic/useAnimaticProject.js   loading, autosave and the dirty baseline
//   animatic/useTimelineTransport.js the playhead, shuttle, marks, video slaves
//   animatic/useUndoStack.js         Ctrl+Z, and the gesture bracket
//   components/properties/           the six Properties panes
//
// Everything here is local and free: no AI call is made, and preview costs
// nothing. "Export video" and "✨ Animate" are the only two that touch the
// server for real work, and only the second one SPENDS MONEY.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api.js";
import {
  ANIMATABLE,
  LOOK_KINDS,
  TEXT_DEFAULTS,
  defaultFor,
  lookProps,
  lookPropParts,
  lookValueOf,
  resolveLook,
  sceneAt,
  setLookValue,
  valueAt,
} from "../animatic/scene.js";
import { DEFAULT_FONT, ensureFontsLoaded, fontFamily } from "../animatic/fonts.js";
import {
  disableProp,
  enableProp,
  isAnimatedProp,
  moveKey,
  moveKeysAt,
  neighbourKey,
  removeKey,
  setKey,
  setKeyEase,
} from "../animatic/keyframes.js";
import { DEFAULT_TRANSITION_MS } from "../animatic/transitions.js";
import { clamp } from "../animatic/util.js";
import useAnimaticProject from "../animatic/useAnimaticProject.js";
import useAudioAnalysis from "../animatic/useAudioAnalysis.js";
import { forgetAudio } from "../animatic/beats.js";
import useTimelineTransport, { useMonitorVideo } from "../animatic/useTimelineTransport.js";
import useUndoStack from "../animatic/useUndoStack.js";
import FrameStrip, { sortFiles } from "./FrameStrip.jsx";
import { UNTITLED } from "./AnimaticLibrary.jsx";
import Timeline, { formatTime } from "./Timeline.jsx";
import Icon from "./Icon.jsx";
import ProgramCanvas from "./ProgramCanvas.jsx";
import ShapeGallery, {
  DEFAULT_SHAPE_COLOR,
  SHAPE_KINDS,
  ShapeSwatch,
} from "./Shapes.jsx";
import EffectsPanel from "./EffectsPanel.jsx";
import {
  AudioProperties,
  FrameProperties,
  ShapeProperties,
  TextProperties,
  TransitionProperties,
  VideoProperties,
} from "./properties/index.js";

// The timeline's scale, in pixels per second. CONTINUOUS, not a list of steps:
// the scroll bar's grips ask for whatever scale frames the stretch you dragged
// them around, and rounding that to the nearest power of two would make the
// gesture lie about what it was going to show you. The ＋/− buttons and the
// Zoom tool still move in steps — `ZOOM_STEP` — which is what a click wants.
const MIN_PPS = 2;
const MAX_PPS = 600;
const DEFAULT_PPS = 32;
const ZOOM_STEP = 1.6;
const MIN_MS = 100;
// Mirrors API_MAX_ANIMATIC_AUDIO_TRACKS on the server.
const MAX_AUDIO_TRACKS = 4;

const RESOLUTIONS = [
  { id: 720, label: "720p" },
  { id: 1080, label: "1080p" },
  { id: 1440, label: "1440p" },
  { id: 2160, label: "4K" },
];

// The familiar frame sizes, written for a 1080 short edge and scaled from there
// — the SAME rule `resolve_size()` uses on the server, so the dialog can show
// the real output size before anything is encoded. Keep the two in step.
const BASE_SIZES = {
  "16:9": [1920, 1080],
  "9:16": [1080, 1920],
  "1:1": [1080, 1080],
  "4:3": [1440, 1080],
  "3:4": [1080, 1440],
  "4:5": [1080, 1350],
  "21:9": [1920, 824],
};
const even = (n) => (Math.round(n) % 2 === 0 ? Math.round(n) : Math.round(n) + 1);
function frameSizeFor(aspect, resolution) {
  const base = BASE_SIZES[aspect] || BASE_SIZES["16:9"];
  const scale = (resolution || 1080) / 1080;
  return [even(base[0] * scale), even(base[1] * scale)];
}

const newId = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

// A new caption. Defaults to a scrim so it stays readable over a grey
// storyboard thumbnail, which is what most of these frames are.
const newTextClip = (startMs, durationMs) => ({
  id: newId(),
  text: "",
  start_ms: Math.max(0, Math.round(startMs)),
  duration_ms: Math.max(100, Math.round(durationMs)),
  position: "bottom",
  align: "center",
  size: "medium",
  color: "#ffffff",
  backdrop: "scrim",
  // The type, all at the values that reproduce exactly what a caption drew
  // before Phase 5 — a new clip and an old one must look the same, or every
  // animatic in the library changes appearance the day this ships.
  font: DEFAULT_FONT,
  place: "flow",
  x: TEXT_DEFAULTS.x,
  y: TEXT_DEFAULTS.y,
  stroke_px: 0,
  stroke_color: "#000000",
  shadow: 0,
  letter_spacing: 0,
});

/**
 * The inline style that makes a caption in the monitor the caption in the MP4.
 *
 * ⚠ EVERY NUMBER HERE IS MATCHED TO `_draw_text_block` IN animatic.py, by
 * construction rather than by eye:
 *   · the FONT is the same bundled .ttf file, loaded by `ensureFontsLoaded`
 *     from `/fonts/` — never a family name the machine resolves;
 *   · `stroke_px` is pixels at 1080p, so it is scaled by the frame height,
 *     which in here is `100cqh` (`.an-screen` is a size container — the same
 *     trick the sz-* font sizes already use);
 *   · `shadow` and `letter_spacing` are fractions of the font size, i.e. `em`,
 *     which is the same number on both sides with no conversion at all;
 *   · the shadow's blur is ZERO and its ink is rgba(0,0,0,.55) because Pillow
 *     draws a hard shadow at alpha 140. A blurred one here would be prettier
 *     and would be a preview that lies.
 */
function captionStyle(c) {
  const style = {
    color: c.color || "#ffffff",
    opacity: c.opacity ?? 1,
    fontFamily: fontFamily(c.font),
  };
  if (c.letter_spacing) style.letterSpacing = `${c.letter_spacing}em`;
  if (c.stroke_px) {
    style.WebkitTextStrokeWidth = `calc(100cqh * ${c.stroke_px} / 1080)`;
    style.WebkitTextStrokeColor = c.stroke_color || "#000000";
    style.paintOrder = "stroke fill";
  }
  if (c.shadow) {
    style.textShadow = `${c.shadow}em ${c.shadow}em 0 rgba(0, 0, 0, 0.55)`;
  }
  return style;
}

// A new shape: a quarter of the frame, dead centre. Geometry is in FRACTIONS of
// the frame (never pixels), so the same shape lands in the same place whether
// the preview is 400px wide or the export is 4K — see AnimaticShape on the
// server and `draw_shapes` in animatic.py.
const newShape = (kind, startMs, durationMs) => ({
  id: newId(),
  kind,
  start_ms: Math.max(0, Math.round(startMs)),
  duration_ms: Math.max(100, Math.round(durationMs)),
  x: 0.5,
  y: 0.5,
  w: 0.25,
  h: 0.25,
  color: DEFAULT_SHAPE_COLOR,
  opacity: 1,
  rotation: 0,
});

// A new VIDEO clip. `duration_ms` is its length on the TIMELINE and `in_ms` /
// `out_ms` the window of the SOURCE it reads — see `sourceAt` in scene.js for
// why speed widens that window instead of re-timing the clip. A clip whose file
// we couldn't measure opens at the default hold rather than at zero length.
const newVideoClip = (uploadId, durationMs, label) => ({
  id: newId(),
  src: { kind: "video", upload_id: uploadId },
  kind: "video",
  duration_ms: clamp(Math.round(durationMs || 2000), MIN_MS, 600000),
  label: label || "",
  in_ms: 0,
  out_ms: durationMs ? Math.round(durationMs) : null,
  speed: 1,
  scale: 1,
  x: 0.5,
  y: 0.5,
  opacity: 1,
  keyframes: {},
});

// A new COLOUR CARD — a clip with no file behind it at all. A slug, a blackout,
// a flash. `src` is still sent because the server's schema requires one; nothing
// ever resolves it for this kind.
const newColorClip = (color, durationMs) => ({
  id: newId(),
  src: { kind: "upload" },
  kind: "color",
  color: color || "#000000",
  duration_ms: clamp(Math.round(durationMs || 1000), MIN_MS, 600000),
  label: "",
  in_ms: 0,
  out_ms: null,
  speed: 1,
  scale: 1,
  x: 0.5,
  y: 0.5,
  opacity: 1,
  keyframes: {},
});

// --- Tools (Premiere's keys, only the ones that mean something here) --------
// An animatic is stills, captions, shapes and audio: there are no keyframes to
// pull, so there is no Pen tool. Everything else maps onto a real action.
const TOOLS = [
  { id: "select", key: "V", label: "Selection", hint: "Select and move clips" },
  { id: "razor", key: "C", label: "Razor", hint: "Click a frame to split it there" },
  { id: "ripple", key: "B", label: "Ripple edit", hint: "Drag an edit point; everything after it shifts" },
  { id: "rolling", key: "N", label: "Rolling edit", hint: "Drag an edit point; the next frame absorbs it, so the video stays the same length" },
  { id: "hand", key: "H", label: "Hand", hint: "Drag to scroll the timeline" },
  { id: "zoom", key: "Z", label: "Zoom", hint: "Click to zoom in · Alt-click to zoom out" },
];

// What a dropped file is, by MIME with an extension fallback (a drag from some
// file managers arrives with an empty type).
function kindOf(file) {
  const type = (file.type || "").toLowerCase();
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("audio/")) return "audio";
  if (type.startsWith("video/")) return "video";
  const ext = (file.name || "").split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "webp", "gif", "bmp"].includes(ext)) return "image";
  if (["mp3", "wav", "m4a", "aac", "ogg", "oga"].includes(ext)) return "audio";
  if (["mp4", "mov", "avi", "mkv", "webm"].includes(ext)) return "video";
  return "other";
}

// Read an audio file's length in the browser. The server has no audio decoder
// (and doesn't need one) — this is what "fit frames to audio" measures against.
function measureAudio(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const probe = new Audio();
    probe.preload = "metadata";
    const done = (ms) => {
      URL.revokeObjectURL(url);
      resolve(ms);
    };
    probe.onloadedmetadata = () => done(Math.round((probe.duration || 0) * 1000));
    probe.onerror = () => done(0);
    probe.src = url;
  });
}

export default function AnimaticEditor({
  animaticId,
  onBack,
  onDeleted,
  onMakeFinalVideo,
}) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // --- Media ---
  const [urls, setUrls] = useState({}); // frame id → object URL
  const urlsRef = useRef({});
  // upload_id → object URL, for the overlay pictures.
  const [overlayUrls, setOverlayUrls] = useState({});
  const overlayUrlsRef = useRef({});
  // upload_id → object URL of the VIDEO FILE ITSELF, for the Program monitor.
  // Separate from `urls`, which holds one THUMBNAIL per clip: the strip and the
  // Properties pane want a still, the monitor wants something that can play.
  // Keyed by upload id, not clip id, so the same take cut three times over is
  // fetched once — the rule the overlay pictures already follow.
  const [videoUrls, setVideoUrls] = useState({});
  const videoUrlsRef = useRef({});
  // upload_id → the <video> element showing it, so playback can slave them all
  // to the clock. Same shape as `audioElsRef`, deliberately.
  const videoElsRef = useRef({});
  // upload_id → object URL, and upload_id → its <audio> element.
  const [audioUrls, setAudioUrls] = useState({});
  const audioUrlsRef = useRef({});
  const audioElsRef = useRef({});

  // --- UI ---
  const [selectedId, setSelectedId] = useState(null);
  const [selectedTextId, setSelectedTextId] = useState(null);
  const [selectedShapeId, setSelectedShapeId] = useState(null);
  const [selectedOverlayId, setSelectedOverlayId] = useState(null);
  const [selectedTransitionId, setSelectedTransitionId] = useState(null);
  // Which half of the Media pane is showing: the footage, or the shape picker.
  const [mediaTab, setMediaTab] = useState("media");
  // An audio track selected for editing — its controls live in Properties, like
  // everything else that has settings.
  const [selectedTrackId, setSelectedTrackId] = useState(null);
  const [pxPerSec, setPxPerSec] = useState(DEFAULT_PPS);
  // The active tool (V / C / B / N / H / Z) and whether clip edges snap (S).
  const [tool, setTool] = useState("select");
  // Which shape lane the picker will drop the next shape onto ("" = default).
  const [pendingShapeLane, setPendingShapeLane] = useState("");
  const [snapping, setSnapping] = useState(true);
  // Which pane is filling the workspace (~), and which one the pointer is over
  // so ~ knows which to maximize — exactly how Premiere decides.
  const [maximized, setMaximized] = useState(null);
  const hoverPaneRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [exportJob, setExportJob] = useState(null);
  const [exportBusy, setExportBusy] = useState(false);
  // True while the final-video project is being created and navigated to.
  const [makingVideo, setMakingVideo] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // The "name this animatic" panel. Null = closed; a string is the typed name.
  const [saveAsName, setSaveAsName] = useState(null);
  // A file is being dragged over the Media pane.
  const [dropping, setDropping] = useState(false);
  // The "what kind of layer?" picker opened by ＋ Add layer.
  const [layerMenu, setLayerMenu] = useState(false);
  // The export dialog, and the file name it will download as.
  const [exportOpen, setExportOpen] = useState(false);
  const [exportName, setExportName] = useState("");

  // --- Animating a frame with Veo (THE ONE THING HERE THAT COSTS MONEY) ---
  // The records themselves are SERVER-owned and live on the project (see
  // `useAnimaticProject`): a save must not be able to erase the record of a
  // clip that was paid for.
  // The "animate this shot" panel. Null = closed; otherwise the frame id.
  const [animateFor, setAnimateFor] = useState(null);
  const [animatePrompt, setAnimatePrompt] = useState("");
  const [animateRender, setAnimateRender] = useState({
    tier: "fast",
    resolution: "720p",
    duration_seconds: 8,
    generate_audio: false,
    negative_prompt: "",
  });
  // The PRICED confirmation. Nothing is submitted until this has been shown and
  // accepted — the rule every paid path in this app follows.
  const [animateConfirm, setAnimateConfirm] = useState(null);
  const [animateBusy, setAnimateBusy] = useState(false);
  // ⚠ A PLAIN BOOLEAN, and the polling effect below keys on THIS ALONE.
  // It used to key on the polled job object, which the poll itself wrote — so
  // the moment the batch finished and the job went RUNNING → QUEUED, the effect
  // re-ran, its cleanup killed the in-flight poll, and the finished clip was
  // never attached. The render had succeeded and been paid for; the editor just
  // sat on "Animating…" for ever. Nothing but `setAnimating` may write this.
  const [animating, setAnimating] = useState(false);
  // Progress, kept separate for the same reason: it changes on every tick and
  // must never be something the effect restarts on.
  const [animateProgress, setAnimateProgress] = useState(null);
  // Renders already dealt with, so a finished clip is attached exactly once and
  // an old one is never re-attached over a frame the user has since changed.
  const veoHandledRef = useRef(new Set());
  const framesRef = useRef([]);

  // --- Captions and voiceover (the other two things here that spend quota) ---
  // Same two-step discipline as ✨ Animate, deliberately: a panel that spends
  // nothing, then a priced confirmation, then the call. These are far cheaper —
  // fractions of a cent against a couple of dollars — which is precisely why
  // the discipline is kept, because a cheap button is the one clicked forty
  // times. Null = closed; "captions" or "voiceover" is which pass is open.
  const [speechFor, setSpeechFor] = useState(null);
  const [speechTrack, setSpeechTrack] = useState("");
  const [speechLanguage, setSpeechLanguage] = useState("");
  const [speechVoice, setSpeechVoice] = useState("Kore");
  const [speechReplace, setSpeechReplace] = useState(true);
  const [speechCaptions, setSpeechCaptions] = useState(true);
  const [speechConfirm, setSpeechConfirm] = useState(null);
  const [speechBusy, setSpeechBusy] = useState(false);
  // ⚠ ITS OWN ERROR, not the editor's banner. The banner renders in the status
  // bar at the top of the page, which is BEHIND the modal overlay — so a failed
  // "See the price" wrote its reason somewhere the user could not possibly see
  // it, and the button looked dead. Anything that can go wrong while this panel
  // is open has to say so INSIDE the panel.
  const [speechError, setSpeechError] = useState("");
  // A plain boolean the poll keys on ALONE, for the reason spelled out above
  // `animating`: an effect that restarts on what its own poll writes cancels
  // itself mid-flight, and the work is already paid for by then.
  const [speechRunning, setSpeechRunning] = useState(false);
  const [speechProgress, setSpeechProgress] = useState(null);

  const textAreaRef = useRef(null);
  const audioInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const assetInputRef = useRef(null);
  const overlayInputRef = useRef(null);
  // Which audio lane a just-picked file belongs to ("" = a lane of its own).
  const pendingAudioLane = useRef("");

  const exporting = exportJob?.status === "running" || exportBusy;
  // Anything that has the server holding this job — an export encoding, a Veo
  // batch rendering, or a captions/voiceover pass. All of them put it in
  // RUNNING, and `save_animatic` refuses to write through that, so the autosave
  // has to stand down for ALL of them. Separate from `exporting` on purpose:
  // that one drives the Export/Stop buttons and the progress strip, and a Veo
  // render must not make them say "export".
  //
  // ⚠ The captions pass is the one where standing down MATTERS MOST: it is the
  // server that writes the caption clips, so an autosave landing mid-run would
  // put the editor's older `texts` back over work that was paid for. That is
  // the same failure the Veo records were moved into `result` to avoid.
  const serverBusy = exporting || animating || speechRunning;

  // ------------------------------------------------------------- the project
  // Loading, autosave and "is this saved?" — see `useAnimaticProject`.
  //
  // ⚠ `onLoaded` is LATE-BOUND through a ref. It runs from inside the load
  // promise and needs `reconcileVeoClips` and the undo stack, both of which are
  // declared further down this file; the ref is assigned beside
  // `reconcileVeoClips`, which is the only place it makes sense to read it.
  const onLoadedRef = useRef(() => true);
  const {
    loading,
    title, setTitle,
    frames, setFrames,
    settings, setSettings,
    texts, setTexts,
    shapes, setShapes,
    layers, setLayers,
    overlays, setOverlays,
    transitions, setTransitions,
    audioTracks, setAudioTracks,
    video, setVideo,
    sourceBoard,
    veoClips, setVeoClips,
    doc, signature, applySnapshot,
    saveState, savedFlash, flush,
    loadedRef, dirtyRef, baselineRef,
  } = useAnimaticProject({
    animaticId,
    serverBusy,
    onLoaded: (p) => onLoadedRef.current(p),
    onError: setError,
  });

  const totalMs = useMemo(
    () => frames.reduce((sum, f) => sum + (f.duration_ms || 0), 0),
    [frames]
  );
  const starts = useMemo(() => {
    let t = 0;
    return frames.map((f) => {
      const start = t;
      t += f.duration_ms || 0;
      return start;
    });
  }, [frames]);

  // Exactly one thing is selected at a time, and the Properties pane follows it:
  // a transition, else a text clip, else a shape, else a track, else a frame,
  // else the video itself. Selecting one clears the others (see `selectOnly`),
  // so the pane can never show the wrong one.
  const selectedTransition =
    transitions.find((t) => t.id === selectedTransitionId) || null;
  const selectedText = selectedTransition
    ? null
    : texts.find((c) => c.id === selectedTextId) || null;
  const selectedShape =
    selectedTransition || selectedText
      ? null
      : shapes.find((s) => s.id === selectedShapeId) || null;
  const selectedOverlay =
    selectedTransition || selectedText || selectedShape
      ? null
      : overlays.find((o) => o.id === selectedOverlayId) || null;
  const selectedTrack =
    selectedTransition || selectedText || selectedShape || selectedOverlay
      ? null
      : audioTracks.find((a) => a.upload_id === selectedTrackId) || null;
  const selectedFrame =
    selectedTransition || selectedText || selectedShape || selectedOverlay || selectedTrack
      ? null
      : frames.find((f) => f.id === selectedId) || null;

  // One helper so every "select this" path clears the others — the pane can
  // then never show something that isn't selected.
  function selectOnly({
    frame = null,
    text = null,
    track = null,
    shape = null,
    overlay = null,
    transition = null,
  }) {
    setSelectedId(frame);
    setSelectedTextId(text);
    setSelectedTrackId(track);
    setSelectedShapeId(shape);
    setSelectedOverlayId(overlay);
    setSelectedTransitionId(transition);
  }

  // How long a track PLAYS: its trim, else the rest of the file after the
  // offset. The same rule as the timeline's `trackLength`.
  const playLength = (a) => {
    const rest = Math.max(0, (a.duration_ms || 0) - (a.offset_ms || 0));
    return a.trim_ms ? Math.min(a.trim_ms, rest || a.trim_ms) : rest;
  };
  // The longest track — what "fit frames to audio" matches, and what the
  // length comparison in the timeline header reports against.
  const audioMs = audioTracks.reduce((max, a) => Math.max(max, playLength(a)), 0);
  // How far the TIMELINE reaches. The video is still only as long as the frames
  // — that's what exports — but if the audio runs past them the timeline has to
  // show it, or you can't scrub into your own track to place pictures against it.
  const spanMs = Math.max(
    totalMs,
    audioMs,
    texts.reduce((max, c) => Math.max(max, c.start_ms + c.duration_ms), 0),
    shapes.reduce((max, s) => Math.max(max, s.start_ms + s.duration_ms), 0),
    overlays.reduce((max, o) => Math.max(max, o.start_ms + o.duration_ms), 0)
  );

  // Each track decoded once — the beats the timeline marks and snaps to, and
  // the voice envelope the duck follows while previewing. The waveforms read
  // the same cache, so a file is decoded once however many things want it.
  const audioAnalyses = useAudioAnalysis(audioUrls);

  // -------------------------------------------------------------- transport
  // The playhead and everything that moves it. It owns the CLOCK, and the scene
  // below is derived from that clock — which is why `useMonitorVideo`, the part
  // that pushes the scene into the monitor's <video> elements, is a separate
  // call made after the scene exists rather than part of this one.
  const {
    timeMs, timeRef,
    playing, rate,
    markIn, setMarkIn,
    markOut, setMarkOut,
    seek, togglePlay, stopPlayback, shuttle,
    stepOneFrame, gotoEditPoint,
  } = useTimelineTransport({
    frames,
    audioTracks,
    audioElsRef,
    audioUrls,
    audioAnalyses,
    spanMs,
    totalMs,
    // How long the EXPORT will be, which is where a fade out has to land. The
    // two options are the same number until the audio outlasts the pictures.
    exportMs: settings.end_at === "frames" ? totalMs : spanMs,
    starts,
    fps: settings.fps,
    onSelectFrame: setSelectedId,
  });

  // ------------------------------------------------------------------ scene
  // WHAT THE VIEWER SEES RIGHT NOW, and the single place that decides it.
  //
  // This used to be four separate derivations — one loop for which picture is
  // up, and three `useMemo`s filtering the text / shape / overlay lists by
  // time. They agreed with the exporter only because nothing moved; the moment
  // a property is keyframed, "which clips are visible" stops being the whole
  // answer and "what have their values become" starts to matter. `sceneAt` is
  // that answer, and `animatic_render.py` is the same function again in Python
  // so the MP4 shows what this pane shows. `tests/render_parity.py` proves it.
  //
  // `spanMs` goes in as the end so the LAST PICTURE IS HELD while a longer
  // audio track plays out — which is what the export has always done, and what
  // this preview previously did not.
  // The playhead can sit ON `spanMs`, which is one tick PAST the last picture —
  // a clip is alive up to but not including its end. Asking for the scene there
  // would correctly return no frame, and the monitor would go black at the end
  // of every playthrough. Reading the last visible instant instead is what a
  // person means by "parked at the end".
  const scene = useMemo(
    () =>
      sceneAt(
        { frames, texts, shapes, overlays, transitions },
        Math.min(timeMs, Math.max(0, spanMs - 1)),
        spanMs
      ),
    [frames, texts, shapes, overlays, transitions, timeMs, spanMs]
  );

  const currentIndex = scene.frame ? scene.frame.index : -1;
  const currentFrame = currentIndex >= 0 ? frames[currentIndex] : null;
  // The resolved picture — `currentFrame` with its pan/zoom/fade applied. The
  // two are different things and both are wanted: edits are written to the
  // stored frame, the preview draws the resolved one.
  const shownFrame = scene.frame;
  // The monitor is WebGL now, and a browser can refuse to give us a context —
  // an old machine, a blocked GPU, too many live contexts on one page. When it
  // does, say so ON the monitor rather than showing a black rectangle that
  // looks like a broken project, and say that the EXPORT is unaffected: the MP4
  // is rendered by Pillow on the server and never touches this.
  const [glFailed, setGlFailed] = useState(false);
  const activeTexts = scene.texts;
  const activeShapes = scene.shapes;
  const activeOverlays = scene.overlays;

  // The bundled caption faces. Registered once, here rather than at module
  // scope so nothing is injected into a document that may not exist yet.
  useEffect(() => {
    ensureFontsLoaded();
  }, []);

  // Does any clip come from a storyboard panel? A voiceover reads the BOARD's
  // dialogue, so on an animatic built from uploaded stills there is nothing to
  // read and never will be — better to disable the button and say why than to
  // let two clicks end in "there is no dialogue".
  const hasBoardFrames = useMemo(
    () => frames.some((f) => f.src?.kind === "panel" || f.src?.kind === "pose"),
    [frames]
  );

  const captionClass = (c) =>
    [
      "an-text-clip",
      `sz-${c.size || "medium"}`,
      `bd-${c.backdrop || "scrim"}`,
      `al-${c.align || "center"}`,
      selectedTextId === c.id ? "sel" : "",
    ].join(" ");

  // Nothing in it and never named — i.e. you opened it and did nothing. Leaving
  // such an animatic throws it away instead of leaving an empty "Untitled" on
  // the library forever.
  const isEmpty =
    !frames.length &&
    !texts.length &&
    !shapes.length &&
    !layers.length &&
    !overlays.length &&
    !transitions.length &&
    !audioTracks.length &&
    !video &&
    (!title.trim() || title.trim() === UNTITLED);
  // Has content but still carries the placeholder name, so Save should ask for
  // a real one first.
  const needsName = !title.trim() || title.trim() === UNTITLED;

  useMonitorVideo({ scene, frames, videoElsRef, playing, rate });

  // Steps by PICTURE, which is what the transport arrows mean — they sit next
  // to a "Frame 3 of 12" readout. It stays here rather than in the transport
  // hook because it needs `currentIndex`, and that comes from the scene, which
  // is derived from the clock the hook owns.
  const stepFrame = useCallback(
    (delta) => {
      if (!frames.length) return;
      const next = Math.max(0, Math.min(frames.length - 1, currentIndex + delta));
      setSelectedId(frames[next].id);
      seek(starts[next]);
    },
    [frames, currentIndex, starts, seek]
  );

  // ----------------------------------------------------------------- media
  // Fetch each frame's picture as an authed blob, a few at a time so a
  // 60-panel board doesn't open 60 sockets at once.
  useEffect(() => {
    let alive = true;
    const wanted = new Set(frames.map((f) => f.id));

    // Drop pictures for frames that no longer exist.
    for (const id of Object.keys(urlsRef.current)) {
      if (!wanted.has(id)) {
        URL.revokeObjectURL(urlsRef.current[id]);
        delete urlsRef.current[id];
      }
    }

    // A COLOUR CARD IS SKIPPED, not fetched. It has no file behind it, so the
    // url the server fills in for every frame alike can only 404 — one wasted
    // request per card on every load, and a thumbnail stuck on its spinner
    // waiting for a picture that is never coming.
    const missing = frames.filter(
      (f) => f.url && (f.kind || "image") !== "color" && !urlsRef.current[f.id]
    );
    if (!missing.length) {
      setUrls({ ...urlsRef.current });
      return;
    }

    (async () => {
      for (let i = 0; i < missing.length; i += 5) {
        if (!alive) return;
        const batch = missing.slice(i, i + 5);
        await Promise.all(
          batch.map(async (f) => {
            try {
              const url = await api.fetchAnimaticMedia(f.url);
              if (!alive) {
                URL.revokeObjectURL(url);
                return;
              }
              urlsRef.current[f.id] = url;
            } catch {
              /* a missing picture shows as an empty tile, not an error banner */
            }
          })
        );
        if (alive) setUrls({ ...urlsRef.current });
      }
    })();

    return () => {
      alive = false;
    };
  }, [frames]);

  // One blob per overlay picture, keyed by UPLOAD ID — several overlays can
  // point at the same file (a logo used four times), so keying by clip id would
  // fetch and hold the same bytes over and over.
  useEffect(() => {
    let alive = true;
    const wanted = new Set(overlays.map((o) => o.upload_id));
    for (const id of Object.keys(overlayUrlsRef.current)) {
      if (!wanted.has(id)) {
        URL.revokeObjectURL(overlayUrlsRef.current[id]);
        delete overlayUrlsRef.current[id];
      }
    }
    const missing = [...wanted].filter((id) => !overlayUrlsRef.current[id]);
    if (!missing.length) {
      setOverlayUrls({ ...overlayUrlsRef.current });
      return undefined;
    }
    (async () => {
      for (const uploadId of missing) {
        try {
          const url = await api.fetchAnimaticMedia(
            `/animatics/${animaticId}/media/${uploadId}`
          );
          if (!alive) {
            URL.revokeObjectURL(url);
            return;
          }
          overlayUrlsRef.current[uploadId] = url;
          setOverlayUrls({ ...overlayUrlsRef.current });
        } catch {
          /* a picture that won't load shows as an empty box, not a banner */
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [overlays, animaticId]);

  // One blob per VIDEO SOURCE, for the Program monitor.
  //
  // Fetched whole rather than streamed, which is deliberate: every media path in
  // this app is behind a bearer token, so a <video src> can't point straight at
  // the server. Pulling the file into a blob solves the auth AND makes scrubbing
  // instant — the browser seeks inside memory rather than issuing a range
  // request per playhead move. The cost is that a large clip is held in memory
  // while the editor is open, which is the right trade for a rough-cut tool.
  useEffect(() => {
    let alive = true;
    const wanted = new Set(
      frames
        .filter((f) => (f.kind || "image") === "video" && f.src?.upload_id)
        .map((f) => f.src.upload_id)
    );
    for (const id of Object.keys(videoUrlsRef.current)) {
      if (!wanted.has(id)) {
        URL.revokeObjectURL(videoUrlsRef.current[id]);
        delete videoUrlsRef.current[id];
        delete videoElsRef.current[id];
      }
    }
    const missing = [...wanted].filter((id) => !videoUrlsRef.current[id]);
    if (!missing.length) {
      setVideoUrls({ ...videoUrlsRef.current });
      return undefined;
    }
    (async () => {
      // One at a time: these are the biggest files in the project, and five
      // parallel 100MB fetches is a worse first impression than a slower one.
      for (const uploadId of missing) {
        try {
          const url = await api.fetchAnimaticMedia(
            `/animatics/${animaticId}/media/${uploadId}`
          );
          if (!alive) {
            URL.revokeObjectURL(url);
            return;
          }
          videoUrlsRef.current[uploadId] = url;
          setVideoUrls({ ...videoUrlsRef.current });
        } catch {
          /* a clip that won't load shows its thumbnail, not an error banner */
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [frames, animaticId]);

  // One blob per audio track, for the waveforms and for playback.
  useEffect(() => {
    let alive = true;
    const wanted = new Set(audioTracks.map((a) => a.upload_id));

    for (const id of Object.keys(audioUrlsRef.current)) {
      if (!wanted.has(id)) {
        // The decoded analysis is cached BY URL, so it has to go with the url —
        // otherwise a removed track's samples sit in memory for the life of the
        // page, keyed by a blob that no longer exists.
        forgetAudio(audioUrlsRef.current[id]);
        URL.revokeObjectURL(audioUrlsRef.current[id]);
        delete audioUrlsRef.current[id];
      }
    }

    const missing = audioTracks.filter((a) => !audioUrlsRef.current[a.upload_id]);
    if (!missing.length) {
      setAudioUrls({ ...audioUrlsRef.current });
      return undefined;
    }

    (async () => {
      for (const track of missing) {
        try {
          // Fetched by UPLOAD ID, not the project's /audio route: the save is
          // debounced, so straight after an upload the file is on disk but not
          // yet on the project, and /audio would 404 — which left the waveform
          // blank and playback silent until a reload.
          const url = await api.fetchAnimaticMedia(
            `/animatics/${animaticId}/media/${track.upload_id}`
          );
          if (!alive) {
            URL.revokeObjectURL(url);
            return;
          }
          audioUrlsRef.current[track.upload_id] = url;
          setAudioUrls({ ...audioUrlsRef.current });
        } catch {
          /* a track that won't load shows no waveform; it isn't an error banner */
        }
      }
    })();

    return () => {
      alive = false;
    };
  }, [audioTracks, animaticId]);

  // Object URLs are page-lifetime unless revoked — clean the lot up on the way out.
  useEffect(
    () => () => {
      for (const url of Object.values(urlsRef.current)) URL.revokeObjectURL(url);
      urlsRef.current = {};
      for (const url of Object.values(overlayUrlsRef.current)) URL.revokeObjectURL(url);
      overlayUrlsRef.current = {};
      for (const url of Object.values(videoUrlsRef.current)) URL.revokeObjectURL(url);
      videoUrlsRef.current = {};
      videoElsRef.current = {};
      for (const url of Object.values(audioUrlsRef.current)) {
        forgetAudio(url);
        URL.revokeObjectURL(url);
      }
      audioUrlsRef.current = {};
    },
    []
  );

  // ----------------------------------------------------------------- lanes
  // ONE list describing every row on the timeline, in top-to-bottom order. The
  // gutter labels and the tracks are both generated from it, so a label can
  // never end up beside the wrong lane (which is exactly what happened when the
  // two were written out separately and matched by position).
  //
  // Order is by KIND — pictures, images over them, text, shapes, audio — and
  // within a kind the DEFAULT lane comes first, then the ones the user added.
  const lanes = useMemo(() => {
    const of = (kind) => layers.filter((l) => l.kind === kind);
    const out = [
      { key: "frames", kind: "frames", name: "Images", layerId: null, removable: false },
    ];
    for (const l of of("image")) {
      out.push({ key: l.id, kind: "image", name: l.name, layerId: l.id, removable: true });
    }
    out.push({ key: "text:", kind: "text", name: "Text", layerId: "", removable: false });
    for (const l of of("text")) {
      out.push({ key: l.id, kind: "text", name: l.name, layerId: l.id, removable: true });
    }
    out.push({ key: "shape:", kind: "shape", name: "Shapes", layerId: "", removable: false });
    for (const l of of("shape")) {
      out.push({ key: l.id, kind: "shape", name: l.name, layerId: l.id, removable: true });
    }
    // Audio: a track saved before layers owns its own lane (that is how it has
    // always been drawn); a track added to a layer sits on that layer's lane,
    // which exists even while it is still empty.
    const loose = audioTracks.filter((a) => !a.layer_id);
    for (const track of loose) {
      out.push({
        key: track.upload_id,
        kind: "audio",
        name: track.filename,
        layerId: "",
        track,
        removable: false,
      });
    }
    for (const l of of("audio")) {
      const track = audioTracks.find((a) => a.layer_id === l.id) || null;
      out.push({
        key: l.id,
        kind: "audio",
        name: track ? track.filename : l.name,
        layerId: l.id,
        track,
        removable: true,
      });
    }
    // With no audio at all, keep the empty band that has always been there —
    // it is the obvious place to click to add some.
    if (!loose.length && !of("audio").length) {
      out.push({ key: "audio:", kind: "audio", name: "Audio", layerId: "", track: null, removable: false });
    }
    return out;
  }, [layers, audioTracks]);

  // ------------------------------------------------------------ undo / redo
  // One stack for the whole document — see `useUndoStack`. `gestureProps` is
  // spread onto anything draggable so a whole drag is a single Ctrl+Z, and
  // `resetHistory` is called once the project has loaded, because anything
  // recorded before that describes an editor that hadn't loaded yet.
  const {
    undo,
    redo,
    canUndo,
    canRedo,
    setGesture,
    gestureProps,
    reset: resetHistory,
  } = useUndoStack({
    doc,
    signature,
    loadedRef,
    apply: applySnapshot,
    onNotice: setNotice,
  });

  // ------------------------------------------------------------ add an edit
  // The razor: split the picture at `ms` into two frames that add up to the
  // same hold. Both halves point at the SAME source image — that is what
  // cutting a still means, and nothing is uploaded twice.
  const splitFrameAt = useCallback(
    (ms) => {
      if (!frames.length) return;
      let i = -1;
      for (let k = frames.length - 1; k >= 0; k--) {
        if (ms >= starts[k]) {
          i = k;
          break;
        }
      }
      if (i < 0) return;
      const source = frames[i];
      const offset = Math.round(ms - starts[i]);
      if (offset < MIN_MS || source.duration_ms - offset < MIN_MS) {
        setNotice(
          `Too close to a cut — each side of an edit needs at least ${MIN_MS}ms.`
        );
        return;
      }
      const tail = { ...source, id: newId(), duration_ms: source.duration_ms - offset };
      setFrames((list) => {
        const next = [...list];
        next.splice(i, 1, { ...source, duration_ms: offset }, tail);
        return next;
      });
      // ⚠ The head keeps the source's id, so a transition anchored to it would
      // silently jump to the NEW cut in the middle of the split. The edit point
      // it was put on still exists — it now follows the second half.
      setTransitions((list) =>
        list.map((t) =>
          t.after_frame_id === source.id ? { ...t, after_frame_id: tail.id } : t
        )
      );
      setNotice("Cut — that picture is now two frames you can time separately.");
    },
    [frames, starts]
  );

  // Delete whatever is selected, in the same order the Properties pane picks
  // what to show — so Delete always removes the thing the pane is describing,
  // which is the only reading of "the selection" a person can act on.
  const deleteSelection = useCallback(() => {
    if (selectedTransition) {
      deleteTransition(selectedTransition.id);
    } else if (selectedText) {
      deleteText(selectedText.id);
      setNotice("Text clip deleted.");
    } else if (selectedShape) {
      deleteShape(selectedShape.id);
      setNotice("Shape deleted.");
    } else if (selectedOverlay) {
      deleteOverlay(selectedOverlay.id);
      setNotice("Picture removed from the layer.");
    } else if (selectedTrack) {
      removeTrack(selectedTrack.upload_id);
    } else if (selectedFrame) {
      const at = frames.findIndex((f) => f.id === selectedFrame.id);
      deleteFrame(selectedFrame.id);
      // Land on the neighbour rather than nothing, so Delete-Delete-Delete
      // works down a sequence without reaching for the mouse in between.
      const next = frames[at + 1] || frames[at - 1];
      if (next) setSelectedId(next.id);
      setNotice("Frame removed from the sequence.");
    } else {
      setNotice("Nothing is selected — click a frame, clip or shape first.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    selectedTransition, selectedText, selectedShape, selectedOverlay,
    selectedTrack, selectedFrame, frames,
  ]);

  // ------------------------------------------------------------- shortcuts
  // Premiere's keys, for the things this editor actually has. Deliberately NO
  // Pen tool (P): a pen pulls keyframes, and an animatic has none to pull.
  //
  // No dependency array on purpose — the handler closes over state that changes
  // every render, and re-binding is cheaper than a stale closure.
  useEffect(() => {
    function onKey(e) {
      const tag = e.target?.tagName;
      const typing =
        tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable;
      // Let a native shortcut through unless it's one we deliberately take.
      const mod = e.ctrlKey || e.metaKey;
      // While typing, the field owns the keyboard — including its own Ctrl+Z.
      // Ctrl+S is the one exception: "save my work" means the project wherever
      // the cursor happens to be, and the browser's Save Page dialog never
      // helps here.
      if (typing && !(mod && e.code === "KeyS")) return;

      if (mod) {
        switch (e.code) {
          case "KeyS":
            e.preventDefault();
            handleSave();
            return;
          case "KeyZ":
            e.preventDefault();
            // Shift+Ctrl+Z redoes, as it does everywhere else.
            (e.shiftKey ? redo : undo)();
            return;
          case "KeyY":
            e.preventDefault();
            redo();
            return;
          case "KeyK":
            e.preventDefault();
            splitFrameAt(timeRef.current);
            return;
          case "KeyX":
            if (e.shiftKey) {
              e.preventDefault();
              setMarkIn(null);
              setMarkOut(null);
              setNotice("In and out marks cleared.");
            }
            return;
          default:
            return; // every other Ctrl combo is the browser's
        }
      }

      if (e.altKey) return;

      switch (e.code) {
        // --- playback ---
        case "Space":
          e.preventDefault();
          togglePlay();
          return;
        case "KeyL":
          e.preventDefault();
          shuttle(1);
          return;
        case "KeyJ":
          e.preventDefault();
          shuttle(-1);
          return;
        case "KeyK":
          e.preventDefault();
          stopPlayback();
          return;
        case "ArrowRight":
          e.preventDefault();
          stepOneFrame(1);
          return;
        case "ArrowLeft":
          e.preventDefault();
          stepOneFrame(-1);
          return;
        case "ArrowUp":
          e.preventDefault();
          gotoEditPoint(-1);
          return;
        case "ArrowDown":
          e.preventDefault();
          gotoEditPoint(1);
          return;

        // --- delete the selection ---
        // Backspace too: on a Mac keyboard that IS the delete key, and it
        // otherwise navigates the page back, which loses the editor.
        case "Delete":
        case "Backspace":
          e.preventDefault();
          deleteSelection();
          return;

        // --- marks ---
        case "KeyI":
          e.preventDefault();
          setMarkIn(Math.round(timeRef.current));
          // An in-point past the out-point is nonsense; drop the stale one.
          setMarkOut((o) => (o !== null && o <= timeRef.current ? null : o));
          return;
        case "KeyO":
          e.preventDefault();
          setMarkOut(Math.round(timeRef.current));
          setMarkIn((i) => (i !== null && i >= timeRef.current ? null : i));
          return;

        // --- the rest ---
        case "KeyS":
          e.preventDefault();
          setSnapping((s) => !s);
          return;
        case "Backquote":
          e.preventDefault();
          setMaximized((m) => (m ? null : hoverPaneRef.current));
          return;
        default:
          break;
      }

      // Tools last, so a tool letter can never shadow one of the above.
      const picked = TOOLS.find((t) => `Key${t.key}` === e.code);
      if (picked) {
        e.preventDefault();
        setTool(picked.id);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ---------------------------------------------------------- frame edits
  const patchFrame = (id, patch) =>
    setFrames((list) => list.map((f) => (f.id === id ? { ...f, ...patch } : f)));

  function reorder(from, to) {
    setFrames((list) => {
      const next = [...list];
      const [moved] = next.splice(from, 1);
      next.splice(to > from ? to - 1 : to, 0, moved);
      return next;
    });
  }

  function duplicateFrame(id) {
    const copyId = newId();
    setFrames((list) => {
      const i = list.findIndex((f) => f.id === id);
      if (i < 0) return list;
      const copy = { ...list[i], id: copyId };
      // The picture is identical, so point the new frame at the same source —
      // its blob is fetched from the same URL and nothing is uploaded twice.
      const next = [...list];
      next.splice(i + 1, 0, copy);
      return next;
    });
    // The copy lands between this frame and whatever followed it, so the cut a
    // transition was put on is now the COPY's. Left where it was it would sit
    // between two identical pictures and dissolve invisibly.
    setTransitions((list) =>
      list.map((t) => (t.after_frame_id === id ? { ...t, after_frame_id: copyId } : t))
    );
  }

  /**
   * Transitions that still have a cut to live on.
   *
   * A transition is anchored to the frame it FOLLOWS, so it survives exactly as
   * long as that frame does AND something comes after it. Deleting the frame
   * takes the cut away; deleting everything past it does too. Both are pruned
   * here rather than left as inert records — the renderer would ignore them
   * either way, but a Properties pane describing a transition you can't see is
   * worse than not having it.
   */
  const pruneTransitions = (list, frameList) => {
    const ids = new Set(frameList.map((f) => f.id));
    const lastId = frameList[frameList.length - 1]?.id;
    return list.filter((t) => ids.has(t.after_frame_id) && t.after_frame_id !== lastId);
  };

  function deleteFrame(id) {
    // ⚠ The next list is computed HERE rather than inside the updater: writing
    // to a second piece of state from inside a `setFrames(current => …)` is a
    // setState-during-render, which React runs twice in StrictMode. Same rule
    // the timeline's drags follow.
    const next = frames.filter((f) => f.id !== id);
    setFrames(next);
    setTransitions((list) => pruneTransitions(list, next));
    setSelectedId((s) => (s === id ? null : s));
  }

  // ------------------------------------------------------ transitions
  // What happens on a cut. Boundary-local: the blend straddles the edit point,
  // so adding one leaves the timeline exactly as long and every other clip
  // exactly where it was. See `client/src/animatic/transitions.js`.
  const patchTransition = (id, patch) =>
    setTransitions((list) => list.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  function addTransition(afterFrameId) {
    // One per cut. Pressing ＋ on a cut that already has one selects it rather
    // than stacking a second, which would make the render depend on list order.
    const existing = transitions.find((t) => t.after_frame_id === afterFrameId);
    if (existing) {
      selectOnly({ transition: existing.id });
      return;
    }
    const transition = {
      id: newId(),
      after_frame_id: afterFrameId,
      kind: "dissolve",
      duration_ms: DEFAULT_TRANSITION_MS,
    };
    setTransitions((list) => [...list, transition]);
    selectOnly({ transition: transition.id });
    setNotice(
      "Dissolve added on that cut — it blends across the edit without making the video any longer."
    );
  }

  function deleteTransition(id) {
    setTransitions((list) => list.filter((t) => t.id !== id));
    setSelectedTransitionId((s) => (s === id ? null : s));
    setNotice("Transition removed — that edit is a straight cut again.");
  }

  // ----------------------------------------------------------- text layer
  // A new clip covers the frame the playhead is sitting on — "add text to this
  // shot" is what people mean nine times out of ten — but it is a free-floating
  // clip from that moment on, so it can be dragged and stretched anywhere.
  // Anything that isn't a string is not a lane id — most likely a React event
  // from a handler passed bare to onClick. Orphaning a clip on a lane that
  // doesn't exist makes it invisible AND unreachable, so it is worth refusing.
  const laneId = (value) => (typeof value === "string" ? value : "");

  function addText(layerId = "") {
    const i = currentIndex >= 0 ? currentIndex : 0;
    const start = frames.length ? starts[i] : 0;
    const length = frames.length ? frames[i].duration_ms : 2000;
    const clip = { ...newTextClip(start, length), layer_id: laneId(layerId) };
    setTexts((list) => [...list, clip]);
    setSelectedTextId(clip.id);
    seek(start);
    setNotice("Text added over this frame — type it below, then drag its edge to re-time it.");
  }

  const patchText = (id, patch) =>
    setTexts((list) => list.map((c) => (c.id === id ? { ...c, ...patch } : c)));

  function deleteText(id) {
    setTexts((list) => list.filter((c) => c.id !== id));
    setSelectedTextId((s) => (s === id ? null : s));
  }

  function duplicateText(id) {
    setTexts((list) => {
      const source = list.find((c) => c.id === id);
      if (!source) return list;
      const copy = { ...source, id: newId(), start_ms: source.start_ms + source.duration_ms };
      setSelectedTextId(copy.id);
      return [...list, copy];
    });
  }

  // ---------------------------------------------------------------- layers
  // "+ Add layer" makes a BLANK lane and stops there. It used to add content —
  // an upload dialog for images, a caption, a shape — which is not what adding
  // a layer means: you add the row, then you put things on it.
  const LAYER_NAMES = { image: "Images", text: "Text", shape: "Shapes", audio: "Audio" };

  function addLayer(kind) {
    const taken = layers.filter((l) => l.kind === kind).length;
    const layer = {
      id: newId(),
      kind,
      // Numbered from 2 because the default lane of that kind is already "Text",
      // "Shapes", … on screen — so the first ADDED one reads as the second row.
      name: `${LAYER_NAMES[kind] || "Layer"} ${taken + 2}`,
    };
    setLayers((list) => [...list, layer]);
    setNotice(
      `Empty ${LAYER_NAMES[kind]?.toLowerCase() || ""} layer added — use its ＋ to put something on it.`
    );
    return layer;
  }

  // Removing a lane takes its contents with it: they have nowhere else to live,
  // and silently moving them to another row would be worse than saying so.
  function removeLayer(layerId) {
    const layer = layers.find((l) => l.id === layerId);
    if (!layer) return;
    setLayers((list) => list.filter((l) => l.id !== layerId));
    if (layer.kind === "text") setTexts((list) => list.filter((c) => c.layer_id !== layerId));
    if (layer.kind === "shape") setShapes((list) => list.filter((s) => s.layer_id !== layerId));
    if (layer.kind === "image") setOverlays((list) => list.filter((o) => o.layer_id !== layerId));
    if (layer.kind === "audio")
      setAudioTracks((list) => list.filter((a) => a.layer_id !== layerId));
    setNotice("Layer removed.");
  }

  // The ＋ on a lane. ONE entry point, so "add to this row" behaves the same
  // whether it is pressed in the gutter or on the empty band of the track.
  // Which lane it was pressed on decides what gets added, and where.
  const pendingOverlayLane = useRef("");

  function addToLane(lane) {
    if (lane.kind === "frames") {
      imageInputRef.current?.click();
      return;
    }
    if (lane.kind === "text") {
      addText(lane.layerId || "");
      return;
    }
    if (lane.kind === "shape") {
      // No sensible "default shape" — open the picker and let them choose.
      setPendingShapeLane(lane.layerId || "");
      setMediaTab("shapes");
      setNotice("Pick a shape to put on this layer.");
      return;
    }
    if (lane.kind === "image") {
      pendingOverlayLane.current = lane.layerId || "";
      overlayInputRef.current?.click();
      return;
    }
    if (lane.kind === "audio") {
      pendingAudioLane.current = lane.layerId || "";
      openAudioPicker();
    }
  }

  // -------------------------------------------------------- image overlays
  // A picture composited over the sequence. Same geometry as a shape, because
  // it is placed with the same handles — only the fill differs.
  const patchOverlay = (id, patch) =>
    setOverlays((list) => list.map((o) => (o.id === id ? { ...o, ...patch } : o)));

  function deleteOverlay(id) {
    setOverlays((list) => list.filter((o) => o.id !== id));
    setSelectedOverlayId((s) => (s === id ? null : s));
  }

  function duplicateOverlay(id) {
    setOverlays((list) => {
      const source = list.find((o) => o.id === id);
      if (!source) return list;
      const copy = { ...source, id: newId(), start_ms: source.start_ms + source.duration_ms };
      setSelectedOverlayId(copy.id);
      return [...list, copy];
    });
  }

  // Upload pictures INTO an image layer. They land at the playhead, a third of
  // the frame wide, and are dragged from there — unlike a frame, an overlay has
  // no place in the sequence to be added to.
  async function addOverlayFiles(files, layerId) {
    const images = [...files].filter((f) => kindOf(f) === "image");
    if (!images.length) {
      setNotice("An image layer takes pictures — that file isn't one.");
      return;
    }
    setUploading(true);
    setError("");
    try {
      const res = await api.uploadAnimaticImages(animaticId, images);
      const start = Math.round(timeRef.current);
      const added = (res.items || []).map((item, i) => ({
        id: newId(),
        layer_id: laneId(layerId),
        upload_id: item.upload_id,
        start_ms: start + i * 2000,
        duration_ms: 2000,
        x: 0.5,
        y: 0.5,
        w: 0.3,
        h: 0.3,
        opacity: 1,
        rotation: 0,
        url: `/animatics/${animaticId}/media/${item.upload_id}`,
      }));
      setOverlays((list) => [...list, ...added]);
      if (added.length) selectOnly({ overlay: added[added.length - 1].id });
      setNotice(
        `Added ${added.length} picture${added.length === 1 ? "" : "s"} to this layer — drag to place ${added.length === 1 ? "it" : "them"} on the frame.`
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  }

  // ---------------------------------------------------------- shape layer
  // Like a caption, a new shape covers the frame the playhead is on, and is a
  // free-floating clip from that moment on.
  function addShape(kind, layerId = "") {
    const i = currentIndex >= 0 ? currentIndex : 0;
    const start = frames.length ? starts[i] : 0;
    const length = frames.length ? frames[i].duration_ms : 2000;
    const shape = { ...newShape(kind, start, length), layer_id: laneId(layerId) };
    setShapes((list) => [...list, shape]);
    selectOnly({ shape: shape.id });
    seek(start);
    setNotice("Shape added — drag it on the picture to move it, or its corner to resize.");
  }

  const patchShape = (id, patch) =>
    setShapes((list) => list.map((s) => (s.id === id ? { ...s, ...patch } : s)));

  // ------------------------------------------------------------- keyframes
  // WHAT IS BEING INSPECTED, in one description. The Properties pane already
  // shows exactly one thing at a time (see `selectOnly`), so the keyframe
  // plumbing is written once here rather than four times in four panes — and
  // adding a fifth kind of clip later means adding one line to this list.
  //
  // `startMs` is the piece that cannot be guessed: key times are relative to
  // the clip's own start, and for a FRAME that start is its position in the
  // sequence, not a field on it.
  const inspected = useMemo(() => {
    if (selectedText)
      return { clip: selectedText, kind: "text", patch: patchText, startMs: selectedText.start_ms };
    if (selectedOverlay)
      return {
        clip: selectedOverlay,
        kind: "overlay",
        patch: patchOverlay,
        startMs: selectedOverlay.start_ms,
      };
    if (selectedShape)
      return { clip: selectedShape, kind: "shape", patch: patchShape, startMs: selectedShape.start_ms };
    if (selectedFrame) {
      const i = frames.findIndex((f) => f.id === selectedFrame.id);
      return { clip: selectedFrame, kind: "frame", patch: patchFrame, startMs: starts[i] ?? 0 };
    }
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedText, selectedOverlay, selectedShape, selectedFrame, frames, starts]);

  // The playhead, in the inspected clip's own time.
  const kfTime = inspected ? timeMs - inspected.startMs : 0;

  /**
   * Write values to a clip, turning them into KEYS where the property is animated.
   *
   * THE ONE RULE THAT MAKES THIS AN ANIMATION TOOL: while a property is
   * animated, setting a value writes a key at the playhead instead of changing
   * the value everywhere. Without it the stopwatch would be a light that does
   * nothing — you could turn animation on and then have no way to say what the
   * value should become. Un-animated properties are written straight through,
   * exactly as before, so nothing changes for a clip nobody has keyframed.
   *
   * ⚠ Takes the STORED clip and an explicit time, rather than reading the
   * selection. Both matter: `sceneAt` hands the preview RESOLVED clips (whose
   * `x` is where the shape is right now, not what is saved on it), and a drag
   * that has only just called `selectOnly` is running one render ahead of
   * `inspected`. Passing both in is what keeps the two callers honest.
   */
  function writeAnimatable(kind, stored, startMs, write, values, atMs) {
    // An explicit `keyframes` in the patch means the caller is managing the
    // animation itself — "Reset motion" clears the curves AND the values in one
    // press, and turning those values into keys on the curves being deleted
    // would be the exact opposite of what it says.
    if ("keyframes" in values) return write(stored.id, values);
    // ⚠ `lookProps` is what makes an EFFECT PARAMETER animate like everything
    // else. A grade's tracks are named per clip ("fx:e3:amount"), so the list
    // cannot be a constant the way ANIMATABLE is — but once it is here, typing
    // a LUT strength while its stopwatch is on writes a key exactly as typing a
    // zoom does, and nothing below this line knows the difference.
    const animatable = LOOK_KINDS.includes(kind)
      ? [...(ANIMATABLE[kind] || []), ...lookProps(stored)]
      : ANIMATABLE[kind] || [];
    const tRel = (atMs ?? timeMs) - startMs;
    const plain = {};
    // Keys accumulate against a clip that already carries the previous ones: a
    // single write can touch two animated properties (dragging a shape moves x
    // and y together) and each `setKey` must see the other's work.
    let working = stored;
    let touchedKeys = false;
    let touchedLook = false;
    for (const [prop, value] of Object.entries(values)) {
      if (animatable.includes(prop) && isAnimatedProp(stored, prop)) {
        working = { ...working, ...setKey(working, prop, tRel, value) };
        touchedKeys = true;
      } else if (lookPropParts(prop)) {
        // Not animated, so it is written where it LIVES — inside the effect or
        // the mask — rather than as a flat key the schema would drop. See
        // `setLookValue` for why that distinction is worth a function.
        working = { ...working, ...setLookValue(working, prop, value) };
        touchedLook = true;
      } else {
        plain[prop] = value;
      }
    }
    const patch = { ...plain };
    if (touchedKeys) patch.keyframes = working.keyframes;
    if (touchedLook) {
      if (working.effects) patch.effects = working.effects;
      if (working.mask) patch.mask = working.mask;
    }
    write(stored.id, patch);
  }

  /** The change handler the Properties pane gets — `writeAnimatable` bound to the selection. */
  function patchInspected(id, patch) {
    if (!inspected || inspected.clip.id !== id) return;
    writeAnimatable(inspected.kind, inspected.clip, inspected.startMs, inspected.patch, patch);
  }

  // The four things the ⏱ row can ask for.
  // A look property's fallback lives INSIDE the effect or the mask, not on the
  // clip, so the two cases are asked differently — everything downstream then
  // treats them identically.
  const kfFallback = (clip, kind, prop) =>
    lookPropParts(prop) ? lookValueOf(clip, prop) : defaultFor(kind, prop);

  const kfHandlers = {
    onToggle: (prop) => {
      const { clip, kind, patch } = inspected;
      const fallback = kfFallback(clip, kind, prop);
      if (!isAnimatedProp(clip, prop)) {
        return patch(clip.id, enableProp(clip, prop, kfTime, fallback));
      }
      const off = disableProp(clip, prop, kfTime, fallback);
      if (lookPropParts(prop)) {
        // ⚠ Freeze it back into the EFFECT, not onto the clip. `disableProp`
        // returns `{[prop]: frozen}`, which for a look track would be a flat
        // key the schema has no field for and drops on the next save — so
        // switching the stopwatch off would look like it worked and quietly
        // lose the value you were standing on.
        const frozen = off[prop];
        delete off[prop];
        Object.assign(off, setLookValue(clip, prop, frozen));
      }
      patch(clip.id, off);
    },
    onKey: (prop, add) => {
      const { clip, kind, patch } = inspected;
      const fallback = kfFallback(clip, kind, prop);
      patch(
        clip.id,
        add
          ? // A key added by the diamond takes the value that is ALREADY on
            // screen at this instant — adding one must never change the picture.
            setKey(clip, prop, kfTime, valueAt(clip, prop, kfTime, clip[prop] ?? fallback))
          : removeKey(clip, prop, kfTime)
      );
    },
    onSeekKey: (prop, dir) => {
      const key = neighbourKey(inspected.clip, prop, kfTime, dir);
      if (key) seek(inspected.startMs + key.t);
    },
    onEase: (prop, ease) =>
      inspected.patch(inspected.clip.id, setKeyEase(inspected.clip, prop, kfTime, ease)),
  };

  /**
   * Re-time one keyframe, dragged on the timeline.
   *
   * Deliberately NOT routed through `inspected`: you can drag a key on a clip
   * that isn't selected, and the timeline is the one that knows which lane was
   * grabbed. Both operations return null when there is no key at `fromT` — a
   * stale drag against a clip that has since changed — and that is left as a
   * no-op rather than written as an empty patch.
   *
   * `prop` is the row the diamond was on, so a plain drag re-times THAT
   * property and nothing else — which is what the row means, and what you can
   * see. `all` (shift-drag) moves every property keyed at that instant instead,
   * so a Ken Burns push can still be slid along without pulling `scale` and
   * `x` apart.
   */
  const moveKeyframe = useCallback(
    (kind, id, fromT, toT, prop, all) => {
      const [list, write] =
        kind === "frames"
          ? [frames, patchFrame]
          : kind === "text"
            ? [texts, patchText]
            : kind === "shape"
              ? [shapes, patchShape]
              : [overlays, patchOverlay];
      const clip = list.find((c) => c.id === id);
      if (!clip) return;
      const next =
        all || !prop
          ? moveKeysAt(clip, fromT, toT)
          : moveKey(clip, prop, fromT, toT);
      if (next) write(id, next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [frames, texts, shapes, overlays]
  );

  // Handed to every Properties pane as one prop, so a pane renders a ⏱ with
  // `<KeyframeControls {...kf} prop="opacity" />` and knows nothing else.
  // ⚠ `clip` here is the STORED clip — the one that owns the keyframes, which is
  // what every operation in `keyframes.js` needs.
  const kf = inspected ? { clip: inspected.clip, tRel: kfTime, ...kfHandlers } : null;

  /**
   * The inspected clip with its animated properties resolved AT THE PLAYHEAD —
   * what the panes display.
   *
   * The stored clip is the wrong thing to show: keyframe a zoom from 100% to
   * 200% and the stored `scale` stays 1, so parked half way along, the field
   * would read "100%" while the picture plainly shows 150%. An inspector that
   * disagrees with the monitor is worse than no inspector. Editing still writes
   * through the stored clip — `writeAnimatable` takes that one explicitly — so
   * this is a display concern only.
   *
   * `sceneAt`'s resolved clips can't be reused for this: they only exist while
   * the playhead is inside the clip, and you can perfectly well select a frame
   * and then scrub somewhere else.
   */
  const inspectedShown = useMemo(() => {
    if (!inspected) return null;
    const out = { ...inspected.clip };
    for (const prop of ANIMATABLE[inspected.kind] || []) {
      out[prop] = valueAt(inspected.clip, prop, kfTime, defaultFor(inspected.kind, prop));
    }
    // The grade, resolved at the playhead for the same reason: a LUT keyframed
    // from 0 to 1 would otherwise read "0%" in the pane while the monitor
    // plainly showed it half way in.
    if (LOOK_KINDS.includes(inspected.kind)) {
      Object.assign(out, resolveLook(inspected.clip, kfTime));
    }
    return out;
  }, [inspected, kfTime]);

  // The LOOK rows, built once and slotted into whichever pane is showing. Only
  // the two clip kinds that are PICTURES get them — a shape is vector and a
  // caption is text, and neither has pixels of its own to grade. Passed as a
  // node rather than a flag so the panes stay presentational and neither of
  // them has to know what an effect is.
  const lookPanel =
    inspected && LOOK_KINDS.includes(inspected.kind) ? (
      <EffectsPanel
        clip={inspectedShown}
        stored={inspected.clip}
        kf={kf}
        gesture={gestureProps}
        onChange={patchInspected}
      />
    ) : null;

  function deleteShape(id) {
    setShapes((list) => list.filter((s) => s.id !== id));
    setSelectedShapeId((s) => (s === id ? null : s));
  }

  function duplicateShape(id) {
    setShapes((list) => {
      const source = list.find((s) => s.id === id);
      if (!source) return list;
      const copy = { ...source, id: newId(), start_ms: source.start_ms + source.duration_ms };
      setSelectedShapeId(copy.id);
      return [...list, copy];
    });
  }

  // Dragging a shape ON THE PICTURE. Everything is computed as a fraction of
  // the preview box, which is the same unit the project stores and the exporter
  // draws in — so what you drag to is what gets encoded, at any resolution.
  const screenRef = useRef(null);

  // `kind` is "shape" or "overlay": the geometry and the handles are identical
  // (that is the point — a picture is placed exactly like a box), so one drag
  // implementation serves both and they cannot drift apart.
  function startShapeDrag(e, shape, mode, kind = "shape") {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const write = kind === "overlay" ? patchOverlay : patchShape;
    selectOnly(kind === "overlay" ? { overlay: shape.id } : { shape: shape.id });
    const box = screenRef.current?.getBoundingClientRect();
    if (!box || !box.width || !box.height) return;

    // ⚠ `shape` here is the RESOLVED clip from `sceneAt` — its x/y/w/h are where
    // the shape is on screen at this instant, which is exactly what a drag must
    // start from. The STORED clip is a different object, and it is the one that
    // owns the keyframes, so both are needed: drag from what you see, write to
    // what is saved.
    const stored =
      (kind === "overlay" ? overlays : shapes).find((s) => s.id === shape.id) || shape;
    // The playhead is captured ONCE. A drag lands its keys where it began, not
    // wherever the clock has crept to by the time the pointer comes up.
    const at = timeMs;
    const patch = (id, values) =>
      writeAnimatable(kind === "overlay" ? "overlay" : "shape", stored, stored.start_ms, write, values, at);

    const startX = e.clientX;
    const startY = e.clientY;
    const from = { x: shape.x, y: shape.y, w: shape.w, h: shape.h };
    // The corner OPPOSITE the handle stays put while resizing — the behaviour
    // every editor has, and the reason the centre has to move with the size.
    const anchorX = from.x - from.w / 2;
    const anchorY = from.y - from.h / 2;

    const move = (ev) => {
      const dx = (ev.clientX - startX) / box.width;
      const dy = (ev.clientY - startY) / box.height;
      if (mode === "move") {
        // Clamped so a shape can be run half off the frame but never lost
        // entirely off it, which would leave an unreachable clip on the timeline.
        patch(shape.id, {
          x: clamp(from.x + dx, -0.5, 1.5),
          y: clamp(from.y + dy, -0.5, 1.5),
        });
      } else {
        const w = clamp(from.w + dx, 0.02, 4);
        const h = clamp(from.h + dy, 0.02, 4);
        patch(shape.id, { w, h, x: anchorX + w / 2, y: anchorY + h / 2 });
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setGesture(false);
    };
    setGesture(true);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // Typing in the caption box should focus it as soon as a clip is picked.
  useEffect(() => {
    if (selectedTextId) textAreaRef.current?.focus();
  }, [selectedTextId]);

  async function addFiles(files, insertAt) {
    if (!files.length) return;
    setUploading(true);
    setError("");
    try {
      const res = await api.uploadAnimaticImages(animaticId, files);
      const added = (res.items || []).map((item) => ({
        id: newId(),
        src: { kind: "upload", upload_id: item.upload_id },
        duration_ms: 2000,
        label: "",
        // Uploads are servable immediately, before the project is saved.
        url: `/animatics/${animaticId}/media/${item.upload_id}`,
      }));
      setFrames((list) => {
        const at = insertAt === undefined ? list.length : insertAt;
        const next = [...list];
        next.splice(at, 0, ...added);
        return next;
      });
      if (added.length && !selectedId) setSelectedId(added[0].id);
      if (res.rejected?.length) {
        setNotice(`Skipped ${res.rejected.length}: ${res.rejected.join(", ")}`);
      } else {
        setNotice(`Added ${added.length} image${added.length === 1 ? "" : "s"}.`);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  }

  /**
   * Upload video files and drop them onto the picture track as clips.
   *
   * A clip opens at its FULL natural length with `out_ms` set to the end of the
   * file, which is what "I dropped a 6-second take in" should mean. When the
   * server couldn't measure the file (`duration_ms: 0`) the clip falls back to
   * the default hold and `out_ms` stays null — the source simply runs on, and
   * the user can trim it by hand.
   *
   * Returns how many landed, so the one shared "here's what I did with your
   * files" notice can report them alongside the images and audio.
   */
  async function addVideoClips(files, insertAt) {
    if (!files.length) return 0;
    setUploading(true);
    setError("");
    try {
      const res = await api.uploadAnimaticVideos(animaticId, files);
      const added = (res.items || []).map((item) =>
        newVideoClip(
          item.upload_id,
          item.duration_ms,
          (item.filename || "").replace(/\.[^.]+$/, "")
        )
      );
      setFrames((list) => {
        const at = insertAt === undefined ? list.length : insertAt;
        const next = [...list];
        next.splice(at, 0, ...added);
        return next;
      });
      if (added.length && !selectedId) setSelectedId(added[0].id);
      if (res.rejected?.length) {
        setNotice(`Skipped ${res.rejected.length}: ${res.rejected.join(", ")}`);
      }
      return added.length;
    } catch (e) {
      setError(e.message);
      return 0;
    } finally {
      setUploading(false);
    }
  }

  // A colour card: a clip with no file at all. Dropped in at the playhead's
  // clip, or at the end, like every other insert here.
  function addColorCard() {
    const card = newColorClip("#000000", 1000);
    setFrames((list) => {
      const at = currentIndex >= 0 ? currentIndex + 1 : list.length;
      const next = [...list];
      next.splice(at, 0, card);
      return next;
    });
    selectOnly({ frame: card.id });
    setNotice("Added a colour card.");
  }

  // Opening the OS file dialog is the whole action for the audio layer, so both
  // entry points (the tools row and the ＋ on the Audio track) share this.
  function openAudioPicker() {
    audioInputRef.current?.click();
  }

  // ONE way in for everything. Images become frames, an audio file becomes the
  // track — the user shouldn't have to pick the right button first, and used to
  // face three of them for the same job.
  async function addAssets(fileList, insertAt) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    const images = sortFiles(files.filter((f) => kindOf(f) === "image"));
    const audios = files.filter((f) => kindOf(f) === "audio");
    const videos = files.filter((f) => kindOf(f) === "video");
    const others = files.filter((f) => kindOf(f) === "other");

    if (images.length) await addFiles(images, insertAt);
    // A video file becomes a CLIP on the picture track, alongside the stills —
    // one timeline, three kinds of clip, which is the whole point of the phase.
    let addedVideos = 0;
    if (videos.length) addedVideos = await addVideoClips(sortFiles(videos), insertAt);
    // Each audio file becomes its OWN track — dropping music and a voiceover
    // together gives you two layers, which is the point of the layer control.
    const room = MAX_AUDIO_TRACKS - audioTracks.length;
    const taking = audios.slice(0, Math.max(0, room));
    for (const file of taking) await addAudioTrack(file);

    const said = [];
    if (images.length) said.push(`${images.length} image${images.length === 1 ? "" : "s"}`);
    if (addedVideos) said.push(`${addedVideos} video clip${addedVideos === 1 ? "" : "s"}`);
    if (taking.length)
      said.push(
        taking.length === 1
          ? `audio “${taking[0].name}”`
          : `${taking.length} audio tracks`
      );
    const ignored = [];
    if (audios.length > taking.length)
      ignored.push(
        `${audios.length - taking.length} audio file(s) — at most ${MAX_AUDIO_TRACKS} tracks`
      );
    if (others.length)
      ignored.push(`${others.length} file(s) that aren't images, video or audio`);

    if (said.length || ignored.length) {
      setNotice(
        [said.length ? `Added ${said.join(" and ")}.` : "", ignored.length ? `Skipped ${ignored.join("; ")}.` : ""]
          .filter(Boolean)
          .join(" ")
      );
    }
  }

  // Adds a NEW track — it never replaces an existing one. The cap is checked by
  // the caller so a multi-file drop can report what it had to leave out.
  async function addAudioTrack(file) {
    if (!file) return;
    setError("");
    try {
      const [res, durationMs] = await Promise.all([
        api.uploadAnimaticAudio(animaticId, file),
        measureAudio(file),
      ]);
      // The lane the ＋ was pressed on, if any — so the file lands on THAT row
      // rather than making a new one. Consumed here, so a later drop onto the
      // pane can't inherit it.
      const layerId = pendingAudioLane.current;
      pendingAudioLane.current = "";
      setAudioTracks((list) => [
        // A lane holds one track: dropping a second file on it replaces what
        // was there, which is what "add audio to this row" has to mean.
        ...list.filter((a) => !layerId || a.layer_id !== layerId),
        {
          upload_id: res.upload_id,
          layer_id: layerId,
          filename: res.filename || file.name,
          duration_ms: durationMs,
          offset_ms: 0,
          volume: 1,
          muted: false,
          url: `/animatics/${animaticId}/media/${res.upload_id}`,
        },
      ]);
    } catch (e) {
      setError(e.message);
    }
  }

  // The Audio layer's ＋ and the "Add layer" control both land here.
  async function pickAudio(file) {
    if (!file) return;
    if (audioTracks.length >= MAX_AUDIO_TRACKS) {
      setNotice(`That's the limit — an animatic can hold ${MAX_AUDIO_TRACKS} audio tracks.`);
      return;
    }
    await addAudioTrack(file);
    setNotice(`Audio track added — “${file.name}”.`);
  }

  const patchTrack = (uploadId, patch) =>
    setAudioTracks((list) =>
      list.map((a) => (a.upload_id === uploadId ? { ...a, ...patch } : a))
    );

  function removeTrack(uploadId) {
    setAudioTracks((list) => list.filter((a) => a.upload_id !== uploadId));
    setNotice("Audio track removed.");
  }

  // Scale every hold so the sequence lands exactly on the end of the track.
  function fitToAudio() {
    if (!audioMs || !totalMs) return;
    const factor = audioMs / totalMs;
    setFrames((list) => {
      const scaled = list.map((f) => ({
        ...f,
        duration_ms: Math.max(MIN_MS, Math.round((f.duration_ms * factor) / 100) * 100),
      }));
      // Rounding to 100ms leaves a few ms over or short; put the remainder on
      // the last frame so the total is EXACT rather than nearly right.
      const sum = scaled.reduce((s, f) => s + f.duration_ms, 0);
      const drift = audioMs - sum;
      if (scaled.length && drift) {
        const last = scaled[scaled.length - 1];
        last.duration_ms = Math.max(MIN_MS, last.duration_ms + drift);
      }
      return scaled;
    });
    setNotice(`Frames stretched to match the audio (${formatTime(audioMs)}).`);
  }

  function setAllDurations(ms) {
    setFrames((list) => list.map((f) => ({ ...f, duration_ms: ms })));
    setNotice(`Every frame set to ${(ms / 1000).toFixed(1)}s.`);
  }

  // ------------------------------------------------------------- exporting
  async function startExport() {
    setError("");
    setExportBusy(true);
    try {
      await flush(); // the encoder reads the SAVED project, so save first
      await api.exportAnimatic(animaticId);
      setExportJob({ status: "running", progress: { percent: 0, message: "Starting…" } });
    } catch (e) {
      setError(e.message);
      setExportBusy(false);
    }
  }

  useEffect(() => {
    if (exportJob?.status !== "running") return;
    let alive = true;
    let timer;
    async function poll() {
      try {
        const job = await api.getJob(animaticId);
        if (!alive) return;
        setExportJob(job);
        if (job.status === "running" || job.status === "queued") {
          timer = setTimeout(poll, 1200);
        } else {
          setExportBusy(false);
          if (job.status === "succeeded" && job.result?.video) {
            setVideo(job.result.video);
            setNotice("Video ready — hit Download.");
          } else if (job.status === "failed") {
            setError(job.error || "The export failed.");
          }
        }
      } catch (e) {
        if (!alive) return;
        setExportBusy(false);
        setError(e.message);
      }
    }
    timer = setTimeout(poll, 900);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [exportJob?.status, animaticId]);

  // ------------------------------------------------- animate a frame (Veo)
  // ⚠ THE ONE PATH IN THIS EDITOR THAT SPENDS MONEY. It follows the discipline
  // `FinalVideoRenderStep` established: no button renders anything directly —
  // every one opens the priced confirm dialog, and nothing is submitted until
  // the number has been on screen and accepted.
  //
  // A finished clip lands as an ordinary VIDEO UPLOAD and is attached to the
  // frame it was generated from, so from that moment it is the same object on
  // the timeline as a file dragged in from the desktop: same trimming, same
  // speed, same export path.

  // The current frame list, readable from a callback that must NOT re-fire when
  // it changes. The Veo poll is the one that matters: an effect which restarts
  // on the frames would cancel its own in-flight fetch the moment it attached a
  // clip, which is how a paid render went missing once already.
  useEffect(() => {
    framesRef.current = frames;
  }, [frames]);

  function openAnimate(frameId) {
    const frame = frames.find((f) => f.id === frameId);
    setAnimateFor(frameId);
    // The frame's label is a starting draft, not a finished prompt — a label
    // says what the shot IS and Veo wants to hear what MOVES — but an empty box
    // is worse, and the placeholder explains the difference.
    setAnimatePrompt(frame?.label || "");
    setAnimateConfirm(null);
  }

  const veoFor = (frameId) =>
    veoClips.filter((c) => c.frame_id === frameId).slice(-1)[0] || null;

  // Ask what it would cost. FREE — this is the call that fills the dialog.
  async function askToAnimate(force = false) {
    if (!animateFor || !animatePrompt.trim()) return;
    setError("");
    setAnimateBusy(true);
    try {
      // The encoder and the renderer both read the SAVED project, so the frame
      // being animated has to be on the server before its picture is resolved.
      await flush();
      const estimate = await api.estimateAnimateFrames(animaticId, {
        frameIds: [animateFor],
        prompts: { [animateFor]: animatePrompt.trim() },
        render: animateRender,
        force,
      });
      if (!estimate.shots) {
        setError(
          force
            ? "Nothing to re-render."
            : "This frame already has a clip — use “Render again” if you want to pay for another."
        );
        return;
      }
      setAnimateConfirm({ estimate, force });
    } catch (e) {
      setError(e.message);
    } finally {
      setAnimateBusy(false);
    }
  }

  // The only place that actually spends. Reached solely from the confirm dialog.
  async function doAnimate() {
    if (!animateConfirm || !animateFor) return;
    const { force } = animateConfirm;
    setAnimateBusy(true);
    setAnimateConfirm(null);
    try {
      await api.animateAnimaticFrames(animaticId, {
        frameIds: [animateFor],
        prompts: { [animateFor]: animatePrompt.trim() },
        render: animateRender,
        force,
      });
      setAnimateFor(null);
      setAnimating(true);
      setAnimateProgress({ percent: 0, message: "Starting…" });
      setNotice("Animating with Veo — this takes a couple of minutes.");
    } catch (e) {
      setError(e.message);
    } finally {
      setAnimateBusy(false);
    }
  }

  // Turn a finished render into a video clip ON the frame it came from. This is
  // an ordinary document edit — the clip bytes and the paid record are already
  // safe on the server, so the worst a failed attach costs is the attach.
  const attachVeoClip = useCallback((clip) => {
    if (!clip?.upload_id) return;
    setFrames((list) =>
      list.map((f) =>
        f.id === clip.frame_id
          ? {
              ...f,
              kind: "video",
              src: { kind: "video", upload_id: clip.upload_id },
              // The clip is as long as we asked Veo for, and it opens showing
              // all of it — trimming and speed are then the ordinary controls.
              duration_ms: clip.duration_ms || f.duration_ms,
              in_ms: 0,
              out_ms: clip.duration_ms || null,
              speed: 1,
            }
          : f
      )
    );
  }, []);

  /**
   * Put every finished render where it belongs, and say whether any is still going.
   *
   * ⚠ SELF-HEALING ON PURPOSE. A clip that finished while the editor was closed
   * — or while an earlier version of this code was busy killing its own polling
   * loop — is still a clip that was PAID FOR, and it is sitting on the server
   * fully rendered. So this does not only run at the end of a batch: it runs on
   * every load too, and attaches anything ready whose frame isn't already video.
   * The alternative is a charge with nothing to show for it.
   *
   * It never touches a frame that IS already video, so it cannot fight a clip
   * the user has since trimmed, replaced or re-rendered.
   */
  const reconcileVeoClips = useCallback(
    (clips, currentFrames) => {
      let attached = 0;
      let failure = "";
      let pending = 0;
      for (const clip of clips || []) {
        if (clip.status === "queued" || clip.status === "rendering") {
          pending += 1;
          continue;
        }
        if (veoHandledRef.current.has(clip.id)) continue;
        if (clip.status === "failed") {
          veoHandledRef.current.add(clip.id);
          failure = clip.error || "The render failed.";
          continue;
        }
        if (clip.status !== "ready" || !clip.upload_id) continue;
        const frame = (currentFrames || []).find((f) => f.id === clip.frame_id);
        if (!frame) {
          // The frame it was generated from has gone. The clip is not lost —
          // it is an ordinary upload — but there is nowhere obvious to put it.
          veoHandledRef.current.add(clip.id);
          continue;
        }
        veoHandledRef.current.add(clip.id);
        if ((frame.kind || "image") === "video") continue;
        attachVeoClip(clip);
        attached += 1;
      }
      return { attached, failure, pending };
    },
    [attachVeoClip]
  );

  /**
   * Everything the editor does with a freshly loaded project.
   *
   * Assigned to the ref declared beside the `useAnimaticProject` call at the top
   * of this component, and called from inside its load promise — it sits HERE
   * because this is the first point in the file where `reconcileVeoClips` and
   * `resetHistory` both exist.
   *
   * Returns whether the load may be adopted as the saved baseline: FALSE if it
   * changed the document, because adopting then would fold that change into
   * "what the server already has" and it would never be saved.
   */
  onLoadedRef.current = (p) => {
    setSelectedId(p.frames?.[0]?.id || null);
    if (p.status === "running") setExportJob({ status: "running", progress: null });
    // RECOVER ANY PAID CLIP THAT NEVER LANDED. A render that finished while
    // this editor was closed is still a charge on someone's card, and the
    // MP4 is sitting on the server fully rendered. Attach it now; and if one
    // is still in flight, pick the polling back up where it left off. Both
    // run off the frames just loaded, not off state that hasn't settled yet.
    framesRef.current = p.frames || [];
    const { attached, pending } = reconcileVeoClips(p.veo_clips || [], p.frames || []);
    if (pending > 0) setAnimating(true);
    else if (attached) {
      setNotice(
        attached === 1
          ? "A clip you'd already rendered was waiting — it's on the timeline."
          : `${attached} rendered clips were waiting — they're on the timeline.`
      );
    }
    // This is also where UNDO history begins. Anything recorded before this
    // point describes an editor that hadn't loaded yet.
    resetHistory();
    return attached === 0;
  };

  // ⚠ KEYED ON `animating` ALONE. Everything this loop writes — the clip list,
  // the progress — is deliberately NOT in the dependency array, because an
  // effect that restarts on what its own poll writes cancels itself mid-flight.
  // That is exactly what went wrong the first time: the batch finished, the job
  // went RUNNING → QUEUED, the effect re-ran, its cleanup set `alive = false`,
  // and the awaited fetch returned to a dead closure. The clip was rendered,
  // charged for, and never attached.
  useEffect(() => {
    if (!animating) return undefined;
    let alive = true;
    let timer;
    async function poll() {
      try {
        // The RECORDS are the truth about whether anything is still rendering.
        // The job's status is not: a Veo batch ends by putting it back to
        // QUEUED, which is indistinguishable from an idle animatic.
        const project = await api.getAnimatic(animaticId);
        if (!alive) return;
        setVeoClips(project.veo_clips || []);
        const { attached, failure, pending } = reconcileVeoClips(
          project.veo_clips || [],
          framesRef.current
        );
        if (pending > 0) {
          try {
            const job = await api.getJob(animaticId);
            if (alive) setAnimateProgress(job.progress || null);
          } catch {
            /* progress is a nicety; losing it must not stop the poll */
          }
          if (alive) timer = setTimeout(poll, 2000);
          return;
        }
        setAnimating(false);
        setAnimateProgress(null);
        if (failure) setError(failure);
        else if (attached) setNotice("Clip ready — it's on the timeline.");
      } catch (e) {
        if (!alive) return;
        setAnimating(false);
        setAnimateProgress(null);
        setError(e.message);
      }
    }
    timer = setTimeout(poll, 1500);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [animating, animaticId, reconcileVeoClips]);

  // ------------------------------------------- captions & voiceover (Gemini)
  // ⚠ TWO STEPS, ALWAYS, exactly as ✨ Animate does it: a panel that spends
  // nothing, then a priced confirmation, then the call. Nothing here submits
  // directly.
  //
  // Both passes are written by the SERVER into the saved project — captions
  // become `texts`, a voiceover becomes an audio track — so when one finishes
  // the editor re-reads the project rather than trying to construct the result
  // locally. That is why `speechRunning` is part of `serverBusy`: for the life
  // of the run the server is the only writer.
  function openCaptions(uploadId) {
    setSpeechFor("captions");
    setSpeechTrack(uploadId || audioTracks[0]?.upload_id || "");
    setSpeechConfirm(null);
    setSpeechError("");
  }

  function openVoiceover() {
    setSpeechFor("voiceover");
    setSpeechConfirm(null);
    setSpeechError("");
  }

  // Ask what it would cost. FREE — this is the call that fills the dialog.
  async function askForSpeech() {
    setSpeechError("");
    setSpeechBusy(true);
    try {
      // The server reads the SAVED project — which frames are on the timeline
      // decides which dialogue gets read — so it has to be up to date first.
      await flush();
      const estimate =
        speechFor === "captions"
          ? await api.estimateCaptions(animaticId, {
              uploadId: speechTrack,
              language: speechLanguage,
              replace: speechReplace,
            })
          : await api.estimateVoiceover(animaticId, {
              voice: speechVoice,
              addCaptions: speechCaptions,
              replace: speechReplace,
            });
      if (speechFor === "voiceover" && !estimate.lines) {
        // By far the likeliest reason this button appears to do nothing, so it
        // says all three things that could be true rather than just the first.
        setSpeechError(
          "There is no dialogue to read. The lines come from the storyboard " +
            "this animatic was made from — so either these frames aren't from " +
            "a board, or the board's shots have no spoken lines on them."
        );
        return;
      }
      setSpeechConfirm({ estimate });
    } catch (e) {
      setSpeechError(e.message);
    } finally {
      setSpeechBusy(false);
    }
  }

  // The only place either pass actually spends. Reached solely from the dialog.
  async function doSpeech() {
    if (!speechConfirm) return;
    const pass = speechFor;
    setSpeechBusy(true);
    setSpeechConfirm(null);
    try {
      if (pass === "captions") {
        await api.captionAnimatic(animaticId, {
          uploadId: speechTrack,
          language: speechLanguage,
          replace: speechReplace,
        });
      } else {
        await api.voiceAnimatic(animaticId, {
          voice: speechVoice,
          addCaptions: speechCaptions,
          replace: speechReplace,
        });
      }
      setSpeechFor(null);
      setSpeechRunning(true);
      setSpeechProgress({ percent: 0, message: "Starting…" });
      setNotice(
        pass === "captions"
          ? "Listening to that track and writing the captions…"
          : "Reading the dialogue aloud — this takes a moment per line."
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setSpeechBusy(false);
    }
  }

  // ⚠ KEYED ON `speechRunning` ALONE, for the reason written out above the Veo
  // poll: an effect that restarts on what its own poll writes cancels itself
  // mid-flight, and by then the work has been paid for.
  //
  // Unlike the Veo poll this reads the JOB STATUS rather than a record list,
  // because there is nothing to reconcile — the captions are already in the
  // saved project by the time the job leaves RUNNING. The pass ends by putting
  // the job back to QUEUED, exactly as a Veo batch does, and reports what went
  // wrong (if anything) in the job's `error`.
  useEffect(() => {
    if (!speechRunning) return undefined;
    let alive = true;
    let timer;
    async function poll() {
      try {
        const job = await api.getJob(animaticId);
        if (!alive) return;
        if (job.status === "running") {
          setSpeechProgress(job.progress || null);
          timer = setTimeout(poll, 1500);
          return;
        }
        // Finished, one way or the other. Re-read the document: this is the one
        // path where the server, not the editor, wrote the project's content.
        const project = await api.getAnimatic(animaticId);
        if (!alive) return;
        setTexts(project.texts || []);
        setAudioTracks(project.audio_tracks || []);
        setSpeechRunning(false);
        setSpeechProgress(null);
        if (job.error) setError(job.error);
        else setNotice("Done — it's on the timeline.");
      } catch (e) {
        if (!alive) return;
        setSpeechRunning(false);
        setSpeechProgress(null);
        setError(e.message);
      }
    }
    timer = setTimeout(poll, 1200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [speechRunning, animaticId, setTexts, setAudioTracks]);

  async function stopExport() {
    try {
      await api.stopAnimaticExport(animaticId);
      setNotice("Stopping the export…");
    } catch (e) {
      setError(e.message);
    }
  }

  // Hand this animatic to Animatics → Final Video. Its frames become shots,
  // still unrendered: creating the project spends nothing, so this is a
  // navigation, not a commitment.
  async function makeFinalVideo() {
    setMakingVideo(true);
    setError("");
    try {
      // Flush first, or shots are built from the frames as they were last
      // saved rather than as they are on screen.
      await flush();
      const project = await api.createFinalVideo({ sourceAnimaticId: animaticId });
      onMakeFinalVideo(project.job_id);
    } catch (e) {
      setError(e.message);
      setMakingVideo(false);
    }
  }

  // Leaving: an animatic you never put anything into is discarded, so "open it,
  // change your mind, go back" doesn't leave a row in the library. Anything with
  // content — or a name you chose — is kept.
  async function handleBack() {
    if (isEmpty) {
      dirtyRef.current = false; // nothing worth flushing on the way out
      try {
        await api.deleteAnimatic(animaticId);
      } catch {
        // If the discard fails it's not worth blocking the exit — worst case an
        // empty animatic stays and can be deleted from the library.
      }
    }
    onBack();
  }

  // Save on an unnamed animatic asks for a name first — that's the "save as"
  // moment. Once it has a real name, Save just writes.
  function handleSave() {
    if (needsName) {
      setSaveAsName(title.trim() === UNTITLED ? "" : title);
      return;
    }
    flush();
  }

  async function confirmSaveAs() {
    const name = (saveAsName || "").trim();
    if (!name) return;
    setSaveAsName(null);
    setTitle(name);
    // The autosave effect will pick the new title up, but don't make the user
    // wait for the debounce when they've explicitly asked to save.
    try {
      await api.saveAnimatic(animaticId, { title: name });
      baselineRef.current = null; // force the pending debounce to write the rest
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDelete() {
    try {
      await api.deleteAnimatic(animaticId);
      onDeleted?.();
    } catch (e) {
      setError(e.message);
      setConfirmDelete(false);
    }
  }

  // ------------------------------------------------------------------ view
  if (loading) {
    return (
      <div className="workflow-head-wrap">
        <div className="card placeholder">
          <span className="spinner-inline" /> Opening your animatic…
        </div>
      </div>
    );
  }

  if (error && !frames.length && !title) {
    return (
      <div className="workflow-head-wrap">
        <div className="card">
          <p className="error">{error}</p>
          <button type="button" className="btn" onClick={onBack}>
            ← Your Animatics
          </button>
        </div>
      </div>
    );
  }

  // What the export will actually be: the whole timeline by default, or just
  // the pictures if that's been chosen.
  const exportMs = settings.end_at === "frames" ? totalMs : spanMs;
  const aspectCss = (settings.aspect_ratio || "16:9").replace(":", " / ");
  // The same ratio as a plain number. CSS can hold the shape with `aspect-ratio`
  // alone only when ONE axis is definite; in a box constrained on both (which is
  // what "fit inside this pane" means) it silently gives up and the preview
  // stops matching the exported frame. Sizing the width off the container's
  // height with this number keeps it exact — see `.an-screen-fit`.
  const [arW, arH] = (settings.aspect_ratio || "16:9").split(":").map(Number);
  const arNum = arW && arH ? arW / arH : 16 / 9;
  // One step of zoom, from a button or from the Zoom tool. `dir` is ±1.
  const zoomBy = (dir) =>
    setPxPerSec((p) =>
      Math.min(MAX_PPS, Math.max(MIN_PPS, p * Math.pow(ZOOM_STEP, dir)))
    );
  const lengthMatches = audioMs > 0 && Math.abs(audioMs - totalMs) <= 250;
  const progress = exportJob?.progress || {};

  // The workspace is a fixed-height grid — three panes over a full-width
  // timeline — rather than a page that scrolls. An editor where the picture
  // slides off screen while you drag a clip isn't usable; every pane scrolls
  // inside itself instead.
  // `~` maximizes the pane the pointer is over, exactly as Premiere does — so
  // every pane reports its own name on hover and the workspace is told which
  // one is filling it.
  // --- The picture is a CANVAS now -----------------------------------------
  // Every pixel of the monitor — both pictures, the transition between them,
  // the shapes and the overlay pictures — is composited by `ProgramCanvas` in
  // WebGL. The DOM keeps only what WebGL adds nothing to: the captions, the
  // shot label, and the selection outlines and resize handles below.
  //
  // Why it had to stop being the DOM: CSS can fake a brightness slider, but not
  // a 3D LUT, a feathered mask or a chroma key, and `mix-blend-mode` blends a
  // whole ELEMENT rather than one clip against the pixels beneath it. The shape
  // FILLS moved with it because the compositing order is picture → shapes →
  // overlays and an overlay's blend mode needs every pixel underneath it — so
  // a DOM shape would sit either in the wrong order or outside the backdrop the
  // blend reads.
  //
  // ⚠ THE OLD KNOWN LIMIT IS GONE. The monitor and `_transition_canvas` now
  // both composite the incoming picture OVER the outgoing one, so a clip faded
  // by its own keyframes — or carrying a chroma key or a mask — mid-transition
  // looks the same in both. Each transition branch in `ProgramCanvas` is still
  // the counterpart of one in `animatic.py`: the same fractions travelling the
  // same way.

  const paneProps = (name) => ({
    onMouseEnter: () => {
      hoverPaneRef.current = name;
    },
    className: `an-pane an-pane-${name}${maximized === name ? " an-maxed" : ""}`,
  });

  return (
    <div className={`an-nle ${maximized ? `an-has-max an-max-${maximized}` : ""}`}>
      {/* ------------------------------------------------------- top bar */}
      <header className="an-topbar">
        <button type="button" className="btn small" onClick={handleBack}>
          ← Your Animatics
        </button>

        <input
          className="an-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Untitled animatic"
          aria-label="Animatic title"
        />

        {/* Only speaks when it has something to say. A permanent "✓ Saved" is
            noise — it's the DEFAULT state, so showing it always tells you
            nothing. The tick appears for a moment after a save, then goes. */}
        <span className={`an-save an-save-${saveState}`}>
          {saveState === "saving" && (
            <>
              <span className="spinner-inline" /> Saving…
            </>
          )}
          {saveState === "dirty" && "• Unsaved changes"}
          {saveState === "error" && "⚠ Not saved"}
          {saveState === "saved" && savedFlash && "✓ Saved"}
        </span>

        <span className="an-spacer" />

        {video && !exporting && (
          <button
            type="button"
            className={`btn small ${video.stale ? "an-stale" : ""}`}
            onClick={() =>
              api.downloadAnimaticVideo(animaticId, `${exportName || title || "animatic"}.mp4`)
            }
            title={
              video.stale
                ? "This file is from before your latest edits — export again for an up-to-date one"
                : `${formatTime(video.duration_ms)} · ${video.width}×${video.height} · ${(
                    (video.size_bytes || 0) / 1048576
                  ).toFixed(1)} MB`
            }
          >
            <Icon name="download" />
            {video.stale ? " MP4 (out of date)" : " Download MP4"}
          </button>
        )}

        {/* Saving is automatic, but a Save button is still worth having: it's
            reassurance, and it's the way to force the write before leaving. */}
        <button
          type="button"
          className="btn small"
          disabled={saveState === "saving" || (saveState === "saved" && !needsName)}
          onClick={handleSave}
          title={
            needsName
              ? "Save — you'll be asked for a name"
              : saveState === "saved"
                ? "Everything is already saved"
                : "Save now (it also saves on its own)"
          }
        >
          {saveState === "saving" ? (
            <>
              <span className="spinner-inline" /> Saving…
            </>
          ) : (
            <>
              <Icon name="save" /> Save
            </>
          )}
        </button>

        {exporting ? (
          <button type="button" className="btn danger-btn" onClick={stopExport}>
            ⏹ Stop export
          </button>
        ) : (
          <button
            type="button"
            className="btn primary an-export"
            disabled={!frames.length}
            onClick={() => {
              setExportName((n) => n || title || "animatic");
              setExportOpen(true);
            }}
            title="Choose the export settings, then encode"
          >
            <Icon name="download" /> Export video
          </button>
        )}

        {/* Hands this animatic to the next workflow. Creating the project is
            free — nothing renders until a motion prompt is written over there
            and the price is confirmed — so this can sit next to Export without
            being a trap. */}
        {onMakeFinalVideo && (
          <button
            type="button"
            className="btn"
            disabled={!frames.length || makingVideo}
            onClick={makeFinalVideo}
            title="Turn these frames into real footage with Veo (free to start)"
          >
            {makingVideo ? "Creating…" : "🎞️ Make final video"}
          </button>
        )}

        {/* Sits last, past Export: destructive, so it's the furthest thing from
            the button you actually came here to press. */}
        {confirmDelete ? (
          <span className="an-del-confirm">
            <span className="tiny">Delete this animatic?</span>
            <button type="button" className="btn small danger-btn" onClick={handleDelete}>
              Yes, delete
            </button>
            <button
              type="button"
              className="btn small ghost"
              onClick={() => setConfirmDelete(false)}
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn small an-del-btn"
            onClick={() => setConfirmDelete(true)}
            title="Delete this animatic — the storyboard it came from is untouched"
          >
            <Icon name="trash" />
          </button>
        )}
      </header>

      {(error || notice || exporting || animating || speechRunning) && (
        <div className="an-statusbar">
          {error && <span className="an-status-error">{error}</span>}
          {!error && notice && <span className="an-status-note">{notice}</span>}
          {exporting && (
            <span className="an-status-export">
              <span className="spinner-inline" />
              {progress.message || "Preparing…"}
              <span className="an-status-bar">
                <span style={{ width: `${progress.percent ?? 0}%` }} />
              </span>
              {progress.percent ?? 0}%
            </span>
          )}
          {/* A Veo render takes minutes and costs money, so it says so the whole
              time rather than leaving a button reading "Animating…" as the only
              sign anything is happening. */}
          {animating && !exporting && (
            <span className="an-status-export">
              <span className="spinner-inline" />
              {animateProgress?.message || "Animating with Veo…"}
              <span className="an-status-bar">
                <span style={{ width: `${animateProgress?.percent ?? 0}%` }} />
              </span>
              {animateProgress?.percent ?? 0}%
            </span>
          )}
          {/* A captions or voiceover pass is quick but it is still the SERVER
              writing this project, so it says so for the same reason: the
              editor is read-only until it finishes. */}
          {speechRunning && !exporting && !animating && (
            <span className="an-status-export">
              <span className="spinner-inline" />
              {speechProgress?.message || "Working…"}
              <span className="an-status-bar">
                <span style={{ width: `${speechProgress?.percent ?? 0}%` }} />
              </span>
              {speechProgress?.percent ?? 0}%
            </span>
          )}
        </div>
      )}

      {/* ------------------------------------------------- the three panes */}
      <div className="an-panes">
        {/* ---- Media: the frames in this animatic, plus the audio ---- */}
        <section {...paneProps("media")}>
          {/* Two tabs rather than one long scroll: the shape picker is a
              LIBRARY you pick from, not part of this animatic's footage, and
              stacking it under the frames buried it below a 60-panel board. */}
          <div className="an-pane-head">
            <span className="an-tabs">
              <button
                type="button"
                className={`an-tab ${mediaTab === "media" ? "on" : ""}`}
                onClick={() => setMediaTab("media")}
              >
                Media
              </button>
              <button
                type="button"
                className={`an-tab ${mediaTab === "shapes" ? "on" : ""}`}
                onClick={() => setMediaTab("shapes")}
              >
                Shapes
              </button>
            </span>
            <span className="an-spacer" />
            <span className="tiny muted">
              {mediaTab === "media"
                ? `${frames.length} frames`
                : `${shapes.length} on the timeline`}
            </span>
          </div>

          {mediaTab === "shapes" ? (
            <div className="an-pane-body">
              <ShapeGallery
                onAdd={(kind) => {
                  addShape(kind, pendingShapeLane);
                  setPendingShapeLane("");
                }}
              />
              <p className="an-shape-hint tiny muted">
                A shape lands on the frame at the playhead, then moves and
                re-times like any other clip. Drag it on the picture to place it.
              </p>

              {shapes.length > 0 && (
                <div className="an-media-audio">
                  <div className="an-media-sub">
                    In this animatic <span className="muted">({shapes.length})</span>
                  </div>
                  {shapes.map((s, i) => (
                    <button
                      type="button"
                      key={s.id}
                      className={`an-media-track ${selectedShapeId === s.id ? "sel" : ""}`}
                      onClick={() => {
                        selectOnly({ shape: s.id });
                        seek(s.start_ms);
                      }}
                    >
                      <ShapeSwatch kind={s.kind} color={s.color} className="an-media-ico" />
                      <span className="an-media-name">
                        {SHAPE_KINDS.find((k) => k.id === s.kind)?.label || s.kind} {i + 1}
                      </span>
                      <span className="tiny muted">{formatTime(s.start_ms)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
          <div
            className={`an-pane-body ${dropping ? "an-dropping" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDropping(true);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget)) setDropping(false);
            }}
            onDrop={(e) => {
              // Only OS file drops — a frame being dragged to reorder carries no
              // files and must fall through to the strip's own handler.
              if (e.dataTransfer?.files?.length) {
                e.preventDefault();
                addAssets(e.dataTransfer.files);
              }
              setDropping(false);
            }}
          >
            {/* One control for everything — images, audio, whatever. There were
                three ("Add images", the drop card, and "Add an MP3") for what is
                really a single action. */}
            <button
              type="button"
              className="an-asset-drop"
              disabled={uploading}
              onClick={() => assetInputRef.current?.click()}
            >
              <span className="an-asset-plus">＋</span>
              <span className="an-asset-text">
                {uploading ? "Uploading…" : "Add assets or drop them here"}
              </span>
              <span className="an-asset-note">Images for frames · an MP3 for the audio track</span>
            </button>

            <FrameStrip
              vertical
              showAdd={false}
              frames={frames}
              urls={urls}
              selectedId={selectedId}
              uploading={uploading}
              onSelect={(id) => {
                selectOnly({ frame: id });
                const i = frames.findIndex((f) => f.id === id);
                if (i >= 0) seek(starts[i]);
              }}
              onReorder={reorder}
              onDuration={(id, ms) => patchFrame(id, { duration_ms: ms })}
              onDelete={deleteFrame}
              onDuplicate={duplicateFrame}
              onAddFiles={addAssets}
            />

            {/* Only appears once there IS audio — an empty "Audio" heading with
                its own add button was the third of the three controls. */}
            {audioTracks.length > 0 && (
              <div className="an-media-audio">
                <div className="an-media-sub">
                  Audio <span className="muted">({audioTracks.length})</span>
                </div>
                {/* A LIST, not a mixer. Volume and the rest live in Properties
                    — click a track to edit it, same as a frame or a caption. */}
                {audioTracks.map((track) => (
                  <button
                    type="button"
                    className={`an-media-track ${
                      selectedTrackId === track.upload_id ? "sel" : ""
                    }`}
                    key={track.upload_id}
                    onClick={() => selectOnly({ track: track.upload_id })}
                  >
                    <span className="an-media-ico">♪</span>
                    <span className="an-media-name" title={track.filename}>
                      {track.filename}
                    </span>
                    <span className="tiny muted">
                      {track.muted ? "muted" : `${Math.round((track.volume ?? 1) * 100)}%`}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
          )}
        </section>

        {/* ---- Program: what the viewer would see right now ---- */}
        <section {...paneProps("program")}>
          <div className="an-pane-head">
            <span className="an-pane-title">Program</span>
            <span className="tiny muted">
              {settings.aspect_ratio} · {settings.fps} fps
            </span>
          </div>
          <div className="an-pane-body an-program-body">
            {/* The fitter is a size container; the screen sizes itself off its
                height, so the frame shape on screen is exactly the frame shape
                that gets exported. */}
            <div className="an-screen-fit">
            <div
              className="an-screen"
              ref={screenRef}
              style={{
                aspectRatio: aspectCss,
                "--ar-num": arNum,
                background: settings.background,
              }}
            >
              {/* EVERY PIXEL of the monitor: both pictures, the transition
                  between them, the shape fills and the overlay pictures, with
                  each clip's effects, mask and blend mode applied. The DOM
                  layers below are the handles and the captions only. x/y are
                  the picture's centre here and in `place_picture`, which is the
                  only reading under which a zoom doesn't also shift it. */}
              <ProgramCanvas
                scene={scene}
                frames={frames}
                urls={urls}
                videoUrls={videoUrls}
                overlayUrls={overlayUrls}
                settings={settings}
                videoElsRef={videoElsRef}
                onUnavailable={() => setGlFailed(true)}
              />
              {(!shownFrame || glFailed) && (
                <div className="an-screen-empty">
                  {glFailed
                    ? "This browser can't show the preview — it has no WebGL. The export is unaffected."
                    : frames.length
                      ? "Loading…"
                      : "Add images or video to start your animatic"}
                </div>
              )}

              {/* The shape HANDLES. The fills are in the canvas; these boxes
                  are the drag targets, laid over them at the same fractions the
                  compositor draws at, so a shape and its handle cannot
                  separate. Everything is positioned in % of this box, which is
                  the same fraction the project stores and `draw_shapes` scales
                  into the exported frame. */}
              {activeShapes.length > 0 && (
                <div className="an-shape-layer">
                  {activeShapes.map((s) => (
                    <div
                      key={s.id}
                      className={`an-shape ${selectedShapeId === s.id ? "sel" : ""}`}
                      style={{
                        left: `${s.x * 100}%`,
                        top: `${s.y * 100}%`,
                        width: `${s.w * 100}%`,
                        height: `${s.h * 100}%`,
                        transform: `translate(-50%, -50%) rotate(${s.rotation || 0}deg)`,
                      }}
                      onPointerDown={(e) => startShapeDrag(e, s, "move")}
                      title="Drag to move · drag the corner to resize"
                    >
                      {/* ⚠ NO FILL. The shape itself is drawn in the canvas
                          under this box — it has to be, so an overlay's blend
                          mode can read it as backdrop. What is left here is the
                          hit target and the handle, which is the half WebGL
                          would have made harder rather than easier. */}
                      {selectedShapeId === s.id && (
                        <span
                          className="an-shape-handle"
                          onPointerDown={(e) => startShapeDrag(e, s, "resize")}
                          title="Drag to resize"
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Overlay HANDLES, over the shapes' — the same box a shape gets,
                  because they are placed and dragged the same way. The pictures
                  themselves are composited in the canvas, after the shapes and
                  under the text, which is the order `render_frame` uses. */}
              {activeOverlays.length > 0 && (
                <div className="an-shape-layer">
                  {activeOverlays.map((o) => (
                    <div
                      key={o.id}
                      className={`an-shape an-overlay ${selectedOverlayId === o.id ? "sel" : ""}`}
                      style={{
                        left: `${o.x * 100}%`,
                        top: `${o.y * 100}%`,
                        width: `${o.w * 100}%`,
                        height: `${o.h * 100}%`,
                        transform: `translate(-50%, -50%) rotate(${o.rotation || 0}deg)`,
                        // ⚠ NOT faded with the clip. The picture's opacity is
                        // applied in the canvas; fading the HANDLE too would
                        // leave a nearly-transparent overlay with a drag target
                        // nobody can see.
                      }}
                      onPointerDown={(e) => startShapeDrag(e, o, "move", "overlay")}
                      title="Drag to move · drag the corner to resize"
                    >
                      {/* ⚠ NO <img>. The picture is drawn in the canvas, fitted
                          "contain" inside this box exactly as `draw_overlays`
                          fits it — see `overlayRect`. This box is the drag
                          target and nothing else. */}
                      {selectedOverlayId === o.id && (
                        <span
                          className="an-shape-handle"
                          onPointerDown={(e) => startShapeDrag(e, o, "resize", "overlay")}
                          title="Drag to resize"
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* The text layer, over the picture. Sized in `cqh` (a fraction
                  of this box's own height) using the SAME divisors the exporter
                  uses, so the preview and the MP4 agree by construction rather
                  than by two numbers kept in step by hand. */}
              {activeTexts.length > 0 && (
                <div className="an-text-layer">
                  {["top", "middle", "bottom"].map((zone) => {
                    const zoneClips = activeTexts.filter(
                      (c) => (c.place || "flow") !== "free"
                        && (c.position || "bottom") === zone
                    );
                    if (!zoneClips.length) return null;
                    return (
                      <div key={zone} className={`an-text-zone an-text-${zone}`}>
                        {zoneClips.map((c) => (
                          <span
                            key={c.id}
                            className={captionClass(c)}
                            style={captionStyle(c)}
                          >
                            {c.text}
                          </span>
                        ))}
                      </div>
                    );
                  })}
                  {/* Free-placed captions sit at their own x/y rather than in a
                      zone — the same fractions `draw_texts` centres the block on
                      in the exported frame, so dragging one here puts it there
                      in the MP4 at any resolution. */}
                  {activeTexts
                    .filter((c) => (c.place || "flow") === "free")
                    .map((c) => (
                      <span
                        key={c.id}
                        className={`${captionClass(c)} an-text-free`}
                        style={{
                          ...captionStyle(c),
                          left: `${(c.x ?? 0.5) * 100}%`,
                          top: `${(c.y ?? 0.85) * 100}%`,
                        }}
                      >
                        {c.text}
                      </span>
                    ))}
                </div>
              )}

              {settings.show_labels && currentFrame?.label && (
                <span className="an-screen-label">{currentFrame.label}</span>
              )}
            </div>
            </div>

            <div className="an-transport">
              <button type="button" className="an-tbtn" onClick={() => seek(0)} title="Back to start">
                ⏮
              </button>
              <button
                type="button"
                className="an-tbtn"
                onClick={() => stepFrame(-1)}
                title="Previous frame"
              >
                ◀
              </button>
              <button
                type="button"
                className="an-tbtn an-play"
                onClick={togglePlay}
                disabled={!frames.length}
                title={playing ? "Pause (space)" : "Play (space)"}
              >
                {playing ? "❚❚" : "▶"}
              </button>
              <button type="button" className="an-tbtn" onClick={() => stepFrame(1)} title="Next frame">
                ▶
              </button>
              {/* Counts against the TIMELINE, not the video: with audio running
                  past the pictures the playhead can legitimately sit beyond the
                  video's end, and "0:30 / 0:02" just looked broken. The video's
                  own length is stated in the timeline header. */}
              <span className="an-clock">
                {formatTime(timeMs)} <span className="muted">/ {formatTime(spanMs)}</span>
              </span>
              {timeMs > totalMs + 20 && (
                <span className="an-past-end-note" title="The exported video stops here">
                  past the end of the video
                </span>
              )}
              <span className="an-shotnum">
                {currentIndex >= 0 ? `Frame ${currentIndex + 1} of ${frames.length}` : ""}
              </span>
            </div>
          </div>
        </section>

        {/* ---- Properties: whatever is selected. One pane, three states,
                so there is only ever one place to look for a setting. ---- */}
        <section {...paneProps("props")}>
          <div className="an-pane-head">
            <span className="an-pane-title">Properties</span>
            <span className="tiny muted">
              {selectedTransition
                ? "Transition"
                : selectedText
                  ? "Text"
                  : selectedShape
                    ? "Shape"
                    : selectedOverlay
                      ? "Picture"
                      : selectedTrack
                        ? "Audio"
                        : selectedFrame
                          ? "Frame"
                          : "Video"}
            </span>
            {/* Without this there is no way back: selecting anything hides the
                whole-video settings, and nothing deselects. */}
            {(selectedTransition ||
              selectedText ||
              selectedShape ||
              selectedOverlay ||
              selectedFrame ||
              selectedTrack) && (
              <button
                type="button"
                className="an-pane-back"
                title="Deselect — show the settings for the whole video"
                onClick={() => selectOnly({})}
              >
                ← Video
              </button>
            )}
          </div>
          <div className="an-pane-body">
            {selectedTransition ? (
              <TransitionProperties
                transition={selectedTransition}
                frames={frames}
                onChange={patchTransition}
                onDelete={deleteTransition}
                onClose={() => selectOnly({})}
              />
            ) : selectedText ? (
              <TextProperties
                clip={inspectedShown}
                stored={inspected.clip}
                totalMs={totalMs}
                textAreaRef={textAreaRef}
                kf={kf}
                gesture={gestureProps}
                onChange={patchInspected}
                onDuplicate={duplicateText}
                onDelete={deleteText}
                onClose={() => setSelectedTextId(null)}
              />
            ) : selectedOverlay ? (
              <ShapeProperties
                shape={inspectedShown}
                totalMs={totalMs}
                picture={overlayUrls[selectedOverlay.upload_id]}
                kf={kf}
                gesture={gestureProps}
                onChange={patchInspected}
                look={lookPanel}
                onDuplicate={duplicateOverlay}
                onDelete={deleteOverlay}
                onClose={() => setSelectedOverlayId(null)}
              />
            ) : selectedShape ? (
              <ShapeProperties
                shape={inspectedShown}
                totalMs={totalMs}
                kf={kf}
                gesture={gestureProps}
                onChange={patchInspected}
                onDuplicate={duplicateShape}
                onDelete={deleteShape}
                onClose={() => setSelectedShapeId(null)}
              />
            ) : selectedTrack ? (
              <AudioProperties
                track={selectedTrack}
                index={audioTracks.findIndex((a) => a.upload_id === selectedTrack.upload_id)}
                tracks={audioTracks}
                gesture={gestureProps}
                onChange={patchTrack}
                onRemove={removeTrack}
                onCaptions={openCaptions}
                captionsBusy={serverBusy}
              />
            ) : selectedFrame ? (
              <FrameProperties
                frame={inspectedShown}
                index={frames.findIndex((f) => f.id === selectedFrame.id)}
                url={urls[selectedFrame.id]}
                kf={kf}
                gesture={gestureProps}
                // Which moment of its source is under the playhead — only
                // meaningful, and only shown, while THIS clip is the one on
                // screen. Reading the scene rather than recomputing it means
                // the pane can't disagree with the monitor.
                sourceMs={
                  shownFrame && frames[shownFrame.index]?.id === selectedFrame.id
                    ? shownFrame.source_ms
                    : null
                }
                look={lookPanel}
                onChange={patchInspected}
                onDuplicate={duplicateFrame}
                onDelete={deleteFrame}
                // The paid path. The pane only ever OPENS the dialog — it can
                // never render anything itself.
                onAnimate={openAnimate}
                veo={veoFor(selectedFrame.id)}
                animating={animating}
              />
            ) : (
              <VideoProperties
                settings={settings}
                onChange={(patch) => setSettings((s) => ({ ...s, ...patch }))}
                sourceBoard={sourceBoard}
              />
            )}
          </div>
        </section>
      </div>

      {/* ------------------------------------------------------- timeline */}
      <section {...paneProps("timeline")}>
        <div className="an-pane-head">
          <span className="an-pane-title">Timeline</span>
          <span className="an-tl-total tiny">
            <strong>{formatTime(totalMs)}</strong>
            {audioMs > 0 && (
              <span className={`an-match ${lengthMatches ? "ok" : "off"}`}>
                {lengthMatches
                  ? "✓ matches the audio"
                  : `audio ${formatTime(audioMs)} — ${
                      totalMs > audioMs ? "video runs longer" : "video ends early"
                    }`}
              </span>
            )}
          </span>

          {/* The tool palette. Each one is a real behaviour on this timeline —
              see TOOLS. Premiere's letters, so the muscle memory carries over. */}
          <span className="an-tools" role="group" aria-label="Tools">
            {TOOLS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`an-tool ${tool === t.id ? "on" : ""}`}
                onClick={() => setTool(t.id)}
                title={`${t.label} (${t.key}) — ${t.hint}`}
                aria-pressed={tool === t.id}
              >
                {t.key}
              </button>
            ))}
          </span>

          <span className="an-spacer" />

          <button
            type="button"
            className="an-tool"
            onClick={undo}
            disabled={!canUndo}
            title="Undo (Ctrl+Z)"
          >
            ↶
          </button>
          <button
            type="button"
            className="an-tool"
            onClick={redo}
            disabled={!canRedo}
            title="Redo (Ctrl+Shift+Z)"
          >
            ↷
          </button>
          <button
            type="button"
            className={`an-tool ${snapping ? "on" : ""}`}
            onClick={() => setSnapping((s) => !s)}
            title={`Snapping is ${snapping ? "on" : "off"} (S) — clip edges jump to nearby cuts, the playhead and the marks`}
            aria-pressed={snapping}
          >
            🧲
          </button>

          <button
            type="button"
            className="btn small an-add-text"
            onClick={
              // NOT `onClick={addText}` — React would pass the click event as
              // the layer id and the caption would land on a lane that doesn't
              // exist. The header button always means the default text lane.
              () => addText("")
            }
            title="Add a text clip over the frame at the playhead"
          >
            <Icon name="text" /> Text
          </button>
          {/* The other clip you can make without a file. Sits beside Text on
              purpose: those two are the whole set, and a colour card had no way
              in at all until now even though the `kind: "color"` clip underneath
              it was built and tested. Not disabled on an empty animatic — a
              black slug is a perfectly ordinary first clip. */}
          <button
            type="button"
            className="btn small an-add-card"
            onClick={() => addColorCard()}
            title="Add a colour card after the frame at the playhead — a slug, a blackout or a flash. Pick its colour in Properties."
          >
            <Icon name="card" /> Colour card
          </button>
          {/* ⚠ SPENDS QUOTA — but only through the priced panel it opens, like
              ✨ Animate. The lines come from the board this animatic was made
              from, timed to the shots that reference them, so there is nothing
              to type: that is the whole reason it lives in here.

              Plain `btn small`, like "Fit to audio" beside it, and deliberately
              NOT the `.an-add-text` / `.an-add-card` weight: those two are a
              pair that makes a clip out of nothing and costs nothing. This one
              spends, and reading as one of them would be a lie about it. */}
          <button
            type="button"
            className="btn small"
            disabled={!hasBoardFrames || serverBusy}
            onClick={openVoiceover}
            title={
              hasBoardFrames
                ? "Read the storyboard's dialogue aloud onto the audio layer — costs a little; you see the price first"
                : "Nothing here to read: a voiceover comes from the storyboard's dialogue, and none of these clips are board panels"
            }
          >
            🎙 Voiceover
          </button>
          <button
            type="button"
            className="btn small"
            disabled={!audioMs || !frames.length}
            onClick={fitToAudio}
            title="Stretch every frame proportionally so the video is exactly as long as the audio"
          >
            ⇔ Fit to audio
          </button>
          <span className="an-setall">
            Set all
            {[1, 2, 3, 5].map((s) => (
              <button
                key={s}
                type="button"
                className="an-setall-btn"
                disabled={!frames.length}
                onClick={() => setAllDurations(s * 1000)}
              >
                {s}s
              </button>
            ))}
          </span>
          <span className="an-zoom">
            <button
              type="button"
              className="an-tbtn small"
              disabled={pxPerSec <= MIN_PPS + 0.01}
              onClick={() => zoomBy(-1)}
              title="Zoom out"
            >
              −
            </button>
            <button
              type="button"
              className="an-tbtn small"
              disabled={pxPerSec >= MAX_PPS - 0.01}
              onClick={() => zoomBy(1)}
              title="Zoom in"
            >
              ＋
            </button>
          </span>
        </div>

        <div className="an-pane-body an-timeline-body">
          <Timeline
            frames={frames}
            texts={texts}
            shapes={shapes}
            totalMs={totalMs}
            spanMs={spanMs || 1000}
            timeMs={timeMs}
            pxPerSec={pxPerSec}
            selectedId={selectedId}
            selectedTextId={selectedTextId}
            selectedShapeId={selectedShapeId}
            selectedOverlayId={selectedOverlayId}
            overlays={overlays}
            overlayUrls={overlayUrls}
            transitions={transitions}
            selectedTransitionId={selectedTransitionId}
            onSelectTransition={(id) => selectOnly({ transition: id })}
            onAddTransition={addTransition}
            onTransitionChange={patchTransition}
            lanes={lanes}
            audioUrls={audioUrls}
            audioAnalyses={audioAnalyses}
            onToggleMute={(id) =>
              patchTrack(id, {
                muted: !audioTracks.find((a) => a.upload_id === id)?.muted,
              })
            }
            onTrimTrack={(id, ms) => patchTrack(id, { trim_ms: ms })}
            onFadeChange={(id, patch) => patchTrack(id, patch)}
            onSelect={(id) => selectOnly({ frame: id })}
            onSelectText={(id) => selectOnly({ text: id })}
            onSelectShape={(id) => selectOnly({ shape: id })}
            onSelectOverlay={(id) => selectOnly({ overlay: id })}
            selectedTrackId={selectedTrackId}
            onSelectTrack={(id) => selectOnly({ track: id })}
            onSeek={seek}
            onResize={(id, ms) => patchFrame(id, { duration_ms: ms })}
            onTextChange={patchText}
            onShapeChange={patchShape}
            onOverlayChange={patchOverlay}
            onKeyMove={moveKeyframe}
            onAddToLane={addToLane}
            onRemoveLayer={removeLayer}
            onAddLayer={() => setLayerMenu(true)}
            onRemoveTrack={removeTrack}
            tool={tool}
            snapping={snapping}
            onSplitAt={splitFrameAt}
            onZoomAt={zoomBy}
            /* The scroll bar's grips set the scale outright — see
               ZoomScrollbar.jsx — so they get the setter, not the stepper. */
            onSetPxPerSec={(next) =>
              setPxPerSec(Math.min(MAX_PPS, Math.max(MIN_PPS, next)))
            }
            minPxPerSec={MIN_PPS}
            maxPxPerSec={MAX_PPS}
            markIn={markIn}
            markOut={markOut}
          />
        </div>
      </section>

      {/* Export settings, confirmed before anything is encoded. */}
      {exportOpen && (
        <div className="modal-overlay" onClick={() => setExportOpen(false)}>
          <div className="card an-export-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setExportOpen(false)}>
              ✕
            </button>
            <h2>Export video</h2>

            <div className="an-exp-grid">
              <label className="an-exp-label" htmlFor="exp-name">
                File name
              </label>
              <span className="an-exp-name">
                <input
                  id="exp-name"
                  className="an-prop-input"
                  value={exportName}
                  placeholder="animatic"
                  onChange={(e) => setExportName(e.target.value)}
                />
                <span className="tiny muted">.mp4</span>
              </span>

              <span className="an-exp-label">Format</span>
              <span className="tiny muted an-exp-fixed">
                MP4 · H.264 + AAC — plays everywhere
              </span>

              <label className="an-exp-label" htmlFor="exp-res">
                Resolution
              </label>
              <select
                id="exp-res"
                className="an-select"
                value={settings.resolution ?? 1080}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, resolution: Number(e.target.value) }))
                }
              >
                {RESOLUTIONS.map((r) => {
                  const [w, h] = frameSizeFor(settings.aspect_ratio, r.id);
                  return (
                    <option key={r.id} value={r.id}>
                      {r.label} — {w}×{h}
                    </option>
                  );
                })}
              </select>

              <label className="an-exp-label" htmlFor="exp-fps">
                Frame rate
              </label>
              <select
                id="exp-fps"
                className="an-select"
                value={settings.fps}
                onChange={(e) => setSettings((s) => ({ ...s, fps: Number(e.target.value) }))}
              >
                <option value={12}>12 fps</option>
                <option value={24}>24 fps (film)</option>
                <option value={25}>25 fps</option>
                <option value={30}>30 fps</option>
              </select>

              <label className="an-exp-label" htmlFor="exp-q">
                Quality
              </label>
              <select
                id="exp-q"
                className="an-select"
                value={settings.quality || "high"}
                onChange={(e) => setSettings((s) => ({ ...s, quality: e.target.value }))}
              >
                <option value="high">High — best looking</option>
                <option value="medium">Medium</option>
                <option value="low">Low — smallest file</option>
              </select>

              <label className="an-exp-label" htmlFor="exp-end">
                Video length
              </label>
              <select
                id="exp-end"
                className="an-select"
                value={settings.end_at || "timeline"}
                onChange={(e) => setSettings((s) => ({ ...s, end_at: e.target.value }))}
              >
                <option value="timeline">
                  Whole timeline — {formatTime(spanMs)}
                </option>
                <option value="frames">Just the images — {formatTime(totalMs)}</option>
              </select>

              <span className="an-exp-label">Audio</span>
              <label className="an-check">
                <input
                  type="checkbox"
                  checked={settings.include_audio !== false}
                  disabled={!audioTracks.length}
                  onChange={(e) =>
                    setSettings((s) => ({ ...s, include_audio: e.target.checked }))
                  }
                />
                {audioTracks.length
                  ? `Include ${audioTracks.length} track${audioTracks.length === 1 ? "" : "s"}`
                  : "No audio on this animatic"}
              </label>
            </div>

            <div className="an-exp-summary">
              <strong>{formatTime(exportMs)}</strong>
              <span>
                {frameSizeFor(settings.aspect_ratio, settings.resolution ?? 1080).join("×")}
              </span>
              <span>{settings.fps} fps</span>
              <span>
                {frames.length} frame{frames.length === 1 ? "" : "s"}
              </span>
              {texts.length > 0 && <span>{texts.length} text</span>}
            </div>
            {/* No size estimate on purpose: an animatic is mostly still frames,
                which compress far better than normal video, so any figure we
                printed would be wrong by a wide margin. */}
            <p className="tiny muted an-exp-note">
              {settings.end_at === "frames"
                ? `Stops at your last image — ${formatTime(totalMs)}. Anything after it is cut.`
                : spanMs > totalMs
                  ? `Runs to ${formatTime(spanMs)}: your last image is held on screen while the rest of the audio plays. Choose “Just the images” to stop at ${formatTime(totalMs)} instead.`
                  : `${formatTime(totalMs)} — your images, text and audio all end together.`}
            </p>

            <div className="an-name-actions">
              <button type="button" className="btn ghost" onClick={() => setExportOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={!frames.length}
                onClick={() => {
                  setExportOpen(false);
                  startExport();
                }}
              >
                <Icon name="download" /> Export
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ＋ Add layer — pick what kind. */}
      {/* Adds an EMPTY lane and nothing else. It used to add content — an
          upload dialog for images, a caption, a shape — which is not what
          "add a layer" means: you add the row, then you put things on it with
          that row's own ＋. */}
      {layerMenu && (
        <div className="modal-overlay" onClick={() => setLayerMenu(false)}>
          <div className="card an-layer-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setLayerMenu(false)}>
              ✕
            </button>
            <h2>Add a layer</h2>
            <p className="muted">
              A new, empty row on the timeline. Fill it afterwards with the ＋ on
              the layer itself.
            </p>

            <div className="an-layer-list">
              {[
                {
                  kind: "image",
                  ico: "🖼",
                  label: "Images",
                  note: "Pictures composited OVER the video — a logo, an inset, a cut-in",
                },
                {
                  kind: "text",
                  ico: "T",
                  label: "Text",
                  note: "Another row of captions, timed on their own",
                },
                {
                  kind: "shape",
                  ico: "◆",
                  label: "Shape",
                  note: "Another row for boxes, circles, pentagons and stars",
                },
                {
                  kind: "audio",
                  ico: "♪",
                  label: "Audio",
                  note: `An empty track, mixed with the others (${audioTracks.length}/${MAX_AUDIO_TRACKS})`,
                  disabled: audioTracks.length >= MAX_AUDIO_TRACKS,
                  disabledNote: `You already have the maximum of ${MAX_AUDIO_TRACKS} tracks`,
                },
              ].map((opt) => (
                <button
                  key={opt.kind}
                  type="button"
                  className="an-layer-opt"
                  disabled={opt.disabled}
                  onClick={() => {
                    setLayerMenu(false);
                    addLayer(opt.kind);
                  }}
                >
                  <span className="an-layer-opt-ico">{opt.ico}</span>
                  <span>
                    <strong>{opt.label}</strong>
                    <span className="tiny muted">
                      {opt.disabled ? opt.disabledNote : opt.note}
                    </span>
                  </span>
                </button>
              ))}

              {/* Listed because it's the obvious fifth thing to look for —
                  saying "not yet" is better than leaving people hunting. */}
              <button type="button" className="an-layer-opt" disabled>
                <span className="an-layer-opt-ico">🎞</span>
                <span>
                  <strong>Video</strong>
                  <span className="tiny muted">
                    Not supported yet — an animatic is stills plus audio
                  </span>
                </span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- Animate a shot with Veo: write the motion, then see the price -- */}
      {/* ⚠ TWO STEPS, ALWAYS. This first panel spends nothing — it only writes
          the prompt and picks the quality. The button at the bottom asks the
          server what that would cost and hands over to the confirm dialog
          below. No button in this editor renders anything directly. */}
      {animateFor !== null && !animateConfirm && (
        <div className="modal-overlay" onClick={() => setAnimateFor(null)}>
          <div className="card an-name-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setAnimateFor(null)}>
              ✕
            </button>
            <h2>Animate this shot</h2>
            <p className="muted">
              Veo turns this still into real footage. Describe what MOVES — the
              picture already says what it is.
            </p>
            <textarea
              className="an-tp-text"
              autoFocus
              rows={3}
              value={animatePrompt}
              placeholder="e.g. he lowers the lamp and turns towards the door; slow push in"
              maxLength={1000}
              onChange={(e) => setAnimatePrompt(e.target.value)}
            />

            <div className="an-prop-row">
              <span className="an-prop-label">Quality</span>
              <select
                className="an-select"
                value={animateRender.tier}
                onChange={(e) =>
                  setAnimateRender((r) => ({ ...r, tier: e.target.value }))
                }
              >
                <option value="lite">Lite — cheapest</option>
                <option value="fast">Fast — the usual choice</option>
                <option value="standard">Standard — dearest</option>
              </select>
            </div>

            <div className="an-prop-row">
              <span className="an-prop-label">Size</span>
              <select
                className="an-select"
                value={animateRender.resolution}
                onChange={(e) =>
                  setAnimateRender((r) => ({ ...r, resolution: e.target.value }))
                }
              >
                <option value="720p">720p</option>
                <option value="1080p">1080p</option>
              </select>
            </div>

            <div className="an-prop-row">
              <span className="an-prop-label">Length</span>
              <select
                className="an-select"
                value={animateRender.duration_seconds}
                onChange={(e) =>
                  setAnimateRender((r) => ({
                    ...r,
                    duration_seconds: Number(e.target.value),
                  }))
                }
              >
                <option value={4}>4 seconds</option>
                <option value={6}>6 seconds</option>
                <option value={8}>8 seconds</option>
              </select>
            </div>

            {/* Off by default here, unlike the final-video workspace: an
                animatic usually already carries a scratch voiceover, and sound
                costs more for something you are about to mute. */}
            <label className="an-check">
              <input
                type="checkbox"
                checked={animateRender.generate_audio}
                onChange={(e) =>
                  setAnimateRender((r) => ({ ...r, generate_audio: e.target.checked }))
                }
              />
              Let Veo generate sound too (costs more)
            </label>

            <div className="an-name-actions">
              <button type="button" className="btn ghost" onClick={() => setAnimateFor(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={!animatePrompt.trim() || animateBusy}
                onClick={() => askToAnimate(Boolean(veoFor(animateFor)?.upload_id))}
              >
                {animateBusy ? "Checking the price…" : "See the price →"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- The confirm dialog: the last thing before money moves --------- */}
      {/* Same shape as FinalVideoRenderStep's, deliberately — this is the one
          screen in the app where a familiar layout is worth more than a novel
          one. `.an-name-actions` for the footer, NOT `.lib-confirm-btns`. */}
      {animateConfirm && (
        <div className="modal-overlay" onClick={() => setAnimateConfirm(null)}>
          <div className="card fv-confirm" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="modal-close"
              onClick={() => setAnimateConfirm(null)}
            >
              ✕
            </button>
            <h2>Animate this shot?</h2>

            <div className="fv-confirm-price">
              <span className="fv-confirm-usd">
                ~${animateConfirm.estimate.usd.toFixed(2)}
              </span>
              <span className="tiny muted">estimated</span>
            </div>

            <p className="muted">
              {animateConfirm.estimate.seconds}s of video at {animateRender.tier} /{" "}
              {animateRender.resolution}
              {animateRender.generate_audio ? " with sound" : ", silent"}.
              {animateConfirm.force &&
                " This shot already has a clip — rendering again costs the same as the first time."}
            </p>
            <p className="tiny muted">
              An estimate from list prices, not a quote. Google bills the actual
              amount, and you are only charged for renders that succeed.
            </p>

            <div className="an-name-actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setAnimateConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={animateBusy}
                onClick={doAnimate}
              >
                <Icon name="play" />{" "}
                {animateBusy
                  ? "Starting…"
                  : `Animate — ~$${animateConfirm.estimate.usd.toFixed(2)}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- Captions / voiceover: the panel, then the price --------------- */}
      {/* ⚠ TWO STEPS, exactly as ✨ Animate. This panel spends nothing — it
          picks the track or the voice. The button at the bottom asks the server
          what that would cost and hands over to the confirm dialog below. */}
      {speechFor !== null && !speechConfirm && (
        <div className="modal-overlay" onClick={() => setSpeechFor(null)}>
          <div className="card an-name-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setSpeechFor(null)}>
              ✕
            </button>
            {speechFor === "captions" ? (
              <>
                <h2>Write captions from a track</h2>
                <p className="muted">
                  Listens to one audio track and writes a caption for each line,
                  timed to when it is said. They arrive as ordinary text clips —
                  every one can be edited, restyled or deleted afterwards.
                </p>
                <div className="an-prop-row">
                  <span className="an-prop-label">Track</span>
                  <select
                    className="an-select"
                    value={speechTrack}
                    onChange={(e) => setSpeechTrack(e.target.value)}
                  >
                    {audioTracks.map((t, i) => (
                      <option key={t.upload_id} value={t.upload_id}>
                        {t.filename || `Track ${i + 1}`}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="an-prop-row">
                  <label className="an-tp-field">
                    <span>Language</span>
                    <input
                      className="an-name-input an-speech-lang"
                      value={speechLanguage}
                      placeholder="let the model tell"
                      onChange={(e) => setSpeechLanguage(e.target.value)}
                    />
                  </label>
                </div>
              </>
            ) : (
              <>
                <h2>Read the dialogue aloud</h2>
                <p className="muted">
                  Every spoken line on the storyboard, read in order and laid
                  under the shot it belongs to. A line longer than its shot
                  pushes the next one later rather than talking over it.
                </p>
                <div className="an-prop-row">
                  <span className="an-prop-label">Voice</span>
                  <select
                    className="an-select"
                    value={speechVoice}
                    onChange={(e) => setSpeechVoice(e.target.value)}
                  >
                    {["Kore", "Puck", "Charon", "Zephyr", "Fenrir", "Aoede"].map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </div>
                <label className="an-check">
                  <input
                    type="checkbox"
                    checked={speechCaptions}
                    onChange={(e) => setSpeechCaptions(e.target.checked)}
                  />
                  Add captions for the spoken lines too (free — the timings come
                  back with the audio)
                </label>
              </>
            )}

            <label className="an-check">
              <input
                type="checkbox"
                checked={speechReplace}
                onChange={(e) => setSpeechReplace(e.target.checked)}
              />
              Replace captions a previous run made (captions you typed are never
              touched)
            </label>

            {/* ⚠ IN HERE, not in the status bar. The banner is behind this
                overlay, so an error written there is an error nobody sees and a
                button that looks broken. */}
            {speechError && <p className="an-prop-warn">⚠ {speechError}</p>}

            <div className="an-name-actions">
              <button type="button" className="btn ghost" onClick={() => setSpeechFor(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={speechBusy || (speechFor === "captions" && !speechTrack)}
                onClick={askForSpeech}
              >
                {speechBusy ? "Checking the price…" : "See the price →"}
              </button>
            </div>
          </div>
        </div>
      )}

      {speechConfirm && (
        <div className="modal-overlay" onClick={() => setSpeechConfirm(null)}>
          <div className="card fv-confirm" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="modal-close"
              onClick={() => setSpeechConfirm(null)}
            >
              ✕
            </button>
            <h2>{speechFor === "captions" ? "Write the captions?" : "Read the dialogue?"}</h2>

            <div className="fv-confirm-price">
              <span className="fv-confirm-usd">
                ~${speechConfirm.estimate.usd.toFixed(4)}
              </span>
              <span className="tiny muted">estimated</span>
            </div>

            <p className="muted">
              {speechFor === "captions"
                ? `${Math.round(speechConfirm.estimate.seconds)}s of audio, transcribed by ${speechConfirm.estimate.model}.`
                : `${speechConfirm.estimate.lines} line(s), ${speechConfirm.estimate.characters} characters, read by ${speechVoice}.`}
            </p>
            {speechConfirm.estimate.over_limit && (
              <p className="an-prop-warn">
                ⚠ That is over the limit for one run ({speechConfirm.estimate.limit}).
                Do it in smaller passes — this is a spend guard, not a technical one.
              </p>
            )}
            <p className="tiny muted">
              An estimate from list prices, not a quote. Google bills the actual
              amount.
            </p>

            <div className="an-name-actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setSpeechConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={speechBusy || speechConfirm.estimate.over_limit}
                onClick={doSpeech}
              >
                <Icon name="play" />{" "}
                {speechBusy
                  ? "Starting…"
                  : `Go — ~$${speechConfirm.estimate.usd.toFixed(4)}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Save on an unnamed animatic lands here first. */}
      {saveAsName !== null && (
        <div className="modal-overlay" onClick={() => setSaveAsName(null)}>
          <div className="card an-name-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setSaveAsName(null)}>
              ✕
            </button>
            <h2>Save animatic as…</h2>
            <p className="muted">
              This animatic hasn't got a name yet. Give it one and it'll show up
              in Your Animatics under that title.
            </p>
            <input
              className="an-name-input"
              autoFocus
              value={saveAsName}
              placeholder="e.g. Episode 1 — opening scene"
              maxLength={120}
              onChange={(e) => setSaveAsName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") confirmSaveAs();
                if (e.key === "Escape") setSaveAsName(null);
              }}
            />
            <div className="an-name-actions">
              <button type="button" className="btn ghost" onClick={() => setSaveAsName(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={!saveAsName.trim()}
                onClick={confirmSaveAs}
              >
                <Icon name="save" /> Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* The real clock. Hidden, but it is what the pictures follow — the first
          track that is actually playing drives the playhead. */}
      {audioTracks.map((track) =>
        audioUrls[track.upload_id] ? (
          <audio
            key={track.upload_id}
            ref={(el) => {
              if (el) audioElsRef.current[track.upload_id] = el;
              else delete audioElsRef.current[track.upload_id];
            }}
            src={audioUrls[track.upload_id]}
            preload="auto"
          />
        ) : null
      )}

      {/* One hidden input per media type, shared by every entry point that
          adds to that layer (the pane, the strip and the timeline's ＋). */}
      <input
        ref={imageInputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) addFiles(sortFiles(e.target.files));
          e.target.value = "";
        }}
      />
      <input
        ref={audioInputRef}
        type="file"
        accept="audio/*"
        hidden
        onChange={(e) => {
          pickAudio(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {/* Pictures for an IMAGE LAYER. Separate from the asset input because
          these become overlays on one lane, not frames in the sequence. */}
      <input
        ref={overlayInputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) {
            addOverlayFiles(e.target.files, pendingOverlayLane.current);
          }
          pendingOverlayLane.current = "";
          e.target.value = "";
        }}
      />
      {/* The Media pane's single control — takes both kinds and sorts them out. */}
      <input
        ref={assetInputRef}
        type="file"
        accept="image/*,audio/*"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) addAssets(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
