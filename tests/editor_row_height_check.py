"""ONE ROW AT A TIME, BY THE SEAM UNDER IT — in the real editor.

    python tests/editor_row_height_check.py

The report:

    "First i want keep Key size little big in clip and second add fuction like
     this my ref image: when user mouse go two layer in betwen then he do layer
     height change samll and big, like my Four panel move program, media,
     properties and time — i want somthing like this in timline"

Two things, and the second is the reason the first is worth doing.

⚠ THE TIMELINE HAD ONE HEIGHT FOR EVERY ROW. `--tl-track-h` was a single number
driven by the vertical scroll bar's grips, and the rule at the top of
`animatic-lanes.css` says why it was one number: "do not give one kind of lane
its own height — that is what put every label beside the wrong track once
before". That rule is about a KIND of lane being drawn taller on ONE column. Per
ROW, applied to BOTH columns from the same number, the invariant it protects is
untouched — and this file is the proof: every check below reads the GUTTER box
and the TRACK box and requires them to agree.

⚠ AND THE KEYFRAME DIAMONDS ARE A FRACTION OF THE ROW NOW, so "the keys are too
small" has an answer the user can act on: drag the row taller. That is checked
here too — a diamond measured before and after a resize, and every key still
inside its own lane afterwards.

⚠ IT IS THE SAME `PaneSplitter` THE WORKSPACE SEAMS USE, which is what was asked
for ("like my four panel move"). So the drag, the arrow-key nudge and the
double-click-to-reset are one implementation, and this file drives all three
through the mouse and the keyboard rather than trusting the component twice.

No backend: every call the editor makes is answered off the fixture below.
"""

import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from PIL import Image
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # noqa: BLE001
    print(f"  This check needs playwright and pillow ({exc}).")
    raise SystemExit(2) from None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")
PROBE_HTML = os.path.join(CLIENT, "__probe_rowheight.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_rowheight.jsx")

NL = "\n"
failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The stubbed project
# ---------------------------------------------------------------------------
# ⚠ A CLIP THAT IS ANIMATED, because half of this file is about the keyframe
# diamonds and a clip with no keys draws none. Four properties is the FRAME
# budget in `animatic-text.css` — the case its arithmetic is written against.
FREE_START_MS = 1000
FREE_LEN_MS = 4000
KEYS = {
    prop: [{"t": 0, "v": 0.4, "ease": "ease-in-out"},
           {"t": 4000, "v": 1.0, "ease": "linear"}]
    for prop in ("scale", "x", "y", "opacity")
}

PROJECT = {
    "id": "probe",
    "title": "probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [
        {"id": "f1", "kind": "image", "src": {"kind": "upload", "upload_id": "u1"},
         "duration_ms": 4000, "label": "Shot 1", "url": "/animatics/probe/media/u1",
         "keyframes": KEYS},
        {"id": "f2", "kind": "image", "src": {"kind": "upload", "upload_id": "u2"},
         "duration_ms": 4000, "label": "Shot 2", "url": "/animatics/probe/media/u2"},
    ],
    "texts": [{"id": "t1", "text": "caption", "start_ms": FREE_START_MS,
               "duration_ms": FREE_LEN_MS, "layer_id": "", "group_id": ""}],
    "shapes": [{"id": "s1", "kind": "rect", "start_ms": FREE_START_MS,
                "duration_ms": FREE_LEN_MS, "layer_id": "", "group_id": ""}],
    "layers": [
        {"id": "L_img1", "kind": "image", "name": "Picture layer"},
        {"id": "L_aud1", "kind": "audio", "name": "Sound"},
    ],
    "overlays": [{"id": "o1", "upload_id": "u2", "layer_id": "L_img1", "group_id": "",
                  "start_ms": FREE_START_MS, "duration_ms": FREE_LEN_MS,
                  "url": "/animatics/probe/media/u2"}],
    "transitions": [],
    "audio_tracks": [
        {"id": "a1", "upload_id": "u3", "filename": "sound.wav",
         "layer_id": "L_aud1", "group_id": "", "duration_ms": 8000,
         "start_ms": 0, "offset_ms": 0, "trim_ms": 8000,
         "volume": 1.0, "muted": False, "url": "/animatics/probe/media/u3"},
    ],
    "veo_clips": [], "video": None,
}


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


def wav_bytes(ms=8000, rate=8000):
    """A silent mono WAV, written by hand — no ffmpeg, so no binary to install."""
    data = b"\x00\x00" * int(rate * ms / 1000)
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


MEDIA = {
    "u1": ("image/png", picture_bytes((74, 134, 200))),
    "u2": ("image/png", picture_bytes((200, 120, 60))),
    "u3": ("audio/wav", wav_bytes()),
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
        if found is None:
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

const round = (n) => Math.round(n * 10) / 10;

/**
 * EVERY ROW, MEASURED ON BOTH COLUMNS — the whole question this file asks.
 *
 * ⚠ THE GUTTER BOX AND THE TRACK BOX ARE READ SEPARATELY AND BOTH RETURNED,
 * never one and "the other must match". The failure this guards against is
 * precisely the two columns disagreeing, so a helper that measured one of them
 * and assumed the other would be a helper that cannot see the bug.
 */
probe.rowBoxes = () => {
  const out = {};
  for (const row of document.querySelectorAll("[data-lane-row]")) {
    const key = row.dataset.lane;
    const gr = row.getBoundingClientRect();
    out[row.dataset.laneRow] = { gutter: { h: round(gr.height), top: round(gr.top) } };
  }
  for (const lane of document.querySelectorAll("[data-lane]")) {
    const r = lane.getBoundingClientRect();
    const at = out[lane.dataset.lane] || (out[lane.dataset.lane] = {});
    at.track = { h: round(r.height), top: round(r.top) };
  }
  return out;
};

/**
 * The ROW KEY each seam belongs to, in draw order.
 *
 * ⚠ READ OFF THE DOM STRUCTURE, NOT OFF THE aria-label. The label is
 * "<name> height" and a name is not a key — two rows can be called the same
 * thing, and `rowBoxes` is keyed the way the editor keys rows. The seam is the
 * sibling of its row inside `.tl-gutter-stack`, which is exactly the
 * relationship "this seam sizes that row" means.
 */
probe.seamKeys = () =>
  Array.from(document.querySelectorAll(".tl-row-split")).map((s) => {
    const row = s.parentElement?.querySelector("[data-lane-row]");
    return row ? row.dataset.laneRow : null;
  });

/** Which lane draws the keys, and what that lane thinks its own height is. */
probe.keyLane = () => {
  const key = document.querySelector(".tl-key");
  if (!key) return null;
  const lane = key.closest("[data-lane]");
  const cs = lane ? getComputedStyle(lane) : null;
  return {
    lane: lane ? lane.dataset.lane : null,
    trackH: cs ? cs.getPropertyValue("--tl-track-h").trim() : null,
    keySize: cs ? cs.getPropertyValue("--tl-key-size").trim() : null,
    laneH: lane ? Math.round(lane.getBoundingClientRect().height) : null,
  };
};

/** One keyframe diamond's drawn size, and whether every key is inside its lane. */
probe.keys = () => {
  const nodes = Array.from(document.querySelectorAll(".tl-key"));
  if (!nodes.length) return { count: 0 };
  const first = nodes[0].getBoundingClientRect();
  let overflowing = 0;
  for (const node of nodes) {
    const lane = node.closest("[data-lane]");
    if (!lane) continue;
    const kr = node.getBoundingClientRect();
    const lr = lane.getBoundingClientRect();
    // The diamond is rotated 45°, so its box already covers the corners.
    if (kr.top < lr.top - 0.5 || kr.bottom > lr.bottom + 0.5) overflowing += 1;
  }
  return { count: nodes.length, size: round(first.width), overflowing };
};

/** Scroll the lanes back to the top so a seam's box is where it looks. */
probe.toTop = () => {
  const sc = document.querySelector(".tl-scroll");
  if (sc) sc.scrollTop = 0;
  return sc ? sc.scrollTop : -1;
};

/**
 * Bring one row's SEAM inside the scroller, and say whether that worked.
 *
 * ⚠ A DRAG CANNOT SCROLL — the same rule `editor_lane_move_check` follows. So the
 * seam is brought into view BEFORE the press, and a seam that cannot be shown is
 * reported rather than pressed at a coordinate where it is not. This is the bug
 * this helper was written for: the frames row sits below the fold on a six-row
 * project, its handle box read fine, and the press landed on whatever was
 * actually at those coordinates — a drag that moved nothing and a check that
 * looked like a broken feature.
 */
probe.revealRow = (laneKey) => {
  const sc = document.querySelector(".tl-scroll");
  const lane = document.querySelector(`[data-lane="${laneKey}"]`);
  if (!sc || !lane) return { ok: false, why: "no row " + laneKey };
  const view = sc.getBoundingClientRect();
  const box = lane.getBoundingClientRect();
  // The seam hangs a few px BELOW the row, so aim past the row's bottom edge.
  const want = box.bottom + 12;
  if (want > view.bottom) sc.scrollTop += want - view.bottom;
  else if (box.top < view.top) sc.scrollTop -= view.top - box.top;
  const after = lane.getBoundingClientRect();
  return {
    ok: after.bottom + 8 <= view.bottom && after.top >= view.top - 1,
    top: Math.round(after.top),
    bottom: Math.round(after.bottom),
    view: [Math.round(view.top), Math.round(view.bottom)],
  };
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>row height probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_rowheight.jsx"></script>
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


def seam_for(page, lane_key):
    """The seam that sizes the row keyed `lane_key`, scrolled into view first.

    ⚠ THE SCROLL COMES BEFORE THE HANDLE IS READ, not after. A seam below the
    fold still reports a bounding box — one outside the scroller — and pressing at
    those coordinates presses whatever is really there.
    """
    shown = page.evaluate("(k) => window.__probe.revealRow(k)", lane_key)
    page.wait_for_timeout(80)
    rows = page.evaluate("() => window.__probe.seamKeys()")
    if lane_key not in rows:
        return None, rows
    idx = rows.index(lane_key)
    if not shown.get("ok"):
        print(f"      (could not fully show {lane_key}: {json.dumps(shown)})")
    return page.query_selector_all(".tl-row-split")[idx], rows


def drag(page, handle, dy):
    box = handle.bounding_box()
    if box is None:
        return
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    # In steps, because the component reads every move: one jump would test a
    # single event rather than a drag.
    for i in range(1, 6):
        page.mouse.move(x, y + dy * i / 5)
        page.wait_for_timeout(16)
    page.mouse.up()
    page.wait_for_timeout(120)


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
            print("  Vite would not start — the editor was NOT driven.")
            return 2

        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=[
                "--use-gl=angle", "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            ])
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_rowheight.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            try:
                page.wait_for_selector('[data-sel^="frame:"]', timeout=45000)
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts", False, str(exc)[:160])
                page.screenshot(path=os.path.join(ROOT, "rowheight_probe_failed.png"))
                browser.close()
                return 1
            page.wait_for_timeout(500)
            page.evaluate("() => window.__probe.toTop()")

            print(NL + "⚠ EVERY ROW HAS A SEAM, AND THE TWO COLUMNS AGREE TO START" + NL)
            before = page.evaluate("() => window.__probe.rowBoxes()")
            seams = page.evaluate("() => window.__probe.seamKeys()")
            check("there is a seam for every row on the timeline",
                  len(seams) == len(before) and len(seams) > 2,
                  f"{len(seams)} seams, {len(before)} rows")
            paired = {k: v for k, v in before.items() if "gutter" in v and "track" in v}
            check("every row is measurable on both columns",
                  len(paired) == len(before), f"{len(paired)} of {len(before)}")
            check("⚠ AND EVERY ROW IS THE SAME HEIGHT ON BOTH — the invariant the"
                  " one-height rule was protecting",
                  all(abs(v["gutter"]["h"] - v["track"]["h"]) <= 1 for v in paired.values()),
                  json.dumps({k: v for k, v in list(paired.items())[:3]}))
            check("...and each row's label is level with its own track",
                  all(abs(v["gutter"]["top"] - v["track"]["top"]) <= 1.5
                      for v in paired.values()),
                  json.dumps({k: [v["gutter"]["top"], v["track"]["top"]]
                              for k, v in list(paired.items())[:4]}))

            target = seams[0]
            others = seams[1:]
            print(NL + "⚠ DRAGGING THE SEAM UNDER ONE ROW RESIZES THAT ROW ALONE" + NL)
            handle, _ = seam_for(page, target)
            check(f"the seam under {target!r} is reachable", handle is not None)
            if handle is None:
                browser.close()
                return 1
            drag(page, handle, 40)
            grown = page.evaluate("() => window.__probe.rowBoxes()")
            gained = grown[target]["gutter"]["h"] - before[target]["gutter"]["h"]
            check("the row got taller by about what the pointer travelled",
                  30 <= gained <= 50, f"grew {gained}px on a 40px drag")
            check("⚠ ON BOTH COLUMNS, BY THE SAME AMOUNT",
                  abs(grown[target]["track"]["h"] - grown[target]["gutter"]["h"]) <= 1,
                  json.dumps(grown[target]))
            check("⚠ AND NOT ONE OTHER ROW CHANGED HEIGHT",
                  all(abs(grown[k]["gutter"]["h"] - before[k]["gutter"]["h"]) <= 1
                      for k in others),
                  json.dumps({k: [before[k]["gutter"]["h"], grown[k]["gutter"]["h"]]
                              for k in others}))
            check("...and the columns still line up all the way down",
                  all(abs(v["gutter"]["top"] - v["track"]["top"]) <= 1.5
                      for v in grown.values() if "gutter" in v and "track" in v),
                  json.dumps({k: [v.get("gutter", {}).get("top"),
                                  v.get("track", {}).get("top")]
                              for k, v in list(grown.items())[:4]}))

            print(NL + "⚠ THE KEYFRAME DIAMONDS ARE A FRACTION OF THE ROW" + NL)
            keys_before = page.evaluate("() => window.__probe.keys()")
            check("the animated shot draws its keys", keys_before.get("count", 0) >= 8,
                  json.dumps(keys_before))
            check("⚠ AND NONE OF THEM IS SLICED OFF BY ITS LANE",
                  keys_before.get("overflowing") == 0, json.dumps(keys_before))

            # ⚠ THE ROW THAT ACTUALLY DRAWS THE KEYS, ASKED FOR BY NAME. Guessing
            # it from the lane keys ("the one with 'image' in it") picked the
            # overlay row and resized a row with no keyframes on it at all — a
            # check that passed nothing and failed for the wrong reason.
            key_lane = page.evaluate("() => window.__probe.keyLane()") or {}
            keys_row = key_lane.get("lane")
            check("the keys are on a row that has a seam", keys_row in seams,
                  f"{keys_row!r} against {seams}")
            if keys_row in seams:
                handle, _ = seam_for(page, keys_row)
                if handle is not None:
                    drag(page, handle, 45)
                    keys_after = page.evaluate("() => window.__probe.keys()")
                    check("⚠ A TALLER ROW DRAWS BIGGER DIAMONDS — which is the answer"
                          " to 'keep key size little big'",
                          keys_after.get("size", 0) > keys_before.get("size", 0) + 0.4,
                          f"{keys_before.get('size')}px -> {keys_after.get('size')}px")
                    check("...and they are all still inside their lane",
                          keys_after.get("overflowing") == 0, json.dumps(keys_after))

            print(NL + "⚠ DOUBLE-CLICK PUTS A ROW BACK, AND THE ARROWS NUDGE IT" + NL)
            handle, _ = seam_for(page, target)
            handle.dblclick()
            page.wait_for_timeout(150)
            reset = page.evaluate("() => window.__probe.rowBoxes()")
            check("double-clicking the seam restores the default height",
                  abs(reset[target]["gutter"]["h"] - before[target]["gutter"]["h"]) <= 1,
                  f"{before[target]['gutter']['h']} -> {reset[target]['gutter']['h']}")
            handle.focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            nudged = page.evaluate("() => window.__probe.rowBoxes()")
            check("⚠ AND IT IS REACHABLE FROM THE KEYBOARD — two ArrowDowns make it"
                  " taller, because it is the same separator the panes use",
                  nudged[target]["gutter"]["h"] > reset[target]["gutter"]["h"] + 4,
                  f"{reset[target]['gutter']['h']} -> {nudged[target]['gutter']['h']}")

            print(NL + "⚠ AND IT CANNOT BE DRAGGED INTO NOTHING" + NL)
            handle, _ = seam_for(page, target)
            drag(page, handle, -600)
            floored = page.evaluate("() => window.__probe.rowBoxes()")
            check("a drag far past the top leaves a row that is still a row",
                  floored[target]["gutter"]["h"] >= 20,
                  f"{floored[target]['gutter']['h']}px")
            check("...on both columns",
                  abs(floored[target]["track"]["h"] - floored[target]["gutter"]["h"]) <= 1,
                  json.dumps(floored[target]))
            handle, _ = seam_for(page, target)
            drag(page, handle, 900)
            capped = page.evaluate("() => window.__probe.rowBoxes()")
            check("...and a drag far past the bottom is capped rather than endless",
                  capped[target]["gutter"]["h"] <= 6 * 16 + 4,
                  f"{capped[target]['gutter']['h']}px against a 6rem ceiling")

            print(NL + "⚠ AND IT IS REMEMBERED, THE WAY THE PANE SIZES ARE" + NL)
            # The row is currently at the ceiling from the drag above. Reload and
            # read it back: what is under test is the record, not the state.
            tall = page.evaluate("() => window.__probe.rowBoxes()")[target]["gutter"]["h"]
            # ⚠ THE WRITE IS DEBOUNCED BY 250ms, so reading storage the instant the
            # drag ends reads the PREVIOUS drag's record — which is how this check
            # first "failed": it compared a 6rem row against the 1.5rem the drag
            # before it had left in the store. A resize is thirty state changes and
            # the debounce is what stops it being thirty synchronous writes.
            page.wait_for_timeout(450)
            # ⚠ AND IT IS WRITTEN UNDER THE PROJECT'S ID, not under the lane key
            # alone. The flat record was a bug you could see on the first screen:
            # a lane key is the same string in every project (`text:`, `frames:0`,
            # and the `_import_text_0` ids every import produces), so one drag
            # made every project opened afterwards come up with some rows tall and
            # some short. See `row_heights.js`.
            stored = page.evaluate("() => localStorage.getItem('cas_animatic_rows2')")
            check("the drag was written to storage", bool(stored), str(stored))
            check("...under THIS project's id, and nowhere else",
                  bool(stored) and list(json.loads(stored)) == ["probe"]
                  and bool(json.loads(stored)["probe"]["rows"]),
                  str(stored))
            check("...and the flat, project-less record is gone",
                  page.evaluate("() => localStorage.getItem('cas_animatic_rows')") is None,
                  "cas_animatic_rows still present")
            page.reload()
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_selector('[data-sel^="frame:"]', timeout=45000)
            page.wait_for_timeout(500)
            page.evaluate("() => window.__probe.toTop()")
            back = page.evaluate("() => window.__probe.rowBoxes()")
            check("⚠ AND THE ROW COMES BACK THE HEIGHT IT WAS LEFT",
                  abs(back[target]["gutter"]["h"] - tall) <= 2,
                  f"{tall}px before the reload, {back[target]['gutter']['h']}px after")
            check("...on both columns, still level",
                  abs(back[target]["track"]["h"] - back[target]["gutter"]["h"]) <= 1
                  and abs(back[target]["gutter"]["top"] - back[target]["track"]["top"]) <= 1.5,
                  json.dumps(back[target]))
            check("...and a row nobody dragged is still the default height",
                  abs(back[others[0]]["gutter"]["h"] - before[others[0]]["gutter"]["h"]) <= 1,
                  f"{before[others[0]]['gutter']['h']} -> {back[others[0]]['gutter']['h']}")
            # Leave the store as we found it, or the next run starts with a tall row.
            page.evaluate("() => localStorage.removeItem('cas_animatic_rows2')")

            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors[:3]))
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
        for name in failures:
            print("  -", name)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
