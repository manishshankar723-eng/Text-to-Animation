// AdminPanel.jsx — the admin panel's shell: the header and the tab rail.
//
// ⚠ THE TABS ARE STATE, NOT ROUTES, because this app has no router — a "page"
// is a branch in App.jsx and always has been (see the note at the top of it).
// So the panel owns one `tab` string exactly the way the shell owns `nav`, and
// nothing about the URL changes. That also means the panel cannot be reached by
// typing an address, which is a happy accident and NOT the security model: the
// real guard is `require_admin` on the server, which answers 404 to anyone else.
//
// The header is the app's own `workflow-header`, not a bespoke one, so the panel
// sits in the shell like every workflow does — see the UI rule in AGENTS.md.
import { useState } from "react";
import AdminOverview from "./AdminOverview.jsx";
import AdminUsers from "./AdminUsers.jsx";
import AdminActivity from "./AdminActivity.jsx";
import AdminFeatures from "./AdminFeatures.jsx";
import AdminPricing from "./AdminPricing.jsx";
import AdminSales from "./AdminSales.jsx";
import AdminBrand from "./AdminBrand.jsx";
import AdminBanners from "./AdminBanners.jsx";

// ⚠ ORDER IS "WHAT IS GOING ON" → "WHO" → "WHAT THEY CAN SEE" → "WHAT HAPPENED",
// which is the order somebody opening this at speed actually wants. Pricing sits
// beside Features because they answer one question between them: Features says
// WHICH TIER unlocks a thing, Pricing says WHAT THAT TIER COSTS.
const TABS = [
  { id: "overview", label: "Overview", ico: "📊" },
  { id: "users", label: "Users", ico: "👥" },
  { id: "features", label: "Features", ico: "🎛️" },
  { id: "pricing", label: "Pricing", ico: "💳" },
  // Pricing is the MENU (what a plan costs); Sales is the TRANSACTIONS and the
  // discounts that shaped them. Kept apart because they are edited at
  // completely different moments.
  { id: "sales", label: "Sales", ico: "🧾" },
  // ⚠ LAST BEFORE ACTIVITY, NOT FIRST. It is the tab an operator opens ONCE —
  // when the app is first set up, and again the day the logo is redrawn — and
  // putting a once-a-year screen in front of Users would push the daily work
  // down the rail. Its neighbours are the other "what the product IS" tabs.
  { id: "brand", label: "Brand", ico: "✨" },
  // ⚠ NEXT TO BRAND, NOT NEXT TO SALES. Both of these are "what the product
  // SAYS about itself" — the name and mark on one, the billboards on the front
  // page on the other — and an operator who has just renamed the app is the one
  // most likely to want the banner reworded too. It is nowhere near Sales
  // because a banner is not a discount, even when it is advertising one.
  { id: "banners", label: "Banners", ico: "🖼️" },
  { id: "activity", label: "Activity", ico: "🕑" },
];

export default function AdminPanel() {
  const [tab, setTab] = useState("overview");
  // Set by a click on a row in the activity feed or the dashboard: the Users
  // tab opens with this address already searched for. Cleared once consumed, so
  // returning to the tab later shows the whole list again rather than a filter
  // the user cannot remember setting.
  const [focusEmail, setFocusEmail] = useState("");

  function openUser(email) {
    setFocusEmail(email || "");
    setTab("users");
  }

  return (
    <div className="workflow-head-wrap admin-wrap">
      <div className="workflow-header">
        <span className="wf-icon">🛡️</span>
        <div>
          <h1 className="wf-title">Admin</h1>
          <p className="muted">
            Who signed up, who signed in, and what has happened to their accounts.
          </p>
        </div>
      </div>

      <div className="admin-tabs" role="tablist" aria-label="Admin sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`admin-tab ${tab === t.id ? "on" : ""}`}
            onClick={() => setTab(t.id)}
          >
            <span className="admin-tab-ico">{t.ico}</span>
            {t.label}
          </button>
        ))}
      </div>

      {/* Keyed so switching tabs remounts the screen and it re-reads. An admin
          panel that shows a number cached from four minutes ago is worse than
          one that takes a moment — the whole reason to open it is to find out
          what is true NOW. */}
      {tab === "overview" && <AdminOverview onOpenUser={openUser} onSeeAll={setTab} />}
      {tab === "users" && (
        <AdminUsers
          initialSearch={focusEmail}
          onSearchConsumed={() => setFocusEmail("")}
        />
      )}
      {tab === "features" && <AdminFeatures />}
      {tab === "pricing" && <AdminPricing />}
      {tab === "sales" && <AdminSales onOpenUser={openUser} />}
      {tab === "brand" && <AdminBrand />}
      {tab === "banners" && <AdminBanners />}
      {tab === "activity" && <AdminActivity onOpenUser={openUser} />}
    </div>
  );
}
