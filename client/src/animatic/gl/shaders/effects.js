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
// Point-wise grades — every one a function of a single pixel and nothing else
// ---------------------------------------------------------------------------
// That constraint is the admission price for this file: the monitor grades in
// ONE fragment shader pass with no neighbourhood available, so blur, sharpen
// and grain cannot live here. See the note on EFFECT_PARAMS in scene.js.
export const EXPOSURE = /* glsl */ `
// STOPS, not a multiplier: +1 is twice the light, -1 is half. exp2 rather than
// pow(2.0, s) because that is the instruction the hardware actually has, and
// because "how many stops" is the unit a colourist already thinks in.
vec3 fxExposure(vec3 c, float stops) { return c * exp2(stops); }
`;

export const GAMMA = /* glsl */ `
// A power curve per channel. 1.0 is unchanged; BELOW 1 lifts the shadows,
// which is the direction people expect from a control called gamma.
// Clamped away from zero at both ends: pow() of a negative base is undefined
// in GLSL and NaN in numpy, and 1.0/0.0 is a different kind of black frame.
vec3 fxGamma(vec3 c, float gamma) {
  return pow(max(c, vec3(0.0)), vec3(1.0 / max(gamma, 0.01)));
}
`;

export const TEMPERATURE = /* glsl */ `
// Warm/cool on the red-blue axis, green/magenta on the other, as a plain shift.
// ⚠ DELIBERATELY NAIVE, and it is worth saying so: a real white balance is a
// matrix applied in a LINEAR working space, and neither renderer has one — both
// grade straight on the 0-1 sRGB values. This is the honest version of what a
// slider called Temperature can do here, not an approximation of a better one.
vec3 fxTemperature(vec3 c, float temperature, float tint) {
  return c + vec3(temperature, tint, -temperature) * 0.2;
}
`;

export const HUE = /* glsl */ `
// Rotate the hue, as a rotation of the CHROMA PLANE about the luma axis.
//
// ⚠ THROUGH YIQ, NOT THE SVG feColorMatrix hueRotate MATRIX. That matrix is
// built on the 709 weights (0.213/0.715/0.072); this file's luma is 601, and
// mixing the two would mean a hue rotation of 0 degrees did not quite agree
// with saturation 1 or with Image.convert("L"). YIQ's Y is exactly LUMA, so
// rotating (I, Q) leaves brightness alone by construction.
vec3 fxHue(vec3 c, float degrees) {
  float a = radians(degrees);
  float co = cos(a);
  float si = sin(a);
  float y = dot(c, LUMA);
  float i = dot(c, vec3(0.595716, -0.274453, -0.321263));
  float q = dot(c, vec3(0.211456, -0.522591, 0.311135));
  float i2 = i * co - q * si;
  float q2 = i * si + q * co;
  return vec3(
    y + 0.9563 * i2 + 0.6210 * q2,
    y - 0.2721 * i2 - 0.6474 * q2,
    y - 1.1070 * i2 + 1.7046 * q2
  );
}
`;

export const SEPIA = /* glsl */ `
// The classic sepia matrix, dialled back by amount. NOT a tint over a greyscale
// picture: the matrix is warmer in the highlights than a flat tone is, and that
// difference is the whole look.
vec3 fxSepia(vec3 c, float amount) {
  vec3 s = vec3(
    dot(c, vec3(0.393, 0.769, 0.189)),
    dot(c, vec3(0.349, 0.686, 0.168)),
    dot(c, vec3(0.272, 0.534, 0.131))
  );
  return mix(c, s, clamp(amount, 0.0, 1.0));
}
`;

export const POSTERIZE = /* glsl */ `
// Quantise each channel to 'levels' evenly spaced values, BOTH ENDS INCLUDED —
// so 2 levels is pure black and pure white rather than black and mid grey,
// which is what makes the control read as "how many bands" rather than "how
// dark". Hence (levels - 1) as the divisor.
//
// ⚠ floor(x + 0.5), NEVER a round(). numpy rounds halves to EVEN and GLSL
// rounds them away from zero, and a band edge is exactly where the halves land
// — so round() would put whole regions of a posterised frame one band apart
// between the monitor and the export.
vec3 fxPosterize(vec3 c, float levels) {
  float n = max(levels, 2.0) - 1.0;
  return floor(clamp(c, 0.0, 1.0) * n + 0.5) / n;
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
