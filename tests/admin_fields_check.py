"""EVERY FIELD IN THE ADMIN PANEL IS TALL ENOUGH FOR ITS OWN TEXT — in a browser.

Why this exists, in one sentence: the panel's own filter row shipped with the
bottom third of "Any status" sliced off, and nothing in the repository could see
it.

    tests/admin_check.py      pins the ROUTES, the roles and the event log
    THIS FILE                 opens the panel in Chromium and MEASURES the boxes

The fault it was written for is arithmetic, not design. `theme.css` sets one
global field box for the whole app —

    input, select, textarea { padding: 0.65rem 0.75rem }   /* ~21px vertical */

— which is right for a full-size field in a form. `admin.css` then gives the
panel's compact fields a FIXED height, because trap 2 in that file says every
control in a row must have an identical box. Put the first inside the second and
34px − 21px padding − 2px border leaves an 11px content box for a 17px line of
text. A button in that state lets its label overflow and nobody notices; a form
control CLIPS. `.admin-badge-input` was 30px tall and 8px short — the worst on
the screen.

Reported with three screenshots and the words "text half hide from box not full
view … aur kahi v admin panel aisa problem dikhe to thik kar dena", which is why
this walks EVERY tab and every form the tabs can open rather than the one row in
the screenshot.

⚠ SO THE ASSERTION IS A MEASUREMENT, NOT A SCREENSHOT. For every field on screen
it computes the content box (`clientHeight` minus vertical padding) and the line
box (`line-height`, times `rows` for a textarea) and fails when the first is
smaller than the second. That is engine-independent, needs no reference image,
and cannot pass a field that merely looks right at one font size.

⚠ AND IT ALSO WATCHES FOR TEXT CUT OFF SIDEWAYS. `.admin-main` is
`overflow-x: hidden` and the detail column is narrow, so a line that will not
wrap is a line that is silently trimmed with no scrollbar to reveal it. Any
element whose own overflow is hidden, whose `text-overflow` is `clip` and whose
`scrollWidth` exceeds its box is reported. Elements that deliberately trim with
an ellipsis (`.admin-brand-name`, `.view-as-label`) are exempt, because an
ellipsis is a decision rather than an accident.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/admin_fields_check.py

⚠ NO MONGODB, NO AI KEY, NO SERVER OF YOUR OWN. Every store is pointed at a
temporary directory before `server.config` is imported (the same trick
`admin_check.py` uses), a real uvicorn is started on a free port, and Vite is
started on another. Both are killed in a `finally`.

⚠ THE ADMIN IS PINNED THROUGH `ADMIN_EMAILS`, not promoted through the store.
The API runs in its own process, so writing a role into the local JSON file from
this one is a race against that process's cache; a pinned address is an admin
whatever the document says, which is exactly the property this needs.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

ADMIN = "boss@example.com"
PASSWORD = "password123"
CUSTOMERS = ["cust@example.com", "someone.with.a.long.address@example.com"]

# In CSS pixels. A field is reported when its content box is short of its line
# box by MORE than this. Half a pixel of slack absorbs the sub-pixel rounding
# that `clientHeight` does (it is an integer) against a fractional line-height —
# a real clip on this screen is 6 to 8 pixels, so nothing borderline is being
# waved through.
SLACK = 0.5

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The measurement, run in the page
# ---------------------------------------------------------------------------
# ⚠ `type=range`, `checkbox` and `file` ARE OUT ON PURPOSE. None of them draws
# text of its own, so "the content box is shorter than a line" says nothing
# about them — the slider in the rollout box would be a permanent false
# positive.
MEASURE_JS = r"""
() => {
  const bad = [];
  const label = (el) => {
    const cls = (el.className || "").toString().trim().split(/\s+/).filter(Boolean);
    const id = cls.length ? "." + cls.join(".") : el.tagName.toLowerCase();
    const hint = el.getAttribute("aria-label") || el.placeholder || el.value || "";
    return id + (hint ? ` ("${hint.slice(0, 40)}")` : "");
  };
  const seen = (el) => el.offsetParent !== null || el.getClientRects().length > 0;

  // --- 1. a field shorter than its own line of text -----------------------
  const FIELDS = 'input:not([type="range"]):not([type="checkbox"]):not([type="file"]), select, textarea';
  for (const el of document.querySelectorAll(FIELDS)) {
    if (!seen(el)) continue;
    const cs = getComputedStyle(el);
    const fs = parseFloat(cs.fontSize);
    const lh = cs.lineHeight === "normal" ? fs * 1.2 : parseFloat(cs.lineHeight);
    const rows = el.tagName === "TEXTAREA" ? (el.rows || 1) : 1;
    const content = el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const need = lh * rows;
    if (content + __SLACK__ < need) {
      bad.push({
        kind: "cut vertically",
        what: label(el),
        detail: `content box ${content.toFixed(1)}px, needs ${need.toFixed(1)}px ` +
                `(height ${cs.height}, padding ${cs.paddingTop}/${cs.paddingBottom})`,
      });
    }
  }

  // --- 2. text trimmed sideways with nothing to reveal it -----------------
  // Only leaves, and only where the element itself does the hiding: a parent
  // that scrolls is not a bug, and neither is an intentional ellipsis.
  for (const el of document.querySelectorAll(".admin-shell *")) {
    if (!seen(el) || el.children.length) continue;
    const text = (el.textContent || "").trim();
    if (!text) continue;
    const cs = getComputedStyle(el);
    const hides = cs.overflowX === "hidden" || cs.overflowX === "clip";
    if (!hides || cs.textOverflow !== "clip") continue;
    if (el.scrollWidth > el.clientWidth + 1) {
      bad.push({
        kind: "cut sideways",
        what: label(el),
        detail: `${el.scrollWidth}px of text in a ${el.clientWidth}px box — "${text.slice(0, 50)}"`,
      });
    }
  }
  return bad;
}
""".replace("__SLACK__", str(SLACK))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def post(base, path, body, token=None):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def start_api(port, tmp):
    env = dict(os.environ)
    env.update(
        {
            "API_USER_STORE": "local",
            "API_JOB_STORE": "memory",
            "API_LOCAL_USERS_PATH": os.path.join(tmp, "users.json"),
            "API_LOCAL_DRAFTS_PATH": os.path.join(tmp, "drafts.json"),
            "API_LOCAL_EVENTS_PATH": os.path.join(tmp, "events.json"),
            "API_LOCAL_JOBS_PATH": os.path.join(tmp, "jobs.json"),
            # ⚠ ALL NINE, NOT JUST THE FOUR `admin_check.py` NEEDS. The panel
            # touches every store there is, and the ones this list forgot got
            # written straight into the REPOSITORY ROOT as `.local_features.json`
            # and friends — a test that leaves files in `git status` behind it.
            "API_LOCAL_FEATURES_PATH": os.path.join(tmp, "features.json"),
            "API_LOCAL_TIERS_PATH": os.path.join(tmp, "tiers.json"),
            "API_LOCAL_OFFERS_PATH": os.path.join(tmp, "offers.json"),
            "API_LOCAL_SUBSCRIPTIONS_PATH": os.path.join(tmp, "subscriptions.json"),
            "API_LOCAL_USAGE_PATH": os.path.join(tmp, "usage.json"),
            "API_REAP_ORPHANED_JOBS": "0",
            "JWT_SECRET": "admin-fields-check-not-a-real-secret",
            # See the header: pinned, not promoted.
            "ADMIN_EMAILS": ADMIN,
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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
        cwd=CLIENT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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


def seed(base):
    """An admin, two customers, one recorded payment and one offer.

    ⚠ EVERY TAB HAS TO HAVE SOMETHING ON IT. An empty Sales tab draws no form
    fields at all, so a panel with no data would pass this test by having
    nothing to measure.
    """
    status, body = post(base, "/auth/register", {"email": ADMIN, "password": PASSWORD})
    token = body.get("access_token")
    for email in CUSTOMERS:
        post(base, "/auth/register", {"email": email, "password": PASSWORD})
        post(base, "/auth/login", {"email": email, "password": PASSWORD})
    # A wrong password too, so the Activity feed has a failure row in it.
    post(base, "/auth/login", {"email": CUSTOMERS[0], "password": "wrong"})

    tiers = []
    req = urllib.request.Request(base + "/admin/tiers",
                                headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tiers = (json.loads(r.read())).get("tiers") or []
    except Exception as e:
        print("  could not read tiers:", e)
    paid = next((t["id"] for t in tiers if not t.get("archived")), None)
    if paid:
        post(base, "/admin/subscriptions",
             {"email": CUSTOMERS[0], "tier": paid, "period": "monthly",
              "note": "Bank transfer, invoice INV-0001"}, token=token)
    post(base, "/admin/offers",
         {"code": "LAUNCH50", "label": "Launch week", "kind": "percent",
          "value": 50, "period": "both"}, token=token)
    return token


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------
def sweep(page, where):
    """Measure everything on screen and report it under one label."""
    page.wait_for_timeout(250)
    bad = page.evaluate(MEASURE_JS)
    check(
        f"{where} — every field fits its own text",
        not bad,
        "; ".join(f"{b['kind']}: {b['what']} — {b['detail']}" for b in bad[:6]),
    )
    return bad


def click_if(page, selector, label=None):
    """Click the first match if it is there; say nothing if it is not."""
    loc = page.locator(selector).first
    try:
        if loc.count() and loc.is_visible():
            loc.click()
            page.wait_for_timeout(400)
            return True
    except Exception as e:
        print(f"  (could not click {label or selector}: {e})")
    return False


def tab(page, name):
    page.locator(".admin-tab", has_text=name).first.click()
    page.wait_for_timeout(700)


def main():
    tmp = tempfile.mkdtemp(prefix="admin_fields_")
    api = vite = None
    try:
        api_port, vite_port = free_port(), free_port()
        print("\n--- servers ---")
        api, api_base = start_api(api_port, tmp)
        check("the API started", api is not None, f"port {api_port}")
        if not api:
            return 1
        token = seed(api_base)
        check("the pinned admin has a token", bool(token))
        if not token:
            return 1

        vite = start_vite(vite_port, api_base)
        check("Vite started", vite is not None, f"port {vite_port}")
        if not vite:
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch()
            # 1440×900 is the laptop the panel is designed against. The filter
            # row is a `flex-wrap` row, so a narrower window would wrap it and
            # hide the very row the bug was reported on.
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script(
                f"localStorage.setItem('cas_token', {json.dumps(token)});"
                f"localStorage.setItem('cas_email', {json.dumps(ADMIN)});"
            )
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{vite_port}/?admin=1")
            page.wait_for_selector(".admin-shell", timeout=30000)
            page.wait_for_selector(".admin-tab", timeout=30000)

            print("\n--- the six tabs ---")
            tab(page, "Overview")
            sweep(page, "Overview")

            tab(page, "Users")
            page.wait_for_selector(".admin-filters", timeout=15000)
            sweep(page, "Users — the filter row")
            # ⚠ THE DETAIL PANEL IS WHERE THE PLAN SELECT AND THE PRIVATE NOTE
            # LIVE, and it does not exist until a row is picked.
            if click_if(page, ".admin-row", "a user row"):
                page.wait_for_selector(".admin-detail", timeout=15000)
                sweep(page, "Users — the account detail")
                if click_if(page, ".admin-detail-head-actions .btn", "View as"):
                    sweep(page, "Users — View as")
                    # ⚠ CLOSED BY ITS OWN X, NOT BY Escape. The dialog does not
                    # listen for a key, and an overlay left open swallows the
                    # click on the next tab.
                    click_if(page, ".modal-overlay .modal-close", "the dialog's X")

            tab(page, "Features")
            page.wait_for_selector(".admin-feature", timeout=15000)
            sweep(page, "Features")
            # Open the rollout box, then walk its modes — the address list and
            # the percentage slider are each drawn by only one of them.
            if click_if(page, ".admin-feature-more", "Who sees it"):
                page.wait_for_selector(".admin-rollout", timeout=15000)
                sweep(page, "Features — the rollout box")
                for mode, what in (("allowlist", "an address list"), ("percent", "a percentage")):
                    try:
                        page.locator(".admin-rollout .admin-select").first.select_option(mode)
                        page.wait_for_timeout(600)
                        sweep(page, f"Features — rollout by {what}")
                    except Exception as e:
                        print(f"  (could not set rollout {mode}: {e})")

            tab(page, "Pricing")
            page.wait_for_selector(".admin-tier", timeout=15000)
            sweep(page, "Pricing")

            tab(page, "Sales")
            page.wait_for_selector(".admin-record-open, .admin-tier, .admin-table",
                                  timeout=15000)
            sweep(page, "Sales")
            if click_if(page, ".admin-record-open .btn", "Record a payment"):
                sweep(page, "Sales — record a payment")
            if click_if(page, ".admin-section-head .btn.ghost", "New offer"):
                sweep(page, "Sales — the offer form")

            tab(page, "Activity")
            page.wait_for_selector(".admin-feed, .admin-empty", timeout=15000)
            sweep(page, "Activity")

            print("\n--- the console ---")
            check("nothing reached window.onerror", not errors, "; ".join(errors[:3]))
            browser.close()
    finally:
        for proc in (vite, api):
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"❌ {len(failures)} FAILED:")
        for f in failures:
            print("   -", f)
        return 1
    print("✅ every field in the admin panel fits its own text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
