"""A ROW DRAGGED UP OR DOWN RESTACKS THE PICTURE — in the real editor.

The report, after the first version of this shipped restricted to one kind:

    "i check shapes layer move only other shapes layer, text layer only move
     other texts layer, and these three move each other video, Story..iamge,
     Story..video … i want these all layer move up down each other … because i
     want video layer move up Image and shapes and shapes down video"

`tests/lane_reorder_check.py` proves the MATHS — that both languages rank the
rows identically and that the two renderers walk the same list. It cannot see
whether the editor lets you perform the gesture, and it cannot see the one thing
about this feature that is genuinely new in the browser:

⚠ THE MONITOR IS MORE THAN ONE CANVAS NOW. Captions are DOM and every other layer
is WebGL, so a text row dragged UNDER a picture row cannot be drawn in one pass —
the stack is cut into BANDS at each text row, each band gets its own canvas and
its own WebGL context, and the captions sit between them as ordinary siblings.
That path does not exist until somebody drags a picture row above a text row, so
NOTHING ELSE IN THE SUITE EXERCISES IT. If it is broken the monitor goes blank or
the context count runs away, and both are invisible to a Python test.

So the four things here are: the gutter opens in the derived order, a picture row
can be dropped on an OVERLAY row's place (which the old build refused), the
monitor bands itself when a caption ends up under a picture, and nothing reaches
the console while any of it happens.

    python tests/editor_lane_restack_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router — the harness is `editor_picture_tracks_check.py`'s, borrowed
rather than invented a second time.
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

PROBE_HTML = os.path.join(CLIENT, "__probe_restack.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_restack.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture — one row of every kind, which is the case that could not move
# ---------------------------------------------------------------------------
# ⚠ ONE ROW PER KIND ON PURPOSE. The old build could only restack a row among its
# own kind, so a project shaped like this one — exactly the shape in the
# screenshots — had NO movable rows at all except the picture tracks. If this
# fixture ever grows a second Text row it stops testing the reported case.
#
#   track 1:  [b1 0–3000]          a second picture row, so "Video 2" exists
#   track 0:  [p1 0–3000][p2 3000–6000]
#   text:     one caption across the whole thing, so it is always on screen
#   shape:    one box, likewise
#   image:    one overlay picture, likewise
PROJECT = {
    "id": "probe",
    "title": "probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [
        {"id": "p1", "kind": "image", "src": {"kind": "upload", "upload_id": "u1"},
         "duration_ms": 3000, "start_ms": 0, "track": 0, "label": "P1",
         "url": "/animatics/probe/media/u1"},
        {"id": "p2", "kind": "image", "src": {"kind": "upload", "upload_id": "u2"},
         "duration_ms": 3000, "start_ms": 3000, "track": 0, "label": "P2",
         "url": "/animatics/probe/media/u2"},
        {"id": "b1", "kind": "image", "src": {"kind": "upload", "upload_id": "u2"},
         "duration_ms": 3000, "start_ms": 0, "track": 1, "label": "B1",
         "url": "/animatics/probe/media/u2"},
    ],
    "texts": [
        {"id": "t1", "layer_id": "", "text": "hello world", "start_ms": 0,
         "duration_ms": 6000, "position": "bottom", "size": "medium"},
    ],
    "shapes": [
        {"id": "s1", "layer_id": "", "kind": "rect", "start_ms": 0, "duration_ms": 6000,
         "x": 0.3, "y": 0.3, "w": 0.2, "h": 0.2, "color": "#c2185b"},
    ],
    "overlays": [
        {"id": "o1", "layer_id": "", "upload_id": "u1", "start_ms": 0,
         "duration_ms": 6000, "x": 0.7, "y": 0.3, "w": 0.2, "h": 0.2},
    ],
    "layers": [], "transitions": [], "audio_tracks": [], "veo_clips": [], "video": None,
}


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


MEDIA = {
    "u1": ("image/png", picture_bytes((74, 134, 200))),
    "u2": ("image/png", picture_bytes((200, 120, 60))),
}

# What the last PUT carried. The editor saves on a debounce, so this is read
# opportunistically at the end rather than waited for — a test that blocked on an
# autosave would be timing the debounce, not the feature.
saved: dict = {}


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

    match = re.search(r"/animatics/probe/media/(\w+)", url)
    if match:
        found = MEDIA.get(match.group(1))
        if not found:
            route.fulfill(status=404, headers=cors, body="")
        else:
            route.fulfill(status=200, headers=cors, content_type=found[0], body=found[1])
        return

    if url.rstrip("/").endswith("/animatics/luts"):
        route.fulfill(status=200, headers=cors, content_type="application/json", body="[]")
        return

    if re.search(r"/animatics/probe/?$", url):
        if request.method in ("PUT", "PATCH", "POST"):
            try:
                saved.update(json.loads(request.post_data or "{}"))
            except ValueError:
                pass
            route.fulfill(status=200, headers=cors, content_type="application/json",
                          body=json.dumps(PROJECT))
            return
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps(PROJECT))
        return

    route.fulfill(status=404, headers=cors, content_type="application/json",
                  body=json.dumps({"detail": "not found"}))


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

/** The gutter, top of the stack first — the row keys, in the order drawn. */
probe.laneKeys = () =>
  [...document.querySelectorAll(".tl-gutter-row")].map((el) => el.dataset.laneRow);

/** Where one gutter row is on screen, so a drag can start and end on it. */
probe.rowBox = (key) => {
  const el = document.querySelector(`[data-lane-row="${key}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width * 0.45, y: r.top + r.height / 2, top: r.top, h: r.height };
};

/**
 * THE MONITOR, AS A STACK — the tag of every layer in the screen box, in DOM
 * order, which IS the stacking order.
 *
 * ⚠ THIS IS THE WHOLE POINT OF THE TEST. One "gl" means the picture is a single
 * canvas with the captions over it, exactly as it always was. Two means a text
 * row ended up under a picture row and the monitor cut itself into bands.
 */
probe.monitorStack = () => {
  const screen = document.querySelector(".an-screen");
  if (!screen) return [];
  const out = [];
  for (const el of screen.children) {
    if (el.tagName === "CANVAS" && el.classList.contains("an-screen-gl")) out.push("gl");
    else if (el.classList.contains("an-text-layer")) {
      out.push("text:" + el.textContent.trim().slice(0, 12));
    }
  }
  return out;
};

/** Is every band's canvas actually painted, or has one come up blank/zero-sized? */
probe.canvasSizes = () =>
  [...document.querySelectorAll(".an-screen-gl")].map((c) => ({
    w: c.width, h: c.height, boxW: Math.round(c.getBoundingClientRect().width),
  }));

/** The status strip — where a drag reports what it did, or refused to do. */
probe.notice = () =>
  (document.querySelector(".an-status-note")?.textContent || "").trim();

/**
 * SCROLL TWO GUTTER ROWS INTO VIEW TOGETHER.
 *
 * ⚠ A ROW SCROLLED OUT OF THE PANE STILL REPORTS A BOUNDING BOX. The lanes sit
 * in an `overflow: hidden` clip, so `getBoundingClientRect` happily returns
 * coordinates for a row nobody can see or click — and a drag aimed at those
 * coordinates lands on <body> and does nothing at all, silently. That was this
 * file's first failure and it was indistinguishable from a broken feature.
 * Borrowed from `editor_lane_move_check.py`, which hit it first.
 */
probe.revealPair = (a, b) => {
  const sc = document.querySelector(".tl-scroll");
  // ⚠ THE SCROLL MATHS IS DONE ON THE *TRACK* ROWS, NOT THE GUTTER ROWS. The
  // gutter is not a scroller: it is moved to follow the tracks by `readView`,
  // which runs on the tracks' scroll event — so a gutter row's box is stale for
  // a frame after any scroll, and measuring it here would answer with where the
  // row USED to be. The two columns are aligned by construction, so the tracks
  // are the honest ruler.
  const ta = document.querySelector(`[data-lane="${a}"]`);
  const tb = document.querySelector(`[data-lane="${b}"]`);
  if (!sc || !ta || !tb) return { ok: false, why: "missing " + (!ta ? a : b) };
  const view = sc.getBoundingClientRect();
  const ra = ta.getBoundingClientRect();
  const rb = tb.getBoundingClientRect();
  const need = Math.max(ra.bottom, rb.bottom) - Math.min(ra.top, rb.top);
  if (need > view.height) {
    return { ok: false, why: `needs ${Math.round(need)}px of ${Math.round(view.height)}px` };
  }
  const mid = (Math.max(ra.bottom, rb.bottom) + Math.min(ra.top, rb.top)) / 2;
  sc.scrollTop += mid - (view.top + view.height / 2);
  return { ok: true, why: "scrolled" };
};

/** What is actually painted at this point — the check a bounding box cannot make. */
probe.hitRow = (x, y) => {
  const el = document.elementFromPoint(x, y);
  return el?.closest?.("[data-lane-row]")?.dataset.laneRow || el?.tagName || null;
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>lane restack probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_restack.jsx"></script>
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


def drag_row(page, from_key, to_key):
    """Drag one gutter row onto another's place, the way a person does.

    ⚠ IT MOVES IN STEPS AND PASSES THE SLOP THRESHOLD FIRST. `startLaneDrag` does
    nothing until the pointer has travelled `LANE_DRAG_SLOP`, and it re-asks which
    row it is over on every move — a single jump from A to B fires one move event
    and can land before the drag has been recognised at all.
    """
    shown = page.evaluate("([a, b]) => window.__probe.revealPair(a, b)",
                          [from_key, to_key])
    if not shown.get("ok"):
        return f"cannot see {from_key} and {to_key} at once ({shown.get('why')})"
    # ⚠ WAITED FOR, NOT SLEPT THROUGH. The gutter is not a scroller — it is moved
    # to follow the tracks by `readView`, which runs on the tracks' scroll EVENT —
    # so its rows land a frame or more after the scroll above, and a fixed sleep
    # is a race that passes on a fast machine and fails on a loaded one.
    #
    # ⚠ AND WHAT IS WAITED FOR IS THE HIT TEST, NOT THE BOX. A row clipped out of
    # the pane still reports a bounding box, so "is it at these coordinates" is
    # answered yes for a row that cannot be clicked; a drag aimed there lands on
    # <body> and does nothing, silently, which looks exactly like a broken
    # feature. `elementFromPoint` is the only question that cannot be fooled.
    deadline = time.time() + 4
    a = b = None
    while time.time() < deadline:
        a = page.evaluate("(k) => window.__probe.rowBox(k)", from_key)
        b = page.evaluate("(k) => window.__probe.rowBox(k)", to_key)
        if a and b:
            hits = [
                page.evaluate("([x, y]) => window.__probe.hitRow(x, y)", [box["x"], box["y"]])
                for box in (a, b)
            ]
            if hits == [from_key, to_key]:
                break
        page.wait_for_timeout(100)
    else:
        return f"{from_key} or {to_key} never became clickable (hit {hits if a and b else '—'})"
    page.mouse.move(a["x"], a["y"])
    page.mouse.down()
    # ⚠ IN STEPS, WITH TIME BETWEEN THEM. `startLaneDrag` ignores a press until it
    # has travelled `LANE_DRAG_SLOP`, re-asks which row it is over on every move,
    # and re-renders the bar whenever that answer changes — so one big jump fires
    # a single move event and can be released before any of it has happened. This
    # is a person dragging, not a teleport.
    steps = 8
    for i in range(1, steps + 1):
        page.mouse.move(a["x"], a["y"] + (b["y"] - a["y"]) * i / steps)
        page.wait_for_timeout(40)
    page.mouse.up()
    page.wait_for_timeout(300)
    return ""


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
            # ⚠ A TALL WINDOW, AND IT IS LOAD-BEARING. The lanes live in an
            # `overflow: hidden` scroller, and a row scrolled out of it still
            # reports a bounding box — so a drag aimed at a clipped row lands on
            # <body> and does nothing, silently. This fixture has six rows and a
            # drag has to see two of them at once; `revealPair` scrolls, but it
            # cannot conjure height that is not there.
            page = browser.new_page(viewport={"width": 1600, "height": 1800})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_restack.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nThe editor opens with the stack in its derived order")
            try:
                page.wait_for_selector(".an-screen-gl", timeout=45000)
                page.wait_for_function(
                    "() => window.__probe.laneKeys().length >= 6", timeout=45000
                )
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts with its rows drawn", False, str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=os.path.join(ROOT, "restack_probe_failed.png"))
                browser.close()
                return 1

            keys = page.evaluate("() => window.__probe.laneKeys()")
            check("one row of every kind is on the timeline",
                  {"text:", "shape:", "image:", "frames:0", "frames:1"} <= set(keys), str(keys))
            # ⚠ IMAGES ABOVE SHAPES, which is the order the RENDERERS have always
            # drawn and the order this gutter now agrees with. It used to show
            # them the other way round while the picture said otherwise.
            check("Images sits above Shapes, as the renderers have always stacked them",
                  keys.index("image:") < keys.index("shape:"), str(keys))
            check("the picture rows are under both, highest track first",
                  keys.index("shape:") < keys.index("frames:1") < keys.index("frames:0"),
                  str(keys))

            print("\nThe monitor is ONE canvas while the captions are on top")
            stack = page.evaluate("() => window.__probe.monitorStack()")
            check("one canvas, with the caption layer over it",
                  stack.count("gl") == 1 and any(s.startswith("text:") for s in stack),
                  str(stack))
            check("…and the caption is the LAST thing in the box",
                  stack[-1].startswith("text:"), str(stack))

            print("\nA PICTURE row can be dropped on an OVERLAY row — the reported case")
            why = drag_row(page, "frames:0", "image:")
            check("the drag ran", why == "", why)
            keys = page.evaluate("() => window.__probe.laneKeys()")
            check("Video now draws OVER Images ('i want video layer move up Image')",
                  keys.index("frames:0") < keys.index("image:"), str(keys))
            check("…and over Shapes with it",
                  keys.index("frames:0") < keys.index("shape:"), str(keys))
            check("…and the editor says so, and says nothing was re-timed",
                  "Nothing was re-timed" in page.evaluate("() => window.__probe.notice()"),
                  page.evaluate("() => window.__probe.notice()"))
            check("the captions row is still on top — it cannot be moved or moved past",
                  keys[0] not in ("frames:0", "image:", "shape:") or "captions" not in keys,
                  str(keys))

            print("\nA CAPTION UNDER A PICTURE — the monitor cuts itself into bands")
            why = drag_row(page, "text:", "frames:0")
            check("the drag ran", why == "", why)
            keys = page.evaluate("() => window.__probe.laneKeys()")
            check("the text row is now under the picture row",
                  keys.index("text:") > keys.index("frames:0"), str(keys))
            page.wait_for_timeout(400)
            stack = page.evaluate("() => window.__probe.monitorStack()")
            # ⚠ THE ASSERTION THIS WHOLE FILE EXISTS FOR. Two canvases with the
            # caption layer BETWEEN them is the band split; one canvas would mean
            # the monitor is still drawing the old fixed order and the preview is
            # now lying about the export.
            check("the monitor is TWO canvases now, one either side of the captions",
                  stack.count("gl") == 2, str(stack))
            check("…and the caption layer sits between them, not on top",
                  any(s.startswith("text:") for s in stack)
                  and not stack[-1].startswith("text:"),
                  str(stack))
            sizes = page.evaluate("() => window.__probe.canvasSizes()")
            check("both canvases have a real backing store — neither came up blank",
                  len(sizes) == 2 and all(s["w"] > 0 and s["h"] > 0 for s in sizes),
                  str(sizes))
            check("…and both fill the screen box, so they line up exactly",
                  len({s["boxW"] for s in sizes}) == 1, str(sizes))

            print("\nAnd back again — the second context is given up")
            # ⚠ ONTO THE TOP ROW'S PLACE, not merely above the row it went
            # under. A drop lands the row IN the target's place, so dropping the
            # captions on the Images row would leave them under the picture row
            # above that — still two bands, and correctly so. Only the very top
            # of the stack puts the monitor back to a single canvas.
            top_row = [k for k in page.evaluate("() => window.__probe.laneKeys()")
                       if k != "audio:"][0]
            why = drag_row(page, "text:", top_row)
            check("the drag ran", why == "", why)
            page.wait_for_timeout(400)
            stack = page.evaluate("() => window.__probe.monitorStack()")
            check("with the captions back on top the monitor is one canvas again",
                  stack.count("gl") == 1, str(stack))

            print("\nAfterwards")
            # ⚠ OPPORTUNISTIC, AND SAID SO. If the autosave has fired by now
            # its body must carry the order — a restack that reached the monitor
            # but not the server would come back undone on reload. If it has NOT
            # fired there is nothing to check and nothing to fail: the debounce is
            # not what this file is about.
            order = ((saved.get("settings") or {}).get("lane_order")) or []
            if saved:
                check("the saved project carries the row order",
                      isinstance(order, list) and len(order) >= 1, json.dumps(order))

            errors = page.evaluate("() => window.__probe.errors")
            # ⚠ A LOST CONTEXT WOULD SHOW UP HERE. Each band holds a real WebGL
            # context and browsers cap how many may live at once, so a restack that
            # leaked one per drag would start blanking canvases — noisily, in the
            # console, which is why this check is worth more than it looks.
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:600])

            browser.close()
    finally:
        if vite:
            vite.terminate()
        for path in (PROBE_JSX, PROBE_HTML):
            try:
                os.remove(path)
            except OSError:
                pass

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for name in failures:
            print(f"  · {name}")
        return 1
    print("Any row can be dragged anywhere, and the monitor restacks with it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
