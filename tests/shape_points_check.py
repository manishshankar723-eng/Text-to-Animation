"""EVERY SHAPE, DRAWN THE SAME WAY BY BOTH RENDERERS — and drawable at all.

A shape is drawn three times: as a CSS `clip-path` in the pane, as a triangle fan
in the Program monitor, and as a Pillow polygon in the export. The browser's two
now read ONE table (`client/src/animatic/shape_points.js`); the exporter cannot,
because it runs with no JS at all, so `_SHAPE_POINTS` in `animatic.py` is a second
copy of the same numbers.

That copy used to be kept in step by hand, with a comment in each file
apologising for the other. With four shapes that was survivable. With forty-one it
is not: a mistyped digit is a shape that is one thing in the editor and another in
the film, and nothing fails — the export just quietly differs from what was
approved. So this compares the two sides point by point, and pins the two
properties a "just add a shape" change gets wrong:

  · EVERY SHAPE IS STAR-SHAPED ABOUT (0.5, 0.5). The monitor triangulates with a
    fan anchored at the centre (`shapeFan`), so an outline the centre cannot see
    all of draws correctly in CSS and in Pillow and WRONGLY on the canvas — a mess
    that still looks vaguely like the shape, which is the worst kind of wrong. It
    is also why the library has no ring and no crescent: a hole cannot be one fan.

  · THE FIRST FOUR IDS ARE FROZEN. Saved projects store `kind: "pentagon"`, and a
    pentagon that changed shape when the builders arrived would silently redraw
    somebody's finished animatic. Those four points are compared against literals
    written out here, not against the module.

And it checks the picker cannot offer something the renderers do not have: the
folders in `SHAPE_CATEGORIES` are where a user picks a shape, so a kind listed
there with no points is a tile that adds a plain box.

    python tests/shape_points_check.py

Needs node for the cross-language half; the fan-safety and Pillow halves run
either way, exactly as `asset_fields_check.py` degrades.
"""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import animatic

ROOT = Path(__file__).resolve().parent.parent

failures: list[str] = []
skipped: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def skip(label, why):
    print(f"  skip {label}  ({why})")
    skipped.append(label)


# How far apart the two languages may be on one coordinate.
#
# ⚠ NOT ZERO, AND NOT BECAUSE THE TABLES DISAGREE. Both sides round every point to
# six decimals with the same expression (`floor(v * 1e6 + 0.5)`), but the value
# going INTO that rounding comes out of each platform's own `cos`/`sin`/`pow`,
# which are allowed to differ in the last bit. A coordinate sitting exactly on a
# rounding boundary can therefore land one step of the 1e-6 grid apart — a
# millionth of a shape's width, or about a thousandth of a pixel at 1080p.
# Anything bigger than one grid step is a real difference and fails.
TOLERANCE = 1.5e-6

# ⚠ WRITTEN OUT, NOT IMPORTED. The point of this block is to fail if the module's
# idea of these four ever changes, so reading them from the module would defeat it.
FROZEN = {
    "rect": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
    "pentagon": [(0.5, 0.0), (1.0, 0.38), (0.82, 1.0), (0.18, 1.0), (0.0, 0.38)],
    "star": [
        (0.5, 0.0), (0.61, 0.35), (0.98, 0.35), (0.68, 0.57),
        (0.79, 0.91), (0.5, 0.7), (0.21, 0.91), (0.32, 0.57),
        (0.02, 0.35), (0.39, 0.35),
    ],
}

# The one shape allowed a fan-safety margin of zero rather than a positive one:
# a gear's tooth flanks point AT the centre, so the fan triangle on those edges
# has no area — it draws nothing, which is exactly right.
ZERO_MARGIN_OK = {"cog"}

HARNESS = """
import { SHAPE_CATEGORIES, SHAPE_KINDS, SHAPE_POINTS, shapeOutline } from %(mod)r;
process.stdout.write(JSON.stringify({
  points: SHAPE_POINTS,
  kinds: SHAPE_KINDS.map((k) => k.id),
  labels: SHAPE_KINDS.map((k) => k.label),
  groups: SHAPE_CATEGORIES.map((g) => ({ id: g.id, kinds: g.kinds.map((k) => k.id) })),
  ellipse: shapeOutline("ellipse").length,
  unknown: shapeOutline("no-such-shape"),
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="shapepts_")
    try:
        mod = (ROOT / "client/src/animatic/shape_points.js").as_uri()
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {"mod": mod})
        proc = subprocess.run(
            ["node", harness],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:800])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def kernel_margin(points) -> float:
    """How far inside the shape (0.5, 0.5) is, worst edge.

    The kernel of a simple polygon — the set of points that can see all of it — is
    the intersection of the inner half-plane of every edge. So the smallest signed
    cross product over the edges IS the answer: positive means a centre fan
    triangulates this outline exactly, negative means it does not.
    """
    n = len(points)
    area = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    sign = 1.0 if area > 0 else -1.0
    worst = math.inf
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        worst = min(worst, ((x1 - x0) * (0.5 - y0) - (y1 - y0) * (0.5 - x0)) * sign)
    return worst


print("\nSHAPES — the exporter's table against the browser's")

py_points = animatic._SHAPE_POINTS
py_kinds = list(animatic.SHAPE_KINDS)

got = run_node()
if got is None:
    for label in (
        "the two tables hold the same shapes",
        "every point agrees across the two languages",
        "the picker's order is the exporter's order",
        "the ellipse is sampled for the fan and absent from the table",
        "an unknown kind folds to a plain box on both sides",
    ):
        skip(label, "node not available")
else:
    js_points = {k: [tuple(p) for p in v] for k, v in got["points"].items()}
    check(
        "the two tables hold the same shapes",
        set(js_points) == set(py_points),
        f"only in JS: {sorted(set(js_points) - set(py_points))}; "
        f"only in Python: {sorted(set(py_points) - set(js_points))}",
    )

    worst = ("", 0.0)
    bad: list[str] = []
    for kind in sorted(set(js_points) & set(py_points)):
        a, b = js_points[kind], py_points[kind]
        if len(a) != len(b):
            bad.append(f"{kind}: {len(a)} points in JS, {len(b)} in Python")
            continue
        for i, ((ax, ay), (bx, by)) in enumerate(zip(a, b)):
            gap = max(abs(ax - bx), abs(ay - by))
            if gap > worst[1]:
                worst = (f"{kind}[{i}]", gap)
            if gap > TOLERANCE:
                bad.append(f"{kind}[{i}]: JS {(ax, ay)} vs Python {(bx, by)}")
    check(
        "every point agrees across the two languages",
        not bad,
        "; ".join(bad[:4]) + (f" (+{len(bad) - 4} more)" if len(bad) > 4 else ""),
    )
    print(f"       worst gap: {worst[1]:.2e} at {worst[0] or 'nothing'}")

    # ⚠ ORDER, not just membership. The Properties picker walks the categories and
    # the Media tab walks the same list; if the exporter's tuple drifts out of that
    # order nothing breaks today, but the two lists stop being one thing and the
    # next person has to guess which is canonical.
    check(
        "the picker's order is the exporter's order",
        got["kinds"] == py_kinds,
        f"JS {got['kinds'][:6]}… vs Python {py_kinds[:6]}…",
    )

    check(
        "the ellipse is sampled for the fan and absent from the table",
        "ellipse" not in js_points and "ellipse" not in py_points and got["ellipse"] >= 32,
        f"segments={got['ellipse']}",
    )

    check(
        "an unknown kind folds to a plain box on both sides",
        [tuple(p) for p in got["unknown"]] == js_points["rect"]
        and py_points.get("no-such-shape", py_points["rect"]) == py_points["rect"],
    )

    # A tile in a folder that has no points is a tile that adds a plain box.
    listed = [k for group in got["groups"] for k in group["kinds"]]
    check(
        "every kind in the picker's folders can actually be drawn",
        all(k == "ellipse" or k in js_points for k in listed),
        f"no points for: {[k for k in listed if k != 'ellipse' and k not in js_points]}",
    )
    check(
        "no kind is listed in two folders, and none is left out of all of them",
        len(listed) == len(set(listed)) and set(listed) == set(js_points) | {"ellipse"},
        f"listed {len(listed)}, unique {len(set(listed))}, table {len(js_points)}",
    )
    check(
        "every shape has its own name",
        len(set(got["labels"])) == len(got["labels"]),
        f"{len(got['labels']) - len(set(got['labels']))} duplicate label(s)",
    )

print("\nWHAT THE MONITOR CAN DRAW")

# ⚠ THE CHECK THAT MAKES A NEW SHAPE SAFE TO ADD BY EYE. Everything else here
# compares two tables; this one says whether the shape can be drawn at all.
unsafe = []
for kind, points in py_points.items():
    margin = kernel_margin(points)
    floor = -1e-6 if kind in ZERO_MARGIN_OK else 1e-6
    if margin < floor:
        unsafe.append(f"{kind} ({margin:+.2e})")
check(
    "every shape is star-shaped about its centre, so a centre fan is exact",
    not unsafe,
    "; ".join(unsafe),
)

thin = [k for k, v in py_points.items() if len(v) < 3]
check("no shape is a point or a line", not thin, f"{thin}")

out_of_box = [
    k for k, v in py_points.items()
    if any(x < -1e-9 or x > 1 + 1e-9 or y < -1e-9 or y > 1 + 1e-9 for x, y in v)
]
check(
    "every shape stays inside its own box",
    not out_of_box,
    f"{out_of_box}",
)

doubled = [
    k for k, v in py_points.items()
    if any(
        abs(v[i][0] - v[(i + 1) % len(v)][0]) < 1e-9 and abs(v[i][1] - v[(i + 1) % len(v)][1]) < 1e-9
        for i in range(len(v))
    )
]
check(
    "no shape carries a repeated point (a zero-area triangle in the fan)",
    not doubled,
    f"{doubled}",
)

print("\nWHAT THE EXPORTER ACTUALLY PAINTS")

try:
    from PIL import Image
except Exception as exc:  # pragma: no cover - Pillow is a hard dep of the exporter
    skip("every kind paints ink onto the frame", f"no Pillow ({exc})")
    skip("an unknown kind paints a box rather than nothing", "no Pillow")
else:
    def paint(kind):
        canvas = Image.new("RGB", (120, 120), (0, 0, 0))
        animatic.draw_shapes(canvas, [{
            "kind": kind, "x": 0.5, "y": 0.5, "w": 0.9, "h": 0.9,
            "color": "#ffffff", "opacity": 1.0,
        }])
        return canvas

    blank = []
    overflowing = []
    for kind in py_kinds:
        img = paint(kind)
        pixels = img.load()
        lit = sum(1 for y in range(120) for x in range(120) if pixels[x, y][0] > 8)
        if lit < 200:
            blank.append(f"{kind} ({lit}px)")
        # 90% of a 120px frame is a 108px box; anything much past that means the
        # points ran outside the unit square after all.
        if lit > 118 * 118:
            overflowing.append(f"{kind} ({lit}px)")
    check("every kind paints ink onto the frame", not blank, "; ".join(blank))
    check("no kind floods the whole frame", not overflowing, "; ".join(overflowing))

    unknown = paint("no-such-shape")
    box = paint("rect")
    check(
        "an unknown kind paints a box rather than nothing",
        unknown.tobytes() == box.tobytes(),
    )

print("\nTHE FOUR THAT CANNOT CHANGE")

for kind, points in FROZEN.items():
    check(
        f"'{kind}' is exactly what saved projects were drawn with",
        [tuple(p) for p in py_points[kind]] == points,
        f"{py_points[kind]}",
    )

print()
if skipped:
    print(f"{len(skipped)} check(s) skipped — install node to run them.")
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  · {f}")
    sys.exit(1)
print(f"all shape checks passed — {len(py_kinds)} kinds, {len(py_points)} point sets.")
