/**
 * TRANSITION MATTES — one GLSL chunk per shape, and not a compositing stage.
 *
 * ⚠ A REVEAL TRANSITION IS A SECOND MASK ON THE INCOMING PICTURE. That is the
 * whole design, and it is worth stating plainly because the obvious alternative
 * is wrong here:
 *
 *     a wipe at 50%  =  show the incoming picture where p.x < 0.5
 *     a mask         =  show this picture where it is inside the region
 *
 * Same operation. So a transition matte is multiplied into the arriving
 * picture's ALPHA, immediately after `maskCoverage` in `layer.js`, and driven by
 * the transition's progress instead of by keyframes.
 *
 * What that buys, and why this is not a `mix(getFromColor, getToColor)` stage
 * the way gl-transitions does it: `_transition_canvas` in animatic.py composites
 * the incoming picture OVER the outgoing one rather than blending with it, so
 * that "a caption keyed out of the arriving shot reveals the shot it is arriving
 * over, not black". A two-texture mix stage would throw that away — clip B's
 * blend mode, chroma key and per-clip mask would have nothing left to blend
 * against. Folding the matte into the alpha keeps all four working, needs no new
 * program and no extra framebuffers, and does not touch the `uFxArgs` budget.
 *
 * ⚠ EVERY FUNCTION HERE HAS A TWIN in `animatic_transitions.py`, the same way
 * every function in `effects.js` has one in `animatic_effects.py`. Same formula,
 * same order, two languages. Change one, change the other.
 *
 * They are exported as STRINGS rather than .glsl files for the reason
 * `effects.js` gives: `tests/effects_parity_check.py` runs under plain `node`
 * with no bundler, and has to import the exact source the browser compiles.
 *
 * ---------------------------------------------------------------------------
 * THE TWO RULES EVERY MATTE OBEYS
 * ---------------------------------------------------------------------------
 * 1. `field(p)` returns a distance in 0–1: 0 where the matte opens FIRST, 1
 *    where it opens LAST. Progress then sweeps a threshold across it. Writing
 *    the shapes as scalar fields rather than as nine separate sweeps is what
 *    keeps each one three lines long and makes softness a single shared line.
 *
 * 2. AT PROGRESS 0 NOTHING IS REVEALED AND AT PROGRESS 1 EVERYTHING IS, exactly,
 *    at every softness. `matteCoverage` widens the threshold's travel by the
 *    feather either side to guarantee it. A transition has to be invisible at
 *    its own two ends — that is what lets it straddle a cut without anything
 *    appearing to jump — and a matte that only approximately closed would show
 *    as a one-frame flash of the wrong shot at the edge of every window.
 *
 * ⚠ SAMPLED AT PIXEL CENTRES, like `maskCoverage`, and in FRACTION space — so an
 * iris is an ellipse-in-fractions on a 16:9 frame and comes out wider than it is
 * tall, exactly as a `border-radius: 50%` shape and an ellipse mask already do.
 * Pixel centres are also what make the field strictly less than 1 everywhere, so
 * the threshold at progress 1 covers the whole frame with no corner left behind.
 */

// ---------------------------------------------------------------------------
// Straight edges
// ---------------------------------------------------------------------------
export const M_LINEAR = /* glsl */ `
// A hard edge travelling across the frame — what a wipe has always been.
// 'dir' is the way the EDGE TRAVELS, so DIR_RIGHT starts at the left.
float matteLinear(vec2 p, int dir) {
  if (dir == DIR_LEFT) return 1.0 - p.x;
  if (dir == DIR_UP)   return 1.0 - p.y;
  if (dir == DIR_DOWN) return p.y;
  return p.x;
}
`;

export const M_DIAGONAL = /* glsl */ `
// The same edge held at 45 degrees, so it arrives from a CORNER. Halved because
// the two axes sum to 2 at the far corner and the field has to land in 0-1.
float matteDiagonal(vec2 p, int dir) {
  if (dir == DIR_LEFT) return ((1.0 - p.x) + (1.0 - p.y)) * 0.5;
  if (dir == DIR_UP)   return (p.x + (1.0 - p.y)) * 0.5;
  if (dir == DIR_DOWN) return ((1.0 - p.x) + p.y) * 0.5;
  return (p.x + p.y) * 0.5;
}
`;

export const M_SPLIT = /* glsl */ `
// Barn doors: the arriving shot opens from the CENTRE LINE outwards. Doubled so
// the field reaches 1 at the two edges. Left/right split it down the middle,
// up/down split it across — one axis, named by the way the doors travel.
float matteSplit(vec2 p, int dir) {
  float c = (dir == DIR_UP || dir == DIR_DOWN) ? p.y : p.x;
  return abs(c - 0.5) * 2.0;
}
`;

// ---------------------------------------------------------------------------
// Irises — a shape growing out of the middle
// ---------------------------------------------------------------------------
export const M_RADIAL = /* glsl */ `
// A circle opening from the centre. Divided by the half-diagonal so the field
// reaches 1 in the CORNERS rather than at the edges — otherwise the last few
// per cent of the transition would have nothing left to do but the corners.
float matteRadial(vec2 p) { return length(p - vec2(0.5)) / 0.70710678; }
`;

export const M_DIAMOND = /* glsl */ `
// The same iris under the Manhattan metric: a square standing on its point.
float matteDiamond(vec2 p) { return abs(p.x - 0.5) + abs(p.y - 0.5); }
`;

export const M_BOX = /* glsl */ `
// And under the Chebyshev metric: a rectangle opening from the centre. All
// three irises are one line apart, which is the point of fields over sweeps.
float matteBox(vec2 p) { return max(abs(p.x - 0.5), abs(p.y - 0.5)) * 2.0; }
`;

export const M_ANGULAR = /* glsl */ `
// A clock hand sweeping from twelve, clockwise. The y argument is flipped
// because frame space runs y DOWN, and the branch is what turns atan's
// (-PI, PI] into a field running 0 at twelve the whole way round, not jumping.
float matteAngular(vec2 p) {
  float a = atan(p.x - 0.5, 0.5 - p.y);
  return (a < 0.0 ? a + 6.28318531 : a) / 6.28318531;
}
`;

// ---------------------------------------------------------------------------
// Repeats — the field is taken modulo a count, so every cell opens at once
// ---------------------------------------------------------------------------
export const M_BLINDS = /* glsl */ `
// Venetian blinds: 'count' bands, each running its own little wipe at the same
// moment. fract() is the whole trick — the band index is discarded and only the
// position WITHIN a band survives, so one field drives all of them.
float matteBlinds(vec2 p, int dir, float count) {
  float c = (dir == DIR_UP || dir == DIR_DOWN) ? p.y : p.x;
  if (dir == DIR_LEFT || dir == DIR_UP) c = 1.0 - c;
  return fract(c * max(count, 1.0));
}
`;

export const M_CHECKER = /* glsl */ `
// A checkerboard: cells of one parity go in the FIRST half of the window, the
// others in the second. Compressed into halves rather than run together,
// because two interleaved grids arriving at once is just a soft-edged wipe.
float matteChecker(vec2 p, float count) {
  float n = max(count, 1.0);
  float parity = mod(floor(p.x * n) + floor(p.y * n), 2.0);
  return (parity + fract(p.x * n)) * 0.5;
}
`;

// ---------------------------------------------------------------------------
// The dispatcher — the only thing layer.js calls
// ---------------------------------------------------------------------------
export const MATTE = /* glsl */ `
float matteField(vec2 p, int kind, int dir, float count) {
  if (kind == MATTE_LINEAR)   return matteLinear(p, dir);
  if (kind == MATTE_DIAGONAL) return matteDiagonal(p, dir);
  if (kind == MATTE_SPLIT)    return matteSplit(p, dir);
  if (kind == MATTE_RADIAL)   return matteRadial(p);
  if (kind == MATTE_DIAMOND)  return matteDiamond(p);
  if (kind == MATTE_BOX)      return matteBox(p);
  if (kind == MATTE_ANGULAR)  return matteAngular(p);
  if (kind == MATTE_BLINDS)   return matteBlinds(p, dir, count);
  if (kind == MATTE_CHECKER)  return matteChecker(p, count);
  return 0.0;
}

// How much of the ARRIVING picture this pixel shows. Kind 0 is "no matte" and
// returns 1, which is every layer that is not the incoming half of a reveal —
// so this costs an ordinary frame one comparison.
//
// ⚠ THE THRESHOLD TRAVELS FURTHER THAN 0-1, by the feather either side. That is
// what holds rule 2 above: at progress 0 the far edge of the ramp is still at
// the field's 0, and at progress 1 the near edge has passed its 1, so the matte
// is exactly empty and exactly full at the two ends AT EVERY SOFTNESS. Ramping
// from 0 to 1 instead would leave half a feather showing at both ends, and the
// shot would jump on the frame either side of every transition.
float matteCoverage(vec2 p, int kind, float progress, float softness, float count, int dir) {
  if (kind == 0) return 1.0;
  float s = max(softness, 0.0);
  float d = matteField(p, kind, dir, count);
  float edge = progress * (1.0 + 2.0 * s) - s;
  // A hard edge is a COMPARISON, not a degenerate smoothstep: GLSL's smoothstep
  // divides by (edge1 - edge0) and is undefined when they are equal, and the
  // Python twin would then have to guess the same undefined answer.
  if (s <= 0.0) return d < edge ? 1.0 : 0.0;
  return 1.0 - smoothstep(edge - s, edge + s, d);
}
`;
