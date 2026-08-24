// ViewAsUser.jsx — the app as this customer sees it.
//
// ⚠ IT INVENTS NO RULES. Every state on this screen comes from
// `features.resolve(email)` on the server — the SAME function that decides what
// the real sidebar draws and what every `require_feature` guard allows. A
// preview built from its own copy of the logic would eventually disagree with
// the app, and would then be worse than nothing: a support tool that lies.
//
// ⚠ AND IT IS READ-ONLY. This is not impersonation — no token is issued, no
// request is made as them, and nothing here can act on their behalf. It answers
// "what does this person see", which is the question a support ticket actually
// asks.
import { accessReason } from "./format.js";

export default function ViewAsUser({ detail, onClose }) {
  const meta = detail.feature_meta || [];
  const states = detail.feature_states || {};

  const workflows = meta.filter((f) => f.group === "workflow");
  const capabilities = meta.filter((f) => f.group === "capability");

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="card view-as"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`What ${detail.user.email} sees`}
      >
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ✕
        </button>

        <h2 className="admin-h2">What {detail.user.email} sees</h2>
        <p className="muted tiny">
          On {detail.tier_name || detail.tier}. Read-only — nothing here signs in
          as them or acts on their behalf.
        </p>

        <div className="view-as-cols">
          <div>
            <h3 className="admin-h3">Their sidebar</h3>
            {/* Drawn to look like the real rail, because "is the row there?" is
                the question — a table of booleans answers it less well than a
                picture of the thing being asked about. */}
            <div className="view-as-rail">
              {workflows.map((f) => {
                const st = states[f.key] || {};
                if (!st.visible) return null;
                return (
                  <div
                    className={`view-as-row ${st.on ? "" : "off"}`}
                    key={f.key}
                    title={accessReason(st)}
                  >
                    <span className="view-as-ico">{f.icon}</span>
                    <span className="view-as-label">{f.label}</span>
                    {st.status === "soon" && <span className="sb-badge-soon">Soon</span>}
                    {st.source === "tier" && (
                      <span className="view-as-lock" title={`Needs ${st.min_tier}`}>
                        🔒
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
            {/* ⚠ WHAT IS MISSING IS AS INFORMATIVE AS WHAT IS THERE, so the
                hidden rows are listed rather than silently absent — otherwise
                "why can't they see X" is answered by a blank space. */}
            {workflows.some((f) => !(states[f.key] || {}).visible) && (
              <p className="muted tiny view-as-hidden">
                Not shown at all:{" "}
                {workflows
                  .filter((f) => !(states[f.key] || {}).visible)
                  .map((f) => f.label)
                  .join(", ")}
              </p>
            )}
          </div>

          <div>
            <h3 className="admin-h3">What they can do</h3>
            <ul className="admin-access">
              {capabilities.map((f) => {
                const st = states[f.key] || {};
                return (
                  <li className={`admin-access-row ${st.on ? "on" : "off"}`} key={f.key}>
                    <span className="admin-access-ico">{st.on ? "✓" : "✕"}</span>
                    <span className="admin-access-text">
                      <span className="admin-access-label">{f.label}</span>
                      <span className="muted tiny">{accessReason(st)}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
