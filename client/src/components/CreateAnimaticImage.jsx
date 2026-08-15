// Image to Animatic Image — a workflow in its own right.
//
// (The file, component and nav id keep the older `CreateAnimaticImage` /
// `create-animatic-image` names on purpose: those are internal keys, and the
// nav id in particular must not change or it strands anyone mid-session.
// Renaming a workflow means changing its `label`, not its id.)
//
// Two states, like Storyboard to Animatics: pick one of your boards, then work
// on it. "Work on it" IS the storyboard board page — the same last page Script
// to Storyboard ends on, where panels are drawn, redrawn, restyled and
// exported. That is the whole point of this workflow: it is the shortest route
// to the panel images, without walking the script → breakdown → review steps
// again.
//
// THE LIBRARY HERE SHOWS THIS WORKFLOW'S OWN BOARDS, NOT EVERY STORYBOARD.
// "From a Storyboard" DEEP-COPIES the board you pick — its own job record and
// its own panel files — and that copy is what you then work on. Drawing,
// redrawing or restyling here therefore CANNOT change the storyboard it came
// from, which is the whole requirement: refine images for the animatic without
// disturbing the script's board. See `copy_storyboard` in server/main.py.
//
// The screens themselves are the shared components the storyboard workflow
// already uses — `StoryboardLibrary` (filtered to this workflow, with its
// creating actions off) and `StoryboardBoard` exactly as it is. Change either
// and both workflows change together, which is the point.
import { useState } from "react";
import * as api from "../api.js";
import StoryboardBoard, { styleLabelFor } from "./StoryboardBoard.jsx";
import StoryboardLibrary from "./StoryboardLibrary.jsx";

// Tag on every board this workflow owns. The server filters on it, so it has to
// match what the library and Home ask for.
const WORKFLOW = "animatic-image";

export default function CreateAnimaticImage({ onOpenAnimatic }) {
  // The board summary being worked on, or null for the library. Held whole (not
  // just the id) so the board gets its style and aspect with no second fetch.
  const [board, setBoard] = useState(null);
  // Source boards for the picker modal, or null when it's closed. These are
  // Script to Storyboard's originals — the things available to copy FROM.
  const [picking, setPicking] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  // Bumped after a copy so the library re-fetches and the new board appears.
  const [refreshKey, setRefreshKey] = useState(0);

  if (board) {
    return (
      <StoryboardBoard
        jobId={board.job_id}
        styleLabel={styleLabelFor(board.style)}
        aspect={board.aspect_ratio || "16:9"}
        backLabel="← Your Boards"
        onBack={() => setBoard(null)}
        onRestart={() => setBoard(null)}
        onOpenAnimatic={onOpenAnimatic}
        /* THE workflow: shots stacked in one column, each with a Generate
           button that blocks its motion out as key poses. */
        sequenceMode
      />
    );
  }

  // Show the ORIGINALS to copy from. They are a different set from the boards
  // in the library below, so this is its own fetch rather than the library's.
  async function openPicker() {
    setError("");
    try {
      setPicking(await api.listStoryboards());
    } catch (e) {
      setError(e.message);
    }
  }

  // Copy first, THEN open the copy. Opening the source directly is what would
  // let edits leak back into Script to Storyboard.
  async function copyAndOpen(source) {
    setBusyId(source.job_id);
    setError("");
    try {
      const copy = await api.copyStoryboard(source.job_id, WORKFLOW);
      setPicking(null);
      setRefreshKey((k) => k + 1);
      setBoard(copy);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      {error && <div className="error">{error}</div>}

      <StoryboardLibrary
        icon="🖼️"
        title="Image to Animatic Image"
        subtitle="Your own copies of a board, safe to redraw. Changes here never touch the original storyboard."
        workflow={WORKFLOW}
        refreshKey={refreshKey}
        onOpen={setBoard}
        /* Brings a board IN by copying it. No `onDuplicate`, because MAKING a
           board belongs to Script to Storyboard and a second front door to it
           would only confuse. */
        onNew={openPicker}
        newLabel="From a Storyboard"
        newHint={(n) =>
          n ? `${n} board${n === 1 ? "" : "s"} here` : "Copy a board to start"
        }
      />

      {picking && (
        <div className="modal-overlay" onClick={() => setPicking(null)}>
          <div
            className="card an-pick-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="modal-close"
              onClick={() => setPicking(null)}
            >
              ✕
            </button>
            <h2>Copy a storyboard</h2>
            <p className="muted">
              Takes a <strong>copy</strong> of the board — its own panels, its
              own record — and adds it here. Redraw and restyle it as much as
              you like:{" "}
              <strong>the original storyboard is never changed</strong>. Copying
              is free; only drawing a panel spends image credits.
            </p>
            {!picking.length && (
              <p className="muted">
                You haven't got a storyboard yet — make one in Script to
                Storyboard first.
              </p>
            )}
            <div className="an-pick-list">
              {picking.map((b) => (
                <button
                  key={b.job_id}
                  type="button"
                  className="an-pick-row"
                  disabled={busyId === b.job_id}
                  onClick={() => copyAndOpen(b)}
                >
                  <span className="an-pick-title">{b.title}</span>
                  <span className="muted">
                    {b.panel_count} panel{b.panel_count === 1 ? "" : "s"} ·{" "}
                    {b.aspect_ratio || "16:9"}
                  </span>
                  {busyId === b.job_id && <span className="spinner-inline" />}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
