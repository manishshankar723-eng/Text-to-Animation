// Storyboard cast setup (Stage B — character consistency).
// Shows the characters the AI found in the script. For each, the user can edit
// the visual description and generate a reference image (reusing the existing
// character-reference endpoint). Those references are then fed into every panel
// the character appears in, so they stay consistent. Generating refs is optional.
import { useRef, useState } from "react";
import * as api from "../api.js";
import ImageLightbox from "./ImageLightbox.jsx";
import GrowTextarea from "./GrowTextarea.jsx";
import RefVersions from "./RefVersions.jsx";
// A reference is a drawn image, so it spends `cap.image-generate` — guarded on
// POST /characters/reference. ⚠ UPLOADING ONE IS NOT GATED and must not be:
// the file is the customer's own, nothing is drawn, and it is the way an
// account without generation still gets a consistent cast.
import useCapability from "../useCapability.js";

import WorkflowIcon from "./WorkflowIcon.jsx";
// Fields worth keeping when the user steps away from this page (see `saved`).
// `uploaded` rides along so "Generate all" keeps skipping the user's own images
// even after they leave and come back.
// ⚠ `versions` AND `activeVersion` ARE DURABLE TOO, or stepping away to the
// props step and back would throw away every take but the last one — which is
// the exact loss this feature exists to stop.
const DURABLE = [
  "description",
  "referenceId",
  "previewUrl",
  "uploaded",
  "versions",
  "activeVersion",
];


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
        // How many shots this character is in. Read-only, and shown on the
        // card so a face that appears once can be skipped knowingly.
        shotCount: c.shotCount || 0,
        description: prev.description ?? c.description ?? "",
        referenceId: prev.referenceId ?? null,
        previewUrl: prev.previewUrl ?? null,
        uploaded: prev.uploaded ?? false, // user's own image → never auto-generated
        // Every take drawn or uploaded for this character, oldest first, and
        // which one is live. A card saved before versions existed carries a
        // lone reference: ADOPT it as take 1 rather than showing "0 versions"
        // beside a picture that plainly exists — the same adoption the board
        // does on read for panels drawn before it kept versions.
        versions:
          prev.versions ??
          (prev.referenceId
            ? [
                {
                  referenceId: prev.referenceId,
                  previewUrl: prev.previewUrl ?? null,
                  uploaded: prev.uploaded ?? false,
                },
              ]
            : []),
        activeVersion: prev.activeVersion ?? 0,
        busy: false,
        error: "",
      };
    })
  );
  const [bulkBusy, setBulkBusy] = useState(false);
  const fileInputs = useRef([]);
  // ⚠ THE LATEST CAST, READABLE FROM AN ASYNC HANDLER. `runGenerate` takes a
  // snapshot so a bulk loop doesn't wait on React state, and appending a take
  // to a snapshot taken before the previous take landed would DROP that take.
  // The ref is rewritten on every render, so by the time a fetch resolves it
  // holds the array the last render saw.
  const castRef = useRef(cast);
  castRef.current = cast;

  // Append a take and make it live. Everything downstream — handleGenerate,
  // readyCount, needsGen — keeps reading the flat `referenceId`/`previewUrl`,
  // which is exactly the active take's, so nothing else has to know versions
  // exist.
  function addVersion(i, take) {
    const versions = [...(castRef.current[i]?.versions || []), take];
    patch(i, {
      versions,
      activeVersion: versions.length - 1,
      ...take,
      busy: false,
      error: "",
    });
  }

  // Switch which take is live. Named `pickVersion`, not `useVersion` — a
  // component-scope function called `use…` reads as a hook.
  //
  // ⚠ A RESTORED TAKE HAS ITS ID BUT NO PICTURE. Resuming a draft pulls down
  // only the take that is live (see `restoreSavedRefs` in the workflow — a
  // dozen names times three takes is megabytes nobody asked for), so stepping
  // onto an older one fetches its image here, once, and keeps it.
  async function pickVersion(i, n) {
    const take = (castRef.current[i]?.versions || [])[n];
    if (!take) return;
    if (take.previewUrl) {
      patch(i, { activeVersion: n, ...take });
      return;
    }
    patch(i, { activeVersion: n, ...take, busy: true });
    try {
      const previewUrl = await api.fetchReferenceImage(take.referenceId);
      const filled = { ...take, previewUrl };
      const versions = (castRef.current[i]?.versions || []).map((v, k) =>
        k === n ? filled : v
      );
      patch(i, { versions, activeVersion: n, ...filled, busy: false });
    } catch {
      // The image is gone from the server; the take still points somewhere the
      // board can use, so keep it selected and leave the placeholder.
      patch(i, { busy: false });
    }
  }

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
      // uploaded:false — a generated take is never treated as the user's own.
      // ⚠ APPENDED, NOT WRITTEN OVER: the take before this one is still on the
      // server under its own id and still on screen behind the ‹ › arrows.
      addVersion(i, { referenceId: res.reference_id, previewUrl, uploaded: false });
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
      // An upload is a take like any other — it can be compared against a
      // drawn one and switched away from without re-picking the file.
      addVersion(i, { referenceId: res.reference_id, previewUrl, uploaded: true });
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
        {/* ⚠ BULK ACTIONS SHARE THE STEP'S OWN ROW, and they sit BEFORE the
            primary. They used to hang in a second strip of their own below the
            divider, which read as belonging to the grid and put "Generate all"
            under "Generate panels" — two different jobs, stacked, in two
            different places. One row, one order: fill the refs, then leave. */}
        {cast.length > 0 && imageCap.on && failedCount > 0 && (
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
        {cast.length > 0 && imageCap.on && toGenCount > 0 && (
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
                {/* ⚠ WHAT IS ON SCREEN IS WHAT GOES TO THE PANELS. See
                    RefVersions — picking a take swaps this card's live
                    `referenceId`, which is the one `handleGenerate` sends. */}
                <RefVersions
                  total={(ch.versions || []).length}
                  active={ch.activeVersion || 0}
                  disabled={ch.busy || bulkBusy || busy}
                  onPick={(n) => pickVersion(i, n)}
                />
              </div>
              <div className="cast-body">
                <div className="cast-name">
                  {ch.name}
                  {/* ⚠ WHAT A REFERENCE IS WORTH, IN ONE NUMBER. Every
                      sheet costs an image, and a board came back carrying a
                      full character sheet for an artisan who appears only as
                      a pair of HANDS in one close-up. Reported. The count is
                      a fact rather than a guess about what is visible, and
                      this step is already optional — so the honest thing is
                      to say who is barely in the film and let the user skip
                      them. The reasoning lives in the tooltip. */}
                  {ch.shotCount > 0 && (
                    <span
                      className={`cast-shots${ch.shotCount === 1 ? " one" : ""}`}
                      title={
                        ch.shotCount === 1
                          ? "In one shot only — a reference costs an image, and may not be worth it here."
                          : `In ${ch.shotCount} shots — a reference keeps them looking the same across all of them.`
                      }
                    >
                      {ch.shotCount} shot{ch.shotCount === 1 ? "" : "s"}
                    </span>
                  )}
                </div>
                {/* ⚠ IT GROWS TO ITS TEXT — the same fault as the review
                    step's image prompt, on the same kind of box. Fixed at
                    76px it clipped every description mid-sentence, behind a
                    scrollbar — and this is the text that DRAWS the
                    character. */}
                <GrowTextarea
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
