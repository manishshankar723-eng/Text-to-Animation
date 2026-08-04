// The script the user pasted or uploaded, shown in full on the review step.
//
// Line-numbered on purpose: each shot card says "FROM YOUR SCRIPT · LINE 12",
// and this is where that number can be looked up. Read-only — the script is the
// source, and editing it here would not re-run the breakdown.
// `defaultOpen` is false on the finished board: the board is about the panels,
// so the script rides along collapsed and opens on demand.
export default function ScriptPanel({ script, defaultOpen = true }) {
  const text = (script || "").replace(/\s+$/, "");
  if (!text.trim()) return null;

  const lines = text.split("\n");
  const words = text.trim().split(/\s+/).length;

  return (
    <details className="card script-panel" open={defaultOpen}>
      <summary className="script-panel-head">
        <span className="script-panel-title">📄 Your script</span>
        <span className="script-panel-meta">
          {lines.length} line{lines.length === 1 ? "" : "s"} · {words} word
          {words === 1 ? "" : "s"}
        </span>
      </summary>
      <div className="script-panel-body">
        <ol className="script-panel-lines">
          {lines.map((line, i) => (
            <li key={i}>
              <span className="spl-num">{i + 1}</span>
              {/* nbsp keeps a blank line's row height so numbering stays readable */}
              <span className="spl-text">{line || " "}</span>
            </li>
          ))}
        </ol>
      </div>
    </details>
  );
}
