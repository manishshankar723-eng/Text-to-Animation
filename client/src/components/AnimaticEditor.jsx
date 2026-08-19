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
//   animatic/selection.js            what "the selection" is, now that it is a
//                                    LIST rather than one id — and groups
//   components/properties/           the Properties panes
//
// ⚠ TWO KINDS OF "SELECTED", and they are not the same thing: the six
// `selected*Id` states are the PRIMARY — the one clip the Properties pane
// describes — and `selection` is the whole list a rubber band, a shift-click or
// a group produces. `selectOnly` is the only writer of both; read its comment
// before adding a third way to select something.
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
  frameOrigin,
  frameSpans,
  frameTrack,
  lookProps,
  lookPropParts,
  lookValueOf,
  resolveLook,
  pictureTracks,
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
import {
  audioEndMs,
  clipAt,
  clipId,
  crossfadePatch,
  crossfadeTarget,
  DEFAULT_CROSSFADE_MS,
  fadeEndPatch,
  laneClips,
  MIN_CLIP_MS,
  splitClip,
} from "../animatic/audio_clips.js";
import { CAPTION_LAYER_ID, CAPTION_LAYER_NAME } from "../animatic/captions.js";
import { MIN_SPLIT_MS, splitTimedClip } from "../animatic/razor.js";
import {
  PRESETS as EXPORT_PRESETS,
  applyPreset,
  matchPreset,
  containerExt,
  normaliseContainer,
} from "../animatic/export_presets.js";
import {
  expandGroup,
  expandSelection,
  GROUPABLE,
  selectionLabel,
  toggleItems,
  uniqueItems,
} from "../animatic/selection.js";
import { clamp } from "../animatic/util.js";
import {
  WORKSPACES,
  getWorkspace,
  saveWorkspace,
  workspaceIcon,
  workspaceLabel,
} from "../animatic/workspace.js";
import {
  clampLayout,
  defaultLayout,
  getPaneLayout,
  paneLimits,
  savePaneLayout,
  viewport,
} from "../animatic/pane_layout.js";
import {
  ASPECTS,
  aspectNumber,
  frameSizeFor,
  knownAspect,
  refitBox,
} from "../animatic/aspects.js";
import { MEDIA_VIEWS, getMediaView, saveMediaView } from "../animatic/media_view.js";
import useAnimaticProject from "../animatic/useAnimaticProject.js";
import useAudioAnalysis from "../animatic/useAudioAnalysis.js";
import { forgetAudio } from "../animatic/beats.js";
import { beatMarks, cutsToDurations, planBeatCuts } from "../animatic/beat_cut.js";
import useTimelineTransport, { useMonitorVideo } from "../animatic/useTimelineTransport.js";
import useUndoStack from "../animatic/useUndoStack.js";
import FrameStrip, { sortFiles } from "./FrameStrip.jsx";
import { UNTITLED } from "./AnimaticLibrary.jsx";
import Timeline, { formatTime } from "./Timeline.jsx";
import Icon from "./Icon.jsx";
import PaneSplitter from "./PaneSplitter.jsx";
import ProgramCanvas from "./ProgramCanvas.jsx";
import ShapeGallery, {
  DEFAULT_SHAPE_COLOR,
  SHAPE_KINDS,
  ShapeSwatch,
} from "./Shapes.jsx";
import EffectsPanel from "./EffectsPanel.jsx";
import EffectsLibrary from "./EffectsLibrary.jsx";
import { FX_ITEM_COUNT, fxEntry } from "../animatic/fx_library.js";
import { MAX_EFFECTS } from "../animatic/gl/shaders/layer.js";
import RegeneratePanelInline, { RelengthShotInline } from "./RegeneratePanelInline.jsx";
import {
  AudioProperties,
  FrameProperties,
  SelectionProperties,
  ShapeProperties,
  TextProperties,
  TransitionProperties,
  VideoProperties,
} from "./properties/index.js";
// The two layout primitives this file uses directly: `PropRow` for the rows it
// slots INTO a pane (the reframe button), so they line up with the rows around
// them, and `PropGroup` for the Media pane's own sections.
// ⚠ THE MEDIA PANE USES THE PROPERTIES PANE'S SECTION ON PURPOSE. A second,
// media-only collapsible would be the same control drawn twice — same twist,
// same count pill, one of them subtly different — and the two panes sit side by
// side. One component means Frames collapses exactly the way Motion does.
import { PropGroup, PropRow, openGroup } from "./properties/PropGroup.jsx";

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

// The long edge each frame's picture is FETCHED at, for the monitor and the
// timeline. Half of the export's 1920 (`LONG_EDGE` in animatic.py), which is
// where the phrase "half-res proxy" comes from — and one of the rungs
// `proxies.PROXY_EDGES` offers, so every editor session shares one cached file
// per picture rather than writing one per window size.
//
// ⚠ THE PREVIEW, NEVER THE EXPORT. The encoder opens the source files; nothing
// on this side of the wire can reach it. What this trades away is SHARPNESS at
// high zoom and nothing else — a proxy is a lossless resize, so colour, timing
// and geometry are untouched. See the rules at the top of `proxies.py`.
const PREVIEW_MAX_EDGE = 960;

// The frame shapes and their pixel sizes moved to `animatic/aspects.js` — the
// Shape chips in Video properties, the Program pane's picker and the export
// dialog's size table all read the one list now.

const newId = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

// RETIRE a blob that has just been replaced, rather than revoking it on the
// spot. The <img> or <video> showing it is still showing it until React has
// committed the new one, and revoking underneath that renders a broken tile for
// a frame — which on a redraw looks exactly like the redraw failed. The delay
// is one macrotask, which is a commit and then some. Same rule as
// `StoryboardBoard.refreshPanelImage`.
const retireBlob = (url) => {
  if (url) setTimeout(() => URL.revokeObjectURL(url), 2000);
};

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
//
// ⚠ `url` IS SET HERE, and it has to be. Every other clip factory that has a
// file behind it sets one, because the thumbnail effect only fetches frames that
// HAVE a url — and this one didn't, so a freshly uploaded video sat on the
// loading spinner in the Media pane until the page was reloaded and the server
// filled a url in. Reported as "I upload a video file here but it doesn't show
// in the media panel", and it looked like an upload still running.
// `?poster=1` because the file itself is a VIDEO: the raw route hands back an
// MP4, which an <img> can only fail to draw. See `_video_poster` on the server.
const newVideoClip = (uploadId, durationMs, label, animaticId) => ({
  id: newId(),
  src: { kind: "video", upload_id: uploadId },
  kind: "video",
  url: `/animatics/${animaticId}/media/${uploadId}?poster=1`,
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
  {
    id: "select",
    key: "V",
    label: "Selection",
    hint:
      "Select and move clips · drag the empty part of a lane to select several · " +
      "shift-click to add one · double-click a lane's name for the whole row",
  },
  { id: "razor", key: "C", label: "Razor", hint: "Click a clip — a picture, a caption, a shape or audio — to split it there" },
  // ⚠ THESE TWO ARE NO LONGER "the only way a trim behaves" — they are the two
  // ways it can behave OTHER than the plain one. With V a picture's edge moves and
  // nothing else does (a gap is left, and a gap shows the track underneath), which
  // is what "each layer independent" means and what the picture track could not do
  // while it was one butt-jointed sequence. B is the old behaviour, kept as a tool.
  { id: "ripple", key: "B", label: "Ripple edit", hint: "Trim an edge and close up behind it — everything after it on that track moves too" },
  { id: "rolling", key: "N", label: "Rolling edit", hint: "Trim an edge and let the next clip absorb it — the cut moves, the track stays the same length" },
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
  // frame id → the SERVER PATH that blob was fetched from, `?v=` and all.
  // ⚠ Not the same question as "have I got a blob for this clip?", and the
  // difference is the whole of "I press Regenerate and nothing happens": a
  // redrawn panel keeps its frame id and its route, and only the version moves.
  // See the fetch effect below.
  const urlSrcRef = useRef({});
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
  // …and how that footage is listed: thumbnails in a grid, or compact rows.
  // Remembered per browser, like the workspace — see animatic/media_view.js.
  const [mediaView, setMediaView] = useState(getMediaView);
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
  // Which LAYOUT the panes are arranged in — long-form or reel/shorts. ⚠ UI
  // ONLY: it rearranges the screen and never touches the project's frame size
  // or aspect ratio. Remembered per browser, so it survives a reload.
  const [workspace, setWorkspace] = useState(getWorkspace);
  // The ⚙ menu in the top bar. Null = closed.
  const [settingsOpen, setSettingsOpen] = useState(false);
  // How big the panes are — three px sizes the seams between them drag (see
  // `animatic/pane_layout.js`). Loaded for THIS workspace, since the two
  // layouts want different shapes and each remembers its own.
  const [layout, setLayout] = useState(() => getPaneLayout(getWorkspace()));
  // The window, as state rather than a read at render time: the limits a drag is
  // clamped against are a fraction of it, so they have to change when it does.
  const [vp, setVp] = useState(viewport);
  // ⚠ THE PANE-SIZE HOOKS LIVE UP HERE WITH THE REST, and not beside the layout
  // code they belong to further down. That code sits BELOW `if (loading)
  // return …`, so a hook there runs on the second render and not the first —
  // "Rendered more hooks than during the previous render", and a blank editor.
  useEffect(() => {
    const onResize = () => setVp(viewport());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  // Written after the drag rather than during it — a pointer move fires dozens
  // of times a second and every one of them would be a JSON write.
  // ⚠ ONLY ONCE A SEAM HAS ACTUALLY BEEN DRAGGED. Saving the defaults on mount
  // would freeze the sizes this window happened to open at, and the defaults are
  // deliberately a fraction of the window — opening the editor once on a laptop
  // would then hand the same panes to a 4K screen forever.
  const layoutTouched = useRef(false);
  useEffect(() => {
    if (!layoutTouched.current) return;
    const t = setTimeout(() => savePaneLayout(workspace, layout), 250);
    return () => clearTimeout(t);
  }, [workspace, layout]);
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
  // ⚠ HOW MANY EMPTY PICTURE TRACKS TO DRAW BEYOND THE ONES IN USE, and it is
  // VIEW state on purpose — not saved. A picture track is a NUMBER on a clip
  // (`frameTrack`), so the rows are derived from which numbers are in use; there
  // is no record to create. This is only "the user asked for a row to drop onto",
  // and an empty row that survived a reload would be a layer the document does not
  // have. Anything actually put on it makes the row real.
  const [extraPictureTracks, setExtraPictureTracks] = useState(0);
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

  // --- Phase 7: reaching back to the BOARD, and framing for a new shape ---
  // ⚠ THE RE-BLOCK RUNS ON THE STORYBOARD'S JOB, not this one. The key poses
  // belong to the board, so the id being polled is the board's — which is why
  // this is its own poll rather than a branch of the one above, and why it is
  // NOT part of `serverBusy`: this animatic is not busy, so the autosave carries
  // on as normal and the user can keep cutting while the drawings arrive.
  const [reblockFor, setReblockFor] = useState(null); // frame id, or null
  const [reblockJob, setReblockJob] = useState(null); // the BOARD's job id
  const [reblockProgress, setReblockProgress] = useState(null);

  // Auto-reframe. Two steps like every other paid path here: a panel that
  // spends nothing, then a priced confirmation, then the call.
  const [reframeOpen, setReframeOpen] = useState(false);
  const [reframeAspect, setReframeAspect] = useState("");
  const [reframeScope, setReframeScope] = useState("all"); // "all" | "selection"
  const [reframeConfirm, setReframeConfirm] = useState(null);
  const [reframeBusy, setReframeBusy] = useState(false);
  // Its own error, inside the panel — the editor's banner renders BEHIND the
  // modal overlay. Same lesson as `speechError`.
  const [reframeError, setReframeError] = useState("");
  const [reframeRunning, setReframeRunning] = useState(false);
  const [reframeProgress, setReframeProgress] = useState(null);

  const textAreaRef = useRef(null);
  const audioInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const assetInputRef = useRef(null);
  // The Video row's ＋. A picker of its own rather than the general one, because
  // the row it fills holds one kind of clip: offering images on it would put them
  // somewhere the user did not press.
  const videoInputRef = useRef(null);
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
  const serverBusy = exporting || animating || speechRunning || reframeRunning;

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

  // ⚠ BOTH COME OUT OF `frameSpans` NOW — the same evaluator the monitor and the
  // exporter use. They used to be a running total written here, which was exact
  // while the picture was ONE sequence laid end to end and is wrong now that clips
  // are placed by `start_ms` on numbered TRACKS: "add up the clips before it"
  // answers a question nobody is asking, and `totalMs` would have been the sum of
  // every track's lengths rather than the moment the last one ends.
  const pictureSpans = useMemo(() => frameSpans(frames), [frames]);
  const totalMs = pictureSpans.totalMs;
  // Where each picture starts, parallel to `frames` — what the caption tools, the
  // strip's badges and the Properties pane all read.
  const starts = useMemo(() => pictureSpans.spans.map((s) => s.start), [pictureSpans]);

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
      : // ⚠ By CLIP id, not by upload: after a cut, one file is two clips and
        // only one of them is the thing the pane is describing.
        audioTracks.find((a) => clipId(a) === selectedTrackId) || null;
  const selectedFrame =
    selectedTransition || selectedText || selectedShape || selectedOverlay || selectedTrack
      ? null
      : frames.find((f) => f.id === selectedId) || null;

  // --------------------------------------------------------- the selection
  // ⚠ TWO THINGS, AND THEY ARE NOT THE SAME THING. The six `selected*Id` above
  // are the PRIMARY — the one clip the Properties pane is describing, and there
  // is at most one of it. `selection` is the whole LIST, which is what a rubber
  // band, a shift-click and a group all produce, and what Delete, a drag and
  // Group act on. `selectOnly` keeps them in step: it is the only writer of
  // both, so the list can never disagree with the pane.
  //
  // Items are `{ kind, id }` — see `animatic/selection.js` for why an id alone
  // will not do.
  const [selection, setSelection] = useState([]);

  // The clip lists as `expandGroup` wants them: by kind, each carrying `id` and
  // `group_id`. ⚠ Audio is keyed by CLIP id, never by upload — after a cut one
  // file is several clips and only the piece in the group belongs in it.
  const groupPools = useMemo(
    () => ({
      text: texts,
      shape: shapes,
      overlay: overlays,
      audio: audioTracks.map((a) => ({ id: clipId(a), group_id: a.group_id || "" })),
    }),
    [texts, shapes, overlays, audioTracks]
  );

  // One helper so every "select this" path clears the others — the pane can
  // then never show something that isn't selected.
  //
  // ⚠ IT ALSO SETS THE LIST, and expands a group while doing it: clicking one
  // member of a group selects every member, which is the whole meaning of a
  // group. Doing that HERE rather than at the timeline's click handlers is what
  // makes it true of every path — the media pane, the monitor's handles and the
  // keyboard all go through this one function.
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
    const one = frame
      ? { kind: "frame", id: frame }
      : text
        ? { kind: "text", id: text }
        : shape
          ? { kind: "shape", id: shape }
          : overlay
            ? { kind: "overlay", id: overlay }
            : track
              ? { kind: "audio", id: track }
              : null;
    // A transition is not a clip and is never part of a multi-selection: it
    // belongs to a CUT, has no span to sweep over and nothing to be grouped
    // with. Selecting one empties the list, which is right — Delete then means
    // the transition, which is what the pane is showing.
    setSelection(one ? expandGroup(one, groupPools) : []);
  }

  /** Replace (or extend) the selection — the rubber band and "select this row". */
  function selectMany(items, { add = false, lane = "" } = {}) {
    const picked = expandSelection(items, groupPools);
    const next = add ? uniqueItems([...selection, ...picked]) : picked;
    setSelection(next);
    // The pane follows the FIRST thing picked up, so a selection is never
    // "nothing is selected" in one panel and forty clips in another.
    const head = next[0] || null;
    setSelectedId(head?.kind === "frame" ? head.id : null);
    setSelectedTextId(head?.kind === "text" ? head.id : null);
    setSelectedShapeId(head?.kind === "shape" ? head.id : null);
    setSelectedOverlayId(head?.kind === "overlay" ? head.id : null);
    setSelectedTrackId(head?.kind === "audio" ? head.id : null);
    setSelectedTransitionId(null);
    if (!next.length) {
      setNotice(lane ? `Nothing on ${lane} to select.` : "Nothing in that area.");
      return;
    }
    setNotice(
      `${selectionLabel(next)} selected — Delete removes them, Ctrl+G groups them.`
    );
  }

  /** Every clip on the timeline — what Ctrl+A means here. */
  function everything() {
    return [
      ...frames.map((f) => ({ kind: "frame", id: f.id })),
      ...texts.map((c) => ({ kind: "text", id: c.id })),
      ...shapes.map((s) => ({ kind: "shape", id: s.id })),
      ...overlays.map((o) => ({ kind: "overlay", id: o.id })),
      ...audioTracks.map((a) => ({ kind: "audio", id: clipId(a) })),
    ];
  }

  /** Shift-click: in if it was out, out if it was in. Groups toggle as one. */
  function toggleSelect(kind, id) {
    const next = toggleItems(selection, expandGroup({ kind, id }, groupPools));
    setSelection(next);
    // The primary follows what is still in the list, so the pane always
    // describes something that is actually selected.
    const head = next.find((item) => item.kind === kind && item.id === id) || next[0] || null;
    setSelectedId(head?.kind === "frame" ? head.id : null);
    setSelectedTextId(head?.kind === "text" ? head.id : null);
    setSelectedShapeId(head?.kind === "shape" ? head.id : null);
    setSelectedOverlayId(head?.kind === "overlay" ? head.id : null);
    setSelectedTrackId(head?.kind === "audio" ? head.id : null);
    setSelectedTransitionId(null);
  }

  // Where the LAST audio clip ends — what "fit frames to audio" matches, and
  // what the length comparison in the timeline header reports against. Measured
  // from where each clip SITS, so a piece dragged out to 0:40 makes the audio
  // forty seconds long even if the piece itself is two seconds.
  const audioMs = audioEndMs(audioTracks);
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

  // ------------------------------------------------------ which rows are OFF
  // ⚠ A HIDDEN ROW IS A PROPERTY OF THE PROJECT, NOT OF THIS BROWSER, and that
  // follows from what the eye in the gutter promises: the row is left out of the
  // VIDEO. A view preference (the workspace, the media view) can live in
  // localStorage because being wrong about it costs a glance; being wrong about
  // this costs an export. So it is saved — `settings.hidden_lanes` — and the same
  // list is read by the monitor below and by the encoder on the server.
  //
  // ⚠ IT NAMES A ROW, NOT ITS CLIPS. Emptying a hidden row, or adding more to it,
  // must not turn anything back on: what you switched off is the row. The token
  // is `kind:layer_id` for a lane of clips (`text:`, `text:<id>`, `shape:<id>`,
  // `image:<id>`) and `frames:<n>` for picture track n (the
  // picture track — an encoding the SERVER can rebuild from a clip's own fields,
  // which is what lets one list work in both places (`_lane_hidden` in
  // server/animatics.py). Audio is deliberately not in here: a track has `muted`,
  // which is this idea for a row you hear rather than see, and two switches for
  // one idea is worse than either.
  const hiddenLanes = useMemo(
    () => new Set(settings.hidden_lanes || []),
    [settings.hidden_lanes]
  );

  // The token for one lane — the ONE place the encoding is written on the client.
  const laneToken = (lane) => {
    if (lane.kind === "audio") return "";
    if (lane.kind === "frames") return `frames:${lane.track || 0}`;
    return `${lane.kind}:${lane.layerId || ""}`;
  };

  const toggleLaneHidden = (lane) => {
    const token = laneToken(lane);
    if (!token) return;
    const wasHidden = hiddenLanes.has(token);
    setSettings((s) => {
      const now = new Set(s.hidden_lanes || []);
      if (now.has(token)) now.delete(token);
      else now.add(token);
      return { ...s, hidden_lanes: [...now] };
    });
    setNotice(
      wasHidden
        ? `${lane.name} is back in the video.`
        : `${lane.name} is hidden — it stays on the timeline and is left out of the monitor and the export.`
    );
  };

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
  //
  // ⚠ WHAT GOES IN IS THE DOCUMENT WITH THE HIDDEN ROWS TAKEN OUT — see
  // `hiddenLanes` above. The eye in the timeline's gutter has to mean the same
  // thing here as it does in the MP4, and this is the one place the monitor's
  // answer comes from, so it is the one place that has to know.
  //
  // ⚠ A HIDDEN PICTURE TRACK IS BLANKED ON TRACK 0 AND DROPPED ABOVE IT, and the
  // asymmetry is not a compromise — the two are the SAME PICTURE where each one
  // applies, and only one of them is safe in each case.
  //
  //   TRACK 0 is the bottom of the stack, so what a dropped clip would reveal is
  //     the letterbox colour — which is exactly what a colour card of the
  //     letterbox colour draws. Blanking is chosen because it also HOLDS THE
  //     TIME: a base track hidden in full would otherwise leave the export with
  //     no pictures at all, which `build_animatic` cannot encode.
  //   ABOVE IT a dropped clip reveals the track UNDERNEATH, and an opaque card
  //     would hide it. So those are dropped, which is what an NLE shows for a
  //     track it is not outputting.
  //
  // ⚠ TWINNED IN `server/animatics.py`, clip for clip, or the monitor and the MP4
  // would disagree about a row you had switched off.
  const shown = useMemo(() => {
    const doc = { frames, texts, shapes, overlays, transitions };
    if (!hiddenLanes.size) return doc;
    const kept = (kind) => (clip) => !hiddenLanes.has(`${kind}:${clip.layer_id || ""}`);
    const hiddenTrack = (f) => hiddenLanes.has(`frames:${frameTrack(f)}`);
    return {
      ...doc,
      frames: frames
        .filter((f) => !(hiddenTrack(f) && frameTrack(f) > 0))
        .map((f) =>
          hiddenTrack(f)
            ? { ...f, kind: "color", color: settings.background || "#000000" }
            : f
        ),
      texts: texts.filter(kept("text")),
      shapes: shapes.filter(kept("shape")),
      overlays: overlays.filter(kept("image")),
    };
  }, [frames, texts, shapes, overlays, transitions, hiddenLanes, settings.background]);

  const scene = useMemo(
    () => sceneAt(shown, Math.min(timeMs, Math.max(0, spanMs - 1)), spanMs),
    [shown, timeMs, spanMs]
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
  // Stable, because `ProgramCanvas` builds its WebGL context once and a prop that
  // changed identity every render used to take the context down with it.
  const onGlUnavailable = useCallback(() => setGlFailed(true), []);
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

  // Steps by PICTURE, not by video frame — that is what the transport arrows
  // mean. It stays here rather than in the transport hook because it needs
  // `currentIndex`, and that comes from the scene, which is derived from the
  // clock the hook owns.
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
  //
  // ⚠ CACHED BY FRAME ID, RE-FETCHED WHEN THE URL MOVES. Those are two different
  // questions and this used to answer only the first: "have I got a blob for
  // this clip?". A storyboard panel redrawn from the Properties pane keeps its
  // frame id and its route — only the server's `?v=<mtime>` changes — so the
  // check `!urlsRef.current[f.id]` was true before the redraw and true after,
  // and the editor went on showing the old drawing for ever. `urlSrcRef`
  // remembers which url each blob came from, which is what makes a redraw
  // visible. See `_frame_version` in server/animatics.py.
  useEffect(() => {
    let alive = true;
    const wanted = new Set(frames.map((f) => f.id));

    // Drop pictures for frames that no longer exist.
    for (const id of Object.keys(urlsRef.current)) {
      if (!wanted.has(id)) {
        URL.revokeObjectURL(urlsRef.current[id]);
        delete urlsRef.current[id];
        delete urlSrcRef.current[id];
      }
    }

    // A COLOUR CARD IS SKIPPED, not fetched. It has no file behind it, so the
    // url the server fills in for every frame alike can only 404 — one wasted
    // request per card on every load, and a thumbnail stuck on its spinner
    // waiting for a picture that is never coming.
    const missing = frames.filter(
      (f) =>
        f.url &&
        (f.kind || "image") !== "color" &&
        (!urlsRef.current[f.id] || urlSrcRef.current[f.id] !== f.url)
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
              // A PROXY, not the source: these blobs draw the monitor and the
              // timeline tiles, never the MP4. See PREVIEW_MAX_EDGE.
              const url = await api.fetchAnimaticMedia(f.url, PREVIEW_MAX_EDGE);
              if (!alive) {
                URL.revokeObjectURL(url);
                return;
              }
              // NEW PIXELS FIRST, then retire the old blob — never revoke it on
              // the spot. The <img> is still showing it until React commits,
              // and revoking underneath it is a tile that flashes empty on
              // every redraw. Same rule as `StoryboardBoard.refreshPanelImage`.
              const stale = urlsRef.current[f.id];
              urlsRef.current[f.id] = url;
              urlSrcRef.current[f.id] = f.url;
              if (stale) retireBlob(stale);
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
      urlSrcRef.current = {};
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

  // Is anything on the captions lane? Kept as a boolean rather than reading
  // `texts` where it is used, so typing in a caption doesn't rebuild the lanes.
  const hasCaptionClips = useMemo(
    () => texts.some((c) => (c.layer_id || "") === CAPTION_LAYER_ID),
    [texts]
  );

  // ------------------------------------------------- the picture track, split
  // The same one sequence, grouped by where each clip came from (`frameOrigin`).
  //
  // ⚠ THIS IS A VIEW, NOT THREE TRACKS. `frames` is still ONE sequence played in
  // order, and it has to be: the clips are laid end to end, so a length here is
  // a time everything else — the audio, the captions, the export — is measured
  // against. What the grouping buys is the question you actually ask of the
  // Media pane ("where's the video I just dropped in?") and of the timeline
  // ("which row is my footage on?"), neither of which the one long strip could
  // answer once a board ran to thirty panels.
  //
  // `at` is what keeps it honest: every clip carries its INDEX IN THE FULL
  // SEQUENCE, so a number badge, a drop position and a reorder all mean the same
  // thing in a section as they do in the whole strip.
  const pictureTrack = useMemo(() => {
    const groups = { board: [], video: [], image: [] };
    const at = new Map();
    frames.forEach((f, i) => {
      at.set(f.id, i);
      groups[frameOrigin(f)].push(f);
    });
    return { ...groups, at };
  }, [frames]);

  // A clip's place in the whole sequence — for the strip's numbers, and for
  // translating a drop inside one section back into the sequence.
  const seqIndex = useCallback(
    (frame) => pictureTrack.at.get(frame?.id) ?? 0,
    [pictureTrack]
  );

  // ----------------------------------------------------------------- lanes
  // ONE list describing every row on the timeline, in top-to-bottom order. The
  // gutter labels and the tracks are both generated from it, so a label can
  // never end up beside the wrong lane (which is exactly what happened when the
  // two were written out separately and matched by position).
  //
  // ⚠ THE ORDER IS THE COMPOSITING ORDER, TOP OF THE STACK FIRST — captions,
  // text, shapes, overlay pictures, the picture sequence, its video, then audio
  // at the bottom. That is what an NLE means by a track above another one: it
  // draws over it. Reading it from the bottom up (audio, video, images, shapes,
  // text, captions) is reading the export from the back to the front, which is
  // the order it was asked for and the order it is built in.
  //
  // Within a kind the DEFAULT lane comes first, then the ones the user added.
  //
  // ⚠ THE CAPTIONS LANE IS FIRST OF ALL. It is written by the server (a captions
  // or voiceover run), it is the row you check against the audio you just cut,
  // and burying it among however many text layers the project has is what made
  // generated captions look like they had landed on top of the user's own text.
  // Top of the stack is also where a subtitle track sits in every NLE.
  //
  // ⚠ AND THERE IS A ROW PER PICTURE TRACK. It used to be two rows of ONE
  // sequence, filtered by where each clip came from (`only` / `frameOrigin`) —
  // which looked like two layers and was not: every clip's place was the sum of
  // the clips before it, so trimming footage moved the stills, on the row above.
  // Reported as "when i do video trim so i see my image layer conetnt move like
  // snip … i want user move independaly each asstes/conetnt in layer", and true
  // by construction rather than by accident. A row is a real track now: a clip
  // carries which one it is on (`track`) and where it sits (`start_ms`), a higher
  // track draws OVER a lower one, and a gap shows whatever is underneath.
  const lanes = useMemo(() => {
    // Everything except the captions lane, which is placed by hand below.
    const of = (kind) =>
      layers.filter((l) => l.kind === kind && l.id !== CAPTION_LAYER_ID);
    const out = [];
    // ⚠ SHOWN WHENEVER THERE ARE CAPTION CLIPS, even if the layer record is
    // missing. A clip whose lane doesn't exist is filtered out of every lane
    // there is — it would be invisible on the timeline while still drawing in
    // the monitor and the export, which reads as captions that cannot be
    // deleted. This is the safety net for a project the server wrote a lane for
    // and something later dropped.
    const captionLayer = layers.find((l) => l.id === CAPTION_LAYER_ID);
    if (captionLayer || hasCaptionClips) {
      out.push({
        key: CAPTION_LAYER_ID,
        kind: "text",
        name: captionLayer?.name || CAPTION_LAYER_NAME,
        layerId: CAPTION_LAYER_ID,
        removable: true,
        icon: "❝",
        hint: "Captions written from a track — a run replaces this row, never your own text",
        add: "Add a caption to this row by hand",
      });
    }
    out.push({ key: "text:", kind: "text", name: "Text", layerId: "", removable: false });
    for (const l of of("text")) {
      out.push({ key: l.id, kind: "text", name: l.name, layerId: l.id, removable: true });
    }
    out.push({ key: "shape:", kind: "shape", name: "Shapes", layerId: "", removable: false });
    for (const l of of("shape")) {
      out.push({ key: l.id, kind: "shape", name: l.name, layerId: l.id, removable: true });
    }
    // Pictures composited OVER the sequence sit directly above it: they are the
    // last thing drawn before the frame itself.
    for (const l of of("image")) {
      out.push({ key: l.id, kind: "image", name: l.name, layerId: l.id, removable: true });
    }
    // The picture tracks, HIGHEST FIRST — the same rule the rest of this list
    // follows, since a higher track is drawn over a lower one. Track 0 always
    // exists (`pictureTracks`), so a project with no pictures still has a row to
    // put some on.
    //
    // ⚠ NAMED LIKE THE OTHER LAYERS: "Pictures", then "Pictures 2", … so the
    // gutter reads consistently and the numbering matches what "+ Add layer"
    // says it is adding. They are not removable: a picture track is structural,
    // and its ✕ empties it (`onClearLane`) exactly as the default text and shape
    // rows do.
    // The tracks in use, plus any empty ones asked for — see `extraPictureTracks`.
    const usedTracks = pictureTracks(frames);
    const allTracks = [...usedTracks];
    for (let n = Math.max(...usedTracks) + 1; n <= extraPictureTracks; n += 1) {
      allTracks.push(n);
    }
    for (const track of allTracks.reverse()) {
      out.push({
        key: `frames:${track}`,
        kind: "frames",
        track,
        name: track === 0 ? "Pictures" : `Pictures ${track + 1}`,
        layerId: null,
        removable: false,
        // Is this row carrying stills AND footage? If so it can be split into
        // two — see `splitFootageOntoTrack`, and the ▶⇧ in the gutter.
        mixed: (() => {
          const on = frames.filter((f) => frameTrack(f) === track);
          const video = on.filter((f) => frameOrigin(f) === "video").length;
          return video > 0 && video < on.length;
        })(),
        hint:
          track === 0
            ? "The base picture track — stills and footage, each placed on its own"
            : `Picture track ${track + 1} — drawn OVER the tracks below it; a gap shows what is under it`,
        add: "Add pictures to the end of this track",
      });
    }
    // Audio: a track saved before layers owns its own lane (that is how it has
    // always been drawn); a track added to a layer sits on that layer's lane,
    // which exists even while it is still empty.
    //
    // ⚠ A LANE CARRIES A LIST OF CLIPS, not one track. Cutting a track in half
    // leaves two entries reading the same file, and they belong on the SAME row
    // — that is what makes the cut look like a cut rather than like a second
    // track appearing. So the loose tracks are grouped by their upload: one
    // file, one lane, however many pieces it has been cut into.
    const loose = audioTracks.filter((a) => !a.layer_id);
    const byFile = new Map();
    for (const track of loose) {
      if (!byFile.has(track.upload_id)) byFile.set(track.upload_id, []);
      byFile.get(track.upload_id).push(track);
    }
    for (const [uploadId, clips] of byFile) {
      const ordered = [...clips].sort((a, b) => (a.start_ms || 0) - (b.start_ms || 0));
      out.push({
        key: uploadId,
        kind: "audio",
        name: ordered[0].filename,
        layerId: "",
        tracks: ordered,
        removable: false,
      });
    }
    for (const l of of("audio")) {
      const clips = laneClips(audioTracks, l.id);
      // ⚠ NAMED FOR THE FILE ONLY WHILE IT HOLDS ONE. A row can hold clips from
      // several files now (a clip dragged down from another row — see
      // `moveClipToLane`), and calling that row by whichever file happens to
      // start earliest would rename it under you as you dragged. The LAYER's own
      // name is the honest label for a row with a mix on it.
      const files = new Set(clips.map((c) => c.upload_id));
      out.push({
        key: l.id,
        kind: "audio",
        name: files.size === 1 ? clips[0].filename : l.name,
        layerId: l.id,
        tracks: clips,
        removable: true,
      });
    }
    // With no audio at all, keep the empty band that has always been there —
    // it is the obvious place to click to add some.
    if (!byFile.size && !of("audio").length) {
      out.push({ key: "audio:", kind: "audio", name: "Audio", layerId: "", tracks: [], removable: false });
    }
    // One pass at the end, so no branch above has to remember to do it: every
    // lane carries its own visibility token and whether it is currently off.
    // ⚠ HOW MUCH IS ON A ROW IS NOT COMPUTED HERE. That would mean depending on
    // `texts`, and this list must not rebuild on every keystroke in a caption —
    // the timeline already holds the clips and counts them itself.
    return out.map((lane) => {
      const vis = laneToken(lane);
      return { ...lane, vis, hidden: !!vis && hiddenLanes.has(vis) };
    });
    // ⚠ `hasCaptionClips`, not `texts`, for the reason above — this list only
    // cares WHETHER any clip is on the captions lane. `frames` is in here for one
    // question too: WHICH TRACKS EXIST, which decides how many picture rows there
    // are. That does mean the list rebuilds when a picture is added or moved
    // across tracks, which is exactly when the rows change.
  }, [layers, audioTracks, hasCaptionClips, frames, hiddenLanes, extraPictureTracks]);

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
    (ms, id = null) => {
      if (!frames.length) return;
      // ⚠ THE CLIP THE RAZOR NAMED, and only if the blade is inside it. It used
      // to be "the last clip that had started by `ms`", which was exact while the
      // picture was one gapless sequence and is now wrong twice: the blade may be
      // in a GAP (nothing to cut) and the clip it lands on belongs to whichever
      // TRACK was clicked, not to whichever started most recently.
      const i = id
        ? frames.findIndex((f) => f.id === id)
        : frameIndexContaining(ms);
      if (i < 0) return;
      const span = pictureSpans.spans[i];
      if (!span || ms < span.start || ms >= span.end) {
        setNotice("The razor cuts a CLIP — there is nothing on that row at that moment.");
        return;
      }
      const source = frames[i];
      const offset = Math.round(ms - span.start);
      if (offset < MIN_MS || source.duration_ms - offset < MIN_MS) {
        setNotice(
          `Too close to a cut — each side of an edit needs at least ${MIN_MS}ms.`
        );
        return;
      }
      // ⚠ BOTH HALVES CARRY EXPLICIT STARTS. The tail begins where the blade
      // fell; the head keeps the clip's own start. A split used to be a list
      // splice and nothing else, because a clip's place WAS its position in the
      // list — with free placement the two halves have to be told where they are,
      // or the tail would land wherever `frameSpans` last left that track's clock.
      const tail = {
        ...source,
        id: newId(),
        start_ms: span.start + offset,
        duration_ms: source.duration_ms - offset,
      };
      setFrames((list) => {
        const next = [...list];
        next.splice(i, 1, { ...source, start_ms: span.start, duration_ms: offset }, tail);
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
      setNotice("Cut — that picture is now two clips you can time separately.");
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [frames, pictureSpans]
  );

  /**
   * Cut one caption, shape or overlay picture where the razor landed.
   *
   * The arithmetic — including planting a keyframe at the blade so the animation
   * does not jump at the edit — is all in `splitTimedClip`; this is the part
   * that has to know about React state and about what to say when a cut is
   * refused.
   */
  function splitTimedAt(kind, id, ms) {
    const pool = { text: texts, shape: shapes, overlay: overlays }[kind];
    const setPool = { text: setTexts, shape: setShapes, overlay: setOverlays }[kind];
    const clip = (pool || []).find((c) => c.id === id);
    if (!clip || !setPool) return false;
    const halves = splitTimedClip(clip, ms, newId());
    if (!halves) {
      setNotice(
        `Too close to the edge of that clip — each side of a cut needs at least ${MIN_SPLIT_MS}ms.`
      );
      return false;
    }
    // ⚠ IN PLACE, not appended. These lanes draw their clips in list order, so
    // pushing the tail onto the end would put the second half of a cut behind
    // every other clip on the row — invisible wherever two of them overlap.
    setPool((list) => list.flatMap((c) => (c.id === id ? halves : [c])));
    setNotice("Cut — that's two clips now, and each can be timed on its own.");
    return true;
  }

  /**
   * THE RAZOR, and there is one of it.
   *
   * ⚠ IT CUTS THE CLIP THE TIMELINE NAMED, AND NOTHING ELSE. It used to be
   * given a time and left to work out what that meant, which is how a press in
   * the time ruler ended up cutting the picture sequence (user-reported: "I
   * click in the seconds row and my image clip got cut"). Every lane now
   * identifies its own clip at the press, so the razor cuts the layer you are
   * looking at — and a press that hit no clip says so instead of guessing.
   */
  function razorAt(kind, id, ms) {
    if (!kind) {
      setNotice("The razor cuts a CLIP — click the bar itself, on the layer you want to cut.");
      return;
    }
    if (kind === "frame") {
      // ⚠ THE ID, not just the time. There can be a clip at `ms` on more than one
      // picture track, and the razor cuts the one you clicked.
      splitFrameAt(ms, id);
      return;
    }
    if (kind === "audio") {
      splitAudioAt(id, ms);
      return;
    }
    splitTimedAt(kind, id, ms);
  }

  // ⚠ THE SELECTION, MINUS ANYTHING THAT NO LONGER EXISTS. A clip can leave the
  // project while it is selected — an undo, a delete from a pane, a captions
  // run replacing its own row — and a list holding ids of things that are gone
  // would report "12 clips selected" over an empty timeline. Filtered where it
  // is READ rather than pruned on every write: there is one place to get that
  // right instead of one per path, and a stale id can then never survive
  // anywhere it would be acted on.
  const liveSelection = useMemo(
    () =>
      selection.filter((item) =>
        item.kind === "frame"
          ? frames.some((f) => f.id === item.id)
          : (groupPools[item.kind] || []).some((c) => c.id === item.id)
      ),
    [selection, frames, groupPools]
  );

  const idsOf = (items, kind) =>
    new Set(items.filter((i) => i.kind === kind).map((i) => i.id));

  // Is the pane describing a SET rather than a clip? One clip selected is still
  // that clip's own pane — a "1 clip selected" summary would be a pane that
  // tells you less than the one it replaced.
  const multiSelected = liveSelection.length > 1;

  /**
   * The EARLIEST start among the selected clips — how far left a group move is
   * allowed to travel.
   *
   * ⚠ THE CLAMP HAS TO BE ON THE DELTA, NOT ON EACH CLIP. Clamping every clip at
   * zero on its own looks like the same thing and is not: drag a group hard left
   * and the ones that hit the front of the video stop while the rest keep going,
   * so the spacing you were preserving is quietly squashed. One floor for the
   * whole selection means the group stops when its FIRST clip reaches 0:00 and
   * everything keeps its distance. The timeline is given this too, so the drag
   * you see and the write that follows it agree.
   */
  const selectionFloorMs = useMemo(() => {
    let floor = Infinity;
    const startOf = (list, id) => list.find((c) => c.id === id)?.start_ms;
    for (const item of liveSelection) {
      let start;
      if (item.kind === "text") start = startOf(texts, item.id);
      else if (item.kind === "shape") start = startOf(shapes, item.id);
      else if (item.kind === "overlay") start = startOf(overlays, item.id);
      else if (item.kind === "audio") {
        start = audioTracks.find((a) => clipId(a) === item.id)?.start_ms;
      } else continue; // a picture is not moved, so it sets no floor
      if (start !== undefined) floor = Math.min(floor, Math.max(0, start || 0));
    }
    return floor === Infinity ? 0 : floor;
  }, [liveSelection, texts, shapes, overlays, audioTracks]);

  /**
   * Delete every selected clip, in one pass and one undo step.
   *
   * ⚠ ONE `set…` PER LIST, not one per clip. Forty `deleteText(id)` calls would
   * be forty renders, forty document signatures and forty presses of Ctrl+Z to
   * get back — which is exactly the thing this whole feature exists to stop
   * being a forty-step job.
   */
  function deleteMany(items) {
    if (!items.length) return;
    const frameIds = idsOf(items, "frame");
    const textIds = idsOf(items, "text");
    const shapeIds = idsOf(items, "shape");
    const overlayIds = idsOf(items, "overlay");
    const audioIds = idsOf(items, "audio");
    if (frameIds.size) {
      // Computed here, not inside the updater: `setTransitions` needs the list
      // that is about to exist, and writing state from inside another state's
      // updater is a setState-during-render (see `deleteFrame`).
      const nextFrames = frames.filter((f) => !frameIds.has(f.id));
      setFrames(nextFrames);
      setTransitions((list) => pruneTransitions(list, nextFrames));
    }
    if (textIds.size) setTexts((list) => list.filter((c) => !textIds.has(c.id)));
    if (shapeIds.size) setShapes((list) => list.filter((s) => !shapeIds.has(s.id)));
    if (overlayIds.size) setOverlays((list) => list.filter((o) => !overlayIds.has(o.id)));
    if (audioIds.size) {
      setAudioTracks((list) => list.filter((a) => !audioIds.has(clipId(a))));
    }
    selectOnly({});
    setNotice(`Deleted ${selectionLabel(items)}.`);
  }

  /**
   * Slide every selected clip along the timeline by the same amount.
   *
   * The delta is the SNAPPED movement of the clip that was actually dragged, so
   * the spacing between the pieces is exactly what it was — a group move that
   * re-snapped each clip on its own would shuffle them about relative to each
   * other, which is the one thing it must never do. The front of the video is a
   * wall, and it is the WHOLE MOVE that stops there rather than each clip on its
   * own: see `selectionFloorMs`.
   *
   * ⚠ Pictures are not moved — see `MOVABLE` in `animatic/selection.js`. A frame
   * starts where the one before it ended; "later" is not a thing you can do to
   * one without re-timing the sequence.
   */
  function moveSelection(deltaMs) {
    const delta = Math.max(-selectionFloorMs, Math.round(deltaMs || 0));
    if (!delta) return;
    const items = liveSelection;
    const textIds = idsOf(items, "text");
    const shapeIds = idsOf(items, "shape");
    const overlayIds = idsOf(items, "overlay");
    const audioIds = idsOf(items, "audio");
    // No per-clip clamp: the delta above is already the most the whole selection
    // can travel, so `+ delta` cannot take anything below zero.
    const slide = (c) => ({ ...c, start_ms: Math.max(0, (c.start_ms || 0) + delta) });
    if (textIds.size) setTexts((list) => list.map((c) => (textIds.has(c.id) ? slide(c) : c)));
    if (shapeIds.size) setShapes((list) => list.map((s) => (shapeIds.has(s.id) ? slide(s) : s)));
    if (overlayIds.size) {
      setOverlays((list) => list.map((o) => (overlayIds.has(o.id) ? slide(o) : o)));
    }
    if (audioIds.size) {
      setAudioTracks((list) => list.map((a) => (audioIds.has(clipId(a)) ? slide(a) : a)));
    }
  }

  /**
   * A CLIP WAS DRAGGED ONTO ANOTHER ROW — the vertical half of a move drag.
   *
   * ⚠ THE TIMELINE HANDS OVER THE ROW, NOT A LAYER ID, and this is where "that
   * row" is turned into something a clip can carry. For a caption, a shape or an
   * overlay picture a row IS a `layer_id` and the write is one field. For AUDIO
   * it is not: a track saved before layers existed owns a row of its own, and
   * those rows are grouped by FILE (see `lanes`) precisely so that razoring one
   * take into six pieces looks like six cuts rather than six new tracks. A row
   * with no id is not a row a clip can be told to sit on — which is why "drop it
   * on the other audio row" used to be refused outright, reported as the main
   * complaint here: "I can't move some audio part to the other audio layer".
   *
   * So a file-grouped row is PROMOTED to a real layer the first time something is
   * dropped on it, taking its own clips with it. After that it is an ordinary
   * layer row that happens to have started life as one file, and it can hold as
   * many as you like.
   *
   * ⚠ ONE UNDO. The promotion is `setLayers` + `setAudioTracks` in the same event
   * handler, so React batches them into a single render and the stack records a
   * single signature change (see `useUndoStack`). Ctrl+Z puts the clip back AND
   * takes the layer away, which is the one thing the user did.
   */
  function moveClipToLane(kind, id, lane, patch = {}) {
    if (!lane) return;
    if (kind === "audio") {
      moveTrackToLane(id, lane, patch);
      return;
    }
    // A PICTURE's row is a numbered TRACK, not a layer id — see `frameTrack`.
    if (kind === "frame") {
      patchFrame(id, { ...patch, track: lane.track || 0 });
      selectOnly({ frame: id });
      setNotice(
        `Moved to ${lane.name} at ${formatTime(patch.start_ms || 0)}.${hiddenWarning(lane)}`
      );
      return;
    }
    const layer_id = laneId(lane.layerId || "");
    const write = { ...patch, layer_id };
    if (kind === "text") {
      patchText(id, write);
      selectOnly({ text: id });
    } else if (kind === "shape") {
      patchShape(id, write);
      selectOnly({ shape: id });
    } else if (kind === "overlay") {
      patchOverlay(id, write);
      selectOnly({ overlay: id });
    } else return;
    setNotice(`Moved to ${lane.name} at ${formatTime(patch.start_ms || 0)}.${hiddenWarning(lane)}`);
  }

  /**
   * ⚠ A ROW WITH ITS EYE OFF IS STILL A DESTINATION, and it says so.
   *
   * Refusing the drop would be the wrong answer — hiding a row is a VIEW state,
   * not a lock, and a gesture that silently does nothing is the worst of the
   * three outcomes. But landing a clip somewhere it stops drawing, without a
   * word, looks exactly like the clip being deleted. So it moves, and the notice
   * says why the monitor did not change.
   */
  const hiddenWarning = (lane) =>
    lane.hidden
      ? " That row is hidden, so it is left out of the monitor and the export until you turn its eye back on."
      : "";

  /** The audio half of `moveClipToLane` — see the promotion rule above. */
  function moveTrackToLane(id, lane, patch) {
    const track = audioTracks.find((a) => clipId(a) === id);
    if (!track) return;
    // A row that already IS a layer is a destination as it stands.
    if (lane.layerId) {
      patchTrack(id, { ...patch, layer_id: lane.layerId });
      selectOnly({ track: id });
      setNotice(
        `“${track.filename}” moved to ${lane.name} at ${formatTime(patch.start_ms || 0)}.` +
          hiddenWarning(lane)
      );
      return;
    }
    const sitting = lane.tracks || [];
    // The placeholder "Audio" band, which only exists on a project with NO
    // tracks at all — so there is nothing to have dragged onto it. Belt and
    // braces: promoting an empty row would leave a layer nothing lives on.
    if (!sitting.length) return;
    const layer = addLayer("audio", { name: lane.name, notice: false });
    const moving = new Set(sitting.map((t) => clipId(t)));
    setAudioTracks((list) =>
      list.map((a) => {
        const cid = clipId(a);
        // The dragged clip lands where it was dropped; the row's own clips only
        // change which row they belong to, never when they play.
        if (cid === id) return { ...a, ...patch, layer_id: layer.id };
        return moving.has(cid) ? { ...a, layer_id: layer.id } : a;
      })
    );
    selectOnly({ track: id });
    setNotice(
      `“${track.filename}” moved onto ${lane.name} at ${formatTime(patch.start_ms || 0)} — ` +
        "that row is a layer now, so it can hold both files." +
        hiddenWarning(lane)
    );
  }

  /**
   * Tie the selected clips together, or untie them.
   *
   * A group is a `group_id` shared by its members and nothing else — no
   * container, no list to keep in step (read the field's comment in
   * `server/schemas.py`). Grouping is therefore one patch per list, and
   * ungrouping is the same patch with "".
   *
   * Pictures cannot be grouped: they are a flow, not free-floating clips, so
   * there is nothing about them for a group to hold together.
   */
  function groupSelection(join = true) {
    const items = liveSelection.filter((i) => GROUPABLE.includes(i.kind));
    if (join && items.length < 2) {
      setNotice("Select at least two clips to group them — shift-click, or drag a box round them.");
      return;
    }
    if (!items.length) {
      setNotice("Nothing groupable is selected. Pictures can't be grouped — they're a sequence.");
      return;
    }
    const group = join ? `g${newId()}` : "";
    const textIds = idsOf(items, "text");
    const shapeIds = idsOf(items, "shape");
    const overlayIds = idsOf(items, "overlay");
    const audioIds = idsOf(items, "audio");
    const tag = (c) => ({ ...c, group_id: group });
    if (textIds.size) setTexts((list) => list.map((c) => (textIds.has(c.id) ? tag(c) : c)));
    if (shapeIds.size) setShapes((list) => list.map((s) => (shapeIds.has(s.id) ? tag(s) : s)));
    if (overlayIds.size) {
      setOverlays((list) => list.map((o) => (overlayIds.has(o.id) ? tag(o) : o)));
    }
    if (audioIds.size) {
      setAudioTracks((list) => list.map((a) => (audioIds.has(clipId(a)) ? tag(a) : a)));
    }
    setNotice(
      join
        ? `Grouped ${selectionLabel(items)} — clicking one now selects them all.`
        : `Ungrouped ${selectionLabel(items)}.`
    );
  }

  // Delete whatever is selected, in the same order the Properties pane picks
  // what to show — so Delete always removes the thing the pane is describing,
  // which is the only reading of "the selection" a person can act on.
  const deleteSelection = useCallback(() => {
    // Several things selected: they all go, in one step. The single-selection
    // path below is kept for the one-clip case because it does more than delete
    // — it lands the selection on the next frame so Delete-Delete-Delete works.
    if (liveSelection.length > 1) {
      deleteMany(liveSelection);
      return;
    }
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
      // ⚠ ONE CLIP, not the whole track. This is what takes a gap out: cut
      // either side of the pause and press Delete on the middle. The gutter's ✕
      // is still there for removing the track outright.
      removeTrack(clipId(selectedTrack));
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
    selectedTrack, selectedFrame, frames, liveSelection,
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
            // ⚠ Cuts what is SELECTED, and now that means every kind of clip
            // rather than only the two that had a razor. With something
            // selected, Ctrl+K is about that clip — anything else would make
            // the shortcut useless for the edit it was just asked for, since
            // the picture sequence is never what you were looking at when you
            // selected a waveform, a caption or a shape. The picture is the
            // fallback because it is the only layer that is always there.
            if (selectedTrack) razorAt("audio", selectedTrackId, timeRef.current);
            else if (selectedText) razorAt("text", selectedTextId, timeRef.current);
            else if (selectedShape) razorAt("shape", selectedShapeId, timeRef.current);
            else if (selectedOverlay) razorAt("overlay", selectedOverlayId, timeRef.current);
            else razorAt("frame", null, timeRef.current);
            return;
          case "KeyX":
            if (e.shiftKey) {
              e.preventDefault();
              setMarkIn(null);
              setMarkOut(null);
              setNotice("In and out marks cleared.");
            }
            return;
          case "KeyA":
            // Everything on the timeline. ⚠ Taken from the browser (which would
            // select the page's TEXT) because in an editor Ctrl+A can only
            // sensibly mean the clips — selecting the chrome is never useful.
            e.preventDefault();
            selectMany(everything());
            return;
          case "KeyG":
            e.preventDefault();
            // Shift+Ctrl+G unties, the pairing every program with grouping uses.
            groupSelection(!e.shiftKey);
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

  /**
   * SEVERAL pictures in one write — what every timeline edit on a picture track
   * comes through (`onFramesChange`).
   *
   * ⚠ ONE `setFrames`, ONE UNDO. A picture edit is rarely one clip now: a RIPPLE
   * trim moves everything after it on that track, a ROLLING trim gives the
   * neighbour what it takes, a group move carries the selection. Forty separate
   * `patchFrame` calls would be forty renders and forty presses of Ctrl+Z.
   */
  const patchFrames = useCallback((patches) => {
    const list = Array.isArray(patches) ? patches.filter((x) => x && x.id) : [];
    if (!list.length) return;
    const by = new Map(list.map((x) => [x.id, x]));
    setFrames((frames) =>
      frames.map((f) => (by.has(f.id) ? { ...f, ...by.get(f.id), id: f.id } : f))
    );
  }, []);

  /**
   * INSERT pictures into a track at `atIndex`, closing up behind them.
   *
   * ⚠ THE NEW CLIPS GET EXPLICIT STARTS AND EVERYTHING AFTER THEM RIPPLES. That
   * is what "insert" has always meant here — it was free while the picture was one
   * butt-jointed sequence, because a list splice WAS the edit. With clips placed
   * freely it has to be done on purpose: splicing alone would leave the newcomers
   * on top of whatever already occupied that stretch of track.
   *
   * `atIndex` is an index into `frames` (what `frameIndexAt` returns), so "at the
   * end" is `frames.length` and lands the clips after the last one on their track.
   */
  function insertPictures(list, added, atIndex, track) {
    const spans = frameSpans(list).spans;
    const on = spans.filter((s) => s.track === track).sort((a, b) => a.start - b.start);
    // Where the newcomers begin: the start of the clip they are going in front
    // of, or the end of the track when they are going on the end.
    const ahead = on.find((s) => s.index >= atIndex);
    const at = ahead ? ahead.start : on.length ? on[on.length - 1].end : 0;
    let clock = at;
    const placed = added.map((clip) => {
      const start = clock;
      clock += Math.max(100, Number(clip.duration_ms) || 2000);
      return { ...clip, track, start_ms: start };
    });
    const shift = clock - at;
    const next = list.map((f) => {
      const span = spans.find((s) => s.index === list.indexOf(f));
      if (!span || span.track !== track || span.start < at) return f;
      return { ...f, start_ms: span.start + shift };
    });
    next.splice(atIndex === undefined ? next.length : atIndex, 0, ...placed);
    return next;
  }

  /**
   * The Media pane's drag: put this picture at that place in ITS TRACK's order.
   *
   * ⚠ IT RE-LAYS THE TRACK END TO END, and that is a deliberate choice rather
   * than a limitation. The Media pane lists the pictures as a SEQUENCE — that is
   * what its numbers and its ‹ › mean — so a reorder there is a sequence
   * operation, and the only reading of "put this one third" that leaves a
   * predictable timeline is "close the row up in the new order". Dragging on the
   * TIMELINE is the gesture that moves one clip and touches nothing else.
   */
  function reorder(from, to) {
    setFrames((list) => {
      const track = frameTrack(list[from]);
      const next = [...list];
      const [moved] = next.splice(from, 1);
      next.splice(to > from ? to - 1 : to, 0, moved);
      // Re-lay this track from where it currently begins, in the new list order.
      const on = next.filter((f) => frameTrack(f) === track);
      const at = Math.min(
        ...frameSpans(list)
          .spans.filter((s) => s.track === track)
          .map((s) => s.start)
      );
      let clock = Number.isFinite(at) ? at : 0;
      const placed = new Map();
      for (const f of on) {
        placed.set(f.id, clock);
        clock += Math.max(100, Number(f.duration_ms) || 2000);
      }
      return next.map((f) =>
        placed.has(f.id) ? { ...f, start_ms: placed.get(f.id) } : f
      );
    });
    setNotice("Re-ordered — that picture row is closed up in the new order.");
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

  /**
   * One transition record.
   *
   * ⚠ ONE LITERAL, TWO CALLERS — the ＋ on a cut and a preset dropped out of
   * the library. A field added to a transition has exactly one place to be
   * added, so it cannot arrive on the ones made one way and not the other.
   */
  function newTransition(afterFrameId, kind = "dissolve", params = {}) {
    return {
      id: newId(),
      after_frame_id: afterFrameId,
      kind,
      // ⚠ WHAT THE PRESET SAYS, AND NOTHING MORE. `transitionParams` supplies
      // every default on the way out, so filling them in here would pin today's
      // defaults into a project that then ignores tomorrow's — the same reason
      // a new clip carries no `keyframes` until one is set. The ＋ button
      // passes none at all, which is the plain dissolve it has always made.
      params: { ...params },
      duration_ms: DEFAULT_TRANSITION_MS,
    };
  }

  function addTransition(afterFrameId) {
    // One per cut. Pressing ＋ on a cut that already has one selects it rather
    // than stacking a second, which would make the render depend on list order.
    const existing = transitions.find((t) => t.after_frame_id === afterFrameId);
    if (existing) {
      selectOnly({ transition: existing.id });
      return;
    }
    const transition = newTransition(afterFrameId);
    setTransitions((list) => [...list, transition]);
    selectOnly({ transition: transition.id });
    setNotice(
      "Dissolve added on that cut — it blends across the edit without making the video any longer."
    );
  }

  // ------------------------------------------------- the effects library
  // The Effects tab in the Media pane is a browser you take from, and this is
  // the one door everything it offers comes through — a tile DRAGGED onto a row
  // and a tile CLICKED in the list end up in the same two functions. Two ways in
  // that meant different things is a UI you cannot predict, and it is the thing
  // that goes wrong first when a drop path is bolted on beside a click path.

  /**
   * Open the chain of the clip that carries it.
   *
   * ⚠ SELECTING THE CLIP IS ONLY HALF THE ANSWER. With Effects folded shut the
   * Properties pane looks exactly as it did and the effect that just landed is
   * invisible — the same "I added it and nothing happened" that `openGroup`
   * exists for. This is also what the ƒx badge on a timeline clip calls.
   */
  function manageEffects(what, id) {
    selectOnly(what === "overlay" ? { overlay: id } : { frame: id });
    openGroup("look:effects");
  }

  function addEffectToClip(entry, what, clip) {
    const chain = clip.effects || [];
    if (chain.length >= MAX_EFFECTS) {
      setNotice(
        `That clip already carries ${MAX_EFFECTS} effects, which is the most one clip can hold.`
      );
      return;
    }
    // ⚠ THE SAME ID SCHEME THE PROPERTIES PANE USES, and it matters: the id is
    // what a keyframe track names (`fx:<id>:<param>`), so it has to be unique
    // within the clip and has to survive the chain being re-ordered.
    const effect = {
      id: `fx${Date.now().toString(36)}${chain.length}`,
      kind: entry.kind,
      // What the PRESET says, and nothing more — `effectParams` fills in every
      // default when it is read. No effect has presets yet; carrying them here
      // is what stops the first one that does needing this line changed.
      params: { ...entry.params },
    };
    (what === "overlay" ? patchOverlay : patchFrame)(clip.id, {
      effects: [...chain, effect],
    });
    manageEffects(what, clip.id);
    setNotice(`${entry.label} added — its controls are in Properties, under Effects.`);
  }

  /**
   * Put a transition on one cut. `cut` indexes the EDGES of the sequence, the
   * way `frameIndexAt` counts them: 0 is before the first picture and
   * `frames.length` is past the last, and neither of those is an edit point.
   */
  function addTransitionAtCut(entry, cut) {
    if (cut <= 0 || cut >= frames.length) {
      setNotice("A transition goes on a cut BETWEEN two shots — there isn't one there.");
      return;
    }
    const after = frames[cut - 1];
    const existing = transitions.find((t) => t.after_frame_id === after.id);
    if (existing) {
      // ⚠ REPLACE, DON'T STACK. One transition per cut is what keeps
      // `transitionAt` single-valued — two on a cut would make the render
      // depend on list order. Dropping a wipe on a cut that already dissolves
      // means "make it a wipe", which is the only reading that is neither a
      // no-op nor a project that shouldn't exist.
      //
      // ⚠ AND THE PARAMETERS ARE REPLACED WHOLESALE, not merged: a preset IS
      // its parameters, so dropping "Wipe up" on a cut that wipes right has to
      // leave a wipe travelling up with nothing of the old one behind it.
      patchTransition(existing.id, { kind: entry.kind, params: { ...entry.params } });
      selectOnly({ transition: existing.id });
      setNotice(`That cut is a ${entry.label.toLowerCase()} now.`);
      return;
    }
    const made = newTransition(after.id, entry.kind, entry.params);
    setTransitions((list) => [...list, made]);
    selectOnly({ transition: made.id });
    setNotice(
      `${entry.label} added on that cut — it blends across the edit without making the video any longer.`
    );
  }

  /**
   * Which picture is on screen at `ms` — the clip an effect dropped there grades.
   *
   * ⚠ ON ONE TRACK, AND THE ANSWER MAY BE "NONE". A track can have a gap in it
   * now, so an effect dropped into one has nothing to grade — which is a thing to
   * say, not a clip to guess at. Reading the whole project and taking the last
   * clip that had started (which is what this used to do) would grade whatever was
   * on some other row.
   */
  function frameIndexContaining(ms, track = null) {
    let best = -1;
    for (const span of pictureSpans.spans) {
      if (track !== null && span.track !== track) continue;
      if (ms < span.start || ms >= span.end) continue;
      // The later clip wins where two overlap — the same tie-break `stackAt`
      // takes, so the effect lands on the picture you can actually see.
      if (best < 0 || span.start >= pictureSpans.spans[best].start) best = span.index;
    }
    return best;
  }

  /**
   * A tile CLICKED rather than dragged. It lands where the playhead is, which
   * is the same rule the shape gallery beside it follows — and the only path
   * to the library that works without a mouse.
   */
  function addFxFromLibrary(entry) {
    if (entry.type === "transition") {
      addTransitionAtCut(entry, frameIndexAt(timeMs));
      return;
    }
    // ⚠ A CROSSFADE NEEDS A ROW AS WELL AS A MOMENT, which is the one thing the
    // playhead cannot supply on its own: several lanes can be sounding at once,
    // and crossfading "whichever one `clipAt` happened to find last" is a
    // lottery. Resolved by the SAME three lines as the razor's keyboard shortcut
    // — prefer the selected clip when the playhead is standing on it, otherwise
    // take whatever is there — because "cut what I'm looking at" and "crossfade
    // what I'm looking at" are the same question and must not have two answers.
    if (entry.type === "audioTransition") {
      const selected = audioTracks.find((a) => clipId(a) === selectedTrackId);
      const clip =
        (selected && clipAt([selected], timeMs) ? selected : null) ||
        clipAt(audioTracks, timeMs);
      if (!clip) {
        setNotice(
          "Park the playhead on an audio clip first — a crossfade shapes a sound, so it needs one under the playhead."
        );
        return;
      }
      addCrossfade(entry, laneSiblings(clip), timeMs);
      return;
    }
    // An overlay you have selected beats the picture underneath it: you picked
    // it, so it is what "this clip" means.
    if (selectedOverlay) {
      addEffectToClip(entry, "overlay", selectedOverlay);
      return;
    }
    if (currentIndex < 0) {
      setNotice("Add a picture first — an effect grades a shot, so it needs one to sit on.");
      return;
    }
    addEffectToClip(entry, "frame", frames[currentIndex]);
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
      // ⚠ NOT IN THE ORIGINAL'S GROUP. A copy is a new clip, and one that
      // joined the group silently would move and delete with clips the user
      // never pointed at — see `group_id` in `server/schemas.py`.
      const copy = {
        ...source,
        id: newId(),
        group_id: "",
        start_ms: source.start_ms + source.duration_ms,
      };
      setSelectedTextId(copy.id);
      return [...list, copy];
    });
  }

  // ---------------------------------------------------------------- layers
  // "+ Add layer" makes a BLANK lane and stops there. It used to add content —
  // an upload dialog for images, a caption, a shape — which is not what adding
  // a layer means: you add the row, then you put things on it.
  const LAYER_NAMES = { image: "Images", text: "Text", shape: "Shapes", audio: "Audio" };

  // `name` and `notice` are for ONE caller: promoting a file-grouped audio row
  // into a real layer so a clip can be dropped on it (`moveClipToLane`). That is
  // not "a layer was added" from where the user is standing — the row was already
  // on screen — so it keeps the row's name and says nothing of its own.
  function addLayer(kind, { name = "", notice = true } = {}) {
    const taken = layers.filter((l) => l.kind === kind).length;
    const layer = {
      id: newId(),
      kind,
      // Numbered from 2 because the default lane of that kind is already "Text",
      // "Shapes", … on screen — so the first ADDED one reads as the second row.
      name: name || `${LAYER_NAMES[kind] || "Layer"} ${taken + 2}`,
    };
    setLayers((list) => [...list, layer]);
    if (notice) {
      setNotice(
        `Empty ${LAYER_NAMES[kind]?.toLowerCase() || ""} layer added — use its ＋ to put something on it.`
      );
    }
    return layer;
  }

  /**
   * A new, EMPTY picture track above the ones that exist.
   *
   * ⚠ NOT AN `addLayer`, and the difference is what a picture track IS. The other
   * rows are records in `layers` with an id that clips point at; a picture track
   * is a NUMBER on the clip (`frameTrack`), and the rows are derived from which
   * numbers are in use (`pictureTracks`). So there is nothing to create — the row
   * appears when something is on it. What this does is remember that you asked for
   * one, so an empty row is drawn to drop onto.
   */
  function addPictureTrack() {
    const next = Math.max(...pictureTracks(frames)) + 1;
    setExtraPictureTracks((n) => Math.max(n, next));
    setNotice(
      `Picture track ${next + 1} added — drag a clip up onto it, or use its ＋.`
    );
  }

  /**
   * PUT THE FOOTAGE ON ITS OWN TRACK — one press, for the layout people had
   * before tracks existed.
   *
   * The picture rows used to be split by ORIGIN: "Images" and "Video" were one
   * sequence drawn twice. That is gone (it is what made trimming footage move the
   * stills), and everything opens on ONE track — which is right, because it
   * reproduces the existing edit exactly. But the SPLIT was useful, so it is
   * offered as an action instead of imposed as a model.
   *
   * ⚠ IT MOVES ONLY, NEVER RE-TIMES. Every clip keeps the moment it plays at, so
   * the film is frame-for-frame what it was before the press; all that changes is
   * which row each clip is drawn on. Any transition that spanned one of those
   * boundaries becomes INERT (a transition needs two clips that touch, on one
   * track — see `transitionWindow`), so it is counted and reported rather than
   * silently doing nothing.
   */
  function splitFootageOntoTrack() {
    const base = frames.filter((f) => frameTrack(f) === 0);
    const footage = base.filter((f) => frameOrigin(f) === "video");
    if (!footage.length || footage.length === base.length) {
      setNotice(
        "Nothing to split — the base track is all stills or all footage already."
      );
      return;
    }
    const to = Math.max(...pictureTracks(frames)) + 1;
    const spans = new Map(pictureSpans.spans.map((s) => [frames[s.index].id, s.start]));
    const moving = new Set(footage.map((f) => f.id));
    // Which transitions stop meaning anything: their cut is between a clip that
    // is moving and one that is not.
    const stranded = transitions.filter((t) => {
      const i = frames.findIndex((f) => f.id === t.after_frame_id);
      if (i < 0) return false;
      const own = pictureSpans.spans[i];
      const next = pictureSpans.spans.find(
        (s) => s.track === own.track && s.start === own.end
      );
      if (!next) return false;
      return moving.has(frames[i].id) !== moving.has(frames[next.index].id);
    });
    setFrames((list) =>
      list.map((f) =>
        moving.has(f.id) ? { ...f, track: to, start_ms: spans.get(f.id) ?? 0 } : f
      )
    );
    setExtraPictureTracks((n) => Math.max(n, to));
    setNotice(
      `${footage.length} video clip${footage.length === 1 ? "" : "s"} moved to Pictures ${to + 1} — ` +
        "every one still plays at the same moment." +
        (stranded.length
          ? ` ${stranded.length} transition${stranded.length === 1 ? "" : "s"} now sit${
              stranded.length === 1 ? "s" : ""
            } across a gap and will not play until you close it.`
          : "")
    );
  }

  // Removing a lane takes its contents with it: they have nowhere else to live,
  // and silently moving them to another row would be worse than saying so.
  function removeLayer(layerId) {
    const layer = layers.find((l) => l.id === layerId);
    // The captions lane is drawn from its CLIPS as well as from its record (see
    // `lanes`), so removing it has to work even when the record is the half
    // that went missing — otherwise the ✕ on a visible row does nothing.
    if (!layer && layerId === CAPTION_LAYER_ID) {
      setLayers((list) => list.filter((l) => l.id !== layerId));
      setTexts((list) => list.filter((c) => (c.layer_id || "") !== layerId));
      setNotice("Captions removed.");
      return;
    }
    if (!layer) return;
    setLayers((list) => list.filter((l) => l.id !== layerId));
    if (layer.kind === "text") setTexts((list) => list.filter((c) => c.layer_id !== layerId));
    if (layer.kind === "shape") setShapes((list) => list.filter((s) => s.layer_id !== layerId));
    if (layer.kind === "image") setOverlays((list) => list.filter((o) => o.layer_id !== layerId));
    if (layer.kind === "audio")
      setAudioTracks((list) => list.filter((a) => a.layer_id !== layerId));
    setNotice("Layer removed.");
  }

  /**
   * ✕ on a DEFAULT row: empty it, and leave the row.
   *
   * ⚠ NOT `removeLayer`, AND THE DIFFERENCE IS THE POINT. The default rows — Text,
   * Shapes, Images, Video — are not records that can be deleted; they are where
   * clips with no lane of their own live, so there is always one of each. Until
   * now that meant they had no ✕ at all, and emptying one was done clip by clip
   * or with a marquee that misses whatever is scrolled off the end of the row.
   * This deletes what is ON the row and keeps the row, which is the only thing
   * "remove" can honestly mean here.
   *
   * ⚠ ASKS FIRST, unlike every other ✕ in the gutter, because this is the one
   * that can be forty clips — a whole board's worth of pictures behind one click.
   * Undo covers it (it is an ordinary document edit), but a confirm is cheaper
   * than finding that out.
   */
  function clearLane(lane) {
    const on = (list) => list.filter((c) => (c.layer_id || "") === (lane.layerId || ""));
    const off = (list) => list.filter((c) => (c.layer_id || "") !== (lane.layerId || ""));
    let count = 0;
    if (lane.kind === "frames") {
      count = frames.filter((f) => frameTrack(f) === (lane.track || 0)).length;
    } else if (lane.kind === "text") count = on(texts).length;
    else if (lane.kind === "shape") count = on(shapes).length;
    else if (lane.kind === "image") count = on(overlays).length;
    if (!count) return;

    const what = `${count} clip${count === 1 ? "" : "s"} on ${lane.name}`;
    if (!window.confirm(`Delete ${what}? Ctrl+Z puts them back.`)) return;

    if (lane.kind === "frames") {
      // Just this track. ⚠ AND NOTHING ELSE MOVES: a picture holds its own start,
      // so emptying a track leaves the rows around it exactly where they were.
      // (It used to shorten the whole sequence, because there was only one.)
      setFrames((list) => list.filter((f) => frameTrack(f) !== (lane.track || 0)));
    } else if (lane.kind === "text") setTexts(off);
    else if (lane.kind === "shape") setShapes(off);
    else if (lane.kind === "image") setOverlays(off);
    setNotice(`Deleted ${what}.`);
  }

  // The ＋ on a lane. ONE entry point, so "add to this row" behaves the same
  // whether it is pressed in the gutter or on the empty band of the track.
  // Which lane it was pressed on decides what gets added, and where.
  const pendingOverlayLane = useRef("");
  // Which picture track a ＋ / drop is filling. Set at the gesture and read when
  // the files arrive, the same way `pendingAudioLane` works.
  const pendingPictureTrack = useRef(0);

  function addToLane(lane) {
    if (lane.kind === "frames") {
      // ⚠ WHICH TRACK the picked files land on, remembered for the change
      // handler. A picture track takes stills AND footage now, so there is one
      // picker rather than one per row — `addAssets` routes by file type, which
      // it always did.
      pendingPictureTrack.current = lane.track || 0;
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

  /**
   * SOMETHING WAS DROPPED ON A LANE — from the Media pane, or off the desktop.
   *
   * The timeline decides WHERE (which row, and the snapped time under the
   * pointer); this decides what that means, because only the editor knows what
   * an asset is. `atMs` is a TIME, and every branch below turns it into whatever
   * that row measures in:
   *
   *   · the picture rows are a SEQUENCE with no gaps, so a time is the nearest
   *     CUT — the clip is moved to that place in the order, not left floating at
   *     0:07 with a hole in front of it. That is what the picture track has
   *     always done; a gap on it is not a thing that can exist.
   *   · an audio row is free-floating, so a time is exactly the time: the clip's
   *     `start_ms`.
   *
   * ⚠ A PICTURE IS NO LONGER CONVERTED OR REFUSED BY WHICH ROW IT LANDS ON. The
   * picture rows were once one sequence filtered by ORIGIN, so a video dropped on
   * the Images row had to be refused — the row could not have held it. A row is a
   * real TRACK now and which one a clip is on is yours to choose, so both kinds
   * are welcome anywhere. The rule survives for the cases only the editor can
   * see: which file an audio clip belongs to, and what kind a dropped file is.
   */
  function frameIndexAt(ms, track = null) {
    // The nearest edit point ON THAT TRACK, including both ends: dropping past
    // the last picture is an append, dropping before the first is an insert at
    // the head. ⚠ ONE TRACK AT A TIME — with several of them the nearest cut in
    // the whole project is usually on a row you were not pointing at.
    const on =
      track === null
        ? pictureSpans.spans
        : pictureSpans.spans.filter((s) => s.track === track);
    if (!on.length) return frames.length;
    const ordered = [...on].sort((a, b) => a.start - b.start);
    const edges = [...ordered.map((s) => s.start), ordered[ordered.length - 1].end];
    let best = 0;
    let bestGap = Infinity;
    edges.forEach((t, i) => {
      const gap = Math.abs(t - ms);
      if (gap < bestGap) {
        bestGap = gap;
        best = i;
      }
    });
    // Back into an index in `frames`, which is what every caller wants: the clip
    // that edit point sits in FRONT of, or the end of the list for the last one.
    if (best >= ordered.length) {
      const last = ordered[ordered.length - 1];
      return last.index + 1;
    }
    return ordered[best].index;
  }


  /**
   * A PICTURE FROM THE SEQUENCE, COPIED ONTO AN IMAGE LAYER.
   *
   * Dropping a frame card on an image layer means "put this picture over the
   * video from here", so it is a COPY: the still stays where it is in the
   * sequence. Moving it would empty a cut out of the video to make an overlay,
   * which is never what dragging a picture onto a layer above meant.
   *
   * ⚠ A BOARD PANEL HAS NO UPLOAD OF ITS OWN — its picture belongs to the
   * storyboard (`src.storyboard_id`), while an overlay is only ever an
   * `upload_id` served from this animatic's media route. So its bytes, which
   * the editor is already holding as a blob for the thumbnail, are uploaded
   * into this animatic once. An uploaded still is reused as-is: same picture,
   * same upload, nothing sent twice.
   */
  async function overlayFromFrame(frame, lane, at) {
    if ((frame.kind || "image") === "color") {
      setNotice("A colour card has no picture to put on a layer.");
      return;
    }
    if (frameOrigin(frame) === "video") {
      setNotice("That is footage — an image layer holds a picture.");
      return;
    }
    let uploadId = frame.src?.upload_id || "";
    try {
      if (!uploadId) {
        const blobUrl = urls[frame.id];
        if (!blobUrl) {
          setNotice("That picture is still loading — try again in a moment.");
          return;
        }
        setUploading(true);
        const blob = await (await fetch(blobUrl)).blob();
        const type = blob.type || "image/png";
        const file = new File([blob], `${frame.label || "frame"}.${type.split("/")[1] || "png"}`, {
          type,
        });
        const res = await api.uploadAnimaticImages(animaticId, [file]);
        uploadId = res.items?.[0]?.upload_id || "";
        if (!uploadId) {
          setNotice("That picture could not be copied onto the layer.");
          return;
        }
      }
      const overlay = {
        id: newId(),
        layer_id: laneId(lane.layerId || ""),
        upload_id: uploadId,
        start_ms: at,
        // As long as the still is held in the sequence — the length you can
        // already see on its card, so the overlay arrives the size of the thing
        // you dragged rather than an arbitrary two seconds.
        duration_ms: frame.duration_ms || 2000,
        x: 0.5,
        y: 0.5,
        w: 0.3,
        h: 0.3,
        opacity: 1,
        rotation: 0,
        url: `/animatics/${animaticId}/media/${uploadId}`,
      };
      setOverlays((list) => [...list, overlay]);
      selectOnly({ overlay: overlay.id });
      setNotice(`Picture added to ${lane.name} at ${formatTime(at)} — drag it on the frame to place it.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  }

  async function dropAsset({ lane, atMs, asset, files }) {
    const at = Math.max(0, Math.round(atMs || 0));

    // ---- from the desktop -------------------------------------------------
    if (files?.length) {
      if (lane.kind === "frames") {
        // ⚠ `addAssets`, NOT a picker of its own: it is the one door every
        // upload goes through, and it routes by file type. A video dropped on
        // the Images row still becomes a video clip — it simply appears on the
        // Video row, which is the truth about what it is.
        const usable = files.filter((f) => kindOf(f) === "image" || kindOf(f) === "video");
        if (!usable.length) {
          setNotice("That is not a picture or a video — the picture rows take images and footage.");
          return;
        }
        await addAssets(usable, frameIndexAt(at));
        return;
      }
      if (lane.kind === "image") {
        await addOverlayFiles(files, lane.layerId || "", at);
        return;
      }
      const audio = files.filter((f) => kindOf(f) === "audio");
      if (!audio.length) {
        setNotice("The audio rows take sound files — a picture belongs on the picture track.");
        return;
      }
      if (audioFileCount() >= MAX_AUDIO_TRACKS) {
        setNotice(`That's the limit — an animatic can hold ${MAX_AUDIO_TRACKS} audio tracks.`);
        return;
      }
      pendingAudioLane.current = lane.layerId || "";
      await addAudioTrack(audio[0], at);
      setNotice(`“${audio[0].name}” added at ${formatTime(at)}.`);
      return;
    }

    if (!asset?.id) return;

    // ---- a picture already in the sequence --------------------------------
    if (asset.kind === "frame") {
      const from = frames.findIndex((f) => f.id === asset.id);
      if (from < 0) return;
      // Onto an image LAYER: a copy over the video, not a place in the
      // sequence. The two rows both say "image" and mean different things —
      // see `overlayFromFrame`.
      if (lane.kind === "image") {
        await overlayFromFrame(frames[from], lane, at);
        return;
      }
      // ⚠ NOTHING IS REFUSED FOR BEING THE WRONG KIND ANY MORE. The picture rows
      // were one sequence filtered by ORIGIN, so a video dragged onto the Images
      // row had to be turned away — the row could not have held it. A row is a
      // real TRACK now: dropping a clip on one MOVES IT THERE, kind and all.
      const track = lane.track || 0;
      if (frameTrack(frames[from]) !== track) {
        patchFrame(asset.id, { track, start_ms: Math.max(0, Math.round(at)) });
        selectOnly({ frame: asset.id });
        setNotice(`Moved to ${lane.name} at ${formatTime(at)}.`);
        return;
      }
      const to = frameIndexAt(at, track);
      // Dropping a clip either side of itself is where it already is.
      if (to === from || to === from + 1) return;
      reorder(from, to);
      // ⚠ ITS NEW PLACE, NOT THE TIME IT WAS DROPPED AT. `starts` is the
      // sequence BEFORE the move, so quoting a time off it would name where the
      // clip that used to be there began. A position is exact, and it is the
      // number already printed on the card in the Media pane.
      setNotice(`Moved to #${(to > from ? to - 1 : to) + 1} in the sequence.`);
      return;
    }

    // ---- an effect or a transition, out of the Effects library ------------
    // ⚠ THE PAYLOAD NAMES A PRESET AND IS LOOKED UP, NOT TRUSTED. It carries an
    // ENTRY id ("wipe:up"), never a kind — four wipes share one kind, so a kind
    // could not say which. And it carries no parameters: those are read out of
    // the library HERE, at drop time, so a tab left open since before a preset
    // was last edited still drops the current one. `fxEntry` returning null is
    // how an id this build doesn't have stops here rather than becoming an
    // effect both renderers silently skip.
    if (asset.kind === "fxAudioTransition") {
      const entry = fxEntry("audioTransition", asset.id);
      // ⚠ `laneTakes` HAS ALREADY REFUSED EVERY OTHER ROW, so unlike a video
      // transition there is nothing left to turn away here — the marker on the
      // drag said "audio only" and the picture rows never lit up.
      if (entry) addCrossfade(entry, lane.tracks || [], at);
      return;
    }

    if (asset.kind === "fxEffect" || asset.kind === "fxTransition") {
      const entry = fxEntry(
        asset.kind === "fxTransition" ? "transition" : "effect",
        asset.id
      );
      if (!entry) return;

      if (entry.type === "transition") {
        // The two picture rows are one sequence, so a cut is a cut on either —
        // but an image LAYER is a picture composited over the film and has no
        // edit points at all. `laneTakes` cannot tell the two payloads apart
        // mid-drag, so this is where a transition on an overlay row is refused.
        if (lane.kind !== "frames") {
          setNotice(
            "A transition goes on a cut in the picture sequence — an image layer has no cuts."
          );
          return;
        }
        addTransitionAtCut(entry, frameIndexAt(at));
        return;
      }

      if (lane.kind === "image") {
        const onto = overlays.find(
          (o) =>
            (o.layer_id || "") === (lane.layerId || "") &&
            at >= (o.start_ms || 0) &&
            at < (o.start_ms || 0) + (o.duration_ms || 0)
        );
        if (!onto) {
          setNotice("Drop it ON a picture — an effect grades a clip, not an empty stretch of row.");
          return;
        }
        addEffectToClip(entry, "overlay", onto);
        return;
      }

      // ⚠ ON THIS TRACK. A track can have a gap in it, and a drop into one must
      // not quietly grade whatever is on the row underneath — same rule as "an
      // effect lands on a CLIP, not at a moment".
      const i = frameIndexContaining(at, lane.track || 0);
      if (i < 0) {
        setNotice("There is no picture on that row at that moment — drop it on the bar itself.");
        return;
      }
      // (`frameIndexContaining` was already asked for THIS TRACK, so a drop into
      // a gap has come back as -1 above and been refused with a reason.)
      addEffectToClip(entry, "frame", frames[i]);
      return;
    }

    // ---- a shape, out of the picker or off the timeline -------------------
    if (asset.kind === "shape") {
      addShape(asset.id, lane.layerId || "", at);
      return;
    }
    if (asset.kind === "shapeClip") {
      const shape = shapes.find((x) => x.id === asset.id);
      if (!shape) return;
      patchShape(asset.id, { start_ms: at, layer_id: laneId(lane.layerId || "") });
      selectOnly({ shape: asset.id });
      setNotice(`Shape moved to ${formatTime(at)}.`);
      return;
    }

    // ---- an audio clip ----------------------------------------------------
    if (asset.kind === "audio") {
      const track = audioTracks.find((a) => clipId(a) === asset.id);
      if (!track) return;
      // ⚠ A LOOSE TRACK KEEPS ITS OWN ROW. Those rows are grouped by FILE (see
      // `lanes`), not chosen — so "drop it on that other file's row" is a
      // promise the timeline cannot keep, and moving it in time while it jumped
      // back to its own row would look like a bug. A layer row is a real
      // destination, so that one moves it.
      const ownRow = lane.layerId ? lane.layerId === (track.layer_id || "") : lane.key === track.upload_id;
      if (!lane.layerId && !ownRow) {
        setNotice(`“${track.filename}” has its own row — drop it there to move it in time, or on a layer row to move it across.`);
        return;
      }
      patchTrack(asset.id, {
        start_ms: at,
        ...(lane.layerId && lane.layerId !== (track.layer_id || "") ? { layer_id: lane.layerId } : {}),
      });
      selectOnly({ track: asset.id });
      setNotice(`“${track.filename}” moved to ${formatTime(at)}.`);
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
      // ⚠ NOT IN THE ORIGINAL'S GROUP. A copy is a new clip, and one that
      // joined the group silently would move and delete with clips the user
      // never pointed at — see `group_id` in `server/schemas.py`.
      const copy = {
        ...source,
        id: newId(),
        group_id: "",
        start_ms: source.start_ms + source.duration_ms,
      };
      setSelectedOverlayId(copy.id);
      return [...list, copy];
    });
  }

  // Upload pictures INTO an image layer. They land at the playhead, a third of
  // the frame wide, and are dragged from there — unlike a frame, an overlay has
  // no place in the sequence to be added to.
  async function addOverlayFiles(files, layerId, startMs) {
    const images = [...files].filter((f) => kindOf(f) === "image");
    if (!images.length) {
      setNotice("An image layer takes pictures — that file isn't one.");
      return;
    }
    setUploading(true);
    setError("");
    try {
      const res = await api.uploadAnimaticImages(animaticId, images);
      // The playhead for the ＋ and the picker; the drop point for a file
      // dragged straight onto the row (`dropAsset`).
      const start = Math.round(startMs === undefined ? timeRef.current : startMs);
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
  function addShape(kind, layerId = "", startMs) {
    // The shot it belongs to: the one at the playhead for the picker, the one
    // UNDER THE DROP for a shape dragged onto a row. Its length is that shot's,
    // which is what makes a shape arrive covering the picture it was aimed at
    // rather than an arbitrary two seconds of whatever is there.
    const at = startMs === undefined ? null : Math.max(0, Math.round(startMs));
    let i = currentIndex >= 0 ? currentIndex : 0;
    if (at !== null && frames.length) {
      const under = starts.findIndex(
        (s, k) => at >= s && at < s + (frames[k].duration_ms || 0)
      );
      if (under >= 0) i = under;
    }
    const start = at === null ? (frames.length ? starts[i] : 0) : at;
    const length = frames.length ? frames[i].duration_ms : 2000;
    const shape = { ...newShape(kind, start, length), layer_id: laneId(layerId) };
    setShapes((list) => [...list, shape]);
    selectOnly({ shape: shape.id });
    seek(start);
    // Same reasoning as `addAssets`: the list it joined may be folded shut.
    openGroup("media:shapes");
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
      // ⚠ NOT IN THE ORIGINAL'S GROUP. A copy is a new clip, and one that
      // joined the group silently would move and delete with clips the user
      // never pointed at — see `group_id` in `server/schemas.py`.
      const copy = {
        ...source,
        id: newId(),
        group_id: "",
        start_ms: source.start_ms + source.duration_ms,
      };
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
      // ⚠ ONTO A TRACK, AT A TIME. `insertPictures` gives the newcomers explicit
      // starts and ripples what follows them on that row — a list splice alone
      // would drop them on top of whatever already occupied that stretch.
      setFrames((list) =>
        insertPictures(list, added, insertAt, pendingPictureTrack.current || 0)
      );
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
          (item.filename || "").replace(/\.[^.]+$/, ""),
          animaticId
        )
      );
      // ⚠ ONTO A TRACK, AT A TIME. `insertPictures` gives the newcomers explicit
      // starts and ripples what follows them on that row — a list splice alone
      // would drop them on top of whatever already occupied that stretch.
      setFrames((list) =>
        insertPictures(list, added, insertAt, pendingPictureTrack.current || 0)
      );
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
    setMediaTab("media");
    // A card is a still you made, so it lists with the images — see `frameOrigin`.
    openGroup("media:images");
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
    const room = MAX_AUDIO_TRACKS - audioFileCount();
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

    // ⚠ SHOW WHAT WAS JUST ADDED. The Media pane's lists are sections you can
    // fold shut, and this control sits outside them — so with a section closed an
    // upload moved a count and changed nothing else on screen, reported as "I
    // uploaded a video but it's not in the media panel". Open the section the
    // files landed in, and switch to the tab that has it: an add you cannot see
    // is indistinguishable from an add that failed.
    //
    // ⚠ THE SECTION THE KIND LANDS IN, not "the frames one" — images and video
    // are two sections now (`pictureTrack`), and opening the wrong one would be
    // the same bug wearing a fix.
    if (images.length || addedVideos) setMediaTab("media");
    if (images.length) openGroup("media:images");
    if (addedVideos) openGroup("media:video");
    // (`addAudioTrack` opens the Audio section itself, for every way in.)
    if (taking.length) setMediaTab("media");
  }

  // How many audio FILES the project is carrying. ⚠ Not how many clips: the
  // razor makes clips out of a file it already has, so counting clips against
  // the cap would make a track uncuttable after three cuts. Mirrors
  // `_audio_files_of` on the server, which enforces the same rule.
  const audioFileCount = () => new Set(audioTracks.map((a) => a.upload_id)).size;

  // Adds a NEW track — it never replaces an existing one. The cap is checked by
  // the caller so a multi-file drop can report what it had to leave out.
  // `startMs` is where on the timeline it lands: 0 for every picker and card,
  // and the drop time for a file dragged straight onto an audio lane.
  async function addAudioTrack(file, startMs = 0) {
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
        // was there — every clip of it — which is what "add audio to this row"
        // has to mean.
        ...list.filter((a) => !layerId || a.layer_id !== layerId),
        {
          // A fresh clip identity. It happens to equal the upload for a track
          // nobody has cut yet, which is exactly what the server's backfill
          // gives an animatic saved before the razor existed.
          id: res.upload_id,
          upload_id: res.upload_id,
          layer_id: layerId,
          filename: res.filename || file.name,
          duration_ms: durationMs,
          start_ms: Math.max(0, Math.round(startMs || 0)),
          offset_ms: 0,
          volume: 1,
          muted: false,
          url: `/animatics/${animaticId}/media/${res.upload_id}`,
        },
      ]);
      // Here rather than in the callers: this is the one function every audio
      // add goes through — the pane's drop card, the Audio lane's ＋ and
      // "Add layer" — so the section that now holds it opens for all three.
      openGroup("media:audio");
    } catch (e) {
      setError(e.message);
    }
  }

  // The Audio layer's ＋ and the "Add layer" control both land here.
  async function pickAudio(file) {
    if (!file) return;
    if (audioFileCount() >= MAX_AUDIO_TRACKS) {
      setNotice(`That's the limit — an animatic can hold ${MAX_AUDIO_TRACKS} audio tracks.`);
      return;
    }
    await addAudioTrack(file);
    setNotice(`Audio track added — “${file.name}”.`);
  }

  // ⚠ Every one of these takes CLIP ids, never an upload id. Since the razor can
  // cut one file into several clips, an upload names a sound rather than a thing
  // on the timeline — patching by upload would change every piece of a track
  // when you meant to change one.
  const patchTrack = (id, patch) =>
    setAudioTracks((list) => list.map((a) => (clipId(a) === id ? { ...a, ...patch } : a)));

  /**
   * Every clip drawn on the SAME ROW as this one — the neighbours a crossfade
   * can reach.
   *
   * ⚠ GROUPED THE WAY `lanes` GROUPS THEM, and it has to be: "the clip next to
   * this one" can only mean the one you can SEE next to it. A loose clip's row
   * is its FILE (that is what makes a razored track look cut rather than
   * doubled), and a clip on a layer shares that layer's row with everything on
   * it — so the two cases are grouped by different keys, exactly as they are
   * where the rows are built.
   */
  function laneSiblings(track) {
    if (!track) return [];
    if (track.layer_id) return laneClips(audioTracks, track.layer_id);
    return audioTracks
      .filter((a) => !a.layer_id && a.upload_id === track.upload_id)
      .sort((a, b) => (a.start_ms || 0) - (b.start_ms || 0));
  }

  /**
   * Lay a crossfade from the library onto the audio at `at`.
   *
   * ⚠ ONE `setAudioTracks`, WRITING BOTH CLIPS, and that is the whole reason
   * this exists rather than two `patchTrack` calls: the undo stack takes one
   * entry per state update, so two calls would mean Ctrl+Z undid half a
   * crossfade and left the other half's fade behind. A crossfade is one thing
   * you did.
   *
   * The three outcomes, and none of them is an error:
   *   • ON A CUT — the crossfade, eating handles (`crossfadePatch`).
   *   • ON A FREE END — a plain fade of that curve. Which is what a crossfade
   *     dragged to the head of the first clip or the tail of the last one means
   *     in Premiere too, and is genuinely useful rather than a mistake to
   *     report: the three curves are three fade shapes before they are anything
   *     to do with cuts.
   *   • ACROSS A GAP — the same plain fade. Reaching over silence for the clip
   *     on the far side would be a crossfade you cannot hear.
   */
  function addCrossfade(entry, clips, at) {
    const target = crossfadeTarget(clips, at);
    if (!target) {
      setNotice("Drop it ON a sound — a crossfade shapes a clip, not an empty stretch of row.");
      return;
    }
    const { clip, side, neighbour } = target;
    const curve = entry.kind;
    const cut = neighbour
      ? crossfadePatch(
          ...(side === "in" ? [neighbour, clip] : [clip, neighbour]),
          DEFAULT_CROSSFADE_MS,
          curve
        )
      : { ok: false, reason: "alone" };

    const patches = cut.ok
      ? cut.patches
      : { [clipId(clip)]: fadeEndPatch(clip, side, DEFAULT_CROSSFADE_MS, curve) };
    setAudioTracks((list) =>
      list.map((a) => (patches[clipId(a)] ? { ...a, ...patches[clipId(a)] } : a))
    );
    selectOnly({ track: clipId(clip) });

    const where = side === "in" ? "start" : "end";
    if (!cut.ok) {
      setNotice(
        `${entry.label} on the ${where} of “${clip.filename}” — ` +
          (cut.reason === "gap"
            ? "there is silence on the other side of it, so it fades rather than crosses."
            : "nothing is butted against that end, so it fades rather than crosses.")
      );
      return;
    }
    const secs = (cut.appliedMs / 1000).toFixed(1);
    setNotice(
      cut.overlapped
        ? `${entry.label} across that cut — ${secs}s, with both sounds playing through it.`
        : `${entry.label} across that cut — ${secs}s each side. Neither clip has spare audio ` +
          `to overlap with, so it dips through the cut instead of crossing.`
    );
  }

  // Takes a LIST, because the gutter's speaker speaks for the whole lane: after
  // a cut, "mute this track" has to mean all of its pieces.
  const muteTracks = (ids, muted) =>
    setAudioTracks((list) =>
      list.map((a) => (ids.includes(clipId(a)) ? { ...a, muted } : a))
    );

  function removeTrack(ids) {
    const list = Array.isArray(ids) ? ids : [ids];
    setAudioTracks((tracks) => tracks.filter((a) => !list.includes(clipId(a))));
    if (list.includes(selectedTrackId)) setSelectedTrackId(null);
    setNotice(list.length > 1 ? "Audio track removed." : "Audio clip removed.");
  }

  /**
   * THE RAZOR ON AUDIO — the thing this whole `start_ms` business exists for.
   *
   * Cuts one clip at `ms` into two that add up to it, so the piece between two
   * cuts can be deleted (leaving the gap you wanted out) or dragged somewhere
   * else. `id` is the clip the razor was clicked on; with none — the keyboard
   * shortcut — it cuts whichever clip the playhead is standing on, preferring
   * the selected one so Ctrl+K is predictable when several lanes overlap.
   *
   * The arithmetic is all in `splitClip`; this is the part that has to know
   * about React state and about what to say when the cut is refused.
   */
  const splitAudioAt = useCallback(
    (id, ms) => {
      const at = Math.round(ms);
      let clip = id ? audioTracks.find((a) => clipId(a) === id) : null;
      if (!clip) {
        // Prefer the selected clip, then anything else under the playhead —
        // "cut what I'm looking at" is the only reading that isn't a lottery.
        const selected = audioTracks.find((a) => clipId(a) === selectedTrackId);
        clip =
          (selected && clipAt([selected], at) ? selected : null) || clipAt(audioTracks, at);
      }
      if (!clip) {
        setNotice("The razor found no audio clip there — click on the waveform itself.");
        return false;
      }
      const halves = splitClip(clip, at, newId());
      if (!halves) {
        setNotice(
          `Too close to the edge of that clip — each side of a cut needs at least ${MIN_CLIP_MS}ms.`
        );
        return false;
      }
      const [head, tail] = halves;
      setAudioTracks((list) =>
        list.flatMap((a) => (clipId(a) === clipId(clip) ? [head, tail] : [a]))
      );
      setNotice(
        "Cut — that's two audio clips now. Delete one to take the gap out, or drag it somewhere else."
      );
      return true;
    },
    [audioTracks, selectedTrackId]
  );

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
              // ⚠ THE ORIGIN SURVIVES THE ANIMATION. `src` used to be replaced
              // outright, which threw away `storyboard_id`/`index` — and those
              // are the only record that this clip is a board shot rather than a
              // file someone dropped in. The Media pane groups by exactly that
              // (`frameOrigin`), so an animated Shot 1 would have jumped out of
              // Storyboard Frames and into Video. Harmless to keep: every server
              // path branches on `src.kind` first, so the extra ids are inert.
              src: { ...(f.src || {}), kind: "video", upload_id: clip.upload_id },
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
    // ⚠ EVERY PICTURE GETS AN EXPLICIT `start_ms` THE FIRST TIME IT IS OPENED,
    // and this is the one place that happens.
    //
    // `null` means "after the last clip on my track" (`frameSpans`), which is what
    // makes every animatic written before tracks lay out exactly as it did — the
    // old running total, reproduced. But it is a RELATIVE answer: it depends on
    // list order, so as soon as one clip is given a start of its own the nulls
    // around it can be pushed on top of it. Filling them in once, from the
    // placement the project already had, turns the document into what it always
    // meant and takes that whole class of surprise off the table.
    //
    // It is a real edit and the autosave will persist it, which is intended: it is
    // exactly the same timeline, written down. Done BEFORE `resetHistory` below,
    // so it is not something the first Ctrl+Z can undo into a mixed state.
    const loaded = p.frames || [];
    if (loaded.some((f) => f.start_ms === null || f.start_ms === undefined)) {
      const spans = frameSpans(loaded).spans;
      const placed = loaded.map((f, i) => ({
        ...f,
        track: frameTrack(f),
        start_ms: spans[i].start,
      }));
      setFrames(placed);
      framesRef.current = placed;
      p = { ...p, frames: placed };
    }
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
        // ⚠ THE LAYERS TOO, and this is not optional. A captions run writes a
        // LANE as well as clips (`captions.CAPTION_LAYER_ID`), and taking the
        // clips without the lane they sit on leaves the editor holding captions
        // whose row it doesn't know about — the next autosave would then write
        // that missing row back and delete it from the project.
        setLayers(project.layers || []);
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
  }, [speechRunning, animaticId, setTexts, setLayers, setAudioTracks]);

  // -------------------------------------------- Phase 7: back to the board
  //
  // ⚠ A FRAME IS A REFERENCE TO A PANEL, NOT A COPY OF ONE. Everything in this
  // section leans on that: redrawing the panel or re-blocking its poses changes
  // what this animatic SHOWS without touching this animatic's document at all.
  // Which is exactly why it is easy to get wrong — nothing in the project
  // changes, so nothing re-renders, so it looks like nothing happened.

  // The panel behind one clip has been re-drawn. The server answers with the
  // FRAME, its url carrying a new `?v=<mtime>`, and this is the whole reason it
  // does: writing that url onto the clip is what makes the fetch effect notice.
  //
  // ⚠ The URL IS NOT PART OF THE SAVED DOCUMENT — `frameForSave` drops it, and
  // the server fills it in fresh on every read — so this is not an edit and
  // must not go on the undo stack. It is a cache key, and treating it as
  // content would put "the picture was redrawn" in the middle of the user's
  // undo history where it can never be undone anyway.
  const onPanelRedrawn = useCallback(
    (frame) => {
      if (!frame?.id) return;
      setFrames((list) =>
        list.map((f) => (f.id === frame.id ? { ...f, url: frame.url } : f))
      );
      setNotice("Re-drawn — the new picture is on the timeline.");
    },
    [setFrames]
  );

  // "Make this shot 2s longer." Runs on the BOARD's job; when it lands, the run
  // of pose clips for this shot has to be rebuilt on the timeline.
  async function relengthShot(frameId, seconds) {
    if (reblockJob) return;
    setError("");
    try {
      // The server reads the SAVED project to find which panel this clip points
      // at, so the clip has to be on the server first.
      await flush();
      const res = await api.relengthFrameSequence(animaticId, frameId, seconds);
      setReblockFor(frameId);
      setReblockJob(res.job_id);
      setReblockProgress({ percent: 0, message: "Planning the rest of the shot…" });
      setNotice(res.message);
    } catch (e) {
      setError(e.message);
    }
  }

  // ⚠ POLLS THE BOARD, keyed on `reblockJob` ALONE — the same rule as the Veo
  // and captions polls, for the same reason: an effect that restarts on what
  // its own poll writes cancels itself mid-flight, and by then the drawings
  // have been paid for.
  useEffect(() => {
    if (!reblockJob || !reblockFor) return undefined;
    let alive = true;
    let timer;
    async function poll() {
      try {
        const job = await api.getJob(reblockJob);
        if (!alive) return;
        if (job.status === "running") {
          setReblockProgress(job.progress || null);
          timer = setTimeout(poll, 1500);
          return;
        }
        // Which poses exist NOW, counted off disk by the server.
        const seq = await api.getFrameSequence(animaticId, reblockFor);
        if (!alive) return;
        setReblockJob(null);
        setReblockFor(null);
        setReblockProgress(null);
        if (job.error) {
          setError(job.error);
          return;
        }
        const added = rebuildPoseRun(reblockFor, seq);
        setNotice(
          added > 0
            ? `That shot is ${added} drawing${added === 1 ? "" : "s"} longer.`
            : "That shot already had every drawing it needs."
        );
      } catch (e) {
        if (!alive) return;
        setReblockJob(null);
        setReblockFor(null);
        setReblockProgress(null);
        setError(e.message);
      }
    }
    timer = setTimeout(poll, 1500);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reblockJob]);

  /**
   * Put the new key poses on the timeline, next to the ones already there.
   *
   * ⚠ THE EXISTING POSE CLIPS ARE LEFT ALONE — not rebuilt, REUSED. They are
   * the user's clips: one may have been cut, retimed, given a push, dragged
   * somewhere else entirely. Replacing the run wholesale would be correct about
   * the pictures and would throw away every edit made to them, which is the
   * more expensive of the two. So this only ADDS the poses that have no clip
   * yet, immediately after the last clip of that shot.
   *
   * Returns how many were added, so the caller can say something true.
   */
  function rebuildPoseRun(frameId, seq) {
    const numbers = seq?.frame_numbers || [];
    if (!numbers.length) return 0;
    // ⚠ COMPUTED OUT HERE, NOT INSIDE A `setFrames` UPDATER. An updater runs
    // when React commits, not when it is handed over, so a count assigned
    // inside one is still zero by the time the caller reads it — the message
    // would have said "0 drawings longer" on every successful run. `framesRef`
    // is the current list, kept in step by the effect above.
    const list = framesRef.current;
    const anchor = list.find((f) => f.id === frameId);
    const index = anchor?.src?.index;
    if (index == null) return 0;
    const boardId = anchor.src.storyboard_id;

    // Every clip of THIS shot, wherever it sits, and which pose each shows.
    const mine = list.filter(
      (f) =>
        f.src?.kind === "pose" &&
        f.src.storyboard_id === boardId &&
        f.src.index === index
    );
    const have = new Set(mine.map((f) => f.src.frame));
    const fresh = numbers.filter((n) => !have.has(n));
    if (!fresh.length) return 0;

    // A pose holds for a quarter of a second at the rate the sequence was
    // planned at — the same arithmetic `_frames_from_board` uses on the server.
    // Copying the hold of a clip already there would be wrong the moment the
    // user has retimed one.
    const hold = Math.max(MIN_MS, Math.round(1000 / 4));
    const last = mine.length ? list.lastIndexOf(mine[mine.length - 1]) : -1;
    const at = last >= 0 ? last + 1 : list.length;
    const built = fresh.map((n) => ({
      id: newId(),
      kind: "image",
      src: { kind: "pose", storyboard_id: boardId, index, frame: n },
      duration_ms: hold,
      label: `Shot ${index + 1}.${n + 1}`,
    }));
    setFrames((was) => {
      const next = [...was];
      next.splice(Math.min(at, next.length), 0, ...built);
      return next;
    });
    return built.length;
  }

  // ------------------------------------------------------ auto-reframe
  // ⚠ WHAT COMES BACK IS `scale` / `x` / `y` ON THE FRAMES — ordinary
  // keyframable properties the exporter already resolves, written server-side.
  // There is no crop concept anywhere in this app and this is not the place to
  // add one: a second way of saying where a picture sits is a second thing for
  // the preview and the export to disagree about.
  function openReframe() {
    setReframeOpen(true);
    setReframeAspect(settings.aspect_ratio);
    setReframeScope(liveSelection.some((s) => s.kind === "frame") ? "selection" : "all");
    setReframeConfirm(null);
    setReframeError("");
  }

  const reframeIds = () =>
    reframeScope === "selection"
      ? liveSelection.filter((s) => s.kind === "frame").map((s) => s.id)
      : [];

  // Ask what it would cost. FREE — this is the call that fills the dialog.
  async function askToReframe() {
    setReframeError("");
    setReframeBusy(true);
    try {
      // The server looks at the pictures of the SAVED project.
      await flush();
      const estimate = await api.estimateReframe(animaticId, {
        frameIds: reframeIds(),
        aspectRatio: reframeAspect,
      });
      if (!estimate.frames) {
        setReframeError(
          "None of these clips is a still with a picture behind it, so there " +
            "is nothing to re-frame. Video clips are framed by the footage, and " +
            "a colour card has no picture."
        );
        return;
      }
      setReframeConfirm({ estimate });
    } catch (e) {
      setReframeError(e.message);
    } finally {
      setReframeBusy(false);
    }
  }

  // The only place this spends. Reached solely from the confirm dialog.
  async function doReframe() {
    if (!reframeConfirm) return;
    setReframeBusy(true);
    setReframeConfirm(null);
    try {
      await api.reframeAnimatic(animaticId, {
        frameIds: reframeIds(),
        aspectRatio: reframeAspect,
      });
      setReframeOpen(false);
      setReframeRunning(true);
      setReframeProgress({ percent: 0, message: "Starting…" });
      setNotice(`Framing each shot for ${reframeAspect}…`);
    } catch (e) {
      setError(e.message);
    } finally {
      setReframeBusy(false);
    }
  }

  // Keyed on `reframeRunning` alone — see the note above the captions poll.
  useEffect(() => {
    if (!reframeRunning) return undefined;
    let alive = true;
    let timer;
    async function poll() {
      try {
        const job = await api.getJob(animaticId);
        if (!alive) return;
        if (job.status === "running") {
          setReframeProgress(job.progress || null);
          timer = setTimeout(poll, 1500);
          return;
        }
        // The server wrote the frames, so re-read them rather than trying to
        // reconstruct the result locally.
        const project = await api.getAnimatic(animaticId);
        if (!alive) return;
        setFrames(project.frames || []);
        setReframeRunning(false);
        setReframeProgress(null);
        if (job.error) setError(job.error);
        else setNotice("Re-framed. Every shot is an ordinary pan you can still edit.");
      } catch (e) {
        if (!alive) return;
        setReframeRunning(false);
        setReframeProgress(null);
        setError(e.message);
      }
    }
    timer = setTimeout(poll, 1200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [reframeRunning, animaticId, setFrames]);

  // ------------------------------------------------------------ cut to beat
  /**
   * PULL EVERY CUT ONTO THE NEAREST BEAT.
   *
   * The arithmetic is `animatic/beat_cut.js` — pure, and checked under node,
   * because "a cut is not a thing you can move" is three rules deep and none of
   * them are testable from inside a click handler. Read that file's header
   * before changing any of this. What is left here is the three things only the
   * editor knows: whether there is anything to cut, whether there is anything
   * to cut TO, and what to say when there isn't.
   */
  const cutToBeat = useCallback(() => {
    if (frames.length < 2) {
      setNotice("There is nothing to cut — a sequence needs at least two clips.");
      return;
    }
    const marks = beatMarks(audioTracks, audioAnalyses);
    if (!marks.length) {
      setNotice(
        "No beats found. Cut to beat reads the audio on the timeline — add a " +
          "music track (and unmute it) and the markers appear on its lane."
      );
      return;
    }
    const durations = frames.map((f) => f.duration_ms);
    const { cuts, moved } = planBeatCuts(durations, marks, { minMs: MIN_MS });
    if (!moved) {
      setNotice("Every cut is already on a beat.");
      return;
    }
    // No explicit undo push: the stack watches the document's signature, so one
    // `setFrames` is one snapshot and therefore one Ctrl+Z. See `useUndoStack`.
    const timed = cutsToDurations(durations, cuts, { minMs: MIN_MS });
    setFrames((list) => list.map((f, i) => ({ ...f, duration_ms: timed[i] })));
    setNotice(
      `${moved} cut${moved === 1 ? "" : "s"} pulled onto the beat. Ctrl+Z puts them back.`
    );
  }, [frames, audioTracks, audioAnalyses, setFrames]);

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
          <button
            type="button"
            className="btn back-btn"
            onClick={onBack}
            title="Your Animatics"
            aria-label="Your Animatics"
          >
            ←
          </button>
        </div>
      </div>
    );
  }

  // What the export will actually be: the whole timeline by default, or just
  // the pictures if that's been chosen.
  const exportMs = settings.end_at === "frames" ? totalMs : spanMs;
  // Which FILE the Export button will produce. Everything saved before presets
  // existed has no `container` at all and normalises to "mp4", which is what it
  // has always written.
  const exportContainer = normaliseContainer(settings.container);
  const aspectCss = (settings.aspect_ratio || "16:9").replace(":", " / ");
  // The same ratio as a plain number. CSS can hold the shape with `aspect-ratio`
  // alone only when ONE axis is definite; in a box constrained on both (which is
  // what "fit inside this pane" means) it silently gives up and the preview
  // stops matching the exported frame. Sizing the width off the container's
  // height with this number keeps it exact — see `.an-screen-fit`.
  const arNum = aspectNumber(settings.aspect_ratio);
  // A workspace is the shape of the SCREEN; the aspect ratio is the shape of the
  // FILM. They are allowed to disagree — cutting a vertical version of a wide
  // film is a real job, and it is the one the Reel workspace was built for — but
  // the commonest reason they disagree is that someone switched workspace and
  // expected the video to follow it. So the Program head offers the change once,
  // in one direction only: "Reel / Shorts" says outright what shape it is for,
  // while Long is the DEFAULT workspace, so a vertical film sitting in it means
  // nothing and a nag there would fire on every project that isn't wide.
  const suggestedAspect = workspace === "reel" && arNum > 1 ? "9:16" : "";

  // ⚠ THE ONE WAY IN FOR A CHANGE OF FRAME SHAPE. Every control that can write
  // `aspect_ratio` goes through here — the Program pane's menu, the Shape chips
  // in Video properties (including their ↺), the "Make it 9:16" offer and the
  // export presets, which reshape the film as a side effect of choosing TikTok.
  // A plain `setSettings` beside any of them would be a route that skips the
  // carrying-over below, and the bug it reintroduces (a star stretched into a
  // lozenge) shows up two screens away from the line that caused it.
  //
  // The pictures need nothing: `placePicture` re-fits each one from its source
  // against the new frame on the very next draw, which is what "fit" means. It
  // is the boxes that have to be carried — see `refitBox`.
  //
  // One event, so React commits all three together: one document change, and
  // therefore ONE Ctrl+Z that puts the shape back AND the boxes with it.
  function reshapeFrame(patch) {
    const from = settings.aspect_ratio || "16:9";
    const next = typeof patch === "function" ? patch(settings) : { ...settings, ...patch };
    setSettings(next);
    const to = next.aspect_ratio || "16:9";
    if (to === from) return;
    const carry = (list) => list.map((item) => ({ ...item, ...refitBox(item, from, to) }));
    setShapes(carry);
    setOverlays(carry);
  }
  // One step of zoom, from a button or from the Zoom tool. `dir` is ±1.
  const zoomBy = (dir) =>
    setPxPerSec((p) =>
      Math.min(MAX_PPS, Math.max(MIN_PPS, p * Math.pow(ZOOM_STEP, dir)))
    );
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

  // ⚠ WHAT IS APPLIED IS THE CLAMPED COPY, NOT THE STATE. The window can shrink
  // under a layout that was fine when it was dragged, and re-clamping on the way
  // OUT means the pane comes back to the size you chose when the window is
  // opened up again, instead of being permanently trimmed by the smallest window
  // it ever saw.
  const limits = paneLimits(vp);
  const sized = clampLayout(layout, vp);
  const setPane = (key, px) => {
    layoutTouched.current = true;
    setLayout((l) => ({ ...l, [key]: px }));
  };
  // Double-click a seam. Back to this workspace's default for THAT pane only —
  // the other two are still whatever you made them.
  const resetPane = (key) => {
    layoutTouched.current = true;
    setLayout((l) => ({ ...l, [key]: defaultLayout(workspace, vp)[key] }));
  };

  // Switching workspaces is a LAYOUT change and nothing else — no setSettings
  // call belongs here. A maximized pane is dropped on the way, because "which
  // pane is filling the screen" means something different once the panes have
  // moved, and the sizes come back to whatever that workspace was left at.
  const chooseWorkspace = (id) => {
    setWorkspace(id);
    saveWorkspace(id);
    // Loaded, not carried over: these are the sizes that workspace was left in.
    layoutTouched.current = false;
    setLayout(getPaneLayout(id, vp));
    setMaximized(null);
    setSettingsOpen(false);
  };

  const paneProps = (name) => ({
    onMouseEnter: () => {
      hoverPaneRef.current = name;
    },
    className: `an-pane an-pane-${name}${maximized === name ? " an-maxed" : ""}`,
  });

  return (
    <div
      className={`an-nle an-ws-${workspace} ${
        maximized ? `an-has-max an-max-${maximized}` : ""
      }`}
      // The whole layout, as three custom properties. The grid and the timeline
      // read them; nothing else in the editor has to know a pane was resized.
      style={{
        "--an-col-left": `${sized.left}px`,
        "--an-col-right": `${sized.right}px`,
        "--an-timeline-h": `${sized.timeline}px`,
      }}
    >
      {/* ------------------------------------------------------- top bar */}
      <header className="an-topbar">
        <button
          type="button"
          className="btn small back-btn"
          onClick={handleBack}
          title="Your Animatics"
          aria-label="Your Animatics"
        >
          ←
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

        {/* The workspace, named and changeable — Premiere puts the same thing
            in the same corner. The name is here rather than only inside the
            menu because "which layout am I in?" is a question you ask by
            LOOKING, not by opening something. */}
        <span className="an-ws-name" title="The layout you're editing in">
          {workspaceLabel(workspace)}
        </span>
        {/* ⚠ THE BUTTON WEARS THE LAYOUT IT IS IN, not a gear. A cog says
            "settings live here" and nothing about what pressing it changes;
            this draws the arrangement you are currently working in — the
            picture's column filled in — so the icon and the name beside it say
            the same thing. It changes when you switch workspace. */}
        <button
          type="button"
          className="btn small an-ws-btn"
          onClick={() => setSettingsOpen(true)}
          title={`${workspaceLabel(workspace)} — click to switch layout`}
          aria-label="Workspace layout"
        >
          <Icon name={workspaceIcon(workspace)} />
        </button>

        {video && !exporting && (
          <button
            type="button"
            className={`btn small ${video.stale ? "an-stale" : ""}`}
            // The extension comes off the EXPORT that was made, never off the
            // dialog's current setting — changing the preset without exporting
            // again must not rename a file it did not produce.
            onClick={() =>
              api.downloadAnimaticVideo(
                animaticId,
                `${exportName || title || "animatic"}.${containerExt(video.container)}`
              )
            }
            title={
              video.stale
                ? "This file is from before your latest edits — export again for an up-to-date one"
                : `${
                    video.duration_ms ? `${formatTime(video.duration_ms)} · ` : ""
                  }${video.width}×${video.height} · ${(
                    (video.size_bytes || 0) / 1048576
                  ).toFixed(1)} MB`
            }
          >
            <Icon name="download" />
            {video.stale
              ? ` ${containerExt(video.container).toUpperCase()} (out of date)`
              : ` Download ${containerExt(video.container).toUpperCase()}`}
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
              // ⚠ THE PLAYHEAD IS CAPTURED HERE, on the way in, and NOT when
              // Export is pressed. A still is a picture of a moment, and the
              // moment it is a picture of has to be settled and shown before
              // the button is pressed — the dialog prints it. Reading it at
              // press time instead would race the autosave: `flush()` sends
              // whatever the doc ref holds, which is last render's settings.
              setSettings((s) => ({ ...s, still_ms: Math.round(timeMs) }));
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
              {/* ⚠ A LIBRARY, LIKE SHAPES — NOT A PANEL OF WHAT IS APPLIED.
                  The chain a clip carries is MANAGED in Properties, where there
                  is room for its parameters and its keyframes; this tab is the
                  shelf you take from. Two places, two questions: "what can I
                  add" and "what is on this clip". */}
              <button
                type="button"
                className={`an-tab ${mediaTab === "effects" ? "on" : ""}`}
                onClick={() => setMediaTab("effects")}
              >
                Effects
              </button>
            </span>
            <span className="an-spacer" />
            {/* How the footage is listed. Only on the Media tab: the shape
                picker is a fixed gallery of tiles, so a view switch over it
                would be a control that does nothing. */}
            {mediaTab === "media" && (
              <span className="an-view-switch">
                {MEDIA_VIEWS.map((v) => (
                  <button
                    type="button"
                    key={v.id}
                    className={`an-tool ${mediaView === v.id ? "on" : ""}`}
                    aria-pressed={mediaView === v.id}
                    title={`${v.label} — ${v.note}`}
                    onClick={() => {
                      setMediaView(v.id);
                      saveMediaView(v.id);
                    }}
                  >
                    <Icon name={v.ico} title={v.label} />
                  </button>
                ))}
              </span>
            )}
            <span className="tiny muted">
              {mediaTab === "media"
                ? `${frames.length} frames`
                : mediaTab === "effects"
                  ? `${FX_ITEM_COUNT} to drag`
                  : `${shapes.length} on the timeline`}
            </span>
          </div>

          {mediaTab === "effects" ? (
            <div className="an-pane-body an-media-body">
              {/* The hint lives behind the section's ⓘ for the same reason the
                  shape picker's does: it is true forever and read once, so as
                  standing prose it costs three lines of a narrow pane on every
                  visit. */}
              <PropGroup
                id="media:fx-library"
                title="Add an effect"
                info="Drag one onto a picture on the timeline to grade that shot, or onto a cut to put a transition on it — the bar you are over lights up. Clicking adds it at the playhead instead. Its controls then live in Properties, under Effects."
              >
                <EffectsLibrary onAdd={addFxFromLibrary} />
              </PropGroup>
            </div>
          ) : mediaTab === "shapes" ? (
            <div className="an-pane-body an-media-body">
              {/* ⚠ THE HINT IS THE SECTION'S ⓘ, NOT A PARAGRAPH UNDER THE
                  TILES. It is true forever and read once, so as standing prose
                  it cost three lines of a narrow pane on every visit for
                  something you stop seeing after the first (asked for as "put
                  it behind an i button"). `info` on `PropGroup` is where a note
                  about a WHOLE section goes — same ⓘ, same right-hand column,
                  as every row in the Properties pane. */}
              <PropGroup
                id="media:shape-library"
                title="Add a shape"
                info="A shape lands on the frame at the playhead, then moves and re-times like any other clip. Drag it on the picture to place it — or drag a tile straight onto a shape row on the timeline to drop it there instead."
              >
                <ShapeGallery
                  onAdd={(kind) => {
                    addShape(kind, pendingShapeLane);
                    setPendingShapeLane("");
                  }}
                />
              </PropGroup>

              {shapes.length > 0 && (
                <PropGroup id="media:shapes" title="In this animatic" count={shapes.length}>
                  {shapes.map((s, i) => (
                    <button
                      type="button"
                      key={s.id}
                      className={`an-media-track ${selectedShapeId === s.id ? "sel" : ""}`}
                      onClick={() => {
                        selectOnly({ shape: s.id });
                        seek(s.start_ms);
                      }}
                      /* Drag it onto a shape row to re-time it, or onto a
                         different one to move it there — same marker trick as
                         every other draggable asset in this pane. */
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.effectAllowed = "move";
                        e.dataTransfer.setData(
                          "application/x-anim-asset",
                          JSON.stringify({ kind: "shapeClip", id: s.id })
                        );
                        e.dataTransfer.setData("application/x-anim-shape", "");
                      }}
                    >
                      <ShapeSwatch kind={s.kind} color={s.color} className="an-media-ico" />
                      <span className="an-media-name">
                        {SHAPE_KINDS.find((k) => k.id === s.kind)?.label || s.kind} {i + 1}
                      </span>
                      <span className="tiny muted">{formatTime(s.start_ms)}</span>
                    </button>
                  ))}
                </PropGroup>
              )}
            </div>
          ) : (
          <div
            className={`an-pane-body an-media-body ${dropping ? "an-dropping" : ""}`}
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
              <span className="an-asset-note">
                Images and video for the picture track · an MP3 for the audio
              </span>
            </button>

            {/* ⚠ EVERY LIST IN THIS PANE IS A SECTION YOU CAN CLOSE, and it is
                the Properties pane's section (`PropGroup`) — same twist, same
                count pill, same memory of what you closed. A 31-frame board
                pushed the audio and the shapes below the fold, so the only way
                to reach a track was to scroll past every panel; now you fold
                Frames shut and what you added after it is right there. The
                header still carries the count, so a closed section says what is
                inside it.
                ⚠ The add-assets control stays OUTSIDE the sections: it is what
                fills them, and a drop target you can collapse is one you cannot
                drop on. */}
            {/* ⚠ THREE SECTIONS, ONE SEQUENCE. The picture track is grouped by
                WHERE EACH CLIP CAME FROM (`pictureTrack` / `frameOrigin`), which
                is the question you ask this pane: "where is the video I just
                dropped in?" was unanswerable in a strip of thirty-two panels
                that happened to end with it. Grouping by origin and not by kind
                is what keeps an animated board shot — a video clip now — in
                Storyboard Frames where you left it.
                Each strip gets `indexOf`, so a number badge, a drop and a
                reorder still mean a place in the WHOLE sequence: the sections are
                a way of looking at one track, not three tracks. A section with
                nothing in it isn't drawn — an empty "Video" heading on a board
                that has none is a row of furniture. */}
            {[
              { id: "media:frames", title: "Storyboard Frames", list: pictureTrack.board },
              { id: "media:video", title: "Video", list: pictureTrack.video },
              { id: "media:images", title: "Images", list: pictureTrack.image },
            ]
              .filter((sec) => sec.list.length > 0)
              .map((sec) => (
                <PropGroup key={sec.id} id={sec.id} title={sec.title} count={sec.list.length}>
                  <FrameStrip
                    vertical
                    view={mediaView}
                    showAdd={false}
                    heading={false}
                    frames={sec.list}
                    indexOf={seqIndex}
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
                </PropGroup>
              ))}

            {/* Only appears once there IS audio — an empty "Audio" heading with
                its own add button was the third of the three controls. */}
            {audioTracks.length > 0 && (
              <PropGroup id="media:audio" title="Audio" count={audioTracks.length}>
                {/* A LIST, not a mixer. Volume and the rest live in Properties
                    — click a track to edit it, same as a frame or a caption. */}
                {audioTracks.map((track) => (
                  <button
                    type="button"
                    className={`an-media-track ${
                      selectedTrackId === clipId(track) ? "sel" : ""
                    }`}
                    key={clipId(track)}
                    onClick={() => selectOnly({ track: clipId(track) })}
                    /* Drag it onto an audio lane to move it there — see
                       `dropAsset`. The empty `…-audio` marker beside the
                       payload is what lets a lane refuse it mid-drag, when
                       `getData` still reads blank (`dragKind` in
                       Timeline.jsx). */
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData(
                        "application/x-anim-asset",
                        JSON.stringify({ kind: "audio", id: clipId(track) })
                      );
                      e.dataTransfer.setData("application/x-anim-audio", "");
                    }}
                  >
                    <span className="an-media-ico">♪</span>
                    <span className="an-media-name" title={track.filename}>
                      {track.filename}
                      {/* Which PIECE of the file this is, once there is more
                          than one. Without it a cut track reads as the same
                          name listed twice with no way to tell them apart. */}
                      {audioTracks.filter((a) => a.upload_id === track.upload_id).length > 1 && (
                        <span className="muted">
                          {" "}
                          · clip{" "}
                          {audioTracks
                            .filter((a) => a.upload_id === track.upload_id)
                            .findIndex((a) => clipId(a) === clipId(track)) + 1}
                        </span>
                      )}
                    </span>
                    <span className="tiny muted">
                      {track.muted ? "muted" : `${Math.round((track.volume ?? 1) * 100)}%`}
                    </span>
                  </button>
                ))}
              </PropGroup>
            )}
          </div>
          )}
        </section>

        {/* The seam between the first column and the middle. ⚠ IT DOES NOT MOVE
            WITH THE WORKSPACE: it always sizes the LEFT column, whichever pane
            the workspace has put there — Media in Long, Program in Reel. Only
            the panes reorder (CSS `order`); the two seams stay where they are,
            which is the only arrangement where a drag means one thing. */}
        <PaneSplitter
          className="an-split-left"
          value={sized.left}
          min={limits.left.min}
          max={limits.left.max}
          sign={1}
          onChange={(px) => setPane("left", px)}
          onReset={() => resetPane("left")}
          label={workspace === "reel" ? "Program width" : "Media width"}
        />

        {/* ---- Program: what the viewer would see right now ---- */}
        <section {...paneProps("program")}>
          <div className="an-pane-head">
            <span className="an-pane-title">Program</span>
            {/* ⚠ THIS WRITES THE PROJECT — it is not the workspace picker. The
                shape of the film used to be reachable only through Video
                properties, which is the pane you are NOT looking at whenever a
                clip is selected; switching to the Reel workspace and finding
                the video still 16:9 with no visible way to change it is exactly
                what that cost. It sits here because the monitor is the thing
                that changes shape when you press it, and it is the same field
                the Shape chips write — one project, one aspect ratio. */}
            <select
              className="an-select an-ar-select"
              aria-label="Aspect ratio"
              title="The shape of the video — every export uses this"
              value={settings.aspect_ratio || "16:9"}
              onChange={(e) => reshapeFrame({ aspect_ratio: e.target.value })}
            >
              {/* A project can carry a shape that isn't a chip (a 21:9 board,
                  say). Offering it keeps the menu honest about what the film
                  currently is instead of showing the nearest one it knows. */}
              {!knownAspect(settings.aspect_ratio) && settings.aspect_ratio && (
                <option value={settings.aspect_ratio}>{settings.aspect_ratio}</option>
              )}
              {ASPECTS.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label} — {a.note}
                </option>
              ))}
            </select>
            <span className="tiny muted">
              {frameSizeFor(settings.aspect_ratio, settings.resolution ?? 1080).join("×")} ·{" "}
              {settings.fps} fps
            </span>
            {/* The workspace and the film disagree about which way up this is.
                ⚠ A SUGGESTION, NEVER AN AUTOMATIC CHANGE: `chooseWorkspace` is
                still forbidden from writing settings, because rearranging your
                screen must not silently reshape a finished edit. This is the
                user pressing a button, which is a different thing entirely. */}
            {suggestedAspect && (
              <button
                type="button"
                className="an-tool an-ar-suggest"
                title={`This workspace is for ${suggestedAspect} video, but the film is ${settings.aspect_ratio}. Press to reshape it — your shots keep their framing and get bars until you reframe them.`}
                onClick={() => reshapeFrame({ aspect_ratio: suggestedAspect })}
              >
                Make it {suggestedAspect}
              </button>
            )}
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
                onUnavailable={onGlUnavailable}
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
              {/* ⚠ NO "Frame 7 of 34" READOUT HERE ANY MORE — asked for and
                  removed. The monitor's job is the picture; which frame is up
                  is already told by the playhead and by the selected bar on
                  the timeline, so a third copy under the clock was furniture in
                  a bar that had to get smaller, not wider. `currentIndex` is
                  still what the arrows step (`stepFrame`). */}
            </div>
          </div>
        </section>

        {/* …and the seam on the other side. `sign={-1}`: this handle is on the
            LEFT of Properties, so travelling right makes that column narrower,
            not wider. */}
        <PaneSplitter
          className="an-split-right"
          value={sized.right}
          min={limits.right.min}
          max={limits.right.max}
          sign={-1}
          onChange={(px) => setPane("right", px)}
          onReset={() => resetPane("right")}
          label="Properties width"
        />

        {/* ---- Properties: whatever is selected. One pane, three states,
                so there is only ever one place to look for a setting. ---- */}
        <section {...paneProps("props")}>
          <div className="an-pane-head">
            <span className="an-pane-title">Properties</span>
            <span className="tiny muted">
              {multiSelected
                ? `Selection · ${liveSelection.length}`
                : selectedTransition
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
            {(multiSelected ||
              selectedTransition ||
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
            {/* ⚠ THE SET COMES FIRST. With forty clips lit up on the timeline,
                showing the first one's colour picker would let you edit one
                thing while looking at forty — so a multi-selection gets a pane
                about the SET, and clicking a single clip is how you get back to
                its own settings. */}
            {multiSelected ? (
              <SelectionProperties
                selection={liveSelection}
                groupedCount={
                  liveSelection.filter((item) =>
                    (groupPools[item.kind] || []).some(
                      (c) => c.id === item.id && c.group_id
                    )
                  ).length
                }
                onMove={moveSelection}
                onGroup={() => groupSelection(true)}
                onUngroup={() => groupSelection(false)}
                onDelete={() => deleteMany(liveSelection)}
                onClose={() => selectOnly({})}
              />
            ) : selectedTransition ? (
              <TransitionProperties
                transition={selectedTransition}
                frames={frames}
                background={settings.background}
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
                index={audioTracks.findIndex((a) => clipId(a) === clipId(selectedTrack))}
                tracks={audioTracks}
                gesture={gestureProps}
                onChange={patchTrack}
                onRemove={removeTrack}
                onCaptions={openCaptions}
                captionsBusy={serverBusy}
                captionsProgress={speechRunning ? speechProgress : null}
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
                // BACK TO THE STORYBOARD, from the pane you are already in.
                // Both render nothing unless this clip's picture comes from a
                // board panel, so an animatic built from uploads is unchanged.
                board={
                  <>
                    <RegeneratePanelInline
                      animaticId={animaticId}
                      frameId={selectedFrame.id}
                      url={urls[selectedFrame.id]}
                      onRedrawn={onPanelRedrawn}
                      onError={setError}
                    />
                    <RelengthShotInline
                      animaticId={animaticId}
                      frameId={selectedFrame.id}
                      busy={reblockFor === selectedFrame.id}
                      onRelength={(seconds) => relengthShot(selectedFrame.id, seconds)}
                    />
                  </>
                }
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
                // Through `reshapeFrame`, not `setSettings`: the Shape chips and
                // their ↺ write the same field the Program menu does, so they
                // have to carry the shapes over the same way. It is a no-op for
                // every other setting in this pane.
                onChange={reshapeFrame}
                sourceBoard={sourceBoard}
                // ⚠ OPENS THE PRICED DIALOG, never runs anything. Same rule as
                // ✨ Animate and the two speech passes: no button in a
                // Properties pane may spend on its own.
                reframe={
                  <PropRow full>
                    <button
                      type="button"
                      className="btn small"
                      disabled={reframeRunning || serverBusy || !frames.length}
                      onClick={openReframe}
                      title={
                        `Look at each shot and pan it so the subject is framed for ` +
                        `${settings.aspect_ratio}. You'll see the price first, and what ` +
                        `it writes is an ordinary pan you can still change.`
                      }
                    >
                      {reframeRunning
                        ? `Framing for ${settings.aspect_ratio}…`
                        : `✨ Reframe every shot for ${settings.aspect_ratio}`}
                    </button>
                  </PropRow>
                }
              />
            )}
          </div>
        </section>
      </div>

      {/* The seam between the panes and the timeline. Dragging it DOWN gives the
          picture the height and takes it off the timeline, so the value goes the
          other way — hence `sign={-1}` again. */}
      <PaneSplitter
        orientation="horizontal"
        className="an-split-timeline"
        value={sized.timeline}
        min={limits.timeline.min}
        max={limits.timeline.max}
        sign={-1}
        onChange={(px) => setPane("timeline", px)}
        onReset={() => resetPane("timeline")}
        label="Timeline height"
      />

      {/* ------------------------------------------------------- timeline */}
      <section {...paneProps("timeline")}>
        <div className="an-pane-head">
          <span className="an-pane-title">Timeline</span>
          {/* THE LENGTH, AND NOTHING ELSE. The "audio 2:40 — video ends early"
              badge that used to sit beside it is gone at the user's request: it
              was a sentence of running commentary on the busiest bar in the
              editor, and it was never news — the ruler already runs to the end
              of the audio and the transport clock counts past the last picture,
              so the timeline SHOWS what the badge was describing. ⇔ Fit to audio,
              a step to the right, is still the fix it was pointing at. */}
          <span className="an-tl-total tiny">
            <strong>{formatTime(totalMs)}</strong>
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
          {/* CUT TO BEAT. Beside snapping because it is the same idea done to
              the whole sequence at once: the beat markers are already drawn on
              the audio lane and already snap targets, and this is dragging
              every cut onto the nearest one without dragging any of them.
              Costs nothing — the decode has already happened. */}
          <button
            type="button"
            className="an-tool"
            onClick={cutToBeat}
            disabled={frames.length < 2 || !audioTracks.length}
            title={
              !audioTracks.length
                ? "Cut to beat needs music on the timeline — it reads the beats already marked on the audio lane"
                : "Pull every cut onto the nearest beat of the music. Free, and one Ctrl+Z puts them all back."
            }
          >
            🥁
          </button>

          {/* ⚠ TEXT / COLOUR CARD / VOICEOVER ARE NOT HERE ANY MORE — they are
              passed to `<Timeline addTools>` below and drawn beside ＋ Add
              layer. They were at the far right of this bar, a bar's width from
              the only other control that adds anything; asked for as "one place
              where all the add buttons are". They are still THIS component's
              buttons — what they make, and that the voiceover one spends quota,
              is the editor's business, not the timeline's. */}
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
            onDropAsset={dropAsset}
            onManageEffects={manageEffects}
            addTools={
              <>
                <button
                  type="button"
                  className="btn small an-add-text"
                  onClick={
                    // NOT `onClick={addText}` — React would pass the click event
                    // as the layer id and the caption would land on a lane that
                    // doesn't exist. This button always means the default text
                    // lane.
                    () => addText("")
                  }
                  title="Add a text clip over the frame at the playhead"
                >
                  <Icon name="text" /> Text
                </button>
                {/* The other clip you can make without a file. Sits beside Text
                    on purpose: those two are the whole set, and a colour card
                    had no way in at all until now even though the
                    `kind: "color"` clip underneath it was built and tested. Not
                    disabled on an empty animatic — a black slug is a perfectly
                    ordinary first clip. */}
                <button
                  type="button"
                  className="btn small an-add-card"
                  onClick={() => addColorCard()}
                  title="Add a colour card after the frame at the playhead — a slug, a blackout or a flash. Pick its colour in Properties."
                >
                  <Icon name="card" /> Colour card
                </button>
                {/* ⚠ SPENDS QUOTA — but only through the priced panel it opens,
                    like ✨ Animate. The lines come from the board this animatic
                    was made from, timed to the shots that reference them, so
                    there is nothing to type: that is the whole reason it lives
                    in here.

                    Plain `btn small`, and deliberately NOT the `.an-add-text` /
                    `.an-add-card` weight: those two are a pair that makes a clip
                    out of nothing and costs nothing. This one spends, and
                    reading as one of them would be a lie about it. */}
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
              </>
            }
            frames={frames}
            texts={texts}
            shapes={shapes}
            totalMs={totalMs}
            spanMs={spanMs || 1000}
            timeMs={timeMs}
            pxPerSec={pxPerSec}
            // The ruler reads its timecode in the rate the film is EXPORTED at,
            // so the frame you cut on is the frame that gets rendered.
            fps={settings.fps}
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
            onToggleMute={muteTracks}
            onTrackChange={patchTrack}
            onSelect={(id) => selectOnly({ frame: id })}
            onSelectText={(id) => selectOnly({ text: id })}
            onSelectShape={(id) => selectOnly({ shape: id })}
            onSelectOverlay={(id) => selectOnly({ overlay: id })}
            selectedTrackId={selectedTrackId}
            onSelectTrack={(id) => selectOnly({ track: id })}
            /* More than one at a time — the rubber band, shift-click, and a
               whole selection dragged as one. See `animatic/selection.js`. */
            selection={liveSelection}
            onSelectMany={selectMany}
            onToggleSelect={toggleSelect}
            onMoveSelection={moveSelection}
            onMoveToLane={moveClipToLane}
            selectionFloorMs={selectionFloorMs}
            onSeek={seek}
            onResize={(id, ms) => patchFrame(id, { duration_ms: ms })}
            onFramesChange={patchFrames}
            // The head trim of the first picture writes `duration_ms` and
            // `in_ms` in ONE patch — see `startHeadTrim` in `Timeline.jsx`.
            onFrameChange={patchFrame}
            onTextChange={patchText}
            onShapeChange={patchShape}
            onOverlayChange={patchOverlay}
            onKeyMove={moveKeyframe}
            onAddToLane={addToLane}
            onRemoveLayer={removeLayer}
            onAddLayer={() => setLayerMenu(true)}
            onRemoveTrack={removeTrack}
            onClearLane={clearLane}
            onToggleHidden={toggleLaneHidden}
            onSplitFootage={splitFootageOntoTrack}
            tool={tool}
            snapping={snapping}
            onRazor={razorAt}
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

      {/* ⚠ THE STATUS STRIP IS AT THE FOOT OF THE EDITOR, not under the top
          bar. It is a running commentary — a notice, an export percentage — and
          up there it sat between the title and the workspace, pushing the
          monitor and every pane down the moment it had anything to say. Down
          here it is the status bar every editor in the world has, it appears
          and disappears without moving the picture, and it is still ONE line
          for all of them so two events can never stack.
          ⚠ It is also LAST IN THE DOM now, which is what puts it at the foot of
          the Long workspace's flex column; the Reel workspace places it by name
          (`stat`), so its grid template moved it too. */}
      {(error || notice || exporting || animating || speechRunning ||
        reframeRunning || reblockJob) && (
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
          {/* A reframe pass is the server writing this project's frames, so it
              says so for exactly the reason the captions pass does. */}
          {reframeRunning && !exporting && !animating && !speechRunning && (
            <span className="an-status-export">
              <span className="spinner-inline" />
              {reframeProgress?.message || "Framing each shot…"}
              <span className="an-status-bar">
                <span style={{ width: `${reframeProgress?.percent ?? 0}%` }} />
              </span>
              {reframeProgress?.percent ?? 0}%
            </span>
          )}
          {/* ⚠ A RE-BLOCK IS THE ONE THAT IS NOT THIS PROJECT. The drawings are
              made on the STORYBOARD, so this animatic stays fully editable
              while it runs — which is exactly why it needs to say something,
              or minutes pass with nothing on screen but a pane that has gone
              quiet. */}
          {reblockJob && (
            <span className="an-status-export">
              <span className="spinner-inline" />
              {reblockProgress?.message || "Drawing more key poses on the storyboard…"}
              <span className="an-status-bar">
                <span style={{ width: `${reblockProgress?.percent ?? 0}%` }} />
              </span>
              {reblockProgress?.percent ?? 0}%
            </span>
          )}
        </div>
      )}

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

              {/* ⚠ FIRST, because it writes the four rows under it. Choosing a
                  preset is nothing but a shorthand for setting them, so
                  changing one afterwards is not a conflict — it just drops the
                  menu back to Custom (`matchPreset` is the exact inverse of
                  `applyPreset`). The table is the twin of `export_presets.py`. */}
              <label className="an-exp-label" htmlFor="exp-preset">
                Preset
              </label>
              <select
                id="exp-preset"
                className="an-select"
                value={matchPreset(settings)}
                // ⚠ A PRESET RESHAPES THE FILM — TikTok is 9:16, and that is the
                // whole point of choosing it — so it goes through the same door
                // as the Program menu. Picking TikTok used to stretch every
                // shape on the way past, silently, from inside the export
                // dialog.
                onChange={(e) => reshapeFrame((s) => applyPreset(e.target.value, s))}
              >
                <option value="">Custom</option>
                {EXPORT_PRESETS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label} — {p.hint}
                  </option>
                ))}
              </select>

              <span className="an-exp-label">Format</span>
              <span className="tiny muted an-exp-fixed">
                {exportContainer === "gif"
                  ? "GIF · silent, loops forever"
                  : exportContainer === "png"
                    ? `PNG · one frame, at ${formatTime(settings.still_ms || 0)}`
                    : "MP4 · H.264 + AAC — plays everywhere"}
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
                disabled={exportContainer === "png"}
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
                // Quality is an x264 CRF. A GIF is palette-quantised and a PNG
                // is lossless, so there is nothing for it to mean in either —
                // disabled rather than hidden, so the row doesn't jump.
                disabled={exportContainer !== "mp4"}
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
                disabled={exportContainer === "png"}
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
                  checked={settings.include_audio !== false && exportContainer === "mp4"}
                  // A GIF and a PNG have no audio track to carry, so the box is
                  // off and unclickable rather than ticked and ignored.
                  disabled={!audioTracks.length || exportContainer !== "mp4"}
                  onChange={(e) =>
                    setSettings((s) => ({ ...s, include_audio: e.target.checked }))
                  }
                />
                {exportContainer !== "mp4"
                  ? `A ${exportContainer.toUpperCase()} carries no sound`
                  : audioTracks.length
                    ? `Include ${audioTracks.length} track${audioTracks.length === 1 ? "" : "s"}`
                    : "No audio on this animatic"}
              </label>
            </div>

            <div className="an-exp-summary">
              <strong>
                {exportContainer === "png" ? "One frame" : formatTime(exportMs)}
              </strong>
              <span>
                {frameSizeFor(settings.aspect_ratio, settings.resolution ?? 1080).join("×")}
              </span>
              {exportContainer !== "png" && <span>{settings.fps} fps</span>}
              <span>
                {frames.length} frame{frames.length === 1 ? "" : "s"}
              </span>
              {texts.length > 0 && <span>{texts.length} text</span>}
            </div>
            {/* No size estimate on purpose: an animatic is mostly still frames,
                which compress far better than normal video, so any figure we
                printed would be wrong by a wide margin. */}
            <p className="tiny muted an-exp-note">
              {exportContainer === "png"
                ? `The picture at ${formatTime(settings.still_ms || 0)} — where the playhead was when you opened this. Close it, move the playhead, and open it again for a different frame.`
                : exportContainer === "gif"
                  ? `${formatTime(exportMs)} of silent, looping GIF. They are big and 256 colours; a short stretch reads far better than the whole film.`
                  : settings.end_at === "frames"
                    ? `Stops at your last image — ${formatTime(totalMs)}. Anything after it is cut.`
                    : spanMs > totalMs
                      ? `Runs to ${formatTime(spanMs)}: your last image is held on screen while the rest of the audio plays. Choose “Just the images” to stop at ${formatTime(totalMs)} instead.`
                      : `${formatTime(totalMs)} — your images, text and audio all end together.`}
            </p>
            {/* ⚠ A PRESET CAN RESHAPE THE FILM, and it must say so. YouTube is
                16:9 and TikTok is 9:16, so choosing one WRITES the project's
                aspect ratio — a real edit, visible in the monitor behind this
                dialog the moment it is chosen. What it does not do is re-frame
                the shots: they keep their framing and get bars. ✨ Reframe, on
                the Frame tab, is the thing that re-composes them. Shown only
                when a preset states a shape, so an ordinary export is not
                lectured about a change nobody made. */}
            {matchPreset(settings) &&
              EXPORT_PRESETS.find((p) => p.id === matchPreset(settings))?.aspect_ratio && (
                <p className="tiny muted an-exp-note">
                  This preset set the project to {settings.aspect_ratio}. Your shots keep
                  their framing, so anything that doesn’t fit gets{" "}
                  {settings.fit === "cover" ? "cropped" : "bars"} — use ✨ Reframe on the
                  Frame tab to re-compose them for this shape.
                </p>
              )}

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

      {/* ⚙ Settings — the workspace picker. */}
      {/* ⚠ NOTHING HERE WRITES THE PROJECT. A workspace decides where the panes
          sit and how wide they are; the frame size, the aspect ratio and the
          fps are the Video tab's business and are left exactly as they were.
          Switching to Reel / Shorts on a 16:9 animatic gives you a tall monitor
          to work in and a 16:9 export, which is the point — you can cut a
          vertical version without converting the video first. */}
      {settingsOpen && (
        <div className="modal-overlay" onClick={() => setSettingsOpen(false)}>
          <div className="card an-layer-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setSettingsOpen(false)}>
              ✕
            </button>
            <h2>Workspace</h2>
            {/* ⚠ SAY WHERE THE OTHER THING IS. "Your video stays 16:9" is true
                and was, on its own, the whole problem: it told you what had NOT
                happened and left you hunting for the control that would make it
                happen. The shape of the film is one menu away, in the Program
                pane's head. */}
            <p className="muted">
              How the editor is laid out. This changes the screen only — your
              video stays {settings.aspect_ratio} at {settings.fps} fps. To
              change the shape of the video itself, use the ratio menu at the top
              of the Program pane.
            </p>

            <div className="an-layer-list">
              {WORKSPACES.map((w) => (
                <button
                  key={w.id}
                  type="button"
                  className={`an-layer-opt an-ws-opt ${workspace === w.id ? "on" : ""}`}
                  onClick={() => chooseWorkspace(w.id)}
                  aria-pressed={workspace === w.id}
                >
                  <span className="an-layer-opt-ico an-ws-opt-ico">
                    {/* The size is CSS's (`.an-ws-opt-ico .icon`), not this
                        attribute's — the drawing fills its square in both
                        places and that rule is where it is decided. */}
                    <Icon name={w.ico} title={w.label} />
                  </span>
                  <span>
                    <strong>{w.label}</strong>
                    <span className="tiny muted">{w.note}</span>
                  </span>
                  {workspace === w.id && <span className="an-ws-tick">✓</span>}
                </button>
              ))}
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
                  kind: "picture",
                  ico: "🎞",
                  label: "Picture track",
                  note: "Another row for stills and footage — drawn OVER the tracks below it",
                },
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
                    if (opt.kind === "picture") addPictureTrack();
                    else addLayer(opt.kind);
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

              {/* ⚠ THE "Video — not supported yet" ENTRY IS GONE, because it is
                  supported: a picture track holds footage and stills alike, and
                  it is the first entry in this list. Leaving a disabled row
                  saying otherwise would be the worse kind of stale copy. */}
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
                  every one can be edited, restyled or deleted afterwards — on
                  their own <strong>Captions</strong> row at the top of the
                  timeline, so nothing lands on the text you typed.
                </p>
                <p className="muted">
                  Cuts are followed: a track you have razored is captioned piece
                  by piece, where each piece actually plays, and the words in the
                  parts you cut out are not written at all.
                </p>
                <div className="an-prop-row">
                  <span className="an-prop-label">Track</span>
                  <select
                    className="an-select"
                    value={speechTrack}
                    onChange={(e) => setSpeechTrack(e.target.value)}
                  >
                    {/* ⚠ ONE ENTRY PER FILE. Transcribing is done on the FILE,
                        so a track cut into three clips is still one thing to
                        listen to — and three identical options would be three
                        ways to pay for the same transcript. */}
                    {audioTracks
                      .filter(
                        (t, i) =>
                          audioTracks.findIndex((a) => a.upload_id === t.upload_id) === i
                      )
                      .map((t, i) => (
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

      {/* AUTO-REFRAME — the setup, then the price, then the call. Same two-step
          discipline as ✨ Animate and the two speech passes, and kept for the
          same reason: this one is cheap, and a cheap button is the one that
          gets pressed forty times. */}
      {reframeOpen && (
        <div className="modal-overlay" onClick={() => setReframeOpen(false)}>
          <div className="card an-speech-card" onClick={(e) => e.stopPropagation()}>
            <button type="button" className="modal-close" onClick={() => setReframeOpen(false)}>
              ✕
            </button>
            <h2>Reframe for a different shape</h2>
            <p className="muted">
              Each shot is looked at once to find its subject, then panned and
              pushed in so that subject is framed for the new shape. What gets
              written is an ordinary pan — the same Scale and Position you can
              set by hand — so you can change any of it afterwards.
            </p>

            <div className="an-prop-row">
              <span className="an-prop-label">Frame for</span>
              <span className="an-set-chips">
                {["16:9", "9:16", "1:1", "4:5"].map((a) => (
                  <button
                    key={a}
                    type="button"
                    className={`opt-chip ${reframeAspect === a ? "active" : ""}`}
                    onClick={() => setReframeAspect(a)}
                  >
                    {a}
                    {a === settings.aspect_ratio && (
                      <span className="opt-chip-note">this video</span>
                    )}
                  </button>
                ))}
              </span>
            </div>

            <div className="an-prop-row">
              <span className="an-prop-label">Which shots</span>
              <span className="an-set-chips">
                <button
                  type="button"
                  className={`opt-chip ${reframeScope === "all" ? "active" : ""}`}
                  onClick={() => setReframeScope("all")}
                >
                  Every shot
                  <span className="opt-chip-note">{frames.length} on the timeline</span>
                </button>
                <button
                  type="button"
                  className={`opt-chip ${reframeScope === "selection" ? "active" : ""}`}
                  disabled={!liveSelection.some((s) => s.kind === "frame")}
                  onClick={() => setReframeScope("selection")}
                >
                  Just the selection
                  <span className="opt-chip-note">
                    {liveSelection.filter((s) => s.kind === "frame").length} selected
                  </span>
                </button>
              </span>
            </div>

            {/* ⚠ The honest limit, said before it is paid for rather than
                after. Video clips and colour cards are skipped: a clip's
                framing is a property of its footage, and a card has no
                picture. */}
            <p className="tiny muted">
              Stills only — video clips and colour cards are left alone.
            </p>

            {/* ⚠ IN HERE, not in the status bar. The banner is behind this
                overlay, so an error written there is one nobody sees. */}
            {reframeError && <p className="an-prop-warn">⚠ {reframeError}</p>}

            <div className="an-name-actions">
              <button type="button" className="btn ghost" onClick={() => setReframeOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={reframeBusy}
                onClick={askToReframe}
              >
                {reframeBusy ? "Checking the price…" : "See the price →"}
              </button>
            </div>
          </div>
        </div>
      )}

      {reframeConfirm && (
        <div className="modal-overlay" onClick={() => setReframeConfirm(null)}>
          <div className="card fv-confirm" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="modal-close"
              onClick={() => setReframeConfirm(null)}
            >
              ✕
            </button>
            <h2>Reframe {reframeConfirm.estimate.frames} shot(s)?</h2>

            <div className="fv-confirm-price">
              <span className="fv-confirm-usd">
                ~${reframeConfirm.estimate.usd.toFixed(4)}
              </span>
              <span className="tiny muted">estimated</span>
            </div>

            <p className="muted">
              {reframeConfirm.estimate.frames} shot(s) framed for{" "}
              {reframeConfirm.estimate.aspect_ratio} by{" "}
              {reframeConfirm.estimate.model}.
            </p>
            {reframeConfirm.estimate.over_limit && (
              <p className="an-prop-warn">
                ⚠ That is over the limit for one run ({reframeConfirm.estimate.limit}).
                Do it in smaller passes — this is a spend guard, not a technical one.
              </p>
            )}
            <p className="tiny muted">
              An estimate from list prices, not a quote. Google bills the actual
              amount. One Ctrl+Z puts every shot back where it was.
            </p>

            <div className="an-name-actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setReframeConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={reframeBusy || reframeConfirm.estimate.over_limit}
                onClick={doReframe}
              >
                <Icon name="play" />{" "}
                {reframeBusy
                  ? "Starting…"
                  : `Go — ~$${reframeConfirm.estimate.usd.toFixed(4)}`}
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
          track that is actually playing drives the playhead.
          ⚠ ONE ELEMENT PER CLIP, keyed by the clip and not by the upload. Two
          halves of a cut are two windows of one file playing at two different
          moments; one element between them would be seeked back and forth by
          both and would play neither. The blob url is still fetched once per
          FILE — the elements share it. */}
      {audioTracks.map((track) =>
        audioUrls[track.upload_id] ? (
          <audio
            key={clipId(track)}
            ref={(el) => {
              if (el) audioElsRef.current[clipId(track)] = el;
              else delete audioElsRef.current[clipId(track)];
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
        // ⚠ VIDEO TOO. `addAssets` has routed video files since the picture track
        // learned to hold them, and the DROP target next to this button always
        // accepted them — so leaving it out here made the file dialog refuse the
        // exact thing you could drag in, on the control whose whole promise is
        // "one way in for everything".
        accept="image/*,video/*,audio/*"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) addAssets(e.target.files);
          e.target.value = "";
        }}
      />
      {/* The Video row's own ＋ — same handler, narrower filter. */}
      <input
        ref={videoInputRef}
        type="file"
        accept="video/*"
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
