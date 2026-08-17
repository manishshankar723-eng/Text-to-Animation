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

/** ⚠ ELEMENT FOR ELEMENT the same as FONTS in `animatic_fonts.py`. */
export const FONTS = [
  { id: "inter", label: "Inter", file: "Inter-SemiBold.ttf", family: "AnimaticInter" },
  { id: "anton", label: "Anton", file: "Anton-Regular.ttf", family: "AnimaticAnton" },
  { id: "bebas", label: "Bebas Neue", file: "BebasNeue-Regular.ttf", family: "AnimaticBebas" },
  {
    id: "playfair",
    label: "Playfair Display",
    file: "PlayfairDisplay-SemiBold.ttf",
    family: "AnimaticPlayfair",
  },
  {
    id: "courier",
    label: "Courier Prime",
    file: "CourierPrime-Regular.ttf",
    family: "AnimaticCourier",
  },
  { id: "caveat", label: "Caveat", file: "Caveat-SemiBold.ttf", family: "AnimaticCaveat" },
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
 * Register every bundled font with the browser. Idempotent; call it once.
 *
 * ⚠ THE @font-face RULES ARE GENERATED, NOT WRITTEN IN A .css FILE. A third
 * hand-maintained copy of the list is exactly the failure this whole module is
 * built to avoid — `_SHAPE_POINTS` / `POINTS` is what that looks like after a
 * year. The list above is the only place a font is named.
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
