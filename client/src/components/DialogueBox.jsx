// The lines SPOKEN in a shot, read-only — shown under the panel on the board
// the way a script prints them: the speaker's name above what they say.
//
// Renders NOTHING when the shot has no dialogue. A silent establishing shot or
// an action beat genuinely has nothing to say, and an empty "Dialogue" heading
// on two thirds of a board is noise: the breakdown returns an empty list for
// those shots and every consumer treats that as "draw no dialogue block".
export default function DialogueBox({ dialogue, className = "" }) {
  const lines = (dialogue || []).filter((d) => (d?.line || "").trim());
  if (!lines.length) return null;

  return (
    <div className={`dialogue ${className}`.trim()}>
      {lines.map((d, i) => (
        <p className="dialogue-line" key={i}>
          {(d.character || "").trim() && (
            <span className="dialogue-who">{d.character.trim()}</span>
          )}
          <span className="dialogue-text">{d.line.trim()}</span>
        </p>
      ))}
    </div>
  );
}
