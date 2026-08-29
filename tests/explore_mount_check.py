"""DOES THE EXPLORE PAGE OPEN, AND DOES HOME STILL OPEN AFTER THE REFACTOR?

Two questions, one rig, because one change caused both.

Explore is a new screen: billboards, a tile per workflow, and every project this
account owns as one picture wall. It is built out of `dashboard_feed.js` — the
six workflow groups, the cover fetcher and the cache subscription, which USED TO
LIVE INSIDE `Home.jsx` and were moved out so two dashboards could not drift into
two different answers. So this check mounts BOTH screens: the new one, and the
one whose insides were taken out from under it.

⚠ WHY A BROWSER AND NOT A `*_check.py` THAT READS THE FILE. `npm run build`
passes with a crash in it — esbuild never evaluates the module. The white page
this suite's sibling (`workflow_mount_check.py`) was written for was a
`ReferenceError` thrown during the FIRST render, and every static check in this
repo was green while it shipped. Same rule here, and this screen has more of the
shapes that only fail at runtime: a `useMemo` whose dependency is computed above
it, a `setInterval` on a carousel, and a hook (`useCovers`) that now lives in a
different module from the component that calls it.

⚠ MOUNTED INSIDE `<React.StrictMode>`, like `main.jsx` does. StrictMode mounts
twice with the component's state KEPT, which is exactly the shape `useCovers`
got wrong once already — its `live` flag was set false on the way out and never
back to true, so on the second, real mount every cover that arrived was revoked
and thrown away and not one picture ever appeared. A probe that renders once is
a probe that would have missed it, and moving that hook to a new file is
precisely when you would want to know.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/explore_mount_check.py

No backend needed — Vite is started here and every API call is answered by the
router below.

⚠ THE PROBE PAGE IS WRITTEN INTO `client/` AND DELETED AGAIN: Vite serves its
own root and nothing above it, so a harness in a temp directory would be outside
`server.fs.allow` and refused. Both files carry a `__probe` prefix and are
removed in a `finally`.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_explore.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_explore.jsx")

# What `api.js` falls back to when VITE_API_BASE is unset.
API_BASE = "http://127.0.0.1:8000"

# ---------------------------------------------------------------------------
# The account's work. ⚠ ONE ITEM PER WORKFLOW AT LEAST, because the whole point
# of the wall is that six workflows land in one grid — a fixture with only
# storyboards in it would pass while five groups were silently dropped.
# ⚠ AND THE STATUSES ARE MIXED ON PURPOSE: one running, one failed, the rest
# finished, so "In progress" has something to be right about.
# ---------------------------------------------------------------------------
PLANS = [
    {"job_id": "plan-1", "title": "Diwali plan", "item_count": 2,
     "updated_at": "2026-08-27T10:00:00+00:00"},
]
JOBS = [
    {"job_id": "job-1", "character_name": "ANANYA", "status": "succeeded",
     "created_at": "2026-08-26T10:00:00+00:00", "result": {}},
]
BOARDS = [
    {"job_id": "board-1", "title": "Ganesh Utsav: Ek Rishta", "status": "succeeded",
     "panel_count": 15, "aspect_ratio": "9:16", "size_bytes": 51380224,
     "cover_index": 0, "cover_url": None,
     "created_at": "2026-08-28T10:00:00+00:00"},
    {"job_id": "board-2", "title": "Ghar ki taiyari", "status": "running",
     "panel_count": 4, "aspect_ratio": "16:9", "size_bytes": 0,
     "cover_index": None, "cover_url": None,
     "created_at": "2026-08-28T09:00:00+00:00"},
]
COPIED_BOARDS = [
    {"job_id": "copy-1", "title": "dance video", "status": "succeeded",
     "panel_count": 8, "aspect_ratio": "16:9", "size_bytes": 28311552,
     "cover_index": 0, "cover_url": None,
     "updated_at": "2026-08-26T10:00:00+00:00"},
]
ANIMATICS = [
    {"job_id": "anim-1", "title": "Untitled Project", "status": "succeeded",
     "frame_count": 18, "aspect_ratio": "9:16", "size_bytes": 33554432,
     "cover_url": "/media/anim-cover",
     "updated_at": "2026-08-26T12:00:00+00:00"},
]
VIDEOS = [
    {"job_id": "vid-1", "title": "Final cut", "status": "failed",
     "shot_count": 3, "rendered_count": 1, "aspect_ratio": "16:9",
     "size_bytes": 0, "cover_url": "/media/vid-cover",
     "updated_at": "2026-08-25T12:00:00+00:00"},
]

# Everything on the wall, when nothing is hidden and no filter is on.
WALL_TOTAL = len(PLANS) + len(JOBS) + len(BOARDS) + len(COPIED_BOARDS) \
    + len(ANIMATICS) + len(VIDEOS)
# The two that are not finished — see the note on the fixtures.
UNFINISHED = 2

# ---------------------------------------------------------------------------
# The discount the pop-up advertises, in the shape `GET /billing/tiers` sends —
# every field an administrator can type into `AdminSales`, so the card is
# rendered from a real row rather than from its own fallbacks.
#
# ⚠ MUTABLE ON PURPOSE. Section [10] rewrites it between page loads to ask the
# two questions a single fixture cannot: does a NEW offer get its turn after the
# last one was dismissed, and does `popup: false` really draw nothing.
# ---------------------------------------------------------------------------
OFFER = {
    "id": "offer-1",
    "code": "LAUNCH50",
    "label": "Launch week",
    "summary": "50% off",
    "kind": "percent",
    "value": 50,
    "period": "both",
    "applies_to": [],
    "ends_at": "",
    "banner": "",
    "is_sale": False,
    "remaining": 34,
    "popup": True,
    "popup_title": "Aniwala launch offer",
    "popup_lines": ["Every plan, monthly or yearly.", "Cancel whenever you like."],
    "popup_note": "New customers only.",
    "popup_cta": "See the plans",
}

# A 1×1 PNG for every cover the wall asks for. Its pixels do not matter; what is
# being checked is that a cover ARRIVES AT ALL under StrictMode's double mount.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005570cf5a30000000049454e44ae426082"
)

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


PROBE_JSX_SOURCE = r"""
import React from "react";
import ReactDOM from "react-dom/client";
import Explore from "./src/components/Explore.jsx";
import Home from "./src/components/Home.jsx";
// ⚠ THE REAL RAIL, not a stand-in. The whole ask was "an Explore button above
// Home", and a test that asserts the PAGE exists while nothing checks the row
// that opens it is a test of the half nobody asked for.
import Sidebar, { WORKFLOWS } from "./src/components/Sidebar.jsx";
// ⚠ THE WHOLE APP, for one question only: which screen does it OPEN on? That
// is a `useState` initialiser in App.jsx and no amount of mounting the pages
// by hand can see it.
import App from "./src/App.jsx";
// ⚠ THE SCREEN THE POP-UP'S WORDS ARE TYPED ON. A card whose copy comes from
// the admin panel is only half-built until the panel can be opened — and a
// green `npm run build` says nothing about whether it renders.
import AdminSales from "./src/admin/AdminSales.jsx";
import { applyTheme, getTheme } from "./src/theme.js";
import "./src/styles/index.css";

// ⚠ Anything that escapes a render lands here as well as on `pageerror`, so a
// failure Playwright somehow misses is still readable off the page.
window.__errors = [];
window.addEventListener("error", (e) => window.__errors.push(String(e.message)));
// Every navigation either screen asks for, in order. This is how "the tile
// opens the workflow" is asserted without an App shell around it.
window.__nav = [];

applyTheme(getTheme());

// The token only has to EXIST — every request is answered by the test's router.
localStorage.setItem("cas_token", "probe-token");

const root = ReactDOM.createRoot(document.getElementById("root"));
let hidden = [];
let screen = "explore";

function draw() {
  const workflows = WORKFLOWS.filter((w) => !hidden.includes(w.id));
  if (screen === "sales") {
    root.render(
      <React.StrictMode>
        <AdminSales onOpenUser={() => {}} />
      </React.StrictMode>
    );
    return;
  }
  // The real shell draws its own rail, so it is rendered bare — no wrapper.
  if (screen === "app") {
    root.render(
      <React.StrictMode>
        <App />
      </React.StrictMode>
    );
    return;
  }
  root.render(
    <React.StrictMode>
      <div className="shell">
      <Sidebar
        active={screen}
        onNavigate={(id) => window.__nav.push(id)}
        workflows={workflows}
        workflowsKnown={true}
        email="probe@example.com"
        displayName="Probe"
        theme="dark"
        onToggleTheme={() => {}}
        onUpgrade={() => {}}
        onOpenAccount={() => {}}
        onLogout={() => {}}
        accounts={[]}
      />
      <main className="shell-main">
      {screen === "explore" ? (
        <Explore
          workflows={workflows}
          workflowsKnown={true}
          onNavigate={(id) => window.__nav.push(id)}
          onOpenJob={(id) => window.__nav.push("job:" + id)}
          onUpgrade={() => window.__nav.push("upgrade")}
        />
      ) : (
        <Home
          email="probe@example.com"
          visibleWorkflows={workflows.map((w) => w.id)}
          onNavigate={(id) => window.__nav.push(id)}
          onOpenJob={(id) => window.__nav.push("job:" + id)}
          onUpgrade={() => {}}
          onOpenProfile={() => {}}
        />
      )}
      </main>
      </div>
    </React.StrictMode>
  );
}

// An administrator hiding a workflow, from out here.
window.__hide = (ids) => { hidden = ids; draw(); };
window.__screen = (s) => { screen = s; draw(); };
draw();
"""

PROBE_HTML_SOURCE = """<!doctype html>
<meta charset="utf-8">
<title>explore mount probe</title>
<div id="root"></div>
<script type="module" src="/__probe_explore.jsx"></script>
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def route_api(route, request):
    """Answer everything the two dashboards ask the server for.

    ⚠ ORDER MATTERS AND THE COMMENTS SAY WHY. A cover's URL contains
    "/storyboards", so the picture rules have to come before the list rules or
    the wall is handed a JSON array where it expected a PNG.
    """
    url = request.url

    # --- pictures first, for the reason above ---
    if "/panel/" in url or "/media/" in url:
        route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
        return

    # --- the six dashboard lists ---
    if "/plans" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(PLANS))
        return
    if "/jobs" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(JOBS))
        return
    if "/animatics" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(ANIMATICS))
        return
    if "/final-videos" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(VIDEOS))
        return
    # ⚠ THE TWO BOARD FEEDS ARE ONE ENDPOINT AND A QUERY PARAMETER. Script to
    # Storyboard owns the originals (no `workflow`), Image to Animatic Image its
    # own copies — and answering both with the same list is how the wall would
    # show every board twice while the test stayed green.
    if "/storyboards" in url:
        body = COPIED_BOARDS if "workflow=animatic-image" in url else BOARDS
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(body))
        return

    # The admin panel's Sales screen. ⚠ `_offer_row` RESOLVES `popup` for the
    # panel exactly as `public_offer` does for the customer, so the fixture
    # carries it — a row without it would be testing the fallback, not the
    # switch.
    if "/admin/offers" in url and request.method == "GET":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            # ⚠ `popup` AND `promoted` ARE STATED HERE, not inherited from
            # OFFER — section [10] flips `OFFER["popup"]` to False on its way
            # through, and a panel fixture that quietly followed it would make
            # this section's result depend on the order the sections run in.
            "offers": [{**OFFER, "live": True, "active": True, "redeemed": 6,
                        "max_redemptions": 40, "promoted": True, "popup": True}],
            "kinds": ["percent", "amount"],
            "periods": ["monthly", "yearly", "both"],
            # ⚠ `tier_ids`, NOT `tiers` — the panel reads `offers.tier_ids[1]`
            # for the plan the record form opens on, and the wrong key name here
            # is a crash rather than an empty dropdown.
            "tier_ids": [{"id": "starter", "name": "Starter"},
                         {"id": "studio", "name": "Studio"}],
            "currency": "USD",
        }))
        return
    if "/admin/subscriptions" in url and request.method == "GET":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "subscriptions": [], "total": 0, "active": 0,
            "recorded_monthly": 0, "currency": "USD",
        }))
        return

    # ⚠ BEFORE the catch-all, which answers "{}" — and an offer strip handed an
    # empty object draws nothing, so the pop-up would never have been tested.
    if "/billing/tiers" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"tiers": [], "offers": [OFFER]}))
        return

    if url.rstrip("/").endswith("/auth/me"):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"email": "probe@example.com", "display_name": "Probe",
             "created_at": "2026-07-20T10:00:00+00:00", "is_admin": False}))
        return

    # Entitlements, usage, offers — an empty list or object answers all of them,
    # and the offer strip on Home is written to draw NOTHING when it reads one.
    body = "[]" if request.method == "GET" else "{}"
    route.fulfill(status=200, content_type="application/json", body=body)


def start_vite(port):
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        return None
    proc = subprocess.Popen(
        [npx, "vite", "--port", str(port), "--host", "127.0.0.1", "--strictPort"],
        cwd=CLIENT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        shell=os.name == "nt",
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if "ready in" in line or "Local:" in line:
            time.sleep(2)
            return proc
        if "error" in line.lower() and "Local:" not in line:
            print("  vite:", line.rstrip())
    proc.terminate()
    return None


def main():
    if not os.path.isdir(os.path.join(CLIENT, "node_modules")):
        print("  client/node_modules is missing — run `cd client && npm install` first.")
        return 2

    with open(PROBE_JSX, "w", encoding="utf-8") as fh:
        fh.write(PROBE_JSX_SOURCE)
    with open(PROBE_HTML, "w", encoding="utf-8") as fh:
        fh.write(PROBE_HTML_SOURCE)

    port = free_port()
    vite = None
    try:
        vite = start_vite(port)
        if not vite:
            print("  could not start Vite.")
            return 2

        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1780, "height": 1000})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route(f"{API_BASE}/**", route_api)

            page.goto(f"http://127.0.0.1:{port}/__probe_explore.html",
                      wait_until="load")
            page.wait_for_timeout(3000)

            print("\n[1] it mounts at all")
            check("⚠ nothing reached `pageerror` — a crash during render "
                  "unmounts the whole tree and leaves a white page, and "
                  "`npm run build` cannot see it",
                  not errors, "; ".join(errors[:2]))
            html_len = page.eval_on_selector("#root", "el => el.innerHTML.length")
            check("…and something was actually drawn", html_len > 500, f"{html_len} chars")
            check("…including under StrictMode's second mount",
                  not page.evaluate("window.__errors.length"),
                  str(page.evaluate("window.__errors")))

            print("")
            print("[1b] the row that opens it")
            rail = page.locator(".sidebar")
            check("the rail carries an Explore row",
                  rail.locator(".sb-item", has_text="Explore").count() == 1,
                  str(rail.locator(".sb-item", has_text="Explore").count()))
            labels = rail.locator(".sb-item-label").all_inner_texts()
            check("⚠ …ABOVE Home, which is the order that was asked for",
                  labels.index("Explore") < labels.index("Home"),
                  str(labels[:3]))
            check("…and it is marked as the page you are on",
                  "active" in (rail.locator(".sb-item", has_text="Explore")
                               .get_attribute("class") or ""))
            rail.locator(".sb-item", has_text="Home").click()
            page.wait_for_timeout(200)
            check("…and Home is still one click away, unchanged",
                  page.evaluate("window.__nav").count("home") == 1,
                  str(page.evaluate("window.__nav")))

            print("")
            print("[1c] the offer comes to the customer")
            # ⚠ IT ARRIVES, IT DOES NOT LOAD WITH THE PAGE. `ENTER_MS` of delay
            # is what makes it read as an arrival; a card that is simply there
            # on the first frame is part of the layout and gets scrolled past
            # like the rest of it.
            page.wait_for_selector(".promo-pop.in", timeout=4000)
            promo = page.locator(".promo-pop")
            check("the offer card slid in", promo.count() == 1)
            check("⚠ …headed by the words an ADMINISTRATOR typed, not by the "
                  "component's own fallback",
                  promo.locator(".promo-title").inner_text()
                  == OFFER["popup_title"],
                  promo.locator(".promo-title").inner_text())
            check("…with a bullet per line they wrote",
                  promo.locator(".promo-lines li").count()
                  == len(OFFER["popup_lines"]),
                  str(promo.locator(".promo-lines li").count()))
            check("…their small print under them",
                  OFFER["popup_note"] in promo.inner_text())
            check("…their words on the button",
                  OFFER["popup_cta"] in promo.inner_text(), promo.inner_text())
            promo.locator(".promo-cta").click()
            page.wait_for_timeout(250)
            check("⚠ …and that button opens the pricing modal — the SAME one "
                  "the rail's Upgrade opens, not a second pricing screen",
                  page.evaluate("window.__nav").count("upgrade") == 1,
                  str(page.evaluate("window.__nav")))
            check("⚠ …and the CODE, copyable — a coupon nobody can copy is a "
                  "coupon typed wrong",
                  promo.locator(".promo-code-text").inner_text() == OFFER["code"])
            check("…the discount itself is the card's artwork",
                  promo.locator(".promo-cut").inner_text() == OFFER["summary"])
            check("⚠ …and it is NOT a modal — the page behind it still works",
                  page.locator(".modal-overlay").count() == 0
                  and page.locator(".xp-tile").count() > 0)

            page.screenshot(path=os.path.join(ROOT, "output", "explore_promo.png"))

            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
            check("Escape closes it", page.locator(".promo-pop").count() == 0)

            print("")
            print("[2] the billboards")
            check("the rotating banner is there", page.locator(".xp-hero").count() == 1)
            check("⚠ …with a dot per slide — the brand one plus three "
                  "workflows, which is what HERO_WORKFLOWS says",
                  page.locator(".xp-hero-dot").count() == 4,
                  str(page.locator(".xp-hero-dot").count()))
            first_title = page.locator(".xp-hero .xp-banner-title").inner_text()
            page.locator(".xp-hero-dot").nth(1).click()
            page.wait_for_timeout(400)
            check("⚠ …and a dot actually changes the slide — a carousel whose "
                  "controls do nothing is four banners nobody but the first "
                  "will ever read",
                  page.locator(".xp-hero .xp-banner-title").inner_text() != first_title)
            check("the fixed billboard beside it names a workflow, and it is "
                  "the one SIDE_PREFERRED asks for",
                  page.locator(".tone-side .xp-banner-title").inner_text()
                  == "Video Editor",
                  page.locator(".tone-side .xp-banner-title").inner_text())
            check("⚠ …and neither banner's body is a lone full stop, which is "
                  "what a workflow with no pitch written for it used to print",
                  page.locator(".xp-banner-sub").nth(0).inner_text().strip() != ".")

            # ⚠ THE BUG THIS BLOCK EXISTS FOR. The two billboards share a grid
            # row, so a slide whose body ran two lines longer pushed BOTH of
            # them taller and then let them snap back — the page flinching every
            # six seconds. Reported as *"image to animatics image ka panel bara
            # ho jata hai kyun ismai text jayada hai"*. Measured, not eyed.
            #
            # ⚠ AND IT WALKS EVERY SLIDE. The first version of the doubled-stop
            # check read slide 0 only — and the workflow that produced ".." is
            # never slide 0, so it reported green on the broken build.
            heights = []
            subs = []
            for n in range(page.locator(".xp-hero-dot").count()):
                page.locator(".xp-hero-dot").nth(n).click()
                page.wait_for_timeout(350)
                heights.append(round(page.locator(".xp-banners").bounding_box()["height"]))
                subs.append(page.locator(".xp-hero .xp-banner-sub").inner_text())
            check("⚠ every slide leaves the billboard row exactly the same "
                  "height — the longest body must not be the one that moves the "
                  "page",
                  len(set(heights)) == 1, str(heights))
            check("⚠ …and no slide ends in a doubled full stop, which is what "
                  "gluing a '.' onto a sentence that already had one produced",
                  not [t for t in subs if ".." in t],
                  str([t for t in subs if ".." in t]))
            check("…every slide has a body worth reading, none of them a lone "
                  "full stop",
                  all(len(t.strip()) > 1 for t in subs), str(subs))

            print("\n[3] a tile per workflow, and the tiles go somewhere")
            check("six tiles, one per workflow in the rail",
                  page.locator(".xp-tile").count() == 6,
                  str(page.locator(".xp-tile").count()))
            # ⚠ THE OPPOSITE OF WHAT THIS CHECK USED TO SAY, and the reason is
            # in the reply that changed it: *"ye 3 button ka colour ek jaisa
            # rakho, ismai golden hata do."* The rail's order is the owner's
            # pipeline, not a recommendation, and gold means "the action"
            # everywhere else on this page.
            check("⚠ not one of them is gold — the row is one colour end to end",
                  page.locator(".xp-tile-lead").count() == 0,
                  str(page.locator(".xp-tile-lead").count()))
            page.locator(".xp-tile", has_text="Script to Storyboard").click()
            page.wait_for_timeout(200)
            check("…and pressing one opens that workflow",
                  page.evaluate("window.__nav").count("script-to-storyboard") == 1,
                  str(page.evaluate("window.__nav")))

            print("\n[4] the wall — every workflow's work in one grid")
            cards = page.locator(".xp-card")
            check(f"⚠ all {WALL_TOTAL} projects are on it, from all six "
                  "workflows — a wall that quietly drops a group is the exact "
                  "fault `dashboard_feed.js` exists to make impossible",
                  cards.count() == WALL_TOTAL, str(cards.count()))
            check("…a finished project wears no badge, and the two unfinished "
                  "ones do", page.locator(".xp-card-badge").count() == UNFINISHED,
                  str(page.locator(".xp-card-badge").count()))
            check("⚠ …and a cover picture actually arrived, which is the thing "
                  "StrictMode's double mount broke in `useCovers` once before",
                  page.locator(".xp-card-pic img").count() > 0,
                  str(page.locator(".xp-card-pic img").count()))
            check("…a project with no picture yet falls back to its workflow's "
                  "own glyph rather than a grey box",
                  page.locator(".xp-card-glyph").count() > 0)
            check("…each card says which workflow it came from",
                  page.locator(".xp-card-wf").count() == WALL_TOTAL,
                  str(page.locator(".xp-card-wf").count()))

            print("\n[5] the filters")
            page.locator(".xp-chip", has_text="Storyboard").first.click()
            page.wait_for_timeout(300)
            check("the workflow chip narrows the wall to that workflow",
                  page.locator(".xp-card").count() == len(BOARDS),
                  str(page.locator(".xp-card").count()))
            page.locator(".xp-chip", has_text="For you").first.click()
            page.wait_for_timeout(300)
            check("…and clears again", page.locator(".xp-card").count() == WALL_TOTAL,
                  str(page.locator(".xp-card").count()))

            page.locator(".xp-tab", has_text="In progress").click()
            page.wait_for_timeout(300)
            check("⚠ 'In progress' keeps only what is NOT finished — a plan has "
                  "no status and must not be swept in as unfinished work",
                  page.locator(".xp-card").count() == UNFINISHED,
                  str(page.locator(".xp-card").count()))
            page.locator(".xp-tab", has_text="Highlights").click()
            page.wait_for_timeout(300)

            page.fill(".xp-search-input", "dance")
            page.wait_for_timeout(400)
            check("the search box filters by title",
                  page.locator(".xp-card").count() == 1,
                  str(page.locator(".xp-card").count()))
            page.fill(".xp-search-input", "zzzz-nothing")
            page.wait_for_timeout(400)
            check("…and a search that matches nothing says so instead of "
                  "leaving an empty page",
                  page.locator(".xp-empty").count() == 1
                  and "Nothing matches" in page.inner_text(".xp-empty"))
            page.locator(".xp-search-clear").click()
            page.wait_for_timeout(400)
            check("…and clearing it brings the wall back",
                  page.locator(".xp-card").count() == WALL_TOTAL,
                  str(page.locator(".xp-card").count()))
            check("nothing threw during any of it",
                  not errors, "; ".join(errors[:2]))

            print("\n[6] a workflow an administrator has hidden is hidden HERE too")
            page.evaluate("window.__hide(['animatics-to-video'])")
            page.wait_for_timeout(600)
            body = page.inner_text("body")
            check("its tile is gone",
                  page.locator(".xp-tile").count() == 5,
                  str(page.locator(".xp-tile").count()))
            check("⚠ …and so is its work — the rail was filtered and the "
                  "dashboard was not, once, and the hidden workflow kept a "
                  "column with a 'View all' into a room with no door",
                  page.locator(".xp-card").count() == WALL_TOTAL - len(VIDEOS),
                  str(page.locator(".xp-card").count()))
            check("…and its name is nowhere on the page",
                  "Image to AI Video" not in body)
            page.evaluate("window.__hide([])")
            page.wait_for_timeout(600)

            print("\n[7] Home still opens — its insides moved to dashboard_feed.js")
            page.evaluate("window.__screen('home')")
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            check("⚠ it mounts with nothing thrown. `buildGroups`, `useCovers`, "
                  "`useDashboard`, `formatDate` and `statusClass` were all cut "
                  "out of this file and imported back in — a missed one is a "
                  "white dashboard, and the build says nothing",
                  not errors, "; ".join(errors[:2]))
            check("…the dashboard is drawn", "Welcome back" in body)
            check("…Recent work still has a group per workflow",
                  page.locator(".wf-group").count() == 6,
                  str(page.locator(".wf-group").count()))
            check("…with the projects in them", "Ganesh Utsav: Ek Rishta" in body)
            check("…and their covers still arrive",
                  page.locator(".lib-thumb-pic img").count() > 0,
                  str(page.locator(".lib-thumb-pic img").count()))
            check("…and the dates still read as dates, not as 'Invalid Date'",
                  "Invalid Date" not in body)

            check("nothing threw during any of it", not errors, "; ".join(errors[:2]))
            page.evaluate("window.__screen('explore')")
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(ROOT, "output", "explore_mount.png"),
                            full_page=True)

            print("")
            print("[8] and it survives the theme switch")
            # ⚠ HEADLESS CHROMIUM REPORTS A LIGHT OS, so everything above ran in
            # the LIGHT theme — which is the half of this app that gets a whole
            # second palette in theme.css (a deeper gold, an inverted ink) and
            # is therefore the half a new screen is most likely to get wrong.
            # The dark theme is the app's default for everybody whose OS says
            # so, so it does not go unlooked-at.
            page.evaluate(
                "document.documentElement.dataset.theme = 'dark';"
                "localStorage.setItem('cas_theme', 'dark');"
            )
            page.wait_for_timeout(600)
            check("the page is still drawn after the palette swaps",
                  page.locator(".xp-card").count() == WALL_TOTAL,
                  str(page.locator(".xp-card").count()))
            check("⚠ …and the page's own background actually changed with it — "
                  "a screen that defines a colour only inside a light-theme "
                  "block is a screen that renders white-on-white in the dark",
                  page.evaluate(
                      "getComputedStyle(document.documentElement)"
                      ".getPropertyValue('--panel').trim()") == "#13161f",
                  page.evaluate(
                      "getComputedStyle(document.documentElement)"
                      ".getPropertyValue('--panel').trim()"))
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))
            page.screenshot(
                path=os.path.join(ROOT, "output", "explore_mount_dark.png"),
                full_page=True)

            print("")
            print("[9] the WHOLE app, signed in, opens on Explore")
            # ⚠ THE ONE THING NOTHING ABOVE CAN SEE. Every section so far mounts
            # the pages by hand and tells them which to draw; where the APP
            # lands is a `useState` initialiser in App.jsx, and it was spelled
            # out in five separate places until `LANDING_NAV` gathered them.
            # Asked for outright: *"jab user aaye to explore page khule, home
            # page nhi"*.
            page.evaluate("window.__screen('app')")
            page.wait_for_timeout(2500)
            check("it mounts with nothing thrown",
                  not errors, "; ".join(errors[:2]))
            check("⚠ a signed-in session opens on EXPLORE",
                  page.locator(".explore").count() == 1,
                  str(page.locator(".explore").count()))
            check("…and not on the dashboard",
                  page.locator(".home").count() == 0,
                  str(page.locator(".home").count()))
            rail = page.locator(".sidebar")
            check("…with the rail agreeing about where you are",
                  "active" in (rail.locator(".sb-item", has_text="Explore")
                               .get_attribute("class") or ""))
            check("⚠ …and Home is still there, one click away and unchanged — "
                  "moving the front door must not remove the desk",
                  rail.locator(".sb-item", has_text="Home").count() == 1)
            rail.locator(".sb-item", has_text="Home").click()
            page.wait_for_timeout(900)
            check("…and that click really opens it",
                  page.locator(".home").count() == 1
                  and "Welcome back" in page.inner_text("body"))
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))

            print("")
            print("[10] a dismissed offer stays dismissed — a NEW one does not")
            # ⚠ THE TRAP A SINGLE "seen the popup" FLAG WOULD HAVE SET. Closing
            # one card must not silence every offer this account is ever shown,
            # and the only way to tell the two apart is to reload with a
            # different id. Section [1c] already pressed Escape on `offer-1`.
            page.reload(wait_until="load")
            page.wait_for_timeout(2500)
            check("⚠ the card somebody closed does not come back",
                  page.locator(".promo-pop").count() == 0,
                  str(page.locator(".promo-pop").count()))

            OFFER["id"] = "offer-2"
            OFFER["popup_title"] = "Second offer"
            page.reload(wait_until="load")
            page.wait_for_selector(".promo-pop.in", timeout=5000)
            check("⚠ …but a NEW offer still gets its turn",
                  page.locator(".promo-title").inner_text() == "Second offer",
                  page.locator(".promo-title").inner_text())

            OFFER["id"] = "offer-3"
            OFFER["popup"] = False
            page.reload(wait_until="load")
            page.wait_for_timeout(2500)
            check("⚠ …and an offer an administrator switched the pop-up OFF for "
                  "draws nothing at all — the panel is the control, not a "
                  "suggestion",
                  page.locator(".promo-pop").count() == 0,
                  str(page.locator(".promo-pop").count()))
            check("…and the page is still fine without it",
                  page.locator(".xp-tile").count() == 6,
                  str(page.locator(".xp-tile").count()))
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))

            print("")
            print("[11] and the panel those words are typed on still opens")
            page.evaluate("window.__screen('sales')")
            page.wait_for_timeout(1600)
            check("Sales mounts with nothing thrown",
                  not errors, "; ".join(errors[:2]))
            check("…and its offer is listed", "LAUNCH50" in page.inner_text("body"))
            check("⚠ …with the pop-up switch on the row — a third question, not "
                  "a second name for Show/Hide",
                  page.get_by_role("button", name="No pop-up").count() == 1,
                  page.inner_text(".admin-offer-acts") if
                  page.locator(".admin-offer-acts").count() else "no acts")

            # ⚠ THE FIELDS ARE INSIDE THE CREATE FORM, which is collapsed until
            # somebody presses the button — so the check has to press it. A
            # form nobody can open is a form nobody can fill in.
            page.get_by_role("button", name="New offer").first.click()
            page.wait_for_timeout(400)
            body = page.inner_text("body")
            for label in ("Also slide it in as a card on Explore",
                          "Card heading", "Bullet points", "Small print",
                          "Button words"):
                check(f"…the form asks for it: {label}", label in body)
            check("⚠ …and the bullet box GROWS to its text (E1) rather than "
                  "clipping the third line out of sight",
                  page.locator("textarea.admin-offer-lines").count() == 1,
                  str(page.locator("textarea.admin-offer-lines").count()))
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))
            page.screenshot(path=os.path.join(ROOT, "output", "admin_offer_popup.png"),
                            full_page=True)

            browser.close()
    finally:
        if vite:
            vite.terminate()
        for path in (PROBE_HTML, PROBE_JSX):
            try:
                os.remove(path)
            except OSError:
                pass

    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("Explore opens, rotates, filters, hides what the account may not see — "
          "and Home still opens on the shared feed it was refactored onto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
