// AdminSales.jsx — who purchased what, and the discounts on offer.
//
// TWO SECTIONS, DELIBERATELY ON ONE SCREEN. Subscriptions are the ledger;
// offers are what shaped the numbers in it. Reading either without the other
// leaves you asking a question the neighbouring table answers.
//
// ⚠ AN OFFER HAS TWO SWITCHES AND THEY ARE NOT THE SAME QUESTION. "Switch
// on/off" is whether the discount WORKS. "Show/Hide" is whether a customer is
// TOLD about it — a live coupon that is hidden still works when typed and
// appears nowhere, which is a code you email to one person. A live coupon that
// is SHOWN is printed on the pricing page as an offer card with its code on it,
// and that is the only way somebody who was never emailed can ever use it. Every
// row says which of the two it is, because a discount nobody can find looks
// exactly like a broken one.
//
// ⚠ NOTHING ON THIS SCREEN TAKES MONEY, AND IT SAYS SO IN THREE PLACES. Every
// row is a bookkeeping entry an administrator typed after a bank transfer or an
// invoice. A table of amounts that looks like revenue and isn't is the single
// most misleading thing an admin panel can show.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
// ⚠ RULEBOOK E1: a box somebody must EDIT grows to its text. The pop-up's
// bullet lines are the only multi-line field on this screen, and a fixed
// `rows={3}` would clip the third one out of sight — which is the exact fault
// that has been fixed on four other screens.
import GrowTextarea from "../components/GrowTextarea.jsx";
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

  // ⚠ RETURNS THE FAILURE, doesn't only park it in the page-level banner. A
  // form that fires this off and closes itself regardless throws away every
  // field that was typed and prints the reason a screenful above where the
  // person is looking. A caller that can say it beside its own fields passes
  // `silent`, gets the message back, and stays open on a failure.
  async function act(name, fn, { silent = false } = {}) {
    setBusy(name);
    if (!silent) setError("");
    try {
      await fn();
      load();
      return "";
    } catch (e) {
      if (!silent) setError(e.message);
      return e.message || "That didn't work.";
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
        onSave={(body) =>
          act("new", () => api.adminCreateSubscription(body), { silent: true })
        }
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
        onCreate={(body) =>
          act("offer", () => api.adminCreateOffer(body), { silent: true })
        }
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
  const [formError, setFormError] = useState("");

  // ⚠ THE ADDRESS IS THE ONE FIELD THAT CANNOT BE CORRECTED AFTERWARDS.
  // Everything else on this form describes the payment; the email decides WHOSE
  // account gets the plan, and a typo puts somebody who paid on nothing while a
  // subscription sits against an address that does not exist. This is a shape
  // check only — the server is what decides the account is real.
  const typo = email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())
    ? "That doesn't look like an email address — it needs an @ and a domain after it."
    : "";

  if (!open) {
    return (
      <div className="admin-actions admin-record-open">
        <button
          className="btn"
          onClick={() => {
            setFormError("");
            setOpen(true);
          }}
        >
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
        <button
          className="btn ghost small"
          onClick={() => {
            setFormError("");
            setOpen(false);
          }}
        >
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
          onChange={(e) => {
            setEmail(e.target.value);
            setFormError("");
          }}
          aria-invalid={typo ? "true" : undefined}
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
      {(typo || formError) && <p className="error">{typo || formError}</p>}

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
          disabled={busy || !email.trim() || Boolean(typo)}
          onClick={async () => {
            setFormError("");
            const failed = await onSave({
              email: email.trim(),
              tier,
              period,
              code: code.trim() || null,
              note: note.trim(),
              provider_ref: ref.trim(),
            });
            // ⚠ THE FIELDS ARE CLEARED ONLY ONCE THE RECORD EXISTS. Wiping
            // them regardless is worse here than anywhere else on this screen:
            // an invoice number and a payment reference are copied in from
            // somewhere else, and a refused save that empties them sends
            // somebody back to their bank statement to find them again.
            if (failed) {
              setFormError(failed);
              return;
            }
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

// ⚠ CHECKED HERE AS WELL AS ON THE SERVER, and that is not a duplicate. The
// server does refuse a percentage over 100 — but it refuses it once the form has
// been submitted, and the only place somebody typing a number is looking is the
// field they are typing it into. This says it there, before the trip.
function offerProblem(form) {
  const value = Number(form.value);
  if (!Number.isFinite(value) || value < 0) {
    return "The discount has to be a number, and not a negative one.";
  }
  if (value === 0) {
    return "A discount of zero takes nothing off — put in a number above zero.";
  }
  if (form.kind === "percent") {
    if (!Number.isInteger(value)) {
      return "A percentage has to be a whole number.";
    }
    if (value > 100) {
      return "A percentage discount can't be more than 100 — at 100% it is already free.";
    }
  }
  return "";
}

function Offers({ data, currency, busy, onCreate, onUpdate }) {
  const [open, setOpen] = useState(false);
  const [formError, setFormError] = useState("");
  const [form, setForm] = useState({
    code: "",
    label: "",
    kind: "percent",
    value: 20,
    period: "both",
    applies_to: [],
    banner: "",
    ends_at: "",
    // ⚠ TICKED BY DEFAULT. An offer nobody is shown is a discount that only
    // exists in this panel; somebody creating one has, by default, decided
    // customers should hear about it.
    promoted: true,
    // --- The pop-up card on Explore. Ticked by default for the same reason,
    // and it can only ever fire for an offer that is ALSO promoted — see
    // `offers.is_popup`. Every text field below is optional; left empty, the
    // card heads itself with the offer's own label and summary. ---
    popup: true,
    popup_title: "",
    // ⚠ HELD AS THE RAW TEXT OF THE BOX, split only on the way OUT. Splitting
    // and re-joining on every keystroke is what ate the separator in the props
    // field (RULEBOOK E8) — pressing Enter for a second bullet would have
    // deleted the newline as fast as it was typed.
    popup_lines: "",
    popup_note: "",
    popup_cta: "",
  });

  // Editing anything clears the last complaint from the server — it was about
  // the numbers as they were, not as they are now.
  function update(patch) {
    setForm((f) => ({ ...f, ...patch }));
    setFormError("");
  }

  const problem = offerProblem(form);
  const sales = data.offers.filter((o) => o.is_sale);
  const coupons = data.offers.filter((o) => !o.is_sale);

  return (
    <section className="card admin-card">
      <div className="admin-section-head">
        <div>
          <h2 className="admin-h2">Offers</h2>
          <p className="muted tiny admin-group-blurb admin-offers-blurb">
            A <strong>sale</strong> has no code and applies to everyone
            automatically — it changes the price on the pricing page and strikes
            through the old one. A <strong>coupon</strong> applies to nobody until
            somebody types it, so it only reaches a customer if you also{" "}
            <strong>show</strong> it — that prints it on the pricing page as an
            offer card with the code on it and an Apply button. Hide it instead
            for a code you mean to email to one person.
          </p>
        </div>
        {/* ⚠ NOT A GHOST WHEN IT IS THE ACTION. `.btn.ghost` is transparent and
            borderless, which in a section head beside a heading reads as a
            label rather than a button — the one thing an administrator comes to
            this section to press was the least visible thing in it. Cancel stays
            a ghost, because cancelling is the retreat, not the action. */}
        <button
          className={`btn small ${open ? "ghost" : ""}`}
          onClick={() => {
            setFormError("");
            setOpen((o) => !o);
          }}
        >
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
              onChange={(e) => update({ code: e.target.value })}
            />
          </label>
          <label className="admin-rollout-row">
            <span className="muted tiny">Label</span>
            <input
              className="admin-search"
              value={form.label}
              placeholder="Launch week"
              onChange={(e) => update({ label: e.target.value })}
            />
          </label>
          <label className="admin-rollout-row">
            <span className="muted tiny">Discount</span>
            <span className="admin-pct">
              <select
                className="admin-select"
                value={form.kind}
                onChange={(e) => update({ kind: e.target.value })}
              >
                <option value="percent">Percent</option>
                <option value="amount">Fixed amount</option>
              </select>
              <input
                className="admin-badge-input"
                type="number"
                min={0}
                /* ⚠ THE CAP MOVES WITH THE KIND. 1000 is a nonsense
                   percentage and a perfectly ordinary fixed amount, so the
                   ceiling belongs to "percent" and must come off again the
                   moment the dropdown says "Fixed amount". */
                max={form.kind === "percent" ? 100 : undefined}
                aria-invalid={problem ? "true" : undefined}
                value={form.value}
                onChange={(e) => update({ value: Number(e.target.value) })}
              />
              <span className="muted tiny">{form.kind === "percent" ? "%" : currency}</span>
            </span>
          </label>
          <label className="admin-rollout-row">
            <span className="muted tiny">Applies to</span>
            <select
              className="admin-select"
              value={form.period}
              onChange={(e) => update({ period: e.target.value })}
            >
              <option value="both">Both periods</option>
              <option value="monthly">Monthly only</option>
              <option value="yearly">Yearly only</option>
            </select>
          </label>
          <label className="admin-rollout-row wide">
            <span className="muted tiny">
              Banner above the pricing cards (optional)
            </span>
            <input
              className="admin-search"
              value={form.banner}
              placeholder="Launch week — 50% off everything"
              onChange={(e) => update({ banner: e.target.value })}
            />
          </label>
          <label className="admin-rollout-row wide admin-check-row">
            <span className="admin-check">
              <input
                type="checkbox"
                checked={form.promoted}
                onChange={(e) => update({ promoted: e.target.checked })}
              />
              Show this to customers on the pricing page
            </span>
            <span className="muted tiny">
              {form.code.trim()
                ? "Prints an offer card with this code on it and an Apply button. Untick it for a code you'll email to one person — it still works when typed."
                : "A sale changes every price whether or not this is ticked; ticking it also prints an offer card saying what the discount is."}
            </span>
          </label>
          <label className="admin-rollout-row wide admin-check-row">
            <span className="admin-check">
              <input
                type="checkbox"
                checked={form.popup}
                onChange={(e) => update({ popup: e.target.checked })}
              />
              Also slide it in as a card on Explore
            </span>
            <span className="muted tiny">
              The card arrives from the right a moment after Explore opens, and
              is dismissed for good once a customer closes it — until you make a
              new offer. Only an offer that is shown on the pricing page can
              appear here.
            </span>
          </label>

          {/* ⚠ THE FOUR TEXT FIELDS ONLY EXIST WHEN THE CARD DOES. Four empty
              boxes for a card nobody is going to show is four questions the
              form did not need to ask. Every one of them is optional even when
              it is on screen — see the placeholders, which are what the card
              falls back to. */}
          {form.popup && (
            <>
              <label className="admin-rollout-row wide">
                <span className="muted tiny">Card heading (optional)</span>
                <input
                  className="admin-search"
                  value={form.popup_title}
                  placeholder={form.label || "Launch week offer"}
                  onChange={(e) => update({ popup_title: e.target.value })}
                />
              </label>
              <label className="admin-rollout-row wide">
                <span className="muted tiny">
                  Bullet points — one per line, up to four
                </span>
                <GrowTextarea
                  className="admin-search admin-offer-lines"
                  rows={2}
                  value={form.popup_lines}
                  placeholder={
                    "Every plan, monthly or yearly.\n" +
                    "Cancel whenever you like."
                  }
                  onChange={(e) => update({ popup_lines: e.target.value })}
                />
              </label>
              <label className="admin-rollout-row wide">
                <span className="muted tiny">Small print under them (optional)</span>
                <input
                  className="admin-search"
                  value={form.popup_note}
                  placeholder="New customers only."
                  onChange={(e) => update({ popup_note: e.target.value })}
                />
              </label>
              <label className="admin-rollout-row wide">
                <span className="muted tiny">Button words (optional)</span>
                <input
                  className="admin-search"
                  value={form.popup_cta}
                  placeholder="View plans"
                  onChange={(e) => update({ popup_cta: e.target.value })}
                />
              </label>
            </>
          )}

          {(problem || formError) && (
            <p className="error admin-offer-error">{problem || formError}</p>
          )}
          <div className="admin-actions">
            <button
              className="btn primary"
              disabled={busy === "offer" || Boolean(problem)}
              onClick={async () => {
                setFormError("");
                const failed = await onCreate({
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
                  // ⚠ SPLIT ON THE NEWLINE AND NOTHING ELSE, here at the
                  // boundary rather than on keystroke — RULEBOOK E8. The
                  // server trims, drops the blanks and caps the count.
                  popup_lines: form.popup_lines.split("\n"),
                });
                // ⚠ ONLY CLOSES WHEN IT WORKED. Closing regardless loses every
                // field that was filled in, so the person retypes the whole
                // offer just to find out what was wrong with it.
                if (failed) setFormError(failed);
                else setOpen(false);
              }}
            >
              {busy === "offer" ? "Creating…" : "Create"}
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
              {/* ⚠ TWO SWITCHES, AND THE ROW NAMES BOTH. "Switch off" stops the
                  discount working; "Hide" only stops customers being TOLD about
                  it. Collapsing them into one control is how a live coupon ends
                  up working perfectly and reaching nobody. */}
              <span className="admin-offer-acts">
                <span
                  className={`badge ${o.promoted && o.live ? "ok" : ""}`}
                  title={
                    o.is_sale
                      ? "A sale changes every price whether or not it has a card. This only controls the offer card above the plans."
                      : o.promoted
                        ? "Customers see this code as an offer card on the pricing page."
                        : "This only works for somebody who already has the code — it is printed nowhere."
                  }
                >
                  {o.promoted && o.live
                    ? "On the pricing page"
                    : o.promoted
                      ? "Shown when live"
                      : "Hidden"}
                </span>
                <button
                  className="btn ghost small"
                  disabled={busy === o.id}
                  onClick={() => onUpdate(o.id, { promoted: !o.promoted })}
                >
                  {o.promoted ? "Hide" : "Show"}
                </button>
                {/* ⚠ A THIRD SWITCH, AND IT IS A THIRD QUESTION. "Show" puts
                    the offer on the pricing page; this puts it in front of
                    somebody who did not go looking for it. Disabled while the
                    offer is hidden, because the pop-up reads the promoted list
                    and a button that changes nothing is worse than no button —
                    the title says why. */}
                <button
                  className="btn ghost small"
                  disabled={busy === o.id || !o.promoted}
                  title={
                    o.promoted
                      ? "The card that slides in on Explore."
                      : "Show this offer first — the pop-up only draws from what is on the pricing page."
                  }
                  onClick={() => onUpdate(o.id, { popup: !o.popup })}
                >
                  {o.popup ? "No pop-up" : "Pop-up"}
                </button>
                <button
                  className="btn ghost small"
                  disabled={busy === o.id}
                  onClick={() => onUpdate(o.id, { active: !o.active })}
                >
                  {o.active ? "Switch off" : "Switch on"}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
