// What the app is CALLED and what its mark looks like — the browser's half of
// `server/branding.py`.
//
// THE PROBLEM THIS SOLVES: the product name was typed into eight components and
// the mark was drawn in two more, so renaming the app was a code change. It is
// an admin panel field now, and the whole point is that ONE save lands
// EVERYWHERE — the rail, the sign-in card, the landing page, the admin top bar,
// a shared storyboard link, the browser tab and the favicon — without any screen
// keeping a copy of the answer.
//
// ⚠ A MODULE-LEVEL STORE, NOT A CONTEXT, exactly like `entitlements.js` — read
// its header for the full reasoning. The short version: a Context here would
// mean a provider around the shell AND around the logged-out landing page AND
// around the public storyboard viewer, which are three different roots.
//
// ⚠ **IT IS REMEMBERED IN `localStorage`, AND THAT IS THE POINT OF THE WHOLE
// FILE.** The name is drawn in the first paint, before any request can have
// answered. Without a remembered answer every reload would show the built-in
// "Aniwala AI Studio" for a beat and then flip to the customer's name — on the
// sign-in card, which is the first thing anybody sees. Same fix, same reasoning
// as the remembered entitlements and the remembered work counts in `api.js`.
//
// ⚠ **TWO LOGOS, ONE PER THEME.** A logo is a flat picture: unlike the drawn
// mark in `Logo.jsx`, which is painted in `currentColor` and re-colours itself
// for free, an uploaded white wordmark vanishes into the light theme. The server
// resolves the fallback (one upload still covers both), so BOTH `logoUrl` and
// `logoUrlLight` always point at a real file when there is any logo at all —
// there is no "borrow the other one" rule on this side.
//
// ⚠ **AND IT FAILS BACK TO THE BUILT-IN, NEVER TO NOTHING.** A failed call keeps
// whatever is already on screen. An app that draws no title at all is a worse
// outage than one showing yesterday's name.
//
// ⚠ **AND THE COLOURS RIDE ALONG WITH THE NAME, IN ONE CALL.** The palette an
// administrator chose on the Brand screen is part of what the app is CALLED as
// far as a browser is concerned — both are public, both are needed before the
// sign-in card is drawn, and both must be remembered or the first paint is the
// wrong one. A second `/public/theme` call would be a second thing to fail, a
// second thing to remember, and a second chance for the two to disagree about
// which brand is live. The maths that turns two hex strings into every token
// the app paints with is in `palette.js`; this file only carries them.
import * as api from "./api.js";
import { applyPalette, DEFAULT_PALETTE, normalisePalette } from "./palette.js";

const STORE_KEY = "cas_brand";

// What the app ships as. ⚠ KEEP IN STEP WITH `DEFAULT_NAME` IN
// `server/branding.py` — the server falls back to its copy, this falls back to
// this one, and they are only ever both used when the two cannot talk.
export const DEFAULT_BRAND = Object.freeze({
  name: "Aniwala AI Studio",
  // "" in both means: draw the built-in mark (`components/Logo.jsx`).
  logoUrl: "",
  logoUrlLight: "",
  stamp: "",
  stampLight: "",
  // The app as it ships. ⚠ This palette injects NO CSS at all — see
  // `palette.js`. It is the absence of an override, not a copy of the
  // stylesheet.
  palette: DEFAULT_PALETTE,
});

// The favicon the app ships with, restored when the uploaded logos are removed.
const DEFAULT_ICON = { href: "/favicon.svg", type: "image/svg+xml" };

/** `{name, logo_url, logo_url_light, …}` from the wire → the shape the app uses. */
function shape(raw) {
  const name = (raw?.name || "").trim();
  // ⚠ ABSOLUTE, BUILT HERE. The server answers with PATHS because the API's own
  // address is `VITE_API_BASE` and only this side knows it. Every consumer is an
  // `<img src>` or a `<link href>`, which resolve against the PAGE — so a bare
  // path would ask Vite on :5173 for a file the API holds on :8000.
  const dark = raw?.logo_url ? api.absoluteUrl(raw.logo_url) : "";
  // ⚠ FALLS BACK TO THE DARK ONE HERE TOO, even though the server already
  // resolved it. This is what makes an answer REMEMBERED BEFORE the light slot
  // existed still work: it has no `logo_url_light` at all, and without this the
  // light theme would draw nothing for one page load after the upgrade.
  const light = raw?.logo_url_light ? api.absoluteUrl(raw.logo_url_light) : dark;
  return Object.freeze({
    name: name || DEFAULT_BRAND.name,
    logoUrl: dark,
    logoUrlLight: light,
    stamp: raw?.stamp || "",
    stampLight: raw?.stamp_light || raw?.stamp || "",
    // ⚠ `normalisePalette` NEVER THROWS AND NEVER RETURNS A PARTIAL ANSWER, and
    // that is load-bearing here: this same function shapes the REMEMBERED
    // answer, which on the release that adds colours has no `accent` in it at
    // all, and can also be whatever somebody typed into localStorage.
    palette: normalisePalette({
      id: raw?.theme_id,
      accent: raw?.accent,
      ground: raw?.ground,
    }),
  });
}

function remembered() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    return raw && typeof raw === "object" ? shape(raw) : DEFAULT_BRAND;
  } catch {
    // Private mode, or somebody edited it by hand. No memory is not an error.
    return DEFAULT_BRAND;
  }
}

function remember(raw) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(raw));
  } catch {
    // Storage full or disabled — the app just flashes the default next reload.
  }
}

// ---------------------------------------------------------------------------
// The store
// ---------------------------------------------------------------------------
// One object, REPLACED and never mutated, so `useSyncExternalStore` can compare
// snapshots by identity. Seeded from the remembered answer at module load —
// which is before the first render, and is what removes the flash.
let _current = remembered();
const _subscribers = new Set();

/** The whole current brand, by reference. Stable between `setBrand` calls. */
export function getBrand() {
  return _current;
}

export function subscribe(fn) {
  _subscribers.add(fn);
  return () => _subscribers.delete(fn);
}

/**
 * Hand a fresh answer to every screen at once.
 *
 * ⚠ THE ADMIN PANEL CALLS THIS DIRECTLY AFTER A SAVE, and that is what makes
 * the change land instantly in the rail behind the panel instead of on the next
 * reload. The save's response is the same shape the public call returns, so
 * there is one path in and one path out.
 */
export function setBrand(raw) {
  const next = shape(raw);
  // Identity is the snapshot test, so an unchanged answer must not produce a new
  // object — a poll that re-set the same brand would otherwise re-render the
  // whole app on every tick.
  if (
    next.name === _current.name &&
    next.logoUrl === _current.logoUrl &&
    next.logoUrlLight === _current.logoUrlLight &&
    next.palette.id === _current.palette.id &&
    next.palette.accent === _current.palette.accent &&
    next.palette.ground === _current.palette.ground
  ) {
    return _current;
  }
  _current = next;
  remember({
    name: next.name,
    logo_url: raw?.logo_url || "",
    logo_url_light: raw?.logo_url_light || "",
    stamp: next.stamp,
    stamp_light: next.stampLight,
    // ⚠ REMEMBERED IN THE WIRE'S OWN FIELD NAMES, not the shaped ones, because
    // `remembered()` feeds this straight back through `shape`. A `palette`
    // object saved here would come back as a brand with no colours.
    theme_id: next.palette.id,
    accent: next.palette.accent,
    ground: next.palette.ground,
  });
  applyToDocument(next);
  for (const fn of _subscribers) fn();
  return _current;
}

/** Which logo THIS theme should draw. `dark` unless `<html>` says otherwise. */
function markFor(brand, theme) {
  return theme === "light" ? brand.logoUrlLight : brand.logoUrl;
}

/**
 * The browser tab: the title and the favicon.
 *
 * ⚠ THE TAB IS PART OF "EVERYWHERE" AND IT IS THE HALF PEOPLE FORGET. A rail
 * that says "Acme Studio" above a tab that still says "Aniwala AI Studio" is the
 * exact bug this whole change exists to remove — and the favicon is worse,
 * because a stale one survives in the bookmark.
 *
 * ⚠ THE FAVICON FOLLOWS THE THEME TOO, and it is the one place that needs JS to
 * do it: the in-app mark swaps in pure CSS off `<html data-theme>` (see
 * `base.css`), but a `<link rel=icon>` has no styling and a browser will not
 * pick between two of them. So this is re-run whenever `data-theme` changes —
 * see the observer at the foot of the file.
 *
 * The `<link>` is REPLACED, not edited: some browsers ignore an href changed in
 * place on the element they already parsed.
 */
export function applyToDocument(brand = _current) {
  if (typeof document === "undefined") return;
  document.title = brand.name;

  // ⚠ THE PALETTE IS STAMPED HERE, BESIDE THE TITLE AND THE FAVICON, AND NOT IN
  // A REACT EFFECT. Three of the screens that must be painted in the chosen
  // colours — the landing page, the sign-in card and the public storyboard
  // viewer — are outside the app shell, and one of them (`main.jsx`) runs this
  // BEFORE React mounts at all. An effect somewhere in the tree would repaint
  // the app a beat after it was drawn, which is the flash this file exists to
  // remove. Idempotent: `applyPalette` replaces one `<style>` and does nothing
  // when the CSS has not changed.
  applyPalette(brand.palette);

  const head = document.head;
  if (!head) return;
  const mark = markFor(brand, document.documentElement?.dataset?.theme);
  const icon = mark ? { href: mark, type: "image/png" } : DEFAULT_ICON;
  for (const old of head.querySelectorAll("link[rel='icon']")) old.remove();
  const link = document.createElement("link");
  link.rel = "icon";
  link.type = icon.type;
  link.href = icon.href;
  head.appendChild(link);
}

/**
 * Ask the server what the app is called. Safe to call more than once.
 *
 * ⚠ NO TOKEN AND NO ACCOUNT — the route is public because the sign-in card
 * needs the answer BEFORE anybody has signed in. See `server/branding.py`.
 *
 * ⚠ ITS FAILURE IS SILENT ON PURPOSE. There is nothing a visitor could do about
 * it and nothing to show them: the remembered name (or the built-in one) is
 * already on screen and stays there.
 */
export function loadBranding() {
  return api
    .publicBranding()
    .then((raw) => setBrand(raw))
    .catch(() => _current);
}

// ---------------------------------------------------------------------------
// Following the theme
// ---------------------------------------------------------------------------
// ⚠ AN OBSERVER, NOT A CALL FROM `theme.js`, and the reason is the number of
// places that flip the theme: `App.jsx`'s effect, the landing page's own switch,
// the admin bar's, and `main.jsx` at boot. Making each of them also re-stamp the
// favicon is four call sites and a fifth waiting to be forgotten — the same
// "everywhere" bug this file exists to end. `data-theme` on `<html>` is the ONE
// fact all four already agree on, so it is the thing to watch.
//
// Only the favicon needs this. The mark inside the app swaps in CSS.
if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
  new MutationObserver(() => applyToDocument()).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
}
