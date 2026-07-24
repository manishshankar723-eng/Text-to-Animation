// Storyboard asset setup (Stage B2 — prop & background consistency).
// Shows the key props and backgrounds the AI found in the script. For each, the
// user can edit the visual description and generate (or upload) a reference
// image. Those references are fed into every panel the asset appears in, so the
// same slipper / bedroom looks identical across shots. This step is optional —
// any asset can be skipped, mirroring the Cast page.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import ImageLightbox from "./ImageLightbox.jsx";

// Small labelled badge so props and backgrounds are visually distinct.
const CATEGORY_META = {
  prop: { label: "Prop", icon: "🎒" },
  background: { label: "Background", icon: "🏙️" },
};

export default function StoryboardAssets({ assets, onBack, onGenerate, busy }) {
  const [items, setItems] = useState(() =>
    (assets || []).map((a) => ({
      name: a.name,
      category: a.category === "background" ? "background" : "prop",
      description: a.description || "",
      referenceId: null,
      previewUrl: null,
      busy: false,
      error: "",
    }))
  );
  const [lightbox, setLightbox] = useState(null);
  const blobUrls = useRef([]);
  const fileInputs = useRef([]);

  // Revoke any object URLs we created on unmount.
  useEffect(() => {
    return () => blobUrls.current.forEach((u) => URL.revokeObjectURL(u));
  }, []);

  function patch(i, fields) {
    setItems((c) => c.map((it, idx) => (idx === i ? { ...it, ...fields } : it)));
  }

  async function generateRef(i) {
    const it = items[i];
    if (it.busy) return;
    patch(i, { busy: true, error: "" });
    try {
      const prompt = it.description.trim() || it.name;
      const res = await api.generateAssetReference(prompt, it.category);
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
    if (!file || items[i].busy) return;
    patch(i, { busy: true, error: "" });
    try {
      // Uploads reuse the shared reference-upload endpoint (any image → ref).
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
    items.forEach((it) => {
      if (it.referenceId) refs[it.name] = it.referenceId;
    });
    onGenerate(refs);
  }

  const readyCount = items.filter((c) => c.referenceId).length;

  return (
    <div className="workflow-head-wrap sb-cast">
      <div className="workflow-header">
        <span className="wf-icon">🎬</span>
        <div>
          <h1 className="wf-title">Props &amp; backgrounds</h1>
          <p className="muted">
            Lock the key objects and locations so they look the same in every
            panel (e.g. the same slipper, the same bedroom). This step is optional
            — you can skip any asset.
          </p>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="card">
          <p className="muted">
            No recurring props or backgrounds were detected in this script. You can
            generate the storyboard without asset references.
          </p>
        </div>
      ) : (
        <div className="cast-grid">
          {items.map((it, i) => {
            const meta = CATEGORY_META[it.category] || CATEGORY_META.prop;
            return (
              <div className="card cast-card" key={i}>
                <div
                  className={`cast-portrait ${it.previewUrl ? "clickable" : ""}`}
                  onClick={() => it.previewUrl && setLightbox(it.previewUrl)}
                  title={it.previewUrl ? "Click to enlarge" : undefined}
                >
                  {it.previewUrl ? (
                    <img src={it.previewUrl} alt={it.name} />
                  ) : it.busy ? (
                    <div className="cast-portrait-empty">
                      <span className="spinner" />
                    </div>
                  ) : (
                    <div className="cast-portrait-empty">{meta.icon}</div>
                  )}
                </div>
                <div className="cast-body">
                  <div className="cast-name">
                    {it.name}
                    <span className={`asset-badge asset-badge-${it.category}`}>
                      {meta.icon} {meta.label}
                    </span>
                  </div>
                  <textarea
                    className="prompt-textarea cast-desc"
                    value={it.description}
                    placeholder={
                      it.category === "background"
                        ? "Describe this location / set…"
                        : "Describe how this object looks…"
                    }
                    onChange={(e) => patch(i, { description: e.target.value })}
                  />
                  {it.error && <div className="error">{it.error}</div>}
                  <div className="cast-actions">
                    <button
                      type="button"
                      className="btn secondary cast-btn"
                      disabled={it.busy}
                      onClick={() => generateRef(i)}
                    >
                      {it.busy ? (
                        <>
                          <span className="spinner-inline" /> Working…
                        </>
                      ) : it.referenceId ? (
                        "🔄 Regenerate"
                      ) : (
                        "✨ Generate"
                      )}
                    </button>
                    <button
                      type="button"
                      className="btn secondary cast-btn"
                      disabled={it.busy}
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
            );
          })}
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

      <ImageLightbox src={lightbox} alt="Asset reference" onClose={() => setLightbox(null)} />
    </div>
  );
}
