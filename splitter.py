"""
splitter.py — Split a 2×2 turnaround sheet into 4 individual view images.

Layout:
    top-left     = front
    top-right    = left
    bottom-left  = three_quarter
    bottom-right = back

Each quadrant is resized to 1080×1080.
"""

import logging
from PIL import Image

logger = logging.getLogger(__name__)

OUTPUT_SIZE = (1080, 1080)

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
        to a 1080×1080 PIL Image.
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

        quadrant = sheet.crop((left, upper, right, lower))
        quadrant = quadrant.resize(OUTPUT_SIZE, Image.LANCZOS)

        views[view_name] = quadrant
        logger.debug("Split view '%s': cropped (%d,%d,%d,%d) → 1080×1080",
                      view_name, left, upper, right, lower)

    logger.info("Split sheet (%dx%d) into %d views", w, h, len(views))
    return views
