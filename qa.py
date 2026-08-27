"""DID THE BOARD ACTUALLY COME OUT RIGHT? — the check that runs on every board.

⚠ THIS EXISTS BECAUSE EVERY FIX IN PHASES 1-3 IS A PROMPT, AND A PROMPT IS A
REQUEST, NOT A GUARANTEE. The cast sheet is asked to be photographic, the app
screen is asked to be priced in ₹, the brand mark is asked to be a flat magenta
placeholder. Each of those is honoured most of the time and ignored some of the
time — that is what an image model is — and the ones that get ignored are
exactly the panels nobody looks at until a customer does.

So the board is measured after it is drawn. Two layers, and the split is about
MONEY:

  A. THIS FILE, `audit()` — free. Pillow and NumPy over pictures we already
     have on disk. It runs on every board, automatically, and costs nothing.
  B. `deep_audit()` — ONE paid vision call for a whole board (see the contact
     sheet below). It sees things pixels cannot: a `$`, an invented logo, a sign
     in the wrong language. ⚠ NEVER AUTOMATIC. It spends the customer's money,
     so it happens when they press the button and not before.

⚠ AND A FINDING IS NOT A FAILURE. Nothing here deletes, redraws or blocks
anything — it reports. A board with a warning on it is still the user's board,
and half of these are judgement calls that only they can settle. The one thing
this must never do is cry wolf: a checker that flags healthy panels gets ignored
within a week, and then it is worse than nothing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Severity is advice about ATTENTION, not about correctness.
#   "error"   — something we can prove went wrong. A magenta square shipped.
#   "warning" — a strong signal, still worth a human eye.
#   "note"    — a hint. Might be the film, might be a bug.
SEVERITIES = ("error", "warning", "note")

# How much colour a panel may carry before a black-and-white style calls it
# coloured. `conform_to_style` should have stripped it long before here.
MAX_GREY_CHROMA = 12


def _finding(code: str, severity: str, message: str, panels=None, hint: str = "") -> dict:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "panels": sorted(panels or []),
        "hint": hint,
    }


def _stats(path: str) -> dict | None:
    """Colourfulness and brightness for one panel, or None if unreadable."""
    import numpy as np
    from PIL import Image

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            # Downsampled hard: these are whole-frame averages, and a thumbnail
            # gives the same answer as the full picture for a fraction of the
            # time — this runs over every panel of every board.
            small = im.resize((96, 96))
            size = im.size
    except (OSError, ValueError):
        return None

    arr = np.asarray(small, dtype=np.float32)
    hi = arr.max(axis=2)
    lo = arr.min(axis=2)
    return {
        # Distance between the strongest and weakest channel: 0 on true grey,
        # large on saturated colour. The cheapest honest measure of "how
        # coloured is this", and the one that separates a photograph from a
        # 3D render far better than brightness does.
        "chroma": float((hi - lo).mean()),
        "luma": float(arr.mean()),
        "size": size,
    }


def _marker_pixels(path: str) -> int:
    """How many placeholder-coloured pixels survived into a finished panel."""
    import numpy as np
    from PIL import Image

    import brand

    try:
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.int16)
    except (OSError, ValueError):
        return 0
    distance = np.abs(arr - np.array(brand.MARKER_RGB, dtype=np.int16)).sum(axis=2)
    return int((distance <= brand.MARKER_TOLERANCE).sum())


def _ratio_of(aspect_ratio: str) -> float | None:
    try:
        w, h = str(aspect_ratio or "").split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return None


def audit(panels: list[dict], *, style: str = "", aspect_ratio: str = "",
          brand_data: dict | None = None, panel_path=None) -> dict:
    """Measure a finished board. Free — no model call, no network.

    Args:
        panels: the board's panel dicts, as `run_storyboard` returns them.
        style / aspect_ratio: what the board was ASKED for, to check against.
        brand_data: the board's brand, so a branded board can be asked the one
            question that matters most — did the logo actually land anywhere?
        panel_path: callable(panel) -> absolute path, because only the caller
            knows where a variant's files live.

    Returns:
        {"findings": [...], "checked": n, "ok": bool} — `ok` is "nothing worth
        an error", not "perfect".
    """
    import brand as brand_mod
    from gemini_client import is_greyscale_style

    findings: list[dict] = []
    drawn: list[tuple[int, str]] = []
    failed: list[int] = []

    for p in panels or []:
        idx = int(p.get("index", 0))
        if p.get("failed") or not p.get("url"):
            failed.append(idx)
            continue
        path = panel_path(p) if panel_path else ""
        if path and os.path.isfile(path):
            drawn.append((idx, path))

    if failed:
        findings.append(_finding(
            "panel_failed", "warning",
            f"{len(failed)} panel(s) did not render.",
            failed,
            "Retry them from the board — a refusal is usually the shot's wording, "
            "not the board.",
        ))

    if not drawn:
        return {"findings": findings, "checked": 0, "ok": not findings}

    stats = {idx: _stats(path) for idx, path in drawn}
    stats = {i: s for i, s in stats.items() if s}

    # --- 1. A placeholder that shipped -------------------------------------
    # ⚠ THE LOUDEST POSSIBLE FAILURE OF PHASE 3, and the reason this check is
    # first. A bright magenta square on a phone screen is far worse than the
    # drifting logo the placeholder scheme replaced. `brand.erase_markers()`
    # should make this unreachable; this is what PROVES it, rather than
    # assuming it.
    leftover = [idx for idx, path in drawn if _marker_pixels(path) > 200]
    if leftover:
        findings.append(_finding(
            "marker_left", "error",
            f"{len(leftover)} panel(s) still show the magenta logo placeholder.",
            leftover,
            "The logo file was probably missing when these were drawn. Re-upload "
            "the logo and redraw them.",
        ))

    # --- 2. A branded board where the logo never landed --------------------
    # ⚠ THIS IS THE CHECK THE WHOLE PHASE WAS WORTH WRITING FOR. The brand
    # scheme rests on the model actually drawing a flat magenta placeholder
    # when asked, and if it simply does not, everything still "works": panels
    # render, nothing errors, and the board quietly has no logo on it anywhere.
    # Silent success is the failure mode of the entire feature, so it is named.
    if brand_mod.has_logo(brand_data) and not is_greyscale_style(style):
        stamped = sum(1 for p in panels or [] if p.get("brand_stamped"))
        if stamped == 0:
            findings.append(_finding(
                "logo_never_landed", "warning",
                "A logo was uploaded, but no panel ended up carrying it.",
                [],
                "That can be right — if no shot shows the app or the packaging, "
                "there is nowhere for it to go. If a shot DOES show one, the "
                "model skipped the placeholder: redraw that panel.",
            ))

    # --- 3. Colour where the style forbids it ------------------------------
    if is_greyscale_style(style):
        coloured = [idx for idx, s in stats.items() if s["chroma"] > MAX_GREY_CHROMA]
        if coloured:
            findings.append(_finding(
                "colour_on_greyscale", "warning",
                f"{len(coloured)} panel(s) carry colour on a black-and-white style.",
                coloured,
                "conform_to_style() should have stripped this. Redrawing the "
                "panel is the quick fix.",
            ))

    # --- 4. The wrong shape ------------------------------------------------
    want = _ratio_of(aspect_ratio)
    if want:
        wrong = [
            idx for idx, s in stats.items()
            if s["size"][1] and abs(s["size"][0] / s["size"][1] - want) > 0.02
        ]
        if wrong:
            findings.append(_finding(
                "aspect_wrong", "warning",
                f"{len(wrong)} panel(s) are not {aspect_ratio}.",
                wrong,
                "Redraw them — normalise_panel() crops to the board's ratio, so "
                "these came from somewhere else.",
            ))

    # --- 5. "Half the board is cartoon" — NOT CHECKED HERE, AND ON PURPOSE ---
    #
    # ⚠ THE REPORTED PHASE 1 SYMPTOM IS THE ONE THING PIXEL STATISTICS CANNOT
    # HONESTLY ANSWER, and this is the note that stops it being re-added.
    #
    # A first version flagged panels whose colourfulness sat far from the
    # board's median, on the reasoning that a 3D cartoon among photographs would
    # stand out. It does — and so does a night exterior next to a daylit
    # kitchen, and a close-up on a face next to a wide of a street. Measured on
    # a synthetic board it fired on exactly that: two ordinary shots, no fault
    # between them.
    #
    # Chroma cannot separate "different MEDIUM" from "different LIGHT", and
    # nothing else cheap can either — smoothness catches a render and also
    # catches fog; edge density catches line art and also catches a crowd. A
    # checker that calls an ordinary night shot a bug gets switched off inside a
    # week, and the real finding is switched off with it.
    #
    # So it belongs to `deep_audit()`, which has a model that can actually LOOK
    # and say "this one is a 3D cartoon and the rest are photographs". Paying
    # for a true answer beats a free guess that trains people to ignore you.

    return {
        "findings": findings,
        "checked": len(drawn),
        "ok": not any(f["severity"] == "error" for f in findings),
    }


# ---------------------------------------------------------------------------
# The paid half: one call for a whole board
# ---------------------------------------------------------------------------
# ⚠ A CONTACT SHEET, NOT ONE CALL PER PANEL, AND THE ARITHMETIC IS THE ARGUMENT.
# A 28-panel board audited a panel at a time is 28 vision calls; as one montage
# it is ONE. The things being looked for — a currency symbol, a logo, a language
# — are all legible at thumbnail size, so the resolution a per-panel call buys
# is resolution nobody needed. Cost is not a footnote on a feature a customer
# presses repeatedly.
CONTACT_COLUMNS = 4
CONTACT_CELL = 380
CONTACT_LABEL = 34
# Beyond this the sheet stops being readable to the model as well as to a human.
# A longer board is audited in several sheets rather than one unreadable one.
MAX_CELLS_PER_SHEET = 24


def build_contact_sheet(paths: list[str], start_number: int = 1):
    """A numbered grid of panels, as one image. Returns a PIL image or None.

    The NUMBER printed on each cell is what makes the answer usable: the model
    is asked to report findings by panel number, so the number has to be in the
    picture rather than implied by position.
    """
    from PIL import Image, ImageDraw

    usable = [p for p in paths if p and os.path.isfile(p)]
    if not usable:
        return None

    thumbs: list[tuple[int, "Image.Image"]] = []
    for i, path in enumerate(usable):
        try:
            with Image.open(path) as im:
                thumb = im.convert("RGB")
                thumb.thumbnail((CONTACT_CELL, CONTACT_CELL))
        except (OSError, ValueError):
            continue
        thumbs.append((start_number + i, thumb))
    if not thumbs:
        return None

    # ⚠ THE ROW HEIGHT FOLLOWS THE PICTURES, it is not a fixed square. A board
    # of 16:9 panels laid into square cells is nearly half empty space, and on a
    # vision call empty space is tokens — which is money, on a button the user
    # presses repeatedly. Every board here is one aspect ratio, so this collapses
    # the sheet to the shape the film actually is.
    cols = min(CONTACT_COLUMNS, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    row_heights = [
        max(t.height for _, t in thumbs[r * cols : (r + 1) * cols]) + CONTACT_LABEL
        for r in range(rows)
    ]
    sheet = Image.new("RGB", (cols * CONTACT_CELL, sum(row_heights)), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)

    for i, (number, thumb) in enumerate(thumbs):
        row = i // cols
        x = (i % cols) * CONTACT_CELL
        y = sum(row_heights[:row])
        sheet.paste(thumb, (x + (CONTACT_CELL - thumb.width) // 2, y + CONTACT_LABEL))
        draw.text((x + 8, y + 8), f"PANEL {number}", fill=(255, 255, 255))
    return sheet


_AUDIT_SYSTEM = (
    "You are a quality checker for a storyboard. You are shown a numbered "
    "contact sheet of the panels of ONE film. Report only what you can actually "
    "SEE in the pictures. Judge each panel on its own and cite it by the number "
    "printed on its cell. If a panel is fine, say nothing about it — an empty "
    "report is a good report, and inventing a problem to look useful is the "
    "worst thing you can do here."
)


def audit_prompt(market: dict | None, brand_data: dict | None) -> str:
    """What to look for, given what this film was supposed to be.

    ⚠ EVERY QUESTION IS ANCHORED TO A RULE THE BOARD WAS ACTUALLY GIVEN. Asking
    an open "is anything wrong?" gets back opinions about composition, which
    nobody asked for and which will be different every run. These are the three
    promises Phases 1-3 make, asked as questions.
    """
    import brand as brand_mod
    import market as market_mod

    data = market_mod.coerce(market or {})
    brand_bits = brand_mod.coerce(brand_data or {})
    lines = [
        "Look at every panel and report ONLY these problems:",
        "",
        "1. MONEY. ",
    ]
    if data.get("currency"):
        lines[-1] += (
            f"This film is for {data.get('country') or 'this market'} and its "
            f"money is {data['currency']}. Report any panel showing a price or "
            f"currency symbol from a DIFFERENT market (a $ sign, a €, a £, and "
            f"so on). A panel with no price at all is fine."
        )
    else:
        lines[-1] += (
            "No market was set for this film, so NO price and NO currency symbol "
            "of any kind should be readable. Report any panel showing one."
        )

    lines.append("")
    lines.append("2. LANGUAGE. ")
    if data.get("language"):
        lines[-1] += (
            f"Readable text on screens, signs and packaging should be in "
            f"{data['language']}. Report any panel whose on-screen text is in "
            f"another language. Ignore text that is too small or blurred to read."
        )
    else:
        lines[-1] += (
            "Report any panel with misspelt or nonsense readable text on a "
            "screen, sign or package."
        )

    lines.append("")
    lines.append("3. BRAND. ")
    if brand_bits.get("logo_ref_id"):
        lines[-1] += (
            "This film has ONE logo, supplied by the film-maker. Report any "
            "panel showing a DIFFERENT logo or brand mark, and any panel still "
            "showing a flat magenta square where a logo should be."
        )
    else:
        lines[-1] += (
            "This film has no logo. Every app icon, sign and package should be "
            "blank and unbranded. Report any panel where a logo, brand mark or "
            "brand name has been invented and drawn."
        )
    if brand_bits.get("name"):
        lines.append("")
        lines.append(
            f"4. PLACEHOLDERS. The brand is called \"{brand_bits['name']}\". "
            f"Report any panel showing bracketed placeholder text such as "
            f"[Your App Name] or [Brand]."
        )

    # ⚠ THE ONE QUESTION PIXELS CANNOT ANSWER, so it is asked here. A cast sheet
    # drawn in the wrong medium drags its panels with it, and the reported board
    # came back half photoreal and half 3D cartoon — visible instantly to anyone
    # LOOKING, and indistinguishable from a night shot to any cheap statistic.
    # See the note in `audit()` on why this is not attempted for free.
    lines.append("")
    lines.append(
        "5. MEDIUM. Every panel of a film should be made of the same material — "
        "all photographs, or all 3D renders, or all pencil drawings. Report any "
        "panel that is plainly a DIFFERENT medium from the majority (a cartoon "
        "among photographs, a photograph among drawings). Different lighting, "
        "a night scene or a close-up is NOT a different medium — do not report "
        "those."
    )

    lines.append("")
    lines.append(
        "Report nothing else. Not composition, not lighting, not art quality, "
        "not anatomy — those are the film-maker's choices, not faults."
    )
    return "\n".join(lines)


def _deep_schema():
    """The answer shape. Structured output, so nothing has to parse prose."""
    from google.genai import types

    return types.Schema(
        type=types.Type.OBJECT,
        required=["findings"],
        properties={
            "findings": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["panel", "kind", "detail"],
                    properties={
                        "panel": types.Schema(type=types.Type.INTEGER),
                        "kind": types.Schema(
                            type=types.Type.STRING,
                            enum=["money", "language", "brand", "placeholder", "look"],
                        ),
                        "detail": types.Schema(type=types.Type.STRING),
                    },
                ),
            )
        },
    )


class DeepAuditError(Exception):
    """The deep audit could not be run. Never raised for a CLEAN board."""


def deep_audit(paths: list[str], *, market: dict | None = None,
               brand_data: dict | None = None, provider: str | None = None) -> dict:
    """Look at a whole board with a vision model. ⚠ THIS SPENDS MONEY.

    ⚠ ONE CALL PER SHEET OF UP TO 24 PANELS, not one per panel. A 28-panel board
    is two calls, not twenty-eight. Everything being looked for — a currency
    symbol, a logo, a language — is legible at thumbnail size, so the resolution
    a per-panel call buys is resolution nobody needed.

    ⚠ AND IT IS ONLY EVER CALLED FROM A BUTTON. Running it at the end of every
    board would bill a customer for a check they did not ask for, on a board
    they may not even keep.

    Returns {"findings": [{panel, kind, detail}], "sheets": n, "checked": n}
    with panel numbers matching the board's own 1-based numbering.
    """
    import json

    from google.genai import types

    from script_breakdown import _model_id, _resolve_provider, get_client

    usable = [p for p in paths if p and os.path.isfile(p)]
    if not usable:
        raise DeepAuditError("This board has no drawn panels to check yet.")

    provider = _resolve_provider(provider)
    client = get_client(provider)
    model_id = _model_id(provider)
    instruction = audit_prompt(market, brand_data)

    findings: list[dict] = []
    sheets = 0
    for start in range(0, len(usable), MAX_CELLS_PER_SHEET):
        chunk = usable[start : start + MAX_CELLS_PER_SHEET]
        sheet = build_contact_sheet(chunk, start_number=start + 1)
        if sheet is None:
            continue
        sheets += 1
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[instruction, sheet],
                config=types.GenerateContentConfig(
                    system_instruction=_AUDIT_SYSTEM,
                    # ⚠ GREEDY. A quality check that returns different answers
                    # for the same board is not a check, it is an opinion — and
                    # the user is paying per run to compare them.
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=_deep_schema(),
                ),
            )
            parsed = json.loads(response.text or "{}")
        except Exception as e:  # noqa: BLE001 — surface the real reason
            logger.warning("[qa] deep audit sheet %d failed: %s", sheets, e)
            raise DeepAuditError(f"The check could not be completed: {e}") from None

        for item in (parsed.get("findings") or []):
            try:
                panel = int(item.get("panel"))
            except (TypeError, ValueError):
                continue
            # ⚠ A NUMBER OUTSIDE THIS SHEET IS DROPPED. The model occasionally
            # answers about a panel it was not shown; keeping that would point
            # the user at a picture that has nothing wrong with it, which is the
            # fastest way to make them stop trusting the whole feature.
            if not (start + 1 <= panel <= start + len(chunk)):
                logger.warning("[qa] dropped a finding for panel %s, not on this sheet.", panel)
                continue
            findings.append({
                "panel": panel,
                "kind": str(item.get("kind") or "brand"),
                "detail": str(item.get("detail") or "").strip(),
            })

    findings.sort(key=lambda f: (f["panel"], f["kind"]))
    return {"findings": findings, "sheets": sheets, "checked": len(usable)}
