// AdminUsers.jsx — the user table. Search, filter, sort, page, and open one.
//
// ⚠ THE TABLE IS ONE ROUND TRIP AND STAYS THAT WAY. Project counts are a query
// per row, so they are OFF until asked for — the "Count projects" button. A
// table that quietly fans out fifty queries every time somebody types a letter
// in the search box is how an admin panel becomes the slowest page in the app.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import AdminUserDetail from "./AdminUserDetail.jsx";
import { formatDate, num, timeAgo } from "./format.js";

const PAGE = 25;

// Which columns can be sorted, and what the server calls them. A header that
// isn't in here renders as plain text rather than as a dead button.
const SORTABLE = {
  email: "email",
  created_at: "created_at",
  last_login_at: "last_login_at",
  login_count: "login_count",
};

export default function AdminUsers({ initialSearch = "", onSearchConsumed }) {
  const [search, setSearch] = useState(initialSearch);
  // ⚠ TWO SEARCH STATES ON PURPOSE. `search` is what the box shows and must
  // update on every keystroke or typing feels broken; `query` is what has
  // actually been sent, and lags by the debounce. One state would mean either a
  // laggy field or a request per character against a remote database.
  const [query, setQuery] = useState(initialSearch);
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("created_at");
  const [desc, setDesc] = useState(true);
  const [skip, setSkip] = useState(0);
  const [withCounts, setWithCounts] = useState(false);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openEmail, setOpenEmail] = useState("");

  // The arriving `initialSearch` (a click from the dashboard or the activity
  // feed) is consumed once. Telling the parent lets it forget the address, so
  // coming back to this tab later isn't still filtered by it.
  const consumed = useRef(false);
  useEffect(() => {
    if (initialSearch && !consumed.current) {
      consumed.current = true;
      setOpenEmail(initialSearch);
      onSearchConsumed?.();
    }
  }, [initialSearch, onSearchConsumed]);

  // Debounce the box → the request.
  useEffect(() => {
    const t = setTimeout(() => {
      setQuery(search);
      setSkip(0); // a new search starts at page one, not wherever you were
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .adminListUsers({
        search: query,
        role,
        disabled: status === "" ? null : status === "disabled",
        sort,
        desc,
        limit: PAGE,
        skip,
        withCounts,
      })
      .then((r) => {
        setRows(r.users || []);
        setTotal(r.total || 0);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [query, role, status, sort, desc, skip, withCounts]);

  useEffect(load, [load]);

  function toggleSort(col) {
    const key = SORTABLE[col];
    if (!key) return;
    if (key === sort) {
      setDesc((d) => !d);
    } else {
      setSort(key);
      // A new column starts newest/highest first — which is what somebody
      // clicking "Last seen" means, every time.
      setDesc(true);
    }
    setSkip(0);
  }

  const page = Math.floor(skip / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div className="admin-body admin-split">
      <section className="card admin-card admin-table-card">
        <div className="admin-filters">
          <input
            className="admin-search"
            type="search"
            placeholder="Search email, name or company…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search users"
          />
          <select
            className="admin-select"
            value={role}
            onChange={(e) => {
              setRole(e.target.value);
              setSkip(0);
            }}
            aria-label="Filter by role"
          >
            <option value="">All roles</option>
            <option value="admin">Admins</option>
            <option value="user">Users</option>
          </select>
          <select
            className="admin-select"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setSkip(0);
            }}
            aria-label="Filter by status"
          >
            <option value="">Any status</option>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
          </select>
          <button
            type="button"
            className={`btn ghost small ${withCounts ? "on" : ""}`}
            onClick={() => setWithCounts((c) => !c)}
            title={
              withCounts
                ? "Stop counting projects (one extra query per row)"
                : "Count each account's projects — one extra query per row"
            }
          >
            {withCounts ? "✓ Projects" : "Count projects"}
          </button>
        </div>

        {error && <p className="error">{error}</p>}

        {/* ⚠ EXACTLY ONE SCROLLER IN THE CHAIN. The card is
            `overflow:hidden; display:flex; min-height:0` and this wrapper is
            `flex:1; min-height:0; overflow:auto` — nest a second and the
            horizontal bar ends up at the bottom of the CONTENT, off-screen.
            See the UI rule in AGENTS.md. */}
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <Th col="email" label="Account" {...{ sort, desc, toggleSort }} />
                <th>Plan</th>
                <th>Role</th>
                <Th col="created_at" label="Registered" {...{ sort, desc, toggleSort }} />
                <Th col="last_login_at" label="Last seen" {...{ sort, desc, toggleSort }} />
                <Th col="login_count" label="Sign-ins" {...{ sort, desc, toggleSort }} />
                {withCounts && <th>Projects</th>}
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 && (
                <tr>
                  <td colSpan={withCounts ? 7 : 6} className="muted admin-empty">
                    Loading…
                  </td>
                </tr>
              )}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={withCounts ? 7 : 6} className="muted admin-empty">
                    {query || role || status
                      ? "No accounts match those filters."
                      : "No accounts yet."}
                  </td>
                </tr>
              )}
              {rows.map((u) => (
                <tr
                  key={u.email}
                  className={`admin-row ${openEmail === u.email ? "on" : ""} ${
                    u.disabled ? "off" : ""
                  }`}
                  onClick={() => setOpenEmail(u.email)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setOpenEmail(u.email);
                    }
                  }}
                >
                  <td>
                    <span className="admin-cell-name">
                      {u.display_name || u.full_name || u.email.split("@")[0]}
                    </span>
                    <span className="muted tiny admin-cell-sub">{u.email}</span>
                  </td>
                  <td>
                    <span className="chip admin-tier-chip">{u.tier || "trial"}</span>
                  </td>
                  <td>
                    {u.account_role === "admin" && (
                      <span className="badge running">Admin</span>
                    )}
                    {u.disabled && <span className="badge fail">Disabled</span>}
                    {u.account_role !== "admin" && !u.disabled && (
                      <span className="muted tiny">—</span>
                    )}
                  </td>
                  <td>
                    <span>{formatDate(u.created_at)}</span>
                    <span className="muted tiny admin-cell-sub">
                      {timeAgo(u.created_at)}
                    </span>
                  </td>
                  <td>
                    {/* "never" is a real answer here and reads better than a
                        dash — every account predating the activity log has it. */}
                    <span className={u.last_login_at ? "" : "muted"}>
                      {u.last_login_at ? timeAgo(u.last_login_at) : "never"}
                    </span>
                  </td>
                  <td>{num(u.login_count)}</td>
                  {withCounts && <td>{num(u.projects)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="admin-pager">
          <span className="muted tiny">
            {num(total)} account{total === 1 ? "" : "s"} · page {page} of {pages}
          </span>
          <span className="admin-pager-btns">
            <button
              className="btn ghost small"
              disabled={skip === 0 || loading}
              onClick={() => setSkip((s) => Math.max(0, s - PAGE))}
            >
              ← Previous
            </button>
            <button
              className="btn ghost small"
              disabled={skip + PAGE >= total || loading}
              onClick={() => setSkip((s) => s + PAGE)}
            >
              Next →
            </button>
          </span>
        </div>
      </section>

      <section className="admin-detail-col">
        {openEmail ? (
          <AdminUserDetail
            email={openEmail}
            onClose={() => setOpenEmail("")}
            /* A change to a row — disabled, role, deleted — has to be reflected
               in the table behind it, so the detail asks for a reload rather
               than patching a copy of the row it doesn't own. */
            onChanged={load}
            onDeleted={() => {
              setOpenEmail("");
              load();
            }}
          />
        ) : (
          <div className="card placeholder admin-detail-empty">
            <p className="muted">Select an account to see its history.</p>
          </div>
        )}
      </section>
    </div>
  );
}

function Th({ col, label, sort, desc, toggleSort }) {
  const key = SORTABLE[col];
  const on = key === sort;
  return (
    <th
      className={`admin-th ${on ? "on" : ""}`}
      onClick={() => toggleSort(col)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggleSort(col);
        }
      }}
      aria-sort={on ? (desc ? "descending" : "ascending") : "none"}
      title={`Sort by ${label.toLowerCase()}`}
    >
      {label}
      <span className="admin-th-caret" aria-hidden="true">
        {on ? (desc ? "▾" : "▴") : ""}
      </span>
    </th>
  );
}
