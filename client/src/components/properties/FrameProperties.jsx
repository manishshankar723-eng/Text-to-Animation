// FrameProperties.jsx — the pane for a clip on the sequence, whichever of the
// three kinds it is. The rows every kind shares live here; the kind-specific
// ones are slotted in from `VideoClipProperties`.
//
// Laid out with the primitives in `PropGroup.jsx` — named sections, one property
// per row, one ⏱ per row — so read that file's header before moving anything
// here. In particular the Motion rows are in `ANIMATABLE` order (scale, x, y,
// opacity) because the timeline draws its diamond rows in that order and the two
// are meant to read as the same list.

import Icon from "../Icon.jsx";
import KeyframeControls from "../KeyframeControls.jsx";
// ⚠ THE SAME ANSWER THE TIMELINE'S COPY OF THIS BUTTON READS. Both open the
// same priced dialog and both spend `cap.veo-render`, so both ask the same
// question — asked here rather than passed in, because a prop for this would
// have to be threaded through a pane that only hosts the button.
import useCapability from "../../useCapability.js";
import VideoClipProperties, { ColorCardProperties } from "./VideoClipProperties.jsx";
import { PropGroup, PropRow, NumField, PropSlider } from "./PropGroup.jsx";
import { clamp } from "../../animatic/util.js";

// What this clip is called in its own header. Also the word the rest of the
// pane uses for it, so "Duration" below is unambiguous whichever kind is up.
const KIND_LABEL = { image: "Frame", video: "Video clip", color: "Colour card" };

// The hold a clip is created with, and therefore what ↺ on Duration goes back
// to. ⚠ Matches `newFrame` in AnimaticEditor and `duration_ms`'s default on
// `AnimaticFrame`; the three have to agree or a reset lands somewhere the clip
// has never been.
const DEFAULT_HOLD_MS = 2000;

export default function FrameProperties({
  frame,
  index,
  url,
  kf,
  gesture,
  sourceMs,
  // The LOOK rows (effects, mask, blend), built by the editor and slotted in.
  look,
  // The two "reach back to the storyboard" groups — redraw this shot, and run
  // it longer. Slotted in the same way `look` is and for the same reason: they
  // talk to the server and this file stays presentational. Both render nothing
  // for a clip that has no board panel behind it, which is every animatic built
  // from uploaded stills.
  board,
  onChange,
  onDuplicate,
  onDelete,
  onAnimate,
  veo,
  animating,
}) {
  // A frame's own pan / zoom / fade. New with keyframes, and the reason they
  // matter most here: keyframe `Scale` and `Position X` and a held storyboard
  // panel stops being a slide and becomes a shot. All four default to an
  // identity transform, so a frame nobody touches is placed exactly as it
  // always was.
  const pct = (v) => Math.round((v ?? 0) * 100);
  const kind = frame.kind || "image";
  // May this account render footage at all? Fail-open until the boot call
  // answers — see `entitlements.js`.
  const veoCap = useCapability("veo-render");
  // Is this property animated? A ↺ has to light up for that too — a keyframed
  // scale is a change even when the value under the playhead reads 100%.
  const keyed = (prop) => (frame.keyframes?.[prop] || []).length > 0;
  // Put one property back, keys and all. The keyframe map is rewritten rather
  // than mutated, for the same reason every write in this editor is: the
  // document is a value, and undo compares snapshots of it.
  const resetProp = (prop, value) => {
    const keys = { ...(frame.keyframes || {}) };
    delete keys[prop];
    onChange(frame.id, { [prop]: value, keyframes: keys });
  };
  const moved =
    (frame.scale ?? 1) !== 1 ||
    (frame.x ?? 0.5) !== 0.5 ||
    (frame.y ?? 0.5) !== 0.5 ||
    (frame.opacity ?? 1) !== 1 ||
    Object.keys(frame.keyframes || {}).length > 0;

  return (
    <div className="an-props">
      {/* WHAT AM I LOOKING AT. One block at the top saying which clip this is,
          so nothing below has to repeat it and every group can get straight to
          its own settings. */}
      <div className="an-prop-ident">
        <div className="an-prop-thumb">
          {kind === "color" ? (
            // A card has no picture to show, so it shows itself.
            <span className="an-prop-card" style={{ background: frame.color || "#000000" }} />
          ) : url ? (
            <img src={url} alt={frame.label || `Frame ${index + 1}`} />
          ) : (
            <span className="fs-thumb-wait" />
          )}
        </div>
        <div className="an-prop-ident-text">
          <span className="an-prop-kind">{KIND_LABEL[kind] || "Frame"}</span>
          <span className="an-prop-name">{frame.label || `Shot ${index + 1}`}</span>
          <span className="an-prop-sub">#{index + 1} in the sequence</span>
        </div>
      </div>

      <PropGroup id="frame:clip" title="Clip">
        <PropRow
          label="Name"
          title="Shown on the timeline, and burned in when 'shot labels' is on"
          reset={() => onChange(frame.id, { label: "" })}
          changed={Boolean(frame.label)}
          resetTo={`“Shot ${index + 1}”`}
        >
          <input
            className="an-prop-input"
            value={frame.label || ""}
            placeholder={`Shot ${index + 1}`}
            onChange={(e) => onChange(frame.id, { label: e.target.value })}
          />
        </PropRow>

        <PropRow
          label="Duration"
          title="How much of the TIMELINE this clip occupies — everything after it moves"
          reset={() => onChange(frame.id, { duration_ms: DEFAULT_HOLD_MS })}
          changed={frame.duration_ms !== DEFAULT_HOLD_MS}
          resetTo={`${DEFAULT_HOLD_MS / 1000}s`}
        >
          <NumField
            unit="s"
            step="0.1"
            min="0.1"
            value={(frame.duration_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(frame.id, {
                duration_ms: Math.max(100, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
      </PropGroup>

      {/* What this KIND of clip adds. A still adds nothing — which is why every
          animatic that exists today shows exactly the pane it always did. */}
      {kind === "video" && (
        <VideoClipProperties clip={frame} sourceMs={sourceMs} onChange={onChange} />
      )}
      {kind === "color" && <ColorCardProperties clip={frame} onChange={onChange} />}

      {/* --- Motion. ⚠ ROWS FOLLOW `ANIMATABLE.frame` — scale, x, y, opacity —
              and the timeline's diamond rows follow the same list. Re-order one
              without the other and the two panes stop describing each other. */}
      <PropGroup
        id="frame:motion"
        title="Motion"
        hint="Press ⏱, move the playhead, change the value"
      >
        {/* ⚠ Each ↺ also clears that property's KEYFRAMES, which is the only
            honest reading of "put this back": a scale left animated from 100%
            to 100% is not the identity transform this clip started with, and
            leaving the keys behind would make the reset look like it had
            failed the moment the playhead moved. */}
        <PropRow
          label="Scale"
          title="100% is the whole picture. Above that it is pushed in."
          reset={() => resetProp("scale", 1)}
          changed={(frame.scale ?? 1) !== 1 || keyed("scale")}
          resetTo="100%"
        >
          <NumField
            unit="%"
            step="5"
            min="10"
            max="1000"
            value={pct(frame.scale ?? 1)}
            onChange={(e) =>
              onChange(frame.id, {
                scale: clamp((parseFloat(e.target.value) || 100) / 100, 0.1, 10),
              })
            }
          />
          {kf && <KeyframeControls {...kf} prop="scale" />}
        </PropRow>

        <PropRow
          label="Position X"
          title="Across the frame. 50% is centred."
          reset={() => resetProp("x", 0.5)}
          changed={(frame.x ?? 0.5) !== 0.5 || keyed("x")}
          resetTo="50%"
        >
          <NumField
            unit="%"
            step="1"
            value={pct(frame.x ?? 0.5)}
            onChange={(e) =>
              onChange(frame.id, { x: clamp((parseFloat(e.target.value) || 0) / 100, -2, 3) })
            }
          />
          {kf && <KeyframeControls {...kf} prop="x" />}
        </PropRow>

        <PropRow
          label="Position Y"
          title="Down the frame. 50% is centred."
          reset={() => resetProp("y", 0.5)}
          changed={(frame.y ?? 0.5) !== 0.5 || keyed("y")}
          resetTo="50%"
        >
          <NumField
            unit="%"
            step="1"
            value={pct(frame.y ?? 0.5)}
            onChange={(e) =>
              onChange(frame.id, { y: clamp((parseFloat(e.target.value) || 0) / 100, -2, 3) })
            }
          />
          {kf && <KeyframeControls {...kf} prop="y" />}
        </PropRow>

        <PropSlider
          label="Opacity"
          title="How much of this clip you can see through"
          min="0"
          max="1"
          step="0.05"
          value={frame.opacity ?? 1}
          readout={`${pct(frame.opacity ?? 1)}%`}
          kf={kf && <KeyframeControls {...kf} prop="opacity" />}
          reset={() => resetProp("opacity", 1)}
          changed={(frame.opacity ?? 1) !== 1 || keyed("opacity")}
          resetTo="100%"
          {...gesture}
          onChange={(e) => onChange(frame.id, { opacity: parseFloat(e.target.value) })}
        />

        {/* One press, rather than making someone type 100 / 50 / 50 / 100 back
            in — which is the only way to undo a motion once the ⏱ is off. */}
        {moved && (
          <PropRow full>
            <button
              type="button"
              className="btn small ghost"
              onClick={() =>
                onChange(frame.id, { scale: 1, x: 0.5, y: 0.5, opacity: 1, keyframes: {} })
              }
              title="Back to the whole picture, centred, with no animation"
            >
              Reset motion
            </button>
          </PropRow>
        )}
      </PropGroup>

      {look}

      {/* Below the Look on purpose: these change the PICTURE ITSELF, on the
          storyboard, which is a bigger act than anything above them and belongs
          at the end of the pane rather than in the middle of the framing rows. */}
      {board}

      {/* --- Turn this shot into real footage ---------------------------- */}
      {/* ⚠ SPENDS MONEY, so this button does not render anything — it opens
          the dialog that prices the job first. The wording changes once a clip
          exists, because paying a second time has to be a deliberate act and
          never look like a retry. */}
      {/* ⚠ THE WHOLE GROUP GOES when the capability is not visible — a
          hidden capability must not leave an empty "Footage" section behind. A
          LOCKED one keeps its group, because the rendered-cost row below is
          still the record of what this shot has already cost. */}
      {onAnimate && veoCap.visible && (
        <PropGroup id="frame:veo" title="Footage" hint="Turn this still into moving video">
          <PropRow full>
            <button
              type="button"
              className={`btn small ${veoCap.on ? "" : "cap-off"}`}
              disabled={!veoCap.on || animating}
              onClick={() => onAnimate(frame.id)}
              title={
                !veoCap.on
                  ? veoCap.reason
                  : veo?.status === "ready"
                    ? "Render this shot again with Veo — it costs the same as the first time"
                    : "Turn this still into real footage with Veo (you'll see the price first)"
              }
            >
              {!veoCap.on
                ? "🔒 Animate with Veo"
                : animating
                  ? "Animating…"
                  : veo?.status === "ready"
                    ? "✨ Render again with Veo"
                    : "✨ Animate with Veo"}
            </button>
          </PropRow>
          {/* Printed, not only hovered — see the same note in DirectorPanel. */}
          {!veoCap.on && (
            <PropRow full>
              <span className="tiny muted">🔒 {veoCap.reason}</span>
            </PropRow>
          )}
          {veo?.status === "ready" && (
            <PropRow label="Rendered" title="What this shot has already cost">
              <span className="an-row-read">~${(veo.cost_usd || 0).toFixed(2)}</span>
            </PropRow>
          )}
          {veo?.status === "failed" && (
            <PropRow full>
              <span className="tiny an-status-error">{veo.error}</span>
            </PropRow>
          )}
        </PropGroup>
      )}

      <div className="an-prop-actions">
        <button type="button" className="btn small ghost" onClick={() => onDuplicate(frame.id)}>
          <Icon name="copy" /> Duplicate
        </button>
        <button type="button" className="btn small danger-btn" onClick={() => onDelete(frame.id)}>
          <Icon name="close" /> Remove
        </button>
      </div>
    </div>
  );
}
