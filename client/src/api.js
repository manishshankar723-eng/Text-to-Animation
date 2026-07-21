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

  const res = await fetch(`${BASE}${path}`, { method, headers, body: payload });

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
export function regeneratePart(jobId, part, prompt, provider) {
  const body = { part };
  if (prompt) body.prompt = prompt;
  if (provider) body.provider = provider;
  return request(`/jobs/${jobId}/regenerate-part`, { method: "POST", body });
}

// Download the zip via an authenticated request (the endpoint requires a bearer
// token, so we can't just point a link at it). Fetch follows the GCS redirect
// automatically for cloud runs, or streams the local file otherwise.
export async function downloadZip(jobId, filename, zipUrl) {
  // If we have a public GCS/HTTP URL, trigger direct browser download
  if (zipUrl && (zipUrl.startsWith("http://") || zipUrl.startsWith("https://"))) {
    const a = document.createElement("a");
    a.href = zipUrl;
    a.download = filename || `${jobId}_assets.zip`;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
    return;
  }

  const token = getToken();
  const res = await fetch(`${BASE}/jobs/${jobId}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
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

export { BASE };
