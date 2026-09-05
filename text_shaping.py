"""Complex-script SHAPING for the exporter — the thing that makes हिन्दी land as हिन्दी.

⚠ IMPORT THIS BEFORE `PIL.ImageFont`, IN EVERY MODULE THAT DRAWS TEXT. Not for
tidiness — for correctness, and the window is exactly one import wide. Read on.

WHAT THIS FIXES
---------------
Most of the world's writing does not work the way Latin does. A run of
characters is not a run of glyphs:

    हिन्दी   the ि is typed AFTER its consonant and drawn BEFORE it, and
             न + ् + द is one conjoined glyph, not three
    ਪੰਜਾਬੀ   vowel signs sit above and below the letter they belong to
    العربية  every letter has four shapes depending on its neighbours, and the
             line runs right to left
    ไทย      tone marks stack over the vowel, not over the consonant

Turning characters into correctly-ordered, correctly-joined, correctly-stacked
glyphs is called SHAPING, and it is a real piece of software — HarfBuzz — not a
lookup table. Browsers have always done it. Pillow does it too, through
`libraqm` (HarfBuzz for the joining, FriBiDi for the right-to-left), but ONLY
IF libraqm can load.

WHY IT WAS OFF, AND WHAT THAT COST
----------------------------------
Pillow's own wheel already carries HarfBuzz and libraqm. What it does NOT carry
is FriBiDi: libraqm looks for it by name at import time and, not finding it,
switches itself off — silently, with a working Pillow left behind. So
`draw.text` kept drawing. It just drew the characters in typed order, unjoined:

    हिन्दी  →  हनि्दी          क्षत्रिय  →  क् षत् रयि

That is the WHOLE PROJECT'S ONE INVARIANT broken, in the worst possible
direction. `animatic_fonts.py` exists so that the caption in the Program
monitor and the caption in the MP4 come off the same .ttf and cannot disagree.
They came off the same file — and disagreed anyway, because the browser shaped
the text and the exporter did not. The preview was RIGHT, which is what made it
invisible: nobody reviewing on screen could see that the video was wrong.

So this is not "extra language support". Every Hindi caption this app has ever
exported was wrong, and the only reason it was ever exported at all is that the
preview lied about it.

HOW IT IS TURNED ON
-------------------
FriBiDi is loaded into the process BEFORE Pillow's font module is imported, so
that libraqm finds it already in memory and enables itself. Once
`PIL._imagingft` has been imported without it, that decision is FINAL for the
life of the process — there is no re-check, no re-enable. Hence the rule at the
top of this file: modules that draw text import this one first.

Where the library comes from, per platform:

  Windows   `vendor/fribidi/libfribidi-0.dll`, shipped with the project. Same
            reasoning as bundling the fonts themselves: a dependency resolved
            off the machine is a dependency that renders differently on the
            next machine. LGPL-2.1+, unmodified, loaded dynamically — see
            `vendor/fribidi/README.md` for the licence and the source.
  Linux     `libfribidi.so.0` from the system (`apt-get install libfribidi0`;
            already present on most distributions and in most base images).
  macOS     `libfribidi.dylib` from Homebrew (`brew install fribidi`).

`ctypes.CDLL` rather than editing PATH on purpose: PATH is inherited by every
subprocess, and this project launches ffmpeg. A process-local load changes
nothing outside this process.

WHEN IT CANNOT BE TURNED ON
---------------------------
`AVAILABLE` is False and the app keeps working — Latin, Cyrillic, Greek and CJK
captions do not need shaping and are unaffected. What must NOT happen is a
complex-script caption being exported anyway, wrong, in silence. That is what
`needs_shaping()` is for: the caller asks whether this particular string needs
shaping, and the editor refuses to offer those fonts while `AVAILABLE` is
False. `tests/captions_check.py` fails outright if shaping is off, because a
dev box that cannot shape produces MP4s nobody should ship.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys

logger = logging.getLogger(__name__)

# The vendored copy, and the names the system copy goes by. Tried in order: the
# project's own file first so a machine that HAS fribidi still uses the version
# this project was tested against, which is the same argument the bundled fonts
# make.
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "fribidi")

if sys.platform == "win32":
    _CANDIDATES = (
        os.path.join(_VENDOR_DIR, "libfribidi-0.dll"),
        "libfribidi-0.dll",
        "fribidi-0.dll",
        "fribidi.dll",
    )
elif sys.platform == "darwin":
    _CANDIDATES = (
        os.path.join(_VENDOR_DIR, "libfribidi.dylib"),
        "libfribidi.dylib",
        "/opt/homebrew/lib/libfribidi.dylib",
        "/usr/local/lib/libfribidi.dylib",
    )
else:
    _CANDIDATES = (
        os.path.join(_VENDOR_DIR, "libfribidi.so.0"),
        "libfribidi.so.0",
        "libfribidi.so",
    )


def _load_fribidi() -> str | None:
    """Pull FriBiDi into this process. The loaded path, or None.

    ⚠ RUNS AT IMPORT, and has to. libraqm asks for FriBiDi exactly once, while
    `PIL._imagingft` is initialising; a load after that point is a load nobody
    reads. Windows matches an already-loaded module by its BASE NAME, and
    every platform's dynamic loader does the same, which is why loading the
    vendored file by its full path still satisfies libraqm's search by name.
    """
    for candidate in _CANDIDATES:
        if os.sep in candidate and not os.path.isfile(candidate):
            continue
        try:
            ctypes.CDLL(candidate)
            return candidate
        except OSError:
            continue
    # Last resort on Unix: let the linker's own search find it under whatever
    # soname this distribution uses.
    found = ctypes.util.find_library("fribidi")
    if found:
        try:
            ctypes.CDLL(found)
            return found
        except OSError:
            pass
    return None


FRIBIDI_PATH = _load_fribidi()

# ⚠ THE FIRST IMPORT OF PIL's FONT MODULE IN THE PROCESS, and deliberately
# below the load above. Everything this module promises rests on that order.
from PIL import features as _pil_features  # noqa: E402

try:
    AVAILABLE: bool = bool(_pil_features.check("raqm"))
except Exception:  # pragma: no cover - a Pillow built without _imagingft at all
    AVAILABLE = False

HARFBUZZ_VERSION = _pil_features.version("harfbuzz") if AVAILABLE else None
FRIBIDI_VERSION = _pil_features.version("fribidi") if AVAILABLE else None

if not AVAILABLE:
    logger.warning(
        "text shaping is OFF (FriBiDi not loaded from %s) — Devanagari, Gurmukhi, "
        "Bengali, Tamil, Telugu, Kannada, Malayalam, Gujarati, Odia, Arabic, Urdu, "
        "Hebrew and Thai captions would export MALFORMED. Those fonts stay locked. "
        "See text_shaping.py for how to install it.",
        _VENDOR_DIR,
    )

# ---------------------------------------------------------------------------
# Which text needs shaping at all
# ---------------------------------------------------------------------------
# ⚠ THIS IS A TWIN of `SHAPED_RANGES` in `client/src/animatic/fonts.js`, and
# `tests/captions_check.py` compares them. The browser needs the same answer to
# decide whether to warn, and two hand-kept range tables is exactly the drift
# this project keeps a test for everywhere else.
#
# Latin, Cyrillic, Greek and CJK are NOT here: their characters map one-to-one
# onto glyphs in typed order, so an unshaped Pillow draws them correctly and
# always has. Only writing systems that reorder, join or stack are listed.
SHAPED_RANGES: tuple[tuple[int, int], ...] = (
    (0x0590, 0x05FF),  # Hebrew
    (0x0600, 0x06FF),  # Arabic
    (0x0700, 0x074F),  # Syriac
    (0x0750, 0x077F),  # Arabic Supplement
    (0x0780, 0x07BF),  # Thaana (Dhivehi)
    (0x0900, 0x097F),  # Devanagari
    (0x0980, 0x09FF),  # Bengali
    (0x0A00, 0x0A7F),  # Gurmukhi
    (0x0A80, 0x0AFF),  # Gujarati
    (0x0B00, 0x0B7F),  # Odia
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0C80, 0x0CFF),  # Kannada
    (0x0D00, 0x0D7F),  # Malayalam
    (0x0D80, 0x0DFF),  # Sinhala
    (0x0E00, 0x0E7F),  # Thai
    (0x0E80, 0x0EFF),  # Lao
    (0x0F00, 0x0FFF),  # Tibetan
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)


def needs_shaping(text: str) -> bool:
    """Would `text` come out wrong if it were drawn without shaping?

    ⚠ MIRRORS `needsShaping` in `fonts.js`. Asked before an export and before
    the picker offers a font, so that "we cannot draw this correctly" is said
    out loud instead of being discovered in the finished video.
    """
    for ch in text or "":
        code = ord(ch)
        for low, high in SHAPED_RANGES:
            if low <= code <= high:
                return True
    return False


def report() -> str:
    """One line for a log or a health check. Never raises."""
    if AVAILABLE:
        return (
            f"text shaping ON (HarfBuzz {HARFBUZZ_VERSION}, FriBiDi "
            f"{FRIBIDI_VERSION}, via {FRIBIDI_PATH})"
        )
    return "text shaping OFF — complex-script captions are locked"
