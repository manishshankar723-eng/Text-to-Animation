"""THE LANDING PAGE SELLS WHAT THE ADMIN PANEL ACTUALLY LAUNCHED — in a browser.

Why this exists, in one sentence: the marketing page kept a hand-written copy of
the workflow list, so hiding a workflow left it advertised to every stranger who
visited and launching one left it unmentioned until somebody edited a JSX file.

    tests/admin_check.py          pins the routes and the roles
    tests/admin_fields_check.py   measures the panel's boxes in Chromium
    THIS FILE                     hides a workflow and watches the SHOP WINDOW

⚠ THE ASSERTION IS A ROUND TRIP, NOT A SNAPSHOT. It boots the real API, counts
the cards a logged-out visitor is shown, hides one workflow through
`PATCH /admin/features/{key}`, reloads the public page and counts again. A test
that only read the endpoint would still pass if the page ignored the answer,
which is exactly the bug being fixed.

It also pins the four things the rename touched, each of which was a real fault:

  1. **The name.** "Character Asset Studio" was the name of the ONE workflow
     that is switched off. No screen may still say it.
  2. **The mark.** `Logo.jsx` draws sprocket holes through an SVG `<mask>`, and
     every instance needs its OWN id — the landing page draws the mark twice
     (nav and footer), and two `<mask id="film">` in one document silently
     resolve to the first. So: two marks, two different mask ids.
  3. **The theme switch**, which is the only one a logged-out visitor can reach.
     Pressing it must stamp `<html data-theme>` AND survive a reload.
  4. **The rail's own name.** `shell.css` notes the sidebar was widened to 280px
     because "Character Studio" ellipsised at 264; "Aniwala AI Studio" is wider
     again, and `.sb-brand-name` trims with an ellipsis, so a name that no
     longer fits would be trimmed SILENTLY. Measured, not eyeballed.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/brand_landing_check.py

⚠ NO MONGODB, NO AI KEY, NO SERVER OF YOUR OWN — the same boot as
`admin_fields_check.py`: every store is pointed at a temporary directory, a real
uvicorn is started on a free port, Vite on another, and both die in a `finally`.

⚠ THE ADMIN IS PINNED THROUGH `ADMIN_EMAILS` rather than promoted through the
store, for the same reason that file gives: the API runs in its own process, so
writing a role from this one is a race against that process's cache.
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
SHOTS = os.path.join(ROOT, "output", "brand_check")

ADMIN = "boss@example.com"
PASSWORD = "password123"

BRAND = "Aniwala AI Studio"
# Every name the app used to answer to. None of them may survive anywhere.
OLD_NAMES = ["Character Asset Studio", "Character Studio"]

# The workflow taken off the shelf mid-test. ⚠ NOT the first one: the page
# numbers its "How a project moves" steps from whatever it was given, so hiding
# something in the MIDDLE also proves the numbering re-flows instead of leaving
# a 1, 2, 4.
HIDE_KEY = "workflow.create-animatic-image"
HIDE_TITLE = "Image to Animatic Image"

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


def api(base, path, body=None, token=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data else "GET"),
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
        "API_LOCAL_SUBSCRIPTIONS_PATH": os.path.join(tmp, "subscriptions.json"),
        "API_LOCAL_USAGE_PATH": os.path.join(tmp, "usage.json"),
        "API_REAP_ORPHANED_JOBS": "0",
        "JWT_SECRET": "brand-landing-check-not-a-real-secret",
        "ADMIN_EMAILS": ADMIN,
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
# The measurement, run in the page
# ---------------------------------------------------------------------------
# ⚠ `scrollWidth > clientWidth` IS THE WHOLE TEST FOR A TRIMMED NAME. The
# element ellipsises, so a name too long for the rail does not overflow visibly
# and does not warn — it just quietly loses its last few letters. Rounded up by
# a pixel because both numbers are integers and the text is fractional.
NAME_FITS_JS = r"""
() => {
  const el = document.querySelector(".sb-brand-name");
  if (!el) return { found: false };
  return {
    found: true,
    text: (el.textContent || "").trim(),
    scroll: el.scrollWidth,
    client: el.clientWidth,
  };
}
"""


def landing_cards(page):
    """The workflow titles the shop window is showing, in order."""
    return [t.strip() for t in page.locator(".lp-wf-card h3").all_inner_texts()]


def run(page, app, base, token):
    os.makedirs(SHOTS, exist_ok=True)

    # ---------------------------------------------------------------- 1. name
    page.goto(app, wait_until="networkidle")
    page.wait_for_selector(".landing-nav", timeout=20000)
    # ⚠ VISIBLE TEXT, NOT `page.content()`. In dev Vite inlines every stylesheet
    # into a `<style>` tag — comments included — so the raw HTML carries CSS
    # prose about how the rail was sized that no visitor will ever read. Asking
    # the DOM for its text asks the question the user actually cares about.
    seen = page.inner_text("body")
    check("landing calls itself " + BRAND, BRAND in seen)
    for old in OLD_NAMES:
        check(f"landing no longer says {old!r}", old not in seen)
    check("the browser tab is renamed too", BRAND in page.title(), page.title())

    # ---------------------------------------------------------------- 2. mark
    marks = page.locator(".landing .brand-mark")
    check("the mark is drawn twice (nav + footer)", marks.count() == 2, str(marks.count()))
    ids = page.eval_on_selector_all(
        ".landing .brand-mark mask", "els => els.map(e => e.id)")
    check("each mark masks with its OWN id", len(ids) == len(set(ids)) and all(ids),
          ", ".join(ids))

    # ------------------------------------------------------- 3. theme switch
    before = page.get_attribute("html", "data-theme")
    page.click(".lp-theme")
    page.wait_for_timeout(200)
    after = page.get_attribute("html", "data-theme")
    check("the landing switch flips the theme", before != after, f"{before} -> {after}")
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".landing-nav", timeout=20000)
    check("and the choice survives a reload",
          page.get_attribute("html", "data-theme") == after, after)
    page.screenshot(path=os.path.join(SHOTS, f"landing-{after}.png"), full_page=True)
    page.click(".lp-theme")
    page.wait_for_timeout(250)
    page.screenshot(path=os.path.join(SHOTS, f"landing-{before}.png"), full_page=True)

    # ------------------------------------------------ 4. the list is the SERVER'S
    status, body = api(base, "/public/workflows")
    served = [w["id"] for w in (body.get("workflows") or [])]
    check("GET /public/workflows answers without a token", status == 200, str(status))
    check("and it needs no account to name the workflows", len(served) > 1, str(served))

    page.reload(wait_until="networkidle")
    page.wait_for_selector(".lp-wf-card", timeout=20000)
    shown = landing_cards(page)
    check("the page shows one card per served workflow",
          len(shown) == len(served), f"{len(shown)} cards vs {len(served)} served")
    check(f"{HIDE_TITLE!r} is on the page to begin with", HIDE_TITLE in shown,
          "; ".join(shown))

    # Take it off the shelf THROUGH THE PANEL'S OWN ROUTE — not by editing a file.
    status, _ = api(base, f"/admin/features/{HIDE_KEY}", {"status": "hidden"},
                    token=token, method="PATCH")
    check("admin can hide a workflow", status == 200, str(status))

    page.reload(wait_until="networkidle")
    page.wait_for_selector(".lp-wf-card", timeout=20000)
    after_hide = landing_cards(page)
    check("hiding it removes the card from the PUBLIC page",
          HIDE_TITLE not in after_hide, "; ".join(after_hide))
    check("and it takes exactly one card with it",
          len(after_hide) == len(shown) - 1, f"{len(shown)} -> {len(after_hide)}")

    steps = [t.strip() for t in page.locator(".step-card .step-num").all_inner_texts()]
    check("the How-it-works steps renumber without a gap",
          steps == [str(i + 1) for i in range(len(after_hide))], "; ".join(steps))

    # Put it back, so the signed-in half of this test sees the full rail.
    api(base, f"/admin/features/{HIDE_KEY}", {"status": "live"}, token=token,
        method="PATCH")

    # ------------------------------------------------ 5. the rail's own name
    page.goto(app, wait_until="networkidle")
    page.evaluate("t => localStorage.setItem('cas_token', t)", token)
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".sb-brand-name", timeout=25000)
    page.wait_for_timeout(600)

    name = page.evaluate(NAME_FITS_JS)
    check("the rail draws the brand", name.get("found") and BRAND in name.get("text", ""),
          json.dumps(name))
    check("the rail's name is NOT trimmed by its own ellipsis",
          name.get("scroll", 0) <= name.get("client", 0) + 1,
          f"{name.get('scroll')}px of text in a {name.get('client')}px box")

    icons = page.locator(".sb-item .sb-ico svg").count()
    check("every rail row wears a drawn glyph, not an emoji", icons >= 4, str(icons))
    page.screenshot(path=os.path.join(SHOTS, "app-sidebar.png"), full_page=False)

    print(f"\n  screenshots → {SHOTS}")


def main():
    tmp = tempfile.mkdtemp(prefix="brand-check-")
    api_proc = vite_proc = None
    try:
        api_port, app_port = free_port(), free_port()
        print("booting the api…")
        api_proc, base = start_api(api_port, tmp)
        if not api_proc:
            print("could not start the api"); return 1

        status, body = api(base, "/auth/register", {"email": ADMIN, "password": PASSWORD})
        token = body.get("access_token")
        if not token:
            print("could not register the admin:", status); return 1

        print("booting vite…")
        vite_proc = start_vite(app_port, base)
        if not vite_proc:
            print("could not start vite"); return 1
        app = f"http://127.0.0.1:{app_port}/"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: failures.append(f"page error: {e}"))
            try:
                run(page, app, base, token)
            finally:
                browser.close()
    finally:
        for proc in (vite_proc, api_proc):
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
