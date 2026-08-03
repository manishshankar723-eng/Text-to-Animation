// AnimaticEditor.jsx — the animatic screen: preview, frame strip, timeline.
//
// The important design decision is the CLOCK. Images are not advanced by a
// timer; every animation frame reads the <audio> element's currentTime and
// picks the picture whose slice of the sequence contains it. Audio is the
// master, so the pictures can never drift away from the sound — which is the
// one thing this whole feature exists to let you check.
//
// Everything here is local and free: no AI call is made, and preview costs
// nothing. Only "Export video" touches the server for real work.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api.js";
import FrameStrip, { sortFiles } from "./FrameStrip.jsx";
import { UNTITLED } from "./AnimaticLibrary.jsx";
import Timeline, { formatTime } from "./Timeline.jsx";
import Icon from "./Icon.jsx";
import ShapeGallery, {
  DEFAULT_SHAPE_COLOR,
  SHAPE_KINDS,
  ShapeSwatch,
  shapeCss,
} from "./Shapes.jsx";

const ZOOMS = [8, 16, 32, 64, 128, 256]; // pixels per second
const DEFAULT_ZOOM = 2;
const AUTOSAVE_MS = 900;
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

const ASPECTS = [
  { id: "16:9", label: "16:9", note: "Wide" },
  { id: "9:16", label: "9:16", note: "Reels" },
  { id: "1:1", label: "1:1", note: "Square" },
  { id: "4:3", label: "4:3", note: "Classic" },
  { id: "4:5", label: "4:5", note: "Portrait" },
];

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
});

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
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

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
// Shuttle speeds for J / L, in the order repeated presses step through them.
const SHUTTLE = [1, 2, 4];

const TEXT_POSITIONS = [
  { id: "top", label: "Top" },
  { id: "middle", label: "Middle" },
  { id: "bottom", label: "Bottom" },
];
const TEXT_ALIGNS = [
  { id: "left", label: "◧" },
  { id: "center", label: "▣" },
  { id: "right", label: "◨" },
];
const TEXT_SIZES = [
  { id: "small", label: "S" },
  { id: "medium", label: "M" },
  { id: "large", label: "L" },
];
const TEXT_BACKDROPS = [
  { id: "scrim", label: "Shaded bar" },
  { id: "box", label: "Solid box" },
  { id: "none", label: "Outline only" },
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

export default function AnimaticEditor({ animaticId, onBack, onDeleted }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // --- The project ---
  const [title, setTitle] = useState("");
  const [frames, setFrames] = useState([]);
  const [settings, setSettings] = useState({
    aspect_ratio: "16:9",
    fps: 24,
    fit: "contain",
    background: "#000000",
    show_labels: false,
  });
  // The text layer. Timed independently of the frames, which is why it isn't
  // just a field on a frame.
  const [texts, setTexts] = useState([]);
  // The shape layer — boxes, circles, pentagons and stars drawn over the
  // picture. Timed like the text layer, and like it, independent of the frames.
  const [shapes, setShapes] = useState([]);
  // Lanes the USER added. "+ Add layer" creates one of these and nothing else —
  // it is a blank row, filled afterwards by that row's own ＋. Every kind also
  // has an implicit DEFAULT lane (clips whose layer_id is ""), which is what an
  // animatic saved before layers is made of, and what a new one starts with.
  const [layers, setLayers] = useState([]);
  // Pictures composited over the sequence — the content of image layers.
  const [overlays, setOverlays] = useState([]);
  // Zero or more audio tracks, mixed on export. Music under a voiceover is the
  // pair this exists for.
  const [audioTracks, setAudioTracks] = useState([]);
  const [video, setVideo] = useState(null);
  const [sourceBoard, setSourceBoard] = useState(null);

  // --- Media ---
  const [urls, setUrls] = useState({}); // frame id → object URL
  const urlsRef = useRef({});
  // upload_id → object URL, for the overlay pictures.
  const [overlayUrls, setOverlayUrls] = useState({});
  const overlayUrlsRef = useRef({});
  // upload_id → object URL, and upload_id → its <audio> element.
  const [audioUrls, setAudioUrls] = useState({});
  const audioUrlsRef = useRef({});
  const audioElsRef = useRef({});

  // --- Playback ---
  const [timeMs, setTimeMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timeRef = useRef(0);
  // Shuttle speed (J / K / L). 1 is normal play and is the ONLY rate that uses
  // the audio as the master clock; see the playback effect.
  const [rate, setRate] = useState(1);
  // Mark in / out (I / O). Null = not marked. They bound PLAYBACK, not the
  // export — the export is still the whole timeline, which is what the export
  // dialog says it is.
  const [markIn, setMarkIn] = useState(null);
  const [markOut, setMarkOut] = useState(null);

  // --- UI ---
  const [selectedId, setSelectedId] = useState(null);
  const [selectedTextId, setSelectedTextId] = useState(null);
  const [selectedShapeId, setSelectedShapeId] = useState(null);
  const [selectedOverlayId, setSelectedOverlayId] = useState(null);
  // Which half of the Media pane is showing: the footage, or the shape picker.
  const [mediaTab, setMediaTab] = useState("media");
  // An audio track selected for editing — its controls live in Properties, like
  // everything else that has settings.
  const [selectedTrackId, setSelectedTrackId] = useState(null);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
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
  const [saveState, setSaveState] = useState("saved"); // saved | dirty | saving | error
  // True for a couple of seconds after a save lands, so the tick is a moment of
  // feedback rather than a permanent label.
  const [savedFlash, setSavedFlash] = useState(false);
  const [exportJob, setExportJob] = useState(null);
  const [exportBusy, setExportBusy] = useState(false);
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

  const textAreaRef = useRef(null);
  const audioInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const assetInputRef = useRef(null);
  const overlayInputRef = useRef(null);
  // Which audio lane a just-picked file belongs to ("" = a lane of its own).
  const pendingAudioLane = useRef("");
  const loadedRef = useRef(false);
  const docRef = useRef(null); // latest project, for the unmount flush
  const dirtyRef = useRef(false);
  // A signature of the project as it is ON THE SERVER. "Dirty" is decided by
  // comparing content against this — NOT by "did an effect fire", which is what
  // it used to do via a setTimeout(0) flag. That race was lost whenever React
  // invoked the load effect twice (StrictMode in dev), so a freshly created
  // animatic opened as "Unsaved changes" and immediately fired a pointless PUT.
  // Comparing content also means editing something back to its original value
  // correctly reads as saved again.
  const baselineRef = useRef(null);
  const adoptBaselineRef = useRef(false);

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

  const currentIndex = useMemo(() => {
    if (!frames.length) return -1;
    for (let i = frames.length - 1; i >= 0; i--) {
      if (timeMs >= starts[i]) return i;
    }
    return 0;
  }, [frames, starts, timeMs]);
  const currentFrame = currentIndex >= 0 ? frames[currentIndex] : null;

  // What the viewer would see right now. Empty clips are skipped here AND in the
  // exporter, so an unfinished caption never burns a blank bar into the video.
  const activeTexts = useMemo(
    () =>
      texts.filter(
        (c) =>
          (c.text || "").trim() &&
          timeMs >= c.start_ms &&
          timeMs < c.start_ms + c.duration_ms
      ),
    [texts, timeMs]
  );
  // The shapes on screen at this moment. A fully transparent one is skipped
  // here AND in the exporter, so the two always agree on what is visible.
  const activeShapes = useMemo(
    () =>
      shapes.filter(
        (s) =>
          (s.opacity ?? 1) > 0 &&
          timeMs >= s.start_ms &&
          timeMs < s.start_ms + s.duration_ms
      ),
    [shapes, timeMs]
  );
  // The overlay pictures on screen now. Same rule, and the exporter uses it too.
  const activeOverlays = useMemo(
    () =>
      overlays.filter(
        (o) =>
          (o.opacity ?? 1) > 0 &&
          timeMs >= o.start_ms &&
          timeMs < o.start_ms + o.duration_ms
      ),
    [overlays, timeMs]
  );
  // Exactly one thing is selected at a time, and the Properties pane follows it:
  // a text clip, else a shape, else a track, else a frame, else the video
  // itself. Selecting one clears the others (see `selectOnly`), so the pane can
  // never show the wrong one.
  const selectedText = texts.find((c) => c.id === selectedTextId) || null;
  const selectedShape = selectedText
    ? null
    : shapes.find((s) => s.id === selectedShapeId) || null;
  const selectedOverlay =
    selectedText || selectedShape
      ? null
      : overlays.find((o) => o.id === selectedOverlayId) || null;
  const selectedTrack =
    selectedText || selectedShape || selectedOverlay
      ? null
      : audioTracks.find((a) => a.upload_id === selectedTrackId) || null;
  const selectedFrame =
    selectedText || selectedShape || selectedOverlay || selectedTrack
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
  }) {
    setSelectedId(frame);
    setSelectedTextId(text);
    setSelectedTrackId(track);
    setSelectedShapeId(shape);
    setSelectedOverlayId(overlay);
  }

  const exporting = exportJob?.status === "running" || exportBusy;
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

  // Nothing in it and never named — i.e. you opened it and did nothing. Leaving
  // such an animatic throws it away instead of leaving an empty "Untitled" on
  // the library forever.
  const isEmpty =
    !frames.length &&
    !texts.length &&
    !shapes.length &&
    !layers.length &&
    !overlays.length &&
    !audioTracks.length &&
    !video &&
    (!title.trim() || title.trim() === UNTITLED);
  // Has content but still carries the placeholder name, so Save should ask for
  // a real one first.
  const needsName = !title.trim() || title.trim() === UNTITLED;

  // ---------------------------------------------------------------- loading
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .getAnimatic(animaticId)
      .then((p) => {
        if (!alive) return;
        setTitle(p.title);
        setFrames(p.frames || []);
        setTexts(p.texts || []);
        setShapes(p.shapes || []);
        setLayers(p.layers || []);
        setOverlays(p.overlays || []);
        setSettings(p.settings);
        setAudioTracks(p.audio_tracks || []);
        setVideo(p.video || null);
        setSourceBoard(p.source_storyboard_id || null);
        setSelectedId(p.frames?.[0]?.id || null);
        if (p.status === "running") setExportJob({ status: "running", progress: null });
        setLoading(false);
        // Whatever renders next IS the saved state — take it as the baseline.
        adoptBaselineRef.current = true;
        // …and it is also where UNDO history begins. Anything recorded before
        // this point describes an editor that hadn't loaded yet.
        historyRef.current = { past: [], future: [], present: null, sig: null, lastPush: 0 };
        setHistoryTick((t) => t + 1);
        loadedRef.current = true;
      })
      .catch((e) => {
        if (!alive) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      alive = false;
      loadedRef.current = false;
    };
  }, [animaticId]);

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

    const missing = frames.filter((f) => f.url && !urlsRef.current[f.id]);
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

  // One blob per audio track, for the waveforms and for playback.
  useEffect(() => {
    let alive = true;
    const wanted = new Set(audioTracks.map((a) => a.upload_id));

    for (const id of Object.keys(audioUrlsRef.current)) {
      if (!wanted.has(id)) {
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
      for (const url of Object.values(audioUrlsRef.current)) URL.revokeObjectURL(url);
      audioUrlsRef.current = {};
    },
    []
  );

  // ---------------------------------------------------------------- saving
  const flush = useCallback(async () => {
    if (!loadedRef.current || !dirtyRef.current) return;
    const doc = docRef.current;
    if (!doc) return;
    dirtyRef.current = false;
    // Captured BEFORE the request: if the user edits while it's in flight, the
    // new signature won't match this and the project correctly stays dirty.
    const sent = doc.signature;
    setSaveState("saving");
    try {
      await api.saveAnimatic(animaticId, {
        title: doc.title,
        settings: doc.settings,
        frames: doc.frames.map((f) => ({
          id: f.id,
          src: f.src,
          duration_ms: f.duration_ms,
          label: f.label || "",
        })),
        texts: doc.texts,
        shapes: doc.shapes,
        layers: doc.layers,
        overlays: doc.overlays.map((o) => ({ ...o, url: undefined })),
        audioTracks: doc.audioTracks,
      });
      baselineRef.current = sent;
      setSaveState("saved");
      setSavedFlash(true);
      // The exported file no longer matches the project — the server flags this
      // too, but saying so immediately is what stops a stale download.
      setVideo((v) => (v ? { ...v, stale: true } : v));
    } catch (e) {
      dirtyRef.current = true;
      setSaveState("error");
      setError(e.message);
    }
  }, [animaticId]);

  // Let the tick fade after a moment. Cleared on any new edit too, via the
  // autosave effect below.
  useEffect(() => {
    if (!savedFlash) return undefined;
    const t = setTimeout(() => setSavedFlash(false), 2200);
    return () => clearTimeout(t);
  }, [savedFlash]);

  // Exactly what a save would send — so comparing it against the baseline
  // answers "is there anything to save?" honestly.
  const signature = useMemo(
    () =>
      JSON.stringify({
        title,
        settings,
        frames: frames.map((f) => ({
          id: f.id,
          src: f.src,
          duration_ms: f.duration_ms,
          label: f.label || "",
        })),
        texts,
        shapes,
        layers,
        overlays,
        audioTracks,
      }),
    [title, settings, frames, texts, shapes, layers, overlays, audioTracks]
  );

  // Keep the latest project in a ref so the unmount flush sees it.
  useEffect(() => {
    docRef.current = {
      title, settings, frames, texts, shapes, layers, overlays, audioTracks, signature,
    };
  }, [title, settings, frames, texts, shapes, layers, overlays, audioTracks, signature]);

  // Debounced autosave. Blocked during an export (the server refuses a save
  // while ffmpeg is reading these exact frames), and retried once it ends.
  useEffect(() => {
    if (!loadedRef.current) return undefined;

    // The first render after a load establishes what "saved" looks like.
    if (adoptBaselineRef.current) {
      adoptBaselineRef.current = false;
      baselineRef.current = signature;
      dirtyRef.current = false;
      setSaveState("saved");
      return undefined;
    }

    // Content-identical to the server? Then there is nothing to save, however
    // many times React re-ran this.
    if (signature === baselineRef.current) {
      dirtyRef.current = false;
      setSaveState("saved");
      return undefined;
    }

    dirtyRef.current = true;
    setSaveState("dirty");
    setSavedFlash(false);
    if (exporting) return undefined;
    const t = setTimeout(flush, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [signature, exporting, flush]);

  useEffect(
    () => () => {
      // Leaving the editor: don't lose the last few hundred ms of edits.
      if (dirtyRef.current) flush();
    },
    [flush]
  );

  // ------------------------------------------------------------- playback
  // Every track's <audio>, paired with its project entry. Elements that haven't
  // mounted yet (a blob still loading) are simply absent.
  const liveTracks = useCallback(
    () =>
      audioTracks
        .map((track) => ({ track, el: audioElsRef.current[track.upload_id] }))
        .filter((x) => x.el),
    [audioTracks]
  );

  // Put one element at the given video time. `offset_ms` is how far into the
  // FILE the sequence starts, so file time = video time + offset.
  function placeTrack(el, track, videoMs) {
    const at = (videoMs + (track.offset_ms || 0)) / 1000;
    if (!Number.isFinite(at)) return;
    el.currentTime = Math.max(0, Math.min(el.duration || at, at));
  }

  const seek = useCallback(
    (ms) => {
      const t = Math.max(0, Math.min(spanMs, Math.round(ms)));
      timeRef.current = t;
      setTimeMs(t);
      for (const { track, el } of liveTracks()) placeTrack(el, track, t);
    },
    [spanMs, liveTracks]
  );

  // Keep the elements' own volume/mute in step with the project. Browser volume
  // caps at 1, so a track boosted above that previews at 1 — the EXPORT still
  // applies the real figure via ffmpeg's volume filter.
  useEffect(() => {
    for (const { track, el } of liveTracks()) {
      el.volume = Math.max(0, Math.min(1, track.volume ?? 1));
      el.muted = Boolean(track.muted);
    }
  }, [audioTracks, audioUrls, liveTracks]);

  // Where playback stops, and where it starts from. With no marks that's the
  // whole timeline, exactly as before.
  const playFrom = markIn ?? 0;
  const playTo = markOut ?? spanMs;

  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0;
    let anchorWall = performance.now();
    let anchorT = timeRef.current;

    const tick = () => {
      const now = performance.now();
      // At NORMAL speed the first track genuinely playing is the master clock,
      // so the pictures can never drift from the sound. If it ends early (a
      // track shorter than the sequence) we carry on from the wall clock — the
      // handover is seamless because the anchor is re-set every frame, and the
      // video's length is decided by the frames, not by any track.
      //
      // Shuttling (J/L) is wall-clock only: a browser cannot play an <audio>
      // element backwards at all, and reading currentTime as the clock while
      // scrubbing at 4x fights the element rather than following it.
      const master =
        rate === 1
          ? liveTracks().find(
              ({ el }) => !el.paused && !el.ended && !Number.isNaN(el.currentTime)
            )
          : null;
      let t;
      if (master) {
        t = master.el.currentTime * 1000 - (master.track.offset_ms || 0);
      } else {
        t = anchorT + (now - anchorWall) * rate;
      }
      anchorT = t;
      anchorWall = now;

      // Runs to the end of the TIMELINE, not the end of the video: with a
      // 2-minute track under 2 seconds of pictures you still want to hear it.
      // With marks set, the marked range is the limit instead.
      if (t >= playTo || t <= (rate < 0 ? playFrom : -1)) {
        const stopAt = clamp(t >= playTo ? playTo : playFrom, 0, spanMs);
        timeRef.current = stopAt;
        setTimeMs(stopAt);
        setPlaying(false);
        setRate(1);
        for (const { el } of liveTracks()) el.pause();
        return;
      }
      timeRef.current = t;
      setTimeMs(t);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, spanMs, liveTracks, rate, playFrom, playTo]);

  const stopPlayback = useCallback(() => {
    for (const { el } of liveTracks()) {
      el.pause();
      el.playbackRate = 1;
    }
    setPlaying(false);
    setRate(1);
  }, [liveTracks]);

  // Start (or re-start) playback at `nextRate`. Negative shuttles backwards.
  const playAt = useCallback(
    (nextRate) => {
      if (!frames.length) return;
      // Off the end (or, going backwards, off the front) — jump to the other
      // side of the marked range rather than refusing to play.
      if (nextRate > 0 && timeRef.current >= playTo - 30) seek(playFrom);
      if (nextRate < 0 && timeRef.current <= playFrom + 30) seek(playTo);
      setRate(nextRate);
      setPlaying(true);
      for (const { track, el } of liveTracks()) {
        if (nextRate < 0) {
          // No audio in reverse: browsers can't do it. The pictures still run.
          el.pause();
          continue;
        }
        placeTrack(el, track, timeRef.current);
        // Browsers accept roughly 0.06–16x; our shuttle only goes to 4.
        el.playbackRate = nextRate;
        el.play().catch(() => {
          /* autoplay policy — the wall clock still drives the pictures */
        });
      }
    },
    [frames.length, liveTracks, seek, playFrom, playTo]
  );

  const togglePlay = useCallback(() => {
    if (playing) {
      stopPlayback();
      return;
    }
    playAt(1);
  }, [playing, stopPlayback, playAt]);

  // J and L step through the shuttle speeds: press again to go faster, and
  // pressing the opposite key always drops back to 1x in that direction.
  const shuttle = useCallback(
    (direction) => {
      const current = playing ? rate : 0;
      const sameWay = Math.sign(current) === direction;
      const step = sameWay
        ? SHUTTLE[Math.min(SHUTTLE.indexOf(Math.abs(current)) + 1, SHUTTLE.length - 1)]
        : SHUTTLE[0];
      playAt(step * direction);
    },
    [playing, rate, playAt]
  );

  // One video frame at the project's frame rate — what Left/Right mean in an
  // NLE. Moving to the next PICTURE is Up/Down (the next edit point).
  const stepOneFrame = useCallback(
    (delta) => {
      const frameMs = 1000 / Math.max(1, settings.fps || 24);
      seek(timeRef.current + delta * frameMs);
    },
    [seek, settings.fps]
  );

  // The cuts in the sequence — every picture boundary, plus the two ends.
  const editPoints = useMemo(
    () => [...starts, totalMs, spanMs].filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b),
    [starts, totalMs, spanMs]
  );

  const gotoEditPoint = useCallback(
    (delta) => {
      const here = timeRef.current;
      const target =
        delta > 0
          ? editPoints.find((p) => p > here + 1)
          : [...editPoints].reverse().find((p) => p < here - 1);
      if (target === undefined) return;
      seek(target);
      const i = starts.lastIndexOf(target);
      if (i >= 0) setSelectedId(frames[i].id);
    },
    [editPoints, seek, starts, frames]
  );

  // Kept for the transport buttons, which step by PICTURE (their arrows sit
  // next to a "Frame 3 of 12" readout, so that is what they should do).
  const stepFrame = useCallback(
    (delta) => {
      if (!frames.length) return;
      const next = Math.max(0, Math.min(frames.length - 1, currentIndex + delta));
      setSelectedId(frames[next].id);
      seek(starts[next]);
    },
    [frames, currentIndex, starts, seek]
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
  // History of the whole document, because that is the unit a person means by
  // "undo": one stack, not one per layer. Entries hold the actual state arrays
  // (not JSON), so restoring is exact and costs nothing to serialise.
  const historyRef = useRef({ past: [], future: [], present: null, sig: null, lastPush: 0 });
  // Bumped on every history change purely so the toolbar's enabled/disabled
  // state re-renders — the stack itself lives in the ref.
  const [historyTick, setHistoryTick] = useState(0);
  const doc = useMemo(
    () => ({ title, settings, frames, texts, shapes, layers, overlays, audioTracks }),
    [title, settings, frames, texts, shapes, layers, overlays, audioTracks]
  );

  useEffect(() => {
    const h = historyRef.current;
    // ⚠ Nothing is recorded until the project has LOADED. An editor mounts with
    // empty frames/texts/shapes and fills them from the server a moment later;
    // recording that as an edit made the very first Ctrl+Z restore the empty
    // document and wipe the animatic on screen. The load handler resets this
    // ref, so the loaded state — not the empty one — is where history begins.
    if (!loadedRef.current) return;
    if (h.present === null || h.restoring) {
      // First render, or the state we just restored — neither is a new edit.
      h.restoring = false;
      h.present = doc;
      h.sig = signature;
      return;
    }
    if (h.sig === signature) return; // identity changed, content didn't
    // Coalesce: a drag fires dozens of changes a second, and undoing one pixel
    // at a time is useless. A burst inside half a second shares one entry.
    if (Date.now() - h.lastPush > 500) {
      h.past = [...h.past.slice(-49), h.present];
      h.lastPush = Date.now();
      setHistoryTick((t) => t + 1);
    }
    h.future = [];
    h.present = doc;
    h.sig = signature;
  }, [signature, doc]);

  const applyDoc = useCallback((snapshot) => {
    historyRef.current.restoring = true;
    setTitle(snapshot.title);
    setSettings(snapshot.settings);
    setFrames(snapshot.frames);
    setTexts(snapshot.texts);
    setShapes(snapshot.shapes);
    setLayers(snapshot.layers);
    setOverlays(snapshot.overlays);
    setAudioTracks(snapshot.audioTracks);
  }, []);

  const undo = useCallback(() => {
    const h = historyRef.current;
    if (!h.past.length) return;
    const previous = h.past[h.past.length - 1];
    h.past = h.past.slice(0, -1);
    h.future = [h.present, ...h.future].slice(0, 50);
    h.lastPush = 0; // the next real edit starts a fresh entry
    applyDoc(previous);
    setHistoryTick((t) => t + 1);
    setNotice("Undo");
  }, [applyDoc]);

  const redo = useCallback(() => {
    const h = historyRef.current;
    if (!h.future.length) return;
    const next = h.future[0];
    h.future = h.future.slice(1);
    h.past = [...h.past.slice(-49), h.present];
    h.lastPush = 0;
    applyDoc(next);
    setHistoryTick((t) => t + 1);
    setNotice("Redo");
  }, [applyDoc]);

  // Read off the ref, but recomputed when the tick says the stack moved — the
  // stack itself must not be state, or every push would re-render the editor.
  const { canUndo, canRedo } = useMemo(
    () => ({
      canUndo: historyRef.current.past.length > 0,
      canRedo: historyRef.current.future.length > 0,
    }),
    [historyTick]
  );

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
      setFrames((list) => {
        const next = [...list];
        next.splice(
          i,
          1,
          { ...source, duration_ms: offset },
          { ...source, id: newId(), duration_ms: source.duration_ms - offset }
        );
        return next;
      });
      setNotice("Cut — that picture is now two frames you can time separately.");
    },
    [frames, starts]
  );

  // Delete whatever is selected, in the same order the Properties pane picks
  // what to show — so Delete always removes the thing the pane is describing,
  // which is the only reading of "the selection" a person can act on.
  const deleteSelection = useCallback(() => {
    if (selectedText) {
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
  }, [selectedText, selectedShape, selectedOverlay, selectedTrack, selectedFrame, frames]);

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
    setFrames((list) => {
      const i = list.findIndex((f) => f.id === id);
      if (i < 0) return list;
      const copy = { ...list[i], id: newId() };
      // The picture is identical, so point the new frame at the same source —
      // its blob is fetched from the same URL and nothing is uploaded twice.
      const next = [...list];
      next.splice(i + 1, 0, copy);
      return next;
    });
  }

  function deleteFrame(id) {
    setFrames((list) => list.filter((f) => f.id !== id));
    setSelectedId((s) => (s === id ? null : s));
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
    const patch = kind === "overlay" ? patchOverlay : patchShape;
    selectOnly(kind === "overlay" ? { overlay: shape.id } : { shape: shape.id });
    const box = screenRef.current?.getBoundingClientRect();
    if (!box || !box.width || !box.height) return;

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
    };
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
    // Each audio file becomes its OWN track — dropping music and a voiceover
    // together gives you two layers, which is the point of the layer control.
    const room = MAX_AUDIO_TRACKS - audioTracks.length;
    const taking = audios.slice(0, Math.max(0, room));
    for (const file of taking) await addAudioTrack(file);

    const said = [];
    if (images.length) said.push(`${images.length} image${images.length === 1 ? "" : "s"}`);
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
    if (videos.length) ignored.push(`${videos.length} video file(s) — video isn't supported yet`);
    if (others.length) ignored.push(`${others.length} file(s) that aren't images or audio`);

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

  async function stopExport() {
    try {
      await api.stopAnimaticExport(animaticId);
      setNotice("Stopping the export…");
    } catch (e) {
      setError(e.message);
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
  const pxPerSec = ZOOMS[zoom];
  const lengthMatches = audioMs > 0 && Math.abs(audioMs - totalMs) <= 250;
  const progress = exportJob?.progress || {};

  // The workspace is a fixed-height grid — three panes over a full-width
  // timeline — rather than a page that scrolls. An editor where the picture
  // slides off screen while you drag a clip isn't usable; every pane scrolls
  // inside itself instead.
  // `~` maximizes the pane the pointer is over, exactly as Premiere does — so
  // every pane reports its own name on hover and the workspace is told which
  // one is filling it.
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

      {(error || notice || exporting) && (
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
              {currentFrame && urls[currentFrame.id] ? (
                <img
                  src={urls[currentFrame.id]}
                  alt={currentFrame.label || `Frame ${currentIndex + 1}`}
                  className={settings.fit === "cover" ? "cover" : ""}
                />
              ) : (
                <div className="an-screen-empty">
                  {frames.length ? "Loading…" : "Add images to start your animatic"}
                </div>
              )}

              {/* The shape layer, UNDER the text — a shape is usually a
                  highlight or a mask ON the art, and a caption you couldn't
                  read over it would be pointless. The exporter stacks them the
                  same way round. Everything is positioned in % of this box,
                  which is the same fraction the project stores. */}
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
                      {/* The FILL is a child, because clip-path would otherwise
                          cut off this shape's own selection outline and resize
                          handle — a star's corners are exactly where they sit. */}
                      <span
                        className="an-shape-fill"
                        style={{
                          background: s.color,
                          opacity: s.opacity ?? 1,
                          ...shapeCss(s.kind),
                        }}
                      />
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

              {/* Overlay pictures — composited over the video and UNDER the
                  text. ⚠ Rendered AFTER the shapes so they sit ON TOP of them,
                  which is the order `render_frame` composites in — the preview
                  has to be what gets exported. Placed and
                  dragged exactly like a shape, because they are the same box
                  with a picture in it instead of a colour. */}
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
                        opacity: o.opacity ?? 1,
                      }}
                      onPointerDown={(e) => startShapeDrag(e, o, "move", "overlay")}
                      title="Drag to move · drag the corner to resize"
                    >
                      {overlayUrls[o.upload_id] && (
                        // `contain`, matching the exporter: a logo dropped into
                        // a square box must not be stretched into a new logo.
                        <img className="an-overlay-img" src={overlayUrls[o.upload_id]} alt="" />
                      )}
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
                      (c) => (c.position || "bottom") === zone
                    );
                    if (!zoneClips.length) return null;
                    return (
                      <div key={zone} className={`an-text-zone an-text-${zone}`}>
                        {zoneClips.map((c) => (
                          <span
                            key={c.id}
                            className={[
                              "an-text-clip",
                              `sz-${c.size || "medium"}`,
                              `bd-${c.backdrop || "scrim"}`,
                              `al-${c.align || "center"}`,
                              selectedTextId === c.id ? "sel" : "",
                            ].join(" ")}
                            style={{ color: c.color || "#ffffff" }}
                          >
                            {c.text}
                          </span>
                        ))}
                      </div>
                    );
                  })}
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
              {selectedText
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
            {(selectedText ||
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
            {selectedText ? (
              <TextProperties
                clip={selectedText}
                totalMs={totalMs}
                textAreaRef={textAreaRef}
                onChange={patchText}
                onDuplicate={duplicateText}
                onDelete={deleteText}
                onClose={() => setSelectedTextId(null)}
              />
            ) : selectedOverlay ? (
              <ShapeProperties
                shape={selectedOverlay}
                totalMs={totalMs}
                picture={overlayUrls[selectedOverlay.upload_id]}
                onChange={patchOverlay}
                onDuplicate={duplicateOverlay}
                onDelete={deleteOverlay}
                onClose={() => setSelectedOverlayId(null)}
              />
            ) : selectedShape ? (
              <ShapeProperties
                shape={selectedShape}
                totalMs={totalMs}
                onChange={patchShape}
                onDuplicate={duplicateShape}
                onDelete={deleteShape}
                onClose={() => setSelectedShapeId(null)}
              />
            ) : selectedTrack ? (
              <AudioProperties
                track={selectedTrack}
                index={audioTracks.findIndex((a) => a.upload_id === selectedTrack.upload_id)}
                onChange={patchTrack}
                onRemove={removeTrack}
              />
            ) : selectedFrame ? (
              <FrameProperties
                frame={selectedFrame}
                index={frames.findIndex((f) => f.id === selectedFrame.id)}
                url={urls[selectedFrame.id]}
                onChange={patchFrame}
                onDuplicate={duplicateFrame}
                onDelete={deleteFrame}
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
              disabled={zoom === 0}
              onClick={() => setZoom((z) => Math.max(0, z - 1))}
              title="Zoom out"
            >
              −
            </button>
            <button
              type="button"
              className="an-tbtn small"
              disabled={zoom === ZOOMS.length - 1}
              onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))}
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
            lanes={lanes}
            audioUrls={audioUrls}
            onToggleMute={(id) =>
              patchTrack(id, {
                muted: !audioTracks.find((a) => a.upload_id === id)?.muted,
              })
            }
            onTrimTrack={(id, ms) => patchTrack(id, { trim_ms: ms })}
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
            onAddToLane={addToLane}
            onRemoveLayer={removeLayer}
            onAddLayer={() => setLayerMenu(true)}
            onRemoveTrack={removeTrack}
            tool={tool}
            snapping={snapping}
            onSplitAt={splitFrameAt}
            onZoomAt={(dir) =>
              setZoom((z) => Math.max(0, Math.min(ZOOMS.length - 1, z + dir)))
            }
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

// ---------------------------------------------------------------------------
// Properties pane — one component per selection state. Split out so the editor
// itself stays readable; they are presentational and hold no state of their own.
// ---------------------------------------------------------------------------
function TextProperties({ clip, totalMs, textAreaRef, onChange, onDuplicate, onDelete, onClose }) {
  const overruns = clip.start_ms + clip.duration_ms > totalMs;
  return (
    <div className="an-props">
      <textarea
        ref={textAreaRef}
        className="an-tp-text"
        rows={3}
        value={clip.text}
        placeholder="Type the caption — press Enter for a second line"
        onChange={(e) => onChange(clip.id, { text: e.target.value })}
      />

      <div className="an-prop-row">
        <label className="an-tp-field">
          <span>Starts at</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={(clip.start_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(clip.id, {
                start_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          <span className="an-tp-unit">s</span>
        </label>
        <label className="an-tp-field">
          <span>Stays for</span>
          <input
            type="number"
            step="0.1"
            min="0.1"
            value={(clip.duration_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(clip.id, {
                duration_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          <span className="an-tp-unit">s</span>
        </label>
      </div>

      {overruns && (
        <p className="an-prop-warn">
          ⚠ This runs past the end of the video, so part of it is never seen.
        </p>
      )}

      <div className="an-prop-row">
        <span className="an-prop-label">Position</span>
        <span className="an-tp-group">
          {TEXT_POSITIONS.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`an-tp-btn ${clip.position === p.id ? "on" : ""}`}
              onClick={() => onChange(clip.id, { position: p.id })}
            >
              {p.label}
            </button>
          ))}
        </span>
      </div>

      <div className="an-prop-row">
        <span className="an-prop-label">Align</span>
        <span className="an-tp-group">
          {TEXT_ALIGNS.map((a) => (
            <button
              key={a.id}
              type="button"
              className={`an-tp-btn ${clip.align === a.id ? "on" : ""}`}
              title={`Align ${a.id}`}
              onClick={() => onChange(clip.id, { align: a.id })}
            >
              {a.label}
            </button>
          ))}
        </span>
        <span className="an-tp-group">
          {TEXT_SIZES.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`an-tp-btn ${clip.size === s.id ? "on" : ""}`}
              title={`${s.id} text`}
              onClick={() => onChange(clip.id, { size: s.id })}
            >
              {s.label}
            </button>
          ))}
        </span>
      </div>

      <div className="an-prop-row">
        <span className="an-prop-label">Backdrop</span>
        <select
          className="an-select"
          value={clip.backdrop}
          onChange={(e) => onChange(clip.id, { backdrop: e.target.value })}
          title="How the text is kept readable over the art"
        >
          {TEXT_BACKDROPS.map((b) => (
            <option key={b.id} value={b.id}>
              {b.label}
            </option>
          ))}
        </select>
        <input
          type="color"
          className="an-colour"
          value={clip.color}
          onChange={(e) => onChange(clip.id, { color: e.target.value })}
          title="Text colour"
        />
      </div>

      <div className="an-prop-actions">
        <button type="button" className="btn small ghost" onClick={() => onDuplicate(clip.id)}>
          <Icon name="copy" /> Duplicate
        </button>
        <button type="button" className="btn small danger-btn" onClick={() => onDelete(clip.id)}>
          <Icon name="close" /> Remove
        </button>
        <button type="button" className="btn small ghost" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

// A shape's settings. Position and size are shown as PERCENTAGES of the frame,
// because that is what they are — the project stores fractions so the same
// shape lands identically at 720p and 4K, and showing pixels here would be a
// number that means nothing outside this preview.
// Serves BOTH a shape and an overlay picture: they are the same box, placed
// with the same handles and the same numbers. `picture` (a blob url) is what
// says which — an overlay has no shape kind to pick and no fill to colour.
function ShapeProperties({
  shape,
  totalMs,
  picture,
  onChange,
  onDuplicate,
  onDelete,
  onClose,
}) {
  const isPicture = picture !== undefined;
  const overruns = shape.start_ms + shape.duration_ms > totalMs;
  const pct = (v) => Math.round(v * 100);
  const setPct = (field, value, lo, hi) =>
    onChange(shape.id, { [field]: clamp((parseFloat(value) || 0) / 100, lo, hi) });

  return (
    <div className="an-props">
      <div className="an-prop-row">
        <span className="an-prop-label">{isPicture ? "Picture" : "Shape"}</span>
        {isPicture ? (
          picture ? (
            <img className="an-prop-thumb" src={picture} alt="" />
          ) : (
            <span className="tiny muted">Loading…</span>
          )
        ) : (
          <span className="an-tp-group">
            {SHAPE_KINDS.map((k) => (
              <button
                key={k.id}
                type="button"
                className={`an-tp-btn an-shape-pick ${shape.kind === k.id ? "on" : ""}`}
                title={k.label}
                onClick={() => onChange(shape.id, { kind: k.id })}
              >
                <ShapeSwatch kind={k.id} />
              </button>
            ))}
          </span>
        )}
      </div>

      <div className="an-prop-row">
        <label className="an-tp-field">
          <span>Starts at</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={(shape.start_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(shape.id, {
                start_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          <span className="an-tp-unit">s</span>
        </label>
        <label className="an-tp-field">
          <span>Stays for</span>
          <input
            type="number"
            step="0.1"
            min="0.1"
            value={(shape.duration_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(shape.id, {
                duration_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          <span className="an-tp-unit">s</span>
        </label>
      </div>

      {overruns && (
        <p className="an-prop-warn">
          ⚠ This runs past the end of the video, so part of it is never seen.
        </p>
      )}

      <div className="an-prop-row">
        <label className="an-tp-field">
          <span>X</span>
          <input
            type="number"
            step="1"
            value={pct(shape.x)}
            onChange={(e) => setPct("x", e.target.value, -0.5, 1.5)}
          />
          <span className="an-tp-unit">%</span>
        </label>
        <label className="an-tp-field">
          <span>Y</span>
          <input
            type="number"
            step="1"
            value={pct(shape.y)}
            onChange={(e) => setPct("y", e.target.value, -0.5, 1.5)}
          />
          <span className="an-tp-unit">%</span>
        </label>
      </div>

      <div className="an-prop-row">
        <label className="an-tp-field">
          <span>Width</span>
          <input
            type="number"
            step="1"
            min="2"
            value={pct(shape.w)}
            onChange={(e) => setPct("w", e.target.value, 0.02, 4)}
          />
          <span className="an-tp-unit">%</span>
        </label>
        <label className="an-tp-field">
          <span>Height</span>
          <input
            type="number"
            step="1"
            min="2"
            value={pct(shape.h)}
            onChange={(e) => setPct("h", e.target.value, 0.02, 4)}
          />
          <span className="an-tp-unit">%</span>
        </label>
      </div>

      <div className="an-prop-row an-prop-stack">
        <span className="an-prop-label">
          Opacity <span className="tiny muted">{Math.round((shape.opacity ?? 1) * 100)}%</span>
        </span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={shape.opacity ?? 1}
          onChange={(e) => onChange(shape.id, { opacity: parseFloat(e.target.value) })}
        />
      </div>

      <div className="an-prop-row">
        <span className="an-prop-label">Rotation</span>
        <input
          className="an-prop-input"
          type="number"
          step="5"
          min="-360"
          max="360"
          value={Math.round(shape.rotation || 0)}
          onChange={(e) =>
            onChange(shape.id, { rotation: clamp(parseFloat(e.target.value) || 0, -360, 360) })
          }
        />
        <span className="an-tp-unit">°</span>
        {!isPicture && (
          <input
            type="color"
            className="an-colour"
            value={shape.color}
            onChange={(e) => onChange(shape.id, { color: e.target.value })}
            title="Fill colour"
          />
        )}
      </div>

      <div className="an-prop-actions">
        <button type="button" className="btn small ghost" onClick={() => onDuplicate(shape.id)}>
          <Icon name="copy" /> Duplicate
        </button>
        <button type="button" className="btn small danger-btn" onClick={() => onDelete(shape.id)}>
          <Icon name="close" /> Remove
        </button>
        <button type="button" className="btn small ghost" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

function AudioProperties({ track, index, onChange, onRemove }) {
  const volume = track.volume ?? 1;
  const rest = Math.max(0, (track.duration_ms || 0) - (track.offset_ms || 0));
  const playLen = track.trim_ms ? Math.min(track.trim_ms, rest || track.trim_ms) : rest;
  return (
    <div className="an-props">
      <div className="an-prop-row">
        <span className="an-prop-label">Track {index + 1}</span>
        <span className="tiny" title={track.filename}>
          {track.filename}
        </span>
      </div>

      <div className="an-prop-row an-prop-stack">
        <span className="an-prop-label">Volume in the mix</span>
        <div className="an-prop-vol">
          <button
            type="button"
            className={`an-mute ${track.muted ? "on" : ""}`}
            title={track.muted ? "Unmute this track" : "Mute this track"}
            onClick={() => onChange(track.upload_id, { muted: !track.muted })}
          >
            {track.muted ? "🔇" : "🔊"}
          </button>
          <input
            className="an-vol"
            type="range"
            min="0"
            max="1.5"
            step="0.05"
            value={volume}
            disabled={track.muted}
            onChange={(e) =>
              onChange(track.upload_id, { volume: Number(e.target.value) })
            }
          />
          <span className="tiny muted an-vol-read">{Math.round(volume * 100)}%</span>
        </div>
        <p className="tiny muted an-prop-hint">
          100% is the file as recorded. Pull a music bed down to sit under a
          voice — the tracks are mixed together when the video is exported.
          {volume > 1 && " Above 100% the editor previews at 100%, but the export uses the real figure."}
        </p>
      </div>

      <div className="an-prop-row">
        <label className="an-tp-field">
          <span>Starts at</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={((track.offset_ms || 0) / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(track.upload_id, {
                offset_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
            title="How far INTO this file playback starts — use it to skip an intro"
          />
          <span className="an-tp-unit">s</span>
        </label>
        <span className="tiny muted">of {formatTime(track.duration_ms || 0)}</span>
      </div>

      <div className="an-prop-row">
        <label className="an-tp-field">
          <span>Plays for</span>
          <input
            type="number"
            step="0.1"
            min="0.1"
            value={(playLen / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(track.upload_id, {
                trim_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
            title="How much of the track plays — the same as dragging its right edge"
          />
          <span className="an-tp-unit">s</span>
        </label>
        {track.trim_ms ? (
          <button
            type="button"
            className="btn small ghost"
            onClick={() => onChange(track.upload_id, { trim_ms: null })}
            title="Play the whole file from the start point"
          >
            Use whole track
          </button>
        ) : (
          <span className="tiny muted">whole track</span>
        )}
      </div>

      <div className="an-prop-actions">
        <button
          type="button"
          className="btn small danger-btn"
          onClick={() => onRemove(track.upload_id)}
        >
          <Icon name="close" /> Remove track
        </button>
      </div>
    </div>
  );
}

function FrameProperties({ frame, index, url, onChange, onDuplicate, onDelete }) {
  return (
    <div className="an-props">
      <div className="an-prop-thumb">
        {url ? <img src={url} alt={frame.label || `Frame ${index + 1}`} /> : <span className="fs-thumb-wait" />}
      </div>

      <div className="an-prop-row">
        <span className="an-prop-label">Frame</span>
        <span className="tiny">
          {index + 1}
          {frame.label ? ` · ${frame.label}` : ""}
        </span>
      </div>

      <div className="an-prop-row">
        <label className="an-tp-field">
          <span>Held for</span>
          <input
            type="number"
            step="0.1"
            min="0.1"
            value={(frame.duration_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(frame.id, {
                duration_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          <span className="an-tp-unit">s</span>
        </label>
      </div>

      <div className="an-prop-row">
        <span className="an-prop-label">Label</span>
        <input
          className="an-prop-input"
          value={frame.label || ""}
          placeholder="Shot 1"
          onChange={(e) => onChange(frame.id, { label: e.target.value })}
          title="Shown on the timeline, and burned in when 'shot labels' is on"
        />
      </div>

      <div className="an-prop-actions">
        <button type="button" className="btn small ghost" onClick={() => onDuplicate(frame.id)}>
          <Icon name="copy" /> Duplicate
        </button>
        <button type="button" className="btn small danger-btn" onClick={() => onDelete(frame.id)}>
          <Icon name="close" /> Remove
        </button>
      </div>
    </div>
  );
}

function VideoProperties({ settings, onChange, sourceBoard }) {
  return (
    <div className="an-props">
      <p className="tiny muted an-prop-hint">
        Nothing selected — these settings apply to the whole video. Click a frame
        or a text clip to edit just that.
      </p>

      <div className="an-prop-row an-prop-stack">
        <span className="an-prop-label">Frame shape</span>
        <span className="an-set-chips">
          {ASPECTS.map((a) => (
            <button
              key={a.id}
              type="button"
              className={`opt-chip ${settings.aspect_ratio === a.id ? "active" : ""}`}
              onClick={() => onChange({ aspect_ratio: a.id })}
            >
              {a.label}
              <span className="opt-chip-note">{a.note}</span>
            </button>
          ))}
        </span>
      </div>

      <div className="an-prop-row an-prop-stack">
        <span className="an-prop-label">Images that don't fit</span>
        <span className="an-set-chips">
          <button
            type="button"
            className={`opt-chip ${settings.fit === "contain" ? "active" : ""}`}
            onClick={() => onChange({ fit: "contain" })}
          >
            Fit whole image
            <span className="opt-chip-note">bars at the edges</span>
          </button>
          <button
            type="button"
            className={`opt-chip ${settings.fit === "cover" ? "active" : ""}`}
            onClick={() => onChange({ fit: "cover" })}
          >
            Fill the frame
            <span className="opt-chip-note">crops the edges</span>
          </button>
        </span>
      </div>

      <div className="an-prop-row">
        <span className="an-prop-label">Frame rate</span>
        <select
          className="an-select"
          value={settings.fps}
          onChange={(e) => onChange({ fps: Number(e.target.value) })}
        >
          <option value={12}>12 fps</option>
          <option value={24}>24 fps (film)</option>
          <option value={25}>25 fps</option>
          <option value={30}>30 fps</option>
        </select>
      </div>

      <div className="an-prop-row">
        <span className="an-prop-label">Bar colour</span>
        <input
          type="color"
          className="an-colour"
          value={settings.background}
          onChange={(e) => onChange({ background: e.target.value })}
        />
      </div>

      <label className="an-check">
        <input
          type="checkbox"
          checked={settings.show_labels}
          onChange={(e) => onChange({ show_labels: e.target.checked })}
        />
        Burn shot labels into the video
      </label>

      {sourceBoard && (
        <p className="tiny muted an-source">
          Frames come from a storyboard — re-draw a panel there and it updates here.
        </p>
      )}
    </div>
  );
}
