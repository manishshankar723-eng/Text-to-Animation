/**
 * The effect chain, one GLSL function per kind.
 *
 * ⚠ EVERY FUNCTION HERE HAS A TWIN in `animatic_effects.py`, written to the same
 * formula in the same order. They cannot be compared byte for byte — WebGL and
 * Pillow use different float precision and different rasterisers — so
 * `tests/effects_parity_check.py` compares them with a TOLERANCE, and
 * `tests/effects_check.py` pins the Python side to exact numbers so the pair
 * can't drift together.
 *
 * They are exported as strings rather than .glsl files on purpose: the parity
 * harness runs under plain `node`, with no bundler and no loader plugins, and
 * has to be able to `import` the exact source the browser compiles. A `?raw`
 * import would work in Vite and nowhere else.
 *
 * Chunks are kept separate, one per effect, so adding a kind is adding a file
 * and a line — the same shape the Python side has.
 */

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------
// ⚠ THE SAME THREE NUMBERS as LUMA in animatic_effects.py, and the same ones
// `Image.convert("L")` uses. Saturation 0 and a greyscale conversion agree
// because of this line.
export const COMMON = /* glsl */ `
const vec3 LUMA = vec3(0.299, 0.587, 0.114);

float luma(vec3 c) { return dot(c, LUMA); }
`;

// ---------------------------------------------------------------------------
// Colour
// ---------------------------------------------------------------------------
export const BRIGHTNESS = /* glsl */ `
// A plain multiply, exactly ImageEnhance.Brightness. 1.0 is unchanged.
vec3 fxBrightness(vec3 c, float amount) { return c * amount; }
`;

export const CONTRAST = /* glsl */ `
// ⚠ PIVOTED ON MID GREY, not on the picture's own mean. ImageEnhance.Contrast
// uses the mean, which a fragment shader cannot know — the monitor and the
// export would then differ on every picture, by an amount that depends on the
// picture. See the note at the top of animatic_effects.py.
vec3 fxContrast(vec3 c, float amount) { return (c - 0.5) * amount + 0.5; }
`;

export const SATURATION = /* glsl */ `
// Toward (or past) the ITU-R 601 grey. 0 is greyscale, above 1 pushes colour.
vec3 fxSaturation(vec3 c, float amount) {
  vec3 grey = vec3(luma(c));
  return grey + (c - grey) * amount;
}
`;

// ---------------------------------------------------------------------------
// LUT
// ---------------------------------------------------------------------------
export const LUT = /* glsl */ `
// A 3D LUT held as a 2D strip: size slices of size×size, laid left to right, so
// the texture is (size*size) wide and size tall. See buildLutTexture in lut.js.
//
// The blue interpolation is done BY HAND between two slices; only red and green
// are left to the sampler's own bilinear filter. Sampling across the whole strip
// with GL_LINEAR would bleed one slice into the next at its edge — a colour
// shift that only shows on the exact hues sitting on a slice boundary, which is
// the worst kind of bug to go looking for. The half-texel insets below are what
// keep each lookup inside its own slice.
vec3 lutLookup(sampler2D lut, vec3 c, float size) {
  c = clamp(c, 0.0, 1.0);
  float slice = 1.0 / size;
  float zPos = c.b * (size - 1.0);
  float z0 = floor(zPos);
  float z1 = min(z0 + 1.0, size - 1.0);
  float u = (c.r * (size - 1.0) + 0.5) / (size * size);
  float v = (c.g * (size - 1.0) + 0.5) / size;
  vec3 a = texture2D(lut, vec2(u + z0 * slice, v)).rgb;
  vec3 b = texture2D(lut, vec2(u + z1 * slice, v)).rgb;
  return mix(a, b, zPos - z0);
}
`;

// ---------------------------------------------------------------------------
// Chroma key
// ---------------------------------------------------------------------------
export const CHROMA = /* glsl */ `
// Distance measured in CHROMA ONLY (Cb/Cr), never in RGB. A green screen is lit
// unevenly, so brightness has to be free to vary while the hue is what
// identifies it — keying in RGB is the classic way to get a subject with a hard
// black rim. Returns the colour with spill pulled off and the new alpha in .a.
vec4 fxChroma(vec3 c, float alpha, vec3 key, float similarity, float smoothness, float spill) {
  float y = luma(c);
  float keyY = luma(key);
  vec2 d = vec2((c.b - y) * 0.5643, (c.r - y) * 0.7132)
         - vec2((key.b - keyY) * 0.5643, (key.r - keyY) * 0.7132);
  float keep = smoothstep(similarity, similarity + max(smoothness, 1e-4), length(d));
  // Only where the key is biting: (1 - keep) is exactly "how much of this pixel
  // the key thinks is screen", so the desaturation fades out with it instead of
  // flattening the whole picture.
  c = mix(c, vec3(y), clamp(spill * (1.0 - keep), 0.0, 1.0));
  return vec4(c, alpha * keep);
}
`;

// ---------------------------------------------------------------------------
// Mask
// ---------------------------------------------------------------------------
export const MASK = /* glsl */ `
// How much of this pixel of the FRAME the mask lets through. 'p' is in frame
// coordinates (0–1, y down), matching where the project stores the geometry —
// which is why an ellipse mask is an ellipse in FRACTION space and comes out
// wider than it is tall on a 16:9 frame, exactly as '.an-shape' with
// border-radius: 50% does.
//
// 'feather' is in the mask's OWN normalised units, so a small mask gets a
// proportionally small softness and the edge keeps its hardness while the mask
// is animated bigger.
float maskCoverage(vec2 p, int kind, vec2 centre, vec2 half_, float feather, float invert) {
  if (kind == 0) return 1.0;
  vec2 d = (p - centre) / max(half_, vec2(1e-4));
  float dist = (kind == 2) ? length(d) : max(abs(d.x), abs(d.y));
  float f = max(feather, 1e-3);
  float coverage = 1.0 - smoothstep(1.0 - f, 1.0 + f, dist);
  return mix(coverage, 1.0 - coverage, invert);
}
`;

// ---------------------------------------------------------------------------
// Blend
// ---------------------------------------------------------------------------
export const BLEND = /* glsl */ `
// ⚠ THE ALPHA IS THE MIX, ALWAYS — see blend_onto in animatic_effects.py. Every
// mode is base + (blend(base, layer) - base) * alpha, which is what lets a
// blend mode compose with opacity, a chroma key and a feathered mask without
// any of them knowing about the others.
vec3 blendMode(vec3 base, vec3 layer, int mode) {
  if (mode == 1) return base * layer;                                  // multiply
  if (mode == 2) return 1.0 - (1.0 - base) * (1.0 - layer);            // screen
  // overlay: splits on the BASE, not the layer. The other way round is hard
  // light, a different mode, and the two are constantly confused.
  if (mode == 3) return mix(2.0 * base * layer,
                            1.0 - 2.0 * (1.0 - base) * (1.0 - layer),
                            step(0.5, base));
  if (mode == 4) return min(vec3(1.0), base + layer);                  // add
  if (mode == 5) return min(base, layer);                              // darken
  if (mode == 6) return max(base, layer);                              // lighten
  return layer;                                                        // normal
}
`;
