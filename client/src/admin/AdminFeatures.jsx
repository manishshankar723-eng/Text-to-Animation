// AdminFeatures.jsx — the switchboard. Hide it, launch it, stage it, reorder it.
//
// This is the screen the whole phase exists for: before it, hiding a workflow
// meant editing `Sidebar.jsx` and redeploying. Every row here writes to the same
// registry that `features.resolve` reads, so a change lands on the sidebar, on
// `/auth/me/entitlements` and on every `require_feature` guard at once — there
// is no second place to also remember to change.
//
// ⚠ EACH CONTROL SAVES ON CHANGE, ONE FIELD AT A TIME. No Save button, because
// there is nothing to batch: these are switches, and a switch that needs
// confirming is a switch people leave half-thrown. The PATCH sends only the
// field that moved, so two people editing different settings on the same
// feature don't overwrite each other.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import { formatDateTime } from "./format.js";

// What each status MEANS, in the words of what the user experiences — not
// "live/soon/hidden", which describes the database rather than the person.
const STATUS = {
  live: { label: "Live", tone: "ok", says: "Everyone in the rollout can use it." },
  soon: { label: "Soon", tone: "warn", says: "Shown with a badge. The page says coming soon; the routes refuse." },
  hidden: { label: "Hidden", tone: "fail", says: "Gone from the app entirely. Routes refuse for everyone, admins included." },
};

const ROLLOUT = {
  all: "Everyone",
  admins: "Admins only",
  allowlist: "Named people",
  percent: "A percentage",
};

export default function AdminFeatures() {
  const [rows, setRows] = useState([]);
  const [tiers, setTiers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    // Two calls, because the picker offers TIERS and the table lists FEATURES.
    // Fetched together rather than by the row, which would be one request per
    // feature for a list that is the same every time.
    Promise.all([api.adminListFeatures(), api.adminListTiers().catch(() => null)])
      .then(([f, t]) => {
        setRows(f.features || []);
        setTiers(t?.tiers || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  // ⚠ ITS OWN ROUTE, NOT PART OF THE PATCH. `min_tier` is what the pricing
  // screen DERIVES "what's in Pro" from, so it is worth an endpoint that
  // validates the tier exists and records the before/after by name.
  async function saveMinTier(key, tierId) {
    setBusy(key);
    setError("");
    try {
      const saved = await api.adminSetMinTier(key, tierId);
      setRows((list) => list.map((f) => (f.key === saved.key ? saved : f)));
    } catch (e) {
      setError(e.message);
      load();
    } finally {
      setBusy("");
    }
  }

  async function save(key, fields) {
    setBusy(key);
    setError("");
    try {
      const saved = await api.adminUpdateFeature(key, fields);
      // Patch the one row in place rather than reloading the table: a full
      // reload moves focus and scroll, which makes changing three things in a
      // row feel like fighting the page.
      setRows((list) => list.map((f) => (f.key === saved.key ? saved : f)));
    } catch (e) {
      setError(e.message);
      // The control is bound to `rows`, so re-reading is what puts a rejected
      // change back to what the server actually holds.
      load();
    } finally {
      setBusy("");
    }
  }

  if (loading && rows.length === 0) {
    return (
      <div className="admin-body">
        <div className="card admin-card">
          <p className="muted">Loading…</p>
        </div>
      </div>
    );
  }

  const groups = [
    {
      id: "workflow",
      title: "Workflows",
      blurb:
        "The rows in the sidebar. Hiding one takes it out of the rail and refuses the routes behind it; the order here is the order it is drawn in.",
    },
    {
      id: "capability",
      title: "Capabilities",
      blurb:
        "The expensive or optional things inside the workflows. Switching one off leaves the workflow usable and stops that one action.",
    },
  ];

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="info-msg admin-note-box">
        Changes take effect immediately for new requests. A browser already open
        picks them up when the page is next loaded or the user moves between
        workflows.
      </div>

      {groups.map((g) => {
        const items = rows.filter((f) => f.group === g.id);
        if (items.length === 0) return null;
        return (
          <section className="card admin-card" key={g.id}>
            <div className="admin-section-head">
              <div>
                <h2 className="admin-h2">{g.title}</h2>
                <p className="muted tiny admin-group-blurb">{g.blurb}</p>
              </div>
            </div>

            <div className="admin-feature-list">
              {items.map((f, i) => (
                <FeatureRow
                  key={f.key}
                  feature={f}
                  tiers={tiers}
                  onSaveMinTier={saveMinTier}
                  busy={busy === f.key}
                  first={i === 0}
                  last={i === items.length - 1}
                  onSave={save}
                  /* Reordering swaps this row's `order` with its neighbour's,
                     which is why it needs to know who that is. */
                  prev={items[i - 1]}
                  next={items[i + 1]}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function FeatureRow({ feature, tiers, onSaveMinTier, busy, first, last, onSave, prev, next }) {
  const [open, setOpen] = useState(false);
  const rollout = feature.rollout || { mode: "all", emails: [], percent: 100 };
  const status = STATUS[feature.status] || STATUS.live;

  // ⚠ SWAP, DON'T RENUMBER. Writing 0..n across the whole group on every move
  // would be one PATCH per row and would fight anybody else editing the list;
  // exchanging two `order` values is a single change that means the same thing.
  function move(dir) {
    const other = dir < 0 ? prev : next;
    if (!other) return;
    onSave(feature.key, { order: other.order });
    onSave(other.key, { order: feature.order });
  }

  return (
    <div className={`admin-feature ${feature.status}`}>
      <div className="admin-feature-head">
        <span className="admin-feature-ico">{feature.icon}</span>
        <span className="admin-feature-name">
          <span className="admin-feature-label">{feature.label}</span>
          <code className="admin-feature-key">{feature.key}</code>
          {feature.note && <span className="muted tiny">{feature.note}</span>}
        </span>

        <span className="admin-segment admin-status-seg" role="group" aria-label={`${feature.label} status`}>
          {Object.entries(STATUS).map(([id, meta]) => (
            <button
              key={id}
              type="button"
              className={`admin-seg-btn ${feature.status === id ? `on ${meta.tone}` : ""}`}
              disabled={busy}
              title={meta.says}
              onClick={() => feature.status !== id && onSave(feature.key, { status: id })}
            >
              {meta.label}
            </button>
          ))}
        </span>

        <span className="admin-feature-ord">
          <button
            type="button"
            className="admin-ord-btn"
            disabled={first || busy}
            onClick={() => move(-1)}
            title="Move up"
            aria-label="Move up"
          >
            ▲
          </button>
          <button
            type="button"
            className="admin-ord-btn"
            disabled={last || busy}
            onClick={() => move(1)}
            title="Move down"
            aria-label="Move down"
          >
            ▼
          </button>
        </span>

        <button
          type="button"
          className="btn ghost small admin-feature-more"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
        >
          {open ? "Done" : "Who sees it"}
        </button>
      </div>

      <p className="muted tiny admin-feature-says">
        {status.says}
        {rollout.mode !== "all" && ` · ${ROLLOUT[rollout.mode]}`}
        {rollout.mode === "percent" && ` (${rollout.percent}%)`}
        {rollout.mode === "allowlist" && ` (${rollout.emails.length})`}
        {feature.min_tier && ` · needs ${feature.min_tier}`}
        {feature.updated_at && (
          <>
            {" · last changed "}
            {formatDateTime(feature.updated_at)}
            {feature.updated_by && ` by ${feature.updated_by}`}
          </>
        )}
      </p>

      {open && (
        <Rollout
          feature={feature}
          tiers={tiers}
          onSaveMinTier={onSaveMinTier}
          busy={busy}
          onSave={onSave}
        />
      )}
    </div>
  );
}

function Rollout({ feature, tiers, onSaveMinTier, busy, onSave }) {
  const rollout = feature.rollout || { mode: "all", emails: [], percent: 100 };
  // The address list is edited as text and saved on blur — one PATCH when you
  // finish, not one per keystroke.
  const [emails, setEmails] = useState((rollout.emails || []).join("\n"));
  const [percent, setPercent] = useState(rollout.percent ?? 100);

  function saveRollout(patch) {
    onSave(feature.key, {
      rollout: { mode: rollout.mode, emails: rollout.emails, percent: rollout.percent, ...patch },
    });
  }

  return (
    <div className="admin-rollout">
      <label className="admin-rollout-row">
        <span className="muted tiny">Who</span>
        <select
          className="admin-select"
          value={rollout.mode}
          disabled={busy}
          onChange={(e) => saveRollout({ mode: e.target.value })}
        >
          {Object.entries(ROLLOUT).map(([id, label]) => (
            <option value={id} key={id}>
              {label}
            </option>
          ))}
        </select>
      </label>

      {rollout.mode === "allowlist" && (
        <label className="admin-rollout-row wide">
          <span className="muted tiny">Addresses, one per line</span>
          <textarea
            className="admin-note"
            rows={4}
            value={emails}
            disabled={busy}
            onChange={(e) => setEmails(e.target.value)}
            onBlur={() =>
              saveRollout({
                emails: emails
                  .split(/[\n,]/)
                  .map((x) => x.trim())
                  .filter(Boolean),
              })
            }
          />
        </label>
      )}

      {rollout.mode === "percent" && (
        <label className="admin-rollout-row">
          <span className="muted tiny">Percentage of accounts</span>
          <span className="admin-pct">
            <input
              type="range"
              min={0}
              max={100}
              value={percent}
              disabled={busy}
              onChange={(e) => setPercent(Number(e.target.value))}
              onMouseUp={() => saveRollout({ percent })}
              onTouchEnd={() => saveRollout({ percent })}
              onKeyUp={() => saveRollout({ percent })}
            />
            <span className="admin-pct-num">{percent}%</span>
          </span>
        </label>
      )}

      {tiers.length > 0 && (
        <label className="admin-rollout-row">
          <span className="muted tiny">Needs at least</span>
          <select
            className="admin-select"
            value={feature.min_tier || ""}
            disabled={busy}
            onChange={(e) => onSaveMinTier(feature.key, e.target.value)}
          >
            {/* ⚠ "Every tier" IS NOT A TIER. An empty requirement means the
                feature is in everything, including the free one — which is not
                the same as requiring the lowest-ranked tier, because that one
                can later be archived or re-ranked. */}
            <option value="">Every tier (no requirement)</option>
            {tiers
              .filter((t) => !t.archived)
              .map((t) => (
                <option value={t.id} key={t.id}>
                  {t.name}
                </option>
              ))}
          </select>
        </label>
      )}

      <p className="muted tiny admin-rollout-note">
        {/* Stated here rather than buried in a docstring, because it is
            surprising the first time and it is exactly what somebody staging a
            feature needs to know. */}
        Administrators can always use a feature that is <strong>Live</strong>,
        whatever the rollout says — otherwise you couldn't check what you were
        about to launch. <strong>Hidden</strong> is the one that applies to
        everyone; to look at something hidden, give your own account an override
        from its row in Users.
      </p>
    </div>
  );
}
