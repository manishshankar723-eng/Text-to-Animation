// Step 2 — Render shots. THE ONLY SCREEN IN THIS APP THAT SPENDS PER CLICK.
//
// One Veo render per shot, billed per second of output. Everything about this
// component is shaped by that:
//
//   • No button renders anything directly. They all open the confirm dialog,
//     which asks the server for an estimate FIRST and shows it.
//   • A shot with no motion prompt is never submitted — it could only produce a
//     paid failure. Its Render button is disabled and says why.
//   • A shot that already has a clip is not re-rendered unless the user says so
//     explicitly ("Re-render" is a separate, differently-worded action).
//   • The estimate is labelled an estimate. List prices drift, and only Google
//     bills — promising a number we don't control would be worse than useless.
//
// The prompt field asks for MOTION, not description: the picture is already the
// input, so re-describing it wastes the prompt. The placeholder says so.
import { useEffect, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";

const TIERS = [
  { id: "lite", label: "Lite", hint: "cheapest, roughest" },
  { id: "fast", label: "Fast", hint: "the usual choice" },
  { id: "standard", label: "Standard", hint: "best, ~3× Fast" }
];
const RESOLUTIONS = ["720p", "1080p"];
// Veo offers a fixed menu of lengths, not a free-form number.
const DURATIONS = [4, 6, 8];

const STATUS_LABEL = {
  pending: "Not rendered",
  queued: "Queued",
  rendering: "Rendering…",
  ready: "Ready",
  failed: "Failed"
};

export default function FinalVideoRenderStep({
  project,
  media,
  patch,
  patchShot,
  flushSave,
  onProject,
  onError,
  onNotice,
  backendOk,
  onNext
}) {
  // { shotIds, force, estimate } while the confirm dialog is open.
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);

  const running = project.status === "running";
  const settings = project.settings;

  useEffect(() => {
    for (const shot of project.shots) {
      media.load(shot.image_url);
      if (shot.status === "ready" && shot.url) media.load(shot.url);
    }
  }, [project.shots, media]);

  function setRender(fields) {
    patch({
      settings: { ...settings, render: { ...settings.render, ...fields } }
    });
  }

  // Every render goes through here: save pending edits, ask what it costs, then
  // show the number. Nothing is submitted until the user confirms.
  async function askToRender(shotIds, force = false) {
    onError("");
    setBusy(true);
    try {
      await flushSave();
      const estimate = await api.estimateFinalVideo(project.job_id, {
        shotIds,
        force
      });
      if (!estimate.shots) {
        onError(
          force
            ? "Nothing to re-render."
            : "Nothing to render — shots need a motion prompt, and rendered shots are only redone on purpose."
        );
        return;
      }
      setConfirm({ shotIds, force, estimate });
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function doRender() {
    const { shotIds, force } = confirm;
    setBusy(true);
    setConfirm(null);
    try {
      await api.renderFinalVideoShots(project.job_id, { shotIds, force });
      onProject(await api.getFinalVideo(project.job_id));
      onNotice("Rendering — this takes a couple of minutes per shot.");
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // Matches the server's "render remaining" rule exactly, so the count on the
  // button is the count the confirm dialog then prices.
  const renderable = project.shots.filter(
    (s) =>
      (s.prompt || "").trim() && s.status !== "ready" && s.include !== false
  );
  const ready = project.shots.filter((s) => s.status === "ready");

  return (
    <div className="fv-step-pane">
      <div className="fv-pane-head">
        <div>
          <h2>Render shots</h2>
          <p className="muted">
            Each shot becomes a Veo clip from its picture plus what you say
            should move. <strong>This is the step that costs money</strong> —
            billed per second of video, so every render shows its price first.
          </p>
        </div>
        <button
          type="button"
          className="btn"
          onClick={onNext}
          disabled={!ready.length}
        >
          Next: assemble →
        </button>
      </div>

      {/* --- Defaults every shot inherits ---------------------------------- */}
      <section className="fv-section">
        <div className="fv-section-head">
          <h3>Render settings</h3>
          <span className="tiny muted">
            Applies to every shot without its own override
          </span>
        </div>
        <div className="fv-settings">
          <label className="fv-field">
            <span className="tiny muted">Quality</span>
            <select
              value={settings.render.tier}
              disabled={running}
              onChange={(e) => setRender({ tier: e.target.value })}
            >
              {TIERS.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label} — {t.hint}
                </option>
              ))}
            </select>
          </label>

          <label className="fv-field">
            <span className="tiny muted">Resolution</span>
            <select
              value={settings.render.resolution}
              disabled={running}
              onChange={(e) => setRender({ resolution: e.target.value })}
            >
              {RESOLUTIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>

          <label className="fv-field">
            <span className="tiny muted">Clip length</span>
            <select
              value={settings.render.duration_seconds}
              disabled={running}
              onChange={(e) =>
                setRender({ duration_seconds: Number(e.target.value) })
              }
            >
              {DURATIONS.map((d) => (
                <option key={d} value={d}>
                  {d}s
                </option>
              ))}
            </select>
          </label>

          <label className="fv-field">
            <span className="tiny muted">Frame shape</span>
            <select
              value={settings.aspect_ratio}
              disabled={running}
              onChange={(e) =>
                patch({
                  settings: { ...settings, aspect_ratio: e.target.value }
                })
              }
            >
              {/* Veo renders these two only. */}
              <option value="16:9">16:9 — landscape</option>
              <option value="9:16">9:16 — vertical</option>
            </select>
          </label>

          <label className="fv-check">
            <input
              type="checkbox"
              checked={settings.render.generate_audio}
              disabled={running}
              onChange={(e) => setRender({ generate_audio: e.target.checked })}
            />
            <span>
              Generate sound
              <span className="tiny muted">
                {" "}
                — costs a little more per second
              </span>
            </span>
          </label>
        </div>

        <label className="fv-field wide">
          <span className="tiny muted">
            Never show (optional) — what must not appear or happen
          </span>
          <input
            type="text"
            value={settings.render.negative_prompt}
            disabled={running}
            placeholder="e.g. text on screen, extra fingers, camera shake"
            onChange={(e) => setRender({ negative_prompt: e.target.value })}
          />
        </label>

        <div className="fv-batch">
          <button
            type="button"
            className="btn"
            disabled={running || busy || !backendOk || !renderable.length}
            onClick={() => askToRender([], false)}
            title={
              !backendOk
                ? "Veo isn't reachable — see the banner above"
                : "Render every shot that has a prompt and no clip yet"
            }
          >
            ▶ Render remaining ({renderable.length})
          </button>
          <span className="tiny muted">
            Shows the price before it spends anything.
          </span>
        </div>
      </section>

      {/* --- The shots ----------------------------------------------------- */}
      <section className="fv-section">
        <div className="fv-section-head">
          <h3>Shots</h3>
          <span className="tiny muted">
            {ready.length}/{project.shots.length} rendered
          </span>
        </div>

        {project.shots.length === 0 && (
          <p className="muted fv-empty">
            No shots yet — start one from the Video Editor to fill them in.
          </p>
        )}

        <div className="fv-shots">
          {project.shots.map((shot, i) => {
            const hasPrompt = !!(shot.prompt || "").trim();
            const isReady = shot.status === "ready";
            const working =
              shot.status === "rendering" || shot.status === "queued";
            return (
              <div className={`card fv-shot ${shot.status}`} key={shot.id}>
                <div className="fv-shot-media">
                  {/* Once a clip exists it replaces the still: what you want to
                      look at after a render is the motion, not the input. */}
                  {isReady && media.urls[shot.url] ? (
                    <video
                      src={media.urls[shot.url]}
                      controls
                      loop
                      playsInline
                    />
                  ) : media.urls[shot.image_url] ? (
                    <img src={media.urls[shot.image_url]} alt={shot.label} />
                  ) : (
                    <div className="fv-shot-placeholder">🎞️</div>
                  )}
                  {working && (
                    <span className="fv-shot-working">
                      <span className="spinner" /> {STATUS_LABEL[shot.status]}
                    </span>
                  )}
                </div>

                <div className="fv-shot-body">
                  <div className="fv-shot-head">
                    <strong>{shot.label || `Shot ${i + 1}`}</strong>
                    <span className={`chip ${shot.status}`}>
                      {STATUS_LABEL[shot.status] || shot.status}
                    </span>
                    {shot.cost_usd > 0 && (
                      <span className="tiny muted">
                        ~${shot.cost_usd.toFixed(2)}
                      </span>
                    )}
                  </div>

                  <textarea
                    className="fv-prompt"
                    rows={3}
                    value={shot.prompt || ""}
                    disabled={running}
                    placeholder="What MOVES? e.g. 'slow push in as she turns to face the door; her scarf lifts in the wind'. The picture is already the input — describe motion and camera, not the scene."
                    onChange={(e) =>
                      patchShot(shot.id, { prompt: e.target.value })
                    }
                  />

                  {shot.error && (
                    <div className="fv-shot-error">{shot.error}</div>
                  )}

                  <div className="fv-shot-foot">
                    {/* Whether a shot is in the film is the USER's call and is
                        kept apart from render status — leaving a rendered shot
                        out must not erase the clip you paid for. */}
                    <label className="fv-check tiny">
                      <input
                        type="checkbox"
                        checked={shot.include === false}
                        disabled={running}
                        onChange={(e) =>
                          patchShot(shot.id, { include: !e.target.checked })
                        }
                      />
                      <span>Leave out</span>
                    </label>

                    <div className="fv-shot-actions">
                      {isReady ? (
                        <button
                          type="button"
                          className="btn small ghost"
                          disabled={running || busy || !backendOk}
                          title="Render this shot again — it costs the same as the first time"
                          onClick={() => askToRender([shot.id], true)}
                        >
                          ↻ Re-render
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn small"
                          disabled={running || busy || !hasPrompt || !backendOk}
                          title={
                            !hasPrompt
                              ? "Say what should move first — a shot with no prompt can only fail"
                              : !backendOk
                                ? "Veo isn't reachable — see the banner above"
                                : "Render just this shot"
                          }
                          onClick={() => askToRender([shot.id], false)}
                        >
                          ▶ Render
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* --- The confirm dialog: the last thing before money moves --------- */}
      {confirm && (
        <div className="modal-overlay" onClick={() => setConfirm(null)}>
          <div className="card fv-confirm" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="modal-close"
              onClick={() => setConfirm(null)}
            >
              ✕
            </button>
            <h2>
              Render {confirm.estimate.shots} shot
              {confirm.estimate.shots === 1 ? "" : "s"}?
            </h2>

            <div className="fv-confirm-price">
              <span className="fv-confirm-usd">
                ~${confirm.estimate.usd.toFixed(2)}
              </span>
              <span className="tiny muted">estimated</span>
            </div>

            <p className="muted">
              {confirm.estimate.seconds}s of video at {settings.render.tier} /{" "}
              {settings.render.resolution}
              {settings.render.generate_audio ? " with sound" : ", silent"}.
              {confirm.force &&
                " These shots already have clips — rendering again costs the same as the first time."}
            </p>
            <p className="tiny muted">
              An estimate from list prices, not a quote. Google bills the actual
              amount, and you are only charged for renders that succeed.
            </p>

            {/* Same footer as the animatic's Export dialog: `.an-name-actions`
                with a full-size ghost Cancel and a full-size gold primary. NOT
                `.lib-confirm-btns` + `btn small` — that pair is for the inline
                confirm strip INSIDE a library card, and using it here made the
                two buttons a different size and left the gold one hanging low
                (`.btn.primary` carries margin-top: 1.1rem, which the modal
                footer rule cancels). */}
            <div className="an-name-actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={busy}
                onClick={doRender}
              >
                <Icon name="play" />{" "}
                {busy
                  ? "Starting…"
                  : `Render — ~$${confirm.estimate.usd.toFixed(2)}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
