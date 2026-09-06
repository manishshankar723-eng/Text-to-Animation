// PresetPicker.jsx — one shelf of animation presets, filed in folders.
//
// ⚠ THE SAME PICKER THE SHAPE KIND USES, AND DELIBERATELY SO. `ShapeProperties`
// already had this problem — forty-one choices that will not fit in a row — and
// answered it with a scrolling column of captioned groups (`.an-shape-picker`).
// Two pickers of the same shape in one Properties pane that looked different
// would be two things to learn where there is one, so this reuses those class
// names' rules rather than inventing a second look; see the shared selector in
// `animatic-tools.css`.
//
// ⚠ AND IT IS CAPPED IN HEIGHT RATHER THAN COLLAPSED, for the reason written on
// the shape picker: choosing an animation is a small adjustment made often, and
// a picker you have to open first is a worse trade than a picker you scroll.
//
// ⚠ NOTHING IN HERE IS EVER SHOWN AS "CURRENT". A preset is a keyframe macro —
// it writes keys and is not stored — so there is no current preset to highlight,
// and a button that lit up would be lying the moment somebody dragged one of the
// keys it wrote. Read the header of `preset_util.js` for why that trade is worth
// making. The buttons are ACTIONS, not a radio group, and they are written as
// buttons so they read as ones.

/**
 * @param categories  [{ id, label, note }] — the shelves, in order.
 * @param presets     [{ id, label, category, hint }] — filed by `category`.
 * @param onPick      (id) => void
 * @param onRemove    optional (id) => void. An entry marked `removable` gets a
 *                    small ✕ beside it; everything else is untouched.
 */
export default function PresetPicker({ categories, presets, onPick, onRemove }) {
  // ⚠ FILED HERE RATHER THAN IN THE TABLE, so a preset whose category names no
  // shelf still appears — under "Other" — instead of vanishing. Same rule
  // `fx_library.js` keeps about its folders and the timeline keeps about an
  // unfiled transition: something nobody filed should be visible and ugly, never
  // invisible. A silently missing preset is a bug nobody can see.
  const shelves = categories.map((c) => ({ ...c, items: [] }));
  const other = { id: "other", label: "Other", note: "Not filed anywhere yet", items: [] };
  const byId = new Map(shelves.map((s) => [s.id, s]));
  for (const preset of presets) {
    (byId.get(preset.category) || other).items.push(preset);
  }
  if (other.items.length) shelves.push(other);

  return (
    <span className="an-preset-picker">
      {shelves
        .filter((shelf) => shelf.items.length)
        .map((shelf) => (
          <span className="an-preset-group" key={shelf.id}>
            <span className="an-preset-cap" title={shelf.note}>
              {shelf.label}
            </span>
            <span className="an-tp-group">
              {shelf.items.map((preset) =>
                // ⚠ A REMOVABLE ENTRY IS TWO BUTTONS, NOT ONE BUTTON WITH A
                // NESTED ONE. A `<button>` inside a `<button>` is invalid HTML
                // and the browsers disagree about which one a click reaches —
                // which for a delete is the worst possible thing to be unsure
                // about. They sit side by side and read as one chip.
                onRemove && preset.removable ? (
                  <span className="an-preset-pair" key={preset.id}>
                    <button
                      type="button"
                      className="an-tp-btn"
                      title={preset.hint}
                      onClick={() => onPick(preset.id)}
                    >
                      {preset.label}
                    </button>
                    <button
                      type="button"
                      className="an-tp-btn an-preset-x"
                      title={`Forget “${preset.label}”`}
                      aria-label={`Forget ${preset.label}`}
                      onClick={() => onRemove(preset.id)}
                    >
                      ✕
                    </button>
                  </span>
                ) : (
                  <button
                    key={preset.id}
                    type="button"
                    className="an-tp-btn"
                    // ⚠ THE WHOLE EXPLANATION LIVES IN THE TOOLTIP, and the
                    // button carries three or four words. House rule, and the
                    // reason a shelf of forty is still readable.
                    title={preset.hint}
                    onClick={() => onPick(preset.id)}
                  >
                    {preset.label}
                  </button>
                )
              )}
            </span>
          </span>
        ))}
    </span>
  );
}
