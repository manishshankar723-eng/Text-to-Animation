// VideoClipProperties.jsx — the groups a VIDEO CLIP or a COLOUR CARD adds to the
// Properties pane.
//
// Not a whole pane: a clip is still a clip, so `FrameProperties` keeps the parts
// every kind shares (its name, how long it holds, its motion, duplicate and
// remove) and slots this in for the parts only these kinds have. Splitting it
// the other way round would have meant three panes with four identical rows
// each, and they would have drifted apart the first time one of them changed.
//
// ⚠ THE ONE THING TO UNDERSTAND BEFORE EDITING THIS FILE. A video clip has TWO
// lengths and they are not the same number:
//
//   "Duration" (in FrameProperties)  — how much TIMELINE the clip occupies.
//   "In / Out / Speed" (here)        — which part of the SOURCE FILE it reads,
//                                      and how fast it reads it.
//
// Speed does NOT re-time the clip. At 2× the clip covers twice as much footage
// in the same stretch of timeline, so nothing after it moves — no cut shifts, no
// caption comes unstuck, no transition slides off its edit. `sourceAt` in
// `animatic/scene.js` carries the full reasoning; this pane just has to say so
// clearly enough that nobody is surprised by it. That is also why Source and
// Speed are two separate groups rather than one: they answer two questions, and
// running them together is what made people read Speed as a length control.

import Icon from "../Icon.jsx";
import { PropGroup, PropRow, NumField, PropNote } from "./PropGroup.jsx";
import { DEFAULT_CLIP_COLOR as DEFAULT_CARD_COLOR } from "../../animatic/scene.js";
import { clamp } from "../../animatic/util.js";

// The speeds worth one click. Anything else is typed into the box below them.
const SPEED_PRESETS = [0.25, 0.5, 1, 2, 4];
const MIN_SPEED = 0.1;
const MAX_SPEED = 10;

const secs = (ms) => (Math.max(0, ms || 0) / 1000).toFixed(1);

/**
 * The source range and speed of one video clip.
 *
 * `clip` is the STORED clip (not the resolved picture): these are the values
 * being edited, and none of them is animatable, so there is no ⏱ on any row
 * here and no resolved-vs-stored distinction to worry about.
 */
export default function VideoClipProperties({ clip, sourceMs, onChange }) {
  const inMs = clip.in_ms || 0;
  const outMs = clip.out_ms ?? null;
  const speed = clip.speed ?? 1;
  // How much footage this clip will actually get through: its timeline length
  // multiplied by the read speed, stopped at the out point. This is the number
  // that tells you whether you have trimmed too tight, and it is worth showing
  // because you cannot work it out in your head while dragging a speed slider.
  const wanted = (clip.duration_ms || 0) * speed;
  const available = outMs === null ? null : Math.max(0, outMs - inMs);
  const short = available !== null && wanted > available + 1;

  const set = (patch) => onChange(clip.id, patch);

  return (
    <>
      <PropGroup id="clip:source" title="Source" hint="Which part of the file this clip plays">
        <PropRow
          label="In"
          title="How far INTO the file this clip starts"
          reset={() => set({ in_ms: 0 })}
          changed={inMs > 0}
          resetTo="the start of the file"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={secs(inMs)}
            onChange={(e) => {
              const next = Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000));
              // The in point may not pass the out point: an inverted range has
              // no frames in it, and `source_at` would clamp it to a single
              // held frame — a clip that silently stopped being video.
              set({ in_ms: outMs === null ? next : Math.min(next, outMs - 100) });
            }}
          />
        </PropRow>

        {/* ⚠ The old "Use whole clip" button is gone: the ↺ on this row does
            exactly that, and every other row has one now — a second, differently
            worded control for the same thing is what made this pane feel like
            fifteen unrelated forms in the first place. */}
        <PropRow
          label="Out"
          title="Where in the file this clip stops. Empty means the end of the file."
          reset={() => set({ out_ms: null })}
          changed={outMs !== null}
          resetTo="the end of the file"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0.1"
            value={outMs === null ? "" : secs(outMs)}
            placeholder="end"
            onChange={(e) => {
              const raw = parseFloat(e.target.value);
              if (!Number.isFinite(raw)) {
                set({ out_ms: null });
                return;
              }
              set({ out_ms: Math.max(inMs + 100, Math.round(raw * 1000)) });
            }}
          />
        </PropRow>

        <PropRow label="Selected" title="How much footage the in and out points leave">
          <span className="an-row-read">
            {outMs === null ? "the whole file" : `${secs(available)}s`}
          </span>
        </PropRow>

        <PropRow label="Showing" title="Which moment of the file is under the playhead right now">
          {/* The single most useful readout when trimming: it is the number
              `source_at` resolved, so it is also exactly what the export will
              draw. */}
          <span className="an-row-read">
            {sourceMs == null ? "—" : `${secs(sourceMs)}s into the file`}
          </span>
        </PropRow>
      </PropGroup>

      <PropGroup id="clip:speed" title="Speed" hint={`${Math.round(speed * 100)}% of real time`}>
        <PropRow label="Preset" title="The speeds worth one click">
          <span className="an-set-chips">
            {SPEED_PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                className={`opt-chip ${Math.abs(speed - preset) < 1e-6 ? "active" : ""}`}
                onClick={() => set({ speed: preset })}
              >
                {preset}×
              </button>
            ))}
          </span>
        </PropRow>

        <PropRow
          label="Exactly"
          title="Any speed between 0.1× and 10×"
          reset={() => set({ speed: 1 })}
          changed={Math.abs(speed - 1) > 1e-6}
          resetTo="1× (real time)"
          /* Said out loud, because it is the one thing about this group that is
             not what people expect from a speed control. */
          info={
            <>
              Speed changes how much footage plays in this clip, not how long the
              clip is. To make it longer on the timeline, change <b>Duration</b>{" "}
              above — nothing after it will move either way.
            </>
          }
        >
          <NumField
            unit="×"
            step="0.05"
            min={MIN_SPEED}
            max={MAX_SPEED}
            value={speed}
            onChange={(e) =>
              set({ speed: clamp(parseFloat(e.target.value) || 1, MIN_SPEED, MAX_SPEED) })
            }
          />
        </PropRow>

        {/* The failure this group can actually cause: asking for more footage
            than the trim leaves. It is not an error — the clip holds its last
            frame, which is a legitimate freeze — but it is never what someone
            MEANT. */}
        {short && (
          <PropNote tone="warn">
            <Icon name="close" /> This clip wants {secs(wanted)}s of footage but
            only {secs(available)}s is selected, so its last{" "}
            {secs(wanted - available)}s will hold on one frozen frame. Move the
            out point later, lower the speed, or shorten the clip.
          </PropNote>
        )}
      </PropGroup>
    </>
  );
}

/**
 * The one row a COLOUR CARD adds: its colour.
 *
 * A card has no source, no speed and no file — it is a clip whose whole content
 * is a colour, used as a slug, a blackout or a flash. It lives in this file
 * rather than its own because it is the same idea (a clip kind that isn't a
 * still) and it is four lines long.
 */
export function ColorCardProperties({ clip, onChange }) {
  return (
    <PropGroup id="clip:card" title="Card">
      <PropRow
        label="Colour"
        title="What the card is filled with"
        reset={() => onChange(clip.id, { color: DEFAULT_CARD_COLOR })}
        changed={(clip.color || DEFAULT_CARD_COLOR) !== DEFAULT_CARD_COLOR}
        resetTo="black"
      >
        <input
          type="color"
          className="an-colour"
          value={clip.color || DEFAULT_CARD_COLOR}
          onChange={(e) => onChange(clip.id, { color: e.target.value })}
        />
      </PropRow>
    </PropGroup>
  );
}
