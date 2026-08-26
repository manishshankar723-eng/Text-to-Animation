// OfferCard.jsx — the one offer card, and the strip that fetches its own.
//
// ⚠ ONE COMPONENT, THREE PLACES, ON PURPOSE. This card is drawn on the pricing
// modal, on the logged-out landing page and on the signed-in dashboard. Three
// copies of "a gold ticket with a code on it" is three things to keep in step,
// and the first one somebody forgets is the one a customer is looking at. What
// changes between the three is the BUTTON, and that is a prop.
//
// ⚠ A COUPON IS THE ONLY REASON THIS EXISTS. A sale reaches somebody as a
// changed number on a plan card, so it announces itself; a coupon changes
// nothing until it is typed, so unless its code is printed somewhere it is a
// discount that exists only in the admin panel. See `server/offers.py`.
//
// ⚠ AND THE STRIP FAILS CLOSED. If `/billing/tiers` cannot be read, or carries
// no promoted offers, this renders NOTHING — never a placeholder, never a
// skeleton. An offer we could not read is a discount nobody is currently
// entitled to, and a card that says "loading your discount" to somebody who has
// none is worse than silence.
import { useEffect, useState } from "react";
import * as api from "../api.js";

// "Ends in 6 days". ⚠ AN OFFER THAT HAS ALREADY ENDED RETURNS "" RATHER THAN A
// NEGATIVE COUNT — the server has stopped sending expired offers, so anything
// this sees should still be running; if the two clocks disagree, saying nothing
// is better than saying "ends in -3 hours".
export function timeLeft(iso) {
  if (!iso) return "";
  const ms = new Date(iso).getTime() - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const hrs = Math.floor(ms / 3600000);
  if (hrs < 1) return `Ends in ${Math.max(1, Math.floor(ms / 60000))} min`;
  if (hrs < 24) return `Ends in ${hrs} hour${hrs === 1 ? "" : "s"}`;
  const days = Math.floor(hrs / 24);
  return `Ends in ${days} day${days === 1 ? "" : "s"}`;
}

// What an offer covers, in one line. ⚠ AN EMPTY `applies_to` MEANS EVERY PLAN,
// not none — the same reading the server uses (`offers.applies_to`). Getting it
// backwards here would print "no plans" on a site-wide discount.
export function scopeOf(offer, tiers = []) {
  const names = (offer.applies_to || [])
    .map((id) => tiers.find((t) => t.id === id)?.name || id)
    .filter(Boolean);
  const where = names.length ? names.join(", ") : "every plan";
  const when =
    offer.period === "monthly"
      ? "monthly billing"
      : offer.period === "yearly"
        ? "yearly billing"
        : "monthly or yearly";
  return `${where} · ${when}`;
}

/**
 * One promoted offer, as a ticket.
 *
 * ⚠ A SALE AND A COUPON GET DIFFERENT RIGHT-HAND SIDES, because they ask
 * different things of the reader. A coupon needs its code, in a form that can be
 * both COPIED (for a friend, or for a checkout later) and used in one press. A
 * sale is already in the prices — on the pricing page `saleNote` says so, and
 * offering an "Apply" for it would invite somebody to press a button that cannot
 * do anything.
 *
 * @param {object} p
 * @param {object} p.offer      One row from `GET /billing/tiers` → `offers`.
 * @param {Array}  [p.tiers]    The tier list, only so `applies_to` ids can be
 *   printed as names. Missing is fine — the ids read acceptably on their own.
 * @param {boolean} [p.applied] This code is currently applied (pricing modal).
 * @param {boolean} [p.busy]    A check is in flight; the button waits.
 * @param {string} [p.ctaLabel] The button's words. No label, no button.
 * @param {Function} [p.onCta]  What the button does.
 * @param {string} [p.saleNote] Shown INSTEAD of the button on a sale.
 */
export function OfferCard({
  offer,
  tiers = [],
  applied = false,
  busy = false,
  ctaLabel = "",
  onCta = null,
  saleNote = "",
}) {
  const [copied, setCopied] = useState(false);
  const ends = timeLeft(offer.ends_at);
  const capped = offer.remaining !== null && offer.remaining !== undefined;
  const cta = ctaLabel && onCta;

  async function copy() {
    try {
      await navigator.clipboard.writeText(offer.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // No clipboard permission — the code is on screen and selectable, which
      // is the whole reason it is rendered as text and not as an image.
    }
  }

  return (
    <article className={`pricing-offer ${applied ? "on" : ""}`}>
      <span className="pricing-offer-flag">
        {offer.is_sale ? "Sale" : "Limited offer"}
      </span>

      <div className="pricing-offer-main">
        <span className="pricing-offer-cut">{offer.summary}</span>
        <span className="pricing-offer-name">
          {offer.label || (offer.is_sale ? "Site-wide sale" : "Discount code")}
        </span>
        <span className="pricing-offer-scope">{scopeOf(offer, tiers)}</span>
      </div>

      <div className="pricing-offer-side">
        {offer.is_sale && saleNote ? (
          <span className="pricing-offer-auto">{saleNote}</span>
        ) : (
          <>
            {/* No code on a sale — there is nothing to type, so there is no
                box pretending there is. */}
            {!offer.is_sale && (
              <button
                type="button"
                className="pricing-offer-code"
                onClick={copy}
                title="Copy this code"
                aria-label={`Copy the code ${offer.code}`}
              >
                <span className="pricing-offer-code-text">{offer.code}</span>
                <span className="pricing-offer-copy">
                  {copied ? "Copied" : "Copy"}
                </span>
              </button>
            )}
            {cta && (
              <button
                type="button"
                className="btn small primary pricing-offer-apply"
                disabled={busy || applied}
                onClick={onCta}
              >
                {applied ? "✓ Applied" : busy ? "Applying…" : ctaLabel}
              </button>
            )}
          </>
        )}
      </div>

      {(ends || capped) && (
        <div className="pricing-offer-meta">
          {ends && <span className="pricing-offer-clock">⏳ {ends}</span>}
          {/* ⚠ NOT THE REDEEMED COUNT. How many are LEFT is the customer's
              question; how many have gone is the business's. */}
          {capped && <span>{offer.remaining} left</span>}
        </div>
      )}
    </article>
  );
}

/**
 * Every promoted offer, fetched here, for a screen that has no tier list of its
 * own — the landing page and the dashboard.
 *
 * ⚠ IT USES THE PUBLIC PRICE ROUTE, WHICH IS WHY THE LANDING PAGE CAN HAVE ONE.
 * `GET /billing/tiers` needs no token; it is the same request the pricing modal
 * makes, and the browser will usually have it cached by the time both are on
 * screen.
 *
 * ⚠ THERE IS NO "APPLY" HERE, AND THERE MUST NOT BE. `POST /billing/coupon` is
 * signed-in only, so an Apply button on the landing page would 401 in front of a
 * prospect — worse than no button. The code is shown and copyable, and the CTA
 * sends them where the code can actually be used.
 *
 * @param {object} p
 * @param {string} [p.className] Extra class on the wrapper, for placement.
 * @param {string} [p.ctaLabel]  The button's words on every card.
 * @param {Function} [p.onCta]   What that button does.
 * @param {number} [p.limit]     How many cards at most. Two is a promotion;
 *   five is a coupon site.
 */
export function OfferStrip({ className = "", ctaLabel = "", onCta = null, limit = 2 }) {
  const [offers, setOffers] = useState([]);
  const [tiers, setTiers] = useState([]);

  useEffect(() => {
    let cancelled = false;
    api
      .tiers()
      .then((r) => {
        if (cancelled) return;
        setOffers(r?.offers || []);
        setTiers(r?.tiers || []);
      })
      .catch(() => {
        // Fail CLOSED — see the file header. Nothing is drawn.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!offers.length) return null;

  return (
    <div className={`pricing-offers ${className}`} role="region" aria-label="Current offers">
      {offers.slice(0, limit).map((offer) => (
        <OfferCard
          key={offer.id || offer.code}
          offer={offer}
          tiers={tiers}
          ctaLabel={ctaLabel}
          onCta={onCta}
        />
      ))}
    </div>
  );
}

export default OfferCard;
