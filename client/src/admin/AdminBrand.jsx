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
import { getBrand, setBrand } from "../branding.js";
import {
  applyPalette,
  contrast,
  derive,
  isBuiltIn,
  isHex,
  normalisePalette,
  PRESETS,
} from "../palette.js";
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

          <div className="admin-brand-acts btn-row">
            <button
              type="button"
              className="btn primary"
              disabled={!dirty || !!busy}
              onClick={() => run("name", () => api.adminSaveBranding({ name: trimmed }))}
            >
              {busy === "name" ? "Saving…" : "Save name"}
            </button>
            {/* Only offered when it would change something — a "Reset" that does
                nothing is a button people press to find out what it does.
                ⚠ NOT `btn small`: it stands beside Save name, and two buttons
                side by side are one size (RULEBOOK E102). */}
            {row.name !== row.default_name && (
              <button
                type="button"
                className="btn"
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

      {/* ================================================== the colours ====
          ⚠ LAST ON THE PAGE, AND ASKED FOR THERE: *"colours wala panel niche
          rakho, Logo — one per theme panel ke niche move karo"*. It also reads
          better here — the name and the mark are what the app IS, and are the
          two things somebody opens this tab to set; the palette is the thing
          they come back to fiddle with. Pinned in `tests/palette_check.py` §7,
          because "which card is third" is exactly the kind of thing a later
          edit reshuffles without noticing. */}
      <ThemePicker row={row} busy={busy} onRun={run} />
    </div>
  );
}


/**
 * The colour theme: eight presets, or two colours of your own.
 *
 * ⚠ **CHOOSING IS A LIVE PREVIEW OF THE WHOLE APP, NOT A SWATCH.** Clicking a
 * card repaints everything on screen immediately — this panel, its top bar, the
 * rail behind it — and nothing has been saved yet. That is the entire request
 * this screen answers: *"mujhe change kar ke dekhna hai kismai thik lagega so
 * fir mai ohi set kar dunga"*. A grid of postage stamps cannot answer "which of
 * these is right", because what a theme feels like is a whole screen of it, not
 * a 40px square.
 *
 * ⚠ **AND AN UNSAVED PREVIEW IS PUT BACK ON THE WAY OUT.** Leaving this tab, or
 * pressing Cancel, restores whatever the server actually holds. A panel that
 * left the app wearing a colour nobody saved would have the administrator
 * hunting a setting that does not exist — and the store is the only truth about
 * what everyone ELSE is seeing.
 *
 * ⚠ **THE CONTRAST IS MEASURED AND SHOWN, NOT LEFT TO THE EYE.** A custom
 * accent is two clicks from being pale yellow text on a white panel. The
 * numbers under the pickers are the real WCAG ratios of the derived tokens
 * (`palette.js` does the derivation; this only reads it), for BOTH themes,
 * because a colour chosen in dark mode is the same colour half the users see on
 * white. Below the bar it says so in words — it does not block the save, which
 * is a deployment's own call to make, but it will not let it be made by
 * accident.
 */
// ⚠ EXPORTED FOR THE TEST, AND THAT IS A REAL REASON. `tests/palette_check.py`
// renders this component with `renderToStaticMarkup` — RULEBOOK E90: a green
// `npm run build` is not evidence a screen renders, because esbuild never calls
// the function. It is not imported anywhere else; `AdminBrand` uses it directly.
export function ThemePicker({ row, busy, onRun }) {
  const saved = normalisePalette(row);
  const [pal, setPal] = useState(saved);

  // ⚠ THE PREVIEW IS AN EFFECT ON `pal`, AND THE CLEANUP IS THE PUT-BACK. One
  // place paints and one place restores, so there is no path out of this
  // component — tab change, navigation, unmount — that leaves the app painted
  // in something nobody chose. `getBrand()` rather than `saved` on the way out
  // because a save that happened while this was open is the newer truth.
  useEffect(() => {
    applyPalette(pal);
    return () => applyPalette(getBrand().palette);
  }, [pal]);

  // A fresh answer from the server (a save landed, or the tab reloaded) is what
  // "unchanged" is measured against from then on.
  useEffect(() => {
    setPal(normalisePalette(row));
  }, [row.theme_id, row.accent, row.ground]);

  const dirty = pal.id !== saved.id || pal.accent !== saved.accent
    || pal.ground !== saved.ground;
  const tokens = derive(pal);

  // The four things that decide whether the app is READABLE, in both themes.
  //
  // ⚠ EACH HAS ITS OWN BAR, AND THE BUTTON'S IS LOWER FOR A REASON. Body text,
  // quiet text and the accent-as-text carry SENTENCES, so they are held to
  // WCAG AA's 4.5:1. A filled accent button carries a short bold label, which
  // is WCAG's large-text case at 3:1 — `theme.css` says exactly this about its
  // own gold, and holding the button to 4.5 would rule out most brand colours
  // for no reading benefit.
  //
  // ⚠ AND THE LAST ROW IS THE ONE NOTHING CAN CORRECT, WHICH IS WHY IT IS THE
  // ROW THIS WHOLE STRIP EXISTS FOR. The first three are DERIVED, and
  // `readable()` moves them until they clear — measured, they essentially
  // always pass, and a warning that can never fire is decoration. "Button on
  // panel" is different: the fill IS the colour that was chosen, sitting on the
  // ground that was chosen, and nothing may quietly change either. Pick an
  // accent close to the ground and the buttons stop being visible at all —
  // which is RULEBOOK **E66** (*"buttun merge ho ja raha hai bg mai"*) arriving
  // by a new door, and the only honest answer is to measure it and say so.
  // 3:1 is WCAG's bar for a user-interface component's own boundary.
  const rows = [
    { id: "text", label: "Body text", want: 4.5, on: "--panel", token: "--text" },
    { id: "muted", label: "Quiet text", want: 4.5, on: "--panel", token: "--muted" },
    { id: "primary", label: "Accent as text", want: 4.5, on: "--panel", token: "--primary" },
    { id: "ink", label: "Button label", want: 3, on: "--gold-fill", token: "--gold-ink" },
    { id: "fill", label: "Button on panel", want: 3, on: "--panel", token: "--gold-fill" },
  ].map((r) => ({
    ...r,
    dark: contrast(tokens.dark[r.token], tokens.dark[r.on]),
    light: contrast(tokens.light[r.token], tokens.light[r.on]),
  }));
  const failing = rows.filter((r) => r.dark < r.want || r.light < r.want);

  function setColour(which, value) {
    // ⚠ THE ID BECOMES "custom" THE MOMENT A COLOUR IS TOUCHED. Leaving the
    // preset's name on a palette that is no longer the preset is how a panel
    // ends up highlighting "Emerald" over a screen that is orange.
    setPal((p) => normalisePalette({ id: "custom", ...p, [which]: value }));
  }

  return (
    <section className="card admin-card">
      <div className="admin-section-head">
        <div>
          <h2 className="admin-h2">Colours</h2>
          <p className="muted tiny admin-group-blurb">
            Two colours — an accent and a ground — and the app derives the rest:
            panels, borders, text, buttons and the timeline's highlights, in
            <strong> both</strong> light and dark mode. Click one to see it;
            nothing is saved until you press Save colours.
          </p>
        </div>
      </div>

      <div className="admin-theme-grid">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`admin-theme-card ${pal.id === p.id ? "on" : ""}`}
            disabled={!!busy}
            title={p.blurb}
            aria-pressed={pal.id === p.id}
            onClick={() => setPal(normalisePalette({ id: p.id }))}
          >
            <ThemeChip accent={p.accent} ground={p.ground} />
            <span className="admin-theme-card-label">
              {p.label}
              {p.builtIn && <span className="muted tiny"> · built-in</span>}
            </span>
          </button>
        ))}
      </div>

      {/* ---- your own two colours ---------------------------------------- */}
      <div className="admin-theme-own">
        <ThemeChip accent={pal.accent} ground={pal.ground} big />
        <div className="admin-theme-fields">
          <ColourField
            label="Accent"
            hint="Buttons, links, the selected clip on the timeline."
            value={pal.accent}
            busy={!!busy}
            onChange={(v) => setColour("accent", v)}
          />
          <ColourField
            label="Ground"
            hint="The dark panels. Light mode stays white, tinted with this hue."
            value={pal.ground}
            busy={!!busy}
            onChange={(v) => setColour("ground", v)}
          />
        </div>
      </div>

      {/* ---- what the choice actually measures ---------------------------- */}
      <div className={`admin-theme-checks ${failing.length ? "warn" : ""}`}>
        <span className="admin-theme-checks-head">
          Readability {failing.length ? "⚠" : "✓"}
        </span>
        {rows.map((r) => (
          <span key={r.id} className="admin-theme-check">
            {r.label}
            <b className={r.dark < r.want ? "bad" : ""}>{r.dark.toFixed(1)}:1</b>
            <span className="muted tiny">dark</span>
            <b className={r.light < r.want ? "bad" : ""}>{r.light.toFixed(1)}:1</b>
            <span className="muted tiny">light</span>
          </span>
        ))}
      </div>
      {failing.length > 0 && (
        <p className="muted tiny admin-theme-warn">
          {failing.map((r) => `${r.label} needs ${r.want}:1`).join(" · ")}. You
          can still save this — the numbers above are measured, not guessed —
          but look at both themes before you do.
        </p>
      )}

      <div className="admin-brand-acts btn-row">
        <button
          type="button"
          className="btn primary"
          disabled={!dirty || !!busy}
          onClick={() =>
            onRun("theme", () =>
              api.adminSaveBranding({
                theme_id: pal.id,
                accent: pal.accent,
                ground: pal.ground,
              })
            )
          }
        >
          {busy === "theme" ? "Saving…" : "Save colours"}
        </button>
        {/* Only while there is something to undo — a Cancel over an unchanged
            form is a button people press to find out what it does. */}
        {dirty && (
          <button
            type="button"
            className="btn"
            disabled={!!busy}
            onClick={() => setPal(saved)}
          >
            Cancel
          </button>
        )}
        {!isBuiltIn(saved) && !dirty && (
          <button
            type="button"
            className="btn"
            disabled={!!busy}
            title="Back to the colours the app ships with"
            onClick={() => setPal(normalisePalette({ id: "gold" }))}
          >
            Back to built-in
          </button>
        )}
      </div>
    </section>
  );
}

/** A theme in miniature: the ground, a panel on it, and the accent twice. */
function ThemeChip({ accent, ground, big = false }) {
  return (
    <span
      className={`admin-theme-chip ${big ? "big" : ""}`}
      style={{ background: ground }}
      aria-hidden="true"
    >
      <span className="admin-theme-chip-bar" style={{ background: accent }} />
      <span className="admin-theme-chip-dot" style={{ background: accent }} />
    </span>
  );
}

/**
 * One colour: the OS picker and the hex beside it.
 *
 * ⚠ BOTH, NOT ONE. `<input type="color">` is how somebody browses for a colour
 * and is the only comfortable way to do it with a mouse; the text box is the
 * only way to type the hex off a brand guideline, which is how a real brand
 * colour actually arrives. They are the same value.
 *
 * ⚠ THE TEXT BOX IS FREE UNTIL IT IS VALID. It keeps its own draft so that
 * clearing the field to retype does not repaint the whole app black on the way
 * through "#", and only hands up an answer once there is a whole colour in it.
 */
function ColourField({ label, hint, value, busy, onChange }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  return (
    <label className="admin-theme-field">
      <span className="admin-theme-field-label">{label}</span>
      <span className="admin-theme-field-row">
        <input
          type="color"
          className="admin-theme-swatch"
          value={value}
          disabled={busy}
          onChange={(e) => onChange(e.target.value)}
        />
        <input
          type="text"
          className="admin-search admin-theme-hex"
          value={draft}
          disabled={busy}
          spellCheck={false}
          maxLength={7}
          onChange={(e) => {
            setDraft(e.target.value);
            if (isHex(e.target.value)) onChange(e.target.value.trim().toLowerCase());
          }}
          onBlur={() => setDraft(value)}
        />
      </span>
      <span className="muted tiny">{hint}</span>
    </label>
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

      <div className="admin-brand-acts btn-row">
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
