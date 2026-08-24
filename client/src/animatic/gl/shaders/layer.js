/**
 * The one program the compositor draws everything with.
 *
 * Every layer on the frame — a picture, a colour card, a shape — is the same
 * fragment shader: sample the layer, run its effect chain, apply its mask,
 * blend the result onto whatever is already composited underneath. Only the
 * GEOMETRY differs, and geometry is built on the CPU in `compositor.js` so that
 * the placement maths can mirror `place_picture` and `draw_shapes` line for
 * line rather than being re-derived in GLSL.
 *
 * ⚠ VERTICES ARE IN FRAME SPACE: x right, y DOWN, both 0–1, which is the space
 * the project stores every geometry in. The vertex shader is the only place
 * that knows about clip space, so nothing downstream has to remember whether y
 * is up or down — a mistake that shows as a silently mirrored mask.
 *
 * The effect kind constants are GENERATED from `EFFECT_KINDS` rather than
 * written out, so an effect added to the scene model cannot end up with a
 * different number here than the JS that fills the uniforms uses.
 */

import { BLEND_MODES, EFFECT_KINDS } from "../../scene.js";
import { MATTE_KINDS, TRANSITION_DIRECTIONS } from "../../transitions.js";
import {
  BLEND,
  BRIGHTNESS,
  CHROMA,
  COMMON,
  CONTRAST,
  EXPOSURE,
  GAMMA,
  HUE,
  LUT,
  MASK,
  POSTERIZE,
  SATURATION,
  SEPIA,
  TEMPERATURE,
} from "./effects.js";
import {
  MATTE,
  M_ANGULAR,
  M_BLINDS,
  M_BOX,
  M_CHECKER,
  M_DIAGONAL,
  M_DIAMOND,
  M_LINEAR,
  M_RADIAL,
  M_SPLIT,
} from "./mattes.js";

// How many effects one clip's chain can carry. A uniform array needs a fixed
// size in GLSL ES 1.0, and this is also the cap the Effects pane enforces — if
// the two ever disagreed the monitor would silently stop showing the last
// effect while the export still applied it.
export const MAX_EFFECTS = 6;
// How many DIFFERENT LUTs one chain can use. Samplers cannot be indexed by a
// loop variable, so each one needs its own uniform and its own branch. Two is
// past what anyone stacks; a third `lut` in one chain is dropped, loudly.
export const MAX_LUTS = 2;

/** brightness → 0, contrast → 1, … Shared by the shader and the uniform writer. */
export const fxIndex = (kind) => EFFECT_KINDS.indexOf(kind);
/** normal → 0, multiply → 1, … Must match `blendMode()` in effects.js. */
export const blendIndex = (mode) => Math.max(0, BLEND_MODES.indexOf(mode));
/** none → 0, linear → 1, … Shared by the shader and the uniform writer. */
export const matteIndex = (matte) => Math.max(0, MATTE_KINDS.indexOf(matte));
/** left → 0, right → 1, … `direction` reaches the shader as a number. */
export const dirIndex = (direction) => Math.max(0, TRANSITION_DIRECTIONS.indexOf(direction));

export const VERTEX = /* glsl */ `
attribute vec2 aFrame;
attribute vec2 aUV;
varying vec2 vFrame;
varying vec2 vUV;

void main() {
  vFrame = aFrame;
  vUV = aUV;
  // Frame space (y down) to clip space (y up). The single place the flip lives.
  gl_Position = vec4(aFrame.x * 2.0 - 1.0, 1.0 - aFrame.y * 2.0, 0.0, 1.0);
}
`;

export const FRAGMENT = /* glsl */ `
precision highp float;

varying vec2 vFrame;
varying vec2 vUV;

uniform sampler2D uTexture;
uniform float uUseTexture;   // 1 = sample uTexture, 0 = flat uColor
uniform float uUseAlpha;     // 1 = keep the source's alpha, 0 = force opaque
uniform vec3  uColor;
uniform float uOpacity;

uniform sampler2D uBackdrop; // everything composited under this layer, THIS BAND
// ⚠ WHAT IS UNDER THE WHOLE BAND — the finished canvas of the band below this
// one, or nothing when this IS the bottom band (uHasUnder = 0). It exists for one
// job: a blend mode has to blend against what a person can SEE beneath the layer,
// and beneath an upper band that is a different canvas which this band's own
// framebuffer knows nothing about. It is read for the COLOUR MATHS ONLY and never
// written out, or the band would paint the picture below it back over the
// captions that sit between them.
uniform sampler2D uUnder;
uniform float uHasUnder;
uniform vec2  uResolution;
uniform int   uBlend;

uniform int   uFxCount;
uniform int   uFxKind[${MAX_EFFECTS}];
// ⚠ POSITIONAL, NOT NAMED: x/y/z are the kind's numeric parameters in the order
// EFFECT_PARAMS declares them, and w is the LUT size. So chroma's x is its
// similarity, temperature's x is its temperature and its y is its tint, and a
// kind with one number uses x alone. _setLook packs them straight off the
// descriptor, which is why adding a point-wise effect needs no change there.
uniform vec4  uFxArgs[${MAX_EFFECTS}];
uniform vec3  uFxColor[${MAX_EFFECTS}];
uniform int   uFxLutSlot[${MAX_EFFECTS}];
uniform sampler2D uLut0;
uniform sampler2D uLut1;

uniform int   uMaskKind;     // 0 none · 1 rect · 2 ellipse
uniform vec2  uMaskCentre;
uniform vec2  uMaskHalf;
uniform float uMaskFeather;
uniform float uMaskInvert;

// The TRANSITION MATTE — its own small block, deliberately apart from the
// effect chain: it is not an effect, it costs nothing on an ordinary frame (one
// integer test), and keeping it out of uFxArgs means that adding reveal shapes
// can never eat into the MAX_EFFECTS budget. 0 is "no matte", which is every
// layer except the arriving half of a reveal.
uniform int   uMatteKind;
uniform float uMatteProgress;
uniform float uMatteSoftness;
uniform float uMatteCount;
uniform int   uMatteDir;

${COMMON}
${BRIGHTNESS}
${CONTRAST}
${SATURATION}
${LUT}
${CHROMA}
${EXPOSURE}
${GAMMA}
${TEMPERATURE}
${HUE}
${SEPIA}
${POSTERIZE}
${MASK}
${BLEND}

#define DIR_LEFT  ${dirIndex("left")}
#define DIR_RIGHT ${dirIndex("right")}
#define DIR_UP    ${dirIndex("up")}
#define DIR_DOWN  ${dirIndex("down")}

#define MATTE_LINEAR   ${matteIndex("linear")}
#define MATTE_DIAGONAL ${matteIndex("diagonal")}
#define MATTE_SPLIT    ${matteIndex("split")}
#define MATTE_RADIAL   ${matteIndex("radial")}
#define MATTE_DIAMOND  ${matteIndex("diamond")}
#define MATTE_BOX      ${matteIndex("box")}
#define MATTE_ANGULAR  ${matteIndex("angular")}
#define MATTE_BLINDS   ${matteIndex("blinds")}
#define MATTE_CHECKER  ${matteIndex("checker")}

${M_LINEAR}
${M_DIAGONAL}
${M_SPLIT}
${M_RADIAL}
${M_DIAMOND}
${M_BOX}
${M_ANGULAR}
${M_BLINDS}
${M_CHECKER}
${MATTE}

#define FX_BRIGHTNESS ${fxIndex("brightness")}
#define FX_CONTRAST   ${fxIndex("contrast")}
#define FX_SATURATION ${fxIndex("saturation")}
#define FX_LUT        ${fxIndex("lut")}
#define FX_CHROMA     ${fxIndex("chroma")}
#define FX_EXPOSURE    ${fxIndex("exposure")}
#define FX_GAMMA       ${fxIndex("gamma")}
#define FX_TEMPERATURE ${fxIndex("temperature")}
#define FX_HUE         ${fxIndex("hue")}
#define FX_SEPIA       ${fxIndex("sepia")}
#define FX_POSTERIZE   ${fxIndex("posterize")}

void main() {
  vec4 src = uUseTexture > 0.5 ? texture2D(uTexture, vUV) : vec4(uColor, 1.0);
  vec3 c = src.rgb;
  float a = mix(1.0, src.a, uUseAlpha);

  // The chain, in the order the user wrote it. Clamped BETWEEN steps because
  // Pillow writes to an 8-bit buffer between passes and so clamps too — letting
  // a value ride at 1.4 into the next effect would give the monitor headroom
  // the export does not have.
  for (int i = 0; i < ${MAX_EFFECTS}; i++) {
    if (i >= uFxCount) break;
    int kind = uFxKind[i];
    vec4 args = uFxArgs[i];
    if (kind == FX_BRIGHTNESS) {
      c = fxBrightness(c, args.x);
    } else if (kind == FX_CONTRAST) {
      c = fxContrast(c, args.x);
    } else if (kind == FX_SATURATION) {
      c = fxSaturation(c, args.x);
    } else if (kind == FX_LUT) {
      // Samplers cannot be indexed by a loop variable, hence the branch.
      vec3 graded = uFxLutSlot[i] == 0 ? lutLookup(uLut0, c, args.w)
                                       : lutLookup(uLut1, c, args.w);
      c = mix(c, graded, clamp(args.x, 0.0, 1.0));
    } else if (kind == FX_CHROMA) {
      vec4 keyed = fxChroma(c, a, uFxColor[i], args.x, args.y, args.z);
      c = keyed.rgb;
      a = keyed.a;
    } else if (kind == FX_EXPOSURE) {
      c = fxExposure(c, args.x);
    } else if (kind == FX_GAMMA) {
      c = fxGamma(c, args.x);
    } else if (kind == FX_TEMPERATURE) {
      c = fxTemperature(c, args.x, args.y);
    } else if (kind == FX_HUE) {
      c = fxHue(c, args.x);
    } else if (kind == FX_SEPIA) {
      c = fxSepia(c, args.x);
    } else if (kind == FX_POSTERIZE) {
      c = fxPosterize(c, args.x);
    }
    c = clamp(c, 0.0, 1.0);
  }

  // Opacity multiplies the alpha the chain produced rather than replacing it,
  // so a chroma key or a cut-out PNG stays cut out when the clip is faded.
  a *= uOpacity;
  // The mask is LAST and in FRAME coordinates: it is a region of the picture
  // being made, not of the file that was fed in.
  a *= maskCoverage(vFrame, uMaskKind, uMaskCentre, uMaskHalf, uMaskFeather, uMaskInvert);
  // ⚠ AND THE TRANSITION MATTE IS THE LAST THING OF ALL, one line further out
  // than the clip's own mask. A reveal is a mask on the ARRIVING picture — see
  // mattes.js — so it multiplies the alpha that the chain, the opacity and the
  // mask have already agreed on. Because it only ever touches the alpha, the
  // composite below is untouched: the incoming picture is still composited OVER
  // what is under it, which is what keeps a keyed or masked clip revealing the
  // shot it is arriving over rather than the backdrop.
  a *= matteCoverage(vFrame, uMatteKind, uMatteProgress, uMatteSoftness, uMatteCount, uMatteDir);

  // ⚠ THE COMPOSITE BELOW HAS AN ALPHA NOW, AND THIS USED TO WRITE 1.0.
  // Everything the compositor drew was opaque, which was true for as long as the
  // monitor was ONE canvas cleared to the letterbox colour — and became a black
  // screen the moment it was more than one. A band above the bottom one is a
  // sheet of glass over the bands below it (captions are DOM, so a text row
  // under a picture row splits the stack — see ProgramCanvas.jsx), and an opaque
  // sheet hides the entire film: the top band reads as black everywhere its own
  // layers do not cover. Reported by tests/editor_lane_restack_check.py
  // sampling a pixel through the upper band; nothing structural could see it.
  //
  // ⚠ AND IT REDUCES TO THE OLD LINE, EXACTLY, WHEN THE BACKDROP IS OPAQUE.
  // With ab = 1: cs = B(cb, c), ao = 1, and co = as*B + cb*(1-as), which is the
  // mix(base, blendMode(...), a) line this replaced. The bottom band always clears
  // to an opaque colour, so every project that has never been restacked — i.e.
  // one band — composites bit for bit as it did.
  vec4 bg = texture2D(uBackdrop, gl_FragCoord.xy / uResolution);
  float as = clamp(a, 0.0, 1.0);
  float ab = clamp(bg.a, 0.0, 1.0);
  // ⚠ A BLEND MODE DEGENERATES TO THE SOURCE WHERE THERE IS NOTHING UNDER IT,
  // which is the W3C compositing rule (Cs = (1 - ab)·cs + ab·B(cb, cs)) and the
  // only sane reading: "multiply" against emptiness is not black, it is the
  // layer. Without this a screened flare on an upper band came out inverted.
  // ⚠ THE BLEND SEES THROUGH THE BAND, THE ALPHA DOES NOT. Two different
  // backdrops, and keeping them apart is the whole trick: the COLOUR is blended
  // against this band's own composite laid over the band below it (what the eye
  // sees under the layer), while the alpha bookkeeping below stays band-local so
  // the canvas remains transparent where this band drew nothing.
  vec4 under = texture2D(uUnder, gl_FragCoord.xy / uResolution) * uHasUnder;
  vec3 cbSeen = mix(under.rgb, bg.rgb, ab);
  float abSeen = ab + under.a * (1.0 - ab);
  vec3 cs = mix(c, clamp(blendMode(cbSeen, c, uBlend), 0.0, 1.0), abSeen);
  float ao = as + ab * (1.0 - as);
  vec3 co = ao > 0.0 ? (as * cs + ab * bg.rgb * (1.0 - as)) / ao : vec3(0.0);
  gl_FragColor = vec4(co, ao);
}
`;

/** A pass-through used to copy one framebuffer into the next, and to the canvas. */
export const COPY_FRAGMENT = /* glsl */ `
precision highp float;
varying vec2 vUV;
uniform sampler2D uTexture;
// ⚠ ALPHA IS CARRIED, NOT REPLACED WITH 1.0. This copy runs between every pair
// of layers and again onto the visible canvas, so forcing alpha here would undo
// the transparency FRAGMENT so carefully computes — and did: an upper band came
// out opaque black however correct the layer maths was.
void main() { gl_FragColor = texture2D(uTexture, vUV); }
`;
