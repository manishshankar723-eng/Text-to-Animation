// AudioProperties.jsx — one audio track's place in the mix: its level, where it
// starts in the file, and how much of it plays.
//
// Laid out with the primitives in `PropGroup.jsx`, like every other pane.

import Icon from "../Icon.jsx";
import { EQ_BANDS, EQ_EPSILON, eqGains } from "../../animatic/audio_mix.js";
import { formatTime } from "../Timeline.jsx";
import { PropGroup, PropRow, NumField, PropNote } from "./PropGroup.jsx";

export default function AudioProperties({
  track,
  index,
  // Every track on the project, because two of the controls here are ABOUT the
  // other tracks: which one is the voice, and which voice this one ducks under.
  tracks = [],
  gesture,
  onChange,
  onRemove,
  onCaptions,
  captionsBusy,
}) {
  const volume = track.volume ?? 1;
  const rest = Math.max(0, (track.duration_ms || 0) - (track.offset_ms || 0));
  const playLen = track.trim_ms ? Math.min(track.trim_ms, rest || track.trim_ms) : rest;
  const duckTo = track.duck_to ?? 1;
  const eq = eqGains(track);
  const flat = eq.every((g) => Math.abs(g) < EQ_EPSILON);
  const voices = tracks.filter(
    (t) => t.role === "voice" && t.upload_id !== track.upload_id
  );
  return (
    <div className="an-props">
      <div className="an-prop-ident">
        <div className="an-prop-ident-text">
          <span className="an-prop-kind">Track {index + 1}</span>
          <span className="an-prop-name" title={track.filename}>
            {track.filename}
          </span>
          <span className="an-prop-sub">{formatTime(track.duration_ms || 0)} long</span>
        </div>
      </div>

      <PropGroup id="audio:mix" title="Mix">
        {/* The mute button belongs beside the fader, not on a row of its own —
            they are one control, and every mixer in the world draws them so. */}
        <PropRow label="Volume" title="How loud this track sits in the finished mix">
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
            {...gesture}
            onChange={(e) => onChange(track.upload_id, { volume: Number(e.target.value) })}
          />
          <span className="an-num-read">{Math.round(volume * 100)}%</span>
        </PropRow>
        {/* ⚠ This used to end "above 100% the editor previews at 100%, but the
            export uses the real figure" — an apology for an <audio> element's
            volume being clamped to 1. Playback goes through a gain node now
            (`audio_engine.js`), so the sentence is gone because the limit is. */}
        <PropNote>
          100% is the file as recorded. Pull a music bed down to sit under a
          voice — the tracks are mixed together when the video is exported.
        </PropNote>

        {/* ⚠ STATED, NEVER GUESSED. The duck below needs to know which track is
            the voice, and "the other one" is wrong the first time someone lays
            two music beds — so it is asked for rather than inferred. */}
        <PropRow label="This track is" title="What this track is in the mix — a duck needs to know">
          <select
            className="an-select"
            value={track.role || ""}
            onChange={(e) => onChange(track.upload_id, { role: e.target.value })}
          >
            <option value="">Not said</option>
            <option value="voice">A voice — dialogue or narration</option>
            <option value="music">Music or atmosphere</option>
          </select>
        </PropRow>

        <PropRow
          label="Duck under voice"
          title="How far this track drops while the voice is talking"
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
            onChange={(e) => onChange(track.upload_id, { duck_to: Number(e.target.value) })}
          />
          <span className="an-num-read">
            {duckTo >= 1 ? "off" : `${Math.round(duckTo * 100)}%`}
          </span>
        </PropRow>

        {/* Only when the answer isn't obvious. With one voice on the project
            there is nothing to choose, and a select with one option in it is a
            control that asks a question it already knows the answer to. */}
        {voices.length > 1 && duckTo < 1 && (
          <PropRow label="Ducks under" title="Which voice opens the duck">
            <select
              className="an-select"
              value={track.duck_target || ""}
              onChange={(e) => onChange(track.upload_id, { duck_target: e.target.value })}
            >
              <option value="">The first voice track</option>
              {voices.map((t) => (
                <option key={t.upload_id} value={t.upload_id}>
                  {t.filename || t.upload_id}
                </option>
              ))}
            </select>
          </PropRow>
        )}

        <PropNote>
          {voices.length
            ? "While the voice is talking this track is pulled down to about that level, and comes back up between lines."
            : "Ducking needs a voice: mark the dialogue track as “A voice” and this comes alive."}
          {" The editor previews the duck; the export applies the real compressor, so it is close rather than identical."}
        </PropNote>
      </PropGroup>

      {/* --- Tone ------------------------------------------------------------
          Three fixed bands, because each one is exactly one biquad here and
          exactly one ffmpeg filter in the export — see `EQ_BANDS`. They are
          drawn in `EQ_BANDS` order (low → high), which is the order every EQ
          in the world reads in. */}
      <PropGroup
        id="audio:tone"
        title="Tone"
        actions={
          flat ? null : (
            <button
              type="button"
              className="an-fx-btn"
              title="Set all three bands back to flat"
              onClick={() =>
                onChange(track.upload_id, { eq_low: 0, eq_mid: 0, eq_high: 0 })
              }
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
          >
            <input
              className="an-vol"
              type="range"
              min="-12"
              max="12"
              step="0.5"
              value={eq[i]}
              {...gesture}
              onChange={(e) =>
                onChange(track.upload_id, { [band.field]: Number(e.target.value) })
              }
            />
            <span className="an-num-read">
              {eq[i] > 0 ? "+" : ""}
              {eq[i].toFixed(1)} dB
            </span>
          </PropRow>
        ))}
        <PropNote>
          Low is a shelf at 120 Hz, Mid a bell at 1 kHz, High a shelf at 6 kHz.
          Pull Mid down on a music bed to make room for a voice, or Low down to
          take the rumble off a room recording — what you hear here is the
          filter the export applies.
        </PropNote>
      </PropGroup>

      <PropGroup id="audio:timing" title="Timing">
        <PropRow
          label="Starts at"
          title="How far INTO this file playback starts — use it to skip an intro"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={((track.offset_ms || 0) / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(track.upload_id, {
                offset_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          <span className="an-row-read">of {formatTime(track.duration_ms || 0)}</span>
        </PropRow>

        <PropRow
          label="Plays for"
          title="How much of the track plays — the same as dragging its right edge"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0.1"
            value={(playLen / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(track.upload_id, {
                trim_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
          {track.trim_ms ? (
            <button
              type="button"
              className="btn small ghost an-row-end"
              onClick={() => onChange(track.upload_id, { trim_ms: null })}
              title="Play the whole file from the start point"
            >
              Use whole track
            </button>
          ) : (
            <span className="an-row-read">whole track</span>
          )}
        </PropRow>

        {/* The same two numbers as the grips on the clip's top corners — this
            is where you type one you can't drag accurately at this zoom. */}
        <PropRow label="Fade in" title="How long the track takes to come up to its level">
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={((track.fade_in_ms || 0) / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(track.upload_id, {
                fade_in_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
        <PropRow label="Fade out" title="How long it takes to go silent at the end">
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={((track.fade_out_ms || 0) / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(track.upload_id, {
                fade_out_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
        <PropNote>
          A fade out lands on the end of what this track PLAYS — its trim, or the
          end of the video if that comes first — so trimming the clip carries the
          fade with it.
        </PropNote>
      </PropGroup>

      {/* ⚠ SPENDS QUOTA, and therefore never directly: it opens the priced
          panel, which opens the priced confirmation. The one thing this track
          can be turned into that isn't free. */}
      {onCaptions && (
        <PropGroup id="audio:captions" title="Captions">
          <PropRow full>
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
          <PropNote>
            Costs a fraction of a cent, and you see the price before anything is
            spent. The captions arrive as ordinary text clips.
          </PropNote>
        </PropGroup>
      )}

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
