// TextProperties.jsx — a caption's settings: what it says, when, where, in what
// face, and how it is kept readable over the art.
//
// Laid out with the primitives in `PropGroup.jsx`. The animatable rows sit in
// `ANIMATABLE.text` order (opacity, x, y) so the pane and the timeline's diamond
// rows read as the same list — see that file's header.

import { useState } from "react";

import Icon from "../Icon.jsx";
import KeyframeControls from "../KeyframeControls.jsx";
import {
  FONTS,
  bestFontForText,
  fontEntry,
  fontGroups,
  missingScripts,
  scriptEntry,
} from "../../animatic/fonts.js";
import {
  TEXT_PRESETS,
  TEXT_PRESET_CATEGORIES,
  applyTextPreset,
} from "../../animatic/text_presets.js";
import {
  TEXT_STYLES,
  TEXT_STYLE_CATEGORIES,
  applyTextStyle,
  deleteCustomStyle,
  listCustomStyles,
  saveCustomStyle,
  styleFromClip,
} from "../../animatic/text_styles.js";
import { backdropHasFill, backdropPatch, textBackdrop } from "../../animatic/scene.js";
import { PropGroup, PropRow, NumField, PropSlider, PropNote } from "./PropGroup.jsx";
import PresetPicker from "./PresetPicker.jsx";

/**
 * How long the beats of the NEXT preset are.
 *
 * ⚠ THE MIDDLE ONE IS THE DEFAULT AND IT IS THE NUMBER THE PRESETS ALREADY USE.
 * `DEFAULT_IN_MS` / `DEFAULT_OUT_MS` in `text_presets.js` are 400 each, so
 * "Normal" passes nothing at all and every preset behaves exactly as it did
 * before this control existed. The other two are the same animation, retimed.
 *
 * ⚠ AND THEY ARE CLAMPED AGAINST THE CLIP'S OWN LENGTH FURTHER DOWN, in
 * `beatsFor` — asking for a 900ms beat on a 400ms caption gives a fast animation
 * rather than keys sitting past the end of the clip.
 */
const BEAT_SPEEDS = [
  { id: "fast", label: "Fast", ms: 200, hint: "Snappy — a reel, a hard cut, a punchline." },
  { id: "normal", label: "Normal", ms: 0, hint: "The house timing. What every preset is drawn at." },
  { id: "slow", label: "Slow", ms: 800, hint: "Unhurried — a title card, a mood, a long hold." },
];

function beatOptions(speedId) {
  const speed = BEAT_SPEEDS.find((s) => s.id === speedId);
  // ⚠ NOTHING AT ALL FOR "Normal", rather than the same number spelled out. The
  // preset's own defaults are then what runs, so there is one place that decides
  // what a beat is worth and this control cannot drift away from it.
  return speed && speed.ms ? { inMs: speed.ms, outMs: speed.ms } : {};
}

/**
 * The tracks a preset owns, and therefore the ones the "Animation" ↺ clears.
 *
 * ⚠ THIS IS `OWNED` IN `text_presets.js`, AND IT HAS TO STAY THAT LIST. A reset
 * that cleared fewer would leave part of an animation running after you asked
 * for it to be taken off — a caption still spinning under a "no animation"
 * readout, which is the reset looking broken rather than the preset.
 */
const PRESET_TRACKS = ["opacity", "x", "y", "scale", "rotation"];

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
// ⚠ THE IDS ARE THE STORED VALUES — twin of `TEXT_BACKDROPS` in scene.js and in
// animatic_render.py. "none" is a misleading id kept because it is what every
// caption ever saved carries: it means "no bar, but an automatic outline". The
// kind that really draws nothing at all is "plain".
const TEXT_BACKDROPS = [
  { id: "scrim", label: "Shaded bar", hint: "A translucent bar behind the text" },
  { id: "box", label: "Solid box", hint: "A nearly solid box behind the text" },
  {
    id: "none",
    label: "Outline only",
    hint: "No bar, but the letters get a dark outline so they stay readable",
  },
  {
    id: "plain",
    label: "Just the letters",
    hint:
      "Nothing at all — no bar, no outline, no shadow. Choosing this clears "
      + "the Outline and Shadow below.",
  },
];
// ⚠ TWIN of `_TEXT_CASES` in animatic.py, and of `CAPTION_TRANSFORM` in
// `AnimaticEditor.jsx` — the ids are what both sides switch on.
const TEXT_CASES = [
  { id: "none", label: "Aa", hint: "Leave the text as typed" },
  { id: "upper", label: "AA", hint: "ALL CAPS" },
  { id: "lower", label: "aa", hint: "all lower case" },
  { id: "title", label: "Ab", hint: "Capitalise Every Word" },
];
// What each backdrop kind is worth when the clip hasn't named a strength — the
// number the opacity slider shows before you have touched it. ⚠ 140/255 and
// 225/255, the two alphas `_draw_text_block` fills with.
const BACKDROP_ALPHA = { scrim: 0.55, box: 0.88 };
// The pixel size each S/M/L preset resolves to at 1080p, for the "exact size"
// row to show as its placeholder. ⚠ `_TEXT_DIVISOR` in animatic.py and the
// `sz-*` rules in animatic-text.css are the same three divisors — 1080/30,
// 1080/21, 1080/14.
const PRESET_PX = { small: 36, medium: 51, large: 77 };

/**
 * What a fresh caption is, field for field — and therefore what every ↺ in this
 * pane goes back to.
 *
 * ⚠ A THIRD COPY of the same table, and it has to stay in step with the other
 * two: the field defaults on `AnimaticTextClip` and `TEXT_DEFAULTS` in
 * `animatic/scene.js`. A reset that lands on a value the clip was never created
 * with is worse than no reset at all, because it looks like it worked.
 */
const CAPTION_DEFAULTS = {
  duration_ms: 2000,
  place: "flow",
  position: "bottom",
  align: "center",
  size: "medium",
  font: "inter",
  color: "#ffffff",
  backdrop: "scrim",
  opacity: 1,
  x: 0.5,
  y: 0.85,
  // ⚠ THE SAME TWO NUMBERS AS `TEXT_DEFAULTS` IN `scene.js` AND
  // `animatic_render.py`, AND AS THE FIELD DEFAULTS ON `AnimaticTextClip`. Four
  // copies now, and the rule is what it has always been for this table: they
  // have to agree, or ↺ puts a caption somewhere it has never been.
  scale: 1,
  rotation: 0,
  letter_spacing: 0,
  stroke_px: 0,
  stroke_color: "#000000",
  shadow: 0,
  size_px: 0,
  line_height: 1.28,
  text_case: "none",
  wrap: 0.86,
  backdrop_color: "#000000",
  // ⚠ null, NOT a number — "whatever the backdrop kind is worth". A default of
  // 0.55 here would make every solid box 55% the moment anything reset it.
  backdrop_opacity: null,
  backdrop_radius: 0.25,
  backdrop_pad: 1,
  shadow_color: "#000000",
  shadow_opacity: 0.55,
  shadow_angle: 45,
};

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
  // How many captions share this clip's row, and the one call that restyles
  // them all. Both come from the editor, which is the only thing that can see
  // the other clips — this pane has never been given the document and should
  // not start now.
  laneCount = 0,
  onRestyleLane,
}) {
  const overruns = clip.start_ms + clip.duration_ms > totalMs;
  const free = (clip.place || "flow") === "free";
  // ⚠ PANE STATE, NOT DOCUMENT STATE, and deliberately not stored on the clip.
  // It is the length of the beats the NEXT preset writes; once a preset has run,
  // the keys ARE the timing and they are what you drag. Storing it would be a
  // second, silent opinion about a clip's animation that nothing renders.
  const [speed, setSpeed] = useState("normal");

  // --- The Look shelf ------------------------------------------------------
  // ⚠ READ ONCE, ON MOUNT, AND KEPT IN STATE. `listCustomStyles` parses and
  // filters a localStorage string; calling it in the render body would re-parse
  // on every keystroke in every field in this pane, and would hand `PresetPicker`
  // a brand-new array each time. Every write below returns the new list, so the
  // state is refreshed from the store rather than guessed at.
  const [customStyles, setCustomStyles] = useState(() => listCustomStyles());
  const [styleName, setStyleName] = useState("");
  const [styleError, setStyleError] = useState("");
  // ⚠ `removable` IS WHAT PUTS THE ✕ ON A ROW, and only the saved ones carry it:
  // a built-in look cannot be deleted, so it must not offer to be.
  const styleShelf = [
    ...TEXT_STYLES,
    ...customStyles.map((s) => ({ ...s, removable: true })),
  ];
  const applyStyle = (styleId) => {
    setStyleError("");
    onChange(clip.id, applyTextStyle(stored || clip, styleId, customStyles));
  };
  const keepStyle = () => {
    const next = saveCustomStyle(styleName, styleFromClip(stored || clip));
    if (!next) {
      // Storage refused — private mode, or site data switched off. Said once,
      // rather than a Save button that silently does nothing for ever.
      setStyleError("This browser would not keep the style. Private mode?");
      return;
    }
    setCustomStyles(next);
    setStyleName("");
    setStyleError("");
  };
  const forgetStyle = (styleId) => {
    const next = deleteCustomStyle(styleId);
    if (next) setCustomStyles(next);
  };
  // ⚠ FOLDED THROUGH THE SHARED HELPERS, not read off the clip. This pane
  // decides which rows EXIST from the backdrop kind, so if it folded an unknown
  // value differently from the renderers it would offer you a fill colour for a
  // caption that draws none. See `textBackdrop` in scene.js.
  const backdrop = textBackdrop(clip);
  const hasFill = backdropHasFill(clip);
  // What the fill opacity row shows when the clip hasn't named one: whatever
  // the KIND is worth. Resolved once, because the slider and its readout must
  // not be able to answer that question differently.
  const fillAlpha = clip.backdrop_opacity ?? BACKDROP_ALPHA[backdrop] ?? 0.55;
  const set = (patch) => onChange(clip.id, patch);
  // Is this field away from what a fresh caption carries? Compared against the
  // RESOLVED clip, which is what the row is showing — the ↺ has to light up for
  // the value you can actually see.
  const off = (field) => (clip[field] ?? CAPTION_DEFAULTS[field]) !== CAPTION_DEFAULTS[field];
  const keyed = (prop) => (clip.keyframes?.[prop] || []).length > 0;
  // The writing systems this caption's own words need that its face has not
  // got, and the face to offer instead. Measured against the .ttf's cmap by
  // `tools/fonts_sync.py`, so this is a fact about the file rather than a guess
  // from its name — see `fonts.js`. Recomputed on every keystroke on purpose:
  // the warning has to arrive while the text is being typed, not after.
  const gaps = missingScripts(clip.font, clip.text || "");
  const suggestion = bestFontForText(clip.text || "", clip.font);
  // A ↺ on an animatable row clears that property's keys as well — see the note
  // in `FrameProperties`. Reading the keyframe map off `stored` rather than off
  // `clip` for the same reason the presets do: `clip` is resolved, and a preset
  // or a reset must work from what is saved.
  const resetProp = (prop) => {
    const keys = { ...((stored || clip).keyframes || {}) };
    delete keys[prop];
    set({ [prop]: CAPTION_DEFAULTS[prop], keyframes: keys });
  };

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
        <PropRow
          label="Starts at"
          title="How far into the video this appears"
          reset={() => set({ start_ms: 0 })}
          changed={clip.start_ms > 0}
          resetTo="the start of the video"
        >
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
        <PropRow
          label="Stays for"
          title="How long it is on screen"
          reset={() => set({ duration_ms: CAPTION_DEFAULTS.duration_ms })}
          changed={off("duration_ms")}
          resetTo={`${CAPTION_DEFAULTS.duration_ms / 1000}s`}
        >
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

      {/* --- The look -------------------------------------------------------
          ⚠ A STYLE IS A BAG OF ORDINARY CAPTION FIELDS AND NOTHING ELSE — the
          same bargain the animation shelves make about keyframes. It writes the
          Type and Readability rows below and gets out of the way, so every one
          of them stays editable afterwards, undo treats it as one edit, and no
          renderer had to learn a single new word. See `text_styles.js`.
          ⚠ AND IT NEVER MOVES THE CAPTION. Position, placement, size on screen
          and every animation track are outside a style on purpose: restyling a
          whole run of subtitles has to change how they look and leave every one
          of them exactly where it is. */}
      <PropGroup
        id="text:style"
        title="Look"
        hint="Ready-made type. Everything it sets is still editable below."
      >
        <PropRow full>
          <PresetPicker
            categories={TEXT_STYLE_CATEGORIES}
            presets={styleShelf}
            onPick={applyStyle}
            onRemove={forgetStyle}
          />
        </PropRow>

        {/* ⚠ THE ROW THAT MAKES THE SHELF WORTH HAVING. A film has one subtitle
            track and forty clips on it; restyling them one at a time is forty
            identical decisions. Offered only when there IS more than one caption
            on this clip's row, so it never appears as a button that does
            nothing, and it is ONE document edit so one Ctrl+Z puts it back. */}
        {onRestyleLane && laneCount > 1 && (
          <PropRow
            label="Whole row"
            title={`Give all ${laneCount} captions on this row the look this one has`}
            info="Only the look travels — every caption keeps its own words, its timing, its place on screen and any animation on it."
          >
            <button
              type="button"
              className="btn small ghost"
              onClick={() => onRestyleLane(clip.id)}
            >
              Match all {laneCount}
            </button>
          </PropRow>
        )}

        {/* ⚠ SAVED PER BROWSER, AND THE LABEL SAYS SO. A caption look is closer
            to a brand asset than to a pane layout and really belongs on the
            account; that is a store, a route and a schema, and not a change to
            make in the same visit as the feature. The honest word on screen is
            worth more than the pretence. */}
        <PropRow
          label="Save look"
          title="Keep this caption's type as a style you can put on another one"
          info="Kept in this browser only — it will not follow you to another machine, and clearing site data forgets it."
        >
          <span className="an-tp-save-style">
            <input
              className="an-prop-input"
              value={styleName}
              placeholder="Name this look"
              maxLength={40}
              onChange={(e) => setStyleName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  keepStyle();
                }
              }}
            />
            <button
              type="button"
              className="btn small ghost"
              disabled={!styleName.trim()}
              onClick={keepStyle}
            >
              Save
            </button>
          </span>
        </PropRow>
        {styleError && <PropNote tone="warn">{styleError}</PropNote>}
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
          reset={() => resetProp("opacity")}
          changed={off("opacity") || keyed("opacity")}
          resetTo="100%"
          {...gesture}
          onChange={(e) => onChange(clip.id, { opacity: parseFloat(e.target.value) })}
        />

        <PropRow
          label="Placement"
          title="Whether this caption flows in a zone or is placed freely"
          reset={() => set({ place: CAPTION_DEFAULTS.place })}
          changed={off("place")}
          resetTo="a zone"
        >
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
              reset={() => resetProp("x")}
              changed={off("x") || keyed("x")}
              resetTo="50%"
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
              reset={() => resetProp("y")}
              changed={off("y") || keyed("y")}
              resetTo="85%"
              {...gesture}
              onChange={(e) => onChange(clip.id, { y: parseFloat(e.target.value) })}
            />
          </>
        ) : (
          <PropRow
            label="Zone"
            title="Which band of the frame this caption stacks in"
            reset={() => set({ position: CAPTION_DEFAULTS.position })}
            changed={off("position")}
            resetTo="the bottom"
          >
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

        {/* --- Zoom and angle ------------------------------------------------
            ⚠ BOTH OF THESE WERE ANIMATABLE WITH NO CONTROL TO SET THEM. `scale`
            has been in `ANIMATABLE.text` since Phase 5 and could only be reached
            through a preset or through the AI editor; `rotation` arrived with
            the preset shelves. A property the renderers honour and the pane does
            not offer is one a person can only get at by accident.
            ⚠ AND THEY SIT OUTSIDE THE `free` BRANCH ON PURPOSE — unlike x/y,
            both work in a ZONE as well, because the browser applies them about
            the caption's zone anchor and `draw_texts` turns the measured block
            about the same point. That is what lets a whole run of stacked
            subtitles pop or spin without one of them leaving its zone. */}
        <PropSlider
          label="Zoom"
          title="The size of the whole caption — glyphs, backdrop and padding together"
          min="0.2"
          max="4"
          step="0.01"
          value={clip.scale ?? 1}
          readout={`${Math.round((clip.scale ?? 1) * 100)}%`}
          kf={kf && <KeyframeControls {...kf} prop="scale" />}
          reset={() => resetProp("scale")}
          changed={off("scale") || keyed("scale")}
          resetTo="100%"
          info="This scales the caption as it was laid out, so the line breaks never move while it grows. It is not the same as the font size, which re-wraps the text."
          {...gesture}
          onChange={(e) => onChange(clip.id, { scale: parseFloat(e.target.value) })}
        />
        <PropSlider
          label="Angle"
          title="Degrees clockwise. It turns about wherever the caption is anchored."
          min="-180"
          max="180"
          step="1"
          value={clip.rotation ?? 0}
          readout={`${Math.round(clip.rotation ?? 0)}°`}
          kf={kf && <KeyframeControls {...kf} prop="rotation" />}
          reset={() => resetProp("rotation")}
          changed={off("rotation") || keyed("rotation")}
          resetTo="0°"
          {...gesture}
          onChange={(e) => onChange(clip.id, { rotation: parseFloat(e.target.value) })}
        />
      </PropGroup>

      {/* --- The face -------------------------------------------------------
          The list is BUNDLED, not the machine's fonts: the same .ttf file is
          loaded here and by the exporter, so a caption cannot wrap onto three
          lines in the video and two in the monitor. See animatic_fonts.py. */}
      <PropGroup id="text:type" title="Type">
        <PropRow
          label="Font"
          title="The typeface — the same file is used for the exported video"
          reset={() => set({ font: CAPTION_DEFAULTS.font })}
          changed={off("font")}
          resetTo={FONTS.find((f) => f.id === CAPTION_DEFAULTS.font)?.label || "the default"}
        >
          {/* ⚠ GROUPED BY WRITING SYSTEM, and derived — `fontGroups()` builds
              the shelves off the font list itself, so a face added to
              `fonts.js` appears here without this file being touched. Fifty-six
              faces in one flat list is not something anyone can read, and the
              shelf label is also the answer to "which of these does my
              language": it is written in the language. */}
          <select
            className="an-select"
            value={clip.font || "inter"}
            onChange={(e) => onChange(clip.id, { font: e.target.value })}
          >
            {fontGroups().map((group) => (
              <optgroup key={group.id} label={group.note ? `${group.label} — ${group.note}` : group.label}>
                {group.fonts.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </PropRow>
        {/* ⚠ THE ONE FAILURE A PICKER CANNOT SHOW BY ITSELF. Every face in the
            list draws SOMETHING, so a Hindi title set in Anton looks like a
            valid choice right up until the render, where it comes out ▯▯▯ —
            and the customer has paid for that render. The caption's own text is
            checked against the face's measured coverage, and the fix is offered
            rather than described: `bestFontForText` already knows which face to
            reach for. */}
        {gaps.length > 0 && (
          <PropNote tone="warn">
            {fontEntry(clip.font).label} cannot draw{" "}
            {gaps.map((id) => (scriptEntry(id) || {}).label || id).join(" or ")} — the
            video would show empty boxes.{" "}
            <button
              type="button"
              className="an-tp-btn"
              onClick={() => set({ font: suggestion })}
            >
              Use {fontEntry(suggestion).label}
            </button>
          </PropNote>
        )}
        <PropRow
          label="Size"
          reset={() => set({ size: CAPTION_DEFAULTS.size })}
          changed={off("size")}
          resetTo="medium"
        >
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
        {/* The escape hatch from S/M/L. Quoted at 1080p and scaled by the real
            frame height, like `stroke_px`, so a 120px title is the same
            fraction of the picture at 720p and at 4K. 0 hands the size back to
            the three buttons above, which is what the placeholder shows. */}
        <PropRow
          label="Exact size"
          title="Font size in pixels at 1080p. Empty uses the S / M / L preset above."
          reset={() => set({ size_px: 0 })}
          changed={off("size_px")}
          resetTo="the preset size"
          info="Small, Medium and Large are 36, 51 and 77px at 1080p. Type a number here for anything else — it scales with the frame, so the same project looks the same exported at any resolution."
        >
          <NumField
            unit="px"
            step="1"
            min="0"
            max="400"
            placeholder={String(PRESET_PX[clip.size || "medium"] ?? 51)}
            value={clip.size_px || ""}
            onChange={(e) =>
              onChange(clip.id, {
                size_px: Math.max(0, Math.min(400, parseFloat(e.target.value || 0) || 0)),
              })
            }
          />
        </PropRow>
        <PropRow
          label="Align"
          reset={() => set({ align: CAPTION_DEFAULTS.align })}
          changed={off("align")}
          resetTo="centred"
        >
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
        {/* CASE, not a second copy of the text: the typed words are left alone
            and only their drawing changes, so switching back to Aa gives you
            what you wrote. Applied BEFORE the wrap on both sides — see
            `_apply_case`. */}
        <PropRow
          label="Case"
          title="Draw the text in capitals, in lower case, or as typed"
          reset={() => set({ text_case: CAPTION_DEFAULTS.text_case })}
          changed={off("text_case")}
          resetTo="as typed"
          info="This changes how the words are DRAWN, not what you typed — switch back to Aa and your original capitals are still there."
        >
          <span className="an-tp-group">
            {TEXT_CASES.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`an-tp-btn ${(clip.text_case || "none") === c.id ? "on" : ""}`}
                title={c.hint}
                onClick={() => onChange(clip.id, { text_case: c.id })}
              >
                {c.label}
              </button>
            ))}
          </span>
        </PropRow>
        <PropRow
          label="Colour"
          title="Text colour"
          reset={() => set({ color: CAPTION_DEFAULTS.color })}
          changed={off("color")}
          resetTo="white"
        >
          <input
            type="color"
            className="an-colour"
            value={clip.color}
            onChange={(e) => onChange(clip.id, { color: e.target.value })}
          />
        </PropRow>
        <PropRow
          label="Tracking"
          title="Space between letters, as a fraction of the text size"
          reset={() => set({ letter_spacing: 0 })}
          changed={off("letter_spacing")}
          resetTo="none"
        >
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
        <PropRow
          label="Line spacing"
          title="Distance between lines, as a multiple of the font's own"
          reset={() => set({ line_height: CAPTION_DEFAULTS.line_height })}
          changed={off("line_height")}
          resetTo="1.28"
        >
          <NumField
            step="0.02"
            min="0.6"
            max="3"
            value={clip.line_height ?? 1.28}
            onChange={(e) =>
              onChange(clip.id, {
                line_height: Math.max(0.6, Math.min(3, parseFloat(e.target.value || 0) || 0.6)),
              })
            }
          />
        </PropRow>
        {/* How wide the block may get before it breaks — a fraction of the
            FRAME on both sides, so a title narrowed here breaks in the same
            place in the MP4. */}
        <PropSlider
          label="Line width"
          title="How wide the text may run before it wraps, as a fraction of the frame"
          min="0.1"
          max="1"
          step="0.02"
          value={clip.wrap ?? 0.86}
          readout={`${Math.round((clip.wrap ?? 0.86) * 100)}%`}
          reset={() => set({ wrap: CAPTION_DEFAULTS.wrap })}
          changed={off("wrap")}
          resetTo="86%"
          info="Narrow this to force a title to break after a few words instead of running the width of the shot."
          {...gesture}
          onChange={(e) => onChange(clip.id, { wrap: parseFloat(e.target.value) })}
        />
      </PropGroup>

      {/* --- Kept readable over the art -------------------------------------- */}
      <PropGroup id="text:legibility" title="Readability">
        <PropRow
          label="Backdrop"
          title="How the text is kept readable over the art"
          reset={() => set({ backdrop: CAPTION_DEFAULTS.backdrop })}
          changed={off("backdrop")}
          resetTo="a shaded bar"
        >
          <select
            className="an-select"
            value={backdrop}
            title={TEXT_BACKDROPS.find((b) => b.id === backdrop)?.hint}
            onChange={(e) => onChange(clip.id, backdropPatch(e.target.value))}
          >
            {TEXT_BACKDROPS.map((b) => (
              <option key={b.id} value={b.id} title={b.hint}>
                {b.label}
              </option>
            ))}
          </select>
        </PropRow>
        {/* The backdrop's own look, and only for the kinds that HAVE one —
            "Outline only" draws no box, so a fill colour on it would be four
            controls that change nothing. */}
        {hasFill && (
          <>
            <PropRow
              label="Fill"
              title="The backdrop's colour"
              reset={() => set({ backdrop_color: CAPTION_DEFAULTS.backdrop_color })}
              changed={off("backdrop_color")}
              resetTo="black"
            >
              <input
                type="color"
                className="an-colour"
                value={clip.backdrop_color || "#000000"}
                onChange={(e) => onChange(clip.id, { backdrop_color: e.target.value })}
              />
            </PropRow>
            {/* Untouched, this shows what the KIND is worth — 55% for a shaded
                bar, 88% for a solid box. Dragging it makes the number the
                clip's own, and ↺ hands it back to the kind. */}
            <PropSlider
              label="Fill opacity"
              title="How solid the backdrop is"
              min="0"
              max="1"
              step="0.02"
              value={fillAlpha}
              readout={`${Math.round(fillAlpha * 100)}%`}
              reset={() => set({ backdrop_opacity: null })}
              changed={clip.backdrop_opacity != null}
              resetTo="what the backdrop kind is worth"
              info="Leave this alone and the backdrop is whatever its kind is worth — a shaded bar 55%, a solid box 88%. Move it and this clip keeps its own number."
              {...gesture}
              onChange={(e) =>
                onChange(clip.id, { backdrop_opacity: parseFloat(e.target.value) })
              }
            />
            <PropRow
              label="Corners"
              title="Corner radius, as a fraction of the text size"
              reset={() => set({ backdrop_radius: CAPTION_DEFAULTS.backdrop_radius })}
              changed={off("backdrop_radius")}
              resetTo="0.25"
            >
              <NumField
                step="0.05"
                min="0"
                max="2"
                value={clip.backdrop_radius ?? 0.25}
                onChange={(e) =>
                  onChange(clip.id, {
                    backdrop_radius: Math.max(
                      0,
                      Math.min(2, parseFloat(e.target.value || 0) || 0)
                    ),
                  })
                }
              />
            </PropRow>
            <PropRow
              label="Padding"
              title="How much room there is around the text inside the backdrop"
              reset={() => set({ backdrop_pad: CAPTION_DEFAULTS.backdrop_pad })}
              changed={off("backdrop_pad")}
              resetTo="normal"
            >
              <NumField
                tag="×"
                step="0.1"
                min="0"
                max="4"
                value={clip.backdrop_pad ?? 1}
                onChange={(e) =>
                  onChange(clip.id, {
                    backdrop_pad: Math.max(0, Math.min(4, parseFloat(e.target.value || 0) || 0)),
                  })
                }
              />
            </PropRow>
          </>
        )}
        <PropRow
          label="Outline"
          title="Outline thickness, in pixels at 1080p — it scales with the frame"
          reset={() => set({ stroke_px: 0, stroke_color: CAPTION_DEFAULTS.stroke_color })}
          changed={off("stroke_px") || off("stroke_color")}
          resetTo="no outline"
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
          reset={() => set({ shadow: 0 })}
          changed={off("shadow")}
          resetTo="no shadow"
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
        {/* Only once there IS a shadow. Its ink, its strength and which way it
            falls — 45° is down and to the right, which is the offset every
            caption cast before the angle existed. */}
        {(clip.shadow ?? 0) > 0 && (
          <>
            <PropRow
              label="Shadow ink"
              title="The shadow's colour"
              reset={() => set({ shadow_color: CAPTION_DEFAULTS.shadow_color })}
              changed={off("shadow_color")}
              resetTo="black"
            >
              <input
                type="color"
                className="an-colour"
                value={clip.shadow_color || "#000000"}
                onChange={(e) => onChange(clip.id, { shadow_color: e.target.value })}
              />
            </PropRow>
            <PropSlider
              label="Shadow strength"
              title="How dark the shadow is"
              min="0"
              max="1"
              step="0.02"
              value={clip.shadow_opacity ?? 0.55}
              readout={`${Math.round((clip.shadow_opacity ?? 0.55) * 100)}%`}
              reset={() => set({ shadow_opacity: CAPTION_DEFAULTS.shadow_opacity })}
              changed={off("shadow_opacity")}
              resetTo="55%"
              {...gesture}
              onChange={(e) => onChange(clip.id, { shadow_opacity: parseFloat(e.target.value) })}
            />
            <PropRow
              label="Shadow angle"
              title="Which way it falls, in degrees clockwise from the right"
              reset={() => set({ shadow_angle: CAPTION_DEFAULTS.shadow_angle })}
              changed={off("shadow_angle")}
              resetTo="45° (down and right)"
              info="0° throws it to the right, 90° straight down, 180° to the left. The distance is the Shadow value above, so turning this moves the shadow round the text without lengthening it."
            >
              <NumField
                unit="°"
                step="5"
                min="0"
                max="360"
                value={clip.shadow_angle ?? 45}
                onChange={(e) =>
                  onChange(clip.id, {
                    shadow_angle: Math.max(
                      0,
                      Math.min(360, parseFloat(e.target.value || 0) || 0)
                    ),
                  })
                }
              />
            </PropRow>
          </>
        )}
      </PropGroup>

      {/* --- In/out animation ---------------------------------------------
          ⚠ A PRESET IS A KEYFRAME MACRO AND NOTHING ELSE — it writes keys on
          opacity/x/y and gets out of the way, so the timeline shows its
          diamonds, every key can be dragged afterwards, undo treats it as one
          edit, and the exporter needed no changes at all. Nothing records which
          preset was used, which is why none of these is ever shown as "current":
          once you have moved one of its keys, there is no current preset. */}
      <PropGroup id="text:preset" title="In / out animation">
        {/* ⚠ THE SPEED IS PICKED BEFORE THE PRESET, WHICH IS WHY IT IS ABOVE IT.
            A preset is applied the instant you click it — there is no "apply"
            button and no dialog — so a speed control underneath would be one you
            could only reach by choosing the animation twice.
            ⚠ AND IT IS NOT STORED ANYWHERE. It is the length of the beats this
            click writes, held for as long as the pane is open; the document
            keeps the KEYS, which is the honest record of what the animation is
            and the only one you can drag afterwards. */}
        <PropRow
          label="Speed"
          title="How long the in and out beats are for the preset you pick next"
          info="This is not saved on the clip — it only sets the length of the keys the next preset writes. Drag the keyframes on the timeline to change one afterwards."
        >
          <span className="an-tp-group">
            {BEAT_SPEEDS.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`an-tp-btn ${speed === s.id ? "on" : ""}`}
                title={s.hint}
                onClick={() => setSpeed(s.id)}
              >
                {s.label}
              </button>
            ))}
          </span>
        </PropRow>
        <PropRow full>
          <PresetPicker
            categories={TEXT_PRESET_CATEGORIES}
            presets={TEXT_PRESETS}
            onPick={(id) =>
              onChange(
                clip.id,
                applyTextPreset(stored || clip, id, beatOptions(speed))
              )
            }
          />
        </PropRow>
        {/* A preset writes keys on FIVE properties at once, so its ↺ has to
            clear all five — each row's own ↺ would leave a fifth of the
            animation behind, which is not "no animation" and does not look
            like one either.
            ⚠ FIVE, NOT THREE, SINCE THE PRESET SHELVES LANDED. `scale` and
            `rotation` joined `OWNED` in `text_presets.js` when Pop, Zoom and
            Spin arrived, and a ↺ that still cleared three would leave a caption
            spinning after you had asked for the animation to be taken off. The
            two lists are the same list and have to stay that way. */}
        <PropRow
          label="Animation"
          title="Every key a preset wrote — on opacity, position, size and angle"
          reset={() =>
            set({
              keyframes: Object.fromEntries(
                Object.entries((stored || clip).keyframes || {}).filter(
                  ([prop]) => !PRESET_TRACKS.includes(prop)
                )
              ),
            })
          }
          changed={PRESET_TRACKS.some(keyed)}
          resetTo="no animation"
          info="These write keyframes you can then drag on the timeline — a preset isn't a mode, it's a starting point."
        >
          <span className="an-row-read">
            {PRESET_TRACKS.some(keyed) ? "keyframed" : "none"}
          </span>
        </PropRow>
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
