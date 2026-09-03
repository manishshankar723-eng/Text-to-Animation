"""A PAID VEO RENDER SHOWS ITS PICTURE — in the Media pane and in the monitor.

The report:

    "same erroe when i upload video see image not view in program panel and in
     media now i see uploading type view"

A 4-second render landed on its Storyboard video row, at the right moment, with
the right length — and it was a loading spinner in Media and a black hole in the
Program monitor. The panel underneath showed through instead, so it read as an
upload that never finished.

ONE MISSING FIELD, and it is `url`. `attachVeoClip` wrote its clip out by hand
instead of going through `newVideoClip`, and left out the one thing a
hand-written copy always leaves out. Two things break from that, not one:

  · The thumbnail effect only fetches frames that HAVE a url, so no poster was
    ever requested and the Media card sat on `.fs-thumb-wait` for ever.
  · `ProgramCanvas` falls back to a video clip's THUMBNAIL while the video blob
    is still downloading (blobs are fetched one at a time, and these are the
    biggest files in the project). With no thumbnail there is no fallback, so the
    monitor drew nothing at all for the render.

A reload fixed it, which is what made it look like an upload problem: the server
fills a url in on read. That is the same bug `newVideoClip`'s own ⚠ note
describes — "I upload a video file here but it doesn't show in the media panel" —
happening a second time, in the one place that did not use that factory.

---------------------------------------------------------------------------
⚠ THE MONITOR CHECK IS A COLOUR, AND THE TWO PICTURES ARE DELIBERATELY DIFFERENT
---------------------------------------------------------------------------
The panel is BLUE and the render's poster is RED, stacked at the same moment with
the render on the row above. So "the monitor shows the render" and "the monitor
shows the panel showing through" are two different pixels, and the assertion
cannot be satisfied by drawing something. ⚠ The raw video route is ABORTED on
purpose, so the only way red can reach the screen is the thumbnail fallback —
which is the code path the report is about.

This also needs no animate dialog and spends nothing: `reconcileVeoClips` runs on
every LOAD (it is self-healing, for renders that finished while the editor was
shut), so a `veo_clips` record in the fixture drives the same attach.

    python tests/editor_veo_attach_check.py

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

PROBE_HTML = os.path.join(CLIENT, "__probe_veo.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_veo.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture — one board panel, and a finished render of it
# ---------------------------------------------------------------------------
# ⚠ THE PANEL AND THE RENDER COVER THE SAME 4 SECONDS. That is the point: the
# render goes on the row ABOVE (`boardVideoTrack`) at the panel's own start, with
# the still left underneath, so whichever of the two the monitor draws is a fact
# about the code and not about the timing.
BOARD_ID = "board1"
VEO_UPLOAD = "u9veo000abcd"
CLIP_MS = 4000

PANEL_RGB = (30, 80, 220)      # the panel, on the row BELOW
POSTER_RGB = (225, 45, 40)     # the render's poster, on the row ABOVE

PROJECT = {
    "id": "probe",
    "title": "probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [
        {"id": "p1", "kind": "image",
         "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": 0},
         "duration_ms": CLIP_MS, "start_ms": 0, "track": 0, "label": "Shot 1",
         "url": "/animatics/probe/frame/p1?v=1"},
    ],
    "texts": [], "shapes": [], "layers": [], "overlays": [],
    "transitions": [], "audio_tracks": [], "video": None,
    # SERVER-OWNED, and the whole fixture: a render that is ready and not yet on
    # the timeline. `reconcileVeoClips` attaches it on load.
    "veo_clips": [
        {"id": "c1", "frame_id": "p1", "upload_id": VEO_UPLOAD, "prompt": "he turns",
         "status": "ready", "error": "", "duration_ms": CLIP_MS, "cost_usd": 0.5,
         "rendered_at": "2026-08-20T10:00:00Z"},
    ],
}

# Which media requests the client actually made. The bug is invisible in the DOM
# alone — a spinner looks the same whether the fetch failed or was never issued
# — so what is asserted is that the POSTER WAS ASKED FOR.
POSTER_ASKS: list[str] = []
RAW_ASKS: list[str] = []


def picture_bytes(rgb, size=(320, 180)):
    buf = io.BytesIO()
    Image.new("RGB", size, rgb).save(buf, "PNG")
    return buf.getvalue()


PANEL_PNG = picture_bytes(PANEL_RGB)
POSTER_PNG = picture_bytes(POSTER_RGB)


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

    # --- the panel underneath ------------------------------------------------
    if "/animatics/probe/frame/" in url:
        route.fulfill(status=200, headers=cors, content_type="image/png",
                      body=PANEL_PNG)
        return

    # --- the render's upload: a POSTER, or the file itself --------------------
    if f"/animatics/probe/media/{VEO_UPLOAD}" in url:
        if "poster=1" in url:
            POSTER_ASKS.append(url)
            route.fulfill(status=200, headers=cors, content_type="image/png",
                          body=POSTER_PNG)
        else:
            # ⚠ REFUSED ON PURPOSE. The monitor prefers the video blob; killing it
            # leaves the thumbnail fallback as the only way the render can reach
            # the screen, which is the path the report is about. A real 100MB take
            # downloading behind another one is the same situation, slowly.
            RAW_ASKS.append(url)
            route.abort()
        return

    if url.rstrip("/").endswith("/animatics/luts"):
        send([])
        return

    if re.search(r"/animatics/probe/?(\?.*)?$", url):
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

/** Every media card, and whether it drew a picture or is still waiting. */
probe.cards = () =>
  Array.from(document.querySelectorAll(".fs-card")).map((card) => {
    const kind = card.querySelector(".fs-kind");
    return {
      num: (card.querySelector(".fs-num") || {}).textContent || "",
      label: (card.querySelector(".fs-label") || {}).textContent || "",
      video: Boolean(kind),
      badge: kind ? kind.textContent.trim() : "",
      drawn: Boolean(card.querySelector(".fs-thumb img")),
      waiting: Boolean(card.querySelector(".fs-thumb-wait")),
    };
  });

/** Which row each clip is drawn on, and where it starts, in pixels. */
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

probe.laneKeys = () =>
  Array.from(document.querySelectorAll("[data-lane]")).map((n) => n.dataset.lane);

/** The monitor canvas, so a screenshot can be clipped to exactly it. */
probe.canvasBox = () => {
  const c = document.querySelector(".an-screen canvas") || document.querySelector("canvas");
  if (!c) return null;
  const r = c.getBoundingClientRect();
  return { x: r.left, y: r.top, width: r.width, height: r.height };
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>veo attach probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_veo.jsx"></script>
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


def monitor_colour(page):
    """The average colour in the MIDDLE of the monitor, as (r, g, b).

    ⚠ THE MIDDLE, not the whole canvas: the picture is letterboxed (`fit:
    contain`) against the project background, so averaging the lot mixes the bars
    into the answer and both outcomes come out grey.
    """
    box = page.evaluate("() => window.__probe.canvasBox()")
    if not box or box["width"] < 20 or box["height"] < 20:
        return None
    inset_x = box["width"] * 0.3
    inset_y = box["height"] * 0.3
    shot = page.screenshot(clip={
        "x": box["x"] + inset_x,
        "y": box["y"] + inset_y,
        "width": box["width"] - inset_x * 2,
        "height": box["height"] - inset_y * 2,
    })
    img = Image.open(io.BytesIO(shot)).convert("RGB")
    # Averaged off the raw bytes rather than through `getdata()`, which Pillow 14
    # removes.
    raw = img.tobytes()
    n = len(raw) // 3
    return tuple(round(sum(raw[i::3]) / n) for i in range(3))


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
            page.goto(f"http://127.0.0.1:{port}/__probe_veo.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nA finished render attaches itself on load")
            try:
                page.wait_for_selector("canvas", timeout=45000)
                page.wait_for_function(
                    "() => document.querySelectorAll('[data-sel^=\"frame:\"]').length >= 2",
                    timeout=45000,
                )
            except Exception as exc:  # noqa: BLE001
                check("the panel and its render are both on the timeline", False,
                      str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=shot("veo_probe_failed.png"))
                browser.close()
                return 1

            bars = page.evaluate("() => window.__probe.bars()")
            render_id = next((k for k in bars if k != "p1"), None)
            check("the render was attached", render_id is not None, str(bars))
            if render_id is None:
                page.screenshot(path=shot("veo_probe_failed.png"))
                browser.close()
                return 1
            check("…on a row ABOVE the panel, not in place of it",
                  bars[render_id]["lane"] != bars["p1"]["lane"]
                  and "frames:0" == bars["p1"]["lane"],
                  str({k: v["lane"] for k, v in bars.items()}))
            check("…starting at the same moment as the panel",
                  abs(bars[render_id]["left"] - bars["p1"]["left"]) <= 2,
                  str({k: v["left"] for k, v in bars.items()}))

            # -----------------------------------------------------------------
            # THE MEDIA PANE
            # -----------------------------------------------------------------
            print("\nIn Media it is a picture, not a spinner")
            page.wait_for_timeout(1500)
            cards = page.evaluate("() => window.__probe.cards()")
            video_cards = [c for c in cards if c["video"]]
            check("the render is listed as a video clip", len(video_cards) == 1,
                  json.dumps(cards)[:300])
            if video_cards:
                card = video_cards[0]
                check("and it says how much footage is inside it",
                      card["badge"].startswith("▶ 4.0"), card["badge"])
                check("it has DRAWN its poster", card["drawn"], json.dumps(card))
                check("…so it is not sitting on the loading spinner",
                      not card["waiting"], json.dumps(card))
            check("the poster was actually asked for", bool(POSTER_ASKS),
                  "no request for ?poster=1 was ever made")

            # -----------------------------------------------------------------
            # THE PROGRAM MONITOR
            # -----------------------------------------------------------------
            # ⚠ RED = the render is on screen. BLUE = the panel underneath is
            # showing through, which is what the report described.
            print("\nAnd in the Program monitor it is the render, not the panel")
            check("the raw video was refused, so only the fallback can draw",
                  bool(RAW_ASKS), "the monitor never even asked for the file")
            colour = monitor_colour(page)
            check("the monitor drew something", colour is not None, "no canvas box")
            if colour:
                r, g, b = colour
                check("it is the RENDER's picture (red), not the panel's (blue)",
                      r > b + 40, f"average rgb{colour} — blue means the panel showed through")

            print("\nAfterwards")
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:400])
            if failures:
                page.screenshot(path=shot("veo_probe_failed.png"))
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
    print("A render shows its own picture the moment it attaches, not after a reload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
