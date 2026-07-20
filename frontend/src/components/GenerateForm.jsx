import { useEffect, useState } from "react";
import * as api from "../api.js";

// Upload a reference image + options and start a generation job.
// Calls onJobCreated(jobId) after a successful enqueue.
export default function GenerateForm({ onJobCreated }) {
  const [templates, setTemplates] = useState([]);
  const [name, setName] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [template, setTemplate] = useState("");
  const [provider, setProvider] = useState(""); // "" = server default
  const [parts, setParts] = useState("");
  const [skip, setSkip] = useState("");
  const [meshy, setMeshy] = useState("");
  const [localOnly, setLocalOnly] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .listTemplates()
      .then(setTemplates)
      .catch((e) => setError(e.message));
  }, []);

  function onFile(e) {
    const f = e.target.files?.[0] || null;
    setFile(f);
    setPreview(f ? URL.createObjectURL(f) : null);
  }

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (!file) {
      setError("Please choose a reference image.");
      return;
    }
    if (meshy && localOnly) {
      setError("Meshy needs public URLs — uncheck 'local only' to use it.");
      return;
    }

    const fd = new FormData();
    fd.append("name", name.trim());
    fd.append("image", file);
    if (template) fd.append("template", template);
    if (provider) fd.append("provider", provider);
    if (parts.trim()) fd.append("parts", parts.trim());
    if (skip.trim()) fd.append("skip", skip.trim());
    if (meshy.trim()) fd.append("meshy", meshy.trim());
    fd.append("local_only", String(localOnly));

    setBusy(true);
    try {
      const res = await api.createCharacter(fd);
      onJobCreated(res.job_id);
      // Reset the volatile bits so the next run starts clean.
      setParts("");
      setSkip("");
      setMeshy("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New generation</h2>

      <div className="grid2">
        <div>
          <label>Character name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. kamla"
            required
          />

          <label>Template</label>
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="">(default)</option>
            {templates.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>

          <label>Image provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="">Server default</option>
            <option value="vertex">Vertex AI</option>
            <option value="gemini">Gemini API</option>
          </select>
        </div>

        <div>
          <label>Reference image</label>
          <input type="file" accept="image/png,image/jpeg,image/webp" onChange={onFile} />
          {preview && <img className="preview" src={preview} alt="preview" />}
        </div>
      </div>

      <details className="advanced">
        <summary>Advanced options</summary>
        <label>Only parts (comma-separated — cheap test)</label>
        <input value={parts} onChange={(e) => setParts(e.target.value)} placeholder="e.g. hair" />

        <label>Skip parts (comma-separated)</label>
        <input value={skip} onChange={(e) => setSkip(e.target.value)} placeholder="e.g. goggles,headphone" />

        <label>Submit to Meshy 3D (comma-separated parts)</label>
        <input
          value={meshy}
          onChange={(e) => setMeshy(e.target.value)}
          placeholder="e.g. hair,saree"
          disabled={localOnly}
        />

        <label className="checkbox">
          <input
            type="checkbox"
            checked={localOnly}
            onChange={(e) => setLocalOnly(e.target.checked)}
          />
          Local only (skip cloud upload; no gallery/Meshy)
        </label>
      </details>

      {error && <div className="error">{error}</div>}

      <button className="btn primary" disabled={busy} type="submit">
        {busy ? "Starting…" : "Generate"}
      </button>
    </form>
  );
}
