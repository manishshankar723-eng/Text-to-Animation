// AdminActivity.jsx — the activity log, filterable. Registrations, sign-ins,
// failed sign-ins, and every administrative change with who made it.
//
// The type list comes from `GET /admin/meta` rather than being written out
// here: the server already knows what types it writes, and a copy in the client
// is a second place to edit every time one is added — which is exactly the kind
// of drift that leaves a filter quietly unable to find a whole class of event.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import {
  eventDetail,
  eventIcon,
  eventLabel,
  eventTone,
  formatDateTime,
  timeAgo,
} from "./format.js";

// The windows worth offering. `null` is "everything the log still holds", which
// is bounded by retention (180 days by default) rather than unbounded.
const RANGES = [
  { id: "1", label: "Today", days: 1 },
  { id: "7", label: "7 days", days: 7 },
  { id: "30", label: "30 days", days: 30 },
  { id: "", label: "All", days: null },
];

// Groups, so the filter is three buttons rather than ten checkboxes. An
// administrator asks "what are people doing" or "what have WE done", almost
// never "show me note-saves specifically".
const GROUPS = [
  { id: "all", label: "Everything", match: () => true },
  { id: "user", label: "People", match: (t) => t.startsWith("user.") },
  { id: "admin", label: "Admin actions", match: (t) => t.startsWith("admin.") },
  {
    id: "failed",
    label: "Failed sign-ins",
    match: (t) => t === "user.login_failed",
  },
];

export default function AdminActivity({ onOpenUser }) {
  const [types, setTypes] = useState([]);
  // ⚠ SEPARATE FROM `types.length`, and it has to be. A FAILED meta call also
  // leaves the list empty, and "haven't asked yet" and "asked, got nothing" need
  // different answers below — conflating them wedges the three narrow filters on
  // "Loading…" for ever the moment /admin/meta is unavailable.
  const [metaReady, setMetaReady] = useState(false);
  const [group, setGroup] = useState("all");
  const [range, setRange] = useState("7");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // The known type list, once. A failure here is not fatal — an empty list
  // means "no type filter", and the feed still renders everything.
  useEffect(() => {
    api
      .adminMeta()
      .then((m) => setTypes(m.event_types || []))
      .catch(() => setTypes([]))
      .finally(() => setMetaReady(true));
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    const matcher = GROUPS.find((g) => g.id === group)?.match || (() => true);
    // ⚠ AN EMPTY ARRAY MEANS "DON'T FILTER", not "match nothing". Before the
    // meta call answers, `types` is empty and every group would otherwise send
    // no types at all — which the API reads as no filter, which is the right
    // answer for "Everything" and the wrong one for the other three. Sending
    // the group's types only once we know them keeps those two cases apart.
    const wanted = group === "all" ? [] : types.filter(matcher);
    if (group !== "all" && !metaReady) {
      // Meta hasn't answered yet — wait for it rather than showing the lot
      // under a filter that says otherwise. The effect re-runs when it lands.
      return;
    }
    if (group !== "all" && wanted.length === 0) {
      // Meta answered and there is nothing in this group — say so, rather than
      // sending no types (which the API reads as "no filter" and would answer
      // with the whole log under a heading promising otherwise).
      setRows([]);
      setLoading(false);
      return;
    }
    api
      .adminEvents({
        limit: 100,
        types: wanted,
        days: RANGES.find((r) => r.id === range)?.days ?? null,
      })
      .then((r) => setRows(r.events || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [group, range, types, metaReady]);

  useEffect(load, [load]);

  return (
    <div className="admin-body">
      <section className="card admin-card admin-table-card">
        <div className="admin-filters">
          <span className="admin-segment" role="group" aria-label="Event kind">
            {GROUPS.map((g) => (
              <button
                key={g.id}
                type="button"
                className={`admin-seg-btn ${group === g.id ? "on" : ""}`}
                onClick={() => setGroup(g.id)}
              >
                {g.label}
              </button>
            ))}
          </span>
          <span className="admin-segment" role="group" aria-label="Time range">
            {RANGES.map((r) => (
              <button
                key={r.id}
                type="button"
                className={`admin-seg-btn ${range === r.id ? "on" : ""}`}
                onClick={() => setRange(r.id)}
              >
                {r.label}
              </button>
            ))}
          </span>
          <button className="btn ghost small" onClick={load} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>

        {error && <p className="error">{error}</p>}

        <div className="admin-table-wrap">
          {rows.length === 0 && !loading ? (
            <p className="muted admin-empty">
              Nothing in this window. The activity log only covers what has
              happened since it was added — it does not reach back over accounts
              that already existed.
            </p>
          ) : (
            <ul className="admin-feed admin-feed-full">
              {rows.map((ev) => (
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
                    {ev.actor && ev.actor !== ev.email && (
                      <span className="muted tiny"> by {ev.actor}</span>
                    )}
                    {eventDetail(ev) && (
                      <span className="muted tiny"> — {eventDetail(ev)}</span>
                    )}
                    {ev.ip && <span className="muted tiny admin-feed-ip">{ev.ip}</span>}
                  </span>
                  <span className="muted tiny admin-feed-when" title={formatDateTime(ev.at)}>
                    {timeAgo(ev.at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <p className="muted tiny admin-foot">
          Newest first, up to 100 at a time.
        </p>
      </section>
    </div>
  );
}
