"""The fonts a caption can be set in — the SERVER's half of the list.

⚠ THIS MODULE IS A TWIN of `client/src/animatic/fonts.js`. Not "similar to":
the two lists must be element-for-element identical, and
`tests/captions_check.py` fails if they are not.

WHY THIS IS A TWIN AT ALL, when nothing else about a font is:

    The preview runs in a BROWSER and the export runs on the SERVER, and until
    now each went looking for a font by name and took whatever it found.
    `_text_font` asked Pillow for "arial.ttf" and fell back to DejaVu, then to
    Pillow's bitmap default; the monitor asked CSS for whatever the operating
    system called the nearest sans. On the developer's Windows machine those
    happened to look alike. On a Linux server they do not, and the caption you
    positioned in the monitor is not the caption that lands in the MP4 —
    different width, different wrap, different number of lines.

    So the font is a FILE THAT SHIPS WITH THE PROJECT. Both sides load the same
    six .ttf files out of `client/public/fonts/`, addressed by the same id, and
    neither ever consults the machine it is running on.

`family` is the CSS name the browser registers each file under. It is
deliberately NOT the font's real family name ("Inter", "Anton"): a user with
Inter installed would otherwise get the system copy, which is exactly the
divergence this module exists to prevent. Prefixing it guarantees the only
Inter in play is the one in `client/public/fonts/`.

Adding a font: drop the .ttf in `client/public/fonts/`, add one entry HERE and
the identical entry in `fonts.js`, and record its licence in that folder's
README. Nothing else — the picker, the @font-face rules and the exporter are
all generated from these two lists.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Where the .ttf files live. Under `client/public/` because the BROWSER has to
# be able to fetch them at `/fonts/<file>` — Vite serves that folder verbatim —
# and there is no version of "one file for both sides" where the server keeps a
# second copy of its own. The server simply reads out of the client's folder.
FONT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "client", "public", "fonts"
)

# `line_ratio` is (ascent + descent) ÷ font size for the file, measured with
# Pillow and rounded to two places — `tests/captions_check.py` re-measures it and
# fails if the number here has drifted from the .ttf.
#
# ⚠ IT IS HERE BECAUSE ONLY THE BROWSER NEEDS IT, and only this list can tell it.
# The exporter steps its baselines by `(ascent + descent) × line_height`, which
# it reads off the face; CSS `line-height` is a multiple of the FONT SIZE, which
# is a different number — 22% smaller for Inter and 51% for Anton. So a two-line
# caption sat closer together in the monitor than in the MP4, by an amount that
# depended on which font it was set in. The browser multiplies by this to ask for
# the same distance the exporter draws. (Fixed in the PREVIEW rather than in the
# exporter on purpose: no MP4 anyone has already made changes.)
#
# ⚠ ELEMENT FOR ELEMENT the same as FONTS in `client/src/animatic/fonts.js`.
FONTS: tuple[dict[str, str | float], ...] = (
    # --- Sans, for a caption you are meant to READ rather than look at ------
    # ⚠ INTER IS FIRST AND HAS TO STAY FIRST: `font_entry` falls back to
    # `FONTS[0]`, so the head of this list is what an id nobody knows folds down
    # to, and `DEFAULT_FONT` below names the same one.
    {
        "id": "inter",
        "label": "Inter",
        "file": "Inter-SemiBold.ttf",
        "family": "AnimaticInter",
        "line_ratio": 1.22,
    },
    {
        "id": "montserrat",
        "label": "Montserrat",
        "file": "Montserrat-SemiBold.ttf",
        "family": "AnimaticMontserrat",
        "line_ratio": 1.23,
    },
    {
        "id": "poppins",
        "label": "Poppins",
        "file": "Poppins-SemiBold.ttf",
        "family": "AnimaticPoppins",
        "line_ratio": 1.4,
    },
    {
        "id": "nunito",
        "label": "Nunito",
        "file": "Nunito-Bold.ttf",
        "family": "AnimaticNunito",
        "line_ratio": 1.38,
    },
    # --- Condensed and heavy, for a title card or a lower third -------------
    {
        "id": "anton",
        "label": "Anton",
        "file": "Anton-Regular.ttf",
        "family": "AnimaticAnton",
        "line_ratio": 1.51,
    },
    {
        "id": "bebas",
        "label": "Bebas Neue",
        "file": "BebasNeue-Regular.ttf",
        "family": "AnimaticBebas",
        "line_ratio": 1.2,
    },
    {
        "id": "oswald",
        "label": "Oswald",
        "file": "Oswald-Medium.ttf",
        "family": "AnimaticOswald",
        "line_ratio": 1.49,
    },
    {
        "id": "archivo",
        "label": "Archivo Black",
        "file": "ArchivoBlack-Regular.ttf",
        "family": "AnimaticArchivo",
        "line_ratio": 1.09,
    },
    # --- Serif --------------------------------------------------------------
    {
        "id": "playfair",
        "label": "Playfair Display",
        "file": "PlayfairDisplay-SemiBold.ttf",
        "family": "AnimaticPlayfair",
        "line_ratio": 1.35,
    },
    {
        "id": "merriweather",
        "label": "Merriweather",
        "file": "Merriweather-Bold.ttf",
        "family": "AnimaticMerriweather",
        "line_ratio": 1.27,
    },
    # --- Faces with a voice of their own ------------------------------------
    {
        "id": "bangers",
        "label": "Bangers",
        "file": "Bangers-Regular.ttf",
        "family": "AnimaticBangers",
        "line_ratio": 1.08,
    },
    {
        "id": "lobster",
        "label": "Lobster",
        "file": "Lobster-Regular.ttf",
        "family": "AnimaticLobster",
        "line_ratio": 1.25,
    },
    {
        "id": "caveat",
        "label": "Caveat",
        "file": "Caveat-SemiBold.ttf",
        "family": "AnimaticCaveat",
        "line_ratio": 1.26,
    },
    {
        "id": "courier",
        "label": "Courier Prime",
        "file": "CourierPrime-Regular.ttf",
        "family": "AnimaticCourier",
        "line_ratio": 1.14,
    },
)

FONT_IDS = tuple(f["id"] for f in FONTS)

# The one every caption written before this phase is set in, and what an
# unrecognised id folds down to. Same forgiveness `ease`, `clip_kind` and the
# transition kinds get: a project written by a newer client still opens and
# still exports rather than being lost over one word.
DEFAULT_FONT = "inter"


def font_entry(font_id: str | None) -> dict[str, str | float]:
    """The list entry for `font_id`, or the default's. Mirrors `fontEntry`."""
    for font in FONTS:
        if font["id"] == font_id:
            return font
    return FONTS[0]


def font_path(font_id: str | None) -> str | None:
    """The .ttf on disk for `font_id`, or None if it isn't there.

    None rather than an exception: a missing font file must degrade to Pillow's
    built-in face and still produce a video. An export that dies because one
    caption named a font nobody shipped is a worse failure than an ugly caption.
    """
    path = os.path.join(FONT_DIR, str(font_entry(font_id)["file"]))
    if os.path.isfile(path):
        return path
    logger.warning(
        "bundled font %s is missing from %s — falling back", font_id, FONT_DIR
    )
    return None
