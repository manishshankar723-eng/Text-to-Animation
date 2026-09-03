"""button_row_check.py — TWO BUTTONS SIDE BY SIDE ARE ONE SIZE, ON ONE LINE.

Run:  python tests/button_row_check.py   (no backend, no browser — source only)

Why this file exists, in one sentence: **the same complaint kept coming back.**

    "dekho admin panel mai save name and reset name buttun uper niche v hai aur
     chhota bara … please fix mai bahut baar bola hun aisa nhi hona chaiye magar
     aisa mil hi jata hai … not align and not match each other size"

⚠ **IT KEPT COMING BACK BECAUSE THE CAUSE IS A GLOBAL RULE, NOT A SCREEN.**
`.btn.primary` in `base.css` carries `margin-top: 1.1rem` — a FORM's spacing
living on a COLOUR variant. Every primary in the app is therefore born 1.1rem
lower than whatever button stands beside it, and each person who noticed patched
their own container: **39 rules across 21 stylesheets exist for no other reason
than to undo it.** That is one bug found thirty-nine times.

The margin is still there on purpose — 68 buttons in 34 files wear
`btn primary`, and the ones outside those 39 rules are form submits relying on
it. Deleting it is one line and 68 browser checks, and nothing in this repo can
see a gap close up. So a ROW opts out instead, with `btn-row`, and this file is
what stops the fortieth private patch from being needed.

WHAT IS CHECKED, and both halves matter because fixing one does not fix the
other:

  1. **The top edge.** A run of buttons containing a `primary` must sit in a
     container that resets the margin — by wearing `btn-row`, or by a rule of
     its own (`.pf-foot .btn { margin-top: 0 }` and the 38 others stay valid;
     this is not a demand to rewrite them).
  2. **The size.** `btn primary` beside `btn small` is a big button next to a
     little one wherever its top edge is, and NO CSS can fix that without
     overriding a size somebody chose deliberately. So it is read out of the
     JSX: buttons standing next to each other must agree on size, unless their
     container states one height or padding for all of them.

RULEBOOK **E102**.

⚠ **THE DETECTOR IS PROVED TO FAIL FIRST.** Section 4 feeds it the exact markup
that was reported — Save name next to a small Reset name — and requires it to be
rejected, then the fixed shape and requires it to pass. A scan that has never
caught anything is decoration.

⚠ **WHAT IT DELIBERATELY DOES NOT FLAG.** Square icon buttons (`back-btn`,
`icon-btn`) are their own shape and never a slab; and buttons in different
branches of a conditional are never on screen together, so a run BREAKS at a
branch boundary.
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "client", "src")
STYLES = os.path.join(SRC, "styles")

failures = []


def check(label, got, want=True, extra=""):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {extra}"))
    if not good:
        failures.append(label)


# ===========================================================================
# The reader.
#
# ⚠ THIS IS NOT A JSX PARSER AND MUST NOT PRETEND TO BE ONE. It walks opening
# and closing tags to learn who is whose child, which is all the question needs.
# The one thing that genuinely breaks a tag regex here is the arrow function —
# `onClick={() => …}` puts a `>` inside a tag — so `=>` is masked out first.
# ===========================================================================

TAG = re.compile(r"<(/?)([A-Za-z][\w.]*)((?:[^<>]|=>)*?)(/?)>", re.S)
JSX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.S)
MASK = ""      # stands in for the `>` of an arrow function, same width
VOID = {"br", "hr", "img", "input", "path", "circle", "rect", "line", "polygon",
        "polyline", "ellipse", "use", "source", "track", "col", "meta", "link"}


def class_list(attrs):
    """The class names on a tag, for the three shapes this codebase writes."""
    m = re.search(r'className\s*=\s*"([^"]*)"', attrs)
    if m:
        return m.group(1).split()
    m = re.search(r"className\s*=\s*\{`([^`]*)`\}", attrs)
    if m:                                    # template literal: drop the ${…}
        return re.sub(r"\$\{[^}]*\}", " ", m.group(1)).split()
    m = re.search(r'className\s*=\s*\{[^}]*"([^"]*)"[^}]*\}', attrs)
    if m:
        return m.group(1).split()
    return []


def button_runs(src, path="<memory>"):
    """Every run of buttons that stand next to each other, with their parent.

    A run is broken by any other element between two buttons, and by a
    conditional branch closing — `)}` or `</>` — because the button before it
    and the button after it are never on screen at the same time.

    ⚠ THE GAP IS MEASURED FROM THE END OF THE PREVIOUS BUTTON'S OPENING TAG,
    NOT FROM ITS START, and that is not a detail. Nearly every button here
    carries `onClick={() => something()}`, and that `)}` looks exactly like a
    branch closing to a text scan. Measuring from the start swallowed those
    attributes and cut the app's 70 real rows down to 27 — a green run that had
    quietly stopped looking at most of the screens. Section 2 asserts the count.
    """
    masked = src.replace("=>", "=" + MASK)
    stack, runs = [], []

    def close_frame(frame):
        _tag, pcls, kids, line = frame
        run = []
        for kind, cls, kline, start, end in kids + [("other", [], 0, 0, 0)]:
            if kind == "btn":
                if run:
                    between = JSX_COMMENT.sub("", masked[run[-1][3]:start])
                    if ")}" in between or "</>" in between:
                        if len(run) >= 2:
                            runs.append((path, line, pcls, run[:]))
                        run = []
                run.append((cls, kline, start, end))
            else:
                if len(run) >= 2:
                    runs.append((path, line, pcls, run[:]))
                run = []

    for m in TAG.finditer(masked):
        closing, tag, attrs, selfclose = m.groups()
        line = masked.count("\n", 0, m.start()) + 1
        if closing:
            while stack:
                top = stack.pop()
                if top[0] == tag:
                    close_frame(top)
                    break
            continue
        cls = class_list(attrs)
        is_btn = tag.lower() == "button" and "btn" in cls
        if stack:
            stack[-1][2].append(
                ("btn" if is_btn else "other", cls, line, m.start(), m.end()))
        if selfclose or tag.lower() in VOID:
            continue
        stack.append([tag, cls, [], line])
    return runs


def jsx_files():
    for base, _dirs, names in os.walk(SRC):
        for name in sorted(names):
            if name.endswith(".jsx"):
                yield os.path.join(base, name)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


CSS = "\n".join(read(os.path.join(STYLES, n))
                for n in sorted(os.listdir(STYLES)) if n.endswith(".css"))


def container_rule(parent_classes, prop):
    """Does some rule already state `prop` for every .btn inside this box?"""
    for c in parent_classes:
        if re.search(r"\." + re.escape(c) + r"\b[^{,]*\.btn\b[^{]*\{[^}]*" + prop, CSS):
            return True
    return False


def resets_margin(parent_classes):
    return ("btn-row" in parent_classes
            or container_rule(parent_classes, r"margin-top:\s*0"))


def states_one_size(parent_classes):
    return (container_rule(parent_classes, r"height:")
            or container_rule(parent_classes, r"padding:"))


def size_of(cls):
    """None for the square icon buttons — they are a shape, not a slab."""
    if "back-btn" in cls or "icon-btn" in cls:
        return None
    return "small" if "small" in cls else "full"


def mixed_sizes(runs):
    out = []
    for path, line, pcls, btns in runs:
        sizes = {size_of(c) for c, _l, _s, _e in btns}
        sizes.discard(None)
        if len(sizes) > 1 and not states_one_size(pcls):
            out.append((path, line, pcls, btns))
    return out


def unreset_primaries(runs):
    out = []
    for path, line, pcls, btns in runs:
        if any("primary" in c for c, _l, _s, _e in btns) and not resets_margin(pcls):
            out.append((path, line, pcls, btns))
    return out


def describe(items):
    bits = []
    for path, line, pcls, btns in items:
        who = ", ".join("'%s' (line %d)" % (" ".join(c), l) for c, l, _s, _e in btns)
        where = os.path.relpath(path, ROOT) if os.path.isabs(path) else path
        bits.append("%s:%d [%s] -> %s" % (where, line, " ".join(pcls) or "?", who))
    return "  |  ".join(bits)


# ===========================================================================
print("1 · the shared rule exists and is where the cascade needs it")

base_css = read(os.path.join(STYLES, "base.css"))
check("`.btn-row` is defined in base.css", ".btn-row {" in base_css)
check("…and it zeroes the margin on the buttons inside it",
      re.search(r"\.btn-row\s*>\s*\.btn\s*\{[^}]*margin-top:\s*0", base_css) is not None)

# ⚠ A TIE ON SPECIFICITY IS DECIDED BY ORDER, so this is not a style point.
# `.btn-row > .btn` and `.btn.primary` are both 0-2-0; move the row rule above
# the variant and the margin silently comes back with every test still green.
check("…and it comes AFTER `.btn.primary`, which is the only reason it wins",
      base_css.index(".btn-row > .btn") > base_css.index(".btn.primary {"))

# The cause, stated in the file itself, so nobody deletes the rule as dead code.
check("base.css still explains why the primary's margin is not simply deleted",
      "margin-top: 1.1rem" in base_css and "btn-row" in base_css)


# ===========================================================================
print("\n2 · no two buttons stand side by side at different sizes")

runs = []
for path in jsx_files():
    runs.extend(button_runs(read(path), path))

# ⚠ NOT `> 0`. The reader has already lost two thirds of the app once, in a
# way that left every other check in this file green (see `button_runs`), so the
# floor is set just under the real count and is meant to be raised, not lowered.
check("the reader found every button row in the app (%d)" % len(runs), len(runs) >= 65)

bad_size = mixed_sizes(runs)
check("every side-by-side pair agrees on size", bad_size == [],
      extra=describe(bad_size))


# ===========================================================================
print("\n3 · no primary sits lower than the button beside it")

bad_top = unreset_primaries(runs)
check("every row holding a primary resets the form margin", bad_top == [],
      extra=describe(bad_top))

# The screen that was reported, named outright — a general scan that stops
# covering the actual complaint is a scan that passes for the wrong reason.
brand = read(os.path.join(SRC, "admin", "AdminBrand.jsx"))
check("Save name / Reset name sit in a `btn-row`",
      'className="admin-brand-acts btn-row"' in brand)
check("…and Reset name is no longer the small one",
      'className="btn small"' not in brand.split("Reset name")[0][-600:])


# ===========================================================================
print("\n4 · the detector is proved to reject the markup that was reported")

REPORTED = """
<div className="admin-brand-acts">
  <button type="button" className="btn primary">Save name</button>
  <button type="button" className="btn small">Reset name</button>
</div>
"""
FIXED = """
<div className="admin-brand-acts btn-row">
  <button type="button" className="btn primary">Save name</button>
  <button type="button" className="btn">Reset name</button>
</div>
"""
# Two buttons that are never on screen together are NOT a row, and a scan that
# cannot tell the difference reports the Director panel every time it runs.
BRANCHES = """
<div className="dir-actions">
  {phase === "brief" && (
    <><button type="button" className="btn primary">Read it</button></>
  )}
  {phase === "plan" && (
    <><button type="button" className="btn small">Undo</button></>
  )}
</div>
"""
# A square icon button beside a slab is a shape, not a size mismatch.
ICONS = """
<div className="an-topbar">
  <button type="button" className="btn small back-btn">back</button>
  <button type="button" className="btn">Export</button>
</div>
"""
LONE = '<div className="x"><button className="btn primary">Go</button></div>'

check("the reported pair is caught as mixed sizes",
      mixed_sizes(button_runs(REPORTED)) != [])
check("…and caught as an unreset primary too",
      unreset_primaries(button_runs(REPORTED)) != [])
check("the fixed pair passes both",
      mixed_sizes(button_runs(FIXED)) == []
      and unreset_primaries(button_runs(FIXED)) == [])
check("buttons in different conditional branches are not called a row",
      mixed_sizes(button_runs(BRANCHES)) == [])
check("a square icon button beside a slab is not a size mismatch",
      mixed_sizes(button_runs(ICONS)) == [])
check("a lone button is not a row", button_runs(LONE) == [])


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures
              else "All button-row checks passed (%d rows read)." % len(runs)))
sys.exit(1 if failures else 0)
