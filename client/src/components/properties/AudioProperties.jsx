// AudioProperties.jsx — one audio CLIP's place in the mix: its level, where it
// sits on the timeline, where it starts in the file, and how much of it plays.
//
// Laid out with the primitives in `PropGroup.jsx`, like every other pane — one
// property per row, a ⏱ where a property can be animated, and a ↺ on every row
// that has a default to go back to.
//
// ⚠ A CLIP, NOT A FILE. Since the razor learned to cut audio, several entries
// can read the same upload, and this pane describes ONE of them. Every write
// goes out keyed by `clipId(track)`; the only thing still keyed by the upload is
// the captions button, because transcribing is done on the file.

import Icon from "../Icon.jsx";
import {
  EQ_BANDS,
  EQ_EPSILON,
  eqGains,
  fadeCurve,
  FADE_CURVE_INFO,
  FADE_CURVES,
} from "../../animatic/audio_mix.js";
import { clipId } from "../../animatic/audio_clips.js";
import { formatTime } from "../Timeline.jsx";
import { PropGroup, PropRow, NumField } from "./PropGroup.jsx";

export default function AudioProperties({
  track,
  index,
  // Every clip on the project, because two of the controls here are ABOUT the
  // others: which one is the voice, and which voice this one ducks under.
  tracks = [],
  gesture,
  onChange,
  onRemove,
  onCaptions,
  captionsBusy,
  // `{ percent, message }` while the SERVER is writing captions, or null. Shown
  // beside the button that started it as well as in the status bar at the top of
  // the editor — a pass that takes ten seconds with its only sign of life three
  // panes away reads as a button that did nothing.
  captionsProgress,
}) {
  const id = clipId(track);
  const set = (patch) => onChange(id, patch);
  const volume = track.volume ?? 1;
  const startMs = Math.max(0, track.start_ms || 0);
  const rest = Math.max(0, (track.duration_ms || 0) - (track.offset_ms || 0));
  const playLen = track.trim_ms ? Math.min(track.trim_ms, rest || track.trim_ms) : rest;
  const duckTo = track.duck_to ?? 1;
  const eq = eqGains(track);
  const flat = eq.every((g) => Math.abs(g) < EQ_EPSILON);
  const voices = tracks.filter((t) => t.role === "voice" && clipId(t) !== id);
  // How many pieces this file has been cut into, and which one this is — the
  // only way to tell two halves of one take apart in a pane that would
  // otherwise show the same filename for both.
  const siblings = tracks.filter((t) => t.upload_id === track.upload_id);
  const piece = siblings.findIndex((t) => clipId(t) === id) + 1;

  /**
   * The curve chips for one END of this clip — Premiere's three crossfades,
   * which are three shapes for one ramp.
   *
   * ⚠ ONLY WHERE THERE IS A RAMP TO SHAPE. A curve on a fade of zero does
   * nothing at all, so on the ordinary clip with hard edges these two rows are
   * not drawn — and the moment you drag a fade grip out, the row for that end
   * appears under the number it belongs to. Every other row in this pane is
   * always present because every other row always means something.
   *
   * ⚠ ONE ROW PER END, not one for the clip. A crossfade writes one end of one
   * clip and the opposite end of its neighbour, so the two ends genuinely differ
   * — and a single control would quietly rewrite the crossfade at the other end
   * of the clip every time you shaped this one.
   */
  function curveRow(side) {
    const ms = side === "in" ? track.fade_in_ms || 0 : track.fade_out_ms || 0;
    if (ms <= 0) return null;
    const field = side === "in" ? "fade_in_curve" : "fade_out_curve";
    const current = fadeCurve(track, side);
    return (
      <PropRow
        label={side === "in" ? "In shape" : "Out shape"}
        title="The curve the ramp follows"
        reset={() => set({ [field]: "linear" })}
        changed={current !== "linear"}
        resetTo={FADE_CURVE_INFO.linear.label}
        info="Constant Gain is a straight line; two of them crossing scoop about 3 dB out of the middle of the crossfade. Constant Power crosses at equal power instead, which is why it holds its level — it is the one to reach for on a cut between two pieces of music. Exponential Fade holds on and drops away late, for a long tail at the end of a track. These are the same three curves as the Crossfade folder in the Effects tab, and dropping one there sets this."
      >
        <span className="an-set-chips">
          {FADE_CURVES.map((curve) => (
            <button
              key={curve}
              type="button"
              /* The FOLDED curve, not the stored one, so the chip that lights up
                 is the shape the monitor is actually playing. */
              className={`opt-chip ${current === curve ? "active" : ""}`}
              onClick={() => set({ [field]: curve })}
            >
              {/* ⚠ THE NAME ONLY. The three notes live behind this row's ⓘ,
                  where the difference between them can be put in one paragraph
                  rather than three fragments that each need the other two to
                  make sense — and three chips carrying a sentence each made one
                  control taller than most of the pane it sits in. The note text
                  is still in `FADE_CURVE_INFO`, where the library reads it. */}
              {FADE_CURVE_INFO[curve].label}
            </button>
          ))}
        </span>
      </PropRow>
    );
  }

  return (
    <div className="an-props">
      <div className="an-prop-ident">
        <div className="an-prop-ident-text">
          <span className="an-prop-kind">
            {siblings.length > 1 ? `Audio clip ${piece} of ${siblings.length}` : `Track ${index + 1}`}
          </span>
          <span className="an-prop-name" title={track.filename}>
            {track.filename}
          </span>
          <span className="an-prop-sub">
            {formatTime(track.duration_ms || 0)} long
            {siblings.length > 1 ? " · one file, cut into pieces" : ""}
          </span>
        </div>
      </div>

      <PropGroup id="audio:mix" title="Mix">
        {/* The mute button belongs beside the fader, not on a row of its own —
            they are one control, and every mixer in the world draws them so. */}
        <PropRow
          label="Volume"
          title="How loud this track sits in the finished mix"
          reset={() => set({ volume: 1, muted: false })}
          changed={Math.abs(volume - 1) > 1e-6 || Boolean(track.muted)}
          resetTo="100%, unmuted"
          /* ⚠ This used to end "above 100% the editor previews at 100%, but the
             export uses the real figure" — an apology for an <audio> element's
             volume being clamped to 1. Playback goes through a gain node now
             (`audio_engine.js`), so the sentence is gone because the limit is. */
          info="100% is the file as recorded. Pull a music bed down to sit under a voice — the tracks are mixed together when the video is exported."
        >
          <button
            type="button"
            className={`an-mute ${track.muted ? "on" : ""}`}
            title={track.muted ? "Unmute this track" : "Mute this track"}
            onClick={() => set({ muted: !track.muted })}
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
            {...gesture}
            onChange={(e) => set({ volume: Number(e.target.value) })}
          />
          <span className="an-num-read">{Math.round(volume * 100)}%</span>
        </PropRow>

        {/* ⚠ STATED, NEVER GUESSED. The duck below needs to know which track is
            the voice, and "the other one" is wrong the first time someone lays
            two music beds — so it is asked for rather than inferred. */}
        <PropRow
          label="This track is"
          title="What this track is in the mix — a duck needs to know"
          reset={() => set({ role: "" })}
          changed={Boolean(track.role)}
          resetTo="not said"
        >
          <select
            className="an-select"
            value={track.role || ""}
            onChange={(e) => set({ role: e.target.value })}
          >
            <option value="">Not said</option>
            <option value="voice">A voice — dialogue or narration</option>
            <option value="music">Music or atmosphere</option>
          </select>
        </PropRow>

        <PropRow
          label="Duck under voice"
          title="How far this track drops while the voice is talking"
          reset={() => set({ duck_to: 1, duck_target: "" })}
          changed={duckTo < 1 || Boolean(track.duck_target)}
          resetTo="off"
          /* ⚠ HERE, and not on "Ducks under" below — that row only exists when
             the project has two voices, and this is needed most when it has
             none. */
          info={
            (voices.length
              ? "While the voice is talking this track is pulled down to about that level, and comes back up between lines."
              : "Ducking needs a voice: mark the dialogue track as “A voice” and this comes alive.") +
            " The editor previews the duck; the export applies the real compressor, so it is close rather than identical."
          }
        >
          <input
            className="an-vol"
            type="range"
            min="0.1"
            max="1"
            step="0.05"
            value={duckTo}
            disabled={!voices.length}
            {...gesture}
            onChange={(e) => set({ duck_to: Number(e.target.value) })}
          />
          <span className="an-num-read">
            {duckTo >= 1 ? "off" : `${Math.round(duckTo * 100)}%`}
          </span>
        </PropRow>

        {/* Only when the answer isn't obvious. With one voice on the project
            there is nothing to choose, and a select with one option in it is a
            control that asks a question it already knows the answer to. */}
        {voices.length > 1 && duckTo < 1 && (
          <PropRow
            label="Ducks under"
            title="Which voice opens the duck"
            reset={() => set({ duck_target: "" })}
            changed={Boolean(track.duck_target)}
            resetTo="the first voice track"
          >
            <select
              className="an-select"
              value={track.duck_target || ""}
              onChange={(e) => set({ duck_target: e.target.value })}
            >
              <option value="">The first voice track</option>
              {voices.map((t) => (
                <option key={clipId(t)} value={clipId(t)}>
                  {t.filename || clipId(t)}
                </option>
              ))}
            </select>
          </PropRow>
        )}
      </PropGroup>

      {/* --- Tone ------------------------------------------------------------
          Three fixed bands, because each one is exactly one biquad here and
          exactly one ffmpeg filter in the export — see `EQ_BANDS`. They are
          drawn in `EQ_BANDS` order (low → high), which is the order every EQ
          in the world reads in. */}
      <PropGroup
        id="audio:tone"
        title="Tone"
        /* On the section, not a row: it explains all three bands at once, and
           there is no one band it is more about than the others. */
        info="Low is a shelf at 120 Hz, Mid a bell at 1 kHz, High a shelf at 6 kHz. Pull Mid down on a music bed to make room for a voice, or Low down to take the rumble off a room recording — what you hear here is the filter the export applies."
        actions={
          flat ? null : (
            <button
              type="button"
              className="an-fx-btn"
              title="Set all three bands back to flat"
              onClick={() => set({ eq_low: 0, eq_mid: 0, eq_high: 0 })}
            >
              Flat
            </button>
          )
        }
      >
        {EQ_BANDS.map((band, i) => (
          <PropRow
            key={band.id}
            label={band.label}
            title={`${band.hz} Hz — the same filter the export applies`}
            reset={() => set({ [band.field]: 0 })}
            changed={Math.abs(eq[i]) >= EQ_EPSILON}
            resetTo="flat"
          >
            <input
              className="an-vol"
              type="range"
              min="-12"
              max="12"
              step="0.5"
              value={eq[i]}
              {...gesture}
              onChange={(e) => set({ [band.field]: Number(e.target.value) })}
            />
            <span className="an-num-read">
              {eq[i] > 0 ? "+" : ""}
              {eq[i].toFixed(1)} dB
            </span>
          </PropRow>
        ))}
      </PropGroup>

      <PropGroup id="audio:timing" title="Timing">
        {/* ⚠ THE ROW THE RAZOR NEEDED. A track used to be pinned to the head of
            the video, so the only edits were its two ends — which is exactly
            why a pause in the middle of a take could not be taken out. This is
            where the clip SITS; "Starts at" below is where it READS. */}
        <PropRow
          label="Sits at"
          title="Where on the TIMELINE this clip begins — the same as dragging it sideways"
          reset={() => set({ start_ms: 0 })}
          changed={startMs > 0}
          resetTo="the start of the video"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={(startMs / 1000).toFixed(1)}
            onChange={(e) =>
              set({ start_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)) })
            }
          />
          <span className="an-row-read">plays to {formatTime(startMs + playLen)}</span>
        </PropRow>

        <PropRow
          label="Starts at"
          title="How far INTO this file playback starts — use it to skip an intro"
          reset={() => set({ offset_ms: 0 })}
          changed={(track.offset_ms || 0) > 0}
          resetTo="the start of the file"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={((track.offset_ms || 0) / 1000).toFixed(1)}
            onChange={(e) =>
              set({
                offset_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          <span className="an-row-read">of {formatTime(track.duration_ms || 0)}</span>
        </PropRow>

        <PropRow
          label="Plays for"
          title="How much of the track plays — the same as dragging its right edge"
          reset={() => set({ trim_ms: null })}
          changed={Boolean(track.trim_ms)}
          resetTo="the whole file"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0.1"
            value={(playLen / 1000).toFixed(1)}
            onChange={(e) =>
              set({
                trim_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          {!track.trim_ms && <span className="an-row-read">whole track</span>}
        </PropRow>

        {/* The same two numbers as the grips on the clip's top corners — this
            is where you type one you can't drag accurately at this zoom. */}
        <PropRow
          label="Fade in"
          title="How long the track takes to come up to its level"
          reset={() => set({ fade_in_ms: 0 })}
          changed={(track.fade_in_ms || 0) > 0}
          resetTo="a hard start"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={((track.fade_in_ms || 0) / 1000).toFixed(1)}
            onChange={(e) =>
              set({
                fade_in_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
        {curveRow("in")}
        <PropRow
          label="Fade out"
          title="How long it takes to go silent at the end"
          reset={() => set({ fade_out_ms: 0 })}
          changed={(track.fade_out_ms || 0) > 0}
          resetTo="a hard stop"
          info="A fade out lands on the end of what this clip PLAYS — its trim, or the end of the video if that comes first — so trimming the clip carries the fade with it. To cut a gap out of the middle, take the razor (C) to the waveform on each side of it and delete the piece between."
        >
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={((track.fade_out_ms || 0) / 1000).toFixed(1)}
            onChange={(e) =>
              set({
                fade_out_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
        {curveRow("out")}
      </PropGroup>

      {/* ⚠ SPENDS QUOTA, and therefore never directly: it opens the priced
          panel, which opens the priced confirmation. The one thing this track
          can be turned into that isn't free.
          ⚠ Keyed by the UPLOAD, not the clip — transcribing listens to the
          whole FILE, so cutting it up neither changes nor multiplies the job. */}
      {onCaptions && (
        <PropGroup id="audio:captions" title="Captions">
          <PropRow
            full
            /* Beside the button that spends the money, which is where anyone
               about to press it is looking. */
            info="Costs a fraction of a cent, and you see the price before anything is spent. The captions arrive as ordinary text clips on their own Captions row at the top of the timeline — your own text is never touched — and they follow this track's CUTS: a caption is written where the words are actually heard, and nothing is written for audio you have cut out."
          >
            <button
              type="button"
              className="btn small"
              disabled={captionsBusy}
              onClick={() => onCaptions(track.upload_id)}
              title="Listen to this track and write a caption for each spoken line"
            >
              <Icon name="text" /> Write captions from this track
            </button>
          </PropRow>
          {/* ⚠ WHERE THE BUTTON IS, because that is where the user is looking.
              The status bar at the top of the editor has always reported this,
              but it is three panes away from the thing just clicked, so the
              pass read as "nothing happened, then captions appeared". The
              percentage is a STAGE, not a measurement — transcription is one
              model call that cannot be asked how far through it is. */}
          {captionsBusy && captionsProgress && (
            <PropRow full>
              <span className="an-prop-progress">
                <span className="spinner-inline" />
                <span className="an-prop-progress-msg">
                  {captionsProgress.message || "Working…"}
                </span>
                <span className="an-status-bar">
                  <span style={{ width: `${captionsProgress.percent ?? 0}%` }} />
                </span>
              </span>
            </PropRow>
          )}
        </PropGroup>
      )}

      <div className="an-prop-actions">
        <button type="button" className="btn small danger-btn" onClick={() => onRemove(id)}>
          <Icon name="close" />{" "}
          {siblings.length > 1 ? "Remove this clip" : "Remove track"}
        </button>
      </div>
    </div>
  );
}
