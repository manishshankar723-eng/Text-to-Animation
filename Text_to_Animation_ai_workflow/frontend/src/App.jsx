import { useState } from "react";
import * as api from "./api.js";
import Login from "./components/Login.jsx";
import GenerateForm from "./components/GenerateForm.jsx";
import JobList from "./components/JobList.jsx";
import JobDetail from "./components/JobDetail.jsx";

export default function App() {
  const [email, setEmail] = useState(api.getEmail());
  const [authed, setAuthed] = useState(Boolean(api.getToken()));
  const [selectedId, setSelectedId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  function refreshJobs() {
    setRefreshKey((k) => k + 1);
  }

  function onAuthed(mail) {
    setEmail(mail);
    setAuthed(true);
  }

  function logout() {
    api.clearSession();
    setAuthed(false);
    setEmail(null);
    setSelectedId(null);
  }

  function onJobCreated(jobId) {
    setSelectedId(jobId);
    refreshJobs();
  }

  if (!authed) {
    return <Login onAuthed={onAuthed} />;
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand small">🎭 Character Asset Studio</span>
        <div className="topbar-right">
          <span className="muted">{email}</span>
          <button className="btn ghost small" onClick={logout}>
            Log out
          </button>
        </div>
      </header>

      <main className="layout">
        <section className="col-left">
          <GenerateForm onJobCreated={onJobCreated} />
          <JobList
            selectedId={selectedId}
            onSelect={setSelectedId}
            refreshKey={refreshKey}
          />
        </section>

        <section className="col-right">
          {selectedId ? (
            <JobDetail jobId={selectedId} onChanged={refreshJobs} />
          ) : (
            <div className="card placeholder">
              <p className="muted">Select a job, or start a new generation.</p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
