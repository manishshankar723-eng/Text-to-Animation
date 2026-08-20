"""A CLIP CAN BE DRAGGED ONTO ANOTHER ROW — in the real editor.

The report this was written for:

    "mai big thing i not move some audio part in other audio layer on blank area
     i want i do move audio content to other audio layer"
    "and i see same problem with other like shape not move other shapes layer
     sam with image, text, amd caption"

A clip's ROW used to be decided once, when it was made. A move drag was purely
horizontal, so the only way to change row was to drag the thing out of the Media
pane again — which existed for shapes and for audio and for nothing else, and
which for audio was refused outright whenever the destination was one of the rows
grouped by FILE. There was no gesture at all for a caption or an overlay picture.

⚠ NO ARITHMETIC TEST COULD HAVE CAUGHT THIS, and none can check the fix. The
times and lengths were always right; what was missing was the vertical half of a
gesture. So this drives the real `<AnimaticEditor>` in Chromium, drags each kind
of clip from one row to another with the mouse, and asserts on WHICH ROW IS
DRAWING IT AFTERWARDS — read out of the DOM, because "the state changed" can be
true while the clip is still painted on the row it came from.

---------------------------------------------------------------------------
⚠ EVERY DRAG IS ASSERTED IN BOTH DIRECTIONS: the destination gained it AND the
   source lost it
---------------------------------------------------------------------------
A "the clip is on row B" assertion passes just as happily for a COPY as for a
move, and a copy is a different (and much worse) bug — two clips playing the same
caption. So each check reads the clip's lane before and after and requires it to
have changed, and re-counts both rows.

The two audio cases are deliberately different edits:
  - onto an EMPTY AUDIO LAYER — the user's own words, and the simple path: the
    row is a layer, so the clip is told to sit on it.
  - onto a FILE-GROUPED ROW — the path that had no answer at all. Those rows are
    grouped by upload (that is what makes a razored take look cut rather than
    doubled) and carry no id a clip could be given, so the row is PROMOTED to a
    real layer, taking its own clips with it. The assertion is that both clips
    end up on ONE row that is neither of the two they started on.

Also pinned: a TRIM grip is not a move. Dragging the head grip of a clip
vertically must change its length and leave it exactly where it lives — the grips
and the body share one implementation, so "the whole thing moves rows now" is a
plausible way to break trimming.

    python tests/editor_lane_move_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router — the harness is `editor_razor_check.py`'s, borrowed
deliberately rather than invented a second time.
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

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_lanemove.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_lanemove.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The stubbed project — TWO ROWS OF EVERY KIND
# ---------------------------------------------------------------------------
# ⚠ THAT IS THE FIXTURE'S POINT. The gesture under test is "from this row to that
# one", so every kind needs a second row to be dragged to. The second text and
# shape rows are LAYERS, because the default row ("" / no layer) is where clips
# with no row of their own live and there is exactly one of it.
TOTAL_MS = 8000       # two 4s shots
FREE_START_MS = 2000  # every free clip: 2.0s -> 6.0s
FREE_LEN_MS = 4000

PROJECT = {
    "id": "probe",
    "title": "probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [
        {"id": "f1", "kind": "image", "src": {"kind": "upload", "upload_id": "u1"},
         "duration_ms": 4000, "label": "Shot 1", "url": "/animatics/probe/media/u1"},
        {"id": "f2", "kind": "image", "src": {"kind": "upload", "upload_id": "u2"},
         "duration_ms": 4000, "label": "Shot 2", "url": "/animatics/probe/media/u2"},
    ],
    # On the DEFAULT text row, so the drag is default -> layer. `keyframes` is
    # there because a head trim has to re-time them, and the trim assertion at the
    # end would otherwise be testing the easy case.
    "texts": [{"id": "t1", "text": "caption", "start_ms": FREE_START_MS,
               "duration_ms": FREE_LEN_MS, "layer_id": "", "group_id": "",
               "keyframes": {"opacity": [{"t": 0, "v": 0.2, "ease": "linear"},
                                         {"t": FREE_LEN_MS, "v": 1.0, "ease": "linear"}]}}],
    "shapes": [{"id": "s1", "kind": "rect", "start_ms": FREE_START_MS,
                "duration_ms": FREE_LEN_MS, "layer_id": "", "group_id": ""}],
    "layers": [
        {"id": "L_txt2", "kind": "text", "name": "Text 2"},
        {"id": "L_shp2", "kind": "shape", "name": "Shapes 2"},
        {"id": "L_img1", "kind": "image", "name": "Picture layer"},
        {"id": "L_img2", "kind": "image", "name": "Picture layer 2"},
        {"id": "L_aud1", "kind": "audio", "name": "Sound"},
        # ⚠ EMPTY, and that is the case that was reported: "move audio content to
        # other audio layer on blank area". An audio LAYER draws a row even with
        # nothing on it, so the row exists to be dropped on.
        {"id": "L_aud2", "kind": "audio", "name": "Sound 2"},
    ],
    "overlays": [{"id": "o1", "upload_id": "u2", "layer_id": "L_img1", "group_id": "",
                  "start_ms": FREE_START_MS, "duration_ms": FREE_LEN_MS,
                  "url": "/animatics/probe/media/u2"}],
    "transitions": [],
    "audio_tracks": [
        {"id": "a1", "upload_id": "u3", "filename": "sound.wav",
         "layer_id": "L_aud1", "group_id": "", "duration_ms": 8000,
         "start_ms": FREE_START_MS, "offset_ms": 0, "trim_ms": FREE_LEN_MS,
         "volume": 1.0, "muted": False, "url": "/animatics/probe/media/u3"},
        # ⚠ NO `layer_id`: a track saved before layers existed, which gets a row
        # of its own keyed by its UPLOAD. That row is the one with no id to write,
        # and the one the promotion path exists for.
        {"id": "a2", "upload_id": "u4", "filename": "loose.wav",
         "layer_id": "", "group_id": "", "duration_ms": 8000,
         "start_ms": FREE_START_MS, "offset_ms": 0, "trim_ms": FREE_LEN_MS,
         "volume": 1.0, "muted": False, "url": "/animatics/probe/media/u4"},
    ],
    "veo_clips": [], "video": None,
}


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


def wav_bytes(ms=8000, rate=8000):
    """A silent mono WAV, written by hand.

    ⚠ NOT generated with ffmpeg, deliberately — the same reasoning as
    `editor_razor_check.py`: the audio rows have to be DRAWN before a clip on one
    can be dragged, and that must not depend on a binary being installed.
    """
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
    "u4": ("audio/wav", wav_bytes()),
}


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
        # PUT is the autosave: answered with the document, so a save firing
        # mid-drag cannot change what the editor is holding.
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
 * WHICH ROW is drawing this clip — the whole question this test asks.
 *
 * ⚠ READ OUT OF THE DOM, off the clip's own lane ancestor. "The document says
 * layer_id is L_txt2" can be true while the row on screen still draws the clip,
 * and a person can only see the screen. Every lane carries `data-lane`, so this
 * asks the same question `laneAtPoint` does.
 */
probe.laneOf = (key) => {
  const el = document.querySelector(`[data-sel="${key}"]`);
  if (!el) return null;
  const lane = el.closest("[data-lane]");
  return lane ? lane.dataset.lane : "no-lane";
};

/** How many clips each row is drawing, keyed by lane. */
probe.rows = () => {
  const out = {};
  for (const lane of document.querySelectorAll("[data-lane]")) {
    out[lane.dataset.lane] = lane.querySelectorAll("[data-sel]").length;
  }
  return out;
};

/** Every row on the timeline, in the order they are drawn. */
probe.laneKeys = () =>
  Array.from(document.querySelectorAll("[data-lane]")).map((n) => n.dataset.lane);

/** Where one clip is drawn, and how wide — "did the trim really trim?" */
probe.spanOf = (sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { left: Math.round(r.left), width: Math.round(r.width) };
};

/**
 * Scroll the timeline so a clip AND the row it is to be dragged to are both
 * visible at once, and say whether that was possible.
 *
 * ⚠ A DRAG CANNOT SCROLL. `scroll_into_view_if_needed` is the right tool for a
 * click and useless here: scrolling half way through the gesture pulls the clip
 * out from under a pointer that is already down. So both boxes are brought into
 * the scroller BEFORE the press, and a pair that cannot fit in it together is
 * reported as such rather than mis-clicked.
 */
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

/**
 * Does every empty-row PROMPT fit inside its own row?
 *
 * ⚠ MEASURED OFF THE TEXT NODE, not off the button. The button is the full
 * height of the lane by construction, so asking IT whether it overflows answers
 * "no" whatever the text inside it is doing. A Range round the text gives the
 * line box that is actually painted — which is the thing the lane's
 * `overflow: hidden` was slicing.
 *
 * `trackH` is written straight onto `.tl-wrap`, which is exactly what the
 * VERTICAL ZOOM does (see `trackH` in Timeline.jsx), so this measures the real
 * short-row case rather than a mock of it.
 */
probe.promptFit = (trackH) => {
  const wrap = document.querySelector(".tl-wrap");
  if (!wrap) return [{ row: "no .tl-wrap", clipped: true }];
  const was = wrap.style.getPropertyValue("--tl-track-h");
  if (trackH) wrap.style.setProperty("--tl-track-h", trackH);
  const out = [];
  for (const el of document.querySelectorAll(".tl-lane .tl-track-empty")) {
    const lane = el.closest("[data-lane]");
    const node = el.firstChild;
    if (!lane || !node || node.nodeType !== Node.TEXT_NODE) continue;
    const range = document.createRange();
    range.selectNodeContents(el);
    const text = range.getBoundingClientRect();
    const box = lane.getBoundingClientRect();
    out.push({
      row: lane.dataset.lane,
      // Positive = how far the prompt sticks out of the row it belongs to.
      over: Math.round(Math.max(box.top - text.top, text.bottom - box.bottom)),
      clipped: text.top < box.top - 0.5 || text.bottom > box.bottom + 0.5,
    });
  }
  if (trackH) {
    if (was) wrap.style.setProperty("--tl-track-h", was);
    else wrap.style.removeProperty("--tl-track-h");
  }
  return out;
};

/** What is really on top at a point — the answer to "my press went WHERE?" */
probe.hitAt = (x, y) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return "nothing";
  const chain = [];
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    chain.push(n.className && n.className.split ? n.className.split(" ")[0] : n.tagName);
  }
  return chain.join(" < ");
};

/** Whatever the editor last said in the status bar. */
probe.notice = () => {
  const el = document.querySelector(".an-status-note");
  return el ? el.textContent : "";
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>lane move probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_lanemove.jsx"></script>
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


def drag_to_lane(page, selector, lane_key, grip=None, dx=0):
    """Drag one clip onto the row keyed `lane_key`. "" on success, else why not.

    ⚠ BOTH ROWS HAVE TO BE ON SCREEN AT ONCE, and unlike a click there is no
    scrolling half way through a drag that would not also pull the clip out from
    under the pointer. So the two boxes are read first and a drag that cannot be
    performed comes back as a MISS rather than as a gesture that did nothing —
    which is the most misleading way this file could fail.

    `grip` presses a CHILD of the clip (`.tl-handle-l`) instead of its body, which
    is how the "a trim is not a move" case is expressed with the same code.
    """
    clip = page.query_selector(selector)
    if clip is None:
        return f"nothing matched {selector}"
    lane = page.query_selector(f'[data-lane="{lane_key}"]')
    if lane is None:
        return f"there is no row keyed {lane_key!r}"
    shown = page.evaluate(
        "([s, k]) => window.__probe.revealPair(s, k)", [selector, lane_key]
    )
    if not shown.get("ok"):
        return f"cannot see {selector} and row {lane_key} at once ({shown.get('why')})"
    page.wait_for_timeout(80)
    press_on = clip.query_selector(grip) if grip else clip
    if press_on is None:
        return f"{selector} has no {grip}"
    box = press_on.bounding_box()
    lbox = lane.bounding_box()
    if not box or not lbox:
        return "one of the two has no box"
    x0 = box["x"] + box["width"] / 2
    y0 = box["y"] + box["height"] / 2
    y1 = lbox["y"] + lbox["height"] / 2
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
    # ⚠ MORE THAN ONE MOVE. Every drag on this timeline decides what it is on the
    # FIRST pointermove and writes its result from the last one; a single jump puts
    # both on one event and is not the gesture a hand makes.
    page.mouse.move(x0 + 4, y0, steps=2)
    page.mouse.move(x0 + dx, (y0 + y1) / 2, steps=4)
    page.mouse.move(x0 + dx, y1, steps=4)
    page.mouse.up()
    page.wait_for_timeout(200)
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
            # ⚠ TALL. Ten rows have to be reachable, and a drag cannot scroll
            # half way through without pulling the clip out from under the pointer.
            page = browser.new_page(viewport={"width": 1600, "height": 1800})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_lanemove.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nThe editor comes up with two rows of every kind")
            try:
                page.wait_for_selector("canvas", timeout=45000)
                # The audio clips arrive last: their blobs have to be fetched
                # before the rows draw anything but a "Loading…" placeholder.
                page.wait_for_function(
                    "() => document.querySelectorAll"
                    "('.tl-audio-clip:not(.loading)').length >= 2",
                    timeout=45000,
                )
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts with every row drawn", False, str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=os.path.join(ROOT, "lanemove_probe_failed.png"))
                browser.close()
                return 1

            keys = page.evaluate("() => window.__probe.laneKeys()")
            # ⚠ `frames:0`, not `frames`. The picture rows are numbered TRACKS now
            # — one row per track, keyed by its number — where they used to be one
            # sequence drawn twice and filtered by origin. See `pictureTracks`.
            wanted = ["text:", "L_txt2", "shape:", "L_shp2", "L_img1", "L_img2",
                      "frames:0", "u4", "L_aud1", "L_aud2"]
            missing = [k for k in wanted if k not in keys]
            check("every row this test drags between is on the timeline",
                  not missing, f"missing {missing} - have {keys}")
            if missing:
                page.screenshot(path=os.path.join(ROOT, "lanemove_probe_failed.png"))
                browser.close()
                return 1

            # -----------------------------------------------------------------
            # A CLIP GOES WHERE YOU DRAG IT
            # -----------------------------------------------------------------
            # ⚠ EACH CASE IS TWO CLAIMS: the destination draws it now, and the row
            # it came from does not. The second half is what tells a MOVE apart
            # from a COPY — two clips playing the same caption is a worse bug than
            # the one being fixed.
            print("\nDragging one clip of each kind onto another row")
            cases = [
                ("a caption", "text:t1", ".tl-texts .tl-text", "text:", "L_txt2"),
                ("a shape", "shape:s1", ".tl-shapes .tl-shape", "shape:", "L_shp2"),
                ("an overlay picture", "overlay:o1", ".tl-overlays .tl-overlay",
                 "L_img1", "L_img2"),
                # The reported case, in the user's own words: onto an EMPTY row.
                ("an audio clip, onto an empty audio layer", "audio:a1",
                 '[data-lane="L_aud1"] .tl-audio-clip', "L_aud1", "L_aud2"),
            ]
            for name, sel_key, selector, from_key, to_key in cases:
                before = page.evaluate("(k) => window.__probe.laneOf(k)", sel_key)
                rows_before = page.evaluate("() => window.__probe.rows()")
                missed = drag_to_lane(page, selector, to_key)
                after = page.evaluate("(k) => window.__probe.laneOf(k)", sel_key)
                rows_after = page.evaluate("() => window.__probe.rows()")
                said = page.evaluate("() => window.__probe.notice()")
                check(
                    f"{name} lands on the row it was dragged to",
                    not missed and before == from_key and after == to_key,
                    missed or f"{before} -> {after} - editor said: {said!r}",
                )
                check(
                    f"...and {name} is GONE from the row it came from",
                    rows_after.get(from_key, 0) == rows_before.get(from_key, 0) - 1
                    and rows_after.get(to_key, 0) == rows_before.get(to_key, 0) + 1,
                    f"{from_key}: {rows_before.get(from_key)} -> {rows_after.get(from_key)},"
                    f" {to_key}: {rows_before.get(to_key)} -> {rows_after.get(to_key)}",
                )

            # -----------------------------------------------------------------
            # THE ROW THAT HAD NO ID: dropping on it PROMOTES it
            # -----------------------------------------------------------------
            print("\nDragging an audio clip onto a row grouped by FILE")
            # `a1` lives on L_aud2 now (the drag above). `u4` is `a2`'s own row,
            # keyed by its upload — the kind with no id to write, which is exactly
            # why "drop it on that other file's row" used to be refused.
            before = page.evaluate("() => [window.__probe.laneOf('audio:a1'),"
                                   " window.__probe.laneOf('audio:a2')]")
            missed = drag_to_lane(page, '[data-lane="L_aud2"] .tl-audio-clip', "u4")
            after = page.evaluate("() => [window.__probe.laneOf('audio:a1'),"
                                  " window.__probe.laneOf('audio:a2')]")
            said = page.evaluate("() => window.__probe.notice()")
            check(
                "the two clips end up on ONE row — the file row became a layer",
                not missed and after[0] is not None and after[0] == after[1],
                missed or f"{before} -> {after} - editor said: {said!r}",
            )
            check(
                "...and it is a NEW row, not either of the two they started on",
                after[0] not in ("u4", "L_aud1", "L_aud2", None, "no-lane"),
                str(after),
            )
            check(
                "...and the editor says the row is a layer now rather than doing it quietly",
                "layer" in said.lower(),
                said,
            )

            # -----------------------------------------------------------------
            # A TRIM IS NOT A MOVE
            # -----------------------------------------------------------------
            print("\nThe grips are still trims")
            # The caption is on L_txt2 now. Dragging its HEAD grip down onto the
            # row below must change its LENGTH and leave it on its own row: the
            # body and the grips share one implementation, so "everything changes
            # row now" is a plausible way to have broken trimming.
            span_before = page.evaluate("() => window.__probe.spanOf('.tl-texts .tl-text')")
            lane_before = page.evaluate("() => window.__probe.laneOf('text:t1')")
            # ⚠ WITH A HORIZONTAL TRAVEL. A head trim that ends where it began
            # writes nothing (`trimTimedClipStart` returns null), so a purely
            # vertical drag would assert "the length did not change" against a
            # gesture that correctly did nothing.
            missed = drag_to_lane(page, ".tl-texts .tl-text", "shape:",
                                  grip=".tl-handle-l", dx=70)
            lane_after = page.evaluate("() => window.__probe.laneOf('text:t1')")
            span_after = page.evaluate("() => window.__probe.spanOf('.tl-texts .tl-text')")
            check(
                "dragging a HEAD GRIP across rows trims the clip and does not move it",
                not missed and lane_after == lane_before == "L_txt2",
                missed or f"{lane_before} -> {lane_after}",
            )
            check(
                "...and it really trimmed — the clip is a different length",
                bool(span_before) and bool(span_after)
                and span_after["width"] != span_before["width"],
                f"{span_before} -> {span_after}",
            )

            # -----------------------------------------------------------------
            # THE PICTURE ROWS ARE NOT A DESTINATION
            # -----------------------------------------------------------------
            # `frames` is ONE sequence drawn as rows filtered by origin, so which
            # row a picture is on is READ OFF the clip and cannot be chosen. A
            # shape dragged onto it must stay a shape, on its own row.
            print("\nA clip cannot change what it is by being dropped somewhere else")
            lane_before = page.evaluate("() => window.__probe.laneOf('shape:s1')")
            missed = drag_to_lane(page, ".tl-shapes .tl-shape", "frames:0")
            lane_after = page.evaluate("() => window.__probe.laneOf('shape:s1')")
            check(
                "a shape dragged onto a picture track stays on its shapes row",
                not missed and lane_after == lane_before,
                missed or f"{lane_before} -> {lane_after}",
            )

            # -----------------------------------------------------------------
            # THE EMPTY-ROW PROMPT FITS ON THE ROW
            # -----------------------------------------------------------------
            # The other half of the report: "first you see layer empty text not
            # view full i see it gos in down". The prompt was padded down from the
            # top of a row whose height the VERTICAL ZOOM writes, so at the short
            # end it landed on the row's bottom edge and `overflow: hidden` took
            # its descenders off. Checked at BOTH ends of the zoom, because a fix
            # that only holds at the default height is not a fix.
            print("\nThe empty-row prompts fit their rows at any track height")
            for label, height in [("the default row height", ""),
                                  ("the SHORTEST row the zoom allows", "1.5rem"),
                                  ("the tallest row the zoom allows", "6rem")]:
                fit = page.evaluate("(h) => window.__probe.promptFit(h)", height)
                bad = [f for f in fit if f["clipped"]]
                check(
                    f"no empty-row prompt is sliced by its row at {label}",
                    bool(fit) and not bad,
                    f"measured {len(fit)} prompt(s); overflowing: {bad}",
                )

            print("\nAfterwards")
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:400])
            if failures:
                page.screenshot(path=os.path.join(ROOT, "lanemove_probe_failed.png"))
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
    print("Every kind of clip can be dragged to another row, and only the row changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
