"""The LOOK is written twice. This proves the two agree — within a tolerance.

`client/src/animatic/gl/shaders/` decides what the Program monitor shows;
`animatic_effects.py` decides what the exported MP4 shows. Same formulae, two
languages, one of them running on a GPU.

⚠ THE COMPARISON IS ON PIXELS, AND IT IS NOT EXACT. That is the opposite of
`tests/render_parity.py`, which compares NUMBERS to six decimal places, and the
difference is deliberate. A resolved scene value can be identical in both
languages. A rendered pixel cannot: WebGL and Pillow use different float
precision, different rounding on the way to 8 bits, and a LUT quantised to a
texture on one side and interpolated from floats on the other. Demanding an
exact match would fail forever and be switched off within a month, which is
worse than no test at all.

So the bar is the honest one:

    mean |Δ|  <  3/255   across every channel of every pixel
    max  |Δ|  < 12/255   on any single channel

Wide enough to survive two rasterisers, tight enough that a wrong constant, a
swapped channel, a mis-ordered chain or a mirrored mask all fail it — each of
which moves whole regions of the frame by far more than 12.

`tests/effects_check.py` is the other half: it pins the PYTHON side to exact
golden values, so the pair cannot drift together and agree with each other while
both being wrong.

    python tests/effects_parity_check.py

⚠ NEEDS `gl` (headless-gl), which is a NATIVE module and is not in
package.json — it exists only for this test, and making every `npm install` on
the project build a C++ GL binding would be a poor trade.

    cd client && npm install --no-save gl

On Windows that needs the Visual Studio "Desktop development with C++"
workload; on Linux, libx11-dev/libxi-dev/mesa. If it isn't there this test
EXITS 2 and says so rather than passing: a skip here is a real gap, not a pass.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

from animatic_effects import apply_effects, apply_mask, blend_onto

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

# Deliberately small and ODD-ish. Small because every case renders the whole
# frame and there are a dozen of them; the maths is per-pixel, so more pixels
# prove nothing more.
WIDTH, HEIGHT = 160, 90

# The tolerance, in 8-bit code values. See the module header for why it is not
# zero and why it is not looser than this.
MEAN_TOLERANCE = 3.0
MAX_TOLERANCE = 12

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# The test picture
# ---------------------------------------------------------------------------
def test_image() -> Image.Image:
    """A picture chosen to make every effect say something different.

    Two smooth ramps so brightness, contrast and a LUT have a full range to act
    over; a green block so the chroma key has something to bite on; a grey block
    so saturation has something it must leave alone; and hard edges between them
    so a mis-ordered chain or a mirrored mask shows up as a whole region moving
    rather than as a rounding difference.

    ⚠ BUILT FROM ARITHMETIC, not loaded from a file, so both sides can construct
    the identical bytes without a PNG decoder in the loop.
    """
    xs = np.arange(WIDTH, dtype=np.float32) / (WIDTH - 1)
    ys = np.arange(HEIGHT, dtype=np.float32) / (HEIGHT - 1)
    rgb = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    rgb[..., 0] = xs[None, :]
    rgb[..., 1] = ys[:, None]
    rgb[..., 2] = 1.0 - xs[None, :] * ys[:, None]
    rgb[10:35, 10:60] = (0.0, 0.85, 0.05)  # a green screen to key
    rgb[50:80, 90:150] = (0.5, 0.5, 0.5)  # neutral grey
    out = np.concatenate([rgb, np.ones((HEIGHT, WIDTH, 1), dtype=np.float32)], axis=-1)
    return Image.fromarray(np.round(out * 255).astype(np.uint8), "RGBA")


SOURCE = test_image()
SOURCE_BYTES = SOURCE.tobytes()

# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------
# Each is { name, effects, mask, blend, background }. The layer is drawn 1:1 —
# source size == frame size, no fit, no zoom — ON PURPOSE: this test is about
# the EFFECT MATHS, and letting a LANCZOS resize on one side meet a bilinear
# sample on the other would mix a resampling difference into every number and
# force the tolerance so wide it proved nothing. Placement has its own twin
# check (`placePicture` vs `place_picture`) further down.
def fx(kind, **params):
    return {"id": kind, "kind": kind, "params": params}


MASK_OFF = {"kind": "none", "x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5,
            "feather": 0.1, "invert": False}

CASES = [
    {"name": "no look at all", "effects": []},
    {"name": "brightness 1.4", "effects": [fx("brightness", amount=1.4)]},
    {"name": "brightness 0.5", "effects": [fx("brightness", amount=0.5)]},
    {"name": "contrast 1.8", "effects": [fx("contrast", amount=1.8)]},
    {"name": "contrast 0.4", "effects": [fx("contrast", amount=0.4)]},
    {"name": "saturation 0 (greyscale)", "effects": [fx("saturation", amount=0.0)]},
    {"name": "saturation 1.8", "effects": [fx("saturation", amount=1.8)]},
    {
        "name": "a LUT at full strength",
        "effects": [fx("lut", name="teal_orange", amount=1.0)],
    },
    {"name": "the same LUT at half", "effects": [fx("lut", name="teal_orange", amount=0.5)]},
    {"name": "a monochrome LUT", "effects": [fx("lut", name="noir", amount=1.0)]},
    {
        "name": "chroma key on the green block",
        "effects": [fx("chroma", color="#00d90d", similarity=0.25, smoothness=0.1, spill=0.0)],
    },
    {
        "name": "chroma key with spill removal",
        "effects": [fx("chroma", color="#00d90d", similarity=0.25, smoothness=0.1, spill=0.8)],
    },
    {
        # ⚠ ORDER, not just presence. Reversed, this is a visibly different
        # picture — which is the point of running the chain rather than one
        # effect at a time.
        "name": "a chain: saturation then contrast then a LUT",
        "effects": [
            fx("saturation", amount=0.4),
            fx("contrast", amount=1.3),
            fx("lut", name="warm", amount=0.7),
        ],
    },
    {
        "name": "a hard rectangular mask",
        "effects": [],
        "mask": {**MASK_OFF, "kind": "rect", "x": 0.4, "y": 0.55, "w": 0.5, "h": 0.6,
                 "feather": 0.0},
    },
    {
        "name": "a feathered ellipse mask",
        "effects": [],
        "mask": {**MASK_OFF, "kind": "ellipse", "x": 0.5, "y": 0.5, "w": 0.7, "h": 0.8,
                 "feather": 0.35},
    },
    {
        "name": "an inverted mask",
        "effects": [],
        "mask": {**MASK_OFF, "kind": "ellipse", "x": 0.3, "y": 0.4, "w": 0.4, "h": 0.5,
                 "feather": 0.2, "invert": True},
    },
    {
        # A mask AND a key together, which is the case that proves the mask
        # multiplies the alpha it finds rather than replacing it.
        "name": "a mask over a chroma key",
        "effects": [fx("chroma", color="#00d90d", similarity=0.25, smoothness=0.1)],
        "mask": {**MASK_OFF, "kind": "rect", "x": 0.5, "y": 0.5, "w": 0.8, "h": 0.8,
                 "feather": 0.15},
    },
]
# Every blend mode, each over a mid-grey backdrop so none of them is a no-op.
for mode in ("multiply", "screen", "overlay", "add", "darken", "lighten"):
    CASES.append({
        "name": f"blend: {mode}",
        "effects": [],
        "blend": mode,
        "background": "#808080",
    })
# …and one with the layer part-transparent, which is THE rule every mode obeys:
# the alpha is the mix.
CASES.append({
    "name": "blend: multiply through a feathered mask",
    "effects": [],
    "blend": "multiply",
    "background": "#3a6ea5",
    "mask": {**MASK_OFF, "kind": "ellipse", "x": 0.5, "y": 0.5, "w": 0.8, "h": 0.9,
             "feather": 0.4},
})

for case in CASES:
    case.setdefault("mask", MASK_OFF)
    case.setdefault("blend", "normal")
    case.setdefault("background", "#101820")


# ---------------------------------------------------------------------------
# The Python side
# ---------------------------------------------------------------------------
def render_python(case) -> np.ndarray:
    """Exactly what `_picture_layer` + `_flatten` do, minus the placement."""
    layer = apply_effects(SOURCE, case["effects"])
    layer = apply_mask(layer, case["mask"])
    from animatic import _parse_colour

    base = Image.new("RGB", (WIDTH, HEIGHT), _parse_colour(case["background"]))
    return np.asarray(blend_onto(base, layer, case["blend"]), dtype=np.int16)


# ---------------------------------------------------------------------------
# The JS side
# ---------------------------------------------------------------------------
HARNESS = r"""
import fs from "node:fs";
import path from "node:path";
import createContext from "gl";

import { Compositor, quad } from %(compositor)s;
import { buildLutPixels, parseCube } from %(cube)s;

const { width, height, source, cases, lutDir } = JSON.parse(
  fs.readFileSync(process.argv[2], "utf8")
);
const pixels = Buffer.from(source, "base64");

// headless-gl hands back a context, not a canvas. The compositor only ever asks
// a canvas for `getContext`, `addEventListener` and its width/height, so a
// four-line stand-in is the whole shim — and it means the test drives the SAME
// class the browser does rather than a copy of it.
const gl = createContext(width, height, { preserveDrawingBuffer: true });
const canvas = {
  width,
  height,
  getContext: () => gl,
  addEventListener: () => {},
};

const compositor = new Compositor(canvas);
compositor.resize(width, height);
const layer = compositor.texturePixels("source", {
  width,
  height,
  data: new Uint8Array(pixels),
});

// Every LUT the cases ask for, read off disk and uploaded — the same .cube
// bytes `animatic_effects.py` reads, which is the entire reason a LUT is a file.
const luts = new Map();
for (const name of new Set(
  cases.flatMap((c) => (c.effects || []).filter((e) => e.kind === "lut").map((e) => e.params.name))
)) {
  if (!name) continue;
  const text = fs.readFileSync(path.join(lutDir, `${name}.cube`), "utf8");
  luts.set(name, compositor.lutTexture(name, buildLutPixels(parseCube(text))));
}

const out = [];
for (const testCase of cases) {
  compositor.begin(testCase.background);
  compositor.layer({
    // 1:1 over the whole frame — see the note on CASES for why placement is
    // deliberately not part of this comparison.
    vertices: quad({ x: 0, y: 0, w: 1, h: 1 }),
    count: 6,
    mode: gl.TRIANGLES,
    source: layer,
    opacity: 1,
    // Matching `_picture_layer`'s deliberate convert("RGB") for a frame.
    useAlpha: false,
    look: { effects: testCase.effects, mask: testCase.mask, blend: testCase.blend },
    luts,
  });
  out.push(Buffer.from(compositor.readPixels()).toString("base64"));
}
process.stdout.write(JSON.stringify({ frames: out }));
"""


def _file_url(path: str) -> str:
    from pathlib import Path

    return Path(path).resolve().as_uri()


def run_node() -> list[np.ndarray]:
    if not shutil.which("node"):
        print("  node is not on PATH — cannot run the shaders.")
        sys.exit(2)

    tmp = tempfile.mkdtemp(prefix="fxparity_")
    try:
        harness = os.path.join(tmp, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS
                % {
                    "compositor": json.dumps(
                        _file_url(os.path.join(CLIENT, "src", "animatic", "gl", "compositor.js"))
                    ),
                    "cube": json.dumps(
                        _file_url(os.path.join(CLIENT, "src", "animatic", "gl", "cube.js"))
                    ),
                }
            )
        payload = os.path.join(tmp, "payload.json")
        with open(payload, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "width": WIDTH,
                    "height": HEIGHT,
                    "source": base64.b64encode(SOURCE_BYTES).decode("ascii"),
                    "cases": CASES,
                    "lutDir": os.path.join(ROOT, "luts"),
                },
                fh,
            )
        proc = subprocess.run(
            ["node", harness, payload],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
            # `gl` resolves out of the client's node_modules, and so does the
            # compositor's own import graph.
            cwd=CLIENT,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if "Cannot find package 'gl'" in stderr or "Cannot find module 'gl'" in stderr:
                print("  headless-gl is not installed, so the SHADERS WERE NOT RUN.")
                print("  This test is the only thing keeping the monitor's grade and the")
                print("  exported grade in step; a skip here is a real gap, not a pass.\n")
                print("      cd client && npm install --no-save gl\n")
                print("  It is a native module: on Windows it needs the Visual Studio")
                print("  'Desktop development with C++' workload, on Linux libx11-dev,")
                print("  libxi-dev and mesa. It is deliberately NOT in package.json —")
                print("  every install on this project would otherwise build a C++ GL")
                print("  binding for a test most sessions never run.")
                sys.exit(2)
            print(stderr[:3000])
            print("\n  The shaders could not be run (see above).")
            sys.exit(1)
        frames = json.loads(proc.stdout)["frames"]
        return [
            np.frombuffer(base64.b64decode(f), dtype=np.uint8)
            .reshape(HEIGHT, WIDTH, 4)[..., :3]
            .astype(np.int16)
            for f in frames
        ]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# The static half — runs with no GPU at all
# ---------------------------------------------------------------------------
# ⚠ THIS EXISTS BECAUSE THE GPU HALF CAN BE SKIPPED. headless-gl is a native
# module that will not build on every machine, and on the machines where it
# doesn't, the pixel comparison below never runs. The single most likely way for
# the two sides to drift — adding an effect or a blend mode to the scene model
# and forgetting its shader — needs no GPU to catch, so it is caught here and
# runs even when the rest is skipped.
STATIC_HARNESS = r"""
import { EFFECT_KINDS, BLEND_MODES, MASK_KINDS } from %(scene)s;
import { FRAGMENT, MAX_EFFECTS, blendIndex, fxIndex } from %(layer)s;
process.stdout.write(JSON.stringify({
  fragment: FRAGMENT,
  effects: EFFECT_KINDS,
  blends: BLEND_MODES,
  masks: MASK_KINDS,
  maxEffects: MAX_EFFECTS,
  fxIndices: Object.fromEntries(EFFECT_KINDS.map((k) => [k, fxIndex(k)])),
  blendIndices: Object.fromEntries(BLEND_MODES.map((m) => [m, blendIndex(m)])),
}));
"""


def run_static() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — cannot read the shaders at all.")
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="fxstatic_")
    try:
        path = os.path.join(tmp, "static.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                STATIC_HARNESS
                % {
                    "scene": json.dumps(
                        _file_url(os.path.join(CLIENT, "src", "animatic", "scene.js"))
                    ),
                    "layer": json.dumps(
                        _file_url(os.path.join(CLIENT, "src", "animatic", "gl",
                                               "shaders", "layer.js"))
                    ),
                }
            )
        proc = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            print("\n  The shader modules could not be loaded (see above).")
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


print("Effect parity — client/src/animatic/gl/shaders vs animatic_effects.py")
print(f"tolerance: mean |Δ| < {MEAN_TOLERANCE}/255, no channel off by more than "
      f"{MAX_TOLERANCE}/255\n")

print("The shader source, checked against the scene model (no GPU needed)\n")
static = run_static()

from animatic_render import BLEND_MODES as PY_BLENDS
from animatic_render import EFFECT_KINDS as PY_EFFECTS
from animatic_render import EFFECT_PARAMS, MASK_KINDS as PY_MASKS

check("both sides know the same effect kinds",
      list(static["effects"]) == list(PY_EFFECTS),
      f"(js={static['effects']} py={list(PY_EFFECTS)})")
check("both sides know the same blend modes",
      list(static["blends"]) == list(PY_BLENDS),
      f"(js={static['blends']} py={list(PY_BLENDS)})")
check("both sides know the same mask kinds",
      list(static["masks"]) == list(PY_MASKS),
      f"(js={static['masks']} py={list(PY_MASKS)})")

fragment = static["fragment"]
# Every effect must have a branch in the chain. A kind added to the scene model
# with no shader would silently do NOTHING in the monitor while the export
# applied it — the preview lying, in the direction that is hardest to notice.
for kind in static["effects"]:
    token = f"FX_{kind.upper()}"
    check(f"the shader has a branch for '{kind}'",
          f"#define {token} " in fragment and f"kind == {token}" in fragment)
# …and the numbering has to be the one the uniform writer uses. The `#define`s
# are column-aligned in the source, so the comparison is on the numbers rather
# than on the exact spacing.
defines = dict(re.findall(r"#define FX_(\w+)\s+(-?\d+)", fragment))
check("the effect numbering is generated, not written out twice",
      {kind.upper(): str(index) for kind, index in static["fxIndices"].items()} == defines,
      f"(shader={defines} js={static['fxIndices']})")
# Every blend mode past "normal" needs its own branch; `normal` is the fallback.
check("the shader handles every blend mode",
      all(f"mode == {index}" in fragment
          for mode, index in static["blendIndices"].items() if index > 0),
      f"({static['blendIndices']})")
check("the uniform arrays are sized for the chain the pane allows",
      f"uFxKind[{static['maxEffects']}]" in fragment and static["maxEffects"] >= 1)
# The chroma key is the one effect that returns ALPHA as well as colour, so it
# is wired differently from the rest — proving its branch exists is not enough.
# Drop the `a = keyed.a` and the key would tint the picture without ever making
# a pixel transparent, which looks like a keyer that is merely badly tuned.
check("the chroma branch writes back the alpha, not just the colour",
      "vec4 keyed = fxChroma(" in fragment and "a = keyed.a;" in fragment)
check("every effect's parameters exist on the Python side too",
      all(kind in EFFECT_PARAMS for kind in static["effects"]))

# Stop HERE if the source-level checks failed. Running the GPU half after them
# would either bury the failure in a wall of pixel numbers or — on a machine
# with no headless-gl — exit 2 and never print it at all.
if failures:
    print(f"\n{len(failures)} check(s) FAILED before a single pixel was drawn:")
    for f in failures:
        print(f"  - {f}")
    print("\nThe scene model and the shaders have drifted apart. Fix before shipping.")
    sys.exit(1)

print("\nThe pixels, through a headless GL context\n")
gpu = run_node()
check("the shaders rendered every case", len(gpu) == len(CASES),
      f"(got {len(gpu)} of {len(CASES)})")

worst_mean = 0.0
worst_max = 0
for case, got in zip(CASES, gpu):
    want = render_python(case)
    delta = np.abs(want - got)
    mean = float(delta.mean())
    peak = int(delta.max())
    worst_mean = max(worst_mean, mean)
    worst_max = max(worst_max, peak)
    check(
        case["name"],
        mean < MEAN_TOLERANCE and peak <= MAX_TOLERANCE,
        f"(mean {mean:.2f}, worst pixel {peak})",
    )

# A test that passes because both sides rendered NOTHING would be worse than no
# test. The un-graded case must actually be the picture, and the graded ones
# must actually differ from it.
plain = render_python(CASES[0])
check("the fixture is a real picture, not a flat field", int(plain.std()) > 20,
      f"(std {plain.std():.1f})")
check("the effects actually changed it",
      all(np.abs(render_python(c) - plain).mean() > 3 for c in CASES[1:]),
      "(some case rendered identically to the ungraded picture)")

print(f"\nworst case: mean {worst_mean:.2f}/255, single pixel {worst_max}/255")
if failures:
    print(f"\n{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    print("\nThe monitor and the export now grade differently. Fix before shipping.")
    sys.exit(1)
print("The monitor and the export grade the same picture.")
