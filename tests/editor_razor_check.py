"""THE RAZOR CUTS THE LAYER YOU CLICKED, AND NOTHING ELSE — in the real editor.

The report this was written for:

    "when i cut so my cut icon in top of timeline in time sec show row and i
     click so i notice my image clip cut but this not happen again when i go
     image layer then i cut only image layer and same do in all layer not cut
     from any where"

    "when i cut audio so i not see cut icon like when i cut video"

Both were routing, not arithmetic. `toolPress` answered the razor for the RULER
and for the empty part of EVERY lane, and what it called was the picture razor —
so a press anywhere at all cut the picture sequence, including in the seconds row
where no clip is drawn. And `.tl-audio-clip` sets its own `grab` cursor, which
beat the container's `crosshair`, so the one lane you could already cut was the
one that did not look cuttable.

⚠ NO ARITHMETIC TEST COULD HAVE CAUGHT EITHER. `splitFrameAt` and `splitClip`
were both correct and both stayed correct; the bug was in which one a press
reached, and how the pointer looked on the way. So this drives the real
`<AnimaticEditor>` in Chromium and asserts on WHAT GOT CUT — counting the clips
on every lane before and after each press, which is the only statement that tells
"cut the right clip" apart from "cut a clip".

---------------------------------------------------------------------------
⚠ EVERY "NOTHING TO CUT" PRESS LANDS AT 2.0s, AND THAT IS THE MOST IMPORTANT
   DECISION IN THIS FILE
---------------------------------------------------------------------------
A "nothing was cut" assertion is worthless if the cut would have been REFUSED
anyway. The first version of this test aimed at the middle of the ruler, which on
an 8s sequence of two 4s shots is exactly the cut between them — so the old,
broken code refused it for being 0ms from an edit point, and the test passed
against the very bug it exists for. (Found by putting the bug back and running
it. That is the only way to find this out.)

So the geometry is deliberate now. `PROBE_MS` = 2000 is:
  • 2.0s into the first picture and 2.0s from either of its edges, so the OLD code
    cut there happily — a pass now means the routing changed, not that the razor
    happened to be refusing;
  • before every free clip starts (they all begin at 3.0s), so the same x is
    genuinely empty on the caption, shape, overlay and audio rows.
And the picture is then cut AT THE SAME x, so the pair of assertions is "this
press cuts the shot, and does not cut it from the ruler" rather than two hopeful
clicks that might both be no-ops.

    python tests/editor_razor_check.py

No backend is needed: Vite is started here and every API call is answered by
Playwright's router, the same way `editor_effects_drop_check.py` does it — this
file borrows that harness deliberately rather than inventing a second one.
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

PROBE_HTML = os.path.join(CLIENT, "__probe_razor.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_razor.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The stubbed project — ONE CLIP ON EVERY KIND OF LANE
# ---------------------------------------------------------------------------
# ⚠ THAT IS THE FIXTURE'S POINT. The bug was a cut landing on the wrong LAYER, so
# a project with only pictures in it cannot express the failure: every assertion
# below is "this lane gained a clip AND no other lane changed", and the second
# half needs every other lane to exist and to be countable.
TOTAL_MS = 8000       # two 4s shots
FREE_START_MS = 3000  # every free clip: 3.0s → 7.0s
FREE_LEN_MS = 4000
PROBE_MS = 2000       # where every "nothing to cut" press lands — see the header

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
    "texts": [{"id": "t1", "text": "caption", "start_ms": FREE_START_MS,
               "duration_ms": FREE_LEN_MS, "layer_id": "", "group_id": ""}],
    "shapes": [{"id": "s1", "kind": "rect", "start_ms": FREE_START_MS,
                "duration_ms": FREE_LEN_MS, "layer_id": "", "group_id": "",
                # Animated, so the split has keyframes to carry across — the one
                # part of cutting these clips that is not two numbers.
                "keyframes": {"opacity": [{"t": 0, "v": 0.2, "ease": "linear"},
                                          {"t": FREE_LEN_MS, "v": 1.0, "ease": "linear"}]}}],
    "layers": [
        {"id": "L_img", "kind": "image", "name": "Picture layer"},
        {"id": "L_aud", "kind": "audio", "name": "Sound"},
    ],
    "overlays": [{"id": "o1", "upload_id": "u2", "layer_id": "L_img", "group_id": "",
                  "start_ms": FREE_START_MS, "duration_ms": FREE_LEN_MS,
                  "url": "/animatics/probe/media/u2"}],
    "transitions": [],
    "audio_tracks": [{"id": "a1", "upload_id": "u3", "filename": "sound.wav",
                      "layer_id": "L_aud", "group_id": "", "duration_ms": 8000,
                      "start_ms": FREE_START_MS, "offset_ms": 0,
                      "trim_ms": FREE_LEN_MS, "volume": 1.0, "muted": False,
                      "url": "/animatics/probe/media/u3"}],
    "veo_clips": [], "video": None,
}

# Every lane the razor can land on, by the prefix its clips carry in `data-sel`.
LANES = ["frame", "text", "shape", "overlay", "audio"]


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


def wav_bytes(ms=8000, rate=8000):
    """A silent mono WAV, written by hand.

    ⚠ NOT generated with ffmpeg, deliberately: the audio lane has to be DRAWN
    before the razor can be clicked on it, and the editor only draws a clip once
    its blob has arrived. Making that depend on an ffmpeg binary would make the
    lane — and a fifth of this file — skip on a machine where the bug it covers
    reproduces perfectly.
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
        # PUT is the autosave. Answering it with the document means an autosave
        # firing mid-test cannot change what the editor is holding.
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
 * How many clips each lane is drawing.
 *
 * ⚠ COUNTED OFF `data-sel`, not off React state. Every selectable thing on the
 * timeline carries it, so this asks the same question the marquee does — and it
 * cannot be satisfied by a cut that updated the document without reaching the
 * screen, which is a way this could be broken while "the state is right".
 */
probe.counts = () =>
  ["frame", "text", "shape", "overlay", "audio"].reduce((out, kind) => {
    out[kind] = document.querySelectorAll(`[data-sel^="${kind}:"]`).length;
    return out;
  }, {});

/** The cursor actually computed for one element, which is what the user sees. */
probe.cursorOf = (sel) => {
  const el = document.querySelector(sel);
  return el ? getComputedStyle(el).cursor : null;
};

/** What is really on top at a point — the answer to "my click went WHERE?" */
probe.hitAt = (x, y) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return "nothing";
  const chain = [];
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    chain.push(n.className && n.className.split ? n.className.split(" ")[0] : n.tagName);
  }
  return chain.join(" < ");
};

/** Whatever the editor last said in the status bar — the reason for a refusal. */
probe.notice = () => {
  const el = document.querySelector(".an-status-note");
  return el ? el.textContent : "";
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>editor razor probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_razor.jsx"></script>
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


def press(page, selector, at_ms=None, index=0):
    """Press one element. Returns "" on success, else why the press never happened.

    `at_ms` presses at that MOMENT on the timeline rather than at the element's
    middle. The horizontal geometry is the point of this test, so it is stated in
    milliseconds and converted here — the lanes and the ruler share a width and an
    origin, so one number aims at "2.0s" on whichever row is being tested.

    ⚠ SCROLLED INTO VIEW FIRST, AND THEN CHECKED. `bounding_box()` answers for an
    element scrolled out of its pane just as happily as for one you can see, and
    `page.mouse` clicks a POINT rather than a node — so aiming at a lane below the
    fold sends the press to whatever is painted at those coordinates instead. It
    cost an hour: the zoom scrollbar swallowed the picture press and the status bar
    swallowed the audio one, and both were reported as "the razor did not cut",
    which is the most misleading way this file could fail. So the element on top is
    verified to be the one we aimed at BEFORE clicking, and a miss comes back as a
    miss rather than as a razor that does not work.
    """
    els = page.query_selector_all(selector)
    if len(els) <= index:
        return f"nothing matched {selector}"
    el = els[index]
    el.scroll_into_view_if_needed()
    page.wait_for_timeout(80)
    box = el.bounding_box()
    if not box:
        return f"{selector} has no box"
    y = box["y"] + box["height"] / 2
    x = box["x"] + (box["width"] / 2 if at_ms is None else box["width"] * at_ms / TOTAL_MS)
    on_target = page.evaluate(
        "([x, y, s]) => { const el = document.elementFromPoint(x, y);"
        " return Boolean(el && el.closest(s)); }",
        [x, y, selector],
    )
    if not on_target:
        landed = page.evaluate("([x, y]) => window.__probe.hitAt(x, y)", [x, y])
        return f"{selector} is out of reach — the press would land on {landed}"
    page.mouse.click(x, y)
    page.wait_for_timeout(140)
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
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_razor.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nThe editor comes up with a clip on every lane")
            try:
                page.wait_for_selector("canvas", timeout=45000)
                # The audio clip arrives last: its blob has to be fetched before
                # the lane draws anything but a "Loading…" placeholder.
                page.wait_for_selector(".tl-audio-clip:not(.loading)", timeout=45000)
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts with every lane drawn", False, str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=os.path.join(ROOT, "razor_probe_failed.png"))
                browser.close()
                return 1
            start = page.evaluate("() => window.__probe.counts()")
            complete = all(start[kind] >= 1 for kind in LANES)
            check("the editor mounts with every lane drawn", complete, str(start))
            if not complete:
                page.screenshot(path=os.path.join(ROOT, "razor_probe_failed.png"))
                browser.close()
                return 1

            # The razor, picked the way a user picks it.
            page.click("button.an-tool[title^='Razor']")
            check(
                "the razor is the active tool",
                page.get_attribute("button.an-tool[title^='Razor']", "aria-pressed") == "true",
            )

            # -----------------------------------------------------------------
            # THE REPORTED BUG: the seconds row is not a clip
            # -----------------------------------------------------------------
            print(f"\nPressing the razor at {PROBE_MS / 1000:.1f}s, where there is no clip")
            before = page.evaluate("() => window.__probe.counts()")
            missed = press(page, ".tl-ruler", at_ms=PROBE_MS)
            after = page.evaluate("() => window.__probe.counts()")
            check(
                "a press in the time ruler cuts NOTHING — it used to cut the picture",
                not missed and after == before,
                missed or f"{before} → {after}",
            )
            check(
                "…and the ruler still moves the playhead while the razor is up",
                page.evaluate(
                    "() => { const p = document.querySelector('.tl-playhead');"
                    " return p ? p.getBoundingClientRect().left : -1; }"
                ) > 0,
            )

            before = page.evaluate("() => window.__probe.counts()")
            missed = press(page, ".tl-lane.tl-shapes", at_ms=PROBE_MS)
            after = page.evaluate("() => window.__probe.counts()")
            check(
                "a press on the empty part of a lane cuts nothing either",
                not missed and after == before,
                missed or f"{before} → {after}",
            )
            check(
                "…and the editor says why, rather than doing nothing quietly",
                "clip" in page.evaluate("() => window.__probe.notice()").lower(),
                page.evaluate("() => window.__probe.notice()"),
            )

            # -----------------------------------------------------------------
            # EACH LAYER CUTS ITSELF, AND ONLY ITSELF
            # -----------------------------------------------------------------
            print("\nCutting each layer in turn")
            # ⚠ EACH ASSERTION IS TWO CLAIMS: this lane gained a clip, and every
            # other lane is untouched. The second half is the test — the old razor
            # also "cut successfully", it just cut the picture whichever bar you
            # aimed at.
            #
            # The picture is pressed at PROBE_MS — the SAME x that must cut nothing
            # from the ruler, which is what makes those two assertions a pair.
            # Everything else is pressed at its own middle, 5.0s, clear of both its
            # edges.
            targets = [
                ("frame", ".tl-bar", "an image clip", PROBE_MS),
                ("text", ".tl-text", "a caption", None),
                ("shape", ".tl-shape", "a shape", None),
                ("overlay", ".tl-overlay", "an image-layer picture", None),
                ("audio", ".tl-audio-clip", "an audio clip", None),
            ]
            for kind, selector, name, at_ms in targets:
                before = page.evaluate("() => window.__probe.counts()")
                missed = press(page, selector, at_ms=at_ms)
                if missed:
                    check(f"cutting {name} splits it in two", False, missed)
                    continue
                after = page.evaluate("() => window.__probe.counts()")
                gained = after[kind] - before[kind]
                others = {k: (before[k], after[k]) for k in LANES
                          if k != kind and after[k] != before[k]}
                check(
                    f"cutting {name} splits it in two",
                    gained == 1,
                    f"{kind}: {before[kind]} → {after[kind]}"
                    f" · editor said: {page.evaluate('() => window.__probe.notice()')!r}",
                )
                check(
                    f"…and cutting {name} leaves every other lane alone",
                    not others,
                    f"also changed: {others}",
                )

            # -----------------------------------------------------------------
            # ONE CUT CURSOR, ON EVERYTHING THAT CAN BE CUT
            # -----------------------------------------------------------------
            print("\nThe cut cursor is one cursor")
            cursors = {
                sel: page.evaluate("(s) => window.__probe.cursorOf(s)", sel)
                for sel in [".tl-bar", ".tl-text", ".tl-shape", ".tl-overlay",
                            ".tl-audio-clip"]
            }
            check(
                "every kind of clip shows the SAME cursor under the razor",
                len(set(cursors.values())) == 1 and "crosshair" in set(cursors.values()),
                str(cursors),
            )
            check(
                "…including the audio clip, which used to show a grab hand",
                cursors[".tl-audio-clip"] == "crosshair",
                cursors[".tl-audio-clip"],
            )
            grips = page.evaluate(
                "() => ['.tl-handle', '.tl-fade-grip'].map((s) => {"
                " const el = document.querySelector(s);"
                " return el ? getComputedStyle(el).pointerEvents : 'absent'; })"
            )
            check(
                "trim handles and fade grips are out of the blade's way",
                all(v in ("none", "absent") for v in grips),
                str(grips),
            )
            # Back to the selection tool: the grips must come back, or the razor
            # would have disabled trimming for the rest of the session.
            page.click("button.an-tool[title^='Selection']")
            back = page.evaluate(
                "() => { const el = document.querySelector('.tl-handle');"
                " return el ? getComputedStyle(el).pointerEvents : 'absent'; }"
            )
            check("…and they come back when the razor is put down", back != "none", back)

            print("\nAfterwards")
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:400])
            if failures:
                page.screenshot(path=os.path.join(ROOT, "razor_probe_failed.png"))
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
    print("The razor cuts the layer you clicked, and every clip wears the same blade.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
