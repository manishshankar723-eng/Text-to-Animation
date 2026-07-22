"""
postprocess.py — Clean white backgrounds and normalize framing.

Two steps per image:
1. Clean white: any pixel with R, G, B all >= 205 → force to pure (255,255,255).
   Leaves the dark subject untouched, kills murky/tinted backgrounds.
2. Auto-crop + normalize: find bounding box of non-white content, crop,
   then scale so the subject occupies ~85% of a fresh 1080×1080 white canvas, centered.
"""

import logging
import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

OUTPUT_SIZE = 1080
SUBJECT_FILL = 0.85  # Subject should fill ~85% of the canvas
WHITE_THRESHOLD = 205  # R, G, B all >= this → treat as white


def _clean_white(image: Image.Image) -> Image.Image:
    """
    Force near-white pixels to pure white.
    Any pixel where R >= 205 AND G >= 205 AND B >= 205 becomes (255, 255, 255).
    """
    arr = np.array(image)

    # Create mask: True where ALL of R, G, B are >= threshold
    white_mask = np.all(arr >= WHITE_THRESHOLD, axis=2)

    # Set those pixels to pure white
    arr[white_mask] = [255, 255, 255]

    return Image.fromarray(arr)


def _auto_crop_and_normalize(image: Image.Image) -> Image.Image:
    """
    Find the bounding box of non-white content, crop to it, then scale
    to fill ~85% of a 1080×1080 pure-white canvas, centered.
    """
    arr = np.array(image)

    # Find non-white pixels (any channel < 255)
    non_white = np.any(arr < 255, axis=2)

    if not np.any(non_white):
        # Entire image is white — return blank canvas
        logger.warning("Image is entirely white after cleaning — returning blank canvas")
        return Image.new("RGB", (OUTPUT_SIZE, OUTPUT_SIZE), (255, 255, 255))

    # Get bounding box of non-white content
    rows = np.any(non_white, axis=1)
    cols = np.any(non_white, axis=0)
    top = np.argmax(rows)
    bottom = len(rows) - np.argmax(rows[::-1])
    left = np.argmax(cols)
    right = len(cols) - np.argmax(cols[::-1])

    # Crop to bounding box
    cropped = image.crop((left, top, right, bottom))
    cw, ch = cropped.size

    if cw == 0 or ch == 0:
        logger.warning("Cropped to zero size — returning blank canvas")
        return Image.new("RGB", (OUTPUT_SIZE, OUTPUT_SIZE), (255, 255, 255))

    # Calculate scale to fill SUBJECT_FILL (85%) of the canvas
    target_size = int(OUTPUT_SIZE * SUBJECT_FILL)
    scale = min(target_size / cw, target_size / ch)
    new_w = int(cw * scale)
    new_h = int(ch * scale)

    resized = cropped.resize((new_w, new_h), Image.LANCZOS)

    # Center on fresh white canvas
    canvas = Image.new("RGB", (OUTPUT_SIZE, OUTPUT_SIZE), (255, 255, 255))
    paste_x = (OUTPUT_SIZE - new_w) // 2
    paste_y = (OUTPUT_SIZE - new_h) // 2
    canvas.paste(resized, (paste_x, paste_y))

    return canvas


def _clean_and_crop(image: Image.Image):
    """Clean near-white to white, then crop to the true subject bounding box.

    Returns (cropped_image, (w, h)), or (None, (0, 0)) if the image is empty.

    Robustness: the 2×2 sheet often has faint grid-divider lines; after splitting,
    a sliver survives at a panel's edge. We (a) ignore a thin OUTER BORDER so those
    edge lines don't inflate the box (which was pushing subjects off-center), and
    (b) require a minimum number of non-white pixels per row/column so stray
    1–2px lines are ignored.
    """
    cleaned = _clean_white(image.convert("RGB"))
    arr = np.array(cleaned)
    non_white = np.any(arr < 255, axis=2)
    if not np.any(non_white):
        return None, (0, 0)

    h, w = non_white.shape
    mask = non_white.copy()

    # (a) Ignore a thin outer border — split-grid divider remnants live here.
    b = max(2, int(round(0.02 * min(h, w))))
    mask[:b, :] = False
    mask[-b:, :] = False
    mask[:, :b] = False
    mask[:, -b:] = False

    # (b) Ignore thin stray lines/noise: need a minimum run of content.
    row_counts = mask.sum(axis=1)
    col_counts = mask.sum(axis=0)
    row_thr = max(3, int(0.03 * w))
    col_thr = max(3, int(0.03 * h))
    rows = np.where(row_counts > row_thr)[0]
    cols = np.where(col_counts > col_thr)[0]

    # Fallback to any non-white if the filters removed everything.
    if rows.size == 0 or cols.size == 0:
        rows = np.where(non_white.any(axis=1))[0]
        cols = np.where(non_white.any(axis=0))[0]

    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    cropped = cleaned.crop((left, top, right, bottom))
    return cropped, cropped.size


def clean_and_normalize_group(views: dict[str, Image.Image]) -> dict[str, Image.Image]:
    """Normalize the 4 views of ONE part with a SHARED scale.

    Scaling each view independently to 85% makes a narrow side-view character
    appear taller than a wide front-view one. Here we compute a single scale
    factor (the most-constrained view, so none overflow) and apply it to all
    views — so the subject is the SAME size across front / left / ¾ / back.
    """
    target = int(OUTPUT_SIZE * SUBJECT_FILL)
    cropped = {}
    scales = []
    for name, img in views.items():
        crop, (cw, ch) = _clean_and_crop(img)
        cropped[name] = (crop, cw, ch)
        if crop is not None and cw > 0 and ch > 0:
            scales.append(min(target / cw, target / ch))

    # One shared scale for consistency; fall back to 1.0 if all views empty.
    shared = min(scales) if scales else 1.0

    out = {}
    for name, (crop, cw, ch) in cropped.items():
        canvas = Image.new("RGB", (OUTPUT_SIZE, OUTPUT_SIZE), (255, 255, 255))
        if crop is not None and cw > 0 and ch > 0:
            new_w = max(1, int(cw * shared))
            new_h = max(1, int(ch * shared))
            resized = crop.resize((new_w, new_h), Image.LANCZOS)
            # Upscaling a low-res generated view softens it — a light unsharp
            # mask restores crispness. Only when we actually enlarged it.
            if shared > 1.05:
                resized = resized.filter(
                    ImageFilter.UnsharpMask(radius=1.5, percent=85, threshold=3)
                )
            canvas.paste(resized, ((OUTPUT_SIZE - new_w) // 2, (OUTPUT_SIZE - new_h) // 2))
        out[name] = canvas
    logger.info("Group-normalized %d views with shared scale %.4f", len(views), shared)
    return out


def clean_and_normalize(image: Image.Image) -> Image.Image:
    """
    Full post-processing pipeline for one view image:
    1. Clean near-white pixels to pure white
    2. Auto-crop + normalize to 1080×1080 with ~85% subject fill

    Args:
        image: 1080×1080 PIL Image (one view of one part)

    Returns:
        Cleaned and normalized 1080×1080 PIL Image
    """
    # Ensure RGB (no alpha channel issues)
    image = image.convert("RGB")

    # Step 1: clean white
    cleaned = _clean_white(image)
    logger.debug("Cleaned white pixels (threshold=%d)", WHITE_THRESHOLD)

    # Step 2: auto-crop and normalize
    normalized = _auto_crop_and_normalize(cleaned)
    logger.debug("Auto-cropped and normalized to %dx%d (fill=%.0f%%)",
                 OUTPUT_SIZE, OUTPUT_SIZE, SUBJECT_FILL * 100)

    return normalized
