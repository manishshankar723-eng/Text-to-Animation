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
from PIL import Image

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
    half_w = w // 2
    half_h = h // 2

    views = {}

    for view_name, (col, row) in VIEW_POSITIONS.items():
        # Calculate crop box: (left, upper, right, lower)
        left = col * half_w
        upper = row * half_h
        right = left + half_w
        lower = upper + half_h

        # Crop only — keep the quadrant's true proportions. Force-resizing to a
        # square here would stretch subjects when the grid is widescreen.
        quadrant = sheet.crop((left, upper, right, lower))

        views[view_name] = quadrant
        logger.debug("Split view '%s': cropped (%d,%d,%d,%d) → %dx%d (aspect preserved)",
                      view_name, left, upper, right, lower, half_w, half_h)

    logger.info("Split sheet (%dx%d) into %d views", w, h, len(views))
    return views
