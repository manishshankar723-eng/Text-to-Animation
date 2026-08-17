// VideoProperties.jsx — the pane shown when nothing is selected: the settings
// that apply to the whole video.
//
// Laid out with the primitives in `PropGroup.jsx`, like every other pane.

import { PropGroup, PropRow, PropNote } from "./PropGroup.jsx";

const ASPECTS = [
  { id: "16:9", label: "16:9", note: "Wide" },
  { id: "9:16", label: "9:16", note: "Reels" },
  { id: "1:1", label: "1:1", note: "Square" },
  { id: "4:3", label: "4:3", note: "Classic" },
  { id: "4:5", label: "4:5", note: "Portrait" },
];

export default function VideoProperties({ settings, onChange, sourceBoard }) {
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
        <PropRow label="Shape" title="The aspect ratio the video is exported at">
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

        <PropRow label="Images that don't fit" title="What happens to art of the wrong shape">
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

        <PropRow label="Bar colour" title="What shows behind an image that doesn't fill the frame">
          <input
            type="color"
            className="an-colour"
            value={settings.background}
            onChange={(e) => onChange({ background: e.target.value })}
          />
        </PropRow>
      </PropGroup>

      <PropGroup id="video:export" title="Export">
        <PropRow label="Frame rate" title="How many frames a second the video is encoded at">
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
            here.
          </PropNote>
        )}
      </PropGroup>
    </div>
  );
}
