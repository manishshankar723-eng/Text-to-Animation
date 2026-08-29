// format.js — the admin panel's shared formatting. Dates, labels, numbers.
//
// It is one file rather than a helper repeated in each screen because the
// activity feed on the dashboard and the activity TAB have to render an event
// the same way — the moment they don't, one of them is the wrong one and there
// is no telling which.

// Every timestamp the API sends is an ISO-8601 UTC string
// (`datetime.now(timezone.utc).isoformat()`), so it parses natively.
// A missing one is a fact, not an error: "never signed in" is a real state for
// an account created before sign-ins were recorded.
export function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// "3 days ago". Used beside the absolute date, never instead of it — "2 months
// ago" is the shape of the answer, the date is the answer.
export function timeAgo(iso) {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const secs = Math.floor((Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 31) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

// Thousands separators, and a dash for "the server could not say". ⚠ `0` and
// `null` are different answers and must not render the same: zero projects is a
// measurement, no answer is a store that cannot count.
export function num(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString();
}

// Minor units → what a person reads. ⚠ PRICES ARE INTEGERS IN MINOR UNITS
// EVERYWHERE — 2800 is $28.00 — so this division is the ONLY place a price
// becomes a fraction, and it happens at the moment of display.
const SYMBOLS = { USD: "$", INR: "₹", EUR: "€", GBP: "£" };

export function money(minor, currency = "USD") {
  const major = (minor || 0) / 100;
  const symbol = SYMBOLS[currency] || "";
  const shown = Number.isInteger(major) ? major : major.toFixed(2);
  return symbol ? `${symbol}${shown}` : `${shown} ${currency}`;
}

// --- Events -----------------------------------------------------------------
// One row per event type: how to say it, and which colour it carries. The
// server sends raw types (`user.login`); the panel is the only place that knows
// they are meant to read as English.
//
// ⚠ AN UNKNOWN TYPE STILL RENDERS. A type added to the API before this map is
// updated falls through to its raw string rather than to an empty cell —
// undecorated, but never invisible.
const EVENT_LABELS = {
  "user.registered": { label: "Registered", tone: "ok", ico: "✦" },
  "user.login": { label: "Signed in", tone: "", ico: "→" },
  "user.login_failed": { label: "Sign-in failed", tone: "warn", ico: "✕" },
  "user.password_changed": { label: "Changed password", tone: "", ico: "⚿" },
  "user.deleted": { label: "Deleted their account", tone: "fail", ico: "⊘" },
  "admin.user_disabled": { label: "Account disabled", tone: "fail", ico: "🔒" },
  "admin.user_enabled": { label: "Account enabled", tone: "ok", ico: "🔓" },
  "admin.role_changed": { label: "Role changed", tone: "warn", ico: "⚑" },
  "admin.user_deleted": { label: "Account deleted", tone: "fail", ico: "🗑" },
  "admin.note_saved": { label: "Note saved", tone: "", ico: "✎" },
  "admin.feature_changed": { label: "Feature changed", tone: "warn", ico: "🎛" },
  "admin.override_set": { label: "Access overridden", tone: "warn", ico: "🔑" },
  "admin.tier_changed": { label: "Price changed", tone: "warn", ico: "💳" },
  "admin.user_tier_changed": { label: "Moved tier", tone: "ok", ico: "⬆" },
  "admin.offer_changed": { label: "Offer changed", tone: "warn", ico: "🏷" },
  "admin.branding_changed": { label: "Brand changed", tone: "warn", ico: "✨" },
  "subscription.started": { label: "Subscribed", tone: "ok", ico: "🧾" },
  "subscription.cancelled": { label: "Subscription cancelled", tone: "fail", ico: "⊘" },
};

export function eventLabel(type) {
  return EVENT_LABELS[type]?.label || type || "—";
}
export function eventTone(type) {
  return EVENT_LABELS[type]?.tone || "";
}
export function eventIcon(type) {
  return EVENT_LABELS[type]?.ico || "•";
}

// The one-line detail beside an event. Built from `meta`, which is
// type-specific — so this is a small switch and not a generic key/value dump,
// because a dump of `{"existed": false}` explains nothing to the person reading
// it at two in the morning.
export function eventDetail(ev) {
  const meta = ev?.meta || {};
  switch (ev?.type) {
    case "user.login_failed":
      if (meta.reason === "disabled") return "the account is disabled";
      // ⚠ THIS IS THE USEFUL ONE. A run of failures against addresses that
      // exist is somebody attacking accounts; the same run against addresses
      // that don't is a scanner working through a list.
      return meta.existed === false ? "no such account" : "wrong password";
    case "admin.role_changed":
      return `${meta.was || "user"} → ${meta.now || "user"}`;
    case "admin.feature_changed":
      // The status move is the part worth reading back weeks later; a rename or
      // a reorder says so without pretending a status changed.
      return meta.was_status && meta.was_status !== meta.now_status
        ? `${meta.feature}: ${meta.was_status} → ${meta.now_status}`
        : `${meta.feature} (${(meta.fields || []).join(", ") || "updated"})`;
    case "admin.tier_changed":
      // The money is what anybody reads this row back for; a copy edit says so
      // rather than pretending a price moved.
      if (meta.was_monthly !== meta.now_monthly) {
        return `${meta.tier}: ${money(meta.was_monthly)} → ${money(meta.now_monthly)} monthly`;
      }
      if (meta.was_yearly !== meta.now_yearly) {
        return `${meta.tier}: ${money(meta.was_yearly)} → ${money(meta.now_yearly)} yearly`;
      }
      return `${meta.tier} (${(meta.fields || []).join(", ") || "updated"})`;
    case "admin.user_tier_changed":
      return `${meta.was || "trial"} → ${meta.now || "trial"}`;
    case "admin.offer_changed":
      return `${meta.code || "sale"} ${meta.action || "changed"}${
        meta.summary ? ` (${meta.summary})` : ""
      }`;
    case "admin.branding_changed":
      // The rename is the one worth reading back months later, and it is the one
      // that needs both halves: "the app was renamed" without saying FROM WHAT
      // is the row somebody opens the feed to answer.
      if (meta.action === "renamed") return `renamed: ${meta.was || "—"} → ${meta.now || "—"}`;
      if (meta.action === "logo_uploaded") return "new logo uploaded";
      if (meta.action === "logo_removed") return "logo removed — back to the built-in mark";
      return meta.action || "updated";
    case "subscription.started":
      return `${meta.tier} ${meta.period} · ${money(meta.amount)}${
        meta.code ? ` with ${meta.code}` : ""
      }`;
    case "subscription.cancelled":
      return meta.tier || "";
    case "admin.override_set":
      if (meta.value === null || meta.value === undefined) {
        return `${meta.feature}: override removed`;
      }
      return `${meta.feature}: forced ${meta.value ? "on" : "off"}`;
    default:
      return "";
  }
}

// --- Access ------------------------------------------------------------------
// Why one feature resolves the way it does for one account. `source` comes from
// `features._resolve_one`, which is the SAME function that decides the answer —
// so the explanation can never drift from the thing it is explaining.
//
// ⚠ THE REASON IS THE WHOLE VALUE OF THIS PANEL. "Off" on its own is an
// unanswerable support ticket; "off because it is hidden for everyone" is a
// closed one.
const ACCESS_REASONS = {
  hidden: "Hidden for everyone — the site-wide switch is off",
  override: "Set by hand on this account",
  soon: "Marked Soon — shown with a badge, not usable",
  all: "On for everyone",
  admin: "Allowed because this account is an administrator",
  "admins-only": "Staged to administrators only",
  allowlist: "Not on the allow-list for this feature",
  percent: "Outside the percentage currently rolled out",
};

export function accessReason(state) {
  const base = ACCESS_REASONS[state?.source] || "";
  // An allow-list or percentage that PASSED reads wrong as its "not included"
  // wording, so those two flip on the outcome rather than on the rule alone.
  if (state?.on && state?.source === "allowlist") return "On the allow-list";
  if (state?.on && state?.source === "percent") return "Inside the rolled-out percentage";
  return base;
}

// Workflow names for the job-count breakdown. Keyed by `JobKind`'s values.
// ⚠ THESE ARE THE SIDEBAR'S NAMES, not the enum's — an administrator and a
// customer have to be able to talk about the same screen. `animatics-to-video`
// is the historical id whose label changed; the same applies here.
const KIND_LABELS = {
  generate: "Turnaround images",
  meshy: "3D models",
  storyboard: "Storyboards",
  animatic: "Animatics",
  plan: "Plans & scripts",
  final_video: "Final videos",
};

export function kindLabel(kind) {
  return KIND_LABELS[kind] || kind;
}
