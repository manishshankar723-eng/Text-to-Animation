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
# A panel drawn smaller than this has stopped being a storyboard. It is what
# decides 6-up vs 4-up for a page: if the rows on this page can't keep their
# pictures at least this tall once their text is reserved, fewer rows go on it.
MIN_PIC_H = 210

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
# Only reserved on a board that actually HAS dialogue. NB this reserves SPACE in
# the cell; it is not a fixed baseline — the rows below flow up to meet whatever
# is actually there, so a silent shot shows no gap.
DIALOGUE_H = 152
# What Camera / Location / the cast chips need under the dialogue. Kept back so
# a talkative panel can't push them off the bottom of its cell.
META_H = 84


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


def _dialogue_block(draw, x, y, dialogue, f_label, f_name, f_line, max_width, max_h):
    """Draw the spoken lines under the caption; returns the next y.

    The first speaker is prefixed with the word **Dialogue**, in the same
    label style as the Camera and Location rows below — a bare "VIVAN" doesn't
    say what it is, and the rest of the card labels everything it prints.
    Later speakers in the same panel need no repeat of the label.

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
            name_x = text_x
            if drawn == 0:
                draw.text((text_x, y + 2), "Dialogue", font=f_label, fill=MUTED)
                name_x += draw.textlength("Dialogue", font=f_label) + 10
            draw.text((name_x, y), name, font=f_name, fill=DIALOGUE_NAME)
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
    # The word "Dialogue", in the same weight and colour as Camera / Location.
    f_dlabel = f_label

    def _row_text_h(row: list[dict]) -> int:
        """Height to keep back under a row's pictures for its text.

        Reserved PER ROW: a row of silent shots gives the dialogue band back
        instead of printing it blank, which is what left a hole in the middle of
        every card.
        """
        return TEXT_H + (DIALOGUE_H if any(_dialogue_lines(p) for p in row) else 0)

    def _fits(chunk: list[dict], grid_h: int, count: int) -> bool:
        """Would `count` rows of `chunk` still leave a usable picture?"""
        cell = (grid_h - (count - 1) * GUTTER) // count
        for r in range(count):
            row = chunk[r * COLS : (r + 1) * COLS]
            if row and cell - _row_text_h(row) < MIN_PIC_H:
                return False
        return True

    pages: list[Image.Image] = []
    cell_w = (PAGE_W - 2 * MARGIN - (COLS - 1) * GUTTER) // COLS
    start = 0

    while start < len(drawable):
        page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
        draw = ImageDraw.Draw(page)

        top = MARGIN
        if start == 0:
            draw.text((MARGIN, top), title or "Storyboard", font=f_title, fill=INK)
            top += 64
            draw.line([(MARGIN, top), (PAGE_W - MARGIN, top)], fill=LINE, width=2)
            top += 24

        grid_h = PAGE_H - top - MARGIN
        # How many rows fit on THIS page, given what is on it. A page of silent
        # shots prints 6-up as it always did; one carrying an exchange drops to
        # 4-up rather than squeezing the pictures down to nothing for it.
        rows = ROWS
        for candidate in (ROWS, ROWS_DIALOGUE, 1):
            if _fits(drawable[start : start + COLS * candidate], grid_h, candidate):
                rows = candidate
                break

        chunk = drawable[start : start + COLS * rows]
        start += len(chunk)
        cell_h = (grid_h - (rows - 1) * GUTTER) // rows
        # The tallest a picture may be drawn on this page. Each row then takes
        # only what its own text needs, so the slack ends up at the foot of the
        # page rather than inside a card.
        img_box_h = cell_h - TEXT_H

        # Fit every picture FIRST, so each row can be given the height its
        # pictures actually need. A 16:9 panel in a tall cell is width-limited
        # and leaves ~100px of nothing under it otherwise; packing the rows
        # collects that slack at the foot of the page, where it reads as a
        # margin instead of a hole in every card.
        fitted_by_index: dict[int, Image.Image] = {}
        for p in chunk:
            try:
                with Image.open(os.path.join(src_dir, f"panel_{p['index']:02d}.png")) as im:
                    fitted_by_index[p["index"]] = _fit(im.convert("RGB"), cell_w - 4, img_box_h - 4)
            except OSError:
                pass  # drawn as "(panel missing)" at the reserved height below

        row_pic_h, row_text_h = [], []
        for r in range(rows):
            row = chunk[r * COLS : (r + 1) * COLS]
            heights = [
                fitted_by_index[p["index"]].height + 4
                for p in row
                if p["index"] in fitted_by_index
            ]
            row_pic_h.append(max(heights) if heights else img_box_h)
            row_text_h.append(_row_text_h(row))

        for i, p in enumerate(chunk):
            col = i % COLS
            row = i // COLS
            x = MARGIN + col * (cell_w + GUTTER)
            y = top + sum(row_pic_h[r] + row_text_h[r] + GUTTER for r in range(row))

            # The frame is drawn around the picture at the size it ACTUALLY
            # comes out, not around the whole reserved box. Framing the box left
            # grey bars above and below a 16:9 panel and pushed every caption
            # down past them — dead space in the middle of the card.
            fitted = fitted_by_index.get(p["index"])
            drawn_h = row_pic_h[row]
            if fitted is not None:
                fx = x + (cell_w - fitted.width) // 2
                draw.rectangle(
                    [fx - 2, y, fx + fitted.width + 2, y + fitted.height + 4],
                    fill=CELL_BG,
                    outline=LINE,
                )
                page.paste(fitted, (fx, y + 2))
            else:
                draw.rectangle([x, y, x + cell_w, y + drawn_h], fill=CELL_BG, outline=LINE)
                draw.text((x + 12, y + 12), "(panel missing)", font=f_cap, fill=MUTED)

            # Everything below FLOWS: each row is drawn straight after the one
            # above it, so a shot with no dialogue has no gap where dialogue
            # would have gone. Whatever is left over ends up at the foot of the
            # cell, where it reads as ordinary spacing rather than a hole.
            ty = y + drawn_h + 10
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

            # What is SAID in this panel, straight after the caption. A silent
            # shot draws nothing here and its Camera row moves up to meet the
            # description.
            spoken = _dialogue_lines(p)
            if spoken:
                # Room left in the cell, keeping back what Camera / Location and
                # the cast chips still need underneath.
                room = (y + drawn_h + row_text_h[row] - META_H) - (ty + 4)
                ty = _dialogue_block(
                    draw, x, ty + 4, spoken, f_dlabel, f_dname, f_dline, cell_w, room
                )

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
