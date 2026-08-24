// AdminPricing.jsx — the tiers: what they cost, and what they actually unlock.
//
// ⚠ THE TWO COLUMNS ON EACH CARD ARE THE WHOLE POINT OF THIS SCREEN. On the
// left, the marketing bullets — free text, what the pricing page promises. On
// the right, "Unlocks", DERIVED by asking every feature which tier it needs.
// Nothing keeps those two in step automatically, so they are shown side by side:
// copy that promises something the flags don't grant is visible here, rather
// than discovered by a customer who paid for it.
//
// A tier does NOT store a list of features. See the note at the top of
// `server/billing.py` for why that would be two places to answer one question.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import { formatDateTime, num } from "./format.js";

// Minor units ↔ what an administrator types. ⚠ THE FIELD IS IN MAJOR UNITS
// (dollars) AND THE WIRE IS IN MINOR (cents): typing 28 must send 2800, and a
// price that arrives as 2800 must show as 28. The conversion happens in exactly
// these two functions, and the server refuses a non-integer outright.
function toMajor(minor) {
  return String((minor || 0) / 100);
}
function toMinor(major) {
  const n = Number(String(major).replace(/[^0-9.]/g, ""));
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n * 100);
}

const SYMBOLS = { USD: "$", INR: "₹", EUR: "€", GBP: "£" };

export default function AdminPricing() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .adminListTiers()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  async function save(id, fields) {
    setBusy(id);
    setError("");
    try {
      const saved = await api.adminUpdateTier(id, fields);
      setData((d) => ({
        ...d,
        // Keep the derived `includes` and `subscribers` from the row we already
        // have — the PATCH answers with the tier, not with what it unlocks, and
        // dropping them would blank that column until the next reload.
        tiers: d.tiers.map((t) => (t.id === saved.id ? { ...t, ...saved } : t)),
      }));
    } catch (e) {
      setError(e.message);
      load();
    } finally {
      setBusy("");
    }
  }

  if (loading && !data) {
    return (
      <div className="admin-body">
        <div className="card admin-card">
          <p className="muted">Loading…</p>
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="admin-body">
        <div className="card admin-card">
          <p className="error">{error || "Could not load the tiers."}</p>
          <button className="btn small" onClick={load}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  const symbol = SYMBOLS[data.currency] || "";

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="info-msg admin-note-box">
        Prices are edited in {data.currency} and take effect on the pricing page
        immediately. <strong>Nobody is re-charged</strong> — there is no payment
        provider connected yet, so a price here is what a new customer would be
        quoted. Which tier an account is on is set from that account's row in
        Users.
      </div>

      <div className="admin-tier-grid">
        {data.tiers.map((t) => (
          <TierCard
            key={t.id}
            tier={t}
            symbol={symbol}
            currency={data.currency}
            isDefault={t.id === data.default_tier}
            busy={busy === t.id}
            onSave={save}
          />
        ))}
      </div>
    </div>
  );
}

function TierCard({ tier, symbol, currency, isDefault, busy, onSave }) {
  const [monthly, setMonthly] = useState(toMajor(tier.monthly));
  const [yearly, setYearly] = useState(toMajor(tier.yearly));
  const [compare, setCompare] = useState(toMajor(tier.compare_at));
  const [name, setName] = useState(tier.name);
  const [blurb, setBlurb] = useState(tier.blurb);
  const [badge, setBadge] = useState(tier.badge || "");
  const [local, setLocal] = useState("");

  // Saved on blur, one field per PATCH — the same rule the Features screen
  // follows, so two admins editing different fields don't overwrite each other.
  function commitMoney(field, value, setter, was) {
    const minor = toMinor(value);
    if (minor === null) {
      setLocal("That isn't a price.");
      setter(toMajor(was));
      return;
    }
    setLocal("");
    if (minor !== was) onSave(tier.id, { [field]: minor });
  }

  function commitText(field, value, was) {
    const next = value.trim();
    if (next !== (was || "")) onSave(tier.id, { [field]: next });
  }

  const saving =
    tier.monthly > 0 ? Math.round((1 - tier.yearly / tier.monthly) * 100) : 0;

  return (
    <section className={`card admin-tier ${tier.archived ? "archived" : ""}`}>
      <div className="admin-tier-head">
        <input
          className="admin-tier-name"
          value={name}
          maxLength={60}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => commitText("name", name, tier.name)}
          aria-label="Tier name"
        />
        <span className="admin-tier-badges">
          {isDefault && (
            <span className="badge queued" title="Every new account starts here, and it can't be archived">
              Default
            </span>
          )}
          {tier.highlight && <span className="badge running">Featured</span>}
          {tier.archived && <span className="badge fail">Archived</span>}
        </span>
      </div>

      <code className="admin-feature-key">{tier.id}</code>

      <textarea
        className="admin-note admin-tier-blurb"
        rows={2}
        value={blurb}
        maxLength={240}
        disabled={busy}
        onChange={(e) => setBlurb(e.target.value)}
        onBlur={() => commitText("blurb", blurb, tier.blurb)}
        aria-label="Tier blurb"
      />

      {local && <p className="error tiny">{local}</p>}

      <div className="admin-price-row">
        <label className="admin-price-field">
          <span className="muted tiny">Monthly</span>
          <span className="admin-price-input">
            <span className="admin-price-sym">{symbol}</span>
            <input
              value={monthly}
              disabled={busy}
              inputMode="decimal"
              onChange={(e) => setMonthly(e.target.value)}
              onBlur={() => commitMoney("monthly", monthly, setMonthly, tier.monthly)}
            />
          </span>
        </label>
        <label className="admin-price-field">
          <span className="muted tiny">Yearly (per month)</span>
          <span className="admin-price-input">
            <span className="admin-price-sym">{symbol}</span>
            <input
              value={yearly}
              disabled={busy}
              inputMode="decimal"
              onChange={(e) => setYearly(e.target.value)}
              onBlur={() => commitMoney("yearly", yearly, setYearly, tier.yearly)}
            />
          </span>
        </label>
        <label className="admin-price-field">
          <span className="muted tiny">Was (struck through)</span>
          <span className="admin-price-input">
            <span className="admin-price-sym">{symbol}</span>
            <input
              value={compare}
              disabled={busy}
              inputMode="decimal"
              onChange={(e) => setCompare(e.target.value)}
              onBlur={() => commitMoney("compare_at", compare, setCompare, tier.compare_at)}
            />
          </span>
        </label>
      </div>

      <p className="muted tiny">
        {/* Computed, never typed. The modal shows the same number, worked out
            the same way, so the two cannot disagree about the discount. */}
        {saving > 0
          ? `Yearly saves ${saving}% — the pricing page works this out itself.`
          : "No yearly saving at these prices."}
        {tier.compare_at > 0 && tier.compare_at <= tier.monthly && (
          <>
            {" "}
            <strong>
              The "was" price isn't higher than the monthly one, so the strike-through
              will look wrong.
            </strong>
          </>
        )}
      </p>

      <div className="admin-tier-cols">
        <div>
          <h4 className="admin-h4">Says on the card</h4>
          <ul className="pricing-features admin-tier-bullets">
            {(tier.bullets || []).map((b, i) => (
              <li key={i} className={b.ok ? "" : "no"}>
                <span className="pricing-ic">{b.ok ? "✓" : "✕"}</span>
                <span className={b.strong ? "strong" : ""}>{b.text}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="admin-h4">Actually unlocks</h4>
          {/* ⚠ DERIVED FROM `min_tier` ON EACH FEATURE, not stored here. Set it
              on the Features screen; this only reports the result. */}
          <ul className="admin-tier-unlocks">
            {tier.includes.length === 0 ? (
              <li className="muted tiny">Nothing — every feature needs a higher tier.</li>
            ) : (
              tier.includes.map((f) => (
                <li key={f.key} className="muted tiny">
                  {f.label}
                </li>
              ))
            )}
          </ul>
          <p className="muted tiny">
            Set on <strong>Features → which tier unlocks it</strong>.
          </p>
        </div>
      </div>

      <div className="admin-tier-foot">
        <span className="muted tiny">
          {num(tier.subscribers)} account{tier.subscribers === 1 ? "" : "s"}
          {tier.updated_at && (
            <>
              {" · changed "}
              {formatDateTime(tier.updated_at)}
              {tier.updated_by && ` by ${tier.updated_by}`}
            </>
          )}
        </span>
        <span className="admin-actions">
          <label className="admin-check tiny">
            <input
              type="checkbox"
              checked={!!tier.visible}
              disabled={busy}
              onChange={(e) => onSave(tier.id, { visible: e.target.checked })}
            />
            Show on the pricing page
          </label>
          <label className="admin-check tiny">
            <input
              type="checkbox"
              checked={!!tier.highlight}
              disabled={busy}
              onChange={(e) => onSave(tier.id, { highlight: e.target.checked })}
            />
            Feature it
          </label>
          <input
            className="admin-badge-input"
            value={badge}
            maxLength={30}
            disabled={busy}
            placeholder="Badge"
            onChange={(e) => setBadge(e.target.value)}
            onBlur={() => commitText("badge", badge, tier.badge)}
            aria-label="Badge text"
          />
          {/* ⚠ ARCHIVE, NEVER DELETE — a tier somebody is subscribed to has to
              keep resolving, or their account cannot be priced. The default tier
              refuses outright; the server enforces that too. */}
          {!isDefault && (
            <button
              className="btn ghost small"
              disabled={busy}
              onClick={() => onSave(tier.id, { archived: !tier.archived })}
              title={
                tier.archived
                  ? "Show it again on the pricing page"
                  : "Take it off the pricing page. Anyone already on it keeps it."
              }
            >
              {tier.archived ? "Restore" : "Archive"}
            </button>
          )}
        </span>
      </div>
    </section>
  );
}
