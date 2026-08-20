"""TRIMMING ONE PICTURE MOVES NO OTHER PICTURE — in the real editor.

The report:

    "second i see when i do video trim so i see my image layer conetnt move like
     snip and same with image when i trim image so my video layer content move.
     i want user move independaly each asstes/conetnt in layer"

It was true by construction. The picture was ONE sequence laid end to end — a
clip's place was the sum of the clips before it — and the two picture rows were
that same sequence drawn twice, filtered by where each clip came from. So changing
any clip's length moved every clip after it, on both rows. A picture carries
`track` and `start_ms` now, which is what makes the rows real; this file is the
GESTURE half of that, and there is no other way to check it.

`tests/render_parity.py` proves the two evaluators agree about a stack.
`tests/picture_tracks_check.py` proves the exporter composites and encodes one.
Neither can see whether the timeline lets you BUILD one: that a trim leaves its
neighbours alone, that B still ripples and N still rolls when you want them to,
and that a clip can be dragged onto the track above. Those are the four things
here, and every one of them is a mouse drag against the real `<AnimaticEditor>`.

---------------------------------------------------------------------------
⚠ EVERY ASSERTION IS "WHAT MOVED", NOT "WHAT THIS CLIP IS NOW"
---------------------------------------------------------------------------
The bug was never about the clip you were dragging — that one always did the right
thing. It was about the clips you were NOT dragging. So each check reads the start
of every picture before and after the gesture and names exactly which ones changed;
"the clip I trimmed got shorter" would have passed against the bug.

    python tests/editor_picture_tracks_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router — the harness is `editor_razor_check.py`'s, borrowed rather
than invented a second time.
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

PROBE_HTML = os.path.join(CLIENT, "__probe_ptracks.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_ptracks.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture — four clips on the base track, one of them footage
# ---------------------------------------------------------------------------
# ⚠ EVERY CLIP CARRIES AN EXPLICIT `start_ms`, because that is what the editor
# writes the first time it opens a project (the load-time fill in `onLoadedRef`).
# Leaving them out would test the compatibility path instead of the edited one,
# and the compatibility path is what `render_parity.py` covers.
#
#   track 0:  [p1 0–2000][p2 2000–4000][vid 4000–6000][p3 6000–8000]
#
# `vid` is a video clip in the middle, which is the exact shape the report
# describes: trim it, and under the old model p3 moved.
TOTAL_MS = 8000
PROJECT = {
    "id": "probe",
    "title": "probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [
        {"id": "p1", "kind": "image", "src": {"kind": "upload", "upload_id": "u1"},
         "duration_ms": 2000, "start_ms": 0, "track": 0, "label": "P1",
         "url": "/animatics/probe/media/u1"},
        {"id": "p2", "kind": "image", "src": {"kind": "upload", "upload_id": "u2"},
         "duration_ms": 2000, "start_ms": 2000, "track": 0, "label": "P2",
         "url": "/animatics/probe/media/u2"},
        # A dropped-in video FILE — origin "video", so this is the clip the ▶⇧
        # split moves onto a track of its own.
        {"id": "vid", "kind": "video", "src": {"kind": "video", "upload_id": "u3"},
         "duration_ms": 2000, "start_ms": 4000, "track": 0, "label": "VID",
         "in_ms": 0, "out_ms": 6000, "speed": 1.0,
         "url": "/animatics/probe/media/u3"},
        {"id": "p3", "kind": "image", "src": {"kind": "upload", "upload_id": "u1"},
         "duration_ms": 2000, "start_ms": 6000, "track": 0, "label": "P3",
         "url": "/animatics/probe/media/u1"},
    ],
    "texts": [], "shapes": [], "layers": [], "overlays": [],
    "transitions": [], "audio_tracks": [], "veo_clips": [], "video": None,
}


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


MEDIA = {
    "u1": ("image/png", picture_bytes((74, 134, 200))),
    "u2": ("image/png", picture_bytes((200, 120, 60))),
    # A video route that 404s: the clip still DRAWS (it falls back to its
    # thumbnail, and here to nothing), and every gesture under test is about
    # times rather than pixels. Decoding an MP4 in the harness would buy nothing.
    "u3": None,
}


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

/**
 * WHERE EVERY PICTURE SITS AND HOW WIDE IT IS, in pixels off the screen.
 *
 * ⚠ MEASURED, NOT READ OUT OF STATE. The whole bug was that clips MOVED when they
 * should not have, and "the document says 4000" is not the same claim as "the bar
 * is drawn at 4000". Pixels also make the check independent of how the editor
 * stores a start, which is exactly the thing that changed.
 */
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

/** Which rows exist, top to bottom. */
probe.laneKeys = () =>
  Array.from(document.querySelectorAll("[data-lane]")).map((n) => n.dataset.lane);

/** Scroll a clip and a row into view together — a drag cannot scroll. */
probe.revealPair = (sel, laneKey) => {
  const sc = document.querySelector(".tl-scroll");
  const a = document.querySelector(sel);
  const b = document.querySelector(`[data-lane="${laneKey}"]`);
  if (!sc || !a || !b) return { ok: false, why: "missing " + (!a ? sel : laneKey) };
  const view = sc.getBoundingClientRect();
  const boxes = () => [a.getBoundingClientRect(), b.getBoundingClientRect()];
  let [ar, br] = boxes();
  const need = Math.max(ar.bottom, br.bottom) - Math.min(ar.top, br.top);
  if (need > view.height) return { ok: false, why: `needs ${Math.round(need)}px of ${Math.round(view.height)}px` };
  const mid = (Math.max(ar.bottom, br.bottom) + Math.min(ar.top, br.top)) / 2;
  sc.scrollTop += mid - (view.top + view.height / 2);
  [ar, br] = boxes();
  const inside = (r) => r.top >= view.top - 1 && r.bottom <= view.bottom + 1;
  return { ok: inside(ar) && inside(br), why: "scrolled" };
};

probe.hitAt = (x, y) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return "nothing";
  const chain = [];
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    chain.push(n.className && n.className.split ? n.className.split(" ")[0] : n.tagName);
  }
  return chain.join(" < ");
};

probe.notice = () => {
  const el = document.querySelector(".an-status-note");
  return el ? el.textContent : "";
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>picture tracks probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_ptracks.jsx"></script>
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


def press_tool(page, key):
    """Pick a tool the way a person does — its button, not a keyboard shortcut."""
    page.click(f"button.an-tool[title^='{key}']")
    page.wait_for_timeout(60)


def drag(page, selector, dx, dy=0, grip=None, lane_key=None):
    """Drag `selector` (or a grip inside it) by dx/dy. "" on success, else why not."""
    clip = page.query_selector(selector)
    if clip is None:
        return f"nothing matched {selector}"
    if lane_key:
        shown = page.evaluate(
            "([s, k]) => window.__probe.revealPair(s, k)", [selector, lane_key]
        )
        if not shown.get("ok"):
            return f"cannot see {selector} and row {lane_key} at once ({shown.get('why')})"
        page.wait_for_timeout(80)
    else:
        clip.scroll_into_view_if_needed()
        page.wait_for_timeout(60)
    press_on = clip.query_selector(grip) if grip else clip
    if press_on is None:
        return f"{selector} has no {grip}"
    box = press_on.bounding_box()
    if not box:
        return "no box"
    x0 = box["x"] + box["width"] / 2
    y0 = box["y"] + box["height"] / 2
    on_target = page.evaluate(
        "([x, y, s]) => { const el = document.elementFromPoint(x, y);"
        " return Boolean(el && el.closest(s)); }",
        [x0, y0, grip or selector],
    )
    if not on_target:
        landed = page.evaluate("([x, y]) => window.__probe.hitAt(x, y)", [x0, y0])
        return f"{grip or selector} is out of reach — the press would land on {landed}"
    page.mouse.move(x0, y0)
    page.mouse.down()
    # More than one move: every drag here decides what it is on the FIRST
    # pointermove and writes its result from the last one.
    page.mouse.move(x0 + (4 if dx >= 0 else -4), y0, steps=2)
    page.mouse.move(x0 + dx, y0 + dy / 2, steps=4)
    page.mouse.move(x0 + dx, y0 + dy, steps=4)
    page.mouse.up()
    page.wait_for_timeout(200)
    return ""


def step_for(bars, cid, fraction=0.3):
    """A drag distance that is a FRACTION OF THE BAR, never a fixed pixel count.

    ⚠ THE TIMELINE'S ZOOM IS NOT KNOWN HERE. At the default pixels-per-second a
    two-second clip is a few dozen pixels wide, so a "60px" drag is nearly two
    seconds and collapses the clip onto the 100ms floor — at which point the trim
    correctly refuses and the test reads it as nothing having happened. (It did, on
    the first run.) A third of the bar is a real edit at any zoom, and can never
    reach the floor.
    """
    return max(8, int(round(bars[cid]["width"] * fraction)))


def moved(before, after, tol=3):
    """Which bars changed where they START, by more than a rounding pixel."""
    out = []
    for cid, box in after.items():
        was = before.get(cid)
        if not was:
            continue
        if abs(box["left"] - was["left"]) > tol:
            out.append(cid)
    return sorted(out)


def resized(before, after, tol=3):
    out = []
    for cid, box in after.items():
        was = before.get(cid)
        if not was:
            continue
        if abs(box["width"] - was["width"]) > tol:
            out.append(cid)
    return sorted(out)


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
            page.goto(f"http://127.0.0.1:{port}/__probe_ptracks.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nFour pictures on one track")
            try:
                page.wait_for_selector("canvas", timeout=45000)
                page.wait_for_function(
                    "() => document.querySelectorAll('[data-sel^=\"frame:\"]').length >= 4",
                    timeout=45000,
                )
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts with the picture track drawn", False, str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=os.path.join(ROOT, "ptracks_probe_failed.png"))
                browser.close()
                return 1

            keys = page.evaluate("() => window.__probe.laneKeys()")
            check("the picture row is a numbered TRACK", "frames:0" in keys, str(keys))
            bars = page.evaluate("() => window.__probe.bars()")
            check("all four clips are on it", len(bars) == 4 and
                  all(b["lane"] == "frames:0" for b in bars.values()), str(bars))
            check("and they are drawn butted up, in order",
                  bars["p1"]["left"] < bars["p2"]["left"] < bars["vid"]["left"] < bars["p3"]["left"],
                  str({k: v["left"] for k, v in bars.items()}))

            # -----------------------------------------------------------------
            # THE REPORTED BUG
            # -----------------------------------------------------------------
            print("\n⚠ THE REPORT: trimming the video clip used to move the stills")
            press_tool(page, "Selection")
            before = page.evaluate("() => window.__probe.bars()")
            missed = drag(page, '[data-sel="frame:vid"]', -step_for(before, "vid"),
                          grip=".tl-handle:not(.tl-handle-l)")
            after = page.evaluate("() => window.__probe.bars()")
            check(
                "trimming the video clip's tail makes IT shorter",
                not missed and resized(before, after) == ["vid"],
                missed or f"resized: {resized(before, after)}",
            )
            check(
                "⚠ …AND MOVES NOTHING ELSE — the still after it stays put",
                not missed and moved(before, after) == [],
                missed or f"also moved: {moved(before, after)}",
            )

            print("\nAnd the same the other way round: trimming a still")
            before = page.evaluate("() => window.__probe.bars()")
            missed = drag(page, '[data-sel="frame:p2"]', -step_for(before, "p2"),
                          grip=".tl-handle:not(.tl-handle-l)")
            after = page.evaluate("() => window.__probe.bars()")
            check(
                "trimming a still makes IT shorter and moves nothing",
                not missed and resized(before, after) == ["p2"] and moved(before, after) == [],
                missed or f"resized {resized(before, after)}, moved {moved(before, after)}",
            )

            # -----------------------------------------------------------------
            # THE TOOLS THAT DO MOVE THINGS, WHEN YOU ASK THEM TO
            # -----------------------------------------------------------------
            print("\nB — ripple: the same trim, and the row closes up behind it")
            press_tool(page, "Ripple")
            before = page.evaluate("() => window.__probe.bars()")
            missed = drag(page, '[data-sel="frame:p1"]', -step_for(before, "p1"),
                          grip=".tl-handle:not(.tl-handle-l)")
            after = page.evaluate("() => window.__probe.bars()")
            check(
                "a ripple trim moves everything after it on that track",
                not missed and moved(before, after) == ["p2", "p3", "vid"],
                missed or f"moved: {moved(before, after)}",
            )
            check("…and the clip itself is the only one that changed length",
                  not missed and resized(before, after) == ["p1"],
                  missed or str(resized(before, after)))

            print("\nN — rolling: the cut moves, the track's length does not")
            # ⚠ ON A PAIR THAT STILL TOUCHES. The two plain trims above left a GAP
            # after `vid` and after `p2` — which is what a plain trim is FOR — and
            # rolling needs a real cut to roll: p1 and p2 are still butted, because
            # the ripple trim closed up behind itself.
            press_tool(page, "Rolling")
            before = page.evaluate("() => window.__probe.bars()")
            end_before = max(b["left"] + b["width"] for b in before.values())
            missed = drag(page, '[data-sel="frame:p1"]', step_for(before, "p1"),
                          grip=".tl-handle:not(.tl-handle-l)")
            after = page.evaluate("() => window.__probe.bars()")
            end_after = max(b["left"] + b["width"] for b in after.values())
            check(
                "a rolling trim gives to the next clip what it takes",
                not missed and sorted(resized(before, after)) == ["p1", "p2"],
                missed or f"resized: {resized(before, after)}",
            )
            check("…and only the clip after it moved — its start IS the cut",
                  not missed and moved(before, after) == ["p2"],
                  missed or f"moved: {moved(before, after)}")
            check("…so the track ends exactly where it did",
                  not missed and abs(end_after - end_before) <= 3,
                  f"{end_before} -> {end_after}")

            # ⚠ AND IT FALLS BACK RATHER THAN GUESSING. `p2` has a gap after it now,
            # so there is no neighbour to give to — rolling there is a plain trim,
            # which is the honest answer and the one the grip's tooltip promises.
            #
            # ⚠ THE ASSERTION IS "IT TOUCHED NOTHING ELSE", not "it resized by n
            # pixels". How far the clip under the pointer ends up moving depends on
            # the zoom and on which cut its edge snapped to, and the plain-trim
            # cases above already prove that a trim trims. What THIS case is for is
            # that rolling with nothing to roll against does not reach for a clip
            # you were not pointing at.
            before = page.evaluate("() => window.__probe.bars()")
            missed = drag(page, '[data-sel="frame:p2"]', -step_for(before, "p2"),
                          grip=".tl-handle:not(.tl-handle-l)")
            after = page.evaluate("() => window.__probe.bars()")
            others = sorted(
                {c for c in resized(before, after) + moved(before, after) if c != "p2"}
            )
            check(
                "rolling a clip with a GAP after it touches no other clip",
                not missed and not others,
                missed or f"also changed: {others}",
            )

            print("\n▶⇧ — put the footage on a track of its own")
            press_tool(page, "Selection")
            before = page.evaluate("() => window.__probe.bars()")
            split = page.query_selector(".tl-layer-split")
            check("the picture row offers the split, because it is carrying both",
                  split is not None)
            if split is not None:
                split.click()
                page.wait_for_timeout(250)
                after = page.evaluate("() => window.__probe.bars()")
                keys = page.evaluate("() => window.__probe.laneKeys()")
                check("a second picture track appeared", "frames:1" in keys, str(keys))
                check("the video clip is on it", after["vid"]["lane"] == "frames:1",
                      after["vid"]["lane"])
                check("the stills stayed on the base track",
                      [after[k]["lane"] for k in ("p1", "p2", "p3")]
                      == ["frames:0"] * 3,
                      str([after[k]["lane"] for k in ("p1", "p2", "p3")]))
                check("⚠ AND NOTHING WAS RE-TIMED — every clip plays where it did",
                      moved(before, after) == [] and resized(before, after) == [],
                      f"moved {moved(before, after)}, resized {resized(before, after)}")
                check("…and the editor says so rather than doing it quietly",
                      "same moment" in page.evaluate("() => window.__probe.notice()"),
                      page.evaluate("() => window.__probe.notice()"))

            # -----------------------------------------------------------------
            # A CLIP ONLY MOVES TO A ROW OF ITS OWN KIND
            # -----------------------------------------------------------------
            # ⚠ THIS CHECK USED TO EXPECT THE OPPOSITE, and the change is
            # deliberate rather than a regression. It asserted that "a still
            # dragged up onto the footage track goes there", which was true while
            # any picture row took any picture clip. The rows are STRICT now — the
            # split above left `frames:0` holding stills and `frames:1` holding
            # footage, and each row only accepts its own kind (`clipRowKind` /
            # `ROW_TAKES`). Asked for directly: "i only move each same layer clip
            # like image move in only image layer and video move video any layer".
            #
            # Both directions are checked, because a rule that only holds one way
            # round is not the rule — and a refused drag must leave the timeline
            # exactly as it was rather than half-moving anything.
            print("\nA picture only moves to a row of its own kind")
            before = page.evaluate("() => window.__probe.bars()")
            drag(page, '[data-sel="frame:p3"]', 0, dy=-40, lane_key="frames:1")
            after = page.evaluate("() => window.__probe.bars()")
            check(
                "a still dragged onto the footage row is refused",
                after["p3"]["lane"] == "frames:0",
                f'landed on {after["p3"]["lane"]}',
            )
            check("…and nothing moved on the way",
                  moved(before, after) == [] and resized(before, after) == [],
                  f"moved {moved(before, after)}, resized {resized(before, after)}")

            before = page.evaluate("() => window.__probe.bars()")
            drag(page, '[data-sel="frame:vid"]', 0, dy=40, lane_key="frames:0")
            after = page.evaluate("() => window.__probe.bars()")
            check(
                "and footage dragged onto the stills row is refused too",
                after["vid"]["lane"] == "frames:1",
                f'landed on {after["vid"]["lane"]}',
            )
            check("…and nothing moved on the way either",
                  moved(before, after) == [] and resized(before, after) == [],
                  f"moved {moved(before, after)}, resized {resized(before, after)}")

            print("\nAfterwards")
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:400])
            if failures:
                page.screenshot(path=os.path.join(ROOT, "ptracks_probe_failed.png"))
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
    print("A trim moves one clip. Ripple and rolling move more, when you ask them to.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
