"""
splitter.py — Split a 2×2 turnaround sheet into 4 individual view images.

Layout:
    top-left     = front
    top-right    = left
    bottom-left  = three_quarter
    bottom-right = back

Each quadrant is returned at its NATURAL cropped resolution (aspect ratio
preserved). The downstream post-process step (`postprocess.clean_and_normalize`)
handles the final aspect-preserving fit into a 1080×1080 canvas.

IMPORTANT: do NOT force-resize quadrants to a square here — the source grid may
be widescreen (e.g. 1408×768 → 704×384 cells), and squishing a wide cell into a
square stretches the subject vertically. Keep the crop's true proportions.
"""

import logging
import numpy as np
from PIL import Image

# How far from the exact middle we're willing to look for the real gutter.
GUTTER_SEARCH_FRAC = 0.18
# A pixel this bright in every channel counts as background.
_WHITE = 235
# Figure detection uses a STRICTER darkness cut than _WHITE. Sheets come back on
# an off-white (~246) ground with soft contact shadows around 230–240; treating
# those as content welds neighbouring figures into one blob, and the sheet then
# looks like it has 1–2 figures instead of 4.
_INK = 215
# …and a row/column only counts as occupied if this fraction of it is ink, so a
# shadow smear or a stray speck can't bridge the gap between two figures.
_INK_FRAC = 0.01


def _find_split(counts: np.ndarray, center: int, span: int) -> tuple[int, int]:
    """Locate the real panel boundary near `center`.

    Returns (end_of_first, start_of_second) so any pixels between the two are
    discarded. The model rarely puts its 2×2 grid on the exact midpoint, so
    cutting blindly at w/2 or h/2 either bleeds a sliver of the neighbouring
    panel in or slices a figure in half (heads cut off, legs-only panels).

    Two cases, in priority order:
      1. The model DREW a divider line — a thin run that is dark down almost the
         whole span. Cut it out entirely so no sliver survives into a panel.
      2. Otherwise take the emptiest row/column (the white gutter), resolving
         ties toward the centre so a clean, centred sheet splits as before.
    """
    n = len(counts)
    band = max(1, int(round(n * GUTTER_SEARCH_FRAC)))
    lo = max(1, center - band)
    hi = min(n - 1, center + band + 1)
    if hi <= lo:
        return center, center
    seg = counts[lo:hi]

    # (1) A drawn divider: nearly full-length content, only a few px thick.
    line_idx = np.where(seg >= 0.80 * span)[0]
    if line_idx.size and line_idx.size <= 6:
        start, end = int(line_idx[0]) + lo, int(line_idx[-1]) + lo
        # Only trust it if it really is a thin contiguous run.
        if end - start <= 5:
            return start, end + 1

    # (2) White gutter — emptiest index, nearest the centre.
    candidates = np.where(seg == seg.min())[0] + lo
    idx = int(candidates[np.argmin(np.abs(candidates - center))])
    return idx, idx

logger = logging.getLogger(__name__)

# View names mapped to their grid position
VIEW_POSITIONS = {
    "front":         (0, 0),  # top-left
    "left":          (1, 0),  # top-right
    "three_quarter": (0, 1),  # bottom-left
    "back":          (1, 1),  # bottom-right
}
# The order the prompt asks the model to draw the views in. Used when the sheet
# ISN'T a 2×2 grid and the figures have to be read off in layout order instead.
VIEW_ORDER = ("front", "left", "three_quarter", "back")

# A detected figure must be at least this fraction of the sheet to count — keeps
# a stray speck or a soft ground shadow from being mistaken for a view. Small
# enough that a genuinely small far view still qualifies.
MIN_CELL_FRAC = 0.004


def _runs(flags: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """[(start, end)] spans of True in `flags`, merging spans closer than min_gap.

    The gap merge matters: a figure's arm can be separated from its body by a
    column of background (T-pose hands vs. the gap under the armpit), and each
    figure must stay ONE run rather than fragmenting into three.
    """
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return []
    spans = []
    start = prev = idx[0]
    for i in idx[1:]:
        if i - prev > min_gap:
            spans.append((int(start), int(prev) + 1))
            start = i
        prev = i
    spans.append((int(start), int(prev) + 1))
    return spans


def _occupied(mask: np.ndarray, axis: int) -> np.ndarray:
    """Which rows/columns hold real ink — a COUNT test, not 'any dark pixel'."""
    counts = mask.sum(axis=axis)
    return counts >= max(1, int(round(mask.shape[axis] * _INK_FRAC)))


def _detect_cells(content: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Find the drawn figures as (left, upper, right, lower) boxes.

    Columns of background split the sheet into vertical bands; each band is then
    split by rows of background. This reads the layout the model ACTUALLY drew
    instead of assuming a 2×2 grid — the model often lays four standing figures
    out in a row, or two tall ones plus two small stacked ones, and a blind
    midpoint cut then slices those tall figures at the waist.
    """
    h, w = content.shape
    min_area = MIN_CELL_FRAC * w * h
    boxes: list[tuple[int, int, int, int]] = []

    # Column gaps are the wide ones between figures; row gaps can be tight — two
    # stacked views may be only ~20px apart, and a generous row threshold welds
    # them into a single cell (which is exactly how the four-view sheet turned
    # into two half-figures). Figures are continuous vertically, so a small row
    # gap is safe.
    for x0, x1 in _runs(_occupied(content, 0), min_gap=max(4, int(w * 0.015))):
        band = content[:, x0:x1]
        for y0, y1 in _runs(_occupied(band, 1), min_gap=max(4, int(h * 0.01))):
            # Tighten the box horizontally to this row-span's own content, so a
            # narrow figure doesn't inherit a wider neighbour's bounds.
            cols = np.flatnonzero(band[y0:y1].any(axis=0))
            if cols.size == 0:
                continue
            left, right = x0 + int(cols[0]), x0 + int(cols[-1]) + 1
            if (right - left) * (y1 - y0) >= min_area:
                boxes.append((left, y0, right, y1))
    return boxes


def _order_cells(boxes: list[tuple[int, int, int, int]], w: int, h: int) -> list[tuple]:
    """Put four detected cells into front, left, three_quarter, back order.

    A true 2×2 grid is read ROW-major, matching VIEW_POSITIONS. Any other layout
    (a single row, or columns with stacked views) is read COLUMN-major —
    left-to-right, top-to-bottom within a column — which is the order the prompt
    asks for and the order these sheets are drawn in.
    """
    cx = [(b[0] + b[2]) / 2 for b in boxes]
    cy = [(b[1] + b[3]) / 2 for b in boxes]
    two_cols = max(cx) - min(cx) > w * 0.15 and len({c > (min(cx) + max(cx)) / 2 for c in cx}) == 2
    two_rows = max(cy) - min(cy) > h * 0.15 and len({c > (min(cy) + max(cy)) / 2 for c in cy}) == 2
    if two_cols and two_rows:
        mid_x, mid_y = (min(cx) + max(cx)) / 2, (min(cy) + max(cy)) / 2
        quads = {(cx[i] > mid_x, cy[i] > mid_y): boxes[i] for i in range(len(boxes))}
        if len(quads) == 4:  # one box per quadrant → a real 2×2 grid
            logger.info("Sheet layout: 2×2 grid (row-major).")
            return [quads[(False, False)], quads[(True, False)],
                    quads[(False, True)], quads[(True, True)]]
    logger.info("Sheet layout: not a 2×2 grid — reading cells column-major.")
    return [b for _, b in sorted(zip(zip(cx, cy), boxes), key=lambda p: (p[0][0], p[0][1]))]


def split_sheet(sheet: Image.Image) -> dict[str, Image.Image]:
    """
    Cut a 2×2 turnaround sheet into 4 separate view images.

    Args:
        sheet: PIL Image containing the 2×2 grid.

    Returns:
        Dict mapping view name ("front", "left", "three_quarter", "back")
        to a PIL Image cropped at its natural resolution (aspect ratio preserved;
        NOT resized to a square — post-process handles final framing).
    """
    w, h = sheet.size
    arr = np.array(sheet.convert("RGB"))
    content = np.any(arr < _WHITE, axis=2)

    # FIRST: read the layout the model actually drew. Only when that doesn't
    # yield exactly four figures do we fall back to cutting a 2×2 grid.
    cells = _detect_cells(np.any(arr < _INK, axis=2))
    if len(cells) == 4:
        ordered = _order_cells(cells, w, h)
        views = {name: sheet.crop(box) for name, box in zip(VIEW_ORDER, ordered)}
        for name, box in zip(VIEW_ORDER, ordered):
            logger.info("Split view '%s': %s → %dx%d", name, box, box[2] - box[0], box[3] - box[1])
        return views

    logger.warning(
        "Detected %d figures on the sheet, expected 4 — falling back to a 2×2 grid cut.",
        len(cells),
    )

    # Find where the panels ACTUALLY divide rather than assuming the midpoint.
    x_end, x_start = _find_split(content.sum(axis=0), w // 2, h)
    y_end, y_start = _find_split(content.sum(axis=1), h // 2, w)
    logger.info(
        "Split points: x=%d..%d (mid %d), y=%d..%d (mid %d)",
        x_end, x_start, w // 2, y_end, y_start, h // 2,
    )

    bounds_x = [(0, x_end), (x_start, w)]
    bounds_y = [(0, y_end), (y_start, h)]

    views = {}

    for view_name, (col, row) in VIEW_POSITIONS.items():
        left, right = bounds_x[col]
        upper, lower = bounds_y[row]

        # Crop only — keep the quadrant's true proportions. Force-resizing to a
        # square here would stretch subjects when the grid is widescreen.
        quadrant = sheet.crop((left, upper, right, lower))

        views[view_name] = quadrant
        logger.debug("Split view '%s': cropped (%d,%d,%d,%d) → %dx%d (aspect preserved)",
                      view_name, left, upper, right, lower, right - left, lower - upper)

    logger.info("Split sheet (%dx%d) into %d views", w, h, len(views))
    return views
