// Step 3 — Assemble the sequence.
//
// Joins the rendered clips into one cut. This is ffmpeg, not AI: it is FREE and
// repeatable, which is the opposite of step 2 and worth saying on screen —
// otherwise people hoard re-cuts the way they (rightly) hoard re-renders.
//
// Because it is free, the order and the transition are editable right up to the
// button, and re-assembling after a change is one click.
import { useEffect, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
import { formatTime } from "./Timeline.jsx";

export default function FinalVideoAssembleStep({
  project,
  media,
  patch,
  patchShot,
  flushSave,
  onProject,
  onError,
  onNotice,
  onDeleted
}) {
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const running = project.status === "running";
  const settings = project.settings;
  const video = project.video;
  // What actually reaches the cut: rendered AND not left out. A rendered shot
  // the user excluded keeps its clip and its cost — it just isn't in the film.
  const ready = project.shots.filter(
    (s) => s.status === "ready" && s.include !== false
  );

  useEffect(() => {
    for (const shot of ready) media.load(shot.image_url);
    if (video && !video.stale)
      media.load(`/final-videos/${project.job_id}/video`);
  }, [project.shots, video, media]); // eslint-disable-line react-hooks/exhaustive-deps

  function setSetting(fields) {
    patch({ settings: { ...settings, ...fields } });
  }

  // Reordering is a whole-list replace, like every other list in this app.
  function move(index, delta) {
    const next = [...project.shots];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    patch({ shots: next });
  }

  async function assemble() {
    setBusy(true);
    onError("");
    try {
      await flushSave();
      await api.assembleFinalVideo(project.job_id);
      onProject(await api.getFinalVideo(project.job_id));
      onNotice("Assembling the cut…");
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    setBusy(true);
    try {
      await api.downloadFinalVideo(
        project.job_id,
        `${project.title || "final"}.mp4`
      );
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function doDelete() {
    setBusy(true);
    try {
      await api.deleteFinalVideo(project.job_id);
      onDeleted?.();
    } catch (e) {
      onError(e.message);
      setBusy(false);
    }
  }

  const videoUrl = media.urls[`/final-videos/${project.job_id}/video`];
  const estimatedMs =
    ready.reduce((sum, s) => sum + (s.duration_ms || 0), 0) -
    (settings.transition === "crossfade"
      ? settings.transition_ms * Math.max(0, ready.length - 1)
      : 0);

  return (
    <div className="fv-step-pane">
      <div className="fv-pane-head">
        <div>
          <h2>Assemble the sequence</h2>
          <p className="muted">
            Join the rendered clips into one cut, in this order.{" "}
            <strong>Free and repeatable</strong> — this step spends nothing, so
            re-cut as often as you like.
          </p>
        </div>
      </div>

      {/* --- The cut, if there is one -------------------------------------- */}
      {video && (
        <section className="fv-section">
          <div className="fv-section-head">
            <h3>Your cut</h3>
            <span className="tiny muted">
              {formatTime(video.duration_ms || 0)} · {video.clip_count} clip
              {video.clip_count === 1 ? "" : "s"} ·{" "}
              {Math.round((video.size_bytes || 0) / 1_048_576)} MB
            </span>
          </div>

          {video.stale && (
            <div className="fv-banner warn">
              This cut is out of date — the project changed after it was made.
              Assemble again to bring it up to date.
            </div>
          )}

          {videoUrl ? (
            <video
              className="fv-final-player"
              src={videoUrl}
              controls
              playsInline
            />
          ) : (
            <div className="fv-final-placeholder">
              {video.stale
                ? "The old cut is still downloadable."
                : "Loading the cut…"}
            </div>
          )}

          {video.skipped?.length > 0 && (
            <p className="tiny muted">
              {video.skipped.length} clip(s) were missing and left out.
            </p>
          )}

          <div className="fv-final-actions">
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={download}
            >
              <Icon name="download" /> Download MP4
            </button>
          </div>
        </section>
      )}

      {/* --- Settings ------------------------------------------------------ */}
      <section className="fv-section">
        <div className="fv-section-head">
          <h3>How it joins</h3>
        </div>
        <div className="fv-settings">
          <label className="fv-field">
            <span className="tiny muted">Between shots</span>
            <select
              value={settings.transition}
              disabled={running}
              onChange={(e) => setSetting({ transition: e.target.value })}
            >
              <option value="cut">Cut — instant, lossless</option>
              <option value="crossfade">Crossfade — re-encodes, slower</option>
            </select>
          </label>

          {settings.transition === "crossfade" && (
            <label className="fv-field">
              <span className="tiny muted">Crossfade length</span>
              <select
                value={settings.transition_ms}
                disabled={running}
                onChange={(e) =>
                  setSetting({ transition_ms: Number(e.target.value) })
                }
              >
                {[200, 400, 600, 1000].map((ms) => (
                  <option key={ms} value={ms}>
                    {ms} ms
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="fv-field">
            <span className="tiny muted">Frame rate</span>
            <select
              value={settings.fps}
              disabled={running}
              onChange={(e) => setSetting({ fps: Number(e.target.value) })}
            >
              {[24, 25, 30].map((f) => (
                <option key={f} value={f}>
                  {f} fps
                </option>
              ))}
            </select>
          </label>

          <label className="fv-check">
            <input
              type="checkbox"
              checked={settings.include_clip_audio}
              disabled={running}
              onChange={(e) =>
                setSetting({ include_clip_audio: e.target.checked })
              }
            />
            <span>
              Keep the sound Veo generated
              <span className="tiny muted">
                {" "}
                — off gives a silent cut to lay your own audio under
              </span>
            </span>
          </label>
        </div>
      </section>

      {/* --- Order --------------------------------------------------------- */}
      <section className="fv-section">
        <div className="fv-section-head">
          <h3>Order</h3>
          <span className="tiny muted">
            {ready.length} clip{ready.length === 1 ? "" : "s"} ·{" "}
            {formatTime(Math.max(0, estimatedMs))}
          </span>
        </div>

        {ready.length === 0 ? (
          <p className="muted fv-empty">
            Nothing to assemble yet — render at least one shot on step 2 first.
          </p>
        ) : (
          <ol className="fv-order">
            {project.shots.map((shot, i) => {
              const rendered = shot.status === "ready";
              const included = rendered && shot.include !== false;
              return (
                <li
                  className={`fv-order-row ${included ? "" : "excluded"}`}
                  key={shot.id}
                >
                  <div className="fv-order-thumb">
                    {media.urls[shot.image_url] ? (
                      <img src={media.urls[shot.image_url]} alt="" />
                    ) : (
                      <span className="fv-art-placeholder">🎞️</span>
                    )}
                  </div>
                  <span className="fv-order-label">
                    {shot.label || `Shot ${i + 1}`}
                    {!included && (
                      <span className="tiny muted">
                        {" "}
                        — {rendered ? "left out" : "not rendered"}
                      </span>
                    )}
                  </span>
                  {included && (
                    <span className="tiny muted">
                      {formatTime(shot.duration_ms || 0)}
                    </span>
                  )}
                  <div className="fv-order-move">
                    <button
                      type="button"
                      className="lib-icon"
                      disabled={running || i === 0}
                      title="Move earlier"
                      onClick={() => move(i, -1)}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="lib-icon"
                      disabled={running || i === project.shots.length - 1}
                      title="Move later"
                      onClick={() => move(i, 1)}
                    >
                      ↓
                    </button>
                    {rendered && (
                      <button
                        type="button"
                        className="lib-icon"
                        disabled={running}
                        title={
                          included
                            ? "Leave this clip out of the cut — the clip is kept"
                            : "Put this clip back in the cut"
                        }
                        onClick={() =>
                          patchShot(shot.id, { include: !included })
                        }
                      >
                        {included ? <Icon name="close" /> : "＋"}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}

        <div className="fv-batch">
          <button
            type="button"
            className="btn primary"
            disabled={running || busy || !ready.length}
            onClick={assemble}
          >
            {video ? "🎞️ Assemble again" : "🎞️ Assemble the sequence"}
          </button>
          <span className="tiny muted">Costs nothing.</span>
        </div>
      </section>

      {/* --- Danger zone ---------------------------------------------------- */}
      <section className="fv-section">
        {confirmDelete ? (
          <div className="lib-confirm">
            <span className="tiny">
              Delete “{project.title}”? Its {ready.length} rendered clip
              {ready.length === 1 ? "" : "s"} go for good — including what you
              paid to render. The animatic it came from is untouched.
            </span>
            <div className="lib-confirm-btns">
              <button
                type="button"
                className="btn small"
                disabled={busy}
                onClick={() => setConfirmDelete(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn small lib-delete"
                disabled={busy}
                onClick={doDelete}
              >
                {busy ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="btn small danger-btn"
            disabled={running}
            onClick={() => setConfirmDelete(true)}
          >
            <Icon name="trash" /> Delete this project
          </button>
        )}
      </section>
    </div>
  );
}
