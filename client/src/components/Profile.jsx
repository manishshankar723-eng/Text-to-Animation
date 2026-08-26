import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
import Avatar from "./Avatar.jsx";
import {
  ALL_STYLES,
  ASPECTS,
  ALL_GENRES,
  MARKET_COUNTRIES,
  MARKET_LANGUAGES,
  ROLES,
} from "../storyboardOptions.js";

// Profile — everything about the person using the app, in one place.
//
// Sections, in the order they matter to the user:
//   Identity  — name and how they're addressed. Email is shown but NOT editable:
//               it is the login, so changing it is an account migration.
//   Work      — company and role. This is a studio tool; a shared board is more
//               useful when it's attributable to a person.
//   Defaults  — the storyboard form asks for style / aspect / genre every single
//               time. Setting them once here pre-fills that form. This is the
//               section that actually saves the user work.
//   Security  — change password (verifies the current one server-side).
//   3D keys   — moved here from Home: it's account configuration, not dashboard.
//   Danger    — delete account, also moved from Home, behind a typed confirmation.
//
// Each section saves on its own, and the API PATCH is partial, so saving one
// can never blank another.

const EMPTY = {
  full_name: "",
  display_name: "",
  company: "",
  role: "",
  default_style: "",
  default_aspect_ratio: "",
  default_genre: "",
  default_country: "",
  default_language: "",
  timezone: "",
};

// Offered as a short list; the browser's own zone is put first so the common
// case is one click. Not exhaustive on purpose — a 400-entry <select> is worse
// than a sensible few for a field this incidental.
const COMMON_TIMEZONES = [
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
  "Australia/Sydney",
  "UTC",
];

export default function Profile({
  email,
  onLogout,
  // ⚠ "Manage accounts" IN THE SIDEBAR'S SWITCHER LANDS HERE, so the managing
  // has to exist here. A menu row that opens a page with nothing to manage on
  // it is a dead end wearing a useful name. All optional: without them the
  // section hides and this is the single-account profile it has always been.
  accounts,
  onSwitchAccount,
  onAddAccount,
  onForgetAccount,
}) {
  const [form, setForm] = useState(EMPTY);
  const [loaded, setLoaded] = useState(null); // last saved copy, for dirty checks
  const [createdAt, setCreatedAt] = useState(null);
  const [apiKeys, setApiKeys] = useState({});
  const [error, setError] = useState("");
  const [savedNote, setSavedNote] = useState("");
  const [savingSection, setSavingSection] = useState("");

  // Password change
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [pwBusy, setPwBusy] = useState(false);
  const [pwError, setPwError] = useState("");
  const [pwDone, setPwDone] = useState(false);

  // Delete account — typed confirmation, not a single click.
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteText, setDeleteText] = useState("");
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, k] = await Promise.all([api.me(), api.getApiKeys().catch(() => ({}))]);
      const next = { ...EMPTY };
      for (const key of Object.keys(EMPTY)) next[key] = p?.[key] || "";
      setForm(next);
      setLoaded(next);
      setCreatedAt(p?.created_at || null);
      setApiKeys(k || {});
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
    setSavedNote("");
  }

  // Save only the fields of one section. PATCH is partial server-side, so the
  // other sections keep whatever they had even if this form is stale.
  async function saveSection(name, fields) {
    setSavingSection(name);
    setError("");
    setSavedNote("");
    try {
      const body = {};
      for (const f of fields) body[f] = form[f];
      const p = await api.updateProfile(body);
      const next = { ...form };
      for (const f of fields) next[f] = p?.[f] || "";
      setForm(next);
      setLoaded((prev) => ({ ...(prev || EMPTY), ...next }));
      setSavedNote(name);
      setTimeout(() => setSavedNote((s) => (s === name ? "" : s)), 2500);
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingSection("");
    }
  }

  function dirty(fields) {
    if (!loaded) return false;
    return fields.some((f) => (form[f] || "") !== (loaded[f] || ""));
  }

  async function submitPassword(e) {
    e.preventDefault();
    setPwError("");
    setPwDone(false);
    if (pw.next !== pw.confirm) {
      setPwError("The new passwords don't match.");
      return;
    }
    if (pw.next.length < 8) {
      setPwError("Use at least 8 characters.");
      return;
    }
    setPwBusy(true);
    try {
      await api.changePassword(pw.current, pw.next);
      setPw({ current: "", next: "", confirm: "" });
      setPwDone(true);
    } catch (err) {
      setPwError(err.message);
    } finally {
      setPwBusy(false);
    }
  }

  async function removeKey(provider) {
    try {
      await api.deleteApiKey(provider);
      setApiKeys((prev) => {
        const next = { ...prev };
        delete next[provider];
        return next;
      });
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setError("");
    try {
      await api.deleteAccount();
      api.clearSession();
      onLogout();
    } catch (e) {
      setError(e.message);
      setDeleting(false);
    }
  }

  const memberSince = createdAt
    ? new Date(createdAt).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : "—";
  const initial = (form.display_name || form.full_name || email || "?")
    .trim()
    .charAt(0)
    .toUpperCase();
  const savedKeys = Object.keys(apiKeys).filter((p) => apiKeys[p]);

  // The browser's zone first, then the rest, deduped.
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zones = [browserTz, ...COMMON_TIMEZONES.filter((z) => z !== browserTz)].filter(Boolean);

  function SaveRow({ name, fields }) {
    return (
      <div className="profile-save-row">
        <button
          type="button"
          className="btn primary small"
          disabled={savingSection === name || !dirty(fields)}
          onClick={() => saveSection(name, fields)}
        >
          {savingSection === name ? "Saving…" : "Save"}
        </button>
        {savedNote === name && <span className="profile-saved">✓ Saved</span>}
      </div>
    );
  }

  return (
    <div className="home profile-page">
      <header className="home-head">
        <h1>Your profile</h1>
        <p className="muted">
          Your details, your usual storyboard choices, and account settings.
        </p>
      </header>

      {error && <div className="error">{error}</div>}

      {/* Who you are */}
      <section className="card home-card">
        <div className="profile-top">
          <Avatar size={56} initial={initial === "?" ? "" : initial} />
          <div>
            <h2 className="profile-email">
              {form.display_name || form.full_name || email}
            </h2>
            <p className="muted tiny">Member since {memberSince}</p>
          </div>
        </div>

        <div className="profile-grid">
          <label className="field">
            <span className="field-label">Full name</span>
            <input
              value={form.full_name}
              maxLength={120}
              placeholder="e.g. Manish Shankar"
              onChange={(e) => set("full_name", e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Display name</span>
            <input
              value={form.display_name}
              maxLength={60}
              placeholder="What we should call you"
              onChange={(e) => set("display_name", e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Email</span>
            <input value={email || ""} disabled readOnly />
            <span className="field-hint">
              This is your login, so it can't be changed here.
            </span>
          </label>
          <label className="field">
            <span className="field-label">Time zone</span>
            <select
              value={form.timezone}
              onChange={(e) => set("timezone", e.target.value)}
            >
              <option value="">Not set</option>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
          </label>
        </div>
        <SaveRow name="identity" fields={["full_name", "display_name", "timezone"]} />
      </section>

      {/* Accounts on this browser. Hidden unless the shell handed the list
          down — see the prop note at the top. */}
      {accounts?.length > 0 && (
        <section className="card home-card">
          <h2>Accounts</h2>
          <p className="muted tiny">
            Signed in on this browser. Switching doesn't sign the others out.
          </p>

          <div className="acct-list">
            {accounts.map((a) => {
              const live = a.email === email;
              return (
                <div key={a.email} className={`acct-row ${live ? "on" : ""}`}>
                  <Avatar
                    size={38}
                    initial={(a.name || a.email || "?").trim().charAt(0).toUpperCase()}
                  />
                  <span className="acct-who">
                    <span className="acct-name">{a.name || a.email}</span>
                    {a.name && <span className="muted tiny">{a.email}</span>}
                  </span>
                  {live ? (
                    <span className="acct-live">Signed in</span>
                  ) : (
                    onSwitchAccount && (
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => onSwitchAccount(a.email)}
                      >
                        Switch
                      </button>
                    )
                  )}
                  {onForgetAccount && (
                    /* ⚠ "Remove" IS NOT "Delete account". It drops the token
                       this browser is holding — the account itself is
                       untouched, and signing in again brings it straight back.
                       Deleting for real is at the bottom of this page, behind a
                       typed confirmation, and says so. */
                    <button
                      type="button"
                      className="btn small ghost"
                      title={
                        live
                          ? "Remove this account from this browser — you'll be signed out"
                          : "Forget this account on this browser"
                      }
                      onClick={() => onForgetAccount(a.email)}
                    >
                      Remove
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          {onAddAccount && (
            <button type="button" className="btn" onClick={onAddAccount}>
              ＋ Add another account
            </button>
          )}
        </section>
      )}

      {/* Work */}
      <section className="card home-card">
        <h2>Work</h2>
        <p className="muted tiny">
          Shown alongside boards you share, so people know whose work it is.
        </p>
        <div className="profile-grid">
          <label className="field">
            <span className="field-label">Company / studio</span>
            <input
              value={form.company}
              maxLength={120}
              placeholder="e.g. VRImmersive Tech"
              onChange={(e) => set("company", e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Role</span>
            <select
              value={form.role}
              onChange={(e) => set("role", e.target.value)}
            >
              <option value="">Not set</option>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        </div>
        <SaveRow name="work" fields={["company", "role"]} />
      </section>

      {/* Creative defaults — the section that saves clicks */}
      <section className="card home-card">
        <h2>Storyboard defaults</h2>
        <p className="muted tiny">
          Your usual choices. New storyboards start with these already selected —
          you can still change them per board.
        </p>
        <div className="profile-grid">
          <label className="field">
            <span className="field-label">Visual style</span>
            <select
              value={form.default_style}
              onChange={(e) => set("default_style", e.target.value)}
            >
              <option value="">Ask me each time</option>
              {ALL_STYLES.filter((s) => s.id !== "custom").map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Aspect ratio</span>
            <select
              value={form.default_aspect_ratio}
              onChange={(e) => set("default_aspect_ratio", e.target.value)}
            >
              <option value="">Ask me each time</option>
              {ASPECTS.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.id} — {a.note}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Genre</span>
            <select
              value={form.default_genre}
              onChange={(e) => set("default_genre", e.target.value)}
            >
              <option value="">Ask me each time</option>
              {ALL_GENRES.filter((g) => g.id !== "custom").map((g) => (
                <option key={g.id} value={g.id}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* ⚠ THE AUDIENCE IS A SEPARATE BLOCK BECAUSE IT IS A DIFFERENT KIND OF
            SETTING. The three above change how a board LOOKS; these two change
            what is DRAWN — the money on a price tag, the language on a shop
            sign. Set once by a creator who always makes films for one market,
            and left blank the films simply show no prices, which is the right
            answer when nobody has said who is watching. See market.py. */}
        <h3 className="profile-subhead">Who your films are for</h3>
        <p className="muted tiny">
          Sets the currency and the on-screen language in every board you draw.
          Leave it blank and screens and signs are drawn with no prices and no
          readable text — better than the wrong country&rsquo;s.
        </p>
        <div className="profile-grid">
          <label className="field">
            <span className="field-label">Country / market</span>
            <select
              value={form.default_country}
              onChange={(e) => set("default_country", e.target.value)}
            >
              {MARKET_COUNTRIES.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id ? c.label : "Ask me each time"}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">On-screen language</span>
            <select
              value={form.default_language}
              onChange={(e) => set("default_language", e.target.value)}
            >
              {MARKET_LANGUAGES.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.id ? l.label : "Country's own language"}
                </option>
              ))}
            </select>
          </label>
        </div>
        <SaveRow
          name="defaults"
          fields={[
            "default_style",
            "default_aspect_ratio",
            "default_genre",
            "default_country",
            "default_language",
          ]}
        />
      </section>

      {/* Security */}
      <section className="card home-card">
        <h2>Password</h2>
        <form className="profile-grid" onSubmit={submitPassword}>
          <label className="field">
            <span className="field-label">Current password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={pw.current}
              onChange={(e) => setPw((p) => ({ ...p, current: e.target.value }))}
            />
          </label>
          <label className="field">
            <span className="field-label">New password</span>
            <input
              type="password"
              autoComplete="new-password"
              value={pw.next}
              onChange={(e) => setPw((p) => ({ ...p, next: e.target.value }))}
            />
            <span className="field-hint">At least 8 characters.</span>
          </label>
          <label className="field">
            <span className="field-label">Confirm new password</span>
            <input
              type="password"
              autoComplete="new-password"
              value={pw.confirm}
              onChange={(e) => setPw((p) => ({ ...p, confirm: e.target.value }))}
            />
          </label>
          <div className="profile-save-row">
            <button
              type="submit"
              className="btn primary small"
              disabled={pwBusy || !pw.current || !pw.next || !pw.confirm}
            >
              {pwBusy ? "Changing…" : "Change password"}
            </button>
            {pwDone && <span className="profile-saved">✓ Password changed</span>}
          </div>
        </form>
        {pwError && <div className="error tiny">{pwError}</div>}
      </section>

      {/* 3D provider keys */}
      <section className="card home-card">
        <h2>3D API keys</h2>
        {savedKeys.length === 0 ? (
          <p className="muted tiny">
            No keys saved. You'll be asked for one when you generate a 3D model.
          </p>
        ) : (
          <ul className="api-keys-list">
            {savedKeys.map((p) => (
              <li key={p} className="api-key-item">
                <span>
                  <strong style={{ textTransform: "capitalize" }}>{p}</strong>{" "}
                  <span className="badge ok">saved</span>
                </span>
                <button className="btn small danger-btn" onClick={() => removeKey(p)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Danger zone */}
      <section className="card home-card">
        <div className="danger-zone">
          <h3 className="danger-title">Danger zone</h3>
          {!confirmingDelete ? (
            <>
              <p className="muted tiny">
                Deleting your account removes your login permanently. This cannot
                be undone.
              </p>
              <button className="btn danger-btn" onClick={() => setConfirmingDelete(true)}>
                <Icon name="trash" /> Delete account
              </button>
            </>
          ) : (
            <div className="danger-confirm">
              <p className="tiny">
                This permanently deletes your account and cannot be undone. Type{" "}
                <strong>DELETE</strong> to confirm.
              </p>
              <input
                  value={deleteText}
                placeholder="DELETE"
                onChange={(e) => setDeleteText(e.target.value)}
              />
              <div className="danger-actions">
                <button
                  className="btn danger-btn"
                  disabled={deleting || deleteText.trim().toUpperCase() !== "DELETE"}
                  onClick={handleDelete}
                >
                  {deleting ? "Deleting…" : "Yes, delete permanently"}
                </button>
                <button
                  className="btn ghost small"
                  disabled={deleting}
                  onClick={() => {
                    setConfirmingDelete(false);
                    setDeleteText("");
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
