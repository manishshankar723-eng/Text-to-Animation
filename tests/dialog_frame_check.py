"""dialog_frame_check.py — NO DIALOG IN THIS APP CLOSES ON A STRAY CLICK.

Run:  python tests/dialog_frame_check.py   (no backend, no browser — source only)

Why this file exists, in one sentence: **a click that landed a few pixels
outside a dialog threw away a Premiere import somebody was a dozen steps into.**

    "mai abhi premiere pro ka file import kar raha tha … galti se mera mouse pop
     up se bahar screen ke click hua mera popup cut gaya … mera mehnat bekar ho
     gaya … aisa nhi hona chahiye"

⚠ **THE BACKDROP CLICK WAS THE APP'S OWN CONVENTION, AND THAT IS THE DANGER.**
Every one of the thirty dialogs here was written by copying the one beside it,
so `onClick={() => setThingOpen(false)}` on `.modal-overlay` spread to all of
them — and a dialog's contents are saved NOWHERE. A read that took minutes, a
half-typed name, the folder list a report just printed: one click outside the
box and it is gone, with no way back but doing the whole thing again.

So the rule is app-wide, and this file is what keeps it that way: **no
`.modal-overlay` may carry an `onClick`**, and **every dialog must still have a
✕**, because a dialog with neither would be a trap. RULEBOOK **E65**.

⚠ **AND A DIALOG THAT CANNOT BE DISMISSED MUST BE MOVEABLE**, or it simply sits
on top of the thing the user is trying to check it against. That is
`client/src/dialog_move.js` — ONE implementation for every dialog, installed
once from `App.jsx`, which is also why the checks below matter: a next agent who
opens a single dialog's JSX sees nothing about dragging in it at all.

⚠ **THE DETECTOR IS PROVED TO FAIL FIRST.** A grep that has never caught
anything is decoration, so the last section puts the old handler back — in both
shapes it was really written in — and requires the same scan to reject it.
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "client", "src")

failures = []


def check(label, got, want=True, extra=""):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {extra}"))
    if not good:
        failures.append(label)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def jsx_files():
    for base, _dirs, names in os.walk(SRC):
        for name in sorted(names):
            if name.endswith(".jsx"):
                yield os.path.join(base, name)


def overlay_tags(source):
    """Every overlay's OPENING TAG, however it is wrapped across lines.

    ⚠ NOT a line-based grep. The one dialog whose handler was spread over five
    lines (`FinalVideoArtStep`) is exactly the one a single-line pattern would
    have walked straight past.
    """
    for m in re.finditer(r'className="modal-overlay"', source):
        start = source.rindex("<", 0, m.start())
        end = source.index(">", m.end())
        yield start, source[start:end + 1]


def backdrop_handlers(source):
    return [tag for _at, tag in overlay_tags(source) if "onClick" in tag]


# ===========================================================================
print("\n1 · no dialog closes on a click outside it")

seen = 0
for path in jsx_files():
    src = read(path)
    if 'className="modal-overlay"' not in src:
        continue
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    bad = backdrop_handlers(src)
    seen += len(list(overlay_tags(src)))
    check(f"{rel} — no onClick on the overlay", bad == [], extra=str(bad[:1]))

# ⚠ A FLOOR ON THE COUNT, so that deleting every dialog is not a way to pass.
# It is not an assertion about any particular screen — new dialogs are welcome.
check(f"…across every dialog in the app ({seen} found)", seen >= 30, extra=str(seen))


# ===========================================================================
print("\n2 · …and every one of them still has a way out")

# ⚠ THE OTHER HALF OF THE RULE. Taking the backdrop away from a dialog that had
# no ✕ would not be a fix, it would be a trap with no exit at all.
for path in jsx_files():
    src = read(path)
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    for at, _tag in overlay_tags(src):
        line = src[:at].count("\n") + 1
        check(f"{rel}:{line} — offers a ✕", "modal-close" in src[at:at + 6000],
              extra="no modal-close inside the dialog")


# ===========================================================================
print("\n3 · so every dialog can be dragged out of the way instead")

move = read("client", "src", "dialog_move.js")
app = read("client", "src", "App.jsx")

check("the behaviour is installed once, from App.jsx",
      'from "./dialog_move.js"' in app and "installDialogMove()" in app)
check("…and it is idempotent, because App can remount",
      "if (installed" in move)
check("it finds dialogs by the shared class, not by component",
      '.closest(".modal-overlay")' in move)
# ⚠ THE HANDLE IS THE HEADING. A card-wide grab would eat text selection, and a
# paragraph in a dialog is often the thing being read out to somebody.
check("the handle is the heading, not the whole card",
      'closest("h1, h2, h3, h4")' in move)
check("…and never something the user could be operating",
      'closest("button, a, input, select, textarea, label, [contenteditable]")' in move)
# ⚠ POINTER CAPTURE, or a fast drag off the card leaves the dialog stuck to the
# cursor with the button already released.
check("the pointer is captured for the length of the drag",
      "setPointerCapture" in move and "releasePointerCapture" in move)
# ⚠ A CARD DRAGGED PAST AN EDGE WOULD BE UNREACHABLE — ✕ included — and these
# dialogs no longer have any other exit.
check("a strip of the card is always kept on screen",
      "window.innerWidth - EDGE" in move and "window.innerHeight - EDGE" in move)
check("…and it can never be dragged above the top, where the handle is",
      "-from.top" in move)
# ⚠ THE OFFSET LIVES ON THE NODE. A dialog is unmounted when it closes, so the
# next one is a new element with no offset — which is what makes "it opens in
# the middle again" free. A module-level position would come back where it was
# last shoved, which reads as broken.
check("the position is remembered on the element, not in the module",
      "card._dlgX" in move)
check("the card is moved with a transform, not by re-laying it out",
      "style.transform" in move)

css = read("client", "src", "styles", "soon-upgrade.css")
check("the cursor says the heading is a handle",
      ".modal-overlay h2," in css and "cursor: grab" in css and "cursor: grabbing" in css)
check("…and a drag does not paint the title blue instead",
      "user-select: none" in css)
# RULEBOOK E4: helper text goes in the element's own `title`, on hover — and a
# hint written into thirty dialogs by hand is a hint most of them would lose.
check("…with the words in a hover hint, stamped by the shared module",
      "Drag to move this window" in move)
# ⚠ THE WARNING LIVES WHERE A DIALOG'S SURFACE IS ACTUALLY EDITED. Nothing in a
# dialog's own JSX mentions any of this.
check("the CSS beside .modal-overlay carries the warning",
      "RULEBOOK E65" in css and "dialog_move.js" in css)


# ===========================================================================
print("\n4 · the dialog that paid for this keeps its ✕ on screen")

# ⚠ THE IMPORT DIALOG SCROLLS FURTHER THAN ANY OTHER — its report can name a
# dozen folders — so its ✕ and its heading both scrolled away with the content.
# On a dialog that no longer closes on the backdrop, that is a reader with no
# exit and no handle on screen at all.
modal = read("client", "src", "components", "ProjectImportModal.jsx")
xchg = read("client", "src", "styles", "animatic-editor.css")
check("its title bar sticks to the top of the card",
      '"an-xchg-bar"' in modal and ".an-xchg-bar {" in xchg and "position: sticky" in xchg)
check("…and the ✕ rides inside that bar",
      modal.index('"an-xchg-bar"') < modal.index('className="modal-close"'))
check("…and there is no second, local copy of the dragging",
      "onPointerDown" not in modal and "setPointerCapture" not in modal)
# ⚠ Escape closes this one too, and ONLY this one: it is one keystroke away from
# the typing done in this dialog and threw away the same minutes the stray click
# did. Elsewhere Escape is a deliberate press and is left alone.
check("…and Escape does not close it either", '"Escape"' not in modal)


# ===========================================================================
print("\n5 · a button in a dialog has to look like a button")

# ⚠ REPORTED OF THIS SAME DIALOG, AND IT IS AN APP-WIDE RULE — the two ghost
# buttons under the file picker read as plain text sitting on the panel:
# *"buttun merge ho ja raha hai bg mai … thoda aur stroke/highlight karo …
# agar aur kahi hai to usko v kar do"*. It matters most HERE, because since
# E65 the way out of a dialog is a button; a Cancel nobody can see is a dialog
# with a hidden exit.
base = read("client", "src", "styles", "base.css")
theme = read("client", "src", "styles", "theme.css")

ghost = base[base.index(".btn.ghost {"):]
ghost = ghost[:ghost.index("}")]
check("a ghost button keeps a visible edge",
      "border-color: transparent" not in ghost and "--btn-border" in ghost,
      extra=" ".join(ghost.split()))
# ⚠ WHAT MAKES IT A GHOST IS THE MISSING FILL, NOT THE MISSING OUTLINE. Give it
# a background as well and the quieter of two buttons standing side by side
# stops being the quieter one.
check("…and is still the quiet one, with no fill",
      "background: transparent" in ghost, extra=" ".join(ghost.split()))
# ⚠ `--border` IS A PANEL'S EDGE — a line whose whole job is to be barely there.
# A button wearing it on the dark page has no edge at all, which is how these
# buttons came to be invisible in the first place.
dark = theme[:theme.index("--btn-border: #c7cedd")]
check("a button's edge is its own value, brighter than a panel's",
      "--btn-border: var(--border)" not in dark,
      extra="--btn-border is --border again")
# The hover was never the problem and must survive:
# *"jab mai jata hun tab highlight ho hota hai ye badhiya hai isko rakhne do"*.
check("…and the hover still lifts the edge to gold",
      "border-color: var(--primary)" in base[base.index(".btn:hover"):][:220])


# ===========================================================================
print("\n6 · the scan is proved to catch the handler coming back")

# ⚠ EVERY DIALOG WAS WRITTEN BY COPYING THE ONE BESIDE IT — which is how the
# handler reached all thirty in the first place. It will be offered again.
broken_single = ('<div className="modal-overlay" onClick={() => setThingOpen(false)}>\n'
                 '  <div className="card" />\n')
broken_multi = ('<div\n  className="modal-overlay"\n  onClick={() => {\n'
                '    setPicking(false);\n  }}\n>\n  <div className="card" />\n')
good = '<div className="modal-overlay">\n  <div className="card" />\n'
check("a one-line backdrop handler is caught", backdrop_handlers(broken_single) != [])
check("…and one spread over five lines is caught too", backdrop_handlers(broken_multi) != [])
check("…while the fixed shape passes", backdrop_handlers(good) == [])


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures
              else f"All dialog-frame checks passed ({seen} dialogs)."))
sys.exit(1 if failures else 0)
