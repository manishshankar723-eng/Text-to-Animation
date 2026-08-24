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
// ⚠ THE FALLBACK BELOW IS NOT DEAD CODE. If the request fails, a customer who
// clicked Upgrade must still see a price list — an empty modal is a lost sale
// and looks like a broken app. It is the same shape the server seeds from, so
// the two agree until somebody edits the real one.
//
// Payments aren't wired up yet, so the Upgrade CTAs show a "coming soon" note
// rather than starting a checkout — Phase 4/6 of the admin panel plan.
import { useEffect, useState } from "react";
import * as api from "../api.js";

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
  // The coupon box. ⚠ CHECKING A CODE REDEEMS NOTHING — the server counts a
  // redemption only when a subscription is actually recorded against it, so
  // somebody can try a code, change their mind, and try it again tomorrow.
  const [code, setCode] = useState("");
  const [coupon, setCoupon] = useState(null);
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

  // ⚠ THE COUPON IS CHECKED AGAINST ONE TIER, so it is re-checked when the
  // period toggles: "20% off yearly" is not a discount on the monthly price,
  // and showing the old figure after the toggle would quote a price that does
  // not exist.
  async function check(tierId) {
    const trimmed = code.trim();
    if (!trimmed) return;
    setChecking(true);
    try {
      const r = await api.checkCoupon(trimmed, tierId, yearly ? "yearly" : "monthly");
      setCoupon(r?.valid ? { ...r, tier: tierId } : { valid: false });
    } catch {
      setCoupon({ valid: false });
    } finally {
      setChecking(false);
    }
  }

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

        <div className="pricing-grid">
          {tiers.map((tier) => {
            // The server has already applied any site-wide sale to these
            // numbers; a coupon is per-customer and applies on top of what is
            // shown, which is why it is worked out here and not folded in.
            const listed = yearly ? tier.yearly : tier.monthly;
            const applied =
              coupon?.valid && coupon.tier === tier.id ? coupon : null;
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
                  <div className="pricing-sale-tag">{applied.code} applied</div>
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

        <div className="pricing-coupon">
          <input
            value={code}
            placeholder="Discount code"
            aria-label="Discount code"
            onChange={(e) => {
              setCode(e.target.value);
              setCoupon(null);
            }}
          />
          <button
            type="button"
            className="btn small"
            disabled={checking || !code.trim()}
            /* Checked against the FEATURED tier, or the first paid one — the
               card somebody is most likely looking at. Switching plan or period
               re-checks, so the figure on screen is always for what is on
               screen. */
            onClick={() =>
              check(
                (tiers.find((t) => t.highlight) || tiers.find((t) => t.monthly > 0) || tiers[0])
                  .id
              )
            }
          >
            {checking ? "Checking…" : "Apply"}
          </button>
          {coupon && !coupon.valid && (
            <span className="muted tiny">That code isn't valid.</span>
          )}
          {coupon?.valid && (
            <span className="muted tiny">
              {coupon.label} — {money(coupon.discount, currency)} off {" "}
              {tiers.find((t) => t.id === coupon.tier)?.name}
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
