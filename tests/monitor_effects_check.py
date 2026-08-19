"""The Program monitor's effects, DRIVEN IN A REAL BROWSER.

Why this exists, in one sentence: every other effects test proves the *maths* is
right, and the bug that shipped was not in the maths.

    tests/effects_check.py          pins the PYTHON numbers
    tests/effects_parity_check.py   compares the shaders to those numbers
    THIS FILE                       mounts <ProgramCanvas> in Chromium and turns
                                    an effect on the way a user does

The fault it was written for: `Compositor.dispose()` handed `deleteTexture` a
`{ texture, size }` LUT ENTRY instead of the texture inside it. That throws — and
the throw came out of a React effect's CLEANUP, so React unmounted the monitor
and the editor showed a black rectangle. It could only fire once a LUT had been
uploaded, so the symptom was "the screen goes black when I pick a colour look"
and nothing else in the editor looked wrong. Both other tests passed throughout,
because neither of them ever unmounts anything.

⚠ SO THE ASSERTIONS HERE ARE ABOUT SURVIVAL, NOT ABOUT COLOUR. Does the monitor
still exist after the look changes; is the picture still being drawn; was the GL
context built ONCE rather than rebuilt per render; did anything reach
`window.onerror`. The colour checks are a thin parity sanity pass on flat
colours, kept only so a monitor that survives while drawing the wrong thing
cannot pass.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/monitor_effects_check.py

No backend and no dev server of your own are needed — this starts Vite itself and
answers the LUT requests off `luts/` with Playwright's own router.

⚠ THE PROBE PAGE IS WRITTEN INTO `client/` AND DELETED AGAIN. Vite serves its
root and nothing above it, so a harness in a temp directory (which is what
`effects_parity_check.py` can get away with, running under plain node) would be
outside `server.fs.allow` and refused. The two files are named with a `__probe`
prefix and removed in a `finally`.
"""

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

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from animatic_effects import apply_effects

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")
LUT_DIR = os.path.join(ROOT, "luts")

PROBE_HTML = os.path.join(CLIENT, "__probe_monitor.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_monitor.jsx")

# The flat colour every case is graded from. Flat ON PURPOSE: a colour card fills
# the frame edge to edge with no fit, no zoom and no resample, so the only thing
# between the source value and the read-back value is the effect chain itself.
CARD = "#4a86c8"
CARD_RGB = (0x4A, 0x86, 0xC8)

# In 8-bit code values. The shader works in floats and lands on a byte once; the
# Python side rounds through a byte between every step. 4 is the honest bar for
# a flat colour — far tighter than the 12 a full frame needs, and still wide
# enough that it will never fail for rounding alone.
TOLERANCE = 4

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------
def fx(kind, **params):
    return {"id": kind, "kind": kind, "params": params}


# ⚠ EVERY KIND IN `EFFECT_PARAMS` IS HERE, TWICE: once at a value that MOVES
# the picture, and once FRESHLY DROPPED with `params: {}`. Both are needed and
# neither is enough. A value case passes numbers in, so it proves the formula
# and says nothing about where a default comes from; a freshly-dropped case
# passes nothing in, so it proves the default reaches the shader and says
# nothing about the formula. The bug that shipped was in the second half.
CASES = [
    ("no effects", []),
    ("brightness 1.4", [fx("brightness", amount=1.4)]),
    ("brightness 0.5", [fx("brightness", amount=0.5)]),
    ("contrast 1.8", [fx("contrast", amount=1.8)]),
    ("contrast 0.4", [fx("contrast", amount=0.4)]),
    ("saturation 0", [fx("saturation", amount=0.0)]),
    ("saturation 1.8", [fx("saturation", amount=1.8)]),
    # The one the user reported. Identity is the case that CANNOT be seen, which
    # is exactly why picking it read as "the effect broke the picture".
    ("LUT identity", [fx("lut", name="identity", amount=1.0)]),
    ("LUT noir", [fx("lut", name="noir", amount=1.0)]),
    ("LUT cool", [fx("lut", name="cool", amount=1.0)]),
    ("LUT warm", [fx("lut", name="warm", amount=1.0)]),
    ("LUT teal_orange", [fx("lut", name="teal_orange", amount=1.0)]),
    ("LUT at half strength", [fx("lut", name="noir", amount=0.5)]),
    ("LUT at amount 0", [fx("lut", name="noir", amount=0.0)]),
    # A name with no file behind it is a NO-OP on both sides — never a black
    # frame and never a throw. A saved project can name a LUT that was deleted.
    ("a LUT that doesn't exist", [fx("lut", name="no_such_lut", amount=1.0)]),
    # A freshly added effect, before the user has touched anything: `params` is
    # `{}` and every value has to come from the defaults.
    ("a freshly added LUT (no params)", [fx("lut")]),
    ("two LUTs in one chain", [
        fx("lut", name="identity", amount=1.0),
        fx("lut", name="noir", amount=1.0),
    ]),
    # ⚠ ORDER. Reversed this is a different picture, so a chain that silently
    # sorted itself would fail here rather than look plausible.
    ("LUT then brightness", [
        fx("lut", name="noir", amount=1.0),
        fx("brightness", amount=1.4),
    ]),
    ("brightness then LUT", [
        fx("brightness", amount=1.4),
        fx("lut", name="noir", amount=1.0),
    ]),
    ("the whole chain at once", [
        fx("brightness", amount=1.2),
        fx("contrast", amount=1.3),
        fx("saturation", amount=0.7),
        fx("lut", name="warm", amount=0.6),
    ]),
    # -----------------------------------------------------------------------
    # The six point-wise grades, and the KEY. Added because the list above once
    # claimed "every kind is here" while covering four of eleven — the six
    # below reached both renderers without ever being drawn in a browser.
    # -----------------------------------------------------------------------
    ("exposure +1 stop", [fx("exposure", stops=1.0)]),
    ("exposure -1 stop", [fx("exposure", stops=-1.0)]),
    ("gamma 2.2", [fx("gamma", gamma=2.2)]),
    ("gamma 0.5", [fx("gamma", gamma=0.5)]),
    # ⚠ NOT A NO-OP CASE. gamma 0 is the value a slider dragged to its floor
    # sends, and 1/0 is the difference between a clamp and a black frame.
    ("gamma 0 (clamped, not a black frame)", [fx("gamma", gamma=0.0)]),
    ("temperature warm", [fx("temperature", temperature=0.5, tint=0.0)]),
    ("temperature cool with tint", [fx("temperature", temperature=-0.4, tint=0.25)]),
    ("hue 120", [fx("hue", degrees=120.0)]),
    ("hue -90", [fx("hue", degrees=-90.0)]),
    ("sepia full", [fx("sepia", amount=1.0)]),
    ("sepia half", [fx("sepia", amount=0.5)]),
    ("posterize 4", [fx("posterize", levels=4)]),
    ("posterize 2", [fx("posterize", levels=2)]),
    ("chroma key that misses", [fx("chroma", color="#00ff00", similarity=0.35,
                                   smoothness=0.08, spill=0.0)]),
    # -----------------------------------------------------------------------
    # ⚠ FRESHLY DROPPED, ONE PER KIND: `params` is `{}`, which is EXACTLY what
    # the Effects library's drag-and-drop and the pane's "Add" both write. Every
    # value has to come from `EFFECT_PARAMS`, and a kind whose default does not
    # reach the shader shows up here as a black or a flat frame — the bug this
    # block was written for. A value case cannot catch it: it passes its own
    # numbers in, so it never asks where a default comes from.
    # -----------------------------------------------------------------------
    ("freshly dropped: brightness", [fx("brightness")]),
    ("freshly dropped: contrast", [fx("contrast")]),
    ("freshly dropped: saturation", [fx("saturation")]),
    ("freshly dropped: chroma", [fx("chroma")]),
    ("freshly dropped: exposure", [fx("exposure")]),
    ("freshly dropped: gamma", [fx("gamma")]),
    ("freshly dropped: temperature", [fx("temperature")]),
    ("freshly dropped: hue", [fx("hue")]),
    ("freshly dropped: sepia", [fx("sepia")]),
    ("freshly dropped: posterize", [fx("posterize")]),
    # The two the user dropped together, in the order the report described.
    ("freshly dropped: gamma then exposure", [fx("gamma"), fx("exposure")]),
    # Back to nothing, from a chain that had a LUT in it. This is the transition
    # that used to run `dispose()` with a LUT texture in the map.
    ("and back to no effects", []),
]


def expected(effects):
    """What the export makes of the same card. The Python side is the reference."""
    img = Image.new("RGBA", (8, 8), CARD_RGB + (255,))
    out = np.asarray(apply_effects(img, effects))
    return tuple(int(v) for v in out[4, 4, :3])


# ---------------------------------------------------------------------------
# The probe page
# ---------------------------------------------------------------------------
# Mounts the REAL <ProgramCanvas> on a REAL scene resolved by the REAL `sceneAt`,
# and drives it by setting the clip's effects — which is what the Effects pane
# does and nothing more. The compositor is taken off the prototype because the
# component keeps it in a ref: `end` is where a finished frame exists, and
# `dispose` is counted because a count above zero IS the bug.
PROBE_JSX_SOURCE = r"""
import React, { useState } from "react";
import { createRoot } from "react-dom/client";

import { Compositor } from "/src/animatic/gl/compositor.js";
import ProgramCanvas from "/src/components/ProgramCanvas.jsx";
import { sceneAt } from "/src/animatic/scene.js";

const probe = { renders: 0, disposes: 0, errors: [], ready: false };
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

const SETTINGS = { fit: "contain", background: "#000000", aspect_ratio: "16:9" };

function Harness() {
  probe.renders += 1;
  const [effects, setEffects] = useState([]);
  probe.setEffects = setEffects;
  // A COLOUR CARD, so there is no image to load, no fit and no resample — the
  // read-back value is the card's colour with the chain applied and nothing else.
  const frames = [{ id: "f1", kind: "color", color: "__CARD__",
                    duration_ms: 2000, effects }];
  const scene = sceneAt({ frames, texts: [], shapes: [], overlays: [],
                          transitions: [], settings: SETTINGS }, 0);
  return (
    <ProgramCanvas
      scene={scene}
      frames={frames}
      urls={{}}
      videoUrls={{}}
      overlayUrls={{}}
      settings={SETTINGS}
      videoElsRef={{ current: {} }}
      onUnavailable={(e) => { probe.unavailable = String(e); }}
    />
  );
}

createRoot(document.getElementById("root")).render(<Harness />);

/** The centre pixel of the finished frame, straight out of the framebuffer. */
probe.read = () => {
  const comp = probe.compositor;
  if (!comp || !document.querySelector("canvas")) return null;
  const [w, h] = comp.size;
  const px = comp.readPixels();
  const i = (Math.round(h / 2) * w + Math.round(w / 2)) * 4;
  return {
    rgb: [px[i], px[i + 1], px[i + 2]],
    glError: comp.gl.getError(),
    renders: probe.renders,
    disposes: probe.disposes,
    errors: probe.errors.slice(),
    canvas: Boolean(document.querySelector("canvas")),
  };
};

/** `dispose()` with a LUT texture in the map — the exact call that threw. */
probe.disposeIsSafe = () => {
  try {
    const comp = probe.compositor;
    if (!comp) return "no compositor";
    if (!comp.luts.size) return "no LUT was uploaded — the check would prove nothing";
    comp.dispose();
    return true;
  } catch (e) {
    return String(e && e.message || e);
  }
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<meta charset="utf-8">
<title>monitor effects probe</title>
<style>
  html, body { margin: 0; background: #000; }
  #root { width: 320px; height: 180px; position: relative; }
  #root canvas { width: 100%; height: 100%; display: block; }
</style>
<div id="root"></div>
<script type="module" src="/__probe_monitor.jsx"></script>
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def lut_text(name):
    path = os.path.join(LUT_DIR, f"{name}.cube")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def route_luts(route, request):
    """Answer the LUT endpoints off `luts/` — the same bytes the server serves."""
    url = request.url
    match = re.search(r"/animatics/luts/([^/?]+)$", url)
    if match:
        text = lut_text(match.group(1))
        if text is None:
            route.fulfill(status=404, content_type="application/json",
                          body=json.dumps({"detail": "no such LUT"}))
        else:
            route.fulfill(status=200, content_type="text/plain", body=text)
        return
    if url.rstrip("/").endswith("/animatics/luts"):
        names = sorted(f[:-5] for f in os.listdir(LUT_DIR) if f.endswith(".cube"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(names))
        return
    route.continue_()


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
        fh.write(PROBE_JSX_SOURCE.replace("__CARD__", CARD))
    with open(PROBE_HTML, "w", encoding="utf-8") as fh:
        fh.write(PROBE_HTML_SOURCE)

    port = free_port()
    vite = None
    try:
        vite = start_vite(port)
        if vite is None:
            print("  Vite would not start — cannot drive the monitor.")
            return 2

        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=[
                # SwiftShader, so this runs the same on a CI box with no GPU as
                # it does on a laptop. It is a real GL driver, not a stub.
                "--use-gl=angle", "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            ])
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.route("**/animatics/luts**", route_luts)
            page.goto(f"http://127.0.0.1:{port}/__probe_monitor.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_function("window.__probe.read() !== null", timeout=30000)

            print("\nEvery effect, turned on the way the Effects pane turns it on")
            last = None
            for label, effects in CASES:
                page.evaluate("(e) => window.__probe.setEffects(e)", effects)
                # The LUTs are fetched, so a look that names one needs a beat
                # before the monitor has the table. This is the SAME wait the
                # editor makes the user do; the picture is ungraded until then.
                names = [e["params"].get("name") for e in effects
                         if e["kind"] == "lut" and e.get("params")]
                page.wait_for_timeout(900 if names else 250)
                got = page.evaluate("() => window.__probe.read()")
                last = got

                if got is None or not got["canvas"]:
                    check(f"{label} — the monitor is still there", False, "canvas is gone")
                    continue
                want = expected(effects)
                delta = max(abs(a - b) for a, b in zip(got["rgb"], want))
                check(
                    f"{label:34} → {tuple(got['rgb'])}",
                    delta <= TOLERANCE,
                    f"export says {want}, monitor says {tuple(got['rgb'])}, Δ{delta}",
                )
                check(f"{label:34}   no GL error", got["glError"] == 0, str(got["glError"]))

            print("\nWhat the black screen actually was")
            check("nothing reached window.onerror or console.error",
                  last is not None and not last["errors"],
                  "; ".join(last["errors"][:3]) if last and last["errors"] else "")
            # ⚠ THE ONE THAT CATCHES THE REGRESSION. `dispose()` is the monitor
            # being torn down; a count above zero means the context is being
            # rebuilt as the user works, and it was the vehicle for the throw.
            check("the GL context was built once, not rebuilt per render",
                  last is not None and last["disposes"] == 0,
                  f"{last['disposes']} disposes over {last['renders']} renders"
                  if last else "")
            check("WebGL never reported itself unavailable",
                  page.evaluate("() => window.__probe.unavailable || null") is None)

            # Last, because it destroys the compositor it is testing.
            page.evaluate("(e) => window.__probe.setEffects(e)",
                          [fx("lut", name="noir", amount=1.0)])
            page.wait_for_timeout(900)
            verdict = page.evaluate("() => window.__probe.disposeIsSafe()")
            check("dispose() is safe once a LUT texture has been uploaded",
                  verdict is True, str(verdict))

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
    print("The monitor draws every effect, and survives every one being switched on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
