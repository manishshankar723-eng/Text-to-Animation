"""End-to-end browser test of the animatic editor (Playwright + Chromium).

Layout, responsiveness and real behaviour — the things a build and a unit test
cannot see. It has already caught four real bugs: mis-aligned timeline gutter
labels, a waveform that never drew, a preview that wasn't the exported frame
shape, and whole-video settings that became unreachable once anything was
selected.

    pip install -r requirements-dev.txt
    python -m playwright install chromium

Then, in three terminals (ports are deliberately not the dev defaults so this
can't touch your real data):

    # 1. an isolated API
    API_JOB_STORE=memory API_LOCAL_JOBS_PATH= API_USER_STORE=local     API_LOCAL_USERS_PATH=/tmp/pw/users.json API_OUTPUT_DIR=/tmp/pw/output     API_UPLOAD_DIR=/tmp/pw/uploads JWT_SECRET=playwright-testing-secret-key-32     python -m uvicorn server.main:app --port 8124

    # 2. the client, pointed at it
    cd client && VITE_API_BASE=http://127.0.0.1:8124 npx vite --port 5199

    # 3. this
    python tests/e2e_animatic.py

Screenshots of every viewport land in %TEMP%/pw_test/shots.
"""

import math, os, struct, sys, tempfile, wave

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

# Screenshots go to `test_shots/`, which git ignores — never the repo root, and
# never the OS temp dir, where nobody thinks to look. See `tests/_shots.py`.
from _shots import shots_dir

APP = os.environ.get("PW_APP", "http://localhost:5199")
API = os.environ.get("PW_API", "http://127.0.0.1:8124")
SHOTS = shots_dir("e2e_animatic")

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

failures, notes = [], []
def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{extra}]" if extra else ""))
    if not cond:
        failures.append(f"{label} [{extra}]" if extra else label)

# --- fixtures --------------------------------------------------------------
TMP = tempfile.mkdtemp(prefix="pw_media_")
def png(name, size, colour):
    p = os.path.join(TMP, name)
    Image.new("RGB", size, colour).save(p, "PNG")
    return p
IMAGES = [png(f"{i:02d}.png", (1280, 720), (40 + 60 * i, 90, 160)) for i in range(3)]
WAV = os.path.join(TMP, "track.wav")
with wave.open(WAV, "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(i / 18))) for i in range(22050 * 8)))

# --- a logged-in session ---------------------------------------------------
EMAIL = "pw@example.com"
requests.post(f"{API}/auth/register", json={"email": EMAIL, "password": "hunter2222"}, timeout=20)
TOKEN = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": "hunter2222"},
                      timeout=20).json()["access_token"]

VIEWPORTS = [
    ("desktop-1920", 1920, 1080),
    ("laptop-1440", 1440, 900),
    ("small-1280", 1280, 800),
    ("stacked-1024", 1024, 768),
    ("phone-390", 390, 844),
]

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    ctx.add_init_script(
        f"localStorage.setItem('cas_token', {TOKEN!r}); localStorage.setItem('cas_email', {EMAIL!r});"
    )
    page = ctx.new_page()

    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    print("\n=== 1. app loads, logged in ===")
    page.goto(APP, wait_until="networkidle")
    check("sidebar rendered", page.locator(".sidebar").is_visible())
    check("landed logged-in (no login form)", page.locator(".sidebar").count() == 1)

    print("\n=== 2. Video Editor library ===")
    page.click("text=Video Editor")
    page.wait_for_selector(".lib-new", timeout=15000)
    check("page title", page.locator(".wf-title").inner_text().strip() == "Your Projects",
          page.locator(".wf-title").inner_text().strip())
    tiles = page.locator(".lib-new-row .lib-new")
    check("two New tiles", tiles.count() == 2, str(tiles.count()))
    a, b = tiles.nth(0).bounding_box(), tiles.nth(1).bounding_box()
    check("the two tiles sit SIDE BY SIDE", abs(a["y"] - b["y"]) < 4 and b["x"] > a["x"],
          f"y {a['y']:.0f}/{b['y']:.0f}")
    # ⚠ ONE SECTION, NOT TWO. "All Animatics" used to repeat the whole list
    # underneath "Recent", so the newest project was drawn on the page twice.
    heads = page.locator(".lib-section-title").all_inner_texts()
    check("one Recent Projects section", heads == ["Recent Projects"], str(heads))

    print("\n=== 2b. open it, touch nothing, leave — it must NOT be kept ===")
    def server_count():
        return len(requests.get(f"{API}/animatics",
                                headers={"Authorization": f"Bearer {TOKEN}"}, timeout=20).json())
    before = server_count()
    page.click(".lib-new-row .lib-new >> nth=0")
    page.wait_for_selector(".an-nle", timeout=15000)
    check("New goes straight into the editor", page.locator(".an-name-modal").count() == 0)
    page.wait_for_timeout(700)
    page.click(".an-topbar .back-btn")
    page.wait_for_selector(".lib-new", timeout=15000)
    page.wait_for_timeout(900)
    # The reported bug: an untouched animatic used to be kept, so the library
    # filled up with empty "Untitled animatic" rows.
    check("an untouched animatic is discarded on the way out",
          server_count() == before, f"{server_count()} vs {before} before")

    print("\n=== 3. open the editor (this one gets used) ===")
    page.click(".lib-new-row .lib-new >> nth=0")
    page.wait_for_selector(".an-nle", timeout=15000)
    # ⚠ NOTHING IS CREATED BY OPENING THE EDITOR ANY MORE. The project is born
    # at the first real action — here that is the "Save as…" a few steps down.
    # It used to be created on the way in and discarded on the way out, and the
    # discard only ran on ←, so every other exit left an empty "Untitled
    # Project" in the library for ever.
    check("opening the editor creates NOTHING yet", server_count() == before,
          f"{server_count()} vs {before} before")
    for region in ("an-topbar", "an-pane-media", "an-pane-program", "an-pane-props", "an-pane-timeline"):
        box = page.locator(f".{region}").bounding_box()
        check(f"{region} rendered with real size", box and box["width"] > 40 and box["height"] > 20,
              f"{box['width']:.0f}x{box['height']:.0f}" if box else "no box")

    print("\n=== 4. THE REPORTED BUG: does the workspace fill the screen? ===")
    m = page.evaluate("""() => {
        const d = document.documentElement, n = document.querySelector('.an-nle');
        return { docH: d.scrollHeight, vh: window.innerHeight, nleH: n.getBoundingClientRect().height,
                 docW: d.scrollWidth, vw: d.clientWidth };
    }""")
    check("no vertical page scroll (no empty area below)", m["docH"] <= m["vh"] + 2,
          f"document {m['docH']}px vs viewport {m['vh']}px")
    check("workspace fills the viewport height", m["nleH"] >= m["vh"] - 80,
          f"workspace {m['nleH']:.0f}px of {m['vh']}px")
    check("no horizontal page scroll", m["docW"] <= m["vw"] + 1, f"{m['docW']} vs {m['vw']}")

    print("\n=== 4b. top bar: quiet save state, button order ===")
    # A fresh animatic has nothing to report, so the save indicator must be
    # silent. A permanent "Saved" is noise — it's the default state.
    check("no permanent 'Saved' label on a fresh animatic",
          page.locator(".an-save").inner_text().strip() == "",
          repr(page.locator(".an-save").inner_text().strip()))
    save_btn = page.locator(".an-topbar button", has_text="Save")
    check("Save sits BEFORE Export",
          save_btn.bounding_box()["x"] < page.locator(".an-export").bounding_box()["x"])
    check("the ⚙ menu (which holds Delete) sits AFTER Export",
          page.locator(".an-settings-btn").bounding_box()["x"]
          > page.locator(".an-export").bounding_box()["x"])
    # An unnamed animatic must ASK for a name when you press Save — that is the
    # "save as" moment.
    check("Save on an unnamed animatic opens the name panel",
          not save_btn.is_disabled(), "should be pressable while unnamed")
    save_btn.click()
    page.wait_for_timeout(400)
    check("the 'Save as…' panel appears", page.locator(".an-name-modal").count() == 1)
    check("its Save is disabled until a name is typed",
          page.locator(".an-name-modal .btn.primary").is_disabled())
    page.locator(".an-name-input").fill("Episode 1 opening")
    page.click(".an-name-modal .btn.primary")
    page.wait_for_timeout(1000)
    check("the panel closes and the title takes",
          page.locator(".an-name-modal").count() == 0
          and page.locator(".an-title").input_value() == "Episode 1 opening",
          page.locator(".an-title").input_value())
    page.wait_for_timeout(1200)
    check("a NAMED animatic saves without asking again", save_btn.is_disabled())
    # Naming it IS doing something with it — that is the moment the project is
    # created on the server. See `ensureProject` in AnimaticEditor.jsx.
    check("naming it is what creates the project", server_count() == before + 1,
          f"{server_count()} vs {before} before")

    page.locator(".an-title").fill("Renamed by the test")
    page.wait_for_timeout(150)
    check("an edit says 'Unsaved changes'", "Unsaved" in page.locator(".an-save").inner_text(),
          page.locator(".an-save").inner_text().strip())
    page.wait_for_timeout(2000)
    check("the tick shows just after saving", "Saved" in page.locator(".an-save").inner_text(),
          page.locator(".an-save").inner_text().strip())
    page.wait_for_timeout(2600)
    check("...and then goes quiet again",
          page.locator(".an-save").inner_text().strip() == "",
          repr(page.locator(".an-save").inner_text().strip()))
    page.click(".an-settings-btn")
    page.wait_for_timeout(250)
    page.click(".an-settings-menu button:has-text('Delete project')")
    page.wait_for_timeout(250)
    check("Delete asks first", page.locator(".an-del-confirm").count() == 1)
    page.locator(".an-del-confirm button", has_text="Cancel").click()
    page.wait_for_timeout(250)

    # They were a mix of `btn small` and full-size `btn primary`, so they sat at
    # different heights and read as unrelated controls. Only the FILL should
    # tell them apart.
    # `icon` means ICON-ONLY: no label of its own. Detected by "has an <svg> and
    # no text", NOT by the glyph — Delete was a literal 🗑 when this was written
    # and is an <Icon name="trash"/> now, so matching on the character silently
    # found nothing and the squareness check below never ran.
    style = page.evaluate("""() => [...document.querySelectorAll('.an-topbar .btn')].map(b => {
        const r = b.getBoundingClientRect(), s = getComputedStyle(b);
        return { h: Math.round(r.height), w: Math.round(r.width), y: Math.round(r.top),
                 font: s.fontSize, radius: s.borderRadius, border: s.borderWidth,
                 icon: b.textContent.trim() === "" && !!b.querySelector('svg') };
    })""")
    for key, name in (("h", "height"), ("y", "baseline"), ("font", "font size"),
                      ("radius", "corner radius"), ("border", "border width")):
        vals = {s[key] for s in style}
        check(f"top-bar buttons share one {name}", len(vals) == 1, str(sorted(vals)))
    icon = next((s for s in style if s["icon"]), None)
    check("the icon-only Delete is square", icon and abs(icon["w"] - icon["h"]) <= 1,
          f"{icon['w']}x{icon['h']}" if icon else "not found")

    print("\n=== 4c. the workspace really fills the window ===")
    fit = page.evaluate("""() => {
        const n = document.querySelector('.an-nle').getBoundingClientRect();
        const t = document.querySelector('.an-pane-timeline').getBoundingClientRect();
        return { above: Math.round(n.top), below: Math.round(window.innerHeight - t.bottom),
                 statusbar: !!document.querySelector('.an-statusbar') };
    }""")
    check("no dead space under the timeline", fit["below"] <= 16, f"{fit['below']}px")
    check("framing is symmetric", abs(fit["above"] - fit["below"]) <= 2,
          f"top {fit['above']}px / bottom {fit['below']}px")

    print("\n=== 5. timeline track heights (the --tl-* variables) ===")
    # ⚠ THE TIMELINE HAS AS MANY LANES AS THE PROJECT HAS — never a fixed three.
    # This section used to name `.tl-bars` / `.tl-texts` / `.tl-audio` by hand,
    # so the SHAPES lane pushed the gutter to four rows and the alignment loop
    # below indexed off the end of that list. The whole run died there, taking
    # sections 6-14 with it, and it stayed dead because a crash reads like a
    # broken environment rather than a stale assertion. Walk what is rendered.
    sizes = page.evaluate("""() => ({
        rows: [...document.querySelectorAll('.tl-gutter-row')].map(e => Math.round(e.getBoundingClientRect().height)),
        tracks: [...document.querySelectorAll('.tl-lane')].map(e => Math.round(e.getBoundingClientRect().height)),
    })""")
    check("every layer button is the SAME size", len(set(sizes["rows"])) == 1, str(sizes["rows"]))
    check("every track is the SAME size", len(set(sizes["tracks"])) == 1, str(sizes["tracks"]))
    check("a gutter row for every lane", len(sizes["rows"]) == len(sizes["tracks"]),
          f"{len(sizes['rows'])} rows / {len(sizes['tracks'])} lanes")
    for cls, name in ((".tl-bars", "Images"), (".tl-texts", "Text"),
                      (".tl-shapes", "Shapes"), (".tl-audio", "Audio")):
        box = page.locator(cls).first.bounding_box()
        check(f"{name} track has height", box and box["height"] > 15,
              f"{box['height']:.0f}px" if box else "no box")
    # The gutter and the tracks are two COLUMNS generated from the one `lanes`
    # list, so row i must sit beside lane i whatever the project contains.
    rows = page.locator(".tl-gutter-row")
    lanes = page.locator(".tl-lane")
    ok = rows.count() == lanes.count()
    for i in range(min(rows.count(), lanes.count())):
        gb, tb = rows.nth(i).bounding_box(), lanes.nth(i).bounding_box()
        if abs(gb["height"] - tb["height"]) > 2 or abs(gb["y"] - tb["y"]) > 3:
            ok = False
    check("gutter labels line up with their tracks", ok,
          f"{rows.count()} rows / {lanes.count()} lanes")

    print("\n=== 5b. the Media pane has ONE way to add things ===")
    pane = page.locator(".an-pane-media")
    check("a single add control", pane.locator(".an-asset-drop").count() == 1,
          str(pane.locator(".an-asset-drop").count()))
    check("no separate '+ Add images'", pane.get_by_text("Add images", exact=False).count() == 0)
    check("no separate 'Add an MP3'", pane.get_by_text("Add an MP3").count() == 0)
    check("no empty Audio section before there is audio",
          pane.locator(".an-media-audio").count() == 0)

    print("\n=== 6. add images AND audio through that one control ===")
    page.locator('input[accept="image/*,video/*,audio/*"]').set_input_files(IMAGES + [WAV])
    page.wait_for_selector(".wave-canvas", timeout=30000)
    page.wait_for_selector(".fs-card:not(.fs-add)", timeout=30000)
    page.wait_for_timeout(1500)
    check("3 frame cards", page.locator(".fs-card:not(.fs-add)").count() == 3,
          str(page.locator(".fs-card:not(.fs-add)").count()))
    check("3 timeline bars", page.locator(".tl-bar").count() == 3, str(page.locator(".tl-bar").count()))
    check("preview shows a picture", page.locator(".an-screen img").is_visible())
    check("length reads 0:06", "0:06" in page.locator(".an-pane-timeline .an-tl-total").inner_text(),
          page.locator(".an-pane-timeline .an-tl-total").inner_text().strip())

    print("\n=== 7. per-frame duration typed in ===")
    dur = page.locator(".fs-dur-input").nth(1)
    dur.click(); dur.fill("5"); dur.press("Enter")
    page.wait_for_timeout(600)
    check("total became 0:09 after typing 5s",
          "0:09" in page.locator(".an-pane-timeline .an-tl-total").inner_text(),
          page.locator(".an-pane-timeline .an-tl-total").inner_text().strip())
    bars = [page.locator(".tl-bar").nth(i).bounding_box()["width"] for i in range(3)]
    check("that bar got proportionally wider", bars[1] > bars[0] * 2, f"{bars[0]:.0f} vs {bars[1]:.0f}")

    print("\n=== 8. text layer ===")
    page.click(".an-add-text")
    page.wait_for_selector(".tl-text", timeout=10000)
    check("a clip appears on the Text track", page.locator(".tl-text").count() == 1)
    check("Properties switched to Text",
          page.locator(".an-pane-props .an-pane-head .muted").inner_text().strip() == "Text",
          page.locator(".an-pane-props .an-pane-head .muted").inner_text().strip())
    page.locator(".an-tp-text").fill("A quiet classroom")
    page.wait_for_timeout(500)
    check("caption appears over the picture", page.locator(".an-text-clip").is_visible())
    check("caption text is right",
          page.locator(".an-text-clip").inner_text().strip() == "A quiet classroom",
          page.locator(".an-text-clip").inner_text().strip())
    scr = page.locator(".an-screen").bounding_box()
    cap = page.locator(".an-text-clip").bounding_box()
    check("caption sits INSIDE the frame", cap["x"] >= scr["x"] - 1 and
          cap["y"] + cap["height"] <= scr["y"] + scr["height"] + 1)
    check("caption is scaled to the frame, not a fixed size", cap["height"] > 20,
          f"{cap['height']:.0f}px tall")

    print("\n=== 8b. the preview must be the REAL frame shape ===")
    def screen_ratio():
        b = page.locator(".an-screen").bounding_box()
        return b["width"] / b["height"], b
    r, b = screen_ratio()
    check("16:9 project previews at 16:9", abs(r - 16 / 9) < 0.02,
          f"{b['width']:.0f}x{b['height']:.0f} = {r:.3f}, want 1.778")
    img = page.locator(".an-screen img").bounding_box()
    check("a 16:9 image fills it edge to edge (no false letterbox)",
          abs(img["width"] - b["width"]) < 3 and abs(img["height"] - b["height"]) < 3,
          f"img {img['width']:.0f}x{img['height']:.0f} vs box {b['width']:.0f}x{b['height']:.0f}")

    # There must be a way BACK to the whole-video settings once something is
    # selected — without it those settings are unreachable for the rest of the
    # session, which is how this check first failed.
    check("a '← Video' deselect exists while something is selected",
          page.locator(".an-pane-back").count() == 1)
    page.click(".an-pane-back")
    page.wait_for_timeout(400)
    check("clicking it reveals the whole-video settings",
          page.locator("text=Frame shape").count() == 1)
    check("...and the header says Video",
          page.locator(".an-pane-props .an-pane-head .muted").inner_text().strip() == "Video",
          page.locator(".an-pane-props .an-pane-head .muted").inner_text().strip())

    # Switch the project to 9:16 and confirm the preview really re-shapes.
    page.click(".an-pane-props .opt-chip >> nth=1")   # 9:16
    page.wait_for_timeout(800)
    r2, b2 = screen_ratio()
    check("9:16 project previews at 9:16", abs(r2 - 9 / 16) < 0.02,
          f"{b2['width']:.0f}x{b2['height']:.0f} = {r2:.3f}, want 0.563")
    check("the tall frame still fits inside the pane",
          b2["height"] <= page.locator(".an-screen-fit").bounding_box()["height"] + 2,
          f"{b2['height']:.0f}px vs pane {page.locator('.an-screen-fit').bounding_box()['height']:.0f}px")
    page.screenshot(path=os.path.join(SHOTS, "portrait-9x16.png"))
    page.click(".an-pane-props .opt-chip >> nth=0")   # back to 16:9
    page.wait_for_timeout(600)

    print("\n=== 9. audio + waveform ===")
    page.locator('input[accept="audio/*"]').set_input_files(WAV)
    page.wait_for_selector(".wave-canvas", timeout=30000)
    check("waveform canvas drawn", page.locator(".wave-canvas").is_visible())
    drawn = page.evaluate("""() => {
        const c = document.querySelector('.wave-canvas');
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
        return n;
    }""")
    check("waveform has actual pixels (not a blank canvas)", drawn > 500, f"{drawn} lit pixels")
    check("audio named in the Media pane",
          "track.wav" in page.locator(".an-media-name").first.inner_text())
    # Audio is a LAYER now: each track gets its own lane, gutter row and volume.
    # TWO tracks by this point, and that is correct — §6 put the same WAV in
    # through the Media pane's combined control, and this section added it again
    # through the audio picker. Two uploads are two tracks (music under a
    # voiceover is what the multi-track cap exists for), so assert the RELATION
    # — one lane, and one gutter row, per track — rather than a fixed number.
    audio_lanes = page.locator(".tl-audio").count()
    check("one audio lane per track", audio_lanes == 2, f"{audio_lanes} lanes for 2 uploads")
    check("one gutter row per audio lane",
          page.locator(".tl-gutter-audio").count() == audio_lanes,
          f"{page.locator('.tl-gutter-audio').count()} rows / {audio_lanes} lanes")
    # The volume slider lives in Properties, which follows the SELECTION — so a
    # track has to be selected before it can be there. Checking for it without
    # clicking was asserting that the pane shows a control for something nobody
    # picked, which is exactly what `selectOnly` exists to prevent.
    page.locator(".tl-audio").first.click(position={"x": 5, "y": 5})
    page.wait_for_timeout(400)
    check("selecting a track opens its Properties", page.locator(".an-vol").count() == 1,
          f"{page.locator('.an-vol').count()} volume sliders")
    check("...with the mute beside it", page.locator(".an-props .an-mute").count() == 1)
    check("an 'Add layer' control exists", page.locator(".tl-add-layer").count() == 1)

    print("\n=== 10. playback ===")
    page.click(".an-play")
    page.wait_for_timeout(1600)
    clock = page.locator(".an-clock").inner_text()
    check("the clock advanced while playing", clock.strip().split()[0] != "0:00", clock.strip())
    page.click(".an-play")

    print("\n=== 11. selecting a frame swaps the Properties pane ===")
    page.locator(".tl-bar").nth(0).click()
    page.wait_for_timeout(400)
    check("Properties switched to Frame",
          page.locator(".an-pane-props .an-pane-head .muted").inner_text().strip() == "Frame",
          page.locator(".an-pane-props .an-pane-head .muted").inner_text().strip())
    check("only one thing is selected at a time", page.locator(".tl-text.sel").count() == 0)

    print("\n=== 11b. a colour card — the clip you make without a file ===")
    # `addColorCard` shipped with Phase 3 and had NO caller, so the whole
    # `kind: "color"` path was unreachable from the UI while being fully built
    # and unit-tested underneath. Added AND REMOVED again inside this section,
    # so every duration assertion after it still sees the same three frames.
    bars_before = page.locator(".tl-bar").count()
    page.click(".an-add-card")
    page.wait_for_timeout(600)
    check("the card lands on the sequence",
          page.locator(".tl-bar").count() == bars_before + 1,
          f"{page.locator('.tl-bar').count()} bars, was {bars_before}")
    props = page.locator(".an-pane-props .an-props").inner_text()
    check("Properties calls it a Colour card", "Colour card" in props,
          props.split("\n")[1] if "\n" in props else props[:40])
    check("...and offers the one control it has",
          page.locator(".an-pane-props .an-colour").count() == 1,
          f"{page.locator('.an-pane-props .an-colour').count()} colour inputs")
    check("the swatch stands in for the missing picture",
          page.locator(".an-pane-props .an-prop-card").count() == 1)
    page.locator(".an-prop-actions .danger-btn").click()
    page.wait_for_timeout(500)
    check("removing it puts the sequence back",
          page.locator(".tl-bar").count() == bars_before,
          f"{page.locator('.tl-bar').count()} bars, was {bars_before}")

    page.screenshot(path=os.path.join(SHOTS, "editor-1920.png"), full_page=False)

    print("\n=== 12. every viewport ===")
    for name, w, h in VIEWPORTS:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(700)
        r = page.evaluate("""() => {
            const d = document.documentElement;
            const q = s => { const e = document.querySelector(s); if (!e) return null;
                             const b = e.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height}; };
            return { docW: d.scrollWidth, vw: d.clientWidth, docH: d.scrollHeight, vh: window.innerHeight,
                     media: q('.an-pane-media'), program: q('.an-pane-program'),
                     props: q('.an-pane-props'), timeline: q('.an-pane-timeline'),
                     bars: q('.tl-bars'), screen: q('.an-screen') };
        }""")
        check(f"[{name}] no horizontal scroll", r["docW"] <= r["vw"] + 1, f"{r['docW']} vs {r['vw']}")
        check(f"[{name}] timeline tracks still have height", r["bars"] and r["bars"]["h"] > 15,
              f"{r['bars']['h']:.0f}px" if r["bars"] else "missing")
        check(f"[{name}] picture still visible", r["screen"] and r["screen"]["w"] > 60 and r["screen"]["h"] > 40,
              f"{r['screen']['w']:.0f}x{r['screen']['h']:.0f}" if r["screen"] else "missing")
        if w > 1180 and h > 620:
            side = (r["media"]["x"] < r["program"]["x"] < r["props"]["x"]
                    and abs(r["media"]["y"] - r["props"]["y"]) < 4)
            check(f"[{name}] three panes side by side", side)
            check(f"[{name}] fits the viewport (no void)", r["docH"] <= r["vh"] + 2,
                  f"doc {r['docH']} vs vh {r['vh']}")
        else:
            check(f"[{name}] panes stack", r["media"]["y"] < r["program"]["y"], "expected stacking")
        page.screenshot(path=os.path.join(SHOTS, f"{name}.png"), full_page=False)

    print("\n=== 12b. an un-exported animatic must not claim to be exporting ===")
    page.set_viewport_size({"width": 1600, "height": 950})
    page.click(".an-topbar .back-btn")
    page.wait_for_selector(".lib-new", timeout=15000)
    page.wait_for_timeout(900)
    # ⚠ THE STATUS MOVED FROM THE THUMBNAIL TO THE DETAILS COLUMN when the
    # libraries became lists of rows: on a 72px thumbnail the word "Exporting…"
    # covered the very frame it described. Read the CHIPS, not `.lib-badge` —
    # that is now only the tiny "!" a failed job carries — or this check passes
    # by finding nothing rather than by finding nothing wrong.
    chips = page.locator(".lib-cell-meta .chip").all_inner_texts()
    # `queued` means "draft, never exported" for an animatic — the library used
    # to read it as work-in-progress and every row said "Exporting…" forever.
    check("no row falsely says 'Exporting…'",
          not any("Export" in t for t in chips), f"chips: {chips}")
    check("no spinner stuck on a thumbnail",
          page.locator(".lib-thumb-pic .spinner").count() == 0)

    print("\n=== 13. library at phone width ===")
    # Already on the library — 12b navigated back to it.
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_selector(".lib-new", timeout=15000)
    page.wait_for_timeout(600)
    r = page.evaluate("() => ({docW: document.documentElement.scrollWidth, vw: document.documentElement.clientWidth})")
    check("[phone] library has no horizontal scroll", r["docW"] <= r["vw"] + 1, f"{r['docW']} vs {r['vw']}")
    page.screenshot(path=os.path.join(SHOTS, "library-phone.png"), full_page=True)
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.wait_for_timeout(400)
    page.screenshot(path=os.path.join(SHOTS, "library-1920.png"), full_page=True)

    print("\n=== 14. console ===")
    real = [e for e in console_errors if "favicon" not in e.lower()]
    check("no console errors", not real, "; ".join(real[:3]))

    browser.close()

print(f"\nscreenshots -> {SHOTS}")
print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED:"))
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
