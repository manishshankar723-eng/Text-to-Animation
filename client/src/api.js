// api.js — thin client for the Character Asset Generation API.
// Handles the JWT bearer token, JSON vs. multipart bodies, and error messages.

const BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
const TOKEN_KEY = "cas_token";
const EMAIL_KEY = "cas_email";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function getEmail() {
  return localStorage.getItem(EMAIL_KEY);
}
export function setSession(token, email) {
  localStorage.setItem(TOKEN_KEY, token);
  if (email) localStorage.setItem(EMAIL_KEY, email);
}
export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
}

// Ride out a backend that is momentarily unreachable (typically uvicorn
// --reload restarting after a code change). Only connection failures are
// retried — once the server answers, its response is returned as-is, errors
// included, so real 4xx/5xx are never re-sent.
// A request that never answers must not hang FOREVER. `fetch()` has no timeout
// of its own: if the server accepts the connection and then goes quiet — a
// wedged database lookup is the usual way — the promise simply never settles,
// every caller's `loading` stays true, and the screen shimmers with no error
// until the tab is reloaded. That was reported as "why do all the panels look
// like this". Generous, because some calls are legitimately slow (the script
// breakdown and a single-panel redraw are synchronous AI calls), but finite.
const REQUEST_TIMEOUT_MS = 120000;

async function fetchWithRetry(url, options, attempts = 3, delayMs = 700) {
  for (let i = 1; ; i++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } catch (e) {
      // A TIMEOUT is not a blip — re-sending would just wait another two
      // minutes for the same silent server. Only a failed connection is worth
      // retrying (in dev that is usually uvicorn's --reload restarting).
      if (e?.name === "AbortError") {
        throw new Error(
          `The server didn't respond within ${REQUEST_TIMEOUT_MS / 1000}s. ` +
            `It may be stuck (a database it needs can do this) — check the ` +
            `backend's log, then try again.`
        );
      }
      if (i >= attempts) throw e;
      await new Promise((r) => setTimeout(r, delayMs));
    } finally {
      clearTimeout(timer);
    }
  }
}

async function request(path, { method = "GET", body, isForm = false } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let payload = body;
  if (body && !isForm) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let res;
  try {
    // fetch() rejects with a TypeError when the browser can't reach the server
    // at all. In dev that is nearly always uvicorn's --reload restarting, a
    // window of a second or two, so retry briefly before giving up. A rejected
    // fetch means the request never got a response, so re-sending is safe.
    res = await fetchWithRetry(`${BASE}${path}`, { method, headers, body: payload });
  } catch (e) {
    // "Couldn't connect" and "connected, then silence" are different faults
    // with different fixes, so don't flatten the second into the first — the
    // timeout message says the server is UP but stuck, which is the harder
    // case to diagnose and the one worth naming.
    if (e?.message?.includes("didn't respond")) throw e;
    throw new Error(
      `Can't reach the server at ${BASE}. Make sure the backend is running ` +
        `(uvicorn) and reachable, then try again.`
    );
  }

  if (res.status === 401) {
    clearSession();
    throw new Error("Session expired — please log in again.");
  }
  if (!res.ok) {
    let detail;
    try {
      const j = await res.json();
      detail = j.detail;
    } catch {
      detail = res.statusText;
    }
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail) || "Request failed"
    );
  }

  // A 204/205 has NO body, but FastAPI still labels it `application/json`.
  // Parsing on the content-type alone therefore threw "Unexpected end of JSON
  // input" and turned every successful DELETE (animatic, storyboard, job, saved
  // API key) into an error the UI showed while the thing really had been
  // deleted. Check for a body before trying to read one.
  if (res.status === 204 || res.status === 205) return null;
  if (res.headers.get("content-length") === "0") return null;

  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

// --- Auth ---
export function register(email, password) {
  return request("/auth/register", { method: "POST", body: { email, password } });
}
export function login(email, password) {
  return request("/auth/login", { method: "POST", body: { email, password } });
}
export function me() {
  return request("/auth/me");
}
// Partial: send only the fields being edited. Anything omitted is left alone,
// and privilege fields (email / disabled / password_hash) are ignored server-side.
export function updateProfile(fields) {
  return request("/auth/me", { method: "PATCH", body: fields });
}
export function changePassword(currentPassword, newPassword) {
  return request("/auth/me/password", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}
export function deleteAccount() {
  return request("/auth/me", { method: "DELETE" });
}

// --- 3D provider API keys (saved on profile) ---
export function getApiKeys() {
  return request("/auth/me/api-keys"); // → { meshy: true, ... }
}
export function saveApiKey(provider, apiKey) {
  return request("/auth/me/api-keys", {
    method: "PUT",
    body: { provider, api_key: apiKey },
  });
}
export function deleteApiKey(provider) {
  return request(`/auth/me/api-keys/${provider}`, { method: "DELETE" });
}

// --- Script draft (autosave) ---
// The script currently being written, so a refresh can't lose it. ONE draft per
// account; saving overwrites it. Reading never 404s — a user who has never
// saved gets an empty draft back.
export function getScriptDraft() {
  return request("/scripts/draft"); // → { text, title, updated_at }
}
export function saveScriptDraft({ text, title } = {}) {
  return request("/scripts/draft", {
    method: "PUT",
    body: { text: text || "", title: title || "" },
  });
}
export function clearScriptDraft() {
  return request("/scripts/draft", { method: "DELETE" });
}

// --- Plan & Script ---
// A planning session is a conversation with the strategist agent plus the
// calendar it produced. Text quota only — nothing here generates an image.
export function listPlans() {
  return request("/plans");
}
export function createPlan(title) {
  return request("/plans", { method: "POST", body: { title: title || null } });
}
export function getPlan(planId) {
  return request(`/plans/${planId}`);
}
export function renamePlan(planId, title) {
  return request(`/plans/${planId}`, { method: "PATCH", body: { title } });
}
export function deletePlan(planId) {
  return request(`/plans/${planId}`, { method: "DELETE" });
}
export function sendPlanMessage(planId, message) {
  return request(`/plans/${planId}/chat`, { method: "POST", body: { message } });
}
export function attachPlanChannel(planId, url) {
  return request(`/plans/${planId}/channel`, { method: "POST", body: { url } });
}
export function generatePlan(planId, { months, cadence } = {}) {
  return request(`/plans/${planId}/generate`, {
    method: "POST",
    body: { months: months || 1, cadence: cadence || null },
  });
}
export function youtubeConfigured() {
  return request("/plans/config/youtube"); // → { configured: bool }
}
// Exports are binary — fetched as an authed blob and handed to the browser,
// the same way the storyboard PDF/ZIP downloads work. The server names the
// file from the plan title; `serverFilename` reads that back off the header.
export async function downloadPlan(planId, format) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}/plans/${planId}/export?format=${format}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = serverFilename(res, `plan.${format}`);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- Storyboard draft (the review step's backing store) ---
// A breakdown is saved server-side the moment it returns, so the reviewed
// shots / cast / assets / world survive a refresh. `getStoryboardDraft` returns
// { job_id: null, … } when there is nothing to resume — not an error.
export function getStoryboardDraft() {
  return request("/storyboards/draft");
}
export function saveStoryboardDraft(jobId, fields) {
  // PATCH is partial: send only what changed. Omitted fields are left alone.
  return request(`/storyboards/draft/${jobId}`, { method: "PATCH", body: fields });
}
export function discardStoryboardDraft(jobId) {
  return request(`/storyboards/draft/${jobId}`, { method: "DELETE" });
}

// --- Script → Storyboard ---
// Stage A: break a script into an ordered shot list. Returns { shots, count,
// style, aspect_ratio }. `provider` is optional ("vertex" | "gemini").
// Also returns `draft_job_id`: the breakdown is saved as a DRAFT job so the
// review step is backed by the database. Pass `title` so the saved draft is
// named something better than its opening words.
export function breakdownScript(script, { style, aspectRatio, genre, provider, title } = {}) {
  return request("/storyboards/breakdown", {
    method: "POST",
    body: { script, style, aspect_ratio: aspectRatio, genre, provider, title },
  });
}

// Stage D: generate panels from the reviewed shots. Returns a job (poll getJob).
// `characterRefs` / `assetRefs` are optional { name: reference_id } maps that
// lock character faces / props+backgrounds so they stay consistent across panels.
export function createStoryboard({
  shots,
  style,
  aspectRatio,
  title,
  genre,
  characters,
  assets,
  characterRefs,
  assetRefs,
  assetCategories,
  world,
  script,
  provider,
  draftJobId,
} = {}) {
  return request("/storyboards", {
    method: "POST",
    body: {
      shots,
      style,
      aspect_ratio: aspectRatio,
      title,
      genre,
      // THE WRITTEN CONTINUITY BIBLE — the reviewed cast and asset lists WITH
      // their descriptions. Every panel is told what the people in it look
      // like, which is what stops the same character being drawn as a
      // different person from shot to shot. Sent for every style, including
      // the ones that skip the reference-image steps entirely: words cost
      // nothing, and for those styles they are the only continuity there is.
      characters: characters || [],
      assets: assets || [],
      // Promotes the draft this board was reviewed as, instead of creating a
      // second record. Harmless to omit.
      draft_job_id: draftJobId || null,
      // The script's region/period/culture — prefixed onto every panel prompt.
      world: world || null,
      // Saved so a re-opened / duplicated board can still show the source script.
      script: script || null,
      character_refs: characterRefs || {},
      asset_refs: assetRefs || {},
      // Lets the assets ZIP file each reference under props/ or backgrounds/.
      asset_categories: assetCategories || {},
      provider,
    },
  });
}

// --- Storyboard library ("Your Storyboards") ---
// A saved project IS a storyboard job, so these all read/write the same records
// the board itself uses — nothing can drift out of sync.

// Every storyboard this user has generated, newest first.
// Boards belong to a workflow. Script to Storyboard's own boards carry no tag
// (pass nothing); Image to Animatic Image asks for its copies by name, so
// neither library shows the other's.
export function listStoryboards(workflow = "") {
  const q = workflow ? `?workflow=${encodeURIComponent(workflow)}` : "";
  return request(`/storyboards${q}`);
}

// Deep-copy a board — its own record AND its own panel files, so drawing or
// restyling the copy can never reach back into the original. This is what
// "From a Storyboard" does in Image to Animatic Image.
export function copyStoryboard(jobId, workflow = "") {
  const q = workflow ? `?workflow=${encodeURIComponent(workflow)}` : "";
  return request(`/storyboards/${jobId}/copy${q}`, { method: "POST" });
}

// A saved board's shots + settings, for re-opening it as a new storyboard.
export function getStoryboardProject(jobId) {
  return request(`/storyboards/${jobId}/project`);
}

export function renameStoryboard(jobId, title) {
  return request(`/storyboards/${jobId}`, { method: "PATCH", body: { title } });
}

// Deletes the record AND the generated panel files — not undoable.
export function deleteStoryboard(jobId) {
  return request(`/storyboards/${jobId}`, { method: "DELETE" });
}

// Turn the public link on / off. Returns { shared, share_token }.
export function shareStoryboard(jobId) {
  return request(`/storyboards/${jobId}/share`, { method: "POST" });
}
export function unshareStoryboard(jobId) {
  return request(`/storyboards/${jobId}/share`, { method: "DELETE" });
}

// The link handed out for a shared board. Opening the app with ?s=<token>
// renders the read-only public viewer instead of the login screen.
export function shareUrl(token) {
  return `${window.location.origin}${window.location.pathname}?s=${token}`;
}

// --- Public (shared) board — these are the only calls that work logged OUT ---
export function getPublicStoryboard(token) {
  return request(`/public/storyboards/${token}`);
}

// Public panels need no auth, so an <img src> can point straight at them.
export function publicPanelUrl(token, index) {
  return `${BASE}/public/storyboards/${token}/panel/${index}`;
}

// Fetch one generated panel as an object URL (endpoint requires the bearer token,
// so we can't point an <img src> straight at it). `path` is the panel's own URL
// (which carries the ?v=<variant> query when the board has style variants).
export async function fetchStoryboardPanel(jobId, index, path) {
  const token = getToken();
  let rel = path || `/storyboards/${jobId}/panel/${index}`;
  // Cache-bust so a regenerated panel (same URL, new pixels) isn't served stale.
  rel += `${rel.includes("?") ? "&" : "?"}_=${Date.now()}`;
  const res = await fetch(`${BASE}${rel}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Panel ${index} not ready`);
  return URL.createObjectURL(await res.blob());
}

// Re-draw the whole board in a new style (kept as a new variant). Returns a job.
// --- Panel versions: every redraw is kept, so nothing is lost ---------------
// Re-drawing a shot archives the new picture instead of overwriting the old
// one, so you can step back to the version you preferred.

export function getPanelVersions(jobId, index) {
  return request(`/storyboards/${jobId}/panels/${index}/versions`);
}

// Make version `n` the panel's picture again. Everything downstream (PDF, ZIP,
// key poses, animatic) reads the active picture, so this switches all of them.
export function usePanelVersion(jobId, index, n) {
  return request(`/storyboards/${jobId}/panels/${index}/versions/${n}`, {
    method: "POST"
  });
}

// --- Image to Animatic Image: one panel → its key-pose sequence ------------
// The "flipbook". `durationSeconds` is the SHOT LENGTH (2/4/6/8/10); how many
// drawings that means is decided server-side (4 per second, so 4s = 16), so the
// client can never ask for hundreds of images.

// What key poses this shot has so far.
export function getPanelSequence(jobId, index) {
  return request(`/storyboards/${jobId}/panels/${index}/sequence`);
}

// SPENDS IMAGE CREDITS — one per drawing. Async: poll getJob(jobId).
// Stop with stopStoryboard(jobId); calling this again RESUMES from the frames
// already drawn, so nothing is paid for twice.
// `preview` draws only the first couple of poses and stops, so you can see
// whether the shot actually moves before paying for all of them. Continuing
// afterwards is an ordinary resume — nothing already drawn is redrawn.
export function generatePanelSequence(
  jobId,
  index,
  durationSeconds,
  resume = true,
  preview = false,
  redraw = []
) {
  return request(`/storyboards/${jobId}/panels/${index}/sequence`, {
    method: "POST",
    body: { duration_seconds: durationSeconds, resume, preview, redraw }
  });
}

// Re-draw ONE key pose that came out wrong, reusing the pose plan the sequence
// was built from. Costs a single image, and leaves every other drawing alone.
export function redrawPanelPose(jobId, index, durationSeconds, poseNumber) {
  return generatePanelSequence(jobId, index, durationSeconds, true, false, [
    poseNumber
  ]);
}

// One shot's key poses as a ZIP (pose_001.png … in play order).
export async function downloadPanelFrames(jobId, index, filename) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(
      `${BASE}/storyboards/${jobId}/panels/${index}/frames.zip`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} }
    );
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = serverFilename(res, filename || "key-poses.zip");
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Throw the sequence away so Generate starts clean instead of resuming.
export function deletePanelSequence(jobId, index) {
  return request(`/storyboards/${jobId}/panels/${index}/sequence`, {
    method: "DELETE"
  });
}

export function restyleStoryboard(jobId, style) {
  return request(`/storyboards/${jobId}/restyle`, {
    method: "POST",
    body: { style },
  });
}

// Stop a board that is still generating. Panels not yet started are skipped;
// the ones already in flight finish. Returns { stopping: true }.
export function stopStoryboard(jobId) {
  return request(`/storyboards/${jobId}/stop`, { method: "POST" });
}

// Stop a character run (Text to Image). The part being drawn finishes, nothing
// after it starts, and what's already generated stays downloadable.
export function stopJob(jobId) {
  return request(`/jobs/${jobId}/stop`, { method: "POST" });
}

// Switch which style variant is shown/exported (no regeneration).
export function setActiveVariant(jobId, index) {
  return request(`/storyboards/${jobId}/active-variant`, {
    method: "POST",
    body: { index },
  });
}

// Insert a blank panel at position `at` (shifts the rest down). The new panel
// has no image — generate it afterwards with regenerateStoryboardPanel.
export function insertStoryboardPanel(jobId, at, description = "") {
  return request(`/storyboards/${jobId}/panels/insert`, {
    method: "POST",
    body: { at, description },
  });
}

// Delete the panel at `index` (removes its image, shifts the rest up).
export function deleteStoryboardPanel(jobId, index) {
  return request(`/storyboards/${jobId}/panels/${index}`, { method: "DELETE" });
}

// Re-draw one panel (Retry / regenerate). Returns { panel }.
// `overrides` may carry an edited { description, camera, location } to re-draw
// the shot with new wording.
export function regenerateStoryboardPanel(jobId, index, overrides = {}) {
  return request(`/storyboards/${jobId}/regenerate-panel`, {
    method: "POST",
    body: { index, ...overrides },
  });
}

// The name the SERVER wants this file saved as. Downloads go through fetch as
// authed blobs, so the browser never applies Content-Disposition itself — we
// read it and put it on the <a download>. The server derives it from the board
// title (one source of truth), and `fallback` covers a deployment that hasn't
// exposed the header yet.
function serverFilename(res, fallback) {
  const cd = res.headers.get("content-disposition") || "";
  // filename*=UTF-8''name.pdf  (preferred, encoded) or filename="name.pdf"
  const star = /filename\*=\s*UTF-8''([^;]+)/i.exec(cd);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch {
      /* malformed encoding — fall through to the plain form */
    }
  }
  const plain = /filename=\s*"?([^";]+)"?/i.exec(cd);
  return plain ? plain[1].trim() : fallback;
}

// Stage F: download the board as a PDF (authed blob → browser download).
// `filename` is only a FALLBACK — the server's own name wins when readable.
export async function downloadStoryboardPdf(jobId, filename) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}/storyboards/${jobId}/pdf`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = serverFilename(res, filename || "storyboard.pdf");
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Download a reusable ZIP of generated references (characters + props/backgrounds)
// plus the storyboard PDF (authed blob → browser download).
export async function downloadStoryboardBundle(jobId, filename) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}/storyboards/${jobId}/bundle`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = serverFilename(res, filename || "storyboard_assets.zip");
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- Storyboard → Animatic -------------------------------------------------
// A saved animatic IS a job (kind "animatic"), the same call the storyboard
// library made. Nothing here costs AI quota — it's images, timing and audio.

// Start a new animatic. Pass `sourceStoryboardId` alone and the server fills the
// sequence with that board's drawn panels (the board's "Make animatic" button).
export function createAnimatic({
  title,
  sourceStoryboardId,
  settings,
  frames,
  defaultDurationMs,
} = {}) {
  return request("/animatics", {
    method: "POST",
    body: {
      title: title || null,
      source_storyboard_id: sourceStoryboardId || null,
      settings: settings || null,
      frames: frames || [],
      default_duration_ms: defaultDurationMs || 2000,
    },
  });
}

export function listAnimatics() {
  return request("/animatics");
}
export function getAnimatic(id) {
  return request(`/animatics/${id}`);
}

// Save the edited project. Every field is optional; removing the audio needs
// `clear_audio: true`, because `audio: null` can't be told apart from "not sent".
export function saveAnimatic(
  id,
  {
    title, settings, frames, assets, texts, shapes, layers, overlays, transitions,
    audioTracks,
  } = {}
) {
  const body = {};
  if (title !== undefined) body.title = title;
  if (settings !== undefined) body.settings = settings;
  if (frames !== undefined) body.frames = frames;
  if (texts !== undefined) body.texts = texts;
  // Whole list, like the audio tracks — an empty array removes every shape.
  if (shapes !== undefined) body.shapes = shapes;
  // The lanes themselves, and the pictures composited over the sequence.
  if (layers !== undefined) body.layers = layers;
  // THE MEDIA LIBRARY. Whole list, like the shapes — an empty array empties it,
  // which is a thing the user can do (✕ on the last card) and must therefore be
  // sayable.
  //
  // ⚠ THIS DESTRUCTURED LIST IS A WHITELIST, AND IT HAS DROPPED A FIELD ALREADY.
  // `assets` was added to `AnimaticSaveRequest`, to `flush`, to the signature and
  // to the schema — and not to this line, so every save quietly sent the whole
  // project WITHOUT the library and the server never heard of it. Nothing errors:
  // an unnamed key simply isn't in `body`. Caught by
  // `tests/editor_media_bin_check.py`, which asserts on the PUT body itself for
  // exactly this reason. Same trap as `frameForSave`; add to BOTH.
  if (assets !== undefined) body.assets = assets;
  if (overlays !== undefined) body.overlays = overlays;
  // What happens on the cuts. Whole list again — an empty array puts the
  // sequence back to straight cuts.
  if (transitions !== undefined) body.transitions = transitions;
  // The whole list, every time. An empty array removes every track, so there's
  // no companion "clear" flag to keep in step.
  if (audioTracks !== undefined) body.audio_tracks = audioTracks;
  return request(`/animatics/${id}`, { method: "PUT", body });
}

export function deleteAnimatic(id) {
  return request(`/animatics/${id}`, { method: "DELETE" });
}

// --- LUTs -------------------------------------------------------------------
// The colour tables the Effects pane offers. A LUT is a FILE both sides read:
// the exporter loads it with Pillow's Color3DLUT and the monitor fetches the
// same bytes into a WebGL texture, so there is one copy of the numbers and
// nothing to keep in step by hand.
export function listLuts() {
  return request("/animatics/luts");
}

// The .cube itself, as text. Not JSON, so `request` hands back the Response and
// the caller reads it — see `loadLut` in animatic/gl/lut.js, which parses and
// caches it for the life of the tab.
export function getLutFile(name) {
  return request(`/animatics/luts/${encodeURIComponent(name)}`);
}

// Upload images into an animatic. They're stored but NOT sequenced — the client
// decides the order and saves the project afterwards.
export function uploadAnimaticImages(id, files) {
  const fd = new FormData();
  for (const file of files) fd.append("files", file);
  return request(`/animatics/${id}/images`, { method: "POST", body: fd, isForm: true });
}

// Upload video clips into an animatic. Stored but NOT sequenced, exactly like
// the images — the client decides where on the timeline they land.
//
// Each item comes back with a `duration_ms` MEASURED BY THE SERVER (ffmpeg),
// not by the browser as an audio track's is. It has to be the same number the
// exporter works from, so there is one measurer; 0 means it couldn't be read,
// and the clip opens at the default hold instead of its natural length.
export function uploadAnimaticVideos(id, files) {
  const fd = new FormData();
  for (const file of files) fd.append("files", file);
  return request(`/animatics/${id}/videos`, { method: "POST", body: fd, isForm: true });
}

// Bring a STORYBOARD's drawn panels into an animatic that already exists.
//
// ⚠ NOT `createAnimatic({ source_storyboard_id })`, which starts a NEW project
// from a board. This one is pressed mid-cut and returns the frames instead of
// saving them, because which row they land on is the editor's decision — see
// `import_storyboard` on the server.
export function importStoryboardIntoAnimatic(id, storyboardId, defaultDurationMs = 2000) {
  return request(`/animatics/${id}/import-storyboard`, {
    method: "POST",
    body: { storyboard_id: storyboardId, default_duration_ms: defaultDurationMs },
  });
}

export function uploadAnimaticAudio(id, file) {
  const fd = new FormData();
  fd.append("file", file);
  return request(`/animatics/${id}/audio`, { method: "POST", body: fd, isForm: true });
}

// --- Animating a frame with Veo, from inside the editor ---------------------
// ⚠ The one path in the animatic editor that SPENDS MONEY. The pair mirrors
// `estimateFinalVideo` / `renderFinalVideoShots` exactly, including taking the
// SAME body: the number the confirm dialog shows can then only be the price of
// the thing the button goes on to do.

// Free to call. Drives the confirm dialog, so the price is on screen before the
// button that spends it.
export function estimateAnimateFrames(id, { frameIds, prompts, render, force } = {}) {
  return request(`/animatics/${id}/animate/estimate`, {
    method: "POST",
    body: {
      frame_ids: frameIds || [],
      prompts: prompts || {},
      render,
      force: !!force,
    },
  });
}

// SPENDS MONEY. Renders the named frames with Veo, async — poll getJob(id).
// Each finished clip lands as an ordinary video upload, so from that moment it
// is the same thing on the timeline as a file dragged in from the desktop.
export function animateAnimaticFrames(id, { frameIds, prompts, render, force } = {}) {
  return request(`/animatics/${id}/animate`, {
    method: "POST",
    body: {
      frame_ids: frameIds || [],
      prompts: prompts || {},
      render,
      force: !!force,
    },
  });
}

// --- Captions and voiceover -------------------------------------------------
// ⚠ The other two paths in this editor that SPEND QUOTA. Same shape as the pair
// above and for the same reason: estimate and run take the SAME body, so the
// number in the confirm dialog can only be the price of what the button does.
// Far cheaper than a Veo render, which is exactly why the discipline is kept —
// a cheap button is the one that gets pressed forty times.

// Free. What transcribing that audio track into captions would cost.
export function estimateCaptions(id, { uploadId, language, replace } = {}) {
  return request(`/animatics/${id}/captions/estimate`, {
    method: "POST",
    body: {
      upload_id: uploadId,
      language: language || "",
      replace: replace !== false,
    },
  });
}

// SPENDS QUOTA. Writes caption clips from one audio track, async — poll
// getJob(id), then re-read the project: the captions are written server-side.
export function captionAnimatic(id, { uploadId, language, replace } = {}) {
  return request(`/animatics/${id}/captions`, {
    method: "POST",
    body: {
      upload_id: uploadId,
      language: language || "",
      replace: replace !== false,
    },
  });
}

// Free, and calls no model. THE DIALOGUE SHEET: every spoken line on this
// timeline, the shot it belongs to, a persona guessed from the board's cast, and
// the two pickers (`voices`, `personas`) — which come from the server because
// `tts.CAST` is the only place a voice exists.
export function getAnimaticDialogue(id) {
  return request(`/animatics/${id}/dialogue`);
}

// The sheet, on its way back up. ⚠ SENT ON BOTH CALLS, so the price quoted is
// the price of the words on screen: an edited line is cheaper or dearer than the
// board's, and a quote for something else is a quote that looks made up.
function voiceoverBody({ voice, frameIds, lines, fitShots, addCaptions, replace }) {
  return {
    voice: voice || "Kore",
    frame_ids: frameIds || [],
    lines: (lines || []).map((l) => ({
      frame_id: l.frame_id || "",
      character: l.character || "",
      persona: l.persona || "",
      voice: l.voice || "",
      text: l.text || "",
    })),
    fit_shots: fitShots !== false,
    add_captions: addCaptions !== false,
    replace: replace !== false,
  };
}

// Free. What reading the board's dialogue aloud would cost.
export function estimateVoiceover(id, opts = {}) {
  return request(`/animatics/${id}/voiceover/estimate`, {
    method: "POST",
    body: voiceoverBody(opts),
  });
}

// SPENDS QUOTA. Reads the dialogue aloud onto the audio layer, async — same
// polling as the captions call. The spoken lines come back as captions too,
// timed to when they were ACTUALLY read rather than when they were asked for.
//
// ⚠ IT ALSO MOVES PICTURES when `fitShots` is on (the default): the shot that
// owns a line is stretched to cover it and the shots after it are pushed along,
// exactly as animating one does. So the caller must re-read the project's
// FRAMES when the job finishes, not only its texts and audio.
export function voiceAnimatic(id, opts = {}) {
  return request(`/animatics/${id}/voiceover`, {
    method: "POST",
    body: voiceoverBody(opts),
  });
}

// --- Reaching back to the BOARD from inside the editor (Phase 7) ------------
// ⚠ An animatic frame is a REFERENCE to a storyboard panel, never a copy of one,
// so redrawing the panel updates the animatic with nothing to re-import. These
// four calls are what let the editor ask for that without leaving the timeline.

// Free. The board panel behind one clip — its wording, and whether it can be
// re-drawn at all (an uploaded still and a video clip cannot).
export function getFramePanel(id, frameId) {
  return request(`/animatics/${id}/frames/${frameId}/panel`);
}

// SPENDS QUOTA. Re-draws that panel. Synchronous — one image, so there is no
// job to poll.
//
// ⚠ RETURNS THE FRAME, and its `url` carries a NEW `?v=`. That is the point:
// every picture here is an authed blob cached BY URL, so the caller re-fetches
// this one url and the shot updates everywhere at once. Throwing the response
// away and re-reading the project works too, but re-downloads the whole board.
export function regenerateFramePanel(id, frameId, overrides = {}) {
  return request(`/animatics/${id}/frames/${frameId}/panel`, {
    method: "POST",
    body: {
      description: overrides.description ?? null,
      camera: overrides.camera ?? null,
      location: overrides.location ?? null,
    },
  });
}

// Free. The key poses of the shot behind this clip, counted off disk — what to
// read after a re-block finishes to find out how many poses the shot now has.
export function getFrameSequence(id, frameId) {
  return request(`/animatics/${id}/frames/${frameId}/sequence`);
}

// SPENDS QUOTA. Re-blocks the shot at a new length ("make this shot 2s longer").
// Async, and ⚠ the job it returns is the STORYBOARD's, not this animatic's —
// the drawings belong to the board. Poll getJob(res.job_id).
//
// It RESUMES: the poses already drawn are kept and only the new tail is bought.
export function relengthFrameSequence(id, frameId, durationSeconds) {
  return request(`/animatics/${id}/frames/${frameId}/sequence`, {
    method: "POST",
    body: { duration_seconds: durationSeconds },
  });
}

// --- Auto-reframe -----------------------------------------------------------
// One vision call per shot says where the subject is; the server turns that into
// the ordinary `scale`/`x`/`y` a frame already has. Same estimate/run pair as
// every other paid path here.

// Free. What re-framing these shots would cost.
export function estimateReframe(id, { frameIds, aspectRatio } = {}) {
  return request(`/animatics/${id}/reframe/estimate`, {
    method: "POST",
    body: { frame_ids: frameIds || [], aspect_ratio: aspectRatio || "" },
  });
}

// SPENDS QUOTA. Frames each shot for a new shape, async — poll getJob(id), then
// re-read the project: the values are written server-side onto the frames.
export function reframeAnimatic(id, { frameIds, aspectRatio } = {}) {
  return request(`/animatics/${id}/reframe`, {
    method: "POST",
    body: { frame_ids: frameIds || [], aspect_ratio: aspectRatio || "" },
  });
}

// Encode the MP4 (async — poll getJob(id) for progress, same as a board).
export function exportAnimatic(id) {
  return request(`/animatics/${id}/export`, { method: "POST" });
}
export function stopAnimaticExport(id) {
  return request(`/animatics/${id}/stop`, { method: "POST" });
}

// Frames and audio live behind the bearer token, so an <img>/<audio> src can't
// point straight at them — fetch as a blob and hand back an object URL. The
// CALLER owns the URL and must revoke it.
//
// `maxEdge` asks the server for a PROXY: the same picture, losslessly resized
// so its long edge is at most that many pixels. The editor holds every frame of
// a board in memory at once to draw a monitor a few hundred pixels wide, and a
// 1920px PNG per clip is most of that memory and most of the wait on open. The
// server falls back to the source for any picture it can't proxy, so this is
// only ever a size hint. Omit it and nothing changes — which is what every
// other caller (uploads, audio, final-video stills) does.
export async function fetchAnimaticMedia(path, maxEdge = 0) {
  const token = getToken();
  let rel = path;
  if (maxEdge > 0) rel += `${rel.includes("?") ? "&" : "?"}w=${Math.round(maxEdge)}`;
  const res = await fetch(`${BASE}${rel}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Media not available");
  return URL.createObjectURL(await res.blob());
}

// SAVE ONE SOURCE FILE OUT OF A PROJECT — used by the Veo download, which is
// the one asset in an animatic that cannot be got back: an upload can be dropped
// in again and a panel is still on the board, but re-rendering this costs money.
// So it has to be savable BEFORE the project is deleted.
//
// ⚠ IT GOES THROUGH `fetch`, NOT A PLAIN LINK, for the reason every download in
// this file does: `/animatics/{id}/media/{upload_id}` requires a bearer token, and
// an `<a href>` sends no headers — it would land on a 401 page. The blob is
// fetched with the token, handed to a temporary `<a download>`, and revoked.
//
// ⚠ THE NAME IS THE CALLER'S. This route serves stills, footage and audio and
// says nothing about which — there is no Content-Disposition to read, so
// `serverFilename` has nothing to work with. The editor knows the clip's label,
// which is what the user will look for on their desk.
export async function downloadAnimaticMedia(id, uploadId, filename) {
  if (!id || !uploadId) throw new Error("There's no file behind this clip.");
  const token = getToken();
  let res;
  try {
    res = await fetch(`${BASE}/animatics/${id}/media/${uploadId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "clip.mp4";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function downloadAnimaticVideo(id, filename) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}/animatics/${id}/video`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "project.mp4";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- Animatics → Final Video -----------------------------------------------
// A project IS a job (kind "final_video"), like every other workflow. Unlike the
// animatic, this one SPENDS: each shot is a Veo render billed per second of
// output. Anything below that can spend says so in its comment.

// Start a project. Pass `sourceAnimaticId` alone and the server fills the shot
// list from that animatic's frames — the final-video library's "start from an
// animatic". The animatic editor used to have its own button for this; it was
// removed 2026-08-20, the endpoint's behaviour was not.
export function createFinalVideo({
  title,
  sourceAnimaticId,
  sourceStoryboardId,
  settings,
  shots,
} = {}) {
  return request("/final-videos", {
    method: "POST",
    body: {
      title: title || null,
      source_animatic_id: sourceAnimaticId || null,
      source_storyboard_id: sourceStoryboardId || null,
      settings: settings || null,
      shots: shots || [],
    },
  });
}

export function listFinalVideos() {
  return request("/final-videos");
}
export function getFinalVideo(id) {
  return request(`/final-videos/${id}`);
}

// Save the edited project. Sending `shots` or `art` replaces the whole list, so
// removing one is sending the list without it. Render STATE on a shot (status,
// cost, timings) is server-owned and ignored here — an autosave racing a
// finished render can't roll it back and lose a clip you paid for.
export function saveFinalVideo(id, { title, settings, shots, art } = {}) {
  const body = {};
  if (title !== undefined) body.title = title;
  if (settings !== undefined) body.settings = settings;
  if (shots !== undefined) body.shots = shots;
  if (art !== undefined) body.art = art;
  return request(`/final-videos/${id}`, { method: "PUT", body });
}

export function deleteFinalVideo(id) {
  return request(`/final-videos/${id}`, { method: "DELETE" });
}

// Is Veo reachable at all? Called before the first paid click so a missing key
// is a banner, not a failed render.
export function getVideoBackend() {
  return request("/final-videos/backend");
}

// Upload stills into the art tray (step 1). Stored but not attached to any
// shot — the caller appends the returned refs to `art` and saves.
export function uploadFinalArt(id, files) {
  const fd = new FormData();
  for (const file of files) fd.append("files", file);
  return request(`/final-videos/${id}/art`, { method: "POST", body: fd, isForm: true });
}

// What would this render cost? Free to call; drives the confirm dialog so the
// price is on screen before the button that spends it.
export function estimateFinalVideo(id, { shotIds, force } = {}) {
  return request(`/final-videos/${id}/estimate`, {
    method: "POST",
    body: { shot_ids: shotIds || [], force: !!force },
  });
}

// SPENDS MONEY. Renders shots with Veo, async — poll getJob(id) for progress.
// Empty `shotIds` means "everything not already rendered".
export function renderFinalVideoShots(id, { shotIds, force } = {}) {
  return request(`/final-videos/${id}/render`, {
    method: "POST",
    body: { shot_ids: shotIds || [], force: !!force },
  });
}

// Free. Joins the rendered clips into the cut (async — poll getJob(id)).
export function assembleFinalVideo(id) {
  return request(`/final-videos/${id}/assemble`, { method: "POST" });
}

// Stops whichever of the two is running. Keeps every clip already paid for.
export function stopFinalVideo(id) {
  return request(`/final-videos/${id}/stop`, { method: "POST" });
}

// Stills and clips sit behind the bearer token, so an <img>/<video> src can't
// point straight at them. Reuses fetchAnimaticMedia — same problem, same fix.
// The CALLER owns the returned URL and must revoke it.
export const fetchFinalVideoMedia = fetchAnimaticMedia;

export async function downloadFinalVideo(id, filename) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}/final-videos/${id}/video`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "final.mp4";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- Metadata ---
export function listTemplates() {
  return request("/templates");
}
export function health() {
  return request("/health");
}

// --- Jobs ---
// `world` is the script's region/period/culture (from the breakdown). Passing it
// is what stops the model drawing its Western default for a non-Western story.
export function generateReference(prompt, world, provider) {
  const body = { prompt };
  if (world) body.world = world;
  if (provider) body.provider = provider;
  return request("/characters/reference", { method: "POST", body });
}
// Generate a prop / background reference image (Stage B2 asset consistency).
// `category` is "prop" or "background". Returns { reference_id, image_url }.
export function generateAssetReference(prompt, category = "prop", world, provider) {
  const body = { prompt, category };
  if (world) body.world = world;
  if (provider) body.provider = provider;
  return request("/assets/reference", { method: "POST", body });
}
// Upload your own image as a character reference. Returns { reference_id, ... }.
export function uploadReference(file) {
  const fd = new FormData();
  fd.append("image", file);
  return request("/characters/reference/upload", {
    method: "POST",
    body: fd,
    isForm: true,
  });
}
export function getReferenceImageUrl(referenceId) {
  return `${BASE}/characters/reference/${referenceId}/image`;
}
export function createCharacter(formData) {
  return request("/characters", { method: "POST", body: formData, isForm: true });
}
// The Text-to-Image workflow's jobs: character runs and their 3D submissions.
// Storyboards are deliberately NOT here — they live in "Your Storyboards", and
// mixing them put boards in the character job list (and offered a Download that
// storyboard jobs can't serve).
export const CHARACTER_JOB_KINDS = ["generate", "meshy"];

// `kinds` is an array of job kinds, e.g. CHARACTER_JOB_KINDS. Omit for all.
export function listJobs(kinds) {
  const q = kinds?.length ? `?kind=${encodeURIComponent(kinds.join(","))}` : "";
  return request(`/jobs${q}`);
}
export function getJob(jobId) {
  return request(`/jobs/${jobId}`);
}
// Permanently delete a job: its record, reference upload and generated assets.
export function deleteJob(jobId) {
  return request(`/jobs/${jobId}`, { method: "DELETE" });
}
export function getAssets(jobId) {
  return request(`/jobs/${jobId}/assets`);
}
export function submitMeshy(jobId, parts, meshyApiKey) {
  const body = { parts };
  if (meshyApiKey) body.api_key = meshyApiKey;
  return request(`/jobs/${jobId}/meshy`, { method: "POST", body });
}
// Submit ONE (or more) parts for 3D via a chosen provider. api_key is optional
// when the user has a saved key for that provider on their profile.
export function submitModel3D(jobId, parts, provider, apiKey) {
  const body = { parts, provider };
  if (apiKey) body.api_key = apiKey;
  return request(`/jobs/${jobId}/meshy`, { method: "POST", body });
}
export function regeneratePart(jobId, part, prompt, provider) {
  const body = { part };
  if (prompt) body.prompt = prompt;
  if (provider) body.provider = provider;
  return request(`/jobs/${jobId}/regenerate-part`, { method: "POST", body });
}
// Regenerate ONE view (front/left/three_quarter/back) of a part.
export function regenerateView(jobId, part, view, prompt, provider) {
  const body = { part, view };
  if (prompt) body.prompt = prompt;
  if (provider) body.provider = provider;
  return request(`/jobs/${jobId}/regenerate-view`, { method: "POST", body });
}

// Download the zip via an authenticated request (the endpoint requires a bearer
// token, so we can't just point a link at it). Fetch follows the GCS redirect
// automatically for cloud runs, or streams the local file otherwise.
export async function downloadZip(jobId, filename, zipUrl) {
  // If we have a public GCS/HTTP URL, trigger direct browser download.
  if (zipUrl && (zipUrl.startsWith("http://") || zipUrl.startsWith("https://"))) {
    // The cloud zip lives at a fixed URL that every run overwrites, so the
    // browser may serve a stale cached copy. Add a cache-buster to force fresh.
    const bust = `t=${Date.now()}`;
    const fresh = zipUrl + (zipUrl.includes("?") ? "&" : "?") + bust;
    const a = document.createElement("a");
    a.href = fresh;
    a.download = filename || `${jobId}_assets.zip`;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
    return;
  }

  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}/jobs/${jobId}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || `${jobId}_assets.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Download one part's 4 views as a zip (per-section download). Always goes
// through the authenticated endpoint and streams the blob.
export async function downloadPart(jobId, part, filename) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}/jobs/${jobId}/download/${part}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error(`Can't reach the server at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-json */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || `${part}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export { BASE };
