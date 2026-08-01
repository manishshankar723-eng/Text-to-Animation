// The dialogue of ONE shot, editable — the review step's counterpart to
// DialogueBox. The breakdown fills this in from the script; the writer fixes
// the wording, re-attributes a line, or adds one the model missed.
//
// A shot with nothing spoken in it shows no list and no heading — only the
// quiet "＋ Add dialogue" link, so a silent shot's card stays as short as it
// was before dialogue existed. That is also why removing the last line leaves
// the block empty rather than keeping a blank row around.
export default function DialogueEditor({ dialogue, characters = [], onChange }) {
  const lines = dialogue || [];

  const update = (i, patch) =>
    onChange(lines.map((d, idx) => (idx === i ? { ...d, ...patch } : d)));
  const remove = (i) => onChange(lines.filter((_, idx) => idx !== i));
  // A new line is attributed to the shot's first character when there is one —
  // usually right, always one field less to fill in.
  const add = () =>
    onChange([...lines, { character: characters[0] || "", line: "" }]);

  if (!lines.length) {
    return (
      <button type="button" className="dialogue-add empty" onClick={add}>
        ＋ Add dialogue
      </button>
    );
  }

  return (
    <div className="dialogue-edit">
      <label className="shot-prompt-label">Dialogue</label>
      {lines.map((d, i) => (
        <div className="dialogue-row" key={i}>
          <input
            className="dialogue-who-input"
            value={d.character || ""}
            placeholder="Who"
            list="sb-cast-names"
            onChange={(e) => update(i, { character: e.target.value })}
          />
          <input
            className="dialogue-line-input"
            value={d.line || ""}
            placeholder="What they say…"
            onChange={(e) => update(i, { line: e.target.value })}
          />
          <button
            type="button"
            className="shot-btn danger"
            onClick={() => remove(i)}
            title="Remove this line"
          >
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="dialogue-add" onClick={add}>
        ＋ Add a line
      </button>
    </div>
  );
}
