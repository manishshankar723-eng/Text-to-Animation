// Storyboard cast setup (Stage B — character consistency).
// Shows the characters the AI found in the script. For each, the user can edit
// the visual description and generate a reference image (reusing the existing
// character-reference endpoint). Those references are then fed into every panel
// the character appears in, so they stay consistent. Generating refs is optional.
import { useRef, useState } from "react";
import * as api from "../api.js";
import ImageLightbox from "./ImageLightbox.jsx";
// A reference is a drawn image, so it spends `cap.image-generate` — guarded on
// POST /characters/reference. ⚠ UPLOADING ONE IS NOT GATED and must not be:
// the file is the customer's own, nothing is drawn, and it is the way an
// account without generation still gets a consistent cast.
import useCapability from "../useCapability.js";

import WorkflowIcon from "./WorkflowIcon.jsx";
// Fields worth keeping when the user steps away from this page (see `saved`).
// `uploaded` rides along so "Generate all" keeps skipping the user's own images
// even after they leave and come back.
const DURABLE = ["description", "referenceId", "previewUrl", "uploaded"];

export default function StoryboardCast({
  characters,
  saved,
  onSave,
  // The script's region/period/culture — sent with every reference so the cast
  // is drawn as people of the story's world, not the model's default.
  world,
  // ⚠ THE BOARD'S ART STYLE, AND IT HAS TO COME DOWN HERE. A cast sheet is not
  // a neutral identity photo — it becomes a look reference inside every panel
  // this character appears in, so a sheet drawn in the wrong medium takes those
  // panels with it. Without this the sheet was ALWAYS a Pixar cartoon, and a
  // Cinematic board came back with cartoon people in half its shots.
  style,
  // The audience. A T-pose on white shows no prices, but the country still
  // says who these people are and what is written on anything they carry.
  market,
  onBack,
  onGenerate,
  busy,
}) {
  const [lightbox, setLightbox] = useState(null);
  const imageCap = useCapability("image-generate");
  // `saved` holds what the user already set up for these characters on an
  // earlier visit (the workflow owns it, so it outlives this component). Seed
  // from it, otherwise fall back to the breakdown's description.
  const [cast, setCast] = useState(() =>
    (characters || []).map((c) => {
      const prev = saved?.[(c.name || "").trim().toLowerCase()] || {};
      return {
        name: c.name,
        description: prev.description ?? c.description ?? "",
        referenceId: prev.referenceId ?? null,
        previewUrl: prev.previewUrl ?? null,
        uploaded: prev.uploaded ?? false, // user's own image → never auto-generated
        busy: false,
        error: "",
      };
    })
  );
  const [bulkBusy, setBulkBusy] = useState(false);
  const fileInputs = useRef([]);

  function patch(i, fields) {
    setCast((c) => c.map((ch, idx) => (idx === i ? { ...ch, ...fields } : ch)));
    // Mirror the durable fields up to the workflow so leaving this step (Back,
    // or on to props) doesn't discard the reference the user just set up.
    const durable = {};
    for (const k of DURABLE) if (k in fields) durable[k] = fields[k];
    if (Object.keys(durable).length > 0) onSave?.(cast[i].name, durable);
  }

  // Generate one reference. Takes the item snapshot so a bulk loop doesn't rely
  // on React state having updated between iterations.
  async function runGenerate(i, item) {
    patch(i, { busy: true, error: "" });
    try {
      const prompt = item.description.trim() || `A character named ${item.name}`;
      const res = await api.generateReference(prompt, world, style, market);
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
    if (cast[i].busy) return;
    runGenerate(i, cast[i]);
  }

  async function uploadRef(i, file) {
    if (!file || cast[i].busy) return;
    patch(i, { busy: true, error: "" });
    try {
      const res = await api.uploadReference(file);
      const previewUrl = URL.createObjectURL(file);
      // uploaded:true so bulk "Generate all" / "Retry failed" leaves it alone.
      patch(i, { referenceId: res.reference_id, previewUrl, uploaded: true, error: "", busy: false });
    } catch (e) {
      patch(i, { busy: false, error: e.message });
    }
  }

  // A character still needs generating if it has no reference AND the user
  // hasn't uploaded their own image for it.
  const needsGen = (c) => !c.referenceId && !c.uploaded;
  // A failed one is worth retrying (uploads never "fail" this way).
  const isFailed = (c) => Boolean(c.error) && !c.uploaded;

  const toGenCount = cast.filter(needsGen).length;
  const failedCount = cast.filter(isFailed).length;

  // Generate references sequentially (one at a time — gentler on the image quota,
  // like the board's "Retry all failed"). Snapshot targets up front so the set
  // doesn't shift as state updates.
  async function runBulk(predicate) {
    if (bulkBusy) return;
    setBulkBusy(true);
    const targets = cast
      .map((c, i) => ({ c, i }))
      .filter(({ c }) => predicate(c) && !c.busy);
    for (const { c, i } of targets) {
      await runGenerate(i, c);
    }
    setBulkBusy(false);
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
        {/* Back leads the header row, in the same box as the icon beside it —
            see `.wf-back` in shell.css. */}
        <button
          type="button"
          className="btn back-btn wf-back"
          onClick={onBack}
          disabled={busy}
          title="Back"
          aria-label="Back"
        >
          ←
        </button>
        <span className="wf-icon"><WorkflowIcon id="script-to-storyboard" /></span>
        <div>
          <h1 className="wf-title">Set up your cast</h1>
          <p className="muted">
            Generate a reference for each character so they look the same in every
            panel. This step is optional — you can skip any character.
          </p>
        </div>
      </div>

      <div className="review-actions board-actions top-actions">
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

      {/* ⚠ THE REASON IS PRINTED ONCE, ABOVE THE LIST, and it says what
          still works. Uploading is untouched — an account that cannot draw a
          reference can still give every character one. */}
      {!imageCap.on && imageCap.visible && (
        <p className="tiny muted">
          🔒 {imageCap.reason} You can still upload your own reference for each
          character.
        </p>
      )}
      {cast.length > 0 && imageCap.on && (toGenCount > 0 || failedCount > 0) && (
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
              title="Generate a reference for every character that doesn't have one (your uploads are left as-is)"
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
                    className={`btn secondary cast-btn ${imageCap.on ? "" : "cap-off"}`}
                    disabled={!imageCap.on || ch.busy}
                    onClick={() => generateRef(i)}
                    title={imageCap.on ? undefined : imageCap.reason}
                  >
                    {!imageCap.on ? (
                      "🔒 Generate"
                    ) : ch.busy ? (
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

      <ImageLightbox src={lightbox} alt="Character reference" onClose={() => setLightbox(null)} />
    </div>
  );
}
