"""DOES THE WORKFLOW OPEN AT ALL, OR IS IT A WHITE PAGE?

Why this exists, in one sentence: **`npm run build` passed with the crash in
it.**

The fault it was written for. `concept` was added to the script-autosave
effect's dependency array, at line 294 — and `const [concept, setConcept] =
useState(null)` sat at line 647. A dependency array is evaluated DURING RENDER,
and `const` is not hoisted, so the very first render threw

    ReferenceError: Cannot access 'concept' before initialization

React unmounted the tree and Script → Storyboard rendered as **a blank white
page**. ⚠ **esbuild never evaluates the module**, so the build was clean, every
Python check still passed (they read the file as TEXT), and the only thing that
knew was a browser.

⚠ SO THE ASSERTIONS HERE ARE ABOUT SURVIVAL FIRST. Does the component mount;
did anything reach `pageerror`; is there anything on the screen. Same shape as
`tests/monitor_effects_check.py`, which exists for the same reason — a black
monitor was a CRASH before it was a rendering bug.

Then, because the rig is standing anyway, it walks the path that crash was
hiding, with a draft served off Playwright's own router: the saved card reopens,
its scenes reorder, ← and the form's own link get you out and back — **and a
REMOUNT does not reopen it**, which is the storyboard-draft bug this feature was
one line away from repeating. Nothing without a browser can see any of it.

⚠ THIS TEST HAS ALREADY EARNED ITS KEEP TWICE: once on the white page, and once
on the offer link, which sat in the form's status row where **the only route to
the form clears the concept on the way past**. It could never have fired, and it
read perfectly well in the diff.

⚠ MOUNTED INSIDE `<React.StrictMode>`, like `main.jsx` does. StrictMode mounts
twice, and a double mount is exactly what caught the storyboard-draft resume bug
— a probe that renders once is a probe that would have missed it.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/workflow_mount_check.py

No backend needed — Vite is started here and every API call is answered by the
router below.

⚠ THE PROBE PAGE IS WRITTEN INTO `client/` AND DELETED AGAIN, for the reason
`monitor_effects_check.py` gives: Vite serves its own root and nothing above it,
so a harness in a temp directory would be outside `server.fs.allow` and refused.
Both files carry a `__probe` prefix and are removed in a `finally`.
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

PROBE_HTML = os.path.join(CLIENT, "__probe_workflow.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_workflow.jsx")

# What `api.js` falls back to when VITE_API_BASE is unset. Everything under it
# is answered here, so the probe never waits on a server nobody started.
API_BASE = "http://127.0.0.1:8000"

# The draft the router hands back — a real concept, in the user's own Hinglish,
# so the restore is tested against the shape it actually meets.
DRAFT = {
    "text": "Ek parivaar ki Ganesh Chaturthi, 40 second.",
    "title": "Ganesh Utsav",
    "concept": {
        "title": "Ganesh Utsav: Ek Rishta",
        "premise": "Ek parivaar Ganesh Chaturthi manata hai.",
        "story_direction": "Ghar ki taiyari -> Bappa ka aagman -> Bhaavuk visarjan -> Shanti ka ehsaas.",
        "key_scenes": [
            "Murti ghar ke andar aa rahi hai.",
            "Parivaar murti ko visarjit kar raha hai.",
            "Baccha aasmaan ki taraf dekh raha hai.",
        ],
        "duration_seconds": 40,
        "visual_direction": "Garm, ghanishth, jeevant.",
    },
    "updated_at": "2026-08-28T10:00:00+00:00",
}

# A storyboard draft with two shots, resumed to reach the REVIEW step. ⚠ Shot 1
# already names a prop and shot 2 names none — the second is the case the whole
# props chain died on, because the breakdown returned an empty asset list and
# nothing downstream could add one.
BOARD_DRAFT = {
    "job_id": "probe-board-1",
    "title": "Ganesh Utsav",
    "script": "SCENE 1. INT. PUJA ROOM - EVENING\nThe space is empty, waiting.",
    "style": "cinematic",
    "aspect_ratio": "9:16",
    "world": {"region": "Modern India, during Ganesh Chaturthi festival"},
    "characters": [{"name": "ANANYA", "description": "A slender girl, 7."}],
    "assets": [],
    "shots": [
        {
            "scene_number": 1, "shot_number": 1,
            "description": "A wide shot of the decorated puja room, empty and waiting.",
            "characters": [], "assets": ["Ganesh idol"],
            "camera": "wide establishing", "location": "Puja Room",
            "movement": "static", "duration_seconds": 3, "dialogue": [],
        },
        {
            "scene_number": 2, "shot_number": 1,
            "description": "Father carries the decorated Ganesh idol into the room.",
            "characters": ["ANANYA"], "assets": [],
            "camera": "medium shot", "location": "Puja Room",
            "movement": "static", "duration_seconds": 3, "dialogue": [],
        },
    ],
    "updated_at": "2026-08-28T11:00:00+00:00",
}

# A FINISHED board, as the library grid lists it, plus the project behind it.
# ⚠ The summary is deliberately lean — it carries no shots, which is exactly
# why re-opening one had nothing to put on the review step.
BOARD_SUMMARY = {
    "job_id": "probe-board-done",
    "title": "Ganesh Utsav: Ek Rishta",
    "status": "completed",
    "style": "cinematic",
    "aspect_ratio": "9:16",
    "genre": "Mythology",
    "panel_count": 2,
    "cover_index": None,
    "cover_url": None,
    "shared": False,
}
BOARD_PROJECT = {
    "job_id": "probe-board-done",
    "title": "Ganesh Utsav: Ek Rishta",
    "style": "cinematic",
    "aspect_ratio": "9:16",
    "genre": "Mythology",
    "shots": BOARD_DRAFT["shots"],
    "characters": [{"name": "ANANYA", "description": "A slender girl, about 7."}],
    "assets": [{"name": "Ganesh idol", "category": "prop", "description": "A decorated idol."}],
    "world": {"region": "Modern India, during Ganesh Chaturthi festival"},
    "script": BOARD_DRAFT["script"],
    # ⚠ A reference the user has already paid for. Old boards store only the
    # resolved PATH; the server recovers the id from it (`_ref_ids_from_paths`).
    "character_refs": {"ANANYA": "ref-ananya-1"},
    "asset_refs": {},
}

# A 1×1 PNG, so the preview fetch that follows the restore has something to
# resolve. Its content does not matter — the assertion is on the BUTTON.
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
import ScriptToStoryboard from "./src/components/ScriptToStoryboard.jsx";
import { applyTheme, getTheme } from "./src/theme.js";
import "./src/styles/index.css";

// ⚠ Anything that escapes a render lands here as well as on `pageerror`, so a
// failure that Playwright somehow misses is still readable off the page.
window.__errors = [];
window.addEventListener("error", (e) => window.__errors.push(String(e.message)));

applyTheme(getTheme());

// The token only has to EXIST — every request is answered by the test's router.
localStorage.setItem("cas_token", "probe-token");

// ⚠ `window.__remount()` changes the KEY, which is a real unmount and remount
// of the component inside the SAME page load — precisely what switching
// workflows does in `App.jsx`. It is how the "does not reopen twice" promise
// is tested, and the only way to tell a page load from a mount from out here.
const root = ReactDOM.createRoot(document.getElementById("root"));
let generation = 0;
function draw() {
  root.render(
    <React.StrictMode>
      <ScriptToStoryboard key={generation} onOpenAnimatic={() => {}} />
    </React.StrictMode>
  );
}
window.__remount = () => {
  generation += 1;
  draw();
};
draw();
"""

PROBE_HTML_SOURCE = """<!doctype html>
<meta charset="utf-8">
<title>workflow mount probe</title>
<div id="root"></div>
<script type="module" src="/__probe_workflow.jsx"></script>
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def route_api(route, request):
    """Answer everything the component asks the server for.

    ⚠ A 200 with an EMPTY body where the shape matters, never a 500 — this test
    is about whether the screen survives, and a component that only crashes when
    the API is angry is a different test.
    """
    url = request.url
    if "/scripts/draft" in url:
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(DRAFT))
        else:  # PUT / DELETE — the autosave, which must not break anything
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(DRAFT))
        return
    # ⚠ PLURAL FIRST — "/storyboards/drafts" contains "/storyboards/draft",
    # and answering the list with the singular's 404 leaves the library empty
    # and the review step unreachable.
    if "/storyboards/drafts" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps([BOARD_DRAFT]))
        return
    if "/storyboards/draft" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(BOARD_DRAFT))
        return
    if "/_references/" in url or "/reference" in url:
        route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
        return
    if url.rstrip("/").endswith("/project"):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(BOARD_PROJECT))
        return
    # The library grid. ⚠ Checked AFTER every "/storyboards/…" path above, or it
    # would answer the draft and project endpoints with a list.
    if "/storyboards" in url and request.method == "GET":
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps([BOARD_SUMMARY]))
        return
    if url.rstrip("/").endswith("/auth/me"):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"email": "probe@example.com", "name": "Probe", "company": "",
             "is_admin": False}))
        return
    # Lists, entitlements, usage, offers — an empty list or object is a valid
    # answer to every one of them.
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
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route(f"{API_BASE}/**", route_api)

            page.goto(f"http://127.0.0.1:{port}/__probe_workflow.html",
                      wait_until="load")
            page.wait_for_timeout(2500)

            print("\n[1] it mounts at all")
            # ⚠ THE ASSERTION THE WHITE PAGE WOULD HAVE FAILED. A TDZ read in a
            # dependency array throws on the FIRST render, so there is no tree
            # and no text — the build says nothing about either.
            check("⚠ nothing reached `pageerror` — a ReferenceError during "
                  "render unmounts the whole tree and leaves a white page, and "
                  "`npm run build` cannot see it",
                  not errors, "; ".join(errors[:2]))
            html_len = page.eval_on_selector("#root", "el => el.innerHTML.length")
            check("…and something was actually drawn", html_len > 500, f"{html_len} chars")
            check("…including under StrictMode's second mount",
                  not page.evaluate("window.__errors.length"),
                  str(page.evaluate("window.__errors")))

            print("\n[2] the card the page died on is back on screen")
            # ⚠ IT REOPENS, IT IS NOT OFFERED. The first attempt put a link in
            # the form's status row and this test is what proved that could
            # never fire: the only route to the form from a cold start is "New
            # storyboard", and that calls `resetWorkflow()`, which clears the
            # concept on the way past. The offer was unreachable by design.
            body = page.inner_text("body")
            check("⚠ a fresh page load reopens the concept the user was "
                  "editing — a refresh used to lose it, and re-generating from "
                  "the same brief returns a DIFFERENT film",
                  "Is this the right direction?" in body)
            for line in DRAFT["concept"]["key_scenes"]:
                check(f"…scene restored: {line[:38]}…",
                      page.locator(f'input[value="{line}"]').count() > 0)
            check("…and the Hinglish arc survived the round trip",
                  "Bhaavuk visarjan" in page.inner_html("body"))
            check("…with no error on the way", not errors, "; ".join(errors[:2]))

            print("\n[3] the scenes can be put in order")
            ups = page.locator('button[title="Move up"]')
            check("every scene row carries a Move up", ups.count() == 3, str(ups.count()))
            check("⚠ …and the first one is disabled, so scene 1 cannot wrap to "
                  "the bottom", ups.first.is_disabled())
            first_before = page.locator(".sts-concept-scenes input").first.input_value()
            ups.nth(2).click()
            page.wait_for_timeout(400)
            slots = page.locator(".sts-concept-scenes input")
            check("moving the last scene up actually reorders the list",
                  slots.nth(1).input_value() == DRAFT["concept"]["key_scenes"][2],
                  f"slot 2 is {slots.nth(1).input_value()!r}")
            check("…and it swapped with its neighbour rather than shuffling",
                  slots.first.input_value() == first_before)

            print("\n[4] ← and back again, without generating anything")
            page.click(".wf-back")
            page.wait_for_timeout(500)
            check("← leaves the card for the form",
                  page.locator("textarea").first.is_visible())
            offer = page.get_by_role("button", name="↩ Resume your concept")
            check("…and the form offers the way back in, so a card you stepped "
                  "out of does not need generating again",
                  offer.is_visible())
            offer.click()
            page.wait_for_timeout(500)
            check("…which reopens it, edits and all",
                  page.locator(".sts-concept-scenes input").nth(1).input_value()
                  == DRAFT["concept"]["key_scenes"][2])

            print("\n[5] ⚠ but it does NOT reopen on a REMOUNT")
            # The storyboard-draft bug, which cost several sessions: leaving the
            # workflow and coming back re-opened a board the user had walked out
            # of, because the resume ran on every mount. The latch here is module
            # scope — one page load, not one mount — and this is the difference.
            page.evaluate("window.__remount()")
            page.wait_for_timeout(2500)
            after = page.inner_text("body")
            check("⚠ switching away and back does NOT drag the user into the "
                  "card again — the latch is per PAGE LOAD, not per mount, and "
                  "a ref-based one would be spent by StrictMode's second mount",
                  "Is this the right direction?" not in after)
            check("…and the remount itself was clean",
                  not errors, "; ".join(errors[:2]))

            print("\n[6] a shot can name the props that must not drift")
            # ⚠ THE FAULT THIS SECTION EXISTS FOR. On the first finished board
            # the Ganesh idol — the subject of the film, in nine of fifteen
            # panels — was drawn differently every time. Every CHARACTER was
            # consistent, because each had a reference. The idol had none: the
            # breakdown returned an empty asset list, the props step only opens
            # when that list is non-empty, and no screen could add to it.
            page.evaluate("window.__remount()")
            page.wait_for_timeout(2000)
            # ⚠ `.lib-resume`, not a role lookup: the button reads "Resume →",
            # so its accessible name carries the arrow and `name="Resume"` finds
            # nothing. And not a comma-selector either — `.lib-new` sits earlier
            # in the DOM, so "first match" opened a NEW storyboard instead.
            page.wait_for_selector(".lib-resume", timeout=15000)
            page.click(".lib-resume")
            page.wait_for_timeout(1500)
            check("the review step is reachable from a saved draft",
                  "Review your shots" in page.inner_text("body"))

            props = page.locator(".shot-assets-row input")
            check("⚠ EVERY shot card carries a props field. Without it the "
                  "chain that keeps a prop identical — shot names it → props "
                  "step opens → reference drawn → panel holds it — has no "
                  "first link, and the props step never opens at all",
                  props.count() == 2, str(props.count()))
            check("…and a prop the breakdown DID find is shown in it",
                  props.first.input_value() == "Ganesh idol",
                  repr(props.first.input_value()))

            # ⚠ TWO WAYS TO GET THIS WRONG, AND BOTH WERE SHIPPED BRIEFLY.
            # Filtering the empty piece eats the comma as it is typed; trimming
            # each piece eats the SPACE inside a name, so "Ganesh idol" could
            # only be typed as "Ganeshidol". Both are caught below.
            props.nth(1).click()
            props.nth(1).type("Ganesh idol, ")
            page.wait_for_timeout(300)
            check("⚠ a comma can actually be TYPED — splitting and filtering "
                  "would swallow it and cap the field at one name for ever",
                  props.nth(1).input_value() == "Ganesh idol, ",
                  repr(props.nth(1).input_value()))
            props.nth(1).type("puja room")
            page.wait_for_timeout(300)
            check("…and a second name lands beside the first",
                  props.nth(1).input_value() == "Ganesh idol, puja room",
                  repr(props.nth(1).input_value()))
            check("…with nothing thrown while typing", not errors,
                  "; ".join(errors[:2]))

            print("\n[7] a saved board re-opens WITH the work behind it")
            # ⚠ THE FAULT: the library handed the board step the display
            # settings and the job id, and nothing else — so the review, cast
            # and props steps had no content, and ← Back was wired straight to
            # the library because there was nowhere else with any. Reported as
            # *"recent se khola to direct last page pe chala jata hun, beech ka
            # page nahi aa raha hai"*. The panels were reachable; everything
            # they were made from was not.
            page.evaluate("window.__remount()")
            page.wait_for_timeout(2000)
            page.wait_for_selector(".lib-title", timeout=15000)
            page.get_by_text("Ganesh Utsav: Ek Rishta").first.click()
            page.wait_for_timeout(2000)
            check("the board opens from the library", "≈" in page.inner_text("body")
                  or "panels" in page.inner_text("body"))
            check("…with no error on the way", not errors, "; ".join(errors[:2]))

            page.click(".wf-back")
            page.wait_for_timeout(1200)
            body7 = page.inner_text("body")
            check("⚠ ← now lands on the REVIEW step, not back out at the "
                  "library — the steps between the form and the panels exist "
                  "again",
                  "Review your shots" in body7)
            check("…and the shots behind the board came with it",
                  page.locator(".shot-assets-row input").count() == 2,
                  str(page.locator(".shot-assets-row input").count()))
            check("⚠ …and the board still reads as UP TO DATE, so there is a "
                  "free way back to panels that already exist. Without the "
                  "signature the only route back is drawing all of them again",
                  "Back to your storyboard" in body7)
            check("…and cast and props are reachable from here (E7)",
                  "Cast & props" in body7 or "Next: cast" in body7)

            # ⚠ THE PICTURES ALREADY PAID FOR HAVE TO COME BACK WITH IT.
            # Re-opening used to land on a cast page of empty cards whose only
            # offered action was "Generate panels (skip refs)" — every reference
            # the user had bought, invisible, and buying them again the only
            # visible way forward. `readyCount` counts cards that HAVE a
            # reference id, so the button's own words are the assertion.
            page.click("text=Cast & props")
            page.wait_for_timeout(1500)
            cast_body = page.inner_text("body")
            check("the cast step opens from the review step",
                  "Set up your cast" in cast_body)
            check("⚠ …showing the reference ALREADY PAID FOR, not an empty card "
                  "offering to sell it again",
                  "(skip refs)" not in cast_body and "ref" in cast_body,
                  cast_body[cast_body.find("Generate panels"):][:40]
                  if "Generate panels" in cast_body else "no generate button")
            check("nothing threw on the way back", not errors, "; ".join(errors[:2]))

            check("nothing threw during any of it", not errors, "; ".join(errors[:2]))
            page.screenshot(path=os.path.join(ROOT, "output", "workflow_mount.png"))
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
    print("Script → Storyboard opens, remembers the card it was handed, lets the "
          "scenes be put in order, and lets a shot name the props that must not "
          "drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
