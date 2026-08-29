// AdminExplore.jsx — ONE tab for the whole public page, with the billboards and
// the wall of work as two sections inside it.
//
// ⚠ THESE WERE TWO TOP-LEVEL TABS AND THAT WAS THE COMPLAINT. Asked for
// directly: *"admin panel banners and showcase both section same work kar raha
// hai explore page ke liye — so tum ek explore ka hi banao aur uske under banner
// and showcase rakho, to mai explore ke under ye dono ko handle kar sakun."*
//
// The tab strip had already half-admitted it: the note above `showcase` in
// AdminPanel.jsx read *"IMMEDIATELY AFTER BANNERS, because they are the SAME
// PAGE … two tabs apart is two tabs too far"*. Adjacency was the workaround. One
// tab is the fix, and the rail is one entry shorter for it.
//
// ⚠ NOTHING WAS REWRITTEN TO GET HERE. `AdminBanners` and `AdminShowcase` are
// untouched and still own their own note box, their own card and their own
// create form; this file is a strip and a switch. Merging their markup would
// have meant one enormous screen and two working editors turned into one
// half-tested one — and the two stores, two routes and two upload rules behind
// them are genuinely different things that happen to live on the same page.
//
// ⚠ THE SECTION IS STATE, NOT A ROUTE, exactly as `tab` is in AdminPanel and
// `nav` is in the shell. This app has no router; see the header of AdminPanel.
import { useState } from "react";

import AdminBanners from "./AdminBanners.jsx";
import AdminShowcase from "./AdminShowcase.jsx";

// ⚠ BANNERS FIRST BECAUSE THE PAGE IS DRAWN THAT WAY — billboards at the top,
// the wall of work underneath. An operator reading this strip left to right is
// reading Explore top to bottom.
const SECTIONS = [
  { id: "banners", label: "Banners", hint: "The two billboards at the top of Explore." },
  { id: "showcase", label: "Showcase", hint: "The wall of work underneath them." },
];

export default function AdminExplore() {
  const [section, setSection] = useState(SECTIONS[0].id);

  return (
    <div className="admin-body">
      {/* ⚠ THE ONE THING NEITHER SECTION CAN SAY FOR ITSELF: that they are two
          halves of one screen. Each child still explains what IT is, in its own
          note box, and repeating that here would be three explanations for two
          things. */}
      <div className="info-msg admin-note-box">
        Everything on <strong>Explore</strong> — the page anybody who is not
        signed in lands on. <strong>Banners</strong> are the billboards across
        the top; <strong>Showcase</strong> is the wall of work underneath them.
      </div>

      {/* The same segmented control Activity, Sales, Features and the user
          detail all use. A second tab strip that merely resembled the one above
          it is the mismatch this repo keeps paying for. */}
      <div className="admin-filters">
        <span className="admin-segment" role="group" aria-label="Explore section">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`admin-seg-btn ${section === s.id ? "on" : ""}`}
              onClick={() => setSection(s.id)}
              title={s.hint}
            >
              {s.label}
            </button>
          ))}
        </span>
      </div>

      {/* ⚠ MOUNTED ONE AT A TIME, NOT HIDDEN WITH CSS. Both of these fetch their
          own list on mount and hold an open create form; keeping the other one
          alive would mean a half-typed banner surviving a trip to Showcase and
          back, which is the kind of state nobody expects and nobody tests. */}
      {section === "banners" ? <AdminBanners /> : <AdminShowcase />}
    </div>
  );
}
