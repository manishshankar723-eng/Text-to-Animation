import { useEffect, useState } from "react";
import * as api from "../api.js";

const STATUS_CLASS = {
  queued: "badge queued",
  running: "badge running",
  succeeded: "badge ok",
  failed: "badge fail",
};

// List of the current user's jobs. Auto-refreshes while any job is active.
export default function JobList({ selectedId, onSelect, refreshKey }) {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");

  async function load() {
    try {
      setJobs(await api.listJobs());
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, [refreshKey]);

  // Poll the list while anything is still in progress.
  useEffect(() => {
    const active = jobs.some((j) => j.status === "queued" || j.status === "running");
    if (!active) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [jobs]);

  return (
    <div className="card joblist">
      <div className="joblist-head">
        <h2>Your jobs</h2>
        <button className="btn ghost small" onClick={load}>
          Refresh
        </button>
      </div>
      {/* Only surface errors when we have nothing to show — a transient poll
          failure shouldn't hide the jobs we already loaded. */}
      {error && jobs.length === 0 && <div className="error">{error}</div>}
      {!error && jobs.length === 0 && <p className="muted">No jobs yet.</p>}
      <ul>
        {jobs.map((j) => (
          <li
            key={j.job_id}
            className={j.job_id === selectedId ? "selected" : ""}
            onClick={() => onSelect(j.job_id)}
          >
            <div className="job-row">
              <span className="job-name">
                {j.character_name}
                {j.kind === "meshy" ? " · 3D" : ""}
              </span>
              <span className={STATUS_CLASS[j.status] || "badge"}>{j.status}</span>
            </div>
            <div className="muted tiny">
              {j.template || "default"} · {j.created_at?.slice(0, 19).replace("T", " ")}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
