import { useEffect, useState } from "react";
import * as api from "../api.js";

// Upload a reference image OR generate one from text, then start a pipeline job.
// Calls onJobCreated(jobId) after a successful enqueue.
export default function GenerateForm({ onJobCreated }) {
  const [templates, setTemplates] = useState([]);
  const [name, setName] = useState("");

  // --- Tab state: "describe" (text prompt) vs "upload" (file) ---
  const [tab, setTab] = useState("describe");

  // Describe tab
  const [prompt, setPrompt] = useState("");
  const [genProvider, setGenProvider] = useState(""); // provider for Step 0
  const [referenceId, setReferenceId] = useState(null);
  const [refPreview, setRefPreview] = useState(null);
  const [generating, setGenerating] = useState(false);

  // Upload tab
  const [file, setFile] = useState(null);
  const [filePreview, setFilePreview] = useState(null);

  // Common fields
  const [template, setTemplate] = useState("");
  const [provider, setProvider] = useState(""); // provider for pipeline
  const [parts, setParts] = useState("");
  const [skip, setSkip] = useState("");
  const [meshy, setMeshy] = useState("");
  const [localOnly, setLocalOnly] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Lightbox popup
  const [lightboxSrc, setLightboxSrc] = useState(null);

  useEffect(() => {
    api
      .listTemplates()
      .then(setTemplates)
      .catch((e) => setError(e.message));
  }, []);

  // --- Describe tab handlers ---
  async function handleGenerate() {
    if (!prompt.trim()) {
      setError("Please describe your character.");
      return;
    }
    setError("");
    setGenerating(true);
    try {
      const res = await api.generateReference(
        prompt.trim(),
        genProvider || undefined
      );
      setReferenceId(res.reference_id);
      // Build the preview URL with auth token
      const token = api.getToken();
      const imgUrl = api.getReferenceImageUrl(res.reference_id);
      // Fetch with auth to display as blob URL
      const imgRes = await fetch(imgUrl, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (imgRes.ok) {
        const blob = await imgRes.blob();
        setRefPreview(URL.createObjectURL(blob));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setGenerating(false);
    }
  }

  function handleRegenerate() {
    setReferenceId(null);
    setRefPreview(null);
    handleGenerate();
  }

  // --- Upload tab handlers ---
  function onFile(e) {
    const f = e.target.files?.[0] || null;
    setFile(f);
    setFilePreview(f ? URL.createObjectURL(f) : null);
    // Clear describe tab state when switching to upload
    setReferenceId(null);
    setRefPreview(null);
  }

  // --- Submit pipeline job ---
  async function submit(e) {
    e.preventDefault();
    setError("");

    const hasUpload = tab === "upload" && file;
    const hasRef = tab === "describe" && referenceId;

    if (!hasUpload && !hasRef) {
      setError(
        tab === "describe"
          ? "Generate a reference image first, then start the pipeline."
          : "Please choose a reference image."
      );
      return;
    }
    if (!name.trim()) {
      setError("Please enter a character name.");
      return;
    }
    if (meshy && localOnly) {
      setError("Meshy needs public URLs — uncheck 'local only' to use it.");
      return;
    }

    const fd = new FormData();
    fd.append("name", name.trim());

    if (hasUpload) {
      fd.append("image", file);
    } else {
      fd.append("reference_id", referenceId);
    }

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

  const activePreview =
    tab === "describe" ? refPreview : filePreview;
  const canSubmit =
    tab === "describe" ? !!referenceId : !!file;

  return (
    <form className="card" onSubmit={submit}>
      <h2>New generation</h2>

      {/* ----- Tab switcher ----- */}
      <div className="tab-bar">
        <button
          type="button"
          className={`tab-btn${tab === "describe" ? " active" : ""}`}
          onClick={() => setTab("describe")}
        >
          ✨ Describe Character
        </button>
        <button
          type="button"
          className={`tab-btn${tab === "upload" ? " active" : ""}`}
          onClick={() => setTab("upload")}
        >
          📁 Upload Image
        </button>
      </div>

      {/* ----- Describe tab ----- */}
      {tab === "describe" && (
        <div className="tab-content">
          <label>Character description</label>
          <textarea
            className="prompt-textarea"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. An Indian woman in a red saree, age 30, medium brown skin, with a bindi and gold earrings"
            rows={3}
          />

          <label>Image provider (for reference generation)</label>
          <select
            value={genProvider}
            onChange={(e) => setGenProvider(e.target.value)}
          >
            <option value="">Server default</option>
            <option value="vertex">Vertex AI</option>
            <option value="gemini">Gemini API</option>
          </select>

          {!referenceId && (
            <button
              type="button"
              className="btn secondary"
              disabled={generating || !prompt.trim()}
              onClick={handleGenerate}
            >
              {generating ? (
                <span className="spinner-inline" />
              ) : null}
              {generating ? " Generating…" : "Generate Reference Image"}
            </button>
          )}

          {refPreview && (
            <div className="ref-preview">
              <img
                className="preview clickable"
                src={refPreview}
                alt="Generated reference"
                onClick={() => setLightboxSrc(refPreview)}
                title="Click to view full size"
              />
              <div className="ref-actions">
                <button
                  type="button"
                  className="btn secondary small"
                  onClick={handleRegenerate}
                  disabled={generating}
                >
                  🔄 Regenerate
                </button>
                <span className="ref-ok">✅ Reference ready</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ----- Upload tab ----- */}
      {tab === "upload" && (
        <div className="tab-content">
          <label>Reference image</label>
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={onFile}
          />
          {filePreview && (
            <img
              className="preview clickable"
              src={filePreview}
              alt="preview"
              onClick={() => setLightboxSrc(filePreview)}
              title="Click to view full size"
            />
          )}
        </div>
      )}

      {/* ----- Common fields ----- */}
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
          <select
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
          >
            <option value="">(default)</option>
            {templates.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>

          <label>Image provider (for pipeline)</label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">Server default</option>
            <option value="vertex">Vertex AI</option>
            <option value="gemini">Gemini API</option>
          </select>
        </div>

        <div>
          {activePreview && (
            <>
              <label>Reference preview</label>
              <img
                className="preview clickable"
                src={activePreview}
                alt="reference"
                onClick={() => setLightboxSrc(activePreview)}
                title="Click to view full size"
              />
            </>
          )}
        </div>
      </div>

      <details className="advanced">
        <summary>Advanced options</summary>
        <label>Only parts (comma-separated — cheap test)</label>
        <input
          value={parts}
          onChange={(e) => setParts(e.target.value)}
          placeholder="e.g. hair"
        />

        <label>Skip parts (comma-separated)</label>
        <input
          value={skip}
          onChange={(e) => setSkip(e.target.value)}
          placeholder="e.g. goggles,headphone"
        />

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

      <button
        className="btn primary"
        disabled={busy || !canSubmit}
        type="submit"
      >
        {busy ? "Starting…" : "Generate"}
      </button>

      {/* ----- Lightbox popup ----- */}
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
            alt="Full size preview"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </form>
  );
}
