/**
 * The Program monitor's compositor — WebGL, and the reason it had to stop being
 * the DOM.
 *
 * CSS can fake a brightness slider. It cannot do a 3D LUT, a feathered mask or a
 * chroma key, and `mix-blend-mode` blends a whole ELEMENT rather than one clip
 * against the pixels beneath it. Once effects exist, a DOM preview stops being
 * an approximation of the export and becomes a different picture — which is the
 * one failure an editor cannot survive.
 *
 * WHAT MOVED AND WHAT DIDN'T:
 *
 *     canvas   the pictures (frame A, frame B, the transition between them),
 *              the shapes, and the overlay pictures — everything with pixels.
 *     DOM      the captions, the shot label, and the selection outlines and
 *              resize handles over the shapes and overlays.
 *
 * The handles stayed in the DOM deliberately. Hit-testing and drag handles are
 * most of what a canvas editor costs, and they are exactly the part WebGL adds
 * nothing to. `ProgramCanvas` lays them over the canvas at the same fractions
 * the compositor draws at, so a shape and its handle cannot separate.
 *
 * SHAPES ARE DRAWN HERE TOO, which is not an optimisation — it is forced. The
 * compositing order is picture → shapes → overlays → text, and an overlay's
 * blend mode needs every pixel beneath it. Leaving the shapes in the DOM would
 * put them either above the overlays (wrong order) or outside the backdrop the
 * blend reads (wrong picture).
 *
 * HOW A BLEND MODE IS POSSIBLE AT ALL: two framebuffers, ping-ponged. Each layer
 * copies the composite so far into the other buffer, then draws itself while
 * SAMPLING that copy as its backdrop. Two draws per layer, a handful of layers
 * per frame — cheap, and the only arrangement in which "multiply this overlay
 * into the shot" means anything.
 */

import { DEFAULT_MASK, MASK_KINDS } from "../scene.js";
import { buildLutPixels } from "./cube.js";
import {
  COPY_FRAGMENT,
  FRAGMENT,
  MAX_EFFECTS,
  MAX_LUTS,
  VERTEX,
  blendIndex,
  fxIndex,
} from "./shaders/layer.js";

// ⚠ THE SAME POLYGONS AS `POINTS` in Shapes.jsx and `_SHAPE_POINTS` in
// animatic.py. This is now the THIRD copy, which is exactly the drift the
// project already regrets — but a CSS clip-path, a Pillow polygon and a vertex
// buffer genuinely cannot share one representation. `shapeOutline` below is the
// single place this file reads them, so there is one line to change.
const POINTS = {
  rect: [[0, 0], [1, 0], [1, 1], [0, 1]],
  pentagon: [[0.5, 0], [1, 0.38], [0.82, 1], [0.18, 1], [0, 0.38]],
  star: [
    [0.5, 0], [0.61, 0.35], [0.98, 0.35], [0.68, 0.57],
    [0.79, 0.91], [0.5, 0.7], [0.21, 0.91], [0.32, 0.57],
    [0.02, 0.35], [0.39, 0.35],
  ],
};
// Pillow draws a true ellipse; a fan can only approximate one. 96 segments is
// under a third of a pixel of error on a 1080-tall frame, which is well inside
// what `tests/effects_parity_check.py` allows and far below what an eye finds.
const ELLIPSE_SEGMENTS = 96;

// How many uploaded pictures to keep. Two frames and a handful of overlays are
// all that can be on screen at once; the rest is scrub history, and keeping it
// unbounded is how a long animatic ends up holding every panel in VRAM.
const MAX_TEXTURES = 12;

function shapeOutline(kind) {
  if (kind === "ellipse") {
    const points = [];
    for (let i = 0; i < ELLIPSE_SEGMENTS; i++) {
      const a = (i / ELLIPSE_SEGMENTS) * Math.PI * 2;
      points.push([0.5 + Math.cos(a) / 2, 0.5 + Math.sin(a) / 2]);
    }
    return points;
  }
  return POINTS[kind] || POINTS.rect;
}

function parseColour(value, fallback = [0, 0, 0]) {
  let s = String(value || "").trim().replace(/^#/, "");
  if (s.length === 3) s = [...s].map((c) => c + c).join("");
  if (s.length !== 6) return fallback;
  const n = Number.parseInt(s, 16);
  if (!Number.isFinite(n)) return fallback;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
}

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`shader failed to compile: ${log}`);
  }
  return shader;
}

function link(gl, vertexSource, fragmentSource) {
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`program failed to link: ${gl.getProgramInfoLog(program)}`);
  }
  return program;
}

/**
 * Where one picture lands on the frame, in frame coordinates (0–1, y down).
 *
 * ⚠ TWIN of `place_picture` in `animatic_render.py`. Same "contain" / "cover"
 * rule, same order — the fit, the zoom and the pan are ONE calculation, because
 * doing them in sequence rounds twice and drifts the picture. Expressed as
 * fractions rather than pixels so it is resolution-independent, which is what
 * makes the monitor and a 4K export agree without either knowing the other's
 * size.
 */
export function placePicture(sourceW, sourceH, frameW, frameH, fit, scale, x, y) {
  if (!(sourceW > 0) || !(sourceH > 0)) return { x: 0, y: 0, w: 1, h: 1 };
  const base =
    fit === "cover"
      ? Math.max(frameW / sourceW, frameH / sourceH)
      : Math.min(frameW / sourceW, frameH / sourceH);
  const factor = base * Math.max(0.01, Number(scale) || 1);
  const w = (sourceW * factor) / frameW;
  const h = (sourceH * factor) / frameH;
  // x/y are the picture's CENTRE, the only reading under which a zoom doesn't
  // also shift it.
  return { x: Number(x) - w / 2, y: Number(y) - h / 2, w, h };
}

export class Compositor {
  constructor(canvas) {
    this.canvas = canvas;
    const options = {
      alpha: false,
      antialias: false, // it would only apply to the default framebuffer anyway
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
      premultipliedAlpha: false,
    };
    this.gl =
      canvas.getContext("webgl", options) ||
      canvas.getContext("experimental-webgl", options);
    if (!this.gl) throw new Error("WebGL is not available in this browser.");

    const gl = this.gl;
    this.program = link(gl, VERTEX, FRAGMENT);
    this.copyProgram = link(gl, VERTEX, COPY_FRAGMENT);
    this.buffer = gl.createBuffer();
    this.textures = new Map(); // source key → { texture, width, height, stamp }
    this.luts = new Map(); // lut name → texture
    this.targets = [];
    this.size = [0, 0];
    this.lost = false;

    // A context CAN be lost — a GPU driver reset, a laptop switching cards, too
    // many live contexts. Without this the monitor goes black and stays black
    // with nothing in the console; the flag lets `ProgramCanvas` fall back.
    canvas.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      this.lost = true;
    });
  }

  dispose() {
    const gl = this.gl;
    for (const entry of this.textures.values()) gl.deleteTexture(entry.texture);
    for (const texture of this.luts.values()) gl.deleteTexture(texture);
    for (const target of this.targets) {
      gl.deleteFramebuffer(target.fbo);
      gl.deleteTexture(target.texture);
    }
    this.textures.clear();
    this.luts.clear();
    this.targets = [];
  }

  // --- Resources -----------------------------------------------------------
  _blankTexture() {
    if (!this._blank) {
      const gl = this.gl;
      this._blank = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, this._blank);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
                    new Uint8Array([0, 0, 0, 255]));
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    return this._blank;
  }

  /**
   * Upload (or re-upload) one <img> / <video> / <canvas>.
   *
   * A VIDEO is re-uploaded EVERY frame and everything else is uploaded once.
   * Keying the cache on the element alone would freeze a playing clip on its
   * first frame — the same freeze-frame bug `source_ms` guards against on the
   * export side, arriving by a completely different route.
   *
   * ⚠ `key` MUST INCLUDE THE URL, not just the clip id. A panel redrawn on the
   * storyboard comes back at the same clip id and usually at the same size, so
   * a key of the id alone would hit this cache and show the OLD drawing for the
   * rest of the session — with nothing to suggest the monitor was stale.
   */
  texture(key, element, { animated = false } = {}) {
    const gl = this.gl;
    const width = element.naturalWidth || element.videoWidth || element.width || 0;
    const height = element.naturalHeight || element.videoHeight || element.height || 0;
    if (!width || !height) return null;

    let entry = this.textures.get(key);
    if (entry) {
      // Re-inserted on every hit, so Map order is least-recently-used first and
      // the eviction below is an LRU rather than a FIFO.
      this.textures.delete(key);
    } else {
      entry = { texture: gl.createTexture(), width: 0, height: 0, fresh: false };
      // A sixty-panel animatic would otherwise hold sixty full-size textures for
      // the life of the tab — a quarter of a gigabyte of VRAM to show one
      // picture. Only a couple are ever on screen at once, so a small cache is
      // all a scrub needs, and a re-upload of a blob URL is cheap.
      while (this.textures.size >= MAX_TEXTURES) {
        const [oldest, victim] = this.textures.entries().next().value;
        gl.deleteTexture(victim.texture);
        this.textures.delete(oldest);
      }
    }
    this.textures.set(key, entry);
    gl.bindTexture(gl.TEXTURE_2D, entry.texture);
    if (!entry.fresh || animated || entry.width !== width || entry.height !== height) {
      // FLIP_Y off, and the UVs put v=0 at the picture's top instead — one
      // convention, set in the vertex data, rather than two that can disagree.
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, element);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      entry.width = width;
      entry.height = height;
      entry.fresh = true;
    }
    return entry;
  }

  /**
   * A texture from RAW PIXELS rather than from a DOM element.
   *
   * The browser never needs this — `texture()` hands `texImage2D` the <img> or
   * <video> directly. `tests/effects_parity_check.py` does: it drives this exact
   * compositor under `node` against a headless GL context, where there are no
   * DOM elements at all, and a parity test that uploaded its input by a
   * different path than the shaders read it would be testing the wrong thing.
   */
  texturePixels(key, { width, height, data }) {
    const gl = this.gl;
    let entry = this.textures.get(key);
    if (!entry) {
      entry = { texture: gl.createTexture(), width: 0, height: 0, fresh: false };
      this.textures.set(key, entry);
    }
    gl.bindTexture(gl.TEXTURE_2D, entry.texture);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA,
                  gl.UNSIGNED_BYTE, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    Object.assign(entry, { width, height, fresh: true });
    return entry;
  }

  /** A parsed .cube, uploaded once and kept. `data` is `buildLutPixels`'s shape. */
  lutTexture(name, data) {
    if (this.luts.has(name)) return this.luts.get(name);
    if (!data) return null;
    const gl = this.gl;
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, data.width, data.height, 0,
                  gl.RGBA, gl.UNSIGNED_BYTE, data.pixels);
    // LINEAR gives red and green their interpolation for free; blue is done by
    // hand in `lutLookup`, which is what keeps a lookup inside its own slice.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.luts.set(name, { texture, size: data.size });
    return this.luts.get(name);
  }

  // --- Framebuffers --------------------------------------------------------
  resize(width, height) {
    const gl = this.gl;
    width = Math.max(1, Math.round(width));
    height = Math.max(1, Math.round(height));
    if (this.size[0] === width && this.size[1] === height && this.targets.length) return;

    for (const target of this.targets) {
      gl.deleteFramebuffer(target.fbo);
      gl.deleteTexture(target.texture);
    }
    this.targets = [0, 1].map(() => {
      const texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA,
                    gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      const fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D,
                              texture, 0);
      return { fbo, texture };
    });
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.canvas.width = width;
    this.canvas.height = height;
    this.size = [width, height];
    this.front = 0;
  }

  // --- Drawing -------------------------------------------------------------
  _draw(program, vertices, count, mode) {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.DYNAMIC_DRAW);
    const frameLoc = gl.getAttribLocation(program, "aFrame");
    const uvLoc = gl.getAttribLocation(program, "aUV");
    gl.enableVertexAttribArray(frameLoc);
    gl.vertexAttribPointer(frameLoc, 2, gl.FLOAT, false, 16, 0);
    gl.enableVertexAttribArray(uvLoc);
    gl.vertexAttribPointer(uvLoc, 2, gl.FLOAT, false, 16, 8);
    gl.drawArrays(mode, 0, count);
  }

  /**
   * Copy one texture over a whole target.
   *
   * ⚠ THE UVs ARE FLIPPED. A framebuffer's texture has its origin at the BOTTOM
   * left while frame space runs y DOWN, so a straight copy would draw the
   * picture upside down — once here and once again on the way to the canvas,
   * which cancels out and is therefore invisible until the day one of the two
   * is changed.
   */
  _copy(sourceTexture, targetFbo) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, targetFbo);
    gl.viewport(0, 0, this.size[0], this.size[1]);
    gl.useProgram(this.copyProgram);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, sourceTexture);
    gl.uniform1i(gl.getUniformLocation(this.copyProgram, "uTexture"), 0);
    this._draw(
      this.copyProgram,
      // frame x, frame y, u, v — v inverted against y, as above.
      new Float32Array([
        0, 0, 0, 1, 1, 0, 1, 1, 0, 1, 0, 0,
        0, 1, 0, 0, 1, 0, 1, 1, 1, 1, 1, 0,
      ]),
      6,
      gl.TRIANGLES
    );
  }

  _setLook(look, luts) {
    const gl = this.gl;
    const p = this.program;
    const u = (name) => gl.getUniformLocation(p, name);

    const effects = (look?.effects || []).slice(0, MAX_EFFECTS);
    gl.uniform1i(u("uFxCount"), effects.length);
    let lutSlot = 0;
    effects.forEach((effect, i) => {
      const params = effect.params || {};
      gl.uniform1i(u(`uFxKind[${i}]`), fxIndex(effect.kind));
      let slot = -1;
      let size = 2;
      if (effect.kind === "lut") {
        const lut = luts?.get(params.name);
        if (lut && lutSlot < MAX_LUTS) {
          slot = lutSlot;
          size = lut.size;
          gl.activeTexture(gl.TEXTURE2 + slot);
          gl.bindTexture(gl.TEXTURE_2D, lut.texture);
          gl.uniform1i(u(`uLut${slot}`), 2 + slot);
          lutSlot += 1;
        }
      }
      gl.uniform1i(u(`uFxLutSlot[${i}]`), Math.max(0, slot));
      gl.uniform4f(
        u(`uFxArgs[${i}]`),
        // `similarity` and `amount` share the slot: no effect has both.
        Number(effect.kind === "chroma" ? params.similarity : params.amount) || 0,
        Number(params.smoothness) || 0,
        Number(params.spill) || 0,
        // A LUT that hasn't loaded grades with `amount` forced to 0 below, so
        // the size here only has to be legal, never right.
        size
      );
      gl.uniform3fv(u(`uFxColor[${i}]`), parseColour(params.color, [0, 1, 0]));
      if (effect.kind === "lut" && slot < 0) {
        // Not loaded (or a third LUT in one chain): grade by nothing rather
        // than by whatever texture happens to be bound, which would be a
        // preview showing a grade the export doesn't have.
        gl.uniform4f(u(`uFxArgs[${i}]`), 0, 0, 0, 2);
      }
    });
    // The unused tail still has to name a legal sampler slot, or some drivers
    // refuse to link on the spot.
    for (let i = effects.length; i < MAX_EFFECTS; i++) {
      gl.uniform1i(u(`uFxKind[${i}]`), -1);
      gl.uniform1i(u(`uFxLutSlot[${i}]`), 0);
    }
    gl.uniform1i(u("uLut0"), 2);
    gl.uniform1i(u("uLut1"), 3);

    const mask = look?.mask || DEFAULT_MASK;
    const kindIndex = Math.max(0, MASK_KINDS.indexOf(mask.kind || "none"));
    gl.uniform1i(u("uMaskKind"), kindIndex);
    gl.uniform2f(u("uMaskCentre"), Number(mask.x ?? 0.5), Number(mask.y ?? 0.5));
    gl.uniform2f(u("uMaskHalf"),
                 Math.abs(Number(mask.w ?? 0.5)) / 2, Math.abs(Number(mask.h ?? 0.5)) / 2);
    gl.uniform1f(u("uMaskFeather"), Number(mask.feather ?? 0.1));
    gl.uniform1f(u("uMaskInvert"), mask.invert ? 1 : 0);
    gl.uniform1i(u("uBlend"), blendIndex(look?.blend || "normal"));
  }

  /**
   * One layer, onto whatever is composited so far.
   *
   * `vertices` are already in frame space. `source` is either a texture entry
   * from `texture()` or `{ color }` for a flat fill.
   */
  layer({ vertices, count, mode, source, opacity = 1, useAlpha = true, look, luts }) {
    const gl = this.gl;
    const back = this.targets[this.front];
    const front = this.targets[1 - this.front];
    // The composite so far, copied forward so the parts of the frame this layer
    // doesn't touch survive, and read back as this layer's backdrop.
    this._copy(back.texture, front.fbo);

    gl.bindFramebuffer(gl.FRAMEBUFFER, front.fbo);
    gl.viewport(0, 0, this.size[0], this.size[1]);
    gl.useProgram(this.program);
    const u = (name) => gl.getUniformLocation(this.program, name);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, source?.texture || this._blankTexture());
    gl.uniform1i(u("uTexture"), 0);
    gl.uniform1f(u("uUseTexture"), source?.texture ? 1 : 0);
    gl.uniform1f(u("uUseAlpha"), useAlpha ? 1 : 0);
    gl.uniform3fv(u("uColor"), source?.color || [0, 0, 0]);
    gl.uniform1f(u("uOpacity"), Math.max(0, Math.min(1, opacity)));

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, back.texture);
    gl.uniform1i(u("uBackdrop"), 1);
    gl.uniform2f(u("uResolution"), this.size[0], this.size[1]);

    this._setLook(look, luts);
    this._draw(this.program, vertices, count, mode);
    this.front = 1 - this.front;
  }

  /** Start a frame: everything cleared to the bar colour. */
  begin(background) {
    const gl = this.gl;
    const [r, g, b] = parseColour(background);
    this.front = 0;
    for (const target of this.targets) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, target.fbo);
      gl.viewport(0, 0, this.size[0], this.size[1]);
      gl.clearColor(r, g, b, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
    }
  }

  /** Finish a frame: the composite onto the canvas the user is looking at. */
  end() {
    this._copy(this.targets[this.front].texture, null);
    this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, null);
  }

  /** Read the finished frame back as RGBA — used by the parity harness only. */
  readPixels() {
    const gl = this.gl;
    const [width, height] = this.size;
    const out = new Uint8Array(width * height * 4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.targets[this.front].fbo);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, out);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    // Rows come back bottom-up out of a framebuffer; hand them back top-down so
    // the caller can compare them with a PNG without knowing any of this.
    const flipped = new Uint8Array(out.length);
    const stride = width * 4;
    for (let y = 0; y < height; y++) {
      flipped.set(out.subarray((height - 1 - y) * stride, (height - y) * stride), y * stride);
    }
    return flipped;
  }
}

// ---------------------------------------------------------------------------
// Geometry, in frame space (0–1, y down). Built here rather than in GLSL so it
// can mirror the Python placement line for line.
// ---------------------------------------------------------------------------
/** Two triangles covering a rect, with UVs. */
export function quad({ x, y, w, h }, uv = { x: 0, y: 0, w: 1, h: 1 }) {
  const x1 = x + w;
  const y1 = y + h;
  const u1 = uv.x + uv.w;
  const v1 = uv.y + uv.h;
  return new Float32Array([
    x, y, uv.x, uv.y, x1, y, u1, uv.y, x, y1, uv.x, v1,
    x, y1, uv.x, v1, x1, y, u1, uv.y, x1, y1, u1, v1,
  ]);
}

/**
 * Where one OVERLAY picture lands, in frame coordinates.
 *
 * ⚠ TWIN of the sizing in `draw_overlays`. The picture is fitted INSIDE its box
 * preserving aspect ("contain"), so a logo dropped into a square box is not
 * stretched into a different logo — and it scales BOTH ways, which is why the
 * exporter uses `resize` rather than `thumbnail`.
 *
 * Worked in pixels and converted back, rather than in fractions throughout,
 * because "contain" is a question about the picture's real aspect against the
 * box's real aspect and the frame is not square.
 */
export function overlayRect(item, sourceW, sourceH, frameW, frameH) {
  const boxW = Math.abs(Number(item.w ?? 0.3)) * frameW;
  const boxH = Math.abs(Number(item.h ?? 0.3)) * frameH;
  if (!(sourceW > 0) || !(sourceH > 0)) return { w: 0, h: 0 };
  const scale = Math.min(boxW / sourceW, boxH / sourceH);
  return { w: (sourceW * scale) / frameW, h: (sourceH * scale) / frameH };
}

/**
 * A textured quad centred at (cx, cy), rotated CLOCKWISE about that centre.
 *
 * Clockwise to match CSS and the editor's own handles, which is why
 * `draw_overlays` negates the angle for Pillow — its rotation runs the other
 * way. Rotating about the centre is also what makes `x`/`y` mean the same thing
 * before and after a rotation, and is why the project stores centres at all.
 */
export function rotatedQuad(cx, cy, w, h, rotation) {
  const angle = ((Number(rotation) || 0) * Math.PI) / 180;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const corners = [[0, 0], [1, 0], [0, 1], [0, 1], [1, 0], [1, 1]];
  const out = new Float32Array(corners.length * 4);
  corners.forEach(([u, v], i) => {
    const dx = (u - 0.5) * w;
    const dy = (v - 0.5) * h;
    out[i * 4] = cx + dx * cos - dy * sin;
    out[i * 4 + 1] = cy + dx * sin + dy * cos;
    out[i * 4 + 2] = u;
    out[i * 4 + 3] = v;
  });
  return out;
}

/**
 * A shape's outline as a triangle fan, centred, sized and rotated.
 *
 * ⚠ THE FAN IS ANCHORED AT THE CENTRE, not at the first point. A star is
 * CONCAVE: fanning from one of its own tips puts triangles outside the outline
 * and draws a mess that still looks vaguely star-shaped, which is the worst
 * kind of wrong. Every shape here is star-shaped about its centre, so a centre
 * fan triangulates all four correctly — and the loop is closed by repeating the
 * first point.
 *
 * ⚠ ROTATION IS CLOCKWISE, like CSS and like the editor's handles — which is
 * why `draw_shapes` NEGATES the angle for Pillow, whose rotation runs the other
 * way. Aspect is not corrected: a rotated shape shears with the frame here
 * exactly as it does in the exporter, because both work in frame fractions.
 */
export function shapeFan(shape) {
  const points = shapeOutline((shape.kind || "rect").toLowerCase());
  const cx = Number(shape.x ?? 0.5);
  const cy = Number(shape.y ?? 0.5);
  const w = Math.abs(Number(shape.w ?? 0.25));
  const h = Math.abs(Number(shape.h ?? 0.25));
  const angle = ((Number(shape.rotation) || 0) * Math.PI) / 180;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);

  const ring = [[0.5, 0.5], ...points, points[0]];
  const out = new Float32Array(ring.length * 4);
  ring.forEach(([px, py], i) => {
    const dx = (px - 0.5) * w;
    const dy = (py - 0.5) * h;
    out[i * 4] = cx + dx * cos - dy * sin;
    out[i * 4 + 1] = cy + dx * sin + dy * cos;
    out[i * 4 + 2] = px;
    out[i * 4 + 3] = py;
  });
  return { vertices: out, count: ring.length };
}

export { buildLutPixels, MAX_EFFECTS, MAX_LUTS };
