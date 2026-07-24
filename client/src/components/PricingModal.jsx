// Plans & pricing modal, opened from the sidebar "Upgrade" button.
//
// Payments aren't wired up yet, so the Upgrade CTAs show a "coming soon" note
// rather than starting a checkout — the tiers/copy are real so the page is ready
// to hook to a billing provider later. Themed in the app's dark+gold language
// (not the reference's branding).
import { useState } from "react";

// Per-month price when billed monthly vs. yearly. `was` is the struck-through
// "regular" price shown against the limited-time offer.
const PLANS = [
  {
    id: "trial",
    name: "Trial",
    blurb: "Explore the studio for free — bring a script to life in minutes.",
    monthly: 0,
    yearly: 0,
    badge: "Your Plan",
    current: true,
    features: [
      { text: "2 projects", ok: true },
      { text: "9 shots per project", ok: true },
      { text: "50 image generations", ok: true },
      { text: "Export with watermark", ok: true },
      { text: "No commercial use", ok: false },
    ],
  },
  {
    id: "starter",
    name: "Starter",
    blurb: "For creators. Ideal for short clips, commercials or short films.",
    monthly: 28,
    yearly: 21,
    was: 69,
    highlight: true,
    badge: "Most Popular",
    features: [
      { text: "5 projects per month", ok: true, strong: true },
      { text: "Stories up to 10 pages", ok: true },
      { text: "Unlimited image generations", ok: true },
      { text: "Commercial use", ok: true },
      { text: "Export to various formats", ok: true },
    ],
  },
  {
    id: "pro",
    name: "Pro Unlimited",
    blurb: "For professionals and agencies — ad campaigns and longer films.",
    monthly: 69,
    yearly: 53,
    was: 149,
    badge: "Best Value",
    features: [
      { text: "Unlimited projects", ok: true, strong: true },
      { text: "Stories up to 30 pages", ok: true, strong: true },
      { text: "Unlimited image generations", ok: true },
      { text: "Commercial use", ok: true },
      { text: "Export to various formats", ok: true },
    ],
  },
  {
    id: "production",
    name: "Production Unlimited",
    blurb: "For film pros — features or series, regardless of screenplay length.",
    monthly: 179,
    yearly: 135,
    was: 399,
    features: [
      { text: "Unlimited projects", ok: true },
      { text: "Unlimited story length", ok: true, strong: true },
      { text: "Unlimited image generations", ok: true },
      { text: "Commercial use", ok: true },
      { text: "Export to various formats", ok: true },
    ],
  },
];

export default function PricingModal({ onClose }) {
  const [yearly, setYearly] = useState(true);
  const [notice, setNotice] = useState("");

  function onUpgrade(plan) {
    setNotice(`Checkout for ${plan.name} is coming soon — hang tight!`);
  }

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
              Yearly <span className="pricing-save">Save 25%</span>
            </button>
          </div>
        </div>

        {notice && <div className="info-msg pricing-notice">{notice}</div>}

        <div className="pricing-grid">
          {PLANS.map((plan) => {
            const price = yearly ? plan.yearly : plan.monthly;
            return (
              <div
                className={`pricing-card ${plan.highlight ? "featured" : ""} ${
                  plan.current ? "current" : ""
                }`}
                key={plan.id}
              >
                {plan.badge && <span className="pricing-badge">{plan.badge}</span>}
                <h3 className="pricing-name">{plan.name}</h3>
                <p className="pricing-blurb">{plan.blurb}</p>

                <div className="pricing-price">
                  {price === 0 ? (
                    <span className="pricing-amount">Free</span>
                  ) : (
                    <>
                      <span className="pricing-amount">${price}</span>
                      {plan.was && <span className="pricing-was">${plan.was}</span>}
                    </>
                  )}
                </div>
                {price > 0 && (
                  <div className="pricing-per">
                    monthly, billed {yearly ? "yearly" : "monthly"}
                  </div>
                )}

                {plan.current ? (
                  <button className="btn pricing-cta" disabled>
                    ✓ Current Plan
                  </button>
                ) : (
                  <button
                    className={`btn pricing-cta ${plan.highlight ? "primary" : ""}`}
                    onClick={() => onUpgrade(plan)}
                  >
                    Upgrade
                  </button>
                )}

                <ul className="pricing-features">
                  {plan.features.map((f, i) => (
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
