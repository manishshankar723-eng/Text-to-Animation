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
import {
  clockRead,
  duckEnvelope,
  envelopeAt,
  eqGains,
  fadeGainAt,
  trackWindow,
} from "./audio_mix.js";
import { clipId } from "./audio_clips.js";
import { applyTrackAudio, resumeAudio } from "./audio_engine.js";

// Shuttle speeds for J / L, in the order repeated presses step through them.
const SHUTTLE = [1, 2, 4];
// How far a <video> may drift from the clock before it is pulled back.
const DRIFT_MS = 120;
// How far an <audio> clip that ISN'T the master clock may drift before it is
// pulled back. Generous on purpose: correcting an element that is only a few
// frames out costs a re-decode you can hear, and nothing here is sample-locked
// anyway. Only ever applied to a follower — see `syncTracks`.
const AUDIO_DRIFT_MS = 200;

// The master clock's own guard lives in `audio_mix.js` beside the mix it feeds
// — see `clockRead` and `CLOCK_STALL_MS` there for the bug it exists for.

// Put one element at the given video time.
//
// ⚠ TWO SHIFTS, and they pull in opposite directions. `start_ms` is where the
// clip sits on the TIMELINE, so the file is that much BEHIND the playhead;
// `offset_ms` is how far into the FILE it starts reading, so the file is that
// much AHEAD. Both, or a clip cut out of the middle of a take plays the wrong
// sound at the wrong moment.
function placeTrack(el, track, videoMs) {
  const at = (videoMs - (track.start_ms || 0) + (track.offset_ms || 0)) / 1000;
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

  // Every audio CLIP's <audio>, paired with its project entry. Elements that
  // haven't mounted yet (a blob still loading) are simply absent.
  //
  // ⚠ Keyed by the CLIP, not by the upload. Cutting one file into two clips
  // gives two elements playing two different windows of the same blob, and
  // keying on the upload would give them one element to fight over.
  const liveTracks = useCallback(
    () =>
      audioTracks
        .map((track) => ({ track, el: audioElsRef.current[clipId(track)] }))
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
    .map(
      (t) =>
        `${clipId(t)}:${t.role || ""}:${t.duck_to ?? 1}:${t.duck_target || ""}` +
        `:${t.offset_ms || 0}:${t.start_ms || 0}`
    )
    .join("|");
  const duckEnvs = useMemo(() => {
    const out = {};
    const voices = audioTracks.filter((t) => t.role === "voice");
    const voiceOf = (track) =>
      audioTracks.find((t) => clipId(t) && clipId(t) === track.duck_target) || voices[0];
    const sources = new Set();
    for (const track of audioTracks) {
      if ((track.duck_to ?? 1) >= 1) continue;
      const voice = voiceOf(track);
      if (voice && clipId(voice) !== clipId(track)) sources.add(clipId(voice));
    }
    for (const track of audioTracks) {
      const to = track.duck_to ?? 1;
      if (to >= 1 || sources.has(clipId(track))) continue;
      const voice = voiceOf(track);
      if (!voice || clipId(voice) === clipId(track)) continue;
      // The ANALYSIS is of the file, so it is still fetched by upload id — two
      // clips cut from one voiceover are two windows of one decoded envelope.
      const analysis = audioAnalyses[voice.upload_id];
      if (!analysis) continue;
      out[clipId(track)] = {
        env: duckEnvelope(analysis.envelope, analysis.hopMs, to),
        hopMs: analysis.hopMs,
        // The envelope is in FILE time. Video time reaches it by the same two
        // shifts `placeTrack` makes: back by where the VOICE clip sits, forward
        // by how far into its file it reads.
        offsetMs: (voice.offset_ms || 0) - (voice.start_ms || 0),
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
      // ⚠ TWO DIFFERENT LENGTHS, and swapping them is a real bug. WHETHER the
      // clip is audible is measured against the SPAN — the timeline reaches past
      // the pictures precisely so you can scrub into the rest of a long track,
      // and gating on the export length would make that stretch silent. WHERE
      // its fade out lands is measured against the EXPORT, which is the existing
      // rule: a ramp is placed on the end of the video when that comes first,
      // because a fade nobody hears is not a fade.
      const { startMs, endMs } = trackWindow(track, spanMs);
      // ⚠ SILENT OUTSIDE ITS OWN WINDOW, and this is not belt-and-braces: the
      // element is paused there, but pausing is a decision made once per
      // boundary crossing while this runs every frame. Without it a clip whose
      // element the browser refused to pause (or that is mid-seek) would be
      // heard where the timeline shows nothing.
      if (videoMs < startMs || videoMs >= endMs) return 0;
      let gain = Math.max(0, track.volume ?? 1);
      // The fades belong to the CLIP, so they are measured from where it starts
      // — that is what carries a fade along when a clip is dragged.
      gain *= fadeGainAt(track, videoMs - startMs, exportMs || spanMs);
      const duck = duckEnvs[clipId(track)];
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

  // --- Which clips are playing right now ----------------------------------
  // ⚠ THE PART THE RAZOR MADE NECESSARY. A track used to start at the head of
  // the video and run to its end, so "start playback" meant `play()` on every
  // element once and nothing had to be scheduled. A CLIP occupies a stretch of
  // the timeline, several can share one file, and the playhead crosses in and
  // out of them — so which elements should be running is a question with a
  // different answer every frame, and this is where it is answered.
  //
  // `playing` and `rate` are read from refs rather than from state because
  // `playAt` has to schedule from inside the same call that starts playback,
  // before React has re-rendered with the new values.
  const playingRef = useRef(false);
  const rateRef = useRef(1);

  // What each element's clock last read, and when. A WeakMap for the same reason
  // `appliedRef` is one: an element that leaves the page must not be held here.
  const clockSeenRef = useRef(new WeakMap());

  /**
   * IS THIS ELEMENT'S CLOCK ACTUALLY RUNNING? — the guard between "unpaused"
   * and "playing", which are not the same thing. See `CLOCK_STALL_MS`.
   *
   * Two ways to fail: it has decoded nothing yet (`readyState` below
   * HAVE_CURRENT_DATA), or it has been sitting on one timestamp for longer than
   * a stall is worth waiting for. Either way it is not something to tell the
   * time by.
   */
  const advancing = useCallback((el, now) => {
    const { usable, seen } = clockRead(el, clockSeenRef.current.get(el), now);
    if (seen) clockSeenRef.current.set(el, seen);
    return usable;
  }, []);

  const syncTracks = useCallback(
    (videoMs) => {
      // The SPAN, for the same reason `gainAt` uses it: which clips are running
      // is a question about the timeline you are scrubbing, not about how long
      // the encode will be.
      const running = playingRef.current && rateRef.current === 1;
      let master = null;
      for (const { track, el } of liveTracks()) {
        const { startMs, endMs } = trackWindow(track, spanMs);
        if (videoMs < startMs || videoMs >= endMs) {
          // Off this clip. Paused rather than left running at zero gain: an
          // element playing silently past its own out point is still decoding,
          // and it would be a long way from the clock by the time the playhead
          // came back to it.
          if (!el.paused) el.pause();
          continue;
        }
        if (!running) {
          // Scrubbing, paused, or shuttling — pause and seek exactly, which is
          // the same rule the monitor's <video> elements follow.
          if (!el.paused) el.pause();
          placeTrack(el, track, videoMs);
          continue;
        }
        if (el.paused) {
          // The playhead has just reached this clip. Place it first: `play()`
          // starts from wherever the element happens to be sitting.
          placeTrack(el, track, videoMs);
          el.playbackRate = 1;
          el.play().catch(() => {
            /* autoplay policy — the wall clock still drives the pictures */
          });
          master = master || el;
          continue;
        }
        // ⚠ THE FIRST ONE PLAYING IS THE MASTER CLOCK AND IS NEVER CORRECTED —
        // the tick below reads the time OFF it, so pulling it back to where it
        // says we are is a loop that fights itself. Every other clip is a
        // follower and is nudged only when it has drifted audibly.
        if (!master) {
          master = el;
          continue;
        }
        const want = videoMs - startMs + (track.offset_ms || 0);
        if (Math.abs(el.currentTime * 1000 - want) > AUDIO_DRIFT_MS) {
          placeTrack(el, track, videoMs);
        }
      }
    },
    [liveTracks, spanMs]
  );

  const seek = useCallback(
    (ms) => {
      const t = Math.max(0, Math.min(spanMs, Math.round(ms)));
      timeRef.current = t;
      setTimeMs(t);
      syncTracks(t);
      applyGains(t);
    },
    [spanMs, syncTracks, applyGains]
  );

  // Keep the elements' own level and mute in step with the project.
  useEffect(() => {
    for (const { track, el } of liveTracks()) el.muted = Boolean(track.muted);
    // …and in step with where the clips are. Dragging one along the timeline,
    // or cutting one in two, changes which elements should be running without
    // the playhead moving at all.
    syncTracks(timeRef.current);
    applyGains(timeRef.current);
  }, [audioTracks, audioUrls, liveTracks, syncTracks, applyGains]);

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
      // ⚠ "GENUINELY PLAYING" MEANS ITS CLOCK IS MOVING, not merely that nobody
      // paused it. An element still loading is unpaused and stuck at 0, and
      // taking the time off one froze the playhead where it stood. See
      // `CLOCK_STALL_MS`.
      const master =
        rate === 1
          ? liveTracks().find(
              ({ el }) =>
                !el.paused &&
                !el.ended &&
                !Number.isNaN(el.currentTime) &&
                advancing(el, now)
            )
          : null;
      let t;
      if (master) {
        // The inverse of `placeTrack`: back out of file time by the same two
        // shifts that got us into it.
        t =
          master.el.currentTime * 1000 -
          (master.track.offset_ms || 0) +
          (master.track.start_ms || 0);
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
        playingRef.current = false;
        rateRef.current = 1;
        setPlaying(false);
        setRate(1);
        for (const { el } of liveTracks()) el.pause();
        return;
      }
      timeRef.current = t;
      setTimeMs(t);
      // Which clips should be running is a per-moment question too, and for the
      // same reason: the playhead crosses into a clip and out of the one before
      // it while nothing about the project has changed. Done BEFORE the gains,
      // so an element that has just been started is at the right level on its
      // very first frame instead of one frame late.
      syncTracks(t);
      // The fades and the duck are a gain PER MOMENT, so they are applied here
      // rather than in an effect — an effect only runs when the project changes,
      // and a fade that only moved when you edited something would be a ramp
      // nobody ever heard.
      applyGains(t);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, spanMs, liveTracks, rate, playFrom, playTo, applyGains, syncTracks, advancing]);

  const stopPlayback = useCallback(() => {
    for (const { el } of liveTracks()) {
      el.pause();
      el.playbackRate = 1;
    }
    playingRef.current = false;
    rateRef.current = 1;
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
      // ⚠ The refs BEFORE the state, and before anything is started: `syncTracks`
      // reads them, and it is called on the next line — long before React has
      // re-rendered with the new `playing` / `rate`.
      playingRef.current = true;
      rateRef.current = nextRate;
      setRate(nextRate);
      setPlaying(true);
      if (nextRate === 1) {
        // The ordinary case, and the only one with sound: every clip the
        // playhead is standing on starts, and the rest wait their turn.
        syncTracks(timeRef.current);
      } else {
        for (const { track, el } of liveTracks()) {
          const { startMs, endMs } = trackWindow(track, spanMs);
          const inside = timeRef.current >= startMs && timeRef.current < endMs;
          if (nextRate < 0 || !inside) {
            // No audio in reverse: browsers can't do it. The pictures still run.
            // And a clip the playhead isn't standing on stays silent — shuttling
            // does not re-schedule (see below), so starting one here would leave
            // it running long after the playhead had left it.
            el.pause();
            continue;
          }
          // Shuttling forward. Scheduling is skipped on purpose — at 2× or 4×
          // the playhead crosses clip boundaries faster than an element can be
          // started, so only the clips already under it are shuttled and the
          // rest stay quiet.
          placeTrack(el, track, timeRef.current);
          // Browsers accept roughly 0.06–16x; our shuttle only goes to 4.
          el.playbackRate = nextRate;
          el.play().catch(() => {
            /* autoplay policy — the wall clock still drives the pictures */
          });
        }
      }
    },
    [frames.length, liveTracks, seek, syncTracks, playFrom, playTo, spanMs]
  );

  // The refs are written by hand wherever playback starts or stops, because
  // `syncTracks` needs them before React has re-rendered. This is the belt to
  // that braces: whatever the state ends up saying, the refs agree with it.
  useEffect(() => {
    playingRef.current = playing;
    rateRef.current = rate;
  }, [playing, rate]);

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
  /**
   * THE CLIP A RESOLVED PICTURE POINTS AT — by ID, never by `.index`.
   *
   * ⚠ TWIN OF THE SAME MAP IN `ProgramCanvas.jsx`, and it is here for the same
   * reason it is there. `picture.index` is a position in the array `sceneAt` was
   * given, and the editor gives it the HIDDEN-LANE-FILTERED one (`shown.frames`);
   * `frames` here is the FULL, unfiltered project. The two agree only while
   * nothing is hidden — switch a picture row off and every index after it shifts
   * down, so `frames[picture.index]` names the WRONG CLIP.
   *
   * ⚠ AND THE WRONG CLIP IS SILENT HERE, which is why it survived the round of
   * fixes that caught the same fault in the monitor and in `currentIndex`: the
   * cue then carries some other clip's id, `videoElsRef` has no element under it,
   * the loop below skips, and the <video> that IS on screen is simply never told
   * to play. It sits on the frame it was parked at for the whole clip while the
   * monitor keeps re-uploading that one still — reported as "video ka sirf ek
   * thumbnail jaisa dikhta hai … pura clip mein ek image jaisa", on a project
   * with six of its eight picture rows switched off. Nothing threw, nothing
   * logged, and the images and the sound on the same timeline played perfectly.
   */
  const framesById = useMemo(() => {
    const map = new Map();
    for (const f of frames || []) map.set(f.id, f);
    return map;
  }, [frames]);

  const videoCues = useMemo(() => {
    const cues = [];
    // ⚠ EVERY PICTURE TRACK, and both sides of each track's transition. Reading
    // `scene.frame` / `scene.frame_b` alone would cue only the TOPMOST track, so a
    // video clip on a track underneath another would never be told to play — it
    // would sit on one frozen frame while the monitor claimed to be playing it.
    const showing = [];
    for (const layer of scene.pictures || []) {
      showing.push(layer.frame, layer.frame_b);
    }
    for (const picture of showing) {
      if (!picture || picture.kind !== "video") continue;
      // ⚠ BY ID. See `framesById` above — `picture.index` indexes the filtered
      // array and this one is the whole project.
      const clip = framesById.get(picture.id);
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
  }, [scene, framesById]);

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
