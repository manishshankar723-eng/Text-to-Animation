// KeyframeControls.jsx — the ⏱ that turns a property into an animation.
//
// One row of controls, sitting at the end of a property's existing row in the
// Properties pane. Deliberately Premiere's grammar, because it is the one every
// editor already knows:
//
//   ⏱        stopwatch — animate this property / stop animating it
//   ‹ ◆ ›    jump to the previous key · add or remove a key HERE · next key
//   curve    how the value travels from this key to the next
//
// The rest follows from that: while a property is animated, typing a value sets
// a key at the playhead instead of changing the value everywhere. That is the
// behaviour that makes an inspector feel like an animation tool rather than a
// form, and it lives in `AnimaticEditor`'s change handler, not here.
//
// This file only renders and reports intent. Every operation on the data is in
// `../animatic/keyframes.js`, so the two can be reasoned about separately.
import { EASINGS } from "../animatic/scene.js";
import { keyAt, keysOf, neighbourKey } from "../animatic/keyframes.js";

const EASE_LABEL = {
  linear: "Even",
  hold: "Hold",
  "ease-in": "Ease in",
  "ease-out": "Ease out",
  "ease-in-out": "Ease both",
};

/**
 * @param clip      the frame / text / shape / overlay being inspected
 * @param prop      which property this row is for ("scale", "opacity", …)
 * @param tRel      the playhead, in ms from the CLIP's start — never absolute
 * @param onToggle  (prop) => void — stopwatch pressed
 * @param onKey     (prop, add: boolean) => void — diamond pressed
 * @param onSeekKey (prop, dir) => void — ‹ or › pressed
 * @param onEase    (prop, ease) => void — curve changed
 */
export default function KeyframeControls({
  clip,
  prop,
  tRel,
  onToggle,
  onKey,
  onSeekKey,
  onEase,
}) {
  const keys = keysOf(clip, prop);
  const animated = keys.length > 0;
  const here = keyAt(clip, prop, tRel);
  const prev = neighbourKey(clip, prop, tRel, -1);
  const next = neighbourKey(clip, prop, tRel, 1);

  return (
    <span className={`an-kf ${animated ? "on" : ""}`}>
      {/* The legend, and the only one there is. The timeline draws one ROW of
          diamonds per animated property, in this pane's own order — so the
          swatch here is what says which row down there is this control. It
          appears only once the property is animated, because until then there
          is no row for it to name. Colours live in `--kf-*` (styles.css) and
          are shared with `.tl-key`; changing one without the other breaks the
          pairing silently, since neither side errors. */}
      {animated && <span className={`an-kf-swatch k-${prop}`} aria-hidden="true" />}
      <button
        type="button"
        className={`an-kf-btn an-kf-watch ${animated ? "on" : ""}`}
        onClick={() => onToggle(prop)}
        title={
          animated
            ? "Stop animating this — it keeps the value that's on screen now"
            : "Animate this: adds a key at the playhead, then move the playhead and change the value"
        }
        aria-pressed={animated}
      >
        ⏱
      </button>

      {/* The navigation only exists once there is something to navigate. Three
          dead buttons on every property row would be noise on a pane that is
          already dense. */}
      {animated && (
        <>
          <button
            type="button"
            className="an-kf-btn"
            disabled={!prev}
            onClick={() => onSeekKey(prop, -1)}
            title="Previous key"
          >
            ‹
          </button>
          <button
            type="button"
            className={`an-kf-btn an-kf-dot ${here ? "on" : ""}`}
            onClick={() => onKey(prop, !here)}
            title={here ? "Remove the key here" : "Add a key at the playhead"}
          >
            {here ? "◆" : "◇"}
          </button>
          <button
            type="button"
            className="an-kf-btn"
            disabled={!next}
            onClick={() => onSeekKey(prop, 1)}
            title="Next key"
          >
            ›
          </button>
          {/* The curve belongs to the key the playhead is ON, and shapes the
              span that FOLLOWS it — so it is only offered when you are standing
              on a key, and never on the last one, where it would do nothing. */}
          {here && next && (
            <select
              className="an-kf-ease"
              value={here.ease || "linear"}
              onChange={(e) => onEase(prop, e.target.value)}
              title="How the value travels from this key to the next"
            >
              {EASINGS.map((e) => (
                <option key={e} value={e}>
                  {EASE_LABEL[e] || e}
                </option>
              ))}
            </select>
          )}
        </>
      )}
    </span>
  );
}
