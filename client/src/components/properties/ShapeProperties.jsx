// ShapeProperties.jsx — a shape's settings, and an overlay picture's.
//
// Laid out with the primitives in `PropGroup.jsx`: named sections, one property
// per row, one ⏱ per row. The Transform rows are in `ANIMATABLE.shape` order
// (x, y, w, h, opacity, rotation) because the timeline draws its diamond rows in
// that order — see the header of `PropGroup.jsx`.

import Icon from "../Icon.jsx";
import KeyframeControls from "../KeyframeControls.jsx";
import { DEFAULT_SHAPE_COLOR, SHAPE_CATEGORIES, ShapeSwatch } from "../Shapes.jsx";
import { PropGroup, PropRow, NumField, PropSlider, PropNote } from "./PropGroup.jsx";
import { clamp } from "../../animatic/util.js";

// How long a shape or an overlay is created for, and therefore what ↺ on
// "Stays for" goes back to. ⚠ Matches `duration_ms`'s default on
// `AnimaticShape` / `AnimaticOverlay` and the clips the editor makes.
const DEFAULT_CLIP_MS = 2000;

// Where a fresh box sits and how big it is — what every ↺ in the Transform
// group goes back to. ⚠ THE TWO KINDS DIFFER, and only in size: `AnimaticShape`
// is created at 25% of the frame and `AnimaticOverlay` at 30%. Reading both
// from one table would make the reset on a picture put it back to a size it had
// never been.
const SHAPE_GEO = { x: 0.5, y: 0.5, w: 0.25, h: 0.25 };
const OVERLAY_GEO = { x: 0.5, y: 0.5, w: 0.3, h: 0.3 };

// A shape's settings. Position and size are shown as PERCENTAGES of the frame,
// because that is what they are — the project stores fractions so the same
// shape lands identically at 720p and 4K, and showing pixels here would be a
// number that means nothing outside this preview.
// Serves BOTH a shape and an overlay picture: they are the same box, placed
// with the same handles and the same numbers. `picture` (a blob url) is what
// says which — an overlay has no shape kind to pick and no fill to colour.
export default function ShapeProperties({
  shape,
  totalMs,
  picture,
  kf,
  gesture,
  // The LOOK rows (effects, mask, blend), built by the editor and slotted in —
  // an OVERLAY gets them because it is a picture; a shape is vector and has no
  // pixels of its own to grade, so it is simply passed nothing.
  look,
  onChange,
  onDuplicate,
  onDelete,
  onClose,
}) {
  const isPicture = picture !== undefined;
  const overruns = shape.start_ms + shape.duration_ms > totalMs;
  const pct = (v) => Math.round(v * 100);
  const setPct = (field, value, lo, hi) =>
    onChange(shape.id, { [field]: clamp((parseFloat(value) || 0) / 100, lo, hi) });
  const geo = isPicture ? OVERLAY_GEO : SHAPE_GEO;
  // A ↺ on a transform row also clears that property's KEYS — see the note in
  // `FrameProperties`: a property left animated is not back where it started,
  // whatever the number under the playhead says.
  const keyed = (prop) => (shape.keyframes?.[prop] || []).length > 0;
  // The Scale row, read and written through `w`/`h` — see the block above it.
  // Measured on the WIDTH alone and applied to both, so a box that has been
  // stretched keeps the proportion it was stretched to instead of snapping back
  // to square the first time it is scaled.
  const scalePct = Math.round(((shape.w ?? geo.w) / geo.w) * 100);
  const setScale = (value) => {
    const fromW = shape.w ?? geo.w;
    const fromH = shape.h ?? geo.h;
    // ⚠ THE **FACTOR** IS WHAT GETS CLAMPED, NOT `w` AND `h` SEPARATELY — and
    // that is the whole of why this row keeps a box's proportion. Clamping the
    // two sides independently means a 2:1 box scaled down far enough has its
    // height pinned at the 2% floor while its width is still shrinking, and it
    // comes back up SQUARE. One number applied to both cannot do that: either
    // the pair moves or it does not. (Found by `tests/editor_scrub_check.py`
    // scaling an 80×40 box back to 100% and getting 25×25.)
    const wantedFactor = fromW > 0 ? (geo.w * ((parseFloat(value) || 0) / 100)) / fromW : 1;
    const factor = clamp(
      wantedFactor,
      Math.max(0.02 / fromW, 0.02 / fromH),
      Math.min(4 / fromW, 4 / fromH)
    );
    onChange(shape.id, { w: fromW * factor, h: fromH * factor });
  };
  const resetProp = (prop, value) => {
    const keys = { ...(shape.keyframes || {}) };
    delete keys[prop];
    onChange(shape.id, { [prop]: value, keyframes: keys });
  };

  return (
    <div className="an-props">
      <div className="an-prop-ident">
        {isPicture ? (
          <div className="an-prop-thumb">
            {picture ? <img src={picture} alt="" /> : <span className="fs-thumb-wait" />}
          </div>
        ) : null}
        <div className="an-prop-ident-text">
          <span className="an-prop-kind">{isPicture ? "Picture" : "Shape"}</span>
          <span className="an-prop-sub">
            {isPicture
              ? "An image laid over the video"
              : "Drawn on top of whatever is under it"}
          </span>
        </div>
      </div>

      {!isPicture && (
        <PropGroup id="shape:kind" title="Shape">
          {/* ⚠ STILL SWATCHES, NOW IN A SCROLLING BLOCK WITH ITS GROUPS NAMED.
              The shape IS its own label — forty-one names in a row of buttons is
              unreadable at this width, which is why this was never a `<select>`
              — but forty-one unlabelled swatches in one wrap is a bag of
              confetti. The captions are the SAME five groups the Shapes tab's
              folders are (`SHAPE_CATEGORIES`), in the same order, so "it was in
              Stars & bursts" is a true sentence in both places. Capped in height
              rather than collapsed: changing a shape's kind is a small
              adjustment, and a picker that has to be opened first is a worse
              trade than a picker you scroll. */}
          <PropRow label="Kind" title="What is drawn">
            <span className="an-shape-picker">
              {SHAPE_CATEGORIES.map((group) => (
                <span className="an-shape-picker-group" key={group.id}>
                  <span className="an-shape-picker-cap" title={group.note}>
                    {group.label}
                  </span>
                  <span className="an-tp-group">
                    {group.kinds.map((k) => (
                      <button
                        key={k.id}
                        type="button"
                        className={`an-tp-btn an-shape-pick ${shape.kind === k.id ? "on" : ""}`}
                        title={k.label}
                        onClick={() => onChange(shape.id, { kind: k.id })}
                      >
                        <ShapeSwatch kind={k.id} />
                      </button>
                    ))}
                  </span>
                </span>
              ))}
            </span>
          </PropRow>
          <PropRow
            label="Fill"
            title="Fill colour"
            reset={() => onChange(shape.id, { color: DEFAULT_SHAPE_COLOR })}
            changed={(shape.color || "").toLowerCase() !== DEFAULT_SHAPE_COLOR.toLowerCase()}
            resetTo="the default fill"
          >
            <input
              type="color"
              className="an-colour"
              value={shape.color}
              onChange={(e) => onChange(shape.id, { color: e.target.value })}
            />
          </PropRow>
        </PropGroup>
      )}

      <PropGroup id="shape:timing" title="Timing">
        <PropRow
          label="Starts at"
          title="How far into the video this appears"
          reset={() => onChange(shape.id, { start_ms: 0 })}
          changed={shape.start_ms > 0}
          resetTo="the start of the video"
        >
          <NumField
            unit="s"
            step="0.1"
            min="0"
            value={(shape.start_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(shape.id, {
                start_ms: Math.max(0, Math.round(parseFloat(e.target.value || 0) * 1000)),
              })
            }
          />
        </PropRow>
        <PropRow
          label="Stays for"
          title="How long it is on screen"
          reset={() => onChange(shape.id, { duration_ms: DEFAULT_CLIP_MS })}
          changed={shape.duration_ms !== DEFAULT_CLIP_MS}
          resetTo={`${DEFAULT_CLIP_MS / 1000}s`}
        >
          <NumField
            unit="s"
            step="0.1"
            min="0.1"
            value={(shape.duration_ms / 1000).toFixed(1)}
            onChange={(e) =>
              onChange(shape.id, {
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

      {/* ⚠ ROWS FOLLOW `ANIMATABLE.shape` — x, y, w, h, opacity, rotation. */}
      <PropGroup
        id="shape:transform"
        title="Transform"
        hint="Press ⏱, move the playhead, change the value"
      >
        <PropRow
          label="Position X"
          title="Across the frame. 50% is centred."
          reset={() => resetProp("x", geo.x)}
          changed={shape.x !== geo.x || keyed("x")}
          resetTo="50%"
        >
          <NumField
            unit="%"
            step="1"
            value={pct(shape.x)}
            onChange={(e) => setPct("x", e.target.value, -0.5, 1.5)}
          />
          {kf && <KeyframeControls {...kf} prop="x" />}
        </PropRow>
        <PropRow
          label="Position Y"
          title="Down the frame. 50% is centred."
          reset={() => resetProp("y", geo.y)}
          changed={shape.y !== geo.y || keyed("y")}
          resetTo="50%"
        >
          <NumField
            unit="%"
            step="1"
            value={pct(shape.y)}
            onChange={(e) => setPct("y", e.target.value, -0.5, 1.5)}
          />
          {kf && <KeyframeControls {...kf} prop="y" />}
        </PropRow>
        {/* --- SCALE -------------------------------------------------------
            ⚠ NOT A STORED FIELD, AND IT MUST NOT BECOME ONE without the
            renderers changing too. `w`/`h` ARE this box's size — there is no
            "natural size" underneath them the way a picture has one — so a
            second multiplier would be a number both `shapeFan` and `draw_shapes`
            had to learn, on both sides, for no capability the two rows below do
            not already have.

            What it is instead: RESIZE BOTH TOGETHER, KEEPING THE ASPECT. It
            reads as a percentage of the size this kind of clip is CREATED at
            (25% of the frame for a shape, 30% for a picture — `geo` above),
            which is the same thing a frame's Scale means by 100%: "the size it
            came at". So a fresh clip reads 100%, and a clip stretched to 80%
            wide reads 320% because it is indeed 3.2× the size it was made.

            ⚠ NO ⏱ ON THIS ROW, deliberately. It writes `w` and `h`, and those
            two have their own — keyframe them. A ⏱ here would have to key two
            properties from one control, which is exactly the thing rule 1 in
            `PropGroup.jsx` forbids, and the timeline has no row to draw it on.
            It sits directly above the two rows it drives so that is legible. */}
        <PropRow
          label="Scale"
          title="Resize width and height together. 100% is the size it was created at."
          reset={() => {
            const keys = { ...(shape.keyframes || {}) };
            delete keys.w;
            delete keys.h;
            onChange(shape.id, { w: geo.w, h: geo.h, keyframes: keys });
          }}
          changed={shape.w !== geo.w || shape.h !== geo.h || keyed("w") || keyed("h")}
          resetTo="100%"
          info="A shortcut for the two rows below, not a property of its own — it scales Width and Height together and keeps their proportion. To animate a scale, keyframe Width and Height."
        >
          <NumField
            unit="%"
            step="5"
            min="1"
            value={scalePct}
            onChange={(e) => setScale(e.target.value)}
          />
        </PropRow>
        <PropRow
          label="Width"
          title="As a percentage of the frame's width"
          reset={() => resetProp("w", geo.w)}
          changed={shape.w !== geo.w || keyed("w")}
          resetTo={`${Math.round(geo.w * 100)}%`}
        >
          <NumField
            unit="%"
            step="1"
            min="2"
            value={pct(shape.w)}
            onChange={(e) => setPct("w", e.target.value, 0.02, 4)}
          />
          {kf && <KeyframeControls {...kf} prop="w" />}
        </PropRow>
        <PropRow
          label="Height"
          title="As a percentage of the frame's height"
          reset={() => resetProp("h", geo.h)}
          changed={shape.h !== geo.h || keyed("h")}
          resetTo={`${Math.round(geo.h * 100)}%`}
        >
          <NumField
            unit="%"
            step="1"
            min="2"
            value={pct(shape.h)}
            onChange={(e) => setPct("h", e.target.value, 0.02, 4)}
          />
          {kf && <KeyframeControls {...kf} prop="h" />}
        </PropRow>
        <PropSlider
          label="Opacity"
          min="0"
          max="1"
          step="0.05"
          value={shape.opacity ?? 1}
          readout={`${Math.round((shape.opacity ?? 1) * 100)}%`}
          kf={kf && <KeyframeControls {...kf} prop="opacity" />}
          reset={() => resetProp("opacity", 1)}
          changed={(shape.opacity ?? 1) !== 1 || keyed("opacity")}
          resetTo="100%"
          {...gesture}
          onChange={(e) => onChange(shape.id, { opacity: parseFloat(e.target.value) })}
        />
        <PropRow
          label="Rotation"
          title="Clockwise, in degrees"
          reset={() => resetProp("rotation", 0)}
          changed={(shape.rotation || 0) !== 0 || keyed("rotation")}
          resetTo="0°"
        >
          <NumField
            unit="°"
            step="5"
            min="-360"
            max="360"
            value={Math.round(shape.rotation || 0)}
            onChange={(e) =>
              onChange(shape.id, { rotation: clamp(parseFloat(e.target.value) || 0, -360, 360) })
            }
          />
          {kf && <KeyframeControls {...kf} prop="rotation" />}
        </PropRow>
      </PropGroup>

      {look}

      <div className="an-prop-actions">
        <button type="button" className="btn small ghost" onClick={() => onDuplicate(shape.id)}>
          <Icon name="copy" /> Duplicate
        </button>
        <button type="button" className="btn small danger-btn" onClick={() => onDelete(shape.id)}>
          <Icon name="close" /> Remove
        </button>
        <button type="button" className="btn small ghost" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
