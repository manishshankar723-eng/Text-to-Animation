// Step 1 — Apply final art & characters.
//
// Veo takes up to three "reference" stills alongside the starting frame, and
// they are what keep a character looking like the same person from shot to
// shot. This step builds the tray of those stills and says which shots use
// which — nothing here spends anything.
//
// The tray can be filled two ways:
//   • Upload  — final art, a colour key, a character sheet from elsewhere.
//   • From a character run — one view of one part of a Text-to-Image job. This
//     is the reuse that makes the whole pipeline pay off: the character is
//     already drawn, so it doesn't have to be described in words and guessed
//     at again per shot.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";

// Veo's own ceiling. Picking a fourth has to replace something, not silently
// drop it at render time.
const MAX_REFS = 3;

// Views a character run produces, in the order the asset sheets use them.
const VIEWS = ["front", "left", "three_quarter", "back"];

function newId() {
  return Math.random().toString(16).slice(2, 14);
}

export default function FinalVideoArtStep({
  project,
  media,
  patch,
  patchShot,
  onError,
  onNext
}) {
  const [picking, setPicking] = useState(false);
  const [characterJobs, setCharacterJobs] = useState([]);
  const [assets, setAssets] = useState(null); // {job, parts} of the open run
  const [uploading, setUploading] = useState(false);
  // Which shot's reference row is open. Null = the "all shots" summary view.
  const [openShot, setOpenShot] = useState(null);
  const fileInput = useRef(null);

  // Thumbnails for the tray and the shot strip.
  useEffect(() => {
    for (const ref of project.art) media.load(ref.url);
    for (const shot of project.shots) media.load(shot.image_url);
  }, [project.art, project.shots, media]);

  async function openPicker() {
    setPicking(true);
    if (characterJobs.length) return;
    try {
      const jobs = await api.listJobs(["generate"]);
      setCharacterJobs(jobs.filter((j) => j.status === "succeeded"));
    } catch (e) {
      onError(e.message);
    }
  }

  async function openRun(job) {
    try {
      const detail = await api.getAssets(job.job_id);
      setAssets({ job, parts: detail.parts || {} });
    } catch (e) {
      onError(e.message);
    }
  }

  async function upload(files) {
    if (!files?.length) return;
    setUploading(true);
    onError("");
    try {
      const refs = await api.uploadFinalArt(project.job_id, Array.from(files));
      if (!refs.length) {
        onError("Nothing was added — those files couldn't be read as images.");
      }
      patch({ art: [...project.art, ...refs] });
      for (const ref of refs) media.load(ref.url);
    } catch (e) {
      onError(e.message);
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function addAssetRef(job, part, view) {
    const ref = {
      id: newId(),
      kind: "asset",
      name: `${job.character_name} · ${part}`,
      asset_job_id: job.job_id,
      part,
      view,
      url: null
    };
    // The serve URL is by ref id, so it can only answer once the project has
    // been saved with this ref on it — the thumbnail fills in a moment later.
    patch({ art: [...project.art, ref] });
    setPicking(false);
    setAssets(null);
  }

  function removeRef(refId) {
    patch({
      art: project.art.filter((a) => a.id !== refId),
      // A reference that no longer exists must not linger on a shot, or the
      // render would quietly go out with fewer ingredients than the UI shows.
      shots: project.shots.map((s) => ({
        ...s,
        reference_ids: (s.reference_ids || []).filter((id) => id !== refId),
        last_frame_ref_id:
          s.last_frame_ref_id === refId ? null : s.last_frame_ref_id
      }))
    });
  }

  function toggleRefOnShot(shot, refId) {
    const current = shot.reference_ids || [];
    if (current.includes(refId)) {
      patchShot(shot.id, {
        reference_ids: current.filter((id) => id !== refId)
      });
    } else if (current.length >= MAX_REFS) {
      onError(`A shot can carry ${MAX_REFS} references — remove one first.`);
    } else {
      onError("");
      patchShot(shot.id, { reference_ids: [...current, refId] });
    }
  }

  // The button that makes this step worth having: one cast, applied everywhere.
  function applyToAll(refId) {
    patch({
      shots: project.shots.map((s) => {
        const current = s.reference_ids || [];
        if (current.includes(refId) || current.length >= MAX_REFS) return s;
        return { ...s, reference_ids: [...current, refId] };
      })
    });
  }

  const artById = Object.fromEntries(project.art.map((a) => [a.id, a]));

  return (
    <div className="fv-step-pane">
      <div className="fv-pane-head">
        <div>
          <h2>Final art &amp; characters</h2>
          <p className="muted">
            Add the stills that lock how things look, then give each shot up to{" "}
            {MAX_REFS} of them. These ride along with every render, which is
            what keeps a character the same person from shot to shot. Free —
            nothing is rendered on this step.
          </p>
        </div>
        <button type="button" className="btn" onClick={onNext}>
          Next: render shots →
        </button>
      </div>

      {/* --- The tray ----------------------------------------------------- */}
      <section className="fv-section">
        <div className="fv-section-head">
          <h3>Art tray</h3>
          <div className="fv-section-actions">
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={(e) => upload(e.target.files)}
            />
            <button
              type="button"
              className="btn small"
              disabled={uploading}
              onClick={() => fileInput.current?.click()}
            >
              {uploading ? "Uploading…" : "＋ Upload art"}
            </button>
            <button
              type="button"
              className="btn small ghost"
              onClick={openPicker}
            >
              🎭 From a character run
            </button>
          </div>
        </div>

        {project.art.length === 0 ? (
          <p className="muted fv-empty">
            The tray is empty. Shots will still render — Veo just won't be told
            what your characters look like, so expect them to drift between
            shots.
          </p>
        ) : (
          <div className="fv-art-tray">
            {project.art.map((ref) => (
              <div className="fv-art" key={ref.id}>
                <div className="fv-art-thumb">
                  {media.urls[ref.url] ? (
                    <img src={media.urls[ref.url]} alt={ref.name} />
                  ) : (
                    <span className="fv-art-placeholder">🖼</span>
                  )}
                </div>
                <span className="fv-art-name" title={ref.name}>
                  {ref.name || "Untitled"}
                </span>
                <div className="fv-art-actions">
                  <button
                    type="button"
                    className="btn small ghost"
                    title="Add this reference to every shot that has room"
                    onClick={() => applyToAll(ref.id)}
                  >
                    All shots
                  </button>
                  <button
                    type="button"
                    className="lib-icon danger"
                    title="Remove from the tray"
                    onClick={() => removeRef(ref.id)}
                  >
                    <Icon name="trash" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* --- Per-shot assignment ------------------------------------------ */}
      <section className="fv-section">
        <div className="fv-section-head">
          <h3>Which shots use what</h3>
          <span className="tiny muted">
            {project.shots.length} shot{project.shots.length === 1 ? "" : "s"}
          </span>
        </div>

        {project.shots.length === 0 ? (
          <p className="muted fv-empty">
            This project has no shots yet. Start one from the Video Editor to fill
            them in automatically.
          </p>
        ) : (
          <div className="fv-shot-strip">
            {project.shots.map((shot, i) => {
              const refs = shot.reference_ids || [];
              const open = openShot === shot.id;
              return (
                <div
                  className={`card fv-shot-mini ${open ? "open" : ""}`}
                  key={shot.id}
                >
                  <button
                    type="button"
                    className="fv-shot-mini-head"
                    onClick={() => setOpenShot(open ? null : shot.id)}
                  >
                    <div className="fv-shot-mini-thumb">
                      {media.urls[shot.image_url] ? (
                        <img
                          src={media.urls[shot.image_url]}
                          alt={shot.label}
                        />
                      ) : (
                        <span className="fv-art-placeholder">🎞️</span>
                      )}
                    </div>
                    <span className="fv-shot-mini-label">
                      {shot.label || `Shot ${i + 1}`}
                    </span>
                    <span className="tiny muted">
                      {refs.length
                        ? `${refs.length} ref${refs.length === 1 ? "" : "s"}`
                        : "no refs"}
                    </span>
                  </button>

                  {open && (
                    <div className="fv-shot-mini-body">
                      {project.art.length === 0 ? (
                        <p className="tiny muted">Add art to the tray first.</p>
                      ) : (
                        <div className="fv-ref-picker">
                          {project.art.map((ref) => (
                            <button
                              type="button"
                              key={ref.id}
                              className={`fv-ref-chip ${refs.includes(ref.id) ? "on" : ""}`}
                              onClick={() => toggleRefOnShot(shot, ref.id)}
                              title={ref.name}
                            >
                              {media.urls[ref.url] ? (
                                <img src={media.urls[ref.url]} alt="" />
                              ) : (
                                <span>🖼</span>
                              )}
                              <span className="fv-ref-chip-name">
                                {ref.name}
                              </span>
                            </button>
                          ))}
                        </div>
                      )}

                      {/* Interpolating to a fixed end frame is what makes two
                          consecutive shots actually line up at the cut. */}
                      <label className="fv-field">
                        <span className="tiny muted">End on (optional)</span>
                        <select
                          value={shot.last_frame_ref_id || ""}
                          onChange={(e) =>
                            patchShot(shot.id, {
                              last_frame_ref_id: e.target.value || null
                            })
                          }
                        >
                          <option value="">Let Veo decide the ending</option>
                          {project.art.map((ref) => (
                            <option key={ref.id} value={ref.id}>
                              {ref.name || "Untitled"}
                            </option>
                          ))}
                        </select>
                      </label>
                      {shot.last_frame_ref_id &&
                        !artById[shot.last_frame_ref_id] && (
                          <p className="tiny muted">
                            That end frame is no longer in the tray — it will be
                            ignored.
                          </p>
                        )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* --- Character-run picker ----------------------------------------- */}
      {picking && (
        <div className="modal-overlay">
          <div
            className="card an-pick-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="modal-close"
              onClick={() => {
                setPicking(false);
                setAssets(null);
              }}
            >
              ✕
            </button>

            {!assets ? (
              <>
                <h2>Pick a character run</h2>
                <p className="muted">
                  Any completed Text to Turnaround Image run. Its views become
                  references, so the character you already designed is the one
                  Veo animates.
                </p>
                {!characterJobs.length && (
                  <p className="muted">
                    No completed character runs yet — make one in Text to
                    Turnaround Image first.
                  </p>
                )}
                <div className="an-pick-list">
                  {characterJobs.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      className="an-pick-row"
                      onClick={() => openRun(j)}
                    >
                      <span className="an-pick-title">{j.character_name}</span>
                      <span className="muted">{j.template || "default"}</span>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <>
                <h2>{assets.job.character_name}</h2>
                <p className="muted">
                  Pick the view that best shows the character. Front usually
                  works hardest; add a three-quarter as a second reference.
                </p>
                <div className="fv-asset-grid">
                  {Object.keys(assets.parts).map((part) =>
                    VIEWS.filter((v) => assets.parts[part]?.[v]).map((view) => (
                      <button
                        type="button"
                        key={`${part}:${view}`}
                        className="fv-asset-pick"
                        onClick={() => addAssetRef(assets.job, part, view)}
                      >
                        <span className="fv-asset-name">{part}</span>
                        <span className="tiny muted">
                          {view.replace("_", " ")}
                        </span>
                      </button>
                    ))
                  )}
                </div>
                <button
                  type="button"
                  className="btn small ghost"
                  onClick={() => setAssets(null)}
                >
                  ← Other runs
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
