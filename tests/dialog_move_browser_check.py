"""dialog_move_browser_check.py — the dialogs, driven with a real mouse.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/dialog_move_browser_check.py

No backend: Vite is started here and the page mounts a plain dialog of the same
shape every dialog in this app has (`.modal-overlay` → a card → a `modal-close`
✕ → an `h2`), with `installDialogMove()` wired in exactly as `App.jsx` wires it.

⚠ **`dialog_frame_check.py` CANNOT SEE ANY OF THIS.** That file reads the source
and proves no dialog carries a backdrop `onClick` and that the shared module
says the right things. It would stay green if `installDialogMove()` never
attached a listener, if the capture phase swallowed the ✕'s own click, or if the
clamp were off by a sign and every drag pinned the card to a corner. RULEBOOK
**G7**: a green build and a green grep are not evidence that a screen behaves.

So the questions here are the ones a hand would ask:

    1. does a click on the dark area really leave the dialog alone
    2. does dragging the heading really move it, and does it stay where dropped
    3. can it be dragged off the screen — it must NOT be, ✕ and all
    4. do the controls inside still work (the ✕, and typing in a field)
    5. does the next dialog open in the middle again, not where the last was
       shoved

⚠ **AND THE PROBE PAGE IS WRITTEN INTO `client/` AND DELETED AGAIN**, for the
reason the other browser checks give: Vite serves its own root and nothing above
it, so a harness in a temp directory is outside `server.fs.allow` and refused.
"""

import os
import shutil
import socket
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")
PROBE_HTML = os.path.join(CLIENT, "__probe_dialogmove.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_dialogmove.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ⚠ THE DIALOG HERE IS DELIBERATELY PLAIN. The point is the SHARED behaviour, so
# the probe uses the markup every dialog in the app shares rather than importing
# one particular screen — a change that broke every dialog but the imported one
# would otherwise pass.
PROBE_JSX_SOURCE = r"""
import React from "react";
import { createRoot } from "react-dom/client";

import { installDialogMove } from "/src/dialog_move.js";
import "/src/styles/index.css";

const probe = { closes: 0, typed: "" };
window.__probe = probe;

function Dialog({ onClose }) {
  return (
    <div className="modal-overlay">
      <div className="card an-name-modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>
          ✕
        </button>
        <h2>A dialog with something in it</h2>
        <p className="muted">
          Half-typed work lives in here, and it is saved nowhere at all.
        </p>
        <input
          className="probe-field"
          onChange={(e) => {
            probe.typed = e.target.value;
          }}
        />
      </div>
    </div>
  );
}

function Harness() {
  const [open, setOpen] = React.useState(true);
  React.useEffect(() => installDialogMove(), []);
  probe.reopen = () => setOpen(true);
  return (
    <>
      <button className="probe-open" onClick={() => setOpen(true)}>
        Open
      </button>
      {open && (
        <Dialog
          onClose={() => {
            probe.closes += 1;
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

createRoot(document.getElementById("root")).render(<Harness />);

probe.box = () => {
  const el = document.querySelector(".modal-overlay .card");
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.left), y: Math.round(r.top),
           w: Math.round(r.width), h: Math.round(r.height) };
};
probe.isOpen = () => Boolean(document.querySelector(".modal-overlay"));
probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>dialog move probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_dialogmove.jsx"></script>
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
        if "Local:" in line or "ready in" in line:
            return proc
    return proc if proc.poll() is None else None


def drag(page, start, delta, steps=12):
    page.mouse.move(*start)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(start[0] + delta[0] * i / steps,
                        start[1] + delta[1] * i / steps)
    page.mouse.up()


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
            print("  Vite would not start — cannot drive the dialog.")
            return 2

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/__probe_dialogmove.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_selector(".modal-overlay .card", timeout=30000)

            # -----------------------------------------------------------------
            print("\n1 · the click that used to throw the work away")
            # -----------------------------------------------------------------
            page.fill(".probe-field", "half-typed work")
            # The far corner of the dark area — as far from the card as the
            # window allows, which is exactly where a slipped mouse lands.
            page.mouse.click(40, 860)
            page.wait_for_timeout(150)
            check("a click on the backdrop does not close the dialog",
                  page.evaluate("() => window.__probe.isOpen()"), "the dialog is gone")
            check("…and nothing inside it was lost",
                  page.input_value(".probe-field") == "half-typed work",
                  page.input_value(".probe-field"))
            check("…and onClose was never called",
                  page.evaluate("() => window.__probe.closes") == 0,
                  str(page.evaluate("() => window.__probe.closes")))

            # -----------------------------------------------------------------
            print("\n2 · so it moves instead")
            # -----------------------------------------------------------------
            before = page.evaluate("() => window.__probe.box()")
            head = page.locator(".modal-overlay .card h2").bounding_box()
            drag(page, (head["x"] + 40, head["y"] + head["height"] / 2), (220, 160))
            after = page.evaluate("() => window.__probe.box()")
            check("dragging the heading moves the dialog",
                  abs(after["x"] - before["x"] - 220) <= 4
                  and abs(after["y"] - before["y"] - 160) <= 4,
                  f"{before} -> {after}")
            check("…and it stays where it was dropped",
                  page.evaluate("() => window.__probe.box()") == after, "it sprang back")
            check("…without being resized on the way",
                  (after["w"], after["h"]) == (before["w"], before["h"]),
                  f"{before} -> {after}")

            # ⚠ THE ONE THAT MATTERS MOST. A dialog dragged off the edge would be
            # unreachable — ✕ included — and these dialogs have no other exit.
            # -----------------------------------------------------------------
            print("\n3 · and it cannot be thrown off the screen")
            # -----------------------------------------------------------------
            head = page.locator(".modal-overlay .card h2").bounding_box()
            drag(page, (head["x"] + 40, head["y"] + head["height"] / 2), (-3000, 3000))
            box = page.evaluate("() => window.__probe.box()")
            check("a strip stays on screen when dragged to the left",
                  box["x"] + box["w"] > 0, str(box))
            check("…and it never sinks past the bottom",
                  box["y"] < 900, str(box))
            head = page.locator(".modal-overlay .card h2").bounding_box()
            drag(page, (head["x"] + 40, head["y"] + head["height"] / 2), (3000, -3000))
            box = page.evaluate("() => window.__probe.box()")
            check("…nor off the right", box["x"] < 1200, str(box))
            # The heading IS the handle, so the top edge must stay grabbable or
            # the card cannot be brought back.
            check("…and never above the top, where the handle is",
                  box["y"] >= -1, str(box))

            # -----------------------------------------------------------------
            print("\n4 · the controls inside still work")
            # -----------------------------------------------------------------
            page.fill(".probe-field", "still typeable")
            check("a field inside a moved dialog still takes typing",
                  page.evaluate("() => window.__probe.typed") == "still typeable",
                  page.evaluate("() => window.__probe.typed"))
            page.click(".modal-overlay .modal-close")
            page.wait_for_timeout(150)
            check("the ✕ still closes it",
                  not page.evaluate("() => window.__probe.isOpen()"), "still open")

            # -----------------------------------------------------------------
            print("\n5 · the next one opens in the middle again")
            # -----------------------------------------------------------------
            # ⚠ A dialog that came back where it was last shoved reads as broken.
            page.click(".probe-open")
            page.wait_for_selector(".modal-overlay .card", timeout=5000)
            fresh = page.evaluate("() => window.__probe.box()")
            check("a reopened dialog is centred, not where it was dropped",
                  abs(fresh["x"] - (1200 - fresh["w"]) / 2) <= 4, str(fresh))

            browser.close()
    finally:
        if vite is not None:
            vite.terminate()
        for path in (PROBE_HTML, PROBE_JSX):
            try:
                os.remove(path)
            except OSError:
                pass

    print("\n" + ("FAILED: " + "; ".join(failures) if failures
                  else "All dialog-move browser checks passed."))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
