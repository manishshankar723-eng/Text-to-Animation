"""explore_layout_check.py — the PUBLIC Explore page, measured in Chromium.

Run:  python tests/explore_layout_check.py

⚠ THIS FILE EXISTS BECAUSE A GREEN BUILD SHIPPED A BLANK FIRST SCREEN. The
public page was given the app's own rail and the app's own two-column grid, and
`npm run build` passed, and `showcase_check.py` passed with thirty source
assertions — and what a visitor actually got was a full screen of nothing, with
the nav floating in the middle of it and every row of the page below the fold.
Reported with a screenshot: *"pahla image pura blank hai, magar jab scroll kiya
to tab aaya ye content — uper mai blank kyun dikh raha hai."*

⚠ THE CAUSE WAS ONE CSS LINE AND NO GREP COULD HAVE SEEN IT. The rail was given
`grid-row: 1 / -1`. **A negative grid line counts back from the end of the
EXPLICIT grid**, and that grid declared no rows at all — so `-1` resolved to the
first line, the span collapsed to a single row, and that row was as tall as the
rail: 100dvh. The nav shared it and was centred inside it. Every assertion in
`showcase_check.py` about that grid was TRUE, and the page was still wrong.

So this file measures GEOMETRY. RULEBOOK G7: `npm run build` passing is not
evidence that a screen renders, and neither is a source check.

⚠ AND IT IS PROVED TO FAIL FIRST, in the page rather than by editing the repo.
`break_the_layout()` re-creates the exact broken arrangement with two injected
CSS rules — `display: contents` on the wrapper puts every row back to being a
direct grid child, and the span goes back on the rail — then the same
measurements are taken and must come back WRONG. A check that has never failed
is not a check.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

RAIL_OPEN = 280
RAIL_COLLAPSED = 92

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_api(port, tmp):
    env = dict(os.environ)
    env.update({
        "API_USER_STORE": "local",
        "API_JOB_STORE": "memory",
        "API_LOCAL_USERS_PATH": os.path.join(tmp, "users.json"),
        "API_LOCAL_DRAFTS_PATH": os.path.join(tmp, "drafts.json"),
        "API_LOCAL_EVENTS_PATH": os.path.join(tmp, "events.json"),
        "API_LOCAL_JOBS_PATH": os.path.join(tmp, "jobs.json"),
        "API_LOCAL_FEATURES_PATH": os.path.join(tmp, "features.json"),
        "API_LOCAL_TIERS_PATH": os.path.join(tmp, "tiers.json"),
        "API_LOCAL_OFFERS_PATH": os.path.join(tmp, "offers.json"),
        "API_LOCAL_BANNERS_PATH": os.path.join(tmp, "banners.json"),
        "API_LOCAL_SHOWCASE_PATH": os.path.join(tmp, "showcase.json"),
        "API_REAP_ORPHANED_JOBS": "0",
        "JWT_SECRET": "explore-layout-check-not-a-real-secret",
        "PYTHONIOENCODING": "utf-8",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            print("  api died:\n", proc.stdout.read())
            return None, base
        try:
            with urllib.request.urlopen(base + "/openapi.json", timeout=2):
                return proc, base
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    return None, base


def start_vite(port, api_base):
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        return None
    env = dict(os.environ)
    env["VITE_API_BASE"] = api_base
    proc = subprocess.Popen(
        [npx, "vite", "--port", str(port), "--host", "127.0.0.1", "--strictPort"],
        cwd=CLIENT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", shell=os.name == "nt",
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


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------
# ⚠ EVERY NUMBER IS READ OFF THE REAL BOX, not inferred from a class name. The
# bug this file was written for left every class exactly where it belonged.
GEOMETRY_JS = r"""
() => {
  const box = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { top: r.top + window.scrollY, left: r.left, width: r.width, height: r.height };
  };
  return {
    view: { w: window.innerWidth, h: window.innerHeight },
    rail: box(".sidebar"),
    page: box(".xp-page"),
    nav: box(".xp-nav"),
    banners: box(".xp-banners"),
    tiles: box(".xp-tiles"),
    brandName: (document.querySelector(".sb-brand-name") || {}).textContent || "",
    // ⚠ THE ROWS, IN THE ORDER THEY ARE DRAWN, with the one wearing the
    // highlight named. "Explore above Home" is an ORDER, and an order can only
    // be checked by reading the list rather than by asking whether a row exists.
    rows: [...document.querySelectorAll(".sb-nav .sb-item")].map((el) => ({
      label: (el.querySelector(".sb-item-label") || {}).textContent || "",
      active: el.classList.contains("active"),
      top: el.getBoundingClientRect().top,
    })),
    hasLogo: !!document.querySelector(".sidebar .sb-logo svg, .sidebar .sb-logo img, .sidebar .sb-logo"),
    hasCollapse: !!document.querySelector(".sidebar .sb-collapse"),
    hasAvatar: !!document.querySelector(".sidebar .sb-brand-avatar"),
    footButton: (document.querySelector(".sb-upgrade") || {}).innerText || "",
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
  };
}
"""

# ⚠ THE BUG, PUT BACK, IN THE PAGE. Three rules re-create the broken version
# exactly: `display: contents` dissolves the wrapper so every row is a grid item
# of `.explore-public` again, the rows are forced back into column 2, and the
# rail gets its span back. Nothing in the repo is touched.
#
# ⚠ THE COLUMN RULE IS NOT OPTIONAL AND THE FIRST ATTEMPT LEFT IT OUT — without
# it the rows auto-place SPARSELY (half of them landing in column 1, under the
# rail) which is a different, more obvious bug, and the nav stayed at the top. It
# is `grid-column: 2` forcing every row into one column that put the nav in the
# rail's own 100dvh row and centred it there.
#
# ⚠ AND IT IS SELECTED THROUGH `.xp-page`, NOT `.explore-public`. `display:
# contents` changes the BOX tree; selectors still match the DOM tree, where these
# rows are children of the wrapper whatever it is displaying as.
BREAK_CSS = """
  .xp-page { display: contents !important; }
  .explore-public > .sidebar { grid-row: 1 / -1 !important; }
  .xp-page > .xp-nav,
  .xp-page > .xp-banners,
  .xp-page > .xp-tiles,
  .xp-page > .xp-work-head,
  .xp-page > .xp-toolbar,
  .xp-page > .xp-chips,
  .xp-page > .xp-gallery,
  .xp-page > .xp-empty,
  .xp-page > .xp-foot { grid-column: 2 !important; }
"""


def measure(page):
    return page.evaluate(GEOMETRY_JS)


def run(page, app):
    print("\n--- the page opens on its content, not on a blank screen ---")
    page.goto(app + "?explore=1", wait_until="networkidle")
    page.wait_for_selector(".xp-page", timeout=15000)
    g = measure(page)

    check("the rail is drawn", bool(g["rail"]), str(g["rail"]))
    check("the page column is drawn", bool(g["page"]), str(g["page"]))
    check("the nav is drawn", bool(g["nav"]), str(g["nav"]))
    if not (g["rail"] and g["page"] and g["nav"]):
        return

    # ⚠ THE ASSERTION THE BUG WOULD HAVE FAILED, AND IT IS THE *HEIGHT*, NOT THE
    # TOP. This was written as `nav.top < 160` first and passed on the broken page
    # — because the nav's BOX was always at the top. What the bug did was STRETCH
    # that box down a 100dvh row, leaving `.landing-nav`'s own `align-items:
    # center` to float the links half way down an otherwise blank screen. The box
    # never moved; only its height changed. A header is a header-sized thing.
    check(
        "the nav is a header, not a screen-tall block",
        g["nav"]["height"] < 200,
        f"nav height={g['nav']['height']:.0f}px, viewport={g['view']['h']}px",
    )
    check(
        "and it starts at the top of the page",
        g["nav"]["top"] < 160,
        f"nav top={g['nav']['top']:.0f}px",
    )
    # And the first real row has to be ON the first screen.
    check(
        "the first content row is above the fold",
        g["banners"] and g["banners"]["top"] < g["view"]["h"],
        f"banners top={(g['banners'] or {}).get('top')}, viewport={g['view']['h']}",
    )
    # ⚠ NOTHING MAY SIT UNDER THE RAIL. The old arrangement auto-placed rows and
    # a row that landed in column 1 would be hidden behind it.
    check(
        "the page column starts beside the rail, not under it",
        g["page"]["left"] >= RAIL_OPEN - 1,
        f"page left={g['page']['left']:.0f}, rail width={g['rail']['width']:.0f}",
    )
    check(
        "the rail is the shell's own width",
        abs(g["rail"]["width"] - RAIL_OPEN) <= 1,
        f"{g['rail']['width']:.0f}px, expected {RAIL_OPEN}",
    )
    check(
        "the rail is full height",
        g["rail"]["height"] >= g["view"]["h"] - 1,
        f"rail height={g['rail']['height']:.0f}, viewport={g['view']['h']}",
    )
    # Standing responsive rule: the page body never scrolls sideways.
    check(
        "the page does not scroll sideways",
        g["docScrollW"] <= g["docClientW"] + 1,
        f"scrollW={g['docScrollW']} clientW={g['docClientW']}",
    )

    print("\n--- the three things the report named are on the rail ---")
    check("the mark", g["hasLogo"])
    check("the app's name", bool(g["brandName"].strip()), repr(g["brandName"]))
    check("the collapse toggle", g["hasCollapse"])
    # ⚠ AND THE TWO A VISITOR HAS NO VERSION OF ARE NOT.
    check("no account avatar for a visitor", not g["hasAvatar"])
    check(
        "the foot button asks for a sign-in, not an upgrade",
        "sign in" in g["footButton"].lower(),
        repr(g["footButton"]),
    )

    print("\n--- the rail says which page this is, and how to get back to it ---")
    # ⚠ *"explore ka button kyun nahi dikh raha hai, ye page kahan se khul raha
    # hai? home ke upar explore button daalo."* Without a row for it the rail was
    # a list of places to go from a page it never named — and pressing Home left
    # for the sales page with no way back except a link buried in its nav.
    labels = [r["label"].strip() for r in g["rows"]]
    check("there is an Explore row", "Explore" in labels, str(labels))
    check("there is still a Home row", "Home" in labels, str(labels))
    if "Explore" in labels and "Home" in labels:
        # ⚠ AN ORDER, NOT A PRESENCE. "Above Home" is the whole of the request,
        # and a check that only asks whether the row exists would pass with it
        # sitting at the bottom of the rail.
        check(
            "Explore sits ABOVE Home, as it did on the old signed-in rail",
            labels.index("Explore") < labels.index("Home"),
            f"order: {labels}",
        )
        # DOM order is not screen order. Check the pixels as well.
        ex = g["rows"][labels.index("Explore")]
        hm = g["rows"][labels.index("Home")]
        check("and is drawn above it", ex["top"] < hm["top"],
              f"{ex['top']:.0f} vs {hm['top']:.0f}")
        check("and it is the row wearing the highlight", ex["active"], str(labels))
        check("while Home is not", not hm["active"], str(labels))
    check(
        "the workflows follow underneath",
        len(labels) >= 3 and labels[:2] == ["Explore", "Home"],
        str(labels),
    )

    print("\n--- collapsing narrows the rail and the page follows ---")
    page.click(".sb-collapse")
    page.wait_for_timeout(500)
    c = measure(page)
    check(
        "the rail narrows to the shell's collapsed width",
        abs(c["rail"]["width"] - RAIL_COLLAPSED) <= 1,
        f"{c['rail']['width']:.0f}px, expected {RAIL_COLLAPSED}",
    )
    check(
        "and the page moves with it rather than leaving a gap",
        abs(c["page"]["left"] - RAIL_COLLAPSED) <= 1,
        f"page left={c['page']['left']:.0f}",
    )
    page.click(".sb-collapse")
    page.wait_for_timeout(400)

    print("\n--- under 820px the rail is a block above the page ---")
    page.set_viewport_size({"width": 760, "height": 900})
    page.wait_for_timeout(400)
    n = measure(page)
    check(
        "the rail spans the width",
        n["rail"]["width"] >= n["view"]["w"] - 2,
        f"rail width={n['rail']['width']:.0f}, viewport={n['view']['w']}",
    )
    check(
        "the page sits under it, not beside it",
        n["page"]["left"] < 2,
        f"page left={n['page']['left']:.0f}",
    )
    check(
        "and still no sideways scroll",
        n["docScrollW"] <= n["docClientW"] + 1,
        f"scrollW={n['docScrollW']} clientW={n['docClientW']}",
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(400)

    # -----------------------------------------------------------------------
    print("\n--- proving the check can fail: the old arrangement, put back ---")
    # ⚠ IF THIS SECTION GOES GREEN, THE ASSERTIONS ABOVE ARE MEASURING NOTHING.
    page.add_style_tag(content=BREAK_CSS)
    page.wait_for_timeout(500)
    b = measure(page)
    check(
        "the broken arrangement stretches the nav down a screen-tall row",
        b["nav"]["height"] > 200,
        f"nav height={b['nav']['height']:.0f} — the bug did NOT reproduce, so the "
        f"checks above prove nothing",
    )
    check(
        "and pushes the first content row below the fold",
        b["banners"] and b["banners"]["top"] >= b["view"]["h"],
        f"banners top={(b['banners'] or {}).get('top')} vs viewport {b['view']['h']}",
    )
    print(f"       (reproduced: nav {g['nav']['height']:.0f}px -> "
          f"{b['nav']['height']:.0f}px, first row "
          f"{g['banners']['top']:.0f}px -> {b['banners']['top']:.0f}px)")


def main():
    tmp = tempfile.mkdtemp(prefix="explore-layout-")
    api_proc = vite_proc = None
    try:
        api_port, app_port = free_port(), free_port()
        print("booting the api…")
        api_proc, base = start_api(api_port, tmp)
        if not api_proc:
            print("could not start the api")
            return 1

        print("booting vite…")
        vite_proc = start_vite(app_port, base)
        if not vite_proc:
            print("could not start vite")
            return 1
        app = f"http://127.0.0.1:{app_port}/"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: failures.append(f"page error: {e}"))
            try:
                run(page, app)
            finally:
                browser.close()
    finally:
        for proc in (vite_proc, api_proc):
            if proc:
                proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    print("the public Explore page lays out correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
