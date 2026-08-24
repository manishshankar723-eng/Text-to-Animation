"""A CARD GOES BACK ON THE ROW IT CAME FROM, AND THE GUTTER SAYS WHICH ROW THAT IS.

The report, in one gesture:

    "see when i generate storyborad a image to video so video come in Storyborad
     video layer but then i delete veo video clip in timeline so then next i do
     media panel and then i select Veo video clip and drang and drop on same
     storyboard video layer but i can't drop in Storyboad layer but i drop in
     Video layer this is happng now

     i want one thing when one time come clip in media penal so i drop and drag
     in particuler layer like veo vidio go in Storyboerd in layer any time

     … you change name Storyborad video to Story..Video and Storyborad Image to
     Story..Image … i see my storyborad namke come and show in layer but this not
     happen i want you keep Story..Image … and if anme show not proper so you
     increase layer width like all fit ans show look good"

Two bugs and one rename, checked together because they are one row: the row a
card belongs on, and the label that says so.

And the follow-up report, from the same screenshots, which is the same row again:

    "when i delete Story..Video 2 layer that time dropdown msg appair in below so
     my layer buttun goes up and my time clip layer still so this look not good so
     i want you show dropdown like layer of side like in clip layer side not below.

     second you look image 3 when i delete layer. so only delete layer not clip and
     i want delete clip too

     third keep add Video veo video clip color like pestal prupel"

⚠ THE GUTTER'S DRIFT IS THE ASSERTION FOR THE FIRST OF THOSE, not where the
popover is. The labels are kept level with the tracks by a TRANSFORM, so the bug
was that opening the confirm scrolled their `overflow: hidden` box to reveal it —
every name moved up, every track stood still. `probe.drift` reads one number per
row and the check is that not one of them changed.

---------------------------------------------------------------------------
⚠ WHY THE DROP WAS REFUSED, AND WHY IT LANDED ON *VIDEO* INSTEAD
---------------------------------------------------------------------------
`ROW_TAKES` is "what may be UPLOADED here", and both storyboard rows answer
"nothing" ON PURPOSE — they are filled by the import and by ✨ Animate, not by
dropping files on them. That table was also being asked about a LIBRARY CARD, so
the one drag with every right to land on the Storyboard video row — a Veo render
being put back after its clip was deleted — was turned away, while plain Video
accepted it, because a render is genuinely a video. `cardRowKind` (scene.js) is
the rule now, for the drag AND the drop AND ＋, and a board card has exactly one
row it can land on.

⚠ THE REFUSALS ARE THE ASSERTIONS, and a refusal looks exactly like a drag that
missed — so every one of them is read as "the timeline is unchanged" AND as the
row's own mid-drag answer (`.drop-no` under the pointer, `.drop-ok` on the row
that would take it). A drop that silently lands nowhere would pass the first check
on its own.

⚠ AND THE FIXTURE'S ROWS CARRY THEIR OLD NAMES — "TTBB EP One" (the board title
the import used to write) and "Storyboard video" (the long label). That is the
migration under test: there is no rename in the UI, so neither name is anything
the user typed, and the gutter shows the canonical short one instead.

    python tests/editor_media_row_routing_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router — the harness is `editor_media_bin_check.py`'s.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_rows.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_rows.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture — the state the report describes, exactly
# ---------------------------------------------------------------------------
# THREE ROWS: the board's panels, the row its renders go on (EMPTY — the Veo clip
# has been deleted, which is where the report starts), and a plain video row.
#
# THREE CARDS: the panel, the RENDER OF that panel (a board card: `storyboard_id`
# is kept under the video `src`, see `attachVeoClip`), and a piece of footage that
# came off the disk. The middle one is the whole report — a source in the library
# with no clip on the timeline — and the third is what makes the test mean
# something: "video" cannot be the answer to "which row", because two cards here
# are video and they belong on different rows.
BOARD_ID = "board1"
VEO_UPLOAD = "uveo00000001"
FILE_UPLOAD = "ufile0000001"

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
    ],
    # ⚠ THE STORED NAMES ARE THE OLD ONES, on purpose — see the note at the top.
    "layers": [
        {"id": "L0", "kind": "board_image", "name": "TTBB EP One", "track": 0},
        {"id": "L1", "kind": "board_video", "name": "Storyboard video", "track": 1},
        {"id": "L2", "kind": "video", "name": "Video", "track": 2},
    ],
    "assets": [
        {"id": "a1", "kind": "image",
         "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": 0},
         "upload_id": "", "label": "Shot 1", "duration_ms": 2000, "color": "#000000",
         "url": f"/animatics/probe/panel/{BOARD_ID}/0?v=1"},
        # THE VEO RENDER: a video whose source is still a board panel.
        {"id": "a2", "kind": "video",
         "src": {"kind": "video", "upload_id": VEO_UPLOAD,
                 "storyboard_id": BOARD_ID, "index": 0},
         "upload_id": "", "label": "Shot 1 animated", "duration_ms": 4000,
         "color": "#000000",
         "url": f"/animatics/probe/media/{VEO_UPLOAD}?poster=1"},
        # A FILE: video too, and it belongs on the other row.
        {"id": "a3", "kind": "video",
         "src": {"kind": "video", "upload_id": FILE_UPLOAD},
         "upload_id": "", "label": "footage.mp4", "duration_ms": 6000,
         "color": "#000000",
         "url": f"/animatics/probe/media/{FILE_UPLOAD}?poster=1"},
    ],
    "texts": [], "shapes": [], "overlays": [], "transitions": [],
    "audio_tracks": [], "veo_clips": [], "video": None,
}

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

/** Which row each clip is drawn on, by frame id. */
probe.bars = () => {
  const out = {};
  for (const el of document.querySelectorAll('[data-sel^="frame:"]')) {
    const lane = el.closest("[data-lane]");
    out[el.dataset.sel.slice("frame:".length)] = lane ? lane.dataset.lane : "no-lane";
  }
  return out;
};

/** What the gutter calls each row, top to bottom. */
probe.laneNames = () =>
  Array.from(document.querySelectorAll(".tl-layer-name")).map((n) =>
    (n.textContent || "").trim()
  );

/**
 * WHICH NAMES ARE CUT OFF BY THE COLUMN.
 *
 * ⚠ MEASURED, NOT EYEBALLED. `.tl-layer-name` is `text-overflow: ellipsis`, so a
 * name too long for the gutter still READS as a name — it just stops being the
 * one you can tell apart from the row above. `scrollWidth > clientWidth` is the
 * browser saying so, and 1px of slack keeps sub-pixel layout out of it.
 */
probe.clipped = () =>
  Array.from(document.querySelectorAll(".tl-layer-name"))
    .filter((n) => n.scrollWidth > n.clientWidth + 1)
    .map((n) => (n.textContent || "").trim());

probe.notice = () => {
  const el = document.querySelector(".an-status-note");
  return el ? el.textContent : "";
};

/** Which content colour a bar is wearing — `is-veo` / `is-video` / `is-still`. */
probe.barKind = (id) => {
  const el = document.querySelector(`[data-sel="frame:${id}"]`);
  if (!el) return "";
  return ["is-veo", "is-video", "is-still"].find((c) => el.classList.contains(c)) || "none";
};

/**
 * IS EVERY LABEL STILL BESIDE ITS TRACK? — one number per row.
 *
 * ⚠ THE GUTTER IS KEPT IN STEP BY A TRANSFORM, NOT BY SCROLLING, so anything that
 * scrolls the labels' `overflow: hidden` box is pure drift: the names move and the
 * tracks do not. That is what the ✕'s confirm did when it opened below the row and
 * its Delete button took focus, and it is invisible to any check that only looks
 * at what the popover says.
 */
probe.drift = () => {
  const out = {};
  for (const row of document.querySelectorAll("[data-lane-row]")) {
    const key = row.dataset.laneRow;
    const lane = document.querySelector(`.tl-lane[data-lane="${key}"]`);
    if (!lane) continue;
    out[key] = Math.round(
      row.getBoundingClientRect().top - lane.getBoundingClientRect().top
    );
  }
  return out;
};

/**
 * Press one gutter row's control, found by the name shown in it.
 *
 * ⚠ THE ROW IS SCROLLED INTO VIEW FIRST, and that is not a nicety. `btn.click()`
 * reaches a button on a row that has been scrolled off the bottom of the pane —
 * something no hand can do — and the ✕'s confirm is then CLAMPED inside the pane
 * (deliberately: "a confirm about the top row must not open half way off the top
 * of the timeline"), which puts it nowhere near a row that is not on screen. That
 * failed the "level with the row it is about" check for a situation the UI cannot
 * produce. Revealing the row first asks the question a user would ask.
 */
probe.press = (rowName, cls) => {
  const rows = Array.from(document.querySelectorAll(".tl-gutter-row"));
  const row = rows.find(
    (r) => ((r.querySelector(".tl-layer-name") || {}).textContent || "").trim() === rowName
  );
  if (!row) return "no row " + rowName;
  // The gutter is dragged along by a transform when the LANES scroll, so the
  // thing to scroll is the lane, and the label follows it.
  const key = row.dataset.laneRow;
  const lane = key ? document.querySelector(`[data-lane="${key}"]`) : null;
  const sc = document.querySelector(".tl-scroll");
  if (lane && sc) {
    const view = sc.getBoundingClientRect();
    const box = lane.getBoundingClientRect();
    const mid = box.top + box.height / 2;
    sc.scrollTop += mid - (view.top + view.height / 2);
  }
  const btn = row.querySelector(cls);
  if (!btn) return "no " + cls;
  if (btn.disabled) return cls + " is disabled";
  btn.click();
  return "";
};

/** WHERE THE CONFIRM OPENED, relative to the row it is about and to the gutter. */
probe.confirmBox = () => {
  const el = document.querySelector(".tl-layer-confirm");
  if (!el) return null;
  const row = document.querySelector(`[data-lane-row="${el.dataset.confirm}"]`);
  const gutter = document.querySelector(".tl-gutter");
  if (!row || !gutter) return { why: "no row or gutter for " + el.dataset.confirm };
  const b = el.getBoundingClientRect();
  const r = row.getBoundingClientRect();
  const g = gutter.getBoundingClientRect();
  return {
    why: "",
    key: el.dataset.confirm,
    text: (el.textContent || "").trim(),
    // Past the label column altogether — over the clips, which is where it was
    // asked to go ("like in clip layer side not below").
    beside: b.left >= g.right - 1,
    // Still level with the row it is about, so the two read as one thing.
    level: b.top < r.bottom && b.bottom > r.top,
    below: b.top >= r.bottom - 1,
    // Inside the timeline, not hanging off the bottom of the pane.
    inPane: b.bottom <= document.querySelector(".tl-cols").getBoundingClientRect().bottom + 1,
  };
};

/** The library card whose label is `label`, as a selector. */
probe.cardSel = (label) => {
  const cards = Array.from(document.querySelectorAll(".fs-bin-card"));
  const i = cards.findIndex(
    (c) => (c.querySelector(".fs-label") || {}).textContent === label
  );
  if (i < 0) return "";
  cards.forEach((c) => c.removeAttribute("data-probe"));
  cards[i].setAttribute("data-probe", "card");
  return '[data-probe="card"]';
};

/**
 * A DRAG, DISPATCHED BY HAND, IN THREE STEPS.
 *
 * Playwright's mouse does not start an HTML5 drag in a headless Chromium, so the
 * events are fired over one shared `DataTransfer` — the trick `dragKind` and
 * `dragFromBoard` need, since the marker types are all a lane can read during
 * `dragover`. Borrowed from `editor_media_bin_check.py` rather than reinvented.
 *
 * ⚠ AND IT IS THREE CALLS RATHER THAN ONE, because the row's mid-drag answer is
 * REACT STATE (`dropAt`). A drag event is continuous priority, so the class is not
 * on the lane by the time `dispatchEvent` returns — reading it in the same
 * function would test the render before last. `begin` leaves the drag hanging
 * (nothing clears `dropAt` but a leave or a drop), the test waits, `lit` reads,
 * `finish` drops.
 */
probe.begin = (fromSel, laneKey, xFrac) => {
  const source = document.querySelector(fromSel);
  const target = document.querySelector(`.tl-lane[data-lane="${laneKey}"]`);
  if (!source) return "no source " + fromSel;
  if (!target) return "no lane " + laneKey;
  const dt = new DataTransfer();
  const box = target.getBoundingClientRect();
  const at = {
    clientX: box.left + box.width * (xFrac === undefined ? 0.33 : xFrac),
    clientY: box.top + box.height / 2,
  };
  const fire = (el, type, extra) =>
    el.dispatchEvent(new DragEvent(type, {
      bubbles: true, cancelable: true, dataTransfer: dt, ...extra,
    }));
  probe._drag = { source, target, at, fire };
  fire(source, "dragstart", {});
  fire(target, "dragenter", at);
  fire(target, "dragover", at);
  return "";
};

/** What the row under the pointer is saying, and which rows are lit. */
probe.lit = () => {
  const d = probe._drag;
  if (!d) return { why: "no drag in flight" };
  return {
    why: "",
    ok: d.target.classList.contains("drop-ok"),
    no: d.target.classList.contains("drop-no"),
    // Every row that would take this drag. `dropAt` holds one key at a time, so
    // this is the row under the pointer — it is here to name it when the wrong
    // one lights up.
    lit: Array.from(document.querySelectorAll(".tl-lane.drop-ok")).map(
      (n) => n.dataset.lane
    ),
  };
};

/** End the gesture — with the drop, or by walking away from it. */
probe.finish = (drop) => {
  const d = probe._drag;
  if (!d) return "no drag in flight";
  if (drop) d.fire(d.target, "drop", d.at);
  else d.fire(d.target, "dragleave", { relatedTarget: document.body });
  d.fire(d.source, "dragend", {});
  probe._drag = null;
  return "";
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>row routing probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_rows.jsx"></script>
</body></html>
"""


def hover_over(page, card, lane, x_frac=0.33):
    """Hold a card over a row and read that row's answer, then walk away.

    ⚠ THE WAIT IS THE POINT. `dropAt` is React state set from a drag event, which
    is continuous priority — the class is not on the lane yet when `begin`
    returns.
    """
    why = page.evaluate("([s, k, x]) => window.__probe.begin(s, k, x)", [card, lane, x_frac])
    if why:
        return {"why": why}
    page.wait_for_timeout(250)
    answer = page.evaluate("() => window.__probe.lit()")
    page.evaluate("() => window.__probe.finish(false)")
    page.wait_for_timeout(150)
    return answer


def drop_on(page, card, lane, x_frac=0.33):
    """The whole gesture, finished — and time for the edit to land."""
    why = page.evaluate("([s, k, x]) => window.__probe.begin(s, k, x)", [card, lane, x_frac])
    if why:
        return why
    page.wait_for_timeout(150)
    page.evaluate("() => window.__probe.finish(true)")
    page.wait_for_timeout(700)
    return ""


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
            page.goto(f"http://127.0.0.1:{port}/__probe_rows.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            try:
                page.wait_for_selector("canvas", timeout=45000)
                page.wait_for_function(
                    "() => document.querySelectorAll('.fs-bin-card').length >= 3",
                    timeout=45000,
                )
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts with the library drawn", False, str(exc)[:200])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=os.path.join(ROOT, "row_routing_failed.png"))
                browser.close()
                return 1
            page.wait_for_timeout(1500)

            # -----------------------------------------------------------------
            # THE LABELS
            # -----------------------------------------------------------------
            print("\nThe gutter names a row after its KIND, short enough to read")
            names = page.evaluate("() => window.__probe.laneNames()")
            check("the storyboard's picture row reads “Story..Image”",
                  "Story..Image" in names, str(names))
            check("its render row reads “Story..Video”",
                  "Story..Video" in names, str(names))
            # ⚠ THE BOARD TITLE IS THE BUG, NOT AN ALTERNATIVE. A row called after
            # the storyboard says nothing about which of the four kinds it is.
            check("…and neither is called after the storyboard",
                  not any("TTBB" in n for n in names), str(names))
            check("…nor after the long label a saved project still carries",
                  not any(n.startswith("Storyboard") for n in names), str(names))
            clipped = page.evaluate("() => window.__probe.clipped()")
            check("every row name fits its column, uncut",
                  clipped == [], f"cut off: {clipped}")

            # -----------------------------------------------------------------
            # THE REPORT: the Veo card goes back where it came from
            # -----------------------------------------------------------------
            print("\nA Veo render out of Media lands on Story..Video, and nowhere else")
            veo = page.evaluate("() => window.__probe.cardSel('Shot 1 animated')")
            check("the render is still in Media with no clip on the timeline",
                  bool(veo) and page.evaluate("() => window.__probe.bars()") == {"p1": "frames:0"},
                  veo or json.dumps(page.evaluate("() => window.__probe.bars()")))

            # …over the plain Video row: refused, and it says so mid-drag.
            hover = hover_over(page, veo, "frames:2")
            check("hovering it over Video refuses, before any drop",
                  not hover.get("why") and hover.get("no") and not hover.get("ok"),
                  json.dumps(hover))
            before = page.evaluate("() => window.__probe.bars()")
            drop_on(page, veo, "frames:2")
            after = page.evaluate("() => window.__probe.bars()")
            check("…and dropping it there makes no clip at all",
                  after == before, f"{before} -> {after}")

            # …over its own row: accepted, and it lights up.
            hover = hover_over(page, veo, "frames:1")
            check("hovering it over Story..Video lights that row up",
                  not hover.get("why") and hover.get("ok") and not hover.get("no"),
                  json.dumps(hover))
            drop_on(page, veo, "frames:1", 0.4)
            after = page.evaluate("() => window.__probe.bars()")
            fresh = [k for k in after if k not in before]
            check("dropping it there DOES make a clip",
                  len(fresh) == 1, json.dumps(after))
            check("…on the storyboard's own render row",
                  bool(fresh) and after[fresh[0]] == "frames:1", json.dumps(after))
            # ⚠ AND IT IS PURPLE. A render is a board clip AND a video, so the
            # is-video/is-still pair drew it the same pink as the panel it was made
            # from — the only clip on this timeline that cost money, wearing the
            # colour of the one that did not.
            veo_kind = page.evaluate("(id) => window.__probe.barKind(id)", fresh[0]) if fresh else ""
            check("…and the render's bar wears the Veo purple",
                  veo_kind == "is-veo", f"bar class: {veo_kind}")
            # ⚠ A COPY: the card stays. A library is not a place clips are moved
            # out of — see `editor_media_bin_check.py`.
            check("…and the card is still in Media",
                  bool(page.evaluate("() => window.__probe.cardSel('Shot 1 animated')")),
                  "the card left the library")

            # -----------------------------------------------------------------
            # …AND "VIDEO" IS NOT THE ANSWER TO "WHICH ROW"
            # -----------------------------------------------------------------
            # Two cards here are video. If the rule were the KIND, this pair could
            # not be told apart — which is exactly the bug: the render went to the
            # row the FILE belongs on.
            print("\nA file is video too, and it belongs on the other row")
            shot = page.evaluate("() => window.__probe.cardSel('footage.mp4')")
            hover = hover_over(page, shot, "frames:1")
            check("footage is refused by Story..Video",
                  not hover.get("why") and hover.get("no"), json.dumps(hover))
            before = page.evaluate("() => window.__probe.bars()")
            drop_on(page, shot, "frames:2", 0.6)
            after = page.evaluate("() => window.__probe.bars()")
            fresh = [k for k in after if k not in before]
            check("…and accepted by Video",
                  len(fresh) == 1 and after[fresh[0]] == "frames:2", json.dumps(after))

            print("\nAnd a panel is still a panel")
            panel = page.evaluate("() => window.__probe.cardSel('Shot 1')")
            hover = hover_over(page, panel, "frames:2")
            check("a board panel is refused by Video",
                  not hover.get("why") and hover.get("no"), json.dumps(hover))
            before = page.evaluate("() => window.__probe.bars()")
            drop_on(page, panel, "frames:0", 0.75)
            after = page.evaluate("() => window.__probe.bars()")
            fresh = [k for k in after if k not in before]
            check("…and accepted by Story..Image",
                  len(fresh) == 1 and after[fresh[0]] == "frames:0", json.dumps(after))

            # -----------------------------------------------------------------
            # THE ✕'S CONFIRM: BESIDE THE ROW, AND IT TAKES THE CLIP WITH IT
            # -----------------------------------------------------------------
            print("\n✕ asks beside the row, and answering it takes the clip too")
            drift_before = page.evaluate("() => window.__probe.drift()")
            why = page.evaluate("() => window.__probe.press('Story..Video', '.tl-layer-del')")
            page.wait_for_timeout(400)
            box = page.evaluate("() => window.__probe.confirmBox()")
            check("pressing ✕ opens the confirm", not why and bool(box) and not box.get("why"),
                  why or json.dumps(box))
            check("…beside the row, over the clips — not under it",
                  bool(box) and box.get("beside") and not box.get("below"), json.dumps(box))
            check("…level with the row it is about",
                  bool(box) and box.get("level"), json.dumps(box))
            check("…and inside the pane rather than hanging off the bottom",
                  bool(box) and box.get("inPane"), json.dumps(box))
            # ⚠ THE REAL COMPLAINT: opening it moved the LABELS and not the tracks.
            # Focus on the Delete button scrolled the gutter's hidden box.
            drift_after = page.evaluate("() => window.__probe.drift()")
            check("…and every label is still beside its own track",
                  drift_after == drift_before, f"{drift_before} -> {drift_after}")
            check("the confirm still says what goes with the row",
                  bool(box) and "1 clip" in box.get("text", ""), json.dumps(box))

            before = page.evaluate("() => window.__probe.bars()")
            veo_clip = next((k for k, lane in before.items() if lane == "frames:1"), None)
            page.click(".tl-layer-confirm .tl-layer-confirm-btn.danger")
            page.wait_for_timeout(700)
            names = page.evaluate("() => window.__probe.laneNames()")
            after = page.evaluate("() => window.__probe.bars()")
            check("Delete removes the row", "Story..Video" not in names, str(names))
            # ⚠ THE PROMISE THE CONFIRM HAS ALWAYS MADE. The clip used to drop to
            # track 0 instead, so it reappeared on a row it was never put on —
            # "only delete layer not clip and i want delete clip too".
            check("…and its clip goes with it, rather than dropping to another row",
                  bool(veo_clip) and veo_clip not in after,
                  f"{veo_clip} is now on {after.get(veo_clip)}")
            check("…and nothing on the other rows moved",
                  {k: v for k, v in after.items() if k != veo_clip}
                  == {k: v for k, v in before.items() if k != veo_clip},
                  f"{before} -> {after}")
            # The source survives the row — that is what makes deleting the clip a
            # safe thing to do at all (see `assets.js`).
            check("…and the render is still a card in Media",
                  bool(page.evaluate("() => window.__probe.cardSel('Shot 1 animated')")),
                  "the card left the library")

            print("\nAfterwards")
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:500])
            if failures:
                page.screenshot(path=os.path.join(ROOT, "row_routing_failed.png"))
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
        "A card lands on its own row, the gutter says which row that is, and the ✕ "
        "asks beside it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
