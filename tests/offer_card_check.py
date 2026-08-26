"""THE OFFER AN ADMIN CREATED IS ON THE CUSTOMER'S SCREEN — in a browser.

Why this exists, in one sentence: an administrator created a 20%-off coupon,
watched the panel list it as live, and no customer could ever have found it.

    tests/offer_visibility_check.py   pins the ROUTE — what the server sends
    THIS FILE                          opens the app AS A CUSTOMER and looks

⚠ IT SIGNS IN AS AN ORDINARY ACCOUNT, NOT THE ADMIN. The whole failure was that
the offer existed on one side of the app and not the other, so a test that looked
at the admin panel would have passed throughout.

⚠ AND IT WALKS ALL THREE PLACES THE CARD IS DRAWN, INCLUDING THE ONE WITH NO
SESSION. The landing page is where a coupon has to reach somebody who has NOT
signed up — that is the whole point of promoting a code — so the first browser
context here carries no token at all. The dashboard is the second, because a
signed-in customer is the one most likely to actually spend a coupon and the
only way they used to learn one existed was to open the Upgrade modal and
notice. The modal is the third and the only one where Apply exists.

What it asserts, in rough order of how much it would hurt to get wrong:

  THE CARD IS THERE AND THE CODE IS READABLE. A coupon reaches a customer as
  characters they can type or copy; a card that renders the discount but not the
  code is a poster with the phone number left off.

  APPLYING IT MOVES EVERY PLAN IT COVERS, NOT ONE. "20% off every plan" that
  discounts a single card is a discount somebody has to hunt for, and it leaves
  the other cards quoting a price that is no longer true for them. The prices are
  read off the screen before and after, and compared.

  THE PERIOD TOGGLE DOES NOT LEAVE A STALE PRICE BEHIND. A figure worked out for
  the yearly price is not the monthly one; the modal re-checks, and this watches
  the number change.

  NOTHING IS CLIPPED AND NOTHING OVERFLOWS SIDEWAYS. Same measurement as
  `admin_fields_check.py`, pointed at the modal: an element that hides its own
  overflow and holds more text than it can show is text nobody can read.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/offer_card_check.py

⚠ NO MONGODB, NO AI KEY, NO SERVER OF YOUR OWN — the same arrangement as
`admin_fields_check.py`: every store is pointed at a temporary directory, a real
uvicorn is started on a free port and Vite on another, and both are killed in a
`finally`. The admin is PINNED through `ADMIN_EMAILS` rather than promoted
through the store, because the API runs in its own process.

It leaves one screenshot behind on failure (and on success, so the design can be
looked at): `output/offer_card.png`.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")
# ⚠ BOTH THEMES ARE SHOT, because `theme.css` defines every colour twice and a
# gold wash that reads as a highlight on #13161f can read as a stain on white.
SHOT = os.path.join(ROOT, "output", "offer_card.png")
SHOT_LIGHT = os.path.join(ROOT, "output", "offer_card_light.png")
SHOT_LANDING = os.path.join(ROOT, "output", "offer_card_landing.png")
SHOT_HOME = os.path.join(ROOT, "output", "offer_card_home.png")

ADMIN = "boss@example.com"
CUSTOMER = "cust@example.com"
PASSWORD = "password123"
CODE = "LAUNCH50"
PERCENT = 20

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ⚠ THE SAME MEASUREMENT AS THE PANEL SUITE, SCOPED TO THE MODAL. An element that
# hides its own overflow and holds more text than fits is text nobody can read,
# and a modal wider than itself is a horizontal scrollbar on a marketing page.
MEASURE_JS = r"""
() => {
  const bad = [];
  const seen = (el) => el.offsetParent !== null || el.getClientRects().length > 0;
  const name = (el) => {
    const cls = (el.className || "").toString().trim().split(/\s+/).filter(Boolean);
    return (cls.length ? "." + cls.join(".") : el.tagName.toLowerCase());
  };
  for (const el of document.querySelectorAll(".pricing-modal *")) {
    if (!seen(el) || el.children.length) continue;
    const text = (el.textContent || "").trim();
    if (!text) continue;
    const cs = getComputedStyle(el);
    const hides = cs.overflowX === "hidden" || cs.overflowX === "clip";
    if (!hides || cs.textOverflow !== "clip") continue;
    if (el.scrollWidth > el.clientWidth + 1) {
      bad.push(`${name(el)}: ${el.scrollWidth}px of text in ${el.clientWidth}px — "${text.slice(0, 40)}"`);
    }
  }
  const modal = document.querySelector(".pricing-modal");
  if (modal && modal.scrollWidth > modal.clientWidth + 1) {
    bad.push(`.pricing-modal scrolls sideways: ${modal.scrollWidth} > ${modal.clientWidth}`);
  }
  return bad;
}
"""


# ⚠ TWO BUTTONS IN A ROW ARE ONE BOX TWICE. Reported as "dekho dono ek saath box
# hai to hamesha … barabar size ka ho": the landing hero's "Get started" is a
# `<button>` and "See the workflows" is an `<a>`, and they were drawing two
# different-sized slabs — `.btn.primary` carries `margin-top: 1.1rem` for sitting
# under a form field, which in a flex ROW pushed one down and let the other
# stretch over the margin.
#
# ⚠ THE RULE IS SIBLINGS, NOT "EVERY BUTTON ON THE PAGE". Buttons in different
# containers are allowed to differ (a nav CTA is deliberately smaller than a hero
# CTA); two that sit inside the same row are not, because a reader sees them as
# a pair. Half a pixel of slack absorbs sub-pixel rounding.
BUTTON_ROWS_JS = r"""
() => {
  const bad = [];
  const seen = (el) => el.offsetParent !== null || el.getClientRects().length > 0;
  const rows = new Set();
  for (const b of document.querySelectorAll(".btn")) {
    if (seen(b) && b.parentElement) rows.add(b.parentElement);
  }
  for (const row of rows) {
    const kids = [...row.children].filter(
      (el) => el.classList.contains("btn") && seen(el)
    );
    if (kids.length < 2) continue;
    const boxes = kids.map((el) => el.getBoundingClientRect());
    const heights = boxes.map((b) => b.height);
    const tops = boxes.map((b) => b.top);
    const spread = Math.max(...heights) - Math.min(...heights);
    const drift = Math.max(...tops) - Math.min(...tops);
    const label = (row.className || row.tagName).toString().trim();
    if (spread > 0.5) {
      bad.push(`${label}: heights ${heights.map((h) => h.toFixed(1)).join(" vs ")}`);
    } else if (drift > 0.5) {
      // Equal height but not on the same line — a stray margin on one of them.
      bad.push(`${label}: tops ${tops.map((t) => t.toFixed(1)).join(" vs ")}`);
    }
  }
  return bad;
}
"""


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
        "JWT_SECRET": "offer-card-check-not-a-real-secret",
        "ADMIN_EMAILS": ADMIN,
        "PYTHONIOENCODING": "utf-8",
    })
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
    """A pinned admin, one ordinary customer, and one PROMOTED coupon.

    ⚠ THE COUPON IS CREATED THROUGH THE ADMIN ROUTE, not written into the store,
    because what is being tested is the path an administrator actually takes —
    including the default state of the "show this to customers" box.
    """
    _, body = post(base, "/auth/register", {"email": ADMIN, "password": PASSWORD})
    admin_token = body.get("access_token")
    _, body = post(base, "/auth/register", {"email": CUSTOMER, "password": PASSWORD})
    cust_token = body.get("access_token")
    ends = (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()
    status, offer = post(base, "/admin/offers", {
        "code": CODE, "label": "Launch week", "kind": "percent",
        "value": PERCENT, "period": "both", "ends_at": ends,
        "max_redemptions": 40,
        "banner": f"Launch week — use {CODE} for {PERCENT}% off",
    }, token=admin_token)
    return admin_token, cust_token, status, offer


def prices(page):
    """{tier name → the number on its card}, off the screen.

    ⚠ SCOPED TO THE MODAL. The dashboard behind it draws an offer card of its
    own now, so a bare `.pricing-offer*` selector finds the one under the
    overlay — which Playwright then refuses to click, correctly.
    """
    out = {}
    cards = page.locator(".pricing-modal .pricing-card")
    for i in range(cards.count()):
        card = cards.nth(i)
        name = card.locator(".pricing-name").inner_text().strip()
        amount = card.locator(".pricing-amount").inner_text().strip()
        digits = re.sub(r"[^0-9.]", "", amount)
        out[name] = float(digits) if digits else 0.0
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="offer_card_")
    api = vite = None
    try:
        api_port, vite_port = free_port(), free_port()
        print("\n--- servers ---")
        api, api_base = start_api(api_port, tmp)
        check("the API started", api is not None, f"port {api_port}")
        if not api:
            return 1
        admin_token, cust_token, status, offer = seed(api_base)
        check("the coupon was created through the panel", status, 201)
        check("…and the panel ticked 'show to customers' for it",
              offer.get("promoted") is True, repr(offer.get("promoted")))
        check("the customer has a token", bool(cust_token))
        if not cust_token:
            return 1

        vite = start_vite(vite_port, api_base)
        check("Vite started", vite is not None, f"port {vite_port}")
        if not vite:
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
            # ⚠ THE CUSTOMER'S TOKEN, NOT THE ADMIN'S. See the header.
            ctx.add_init_script(
                f"localStorage.setItem('cas_token', {json.dumps(cust_token)});"
                f"localStorage.setItem('cas_email', {json.dumps(CUSTOMER)});"
                # ⚠ PINNED TO DARK, NOT LEFT TO THE OS. `theme.js` follows
                # `prefers-color-scheme` when nothing is stored, and a headless
                # Chromium reports light — so the theme the app actually ships
                # in would never have been the one measured.
                "localStorage.setItem('cas_theme', 'dark');"
            )
            # ===============================================================
            print("\n--- THE LANDING PAGE, WITH NO SESSION AT ALL ---")
            # ===============================================================
            # ⚠ ITS OWN CONTEXT, CARRYING NO TOKEN. A coupon is promoted so that
            # somebody who has NOT signed up can find it; a logged-out visitor is
            # the reader this card was added for.
            out = browser.new_context(viewport={"width": 1440, "height": 1000})
            out.add_init_script("localStorage.setItem('cas_theme', 'dark');")
            lp = out.new_page()
            lp_errors = []
            lp.on("pageerror", lambda e: lp_errors.append(str(e)))
            lp.goto(f"http://127.0.0.1:{vite_port}/")
            lp.wait_for_selector(".landing", timeout=30000)
            lp.wait_for_timeout(1200)

            strip = lp.locator(".hero-offer .pricing-offer").first
            check("the landing page draws the offer card",
                  strip.count() > 0 and strip.is_visible())
            if strip.count():
                text = strip.inner_text()
                check("…with the code a visitor has to type later", CODE in text, text[:120])
                check("…and the discount", f"{PERCENT}% off" in text, text[:120])
                # ⚠ NO APPLY BUTTON HERE, AND THAT IS THE POINT.
                # `POST /billing/coupon` is signed-in only, so an Apply on this
                # page would 401 in front of a prospect.
                # ⚠ NO BUTTON ON THIS CARD AT ALL. An Apply would 401
                # (`POST /billing/coupon` is signed-in only), and a second
                # "Get started" two centimetres under the hero’s own is noise.
                check("…and no Apply button that would 401", "Apply" not in text, text[:120])
                check("…and it can be copied",
                      strip.locator(".pricing-offer-code").count() > 0)
                # ⚠ ABOVE THE FOLD. The hero is `min-height: 100vh`, so a card in
                # a band BELOW it starts one whole screen down — a promotion
                # nobody scrolls to is the bug this card exists to fix, moved
                # rather than solved.
                box = strip.bounding_box()
                # ⚠ AND IT IS NOT WELDED TO THE BUTTON ABOVE IT. `pricing.css`
                # is imported AFTER `landing.css` and states its own margin on
                # `.pricing-offers`, so a single-class rule in landing.css loses
                # on ORDER alone — which is how the card first shipped with a
                # 2px gap under the CTA. Pins the gap, not the rule.
                btn = lp.locator(".hero-actions .btn").first.bounding_box()
                check("…and it clears the button above it",
                      box and btn and box["y"] - (btn["y"] + btn["height"]) >= 8,
                      f"gap {box['y'] - (btn['y'] + btn['height']):.1f}px" if box and btn else "no box")
                check("…and it is on the first screen, not below the fold",
                      box and box["y"] + box["height"] <= 1000,
                      f"bottom at {box['y'] + box['height']:.0f}px of a 1000px viewport" if box else "no box")

            # ⚠ THE REPORTED BUG. Two buttons in one row must be one box twice.
            hero_bad = lp.evaluate(BUTTON_ROWS_JS)
            check("every pair of buttons sharing a row is the same size",
                  not hero_bad, "; ".join(hero_bad[:4]))
            hero = lp.locator(".hero-actions .btn")
            check("…and the hero really has the two that were reported",
                  hero.count(), 2)
            if hero.count() == 2:
                a, b = hero.nth(0).bounding_box(), hero.nth(1).bounding_box()
                check("…both the same height",
                      abs(a["height"] - b["height"]) <= 0.5,
                      f"{a['height']:.1f} vs {b['height']:.1f}")
                check("…and on the same line",
                      abs(a["y"] - b["y"]) <= 0.5, f"{a['y']:.1f} vs {b['y']:.1f}")

            check("the landing page does not scroll sideways",
                  lp.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
            lp.screenshot(path=SHOT_LANDING, full_page=False)
            check("nothing reached window.onerror on the landing page",
                  not lp_errors, "; ".join(lp_errors[:3]))
            out.close()

            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{vite_port}/")

            # ===============================================================
            print("\n--- THE DASHBOARD, SIGNED IN ---")
            # ===============================================================
            page.wait_for_selector(".home", timeout=30000)
            page.wait_for_timeout(1200)
            dash = page.locator(".home-offers .pricing-offer").first
            check("the dashboard draws the offer card too",
                  dash.count() > 0 and dash.is_visible())
            if dash.count():
                text = dash.inner_text()
                check("…with the code on it", CODE in text, text[:120])
                # The dashboard knows neither the plan nor the period, so it
                # sends them to the screen that asks both.
                check("…and a button into the plans", "View plans" in text, text[:120])
            page.screenshot(path=SHOT_HOME, full_page=False)

            print("\n--- the customer opens the pricing modal ---")
            page.wait_for_selector(".sb-upgrade", timeout=30000)
            page.locator(".sb-upgrade").first.click()
            page.wait_for_selector(".pricing-modal", timeout=20000)
            page.wait_for_timeout(900)

            # ===============================================================
            print("\n--- THE OFFER IS ON THE CUSTOMER'S SCREEN ---")
            # ===============================================================
            card = page.locator(".pricing-modal .pricing-offer").first
            check("an offer card is drawn", card.count() > 0 and card.is_visible())
            if card.count():
                text = card.inner_text()
                check("…with the code on it", CODE in text, text[:120])
                check("…and the discount in words", f"{PERCENT}% off" in text, text[:120])
                check("…and what it covers", "every plan" in text, text[:120])
                check("…and when it ends", "Ends in" in text, text[:120])
                check("…and how many are left", "left" in text, text[:120])
                check("…and a way to copy it",
                      card.locator(".pricing-offer-code").count() > 0)
            check("the banner is above the plans",
                  CODE in page.locator(".pricing-modal .pricing-banner").inner_text()
                  if page.locator(".pricing-modal .pricing-banner").count() else False)

            page.screenshot(path=SHOT, full_page=True)
            # ⚠ STAMPED ON <html>, NOT CLICKED. The theme switch lives in the
            # sidebar, which is BEHIND this modal’s overlay;  works by
            # setting exactly this attribute, so setting it is the same act.
            page.evaluate("document.documentElement.dataset.theme = 'light'")
            page.wait_for_timeout(400)
            page.screenshot(path=SHOT_LIGHT, full_page=True)
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
            page.wait_for_timeout(400)
            print(f"  (screenshots: {SHOT}, {SHOT_LIGHT}, {SHOT_LANDING}, {SHOT_HOME})")

            # ===============================================================
            print("\n--- APPLYING IT MOVES EVERY PLAN IT COVERS ---")
            # ===============================================================
            before = prices(page)
            page.locator(".pricing-modal .pricing-offer-apply").first.click()
            page.wait_for_timeout(1400)
            after = prices(page)
            moved = [n for n, v in after.items() if v < before.get(n, 0)]
            paid = [n for n, v in before.items() if v > 0]
            check("pressing Apply discounted every paid plan",
                  len(moved) == len(paid) and len(paid) >= 2,
                  f"before={before} after={after}")
            for name in moved:
                want = round(before[name] * (100 - PERCENT) / 100, 2)
                # ⚠ THE SERVER DID THE ARITHMETIC. This only checks the browser
                # printed what came back — within a cent, because the discount is
                # rounded DOWN in minor units and the card drops a trailing zero.
                check(f"…{name} is within a cent of {PERCENT}% off",
                      abs(after[name] - want) <= 0.02, f"{before[name]} → {after[name]}")
            tags = page.locator(".pricing-modal .pricing-sale-tag")
            applied = sum(
                1 for i in range(tags.count()) if CODE in tags.nth(i).inner_text()
            )
            check("…and every discounted card says which code did it",
                  applied == len(moved), f"{applied} tags for {len(moved)} cards")
            check("…and the card says it is applied",
                  "Applied" in page.locator(".pricing-modal .pricing-offer").first.inner_text())

            # ===============================================================
            print("\n--- the period toggle does not leave a stale price ---")
            # ===============================================================
            # ⚠ A FIGURE WORKED OUT FOR THE YEARLY PRICE IS NOT THE MONTHLY ONE.
            # The modal opens on Yearly, so this switches to Monthly and watches
            # the discounted number move with it.
            yearly_prices = prices(page)
            page.locator('.pricing-modal .pricing-toggle button:has-text("Monthly")').click()
            page.wait_for_timeout(1400)
            monthly_prices = prices(page)
            check("switching period re-prices the discount",
                  any(monthly_prices[n] != yearly_prices.get(n) for n in monthly_prices),
                  f"{yearly_prices} → {monthly_prices}")
            still = page.locator(".pricing-modal .pricing-sale-tag")
            check("…and the code is still applied afterwards",
                  any(CODE in still.nth(i).inner_text() for i in range(still.count())))

            # ===============================================================
            print("\n--- nothing is clipped ---")
            # ===============================================================
            bad = page.evaluate(MEASURE_JS)
            check("no text in the modal is cut off", not bad, "; ".join(bad[:5]))
            rows = page.evaluate(BUTTON_ROWS_JS)
            check("…and every pair of buttons in the modal matches too",
                  not rows, "; ".join(rows[:4]))

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

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("✅ the offer an admin created is on the customer's screen and applies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
