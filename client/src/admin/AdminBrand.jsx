// AdminBrand.jsx — what the app is CALLED and what its mark looks like.
//
// This is the screen the whole change exists for: before it, renaming the app
// meant editing eight JSX files and the mark could only ever be the one drawn in
// `Logo.jsx`. Both are a field here now, and both land EVERYWHERE at once — the
// rail, the sign-in card, the landing page, the bar above this very form, a
// shared storyboard link, the browser tab and the favicon.
//
// ⚠ THE SAVE UPDATES THE APP IN PLACE, WITHOUT A RELOAD, and that is not a
// flourish. `setBrand` writes into the same module store every screen reads
// (`src/branding.js`), so the top bar over this form changes as the request
// answers — which is the only feedback that actually proves the save worked.
// A panel that needs a refresh to show its own effect is a panel people press
// twice.
//
// ⚠ TWO SEPARATE SAVES, ON PURPOSE. The name has a Save button because it is
// typed and half-typed text must not be written; a logo saves the instant a
// file is chosen, because choosing a file IS the decision — there is nothing
// half-done about it, and a picked-but-unsaved logo sitting behind a button is
// the state people walk away from thinking they were finished.
//
// ⚠ AND THE TWO LOGO CARDS EACH SHOW THEIR OWN THEME'S BACKGROUND, WHATEVER
// THEME THE PANEL ITSELF IS IN. That is the entire reason this screen was
// rebuilt: a white wordmark previewed on a dark card looks perfect and then
// disappears into the light rail — *"jab mai light mode mai karta hun to mera
// logo white mai merge ho raha hai."* You cannot check a logo against a ground
// you are not being shown, so both grounds are always on screen.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { setBrand } from "../branding.js";
import Logo from "../components/Logo.jsx";
import { formatDateTime } from "./format.js";

// ⚠ THE SLOT IDS ARE THE SERVER'S (`branding.SLOTS`) — they go straight into the
// URL. What is editable here is how each one is DESCRIBED, which is the half a
// non-technical owner reads: "Dark mode" is the words on the sidebar's own
// toggle, not "primary" or "inverse".
const SLOTS = [
  {
    id: "dark",
    label: "Dark mode",
    ico: "🌙",
    says: "Shown when the app is in dark mode. A light or white logo belongs here.",
  },
  {
    id: "light",
    label: "Light mode",
    ico: "☀️",
    says: "Shown when the app is in light mode. A dark or black logo belongs here.",
  },
];

export default function AdminBrand() {
  const [row, setRow] = useState(null);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .adminGetBranding()
      .then((r) => {
        setRow(r);
        setName(r.name || "");
        // ⚠ THE READ SEEDS THE STORE TOO. An administrator who opens this tab in
        // a browser that has never loaded the app elsewhere would otherwise be
        // looking at a form full of the real name inside a shell still wearing
        // the built-in one.
        setBrand(r);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  /** Every write funnels through here: one place that applies the answer. */
  async function run(what, call) {
    setBusy(what);
    setError("");
    try {
      const saved = await call();
      setRow(saved);
      setName(saved.name || "");
      setBrand(saved);
    } catch (e) {
      setError(e.message);
      // Put the fields back to what the server actually holds — a rejected
      // upload must not leave a preview of a logo nobody has.
      load();
    } finally {
      setBusy("");
    }
  }

  if (loading || !row) {
    return (
      <div className="admin-body">
        <div className="card admin-card">
          <p className="muted">{error || "Loading…"}</p>
        </div>
      </div>
    );
  }

  const nameMax = row.name_max || 40;
  const trimmed = name.trim().replace(/\s+/g, " ");
  // ⚠ COMPARED AGAINST THE SERVER'S CLEANED NAME, not the raw box. The server
  // collapses whitespace (`branding.clean_name`), so typing two spaces and
  // deleting one must not leave Save looking armed when there is nothing to save.
  const dirty = trimmed && trimmed !== row.name;

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="info-msg admin-note-box">
        A change here lands on every screen at once — the sidebar, the sign-in
        card, the landing page, a shared storyboard link and the browser tab.
        Anyone with the app already open sees it the next time their page loads.
      </div>

      {/* ================================================== the name ======= */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">App name</h2>
            <p className="muted tiny admin-group-blurb">
              What customers see this product called. This is the app's own name
              — not a customer's brand on a storyboard, which is set per board
              inside Script to Storyboard.
            </p>
          </div>
        </div>

        {/* A real preview, drawn with the real component at the sidebar's own
            sizes — so what is in this box is literally what the rail shows,
            in the theme the panel is currently in. */}
        <div className="admin-brand-preview">
          <span className="admin-brand-preview-mark">
            <Logo />
          </span>
          <span className="admin-brand-preview-name">{row.name}</span>
          <span className="muted tiny">
            {row.has_logo ? "Uploaded logo" : "Built-in mark"}
          </span>
        </div>

        <div className="admin-rollout admin-brand-form">
          <label className="admin-rollout-row wide">
            <span className="muted tiny">
              Up to {nameMax} characters, because the sidebar trims anything
              longer
            </span>
            <input
              className="admin-search"
              value={name}
              maxLength={nameMax}
              placeholder={row.default_name}
              disabled={!!busy}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && dirty) {
                  e.preventDefault();
                  run("name", () => api.adminSaveBranding({ name: trimmed }));
                }
              }}
            />
            <span className="muted tiny admin-brand-count">
              {name.length}/{nameMax}
            </span>
          </label>

          <div className="admin-brand-acts">
            <button
              type="button"
              className="btn primary"
              disabled={!dirty || !!busy}
              onClick={() => run("name", () => api.adminSaveBranding({ name: trimmed }))}
            >
              {busy === "name" ? "Saving…" : "Save name"}
            </button>
            {/* Only offered when it would change something — a "Reset" that does
                nothing is a button people press to find out what it does. */}
            {row.name !== row.default_name && (
              <button
                type="button"
                className="btn small"
                disabled={!!busy}
                title={`Back to ${row.default_name}`}
                onClick={() =>
                  run("name", () => api.adminSaveBranding({ name: row.default_name }))
                }
              >
                Reset name
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ================================================== the logos ====== */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">Logo — one per theme</h2>
            <p className="muted tiny admin-group-blurb">
              A logo is a picture and cannot re-colour itself, so a white logo
              disappears on a white background. Upload the version that reads on
              each. ⚠ <strong>One is enough to start</strong> — a logo with its
              own colours is used for both until you upload a second.
            </p>
          </div>
        </div>

        <div className="admin-brand-slots">
          {SLOTS.map((slot) => (
            <LogoSlot
              key={slot.id}
              slot={slot}
              row={row}
              busy={busy}
              name={row.name}
              onRun={run}
            />
          ))}
        </div>

        {row.updated_at && (
          <p className="muted tiny admin-brand-when">
            Last changed {formatDateTime(row.updated_at)}
            {row.updated_by ? ` by ${row.updated_by}` : ""}.
          </p>
        )}
      </section>
    </div>
  );
}

/**
 * One theme's logo: what it looks like on that theme's own background, and the
 * two buttons that change it.
 *
 * ⚠ THE GROUND IS FORCED (`.admin-brand-slot.dark` / `.light`), not inherited.
 * Previewing both logos on whatever theme the panel happens to be in is exactly
 * how the white-on-white fault got shipped in the first place.
 */
function LogoSlot({ slot, row, busy, name, onRun }) {
  const fileRef = useRef(null);
  const state = row.logos?.[slot.id] || {};
  // ⚠ `own` AND `url` ARE DIFFERENT QUESTIONS. `url` is what this theme will
  // ACTUALLY draw — which may be the other slot's file, because the server
  // resolves the fallback. `own` is whether a file was uploaded for THIS slot,
  // and it is what decides whether there is anything to Remove.
  const own = !!state.own;
  const url = slot.id === "light" ? row.logo_url_light : row.logo_url;
  const working = busy === `logo:${slot.id}`;

  return (
    <div className={`admin-brand-slot ${slot.id}`}>
      <div className="admin-brand-slot-head">
        <span className="admin-brand-slot-ico">{slot.ico}</span>
        <span className="admin-brand-slot-label">{slot.label}</span>
      </div>

      {/* The rail row as this theme will draw it: mark, then the app name, on
          this theme's own background. */}
      <div className="admin-brand-slot-stage">
        {url ? (
          <img src={api.absoluteUrl(url)} alt="" className="admin-brand-slot-img" />
        ) : (
          <span className="admin-brand-slot-drawn">
            <Logo plain />
          </span>
        )}
        <span className="admin-brand-slot-name">{name}</span>
      </div>

      <p className="muted tiny admin-brand-slot-says">
        {slot.says}
        {url && !own && " Right now it is borrowing the other one."}
        {!url && " Right now it is the built-in mark."}
      </p>

      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Cleared FIRST, so choosing the same file twice — which is what
          // somebody does after re-exporting it — fires `change` again.
          e.target.value = "";
          if (file) {
            onRun(`logo:${slot.id}`, () =>
              api.adminUploadBrandingLogo(slot.id, file)
            );
          }
        }}
      />

      <div className="admin-brand-acts">
        <button
          type="button"
          className="btn small"
          disabled={!!busy}
          onClick={() => fileRef.current?.click()}
        >
          {working ? "Uploading…" : own ? "Replace" : "📁 Upload"}
        </button>
        {own && (
          <button
            type="button"
            className="btn small"
            disabled={!!busy}
            title="This theme then uses the other logo, or the built-in mark"
            onClick={() =>
              onRun(`logo:${slot.id}`, () => api.adminRemoveBrandingLogo(slot.id))
            }
          >
            Remove
          </button>
        )}
      </div>
    </div>
  );
}
