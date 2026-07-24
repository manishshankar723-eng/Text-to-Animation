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
    res = await fetch(`${BASE}${path}`, { method, headers, body: payload });
  } catch {
    // fetch() rejects with a TypeError ("Failed to fetch") when the browser
    // can't reach the server at all — surface an actionable message instead.
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
  characterRefs,
  assetRefs,
  provider,
} = {}) {
  return request("/storyboards", {
    method: "POST",
    body: {
      shots,
      style,
      aspect_ratio: aspectRatio,
      title,
      character_refs: characterRefs || {},
      asset_refs: assetRefs || {},
      provider,
    },
  });
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

// Switch which style variant is shown/exported (no regeneration).
export function setActiveVariant(jobId, index) {
  return request(`/storyboards/${jobId}/active-variant`, {
    method: "POST",
    body: { index },
  });
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

// Stage F: download the board as a PDF (authed blob → browser download).
export async function downloadStoryboardPdf(jobId, filename) {
  const token = getToken();
  let res;
  try {
    res = await fetch(`${BASE}/storyboards/${jobId}/pdf`, {
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
  a.download = filename || "storyboard.pdf";
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
    res = await fetch(`${BASE}/storyboards/${jobId}/bundle`, {
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
  a.download = filename || "storyboard_assets.zip";
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
export function generateReference(prompt, provider) {
  const body = { prompt };
  if (provider) body.provider = provider;
  return request("/characters/reference", { method: "POST", body });
}
// Generate a prop / background reference image (Stage B2 asset consistency).
// `category` is "prop" or "background". Returns { reference_id, image_url }.
export function generateAssetReference(prompt, category = "prop", provider) {
  const body = { prompt, category };
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
export function listJobs() {
  return request("/jobs");
}
export function getJob(jobId) {
  return request(`/jobs/${jobId}`);
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
    res = await fetch(`${BASE}/jobs/${jobId}/download`, {
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
    res = await fetch(`${BASE}/jobs/${jobId}/download/${part}`, {
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
