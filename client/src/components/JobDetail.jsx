import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";

const VIEWS = ["front", "left", "three_quarter", "back"];

// Friendly section names. Anything not listed is title-cased from its key.
const PART_LABELS = {
  jacket: "Upper Garment",
  pants: "Lower Garment",
};
function prettyPart(p) {
  if (!p) return "";
  return (
    PART_LABELS[p] ||
    p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

// Detail panel for one job: polls until done, then shows the gallery, per-part
// prompt view/edit, single-part regeneration, zip download, and Meshy 3D submission.
export default function JobDetail({ jobId, onChanged }) {
  const [job, setJob] = useState(null);
  const [assets, setAssets] = useState(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  // A stop has been asked for but the run hasn't wound down yet (the part being
  // drawn still has to come back).
  const [stopRequested, setStopRequested] = useState(false);

  // Per-section 3D state: { [part]: { jobId, status, modelUrls, provider } }
  const [model3d, setModel3d] = useState({});
  const [savedKeys, setSavedKeys] = useState({}); // { meshy:true, tripo:true }
  // 3D popup: which part it's for + form fields.
  const [threeDPart, setThreeDPart] = useState(null);
  const [tdProvider, setTdProvider] = useState("meshy");
  const [tdKey, setTdKey] = useState("");
  const [tdSave, setTdSave] = useState(true);
  const [tdBusy, setTdBusy] = useState(false);

  // Per-part prompt editing & regeneration state
  const [editedPrompts, setEditedPrompts] = useState({});
  const [regenBusy, setRegenBusy] = useState({}); // { [partName]: boolean }
  const [viewBusy, setViewBusy] = useState({}); // { [`${part}_${view}`]: boolean }
  const [cacheBust, setCacheBust] = useState(Date.now());

  // Lightbox popup for gallery images
  const [lightboxSrc, setLightboxSrc] = useState(null);

  const load = useCallback(async () => {
    try {
      const j = await api.getJob(jobId);
      setJob(j);
      setError("");
      // Fetch assets both when done AND while running, so parts appear
      // one-by-one as the pipeline finishes each. A 409 ("nothing yet")
      // is expected early on — keep whatever we already have.
      if (j.kind === "generate" && (j.status === "succeeded" || j.status === "running")) {
        try {
          setAssets(await api.getAssets(jobId));
          // Regenerated images reuse the same filenames — bust the cache when
          // the job settles so the browser shows the fresh versions.
          if (j.status === "succeeded") setCacheBust(Date.now());
        } catch {
          /* not ready yet — retain any previously loaded partial assets */
        }
      }
      return j;
    } catch (e) {
      setError(e.message);
      return null;
    }
  }, [jobId]);

  // Sync prompts from job result when job loads
  useEffect(() => {
    if (job?.result?.prompts) {
      setEditedPrompts((prev) => ({
        ...job.result.prompts,
        ...prev,
      }));
    }
  }, [job]);

  // Reset when the selected job changes.
  useEffect(() => {
    setJob(null);
    setAssets(null);
    setModel3d({});
    setThreeDPart(null);
    setEditedPrompts({});
    setRegenBusy({});
    setCacheBust(Date.now());
    load();
  }, [jobId, load]);

  // Which providers the user has a saved key for (to prefill the 3D popup).
  useEffect(() => {
    api.getApiKeys().then(setSavedKeys).catch(() => setSavedKeys({}));
  }, []);

  // Poll any in-flight per-section 3D jobs until they finish.
  useEffect(() => {
    const pending = Object.entries(model3d).filter(
      ([, m]) => m.jobId && (m.status === "queued" || m.status === "running")
    );
    if (pending.length === 0) return;
    const t = setInterval(async () => {
      for (const [part, m] of pending) {
        try {
          const mj = await api.getJob(m.jobId);
          const urls = mj.result?.meshy?.[part]?.model_urls;
          setModel3d((prev) => ({
            ...prev,
            [part]: {
              ...prev[part],
              status: mj.status,
              modelUrls: urls || prev[part]?.modelUrls,
              error: mj.error,
            },
          }));
        } catch {
          /* keep trying */
        }
      }
    }, 5000);
    return () => clearInterval(t);
  }, [model3d]);

  // Poll while the job is active, and tell the parent ONCE when it settles.
  // `onChanged` is deliberately not a dependency and is read through a ref:
  // it bumps state in the parent, so re-running this effect whenever the
  // parent re-rendered would notify → re-render → notify … forever.
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const notifiedRef = useRef(null); // last status we announced, per job

  useEffect(() => {
    if (!job) return;
    const active = job.status === "queued" || job.status === "running";
    if (!active) {
      const key = `${job.job_id}:${job.status}`;
      if (notifiedRef.current !== key) {
        notifiedRef.current = key;
        onChangedRef.current?.();
      }
      return;
    }
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [job, load]);

  if (!job) {
    return (
      <div className="card detail">
        {error ? <div className="error">{error}</div> : <p className="muted">Loading…</p>}
      </div>
    );
  }

  const isActive = job.status === "queued" || job.status === "running";
  const isDone = job.status === "succeeded";
  // The run ended early because the user pressed Stop (server-reported, so it
  // survives a reload) — say so rather than showing a half set as "complete".
  const wasStopped = Boolean(job.result?.stopped);
  const partNames = assets ? Object.keys(assets.parts) : [];

  // Stop the run. The part being drawn finishes, so the button keeps saying
  // "Stopping…" until the job reaches a terminal state.
  async function stopRun() {
    if (stopRequested || !isActive) return;
    setError("");
    setStopRequested(true);
    try {
      await api.stopJob(jobId);
    } catch (e) {
      setError(e.message);
      setStopRequested(false); // it didn't take — let them press it again
    }
  }

  function open3D(part) {
    setThreeDPart(part);
    setTdKey("");
    setTdSave(true);
    // Default to a provider the user already has a key for, else meshy.
    setTdProvider(savedKeys.meshy ? "meshy" : savedKeys.tripo ? "tripo" : "meshy");
    setError("");
  }

  async function download() {
    setDownloading(true);
    setError("");
    try {
      const zipUrl = assets?.zip || job?.result?.zip;
      await api.downloadZip(jobId, `${job.character_name}_assets.zip`, zipUrl);
    } catch (e) {
      setError(e.message);
    } finally {
      setDownloading(false);
    }
  }

  async function handleRegenerateView(partName, view) {
    const key = `${partName}_${view}`;
    setViewBusy((prev) => ({ ...prev, [key]: true }));
    setError("");
    try {
      const customPrompt = editedPrompts[partName];
      const provider = job.params?.provider;
      // Async: kicks off a background job; the parent job goes RUNNING and the
      // poll loop shows progress + refreshes when it finishes.
      await api.regenerateView(jobId, partName, view, customPrompt, provider);
      await load();
    } catch (e) {
      setError(`Failed to start regeneration for '${partName}' ${view}: ${e.message}`);
    } finally {
      setViewBusy((prev) => ({ ...prev, [key]: false }));
    }
  }

  async function handleRegeneratePart(partName) {
    setRegenBusy((prev) => ({ ...prev, [partName]: true }));
    setError("");
    try {
      const customPrompt = editedPrompts[partName];
      const provider = job.params?.provider;
      // Async: background job; poll loop handles progress + refresh.
      await api.regeneratePart(jobId, partName, customPrompt, provider);
      await load();
    } catch (e) {
      setError(`Failed to start regeneration for '${partName}': ${e.message}`);
    } finally {
      setRegenBusy((prev) => ({ ...prev, [partName]: false }));
    }
  }

  async function submit3D() {
    const part = threeDPart;
    const hasSavedKey = Boolean(savedKeys[tdProvider]);
    const key = tdKey.trim();
    if (!key && !hasSavedKey) {
      setError(`Enter your ${tdProvider} API key (or save one in your profile).`);
      return;
    }
    setTdBusy(true);
    setError("");
    try {
      // Optionally persist the key to the user's profile for reuse.
      if (key && tdSave) {
        try {
          await api.saveApiKey(tdProvider, key);
          setSavedKeys((prev) => ({ ...prev, [tdProvider]: true }));
        } catch {
          /* non-fatal — still submit with the key below */
        }
      }
      const res = await api.submitModel3D(jobId, [part], tdProvider, key || undefined);
      setModel3d((prev) => ({
        ...prev,
        [part]: { jobId: res.job_id, status: res.status || "queued", provider: tdProvider },
      }));
      setThreeDPart(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setTdBusy(false);
    }
  }

  // Resolve image source URL: serve through API image route if local or fallback
  function getPartViewUrl(part, view) {
    const rawUrl = assets?.parts?.[part]?.[view];
    if (!rawUrl) return null;
    if (rawUrl.startsWith("http://") || rawUrl.startsWith("https://")) {
      return `${rawUrl}?t=${cacheBust}`;
    }
    return `${api.BASE}/jobs/${jobId}/image/${part}/${view}?t=${cacheBust}`;
  }

  return (
    <div className="card detail">
      <div className="detail-head">
        <h2>
          {job.character_name}{" "}
          <span className={`badge ${statusClass(job.status)}`}>{job.status}</span>
        </h2>
        <span className="muted tiny">{job.kind} · {job.template || "default"}</span>
      </div>

      {isActive && (
        <div className="job-progress">
          <div className="jp-row">
            <span className="jp-msg">
              <span className="spinner-inline" />{" "}
              {job.progress?.message || "Queued — starting soon…"}
            </span>
            <span className="jp-pct">{job.progress?.percent ?? 0}%</span>
          </div>
          <div className="jp-bar">
            <div
              className="jp-fill"
              style={{ width: `${Math.max(job.progress?.percent ?? 4, 4)}%` }}
            />
          </div>
          <div className="jp-foot">
            <span className="muted tiny">
              {job.progress?.total_parts
                ? `${job.progress.done_parts?.length || 0} of ${job.progress.total_parts} parts done`
                : "This can take a few minutes."}
            </span>
            {/* Stop as soon as the first part looks wrong — the parts already
                generated are kept and stay downloadable. */}
            <button
              type="button"
              className="btn small danger-btn"
              disabled={stopRequested}
              onClick={stopRun}
              title="Stop generating the remaining parts — the part being drawn will still finish"
            >
              {stopRequested ? (
                <>
                  <span className="spinner-inline" /> Stopping…
                </>
              ) : (
                "⏹ Stop generation"
              )}
            </button>
          </div>
        </div>
      )}

      {wasStopped && (
        <div className="info-msg">
          ⏹ You stopped this generation — {job.result?.parts_generated?.length || 0} of{" "}
          {job.progress?.total_parts || "?"} parts were made. They're all
          downloadable. Use 🔄 on any section to redo it
          {(job.result?.pending_parts || []).length > 0
            ? ", and the skipped sections listed below have a Generate button."
            : ", or start a new run for the rest."}
        </div>
      )}

      {job.status === "failed" && (
        <div className="error">{job.error || "Job failed."}</div>
      )}

      {error && <div className="error">{error}</div>}
      {job.result?.regen_error && (
        <div className="error">Regeneration failed — {job.result.regen_error}</div>
      )}

      {/* Meshy job result */}
      {job.kind === "meshy" && job.status === "succeeded" && (
        <div className="meshy-result">
          <h3>3D models</h3>
          {Object.entries(job.result?.meshy || {}).map(([part, data]) => (
            <div key={part} className="muted">
              <strong>{part}:</strong>{" "}
              {Object.entries(data.model_urls || {}).map(([fmt, url]) => (
                <a key={fmt} href={url} target="_blank" rel="noreferrer" className="chip">
                  {fmt}
                </a>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* Generation result — gallery shows live while running (parts appear
          one-by-one) and in full once done. Editing/3D controls only when done.
          Also render while active so the FIRST part shows its loading skeleton. */}
      {job.kind === "generate" && (assets || isDone || isActive) && (
        <>
          {isDone && (
            <div className="actions">
              <button className="btn primary" onClick={download} disabled={downloading}>
                {downloading ? "Preparing…" : "⬇ Download zip"}
              </button>
            </div>
          )}

          {isActive && (
            <p className="muted tiny live-hint">
              ✨ Parts appear below as each finishes generating…
            </p>
          )}

          {assets &&
            partNames.map((part) => {
              const currentPrompt =
                editedPrompts[part] ?? job.result?.prompts?.[part] ?? "";
              const isRegening = Boolean(regenBusy[part]);

              return (
                <div key={part} className="part-block">
                  <div className="part-head">
                    {isDone ? (
                      <>
                        <strong className="part-title">{prettyPart(part)}</strong>
                        <div className="part-actions">
                          <button
                            type="button"
                            className="btn small secondary part-btn"
                            title={`Download ${prettyPart(part)} (4 views)`}
                            onClick={() =>
                              api
                                .downloadPart(jobId, part, `${job.character_name}_${part}.zip`)
                                .catch((e) => setError(e.message))
                            }
                          >
                            ⬇ Download zip
                          </button>
                          <button
                            type="button"
                            className="btn small secondary part-btn"
                            title={`Generate a 3D model for ${prettyPart(part)}`}
                            onClick={() => open3D(part)}
                          >
                            🧊 Generate 3D
                          </button>
                        </div>
                      </>
                    ) : (
                      <strong className="part-live-name">
                        {prettyPart(part)} <span className="badge ok">ready</span>
                      </strong>
                    )}
                  </div>

                  {/* Prompt view/edit + regenerate — only once the job is done */}
                  {isDone && (
                    <details
                      className="prompt-details"
                      open={part === "fullbody" || part === "face"}
                    >
                      <summary>📝 View / Edit Prompt for {part}</summary>
                      <textarea
                        className="prompt-textarea"
                        value={currentPrompt}
                        onChange={(e) =>
                          setEditedPrompts((prev) => ({
                            ...prev,
                            [part]: e.target.value,
                          }))
                        }
                        rows={3}
                        placeholder={`Prompt for ${part}...`}
                      />
                      <button
                        type="button"
                        className="btn secondary small"
                        disabled={isRegening || !currentPrompt.trim()}
                        onClick={() => handleRegeneratePart(part)}
                      >
                        {isRegening ? <span className="spinner-inline" /> : null}
                        {isRegening ? ` Regenerating ${part}…` : `🔄 Regenerate ${part}`}
                      </button>
                    </details>
                  )}

                  <div className="gallery">
                    {VIEWS.map((v) => {
                      const imgUrl = getPartViewUrl(part, v);
                      if (!imgUrl) return null;
                      // Regenerating this ONE view, or the whole part (which
                      // redraws all four). Either way this picture is being
                      // replaced, and the image on screen is about to be stale.
                      const replacing =
                        Boolean(viewBusy[`${part}_${v}`]) || isRegening;
                      const vBusy = Boolean(viewBusy[`${part}_${v}`]);
                      return (
                        <figure
                          key={v}
                          className={`view-fig ${replacing ? "is-redrawing" : ""}`}
                        >
                          <img
                            src={imgUrl}
                            alt={`${part} ${v}`}
                            loading="lazy"
                            className="clickable"
                            onClick={() => setLightboxSrc(imgUrl)}
                            title="Click to view full size"
                          />
                          {/* Same veil as the board and the key-pose strip: the
                              old picture used to sit there untouched for the
                              whole redraw, with only a 14px spinner inside the
                              corner button to say so. */}
                          {replacing && (
                            <span className="redraw-veil">
                              <span className="spinner-inline" />
                              <span className="tiny">Regenerating…</span>
                            </span>
                          )}
                          {isDone && !replacing && (
                            <button
                              type="button"
                              className="view-regen"
                              disabled={vBusy}
                              title={`Regenerate only this ${v.replace("_", " ")} image`}
                              onClick={() => handleRegenerateView(part, v)}
                            >
                              🔄
                            </button>
                          )}
                          <figcaption>{v.replace("_", " ")}</figcaption>
                        </figure>
                      );
                    })}
                  </div>

                  {/* Per-section 3D status / download */}
                  {isDone && model3d[part] && (
                    <Model3DStatus m={model3d[part]} part={prettyPart(part)} />
                  )}
                </div>
              );
            })}

          {/* Skeleton loading UI for the section currently being generated */}
          {isActive &&
            job.progress?.current_part &&
            !partNames.includes(job.progress.current_part) && (
              <div className="part-block">
                <div className="part-head">
                  <strong className="part-live-name">
                    {prettyPart(job.progress.current_part)}{" "}
                    <span className="badge running">generating…</span>
                  </strong>
                </div>
                <div className="skeleton-bar">
                  <div className="skeleton-bar-fill" />
                </div>
                <div className="gallery">
                  {VIEWS.map((v) => (
                    <figure key={v} className="skeleton-tile">
                      <div className="skeleton-img" />
                      <figcaption>{v.replace("_", " ")}</figcaption>
                    </figure>
                  ))}
                </div>
              </div>
            )}

          {/* Sections the model failed to produce — offer a retry */}
          {isDone &&
            (job.result?.failed_parts || [])
              .filter((fp) => !partNames.includes(fp))
              .map((fp) => (
                <div key={`failed-${fp}`} className="part-block failed-block">
                  <div className="part-head">
                    <strong className="part-title">
                      {prettyPart(fp)} <span className="badge fail">failed</span>
                    </strong>
                    <button
                      type="button"
                      className="btn small secondary"
                      disabled={Boolean(regenBusy[fp])}
                      onClick={() => handleRegeneratePart(fp)}
                    >
                      {regenBusy[fp] ? <span className="spinner-inline" /> : "🔄"} Regenerate
                    </button>
                  </div>
                  <p className="muted tiny">
                    The AI didn't return a usable image for this part. Try regenerating it.
                  </p>
                </div>
              ))}

          {/* Parts that were never started because the run was stopped —
              offer the same one-click retry instead of forcing a whole new run. */}
          {isDone &&
            (job.result?.pending_parts || [])
              .filter((pp) => !partNames.includes(pp))
              .map((pp) => (
                <div key={`pending-${pp}`} className="part-block pending-block">
                  <div className="part-head">
                    <strong className="part-title">
                      {prettyPart(pp)} <span className="badge queued">not generated</span>
                    </strong>
                    <button
                      type="button"
                      className="btn small secondary"
                      disabled={Boolean(regenBusy[pp])}
                      onClick={() => handleRegeneratePart(pp)}
                    >
                      {regenBusy[pp] ? <span className="spinner-inline" /> : "🔄"} Generate
                    </button>
                  </div>
                  <p className="muted tiny">
                    Skipped because you stopped the run. Generate it now without
                    redoing the parts you already have.
                  </p>
                </div>
              ))}

        </>
      )}

      {/* ----- 3D generation popup ----- */}
      {threeDPart && (
        <div className="modal-overlay" onClick={() => !tdBusy && setThreeDPart(null)}>
          <div className="card td-modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="modal-close"
              onClick={() => !tdBusy && setThreeDPart(null)}
            >
              ✕
            </button>
            <span className="soon-icon">🧊</span>
            <h2>Generate 3D — {prettyPart(threeDPart)}</h2>
            <p className="muted tiny">
              The 4 views of this section are sent to your chosen provider to build a 3D model.
            </p>

            <label>Provider</label>
            <select value={tdProvider} onChange={(e) => setTdProvider(e.target.value)}>
              <option value="meshy">Meshy.ai</option>
              <option value="tripo">Tripo.ai (beta)</option>
            </select>

            <label>API key</label>
            <input
              type="password"
              value={tdKey}
              onChange={(e) => setTdKey(e.target.value)}
              placeholder={
                savedKeys[tdProvider]
                  ? "Saved key on file — leave blank to use it"
                  : `Paste your ${tdProvider} API key`
              }
            />
            <label className="checkbox">
              <input
                type="checkbox"
                checked={tdSave}
                onChange={(e) => setTdSave(e.target.checked)}
              />
              Save this key to my profile
            </label>

            <button className="btn primary" disabled={tdBusy} onClick={submit3D}>
              {tdBusy ? "Submitting…" : "🧊 Generate 3D model"}
            </button>
          </div>
        </div>
      )}

      {/* ----- Lightbox popup for gallery images ----- */}
      {lightboxSrc && (
        <div className="lightbox-overlay" onClick={() => setLightboxSrc(null)}>
          <div className="lightbox-figure" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="lightbox-close"
              onClick={() => setLightboxSrc(null)}
            >
              ✕
            </button>
            <img className="lightbox-img" src={lightboxSrc} alt="Full size view" />
          </div>
        </div>
      )}
    </div>
  );
}

function statusClass(s) {
  return { queued: "queued", running: "running", succeeded: "ok", failed: "fail" }[s] || "";
}

// Per-section 3D status: shows progress while building, then download links.
function Model3DStatus({ m, part }) {
  const urls = m.modelUrls || {};
  const formats = Object.entries(urls).filter(([, u]) => typeof u === "string" && u);

  if (m.status === "succeeded" && formats.length > 0) {
    return (
      <div className="model3d-row done">
        <span className="model3d-label">🧊 3D model ready ({m.provider}):</span>
        {formats.map(([fmt, url]) => (
          <a key={fmt} href={url} target="_blank" rel="noreferrer" className="chip" download>
            ⬇ {fmt}
          </a>
        ))}
      </div>
    );
  }
  if (m.status === "failed") {
    return (
      <div className="model3d-row">
        <span className="error tiny">
          3D generation failed for {part}. {m.error || ""}
        </span>
      </div>
    );
  }
  return (
    <div className="model3d-row">
      <span className="spinner-inline" />
      <span className="muted tiny">
        Building 3D model with {m.provider}… this can take several minutes.
      </span>
    </div>
  );
}
