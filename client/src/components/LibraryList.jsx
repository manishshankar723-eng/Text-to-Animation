// LibraryList.jsx — the ONE-LINE ("long row") list every project library is
// drawn with, plus the Filter box that sits above it.
//
// WHY IT EXISTS. Every library used to draw a grid of big cards and then repeat
// the whole thing twice — "Recent X" showing the newest one, "All X" showing
// the same list again underneath. Four workflows, two identical galleries each,
// and the newest project appeared on screen twice. The owner asked for the
// shape a file browser uses instead: one row per project, a small thumbnail on
// the left, the name beside it, and a Filter box for when there are more
// projects than fit on a screen.
//
// ⚠ ONE SECTION NOW, NOT TWO. The "All …" section is gone on purpose — the
// single "Recent …" heading lists EVERYTHING, newest first. Don't reintroduce a
// second section: that is the duplication this replaced.
//
// ⚠ THE ROW MARKUP LIVES HERE, NOT IN EACH LIBRARY. The four libraries
// (storyboards, animatics, final videos, plans) each used to own a private copy
// of the card markup with a comment asking the next person to keep them in
// sync, and they had already drifted. A library now supplies only what is
// DIFFERENT — its chips, its icon buttons, its empty note — and this file owns
// everything that must look the same.
//
// The look lives in the `.lib-list*` / `.lib-row*` rules in
// `styles/storyboard-library.css`; the older `.lib-card` rules are still there
// for the "New …" tiles, which are unchanged.

// The long edge a row thumbnail asks the server for.
//
// ⚠ ASK FOR A SIZE, NOT FOR THE PICTURE. A drawn storyboard panel is ~3.5 MB
// and this draws it 72px wide, so a page of ten boards was pulling ~35 MB down
// the wire to fill ten postage stamps. 480 is the smallest rung on the proxy
// ladder (see `PROXY_EDGES` in proxies.py) — asking for anything smaller lands
// on the same rung, and asking for more only costs bytes nobody can see. The
// server falls back to the source for any picture it cannot proxy, so this is
// only ever a hint.
export const THUMB_EDGE = 480;

// Does an item survive the filter box? Case-insensitive "contains" over
// whatever fields the caller thinks a user would type — the title, always, plus
// things like a genre or an aspect ratio that read as part of the name on
// screen. An empty box matches everything, which is what makes the filter safe
// to leave mounted at all times.
export function matchesFilter(query, ...fields) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return true;
  return fields.some((f) => f != null && String(f).toLowerCase().includes(q));
}

/**
 * A project's aspect ratio ("9:16") as the thumbnail's CSS shape.
 *
 * ⚠ THIS IS WHY A PORTRAIT PROJECT'S PICTURE IS NOT CROPPED ANY MORE. The
 * thumbnail used to be a fixed 16:9 box with `object-fit: cover` on the image
 * inside it, so a 9:16 board was shown as a thin horizontal slice out of the
 * middle of a tall picture — reported as "iamge full nhi dikh rah ahai". The
 * SLOT stays 72×40 whatever the shape, so every project name still starts at
 * the same x; the PICTURE inside it takes the project's own ratio and simply
 * gets narrower. A 9:16 board is drawn 22×40 and you can see all of it.
 *
 * Returns undefined for a missing or unparseable value, which leaves the CSS
 * default (16:9) in place.
 */
export function aspectStyle(aspect) {
  const m = /^\s*(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)\s*$/.exec(aspect || "");
  if (!m) return undefined;
  const w = Number(m[1]);
  const h = Number(m[2]);
  if (!(w > 0) || !(h > 0)) return undefined;
  return { "--lib-thumb-ar": `${w} / ${h}` };
}

/**
 * Bytes as the Size column reads them — "453 KB", "1.2 GB".
 *
 * ⚠ NOTHING IS "0 B". A project with no files yet and a project whose folder
 * has not been created are the same news to someone reading a library, and
 * "0 B" invites the question "did it lose my work?" — so both are an em dash.
 */
export function formatBytes(n) {
  const bytes = Number(n) || 0;
  if (bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // A decimal only where it carries information: "1.2 GB" is worth saying,
  // "453.7 KB" is noise in a column you scan rather than read.
  return `${value.toFixed(value < 10 && unit > 1 ? 1 : 0)} ${units[unit]}`;
}

/**
 * One project, drawn as a row.
 *
 * `cover` is the thumbnail's CONTENT (an <img>, a placeholder glyph, a spinner)
 * and `badges` whatever sits on top of it — a library knows what "still
 * working" looks like for its own jobs, this file does not.
 *
 * `below` is the full-width strip under the row: the share link, or the delete
 * confirmation. It is outside the grid on purpose, so a confirmation can use
 * the whole width instead of being squeezed into the actions column.
 */
export function LibraryRow({
  cover,
  badges,
  // The project's aspect ratio ("16:9", "9:16"). Shapes the thumbnail so a
  // portrait project is shown whole rather than cropped — see `aspectStyle`.
  aspect,
  name,
  meta,
  date,
  // Bytes on disk. Only drawn where the section asked for a Size column; see
  // `sizeLabel` on LibrarySection.
  size,
  actions,
  below,
  onOpen,
  openTitle = "Open this project",
}) {
  return (
    <div className="lib-row">
      <div className="lib-row-main">
        <div className="lib-cell lib-cell-name">
          {/* Two boxes, not one: the outer SLOT is a fixed 72×40 whatever shape
              the project is, so every name in the column starts at the same x;
              the inner PICTURE takes the project's own ratio. */}
          <div className="lib-thumb" onClick={onOpen} title={openTitle}>
            <div className="lib-thumb-pic" style={aspectStyle(aspect)}>
              {cover}
              {badges}
            </div>
          </div>
          {/* The name is the caller's node, not a string: while a project is
              being renamed this is an <input> instead of a label, and the row
              must not jump when it swaps. */}
          <div className="lib-name">{name}</div>
        </div>
        <div className="lib-cell lib-cell-meta">{meta}</div>
        <div className="lib-cell lib-cell-date">
          <span className="tiny muted">{date}</span>
        </div>
        <div className="lib-cell lib-cell-size">
          <span className="tiny muted">{formatBytes(size)}</span>
        </div>
        <div className="lib-cell lib-cell-actions">{actions}</div>
      </div>
      {below ? <div className="lib-row-below">{below}</div> : null}
    </div>
  );
}

/**
 * The whole section: heading, Filter box, column headers, rows.
 *
 * ⚠ IT IS A REAL COMPONENT AT MODULE LEVEL, and that matters more than it
 * looks. Each library used to declare its section as a render FUNCTION for
 * exactly this reason — a component declared inside a render gets a new
 * identity every time, React remounts everything under it on each keystroke,
 * and the field you are typing in loses focus. Declared out here it is stable,
 * so the Filter box keeps focus while it filters and a rename input keeps focus
 * while you type a name.
 *
 * `total` is how many projects exist and `shown` how many survived the filter:
 * the two are told apart so "you have nothing yet" and "nothing matches what
 * you typed" can say different things. A user who filters everything away and
 * is told "No projects yet" thinks the app ate their work.
 */
export default function LibrarySection({
  title,
  hint,
  // Omit `onQuery` and no Filter box is drawn — for a library too small to need
  // one. Every workflow currently passes it.
  query = "",
  onQuery,
  placeholder = "Filter by name",
  loading = false,
  ghosts = 5,
  total = 0,
  shown = 0,
  // Column headers. "Name" never changes; the middle and date columns are named
  // by the workflow, because "Created" and "Panels" mean different things in a
  // plan library and a video one.
  metaLabel = "Details",
  dateLabel = "Created",
  // ⚠ OMIT IT AND THERE IS NO SIZE COLUMN AT ALL — the rows still render their
  // size cell, CSS hides it. Plans are the reason: a planning session is rows
  // in a database with no files of its own, so a Size column there would be a
  // column of em dashes pretending to be information.
  sizeLabel,
  emptyIcon = "🎬",
  emptyText,
  children,
}) {
  return (
    <section className="lib-section">
      <div className="lib-section-head">
        <h2 className="lib-section-title">{title}</h2>
        <div className="lib-head-tools">
          {hint ? <span className="tiny muted">{hint}</span> : null}
          {onQuery ? (
            <label className="lib-filter">
              <span className="lib-filter-label">Filter</span>
              {/* type="text", NOT type="search": a native search input draws its
                  own ✕ in WebKit, which would sit beside ours and clear the box
                  without React hearing about it. */}
              <input
                type="text"
                className="lib-filter-input"
                value={query}
                placeholder={placeholder}
                onChange={(e) => onQuery(e.target.value)}
              />
              {query ? (
                <button
                  type="button"
                  className="lib-filter-clear"
                  title="Clear the filter"
                  onClick={() => onQuery("")}
                >
                  ✕
                </button>
              ) : null}
            </label>
          ) : null}
        </div>
      </div>

      <div className={`lib-list ${sizeLabel ? "has-size" : ""}`}>
        <div className="lib-list-head" aria-hidden="true">
          <span className="lib-cell-name">Name</span>
          <span className="lib-cell-meta">{metaLabel}</span>
          <span className="lib-cell-date">{dateLabel}</span>
          <span className="lib-cell-size">{sizeLabel}</span>
          <span className="lib-cell-actions">Actions</span>
        </div>

        {loading ? (
          // Shimmering rows shaped like real ones, so the page reads as a list
          // waiting to be filled rather than as bare text.
          <div className="lib-ghosts is-loading">
            {Array.from({ length: ghosts }, (_, i) => (
              <div className="lib-row lib-ghost-row" key={i} aria-hidden="true">
                <div className="lib-row-main">
                  <div className="lib-cell lib-cell-name">
                    <div className="lib-thumb">
                      <div className="lib-thumb-pic lib-ghost-cover" />
                    </div>
                    <div className="lib-ghost-line lib-ghost-title" />
                  </div>
                  <div className="lib-cell lib-cell-meta">
                    <span className="lib-ghost-chip" />
                    <span className="lib-ghost-chip" />
                  </div>
                  <div className="lib-cell lib-cell-date">
                    <span className="lib-ghost-line" />
                  </div>
                  <div className="lib-cell lib-cell-size">
                    <span className="lib-ghost-line" />
                  </div>
                  <div className="lib-cell lib-cell-actions" />
                </div>
              </div>
            ))}
          </div>
        ) : total === 0 ? (
          <div className="lib-list-empty">
            <span className="lib-empty-ico">{emptyIcon}</span>
            <p className="lib-empty-text">{emptyText}</p>
          </div>
        ) : shown === 0 ? (
          // Filtered down to nothing. Says so in the user's own words and hands
          // back the way out, rather than looking like an empty account.
          <div className="lib-list-empty">
            <span className="lib-empty-ico">🔍</span>
            <p className="lib-empty-text">
              Nothing here matches <strong>“{query}”</strong>.{" "}
              <button
                type="button"
                className="lib-linkish"
                onClick={() => onQuery("")}
              >
                Clear the filter
              </button>{" "}
              to see all {total}.
            </p>
          </div>
        ) : (
          children
        )}
      </div>
    </section>
  );
}
