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
import { GrowText } from "./fields.jsx";
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

// ⚠ THE SERVER REFUSES A THIRTEENTH — `TierUpdate.bullets` in `admin.py` is
// `max_length=12`, so the button has to stop where the route stops rather than
// let somebody type a line that comes back as a 422.
const MAX_BULLETS = 12;

// ⚠ A PLAN IS A RUNG, NOT A BASKET, AND THESE TWO FUNCTIONS ARE THE WHOLE
// REASON THE DERIVED COLUMN CAN BE EDITED AT ALL. A feature names the LOWEST
// tier that gets it (`min_tier`), so every tier above that one gets it too —
// which means "put this in Starter" and "take this out of Starter" are both a
// single write of `min_tier`, and both of them ripple:
//
//   adding    → min_tier = this tier   → in this tier AND every dearer one
//   removing  → min_tier = the next tier up → out of this tier AND every cheaper one
//
// ⚠ ADDING TO THE CHEAPEST TIER CLEARS THE REQUIREMENT INSTEAD OF NAMING IT.
// "no requirement" is not the same as "requires the lowest tier": the lowest
// tier can be archived or re-ranked later, and a feature pinned to it would
// then follow it rather than staying free. The Features screen's picker says
// the same thing in its own comment.
function addTierFor(ladder, tierId) {
  return ladder[0] === tierId ? "" : tierId;
}

// The tier one rung up, or "" when there isn't one — the top tier cannot have a
// feature taken off it this way, because there is no higher requirement to set.
function nextTierUp(ladder, tierId) {
  const i = ladder.indexOf(tierId);
  return i >= 0 && i + 1 < ladder.length ? ladder[i + 1] : "";
}

// One marketing bullet, in the shape the wire uses, with both flags filled in.
// ⚠ THE SEEDED CATALOGUE LEAVES `strong` OFF ENTIRELY on most lines and `ok`
// off on all of them, and a control bound to `undefined` is a React warning and
// a toggle that needs two clicks the first time. Trimmed here as well as on the
// way out, so "did this change?" compares like with like.
function normaliseBullets(list) {
  return (list || []).map((b) => ({
    text: (b.text || "").trim(),
    ok: b.ok !== false,
    strong: !!b.strong,
  }));
}

export default function AdminPricing() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  // ⚠ BUMPED WHEN A SAVE FAILS, AND USED AS PART OF EVERY CARD'S `key`, WHICH
  // REMOUNTS THEM. Each card keeps what is being typed in local state, so a
  // rejected PATCH would otherwise leave a line on screen that the server does
  // not have — the reload puts the truth in `data` and nothing shows it. A
  // remount is the whole recovery: local state is thrown away and every field
  // goes back to what came off the wire.
  const [rev, setRev] = useState(0);

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
      setRev((r) => r + 1);
      load();
    } finally {
      setBusy("");
    }
  }

  // ⚠ THIS RENAMES THE FEATURE, NOT A LINE ON ONE CARD. The right-hand column
  // is derived: what it prints is each feature's own `label`, the same string the
  // workflow rail and every refusal message use. So the rename is saved by the
  // SCREEN and written into every tier that lists that feature — patching only
  // the card it was typed on would leave the other three showing the old name
  // until a reload, and make it look like a per-tier caption, which it is not.
  async function renameFeature(key, label) {
    setError("");
    try {
      const saved = await api.adminUpdateFeature(key, { label });
      const now = saved?.label || label;
      setData((d) => ({
        ...d,
        tiers: d.tiers.map((t) => ({
          ...t,
          includes: t.includes.map((f) => (f.key === key ? { ...f, label: now } : f)),
        })),
      }));
    } catch (e) {
      setError(e.message);
      setRev((r) => r + 1);
      load();
    }
  }

  // ⚠ ONE WRITE, EVERY CARD CHANGES, SO THIS RELOADS RATHER THAN PATCHING A ROW.
  // `includes` is derived from `min_tier` for every tier at once (see the note
  // on `addTierFor`), so there is no honest way to mend the four lists in place
  // — the screen would be showing four answers to a question the server has one
  // answer to. `load()` keeps `data`, so nothing blanks while it runs.
  async function setMinTier(key, tierId) {
    setBusy("*");
    setError("");
    try {
      await api.adminSetMinTier(key, tierId);
      load();
    } catch (e) {
      setError(e.message);
      setRev((r) => r + 1);
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
  // Cheapest first, archived left out — `/admin/tiers` already sorts by rank,
  // and `tier_ids` is the same list the Features screen's picker offers.
  const ladder = (data.tiers || []).filter((t) => !t.archived).map((t) => t.id);
  const tierNames = Object.fromEntries((data.tiers || []).map((t) => [t.id, t.name]));
  // ⚠ ASSEMBLED FROM WHAT THE TIERS ALREADY ANSWERED, not from a second request
  // to `/admin/features`. The top tier meets every requirement, so the union of
  // the four `includes` lists IS the catalogue as far as this screen is
  // concerned — and it cannot disagree with the lists drawn beside it.
  const catalog = [];
  const seen = new Set();
  for (const t of data.tiers || [])
    for (const f of t.includes || [])
      if (!seen.has(f.key)) {
        seen.add(f.key);
        catalog.push(f);
      }
  catalog.sort((a, b) => a.label.localeCompare(b.label));

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
            key={`${t.id}:${rev}`}
            tier={t}
            symbol={symbol}
            currency={data.currency}
            isDefault={t.id === data.default_tier}
            busy={busy === t.id || busy === "*"}
            ladder={ladder}
            tierNames={tierNames}
            catalog={catalog}
            onSave={save}
            onRenameFeature={renameFeature}
            onSetMinTier={setMinTier}
          />
        ))}
      </div>
    </div>
  );
}

function TierCard({
  tier,
  symbol,
  currency,
  isDefault,
  busy,
  ladder,
  tierNames,
  catalog,
  onSave,
  onRenameFeature,
  onSetMinTier,
}) {
  const [monthly, setMonthly] = useState(toMajor(tier.monthly));
  const [yearly, setYearly] = useState(toMajor(tier.yearly));
  const [compare, setCompare] = useState(toMajor(tier.compare_at));
  const [name, setName] = useState(tier.name);
  const [blurb, setBlurb] = useState(tier.blurb);
  const [badge, setBadge] = useState(tier.badge || "");
  const [bullets, setBullets] = useState(() => normaliseBullets(tier.bullets));
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

  // ⚠ THE BULLETS ARE ONE FIELD, NOT ONE FIELD PER LINE. `bullets` is a list on
  // the tier, so "line 3 changed" is not something the route can be told — the
  // only patch it takes is the whole list. Hence the local copy above: typing
  // edits it, and a tick, a bold, a delete or leaving a text box posts all of it.
  //
  // Empty lines are dropped on the way out. A row you added and never typed into
  // is not copy, and the server counts it against the twelve.
  function commitBullets(next) {
    setBullets(next);
    const clean = next
      .map((b) => ({ ...b, text: b.text.trim() }))
      .filter((b) => b.text);
    if (JSON.stringify(clean) !== JSON.stringify(normaliseBullets(tier.bullets)))
      onSave(tier.id, { bullets: clean });
  }

  // Local only — every keystroke would otherwise be a PATCH of the whole list.
  function editBullet(i, patch) {
    setBullets((list) => list.map((b, n) => (n === i ? { ...b, ...patch } : b)));
  }

  // A flag is a decision, not a draft, so these save the moment they are clicked.
  function toggleBullet(i, patch) {
    commitBullets(bullets.map((b, n) => (n === i ? { ...b, ...patch } : b)));
  }

  const saving =
    tier.monthly > 0 ? Math.round((1 - tier.yearly / tier.monthly) * 100) : 0;

  // What the derived column's two controls write. `upOne` is "" on the dearest
  // plan, which is what disables its remove buttons.
  const upOne = nextTierUp(ladder, tier.id);
  const addTier = addTierFor(ladder, tier.id);
  const here = new Set((tier.includes || []).map((f) => f.key));
  const missing = catalog.filter((f) => !here.has(f.key));

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
          {/* ⚠ FREE TEXT, AND THE ONLY WORDS A CUSTOMER ACTUALLY READS. Nothing
              checks a line here against what the flags grant — that is what the
              column on the right is for. The tick is a toggle, not a decoration:
              a line with a cross is the one the card prints as NOT included. */}
          <ul className="pricing-features admin-tier-bullets">
            {bullets.map((b, i) => (
              <li key={i} className={b.ok ? "" : "no"}>
                <button
                  type="button"
                  className="pricing-ic admin-bullet-ic"
                  disabled={busy}
                  onClick={() => toggleBullet(i, { ok: !b.ok })}
                  title={
                    b.ok
                      ? "Printed as included. Click for a cross."
                      : "Printed as NOT included. Click for a tick."
                  }
                >
                  {b.ok ? "✓" : "✕"}
                </button>
                <GrowText
                  className={`admin-bullet-text ${b.strong ? "strong" : ""}`}
                  value={b.text}
                  maxLength={120}
                  disabled={busy}
                  placeholder="Type what the card should say…"
                  onChange={(e) => editBullet(i, { text: e.target.value })}
                  onBlur={() => commitBullets(bullets)}
                  aria-label={`Card line ${i + 1}`}
                />
                <span className="admin-bullet-tools">
                  <button
                    type="button"
                    className={`admin-bullet-btn ${b.strong ? "on" : ""}`}
                    disabled={busy}
                    onClick={() => toggleBullet(i, { strong: !b.strong })}
                    title="Print this line in bold on the pricing card"
                    aria-pressed={b.strong}
                  >
                    B
                  </button>
                  <button
                    type="button"
                    className="admin-bullet-btn"
                    disabled={busy}
                    onClick={() => commitBullets(bullets.filter((_, n) => n !== i))}
                    title="Remove this line from the card"
                    aria-label={`Remove card line ${i + 1}`}
                  >
                    ✕
                  </button>
                </span>
              </li>
            ))}
          </ul>
          <div className="admin-bullet-add">
            <button
              className="btn ghost small"
              disabled={busy || bullets.length >= MAX_BULLETS}
              onClick={() =>
                setBullets((list) => [...list, { text: "", ok: true, strong: false }])
              }
              title={
                bullets.length >= MAX_BULLETS
                  ? `${MAX_BULLETS} lines is the most a card takes.`
                  : "Add another line to this card"
              }
            >
              + Add a line
            </button>
            {/* The bold and remove buttons only appear on the line you are
                pointing at, so the sentence that says so is not optional. */}
            <span className="muted tiny">
              {bullets.length >= MAX_BULLETS
                ? `${MAX_BULLETS} lines is the most a card takes.`
                : "Saved when you click away. Point at a line for bold and remove. An empty line isn’t saved."}
            </span>
          </div>
        </div>
        <div>
          <h4 className="admin-h4">Actually unlocks</h4>
          {/* ⚠ STILL DERIVED. Nothing on this side is stored on the tier: every
              line here is a feature whose `min_tier` this tier meets, and the
              three controls all write that one field on the FEATURE. That is
              why they ripple to the other cards, and why the note underneath
              says so in the words an administrator will think in. */}
          <ul className="admin-tier-unlocks">
            {tier.includes.length === 0 ? (
              <li className="muted tiny">Nothing — every feature needs a higher tier.</li>
            ) : (
              tier.includes.map((f) => (
                <li key={f.key} className="muted tiny">
                  <UnlockName feature={f} disabled={busy} onRename={onRenameFeature} />
                  <span className="admin-unlock-tools">
                    <button
                      type="button"
                      className="admin-bullet-btn"
                      disabled={busy || !upOne}
                      onClick={() => onSetMinTier(f.key, upOne)}
                      title={
                        upOne
                          ? `Take "${f.label}" off ${tier.name} — it will need ` +
                            `${tierNames[upOne] || upOne}, so it also goes from every cheaper plan.`
                          : `${tier.name} is the dearest plan, so there is no higher ` +
                            `requirement to give this. Hide the feature on Features instead.`
                      }
                      aria-label={`Take ${f.label} off ${tier.name}`}
                    >
                      ✕
                    </button>
                  </span>
                </li>
              ))
            )}
          </ul>

          {/* One control, and it applies on pick — the same "a switch that needs
              confirming is a switch people leave half-thrown" rule the Features
              screen is built on. It resets to its own label because it is an
              ACTION, not a value: what this tier holds is the list above it. */}
          {missing.length > 0 && (
            <select
              className="admin-select admin-unlock-add"
              value=""
              disabled={busy}
              onChange={(e) => e.target.value && onSetMinTier(e.target.value, addTier)}
              aria-label={`Add a feature to ${tier.name}`}
            >
              <option value="">+ Add a feature to this plan…</option>
              {missing.map((f) => (
                <option value={f.key} key={f.key}>
                  {f.label}
                </option>
              ))}
            </select>
          )}

          <p className="muted tiny">
            {/* ⚠ THE RIPPLE IS THE FIRST THING TO SAY, NOT A FOOTNOTE. Somebody
                taking a line off Starter to sell it dearer needs to know before
                they click that Trial loses it too. */}
            A plan is a rung, not a basket: <strong>adding</strong> puts a feature
            in this plan and every dearer one, <strong>taking one off</strong>{" "}
            removes it from this plan and every cheaper one. Renaming a line
            renames that feature <strong>everywhere in the app</strong> — it is
            the feature's own name, not copy belonging to this card. All three
            write what <strong>Features → Needs at least</strong> reads.
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

// One line of the derived column, as an editable box.
//
// ⚠ WHAT THIS EDITS IS THE FEATURE'S OWN `label` — the string the workflow rail
// prints, the one every "you need a higher plan" message names — NOT a caption
// that belongs to this tier. Two tiers listing the same feature therefore show
// the same words and both change when either is typed into, which is why the
// PATCH is the screen's job (`renameFeature`) and not the card's.
//
// ⚠ AND IT FOLLOWS `feature.label` AFTERWARDS. Rename "Veo video renders" on the
// Starter card and the same line on Pro is a different `UnlockName` with its own
// state; without the effect below it would keep showing the old name until the
// page was reloaded.
function UnlockName({ feature, disabled, onRename }) {
  const [text, setText] = useState(feature.label);

  useEffect(() => setText(feature.label), [feature.label]);

  function commit() {
    const next = text.trim();
    // A feature with no name is unreadable on every screen that prints it, and
    // the server falls back to the raw key. Put the old name back instead.
    if (!next) {
      setText(feature.label);
      return;
    }
    if (next !== feature.label) onRename(feature.key, next);
  }

  return (
    <GrowText
      className="admin-unlock-name"
      value={text}
      maxLength={80}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      aria-label={`Name of ${feature.label}`}
      title="The feature's name, as the whole app shows it"
    />
  );
}
