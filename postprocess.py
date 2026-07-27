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

OUTPUT_SIZE = 1080  # upper bound for the square canvas
SUBJECT_FILL = 0.85  # Subject should fill ~85% of the canvas
WHITE_THRESHOLD = 205  # R, G, B all >= this → treat as white

# The canvas ADAPTS to the art instead of forcing every asset to 1080.
#
# A 2×2 sheet only gives each view ~704×384, so stretching that to fill 85% of a
# 1080 canvas meant a 2–3× enlargement — soft and blurry. Capping the
# enlargement fixed the blur but left the subject small in a sea of white.
# Sizing the canvas to the subject gives BOTH: the subject fills the frame AND
# the pixels stay native (scale ≈ 1.0, no invented detail). The canvas is
# clamped so assets never get uselessly tiny or pointlessly upscaled.
MIN_OUTPUT = 576
MAX_UPSCALE = 1.5  # still applies when a view is so small the canvas clamps


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

    Robustness, in two passes:
      1. CORE box — ignore a thin outer border and thin stray rows/cols, so a
         leftover grid-divider line at a panel edge can't inflate the box (which
         used to push subjects off-centre).
      2. GROW back out — extend the core box outward while the neighbouring
         row/col still has real content, stopping at blank space. This is what
         keeps T-pose HANDS and other parts that reach the panel edge: they are
         connected to the subject, whereas an isolated divider line is separated
         from it by white and is never reached.
    """
    cleaned = _clean_white(image.convert("RGB"))
    arr = np.array(cleaned)
    non_white = np.any(arr < 255, axis=2)
    if not np.any(non_white):
        return None, (0, 0)

    h, w = non_white.shape
    mask = non_white.copy()

    # --- Pass 1: robust CORE box (ignores edge lines + thin strays) ---
    b = max(2, int(round(0.02 * min(h, w))))
    mask[:b, :] = False
    mask[-b:, :] = False
    mask[:, :b] = False
    mask[:, -b:] = False

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

    # --- Pass 2: grow outward to recover content clipped by pass 1 ---
    # A neighbouring line counts as subject if it has more than a trace of
    # content; a 1px divider sitting alone in white never connects.
    grow_row_thr = max(2, int(0.01 * w))
    grow_col_thr = max(2, int(0.01 * h))
    full_rows = non_white.sum(axis=1)
    full_cols = non_white.sum(axis=0)

    while top > 0 and full_rows[top - 1] > grow_row_thr:
        top -= 1
    while bottom < h and full_rows[bottom] > grow_row_thr:
        bottom += 1
    while left > 0 and full_cols[left - 1] > grow_col_thr:
        left -= 1
    while right < w and full_cols[right] > grow_col_thr:
        right += 1

    cropped = cleaned.crop((left, top, right, bottom))
    return cropped, cropped.size


def clean_and_normalize_group(views: dict[str, Image.Image]) -> dict[str, Image.Image]:
    """Normalize the 4 views of ONE part with a SHARED scale.

    Scaling each view independently to 85% makes a narrow side-view character
    appear taller than a wide front-view one. Here we compute a single scale
    factor (the most-constrained view, so none overflow) and apply it to all
    views — so the subject is the SAME size across front / left / ¾ / back.

    The canvas is then sized to the art: big enough that the subject fills
    ~85% of it at (near) native resolution. That keeps the framing tight — no
    sea of white — without the 2–3× enlargement that made assets look blurry.
    """
    cropped = {}
    biggest = 0
    for name, img in views.items():
        crop, (cw, ch) = _clean_and_crop(img)
        cropped[name] = (crop, cw, ch)
        if crop is not None and cw > 0 and ch > 0:
            biggest = max(biggest, cw, ch)

    if biggest <= 0:
        return {n: Image.new("RGB", (MIN_OUTPUT, MIN_OUTPUT), (255, 255, 255)) for n in views}

    # Canvas follows the art, clamped to a sane range.
    canvas_size = int(round(biggest / SUBJECT_FILL))
    canvas_size = max(MIN_OUTPUT, min(OUTPUT_SIZE, canvas_size))

    target = canvas_size * SUBJECT_FILL
    scales = [
        min(target / cw, target / ch)
        for (crop, cw, ch) in cropped.values()
        if crop is not None and cw > 0 and ch > 0
    ]
    shared = min(scales) if scales else 1.0
    # Only relevant when the canvas hit MIN_OUTPUT for a very small subject.
    if shared > MAX_UPSCALE:
        logger.info("Capping upscale %.2f → %.2f to preserve sharpness", shared, MAX_UPSCALE)
        shared = MAX_UPSCALE

    out = {}
    for name, (crop, cw, ch) in cropped.items():
        canvas = Image.new("RGB", (canvas_size, canvas_size), (255, 255, 255))
        if crop is not None and cw > 0 and ch > 0:
            new_w = max(1, int(cw * shared))
            new_h = max(1, int(ch * shared))
            resized = crop.resize((new_w, new_h), Image.LANCZOS)
            # Any enlargement softens a low-res view — a light unsharp mask
            # restores crispness.
            if shared > 1.05:
                resized = resized.filter(
                    ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=3)
                )
            canvas.paste(resized, ((canvas_size - new_w) // 2, (canvas_size - new_h) // 2))
        out[name] = canvas
    logger.info(
        "Group-normalized %d views: canvas %dpx, scale %.3f", len(views), canvas_size, shared
    )
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
