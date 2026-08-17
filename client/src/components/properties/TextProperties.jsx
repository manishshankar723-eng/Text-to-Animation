// TextProperties.jsx — a caption's settings: what it says, when, where, in what
// face, and how it is kept readable over the art.
//
// Laid out with the primitives in `PropGroup.jsx`. The animatable rows sit in
// `ANIMATABLE.text` order (opacity, x, y) so the pane and the timeline's diamond
// rows read as the same list — see that file's header.

import Icon from "../Icon.jsx";
import KeyframeControls from "../KeyframeControls.jsx";
import { FONTS } from "../../animatic/fonts.js";
import { TEXT_PRESETS, applyTextPreset } from "../../animatic/text_presets.js";
import { PropGroup, PropRow, NumField, PropSlider, PropNote } from "./PropGroup.jsx";

const TEXT_PLACES = [
  { id: "flow", label: "In a zone" },
  { id: "free", label: "Anywhere" },
];
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

/**
 * `clip` is the caption RESOLVED AT THE PLAYHEAD — a keyframed opacity reads as
 * what you can see rather than as what it was stored as. `stored` is the raw
 * clip, and the preset buttons need that one: a preset measures its movement
 * from the caption's RESTING position, and reading a mid-animation `y` off the
 * resolved clip would walk the caption up the frame a little more every time
 * one was applied. Same pair, for the same reason, as `EffectsPanel`'s.
 */
export default function TextProperties({
  clip,
  stored,
  totalMs,
  textAreaRef,
  kf,
  gesture,
  onChange,
  onDuplicate,
  onDelete,
  onClose,
}) {
  const overruns = clip.start_ms + clip.duration_ms > totalMs;
  const free = (clip.place || "flow") === "free";

  return (
    <div className="an-props">
      <PropGroup id="text:content" title="Text">
        <textarea
          ref={textAreaRef}
          className="an-tp-text"
          rows={3}
          value={clip.text}
          placeholder="Type the caption — press Enter for a second line"
          onChange={(e) => onChange(clip.id, { text: e.target.value })}
        />
      </PropGroup>

      <PropGroup id="text:timing" title="Timing">
        <PropRow label="Starts at" title="How far into the video this appears">
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={(clip.start_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(clip.id, {
                start_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
        <PropRow label="Stays for" title="How long it is on screen">
          <NumField
            unit="s"
            step="0.1"
            min="0.1"
            value={(clip.duration_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(clip.id, {
                duration_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
        {overruns && (
          <PropNote tone="warn">
            This runs past the end of the video, so part of it is never seen.
          </PropNote>
        )}
      </PropGroup>

      {/* --- Where it sits, and how visible ---------------------------------
          Two ways to place it, and the first is the one every caption written
          before this uses: dropped into a zone, stacking with anything else in
          that zone so two subtitles never land on each other. "Anywhere" swaps
          that for x/y, which are keyframable — and are what the in/out presets
          animate.
          ⚠ ROWS FOLLOW `ANIMATABLE.text` — opacity, x, y. */}
      <PropGroup
        id="text:motion"
        title="Motion"
        hint="Press ⏱, move the playhead, change the value"
      >
        {/* Fades the whole caption — backdrop, ink and outline together. With
            the ⏱ on, this is how a caption ARRIVES rather than appearing. */}
        <PropSlider
          label="Opacity"
          min="0"
          max="1"
          step="0.05"
          value={clip.opacity ?? 1}
          readout={`${Math.round((clip.opacity ?? 1) * 100)}%`}
          kf={kf && <KeyframeControls {...kf} prop="opacity" />}
          {...gesture}
          onChange={(e) => onChange(clip.id, { opacity: parseFloat(e.target.value) })}
        />

        <PropRow label="Placement" title="Whether this caption flows in a zone or is placed freely">
          <span className="an-tp-group">
            {TEXT_PLACES.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`an-tp-btn ${(clip.place || "flow") === p.id ? "on" : ""}`}
                title={
                  p.id === "flow"
                    ? "Sits in a zone and stacks with other captions there"
                    : "Sits exactly where you put it, and can be animated"
                }
                onClick={() => onChange(clip.id, { place: p.id })}
              >
                {p.label}
              </button>
            ))}
          </span>
        </PropRow>

        {free ? (
          <>
            <PropSlider
              label="Across"
              title="Left to right. 50% is centred."
              min="0"
              max="1"
              step="0.01"
              value={clip.x ?? 0.5}
              readout={`${Math.round((clip.x ?? 0.5) * 100)}%`}
              kf={kf && <KeyframeControls {...kf} prop="x" />}
              {...gesture}
              onChange={(e) => onChange(clip.id, { x: parseFloat(e.target.value) })}
            />
            <PropSlider
              label="Down"
              title="Top to bottom. 85% is the usual subtitle line."
              min="0"
              max="1"
              step="0.01"
              value={clip.y ?? 0.85}
              readout={`${Math.round((clip.y ?? 0.85) * 100)}%`}
              kf={kf && <KeyframeControls {...kf} prop="y" />}
              {...gesture}
              onChange={(e) => onChange(clip.id, { y: parseFloat(e.target.value) })}
            />
          </>
        ) : (
          <PropRow label="Zone" title="Which band of the frame this caption stacks in">
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
          </PropRow>
        )}
      </PropGroup>

      {/* --- The face -------------------------------------------------------
          The list is BUNDLED, not the machine's fonts: the same .ttf file is
          loaded here and by the exporter, so a caption cannot wrap onto three
          lines in the video and two in the monitor. See animatic_fonts.py. */}
      <PropGroup id="text:type" title="Type">
        <PropRow label="Font" title="The typeface — the same file is used for the exported video">
          <select
            className="an-select"
            value={clip.font || "inter"}
            onChange={(e) => onChange(clip.id, { font: e.target.value })}
          >
            {FONTS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </PropRow>
        <PropRow label="Size">
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
        </PropRow>
        <PropRow label="Align">
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
        </PropRow>
        <PropRow label="Colour" title="Text colour">
          <input
            type="color"
            className="an-colour"
            value={clip.color}
            onChange={(e) => onChange(clip.id, { color: e.target.value })}
          />
        </PropRow>
        <PropRow label="Tracking" title="Space between letters, as a fraction of the text size">
          <NumField
            step="0.01"
            min="-0.2"
            max="1"
            value={clip.letter_spacing ?? 0}
            onChange={(e) =>
              onChange(clip.id, {
                letter_spacing: Math.max(-0.2, Math.min(1, parseFloat(e.target.value || 0))),
              })
            }
          />
        </PropRow>
      </PropGroup>

      {/* --- Kept readable over the art -------------------------------------- */}
      <PropGroup id="text:legibility" title="Readability">
        <PropRow label="Backdrop" title="How the text is kept readable over the art">
          <select
            className="an-select"
            value={clip.backdrop}
            onChange={(e) => onChange(clip.id, { backdrop: e.target.value })}
          >
            {TEXT_BACKDROPS.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label}
              </option>
            ))}
          </select>
        </PropRow>
        <PropRow
          label="Outline"
          title="Outline thickness, in pixels at 1080p — it scales with the frame"
        >
          <NumField
            unit="px"
            step="1"
            min="0"
            max="24"
            value={clip.stroke_px ?? 0}
            onChange={(e) =>
              onChange(clip.id, {
                stroke_px: Math.max(0, Math.min(24, parseFloat(e.target.value || 0))),
              })
            }
          />
          <input
            type="color"
            className="an-colour"
            value={clip.stroke_color || "#000000"}
            onChange={(e) => onChange(clip.id, { stroke_color: e.target.value })}
            title="Outline colour"
          />
        </PropRow>
        <PropRow
          label="Shadow"
          title="Drop-shadow offset, as a fraction of the text size. 0 is none."
        >
          <NumField
            step="0.01"
            min="0"
            max="0.5"
            value={clip.shadow ?? 0}
            onChange={(e) =>
              onChange(clip.id, {
                shadow: Math.max(0, Math.min(0.5, parseFloat(e.target.value || 0))),
              })
            }
          />
        </PropRow>
      </PropGroup>

      {/* --- In/out animation ---------------------------------------------
          ⚠ A PRESET IS A KEYFRAME MACRO AND NOTHING ELSE — it writes keys on
          opacity/x/y and gets out of the way, so the timeline shows its
          diamonds, every key can be dragged afterwards, undo treats it as one
          edit, and the exporter needed no changes at all. Nothing records which
          preset was used, which is why none of these is ever shown as "current":
          once you have moved one of its keys, there is no current preset. */}
      <PropGroup id="text:preset" title="In / out animation">
        <PropRow full>
          <span className="an-tp-group an-tp-presets">
            {TEXT_PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                className="an-tp-btn"
                title={p.hint}
                onClick={() => onChange(clip.id, applyTextPreset(stored || clip, p.id))}
              >
                {p.label}
              </button>
            ))}
          </span>
        </PropRow>
        <PropNote>
          These write keyframes you can then drag on the timeline — a preset
          isn't a mode, it's a starting point.
        </PropNote>
      </PropGroup>

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
