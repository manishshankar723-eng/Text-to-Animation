import { useEffect, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
// ⚠ THE FILTER ONLY, NOT THE ROW LIST. Every other workflow's library became a
// full-width table of rows (LibraryList.jsx); this one cannot, because it lives
// in the 380px left column beside the job detail pane — four columns do not fit
// in it and never will. What it DOES share is the Filter box, so finding a job
// by name works the same way here as it does everywhere else.
import { matchesFilter } from "./LibraryList.jsx";

const STATUS_CLASS = {
  queued: "badge queued",
  running: "badge running",
  succeeded: "badge ok",
  failed: "badge fail",
};

// List of the current user's Text-to-Image jobs — character runs and their 3D
// submissions ONLY. Storyboards belong to the Script → Storyboard workflow and
// are listed in "Your Storyboards"; they used to appear here too.
// Auto-refreshes while any job is active.
export default function JobList({ selectedId, onSelect, refreshKey }) {
  const [jobs, setJobs] = useState([]);
  // What's typed in the Filter box. A pure VIEW of `jobs` — nothing is
  // re-fetched, and the poll below keeps updating the full list underneath it.
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  // Two-step delete: the row asks for confirmation before anything is removed.
  const [confirmId, setConfirmId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  async function load() {
    try {
      setJobs(await api.listJobs(api.CHARACTER_JOB_KINDS));
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }

  async function remove(jobId) {
    setDeletingId(jobId);
    setError("");
    try {
      await api.deleteJob(jobId);
      // Clear the detail pane if we just deleted what it was showing.
      if (jobId === selectedId) onSelect(null);
      setConfirmId(null);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setDeletingId(null);
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

  // Matched against what a user types looking for a run: the character's name
  // and the template it was generated from.
  const shown = jobs.filter((j) =>
    matchesFilter(query, j.character_name, j.template)
  );

  return (
    <div className="card joblist">
      <div className="joblist-head">
        <h2>Your jobs</h2>
        <button className="btn ghost small" onClick={load}>
          Refresh
        </button>
      </div>
      {/* The same Filter box the project libraries carry, on its own line
          because this column is too narrow to put it beside the heading. Hidden
          until there is something to filter — one job and a search box is just
          furniture. */}
      {jobs.length > 1 && (
        <label className="lib-filter joblist-filter">
          <span className="lib-filter-label">Filter</span>
          <input
            type="text"
            className="lib-filter-input"
            value={query}
            placeholder="Filter jobs"
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className="lib-filter-clear"
              title="Clear the filter"
              onClick={() => setQuery("")}
            >
              ✕
            </button>
          )}
        </label>
      )}
      {/* Only surface errors when we have nothing to show — a transient poll
          failure shouldn't hide the jobs we already loaded. */}
      {error && jobs.length === 0 && <div className="error">{error}</div>}
      {!error && jobs.length === 0 && <p className="muted">No jobs yet.</p>}
      {/* Filtered down to nothing is NOT the same as having nothing — saying
          "No jobs yet" here would look like the account had been emptied. */}
      {jobs.length > 0 && shown.length === 0 && (
        <p className="muted tiny">
          Nothing matches “{query}”.{" "}
          <button
            type="button"
            className="lib-linkish"
            onClick={() => setQuery("")}
          >
            Clear the filter
          </button>{" "}
          to see all {jobs.length}.
        </p>
      )}
      <ul>
        {shown.map((j) => (
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
              <span className="job-row-right">
                <span className={STATUS_CLASS[j.status] || "badge"}>{j.status}</span>
                <button
                  type="button"
                  className="job-del"
                  title="Delete this job"
                  disabled={deletingId === j.job_id}
                  onClick={(e) => {
                    e.stopPropagation(); // don't select the row
                    setConfirmId(confirmId === j.job_id ? null : j.job_id);
                  }}
                >
                  <Icon name="trash" />
                </button>
              </span>
            </div>
            <div className="muted tiny">
              {j.template || "default"} · {j.created_at?.slice(0, 19).replace("T", " ")}
            </div>

            {confirmId === j.job_id && (
              <div className="job-confirm" onClick={(e) => e.stopPropagation()}>
                <span className="tiny">
                  Delete this job and its images? This can't be undone.
                </span>
                <span className="job-confirm-actions">
                  <button
                    type="button"
                    className="btn small danger-btn"
                    disabled={deletingId === j.job_id}
                    onClick={() => remove(j.job_id)}
                  >
                    {deletingId === j.job_id ? "Deleting…" : "Delete"}
                  </button>
                  <button
                    type="button"
                    className="btn ghost small"
                    disabled={deletingId === j.job_id}
                    onClick={() => setConfirmId(null)}
                  >
                    Cancel
                  </button>
                </span>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
