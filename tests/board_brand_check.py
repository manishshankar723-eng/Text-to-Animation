"""THE LOGO IS THE SAME FILE IN EVERY PANEL, BECAUSE THE MODEL NEVER DRAWS IT.

From the same report as `board_look_check.py` and `board_market_check.py` —
five screenshots of one 28-panel mobile-app promo:

    "logo har jagah change hua ek jaisa hona chahiye tha agar user nahi diya
     hai to"

Across that board, "Lickyeat" appeared as a fork-and-spoon roundel (shot 5), a
stylised L (shot 6), a noodle bowl (shot 11) and a grinning mouth (shot 12).

---------------------------------------------------------------------------
1. NO PROMPT CAN FIX THIS, WHICH IS WHY THE FIX IS NOT A PROMPT
---------------------------------------------------------------------------
An image model reconstructs a mark from its description every time it draws
one, and two reconstructions of the same description are never the same
picture. Asking more firmly buys a different wrong logo. So the model is taken
out of the job:

  1. it draws a FLAT SOLID MAGENTA placeholder — a thing it can produce
     identically every time, because there is nothing inside it to get wrong;
  2. `brand.stamp()` finds that magenta and pastes the user's real PNG in.

Bit-identical in every panel, because it IS the same file.

---------------------------------------------------------------------------
2. AND WITH NO UPLOAD, THE ANSWER IS NO LOGO
---------------------------------------------------------------------------
Not a generated one. `context()` then tells the model to leave app icons and
signage blank. A blank app icon is a design choice; four different logos for one
brand in one film is a broken film.

---------------------------------------------------------------------------
3. THE THREE WAYS THIS COULD SHIP SOMETHING WORSE THAN THE BUG
---------------------------------------------------------------------------
A placeholder scheme fails louder than what it replaces, so each failure has a
named guard, and each guard is pinned below:

  · the logo file goes missing between setup and drawing → the panel would ship
    with a BRIGHT MAGENTA SQUARE on the phone. `erase_markers()` repaints it.
  · the board has no brand at all, and a shot genuinely contains something
    magenta — a neon sign, a lit screen → erasing it would be US breaking the
    panel. Only a board that ASKED for a marker may have magenta repainted.
  · the model paints something large magenta (a wall, a jacket) → pasting a logo
    across a quarter of the frame is worse than no logo. It refuses.

Run:
    python tests/board_brand_check.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(label)


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import brand  # noqa: E402
import storyboard_pipeline as pipeline  # noqa: E402


def a_panel(marker_box=(150, 100, 250, 200), size=(400, 300)):
    """A fake panel: a scene with one rounded magenta app icon in it."""
    img = Image.new("RGB", size, (90, 110, 130))
    if marker_box:
        ImageDraw.Draw(img).rounded_rectangle(list(marker_box), radius=20,
                                              fill=brand.MARKER_RGB)
    return img


def a_logo(colour=(240, 120, 30, 255), size=(256, 256)):
    """A fake logo: a coloured ring on transparency, like a real brand PNG."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, size[0] - 8, size[1] - 8], fill=colour)
    d.ellipse([size[0] * 0.31, size[1] * 0.31, size[0] * 0.69, size[1] * 0.69],
              fill=(255, 255, 255, 255))
    return img


def magenta_pixels(img) -> int:
    arr = np.asarray(img.convert("RGB"), dtype=int)
    return int((np.abs(arr - np.array(brand.MARKER_RGB)).sum(axis=2)
                <= brand.MARKER_TOLERANCE).sum())


TMP = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "."
LOGO_PATH = os.path.join(TMP, "board_brand_check_logo.png")
a_logo().save(LOGO_PATH)

# ---------------------------------------------------------------------------
print("\n[1] the placeholder is asked for, and asked for bluntly")

branded = brand.prompt_context({"name": "Lickyeat", "logo_ref_id": "abc"}, "cinematic")
check("a board with a logo is told to draw a PLACEHOLDER, not the logo",
      "DO NOT DRAW THE LOGO" in branded and "PLACEHOLDER" in branded)
check("…in a colour it can name, with the hex to pin it",
      "MAGENTA" in branded and "#FF00FF" in branded)
check("⚠ …and flat, because an emboss or a shine survives the paste as a halo",
      all(w in branded for w in ("no gradient", "no shading", "no highlight",
                                 "no drop shadow")))
check("⚠ …and empty, because anything drawn inside it becomes damage",
      "no letters" in branded and "becomes damage" in branded)
check("⚠ the magenta is reserved — otherwise a magenta jacket becomes a logo",
      "for the brand mark ONLY" in branded)

check("the brand NAME is context, never letterforms",
      "Lickyeat" in branded and "do NOT letter the name" in branded)

# ---------------------------------------------------------------------------
print("\n[2] with no upload, nothing is invented")

unbranded = brand.prompt_context({"name": "Lickyeat"}, "cinematic")
check("a named brand with no logo file gets the invent-nothing rule",
      "do not invent any brand" in unbranded and "NO logo" in unbranded)
check("…and it is told what to draw INSTEAD, not just what to avoid",
      "blank rounded app icon" in unbranded)
check("⚠ …with the reason, because 'no logo' looks like a limitation "
      "until you know a made-up one changes between shots",
      "changes between shots" in unbranded)
check("no marker is requested when there is nothing to paste",
      "#FF00FF" not in unbranded)
check("an empty brand still produces a rule — never an empty string",
      bool(brand.prompt_context({}, "cinematic")))

check("⚠ a greyscale style gets the invent-nothing wording too: its panels are "
      "desaturated after generation, so a marker would be a grey square "
      "nothing could find",
      "#FF00FF" not in brand.prompt_context(
          {"name": "X", "logo_ref_id": "abc"}, "rough-sketch")
      and not brand.wants_marker({"logo_ref_id": "abc"}, "rough-sketch")
      and brand.wants_marker({"logo_ref_id": "abc"}, "cinematic"))

# ---------------------------------------------------------------------------
print("\n[3] the paste itself")

panel = a_panel()
before = magenta_pixels(panel)
stamped = brand.stamp(panel, LOGO_PATH, "cinematic")
check("the placeholder is found", len(brand.find_markers(panel)) == 1)
check("…and there is real magenta to replace", before > 1000)
check("⚠ NOT ONE MAGENTA PIXEL SURVIVES — a leftover fringe is a bright "
      "outline around the logo on every panel of the film",
      magenta_pixels(stamped) == 0)
check("the logo is actually there (the panel changed)",
      np.asarray(stamped).tobytes() != np.asarray(panel).tobytes())
check("…and the picture outside the placeholder is untouched",
      np.array_equal(np.asarray(stamped.convert("RGB"))[0:80, 0:80],
                     np.asarray(panel.convert("RGB"))[0:80, 0:80]))

# ⚠ THE SHAPE THE MODEL DREW IS THE SHAPE THAT SURVIVES. Repainting the
# bounding box instead would square off the rounded corners and leave four
# bright nubs against the scene — and on a tilted phone it would paste a
# straight rectangle onto a screen drawn in perspective.
corner = np.asarray(stamped.convert("RGB"))[101, 151]
scene = np.asarray(panel.convert("RGB"))[101, 151]
check("⚠ only the marker's own pixels are repainted, so the rounded corner is "
      "still scene and not a square white nub",
      np.array_equal(corner, scene))

two = a_panel(marker_box=(30, 30, 90, 90))
ImageDraw.Draw(two).rounded_rectangle([200, 150, 300, 250], radius=18,
                                      fill=brand.MARKER_RGB)
check("two placeholders in one shot are both filled — an icon AND a shop sign",
      len(brand.find_markers(two)) == 2
      and magenta_pixels(brand.stamp(two, LOGO_PATH, "cinematic")) == 0)

# A white-on-transparent logo is a real thing brands ship, and a white tile
# would swallow it whole.
white_logo_path = os.path.join(TMP, "board_brand_check_white.png")
a_logo(colour=(250, 250, 250, 255)).save(white_logo_path)
white_out = brand.stamp(a_panel(), white_logo_path, "cinematic")
tile = np.asarray(white_out.convert("RGB"))[150, 155]
check("⚠ a white logo gets a dark tile, or it would be invisible on the panel",
      int(tile.mean()) < 90)

# ---------------------------------------------------------------------------
print("\n[4] the three ways this could ship something worse")

gone = pipeline.stamp_brand(
    a_panel(), {"logo_ref_id": "abc", "logo_path": os.path.join(TMP, "nope.png")},
    "cinematic",
)
check("⚠ a logo file that has gone leaves NO magenta square on the panel — "
      "that would be far louder than the drifting logo this replaces",
      magenta_pixels(gone) == 0)

untouched = a_panel()
check("⚠ an UNBRANDED board never has magenta repainted: nothing asked for a "
      "marker, so magenta in frame is a neon sign the shot really contains",
      pipeline.stamp_brand(untouched, {}, "cinematic") is untouched)
check("…and a greyscale board is left alone whatever its brand says",
      pipeline.stamp_brand(untouched, {"logo_ref_id": "a", "logo_path": LOGO_PATH},
                           "rough-sketch") is untouched)

flooded = Image.new("RGB", (400, 300), brand.MARKER_RGB)
check("⚠ a panel painted mostly magenta is REFUSED, not stamped — a logo "
      "across a quarter of the frame is worse than no logo",
      brand.find_markers(flooded) == []
      and magenta_pixels(brand.stamp(flooded, LOGO_PATH, "cinematic")) > 10000)
check("a speck of magenta is noise, not a placeholder",
      brand.find_markers(a_panel(marker_box=(10, 10, 13, 13))) == [])

check("⚠ a stamping crash never costs the drawn panel — it has already been "
      "generated and paid for",
      "except Exception" in read("storyboard_pipeline.py").split(
          "def stamped_brand")[1].split("\ndef ")[0])

# ---------------------------------------------------------------------------
print("\n[5] the logo keeps its transparency on the way in")

main = read("server", "main.py")
check("there is a brand-logo upload route of its own",
      '@app.post("/brand/logo"' in main)
check("⚠ …because the character-reference upload flattens to RGB, which fills a "
      "logo's transparent background with black",
      'convert("RGBA").save(image_path, "PNG")' in main
      and 'convert("RGB").save(image_path, "PNG")' in main)
check("the client uses the brand route, not the character one",
      'request("/brand/logo"' in read("client", "src", "api.js"))

# ---------------------------------------------------------------------------
print("\n[6] the same file reaches every panel, and every redraw")

check("the board resolves the logo to a path ONCE and stores it",
      "def _resolve_brand(" in main and 'data["logo_path"] = path' in main)
check("⚠ …and drops the id when the file is missing, so the prompt does not ask "
      "for a placeholder nothing can fill",
      'data["logo_ref_id"] = ""' in main)
check("the board stores the brand for later redraws",
      '"brand": brand,' in main)
check("⚠ a RE-STYLE keeps the board's brand — the logo on the packaging is not "
      "an art style, and must not change between two versions of one board",
      '"brand": job.params.get("brand") or {},' in main)
check("a single-panel redraw keeps it",
      'brand=(job.params or {}).get("brand") or {},' in read("server", "common.py"))
check("…and so does a shot inserted between two others",
      'brand=params.get("brand") or {},' in read("server", "animatics.py"))

sbp = read("storyboard_pipeline.py")


def _stamps(func: str) -> bool:
    """Does this panel path put the logo on? Either spelling counts.

    `run_storyboard` calls `stamped_brand()`, which also reports whether a logo
    LANDED — the free audit needs that to notice the brand scheme silently doing
    nothing. The other two only need the picture, so they call `stamp_brand()`.
    """
    body = sbp.split(f"\ndef {func}(")[1].split("\ndef ")[0]
    return "stamp_brand(" in body or "stamped_brand(" in body


check("all three panel paths stamp",
      all(_stamps(f) for f in
          ("run_storyboard", "regenerate_panel", "draw_loose_shot")))
check("⚠ the board run uses the reporting form, so the audit can tell 'no shot "
      "needed a logo' apart from 'the placeholder scheme did nothing'",
      "stamped_brand(image, brand, style)" in sbp
      and 'panel["brand_stamped"] = logo_landed' in sbp)
def _stamps_after_conform(func: str) -> bool:
    """In this function's body, does the stamp come after the colour pass?"""
    body = sbp.split(f"\ndef {func}(")[1].split("\ndef ")[0]
    at = [body.index(n) for n in ("stamp_brand(", "stamped_brand(") if n in body]
    return bool(at) and "conform_to_style(" in body and body.index(
        "conform_to_style("
    ) < min(at)


check("⚠ …AFTER conform_to_style in every one of them, so a greyscale pass "
      "cannot desaturate the brand's own colours into a grey smudge",
      all(_stamps_after_conform(f)
          for f in ("run_storyboard", "regenerate_panel", "draw_loose_shot")))

g = read("gemini_client.py")
check("the panel prompt carries the brand block unconditionally",
      "parts.append(build_brand_context(brand, style))" in g)

# ---------------------------------------------------------------------------
print("\n[7] the placeholder in the SCRIPT dies too")

sb = read("script_breakdown.py")
check("⚠ the breakdown is given the real name and told to kill "
      "'[Your App Name]' — that bracket was burnt into a finished video's "
      "captions, read aloud",
      "brand_name" in sb and "[Your App Name]" in sb
      and "Never" in sb and "bracketed placeholder" in sb)
check("the route passes the name through",
      "brand_name=(body.brand.name if body.brand else \"\")" in main)

form = read("client", "src", "components", "ScriptToStoryboard.jsx")
check("the form offers a brand name and a logo upload",
      "Brand or app name" in form and "Upload logo" in form)
check("⚠ …and does NOT explain that to the user in a line under the row. It "
      "used to. 'No logo uploaded — we never invent a logo, because it would "
      "come out different in every panel' is our engineering read back at "
      "somebody who only wanted to draw a storyboard; an upload slot marked "
      "'No logo' already says what it wants. The reason lives in the code "
      "comment beside it, which is who actually needs it",
      "never invent a logo" not in form
      and "THE LOGO IS UPLOADED, NEVER GENERATED" in form)
check("changing the brand invalidates the board",
      "brand: effectiveBrand()," in form)
check("the name reaches the breakdown as well as the panels",
      form.count("effectiveBrand()") >= 3)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The logo is the same file in every panel, and where there is no file "
      "there is no logo.")
