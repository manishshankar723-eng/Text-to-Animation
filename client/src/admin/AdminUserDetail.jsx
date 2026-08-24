// AdminUserDetail.jsx — one account: who they are, what they've made, what has
// happened to them, and the two levers an administrator has over it.
//
// ⚠ THE DESTRUCTIVE ACTIONS ASK, AND THE ASK IS INLINE. `.danger-zone` /
// `.danger-confirm` are the profile page's own pattern (home.css) — the app
// already decided that a dangerous action reveals its confirmation in place
// rather than dimming the screen for a modal, and doing it differently here
// would make the one screen where being sure matters most the one screen that
// behaves unlike the rest.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import ViewAsUser from "./ViewAsUser.jsx";
import {
  accessReason,
  eventDetail,
  eventIcon,
  eventLabel,
  eventTone,
  formatDate,
  formatDateTime,
  kindLabel,
  num,
  timeAgo,
} from "./format.js";

export default function AdminUserDetail({ email, onClose, onChanged, onDeleted }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [noteSaved, setNoteSaved] = useState(false);
  // Which destructive action is showing its confirmation: "" | "delete" | "role".
  const [confirm, setConfirm] = useState("");
  const [viewAs, setViewAs] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    setConfirm("");
    api
      .adminGetUser(email)
      .then((d) => {
        setData(d);
        setNote(d.admin_note || "");
        setNoteSaved(false);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [email]);

  useEffect(load, [load]);

  // Every mutation goes through here so that the busy flag, the error message
  // and the reload-both-panels step are written once rather than four times.
  async function act(name, fn) {
    setBusy(name);
    setError("");
    try {
      await fn();
      load();
      onChanged?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  if (loading && !data) {
    return (
      <div className="card admin-card admin-detail">
        <p className="muted">Loading…</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="card admin-card admin-detail">
        <p className="error">{error || "Could not load that account."}</p>
        <button className="btn small" onClick={load}>
          Try again
        </button>
      </div>
    );
  }

  const u = data.user;
  const isAdmin = u.account_role === "admin";
  const kinds = Object.entries(data.jobs_by_kind || {}).sort((a, b) => b[1] - a[1]);
  // The server refuses any administrator action aimed at the caller's own
  // account (see `_target` in admin.py). Knowing that here means the controls
  // are explained rather than simply erroring when pressed.
  const isSelf = data.is_self;

  return (
    <div className="card admin-card admin-detail">
      {viewAs && <ViewAsUser detail={data} onClose={() => setViewAs(false)} />}
      <div className="admin-detail-head">
        <div>
          <h2 className="admin-detail-name">
            {u.display_name || u.full_name || u.email.split("@")[0]}
          </h2>
          <p className="muted tiny">{u.email}</p>
        </div>
        <span className="admin-detail-head-actions">
          <button
            type="button"
            className="btn ghost small"
            onClick={() => setViewAs(true)}
            title="See the app the way this account sees it — read-only"
          >
            👁 View as
          </button>
          <button className="modal-close admin-detail-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </span>
      </div>

      <div className="admin-badges">
        {isAdmin && <span className="badge running">Admin</span>}
        {u.disabled ? (
          <span className="badge fail">Disabled</span>
        ) : (
          <span className="badge ok">Active</span>
        )}
        {data.tier_name && <span className="chip">{data.tier_name}</span>}
        {data.role_locked && (
          <span className="badge queued" title="Pinned by the ADMIN_EMAILS environment variable">
            Pinned in .env
          </span>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      <dl className="admin-kv">
        <Row k="Registered" v={`${formatDate(u.created_at)} · ${timeAgo(u.created_at)}`} />
        <Row
          k="Last seen"
          v={u.last_login_at ? `${formatDateTime(u.last_login_at)} · ${timeAgo(u.last_login_at)}` : "never"}
        />
        <Row k="Sign-ins" v={num(u.login_count)} />
        {u.company && <Row k="Company" v={u.company} />}
        {data.role_title && <Row k="Job title" v={data.role_title} />}
        {data.timezone && <Row k="Timezone" v={data.timezone} />}
        {(data.default_style || data.default_genre || data.default_aspect_ratio) && (
          <Row
            k="Creative defaults"
            v={[data.default_style, data.default_genre, data.default_aspect_ratio]
              .filter(Boolean)
              .join(" · ")}
          />
        )}
      </dl>

      <h3 className="admin-h3">Work</h3>
      {kinds.length === 0 ? (
        <p className="muted tiny">Nothing generated yet.</p>
      ) : (
        <ul className="admin-chips">
          {kinds.map(([kind, n]) => (
            <li className="chip" key={kind}>
              {kindLabel(kind)} · {num(n)}
            </li>
          ))}
        </ul>
      )}

      <h3 className="admin-h3">Usage this month</h3>
      <Usage usage={data.usage} />

      <h3 className="admin-h3">Plan</h3>
      <div className="admin-actions">
        <select
          className="admin-select"
          value={data.tier || ""}
          disabled={isSelf || busy === "tier"}
          onChange={(e) => act("tier", () => api.adminSetUserTier(email, e.target.value))}
          aria-label="Billing tier"
        >
          {(data.tiers || []).map((t) => (
            <option value={t.id} key={t.id}>
              {t.name}
              {t.archived ? " (archived)" : ""}
            </option>
          ))}
        </select>
        {busy === "tier" && <span className="muted tiny">Saving…</span>}
      </div>
      <p className="muted tiny">
        {/* Said plainly, because a dropdown that looks like a billing control
            and takes no money is exactly the thing somebody will assume charges
            a card. */}
        {isSelf
          ? "You can't change your own plan from here."
          : "This records which plan they're on. It does not take a payment — no payment provider is connected yet."}
      </p>

      <h3 className="admin-h3">Access</h3>
      {/* ⚠ EVERY ROW SAYS WHY, NOT JUST WHETHER. "Veo is off for this customer"
          is where a support ticket starts; "off because the feature is hidden
          for everyone" is where it ends. The reason comes from the same
          resolver that decides the answer, so the two cannot disagree. */}
      <ul className="admin-access">
        {(data.feature_meta || []).map((f) => {
          const state = (data.feature_states || {})[f.key] || {};
          return (
            <li className={`admin-access-row ${state.on ? "on" : "off"}`} key={f.key}>
              <span className="admin-access-ico">{f.icon}</span>
              <span className="admin-access-text">
                <span className="admin-access-label">{f.label}</span>
                <span className="muted tiny">{accessReason(state)}</span>
              </span>
              <span className="admin-segment admin-access-seg" role="group" aria-label={f.label}>
                {/* Three states, and the middle one is not "off" — it is
                    "whatever the rollout says". Collapsing them would make
                    "remove this exception" and "ban them from it" one button. */}
                {[
                  ["on", true, "Force on"],
                  ["auto", null, "Follow the rollout rule"],
                  ["off", false, "Force off"],
                ].map(([id, value, title]) => {
                  const current =
                    state.source === "override" ? (state.on ? "on" : "off") : "auto";
                  return (
                    <button
                      key={id}
                      type="button"
                      className={`admin-seg-btn ${current === id ? "on" : ""}`}
                      disabled={busy === `ov:${f.key}`}
                      title={title}
                      onClick={() =>
                        current !== id &&
                        act(`ov:${f.key}`, () => api.adminSetOverride(email, f.key, value))
                      }
                    >
                      {id === "auto" ? "Auto" : id === "on" ? "On" : "Off"}
                    </button>
                  );
                })}
              </span>
            </li>
          );
        })}
      </ul>

      <h3 className="admin-h3">Private note</h3>
      <textarea
        className="admin-note"
        rows={3}
        value={note}
        maxLength={2000}
        placeholder="Only administrators can read this. The customer never sees it."
        onChange={(e) => {
          setNote(e.target.value);
          setNoteSaved(false);
        }}
        disabled={isSelf}
      />
      <div className="admin-actions">
        <button
          className="btn small"
          disabled={busy === "note" || note === (data.admin_note || "") || isSelf}
          onClick={() =>
            act("note", async () => {
              await api.adminSetNote(email, note);
              setNoteSaved(true);
            })
          }
        >
          {busy === "note" ? "Saving…" : "Save note"}
        </button>
        {noteSaved && <span className="muted tiny">Saved.</span>}
      </div>

      <h3 className="admin-h3">History</h3>
      {(data.recent_events || []).length === 0 ? (
        <p className="muted tiny">
          Nothing recorded for this account yet — the activity log only covers
          what has happened since it was added.
        </p>
      ) : (
        <ul className="admin-feed">
          {data.recent_events.map((ev) => (
            <li className="admin-feed-row" key={ev.id}>
              <span className={`admin-feed-ico ${eventTone(ev.type)}`}>
                {eventIcon(ev.type)}
              </span>
              <span className="admin-feed-text">
                <span className="admin-feed-what">{eventLabel(ev.type)}</span>
                {/* ⚠ WHO DID IT, when that isn't the account itself. An admin
                    action names the customer in `email` and the administrator
                    in `actor`, and the whole value of this log during an
                    incident is that it can tell those two apart. */}
                {ev.actor && ev.actor !== ev.email && (
                  <span className="muted tiny"> by {ev.actor}</span>
                )}
                {eventDetail(ev) && <span className="muted tiny"> — {eventDetail(ev)}</span>}
                {ev.ip && <span className="muted tiny admin-feed-ip">{ev.ip}</span>}
              </span>
              <span className="muted tiny admin-feed-when" title={formatDateTime(ev.at)}>
                {timeAgo(ev.at)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* ---- The levers ---- */}
      <div className="danger-zone admin-danger">
        <h3 className="danger-title">Account actions</h3>

        {isSelf ? (
          <p className="muted tiny">
            This is your own account. Administrator actions can't be applied to
            it — use your profile page to change your password or close it. That
            rule is what stops the last administrator locking everybody out.
          </p>
        ) : (
          <>
            <div className="admin-actions">
              <button
                className="btn small"
                disabled={busy === "disabled"}
                onClick={() =>
                  act("disabled", () => api.adminSetDisabled(email, !u.disabled))
                }
                title={
                  u.disabled
                    ? "Let this account sign in again"
                    : "Block sign-in and end any live session within 30 seconds"
                }
              >
                {busy === "disabled"
                  ? "Working…"
                  : u.disabled
                  ? "🔓 Enable account"
                  : "🔒 Disable account"}
              </button>

              <button
                className="btn small"
                disabled={busy === "role" || data.role_locked}
                onClick={() => setConfirm(confirm === "role" ? "" : "role")}
                title={
                  data.role_locked
                    ? "Pinned as an administrator by ADMIN_EMAILS — change that and restart the API"
                    : isAdmin
                    ? "Take away administrator access"
                    : "Give full administrator access"
                }
              >
                {isAdmin ? "⚑ Revoke admin" : "⚑ Make admin"}
              </button>
            </div>

            {confirm === "role" && (
              <div className="danger-confirm">
                <p className="tiny">
                  {isAdmin
                    ? `${u.email} will lose access to this panel within 30 seconds.`
                    : `${u.email} will be able to see every account, disable them, and grant this same access to others.`}
                </p>
                <div className="admin-actions">
                  <button
                    className="btn small danger-btn"
                    disabled={busy === "role"}
                    onClick={() =>
                      act("role", () =>
                        api.adminSetRole(email, isAdmin ? "user" : "admin")
                      )
                    }
                  >
                    {busy === "role" ? "Working…" : "Yes, change the role"}
                  </button>
                  <button className="btn ghost small" onClick={() => setConfirm("")}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <div className="admin-actions">
              <button
                className="btn small danger-btn"
                onClick={() => setConfirm(confirm === "delete" ? "" : "delete")}
              >
                🗑 Delete account
              </button>
            </div>

            {confirm === "delete" && (
              <div className="danger-confirm">
                <p className="tiny">
                  Permanently deletes the login for <strong>{u.email}</strong>.
                  {/* Said plainly, because the opposite is what people assume a
                      Delete button does — and finding out afterwards that a
                      customer's boards are still on the server (or that they
                      aren't) is not a discovery to make later. */}{" "}
                  Their {num(u.projects)} project
                  {u.projects === 1 ? "" : "s"} are <strong>not</strong> deleted —
                  the work stays on the server without an owner who can sign in
                  to reach it. This cannot be undone.
                </p>
                <div className="admin-actions">
                  <button
                    className="btn small danger-btn"
                    disabled={busy === "delete"}
                    onClick={async () => {
                      setBusy("delete");
                      setError("");
                      try {
                        await api.adminDeleteUser(email);
                        onDeleted?.();
                      } catch (e) {
                        setError(e.message);
                        setBusy("");
                      }
                    }}
                  >
                    {busy === "delete" ? "Deleting…" : "Yes, delete this account"}
                  </button>
                  <button className="btn ghost small" onClick={() => setConfirm("")}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * This month's counters against the tier's limits.
 *
 * ⚠ THE TWO KINDS OF LIMIT ARE SHOWN DIFFERENTLY, because they mean different
 * things. A COUNTER (projects, images) gets a bar — it fills up and resets. A
 * PER-REQUEST CAP (shots per project, script pages) gets a plain number, because
 * there is nothing to fill: it applies afresh to every request.
 */
function Usage({ usage }) {
  if (!usage?.period) return <p className="muted tiny">No usage recorded.</p>;
  const counters = usage.counters || {};
  const limits = usage.limits || {};

  return (
    <>
      <ul className="admin-usage">
        {Object.entries(counters).map(([key, used]) => {
          const limit = limits[key];
          const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
          return (
            <li className="admin-usage-row" key={key}>
              <span className="admin-usage-name">{key.replace(/_/g, " ")}</span>
              <span className="admin-kind-bar">
                {/* Unlimited draws no bar at all rather than an empty one — an
                    empty bar reads as "none used of a lot", which is a limit. */}
                {limit ? (
                  <span
                    className={`admin-kind-fill ${used >= limit ? "over" : ""}`}
                    style={{ width: `${Math.max(2, pct)}%` }}
                  />
                ) : null}
              </span>
              <span className="admin-kind-n">
                {used}
                {limit ? ` / ${limit}` : " / ∞"}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="muted tiny">
        {usage.period} · {(usage.text_tokens || 0).toLocaleString()} text tokens
        {usage.cost_usd_est > 0 && ` · ~$${usage.cost_usd_est} est.`}
        {/* The same warning `ai_usage` carries everywhere else it is shown. */}
        {usage.cost_usd_est > 0 && " (advisory)"}
      </p>
      {(limits.shots_per_project || limits.story_pages) && (
        <p className="muted tiny">
          Per request:{" "}
          {[
            limits.shots_per_project && `${limits.shots_per_project} shots per project`,
            limits.story_pages && `${limits.story_pages} script pages`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
      {usage.not_enforced && Object.keys(usage.not_enforced).length > 0 && (
        <p className="muted tiny">
          {/* ⚠ SAID OUT LOUD. A limit nothing checks is marketing copy, and an
              admin panel that lists it beside enforced ones invites somebody to
              rely on it. */}
          Not enforced by the app: {Object.keys(usage.not_enforced).join(", ")}.
        </p>
      )}
    </>
  );
}

function Row({ k, v }) {
  return (
    <>
      <dt className="muted tiny">{k}</dt>
      <dd>{v}</dd>
    </>
  );
}
