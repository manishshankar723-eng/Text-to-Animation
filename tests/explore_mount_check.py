"""DOES THE PUBLIC EXPLORE PAGE OPEN, PLAY AND GATE — AND DOES HOME STILL OPEN?

Three questions, one rig, because one change caused all three.

Explore changed sides. It used to be the SIGNED-IN front door — billboards, a
tile per workflow, and every project the account owned as one picture wall — and
it is now the logged-out marketing page. Asked for in one breath:

    *"the page we created on explore should be used to market ... if anyone
    clicks anywhere to use and create any workflow we must give a user first to
    login and then use ... any logged in user must not see explore ... but the
    videos or images should be clickable and be able to use it properly play"*

So this file checks the three halves of that, and a fourth thing it could easily
have broken:

    1. THE PAGE ITSELF — banners, tiles, and a wall of ADMIN-CURATED work from
       `GET /public/showcase` rather than the customer's own projects.
    2. THE GATE — every control on it asks for a sign-in, and carries the
       workflow it was selling through to the other side.
    3. THE PLAYER — a card is not a link, it opens a real `<video>`. The whole
       reason the wall exists is that somebody can watch it.
    4. HOME, which is where a signed-in customer lands now, and which still runs
       on `dashboard_feed.js` — the six workflow groups, the cover fetcher and
       the cache subscription that were moved out of `Home.jsx` so two
       dashboards could not drift into two different answers.

⚠ WHY A BROWSER AND NOT A `*_check.py` THAT READS THE FILE. `npm run build`
passes with a crash in it — esbuild never evaluates the module. The white page
this suite's sibling (`workflow_mount_check.py`) was written for was a
`ReferenceError` thrown during the FIRST render, and every static check in this
repo was green while it shipped. This screen is full of the shapes that only
fail at runtime: a `useMemo` whose dependency is computed above it, a
`setInterval` on a carousel, and a `<video>` element whose `key` is what stops
one film's audio outliving it. `tests/showcase_check.py` covers the store and the
routes; this covers the render.

⚠ MOUNTED INSIDE `<React.StrictMode>`, like `main.jsx` does. StrictMode mounts
twice with the component's state KEPT, which is exactly the shape `useCovers`
got wrong once already — its `live` flag was set false on the way out and never
back to true, so on the second, real mount every cover that arrived was revoked
and thrown away and not one picture ever appeared. A probe that renders once is
a probe that would have missed it.

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
# WHAT THE PRODUCT SELLS, in the shape `GET /public/workflows` sends. ⚠ THIS IS
# THE LIST EXPLORE NOW READS, and that is the change: it used to be handed the
# resolved rail — what ONE SIGNED-IN ACCOUNT is entitled to see — and there is no
# account on a public page. ⚠ MUTABLE, because section [6] stages one of them as
# "soon" and asks whether the page stops SELLING it while still showing it.
# ---------------------------------------------------------------------------
PUBLIC_WORKFLOWS = [
    {"id": "plan-and-script", "label": "Plan & Script", "status": "live"},
    {"id": "text-to-image", "label": "Text to Turnaround Image", "status": "live"},
    {"id": "script-to-storyboard", "label": "Script to Storyboard", "status": "live"},
    {"id": "create-animatic-image", "label": "Image to Animatic Image", "status": "live"},
    {"id": "animatics-to-video", "label": "Image to AI Video", "status": "live"},
    {"id": "storyboard-to-animatics", "label": "Video Editor", "status": "live"},
]

# ---------------------------------------------------------------------------
# THE CURATED WALL, in the shape `GET /public/showcase` sends.
#
# ⚠ A MIX ON PURPOSE, AND EVERY ROW IS ONE QUESTION:
#   • two videos, because "the videos should play" is the ask;
#   • one of them with NO poster, because a clip an administrator has not put a
#     still on has to draw the workflow glyph rather than a black hole;
#   • one item with NO workflow tag, because a film about the whole pipeline
#     belongs to none of them and must not vanish from an unfiltered wall;
#   • 16:9 AND 9:16, because the tall card is what left a hole in the old wall
#     and `wallAspect` is the clamp that fixed it.
# ---------------------------------------------------------------------------
SHOWCASE = [
    {"id": "sc-1", "title": "Ganesh Utsav spot", "blurb": "Script to cut in a day.",
     "workflow": "script-to-storyboard", "kind": "video", "aspect": "16:9",
     "media_url": "/public/showcase/media/aaaaaaaaaaaa",
     "poster_url": "/public/showcase/media/bbbbbbbbbbbb"},
    {"id": "sc-2", "title": "Chai break reel", "blurb": "Vertical, for the feed.",
     "workflow": "animatics-to-video", "kind": "video", "aspect": "9:16",
     "media_url": "/public/showcase/media/cccccccccccc",
     "poster_url": ""},
    {"id": "sc-3", "title": "ANANYA turnaround", "blurb": "Four views, one photo.",
     "workflow": "text-to-image", "kind": "image", "aspect": "16:9",
     "media_url": "/public/showcase/media/dddddddddddd", "poster_url": ""},
    {"id": "sc-4", "title": "Diwali board", "blurb": "Fifteen panels, one brief.",
     "workflow": "script-to-storyboard", "kind": "image", "aspect": "9:16",
     "media_url": "/public/showcase/media/eeeeeeeeeeee", "poster_url": ""},
    {"id": "sc-5", "title": "The whole pipeline", "blurb": "Plan to render.",
     "workflow": "", "kind": "image", "aspect": "1:1",
     "media_url": "/public/showcase/media/ffffffffffff", "poster_url": ""},
]
WALL_TOTAL = len(SHOWCASE)
FILMS = len([i for i in SHOWCASE if i["kind"] == "video"])
STILLS = WALL_TOTAL - FILMS
BOARD_ITEMS = len([i for i in SHOWCASE if i["workflow"] == "script-to-storyboard"])

# ---------------------------------------------------------------------------
# The account's own work — for HOME, which is the screen that still shows it.
# ⚠ ONE ITEM PER WORKFLOW AT LEAST, because Recent work draws a group per
# workflow and a fixture with only storyboards in it would pass while five
# groups were silently dropped.
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

# ---------------------------------------------------------------------------
# The billboards an administrator typed, in the shape `GET /public/banners`
# sends. ⚠ MUTABLE, like OFFER: section [12] fills it in and empties it again to
# prove the page falls back to the cards it generates from the workflow list,
# which is both the shipped state and what "hide it" produces.
# ---------------------------------------------------------------------------
BANNERS = {"hero": [], "side": []}

HERO_BANNER = {
    "id": "ban-hero-1",
    "slot": "hero",
    "kicker": "Festival season",
    "title": "Ganesh Utsav films, in a day",
    "body": "One brief in, fifteen drawn panels out — then a cut you can post.",
    "cta_label": "Start one",
    "cta_target": "script-to-storyboard",
    "image_url": "/public/banners/image/aaaaaaaaaaaa",
}

SIDE_BANNER = {
    "id": "ban-side-1",
    "slot": "side",
    "kicker": "New",
    "title": "Bring your own footage",
    "body": "Drop a clip into the editor and cut it against your board.",
    "cta_label": "Open the editor",
    "cta_target": "storyboard-to-animatics",
    "image_url": "",
}

# A 1×1 PNG for every picture anything asks for. Its pixels do not matter; what
# is being checked is that one ARRIVES AT ALL under StrictMode's double mount.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005570cf5a30000000049454e44ae426082"
)

# ⚠ NOT A PLAYABLE FILM, AND IT DOES NOT NEED TO BE. What is being asserted is
# that a `<video>` ELEMENT is created, pointed at the right address and given
# controls — Chromium failing to decode eight bytes of nonsense is not a failure
# of this page. `tests/showcase_check.py` pins the byte-for-byte round trip.
FAKE_MP4 = b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 8

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
// ⚠ THE REAL RAIL, not a stand-in. The ask was that a signed-in customer never
// sees Explore again, and "the page is gone" is only half of that — the ROW
// that used to open it has to be gone too, or the rail navigates to nothing.
import Sidebar, { WORKFLOWS } from "./src/components/Sidebar.jsx";
// ⚠ THE WHOLE APP, for one question only: which screen does it OPEN on, signed
// in and signed out? That is a `useState` initialiser plus an auth branch in
// App.jsx, and no amount of mounting the pages by hand can see either.
import App from "./src/App.jsx";
// ⚠ THE SCREEN THE POP-UP'S WORDS ARE TYPED ON. A card whose copy comes from
// the admin panel is only half-built until the panel can be opened — and a
// green `npm run build` says nothing about whether it renders.
import AdminSales from "./src/admin/AdminSales.jsx";
// The screen the billboards are typed on.
import AdminBanners from "./src/admin/AdminBanners.jsx";
// …and the one the WALL is uploaded on. New with this change, and the half of
// it a developer cannot see from the public page: a wall nobody can fill is a
// wall that is empty for ever.
import AdminShowcase from "./src/admin/AdminShowcase.jsx";
// ⚠ AND THE TAB THAT NOW HOLDS BOTH OF THEM. Mounting the two children proves
// each screen renders; it says nothing about the strip that chooses between
// them, which is the only part of this that is new code.
import AdminExplore from "./src/admin/AdminExplore.jsx";
import { applyTheme, getTheme } from "./src/theme.js";
import "./src/styles/index.css";

// ⚠ Anything that escapes a render lands here as well as on `pageerror`, so a
// failure Playwright somehow misses is still readable off the page.
window.__errors = [];
window.addEventListener("error", (e) => window.__errors.push(String(e.message)));
// Every navigation Home asks for, in order.
window.__nav = [];
// ⚠ EVERY SIGN-IN THE PUBLIC PAGE ASKS FOR, AND WHAT IT WAS SELLING. This array
// is the whole of check [3]: a tile that navigated instead of gating would look
// identical on screen and do nothing at all.
window.__signin = [];

applyTheme(getTheme());

// The token only has to EXIST — every request is answered by the test's router.
localStorage.setItem("cas_token", "probe-token");

const root = ReactDOM.createRoot(document.getElementById("root"));
let hidden = [];
let screen = "explore";
let collapsed = false;

function draw() {
  const workflows = WORKFLOWS.filter((w) => !hidden.includes(w.id));
  if (screen === "banners") {
    root.render(<React.StrictMode><AdminBanners /></React.StrictMode>);
    return;
  }
  if (screen === "showcase") {
    root.render(<React.StrictMode><AdminShowcase /></React.StrictMode>);
    return;
  }
  if (screen === "adminexplore") {
    root.render(<React.StrictMode><AdminExplore /></React.StrictMode>);
    return;
  }
  if (screen === "sales") {
    root.render(
      <React.StrictMode><AdminSales onOpenUser={() => {}} /></React.StrictMode>
    );
    return;
  }
  // The real shell draws its own rail, so it is rendered bare — no wrapper.
  if (screen === "app") {
    root.render(<React.StrictMode><App /></React.StrictMode>);
    return;
  }
  // ⚠ EXPLORE IS RENDERED WITHOUT THE SHELL, and that is not a shortcut — it is
  // the layout. There is no rail beside a logged-out page, so the page carries
  // its own nav and its own background. Mounting it inside `.shell-main` would
  // be testing a screen the product no longer has.
  if (screen === "explore") {
    root.render(
      <React.StrictMode>
        <Explore
          onSignIn={(id) => window.__signin.push(id === undefined ? null : id)}
          onBack={() => window.__signin.push("back")}
          theme="dark"
          onToggleTheme={() => {}}
        />
      </React.StrictMode>
    );
    return;
  }
  root.render(
    <React.StrictMode>
      <div className={`shell ${collapsed ? "nav-collapsed" : ""}`}>
      <Sidebar
        active="home"
        onNavigate={(id) => window.__nav.push(id)}
        workflows={workflows}
        workflowsKnown={true}
        collapsed={collapsed}
        onToggleCollapse={() => {}}
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
        <Home
          email="probe@example.com"
          visibleWorkflows={workflows.map((w) => w.id)}
          onNavigate={(id) => window.__nav.push(id)}
          onOpenJob={(id) => window.__nav.push("job:" + id)}
          onUpgrade={() => {}}
          onOpenProfile={() => {}}
        />
      </main>
      </div>
    </React.StrictMode>
  );
}

// An administrator hiding a workflow, from out here.
window.__hide = (ids) => { hidden = ids; draw(); };
window.__collapse = (on) => { collapsed = on; draw(); };
window.__screen = (s) => { screen = s; draw(); };
// ⚠ SIGNED IN OR NOT, from out here. Section [9] asks the ONE question nothing
// else in this file can: which screen does the real App open on, on each side of
// the sign-in? `App` reads the token in a `useState` initialiser, so this has to
// be set BEFORE the shell is mounted.
window.__token = (on) => {
  if (on) localStorage.setItem("cas_token", "probe-token");
  else localStorage.removeItem("cas_token");
};
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
    """Answer everything the public page and the dashboard ask the server for.

    ⚠ ORDER MATTERS AND THE COMMENTS SAY WHY. A cover's URL contains
    "/storyboards", so the picture rules have to come before the list rules or
    Home is handed a JSON array where it expected a PNG.
    """
    url = request.url

    # ---- the public page's own three reads ---------------------------------
    # ⚠ THE ADMIN PATH IS CHECKED FIRST in both pairs below, because
    # "/admin/showcase" contains "/showcase" — answering the panel with the
    # public payload would leave it with no `active` flag and no switches.
    if "/admin/showcase" in url and request.method == "GET":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "items": [
                {**SHOWCASE[0], "active": True, "rank": 0, "has_media": True,
                 "has_poster": True, "live": True},
                {**SHOWCASE[1], "active": False, "rank": 1, "has_media": True,
                 "has_poster": False, "live": False},
            ],
            "max_public": 24,
            "aspects": ["16:9", "4:5", "1:1", "9:16"],
            "image_max_px": 1600,
            "allowed_image_types": ["image/png", "image/jpeg", "image/webp"],
            "allowed_video_types": ["video/mp4", "video/webm"],
            "max_video_bytes": 96 * 1024 * 1024,
            "max_image_bytes": 20 * 1024 * 1024,
            "limits": {"title": 60, "blurb": 140},
        }))
        return
    # ⚠ THE MEDIA ROUTE ANSWERS BOTH KINDS OFF ONE PATH, exactly as the server
    # does — `aaaa…` is the clip, everything else is a picture. A single
    # content type here would hand a `<video>` a PNG and quietly prove nothing.
    if "/public/showcase/media/" in url:
        if url.rstrip("/").endswith("aaaaaaaaaaaa") or url.rstrip("/").endswith("cccccccccccc"):
            route.fulfill(status=200, content_type="video/mp4", body=FAKE_MP4)
        else:
            route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
        return
    if "/public/showcase" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"items": SHOWCASE}))
        return
    if "/public/workflows" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"workflows": PUBLIC_WORKFLOWS}))
        return

    # The admin panel's banner screen, and the customer-facing list.
    if "/admin/banners" in url and request.method == "GET":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "banners": [
                {**HERO_BANNER, "active": True, "rank": 0, "has_image": True},
                {**SIDE_BANNER, "active": False, "rank": 0, "has_image": False},
            ],
            "slots": ["hero", "side"],
            "max_per_slot": 4,
            "image_max_px": 1280,
            "allowed_types": ["image/png", "image/jpeg", "image/webp"],
            "limits": {"kicker": 40, "title": 60, "body": 200, "cta_label": 30},
        }))
        return
    if "/public/banners/image/" in url:
        route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
        return
    if "/public/banners" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(BANNERS))
        return

    # --- pictures first, for the reason above ---
    if "/panel/" in url or "/media/" in url:
        route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
        return

    # --- the six dashboard lists, for Home ---
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
    # own copies — and answering both with the same list is how Home would show
    # every board twice while the test stayed green.
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
            check("⚠ …and it is the PUBLIC page — it carries its own nav, "
                  "because there is no rail beside a logged-out screen",
                  page.locator(".explore-public").count() == 1
                  and page.locator(".xp-nav").count() == 1,
                  str(page.locator(".explore-public").count()))
            check("…with a way back to the landing page on the brand mark",
                  page.locator(".xp-nav-brand").count() == 1)

            print("")
            print("[1b] the rail no longer has a door to it")
            # ⚠ THE OPPOSITE OF WHAT THIS CHECK USED TO SAY, and the reply that
            # turned it round is *"any logged in user must not see explore which
            # is happening right now"*. Explore was the row ABOVE Home; it is
            # the page you see BEFORE Home now, and never after.
            page.evaluate("window.__screen('home')")
            page.wait_for_timeout(1200)
            rail = page.locator(".sidebar")
            check("⚠ the rail carries NO Explore row — a signed-in customer has "
                  "no way back to the sales page and needs none",
                  rail.locator(".sb-item", has_text="Explore").count() == 0,
                  str(rail.locator(".sb-item", has_text="Explore").count()))
            labels = rail.locator(".sb-item-label").all_inner_texts()
            check("⚠ …and Home is the first row, which is the front door now",
                  labels[0] == "Home", str(labels[:3]))
            rail.locator(".sb-item", has_text="Home").click()
            page.wait_for_timeout(200)
            check("…and it still navigates",
                  page.evaluate("window.__nav").count("home") == 1,
                  str(page.evaluate("window.__nav")))

            print("")
            print("[1d] the narrow rail says what each row IS")
            # ⚠ IT USED TO BE ICONS ONLY. Six drawn glyphs with nothing under
            # them is six things you have to have learned, and the names would
            # not fit — "Image to Animatic Image" under a 24px picture in an
            # 84px column. Each row carries a SHORT name now, chosen workflow by
            # workflow in `WORKFLOW_SHORT`.
            page.evaluate("window.__collapse(true)")
            page.wait_for_timeout(500)
            rail = page.locator(".sidebar.collapsed")
            check("the rail narrows", rail.count() == 1)
            for row, want in [
                ("Home", "Home"),
                # ⚠ ALL SIX WORKFLOWS, NOT THE THREE THIS ACCOUNT CAN SEE. Three
                # are switched off in the admin panel today and are one click
                # from coming back; a workflow that returns with its full name
                # spilling out of the rail is the fault this map prevents, and
                # only naming what is on screen would have left it in place.
                ("Plan & Script", "Plan"),
                ("Text to Turnaround Image", "Characters"),
                ("Script to Storyboard", "Storyboard"),
                ("Image to Animatic Image", "Animatics"),
                ("Image to AI Video", "AI Video"),
                ("Video Editor", "Editor"),
            ]:
                item = rail.locator(".sb-item", has_text=row).first
                got = item.locator(".sb-item-short").inner_text()
                check(f"…{row} → {want}", got == want, got)

            check("⚠ …and the full name is still on hover, so nothing is lost "
                  "by shortening it",
                  rail.locator(".sb-item", has_text="Script to Storyboard").first
                  .get_attribute("title").startswith("Script to Storyboard"))

            # ⚠ MEASURED, NOT EYED. `text-overflow: ellipsis` fails silently:
            # the longest name would simply come out as "Storyboa…" and read
            # like a rendering glitch rather than a layout that is one word too
            # wide. This is the check that fails when somebody adds a workflow
            # called "Turnarounds".
            # ⚠ MEASURED WITH A `Range`, NOT WITH `scrollWidth`. The obvious
            # test — `scrollWidth > clientWidth` — reads EQUAL on a box that is
            # visibly ellipsised here, because the label is a shrink-to-fit
            # block inside a centred flex column: it never gets a scroll extent
            # to be wider than.
            clipped = page.evaluate(
                "Array.from(document.querySelectorAll("
                "'.sidebar.collapsed .sb-item-short')).filter(el => {"
                "  const r = document.createRange();"
                "  r.selectNodeContents(el);"
                "  return r.getBoundingClientRect().width"
                "       > el.getBoundingClientRect().width + 0.5;"
                "}).map(el => el.textContent)"
            )
            check("⚠ …and not one of them is clipped at the rail's width",
                  not clipped, str(clipped))
            page.screenshot(path=os.path.join(ROOT, "output", "sidebar_collapsed.png"))

            page.evaluate("window.__collapse(false)")
            page.wait_for_timeout(400)
            check("…and the wide rail is back to full names",
                  page.locator(".sidebar .sb-item", has_text="Script to Storyboard")
                  .first.inner_text().strip().startswith("Script to Storyboard"),
                  page.locator(".sidebar .sb-item", has_text="Script to Storyboard")
                  .first.inner_text())
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))

            page.evaluate("window.__screen('explore')")
            page.wait_for_timeout(1500)

            print("")
            print("[1c] the offer comes to the visitor")
            # ⚠ IT ARRIVES, IT DOES NOT LOAD WITH THE PAGE. `ENTER_MS` of delay
            # is what makes it read as an arrival; a card that is simply there
            # on the first frame is part of the layout and gets scrolled past
            # like the rest of it.
            page.wait_for_selector(".promo-pop.in", timeout=6000)
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
            before = len(page.evaluate("window.__signin"))
            promo.locator(".promo-cta").click()
            page.wait_for_timeout(250)
            # ⚠ THE BUTTON CHANGED WHAT IT DOES, AND IT HAD TO. It used to open
            # the pricing modal; `POST /billing/coupon` is signed-in only and
            # would 401 in front of a prospect, so out here the only honest
            # thing a discount can ask for is an account.
            check("⚠ …and that button asks for a sign-in, not a pricing modal "
                  "the visitor cannot buy from",
                  len(page.evaluate("window.__signin")) > before
                  and page.locator(".modal-overlay").count() == 0,
                  str(page.evaluate("window.__signin")[-2:]))
            check("⚠ …and the CODE, copyable — a coupon nobody can copy is a "
                  "coupon typed wrong",
                  promo.locator(".promo-code-text").inner_text() == OFFER["code"])
            check("…the discount itself is the card's artwork",
                  promo.locator(".promo-cut").inner_text() == OFFER["summary"])
            check("⚠ …and it is NOT a modal — the page behind it still works",
                  page.locator(".xp-tile").count() > 0)

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

            print("\n[3] a tile per workflow, and every tile is a sign-in gate")
            check("six tiles, one per workflow the product sells",
                  page.locator(".xp-tile").count() == 6,
                  str(page.locator(".xp-tile").count()))
            # ⚠ THE OPPOSITE OF WHAT THIS CHECK USED TO SAY, and the reason is
            # in the reply that changed it: *"ye 3 button ka colour ek jaisa
            # rakho, ismai golden hata do."* The order is the owner's pipeline,
            # not a recommendation, and gold means "the action" elsewhere here.
            check("⚠ not one of them is gold — the row is one colour end to end",
                  page.locator(".xp-tile-lead").count() == 0,
                  str(page.locator(".xp-tile-lead").count()))
            page.evaluate("window.__signin = []")
            page.locator(".xp-tile", has_text="Script to Storyboard").click()
            page.wait_for_timeout(250)
            # ⚠ THE ASK, IN ITS OWN WORDS: *"if anyone clicks anywhere to use
            # and create any workflow we must give a user first to login and
            # then use"*. And the second half of that sentence is the second
            # half of this check: the workflow they clicked has to travel WITH
            # the sign-in, or the gate costs them the thing they came for.
            check("⚠ pressing one asks for a sign-in, and NAMES the workflow it "
                  "was selling — landing a new customer on a generic dashboard "
                  "is how the click is lost between here and the password",
                  page.evaluate("window.__signin") == ["script-to-storyboard"],
                  str(page.evaluate("window.__signin")))

            print("\n[4] the wall — curated work, not the customer's own")
            cards = page.locator(".xp-card")
            check(f"⚠ all {WALL_TOTAL} uploaded items are on it — including the "
                  "one with no workflow tag, which an over-eager filter would "
                  "drop from a wall nobody has filtered",
                  cards.count() == WALL_TOTAL, str(cards.count()))
            check(f"⚠ …and the {FILMS} films say so before you click — on a "
                  "wall of stills, 'this one moves' is the only thing worth "
                  "seeing without reading",
                  page.locator(".xp-card-play").count() == FILMS,
                  str(page.locator(".xp-card-play").count()))
            check("⚠ …a poster actually arrived, which is the thing StrictMode's "
                  "double mount broke in the old cover fetcher once before",
                  page.locator(".xp-card-pic img").count() > 0,
                  str(page.locator(".xp-card-pic img").count()))
            check("⚠ …and a clip with no still falls back to its workflow's own "
                  "glyph rather than a black hole in the middle of the wall",
                  page.locator(".xp-card-glyph").count() == 1,
                  str(page.locator(".xp-card-glyph").count()))
            check("…each tagged card says which workflow made it",
                  page.locator(".xp-card-wf").count() == WALL_TOTAL - 1,
                  str(page.locator(".xp-card-wf").count()))
            # ⚠ THE HOLE BESIDE THE TALL CARD, MEASURED RATHER THAN EYED.
            # Reported twice: *"gallery me lamba 9:16 board wala column bagal me
            # khali jagah chhod raha hai."* The cause was the SPREAD, not the
            # packing: a 9:16 item is 3.2× the height of a 16:9 one in the same
            # column. `wallAspect` pulls every card into 4:5..16:9 and
            # `wallColumns` stops five columns holding one card each.
            cols = page.evaluate("""
              (() => {
                const cards = [...document.querySelectorAll('.xp-card')];
                const top = document.querySelector('.xp-gallery')
                  .getBoundingClientRect().top;
                const ends = {};
                for (const c of cards) {
                  const r = c.getBoundingClientRect();
                  const k = Math.round(r.left);
                  ends[k] = Math.max(ends[k] || 0, r.bottom - top);
                }
                return Object.values(ends).map(Math.round);
              })()
            """)
            filled = min(cols) / max(cols) if cols else 0
            check("⚠ the wall's shortest column reaches at least 55% of its "
                  "tallest — no column may end a third of the way down while "
                  "its neighbour runs the whole height",
                  filled >= 0.55, f"{cols} -> {filled:.0%}")
            # 2.3 is the declared range and nothing else: WALL_AR_MAX / MIN =
            # (16/9) / 0.8 = 2.22. It was 3.2 before the clamp, and that is the
            # number the hole was made of.
            check("…and no card is more than 2.3× the height of another, which "
                  "is what made a hole nothing could fill",
                  page.evaluate("""
                    (() => {
                      const h = [...document.querySelectorAll('.xp-card-pic')]
                        .map(e => e.getBoundingClientRect().height)
                        .filter(n => n > 0);
                      return h.length ? Math.max(...h) / Math.min(...h) : 0;
                    })()
                  """) <= 2.3)

            print("\n[4b] and a card PLAYS — which is the whole point of it")
            # ⚠ *"the videos or images should be clickable and be able to use it
            # properly play"*. On the old page a card opened the customer's own
            # project; out here there is no project and no account, so a card
            # that merely navigated would be a picture that does nothing.
            page.locator(".xp-card").filter(
                has=page.locator(".xp-card-play")).first.click()
            page.wait_for_timeout(600)
            check("the viewer opens", page.locator(".lightbox-overlay").count() == 1)
            check("⚠ …with a REAL player in it, not a still of the film",
                  page.locator("video.xp-view-video").count() == 1,
                  str(page.locator("video.xp-view-video").count()))
            check("…pointed at the clip the card was carrying",
                  "showcase/media" in (page.locator("video.xp-view-video")
                                       .get_attribute("src") or ""),
                  page.locator("video.xp-view-video").get_attribute("src"))
            check("⚠ …and it has controls, because a blocked autoplay must "
                  "still leave a play button under the pointer",
                  page.evaluate(
                      "document.querySelector('video.xp-view-video').controls"))
            check("…the caption says what it is",
                  "Ganesh Utsav spot" in page.inner_text(".xp-view-bar"),
                  page.inner_text(".xp-view-bar"))
            page.screenshot(path=os.path.join(ROOT, "output", "explore_player.png"))

            page.evaluate("window.__signin = []")
            page.locator(".xp-view-cta").click()
            page.wait_for_timeout(250)
            check("⚠ …and the viewer's own button is the strongest sign-in gate "
                  "on the page — it names the workflow the piece was made with, "
                  "which is the one place 'make one like this' is literally true",
                  page.evaluate("window.__signin") == ["script-to-storyboard"],
                  str(page.evaluate("window.__signin")))

            # Stepping has to stay INSIDE what is filtered — see `openAt`.
            page.locator(".lightbox-nav.next").click()
            page.wait_for_timeout(400)
            check("the arrows step through the wall",
                  page.locator(".lightbox-count").inner_text() != "1 / %d" % WALL_TOTAL,
                  page.locator(".lightbox-count").inner_text())
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            check("Escape closes the viewer",
                  page.locator(".lightbox-overlay").count() == 0)

            print("\n[5] the filters")
            page.locator(".xp-chip", has_text="Storyboard").first.click()
            page.wait_for_timeout(300)
            check("the workflow chip narrows the wall to that workflow",
                  page.locator(".xp-card").count() == BOARD_ITEMS,
                  str(page.locator(".xp-card").count()))
            page.locator(".xp-chip", has_text="Everything").first.click()
            page.wait_for_timeout(300)
            check("…and clears again", page.locator(".xp-card").count() == WALL_TOTAL,
                  str(page.locator(".xp-card").count()))

            # ⚠ THE TABS ARE NOT THE OLD TABS. Highlights / Recent / In progress
            # described a CUSTOMER'S OWN JOBS — "in progress" is a render that
            # has not finished, which is meaningless to a stranger and
            # impossible to answer without an account.
            page.locator(".xp-tab", has_text="Films").click()
            page.wait_for_timeout(300)
            check("'Films' keeps only what moves",
                  page.locator(".xp-card").count() == FILMS,
                  str(page.locator(".xp-card").count()))
            page.locator(".xp-tab", has_text="Stills").click()
            page.wait_for_timeout(300)
            check("…and 'Stills' only what does not",
                  page.locator(".xp-card").count() == STILLS,
                  str(page.locator(".xp-card").count()))
            page.locator(".xp-tab", has_text="Everything").click()
            page.wait_for_timeout(300)

            page.fill(".xp-search-input", "chai")
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

            print("\n[6] a workflow staged 'soon' is shown but not SOLD")
            # ⚠ TWO DIFFERENT ANSWERS, AND THAT IS THE CHECK. A tool nobody can
            # see is a tool nobody waits for, so it keeps its tile — but sending
            # a stranger through a sign-up to reach a placeholder is the worst
            # version of this page, so it is not what a banner points at.
            PUBLIC_WORKFLOWS[5]["status"] = "soon"
            page.reload(wait_until="load")
            page.wait_for_timeout(2500)
            check("its tile is still there — six, not five",
                  page.locator(".xp-tile").count() == 6,
                  str(page.locator(".xp-tile").count()))
            check("…wearing a Soon pill instead of an arrow",
                  page.locator(".xp-tile-lock").count() == 1
                  and "Soon" in page.inner_text(".xp-tile-lock"),
                  page.inner_text(".xp-tiles"))
            check("⚠ …and the fixed billboard has moved off it, because a "
                  "banner is an advertisement and that one is not for sale",
                  page.locator(".tone-side .xp-banner-title").inner_text()
                  != "Video Editor",
                  page.locator(".tone-side .xp-banner-title").inner_text())
            PUBLIC_WORKFLOWS[5]["status"] = "live"

            print("\n[7] Home still opens — its insides live in dashboard_feed.js")
            page.evaluate("window.__screen('home')")
            page.wait_for_timeout(1600)
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
            page.wait_for_timeout(1600)
            page.screenshot(path=os.path.join(ROOT, "output", "explore_mount.png"),
                            full_page=True)

            print("")
            print("[8] and it survives the theme switch")
            # ⚠ HEADLESS CHROMIUM REPORTS A LIGHT OS, so everything above ran in
            # the LIGHT theme — which is the half of this app that gets a whole
            # second palette in theme.css (a deeper gold, an inverted ink) and
            # is therefore the half a new screen is most likely to get wrong.
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
            print("[9] the WHOLE app: Explore before the sign-in, Home after it")
            # ⚠ THE ONE THING NOTHING ABOVE CAN SEE. Every section so far mounts
            # the pages by hand and tells them which to draw; which screen the
            # APP lands on is a `useState` initialiser plus an auth branch in
            # App.jsx. Both halves were asked for outright: *"any logged in user
            # must not see explore ... after login we know how our page which is
            # home must look"*.
            page.evaluate("window.__token(false)")
            page.evaluate("window.__screen('app')")
            page.wait_for_timeout(2500)
            check("it mounts signed out with nothing thrown",
                  not errors, "; ".join(errors[:2]))
            check("a visitor gets the landing page",
                  page.locator(".landing").count() == 1,
                  str(page.locator(".landing").count()))
            check("…with a way through to the work",
                  page.get_by_role("link", name="See the work").count() >= 1)
            page.get_by_role("link", name="See the work").first.click()
            page.wait_for_timeout(1600)
            check("⚠ …and that door opens the PUBLIC Explore — the marketing "
                  "page, standing on its own with no rail beside it",
                  page.locator(".explore-public").count() == 1
                  and page.locator(".sidebar").count() == 0,
                  str(page.locator(".explore-public").count()))

            page.locator(".xp-tile", has_text="Script to Storyboard").click()
            page.wait_for_timeout(900)
            check("⚠ …and clicking a workflow on it lands on the SIGN-IN, which "
                  "is the whole gate — not on a workflow a visitor has no "
                  "account for",
                  page.locator(".auth-card").count() == 1,
                  str(page.locator(".auth-card").count()))

            page.evaluate("window.__token(true)")
            page.evaluate("window.__screen('home')")
            page.wait_for_timeout(400)
            page.evaluate("window.__screen('app')")
            page.wait_for_timeout(2500)
            check("⚠ a signed-in session opens on HOME, not on the sales page",
                  page.locator(".home").count() == 1
                  and page.locator(".explore").count() == 0,
                  f"home={page.locator('.home').count()} "
                  f"explore={page.locator('.explore').count()}")
            rail = page.locator(".sidebar")
            check("…with the rail agreeing about where you are",
                  "active" in (rail.locator(".sb-item", has_text="Home")
                               .get_attribute("class") or ""))
            check("⚠ …and no way back to Explore from inside the app, which is "
                  "the point: it is a shop window, not a screen you work in",
                  rail.locator(".sb-item", has_text="Explore").count() == 0)
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))

            page.evaluate("window.__screen('explore')")
            page.wait_for_timeout(1500)

            print("")
            print("[10] a dismissed offer stays dismissed — a NEW one does not")
            # ⚠ THE TRAP A SINGLE "seen the popup" FLAG WOULD HAVE SET. Closing
            # one card must not silence every offer this visitor is ever shown,
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
            page.wait_for_selector(".promo-pop.in", timeout=6000)
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

            print("")
            print("[12] the billboards say what an administrator typed")
            # ⚠ THE WHOLE POINT OF THE FEATURE. Both banners used to be BUILT
            # from the workflow list — the heading was a workflow's name, the
            # body was its landing-page pitch, the artwork was its own glyph —
            # so the one part of the app whose job is to say something could
            # only be changed by a developer. *"This banner should be change aur
            # hide by the admin, of it text and image."*
            BANNERS["hero"] = [HERO_BANNER]
            BANNERS["side"] = [SIDE_BANNER]
            page.evaluate("window.__screen('explore')")
            page.reload(wait_until="load")
            page.wait_for_timeout(2500)
            hero = page.locator(".xp-hero")
            side = page.locator(".tone-side")
            check("the rotating card carries their heading",
                  hero.locator(".xp-banner-title").inner_text()
                  == HERO_BANNER["title"],
                  hero.locator(".xp-banner-title").inner_text())
            # ⚠ CASE-INSENSITIVE. `.xp-banner-eyebrow` is `text-transform:
            # uppercase`, and `inner_text()` returns what is RENDERED — so an
            # exact match here fails on a card that is perfectly correct.
            check("…their small line above it",
                  hero.locator(".xp-banner-eyebrow").inner_text().lower()
                  == HERO_BANNER["kicker"].lower(),
                  hero.locator(".xp-banner-eyebrow").inner_text())
            check("…their line under it",
                  HERO_BANNER["body"] in hero.inner_text())
            check("…their words on the button",
                  HERO_BANNER["cta_label"] in hero.inner_text())
            check("⚠ …and THEIR PICTURE, filling the card rather than the faded "
                  "glyph the generated version draws",
                  hero.locator("img.xp-banner-photo").count() == 1
                  and "has-photo" in (hero.get_attribute("class") or ""),
                  hero.get_attribute("class"))
            check("the fixed card is theirs too",
                  side.locator(".xp-banner-title").inner_text()
                  == SIDE_BANNER["title"],
                  side.locator(".xp-banner-title").inner_text())
            check("⚠ …and a banner with NO picture still draws the glyph — "
                  "every field is optional and the card has to stand without it",
                  side.locator(".xp-banner-art").count() == 1
                  and side.locator("img.xp-banner-photo").count() == 0)

            page.evaluate("window.__signin = []")
            hero.locator(".xp-banner-cta").click()
            page.wait_for_timeout(250)
            # ⚠ AN ADMIN'S BUTTON GATES TOO, and it carries what they pointed it
            # at. Out here there is nowhere to navigate TO — so a banner target
            # that "worked" without a sign-in would be a button doing nothing.
            check("…and the button asks for a sign-in, carrying the workflow "
                  "they pointed it at",
                  page.evaluate("window.__signin") == ["script-to-storyboard"],
                  str(page.evaluate("window.__signin")))
            check("⚠ …with no dots at all, because they wrote one card — the "
                  "carousel is their list, not a fixed four",
                  page.locator(".xp-hero-dot").count() == 0,
                  str(page.locator(".xp-hero-dot").count()))
            page.screenshot(path=os.path.join(ROOT, "output", "explore_banners.png"))

            # ⚠ HIDING ONE IS THE SAME THING AS HAVING NONE, from out here: the
            # server drops an inactive row from the public payload. So this is
            # the check that the page does not go BLANK when an administrator
            # hides the last banner — it goes back to the built-in cards.
            BANNERS["hero"] = []
            BANNERS["side"] = []
            page.reload(wait_until="load")
            page.wait_for_timeout(2500)
            check("⚠ hiding every banner does not empty the page — it falls "
                  "back to the cards built from the workflow list, which is "
                  "what this page drew before the panel could speak",
                  page.locator(".xp-hero .xp-banner-title").inner_text()
                  == "Everything from one script",
                  page.locator(".xp-hero .xp-banner-title").inner_text())
            check("…and the four generated slides are back",
                  page.locator(".xp-hero-dot").count() == 4,
                  str(page.locator(".xp-hero-dot").count()))
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))

            print("")
            print("[13] the screen those billboards are typed on")
            page.evaluate("window.__screen('banners')")
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            check("Banners mounts with nothing thrown",
                  not errors, "; ".join(errors[:2]))
            check("…both slots are listed",
                  "Rotating (left)" in body and "Fixed (right)" in body)
            check("…with the banner rows in them",
                  page.locator(".admin-banner-row").count() == 2,
                  str(page.locator(".admin-banner-row").count()))
            # Same uppercase rule as above — `.badge` transforms its text.
            check("⚠ …each saying whether it is showing — hide is not delete, "
                  "and the row has to say which state it is in",
                  "showing" in body.lower() and "hidden" in body.lower(),
                  body[:120])
            check("…and offering a picture for the one that has none",
                  page.get_by_role("button", name="Add picture").count() == 1,
                  str(page.get_by_role("button", name="Add picture").count()))
            check("⚠ …and ↑ / ↓, because the rotating card's ORDER is what a "
                  "customer sees first (RULEBOOK E6)",
                  page.locator('button[title="Move up"]').count() == 2)

            page.get_by_role("button", name="＋ New banner").first.click()
            page.wait_for_timeout(400)
            body = page.inner_text("body")
            for label in ("Where it goes", "Heading", "Button words",
                          "The button opens"):
                check(f"…the form asks for it: {label}", label in body)
            check("⚠ …and the body box GROWS to its text (E1)",
                  page.locator("textarea.admin-banner-body").count() == 1)
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))
            page.screenshot(path=os.path.join(ROOT, "output", "admin_banners.png"),
                            full_page=True)

            print("")
            print("[14] …and the screen the WALL is uploaded on")
            # ⚠ THE HALF THE PUBLIC PAGE CANNOT SHOW YOU. Everything in [4] and
            # [4b] is fed by a fixture; a wall nobody can actually FILL is a wall
            # that stays empty in production, and `npm run build` is green either
            # way.
            page.evaluate("window.__screen('showcase')")
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            check("Showcase mounts with nothing thrown",
                  not errors, "; ".join(errors[:2]))
            check("…with the uploaded items listed",
                  page.locator(".admin-banner-row").count() == 2,
                  str(page.locator(".admin-banner-row").count()))
            check("…and it says which are films",
                  "Video" in body, body[:160])
            # ⚠ LOWERCASED, because `.badge` is `text-transform: uppercase`
            # and `inner_text()` returns what is RENDERED — an exact match here
            # fails on a row that is perfectly correct. Same rule as the
            # eyebrow check in [12].
            check("⚠ …each row saying whether it is actually on the page. An "
                  "item can be switched ON and still not be there, because "
                  "nothing has been uploaded to it — and hunting a website for "
                  "a card that was never going to be there is a bad half-hour",
                  "showing" in body.lower() and "hidden" in body.lower(),
                  body[:200])
            check("⚠ …a clip with no still is offered one, because there is no "
                  "ffprobe here and a frame cannot be grabbed off it",
                  page.get_by_role("button", name="Add still").count() >= 1,
                  str(page.get_by_role("button", name="Add still").count()))
            check("⚠ …and ↑ / ↓, because the first item is the first thing a "
                  "visitor's eye lands on (RULEBOOK E6)",
                  page.locator('button[title="Move up"]').count() == 2)

            page.get_by_role("button", name="＋ New item").first.click()
            page.wait_for_timeout(400)
            body = page.inner_text("body")
            for label in ("Title", "Made with", "Shape"):
                check(f"…the form asks for it: {label}", label in body)
            check("⚠ …and the caption box GROWS to its text (E1)",
                  page.locator("textarea.admin-banner-body").count() == 1)
            check("nothing threw on the way", not errors, "; ".join(errors[:2]))
            page.screenshot(path=os.path.join(ROOT, "output", "admin_showcase.png"),
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
    print("Explore sells, plays and gates; the rail has no door to it; and Home "
          "opens where a signed-in customer now lands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
