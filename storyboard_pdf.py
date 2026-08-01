"""
storyboard_pdf.py — Script → Storyboard, Stage F: export the board as a PDF.

Composes the generated panels (from output/_storyboards/{job_id}/) into a clean,
printable PDF — a 2×3 grid of panels per page with captions, plus a title. Uses
Pillow only (no extra dependency): each page is rendered as an image and saved as
a multi-page PDF.
"""

import logging
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# A4-ish portrait at ~150 DPI.
PAGE_W, PAGE_H = 1240, 1754
MARGIN = 64
GUTTER = 40
COLS, ROWS = 2, 3
# A board WITH dialogue prints 2×2. At 2×3 the band of speech has to come out of
# the picture, which drops a 16:9 panel from ~308px tall to ~215px; at 2×2 the
# cell is tall enough that the pictures stay exactly the size they always were
# and the dialogue is simply extra room. Fewer panels per page, same panel size.
ROWS_DIALOGUE = 2

# Print-friendly palette (white page, dark ink).
INK = (24, 26, 32)
MUTED = (110, 116, 130)
CELL_BG = (238, 240, 244)
LINE = (210, 214, 222)
# Character chips, matching the gold the app uses for pills.
CHIP_BG = (176, 132, 26)
CHIP_INK = (255, 255, 255)
# Scene tag: a tinted pill (not gold — the cast chips own that colour here).
SCENE_BG = (243, 238, 224)
SCENE_LINE = (214, 199, 160)
SCENE_INK = (124, 94, 20)
# Dialogue: a quiet rule down the left with the speaker's name above the line,
# the way a script prints it. Deliberately not gold — the cast chips are gold,
# and a spoken line is a different kind of thing from a name tag.
DIALOGUE_RULE = (176, 132, 26)
DIALOGUE_NAME = (124, 94, 20)
DIALOGUE_INK = (44, 47, 56)

# Height reserved UNDER each panel image for shot label, description, camera,
# location and the cast chips. Grown from the old 96px, which only fitted a
# two-line description.
TEXT_H = 196
# Height of the dialogue band, sized to hold a two-line exchange (the common
# case: one character speaks, another answers) before it has to say "+N more".
# Only reserved on a board that actually HAS dialogue.
DIALOGUE_H = 152


def _load_font(size: int, bold: bool = False):
    """Best-effort TrueType font with a graceful fallback."""
    candidates = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"]
        if bold
        else ["arial.ttf", "DejaVuSans.ttf", "Arial.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_width, max_lines):
    """Wrap text to fit max_width, capped at max_lines (… on overflow)."""
    if not text:
        return []
    # Estimate chars-per-line from average glyph width, then refine.
    approx = max(8, int(max_width / (font.size * 0.55)))
    lines: list[str] = []
    for para in textwrap.wrap(text, width=approx):
        lines.append(para)
    # Trim to max_lines with an ellipsis.
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines and draw.textlength(lines[-1] + "…", font=font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def _truncate(draw, text, font, max_width):
    """Single-line ellipsis so a long location can't run past its cell."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def _meta_row(draw, x, y, label, value, f_label, f_value, max_width):
    """Draw a 'Label  value' metadata row; returns the next y."""
    if not value:
        return y
    draw.text((x, y + 2), label, font=f_label, fill=MUTED)
    label_w = draw.textlength(label, font=f_label) + 10
    draw.text(
        (x + label_w, y),
        _truncate(draw, value, f_value, max_width - label_w),
        font=f_value,
        fill=INK,
    )
    return y + 24


def _scene_pill(draw, x, y, text, font):
    """Draw the 'SCENE n' tag as an outlined pill so it reads on a white page."""
    pad_x, h = 8, 20
    w = draw.textlength(text, font=font) + 2 * pad_x
    draw.rounded_rectangle(
        [x, y, x + w, y + h], radius=h // 2, fill=SCENE_BG, outline=SCENE_LINE
    )
    draw.text((x + pad_x, y + 2), text, font=font, fill=SCENE_INK)
    return x + w


def _cast_chips(draw, x, y, names, font, max_width):
    """Draw character names as gold pills (as they appear in the app).

    Wraps to a second row, then collapses the remainder into a '+N' pill so a
    crowded shot can't overflow into the panel below.
    """
    if not names:
        return y
    pad_x, gap, h = 9, 6, 22
    cx, rows = x, 1
    for i, name in enumerate(names):
        w = draw.textlength(name, font=font) + 2 * pad_x
        if cx + w > x + max_width:
            if rows == 2:  # out of room — say how many are left and stop
                left = len(names) - i
                if left > 0:
                    tag = f"+{left}"
                    tw = draw.textlength(tag, font=font) + 2 * pad_x
                    cx = min(cx, x + max_width - tw)
                    draw.rounded_rectangle([cx, y, cx + tw, y + h], radius=h // 2, fill=CHIP_BG)
                    draw.text((cx + pad_x, y + 3), tag, font=font, fill=CHIP_INK)
                break
            cx, y, rows = x, y + h + gap, rows + 1
        draw.rounded_rectangle([cx, y, cx + w, y + h], radius=h // 2, fill=CHIP_BG)
        draw.text((cx + pad_x, y + 3), name, font=font, fill=CHIP_INK)
        cx += w + gap
    return y + h + gap


def _dialogue_lines(panel: dict) -> list[dict]:
    """A panel's spoken lines as [{character, line}], or [] if nobody speaks.

    Older boards were generated before dialogue existed and simply have no key —
    they get an empty list and print exactly as they always did.
    """
    out = []
    for item in panel.get("dialogue") or []:
        if not isinstance(item, dict):
            continue
        line = str(item.get("line", "") or "").strip()
        if line:
            out.append({"character": str(item.get("character", "") or "").strip(), "line": line})
    return out


def _dialogue_block(draw, x, y, dialogue, f_name, f_line, max_width, max_h):
    """Draw the spoken lines under the caption; returns the next y.

    Drawing stops at `max_h` and says how many lines were left over, so a
    talkative panel can't spill into the cell beneath it. Nothing at all is
    drawn — not even a heading — when the panel is silent.
    """
    if not dialogue:
        return y
    top, bottom = y, y + max_h
    text_x = x + 12  # clears the rule
    text_w = max_width - 12
    drawn = 0
    for i, entry in enumerate(dialogue):
        name = (entry.get("character") or "").upper()
        rows = ([name] if name else []) + _wrap(draw, entry["line"], f_line, text_w, 2)
        needed = (20 if name else 0) + 22 * (len(rows) - (1 if name else 0))
        # Keep back a row for the "+N more" note while lines remain, so running
        # out of room can never look like the panel simply had less to say.
        reserve = 20 if i < len(dialogue) - 1 else 0
        if y + needed > bottom - reserve:
            break
        if name:
            draw.text((text_x, y), name, font=f_name, fill=DIALOGUE_NAME)
            y += 20
        for row in rows[1:] if name else rows:
            draw.text((text_x, y), row, font=f_line, fill=DIALOGUE_INK)
            y += 22
        y += 4
        drawn += 1

    left = len(dialogue) - drawn
    if left > 0 and y + 20 <= bottom:
        draw.text((text_x, y), f"+{left} more line{'' if left == 1 else 's'}", font=f_name, fill=MUTED)
        y += 20
    # One rule spanning everything actually drawn — the "someone is speaking" cue.
    if y > top:
        draw.line([(x + 2, top), (x + 2, y - 4)], fill=DIALOGUE_RULE, width=3)
    return y


def _fit(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Scale an image to fit inside box preserving aspect (no upscaling blur)."""
    out = img.copy()
    out.thumbnail((box_w, box_h), Image.LANCZOS)
    return out


def build_storyboard_pdf(
    job_id: str,
    output_dir: str,
    title: str,
    panels: list[dict],
    subdir: str = "",
) -> str:
    """Render the storyboard panels into a PDF and return its path.

    Args:
        job_id: owning job id (locates the panel PNG folder).
        output_dir: base output directory.
        title: storyboard title shown on page 1.
        panels: list of panel dicts {index, description, dialogue, camera,
            location, failed}. `dialogue` ([{character, line}]) is optional and
            empty for a silent shot — a page with no speech on it is laid out
            exactly as before, at the old panel size.
        subdir: style-variant subfolder holding the PNGs (""=board root / variant 0).

    Returns:
        Absolute path to the written PDF.

    Raises:
        ValueError if there are no drawable panels.
    """
    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    src_dir = os.path.join(board_dir, subdir) if subdir else board_dir

    drawable = [
        p for p in panels
        if not p.get("failed")
        and os.path.isfile(os.path.join(src_dir, f"panel_{p['index']:02d}.png"))
    ]
    if not drawable:
        raise ValueError("No generated panels to export yet.")

    f_title = _load_font(40, bold=True)
    f_shot = _load_font(22, bold=True)
    f_scene = _load_font(16, bold=True)
    f_cap = _load_font(20)
    f_label = _load_font(15, bold=True)
    f_meta = _load_font(17)
    f_chip = _load_font(15, bold=True)
    f_dname = _load_font(14, bold=True)
    f_dline = _load_font(17)

    # The grid is decided ONCE for the whole document, not per page: a board
    # whose pages alternated between 6-up and 4-up would read as two documents
    # stapled together. Any dialogue anywhere → the roomier grid throughout.
    has_dialogue = any(_dialogue_lines(p) for p in drawable)
    rows = ROWS_DIALOGUE if has_dialogue else ROWS
    text_h = TEXT_H + (DIALOGUE_H if has_dialogue else 0)
    per_page = COLS * rows
    pages: list[Image.Image] = []

    for start in range(0, len(drawable), per_page):
        chunk = drawable[start : start + per_page]
        page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
        draw = ImageDraw.Draw(page)

        top = MARGIN
        if start == 0:
            draw.text((MARGIN, top), title or "Storyboard", font=f_title, fill=INK)
            top += 64
            draw.line([(MARGIN, top), (PAGE_W - MARGIN, top)], fill=LINE, width=2)
            top += 24

        cell_w = (PAGE_W - 2 * MARGIN - (COLS - 1) * GUTTER) // COLS
        grid_h = PAGE_H - top - MARGIN
        cell_h = (grid_h - (rows - 1) * GUTTER) // rows
        # Room for shot label, caption, camera, location, the cast chips — and
        # the dialogue band when this board has speech in it.
        img_box_h = cell_h - text_h

        for i, p in enumerate(chunk):
            col = i % COLS
            row = i // COLS
            x = MARGIN + col * (cell_w + GUTTER)
            y = top + row * (cell_h + GUTTER)

            # Image frame
            draw.rectangle([x, y, x + cell_w, y + img_box_h], fill=CELL_BG, outline=LINE)
            panel_path = os.path.join(src_dir, f"panel_{p['index']:02d}.png")
            try:
                img = Image.open(panel_path).convert("RGB")
                fitted = _fit(img, cell_w - 4, img_box_h - 4)
                page.paste(
                    fitted,
                    (x + (cell_w - fitted.width) // 2, y + (img_box_h - fitted.height) // 2),
                )
            except OSError:
                draw.text((x + 12, y + 12), "(panel missing)", font=f_cap, fill=MUTED)

            # Shot label + a SCENE n pill, mirroring the shot card in the app.
            ty = y + img_box_h + 10
            shot_label = f"Shot {p['index'] + 1}"
            draw.text((x, ty), shot_label, font=f_shot, fill=INK)
            scene = p.get("scene_number")
            if scene:
                _scene_pill(
                    draw,
                    x + draw.textlength(shot_label, font=f_shot) + 10,
                    ty + 2,
                    f"SCENE {scene}",
                    f_scene,
                )
            ty += 30

            for line in _wrap(draw, p.get("description", ""), f_cap, cell_w, 2):
                draw.text((x, ty), line, font=f_cap, fill=MUTED)
                ty += 26

            # What is SAID in this panel, straight after the caption — and
            # nothing at all when the shot is silent.
            if has_dialogue:
                d_top = ty + 4
                ty = _dialogue_block(
                    draw, x, d_top, _dialogue_lines(p), f_dname, f_dline, cell_w, DIALOGUE_H - 8
                )
                # Keep the meta rows on the same baseline across the row of
                # cells, so a silent panel doesn't pull its Camera line upward.
                ty = d_top + DIALOGUE_H - 8

            # Camera / Location / cast — the shooting detail a board is used for.
            ty += 4
            ty = _meta_row(draw, x, ty, "Camera", p.get("camera", ""), f_label, f_meta, cell_w)
            ty = _meta_row(draw, x, ty, "Location", p.get("location", ""), f_label, f_meta, cell_w)
            ty += 2
            _cast_chips(draw, x, ty, p.get("characters") or [], f_chip, cell_w)

        pages.append(page)

    pdf_path = os.path.join(board_dir, "storyboard.pdf")
    pages[0].save(
        pdf_path, "PDF", save_all=True, append_images=pages[1:], resolution=150.0
    )
    logger.info("[storyboard %s] wrote PDF (%d pages): %s", job_id, len(pages), pdf_path)
    return pdf_path
