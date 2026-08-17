// useTimelineTransport.js — the playhead: where it is, whether it is moving,
// how fast, and between which two marks.
//
// ⚠ AUDIO IS THE MASTER CLOCK. Images are not advanced by a timer; at normal
// speed the first track genuinely playing is read every animation frame and the
// picture is whichever one's slice of the sequence contains it. The pictures can
// therefore never drift away from the sound, which is the one thing the whole
// feature exists to let you check. Shuttling (J/L) is the exception and is wall
// clock only — a browser cannot play an <audio> element backwards at all.
//
// Everything else that moves is a SLAVE to that clock, including the <video>
// elements in the monitor: time only ever flows INTO them (see `videoCues`).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { clamp } from "./util.js";
import { duckEnvelope, envelopeAt, eqGains, fadeGainAt } from "./audio_mix.js";
import { applyTrackAudio, resumeAudio } from "./audio_engine.js";

// Shuttle speeds for J / L, in the order repeated presses step through them.
const SHUTTLE = [1, 2, 4];
// How far a <video> may drift from the clock before it is pulled back.
const DRIFT_MS = 120;

// Put one element at the given video time. `offset_ms` is how far into the
// FILE the sequence starts, so file time = video time + offset.
function placeTrack(el, track, videoMs) {
  const at = (videoMs + (track.offset_ms || 0)) / 1000;
  if (!Number.isFinite(at)) return;
  el.currentTime = Math.max(0, Math.min(el.duration || at, at));
}

/**
 * @param frames        the sequence, for "is there anything to play"
 * @param audioTracks   the project's tracks, and `audioElsRef` their <audio>s
 * @param audioUrls     blob urls, only as a signal that an element has mounted
 * @param audioAnalyses upload_id → the decoded analysis, for the duck's key
 * @param videoElsRef   clip id → the <video> showing it in the monitor
 * @param spanMs        how far the TIMELINE reaches (may exceed the video)
 * @param totalMs       how long the video is
 * @param exportMs      how long the EXPORT will be — where a fade out lands
 * @param starts        each frame's start time, for the edit points
 * @param fps           the project's frame rate, for a one-frame step
 * @param onSelectFrame follows the playhead onto the picture it lands on
 */
export default function useTimelineTransport({
  frames,
  audioTracks,
  audioElsRef,
  audioUrls,
  audioAnalyses = {},
  spanMs,
  totalMs,
  exportMs = 0,
  starts,
  fps,
  onSelectFrame,
}) {
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

  // Every track's <audio>, paired with its project entry. Elements that haven't
  // mounted yet (a blob still loading) are simply absent.
  const liveTracks = useCallback(
    () =>
      audioTracks
        .map((track) => ({ track, el: audioElsRef.current[track.upload_id] }))
        .filter((x) => x.el),
    [audioTracks, audioElsRef]
  );

  // --- The mix ------------------------------------------------------------
  // A duck is a compressor keyed off the voice, so the preview needs the voice's
  // shape before it can follow it. This is the SAME compressor law the exporter
  // hands to sidechaincompress, run over the envelope `beats.js` decoded — close
  // to the export rather than identical to it, which is stated in the pane.
  //
  // ⚠ Keyed off a track that is NAMED as someone's voice, never guessed at, and
  // a voice is never itself ducked — both rules are `_duck_pairs` in animatic.py,
  // and a preview that ducked a different track from the export would be worse
  // than one that didn't duck at all.
  // Keyed on the fields the duck is MADE of, not on the track list: dragging a
  // volume slider rewrites every track sixty times a second, and re-running a
  // compressor over four minutes of envelope each time would be felt.
  const duckKey = audioTracks
    .map((t) => `${t.upload_id}:${t.role || ""}:${t.duck_to ?? 1}:${t.duck_target || ""}:${t.offset_ms || 0}`)
    .join("|");
  const duckEnvs = useMemo(() => {
    const out = {};
    const voices = audioTracks.filter((t) => t.role === "voice");
    const voiceOf = (track) =>
      audioTracks.find((t) => t.upload_id && t.upload_id === track.duck_target) || voices[0];
    const sources = new Set();
    for (const track of audioTracks) {
      if ((track.duck_to ?? 1) >= 1) continue;
      const voice = voiceOf(track);
      if (voice && voice.upload_id !== track.upload_id) sources.add(voice.upload_id);
    }
    for (const track of audioTracks) {
      const to = track.duck_to ?? 1;
      if (to >= 1 || sources.has(track.upload_id)) continue;
      const voice = voiceOf(track);
      if (!voice || voice.upload_id === track.upload_id) continue;
      const analysis = audioAnalyses[voice.upload_id];
      if (!analysis) continue;
      out[track.upload_id] = {
        env: duckEnvelope(analysis.envelope, analysis.hopMs, to),
        hopMs: analysis.hopMs,
        // The envelope is in FILE time, and the voice starts `offset_ms` in.
        offsetMs: voice.offset_ms || 0,
      };
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `duckKey` IS the
    // part of `audioTracks` this reads; see above.
  }, [duckKey, audioAnalyses]);

  // What ONE track's level is at this moment: its fader, its fades, and the
  // duck. ⚠ NOT clamped to 1 — playback goes through a gain node now, so a
  // track set to 150% previews at 150%, which is what the export does. See
  // `audio_engine.js` for why that used to be impossible.
  const gainAt = useCallback(
    (track, videoMs) => {
      let gain = Math.max(0, track.volume ?? 1);
      gain *= fadeGainAt(track, videoMs, exportMs || spanMs);
      const duck = duckEnvs[track.upload_id];
      if (duck) gain *= envelopeAt(duck.env, duck.hopMs, videoMs + duck.offsetMs);
      return Math.max(0, gain);
    },
    [duckEnvs, exportMs, spanMs]
  );

  // Applied on every animation frame while playing, so writing an unchanged
  // number back is skipped — every one of these is an audio-thread parameter.
  // A WeakMap, not a Map: an element that leaves the page must not be held here
  // by the thing that remembers what its level was.
  const appliedRef = useRef(new WeakMap());
  const applyGains = useCallback(
    (videoMs) => {
      for (const { track, el } of liveTracks()) {
        const gain = gainAt(track, videoMs);
        const eq = eqGains(track);
        const was = appliedRef.current.get(el);
        if (
          was &&
          Math.abs(was.gain - gain) < 0.002 &&
          was.eq.every((v, i) => v === eq[i])
        ) {
          continue;
        }
        appliedRef.current.set(el, { gain, eq });
        applyTrackAudio(el, gain, eq);
      }
    },
    [liveTracks, gainAt]
  );

  const seek = useCallback(
    (ms) => {
      const t = Math.max(0, Math.min(spanMs, Math.round(ms)));
      timeRef.current = t;
      setTimeMs(t);
      for (const { track, el } of liveTracks()) placeTrack(el, track, t);
      applyGains(t);
    },
    [spanMs, liveTracks, applyGains]
  );

  // Keep the elements' own level and mute in step with the project.
  useEffect(() => {
    for (const { track, el } of liveTracks()) el.muted = Boolean(track.muted);
    applyGains(timeRef.current);
  }, [audioTracks, audioUrls, liveTracks, applyGains]);

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
      // The fades and the duck are a gain PER MOMENT, so they are applied here
      // rather than in an effect — an effect only runs when the project changes,
      // and a fade that only moved when you edited something would be a ramp
      // nobody ever heard.
      applyGains(t);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, spanMs, liveTracks, rate, playFrom, playTo, applyGains]);

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
      // ⚠ FIRST, and from the gesture that asked for playback: the mixer's
      // context starts suspended until the user has interacted with the page,
      // and a suspended context is silence with a moving playhead.
      resumeAudio();
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
      const frameMs = 1000 / Math.max(1, fps || 24);
      seek(timeRef.current + delta * frameMs);
    },
    [seek, fps]
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
      if (i >= 0) onSelectFrame(frames[i].id);
    },
    [editPoints, seek, starts, frames, onSelectFrame]
  );

  return {
    timeMs, timeRef,
    playing, rate,
    markIn, setMarkIn,
    markOut, setMarkOut,
    seek, playAt, togglePlay, stopPlayback, shuttle,
    stepOneFrame, gotoEditPoint,
  };
}

/**
 * Slave the monitor's <video> elements to the clock.
 *
 * ⚠ A <video> IN THE MONITOR IS A SLAVE, NEVER THE CLOCK.
 *
 * Audio is this editor's master clock (see the playback effect above), and it
 * has to stay that way: a video element's `currentTime` advances on its own
 * decode schedule, stalls while buffering, and rounds to its own frame grid.
 * Driving the timeline from it would make the pictures, the captions and the
 * sound disagree the moment a clip hiccuped. So time only ever flows INTO
 * these elements — the scene model says which source moment should be showing
 * and this pushes the element there.
 *
 * Three states, and they are genuinely different:
 *   scrubbing / paused — pause and seek. Exact, and the only way to land on a
 *       frame while the playhead is being dragged.
 *   playing forward at 1× — let the element play (that is what makes it look
 *       like video rather than a flipbook) and only correct it when it has
 *       drifted more than DRIFT_MS from where the clock says it should be.
 *   shuttling, or holding past the out point — pause and seek, like scrubbing.
 *       Browsers cannot play backwards at all, and a clip past its out point
 *       is showing ONE held frame; letting it run and yanking it back every
 *       frame is what a stutter looks like.
 *
 * Called separately from the transport above, and after it, because it reads
 * the SCENE — and the scene is derived from the clock the transport owns.
 */
export function useMonitorVideo({ scene, frames, videoElsRef, playing, rate }) {
  const videoCues = useMemo(() => {
    const cues = [];
    for (const picture of [scene.frame, scene.frame_b]) {
      if (!picture || picture.kind !== "video") continue;
      const clip = frames[picture.index];
      const uploadId = clip?.src?.upload_id;
      if (!clip || !uploadId) continue;
      const outMs = clip.out_ms ?? null;
      cues.push({
        clipId: clip.id,
        uploadId,
        sourceMs: picture.source_ms || 0,
        speed: clip.speed ?? 1,
        // `source_at` clamps at the out point, so from here on the clip shows
        // one frozen frame — the element must stop rather than run past it.
        holding: outMs !== null && (picture.source_ms || 0) >= outMs - 1,
      });
    }
    return cues;
  }, [scene, frames]);

  useEffect(() => {
    const live = new Set(videoCues.map((c) => c.clipId));
    // Anything no longer on screen stops. An off-screen clip left playing keeps
    // decoding for nothing, and would drift a long way from the clock before it
    // came back on screen.
    for (const [id, el] of Object.entries(videoElsRef.current)) {
      if (el && !live.has(id) && !el.paused) el.pause();
    }
    for (const cue of videoCues) {
      const el = videoElsRef.current[cue.clipId];
      // `duration` is NaN until the metadata has loaded; seeking then throws it
      // away silently, and the next tick will do it properly.
      if (!el || !Number.isFinite(el.duration) || el.duration <= 0) continue;
      const wantS = clamp(cue.sourceMs / 1000, 0, Math.max(0, el.duration - 0.001));
      if (!playing || rate !== 1 || cue.holding) {
        if (!el.paused) el.pause();
        // A small dead band: seeking to where it already is causes a needless
        // decode, and at 60 renders a second that is visible as flicker.
        if (Math.abs(el.currentTime - wantS) > 0.02) el.currentTime = wantS;
        continue;
      }
      // Playing forward. The element runs at the clip's own speed, so the
      // pictures it produces are the ones `source_at` asks for and the drift
      // correction below almost never has to fire.
      el.playbackRate = clamp(cue.speed, 0.0625, 16);
      if (Math.abs(el.currentTime - wantS) * 1000 > DRIFT_MS) el.currentTime = wantS;
      if (el.paused) {
        el.play().catch(() => {
          /* autoplay policy — the still under it is what shows until a click */
        });
      }
    }
  }, [videoCues, playing, rate, videoElsRef]);
}
