"""THE PRICING CARDS ARE EDITABLE, AND WHAT IS TYPED REACHES THE DATABASE.

Why this exists, in one sentence: the two columns on an admin pricing card used
to be read-only text, an administrator asked to be able to fix the wording from
that screen, and "the box appeared and I typed in it" is not the same claim as
"the tier changed".

    tests/admin_check.py         pins the ROUTES, the roles and the event log
    tests/admin_fields_check.py  opens the panel and MEASURES every field's box
    THIS FILE                    types into the pricing cards and reads the
                                 result back off the API

⚠ EVERY ASSERTION IS A ROUND TRIP, NOT A SCREENSHOT. The browser does the typing
and the clicking; the check that follows re-reads `/admin/tiers` (or
`/admin/features`) with the admin's own token and asks whether the stored
document actually says what the screen said. A test that only looked at the DOM
would pass with the PATCH removed.

What it covers, in order:

  1. a marketing line's TEXT, saved when the field is left
  2. its TICK, which is what makes the card print a line as not included
  3. its BOLD flag
  4. ADDING a line, and that an added line nobody typed into is NOT saved
  5. REMOVING a line
  6. renaming a line in "Actually unlocks" — ⚠ WHICH RENAMES THE FEATURE, so
     the check is that the OTHER cards showing that feature change too, and that
     `/admin/features` agrees
  7. the footer's alignment, because the reason the columns were touched at all
     was a screenshot of the badge box sitting 3.6px above the words beside it

⚠ THE ALIGNMENT ASSERTION IS ARITHMETIC ON `getBoundingClientRect`, not an image
diff: the centre of the badge field against the centre of the checkbox label's
own text, and the tops of the three price boxes against each other. That is what
"they line up" means, and it cannot pass by looking right at one font size.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/pricing_edit_check.py

⚠ NO MONGODB, NO AI KEY, NO SERVER OF YOUR OWN — the whole rig comes from
`admin_fields_check.py`: temporary stores, a real uvicorn, a real Vite, and an
admin pinned through `ADMIN_EMAILS`. Both servers are killed in a `finally`.
"""

import json
import os
import shutil
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.admin_fields_check import (  # noqa: E402
    ADMIN,
    free_port,
    seed,
    start_api,
    start_vite,
    tab,
)

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


def get(base, path, token):
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


def tier(base, token, tier_id):
    for t in get(base, "/admin/tiers", token)["tiers"]:
        if t["id"] == tier_id:
            return t
    raise AssertionError(f"no tier {tier_id}")


def texts(t):
    return [b.get("text") for b in t.get("bullets") or []]


# ---------------------------------------------------------------------------
# The card under test
# ---------------------------------------------------------------------------
# `starter` rather than `trial`: it is not the default tier, so nothing in this
# file can trip the "the default tier can't be archived" guard by accident.
TIER = "starter"


def card(page):
    """The Starter card. Located by its `<code>` key, which is the id itself."""
    return page.locator(".admin-tier").filter(has=page.locator("code", has_text=TIER)).first


# Alignment, measured in the page. Returns the numbers, not a verdict — the
# assertions live in Python where a failure can print what it saw.
#
# ⚠ IT ALSO REPORTS WHETHER THE ROWS WRAPPED, and that is not a detail. Both rows
# are `flex-wrap: wrap`: on a narrow card the badge field drops onto a second
# line BELOW the checkboxes and the third price box onto a second line below the
# other two, and a 40px gap between them is then correct rather than the bug.
# Asserting blind would have failed on a 320px card and said nothing about the
# ~590px one the screenshot came from.
GEOMETRY_JS = r"""
(sel) => {
  const cardEl = [...document.querySelectorAll('.admin-tier')]
    .find(c => (c.querySelector('code')?.textContent || '').trim() === sel);
  const mid = (r) => (r.top + r.bottom) / 2;
  const badgeR = cardEl.querySelector('.admin-badge-input').getBoundingClientRect();
  // The LABEL's TEXT, not the label's box: a box centred on the box beside it is
  // exactly what the bug looked like — what has to agree is where the words sit.
  const lab = cardEl.querySelector('.admin-check');
  const rng = document.createRange();
  rng.selectNodeContents(lab.childNodes[lab.childNodes.length - 1]);
  const wordsR = rng.getBoundingClientRect();
  const priceR = [...cardEl.querySelectorAll('.admin-price-input')]
    .map(e => e.getBoundingClientRect());
  return {
    badgeVsWords: +(mid(badgeR) - mid(wordsR)).toFixed(2),
    footWrapped: Math.abs(badgeR.top - lab.getBoundingClientRect().top) > 20,
    priceTops: priceR.map(r => +r.top.toFixed(1)),
    priceWrapped: Math.max(...priceR.map(r => r.top)) - Math.min(...priceR.map(r => r.top)) > 20,
    cardWidth: +cardEl.getBoundingClientRect().width.toFixed(0),
  };
}
"""


def main():
    tmp = tempfile.mkdtemp(prefix="pricing_edit_")
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
            tab(page, "Pricing")
            page.wait_for_selector(".admin-tier", timeout=15000)

            starter = card(page)
            rows = starter.locator(".admin-tier-bullets li")
            before = tier(api_base, token, TIER)
            n_before = len(before.get("bullets") or [])
            check("the card starts with the seeded lines", n_before > 1, str(texts(before)))

            # --- 1. the text -------------------------------------------------
            NEW = "5 projects every month"
            box = rows.first.locator(".admin-bullet-text")
            box.fill(NEW)
            box.blur()
            page.wait_for_timeout(700)
            after = tier(api_base, token, TIER)
            check("typing a line and clicking away saves it",
                  texts(after)[0] == NEW, str(texts(after)[:2]))

            # --- 2. the tick -------------------------------------------------
            rows.first.locator(".admin-bullet-ic").click()
            page.wait_for_timeout(700)
            after = tier(api_base, token, TIER)
            check("clicking the tick stores the line as NOT included",
                  after["bullets"][0].get("ok") is False, json.dumps(after["bullets"][0]))
            rows.first.locator(".admin-bullet-ic").click()
            page.wait_for_timeout(700)
            after = tier(api_base, token, TIER)
            check("clicking it again puts the tick back",
                  after["bullets"][0].get("ok") is True, json.dumps(after["bullets"][0]))

            # --- 3. bold -----------------------------------------------------
            # ⚠ HOVER FIRST. The two tools are `opacity: 0` until the row is
            # pointed at, which is the whole reason the copy has the width it
            # needs — so a click without a hover is a click on nothing.
            rows.first.hover()
            was_strong = bool((after["bullets"][0] or {}).get("strong"))
            rows.first.locator(".admin-bullet-btn", has_text="B").click()
            page.wait_for_timeout(700)
            after = tier(api_base, token, TIER)
            check("the B button stores the line's bold flag",
                  bool(after["bullets"][0].get("strong")) is (not was_strong),
                  json.dumps(after["bullets"][0]))

            # --- 4. adding ---------------------------------------------------
            starter.locator(".admin-bullet-add .btn").click()
            page.wait_for_timeout(300)
            check("Add a line draws a new row",
                  rows.count() == n_before + 1, f"{rows.count()} rows")
            after = tier(api_base, token, TIER)
            check("an empty new row is NOT saved",
                  len(after.get("bullets") or []) == n_before,
                  f"{len(after.get('bullets') or [])} stored")
            ADDED = "Priority rendering queue"
            last = rows.nth(rows.count() - 1).locator(".admin-bullet-text")
            last.fill(ADDED)
            last.blur()
            page.wait_for_timeout(700)
            after = tier(api_base, token, TIER)
            check("typing into the new row saves it",
                  texts(after)[-1] == ADDED, str(texts(after)))

            # --- 5. removing -------------------------------------------------
            target = rows.nth(1)
            gone = target.locator(".admin-bullet-text").input_value()
            target.hover()
            target.locator(".admin-bullet-btn").nth(1).click()
            page.wait_for_timeout(700)
            after = tier(api_base, token, TIER)
            check("the ✕ removes that line and only that line",
                  gone not in texts(after) and len(texts(after)) == n_before,
                  f"removed {gone!r}, left {texts(after)}")

            # --- 6. renaming what a tier unlocks -----------------------------
            # ⚠ THIS IS THE FEATURE'S OWN NAME. So the assertion is deliberately
            # about the OTHER cards: if this only patched the card it was typed
            # on, the same line on Pro would still read the old name.
            unlock = starter.locator(".admin-unlock-name").first
            old_name = unlock.input_value()
            RENAMED = "Veo renders (house account)"
            unlock.fill(RENAMED)
            unlock.blur()
            page.wait_for_timeout(900)

            feats = get(api_base, "/admin/features", token)["features"]
            renamed = [f for f in feats if f.get("label") == RENAMED]
            check("renaming a line renames the feature itself",
                  len(renamed) == 1, f"{old_name!r} → {RENAMED!r}")

            # ⚠ COUNTED BY `.value`, NOT BY TEXT. These are textareas, and a
            # textarea's text content is what it was RENDERED with, not what it
            # holds now — a `has_text` filter here would pass on stale markup.
            def on_screen(name):
                return page.evaluate(
                    "(name) => [...document.querySelectorAll('.admin-unlock-name')]"
                    ".filter(e => e.value === name).length",
                    name,
                )

            key = renamed[0]["key"] if renamed else ""
            listing_it = len([
                t for t in get(api_base, "/admin/tiers", token)["tiers"]
                if any(i["key"] == key for i in t["includes"])
            ])
            check("every card that lists it shows the new name at once",
                  listing_it > 1 and on_screen(RENAMED) == listing_it,
                  f"{on_screen(RENAMED)} boxes on screen, {listing_it} cards list it")
            check("the old name is nowhere on the screen any more",
                  on_screen(old_name) == 0, f"{on_screen(old_name)} boxes still say {old_name!r}")

            # Emptying it is refused in the browser — a feature with no name is
            # unreadable everywhere, and the server would fall back to the key.
            unlock.fill("")
            unlock.blur()
            page.wait_for_timeout(600)
            check("an empty name is refused and the old one comes back",
                  unlock.input_value() == RENAMED, unlock.input_value())
            still = [f for f in get(api_base, "/admin/features", token)["features"]
                     if f["key"] == key]
            check("and nothing blank was written to the feature",
                  bool(still) and still[0].get("label") == RENAMED,
                  json.dumps(still[0].get("label") if still else None))

            # --- 7. the footer and the price row -----------------------------
            # ⚠ 2400 WIDE, BECAUSE THAT IS THE SCREEN IT WAS REPORTED ON — four
            # cards across a monitor is ~590px each, wide enough that neither row
            # wraps and "they don't line up" means something. The 1440 pass after
            # it is the narrow layout, where wrapping is the right answer and the
            # numbers are printed rather than asserted.
            for width in (2400, 1440):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(400)
                geo = page.evaluate(GEOMETRY_JS, TIER)
                where = f"at {width}px (card {geo['cardWidth']}px)"
                if geo["footWrapped"]:
                    print(f"  --   the badge drops below the checkboxes {where} — not compared")
                else:
                    check(f"the badge box and the words beside it share a centre line {where}",
                          abs(geo["badgeVsWords"]) <= 1.0,
                          f"badge centre is {geo['badgeVsWords']}px off the label's text")
                if geo["priceWrapped"]:
                    print(f"  --   the price row wraps {where} — not compared: {geo['priceTops']}")
                else:
                    check(f"the three price boxes sit on one line {where}",
                          len(set(geo["priceTops"])) == 1, str(geo["priceTops"]))

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
    print("✅ the pricing cards are editable and what is typed is stored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
