// RegeneratePanelInline.jsx — redraw the shot you are looking at, without
// leaving the timeline you are cutting on.
//
// WHY THIS EXISTS. An animatic frame is a REFERENCE to a storyboard panel, not
// a copy of one, so redrawing the panel updates the animatic for free. Until
// this, taking advantage of that meant leaving the editor, finding the board,
// finding the shot, redrawing it, coming back, and reloading — by which point
// you had lost the thing you noticed. The whole feature is that round trip
// collapsed into the pane you were already in.
//
// FOUR RULES, and the first three are the 2026-08-09 "regenerating a picture
// that is already on screen" entry. Every one of them was broken at once, once,
// and the report was simply "I can't see any changes":
//
//   1. **A regenerate must actually redraw.** There is no resume flag here and
//      there must not be one — this is one image, drawn now.
//   2. **A redrawn image must get a NEW URL.** The server answers with the
//      FRAME, carrying a fresh `?v=<mtime>`, and `onRedrawn` hands it to the
//      editor to re-fetch. A path that survives a redraw is a picture that
//      never updates, because every image here is an authed blob cached by url.
//   3. **It must LOOK like it is working, over the old picture.** `.is-redrawing`
//      + `.redraw-veil` — the shared treatment `PanelSequenceStrip`,
//      `StoryboardBoard` and `JobDetail` all use. Blur, don't blank: the layout
//      must not jump and you have to see WHICH picture is being replaced.
//   4. **Editing the prompt is the point, not a bonus.** Regenerating the same
//      wording buys the same shot with a different seed, which is occasionally
//      what you want and usually is not. The description is right there, and
//      what is sent is what is in the box.
//
// ⚠ SPENDS QUOTA — one image per press. It is a single call rather than a batch,
// so there is no priced confirm dialog the way ✨ Animate has one; the button
// says what it costs in words and the work is a few seconds, not minutes.
//
// Presentational apart from its own draft text and busy flag: everything that
// touches the document goes back out through `onRedrawn`.

import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { PropGroup, PropRow, PropNote } from "./properties/PropGroup.jsx";

export default function RegeneratePanelInline({
  animaticId,
  frameId,
  // The clip's picture, so the veil has something to sit over. Without it this
  // renders the rows and no thumbnail, which is what an upload gets.
  url,
  // (frame) => void — the frame as the server now describes it, `url` carrying
  // its new `?v=`. THIS is what the editor re-fetches against; see rule 2.
  onRedrawn,
  // Told to the user rather than swallowed. The pane has no error banner of its
  // own on purpose — the editor has one, and two places to look is one too many.
  onError,
}) {
  const [panel, setPanel] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  // The wording as the SERVER last had it. What ↺ goes back to, and what
  // decides whether the description counts as edited — comparing against the
  // draft's own initial value would make every keystroke permanent.
  const savedRef = useRef(null);

  // Read the panel behind this clip. Free, and it also answers "can this be
  // re-drawn at all?", so nothing below has to guess.
  useEffect(() => {
    let alive = true;
    setPanel(null);
    setDraft(null);
    if (!animaticId || !frameId) return undefined;
    api
      .getFramePanel(animaticId, frameId)
      .then((info) => {
        if (!alive) return;
        setPanel(info);
        const fields = {
          description: info.description || "",
          camera: info.camera || "",
          location: info.location || "",
        };
        savedRef.current = fields;
        setDraft(fields);
      })
      .catch(() => {
        /* a shot whose board is unreachable simply shows no redraw rows */
      });
    return () => {
      alive = false;
    };
  }, [animaticId, frameId]);

  if (!panel || !panel.storyboard_id) return null;

  const saved = savedRef.current || { description: "", camera: "", location: "" };
  const edited = draft && draft.description !== saved.description;

  async function redraw() {
    if (busy || !panel.can_regenerate) return;
    setBusy(true);
    try {
      // Only what the user actually changed. Sending the unchanged wording back
      // would be harmless but it would also PERSIST it onto the board, which is
      // a write nobody asked for.
      const frame = await api.regenerateFramePanel(animaticId, frameId, {
        description: edited ? draft.description : undefined,
      });
      if (edited) savedRef.current = { ...saved, description: draft.description };
      onRedrawn?.(frame);
    } catch (e) {
      onError?.(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <PropGroup
      id="frame:panel"
      title="Storyboard shot"
      hint={`Shot ${(panel.index ?? 0) + 1} of “${panel.title}”`}
      defaultOpen={false}
    >
      {/* WHICH PICTURE IS BEING REPLACED. The veil goes over the thumbnail
          rather than over the pane, so the layout does not move and the thing
          you are watching is the thing that changes. Rule 3. */}
      {url && (
        <div className={`an-redraw-thumb ${busy ? "is-redrawing" : ""}`}>
          <img src={url} alt={`Shot ${(panel.index ?? 0) + 1}`} />
          {busy && (
            <span className="redraw-veil">
              <span className="spinner-inline" />
              <span className="tiny">Redrawing…</span>
            </span>
          )}
        </div>
      )}

      {!panel.can_regenerate ? (
        <PropNote>{panel.reason}</PropNote>
      ) : (
        <>
          <PropRow
            label="Prompt"
            title="What this shot is drawn from. Edit it and re-draw to change the picture."
            full
            reset={() => setDraft((d) => ({ ...d, description: saved.description }))}
            changed={Boolean(edited)}
            resetTo="the wording on the board"
          >
            <textarea
              className="an-prop-input an-redraw-prompt"
              rows={3}
              value={draft?.description || ""}
              disabled={busy}
              placeholder="Describe the shot…"
              onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
            />
          </PropRow>

          <PropRow full>
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={redraw}
              title={
                edited
                  ? "Re-draw this shot from the wording above — it is saved to the storyboard too"
                  : "Draw this shot again. Same wording, so you get a different take of the same shot."
              }
            >
              {busy ? "Redrawing…" : edited ? "↻ Re-draw with these words" : "↻ Re-draw this shot"}
            </button>
          </PropRow>

          {/* ⚠ NOT A DISCLAIMER — the thing people get wrong. The panel is
              shared, so this is an edit to the board, and every other animatic
              built from it shows the new drawing too. Better said once, here,
              than discovered. */}
          <PropNote>
            This re-draws the panel on the storyboard, so every clip that shows
            this shot changes with it. Costs one image.
          </PropNote>
        </>
      )}
    </PropGroup>
  );
}

// The lengths a shot can be blocked out at. ⚠ MIRRORS
// `panel_sequence.ALLOWED_DURATIONS` — the server refuses anything else, so a
// number here that isn't there is a button that 400s.
const DURATIONS = [2, 4, 6, 8, 10];
// What an animator blocks out per second, and therefore how many drawings a
// length costs. ⚠ Mirrors `panel_sequence.KEY_POSES_PER_SECOND`.
const POSES_PER_SECOND = 4;

/**
 * "MAKE THIS SHOT 2s LONGER" — re-block the key poses at a new length.
 *
 * ⚠ THE PRICE IS THE DIFFERENCE, NOT THE WHOLE SHOT, and that is the only
 * reason this is worth having. The server resumes: every drawing already on
 * disk is kept, the plan they were drawn from is handed to the planner as
 * fixed, and only the new tail is bought. So 4s → 6s costs eight drawings, and
 * — the part that matters more than the money — drawing 17 continues the motion
 * drawings 1–16 actually made. A plain re-plan at the bigger count would have
 * produced sixteen drawings describing a motion that never happened.
 *
 * Renders NOTHING for a shot that has no key poses. A held panel is not a
 * flipbook and there is nothing to lengthen; the clip's own Duration row is
 * what makes it stay on screen longer, and offering both here would be two
 * controls that look like the same thing and are not.
 *
 * @param onRelength  (seconds) => void. The editor owns what happens next: the
 *                    job runs on the BOARD, and when it finishes the run of
 *                    pose clips on the timeline has to be rebuilt. None of that
 *                    is this component's business.
 */
export function RelengthShotInline({ animaticId, frameId, onRelength, busy = false }) {
  const [seq, setSeq] = useState(null);

  useEffect(() => {
    let alive = true;
    setSeq(null);
    if (!animaticId || !frameId) return undefined;
    api
      .getFrameSequence(animaticId, frameId)
      .then((info) => alive && setSeq(info))
      .catch(() => {
        /* a clip with no board behind it simply shows no re-block rows */
      });
    // `busy` is in the deps so the strip re-reads when a run finishes — that is
    // when the pose count has actually changed.
  }, [animaticId, frameId, busy]);

  // No poses drawn = nothing to re-block. See the docstring.
  if (!seq || !seq.frames) return null;

  const current = seq.duration_seconds || Math.round(seq.planned / POSES_PER_SECOND);
  const longer = DURATIONS.filter((d) => d > current);

  return (
    <PropGroup
      id="frame:reblock"
      title="Shot length"
      hint={`${seq.frames} key pose${seq.frames === 1 ? "" : "s"}, blocked out for ${current}s`}
      defaultOpen={false}
    >
      {longer.length === 0 ? (
        <PropNote>
          This shot is already blocked out to the longest length there is ({current}s).
        </PropNote>
      ) : (
        <>
          <PropRow
            label="Run it longer"
            title="Draw more key poses so the shot carries on — the ones you have are kept"
          >
            <span className="an-relength">
              {longer.map((d) => (
                <button
                  key={d}
                  type="button"
                  className="btn small ghost"
                  disabled={busy}
                  onClick={() => onRelength?.(d)}
                  title={
                    `Carry this shot on to ${d}s — ${(d - current) * POSES_PER_SECOND} more ` +
                    `drawings. The ${seq.frames} you already have are kept and not paid for again.`
                  }
                >
                  {d}s
                  <span className="tiny muted"> +{(d - current) * POSES_PER_SECOND}</span>
                </button>
              ))}
            </span>
          </PropRow>
          <PropNote>
            {busy
              ? "Drawing the new poses… they appear on the timeline as they land."
              : "Only the new drawings are made — the poses you already have are kept, " +
                "and the motion carries on from where they leave off."}
          </PropNote>
        </>
      )}
    </PropGroup>
  );
}
