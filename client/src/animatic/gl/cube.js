/**
 * Reading a .cube file, and turning it into something a shader can sample.
 *
 * ⚠ NOTHING IS IMPORTED HERE, and that is load-bearing. `tests/effects_parity_check.py`
 * runs this file and the shaders under plain `node` against a headless GL
 * context; one import of `api.js` (which reads `import.meta.env`) would make
 * the whole compositor unloadable outside a bundler, and the parity test would
 * have to re-implement the parsing it is supposed to be checking.
 * `lut.js` is the layer that FETCHES these — it imports this file.
 *
 * ⚠ `parseCube` IS A TWIN of `parse_cube` in `animatic_effects.py`. What is NOT
 * duplicated is the LUT itself: both sides read the same file off the server
 * (`GET /animatics/luts/{name}`), so there is exactly one copy of the numbers.
 * That is the whole reason a LUT is a named file rather than an inline table —
 * it is the mistake `_SHAPE_POINTS` made, not repeated.
 *
 * Only the subset every grading tool actually writes is understood. A 1D LUT is
 * REFUSED rather than half-applied: silently grading wrong is worse than saying
 * "this file isn't a 3D LUT".
 */


// Matches MAX_LUT_SIZE in animatic_effects.py. Bigger than this is a file that
// has no business being uploaded into a browser texture either.
export const MAX_LUT_SIZE = 64;

export class LutError extends Error {}

/**
 * A .cube file's text → { size, table }, `table` being size³ RGB triples.
 *
 * ⚠ VALUES ARE LISTED WITH RED CHANGING FASTEST. It is the one thing about the
 * format that is easy to get backwards and impossible to see in an identity
 * LUT, which is why `tests/effects_check.py` grades a red-only ramp through it.
 */
export function parseCube(text) {
  let size = 0;
  const table = [];
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const parts = line.split(/\s+/);
    const head = parts[0].toUpperCase();
    if (head === "LUT_3D_SIZE") {
      size = Number(parts[1]);
      if (!Number.isFinite(size)) throw new LutError("LUT_3D_SIZE is not a number.");
      continue;
    }
    if (head === "LUT_1D_SIZE") {
      throw new LutError("This is a 1D LUT; only 3D .cube files are supported.");
    }
    if (head === "TITLE" || head === "DOMAIN_MIN" || head === "DOMAIN_MAX") continue;
    if (parts.length !== 3) continue;
    const rgb = parts.map(Number);
    if (rgb.some((v) => !Number.isFinite(v))) continue;
    table.push(...rgb);
  }
  if (!(size > 1)) throw new LutError("The file has no usable LUT_3D_SIZE.");
  if (size > MAX_LUT_SIZE) {
    throw new LutError(`LUT_3D_SIZE ${size} is larger than the ${MAX_LUT_SIZE} limit.`);
  }
  if (table.length !== size ** 3 * 3) {
    throw new LutError(
      `Expected ${size ** 3} entries for size ${size}, found ${table.length / 3}.`
    );
  }
  return { size, table };
}

/**
 * The table laid out as the 2D strip `lutLookup` samples: `size` slices of
 * size×size, left to right, so the texture is (size*size) wide and size tall.
 *
 * Slice z holds every colour whose BLUE is z; within a slice x is red and y is
 * green. RGBA rather than RGB because an RGB texture of an odd width needs
 * UNPACK_ALIGNMENT fiddling, and a wrong alignment shears the LUT by a pixel
 * per row — which reads as a colour cast, not as a layout bug.
 */
export function buildLutPixels({ size, table }) {
  const pixels = new Uint8Array(size * size * size * 4);
  for (let b = 0; b < size; b++) {
    for (let g = 0; g < size; g++) {
      for (let r = 0; r < size; r++) {
        const from = ((b * size + g) * size + r) * 3;
        const x = b * size + r;
        const to = (g * size * size + x) * 4;
        pixels[to] = Math.round(Math.min(1, Math.max(0, table[from])) * 255);
        pixels[to + 1] = Math.round(Math.min(1, Math.max(0, table[from + 1])) * 255);
        pixels[to + 2] = Math.round(Math.min(1, Math.max(0, table[from + 2])) * 255);
        pixels[to + 3] = 255;
      }
    }
  }
  return { width: size * size, height: size, pixels, size };
}
