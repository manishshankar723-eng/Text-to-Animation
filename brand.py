"""THE LOGO IS NEVER DRAWN BY THE MODEL. IT IS PASTED ON AFTERWARDS.

⚠ THIS IS NOT A PROMPTING PROBLEM AND NO PROMPT CAN FIX IT. Reported over five
screenshots of one 28-panel app promo: the "Lickyeat" logo came back different
in every single panel — a fork-and-spoon roundel in shot 5, a stylised "L" in
shot 6, a noodle bowl in shot 11, a grinning mouth in shot 12. Nothing was
wrong with the prompt. Image models reconstruct a mark from a description every
time they draw it, and two reconstructions of the same description are never the
same picture. Asking more firmly produces a different wrong logo.

So the model is taken out of the job entirely:

  1. Where a logo would go, it is told to draw a FLAT SOLID MAGENTA rounded
     square with nothing on it — a placeholder, which is a thing it can draw
     identically every time because there is nothing in it to get wrong.
  2. `stamp()` finds that magenta and pastes the user's real PNG into it.

The result is bit-identical in every panel, because it IS the same file.

⚠ AND IF THERE IS NO UPLOADED LOGO, THE ANSWER IS NO LOGO — not a generated
one. `context()` then tells the model to leave app icons and signage blank. The
user's own note: "logo har jagah change hua, ek jaisa hona chahiye tha agar user
nahi diya hai to". A blank app icon is a design choice; four different logos for
one brand in one film is a broken film.

⚠ GREYSCALE STYLES ARE SKIPPED ENTIRELY, both the marker and the paste. Their
panels are desaturated after generation (see storyboard_pipeline.conform_to_style),
which would turn the marker grey before it could be found and would turn a
colour logo into a grey smudge. A rough-sketch board is a staging thumbnail; it
does not want a brand mark on it.
"""

from __future__ import annotations

import logging
from collections import deque

logger = logging.getLogger(__name__)

# The fields a brand is made of. `logo_ref_id` points at an uploaded PNG in the
# same `_references/{id}/` store character and asset references use.
BRAND_FIELDS = ("name", "logo_ref_id", "primary_color", "secondary_color")

# ---------------------------------------------------------------------------
# The placeholder
# ---------------------------------------------------------------------------
# ⚠ PURE MAGENTA, AND CHOSEN FOR TWO REASONS. It is nearly absent from the
# photographic world — skin, sky, foliage, concrete and cloth do not reach it —
# so a false positive needs a neon sign pointed at the camera. And it is a
# colour the model can NAME, which matters: "#FF00FF" alone gets approximated,
# while "bright magenta" plus the hex gets drawn.
MARKER_RGB = (255, 0, 255)
# How far a pixel may sit from pure magenta and still count. Generous, because
# every style puts its own grade over the top — a cinematic film-still look
# lands the marker nearer (243, 22, 236) than (255, 0, 255).
MARKER_TOLERANCE = 60
# A region smaller than this fraction of the frame is noise (a compression
# artefact, a stray highlight), not a placeholder anybody meant to draw.
MIN_REGION_FRACTION = 0.0006
# ⚠ AND ONE BIGGER THAN THIS MEANS THE MODEL MISUNDERSTOOD and painted a
# magenta wall or a magenta phone. Pasting a logo across a quarter of the frame
# would be far worse than the missing logo, so that panel is left alone.
MAX_REGION_FRACTION = 0.25
# The logo is fitted inside the placeholder with a little air, the way an app
# icon sits inside its rounded square rather than bleeding to the corners.
LOGO_INSET = 0.10


def _clean(value) -> str:
    return str(value or "").strip()


def coerce(raw) -> dict[str, str]:
    """Normalise anything brand-shaped to {field: str} over BRAND_FIELDS."""
    if not isinstance(raw, dict):
        return {}
    return {f: _clean(raw.get(f)) for f in BRAND_FIELDS}


def has_logo(brand) -> bool:
    return bool(coerce(brand).get("logo_ref_id"))


def is_empty(brand) -> bool:
    return not any(coerce(brand).values())


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------
_NO_BRAND_RULE = (
    "BRANDS AND LOGOS — do not invent any brand. Every app icon, sign, "
    "packaging, label, banner and screen in this film must carry NO logo, NO "
    "brand mark, NO wordmark and NO brand name text. Draw those surfaces plain: "
    "a blank rounded app icon in a flat single colour, an unlettered sign, "
    "unbranded packaging. A made-up logo is not a neutral choice — it changes "
    "between shots, and a film whose brand looks different in every scene reads "
    "as broken."
)

# ⚠ THE PLACEHOLDER INSTRUCTION IS DELIBERATELY BLUNT AND REPEATS ITSELF. Half
# measures get a magenta square with a subtle emboss, a gradient or a faint
# glyph "for realism" — all of which survive the paste as a coloured halo around
# the logo. Flat, solid, empty.
_MARKER_RULE = (
    "THE BRAND MARK IS A PLACEHOLDER — DO NOT DRAW THE LOGO. Wherever this "
    "film's app icon, logo or brand mark would appear, draw a PLAIN FLAT SOLID "
    "BRIGHT MAGENTA shape (pure magenta, hex #FF00FF) with ABSOLUTELY NOTHING "
    "on it: no letters, no glyph, no symbol, no gradient, no shading, no "
    "highlight, no shine, no reflection, no border, no drop shadow, no texture. "
    "One flat block of magenta, the shape the icon would be (a rounded square "
    "for an app icon, a rectangle for a sign or a banner), at the size and "
    "position the real mark would sit. The real logo is pasted into this shape "
    "afterwards, so anything drawn inside it becomes damage. Use this magenta "
    "for the brand mark ONLY — never for clothing, walls, lighting, packaging "
    "or anything else in the picture. Everywhere else, no logo and no brand "
    "name text at all."
)


def context(brand) -> str:
    """The brand block for a panel prompt. Never empty.

    ⚠ RETURNS THE NO-BRAND RULE WHEN THERE IS NO BRAND, which is why callers
    append it unconditionally. Skipping it for an unbranded film hands the shot
    back to a model that will happily invent a logo — and invent a different one
    in the next shot, which is the reported bug.
    """
    data = coerce(brand)
    name = data.get("name")
    lines: list[str] = []
    if name:
        # The name is CONTEXT, never letterforms. An image model asked to write
        # a wordmark mis-spells it about as often as it gets it right, and a
        # mis-spelt brand is worse than an absent one.
        lines.append(
            f"THIS FILM IS FOR A BRAND CALLED \"{name}\". That is context for "
            f"what the product is — do NOT letter the name anywhere in the "
            f"picture, on an icon, a screen, a sign or packaging."
        )
    palette = [c for c in (data.get("primary_color"), data.get("secondary_color")) if c]
    if palette:
        lines.append(
            "The brand's colours are "
            + " and ".join(palette)
            + ". Use them for the product's own interface and packaging — "
            "buttons, headers, accents — not for the whole picture's grade."
        )
    lines.append(_MARKER_RULE if data.get("logo_ref_id") else _NO_BRAND_RULE)
    return " ".join(lines)


def wants_marker(brand, style: str = "") -> bool:
    """Should this panel be asked for a magenta placeholder?

    Only when there is a logo to paste into it AND the style keeps its colour.
    A marker on a greyscale board is a grey square nothing can find.
    """
    if not has_logo(brand):
        return False
    from gemini_client import is_greyscale_style

    return not is_greyscale_style(style)


def prompt_context(brand, style: str = "") -> str:
    """`context()`, with the marker suppressed on styles that lose colour.

    The brand NAME and the "invent nothing" rule still apply on a greyscale
    board — it just gets the no-logo wording instead of the placeholder one, so
    the panel comes back with a blank icon rather than a grey square.
    """
    data = coerce(brand)
    if data.get("logo_ref_id") and not wants_marker(brand, style):
        data["logo_ref_id"] = ""
    return context(data)


# ---------------------------------------------------------------------------
# The paste
# ---------------------------------------------------------------------------
def _regions(mask, min_pixels: int) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of the connected magenta blobs in `mask`.

    Flood-filled from the marked pixels only, so the cost is the size of the
    marker rather than the size of the frame. 4-connectivity: two icons that
    touch only at a corner are two icons, not one wide box spanning both.
    """
    import numpy as np

    boxes: list[tuple[int, int, int, int]] = []
    todo = {(int(y), int(x)) for y, x in np.argwhere(mask)}
    while todo:
        seed = todo.pop()
        blob = [seed]
        queue = deque([seed])
        while queue:
            y, x = queue.popleft()
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (ny, nx) in todo:
                    todo.discard((ny, nx))
                    blob.append((ny, nx))
                    queue.append((ny, nx))
        if len(blob) < min_pixels:
            continue
        ys = [p[0] for p in blob]
        xs = [p[1] for p in blob]
        boxes.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    return boxes


def _marker_mask(image):
    """Boolean array of the marker-coloured pixels, or None if there aren't any.

    None also covers "far too many", which is a refusal rather than an absence —
    see the log line.
    """
    import numpy as np

    arr = np.asarray(image.convert("RGB"), dtype=np.int16)
    distance = np.abs(arr - np.array(MARKER_RGB, dtype=np.int16)).sum(axis=2)
    mask = distance <= MARKER_TOLERANCE

    total = image.width * image.height
    marked = int(mask.sum())
    if marked < total * MIN_REGION_FRACTION:
        return None
    if marked > total * MAX_REGION_FRACTION:
        # ⚠ NOT AN ERROR, A REFUSAL. The model painted something large magenta —
        # a wall, a jacket, the whole phone. Pasting a logo across a quarter of
        # the frame is a worse picture than one with no logo in it, and leaving
        # the magenta is worse still, so this panel is simply left as drawn and
        # the miss is logged rather than hidden.
        logger.warning(
            "[brand] %.1f%% of the panel is marker-coloured — too much to be a "
            "placeholder. Leaving this panel alone.", 100.0 * marked / total,
        )
        return None
    return mask


def find_markers(image) -> list[tuple[int, int, int, int]]:
    """Every placeholder box in a finished panel, largest first.

    Returns [] when the model drew none — which is normal and not an error: a
    shot with no phone, no sign and no packaging has nowhere for a logo to go.
    """
    mask = _marker_mask(image)
    if mask is None:
        return []
    total = image.width * image.height
    boxes = _regions(mask, max(1, int(total * MIN_REGION_FRACTION)))
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return boxes


def _tile_colour(logo):
    """What the placeholder becomes behind the logo: white, or near-black.

    ⚠ A WHITE TILE SWALLOWS A WHITE LOGO, and plenty of brands ship a
    white-on-transparent PNG for exactly this kind of use. So the tile is picked
    from the logo's own weight: light artwork gets a dark tile, everything else
    gets white. Judged on the OPAQUE pixels only — averaging in the transparent
    background would call every logo dark.
    """
    import numpy as np

    arr = np.asarray(logo, dtype=np.float32)
    alpha = arr[:, :, 3]
    visible = alpha > 40
    if not visible.any():
        return (255, 255, 255)
    rgb = arr[:, :, :3][visible]
    luma = (0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]).mean()
    return (24, 24, 28) if luma > 170 else (255, 255, 255)


def erase_markers(image, style: str = ""):
    """Repaint any leftover placeholder as a plain neutral tile.

    ⚠ THE SAFETY NET, AND IT IS NOT OPTIONAL. The prompt asks for a magenta
    placeholder on the promise that something replaces it. If that promise
    breaks — the logo file was deleted between the board being set up and the
    panel being drawn, the paste raised, a redraw ran with the brand dropped —
    the panel ships with a BRIGHT MAGENTA SQUARE on the phone, which is a far
    louder bug than the drifting logo this all exists to fix. Repainting it a
    flat neutral grey gives back exactly what an unbranded board would have got:
    a blank app icon.
    """
    import numpy as np
    from PIL import Image as PILImage
    from gemini_client import is_greyscale_style

    if is_greyscale_style(style):
        return image
    mask = _marker_mask(image)
    if mask is None:
        return image
    arr = np.asarray(image.convert("RGB")).copy()
    arr[mask] = (238, 238, 240)
    logger.warning(
        "[brand] a placeholder was left with no logo to put in it — repainted "
        "it blank so the panel does not ship with a magenta square."
    )
    return PILImage.fromarray(arr, "RGB")


def stamp(image, logo_path: str, style: str = ""):
    """Paste the real logo into every placeholder. Returns the panel.

    ⚠ A NO-OP IS THE COMMON CASE AND IS CORRECT. No logo, an unreadable file, a
    greyscale style, or a panel with no placeholder in it all return the picture
    unchanged. This never invents a position: a logo dropped into the corner of
    a shot that had no brand surface is a watermark, and nobody asked for one.

    ⚠ ONLY THE MARKER'S OWN PIXELS ARE REPAINTED, never its bounding box. The
    model draws the placeholder as the shape the icon really is — a rounded
    square, and on a tilted phone a rounded square in PERSPECTIVE — so repainting
    the box would square off those corners and leave four bright nubs sitting
    against the scene. Recolouring the pixels keeps the shape the model drew,
    which is how a flat paste ends up sitting on a tilted screen at all.
    """
    from PIL import Image

    if not logo_path:
        return image
    from gemini_client import is_greyscale_style

    if is_greyscale_style(style):
        return image
    try:
        logo = Image.open(logo_path).convert("RGBA")
    except (OSError, ValueError):
        logger.warning("[brand] logo file %s could not be read — panel unchanged.", logo_path)
        return image

    mask = _marker_mask(image)
    if mask is None:
        return image
    total = image.width * image.height
    boxes = _regions(mask, max(1, int(total * MIN_REGION_FRACTION)))
    if not boxes:
        return image

    from PIL import Image as PILImage
    import numpy as np

    out = image.convert("RGB")
    # Repaint the placeholder in the tile colour, shape and all.
    arr = np.asarray(out).copy()
    arr[mask] = _tile_colour(logo)
    out = PILImage.fromarray(arr, "RGB")

    for x0, y0, x1, y1 in boxes:
        box_w, box_h = x1 - x0, y1 - y0
        inset = int(round(min(box_w, box_h) * LOGO_INSET))
        fit_w, fit_h = max(1, box_w - 2 * inset), max(1, box_h - 2 * inset)
        # Contain, never stretch: a squashed logo is its own kind of wrong.
        scale = min(fit_w / logo.width, fit_h / logo.height)
        new_w = max(1, int(round(logo.width * scale)))
        new_h = max(1, int(round(logo.height * scale)))
        resized = logo.resize((new_w, new_h), Image.LANCZOS)
        out.paste(
            resized,
            (x0 + (box_w - new_w) // 2, y0 + (box_h - new_h) // 2),
            resized,
        )
    logger.info("[brand] stamped the logo into %d placeholder(s).", len(boxes))
    return out
