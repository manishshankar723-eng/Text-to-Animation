// The story's WORLD — region, period, culture, and what its people look like.
//
// Image models default to Western/European faces, dress and architecture unless
// told otherwise, which is how an ancient-Indian hunter came back looking white.
// The breakdown reads this off the script and it is prefixed onto every image
// prompt — cast references, prop and background references, and every panel.
//
// It is EDITABLE here on purpose: the AI's reading of the script is a starting
// point, and the person who wrote the script is the authority on its world.
// Anything typed here reaches the next generation; images already drawn keep
// whatever world they were drawn with until they are regenerated.
const FIELDS = [
  {
    key: "ethnicity",
    label: "The people look like",
    placeholder: "e.g. South Asian (Indian) — warm brown skin, black hair, dark eyes",
    wide: true,
  },
  {
    key: "setting",
    label: "Setting & period",
    placeholder: "e.g. Ancient India, Puranic era — forest, village, stone temple",
  },
  {
    key: "culture",
    label: "Culture / tradition",
    placeholder: "e.g. Hindu (Shaivite) mythology, Shiva Purana",
  },
  {
    key: "wardrobe",
    label: "Clothing",
    placeholder: "e.g. handwoven dhoti, angavastram, rudraksha beads",
  },
  {
    key: "environment",
    label: "Architecture & objects",
    placeholder: "e.g. thatched huts, stone shrines, clay pots, bamboo bows",
  },
  {
    key: "notes",
    label: "Other visual detail",
    placeholder: "e.g. bilva leaves, Shiva linga, oil lamps",
    wide: true,
  },
];

export default function WorldSetting({ world, onChange, collapsible = false }) {
  const w = world || {};
  const filled = FIELDS.filter((f) => (w[f.key] || "").trim()).length;

  const body = (
    <>
      <p className="tiny muted world-note">
        Read from your script and used in <strong>every</strong> image — cast,
        props, backgrounds and panels. Edit anything that isn't right; it applies
        to whatever you generate next.
      </p>
      <div className="world-grid">
        {FIELDS.map((f) => (
          <label className={`world-field ${f.wide ? "wide" : ""}`} key={f.key}>
            <span className="world-label">{f.label}</span>
            <input
              value={w[f.key] || ""}
              placeholder={f.placeholder}
              onChange={(e) => onChange({ ...w, [f.key]: e.target.value })}
            />
          </label>
        ))}
      </div>
    </>
  );

  if (!collapsible) {
    return (
      <div className="card world-card">
        <div className="world-head">
          <h3 className="world-title">🌍 World of your story</h3>
          <span className="world-count">
            {filled === 0 ? "not detected — worth filling in" : `${filled}/${FIELDS.length} set`}
          </span>
        </div>
        {body}
      </div>
    );
  }

  return (
    <details className="world-details">
      <summary>
        🌍 World of your story
        <span className="world-count">
          {filled === 0 ? "not detected" : `${filled}/${FIELDS.length} set`}
        </span>
      </summary>
      {body}
    </details>
  );
}
