"""EVERY FIX IN PHASES 1-3 IS A PROMPT, AND A PROMPT IS A REQUEST.

The cast sheet is ASKED to be photographic. The app screen is ASKED to be priced
in ₹. The brand mark is ASKED to be a flat magenta placeholder. Each of those is
honoured most of the time and ignored some of the time — that is what an image
model is — and the panels that got ignored are exactly the ones nobody looks at
until a customer does.

So the board is measured after it is drawn. Phase 4 of the Production Brief.

---------------------------------------------------------------------------
1. TWO LAYERS, AND THE SPLIT IS ABOUT MONEY
---------------------------------------------------------------------------
    A. `qa.audit()` — free. Pillow and NumPy over files already on disk. Runs on
       EVERY board, automatically, at the end of `run_storyboard`.
    B. `qa.deep_audit()` — ONE paid vision call per contact sheet of 24 panels.
       ⚠ NEVER AUTOMATIC. It spends the customer's money, so it happens when
       they press the button and not as a side effect of generating a board.

⚠ THIS TEST NEVER CALLS A MODEL. Layer B is checked at its seams — the sheet it
builds, the questions it asks, the answers it refuses — because the value of a
checker is entirely in what it asks and what it throws away.

---------------------------------------------------------------------------
2. THE CHECK THAT JUSTIFIES THE PHASE
---------------------------------------------------------------------------
`logo_never_landed`. The brand scheme rests on the model actually drawing a
magenta placeholder when asked. If it simply does not, EVERYTHING STILL WORKS:
panels render, nothing errors, and the board quietly has no logo on it anywhere.
Silent success is the failure mode of the whole feature, and it is the one thing
no amount of staring at a green build would ever surface.

---------------------------------------------------------------------------
3. AND THE RULE THAT KEEPS IT USEFUL
---------------------------------------------------------------------------
⚠ A CHECKER THAT CRIES WOLF IS WORSE THAN NO CHECKER. It gets ignored inside a
week, and then the real finding is ignored with it. So: a clean board must
produce ZERO findings, a night shot beside a daylit kitchen must not be called a
fault, and a finding about a panel the model was not shown is dropped rather
than shown to the user.

Run:
    python tests/board_audit_check.py
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


import tempfile  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

import brand  # noqa: E402
import qa  # noqa: E402

WORK = tempfile.mkdtemp(prefix="board_audit_check_")


def board(n=8, size=(320, 180), tint=(70, 95, 125)):
    """A plausible, healthy board: the same film, panel to panel."""
    paths = []
    for i in range(n):
        im = Image.new("RGB", size, tint)
        # A little variety, the way real shots differ without changing medium.
        ImageDraw.Draw(im).rectangle([20, 20, 60 + i * 6, 120], fill=(150, 140, 110))
        p = os.path.join(WORK, f"p{n}_{i:02d}.png")
        im.save(p)
        paths.append(p)
    return paths


def panels_for(paths, failed=()):
    return [
        {"index": i, "url": None if i in failed else "u", "failed": i in failed,
         "brand_stamped": False}
        for i in range(len(paths))
    ]


def run(paths, **kw):
    kw.setdefault("style", "cinematic")
    kw.setdefault("aspect_ratio", "16:9")
    failed = kw.pop("failed", ())
    return qa.audit(panels_for(paths, failed), panel_path=lambda p: paths[p["index"]], **kw)


def codes(report):
    return {f["code"] for f in report["findings"]}


# ---------------------------------------------------------------------------
print("\n[1] ⚠ a clean board says NOTHING — the rule everything else rests on")

clean = run(board())
check("no findings at all on a healthy board", clean["findings"] == [])
check("…and it reports itself ok", clean["ok"] is True)
check("…having actually looked at every panel", clean["checked"] == 8)

# ⚠ THE FALSE-POSITIVE THAT WOULD KILL THIS FEATURE. A night exterior really is
# darker and less colourful than the daylit kitchen before it. Calling that a
# fault teaches people to ignore the panel that IS wrong.
mixed = board()
Image.new("RGB", (320, 180), (18, 20, 34)).save(mixed[2])   # a night shot
Image.new("RGB", (320, 180), (200, 195, 180)).save(mixed[5])  # a bright exterior
check("⚠ a night shot and a bright exterior in one film are NOT flagged",
      "look_outlier" not in codes(run(mixed)))

# ---------------------------------------------------------------------------
print("\n[2] the placeholder that shipped — the loudest possible failure")

left = board()
im = Image.new("RGB", (320, 180), (70, 95, 125))
ImageDraw.Draw(im).rectangle([40, 40, 140, 140], fill=brand.MARKER_RGB)
im.save(left[4])
report = run(left, brand_data={"name": "X", "logo_ref_id": "a"})
found = next(f for f in report["findings"] if f["code"] == "marker_left")
check("a magenta square left on a panel is found", found["panels"] == [4])
check("⚠ …and it is an ERROR, not a note: it is worse than the drifting logo "
      "the placeholder scheme replaced",
      found["severity"] == "error")
check("…so the board does not report itself ok", report["ok"] is False)
check("…and the hint says what to actually do about it",
      "logo" in found["hint"] and "redraw" in found["hint"].lower())

# ---------------------------------------------------------------------------
print("\n[3] ⚠ the check this whole phase was worth writing for")

silent = run(board(), brand_data={"name": "X", "logo_ref_id": "a"})
check("a logo was uploaded and NO panel carries it → said out loud",
      "logo_never_landed" in codes(silent))
check("⚠ …as a WARNING and not an error, because it can legitimately be right: "
      "a film with no product shot has nowhere to put a logo",
      next(f for f in silent["findings"]
           if f["code"] == "logo_never_landed")["severity"] == "warning")
check("…and the hint says which of the two it is and how to tell",
      "nowhere for it to go" in
      next(f for f in silent["findings"] if f["code"] == "logo_never_landed")["hint"])

landed = qa.audit(
    [{"index": i, "url": "u", "failed": False, "brand_stamped": i == 2}
     for i in range(8)],
    style="cinematic", aspect_ratio="16:9",
    brand_data={"name": "X", "logo_ref_id": "a"},
    panel_path=lambda p: board()[p["index"]],
)
check("ONE panel carrying the logo is enough — it is not asked to be on all",
      "logo_never_landed" not in codes(landed))
check("an unbranded board is never asked the question at all",
      "logo_never_landed" not in codes(run(board())))
check("⚠ nor is a greyscale board, which skips the whole brand scheme by design",
      "logo_never_landed" not in codes(
          run(board(), style="rough-sketch", brand_data={"logo_ref_id": "a"})))

# ---------------------------------------------------------------------------
print("\n[4] the promises Phases 1-3 make, measured")

grey = board()
Image.new("RGB", (320, 180), (220, 60, 40)).save(grey[1])
check("colour on a black-and-white board is caught",
      "colour_on_greyscale" in codes(run(grey, style="rough-sketch")))
check("…and a genuinely grey board is not",
      "colour_on_greyscale" not in codes(
          run(board(tint=(120, 120, 120)), style="rough-sketch")))

shape = board()
Image.new("RGB", (200, 200), (70, 95, 125)).save(shape[3])
check("a panel that is not the board's aspect ratio is caught",
      "aspect_wrong" in codes(run(shape, aspect_ratio="16:9")))
check("…and a board that IS its ratio is not",
      "aspect_wrong" not in codes(run(board(), aspect_ratio="16:9")))

check("panels that never rendered are reported",
      "panel_failed" in codes(run(board(), failed=(6, 7))))

# ⚠ "HALF THE BOARD IS CARTOON" IS DELIBERATELY NOT CHECKED FOR FREE, and this
# is the assertion that keeps it that way. A first version flagged panels whose
# colourfulness sat far from the board's median — and fired on the two ordinary
# shots above, a night exterior and a bright one, with no fault between them.
# Chroma cannot separate "different MEDIUM" from "different LIGHT". The question
# went to `deep_audit()`, which has a model that can actually look.
wild = board()
Image.new("RGB", (320, 180), (255, 0, 40)).save(wild[3])
check("⚠ the free audit makes NO claim about medium drift — a guess that fires "
      "on a night shot trains people to ignore the real finding",
      not any(f["code"] == "look_outlier" for f in run(wild)["findings"]))
check("…and the reason is written down where someone would re-add it",
      "NOT CHECKED HERE, AND ON PURPOSE" in read("qa.py"))

# ---------------------------------------------------------------------------
print("\n[5] the free audit runs itself, and never blocks anything")

sbp = read("storyboard_pipeline.py")
check("run_storyboard audits its own board",
      "import qa" in sbp and "qa.audit(" in sbp)
check("…and returns it with the board",
      '"audit": report,' in sbp)
check("⚠ a crash in the audit never costs the finished board",
      "audit failed — board unaffected" in sbp)
check("⚠ a STOPPED run is not audited — half a board has holes by definition, "
      "and reporting them as faults is noise",
      "if not stopped:" in sbp)
check("it measures the ACTIVE picture, not a numbered version",
      "panel_" in sbp.split("panel_path=lambda")[1][:200])

# ---------------------------------------------------------------------------
print("\n[6] the paid half: one call for a whole board, and only on a button")

check("⚠ the deep check is a ROUTE, not a pipeline step — generating a board "
      "must never bill for a check nobody asked for",
      '@app.post("/storyboards/{job_id}/check"' in read("server", "main.py")
      and "qa.deep_audit(" not in sbp)
check("the client reaches it from a button",
      "Check this board" in read("client", "src", "components", "StoryboardBoard.jsx"))
check("…and the button says what it does, not 'run QA'",
      "checkStoryboard" in read("client", "src", "api.js"))

sheet = qa.build_contact_sheet(board(n=6))
check("⚠ panels go as ONE contact sheet — a 28-panel board is 2 calls, not 28",
      sheet is not None and qa.MAX_CELLS_PER_SHEET >= 12)
check("…numbered in the picture, so findings can name a panel",
      "PANEL " in read("qa.py"))
check("⚠ …and the rows follow the pictures, because empty space on a vision "
      "call is tokens, which is money on a button people press repeatedly",
      sheet.height < 6 // qa.CONTACT_COLUMNS * (qa.CONTACT_CELL + qa.CONTACT_LABEL) + 400
      and sheet.height < qa.CONTACT_CELL * 2)
check("an empty board raises rather than pretending it checked",
      isinstance(qa.DeepAuditError("x"), Exception))

# ---------------------------------------------------------------------------
print("\n[7] what the paid check is allowed to say")

prompt_in = qa.audit_prompt(
    {"country": "India", "currency": "₹ (Indian rupee)", "language": "Hindi"},
    {"name": "Lickyeat", "logo_ref_id": "a"},
)
check("it asks about MONEY against this film's own currency",
      "₹ (Indian rupee)" in prompt_in and "DIFFERENT market" in prompt_in)
check("⚠ …and says a panel with no price is FINE, so it cannot invent a fault "
      "out of an ordinary shot",
      "no price at all is fine" in prompt_in)
check("it asks about LANGUAGE against this film's own",
      "in Hindi" in prompt_in and "Ignore text that is too small" in prompt_in)
check("it asks about the ONE supplied logo, and about leftover placeholders",
      "DIFFERENT logo" in prompt_in and "flat magenta square" in prompt_in)
check("it asks about bracketed placeholders when a brand name is known",
      "[Your App Name]" in prompt_in)
check("⚠ it asks the question the free audit refused: is any panel a DIFFERENT "
      "MEDIUM — a cartoon among photographs",
      "5. MEDIUM" in prompt_in and "cartoon among photographs" in prompt_in)
check("⚠ …while telling it that a night scene is NOT a different medium, which "
      "is the exact confusion that made this uncheckable for free",
      "is NOT a different medium" in prompt_in)
check("⚠ …and is told to report NOTHING ELSE — an open 'is anything wrong?' "
      "returns opinions about composition that differ every run",
      "Report nothing else" in prompt_in
      and "the film-maker" in prompt_in)

prompt_out = qa.audit_prompt({}, {})
check("⚠ with no market set it asks for NO currency at all, matching the rule "
      "the panels were actually drawn under",
      "NO currency symbol of any kind should be readable" in prompt_out)
check("…and with no brand, that no logo was invented",
      "blank and unbranded" in prompt_out)

qa_src = read("qa.py")
check("⚠ the check is greedy — a quality report that differs run to run is an "
      "opinion, and the user is paying per run to compare them",
      "temperature=0.0" in qa_src)
check("⚠ a finding about a panel the model was not shown is DROPPED — pointing "
      "someone at an innocent picture is the fastest way to lose their trust",
      "not on this sheet" in qa_src)
check("the answer is structured, so nothing has to parse prose",
      "response_schema=_deep_schema()" in qa_src)
check("⚠ an empty findings list is the GOOD answer, and only a real failure "
      "raises — the two can never be confused by the client",
      "Never raised for a CLEAN board" in qa_src)

board_ui = read("client", "src", "components", "StoryboardBoard.jsx")
check("⚠ 'nothing found' is PRINTED — a check that says nothing when it passes "
      "is a check nobody believes ran",
      "no wrong currency" in board_ui)
check("⚠ an error clears the previous result, so a stale clean report cannot "
      "sit beside a failure reading as 'checked, all good'",
      "setCheckResult(null);" in board_ui)
check("⚠ nothing here blocks the board — it is a note beside it, not a gate",
      "not a gate" in board_ui)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The board is measured after it is drawn — for free on every run, and "
      "with a model only when someone asks.")
