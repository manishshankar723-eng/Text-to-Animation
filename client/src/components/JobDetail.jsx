import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";

const VIEWS = ["front", "left", "three_quarter", "back"];

// Detail panel for one job: polls until done, then shows the gallery, per-part
// prompt view/edit, single-part regeneration, zip download, and Meshy 3D submission.
export default function JobDetail({ jobId, onChanged }) {
  const [job, setJob] = useState(null);
  const [assets, setAssets] = useState(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [meshySel, setMeshySel] = useState([]);
  const [meshyKey, setMeshyKey] = useState("");
  const [meshyMsg, setMeshyMsg] = useState("");

  // Per-part prompt editing & regeneration state
  const [editedPrompts, setEditedPrompts] = useState({});
  const [regenBusy, setRegenBusy] = useState({}); // { [partName]: boolean }
  const [cacheBust, setCacheBust] = useState(Date.now());

  // Lightbox popup for gallery images
  const [lightboxSrc, setLightboxSrc] = useState(null);

  const load = useCallback(async () => {
    try {
      const j = await api.getJob(jobId);
      setJob(j);
      setError("");
      if (j.status === "succeeded" && j.kind === "generate") {
        try {
          setAssets(await api.getAssets(jobId));
        } catch {
          setAssets(null);
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
    setMeshySel([]);
    setMeshyKey("");
    setMeshyMsg("");
    setEditedPrompts({});
    setRegenBusy({});
    setCacheBust(Date.now());
    load();
  }, [jobId, load]);

  // Poll while the job is active.
  useEffect(() => {
    if (!job) return;
    if (job.status !== "queued" && job.status !== "running") {
      onChanged?.();
      return;
    }
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [job, load, onChanged]);

  if (!job) {
    return (
      <div className="card detail">
        {error ? <div className="error">{error}</div> : <p className="muted">Loading…</p>}
      </div>
    );
  }

  const isActive = job.status === "queued" || job.status === "running";
  const partNames = assets ? Object.keys(assets.parts) : [];

  function toggleMeshy(part) {
    setMeshySel((s) => (s.includes(part) ? s.filter((p) => p !== part) : [...s, part]));
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

  async function handleRegeneratePart(partName) {
    setRegenBusy((prev) => ({ ...prev, [partName]: true }));
    setError("");
    try {
      const customPrompt = editedPrompts[partName];
      const provider = job.params?.provider;
      const updatedJob = await api.regeneratePart(jobId, partName, customPrompt, provider);
      setJob(updatedJob);
      setAssets(await api.getAssets(jobId));
      setCacheBust(Date.now());
      onChanged?.();
    } catch (e) {
      setError(`Failed to regenerate '${partName}': ${e.message}`);
    } finally {
      setRegenBusy((prev) => ({ ...prev, [partName]: false }));
    }
  }

  async function runMeshy() {
    setMeshyMsg("");
    setError("");
    try {
      if (!meshyKey.trim()) {
        setError("Please enter your Meshy API key.");
        return;
      }
      const res = await api.submitMeshy(jobId, meshySel, meshyKey.trim());
      setMeshyMsg(`3D job started: ${res.job_id}. Watch it in the jobs list.`);
      setMeshySel([]);
      onChanged?.();
    } catch (e) {
      setError(e.message);
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
        <div className="progress">
          <div className="spinner" />
          <span>Working… this can take a few minutes.</span>
        </div>
      )}

      {job.status === "failed" && (
        <div className="error">{job.error || "Job failed."}</div>
      )}

      {error && <div className="error">{error}</div>}

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

      {/* Generation result */}
      {job.status === "succeeded" && job.kind === "generate" && (
        <>
          <div className="actions">
            <button className="btn primary" onClick={download} disabled={downloading}>
              {downloading ? "Preparing…" : "⬇ Download zip"}
            </button>
          </div>

          {assets && (
            <>
              {partNames.map((part) => {
                const currentPrompt =
                  editedPrompts[part] ?? job.result?.prompts?.[part] ?? "";
                const isRegening = Boolean(regenBusy[part]);

                return (
                  <div key={part} className="part-block">
                    <div className="part-head">
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={meshySel.includes(part)}
                          onChange={() => toggleMeshy(part)}
                        />
                        <strong>{part}</strong> — select for 3D
                      </label>
                    </div>

                    {/* Per-part prompt view, edit & regenerate section */}
                    <details className="prompt-details" open={part === "fullbody" || part === "face"}>
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

                    <div className="gallery">
                      {VIEWS.map((v) => {
                        const imgUrl = getPartViewUrl(part, v);
                        if (!imgUrl) return null;
                        return (
                          <figure key={v}>
                            <img
                              src={imgUrl}
                              alt={`${part} ${v}`}
                              loading="lazy"
                              className="clickable"
                              onClick={() => setLightboxSrc(imgUrl)}
                              title="Click to view full size"
                            />
                            <figcaption>{v.replace("_", " ")}</figcaption>
                          </figure>
                        );
                      })}
                    </div>
                  </div>
                );
              })}

              <div className="meshy-bar">
                <input
                  type="password"
                  className="meshy-key-input"
                  placeholder="Paste your Meshy API key"
                  value={meshyKey}
                  onChange={(e) => setMeshyKey(e.target.value)}
                />
                <button
                  className="btn"
                  disabled={meshySel.length === 0 || !meshyKey.trim()}
                  onClick={runMeshy}
                >
                  🧊 Generate 3D for {meshySel.length || "0"} selected
                </button>
                {meshyMsg && <span className="ok-msg">{meshyMsg}</span>}
              </div>
            </>
          )}
        </>
      )}

      {/* ----- Lightbox popup for gallery images ----- */}
      {lightboxSrc && (
        <div className="lightbox-overlay" onClick={() => setLightboxSrc(null)}>
          <button
            type="button"
            className="lightbox-close"
            onClick={() => setLightboxSrc(null)}
          >
            ✕
          </button>
          <img
            className="lightbox-img"
            src={lightboxSrc}
            alt="Full size view"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}

function statusClass(s) {
  return { queued: "queued", running: "running", succeeded: "ok", failed: "fail" }[s] || "";
}
