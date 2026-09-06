// OverlayGallery.jsx — the shelf you take a light leak, some grain or a glitch from.
//
// ⚠ NOT A DRAG SOURCE, UNLIKE THE SHAPES AND EFFECTS SHELVES BESIDE IT, and the
// difference is honest rather than lazy. Those two carry a payload the editor
// already has: dropping one is instant, so dragging it to a precise spot is the
// right gesture. An overlay does not exist yet — the server has to DRAW it, at
// this project's aspect ratio, which takes a few seconds — so a drag would
// finish with nothing to drop and the clip would appear somewhere else entirely
// a moment later. A press, a spinner on the tile you pressed, and a clip that
// lands at the playhead is the shape that matches what is actually happening.
//
// ⚠ AND THE BLEND MODE IS ON THE TILE. It is the one thing about these that is
// not obvious from the name and the one thing that ruins the effect if it ends
// up wrong — a leak on "normal" is an orange rectangle. It is set automatically
// when the clip lands; showing it here is so that the word on the tile and the
// word in Properties are visibly the same word.

import { OVERLAYS, OVERLAY_CATEGORIES } from "../animatic/fx_overlays.js";

export default function OverlayGallery({ busy = "", onAdd }) {
  // Filed here rather than in the table, so an overlay whose category names no
  // shelf still appears — under "Other" — instead of vanishing. The same rule
  // `fx_library.js` and the preset pickers keep: something nobody filed should
  // be visible and ugly, never invisible.
  const shelves = OVERLAY_CATEGORIES.map((c) => ({ ...c, items: [] }));
  const other = { id: "other", label: "Other", note: "Not filed anywhere yet", items: [] };
  const byId = new Map(shelves.map((s) => [s.id, s]));
  for (const overlay of OVERLAYS) (byId.get(overlay.category) || other).items.push(overlay);
  if (other.items.length) shelves.push(other);

  return (
    <div className="an-overlay-gallery">
      {shelves
        .filter((shelf) => shelf.items.length)
        .map((shelf) => (
          <div className="an-overlay-shelf" key={shelf.id}>
            <span className="an-preset-cap" title={shelf.note}>
              {shelf.label}
            </span>
            <div className="an-overlay-tiles">
              {shelf.items.map((overlay) => (
                <button
                  key={overlay.id}
                  type="button"
                  className={`an-overlay-tile ${busy === overlay.id ? "working" : ""}`}
                  // ⚠ EVERY TILE IS DISABLED WHILE ANY ONE IS DRAWING, not just
                  // the one pressed. Two generations at once are two multi-second
                  // CPU jobs on one server for one person, and the second clip
                  // would land on a row the first had not finished claiming.
                  disabled={Boolean(busy)}
                  title={`${overlay.note}  ·  blends on “${overlay.blend}”  ·  ${overlay.seconds}s`}
                  onClick={() => onAdd(overlay.id)}
                >
                  <span className="an-overlay-name">{overlay.label}</span>
                  <span className="an-overlay-blend">
                    {busy === overlay.id ? "drawing…" : overlay.blend}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ))}
    </div>
  );
}
