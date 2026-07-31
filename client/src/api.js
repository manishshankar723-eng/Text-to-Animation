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
async function fetchWithRetry(url, options, attempts = 3, delayMs = 700) {
  for (let i = 1; ; i++) {
    try {
      return await fetch(url, options);
    } catch (e) {
      if (i >= attempts) throw e;
      await new Promise((r) => setTimeout(r, delayMs));
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
  } catch {
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

// --- Script → Storyboard ---
// Stage A: break a script into an ordered shot list. Returns { shots, count,
// style, aspect_ratio }. `provider` is optional ("vertex" | "gemini").
export function breakdownScript(script, { style, aspectRatio, genre, provider } = {}) {
  return request("/storyboards/breakdown", {
    method: "POST",
    body: { script, style, aspect_ratio: aspectRatio, genre, provider },
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
  characterRefs,
  assetRefs,
  assetCategories,
  world,
  script,
  provider,
} = {}) {
  return request("/storyboards", {
    method: "POST",
    body: {
      shots,
      style,
      aspect_ratio: aspectRatio,
      title,
      genre,
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
export function listStoryboards() {
  return request("/storyboards");
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
export function saveAnimatic(id, { title, settings, frames, texts, audioTracks } = {}) {
  const body = {};
  if (title !== undefined) body.title = title;
  if (settings !== undefined) body.settings = settings;
  if (frames !== undefined) body.frames = frames;
  if (texts !== undefined) body.texts = texts;
  // The whole list, every time. An empty array removes every track, so there's
  // no companion "clear" flag to keep in step.
  if (audioTracks !== undefined) body.audio_tracks = audioTracks;
  return request(`/animatics/${id}`, { method: "PUT", body });
}

export function deleteAnimatic(id) {
  return request(`/animatics/${id}`, { method: "DELETE" });
}

// Upload images into an animatic. They're stored but NOT sequenced — the client
// decides the order and saves the project afterwards.
export function uploadAnimaticImages(id, files) {
  const fd = new FormData();
  for (const file of files) fd.append("files", file);
  return request(`/animatics/${id}/images`, { method: "POST", body: fd, isForm: true });
}

export function uploadAnimaticAudio(id, file) {
  const fd = new FormData();
  fd.append("file", file);
  return request(`/animatics/${id}/audio`, { method: "POST", body: fd, isForm: true });
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
export async function fetchAnimaticMedia(path) {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Media not available");
  return URL.createObjectURL(await res.blob());
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
  a.download = filename || "animatic.mp4";
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
