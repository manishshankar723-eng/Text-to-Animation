"""EVERY NUMBER IN A PROPERTIES PANE IS A DRAG HANDLE — in a real browser.

The report this was written for:

    "i wand add scal function in shapes clip in properties like other

     i want most inprotent you add fuction when my mouse crouser go on scal name
     or value box so user do drag left so increase velue and go right so decrease
     value so this type of fuction and this type of fuction add other fuction
     like position, opecity etc those in this tyle of contoler of add in all
     properties panel"

(The direction was checked back and settled the other way — RIGHT increases,
which is Premiere, After Effects, Blender and Figma, and what the rest of this
project already matches.)

⚠ NO UNIT TEST COULD COVER THIS, which is why it drives Chromium. The scrub is a
gesture: its whole contract is about the difference between a press that stays
still and a press that moves, about a pointer released outside the box it started
in, and about a value measured from where the drag began rather than accumulated.
Every one of those is a statement about events, not about arithmetic — and the
one that matters most is the NEGATIVE one:

    ⚠ A CLICK MUST STILL BE A CLICK.

Before this, every number in the pane was type-only. If adding the drag cost the
click, the feature would have taken away the thing the control was FOR, and no
amount of "the drag works" would make up for it. So the first assertions here are
that a press with no movement changes nothing and still focuses the box for
typing, and they are first on purpose.

The pane under test is `ShapeProperties`, mounted on its own rather than through
`AnimaticEditor` — it needs no backend, and it carries one of every row shape
this file cares about: plain `NumField`s (Position X, Rotation), the new `Scale`
row, and a `PropSlider` (Opacity) whose LABEL scrubs while its track drags.
`ScrubGesture` is provided the way the editor provides it, so the undo bracket is
exercised too, and `kf` is stubbed so the ⏱ renders and reports which property it
was asked to key.

⚠ THE SCALE SECTION IS ALSO THE RECORD OF A DESIGN THAT WAS WRONG ONCE. Scale
shipped as a shortcut that wrote `w` and `h` together, which meant it could not
carry a ⏱ — one control cannot honestly keyframe two properties. That was asked
for within the hour, so `scale` is a stored, animatable field now and these
checks pin the difference: typing into it writes `scale` and NOTHING else, Width
and Height keep reading what they were set to, and its ⏱ keys `scale`.
"""

import json
import os
import socket
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

# Screenshots go to `test_shots/`, which git ignores — never the repo
# root. See `tests/_shots.py`.
from _shots import shot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_scrub.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_scrub.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# A shape at exactly the size `SHAPE_GEO` creates one at, so "Scale reads 100%"
# is a statement about a FRESH clip rather than about this fixture.
SHAPE = {
    "id": "s1",
    "kind": "rect",
    "start_ms": 0,
    "duration_ms": 2000,
    "x": 0.5,
    "y": 0.5,
    "w": 0.25,
    "h": 0.25,
    "color": "#c2185b",
    "opacity": 1.0,
    "rotation": 0.0,
    "keyframes": {},
}

PROBE_JSX_SOURCE = r"""
import React from "react";
import { createRoot } from "react-dom/client";

import ShapeProperties from "/src/components/properties/ShapeProperties.jsx";
import { ScrubGesture } from "/src/components/properties/PropGroup.jsx";
import "/src/styles/index.css";

const probe = { errors: [], gestures: 0, toggled: [] };
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

// Counts the undo bracket the editor would open. One SCRUB must open exactly
// one, however many pointer-moves it is made of — that is rule 4.
const gestureProps = { onPointerDown: () => { probe.gestures += 1; } };

const START = __SHAPE__;

function Harness() {
  const [shape, setShape] = React.useState(START);
  probe.shape = shape;
  probe.reset = () => setShape(START);
  probe.set = (patch) => setShape((was) => ({ ...was, ...patch }));
  return (
    <ScrubGesture.Provider value={gestureProps}>
      <div className="an-props-host" style={{ width: 340, padding: 12 }}>
        <ShapeProperties
          shape={shape}
          totalMs={8000}
          kf={{
            clip: shape,
            tRel: 0,
            // Enough of the real contract for the stopwatch to render and
            // report. The pane only ever CALLS these, so a stub that records
            // WHICH property was asked for answers the whole question here:
            // does Scale key `scale`, or does it key something else?
            onToggle: (prop) => { probe.toggled.push(prop); },
            onKey: () => {},
            onSeekKey: () => {},
            onEase: () => {},
          }}
          gesture={gestureProps}
          onChange={(id, patch) => setShape((was) => ({ ...was, ...patch }))}
          onDuplicate={() => {}}
          onDelete={() => {}}
          onClose={() => {}}
        />
      </div>
    </ScrubGesture.Provider>
  );
}

createRoot(document.getElementById("root")).render(<Harness />);

/** The row whose LABEL reads exactly `name`. */
probe.row = (name) =>
  Array.from(document.querySelectorAll(".an-row")).find(
    (el) => (el.querySelector(".an-row-label")?.textContent || "").trim() === name
  ) || null;

probe.labelBox = (name) => {
  const el = probe.row(name)?.querySelector(".an-row-label");
  if (!el) return null;
  el.scrollIntoView({ block: "center" });
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
};

probe.fieldBox = (name) => {
  const el = probe.row(name)?.querySelector("input");
  if (!el) return null;
  el.scrollIntoView({ block: "center" });
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
};

probe.fieldValue = (name) => probe.row(name)?.querySelector("input")?.value ?? null;
probe.cursorOf = (name, part) => {
  const el = probe.row(name)?.querySelector(part === "label" ? ".an-row-label" : "input");
  return el ? getComputedStyle(el).cursor : "absent";
};
probe.focusedIsFieldOf = (name) =>
  document.activeElement === probe.row(name)?.querySelector("input");
probe.bodyScrubbing = () => document.body.classList.contains("an-scrubbing");
probe.hasWatch = (name) => !!probe.row(name)?.querySelector(".an-kf-watch");
probe.pressWatch = (name) => {
  const el = probe.row(name)?.querySelector(".an-kf-watch");
  if (!el) return false;
  el.click();
  return true;
};
probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>properties scrub probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_scrub.jsx"></script>
</body></html>
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


def drag(page, spot, dx, steps=12, modifier=None):
    """Press at `spot`, move `dx` px sideways in `steps`, release.

    ⚠ MOVED IN STEPS, NOT IN ONE JUMP. A single move from the origin to the end
    would clear the dead zone and deliver the whole distance in one event, which
    is precisely the case a broken accumulating scrub also gets right. Several
    moves is what tells "measured from the origin" apart from "added up".

    ⚠ AND RELEASED WITH A `pointerup` ON THE WINDOW. `page.mouse.up()` sends it
    wherever the pointer now is, which is the case the implementation has to
    survive — the listener is on the window for exactly this reason.
    """
    page.mouse.move(spot["x"], spot["y"])
    page.mouse.down()
    if modifier:
        page.keyboard.down(modifier)
    for i in range(1, steps + 1):
        page.mouse.move(spot["x"] + dx * i / steps, spot["y"])
        page.wait_for_timeout(8)
    if modifier:
        page.keyboard.up(modifier)
    page.mouse.up()
    page.wait_for_timeout(60)


def main():
    if not os.path.isdir(os.path.join(CLIENT, "node_modules")):
        print("  client/node_modules is missing — run `cd client && npm install` first.")
        return 2

    with open(PROBE_JSX, "w", encoding="utf-8") as fh:
        fh.write(PROBE_JSX_SOURCE.replace("__SHAPE__", json.dumps(SHAPE)))
    with open(PROBE_HTML, "w", encoding="utf-8") as fh:
        fh.write(PROBE_HTML_SOURCE)

    port = free_port()
    vite = None
    try:
        vite = start_vite(port)
        if vite is None:
            print("  vite would not start — the pane was NOT driven.")
            return 2

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 520, "height": 1000})
            page.goto(f"http://127.0.0.1:{port}/__probe_scrub.html")
            page.wait_for_function("() => window.__probe && window.__probe.ready",
                                   timeout=60000)
            page.wait_for_timeout(300)

            def shape():
                return page.evaluate("() => window.__probe.shape")

            def reset():
                page.evaluate("() => window.__probe.reset()")
                page.wait_for_timeout(60)

            def label(name):
                return page.evaluate("(n) => window.__probe.labelBox(n)", name)

            def field(name):
                return page.evaluate("(n) => window.__probe.fieldBox(n)", name)

            # ---------------------------------------------------------------
            print("\nThe pane mounts, and the new row is on it\n")
            check("ShapeProperties renders", bool(shape()), json.dumps(shape())[:200])
            check("there is a Scale row now",
                  page.evaluate("() => !!window.__probe.row('Scale')"))
            check("...and a fresh shape reads 100%",
                  page.evaluate("() => window.__probe.fieldValue('Scale')") == "100",
                  page.evaluate("() => window.__probe.fieldValue('Scale')"))

            # ---------------------------------------------------------------
            print("\n⚠ A CLICK IS STILL A CLICK — the behaviour that must not regress\n")
            spot = field("Position X")
            page.mouse.click(spot["x"], spot["y"])
            page.wait_for_timeout(80)
            check("pressing a number box changes NOTHING", shape()["x"] == 0.5,
                  f"x={shape()['x']}")
            check("...and focuses it, so typing an exact value still works",
                  page.evaluate("() => window.__probe.focusedIsFieldOf('Position X')"))
            page.keyboard.press("Control+a")
            page.keyboard.type("30")
            page.wait_for_timeout(80)
            check("...and what is typed lands", abs(shape()["x"] - 0.30) < 1e-9,
                  f"x={shape()['x']}")
            check("no drag was started, so the undo stack was never bracketed",
                  page.evaluate("() => window.__probe.gestures") == 0)
            reset()

            # ---------------------------------------------------------------
            print("\nDragging the VALUE BOX\n")
            before = page.evaluate("() => window.__probe.gestures")
            drag(page, field("Position X"), 60)
            after_right = shape()["x"]
            check("dragging RIGHT increases the value", after_right > 0.5,
                  f"x={after_right}")
            check("...by the distance dragged, not by a jump",
                  0.60 < after_right < 0.72,
                  f"60px at 3px per 1% should be ~+20% — got {round(after_right * 100)}%")
            check("...as ONE undo entry, however many moves it took",
                  page.evaluate("() => window.__probe.gestures") - before == 1,
                  f"opened {page.evaluate('() => window.__probe.gestures') - before}")
            reset()
            drag(page, field("Position X"), -60)
            check("dragging LEFT decreases it", shape()["x"] < 0.5, f"x={shape()['x']}")
            check("...by the same amount, mirrored",
                  abs((0.5 - shape()["x"]) - (after_right - 0.5)) < 0.02,
                  f"right +{round((after_right - 0.5) * 100)}%, "
                  f"left −{round((0.5 - shape()['x']) * 100)}%")
            reset()

            # ---------------------------------------------------------------
            print("\nDragging the LABEL — the same gesture on the word\n")
            check("a scrubbable label says so with the cursor",
                  page.evaluate("() => window.__probe.cursorOf('Position X', 'label')")
                  == "ew-resize",
                  page.evaluate("() => window.__probe.cursorOf('Position X', 'label')"))
            check("...and so does the box",
                  page.evaluate("() => window.__probe.cursorOf('Position X', 'field')")
                  == "ew-resize")
            check("a row with no number on it does NOT pretend to be draggable",
                  page.evaluate("() => window.__probe.cursorOf('Fill', 'label')")
                  != "ew-resize",
                  page.evaluate("() => window.__probe.cursorOf('Fill', 'label')"))
            drag(page, label("Position Y"), 60)
            check("dragging the LABEL scrubs the row it belongs to",
                  shape()["y"] > 0.5, f"y={shape()['y']}")
            check("...and it moved the row's OWN property and no other",
                  shape()["x"] == 0.5, f"x={shape()['x']}")
            reset()

            # ---------------------------------------------------------------
            print("\nThe drag is measured from where it started\n")
            spot = field("Rotation")
            page.mouse.move(spot["x"], spot["y"])
            page.mouse.down()
            for x in (spot["x"] + 200, spot["x"] + 400, spot["x"]):
                page.mouse.move(x, spot["y"])
                page.wait_for_timeout(20)
            page.mouse.up()
            page.wait_for_timeout(60)
            check("dragging out and back returns to the value you started at",
                  abs(shape()["rotation"]) < 1e-9,
                  f"rotation={shape()['rotation']} — an accumulating scrub never comes back")
            reset()
            # Rotation is min -360 / max 360 with a step of 5, so it is SWEPT:
            # a very long drag has to stop at the end rather than run past it.
            drag(page, field("Rotation"), 2000, steps=20)
            check("a drag past the end of a bounded property clamps",
                  shape()["rotation"] == 360, f"rotation={shape()['rotation']}")
            reset()

            # ---------------------------------------------------------------
            print("\nModifiers, and a slider's label\n")
            drag(page, field("Position X"), 60, modifier="Alt")
            fine = abs(shape()["x"] - 0.5)
            check("Alt makes it fine — a tenth of the travel",
                  0 < fine < 0.05, f"moved {round(fine * 100, 2)}%")
            reset()
            drag(page, label("Opacity"), -60)
            check("a slider's LABEL scrubs too, not just its track",
                  shape()["opacity"] < 1, f"opacity={shape()['opacity']}")
            check("...and stops at the bottom of its range",
                  shape()["opacity"] >= 0, f"opacity={shape()['opacity']}")
            reset()

            # ---------------------------------------------------------------
            print("\nScale — a multiplier on Width and Height\n")
            page.evaluate("() => window.__probe.set({ w: 0.25, h: 0.25, scale: 1 })")
            page.wait_for_timeout(60)
            check("a fresh shape reads 100%",
                  page.evaluate("() => window.__probe.fieldValue('Scale')") == "100",
                  page.evaluate("() => window.__probe.fieldValue('Scale')"))
            spot = field("Scale")
            page.mouse.click(spot["x"], spot["y"])
            page.keyboard.press("Control+a")
            page.keyboard.type("200")
            page.wait_for_timeout(80)
            check("typing 200% writes `scale`, and ONLY `scale`",
                  abs(shape()["scale"] - 2) < 1e-6
                  and shape()["w"] == 0.25 and shape()["h"] == 0.25,
                  f"scale={shape()['scale']} w={shape()['w']} h={shape()['h']}")
            # ⚠ WIDTH AND HEIGHT ARE THE SIZE **BEFORE** THE SCALE — the same
            # relationship a frame's Scale has to its picture and Premiere's has
            # to its source. If they moved too, the rows would be describing the
            # same thing twice, and the shortcut this replaced is exactly what
            # that looks like.
            check("...so Width and Height still read what they were set to",
                  page.evaluate("() => window.__probe.fieldValue('Width')") == "25",
                  page.evaluate("() => window.__probe.fieldValue('Width')"))
            drag(page, label("Scale"), 60)
            check("and Scale scrubs like every other number", shape()["scale"] > 2,
                  f"scale={shape()['scale']}")

            # ---------------------------------------------------------------
            # ⚠ THE REASON `scale` STOPPED BEING A SHORTCUT. The first version of
            # this row wrote `w` and `h` together and had no ⏱, because one
            # control cannot honestly key two properties — rule 1 in
            # `PropGroup.jsx`, and the timeline had no row to draw it on. It was
            # asked for within the hour ("i want you add in scale in Key
            # buttun"), so `scale` is a stored animatable property now, and
            # these are the assertions that say so.
            print("\nScale carries a ⏱, like every other transform row\n")
            for name in ("Position X", "Position Y", "Scale", "Width", "Height",
                         "Opacity", "Rotation"):
                check(f"{name} has a keyframe button",
                      page.evaluate("(n) => window.__probe.hasWatch(n)", name))
            pressed = page.evaluate("(n) => window.__probe.pressWatch(n)", "Scale")
            page.wait_for_timeout(60)
            check("...and Scale's keys ITS OWN property, not `w` and `h`",
                  pressed and page.evaluate("() => window.__probe.toggled") == ["scale"],
                  json.dumps(page.evaluate("() => window.__probe.toggled")))

            # ---------------------------------------------------------------
            print("\nAfterwards\n")
            check("the drag class is cleaned off <body> when the pointer is released",
                  not page.evaluate("() => window.__probe.bodyScrubbing()"))
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:400])
            if failures:
                page.screenshot(path=shot("scrub_probe_failed.png"))
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
    print("Every number in the pane drags, and every click still types.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
