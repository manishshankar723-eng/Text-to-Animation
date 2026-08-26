// Animatics → Final Video: the workspace, as a three-step workflow.
//
//   1. Apply final art & characters — the art tray, and which stills each shot
//      is locked to. Free.
//   2. Render shots                 — one Veo clip per shot. THIS SPENDS.
//   3. Assemble the sequence        — join the clips into the cut. Free.
//
// The steps are TABS, not a wizard: the real loop is render a few shots, look
// at them, fix a prompt, add a reference, render again. Forcing that through a
// one-way wizard would mean walking the whole thing for every retry.
//
// This file owns the state (project, autosave, job polling, media URLs) and the
// header; each step is its own component so none of them is unreadable.
//
// MONEY — the rule this screen is built around: nothing here spends until the
// user has seen the price. Every path to a render goes through the confirm
// dialog in FinalVideoRenderStep, which asks the server for an estimate first.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api.js";
// Rendering is `cap.veo-render`, guarded on POST /final-videos/{id}/render.
import useCapability from "../useCapability.js";
// ONE way to rename a thing, shared with Plan & Script and the storyboard
// board — see TitleInput.jsx.
import TitleInput from "./TitleInput.jsx";
import FinalVideoArtStep from "./FinalVideoArtStep.jsx";
import FinalVideoRenderStep from "./FinalVideoRenderStep.jsx";
import FinalVideoAssembleStep from "./FinalVideoAssembleStep.jsx";
import { UNTITLED } from "./FinalVideoLibrary.jsx";

const STEPS = [
  { id: 1, label: "Apply final art & characters", short: "Final art" },
  { id: 2, label: "Render shots", short: "Render" },
  { id: 3, label: "Assemble the sequence", short: "Assemble" }
];

// Autosave debounce. Long enough that typing a motion prompt is one request,
// short enough that clicking away and back doesn't lose the edit.
const SAVE_DELAY_MS = 900;
// How often to re-read a project while the server is rendering or assembling.
const POLL_MS = 4000;

// Every picture and clip sits behind the bearer token, so an <img>/<video> src
// can't point at it. This caches one object URL per path and revokes them all
// on unmount — the CALLER (this hook) owns them, per api.fetchFinalVideoMedia.
function useAuthedMedia() {
  const [urls, setUrls] = useState({});
  const owned = useRef([]);
  const pending = useRef(new Set());

  const load = useCallback((path) => {
    if (!path || pending.current.has(path)) return;
    pending.current.add(path);
    api
      .fetchFinalVideoMedia(path)
      .then((url) => {
        owned.current.push(url);
        setUrls((m) => ({ ...m, [path]: url }));
      })
      .catch(() => {
        // A missing picture leaves the placeholder. Drop it from `pending` so a
        // later render (e.g. after the panel is re-drawn) can try again.
        pending.current.delete(path);
      });
  }, []);

  // Force a re-fetch — used after a render, when the same URL now has a clip
  // behind it that didn't exist a minute ago.
  const invalidate = useCallback((path) => {
    pending.current.delete(path);
    setUrls((m) => {
      if (!m[path]) return m;
      const next = { ...m };
      delete next[path];
      return next;
    });
  }, []);

  useEffect(
    () => () => owned.current.forEach((u) => URL.revokeObjectURL(u)),
    []
  );

  return { urls, load, invalidate };
}

export default function FinalVideoWorkspace({ videoId, onBack, onDeleted }) {
  const veoCap = useCapability("veo-render");
  const [project, setProject] = useState(null);
  const [step, setStep] = useState(1);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [job, setJob] = useState(null);
  const [backend, setBackend] = useState(null);
  const [saving, setSaving] = useState(false);
  const media = useAuthedMedia();
  const saveTimer = useRef(null);
  // What the last save sent, so an autosave triggered by the SERVER's own reply
  // doesn't bounce straight back at it.
  const lastSaved = useRef("");

  const running = project?.status === "running";

  // --- Load ----------------------------------------------------------------
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [p, b] = await Promise.all([
          api.getFinalVideo(videoId),
          // Advisory: a broken backend is a banner, not a blocked screen. The
          // user can still write prompts and arrange shots without Veo.
          api.getVideoBackend().catch(() => null)
        ]);
        if (!alive) return;
        setProject(p);
        setBackend(b);
        lastSaved.current = JSON.stringify({
          shots: p.shots,
          art: p.art,
          settings: p.settings
        });
        // Land on the step that has work to do, rather than always step 1.
        if (p.shots.some((s) => s.status === "ready")) setStep(p.video ? 3 : 2);
      } catch (e) {
        if (alive) setError(e.message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [videoId]);

  // --- Poll while the server is working ------------------------------------
  // Depends on `media.invalidate` (stable) rather than `media` (a fresh object
  // every render) — otherwise every poll re-rendered, tore the interval down
  // and started a new one, so the real cadence drifted with render frequency.
  const invalidate = media.invalidate;
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(async () => {
      try {
        const [p, j] = await Promise.all([
          api.getFinalVideo(videoId),
          api.getJob(videoId).catch(() => null)
        ]);
        setProject((prev) => {
          // A shot that just became ready has a clip behind a URL we may
          // already have cached as a 404 — clear it so the player can load.
          for (const shot of p.shots) {
            const was = prev?.shots.find((s) => s.id === shot.id);
            if (was && was.status !== "ready" && shot.status === "ready") {
              invalidate(shot.url);
            }
          }
          return p;
        });
        setJob(j);
        lastSaved.current = JSON.stringify({
          shots: p.shots,
          art: p.art,
          settings: p.settings
        });
      } catch {
        // A blip shouldn't tear the screen down; the next tick retries.
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [running, videoId, invalidate]);

  // When the work finishes, say what happened rather than silently stopping.
  const prevRunning = useRef(false);
  useEffect(() => {
    if (prevRunning.current && !running && project) {
      const failed = project.shots.filter((s) => s.status === "failed");
      setJob(null);
      if (project.video && !project.video.stale) {
        setNotice("The sequence is assembled.");
      } else if (failed.length) {
        setNotice(`${failed.length} shot(s) failed — see the message on each.`);
      } else {
        setNotice("Rendering finished.");
      }
    }
    prevRunning.current = running;
  }, [running, project]);

  // --- Autosave ------------------------------------------------------------
  // Refused by the server while a render or assembly is running (it is reading
  // these exact shots), so the timer is simply not armed then.
  const queueSave = useCallback(
    (next) => {
      if (next.status === "running") return;
      const snapshot = JSON.stringify({
        shots: next.shots,
        art: next.art,
        settings: next.settings
      });
      if (snapshot === lastSaved.current) return;
      clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(async () => {
        setSaving(true);
        try {
          const saved = await api.saveFinalVideo(videoId, {
            settings: next.settings,
            shots: next.shots,
            art: next.art
          });
          lastSaved.current = JSON.stringify({
            shots: saved.shots,
            art: saved.art,
            settings: saved.settings
          });
          // Take the server's copy: it owns render state, so this is what puts
          // a just-finished clip's status back on screen after a local edit.
          setProject(saved);
          setError("");
        } catch (e) {
          setError(e.message);
        } finally {
          setSaving(false);
        }
      }, SAVE_DELAY_MS);
    },
    [videoId]
  );

  useEffect(() => () => clearTimeout(saveTimer.current), []);

  // The one way any step changes the project: patch locally for an instant UI,
  // and let the debounce carry it to the server.
  const patch = useCallback(
    (fields) => {
      setProject((prev) => {
        if (!prev) return prev;
        const next = { ...prev, ...fields };
        queueSave(next);
        return next;
      });
      setNotice("");
    },
    [queueSave]
  );

  const patchShot = useCallback(
    (shotId, fields) => {
      setProject((prev) => {
        if (!prev) return prev;
        const next = {
          ...prev,
          shots: prev.shots.map((s) =>
            s.id === shotId ? { ...s, ...fields } : s
          )
        };
        queueSave(next);
        return next;
      });
      setNotice("");
    },
    [queueSave]
  );

  // Push any pending edit before an action that reads the project server-side —
  // otherwise a prompt typed a moment ago is not yet there to render.
  const flushSave = useCallback(async () => {
    clearTimeout(saveTimer.current);
    if (!project || project.status === "running") return project;
    const snapshot = JSON.stringify({
      shots: project.shots,
      art: project.art,
      settings: project.settings
    });
    if (snapshot === lastSaved.current) return project;
    setSaving(true);
    try {
      const saved = await api.saveFinalVideo(videoId, {
        settings: project.settings,
        shots: project.shots,
        art: project.art
      });
      lastSaved.current = JSON.stringify({
        shots: saved.shots,
        art: saved.art,
        settings: saved.settings
      });
      setProject(saved);
      return saved;
    } finally {
      setSaving(false);
    }
  }, [project, videoId]);

  // --- Title ---------------------------------------------------------------
  /** ⚠ THROWS ON FAILURE, on purpose: `TitleInput` needs to hear about it so it
   *  can put the old name back in the box. The page still shows the reason. */
  async function saveTitle(title) {
    try {
      const saved = await api.saveFinalVideo(videoId, { title });
      setProject((p) => ({ ...p, title: saved.title }));
    } catch (e) {
      setError(e.message);
      throw e;
    }
  }

  async function stop() {
    try {
      await api.stopFinalVideo(videoId);
      setNotice("Stopping — clips already rendered are kept.");
    } catch (e) {
      setError(e.message);
    }
  }

  // Nothing in it and never named — i.e. you made one, looked, and left. Same
  // test AnimaticEditor makes, for the same reason: without it the library
  // fills with "Untitled final video" rows nobody asked for.
  //
  // CURRENTLY UNREACHABLE, and kept deliberately. Every project now starts
  // From a Storyboard, so it arrives with shots and a real title and can never
  // be empty by this test. It stays as a guard because the junk-library bug it
  // prevents was user-reported once already: restore any path that creates a
  // blank project and the protection is here waiting, rather than having to be
  // remembered.
  const isEmpty =
    !!project &&
    !project.shots.length &&
    !project.art.length &&
    !project.video &&
    project.status !== "running" &&
    project.title === UNTITLED;

  // Leaving: a project you never put anything into is discarded, so "open it,
  // change your mind, go back" doesn't leave a row in the library — and the
  // server drops its folder with the record. Anything with content — or a name
  // you chose — is kept.
  async function handleBack() {
    if (isEmpty) {
      clearTimeout(saveTimer.current); // nothing worth flushing on the way out
      try {
        await api.deleteFinalVideo(videoId);
      } catch {
        // Not worth blocking the exit — worst case an empty project stays and
        // can be deleted from the library.
      }
    }
    onBack();
  }

  // --- Derived -------------------------------------------------------------
  const counts = useMemo(() => {
    const shots = project?.shots || [];
    return {
      total: shots.length,
      ready: shots.filter((s) => s.status === "ready").length,
      failed: shots.filter((s) => s.status === "failed").length,
      prompted: shots.filter((s) => (s.prompt || "").trim()).length
    };
  }, [project]);

  if (error && !project) {
    return (
      <div className="workflow-head-wrap">
        <div className="error">{error}</div>
        <button
          type="button"
          className="btn back-btn"
          onClick={onBack}
          title="Back to your final videos"
          aria-label="Back to your final videos"
        >
          ←
        </button>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="workflow-head-wrap">
        <div className="fv-loading">
          <span className="spinner" /> Opening the project…
        </div>
      </div>
    );
  }

  const progress = job?.progress;

  return (
    <div className="fv-wrap">
      {/* --- Header ------------------------------------------------------- */}
      <div className="fv-top">
        <button
          type="button"
          className="btn ghost small"
          onClick={handleBack}
          title="Back to your final videos"
        >
          ←
        </button>

        {/* ⚠ IT IS A BOX, NOT A BUTTON THAT BECOMES ONE. Clicking a title to
            turn it into a field was one of four different renames in this app;
            they are one now, and the editor's always-a-field version is the one
            that won. See TitleInput.jsx. */}
        <TitleInput
          value={project.title === UNTITLED ? "" : project.title}
          placeholder="Untitled final video"
          ariaLabel="Project title"
          className="fv-title-input"
          onSave={saveTitle}
        />

        <div className="fv-top-spacer" />

        {/* Spend is always visible. It is the number that can surprise you. */}
        <span
          className="fv-spend"
          title="Estimated Veo spend on this project so far"
        >
          ~${(project.spent_usd || 0).toFixed(2)}
        </span>
        <span className="tiny muted fv-save-state">
          {saving ? "Saving…" : running ? "Working…" : "Saved"}
        </span>
        {running && (
          <button type="button" className="btn danger-btn" onClick={stop}>
            ⏹ Stop
          </button>
        )}
      </div>

      {/* --- Step tabs ---------------------------------------------------- */}
      <div className="fv-steps" role="tablist">
        {STEPS.map((s) => (
          <button
            type="button"
            key={s.id}
            role="tab"
            aria-selected={step === s.id}
            className={`fv-step ${step === s.id ? "active" : ""}`}
            onClick={() => setStep(s.id)}
          >
            <span className="fv-step-dot">{s.id}</span>
            <span className="fv-step-label">{s.label}</span>
            <span className="fv-step-short">{s.short}</span>
          </button>
        ))}
      </div>

      {/* --- Status strip -------------------------------------------------- */}
      <div className="fv-strip">
        <span className="chip">
          {counts.ready}/{counts.total} rendered
        </span>
        {counts.failed > 0 && (
          <span className="chip failed">{counts.failed} failed</span>
        )}
        {counts.total > counts.prompted && (
          <span
            className="chip"
            title="A shot with no motion prompt is never submitted"
          >
            {counts.total - counts.prompted} without a prompt
          </span>
        )}
        {project.video && (
          <span className={`chip ${project.video.stale ? "stale" : ""}`}>
            {project.video.stale ? "🎞️ cut is out of date" : "🎞️ cut ready"}
          </span>
        )}
        {progress?.percent != null && (
          <span className="fv-progress">
            <span className="fv-progress-bar">
              <span style={{ width: `${progress.percent}%` }} />
            </span>
            <span className="tiny muted">
              {progress.message || `${progress.percent}%`}
            </span>
          </span>
        )}
      </div>

      {/* Veo unreachable is worth saying ONCE, up front — not as a failed
          render the user has already waited two minutes for. */}
      {backend && !backend.ok && (
        <div className="fv-banner warn">
          <strong>Veo isn't reachable.</strong> {backend.error} You can still
          write prompts and arrange shots; rendering needs this fixed.
        </div>
      )}

      {/* ⚠ THE SAME SHAPE, ONE ROW DOWN, because it is the same news to
          the person reading it: this screen can do everything except the part
          that spends. Said once here rather than on each of the twenty Render
          buttons — which are disabled and carry the reason as well. */}
      {!veoCap.on && veoCap.visible && (
        <div className="fv-banner warn">
          <strong>🔒 {veoCap.reason}</strong> You can still write prompts,
          arrange shots and assemble the clips you have already rendered.
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {notice && <div className="fv-banner">{notice}</div>}

      {/* --- The step ----------------------------------------------------- */}
      <div className="fv-body">
        {step === 1 && (
          <FinalVideoArtStep
            project={project}
            media={media}
            patch={patch}
            patchShot={patchShot}
            onError={setError}
            onNext={() => setStep(2)}
          />
        )}
        {step === 2 && (
          <FinalVideoRenderStep
            project={project}
            media={media}
            patch={patch}
            patchShot={patchShot}
            flushSave={flushSave}
            onProject={setProject}
            onError={setError}
            onNotice={setNotice}
            backendOk={backend ? backend.ok : true}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && (
          <FinalVideoAssembleStep
            project={project}
            media={media}
            patch={patch}
            patchShot={patchShot}
            flushSave={flushSave}
            onProject={setProject}
            onError={setError}
            onNotice={setNotice}
            onDeleted={onDeleted}
          />
        )}
      </div>
    </div>
  );
}
