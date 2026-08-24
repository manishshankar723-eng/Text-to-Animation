// AdminSales.jsx — who purchased what, and the discounts on offer.
//
// TWO SECTIONS, DELIBERATELY ON ONE SCREEN. Subscriptions are the ledger;
// offers are what shaped the numbers in it. Reading either without the other
// leaves you asking a question the neighbouring table answers.
//
// ⚠ NOTHING ON THIS SCREEN TAKES MONEY, AND IT SAYS SO IN THREE PLACES. Every
// row is a bookkeeping entry an administrator typed after a bank transfer or an
// invoice. A table of amounts that looks like revenue and isn't is the single
// most misleading thing an admin panel can show.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import { formatDate, formatDateTime, money, num, timeAgo } from "./format.js";

export default function AdminSales({ onOpenUser }) {
  const [subs, setSubs] = useState(null);
  const [offers, setOffers] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [status, setStatus] = useState("");

  const load = useCallback(() => {
    setError("");
    Promise.all([
      api.adminListSubscriptions({ status: status || null, limit: 100 }),
      api.adminListOffers(),
    ])
      .then(([s, o]) => {
        setSubs(s);
        setOffers(o);
      })
      .catch((e) => setError(e.message));
  }, [status]);

  useEffect(load, [load]);

  async function act(name, fn) {
    setBusy(name);
    setError("");
    try {
      await fn();
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  if (!subs || !offers) {
    return (
      <div className="admin-body">
        <div className="card admin-card">
          <p className="muted">{error ? <span className="error">{error}</span> : "Loading…"}</p>
        </div>
      </div>
    );
  }

  const currency = subs.currency || "USD";

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="admin-tiles">
        <div className="card admin-tile">
          <span className="admin-tile-num">{num(subs.active)}</span>
          <span className="admin-tile-label">Active subscriptions</span>
          <span className="muted tiny">{num(subs.total)} recorded in total</span>
        </div>
        <div className="card admin-tile">
          <span className="admin-tile-num">{money(subs.recorded_monthly, currency)}</span>
          <span className="admin-tile-label">Recorded monthly</span>
          {/* ⚠ THE QUALIFIER IS NOT OPTIONAL. Without it this tile reads as MRR,
              and nothing in this app has taken a payment. */}
          <span className="muted tiny admin-tile-note">typed in, not charged</span>
        </div>
        <div className="card admin-tile">
          <span className="admin-tile-num">
            {num(offers.offers.filter((o) => o.live).length)}
          </span>
          <span className="admin-tile-label">Live offers</span>
          <span className="muted tiny">{num(offers.offers.length)} in total</span>
        </div>
      </div>

      <RecordForm
        tiers={offers.tier_ids}
        currency={currency}
        busy={busy === "new"}
        onSave={(body) => act("new", () => api.adminCreateSubscription(body))}
      />

      <section className="card admin-card admin-table-card">
        <div className="admin-section-head">
          <h2 className="admin-h2">Subscriptions</h2>
          <span className="admin-segment" role="group" aria-label="Status">
            {[
              ["", "All"],
              ["active", "Active"],
              ["cancelled", "Cancelled"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={`admin-seg-btn ${status === id ? "on" : ""}`}
                onClick={() => setStatus(id)}
              >
                {label}
              </button>
            ))}
          </span>
        </div>

        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Account</th>
                <th>Plan</th>
                <th>Pays</th>
                <th>Started</th>
                <th>Renews</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {subs.subscriptions.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted admin-empty">
                    Nothing recorded yet. Use the form above after a customer pays
                    you.
                  </td>
                </tr>
              )}
              {subs.subscriptions.map((sub) => (
                <tr
                  key={sub.id}
                  className={`admin-row ${sub.status === "cancelled" ? "off" : ""}`}
                >
                  <td>
                    <button
                      type="button"
                      className="admin-link"
                      onClick={() => onOpenUser?.(sub.email)}
                    >
                      {sub.email}
                    </button>
                    {sub.note && <span className="muted tiny admin-cell-sub">{sub.note}</span>}
                  </td>
                  <td>
                    <span className="chip admin-tier-chip">{sub.tier_name}</span>
                    <span className="muted tiny admin-cell-sub">{sub.period}</span>
                  </td>
                  <td>
                    {/* ⚠ THIS IS THE FROZEN PRICE, not the tier's price today.
                        Editing a tier must never re-price an existing customer,
                        which is why the amount lives on this record. */}
                    <span>{money(sub.amount, sub.currency)}</span>
                    {sub.discount > 0 && (
                      <span className="muted tiny admin-cell-sub">
                        {sub.offer_code} · −{money(sub.discount, sub.currency)}
                      </span>
                    )}
                  </td>
                  <td>
                    <span>{formatDate(sub.started_at)}</span>
                    <span className="muted tiny admin-cell-sub">{sub.source}</span>
                  </td>
                  <td>
                    {sub.status === "cancelled" ? (
                      <span className="badge fail">
                        Cancelled {timeAgo(sub.cancelled_at)}
                      </span>
                    ) : (
                      <span>{formatDate(sub.current_period_end)}</span>
                    )}
                  </td>
                  <td>
                    {sub.status === "active" && (
                      <button
                        className="btn ghost small"
                        disabled={busy === sub.id}
                        title="End it now and drop them to the free tier. There is no scheduler, so this can't be deferred to the period end."
                        onClick={() =>
                          act(sub.id, () => api.adminCancelSubscription(sub.id))
                        }
                      >
                        {busy === sub.id ? "…" : "Cancel"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <Offers
        data={offers}
        currency={currency}
        busy={busy}
        onCreate={(body) => act("offer", () => api.adminCreateOffer(body))}
        onUpdate={(id, fields) => act(id, () => api.adminUpdateOffer(id, fields))}
      />
    </div>
  );
}

function RecordForm({ tiers, currency, busy, onSave }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [tier, setTier] = useState(tiers[1]?.id || tiers[0]?.id || "");
  const [period, setPeriod] = useState("monthly");
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [ref, setRef] = useState("");

  if (!open) {
    return (
      <div className="admin-actions admin-record-open">
        <button className="btn" onClick={() => setOpen(true)}>
          ＋ Record a payment
        </button>
        <span className="muted tiny">
          For a bank transfer or invoice you've already been paid for.
        </span>
      </div>
    );
  }

  return (
    <section className="card admin-card">
      <div className="admin-section-head">
        <h2 className="admin-h2">Record a payment</h2>
        <button className="btn ghost small" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {/* ⚠ SAID BEFORE THE FIRST FIELD, not after the last. Somebody filling in
          an amount-shaped form needs to know it charges nothing BEFORE they
          fill it in. */}
      <div className="info-msg admin-note-box">
        This takes no payment — there's no payment provider connected. It records
        that someone has paid you elsewhere, puts them on the plan, and freezes
        what they agreed to pay so later price changes don't affect them.
      </div>

      <div className="admin-filters">
        <input
          className="admin-search"
          type="email"
          placeholder="Customer's email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-label="Customer email"
        />
        <select
          className="admin-select"
          value={tier}
          onChange={(e) => setTier(e.target.value)}
          aria-label="Plan"
        >
          {tiers.map((t) => (
            <option value={t.id} key={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select
          className="admin-select"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          aria-label="Billing period"
        >
          <option value="monthly">Monthly</option>
          <option value="yearly">Yearly</option>
        </select>
        <input
          className="admin-badge-input"
          placeholder="Code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          aria-label="Discount code"
        />
      </div>
      <div className="admin-filters">
        <input
          className="admin-search"
          placeholder="Note — invoice number, who agreed it…"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-label="Note"
        />
        <input
          className="admin-badge-input"
          placeholder="Ref"
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          aria-label="Payment reference"
        />
        <button
          className="btn primary"
          disabled={busy || !email.trim()}
          onClick={() => {
            onSave({
              email: email.trim(),
              tier,
              period,
              code: code.trim() || null,
              note: note.trim(),
              provider_ref: ref.trim(),
            });
            setEmail("");
            setCode("");
            setNote("");
            setRef("");
            setOpen(false);
          }}
        >
          {busy ? "Recording…" : "Record"}
        </button>
      </div>
      <p className="muted tiny">
        The amount is worked out from the plan and the code, in {currency} — so
        what's stored is what the pricing page would have quoted.
      </p>
    </section>
  );
}

function Offers({ data, currency, busy, onCreate, onUpdate }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    code: "",
    label: "",
    kind: "percent",
    value: 20,
    period: "both",
    applies_to: [],
    banner: "",
    ends_at: "",
  });

  const sales = data.offers.filter((o) => o.is_sale);
  const coupons = data.offers.filter((o) => !o.is_sale);

  return (
    <section className="card admin-card">
      <div className="admin-section-head">
        <div>
          <h2 className="admin-h2">Offers</h2>
          <p className="muted tiny admin-group-blurb">
            A <strong>sale</strong> has no code and applies to everyone
            automatically — it changes the price on the pricing page and strikes
            through the old one. A <strong>coupon</strong> applies to nobody until
            somebody types it.
          </p>
        </div>
        <button className="btn ghost small" onClick={() => setOpen((o) => !o)}>
          {open ? "Cancel" : "＋ New offer"}
        </button>
      </div>

      {open && (
        <div className="admin-rollout admin-offer-form">
          <label className="admin-rollout-row">
            <span className="muted tiny">Code — leave empty for a site-wide sale</span>
            <input
              className="admin-badge-input"
              value={form.code}
              placeholder="LAUNCH50"
              onChange={(e) => setForm({ ...form, code: e.target.value })}
            />
          </label>
          <label className="admin-rollout-row">
            <span className="muted tiny">Label</span>
            <input
              className="admin-search"
              value={form.label}
              placeholder="Launch week"
              onChange={(e) => setForm({ ...form, label: e.target.value })}
            />
          </label>
          <label className="admin-rollout-row">
            <span className="muted tiny">Discount</span>
            <span className="admin-pct">
              <select
                className="admin-select"
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value })}
              >
                <option value="percent">Percent</option>
                <option value="amount">Fixed amount</option>
              </select>
              <input
                className="admin-badge-input"
                type="number"
                min={0}
                value={form.value}
                onChange={(e) => setForm({ ...form, value: Number(e.target.value) })}
              />
              <span className="muted tiny">{form.kind === "percent" ? "%" : currency}</span>
            </span>
          </label>
          <label className="admin-rollout-row">
            <span className="muted tiny">Applies to</span>
            <select
              className="admin-select"
              value={form.period}
              onChange={(e) => setForm({ ...form, period: e.target.value })}
            >
              <option value="both">Both periods</option>
              <option value="monthly">Monthly only</option>
              <option value="yearly">Yearly only</option>
            </select>
          </label>
          <label className="admin-rollout-row wide">
            <span className="muted tiny">
              Banner above the pricing cards (sales only, optional)
            </span>
            <input
              className="admin-search"
              value={form.banner}
              placeholder="Launch week — 50% off everything"
              onChange={(e) => setForm({ ...form, banner: e.target.value })}
            />
          </label>
          <div className="admin-actions">
            <button
              className="btn primary"
              disabled={busy === "offer"}
              onClick={() => {
                onCreate({
                  ...form,
                  // ⚠ MINOR UNITS ON THE WIRE. A fixed-amount discount typed as
                  // "5" means five dollars; a percentage means five percent and
                  // must not be multiplied.
                  value:
                    form.kind === "amount"
                      ? Math.round(Number(form.value) * 100)
                      : Number(form.value),
                  code: form.code.trim() || null,
                  ends_at: form.ends_at || null,
                });
                setOpen(false);
              }}
            >
              Create
            </button>
          </div>
        </div>
      )}

      <OfferTable
        title="Sales"
        rows={sales}
        empty="No automatic sale running. Prices on the pricing page are the normal ones."
        busy={busy}
        onUpdate={onUpdate}
      />
      <OfferTable
        title="Coupons"
        rows={coupons}
        empty="No codes yet."
        busy={busy}
        onUpdate={onUpdate}
      />
    </section>
  );
}

function OfferTable({ title, rows, empty, busy, onUpdate }) {
  return (
    <>
      <h3 className="admin-h3">{title}</h3>
      {rows.length === 0 ? (
        <p className="muted tiny">{empty}</p>
      ) : (
        <ul className="admin-feed">
          {rows.map((o) => (
            <li className="admin-feed-row" key={o.id}>
              <span className={`admin-feed-ico ${o.live ? "ok" : ""}`}>
                {o.live ? "●" : "○"}
              </span>
              <span className="admin-feed-text">
                <span className="admin-feed-what">
                  {o.code || o.label || "Sale"} · {o.summary}
                </span>
                <span className="muted tiny">
                  {o.applies_to?.length ? o.applies_to.join(", ") : "every plan"}
                  {o.period !== "both" && ` · ${o.period} only`}
                  {o.ends_at && ` · ends ${formatDateTime(o.ends_at)}`}
                  {o.max_redemptions &&
                    ` · ${o.redeemed}/${o.max_redemptions} used`}
                  {!o.max_redemptions && o.redeemed > 0 && ` · ${o.redeemed} used`}
                  {/* ⚠ "Not live" has several causes and the row says which —
                      switched off, out of date range, or fully redeemed all look
                      identical otherwise. */}
                  {!o.live && !o.active && " · switched off"}
                  {!o.live &&
                    o.active &&
                    o.max_redemptions &&
                    o.redeemed >= o.max_redemptions &&
                    " · fully redeemed"}
                </span>
              </span>
              <button
                className="btn ghost small"
                disabled={busy === o.id}
                onClick={() => onUpdate(o.id, { active: !o.active })}
              >
                {o.active ? "Switch off" : "Switch on"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
