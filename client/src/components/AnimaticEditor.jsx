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
import Timeline, { formatTime } from "./Timeline.jsx";

const ZOOMS = [8, 16, 32, 64, 128, 256]; // pixels per second
const DEFAULT_ZOOM = 2;
const AUTOSAVE_MS = 900;
const MIN_MS = 100;

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
  const [audio, setAudio] = useState(null);
  const [video, setVideo] = useState(null);
  const [sourceBoard, setSourceBoard] = useState(null);

  // --- Media ---
  const [urls, setUrls] = useState({}); // frame id → object URL
  const urlsRef = useRef({});
  const [audioUrl, setAudioUrl] = useState(null);
  const audioUrlRef = useRef(null);

  // --- Playback ---
  const [timeMs, setTimeMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timeRef = useRef(0);
  const audioRef = useRef(null);

  // --- UI ---
  const [selectedId, setSelectedId] = useState(null);
  const [selectedTextId, setSelectedTextId] = useState(null);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const [uploading, setUploading] = useState(false);
  const [saveState, setSaveState] = useState("saved"); // saved | dirty | saving | error
  const [exportJob, setExportJob] = useState(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const textAreaRef = useRef(null);
  const audioInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const loadedRef = useRef(false);
  const docRef = useRef(null); // latest project, for the unmount flush
  const dirtyRef = useRef(false);

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
  const selectedText = texts.find((c) => c.id === selectedTextId) || null;

  const exporting = exportJob?.status === "running" || exportBusy;
  const audioMs = audio?.duration_ms || 0;

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
        setAudio(p.audio || null);
        setVideo(p.video || null);
        setSourceBoard(p.source_storyboard_id || null);
        setSelectedId(p.frames?.[0]?.id || null);
        if (p.status === "running") setExportJob({ status: "running", progress: null });
        setLoading(false);
        // Mark loaded on the NEXT tick so the state writes above don't look
        // like user edits and trigger an immediate pointless save.
        setTimeout(() => (loadedRef.current = true), 0);
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

  // The audio blob, for both the waveform and playback.
  useEffect(() => {
    let alive = true;
    if (!audio) {
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
      setAudioUrl(null);
      return;
    }
    api
      .fetchAnimaticMedia(`/animatics/${animaticId}/audio`)
      .then((url) => {
        if (!alive) {
          URL.revokeObjectURL(url);
          return;
        }
        if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
        audioUrlRef.current = url;
        setAudioUrl(url);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [audio?.upload_id, animaticId]);

  // Object URLs are page-lifetime unless revoked — clean the lot up on the way out.
  useEffect(
    () => () => {
      for (const url of Object.values(urlsRef.current)) URL.revokeObjectURL(url);
      urlsRef.current = {};
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    },
    []
  );

  // ---------------------------------------------------------------- saving
  const flush = useCallback(async () => {
    if (!loadedRef.current || !dirtyRef.current) return;
    const doc = docRef.current;
    if (!doc) return;
    dirtyRef.current = false;
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
        audio: doc.audio || undefined,
        clearAudio: !doc.audio,
      });
      setSaveState("saved");
      // The exported file no longer matches the project — the server flags this
      // too, but saying so immediately is what stops a stale download.
      setVideo((v) => (v ? { ...v, stale: true } : v));
    } catch (e) {
      dirtyRef.current = true;
      setSaveState("error");
      setError(e.message);
    }
  }, [animaticId]);

  // Keep the latest project in a ref so the unmount flush sees it.
  useEffect(() => {
    docRef.current = { title, settings, frames, texts, audio };
  }, [title, settings, frames, texts, audio]);

  // Debounced autosave. Blocked during an export (the server refuses a save
  // while ffmpeg is reading these exact frames), and retried once it ends.
  useEffect(() => {
    if (!loadedRef.current) return;
    dirtyRef.current = true;
    setSaveState("dirty");
    if (exporting) return;
    const t = setTimeout(flush, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [title, settings, frames, texts, audio, exporting, flush]);

  useEffect(
    () => () => {
      // Leaving the editor: don't lose the last few hundred ms of edits.
      if (dirtyRef.current) flush();
    },
    [flush]
  );

  // ------------------------------------------------------------- playback
  const offsetMs = audio?.offset_ms || 0;

  const seek = useCallback(
    (ms) => {
      const t = Math.max(0, Math.min(totalMs, Math.round(ms)));
      timeRef.current = t;
      setTimeMs(t);
      const el = audioRef.current;
      if (el && audioUrl) {
        const at = (t + offsetMs) / 1000;
        if (Number.isFinite(at)) {
          el.currentTime = Math.max(0, Math.min(el.duration || at, at));
        }
      }
    },
    [totalMs, audioUrl, offsetMs]
  );

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let anchorWall = performance.now();
    let anchorT = timeRef.current;

    const tick = () => {
      const now = performance.now();
      const el = audioRef.current;
      let t;
      // Audio is the master clock while it's genuinely playing. If it ends
      // early (a track shorter than the sequence) we carry on from the wall
      // clock — the handover is seamless because the anchor is re-set every
      // frame, and the video's length is decided by the frames, not the track.
      if (el && audioUrl && !el.paused && !el.ended) {
        t = el.currentTime * 1000 - offsetMs;
      } else {
        t = anchorT + (now - anchorWall);
      }
      anchorT = t;
      anchorWall = now;

      if (t >= totalMs) {
        timeRef.current = totalMs;
        setTimeMs(totalMs);
        setPlaying(false);
        audioRef.current?.pause();
        return;
      }
      timeRef.current = t;
      setTimeMs(t);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, totalMs, audioUrl, offsetMs]);

  function togglePlay() {
    if (playing) {
      audioRef.current?.pause();
      setPlaying(false);
      return;
    }
    if (!frames.length) return;
    if (timeRef.current >= totalMs - 30) seek(0);
    const el = audioRef.current;
    if (el && audioUrl) {
      const at = (timeRef.current + offsetMs) / 1000;
      el.currentTime = Math.max(0, Math.min(el.duration || at, at));
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

  async function pickAudio(file) {
    if (!file) return;
    setError("");
    try {
      const [res, durationMs] = await Promise.all([
        api.uploadAnimaticAudio(animaticId, file),
        measureAudio(file),
      ]);
      setAudio({
        upload_id: res.upload_id,
        filename: res.filename || file.name,
        duration_ms: durationMs,
        offset_ms: 0,
      });
      setNotice(
        durationMs
          ? `Audio added — ${formatTime(durationMs)} long.`
          : "Audio added."
      );
    } catch (e) {
      setError(e.message);
    }
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
  const pxPerSec = ZOOMS[zoom];
  const lengthMatches = audioMs > 0 && Math.abs(audioMs - totalMs) <= 250;
  const progress = exportJob?.progress || {};

  return (
    <div className="workflow-head-wrap an-editor">
      {/* -------------------------------------------------------- top bar */}
      <div className="an-topbar">
        <button type="button" className="btn small" onClick={onBack}>
          ← Your Animatics
        </button>

        <input
          className="an-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Untitled animatic"
          aria-label="Animatic title"
        />

        <span className={`an-save an-save-${saveState}`}>
          {saveState === "saving" && (
            <>
              <span className="spinner-inline" /> Saving…
            </>
          )}
          {saveState === "saved" && "✓ Saved"}
          {saveState === "dirty" && "• Unsaved"}
          {saveState === "error" && "⚠ Not saved"}
        </span>

        <span className="an-spacer" />

        {exporting ? (
          <button type="button" className="btn danger-btn" onClick={stopExport}>
            ⏹ Stop export
          </button>
        ) : (
          <button
            type="button"
            className="btn primary an-export"
            disabled={!frames.length}
            onClick={startExport}
            title="Encode an MP4 of exactly what you see here"
          >
            ⬇ Export video
          </button>
        )}
      </div>

      {error && <div className="error">{error}</div>}
      {notice && !error && <div className="info-msg an-notice">{notice}</div>}

      {exporting && (
        <div className="job-progress">
          <div className="jp-row">
            <span className="jp-msg">
              <span className="spinner-inline" />
              {progress.message || "Preparing…"}
            </span>
            <span className="jp-pct">{progress.percent ?? 0}%</span>
          </div>
          <div className="jp-bar">
            <div className="jp-fill" style={{ width: `${progress.percent ?? 0}%` }} />
          </div>
        </div>
      )}

      {video && !exporting && (
        <div className={`an-video-bar ${video.stale ? "stale" : ""}`}>
          <span>
            {video.stale
              ? "⚠ Your last export is out of date — you've edited the animatic since."
              : `🎬 Video ready — ${formatTime(video.duration_ms)}, ${video.width}×${video.height}, ${(
                  (video.size_bytes || 0) / 1048576
                ).toFixed(1)} MB`}
          </span>
          <button
            type="button"
            className="btn small"
            onClick={() => api.downloadAnimaticVideo(animaticId, `${title || "animatic"}.mp4`)}
          >
            ⬇ Download MP4
          </button>
        </div>
      )}

      {/* ------------------------------------------------------- preview */}
      <div className="an-stage">
        <div className="an-screen" style={{ aspectRatio: aspectCss, background: settings.background }}>
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
          {/* The text layer, over the picture. Sized in `cqh` (a fraction of
              this box's own height) using the SAME divisors the exporter uses,
              so the preview and the MP4 agree by construction rather than by
              two numbers that have to be kept in step by hand. */}
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

        <div className="an-transport">
          <button type="button" className="an-tbtn" onClick={() => seek(0)} title="Back to start">
            ⏮
          </button>
          <button type="button" className="an-tbtn" onClick={() => stepFrame(-1)} title="Previous frame">
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
          <span className="an-clock">
            {formatTime(timeMs)} <span className="muted">/ {formatTime(totalMs)}</span>
          </span>
          <span className="an-shotnum">
            {currentIndex >= 0 ? `Frame ${currentIndex + 1} of ${frames.length}` : ""}
          </span>
        </div>
      </div>

      {/* The real clock. Hidden, but it is what the pictures follow. */}
      {audioUrl && <audio ref={audioRef} src={audioUrl} preload="auto" />}

      {/* ---------------------------------------------------- frame strip */}
      <FrameStrip
        frames={frames}
        urls={urls}
        selectedId={selectedId}
        uploading={uploading}
        onSelect={(id) => {
          setSelectedId(id);
          const i = frames.findIndex((f) => f.id === id);
          if (i >= 0) seek(starts[i]);
        }}
        onReorder={reorder}
        onDuration={(id, ms) => patchFrame(id, { duration_ms: ms })}
        onDelete={deleteFrame}
        onDuplicate={duplicateFrame}
        onAddFiles={addFiles}
      />

      {/* --------------------------------------------------- audio + tools */}
      <div className="an-tools">
        {/* The "add a layer" row: text and audio sit together, because that is
            what they are — the two things you lay over the pictures. */}
        <div className="an-tool-group">
          <button
            type="button"
            className="btn small an-add-text"
            onClick={addText}
            title="Add a text clip over the frame at the playhead"
          >
            T ＋ Add text
          </button>
        </div>

        <div className="an-tool-group">
          {/* One hidden input for the whole editor: this button AND the ＋ on
              the Audio layer in the timeline both open it, so there is a single
              place that turns a chosen file into the track. */}
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
          <button type="button" className="btn small" onClick={openAudioPicker}>
            {audio ? "♪ Replace audio" : "♪ Add audio (MP3)"}
          </button>
          {audio && (
            <>
              <span className="an-audio-name" title={audio.filename}>
                {audio.filename}{" "}
                <span className="muted">({formatTime(audioMs)})</span>
              </span>
              <button
                type="button"
                className="btn small ghost"
                onClick={() => {
                  setAudio(null);
                  setNotice("Audio removed.");
                }}
              >
                ✕ Remove
              </button>
            </>
          )}
        </div>

        <div className="an-tool-group">
          <button
            type="button"
            className="btn small"
            disabled={!audioMs || !frames.length}
            onClick={fitToAudio}
            title="Stretch every frame proportionally so the video is exactly as long as the audio"
          >
            ⇔ Fit frames to audio
          </button>
          <span className="an-setall">
            Set all to
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
        </div>

        <div className="an-tool-group an-zoom">
          <span className="muted">Zoom</span>
          <button
            type="button"
            className="an-tbtn small"
            disabled={zoom === 0}
            onClick={() => setZoom((z) => Math.max(0, z - 1))}
          >
            −
          </button>
          <button
            type="button"
            className="an-tbtn small"
            disabled={zoom === ZOOMS.length - 1}
            onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))}
          >
            ＋
          </button>
        </div>
      </div>

      {/* ------------------------------------------------------- timeline */}
      <div className="an-timeline">
        <div className="an-tl-head">
          <span className="an-tl-total">
            Video length <strong>{formatTime(totalMs)}</strong>
            {audioMs > 0 && (
              <span className={`an-match ${lengthMatches ? "ok" : "off"}`}>
                {lengthMatches
                  ? "✓ matches the audio"
                  : `audio is ${formatTime(audioMs)} — ${
                      totalMs > audioMs ? "video runs longer" : "video ends early"
                    }`}
              </span>
            )}
          </span>
        </div>
        <Timeline
          frames={frames}
          texts={texts}
          totalMs={totalMs || 1000}
          timeMs={timeMs}
          pxPerSec={pxPerSec}
          selectedId={selectedId}
          selectedTextId={selectedTextId}
          audioUrl={audioUrl}
          audioOffsetMs={offsetMs}
          onSelect={setSelectedId}
          onSelectText={setSelectedTextId}
          onSeek={seek}
          onResize={(id, ms) => patchFrame(id, { duration_ms: ms })}
          onTextChange={patchText}
          onAddImages={() => imageInputRef.current?.click()}
          onAddText={addText}
          onAddAudio={openAudioPicker}
          hasAudio={Boolean(audio)}
        />

        {/* The Images layer's ＋ opens this. The frame strip has its own picker
            for the same job; both end up in addFiles(), which is the single
            place an upload becomes frames. */}
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
      </div>

      {/* ------------------------------------------------- text inspector */}
      {selectedText && (
        <div className="an-text-panel">
          <div className="an-tp-head">
            <strong>Text</strong>
            <span className="muted">
              {formatTime(selectedText.start_ms)} → {formatTime(selectedText.start_ms + selectedText.duration_ms)}
              {selectedText.start_ms + selectedText.duration_ms > totalMs &&
                " · runs past the end of the video"}
            </span>
            <span className="an-spacer" />
            <button
              type="button"
              className="btn small ghost"
              onClick={() => duplicateText(selectedText.id)}
              title="Copy this text and place it straight after"
            >
              ⧉ Duplicate
            </button>
            <button
              type="button"
              className="btn small danger-btn"
              onClick={() => deleteText(selectedText.id)}
            >
              ✕ Remove
            </button>
            <button
              type="button"
              className="btn small ghost"
              onClick={() => setSelectedTextId(null)}
            >
              Done
            </button>
          </div>

          <textarea
            ref={textAreaRef}
            className="an-tp-text"
            rows={2}
            value={selectedText.text}
            placeholder="Type the caption — press Enter for a second line"
            onChange={(e) => patchText(selectedText.id, { text: e.target.value })}
          />

          <div className="an-tp-rows">
            <label className="an-tp-field">
              <span>Starts at</span>
              <input
                type="number"
                step="0.1"
                min="0"
                value={(selectedText.start_ms / 1000).toFixed(1)}
                onChange={(e) =>
                  patchText(selectedText.id, {
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
                value={(selectedText.duration_ms / 1000).toFixed(1)}
                onChange={(e) =>
                  patchText(selectedText.id, {
                    duration_ms: Math.max(
                      100,
                      Math.round(parseFloat(e.target.value || 0) * 1000)
                    ),
                  })
                }
              />
              <span className="an-tp-unit">s</span>
            </label>

            <span className="an-tp-group">
              {TEXT_POSITIONS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`an-tp-btn ${selectedText.position === p.id ? "on" : ""}`}
                  onClick={() => patchText(selectedText.id, { position: p.id })}
                >
                  {p.label}
                </button>
              ))}
            </span>

            <span className="an-tp-group">
              {TEXT_ALIGNS.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className={`an-tp-btn ${selectedText.align === a.id ? "on" : ""}`}
                  title={`Align ${a.id}`}
                  onClick={() => patchText(selectedText.id, { align: a.id })}
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
                  className={`an-tp-btn ${selectedText.size === s.id ? "on" : ""}`}
                  title={`${s.id} text`}
                  onClick={() => patchText(selectedText.id, { size: s.id })}
                >
                  {s.label}
                </button>
              ))}
            </span>

            <select
              className="an-select"
              value={selectedText.backdrop}
              onChange={(e) => patchText(selectedText.id, { backdrop: e.target.value })}
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
              value={selectedText.color}
              onChange={(e) => patchText(selectedText.id, { color: e.target.value })}
              title="Text colour"
            />
          </div>
        </div>
      )}

      {/* ------------------------------------------------------- settings */}
      <div className="an-settings">
        <button
          type="button"
          className="an-settings-toggle"
          onClick={() => setShowSettings((s) => !s)}
        >
          {showSettings ? "▾" : "▸"} Video settings
          <span className="muted">
            {" "}
            — {settings.aspect_ratio}, {settings.fps} fps,{" "}
            {settings.fit === "cover" ? "fill frame" : "fit whole image"}
          </span>
        </button>

        {showSettings && (
          <div className="an-settings-body">
            <div className="an-set-row">
              <span className="an-set-label">Frame shape</span>
              <span className="an-set-chips">
                {ASPECTS.map((a) => (
                  <button
                    key={a.id}
                    type="button"
                    className={`opt-chip ${settings.aspect_ratio === a.id ? "active" : ""}`}
                    onClick={() => setSettings((s) => ({ ...s, aspect_ratio: a.id }))}
                  >
                    {a.label}
                    <span className="opt-chip-note">{a.note}</span>
                  </button>
                ))}
              </span>
            </div>

            <div className="an-set-row">
              <span className="an-set-label">Images that don't fit</span>
              <span className="an-set-chips">
                <button
                  type="button"
                  className={`opt-chip ${settings.fit === "contain" ? "active" : ""}`}
                  onClick={() => setSettings((s) => ({ ...s, fit: "contain" }))}
                >
                  Fit whole image
                  <span className="opt-chip-note">bars at the edges</span>
                </button>
                <button
                  type="button"
                  className={`opt-chip ${settings.fit === "cover" ? "active" : ""}`}
                  onClick={() => setSettings((s) => ({ ...s, fit: "cover" }))}
                >
                  Fill the frame
                  <span className="opt-chip-note">crops the edges</span>
                </button>
              </span>
            </div>

            <div className="an-set-row">
              <span className="an-set-label">Frame rate</span>
              <select
                className="an-select"
                value={settings.fps}
                onChange={(e) => setSettings((s) => ({ ...s, fps: Number(e.target.value) }))}
              >
                <option value={12}>12 fps</option>
                <option value={24}>24 fps (film)</option>
                <option value={25}>25 fps</option>
                <option value={30}>30 fps</option>
              </select>

              <span className="an-set-label">Bar colour</span>
              <input
                type="color"
                className="an-colour"
                value={settings.background}
                onChange={(e) => setSettings((s) => ({ ...s, background: e.target.value }))}
              />

              <label className="an-check">
                <input
                  type="checkbox"
                  checked={settings.show_labels}
                  onChange={(e) => setSettings((s) => ({ ...s, show_labels: e.target.checked }))}
                />
                Burn shot labels into the video
              </label>
            </div>

            <div className="an-set-row an-danger-row">
              {confirmDelete ? (
                <>
                  <span>Delete this animatic and its uploads? The storyboard is untouched.</span>
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
                </>
              ) : (
                <button
                  type="button"
                  className="btn small ghost"
                  onClick={() => setConfirmDelete(true)}
                >
                  🗑 Delete this animatic
                </button>
              )}
              {sourceBoard && (
                <span className="muted an-source">
                  Frames come from a storyboard — re-draw a panel there and it updates here.
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
