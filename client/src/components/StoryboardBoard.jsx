// Storyboard board (Stage D output + start of Stage E).
// Polls the storyboard job, shows a live gold progress bar, and renders panels
// in a grid as they finish (each fetched as an authed blob). Matches the
// Text-to-Image workflow's look (WorkflowHeader + progress bar + gallery tiles).
import { useEffect, useRef, useState } from "react";
import ScriptPanel from "./ScriptPanel.jsx";
import * as api from "../api.js";
// Drawing a panel spends `cap.image-generate` — guarded on
// POST /storyboards/{id}/regenerate-panel. Fail-open until the boot call lands.
import useCapability from "../useCapability.js";
import DialogueBox from "./DialogueBox.jsx";
// The shot description is the sentence that gets DRAWN — a fixed two-row box
// hid the middle of it behind a scrollbar. Same box, same fault, same fix as
// the review step and the cast/props steps. See GrowTextarea.jsx.
import GrowTextarea from "./GrowTextarea.jsx";
import PanelSequenceStrip from "./PanelSequenceStrip.jsx";
import PanelVersions from "./PanelVersions.jsx";
// "Ask AI", at the point where it means something: a board exists and the
// user wants a specific change to it. See BoardAssistant.jsx.
import BoardAssistant from "./BoardAssistant.jsx";
// One runtime formatter for the whole app — a film that reads "82s" on the
// review step and "1m 22s" here reads as two different numbers.
import { formatRuntime } from "./ScriptToStoryboard.jsx";

import WorkflowIcon from "./WorkflowIcon.jsx";
// ONE way to rename a thing, shared with Plan & Script and the video
// workspaces — see TitleInput.jsx.
import TitleInput from "./TitleInput.jsx";
// Styles the user can re-cast the whole board into (kept as switchable variants).
const RESTYLE_OPTIONS = [
  { id: "rough-sketch", label: "✏️ Rough Sketch" },
  { id: "sketch", label: "🖊️ Sketch" },
  { id: "comic", label: "💥 Comic" },
  { id: "cinematic", label: "🎬 Cinematic" },
  { id: "animation-3d", label: "🧸 Animation 3D" },
  { id: "watercolor", label: "🎨 Watercolor Paint" },
  { id: "photo-commercial", label: "📷 Photo / Commercial" },
  { id: "charcoal", label: "🖤 Charcoal Sketch" },
  { id: "dark-anime", label: "🌃 Dark Anime" },
  { id: "flat-vector", label: "🔷 Flat / Vector" },
  { id: "noir", label: "🎞️ Noir" },
  { id: "stick-figure", label: "🏃 Stick Figure" },
  { id: "graphic-novel", label: "📖 Graphic Novel" },
];
// Exported so anywhere that mounts this board (Image to Video's "Create
// Animatic Image") shows the SAME style names, instead of a second copy of the
// list that drifts.
export const styleLabelFor = (id) =>
  RESTYLE_OPTIONS.find((s) => s.id === id)?.label || id || "Style";

/** "close-up · slow push-in · 3s" — framing, move and length as one line.
 *
 * ⚠ "static" IS NOT PRINTED. It is the answer for most shots, and repeating it
 * under every panel is noise: the absence of a move is what static means. Same
 * rule as `storyboard_pdf._shot_line`, and the two must stay in step. */
function shotLine(p) {
  const bits = [(p.camera || "").trim()];
  const move = (p.movement || "").trim();
  if (move && !["static", "none", "no movement", "still"].includes(move.toLowerCase())) {
    bits.push(move);
  }
  const secs = Number(p.duration_seconds) || 0;
  if (secs > 0) bits.push(`${secs}s`);
  return bits.filter(Boolean).join(" · ");
}

export default function StoryboardBoard({
  jobId,
  styleLabel,
  aspect,
  // WHERE the back arrow goes ("Your Storyboards"). Prose only — no arrow in
  // it: the button draws that itself, and this is read as a tooltip.
  backLabel,
  onBack,
  // Set by App: hands the new animatic's id to the animatics workflow. Absent
  // when the board is rendered somewhere that can't navigate there.
  onOpenAnimatic,
  // Image to Animatic Image turns this on. It stacks the shots in ONE column
  // and gives each a key-pose strip (PanelSequenceStrip). Off everywhere else,
  // so Script to Storyboard's board is exactly as it was.
  sequenceMode = false,
}) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  // May this account draw at all? Every button on this page that costs an image
  // reads it. See `entitlements.js`.
  const imageCap = useCapability("image-generate");
  const [panelUrls, setPanelUrls] = useState({});
  const panelUrlsRef = useRef({});
  // Blobs REPLACED by a fresher render of the same panel. They can't be revoked
  // at swap time — the <img> is still showing one until React commits the new
  // src — so they are parked here and freed on unmount with everything else.
  // Bounded by how many times you redraw in one sitting.
  const retiredBlobs = useRef([]);
  const [lightbox, setLightbox] = useState(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState("");
  // The DEEP check: one vision call over a contact sheet of the whole board.
  // ⚠ IT SPENDS MONEY, so it is a button and never a side effect of generating.
  // `null` = not run yet, which is deliberately different from `{findings: []}`
  // = run and found nothing. A checker whose success looks the same as "it
  // didn't run" gets pressed twice and then distrusted.
  const [checkResult, setCheckResult] = useState(null);
  const [checkBusy, setCheckBusy] = useState(false);
  const [checkError, setCheckError] = useState("");
  const [zipBusy, setZipBusy] = useState(false);
  const [animaticBusy, setAnimaticBusy] = useState(false);
  // A stop has been asked for but the run hasn't wound down yet (the panels
  // already talking to the image API still have to come back).
  const [stopRequested, setStopRequested] = useState(false);
  const [retrying, setRetrying] = useState({});
  // A running bulk draw: { kind: "remaining" | "failed", done, total }. One at a
  // time, and interruptible — `batchStopRef` is read between panels.
  const [batch, setBatch] = useState(null);
  const batchStopRef = useRef(false);
  // Per-panel edited descriptions (keyed by panel index). Undefined = unedited,
  // so the textarea falls back to the panel's stored description.
  const [editedDesc, setEditedDesc] = useState({});
  // Re-style controls + a nonce to restart polling after a restyle kicks off.
  //
  // ⚠ `null` MEANS "THE USER HASN'T PICKED ONE YET", and that is the whole
  // point. This used to be a hard-coded "comic": a board drawn in Cinematic
  // opened with the dropdown reading Comic, so one press of "Restyle all" paid
  // to redraw every panel in a style nobody asked for. Left null, the picker
  // shows the style the board IS (see `selectedStyle`) until the user chooses
  // otherwise — and then the button below refuses the style it already has.
  const [newStyle, setNewStyle] = useState(null);
  const [restyleBusy, setRestyleBusy] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);

  // Poll the job until it finishes (recursive setTimeout — stops at terminal state).
  useEffect(() => {
    let active = true;
    let timer;
    async function poll() {
      if (!active) return;
      try {
        const j = await api.getJob(jobId);
        if (!active) return;
        setJob(j);
        // A poll that WORKS clears the last poll's complaint. Without this, one
        // slow or dropped request left "The server didn't respond within 120s"
        // pinned over a board that had long since recovered and was visibly
        // drawing panels — reported, and it sent us hunting a server fault that
        // had already fixed itself.
        setError((prev) => (prev ? "" : prev));
        if (j.status === "succeeded" || j.status === "failed") return;
      } catch (e) {
        if (active) setError(e.message);
      }
      timer = setTimeout(poll, 2000);
    }
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [jobId, pollNonce]);

  // Fetch each panel image (authed blob) once it has a url. Cached by the panel's
  // URL (which carries ?v=<variant>), so each style variant is fetched separately.
  useEffect(() => {
    const panels = job?.result?.panels || [];
    panels.forEach((p) => {
      if (p.url && !p.failed && !panelUrlsRef.current[p.url]) {
        panelUrlsRef.current[p.url] = "loading";
        api
          .fetchStoryboardPanel(jobId, p.index, p.url)
          .then((url) => {
            // A version switch or a redraw may have swapped a FRESHER picture
            // into this key while this fetch was in flight. Theirs wins — the
            // slot no longer says "loading" — or we would quietly put the old
            // pixels back, which is the bug this whole cache is meant to avoid.
            if (panelUrlsRef.current[p.url] !== "loading") {
              URL.revokeObjectURL(url);
              return;
            }
            panelUrlsRef.current[p.url] = url;
            setPanelUrls((prev) => ({ ...prev, [p.url]: url }));
          })
          .catch(() => {
            panelUrlsRef.current[p.url] = null;
          });
      }
    });
  }, [job, jobId]);

  // Revoke blob URLs on unmount — the live cache AND the ones superseded by a
  // redraw, which are no longer reachable through it.
  useEffect(() => {
    return () => {
      Object.values(panelUrlsRef.current).forEach((u) => {
        if (typeof u === "string" && u.startsWith("blob:")) URL.revokeObjectURL(u);
      });
      retiredBlobs.current.forEach((u) => URL.revokeObjectURL(u));
    };
  }, []);

  // Refresh ONE panel's picture. Nothing else on the board is touched.
  //
  // This exists because `reloadBoard` drops EVERY tile's blob, and it was being
  // used to refresh a single panel — so switching one shot's version, or
  // redrawing one shot, made the whole board blink through empty boxes and
  // re-download every picture (reported: "i only regenerate this panel so why
  // all image refresh"). `reloadBoard` is the right tool ONLY for insert and
  // delete, where indices shift and a blob keyed by "/panel/2" now belongs to a
  // different shot.
  //
  // The new bytes are fetched BEFORE anything on screen changes, so the tile
  // goes from the old picture straight to the new one with no empty frame in
  // between. `fetchStoryboardPanel` cache-busts its request, so an UNCHANGED
  // url — which is exactly what a version switch leaves you with — still comes
  // back with the new pixels.
  async function refreshPanelImage(index, url) {
    const target =
      url || (job?.result?.panels || []).find((p) => p.index === index)?.url;
    if (!target) return;
    let fresh;
    try {
      fresh = await api.fetchStoryboardPanel(jobId, index, target);
    } catch {
      return; // keep the picture that's up rather than blanking the tile
    }
    const displaced = panelUrlsRef.current[target];
    if (typeof displaced === "string" && displaced.startsWith("blob:")) {
      retiredBlobs.current.push(displaced);
    }
    panelUrlsRef.current[target] = fresh;
    setPanelUrls((prev) => ({ ...prev, [target]: fresh }));
  }

  // Forget one cache key. Only for a url nothing renders any more — a redraw
  // that moved the panel to a different url.
  function dropPanelImage(url) {
    if (!url) return;
    const cached = panelUrlsRef.current[url];
    if (typeof cached === "string" && cached.startsWith("blob:")) {
      retiredBlobs.current.push(cached);
    }
    delete panelUrlsRef.current[url];
    setPanelUrls((prev) => {
      if (!(url in prev)) return prev;
      const next = { ...prev };
      delete next[url];
      return next;
    });
  }

  const status = job?.status;
  const progress = job?.progress || {};
  const panels = job?.result?.panels || [];
  const total = job?.result?.count || job?.params?.count || panels.length || 0;
  // The FREE audit, measured by the pipeline the moment the board finished. It
  // costs nothing, so it is simply there — no button, no call, no spend.
  const audit = job?.result?.audit || null;

  async function runDeepCheck() {
    if (checkBusy) return;
    setCheckBusy(true);
    setCheckError("");
    try {
      setCheckResult(await api.checkStoryboard(jobId));
    } catch (e) {
      // ⚠ A FAILURE CLEARS THE PREVIOUS RESULT. Leaving an old clean report on
      // screen beside an error reads as "checked, all good" for a check that
      // did not happen.
      setCheckResult(null);
      setCheckError(e.message);
    } finally {
      setCheckBusy(false);
    }
  }
  // The board's saved title and source script. Both live on the job record, so
  // they're here whether the board was just generated or reopened from the
  // library. Falls back while the first poll is still in flight.
  const boardTitle = job?.character_name || "";

  /** Rename the board from its own heading — the same box the editor uses.
   *  ⚠ IT PATCHES THE LOCAL JOB IMMEDIATELY. The poll that refreshes this
   *  screen runs on its own clock, so waiting for it would leave the old name
   *  on screen for up to a poll interval after a save that had already
   *  succeeded — which reads as "the rename didn't work". Throws on failure so
   *  `TitleInput` puts the old name back. */
  async function renameBoard(next) {
    try {
      const updated = await api.renameStoryboard(jobId, next);
      setJob((j) => (j ? { ...j, character_name: updated.title || next } : j));
    } catch (e) {
      setError(e.message);
      throw e;
    }
  }
  const boardScript = job?.params?.script || "";
  // "We don't know yet" is NOT "it is generating". This used to read
  // `|| !status`, so any board whose job could not be fetched — the server
  // restarting, a dropped request, a poll that errored — rendered as a live
  // run: "Stop generation" in the toolbar, the progress bar up, and every
  // Regenerate button hidden because the board believed it was busy. Nothing
  // the user pressed could recover it. Reported as "i cant see regenarte
  // buttun and i see nothing happen".
  //
  // Unknown status counts as running ONLY while the first fetch is genuinely in
  // flight, which keeps the toolbar from flashing on load. Once a fetch has
  // failed, the board is treated as idle so its buttons come back and the user
  // can act — the error banner already says the fetch failed.
  const loadingFirst = !job && !error;
  const running = status === "queued" || status === "running" || loadingFirst;
  // The run ended early because the user pressed Stop (server-reported, so it
  // survives a reload) — the board says so instead of looking half-finished.
  const stopped = Boolean(job?.result?.stopped);
  // Style variants (each = one full-board render). Absent on older jobs → treat
  // the flat panels as the single variant 0.
  const variants =
    job?.result?.variants ||
    (panels.length ? [{ style: job?.result?.style, panels }] : []);
  const activeVariant = job?.result?.active_variant || 0;
  // The style the board is ACTUALLY drawn in right now, read off the board
  // itself rather than the `styleLabel` prop — the prop is whatever the parent
  // screen was holding when it mounted this, and it does not follow a restyle
  // or a variant switch. Everything on this screen that names a style reads
  // this, so the header, the chip and the picker can never disagree.
  const currentStyle = variants[activeVariant]?.style || job?.result?.style || "";
  // What the picker shows: the user's choice if they made one, otherwise the
  // style the board already is. See the `newStyle` note above.
  const selectedStyle = newStyle ?? currentStyle ?? "";
  // Redrawing into a style this board ALREADY has is pure spend for a picture
  // that exists — the chips above switch to it for free.
  const alreadyStyled = variants.some((v) => v.style === selectedStyle);

  // Switch which style variant is shown (persist server-side, update locally).
  async function switchVariant(idx) {
    if (idx === activeVariant || running) return;
    const v = variants[idx];
    if (!v) return;
    setError("");
    setJob((prev) =>
      prev
        ? {
            ...prev,
            result: {
              ...prev.result,
              active_variant: idx,
              panels: v.panels || [],
              style: v.style,
              ok_count: v.ok_count,
            },
          }
        : prev
    );
    try {
      await api.setActiveVariant(jobId, idx);
    } catch (e) {
      setError(e.message);
    }
  }

  // Re-draw the whole board in a new style (kept as a new variant); resume polling.
  async function handleRestyle() {
    // ⚠ THE `alreadyStyled` GUARD IS THE POINT, not a nicety: this call redraws
    // every panel on the board and bills for every one of them. The button is
    // disabled in that case too — this is the second lock, for the same reason
    // the first one exists.
    if (restyleBusy || running || !selectedStyle || alreadyStyled) return;
    setError("");
    setRestyleBusy(true);
    try {
      await api.restyleStoryboard(jobId, selectedStyle);
      setPollNonce((n) => n + 1); // restart the poll loop for the running restyle
    } catch (e) {
      setError(e.message);
    } finally {
      setRestyleBusy(false);
    }
  }
  const pendingCount = Math.max(0, total - panels.length);
  const tileRatio = (aspect || "16:9").replace(":", " / ");

  // The two "the board is done, what now" actions. A render FUNCTION rather
  // than duplicated JSX, because they sit in the toolbar normally but in the
  // TOP row in sequenceMode (which has no toolbar) — one definition, so the two
  // placements can't drift apart.
  function finishActions() {
    if (!okCount) return null;
    return (
      <>
        <button
          type="button"
          className="btn"
          disabled={zipBusy}
          onClick={handleZip}
          title="Generated character, prop & background images + the PDF, as a ZIP you can reuse"
        >
          {zipBusy ? (
            <>
              <span className="spinner-inline" /> Zipping…
            </>
          ) : (
            "⬇ Download assets (ZIP)"
          )}
        </button>
        {/* LAST, after the download: it's the step you take once the board is
            done, not another export. No AI credits spent. */}
        {onOpenAnimatic && !running && (
          <button
            type="button"
            className="btn board-animatic"
            disabled={animaticBusy}
            onClick={handleMakeAnimatic}
            title="Time these panels against audio and export a video — costs no AI credits"
          >
            {animaticBusy ? (
              <>
                <span className="spinner-inline" /> Opening…
              </>
            ) : (
              "🎬 Make project"
            )}
          </button>
        )}
      </>
    );
  }
  const okCount = panels.filter((p) => !p.failed && p.url).length;
  // The film's length, added up from the shots. 0 on a board made before the
  // breakdown returned one, which is why it is hidden rather than shown as 0s.
  const boardSeconds = panels.reduce(
    (sum, p) => sum + (Number(p.duration_seconds) || 0),
    0
  );

  // ⚠ THE ASSISTANT NEEDS A FINISHED BOARD TO TALK ABOUT. Not in sequenceMode
  // (that workflow is about the motion of a board finished elsewhere), not
  // while panels are still being drawn (the shot numbers are still moving), and
  // not on an empty one.
  const canAssist = !sequenceMode && !running && panels.length > 0;
  // ⚠ `showAssistant` IS COMPUTED WITH THE ASSISTANT'S OWN STATE, FURTHER
  // DOWN. It used to be here, reading `assistantOpen` — which is declared ~60
  // lines below — and that is a temporal-dead-zone ReferenceError, not a
  // warning: React unmounts the whole tree and the page goes WHITE.
  //
  // ⚠ IT ONLY FIRED ON FINISHED BOARDS, WHICH IS WHY IT SURVIVED. `&&`
  // short-circuits: while a board was still drawing, or had no panels,
  // `canAssist` was false and `assistantOpen` was never read. The moment the
  // last panel landed — panels present, `running` false — it was read, and the
  // board the user had just paid for went white. Reported exactly that way:
  // "jab pura panel image generate hua to white ho gaya", and it stayed white
  // on every reopen.
  const failedCount = panels.filter((p) => p.failed).length;

  // Panels with no image and no failure = never drawn. That's what a stopped
  // run leaves behind, and what "Generate remaining" finishes off.
  const emptyIdx = panels.filter((p) => !p.url && !p.failed).map((p) => p.index);
  const failedIdx = panels.filter((p) => p.failed).map((p) => p.index);

  // Draw a set of panels ONE AT A TIME (gentler on the rate limit than firing
  // them all at once) — shared by "Generate remaining" and "Retry all failed".
  // The loop is interruptible: a 20-panel batch going wrong shouldn't have to
  // run to the end, same reasoning as the Stop button on the run itself.
  async function runBatch(kind, indices) {
    if (batch || indices.length === 0) return;
    batchStopRef.current = false;
    setBatch({ kind, done: 0, total: indices.length });
    for (const [i, idx] of indices.entries()) {
      if (batchStopRef.current) break;
      // eslint-disable-next-line no-await-in-loop
      await retryPanel(idx);
      setBatch({ kind, done: i + 1, total: indices.length });
    }
    setBatch(null);
  }

  // THE NUCLEAR RELOAD — for STRUCTURAL edits only (insert / delete).
  //
  // Those shift every later panel's index, so a cached blob keyed by "/panel/2"
  // may now belong to a different shot and the whole cache has to go. That is
  // also why the entire board visibly re-downloads afterwards, which is
  // acceptable exactly once, for an edit that really did change every tile's
  // identity.
  //
  // DO NOT use this to refresh ONE panel — that is `refreshPanelImage`. Calling
  // it for a version switch or a single redraw is what made the whole board
  // blink every time the user pressed ‹ ›.
  async function reloadBoard() {
    Object.values(panelUrlsRef.current).forEach((u) => {
      if (typeof u === "string" && u.startsWith("blob:")) URL.revokeObjectURL(u);
    });
    panelUrlsRef.current = {};
    setPanelUrls({});
    // editedDesc / retrying are keyed by index; indices just shifted, so any
    // stale per-index entries would land on the wrong tile. Clear them — the
    // server persisted each panel's description, so nothing is lost.
    setEditedDesc({});
    setRetrying({});
    const j = await api.getJob(jobId);
    setJob(j);
  }

  const [editBusy, setEditBusy] = useState(false);

  // ---- "Ask AI" ------------------------------------------------------------
  // What the user has clicked, 1-BASED, exactly as printed under the panels —
  // {kind:"panel", shot} | {kind:"scene", scene} | {kind:"none"}. ⚠ THIS IS
  // WHAT MAKES "make this one wider" WORK: without it the assistant has no
  // referent and the only honest answer is "which one?".
  const [selection, setSelection] = useState({ kind: "none" });
  const [assistantOpen, setAssistantOpen] = useState(true);
  // Declared HERE, after the state it reads. See the note where `canAssist` is.
  const showAssistant = canAssist && assistantOpen;

  function toggleSelectPanel(shot) {
    setSelection((cur) =>
      cur.kind === "panel" && cur.shot === shot ? { kind: "none" } : { kind: "panel", shot }
    );
  }
  function toggleSelectScene(scene) {
    setSelection((cur) =>
      cur.kind === "scene" && cur.scene === scene ? { kind: "none" } : { kind: "scene", scene }
    );
  }

  /** Run an approved plan from the assistant, then re-read the board.
   *
   * ⚠ DESCENDING INDEX ORDER, AND EDITS BEFORE STRUCTURE AT THE SAME INDEX.
   * The plan was computed against ONE snapshot, but insert and delete renumber
   * everything after themselves. Working from the highest index down means no
   * action can be shifted by one that has not run yet — and an edit at the same
   * index has to land before an insert there, or it would redraw the blank
   * panel the insert just put in its place.
   */
  async function applyBoardActions(actions) {
    const ordered = [...actions].sort((a, b) => {
      if (b.index !== a.index) return b.index - a.index;
      // same index: edit, then delete, then insert
      const rank = { edit: 0, delete: 1, insert: 2 };
      return (rank[a.action] ?? 9) - (rank[b.action] ?? 9);
    });

    for (const a of ordered) {
      if (a.action === "delete") {
        await api.deleteStoryboardPanel(jobId, a.index);
        continue;
      }
      if (a.action === "insert") {
        await api.insertStoryboardPanel(jobId, a.index, a.description || "");
        // The panel lands blank; a new shot the user asked for in words is
        // meant to be a picture, so draw it straight away.
        await api.regenerateStoryboardPanel(jobId, a.index, {
          description: a.description || "",
        });
        continue;
      }
      // edit — only send the fields that were actually changed, or an empty
      // string would wipe a description the user never asked to lose.
      const overrides = {};
      if (a.description) overrides.description = a.description;
      if (a.camera) overrides.camera = a.camera;
      if (a.location) overrides.location = a.location;
      await api.regenerateStoryboardPanel(jobId, a.index, overrides);
    }

    // Indices have moved; the selection points at a picture that may no longer
    // be there. Drop it rather than let the next sentence act on the wrong one.
    setSelection({ kind: "none" });
    await reloadBoard();
  }

  // Insert a new (empty) panel at position `at`; the user then writes a prompt
  // and generates it with the existing per-panel Generate button.
  async function addPanelAt(at) {
    if (editBusy || running) return;
    setError("");
    setEditBusy(true);
    try {
      await api.insertStoryboardPanel(jobId, at);
      await reloadBoard();
    } catch (e) {
      setError(e.message);
    } finally {
      setEditBusy(false);
    }
  }

  async function deletePanel(index) {
    if (editBusy || running) return;
    setError("");
    setEditBusy(true);
    try {
      await api.deleteStoryboardPanel(jobId, index);
      await reloadBoard();
    } catch (e) {
      setError(e.message);
    } finally {
      setEditBusy(false);
    }
  }

  // Re-draw a single panel (failed, edited, or just unwanted). Sends the edited
  // description when the user has changed the shot's prompt.
  async function retryPanel(index) {
    if (retrying[index]) return;
    setError("");
    setRetrying((r) => ({ ...r, [index]: true }));
    try {
      const overrides = {};
      if (typeof editedDesc[index] === "string") overrides.description = editedDesc[index];
      const prevUrl = (job?.result?.panels || []).find((p) => p.index === index)?.url;
      const res = await api.regenerateStoryboardPanel(jobId, index, overrides);
      const panel = res.panel;
      // NEW PIXELS FIRST, then the new panel object. Emptying the cache and
      // letting the fetch effect pick it up afterwards — which is what this did
      // — meant the tile rendered at least once with no picture at all, so a
      // redraw flashed an empty box before the drawing appeared. Priming the
      // cache before `setJob` closes that gap: by the time render sees the new
      // url, its picture is already in hand.
      await refreshPanelImage(index, panel.url);
      setJob((prev) => {
        if (!prev) return prev;
        const r = prev.result || {};
        const panels = (r.panels || []).map((p) => (p.index === index ? panel : p));
        const variants = r.variants
          ? r.variants.map((v, i) => (i === (r.active_variant || 0) ? { ...v, panels } : v))
          : r.variants;
        return { ...prev, result: { ...r, panels, variants } };
      });
      // Only now that nothing renders it: if the redraw moved the panel to a
      // different url, the old key is dead weight holding a blob.
      if (prevUrl && prevUrl !== panel.url) dropPanelImage(prevUrl);
    } catch (e) {
      setError(e.message);
    } finally {
      setRetrying((r) => ({ ...r, [index]: false }));
    }
  }

  // Fallback download name, used only if the server's Content-Disposition can't
  // be read. Mirrors the server's _safe_filename: punctuation → space, runs
  // collapsed, so "Postmarked: After Death!" reads as "Postmarked After Death".
  function safeTitle() {
    const cleaned = (job?.character_name || "")
      .replace(/['’]/g, "") // "Kabir's" → "Kabirs", never "Kabir s"
      .replace(/[^\p{L}\p{N}\-_ ]/gu, " ")
      .split(/\s+/)
      .filter(Boolean)
      .join(" ")
      .replace(/^[-_ ]+|[-_ ]+$/g, "");
    return cleaned || "storyboard";
  }

  async function handlePdf() {
    if (pdfBusy) return;
    setPdfError("");
    setPdfBusy(true);
    try {
      await api.downloadStoryboardPdf(jobId, `${safeTitle()}.pdf`);
    } catch (e) {
      setPdfError(e.message);
    } finally {
      setPdfBusy(false);
    }
  }

  // Download generated references (characters + props/backgrounds) + PDF as a ZIP,
  // so the user can re-upload the same references next time instead of regenerating.
  async function handleZip() {
    if (zipBusy) return;
    setPdfError("");
    setZipBusy(true);
    try {
      await api.downloadStoryboardBundle(jobId, `${safeTitle()}_assets.zip`);
    } catch (e) {
      setPdfError(e.message);
    } finally {
      setZipBusy(false);
    }
  }

  // Turn this board into an animatic: every drawn panel becomes a frame at a
  // 2-second hold, and the animatics editor opens on it. Costs no AI quota —
  // the frames reference these panels rather than redrawing anything.
  async function handleMakeAnimatic() {
    if (animaticBusy) return;
    setPdfError("");
    setAnimaticBusy(true);
    try {
      const project = await api.createAnimatic({ sourceStoryboardId: jobId });
      onOpenAnimatic(project.job_id);
    } catch (e) {
      setPdfError(e.message);
      setAnimaticBusy(false);
    }
  }

  // Once the run is over the flag has done its job — clear it so a later
  // re-style doesn't open with the button already reading "Stopping…".
  useEffect(() => {
    if (!running) setStopRequested(false);
  }, [running]);

  // Stop the run. Panels not yet started are skipped; ones already in flight
  // finish, so the button keeps saying "Stopping…" until the job goes terminal.
  async function handleStop() {
    if (stopRequested || !running) return;
    setError("");
    setStopRequested(true);
    try {
      await api.stopStoryboard(jobId);
    } catch (e) {
      setError(e.message);
      setStopRequested(false); // it didn't take — let them press it again
    }
  }

  return (
    <div className="workflow-head-wrap sb-board">
      <div className="workflow-header">
        {/* Arrow only, like every other back control in the app — `backLabel`
            is WHERE it goes, and that reads in the tooltip. It leads the header
            row in the same box as the icon beside it (`.wf-back`, shell.css). */}
        <button
          type="button"
          className="btn back-btn wf-back"
          onClick={onBack}
          title={backLabel || "Back to shots"}
          aria-label={backLabel || "Back to shots"}
        >
          ←
        </button>
        <span className="wf-icon"><WorkflowIcon id="script-to-storyboard" /></span>
        <div className="wf-head-main">
          {/* The board's OWN title, not a generic heading — it's what names the
              library card, the PDF and the ZIP, so seeing it here is how you
              know which board you're looking at. Editable in place, like the
              editor's: renaming used to mean going back to the library. */}
          <TitleInput
            value={boardTitle}
            placeholder="Your storyboard"
            ariaLabel="Storyboard title"
            className="wf-title-input"
            onSave={renameBoard}
          />
          <p className="muted">
            {/* The style the board IS, read off the board. The `styleLabel`
                prop is only the fallback for the moment before the first poll
                lands — after a restyle or a variant switch it is stale, and a
                header naming one style over panels drawn in another is how the
                wrong "Restyle all" got pressed in the first place. */}
            {(currentStyle && styleLabelFor(currentStyle)) || styleLabel} · {aspect} ·{" "}
            {total} panel{total === 1 ? "" : "s"}
            {boardSeconds > 0 ? ` · ≈ ${formatRuntime(boardSeconds)}` : ""}
          </p>
        </div>
      </div>

      {/* ⚠ THIS ROW EXISTS ONLY IN sequenceMode NOW. It used to hold "Start
          over" for the normal board, which was a second way out of a screen
          whose way out is the arrow in the header — and once Back moved up
          there, the two sat one above the other saying almost the same thing.
          Everything else on this screen (Download PDF / assets / Make project)
          lives in `.board-toolbar` below; sequenceMode has no toolbar, so the
          finish actions come up here instead. */}
      {sequenceMode && (
        <div className="review-actions board-actions top-actions">
          {/* Wrapped so the finish actions group as one block rather than
              spreading across the row. `.review-actions-right` is the existing
              answer to that. */}
          <div className="review-actions-right">{finishActions()}</div>
        </div>
      )}

      {/* Style variants: switch between saved styles, or add a new one.
          Hidden in sequenceMode — this workflow is about drawing the MOTION of
          a board you already styled, and re-styling every panel would throw the
          key poses out of step with the panels they were drawn from. Restyle in
          Script to Storyboard, then copy the board over. */}
      {!sequenceMode && variants.length > 0 && (
        <div className="board-styles">
          {/* ⚠ SHOWN EVEN FOR A SINGLE STYLE, where it switches nothing. It is
              the one place on this screen that states, in the board's own
              words, what the panels are drawn in — and it sits directly beside
              the picker that spends money changing it. Hiding it until a second
              style existed is exactly why a Cinematic board could be restyled
              to Comic without anything on screen contradicting it. */}
          <div className="board-variant-switch">
            <span className="board-styles-label">Style:</span>
            {variants.map((v, i) => (
              <button
                key={i}
                type="button"
                className={`opt-chip ${i === activeVariant ? "active" : ""}`}
                disabled={running || variants.length === 1}
                onClick={() => switchVariant(i)}
                title={
                  variants.length === 1
                    ? `This board is drawn in ${styleLabelFor(v.style)}`
                    : `Show the ${styleLabelFor(v.style)} version`
                }
              >
                {styleLabelFor(v.style)}
              </button>
            ))}
          </div>
          <div className="board-restyle">
            <span className="board-styles-label">Add a style:</span>
            <select
              className="board-style-select"
              value={selectedStyle}
              disabled={running || restyleBusy}
              onChange={(e) => setNewStyle(e.target.value)}
            >
              {/* Only reachable if the board reports a style this build doesn't
                  know — better an honest blank than silently pointing at
                  whichever option happens to be first in the list. */}
              {!RESTYLE_OPTIONS.some((s) => s.id === selectedStyle) && (
                <option value="">Pick a style…</option>
              )}
              {RESTYLE_OPTIONS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn secondary"
              disabled={running || restyleBusy || !selectedStyle || alreadyStyled}
              onClick={handleRestyle}
              title={
                alreadyStyled
                  ? `This board is already drawn in ${styleLabelFor(selectedStyle)} — pick a different style to add one`
                  : `Re-draw all ${total} panel${total === 1 ? "" : "s"} in ${styleLabelFor(selectedStyle)}, kept as a new switchable version`
              }
            >
              {restyleBusy ? (
                <>
                  <span className="spinner-inline" /> Starting…
                </>
              ) : (
                "🎨 Restyle all"
              )}
            </button>
          </div>
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {pdfError && <div className="error">{pdfError}</div>}
      {status === "failed" && (
        <div className="error">Generation failed: {job?.error || "unknown error"}</div>
      )}

      {/* Hidden in sequenceMode: it reports on the PANEL draw, which happened
          back in Script to Storyboard before this copy was ever made, so here
          it is stale news about someone else's run. Each shot's own key-pose
          strip reports its own state. */}
      {!sequenceMode && stopped && (
        <div className="info-msg">
          ⏹ You stopped this generation — {okCount} of {total} panels drawn.
          {emptyIdx.length > 0
            ? ` Use “✨ Generate remaining (${emptyIdx.length})” to finish the board, or “✨ Generate this panel” on a single tile.`
            : " Every panel now has an image."}
        </div>
      )}

      {/* Toolbar is up while GENERATING too, so Stop is reachable from the
          moment the first panel looks wrong — that's the point of it. */}
      {(running || okCount > 0 || failedCount > 0) && (
        <div className="board-toolbar">
          {running && (
            <button
              type="button"
              className="btn danger-btn"
              disabled={stopRequested}
              onClick={handleStop}
              title="Stop drawing the remaining panels — the ones already started will still finish"
            >
              {stopRequested ? (
                <>
                  <span className="spinner-inline" /> Stopping…
                </>
              ) : (
                "⏹ Stop generation"
              )}
            </button>
          )}
          {/* Finish a stopped board in one click, instead of tile by tile. */}
          {/* ⚠ THE BULK BUTTONS GO WHEN DRAWING IS OFF, and that is the
              one place hiding beats greying: they are the two controls that
              spend forty images in one press, each panel carries its own locked
              button with the reason on it, and a greyed slab labelled "Generate
              remaining (40)" says nothing the tiles do not already say. */}
          {!running && imageCap.on && emptyIdx.length > 0 && (
            <button
              type="button"
              /* While drawing it's BUSY, not unavailable — the gold fill dimmed
                 to 45% read as a broken slab next to the live buttons. */
              className={`btn ${batch?.kind === "remaining" ? "is-busy" : "primary"}`}
              disabled={Boolean(batch)}
              onClick={() => runBatch("remaining", emptyIdx)}
              title="Draw every panel that has no image yet, one at a time"
            >
              {batch?.kind === "remaining" ? (
                <>
                  <span className="spinner-inline" /> Drawing{" "}
                  {Math.min(batch.done + 1, batch.total)} of {batch.total}…
                </>
              ) : (
                `✨ Generate remaining (${emptyIdx.length})`
              )}
            </button>
          )}
          {!running && imageCap.on && failedCount > 0 && (
            <button
              type="button"
              className={`btn board-retry-all ${batch?.kind === "failed" ? "is-busy" : ""}`}
              disabled={Boolean(batch)}
              onClick={() => runBatch("failed", failedIdx)}
            >
              {batch?.kind === "failed" ? (
                <>
                  <span className="spinner-inline" /> Retrying{" "}
                  {Math.min(batch.done + 1, batch.total)} of {batch.total}…
                </>
              ) : (
                `🔄 Retry all failed (${failedCount})`
              )}
            </button>
          )}
          {/* A bulk draw is many paid generations — it must be interruptible too. */}
          {batch && (
            <button
              type="button"
              className="btn danger-btn"
              onClick={() => {
                batchStopRef.current = true;
              }}
              title="Stop after the panel currently being drawn"
            >
              ⏹ Stop
            </button>
          )}
          {/* A PDF is a document to hand someone — the output of Script to
              Storyboard. This workflow produces IMAGES, so its downloads are
              the assets and the per-shot key poses instead. */}
          {!sequenceMode && okCount > 0 && (
            <button
              type="button"
              className="btn"
              disabled={pdfBusy}
              onClick={handlePdf}
            >
              {pdfBusy ? (
                <>
                  <span className="spinner-inline" /> Preparing PDF…
                </>
              ) : (
                "⬇ Download PDF"
              )}
            </button>
          )}
          {/* ⚠ THE CHECK IS A BUTTON, and its label says what it does rather
              than "QA" — the person pressing it wants to know if their film is
              wrong, not to run a process. It costs a model call, so it never
              happens on its own; the free audit above already ran. */}
          {!sequenceMode && okCount > 0 && (
            <button
              type="button"
              className="btn"
              disabled={checkBusy}
              onClick={runDeepCheck}
              title="Look at every panel for the wrong currency, the wrong language or an invented logo"
            >
              {checkBusy ? (
                <>
                  <span className="spinner-inline" /> Checking…
                </>
              ) : (
                "🔍 Check this board"
              )}
            </button>
          )}
          {/* In sequenceMode these two live in the TOP row instead — see
              `finishActions`, rendered once in whichever place applies. */}
          {!sequenceMode && finishActions()}
        </div>
      )}

      {/* WHAT THE CHECKS FOUND — the free one first, the paid one under it.
          ⚠ NEITHER BLOCKS ANYTHING. This is a note beside the board, not a gate
          in front of it: half of these are judgement calls only the film-maker
          can settle, and a board with a warning on it is still their board. */}
      {!running && (audit?.findings?.length > 0 || checkResult || checkError) && (
        <div className="board-audit">
          {(audit?.findings || []).map((f) => (
            <div key={f.code} className={`board-audit-row is-${f.severity}`}>
              <span className="board-audit-msg">{f.message}</span>
              {f.panels?.length > 0 && (
                <span className="tiny muted">
                  {" "}
                  · shot{f.panels.length === 1 ? " " : "s "}
                  {f.panels.map((i) => i + 1).join(", ")}
                </span>
              )}
              {f.hint && <div className="tiny muted">{f.hint}</div>}
            </div>
          ))}

          {checkError && <div className="board-audit-row is-error">{checkError}</div>}

          {/* ⚠ "NOTHING FOUND" IS PRINTED, not left blank. A check that says
              nothing when it passes is a check nobody believes ran. */}
          {checkResult && checkResult.findings.length === 0 && (
            <div className="board-audit-row is-ok">
              Checked {checkResult.checked} panel
              {checkResult.checked === 1 ? "" : "s"} — no wrong currency, wrong
              language or invented logo found.
            </div>
          )}
          {checkResult?.findings.map((f, i) => (
            <div key={`${f.panel}-${f.kind}-${i}`} className="board-audit-row is-warning">
              <span className="board-audit-msg">Shot {f.panel}</span>
              <span className="tiny muted"> · {f.kind}</span>
              <div className="tiny muted">{f.detail}</div>
            </div>
          ))}
        </div>
      )}

      {running && (
        <div className="job-progress">
          <div className="jp-row">
            <span className="jp-msg">
              <span className="spinner-inline" />
              {progress.message || "Starting…"}
            </span>
            <span className="jp-pct">{progress.percent ?? 0}%</span>
          </div>
          <div className="jp-bar">
            <div className="jp-fill" style={{ width: `${progress.percent ?? 0}%` }} />
          </div>
        </div>
      )}

      {/* The source script, ABOVE the panels and collapsed. It's reference for
          reading the board (the shot cards cite "LINE n"), so it belongs with
          the board's other context — not wedged under the grid where it pushed
          the download buttons off the end of the page. */}
      <ScriptPanel script={boardScript} defaultOpen={false} />

      {/* One column in sequence mode: each shot's key-pose strip sits directly
          under its panel, so shot 2 reads BELOW shot 1 rather than beside it.
          A grid would put the strip in a narrow column and break the reading
          order the flipbook depends on. */}
      {/* ⚠ THE ASSISTANT IS OFF IN sequenceMode AND WHILE GENERATING. Image to
          Animatic Image is about the MOTION of a board that was already
          finished elsewhere, so editing its shots here would put the key poses
          out of step with the panels they came from; and a board still drawing
          has shot numbers that are still moving. */}
      <div className={`board-workspace ${showAssistant ? "with-ai" : ""}`}>
      <div className={`board-grid ${sequenceMode ? "board-column" : ""}`}>
        {panels.map((p) => {
          // A new panel the user inserted: no image yet, board not generating.
          const isNew = !p.url && !p.failed && !running;
          const picked =
            (selection.kind === "panel" && selection.shot === p.index + 1) ||
            (selection.kind === "scene" && selection.scene === p.scene_number);
          return (
            <figure
              className={`board-tile ${picked ? "is-selected" : ""}`}
              key={p.index}
            >
              <div
                className={`board-frame ${retrying[p.index] ? "is-redrawing" : ""}`}
                style={{ aspectRatio: tileRatio }}
              >
                {p.url && panelUrls[p.url] ? (
                  <img
                    src={panelUrls[p.url]}
                    alt={`Panel ${p.index + 1}`}
                    onClick={() => setLightbox(panelUrls[p.url])}
                  />
                ) : p.failed ? (
                  <div className="board-failed">
                    {retrying[p.index] ? (
                      <>
                        <span className="spinner" /> Redrawing…
                      </>
                    ) : (
                      <span>⚠️ Couldn’t draw this panel</span>
                    )}
                  </div>
                ) : isNew ? (
                  <div className="board-newpanel">
                    {retrying[p.index] ? (
                      <>
                        <span className="spinner" /> Drawing…
                      </>
                    ) : (
                      <span>✏️ New panel — write a prompt, then Generate</span>
                    )}
                  </div>
                ) : (
                  <div className="board-skeleton" />
                )}

                {/* REDRAWING A PANEL THAT ALREADY HAS A PICTURE. The branches
                    above only show a spinner for a FAILED or a NEW panel — a
                    shot with an image kept showing that image, unchanged, for
                    the whole 30-60s redraw, so the board looked like it had
                    ignored the click. Same veil the key-pose strip uses, for
                    the same reason and with the same wording. */}
                {retrying[p.index] && p.url && panelUrls[p.url] && (
                  <span className="redraw-veil">
                    <span className="spinner-inline" />
                    <span className="tiny">Redrawing…</span>
                  </span>
                )}

                {/* Sits ON the picture, bottom-right. Renders nothing until the
                    shot has been redrawn at least once, so an untouched board
                    looks exactly as it always did. */}
                {p.url && (
                  <PanelVersions
                    jobId={jobId}
                    index={p.index}
                    disabled={running || !!retrying[p.index]}
                    /* Just THIS panel. Switching a version changes one shot's
                       pixels and shifts no indices, so there is nothing for the
                       rest of the board to re-read — it used to call
                       `reloadBoard`, which dropped every tile's blob and made
                       the whole page blink on every ‹ › press. */
                    onSwitched={() => refreshPanelImage(p.index, p.url)}
                  />
                )}
              </div>
              <figcaption>
                <div className="board-caption-head">
                  {/* ⚠ THE NUMBER IS THE SELECTOR, NOT THE PICTURE. Clicking
                      the image opens the lightbox and always has; hanging
                      selection off it would mean choosing between looking at a
                      shot and talking about one. */}
                  <span className="board-shotnum">
                    <button
                      type="button"
                      className={`board-pick ${
                        selection.kind === "panel" && selection.shot === p.index + 1
                          ? "on"
                          : ""
                      }`}
                      onClick={() => toggleSelectPanel(p.index + 1)}
                      title="Talk to the AI about this shot"
                    >
                      Shot {p.index + 1}
                    </button>
                    {p.scene_number ? (
                      <button
                        type="button"
                        className={`board-scene board-pick ${
                          selection.kind === "scene" &&
                          selection.scene === p.scene_number
                            ? "on"
                            : ""
                        }`}
                        onClick={() => toggleSelectScene(p.scene_number)}
                        title="Talk to the AI about this whole scene"
                      >
                        Scene {p.scene_number}
                      </button>
                    ) : null}
                  </span>
                  {/* Structural edits only while the board isn't generating. */}
                  {!running && (
                    <div className="board-tile-actions">
                      <button
                        type="button"
                        className="shot-btn"
                        onClick={() => addPanelAt(p.index)}
                        disabled={editBusy}
                        title="Add a panel before this one"
                      >
                        ＋
                      </button>
                      <button
                        type="button"
                        className="shot-btn danger"
                        onClick={() => deletePanel(p.index)}
                        disabled={editBusy || panels.length <= 1}
                        title="Delete this panel"
                      >
                        ✕
                      </button>
                    </div>
                  )}
                </div>
                {/* ⚠ GROWS TO ITS TEXT. It was `rows={2}` and every shot whose
                    description ran past two lines was cut in half behind a
                    scrollbar — the one thing on the tile the user is meant to
                    EDIT was the one thing they could not read. Third and last
                    place this box lives; the review step and cast/props were
                    already fixed. */}
                <GrowTextarea
                  className="board-caption-edit"
                  value={editedDesc[p.index] ?? p.description ?? ""}
                  onChange={(e) =>
                    setEditedDesc((d) => ({ ...d, [p.index]: e.target.value }))
                  }
                  rows={2}
                  placeholder="Describe what we see in this shot…"
                />
                {/* What is spoken in this panel — nothing at all for a silent
                    shot. Read-only here; dialogue is edited on the shot list.
                    ⚠ ORDER: image prompt → dialogue → camera/location → cast.
                    The review card and the PDF print the same order; a panel
                    that reads differently in three places reads as three
                    different tools. */}
                <DialogueBox dialogue={p.dialogue} className="board-dialogue" />
                {/* Framing · move · length, on one line and in the same order
                    the PDF prints them. ⚠ NONE OF IT IS IN THE PICTURE — a
                    still frame cannot show a move or a length; these are the
                    director's read, and the animatic step is where they
                    actually do something. */}
                {shotLine(p) && (
                  <p className="board-shotline tiny muted">{shotLine(p)}</p>
                )}
                {/* Eats whatever height is left over, so the draw button below
                    lands on the SAME line in every tile of a row. Without it
                    the button rode up under any shot with no dialogue line and
                    a row of four panels read as four buttons at four different
                    heights. Inert in sequenceMode — there the key-pose strip is
                    the thing pinned to the bottom. */}
                <div className="board-tile-fill" aria-hidden="true" />
                {/* ⚠ THE BUTTON STAYS, GREYED AND LABELLED. A board of
                    forty panels with no draw button on any of them and no
                    sentence anywhere saying why is the exact failure this
                    change exists to prevent. */}
                <button
                  type="button"
                  className={`btn small board-regen-btn ${isNew ? "secondary" : ""} ${
                    imageCap.on ? "" : "cap-off"
                  }`}
                  onClick={() => retryPanel(p.index)}
                  disabled={!imageCap.on || retrying[p.index]}
                  title={
                    !imageCap.on
                      ? imageCap.reason
                      : isNew
                        ? "Draw this panel"
                        : "Re-draw this shot with the current prompt"
                  }
                >
                  {!imageCap.on ? (
                    "🔒 Locked"
                  ) : retrying[p.index] ? (
                    <>
                      <span className="spinner-inline" /> {isNew ? "Generating…" : "Redrawing…"}
                    </>
                  ) : isNew ? (
                    "✨ Generate this panel"
                  ) : p.failed ? (
                    "🔄 Retry"
                  ) : sequenceMode ? (
                    /* In this workflow the panel is a starting point you draw
                       FROM, so the action is "Generate", not "Regenerate" —
                       the same word the key-pose button uses. */
                    "✨ Generate panel"
                  ) : (
                    "🔄 Regenerate"
                  )}
                </button>

                {sequenceMode && (
                  <PanelSequenceStrip
                    jobId={jobId}
                    index={p.index}
                    label={`Scene ${p.scene_number ?? 1} · Shot ${p.shot_number ?? p.index + 1}`}
                    boardBusy={running}
                    progress={progress}
                    /* The same shape the panels above are drawn at — a 9:16
                       board's key poses were being cropped to a 16:9 slice. */
                    ratio={aspect || "16:9"}
                    plannedSeconds={Number(p.duration_seconds) || 0}
                    onError={setPdfError}
                    onStarted={() => setPollNonce((n) => n + 1)}
                  />
                )}
              </figcaption>
            </figure>
          );
        })}

        {/* Placeholders for shots not yet reached */}
        {Array.from({ length: pendingCount }).map((_, i) => (
          <figure className="board-tile" key={`pending-${i}`}>
            <div className="board-frame" style={{ aspectRatio: tileRatio }}>
              <div className="board-skeleton" />
            </div>
            <figcaption>
              <span className="board-shotnum">Shot {panels.length + i + 1}</span>
              Waiting…
            </figcaption>
          </figure>
        ))}

        {/* Append a new panel at the end (only when nothing is generating). */}
        {!running && panels.length > 0 && (
          <button
            type="button"
            className="board-tile board-add-tile"
            onClick={() => addPanelAt(panels.length)}
            disabled={editBusy}
            title="Add a panel at the end"
          >
            {editBusy ? <span className="spinner" /> : <span>＋ Add a panel</span>}
          </button>
        )}
      </div>

      {showAssistant && (
        <aside className="board-ai">
          <BoardAssistant
            jobId={jobId}
            selection={selection}
            onClearSelection={() => setSelection({ kind: "none" })}
            onApply={applyBoardActions}
            disabled={running || editBusy}
          />
          <button
            type="button"
            className="btn ghost small board-ai-hide"
            onClick={() => setAssistantOpen(false)}
          >
            Hide the assistant
          </button>
        </aside>
      )}
      </div>

      {/* Brought back with one click; a board is wide and some people want the
          whole width for the pictures. */}
      {canAssist && !assistantOpen && (
        <button
          type="button"
          className="btn secondary board-ai-show"
          onClick={() => setAssistantOpen(true)}
        >
          ✨ Ask AI about this board
        </button>
      )}

      {lightbox && (
        <div className="lightbox-overlay" onClick={() => setLightbox(null)}>
          <div className="lightbox-figure" onClick={(e) => e.stopPropagation()}>
            <button className="lightbox-close" onClick={() => setLightbox(null)}>
              ✕
            </button>
            <img className="lightbox-img" src={lightbox} alt="Panel" />
          </div>
        </div>
      )}
    </div>
  );
}
