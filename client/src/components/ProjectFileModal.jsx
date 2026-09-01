// ProjectFileModal — hand this cut to Premiere Pro, Resolve, Avid or Final Cut.
//
// ⚠ THE LIST OF WHAT WILL **NOT** COME ACROSS IS THE REASON THIS IS A DIALOG AND
// NOT A MENU ROW THAT DOWNLOADS. A project file carries the CUT — which clip,
// where, how long, on which track, which part of each video, the audio and its
// level — and no exchange format on earth carries this app's own look: the
// grades, the LUTs, the masks, the blend modes, the fourteen transition shapes,
// the text and the shape clips. A user who finds that out AFTER opening Premiere
// reports the export as broken. A user who is told here has a tool.
//
// So the dialog asks the server what the export WOULD be (`interchangePreview`,
// which costs nothing and touches no media) and prints both halves before the
// button is live.
//
// ⚠ AND MEDIA TRAVELS BY DEFAULT. The XML is a recipe, not the food: it names
// files, it holds no pixels. Downloading it alone lands in Premiere as a
// timeline of red "Media Offline" rectangles, so the default is the ZIP with a
// `media/` folder in it and "XML only" is the deliberate exception for someone
// whose footage is already on their own disk.
//
// The surface is the editor's own `.modal-overlay` / `.card`, the same as
// Workspace and Export beside it — a dialog drawn its own way reads as another
// app's control.
import { useEffect, useState } from "react";

import * as api from "../api.js";

// ⚠ THE THREE FORMATS, AND EACH ONE SAYS WHAT YOU DO WITH IT — not just its
// name. "FCP7 XML" means nothing to somebody who wants their film in Premiere;
// "Premiere Pro, Resolve, Avid, Final Cut" is the question they actually have.
// ⚠ TWIN of `FORMATS` in `interchange.py`: the ids have to match or the server
// folds an unknown one back to fcp7 and the user silently gets the wrong file.
export const FORMATS = [
  {
    id: "fcp7",
    label: "Premiere Pro, Resolve, Avid, Final Cut",
    ext: "xml",
    note: "The full cut — every track, the audio, and dissolves.",
  },
  {
    id: "aftereffects",
    label: "After Effects",
    ext: "jsx",
    // Said out loud because it is the one that looks wrong in the folder: AE
    // reads no exchange format at all, so this is a script it RUNS.
    note: "A script you run in AE (File \u203A Scripts \u203A Run Script File) — it builds the comp.",
  },
  {
    id: "edl",
    label: "EDL — opens almost anywhere",
    ext: "edl",
    note: "The safe fallback: one video track, cuts only, frame-exact.",
  },
];

// Rough, and said so: the number is the media BEFORE the zip compresses it,
// which is the honest direction to be wrong in.
function readableSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 * 1024 * 1024) return `${Math.round(n / (1024 * 1024))} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function readableLength(frames, fps) {
  const total = Math.round((Number(frames) || 0) / Math.max(1, Number(fps) || 24));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function ProjectFileModal({ open, animaticId, title, onClose }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [withMedia, setWithMedia] = useState(true);
  const [basePath, setBasePath] = useState("");
  // ⚠ NOT RESET WHEN THE DIALOG CLOSES. Somebody who works in After Effects
  // works in After Effects every time, and re-picking it on every export is the
  // kind of small friction that gets reported as "it keeps forgetting".
  const [format, setFormat] = useState("fcp7");
  const chosen = FORMATS.find((f) => f.id === format) || FORMATS[0];

  // Re-asked every time it opens, never cached: the answer is about the
  // timeline as it stands, and the timeline is what the user was just editing.
  // ⚠ `format` IS IN THE DEPENDENCIES AND THAT IS THE POINT OF THE DROPDOWN.
  // The losses are per format — an EDL holds ONE video track and no dissolves —
  // so picking one has to re-ask the server, not re-print the same list.
  useEffect(() => {
    if (!open || !animaticId) return undefined;
    let alive = true;
    setReport(null);
    setError("");
    api
      .interchangePreview(animaticId, format)
      .then((r) => alive && setReport(r))
      .catch((e) => alive && setError(e.message || "Could not read this project."));
    return () => {
      alive = false;
    };
  }, [open, animaticId, format]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const download = async () => {
    setBusy(true);
    setError("");
    try {
      await api.downloadProjectFile(animaticId, {
        format,
        media: withMedia,
        basePath: basePath.trim(),
        filename: `${title || "project"}.${withMedia ? "zip" : chosen.ext}`,
      });
      onClose();
    } catch (e) {
      setError(e.message || "The export failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="card an-xchg-modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={() => !busy && onClose()} title="Close">
          ✕
        </button>

        <h2>Export project file</h2>
        <p className="muted">
          Your <strong>cut</strong> travels — the clips, where they sit and how
          long they hold. The <strong>look</strong> does not.
        </p>

        {/* First, because everything under it depends on the answer. A native
            <select>, like every other menu in this editor. */}
        <label className="an-xchg-opt an-xchg-fmt">
          <span className="tiny muted">Open it in</span>
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            {FORMATS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
          <span className="tiny muted an-xchg-fmt-note">{chosen.note}</span>
        </label>

        {!report && !error && (
          <p className="tiny muted">
            <span className="spinner-inline" /> Reading your timeline…
          </p>
        )}

        {report && (
          <>
            <div className="an-xchg-sum">
              <span title="Picture clips, and the number of video tracks they sit on">
                <strong>{report.clips}</strong> clips on {report.video_tracks} video{" "}
                {report.video_tracks === 1 ? "track" : "tracks"}
              </span>
              <span title="Audio clips, and the number of audio tracks they sit on">
                <strong>{report.audio_clips}</strong> sounds on {report.audio_tracks} audio{" "}
                {report.audio_tracks === 1 ? "track" : "tracks"}
              </span>
              <span title="How long the exported timeline is">
                <strong>{readableLength(report.duration_frames, report.fps)}</strong> at{" "}
                {report.fps} fps
              </span>
              <span title="Every picture, clip and sound the timeline uses — before the zip compresses it">
                <strong>{report.files}</strong> media files · about{" "}
                {readableSize(report.media_bytes)}
              </span>
            </div>

            {/* ⚠ NAMED, NOT SUMMARISED. "Some effects won't transfer" is a
                sentence people ignore; "3 colour grades · 2 masks · 1 text clip"
                is a decision they can make. */}
            {report.dropped.length > 0 && (
              <div className="an-xchg-loss">
                <span className="tiny">Will NOT come across — redo these in the other editor:</span>
                <ul>
                  {report.dropped.map((row) => (
                    <li key={row.what}>
                      <b>{row.count}</b> {row.what}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {report.missing.length > 0 && (
              <div className="an-xchg-loss an-xchg-gone">
                <span className="tiny">
                  These clips have no file any more and are left out:
                </span>
                <ul>
                  {report.missing.slice(0, 8).map((name, i) => (
                    <li key={`${name}-${i}`}>{name}</li>
                  ))}
                  {report.missing.length > 8 && <li>…and {report.missing.length - 8} more</li>}
                </ul>
              </div>
            )}

            <label
              className="an-xchg-opt"
              title="The file only NAMES your pictures and clips — it holds none of them. Without this you get a timeline of offline clips."
            >
              <input
                type="checkbox"
                checked={withMedia}
                onChange={(e) => setWithMedia(e.target.checked)}
              />
              <span>
                Include the media <span className="tiny muted">(recommended)</span>
              </span>
            </label>

            {/* ⚠ ONLY THE PREMIERE XML LINKS BY PATH. The After Effects script
                finds the media folder sitting next to itself, and an EDL names
                reels rather than paths — so for those two this box would be a
                control that does nothing, which is worse than no control. */}
            {format === "fcp7" && (
              <label
                className="an-xchg-opt an-xchg-path"
                title="Optional. Type the folder you will unzip into and every clip links itself — otherwise Premiere asks you to locate the media once, and finding one file finds them all."
              >
                <span className="tiny muted">Unzip folder (optional)</span>
                <input
                  type="text"
                  value={basePath}
                  placeholder="D:\Films\My Project"
                  onChange={(e) => setBasePath(e.target.value)}
                />
              </label>
            )}
          </>
        )}

        {error && <p className="error">{error}</p>}

        <footer className="an-xchg-foot">
          <span className="tiny muted">
            {withMedia
              ? `A .zip — the .${chosen.ext} and a media folder.`
              : `One .${chosen.ext} file, no media.`}
          </span>
          <div className="an-xchg-actions">
            <button className="btn ghost" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button className="btn primary" onClick={download} disabled={busy || !report}>
              {busy ? (
                <>
                  <span className="spinner-inline" /> Building…
                </>
              ) : (
                "Download"
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
