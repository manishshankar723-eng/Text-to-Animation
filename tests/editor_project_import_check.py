"""THE IMPORT DIALOG SURVIVES BEING ACTED ON — the report does not vanish.

    "magar ek baat jab mai dekha mujhe dikha do path mera local ka music ka aur
     logo ka magar mai jab folder select kiya to us folder mai aur bhi music tha
     so mai jab folder select kiya to mai wapas aa gaya ye nhi ki wahi se chalu
     hua fir se try to anyway ke pass hi tha mai fir kuch nhi hua to mai bas usko
     chhor kar import kiya"

Three separate faults, in the order they were hit, and the last one is what cost
the import:

  1. **THE REPORT WAS THROWN AWAY THE MOMENT IT WAS ACTED ON.** `addMedia` called
     `setRead(null)`, so attaching the second folder wiped the only place the
     missing files and their FOLDERS were written down — the very thing the user
     had gone to fetch. On screen that reads as being sent back to the start.

  2. **AND THE WAY BACK WAS A REFUSAL.** With the report gone the footer flips to
     "Read the file", which for a `.prproj` is the STRICT read the server refuses
     on purpose. The "Try to read it anyway" offer is hidden once it has been
     taken (`guessed`), so the second refusal had no second offer: a dead end
     whose only exit is closing the dialog. *"fir kuch nhi hua."*

     ⚠ SINCE THEN THE REFUSAL HAS BEEN TAKEN OUT OF THE USER'S PATH ALTOGETHER
     — *"ye red text dikhne ka zaroori nahi hai user ko"*. The dialog knows the
     extension, so it asks for the best-effort read on the FIRST press: one
     upload instead of two, and the report (badged **BEST GUESS**) is what
     appears. The fix for fault 2 is still what makes the re-read work, and the
     stub below still refuses an unflagged read so a regression to the old
     two-step is a failure rather than a silent extra upload of 27 files.

  3. **AND THE FOLDER IT SENT THEM TO WAS SOMEBODY ELSE'S PROJECT.** The shared
     logo and the music bed live in another film's folder, which is full of other
     films' media — *"us folder mai aur bhi music tha"*. Attaching all of it is
     minutes of upload and a Media pane full of clips this cut never used.

⚠ THIS IS A BROWSER TEST BECAUSE THE FAULT WAS ONLY EVER VISIBLE AS A SEQUENCE.
Every individual piece of state was correct at the moment it was set; what was
wrong was what the third press did to the second press's result. A unit test of
`addMedia` on its own would have passed against all three.

    python tests/editor_project_import_check.py

No backend is needed: Vite is started here and the import route is answered by
Playwright's router — the harness is `editor_board_import_check.py`'s.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time

from playwright.sync_api import sync_playwright

# Screenshots go to `test_shots/`, which git ignores — never the repo
# root. See `tests/_shots.py`.
from _shots import shot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_pimport.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_pimport.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The fixture — the real import, cut down to the files that decided it
# ---------------------------------------------------------------------------
# ⚠ THE VOICEOVER RESOLVED AND THE MUSIC DID NOT, and both are .mp3. That is the
# whole shape of the live case: the project folder was attached and was not the
# wrong folder, it simply did not hold everything the project pointed at.
MUSIC_DIR = (
    "C:/Users/x/OneDrive/Immersive Quest work/"
    "Machine Learning Full LinkedIn Series/1_What is Machine Learning/Clips"
)
LOGO_DIR = "C:/Users/x/OneDrive/Immersive Quest work/Machine Learning Full LinkedIn Series"

FIRST_READ = {
    "frames": [], "texts": [], "shapes": [], "audio_tracks": [], "transitions": [],
    "name": "8_MCP_Model Context Protocol", "reader": "prproj", "fps": 24,
    "clips": 26, "audio_clips": 23, "video_tracks": 3, "video_lane_kinds": ["video"],
    "audio_lanes": 1, "text_lanes": 2, "shape_lanes": 1,
    "texts_read": 40, "shapes_read": 3, "transitions_read": 0, "matched": 25,
    "placeholders": ["ID_logo_RGB_XL.png", "ID_logo_RGB_XL.png", "music.mp3"],
    "missing": [
        {"name": "music.mp3", "folder": MUSIC_DIR, "kind": "sound", "clips": 1},
        {"name": "ID_logo_RGB_XL.png", "folder": LOGO_DIR, "kind": "picture", "clips": 2},
    ],
    "warnings": ["1 sound clip(s) had no file attached and were left out."],
    "rejected": [],
}
# The same read once the two files are attached — nothing missing any more.
SECOND_READ = {**FIRST_READ, "placeholders": [], "missing": [], "warnings": [],
               "matched": 27}

# Every import request, in the order it arrived: (had_experimental, media_names).
CALLS: list = []


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "*",
}

EXPERIMENTAL_PART = 'name="experimental"'
MEDIA_PART = 'name="media"'
FILENAME_KEY = 'filename="'


def route_api(route, request):
    if request.method == "OPTIONS":
        route.fulfill(status=204, headers=CORS)
        return

    body = request.post_data_buffer or b""
    text = body.decode("utf-8", "replace")
    experimental = EXPERIMENTAL_PART in text
    # The multipart parts named `media`, by the filename each one carries.
    names = []
    for part in text.split(MEDIA_PART)[1:]:
        head = part[:300]
        if FILENAME_KEY in head:
            names.append(head.split(FILENAME_KEY, 1)[1].split('"', 1)[0])
    CALLS.append((experimental, names))

    def send(payload, status=200):
        route.fulfill(status=status, headers=CORS, content_type="application/json",
                      body=json.dumps(payload))

    # ⚠ THE REFUSAL IS KEPT HERE AS A TRIPWIRE, NOT AS A STEP OF THE FLOW. The
    # ROUTE still refuses an unflagged `.prproj` by design — see
    # `interchange.ImportRefused` — but the dialog no longer walks a user through
    # that refusal: it knows the extension and sends the flag on the first
    # request. So this branch should now never be reached, and if it is, the
    # dialog has gone back to costing two uploads of the same folder.
    if not experimental:
        send({"detail": "A .prproj is Premiere's private save file. Export a "
                        "Final Cut Pro XML from Premiere instead."}, status=415)
        return
    send(SECOND_READ if names else FIRST_READ)


PROBE_JSX_SOURCE = r"""
import React from "react";
import { createRoot } from "react-dom/client";

import ProjectImportModal from "/src/components/ProjectImportModal.jsx";
import "/src/styles/index.css";

const probe = { errors: [], ready: false, applied: null };
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
  <ProjectImportModal
    open
    animaticId="probe"
    busy={false}
    onClose={() => {}}
    onApply={(report) => {
      probe.applied = report;
    }}
  />
);

/** What the footer's gold button says right now — "" when there isn't one. */
probe.primary = () => {
  const el = document.querySelector(".an-xchg-foot .btn.primary");
  return el ? (el.textContent || "").trim() : "";
};

/** The text of the missing-files panel, folders and all. */
probe.gone = () => {
  const el = document.querySelector(".an-xchg-gone");
  return el ? (el.textContent || "").trim() : "";
};

/** The "this is from the last read" banner, "" when the report is current. */
probe.again = () => {
  const el = document.querySelector(".an-xchg-again");
  return el ? (el.textContent || "").trim() : "";
};

/** How many footage files the dialog is holding, as it says so itself. */
probe.footage = () => {
  const el = Array.from(document.querySelectorAll(".an-xchg-pick .tiny.muted")).find(
    (n) => /file|Optional/.test(n.textContent || "")
  );
  return el ? (el.textContent || "").trim() : "";
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>project import probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_pimport.jsx"></script>
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


def make_files(work):
    """The document, and the OTHER FILM'S FOLDER the report sends the user to.

    ⚠ IT IS A REAL DIRECTORY, not a hand-picked list, because that is the gesture:
    "…or a whole folder" is a `webkitdirectory` input and it takes everything
    inside. ⚠ AND `other-song.mp3` AND `bed2.mp3` ARE THE TEST — they are the
    other film's music sitting beside the one file this cut wants, and taking
    them is the upload the user complained about.
    """
    shared = os.path.join(work, "Machine Learning Full LinkedIn Series")
    os.makedirs(shared, exist_ok=True)
    out = {}
    for name, where in (
        ("ep8.prproj", work),
        ("music.mp3", shared),
        ("ID_logo_RGB_XL.png", shared),
        ("other-song.mp3", shared),
        ("bed2.mp3", shared),
        ("unrelated.png", shared),
    ):
        path = os.path.join(where, name)
        with open(path, "wb") as fh:
            fh.write(b"x" * 64)
        out[name] = path
    out["_shared_dir"] = shared
    return out


def main():
    if not os.path.isdir(os.path.join(CLIENT, "node_modules")):
        print("  client/node_modules is missing — run `cd client && npm install` first.")
        return 2

    with open(PROBE_JSX, "w", encoding="utf-8") as fh:
        fh.write(PROBE_JSX_SOURCE)
    with open(PROBE_HTML, "w", encoding="utf-8") as fh:
        fh.write(PROBE_HTML_SOURCE)

    work = tempfile.mkdtemp(prefix="pimport_")
    files = make_files(work)
    port = free_port()
    vite = None
    try:
        vite = start_vite(port)
        if vite is None:
            print("  Vite would not start — cannot drive the dialog.")
            return 2

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1100, "height": 1400})
            page.route("**/interchange/import", route_api)
            page.goto(f"http://127.0.0.1:{port}/__probe_pimport.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_selector(".an-xchg-modal", timeout=30000)

            inputs = page.locator("input[type=file]")

            # -----------------------------------------------------------------
            print("\nChoosing the .prproj — read on the first press, no refusal in between")
            # -----------------------------------------------------------------
            inputs.nth(0).set_input_files(files["ep8.prproj"])
            page.wait_for_selector(".an-xchg-pick >> nth=1", timeout=5000)
            check("the footage pickers appear once a document is chosen",
                  inputs.count() == 3, f"{inputs.count()} file inputs")
            page.click(".an-xchg-foot .btn.primary")
            # ⚠ THE REPORT, NOT A RED PANEL. This used to be a refusal, a second
            # button, and a second upload of the same folder of footage —
            # *"ye red text dikhne ka zaroori nahi hai user ko"*. The stub still
            # refuses an unflagged read (see `route_api`), so a client that goes
            # back to reading strictly first fails HERE rather than quietly
            # costing an extra upload.
            page.wait_for_selector(".an-xchg-gone", timeout=15000)
            check("one press, one request — the flag was on the FIRST one",
                  len(CALLS) == 1 and CALLS[0][0] is True, str(CALLS))
            check("...so no refusal was ever put on screen",
                  page.locator(".error").count() == 0,
                  page.locator(".error").all_inner_texts()[:1])
            check("...and the offer to press again is gone with it",
                  not page.is_visible("text=Try to read it anyway"), "")
            # ⚠ AND THE HONESTY THAT PANEL CARRIED HAS TO STILL BE ON SCREEN.
            # Losing the refusal is only acceptable because the result says what
            # it is; a badge that quietly stopped rendering would turn this whole
            # change into a guess presented as a read.
            check("...but the result is still badged a guess",
                  page.is_visible(".an-xchg-guess"), "")

            # -----------------------------------------------------------------
            print("\nThe report names the folders it could not find")
            # -----------------------------------------------------------------
            gone = page.evaluate("() => window.__probe.gone()")
            check("the missing sound is named", "music.mp3" in gone, gone[:160])
            # ⚠ THE WHOLE REASON THIS PANEL WAS REWRITTEN. Without the folder the
            # user has a filename and nowhere to go.
            check("...and so is the FOLDER it lived in",
                  "1_What is Machine Learning" in gone, gone[:240])
            check("...and the logo's folder too, listed separately",
                  gone.count("Machine Learning Full LinkedIn Series") >= 2, gone[:400])
            check("a file wanted by two clips is listed ONCE, with a count",
                  gone.count("ID_logo_RGB_XL.png") == 1 and "\u00d72" in gone, gone[:240])
            check("the report is current, so there is no 'read again' banner",
                  page.evaluate("() => window.__probe.again()") == "", "")
            check("...and the gold button offers to ADD it",
                  "Add" in page.evaluate("() => window.__probe.primary()"),
                  page.evaluate("() => window.__probe.primary()"))

            # -----------------------------------------------------------------
            print("\nFetching the folder it named — the gesture that used to reset")
            # -----------------------------------------------------------------
            # Somebody else's project folder, whole: the two files this cut
            # wants, and three it does not.
            inputs.nth(2).set_input_files(files["_shared_dir"])
            # ⚠ TOLERATED, NOT WAITED ON. The bug this section is about makes the
            # banner never appear, and a hard `wait_for_selector` turns that into
            # a traceback instead of the four named failures below — which is the
            # difference between a test that reports a fault and one that just
            # dies.
            try:
                page.wait_for_selector(".an-xchg-again", timeout=5000)
            except Exception:  # noqa: BLE001 — the assertions say what went wrong
                pass
            # ⚠ FAULT 3. Only what the report actually asked for is taken.
            footage = page.evaluate("() => window.__probe.footage()")
            check("only the files the report NAMED are taken from that folder",
                  footage.startswith("2 files"), footage)
            # ⚠ FAULT 1. This is the assertion the bug fails: the panel the user
            # was reading is still on screen after they acted on it.
            still = page.evaluate("() => window.__probe.gone()")
            check("the report is still on screen after footage is added",
                  "1_What is Machine Learning" in still, still[:200])
            check("...and it says plainly that it is out of date",
                  "last" in page.evaluate("() => window.__probe.again()").lower(),
                  page.evaluate("() => window.__probe.again()"))
            # ⚠ AND IT MUST NOT BE ADDABLE. The clips on screen were read WITHOUT
            # the footage just attached; adding them now would put the placeholder
            # cards on the timeline anyway.
            check("...so the gold button no longer offers to add it",
                  page.evaluate("() => window.__probe.primary()") == "Read the file again",
                  page.evaluate("() => window.__probe.primary()"))

            # -----------------------------------------------------------------
            print("\nReading again — and it must not walk into the refusal")
            # -----------------------------------------------------------------
            before = len(CALLS)
            page.click(".an-xchg-foot .btn.primary")
            try:
                page.wait_for_function(
                    "() => window.__probe.primary().startsWith('Add')", timeout=15000
                )
            except Exception:  # noqa: BLE001 — same reason as above
                pass
            # ⚠ FAULT 2. `readFile()` with no argument is the STRICT read, which
            # for a `.prproj` is a 415 with the experimental offer already spent —
            # the dead end the user gave up at. The second read must carry the
            # flag the first one used.
            check("the second read keeps the experimental flag it read with",
                  CALLS[before][0] is True, str(CALLS[before:]))
            # ⚠ BY BASENAME. A `webkitdirectory` pick carries the folder in the
            # part's filename ("Shared/music.mp3"), and the route basenames it
            # before matching — `_store_import_media` in `server/animatics.py`.
            sent = sorted(n.rsplit("/", 1)[-1] for n in CALLS[before][1])
            check("...and it sends the footage that was just attached",
                  sent == ["ID_logo_RGB_XL.png", "music.mp3"], str(CALLS[before][1]))
            check("nothing is missing any more",
                  page.evaluate("() => window.__probe.gone()") == "", "")
            check("...the out-of-date banner is gone",
                  page.evaluate("() => window.__probe.again()") == "", "")
            check("...and the import can finally be added",
                  page.evaluate("() => window.__probe.primary()").startswith("Add"),
                  page.evaluate("() => window.__probe.primary()"))
            if page.evaluate("() => window.__probe.primary()").startswith("Add"):
                page.click(".an-xchg-foot .btn.primary")
            check("pressing it hands the report to the editor",
                  (page.evaluate("() => window.__probe.applied") or {}).get("matched") == 27,
                  str(page.evaluate("() => window.__probe.applied"))[:160])

            # -----------------------------------------------------------------
            print("\nThe title bar - pinned to the top, close button in the corner")
            # -----------------------------------------------------------------
            # ⚠ NO SOURCE CHECK CAN SEE THIS ONE. The CSS said `top: 0.35rem`
            # and the ✕ still sat a whole card-padding lower — *"kaha hua niche
            # hi to dikh raha hai"* — because a STICKY box is pinned by its
            # MARGIN box, so `top: 0` on the bar quietly cancelled the negative
            # top margin that pulls it to the top of the card. Every grep about
            # it was green. So this measures pixels.
            geom = page.evaluate(
                "() => { const c = document.querySelector('.an-xchg-modal');"
                " const x = c.querySelector('.modal-close');"
                " const cr = c.getBoundingClientRect(), xr = x.getBoundingClientRect();"
                " return { top: xr.top - cr.top, right: cr.right - xr.right,"
                " w: xr.width, h: xr.height }; }"
            )
            check("the close button sits in the corner, not beside the heading",
                  geom["top"] <= 12 and geom["right"] <= 14, json.dumps(geom))
            # ⚠ AND IT IS STILL A TARGET. The glyph is one character; shrinking
            # the padding to move it would make the one way out of this dialog
            # (RULEBOOK E65) a 12px speck in the corner of the screen.
            check("...and is still big enough to hit",
                  geom["w"] >= 20 and geom["h"] >= 20, json.dumps(geom))
            # ⚠ THE WHOLE POINT OF THE STICKY BAR: the report is long, and a ✕
            # that scrolls away leaves a dialog that cannot be closed at all.
            page.eval_on_selector(".an-xchg-modal", "el => el.scrollTop = 400")
            page.wait_for_timeout(200)
            scrolled = page.evaluate(
                "() => { const c = document.querySelector('.an-xchg-modal');"
                " const x = c.querySelector('.modal-close');"
                " return x.getBoundingClientRect().top - c.getBoundingClientRect().top; }"
            )
            check("...and it stays there when the report is scrolled",
                  scrolled <= 12, f"{scrolled} px below the top of the card")
            page.eval_on_selector(".an-xchg-modal", "el => el.scrollTop = 0")
            print("\nAfterwards")
            errors = page.evaluate("() => window.__probe.errors")
            check("nothing reached window.onerror or console.error",
                  not errors, json.dumps(errors)[:400])
            if failures:
                page.screenshot(path=shot("pimport_probe_failed.png"))
            browser.close()
    finally:
        if vite is not None:
            vite.terminate()
        shutil.rmtree(work, ignore_errors=True)
        for path in (PROBE_JSX, PROBE_HTML):
            try:
                os.remove(path)
            except OSError:
                pass

    print()
    if failures:
        print(f"{len(failures)} FAILED")
        for f in failures:
            print("  -", f)
        return 1
    print("The import dialog keeps its report, keeps its route, and takes only "
          "what it asked for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
