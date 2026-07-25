// Storyboard asset setup (Stage B2 — prop & background consistency).
// Shows the key props and backgrounds the AI found in the script. For each, the
// user can edit the visual description and generate (or upload) a reference
// image. Those references are fed into every panel the asset appears in, so the
// same slipper / bedroom looks identical across shots. This step is optional —
// any asset can be skipped, mirroring the Cast page.
import { useRef, useState } from "react";
import * as api from "../api.js";
import ImageLightbox from "./ImageLightbox.jsx";

// Small labelled badge so props and backgrounds are visually distinct.
const CATEGORY_META = {
  prop: { label: "Prop", icon: "🎒" },
  background: { label: "Background", icon: "🏙️" },
};

// Fields worth keeping when the user steps away from this page (see `saved`).
// `uploaded` rides along so "Generate all" keeps skipping the user's own images
// even after they leave and come back.
const DURABLE = ["description", "referenceId", "previewUrl", "uploaded"];

export default function StoryboardAssets({
  assets,
  saved,
  onSave,
  // The script's region/period/culture — a hut, a pot and a temple all differ
  // by culture, so every prop/background reference carries it too.
  world,
  onBack,
  onGenerate,
  busy,
}) {
  // `saved` holds what the user already set up for these assets on an earlier
  // visit (the workflow owns it, so it outlives this component). Seed from it,
  // otherwise fall back to the breakdown's description.
  const [items, setItems] = useState(() =>
    (assets || []).map((a) => {
      const prev = saved?.[(a.name || "").trim().toLowerCase()] || {};
      return {
        name: a.name,
        category: a.category === "background" ? "background" : "prop",
        description: prev.description ?? a.description ?? "",
        referenceId: prev.referenceId ?? null,
        previewUrl: prev.previewUrl ?? null,
        uploaded: prev.uploaded ?? false, // user's own image → never auto-generated
        busy: false,
        error: "",
      };
    })
  );
  const [lightbox, setLightbox] = useState(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const fileInputs = useRef([]);

  function patch(i, fields) {
    setItems((c) => c.map((it, idx) => (idx === i ? { ...it, ...fields } : it)));
    // Mirror the durable fields up to the workflow so leaving this step doesn't
    // discard the reference the user just set up.
    const durable = {};
    for (const k of DURABLE) if (k in fields) durable[k] = fields[k];
    if (Object.keys(durable).length > 0) onSave?.(items[i].name, durable);
  }

  // Generate one reference. Takes the item snapshot so a bulk loop doesn't rely
  // on React state having updated between iterations.
  async function runGenerate(i, item) {
    patch(i, { busy: true, error: "" });
    try {
      const prompt = item.description.trim() || item.name;
      const res = await api.generateAssetReference(prompt, item.category, world);
      // Fetch the generated image as an authed blob for preview.
      const token = api.getToken();
      const imgRes = await fetch(api.getReferenceImageUrl(res.reference_id), {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      let previewUrl = null;
      if (imgRes.ok) previewUrl = URL.createObjectURL(await imgRes.blob());
      // uploaded:false — a generated ref replaces any prior upload flag.
      patch(i, { referenceId: res.reference_id, previewUrl, uploaded: false, busy: false });
    } catch (e) {
      patch(i, { busy: false, error: e.message });
    }
  }

  function generateRef(i) {
    if (items[i].busy) return;
    runGenerate(i, items[i]);
  }

  async function uploadRef(i, file) {
    if (!file || items[i].busy) return;
    patch(i, { busy: true, error: "" });
    try {
      // Uploads reuse the shared reference-upload endpoint (any image → ref).
      const res = await api.uploadReference(file);
      const previewUrl = URL.createObjectURL(file);
      // uploaded:true so bulk "Generate all" / "Retry failed" leaves it alone.
      patch(i, { referenceId: res.reference_id, previewUrl, uploaded: true, error: "", busy: false });
    } catch (e) {
      patch(i, { busy: false, error: e.message });
    }
  }

  // An asset still needs generating if it has no reference AND the user hasn't
  // uploaded their own image for it.
  const needsGen = (c) => !c.referenceId && !c.uploaded;
  const isFailed = (c) => Boolean(c.error) && !c.uploaded;

  const toGenCount = items.filter(needsGen).length;
  const failedCount = items.filter(isFailed).length;

  // Generate references one at a time (gentler on the image quota). Snapshot the
  // targets up front so the set doesn't shift as state updates.
  async function runBulk(predicate) {
    if (bulkBusy) return;
    setBulkBusy(true);
    const targets = items
      .map((c, i) => ({ c, i }))
      .filter(({ c }) => predicate(c) && !c.busy);
    for (const { c, i } of targets) {
      await runGenerate(i, c);
    }
    setBulkBusy(false);
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

      <div className="review-actions board-actions top-actions">
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

      {items.length > 0 && (toGenCount > 0 || failedCount > 0) && (
        <div className="cast-toolbar">
          {failedCount > 0 && (
            <button
              type="button"
              className="btn"
              disabled={bulkBusy || busy}
              onClick={() => runBulk(isFailed)}
            >
              {bulkBusy ? (
                <>
                  <span className="spinner-inline" /> Working…
                </>
              ) : (
                `🔄 Retry failed (${failedCount})`
              )}
            </button>
          )}
          {toGenCount > 0 && (
            <button
              type="button"
              className="btn secondary"
              disabled={bulkBusy || busy}
              onClick={() => runBulk(needsGen)}
              title="Generate a reference for every prop/background that doesn't have one (your uploads are left as-is)"
            >
              {bulkBusy ? (
                <>
                  <span className="spinner-inline" /> Generating…
                </>
              ) : (
                `✨ Generate all (${toGenCount})`
              )}
            </button>
          )}
        </div>
      )}

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

      <ImageLightbox src={lightbox} alt="Asset reference" onClose={() => setLightbox(null)} />
    </div>
  );
}
