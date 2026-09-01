// Final confirmation before panel generation starts.
//
// Generation used to begin the moment the user reached the board — the first
// they saw of it was images already being drawn. This modal is the deliberate
// stop in between: it shows exactly what is about to be sent (every shot prompt
// with its cast, props, backgrounds, camera and location) and lets the settings
// that shape the whole board — style, genre and ASPECT RATIO — be changed here,
// where changing them is free. Nothing is generated until "Generate" is pressed.
import ScriptLineBox from "./ScriptLineBox.jsx";
import WorldSetting from "./WorldSetting.jsx";

export default function PreflightModal({
  title,
  shots,
  cast,
  assets,
  // True when the chosen style draws straight from the prompts (Rough Sketch),
  // so the cast + props steps were skipped and no references will be sent.
  refsSkipped,
  charRefs,
  assetRefs,
  savedCast,
  savedAssets,
  styleOptions,
  aspectOptions,
  genreOptions,
  style,
  customStyle,
  onStyle,
  onCustomStyle,
  aspect,
  customAspect,
  onAspect,
  onCustomAspect,
  genre,
  customGenre,
  onGenre,
  onCustomGenre,
  world,
  onWorld,
  busy,
  error,
  onEditShot,
  onCancel,
  onConfirm,
}) {
  const count = shots.length;
  const key = (n) => (n || "").trim().toLowerCase();
  // Category per asset name, so a shot's asset chips can be coloured prop vs.
  // background the same way the props step colours them.
  const categoryByName = new Map(
    assets.map((a) => [key(a.name), a.category === "background" ? "background" : "prop"])
  );

  // One reference row: thumbnail (when we have one) + whether a locked
  // reference is actually going to be sent with every panel this name appears in.
  function refRow(name, kind, saved, refs) {
    const s = saved[key(name)] || {};
    const locked = Boolean(refs[name] || s.referenceId);
    return (
      <div className="pf-ref" key={`${kind}:${name}`}>
        <div className="pf-ref-thumb">
          {s.previewUrl ? (
            <img src={s.previewUrl} alt={name} />
          ) : (
            <span>{(name || "?").trim().charAt(0).toUpperCase()}</span>
          )}
        </div>
        <div className="pf-ref-body">
          <span className="pf-ref-name">{name}</span>
          <span className={`pf-ref-state ${locked ? "ok" : ""}`}>
            {locked ? "✓ Reference locked" : "No reference — drawn from the prompt"}
          </span>
        </div>
        {kind !== "character" && (
          <span className={`asset-badge asset-badge-${kind}`}>
            {kind === "background" ? "Background" : "Prop"}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="modal-overlay">
      <div
        className="pf-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Check everything before generating"
      >
        <button
          className="modal-close"
          onClick={onCancel}
          disabled={busy}
          aria-label="Close"
        >
          ✕
        </button>

        <div className="pf-head">
          <span className="pf-eyebrow">Last stop before we draw</span>
          <h2 className="pf-title">{title || "Your storyboard"}</h2>
          <p className="muted pf-sub">
            This draws <strong>{count}</strong> panel{count === 1 ? "" : "s"} — one
            image per shot. Check everything below: changing it here costs
            nothing, re-drawing afterwards costs another generation.
          </p>
        </div>

        <div className="pf-body">
          <section className="pf-section">
            <h3 className="pf-h3">Settings</h3>
            <div className="pf-settings">
              <label className="pf-field">
                <span className="pf-label">Style</span>
                <select
                  className="board-style-select"
                  value={style}
                  onChange={(e) => onStyle(e.target.value)}
                >
                  {styleOptions.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="pf-field">
                <span className="pf-label">Genre</span>
                <select
                  className="board-style-select"
                  value={genre}
                  onChange={(e) => onGenre(e.target.value)}
                >
                  {genreOptions.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {style === "custom" && (
              <input
                className="custom-genre-input"
                value={customStyle}
                placeholder="Describe your own style, e.g. 1980s retro anime, ink wash…"
                onChange={(e) => onCustomStyle(e.target.value)}
              />
            )}
            {genre === "custom" && (
              <input
                className="custom-genre-input"
                value={customGenre}
                placeholder="Type your own genre…"
                onChange={(e) => onCustomGenre(e.target.value)}
              />
            )}

            <span className="pf-label pf-label-block">Aspect ratio</span>
            <div className="opt-chips">
              {aspectOptions.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className={`opt-chip ${aspect === a.id ? "active" : ""}`}
                  onClick={() => onAspect(a.id)}
                  title={a.note}
                >
                  {a.id}
                  <span className="opt-chip-note">{a.note}</span>
                </button>
              ))}
              <button
                type="button"
                className={`opt-chip ${aspect === "custom" ? "active" : ""}`}
                onClick={() => onAspect("custom")}
              >
                ＋ Custom
              </button>
            </div>
            {aspect === "custom" && (
              <input
                className="custom-genre-input"
                value={customAspect}
                placeholder="Type a ratio, e.g. 4:3, 5:4, 1.85:1…"
                onChange={(e) => onCustomAspect(e.target.value)}
              />
            )}
            <p className="tiny muted pf-note">
              The frame applies to every panel and is baked in when the images are
              drawn — it can't be changed on the finished board.
            </p>
          </section>

          <section className="pf-section">
            <h3 className="pf-h3">World of your story</h3>
            <WorldSetting world={world} onChange={onWorld} collapsible />
          </section>

          {refsSkipped ? (
            <section className="pf-section">
              <h3 className="pf-h3">Cast, props &amp; backgrounds</h3>
              <p className="tiny muted pf-note">
                Not used by this style — a rough thumbnail has no rendered faces
                or sets to keep consistent, so panels are drawn straight from the
                prompts below. That's {cast.length + assets.length} reference
                image{cast.length + assets.length === 1 ? "" : "s"} you don't
                have to generate. Switch to another style above if you want
                locked characters and locations.
              </p>
            </section>
          ) : (cast.length > 0 || assets.length > 0) && (
            <section className="pf-section">
              <h3 className="pf-h3">
                Cast, props &amp; backgrounds ({cast.length + assets.length})
              </h3>
              <div className="pf-refs">
                {cast.map((c) => refRow(c.name, "character", savedCast, charRefs))}
                {assets.map((a) =>
                  refRow(
                    a.name,
                    a.category === "background" ? "background" : "prop",
                    savedAssets,
                    assetRefs
                  )
                )}
              </div>
            </section>
          )}

          <section className="pf-section">
            <h3 className="pf-h3">Prompts ({count})</h3>
            <div className="pf-shots">
              {shots.map((sh, i) => (
                <div className="pf-shot" key={i}>
                  <span className="pf-shotnum">
                    Shot {i + 1}
                    {sh.scene_number ? (
                      <span className="pf-scene">Scene {sh.scene_number}</span>
                    ) : null}
                  </span>
                  <ScriptLineBox shot={sh} />
                  <textarea
                    className="pf-shot-desc"
                    value={sh.description || ""}
                    rows={2}
                    placeholder="Describe what we see in this panel…"
                    onChange={(e) => onEditShot(i, { description: e.target.value })}
                  />
                  {(sh.camera || sh.location) && (
                    <div className="pf-meta">
                      {sh.camera && (
                        <span>
                          <b>Camera</b> {sh.camera}
                        </span>
                      )}
                      {sh.location && (
                        <span>
                          <b>Location</b> {sh.location}
                        </span>
                      )}
                    </div>
                  )}
                  {((sh.characters || []).length > 0 || (sh.assets || []).length > 0) && (
                    <div className="pf-shot-chips">
                      {(sh.characters || []).map((n) => (
                        <span className="chip" key={`c:${n}`}>
                          {n}
                        </span>
                      ))}
                      {(sh.assets || []).map((n) => (
                        <span
                          className={`asset-badge asset-badge-${
                            categoryByName.get(key(n)) || "prop"
                          }`}
                          key={`a:${n}`}
                        >
                          {n}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>

        {error && <div className="error pf-error">{error}</div>}

        <div className="pf-foot">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            ← Back to shots
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={onConfirm}
            disabled={busy || count === 0}
          >
            {busy ? (
              <>
                <span className="spinner-inline" /> Starting…
              </>
            ) : (
              `🎬 Generate my storyboard (${count})`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
