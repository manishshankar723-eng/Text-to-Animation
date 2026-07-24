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

# Print-friendly palette (white page, dark ink).
INK = (24, 26, 32)
MUTED = (110, 116, 130)
CELL_BG = (238, 240, 244)
LINE = (210, 214, 222)
# Character chips, matching the gold the app uses for pills.
CHIP_BG = (176, 132, 26)
CHIP_INK = (255, 255, 255)

# Height reserved UNDER each panel image for shot label, description, camera,
# location and the cast chips. Grown from the old 96px, which only fitted a
# two-line description.
TEXT_H = 196


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
        panels: list of panel dicts {index, description, camera, location, failed}.
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

    per_page = COLS * ROWS
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
        cell_h = (grid_h - (ROWS - 1) * GUTTER) // ROWS
        img_box_h = cell_h - TEXT_H  # room for shot label, caption, camera,
        #                              location and the cast chips

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

            # Shot label + SCENE n, mirroring the shot card in the app.
            ty = y + img_box_h + 10
            shot_label = f"Shot {p['index'] + 1}"
            draw.text((x, ty), shot_label, font=f_shot, fill=INK)
            scene = p.get("scene_number")
            if scene:
                draw.text(
                    (x + draw.textlength(shot_label, font=f_shot) + 10, ty + 6),
                    f"SCENE {scene}",
                    font=f_scene,
                    fill=MUTED,
                )
            ty += 30

            for line in _wrap(draw, p.get("description", ""), f_cap, cell_w, 2):
                draw.text((x, ty), line, font=f_cap, fill=MUTED)
                ty += 26

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
