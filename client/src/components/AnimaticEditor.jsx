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
  belongsOnImageLane,
  cardRowKind,
  clipRowKind,
  dominantRowKind,
  isBoardRow,
  isVeoRender,
  rowKindOrLegacy,
  ROW_TAKES,
  frameOrigin,
  frameSpans,
  frameTrack,
  insertShotBeside,
  lookProps,
  lookPropParts,
  lookValueOf,
  resolveLook,
  pictureTracks,
  sceneAt,
  spreadPanelsForRenders,
  setLookValue,
  boxSize,
  textBackdrop,
  backdropHasFill,
  valueAt,
} from "../animatic/scene.js";
// Making room for a take moves PICTURES; these two carry the rest of the film
// along with them — the captions, the voiceover, the text, the Video row.
import {
  coverGrownShots,
  grownSpans,
  renderShifts,
  rippleAudio,
  rippleClips,
  rippleFrames,
} from "../animatic/ripple.js";
import { ASPECTS as BOARD_ASPECTS } from "../storyboardOptions.js";
import { DEFAULT_FONT, cssLineHeight, ensureFontsLoaded, fontFamily } from "../animatic/fonts.js";
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
import { trackPlayMs, trackStartMs } from "../animatic/audio_mix.js";
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
import {
  CAPTION_LAYER_ID,
  CAPTION_LAYER_NAME,
  isGeneratedCaption,
} from "../animatic/captions.js";
import { MIN_SPLIT_MS, splitTimedClip } from "../animatic/razor.js";
// Dragging a row up or down the gutter — and therefore WHAT DRAWS OVER WHAT. The
// maths is out there and pure so it can be driven from node (see
// `tests/lane_reorder_check.py`) and so the exporter's Python twin has something
// to be a twin OF: this order decides the film, not just the look of the gutter.
import {
  laneMovable,
  laneRank,
  laneTokenFor,
  restack,
  seatLane,
  unseatLane,
} from "../animatic/lane_order.js";
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
  groupOf,
  hasItem,
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
// 🎬 Make Video — the auto-editor. Everything it can do lives in `agent/`, and
// none of it touches state directly: see `agent/actions.js`.
import useDirectorRun from "../animatic/agent/useDirectorRun.js";
import { shotRow } from "../animatic/agent/veo_pass.js";
import { ACTION_API } from "../animatic/agent/actions.js";
// ⚠ `sortFiles` ONLY. The strip itself was the Media pane's list of CLIPS; the
// pane lists the LIBRARY now (`MediaBin`), so the component has no reader here —
// its file-name sort still does, on every upload path.
import { sortFiles } from "./FrameStrip.jsx";
import { UNTITLED, isUntitled } from "./AnimaticLibrary.jsx";
import Timeline, { formatTime } from "./Timeline.jsx";
import Icon from "./Icon.jsx";
// The account dropdown, shared with the sidebar — see AccountMenu.jsx for why
// it is one component and not two lists that agree until somebody edits one.
import AccountMenu, { useMenuDismiss } from "./AccountMenu.jsx";
import PaneSplitter from "./PaneSplitter.jsx";
import MediaBin from "./MediaBin.jsx";
import DirectorPanel from "./DirectorPanel.jsx";
import ProgramCanvas from "./ProgramCanvas.jsx";
import ShapeGallery, {
  DEFAULT_SHAPE_COLOR,
  SHAPE_KINDS,
  ShapeSwatch,
  shapeLabel,
} from "./Shapes.jsx";
import EffectsPanel from "./EffectsPanel.jsx";
import EffectsLibrary from "./EffectsLibrary.jsx";
import { FX_ITEM_COUNT, fxEntry } from "../animatic/fx_library.js";
import { MAX_EFFECTS } from "../animatic/gl/shaders/layer.js";
import RegeneratePanelInline, { RelengthShotInline } from "./RegeneratePanelInline.jsx";
// The board's own dialogue block, reused verbatim in the ✨ Animate dialog so a
// shot's spoken lines look the same here as they do on the storyboard.
import DialogueBox from "./DialogueBox.jsx";
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
import { ScrubGesture, InfoDot, PropGroup, PropRow, openGroup } from "./properties/PropGroup.jsx";
// THE MEDIA LIBRARY — what has been added to this animatic, as opposed to where
// it plays. See the note at the top of `animatic/assets.js` for why the pane and
// the timeline are two lists now.
import {
  assetFromAudio,
  assetFromFrame,
  assetFromOverlay,
  assetKey,
  assetOrigin,
  assetUrl,
  clipFromAsset,
  libraryFromProject,
  mergeAssets,
} from "../animatic/assets.js";

// The timeline's scale, in pixels per second. CONTINUOUS, not a list of steps:
// the scroll bar's grips ask for whatever scale frames the stretch you dragged
// them around, and rounding that to the nearest power of two would make the
// gesture lie about what it was going to show you. The ＋/− buttons and the
// Zoom tool still move in steps — `ZOOM_STEP` — which is what a click wants.
// The ⚙ menu and its trigger, for the outside-press close. ⚠ THE GEAR IS IN IT:
// closing on the button's own `pointerdown` would let the `click` that follows
// reopen what the press just shut, which reads as a dead button.
const MENU_DISMISS = ".an-settings-menu, .an-settings-btn";

const MIN_PPS = 2;
const MAX_PPS = 600;
const DEFAULT_PPS = 32;
const ZOOM_STEP = 1.6;
const MIN_MS = 100;
// Mirrors API_MAX_ANIMATIC_AUDIO_TRACKS on the server.
const MAX_AUDIO_TRACKS = 4;
// `AnimaticFrame.track` is `le=15`, so a sixteenth row is a value the server
// would reject. Checked where a row is ADDED, so the refusal is a notice rather
// than a failed save.
const MAX_PICTURE_TRACK = 15;

// WHAT EACH KIND OF PICTURE ROW IS CALLED, and what it will accept. The kinds
// themselves are `ROW_KINDS` in `scene.js`, next to `clipRowKind` — so the
// timeline, the editor and the exporter cannot disagree about which row a clip
// belongs on.
//
// ⚠ THE THREE ARE STRICT: a clip may only be dragged, dropped or imported onto a
// row of its own kind. Asked for directly — "i only move each same layer clip
// like image move in only image layer and video move video any layer".
//
// ⚠ AND THERE IS NO `stills` KIND ANY MORE — an uploaded picture goes to the
// overlay "Images" lane, never onto a row in the cut. See `ROW_KINDS` and
// `belongsOnImageLane` in scene.js for the whole of that decision; a legacy
// `stills` record is read as a plain video row (`rowKindOrLegacy`), which is
// what its clips already play as.
//
// ⚠ NEITHER BOARD ROW TAKES FILES (`takes: []`), and that is the point of them
// being separate. They are filled by the storyboard import and by ✨ Animate;
// putting an upload on the row your board panels live on is exactly the mixing
// the strict rows exist to stop. Their ＋ opens the thing that DOES fill them.
// ⚠ `takes` COMES FROM `ROW_TAKES` IN scene.js and is not written out again
// here. The timeline reads the same table to decide whether to light a row up as
// a drop target, and two copies of "what may land here" would drift into a row
// that accepts your file and then refuses it.
// ⚠ TWO NAMES PER KIND, AND THE SHORT ONE IS WHAT THE GUTTER SHOWS. The label
// column is a fixed width shared with four controls, so "Storyboard images" could
// only ever arrive there as "Storybo…" — two rows truncating to the same eight
// characters, which is the one thing a row label must not do. Asked for by name:
// "you change name Storyborad video to Story..Video and Storyborad Image to
// Story..Image". `name` stays the full phrase for PROSE — a notice saying which
// row something belongs on has room for it.
const ROW_KIND = {
  board_image: {
    name: "Storyboard images",
    short: "Story..Image",
    takes: ROW_TAKES.board_image,
    hint: "Panels imported from a storyboard — in the cut, one shot each",
    add: "Import a storyboard onto this row",
  },
  board_video: {
    name: "Storyboard video",
    short: "Story..Video",
    takes: ROW_TAKES.board_video,
    hint: "Veo renders of your panels — each drawn OVER the panel it came from",
    add: "Animate a panel with ✨ to fill this row",
  },
  video: {
    name: "Video",
    short: "Video",
    takes: ROW_TAKES.video,
    hint: "Footage and full-frame stills in the cut, each placed on its own",
    add: "Add video or full-frame images to the end of this row",
  },
};

// A picture row's name: its kind's own SHORT word, numbered from 2 for the second
// of that kind — the same rule `addLayer` follows for Text / Shapes / Audio, and
// for the same reason (the first one on screen is just "Video").
const rowKindName = (kind, nth = 0) =>
  `${ROW_KIND[kind]?.short || ROW_KIND.video.short}${nth > 0 ? ` ${nth + 1}` : ""}`;

// Is this file one of the kinds that row accepts? `kindOf` answers "image" /
// "video" / "audio" / "other"; a row's `takes` is a list of the same words.
const rowTakesFile = (rowKind, fileKind) =>
  (ROW_KIND[rowKind]?.takes || []).includes(fileKind);

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
// ⚠ THE LIBRARY FETCHES ITS OWN, SMALLER PICTURES, and that is on purpose. A
// library card is a ~100px tile, and a card usually points at the same file as a
// clip already on the timeline — so asking for the monitor's 960px proxy would
// hold two copies of every panel in memory to draw one of them at a tenth of the
// size. The proxies are cached per width on disk, so the second width costs one
// resize per file and nothing after that. See `proxies.py`.
const LIBRARY_MAX_EDGE = 240;

// The frame shapes and their pixel sizes moved to `animatic/aspects.js` — the
// Shape chips in Video properties, the Program pane's picker and the export
// dialog's size table all read the one list now.

// HOW LONG A GENERATED IN-BETWEEN SHOT HOLDS, and what else may be picked.
// The same ladder the key-pose re-block offers (`panel_sequence.ALLOWED_DURATIONS`),
// so "how long is this shot" is asked the same way wherever it is asked.
//
// ⚠ EIGHT IS THE DEFAULT BECAUSE THE PICTURE IS GOING STRAIGHT ONTO A TIMELINE
// and the next thing that usually happens to it is ✨ Animate, whose longest
// take is 8 seconds. Opening on the board's 2-second hold would mean re-timing
// the clip by hand after every render.
const SHOT_GEN_SECONDS = [2, 4, 6, 8, 10];
const SHOT_GEN_DEFAULT_SECONDS = 8;

// The same ladder for a picture generated into the Images layer — and a
// DIFFERENT default, which is deliberate. A generated SHOT opens at 8s because
// the next thing that happens to it is usually ✨ Animate, whose longest take is
// 8s. This one is an overlay: it goes on the lane where every picture anyone has
// ever dropped in arrives at 2 seconds (`addOverlayFiles`), and matching the
// lane it lands on matters more than matching the other dialog — the first thing
// you compare it to is the clip beside it, not a dialog you closed.
const IMG_GEN_DEFAULT_SECONDS = 2;

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

// Run `work` over `items` with at most `limit` in flight at once.
//
// ⚠ A SLIDING WINDOW, NOT FIXED BATCHES. `Promise.all` over slices of five —
// which is what the thumbnail loader does — makes every request in a batch wait
// for the slowest one before the next batch starts, so a single file that needs
// its proxy generating stalls four idle sockets behind it. Here a worker takes
// the next item the moment its own finishes, so the window stays full.
//
// `work` is expected to swallow its own failures: one file that won't load is a
// tile without a picture, never a rejected run that abandons the rest.
async function runPooled(items, limit, work) {
  let next = 0;
  const worker = async () => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      await work(items[i]);
    }
  };
  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker)
  );
}

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
  // The second half of the type, at the values that reproduce what a caption
  // drew before they existed — see `AnimaticTextClip` for what each one means.
  size_px: 0,
  line_height: 1.28,
  text_case: "none",
  wrap: 0.86,
  backdrop_color: "#000000",
  backdrop_opacity: null,
  backdrop_radius: 0.25,
  backdrop_pad: 1,
  shadow_color: "#000000",
  shadow_opacity: 0.55,
  shadow_angle: 45,
});

// '#rrggbb' + alpha → 'rgba(r, g, b, a)'. Unreadable ink is black, the same
// forgiveness `_parse_colour` gives the exporter.
function rgba(hex, alpha) {
  let s = String(hex || "").trim().replace("#", "");
  if (s.length === 3) s = s.split("").map((c) => c + c).join("");
  const n = /^[0-9a-fA-F]{6}$/.test(s) ? parseInt(s, 16) : 0;
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// CSS `text-transform` for a caption's case. ⚠ The same three mappings
// `_apply_case` implements in Pillow — "title" is `capitalize`, which upper-
// cases first letters and leaves the rest alone.
const CAPTION_TRANSFORM = { upper: "uppercase", lower: "lowercase", title: "capitalize" };

// What a backdrop is worth when the clip hasn't said: a scrim is a bar you read
// through, a box is one you don't. ⚠ 140/255 and 225/255 — the two alphas
// `_draw_text_block` fills with.
const BACKDROP_ALPHA = { scrim: 0.55, box: 0.88 };

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
 *   · the shadow's blur is ZERO because Pillow draws a hard-edged one, and its
 *     default ink is rgba(0,0,0,.55) because Pillow's is alpha 140. A blurred
 *     shadow here would be prettier and would be a preview that lies;
 *   · `size_px` is pixels at 1080p like `stroke_px`, and `backdrop_radius`,
 *     `backdrop_pad` and `wrap` are the same fractions `_draw_text_block`
 *     scales — so they too are one number with no conversion;
 *   · `line_height` is the ONE number that is NOT the same on both sides. CSS
 *     multiplies the FONT SIZE and Pillow multiplies (ascent + descent), so it
 *     goes through `cssLineHeight`, which carries the font's own ratio.
 */
function captionStyle(c, inZone = false) {
  const style = {
    color: c.color || "#ffffff",
    opacity: c.opacity ?? 1,
    fontFamily: fontFamily(c.font),
    // ⚠ NOT `c.line_height` STRAIGHT IN. CSS `line-height` is a multiple of the
    // FONT SIZE; the exporter steps its baselines by `(ascent + descent) ×
    // line_height`, which for Inter is 22% more and for Anton 51% more. The
    // ratio is on the font list — see `cssLineHeight`.
    lineHeight: cssLineHeight(c.font, c.line_height),
  };
  // An explicit size overrides the S/M/L class, and is quoted at 1080p for the
  // same reason `stroke_px` is: `100cqh` is this frame's height, so the caption
  // is the same fraction of it whatever the monitor or the export is.
  if (c.size_px > 0) style.fontSize = `calc(100cqh * ${c.size_px} / 1080)`;
  if (c.text_case && CAPTION_TRANSFORM[c.text_case]) {
    style.textTransform = CAPTION_TRANSFORM[c.text_case];
  }
  // ⚠ THE WRAP WIDTH IS A FRACTION OF THE FRAME ON BOTH SIDES, but the two
  // kinds of caption measure `%` against different boxes: a zone clip sits
  // inside `.an-text-zone`, which is already inset 7% each side (i.e. 86% of
  // the frame — `_TEXT_WIDTH`), while a free one is a child of the full-frame
  // layer. Divide by that inset for the first and the fraction of the FRAME
  // comes out the same, which is what `max_width = width * wrap` gives Pillow.
  const wrap = c.wrap ?? 0.86;
  if (wrap !== 0.86) style.maxWidth = `${(inZone ? wrap / 0.86 : wrap) * 100}%`;
  if (c.letter_spacing) style.letterSpacing = `${c.letter_spacing}em`;
  // Padding and corners in `em`, which is what the exporter multiplies the font
  // size by — see `pad` in `draw_texts` and `radius` in `_draw_text_block`.
  const padMult = c.backdrop_pad ?? 1;
  if (padMult !== 1) style.padding = `${0.28 * padMult}em ${0.5 * padMult}em`;
  if ((c.backdrop_radius ?? 0.25) !== 0.25) style.borderRadius = `${c.backdrop_radius}em`;
  // The backdrop's own ink. Only for the kinds that HAVE one — "Outline only"
  // and "Just the letters" must stay transparent, or the colour picker would
  // quietly give them a box. `backdropHasFill` is the shared answer, so the
  // preview and `_draw_text_block` cannot disagree about which kinds paint.
  if (backdropHasFill(c)) {
    style.background = rgba(c.backdrop_color || "#000000",
                            c.backdrop_opacity ?? BACKDROP_ALPHA[textBackdrop(c)] ?? 0.55);
  }
  if (c.stroke_px) {
    style.WebkitTextStrokeWidth = `calc(100cqh * ${c.stroke_px} / 1080)`;
    style.WebkitTextStrokeColor = c.stroke_color || "#000000";
    style.paintOrder = "stroke fill";
  }
  if (c.shadow) {
    // ⚠ THE SAME √2 THE EXPORTER USES. `shadow` is the offset it always was —
    // one down and one right — so the DISTANCE is `shadow · √2`, and that is
    // what the angle rotates. At the default 45° this comes back out as
    // `shadow em, shadow em`, which is the picture every old caption cast.
    const dist = c.shadow * Math.SQRT2;
    const rad = ((c.shadow_angle ?? 45) * Math.PI) / 180;
    const dx = (dist * Math.cos(rad)).toFixed(4);
    const dy = (dist * Math.sin(rad)).toFixed(4);
    style.textShadow = `${dx}em ${dy}em 0 ${rgba(
      c.shadow_color || "#000000",
      c.shadow_opacity ?? 0.55
    )}`;
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
//
// ⚠ EVERY `id` HERE IS ALSO AN ICON NAME IN `Icon.jsx`, because the buttons draw
// icons rather than printing the letters. Add a tool and you add a path there in
// the same breath — `Icon` renders nothing for a name it does not know, so a
// missing one is an invisible button, not an error.
// ⚠ AND `key` IS NOW ONLY VISIBLE IN THE TOOLTIP. It still binds the shortcut
// (the keydown handler matches it against `e.code` as "Key" + the letter); it
// just no longer labels the button, so the title is the only thing teaching it.
const TOOLS = [
  {
    id: "select",
    key: "V",
    label: "Selection",
    hint:
      "Select and move clips · drag the empty part of a lane to select several · " +
      "shift-click to add one · alt-drag to duplicate · " +
      "double-click a lane's name for the whole row",
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
  // ⚠ NO ACCOUNT PROPS. The ⚙ menu carried Your account / Pricing / Help / Log
  // out for a day and they were taken back out — that is the SIDEBAR's menu's
  // job. This one is project settings, so it needs nothing from the shell.
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
  // asset id → object URL of its LIBRARY thumbnail, and the path each came from.
  // Keyed by ASSET id rather than clip id because a card outlives its clips —
  // see the fetch effect for why it cannot share `urls`.
  const [assetUrls, setAssetUrls] = useState({});
  const assetUrlsRef = useRef({});
  const assetSrcRef = useRef({});
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
  // The ⚙ dropdown in the top bar (account, plan, help, delete, log out).
  const [settingsMenu, setSettingsMenu] = useState(false);
  // Stable, because `useMenuDismiss` lists it in its deps — a fresh function
  // every render would tear the listeners down and put them back on each one.
  const closeSettingsMenu = useCallback(() => setSettingsMenu(false), []);
  // The "name this animatic" panel. Null = closed; a string is the typed name.
  const [saveAsName, setSaveAsName] = useState(null);
  // A file is being dragged over the Media pane.
  const [dropping, setDropping] = useState(false);
  // The "what kind of layer?" picker opened by ＋ Add layer.
  const [layerMenu, setLayerMenu] = useState(false);
  // --- Importing a storyboard onto a row of its own ---
  // Null = closed. Otherwise `{ track }`, where a null track means "make the row
  // as part of the import" — which is what ＋ Add layer does, and what "when i
  // import storyboard so that time create automatic new layer" asks for.
  const [boardImport, setBoardImport] = useState(null);
  // The user's boards, fetched when the picker opens. Null = not fetched yet, so
  // "still loading" and "you have none" are different things on screen.
  const [boardList, setBoardList] = useState(null);
  const [boardPick, setBoardPick] = useState("");
  const [boardBusy, setBoardBusy] = useState(false);
  // ⚠ ITS OWN ERROR, inside the panel. The editor's banner renders in the status
  // bar BEHIND the modal overlay, so a failed import would write its reason
  // somewhere the user cannot see and the button would look dead. Same lesson as
  // `speechError` and `reframeError`.
  const [boardError, setBoardError] = useState("");
  // ⚠ THERE IS NO "EXTRA PICTURE TRACKS" COUNTER ANY MORE, and its removal is
  // the whole of this fix. It was VIEW state — "the user asked for a row to drop
  // onto" — on the reasoning that a picture track is a NUMBER on a clip and an
  // empty row is therefore a layer the document does not have. Which is true, and
  // was the bug: an added row you had not filled yet vanished the moment you left
  // the editor, reported as "when i see again my video picker layer not show". A
  // picture track is a RECORD in `layers` now (`kind: "video"`, carrying its track
  // number), so an empty row is part of the document, is saved with it, and its ✕
  // removes it like any other layer's. See `videoTracks`.
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
  // The BOARD PANEL behind the shot being animated — its wording and its spoken
  // lines. Null while the (free) read is in flight, and for a shot that is not a
  // board panel at all; the dialog simply shows no board block in either case.
  const [animatePanel, setAnimatePanel] = useState(null);
  // Are the board's spoken lines currently appended to the prompt? ⚠ NOT A
  // SEPARATE FIELD THAT GETS SENT — ticking it writes the lines INTO the prompt
  // box, because the whole point of showing the prompt is that what goes to Veo
  // is what is on screen. Anything added invisibly would be the opposite.
  const [animateSpeak, setAnimateSpeak] = useState(false);
  // Which frame the in-flight panel read was started for, so a second ✨ Animate
  // opened before the first answers cannot fill the box with the wrong shot's
  // wording. Compared on arrival and nothing else.
  const animatePanelReq = useRef(null);
  // The exact block `animateSpeak` appended, so unticking can take back what it
  // put in — and only that. If the user has since edited the lines the block no
  // longer matches, and the text stays: their edit outranks the checkbox.
  const animateSpokenRef = useRef("");
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
  // The "generate the shot before / after this one" dialog. Null = closed;
  // otherwise `{frameId, side}` — WHICH clip it is beside and WHICH side of it,
  // because the dialog is one component and the side is the only difference
  // between the two menu lines that open it.
  const [shotGen, setShotGen] = useState(null);
  // The free context read: the name to give the shot, the two shots it goes
  // between, the board's aspect and which model is about to draw. Null until it
  // lands — the dialog opens immediately and fills in.
  const [shotGenCtx, setShotGenCtx] = useState(null);
  const [shotGenPrompt, setShotGenPrompt] = useState("");
  const [shotGenAspect, setShotGenAspect] = useState("");
  const [shotGenSeconds, setShotGenSeconds] = useState(SHOT_GEN_DEFAULT_SECONDS);
  // Drawing (the image call) and suggesting (the text call) are two different
  // waits with two different buttons, so they are two flags — one would grey
  // out the ✨ while the picture renders and read as the ✨ having failed.
  const [shotGenBusy, setShotGenBusy] = useState(false);
  const [shotGenAsking, setShotGenAsking] = useState(false);
  // Is the ✨'s ⓘ open? Folded by default, like every other explanation in this
  // editor — see `InfoDot`. Standing prose about what a control does costs its
  // three lines on every visit for something read once.
  const [shotGenNote, setShotGenNote] = useState(false);

  // The MEDIA PANE's ✨ — "draw me any picture". Null = closed.
  //
  // ⚠ A SEPARATE DIALOG FROM THE SHOT ONE, AND SEPARATE STATE, though they share
  // a card and half their rows. They are two different questions: that one draws
  // a SHOT — the board's look, its references, the gap between two named
  // neighbours, and it lands on the board's row — while this one draws whatever
  // the sentence says, belongs to no storyboard, and lands on the overlay Images
  // lane like every other picture that is not a shot (`belongsOnImageLane`).
  // Folding them into one component would mean a prop for every one of those
  // differences and a prompt that has to decide which of two riders to send.
  const [imgGen, setImgGen] = useState(null);
  const [imgGenPrompt, setImgGenPrompt] = useState("");
  const [imgGenAspect, setImgGenAspect] = useState("");
  const [imgGenSeconds, setImgGenSeconds] = useState(IMG_GEN_DEFAULT_SECONDS);
  const [imgGenBusy, setImgGenBusy] = useState(false);
  const [imgGenNote, setImgGenNote] = useState(false);
  // Which model will draw, read once and cached for the session. ⚠ It does not
  // depend on the project — it is `IMAGE_PROVIDER` in the environment — so it is
  // read on first open and never again.
  const [imageModel, setImageModel] = useState(null);
  // WHICH TAB THE MEDIA ✨ IS ON — "image" | "video". One dialog, two things it
  // can make, asked for as "add two tab like fuction first AI Image and secon AI
  // Video … so user choose easily in one place". They share the card, the prompt
  // box and the Model row and nothing else: an image is one synchronous call
  // that costs a fraction of a cent, and a video is Veo — minutes long, billed
  // per second, and therefore priced and confirmed before anything runs.
  const [imgGenTab, setImgGenTab] = useState("image");
  // --- the Video tab ------------------------------------------------------
  const [vidGenPrompt, setVidGenPrompt] = useState("");
  // The still it starts FROM, uploaded the moment it is chosen so the render
  // request only has to carry an id. Null = text-to-video.
  const [vidGenSource, setVidGenSource] = useState(null);
  const [vidGenUploading, setVidGenUploading] = useState(false);
  const [vidGenRender, setVidGenRender] = useState({
    tier: "fast",
    resolution: "720p",
    duration_seconds: 8,
    // Off by default, exactly as ✨ Animate's is: sound costs more per second,
    // and an animatic usually already carries its own.
    generate_audio: false,
  });
  // The priced confirm. ⚠ NOTHING SPENDS UNTIL THIS HAS BEEN ON SCREEN — the
  // rule every paid path in this editor follows.
  const [vidGenConfirm, setVidGenConfirm] = useState(null);
  const [vidGenBusy, setVidGenBusy] = useState(false);
  const vidGenInputRef = useRef(null);
  // ⚠ THE TWO PROMPT BOXES BOTH EXIST AT ONCE NOW (see `.an-gen-pane`), so
  // neither may carry `autoFocus`: two of them in one tree means the LAST one
  // mounted wins, which is the tab you are not looking at. Focus is put on the
  // live one by hand instead, which also means switching tabs lands the caret in
  // the box you just switched to.
  const imgGenBoxRef = useRef(null);
  const vidGenBoxRef = useRef(null);
  // Which (frame, side) the in-flight context read was started for. ⚠ Same
  // guard as `animatePanelReq`: open a second menu while the first read is in
  // the air and the late answer would describe the wrong gap.
  const shotGenReq = useRef(null);
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
  const layersRef = useRef([]);
  // The picture row and the audio as they stood when a voiceover run was
  // submitted. That pass moves shots on the SERVER, so this is the only record of
  // where they were — and without it there is nothing to measure the shift
  // against when the run comes back. See `doSpeech`.
  const speechFramesRef = useRef([]);
  const speechAudioRef = useRef([]);

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
  // --- THE DIALOGUE SHEET (voiceover only) ---------------------------------
  // ⚠ WHAT IS ABOUT TO BE SAID, ON SCREEN, BEFORE IT IS PAID FOR. The dialog
  // used to offer a voice and a price and nothing else: what would actually be
  // read was whatever the board happened to hold, unseen. Asked for as "i want i
  // see my Storyborad Dialouge in here … so user look if user want chnage so
  // user change/edit Dialouge", which is the same second look ✨ Animate gives
  // you at its prompt.
  //
  // `speechSheet` is the free GET's answer — the pickers and whether these clips
  // came off a board at all. `speechLines` is the EDITABLE copy, and it is what
  // both the estimate and the run send: the price has to be the price of the
  // words on screen.
  const [speechSheet, setSpeechSheet] = useState(null);
  const [speechLines, setSpeechLines] = useState([]);
  const [speechSheetBusy, setSpeechSheetBusy] = useState(false);
  // Stretch each shot to cover its own line and push the shots after it along —
  // the same ripple animating a shot performs. On by default: a 2-second picture
  // under a 10-second line was the reported bug, not a preference.
  const [speechFit, setSpeechFit] = useState(true);
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
  // ⚠ THE SAME PASS, RUN BY THE DIRECTOR (phase B) — a SEPARATE flag, and not to
  // be merged with the one above. `speechRunning` is what the 🎙 dialog's poll
  // effect keys on; setting it from the Director would start that poll as well
  // and the two would race to re-read the same finished job. What the two DO
  // share is `serverBusy`: for the life of either run the server is the only
  // writer of this project, and the autosave has to stand down.
  const [directorSpeaking, setDirectorSpeaking] = useState(false);
  // ⚠ AND ITS OWN FLAG FOR PHASE C, for exactly the reason `directorSpeaking`
  // has one: `POST /animatics/{id}/animate` puts the job into RUNNING and
  // `save_animatic` refuses to write through that, so an autosave landing
  // mid-pass is a 409 on screen. For the life of a render the server is the only
  // writer to this project — see `serverBusy`.
  const [directorRendering, setDirectorRendering] = useState(false);

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
  // A VIDEO ROW's ＋. Takes footage AND stills, because that is what the row
  // holds — see the input itself for what naming it after one of the two cost.
  const pictureInputRef = useRef(null);
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
  const serverBusy =
    exporting || animating || speechRunning || directorSpeaking || directorRendering ||
    reframeRunning;

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
    assets, setAssets,
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
    directorRun, setDirectorRun,
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
  // The same numbers keyed by CLIP ID — what a selection reads, since a
  // selection carries ids and not positions.
  //
  // ⚠ NOT `frame.start_ms`, and that is the whole reason this exists. A clip
  // saved before tracks has no `start_ms` of its own and begins where the one
  // before it on its track ended (see `frameSpans`), so reading the field
  // directly answers `undefined` for exactly the clips a group move must not
  // drop. `spans` is parallel to `frames`, so index i is frame i.
  const frameStartById = useMemo(() => {
    const by = new Map();
    frames.forEach((f, i) => by.set(f.id, pictureSpans.spans[i]?.start ?? 0));
    return by;
  }, [frames, pictureSpans]);

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

  // The token for one lane. ⚠ THE ENCODING ITSELF LIVES IN `lane_order.js` now,
  // because THREE lists speak it (`hidden_lanes`, `locked_lanes`, `lane_order`)
  // and the last of those is read by the Python exporter too — a second spelling
  // on this side would be a row the export ranked differently from the monitor.
  const laneToken = (lane) => laneTokenFor(lane.kind, lane.layerId, lane.track);

  /**
   * The token for a LAYER RECORD, which is a different shape from a lane — its
   * id is the lane's `layerId`, and a picture row carries `track`.
   */
  const layerTokenOf = (layer) =>
    laneTokenFor(layer.kind, layer.id, layer.track);

  /**
   * A ROW HAS JUST BEEN CREATED — put it where its kind lives in the saved order.
   *
   * ⚠ IT IS A NO-OP ON A PROJECT NOBODY HAS RESTACKED, and that is deliberate:
   * an empty `lane_order` means "the derived order", and writing one token into
   * it would promote that row above every other. See `seatLane`.
   */
  const seatNewLane = (token) => {
    if (!token) return;
    setSettings((sett) => {
      const next = seatLane(sett.lane_order, token);
      return next === sett.lane_order ? sett : { ...sett, lane_order: next };
    });
  };

  /**
   * ⚠ THE SAME FUNCTION, REACHABLE FROM A `useCallback([])` BODY. Two of the row
   * makers (the Veo row, the dropped-footage row) are memoised on an empty
   * dependency list — they use `layersRef` for the same reason — so calling
   * `seatNewLane` directly from them would capture the FIRST render's copy and
   * write settings that are several edits stale.
   */
  const seatNewLaneRef = useRef(null);
  seatNewLaneRef.current = seatNewLane;

  /** …and a row has just been deleted. See `unseatLane` for why this matters. */
  const unseatOldLane = (token) => {
    if (!token) return;
    setSettings((sett) => {
      const next = unseatLane(sett.lane_order, token);
      return next === sett.lane_order ? sett : { ...sett, lane_order: next };
    });
  };

  // ⚠ THE SAME TOKENS AS `hiddenLanes`, A DIFFERENT MEANING. Hidden takes a row
  // out of the VIDEO; locked takes it out of REACH and changes nothing about the
  // film. Two lists rather than one flag with two bits, because a row can be
  // either, both, or neither, and because the exporter reads one and must never
  // read the other — see `locked_lanes` in server/schemas.py.
  const lockedLanes = useMemo(
    () => new Set(settings.locked_lanes || []),
    [settings.locked_lanes]
  );

  const toggleLaneLocked = (lane) => {
    const token = laneToken(lane);
    if (!token) return;
    const wasLocked = lockedLanes.has(token);
    setSettings((sett) => {
      const now = new Set(sett.locked_lanes || []);
      if (now.has(token)) now.delete(token);
      else now.add(token);
      return { ...sett, locked_lanes: [...now] };
    });
    setNotice(
      wasLocked
        ? `${lane.name} can be edited again.`
        : `${lane.name} is locked — it still plays and still exports, but nothing on it can be moved, trimmed or deleted.`
    );
  };

  /**
   * IS THIS ROW LOCKED? — asked by every edit, from the token alone.
   *
   * ⚠ TAKES A LANE-SHAPED THING, NOT A LANE. A drag knows a lane; the razor and
   * a clip delete know only a clip's track or its layer id, and rebuilding a
   * whole lane object to ask one question is how the answer comes to differ by
   * caller. So the callers build the same two-field shape `laneToken` needs.
   */
  const laneIsLocked = useCallback(
    (lane) => {
      const token = laneToken(lane);
      return !!token && lockedLanes.has(token);
    },
    [lockedLanes]
  );

  /** Is the row this PICTURE CLIP sits on locked? */
  const frameLocked = useCallback(
    (frame) => laneIsLocked({ kind: "frames", track: frameTrack(frame) }),
    [laneIsLocked]
  );

  /**
   * THE RECORD BEHIND ONE SELECTION ITEM — `{ kind, id }` back to the clip.
   *
   * A selection carries ids and nothing else (see `animatic/selection.js`), which
   * is right for a list that spans five lanes — but every question worth asking
   * about a selected clip ("how long is it", "which row is it on") is a question
   * about the RECORD. This is the one lookup, so there is one place that knows
   * `audio` is keyed by CLIP id rather than by upload.
   */
  const clipOfItem = useCallback(
    (item) => {
      if (!item) return null;
      if (item.kind === "frame") return frames.find((f) => f.id === item.id) || null;
      if (item.kind === "audio") {
        return audioTracks.find((a) => clipId(a) === item.id) || null;
      }
      return (groupPools[item.kind] || []).find((c) => c.id === item.id) || null;
    },
    [frames, audioTracks, groupPools]
  );

  /**
   * IS THE ROW THIS SELECTED ITEM SITS ON LOCKED? — for the KEYBOARD paths.
   *
   * ⚠ THE MOUSE IS ALREADY HANDLED, IN THE TIMELINE. Every press on a clip goes
   * through `startClipDrag`, which refuses a locked lane before it does anything
   * else — so on a locked row a click does not select, a drag does not move and
   * the razor does not cut. What that cannot see is Delete and Ctrl+K, which act
   * on a selection that may predate the lock, or that a double-click on the
   * gutter made. This is the guard for those.
   *
   * ⚠ IT TAKES A SELECTION ITEM AND LOOKS THE CLIP UP ITSELF, which it did not
   * used to: it destructured a `clip` off the item, and every caller passes
   * `{ kind, id }` — so `clip` was always `undefined` and every question came out
   * as "is track 0 locked" for a picture and "is the layer called '' locked" for a
   * caption. `deleteMany`'s guard was therefore reading a row nobody had clicked.
   * Resolving here rather than at the two call sites is what stops that being
   * true again the next time one is added.
   *
   * ⚠ AUDIO IS ALWAYS UNLOCKED, and honestly so: `laneToken` gives an audio row
   * no token, so there is nothing to store a lock against — a loose audio row is
   * keyed by the FILE it holds, which changes as clips are dragged in and out.
   * The padlock is disabled on those rows for the same reason.
   */
  const itemLocked = useCallback(
    (item) => {
      if (!item) return false;
      const kind = item.kind;
      const clip = clipOfItem(item);
      if (!clip) return false;
      if (kind === "frame") return frameLocked(clip);
      const laneKind = kind === "overlay" ? "image" : kind;
      if (laneKind !== "text" && laneKind !== "shape") return false;
      return laneIsLocked({ kind: laneKind, layerId: clip.layer_id || "" });
    },
    [clipOfItem, frameLocked, laneIsLocked]
  );

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
    // ⚠ `settings` IS IN HERE FOR ONE FIELD, AND IT IS NOT OPTIONAL: `lane_order`
    // is what `sceneAt` ranks the stack by. This object used to carry the five
    // clip lists and nothing else — harmless while the draw order was hard-coded
    // in the renderers, and silently wrong the moment it wasn't: the monitor drew
    // the DEFAULT stack while the timeline, the saved project and the export all
    // used the dragged one. A preview that disagrees with the file is the one
    // failure this editor must never ship, and it looked exactly like "the drag
    // does nothing".
    // ⚠ ONLY THE ORDER, not the whole of `settings` — this memo must not rebuild
    // on every fps or aspect-ratio change, and nothing else in the scene model
    // reads a setting.
    const doc = {
      frames, texts, shapes, overlays, transitions,
      settings: { lane_order: settings.lane_order || [] },
    };
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
  }, [
    frames, texts, shapes, overlays, transitions, hiddenLanes,
    settings.background, settings.lane_order,
  ]);

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

  // --- Full screen ---------------------------------------------------------
  // The Program pane, blown up to the whole display, the way a video player
  // does it. It is the MONITOR'S BODY that goes full screen — picture plus
  // transport — not the picture alone: a preview you cannot pause or scrub is
  // a screensaver, and not the whole pane either, because a pane head with an
  // aspect-ratio menu on it is furniture at 2 metres.
  //
  // ⚠ THE FLAG IS DRIVEN BY THE EVENT, NEVER BY THE CLICK. Escape, F11 and the
  // browser's own chrome all leave full screen without telling us, so a boolean
  // flipped in the handler would have left the button drawing "exit" over a
  // window that had already come back — and pressing it would then have done
  // nothing, because there is nothing to exit. `fullscreenchange` is the only
  // thing that knows, so it is the only thing that writes.
  //
  // ⚠ AND IT LIVES UP HERE, WITH THE MONITOR'S OTHER STATE, BECAUSE THIS
  // COMPONENT RETURNS EARLY. Below the view marker there is `if (loading)` and
  // `if (error && !frames.length)`, so a hook declared next to the Program pane's
  // JSX — which is where these three naturally belong — does not run on the
  // first render and does on the second. React counts hooks: that is "Rendered
  // more hooks than during the previous render", and it takes the whole editor
  // down to a black page the moment a project finishes opening. It was written
  // there first and did exactly that.
  const programBodyRef = useRef(null);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    const sync = () =>
      setFullscreen(
        (document.fullscreenElement || document.webkitFullscreenElement) ===
          programBodyRef.current
      );
    sync();
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      document.removeEventListener("webkitfullscreenchange", sync);
    };
  }, []);

  // ⚠ `.catch` IS NOT OPTIONAL. `requestFullscreen` rejects — an iframe without
  // `allowfullscreen`, a browser that refuses outside a user gesture — and an
  // unhandled rejection in a click handler is an error in the console and a
  // button that looks broken with no reason given.
  const toggleFullscreen = useCallback(() => {
    const el = programBodyRef.current;
    if (!el) return;
    const current = document.fullscreenElement || document.webkitFullscreenElement;
    if (current) {
      (document.exitFullscreen || document.webkitExitFullscreen)?.call(document);
      return;
    }
    const request = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!request) return;
    Promise.resolve(request.call(el)).catch((e) =>
      console.warn("[monitor] full screen was refused.", e)
    );
  }, []);

  // ⚠ NO `activeTexts` HERE ANY MORE. The captions used to be read off the scene
  // and drawn as one block at the top of the monitor; the monitor now asks for
  // one BAND of them at a time (`renderCaptions`), because a text row can be
  // under a picture row. These two are still whole-scene lists: they are the
  // HANDLES, which are chrome and always sit over the picture.
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
      // ⚠ FOLDED, not read raw. A backdrop kind this build doesn't know has to
      // become the same thing here as in the exporter — see `textBackdrop`.
      `bd-${textBackdrop(c)}`,
      `al-${c.align || "center"}`,
      selectedTextId === c.id ? "sel" : "",
    ].join(" ");

  /**
   * THE CAPTIONS OF ONE BAND OF THE STACK, as DOM.
   *
   * Handed to <ProgramCanvas> as `renderTexts` and called once per run of text
   * rows: for a project nobody has restacked that is once, over the whole
   * picture, which is exactly the single `.an-text-layer` this used to be. Put a
   * picture row above a text row and it is called twice, and the monitor puts a
   * canvas between the two calls.
   *
   * ⚠ THE ZONES ARE PER BAND, AND THAT IS A REAL CONSEQUENCE OF THE FEATURE, not
   * an oversight. Captions sharing a zone STACK down it (`draw_texts` measures
   * them together), and only captions drawn in the same pass can be stacked
   * against each other — so two text rows with a picture row BETWEEN them can
   * overlap where before they could not. They are on either side of a picture at
   * that point, which is what the user asked the stack to mean. Rows that are
   * still neighbours still stack, because they are still one band.
   *
   * Sized in `cqh` (a fraction of the screen box's own height) using the SAME
   * divisors the exporter uses, so the preview and the MP4 agree by construction
   * rather than by two numbers kept in step by hand.
   */
  const renderCaptions = (clips) => (
    <>
      {["top", "middle", "bottom"].map((zone) => {
        const zoneClips = clips.filter(
          (c) => (c.place || "flow") !== "free" && (c.position || "bottom") === zone
        );
        if (!zoneClips.length) return null;
        return (
          <div key={zone} className={`an-text-zone an-text-${zone}`}>
            {zoneClips.map((c) => (
              <span key={c.id} className={captionClass(c)} style={captionStyle(c, true)}>
                {c.text}
              </span>
            ))}
          </div>
        );
      })}
      {/* Free-placed captions sit at their own x/y rather than in a zone — the
          same fractions `draw_texts` centres the block on in the exported frame,
          so dragging one here puts it there in the MP4 at any resolution. */}
      {clips
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
    </>
  );

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
    isUntitled(title);
  // Has content but still carries the placeholder name, so Save should ask for
  // a real one first.
  const needsName = isUntitled(title);

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

  // One small blob per LIBRARY CARD.
  //
  // ⚠ A SECOND CACHE, NOT A SHARE OF `urls`, and the reason is that `urls` is
  // keyed by CLIP id — a card whose clips have all been deleted has no clip id to
  // look under, which is precisely the state the library exists to represent. So
  // it is keyed by ASSET id and fetched at `LIBRARY_MAX_EDGE`, a tenth of the
  // area, which is also what stops the second copy costing anything much.
  //
  // ⚠ AND IT RE-FETCHES WHEN THE PATH MOVES, exactly as the frame cache does
  // (`urlSrcRef`): a board panel keeps its identity through a redraw and only the
  // server's `?v=` changes, so caching on "have I got one?" alone is how a
  // redrawn panel goes on showing the old drawing for ever.
  useEffect(() => {
    let alive = true;
    const wanted = new Map();
    for (const asset of assets) {
      const path = asset.url || assetUrl(animaticId, asset);
      if (path) wanted.set(asset.id, path);
    }
    for (const id of Object.keys(assetUrlsRef.current)) {
      if (!wanted.has(id)) {
        URL.revokeObjectURL(assetUrlsRef.current[id]);
        delete assetUrlsRef.current[id];
        delete assetSrcRef.current[id];
      }
    }
    const missing = [...wanted].filter(
      ([id, path]) => !assetUrlsRef.current[id] || assetSrcRef.current[id] !== path
    );
    if (!missing.length) {
      setAssetUrls({ ...assetUrlsRef.current });
      return undefined;
    }
    (async () => {
      for (let i = 0; i < missing.length; i += 5) {
        if (!alive) return;
        await Promise.all(
          missing.slice(i, i + 5).map(async ([id, path]) => {
            try {
              const url = await api.fetchAnimaticMedia(path, LIBRARY_MAX_EDGE);
              if (!alive) {
                URL.revokeObjectURL(url);
                return;
              }
              const stale = assetUrlsRef.current[id];
              assetUrlsRef.current[id] = url;
              assetSrcRef.current[id] = path;
              if (stale) retireBlob(stale);
            } catch {
              /* a card with no picture shows an empty tile, not an error banner */
            }
          })
        );
        if (alive) setAssetUrls({ ...assetUrlsRef.current });
      }
    })();
    return () => {
      alive = false;
    };
  }, [assets, animaticId]);

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
      // Five at a time — overlays are logos and title cards, the smallest files
      // on the project, and fetching them one after another made a board full of
      // them the slowest thing left on the open.
      await runPooled(missing, 5, async (uploadId) => {
        if (!alive) return;
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
      });
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
      // TWO at a time. These are the biggest files in the project, so five
      // parallel 100MB fetches is a worse first impression than a slower one —
      // but strictly one at a time meant three clips downloaded back to back
      // before the last one could be scrubbed, and the link sat half idle the
      // whole time. Two keeps the pipe busy without the editor spending its
      // entire budget on video while the thumbnails are still arriving.
      await runPooled(missing, 2, async (uploadId) => {
        if (!alive) return;
        try {
          const url = await api.fetchAnimaticMedia(
            `/animatics/${animaticId}/media/${uploadId}`
          );
          if (!alive) {
            URL.revokeObjectURL(url);
            return;
          }
          // Committed as each one lands, not at the end: a clip is playable the
          // moment its own bytes are here, whatever the others are doing.
          videoUrlsRef.current[uploadId] = url;
          setVideoUrls({ ...videoUrlsRef.current });
        } catch {
          /* a clip that won't load shows its thumbnail, not an error banner */
        }
      });
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
      // Three at a time — the cap is MAX_AUDIO_TRACKS, and audio files are a
      // fraction of a video's size, so this is very nearly "all of them" while
      // still not competing with the clips for the whole link.
      await runPooled(missing, 3, async (track) => {
        if (!alive) return;
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
      });
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
      for (const url of Object.values(assetUrlsRef.current)) URL.revokeObjectURL(url);
      assetUrlsRef.current = {};
      assetSrcRef.current = {};
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

  // ----------------------------------------------------- the media library
  /**
   * THE LIBRARY, GROUPED THE WAY THE MEDIA PANE SHOWS IT.
   *
   * ⚠ GROUPED BY ORIGIN, NOT BY KIND — `assetOrigin`, the same rule `frameOrigin`
   * uses on clips, and for the same reason: a board shot that has been through Veo
   * is a video FILE now and must stay in Storyboard Frames where the user left it,
   * rather than moving to Video the moment it is animated.
   */
  const library = useMemo(() => {
    const groups = { board: [], video: [], image: [], audio: [] };
    for (const asset of assets) (groups[assetOrigin(asset)] || groups.image).push(asset);
    return groups;
  }, [assets]);

  /**
   * HOW MANY CLIPS USE EACH LIBRARY CARD, by asset id.
   *
   * ⚠ MATCHED BY SOURCE (`assetKey`), NEVER BY ID. A clip and the card it came
   * from are two rows in two lists with two id spaces — that separation is the
   * whole feature — so the only thing that can link them is the thing behind
   * both: which file, or which panel of which board.
   *
   * It drives the ×N badge and, more importantly, the ✕'s wording: "also deletes
   * 2 clips" has to be said before it happens rather than discovered after.
   *
   * ⚠ ALL THREE PICTURE LISTS, AND `overlays` IS THE ONE THAT WAS MISSING. A
   * picture on an Images lane is an OVERLAY, not a frame, so a card whose only
   * use was there read "–" while the picture was plainly on screen — and the ✕
   * then promised to delete nothing and orphaned it. ⚠ THIS MEMO, `deleteAsset`
   * and `selectAssetClips` ASK THE SAME QUESTION and must be widened together:
   * a badge that counts what the ✕ does not delete is worse than either bug.
   */
  const libraryUse = useMemo(() => {
    const idOfKey = new Map();
    for (const asset of assets) idOfKey.set(assetKey(asset), asset.id);
    const count = new Map();
    const bump = (key) => {
      const id = idOfKey.get(key);
      if (id) count.set(id, (count.get(id) || 0) + 1);
    };
    for (const frame of frames) bump(assetKey(assetFromFrame(frame)));
    for (const overlay of overlays) bump(assetKey(assetFromOverlay(overlay)));
    for (const track of audioTracks) bump(assetKey(assetFromAudio(track)));
    return count;
  }, [assets, frames, overlays, audioTracks]);

  const assetUsedCount = useCallback(
    (asset) => libraryUse.get(asset?.id) || 0,
    [libraryUse]
  );

  /**
   * PUT SOURCES IN THE LIBRARY — called by every path that adds something.
   *
   * ⚠ EVERY ADD PATH HAS TO CALL THIS, and that is the one thing about the library
   * that is easy to get wrong: a source that reaches the timeline without reaching
   * the library is a clip you can delete and never get back, which is the bug this
   * whole feature was reported over. `mergeAssets` dedupes, so calling it twice for
   * the same file is free and calling it once too often is harmless.
   */
  const addToLibrary = useCallback((cards) => {
    setAssets((list) => mergeAssets(list, cards));
  }, [setAssets]);

  // ------------------------------------------------------ the video rows
  /**
   * WHICH PICTURE TRACKS EXIST, highest first — the compositing order, since a
   * higher track draws over a lower one.
   *
   * ⚠ TWO SOURCES, AND BOTH ARE NECESSARY. A clip's `track` proves a row is
   * OCCUPIED; a `kind: "video"` layer record proves one was ASKED FOR, which is
   * the only evidence an EMPTY row can leave. Records alone would hide every row
   * of every animatic saved before those records existed; clips alone is what
   * made an added-but-unfilled row vanish on reload.
   *
   * A record wins on NAME, because that is the half it exists to carry — the
   * number can only ever produce "Video 3".
   */
  const videoTracks = useMemo(() => {
    const byTrack = new Map();
    for (const l of layers) {
      // ⚠ THROUGH `rowKindOrLegacy`, NOT A BARE LOOKUP. A project saved while
      // Stills rows existed still carries `kind: "stills"` records, and dropping
      // them here would make the row vanish from the gutter while its clips went
      // on playing — a row you can neither empty nor delete. Read as the plain
      // video row its clips already sit on instead. See `ROW_KINDS` in scene.js.
      const rowKind = rowKindOrLegacy(l.kind);
      if (!rowKind) continue;
      const track = Number(l.track);
      // A record with no usable track number describes no row. Dropped rather
      // than folded to 0, which would silently pile it onto the base track.
      if (!Number.isInteger(track) || track < 0 || track > MAX_PICTURE_TRACK) continue;
      if (!byTrack.has(track)) {
        // ⚠ A BOARD ROW IS CALLED AFTER ITS KIND, NEVER AFTER THE BOARD. The
        // import used to name the row after the storyboard it came from, so the
        // gutter read "TTBB_E…" and nothing on screen said which of the three
        // kinds that row was — reported as "i see my storyborad namke come and
        // show in layer but this not happen i want you keep Story..Image". There
        // is no rename in the UI, so a stored name on one of these two rows is
        // never something the user typed: it is either that board title or an
        // older build's long label, and both are better read as the canonical
        // one. Blanking it here hands it to the numbering pass below.
        // ⚠ AND A MIGRATED STILLS ROW IS RENAMED FOR THE SAME REASON: its stored
        // name is "Stills", which is a row kind that no longer exists.
        const name = isBoardRow(rowKind) || rowKind !== l.kind ? "" : l.name;
        byTrack.set(track, { track, rowKind, name, layerId: l.id });
      }
    }
    // `pictureTracks` always includes 0, so the base row exists in an empty
    // project — which is what makes it somewhere to drop the first clip.
    //
    // ⚠ A ROW NO RECORD NAMES IS CALLED AFTER WHAT IS ON IT (`dominantRowKind`),
    // and that is the whole migration for every animatic saved before these
    // kinds existed. An animatic built from a board opens with its panels on
    // track 0, so that row reads "Storyboard images" rather than "Video" without
    // anything being moved or rewritten — the clips already say what they are.
    // An empty row has no clips to ask, and falls back to a plain video row,
    // which is what a new animatic should open with.
    for (const track of pictureTracks(frames)) {
      if (!byTrack.has(track)) {
        const on = frames.filter((f) => frameTrack(f) === track);
        byTrack.set(track, { track, rowKind: dominantRowKind(on), name: "", layerId: null });
      }
    }
    // Numbered per KIND, low track first, so "Storyboard images 2" is the second
    // storyboard row rather than the second row of any sort. Done after the map
    // is complete because it is a question about the whole set.
    const rows = [...byTrack.values()].sort((a, b) => a.track - b.track);
    const seen = new Map();
    for (const row of rows) {
      const nth = seen.get(row.rowKind) || 0;
      seen.set(row.rowKind, nth + 1);
      if (!row.name) row.name = rowKindName(row.rowKind, nth);
    }
    // …and handed back HIGHEST FIRST, which is the compositing order.
    return rows.reverse();
  }, [layers, frames]);

  /** The first row of a kind, or null — where an import or a render belongs. */
  const rowOfKind = useCallback(
    (kind) => videoTracks.find((r) => r.rowKind === kind) || null,
    [videoTracks]
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
    // ⚠ EVERY ROW THAT DRAWS GOES IN HERE FIRST, AND IS RANKED AS ONE SET. The
    // four kinds used to be pushed straight onto `out` in a fixed sequence and
    // sorted only WITHIN themselves, which is precisely the complaint: "i check
    // shapes layer move only other shapes layer, text layer only move other texts
    // layer". They are one stack now, ordered by `laneRank`, and `out` is that
    // stack with the audio rows appended under it.
    //
    // ⚠ THE ORDER THEY ARE PUSHED IN IS ONLY THE TIE-BREAK. It is the DERIVED
    // order — what the gutter shows a project nobody has dragged anything in —
    // and rows that tie on rank come out in it. Rank decides everything else.
    const visual = [];
    // ⚠ SHOWN WHENEVER THERE ARE CAPTION CLIPS, even if the layer record is
    // missing. A clip whose lane doesn't exist is filtered out of every lane
    // there is — it would be invisible on the timeline while still drawing in
    // the monitor and the export, which reads as captions that cannot be
    // deleted. This is the safety net for a project the server wrote a lane for
    // and something later dropped.
    const captionLayer = layers.find((l) => l.id === CAPTION_LAYER_ID);
    if (captionLayer || hasCaptionClips) {
      visual.push({
        key: CAPTION_LAYER_ID,
        kind: "text",
        name: captionLayer?.name || CAPTION_LAYER_NAME,
        layerId: CAPTION_LAYER_ID,
        removable: true,
        // ⚠ NO `icon` ANY MORE. The gutter numbers its rows instead of drawing a
        // glyph for each kind (see `LANE_HINT`'s note in `Timeline.jsx`), so the ❝
        // that stood here would be a field nothing reads. What this row IS is said by
        // its name and its hint — which is where it was always said.
        hint: "Captions written from a track — a run replaces this row, never your own text",
        add: "Add a caption to this row by hand",
      });
    }
    /**
     * ONE OVERLAY GROUP — the default row, then the ones the user added, THEN
     * PUT IN THE ORDER THE USER DRAGGED THEM INTO.
     *
     * ⚠ THE DEFAULT ROW IS NO LONGER PINNED FIRST, and that is the whole reason
     * this is a function rather than the two pushes it replaced. "Text" and
     * "Text 2" are two rows of the same kind and either may be the one on top;
     * before this, the row with no `layer_id` was always first because it was
     * written out first, so the only rows that could be restacked were the ones
     * the user had added — which for a project with one row of each kind (the
     * one in the report) is no rows at all.
     *
     * ⚠ THE SAVED ORDER IS A LIST OF LANE TOKENS, NOT INDICES, and it is the
     * SAME vocabulary as `hidden_lanes` and `locked_lanes` (`laneToken`). A
     * stored index would go stale the moment a row was deleted — exactly the way
     * a stored row NUMBER would (see the gutter's note on why the number is the
     * map index). A row the list does not mention keeps its derived place at the
     * end of the stack — above everything the list names — which is what makes an
     * empty `lane_order` mean "the order this editor has always produced". See
     * `laneRank`.
     */
    const group = (kind, name) => [
      { key: `${kind}:`, kind, name, layerId: "", removable: false },
      ...of(kind).map((l) => ({
        key: l.id,
        kind,
        name: l.name,
        layerId: l.id,
        removable: true,
      })),
    ];
    visual.push(...group("text", "Text"));
    visual.push(...group("shape", "Shapes"));
    // Pictures composited OVER the sequence sit directly above it: they are the
    // last thing drawn before the frame itself.
    //
    // ⚠ THE DEFAULT "Images" ROW IS ALWAYS HERE, exactly as Text, Shapes and
    // Video always are. It used to appear only once an image layer RECORD
    // existed, so a new project opened with four rows and no obvious place to
    // drop a logo or a cut-in — and `addLayer` has always named the first ADDED
    // image layer "Images 2" (it numbers from 2 because the default row is
    // supposed to be on screen holding the name "Images"), which named a row
    // after a row that was not there. Its `layerId` is "" and an overlay with no
    // `layer_id` lands on it, the same rule `clipLane`, `clearLane` and
    // `addToLane` already used for the default Text and Shapes rows.
    // ⚠ AND IT IS `removable: false` — its ✕ EMPTIES it rather than deleting it,
    // because it is the row a layerless overlay falls back to.
    visual.push(...group("image", "Images"));
    // The picture tracks, HIGHEST FIRST — the same rule the rest of this list
    // follows, since a higher track is drawn over a lower one. Track 0 always
    // exists (`pictureTracks`), so a project with no pictures still has a row to
    // put some on.
    //
    // ⚠ CALLED "Video", then "Video 2", … AND NOT "Pictures". The row takes
    // footage and stills alike, so naming it after one of the two kinds was a lie
    // about what you could put on it — reported as "the name is confusing".
    // "Video" is also what the Media pane's own group is called and what every
    // NLE calls this row, so the gutter, the pane and "＋ Add layer" now agree.
    // ⚠ THE OVERLAY LAYER IS STILL "Images", and that is a different thing: it
    // composites a picture OVER this row rather than being part of the cut.
    //
    // ⚠ EVERY TRACK IN USE, UNION EVERY TRACK WITH A RECORD — see `videoTracks`.
    for (const row of videoTracks) {
      const { track } = row;
      visual.push({
        key: `frames:${track}`,
        kind: "frames",
        track,
        // ⚠ WHICH OF THE FOUR PICTURE KINDS THIS ROW IS. `kind` stays "frames"
        // because that is what the row DRAWS (clips out of `frames`, as opposed
        // to captions or audio) and the timeline branches on it everywhere; this
        // is the finer question of what may LAND here, which is what makes the
        // rows strict. Both are needed: a caption can never go on a picture row
        // at all, and a board panel can only go on a board-image one.
        rowKind: row.rowKind,
        name: row.name,
        // ⚠ THE RECORD'S ID, OR NULL FOR A ROW THAT ONLY CLIPS PROVE EXISTS.
        // Unlike every other lane this is NOT what its clips point at — a picture
        // clip carries the track NUMBER — so it is only here so the ✕ knows
        // whether there is a record to delete.
        layerId: row.layerId,
        // Track 0 is the floor of the stack and the place a removed row's clips
        // fall back to, so it is the one row that cannot be removed. Its ✕ empties
        // it instead, exactly as the default Text and Shapes rows do.
        //
        // ⚠ AND A ROW WITH NO RECORD IS NOT REMOVABLE EITHER, however high it is:
        // `onRemoveLayer` is given a layer id, so a null one would be a ✕ that
        // does nothing at all. Rows are adopted on load, so this is the safety
        // net rather than the normal case — and the fallback (empty the row) is
        // the honest thing for a row only its clips prove exists.
        removable: track !== 0 && !!row.layerId,
        // Is this row carrying stills AND footage? If so it can be split into
        // two — see `splitFootageOntoTrack`, and the ▶⇧ in the gutter.
        mixed: (() => {
          const on = frames.filter((f) => frameTrack(f) === track);
          const video = on.filter((f) => frameOrigin(f) === "video").length;
          return video > 0 && video < on.length;
        })(),
        hint:
          track === 0
            ? ROW_KIND[row.rowKind].hint
            : `${ROW_KIND[row.rowKind].hint} — drawn OVER the tracks below it, and a gap shows what is under it`,
        add: ROW_KIND[row.rowKind].add,
      });
    }
    /**
     * THE STACK, TOP FIRST — sorted by the rank every renderer sorts by.
     *
     * ⚠ THIS IS THE ONE LINE THAT MAKES THE GUTTER AND THE FILM THE SAME THING.
     * `laneRank` is the function `sceneAt` and `render_frame` use to decide what
     * draws over what, so a row's place in this column IS its place in the
     * picture — drag Video over Images and the video covers the logo, in the
     * monitor and in the MP4.
     *
     * ⚠ IT ALSO CORRECTS A DISAGREEMENT THAT WAS ALWAYS THERE. This gutter used
     * to show Shapes above Images while the renderers drew overlays above shapes,
     * so the column and the picture said opposite things about those two rows.
     * There is only one answer now and it is the renderers': Images sits above
     * Shapes until someone drags it somewhere else.
     *
     * ⚠ TIES KEEP THE DERIVED ORDER, which is what makes an empty `lane_order`
     * mean "exactly what this editor has always shown". Sorted by hand rather
     * than by `sortByRank` because this list wants TOP first and that one hands
     * back bottom first — reversing it would reverse the ties as well, and "Text"
     * would open below "Text 2".
     */
    out.push(
      ...visual
        .map((lane, i) => ({ lane, i, r: laneRank(laneToken(lane), settings.lane_order) }))
        .sort((a, b) => (a.r === b.r ? a.i - b.i : b.r - a.r))
        .map((d) => d.lane)
    );

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
    //
    // ⚠ AND WHETHER THE ROW CAN BE DRAGGED UP OR DOWN — one question, answered
    // once, here. `laneMovable` is the policy (which kinds move at all).
    const movableCount = out.filter(laneMovable).length;
    return out.map((lane) => {
      const vis = laneToken(lane);
      return {
        ...lane,
        vis,
        hidden: !!vis && hiddenLanes.has(vis),
        // ⚠ AN AUDIO ROW HAS NO TOKEN (`laneToken` returns "" for it), so it
        // cannot be hidden — it has its own mute — and it cannot be locked
        // either. That is a gap worth naming rather than papering over: a lock
        // needs a stable per-row token, and a loose audio row is keyed by the
        // FILE it holds, which changes when a clip is dragged in from elsewhere.
        locked: !!vis && lockedLanes.has(vis),
        // CAN THIS ROW BE DRAGGED UP OR DOWN? The timeline reads it to decide
        // what may be picked up and where it may be dropped, and `moveLane` reads
        // the same answer to decide what a drop MEANS — so a drop the gutter
        // offered can never be one the editor then refuses.
        //
        // ⚠ THE COUNT IS THE OTHER HALF. One movable row has nowhere to go, and a
        // row that offers a grab cursor and then refuses every drop is worse than
        // one that never offered. ⚠ AND IT IS COUNTED ACROSS THE WHOLE STACK, not
        // within a kind: a project with one Text row and one Video row has two
        // movable rows and they can trade places, which is the entire point of
        // the change.
        movable: laneMovable(lane) && movableCount > 1,
      };
    });
    // ⚠ `hasCaptionClips`, not `texts`, for the reason above — this list only
    // cares WHETHER any clip is on the captions lane. `frames` is in here for one
    // question too: WHICH TRACKS EXIST, which decides how many picture rows there
    // are. That does mean the list rebuilds when a picture is added or moved
    // across tracks, which is exactly when the rows change.
  }, [
    layers,
    audioTracks,
    hasCaptionClips,
    frames,
    hiddenLanes,
    lockedLanes,
    videoTracks,
    settings.lane_order,
  ]);

  /**
   * RESTACK THE ROWS — one row dragged onto another's place in the gutter.
   *
   * Asked for twice, the second time to correct what the first version did:
   *
   *     "i want move layer up - down in timline only those layer: Text, shapes,
   *      Image, Video, Story..images, and Story..video  audio and Caption not
   *      move okay"
   *     "i check shapes layer move only other shapes layer, text layer only move
   *      other texts layer … i want these all layer move up down each other …
   *      because i want video layer move up Image and shapes and shapes down
   *      video lie this all move"
   *
   * ⚠ IT IS ONE SETTINGS WRITE, AND NOTHING ELSE IS TOUCHED. That is worth
   * pausing on, because the version this replaced was three edits behind one
   * gesture: it RENUMBERED every picture clip's `track`, it REORDERED the `texts`
   * / `shapes` / `overlays` arrays, and it rewrote the eye's and the padlock's
   * tokens to follow the renumbering. All of that existed because the draw order
   * was hard-coded in the renderers and the only way to reach it was to move the
   * data underneath it — which is also why a row could only ever be dragged among
   * its own kind. The renderers read `lane_order` now (`laneRank`, and its twin
   * in animatic_render.py), so the gesture writes that one list and every clip,
   * every track number and both token lists stay exactly as they are.
   *
   * ⚠ THE WHOLE STACK IS WRITTEN, NOT THE TWO ROWS THAT MOVED. A row the list
   * does not name ranks ABOVE everything it does (see `laneRank`), so a partial
   * list would send every unnamed row to the top — the drag would look like it
   * had moved everything else. `restack` takes the stack as the gutter currently
   * shows it, which already accounts for both the saved order and the rows the
   * saved order has never heard of.
   *
   * ⚠ NOTHING IS RE-TIMED. A clip keeps `start_ms` and a picture clip keeps its
   * `track`, so the film plays at exactly the same moments; the only thing that
   * changes is what is drawn over what.
   *
   * ⚠ A LOCKED ROW IS NOT MOVED AND IS NOT MOVED PAST. "A locked row plays and
   * exports exactly as it did" is the promise the padlock makes, and its place in
   * the stack is part of what it plays as — so any drag that would change a
   * locked row's position is refused, naming the row, rather than silently
   * dropped.
   */
  function moveLane(lane, toKey) {
    const src = lanes.find((l) => l.key === lane?.key);
    const dst = lanes.find((l) => l.key === toKey);
    if (!src || !dst || src.key === dst.key) return;
    // A row that does not move at all cannot have been picked up — the gutter
    // gives it no grab cursor and `startLaneDrag` refuses it — so this is a
    // guard rather than a case: there is no gesture here to explain.
    if (!laneMovable(src) || !laneMovable(dst)) return;

    // The movable stack as it stands and as it would be. Gutter order, top of
    // the stack first, exactly as `lanes` is.
    const rows = lanes.filter(laneMovable);
    const from = rows.findIndex((l) => l.key === src.key);
    const to = rows.findIndex((l) => l.key === dst.key);
    const order = restack(rows.map(laneToken), laneToken(src), laneToken(dst));
    // ⚠ CHECKED ON THE ROWS, NOT ON THE TOKENS, because the message has to name
    // the row a person can see. `order` is the same length and the same set, so
    // comparing it position by position with the rows it came from is exactly
    // "which rows would end up somewhere else".
    const stuck = rows.find((l, i) => l.locked && order[i] !== laneToken(l));
    if (stuck) {
      setNotice(`${stuck.name} is locked — unlock it to restack the rows around it.`);
      return;
    }

    setSettings((sett) => ({ ...sett, lane_order: order }));
    setNotice(
      `${src.name} moved — it now draws ${to < from ? "over" : "under"} ${dst.name}. ` +
        "Nothing was re-timed."
    );
  }

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
      // ⚠ THE KEYBOARD RAZOR ONLY. A razor CLICK on a locked row never gets here
      // — `startClipDrag` refuses it first — but Ctrl+K cuts whatever is under
      // the playhead and has no lane to have been refused by.
      if (frameLocked(source)) {
        setNotice("That row is locked — unlock it in the gutter to cut it.");
        return;
      }
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
      } else if (item.kind === "frame") {
        // ⚠ A PICTURE SETS A FLOOR LIKE EVERYTHING ELSE. It used to be skipped
        // here ("a picture is not moved"), which was true while the picture was
        // one butt-jointed sequence and stopped being true when clips got their
        // own `start_ms` on numbered tracks. Left out, a selection whose
        // LEFTMOST clip was a picture measured its wall from something further
        // right — so dragging left pushed the picture through 0:00, where
        // `moveSelection`'s own clamp then squashed it against the front and
        // broke the spacing the group move exists to preserve.
        start = frameStartById.get(item.id);
      } else continue;
      if (start !== undefined) floor = Math.min(floor, Math.max(0, start || 0));
    }
    return floor === Infinity ? 0 : floor;
  }, [liveSelection, texts, shapes, overlays, audioTracks, frameStartById]);

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
    // ⚠ THE LOCKED ONES ARE DROPPED, NOT THE WHOLE PRESS. A marquee across six
    // rows where one is locked should delete the five — refusing the lot would
    // make one locked row block every edit near it, which is not what a lock on
    // ONE row means. What was skipped is counted, so nothing goes quiet.
    const locked = items.filter(itemLocked);
    const live = locked.length ? items.filter((i) => !itemLocked(i)) : items;
    if (!live.length) {
      setNotice(
        `Nothing deleted — ${
          locked.length === 1 ? "that clip is" : `all ${locked.length} are`
        } on a locked row.`
      );
      return;
    }
    items = live;
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
    setNotice(
      `Deleted ${selectionLabel(items)}.${
        locked.length ? ` ${locked.length} left alone — locked row.` : ""
      }`
    );
  }

  /**
   * DUPLICATE EVERY SELECTED CLIP — alt-click's whole implementation.
   *
   * ⚠ ONE `set…` PER LIST AND ONE OFFSET FOR THE LOT, which is the same rule
   * `deleteMany` and `moveSelection` follow and for the same two reasons: forty
   * copies made one at a time would be forty renders and forty presses of Ctrl+Z,
   * and forty separately-placed copies would not keep the shape of what was
   * copied.
   *
   * ⚠ `offsetMs` IS WHERE THE COPIES GO, AND IT COMES FROM THE DRAG. An alt-drag
   * hands over its snapped travel, so the copies land exactly where the ghost was
   * drawn — which is the whole gesture: you decide where they go while you can see
   * it. Passed as null (no drag — a menu, a button, a future shortcut) it falls
   * back to the BLOCK'S OWN LENGTH, its last end minus its first start, so the
   * copies sit immediately after the originals. For one clip that is exactly its
   * own duration, which is what `duplicateText` / `duplicateShape` /
   * `duplicateOverlay` have always done — so a keyboard or menu duplicate and the
   * pane's button put a copy in the same place.
   *
   * ⚠ EITHER WAY THE OFFSET IS ONE NUMBER FOR THE WHOLE SET, never one per clip.
   * That is what keeps the shape of what was copied: forty clips re-placed
   * individually would arrive as a pile.
   *
   * ⚠ A COPY MAY LAND ON TOP OF WHAT IS ALREADY THERE, and that is chosen rather
   * than worked around — more so now that the drop point is the user's. Every NLE's
   * copy is an overwrite; nothing is destroyed here (the clash is drawn on the bar
   * — `.tl-bar.clash` — and Ctrl+Z takes the copy away); and the ghost shows the
   * overlap before you let go, which is the moment to see it.
   */
  function duplicateMany(items, offsetMs = null) {
    if (!items.length) return;
    // Locked rows are dropped, not the whole press — `deleteMany` says why.
    const locked = items.filter(itemLocked);
    const live = locked.length ? items.filter((i) => !itemLocked(i)) : items;
    if (!live.length) {
      setNotice(
        `Nothing duplicated — ${
          locked.length === 1 ? "that clip is" : `all ${locked.length} are`
        } on a locked row.`
      );
      return;
    }

    /**
     * WHERE ONE ITEM SITS AND FOR HOW LONG, in timeline ms.
     *
     * ⚠ A PICTURE'S START IS NOT NECESSARILY ITS `start_ms` — the same trap
     * `moveSelection` documents. A clip saved before tracks has none and begins
     * where the one before it on its row ended, so the evaluated start
     * (`frameStartById`) is the only number that means anything. ⚠ And an AUDIO
     * clip's length is not `duration_ms` either: that is the FILE's length, while
     * what it occupies is its trim (`trackPlayMs`).
     */
    const spanOf = (item) => {
      const clip = clipOfItem(item);
      if (!clip) return null;
      if (item.kind === "frame") {
        return {
          start: frameStartById.get(item.id) ?? 0,
          length: Math.max(100, Number(clip.duration_ms) || 2000),
        };
      }
      if (item.kind === "audio") {
        return { start: trackStartMs(clip), length: trackPlayMs(clip) };
      }
      return {
        start: Math.max(0, Math.round(clip.start_ms || 0)),
        length: Math.max(0, Math.round(clip.duration_ms || 0)),
      };
    };
    let blockStart = Infinity;
    let blockEnd = 0;
    for (const item of live) {
      const span = spanOf(item);
      if (!span) continue;
      blockStart = Math.min(blockStart, span.start);
      blockEnd = Math.max(blockEnd, span.start + span.length);
    }
    if (blockStart === Infinity) return;
    // ⚠ A DRAGGED OFFSET IS TAKEN AS GIVEN, CLAMPED ONLY AT THE FRONT OF THE VIDEO.
    // The timeline has already clamped and SNAPPED it against the same floor
    // (`selectionFloorMs`), so this is a second lock on the same door rather than a
    // correction — but it is the door that matters: a negative start is not a place
    // on the timeline. Without a drag, the fallback is the block's own length.
    const dragged = Number.isFinite(offsetMs) && Math.round(offsetMs) !== 0;
    const delta = dragged
      ? Math.max(-blockStart, Math.round(offsetMs))
      : // The floor is there for a clip with no measured length — a copy landing
        // exactly on top of its original is a copy you cannot see or grab.
        Math.max(MIN_CLIP_MS, blockEnd - blockStart);

    /**
     * A COPIED GROUP BECOMES A GROUP OF ITS OWN.
     *
     * ⚠ NOT THE ORIGINAL'S GROUP — that part is the same rule the single-clip
     * Duplicate buttons state: a copy that joined the original's group would move
     * and delete with clips the user never pointed at. But it does not follow that
     * a copy is UNGROUPED, and here it matters more than it does there: clicking
     * one member of a group selects them all (`expandGroup`), so alt-clicking a
     * grouped clip duplicates the whole group — and copies that arrived loose
     * would turn one group into a group and a pile. One fresh id per source group,
     * so the shape survives and the two groups stay strangers.
     */
    const groupCopies = new Map();
    const regroup = (clip) => {
      const from = groupOf(clip);
      if (!from) return "";
      if (!groupCopies.has(from)) groupCopies.set(from, newId());
      return groupCopies.get(from);
    };

    // What to select when this is over — the copies, which is what you want to
    // drag next. Filled by each branch below.
    const made = [];

    const frameIds = idsOf(live, "frame");
    if (frameIds.size) {
      // ⚠ EACH COPY GOES IN RIGHT AFTER ITS SOURCE, not at the end of the list.
      // A picture's list position is its place in the SEQUENCE — it is what the
      // strip numbers and what `stackAt` breaks a tie on — so a copy appended to
      // the end would sit mid-timeline and be listed last.
      const next = [];
      // Which transition (if any) has to follow its clip's copy instead. Built
      // here, applied once below.
      const reanchor = new Map();
      for (const f of frames) {
        next.push(f);
        if (!frameIds.has(f.id)) continue;
        const start = frameStartById.get(f.id) ?? 0;
        const length = Math.max(100, Number(f.duration_ms) || 2000);
        const copyId = newId();
        // The picture is identical, so the copy points at the same source — its
        // blob is fetched from the same URL and nothing is uploaded twice. Its
        // effects and keyframes come too: those are timed relative to the clip,
        // so they mean the same thing wherever it sits.
        next.push({ ...f, id: copyId, start_ms: Math.max(0, start + delta) });
        made.push({ kind: "frame", id: copyId });
        // ⚠ A TRANSITION MOVES TO THE COPY ONLY WHEN THE COPY BUTTS UP AGAINST
        // THE ORIGINAL. A transition is anchored to the clip it FOLLOWS, so when
        // the copy lands exactly at the original's end the transition would
        // otherwise dissolve between two identical pictures and be invisible —
        // the reason `duplicateFrame` re-anchors too. When the block is longer
        // than this clip the copy lands somewhere else entirely, the original's
        // cut is still a cut between two different shots, and moving the
        // transition off it would take away a dissolve nobody touched.
        if (delta === length) reanchor.set(f.id, copyId);
      }
      setFrames(next);
      if (reanchor.size) {
        setTransitions((list) =>
          list.map((t) =>
            reanchor.has(t.after_frame_id)
              ? { ...t, after_frame_id: reanchor.get(t.after_frame_id) }
              : t
          )
        );
      }
    }

    // A caption, a shape and an overlay picture are the same three fields here,
    // so they are one function rather than three that drift.
    //
    // ⚠ AND A COPY OF A GENERATED CAPTION IS A TYPED ONE, which falls out of
    // `newId()` and is worth saying out loud because it is load-bearing: a caption
    // counts as generated by its ID PREFIX (`isGeneratedCaption`), and the next
    // captions or voiceover run replaces every clip that has it. A copy that kept
    // the prefix would be swept away by a pass the user had no reason to connect
    // to it. Duplicating one is how you keep a line the machine wrote.
    const copyTimed = (list, kind) => {
      const ids = idsOf(live, kind);
      if (!ids.size) return null;
      const copies = list
        .filter((c) => ids.has(c.id))
        .map((c) => ({
          ...c,
          id: newId(),
          group_id: regroup(c),
          start_ms: Math.max(0, Math.round(c.start_ms || 0)) + delta,
        }));
      for (const c of copies) made.push({ kind, id: c.id });
      return [...list, ...copies];
    };
    const nextTexts = copyTimed(texts, "text");
    if (nextTexts) setTexts(nextTexts);
    const nextShapes = copyTimed(shapes, "shape");
    if (nextShapes) setShapes(nextShapes);
    const nextOverlays = copyTimed(overlays, "overlay");
    if (nextOverlays) setOverlays(nextOverlays);

    const audioIds = idsOf(live, "audio");
    if (audioIds.size) {
      const copies = audioTracks
        .filter((a) => audioIds.has(clipId(a)))
        .map((a) => ({
          ...a,
          // ⚠ AN EXPLICIT `id` IS NOT OPTIONAL HERE. `clipId` falls back to the
          // UPLOAD for a clip that has no id of its own (a project older than the
          // razor), so a copy without one would answer to the same key as the
          // thing it was copied from — select one and you have both, cut one and
          // the razor picks whichever it found last.
          id: newId(),
          group_id: regroup(a),
          start_ms: Math.max(0, trackStartMs(a) + delta),
        }));
      for (const a of copies) made.push({ kind: "audio", id: a.id });
      // ⚠ THE AUDIO CAP IS NOT IN THE WAY, and it should not be: `audioFileCount`
      // counts distinct UPLOADS, and a copy plays the file the original already
      // brought in. Duplicating a clip adds no file, exactly as razoring one
      // does not — same reasoning, same place.
      setAudioTracks([...audioTracks, ...copies]);
    }

    if (!made.length) return;
    selectMany(made);
    // ⚠ AFTER `selectMany`, WHICH WRITES A NOTICE OF ITS OWN. "3 clips selected"
    // is true and is not the news; what just happened is that they were made.
    // ⚠ AND IT SAYS WHERE THEY WENT, because on a drag that is the one thing the
    // user cannot check at a glance: the copies are selected and the originals are
    // not, so if the drop was off by a second the notice names the second.
    setNotice(
      `Duplicated ${selectionLabel(live)} — the cop${
        made.length === 1 ? "y is" : "ies are"
      } ${
        dragged
          ? `at ${formatTime(Math.max(0, blockStart + delta))}`
          : `right after the original${live.length === 1 ? "" : "s"}`
      } and selected, so dragging again moves ${made.length === 1 ? "it" : "them"}.${
        locked.length ? ` ${locked.length} left alone — locked row.` : ""
      }`
    );
  }

  /**
   * AN ALT-DRAG ON A CLIP FINISHED — the timeline's report, turned into copies.
   *
   * `deltaMs` is how far the drag travelled, snapped; it is where the copies go.
   *
   * ⚠ THE SELECTION DECIDES HOW MUCH, and the timeline cannot: it knows which bar
   * was pressed, the document knows what is selected and what is grouped WITH it.
   * Three cases, one rule — "duplicate what a drag here would have moved":
   *
   *   · the clip is one of several selected → the whole selection, so alt-dragging
   *     any member of a marquee copies the lot, exactly as dragging it moves the lot;
   *   · the clip is grouped → the whole group, because that is what clicking it
   *     selects (`expandGroup`) and a gesture that copied half a group would be
   *     the one place in the editor where a group is not one thing;
   *   · otherwise → just it.
   */
  function duplicateAt(kind, id, deltaMs = null) {
    const inSelection = hasItem(liveSelection, kind, id);
    duplicateMany(
      inSelection && liveSelection.length > 1
        ? liveSelection
        : expandGroup({ kind, id }, groupPools),
      deltaMs
    );
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
   * ⚠ PICTURES MOVE HERE TOO, and the missing branch below was the bug behind
   * "we are not able to move here and there properly". `frame` has been in
   * `MOVABLE` (`animatic/selection.js`) since clips got their own `start_ms` on
   * numbered tracks, so the timeline let the drag start and drew the ghost — and
   * then this function, which still said pictures cannot move, wrote every OTHER
   * kind and dropped them. The clip snapped back to where it was, with no error
   * and nothing in the undo stack to show for it.
   *
   * ⚠ A PICTURE'S START IS NOT NECESSARILY ITS `start_ms`. A clip saved before
   * tracks has none and begins where the one before it on its row ended, so
   * `+ delta` on the field would move it from 0 rather than from where you can
   * see it. The move is written from `frameStartById` — the evaluated start,
   * which is also what makes the write pin the clip down explicitly.
   */
  function moveSelection(deltaMs) {
    const delta = Math.max(-selectionFloorMs, Math.round(deltaMs || 0));
    if (!delta) return;
    const items = liveSelection;
    const frameIds = idsOf(items, "frame");
    const textIds = idsOf(items, "text");
    const shapeIds = idsOf(items, "shape");
    const overlayIds = idsOf(items, "overlay");
    const audioIds = idsOf(items, "audio");
    // No per-clip clamp: the delta above is already the most the whole selection
    // can travel, so `+ delta` cannot take anything below zero.
    const slide = (c) => ({ ...c, start_ms: Math.max(0, (c.start_ms || 0) + delta) });
    // ⚠ ONE `setFrames`, so a group move of forty pictures is one render and one
    // press of Ctrl+Z — the same rule `patchFrames` and `insertPictures` follow.
    if (frameIds.size) {
      setFrames((list) =>
        list.map((f) =>
          frameIds.has(f.id)
            ? { ...f, start_ms: Math.max(0, (frameStartById.get(f.id) ?? 0) + delta) }
            : f
        )
      );
    }
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
      setNotice("Nothing groupable is selected. Clips on a video track can't be grouped.");
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
   *
   * ⚠ `atMs` IS WHERE YOU DROPPED IT, and it only decides anything when the
   * newcomers are going on the END of their track — which is every drop onto a
   * row you just made. Without it, a clip dropped on an empty "Video 2" at 0:45
   * jumped to 0:00, because "the end of an empty track" is zero: you aimed at a
   * moment and the clip landed somewhere else. Honouring it means a deliberate
   * GAP in front of the clip, which is a thing that exists now — a gap on a
   * video row shows the row underneath. Inserting BETWEEN two clips still ignores
   * it and ripples, because there the nearest cut is the only placement that
   * doesn't bury what is already there.
   */
  function insertPictures(list, added, atIndex, track, atMs = null) {
    const spans = frameSpans(list).spans;
    const on = spans.filter((s) => s.track === track).sort((a, b) => a.start - b.start);
    // Where the newcomers begin: the start of the clip they are going in front
    // of, or — on the end of the track — the later of the drop time and whatever
    // is already there, so they never land on top of the last clip.
    const ahead = on.find((s) => s.index >= atIndex);
    const tail = on.length ? on[on.length - 1].end : 0;
    const dropAt = Number.isFinite(atMs) ? Math.max(0, Math.round(atMs)) : null;
    const at = ahead ? ahead.start : Math.max(tail, dropAt ?? tail);
    let clock = at;
    const placed = added.map((clip) => {
      const start = clock;
      clock += Math.max(100, Number(clip.duration_ms) || 2000);
      return { ...clip, track, start_ms: start };
    });
    // How far everything after them on this track has to move to make room.
    // Nothing does when they went on the end — there is nothing after them —
    // which is why a drop into a gap leaves the rest of the project alone.
    const shift = clock - at;
    // ⚠ INDEXED, NOT `indexOf`. `spans` is parallel to `list`, so span i belongs
    // to clip i — `list.indexOf(f)` re-scanned the list for every clip (n² on a
    // thirty-panel board) and answered the FIRST match, which is the wrong clip
    // the moment two entries are the same object reference.
    const next = list.map((f, i) => {
      const span = spans[i];
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
    setNotice("Re-ordered — that video row is closed up in the new order.");
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
    const doomed = frames.find((f) => f.id === id);
    if (doomed && frameLocked(doomed)) {
      setNotice("That row is locked — unlock it in the gutter to delete from it.");
      return;
    }
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
    // ⚠ THE ID GOES BACK TO THE CALLER, and the button ignores it. The Director
    // cannot: "add a title, then give it the Rise preset" is two steps, and the
    // second one has no way to name the clip the first one made unless this
    // says. Returning it costs nothing and is the alternative to a second
    // `newTextClip` literal living in the agent — see `agent/actions.js`.
    return clip.id;
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
    seatNewLane(layerTokenOf(layer));
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
   * ⚠ IT IS AN `addLayer` NOW, WITH A TRACK NUMBER ON IT — and that change is the
   * fix for "when i see again my video picker layer not show". It used to bump a
   * view-only counter, on the reasoning that a picture track is a NUMBER on a clip
   * (`frameTrack`) and the rows are derived from the numbers in use, so there was
   * nothing to create: the row became real when something landed on it. True, and
   * useless — an empty row is exactly what you make BEFORE you have something to
   * put on it, and it did not survive the trip to the library and back. The record
   * carries the two things the number cannot: that the row exists while empty, and
   * what it is called.
   *
   * ⚠ THE NUMBER STILL COMES FROM THE CLIPS AND THE RECORDS TOGETHER, so a row
   * can never be handed a track something else already occupies.
   */
  /**
   * @param rowKind which of the four picture kinds — see `ROW_KIND`
   * @param name    override the kind's own numbered name (the storyboard import
   *                names its row after the board)
   * @param quiet   skip the notice, for when the caller says something better
   * @returns the track number it claimed, or null if there was no room
   *
   * ⚠ IT RETURNS THE TRACK, because the two callers that are not the ＋ Add layer
   * menu need to put something ON the row they just made — an import's frames, a
   * Veo render — and reading it back out of `videoTracks` would mean reading
   * state that this render has not produced yet.
   */
  function addPictureTrack(rowKind = "video", { name = "", quiet = false } = {}) {
    const lane = pictureLane(rowKind, name);
    if (!lane) return null; // no room; `pictureLane` said so
    setLayers((list) => [...list, lane]);
    seatNewLane(layerTokenOf(lane));
    if (!quiet) {
      setNotice(
        ROW_KIND[lane.kind].takes.length
          ? `${lane.name} added — drag a clip up onto it, or use its ＋.`
          : `${lane.name} added — ${ROW_KIND[lane.kind].add.toLowerCase()}.`
      );
    }
    return lane.track;
  }

  /**
   * The lane record `addPictureTrack` would create — WITHOUT adding it.
   *
   * Split out for the storyboard import, which cannot let the row reach state
   * first: it has to send the row and the frames that sit on it to the server in
   * ONE write, and the only way to have both values before a render is to build
   * them. See `doBoardImport`.
   *
   * @returns the lane, or null — having said why — when there is no room.
   */
  function pictureLane(rowKind = "video", name = "") {
    const kind = ROW_KIND[rowKind] ? rowKind : "video";
    const next = Math.max(...pictureTracks(frames), ...videoTracks.map((r) => r.track)) + 1;
    if (next > MAX_PICTURE_TRACK) {
      setNotice(`That's the limit — a project can hold ${MAX_PICTURE_TRACK + 1} picture rows.`);
      return null;
    }
    // Numbered among the rows of ITS OWN kind, so the second storyboard row is
    // "Storyboard images 2" however many video rows sit between them.
    const nth = videoTracks.filter((r) => r.rowKind === kind).length;
    return { id: newId(), kind, name: name || rowKindName(kind, nth), track: next };
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
    const to = Math.max(...pictureTracks(frames), ...videoTracks.map((r) => r.track)) + 1;
    if (to > MAX_PICTURE_TRACK) {
      setNotice(`That's the limit — a project can hold ${MAX_PICTURE_TRACK + 1} video rows.`);
      return;
    }
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
    // ⚠ AND IT CLAIMS THE ROW AS A RECORD, not just by putting clips on it. The
    // clips alone would draw the row, but emptying it later would make it vanish
    // — the same disappearing row this whole change is about.
    setLayers((list) => [
      ...list,
      { id: newId(), kind: "video", name: rowKindName("video", videoTracks.filter((r) => r.rowKind === "video").length), track: to },
    ]);
    setNotice(
      `${footage.length} video clip${footage.length === 1 ? "" : "s"} moved to Video ${to + 1} — ` +
        "every one still plays at the same moment." +
        (stranded.length
          ? ` ${stranded.length} transition${stranded.length === 1 ? "" : "s"} now sit${
              stranded.length === 1 ? "s" : ""
            } across a gap and will not play until you close it.`
          : "")
    );
  }

  /**
   * ✕ on a row the user ADDED: delete the record, and deal with what was on it.
   *
   * For a caption, a shape, an overlay or an audio clip that means DELETING the
   * clips too: they point at this lane by `layer_id`, so there is nowhere else
   * for them to live, and silently moving them to another row would be worse
   * than saying so.
   *
   * ⚠ AND A PICTURE ROW IS NO LONGER THE EXCEPTION — ITS CLIPS GO TOO. They carry
   * a track NUMBER rather than a layer id, so track 0 was always somewhere they
   * COULD live, and they used to drop there keeping the moment they played at. Two
   * things were wrong with that. The confirm this ✕ opens has always said "The row
   * and the 1 clip on it" — a promise the code did not keep — and what the user
   * actually saw was the clip reappearing on a row it was never put on: "when i
   * delete layer. so only delete layer not clip and i want delete clip too".
   *
   * ⚠ AND IT IS ONLY SAFE TO DELETE THEM BECAUSE THE MEDIA LIBRARY EXISTS NOW. A
   * shot is the most expensive thing on this timeline to lose — a board panel, an
   * upload, a Veo render that was paid for — and that was the whole argument for
   * saving them. Since `assets.js`, deleting a clip leaves its SOURCE in Media, so
   * what goes here is the placement, not the picture: drag the card back out and
   * it lands on a row of its own kind again. The notice says so, because "and 42
   * clips" is only an answerable question if you know what survives it.
   */
  function removeLayer(layerId) {
    const layer = layers.find((l) => l.id === layerId);
    // ⚠ BEFORE ANYTHING ELSE, and for every kind of row this function can
    // delete: the saved order must not keep naming a row that is gone, or the
    // next row to claim the same token inherits its place in the stack.
    if (layer) unseatOldLane(layerTokenOf(layer));
    if (layer && ROW_KIND[layer.kind]) {
      const track = Number(layer.track);
      const on = frames.filter((f) => frameTrack(f) === track);
      setLayers((list) => list.filter((l) => l.id !== layerId));
      if (on.length) {
        // ⚠ JUST THIS TRACK, AND NOTHING ELSE MOVES. A picture holds its own
        // start, so the rows around it stay exactly where they were — the same
        // reason `clearLane` can empty one row without re-timing the others.
        const going = new Set(on.map((f) => f.id));
        const kept = frames.filter((f) => !going.has(f.id));
        setFrames(kept);
        // ⚠ AND THE SAME TWO CHORES EVERY OTHER FRAME DELETE DOES — the list is
        // computed above rather than inside an updater for the reason
        // `deleteFrame` gives (a setState from inside another one runs twice in
        // StrictMode). A transition whose clip has gone, or one that has become
        // the last cut, is a transition to nothing; and a selection pointing at a
        // deleted clip is a Properties pane describing something that is not
        // there. `selectOnly({})` is what `deleteMany` does, and this is the same
        // kind of press: a whole row at once, not one clip.
        setTransitions((list) => pruneTransitions(list, kept));
        selectOnly({});
      }
      setNotice(
        on.length
          ? `${layer.name} removed with its ${on.length} clip${
              on.length === 1 ? "" : "s"
            } — the sources are still in Media, so you can drop them back in.`
          : `${layer.name} removed.`
      );
      return;
    }
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
   * ⚠ AND IT NO LONGER ASKS — THE ROW'S ✕ HAS ALREADY ASKED. This used to raise a
   * `window.confirm` because it was the one ✕ in the gutter that could be forty
   * clips behind one click. Every ✕ in the gutter opens the row's own popover now
   * (`.tl-layer-confirm`, counted and anchored to the row), so the native dialog
   * was a second question about the same press — in a different place, in the
   * browser's own styling, which is exactly what "same place dropdown" was asked
   * to replace. It is the only caller, so nothing else loses a guard.
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

    if (lane.kind === "frames") {
      // Just this track. ⚠ AND NOTHING ELSE MOVES: a picture holds its own start,
      // so emptying a track leaves the rows around it exactly where they were.
      // (It used to shorten the whole sequence, because there was only one.)
      const kept = frames.filter((f) => frameTrack(f) !== (lane.track || 0));
      setFrames(kept);
      // The same two chores `deleteFrame` and `removeLayer` do — a transition
      // whose clip has gone is a transition to nothing, and a selection pointing
      // at a deleted clip is a Properties pane describing thin air. This skipped
      // both, which was a real hole rather than a difference of opinion.
      setTransitions((list) => pruneTransitions(list, kept));
      selectOnly({});
    } else if (lane.kind === "text") setTexts(off);
    else if (lane.kind === "shape") setShapes(off);
    else if (lane.kind === "image") setOverlays(off);
    setNotice(`Deleted ${what} — Ctrl+Z puts them back.`);
  }

  // The ＋ on a lane. ONE entry point, so "add to this row" behaves the same
  // whether it is pressed in the gutter or on the empty band of the track.
  // Which lane it was pressed on decides what gets added, and where.
  const pendingOverlayLane = useRef("");
  // Which video track the OS FILE DIALOG is filling.
  //
  // ⚠ THIS REF IS ONLY FOR THE PICKER, and it is read once and cleared. It used
  // to be the single source of truth for "which track do new files go on", read
  // by `addFiles` and `addVideoClips` directly — and since only the lane ＋ ever
  // SET it and nothing ever reset it, every other way in (a drop on a row, the
  // Media pane's own button) silently used whatever row was last touched, or
  // track 0. That is why a second video landed beside the first one instead of
  // on the row it was dropped on. The track is a PARAMETER now; this only
  // carries it across the file dialog, which is asynchronous and has no other
  // way to remember what it was opened for.
  const pendingPictureTrack = useRef(0);

  // …and which KIND of row that was, so the dialog can offer only the files the
  // row accepts. Same read-and-clear discipline, same reason.
  const pendingPictureKind = useRef("video");

  // Read-and-clear, so a stale value can never decide a later import.
  function takePendingTrack() {
    const track = pendingPictureTrack.current || 0;
    const rowKind = pendingPictureKind.current || "video";
    pendingPictureTrack.current = 0;
    pendingPictureKind.current = "video";
    return { track, rowKind };
  }

  function addToLane(lane) {
    if (lane.kind === "frames") {
      // ⚠ THE ＋ OPENS WHATEVER FILLS THAT ROW, and for two of the four kinds
      // that is not a file dialog at all. A storyboard row is filled by the
      // import; a Veo row is filled by ✨ Animate. Offering a file picker on
      // either would put an upload on the row your board panels live on, which
      // is the mixing the strict rows exist to stop.
      const rowKind = lane.rowKind || "video";
      if (rowKind === "board_image") {
        openBoardImport(lane.track || 0);
        return;
      }
      if (rowKind === "board_video") {
        setNotice(
          "This row holds Veo renders — pick a storyboard panel and press ✨ Animate to fill it."
        );
        return;
      }
      // ⚠ ONE PICKER, FILTERED BY THE ROW. It used to open the IMAGE input
      // (`accept="image/*"`, straight into the image-only `addFiles`) whatever
      // the row was, so the file dialog hid the very MP4 the same row accepted by
      // drag and drop. `accept` is now the row's own `takes`, and `addAssets`
      // routes by file type as it always did.
      pendingPictureTrack.current = lane.track || 0;
      pendingPictureKind.current = rowKind;
      const input = pictureInputRef.current;
      if (input) {
        input.accept = ROW_KIND[rowKind].takes.map((k) => `${k}/*`).join(",");
        input.click();
      }
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
        // ⚠ WHERE IT CAME FROM, AND THIS IS THE ONE PATH THAT NEEDS IT STORED. A
        // panel has no upload of its own, so `uploadId` above is a COPY minted a
        // few lines up and points at nothing the library knows — without this the
        // Media card the user just dragged could not be matched to the overlay it
        // produced (`assetKey`), so its ×N badge under-counted, its ✕ orphaned
        // this picture, and "Select its clips" missed it. The other two overlay
        // paths reuse the card's own upload id and matched all along.
        src: { ...(frame.src || {}) },
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

  /**
   * REMOVE A LIBRARY CARD, and every clip made from it.
   *
   * ⚠ NO CONFIRM STEP, ASKED FOR DIRECTLY: "when user cilck x buttun so clip in
   * media panel so direct delele fuction no dropdwon delete and cancel option not
   * need here". The layer ✕ in the gutter DOES confirm, and the difference is
   * deliberate rather than inconsistent — a layer ✕ can take forty clips and a
   * whole row with it, where this takes one source you are looking at. The count
   * is on the button's tooltip before the press, which is where the warning went.
   *
   * ⚠ AND IT TAKES THE CLIPS WITH IT. Leaving them would be a clip playing from a
   * source that is no longer listed anywhere — unfixable from the UI, because
   * every control that could reach it is in the pane the card just left.
   *
   * ⚠ INCLUDING THE OVERLAYS, which it used to miss. A picture on an Images lane
   * is an overlay rather than a frame, so this left exactly the orphan the whole
   * feature exists to prevent: a picture still playing from a card that had just
   * been removed from the pane. See `libraryUse` for why the three places that
   * ask this question are widened together.
   */
  function deleteAsset(asset) {
    if (!asset?.id) return;
    const key = assetKey(asset);
    const goneFrames = frames.filter((f) => assetKey(assetFromFrame(f)) === key);
    const goneOverlays = overlays.filter((o) => assetKey(assetFromOverlay(o)) === key);
    const goneAudio = audioTracks.filter((a) => assetKey(assetFromAudio(a)) === key);
    const kept = frames.filter((f) => !goneFrames.includes(f));

    setAssets((list) => list.filter((a) => a.id !== asset.id));
    if (goneFrames.length) {
      setFrames(kept);
      // Same rule `deleteFrame` follows: a transition whose clip has gone, or one
      // that has become the last cut, is a transition to nothing.
      setTransitions((list) => pruneTransitions(list, kept));
      const goneIds = new Set(goneFrames.map((f) => f.id));
      setSelectedId((cur) => (goneIds.has(cur) ? null : cur));
    }
    if (goneOverlays.length) {
      const goneIds = new Set(goneOverlays.map((o) => o.id));
      setOverlays((list) => list.filter((o) => !goneIds.has(o.id)));
      setSelectedOverlayId((cur) => (goneIds.has(cur) ? null : cur));
    }
    if (goneAudio.length) {
      const goneIds = new Set(goneAudio.map((a) => clipId(a)));
      setAudioTracks((list) => list.filter((a) => !goneIds.has(clipId(a))));
      setSelectedTrackId((cur) => (goneIds.has(cur) ? null : cur));
    }
    const used = goneFrames.length + goneOverlays.length + goneAudio.length;
    setNotice(
      used
        ? `“${asset.label || "Media"}” removed, with ${used} clip${
            used === 1 ? "" : "s"
          } that used it.`
        : `“${asset.label || "Media"}” removed from Media.`
    );
  }

  /**
   * NAME A LIBRARY CARD — the double-click on its name, and its menu's Rename.
   *
   * ⚠ IT RENAMES THE CLIPS THAT STILL CARRY THE OLD NAME, AND ONLY THOSE. The
   * library and the timeline are two lists on purpose (`animatic/assets.js`), so
   * this could have written the card alone — but the timeline prints `label` on
   * every bar, and a source renamed to "Chase — wide" whose four bars still read
   * "shot_04.mp4" is a rename that visibly did not take. Renaming a clip the user
   * has ALREADY named by hand would be the worse mistake in the other direction:
   * that name is an edit, not something inherited, so a clip whose label has
   * diverged from the source's is left exactly as it is.
   *
   * ⚠ AN AUDIO CLIP IS NAMED BY `filename`, NOT `label` — that is the field the
   * timeline prints and the field `assetFromAudio` reads back to build the card.
   * Write the other one and the bar keeps the upload's name for ever while the
   * card beside it shows the new one.
   *
   * ⚠ NO REQUEST OF ITS OWN, and none is missing. `assetForSave` carries `label`
   * so the autosave takes it, and the undo stack snapshots the whole document —
   * one write, undoable, like every other edit in this file.
   */
  function renameAsset(asset, name) {
    if (!asset?.id) return;
    const next = String(name || "").replace(/\s+/g, " ").trim().slice(0, 120);
    const was = asset.label || "";
    // An empty name is a cancel, not a blank name — MediaBin has already filtered
    // one out, and this is the second door (a paste of spaces, say).
    if (!next || next === was) return;

    setAssets((list) => list.map((a) => (a.id === asset.id ? { ...a, label: next } : a)));

    // ⚠ WORKED OUT FROM THE CURRENT LISTS, NEVER INSIDE THE UPDATER. Counting in
    // a state updater is a side effect and React is free to run one twice — the
    // same reason `deleteAsset` above builds its lists first and sets afterwards.
    const key = assetKey(asset);
    const picIds = new Set(
      frames
        .filter((f) => assetKey(assetFromFrame(f)) === key && (f.label || "") === was)
        .map((f) => f.id)
    );
    const audIds = new Set(
      audioTracks
        .filter((a) => assetKey(assetFromAudio(a)) === key && (a.filename || "") === was)
        .map((a) => clipId(a))
    );
    if (picIds.size) {
      setFrames((list) => list.map((f) => (picIds.has(f.id) ? { ...f, label: next } : f)));
    }
    if (audIds.size) {
      setAudioTracks((list) =>
        list.map((a) => (audIds.has(clipId(a)) ? { ...a, filename: next } : a))
      );
    }
    const moved = picIds.size + audIds.size;
    setNotice(
      moved
        ? `Renamed to “${next}” — ${moved} clip${
            moved === 1 ? "" : "s"
          } on the timeline took the new name too.`
        : `Renamed to “${next}”.`
    );
  }

  /**
   * SELECT EVERY CLIP CUT FROM ONE SOURCE — the card menu's "Select its clips".
   *
   * ⚠ IT ANSWERS THE QUESTION THE ×2 BADGE POSES AND NOTHING ELSE COULD. A card
   * says how many clips use it; until now nothing said WHERE they are, and on a
   * long timeline the only way to find out was to scroll every row looking for the
   * same thumbnail.
   *
   * ⚠ THE SAME THREE LISTS `deleteAsset` AND `assetUsedCount` LOOK AT, and they
   * are widened together on purpose: a menu offering to select clips the badge
   * does not count, or the ✕ does not delete, is worse than any one of the three
   * being narrow. `overlays` is the list all three used to miss — see `libraryUse`.
   *
   * `selectMany` is the rubber band's own path, so what lands here is a selection
   * like any other: Delete removes them, Ctrl+G groups them, and it says how many.
   */
  function selectAssetClips(asset) {
    if (!asset?.id) return;
    const key = assetKey(asset);
    const items = [
      ...frames
        .filter((f) => assetKey(assetFromFrame(f)) === key)
        .map((f) => ({ kind: "frame", id: f.id })),
      ...overlays
        .filter((o) => assetKey(assetFromOverlay(o)) === key)
        .map((o) => ({ kind: "overlay", id: o.id })),
      ...audioTracks
        .filter((a) => assetKey(assetFromAudio(a)) === key)
        .map((a) => ({ kind: "audio", id: clipId(a) })),
    ];
    if (!items.length) {
      setNotice(`Nothing on the timeline uses “${asset.label || "that source"}” yet.`);
      return;
    }
    selectMany(items);
  }

  /**
   * SAVE A VEO RENDER TO DISK — the Media card's ⬇ and the timeline clip's
   * right-click menu, which are one function because they are one promise.
   *
   * ⚠ IT IS OFFERED ON A PAID RENDER AND ON NOTHING ELSE, which is the whole
   * ask: "only add fuction when user generte Veo video". Every other source in a
   * project is already on the user's machine or is one click away on the board —
   * an upload they dropped in, a panel the storyboard still holds. A render is the
   * one thing that exists ONLY here and costs money to make again, so the reason
   * given for it was "if user want delete project so user first download veo
   * gneereted video": deleting the project has to stop being the thing that
   * destroys it. `isVeoRender` is the same question the timeline already asks to
   * paint these bars purple — see `scene.js`, and do not add a second one.
   *
   * ⚠ IT TAKES A CLIP *OR* A LIBRARY CARD. Both carry `src.upload_id`, because a
   * card is built from the clip by `assetFromFrame`, so one handler serves both
   * places and they cannot come to disagree about which file they save.
   *
   * ⚠ AND IT SAYS SOMETHING BEFORE IT STARTS. A Veo render is tens of megabytes
   * fetched as an authed blob — there is no browser download bar until the whole
   * file has landed, so without the first notice the press looks like it did
   * nothing for several seconds.
   */
  async function downloadVeoClip(item) {
    const uploadId = item?.src?.upload_id || "";
    if (!isVeoRender(item) || !uploadId) return;
    const label = (item.label || "").trim();
    // Windows refuses \ / : * ? " < > | in a file name, and a name that the OS
    // rejects is a download that fails after the bytes have already been fetched.
    const base =
      label.replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, " ").trim().slice(0, 80) ||
      "veo-clip";
    setError("");
    setNotice(`Saving “${base}.mp4”…`);
    try {
      await api.downloadAnimaticMedia(animaticId, uploadId, `${base}.mp4`);
      setNotice(`Saved “${base}.mp4” to your downloads.`);
    } catch (e) {
      setError(e.message);
      setNotice("");
    }
  }

  /**
   * PUT A LIBRARY CARD ON THE TIMELINE without a drag — its ＋, and a double-click.
   *
   * ⚠ A DRAG CANNOT BE THE ONLY WAY. There is no keyboard path through one, and a
   * library scrolled forty cards away from the row you want is a drag that cannot
   * be completed. This lands it on the first row that will TAKE it, at the
   * playhead, and says which row that was.
   */
  async function placeAsset(asset) {
    if (!asset?.id) return;
    const at = Math.round(timeRef.current || 0);
    if ((asset.kind || "image") === "audio") {
      setNotice(
        "Drag a sound onto an audio row — which row it goes on decides what it is mixed with."
      );
      return;
    }
    // ⚠ A PICTURE GOES TO THE IMAGES LANE, and it is the first question asked
    // because it is the one that no longer has anything to do with picture ROWS.
    // The ＋ on a card used to find-or-create a Stills row for it; the routing
    // rule is `belongsOnImageLane` now and it is shared with `addAssets` and with
    // `dropAsset`, so the four doors into "add this picture" cannot disagree.
    // A card can still be DRAGGED onto the Video row to put it in the cut — that
    // is aiming at a row, which is a different act from pressing ＋ on the card.
    if (belongsOnImageLane(asset.kind || "image", assetOrigin(asset) === "board")) {
      const lane = lanes.find((l) => l.kind === "image" && !l.layerId);
      if (!lane) return;
      if (laneIsLocked(lane)) {
        setNotice(`${lane.name} is locked — unlock it to add to it.`);
        return;
      }
      const card = assets.find((a) => a.id === asset.id) || asset;
      await overlayFromFrame(clipFromAsset(card, { id: newId(), animaticId }), lane, at);
      return;
    }
    // The row this kind belongs on: one of its own kind if there is one, else a
    // new one. Exactly the rule `addAssets` follows for a file with no row named.
    // ⚠ ONE DERIVATION, SHARED WITH THE DRAG AND WITH `clipRowKind`. This was
    // written out by hand and the drop rule was written out again in `dropAsset`,
    // and the two disagreed: ＋ put a Veo card on the Storyboard video row while a
    // DRAG of the same card was refused there.
    const wantKind = cardRowKind(asset.kind || "image", assetOrigin(asset) === "board");
    const row = rowOfKind(wantKind);
    let track = row ? row.track : addPictureTrack(wantKind, { quiet: true });
    if (track === null) return; // no room; addPictureTrack said so
    if (laneIsLocked({ kind: "frames", track })) {
      setNotice(`${row?.name || "That row"} is locked — unlock it to add to it.`);
      return;
    }
    // 2000 is the default hold every other add path in this file uses — see
    // `addFiles` and `newVideoClip`. Footage ignores it and opens at its own
    // natural length (`clipFromAsset`).
    const clip = clipFromAsset(asset, { id: newId(), animaticId, defaultMs: 2000 });
    setFrames((list) => insertPictures(list, [clip], frameIndexAt(at, track), track, at));
    selectOnly({ frame: clip.id });
    setNotice(
      `“${asset.label || "Media"}” added to ${row?.name || "a new row"} at ${formatTime(at)}.`
    );
  }

  async function dropAsset({ lane, atMs, asset, files }) {
    const at = Math.max(0, Math.round(atMs || 0));

    // ---- from the desktop -------------------------------------------------
    if (files?.length) {
      if (lane.kind === "frames") {
        // ⚠ `addAssets`, NOT a picker of its own: it is the one door every
        // upload goes through, and it routes by file type — here against the
        // row's own `takes`, so a photo cannot land on a footage row and a board
        // row takes neither.
        const rowKind = lane.rowKind || "video";
        const usable = files.filter((f) => rowTakesFile(rowKind, kindOf(f)));
        if (!usable.length) {
          const takes = ROW_KIND[rowKind]?.takes || [];
          setNotice(
            takes.length
              ? `${lane.name} takes ${takes.join(" and ")} — that file belongs on another row.`
              : `${lane.name} is filled by ${ROW_KIND[rowKind].add.toLowerCase()}, not by dropping files on it.`
          );
          return;
        }
        // ⚠ THE ROW YOU DROPPED ON, PASSED EXPLICITLY — both to place the clip
        // and to find the cut it goes in front of. Neither was true before: the
        // track came from a ref only the lane ＋ ever wrote, so a file dropped on
        // "Video 2" was inserted on "Video" next to whatever was already there,
        // and `frameIndexAt(at)` with no track picked the nearest cut across ALL
        // rows — an index on one row deciding an insert on another. Reported as
        // "the video tracker … imports that only even if we add it on a
        // different layer".
        const track = lane.track || 0;
        await addAssets(usable, frameIndexAt(at, track), track, at, rowKind);
        return;
      }
      if (lane.kind === "image") {
        await addOverlayFiles(files, lane.layerId || "", at);
        return;
      }
      const audio = files.filter((f) => kindOf(f) === "audio");
      if (!audio.length) {
        setNotice("The audio rows take sound files — footage and images belong on a video track.");
        return;
      }
      if (audioFileCount() >= MAX_AUDIO_TRACKS) {
        setNotice(`That's the limit — a project can hold ${MAX_AUDIO_TRACKS} audio tracks.`);
        return;
      }
      pendingAudioLane.current = lane.layerId || "";
      await addAudioTrack(audio[0], at);
      setNotice(`“${audio[0].name}” added at ${formatTime(at)}.`);
      return;
    }

    if (!asset?.id) return;

    // ---- a card out of the MEDIA LIBRARY ----------------------------------
    // ⚠ A COPY, NOT A MOVE, and that one word is the whole difference from the
    // `"frame"` branch below. A library card has no place in the cut to be moved
    // FROM — it may have none or four — so dragging it out MAKES a clip. Drag the
    // same card onto three rows and you get three clips that trim and grade
    // independently, which is what a library is for.
    if (asset.kind === "asset") {
      const card = assets.find((a) => a.id === asset.id);
      if (!card) return;
      const kind = card.kind || "image";

      // Audio is not a picture clip: it becomes a track on an audio row. It also
      // needs no upload — the file is already on the server — so this is the one
      // add path that reaches `setAudioTracks` without going near a file dialog.
      if (kind === "audio") {
        if (lane.kind !== "audio") {
          setNotice("A sound goes on an audio row — the picture rows take stills and footage.");
          return;
        }
        const clip = {
          id: newId(),
          upload_id: card.upload_id,
          layer_id: lane.layerId || "",
          filename: card.label || "",
          duration_ms: card.duration_ms || 0,
          start_ms: at,
          offset_ms: 0,
          volume: 1,
          muted: false,
          url: `/animatics/${animaticId}/media/${card.upload_id}`,
        };
        setAudioTracks((list) => [...list, clip]);
        selectOnly({ track: clipId(clip) });
        setNotice(`“${card.label || "Audio"}” added to ${lane.name} at ${formatTime(at)}.`);
        return;
      }

      // Onto an image LAYER: a picture composited OVER the cut, not a place in
      // the sequence — the same distinction the `"frame"` branch draws, and the
      // reason both rows are called "image" and mean different things.
      if (lane.kind === "image") {
        await overlayFromFrame(clipFromAsset(card, { id: newId(), animaticId }), lane, at);
        return;
      }
      if (lane.kind !== "frames") {
        setNotice(`${lane.name} doesn't take media — drop it on a picture row.`);
        return;
      }
      // ⚠ THE CARD'S OWN ROW, NOT THE ROW'S FILE RULE — and that is the fix for
      // the drag the user could not complete. `rowTakesFile` answers "what may be
      // UPLOADED here", and both board rows answer "nothing"; asking it about a
      // library card meant a Veo render could not be dragged back onto the
      // Storyboard video row it had just been deleted from, and landed on plain
      // Video instead ("i can't drop in Storyboad layer but i drop in Video
      // layer"). `cardRowKind` is the same derivation `clipRowKind` uses for a
      // clip, so a card and the clip made from it can never disagree about where
      // it belongs — and it is the same answer `laneTakes` lit the row up with.
      const rowKind = lane.rowKind || "video";
      const want = cardRowKind(kind, assetOrigin(card) === "board");
      if (rowKind !== want) {
        setNotice(
          `“${card.label || "Media"}” belongs on ${ROW_KIND[want].name} — ` +
            `${lane.name} is a ${ROW_KIND[rowKind]?.name || "different"} row.`
        );
        return;
      }
      const track = lane.track || 0;
      const clip = clipFromAsset(card, { id: newId(), animaticId, defaultMs: 2000 });
      setFrames((list) => insertPictures(list, [clip], frameIndexAt(at, track), track, at));
      selectOnly({ frame: clip.id });
      setNotice(`“${card.label || "Media"}” added to ${lane.name} at ${formatTime(at)}.`);
      return;
    }

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
      // The same uploads a still would make, so they list in the library beside
      // them — and dragging one back out onto an image layer makes another
      // overlay (`dropAsset`). An overlay is a placement, like every other clip.
      addToLibrary(
        added.map((o) =>
          assetFromFrame(
            { kind: "image", src: { kind: "upload", upload_id: o.upload_id }, label: "" },
            newId()
          )
        )
      );
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
    // Same reason as `addText` above: the Director styles what it just made.
    return shape.id;
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
    // ⚠ THE DRAG WORKS IN DRAWN SIZE AND WRITES STORED SIZE, AND THE TWO ARE NOT
    // THE SAME ONCE `scale` IS NOT 1. What you are pulling is the box you can
    // see, which is `w × scale`; what has to be written is `w`. Without the
    // divide, a shape at 300% moved three times as far as the pointer under it —
    // and it would have looked like the handle drifting off the shape rather
    // than like a scale bug, which is why the divide is spelled out here.
    const drawn = boxSize(shape);
    const scale = Math.abs(Number(shape.scale ?? 1)) || 1;
    const from = { x: shape.x, y: shape.y, w: drawn.w, h: drawn.h };
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
        // `w`/`h` here are what is DRAWN; the clip stores them before `scale`.
        const w = clamp(from.w + dx, 0.02, 4);
        const h = clamp(from.h + dy, 0.02, 4);
        patch(shape.id, {
          w: clamp(w / scale, 0.02, 4),
          h: clamp(h / scale, 0.02, 4),
          x: anchorX + w / 2,
          y: anchorY + h / 2,
        });
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

  // ⚠ ESCAPE AND AN OUTSIDE PRESS CLOSE ＋ Add layer, and both have to be written
  // by hand now that it is a dropdown — the modal overlay it replaced did the two
  // of them for free.
  //
  // ⚠ THE ＋ ITSELF IS EXEMPT from the outside-press close, because it TOGGLES:
  // closing on its `pointerdown` would let the `click` that follows reopen what
  // the press just shut, which looks exactly like a dead button.
  useEffect(() => {
    if (!layerMenu) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setLayerMenu(false);
    };
    const onDown = (e) => {
      if (e.target.closest?.(".tl-layer-menu, .tl-add-layer")) return;
      setLayerMenu(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [layerMenu]);

  // The ⚙ dropdown closes the same two ways, and for the same reasons — the
  // gear itself is exempt from the outside press because it toggles. Shared
  // with the sidebar's copy of this menu, so both close alike.
  useMenuDismiss(settingsMenu, closeSettingsMenu, MENU_DISMISS);

  // Typing in the caption box should focus it as soon as a clip is picked.
  useEffect(() => {
    if (selectedTextId) textAreaRef.current?.focus();
  }, [selectedTextId]);

  // The ✨ dialog's live prompt box — on open, and again on every tab switch.
  // ⚠ IT REPLACES `autoFocus`, which cannot be used here: both tab bodies are
  // mounted together so the card can size itself to the taller one, and two
  // `autoFocus` boxes in one tree hand the caret to whichever mounted last.
  useEffect(() => {
    if (!imgGen) return;
    const box = imgGenTab === "image" ? imgGenBoxRef.current : vidGenBoxRef.current;
    box?.focus({ preventScroll: true });
  }, [imgGen, imgGenTab]);

  // ⚠ `track` IS A PARAMETER on all three of these, not a ref they reach for.
  // See `pendingPictureTrack` for what reaching for it cost.
  //
  // Returns how many landed, so `addAssets` can insert the videos AFTER them
  // rather than at an index these have already shifted.
  async function addFiles(files, insertAt, track = 0, atMs = null) {
    if (!files.length) return 0;
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
      setFrames((list) => insertPictures(list, added, insertAt, track, atMs));
      // ⚠ AND INTO THE LIBRARY. The clip is where it plays; the card is the record
      // that the file was ever uploaded, and it has to outlive the clip — see
      // `addToLibrary`.
      addToLibrary(added.map((f) => assetFromFrame(f, newId())));
      if (added.length && !selectedId) setSelectedId(added[0].id);
      if (res.rejected?.length) {
        setNotice(`Skipped ${res.rejected.length}: ${res.rejected.join(", ")}`);
      } else {
        setNotice(`Added ${added.length} image${added.length === 1 ? "" : "s"}.`);
      }
      return added.length;
    } catch (e) {
      setError(e.message);
      return 0;
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
  async function addVideoClips(files, insertAt, track = 0, atMs = null) {
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
      setFrames((list) => insertPictures(list, added, insertAt, track, atMs));
      addToLibrary(added.map((f) => assetFromFrame(f, newId())));
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
    // ⚠ A COLOUR CARD GOES IN THE LIBRARY TOO, keyed by its HEX — so a project
    // with four blackouts has one card, and deleting the last of them still
    // leaves "black" there to drag back out. It is the one asset with no file
    // behind it, which is why `assetUrl` returns "" for it and `MediaBin` draws a
    // swatch rather than waiting for a picture.
    addToLibrary([assetFromFrame(card, newId())]);
    selectOnly({ frame: card.id });
    setMediaTab("media");
    // A card is a still you made, so it lists with the images — see `frameOrigin`.
    openGroup("media:images");
    setNotice("Added a colour card.");
  }

  /**
   * Open the storyboard picker. `track` is the row to fill, or null to make one.
   *
   * The list is fetched on every open rather than cached: boards are made in
   * another workflow entirely, so a list from ten minutes ago can easily be
   * missing the one the user just drew.
   */
  function openBoardImport(track = null) {
    setBoardImport({ track });
    setBoardPick("");
    setBoardError("");
    setBoardList(null);
    api
      .listStoryboards()
      .then((res) => setBoardList(res.items || res || []))
      .catch((e) => {
        setBoardList([]);
        setBoardError(e.message);
      });
  }

  /**
   * Bring the picked board's panels in, onto a Storyboard images row.
   *
   * ⚠ NOTHING REACHES STATE UNTIL THE SAVE HAS LANDED, and that ordering is the
   * whole of this function. A frame's picture is served from
   * `/animatics/{id}/frame/{frameId}`, a route that resolves by looking the frame
   * up in the SAVED project — so a url handed out before the save can only 404,
   * and the fetch effect caches nothing on failure and does not retry. One 404
   * per panel is therefore permanent: a black Program monitor and forty-two blank
   * tiles in Media, for an import that reported success.
   *
   * It used to place the frames, `await flush()`, and then patch the urls in —
   * right in intent and broken in fact, because `flush` reads the document and
   * the dirty flag out of REFS THAT EFFECTS FILL. One microtask after
   * `setFrames`, React has not re-rendered: the flush saw a clean project and
   * returned without writing anything, so the urls went out against a server that
   * had never heard of these frames. Reported as "image panel not show and in
   * media in not upload properly".
   *
   * So the frames, their row and their urls are all built as plain values here,
   * handed to `flush` as an override, and only committed once the write is
   * acknowledged. The import spins for the length of one PUT and then everything
   * appears at once, already fetchable.
   *
   * ⚠ THE ROW IS BUILT, NOT ADDED (`pictureLane`). It has to go to the server in
   * the SAME write as the frames that sit on it — two writes racing the debounce
   * is how a row loses the name it was given.
   */
  async function doBoardImport() {
    if (!boardPick || boardBusy) return;
    setBoardBusy(true);
    setBoardError("");
    try {
      const res = await api.importStoryboardIntoAnimatic(animaticId, boardPick);
      const added = res.frames || [];
      if (!added.length) {
        setBoardError("That board has no drawn panels yet.");
        return;
      }
      // The row to fill: the one whose ＋ was pressed, or one built for this
      // import. `lanes` stays null in the first case — there is no row to save.
      let track = boardImport?.track ?? null;
      let lanes = null;
      if (track === null) {
        // ⚠ NOT `res.name` — THE ROW IS NOT NAMED AFTER THE BOARD. It used to be,
        // and the gutter then read "TTBB_E…" for the one row whose kind matters
        // most, with no hint that it was the storyboard row at all. Which board
        // the panels came from is on every card and in the notice below; what the
        // label has to say is which of the four kinds this row is.
        const lane = pictureLane("board_image");
        if (!lane) return; // no room; `pictureLane` said so
        lanes = [...layers, lane];
        track = lane.track;
        // The row is created here rather than by `addPictureTrack`, so it has to
        // claim its place in the saved order here too — see `seatNewLane`.
        seatNewLane(layerTokenOf(lane));
      }
      // ⚠ ONTO A TRACK, AT A TIME. `insertPictures` gives the newcomers explicit
      // starts and ripples what follows them on that row.
      const placed = insertPictures(framesRef.current, added, undefined, track, null);
      const fresh = new Set(added.map((f) => f.id));
      const next = placed.map((f) =>
        fresh.has(f.id) ? { ...f, url: `/animatics/${animaticId}/frame/${f.id}` } : f
      );
      // ⚠ THE LIBRARY GOES UP IN THE SAME WRITE AS THE FRAMES. A panel imported
      // and then deleted before the debounce fired would otherwise be gone from
      // both lists — the one case the library exists to prevent. Same reasoning as
      // the row below it: everything one action creates is saved by one PUT.
      const cards = mergeAssets(
        assets,
        added.map((f) => assetFromFrame(f, newId()))
      );
      // The write these pictures are served out of. `url` is excluded from the
      // saved shape (`frameForSave`), so carrying it here changes nothing about
      // what is sent — and it means the frames arrive on screen already pointing
      // at a route that answers.
      await flush({ frames: next, assets: cards, ...(lanes ? { layers: lanes } : {}) });
      setAssets(cards);
      if (lanes) setLayers(lanes);
      framesRef.current = next;
      setFrames(next);
      setBoardImport(null);
      setNotice(
        `${added.length} panel${added.length === 1 ? "" : "s"} imported from “${
          res.title || "storyboard"
        }”.${
          res.panels_only
            ? " Its key poses would have overflowed this project, so one frame per shot came across."
            : ""
        }`
      );
      // Where the panels landed — the pane that now holds them, opened for them.
      setMediaTab("media");
      openGroup("media:frames");
    } catch (e) {
      setBoardError(e.message);
    } finally {
      setBoardBusy(false);
    }
  }

  // Opening the OS file dialog is the whole action for the audio layer, so both
  // entry points (the tools row and the ＋ on the Audio track) share this.
  function openAudioPicker() {
    audioInputRef.current?.click();
  }

  // ONE way in for everything. An image becomes an overlay on the Images lane,
  // footage becomes a clip in the cut, an audio file becomes a track — the user
  // shouldn't have to pick the right button first, and used to face three of
  // them for the same job.
  /**
   * @param rowKind which kind of picture row this landed on, or "" for "no
   *                particular row" — the Media pane's own button and the drop
   *                target beside it, where the file's own type picks the row.
   *
   * ⚠ A FILE ONLY EVER LANDS ON A ROW OF ITS OWN KIND. With `rowKind` given, a
   * file the row does not take is REFUSED and named; without one, footage goes to
   * a Video row — creating that row if the project has none. That is what makes
   * "image moves only in image layers, video moves only in video layers" true of
   * importing as well as of dragging.
   *
   * ⚠ AN IMAGE WITH NO ROW NAMED GOES TO THE "Images" LANE, as an OVERLAY —
   * asked for as "when user uplaod media or layer so image shoul come in image
   * layer not sitll layer". It used to find-or-CREATE a Stills row, which sat
   * ABOVE the storyboard rows, so one photo blanked out the opening seconds of
   * the board. ⚠ WITH A ROW NAMED IT STILL GOES IN THE CUT: the Video row takes
   * footage and full-frame stills alike (`ROW_TAKES.video`), and pressing ITS ＋
   * is aiming at that row rather than asking the editor to choose.
   */
  async function addAssets(fileList, insertAt, track = 0, atMs = null, rowKind = "") {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    let images = sortFiles(files.filter((f) => kindOf(f) === "image"));
    const audios = files.filter((f) => kindOf(f) === "audio");
    let videos = files.filter((f) => kindOf(f) === "video");
    const others = files.filter((f) => kindOf(f) === "other");

    // --- Which row does each half go on? ------------------------------------
    // Audio never came through here needing a row, so it is untouched below.
    let imageTrack = track;
    let videoTrack = track;
    // The pictures that go to the overlay lane instead of into the cut. Split
    // out of `images` rather than handled where they are found, so the counted-up
    // notice at the bottom of this function still speaks for every file that
    // came in.
    let overlayImages = [];
    const refused = [];
    if (rowKind) {
      // A row was named, so anything it does not take is refused rather than
      // quietly redirected: you pressed ＋ on THAT row. ⚠ AND A PICTURE STILL HAS
      // A ROW THAT TAKES IT — the plain Video row, which has always held footage
      // and full-frame stills alike (see `ROW_TAKES` and the ＋ Add layer menu).
      // What went is the row that got made FOR you behind your back.
      if (images.length && !rowTakesFile(rowKind, "image")) {
        refused.push(`${images.length} image${images.length === 1 ? "" : "s"}`);
        images = [];
      }
      if (videos.length && !rowTakesFile(rowKind, "video")) {
        refused.push(`${videos.length} video clip${videos.length === 1 ? "" : "s"}`);
        videos = [];
      }
    } else {
      // ⚠ NO ROW NAMED MEANS THE IMAGES ARE OVERLAYS. This is the Media pane's
      // own ＋ and the drop card beside it, and it used to find-or-CREATE a Stills
      // row for them — a row that sat above the storyboard rows, so one photo
      // blanked out the opening seconds of the board. They go to the default
      // "Images" lane now, which composites them over the cut: "when user uplaod
      // media or layer so image shoul come in image layer not sitll layer".
      overlayImages = images;
      images = [];
      if (videos.length) {
        // Footage still finds (or is given) a row of its own. ⚠ THE ROW IS MADE
        // BEFORE THE UPLOAD so its track number is known here — `addPictureTrack`
        // hands the number back precisely because `videoTracks` will not have
        // rebuilt yet.
        const row = rowOfKind("video");
        videoTrack = row ? row.track : addPictureTrack("video", { quiet: true });
        if (videoTrack === null) videos = [];
      }
    }
    if (refused.length) {
      setNotice(
        `${refused.join(" and ")} skipped — ${
          ROW_KIND[rowKind]?.name || "that row"
        } takes ${ROW_KIND[rowKind]?.takes.join(" and ") || "nothing"}. Use that kind's own row.`
      );
    }

    // ⚠ ON THE DEFAULT IMAGES LANE (`layerId: ""`), never on a numbered one. A
    // file dropped ON an image layer does not come through here at all — that is
    // `dropAsset`, which knows which lane it landed on and calls
    // `addOverlayFiles` with it.
    if (overlayImages.length) {
      await addOverlayFiles(overlayImages, "", atMs === null ? undefined : atMs);
    }
    const addedImages = images.length ? await addFiles(images, insertAt, imageTrack, atMs) : 0;
    // A video file becomes a CLIP on the video track, alongside the stills —
    // one timeline, three kinds of clip, which is the whole point of the phase.
    //
    // ⚠ AFTER THE IMAGES, not at the same index. Both used to be handed the same
    // `insertAt`, so dropping a still and a clip together put the clip IN FRONT
    // of the still — the second insert used an index the first one had already
    // shifted. `insertAt` is undefined for an append, where there is nothing to
    // shift.
    // ⚠ ONLY WHEN BOTH LANDED ON THE SAME ROW does the image count shift the
    // video's index — on separate rows the two inserts are independent.
    const sameRow = imageTrack === videoTrack;
    const videoAt =
      insertAt === undefined ? undefined : insertAt + (sameRow ? addedImages : 0);
    let addedVideos = 0;
    // ⚠ AND THE VIDEOS GO AFTER THE IMAGES IN TIME AS WELL AS IN THE LIST: the
    // stills just consumed the stretch starting at `atMs`, so re-using it would
    // stack the clip on top of them. Only matters on an end-of-track drop, which
    // is the only place `atMs` is read.
    if (videos.length) {
      addedVideos = await addVideoClips(
        sortFiles(videos),
        videoAt,
        videoTrack,
        sameRow && addedImages ? null : atMs
      );
    }
    // Each audio file becomes its OWN track — dropping music and a voiceover
    // together gives you two layers, which is the point of the layer control.
    const room = MAX_AUDIO_TRACKS - audioFileCount();
    const taking = audios.slice(0, Math.max(0, room));
    for (const file of taking) await addAudioTrack(file);

    const said = [];
    if (images.length) said.push(`${images.length} image${images.length === 1 ? "" : "s"}`);
    // ⚠ NAMED, NOT JUST COUNTED. An overlay lands on a DIFFERENT row from the one
    // the press was on and a third of the frame wide, so a notice that only said
    // "Added 3 images" would leave the reader looking at the row they aimed at.
    if (overlayImages.length)
      said.push(
        `${overlayImages.length} image${overlayImages.length === 1 ? "" : "s"} to Images`
      );
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
    if (images.length || overlayImages.length || addedVideos) setMediaTab("media");
    // An overlay's upload lists beside a still's — `addOverlayFiles` puts one
    // card in the library per picture — so both open the same section.
    if (images.length || overlayImages.length) openGroup("media:images");
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
      // ⚠ THE FILE JOINS THE LIBRARY, and it is the file and not the clip. A
      // razored voiceover is four clips reading one recording, so the library
      // keeps ONE card — keyed by upload id (`assetKey`) — and deleting every
      // clip of it still leaves the recording there to drag back out.
      addToLibrary([
        assetFromAudio({
          id: res.upload_id,
          upload_id: res.upload_id,
          filename: res.filename || file.name,
          duration_ms: durationMs,
        }, newId()),
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
      setNotice(`That's the limit — a project can hold ${MAX_AUDIO_TRACKS} audio tracks.`);
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

  // ------------------------------------------------------------ the Director
  // 🎬 Make Video. See `animatic/agent/` — the runner, the action registry and
  // the fence all live there, and NONE of it edits the timeline itself: every
  // verb calls one of the functions above, which is why the AI obeys the
  // one-transition-per-cut rule and the effects cap without knowing they exist.
  //
  // ⚠ TWO REFS AND NOTHING ELSE CROSSES THE LINE. The runner cannot hold
  // `frames` — it reads a step, edits, and the NEXT step has to see the result,
  // so anything it captured at mount would be a document 60 edits stale. It gets
  // `readCtx()`, which reads the refs below, and the refs are refreshed on every
  // render. See the header of `useDirectorRun.js` for why the loop is a timer.
  const [directorOpen, setDirectorOpen] = useState(false);
  // ⚠ THE WHOLE DOCUMENT, FOR REVERT — the same object `useUndoStack` records
  // and `applySnapshot` restores. Kept as a ref rather than passed as a value
  // because the snapshot is taken when RUN is pressed, not when the panel
  // opened: between those two moments the user can still edit, and reverting to
  // a document from before their edits would throw away work the Director never
  // touched.
  const directorDocRef = useRef(null);
  directorDocRef.current = doc;
  const directorCtxRef = useRef({});
  directorCtxRef.current = {
    frames,
    starts,
    texts,
    shapes,
    overlays,
    transitions,
    audioTracks,
    layers,
    totalMs,
    fps: settings.fps,
    // ⚠ FOR THE BRIEF, NOT FOR A VERB. `boardFrom` turns the read-model into the
    // description the model is given, and a film's name and shape are part of
    // what it is. No action in the registry reads either.
    title,
    aspectRatio: settings.aspect_ratio,
  };
  // ⚠ A TAKE IS NOT A SHOT, AND THIS IS THE ONE PLACE THAT IS DECIDED.
  //
  // `attachVeoClip` appends a finished Veo render to `frames` as an ordinary
  // clip on the Storyboard video row. So a project that has been animated — by
  // hand, or by the Director's own phase C one pass earlier — hands the runner a
  // list of 48 panels AND 48 takes, and every rule downstream that counts shots
  // reads a 96-shot film that does not exist: `housePlan` takes the median of a
  // list half of which is footage, `shotIndex` accepts "shot 61", and the preview
  // table lists every panel twice.
  //
  // ⚠ IT IS FILTERED HERE RATHER THAN INSIDE THE AGENT because `starts` has to
  // be filtered at the SAME indices — `frameSpans` knows about tracks, explicit
  // `start_ms` and clips that have been dragged, and a second layout derived
  // from the filtered list would disagree with the timeline on screen the moment
  // anything is out of list order. `shotRow` does both together.
  const readDirectorCtx = useCallback(
    () => ({
      ...directorCtxRef.current,
      ...shotRow(directorCtxRef.current.frames, directorCtxRef.current.starts),
      // `add_transition` sets a length on the record it just made, and that
      // record does not exist in the ctx it was handed — it was created one
      // React commit ago. This is the one place a verb needs to read PAST its
      // own snapshot, so it is the one place that gets a live reader.
      readTransitions: () => directorCtxRef.current.transitions,
    }),
    []
  );
  // ⚠ THE NAMES ARE THE CONTRACT — `ACTION_API` in `agent/actions.js` lists
  // exactly these, and `tests/editor_director_check.py` asserts this object
  // supplies every one of them. A verb cannot reach anything not named here.
  const directorApiRef = useRef({});
  directorApiRef.current = {
    patchFrame,
    setAllDurations,
    seek,
    selectOnly,
    addTransitionAtCut,
    patchTransition,
    deleteTransition,
    addEffectToClip,
    addText,
    patchText,
    deleteText,
    addShape,
    patchShape,
    deleteShape,
    addLayer,
    patchTrack,
    addCrossfade,
    laneSiblings,
  };
  // ⚠ KEYED OFF `ACTION_API`, NOT OFF THE OBJECT ABOVE. Building it from its own
  // keys would make the bag whatever the object happens to hold, so a function
  // renamed on one side and not the other would produce a bag that is missing a
  // name and reports nothing. Reading the list means a missing name arrives as
  // `undefined`, which is what `missingApi` in `useDirectorRun` detects and the
  // panel says out loud.
  //
  // Each entry is a thunk through the ref so the runner always calls the CURRENT
  // render's closure — half these functions capture `frames`, and one captured at
  // mount would be editing a document 60 steps stale.
  const directorApi = useMemo(
    () =>
      Object.fromEntries(
        ACTION_API.filter((name) => typeof directorApiRef.current[name] === "function").map(
          (name) => [name, (...args) => directorApiRef.current[name](...args)]
        )
      ),
    []
  );
  // ⚠ THE MODEL IS ONE FUNCTION, AND IT IS OPTIONAL. `useDirectorRun` falls back
  // to the deterministic Phase 0 planner whenever this throws — a backend that
  // is not running, no credentials, a train — and the preview says which planner
  // it is showing. So the 🎬 button never breaks; it sometimes does less.
  //
  // ⚠ IT SENDS THE BOARD AND THE MANIFEST FROM HERE. The document on screen is
  // ahead of the last autosave, and `capabilities()` is derived from the tables
  // the renderers read — see the header of `director.py` on why neither may be
  // rebuilt server-side.
  const askDirectorModel = useCallback(
    (payload) => api.directorPlan(animaticId, payload),
    [animaticId]
  );
  // ⚠ THE INTERRUPTED PASS, IF THERE IS ONE — the record and the clips together,
  // because neither answers the question on its own. The record says what the
  // run MEANT to render; the clips say what was actually paid for; `outstanding`
  // in `veo_pass.js` is the difference and the runner does that arithmetic. All
  // this has to do is hand over both, and only while the record still says the
  // pass never finished.
  const directorPending = useMemo(
    () =>
      directorRun && (directorRun.status || "") === "running"
        ? { run: directorRun, clips: veoClips }
        : null,
    [directorRun, veoClips]
  );
  // ⚠ PHASE B, THROUGH TWO THUNKS, and the ref is doing two jobs at once. The
  // stable-identity one is the same reason `directorApi` is a thunk: the runner
  // must always call the CURRENT render's closure. The other is ORDERING — both
  // passes live further down this file, beside the 🎙 dialog's poll whose
  // re-read (`absorbSpeech`) they share, and naming them here would read a
  // `const` before it exists.
  const directorSpeakRef = useRef(null);
  const directorScriptRef = useRef(null);
  const directorSpeak = useCallback((payload) => directorSpeakRef.current(payload), []);
  const directorReadScript = useCallback(() => directorScriptRef.current(), []);
  // ⚠ PHASE C, THROUGH THE SAME KIND OF THUNK AND FOR BOTH THE SAME REASONS:
  // the runner must always call the CURRENT render's closure (`runDirectorVeoPass`
  // captures `frames` through refs the poll then re-reads), and the pass itself
  // lives further down beside `reconcileVeoClips`, which it shares with ✨ Animate.
  const directorRenderRef = useRef(null);
  const directorRenderPass = useCallback(
    (payload) => directorRenderRef.current(payload),
    []
  );
  const directorVeoQuote = useCallback(
    (payload) => api.directorVeoQuote(animaticId, payload),
    [animaticId]
  );
  const directorVeoStart = useCallback(
    (payload) => api.directorVeoStart(animaticId, payload),
    [animaticId]
  );
  const directorVeoState = useCallback(
    (payload) => api.directorVeoState(animaticId, payload),
    [animaticId]
  );
  const director = useDirectorRun({
    readCtx: readDirectorCtx,
    api: directorApi,
    applySnapshot,
    docRef: directorDocRef,
    onNotice: setNotice,
    askModel: askDirectorModel,
    language: settings.language || "",
    readScript: directorReadScript,
    speak: directorSpeak,
    // ------------------------------------------------------------- phase C
    quoteVeo: directorVeoQuote,
    startVeo: directorVeoStart,
    renderPass: directorRenderPass,
    endVeo: directorVeoState,
    // ⚠ THE PROJECT'S OWN RENDER SETTINGS, UNTOUCHED EXCEPT FOR THE LENGTH. Tier,
    // resolution and whether Veo generates sound are the user's decisions and
    // live in the ✨ Animate dialog; the Director picks the LENGTH per shot,
    // because that is a decision about the cut rather than about the budget.
    veoRender: animateRender,
    veoClips,
    // ⚠ THE RESUME OFFER. A `director_run` still saying "running" on a project
    // that has just been loaded means a pass was interrupted — see `resumeVeo`.
    pendingVeo: directorPending,
  });

  // Which languages have a description written for them, and which backend is
  // wired up. Free — no model call — and fetched only when the panel is opened
  // for the first time, because the editor opens far more often than 🎬 is
  // pressed. An empty list is harmless: the picker still offers "Something
  // else…", which is free text and is what actually makes any language work.
  const [directorLanguages, setDirectorLanguages] = useState([]);
  function openDirector() {
    setDirectorOpen(true);
    director.open();
    if (!directorLanguages.length) {
      api
        .directorConfig()
        .then((cfg) => setDirectorLanguages(cfg?.languages || []))
        .catch(() => {});
    }
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

  // The row records, for the same reason and read by the same callback. ⚠ THE VEO
  // POLL KEYS ON `animating` ALONE (see the note on that state), so the
  // `reconcileVeoClips` it holds can be several renders old — reading rows out of
  // that closure would answer "there is no Storyboard video row" long after one
  // was made, and make a second one.
  useEffect(() => {
    layersRef.current = layers;
  }, [layers]);

  // ⚠ THERE IS DELIBERATELY NO REF FOR THE REST OF THE DOCUMENT. There was one,
  // filled by an effect, and `attachVeoClip` read the captions, the shapes, the
  // overlays and the audio out of it to carry them along when a take makes room.
  // It could be EMPTY (a project straight out of the load promise — no effect
  // has run) or several renders stale (the Veo poll is keyed on `animating`
  // alone), and rippling an empty list is a SILENT no-op that looks exactly like
  // "nothing needed to move". Reported twice; the second time as "now all good
  // audio and image but Caption not move". Every ripple now goes through React's
  // own functional setters, which are handed the live list at commit time — see
  // `RIPPLED_LISTS` in `ripple.js`.

  /**
   * The board's own words for one shot, as the first draft of its motion prompt.
   *
   * ⚠ THE DESCRIPTION ONLY, which is the same draft `_starting_prompt` builds in
   * `server/videos.py` for the Image-to-AI-Video workspace. Two workflows
   * animating the same panel should open on the same wording, and the camera and
   * location lines are about how the STILL is framed — Veo is being asked what
   * moves, and handing it the framing invites it to re-frame the shot.
   */
  const boardDraftPrompt = (panel) => (panel?.description || "").trim();

  /**
   * The shot's spoken lines, written the way a Veo prompt wants to hear them.
   *
   * Quoted, because the words have to arrive as SPEECH rather than as more
   * description — an unquoted line reads as another instruction about the scene.
   * A line whose speaker the breakdown could not attribute says "A voice", the
   * same fallback the board's own dialogue block uses.
   */
  const spokenPromptBlock = (dialogue) =>
    (dialogue || [])
      .map((d) => ({ who: (d?.character || "").trim(), line: (d?.line || "").trim() }))
      .filter((d) => d.line)
      .map((d) => `${d.who || "A voice"} says: "${d.line}"`)
      .join("\n");

  function openAnimate(frameId) {
    const frame = frames.find((f) => f.id === frameId);
    setAnimateFor(frameId);
    // The frame's label is a starting draft, not a finished prompt — a label
    // says what the shot IS and Veo wants to hear what MOVES — but an empty box
    // is worse, and the placeholder explains the difference.
    const label = frame?.label || "";
    // ⚠ A GENERATED IN-BETWEEN SHOT CARRIES ITS OWN WORDING, and it is the only
    // description of that shot there is: it has no panel — it is not on the
    // board — so the free read below finds nothing for it and would leave the
    // box on "After Shot 4", which is a name. `src.prompt` is what it was drawn
    // from, and it does the same job here that the panel's description does for
    // a panel. Applied synchronously because it needs no call.
    const seed = (frame?.src?.prompt || "").trim() || label;
    setAnimatePrompt(seed);
    setAnimateConfirm(null);
    setAnimatePanel(null);
    setAnimateSpeak(false);
    animateSpokenRef.current = "";

    // ⚠ THE BOARD IS THE BETTER DRAFT, AND IT IS A FREE READ. "Shot 1" is a
    // label, not a prompt; the panel this clip was drawn from already carries a
    // sentence describing the shot AND who says what in it, and until now the
    // user retyped both. Reported as "so user see prompt too so user control
    // prompt … and dialouge like generted in last Storyboard panel".
    // Same endpoint the redraw pane reads (`getFramePanel`) — one owner-checked
    // route for "what does the board say about this clip", not two.
    animatePanelReq.current = frameId;
    api
      .getFramePanel(animaticId, frameId)
      .then((info) => {
        // A second ✨ Animate opened while this was in flight: its own read owns
        // the box now, and filling it here would be the wrong shot's wording.
        if (animatePanelReq.current !== frameId) return;
        if (!info?.storyboard_id) return;
        setAnimatePanel(info);
        const draft = boardDraftPrompt(info);
        if (!draft) return;
        // ⚠ ONLY OVER THE DRAFT WE PUT THERE. The read is asynchronous and the
        // box is focused the whole time, so anything the user has already typed
        // outranks the draft — arriving late and overwriting it would be the
        // worst thing this could do. Tested against `seed`, not the label: a
        // generated shot's own wording is in the box, and a late panel read
        // must not overwrite that either.
        setAnimatePrompt((current) => (current.trim() === seed.trim() ? draft : current));
      })
      .catch(() => {
        /* No board, no draft. The label stays and the dialog is unchanged —
           this read is an improvement on the prompt, never a precondition. */
      });
  }

  /**
   * Tick / untick "have Veo speak these lines".
   *
   * ⚠ IT EDITS THE PROMPT BOX, IT DOES NOT SET A FLAG. The dialog exists so the
   * user can see and control exactly what is sent, so the lines are written into
   * the text they are already reading rather than bolted on at submit time.
   * Unticking takes back the block IT added and nothing else — an edited block
   * no longer matches, and the user's words stay.
   */
  function toggleAnimateSpeak(on) {
    const block = spokenPromptBlock(animatePanel?.dialogue);
    if (!block) return;
    if (on) {
      setAnimatePrompt((p) => {
        const base = (p || "").trimEnd();
        return base ? `${base}\n\n${block}` : block;
      });
      animateSpokenRef.current = block;
      setAnimateSpeak(true);
      // ⚠ VEO CANNOT SAY A LINE WITH THE SOUND OFF. Leaving the checkbox as it
      // was would render mouths moving in silence and bill for it, so asking for
      // dialogue turns sound on — and the note under the box says it costs more,
      // because this is the one dialog in the editor where money moves.
      setAnimateRender((r) => (r.generate_audio ? r : { ...r, generate_audio: true }));
    } else {
      const added = animateSpokenRef.current;
      setAnimatePrompt((p) => {
        const trimmed = (p || "").trimEnd();
        return added && trimmed.endsWith(added)
          ? trimmed.slice(0, trimmed.length - added.length).trimEnd()
          : p;
      });
      animateSpokenRef.current = "";
      setAnimateSpeak(false);
    }
  }

  const veoFor = (frameId) =>
    veoClips.filter((c) => c.frame_id === frameId).slice(-1)[0] || null;

  // The shot the ✨ Animate dialog is open on — read for the NAME above its
  // prompt box. ⚠ Looked up on every render rather than captured in
  // `openAnimate`, so renaming a clip while the dialog is open cannot leave the
  // dialog naming it by a name it no longer has.
  const animateFrame =
    animateFor === null ? null : frames.find((f) => f.id === animateFor) || null;

  // ✨ Animate with Veo has a SECOND way in now — the timeline's own add row,
  // beside ＋ Add layer — and a button standing there has no selection to lean on
  // the way the Properties pane's does. THE SELECTED SHOT IF THERE IS ONE, ELSE
  // THE SHOT UNDER THE PLAYHEAD: the same rule ＋ Text follows, so every button in
  // that row means "this shot" and means it the same way.
  // ⚠ IT PICKS A TARGET, IT DOES NOT WIDEN WHAT MAY BE ANIMATED. Both buttons
  // call the one `openAnimate`, which opens the priced dialog and nothing else —
  // what a render costs and what it refuses stays with the server
  // (`_animate_targets` in `server/animatics.py`).
  const veoTarget = selectedFrame || currentFrame;
  const veoTargetClip = veoTarget ? veoFor(veoTarget.id) : null;

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



  /**
   * The still a video render starts from — uploaded the moment it is chosen.
   *
   * ⚠ UPLOADED NOW, NOT AT RENDER TIME, so the render request carries an id
   * rather than a file and the server can check the picture exists BEFORE it
   * quotes a price for it. It is an ordinary animatic image upload, which also
   * means it survives in the project if the user closes the dialog and comes
   * back.
   */
  async function pickVideoSource(fileList) {
    const file = Array.from(fileList || []).find((f) => kindOf(f) === "image");
    if (!file) {
      setNotice("A starting frame has to be a picture — that file isn't one.");
      return;
    }
    setVidGenUploading(true);
    setError("");
    try {
      const res = await api.uploadAnimaticImages(animaticId, [file]);
      const item = (res.items || [])[0];
      if (!item?.upload_id) throw new Error(res.rejected?.[0] || "That picture could not be read.");
      const path = `/animatics/${animaticId}/media/${item.upload_id}`;
      // A small proxy, not the full-size picture: this is a 3rem thumbnail and
      // the original may be 4K. Same `maxEdge` the Media library's cards use.
      let blob = "";
      try {
        blob = await api.fetchAnimaticMedia(path, LIBRARY_MAX_EDGE);
      } catch {
        /* No thumbnail is a cosmetic loss — the render still has its picture. */
      }
      setVidGenSource((was) => {
        // ⚠ RETIRE THE ONE IT REPLACES. Picking a second starting frame without
        // this leaks an object URL per pick, for the life of the tab.
        if (was?.blob) retireBlob(was.blob);
        return {
          upload_id: item.upload_id,
          name: item.filename || "starting frame",
          url: path,
          blob,
        };
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setVidGenUploading(false);
    }
  }

  /**
   * Ask what the video would cost. FREE — this is the call that fills the
   * confirm dialog, and no button in this editor renders anything directly.
   */
  async function askToGenerateVideo() {
    if (!vidGenPrompt.trim() || vidGenBusy) return;
    setVidGenBusy(true);
    setError("");
    try {
      const estimate = await api.estimateGenerateVideo(animaticId, {
        prompt: vidGenPrompt.trim(),
        sourceUploadId: vidGenSource?.upload_id || "",
        render: vidGenRender,
      });
      setVidGenConfirm({ estimate });
    } catch (e) {
      setError(e.message);
    } finally {
      setVidGenBusy(false);
    }
  }

  /** The only place the ✨ Video spends. Reached solely from the confirm. */
  async function doGenerateVideo() {
    if (!vidGenConfirm) return;
    setVidGenBusy(true);
    setVidGenConfirm(null);
    try {
      await api.generateAnimaticVideo(animaticId, {
        prompt: vidGenPrompt.trim(),
        sourceUploadId: vidGenSource?.upload_id || "",
        render: vidGenRender,
      });
      setImgGen(null);
      // ⚠ THE SAME `animating` FLAG THE ✨ ANIMATE PATH USES, which is what puts
      // this render on the SAME poll and the same self-heal. A second mechanism
      // for "a Veo render is in flight" is a second thing that can lose one.
      setAnimating(true);
      setAnimateProgress({ percent: 0, message: "Starting…" });
      setNotice("Generating a video with Veo — this takes a couple of minutes.");
    } catch (e) {
      setError(e.message);
    } finally {
      setVidGenBusy(false);
    }
  }

  // --------------------------------------- generate a shot beside another one
  // ⚠ THE SECOND PATH IN THIS EDITOR THAT CALLS A MODEL FROM THE TIMELINE, and
  // unlike ✨ Animate it is not metered per second, so it does not go through a
  // priced confirm — it is one drawing, the same call the board's own Regenerate
  // makes. What it DOES share is the two-step shape: the dialog writes the
  // wording (and ✨ can write it for you, a cheap text call), and nothing is
  // drawn until the button under it is pressed.
  //
  // ⚠ AND THE STORYBOARD IS NEVER TOUCHED. The picture comes back as an ordinary
  // animatic upload carrying the board id and a `shot_id` of its own, which is
  // what puts it on the Storyboard images row without claiming a panel index
  // that belongs to a real panel. The server's `generate_neighbour_shot` note
  // carries the whole of that reasoning.

  /**
   * Open the dialog for "the shot before / after this clip".
   *
   * ⚠ IT FLUSHES FIRST. The neighbours are worked out SERVER SIDE, off the saved
   * project — so a shot dragged into a new place a moment ago has to be on the
   * server before the question "what does this sit between?" is asked, or the
   * suggestion is written between two shots that no longer sit either side of
   * anything. Same rule `askToAnimate` and `askForSpeech` follow.
   */
  async function openGenerateShot(clip, side) {
    if (!clip?.id) return;
    if (frameLocked(clip)) {
      setNotice("That row is locked — unlock it in the gutter to add a shot to it.");
      return;
    }
    const key = `${clip.id}:${side}`;
    shotGenReq.current = key;
    setShotGen({ frameId: clip.id, side });
    setShotGenCtx(null);
    setShotGenPrompt("");
    setShotGenAspect("");
    setShotGenSeconds(SHOT_GEN_DEFAULT_SECONDS);
    // Folded again on every open. A note pinned open on the last shot would
    // otherwise greet the next one with three lines it has already been read.
    setShotGenNote(false);
    setError("");
    try {
      await flush();
      const info = await api.getNeighbourShot(animaticId, clip.id, side);
      // A second menu was opened while this was in the air: its own read owns
      // the dialog now, and filling it here would describe the wrong gap.
      if (shotGenReq.current !== key) return;
      setShotGenCtx(info);
      // ⚠ ONLY OVER THE EMPTY DEFAULT. The read is asynchronous and the shape
      // picker is live the whole time, so a ratio the user has already chosen
      // outranks the board's default arriving late.
      setShotGenAspect((current) => current || info.aspect_ratio || "");
    } catch (e) {
      if (shotGenReq.current !== key) return;
      setError(e.message);
    }
  }

  /**
   * ✨ — have the model write the missing beat.
   *
   * A TEXT call, a fraction of the price of a drawing, and it is the whole
   * reason the box opens EMPTY rather than on a draft: there is no panel behind
   * a shot that does not exist yet, so there is nothing to prefill it with. What
   * is already typed goes up as STEERING and comes back folded into the answer —
   * which is why this replaces the box rather than appending to it, and why the
   * line under the button says so.
   */
  async function suggestShotPrompt() {
    const target = shotGen;
    if (!target || shotGenAsking) return;
    setShotGenAsking(true);
    setError("");
    try {
      const res = await api.suggestNeighbourShot(animaticId, target.frameId, {
        side: target.side,
        notes: shotGenPrompt.trim(),
      });
      // The dialog may have been closed, or moved to the other side of the clip,
      // while this was in the air.
      if (shotGenReq.current !== `${target.frameId}:${target.side}`) return;
      const written = (res?.description || "").trim();
      // ⚠ NEVER BLANK THE BOX. An empty answer is a failed suggestion, and
      // taking the user's own words away as well would be the worst outcome.
      if (written) setShotGenPrompt(written);
      else setNotice("The model had nothing to add — try describing the shot yourself.");
    } catch (e) {
      setError(e.message);
    } finally {
      setShotGenAsking(false);
    }
  }

  /**
   * Draw it, and put it in the cut.
   *
   * The server makes the picture and hands back a CLIP; where it goes is decided
   * here, because that is a timeline question — the same split the image, video
   * and board imports already follow.
   */
  async function doGenerateShot() {
    const target = shotGen;
    if (!target || !shotGenPrompt.trim() || shotGenBusy) return;
    setShotGenBusy(true);
    setError("");
    try {
      // The neighbours are resolved server side off the SAVED project, and the
      // dialog has been open long enough for the timeline to have moved.
      await flush();
      const res = await api.generateNeighbourShot(animaticId, target.frameId, {
        side: target.side,
        description: shotGenPrompt.trim(),
        aspectRatio: shotGenAspect,
        durationMs: shotGenSeconds * 1000,
      });
      if (!res?.frame?.id) throw new Error("The server drew nothing.");
      placeGeneratedShot(res.frame, target.frameId, target.side);
      setShotGen(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setShotGenBusy(false);
    }
  }

  // THE SHAPES THE MEDIA ✨ OFFERS — the same list, plus the PROJECT's shape
  // when that is not on it. A film can be 4:5 or 21:9, and the first thing
  // anyone draws for it is usually the shape of the film.
  const imgGenAspects = (() => {
    const ids = BOARD_ASPECTS.map((a) => a.id);
    const own = imgGenAspect || settings.aspect_ratio;
    return own && !ids.includes(own) ? [...ids, own] : ids;
  })();

  // THE SHAPES THE GENERATE DIALOG OFFERS — the storyboard's list, plus the
  // board's OWN ratio when that is not on it. A board can be 3:4 or anything
  // else the workflow's Custom field accepted, and a picker that silently
  // showed it as 16:9 would be lying about the picture it is about to draw.
  // Same rule `knownAspect` exists for on the video side.
  const shotGenAspects = (() => {
    const ids = BOARD_ASPECTS.map((a) => a.id);
    const own = shotGenCtx?.aspect_ratio || shotGenAspect;
    return own && !ids.includes(own) ? [...ids, own] : ids;
  })();

  /**
   * THE NEW SHOT GOES IN, AND THE WHOLE FILM MOVES OVER FOR IT.
   *
   * ⚠ ONE EDIT, ONE UNDO, AND EVERY LIST. This is the same job `attachVeoClip`
   * does when a take is longer than the shot it was made from, and it is done
   * the same way for the same reasons: `insertShotBeside` places the clip and
   * makes room on ITS row, `renderShifts` turns what that did into a step map,
   * and the map carries the captions, the typed text, the shapes, the overlays
   * and the voiceover along with it. Asked for as "generated video and come in
   * same layer after same setected clip and move all clip of all layer".
   *
   * ⚠ THE MAP IS BUILT FROM THE AFFECTED ROW ALONE. `renderShifts` reads every
   * `board_image` clip in the project, and a SECOND storyboard row — which did
   * not move — would contribute a run of zero-shift points that `shiftAt` then
   * reads as "nothing after this owes anything", cancelling the debt for every
   * caption past it. Filtering both lists to the one row this insert touched is
   * exact: `frameSpans` lays a track out from that track's clips alone, so a
   * clip's evaluated start is the same in the filtered list as in the whole one.
   *
   * ⚠ AND THE ROW IT PLACED IS `keep`, NOT "every board row". The clips on that
   * row are already standing at their NEW starts, so looking them up in a map
   * written in OLD time would add their debt a second time. Everything else that
   * came off a board — a Veo take sitting over a panel that just moved, a second
   * storyboard row — has NOT been placed by this pass and must be carried, which
   * is exactly what `rippleFrames`'s own board-wide skip would have prevented.
   */
  function placeGeneratedShot(built, neighbourId, side) {
    const before = framesRef.current || frames;
    const { frames: next, placed } = insertShotBeside(before, built, neighbourId, side);
    if (!placed) {
      setError("That shot is no longer on the timeline — the picture is safe in Media.");
      setAssets((list) => mergeAssets(list, [assetFromFrame(built, newId())]));
      return;
    }
    const onRow = (f) => frameTrack(f) === placed.track;
    const shifts = renderShifts(before.filter(onRow), next.filter(onRow));
    const keep = new Set(next.filter(onRow).map((f) => f.id));
    const settled = rippleFrames(next, shifts, keep);
    setFrames(settled);
    if (shifts.length) {
      // ⚠ FUNCTIONAL SETTERS, NEVER A REF — see the same five calls in
      // `attachVeoClip` for what reading these out of a ref cost. ⚠ ALL FIVE OF
      // `RIPPLED_LISTS`, and the only thing left to get wrong is forgetting one.
      //
      // ⚠ NO `coverGrownShots` HERE, and that is not an omission: nothing GREW.
      // A shot was added between two others, so every caption keeps the length
      // it had and simply happens later.
      setTexts((list) => rippleClips(list, shifts));
      setShapes((list) => rippleClips(list, shifts));
      setOverlays((list) => rippleClips(list, shifts));
      // ⚠ `newId` FROM HERE, because cutting the voiceover at the seam mints a
      // clip — ids are the editor's to hand out, not a pure module's.
      setAudioTracks((list) => rippleAudio(list, shifts, newId));
    }
    // ⚠ AND INTO THE LIBRARY, because a generated shot is like a paid render and
    // unlike an upload: deleting the clip must not be the thing that loses the
    // only copy of a picture that cost something to make.
    addToLibrary([assetFromFrame(placed, newId())]);
    framesRef.current = settled;
    setSelectedId(placed.id);
    setNotice(
      shifts.length
        ? `Added “${placed.label}” — everything after it moved along to make room.`
        : `Added “${placed.label}”.`
    );
  }


  // --------------------------------------------- draw any picture (Media ✨)
  /**
   * Open the Media pane's ✨.
   *
   * ⚠ NO CONTEXT CALL AND NOTHING TO FLUSH, unlike the shot dialog. That one has
   * to ask the server what its clip sits between, which is a question about the
   * SAVED project; this one has no neighbours, no board and no clip — the whole
   * brief is the sentence about to be typed. The only read is "which model", and
   * it is cached for the session because it cannot change under us.
   */
  function openImageGen(tab = "image") {
    setImgGen({ at: timeRef.current });
    setImgGenTab(tab === "video" ? "video" : "image");
    setImgGenPrompt("");
    // ⚠ THE VIDEO HALF IS RESET TOO, WHATEVER TAB IS OPENING. Both live in one
    // dialog, so a prompt left in the tab you did not open is a prompt you
    // cannot see — and on the Video tab that is a prompt you could pay for.
    setVidGenPrompt("");
    setVidGenSource(null);
    setVidGenConfirm(null);
    // The project's own shape is the sensible default for a picture going into
    // this film; the picker offers the rest.
    setImgGenAspect(settings.aspect_ratio || "16:9");
    setImgGenSeconds(IMG_GEN_DEFAULT_SECONDS);
    setImgGenNote(false);
    setError("");
    if (imageModel) return;
    api
      .getImageModel()
      .then(setImageModel)
      .catch(() => {
        /* The dialog says "…" for the model and still works. Naming the backend
           is an honesty, never a precondition. */
      });
  }

  /**
   * Draw it, then put it where a picture goes.
   *
   * ⚠ IT LANDS EXACTLY WHERE A DROPPED FILE LANDS — the library AND the overlay
   * "Images" lane — because the server hands back the same upload item a file
   * upload returns and `belongsOnImageLane` has always been the one place that
   * decides where a picture that is not a shot goes. Asked for as "come back in
   * media in image tab name under and in layer image layer come generated
   * image".
   *
   * ⚠ AND IT IS ONE EDIT. The card and the clip are written in the same tick, so
   * one Ctrl+Z takes back the placement — and the card survives it, which is the
   * whole point of the library: deleting the clip must never be what loses a
   * picture that cost something to make.
   */
  async function doGenerateImage() {
    const target = imgGen;
    if (!target || !imgGenPrompt.trim() || imgGenBusy) return;
    setImgGenBusy(true);
    setError("");
    // ⚠ THE DIALOG GOES NOW, NOT WHEN THE PICTURE LANDS, and that is the whole
    // of "keep one method user waiting". This editor has exactly one way of
    // showing a wait — the status strip at the foot, which the export, the Veo
    // renders, the captions, the voiceover and the re-block all report through —
    // and until now the image draw was the only thing in it that made you sit
    // inside a modal instead. The ✨ Video half already closed and reported in
    // the strip, so the two halves of one dialog behaved differently; this is
    // the image half joining the convention rather than the video half leaving
    // it. Everything this function still needs is captured above.
    setImgGen(null);
    try {
      const res = await api.generateAnimaticImage(animaticId, {
        prompt: imgGenPrompt.trim(),
        aspectRatio: imgGenAspect,
      });
      const item = res?.item;
      if (!item?.upload_id) throw new Error("The server drew nothing.");
      const name = (res.name || "").trim() || "Generated image";
      const overlay = {
        id: newId(),
        // ⚠ THE DEFAULT Images LANE (`layer_id: ""`), the same one `addAssets`
        // sends an uploaded picture to when no row was named. This ✨ is on the
        // pane, not on a row, so there is no row it could mean instead.
        layer_id: "",
        upload_id: item.upload_id,
        // Where the playhead was when the dialog opened. ⚠ Captured at OPEN and
        // not read now: a drawing takes a while, and a picture that landed
        // wherever the playhead had wandered to would be a surprise.
        start_ms: Math.max(0, Math.round(target.at || 0)),
        duration_ms: Math.max(MIN_MS, imgGenSeconds * 1000),
        x: 0.5,
        y: 0.5,
        // ⚠ THE WHOLE FRAME, WHERE AN UPLOADED OVERLAY OPENS AT 0.3. Those are
        // two different things arriving: a file you dropped in is usually an
        // inset — a logo, a cut-in — and starting it small is right, because you
        // are about to place it. This was DRAWN, to order, at the shape the
        // dialog asked for, so it is a picture of the film and it arrives as
        // one. Reported the moment it first landed: "image not comback and view
        // full in program panel … so set full panel in program not con and show
        // samll".
        // ⚠ FULL FRAME IS SAFE AT ANY SHAPE. Both renderers fit a picture INSIDE
        // its box preserving aspect ("contain" — `draw_overlays` in animatic.py
        // and its twin in `gl/compositor.js`), so a 1:1 image asked for in a 16:9
        // film fills the height and is letterboxed, never stretched.
        w: 1,
        h: 1,
        opacity: 1,
        rotation: 0,
        // Servable immediately, before the project is saved — the same url
        // `addOverlayFiles` gives a fresh upload.
        url: `/animatics/${animaticId}/media/${item.upload_id}`,
      };
      setOverlays((list) => [...list, overlay]);
      // ⚠ NAMED, unlike an uploaded overlay's card. A dropped file has a
      // filename to fall back on; this has the words that made it, which is what
      // the person who typed them will recognise it by. The server builds the
      // name so it is built once (`_image_name_from_prompt`).
      addToLibrary([
        assetFromFrame(
          {
            kind: "image",
            src: { kind: "upload", upload_id: item.upload_id },
            label: name,
            duration_ms: overlay.duration_ms,
          },
          newId()
        ),
      ]);
      selectOnly({ overlay: overlay.id });
      setNotice(
        // ⚠ IT NO LONGER SAYS "drag it to place it". It arrives covering the
        // frame now, so the next thing to do is not placement — and a notice
        // telling you to place something that is already where it belongs reads
        // as the editor not knowing what it just did.
        `Added “${name}” to Media and to the Images layer, covering the frame — drag its corners if you want it smaller.`
      );
    } catch (e) {
      setError(e.message);
      // ⚠ A FAILED DRAW GIVES THE DIALOG BACK, WITH THE WORDS STILL IN IT.
      // Closing before the wait is what puts this draw in the status strip with
      // everything else — but it also means a refusal would otherwise take the
      // user's prompt away with it, and asking someone to retype a sentence
      // because the model was busy is the worst moment to do it. Nothing was
      // cleared on the way out (only `openImageGen` resets these), so putting
      // the dialog back is enough to restore the prompt, the shape and the
      // length exactly as they were.
      setImgGen(target);
    } finally {
      setImgGenBusy(false);
    }
  }

  // Turn a finished render into a video clip ON the frame it came from. This is
  // an ordinary document edit — the clip bytes and the paid record are already
  // safe on the server, so the worst a failed attach costs is the attach.
  /**
   * A FINISHED VEO RENDER GOES ON THE STORYBOARD VIDEO ROW, ABOVE ITS PANEL.
   *
   * ⚠ IT USED TO REPLACE THE STILL IN PLACE — same clip, `kind` flipped to
   * "video" — and the panel was gone. Asked to be separated: "user want genearte
   * shortyborad image to video footage from VEO 3 model in editor then video
   * genarte and come in Storyboad video layer Sepratlty just up of image layer".
   * So it is a NEW clip on a row of its own, and the panel stays exactly where it
   * was underneath.
   *
   * ⚠ WHY "ABOVE" IS THE WHOLE POINT: a higher track draws over a lower one, so
   * the render is what plays — and 👁 on that row instantly shows the board again.
   * That makes the animation non-destructive and comparable, which replacing the
   * still could never be.
   *
   * ⚠ THE ORIGIN IS CARRIED OVER (`storyboard_id` / `index`) and that is what
   * makes the new clip read as `board_video` rather than as a file someone
   * dropped in — see `clipRowKind`. It is also what the Media pane groups by, so
   * an animated Shot 1 stays with the storyboard rather than jumping into Video.
   *
   * ⚠ AND IT STARTS WHERE THE PANEL STARTS, so the render plays at the moment the
   * shot was cut to. Its LENGTH is what Veo was asked for, which may differ from
   * the panel's hold — that is a real difference and it is left visible rather
   * than trimmed to match, because which of the two is right is the user's call.
   */
  /**
   * WHICH TRACK THE STORYBOARD VIDEO ROW IS, making it if there is none.
   *
   * ⚠ CALLED ONCE PER RECONCILE PASS AND THE ANSWER PASSED IN, not called per
   * clip. A Veo batch finishes as several ready clips at once, and each one
   * asking "is there a row yet?" would get "no" from the same pre-render state —
   * so a batch of four would try to make four rows, all claiming the same track.
   *
   * ⚠ AND IT READS THE REFS, not this render's `layers` / `frames`. The poll that
   * leads here holds a callback several renders old on purpose (see `animating`),
   * so the closure's idea of which rows exist is not to be trusted.
   */
  const boardVideoTrack = useCallback(() => {
    const rows = layersRef.current || [];
    const existing = rows.find((l) => l.kind === "board_video");
    if (existing && Number.isInteger(Number(existing.track))) return Number(existing.track);
    const used = [
      ...pictureTracks(framesRef.current),
      ...rows.filter((l) => ROW_KIND[l.kind]).map((l) => Number(l.track) || 0),
    ];
    const next = Math.max(...used) + 1;
    if (next > MAX_PICTURE_TRACK) {
      // ⚠ SAY THE RENDER IS SAFE. It is paid for and sitting on the server as an
      // ordinary upload; what failed is finding it a row.
      setNotice(
        `The render is safe, but there is no room for a ${ROW_KIND.board_video.name} row — ` +
          `a project can hold ${MAX_PICTURE_TRACK + 1} picture rows.`
      );
      return null;
    }
    const record = {
      id: newId(),
      kind: "board_video",
      name: rowKindName("board_video", 0),
      track: next,
    };
    // Written to the ref as well as to state, so a second call in this same tick
    // finds the row instead of making another.
    layersRef.current = [...rows, record];
    setLayers((list) => [...list, record]);
    seatNewLaneRef.current?.(laneTokenFor(record.kind, record.id, record.track));
    return next;
  }, []);

  const attachVeoClip = useCallback(
    (clip, track) => {
      if (!clip?.upload_id || track === null || track === undefined) return;
      const source = framesRef.current.find((f) => f.id === clip.frame_id);
      if (!source) return;
      // Where the panel sits, evaluated — a clip with no `start_ms` of its own
      // begins where its neighbour on that row ended.
      const spans = frameSpans(framesRef.current).spans;
      const i = framesRef.current.indexOf(source);
      const startMs = spans[i]?.start ?? 0;

      // ⚠ BUILT BY `newVideoClip`, NOT WRITTEN OUT AGAIN, and that is a fix
      // rather than a tidy-up. This wrote the clip out by hand and left out the
      // one field a hand-written copy always leaves out: `url`. The thumbnail
      // effect only fetches frames that HAVE one, so a paid render sat on its
      // loading spinner in Media for ever — and the monitor, whose fallback
      // while the video blob downloads IS that thumbnail, showed a black
      // rectangle instead of the picture. Reported as "see image not view in
      // program panel and in media now i see uploading type view". A reload
      // fixed it, because the server fills a url in on read — which is exactly
      // the bug `newVideoClip`'s own ⚠ note describes, happening a second time
      // in the one place that did not use it.
      const render = {
        ...newVideoClip(
          clip.upload_id,
          clip.duration_ms || source.duration_ms,
          source.label || "",
          animaticId
        ),
        // ⚠ THE PANEL'S `src` IS KEPT UNDERNEATH THE VIDEO ONE, so the render
        // still knows which board shot it came from — that is what keeps it in
        // Storyboard Frames rather than Video (`frameOrigin`).
        src: { ...(source.src || {}), kind: "video", upload_id: clip.upload_id },
        track,
        start_ms: startMs,
        // Only as long as the render actually IS. `newVideoClip` infers this from
        // the duration it was handed, which falls back to the PANEL's hold when
        // the clip could not be measured — and a source window longer than the
        // file is a clip that freezes on its last frame.
        out_ms: clip.duration_ms || null,
      };
      // ⚠ A PLAIN APPEND ON THE VIDEO ROW, NOT `insertPictures`. The render's
      // place is decided by `start_ms` above, and rippling THIS row would push
      // whatever else is on it out of step with the panels it is sitting over —
      // which is the one thing this row must not do.
      //
      // ⚠ THE ROOM IT NEEDS COMES FROM THE ROW BELOW. A take is usually longer
      // than the hold it was made from, so the panels after this one are pushed
      // clear of its end — otherwise the next render, which starts where ITS
      // panel starts, lands underneath this one and the two bars overlap. See
      // `spreadPanelsForRenders`; it is forward-only and a no-op when the panels
      // are already clear.
      const appended = [...framesRef.current, render];
      const next = spreadPanelsForRenders(appended);

      // ⚠ AND THE REST OF THE FILM GOES WITH THEM. The pass above moves PICTURES
      // — that was the collision it was written for — so the voiceover, the
      // captions, the typed text and the clips on the Video row used to stay
      // exactly where they were and come out of sync the moment one shot grew:
      // "my caption and voiver over not move so both still". `renderShifts` turns
      // what just happened into a step map; the five setters below carry
      // everything else along it. All of them are no-ops when nothing moved.
      const shifts = renderShifts(appended, next);
      // WHICH SHOTS GOT LONGER, for the captions over them. A take that turns a
      // 4-second hold into 8 seconds of footage leaves its subtitle stopping
      // half way through the shot — "caption length only 4sec but my video is 8
      // sec so i want caption goes 8 sec so match video length".
      const grown = grownSpans(appended, next);
      const settled = rippleFrames(next, shifts);
      setFrames(settled);
      if (shifts.length) {
        // ⚠ FUNCTIONAL SETTERS, NEVER A REF. These lists used to be read out of a
        // ref filled by an effect, and that ref could be EMPTY (a project that
        // has only just loaded — no effect has run) or several renders stale (a
        // Veo poll is deliberately keyed on `animating` alone). Rippling an empty
        // list is a silent no-op that looks exactly like "nothing needed to
        // move", which is precisely how the captions came to be reported twice as
        // not moving while the pictures and the sound did. React hands an updater
        // the LIVE list at commit time; there is nothing here that can be stale.
        //
        // ⚠ ALL FIVE OF `RIPPLED_LISTS`, and the only thing left to get wrong is
        // forgetting one — which `tests/timeline_ripple_check.py` checks for.
        // ⚠ MOVED FIRST, THEN STRETCHED, and the order is the whole of it:
        // `coverGrownShots` matches a caption to its shot by where it now
        // STARTS, so it has to be handed clips that have already been carried.
        setTexts((list) => coverGrownShots(rippleClips(list, shifts), grown));
        setShapes((list) => rippleClips(list, shifts));
        setOverlays((list) => rippleClips(list, shifts));
        // ⚠ `newId` FROM HERE, because cutting the voiceover at a step mints a
        // clip — ids are the editor's to hand out, not a pure module's.
        setAudioTracks((list) => rippleAudio(list, shifts, newId));
      }
      // ⚠ AND INTO THE LIBRARY, because a render is the one asset that CANNOT be
      // got back: an upload can be dropped in again and a panel is still on the
      // board, but re-making this costs money. Deleting the clip must never be
      // the thing that loses it.
      setAssets((list) => mergeAssets(list, [assetFromFrame(render, newId())]));
      // The ref too, so several clips attaching in one pass each see the ones
      // before them — `already` below is asked against this list, and the next
      // render in a batch has to start from the panels this one just moved.
      framesRef.current = settled;
      // ⚠ DID ANYTHING SHIFT? Both passes hand back the SAME list when they moved
      // nothing, so this is an identity test and not a diff. The notice reads it:
      // a clip moving on its own has to be something the editor said out loud,
      // not something that just happened.
      return next !== appended || shifts.length > 0;
    },
    [animaticId, setAssets, setFrames, setTexts, setShapes, setOverlays, setAudioTracks]
  );


  /**
   * A RENDER THAT WAS NEVER OF A CLIP GOES ON THE PLAIN VIDEO ROW.
   *
   * The Media pane's ✨ Video renders from a sentence, so there is no panel for
   * it to sit over and no shot for it to be a take OF. What it IS, from the
   * moment it lands, is a video file in this project — so it is placed exactly
   * as a dropped MP4 is: `newVideoClip`, appended to the end of the Video row,
   * and a library card beside it.
   *
   * ⚠ APPENDED, NOT RIPPLED. `start_ms: null` means "after the last clip on my
   * track" (see `frameSpans`), which is what a file dropped on that row already
   * does. Nothing else on the timeline moves: this clip is not part of the
   * board's cut and pushing the film along for it would be an edit nobody asked
   * for — the whole reason the ✨ Animate path ripples is that its clip has to
   * stay lined up with the panel underneath it, and this one has no panel.
   *
   * ⚠ AND IT IS `newVideoClip`, NOT A HAND-WRITTEN RECORD. Writing one out by
   * hand has cost this project a missing `url` twice (see that function's own
   * note) — a paid render that sat on its loading spinner in Media for ever.
   */
  const attachGeneratedVideo = useCallback(
    (clip) => {
      if (!clip?.upload_id) return;
      const rows = layersRef.current || [];
      const existing = rows.find((l) => rowKindOrLegacy(l.kind) === "video");
      let track = existing ? Number(existing.track) || 0 : null;
      if (track === null) {
        // No plain Video row yet — make one, the same way an upload does. ⚠ The
        // ref is written as well as the state so a second clip landing in the
        // same tick finds this row instead of making another.
        const used = [
          ...pictureTracks(framesRef.current),
          ...rows.filter((l) => ROW_KIND[l.kind]).map((l) => Number(l.track) || 0),
        ];
        const next = Math.max(...used) + 1;
        if (next > MAX_PICTURE_TRACK) {
          // ⚠ SAY THE RENDER IS SAFE. It is paid for and sitting on the server
          // as an ordinary upload; what failed is finding it a row.
          setNotice(
            `The video is safe in Media, but there is no room for another picture row — ` +
              `a project can hold ${MAX_PICTURE_TRACK + 1}.`
          );
          setAssets((list) =>
            mergeAssets(list, [
              assetFromFrame(
                {
                  kind: "video",
                  src: { kind: "video", upload_id: clip.upload_id },
                  label: clip.label || "",
                  duration_ms: clip.duration_ms || 0,
                  out_ms: clip.duration_ms || null,
                },
                newId()
              ),
            ])
          );
          return;
        }
        track = next;
        const record = {
          id: newId(),
          kind: "video",
          name: rowKindName("video", videoTracks.length),
          track,
        };
        layersRef.current = [...rows, record];
        setLayers((list) => [...list, record]);
        seatNewLaneRef.current?.(laneTokenFor(record.kind, record.id, record.track));
      }

      const made = {
        ...newVideoClip(clip.upload_id, clip.duration_ms, clip.label || "", animaticId),
        track,
        // Null, not a number: "after the last clip on this row". See the note
        // above on why this appends rather than ripples.
        start_ms: null,
      };
      const next = [...framesRef.current, made];
      framesRef.current = next;
      setFrames(next);
      addToLibrary([assetFromFrame(made, newId())]);
      setSelectedId(made.id);
    },
    [animaticId, addToLibrary, setAssets, setFrames, setLayers, videoTracks.length]
  );

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
      // Whether making room for a take pushed any panel along — the notice says
      // so, because a clip that moves on its own must never look like a glitch.
      let shifted = false;
      // ⚠ RESOLVED LAZILY AND ONCE. Lazily because most passes attach nothing and
      // must not create a row for a batch that is still rendering; once because
      // every clip in a finished batch belongs on the SAME row.
      let track;
      const rowForRenders = () => {
        if (track === undefined) track = boardVideoTrack();
        return track;
      };
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
        // ⚠ A RENDER THAT WAS NEVER OF A CLIP. The Media pane's ✨ Video renders
        // from a sentence, so its record carries no `frame_id` at all — it is
        // not a take of anything on the timeline and has no panel to sit over.
        // It lands on the plain Video row like a dropped file, which is what it
        // is from this point on. Told apart from "the frame has since been
        // deleted" by asking whether one was ever NAMED, not by whether one is
        // findable: those are different events and only one of them is a loss.
        if (!clip.frame_id) {
          veoHandledRef.current.add(clip.id);
          const already = (framesRef.current || currentFrames || []).some(
            (f) => f.src?.upload_id === clip.upload_id
          );
          if (already) continue;
          attachGeneratedVideo(clip);
          attached += 1;
          continue;
        }
        const frame = (currentFrames || []).find((f) => f.id === clip.frame_id);
        if (!frame) {
          // The frame it was generated from has gone. The clip is not lost —
          // it is an ordinary upload — but there is nowhere obvious to put it.
          veoHandledRef.current.add(clip.id);
          continue;
        }
        veoHandledRef.current.add(clip.id);
        // ⚠ "IS THIS RENDER ALREADY ON THE TIMELINE?" — asked by UPLOAD ID, and
        // it had to change with the row. The test used to be "is the source frame
        // video yet", which worked only because the render REPLACED the panel; now
        // the panel stays a still for ever, so that test would answer "no" on
        // every load and attach a second copy each time. The upload id is the
        // thing that is actually unique to a render.
        const already = (framesRef.current || currentFrames || []).some(
          (f) => f.src?.upload_id === clip.upload_id
        );
        if (already) continue;
        if (attachVeoClip(clip, rowForRenders())) shifted = true;
        attached += 1;
      }
      return { attached, failure, pending, shifted };
    },
    [attachVeoClip, attachGeneratedVideo, boardVideoTrack]
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
    // Did this handler CHANGE the document? Two of the things below do, and the
    // answer decides whether the load may be adopted as the saved baseline.
    //
    // ⚠ IT USED TO BE `attached === 0` ALONE, which stopped being true the moment
    // `track` and `start_ms` joined the saved shape: the normalisation just below
    // rewrites both, and folding that into "what the server already has" means it
    // is recomputed on every single load instead of written down once.
    let changed = false;
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
      changed = true;
    }
    // ⚠ AND EVERY PICTURE ROW ABOVE THE BASE ONE GETS A RECORD, for the same
    // reason and on the same one-time basis. A row used to be proved only by the
    // clips sitting on it, so a row's existence and a row's EMPTINESS were the
    // same state — which is why an empty one could not be kept. Adopting the
    // rows a project already has makes them all real rows: the ✕ has a record to
    // remove, and emptying one no longer makes it disappear underneath you.
    //
    // ⚠ TRACK 0 IS DELIBERATELY NOT ADOPTED. The base row always exists whether
    // or not anything is on it (`videoTracks`), so a record for it would say
    // nothing — and writing one into a brand-new empty animatic would stop it
    // being discarded on the way out (`isEmpty`).
    const rows = p.layers || [];
    const claimed = new Set(
      rows.filter((l) => ROW_KIND[l.kind]).map((l) => Number(l.track))
    );
    const loadedFrames = p.frames || [];
    const orphans = pictureTracks(loadedFrames).filter(
      (t) => t > 0 && t <= MAX_PICTURE_TRACK && !claimed.has(t)
    );
    if (orphans.length) {
      // ⚠ THE KIND COMES FROM THE CLIPS, not from a default. A project cut before
      // these rows existed has its board panels and its footage already separated
      // onto tracks (that is what the ▶⇧ split did), so asking each row what is on
      // it gives "Storyboard images" and "Video" with nothing moved. Guessing
      // "video" for all of them would label the board row wrongly and then let a
      // panel be dragged onto a footage row, which strict rows are meant to stop.
      const perKind = new Map();
      for (const l of rows) {
        if (ROW_KIND[l.kind]) perKind.set(l.kind, (perKind.get(l.kind) || 0) + 1);
      }
      const adopted = [...rows];
      for (const t of orphans) {
        const kind = dominantRowKind(loadedFrames.filter((f) => frameTrack(f) === t));
        const nth = perKind.get(kind) || 0;
        perKind.set(kind, nth + 1);
        adopted.push({ id: newId(), kind, name: rowKindName(kind, nth), track: t });
      }
      setLayers(adopted);
      p = { ...p, layers: adopted };
      changed = true;
    }
    // ⚠ AND THE MEDIA LIBRARY IS DERIVED ONCE, for a project cut before there was
    // one. Same one-time basis as the two blocks above, and the same reasoning:
    // an animatic whose Media pane opened empty would look like every upload it
    // had ever had was gone.
    //
    // ⚠ `null` ONLY, NEVER AN EMPTY LIST. `null` is "this project predates the
    // library"; `[]` is "the library is empty because the user emptied it". Both
    // arrive as falsy, so testing truthiness here is exactly how the ✕ on the last
    // card would come to look broken — every card back on the next reload. The
    // server keeps the distinction alive on purpose; see `_assets_of`.
    if (p.assets == null) {
      const derived = libraryFromProject(
        {
          frames: p.frames || [],
          // ⚠ THE IMAGES LANES TOO. A project cut before the library existed
          // whose only pictures sit on an Images lane derived an EMPTY library
          // and opened saying "Nothing in Media yet" over a timeline plainly
          // holding pictures — `overlays` is not `frames`.
          overlays: p.overlays || [],
          audioTracks: p.audio_tracks || [],
        },
        newId
      );
      // Nothing on the timeline means nothing to derive, and writing an empty list
      // would make a brand-new animatic dirty on open for no reason — and stop it
      // being discarded on the way out (`isEmpty`).
      if (derived.length) {
        setAssets(derived);
        p = { ...p, assets: derived };
        changed = true;
      }
    }
    // ⚠ AND A RECORDING THAT IS ON THE TIMELINE WITH NO CARD IS PUT RIGHT, which
    // is the narrow repair the block above cannot make: a project WITH a library is
    // never re-derived, so a file that missed the library when it was added misses
    // it for ever. The server-side voiceover pass was that gap (see the speech
    // poll), so every project that ran one before it was fixed is carrying a
    // voiceover the Media pane cannot show — "i see in timeline i have audio layer
    // with audio clip but why not audio clip show in media?" — and it opens that way
    // every time, because nothing here was looking.
    //
    // ⚠ IT IS SAFE FOR ONE REASON AND IT IS WORTH SAYING WHICH: the ✕ takes the
    // clips with it (`deleteAsset`), so a card CANNOT be deleted while a clip made
    // from it is still playing. A track on the timeline whose upload has no card is
    // therefore always a gap and never a choice, which is exactly what re-deriving
    // the WHOLE library gets wrong and why that one is spelled `== null`.
    //
    // ⚠ AUDIO ONLY, deliberately. The same sweep over `frames` would mint a junk
    // card for every clip whose `src` is empty — a colour card is the shape that has
    // no file — and there is no add path for pictures that skips the library: they
    // all go through `addToLibrary`. Widen it when one appears, not before.
    if (p.assets != null) {
      const have = new Set((p.assets || []).map(assetKey));
      const missing = [];
      for (const track of p.audio_tracks || []) {
        if (!track?.upload_id) continue;
        const card = assetFromAudio(track, newId());
        const key = assetKey(card);
        if (have.has(key)) continue;
        have.add(key);
        missing.push(card);
      }
      if (missing.length) {
        const healedAssets = mergeAssets(p.assets || [], missing);
        setAssets(healedAssets);
        p = { ...p, assets: healedAssets };
        changed = true;
      }
    }
    if (p.status === "running") setExportJob({ status: "running", progress: null });
    // RECOVER ANY PAID CLIP THAT NEVER LANDED. A render that finished while
    // this editor was closed is still a charge on someone's card, and the
    // MP4 is sitting on the server fully rendered. Attach it now; and if one
    // is still in flight, pick the polling back up where it left off. Both
    // run off the frames just loaded, not off state that hasn't settled yet.
    framesRef.current = p.frames || [];
    const { attached, pending } = reconcileVeoClips(p.veo_clips || [], p.frames || []);

    // ⚠ AND A TAKE THAT WAS ATTACHED BEFORE THIS RULE EXISTED IS PUT RIGHT NOW.
    // The layout only ever ran on the ATTACH, so a project whose renders landed
    // earlier keeps a 2-second still under 4 seconds of footage for ever — there
    // is no gesture that re-runs it, and the user cannot pay to render the shot
    // again just to straighten the row. Reported as "i check when i generate shot
    // 18 so image not capture video lenth … image still not extend/ripple".
    //
    // ⚠ COSTS NOTHING ON A PROJECT THAT IS ALREADY RIGHT. Both passes are
    // idempotent and hand back the SAME arrays when they change nothing, so a
    // correct board is an identity test and no edit at all — which is also what
    // stops this dirtying every project on open.
    const settled = spreadPanelsForRenders(framesRef.current);
    let healed = false;
    if (settled !== framesRef.current) {
      const shifts = renderShifts(framesRef.current, settled);
      const grown = grownSpans(framesRef.current, settled);
      const placed = rippleFrames(settled, shifts);
      setFrames(placed);
      // ⚠ FUNCTIONAL SETTERS HERE ABOVE ALL, because this runs straight out of
      // the load promise: no effect has run, so anything read from a ref would be
      // empty and every one of these would be a silent no-op. The loader has
      // already queued `setTexts(p.texts)` and friends, and an updater is handed
      // that pending list — so these ripple the project that is arriving, without
      // this handler ever having to hold a copy of it.
      setTexts((list) => coverGrownShots(rippleClips(list, shifts), grown));
      setShapes((list) => rippleClips(list, shifts));
      setOverlays((list) => rippleClips(list, shifts));
      setAudioTracks((list) => rippleAudio(list, shifts, newId));
      framesRef.current = placed;
      healed = true;
      changed = true;
    }

    if (pending > 0) setAnimating(true);
    else if (attached) {
      setNotice(
        attached === 1
          ? "A clip you'd already rendered was waiting — it's on the timeline."
          : `${attached} rendered clips were waiting — they're on the timeline.`
      );
    } else if (healed) {
      // ⚠ SAID OUT LOUD. Clips moving by themselves the moment a project opens
      // is the single most alarming thing this editor can do silently.
      setNotice(
        "Some shots were shorter than the takes over them — they now match, and " +
          "the rest of the timeline moved along with them."
      );
    }
    // This is also where UNDO history begins. Anything recorded before this
    // point describes an editor that hadn't loaded yet.
    resetHistory();
    return attached === 0 && !changed;
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
        const { attached, failure, pending, shifted } = reconcileVeoClips(
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
        else if (attached) {
          // ⚠ IF ANYTHING MOVED, SAY SO. The take is longer than the hold it was
          // made from, so the shot grows to match and everything after it slides
          // along — pictures, captions, sound and all. A clip that moves by
          // itself with nothing said about it reads as the editor losing the
          // user's cut.
          setNotice(
            shifted
              ? "Clip ready — the shot now matches its length, and everything after it moved along to make room."
              : "Clip ready — it's on the timeline."
          );
        }
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
    setSpeechSheet(null);
    setSpeechLines([]);
    setSpeechSheetBusy(true);
    // ⚠ THE SAVED PROJECT IS WHAT THE SERVER READS, so the sheet has to be
    // fetched behind a flush — a line is anchored to the clip it is spoken
    // over, and a clip dragged since the last autosave isn't where the server
    // thinks it is. Same reason `askForSpeech` flushes before pricing.
    //
    // ⚠ AND IT SPENDS NOTHING. This is a read of the board plus a keyword guess
    // at who each speaker is; a dialog that costs money to OPEN is a dialog
    // nobody opens twice.
    flush()
      .then(() => api.getAnimaticDialogue(animaticId))
      .then((sheet) => {
        setSpeechSheet(sheet);
        setSpeechLines(sheet.lines || []);
      })
      .catch((e) => setSpeechError(e.message))
      .finally(() => setSpeechSheetBusy(false));
  }

  /** Edit one line of the sheet. The sheet is the script — nothing here is
   *  written back to the storyboard, exactly as ✨ Animate's prompt isn't. */
  function patchSpeechLine(i, patch) {
    setSpeechLines((lines) =>
      lines.map((line, n) => (n === i ? { ...line, ...patch } : line))
    );
  }

  /** WHICH VOICE WILL READ THIS LINE, worked out the same way the server does
   *  it (`tts.voice_for`): the line's own pick, then its persona's casting, then
   *  the voice chosen at the top of the dialog. Shown rather than left implicit
   *  because "why is my grandfather being read by Kore" is otherwise unanswerable
   *  from anything on screen. */
  const voiceForLine = (line) => {
    if (line?.voice) return line.voice;
    const persona = (speechSheet?.personas || []).find((p) => p.key === (line?.persona || ""));
    return persona && persona.key ? persona.voice : speechVoice;
  };

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
              // ⚠ THE SHEET AS EDITED. Pricing the board instead would quote a
              // different set of words from the ones the button then reads.
              lines: speechLines,
              fitShots: speechFit,
              addCaptions: speechCaptions,
              replace: speechReplace,
            });
      if (speechFor === "voiceover" && !estimate.lines) {
        // By far the likeliest reason this button appears to do nothing, so it
        // says all three things that could be true rather than just the first.
        setSpeechError(
          "There is no dialogue to read. The lines come from the storyboard " +
            "this project was made from — so either these frames aren't from " +
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
    // ⚠ THE PICTURE ROW AS IT STANDS, KEPT FOR THE POLL. A voiceover run STRETCHES
    // the shots it reads over and pushes the ones after them along, server-side —
    // so when it finishes, the only way to know how far each moment of the film
    // slid is to compare the row it started from with the row that comes back.
    // Nothing else on the timeline moves without that comparison, which is the
    // "my caption and voiver over not move" bug in its other form.
    speechFramesRef.current = framesRef.current;
    speechAudioRef.current = audioTracks;
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
          lines: speechLines,
          fitShots: speechFit,
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

  /**
   * TAKE THE SERVER'S ANSWER FOR A SPEECH PASS AND PUT THE WHOLE FILM BACK
   * TOGETHER AROUND IT.
   *
   * ⚠ ONE COPY, TWO CALLERS, and that is the entire reason it is a function.
   * The 🎙 dialog's poll below and the Director's phase B both end here, and the
   * arithmetic they share is the part nobody can check by eye: a voiceover
   * stretched a shot, so every clip on every other row is now out of step with
   * the picture by a different amount depending on where it sat. A second copy
   * of that, drifting, is how "my caption and voiceover not move" comes back.
   *
   * @param project      what `GET /animatics/{id}` returned once the job ended
   * @param beforeFrames the picture row as it was BEFORE the pass ran
   * @param beforeAudio  the audio tracks as they were, for telling the new one apart
   * @returns the re-laid picture row, so the caller can see what moved
   */
  const absorbSpeech = useCallback(
    (project, beforeFrames, beforeAudio) => {
      // ⚠ THE LAYERS FIRST, and this is not optional. A captions run writes a
      // LANE as well as clips (`captions.CAPTION_LAYER_ID`), and taking the
      // clips without the lane they sit on leaves the editor holding captions
      // whose row it doesn't know about — the next autosave would then write
      // that missing row back and delete it from the project.
      setLayers(project.layers || []);
      // ⚠ AND THE FRAMES, which is what makes a voiceover different from a
      // captions run: reading a line aloud STRETCHES the shot that owns it and
      // pushes the shots after it along (`_lay_out_speech`, server side), so
      // the picture rows on screen are stale the moment the run ends. Taking
      // the audio without the pictures would leave the editor holding the old
      // layout — and its next autosave would write that back over the one the
      // server just worked out, putting every line back over the wrong shot.
      const laid = project.frames || [];

      // ⚠ AND THE REST OF THE FILM GOES WITH THEM, exactly as it does when a
      // Veo take makes room (`attachVeoClip`). The server re-laid the board's
      // picture row and wrote its own captions and its own voiceover at the
      // right times — and left every OTHER clip where it was: typed text,
      // shapes, overlays, the Video row, a music bed. One shot growing put all
      // of them out for the rest of the film.
      //
      // ⚠ `keep` IS THE HALF THE SERVER ALREADY TIMED, and it is not optional.
      // The generated captions and the new voiceover track are laid against the
      // NEW layout; shifting them by the same map would move them a second
      // time, which is the bug this call is fixing, applied twice.
      const shifts = renderShifts(beforeFrames || [], laid);
      const known = new Set((beforeAudio || []).map((t) => t.upload_id));
      const keep = new Set([
        ...(project.texts || []).filter(isGeneratedCaption).map((t) => t.id),
        ...(project.audio_tracks || [])
          .filter((t) => !known.has(t.upload_id))
          .map((t) => t.id),
      ]);
      // ⚠ THE SERVER'S OWN LISTS ARE AUTHORITATIVE FOR THE TWO IT REWROTE —
      // the texts (it replaced the generated captions) and the audio (it added
      // a track) — so those two are rippled as VALUES. The two it never touched
      // are rippled through their setters, live, like everywhere else here.
      setFrames(rippleFrames(laid, shifts));
      setTexts(rippleClips(project.texts || [], shifts, keep));
      setAudioTracks(rippleAudio(project.audio_tracks || [], shifts, newId, keep));
      setShapes((list) => rippleClips(list, shifts));
      setOverlays((list) => rippleClips(list, shifts));
      // ⚠ AND THE RECORDING JOINS THE LIBRARY. This is the one add path that
      // reached the timeline WITHOUT reaching the Media pane, because the file it
      // adds is made on the SERVER: every other route goes through
      // `addAudioTrack`, which puts a card in as it goes, and this one takes the
      // project back off the wire and writes `audio_tracks` straight into state.
      // So a generated voiceover played on the timeline and was listed nowhere —
      // reported as "i see in timeline i have audio layer with audio clip but why
      // not audio clip show in media?" — and razoring or deleting its clips lost
      // the take with no way back short of a second paid run.
      //
      // ⚠ THE FILE, NOT THE CLIP, and every track rather than the new one:
      // `assetKey` keys audio by upload id and `mergeAssets` dedupes on it, so a
      // voiceover the server cut into four pieces still makes ONE card, and the
      // music bed that was already listed does not gain a second.
      addToLibrary(
        (project.audio_tracks || [])
          .filter((t) => t?.upload_id)
          .map((t) => assetFromAudio(t, newId()))
      );
      // Same reason `addAudioTrack` does it: the section that now holds something
      // is no use to anyone folded shut.
      openGroup("media:audio");
      return laid;
    },
    [
      addToLibrary,
      openGroup,
      setAudioTracks,
      setFrames,
      setLayers,
      setOverlays,
      setShapes,
      setTexts,
    ]
  );

  // ------------------------------------------------- the Director's phase B
  //
  // ⚠ THE SAME TWO SERVER CALLS THE 🎙 DIALOG MAKES, WITH NO DIALOG. The
  // Director asked its questions in popup one and priced them in popup two, so
  // by the time these run the user has already read what will be said and
  // pressed a button that said it would be. See `agent/voice_pass.js` for why
  // the pass has to happen BEFORE the plan's steps and what the runner does with
  // the picture row it returns.

  /** FREE, no model: the board's dialogue sheet, for the preview to show. */
  async function readDirectorScript() {
    // The server reads the SAVED project to find which panel each clip points
    // at, exactly as `openVoiceover` does — a clip dragged since the last
    // autosave is not where the server thinks it is.
    await flush();
    return api.getAnimaticDialogue(animaticId);
  }

  /**
   * SPENDS. Reads the script aloud, waits it out, and hands back the picture row
   * the server re-laid — which is the only thing the re-anchor needs.
   *
   * ⚠ IT RESOLVES ONLY WHEN THE DOCUMENT IS BACK IN STATE. The runner treats the
   * resolution as "the film on screen is now the film the plan is about", and
   * every step after it reads the editor's live refs. Resolving on the 202 and
   * letting the poll finish later would put the whole step list back to editing
   * a document that is minutes stale — the exact failure phase B exists to stop.
   */
  async function runDirectorVoiceover({ lines, fitShots, addCaptions }) {
    await flush();
    const before = framesRef.current;
    const beforeAudio = audioTracks;
    setDirectorSpeaking(true);
    try {
      await api.voiceAnimatic(animaticId, {
        voice: speechVoice,
        lines,
        fitShots: fitShots !== false,
        addCaptions: addCaptions !== false,
        // ⚠ ALWAYS REPLACE. A Director run is one whole treatment of the film,
        // and running it twice must give the film twice — not the dialogue read
        // twice over itself.
        replace: true,
      });
      // Its own poll, deliberately not the effect below: see `directorSpeaking`.
      for (;;) {
        const job = await api.getJob(animaticId);
        if (job.status !== "running") {
          if (job.error) throw new Error(job.error);
          break;
        }
        await new Promise((done) => setTimeout(done, 1500));
      }
      const project = await api.getAnimatic(animaticId);
      return { frames: absorbSpeech(project, before, beforeAudio) };
    } finally {
      setDirectorSpeaking(false);
    }
  }
  /**
   * SPENDS, AND THIS IS THE MOST EXPENSIVE CALL IN THE EDITOR.
   *
   * ONE pass — up to `MAX_VIDEO_BATCH` shots — submitted, polled to the end,
   * attached, and resolved once the takes are actually on the timeline. The
   * runner calls it once per pass and reads its Stop flag between them; see
   * `veo_pass.js` on why there is no honest way to stop inside one.
   *
   * ⚠ IT GOES THROUGH `POST /animatics/{id}/animate`, THE SAME DOOR ✨ ANIMATE
   * USES, and there must never be a second one. Every spend guard written for
   * that button on 2026-08-07 — the batch cap, the refusal of a promptless
   * frame, the refusal to silently re-render something already paid for, the job
   * going RUNNING so an autosave cannot roll a clip back — governs the
   * Director's pass for free by virtue of it being the same endpoint. A
   * dedicated "director render" route would be four guards to write again and
   * four to forget.
   *
   * ⚠ AND IT SENDS A LENGTH PER SHOT (`durations`), because the Director picks
   * each take's length from the hold it has to cover. That is the one thing
   * ✨ Animate's body could not already say.
   *
   * ⚠ IT RESOLVES ONLY WHEN THE TAKES ARE COMMITTED. `reconcileVeoClips` attaches
   * through `setFrames`, and a `setState` from an async continuation is
   * SCHEDULED, not applied — so returning on the last poll would hand the next
   * pass (and the re-anchor after it) a film without the footage in it. Same
   * reason `runDirectorVoiceover` waits, one function up.
   */
  async function runDirectorVeoPass({ shots, render, onProgress }) {
    const rows = shots || [];
    if (!rows.length) return { attached: 0 };
    // The renderer resolves each frame's PICTURE off the saved project, so the
    // shot being animated has to be on the server before it is submitted.
    await flush();
    setDirectorRendering(true);
    try {
      await api.animateAnimaticFrames(animaticId, {
        frameIds: rows.map((row) => row.frame_id),
        prompts: Object.fromEntries(rows.map((row) => [row.frame_id, row.prompt])),
        durations: Object.fromEntries(rows.map((row) => [row.frame_id, row.seconds])),
        render,
      });

      // ⚠ ITS OWN POLL, DELIBERATELY NOT THE `animating` EFFECT BELOW, and for
      // the reason `runDirectorVoiceover` has its own: the runner has to be able
      // to await this pass before it submits the next one, and an effect cannot
      // be awaited. The BODY is the same body — `reconcileVeoClips`, the one
      // self-heal — so a clip attaches identically whichever poll saw it first.
      let failure = "";
      let records = [];
      for (;;) {
        // eslint-disable-next-line no-await-in-loop
        const project = await api.getAnimatic(animaticId);
        records = project.veo_clips || [];
        setVeoClips(records);
        setDirectorRun(project.director_run || null);
        const seen = reconcileVeoClips(records, framesRef.current);
        if (seen.failure) failure = seen.failure;
        if (!seen.pending) break;
        if (onProgress) {
          try {
            // eslint-disable-next-line no-await-in-loop
            const job = await api.getJob(animaticId);
            onProgress(job.progress?.message || "");
          } catch {
            /* progress is a nicety; losing it must not stop the poll */
          }
        }
        // eslint-disable-next-line no-await-in-loop
        await new Promise((done) => setTimeout(done, 2000));
      }

      // ⚠ NOW WAIT FOR THE ATTACH TO COMMIT — see the note above. Asked by
      // UPLOAD ID, which is the thing that is actually unique to a render;
      // `reconcileVeoClips` uses the same test to decide whether a clip is
      // already on the timeline.
      const wanted = new Set(rows.map((row) => row.frame_id));
      const landed = records
        .filter((c) => wanted.has(c.frame_id) && c.status === "ready" && c.upload_id)
        .map((c) => c.upload_id);
      for (let tries = 0; tries < 60 && landed.length; tries += 1) {
        const have = new Set(
          (framesRef.current || []).map((f) => f.src?.upload_id).filter(Boolean)
        );
        if (landed.every((id) => have.has(id))) break;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((done) => setTimeout(done, 60));
      }
      if (failure) throw new Error(failure);
      return { attached: rows.length };
    } finally {
      setDirectorRendering(false);
    }
  }

  directorSpeakRef.current = runDirectorVoiceover;
  directorScriptRef.current = readDirectorScript;
  directorRenderRef.current = runDirectorVeoPass;

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
        // ⚠ THE WHOLE RE-READ IS ONE CALL, and the Director's phase B makes the
        // same one. Everything it does and why is written over `absorbSpeech`.
        absorbSpeech(project, speechFramesRef.current, speechAudioRef.current);
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
  }, [speechRunning, animaticId, absorbSpeech]);

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
      setSaveAsName(isUntitled(title) ? "" : title);
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
          <span className="spinner-inline" /> Opening your project…
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
            title="Your Projects"
            aria-label="Your Projects"
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
          title="Your Projects"
          aria-label="Your Projects"
        >
          ←
        </button>

        <input
          className="an-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={UNTITLED}
          aria-label="Project title"
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
                `${exportName || title || "project"}.${containerExt(video.container)}`
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
              setExportName((n) => n || title || "project");
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

        {/* Sits last, past Export: everything in here is either about YOU
            rather than this project, or it's destructive. ⚠ THE CORNER IS A
            GEAR, NOT A BIN — Delete used to be the bare 🗑 in this slot, one
            press from the button beside Export, and it is now a row inside this
            menu like the rest. Same reasoning as ＋ Add layer: a short list of
            choices belongs in a dropdown hung off the button that asked, not
            spread across the bar. */}
        {confirmDelete ? (
          <span className="an-del-confirm">
            <span className="tiny">Delete this project?</span>
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
          <span className="an-settings-wrap">
            <button
              type="button"
              className="btn small an-settings-btn"
              onClick={() => setSettingsMenu((open) => !open)}
              title="Project settings"
              aria-label="Settings"
              aria-haspopup="menu"
              aria-expanded={settingsMenu}
            >
              <Icon name="settings" />
            </button>

            {/* ⚠ THIS MENU IS ABOUT THE PROJECT, NOT ABOUT YOU. Your account,
                Pricing and plan, Help and Log out were all here and are GONE
                (user-reported: "not need to show in editor setting") — they are
                account business, they live on the sidebar's copy of this menu,
                and repeating them beside Export only put four ways to leave the
                editor next to the button you came here to press.
                ⚠ THE ROWS ARE LEFT OUT, NOT HIDDEN BY CSS. No handler is passed
                for the three account rows, which is what makes `AccountMenu`
                drop them; `help={false}` drops the fourth, whose handler is
                built in. So the list is `extra` and nothing else — and that is
                also the hook for the next EDITOR setting, which is why the gear
                stays a menu rather than going back to a bare bin. */}
            {settingsMenu && (
              <AccountMenu
                className="an-settings-menu"
                label="Project settings"
                onPick={() => setSettingsMenu(false)}
                help={false}
                extra={[
                  {
                    id: "delete",
                    ico: "🗑",
                    label: "Delete project",
                    note: "Delete this project — the storyboard it came from is untouched",
                    // Doesn't delete: it opens the same inline confirm the bin
                    // always did, which then replaces this button in the bar.
                    on: () => setConfirmDelete(true),
                    danger: true,
                  },
                ]}
              />
            )}
          </span>
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
                ? `${assets.length} in Media`
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
                count={SHAPE_KINDS.length}
                info="Open a folder and pick a tile. A shape lands on the frame at the playhead, then moves and re-times like any other clip. Drag it on the picture to place it — or drag a tile straight onto a shape row on the timeline to drop it there instead."
              >
                <ShapeGallery
                  onAdd={(kind) => {
                    addShape(kind, pendingShapeLane);
                    setPendingShapeLane("");
                  }}
                />
              </PropGroup>

              {shapes.length > 0 && (
                <PropGroup id="media:shapes" title="In this project" count={shapes.length}>
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
                        {shapeLabel(s.kind)} {i + 1}
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
                really a single action.
                ⚠ THE ✨ IS A SIBLING, NOT A CHILD. The drop card is a `<button>`
                and a button inside a button is not a thing the browser will
                render — so the two sit in a positioned wrapper and the ✨ is
                placed in its corner, the same arrangement as the prompt box's
                ✨ in the generate dialogs. */}
            <div className="an-asset-add">
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
              {/* ⚠ IT NAMES THE ROW EACH KIND LANDS ON, and the picture half of
                  that changed: an image goes to the Images layer now, never onto
                  a row in the cut. A note that still said "for the video track"
                  would be the one sentence on screen contradicting where the clip
                  actually appears — see `addAssets`. */}
              <span className="an-asset-note">
                Video for the video track · images for the Images layer · an MP3 for the audio
              </span>
            </button>
              {/* ⚠ IT DRAWS A PICTURE, IT DOES NOT UPLOAD ONE — which is why it
                  is a control of its own on this card rather than another line
                  in the note under it. Same star and same corner as the ✨ in
                  the generate dialogs, so "a model writes this for you" is one
                  mark everywhere in the editor. */}
              <button
                type="button"
                className="an-asset-ai"
                disabled={uploading || imgGenBusy}
                /* ⚠ CALLED WITH A TAB, NOT HANDED THE EVENT. `onClick={openImageGen}`
                   passes a MouseEvent as the tab name — harmless, because it
                   falls through to "image", and exactly the kind of accident
                   that stops being harmless the day a third tab is added. */
                onClick={() => openImageGen("image")}
                title="Describe a picture or a shot and have AI make it — an image lands on the Images layer, a video on the Video layer"
                aria-label="Generate an image or a video with AI"
              >
                <Icon name="sparkle" size="1.15em" />
              </button>
            </div>

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
            {/* ⚠ THESE SECTIONS LIST THE LIBRARY, NOT THE TIMELINE, and that is the
                whole of the change the user asked for. They used to list `frames`
                grouped by origin — the Media pane WAS the timeline — so deleting
                a clip deleted the only record that its source had ever been
                added: "i want when user delete video, storboard image, veo video,
                audio and shapes in timeline after upload in media so only clip
                delete in timeline not delete in media panel". Now a card is a
                SOURCE (`animatic/assets.js`), it survives every clip made from
                it, and dragging it back out makes a new one.
                ⚠ STILL GROUPED BY ORIGIN (`assetOrigin`), for the same reason the
                clips were: an animated board shot is a video FILE, and it has to
                stay in Storyboard Frames where you left it rather than moving to
                Video the moment Veo touches it.
                ⚠ AND AUDIO IS ONE OF THEM NOW. It used to be a list of audio
                CLIPS, which vanished with the last clip like everything else
                here; it is a list of FILES, one card per recording however many
                pieces the razor has cut it into.
                A section with nothing in it isn't drawn — an empty "Video"
                heading on a board that has none is a row of furniture. */}
            {[
              { id: "media:frames", title: "Storyboard Frames", list: library.board },
              { id: "media:video", title: "Video", list: library.video },
              { id: "media:images", title: "Images", list: library.image },
              { id: "media:audio", title: "Audio", list: library.audio },
            ]
              .filter((sec) => sec.list.length > 0)
              .map((sec) => (
                <PropGroup key={sec.id} id={sec.id} title={sec.title} count={sec.list.length}>
                  <MediaBin
                    view={mediaView}
                    assets={sec.list}
                    urls={assetUrls}
                    usedCount={assetUsedCount}
                    onPlace={placeAsset}
                    onDelete={deleteAsset}
                    onDownload={downloadVeoClip}
                    /* Double-click the NAME, or right-click the card — one
                       handler for both, because they are one promise. */
                    onRename={renameAsset}
                    onSelectClips={selectAssetClips}
                  />
                </PropGroup>
              ))}

            {/* ⚠ THE ONE THING A LIBRARY CANNOT SAY: that there is nothing in it
                yet. Every section above hides itself when empty, which is right
                for four of them and wrong for all four at once — an empty pane
                with a drop target and no words reads as a pane that is broken. */}
            {!assets.length && (
              <p className="muted tiny">
                Nothing in Media yet. Whatever you add — an upload, a storyboard
                import, a Veo render, a sound — stays listed here, so deleting a
                clip from the timeline never loses the source.
              </p>
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
            {/* The empty end of the head, which is where a player puts this and
                so the only place anyone looks for it. ⚠ PUSHED RIGHT WITH
                `margin-left: auto`, NOT with an `.an-spacer`: this head wraps,
                and a growing spacer on a wrapped line drags the button to the
                far edge of a row it no longer shares with the title. */}
            <button
              type="button"
              className="an-tool an-tool-ico an-fs-btn"
              onClick={toggleFullscreen}
              title={
                fullscreen
                  ? "Leave full screen (Esc)"
                  : "Full screen — the monitor and its transport fill the display"
              }
              aria-label={fullscreen ? "Leave full screen" : "Full screen"}
              aria-pressed={fullscreen}
            >
              {/* ⚠ SIZED IN `rem`. `.an-tool` is 0.72rem — sized for a capital
                  letter — so an `em` icon comes out ~12px, which is mud. */}
              <Icon name={fullscreen ? "fullscreen-exit" : "fullscreen"} size="1.05rem" />
            </button>
          </div>
          <div className="an-pane-body an-program-body" ref={programBodyRef}>
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
                /* ⚠ THE CAPTIONS ARE DRAWN HERE AND PLACED THERE. They are DOM
                   rather than canvas — that is what keeps them real text, with
                   the eleven type controls and the same CSS the export's fonts
                   are matched against — but WHERE they sit in the stack stopped
                   being "on top of everything" the moment a text row could be
                   dragged under a picture row. The monitor cuts the picture into
                   bands at each caption row and calls this once per band. */
                renderTexts={renderCaptions}
              />
              {(!shownFrame || glFailed) && (
                <div className="an-screen-empty">
                  {glFailed
                    ? "This browser can't show the preview — it has no WebGL. The export is unaffected."
                    : frames.length
                      ? "Loading…"
                      : "Add images or video to start your project"}
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
                        // ⚠ `boxSize`, NOT `s.w`/`s.h` — the fill under this box
                        // is drawn at w/h AFTER `scale`, and a hit target that
                        // ignored the scale would leave you grabbing at where
                        // the shape used to be. That is the failure this helper
                        // exists to make impossible; see its docstring.
                        width: `${boxSize(s).w * 100}%`,
                        height: `${boxSize(s).h * 100}%`,
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
                        // Through `boxSize` for the same reason a shape's is.
                        width: `${boxSize(o).w * 100}%`,
                        height: `${boxSize(o).h * 100}%`,
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

              {/* ⚠ THE CAPTIONS ARE NOT HERE ANY MORE — see `renderCaptions` and
                  the `renderTexts` prop on <ProgramCanvas> above. They used to be
                  the last thing in this box, which is another way of saying they
                  were always on top of the picture; a text row that can be dragged
                  under a picture row cannot be. The monitor owns WHERE they go now,
                  and this file still owns what a caption LOOKS like. */}

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
          {/* ⚠ ONE PROVIDER FOR EVERY NUMBER IN EVERY PANE. Each row's box and
              each row's label is a drag handle (see the SCRUBBING block in
              `PropGroup.jsx`), and a drag has to coalesce into ONE undo entry
              the way a slider already does. Passing `gestureProps` down through
              forty `NumField` call sites would be forty chances to miss one, and
              the rows that were missed would flood the history silently. The
              panes go on taking `gesture` as a prop for their sliders — this is
              the same object, reaching the parts props cannot. */}
          <ScrubGesture.Provider value={gestureProps}>
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
          </ScrubGesture.Provider>
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
              see TOOLS. Premiere's letters still SELECT them; they no longer
              label them.
              ⚠ THE KEY LIVES IN THE `title` NOW AND NOWHERE ELSE. The button used
              to print its letter, which taught the shortcut by simply being
              there; drawing an icon instead ("i want you add icon replace V, C,
              B, N, H, Z leter") takes that away, so the tooltip is the only thing
              left that can teach it — the "(V)" in every title is load-bearing.
              ⚠ AND `aria-label` IS THE LABEL, not the icon: the SVG is
              `aria-hidden`, so without this the button would announce as
              "button". `title` alone is not read reliably.
              ⚠ SIZED IN `rem`, NOT `em`. `.an-tool`'s font-size is 0.72rem —
              sized for a capital letter — so an `em` icon came out ~12px and
              these drawings need ~18 to stay legible. */}
          <span className="an-tools" role="group" aria-label="Tools">
            {TOOLS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`an-tool an-tool-ico ${tool === t.id ? "on" : ""}`}
                onClick={() => setTool(t.id)}
                title={`${t.label} (${t.key}) — ${t.hint}`}
                aria-label={`${t.label} (${t.key})`}
                aria-pressed={tool === t.id}
              >
                <Icon name={t.id} size="1.15rem" />
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
                {/* ⚠ SPENDS MONEY — and like the Properties pane's copy it renders
                    nothing itself: it opens the dialog that prices the job first.
                    It is a SECOND way to the same door, asked for as "Animate
                    with Veo Buttun i want one more place for user confort": the
                    only one before this sat in Properties, which meant selecting
                    the shot, finding the Footage group and scrolling to it —
                    while the thing you were animating was right under the
                    playhead.
                    FIRST IN THE ROW, before Text, because that is the order it
                    was asked for ("+ add layer, Animate with Veo and text, colour
                    card and Voiceover") and because it is the one control here
                    that changes the PICTURE rather than adding something over it.
                    Plain `btn small`, deliberately NOT the `.an-add-text` /
                    `.an-add-card` weight: those two are the pair that makes a clip
                    out of nothing and costs nothing. This one spends, and reading
                    as one of them would be a lie about it — the same reason
                    🎙 Voiceover is plain. */}
                <button
                  type="button"
                  className="btn small"
                  disabled={!veoTarget || serverBusy}
                  onClick={() => veoTarget && openAnimate(veoTarget.id)}
                  title={
                    !veoTarget
                      ? "Nothing to animate yet — add a shot first, or park the playhead on one"
                      : veoTargetClip?.status === "ready"
                        ? `Render “${veoTarget.label || "this shot"}” again with Veo — it costs the same as the first time`
                        : `Turn “${veoTarget.label || "this shot"}” into real footage with Veo (you'll see the price first)`
                  }
                >
                  {animating
                    ? "✨ Animating…"
                    : veoTargetClip?.status === "ready"
                      ? "✨ Render again with Veo"
                      : "✨ Animate with Veo"}
                </button>
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
                {/* ⚠ SPENDS NOTHING, AND SAYS SO. It sits next to 🎙 Voiceover,
                    which does spend, so the two must not read alike: this one is
                    `an-add-director`, weighted with the Text / Colour card pair
                    it belongs to — the buttons that make something out of
                    nothing for free.

                    Everything behind it is rules (`agent/house_style.js`); no
                    model is called and no network request is made. The panel it
                    opens is a PREVIEW — the timeline is not touched until Run is
                    pressed in there, and Revert puts it all back afterwards. */}
                <button
                  type="button"
                  className="btn small an-add-director"
                  disabled={!frames.length}
                  onClick={openDirector}
                  title={
                    frames.length
                      ? "Read the timeline's rhythm and cut it — transitions and camera moves. Free, previewed first, and revertable."
                      : "Add some pictures first — the Director edits a sequence, so it needs one to read"
                  }
                >
                  🎬 Make Video
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
            /* An alt-DRAG on a clip makes copies at the delta it travelled. How
               MANY is decided here and not there — see `duplicateAt`. */
            onDuplicateClip={duplicateAt}
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
            onAddLayer={() => setLayerMenu((open) => !open)}
            /* ＋ Add layer's own dropdown. It adds an EMPTY row and nothing
               else — it used to add content too (an upload dialog for images, a
               caption, a shape), which is not what "add a layer" means: you add
               the row, then you put things on it with that row's own ＋.
               Anchored to the button by the timeline (`.tl-head`) and filled
               here, because which layers exist is this file's business.
               ⚠ ONE LINE PER KIND AND NO PROSE: the notes under each label are
               gone — a menu is read by scanning it, and five sentences is not a
               scan. What a kind means is on the item's `title`, the same place
               the empty lanes and the row ＋ put theirs. */
            addLayerMenu={
              layerMenu && (
                <div className="tl-layer-menu" role="menu" aria-label="Add a layer">
                  {[
                    // ⚠ THE THREE PICTURE ROWS COME FIRST, and each is a row in
                    // the CUT — as opposed to Images below, which composites over
                    // it. `row` marks them so the click handler knows to make a
                    // picture track rather than an ordinary layer record.
                    {
                      kind: "board_image",
                      row: true,
                      ico: "🖼",
                      label: "Storyboard images",
                      note: "Import a storyboard's panels onto a row of their own",
                    },
                    {
                      kind: "board_video",
                      row: true,
                      ico: "✨",
                      label: "Storyboard video",
                      note: "Where ✨ Animate puts a Veo render — above the panel it came from",
                    },
                    {
                      kind: "video",
                      row: true,
                      ico: "🎞",
                      // ⚠ NO SEPARATE "Stills track" BESIDE THIS ONE. There used
                      // to be one, and it made the same row under a second name:
                      // a video row takes footage AND stills (see the row ＋ and
                      // `dropAsset`), so the menu offered two doors into one
                      // place. Full-frame photos go here; Images below is the
                      // one that composites OVER the cut.
                      label: "Video",
                      note: "Another row for footage and full-frame stills — drawn OVER the tracks below it",
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
                      note: "Another row for the vector shapes — boxes, stars, arrows",
                    },
                    {
                      kind: "audio",
                      ico: "♪",
                      // ⚠ COUNTED IN FILES, NOT CLIPS — `audioFileCount()`, the
                      // same measure every other audio limit in this file uses
                      // (`addAssets`, `dropAsset`, `addAudioTrack`). This one
                      // read `audioTracks.length`, which is the number of CLIPS:
                      // razor one voiceover into four pieces and the menu said
                      // you were at the four-track maximum and greyed itself
                      // out, on a project holding one file. Reported as "why is
                      // audio import not available through the dropdown".
                      label: "Audio",
                      note: `An empty track, mixed with the others (${audioFileCount()}/${MAX_AUDIO_TRACKS})`,
                      disabled: audioFileCount() >= MAX_AUDIO_TRACKS,
                      disabledNote: `You already have the maximum of ${MAX_AUDIO_TRACKS} audio files — cutting one into pieces doesn't count against it`,
                    },
                    // ⚠ NO "Video — not supported yet" ENTRY, because it IS
                    // supported: a video track holds footage and stills alike,
                    // and it is the first item here.
                  ].map((opt) => (
                    <button
                      key={opt.kind}
                      type="button"
                      role="menuitem"
                      className="tl-layer-menu-opt"
                      disabled={opt.disabled}
                      title={opt.disabled ? opt.disabledNote : opt.note}
                      onClick={() => {
                        setLayerMenu(false);
                        // ⚠ A STORYBOARD ROW IS MADE BY THE IMPORT, not before
                        // it. An empty one would be a row you cannot fill from
                        // its own ＋ without the picker anyway, so the picker IS
                        // the entry point — "user click Storyboad image then user
                        // get a pop up".
                        if (opt.kind === "board_image") openBoardImport(null);
                        else if (opt.row) addPictureTrack(opt.kind);
                        else addLayer(opt.kind);
                      }}
                    >
                      <span className="tl-layer-menu-ico">{opt.ico}</span>
                      {opt.label}
                    </button>
                  ))}
                </div>
              )
            }
            onRemoveTrack={removeTrack}
            onClearLane={clearLane}
            onToggleHidden={toggleLaneHidden}
            onToggleLocked={toggleLaneLocked}
            /* A row dragged up or down the gutter: `(lane, toKey)`. The timeline
               owns the GESTURE (which row was picked up, which row's place it was
               dropped on) and this file owns what that MEANS — a picture row
               trades track numbers, an overlay row rewrites the saved order and
               its clips' draw order. See `moveLane`. */
            onMoveLane={moveLane}
            /* The timeline's refusals used to all be visible ones — a target that
               never lit up, a bar that did not move. A locked row is the first
               that looks like nothing happening, so it needs the status strip. */
            onNotice={setNotice}
            onDownloadClip={downloadVeoClip}
            /* Right-click a storyboard still → draw the shot missing either side
               of it. Opens the dialog and nothing else; nothing is drawn until
               the button in it is pressed. */
            onGenerateShot={openGenerateShot}
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
        reframeRunning || reblockJob || imgGenBusy) && (
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
          {/* ⚠ THE ONE ROW HERE WITH NO PERCENTAGE. Drawing a picture is a
              SINGLE synchronous call — there are no stages to report and nothing
              to ask how far through it is — so the bar slides rather than
              filling (`is-waiting`) and there is no number after it. Every other
              row above has real progress to show and shows it.
              ⚠ AND IT IS NOT GUARDED AGAINST THE OTHERS. The rows above are
              mutually exclusive because they are all the SERVER writing this
              project and only one can run; a draw writes nothing to the project
              and can honestly sit beside a Veo render that is still going. */}
          {imgGenBusy && (
            <span className="an-status-export">
              <span className="spinner-inline" />
              Drawing your image…
              <span className="an-status-bar is-waiting">
                <span />
              </span>
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
                  placeholder="project"
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
                    : "No audio on this project"}
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
          <div className="card an-ws-modal" onClick={(e) => e.stopPropagation()}>
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

            <div className="an-ws-list">
              {WORKSPACES.map((w) => (
                <button
                  key={w.id}
                  type="button"
                  className={`an-ws-opt ${workspace === w.id ? "on" : ""}`}
                  onClick={() => chooseWorkspace(w.id)}
                  aria-pressed={workspace === w.id}
                >
                  <span className="an-ws-opt-ico">
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

      {/* --- Import a storyboard onto a row of its own --------------------- */}
      {/* ⚠ SPENDS NOTHING. The panels are already drawn and already paid for;
          this only references them, exactly as "Make animatic" on the board does.
          So there is no priced confirmation step — the two-step discipline is for
          the buttons that spend, and adding it here would teach the user to click
          through a dialog that never has a price on it. */}
      {boardImport && (
        <div className="modal-overlay" onClick={() => setBoardImport(null)}>
          <div className="card an-board-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setBoardImport(null)}>
              ✕
            </button>
            <h2>Import a storyboard</h2>
            <p className="muted">
              Its drawn panels come in on a row of their own, in order. The
              storyboard is referenced, not copied — redraw a panel there and it
              updates here.
            </p>

            {boardError && <p className="error">{boardError}</p>}

            {boardList === null ? (
              <p className="muted tiny">Looking for your storyboards…</p>
            ) : !boardList.length ? (
              <p className="muted tiny">
                You have no storyboards yet — make one in Script to Storyboard
                first.
              </p>
            ) : (
              <div className="an-board-list">
                {boardList.map((b) => {
                  const id = b.job_id || b.id;
                  return (
                    <button
                      key={id}
                      type="button"
                      className={`an-board-opt ${boardPick === id ? "on" : ""}`}
                      onClick={() => setBoardPick(id)}
                      aria-pressed={boardPick === id}
                    >
                      <span>
                        <strong>{b.character_name || b.title || UNTITLED}</strong>
                        {/* How much is actually DRAWN, which is what will come
                            across — a board with forty planned shots and two
                            drawings imports two. */}
                        <span className="tiny muted">
                          {b.panel_count
                            ? `${b.panel_count} panel${b.panel_count === 1 ? "" : "s"}`
                            : "no panels drawn yet"}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {/* Cancel first, then the primary — the order every other dialog in
                this editor uses. */}
            <div className="an-name-actions">
              <button type="button" className="btn ghost" onClick={() => setBoardImport(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={!boardPick || boardBusy}
                onClick={doBoardImport}
              >
                {boardBusy ? "Importing…" : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- Draw any picture, from the Media pane's ✨ --------------------- */}
      {/* ⚠ THE SAME CARD AS "Generate this shot", AND A DIFFERENT DIALOG. What
          they share is the shape — an intro, a prompt box, Shape / Length /
          Model, a two-button footer — because they are the same kind of moment
          and this editor does not get to invent a new layout per feature. What
          they do NOT share is the middle: there is no shot name here (this
          picture is not a shot), no Before/After (it sits between nothing), and
          no ✨ inside the box (there are no neighbours to write it from — the
          sentence IS the brief). Every one of those is a real difference, which
          is exactly why this is not the same component with five props. */}
      {imgGen && (
        <div className="modal-overlay" onClick={() => setImgGen(null)}>
          {/* ⚠ ONE SIZE FOR BOTH TABS. The Video half is the taller of the two
              by four rows, so switching tabs resized the card under the pointer
              — reported as "both panel size chnage when i click AI Image and AI
              Video keep same size panel". `.an-gen-modal` is a flex column with
              a floor high enough for the taller half; the body takes the slack
              and the footer sits on the bottom edge in both. */}
          <div
            className="card an-name-modal an-gen-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <button className="modal-close" onClick={() => setImgGen(null)}>
              ✕
            </button>
            <h2>Generate with AI</h2>

            {/* ⚠ TWO TABS, ONE PLACE — "so user choose easily in one place".
                They share the card and the Model row and very little else,
                because they are not two settings of one thing: an image is one
                synchronous call costing a fraction of a cent, and a video is Veo
                — minutes long, billed per second of output, and therefore priced
                and confirmed before a single frame is rendered.
                ⚠ IT IS `.an-tabs`, THE SAME STRIP AS Media / Shapes / Effects at
                the top of the Media pane. A second tab style in the same editor
                would read as a different mechanism. */}
            <div className="an-tabs an-gen-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={imgGenTab === "image"}
                className={`an-tab ${imgGenTab === "image" ? "on" : ""}`}
                onClick={() => setImgGenTab("image")}
              >
                <Icon name="sparkle" /> AI Image
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={imgGenTab === "video"}
                className={`an-tab ${imgGenTab === "video" ? "on" : ""}`}
                onClick={() => setImgGenTab("video")}
              >
                <Icon name="sparkle" /> AI Video
              </button>
            </div>

            {/* ⚠ BOTH BODIES ARE RENDERED, STACKED IN ONE GRID CELL, and that
                is what makes the card ONE SIZE on both tabs. It was a
                `min-height` with a measured number in it, which is the obvious
                fix and the wrong one: the floor lifted the shorter tab but could
                not shrink the taller, so the two still differed — reported twice
                — and any row added to either half would have silently put them
                out of step again. Sharing a grid cell makes the cell the height
                of the TALLER pane, for ever, with no number to maintain.
                ⚠ THE HIDDEN ONE IS `visibility: hidden`, NOT `display: none`.
                Hiding it properly would take its height out of the cell, which
                is the whole mechanism; `visibility` keeps the box and its
                geometry while removing it from the page — and, importantly, from
                the tab order and the accessibility tree, so there is no way to
                type into the half you cannot see. */}
            <div className="an-gen-body">
            <div className="an-gen-pane" data-on={imgGenTab === "image"} aria-hidden={imgGenTab !== "image"}>
            <p className="muted">
              Describe any picture and Gemini draws it. It lands in Media and on
              the Images layer.
            </p>

            <textarea
              className="an-tp-text"
              ref={imgGenBoxRef}
              rows={3}
              value={imgGenPrompt}
              placeholder="e.g. a hand-painted title card, cracked gold lettering on deep navy — or a rain-soaked neon alley at night"
              maxLength={2000}
              onChange={(e) => setImgGenPrompt(e.target.value)}
            />
            <p className="tiny muted an-animate-src an-shot-hint">
              <span>Anything you can describe — this one is not a storyboard shot.</span>
              <InfoDot open={imgGenNote} onToggle={() => setImgGenNote((was) => !was)} />
            </p>
            {imgGenNote && (
              <p className="an-note an-shot-note">
                Nothing about the storyboard is applied — no style, no locked
                characters, no shots either side — so the words you type are the
                whole brief. It arrives on the Images layer, which composites
                OVER the cut rather than into it, so it covers nothing until you
                drag it where you want it on the frame.
              </p>
            )}

            <div className="an-prop-row">
              <span className="an-prop-label">Shape</span>
              <select
                className="an-select"
                value={imgGenAspect}
                onChange={(e) => setImgGenAspect(e.target.value)}
              >
                {imgGenAspects.map((id) => {
                  const note = BOARD_ASPECTS.find((a) => a.id === id)?.note;
                  return (
                    <option key={id} value={id}>
                      {note ? `${id} — ${note}` : id}
                    </option>
                  );
                })}
              </select>
            </div>

            {/* HOW LONG THE CLIP HOLDS. ⚠ Two seconds, which is what every other
                picture arriving on the Images layer opens at — see
                `IMG_GEN_DEFAULT_SECONDS` for why this differs from the shot
                dialog's eight. */}
            <div className="an-prop-row">
              <span className="an-prop-label">Length</span>
              <select
                className="an-select"
                value={imgGenSeconds}
                onChange={(e) => setImgGenSeconds(Number(e.target.value))}
              >
                {SHOT_GEN_SECONDS.map((s) => (
                  <option key={s} value={s}>
                    {s} seconds
                  </option>
                ))}
              </select>
            </div>

            <div className="an-prop-row">
              <span className="an-prop-label">Model</span>
              <span className="an-select an-select-static">
                {imageModel?.model || "…"}
                {imageModel?.provider && (
                  <span className="an-shot-provider"> · {imageModel.provider}</span>
                )}
              </span>
            </div>

            </div>

            <div className="an-gen-pane" data-on={imgGenTab === "video"} aria-hidden={imgGenTab !== "video"}>
                {/* ⚠ THE ONE TAB IN THIS DIALOG THAT SPENDS REAL MONEY. Veo is
                    billed per second of OUTPUT — roughly $0.24 to $3+ for eight
                    seconds — so this half follows the discipline every paid path
                    in this editor follows: the button below asks the server what
                    it would cost, and nothing is submitted until that number has
                    been on screen and accepted. */}
                <p className="muted">
                  Describe a shot and Veo films it. Add a starting picture and it
                  moves that instead. It lands in Media and on the Video layer.
                </p>

                <textarea
                  className="an-tp-text"
                  ref={vidGenBoxRef}
                  rows={3}
                  value={vidGenPrompt}
                  placeholder="e.g. a slow dolly along a rain-soaked street at night, neon reflections sliding over the wet tarmac"
                  maxLength={2000}
                  onChange={(e) => setVidGenPrompt(e.target.value)}
                />

                {/* THE STARTING FRAME — optional, which is the whole difference
                    between this and ✨ Animate. With one it is image-to-video and
                    the picture is what moves; without one Veo renders from the
                    words alone. ⚠ IT IS UPLOADED THE MOMENT IT IS CHOSEN, so the
                    server can check it exists before it quotes a price for it. */}
                <div className="an-vid-source">
                  {vidGenSource ? (
                    <>
                      {/* ⚠ AN AUTHED BLOB, NOT A BARE PATH. Every picture in
                          this app is fetched with the bearer token and shown as
                          an object URL — an `<img src="/animatics/…">` would
                          simply 401. `pickVideoSource` fetches it once, right
                          after the upload, and hands it over on the record. */}
                      {vidGenSource.blob && (
                        <img
                          className="an-vid-source-thumb"
                          src={vidGenSource.blob}
                          alt=""
                        />
                      )}
                      <span className="an-vid-source-name">{vidGenSource.name}</span>
                      <button
                        type="button"
                        className="btn small ghost"
                        onClick={() => {
                          retireBlob(vidGenSource.blob);
                          setVidGenSource(null);
                        }}
                        title="Render from the words alone instead"
                      >
                        Remove
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="an-vid-source-add"
                      disabled={vidGenUploading}
                      onClick={() => vidGenInputRef.current?.click()}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => {
                        if (e.dataTransfer?.files?.length) {
                          e.preventDefault();
                          pickVideoSource(e.dataTransfer.files);
                        }
                      }}
                    >
                      <span className="an-asset-plus">＋</span>
                      {/* ⚠ SHORT. It read "Add a starting picture, or drop one
                          here — optional", which is a sentence in a control and
                          the widest line in the card. What it does is legible
                          from the ＋ and four words; that it is optional is said
                          by the fact that the button below works without it. */}
                      {vidGenUploading ? "Adding the picture…" : "Add or drop image"}
                    </button>
                  )}
                  <input
                    ref={vidGenInputRef}
                    type="file"
                    accept="image/*"
                    hidden
                    onChange={(e) => {
                      pickVideoSource(e.target.files);
                      e.target.value = "";
                    }}
                  />
                </div>

                <div className="an-prop-row">
                  <span className="an-prop-label">Quality</span>
                  <select
                    className="an-select"
                    value={vidGenRender.tier}
                    onChange={(e) =>
                      setVidGenRender((r) => ({ ...r, tier: e.target.value }))
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
                    value={vidGenRender.resolution}
                    onChange={(e) =>
                      setVidGenRender((r) => ({ ...r, resolution: e.target.value }))
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
                    value={vidGenRender.duration_seconds}
                    onChange={(e) =>
                      setVidGenRender((r) => ({
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

                {/* ⚠ THE SHAPE IS THE PROJECT'S AND IS NOT OFFERED. Veo is asked
                    for `settings.aspect_ratio` server-side, the same value
                    ✨ Animate sends, so a generated clip cannot arrive a
                    different shape from the film it is going into. */}
                <div className="an-prop-row">
                  <span className="an-prop-label">Shape</span>
                  <span className="an-select an-select-static">
                    {settings.aspect_ratio}
                    <span className="an-shot-provider"> · the project's</span>
                  </span>
                </div>

                <label className="an-check">
                  <input
                    type="checkbox"
                    checked={vidGenRender.generate_audio}
                    onChange={(e) =>
                      setVidGenRender((r) => ({ ...r, generate_audio: e.target.checked }))
                    }
                  />
                  Let Veo generate sound too (costs more)
                </label>

            </div>
            </div>

            {/* ⚠ ONE FOOTER, OUTSIDE THE TABS. Cancel is the same button in both
                halves, and two copies of it is two places for it to drift; more
                to the point, a footer INSIDE each tab body cannot be pinned to
                the bottom of the card, which is what stops the card resizing.
                Only the primary button differs — and it differs in the way that
                matters: the image half GENERATES, the video half only asks what
                it would cost. */}
            <div className="an-name-actions an-gen-actions">
              <button type="button" className="btn ghost" onClick={() => setImgGen(null)}>
                Cancel
              </button>
              {imgGenTab === "image" ? (
                <button
                  type="button"
                  className="btn primary"
                  disabled={!imgGenPrompt.trim() || imgGenBusy}
                  onClick={doGenerateImage}
                >
                  {imgGenBusy ? "Drawing the image…" : "Generate the image"}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn primary"
                  disabled={!vidGenPrompt.trim() || vidGenBusy || vidGenUploading}
                  onClick={askToGenerateVideo}
                >
                  {vidGenBusy ? "Checking the price…" : "See the price →"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* --- The ✨ Video confirm: the last thing before money moves ------- */}
      {/* Same shape as ✨ Animate's, deliberately — this is the one screen in
          the app where a familiar layout is worth more than a novel one. */}
      {vidGenConfirm && (
        <div className="modal-overlay" onClick={() => setVidGenConfirm(null)}>
          <div className="card fv-confirm" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="modal-close"
              onClick={() => setVidGenConfirm(null)}
            >
              ✕
            </button>
            <h2>Generate this video?</h2>

            <div className="fv-confirm-price">
              <span className="fv-confirm-usd">
                ~${vidGenConfirm.estimate.usd.toFixed(2)}
              </span>
              <span className="tiny muted">estimated</span>
            </div>

            <p className="muted">
              {vidGenConfirm.estimate.seconds}s of video at {vidGenRender.tier} /{" "}
              {vidGenRender.resolution}
              {vidGenRender.generate_audio ? " with sound" : ", silent"},{" "}
              {vidGenSource ? "starting from your picture" : "from your words alone"}.
            </p>
            <p className="tiny muted">
              An estimate from list prices, not a quote. Google bills the actual
              amount, and you are only charged for renders that succeed.
            </p>

            <div className="an-name-actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setVidGenConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={vidGenBusy}
                onClick={doGenerateVideo}
              >
                <Icon name="play" />{" "}
                {vidGenBusy
                  ? "Starting…"
                  : `Generate — ~$${vidGenConfirm.estimate.usd.toFixed(2)}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- Generate the shot before / after this one --------------------- */}
      {/* ⚠ ONE DIALOG FOR BOTH MENU LINES. `shotGen.side` is the only difference
          between them, and it is what the server needs to know which gap it is
          writing into — two near-identical dialogs would be two places to keep
          the shape picker, the length and the ✨ in step.

          ⚠ IT IS THE ✨ ANIMATE DIALOG'S LAYOUT ON PURPOSE. The two are the same
          question asked about the same clip — make something new from this shot —
          and this is not the screen to be inventive on. Same card, same
          name-over-box rhythm, same `.an-name-actions` footer. */}
      {shotGen && (
        <div className="modal-overlay" onClick={() => setShotGen(null)}>
          <div className="card an-name-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setShotGen(null)}>
              ✕
            </button>
            <h2>Generate this shot</h2>
            {/* ⚠ TWO LINES, LIKE ✨ ANIMATE'S. It said where the drawing lands
                as well, which is three lines for something the shot name under
                it already says ("After Shot 9") — and in a card this tall the
                intro is the first thing that should give room back. */}
            <p className="muted">
              A new drawing in the storyboard's own look. Describe what the
              camera SEES.
            </p>

            {/* WHICH SHOT THIS WILL BE. ⚠ The name comes from the server, which
                is where it is built — see `_neighbour_label`. "New shot" stands
                in for the beat before the read lands rather than a second copy
                of the name-builder living here to disagree with it. */}
            <div className="an-animate-shot">
              <span className="an-animate-shot-name">
                {shotGenCtx?.label || "New shot"}
              </span>
              {shotGenCtx?.title && (
                <span className="an-animate-shot-src">from “{shotGenCtx.title}”</span>
              )}
            </div>

            {/* WHAT IT GOES BETWEEN. The one thing this dialog can show that
                ✨ Animate cannot: the shot being written does not exist yet, so
                the only way to judge what belongs here is to read its
                neighbours. Either side is missing at the ends of the row, and it
                says so rather than showing an empty quotation. */}
            {shotGenCtx?.can_generate && (
              <div className="an-shot-gap">
                <p className="tiny muted">
                  <span className="an-shot-gap-side">Before</span>{" "}
                  {shotGenCtx.before_description || "nothing — this would open the film."}
                </p>
                <p className="tiny muted">
                  <span className="an-shot-gap-side">After</span>{" "}
                  {shotGenCtx.after_description || "nothing — this would end the film."}
                </p>
              </div>
            )}

            {/* THE BOX OPENS EMPTY, AND THAT IS THE DIFFERENCE FROM ✨ ANIMATE.
                There is no panel behind a shot that does not exist yet, so there
                is nothing to draft from — the ✨ in the corner is what fills it,
                reading the two shots above and the film around them. */}
            <div className="an-shot-prompt">
              <textarea
                className="an-tp-text"
                autoFocus
                rows={3}
                value={shotGenPrompt}
                placeholder="e.g. low angle on the doorway as he steps through it, the lamp swinging behind him"
                maxLength={2000}
                onChange={(e) => setShotGenPrompt(e.target.value)}
              />
              {/* ⚠ A DRAWN ICON, NOT THE ✨ EMOJI the Animate buttons carry: this
                  one sits INSIDE a text box as a control, and an emoji there
                  inherits the box's font and lands a different size and colour in
                  every browser.
                  ⚠ IT KEEPS ITS COLOUR WHILE IT WORKS. It is `disabled` for the
                  duration — pressing it twice would pay for two suggestions — but
                  the ordinary disabled dim would read as "this button is off",
                  which is the opposite of what is happening, so `is-working`
                  takes the dim back off and lights the outline instead. */}
              <button
                type="button"
                className={`an-shot-ai${shotGenAsking ? " is-working" : ""}`}
                disabled={!shotGenCtx?.can_generate || shotGenAsking || shotGenBusy}
                onClick={suggestShotPrompt}
                title={
                  shotGenPrompt.trim()
                    ? "Write this shot for me, using what you have typed as direction — it replaces the box"
                    : "Write this shot for me, from the shots either side of it and the story around them"
                }
                aria-label="Write this shot for me"
              >
                <Icon name="sparkle" size="1.15em" />
              </button>
            </div>

            {/* ⚠ A MOVING BAR, NOT A LINE OF GREY TEXT. The wait for a text call
                is a couple of seconds of nothing, and "Reading the shots either
                side…" in `muted` under the box was not enough to tell anybody
                that a model was running — reported as "while generating user not
                see any working bar motion".
                ⚠ IT IS `.an-prop-progress`, the row this editor already uses for
                a server pass reported beside the button that started it (the
                captions run). Two things differ, and both had to.
                ⚠ ONE INDICATOR, AND IT IS THE BAR. The row carries a
                `.spinner-inline` as well everywhere else, and here that was two
                things saying the same thing side by side — reported as "why you
                add two working bar … you remove one working status bar not need
                two bar view". The bar is the one that survives: it is the wider
                mark, it reads from across the pane, and it is the half that says
                "still going" rather than merely "busy". ⚠ AND THE SPINNER WAS
                THE WORSE OF THE TWO HERE FOR A REASON THAT IS NOT A MATTER OF
                TASTE: `.spinner-inline` takes its moving edge from
                `--primary-ink`, which is near-black because nearly every one of
                its ~50 uses sits INSIDE a gold button. On this dark panel that
                edge all but vanishes and the circle reads as a static grey ring,
                which is why it was reported as not obviously working. Recolouring
                it is not available — it would break every one of those buttons.
                ⚠ AND THE BAR SLIDES rather than filling: a captions run reports a
                percentage and a single model call reports nothing at all. See
                `.an-status-bar.is-waiting`. */}
            {shotGenAsking ? (
              <div className="an-prop-progress an-shot-thinking" role="status">
                <span className="an-prop-progress-msg">
                  Reading the shots either side, and the story around them…
                </span>
                <span className="an-status-bar is-waiting">
                  <span />
                </span>
              </div>
            ) : (
              /* ⚠ ONE LINE, WITH THE REST BEHIND THE ⓘ — the standing rule for
                 this whole editor, and this dialog was ignoring it. Three lines
                 of grey prose about a control you understand after using it once
                 took more of the card than the two shots the new one goes
                 between, which are the part you actually have to read
                 ("information text keep samll but informative … or you want older
                 style like I icone"). Same `InfoDot` as the Properties rows and
                 the Effects library, imported rather than redrawn. */
              <p className="tiny muted an-animate-src an-shot-hint">
                <span>✨ writes the missing beat between the two shots above.</span>
                <InfoDot
                  open={shotGenNote}
                  onToggle={() => setShotGenNote((was) => !was)}
                />
              </p>
            )}
            {shotGenNote && !shotGenAsking && (
              <p className="an-note an-shot-note">
                It reads both shots and the stretch of film around them, so what
                it writes is a beat that is MISSING rather than a re-run of the
                shot you right-clicked. Type first and it works to your
                direction; either way it replaces the box, and you can edit
                every word of what it writes before anything is drawn.
              </p>
            )}

            {/* Why the button is going to refuse — read out here rather than
                left for a 400, the same contract the redraw pane follows. */}
            {shotGenCtx && !shotGenCtx.can_generate && (
              <p className="tiny an-animate-warn">{shotGenCtx.reason}</p>
            )}

            <div className="an-prop-row">
              <span className="an-prop-label">Shape</span>
              <select
                className="an-select"
                value={shotGenAspect}
                onChange={(e) => setShotGenAspect(e.target.value)}
              >
                {shotGenAspects.map((id) => {
                  const note = BOARD_ASPECTS.find((a) => a.id === id)?.note;
                  return (
                    <option key={id} value={id}>
                      {note ? `${id} — ${note}` : id}
                    </option>
                  );
                })}
              </select>
            </div>

            {/* HOW LONG IT HOLDS ON THE TIMELINE — a property of the CLIP, not
                of the picture. Eight by default because the next thing that
                usually happens to a new shot is ✨ Animate, whose longest take
                is eight seconds. */}
            <div className="an-prop-row">
              <span className="an-prop-label">Length</span>
              <select
                className="an-select"
                value={shotGenSeconds}
                onChange={(e) => setShotGenSeconds(Number(e.target.value))}
              >
                {SHOT_GEN_SECONDS.map((s) => (
                  <option key={s} value={s}>
                    {s} seconds
                  </option>
                ))}
              </select>
            </div>

            {/* WHICH MODEL DRAWS IT. ⚠ Shown, not chosen: there is one image
                model and it is set in the environment (`IMAGE_PROVIDER` and
                `VERTEX_IMAGE_MODEL` / `GEMINI_IMAGE_MODEL`), so a picker over a
                list of one would be theatre. Asked for as "quality model also so
                user see which model genearte iamge".
                ⚠ BUT IT IS IN A BOX, AND THE BOX IS `.an-select`'s. It was the
                one row here whose value was bare text beside its label, so it
                broke the label-over-control rhythm the other rows keep and read
                as a different kind of thing from Quality / Size / Length in the
                ✨ Animate dialog next door — reported as "i want you keep
                consistancy in popop image and video".
                ⚠ NOT A DISABLED `<select>`, which is the obvious way to get the
                box and the wrong one: disabled greys the row out and says "this
                is off", when the fact is that the choice does not exist. A
                static field looks live and simply is not a control. */}
            <div className="an-prop-row">
              <span className="an-prop-label">Model</span>
              <span className="an-select an-select-static">
                {shotGenCtx?.model || "…"}
                {shotGenCtx?.provider && (
                  <span className="an-shot-provider"> · {shotGenCtx.provider}</span>
                )}
              </span>
            </div>

            {/* AND THE SAME BAR FOR THE DRAWING, which is the LONGER of the two
                waits by some way — a synchronous image call, where the only sign
                of life was the button relabelling itself. Same row, same reason:
                one image reports no stages and no percentage, so it slides. And
                no spinner beside it either, for the reason above — the two waits
                in this dialog must look like one mechanism. */}
            {shotGenBusy && (
              <div className="an-prop-progress an-shot-thinking" role="status">
                <span className="an-prop-progress-msg">
                  Drawing the shot in the storyboard's look…
                </span>
                <span className="an-status-bar is-waiting">
                  <span />
                </span>
              </div>
            )}

            <div className="an-name-actions">
              <button type="button" className="btn ghost" onClick={() => setShotGen(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={
                  !shotGenCtx?.can_generate || !shotGenPrompt.trim() || shotGenBusy
                }
                onClick={doGenerateShot}
              >
                {shotGenBusy ? "Drawing the shot…" : "Generate the shot"}
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
            {/* ⚠ THE SHOT'S NAME, AND IT IS HERE BECAUSE THE PROMPT BOX STOPPED
                CARRYING IT. The box used to open on the frame's LABEL, so "Shot 1"
                was the one thing on screen naming what you were about to pay to
                animate — filling the box with the board's description took that
                away. Reported as "see you remove image name like Shot1 i want yuo
                add name too in this pop up ... panel Up keep".
                Above the box, in the same label-over-control rhythm as Quality /
                Size / Length below it. */}
            <div className="an-animate-shot">
              <span className="an-animate-shot-name">
                {animateFrame?.label || "This shot"}
              </span>
              {animatePanel?.title && (
                <span className="an-animate-shot-src">from “{animatePanel.title}”</span>
              )}
            </div>
            <textarea
              className="an-tp-text"
              autoFocus
              rows={3}
              value={animatePrompt}
              placeholder="e.g. he lowers the lamp and turns towards the door; slow push in"
              maxLength={2000}
              onChange={(e) => setAnimatePrompt(e.target.value)}
            />

            {/* ⚠ WHAT THE BOARD ALREADY SAYS ABOUT THIS SHOT, and it is only a
                DRAFT. The box above opens on the panel's description instead of
                the frame's label ("Shot 1"), which is a name and not a prompt.
                Saying where the words came from is what makes editing them feel
                safe — nothing typed here is written back to the storyboard. */}
            {animatePanel?.storyboard_id && boardDraftPrompt(animatePanel) && (
              <p className="tiny muted an-animate-src">
                {/* ⚠ THE BOARD IS NAMED ONCE, in the row above the box. Saying it
                    again here was two labels for one fact. What is left is the
                    part that is not obvious: this text is a DRAFT and typing over
                    it does not touch the storyboard. */}
                Drafted from the storyboard — editing it here changes the render,
                never the board.
              </p>
            )}

            {/* The shot's spoken lines. ⚠ NEVER APPENDED ON THEIR OWN — the
                image side deliberately keeps dialogue OUT of a prompt (a drawing
                model renders words as speech bubbles), but Veo can say them, so
                this is offered as a decision rather than taken. */}
            {spokenPromptBlock(animatePanel?.dialogue) && (
              <div className="an-animate-speech">
                <DialogueBox dialogue={animatePanel.dialogue} />
                <label className="an-check">
                  <input
                    type="checkbox"
                    checked={animateSpeak}
                    onChange={(e) => toggleAnimateSpeak(e.target.checked)}
                  />
                  Have Veo speak these lines
                </label>
                {animateSpeak && !animateRender.generate_audio && (
                  <p className="tiny an-animate-warn">
                    Sound is off, so these lines would be mouthed and never
                    heard. Turn on “Let Veo generate sound too” below.
                  </p>
                )}
              </div>
            )}

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

      {/* --- 🎬 The Director: the plan, then the rail ---------------------- */}
      {/* ⚠ ONE STEP, NOT TWO, and that is the difference between this and the
          three dialogs below it. Those are "panel, then price" because they
          spend; this one previews and runs in the same card because there is
          nothing to confirm — Run costs nothing and Revert undoes all of it.
          The two-popup priced flow arrives with Veo in Phase 4. */}
      {directorOpen && (
        <DirectorPanel
          run={director}
          frames={frames}
          languages={directorLanguages}
          onClose={() => {
            director.close();
            setDirectorOpen(false);
          }}
        />
      )}

      {/* --- Captions / voiceover: the panel, then the price --------------- */}
      {/* ⚠ TWO STEPS, exactly as ✨ Animate. This panel spends nothing — it
          picks the track or the voice. The button at the bottom asks the server
          what that would cost and hands over to the confirm dialog below. */}
      {speechFor !== null && !speechConfirm && (
        <div className="modal-overlay" onClick={() => setSpeechFor(null)}>
          {/* ⚠ WIDER FOR THE VOICEOVER, because it now carries a script. The
              captions panel is two controls and stays the 28rem every other
              dialog here is. */}
          <div
            className={`card an-name-modal ${speechFor === "voiceover" ? "an-vo-modal" : ""}`}
            onClick={(e) => e.stopPropagation()}
          >
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
                  under the shot it belongs to. Change any line here before it is
                  read — nothing you type is written back to the storyboard.
                </p>

                {/* ⚠ THE DEFAULT, NOT THE VOICE. Every line below can be cast on
                    its own, and this is only what reads the ones that aren't —
                    the label used to say "Voice" when it was the only choice
                    there was. */}
                <div className="an-prop-row">
                  <span className="an-prop-label">Default voice</span>
                  <select
                    className="an-select"
                    value={speechVoice}
                    onChange={(e) => setSpeechVoice(e.target.value)}
                  >
                    {(speechSheet?.voices || []).map((v) => (
                      <option key={v.name} value={v.name}>
                        {v.name}
                        {v.tone ? ` — ${v.tone}` : ""}
                      </option>
                    ))}
                    {/* Before the sheet lands there is nothing to list, and an
                        empty picker that then changes under the cursor is worse
                        than one that says it is still reading. */}
                    {!speechSheet && <option value={speechVoice}>{speechVoice}</option>}
                  </select>
                </div>

                {/* --- THE DIALOGUE SHEET ---------------------------------- */}
                {speechSheetBusy && (
                  <p className="tiny muted">Reading the storyboard…</p>
                )}
                {!speechSheetBusy && speechSheet && !speechLines.length && (
                  <p className="an-prop-warn">
                    ⚠{" "}
                    {speechSheet.from_board
                      ? "These shots came from a storyboard, but none of them have spoken lines on it."
                      : "These clips aren't storyboard shots, so there is no dialogue to read."}
                  </p>
                )}
                {speechLines.length > 0 && (
                  <div className="an-vo-sheet">
                    {speechLines.map((line, i) => {
                      const persona = (speechSheet?.personas || []).find(
                        (p) => p.key === (line.persona || "")
                      );
                      return (
                        <div className="an-vo-line" key={`${line.frame_id}-${i}`}>
                          <div className="an-vo-head">
                            {/* WHICH SHOT, first and in the accent colour — the
                                same rhythm ✨ Animate names its shot in. */}
                            <span className="an-vo-shot">{line.shot || "This shot"}</span>
                            <span className="an-vo-who">{line.character || "A voice"}</span>
                            <span className="an-vo-at">{formatTime(line.start_ms || 0)}</span>
                          </div>
                          <textarea
                            className="an-tp-text an-vo-text"
                            rows={2}
                            maxLength={2000}
                            value={line.text}
                            onChange={(e) => patchSpeechLine(i, { text: e.target.value })}
                          />
                          <div className="an-vo-picks">
                            <label className="an-tp-field">
                              <span>Who is speaking</span>
                              <select
                                className="an-select"
                                value={line.persona || ""}
                                // ⚠ RE-CASTS THE VOICE TOO, by clearing the
                                // override. Picking "Grandfather" and still
                                // hearing the young woman you set two clicks ago
                                // is the kind of stuck state nobody finds their
                                // way out of.
                                onChange={(e) =>
                                  patchSpeechLine(i, { persona: e.target.value, voice: "" })
                                }
                              >
                                {(speechSheet?.personas || []).map((p) => (
                                  <option key={p.key} value={p.key}>
                                    {p.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="an-tp-field">
                              <span>Voice</span>
                              <select
                                className="an-select"
                                value={line.voice || ""}
                                onChange={(e) => patchSpeechLine(i, { voice: e.target.value })}
                              >
                                <option value="">{voiceForLine(line)} (cast)</option>
                                {(speechSheet?.voices || []).map((v) => (
                                  <option key={v.name} value={v.name}>
                                    {v.name}
                                    {v.tone ? ` — ${v.tone}` : ""}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </div>
                          {/* ⚠ THE ONLY VISIBLE SIGN THAT AN AGE AND A SEX
                              REACHED THE MODEL AT ALL. A voice name is a timbre;
                              this sentence is what actually gets sent with the
                              line, so it is worth reading before paying. */}
                          {persona?.direction && (
                            <p className="tiny muted an-vo-note">
                              Read as {persona.direction}.
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}

                <label className="an-check">
                  <input
                    type="checkbox"
                    checked={speechFit}
                    onChange={(e) => setSpeechFit(e.target.checked)}
                  />
                  Make each shot hold its own line (a long line stretches its
                  picture and pushes the shots after it along)
                </label>
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
                disabled={
                  speechBusy ||
                  (speechFor === "captions" && !speechTrack) ||
                  // ⚠ NOT WHILE THE SHEET IS STILL COMING, and not when it came
                  // back empty: both would price a request the server is going
                  // to refuse, in a dialog that already knows better.
                  (speechFor === "voiceover" &&
                    (speechSheetBusy ||
                      !speechLines.some((l) => (l.text || "").trim())))
                }
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
                : // ⚠ NAMES THE CAST, NOT "read by Kore". Lines can now be voiced
                  // one at a time, and a confirm dialog naming one voice over a
                  // sheet that shows four is the kind of small lie that makes a
                  // price look made up.
                  `${speechConfirm.estimate.lines} line(s), ${speechConfirm.estimate.characters} characters, read by ${
                    [...new Set(speechLines.map((l) => voiceForLine(l)))].join(", ") ||
                    speechVoice
                  }.`}
            </p>
            {speechFor === "voiceover" && speechFit && (
              <p className="tiny muted">
                Any shot shorter than its line is stretched to cover it, and the
                shots after it move along — the same as animating one does.
              </p>
            )}
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
            <h2>Save project as…</h2>
            <p className="muted">
              This project hasn't got a name yet. Give it one and it'll show up
              in Your Projects under that title.
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
      {/* A VIDEO TRACK's own ＋. ⚠ BOTH KINDS, and through `addAssets` rather
          than `addFiles`: the row holds footage and stills alike, so an
          image-only filter here hid the MP4 the same row accepted by drag and
          drop, and the image-only handler behind it would have uploaded one as a
          still if the OS let it through. The row it is filling comes from
          `pendingPictureTrack`, read once and cleared. */}
      {/* ⚠ `accept` IS SET WHEN THE DIALOG IS OPENED, not fixed here — a Stills
          row offers images, a Video row offers footage, and neither offers the
          other. Written onto the element in `addToLane` rather than rendered,
          because the row is only known at the moment of the press and re-rendering
          an input to change its filter would lose the click that opened it. */}
      <input
        ref={pictureInputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          const { track, rowKind } = takePendingTrack();
          if (e.target.files?.length) {
            addAssets(e.target.files, undefined, track, null, rowKind);
          }
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
      {/* ⚠ THERE IS NO VIDEO-ONLY INPUT ANY MORE. There was one, nothing ever
          clicked it (no `.click()` anywhere), and its `accept="video/*"` was the
          opposite half of the same mistake the row's ＋ was making: a picker that
          takes one of the two kinds a video row holds. One picker, both kinds —
          `pictureInputRef` above. */}
    </div>
  );
}
