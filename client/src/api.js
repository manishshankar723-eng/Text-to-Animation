// api.js — thin client for the Character Asset Generation API.
// Handles the JWT bearer token, JSON vs. multipart bodies, and error messages.

const BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
const TOKEN_KEY = "cas_token";
const EMAIL_KEY = "cas_email";
// ---------------------------------------------------------------- accounts
// MORE THAN ONE SIGNED-IN ACCOUNT, the way a browser profile switcher works.
// `cas_token` / `cas_email` are still THE SESSION — every request reads them and
// nothing below changes that. This is a SIDE LIST of the accounts whose tokens
// we are also holding, so switching is a copy from the list into those two keys
// rather than a password prompt.
//
// ⚠ THE ACTIVE ACCOUNT IS NOT A FLAG IN HERE. It is whatever `cas_email` says,
// and that is deliberate: two places that both claim to know who is signed in
// is two places that can disagree, and the one the API reads has to win.
//
// Shape: `[{ email, token, name }]`. `name` is a cached display name, purely so
// the switcher can show "Manish Kumar" over the address before `me()` answers
// for an account we are not currently signed in as.
const ACCOUNTS_KEY = "cas_accounts";

// ---------------------------------------------------------------- work counts
// HOW MUCH WORK EACH ACCOUNT HAS, `{kind: n}`, as the LOGIN told us.
//
// ⚠ IT IS A HINT ABOUT WHAT TO DRAW, NOT DATA AND NOT A PERMISSION. The one
// question it answers is "is this a brand-new account, or one with a library
// behind it" — and that question has to be answerable BEFORE any list request
// comes back, or the app has no choice but to show a spinner to everybody,
// including the person whose answer is going to be "nothing".
//
// Remembered per email rather than held in memory because the commonest way
// back into the app is a RELOAD with the token already in storage — no login
// call, and so no fresh hint. Last login's shape is a perfectly good guess for
// "does this person have work"; the prefetch that starts in the same breath
// corrects the actual numbers a moment later.
const COUNTS_KEY = "cas_counts";

function readCounts() {
  try {
    const raw = JSON.parse(localStorage.getItem(COUNTS_KEY) || "{}");
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  } catch {
    // Private mode, or somebody edited it by hand. No hint is not an error.
    return {};
  }
}

function writeCounts(map) {
  try {
    localStorage.setItem(COUNTS_KEY, JSON.stringify(map));
  } catch {
    // Storage full or disabled — the app just loses the head start.
  }
}

export function rememberWorkCounts(email, counts) {
  if (!email || !counts || typeof counts !== "object") return;
  const map = readCounts();
  map[email] = counts;
  writeCounts(map);
}

/**
 * The remembered `{kind: n}` for an account, or `null` when we have no hint.
 *
 * ⚠ `null` AND `{}` ARE DIFFERENT ANSWERS and callers must keep them apart.
 * `{}` is "the server counted, and there is nothing" — draw the empty
 * dashboard, skip the requests. `null` is "we never asked" — behave exactly as
 * the app did before this existed and wait for the lists. Collapsing the two
 * would show a returning customer an empty library for a heartbeat, which is
 * the one thing worse than showing them a spinner.
 */
export function getWorkCounts(email) {
  const map = readCounts();
  const hit = map[email || getEmail()];
  return hit && typeof hit === "object" ? hit : null;
}

function forgetWorkCounts(email) {
  if (!email) return;
  const map = readCounts();
  if (!(email in map)) return;
  delete map[email];
  writeCounts(map);
}

// ------------------------------------------------------- remembered rail
// THE LAST ANSWER `/auth/me/entitlements` GAVE, per account.
//
// ⚠ WHAT THIS FIXES. The sidebar has to be drawn on the FIRST paint, before any
// request can possibly have answered, and it used to be drawn from the built-in
// `WORKFLOWS` array in Sidebar.jsx. That array is every workflow that EXISTS —
// so an administrator who had HIDDEN two of them watched both reappear for
// about a second on every single reload, before the real answer arrived and
// took them away again. That is a bad enough bug on its own: a hidden feature
// which flashes up on every refresh is not hidden.
//
// So the answer is kept, and the rail is drawn from what this account was told
// LAST TIME rather than from what exists. In the steady state that is the same
// list the server is about to send, so nothing flashes at all.
//
// ⚠ IT IS STILL "WHAT IS DRAWN", NEVER "WHAT IS ALLOWED". Every workflow route
// is guarded server-side by the feature registry and every /admin route by
// `require_admin`; editing this key in a debugger gets you a page whose every
// request refuses. It is a paint hint — which is exactly why keeping it in
// localStorage is not a privilege escalation.
const ENTITLEMENTS_KEY = "cas_entitlements";

function readEntitlementsMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(ENTITLEMENTS_KEY) || "{}");
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  } catch {
    return {};
  }
}

function writeEntitlementsMap(map) {
  try {
    localStorage.setItem(ENTITLEMENTS_KEY, JSON.stringify(map));
  } catch {
    // Storage full or disabled — the rail goes back to waiting for the
    // request, which is correct, only slower.
  }
}

export function rememberEntitlements(email, payload) {
  const who = email || getEmail();
  // ⚠ AN ANSWER WITH NO WORKFLOWS IS NOT REMEMBERED. That is what a failed or
  // half-built response looks like, and writing it would teach the next reload
  // to draw an empty rail — the exact outage the fail-open rule exists to stop.
  if (!who || !payload?.workflows?.length) return;
  const map = readEntitlementsMap();
  map[who] = payload;
  writeEntitlementsMap(map);
}

/** The last answer this account got, or `null` if we have never had one. */
export function getRememberedEntitlements(email) {
  const hit = readEntitlementsMap()[email || getEmail()];
  return hit?.workflows?.length ? hit : null;
}

export function forgetEntitlements(email) {
  const who = email || getEmail();
  if (!who) return;
  const map = readEntitlementsMap();
  if (!(who in map)) return;
  delete map[who];
  writeEntitlementsMap(map);
}

// ⚠ AN ADMIN CHANGE MUST NOT WAIT FOR THE NEXT SESSION TO BE BELIEVED. Hiding a
// workflow in the panel is exactly the moment the remembered copy becomes
// wrong — so the panel says so, here, and `session_cache` re-reads at once.
// Without it, the administrator who just hid something would see it flash one
// more time on their next reload, which is precisely the complaint.
//
// A callback rather than a direct import, because `session_cache` imports THIS
// module and calling it the other way round would be a cycle.
let _entitlementsWatcher = null;

export function onEntitlementsChanged(fn) {
  _entitlementsWatcher = fn;
}

/**
 * Announce a change and PASS THE RESPONSE STRAIGHT THROUGH.
 *
 * ⚠ IT MUST RETURN ITS ARGUMENT. Written as a bare `.then(entitlementsChanged)`
 * it would resolve every one of these calls to `undefined`, and the admin
 * screens read what they get back.
 */
function entitlementsChanged(response) {
  try {
    _entitlementsWatcher?.();
  } catch {
    // A stale rail is not worth breaking the admin action that caused it.
  }
  return response;
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function getEmail() {
  return localStorage.getItem(EMAIL_KEY);
}

// Every read is defensive: private mode throws on access, and a half-written or
// hand-edited value must not take the app down on boot. A bad store is an EMPTY
// store — you are asked to sign in again, which is recoverable.
function readAccounts() {
  try {
    const raw = JSON.parse(localStorage.getItem(ACCOUNTS_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter((a) => a && typeof a.email === "string" && typeof a.token === "string");
  } catch {
    return [];
  }
}

/**
 * The accounts whose tokens we hold.
 *
 * ⚠ IT REPAIRS AS IT READS, and it has to. Everyone signed in before this
 * existed has a `cas_token` and NO entry for it, so an honest read would show
 * them a switcher that does not list the account they are looking at. The live
 * session is folded in — and written back, so the repair happens once.
 */
export function listAccounts() {
  const list = readAccounts();
  const email = getEmail();
  const token = getToken();
  if (email && token && !list.some((a) => a.email === email)) {
    list.push({ email, token, name: "" });
    writeAccounts(list);
  }
  return list;
}

function writeAccounts(list) {
  try {
    localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(list));
  } catch {
    // Storage full or disabled. The SESSION still works — only the ability to
    // switch back without a password is lost, so this must not throw.
  }
}

export function setSession(token, email) {
  localStorage.setItem(TOKEN_KEY, token);
  if (email) localStorage.setItem(EMAIL_KEY, email);
  if (!email) return;
  // Upsert, keeping any name we had cached for this address. The newest token
  // wins — signing in again is how an expired one gets replaced.
  const list = readAccounts();
  const at = list.findIndex((a) => a.email === email);
  const name = at >= 0 ? list[at].name : "";
  const entry = { email, token, name: name || "" };
  if (at >= 0) list[at] = entry;
  else list.push(entry);
  writeAccounts(list);
}

// Called once `me()` has answered, so the switcher can name an account it is
// not currently signed in as. Cosmetic — never required for a switch to work.
export function rememberAccountName(email, name) {
  if (!email) return;
  const list = readAccounts();
  const at = list.findIndex((a) => a.email === email);
  if (at < 0 || list[at].name === (name || "")) return;
  list[at] = { ...list[at], name: name || "" };
  writeAccounts(list);
}

/**
 * Make a remembered account the live session.
 * @returns {string|null} the email now signed in, or null if it isn't held.
 */
export function switchAccount(email) {
  const found = readAccounts().find((a) => a.email === email);
  if (!found) return null;
  localStorage.setItem(TOKEN_KEY, found.token);
  localStorage.setItem(EMAIL_KEY, found.email);
  return found.email;
}

/**
 * Drop one account's stored token.
 * ⚠ IF IT IS THE LIVE ONE, THE SESSION GOES WITH IT — forgetting the account
 * you are signed in as while leaving yourself signed in would be a lie.
 */
export function forgetAccount(email) {
  writeAccounts(readAccounts().filter((a) => a.email !== email));
  forgetWorkCounts(email);
  forgetEntitlements(email);
  if (getEmail() === email) {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EMAIL_KEY);
  }
}

// ⚠ LOGGING OUT FORGETS THE ACCOUNT YOU LOGGED OUT OF. Keeping its token so it
// could be switched back into without a password is the opposite of what "log
// out" means. The OTHER accounts keep theirs — they were never logged out of.
export function clearSession() {
  const email = getEmail();
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
  if (email) {
    writeAccounts(readAccounts().filter((a) => a.email !== email));
    // The hint goes with the token, for the same reason the token does: what
    // this account had is nobody else's business once they have signed out.
    forgetWorkCounts(email);
    forgetEntitlements(email);
  }
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

// ⚠ THE ONE CALL THAT IS LEGITIMATELY LONGER THAN TWO MINUTES, and it had been
// aborting at 120s: `POST /director/{id}/plan` is TWO model calls in one
// request — analyse, then polish — and each of them is allowed
// `DIRECTOR_BUDGET_SECONDS` (135s) on the server, retries and backoff included.
// So the honest worst case for a request that is working perfectly is about
// 270s, and the tab was giving up at 120 and reporting a stuck database.
// Reported with a screenshot of that exact message over the fallback plan.
//
// ⚠ IT MUST STAY BIGGER THAN 2 × THE SERVER'S BUDGET. Raise one of these two
// numbers and you raise the other, or the browser will abort a request the
// server is still correctly serving — and the paid call it is in the middle of
// is billed either way.
const PLAN_TIMEOUT_MS = 300000;

async function fetchWithRetry(url, options, attempts = 3, delayMs = 700, timeoutMs = REQUEST_TIMEOUT_MS) {
  for (let i = 1; ; i++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } catch (e) {
      // A TIMEOUT is not a blip — re-sending would just wait another two
      // minutes for the same silent server. Only a failed connection is worth
      // retrying (in dev that is usually uvicorn's --reload restarting).
      if (e?.name === "AbortError") {
        throw new Error(
          `The server didn't respond within ${Math.round(timeoutMs / 1000)}s. ` +
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

/**
 * `{a: 1, b: "", c: "x y"}` → `"?a=1&c=x%20y"`. Empty values are DROPPED.
 *
 * Written once because the alternative is what this file used to do: every list
 * function building its own `?a=…` by hand, each one correct on its own and
 * none of them able to add a second parameter without being rewritten. The
 * dropping matters — an omitted `limit` has to mean "the server's default",
 * never `?limit=`.
 */
function qs(params) {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === "" || v === null || v === undefined) continue;
    out.set(k, String(v));
  }
  const s = out.toString();
  return s ? `?${s}` : "";
}

async function request(path, { method = "GET", body, isForm = false, timeoutMs } = {}) {
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
    res = await fetchWithRetry(
      `${BASE}${path}`,
      { method, headers, body: payload },
      3,
      700,
      timeoutMs || REQUEST_TIMEOUT_MS
    );
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

// --- Entitlements: what THIS account may see and use ---
// ⚠ ONE CALL, MADE ON BOOT, AND THE SIDEBAR IS DRAWN FROM IT. The server sends
// a ready-shaped `workflows` list as well as the raw map, so the nav order does
// not end up living in the browser again — which is the thing the whole feature
// registry exists to stop. See server/features.py.
//
// ⚠ THE CALLER MUST FAIL OPEN. If this throws, the app has to fall back to the
// hard-coded WORKFLOWS array in Sidebar.jsx rather than rendering an empty rail:
// one bad request must not blank every user's navigation at once.
export function entitlements() {
  return request("/auth/me/entitlements"); // → { features, states, workflows, account_role }
}

// ⚠ PUBLIC, no token — the logged-out landing page calls it. Answers with the
// workflows a STRANGER should be shown: the kill switch and the rollout rules
// applied, tier-gated ones included and flagged `locked`. Same bargain as
// `tiers()` below: what you sell is public, so the page stops keeping its own
// copy of the list and going stale the day an admin flips a switch.
export function publicWorkflows() {
  return request("/public/workflows"); // \u2192 { workflows: [{id,label,icon,status,locked}] }
}

// --- Billing tiers ---
// ⚠ `tiers()` IS PUBLIC — no token required. A price list is public by nature,
// and the logged-out landing page can therefore show the real prices instead of
// keeping its own copy of them.
//
// ⚠ PRICES ARRIVE AS INTEGER MINOR UNITS: 2800 is $28.00. Never do arithmetic
// on them as dollars; divide only at the moment of display (see `money()` in
// admin/format.js and the modal's own formatter).
export function tiers() {
  return request("/billing/tiers"); // → { tiers: [...], currency, banner }
}

// Check a discount code. ⚠ IT REDEEMS NOTHING — checking a code is not using
// one, and the count only moves when a subscription is recorded against it.
// A rejected code always answers the same way whatever the reason, so this
// can't be used to enumerate which codes exist.
export function checkCoupon(code, tier, period) {
  return request("/billing/coupon", {
    method: "POST",
    body: { code, tier, period },
  });
}

// --- Admin panel ---
// ⚠ EVERY ONE OF THESE ANSWERS 404 TO A NON-ADMIN, not 403 — so a failure here
// reads as "there is no such page", which is what the server wants an ordinary
// account to believe. The client never decides who is an admin; it only reads
// `account_role` off the profile to know whether to DRAW the entry point.
//
// The email is a path segment and can hold a `+` or a `#`, so every call
// encodes it. Without that, "a+b@x.com" reaches the server as "a b@x.com" and
// looks like a user who does not exist.
function adminUserPath(email, suffix = "") {
  return `/admin/users/${encodeURIComponent(email)}${suffix}`;
}

export function adminOverview() {
  return request("/admin/overview");
}
export function adminMeta() {
  return request("/admin/meta");
}
export function adminListUsers({
  search = "",
  role = "",
  disabled = null,
  sort = "created_at",
  desc = true,
  limit = 50,
  skip = 0,
  withCounts = false,
} = {}) {
  const q = new URLSearchParams();
  if (search) q.set("search", search);
  if (role) q.set("role", role);
  // `disabled` is a TRISTATE — null means "don't filter", and sending
  // `disabled=false` would instead mean "only enabled accounts".
  if (disabled !== null) q.set("disabled", disabled ? "true" : "false");
  q.set("sort", sort);
  q.set("desc", desc ? "true" : "false");
  q.set("limit", String(limit));
  q.set("skip", String(skip));
  if (withCounts) q.set("with_counts", "true");
  return request(`/admin/users?${q}`); // → { users, total, limit, skip }
}
export function adminGetUser(email) {
  return request(adminUserPath(email));
}
export function adminSetDisabled(email, disabled) {
  return request(adminUserPath(email, "/disabled"), {
    method: "POST",
    body: { disabled },
  });
}
export function adminSetRole(email, accountRole) {
  return request(adminUserPath(email, "/role"), {
    method: "POST",
    body: { account_role: accountRole },
  });
}
export function adminSetNote(email, note) {
  return request(adminUserPath(email, "/note"), { method: "POST", body: { note } });
}
export function adminDeleteUser(email) {
  return request(adminUserPath(email), { method: "DELETE" });
}
// ⚠ EVERY MUTATION BELOW THAT CAN CHANGE WHICH WORKFLOWS A RAIL DRAWS ENDS IN
// `.then(entitlementsChanged)`. There are five levers: a feature's visibility or
// status, one account's override, the tier a feature needs, what a tier
// contains, and which tier an account is on. Anything added here that moves one
// of them needs the same line, or the panel will go on showing the
// administrator who changed it a stale sidebar. See `onEntitlementsChanged`.
export function adminListFeatures() {
  return request("/admin/features"); // → { features, statuses, rollout_modes, groups }
}
export function adminUpdateFeature(key, fields) {
  // PATCH, and only the fields that changed — a screen editing one control
  // sends one field, so two admins touching different settings on the same
  // feature don't overwrite each other's work.
  return request(`/admin/features/${encodeURIComponent(key)}`, {
    method: "PATCH",
    body: fields,
  }).then(entitlementsChanged);
}
export function adminSetOverride(email, key, value) {
  // ⚠ `value` IS TRISTATE: true forces on, false forces off, and null CLEARS
  // the override so the account goes back to whatever the rollout rule says.
  // "remove this exception" and "deny this customer" are different actions.
  return request(adminUserPath(email, "/override"), {
    method: "POST",
    body: { key, value },
  }).then(entitlementsChanged);
}
export function adminListTiers() {
  return request("/admin/tiers"); // → { tiers, currency, default_tier, tier_ids }
}
export function adminUpdateTier(id, fields) {
  return request(`/admin/tiers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: fields,
  }).then(entitlementsChanged);
}
export function adminSetMinTier(key, tier) {
  // "" clears the requirement — the feature goes back to being included in
  // every tier.
  return request(`/admin/features/${encodeURIComponent(key)}/min-tier`, {
    method: "POST",
    body: { tier: tier || "" },
  }).then(entitlementsChanged);
}
export function adminSetUserTier(email, tier) {
  return request(adminUserPath(email, "/tier"), {
    method: "POST",
    body: { tier },
  }).then(entitlementsChanged);
}
export function adminListOffers() {
  return request("/admin/offers");
}
export function adminCreateOffer(body) {
  return request("/admin/offers", { method: "POST", body });
}
export function adminUpdateOffer(id, fields) {
  return request(`/admin/offers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: fields,
  });
}
export function adminListSubscriptions({ status = null, email = "", limit = 50 } = {}) {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  if (email) q.set("email", email);
  q.set("limit", String(limit));
  return request(`/admin/subscriptions?${q}`);
}
// ⚠ NO AMOUNT IS SENT. The server works the price out from the plan and the
// code, so what lands in the ledger is what the pricing page would have quoted
// rather than a number the browser made up.
export function adminCreateSubscription(body) {
  return request("/admin/subscriptions", { method: "POST", body });
}
export function adminCancelSubscription(id) {
  return request(`/admin/subscriptions/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
}
export function adminEvents({ limit = 50, types = [], email = "", days = null } = {}) {
  const q = new URLSearchParams();
  q.set("limit", String(limit));
  // Repeated, not comma-joined: the route declares `type` as a list, so
  // `?type=a&type=b` is the shape FastAPI parses.
  types.forEach((t) => q.append("type", t));
  if (email) q.set("email", email);
  if (days) q.set("days", String(days));
  return request(`/admin/events?${q}`); // → { events: [...] }
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

// --- Script intake: what did the user actually paste? ---
// Runs when Create storyboard is pressed, BEFORE the breakdown, so an idea is
// never silently expanded into a whole invented film.
//
// Returns { kind, reason, question, decided_by, usage } where kind is one of
// "script" | "brief" | "idea" | "vague" | "empty". Often free: a recognisable
// script is spotted in plain Python on the server and never reaches a model
// (`decided_by: "sniff"`).
//
// ⚠ THE CALLER MUST FAIL OPEN. If this rejects, carry on into the breakdown as
// though the text were a script — which is what the form did before this route
// existed. A classifier that can block a storyboard is a worse bug than the one
// it was added to fix. See server/script_intake.py.
export function intakeScript(text) {
  return request("/script-intake", {
    method: "POST",
    body: { text: text || "" },
  });
}

// --- The approval gate: brief/idea → concept → approved concept → script ---
//
// ⚠ THESE TWO DO NOT FAIL OPEN, and that is the difference between them and
// `intakeScript`. The intake is a helper; this is a GATE. If a concept can't be
// developed the caller must show the error, NOT fall through to breaking the
// raw brief down as a script — that silent invention is the whole reason the
// gate exists. See server/script_concept.py.

// Brief or idea in, ONE concept out. Nothing is drawn.
// Returns { concept: {title, premise, story_direction, key_scenes[],
// duration_seconds, visual_direction}, usage }.
export function developConcept(text, { kind, genre, style, aspectRatio } = {}) {
  return request("/script-concept", {
    method: "POST",
    body: {
      text: text || "",
      kind: kind === "brief" ? "brief" : "idea",
      genre: genre || "",
      style: style || "",
      aspect_ratio: aspectRatio || "",
    },
  });
}

// The concept the user APPROVED (edits included) → a real script in the exact
// layout the breakdown reads. `source` is what they originally pasted, carried
// along for details a concept has no field for (a product name, a required
// line). Returns { script, title, seconds, usage }.
export function conceptToScript(concept, { source, language } = {}) {
  return request("/script-concept/script", {
    method: "POST",
    body: {
      concept: {
        title: concept?.title || "",
        premise: concept?.premise || "",
        story_direction: concept?.story_direction || "",
        key_scenes: (concept?.key_scenes || []).filter((s) => (s || "").trim()),
        duration_seconds: concept?.duration_seconds || 60,
        visual_direction: concept?.visual_direction || "",
      },
      source: source || "",
      language: language || "",
    },
  });
}

// --- Script assistant (the "Ask AI" tab in the Script → Storyboard form) ---
// A normal chat that can also hand back a finished script.
//
// ⚠ STATELESS ON THE SERVER: the whole transcript goes up every turn and
// nothing is stored there. The browser owns the conversation (it lives in
// localStorage, so a refresh keeps it), which is why `messages` is the request
// rather than a chat id. See server/script_chat.py.
//
// The form's current state rides along so the assistant answers about THIS
// board — it won't ask which genre you want one second after you clicked it.
// Returns { reply, script, title, usage }; `script` is "" on every turn that
// wasn't a request for a script, which is most of them.
export function scriptChat({
  messages,
  genre,
  style,
  aspectRatio,
  title,
  currentScript,
} = {}) {
  return request("/script-chat", {
    method: "POST",
    body: {
      messages: (messages || []).map((m) => ({ role: m.role, text: m.text })),
      genre: genre || "",
      style: style || "",
      aspect_ratio: aspectRatio || "",
      title: title || "",
      current_script: currentScript || "",
    },
  });
}

// --- Plan & Script ---
// A planning session is a conversation with the strategist agent plus the
// calendar it produced. Text quota only — nothing here generates an image.
export function listPlans(limit) {
  return request(`/plans${qs({ limit: limit || "" })}`);
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
// ⚠ `language` MUST be forwarded. It was missing here for a while, and because
// every other layer was already wired for it — the picker, the request model,
// plan_agent's LANGUAGES — the bug was invisible from every side except the
// output: you picked Hinglish, the chip on the board said Hinglish, and the
// plan came back in English because the field never left the browser.
export function generatePlan(planId, { months, cadence, language } = {}) {
  return request(`/plans/${planId}/generate`, {
    method: "POST",
    body: {
      months: months || 1,
      cadence: cadence || null,
      language: language || null,
    },
  });
}

// --- Plan & Script: the scripts ---
// Writing one is the only call here that spends quota. `itemIndex` points at a
// row of the generated calendar (the server reads the row itself — see
// server/plans.py on why the browser doesn't send it); `brief` is for a script
// that was never on the calendar. Returns the whole session, so the new script
// and the updated token total arrive together.
export function writePlanScript(
  planId,
  { itemIndex = null, brief = "", seconds = 60, notes = "", language = "" } = {}
) {
  return request(`/plans/${planId}/script`, {
    method: "POST",
    body: {
      item_index: itemIndex,
      brief,
      seconds,
      notes,
      language: language || null,
    },
  });
}
export function deletePlanScript(planId, scriptId) {
  return request(`/plans/${planId}/scripts/${scriptId}`, { method: "DELETE" });
}
// Loads the script into the caller's ONE script draft, which is what Script to
// Storyboard opens on. Overwrites whatever was there — the caller warns first.
export function planScriptToDraft(planId, scriptId) {
  return request(`/plans/${planId}/scripts/${scriptId}/to-draft`, { method: "POST" });
}
export function youtubeConfigured() {
  return request("/plans/config/youtube"); // → { configured: bool }
}
// Exports are binary — fetched as an authed blob and handed to the browser,
// the same way the storyboard PDF/ZIP downloads work. The server names the
// file; `serverFilename` reads that back off the Content-Disposition header.
//
// One helper rather than one copy per endpoint: the error handling here is the
// fiddly part (a failed download answers with JSON, not a blob), and a second
// copy of it is a second place for a 409 to surface as "undefined".
async function downloadAuthed(path, fallbackName) {
  const token = getToken();
  let res;
  try {
    res = await fetchWithRetry(`${BASE}${path}`, {
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
  a.download = serverFilename(res, fallbackName);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function downloadPlan(planId, format) {
  return downloadAuthed(`/plans/${planId}/export?format=${format}`, `plan.${format}`);
}

// One script, as .txt (the exact bytes the storyboard breakdown reads) or
// .docx (laid out as a screenplay, for people).
export function downloadPlanScript(planId, scriptId, format) {
  return downloadAuthed(
    `/plans/${planId}/scripts/${scriptId}/export?format=${format}`,
    `script.${format}`
  );
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
// `brand` is sent for its NAME only — the breakdown is text, and the name is
// what stops a writer's "[Your App Name]" being copied into a shot description
// and then burnt into the finished video's captions, which is what happened.
export function breakdownScript(
  script,
  { style, aspectRatio, genre, brand, provider, title } = {}
) {
  return request("/storyboards/breakdown", {
    method: "POST",
    body: {
      script,
      style,
      aspect_ratio: aspectRatio,
      genre,
      brand: brand || null,
      provider,
      title,
    },
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
  market,
  brand,
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
      // ⚠ WHO THE FILM IS FOR, SENT APART FROM `world` EVEN THOUGH THE SERVER
      // merges it in. What the user PICKED and what the breakdown GUESSED sit
      // at opposite ends of the precedence chain; folded into one dict here,
      // the server could not tell a decision from a lucky guess. See market.py.
      market: market || null,
      // The brand this film sells. Stored on the board so a redraw months later
      // stamps the SAME logo file the rest of the panels are carrying.
      brand: brand || null,
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

// ⚠ THIS SPENDS MONEY — one vision call per contact sheet of 24 panels. It is
// only ever reached from the board's "Check this board" button; the FREE audit
// (`job.result.audit`) is already there without asking.
// An empty `findings` is the GOOD answer, not a failure.
export function checkStoryboard(jobId) {
  return request(`/storyboards/${jobId}/check`, { method: "POST" });
}

// --- Storyboard library ("Your Storyboards") ---
// A saved project IS a storyboard job, so these all read/write the same records
// the board itself uses — nothing can drift out of sync.

// Every storyboard this user has generated, newest first.
// Boards belong to a workflow. Script to Storyboard's own boards carry no tag
// (pass nothing); Image to Animatic Image asks for its copies by name, so
// neither library shows the other's.
export function listStoryboards(workflow = "", limit) {
  return request(`/storyboards${qs({ workflow, limit: limit || "" })}`);
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
// `maxEdge` asks for a PROXY, exactly as `fetchAnimaticMedia` does: the same
// picture, losslessly resized so its long edge is at most that many pixels.
// ⚠ THE 72px LIST THUMBNAILS ARE WHY IT IS HERE. A drawn panel is ~3.5 MB, and
// a library page of ten boards was pulling ~35 MB down the wire to fill ten
// postage stamps. Omit it — which is what the board page and the lightbox do,
// because they want the real picture — and nothing changes.
export async function fetchStoryboardPanel(jobId, index, path, maxEdge = 0) {
  const token = getToken();
  let rel = path || `/storyboards/${jobId}/panel/${index}`;
  if (maxEdge > 0) rel += `${rel.includes("?") ? "&" : "?"}w=${Math.round(maxEdge)}`;
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

// --- "Ask AI" beside a finished board ---
//
// ⚠ THIS CALL CHANGES NOTHING. It comes back with { reply, actions[], usage }
// where each action is an INTENDED edit — the caller shows the list, the user
// presses Apply, and each one then runs through insertStoryboardPanel /
// deleteStoryboardPanel / regenerateStoryboardPanel below. Redrawing a panel is
// an image, and one typed sentence must never spend forty of them unseen.
//
// ⚠ THE PANELS ARE NOT SENT — the server reads them off the job, so the plan is
// always against what is really stored rather than what this tab last drew.
// `selection` is {kind:"panel"|"scene"|"none", shot, scene} with 1-BASED shot
// numbers, exactly as they are printed under the panels.
export function askAboutBoard(jobId, { messages, selection } = {}) {
  return request(`/storyboards/${jobId}/ask`, {
    method: "POST",
    body: {
      messages: (messages || []).map((m) => ({ role: m.role, text: m.text })),
      selection: {
        kind: selection?.kind || "none",
        shot: selection?.shot || 0,
        scene: selection?.scene || 0,
      },
    },
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

export function listAnimatics(limit) {
  return request(`/animatics${qs({ limit: limit || "" })}`);
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

// --- The sound library (Freesound) -----------------------------------------
// ⚠ THE BROWSER NEVER TALKS TO FREESOUND'S API, only to ours. The key is a
// server secret (§B.4 of their terms) and the licence fence that keeps
// NonCommercial sounds out of the results is a server rule — a search the client
// could compose itself is a fence the client could open. The one Freesound URL
// the browser DOES touch is the mp3 preview, which is public and unauthenticated
// and is played straight off their CDN; see `soundPreviewUrl` below.

// Is the Sounds tab worth drawing? Answers `{ configured, licences, sorts,
// notice }` — never the key itself. Called once when the editor opens.
export function soundStatus() {
  return request("/sounds/status");
}

/**
 * Search the sound library.
 *
 * @param q         what to look for; "" browses by `sort` alone
 * @param licence   "safe" (CC0 only, the default) | "credit" (CC BY) | "both"
 * @param sort      one of the keys `soundStatus().sorts` lists
 * @param page      1-based
 *
 * ⚠ DEBOUNCE THE CALLER, DON'T RETRY THIS. A free Freesound key allows 60
 * requests a minute for the WHOLE deployment, so a search-per-keystroke burns a
 * budget every user shares. `SoundLibrary.jsx` waits for a pause in typing.
 */
export function searchSounds({ q = "", licence = "safe", sort = "relevance", page = 1, minSeconds = 0, maxSeconds = 0 } = {}) {
  const params = new URLSearchParams({
    q,
    licence,
    sort,
    page: String(page),
  });
  if (minSeconds > 0) params.set("min_seconds", String(minSeconds));
  if (maxSeconds > 0) params.set("max_seconds", String(maxSeconds));
  return request(`/sounds/search?${params.toString()}`);
}

/**
 * Bring one sound into a project as an ordinary audio upload.
 *
 * ⚠ SENDS THE ID, NOT THE PREVIEW URL THE CARD IS HOLDING. The server re-asks
 * Freesound where the file lives, so a crafted call cannot point our backend at
 * an address of its choosing — and so the licence is re-checked on the way in
 * rather than only at search time.
 *
 * Answers the same shape `uploadAnimaticAudio` does — `{ upload_id, filename,
 * url }` — plus `duration_ms` and the licence fields, so the caller can reuse
 * `addAudioTrack` unchanged.
 */
export function importSound(animaticId, soundId) {
  return request(`/animatics/${animaticId}/sounds`, {
    method: "POST",
    body: { sound_id: String(soundId) },
  });
}

/**
 * THE 🎬 DIRECTOR'S SOUND PASSES — a whole film's worth of sound, in one call.
 *
 * ⚠ SEARCH TERMS GO IN, FILED UPLOADS COME OUT. Nothing here is a sound id: the
 * cues were written by the analyse call that read the film (`sfx` and `music` on
 * the plan response) and the server searches for each one, so the browser never
 * has to see a result it is not going to show anybody.
 *
 * ⚠ ONE CALL FOR THE WHOLE LIST, AND THAT IS NOT A ROUND-TRIP OPTIMISATION. The
 * audio-file cap has to be measured ONCE against the project, or the tail of an
 * eleven-cue list is refused for room the first ten took — see the route's own
 * docstring. It also dedupes: six shots cueing the same sound are one download.
 *
 * ⚠ IT ANSWERS PARTIALLY ON PURPOSE. `{ items, skipped, room_left }` — a cue that
 * found nothing is a row in `skipped` with a reason, not a rejected request, so a
 * film gets the ten sounds that were found rather than none of them.
 *
 * @param payload `{ sounds: [{key, query, kind, max_seconds, min_seconds}] }`
 *                — `soundtrackRequest()` in `sound_pass.js` builds exactly this.
 */
export function buildSoundtrack(animaticId, payload) {
  return request(`/animatics/${animaticId}/soundtrack`, {
    method: "POST",
    body: { sounds: (payload && payload.sounds) || [] },
  });
}

// --- Animating a frame with Veo, from inside the editor ---------------------
// ⚠ The one path in the animatic editor that SPENDS MONEY. The pair mirrors
// `estimateFinalVideo` / `renderFinalVideoShots` exactly, including taking the
// SAME body: the number the confirm dialog shows can then only be the price of
// the thing the button goes on to do.

// Free to call. Drives the confirm dialog, so the price is on screen before the
// button that spends it.
export function estimateAnimateFrames(id, { frameIds, prompts, durations, render, force } = {}) {
  return request(`/animatics/${id}/animate/estimate`, {
    method: "POST",
    body: {
      frame_ids: frameIds || [],
      prompts: prompts || {},
      // frame_id → 4 | 6 | 8. ⚠ ONLY THE 🎬 DIRECTOR SENDS THESE: it picks each
      // take's length from that shot's own hold, so one submission is a mixture.
      // Absent means "the settings' own length", which is ✨ Animate.
      durations: durations || {},
      render,
      force: !!force,
    },
  });
}

// SPENDS MONEY. Renders the named frames with Veo, async — poll getJob(id).
// Each finished clip lands as an ordinary video upload, so from that moment it
// is the same thing on the timeline as a file dragged in from the desktop.
export function animateAnimaticFrames(id, { frameIds, prompts, durations, render, force } = {}) {
  return request(`/animatics/${id}/animate`, {
    method: "POST",
    body: {
      frame_ids: frameIds || [],
      prompts: prompts || {},
      // ⚠ SAME BODY AS THE ESTIMATE ABOVE, `durations` included. The moment the
      // two shapes differ, the number in the confirm dialog stops being the
      // price of the thing the button does.
      durations: durations || {},
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

// Free. The same wording for EVERY clip on the timeline, in one call —
// `[{ frame_id, description }]`, and `description` is "" for a clip the board
// says nothing about.
//
// ⚠ IT IS WHAT LETS THE DIRECTOR'S FREE PLANNER RENDER. "Just the rhythm" writes
// no words of its own, so its Veo prompts are the board's own descriptions; one
// call rather than one per shot, because forty-eight `getFramePanel`s is
// forty-eight reads of the same storyboard record.
export function getAnimaticPanels(id) {
  return request(`/animatics/${id}/panels`);
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

// --- One image from one sentence (the Media pane's ✨) -----------------------
// ⚠ NOT THE SHOT GENERATOR BELOW. That draws a SHOT — the board's style, its
// references, its neighbours, its row. This draws whatever the sentence says and
// belongs to nothing: a title card, a texture, an inset. It comes back as the
// same upload item a FILE upload returns, so from that point the client places
// it exactly as it places a dropped picture — into the library, and onto the
// overlay Images lane.

// Free, and it needs no project: which image model a ✨ here would call. Shown
// in the dialog before anything is spent.
export function getImageModel() {
  return request("/animatics/image-model");
}

// SPENDS QUOTA. Draws it. Synchronous — one image, so there is no job to poll.
export function generateAnimaticImage(id, { prompt, aspectRatio = "" } = {}) {
  return request(`/animatics/${id}/images/generate`, {
    method: "POST",
    body: { prompt, aspect_ratio: aspectRatio },
  });
}

// --- One video from one sentence (the Media pane's ✨, Video tab) ------------
// ⚠ SPENDS MONEY, and follows the same two-step every paid path here does: the
// estimate is free and takes the SAME body as the render, so the number in the
// confirm dialog can only be the price of what the button then does.
//
// ⚠ NOT ✨ Animate. That animates a clip already on the timeline and lands over
// it; this renders from a sentence — with or without a starting still — and
// lands as an ordinary video on the Video row, belonging to nothing.

// Free. What generating this video would cost.
export function estimateGenerateVideo(id, { prompt, sourceUploadId = "", render } = {}) {
  return request(`/animatics/${id}/videos/generate/estimate`, {
    method: "POST",
    body: { prompt, source_upload_id: sourceUploadId, render },
  });
}

// SPENDS MONEY. 202 — the render happens off-request, so poll getJob(id) and
// read the finished clip out of the job's `veo_clips` (it has no `frame_id`).
export function generateAnimaticVideo(id, { prompt, sourceUploadId = "", render } = {}) {
  return request(`/animatics/${id}/videos/generate`, {
    method: "POST",
    body: { prompt, source_upload_id: sourceUploadId, render },
  });
}

// --- A shot that is NOT on the board ----------------------------------------
// "Generate a shot before / after this one", from a storyboard clip's
// right-click menu on the timeline. ⚠ THE BOARD IS NOT EDITED: the picture comes
// back as an ordinary animatic upload and the clip carries `src.shot_id` instead
// of a panel index, because inserting a panel renumbers every panel after it and
// an animatic frame references a panel BY INDEX. The server's own note at
// `generate_neighbour_shot` carries the reasoning.

// Free. What the dialog opens on — the name to give the shot, the shots either
// side of the gap, the board's aspect, and which model is about to draw it.
// `side` is "before" | "after".
export function getNeighbourShot(id, frameId, side = "after") {
  return request(
    `/animatics/${id}/frames/${frameId}/neighbour?side=${encodeURIComponent(side)}`
  );
}

// SPENDS QUOTA — a TEXT call, a fraction of the price of a drawing. Writes the
// missing beat between the two shots and hands it back for the user to edit.
// `notes` is whatever is already in the box: steering, not a replacement.
export function suggestNeighbourShot(id, frameId, { side = "after", notes = "" } = {}) {
  return request(`/animatics/${id}/frames/${frameId}/neighbour/suggest`, {
    method: "POST",
    body: { side, notes },
  });
}

// SPENDS QUOTA. Draws the shot. Synchronous — one image, so there is no job to
// poll.
//
// ⚠ RETURNS A CLIP, NOT A SAVED PROJECT. Where it goes in the cut is the
// client's decision — the same contract the image, video and board imports
// follow — which is what lets the editor insert it beside the clip you
// right-clicked and ripple every layer in ONE undoable edit.
export function generateNeighbourShot(
  id,
  frameId,
  { side = "after", description, aspectRatio = "", durationMs = 8000 } = {}
) {
  return request(`/animatics/${id}/frames/${frameId}/neighbour`, {
    method: "POST",
    body: {
      side,
      description,
      aspect_ratio: aspectRatio,
      duration_ms: durationMs,
    },
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

// --- 🎬 The Director --------------------------------------------------------
// The auto-editor's BRAIN. Two text calls on the server — read the film, write
// the edit — and what comes back is a plan, not an edit: the browser runs it
// through `validatePlan` → `applyGuardrails` → `useDirectorRun`, the same two
// doors the deterministic Phase 0 planner's plan comes through. See
// `client/src/animatic/agent/` and `director.py`.

// Free, no model call. Which backend the Director is wired to and the languages
// that have a description written for them. The 🎬 popup opens on this.
export function directorConfig() {
  return request(`/director/config`);
}

// SPENDS TEXT QUOTA — two calls, a fraction of the price of one drawing, and
// nothing on the timeline moves until the user presses Run in the preview.
//
// ⚠ THE BOARD AND THE VOCABULARY ARE SENT FROM HERE, and both on purpose. The
// document on screen is ahead of the last autosave, so a plan written from the
// stored project would be a plan for a film one edit stale; and the capability
// manifest is DERIVED in `agent/capabilities.js` from the tables the renderers
// read, which is the only honest answer to "what can this build do". A second
// copy of either on the server would be a copy that goes stale.
//
// ⚠ IT ALSO SAVES `language` ONTO THE PROJECT. The language is a property of the
// film — the voiceover and the captions read it too — so the popup that asks is
// the popup that persists it.
// ⚠ AND IT WAITS LONGER THAN ANYTHING ELSE IN THIS FILE. Two model calls in one
// request; see `PLAN_TIMEOUT_MS`.
export function directorPlan(id, { board, capabilities, include, language = "", brief = "" } = {}) {
  return request(`/director/${id}/plan`, {
    method: "POST",
    body: { board, capabilities, include, language, brief },
    timeoutMs: PLAN_TIMEOUT_MS,
  });
}

// --- The Director's Veo pass (Phase 4) --------------------------------------
// ⚠ NOT ONE OF THESE THREE SPENDS ANYTHING, and that is worth stating because
// all three have "veo" in the name. They quote a pass, open a resumable record
// and close it. THE MONEY MOVES THROUGH `animateAnimaticFrames` above, one pass
// of `max_video_batch` at a time — the same door ✨ Animate has used since
// 2026-08-07, so every spend guard written for that button governs the
// Director's pass too, without one of them being restated.

// FREE. What rendering these shots would cost, broken into the passes it will
// actually be submitted in. ⚠ The total comes back as the SUM of the passes to
// the penny — see `_quote_veo_run` on why it is not calculated twice.
export function directorVeoQuote(id, { shots, render } = {}) {
  return request(`/director/${id}/veo/quote`, {
    method: "POST",
    body: { shots: shots || [], render },
  });
}

// FREE. Write down what this pass MEANS to render, before a penny of it moves.
// ⚠ THIS IS WHAT MAKES A RUN RESUMABLE. A record written after the first
// submission would be missing exactly the runs that need it — the ones that die
// on pass one.
export function directorVeoStart(id, { shots, render } = {}) {
  return request(`/director/${id}/veo/start`, {
    method: "POST",
    body: { shots: shots || [], render },
  });
}

// FREE. Close the run: "done", "stopped" or "failed". The shot list is never
// rewritten — how far the run got is a question for `veo_clips`.
export function directorVeoState(id, { runId, status, error = "" } = {}) {
  return request(`/director/${id}/veo/state`, {
    method: "POST",
    body: { run_id: runId || "", status: status || "done", error },
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

// SAVE ONE SOURCE FILE OUT OF A PROJECT, by the relative path this app already
// serves it on.
//
// It began as the Veo download — a render is the one asset in an animatic that
// cannot be got back, so it has to be savable BEFORE the project is deleted.
// ✨ Animatic images made the same true of a key pose, and the ⬇ is now drawn on
// everything with bytes behind it (`isSavable` in `animatic/scene.js`).
//
// ⚠ IT TAKES A PATH BECAUSE NOT EVERY PICTURE IS AN UPLOAD. A Veo render is a
// file under this animatic's id (`/media/<upload_id>`); a storyboard panel and a
// key pose are content-addressed on the BOARD (`/panel/<board>/<index>?frame=n`)
// and have no upload id at all — see `assetUrl` in `animatic/assets.js`, which
// builds exactly these paths for the Media pane. One saver for all of them,
// because "which url shows this?" is a question the library already answers.
//
// ⚠ IT GOES THROUGH `fetch`, NOT A PLAIN LINK, for the reason every download in
// this file does: every media route requires a bearer token, and an `<a href>`
// sends no headers — it would download a 401 page named like a picture. The blob
// is fetched with the token, handed to a temporary `<a download>`, and revoked.
//
// ⚠ THE NAME IS THE CALLER'S. These routes serve stills, footage and audio and
// say nothing about which — there is no Content-Disposition to read, so
// `serverFilename` has nothing to work with. The editor knows the clip's label,
// which is what the user will look for on their desk.
export async function downloadAnimaticFile(path, filename) {
  if (!path) throw new Error("There's no file behind this clip.");
  const token = getToken();
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
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
  a.download = filename || "clip";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// The same thing for a file stored under this animatic's own id. Kept as its own
// name because that is what every existing caller asks for.
export function downloadAnimaticMedia(id, uploadId, filename) {
  if (!id || !uploadId) throw new Error("There's no file behind this clip.");
  return downloadAnimaticFile(
    `/animatics/${id}/media/${uploadId}`,
    filename || "clip.mp4"
  );
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

export function listFinalVideos(limit) {
  return request(`/final-videos${qs({ limit: limit || "" })}`);
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
// ⚠ `style` IS THE BOARD'S STYLE, AND IT IS NOT DECORATION. The sheet this
// returns is fed into every panel the character appears in as a look reference,
// so a sheet drawn in the wrong medium pulls those panels into that medium: a
// Cinematic board whose cast came back as Pixar cartoons is the reported bug
// this argument fixes. Omitting it draws in a neutral medium, not a cartoon.
export function generateReference(prompt, world, style, market, provider) {
  const body = { prompt };
  if (world) body.world = world;
  if (style) body.style = style;
  if (market) body.market = market;
  if (provider) body.provider = provider;
  return request("/characters/reference", { method: "POST", body });
}
// Generate a prop / background reference image (Stage B2 asset consistency).
// `category` is "prop" or "background". Returns { reference_id, image_url }.
// ⚠ `market` MATTERS MOST ON THIS ONE. A prop is a phone, a menu, a price tag,
// a shop front — the surfaces money and signage actually live on — and the
// reference is drawn ONCE and then fed into every panel the object appears in.
// A `$` baked in here is a `$` on the whole board, in a picture no later prompt
// can argue with.
export function generateAssetReference(prompt, category = "prop", world, market, provider) {
  const body = { prompt, category };
  if (world) body.world = world;
  if (market) body.market = market;
  if (provider) body.provider = provider;
  return request("/assets/reference", { method: "POST", body });
}
// Upload your own image as a character reference. Returns { reference_id, ... }.
// ⚠ A LOGO GOES THROUGH ITS OWN ROUTE, NOT `uploadReference`. That one
// normalises uploads with `.convert("RGB")` — right for a character photo,
// destroying for a logo, because flattening the alpha fills the transparent
// background with black and every panel would carry the mark inside a hard
// rectangle. `POST /brand/logo` keeps the transparency.
export function uploadBrandLogo(file) {
  const fd = new FormData();
  fd.append("image", file);
  return request("/brand/logo", { method: "POST", body: fd, isForm: true });
}
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
//
// ⚠ `limit` IS NOT DECORATION — see `qs` below. A screen that shows two rows
// must ASK for a handful, not for a hundred and slice.
export function listJobs(kinds, limit) {
  const q = qs({
    kind: kinds?.length ? kinds.join(",") : "",
    limit: limit || "",
  });
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
