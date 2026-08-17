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
import { BLEND, BRIGHTNESS, CHROMA, COMMON, CONTRAST, LUT, MASK, SATURATION } from "./effects.js";

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

uniform sampler2D uBackdrop; // everything composited under this layer
uniform vec2  uResolution;
uniform int   uBlend;

uniform int   uFxCount;
uniform int   uFxKind[${MAX_EFFECTS}];
// x = amount / similarity · y = smoothness · z = spill · w = LUT size
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

${COMMON}
${BRIGHTNESS}
${CONTRAST}
${SATURATION}
${LUT}
${CHROMA}
${MASK}
${BLEND}

#define FX_BRIGHTNESS ${fxIndex("brightness")}
#define FX_CONTRAST   ${fxIndex("contrast")}
#define FX_SATURATION ${fxIndex("saturation")}
#define FX_LUT        ${fxIndex("lut")}
#define FX_CHROMA     ${fxIndex("chroma")}

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
    }
    c = clamp(c, 0.0, 1.0);
  }

  // Opacity multiplies the alpha the chain produced rather than replacing it,
  // so a chroma key or a cut-out PNG stays cut out when the clip is faded.
  a *= uOpacity;
  // The mask is LAST and in FRAME coordinates: it is a region of the picture
  // being made, not of the file that was fed in.
  a *= maskCoverage(vFrame, uMaskKind, uMaskCentre, uMaskHalf, uMaskFeather, uMaskInvert);

  vec3 base = texture2D(uBackdrop, gl_FragCoord.xy / uResolution).rgb;
  gl_FragColor = vec4(mix(base, clamp(blendMode(base, c, uBlend), 0.0, 1.0), clamp(a, 0.0, 1.0)), 1.0);
}
`;

/** A pass-through used to copy one framebuffer into the next, and to the canvas. */
export const COPY_FRAGMENT = /* glsl */ `
precision highp float;
varying vec2 vUV;
uniform sampler2D uTexture;
void main() { gl_FragColor = vec4(texture2D(uTexture, vUV).rgb, 1.0); }
`;
