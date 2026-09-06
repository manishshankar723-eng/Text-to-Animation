"""AN IMPORTED STORYBOARD ARRIVES WITH ITS PICTURES — in the real editor.

The report:

    "see when i open new animatics and clicl add layer then i click Storyboard
     image then select my storyborad project and import so take see image panel
     not show and in media in not upload properly"

Forty-two panels came in, the row was there, the clips were on the timeline —
and every thumbnail was a spinner and the Program monitor was black. The import
reported success and produced nothing you could see.

WHY, because it is not the obvious thing. A board panel is not uploaded; it is
REFERENCED, and its picture is served from `/animatics/{id}/frame/{frameId}` — a
route that resolves by looking the frame up in the SAVED project. So the frames
have to be on the server BEFORE their urls are handed out, and `doBoardImport`
knew that: it placed the frames, `await flush()`ed, then patched the urls in.

The flush wrote nothing. `flush` reads the document and the dirty flag out of
refs that EFFECTS fill, so one microtask after `setFrames` — which is where that
await lands — React has not re-rendered: it saw a clean project and returned at
its first line. Every url then 404'd against a server that had never heard of
those frames, and the fetch effect caches nothing on failure and does not retry.
One miss per panel, permanent.

---------------------------------------------------------------------------
⚠ WHAT THIS CHECKS IS THE ORDER OF TWO REQUESTS, AND THEN THE PIXELS
---------------------------------------------------------------------------
The fake server here enforces the REAL server's rule: `GET /frame/{id}` is a 404
until a `PUT` has carried that id. That is what makes this a regression test
rather than a screenshot — with the old code the picture requests go out first
and the router answers them exactly as production did. Then the pixels are
checked as well, because "the requests were in the right order" is not the claim
the user made; "I can see my panels" is.

    python tests/editor_board_import_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router — the harness is `editor_picture_tracks_check.py`'s, borrowed
rather than invented a third time.
"""

import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image
from playwright.sync_api import sync_playwright

# Screenshots go to `test_shots/`, which git ignores — never the repo
# root. See `tests/_shots.py`.
from _shots import shot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_bimport.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_bimport.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture — AN EMPTY ANIMATIC, because that is what the report opens with
# ---------------------------------------------------------------------------
# "when i open new animatics" is not incidental: a fresh project is CLEAN, and a
# clean project is the one case where the broken flush returned at its very first
# line. On a document that already had an unsaved edit in it the flush had
# something to write and the frames went up by accident. So the bug showed on
# exactly the path a new user takes and on no other.
BOARD_ID = "board1"
BOARD_NAME = "TTBB EP One"
# ⚠ A BOARD MADE IN ANOTHER WORKFLOW — a copy refined in 🖼 Image to Animatic
# Image, which carries `params.workflow` and is therefore invisible to a caller
# that asks `GET /storyboards` with no tag. Animating one is the whole point of
# making it, so this editor's picker must list it.
COPIED_ID = "board2"
COPIED_NAME = "Ganesh Utsav (refined)"
PANELS = 3
# ⚠ ENOUGH BOARDS TO OVERFLOW THE WINDOW. The account that reported the bug had
# 22; the picker must stay usable at that size, which is what section "the
# footer" below presses.
FILLER = [
    {"job_id": f"filler{i}", "character_name": f"Board number {i}", "panel_count": 12}
    for i in range(20)
]

PROJECT = {
    "id": "probe",
    "title": "Untitled animatic",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [],
    "texts": [], "shapes": [], "layers": [], "overlays": [],
    "transitions": [], "audio_tracks": [], "veo_clips": [], "video": None,
}

# What the import route hands back: `AnimaticFrame`s that REFERENCE the board.
# No upload id anywhere — that is the whole reason these cannot be served until
# they are saved, and why the upload path (`/media/{upload_id}`, servable
# immediately) never had this bug.
IMPORTED = [
    {"id": f"bf{i + 1}", "kind": "image",
     "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": i},
     "duration_ms": 2000, "start_ms": None, "track": 0, "label": f"Shot {i + 1}"}
    for i in range(PANELS)
]
IMPORTED_IDS = [f["id"] for f in IMPORTED]

# ---------------------------------------------------------------------------
# The fake server's ONE rule, and it is the real one
# ---------------------------------------------------------------------------
# `get_frame_image` in `server/animatics.py` looks the frame up in the saved job
# and 404s when it isn't there. `SAVED` is that job. Nothing else about this
# router matters.
SAVED = {"frames": [], "layers": []}
# Every request that decides the outcome, in the order it arrived.
EVENTS: list[tuple] = []
# Frame pictures the server had to refuse — the 404s that became blank tiles.
MISSES: list[str] = []


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


PANEL_PNG = picture_bytes((74, 134, 200))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def route_api(route, request):
    url = request.url
    if request.method == "OPTIONS":
        route.fulfill(status=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "*",
        })
        return
    cors = {"Access-Control-Allow-Origin": "*"}

    def send(payload, status=200):
        route.fulfill(status=status, headers=cors, content_type="application/json",
                      body=json.dumps(payload))

    # --- the storyboard library the picker lists -----------------------------
    if re.search(r"/storyboards(\?|$)", url):
        # ⚠ THE QUERY IS RECORDED, NOT JUST ANSWERED. `GET /storyboards` filters
        # by WORKFLOW: with no tag it returns only Script to Storyboard's own
        # boards, and `workflow=*` returns every board whatever its tag. This
        # picker asked with no tag, so a board refined in 🖼 Image to Animatic
        # Image was silently missing from it — see the check below.
        EVENTS.append(("list", url))
        # ⚠ **A CROWDED ACCOUNT, NOT A TIDY ONE — THAT IS THE POINT OF `FILLER`.**
        # This stub used to answer with one board, and a one-row dialog fits any
        # window, so the check below could never have failed. The live account
        # that reported "import nahi ho raha hai" had 22 boards: the list grew
        # taller than the screen and carried Cancel and Import off the bottom of
        # a `position: fixed` overlay that does not scroll. A fixture has to be
        # as big as the report or it tests nothing.
        send({"items": [
            {"job_id": BOARD_ID, "character_name": BOARD_NAME, "panel_count": PANELS},
            # ⚠ A BOARD FROM ANOTHER WORKFLOW. A real server only sends this row
            # when it was asked with `workflow=*`; the check below reads the query
            # the picker really made rather than trusting this stub.
            {"job_id": COPIED_ID, "character_name": COPIED_NAME,
             "panel_count": PANELS, "workflow": "animatic-image"},
            *FILLER,
        ]})
        return

    # --- the MEDIA LIBRARY's own picture route -------------------------------
    # ⚠ CONTENT-ADDRESSED, AND THAT IS WHY IT IS A SECOND ROUTE. A library card
    # is asked "which panel?" — (board, index) — so it answers with no clip on the
    # timeline and no save behind it, which is exactly what the frame route below
    # cannot do. The two are checked in one file on purpose: this import creates a
    # clip AND a card, and they are served by different rules.
    if "/animatics/probe/panel/" in url:
        EVENTS.append(("card", url.split("/panel/")[1].split("?")[0]))
        route.fulfill(status=200, headers=cors, content_type="image/png", body=PANEL_PNG)
        return

    # --- one panel's picture, and the rule that broke ------------------------
    match = re.search(r"/animatics/probe/frame/(\w+)", url)
    if match:
        fid = match.group(1)
        EVENTS.append(("picture", fid))
        if fid in {f["id"] for f in SAVED["frames"]}:
            route.fulfill(status=200, headers=cors, content_type="image/png",
                          body=PANEL_PNG)
        else:
            MISSES.append(fid)
            send({"detail": "Frame not found."}, status=404)
        return

    if "/import-storyboard" in url:
        EVENTS.append(("import", BOARD_ID))
        send({"frames": IMPORTED, "name": f"{BOARD_NAME} — storyboard",
              "title": BOARD_NAME, "panels_only": False})
        return

    if url.rstrip("/").endswith("/animatics/luts"):
        send([])
        return

    if re.search(r"/animatics/probe/?(\?.*)?$", url):
        if request.method == "PUT":
            sent = request.post_data_json or {}
            SAVED["frames"] = sent.get("frames") or []
            SAVED["layers"] = sent.get("layers") or []
            EVENTS.append(("save", tuple(f["id"] for f in SAVED["frames"])))
            send({**PROJECT, **SAVED})
            return
        send({**PROJECT, **SAVED})
        return

    send({"detail": "not found"}, status=404)


PROBE_JSX_SOURCE = r"""
import React from "react";
import { createRoot } from "react-dom/client";

import AnimaticEditor from "/src/components/AnimaticEditor.jsx";
import "/src/styles/index.css";

const probe = { errors: [], ready: false };
window.__probe = probe;

window.addEventListener("error", (e) => probe.errors.push(String(e.message || e)));
window.addEventListener("unhandledrejection", (e) =>
  probe.errors.push("unhandled rejection: " + String(e.reason))
);
const realError = console.error;
console.error = (...args) => {
  probe.errors.push(args.map(String).join(" "));
  realError.apply(console, args);
};

createRoot(document.getElementById("root")).render(
  <AnimaticEditor animaticId="probe" onBack={() => {}} onDeleted={() => {}} />
);

/**
 * THE MEDIA PANE'S THUMBNAILS, counted by what is actually IN them.
 *
 * ⚠ `drawn` COUNTS <img> ELEMENTS, `waiting` COUNTS SPINNERS. A card renders one
 * or the other (`.fs-thumb img` when a blob exists, `.fs-thumb-wait` when it does
 * not), so the two numbers together ARE the screenshot in the report: forty-two
 * cards, forty-two spinners, no pictures. Counting cards alone would have passed
 * against the bug.
 *
 * ⚠ THESE ARE THE MEDIA LIBRARY'S CARDS NOW (`MediaBin`), not the timeline's.
 * When this was written the pane listed clips, so the count doubled as "did the
 * frames get their urls?"; the pane lists SOURCES now and the cards are served by
 * `/panel/{board}/{index}` instead. The frame-url question it was really written
 * for is asked directly, below, by `saved_before_shown`.
 */
probe.thumbs = () => ({
  cards: document.querySelectorAll(".fs-thumb").length,
  drawn: document.querySelectorAll(".fs-thumb img").length,
  waiting: document.querySelectorAll(".fs-thumb-wait").length,
});

/** Which rows exist, and what the gutter calls them. */
probe.laneKeys = () =>
  Array.from(document.querySelectorAll("[data-lane]")).map((n) => n.dataset.lane);

probe.laneNames = () =>
  Array.from(document.querySelectorAll(".tl-layer-name")).map((n) =>
    (n.textContent || "").trim()
  );

/** Which row each clip is drawn on, by frame id. */
probe.bars = () => {
  const out = {};
  for (const el of document.querySelectorAll('[data-sel^="frame:"]')) {
    const lane = el.closest("[data-lane]");
    out[el.dataset.sel.slice("frame:".length)] = lane ? lane.dataset.lane : "no-lane";
  }
  return out;
};

probe.notice = () => {
  const el = document.querySelector(".an-status-note");
  return el ? el.textContent : "";
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>board import probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_bimport.jsx"></script>
</body></html>
"""


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


def saved_before_shown(fid):
    """Was `fid` in a PUT before its picture was ever asked for?

    ⚠ THIS IS THE BUG, STATED AS A QUESTION ABOUT TWO REQUESTS. "The pictures
    loaded" can be made true by a later retry or a reload; the defect was that
    the editor asked for them too early, and this is the only phrasing of it that
    cannot be satisfied by accident.
    """
    first_ask = next((i for i, e in enumerate(EVENTS) if e == ("picture", fid)), None)
    if first_ask is None:
        return False, "its picture was never asked for at all"
    saved_at = next(
        (i for i, e in enumerate(EVENTS) if e[0] == "save" and fid in e[1]), None
    )
    if saved_at is None:
        return False, "no PUT ever carried it"
    if saved_at > first_ask:
        return False, f"asked for at #{first_ask}, saved only at #{saved_at}"
    return True, ""


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
        if vite is None:
            print("  Vite would not start — cannot drive the editor.")
            return 2

        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=[
                "--use-gl=angle", "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            ])
            page = browser.new_page(viewport={"width": 1600, "height": 1200})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            page.route("**/storyboards", route_api)
            page.route("**/storyboards?*", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_bimport.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nA new animatic, with nothing in it")
            try:
                page.wait_for_selector("button.tl-add-layer", timeout=45000)
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts on an empty project", False, str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=shot("bimport_probe_failed.png"))
                browser.close()
                return 1
            check("it opens with no pictures at all",
                  page.evaluate("() => window.__probe.thumbs().cards") == 0,
                  str(page.evaluate("() => window.__probe.thumbs()")))

            # -----------------------------------------------------------------
            # ＋ Add layer → Storyboard images → pick the board → Import
            # -----------------------------------------------------------------
            # ⚠ THE GESTURE FROM THE REPORT, PRESS FOR PRESS. "Storyboard images"
            # does NOT add a row here — it opens the picker (`openBoardImport`),
            # because a board row you cannot fill from its own ＋ would be a row
            # for nothing. So the row and the frames are made by ONE action, which
            # is exactly why they have to be saved by one write.
            print("\n＋ Add layer → Storyboard images → Import")
            page.click("button.tl-add-layer")
            page.wait_for_selector(".tl-layer-menu", timeout=5000)
            page.click(".tl-layer-menu-opt:has-text('Storyboard images')")
            try:
                page.wait_for_selector(".an-board-modal", timeout=5000)
                page.wait_for_selector(".an-board-opt", timeout=10000)
            except Exception as exc:  # noqa: BLE001
                check("the picker opens and lists the storyboards", False, str(exc)[:160])
                page.screenshot(path=shot("bimport_probe_failed.png"))
                browser.close()
                return 1
            check("the picker opens and lists the storyboards",
                  BOARD_NAME in page.inner_text(".an-board-list"),
                  page.inner_text(".an-board-list")[:120])
            # ⭐ THE FILTER BUG, 2026-09-06. Two screenshots side by side: the
            # dashboard said "22 boards ready" and this very dialog listed 17.
            # The dialog opened perfectly — the board they wanted simply was not
            # in it, which is the worst shape a filter bug can take. `AnimaticLibrary`
            # and `FinalVideoLibrary` both ask with `workflow=*`; only this door
            # disagreed, and `GET /storyboards`'s own docstring says downstream
            # workflows must ask that way or "the copies are a dead end".
            asked = [u for (kind, u) in EVENTS if kind == "list"]
            check("⭐ THE PICKER ASKS FOR EVERY WORKFLOW'S BOARDS, not just the untagged ones",
                  bool(asked) and all("workflow=%2A" in u or "workflow=*" in u for u in asked),
                  " | ".join(asked)[:200])
            check("…so a board refined in another workflow is offered here too",
                  COPIED_NAME in page.inner_text(".an-board-list"),
                  page.inner_text(".an-board-list")[:200])
            # ⚠ AND IT SAYS WHICH WORKFLOW IT CAME FROM. A copy keeps the
            # original's NAME, so a list of every workflow's boards draws pairs
            # of identical rows unless the tag is on them.
            check("…and it is labelled, so two boards of the same name are telling apart",
                  "Image to Animatic Image" in page.inner_text(".an-board-list"),
                  page.inner_text(".an-board-list")[:200])

            # -----------------------------------------------------------------
            # ⭐ THE FOOTER IS ON SCREEN — "import nahi ho raha hai", 2026-09-06
            # -----------------------------------------------------------------
            # With 22 boards the dialog grew past the bottom of the window and
            # took Cancel and Import off the screen with it. Nothing errored and
            # nothing said so; the button simply could not be reached, on an
            # overlay that is `position: fixed` and does not scroll the page.
            # ⚠ THE QUESTION IS "IS IT INSIDE THE VIEWPORT", not "is it in the
            # DOM" — it was always in the DOM. That is the whole difference
            # between this check and one a grep could have written.
            print("\n＋ …and the Import button is still reachable with a full list")
            # ⚠ **ON A LAPTOP, NOT ON THE 1200px TEST WINDOW.** The rest of this
            # file wants a tall viewport so the timeline is not cramped — and a
            # tall viewport is exactly what hides this bug: 22 rows fit in 1200px
            # and the footer never leaves. The report came from a normal laptop.
            # Checked at 1366×768 and put back afterwards, so nothing below this
            # inherits the smaller window.
            page.set_viewport_size({"width": 1366, "height": 768})
            page.wait_for_timeout(120)
            rows = page.query_selector_all(".an-board-opt")
            check("the dialog really is holding a crowded list",
                  len(rows) >= 20, f"{len(rows)} rows")
            btn = page.query_selector(".an-board-modal button.primary")
            bb = btn.bounding_box() if btn else None
            vh = page.evaluate("window.innerHeight")
            check("⭐ IMPORT IS INSIDE THE WINDOW, not pushed off the bottom",
                  bool(bb) and bb["y"] + bb["height"] <= vh,
                  f"button bottom={None if not bb else round(bb['y'] + bb['height'])} viewport={vh}")
            cancel = page.query_selector(".an-board-modal .btn.ghost")
            cb = cancel.bounding_box() if cancel else None
            check("…and so is Cancel, which is the way out",
                  bool(cb) and cb["y"] + cb["height"] <= vh,
                  f"cancel bottom={None if not cb else round(cb['y'] + cb['height'])} viewport={vh}")
            check("…and the heading did not get pushed off the top either",
                  page.query_selector(".an-board-modal h2").bounding_box()["y"] >= 0)
            # ⚠ AND THE LIST IS WHAT ABSORBED IT — it scrolls inside itself, so
            # the footer stays put rather than the whole card scrolling and
            # putting Import at the end of a long scroll.
            check("…because the LIST scrolls, not the card",
                  page.evaluate(
                      "(() => { const l = document.querySelector('.an-board-list');"
                      " return l.scrollHeight > l.clientHeight + 4; })()"))
            page.set_viewport_size({"width": 1600, "height": 1200})
            page.wait_for_timeout(120)

            page.click(f".an-board-opt:has-text('{BOARD_NAME}')")
            page.click(".an-board-modal button.primary")
            # The import SPANS a save now, so the button spins for a round trip
            # before anything appears. Waiting on the modal closing is waiting on
            # exactly that.
            try:
                page.wait_for_selector(".an-board-modal", state="detached", timeout=20000)
            except Exception as exc:  # noqa: BLE001
                err = page.query_selector(".an-board-modal .error")
                check("the import finishes", False,
                      (err.inner_text() if err else str(exc))[:200])
                page.screenshot(path=shot("bimport_probe_failed.png"))
                browser.close()
                return 1
            page.wait_for_timeout(2000)

            # -----------------------------------------------------------------
            # THE CLAIM: the panels are on screen, with their pictures in them
            # -----------------------------------------------------------------
            print("\nThe panels arrive WITH their pictures")
            thumbs = page.evaluate("() => window.__probe.thumbs()")
            check(f"all {PANELS} panels are in the Media pane",
                  thumbs["cards"] == PANELS, str(thumbs))
            check("every one of them has drawn its picture",
                  thumbs["drawn"] == PANELS, str(thumbs))
            check("…so nothing is left on a spinner",
                  thumbs["waiting"] == 0, str(thumbs))
            check("and the server never had to refuse a panel's picture",
                  not MISSES, f"404'd: {sorted(set(MISSES))}")

            print("\nWhich is a question about the ORDER of two requests")
            for fid in IMPORTED_IDS:
                good, why = saved_before_shown(fid)
                check(f"{fid} was SAVED before its picture was asked for", good, why)

            # -----------------------------------------------------------------
            # The row, and the one write it shares with its frames
            # -----------------------------------------------------------------
            print("\nThe row the import made")
            bars = page.evaluate("() => window.__probe.bars()")
            check("every panel is a clip on the timeline",
                  sorted(bars) == sorted(IMPORTED_IDS), str(bars))
            lanes = set(bars.values())
            check("…all on ONE row of their own", len(lanes) == 1, str(lanes))
            # ⚠ AFTER ITS KIND, NOT AFTER THE BOARD — and this check used to assert
            # the opposite. Naming the row "TTBB EP One" left the gutter reading
            # "TTBB E…" with nothing on screen saying which of the four picture
            # kinds it was, and the user asked for the kind: "i see my storyborad
            # namke come and show in layer but this not happen i want you keep
            # Story..Image". Which board the panels came from is on every card in
            # Media and in the import's own notice.
            names = page.evaluate("() => window.__probe.laneNames()")
            check("and the gutter calls it after its KIND",
                  "Story..Image" in names, str(names))
            check("…not after the board",
                  not any(BOARD_NAME in n for n in names), str(names))

            # ⚠ ONE WRITE, NOT TWO. The row and the frames that sit on it go up
            # together; two writes racing the 900ms autosave is how a row loses
            # the name it was given.
            saves = [e for e in EVENTS if e[0] == "save"]
            carried = next((e for e in saves if set(IMPORTED_IDS) <= set(e[1])), None)
            check("the frames went up in a single write", carried is not None,
                  f"saves: {[len(e[1]) for e in saves]}")
            check("and that same write carried their row",
                  any(lane.get("kind") == "board_image" for lane in SAVED["layers"]),
                  json.dumps(SAVED["layers"])[:200])
            check("with the clips on the row's own track",
                  bool(SAVED["layers"]) and all(
                      f.get("track") == SAVED["layers"][0].get("track")
                      for f in SAVED["frames"]),
                  json.dumps({"layers": SAVED["layers"],
                              "tracks": [f.get("track") for f in SAVED["frames"]]})[:240])

            print("\nAfterwards")
            check("the import said what it did",
                  "imported" in (page.evaluate("() => window.__probe.notice()") or "").lower(),
                  page.evaluate("() => window.__probe.notice()"))
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:400])
            if failures:
                page.screenshot(path=shot("bimport_probe_failed.png"))
            browser.close()
    finally:
        if vite is not None:
            vite.terminate()
        for path in (PROBE_JSX, PROBE_HTML):
            try:
                os.remove(path)
            except OSError:
                pass

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("An imported board is saved before it is shown, so its panels have pictures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
