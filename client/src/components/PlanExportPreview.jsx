import Icon from "./Icon.jsx";

// PlanExportPreview — see the file BEFORE downloading it.
//
// Clicking XLSX / DOCX / CSV used to download immediately, so the only way to
// check the export was to open it in Excel or Word. This shows what each format
// will actually contain, laid out the way that format lays it out, and puts the
// download behind a button you press once you're happy.
//
// The three previews differ on purpose, because the FILES differ:
//   xlsx — two sheets (Calendar + Strategy), so a sheet switcher.
//   csv  — the calendar only. The strategy, pillars and assumptions are NOT in
//          a CSV, and the preview says so rather than letting you find out.
//   docx — a document: headings and fields per upload, not a grid.

// MUST match plan_export.COLUMNS in the backend, in the same order.
// tests/plan_export_columns_check.py asserts these two lists are identical, so
// this comment is enforced rather than hopeful.
export const EXPORT_COLUMNS = [
  ["slot", "When"],
  ["title", "Title"],
  ["hook", "Hook (first 3 seconds)"],
  ["format", "Format"],
  ["pillar", "Pillar"],
  ["outline", "Outline"],
  ["keywords", "Keywords"],
  ["cta", "Call to action"],
  ["goal", "Goal"],
  ["effort", "Effort"],
];

const LABEL = {
  xlsx: "Excel workbook",
  docx: "Word document",
  csv: "CSV file",
};

// Same flattening the Python exporters do, so the preview shows the real cell
// contents rather than a prettier version of them.
function cell(item, key) {
  const v = item?.[key];
  if (Array.isArray(v)) {
    return key === "outline"
      ? v.map((x, i) => `${i + 1}. ${x}`).join("\n")
      : v.join(", ");
  }
  return v == null ? "" : String(v);
}

export default function PlanExportPreview({
  format,
  plan,
  title,
  sheet,
  onSheet,
  onClose,
  onDownload,
  downloading,
}) {
  if (!format) return null;
  const items = plan?.items || [];
  const pillars = plan?.pillars || [];
  const assumptions = plan?.assumptions || [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="card export-modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} title="Close" aria-label="Close">
          <Icon name="close" />
        </button>

        <header className="export-modal-head">
          <h2>{LABEL[format]} preview</h2>
          <p className="muted tiny">
            {title || "Content plan"} · {items.length} upload
            {items.length === 1 ? "" : "s"}
            {format === "csv" && " · calendar only"}
          </p>
        </header>

        {/* xlsx has two sheets — let them see both before committing. */}
        {format === "xlsx" && (
          <div className="export-sheets">
            {["Calendar", "Strategy"].map((s) => (
              <button
                key={s}
                className={`btn small ${sheet === s ? "primary" : ""}`}
                onClick={() => onSheet(s)}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        <div className="export-modal-body">
          {/* ---- Spreadsheet-style grid (xlsx Calendar sheet, and csv) ---- */}
          {(format === "csv" || (format === "xlsx" && sheet === "Calendar")) && (
            <div className="export-table-wrap">
              <table className="export-table">
                <thead>
                  <tr>
                    <th className="export-rownum" />
                    {EXPORT_COLUMNS.map(([, label]) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr key={i}>
                      <td className="export-rownum">{i + 1}</td>
                      {EXPORT_COLUMNS.map(([key]) => (
                        <td key={key}>{cell(it, key)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* ---- The xlsx Strategy sheet ---- */}
          {format === "xlsx" && sheet === "Strategy" && (
            <div className="export-table-wrap">
              <table className="export-table export-table-kv">
                <tbody>
                  <tr>
                    <th>Plan</th>
                    <td>{title || "Content plan"}</td>
                  </tr>
                  <tr>
                    <th>Covers</th>
                    <td>{plan?.months} month(s)</td>
                  </tr>
                  <tr>
                    <th>Cadence</th>
                    <td>{plan?.cadence}</td>
                  </tr>
                  <tr>
                    <th>Uploads</th>
                    <td>{items.length}</td>
                  </tr>
                  {plan?.summary && (
                    <tr>
                      <th>Strategy</th>
                      <td>{plan.summary}</td>
                    </tr>
                  )}
                  {pillars.map((p) => (
                    <tr key={p.name}>
                      <th>{p.name}</th>
                      <td>{p.why}</td>
                    </tr>
                  ))}
                  {assumptions.map((a, i) => (
                    <tr key={`a${i}`}>
                      <th>{i === 0 ? "Assumptions" : ""}</th>
                      <td>{a}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* ---- Document layout ---- */}
          {format === "docx" && (
            // The scroller is full width; the page inside it caps the line
            // length. Capping the scroller instead put its scrollbar in the
            // middle of the panel.
            <div className="export-doc">
              <div className="export-doc-page">
              <h1>{title || "Content plan"}</h1>
              <p className="muted">
                {plan?.months} month(s) · {plan?.cadence} · {items.length} uploads
              </p>

              {plan?.summary && (
                <>
                  <h2>Strategy</h2>
                  <p>{plan.summary}</p>
                </>
              )}

              {pillars.length > 0 && (
                <>
                  <h2>Content pillars</h2>
                  <ul>
                    {pillars.map((p) => (
                      <li key={p.name}>
                        <strong>{p.name}</strong>
                        {p.why ? ` — ${p.why}` : ""}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              <h2>Calendar</h2>
              {items.map((it, i) => (
                <div className="export-doc-item" key={i}>
                  <h3>
                    {i + 1}. {it.title}
                  </h3>
                  {it.slot && <p className="export-doc-slot">{it.slot}</p>}
                  {it.hook && (
                    <p>
                      <strong>Hook:</strong> {it.hook}
                    </p>
                  )}
                  {it.format && (
                    <p>
                      <strong>Format:</strong> {it.format}
                    </p>
                  )}
                  {it.pillar && (
                    <p>
                      <strong>Pillar:</strong> {it.pillar}
                    </p>
                  )}
                  {it.outline?.length > 0 && (
                    <>
                      <p>
                        <strong>Outline:</strong>
                      </p>
                      <ol>
                        {it.outline.map((b, j) => (
                          <li key={j}>{b}</li>
                        ))}
                      </ol>
                    </>
                  )}
                  {it.keywords?.length > 0 && (
                    <p>
                      <strong>Keywords:</strong> {it.keywords.join(", ")}
                    </p>
                  )}
                  {it.cta && (
                    <p>
                      <strong>Call to action:</strong> {it.cta}
                    </p>
                  )}
                  {(it.goal || it.effort) && (
                    <p>
                      <strong>Goal / effort:</strong>{" "}
                      {[it.goal, it.effort].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
              ))}

              {assumptions.length > 0 && (
                <>
                  <h2>Assumptions</h2>
                  <ul>
                    {assumptions.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
              </div>
            </div>
          )}
        </div>

        <footer className="export-modal-foot">
          {/* Stated plainly: a CSV genuinely drops the strategy, and finding
              that out after importing it into a tool is the annoying way. */}
          <span className="tiny muted">
            {format === "csv"
              ? "CSV holds the calendar only — no strategy, pillars or assumptions."
              : format === "xlsx"
              ? "Two sheets: Calendar and Strategy. Header frozen, filters on."
              : "A document laid out per upload, ready to read or send on."}
          </span>
          <div className="export-modal-actions">
            <button className="btn ghost" onClick={onClose}>
              Cancel
            </button>
            <button className="btn primary" onClick={onDownload} disabled={downloading}>
              {downloading ? (
                <>
                  <span className="spinner-inline" /> Preparing…
                </>
              ) : (
                <>
                  <Icon name="download" /> Download {format.toUpperCase()}
                </>
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
