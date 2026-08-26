// Plans & pricing modal, opened from the sidebar "Upgrade" button.
//
// ⚠ THE TIERS COME FROM THE SERVER NOW. They used to be a `PLANS` constant in
// this file, which meant changing a price was a code edit and a redeploy — the
// exact thing the admin panel's Phase 3 exists to end. `GET /billing/tiers` is
// public and unauthenticated (a price list is public by nature), and Admin →
// Pricing is what writes it.
//
// ⚠ PRICES ARRIVE AS INTEGER MINOR UNITS: 2800 is $28.00. Nothing in this app's
// money path is ever a float; the division happens once, here, at the moment of
// display.
//
// ⚠ THE OFFER CARDS AT THE TOP ARE THE ONLY PLACE A COUPON IS VISIBLE. A SALE
// reaches a customer as a changed number on a plan card, so it announces itself.
// A COUPON CHANGES NOTHING UNTIL SOMEBODY TYPES IT — so a coupon that is not
// printed somewhere is a discount that exists only in the admin panel, which is
// exactly the bug these cards fix. `GET /billing/tiers` sends the promoted ones
// in `offers`; a code an administrator meant to email to one customer is not
// promoted and never appears here.
//
// ⚠ A COUPON IS CHECKED AGAINST EVERY PAID TIER, NOT ONE. "20% off every plan"
// applied to a single card is a discount somebody has to hunt for by clicking
// each plan in turn, and it leaves the other three cards quoting a price that is
// no longer true for them. One request per paid tier is four requests; a copy of
// the discount arithmetic in JavaScript is a second answer that can disagree
// with the server's, and the one people believe is the one on screen.
//
// ⚠ THE FALLBACK BELOW IS NOT DEAD CODE. If the request fails, a customer who
// clicked Upgrade must still see a price list — an empty modal is a lost sale
// and looks like a broken app. It is the same shape the server seeds from, so
// the two agree until somebody edits the real one. ⚠ IT CARRIES NO OFFERS: an
// offer we could not read is a discount nobody is currently entitled to, and
// inventing one gives money away (the same fail-CLOSED rule as `offers.py`).
//
// Payments aren't wired up yet, so the Upgrade CTAs show a "coming soon" note
// rather than starting a checkout — Phase 4/6 of the admin panel plan.
import { useEffect, useState } from "react";
import * as api from "../api.js";
// ⚠ THE CARD IS SHARED WITH THE LANDING PAGE AND THE DASHBOARD. It used to be
// declared at the bottom of this file, which meant the offer a customer sees
// before signing in and the one they see after were two components that only
// happened to agree. See OfferCard.jsx.
import { OfferCard } from "./OfferCard.jsx";

// Minor units → what a person reads. `$28`, not `$28.00`, when it is whole:
// the pricing page is marketing copy and trailing zeroes read as fine print.
function money(minor, currency) {
  const major = (minor || 0) / 100;
  const symbol = { USD: "$", INR: "₹", EUR: "€", GBP: "£" }[currency] || "";
  const shown = Number.isInteger(major) ? major : major.toFixed(2);
  return symbol ? `${symbol}${shown}` : `${shown} ${currency}`;
}

// The shape the server sends, as a last resort. Kept byte-compatible with
// `_CATALOG` in `server/billing.py`.
const FALLBACK = {
  currency: "USD",
  offers: [],
  tiers: [
    { id: "trial", name: "Trial", blurb: "Explore the studio for free — bring a script to life in minutes.", monthly: 0, yearly: 0, compare_at: 0, badge: "", highlight: false,
      bullets: [{ text: "2 projects", ok: true }, { text: "9 shots per project", ok: true }, { text: "50 image generations", ok: true }, { text: "Export with watermark", ok: true }, { text: "No commercial use", ok: false }] },
    { id: "starter", name: "Starter", blurb: "For creators. Ideal for short clips, commercials or short films.", monthly: 2800, yearly: 2100, compare_at: 6900, badge: "Most Popular", highlight: true,
      bullets: [{ text: "5 projects per month", ok: true, strong: true }, { text: "Stories up to 10 pages", ok: true }, { text: "Unlimited image generations", ok: true }, { text: "Commercial use", ok: true }, { text: "Export to various formats", ok: true }] },
    { id: "pro", name: "Pro Unlimited", blurb: "For professionals and agencies — ad campaigns and longer films.", monthly: 6900, yearly: 5300, compare_at: 14900, badge: "Best Value", highlight: false,
      bullets: [{ text: "Unlimited projects", ok: true, strong: true }, { text: "Stories up to 30 pages", ok: true, strong: true }, { text: "Unlimited image generations", ok: true }, { text: "Commercial use", ok: true }, { text: "Export to various formats", ok: true }] },
    { id: "production", name: "Production Unlimited", blurb: "For film pros — features or series, regardless of screenplay length.", monthly: 17900, yearly: 13500, compare_at: 39900, badge: "", highlight: false,
      bullets: [{ text: "Unlimited projects", ok: true }, { text: "Unlimited story length", ok: true, strong: true }, { text: "Unlimited image generations", ok: true }, { text: "Commercial use", ok: true }, { text: "Export to various formats", ok: true }] },
  ],
};

/**
 * @param {object} p
 * @param {Function} p.onClose
 * @param {string} [p.currentTier] Which tier this account is on, from
 *   `/auth/me/entitlements`. ⚠ IT IS NOT READ FROM THE TIER LIST — that call is
 *   public and knows nothing about who is asking, which is what lets the
 *   logged-out landing page use it.
 */
export default function PricingModal({ onClose, currentTier = "" }) {
  const [yearly, setYearly] = useState(true);
  const [notice, setNotice] = useState("");
  const [data, setData] = useState(null);
  // The coupon box and the offer cards' Apply buttons share this one piece of
  // state. ⚠ CHECKING A CODE REDEEMS NOTHING — the server counts a redemption
  // only when a subscription is actually recorded against it, so somebody can
  // try a code, change their mind, and try it again tomorrow.
  const [code, setCode] = useState("");
  const [coupon, setCoupon] = useState(null);
  const [rejected, setRejected] = useState(false);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .tiers()
      .then((r) => {
        if (!cancelled && r?.tiers?.length) setData(r);
      })
      .catch(() => {
        // Fall through to FALLBACK — see the note at the top of the file.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const { tiers, currency, banner } = data || FALLBACK;
  // ⚠ FROM `data`, NEVER FROM `FALLBACK` — fail closed, see the file header.
  const promos = data?.offers || [];
  const appliedCode = coupon?.code || "";

  /**
   * Check `raw` against every paid tier for the period on screen, and remember
   * what each one becomes.
   *
   * ⚠ ONE REQUEST PER TIER, ON PURPOSE. The route answers for ONE plan, because
   * an offer can be limited to some of them; asking once and reusing the figure
   * would quote the Starter discount on the Production card.
   *
   * ⚠ IT IS "VALID" IF ANY TIER TOOK IT. "20% off Starter only" is a real code
   * that three of the four cards must refuse — treating those refusals as a bad
   * code would tell the customer their working coupon does not exist.
   */
  async function applyCode(raw) {
    const trimmed = (raw || "").trim();
    if (!trimmed) return;
    setChecking(true);
    setRejected(false);
    const period = yearly ? "yearly" : "monthly";
    const paid = tiers.filter((t) => (t.monthly || 0) > 0 || (t.yearly || 0) > 0);
    try {
      const results = await Promise.all(
        paid.map((t) =>
          api.checkCoupon(trimmed, t.id, period).then(
            (r) => [t.id, r],
            () => [t.id, null], // one tier's failure is not the code's failure
          ),
        ),
      );
      const byTier = {};
      let label = "";
      let confirmed = "";
      for (const [id, r] of results) {
        if (!r?.valid) continue;
        byTier[id] = { now: r.now, was: r.was, discount: r.discount };
        label = label || r.label || "";
        confirmed = confirmed || r.code || "";
      }
      if (Object.keys(byTier).length === 0) {
        setCoupon(null);
        setRejected(true);
      } else {
        setCoupon({ code: confirmed || trimmed.toUpperCase(), label, byTier });
        setRejected(false);
      }
    } finally {
      setChecking(false);
    }
  }

  // ⚠ THE PERIOD TOGGLE RE-CHECKS AN APPLIED CODE. "20% off yearly" is not a
  // discount on the monthly price, and leaving the old figure on screen after
  // the toggle quotes a price that does not exist. Deliberately keyed on
  // `yearly` alone — adding `appliedCode` would re-run this on the answer it
  // just stored, and loop.
  useEffect(() => {
    if (appliedCode) applyCode(appliedCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [yearly]);

  function onUpgrade(tier) {
    setNotice(`Checkout for ${tier.name} is coming soon — hang tight!`);
  }

  // The saving is computed, not written down: a hard-coded "Save 25%" beside
  // prices an administrator can edit is a promise that goes stale the first
  // time somebody changes one.
  const savings = tiers
    .filter((t) => t.monthly > 0)
    .map((t) => Math.round((1 - t.yearly / t.monthly) * 100))
    .filter((n) => n > 0);
  const savePct = savings.length ? Math.max(...savings) : 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="pricing-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Plans and pricing"
      >
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ✕
        </button>

        <div className="pricing-head">
          <span className="pricing-eyebrow">Simple, transparent pricing</span>
          <h2 className="pricing-title">Choose your best plan</h2>
          <div className="pricing-pills">
            {[
              "Unlimited image generations",
              "No watermarks",
              "Commercial use",
              "Beyond 9 shots",
              "Full exports",
            ].map((p) => (
              <span className="pricing-pill" key={p}>
                ✓ {p}
              </span>
            ))}
          </div>

          <div className="pricing-toggle" role="group" aria-label="Billing period">
            <button
              type="button"
              className={!yearly ? "on" : ""}
              onClick={() => setYearly(false)}
            >
              Monthly
            </button>
            <button
              type="button"
              className={yearly ? "on" : ""}
              onClick={() => setYearly(true)}
            >
              Yearly
              {savePct > 0 && <span className="pricing-save">Save {savePct}%</span>}
            </button>
          </div>
        </div>

        {banner && <div className="info-msg pricing-notice pricing-banner">{banner}</div>}
        {notice && <div className="info-msg pricing-notice">{notice}</div>}

        {promos.length > 0 && (
          <div className="pricing-offers" role="region" aria-label="Current offers">
            {promos.map((offer) => (
              <OfferCard
                key={offer.id || offer.code}
                offer={offer}
                tiers={tiers}
                busy={checking}
                applied={!!appliedCode && appliedCode === offer.code}
                ctaLabel="Apply"
                onCta={() => {
                  setCode(offer.code);
                  applyCode(offer.code);
                }}
                /* ⚠ A SALE IS ALREADY IN THE NUMBERS BELOW, so it gets a
                   sentence rather than a button that could not do anything. */
                saleNote="✓ Already applied to the prices below"
              />
            ))}
          </div>
        )}

        <div className="pricing-grid">
          {tiers.map((tier) => {
            // The server has already applied any site-wide sale to these
            // numbers; a coupon is per-customer and applies on top of what is
            // shown, which is why it is worked out here and not folded in.
            const listed = yearly ? tier.yearly : tier.monthly;
            const applied = coupon?.byTier?.[tier.id] || null;
            const price = applied ? applied.now : listed;
            // ⚠ WHICH CARD IS "YOURS" IS TOLD TO US, not guessed from the
            // price being zero — that assumption is what made Trial the
            // current plan for everybody, including paying customers.
            const current = tier.id === currentTier;
            return (
              <div
                className={`pricing-card ${tier.highlight ? "featured" : ""} ${
                  current ? "current" : ""
                }`}
                key={tier.id}
              >
                {(current || tier.badge) && (
                  <span className="pricing-badge">
                    {current ? "Your Plan" : tier.badge}
                  </span>
                )}
                <h3 className="pricing-name">{tier.name}</h3>
                <p className="pricing-blurb">{tier.blurb}</p>

                <div className="pricing-price">
                  {price === 0 ? (
                    <span className="pricing-amount">Free</span>
                  ) : (
                    <>
                      <span className="pricing-amount">{money(price, currency)}</span>
                      {/* A coupon strikes through the LISTED price; a sale has
                          already made `compare_at` the pre-sale one. Never both
                          — two struck-through numbers on one card is nobody's
                          idea of a clear offer. */}
                      {applied ? (
                        <span className="pricing-was">{money(listed, currency)}</span>
                      ) : (
                        tier.compare_at > 0 && (
                          <span className="pricing-was">
                            {money(tier.compare_at, currency)}
                          </span>
                        )
                      )}
                    </>
                  )}
                </div>
                {tier.sale && !applied && (
                  <div className="pricing-sale-tag">{tier.sale}</div>
                )}
                {applied && (
                  <div className="pricing-sale-tag">
                    {coupon.code} applied · save {money(applied.discount, currency)}
                  </div>
                )}
                {price > 0 && (
                  <div className="pricing-per">
                    monthly, billed {yearly ? "yearly" : "monthly"}
                  </div>
                )}

                {current ? (
                  <button className="btn pricing-cta" disabled>
                    ✓ Current Plan
                  </button>
                ) : (
                  <button
                    className={`btn pricing-cta ${tier.highlight ? "primary" : ""}`}
                    onClick={() => onUpgrade(tier)}
                  >
                    Upgrade
                  </button>
                )}

                <ul className="pricing-features">
                  {(tier.bullets || []).map((f, i) => (
                    <li key={i} className={f.ok ? "" : "no"}>
                      <span className="pricing-ic">{f.ok ? "✓" : "✕"}</span>
                      <span className={f.strong ? "strong" : ""}>{f.text}</span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>

        {/* Still here even when an offer card is on screen: a code somebody was
            emailed is not promoted and is on no card, so there has to be a box
            to type it into. */}
        <div className="pricing-coupon">
          <input
            value={code}
            placeholder="Discount code"
            aria-label="Discount code"
            onChange={(e) => {
              setCode(e.target.value);
              setCoupon(null);
              setRejected(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyCode(code);
            }}
          />
          <button
            type="button"
            className="btn small"
            disabled={checking || !code.trim()}
            onClick={() => applyCode(code)}
          >
            {checking ? "Checking…" : "Apply"}
          </button>
          {rejected && <span className="muted tiny">That code isn't valid.</span>}
          {coupon && (
            <span className="muted tiny">
              {coupon.label || coupon.code} applied to{" "}
              {Object.keys(coupon.byTier).length === 1
                ? tiers.find((t) => t.id === Object.keys(coupon.byTier)[0])?.name
                : `${Object.keys(coupon.byTier).length} plans`}
            </span>
          )}
        </div>

        <div className="pricing-foot">
          <span>✓ Monthly plans available</span>
          <span>✓ Cancel anytime</span>
          <span>✓ Instant access after upgrade</span>
          <span>✓ No hidden fees</span>
        </div>
      </div>
    </div>
  );
}
