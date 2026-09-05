/**
 * The fonts a caption can be set in — the BROWSER's half of the list.
 *
 * ⚠ THIS FILE HAS A TWIN: `animatic_fonts.py`. The two lists must be
 * element-for-element identical, and `tests/captions_check.py` fails if they
 * are not. Read that module's docstring first: it explains why the font has to
 * be a file that ships with the project rather than a name each side looks up
 * on the machine it happens to be running on — it is the same class of bug as
 * the scene model being written twice, and it shows up as a caption that wraps
 * onto three lines in the MP4 and two in the monitor.
 *
 * `family` is the CSS name each file is registered under, and it is
 * deliberately NOT the font's real family name: a user with Inter installed
 * would otherwise get the system copy, which is precisely the divergence this
 * exists to prevent.
 *
 * ⚠ `scripts` IS MEASURED, NOT DECLARED. Every entry's list of writing systems
 * was read out of the .ttf's own cmap by `tools/fonts_sync.py`. Nothing here is
 * a claim about a font; it is a reading of one. That is what lets `covers()`
 * tell someone their Hindi title is about to render as ▯▯▯ BEFORE they pay for
 * the export rather than after.
 */

/**
 * The writing systems, and how a string is sorted into them.
 *
 * ⚠ ELEMENT FOR ELEMENT the same as SCRIPTS in `animatic_fonts.py`, which
 * carries the full note on why the ORDER of this list is its priority (`urdu`
 * above `arabic`, `vietnamese` above `latin-ext`) and what `any_of` is for.
 * In one line: the first entry whose range contains a character claims it, and
 * Han is the one script no font declares because three regions' fonts cover
 * genuinely different sets of it.
 */
export const SCRIPTS = [
  {
    id: "urdu",
    label: "Urdu",
    note: "اردو, فارسی",
    shaped: true,
    any_of: [],
    ranges: [
      [0x0679, 0x0679], [0x067E, 0x067E], [0x0686, 0x0686], [0x0688, 0x0688],
      [0x0691, 0x0691], [0x0698, 0x0698], [0x06A9, 0x06A9], [0x06AF, 0x06AF],
      [0x06BA, 0x06BA], [0x06BE, 0x06BE], [0x06C0, 0x06C3], [0x06CC, 0x06CC],
      [0x06D2, 0x06D3]
    ],
  },
  {
    id: "arabic",
    label: "Arabic",
    note: "العربية",
    shaped: true,
    any_of: [],
    ranges: [[0x0600, 0x06FF], [0x0750, 0x077F], [0xFB50, 0xFDFF], [0xFE70, 0xFEFF]],
  },
  {
    id: "hebrew",
    label: "Hebrew",
    note: "עברית",
    shaped: true,
    any_of: [],
    ranges: [[0x0590, 0x05FF], [0xFB1D, 0xFB4F]],
  },
  {
    id: "devanagari",
    label: "Devanagari",
    note: "हिन्दी, मराठी, नेपाली, भोजपुरी, संस्कृत",
    shaped: true,
    any_of: [],
    ranges: [[0x0900, 0x097F], [0xA8E0, 0xA8FF]],
  },
  {
    id: "gurmukhi",
    label: "Gurmukhi",
    note: "ਪੰਜਾਬੀ",
    shaped: true,
    any_of: [],
    ranges: [[0x0A00, 0x0A7F]],
  },
  {
    id: "bengali",
    label: "Bengali",
    note: "বাংলা, অসমীয়া",
    shaped: true,
    any_of: [],
    ranges: [[0x0980, 0x09FF]],
  },
  {
    id: "gujarati",
    label: "Gujarati",
    note: "ગુજરાતી",
    shaped: true,
    any_of: [],
    ranges: [[0x0A80, 0x0AFF]],
  },
  {
    id: "odia",
    label: "Odia",
    note: "ଓଡ଼ିଆ",
    shaped: true,
    any_of: [],
    ranges: [[0x0B00, 0x0B7F]],
  },
  {
    id: "tamil",
    label: "Tamil",
    note: "தமிழ்",
    shaped: true,
    any_of: [],
    ranges: [[0x0B80, 0x0BFF]],
  },
  {
    id: "telugu",
    label: "Telugu",
    note: "తెలుగు",
    shaped: true,
    any_of: [],
    ranges: [[0x0C00, 0x0C7F]],
  },
  {
    id: "kannada",
    label: "Kannada",
    note: "ಕನ್ನಡ",
    shaped: true,
    any_of: [],
    ranges: [[0x0C80, 0x0CFF]],
  },
  {
    id: "malayalam",
    label: "Malayalam",
    note: "മലയാളം",
    shaped: true,
    any_of: [],
    ranges: [[0x0D00, 0x0D7F]],
  },
  {
    id: "thai",
    label: "Thai",
    note: "ไทย",
    shaped: true,
    any_of: [],
    ranges: [[0x0E00, 0x0E7F]],
  },
  {
    id: "kana",
    label: "Japanese",
    note: "日本語 — hiragana and katakana",
    shaped: false,
    any_of: [],
    ranges: [[0x3040, 0x30FF], [0x31F0, 0x31FF], [0xFF66, 0xFF9F]],
  },
  {
    id: "hangul",
    label: "Korean",
    note: "한국어",
    shaped: false,
    any_of: [],
    ranges: [[0x1100, 0x11FF], [0x3130, 0x318F], [0xA960, 0xA97F], [0xAC00, 0xD7AF]],
  },
  {
    id: "han",
    label: "Chinese characters",
    note: "汉字 / 漢字",
    shaped: false,
    any_of: ["han-sc", "han-tc", "han-jp"],
    ranges: [[0x3000, 0x303F], [0x3400, 0x4DBF], [0x4E00, 0x9FFF], [0xF900, 0xFAFF]],
  },
  {
    id: "greek",
    label: "Greek",
    note: "Ελληνικά",
    shaped: false,
    any_of: [],
    ranges: [[0x0370, 0x03FF], [0x1F00, 0x1FFF]],
  },
  {
    id: "cyrillic",
    label: "Cyrillic",
    note: "Русский, Українська, Български, Српски",
    shaped: false,
    any_of: [],
    ranges: [[0x0400, 0x052F], [0x2DE0, 0x2DFF], [0xA640, 0xA69F]],
  },
  {
    id: "vietnamese",
    label: "Vietnamese",
    note: "Tiếng Việt",
    shaped: false,
    any_of: [],
    ranges: [
      [0x0102, 0x0103], [0x0110, 0x0111], [0x01A0, 0x01A1], [0x01AF, 0x01B0],
      [0x1EA0, 0x1EF9]
    ],
  },
  {
    id: "latin-ext",
    label: "Latin extended",
    note: "Polski, Türkçe, Čeština, Magyar, Română",
    shaped: false,
    any_of: [],
    ranges: [[0x0100, 0x024F], [0x1E00, 0x1EFF]],
  },
  {
    id: "latin",
    label: "Latin",
    note: "English, Español, Français, Deutsch, Italiano, Português",
    shaped: false,
    any_of: [],
    ranges: [[0x00A0, 0x00FF], [0x2010, 0x2027], [0x20A0, 0x20BF]],
  },
  {
    id: "latin-basic",
    label: "Basic Latin",
    note: "A–Z, 0–9 and punctuation",
    shaped: false,
    any_of: [],
    ranges: [[0x0021, 0x007E]],
  },
  {
    id: "han-sc",
    label: "Chinese (Simplified)",
    note: "简体中文",
    shaped: false,
    any_of: [],
    ranges: [],
  },
  {
    id: "han-tc",
    label: "Chinese (Traditional)",
    note: "繁體中文",
    shaped: false,
    any_of: [],
    ranges: [],
  },
  {
    id: "han-jp",
    label: "Japanese kanji",
    note: "漢字",
    shaped: false,
    any_of: [],
    ranges: [],
  },
];

export const SCRIPT_IDS = SCRIPTS.map((s) => s.id);

/**
 * ⚠ ELEMENT FOR ELEMENT the same as FONTS in `animatic_fonts.py`.
 *
 * `line_ratio` is (ascent + descent) ÷ font size for the file — read that
 * module for why it is on the list and what it fixes. In one line: the exporter
 * steps its baselines by `(ascent + descent) × line_height` and CSS
 * `line-height` is a multiple of the FONT SIZE, so `lineHeight` in the browser
 * has to be multiplied by this to be the same distance. `captionStyle` is the
 * only caller.
 *
 * `group` is the shelf the picker files a face on — one of the ids in
 * `SCRIPTS`. It is an editorial choice rather than a fact about the file: Noto
 * Sans happens to contain Devanagari and is still a Latin font.
 */
export const FONTS = [
  // ⚠ INTER IS FIRST AND HAS TO STAY FIRST — `fontEntry` falls back to
  // `FONTS[0]`, and `DEFAULT_FONT` below names the same one.
  // --- Sans, for a caption you are meant to READ rather than look at -----
  {
    id: "inter",
    label: "Inter",
    file: "Inter-SemiBold.ttf",
    family: "AnimaticInter",
    line_ratio: 1.22,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek"],
  },
  {
    id: "montserrat",
    label: "Montserrat",
    file: "Montserrat-SemiBold.ttf",
    family: "AnimaticMontserrat",
    line_ratio: 1.23,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic"],
  },
  {
    id: "poppins",
    label: "Poppins",
    file: "Poppins-SemiBold.ttf",
    family: "AnimaticPoppins",
    line_ratio: 1.4,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "devanagari"],
  },
  {
    id: "nunito",
    label: "Nunito",
    file: "Nunito-Bold.ttf",
    family: "AnimaticNunito",
    line_ratio: 1.38,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic"],
  },
  // --- Condensed and heavy, for a title card or a lower third ------------
  {
    id: "anton",
    label: "Anton",
    file: "Anton-Regular.ttf",
    family: "AnimaticAnton",
    line_ratio: 1.51,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese"],
  },
  {
    id: "bebas",
    label: "Bebas Neue",
    file: "BebasNeue-Regular.ttf",
    family: "AnimaticBebas",
    line_ratio: 1.2,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext"],
  },
  {
    id: "oswald",
    label: "Oswald",
    file: "Oswald-Medium.ttf",
    family: "AnimaticOswald",
    line_ratio: 1.49,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic"],
  },
  {
    id: "archivo",
    label: "Archivo Black",
    file: "ArchivoBlack-Regular.ttf",
    family: "AnimaticArchivo",
    line_ratio: 1.09,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext"],
  },
  // --- Serif -------------------------------------------------------------
  {
    id: "playfair",
    label: "Playfair Display",
    file: "PlayfairDisplay-SemiBold.ttf",
    family: "AnimaticPlayfair",
    line_ratio: 1.35,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic"],
  },
  {
    id: "merriweather",
    label: "Merriweather",
    file: "Merriweather-Bold.ttf",
    family: "AnimaticMerriweather",
    line_ratio: 1.27,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic"],
  },
  // --- Faces with a voice of their own -----------------------------------
  {
    id: "bangers",
    label: "Bangers",
    file: "Bangers-Regular.ttf",
    family: "AnimaticBangers",
    line_ratio: 1.08,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese"],
  },
  {
    id: "lobster",
    label: "Lobster",
    file: "Lobster-Regular.ttf",
    family: "AnimaticLobster",
    line_ratio: 1.25,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic"],
  },
  {
    id: "caveat",
    label: "Caveat",
    file: "Caveat-SemiBold.ttf",
    family: "AnimaticCaveat",
    line_ratio: 1.26,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "cyrillic"],
  },
  {
    id: "courier",
    label: "Courier Prime",
    file: "CourierPrime-Regular.ttf",
    family: "AnimaticCourier",
    line_ratio: 1.14,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext"],
  },
  // --- Latin, Cyrillic and Greek — the widest coverage on the list -------
  {
    id: "noto-sans",
    label: "Noto Sans",
    file: "NotoSans-SemiBold.ttf",
    family: "AnimaticNotoSans",
    line_ratio: 1.37,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek", "devanagari"],
  },
  {
    id: "noto-serif",
    label: "Noto Serif",
    file: "NotoSerif-SemiBold.ttf",
    family: "AnimaticNotoSerif",
    line_ratio: 1.37,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek"],
  },
  {
    id: "rubik",
    label: "Rubik",
    file: "Rubik-Bold.ttf",
    family: "AnimaticRubik",
    line_ratio: 1.19,
    group: "latin",
    scripts: ["latin-basic", "latin", "latin-ext", "cyrillic", "arabic", "hebrew"],
  },
  {
    id: "be-vietnam",
    label: "Be Vietnam Pro",
    file: "BeVietnamPro-Bold.ttf",
    family: "AnimaticBeVietnam",
    line_ratio: 1.27,
    group: "latin",
    scripts: ["latin-basic", "latin", "vietnamese"],
  },
  // --- Devanagari — हिन्दी, मराठी, नेपाली, भोजपुरी -----------------------
  {
    id: "noto-devanagari",
    label: "Noto Sans Devanagari",
    file: "NotoSansDevanagari-SemiBold.ttf",
    family: "AnimaticNotoDevanagari",
    line_ratio: 1.31,
    group: "devanagari",
    scripts: ["latin-basic", "latin", "latin-ext", "devanagari"],
  },
  {
    id: "mukta",
    label: "Mukta",
    file: "Mukta-Bold.ttf",
    family: "AnimaticMukta",
    line_ratio: 1.67,
    group: "devanagari",
    scripts: ["latin-basic", "latin", "latin-ext", "devanagari"],
  },
  {
    id: "rozha",
    label: "Rozha One",
    file: "RozhaOne-Regular.ttf",
    family: "AnimaticRozha",
    line_ratio: 1.43,
    group: "devanagari",
    scripts: ["latin-basic", "latin", "devanagari"],
  },
  {
    id: "baloo2",
    label: "Baloo 2",
    file: "Baloo2-ExtraBold.ttf",
    family: "AnimaticBaloo2",
    line_ratio: 1.61,
    group: "devanagari",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "devanagari"],
  },
  {
    id: "tiro-devanagari",
    label: "Tiro Devanagari Hindi",
    file: "TiroDevanagariHindi-Regular.ttf",
    family: "AnimaticTiroDevanagari",
    line_ratio: 1.01,
    group: "devanagari",
    scripts: ["latin-basic", "latin", "devanagari"],
  },
  // --- Gurmukhi — ਪੰਜਾਬੀ -------------------------------------------------
  {
    id: "noto-gurmukhi",
    label: "Noto Sans Gurmukhi",
    file: "NotoSansGurmukhi-SemiBold.ttf",
    family: "AnimaticNotoGurmukhi",
    line_ratio: 1.31,
    group: "gurmukhi",
    scripts: ["latin-basic", "latin", "latin-ext", "gurmukhi"],
  },
  {
    id: "mukta-mahee",
    label: "Mukta Mahee",
    file: "MuktaMahee-Bold.ttf",
    family: "AnimaticMuktaMahee",
    line_ratio: 1.67,
    group: "gurmukhi",
    scripts: ["latin-basic", "latin", "latin-ext", "gurmukhi"],
  },
  {
    id: "baloo-paaji",
    label: "Baloo Paaji 2",
    file: "BalooPaaji2-ExtraBold.ttf",
    family: "AnimaticBalooPaaji",
    line_ratio: 1.78,
    group: "gurmukhi",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "gurmukhi"],
  },
  // --- Bengali — বাংলা, অসমীয়া ------------------------------------------
  {
    id: "noto-bengali",
    label: "Noto Sans Bengali",
    file: "NotoSansBengali-SemiBold.ttf",
    family: "AnimaticNotoBengali",
    line_ratio: 1.33,
    group: "bengali",
    scripts: ["latin-basic", "latin", "latin-ext", "bengali"],
  },
  {
    id: "hind-siliguri",
    label: "Hind Siliguri",
    file: "HindSiliguri-Bold.ttf",
    family: "AnimaticHindSiliguri",
    line_ratio: 1.63,
    group: "bengali",
    scripts: ["latin-basic", "latin", "latin-ext", "bengali"],
  },
  {
    id: "baloo-da",
    label: "Baloo Da 2",
    file: "BalooDa2-ExtraBold.ttf",
    family: "AnimaticBalooDa",
    line_ratio: 1.69,
    group: "bengali",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "bengali"],
  },
  // --- Gujarati — ગુજરાતી ------------------------------------------------
  {
    id: "noto-gujarati",
    label: "Noto Sans Gujarati",
    file: "NotoSansGujarati-SemiBold.ttf",
    family: "AnimaticNotoGujarati",
    line_ratio: 1.31,
    group: "gujarati",
    scripts: ["latin-basic", "latin", "latin-ext", "gujarati"],
  },
  {
    id: "baloo-bhai",
    label: "Baloo Bhai 2",
    file: "BalooBhai2-ExtraBold.ttf",
    family: "AnimaticBalooBhai",
    line_ratio: 1.63,
    group: "gujarati",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "gujarati"],
  },
  // --- Odia — ଓଡ଼ିଆ ------------------------------------------------------
  {
    id: "noto-odia",
    label: "Noto Sans Oriya",
    file: "NotoSansOriya-SemiBold.ttf",
    family: "AnimaticNotoOdia",
    line_ratio: 1.37,
    group: "odia",
    scripts: ["latin-basic", "latin", "latin-ext", "odia"],
  },
  // --- Tamil — தமிழ் -----------------------------------------------------
  {
    id: "noto-tamil",
    label: "Noto Sans Tamil",
    file: "NotoSansTamil-SemiBold.ttf",
    family: "AnimaticNotoTamil",
    line_ratio: 1.24,
    group: "tamil",
    scripts: ["latin-basic", "latin", "latin-ext", "tamil"],
  },
  {
    id: "mukta-malar",
    label: "Mukta Malar",
    file: "MuktaMalar-Bold.ttf",
    family: "AnimaticMuktaMalar",
    line_ratio: 1.67,
    group: "tamil",
    scripts: ["latin-basic", "latin", "latin-ext", "tamil"],
  },
  {
    id: "baloo-thambi",
    label: "Baloo Thambi 2",
    file: "BalooThambi2-ExtraBold.ttf",
    family: "AnimaticBalooThambi",
    line_ratio: 1.57,
    group: "tamil",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "tamil"],
  },
  // --- Telugu — తెలుగు ---------------------------------------------------
  {
    id: "noto-telugu",
    label: "Noto Sans Telugu",
    file: "NotoSansTelugu-SemiBold.ttf",
    family: "AnimaticNotoTelugu",
    line_ratio: 1.36,
    group: "telugu",
    scripts: ["latin-basic", "latin", "latin-ext", "telugu"],
  },
  {
    id: "baloo-tammudu",
    label: "Baloo Tammudu 2",
    file: "BalooTammudu2-ExtraBold.ttf",
    family: "AnimaticBalooTammudu",
    line_ratio: 2.3,
    group: "telugu",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "telugu"],
  },
  // --- Kannada — ಕನ್ನಡ ---------------------------------------------------
  {
    id: "noto-kannada",
    label: "Noto Sans Kannada",
    file: "NotoSansKannada-SemiBold.ttf",
    family: "AnimaticNotoKannada",
    line_ratio: 1.35,
    group: "kannada",
    scripts: ["latin-basic", "latin", "latin-ext", "kannada"],
  },
  {
    id: "baloo-tamma",
    label: "Baloo Tamma 2",
    file: "BalooTamma2-ExtraBold.ttf",
    family: "AnimaticBalooTamma",
    line_ratio: 1.76,
    group: "kannada",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "kannada"],
  },
  // --- Malayalam — മലയാളം ------------------------------------------------
  {
    id: "noto-malayalam",
    label: "Noto Sans Malayalam",
    file: "NotoSansMalayalam-SemiBold.ttf",
    family: "AnimaticNotoMalayalam",
    line_ratio: 1.26,
    group: "malayalam",
    scripts: ["latin-basic", "latin", "latin-ext", "malayalam"],
  },
  {
    id: "baloo-chettan",
    label: "Baloo Chettan 2",
    file: "BalooChettan2-ExtraBold.ttf",
    family: "AnimaticBalooChettan",
    line_ratio: 1.48,
    group: "malayalam",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "malayalam"],
  },
  // --- Arabic — العربية --------------------------------------------------
  {
    id: "noto-arabic",
    label: "Noto Sans Arabic",
    file: "NotoSansArabic-SemiBold.ttf",
    family: "AnimaticNotoArabic",
    line_ratio: 2.12,
    group: "arabic",
    scripts: ["latin-basic", "latin", "latin-ext", "arabic", "urdu"],
  },
  {
    id: "cairo",
    label: "Cairo",
    file: "Cairo-Bold.ttf",
    family: "AnimaticCairo",
    line_ratio: 1.89,
    group: "arabic",
    scripts: ["latin-basic", "latin", "latin-ext", "arabic", "urdu"],
  },
  {
    id: "amiri",
    label: "Amiri",
    file: "Amiri-Bold.ttf",
    family: "AnimaticAmiri",
    line_ratio: 1.77,
    group: "arabic",
    scripts: ["latin-basic", "latin", "arabic", "urdu"],
  },
  // --- Urdu — اردو (nastaliq, which Arabic naskh cannot stand in for) ----
  {
    id: "noto-nastaliq",
    label: "Noto Nastaliq Urdu",
    file: "NotoNastaliqUrdu-Bold.ttf",
    family: "AnimaticNotoNastaliq",
    line_ratio: 2.51,
    group: "urdu",
    scripts: ["latin-basic", "latin", "latin-ext", "arabic", "urdu"],
  },
  // --- Hebrew — עברית ----------------------------------------------------
  {
    id: "noto-hebrew",
    label: "Noto Sans Hebrew",
    file: "NotoSansHebrew-SemiBold.ttf",
    family: "AnimaticNotoHebrew",
    line_ratio: 1.37,
    group: "hebrew",
    scripts: ["latin-basic", "latin", "latin-ext", "hebrew"],
  },
  {
    id: "heebo",
    label: "Heebo",
    file: "Heebo-Bold.ttf",
    family: "AnimaticHeebo",
    line_ratio: 1.48,
    group: "hebrew",
    scripts: ["latin-basic", "latin", "latin-ext", "hebrew"],
  },
  // --- Thai — ไทย --------------------------------------------------------
  {
    id: "noto-thai",
    label: "Noto Sans Thai",
    file: "NotoSansThai-SemiBold.ttf",
    family: "AnimaticNotoThai",
    line_ratio: 1.52,
    group: "thai",
    scripts: ["latin-basic", "latin", "latin-ext", "thai"],
  },
  {
    id: "kanit",
    label: "Kanit",
    file: "Kanit-Bold.ttf",
    family: "AnimaticKanit",
    line_ratio: 1.5,
    group: "thai",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "thai"],
  },
  // --- Chinese, Japanese, Korean -----------------------------------------
  {
    id: "noto-sc",
    label: "Noto Sans SC",
    file: "NotoSansSC-Bold.ttf",
    family: "AnimaticNotoSc",
    line_ratio: 1.45,
    group: "han-sc",
    scripts: ["latin-basic", "latin", "vietnamese", "han-sc", "han-tc", "han-jp", "kana"],
  },
  {
    id: "noto-serif-sc",
    label: "Noto Serif SC",
    file: "NotoSerifSC-Bold.ttf",
    family: "AnimaticNotoSerifSc",
    line_ratio: 1.45,
    group: "han-sc",
    scripts: ["latin-basic", "latin", "vietnamese", "han-sc", "han-tc", "han-jp", "kana"],
  },
  {
    id: "noto-tc",
    label: "Noto Sans TC",
    file: "NotoSansTC-Bold.ttf",
    family: "AnimaticNotoTc",
    line_ratio: 1.45,
    group: "han-tc",
    scripts: ["latin-basic", "latin", "vietnamese", "han-tc", "kana"],
  },
  {
    id: "noto-jp",
    label: "Noto Sans JP",
    file: "NotoSansJP-Bold.ttf",
    family: "AnimaticNotoJp",
    line_ratio: 1.45,
    group: "kana",
    scripts: ["latin-basic", "latin", "vietnamese", "han-tc", "han-jp", "kana"],
  },
  {
    id: "mplus-rounded",
    label: "M PLUS Rounded 1c",
    file: "MPLUSRounded1c-Bold.ttf",
    family: "AnimaticMplusRounded",
    line_ratio: 1.4,
    group: "kana",
    scripts: ["latin-basic", "latin", "latin-ext", "vietnamese", "cyrillic", "greek", "hebrew", "han-jp", "kana"],
  },
  {
    id: "noto-kr",
    label: "Noto Sans KR",
    file: "NotoSansKR-Bold.ttf",
    family: "AnimaticNotoKr",
    line_ratio: 1.45,
    group: "hangul",
    scripts: ["latin-basic", "latin", "vietnamese", "kana", "hangul"],
  },
  {
    id: "black-han",
    label: "Black Han Sans",
    file: "BlackHanSans-Regular.ttf",
    family: "AnimaticBlackHan",
    line_ratio: 1.0,
    group: "hangul",
    scripts: ["latin-basic", "hangul"],
  },
];

export const FONT_IDS = FONTS.map((f) => f.id);

// The one every caption written before this phase is set in, and what an
// unrecognised id folds down to — the same forgiveness `ease`, `clipKind` and
// the transition kinds get.
export const DEFAULT_FONT = "inter";

/** The list entry for `id`, or the default's. Mirrors `font_entry`. */
export function fontEntry(id) {
  return FONTS.find((f) => f.id === id) || FONTS[0];
}

/** The `SCRIPTS` row for `id`, or null. Mirrors `script_entry`. */
export function scriptEntry(id) {
  return SCRIPTS.find((s) => s.id === id) || null;
}

/** The CSS `font-family` value for a caption set in `id`. */
export function fontFamily(id) {
  return `"${fontEntry(id).family}", sans-serif`;
}

/**
 * CSS `line-height` for a caption set in `id` at `lineHeight` — the number the
 * exporter multiplies (ascent + descent) by. See `line_ratio` above.
 */
export function cssLineHeight(id, lineHeight = 1.28) {
  return (lineHeight || 1.28) * (fontEntry(id).line_ratio || 1.22);
}

/**
 * The writing systems `text` needs a font to have. Mirrors `scripts_of`.
 *
 * Returned in `SCRIPTS` order, so the answer is stable and reads the way the
 * table does. Whitespace is skipped — every font has a space — and a character
 * no range claims demands nothing, which is what keeps an emoji or a ✓ from
 * raising a warning nobody can act on.
 */
export function scriptsOf(text) {
  const found = new Set();
  for (const char of text || "") {
    if (!char.trim()) continue;
    const code = char.codePointAt(0);
    for (const script of SCRIPTS) {
      if (script.ranges.some(([low, high]) => code >= low && code <= high)) {
        found.add(script.id);
        break;
      }
    }
  }
  return SCRIPTS.filter((s) => found.has(s.id)).map((s) => s.id);
}

/**
 * The writing systems `text` needs that this face has not got. Mirrors
 * `missing_scripts`.
 *
 * ⚠ THE ANSWER THE UI SHOWS, so it names writing systems rather than
 * characters: "this font has no Devanagari" is something a user can act on,
 * and "U+0939 is missing" is not.
 */
export function missingScripts(id, text) {
  const have = new Set(fontEntry(id).scripts);
  return scriptsOf(text).filter((scriptId) => {
    if (have.has(scriptId)) return false;
    const alternatives = (scriptEntry(scriptId) || { any_of: [] }).any_of;
    return !alternatives.some((alt) => have.has(alt));
  });
}

/** Can this face draw every character of `text`? Mirrors `covers`. */
export function covers(id, text) {
  return missingScripts(id, text).length === 0;
}

/** Every face that can draw `text`, in list order. Mirrors `fonts_for_text`. */
export function fontsForText(text) {
  return FONTS.filter((f) => covers(f.id, text));
}

/**
 * The font id to set `text` in — `prefer` if it can draw it, else the best fit.
 * Mirrors `best_font_for_text`, including the ranking; read that docstring for
 * why "fits" is not enough to choose by and why the REQUIRED SCRIPTS rather
 * than the fonts are what gets walked.
 */
export function bestFontForText(text, prefer = "") {
  if (prefer && covers(prefer, text)) return prefer;
  const fits = fontsForText(text);
  if (!fits.length) return DEFAULT_FONT;
  const required = scriptsOf(text);
  const regional = new Set(
    required.flatMap((id) => (scriptEntry(id) || { any_of: [] }).any_of)
  );
  for (const scriptId of required) {
    const shelf = fits.find((f) => f.group === scriptId);
    if (shelf) return shelf.id;
  }
  const near = fits.find((f) => regional.has(f.group));
  return (near || fits[0]).id;
}

/**
 * The picker's shelves: `[{ id, label, note, fonts }]` in `SCRIPTS` order.
 *
 * ⚠ DERIVED, because fifty-six faces in one flat `<select>` is not a list
 * anyone can read — and because a hand-written grouping is a third copy of the
 * font list, which is the thing this whole module exists to avoid. A shelf with
 * nothing on it is dropped rather than drawn empty.
 */
export function fontGroups() {
  return SCRIPTS.map((script) => ({
    id: script.id,
    label: script.label,
    note: script.note,
    fonts: FONTS.filter((f) => f.group === script.id),
  })).filter((group) => group.fonts.length > 0);
}

/**
 * Register every bundled font with the browser. Idempotent; call it once.
 *
 * ⚠ THE @font-face RULES ARE GENERATED, NOT WRITTEN IN A .css FILE. A third
 * hand-maintained copy of the list is exactly the failure this whole module is
 * built to avoid — the shape polygons are what that looks like after a year, and
 * they needed a test (`tests/shape_points_check.py`) to be safe to touch. The
 * list above is the only place a font is named.
 *
 * `font-display: block` rather than `swap` on purpose: swapping means the
 * monitor draws one frame of a fallback face at a different width, which for a
 * tool whose entire promise is "the preview is the export" is worse than a beat
 * of nothing.
 *
 * ⚠ DECLARING FIFTY-SIX FACES DOWNLOADS NONE OF THEM. A browser fetches a
 * `@font-face` file only when something on the page is actually set in it, so
 * the ten-megabyte Chinese faces cost an English project nothing. That is the
 * whole reason the CJK families can be on the list at all, and it is why this
 * must stay a declaration rather than becoming a preload.
 */
let injected = false;
export function ensureFontsLoaded() {
  if (injected || typeof document === "undefined") return;
  injected = true;
  const style = document.createElement("style");
  style.dataset.animaticFonts = "1";
  style.textContent = FONTS.map(
    (f) => `@font-face{font-family:"${f.family}";src:url("/fonts/${f.file}") format("truetype");font-display:block;}`
  ).join("\n");
  document.head.appendChild(style);
}
