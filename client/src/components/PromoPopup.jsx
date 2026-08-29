import { useEffect, useState } from "react";
import * as api from "../api.js";
// ⚠ THE SAME TWO HELPERS THE OFFER CARD USES, not a second reading of the same
// two fields. "Ends in 6 days" and "every plan · monthly or yearly" are
// sentences a customer may see twice in one session — on the pricing page and
// here — and two spellings of one fact is how they stop trusting either.
import { scopeOf, timeLeft } from "./OfferCard.jsx";

// PromoPopup — the offer that comes to YOU: a card that slides in from the
// right of the Explore page, carrying whatever discount is currently running.
//
// ⚠ HOW THIS IS DIFFERENT FROM `OfferStrip`, because two ways of showing one
// coupon needs a reason. The strip is a row a customer scrolls past on Home and
// on the pricing page — it waits to be found. This is shown to somebody who did
// NOT go looking, on the page they land on, which is the only way a coupon
// reaches a customer who never opens the pricing modal. Same data, same words,
// opposite manners.
//
// ⚠ AND BECAUSE IT INTERRUPTS, IT HAS TO BEHAVE. Four rules, all of them here
// rather than in the stylesheet:
//
//   1. ONE CARD, EVER. It takes the FIRST promoted offer — `promoted_offers()`
//      sorts deepest discount first — so two live offers are still one card.
//   2. CLOSING IT MEANS CLOSED. The dismissal is remembered per browser, keyed
//      by the offer's id, so a card someone shut does not come back tomorrow —
//      and a NEW offer still gets its turn, which a single "seen the popup"
//      flag would have silently killed for ever.
//   3. IT ARRIVES AFTER THE PAGE DOES. `ENTER_MS` of delay, so it slides onto a
//      drawn page instead of racing it.
//   4. IT IS NOT A MODAL. Nothing is dimmed, nothing is trapped, the page
//      behind it works — Escape closes it, and so does the ✕.
//
// ⚠ AND IT FAILS CLOSED, like the strip does. No offers, an unreadable
// `/billing/tiers`, an offer an administrator has switched off the pop-up for —
// all of them render NOTHING. Never a placeholder, never "loading your
// discount".

// How long after the page draws the card arrives. Long enough that it reads as
// an arrival rather than as part of the layout, short enough that nobody has
// started reading something else.
const ENTER_MS = 900;

// Where the dismissal is remembered. ⚠ THE OFFER ID IS IN THE KEY — see rule 2.
const SEEN_KEY = "cas_promo_seen";

function seenId() {
  try {
    return localStorage.getItem(SEEN_KEY) || "";
  } catch {
    // Private mode / storage disabled. The card shows; being shown twice is a
    // far smaller failure than crashing the page it sits on.
    return "";
  }
}

function remember(id) {
  try {
    localStorage.setItem(SEEN_KEY, id);
  } catch {
    // As above — nothing here is worth an exception.
  }
}

/**
 * @param {object} p
 * @param {Function} [p.onCta] What the button does — the pricing modal. With no
 *   handler the button is not drawn: a promotion whose only action does nothing
 *   is worse than one that just states the code.
 */
export default function PromoPopup({ onCta = null }) {
  const [offer, setOffer] = useState(null);
  const [tiers, setTiers] = useState([]);
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    api
      .tiers()
      .then((r) => {
        if (cancelled) return;
        // Rule 1: the first promoted offer, and only if this one still wants a
        // pop-up. ⚠ `popup !== false` RATHER THAN `popup` — an offer stored
        // before this field existed carries no key, and the server reads that
        // absence as yes (see `offers.is_popup`); reading it as no here would
        // have made the two disagree about every offer already in the database.
        const first = (r?.offers || []).find((o) => o.popup !== false);
        if (!first || seenId() === first.id) return;
        setTiers(r?.tiers || []);
        setOffer(first);
        timer = window.setTimeout(() => setShown(true), ENTER_MS);
      })
      .catch(() => {
        // Fail CLOSED — see the file header. Nothing is drawn.
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  // Rule 4: Escape closes it. Bound only while it is on screen, so this screen
  // never eats an Escape meant for something else.
  useEffect(() => {
    if (!shown) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, offer]);

  function close() {
    if (offer) remember(offer.id);
    setShown(false);
    // Left mounted for the length of the slide-out, then emptied — unmounting
    // on the click would make it vanish rather than leave.
    window.setTimeout(() => setOffer(null), 260);
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(offer.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // No clipboard permission — the code is on screen and selectable, which
      // is the whole reason it is rendered as text and not as a picture.
    }
  }

  if (!offer) return null;

  const ends = timeLeft(offer.ends_at);
  const capped = offer.remaining !== null && offer.remaining !== undefined;
  // Everything below is an administrator's typing where they did any, and the
  // offer's own facts where they did not — see the placeholders in AdminSales.
  const title =
    offer.popup_title ||
    offer.label ||
    (offer.is_sale ? "Site-wide sale" : "Discount code");
  const lines = offer.popup_lines?.length
    ? offer.popup_lines
    : [scopeOf(offer, tiers)];
  const cta = offer.popup_cta || "View plans";

  return (
    <aside
      className={`promo-pop ${shown ? "in" : ""}`}
      role="dialog"
      aria-label="Current offer"
    >
      <button
        type="button"
        className="promo-close"
        onClick={close}
        title="Close"
        aria-label="Close this offer"
      >
        ✕
      </button>

      <div className="promo-head">
        <div className="promo-head-text">
          <span className="promo-kicker">
            {offer.is_sale ? "Sale" : "Limited offer"}
          </span>
          <h2 className="promo-title">{title}</h2>
        </div>
        {/* ⚠ THE DISCOUNT IS THE ARTWORK. The reference puts a drawn gift box
            here; this app ships no illustration and a stock glyph would say
            nothing. "20% off" is both the picture and the point, and it is
            already a sentence the server writes (`offers.summary`). */}
        <span className="promo-cut" aria-hidden="true">
          {offer.summary}
        </span>
      </div>

      <div className="promo-body">
        <ul className="promo-lines">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>

        {offer.popup_note && (
          <p className="promo-note">
            <span aria-hidden="true">ⓘ</span> {offer.popup_note}
          </p>
        )}

        {(ends || capped) && (
          <p className="promo-meta">
            {ends && <span>⏳ {ends}</span>}
            {/* How many are LEFT is the customer's question; how many have gone
                is the business's. Same split the offer card makes. */}
            {capped && <span>{offer.remaining} left</span>}
          </p>
        )}

        <div className="promo-actions">
          {/* No code on a sale — there is nothing to type, so there is no box
              pretending there is. */}
          {!offer.is_sale && (
            <button
              type="button"
              className="promo-code"
              onClick={copy}
              title="Copy this code"
              aria-label={`Copy the code ${offer.code}`}
            >
              <span className="promo-code-text">{offer.code}</span>
              <span className="promo-code-copy">
                {copied ? "Copied" : "Copy"}
              </span>
            </button>
          )}
          {onCta && (
            <button className="btn primary promo-cta" onClick={onCta}>
              {cta} →
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
