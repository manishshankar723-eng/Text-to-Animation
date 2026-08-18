// media_view.js — how the Media pane LISTS the assets in this animatic.
//
// ⚠ UI ONLY, exactly like `workspace.js`. A view changes the shape of the cards
// in the Media pane and nothing else: not the order of the frames, not their
// holds, not the project. Both views are the same list in the same DOM order,
// so drag-to-reorder behaves identically in either.
//
//   icon — thumbnails in a grid. For SEEING the footage: the picture is the
//          biggest thing on the card, and a 60-panel board is scannable a
//          screenful at a time.
//   list — one compact row each: small thumb, name, hold, tools. For FINDING a
//          shot by name and for retiming several in a row without scrolling
//          past a wall of pictures.
//
// Stored per browser rather than on the animatic, for the same reason the
// workspace is: it's how YOU like to work, not a property of the thing edited.

const KEY = "cas_animatic_media_view";

export const MEDIA_VIEWS = [
  { id: "icon", label: "Icon view", ico: "grid", note: "Thumbnails in a grid" },
  { id: "list", label: "List view", ico: "list", note: "One compact row per asset" },
];

export const DEFAULT_MEDIA_VIEW = "icon";

export function getMediaView() {
  try {
    const saved = localStorage.getItem(KEY);
    if (MEDIA_VIEWS.some((v) => v.id === saved)) return saved;
  } catch {
    // Private mode or storage disabled — the default is still a working pane.
  }
  return DEFAULT_MEDIA_VIEW;
}

export function saveMediaView(id) {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // Not worth telling anyone about: the view still changed for this session.
  }
}
