// pane_layout.js — how big each pane is, and the memory of what you dragged.
//
// Three numbers describe the whole workspace, because that is all the grid has
// left to be told:
//
//   left      the first column's width in px  (Media in Long, Program in Reel)
//   right     the last column's width in px   (Properties in both)
//   timeline  the timeline pane's height in px
//
// The middle column is whatever is left over (`minmax(0, 1fr)`), so it can never
// be dragged into a negative width and the three numbers can't disagree about
// how wide the window is.
//
// ⚠ THEY ARE PIXELS, AND THEY ARE PER WORKSPACE. Pixels because that is what a
// drag produces — storing a fraction would mean the pane you sized to fit a
// waveform quietly resizes itself when you open the window wider. Per workspace
// because the layouts want different shapes: the Reel monitor is tall and needs
// width, the Long one is wide and needs the middle. Switching workspaces
// restores the sizes you left that workspace in.
//
// Everything is clamped against the CURRENT window on the way out (`getPaneLayout`
// / `clampLayout`), so a layout saved on a 4K screen can't leave a 1280 laptop
// with two side panes and no middle.

import { clamp } from "./util.js";

const KEY = "cas_animatic_panes";

export function viewport() {
  return {
    w: window.innerWidth || 1280,
    h: window.innerHeight || 800,
  };
}

// What a drag is allowed to ask for. The minimums are the point at which a pane
// stops being usable rather than merely narrow — a Properties column under
// ~14rem wraps every value box onto its own line — and the maximums exist so
// one pane can't take the whole window and leave nothing to edit.
export function paneLimits(vp = viewport()) {
  const { w, h } = vp;
  return {
    left: { min: 168, max: Math.max(240, Math.round(w * 0.42)) },
    right: { min: 224, max: Math.max(280, Math.round(w * 0.42)) },
    timeline: { min: 132, max: Math.max(200, Math.round(h * 0.62)) },
  };
}

// The sizes a workspace opens at when you have never dragged it. Derived from
// the window rather than hard-coded, so a 4K screen doesn't open with a laptop's
// panes — this is what the `clamp()`s in the stylesheet used to do.
export function defaultLayout(workspace, vp = viewport()) {
  const { w, h } = vp;
  const long = {
    left: clamp(Math.round(w * 0.15), 176, 272),
    right: clamp(Math.round(w * 0.2), 232, 352),
    timeline: clamp(Math.round(h * 0.3), 208, 320),
  };
  if (workspace !== "reel") return long;
  // ⚠ THE MONITOR LEADS HERE, so it opens with real width. A tall picture in a
  // 12rem column is the bug this workspace was reported with: the pane fits the
  // picture to the NARROWER axis, so a column sized for a file list gives a
  // postage stamp however much height it has.
  //
  // The width is derived from the HEIGHT the column now has, because in this
  // workspace Program is a full-height column beside the timeline rather than a
  // pane above it (the "Reel / Shorts" block in styles/animatic-editor.css). A
  // 9:16 picture is bounded by height, so the width that exactly wraps it is
  // that height × 9/16 — anything more is empty gutter either side, anything
  // less crops nothing but shrinks the picture. `chrome` is what the window
  // spends before the column starts: the shell's padding, the top bar, and the
  // pane's own head and transport row.
  const chrome = 190;
  const monitor = Math.round((h - chrome) * (9 / 16));
  return {
    ...long,
    left: clamp(monitor, 264, Math.min(560, Math.round(w * 0.42))),
    // The timeline no longer takes height OFF the picture — it sits beside it —
    // so it opens at the same generous height as the long workspace instead of
    // the squeezed one this layout used to need.
    timeline: clamp(Math.round(h * 0.3), 208, 340),
  };
}

export function clampLayout(layout, vp = viewport()) {
  const lim = paneLimits(vp);
  return {
    left: clamp(Math.round(layout.left), lim.left.min, lim.left.max),
    right: clamp(Math.round(layout.right), lim.right.min, lim.right.max),
    timeline: clamp(Math.round(layout.timeline), lim.timeline.min, lim.timeline.max),
  };
}

function readStore() {
  try {
    const raw = localStorage.getItem(KEY);
    const all = raw ? JSON.parse(raw) : null;
    return all && typeof all === "object" ? all : {};
  } catch {
    // Unreadable or storage disabled — the defaults are a working editor.
    return {};
  }
}

const isSize = (v) => typeof v === "number" && Number.isFinite(v);

export function getPaneLayout(workspace, vp = viewport()) {
  const base = defaultLayout(workspace, vp);
  const saved = readStore()[workspace];
  if (!saved || typeof saved !== "object") return base;
  // Field by field: a half-written or older record keeps whichever sizes it
  // does have and takes the default for the rest.
  return clampLayout(
    {
      left: isSize(saved.left) ? saved.left : base.left,
      right: isSize(saved.right) ? saved.right : base.right,
      timeline: isSize(saved.timeline) ? saved.timeline : base.timeline,
    },
    vp
  );
}

export function savePaneLayout(workspace, layout) {
  try {
    const all = readStore();
    all[workspace] = {
      left: Math.round(layout.left),
      right: Math.round(layout.right),
      timeline: Math.round(layout.timeline),
    };
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // Not worth a message: the panes are still the size you dragged them to.
  }
}
