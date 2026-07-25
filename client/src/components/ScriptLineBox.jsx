// The script lines a shot was drawn from, shown read-only above its image
// prompt so the writer can see exactly which part of THEIR script became this
// panel — the prompt is the AI's words, this is theirs.
//
// Renders nothing when the shot has no verified script text: the breakdown
// matches the model's quote back against the real script and leaves this blank
// rather than showing a line the script doesn't contain. Hand-added shots have
// no source line either, and correctly show no box.
export default function ScriptLineBox({ shot }) {
  const text = (shot?.script_line || "").trim();
  if (!text) return null;

  const from = shot.script_line_start;
  const to = shot.script_line_end;
  const where = !from
    ? ""
    : from === to
      ? ` · line ${from}`
      : ` · lines ${from}–${to}`;

  return (
    <div className="script-line">
      <span className="script-line-tag">📄 From your script{where}</span>
      <p className="script-line-text">{text}</p>
    </div>
  );
}
