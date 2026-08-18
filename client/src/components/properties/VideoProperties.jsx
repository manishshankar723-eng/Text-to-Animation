// VideoProperties.jsx — the pane shown when nothing is selected: the settings
// that apply to the whole video.
//
// Laid out with the primitives in `PropGroup.jsx`, like every other pane.

import { ASPECTS } from "../../animatic/aspects.js";
import { PropGroup, PropRow, PropNote } from "./PropGroup.jsx";

// What a new animatic is set up as, and therefore what every ↺ here goes back
// to. ⚠ Mirrors the field defaults on `AnimaticSettings`; a reset landing
// somewhere the project was never created is worse than no reset at all.
const SETTINGS_DEFAULTS = {
  aspect_ratio: "16:9",
  fit: "contain",
  background: "#000000",
  fps: 24,
  show_labels: false,
};

export default function VideoProperties({ settings, onChange, sourceBoard, reframe }) {
  // Is this setting away from what a new animatic carries?
  const off = (field) =>
    (settings[field] ?? SETTINGS_DEFAULTS[field]) !== SETTINGS_DEFAULTS[field];
  const back = (field) => () => onChange({ [field]: SETTINGS_DEFAULTS[field] });
  return (
    <div className="an-props">
      <div className="an-prop-ident">
        <div className="an-prop-ident-text">
          <span className="an-prop-kind">Whole video</span>
          <span className="an-prop-sub">
            Nothing selected — click a frame or a text clip to edit just that.
          </span>
        </div>
      </div>

      <PropGroup id="video:frame" title="Frame">
        {/* The SAME field the Program pane's picker writes — that one is the
            one you can reach with a clip selected, this one is here because
            everything else about the frame is. Neither is a copy of the other:
            one project, one `aspect_ratio`. */}
        <PropRow
          label="Shape"
          title="The aspect ratio the video is exported at"
          reset={back("aspect_ratio")}
          changed={off("aspect_ratio")}
          resetTo="16:9"
        >
          <span className="an-set-chips">
            {ASPECTS.map((a) => (
              <button
                key={a.id}
                type="button"
                className={`opt-chip ${settings.aspect_ratio === a.id ? "active" : ""}`}
                onClick={() => onChange({ aspect_ratio: a.id })}
              >
                {a.label}
                <span className="opt-chip-note">{a.note}</span>
              </button>
            ))}
          </span>
        </PropRow>

        {/* AUTO-REFRAME, directly under the shape it re-frames FOR. This is the
            moment it is wanted: you have just switched a board drawn wide over
            to Reels, every shot is now a letterboxed strip in the middle of a
            tall frame, and the question "so where do I put the camera on each
            of these forty" is the next thing you think. Slotted in from the
            editor because it talks to the server and this file does not. */}
        {reframe}

        <PropRow
          label="Images that don't fit"
          title="What happens to art of the wrong shape"
          reset={back("fit")}
          changed={off("fit")}
          resetTo="fit the whole image"
        >
          <span className="an-set-chips">
            <button
              type="button"
              className={`opt-chip ${settings.fit === "contain" ? "active" : ""}`}
              onClick={() => onChange({ fit: "contain" })}
            >
              Fit whole image
              <span className="opt-chip-note">bars at the edges</span>
            </button>
            <button
              type="button"
              className={`opt-chip ${settings.fit === "cover" ? "active" : ""}`}
              onClick={() => onChange({ fit: "cover" })}
            >
              Fill the frame
              <span className="opt-chip-note">crops the edges</span>
            </button>
          </span>
        </PropRow>

        <PropRow
          label="Bar colour"
          title="What shows behind an image that doesn't fill the frame"
          reset={back("background")}
          changed={off("background")}
          resetTo="black"
        >
          <input
            type="color"
            className="an-colour"
            value={settings.background}
            onChange={(e) => onChange({ background: e.target.value })}
          />
        </PropRow>
      </PropGroup>

      <PropGroup id="video:export" title="Export">
        <PropRow
          label="Frame rate"
          title="How many frames a second the video is encoded at"
          reset={back("fps")}
          changed={off("fps")}
          resetTo="24 fps"
        >
          <select
            className="an-select"
            value={settings.fps}
            onChange={(e) => onChange({ fps: Number(e.target.value) })}
          >
            <option value={12}>12 fps</option>
            <option value={24}>24 fps (film)</option>
            <option value={25}>25 fps</option>
            <option value={30}>30 fps</option>
          </select>
        </PropRow>

        <PropRow full>
          <label className="an-tp-check">
            <input
              type="checkbox"
              checked={settings.show_labels}
              onChange={(e) => onChange({ show_labels: e.target.checked })}
            />
            <span>Burn shot labels into the video</span>
          </label>
        </PropRow>

        {sourceBoard && (
          <PropNote>
            Frames come from a storyboard — re-draw a panel there and it updates
            here. Or select a shot and re-draw it from its own pane.
          </PropNote>
        )}
      </PropGroup>
    </div>
  );
}
