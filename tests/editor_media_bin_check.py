"""THE MEDIA PANE IS A LIBRARY — ITS CARDS RENAME, A ROW LOCKS, AND ✕ ASKS FIRST.

Three reports, and one of them arrived later. The first three sections answer:

    "i see when i upload/generate Veo video and then i delete in time so i see in
     media panel also delete … i want when user delete video, storboard image,
     veo video, audio and shapes in timeline after upload in media so only clip
     delete in timeline not delete in media panel i want stay in media panel so
     user need deleetd cipl again so user go media panle and drang and drop in
     perticular layer

     and see image 2 i wnat you add lock icon in layer and eyes icon alredy place
     and x cross icon remove not layer clip i wnat you add fucntion x cross icon
     when user click x buttun so user get same place dropdron with deleted layer
     masg then user click delete and cancel so a layer and clip delete."

They are checked together because they are three answers to one question — "what
happens when I press a delete" — and each one's fix is the other two's regression
risk.

The FOURTH section is a later report about the same pane:

    "i want rename ifuction add in media panel … if add any in media panel buy
     user and generted image and video so user go and double click on clip text
     so uver get rename option like this or user right click of moue on clip so
     user get dropdown panel so rename of each clip in media kepp both fuction
     and add in drop down like more option see iamge 3 some improtent fuction in
     clip when user click on clip in media"

⚠ IT BELONGS IN THIS FILE AND NOT A NEW ONE, because a rename is the edit that
crosses the seam the first three sections exist to defend: the pane draws
`asset.label` and the timeline draws `frame.label`, so "the card outlives the
clip" and "the name reaches both lists" are two halves of the same invariant and
each is the other's regression risk. See `renameAsset` in AnimaticEditor.jsx.

---------------------------------------------------------------------------
⚠ THE LIBRARY CHECK IS "IS IT STILL THERE?", ASKED AFTER A DELETE
---------------------------------------------------------------------------
The Media pane used to BE the timeline: it listed `frames`, grouped by where each
clip came from, so deleting a clip deleted the only record that its source had
ever been added. Every card being present is therefore NOT the assertion — that
was true of the bug too, right up until the delete. The assertion is that the
card outlives the clip, and then that dragging it back out MAKES a clip. See
`animatic/assets.js`.

⚠ AND THE LOCK CHECK IS "WHAT DID NOT HAPPEN", which needs a before and an after:
a locked row that refuses a drag looks exactly like a drag that missed. So every
lock assertion reads the clip's position both sides of the gesture and names what
moved, exactly as `editor_picture_tracks_check.py` does.

    python tests/editor_media_bin_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router — the harness is `editor_board_import_check.py`'s.
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

PROBE_HTML = os.path.join(CLIENT, "__probe_bin.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_bin.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture — a board row and a video row, and a library that already has both
# ---------------------------------------------------------------------------
# ⚠ THE PROJECT CARRIES `assets`, so the WHOLE-library backfill is NOT what is
# under test here: this is a project saved since the library existed. (That one is
# pure and is checked in `tests/asset_fields_check.py`.)
#
# ⚠ BUT IT ALSO CARRIES A KEY POSE ON THE TIMELINE WITH NO CARD, and that IS under
# test. It is the state every project blocked out by the first build of
# ✨ Animatic images is in: the drawings were placed and deliberately not carded,
# so the Media pane could not show them and there was no way to save one —
# "i can't see animatic images in midea". A project WITH a library is never
# re-derived, so nothing was ever going to notice; the narrow repair in
# `onLoadedRef` is what does, and this fixture is what proves it runs.
#
# ⚠ AND THERE IS A THIRD CARD WITH NO CLIP — the "orphan". It is the state the
# whole feature exists to make possible, and the one the old pane could not
# represent at all, so it is in the fixture from the start rather than only
# arrived at by deleting something.
BOARD_ID = "board1"
VID_UPLOAD = "uvid00000001"
ORPHAN_UPLOAD = "uorphan00001"

PROJECT = {
    "id": "probe",
    "title": "probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False, "hidden_lanes": [], "locked_lanes": []},
    "frames": [
        {"id": "p1", "kind": "image",
         "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": 0},
         "duration_ms": 2000, "start_ms": 0, "track": 0, "label": "Shot 1",
         "url": "/animatics/probe/frame/p1?v=1"},
        {"id": "p2", "kind": "image",
         "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": 1},
         "duration_ms": 2000, "start_ms": 2000, "track": 0, "label": "Shot 2",
         "url": "/animatics/probe/frame/p2?v=1"},
        {"id": "v1", "kind": "video",
         "src": {"kind": "video", "upload_id": VID_UPLOAD},
         "duration_ms": 4000, "start_ms": 0, "track": 1, "label": "TTBB_EP_1",
         "in_ms": 0, "out_ms": 54420, "speed": 1,
         "url": f"/animatics/probe/media/{VID_UPLOAD}?poster=1"},
        # ⚠ ONE KEY POSE OF SHOT 1, on its own row, WITH NO CARD IN `assets`.
        {"id": "k1", "kind": "image",
         "src": {"kind": "pose", "storyboard_id": BOARD_ID, "index": 0, "frame": 2},
         "duration_ms": 250, "start_ms": 0, "track": 2, "label": "Shot 1 - 3",
         "url": "/animatics/probe/frame/k1?v=1"},
    ],
    "layers": [
        {"id": "L0", "kind": "board_image", "name": "Storyboard images", "track": 0},
        {"id": "L1", "kind": "video", "name": "Video", "track": 1},
        {"id": "L2", "kind": "board_poses", "name": "Anim..Image", "track": 2},
    ],
    "assets": [
        {"id": "a1", "kind": "image",
         "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": 0},
         "upload_id": "", "label": "Shot 1", "duration_ms": 2000, "color": "#000000",
         "url": f"/animatics/probe/panel/{BOARD_ID}/0?v=1"},
        {"id": "a2", "kind": "image",
         "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": 1},
         "upload_id": "", "label": "Shot 2", "duration_ms": 2000, "color": "#000000",
         "url": f"/animatics/probe/panel/{BOARD_ID}/1?v=1"},
        {"id": "a3", "kind": "video",
         "src": {"kind": "video", "upload_id": VID_UPLOAD},
         "upload_id": "", "label": "TTBB_EP_1", "duration_ms": 54420, "color": "#000000",
         "url": f"/animatics/probe/media/{VID_UPLOAD}?poster=1"},
        # THE ORPHAN: in the library, on no row.
        {"id": "a4", "kind": "image",
         "src": {"kind": "upload", "upload_id": ORPHAN_UPLOAD},
         "upload_id": "", "label": "spare.png", "duration_ms": 2000, "color": "#000000",
         "url": f"/animatics/probe/media/{ORPHAN_UPLOAD}"},
    ],
    "texts": [], "shapes": [], "overlays": [], "transitions": [],
    "audio_tracks": [], "veo_clips": [], "video": None,
}

# FOUR CARDS ARE SAVED WITH THE PROJECT, and the fifth is the one the load
# REPAIRS in — the key pose above. Written down once rather than as a literal 4
# in five places, because the number is incidental to every assertion that reads
# it ("nothing else left the library") and updating four of five is how a count
# check comes to be measuring nothing.
CARDS_TOTAL = 5

SAVED: dict = {}


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


PNG = picture_bytes((74, 134, 200))


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

    # Every picture route answers, including the library's own `/panel/{board}/{i}`
    # — the route that exists so a card is servable with no clip behind it.
    if re.search(r"/animatics/probe/(frame|media|panel)/", url):
        route.fulfill(status=200, headers=cors, content_type="image/png", body=PNG)
        return
    if url.rstrip("/").endswith("/animatics/luts"):
        send([])
        return
    if re.search(r"/animatics/probe/?(\?.*)?$", url):
        if request.method == "PUT":
            SAVED.clear()
            SAVED.update(request.post_data_json or {})
            send(PROJECT)
            return
        send(PROJECT)
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

/** The LIBRARY's cards: what is listed, and whether each drew its picture. */
probe.bin = () =>
  Array.from(document.querySelectorAll(".fs-bin-card")).map((card) => ({
    label: (card.querySelector(".fs-label") || {}).textContent || "",
    used: (card.querySelector(".fs-num") || {}).textContent || "",
    drawn: Boolean(card.querySelector(".fs-thumb img")),
  }));

/**
 * THE MEDIA PANE'S SECTIONS — title, count and what is listed under each.
 *
 * ⚠ SECTION BY SECTION, NOT ONE FLAT LIST. `probe.bin()` cannot tell "the card
 * exists" from "the card is somewhere a person will find it", and the report was
 * the second of those: the drawings were filed with the panels, in a section
 * people keep folded shut.
 */
probe.sections = () =>
  Array.from(document.querySelectorAll(".an-media-body .an-grp")).map((sec) => ({
    title: (sec.querySelector(".an-grp-title") || {}).textContent || "",
    count: (sec.querySelector(".an-grp-count") || {}).textContent || "",
    labels: Array.from(sec.querySelectorAll(".fs-bin-card .fs-label")).map(
      (n) => n.textContent
    ),
  }));

/** Does the card with this label offer a ⬇? `null` when there is no such card. */
probe.hasDownload = (label) => {
  const card = Array.from(document.querySelectorAll(".fs-bin-card")).find(
    (c) => ((c.querySelector(".fs-label") || {}).textContent || "") === label
  );
  if (!card) return null;
  return Array.from(card.querySelectorAll(".fs-tool")).some((b) =>
    (b.getAttribute("aria-label") || "").startsWith("Download")
  );
};

/** The TIMELINE's clips: which row each is on, and where it starts, in pixels. */
probe.bars = () => {
  const out = {};
  for (const el of document.querySelectorAll('[data-sel^="frame:"]')) {
    const r = el.getBoundingClientRect();
    const lane = el.closest("[data-lane]");
    out[el.dataset.sel.slice("frame:".length)] = {
      left: Math.round(r.left),
      width: Math.round(r.width),
      lane: lane ? lane.dataset.lane : "no-lane",
    };
  }
  return out;
};

/** One gutter row, by the name shown in it. */
probe.rowByName = (name) => {
  const rows = Array.from(document.querySelectorAll(".tl-gutter-row"));
  const row = rows.find((r) => (r.querySelector(".tl-layer-name") || {}).textContent === name);
  if (!row) return null;
  // ⚠ THE CONFIRM IS NOT INSIDE THE ROW ANY MORE. It opens BESIDE the row, as a
  // child of `.tl-cols`, so that it neither runs off the bottom of the gutter nor
  // scrolls the labels out of line with the tracks when its Delete takes focus
  // (user-reported). It carries `data-confirm` = the row's lane key, which is
  // what still ties the two together. See `editor_media_row_routing_check.py`.
  const key = row.dataset.laneRow;
  const confirm = key
    ? document.querySelector(`.tl-layer-confirm[data-confirm="${key}"]`)
    : null;
  return {
    locked: row.classList.contains("locked"),
    lockPressed: (row.querySelector(".tl-layer-lock") || {}).getAttribute?.("aria-pressed"),
    delDisabled: Boolean((row.querySelector(".tl-layer-del") || {}).disabled),
    confirmOpen: Boolean(confirm),
    confirmText: (confirm?.querySelector(".tl-layer-confirm-msg") || {}).textContent || "",
  };
};

probe.rowNames = () =>
  Array.from(document.querySelectorAll(".tl-layer-name")).map((n) => n.textContent);

probe.notice = () => {
  const el = document.querySelector(".an-status-note");
  return el ? el.textContent : "";
};

/**
 * A DRAG, dispatched by hand.
 *
 * Playwright's mouse does not start an HTML5 drag in a headless Chromium, so the
 * events are fired over one shared `DataTransfer` — the trick `dragKind` needs,
 * since the marker types are all a lane can read during `dragover`. Borrowed
 * from `editor_effects_drop_check.py` rather than reinvented.
 */
probe.drag = (fromSel, laneKey, xFrac) => {
  const source = document.querySelector(fromSel);
  const target = document.querySelector(`.tl-lane[data-lane="${laneKey}"]`);
  if (!source) return "no source " + fromSel;
  if (!target) return "no lane " + laneKey;
  const dt = new DataTransfer();
  const fire = (el, type, extra) =>
    el.dispatchEvent(new DragEvent(type, {
      bubbles: true, cancelable: true, dataTransfer: dt, ...extra,
    }));
  const box = target.getBoundingClientRect();
  const at = {
    clientX: box.left + box.width * (xFrac === undefined ? 0.33 : xFrac),
    clientY: box.top + box.height / 2,
  };
  fire(source, "dragstart", {});
  fire(target, "dragenter", at);
  fire(target, "dragover", at);
  fire(target, "drop", at);
  fire(source, "dragend", {});
  return "";
};

/** Click a gutter row's control by name. */
probe.press = (rowName, cls) => {
  const rows = Array.from(document.querySelectorAll(".tl-gutter-row"));
  const row = rows.find((r) => (r.querySelector(".tl-layer-name") || {}).textContent === rowName);
  if (!row) return "no row " + rowName;
  const btn = row.querySelector(cls);
  if (!btn) return "no " + cls;
  if (btn.disabled) return cls + " is disabled";
  btn.click();
  return "";
};

/** The library card whose label is `label`, as a selector. */
probe.cardSel = (label) => {
  const cards = Array.from(document.querySelectorAll(".fs-bin-card"));
  const i = cards.findIndex(
    (c) => (c.querySelector(".fs-label") || {}).textContent === label
  );
  if (i < 0) return "";
  cards[i].setAttribute("data-probe", "card");
  return '[data-probe="card"]';
};

/**
 * The same, but under a name of the caller's choosing.
 *
 * ⚠ `cardSel` REUSES ONE ATTRIBUTE VALUE, so a second call leaves TWO cards
 * carrying `data-probe="card"` and `querySelector` then returns whichever is
 * first in the DOM — not the one just asked for. That is survivable for the
 * drags above (they assert that nothing moved), and it is not survivable for a
 * rename, which must name the card it actually renamed.
 */
probe.tag = (label, name) => {
  const cards = Array.from(document.querySelectorAll(".fs-bin-card"));
  const card = cards.find(
    (c) => (c.querySelector(".fs-label") || {}).textContent === label
  );
  if (!card) return "";
  card.setAttribute("data-tag", name);
  return '[data-tag="' + name + '"]';
};

/** What a clip is CALLED on the timeline — the half a rename has to reach. */
probe.barLabels = () => {
  const out = {};
  for (const el of document.querySelectorAll('[data-sel^="frame:"]')) {
    out[el.dataset.sel.slice("frame:".length)] =
      (el.querySelector(".tl-bar-label") || {}).textContent || "";
  }
  return out;
};

/** Is a card's name a FIELD right now, and what is in it? */
probe.naming = () => {
  const el = document.querySelector(".fs-name-input");
  if (!el) return null;
  return {
    value: el.value,
    focused: document.activeElement === el,
    // The card must not be draggable while its name is being typed, or the
    // pointer cannot select the text — see the note on `draggable` in MediaBin.
    cardDraggable: el.closest(".fs-bin-card")?.getAttribute("draggable") !== "false",
  };
};

/**
 * ONE MENU LINE'S WORDS, without its icon.
 *
 * ⚠ `textContent` IS NOT THE LABEL. A `.tl-layer-menu-ico` holds an <svg> on most
 * lines — which contributes nothing — but a plain GLYPH on two of them (＋ and
 * ‹), so reading the button whole gives "＋Add to timeline" and a match on the
 * words a user can see fails on exactly those two.
 */
const optLabel = (btn) => {
  const ico = (btn.querySelector(".tl-layer-menu-ico") || {}).textContent || "";
  const all = (btn.textContent || "").trim();
  return (ico && all.startsWith(ico) ? all.slice(ico.length) : all).trim();
};

/**
 * The open card menu — what it offers, and whether it is on screen.
 *
 * ⚠ THE GEOMETRY IS PART OF THE ASSERTION. The menu is `position: fixed` and
 * placed from the pointer, so "it opened" is not the same claim as "you can see
 * it": a card near the bottom of the pane is exactly where a menu that does not
 * flip runs off the window.
 */
probe.menu = () => {
  const el = document.querySelector(".fs-card-menu");
  if (!el) return null;
  const box = el.getBoundingClientRect();
  return {
    of: (el.querySelector(".tl-clip-menu-of") || {}).textContent || "",
    items: Array.from(el.querySelectorAll(".tl-layer-menu-opt")).map((b) => ({
      text: optLabel(b),
      disabled: Boolean(b.disabled),
    })),
    props: Array.from(el.querySelectorAll(".fs-card-prop")).map((r) => [
      (r.querySelector("dt") || {}).textContent || "",
      (r.querySelector("dd") || {}).textContent || "",
    ]),
    onScreen:
      box.left >= 0 && box.top >= 0 &&
      box.right <= window.innerWidth && box.bottom <= window.innerHeight,
    fixed: getComputedStyle(el).position === "fixed",
  };
};

/** Press a line in the open card menu, matched on the start of its text. */
probe.menuClick = (text) => {
  const el = document.querySelector(".fs-card-menu");
  if (!el) return "no menu is open";
  const btn = Array.from(el.querySelectorAll(".tl-layer-menu-opt")).find((b) =>
    optLabel(b).startsWith(text)
  );
  if (!btn) return "no line starting " + text;
  if (btn.disabled) return text + " is disabled";
  btn.click();
  return "";
};

/** How many clips are selected — what "Select its clips" has to change. */
probe.selCount = () => document.querySelectorAll(".tl-bar.sel").length;
/**
 * The pictures on the IMAGES lanes — `overlays`, which are not `frames`.
 *
 * ⚠ A DIFFERENT LIST AND A DIFFERENT BAR CLASS (`.tl-overlay`, where a picture
 * clip is `.tl-bar`), which is exactly how the library came to miss them.
 */
probe.overlayBars = () =>
  Array.from(document.querySelectorAll('[data-sel^="overlay:"]'))
    .map((el) => el.dataset.sel.slice("overlay:".length))
    .sort();

/** How many of THOSE are selected. */
probe.selOverlayCount = () => document.querySelectorAll(".tl-overlay.sel").length;


/**
 * Where the open menu sits RELATIVE TO ITS CARD.
 *
 * ⚠ THE CARD IS FOUND BY `.menu-open`, not by the tag the caller set, so this
 * also asserts that the pane marks which card the menu belongs to — the menu is
 * `position: fixed` and can be flipped away from the card it names.
 */
probe.menuGap = () => {
  const m = document.querySelector(".fs-card-menu");
  const c = document.querySelector(".fs-bin-card.menu-open");
  if (!m || !c) return null;
  const mb = m.getBoundingClientRect();
  const cb = c.getBoundingClientRect();
  return { dx: Math.round(mb.left - cb.left), dy: Math.round(mb.top - cb.top) };
};

/** Scroll the Media pane, and say how far it really moved. */
probe.scrollMedia = (dy) => {
  const el = document.querySelector(".an-media-body");
  if (!el) return null;
  const was = el.scrollTop;
  el.scrollTop = was + dy;
  return el.scrollTop - was;
};

/**
 * A SCROLL EVENT ON THE MEDIA PANE, with nothing actually moving.
 *
 * ⚠ THIS IS THE REGRESSION, NOT A SIMULATION OF ONE. Focusing the menu's first
 * line makes the pane emit a scroll of its own — seen in this very harness — and
 * the first version of the menu closed on any scroll, so it shut in the frame it
 * opened. Dispatching the bare event is the smallest thing that reproduces it,
 * and it does not depend on the fixture's library being tall enough to scroll.
 * (`scroll` does not bubble; the menu listens in the CAPTURE phase, which is what
 * makes an event targeted at the pane reachable from `window`.)
 */
probe.fakeScroll = () => {
  const el = document.querySelector(".an-media-body");
  if (!el) return "no .an-media-body";
  el.dispatchEvent(new Event("scroll"));
  return "";
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>media bin probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_bin.jsx"></script>
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


def labels(bin_rows):
    return sorted(r["label"] for r in bin_rows)


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
            page = browser.new_page(viewport={"width": 1700, "height": 1200})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_bin.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nThe Media pane lists SOURCES, not clips")
            try:
                page.wait_for_selector("canvas", timeout=45000)
                page.wait_for_function(
                    "() => document.querySelectorAll('.fs-bin-card').length >= 4",
                    timeout=45000,
                )
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts with the library drawn", False, str(exc)[:200])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=shot("bin_probe_failed.png"))
                browser.close()
                return 1
            page.wait_for_timeout(1500)

            # -----------------------------------------------------------------
            # A KEY POSE ON THE TIMELINE WITH NO CARD IS PUT RIGHT, IN A SECTION
            # OF ITS OWN — and only what this app MADE offers a ⬇
            # -----------------------------------------------------------------
            print("\nAnimatic images reach Media, and only generated cards save")
            secs = page.evaluate("() => window.__probe.sections()")
            titles = [x["title"] for x in secs]
            check("the pane grows an Animatic Images section",
                  "Animatic Images" in titles, json.dumps(titles))
            poses = next((x for x in secs if x["title"] == "Animatic Images"), None)
            check("…carrying the drawing that had no card",
                  bool(poses) and "Shot 1 - 3" in poses["labels"],
                  json.dumps(poses))
            # ⚠ AND IT IS NOT FILED WITH THE PANELS. Sixteen drawings a shot would
            # bury them, which is the half of the report a card alone does not fix.
            board = next((x for x in secs if x["title"] == "Storyboard Frames"), None)
            check("…and NOT in among the panels it was drawn from",
                  bool(board) and "Shot 1 - 3" not in board["labels"],
                  json.dumps(board))
            # ⚠ THE ⬇ IS FOR WHAT THIS APP MADE. A key pose costs an image credit
            # and there is no other copy of it; a file the user dropped in is
            # already on their machine — "only generated cheezon par dikhe ye ⬇".
            check("the key pose offers a Download",
                  page.evaluate("() => window.__probe.hasDownload('Shot 1 - 3')") is True,
                  "no ⬇ on the drawing")
            check("…so does the storyboard panel it was drawn from",
                  page.evaluate("() => window.__probe.hasDownload('Shot 1')") is True,
                  "no ⬇ on the panel")
            check("…but an uploaded file does not",
                  page.evaluate("() => window.__probe.hasDownload('spare.png')") is False,
                  "an upload is already on the user's machine")

            shelf = page.evaluate("() => window.__probe.bin()")
            check("every source is listed", len(shelf) == CARDS_TOTAL, json.dumps(shelf))
            # ⚠ THE CARD WITH NO CLIP IS THE POINT. The old pane could not draw
            # this row at all: it listed clips, so a source with none did not exist.
            orphan = next((r for r in shelf if r["label"] == "spare.png"), None)
            check("…including one with NO clip on the timeline", orphan is not None,
                  json.dumps(labels(shelf)))
            check("…and it says so, rather than claiming a place in the sequence",
                  bool(orphan) and orphan["used"] == "–", json.dumps(orphan))
            used = next((r for r in shelf if r["label"] == "Shot 1"), None)
            check("a source in the cut says how many clips use it",
                  bool(used) and used["used"] == "×1", json.dumps(used))
            check("every card drew its picture",
                  all(r["drawn"] for r in shelf), json.dumps(shelf))

            # -----------------------------------------------------------------
            # THE REPORT: delete the clip, keep the source
            # -----------------------------------------------------------------
            print("\nDeleting a clip leaves its source in Media")
            page.click('[data-sel="frame:v1"]')
            page.wait_for_timeout(200)
            page.keyboard.press("Delete")
            page.wait_for_timeout(600)
            bars = page.evaluate("() => window.__probe.bars()")
            check("the clip is off the timeline", "v1" not in bars, json.dumps(bars))
            shelf = page.evaluate("() => window.__probe.bin()")
            kept = next((r for r in shelf if r["label"] == "TTBB_EP_1"), None)
            check("but its source is STILL in Media", kept is not None,
                  json.dumps(labels(shelf)))
            check("…now listed as used by nothing",
                  bool(kept) and kept["used"] == "–", json.dumps(kept))
            check("and nothing else left the library", len(shelf) == CARDS_TOTAL, json.dumps(labels(shelf)))

            # -----------------------------------------------------------------
            # …and drag it back out
            # -----------------------------------------------------------------
            print("\nDragging a source out makes a new clip")
            sel = page.evaluate("() => window.__probe.cardSel('TTBB_EP_1')")
            check("the card can be found to drag", bool(sel), "no card labelled TTBB_EP_1")
            if sel:
                before = set(page.evaluate("() => window.__probe.bars()"))
                why = page.evaluate(
                    "([s, k]) => window.__probe.drag(s, k, 0.4)", [sel, "frames:1"]
                )
                page.wait_for_timeout(700)
                after = page.evaluate("() => window.__probe.bars()")
                fresh = [k for k in after if k not in before]
                check("a clip appeared on the row it was dropped on",
                      not why and len(fresh) == 1
                      and after[fresh[0]]["lane"] == "frames:1",
                      why or json.dumps(after))
                # ⚠ A COPY, NOT A MOVE: the library keeps the card. A `frame`
                # payload would have moved a clip; an `asset` payload makes one.
                shelf = page.evaluate("() => window.__probe.bin()")
                check("…and the card is still in Media, now used once",
                      len(shelf) == CARDS_TOTAL
                      and next(r for r in shelf if r["label"] == "TTBB_EP_1")["used"] == "×1",
                      json.dumps(shelf))

            # -----------------------------------------------------------------
            # A CARD CAN BE RENAMED — on its NAME, and from its menu
            # -----------------------------------------------------------------
            # ⚠ THE ASSERTION IS THAT THE NAME REACHES BOTH LISTS. The library and
            # the timeline are two lists on purpose, and a rename is the one edit
            # that has to cross between them: the pane draws `asset.label`, the
            # timeline draws `frame.label`, and a source renamed whose bars still
            # read the old name is a rename that visibly did not take. So every
            # check below reads the card AND the bar.
            print("\nRenaming a source, by double-clicking its name")
            sel = page.evaluate("() => window.__probe.tag('TTBB_EP_1', 'ren')")
            check("the card to rename can be found", bool(sel), "no card labelled TTBB_EP_1")
            if sel:
                was = page.evaluate("() => window.__probe.barLabels()")
                page.dblclick(f"{sel} .fs-label")
                page.wait_for_timeout(300)
                naming = page.evaluate("() => window.__probe.naming()")
                check("double-clicking the NAME opens a field",
                      naming is not None, "no .fs-name-input appeared")
                check("…with the old name in it, to be typed over",
                      bool(naming) and naming["value"] == "TTBB_EP_1", json.dumps(naming))
                check("…already focused, so no second click is needed",
                      bool(naming) and naming["focused"], json.dumps(naming))
                # ⚠ A DRAGGABLE ANCESTOR EATS A TEXT SELECTION — the drag wins the
                # gesture — so the card has to stop being draggable for exactly as
                # long as the field is open, or the name can only be edited from
                # the end.
                check("…and the card stops being draggable while it is open",
                      bool(naming) and not naming["cardDraggable"], json.dumps(naming))

                page.keyboard.type("Chase wide")
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                shelf = page.evaluate("() => window.__probe.bin()")
                check("↵ takes the new name",
                      "Chase wide" in labels(shelf), json.dumps(labels(shelf)))
                check("…without growing a second card for the same source",
                      len(shelf) == CARDS_TOTAL, json.dumps(labels(shelf)))
                now = page.evaluate("() => window.__probe.barLabels()")
                renamed = [k for k in now if now[k] == "Chase wide"]
                check("the clip cut from it takes the new name too",
                      len(renamed) == 1, f"{was} -> {now}")
                check("…and no other clip on the timeline was touched",
                      all(now[k] == was.get(k) for k in now if k not in renamed),
                      f"{was} -> {now}")

                # Escape throws the typing away — the field is a draft until ↵.
                page.dblclick(f"{sel} .fs-label")
                page.wait_for_timeout(250)
                page.keyboard.type("scrap")
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
                shelf = page.evaluate("() => window.__probe.bin()")
                check("Escape throws the typing away",
                      "Chase wide" in labels(shelf) and "scrap" not in labels(shelf),
                      json.dumps(labels(shelf)))

                # ⚠ AN EMPTY NAME IS A CANCEL, NOT A BLANK NAME. A card with no
                # label captions itself "Untitled", so writing "" would look
                # exactly like the rename having failed.
                page.dblclick(f"{sel} .fs-label")
                page.wait_for_timeout(250)
                page.keyboard.press("Control+a")
                page.keyboard.press("Backspace")
                page.keyboard.press("Enter")
                page.wait_for_timeout(400)
                shelf = page.evaluate("() => window.__probe.bin()")
                check("an empty name is a cancel, not a blank card",
                      "Chase wide" in labels(shelf), json.dumps(labels(shelf)))

            # -----------------------------------------------------------------
            # …AND THE SAME CARD HAS A RIGHT-CLICK MENU
            # -----------------------------------------------------------------
            print("\nRight-clicking a card opens its own menu")
            sel = page.evaluate("() => window.__probe.tag('Chase wide', 'menu')")
            check("the renamed card can be found again", bool(sel), "no card labelled Chase wide")
            if sel:
                page.click(sel, button="right")
                page.wait_for_timeout(350)
                menu = page.evaluate("() => window.__probe.menu()")
                check("a menu opens", menu is not None, "no .fs-card-menu appeared")
                texts = [i["text"] for i in (menu or {}).get("items", [])]
                check("…naming the card it is about",
                      bool(menu) and menu["of"] == "Chase wide",
                      json.dumps(menu and menu["of"]))
                for want in ("Rename", "Add to timeline", "Select its",
                             "Properties", "Remove from Media"):
                    check(f"…and it offers {want}",
                          any(t.startswith(want) for t in texts), json.dumps(texts))
                # ⚠ DOWNLOAD IS FOR WHAT THIS APP MADE, AND THIS CARD IS AN
                # ORDINARY UPLOAD — a file already sitting on the user's machine.
                # The gate widened once, from `isVeoRender` to "anything with bytes
                # behind it", so that ✨ Animatic images' key poses could be saved;
                # that put a ⬇ on uploads too and was sent straight back — "only
                # generated cheezon par dikhe ye ⬇ icone". `isSavable` asks for the
                # board reference now, which no upload carries.
                # `tests/veo_download_check.py` pins the rule and the truth table.
                check("…but NOT Download, which only what this app generated offers",
                      not any(t.startswith("Download") for t in texts), json.dumps(texts))
                # ⚠ THE GEOMETRY IS PART OF THE CLAIM: the Media pane scrolls and
                # clips, so a menu that were a child of the card would be cut off
                # at the pane's edge rather than merely mispositioned.
                check("it is pinned to the viewport, so the scrolling pane cannot clip it",
                      bool(menu) and menu["fixed"] and menu["onScreen"], json.dumps(menu))

                # ⚠ A SCROLL MUST NOT CLOSE IT. Focusing the first line makes the
                # pane emit a scroll of its own, so a menu that closed on one shut
                # in the frame it opened — that was a real bug, found here.
                gap = page.evaluate("() => window.__probe.menuGap()")
                check("the pane marks WHICH card the menu belongs to",
                      gap is not None, "no .fs-bin-card.menu-open beside the menu")
                page.evaluate("() => window.__probe.fakeScroll()")
                page.wait_for_timeout(250)
                check("a scroll of the pane does not close it",
                      page.evaluate("() => window.__probe.menu()") is not None,
                      "the menu closed on a scroll event")
                # ⚠ THE WINDOW IS SQUEEZED FIRST, and that is what makes the next
                # check a check at all: four cards do not overflow a 1200px pane,
                # so there is no real scroll to follow and the assertion would
                # pass without ever exercising anything. (A resize closes the menu
                # on purpose — the grid re-flows and no offset can track that — so
                # it is re-opened after.)
                page.set_viewport_size({"width": 1700, "height": 820})
                page.wait_for_timeout(600)
                page.click(sel, button="right")
                page.wait_for_timeout(350)
                gap = page.evaluate("() => window.__probe.menuGap()")
                # Whichever way the pane has room — `page.click` has just scrolled
                # the card into view, so which end it is against is not knowable.
                moved = (page.evaluate("() => window.__probe.scrollMedia(60)")
                         or page.evaluate("() => window.__probe.scrollMedia(-60)"))
                page.wait_for_timeout(300)
                after = page.evaluate("() => window.__probe.menuGap()")
                check("…and a real scroll moves it WITH its card, not away from it",
                      bool(moved) and bool(gap) and bool(after)
                      and abs(after["dx"] - gap["dx"]) <= 2
                      and abs(after["dy"] - gap["dy"]) <= 2,
                      f"moved {moved}px: {gap} -> {after}")
                page.set_viewport_size({"width": 1700, "height": 1200})
                page.wait_for_timeout(600)
                page.click(sel, button="right")
                page.wait_for_timeout(350)

                why = page.evaluate("() => window.__probe.menuClick('Properties')")
                page.wait_for_timeout(300)
                rows = dict((page.evaluate("() => window.__probe.menu()") or {}).get("props", []))
                check("Properties says what the source is",
                      not why and rows.get("Kind") == "Video file", why or json.dumps(rows))
                check("…which section it is listed under",
                      rows.get("Section") == "Video", json.dumps(rows))
                check("…how long the SOURCE runs, not the clip cut from it",
                      rows.get("Length") == "54.4s", json.dumps(rows))
                check("…and how much of the cut uses it",
                      rows.get("In the cut") == "1 clip", json.dumps(rows))
                why = page.evaluate("() => window.__probe.menuClick('Back')")
                page.wait_for_timeout(250)
                back = [i["text"] for i in (page.evaluate("() => window.__probe.menu()") or {}).get("items", [])]
                check("‹ Back returns to the commands",
                      not why and any(t.startswith("Rename") for t in back),
                      why or json.dumps(back))

                # WHERE IS IT IN THE CUT? The question the ×1 badge poses and
                # nothing else could answer.
                page.click(sel, button="right")
                page.wait_for_timeout(300)
                why = page.evaluate("() => window.__probe.menuClick('Select its')")
                page.wait_for_timeout(450)
                check("Select its clips selects them on the timeline",
                      not why and page.evaluate("() => window.__probe.selCount()") == 1,
                      why or str(page.evaluate("() => window.__probe.selCount()")))
                check("…and the menu closed behind it",
                      page.evaluate("() => window.__probe.menu()") is None, "menu stayed open")

                # The menu's Rename is the double-click's own path, not a second one.
                page.click(sel, button="right")
                page.wait_for_timeout(300)
                why = page.evaluate("() => window.__probe.menuClick('Rename')")
                page.wait_for_timeout(350)
                naming = page.evaluate("() => window.__probe.naming()")
                check("the menu's Rename opens the same field the double-click does",
                      not why and naming is not None and naming["value"] == "Chase wide",
                      why or json.dumps(naming))
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)

                page.click(sel, button="right")
                page.wait_for_timeout(300)
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
                check("Escape closes the menu",
                      page.evaluate("() => window.__probe.menu()") is None, "menu stayed open")

            # -----------------------------------------------------------------
            # THE PADLOCK
            # -----------------------------------------------------------------
            print("\n🔒 A locked row refuses every edit, and still plays")
            check("every row has a padlock beside its eye",
                  page.eval_on_selector_all(
                      ".tl-gutter-row", "rows => rows.every(r => r.querySelector('.tl-layer-lock'))"
                  ),
                  "some row has no .tl-layer-lock")
            why = page.evaluate("() => window.__probe.press('Story..Image', '.tl-layer-lock')")
            page.wait_for_timeout(400)
            row = page.evaluate("() => window.__probe.rowByName('Story..Image')")
            check("pressing it locks the row", not why and bool(row) and row["locked"],
                  why or json.dumps(row))
            check("…and the button shows it is on",
                  bool(row) and row["lockPressed"] == "true", json.dumps(row))
            check("…and the editor says what a lock does",
                  "locked" in page.evaluate("() => window.__probe.notice()"),
                  page.evaluate("() => window.__probe.notice()"))

            # A drag off the locked row must move nothing.
            before = page.evaluate("() => window.__probe.bars()")
            page.evaluate("() => window.__probe.drag('[data-sel=\"frame:p1\"]', 'frames:1', 0.6)")
            page.wait_for_timeout(500)
            after = page.evaluate("() => window.__probe.bars()")
            check("a clip on it cannot be dragged to another row",
                  after.get("p1", {}).get("lane") == before.get("p1", {}).get("lane"),
                  f'{before.get("p1")} -> {after.get("p1")}')

            # …and nothing can be dropped ONTO it either.
            sel = page.evaluate("() => window.__probe.cardSel('spare.png')")
            keys_before = set(page.evaluate("() => window.__probe.bars()"))
            page.evaluate("([s, k]) => window.__probe.drag(s, k, 0.5)", [sel, "frames:0"])
            page.wait_for_timeout(500)
            keys_after = set(page.evaluate("() => window.__probe.bars()"))
            check("and a library card cannot be dropped onto it",
                  keys_after == keys_before,
                  f"{sorted(keys_after - keys_before)} appeared")

            # …and Delete leaves it alone.
            #
            # ⚠ THE WHOLE TIMELINE IS COMPARED, NOT JUST `p1`, and that is not
            # belt-and-braces — it is the assertion. Checking only `p1` passed
            # against a real bug: clicking a locked clip did not select it (right)
            # but also did not CLEAR the previous selection (wrong), so Delete
            # removed a clip on a different row while `p1` sat there looking
            # protected. "What did not happen" has to mean the whole timeline.
            before = page.evaluate("() => window.__probe.bars()")
            page.click('[data-sel="frame:p1"]')
            page.wait_for_timeout(200)
            page.keyboard.press("Delete")
            page.wait_for_timeout(500)
            after = page.evaluate("() => window.__probe.bars()")
            check("and Delete does not remove its clips",
                  "p1" in after, "p1 was deleted from a locked row")
            check("…and takes nothing else with it either",
                  sorted(after) == sorted(before),
                  f"gone: {sorted(set(before) - set(after))}")
            row = page.evaluate("() => window.__probe.rowByName('Story..Image')")
            check("its ✕ is disabled while it is locked",
                  bool(row) and row["delDisabled"], json.dumps(row))

            why = page.evaluate("() => window.__probe.press('Story..Image', '.tl-layer-lock')")
            page.wait_for_timeout(400)
            row = page.evaluate("() => window.__probe.rowByName('Story..Image')")
            check("pressing the padlock again unlocks it",
                  not why and bool(row) and not row["locked"], why or json.dumps(row))

            # -----------------------------------------------------------------
            # THE ✕'S CONFIRM
            # -----------------------------------------------------------------
            print("\n✕ on a row asks first, in the same place")
            why = page.evaluate("() => window.__probe.press('Video', '.tl-layer-del')")
            page.wait_for_timeout(300)
            row = page.evaluate("() => window.__probe.rowByName('Video')")
            check("pressing ✕ opens a confirm on that row",
                  not why and bool(row) and row["confirmOpen"], why or json.dumps(row))
            check("…which names the row it is about",
                  bool(row) and "Video" in row["confirmText"], json.dumps(row))
            check("…and counts what would go with it",
                  bool(row) and re.search(r"\d+ clip", row["confirmText"]) is not None,
                  json.dumps(row))

            names_before = page.evaluate("() => window.__probe.rowNames()")
            page.click(".tl-layer-confirm .tl-layer-confirm-btn:not(.danger)")
            page.wait_for_timeout(400)
            check("Cancel closes it and deletes nothing",
                  page.evaluate("() => window.__probe.rowNames()") == names_before
                  and not page.evaluate("() => window.__probe.rowByName('Video')")["confirmOpen"],
                  json.dumps(page.evaluate("() => window.__probe.rowNames()")))

            page.evaluate("() => window.__probe.press('Video', '.tl-layer-del')")
            page.wait_for_timeout(300)
            page.click(".tl-layer-confirm .tl-layer-confirm-btn.danger")
            page.wait_for_timeout(600)
            names_after = page.evaluate("() => window.__probe.rowNames()")
            check("Delete removes the row", "Video" not in names_after, json.dumps(names_after))
            # ⚠ AND THE SOURCES SURVIVE EVEN THAT. Removing a whole row takes its
            # clips; it is still not a reason to lose the files they came from.
            shelf = page.evaluate("() => window.__probe.bin()")
            check("…and its sources are still in Media", len(shelf) == CARDS_TOTAL,
                  json.dumps(labels(shelf)))

            # -----------------------------------------------------------------
            # A PICTURE ON THE IMAGES LANE IS AN OVERLAY — AND THE LIBRARY SEES IT
            # -----------------------------------------------------------------
            # ⚠ THIS IS THE THIRD PICTURE LIST, AND IT WAS INVISIBLE TO ALL THREE
            # OF THE PLACES THAT ASK "who uses this source?". An overlay has no
            # place in `frames`, so the ×N badge read "–" while the picture was
            # plainly on the timeline; the card's ✕ removed the card and left the
            # picture playing from a source no longer listed anywhere — the exact
            # orphan the Media library exists to prevent; and "Select its clips"
            # could not find it. `AnimaticOverlay.src` is what closed all three.
            #
            # ⚠ IT RUNS LAST BECAUSE IT ENDS BY DELETING `spare.png`, which the
            # lock section above still needs as something to try to drop.
            print("\nA picture on the Images lane counts, selects and deletes")
            sel = page.evaluate("() => window.__probe.tag('spare.png', 'ov')")
            check("the unused card is still there to place", bool(sel), "no spare.png card")
            if sel:
                shelf = page.evaluate("() => window.__probe.bin()")
                spare = next((r for r in shelf if r["label"] == "spare.png"), None)
                check("…and it starts used by nothing",
                      bool(spare) and spare["used"] == "–", json.dumps(spare))

                before = page.evaluate("() => window.__probe.overlayBars()")
                page.dblclick(f"{sel} .fs-thumb")
                page.wait_for_timeout(1000)
                after = page.evaluate("() => window.__probe.overlayBars()")
                fresh = [k for k in after if k not in before]
                check("double-clicking it puts a picture on the Images lane",
                      len(fresh) == 1, f"{before} -> {after}")
                shelf = page.evaluate("() => window.__probe.bin()")
                spare = next((r for r in shelf if r["label"] == "spare.png"), None)
                check("…and the card COUNTS it, where it used to read “–”",
                      bool(spare) and spare["used"] == "×1", json.dumps(spare))

                # ⚠ AND THE OVERLAY RECORDS WHICH SOURCE IT IS PLAYING — the one
                # line in `overlayFromFrame` that the pure checks in
                # `asset_fields_check.py` cannot see, because they hand
                # `assetFromOverlay` an overlay that already has a `src`. It
                # matters most for the case this fixture cannot reach: a PANEL
                # dropped on an Images lane is re-uploaded and its `upload_id` is
                # a copy minted on the spot, so `src` is the only thing left that
                # names the card. Read off the SAVE, which is the only place the
                # client's overlay is visible from out here. (AUTOSAVE_MS is 900.)
                page.wait_for_timeout(1800)
                saved_ovs = SAVED.get("overlays") or []
                check("…and the overlay records WHICH source it is playing",
                      len(saved_ovs) == 1
                      and (saved_ovs[0].get("src") or {}).get("upload_id") == ORPHAN_UPLOAD,
                      json.dumps(saved_ovs)[:300])

                page.click(sel, button="right")
                page.wait_for_timeout(350)
                items = {i["text"]: i for i in
                         (page.evaluate("() => window.__probe.menu()") or {}).get("items", [])}
                line = next((t for t in items if t.startswith("Select its")), "")
                check("…and Select its clips is live, not greyed out",
                      line == "Select its 1 clip" and not items[line]["disabled"],
                      json.dumps(sorted(items)))
                page.evaluate("() => window.__probe.menuClick('Select its')")
                page.wait_for_timeout(450)
                check("…and pressing it selects the picture on the Images lane",
                      page.evaluate("() => window.__probe.selOverlayCount()") == 1,
                      str(page.evaluate("() => window.__probe.selOverlayCount()")))

                page.click(sel, button="right")
                page.wait_for_timeout(350)
                why = page.evaluate("() => window.__probe.menuClick('Remove from Media')")
                page.wait_for_timeout(700)
                shelf = page.evaluate("() => window.__probe.bin()")
                check("Remove from Media takes the card",
                      not why and "spare.png" not in labels(shelf),
                      why or json.dumps(labels(shelf)))
                # ⚠ AND THE PICTURE WITH IT. A card removed while the picture it
                # fed goes on playing is unreachable from the UI afterwards: every
                # control that could delete it lives in the pane the card just left.
                check("…and the picture it was feeding, so nothing is orphaned",
                      page.evaluate("() => window.__probe.overlayBars()") == before,
                      json.dumps(page.evaluate("() => window.__probe.overlayBars()")))

            print("\nWhat reached the server")
            check("the library was saved with the project", "assets" in SAVED,
                  f"PUT body had: {sorted(SAVED)}")
            # ⚠ A RENAME MAKES NO REQUEST OF ITS OWN — `assetForSave` carries
            # `label`, so the ordinary autosave is what has to carry it. If this
            # line fails, the name is a thing that survives until reload and no
            # further.
            check("…carrying the name the user typed",
                  any(a.get("label") == "Chase wide" for a in (SAVED.get("assets") or [])),
                  json.dumps([a.get("label") for a in (SAVED.get("assets") or [])]))
            check("…and so was the lock", "settings" in SAVED
                  and "locked_lanes" in (SAVED.get("settings") or {}),
                  json.dumps(SAVED.get("settings"))[:200])

            print("\nAfterwards")
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:500])
            if failures:
                page.screenshot(path=shot("bin_probe_failed.png"))
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
    print(
        "Media keeps the source, a card renames in both lists, a locked row keeps "
        "its edit, and ✕ asks first."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
