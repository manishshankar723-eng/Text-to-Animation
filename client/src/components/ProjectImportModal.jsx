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
// ⚠ A `.prproj` GOES STRAIGHT TO THE BEST-EFFORT READER, AND THAT CHANGED.
// Premiere's own save file has no published structure, so the ROUTE still
// refuses an unflagged one — that is the API's answer and it stays. What this
// dialog no longer does is walk the user through the refusal: for months a
// `.prproj` meant a red panel, then a second button ("Try to read it anyway"),
// then a SECOND upload of the same folder of footage, before anything was on
// screen. Reported as exactly that — *"ye red text dikhne ka zaroori nahi hai
// user ko"*. So the flag goes on the FIRST request, because the extension is
// already known here.
// ⚠ NOTHING IS HIDDEN BY DOING THAT, and the difference matters: what came back
// is still badged **BEST GUESS** for as long as it is on screen, and the first
// line of `warnings` still says it was read out of a private format and names
// the route that always works (File › Export › Final Cut Pro XML). The warning
// moved to where the user actually is — reading the report — instead of
// standing between them and it.
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

// What the server will actually store, mirrored from `interchange.py`'s
// IMAGE_EXTS / VIDEO_EXTS / AUDIO_EXTS. ⚠ FILTERED HERE RATHER THAN UPLOADED AND
// REFUSED: "add a whole folder" otherwise sends the project file itself, every
// Premiere auto-save beside it and any stray document in the tree — megabytes
// the user waits for so the server can say no to them one at a time.
const MEDIA_RE =
  /\.(png|jpe?g|webp|bmp|gif|tiff?|mp4|mov|webm|m4v|avi|mkv|mp3|wav|m4a|aac|ogg|oga|flac)$/i;

export default function ProjectImportModal({
  open,
  animaticId,
  // ⚠ `animaticId` CAN BE NULL — reading a project file INTO a blank project is
  // one of the things that creates it. See `ensureProject` in AnimaticEditor.jsx.
  ensureId,
  busy,
  onClose,
  onApply,
}) {
  const [doc, setDoc] = useState(null);
  const [media, setMedia] = useState([]);
  const [read, setRead] = useState(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  // Whether the LAST read asked the server to guess at a .prproj. Kept so the
  // offer disappears once it has been taken — an unchanged button after a
  // failed second attempt reads as "nothing happened".
  const [guessed, setGuessed] = useState(false);
  // ⚠ THE REPORT SURVIVES ADDING FOOTAGE — IT JUST STOPS BEING CURRENT. Clearing
  // it was the obvious thing and it was wrong twice over. The report is the only
  // place the missing files and their FOLDERS are written, so wiping it at the
  // moment the user goes to fetch one of those folders takes away the very thing
  // they are acting on; and the footer flipped back to "Read the file", which for
  // a `.prproj` is the read the server REFUSES, with the experimental offer
  // already spent (`guessed`) and therefore not shown — a dead end with no way
  // back but closing the dialog. Reported exactly that way: *"mai wapas aa gaya …
  // fir kuch nhi hua to mai bas usko chhor kar import kiya"*.
  const [stale, setStale] = useState(false);
  const docRef = useRef(null);
  const mediaRef = useRef(null);
  const folderRef = useRef(null);
  // ⚠ THIS DIALOG DOES NOT CLOSE BY ACCIDENT. Reported from a long `.prproj`
  // import: a click that landed a few pixels outside the card took the dialog
  // away — with the read, the missing-file folders and the warnings in it — and
  // there is no way back to any of that but doing the whole thing again.
  // *"galti se mera mouse pop up se bahar click hua … mera mehnat bekar ho
  // gaya"*. So the backdrop closes nothing, Escape closes nothing, and ✕ /
  // Cancel are the only doors out (RULEBOOK E65 — now true of EVERY dialog in
  // the app, not just this one).
  //
  // ⚠ AND IT MOVES, BUT NOT FROM ANY CODE IN THIS FILE. `dialog_move.js`, wired
  // in once from `App.jsx`, makes the heading of every dialog a drag handle —
  // do not add a second implementation here. What IS local is the title bar
  // below: this dialog scrolls inside itself, so the bar is `sticky` and takes
  // the ✕ with it, or the only handle and the only exit both scroll out of
  // reach the moment somebody reads the report.

  useEffect(() => {
    if (!open) return undefined;
    setDoc(null);
    setMedia([]);
    setRead(null);
    setError("");
    setGuessed(false);
    setStale(false);
    return undefined;
    // ⚠ NO Escape LISTENER HERE ANY MORE, ON PURPOSE (see the header). Escape
    // is one keystroke away from the typing people do in this dialog, and it
    // threw away the same minutes of work the stray backdrop click did.
  }, [open]);

  if (!open) return null;

  // A .zip carries its own media, so asking for more would be a control that
  // does nothing — the same reason the export dialog hides its path box.
  const isZip = Boolean(doc && /\.zip$/i.test(doc.name || ""));
  // ⚠ OFF THE EXTENSION, NOT OFF THE WORDING OF THE SERVER'S REFUSAL. The
  // sentence the server sends is written for a person to read and will be
  // reworded; matching on it would break this silently, and the failure would
  // look exactly like a file that genuinely cannot be read.
  // ⚠ AND IT IS READ BEFORE THE FIRST REQUEST NOW, not after a refusal — see the
  // note at the top. One upload, no red panel, no second button.
  const isPrproj = Boolean(doc && /\.prproj$/i.test(doc.name || ""));

  const pickDoc = (files) => {
    const file = (files || [])[0];
    if (!file) return;
    setDoc(file);
    // The report belongs to the file it was read from; keeping it after the
    // file changes is how somebody imports the wrong timeline.
    setRead(null);
    setError("");
    setGuessed(false);
    setStale(false);
  };

  // ⚠ IT ADDS, IT DOES NOT REPLACE — and replacing is what it used to do. The
  // media for one project file is routinely spread over several folders
  // (`Images/`, `Videos/`, `Audio/`), and a file picker can only ever see inside
  // ONE folder at a time. So the second pick silently threw the first away, and
  // what the user saw was an import where "only the one image I chose came in".
  // ⚠ KEYED BY NAME, because that is what the server matches on: two files with
  // the same name are the same file whichever folder they were picked from, and
  // re-picking a folder should not double the upload.
  const addMedia = (files) => {
    let picked = Array.from(files || []).filter((f) => MEDIA_RE.test(f.name || ""));
    // ⚠ ONCE A REPORT NAMES WHAT IS MISSING, TAKE ONLY THAT. The second folder a
    // user is sent to fetch is somebody else's project folder — the one holding
    // the shared logo or the music bed — and it is full of OTHER films' media.
    // Reported as *"us folder mai aur bhi music tha"*. Uploading all of it is
    // minutes of waiting and a Media pane full of clips this cut never used.
    // ⚠ AND IT FALLS BACK TO EVERYTHING IF NOTHING MATCHES, because a user
    // adding a folder the report does not name is adding footage for a read that
    // has not happened yet, and silently taking none of it would be the worse
    // failure by far.
    const wanted = new Set(
      (read?.missing || []).map((m) => (m.name || "").toLowerCase())
    );
    if (wanted.size) {
      const onlyWanted = picked.filter((f) =>
        wanted.has((f.name || "").toLowerCase())
      );
      if (onlyWanted.length) picked = onlyWanted;
    }
    if (!picked.length) return;
    setMedia((prev) => {
      const byName = new Map(prev.map((f) => [f.name.toLowerCase(), f]));
      for (const file of picked) byName.set(file.name.toLowerCase(), file);
      return Array.from(byName.values());
    });
    // ⚠ THE REPORT IS KEPT AND MARKED OUT OF DATE, NOT THROWN AWAY — see `stale`.
    // It belongs to the files it was read with, so it must not be ADDED now; but
    // it is also the only place the folders still to fetch are written down.
    setStale(true);
  };

  const readFile = async (experimental = false) => {
    if (!doc || reading) return;
    setReading(true);
    setError("");
    // ⚠ THE FLAG GOES ON THE FIRST TRY FOR A `.prproj`. Reading it strictly first
    // and retrying on the refusal is the same two round trips the user used to
    // make by hand — and each one re-uploads the whole folder of footage, which
    // for the project this was reported from is 27 files. One upload.
    const wantGuess = experimental || isPrproj;
    setGuessed(wantGuess);
    try {
      setRead(
        await api.importProjectFile(animaticId || (await ensureId?.()), {
          document: doc,
          media,
          experimental: wantGuess,
        })
      );
      setStale(false);
    } catch (e) {
      setRead(null);
      setError(e.message || "That file could not be read.");
      // ⚠ A BACKEND THAT NEVER ANSWERED IS NOT A REFUSAL, so the guess was not
      // actually spent — seen live, when uvicorn's `--reload` happened to be
      // restarting. The offer this used to un-hide is gone, but `guessed` still
      // decides what the footer re-reads with, and a `.prproj` that never
      // reached the server must not leave it stuck on a state no read produced.
      if (e?.offline) setGuessed(false);
    } finally {
      setReading(false);
    }
  };

  // What came back IS a guess — badged for as long as it is on screen, not just
  // in the warnings list somebody may scroll past. ⚠ THIS BADGE IS NOW THE ONLY
  // PLACE THE WORD "GUESS" APPEARS BEFORE THE REPORT IS READ, since the refusal
  // panel that used to precede it is gone (see the header). It does not get to
  // be subtle.
  const isGuess = read?.reader === "prproj";

  // ⚠ THE TITLES GET A LINE OF THEIR OWN, and it is only drawn when there ARE
  // some. Users were told for months that Premiere lettering could not come
  // across — an import that now brings forty captions in and says nothing about
  // them leaves that belief in place, and the row they landed on unlooked-for.
  const rows = read
    ? [
        `${read.clips} clips on ${read.video_tracks} row${read.video_tracks === 1 ? "" : "s"}`,
        `${read.audio_clips} sounds on ${read.audio_lanes} row${
          read.audio_lanes === 1 ? "" : "s"
        }`,
        ...(read.texts_read
          ? [
              `${read.texts_read} title${read.texts_read === 1 ? "" : "s"} on ` +
                `${read.text_lanes} text row${read.text_lanes === 1 ? "" : "s"}`,
            ]
          : []),
        ...(read.shapes_read
          ? [`${read.shapes_read} shape${read.shapes_read === 1 ? "" : "s"}`]
          : []),
        `${read.transitions_read} dissolve${read.transitions_read === 1 ? "" : "s"}`,
        `read at ${read.fps} fps`,
      ]
    : [];

  // ⚠ THE NAME OF A MISSING FILE IS NOT ACTIONABLE ON ITS OWN — THE FOLDER IS.
  // A real import lost three files out of twenty-eight: the voiceover was inside
  // the project folder and arrived, while the music bed and the logo lived in
  // ANOTHER project's folder, which is normal (a logo and a music bed are reused
  // across a whole series). The user attached the only folder there was any
  // reason to attach and read "that .mp3 did not arrive" — which says nothing
  // about what to do, and reads as this app being unable to take music at all.
  // The server sends `missing`, one row per FILE with the folder the editor
  // itself recorded, so the dialog can name the folders to add.
  // ⚠ FALLS BACK TO `placeholders`, which is per CLIP and has no paths — an
  // older server, or a format that carried no path. Deduped, or a logo used
  // twice prints twice and reads as two broken files.
  const missing = read?.missing?.length
    ? read.missing
    : Array.from(new Set(read?.placeholders || [])).map((name) => ({
        name,
        folder: "",
        kind: "picture",
        clips: 1,
      }));
  // Grouped by folder, in the order the server sent them (sounds first).
  const missingByFolder = [];
  for (const item of missing) {
    const group = missingByFolder.find((g) => g.folder === item.folder);
    if (group) group.items.push(item);
    else missingByFolder.push({ folder: item.folder, items: [item] });
  }
  // ⚠ "ADD THESE FOLDERS" IS ONLY HONEST WHEN THERE ARE SOME. A Premiere Graphic
  // has no file and never will, and an EDL carries no paths at all — sending
  // somebody to attach a folder that was never named is the same dead end this
  // whole change is about, pointing the other way.
  const missingFolders = missingByFolder.filter((g) => g.folder);

  // ⚠ NO `onClick` ON THE BACKDROP — see the header, and RULEBOOK E65.
  return (
    <div className="modal-overlay">
      <div className="card an-xchg-modal">
        {/* ⚠ THE TITLE BAR STICKS TO THE TOP OF THE CARD, AND THE ✕ RIDES IN IT.
            This dialog scrolls — the report can run to a dozen folders — and a
            ✕ positioned against the card scrolls away with the content, which
            on a dialog that no longer closes on the backdrop would leave no
            exit on screen at all. It is also the drag handle, for the same
            reason: it has to be reachable from wherever the user has read to. */}
        <div className="an-xchg-bar">
          <h2 title="Drag to move this window">Import project file</h2>
          <button
            className="modal-close"
            onClick={() => !reading && !busy && onClose()}
            title="Close"
          >
            ✕
          </button>
        </div>
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
            {/* ⚠ ONE CLICK PER FOLDER, not one per file. The media for a real
                project is spread across `Images/`, `Videos/` and `Audio/`, and
                picking the folder ABOVE those takes all three at once. Both
                pickers add to the same list. */}
            <button
              type="button"
              className="btn ghost"
              onClick={() => folderRef.current?.click()}
              disabled={reading || busy}
              title="Choose a whole folder — everything inside it, including its sub-folders. Only pictures, clips and sounds are taken."
            >
              📁 …or a whole folder
            </button>
            <span className="tiny muted">
              {media.length
                ? `${media.length} file${media.length === 1 ? "" : "s"} — choose again to add another folder`
                : "Optional — without it, clips arrive as labelled gaps"}
            </span>
            {media.length > 0 && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => {
                  setMedia([]);
                  setStale(true);
                }}
                disabled={reading || busy}
                title="Forget the footage chosen so far and start again"
              >
                Clear
              </button>
            )}
            <input
              ref={mediaRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                addMedia(e.target.files);
                e.target.value = "";
              }}
            />
            <input
              ref={folderRef}
              type="file"
              multiple
              hidden
              webkitdirectory=""
              directory=""
              onChange={(e) => {
                addMedia(e.target.files);
                e.target.value = "";
              }}
            />
          </div>
        )}

        {read && (
          <>
            {/* ⚠ THE REPORT STAYS ON SCREEN WHILE IT IS BEING ACTED ON. The user
                is here because it named a folder; taking it away the moment they
                fetch that folder is taking away the instruction. It is dimmed and
                labelled instead, and the footer will not ADD it until it has been
                read again. */}
            {stale && (
              <div className="an-xchg-loss an-xchg-again">
                <span className="tiny">
                  Footage added. Everything below is from the <strong>last</strong>{" "}
                  read — press <strong>Read the file again</strong> to use it.
                </span>
              </div>
            )}
            <div className={stale ? "an-xchg-sum an-xchg-old" : "an-xchg-sum"}>
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

            {/* ⚠ NAMED AND LOCATED, NOT COUNTED ONLY. "12 clips are missing"
                is a number; the file names are what somebody can go and find,
                and the FOLDER is what tells them where to look. Both pickers add
                to the same list, so the fix is one more click per folder. */}
            {missing.length > 0 && (
              <div className="an-xchg-loss an-xchg-gone">
                <span
                  className="tiny"
                  title={
                    "A project often points at files kept outside its own folder — a logo or a " +
                    "music bed shared across a series. Press “…or a whole folder” once for each " +
                    "folder listed here (it adds to what you already chose), then read the file " +
                    "again. A picture that never arrives comes in as a labelled colour card; a " +
                    "sound cannot, so it is left out altogether."
                  }
                >
                  {missing.length} file{missing.length === 1 ? "" : "s"} did not
                  arrive
                  {missingFolders.length
                    ? ` — add ${
                        missingFolders.length === 1 ? "this folder" : "these folders"
                      } too:`
                    : ":"}
                </span>
                {missingByFolder.slice(0, 4).map((group) => (
                  <div className="an-xchg-where" key={group.folder || "_nowhere"}>
                    <span className="tiny muted" title={group.folder || undefined}>
                      {group.folder || "The file does not say where these lived"}
                    </span>
                    <ul>
                      {group.items.slice(0, 8).map((item) => (
                        <li key={item.name}>
                          {item.kind === "sound" ? "🔊 " : ""}
                          {item.name}
                          {item.clips > 1 ? ` ×${item.clips}` : ""}
                        </li>
                      ))}
                      {group.items.length > 8 && (
                        <li>…and {group.items.length - 8} more</li>
                      )}
                    </ul>
                  </div>
                ))}
                {missingByFolder.length > 4 && (
                  <span className="tiny muted">
                    …and {missingByFolder.length - 4} more folder
                    {missingByFolder.length - 4 === 1 ? "" : "s"}
                  </span>
                )}
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

        {/* ⚠ THE "Try to read it anyway" PANEL USED TO LIVE HERE AND IS GONE ON
            PURPOSE — see the header. A `.prproj` is now read with the best-effort
            reader on the first press, so there is no refusal to answer and no
            second button to find. What replaced its job is the BEST GUESS badge
            on the report and the first line of `warnings`, both of which say
            more than this panel did and say it where the user is looking. */}

        <footer className="an-xchg-foot">
          <span className="tiny muted">
            {read && !stale
              ? "Nothing has been added yet."
              : "Reading the file changes nothing on your timeline."}
          </span>
          <div className="an-xchg-actions">
            <button className="btn ghost" onClick={onClose} disabled={reading || busy}>
              Cancel
            </button>
            {read && !stale ? (
              <button className="btn primary" onClick={() => onApply(read)} disabled={busy}>
                {busy ? (
                  <>
                    <span className="spinner-inline" /> Adding…
                  </>
                ) : (
                  // Titles are counted in — a Premiere sequence whose picture is
                  // all offline but whose forty captions read is not "0 clips".
                  `Add ${read.clips + read.audio_clips + (read.texts_read || 0)} clips to the timeline`
                )}
              </button>
            ) : (
              // ⚠ IT RE-READS THE WAY IT READ LAST TIME. `readFile()` with no
              // argument asks for the STRICT read, which for a `.prproj` is the
              // one the server refuses — so a user who had already taken the
              // experimental route and then attached a second folder was thrown
              // back to a refusal, with the "Try to read it anyway" offer already
              // spent and hidden. Carrying `guessed` is what makes the second
              // read do what the first one did.
              <button
                className="btn primary"
                onClick={() => readFile(guessed)}
                disabled={!doc || reading}
              >
                {reading ? (
                  <>
                    <span className="spinner-inline" /> Reading…
                  </>
                ) : stale ? (
                  "Read the file again"
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
