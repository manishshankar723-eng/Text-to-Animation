// Storyboard cast setup (Stage B — character consistency).
// Shows the characters the AI found in the script. For each, the user can edit
// the visual description and generate a reference image (reusing the existing
// character-reference endpoint). Those references are then fed into every panel
// the character appears in, so they stay consistent. Generating refs is optional.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import ImageLightbox from "./ImageLightbox.jsx";

export default function StoryboardCast({ characters, onBack, onGenerate, busy }) {
  const [lightbox, setLightbox] = useState(null);
  const [cast, setCast] = useState(() =>
    (characters || []).map((c) => ({
      name: c.name,
      description: c.description || "",
      referenceId: null,
      previewUrl: null,
      busy: false,
      error: "",
    }))
  );
  const blobUrls = useRef([]);
  const fileInputs = useRef([]);

  // Revoke any object URLs we created on unmount.
  useEffect(() => {
    return () => blobUrls.current.forEach((u) => URL.revokeObjectURL(u));
  }, []);

  function patch(i, fields) {
    setCast((c) => c.map((ch, idx) => (idx === i ? { ...ch, ...fields } : ch)));
  }

  async function generateRef(i) {
    const ch = cast[i];
    if (ch.busy) return;
    patch(i, { busy: true, error: "" });
    try {
      const prompt = ch.description.trim() || `A character named ${ch.name}`;
      const res = await api.generateReference(prompt);
      // Fetch the generated image as an authed blob for preview.
      const token = api.getToken();
      const imgRes = await fetch(api.getReferenceImageUrl(res.reference_id), {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      let previewUrl = null;
      if (imgRes.ok) {
        previewUrl = URL.createObjectURL(await imgRes.blob());
        blobUrls.current.push(previewUrl);
      }
      patch(i, { referenceId: res.reference_id, previewUrl, busy: false });
    } catch (e) {
      patch(i, { busy: false, error: e.message });
    }
  }

  async function uploadRef(i, file) {
    if (!file || cast[i].busy) return;
    patch(i, { busy: true, error: "" });
    try {
      const res = await api.uploadReference(file);
      const previewUrl = URL.createObjectURL(file);
      blobUrls.current.push(previewUrl);
      patch(i, { referenceId: res.reference_id, previewUrl, busy: false });
    } catch (e) {
      patch(i, { busy: false, error: e.message });
    }
  }

  function handleGenerate() {
    const refs = {};
    cast.forEach((ch) => {
      if (ch.referenceId) refs[ch.name] = ch.referenceId;
    });
    onGenerate(refs);
  }

  const readyCount = cast.filter((c) => c.referenceId).length;

  return (
    <div className="workflow-head-wrap sb-cast">
      <div className="workflow-header">
        <span className="wf-icon">🎭</span>
        <div>
          <h1 className="wf-title">Set up your cast</h1>
          <p className="muted">
            Generate a reference for each character so they look the same in every
            panel. This step is optional — you can skip any character.
          </p>
        </div>
      </div>

      {cast.length === 0 ? (
        <div className="card">
          <p className="muted">
            No named characters were found in this script. You can generate the
            storyboard without character references.
          </p>
        </div>
      ) : (
        <div className="cast-grid">
          {cast.map((ch, i) => (
            <div className="card cast-card" key={i}>
              <div
                className={`cast-portrait ${ch.previewUrl ? "clickable" : ""}`}
                onClick={() => ch.previewUrl && setLightbox(ch.previewUrl)}
                title={ch.previewUrl ? "Click to enlarge" : undefined}
              >
                {ch.previewUrl ? (
                  <img src={ch.previewUrl} alt={ch.name} />
                ) : ch.busy ? (
                  <div className="cast-portrait-empty">
                    <span className="spinner" />
                  </div>
                ) : (
                  <div className="cast-portrait-empty">🎭</div>
                )}
              </div>
              <div className="cast-body">
                <div className="cast-name">{ch.name}</div>
                <textarea
                  className="prompt-textarea cast-desc"
                  value={ch.description}
                  placeholder="Describe how this character looks…"
                  onChange={(e) => patch(i, { description: e.target.value })}
                />
                {ch.error && <div className="error">{ch.error}</div>}
                <div className="cast-actions">
                  <button
                    type="button"
                    className="btn secondary cast-btn"
                    disabled={ch.busy}
                    onClick={() => generateRef(i)}
                  >
                    {ch.busy ? (
                      <>
                        <span className="spinner-inline" /> Working…
                      </>
                    ) : ch.referenceId ? (
                      "🔄 Regenerate"
                    ) : (
                      "✨ Generate"
                    )}
                  </button>
                  <button
                    type="button"
                    className="btn secondary cast-btn"
                    disabled={ch.busy}
                    onClick={() => fileInputs.current[i]?.click()}
                  >
                    📁 Upload
                  </button>
                  <input
                    ref={(el) => (fileInputs.current[i] = el)}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    onChange={(e) => {
                      uploadRef(i, e.target.files?.[0]);
                      e.target.value = ""; // allow re-selecting the same file
                    }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="review-actions board-actions">
        <button type="button" className="btn" onClick={onBack} disabled={busy}>
          ← Back
        </button>
        <button
          type="button"
          className="btn primary"
          onClick={handleGenerate}
          disabled={busy}
        >
          {busy ? (
            <>
              <span className="spinner-inline" /> Starting…
            </>
          ) : readyCount > 0 ? (
            `🎬 Generate panels · ${readyCount} ref${readyCount === 1 ? "" : "s"}`
          ) : (
            "🎬 Generate panels (skip refs)"
          )}
        </button>
      </div>

      <ImageLightbox src={lightbox} alt="Character reference" onClose={() => setLightbox(null)} />
    </div>
  );
}
