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
  // Zero or more audio tracks, mixed on export. Music under a voiceover is the
  // pair this exists for.
  const [audioTracks, setAudioTracks] = useState([]);
  const [video, setVideo] = useState(null);
  const [sourceBoard, setSourceBoard] = useState(null);

  // --- Media ---
  const [urls, setUrls] = useState({}); // frame id → object URL
  const urlsRef = useRef({});
  // upload_id → object URL, and upload_id → its <audio> element.
  const [audioUrls, setAudioUrls] = useState({});
  const audioUrlsRef = useRef({});
  const audioElsRef = useRef({});

  // --- Playback ---
  const [timeMs, setTimeMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timeRef = useRef(0);

  // --- UI ---
  const [selectedId, setSelectedId] = useState(null);
  const [selectedTextId, setSelectedTextId] = useState(null);
  // An audio track selected for editing — its controls live in Properties, like
  // everything else that has settings.
  const [selectedTrackId, setSelectedTrackId] = useState(null);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
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
  // Exactly one thing is selected at a time, and the Properties pane follows it:
  // a text clip, else a frame, else the video itself. Selecting one clears the
  // other (see the Timeline handlers), so the pane can never show the wrong one.
  const selectedText = texts.find((c) => c.id === selectedTextId) || null;
  const selectedTrack = selectedText
    ? null
    : audioTracks.find((a) => a.upload_id === selectedTrackId) || null;
  const selectedFrame =
    selectedText || selectedTrack
      ? null
      : frames.find((f) => f.id === selectedId) || null;

  // One helper so every "select this" path clears the other two — the pane can
  // then never show something that isn't selected.
  function selectOnly({ frame = null, text = null, track = null }) {
    setSelectedId(frame);
    setSelectedTextId(text);
    setSelectedTrackId(track);
  }

  const exporting = exportJob?.status === "running" || exportBusy;
  // The longest track — what "fit frames to audio" matches, and what the
  // length comparison in the timeline header reports against.
  const audioMs = audioTracks.reduce(
    (max, a) => Math.max(max, a.duration_ms || 0),
    0
  );
  // How far the TIMELINE reaches. The video is still only as long as the frames
  // — that's what exports — but if the audio runs past them the timeline has to
  // show it, or you can't scrub into your own track to place pictures against it.
  const spanMs = Math.max(
    totalMs,
    audioTracks.reduce(
      (max, a) => Math.max(max, (a.duration_ms || 0) - (a.offset_ms || 0)),
      0
    )
  );

  // Nothing in it and never named — i.e. you opened it and did nothing. Leaving
  // such an animatic throws it away instead of leaving an empty "Untitled" on
  // the library forever.
  const isEmpty =
    !frames.length &&
    !texts.length &&
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
        setSettings(p.settings);
        setAudioTracks(p.audio_tracks || []);
        setVideo(p.video || null);
        setSourceBoard(p.source_storyboard_id || null);
        setSelectedId(p.frames?.[0]?.id || null);
        if (p.status === "running") setExportJob({ status: "running", progress: null });
        setLoading(false);
        // Whatever renders next IS the saved state — take it as the baseline.
        adoptBaselineRef.current = true;
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
        audioTracks,
      }),
    [title, settings, frames, texts, audioTracks]
  );

  // Keep the latest project in a ref so the unmount flush sees it.
  useEffect(() => {
    docRef.current = { title, settings, frames, texts, audioTracks, signature };
  }, [title, settings, frames, texts, audioTracks, signature]);

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

  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0;
    let anchorWall = performance.now();
    let anchorT = timeRef.current;

    const tick = () => {
      const now = performance.now();
      // The FIRST track that is genuinely playing is the master clock, so the
      // pictures can never drift from the sound. If it ends early (a track
      // shorter than the sequence) we carry on from the wall clock — the
      // handover is seamless because the anchor is re-set every frame, and the
      // video's length is decided by the frames, not by any track.
      const master = liveTracks().find(
        ({ el }) => !el.paused && !el.ended && !Number.isNaN(el.currentTime)
      );
      let t;
      if (master) {
        t = master.el.currentTime * 1000 - (master.track.offset_ms || 0);
      } else {
        t = anchorT + (now - anchorWall);
      }
      anchorT = t;
      anchorWall = now;

      // Runs to the end of the TIMELINE, not the end of the video: with a
      // 2-minute track under 2 seconds of pictures you still want to hear it.
      if (t >= spanMs) {
        timeRef.current = spanMs;
        setTimeMs(spanMs);
        setPlaying(false);
        for (const { el } of liveTracks()) el.pause();
        return;
      }
      timeRef.current = t;
      setTimeMs(t);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, spanMs, liveTracks]);

  function togglePlay() {
    if (playing) {
      for (const { el } of liveTracks()) el.pause();
      setPlaying(false);
      return;
    }
    if (!frames.length) return;
    if (timeRef.current >= spanMs - 30) seek(0);
    // Placed, then started, together — starting one before placing another is
    // what makes two tracks drift apart at the top of playback.
    for (const { track, el } of liveTracks()) {
      placeTrack(el, track, timeRef.current);
      el.play().catch(() => {
        /* autoplay policy — the wall clock still drives the pictures */
      });
    }
    setPlaying(true);
  }

  const stepFrame = useCallback(
    (delta) => {
      if (!frames.length) return;
      const next = Math.max(0, Math.min(frames.length - 1, currentIndex + delta));
      setSelectedId(frames[next].id);
      seek(starts[next]);
    },
    [frames, currentIndex, starts, seek]
  );

  // Space / arrows, as long as the user isn't typing into a field.
  useEffect(() => {
    function onKey(e) {
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable) return;
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "ArrowRight") {
        e.preventDefault();
        stepFrame(1);
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        stepFrame(-1);
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
  function addText() {
    const i = currentIndex >= 0 ? currentIndex : 0;
    const start = frames.length ? starts[i] : 0;
    const length = frames.length ? frames[i].duration_ms : 2000;
    const clip = newTextClip(start, length);
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
      setAudioTracks((list) => [
        ...list,
        {
          upload_id: res.upload_id,
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
  return (
    <div className="an-nle">
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
        <section className="an-pane an-pane-media">
          <div className="an-pane-head">
            <span className="an-pane-title">Media</span>
            <span className="tiny muted">{frames.length} frames</span>
          </div>
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
        </section>

        {/* ---- Program: what the viewer would see right now ---- */}
        <section className="an-pane an-pane-program">
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
        <section className="an-pane an-pane-props">
          <div className="an-pane-head">
            <span className="an-pane-title">Properties</span>
            <span className="tiny muted">
              {selectedText
                ? "Text"
                : selectedTrack
                  ? "Audio"
                  : selectedFrame
                    ? "Frame"
                    : "Video"}
            </span>
            {/* Without this there is no way back: selecting anything hides the
                whole-video settings, and nothing deselects. */}
            {(selectedText || selectedFrame || selectedTrack) && (
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
      <section className="an-pane an-pane-timeline">
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

          <span className="an-spacer" />

          <button
            type="button"
            className="btn small an-add-text"
            onClick={addText}
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
            totalMs={totalMs}
            spanMs={spanMs || 1000}
            timeMs={timeMs}
            pxPerSec={pxPerSec}
            selectedId={selectedId}
            selectedTextId={selectedTextId}
            audioTracks={audioTracks}
            audioUrls={audioUrls}
            maxAudioTracks={MAX_AUDIO_TRACKS}
            onToggleMute={(id) =>
              patchTrack(id, {
                muted: !audioTracks.find((a) => a.upload_id === id)?.muted,
              })
            }
            onSelect={(id) => selectOnly({ frame: id })}
            onSelectText={(id) => selectOnly({ text: id })}
            selectedTrackId={selectedTrackId}
            onSelectTrack={(id) => selectOnly({ track: id })}
            onSeek={seek}
            onResize={(id, ms) => patchFrame(id, { duration_ms: ms })}
            onTextChange={patchText}
            onAddImages={() => imageInputRef.current?.click()}
            onAddText={addText}
            onAddAudio={openAudioPicker}
            onAddLayer={() => setLayerMenu(true)}
            onRemoveTrack={removeTrack}
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
              <strong>{formatTime(totalMs)}</strong>
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
              The video is as long as your frames — {formatTime(totalMs)}.
              {audioMs > totalMs &&
                ` Your audio runs to ${formatTime(audioMs)}; use “Fit to audio” first if you want the whole track.`}
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
      {layerMenu && (
        <div className="modal-overlay" onClick={() => setLayerMenu(false)}>
          <div className="card an-layer-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setLayerMenu(false)}>
              ✕
            </button>
            <h2>Add a layer</h2>
            <p className="muted">What do you want to add to the timeline?</p>

            <div className="an-layer-list">
              <button
                type="button"
                className="an-layer-opt"
                onClick={() => {
                  setLayerMenu(false);
                  imageInputRef.current?.click();
                }}
              >
                <span className="an-layer-opt-ico">🖼</span>
                <span>
                  <strong>Images</strong>
                  <span className="tiny muted">
                    Added to the end of the picture sequence
                  </span>
                </span>
              </button>

              <button
                type="button"
                className="an-layer-opt"
                onClick={() => {
                  setLayerMenu(false);
                  addText();
                }}
              >
                <span className="an-layer-opt-ico">T</span>
                <span>
                  <strong>Text</strong>
                  <span className="tiny muted">
                    A caption over the frame at the playhead
                  </span>
                </span>
              </button>

              <button
                type="button"
                className="an-layer-opt"
                disabled={audioTracks.length >= MAX_AUDIO_TRACKS}
                onClick={() => {
                  setLayerMenu(false);
                  openAudioPicker();
                }}
              >
                <span className="an-layer-opt-ico">♪</span>
                <span>
                  <strong>Audio</strong>
                  <span className="tiny muted">
                    {audioTracks.length >= MAX_AUDIO_TRACKS
                      ? `You already have the maximum of ${MAX_AUDIO_TRACKS} tracks`
                      : `Its own track, mixed with the others (${audioTracks.length}/${MAX_AUDIO_TRACKS})`}
                  </span>
                </span>
              </button>

              {/* Listed because it's the obvious fourth thing to look for —
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

function AudioProperties({ track, index, onChange, onRemove }) {
  const volume = track.volume ?? 1;
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
