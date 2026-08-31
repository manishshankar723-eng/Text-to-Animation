"""Does a VIDEO CLIP actually MOVE in the Program monitor? Driven in Chromium.

Why this exists: `tests/video_clip_check.py` pins `sourceAt` — the maths that
says WHICH moment of the file should be on screen — and every one of those sums
can be right while the monitor shows one frozen still for the whole clip. That is
what shipped: a project with six of its eight picture rows switched off played
its images and its sound perfectly and held its video on one frame, reported as
"video ka sirf ek thumbnail jaisa dikhta hai … pura clip mein ek image jaisa".

⚠ SO THE ASSERTION IS "THE PIXELS CHANGED", not "the number is right". Two
read-backs a second and a half apart, off the real <ProgramCanvas> with the real
`useMonitorVideo` slaving the real <video>, and the frame at 1.6s must not be the
frame at 0s.

⚠ AND IT RUNS TWICE. The second pass is the one that matters: it hides a lane the
way the editor hides one, so `scene` is built from the FILTERED clip list while
`useMonitorVideo` is handed the WHOLE one — the exact disagreement that made
`frames[picture.index]` name the wrong clip, leave the real <video> uncued, and
freeze it. `plain` alone passed throughout the bug.

It runs against a REAL H.264 file, because a fake one proves nothing about
decode. `--clip` points it at any mp4; with none given it takes the first
`vid_*.mp4` it finds under `output/_animatics/`, which is a file this install
actually imported.

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python tests/monitor_video_check.py
"""

import argparse
import glob
import os
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_video.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_video.jsx")
PROBE_CLIP = os.path.join(CLIENT, "__probe_clip.mp4")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The probe page
# ---------------------------------------------------------------------------
# ⚠ THE REAL PARTS, WIRED THE WAY `AnimaticEditor` WIRES THEM: `sceneAt` builds
# the moment from the SHOWN clips, <ProgramCanvas> draws it and puts the <video>
# in the document, and `useMonitorVideo` — given the FULL clip list, as the
# editor gives it — is the only thing that touches that element. The harness
# supplies exactly what the editor supplies: a clock, `playing`, `rate`, and a
# blob url per upload id.
PROBE_JSX_SOURCE = r"""
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { Compositor } from "/src/animatic/gl/compositor.js";
import ProgramCanvas from "/src/components/ProgramCanvas.jsx";
import { sceneAt } from "/src/animatic/scene.js";
import { useMonitorVideo } from "/src/animatic/useTimelineTransport.js";

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

const realEnd = Compositor.prototype.end;
Compositor.prototype.end = function patchedEnd() {
  probe.compositor = this;
  probe.draws = (probe.draws || 0) + 1;
  return realEnd.apply(this, arguments);
};

const SETTINGS = { fit: "contain", background: "#101010", aspect_ratio: "16:9" };
const SPAN = 5000;

// The clip as `interchange.to_project` builds one: `out_ms` null, `in_ms` 0,
// speed 1, sitting at the head of the timeline on its own picture track.
const CLIP = {
  id: "v1", label: "clip", kind: "video", track: 4,
  src: { kind: "video", upload_id: "u1" },
  start_ms: 0, duration_ms: SPAN,
  in_ms: 0, out_ms: null, speed: 1,
  scale: 1, x: 0.5, y: 0.5, opacity: 1, effects: [], keyframes: {},
};

/**
 * ⚠ SIX CLIPS ON A ROW THAT IS SWITCHED OFF, AHEAD OF THE VIDEO IN THE ARRAY.
 * That is the whole of the "hidden" case: the editor drops them from what it
 * hands `sceneAt`, so the video's `index` in the resolved picture is 0 while its
 * real position in the project is 6, and anything that reads `frames[index]`
 * lands six clips away. Colour cards, so they need no media and cannot draw over
 * anything — the only thing about them that matters is that they exist.
 */
const HIDDEN_ROW = Array.from({ length: 6 }, (_, i) => ({
  id: `h${i}`, label: `hidden ${i}`, kind: "color", color: "#802020", track: 3,
  src: { kind: "upload" },
  start_ms: 0, duration_ms: SPAN,
  in_ms: 0, out_ms: null, speed: 1,
  scale: 1, x: 0.5, y: 0.5, opacity: 1, effects: [], keyframes: {},
}));

// [what the project holds, what the editor shows]. `hidden` is the editor with
// `frames:3` switched off — `shown.frames` in `AnimaticEditor`.
const MODES = {
  plain: [[CLIP], [CLIP]],
  hidden: [[...HIDDEN_ROW, CLIP], [CLIP]],
};

function Harness() {
  const [mode, setMode] = useState("plain");
  const [timeMs, setTimeMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [videoUrls, setVideoUrls] = useState({});
  const videoElsRef = useRef({});
  const timeRef = useRef(0);

  probe.setMode = (next) => { setPlaying(false); timeRef.current = 0; setTimeMs(0); setMode(next); };
  probe.setPlaying = setPlaying;
  probe.videoElsRef = videoElsRef;
  probe.timeRef = timeRef;
  probe.setVideoUrls = setVideoUrls;
  // Move the playhead without starting playback — the scrub half of
  // `useMonitorVideo`, and the only way to land on an exact frame.
  probe.seek = (ms) => { timeRef.current = ms; setTimeMs(ms); };

  const [frames, shownFrames] = MODES[mode];
  const doc = useMemo(() => ({
    frames: shownFrames, texts: [], shapes: [], overlays: [], transitions: [],
    settings: SETTINGS,
  }), [shownFrames]);
  const scene = useMemo(
    () => sceneAt(doc, Math.min(timeMs, SPAN - 1), SPAN),
    [doc, timeMs]
  );
  probe.scene = scene;

  // ⚠ THE FULL LIST, exactly as `AnimaticEditor` passes it — the monitor and
  // this hook are both handed the project, not the filtered view.
  useMonitorVideo({ scene, frames, videoElsRef, playing, rate: 1 });

  // The transport's clock, cut down to the one branch this is about: a wall
  // clock, ticked on rAF, written to state so the monitor re-renders per frame.
  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0;
    let anchorWall = performance.now();
    let anchorT = timeRef.current;
    const tick = () => {
      const now = performance.now();
      const t = anchorT + (now - anchorWall);
      anchorT = t;
      anchorWall = now;
      if (t >= SPAN) { setPlaying(false); return; }
      timeRef.current = t;
      setTimeMs(t);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  return (
    <ProgramCanvas
      scene={scene}
      frames={frames}
      urls={{}}
      videoUrls={videoUrls}
      overlayUrls={{}}
      settings={SETTINGS}
      videoElsRef={videoElsRef}
      onUnavailable={(e) => { probe.unavailable = String(e); }}
    />
  );
}

createRoot(document.getElementById("root")).render(<Harness />);

// ⚠ FETCHED INTO A BLOB, exactly as `AnimaticEditor` fetches it. Every media
// path in this app is behind a bearer token, so a <video src> pointing straight
// at the file is not the code path the editor takes — and a blob url is where a
// wrong Content-Type would bite.
probe.load = async () => {
  const res = await fetch("/__probe_clip.mp4");
  const blob = await res.blob();
  probe.blobType = blob.type;
  probe.setVideoUrls({ u1: URL.createObjectURL(blob) });
};

/** What the <video> in the document is actually doing. */
probe.videoState = () => {
  const el = probe.videoElsRef.current.v1;
  if (!el) return { present: false };
  return {
    present: true,
    readyState: el.readyState,
    duration: el.duration,
    currentTime: Math.round(el.currentTime * 1000) / 1000,
    paused: el.paused,
    seeking: el.seeking,
    videoWidth: el.videoWidth,
    error: el.error ? el.error.code : null,
  };
};

/** True once the element has finished seeking and has a frame to hand over. */
probe.settled = () => {
  const el = probe.videoElsRef.current.v1;
  return Boolean(el && !el.seeking && el.readyState >= 2);
};

/** How the monitor filled `.an-gl-sources` — a <video>, or a still standing in. */
probe.sourceTags = () => [...document.querySelectorAll(".an-gl-sources *")]
  .map((el) => el.tagName);

/** The finished frame, sampled on a 4x4 grid so dithering cannot fake a change. */
probe.signature = () => {
  const comp = probe.compositor;
  if (!comp) return null;
  const [w, h] = comp.size;
  const px = comp.readPixels();
  const out = [];
  for (let gy = 0; gy < 4; gy++) {
    for (let gx = 0; gx < 4; gx++) {
      const x = Math.round(((gx + 0.5) / 4) * w);
      const y = Math.round(((gy + 0.5) / 4) * h);
      const i = (y * w + x) * 4;
      out.push(px[i], px[i + 1], px[i + 2]);
    }
  }
  return out;
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<meta charset="utf-8">
<title>monitor video probe</title>
<style>
  html, body { margin: 0; background: #000; }
  #root { width: 480px; height: 270px; position: relative; }
  #root canvas { width: 100%; height: 100%; display: block; }
  /* The same 1px corner the editor's stylesheet parks the sources in. */
  .an-gl-sources { position: absolute; width: 1px; height: 1px;
                   overflow: hidden; opacity: 0; pointer-events: none; }
</style>
<div id="root"></div>
<script type="module" src="/__probe_video.jsx"></script>
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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


def find_clip():
    hits = sorted(glob.glob(
        os.path.join(ROOT, "output", "_animatics", "*", "media", "vid_*.mp4")
    ))
    return hits[0] if hits else ""


def spread(a, b):
    """How far two frame signatures are apart, in 8-bit code values."""
    if not a or not b:
        return -1
    return max(abs(x - y) for x, y in zip(a, b))


def run_mode(page, mode, note):
    """One scenario, start to finish. Every check is named with its mode."""
    print(f"\n[{mode}] {note}")
    page.evaluate("(m) => window.__probe.setMode(m)", mode)
    page.wait_for_timeout(900)

    tags = page.evaluate("() => window.__probe.sourceTags()")
    check(f"[{mode}] the monitor put a <video> in the document, not a still",
          tags == ["VIDEO"], f"found {tags}")

    state = page.evaluate("() => window.__probe.videoState()")
    print(f"  video: {state}")
    check(f"[{mode}] the <video> exists and is registered under its clip id",
          bool(state.get("present")), str(state))
    check(f"[{mode}] the browser decoded it (readyState >= 2)",
          (state.get("readyState") or 0) >= 2, str(state))
    check(f"[{mode}] no media error", state.get("error") is None,
          str(state.get("error")))

    # --- Playback: is the element RUNNING? ---------------------------------
    # ⚠ THE TWO CHECKS THAT CAUGHT THE BUG. An uncued element stays paused at
    # time 0 for the whole clip while everything else about the monitor looks
    # perfectly healthy — same <video>, same texture, same draw loop.
    # ⚠ MEASURED AS A DELTA, never as "is it past 1s". An uncued element keeps
    # whatever time the previous case left it at, and a stale 4.9s reads as
    # "playing" to any absolute test — which is how this check passed while the
    # bug was live.
    before = state.get("currentTime") or 0
    page.evaluate("() => window.__probe.setPlaying(true)")
    page.wait_for_timeout(1600)
    mid_state = page.evaluate("() => window.__probe.videoState()")
    page.evaluate("() => window.__probe.setPlaying(false)")
    page.wait_for_timeout(200)
    print(f"  after 1.6s of play: {mid_state}")
    check(f"[{mode}] the element was actually playing, not left paused",
          mid_state.get("paused") is False, str(mid_state))
    moved = (mid_state.get("currentTime") or 0) - before
    check(f"[{mode}] its currentTime ran on by more than a second",
          moved > 1.0,
          f"{before:.3f}s -> {mid_state.get('currentTime')}s — never cued")

    # --- The picture: do the PIXELS follow the playhead? -------------------
    # ⚠ SCRUBBED, NOT PLAYED, and that is deliberate. A seek decodes exactly one
    # frame and says when it is ready; wall-clock playback under SwiftShader
    # stalls the software decoder often enough that "the frame did not change"
    # would mean "this machine is slow" as often as it meant a bug. The cue this
    # exercises is the same one — `useMonitorVideo` resolves the clip once and
    # both branches read it — so a wrongly-resolved clip fails here too.
    seen = []
    for at in (0, 2500, 5000 - 1):
        page.evaluate("(ms) => window.__probe.seek(ms)", at)
        page.wait_for_function("window.__probe.settled()", timeout=15000)
        # One more paint after the decode lands, so what is read back is the
        # frame the seek produced rather than the one before it.
        page.wait_for_timeout(350)
        page.evaluate("(ms) => window.__probe.seek(ms + 1)", at)
        page.wait_for_timeout(250)
        seen.append((at, page.evaluate("() => window.__probe.signature()")))
        print(f"  scrubbed to {at}ms: {page.evaluate('() => window.__probe.videoState()')}")

    for (a_ms, a), (b_ms, b) in zip(seen, seen[1:]):
        check(f"[{mode}] the picture at {b_ms}ms differs from the one at {a_ms}ms",
              spread(a, b) > 6, f"delta {spread(a, b)} — one frozen still")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", default="", help="an .mp4 to drive the monitor with")
    args = parser.parse_args()

    if not os.path.isdir(os.path.join(CLIENT, "node_modules")):
        print("  client/node_modules is missing — run `cd client && npm install` first.")
        return 2
    clip = args.clip or find_clip()
    if not clip or not os.path.isfile(clip):
        print("  No .mp4 to test with. Pass one with --clip.")
        return 2
    print(f"  clip: {clip}  ({os.path.getsize(clip) // 1024} KB)")

    shutil.copyfile(clip, PROBE_CLIP)
    with open(PROBE_JSX, "w", encoding="utf-8") as fh:
        fh.write(PROBE_JSX_SOURCE)
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
                # A probe page has no click in it, and a <video> that will not
                # start without one is not what this test is about.
                "--autoplay-policy=no-user-gesture-required",
            ])
            page = browser.new_page(viewport={"width": 900, "height": 600})
            page.goto(f"http://127.0.0.1:{port}/__probe_video.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            page.evaluate("() => window.__probe.load()")
            page.wait_for_timeout(1500)
            print(f"  blob Content-Type: {page.evaluate('() => window.__probe.blobType')!r}")

            run_mode(page, "plain", "one clip, nothing hidden — the case that always passed")
            run_mode(page, "hidden", "six clips on a switched-off row ahead of it — the bug")

            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, "; ".join(errors[:3]))
            check("WebGL never reported itself unavailable",
                  page.evaluate("() => window.__probe.unavailable || null") is None)

            browser.close()
    finally:
        if vite is not None:
            vite.terminate()
        for path in (PROBE_HTML, PROBE_JSX, PROBE_CLIP):
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
    print("A video clip moves in the Program monitor, hidden rows or not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
