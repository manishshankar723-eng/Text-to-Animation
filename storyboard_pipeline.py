"""
storyboard_pipeline.py — Script → Storyboard, Stage D: generate panels.

Given a reviewed shot list + a style + an aspect ratio, generate ONE image per
shot, centre-crop it to the exact aspect ratio, save it locally, and report
progress after each panel (so the client's board fills in one-by-one).

Synchronous + I/O-bound (Gemini image calls) — run it in the worker thread pool,
never on the FastAPI event loop. Mirrors pipeline.py's progress-callback shape.

Panels are rendered CONCURRENTLY (STORYBOARD_PANEL_CONCURRENCY). Real API pressure
is bounded by the shared throttle in gemini_client, so this pool only controls how
many panels are in flight locally.
"""

import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from PIL import Image

logger = logging.getLogger(__name__)

# How many panels to draw at once. The gemini_client throttle
# (IMAGE_MAX_CONCURRENCY / IMAGE_RPM) is the real ceiling.
PANEL_CONCURRENCY = max(1, int(os.environ.get("STORYBOARD_PANEL_CONCURRENCY", "2")))

# Stop / cancel. The registry is shared with the character pipeline (cancel.py);
# re-exported here because callers already import these names from this module.
# For a board: every panel not yet started is skipped, and the 1–2 already
# talking to the image API finish, because an in-flight call can't be un-sent.
from cancel import clear_cancel, is_cancelled, request_cancel  # noqa: E402,F401


# How much clear margin a normalised panel keeps around its drawing, as a
# fraction of the frame's short side. Small but non-zero: a board reads better
# when the art isn't flush to the edge, and it hides a stray stroke at the rim.
PANEL_MARGIN = 0.02
# How far a pixel may differ from the paper colour and still count as blank.
# Generous enough for the faint grain in a sketch, tight enough that pale
# pencil work still registers as content.
_BLANK_TOLERANCE = 30


# How much the outer ring may vary and still count as blank paper rather than
# artwork. A printed sketch's margin is near-uniform; a picture that runs to the
# edge is not.
_BORDER_UNIFORMITY = 14
# Never trim away more than this share of a side. A safety rail: if the maths
# ever wants to cut a third of the picture off, something has been misread.
_MAX_TRIM = 0.35


def _paper_colour(image: "Image.Image") -> tuple | None:
    """The blank border's colour, or None when the artwork reaches the edges.

    Sampling the corners alone is not enough — on a panel drawn edge to edge the
    corners ARE the picture, and treating them as "paper" makes everything that
    differs from them look like content, cropping the frame down to whatever
    happens to be brightest. (That is a real bug this returns None to avoid.)

    So the whole outer ring is examined: only if it is near-uniform is it a
    margin, and then its median colour is the paper.
    """
    w, h = image.size
    k = max(2, min(w, h) // 60)

    # The outer ring, as four thin strips.
    strips = [
        image.crop((0, 0, w, k)),
        image.crop((0, h - k, w, h)),
        image.crop((0, 0, k, h)),
        image.crop((w - k, 0, w, h)),
    ]
    pixels: list[tuple] = []
    for s in strips:
        # Downsample: enough samples to judge uniformity, few enough to be quick.
        small = s.resize((min(48, max(1, s.width)), min(48, max(1, s.height))))
        pixels.extend(small.convert("RGB").getdata())
    if not pixels:
        return None

    lums = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    mean = sum(lums) / len(lums)
    var = sum((x - mean) ** 2 for x in lums) / len(lums)
    if var**0.5 > _BORDER_UNIFORMITY:
        return None  # the picture runs to the edge — nothing to trim

    return tuple(
        sorted(p[i] for p in pixels)[len(pixels) // 2] for i in range(3)
    )


# --- The drawn frame ------------------------------------------------------
#
# Asked for a "storyboard panel" the model very often draws the BOX as well as
# the picture: a sketchy rectangle just inside the edge, with white paper around
# it. Every one is drawn freehand, so no two match — different thickness,
# different inset, different wobble — and a board of them looks like a pile of
# mismatched Polaroids. Reported twice, the second time as "i decide remove frame
# in image … i not need frame line in storyboard panel image and key poses".
#
# The prompt asks for no border (see gemini_client) and is ignored often enough
# to matter, exactly like the greyscale styles, so it is ALSO enforced here.
#
# THE SIGNAL: a drawn frame line sits at a near-constant depth from its edge for
# the whole length of that edge. Picture content does not. So walk in from every
# column and record the depth of the first ink pixel: a border makes those depths
# a tight cluster (measured: 7px of spread on a 768px side), a picture scatters
# them across the frame (measured: 319px on the same image).
_FRAME_INK = 190          # a border line is drawn dark; a grey wash is not
_FRAME_BAND = 0.12        # how far in from an edge a frame line may sit
_FRAME_COVERAGE = 0.95    # the line runs the whole length of its side
_FRAME_SPREAD = 0.020     # "tight cluster", as a fraction of the side
_FRAME_PAPER = 200        # outside the line must be blank paper, not artwork


def _frame_depth(ink, lum, band: int, side: int) -> int | None:
    """How deep to cut from one edge to lose its drawn line, or None.

    Both arrays arrive oriented edge-first, so this is written once and used for
    all four sides.
    """
    lo, hi = int(ink.shape[1] * 0.12), int(ink.shape[1] * 0.88)
    core = ink[:band, lo:hi]
    has = core.any(axis=0)
    if has.mean() < _FRAME_COVERAGE:
        return None  # not present along the whole side — not a frame line
    first = core.argmax(axis=0)[has]
    d10, d90 = int(np.percentile(first, 10)), int(np.percentile(first, 90))
    if (d90 - d10) > max(3, _FRAME_SPREAD * side):
        return None  # scattered depths are PICTURE, not a ruled line
    if d10 < 2:
        return None  # ink hard against the edge is full bleed — what we want
    # A frame has blank paper OUTSIDE it. Without this any dark shape lying
    # along an edge — a roofline against sky, a wall, a shadow — reads as one.
    if lum[:d10, lo:hi].mean() < _FRAME_PAPER:
        return None
    # Past the line by a hair, so no thickness, fringe or anti-aliasing
    # survives. The line's own thickness can't be measured by walking inward —
    # artwork frequently touches the frame — so it is covered by a pad instead.
    #
    # NOT capped at `band`. The band says where a line may be FOUND; capping the
    # cut there left the inner half of a thick line drawn deep in the band still
    # on the picture. The rail that matters is the absolute one below.
    return min(d90 + max(3, int(0.018 * side)), int(side * 0.20))


def strip_drawn_border(image: "Image.Image") -> "Image.Image":
    """Crop away a frame the model drew around the picture. A no-op if there
    isn't one.

    Each edge is judged on its own and only the ones carrying a line are cut, so
    a picture framed on three sides loses three sides. Fewer than three and
    nothing is done: one or two matching edges are far more likely to be a
    composition than a frame.
    """
    rgb = image.convert("RGB")
    lum = np.asarray(rgb.convert("L"), dtype=np.float32)
    h, w = lum.shape
    ink = lum < _FRAME_INK
    by, bx = max(4, int(h * _FRAME_BAND)), max(4, int(w * _FRAME_BAND))

    top = _frame_depth(ink, lum, by, h)
    bottom = _frame_depth(ink[::-1], lum[::-1], by, h)
    left = _frame_depth(ink.T, lum.T, bx, w)
    right = _frame_depth(ink.T[::-1], lum.T[::-1], bx, w)

    sides = [top, bottom, left, right]
    if sum(s is not None for s in sides) < 3:
        return image

    box = (left or 0, top or 0, w - (right or 0), h - (bottom or 0))
    if box[2] - box[0] < w * 0.5 or box[3] - box[1] < h * 0.5:
        logger.info("[storyboard] drawn-border crop looked wrong (%s) — skipped.", box)
        return image
    logger.info(
        "[storyboard] cropped a drawn frame: t=%s b=%s l=%s r=%s of %dx%d",
        top, bottom, left, right, w, h,
    )
    return rgb.crop(box)


def normalise_panel(image: "Image.Image", aspect_ratio: str) -> "Image.Image":
    """Make every panel fill its frame by the same amount.

    The image model is inconsistent about this: asked for one storyboard panel it
    sometimes draws edge to edge and sometimes drops a small sketch in the middle
    of a big blank page. Measured across one real board, the drawing occupied
    anywhere from 78% to 100% of the frame — which is why a finished board looked
    like a jumble of different-sized pictures.

    So the blank margin is measured and removed, then the content box is grown
    back out to the target aspect using REAL pixels wherever the original still
    has them (never invented bars), leaving a uniform `PANEL_MARGIN`.

    A panel that is already full-bleed, or one with a dark background where there
    is no blank margin to find, passes through unchanged.

    A DRAWN frame is removed first (`strip_drawn_border`) — it is content as far
    as the blank-margin maths is concerned, so leaving it would peg the content
    box to the border and defeat everything below. Doing it here means panels,
    key poses and every redraw are covered by the one call they already make.
    """
    # AFTER the aspect check, not before: an unusable aspect means "hand the
    # image back exactly as it came", and cropping first would quietly break
    # that contract (it did — two checks in tests/panel_normalise_check.py).
    try:
        w_ratio, h_ratio = (float(x) for x in aspect_ratio.split(":"))
        if w_ratio <= 0 or h_ratio <= 0:
            return image
    except (ValueError, AttributeError):
        return image

    # THE PANEL COMES BACK THE SIZE IT WENT IN, on every path.
    #
    # That is the contract a board depends on, but only the main path used to
    # keep it — the half-dozen early returns below handed back whatever
    # `_crop_to_aspect` produced. Harmless while nothing before them changed the
    # size; not harmless now that `strip_drawn_border` can take 40% off a framed
    # panel, which would come back visibly smaller than its neighbours. One exit,
    # one resize. Scaling the cropped picture back up to fill the frame is also
    # exactly the "use full image size" the report asked for.
    out_w, out_h = image.size
    fitted = _fit_panel(strip_drawn_border(image), aspect_ratio, w_ratio, h_ratio)
    if fitted.size != (out_w, out_h):
        fitted = fitted.resize((out_w, out_h), Image.LANCZOS)
    return fitted


def _fit_panel(image, aspect_ratio: str, w_ratio: float, h_ratio: float):
    """normalise_panel's body: trim the blank margin and fit to the aspect.

    Returns whatever size the maths lands on; the caller restores the frame size.
    """
    from PIL import ImageChops

    rgb = image.convert("RGB")
    w, h = rgb.size
    paper = _paper_colour(rgb)
    if paper is None:
        # Artwork already reaches the edges — this is what we want, leave it.
        return _crop_to_aspect(image, aspect_ratio)

    bg = Image.new("RGB", rgb.size, paper)
    mask = ImageChops.difference(rgb, bg).convert("L").point(
        lambda p: 255 if p > _BLANK_TOLERANCE else 0
    )
    box = mask.getbbox()
    if not box:
        return _crop_to_aspect(image, aspect_ratio)  # blank panel — nothing to find

    left, top, right, bottom = box
    content_w, content_h = right - left, bottom - top
    if content_w <= 0 or content_h <= 0:
        return _crop_to_aspect(image, aspect_ratio)

    # Already filling the frame? Leave it alone rather than shave a few pixels.
    if content_w >= w * 0.97 and content_h >= h * 0.97:
        return _crop_to_aspect(image, aspect_ratio)

    # Safety rail: a content box this small means the blank-margin read went
    # wrong (a pale panel, a vignette). Trimming to it would destroy the panel,
    # so leave the image as the model drew it.
    if content_w < w * (1 - _MAX_TRIM) or content_h < h * (1 - _MAX_TRIM):
        logger.info(
            "[storyboard] panel content box %dx%d of %dx%d — too small to be a "
            "margin, leaving the panel untrimmed.", content_w, content_h, w, h,
        )
        return _crop_to_aspect(image, aspect_ratio)

    target = w_ratio / h_ratio
    margin = PANEL_MARGIN * min(content_w, content_h)
    want_w = content_w + 2 * margin
    want_h = content_h + 2 * margin
    # Grow the short dimension until the box matches the target aspect.
    if want_w / want_h < target:
        want_w = want_h * target
    else:
        want_h = want_w / target

    cx, cy = (left + right) / 2, (top + bottom) / 2
    x0, x1 = cx - want_w / 2, cx + want_w / 2
    y0, y1 = cy - want_h / 2, cy + want_h / 2

    # Slide back inside the source before resorting to padding, so we keep real
    # pixels instead of manufacturing bars at the edge.
    if x0 < 0:
        x1, x0 = x1 - x0, 0
    if x1 > w:
        x0, x1 = x0 - (x1 - w), w
    if y0 < 0:
        y1, y0 = y1 - y0, 0
    if y1 > h:
        y0, y1 = y0 - (y1 - h), h

    x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
    x1, y1 = min(w, int(round(x1))), min(h, int(round(y1)))
    cropped = rgb.crop((x0, y0, x1, y1))

    # If the box still doesn't match the target (the source ran out), pad with
    # the paper colour so the panel is the right shape without losing content.
    cw, ch = cropped.size
    if abs(cw / ch - target) > 0.01:
        if cw / ch < target:
            pad_w = int(round(ch * target))
            canvas = Image.new("RGB", (pad_w, ch), paper)
            canvas.paste(cropped, ((pad_w - cw) // 2, 0))
        else:
            pad_h = int(round(cw / target))
            canvas = Image.new("RGB", (cw, pad_h), paper)
            canvas.paste(cropped, (0, (pad_h - ch) // 2))
        cropped = canvas

    return cropped


# ---------------------------------------------------------------------------
# Tonal conformance — the part that is NOT left to the prompt
#
# Every style prompt states its palette, and the image model honours it most of
# the time. Most of the time is not good enough for a film: on one real
# rough-sketch board ("greyscale only, absolutely no colour" in the prompt) two
# panels in fifteen came back as full-colour illustrations, and one pose in an
# eight-pose flipbook did too. Flipping through that is the "colours change"
# the user reported, and it is not something more prompt wording fixes — the
# model either complies or it doesn't.
#
# Measuring colour and removing it is deterministic, free, and always correct,
# so that is what happens here. The prompt still asks; this makes sure.
# ---------------------------------------------------------------------------

# Percentage of pixels that are meaningfully coloured, above which a picture
# counts as coloured.
#
# The FRACTION of coloured pixels, not the mean spread — which was the first
# attempt and was too blunt. A drawing whose colour is one bright accent (a
# glowing object in an otherwise grey frame) averages ~3, indistinguishable from
# grey art's ~0–4, so a whole sequence read as "grey" and the one pose that came
# back with the accent MISSING went unnoticed. By fraction the same set is
# unambiguous. Measured across two real boards:
#     greyscale art .............. 0.00 – 0.69 %
#     grey art with a colour accent 1.5  – 4.4  %
#     fully coloured panels ....... 11.9 – 20.1 %
COLOUR_FRACTION_THRESHOLD = 1.0


def colour_fraction(image: "Image.Image") -> float:
    """Percentage of pixels with a visible hue (max(RGB) − min(RGB) > 25).

    Sampled small — this is a yes/no question, not a measurement that needs
    every pixel.
    """
    from PIL import ImageChops

    r, g, b = image.convert("RGB").resize((240, 135)).split()
    # max(RGB) - min(RGB) per pixel, via lighter/darker composites: exact, and
    # far faster than walking the pixels.
    hi = ImageChops.lighter(ImageChops.lighter(r, g), b)
    lo = ImageChops.darker(ImageChops.darker(r, g), b)
    histogram = ImageChops.difference(hi, lo).histogram()
    total = sum(histogram) or 1
    return sum(histogram[26:]) / total * 100


def is_coloured(image: "Image.Image") -> bool:
    return colour_fraction(image) > COLOUR_FRACTION_THRESHOLD


def conform_to_style(image: "Image.Image", style: str) -> "Image.Image":
    """Strip colour from a picture drawn in a style that forbids it.

    A no-op for colour styles and for pictures that are already grey.
    """
    from gemini_client import is_greyscale_style

    if not is_greyscale_style(style) or not is_coloured(image):
        return image
    logger.info(
        "[storyboard] style '%s' is greyscale but the model returned colour "
        "(%.1f%% of pixels coloured) — desaturating.", style, colour_fraction(image),
    )
    return image.convert("L").convert("RGB")


def conform_to_reference(image: "Image.Image", reference: "Image.Image") -> "Image.Image":
    """Make a key pose match its source panel's palette.

    A pose only has to agree with the ONE picture it is a variation of, so the
    panel is the authority rather than the style name — that also covers
    freeform "Add Your Own Style" boards, where nothing knows whether colour is
    expected. Only the grey direction is enforceable: colour can be removed, it
    cannot be invented, so a grey pose under a coloured panel is reported by the
    caller instead.
    """
    if is_coloured(image) and not is_coloured(reference):
        logger.info(
            "[storyboard] key pose came back coloured under a greyscale panel "
            "(%.1f%% of pixels coloured) — desaturating to match.", colour_fraction(image),
        )
        return image.convert("L").convert("RGB")
    return image


def lost_the_colour(image: "Image.Image", reference: "Image.Image") -> bool:
    """True when a pose came back GREY under a coloured panel.

    The failure conform_to_reference cannot repair, because colour can be
    removed from a picture but not invented. Seen in a real sixteen-pose run: a
    shot built around a glowing blue object where pose 11 alone came back
    entirely greyscale, so the flipbook lost its subject for a frame. Worth one
    more attempt — it is a one-off lapse, not something about that pose.

    Judged against the PANEL, so it costs nothing on a genuinely grey board.
    """
    return is_coloured(reference) and not is_coloured(image)


# NO AUTOMATIC "DID IT MOVE?" CHECK — deliberately.
#
# The obvious one is to threshold both drawings to ink and diff them, and it was
# written, run against the real eight-pose strip where the head provably does
# NOT move (its ink centroid holds to within 3px of 1365), and thrown away: it
# reported 75–100% "change" on every pair. The frames are re-SHADED between
# renders, so a darker pass pushes far more pixels over any fixed ink threshold,
# and a rank-based threshold just picks different structures instead. Forty per
# cent of a static wooden crate "changed" by that measure.
#
# Separating "re-shaded" from "re-posed" is a real vision problem, not a
# heuristic, and a motion gate that cries wolf would be worse than none — it
# would spend money retrying frames that were fine. Colour conformance above IS
# reliably measurable, so that one is enforced; movement is fixed at the prompt,
# where the actual bug was. If this is revisited, judge any candidate metric
# against output/_storyboards/284759…/seq/panel_01, which is a known-bad set.


def _crop_to_aspect(image: "Image.Image", aspect_ratio: str) -> "Image.Image":
    """Centre-crop an image to the given W:H ratio (e.g. '16:9')."""
    try:
        w_ratio, h_ratio = (float(x) for x in aspect_ratio.split(":"))
        if w_ratio <= 0 or h_ratio <= 0:
            return image
    except (ValueError, AttributeError):
        return image

    target = w_ratio / h_ratio
    w, h = image.size
    current = w / h
    if abs(current - target) < 0.01:
        return image

    if current > target:
        # Too wide → trim the sides.
        new_w = int(round(h * target))
        left = (w - new_w) // 2
        return image.crop((left, 0, left + new_w, h))
    # Too tall → trim top/bottom.
    new_h = int(round(w / target))
    top = (h - new_h) // 2
    return image.crop((0, top, w, top + new_h))


def _dialogue_of(shot: dict) -> list[dict]:
    """A shot's spoken lines as plain [{character, line}] dicts.

    Shots arrive either as Pydantic-dumped dicts (the API) or as raw dicts (a
    saved board being re-styled), so entries may be dicts or objects. Anything
    without a `line` is dropped, which is what keeps a silent shot's list EMPTY
    — every consumer treats "no dialogue" as "draw no dialogue block".
    """
    out: list[dict] = []
    for item in shot.get("dialogue") or []:
        if not isinstance(item, dict):
            item = getattr(item, "__dict__", {}) or {}
        line = str(item.get("line", "") or "").strip()
        if line:
            out.append({"character": str(item.get("character", "") or "").strip(), "line": line})
    return out


def _load_refs(ref_paths: dict | None, kind: str = "reference") -> dict:
    """Load reference images once, keyed by lowercased name.

    Used for both character refs and asset (prop/background) refs — the loading
    is identical, only the log label differs.
    """
    refs: dict[str, "Image.Image"] = {}
    for name, path in (ref_paths or {}).items():
        try:
            refs[name.strip().lower()] = Image.open(path).convert("RGB")
        except (OSError, AttributeError):
            logger.warning("[storyboard] couldn't load %s ref for %s: %s", kind, name, path)
    return refs


def _load_character_refs(character_ref_paths: dict | None) -> dict:
    """Load character reference images once, keyed by lowercased name."""
    return _load_refs(character_ref_paths, "character")


def _variant_dir(board_dir: str, variant: int) -> str:
    """Panel folder for a style variant (variant 0 = the board root, for compat)."""
    return board_dir if not variant else os.path.join(board_dir, f"v{variant}")


def versions_dir(board_dir: str, variant: int, i: int) -> str:
    """Where EVERY render of one panel is kept, newest last.

    Re-drawing a shot used to overwrite it, so the picture you had a moment ago
    was gone for good — and with an image model you often want the one before
    (reported). Every render is archived here as `v000.png`, `v001.png`, … and
    `panel_NN.png` stays a COPY of whichever version is active. Keeping that
    file exactly where it always was means the PDF, the ZIP, the key-pose
    generator and the animatic need no changes at all: they read the active
    picture without knowing versions exist.
    """
    return os.path.join(_variant_dir(board_dir, variant), "versions", f"panel_{i:02d}")


def version_path(board_dir: str, variant: int, i: int, n: int) -> str:
    return os.path.join(versions_dir(board_dir, variant, i), f"v{int(n):03d}.png")


def count_versions(board_dir: str, variant: int, i: int) -> int:
    """How many renders of this panel exist on disk."""
    folder = versions_dir(board_dir, variant, i)
    if not os.path.isdir(folder):
        return 0
    n = 0
    while os.path.isfile(version_path(board_dir, variant, i, n)):
        n += 1
    return n


def adopt_existing_as_version(board_dir: str, variant: int, i: int) -> None:
    """Archive the panel picture that is ALREADY on disk as version 0.

    Every board drawn before versions existed has a `panel_NN.png` and no
    archive. Without this the first redraw of such a panel would overwrite that
    picture and archive only the NEW one — leaving a single version, no arrows,
    and the original gone. That was reported: "i generate new shot panel image
    but i not see my older image".

    Idempotent: once anything is archived this does nothing, so it is safe to
    call before every save.
    """
    if count_versions(board_dir, variant, i):
        return
    existing = os.path.join(_variant_dir(board_dir, variant), f"panel_{i:02d}.png")
    if not os.path.isfile(existing):
        return
    os.makedirs(versions_dir(board_dir, variant, i), exist_ok=True)
    shutil.copyfile(existing, version_path(board_dir, variant, i, 0))


def save_panel_version(image, board_dir: str, variant: int, i: int) -> int:
    """Archive `image` as this panel's next version AND make it the active one.

    Returns the new version's index. The caller does not need to write
    `panel_NN.png` itself — that copy is made here, so the two can never
    disagree about what the current picture is.
    """
    # Rescue a pre-versions picture before this render lands on top of it.
    adopt_existing_as_version(board_dir, variant, i)
    folder = versions_dir(board_dir, variant, i)
    os.makedirs(folder, exist_ok=True)
    n = count_versions(board_dir, variant, i)
    image.save(version_path(board_dir, variant, i, n), "PNG")
    image.save(os.path.join(_variant_dir(board_dir, variant), f"panel_{i:02d}.png"), "PNG")
    return n


def activate_panel_version(board_dir: str, variant: int, i: int, n: int) -> bool:
    """Make version `n` the panel's current picture. False if it doesn't exist."""
    src = version_path(board_dir, variant, i, n)
    if not os.path.isfile(src):
        return False
    shutil.copyfile(src, os.path.join(_variant_dir(board_dir, variant), f"panel_{i:02d}.png"))
    return True


def _panel_url(job_id: str, i: int, variant: int) -> str:
    """Serve URL for a panel, tagged with its variant so the client caches per-style."""
    suffix = f"?v={variant}" if variant else ""
    return f"/storyboards/{job_id}/panel/{i}{suffix}"


def _load_composition_ref(composition_ref_dir: str | None, i: int) -> "Image.Image | None":
    """Load an existing panel to feed as a composition reference when re-styling."""
    if not composition_ref_dir:
        return None
    path = os.path.join(composition_ref_dir, f"panel_{i:02d}.png")
    try:
        return Image.open(path).convert("RGB")
    except (OSError, AttributeError):
        return None


def _gather_refs(names, ref_map: dict, cap: int) -> list:
    """Collect up to `cap` reference images for the given names.

    Deduped by name (not by image value — two visually similar assets are still
    distinct references). Name matching is the shared, alias-tolerant one from
    gemini_client: a shot that says "Lead Thug" still finds the reference filed
    under "Thug Leader" instead of silently going unreferenced.
    """
    from gemini_client import resolve_name

    out = []
    seen: set[str] = set()
    for name in names or []:
        key = str(name).strip().lower()
        if not key or key in seen:
            continue
        ref = resolve_name(name, ref_map)
        if ref is None:
            continue
        seen.add(key)
        out.append(ref)
        if len(out) >= cap:
            break
    return out


def _bible_for(entries) -> dict:
    """{name: visual description} from a cast or asset list.

    Accepts what the API stores: a list of {name, description} dicts (Pydantic
    dumps), a list of objects with those attributes, or an already-built dict.
    Entries with no description are dropped — a name on its own tells the image
    model nothing it didn't already have.
    """
    if isinstance(entries, dict):
        return {
            str(k).strip(): str(v).strip()
            for k, v in entries.items()
            if str(k or "").strip() and str(v or "").strip()
        }
    out: dict[str, str] = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            entry = getattr(entry, "__dict__", {}) or {}
        name = str(entry.get("name", "") or "").strip()
        desc = str(entry.get("description", "") or "").strip()
        if name and desc:
            out[name] = desc
    return out


def _assets_in(names, asset_bible: dict) -> dict:
    """The subset of the asset bible this shot actually uses."""
    from gemini_client import resolve_name

    out: dict[str, str] = {}
    for name in names or []:
        desc = resolve_name(name, asset_bible)
        if desc:
            out[str(name).strip()] = desc
    return out


def _story_context(panels: list[dict], i: int) -> dict:
    """Where panel `i` sits in the film — what runs either side of it.

    Fed to the image model as context it must NOT draw. See
    gemini_client.build_flow_context for why this is the difference between a
    board of illustrations and a board of shots.
    """
    total = len(panels)
    previous = panels[i - 1] if i > 0 else None
    following = panels[i + 1] if i + 1 < total else None
    return {
        "previous": (previous or {}).get("description") or "",
        "next": (following or {}).get("description") or "",
        "previous_same_scene": bool(
            previous is not None
            and previous.get("scene_number") == panels[i].get("scene_number")
        ),
        "scene_number": panels[i].get("scene_number"),
        "shot_number": i + 1,
        "of": total,
    }


def _position_of(panels: list[dict], panel: dict) -> int | None:
    """Where `panel` sits in the board: by its `index` field, else by position."""
    index = int(panel.get("index", 0))
    return next(
        (n for n, p in enumerate(panels) if p.get("index") == index),
        index if 0 <= index < len(panels) else None,
    )


def story_context_for(board_panels: list | None, panel: dict) -> dict | None:
    """What runs either side of ONE panel, for a caller that has only that panel.

    PUBLIC because key poses need it as much as panels do. A shot's key poses are
    planned from its description alone unless something tells the planner where
    the shot sits, and a planner told nothing will happily animate a sleeping man
    waking up and sitting on the edge of the bed — in a shot whose only job is to
    establish the room, and immediately before a close-up that shows him still
    asleep. The neighbours are what bound a shot's action. See
    gemini_client.build_flow_context.
    """
    if not board_panels:
        return None
    panels = [dict(p or {}) for p in board_panels]
    position = _position_of(panels, panel)
    if position is None:
        return None
    # Keep the edited wording: the user may have just rewritten this shot.
    panels[position] = {**panels[position], **panel}
    return _story_context(panels, position)


def _continuity_for_redraw(
    board_dir: str, variant: int, board_panels: list | None, panel: dict
) -> tuple[dict | None, "Image.Image | None"]:
    """Story context + a scene look anchor for a SINGLE panel being redrawn.

    The anchor is the nearest ALREADY-DRAWN panel of the same scene (nearest
    first, so it is the most relevant one), read off disk. Returns (None, None)
    when the caller didn't pass the board — an older caller then behaves exactly
    as it did before.
    """
    if not board_panels:
        return None, None
    panels = [dict(p or {}) for p in board_panels]
    position = _position_of(panels, panel)
    if position is None:
        return None, None

    # Keep the edited wording: the user may have just rewritten this shot.
    panels[position] = {**panels[position], **panel}
    context = _story_context(panels, position)

    scene = panels[position].get("scene_number")
    anchor = None
    for n in sorted(range(len(panels)), key=lambda n: abs(n - position)):
        if n == position or panels[n].get("scene_number") != scene:
            continue
        path = os.path.join(_variant_dir(board_dir, variant), f"panel_{n:02d}.png")
        if os.path.isfile(path):
            try:
                anchor = Image.open(path).convert("RGB")
            except OSError:
                continue
            break
    return context, anchor


def draw_loose_shot(
    board_job_id: str,
    description: str,
    *,
    style: str = "custom",
    aspect_ratio: str = "16:9",
    output_dir: str = "output",
    characters: list | None = None,
    assets_named: list | None = None,
    character_ref_paths: dict | None = None,
    asset_ref_paths: dict | None = None,
    variant: int = 0,
    provider: str | None = None,
    world: dict | None = None,
    cast: list | dict | None = None,
    assets: list | dict | None = None,
    anchor_index: int | None = None,
    story_context: dict | None = None,
) -> "Image.Image | None":
    """Draw ONE shot in a board's look WITHOUT putting it on the board.

    ⚠ THE ONLY DIFFERENCE FROM `regenerate_panel` IS WHERE IT GOES, and that
    difference is the whole reason this exists. A redraw belongs to a panel: it
    is archived as a version of it, written into the board's result, and read
    back through the board's index. This one is a shot the timeline invented
    between two others — the animatic editor's "generate a shot after this one"
    — and it must NOT become a panel, because inserting one renumbers every
    panel after it and every other animatic pointing at that board by index
    would then show the wrong picture. So the image is RETURNED and the caller
    stores it as an ordinary animatic upload. Nothing here writes to disk.

    Everything else is deliberately identical, because the new shot has to sit
    between its neighbours without looking like it came from somewhere else: the
    board's active style variant and aspect, its written continuity bible, its
    world, its locked character and asset references, and `anchor_index` — a
    DRAWN panel of the board used as the look anchor, which for this caller is
    the shot right beside the gap and therefore the most relevant one there is.

    `characters` / `assets_named` are the names appearing in this shot; the
    caller passes its neighbours' cast, since a shot invented between two others
    is almost always the same people in the same place.

    Returns a PIL image, or None if the model returned nothing (safety filter).
    """
    from gemini_client import generate_storyboard_panel

    description = (description or "").strip()
    if not description:
        return None

    board_folder = os.path.join(output_dir, "_storyboards", board_job_id)
    char_refs = _load_character_refs(character_ref_paths)
    asset_refs = _load_refs(asset_ref_paths, "asset")
    shot_char_refs = _gather_refs(characters or [], char_refs, 3)
    shot_asset_refs = _gather_refs(assets_named or [], asset_refs, 3)
    asset_bible = _bible_for(assets)

    anchor = None
    if anchor_index is not None:
        path = os.path.join(_variant_dir(board_folder, variant), f"panel_{int(anchor_index):02d}.png")
        if os.path.isfile(path):
            try:
                anchor = Image.open(path).convert("RGB")
            except OSError:
                anchor = None

    image = generate_storyboard_panel(
        description=description,
        style=style,
        aspect_ratio=aspect_ratio,
        characters=characters or [],
        location="",
        camera="",
        reference_images=shot_char_refs or None,
        asset_reference_images=shot_asset_refs or None,
        scene_reference_image=anchor,
        provider=provider,
        world=world,
        character_bible=_bible_for(cast),
        asset_bible=_assets_in(assets_named or [], asset_bible),
        story_context=story_context,
        # ⚠ UNSEEDED, like the Retry button and for the same reason: pressing
        # "generate" again after a picture you did not like must not hand back
        # the identical drawing.
        variation=None,
    )
    if image is None:
        return None
    # Normalised and conformed exactly as a panel is, so a shot generated into
    # the middle of a board reads as one of its pictures rather than standing
    # out as a differently-shaped, differently-graded one.
    return conform_to_style(normalise_panel(image, aspect_ratio), style)


def regenerate_panel(
    job_id: str,
    panel: dict,
    style: str = "custom",
    aspect_ratio: str = "16:9",
    output_dir: str = "output",
    character_ref_paths: dict | None = None,
    asset_ref_paths: dict | None = None,
    variant: int = 0,
    provider: str | None = None,
    world: dict | None = None,
    cast: list | dict | None = None,
    assets: list | dict | None = None,
    board_panels: list | None = None,
) -> dict:
    """Re-generate ONE panel (used by the Retry button). Returns the updated panel.

    `variant` targets the active style variant's subfolder + URL. `world` is the
    script's region/period/culture, so a redrawn panel matches the rest of the
    board instead of reverting to the model's default look. `cast` / `assets`
    are the written continuity bible and `board_panels` is the whole board, so a
    redraw gets the SAME continuity the original run had: the shots either side
    of this one for flow, and a drawn panel from the same scene as a look
    anchor. Without them, pressing Regenerate was the single easiest way to
    knock a panel off-model — it was the one call that knew nothing about the
    rest of the film.
    """
    from gemini_client import generate_storyboard_panel

    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    write_dir = _variant_dir(board_dir, variant)
    os.makedirs(write_dir, exist_ok=True)
    char_refs = _load_character_refs(character_ref_paths)
    asset_refs = _load_refs(asset_ref_paths, "asset")

    i = panel["index"]
    shot_char_refs = _gather_refs(panel.get("characters", []), char_refs, 3)
    shot_asset_refs = _gather_refs(panel.get("assets", []), asset_refs, 3)
    asset_bible = _bible_for(assets)
    context, anchor = _continuity_for_redraw(board_dir, variant, board_panels, panel)

    updated = dict(panel)
    description = str(panel.get("description", "")).strip()
    image = None
    if description:
        image = generate_storyboard_panel(
            description=description,
            style=style,
            aspect_ratio=aspect_ratio,
            characters=panel.get("characters", []) or [],
            location=panel.get("location", "") or "",
            camera=panel.get("camera", "") or "",
            reference_images=shot_char_refs or None,
            asset_reference_images=shot_asset_refs or None,
            scene_reference_image=anchor,
            provider=provider,
            world=world,
            character_bible=_bible_for(cast),
            asset_bible=_assets_in(panel.get("assets", []) or [], asset_bible),
            story_context=context,
            # This is the Retry button: the request is otherwise IDENTICAL to
            # the one that produced the panel being replaced, so it must go
            # unseeded or it would redraw the exact same picture every click.
            variation=None,
        )

    if image is not None:
        # Normalise BEFORE saving so a redrawn panel matches the rest of the
        # board rather than standing out as a differently-sized picture — in
        # shape and, via conform_to_style, in palette.
        image = conform_to_style(normalise_panel(image, aspect_ratio), style)
        # Archived as a new version, NOT written over the old one — a redraw you
        # don't like must not destroy the picture you had.
        n = save_panel_version(image, board_dir, variant, i)
        updated["url"] = _panel_url(job_id, i, variant)
        updated["failed"] = False
        updated["versions"] = n + 1
        updated["active_version"] = n
    else:
        updated["url"] = None
        updated["failed"] = True
    return updated


def run_storyboard(
    job_id: str,
    shots: list[dict],
    style: str = "custom",
    aspect_ratio: str = "16:9",
    output_dir: str = "output",
    provider: str | None = None,
    character_ref_paths: dict | None = None,
    asset_ref_paths: dict | None = None,
    variant: int = 0,
    composition_ref_dir: str | None = None,
    world: dict | None = None,
    cast: list | dict | None = None,
    assets: list | dict | None = None,
    progress_cb=None,
) -> dict:
    """Generate a storyboard panel for each shot.

    `variant` writes panels into a per-style subfolder (0 = the board root) and
    tags their URLs so the client caches each style separately.
    `composition_ref_dir`, when set, feeds the matching existing panel as a
    composition reference so a re-style keeps the same staging (only the art
    style changes).

    Args:
        job_id: owning job id (used for the output folder + panel URLs).
        shots: list of shot dicts {scene_number, shot_number, description,
               characters[], dialogue[], assets[], location, camera}.
               `dialogue` is carried onto the panel for the board/PDF to show,
               and is deliberately kept OUT of the image prompt: asked to draw
               a line of speech, an image model letters it into the panel.
        style / aspect_ratio: chosen on the input page.
        output_dir: base output directory.
        provider: image backend ("vertex" | "gemini"); defaults to IMAGE_PROVIDER.
        character_ref_paths: {character_name: image_path} — reference images fed
            into every panel the character appears in (Stage B consistency).
        asset_ref_paths: {asset_name: image_path} — prop/background reference
            images fed into every panel the asset appears in (Stage B2 consistency).
        world: the script's region/period/culture block, prefixed onto every
            panel prompt so the whole board stays true to the story's world
            (see gemini_client.build_world_context).
        cast: the breakdown's character list [{name, description}, …]. THE
            written continuity bible — every panel is told what the people in it
            look like, so the same person is drawn the same way in shot 12 as in
            shot 1 even when no reference images were generated (which is the
            normal case: the rough-sketch style skips the cast step).
        assets: the breakdown's asset list [{name, description}, …], same idea
            for recurring props and sets.
        progress_cb: optional callable(update: dict) for live progress. Receives
            {percent, stage, message, current, total, panels(partial list)}.

    Returns:
        {style, aspect_ratio, count, panels: [{index, scene_number, shot_number,
         description, characters, dialogue, location, camera, url, failed}]}.
    """
    from gemini_client import generate_storyboard_panel

    total = len(shots)
    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    write_dir = _variant_dir(board_dir, variant)
    os.makedirs(write_dir, exist_ok=True)

    char_refs = _load_character_refs(character_ref_paths)
    asset_refs = _load_refs(asset_ref_paths, "asset")
    # The WRITTEN bible. Free — the breakdown already wrote these descriptions —
    # and the only consistency channel that works with no reference images.
    character_bible = _bible_for(cast)
    asset_bible = _bible_for(assets)
    # Cap references per panel so a crowd scene doesn't overload the request.
    MAX_REFS_PER_PANEL = 3

    # Build every panel up front so the board can show correct shot numbers and a
    # skeleton for each one still rendering (url=None, failed=False → skeleton).
    panels: list[dict] = [
        {
            "index": i,
            "scene_number": shot.get("scene_number", 1),
            "shot_number": shot.get("shot_number", i + 1),
            "description": str(shot.get("description", "")).strip(),
            "characters": shot.get("characters", []) or [],
            # Carried through so the board and the PDF can show what is said in
            # this panel. NOT part of the image prompt — see below.
            "dialogue": _dialogue_of(shot),
            "assets": shot.get("assets", []) or [],
            "location": shot.get("location", "") or "",
            "camera": shot.get("camera", "") or "",
            "url": None,
            "failed": False,
        }
        for i, shot in enumerate(shots)
    ]

    state_lock = threading.Lock()
    done = 0

    def _emit(percent, message, extra=None):
        if not progress_cb:
            return
        with state_lock:
            snapshot = [dict(p) for p in panels]
            current = done
        update = {
            "percent": percent,
            "stage": "generating",
            "message": message,
            "current": current,
            "total": total,
            "panels": snapshot,  # full-length; pending ones render as skeletons
        }
        if extra:
            update.update(extra)
        try:
            progress_cb(update)
        except Exception:  # noqa: BLE001 — progress must never kill the run
            logger.debug("[storyboard %s] progress cb failed (ignored)", job_id, exc_info=True)

    # One LOOK ANCHOR per scene: the scene's first drawn panel, handed to every
    # later panel of that scene so the room, the people and the drawing style
    # carry across the cut. Anchored on ONE fixed picture per scene rather than
    # chained panel→panel — chaining compounds drift (panel_sequence says the
    # same thing about frames, for the same reason).
    scene_anchors: dict[object, "Image.Image"] = {}

    def _render(i: int, *, attempt: int = 1) -> None:
        """Draw ONE panel and record the outcome (runs in the panel pool)."""
        # Every queued panel checks here first, so a stop costs nothing beyond
        # the calls already in flight. Skipped panels stay url=None/failed=False
        # — the board then offers "Generate this panel" for each of them.
        if is_cancelled(job_id):
            return
        panel = panels[i]
        description = panel["description"]
        # Gather reference images for the characters + assets in THIS shot.
        shot_char_refs = _gather_refs(panel["characters"], char_refs, MAX_REFS_PER_PANEL)
        shot_asset_refs = _gather_refs(panel["assets"], asset_refs, MAX_REFS_PER_PANEL)
        with state_lock:
            anchor = scene_anchors.get(panel.get("scene_number"))

        image = None
        if description:
            image = generate_storyboard_panel(
                description=description,
                style=style,
                aspect_ratio=aspect_ratio,
                characters=panel["characters"],
                location=panel["location"],
                camera=panel["camera"],
                reference_images=shot_char_refs or None,
                asset_reference_images=shot_asset_refs or None,
                scene_reference_image=anchor,
                composition_reference_image=_load_composition_ref(composition_ref_dir, i),
                provider=provider,
                world=world,
                character_bible=character_bible,
                asset_bible=_assets_in(panel["assets"], asset_bible),
                story_context=_story_context(panels, i),
                # A second attempt must not redraw the identical picture that
                # just came back empty — a different seed is the only lever we
                # have on a refusal.
                variation=0 if attempt == 1 else attempt,
            )

        if image is not None:
            # Every panel gets the same treatment, so a finished board reads as
            # one set of pictures instead of a jumble of sizes. See normalise_panel.
            image = normalise_panel(image, aspect_ratio)
            # And the same PALETTE. On a greyscale style the model still returns
            # a full-colour illustration now and then — two panels in fifteen on
            # the board this was traced from — and a board that changes medium
            # half way through is the "colours change" report.
            image = conform_to_style(image, style)
            # Version 0 of this panel. Every later redraw appends rather than
            # overwriting — see save_panel_version.
            n = save_panel_version(image, board_dir, variant, i)
            with state_lock:
                panel["url"] = _panel_url(job_id, i, variant)
                panel["failed"] = False
                panel["versions"] = n + 1
                panel["active_version"] = n
                scene_anchors.setdefault(panel.get("scene_number"), image)
            logger.info("[storyboard %s] panel %d/%d done (variant %d)", job_id, i + 1, total, variant)
        else:
            with state_lock:
                panel["failed"] = True
            logger.warning("[storyboard %s] panel %d/%d FAILED (no image)", job_id, i + 1, total)

    def _wave(indices: list[int], label: str, *, attempt: int = 1) -> None:
        """Draw a set of panels concurrently and report as each lands."""
        nonlocal done
        if not indices:
            return
        with ThreadPoolExecutor(
            max_workers=min(PANEL_CONCURRENCY, len(indices)), thread_name_prefix="panel"
        ) as pool:
            futures = {pool.submit(_render, i, attempt=attempt): i for i in indices}
            for future in as_completed(futures):
                i = futures[future]
                try:
                    future.result()
                except Exception:  # noqa: BLE001 — one panel must not kill the board
                    with state_lock:
                        panels[i]["failed"] = True
                    logger.exception("[storyboard %s] panel %d crashed", job_id, i + 1)
                with state_lock:
                    # A retry re-draws a panel that already counted — the
                    # progress bar must not run past the end.
                    if attempt == 1:
                        done += 1
                    completed = done
                _emit(
                    int(2 + (completed / max(total, 1)) * 96),
                    "Stopping — finishing the panels already started…"
                    if is_cancelled(job_id)
                    else label.format(done=completed, total=total),
                )

    logger.info(
        "[storyboard %s] generating %d panels (style=%s, aspect=%s, concurrency=%d)",
        job_id, total, style, aspect_ratio, PANEL_CONCURRENCY,
    )
    # A stop flag left over from a previous run must not kill this one.
    clear_cancel(job_id)
    _emit(2, f"Starting {total} panels…")

    # TWO WAVES, so continuity has something to hold on to.
    #
    # Wave 1 draws the FIRST shot of every scene. Those become the scene's look
    # anchors. Wave 2 draws everything else, each panel handed its own scene's
    # anchor, so the second shot of a bedroom scene is the same bedroom and the
    # same man as the first. Drawing all panels in one flat pass — which is what
    # this did before — gave the model nothing to match, and a twelve-shot board
    # came back as twelve unrelated pictures with a different lead in half of
    # them. Both waves are still concurrent, so the wall clock barely moves.
    scene_leads: list[int] = []
    seen_scenes: set = set()
    for i, panel in enumerate(panels):
        scene = panel.get("scene_number")
        if scene not in seen_scenes:
            seen_scenes.add(scene)
            scene_leads.append(i)
    followers = [i for i in range(total) if i not in set(scene_leads)]

    _wave(scene_leads, "Setting the look of each scene… {done} of {total} done")
    _wave(followers, "Drawing panels… {done} of {total} done")

    # HOLES. A refused panel used to just sit there as a gap — and "Make
    # animatic" silently drops gaps, so a missing panel quietly became a missing
    # BEAT in the film. Most refusals are one-offs (a safety filter reading a
    # word badly, an empty response), so each hole gets exactly one more try, at
    # a different seed and now with its scene's anchor available. One retry, not
    # a loop: a panel that fails twice has a real problem the user should see.
    if not is_cancelled(job_id):
        holes = [i for i, p in enumerate(panels) if p["failed"]]
        if holes:
            logger.info(
                "[storyboard %s] %d panel(s) came back empty — one retry each: %s",
                job_id, len(holes), holes,
            )
            _emit(98, f"Re-drawing {len(holes)} panel(s) that didn't come back…")
            _wave(holes, "Re-drawing missed panels… {done} of {total} done", attempt=2)

    stopped = is_cancelled(job_id)
    clear_cancel(job_id)
    ok = sum(1 for p in panels if p.get("url") and not p["failed"])
    drawn = sum(1 for p in panels if p.get("url") or p["failed"])
    if stopped:
        _emit(100, f"Stopped by you — {ok} of {total} panels drawn.")
        logger.info("[storyboard %s] STOPPED: %d/%d drawn (%d ok)", job_id, drawn, total, ok)
    else:
        _emit(100, f"Done — {ok}/{total} panels generated.")
        logger.info("[storyboard %s] complete: %d/%d ok", job_id, ok, total)

    return {
        "style": style,
        "aspect_ratio": aspect_ratio,
        "count": total,
        "ok_count": ok,
        "variant": variant,
        "panels": panels,
        # The run ended early because the user stopped it — the board says so
        # rather than pretending a half-drawn board is a finished one.
        "stopped": stopped,
    }
