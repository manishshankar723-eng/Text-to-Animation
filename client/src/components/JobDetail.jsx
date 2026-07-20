import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";

const VIEWS = ["front", "left", "three_quarter", "back"];

// Detail panel for one job: polls until done, then shows the gallery, a zip
// download, and a Meshy 3D submission control.
export default function JobDetail({ jobId, onChanged }) {
  const [job, setJob] = useState(null);
  const [assets, setAssets] = useState(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [meshySel, setMeshySel] = useState([]);
  const [meshyKey, setMeshyKey] = useState("");
  const [meshyMsg, setMeshyMsg] = useState("");

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

  // Reset when the selected job changes.
  useEffect(() => {
    setJob(null);
    setAssets(null);
    setMeshySel([]);
    setMeshyKey("");
    setMeshyMsg("");
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
      await api.downloadZip(jobId, `${job.character_name}_assets.zip`);
    } catch (e) {
      setError(e.message);
    } finally {
      setDownloading(false);
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

          {assets?.is_local && (
            <p className="muted">
              This was a local-only run — images are on the server's disk, so no
              gallery preview here. Use the zip.
            </p>
          )}

          {assets && !assets.is_local && (
            <>
              {partNames.map((part) => (
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
                  <div className="gallery">
                    {VIEWS.map((v) =>
                      assets.parts[part][v] ? (
                        <figure key={v}>
                          <img src={assets.parts[part][v]} alt={`${part} ${v}`} loading="lazy" />
                          <figcaption>{v.replace("_", " ")}</figcaption>
                        </figure>
                      ) : null
                    )}
                  </div>
                </div>
              ))}

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
    </div>
  );
}

function statusClass(s) {
  return { queued: "queued", running: "running", succeeded: "ok", failed: "fail" }[s] || "";
}
