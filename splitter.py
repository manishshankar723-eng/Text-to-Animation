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

    # Find where the panels ACTUALLY divide rather than assuming the midpoint.
    arr = np.array(sheet.convert("RGB"))
    content = np.any(arr < _WHITE, axis=2)
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
