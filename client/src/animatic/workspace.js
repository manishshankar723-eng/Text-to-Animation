// workspace.js — which LAYOUT the editor is arranged in.
//
// ⚠ THIS IS UI ONLY. A workspace decides where the panes sit and how wide they
// are; it never touches the project — not the aspect ratio, not the frame size,
// not the fps. Picking "Reel / Shorts" on a 16:9 animatic rearranges the screen
// and leaves the video exactly as it was, the same way Premiere's workspaces do.
//
// The choice is a class on `.an-nle` (`an-ws-long` / `an-ws-reel`), and the
// difference between the two is in two places and no others: where each pane
// sits (the "Workspaces" block of styles/animatic-editor.css — `an-ws-long` is
// the plain column of panes over a timeline, `an-ws-reel` re-lays the same
// markup as a grid so Program can run the full height of the window with the
// timeline beside it) and how big the panes START (`pane_layout.js`, which also
// remembers what you dragged them to, per workspace).
//
// Stored per browser rather than on the animatic, for the same reason the theme
// is: it's how YOU like to work, not a property of the thing being edited.

const KEY = "cas_animatic_workspace";

// ⚠ `ico` IS AN ICON NAME, NOT A GLYPH. These used to be ▭ and ▯ — two
// rectangles that told you one was wider than the other and nothing about where
// anything would go. Each is now a small MAP of the layout it switches to
// (`Icon.jsx`, "layout-long" / "layout-reel"): the seams drawn where the real
// seams are, with the Program pane filled in. Move a pane in the CSS and the
// icon has to move with it.
export const WORKSPACES = [
  {
    id: "long",
    label: "Long Video Workspace",
    short: "Long video",
    ico: "layout-long",
    note: "Media, a wide Program and Properties in a row, timeline underneath — for 16:9 films, ads and explainers",
  },
  {
    id: "reel",
    label: "Reel / Shorts Video Workspace",
    short: "Reel / Shorts",
    ico: "layout-reel",
    note: "One tall monitor down the whole left side, with media, properties and the timeline stacked beside it — for 9:16 phone cuts",
  },
];

export const DEFAULT_WORKSPACE = "long";

export function getWorkspace() {
  try {
    const saved = localStorage.getItem(KEY);
    if (WORKSPACES.some((w) => w.id === saved)) return saved;
  } catch {
    // Private mode or storage disabled — the default is still a working editor.
  }
  return DEFAULT_WORKSPACE;
}

export function saveWorkspace(id) {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // Not worth telling anyone about: the layout still changed for this session.
  }
}

function workspaceOf(id) {
  return WORKSPACES.find((w) => w.id === id) || WORKSPACES[0];
}

export function workspaceLabel(id) {
  return workspaceOf(id).label;
}

// The icon for the layout you are IN — the top bar draws it on the button that
// opens the picker, so the control shows the arrangement it is currently in
// rather than a gear that could open anything.
export function workspaceIcon(id) {
  return workspaceOf(id).ico;
}
