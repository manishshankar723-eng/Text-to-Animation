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
    .ttf files out of `client/public/fonts/`, addressed by the same id, and
    neither ever consults the machine it is running on.

`family` is the CSS name the browser registers each file under. It is
deliberately NOT the font's real family name ("Inter", "Anton"): a user with
Inter installed would otherwise get the system copy, which is exactly the
divergence this module exists to prevent. Prefixing it guarantees the only
Inter in play is the one in `client/public/fonts/`.

⚠ A FONT ALSO DECLARES WHAT IT CAN DRAW, and that half is not decoration.
`scripts` is read out of the file's own cmap by `tools/fonts_sync.py` — never
typed by hand, never inferred from the family name — because the failure it
prevents is silent and expensive. Offer a Hindi title in a face with no
Devanagari in it and the customer gets ▯▯▯, in the export, after paying for
the render. `covers()` and `best_font_for_text()` are what stop that from
being possible; read `SCRIPTS` below for how a string is turned into the set
of writing systems it needs.

⚠ AND HALF THE LIST WOULD BE A LIE WITHOUT `text_shaping.py`. Devanagari,
Gurmukhi, Bengali, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam, Arabic,
Urdu, Hebrew and Thai are not drawn one character to one glyph — they reorder,
join and stack, and a Pillow that cannot shape draws हिन्दी as हनि्दी while the
browser shows it correctly. Bundling a Devanagari font without that module
would have shipped the same silent divergence in a new coat.

Adding a font: run `python tools/fonts_sync.py --write` with an entry added to
its `WANTED` table — it downloads the file, freezes it to one static weight if
upstream only ships variable, measures `line_ratio`, reads `scripts` off the
cmap and prints the entry to paste HERE and, identically, into `fonts.js`.
Record the licence in that folder's README. Nothing else: the picker, the
@font-face rules and the exporter are all generated from these two lists.
"""

from __future__ import annotations

import logging
import os

# ⚠ FIRST, AND BEFORE ANYTHING IMPORTS PIL. This is the module that turns
# complex-script shaping on, and it only works if it runs before Pillow's font
# module is loaded — see `text_shaping.py`. Importing it here means anything
# that reaches for a bundled font has already paid for it.
import text_shaping

logger = logging.getLogger(__name__)

# Where the .ttf files live. Under `client/public/` because the BROWSER has to
# be able to fetch them at `/fonts/<file>` — Vite serves that folder verbatim —
# and there is no version of "one file for both sides" where the server keeps a
# second copy of its own. The server simply reads out of the client's folder.
FONT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "client", "public", "fonts"
)

# ---------------------------------------------------------------------------
# The writing systems, and how a string is sorted into them
# ---------------------------------------------------------------------------
# ⚠ ELEMENT FOR ELEMENT the same as SCRIPTS in `client/src/animatic/fonts.js`.
#
# `ranges` is what turns TEXT into a set of required scripts, and THE ORDER OF
# THIS TUPLE IS THE PRIORITY: the first entry whose range contains a character
# claims it. That is why `urdu` sits above `arabic` and `vietnamese` above
# `latin-ext` — their characters live inside the more general block, and a
# general-first walk would file ٹ as ordinary Arabic and hand the caption to a
# face that cannot draw it.
#
# `shaped` marks the writing systems that do not survive being drawn in typed
# order. `text_shaping.py` carries the same set as ranges of its own and
# explains what happens without it.
#
# `any_of` is the one place a required script is not a script any font
# declares. Han is one block of characters shared by three regions whose fonts
# genuinely differ — Noto Sans TC has no 这, 说 or 们 — so text is only ever
# classified as `han`, and any of the three regional coverages satisfies it.
# Which one to PREFER is what the picker's grouping is for.
SCRIPTS: tuple[dict, ...] = (
    # --- Needs shaping ------------------------------------------------------
    {
        "id": "urdu",
        "label": "Urdu",
        "note": "اردو, فارسی",
        "shaped": True,
        "any_of": (),
        # The letters Urdu and Persian have and Arabic does not. Listed one at a
        # time rather than as a block because they are scattered THROUGH the
        # Arabic block, interleaved with letters this must not claim.
        "ranges": (
            (0x0679, 0x0679), (0x067E, 0x067E), (0x0686, 0x0686), (0x0688, 0x0688),
            (0x0691, 0x0691), (0x0698, 0x0698), (0x06A9, 0x06A9), (0x06AF, 0x06AF),
            (0x06BA, 0x06BA), (0x06BE, 0x06BE), (0x06C0, 0x06C3), (0x06CC, 0x06CC),
            (0x06D2, 0x06D3),
        ),
    },
    {
        "id": "arabic",
        "label": "Arabic",
        "note": "العربية",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    },
    {
        "id": "hebrew",
        "label": "Hebrew",
        "note": "עברית",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0590, 0x05FF), (0xFB1D, 0xFB4F)),
    },
    {
        "id": "devanagari",
        "label": "Devanagari",
        "note": "हिन्दी, मराठी, नेपाली, भोजपुरी, संस्कृत",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0900, 0x097F), (0xA8E0, 0xA8FF)),
    },
    {
        "id": "gurmukhi",
        "label": "Gurmukhi",
        "note": "ਪੰਜਾਬੀ",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0A00, 0x0A7F),),
    },
    {
        "id": "bengali",
        "label": "Bengali",
        "note": "বাংলা, অসমীয়া",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0980, 0x09FF),),
    },
    {
        "id": "gujarati",
        "label": "Gujarati",
        "note": "ગુજરાતી",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0A80, 0x0AFF),),
    },
    {
        "id": "odia",
        "label": "Odia",
        "note": "ଓଡ଼ିଆ",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0B00, 0x0B7F),),
    },
    {
        "id": "tamil",
        "label": "Tamil",
        "note": "தமிழ்",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0B80, 0x0BFF),),
    },
    {
        "id": "telugu",
        "label": "Telugu",
        "note": "తెలుగు",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0C00, 0x0C7F),),
    },
    {
        "id": "kannada",
        "label": "Kannada",
        "note": "ಕನ್ನಡ",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0C80, 0x0CFF),),
    },
    {
        "id": "malayalam",
        "label": "Malayalam",
        "note": "മലയാളം",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0D00, 0x0D7F),),
    },
    {
        "id": "thai",
        "label": "Thai",
        "note": "ไทย",
        "shaped": True,
        "any_of": (),
        "ranges": ((0x0E00, 0x0E7F),),
    },
    # --- Drawn in typed order, no shaping needed ----------------------------
    {
        "id": "kana",
        "label": "Japanese",
        "note": "日本語 — hiragana and katakana",
        "shaped": False,
        "any_of": (),
        "ranges": ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9F)),
    },
    {
        "id": "hangul",
        "label": "Korean",
        "note": "한국어",
        "shaped": False,
        "any_of": (),
        "ranges": ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7AF)),
    },
    {
        "id": "han",
        "label": "Chinese characters",
        "note": "汉字 / 漢字",
        "shaped": False,
        # ⚠ THE ONE SCRIPT NO FONT DECLARES. See the note above this table.
        "any_of": ("han-sc", "han-tc", "han-jp"),
        "ranges": ((0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
    },
    {
        "id": "greek",
        "label": "Greek",
        "note": "Ελληνικά",
        "shaped": False,
        "any_of": (),
        "ranges": ((0x0370, 0x03FF), (0x1F00, 0x1FFF)),
    },
    {
        "id": "cyrillic",
        "label": "Cyrillic",
        "note": "Русский, Українська, Български, Српски",
        "shaped": False,
        "any_of": (),
        "ranges": ((0x0400, 0x052F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F)),
    },
    {
        "id": "vietnamese",
        "label": "Vietnamese",
        "note": "Tiếng Việt",
        "shaped": False,
        "any_of": (),
        "ranges": (
            (0x0102, 0x0103), (0x0110, 0x0111), (0x01A0, 0x01A1), (0x01AF, 0x01B0),
            (0x1EA0, 0x1EF9),
        ),
    },
    {
        "id": "latin-ext",
        "label": "Latin extended",
        "note": "Polski, Türkçe, Čeština, Magyar, Română",
        "shaped": False,
        "any_of": (),
        "ranges": ((0x0100, 0x024F), (0x1E00, 0x1EFF)),
    },
    {
        "id": "latin",
        "label": "Latin",
        "note": "English, Español, Français, Deutsch, Italiano, Português",
        "shaped": False,
        "any_of": (),
        "ranges": ((0x00A0, 0x00FF), (0x2010, 0x2027), (0x20A0, 0x20BF)),
    },
    {
        "id": "latin-basic",
        "label": "Basic Latin",
        "note": "A–Z, 0–9 and punctuation",
        "shaped": False,
        "any_of": (),
        "ranges": ((0x0021, 0x007E),),
    },
    # --- Declared by a font, never required by text -------------------------
    # The three regional cuts of Han. They exist so the picker can say WHICH
    # Chinese a face is for, and so `any_of` above has something to point at.
    # Nothing is ever classified into them, which is why they have no ranges.
    {"id": "han-sc", "label": "Chinese (Simplified)", "note": "简体中文", "shaped": False, "any_of": (), "ranges": ()},
    {"id": "han-tc", "label": "Chinese (Traditional)", "note": "繁體中文", "shaped": False, "any_of": (), "ranges": ()},
    {"id": "han-jp", "label": "Japanese kanji", "note": "漢字", "shaped": False, "any_of": (), "ranges": ()},
)

SCRIPT_IDS = tuple(s["id"] for s in SCRIPTS)

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
# `group` is the shelf the picker files the face on — one of the ids in
# `SCRIPTS`, and an editorial choice rather than a fact about the file. Noto
# Sans happens to contain Devanagari; it is still a Latin font, and filing it
# under हिन्दी would bury the five faces actually chosen for Hindi.
#
# ⚠ ELEMENT FOR ELEMENT the same as FONTS in `client/src/animatic/fonts.js`.
FONTS: tuple[dict, ...] = (
    # ⚠ INTER IS FIRST AND HAS TO STAY FIRST: `font_entry` falls back to
    # `FONTS[0]`, so the head of this list is what an id nobody knows folds down
    # to, and `DEFAULT_FONT` below names the same one.
    # --- Sans, for a caption you are meant to READ rather than look at -------
    {
        "id": "inter",
        "label": "Inter",
        "file": "Inter-SemiBold.ttf",
        "family": "AnimaticInter",
        "line_ratio": 1.22,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek",),
    },
    {
        "id": "montserrat",
        "label": "Montserrat",
        "file": "Montserrat-SemiBold.ttf",
        "family": "AnimaticMontserrat",
        "line_ratio": 1.23,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic",),
    },
    {
        "id": "poppins",
        "label": "Poppins",
        "file": "Poppins-SemiBold.ttf",
        "family": "AnimaticPoppins",
        "line_ratio": 1.4,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "devanagari",),
    },
    {
        "id": "nunito",
        "label": "Nunito",
        "file": "Nunito-Bold.ttf",
        "family": "AnimaticNunito",
        "line_ratio": 1.38,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic",),
    },
    # --- Condensed and heavy, for a title card or a lower third --------------
    {
        "id": "anton",
        "label": "Anton",
        "file": "Anton-Regular.ttf",
        "family": "AnimaticAnton",
        "line_ratio": 1.51,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese",),
    },
    {
        "id": "bebas",
        "label": "Bebas Neue",
        "file": "BebasNeue-Regular.ttf",
        "family": "AnimaticBebas",
        "line_ratio": 1.2,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext",),
    },
    {
        "id": "oswald",
        "label": "Oswald",
        "file": "Oswald-Medium.ttf",
        "family": "AnimaticOswald",
        "line_ratio": 1.49,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic",),
    },
    {
        "id": "archivo",
        "label": "Archivo Black",
        "file": "ArchivoBlack-Regular.ttf",
        "family": "AnimaticArchivo",
        "line_ratio": 1.09,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext",),
    },
    # --- Serif ---------------------------------------------------------------
    {
        "id": "playfair",
        "label": "Playfair Display",
        "file": "PlayfairDisplay-SemiBold.ttf",
        "family": "AnimaticPlayfair",
        "line_ratio": 1.35,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic",),
    },
    {
        "id": "merriweather",
        "label": "Merriweather",
        "file": "Merriweather-Bold.ttf",
        "family": "AnimaticMerriweather",
        "line_ratio": 1.27,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic",),
    },
    # --- Faces with a voice of their own -------------------------------------
    {
        "id": "bangers",
        "label": "Bangers",
        "file": "Bangers-Regular.ttf",
        "family": "AnimaticBangers",
        "line_ratio": 1.08,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese",),
    },
    {
        "id": "lobster",
        "label": "Lobster",
        "file": "Lobster-Regular.ttf",
        "family": "AnimaticLobster",
        "line_ratio": 1.25,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic",),
    },
    {
        "id": "caveat",
        "label": "Caveat",
        "file": "Caveat-SemiBold.ttf",
        "family": "AnimaticCaveat",
        "line_ratio": 1.26,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "cyrillic",),
    },
    {
        "id": "courier",
        "label": "Courier Prime",
        "file": "CourierPrime-Regular.ttf",
        "family": "AnimaticCourier",
        "line_ratio": 1.14,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext",),
    },
    # --- Latin, Cyrillic and Greek — the widest coverage on the list ---------
    {
        "id": "noto-sans",
        "label": "Noto Sans",
        "file": "NotoSans-SemiBold.ttf",
        "family": "AnimaticNotoSans",
        "line_ratio": 1.37,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek", "devanagari",),
    },
    {
        "id": "noto-serif",
        "label": "Noto Serif",
        "file": "NotoSerif-SemiBold.ttf",
        "family": "AnimaticNotoSerif",
        "line_ratio": 1.37,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek",),
    },
    {
        "id": "rubik",
        "label": "Rubik",
        "file": "Rubik-Bold.ttf",
        "family": "AnimaticRubik",
        "line_ratio": 1.19,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "latin-ext", "cyrillic", "arabic", "hebrew",),
    },
    {
        "id": "be-vietnam",
        "label": "Be Vietnam Pro",
        "file": "BeVietnamPro-Bold.ttf",
        "family": "AnimaticBeVietnam",
        "line_ratio": 1.27,
        "group": "latin",
        "scripts": ("latin-basic", "latin", "vietnamese",),
    },
    # --- Devanagari — हिन्दी, मराठी, नेपाली, भोजपुरी -------------------------
    {
        "id": "noto-devanagari",
        "label": "Noto Sans Devanagari",
        "file": "NotoSansDevanagari-SemiBold.ttf",
        "family": "AnimaticNotoDevanagari",
        "line_ratio": 1.31,
        "group": "devanagari",
        "scripts": ("latin-basic", "latin", "latin-ext", "devanagari",),
    },
    {
        "id": "mukta",
        "label": "Mukta",
        "file": "Mukta-Bold.ttf",
        "family": "AnimaticMukta",
        "line_ratio": 1.67,
        "group": "devanagari",
        "scripts": ("latin-basic", "latin", "latin-ext", "devanagari",),
    },
    {
        "id": "rozha",
        "label": "Rozha One",
        "file": "RozhaOne-Regular.ttf",
        "family": "AnimaticRozha",
        "line_ratio": 1.43,
        "group": "devanagari",
        "scripts": ("latin-basic", "latin", "devanagari",),
    },
    {
        "id": "baloo2",
        "label": "Baloo 2",
        "file": "Baloo2-ExtraBold.ttf",
        "family": "AnimaticBaloo2",
        "line_ratio": 1.61,
        "group": "devanagari",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "devanagari",),
    },
    {
        "id": "tiro-devanagari",
        "label": "Tiro Devanagari Hindi",
        "file": "TiroDevanagariHindi-Regular.ttf",
        "family": "AnimaticTiroDevanagari",
        "line_ratio": 1.01,
        "group": "devanagari",
        "scripts": ("latin-basic", "latin", "devanagari",),
    },
    # --- Gurmukhi — ਪੰਜਾਬੀ ---------------------------------------------------
    {
        "id": "noto-gurmukhi",
        "label": "Noto Sans Gurmukhi",
        "file": "NotoSansGurmukhi-SemiBold.ttf",
        "family": "AnimaticNotoGurmukhi",
        "line_ratio": 1.31,
        "group": "gurmukhi",
        "scripts": ("latin-basic", "latin", "latin-ext", "gurmukhi",),
    },
    {
        "id": "mukta-mahee",
        "label": "Mukta Mahee",
        "file": "MuktaMahee-Bold.ttf",
        "family": "AnimaticMuktaMahee",
        "line_ratio": 1.67,
        "group": "gurmukhi",
        "scripts": ("latin-basic", "latin", "latin-ext", "gurmukhi",),
    },
    {
        "id": "baloo-paaji",
        "label": "Baloo Paaji 2",
        "file": "BalooPaaji2-ExtraBold.ttf",
        "family": "AnimaticBalooPaaji",
        "line_ratio": 1.78,
        "group": "gurmukhi",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "gurmukhi",),
    },
    # --- Bengali — বাংলা, অসমীয়া --------------------------------------------
    {
        "id": "noto-bengali",
        "label": "Noto Sans Bengali",
        "file": "NotoSansBengali-SemiBold.ttf",
        "family": "AnimaticNotoBengali",
        "line_ratio": 1.33,
        "group": "bengali",
        "scripts": ("latin-basic", "latin", "latin-ext", "bengali",),
    },
    {
        "id": "hind-siliguri",
        "label": "Hind Siliguri",
        "file": "HindSiliguri-Bold.ttf",
        "family": "AnimaticHindSiliguri",
        "line_ratio": 1.63,
        "group": "bengali",
        "scripts": ("latin-basic", "latin", "latin-ext", "bengali",),
    },
    {
        "id": "baloo-da",
        "label": "Baloo Da 2",
        "file": "BalooDa2-ExtraBold.ttf",
        "family": "AnimaticBalooDa",
        "line_ratio": 1.69,
        "group": "bengali",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "bengali",),
    },
    # --- Gujarati — ગુજરાતી --------------------------------------------------
    {
        "id": "noto-gujarati",
        "label": "Noto Sans Gujarati",
        "file": "NotoSansGujarati-SemiBold.ttf",
        "family": "AnimaticNotoGujarati",
        "line_ratio": 1.31,
        "group": "gujarati",
        "scripts": ("latin-basic", "latin", "latin-ext", "gujarati",),
    },
    {
        "id": "baloo-bhai",
        "label": "Baloo Bhai 2",
        "file": "BalooBhai2-ExtraBold.ttf",
        "family": "AnimaticBalooBhai",
        "line_ratio": 1.63,
        "group": "gujarati",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "gujarati",),
    },
    # --- Odia — ଓଡ଼ିଆ --------------------------------------------------------
    {
        "id": "noto-odia",
        "label": "Noto Sans Oriya",
        "file": "NotoSansOriya-SemiBold.ttf",
        "family": "AnimaticNotoOdia",
        "line_ratio": 1.37,
        "group": "odia",
        "scripts": ("latin-basic", "latin", "latin-ext", "odia",),
    },
    # --- Tamil — தமிழ் -------------------------------------------------------
    {
        "id": "noto-tamil",
        "label": "Noto Sans Tamil",
        "file": "NotoSansTamil-SemiBold.ttf",
        "family": "AnimaticNotoTamil",
        "line_ratio": 1.24,
        "group": "tamil",
        "scripts": ("latin-basic", "latin", "latin-ext", "tamil",),
    },
    {
        "id": "mukta-malar",
        "label": "Mukta Malar",
        "file": "MuktaMalar-Bold.ttf",
        "family": "AnimaticMuktaMalar",
        "line_ratio": 1.67,
        "group": "tamil",
        "scripts": ("latin-basic", "latin", "latin-ext", "tamil",),
    },
    {
        "id": "baloo-thambi",
        "label": "Baloo Thambi 2",
        "file": "BalooThambi2-ExtraBold.ttf",
        "family": "AnimaticBalooThambi",
        "line_ratio": 1.57,
        "group": "tamil",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "tamil",),
    },
    # --- Telugu — తెలుగు -----------------------------------------------------
    {
        "id": "noto-telugu",
        "label": "Noto Sans Telugu",
        "file": "NotoSansTelugu-SemiBold.ttf",
        "family": "AnimaticNotoTelugu",
        "line_ratio": 1.36,
        "group": "telugu",
        "scripts": ("latin-basic", "latin", "latin-ext", "telugu",),
    },
    {
        "id": "baloo-tammudu",
        "label": "Baloo Tammudu 2",
        "file": "BalooTammudu2-ExtraBold.ttf",
        "family": "AnimaticBalooTammudu",
        "line_ratio": 2.3,
        "group": "telugu",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "telugu",),
    },
    # --- Kannada — ಕನ್ನಡ -----------------------------------------------------
    {
        "id": "noto-kannada",
        "label": "Noto Sans Kannada",
        "file": "NotoSansKannada-SemiBold.ttf",
        "family": "AnimaticNotoKannada",
        "line_ratio": 1.35,
        "group": "kannada",
        "scripts": ("latin-basic", "latin", "latin-ext", "kannada",),
    },
    {
        "id": "baloo-tamma",
        "label": "Baloo Tamma 2",
        "file": "BalooTamma2-ExtraBold.ttf",
        "family": "AnimaticBalooTamma",
        "line_ratio": 1.76,
        "group": "kannada",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "kannada",),
    },
    # --- Malayalam — മലയാളം --------------------------------------------------
    {
        "id": "noto-malayalam",
        "label": "Noto Sans Malayalam",
        "file": "NotoSansMalayalam-SemiBold.ttf",
        "family": "AnimaticNotoMalayalam",
        "line_ratio": 1.26,
        "group": "malayalam",
        "scripts": ("latin-basic", "latin", "latin-ext", "malayalam",),
    },
    {
        "id": "baloo-chettan",
        "label": "Baloo Chettan 2",
        "file": "BalooChettan2-ExtraBold.ttf",
        "family": "AnimaticBalooChettan",
        "line_ratio": 1.48,
        "group": "malayalam",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "malayalam",),
    },
    # --- Arabic — العربية ----------------------------------------------------
    {
        "id": "noto-arabic",
        "label": "Noto Sans Arabic",
        "file": "NotoSansArabic-SemiBold.ttf",
        "family": "AnimaticNotoArabic",
        "line_ratio": 2.12,
        "group": "arabic",
        "scripts": ("latin-basic", "latin", "latin-ext", "arabic", "urdu",),
    },
    {
        "id": "cairo",
        "label": "Cairo",
        "file": "Cairo-Bold.ttf",
        "family": "AnimaticCairo",
        "line_ratio": 1.89,
        "group": "arabic",
        "scripts": ("latin-basic", "latin", "latin-ext", "arabic", "urdu",),
    },
    {
        "id": "amiri",
        "label": "Amiri",
        "file": "Amiri-Bold.ttf",
        "family": "AnimaticAmiri",
        "line_ratio": 1.77,
        "group": "arabic",
        "scripts": ("latin-basic", "latin", "arabic", "urdu",),
    },
    # --- Urdu — اردو (nastaliq, which Arabic naskh cannot stand in for) ------
    {
        "id": "noto-nastaliq",
        "label": "Noto Nastaliq Urdu",
        "file": "NotoNastaliqUrdu-Bold.ttf",
        "family": "AnimaticNotoNastaliq",
        "line_ratio": 2.51,
        "group": "urdu",
        "scripts": ("latin-basic", "latin", "latin-ext", "arabic", "urdu",),
    },
    # --- Hebrew — עברית ------------------------------------------------------
    {
        "id": "noto-hebrew",
        "label": "Noto Sans Hebrew",
        "file": "NotoSansHebrew-SemiBold.ttf",
        "family": "AnimaticNotoHebrew",
        "line_ratio": 1.37,
        "group": "hebrew",
        "scripts": ("latin-basic", "latin", "latin-ext", "hebrew",),
    },
    {
        "id": "heebo",
        "label": "Heebo",
        "file": "Heebo-Bold.ttf",
        "family": "AnimaticHeebo",
        "line_ratio": 1.48,
        "group": "hebrew",
        "scripts": ("latin-basic", "latin", "latin-ext", "hebrew",),
    },
    # --- Thai — ไทย ----------------------------------------------------------
    {
        "id": "noto-thai",
        "label": "Noto Sans Thai",
        "file": "NotoSansThai-SemiBold.ttf",
        "family": "AnimaticNotoThai",
        "line_ratio": 1.52,
        "group": "thai",
        "scripts": ("latin-basic", "latin", "latin-ext", "thai",),
    },
    {
        "id": "kanit",
        "label": "Kanit",
        "file": "Kanit-Bold.ttf",
        "family": "AnimaticKanit",
        "line_ratio": 1.5,
        "group": "thai",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "thai",),
    },
    # --- Chinese, Japanese, Korean -------------------------------------------
    {
        "id": "noto-sc",
        "label": "Noto Sans SC",
        "file": "NotoSansSC-Bold.ttf",
        "family": "AnimaticNotoSc",
        "line_ratio": 1.45,
        "group": "han-sc",
        "scripts": ("latin-basic", "latin", "vietnamese", "han-sc", "han-tc", "han-jp", "kana",),
    },
    {
        "id": "noto-serif-sc",
        "label": "Noto Serif SC",
        "file": "NotoSerifSC-Bold.ttf",
        "family": "AnimaticNotoSerifSc",
        "line_ratio": 1.45,
        "group": "han-sc",
        "scripts": ("latin-basic", "latin", "vietnamese", "han-sc", "han-tc", "han-jp", "kana",),
    },
    {
        "id": "noto-tc",
        "label": "Noto Sans TC",
        "file": "NotoSansTC-Bold.ttf",
        "family": "AnimaticNotoTc",
        "line_ratio": 1.45,
        "group": "han-tc",
        "scripts": ("latin-basic", "latin", "vietnamese", "han-tc", "kana",),
    },
    {
        "id": "noto-jp",
        "label": "Noto Sans JP",
        "file": "NotoSansJP-Bold.ttf",
        "family": "AnimaticNotoJp",
        "line_ratio": 1.45,
        "group": "kana",
        "scripts": ("latin-basic", "latin", "vietnamese", "han-tc", "han-jp", "kana",),
    },
    {
        "id": "mplus-rounded",
        "label": "M PLUS Rounded 1c",
        "file": "MPLUSRounded1c-Bold.ttf",
        "family": "AnimaticMplusRounded",
        "line_ratio": 1.4,
        "group": "kana",
        "scripts": ("latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek", "hebrew", "han-jp", "kana",),
    },
    {
        "id": "noto-kr",
        "label": "Noto Sans KR",
        "file": "NotoSansKR-Bold.ttf",
        "family": "AnimaticNotoKr",
        "line_ratio": 1.45,
        "group": "hangul",
        "scripts": ("latin-basic", "latin", "vietnamese", "kana", "hangul",),
    },
    {
        "id": "black-han",
        "label": "Black Han Sans",
        "file": "BlackHanSans-Regular.ttf",
        "family": "AnimaticBlackHan",
        "line_ratio": 1.0,
        "group": "hangul",
        "scripts": ("latin-basic", "hangul",),
    },
)

FONT_IDS = tuple(f["id"] for f in FONTS)

# The one every caption written before this phase is set in, and what an
# unrecognised id folds down to. Same forgiveness `ease`, `clip_kind` and the
# transition kinds get: a project written by a newer client still opens and
# still exports rather than being lost over one word.
DEFAULT_FONT = "inter"


def font_entry(font_id: str | None) -> dict:
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


def script_entry(script_id: str) -> dict | None:
    """The `SCRIPTS` row for `script_id`, or None. Mirrors `scriptEntry`."""
    for script in SCRIPTS:
        if script["id"] == script_id:
            return script
    return None


def scripts_of(text: str) -> list[str]:
    """The writing systems `text` needs a font to have. Mirrors `scriptsOf`.

    In `SCRIPTS` order, so the answer is stable and reads the way the table
    does. Whitespace is skipped — every font has a space, and a caption that is
    all spaces should not demand Latin of a Korean face.

    ⚠ A CHARACTER NO RANGE CLAIMS DEMANDS NOTHING. Emoji, symbols and the more
    obscure blocks fall through here rather than being reported as an
    unsatisfiable requirement — the alternative is a warning on every caption
    with a ✓ in it, which teaches people to ignore warnings.
    """
    found: list[str] = []
    for char in text or "":
        if char.isspace():
            continue
        code = ord(char)
        for script in SCRIPTS:
            if any(low <= code <= high for low, high in script["ranges"]):
                if script["id"] not in found:
                    found.append(script["id"])
                break
    return [s["id"] for s in SCRIPTS if s["id"] in found]


def covers(font_id: str | None, text: str) -> bool:
    """Can this face draw every character of `text`? Mirrors `covers`."""
    return not missing_scripts(font_id, text)


def missing_scripts(font_id: str | None, text: str) -> list[str]:
    """The writing systems `text` needs that this face has not got.

    ⚠ THE ANSWER THE UI SHOWS, so it names writing systems rather than
    characters: "this font has no Devanagari" is something a user can act on,
    and "U+0939 is missing" is not.
    """
    have = set(font_entry(font_id)["scripts"])
    gaps: list[str] = []
    for script_id in scripts_of(text):
        script = script_entry(script_id)
        alternatives = set(script["any_of"]) if script else set()
        if script_id in have:
            continue
        if alternatives and alternatives & have:
            continue
        gaps.append(script_id)
    return gaps


def fonts_for_text(text: str) -> list[dict]:
    """Every face that can draw `text`, in list order. Mirrors `fontsForText`."""
    return [f for f in FONTS if covers(f["id"], text)]


def best_font_for_text(text: str, prefer: str | None = None) -> str:
    """The font id to set `text` in — `prefer` if it can draw it, else the best fit.

    ⚠ THIS IS WHAT STOPS THE AI SHIPPING ▯▯▯. The chat agent and the director
    write captions in whatever language the film is in and have no idea which
    of fifty-six faces has Gurmukhi in it. Rather than teaching every caller
    that, a caption that names no font — or names one that cannot draw its own
    text — is resolved HERE, once.

    `prefer` wins whenever it fits, so an explicit choice by a human is never
    quietly overridden; it is only replaced when it would have rendered as
    empty boxes.

    ⚠ "FITS" IS NOT ENOUGH TO CHOOSE BY, which is what the ranking below is
    for. Rubik has Arabic in it and sits near the top of the list, so a plain
    first-that-fits walk set every Arabic title in a Latin face that happens to
    carry the alphabet. A font is preferred when the SHELF it was filed on is
    one of the writing systems the text actually needs — that is the editorial
    judgement "this face was chosen for Arabic", which is exactly the question
    being asked. Two tiers, because Han is classified as one script and shelved
    as three: an exact shelf match beats a regional one, so 日本の映画 lands on
    the Japanese face rather than the Simplified Chinese one that also covers
    it.
    """
    if prefer and covers(prefer, text):
        return prefer
    fits = [f for f in FONTS if covers(f["id"], text)]
    if not fits:
        return DEFAULT_FONT
    required = scripts_of(text)
    regional = {
        alt
        for script_id in required
        for alt in (script_entry(script_id) or {"any_of": ()})["any_of"]
    }
    # ⚠ THE REQUIRED SCRIPTS ARE WALKED IN `SCRIPTS` ORDER, NOT THE FONTS.
    # `scripts_of` already returns them most-specific-first, and that ordering
    # is the answer to a real question: اردو کی کہانی needs both `urdu` and
    # `arabic`, every Arabic face covers it, and setting Urdu in naskh is a
    # thing Urdu readers experience as wrong rather than as a style. Walking
    # the scripts puts the nastaliq shelf first because `urdu` is above
    # `arabic` in the table. The same reasoning gives Japanese the Japanese
    # face rather than the Simplified Chinese one that also covers its kana.
    for script_id in required:
        for font in fits:
            if font["group"] == script_id:
                return str(font["id"])
    for font in fits:
        if font["group"] in regional:
            return str(font["id"])
    # Nothing was shelved for this writing system — Cyrillic and Greek have no
    # shelf of their own — so the list order decides, which puts an English or
    # Russian caption on Inter exactly as it always did.
    return str(fits[0]["id"])


def needs_shaping(text: str) -> bool:
    """Does drawing `text` need HarfBuzz? Delegates to `text_shaping`."""
    return text_shaping.needs_shaping(text)


SHAPING_AVAILABLE = text_shaping.AVAILABLE
