// useAnimaticProject.js — the animatic DOCUMENT: loading it, holding it, and
// getting it back to the server.
//
// Everything about "is this saved?" lives here, and it is decided by COMPARING
// CONTENT against a baseline signature rather than by "did an effect fire".
// That distinction is the whole reason this file has a comment at the top: the
// flag-based version lost its race whenever React ran the load effect twice
// (StrictMode in dev), so a freshly created animatic opened as "Unsaved
// changes" and immediately fired a pointless PUT.
//
// What is deliberately NOT here: the media blobs, the selection, the export,
// and the Veo records. The first three are the editor's; the last is the
// SERVER's — a save must never be able to erase the record of a clip that was
// paid for, so `veo_clips` arrives on the project and is never sent back.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api.js";

const AUTOSAVE_MS = 900;

/**
 * Exactly what a frame looks like when it is SAVED.
 *
 * ⚠ Used by both the autosave and the dirty-check signature, and that is the
 * whole point of it being one function: when the two were written out
 * separately they were free to drift, and they had — the saved shape carried
 * only id/src/duration/label, so a frame's `scale`, `x`, `y`, `opacity` and its
 * whole `keyframes` track were computed by the editor, previewed in the monitor,
 * and then silently dropped on the way to the server. Phase 1's motion never
 * survived a reload. It does now, and a clip's source range rides on the same
 * fix.
 *
 * `url` is deliberately absent: the server fills it on read and ignores it on
 * write, so sending it back would put a stale path in the saved document.
 */
export function frameForSave(f) {
  return {
    id: f.id,
    src: f.src,
    duration_ms: f.duration_ms,
    label: f.label || "",
    kind: f.kind || "image",
    // The picture's own pan / zoom / fade, and the curves driving them.
    scale: f.scale ?? 1,
    x: f.x ?? 0.5,
    y: f.y ?? 0.5,
    opacity: f.opacity ?? 1,
    keyframes: f.keyframes || {},
    // The source window and read speed — video clips only, harmless elsewhere.
    in_ms: f.in_ms ?? 0,
    out_ms: f.out_ms ?? null,
    speed: f.speed ?? 1,
    color: f.color || "#000000",
  };
}

/**
 * @param animaticId  which project to open
 * @param serverBusy  true while the server owns this job (an export encoding or
 *                    a Veo batch rendering). `save_animatic` refuses to write
 *                    through either, so the autosave stands down and retries.
 * @param onLoaded    called once the project is on screen, with the raw
 *                    document. Everything the editor has to do with a fresh
 *                    load — pick the first frame, pick up a running export,
 *                    attach a paid Veo clip that never landed, restart the undo
 *                    stack — happens in there. It returns whether this render
 *                    may be adopted as the saved baseline: FALSE when it
 *                    changed the document, because adopting then would fold
 *                    that change into "what the server already has" and it
 *                    would never be saved.
 * @param onError     the editor's error banner
 */
export default function useAnimaticProject({ animaticId, serverBusy, onLoaded, onError }) {
  const [loading, setLoading] = useState(true);

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
  // What happens ON the cuts. Anchored to the frame each one FOLLOWS, and
  // boundary-local, so adding one changes nothing about the timeline's length
  // or about where any other clip sits — see `animatic/transitions.js`.
  const [transitions, setTransitions] = useState([]);
  // Zero or more audio tracks, mixed on export. Music under a voiceover is the
  // pair this exists for.
  const [audioTracks, setAudioTracks] = useState([]);
  const [video, setVideo] = useState(null);
  const [sourceBoard, setSourceBoard] = useState(null);
  // Every Veo render made from this editor. SERVER-owned: it arrives on the
  // project and is never sent back.
  const [veoClips, setVeoClips] = useState([]);

  const [saveState, setSaveState] = useState("saved"); // saved | dirty | saving | error
  // True for a couple of seconds after a save lands, so the tick is a moment of
  // feedback rather than a permanent label.
  const [savedFlash, setSavedFlash] = useState(false);

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

  // The load handler is called from inside a promise, long after the render
  // that created it — read it off a ref so a changed callback identity can
  // never restart the load.
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

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
        setTransitions(p.transitions || []);
        setSettings(p.settings);
        setAudioTracks(p.audio_tracks || []);
        setVideo(p.video || null);
        setVeoClips(p.veo_clips || []);
        setSourceBoard(p.source_storyboard_id || null);
        setLoading(false);
        // Whatever renders next IS the saved state — take it as the baseline.
        // ⚠ UNLESS the editor changed something on the way in (it attaches any
        // paid Veo clip that never landed). Adopting the baseline then would
        // fold that attach into "what the server already has", so it would
        // never be saved — the clip would look attached, and be gone again on
        // the next reload. Leaving the baseline unset makes the attach read as
        // an ordinary unsaved edit, which the autosave then persists.
        adoptBaselineRef.current = onLoadedRef.current?.(p) !== false;
        loadedRef.current = true;
      })
      .catch((e) => {
        if (!alive) return;
        onErrorRef.current?.(e.message);
        setLoading(false);
      });
    return () => {
      alive = false;
      loadedRef.current = false;
    };
  }, [animaticId]);

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
        frames: doc.frames.map(frameForSave),
        texts: doc.texts,
        shapes: doc.shapes,
        layers: doc.layers,
        overlays: doc.overlays.map((o) => ({ ...o, url: undefined })),
        transitions: doc.transitions,
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
      onErrorRef.current?.(e.message);
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
        frames: frames.map(frameForSave),
        texts,
        shapes,
        layers,
        overlays,
        transitions,
        audioTracks,
      }),
    [title, settings, frames, texts, shapes, layers, overlays, transitions, audioTracks]
  );

  // The document as one object — what the undo stack snapshots, and (with the
  // signature alongside) what the unmount flush sends.
  const doc = useMemo(
    () => ({
      title, settings, frames, texts, shapes, layers, overlays, transitions,
      audioTracks,
    }),
    [title, settings, frames, texts, shapes, layers, overlays, transitions, audioTracks]
  );

  // Keep the latest project in a ref so the unmount flush sees it.
  useEffect(() => {
    docRef.current = { ...doc, signature };
  }, [doc, signature]);

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
    // Held back while the server owns this job — an autosave firing into a
    // render would 409 and flash "⚠ Not saved" at someone watching a clip they
    // have just paid for. The edit stays dirty and goes as soon as it ends.
    if (serverBusy) return undefined;
    const t = setTimeout(flush, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [signature, serverBusy, flush]);

  useEffect(
    () => () => {
      // Leaving the editor: don't lose the last few hundred ms of edits.
      if (dirtyRef.current) flush();
    },
    [flush]
  );

  // Put a whole document back — what undo / redo restores. Stable, so the undo
  // stack's own callbacks never change identity.
  const applySnapshot = useCallback((snapshot) => {
    setTitle(snapshot.title);
    setSettings(snapshot.settings);
    setFrames(snapshot.frames);
    setTexts(snapshot.texts);
    setShapes(snapshot.shapes);
    setLayers(snapshot.layers);
    setOverlays(snapshot.overlays);
    setTransitions(snapshot.transitions);
    setAudioTracks(snapshot.audioTracks);
  }, []);

  return {
    loading,
    // the document
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
    // saving
    saveState, savedFlash, flush,
    loadedRef, dirtyRef, baselineRef,
  };
}
