// AdminOverview.jsx — the dashboard: how many, how fast, and what just happened.
//
// ⚠ TWO CLOCKS, AND THE PANEL SAYS SO. Signup numbers are counted from
// `users.created_at`, which every account has ever had, so they are correct all
// the way back. Sign-in numbers come from the event log, which started the day
// the admin panel shipped — so a fortnight from now they are right, and today
// they read zero for a site that has been running for months. A dashboard that
// doesn't explain that difference is a dashboard that gets believed.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import { eventIcon, eventLabel, eventTone, eventDetail, kindLabel, money, num, timeAgo } from "./format.js";

export default function AdminOverview({ onOpenUser, onSeeAll }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .adminOverview()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  if (loading && !data) {
    return (
      <div className="admin-tiles">
        {/* The same shimmering placeholder the libraries use, so a slow
            dashboard looks like a slow library rather than a broken page. */}
        {Array.from({ length: 4 }).map((_, i) => (
          <div className="card admin-tile lib-ghost" key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="card placeholder">
        <p className="error">{error}</p>
        <button className="btn small" onClick={load}>
          Try again
        </button>
      </div>
    );
  }

  const d = data || {};
  const stores = d.stores || {};
  const kinds = Object.entries(d.jobs_by_kind || {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="admin-body">
      <div className="admin-section-head">
        <h2 className="admin-h2">At a glance</h2>
        <button className="btn ghost small" onClick={load} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* ⚠ THE DEV-SECRET WARNING SITS ABOVE EVERYTHING. If the API is signing
          tokens with the fallback secret then every session on it is forgeable,
          which makes every number below meaningless — and an admin panel is
          precisely where an operator will see this. */}
      {stores.jwt_secret_is_dev && (
        <div className="error admin-alarm">
          ⚠ This API is signing tokens with the built-in development secret.
          Anyone who knows it can mint a session for any account, including this
          one. Set <code>JWT_SECRET</code> in <code>.env</code> and restart.
        </div>
      )}

      <div className="admin-tiles">
        <Tile label="Accounts" value={num(d.users_total)} hint={`${num(d.users_admin)} admin`} />
        <Tile label="New today" value={num(d.signups_today)} hint={`${num(d.signups_7d)} this week`} />
        <Tile label="New this month" value={num(d.signups_30d)} hint="last 30 days" />
        <Tile
          label="Signed in"
          value={num(d.signed_in_7d)}
          hint="distinct, last 7 days"
          note="since the log began"
        />
        <Tile label="Sign-ins" value={num(d.logins_7d)} hint="last 7 days" />
        <Tile
          label="Failed sign-ins"
          value={num(d.failed_logins_7d)}
          hint="last 7 days"
          tone={d.failed_logins_7d > 0 ? "warn" : ""}
        />
        <Tile label="Disabled" value={num(d.users_disabled)} hint="locked accounts" />
        <Tile label="Projects" value={num(d.jobs_total)} hint="all workflows" />
      </div>

      <div className="admin-cols">
        <section className="card admin-card">
          <div className="admin-section-head">
            <h2 className="admin-h2">Signups — last 30 days</h2>
          </div>
          <Sparkline points={d.signups_daily || []} />
        </section>

        <section className="card admin-card">
          <div className="admin-section-head">
            <h2 className="admin-h2">What's being made</h2>
          </div>
          {kinds.length === 0 ? (
            <p className="muted tiny">Nothing generated yet.</p>
          ) : (
            <ul className="admin-kinds">
              {kinds.map(([kind, n]) => (
                <li className="admin-kind" key={kind}>
                  <span className="admin-kind-name">{kindLabel(kind)}</span>
                  {/* Proportional to the BIGGEST kind, not to the total: the
                      question is which workflow leads, and a share-of-total bar
                      makes six similar numbers into six similar slivers. */}
                  <span className="admin-kind-bar">
                    <span
                      className="admin-kind-fill"
                      style={{ width: `${Math.max(4, (n / kinds[0][1]) * 100)}%` }}
                    />
                  </span>
                  <span className="admin-kind-n">{num(n)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card admin-card">
        <div className="admin-section-head">
          <h2 className="admin-h2">Who's on what</h2>
        </div>
        <ul className="admin-kinds">
          {(d.users_by_tier || []).map((t) => (
            <li className="admin-kind" key={t.id}>
              <span className="admin-kind-name">
                {t.name}
                <span className="muted tiny"> · {t.monthly ? `${money(t.monthly)}/mo` : "free"}</span>
              </span>
              <span className="admin-kind-bar">
                <span
                  className="admin-kind-fill"
                  style={{
                    width: `${Math.max(
                      2,
                      (t.users / Math.max(1, d.users_total)) * 100
                    )}%`,
                  }}
                />
              </span>
              <span className="admin-kind-n">{num(t.users)}</span>
            </li>
          ))}
        </ul>
        <p className="muted tiny admin-foot">
          {/* ⚠ SAID OUT LOUD, because a row of tiers next to a row of prices
              looks exactly like revenue, and it is not — nothing has been
              charged. */}
          Counts only. No payment provider is connected, so these are the plans
          recorded against each account, not money taken.
        </p>
      </section>

      <section className="card admin-card">
        <div className="admin-section-head">
          <h2 className="admin-h2">Latest activity</h2>
          <button className="btn ghost small" onClick={() => onSeeAll?.("activity")}>
            See all
          </button>
        </div>
        {(d.recent_events || []).length === 0 ? (
          <p className="muted tiny">
            Nothing recorded yet. Registrations and sign-ins appear here from now on.
          </p>
        ) : (
          <ul className="admin-feed">
            {d.recent_events.map((ev) => (
              <li className="admin-feed-row" key={ev.id}>
                <span className={`admin-feed-ico ${eventTone(ev.type)}`}>
                  {eventIcon(ev.type)}
                </span>
                <span className="admin-feed-text">
                  <span className="admin-feed-what">{eventLabel(ev.type)}</span>
                  {ev.email && (
                    <button
                      type="button"
                      className="admin-link"
                      onClick={() => onOpenUser?.(ev.email)}
                      title={`Open ${ev.email}`}
                    >
                      {ev.email}
                    </button>
                  )}
                  {eventDetail(ev) && (
                    <span className="muted tiny"> — {eventDetail(ev)}</span>
                  )}
                </span>
                <span className="muted tiny admin-feed-when">{timeAgo(ev.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="muted tiny admin-foot">
        Accounts: <code>{stores.users}</code> · Projects: <code>{stores.jobs}</code> ·
        Activity log: <code>{stores.events}</code>. Signup counts are read from the
        accounts themselves and cover the whole history; sign-in counts start from
        when the activity log was added.
      </p>
    </div>
  );
}

function Tile({ label, value, hint, note, tone = "" }) {
  return (
    <div className="card admin-tile">
      <span className={`admin-tile-num ${tone}`}>{value}</span>
      <span className="admin-tile-label">{label}</span>
      {hint && <span className="muted tiny">{hint}</span>}
      {note && <span className="muted tiny admin-tile-note">{note}</span>}
    </div>
  );
}

/**
 * The signup chart. Bars, drawn with divs.
 *
 * ⚠ NO CHART LIBRARY, AND THAT IS THE HOUSE RULE, NOT A SHORTCUT.
 * `client/package.json` has exactly two runtime dependencies — `react` and
 * `react-dom` — and the timeline, the monitor and every other widget in this app
 * is hand-written for that reason. Thirty bars and a peak label do not justify
 * being the thing that breaks it.
 */
function Sparkline({ points }) {
  const peak = Math.max(1, ...points.map((p) => p.count));
  const total = points.reduce((sum, p) => sum + p.count, 0);

  return (
    <div className="admin-spark-wrap">
      <div className="admin-spark" role="img" aria-label={`${total} signups over ${points.length} days`}>
        {points.map((p) => (
          <span
            className={`admin-spark-bar ${p.count ? "on" : ""}`}
            key={p.day}
            // The bar has to be visible at zero too, or a quiet fortnight looks
            // like missing data rather than like a quiet fortnight.
            style={{ height: `${Math.max(3, (p.count / peak) * 100)}%` }}
            title={`${p.day}: ${p.count} signup${p.count === 1 ? "" : "s"}`}
          />
        ))}
      </div>
      <div className="admin-spark-foot">
        <span className="muted tiny">{points[0]?.day || ""}</span>
        <span className="muted tiny">
          {total} total · peak {peak}/day
        </span>
        <span className="muted tiny">{points[points.length - 1]?.day || ""}</span>
      </div>
    </div>
  );
}
