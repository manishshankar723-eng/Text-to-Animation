// ProjectImportModal — bring somebody else's cut in from Premiere, Resolve, Avid.
//
// ⚠ TWO STEPS, AND NOTHING TOUCHES THE TIMELINE UNTIL THE SECOND ONE. "Read the
// file" asks the server what is in it; the answer is printed here — how many
// clips, on how many rows, what it had to assume, and which pictures did not
// arrive — and only then does "Add to timeline" hand those clips to the editor.
// An import that landed the moment a file was chosen would be a stranger's
// timeline dropped into somebody's film with no warning.
//
// ⚠ AND IT ADDS, IT DOES NOT REPLACE. The clips go onto NEW rows above what is
// already there, so nothing existing can be lost — and because the editor
// applies them in one write, Ctrl+Z takes the whole import back out.
//
// ⚠ THE MEDIA IS THE HARD HALF, AND THE ZIP IS THE EASY ROAD. A project file
// names files by a path on the machine that wrote it; a browser cannot read that
// path. So either the footage is attached too, or the user brings back a .zip
// exported from here, which already holds both. A clip whose file never arrives
// still lands — as a labelled colour card, so the cut is whole and the gap is
// visible.
//
// ⚠ A `.prproj` IS REFUSED FIRST AND GUESSED AT SECOND. Premiere's own save file
// has no published structure, so the server says no and names the route that
// always works (export a Final Cut Pro XML from Premiere). Only after reading
// that does this offer "Try to read it anyway" — and what comes back is badged a
// GUESS for as long as it is on screen. The order is the whole point: an
// experimental reader offered as a checkbox up front is one most people would
// tick without ever seeing the reliable door beside it.
//
// The surface is the editor's own `.modal-overlay` / `.card`, like Workspace and
// Export beside it.
import { useEffect, useRef, useState } from "react";

import * as api from "../api.js";

// What the file picker offers. ⚠ NOT a promise — `detect_format` on the server
// sniffs the BYTES, so a renamed file still works. `.prproj` is listed so the
// picker will SHOW one: it is refused on the first read whatever it is named,
// and the refusal is what offers the experimental route.
const DOC_ACCEPT = ".xml,.edl,.zip,.prproj";

export default function ProjectImportModal({ open, animaticId, busy, onClose, onApply }) {
  const [doc, setDoc] = useState(null);
  const [media, setMedia] = useState([]);
  const [read, setRead] = useState(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  // Whether the LAST read asked the server to guess at a .prproj. Kept so the
  // offer disappears once it has been taken — an unchanged button after a
  // failed second attempt reads as "nothing happened".
  const [guessed, setGuessed] = useState(false);
  const docRef = useRef(null);
  const mediaRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    setDoc(null);
    setMedia([]);
    setRead(null);
    setError("");
    setGuessed(false);
    const onKey = (e) => e.key === "Escape" && !reading && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // `reading`/`busy` are read inside the handler on purpose — re-binding the
    // listener on every keystroke of a long upload would be the only effect of
    // listing them, and the fields above must reset only when it OPENS.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  // A .zip carries its own media, so asking for more would be a control that
  // does nothing — the same reason the export dialog hides its path box.
  const isZip = Boolean(doc && /\.zip$/i.test(doc.name || ""));

  const pickDoc = (files) => {
    const file = (files || [])[0];
    if (!file) return;
    setDoc(file);
    // The report belongs to the file it was read from; keeping it after the
    // file changes is how somebody imports the wrong timeline.
    setRead(null);
    setError("");
    setGuessed(false);
  };

  const readFile = async (experimental = false) => {
    if (!doc || reading) return;
    setReading(true);
    setError("");
    setGuessed(experimental);
    try {
      setRead(
        await api.importProjectFile(animaticId, { document: doc, media, experimental })
      );
    } catch (e) {
      setRead(null);
      setError(e.message || "That file could not be read.");
    } finally {
      setReading(false);
    }
  };

  // ⚠ OFFERED OFF THE EXTENSION, not off the wording of the server's refusal.
  // The sentence the server sends is written for a person to read and will be
  // reworded; matching on it would break the button silently and nobody would
  // notice, because the failure looks exactly like a file that genuinely cannot
  // be read.
  const isPrproj = Boolean(doc && /\.prproj$/i.test(doc.name || ""));
  const offerGuess = Boolean(error && isPrproj && !guessed && !reading);
  // What came back IS a guess — badged for as long as it is on screen, not just
  // in the warnings list somebody may scroll past.
  const isGuess = read?.reader === "prproj";

  const rows = read
    ? [
        `${read.clips} clips on ${read.video_tracks} row${read.video_tracks === 1 ? "" : "s"}`,
        `${read.audio_clips} sounds on ${read.audio_lanes} row${
          read.audio_lanes === 1 ? "" : "s"
        }`,
        `${read.transitions_read} dissolve${read.transitions_read === 1 ? "" : "s"}`,
        `read at ${read.fps} fps`,
      ]
    : [];

  return (
    <div className="modal-overlay" onClick={() => !reading && !busy && onClose()}>
      <div className="card an-xchg-modal" onClick={(e) => e.stopPropagation()}>
        <button
          className="modal-close"
          onClick={() => !reading && !busy && onClose()}
          title="Close"
        >
          ✕
        </button>

        <h2>Import project file</h2>
        <p className="muted">
          A <strong>Final Cut Pro XML</strong> — what Premiere Pro, DaVinci
          Resolve and Avid all export — an <strong>EDL</strong>, or a{" "}
          <strong>.zip</strong> exported from here. The clips are{" "}
          <strong>added</strong> on new rows; nothing already on your timeline is
          touched.
        </p>

        <div className="an-xchg-pick">
          <button
            type="button"
            className="btn"
            onClick={() => docRef.current?.click()}
            disabled={reading || busy}
            title="Choose the .xml, .edl or .zip"
          >
            📄 Choose project file
          </button>
          <span className="tiny muted">{doc ? doc.name : "No file chosen"}</span>
          <input
            ref={docRef}
            type="file"
            accept={DOC_ACCEPT}
            hidden
            onChange={(e) => {
              pickDoc(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {/* ⚠ HIDDEN FOR A ZIP, which already carries every file the document
            names — see the note at the top. */}
        {doc && !isZip && (
          <div className="an-xchg-pick">
            <button
              type="button"
              className="btn ghost"
              onClick={() => mediaRef.current?.click()}
              disabled={reading || busy}
              title="The pictures, clips and sounds the file names. Without them, each clip comes in as a labelled colour card."
            >
              🎞 Add the footage
            </button>
            <span className="tiny muted">
              {media.length
                ? `${media.length} file${media.length === 1 ? "" : "s"}`
                : "Optional — without it, clips arrive as labelled gaps"}
            </span>
            <input
              ref={mediaRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                setMedia(Array.from(e.target.files || []));
                setRead(null);
                e.target.value = "";
              }}
            />
          </div>
        )}

        {read && (
          <>
            <div className="an-xchg-sum">
              <span title="What the sequence was called in the file">
                <strong>{read.name || "Untitled sequence"}</strong>
              </span>
              {/* ⚠ ON SCREEN, NOT ONLY IN THE WARNINGS LIST. This one came out
                  of a format nobody has documented; the badge is here so the
                  word "guess" is still visible at the moment somebody presses
                  "Add to the timeline". */}
              {isGuess && (
                <span
                  className="an-xchg-guess"
                  title="Read from a .prproj, Premiere's private save file. The clips and their places are a best guess — check them against Premiere. Exporting a Final Cut Pro XML from Premiere is the route that always works."
                >
                  best guess
                </span>
              )}
              {rows.map((row) => (
                <span key={row}>{row}</span>
              ))}
            </div>

            {/* Everything the reader had to ASSUME — an EDL's frame rate, an
                NTSC rate read as a whole number, dissolves read as cuts. */}
            {read.warnings.length > 0 && (
              <div className="an-xchg-loss">
                <span className="tiny">Worth knowing before you add it:</span>
                <ul>
                  {read.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* ⚠ NAMED, NOT COUNTED ONLY. "12 clips are missing" is a number;
                the file names are what somebody can go and find. */}
            {read.placeholders.length > 0 && (
              <div className="an-xchg-loss an-xchg-gone">
                <span className="tiny">
                  {read.placeholders.length} file
                  {read.placeholders.length === 1 ? "" : "s"} did not arrive — those
                  clips come in as labelled colour cards, in the right places:
                </span>
                <ul>
                  {read.placeholders.slice(0, 8).map((name, i) => (
                    <li key={`${name}-${i}`}>{name}</li>
                  ))}
                  {read.placeholders.length > 8 && (
                    <li>…and {read.placeholders.length - 8} more</li>
                  )}
                </ul>
              </div>
            )}

            {read.rejected.length > 0 && (
              <div className="an-xchg-loss an-xchg-gone">
                <span className="tiny">These files could not be stored:</span>
                <ul>
                  {read.rejected.slice(0, 6).map((name, i) => (
                    <li key={`${name}-${i}`}>{name}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {error && <p className="error">{error}</p>}

        {/* ⚠ ONLY AFTER THE REFUSAL HAS BEEN READ. The sentence above already
            names the route that always works; this is the answer for somebody
            who no longer has Premiere to export from, and it says what it is
            before it is pressed rather than after. */}
        {offerGuess && (
          <div className="an-xchg-loss">
            <span className="tiny">
              This can <strong>try</strong> to read the .prproj anyway. It is
              experimental: the clips, their lengths and their rows are a guess,
              and effects, titles, colour, speed and volume are not read at all.
            </span>
            <div className="an-xchg-actions" style={{ marginTop: "0.55rem" }}>
              <button
                className="btn ghost"
                onClick={() => readFile(true)}
                disabled={reading || busy}
                title="Open Premiere's own save file with the experimental reader. Check every clip afterwards."
              >
                Try to read it anyway
              </button>
            </div>
          </div>
        )}

        <footer className="an-xchg-foot">
          <span className="tiny muted">
            {read
              ? "Nothing has been added yet."
              : "Reading the file changes nothing on your timeline."}
          </span>
          <div className="an-xchg-actions">
            <button className="btn ghost" onClick={onClose} disabled={reading || busy}>
              Cancel
            </button>
            {read ? (
              <button className="btn primary" onClick={() => onApply(read)} disabled={busy}>
                {busy ? (
                  <>
                    <span className="spinner-inline" /> Adding…
                  </>
                ) : (
                  `Add ${read.clips + read.audio_clips} clips to the timeline`
                )}
              </button>
            ) : (
              <button
                className="btn primary"
                onClick={() => readFile()}
                disabled={!doc || reading}
              >
                {reading ? (
                  <>
                    <span className="spinner-inline" /> Reading…
                  </>
                ) : (
                  "Read the file"
                )}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
