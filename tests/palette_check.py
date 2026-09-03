"""palette_check.py — THE COLOUR THEME IS DERIVED, AND WHAT COMES OUT IS LEGIBLE.

    python tests/palette_check.py     (no backend, no dollar; needs node)

The Brand screen now hands an administrator two colours — an accent and a ground
— and `client/src/palette.js` turns them into every token the app paints with,
for BOTH light and dark mode. That was asked for in as many words:

    "mai chahta hun ki tum theme colour style banao brand ke under hi user user
     mai change kar saku … only dark blue colour hai isliye mujhe change kar ke
     dekhna hai kismai thik lagega … aur haa jab kare color change to sab jagah
     achhe se ho jaye aur test v dekhe achhe se isliye production level mai kaam
     jaise hota hai waise karna"

⚠ **THE ONE WAY A COLOUR PICKER SHIPS A BROKEN PRODUCT IS BY LETTING SOMEBODY
CHOOSE GREY TEXT ON A GREY PANEL** — and nobody finds out until a customer
cannot read their own invoice. So the centre of this file is not "does it emit
CSS", it is **WCAG contrast, measured on the real derived values, for every
preset, in both themes**. A preset that fails here never reaches the panel.

⚠ **IT RUNS THE REAL MODULE UNDER NODE RATHER THAN REIMPLEMENTING THE MATHS IN
PYTHON.** Two copies of a colour derivation is RULEBOOK's "a rule written twice"
in its purest form: the Python one would be the one nobody updates, and it would
go green over a stylesheet that had drifted underneath it. `palette.js` is the
only place the maths lives; this bundles it with esbuild and reads the answer.

⚠ **AND THE TOKEN NAMES ARE CHECKED AGAINST `theme.css` ITSELF.** A derived
`--panel2` (no hyphen) is a variable nothing reads: the app keeps its old colour,
every test here still passes, and the bug is invisible until somebody opens the
screen. Every name emitted must be one `theme.css` actually defines.

⚠ **THE BUILT-IN LOOK MUST EMIT NOTHING AT ALL.** `theme.css` is hand-tuned over
a long series of live tests and no derivation reproduces it to the byte, so a
deployment that never opens this screen has to render through exactly the CSS it
always did. `cssFor(default)` returning "" is what makes shipping this a feature
rather than a silent restyle of everybody's app.

Sections 6 and 7 render the real `ThemePicker` (RULEBOOK E90 — a green
`npm run build` is not evidence a screen renders) and prove the detector-style
claims about the panel's own wiring: the live preview, and the put-back that
stops an unsaved colour outliving the tab.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ===========================================================================
# The harness. See `editor_chat_render_check.py` for the same shape and the
# reasoning behind the entry living inside `client/`.
# ===========================================================================
ENTRY = """
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as P from "./src/palette.js";
import { ThemePicker } from "./src/admin/AdminBrand.jsx";

const cases = {};
for (const preset of P.PRESETS) {
  cases[preset.id] = { id: preset.id };
}
// Two colours nobody would choose, which is exactly why they are here: the
// panel cannot stop somebody typing them, so the derivation has to survive it.
cases["__white_on_white"] = { id: "custom", accent: "#ffffff", ground: "#fdfdfd" };
cases["__black_on_black"] = { id: "custom", accent: "#050505", ground: "#000000" };
cases["__hot_pink"] = { id: "custom", accent: "#ff00aa", ground: "#1c0f18" };

const out = { palettes: {}, junk: {}, render: {} };
for (const [key, raw] of Object.entries(cases)) {
  const pal = P.normalisePalette(raw);
  out.palettes[key] = {
    palette: pal,
    tokens: P.derive(pal),
    css: P.cssFor(pal),
    builtIn: P.isBuiltIn(pal),
  };
}

// ⚠ EVERY ONE OF THESE REACHES `normalisePalette` IN REAL LIFE — from the wire,
// from localStorage, and from a half-typed colour field.
out.junk = {
  nothing: P.normalisePalette(undefined),
  empty: P.normalisePalette({}),
  garbage: P.normalisePalette({ id: "../../etc", accent: "red", ground: 7 }),
  half: P.normalisePalette({ id: "custom", accent: "#ab" }),
  // A preset chosen and then one colour changed. The id must stop being the
  // preset's the moment the colours stop being the preset's.
  edited: P.normalisePalette({ id: "gold", accent: "#ff0000", ground: "#13161f" }),
  // …and a preset round-tripped through the store, colours and all, is still
  // that preset — this is exactly what comes back from a save.
  roundTrip: P.normalisePalette({
    id: "emerald", accent: "#34d399", ground: "#101815",
  }),
  short: P.normalisePalette({ id: "custom", accent: "#fff", ground: "#000" }),
  unknownPreset: P.normalisePalette({ id: "no-such-theme" }),
};

out.contrastSelfCheck = {
  blackOnWhite: P.contrast("#000000", "#ffffff"),
  same: P.contrast("#123456", "#123456"),
};

// ⚠ THE DERIVED TEXT IS PROVED TO BE UNBREAKABLE, ACROSS THE WHOLE GREY RANGE.
// This started out as a hunt for a ground the correction could not save; there
// isn't one, and that is the finding worth keeping — `readable()` can always
// reach black or white, so body text, quiet text and the accent-as-text clear
// 4.5:1 whatever is typed. The sweep stays so that a future change to the
// derivation which BREAKS that is caught the day it lands.
let floor = 21;
let floorGround = null;
for (let v = 0; v <= 255; v += 1) {
  const hex = "#" + v.toString(16).padStart(2, "0").repeat(3);
  const t = P.derive({ id: "custom", accent: hex, ground: hex });
  for (const mode of ["dark", "light"]) {
    const low = Math.min(
      P.contrast(t[mode]["--text"], t[mode]["--panel"]),
      P.contrast(t[mode]["--muted"], t[mode]["--panel"]),
      P.contrast(t[mode]["--primary"], t[mode]["--panel"]));
    if (low < floor) { floor = low; floorGround = hex + " (" + mode + ")"; }
  }
}
out.textFloor = { ratio: floor, at: floorGround };

// ⚠ AND THE ONE THING NOTHING CAN CORRECT: an accent the same colour as the
// ground. The fill is the chosen colour on the chosen ground, so the buttons
// simply disappear - RULEBOOK E66 by a new door. This is what the warning is
// for, and it is reached by a choice somebody can genuinely make in two clicks.
out.invisible = "#4a4a4a";

// The panel itself, rendered — not read as text.
const row = {
  name: "Acme", theme_id: "emerald", accent: "#34d399", ground: "#101815",
  default_theme: { theme_id: "gold", accent: "#e5c158", ground: "#13161f" },
};
out.render.emerald = renderToStaticMarkup(
  React.createElement(ThemePicker, { row, busy: "", onRun: () => {} }));
out.render.unreadable = renderToStaticMarkup(
  React.createElement(ThemePicker, {
    row: { ...row, theme_id: "custom", accent: out.invisible, ground: out.invisible },
    busy: "", onRun: () => {},
  }));

process.stdout.write(JSON.stringify(out));
"""


def run_js():
    if not shutil.which("node"):
        print("  node is not on PATH — nothing checked.")
        return None
    if not (CLIENT / "node_modules" / "react-dom").exists():
        print("  client/node_modules is missing — run `cd client && npm install`.")
        return None

    work = tempfile.mkdtemp(prefix="palette_")
    entry = CLIENT / "__palette_entry.jsx"
    try:
        entry.write_text(ENTRY, encoding="utf-8")
        bundle = os.path.join(work, "bundle.cjs")
        esbuild = CLIENT / ("node_modules/.bin/esbuild.cmd" if os.name == "nt"
                            else "node_modules/.bin/esbuild")
        build = subprocess.run(
            [str(esbuild), str(entry), "--bundle", "--platform=node", "--format=cjs",
             "--loader:.js=jsx", "--jsx=automatic", f"--outfile={bundle}",
             # The panel imports `api.js`, which reads Vite's `import.meta.env`
             # at module scope. Under node that object does not exist and the
             # bundle throws before a single check runs — which reads exactly
             # like the component being broken. One base URL is all it needs;
             # nothing here makes a request.
             '--define:import.meta.env={"VITE_API_BASE":"http://127.0.0.1:8000"}',
             "--log-level=error"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(CLIENT),
        )
        if build.returncode != 0:
            print("    esbuild said:", (build.stderr or "").strip()[:1500])
            return None
        proc = subprocess.run(["node", bundle], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(CLIENT))
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:2000])
            return None
        return json.loads(proc.stdout)
    finally:
        entry.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ===========================================================================
# Colour maths, Python side — used ONLY to re-measure what JS produced, never
# to produce anything. If these two ever disagree the JS is right.
# ===========================================================================
def _rgb(hex_or_rgba: str):
    s = hex_or_rgba.strip()
    m = re.match(r"^#([0-9a-fA-F]{6})$", s)
    if m:
        v = m.group(1)
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    m = re.match(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
    if m:
        return tuple(int(g) for g in m.groups())
    return None


def _lum(hexval: str) -> float:
    rgb = _rgb(hexval)
    if rgb is None:
        return 0.0

    def f(v):
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ===========================================================================
print("1 · the module loads and derives something for every preset")

data = run_js()
if data is None:
    print("\nSKIPPED: the JS harness could not run — nothing was checked.")
    sys.exit(1)

palettes = data["palettes"]
preset_ids = [p for p in palettes if not p.startswith("__")]
check(f"every preset derived ({len(preset_ids)})", len(preset_ids) >= 8)
check("the self-check maths agrees with WCAG",
      abs(data["contrastSelfCheck"]["blackOnWhite"] - 21) < 0.01
      and abs(data["contrastSelfCheck"]["same"] - 1) < 0.001)


# ===========================================================================
print("\n2 · the built-in look injects NOTHING, and everything else injects both blocks")

builtin = palettes["gold"]
check("the built-in palette is recognised as built-in", builtin["builtIn"] is True)
check("…and emits no CSS at all", builtin["css"] == "",
      detail=f"emitted {len(builtin['css'])} chars")

others = {k: v for k, v in palettes.items() if k != "gold"}
check("every other palette emits CSS", all(v["css"] for v in others.values()))
check("…with a `:root` block for dark",
      all(":root {" in v["css"] for v in others.values()))
# ⚠ A PLAIN `:root` CANNOT BEAT `:root[data-theme="light"]` — that is a
# specificity loss, not an ordering one. Without this block light mode keeps the
# shipped gold while dark mode changes, which looks like the feature half-works.
check("…and the light block carries the attribute selector theme.css uses",
      all(':root[data-theme="light"] {' in v["css"] for v in others.values()))

theme_css = read("client", "src", "styles", "theme.css")
light_selector = ':root[data-theme="light"] {' in theme_css
check("…and theme.css really writes that same selector", light_selector)


# ===========================================================================
print("\n3 · every derived name is a variable theme.css actually defines")

defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", theme_css))
emitted = set()
for v in others.values():
    emitted |= set(v["tokens"]["dark"]) | set(v["tokens"]["light"])
unknown = sorted(emitted - defined)
# A typo here is a variable nothing reads: the app silently keeps its old colour
# and every other check in this file still passes.
check("no derived token is a name nothing reads", unknown == [],
      detail=", ".join(unknown))
check("the derivation covers the ground and the accent",
      {"--bg", "--panel", "--panel-2", "--border", "--text", "--muted",
       "--primary", "--gold-fill", "--gold-ink"} <= emitted)

# ⚠ THE CONTENT COLOURS ARE DELIBERATELY NOT DERIVED. The timeline's orange /
# pink / purple / green / violet say WHAT A CLIP HOLDS — asked for by name, and
# pinned by the notes in theme.css. Re-hueing them with the accent would trade a
# learned signal for decoration.
content = {t for t in defined if t.startswith("--clip-") or t.startswith("--lane-edge-")}
check("content colours are left alone", not (emitted & content),
      detail=", ".join(sorted(emitted & content)))

# ⚠ AND THIS IS THE CHECK THAT MAKES "EVERYWHERE" TRUE AND KEEPS IT TRUE.
# The feature was asked for as *"jab kare color change to sab jagah achhe se ho
# jaye"*, and the thing standing in the way was not the derivation — it was 69
# rules across 20 stylesheets that wrote `rgba(229, 193, 88, 0.1)` by hand: a
# tinted row, a glow, a selected clip. Every one of those would have stayed gold
# on a deployment that chose green, and NOTHING here would have gone red. They
# are `rgba(var(--accent-rgb), …)` now, and the 70th is what this catches.
#
# ⚠ theme.css is the ONE file allowed to name the colour, because that is where
# the token is defined. Comments anywhere are fine — several of them explain why
# the gold is what it is, and stripping those would cost more than it saves.
STYLE_DIR = os.path.join(ROOT, "client", "src", "styles")
GOLD = re.compile(r"rgba\(\s*229,\s*193,\s*88|rgba\(\s*189,\s*141,\s*30|#e5c158|#bd8d1e",
                  re.I)
COMMENT = re.compile(r"/\*.*?\*/", re.S)
leaks = []
for name in sorted(os.listdir(STYLE_DIR)):
    if not name.endswith(".css") or name == "theme.css":
        continue
    body = COMMENT.sub("", read("client", "src", "styles", name))
    for line in body.splitlines():
        if GOLD.search(line):
            leaks.append(f"{name}: {line.strip()[:70]}")
check(f"no stylesheet hard-codes the accent any more ({len(leaks)} found)",
      leaks == [], detail=" | ".join(leaks[:6]))

theme_defines = re.search(r"--accent-rgb\s*:", theme_css) is not None
check("…because theme.css defines the token they all use", theme_defines)
check("…and the palette overrides it, or nothing would follow the accent",
      "--accent-rgb" in emitted and "--accent-on-white" in emitted)


# ===========================================================================
print("\n4 · WHAT COMES OUT IS READABLE — every preset, both themes")

# WCAG AA for body text. `--text` and `--muted` carry sentences; `--primary` is
# a link, a running state and a selected row's label, so it carries the same bar.
AA = 4.5
for pid in preset_ids:
    tok = palettes[pid]["tokens"]
    for mode in ("dark", "light"):
        t = tok[mode]
        panel = t["--panel"]
        for name in ("--text", "--muted", "--primary"):
            r = ratio(t[name], panel)
            check(f"{pid} · {mode} · {name} on --panel is {r:.1f}:1", r >= AA,
                  detail=f"{t[name]} on {panel} — needs {AA}")

        # A filled accent button carries a short bold label, which is WCAG's
        # large-text case: 3:1. theme.css says exactly this about its own gold.
        r = ratio(t["--gold-ink"], t["--gold-fill"])
        check(f"{pid} · {mode} · button ink on the accent fill is {r:.1f}:1", r >= 3.0,
              detail=f"{t['--gold-ink']} on {t['--gold-fill']}")

        # A border nobody can see is a card with no edge, which is the fault
        # `--nav-stroke` exists in theme.css to fix. Not a WCAG number — a
        # visible-separation one.
        r = ratio(t["--border"], panel)
        check(f"{pid} · {mode} · --border is visible against --panel ({r:.2f})", r >= 1.15,
              detail=f"{t['--border']} on {panel}")


# ===========================================================================
print("\n5 · a bad pick is survived, and junk never produces a partial palette")

for key in ("__white_on_white", "__black_on_black", "__hot_pink"):
    t = palettes[key]["tokens"]
    for mode in ("dark", "light"):
        # ⚠ NOT "IT LOOKS GOOD" — the guarantee for a colour nobody should have
        # chosen is that `readable()` still drags the text far enough from the
        # ground to be READ. The panel warns about these; it does not refuse
        # them, because refusing a deployment's own brand colour is not this
        # screen's call to make.
        r = ratio(t[mode]["--text"], t[mode]["--panel"])
        check(f"{key} · {mode} · body text is still readable ({r:.1f}:1)", r >= AA,
              detail=f"{t[mode]['--text']} on {t[mode]['--panel']}")

junk = data["junk"]
HEX = re.compile(r"^#[0-9a-f]{6}$")
for name, pal in junk.items():
    ok = (HEX.match(pal["accent"] or "") and HEX.match(pal["ground"] or "")
          and isinstance(pal["id"], str) and pal["id"])
    check(f"junk input `{name}` still yields a whole palette", bool(ok), detail=str(pal))

check("nothing at all falls back to the built-in", junk["nothing"]["id"] == "gold")
check("an unknown preset id falls back too", junk["unknownPreset"]["id"] == "gold")
# ⚠ HALF A COLOUR IS NOT A COLOUR. `#ab` is what the hex box holds for a keypress
# or two while somebody types; accepting it would repaint the app black mid-word.
check("a half-typed colour is not a choice", junk["half"]["id"] == "gold")
check("editing a preset's colour stops it being that preset",
      junk["edited"]["id"] == "custom" and junk["edited"]["accent"] == "#ff0000")
check("…while a preset round-tripped through the store keeps its name",
      junk["roundTrip"]["id"] == "emerald")
check("`#fff` is expanded, not rejected", junk["short"]["accent"] == "#ffffff")


# ===========================================================================
print("\n6 · the panel renders, and says what it is showing")

markup = data["render"]["emerald"]
check("the picker renders at all", bool(markup) and "Colours" not in markup[:0])
check("every preset is on screen as a button",
      markup.count("admin-theme-card") >= 8, detail=f"{markup.count('admin-theme-card')} found")
check("the saved preset is the one marked on", 'aria-pressed="true"' in markup)
check("both colour wells are there", markup.count('type="color"') == 2)
check("the measured contrast is printed, not just judged",
      "admin-theme-check" in markup and ":1" in markup)
# ⚠ THE BUTTON ROW IS THE ONE NOTHING CAN CORRECT — the fill IS the chosen
# colour — so it is the row that must never be quietly dropped from this panel.
check("…including the button label, which no correction can fix",
      "Button label" in markup)
check("Save is offered", "Save colours" in markup)
# Nothing has changed yet, so Save must be disabled — a live preview that starts
# out "dirty" would let somebody save a palette they never chose.
check("…and disabled until something is chosen",
      re.search(r"Save colours", markup) is not None and "disabled" in markup)

# ⚠ THE DERIVED TEXT CANNOT BE BROKEN BY ANY GROUND AT ALL, and that is an
# assertion, not an aspiration: all 256 greys, both themes, worst case reported.
# `readable()` can always reach black or white, so this is a floor the
# derivation owes the app — and a future "improvement" that clamps the walk
# would land here rather than on a customer's screen.
floor = data["textFloor"]
check(f"no ground can make the derived text unreadable (worst {floor['ratio']:.2f}:1 "
      f"at {floor['at']})", floor["ratio"] >= 4.5)

# ⚠ THE WARNING IS FOR THE ONE THING NOTHING CAN CORRECT: an accent the same
# colour as the ground. The fill is the chosen colour and the ground is the
# chosen colour, so the buttons vanish into the panel — RULEBOOK E66 arriving by
# a new door, reachable in two clicks of this very screen.
bad = data["render"]["unreadable"]
check("an accent the same colour as the ground is flagged",
      "admin-theme-checks warn" in bad)
check("…and the row that catches it is named", "Button on panel" in bad)
check("…in words, and says what the row needs", "needs 3:1" in bad)
check("…and still lets it be saved (it is the deployment's call)",
      "Save colours" in bad)


# ===========================================================================
print("\n7 · the panel's own wiring — the parts a static render cannot show")

src = read("client", "src", "admin", "AdminBrand.jsx")
# ⚠ THE PUT-BACK IS THE ONE THAT MATTERS. Without the cleanup, closing this tab
# mid-preview leaves the whole app painted in a colour that was never saved and
# that nobody else can see — an administrator then hunts a setting that does not
# exist.
check("choosing repaints the app live", "applyPalette(pal)" in src)
check("…and leaving puts back what the server holds",
      "return () => applyPalette(getBrand().palette)" in src)
check("the save sends all three fields",
      all(k in src for k in ("theme_id: pal.id", "accent: pal.accent", "ground: pal.ground")))
# ⚠ ORDER ON THE TAB, ASKED FOR OUTRIGHT: *"colours wala panel niche rakho,
# Logo — one per theme panel ke niche move karo"*. Nothing about the component
# breaks if it moves, which is exactly why it would move again unnoticed.
check("the Colours card sits BELOW the logo card",
      src.index("<ThemePicker") > src.index("Logo — one per theme"))

brand_js = read("client", "src", "branding.js")
check("the palette is painted beside the title and favicon, before React mounts",
      "applyPalette(brand.palette)" in brand_js)
check("…and is remembered in the wire's own field names",
      "theme_id: next.palette.id" in brand_js)
check("…and a repaint is not mistaken for 'nothing changed'",
      "next.palette.accent === _current.palette.accent" in brand_js)

server_src = read("server", "branding.py")
check("the server stores the resolved hexes, not just the preset name",
      '"accent"' in server_src and '"ground"' in server_src
      and "EDITABLE = frozenset({\"name\", \"theme_id\", \"accent\", \"ground\"})" in server_src)
check("…and the public payload carries them (the sign-in card needs them)",
      '"theme_id": clean_theme_id(row.get("theme_id"))' in server_src)
check("…and the shipped default is still the built-in gold",
      'DEFAULT_ACCENT = "#e5c158"' in server_src and 'DEFAULT_GROUND = "#13161f"' in server_src)


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures
              else f"All palette checks passed ({len(preset_ids)} presets, both themes)."))
sys.exit(1 if failures else 0)
