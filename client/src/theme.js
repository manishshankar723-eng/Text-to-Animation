// theme.js — light / dark mode.
//
// The choice is stamped on <html data-theme="…">, and every colour in
// styles.css comes from a CSS variable defined under that attribute. That's why
// one toggle re-skins the WHOLE frontend — including screens rendered outside
// the sidebar (landing, login, the public shared board) which never see the
// React state.

const KEY = "cas_theme";

export function getTheme() {
  const saved = localStorage.getItem(KEY);
  if (saved === "light" || saved === "dark") return saved;
  // No choice made yet — follow the OS setting.
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(KEY, theme);
}
