"""Dropping an effect out of the Effects library, IN THE REAL EDITOR.

`tests/monitor_effects_check.py` mounts the MONITOR and hands it a chain. This
one mounts the whole `<AnimaticEditor>` and performs the GESTURE — drag a tile
out of the Effects tab onto a clip on the timeline — because the report this was
written for was not about the maths:

    "when i drag and drop gamma and exposure effects in timeline
     my screen is black right now"

Every effects test passed while that was true, and they passed because none of
them ever ran the editor. The chain they graded was one a test wrote; the chain
a drop writes is `params: {}` on a clip inside a live document, with the
Properties pane opening onto it in the same commit.

⚠ THE ASSERTIONS ARE ABOUT THE SCREEN STILL BEING THERE. Did the monitor survive
the drop, is it still drawing the picture, and did anything reach
`window.onerror` / `console.error` — the three things a black editor fails and a
working one cannot. The graded colour is read back too, so a monitor that
survives while drawing nothing cannot pass.

    python tests/editor_effects_drop_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router, the same way `monitor_effects_check.py` answers the LUTs.

⚠ THE PROBE PAGE IS WRITTEN INTO `client/` AND DELETED AGAIN, for the reason
that file gives: Vite serves its own root and refuses anything above it.
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
LUT_DIR = os.path.join(ROOT, "luts")

PROBE_HTML = os.path.join(CLIENT, "__probe_editor.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_editor.jsx")

# The effects to drop, by the LABEL the library tile carries. The first two are
# the pair in the report; the rest are the other point-wise grades added in the
# same phase, dropped one after another onto the same clip so the chain grows
# the way a user's does rather than being replaced each time.
DROPS = ["Gamma", "Exposure", "Temperature & tint", "Hue rotate", "Sepia", "Posterize"]

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The stubbed project
# ---------------------------------------------------------------------------
# Two clips, so there is a CUT for a transition to land on and a second picture
# for the strip to draw. Uploads rather than panels: an upload's media is one
# route, and this test is not about where a picture comes from.
PROJECT = {
    "id": "probe",
    "title": "probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [
        {"id": "f1", "kind": "image", "src": {"kind": "upload", "upload_id": "u1"},
         "duration_ms": 2000, "label": "Shot 1",
         "url": "/animatics/probe/media/u1"},
        {"id": "f2", "kind": "image", "src": {"kind": "upload", "upload_id": "u2"},
         "duration_ms": 2000, "label": "Shot 2",
         "url": "/animatics/probe/media/u2"},
    ],
    "texts": [], "shapes": [], "layers": [], "overlays": [], "transitions": [],
    "audio_tracks": [], "veo_clips": [], "video": None,
}


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


PICTURES = {"u1": picture_bytes((74, 134, 200)), "u2": picture_bytes((200, 120, 60))}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def route_api(route, request):
    """Every call the editor makes, answered off the fixtures above."""
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
        data = PICTURES.get(match.group(1))
        if data is None:
            route.fulfill(status=404, headers=cors, body="")
        else:
            route.fulfill(status=200, headers=cors, content_type="image/png", body=data)
        return

    match = re.search(r"/animatics/luts/([^/?]+)$", url)
    if match:
        path = os.path.join(LUT_DIR, f"{match.group(1)}.cube")
        if not os.path.isfile(path):
            route.fulfill(status=404, headers=cors, content_type="application/json",
                          body=json.dumps({"detail": "no such LUT"}))
            return
        with open(path, encoding="utf-8") as fh:
            route.fulfill(status=200, headers=cors, content_type="text/plain", body=fh.read())
        return

    if url.rstrip("/").endswith("/animatics/luts"):
        names = sorted(f[:-5] for f in os.listdir(LUT_DIR) if f.endswith(".cube"))
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps(names))
        return

    if re.search(r"/animatics/probe/?$", url):
        # PUT is the autosave. Answering it with the document means an autosave
        # firing mid-test cannot change what the editor is holding.
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps(PROJECT))
        return

    # Everything else — the job poll, the sequence endpoints — is "nothing here",
    # which is a state the editor has to cope with anyway.
    route.fulfill(status=404, headers=cors, content_type="application/json",
                  body=json.dumps({"detail": "not found"}))


PROBE_JSX_SOURCE = r"""
import React from "react";
import { createRoot } from "react-dom/client";

import AnimaticEditor from "/src/components/AnimaticEditor.jsx";
import { Compositor } from "/src/animatic/gl/compositor.js";
import "/src/styles/index.css";

const probe = { errors: [], disposes: 0, ready: false };
window.__probe = probe;

// ⚠ EVERY ROUTE A CRASH CAN TAKE OUT OF REACT. An error thrown while rendering
// reaches window.onerror; one thrown in an effect's cleanup is re-reported by
// React through console.error; an unhandled rejection is neither. A black
// editor has come out of all three.
window.addEventListener("error", (e) => probe.errors.push(String(e.message || e)));
window.addEventListener("unhandledrejection", (e) =>
  probe.errors.push("unhandled rejection: " + String(e.reason))
);
const realError = console.error;
console.error = (...args) => {
  probe.errors.push(args.map(String).join(" "));
  realError.apply(console, args);
};

const realEnd = Compositor.prototype.end;
Compositor.prototype.end = function patchedEnd() {
  probe.compositor = this;
  return realEnd.apply(this, arguments);
};
const realDispose = Compositor.prototype.dispose;
Compositor.prototype.dispose = function patchedDispose() {
  probe.disposes += 1;
  return realDispose.apply(this, arguments);
};

createRoot(document.getElementById("root")).render(
  <AnimaticEditor animaticId="probe" onBack={() => {}} onDeleted={() => {}} />
);

/** The centre pixel of the finished frame, straight out of the framebuffer. */
probe.read = () => {
  const comp = probe.compositor;
  const canvas = document.querySelector("canvas");
  if (!comp || !canvas) return null;
  const [w, h] = comp.size;
  const px = comp.readPixels();
  const i = (Math.round(h / 2) * w + Math.round(w / 2)) * 4;
  return {
    rgb: [px[i], px[i + 1], px[i + 2]],
    glError: comp.gl.getError(),
    errors: probe.errors.slice(),
    disposes: probe.disposes,
    canvas: true,
  };
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>editor drop probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_editor.jsx"></script>
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


# The drag the browser will not synthesise for us. Playwright's mouse moves do
# not start an HTML5 drag in a headless Chromium, so the four events the tile
# and the lane actually listen to are dispatched here, sharing ONE DataTransfer
# — which is the only part of a real drag this code can tell apart from a mouse.
DRAG = """
([source, target]) => {
  if (!source) return "no tile";
  if (!target) return "no drop target";
  const dt = new DataTransfer();
  const fire = (el, type, extra) => {
    const ev = new DragEvent(type, {
      bubbles: true, cancelable: true, dataTransfer: dt, ...extra,
    });
    el.dispatchEvent(ev);
    return ev;
  };
  const box = target.getBoundingClientRect();
  // A THIRD OF THE WAY IN, not the left edge: `frameIndexContaining` reads the
  // drop's x as a time, and the very edge of the first bar is the boundary a
  // rounding error puts on the wrong side of.
  const at = { clientX: box.left + box.width / 3, clientY: box.top + box.height / 2 };
  fire(source, "dragstart", {});
  fire(target, "dragenter", at);
  fire(target, "dragover", at);
  fire(target, "drop", at);
  fire(source, "dragend", {});
  return true;
}
"""


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
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_editor.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nThe editor comes up")
            try:
                page.wait_for_selector("canvas", timeout=45000)
                check("the editor mounts and the monitor is on screen", True)
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts and the monitor is on screen", False, str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:3000])
                page.screenshot(path=os.path.join(ROOT, "editor_probe_failed.png"))
                browser.close()
                return 1
            page.wait_for_function("window.__probe.read() !== null", timeout=30000)
            before = page.evaluate("() => window.__probe.read()")
            check("it is drawing the picture, not a black frame",
                  before is not None and max(before["rgb"]) > 8, str(before and before["rgb"]))

            # The Effects tab, opened the way a user opens it.
            page.click("button.an-tab:has-text('Effects')")
            page.wait_for_selector(".fx-lib", timeout=15000)
            # Every folder open, so the tiles are in the DOM to drag.
            for _ in range(24):
                shut = page.query_selector_all(".fx-folder:not(.open) > .fx-folder-row")
                if not shut:
                    break
                shut[0].click()
            tiles = page.query_selector_all(".fx-entry")
            check("the Effects library is on screen with tiles in it",
                  len(tiles) > 0, f"{len(tiles)} tiles")

            print("\nDropping each grade onto the first clip on the timeline")
            target = page.query_selector(TARGET)
            if target is None:
                check("there is a clip on the timeline to drop onto", False,
                      f"nothing matched {TARGET}")
                page.screenshot(path=os.path.join(ROOT, "editor_probe_failed.png"))
                browser.close()
                return 1
            check("there is a clip on the timeline to drop onto", True)

            for label in DROPS:
                tile = page.query_selector(
                    f".fx-entry:has(.fx-row-name:text-is('{label}'))"
                )
                if tile is None:
                    check(f"the library offers a '{label}' tile", False, "no such tile")
                    continue
                verdict = page.evaluate(DRAG, [tile, target])
                if verdict is not True:
                    check(f"drop {label}", False, str(verdict))
                    continue
                page.wait_for_timeout(500)
                got = page.evaluate("() => window.__probe.read()")
                check(f"{label:20} — the monitor survived the drop",
                      got is not None and got["canvas"], "the canvas is gone")
                if got is None:
                    # The canvas going means React unmounted the tree, so
                    # `read()` can no longer report anything. The errors live on
                    # the probe itself and outlive the component — print them,
                    # because THIS is the black screen and the message is the
                    # whole answer.
                    for line in page.evaluate("() => window.__probe.errors")[:6]:
                        print("        ↳", line[:600])
                    continue
                check(f"{label:20} — it is still drawing the picture",
                      max(got["rgb"]) > 8,
                      f"monitor reads {tuple(got['rgb'])} — a black frame")
                check(f"{label:20} — no GL error", got["glError"] == 0, str(got["glError"]))
                check(f"{label:20} — nothing threw",
                      not got["errors"], "; ".join(got["errors"][:2])[:400])

            # ⚠ AND THE PANE HAS TO DRAW THE ROW, not merely survive drawing it.
            # The crash was in a value FORMATTER, so a chain that renders while
            # every field reads blank would satisfy everything above and still
            # be the bug. These are the defaults out of `EFFECT_PARAMS`, as the
            # `FIELD` table scales them for the control.
            print("\nThe controls that landed in Properties")
            for row, want in PARAM_ROWS:
                value = page.evaluate(
                    """(label) => {
                      for (const r of document.querySelectorAll(".an-row")) {
                        const name = r.querySelector(".an-row-label");
                        const input = r.querySelector("input[type=number]");
                        if (name && input && name.textContent.trim() === label) {
                          return input.value;
                        }
                      }
                      return null;
                    }""",
                    row,
                )
                check(f"the {row!r} field reads {want}", value == want,
                      f"it reads {value!r}")

            print("\nAfterwards")
            last = page.evaluate("() => window.__probe.read()")
            check("the whole chain is on one clip and the monitor still draws it",
                  last is not None and max(last["rgb"]) > 8,
                  str(last and tuple(last["rgb"])))
            check("the GL context was never torn down",
                  last is not None and last["disposes"] == 0,
                  f"{last['disposes']} disposes" if last else "")
            check("nothing reached window.onerror or console.error",
                  last is not None and not last["errors"],
                  "; ".join(last["errors"][:3])[:600] if last and last["errors"] else "")
            # ⚠ ONLY WHEN SOMETHING FAILED. A passing run leaves no files in
            # the repo; a failing one leaves the picture of what went wrong,
            # which for a black editor is the entire diagnosis.
            if failures:
                page.screenshot(path=os.path.join(ROOT, "editor_after_drop.png"))
            browser.close()
    finally:
        if vite is not None:
            vite.terminate()
        for path in (PROBE_HTML, PROBE_JSX):
            try:
                os.remove(path)
            except OSError:
                pass

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("An effect dropped out of the library grades the clip; the editor stays up.")
    return 0


# One row per parameter the drops above add, and the value its control shows
# when nothing has been touched: `EFFECT_PARAMS`'s default, through the `scale`
# and `places` of `FIELD` in EffectsPanel.jsx. Gamma's 1.0 reads as 100 because
# its field is a percentage; exposure's 0 reads as "0" because stops are stops.
PARAM_ROWS = [
    ("Gamma", "100"),
    ("Exposure", "0"),
    ("Temperature", "0"),
    ("Tint", "0"),
    ("Rotate", "0"),
    ("Amount", "100"),
    ("Bands", "8"),
]

# The first picture on the timeline — `tl-bar` is the clip in the Pictures lane
# (Timeline.jsx). The drop handler lives on the LANE and the events bubble, so
# aiming at the bar is both what a user does and what reaches the handler.
TARGET = ".tl-lane.tl-bars .tl-bar"


if __name__ == "__main__":
    sys.exit(main())
