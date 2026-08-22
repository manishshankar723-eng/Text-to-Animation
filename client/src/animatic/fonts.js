/**
 * The fonts a caption can be set in — the BROWSER's half of the list.
 *
 * ⚠ THIS FILE HAS A TWIN: `animatic_fonts.py`. The two lists must be
 * element-for-element identical, and `tests/captions_check.py` fails if they
 * are not. Read that module's docstring for WHY the font has to be a file that
 * ships with the project rather than a name each side looks up on the machine
 * it happens to be running on — it is the same class of bug as the scene model
 * being written twice, and it shows up as a caption that wraps onto three lines
 * in the MP4 and two in the monitor.
 *
 * `family` is the CSS name each file is registered under, and it is
 * deliberately NOT the font's real family name: a user with Inter installed
 * would otherwise get the system copy, which is precisely the divergence this
 * exists to prevent.
 */

/**
 * ⚠ ELEMENT FOR ELEMENT the same as FONTS in `animatic_fonts.py`.
 *
 * `line_ratio` is (ascent + descent) ÷ font size for the file — read that
 * module for why it is on the list and what it fixes. In one line: the exporter
 * steps its baselines by `(ascent + descent) × line_height` and CSS
 * `line-height` is a multiple of the FONT SIZE, so `lineHeight` in the browser
 * has to be multiplied by this to be the same distance. `captionStyle` is the
 * only caller.
 */
export const FONTS = [
  // --- Sans, for a caption you are meant to READ rather than look at ------
  // ⚠ INTER IS FIRST AND HAS TO STAY FIRST — `fontEntry` falls back to
  // `FONTS[0]`, and `DEFAULT_FONT` below names the same one.
  {
    id: "inter",
    label: "Inter",
    file: "Inter-SemiBold.ttf",
    family: "AnimaticInter",
    line_ratio: 1.22,
  },
  {
    id: "montserrat",
    label: "Montserrat",
    file: "Montserrat-SemiBold.ttf",
    family: "AnimaticMontserrat",
    line_ratio: 1.23,
  },
  {
    id: "poppins",
    label: "Poppins",
    file: "Poppins-SemiBold.ttf",
    family: "AnimaticPoppins",
    line_ratio: 1.4,
  },
  {
    id: "nunito",
    label: "Nunito",
    file: "Nunito-Bold.ttf",
    family: "AnimaticNunito",
    line_ratio: 1.38,
  },
  // --- Condensed and heavy, for a title card or a lower third -------------
  {
    id: "anton",
    label: "Anton",
    file: "Anton-Regular.ttf",
    family: "AnimaticAnton",
    line_ratio: 1.51,
  },
  {
    id: "bebas",
    label: "Bebas Neue",
    file: "BebasNeue-Regular.ttf",
    family: "AnimaticBebas",
    line_ratio: 1.2,
  },
  {
    id: "oswald",
    label: "Oswald",
    file: "Oswald-Medium.ttf",
    family: "AnimaticOswald",
    line_ratio: 1.49,
  },
  {
    id: "archivo",
    label: "Archivo Black",
    file: "ArchivoBlack-Regular.ttf",
    family: "AnimaticArchivo",
    line_ratio: 1.09,
  },
  // --- Serif --------------------------------------------------------------
  {
    id: "playfair",
    label: "Playfair Display",
    file: "PlayfairDisplay-SemiBold.ttf",
    family: "AnimaticPlayfair",
    line_ratio: 1.35,
  },
  {
    id: "merriweather",
    label: "Merriweather",
    file: "Merriweather-Bold.ttf",
    family: "AnimaticMerriweather",
    line_ratio: 1.27,
  },
  // --- Faces with a voice of their own ------------------------------------
  {
    id: "bangers",
    label: "Bangers",
    file: "Bangers-Regular.ttf",
    family: "AnimaticBangers",
    line_ratio: 1.08,
  },
  {
    id: "lobster",
    label: "Lobster",
    file: "Lobster-Regular.ttf",
    family: "AnimaticLobster",
    line_ratio: 1.25,
  },
  {
    id: "caveat",
    label: "Caveat",
    file: "Caveat-SemiBold.ttf",
    family: "AnimaticCaveat",
    line_ratio: 1.26,
  },
  {
    id: "courier",
    label: "Courier Prime",
    file: "CourierPrime-Regular.ttf",
    family: "AnimaticCourier",
    line_ratio: 1.14,
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
 * of nothing. The files are local and a few hundred KB, so the beat is short.
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
