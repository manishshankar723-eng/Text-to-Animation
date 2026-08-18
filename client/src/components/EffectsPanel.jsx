// EffectsPanel.jsx — the LOOK groups a picture clip adds to its Properties pane.
//
// Not a pane of its own: it is slotted into `FrameProperties` and (for an
// overlay) `ShapeProperties`, the same way `VideoClipProperties` slots its
// kind-specific groups in. A frame and an overlay carry exactly the same three
// fields — they are the two clips that are PICTURES — so they get exactly the
// same rows, and a shape or a caption gets none.
//
// Two groups, because they are two questions: LOOK is how this clip sits on what
// is under it (blend, mask), EFFECTS is the ordered chain of grades applied to
// its pixels. Each effect in the chain is itself a collapsible group, so a clip
// carrying four of them is still one screen you can read.
//
// Everything here writes through `onChange` and holds no state, like every
// other pane, so it cannot disagree with the document.
//
// ⚠ EVERY NUMERIC PARAMETER IS KEYFRAMABLE, through the ordinary ⏱ — the track
// is just named `fx:<effect id>:<param>` instead of `scale`. That is the whole
// reason the parameters are addressed by a flat string: the timeline's diamond
// rows, the undo stack and the "typing a value sets a key" rule all work on a
// graded clip with no changes at all.

import { useEffect, useState } from "react";

import * as api from "../api.js";
import {
  BLEND_MODES,
  EFFECT_KINDS,
  EFFECT_PARAMS,
  DEFAULT_MASK,
  MASK_KINDS,
  effectParams,
  effectKey,
} from "../animatic/scene.js";
import { MAX_EFFECTS } from "../animatic/gl/shaders/layer.js";
import Icon from "./Icon.jsx";
import KeyframeControls from "./KeyframeControls.jsx";
import {
  PropGroup,
  PropRow,
  NumField,
  PropSlider,
  PropNote,
} from "./properties/PropGroup.jsx";
import { clamp } from "../animatic/util.js";

const EFFECT_LABEL = {
  brightness: "Brightness",
  contrast: "Contrast",
  saturation: "Saturation",
  lut: "Colour look (LUT)",
  chroma: "Chroma key",
};

const BLEND_LABEL = {
  normal: "Normal",
  multiply: "Multiply",
  screen: "Screen",
  overlay: "Overlay",
  add: "Add",
  darken: "Darken",
  lighten: "Lighten",
};

const MASK_LABEL = { none: "No mask", rect: "Rectangle", ellipse: "Ellipse" };

// How each numeric parameter is offered. `scale` is what one unit of the stored
// value is worth on screen: an amount of 1.0 reads as 100%, a feather of 0.1 as
// 10%. Kept here rather than in the scene model because it is a question about
// the CONTROL, not about the picture.
const FIELD = {
  amount: { label: "Amount", scale: 100, unit: "%", min: 0, max: 300, step: 5 },
  similarity: { label: "Tolerance", scale: 100, unit: "%", min: 0, max: 100, step: 1 },
  smoothness: { label: "Softness", scale: 100, unit: "%", min: 0, max: 50, step: 1 },
  spill: { label: "Spill removal", scale: 100, unit: "%", min: 0, max: 100, step: 5 },
};

// The names the LUT dropdown offers. Fetched once for the tab: the list is the
// contents of the server's `luts/` folder and does not change while you edit.
let lutNamesPromise = null;
function useLutNames() {
  const [names, setNames] = useState([]);
  useEffect(() => {
    let live = true;
    lutNamesPromise = lutNamesPromise || api.listLuts().catch(() => []);
    lutNamesPromise.then((list) => {
      if (live) setNames(Array.isArray(list) ? list : []);
    });
    return () => {
      live = false;
    };
  }, []);
  return names;
}

const prettyLut = (name) =>
  name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

/**
 * @param clip     the RESOLVED clip (values at the playhead) for display
 * @param stored   the STORED clip — the structure edits work on
 * @param kf       the ⏱ bundle, or null when nothing is inspected
 * @param gesture  the drag bracket every slider in this editor wears
 * @param onChange (id, patch) => void
 */
export default function EffectsPanel({ clip, stored, kf, gesture, onChange }) {
  const lutNames = useLutNames();
  // ⚠ STRUCTURE COMES FROM `stored`, VALUES FROM `clip`. `resolveLook` drops an
  // effect kind this build doesn't know, so a chain edited through the resolved
  // list would renumber itself around one and delete the wrong row.
  const effects = stored.effects || [];
  const mask = clip.mask || {};
  const masked = (mask.kind || "none") !== "none";
  const blend = clip.blend || "normal";
  // Resolved parameters, by effect id, so a keyframed value reads as what is on
  // screen rather than as what is saved.
  const shown = new Map((clip.effects || []).map((e) => [e.id, e.params]));

  const writeEffects = (next) => onChange(stored.id, { effects: next });

  const addEffect = (kind) => {
    if (!kind || effects.length >= MAX_EFFECTS) return;
    writeEffects([
      ...effects,
      // A unique id, because it is what the keyframe tracks name. Date-based
      // rather than an index: an effect keeps its animation when the chain is
      // re-ordered, which is the point of keying by id at all.
      { id: `fx${Date.now().toString(36)}${effects.length}`, kind, params: {} },
    ]);
  };

  const removeEffect = (index) => {
    const gone = effectKey(effects[index], index);
    const next = effects.filter((_, i) => i !== index);
    // The keyframe tracks go with it. Leaving them would animate a parameter
    // that no longer exists, and they would come back to life the moment an
    // effect happened to be given the same id.
    const keyframes = { ...(stored.keyframes || {}) };
    for (const track of Object.keys(keyframes)) {
      if (track.startsWith(`fx:${gone}:`)) delete keyframes[track];
    }
    onChange(stored.id, { effects: next, keyframes });
  };

  const moveEffect = (index, by) => {
    const to = index + by;
    if (to < 0 || to >= effects.length) return;
    const next = [...effects];
    [next[index], next[to]] = [next[to], next[index]];
    writeEffects(next);
  };

  // A STRING parameter (which LUT, which colour is the screen) is written
  // straight onto the effect — it is never interpolated, so it never becomes a
  // key. A NUMERIC one goes through the flat track name so `writeAnimatable`
  // can turn it into a key when the ⏱ is on.
  const setText = (index, param, value) => {
    const next = [...effects];
    next[index] = {
      ...next[index],
      params: { ...effectParams(next[index]), ...(next[index].params || {}), [param]: value },
    };
    writeEffects(next);
  };

  const setNumber = (id, param, value) => onChange(stored.id, { [`fx:${id}:${param}`]: value });

  const setMask = (axis, value, lo, hi) =>
    onChange(stored.id, {
      [`mask:${axis}`]: clamp((parseFloat(value) || 0) / 100, lo, hi),
    });

  // --- Putting things back -------------------------------------------------
  // ⚠ A ↺ HERE HAS TO CLEAR THE KEYFRAME TRACK TOO, and the track name is the
  // flat string the property is addressed by ("mask:x", "fx:e3:amount"). Miss
  // that and the reset appears to work until the playhead moves, at which point
  // the old animation puts the value straight back.
  const keyedTrack = (track) => ((stored.keyframes || {})[track] || []).length > 0;
  const withoutTrack = (track) => {
    const keys = { ...(stored.keyframes || {}) };
    delete keys[track];
    return keys;
  };
  const resetMask = (axis, value) =>
    onChange(stored.id, {
      [`mask:${axis}`]: value,
      keyframes: withoutTrack(`mask:${axis}`),
    });
  const resetParam = (id, param, value) =>
    onChange(stored.id, {
      [`fx:${id}:${param}`]: value,
      keyframes: withoutTrack(`fx:${id}:${param}`),
    });

  return (
    <>
      {/* --- Look: blend and mask ------------------------------------------- */}
      <PropGroup
        id="look:compose"
        title="Look"
        hint="How this clip sits on what's under it"
      >
        <PropRow
          label="Blend"
          title="How this clip combines with everything composited under it"
          reset={() => onChange(stored.id, { blend: "normal" })}
          changed={blend !== "normal"}
          resetTo="Normal"
        >
          <select
            value={blend}
            onChange={(e) => onChange(stored.id, { blend: e.target.value })}
          >
            {BLEND_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {BLEND_LABEL[mode] || mode}
              </option>
            ))}
          </select>
        </PropRow>

        <PropRow
          label="Mask"
          title="Show this clip only inside a region of the frame"
          reset={() => onChange(stored.id, { mask: { ...mask, kind: "none" } })}
          changed={masked}
          resetTo="no mask"
        >
          <select
            value={mask.kind || "none"}
            onChange={(e) => onChange(stored.id, { mask: { ...mask, kind: e.target.value } })}
          >
            {MASK_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {MASK_LABEL[kind] || kind}
              </option>
            ))}
          </select>
        </PropRow>

        {/* The mask's own geometry, indented so it reads as belonging to the row
            that switched it on rather than as four more properties of the clip.
            ⚠ Order follows `MASK_ANIMATABLE` (x, y, w, h, feather), for the same
            reason the Motion rows follow `ANIMATABLE`. */}
        {masked && (
          <div className="an-sub">
            <PropRow
              label="X"
              title="Centre of the mask, across the frame"
              reset={() => resetMask("x", DEFAULT_MASK.x)}
              changed={(mask.x ?? DEFAULT_MASK.x) !== DEFAULT_MASK.x || keyedTrack("mask:x")}
              resetTo="50%"
            >
              <NumField
                unit="%"
                step="1"
                value={Math.round((mask.x ?? 0.5) * 100)}
                onChange={(e) => setMask("x", e.target.value, -1, 2)}
              />
              {kf && <KeyframeControls {...kf} prop="mask:x" />}
            </PropRow>
            <PropRow
              label="Y"
              title="Centre of the mask, down the frame"
              reset={() => resetMask("y", DEFAULT_MASK.y)}
              changed={(mask.y ?? DEFAULT_MASK.y) !== DEFAULT_MASK.y || keyedTrack("mask:y")}
              resetTo="50%"
            >
              <NumField
                unit="%"
                step="1"
                value={Math.round((mask.y ?? 0.5) * 100)}
                onChange={(e) => setMask("y", e.target.value, -1, 2)}
              />
              {kf && <KeyframeControls {...kf} prop="mask:y" />}
            </PropRow>
            <PropRow
              label="Width"
              reset={() => resetMask("w", DEFAULT_MASK.w)}
              changed={(mask.w ?? DEFAULT_MASK.w) !== DEFAULT_MASK.w || keyedTrack("mask:w")}
              resetTo={`${Math.round(DEFAULT_MASK.w * 100)}%`}
            >
              <NumField
                unit="%"
                step="1"
                min="1"
                value={Math.round((mask.w ?? 0.5) * 100)}
                onChange={(e) => setMask("w", e.target.value, 0.01, 4)}
              />
              {kf && <KeyframeControls {...kf} prop="mask:w" />}
            </PropRow>
            <PropRow
              label="Height"
              reset={() => resetMask("h", DEFAULT_MASK.h)}
              changed={(mask.h ?? DEFAULT_MASK.h) !== DEFAULT_MASK.h || keyedTrack("mask:h")}
              resetTo={`${Math.round(DEFAULT_MASK.h * 100)}%`}
            >
              <NumField
                unit="%"
                step="1"
                min="1"
                value={Math.round((mask.h ?? 0.5) * 100)}
                onChange={(e) => setMask("h", e.target.value, 0.01, 4)}
              />
              {kf && <KeyframeControls {...kf} prop="mask:h" />}
            </PropRow>
            <PropSlider
              label="Feather"
              title="How soft the mask's edge is"
              min="0"
              max="1"
              step="0.01"
              value={mask.feather ?? 0.1}
              readout={`${Math.round((mask.feather ?? 0.1) * 100)}%`}
              kf={kf && <KeyframeControls {...kf} prop="mask:feather" />}
              reset={() => resetMask("feather", DEFAULT_MASK.feather)}
              changed={
                (mask.feather ?? DEFAULT_MASK.feather) !== DEFAULT_MASK.feather ||
                keyedTrack("mask:feather")
              }
              resetTo={`${Math.round(DEFAULT_MASK.feather * 100)}%`}
              {...gesture}
              onChange={(e) => onChange(stored.id, { "mask:feather": parseFloat(e.target.value) })}
            />
            <PropRow full>
              <label className="an-tp-check">
                <input
                  type="checkbox"
                  checked={Boolean(mask.invert)}
                  onChange={(e) =>
                    onChange(stored.id, { mask: { ...mask, invert: e.target.checked } })
                  }
                />
                <span>Invert — keep everything outside instead</span>
              </label>
            </PropRow>
          </div>
        )}
      </PropGroup>

      {/* --- The effect chain ----------------------------------------------- */}
      <PropGroup
        id="look:effects"
        title="Effects"
        count={effects.length || null}
        hint="Applied top to bottom"
      >
        {effects.length === 0 && (
          <PropNote>
            No effects yet. Add one below to grade this clip, load a colour look,
            or key out a green screen.
          </PropNote>
        )}

        {effects.map((effect, index) => {
          const kind = effect.kind;
          const id = effectKey(effect, index);
          const known = EFFECT_KINDS.includes(kind);
          const params = shown.get(id) || effectParams(effect);
          return (
            <PropGroup
              key={id}
              id={`fx:${id}`}
              tone="fx"
              title={EFFECT_LABEL[kind] || kind}
              actions={
                <>
                  <button
                    type="button"
                    className="an-fx-btn"
                    disabled={index === 0}
                    onClick={() => moveEffect(index, -1)}
                    title="Move earlier in the chain"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="an-fx-btn"
                    disabled={index === effects.length - 1}
                    onClick={() => moveEffect(index, 1)}
                    title="Move later in the chain"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="an-fx-btn an-fx-del"
                    onClick={() => removeEffect(index)}
                    title="Remove this effect"
                  >
                    <Icon name="close" />
                  </button>
                </>
              }
            >
              {/* An effect this build doesn't know is kept and left alone rather
                  than deleted — it belongs to a newer client, and the project
                  round-trips through here unharmed. It simply isn't drawn. */}
              {!known ? (
                <PropNote>
                  This effect was added by a newer version. It's kept in the
                  project, but this build can't show or render it.
                </PropNote>
              ) : (
                <>
                  {kind === "lut" && (
                    <PropRow
                      label="Look"
                      title="Which colour look to load"
                      reset={() => setText(index, "name", "")}
                      changed={Boolean(params.name)}
                      resetTo="none"
                    >
                      <select
                        value={params.name || ""}
                        onChange={(e) => setText(index, "name", e.target.value)}
                      >
                        <option value="">— none —</option>
                        {lutNames.map((name) => (
                          <option key={name} value={name}>
                            {prettyLut(name)}
                          </option>
                        ))}
                      </select>
                    </PropRow>
                  )}

                  {kind === "chroma" && (
                    <PropRow
                      label="Screen"
                      title="The colour to key out — pick it off your green screen"
                      reset={() => setText(index, "color", EFFECT_PARAMS.chroma.color)}
                      changed={(params.color || "").toLowerCase() !== EFFECT_PARAMS.chroma.color}
                      resetTo="green"
                    >
                      <input
                        type="color"
                        className="an-colour"
                        value={params.color || "#00ff00"}
                        onChange={(e) => setText(index, "color", e.target.value)}
                      />
                    </PropRow>
                  )}

                  {Object.entries(EFFECT_PARAMS[kind])
                    .filter(([, fallback]) => typeof fallback !== "string")
                    .map(([param]) => {
                      const field = FIELD[param] || {
                        label: param, scale: 100, unit: "%", min: 0, max: 300, step: 1,
                      };
                      const fallback = EFFECT_PARAMS[kind][param];
                      return (
                        <PropRow
                          label={field.label}
                          key={param}
                          reset={() => resetParam(id, param, fallback)}
                          changed={
                            (params[param] ?? fallback) !== fallback ||
                            keyedTrack(`fx:${id}:${param}`)
                          }
                          resetTo={`${Math.round(fallback * field.scale)}${field.unit}`}
                        >
                          <NumField
                            unit={field.unit}
                            step={field.step}
                            min={field.min}
                            max={field.max}
                            value={Math.round((params[param] ?? 0) * field.scale)}
                            onChange={(e) =>
                              setNumber(
                                id,
                                param,
                                clamp(
                                  (parseFloat(e.target.value) || 0) / field.scale,
                                  field.min / field.scale,
                                  field.max / field.scale
                                )
                              )
                            }
                          />
                          {kf && <KeyframeControls {...kf} prop={`fx:${id}:${param}`} />}
                        </PropRow>
                      );
                    })}
                </>
              )}
            </PropGroup>
          );
        })}

        <PropRow full>
          {effects.length >= MAX_EFFECTS ? (
            <span className="tiny muted">
              {MAX_EFFECTS} effects is the most one clip can carry — the monitor
              draws them in a single pass, and it has room for exactly this many.
            </span>
          ) : (
            <select
              className="an-fx-add"
              value=""
              onChange={(e) => addEffect(e.target.value)}
              title="Add an effect to the end of the chain"
            >
              <option value="">+ Add an effect…</option>
              {EFFECT_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {EFFECT_LABEL[kind] || kind}
                </option>
              ))}
            </select>
          )}
        </PropRow>
      </PropGroup>
    </>
  );
}
