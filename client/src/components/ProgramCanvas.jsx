// ProgramCanvas.jsx — the Program monitor's picture, drawn with WebGL.
//
// It replaces the <img> / <video> / colour <div> stack the monitor used to be.
// CSS can fake a brightness slider; it cannot do a 3D LUT, a feathered mask or
// a chroma key, and `mix-blend-mode` blends a whole ELEMENT rather than one
// clip against the pixels beneath it. See `animatic/gl/compositor.js` for the
// design, and for why the SHAPES had to move into the canvas as well.
//
// WHAT STAYED IN THE DOM, and is passed in as `children`: the captions, the
// shot label, and the selection outlines and resize handles. Hit-testing and
// drag handles are most of what a canvas editor costs and exactly the part
// WebGL adds nothing to. They are positioned in the same fractions the
// compositor draws at, so a shape and its handle cannot separate.
//
// The media elements are HIDDEN, not absent. A <video> has to be in the
// document to decode, and `useMonitorVideo` drives them through `videoElsRef`
// exactly as it did when they were the picture — so the clock, the seeking and
// the drift correction are untouched by any of this.

import { useEffect, useMemo, useRef, useState } from "react";

import {
  Compositor,
  overlayRect,
  placePicture,
  quad,
  rotatedQuad,
  shapeFan,
} from "../animatic/gl/compositor.js";
import { loadLut, lutNamesIn } from "../animatic/gl/lut.js";

// The canvas is drawn at the device's real pixel density, up to a point. Past
// this a 4K monitor would have us compositing 16 megapixels per playhead move
// for a preview a few hundred pixels wide.
const MAX_BACKING_PX = 1920;

export default function ProgramCanvas({
  scene,
  frames,
  urls,
  videoUrls,
  overlayUrls,
  settings,
  videoElsRef,
  onUnavailable,
}) {
  const canvasRef = useRef(null);
  const compositorRef = useRef(null);
  const mediaRef = useRef(new Map()); // url → the <img> / <video> element
  const [ready, setReady] = useState(0); // bumped when a picture finishes loading
  const [luts, setLuts] = useState(() => new Map());

  const pictures = useMemo(
    () => [scene.frame, scene.frame_b].filter(Boolean),
    [scene.frame, scene.frame_b]
  );

  // --- What has to be in the document for this moment ----------------------
  // Only what is ON SCREEN NOW, never the whole project: an animatic is often
  // sixty panels, and decoding sixty stills to show one is how a preview eats a
  // gigabyte. React keys these by url, so a picture that stays up across a
  // playhead move keeps its element and its decode.
  const sources = useMemo(() => {
    const out = [];
    for (const picture of pictures) {
      const clip = frames[picture.index];
      if (!clip || picture.kind === "color") continue;
      if (picture.kind === "video") {
        const url = videoUrls[clip.src?.upload_id];
        // Until the blob lands the clip shows its own thumbnail, which is
        // already fetched — a black rectangle while a 100MB file downloads
        // looks like a broken clip rather than a loading one.
        if (url) out.push({ key: clip.id, url, kind: "video", clipId: clip.id });
        else if (urls[clip.id]) out.push({ key: clip.id, url: urls[clip.id], kind: "image" });
        continue;
      }
      if (urls[clip.id]) out.push({ key: clip.id, url: urls[clip.id], kind: "image" });
    }
    for (const overlay of scene.overlays || []) {
      const url = overlayUrls[overlay.upload_id];
      if (url) out.push({ key: `ov:${overlay.id}`, url, kind: "image" });
    }
    return out;
  }, [pictures, frames, urls, videoUrls, overlayUrls, scene.overlays]);

  // --- LUTs ----------------------------------------------------------------
  // Fetched once per name for the life of the tab (`loadLut` caches the
  // promise). Until one arrives the clip draws UNGRADED rather than with some
  // other clip's table — a preview showing a grade the export doesn't have is
  // worse than a preview showing none yet.
  const lutNames = useMemo(
    () => lutNamesIn([...pictures, ...(scene.overlays || [])]).sort().join("|"),
    [pictures, scene.overlays]
  );
  useEffect(() => {
    if (!lutNames) return;
    let live = true;
    const names = lutNames.split("|").filter(Boolean);
    Promise.all(names.map((name) => loadLut(name).then((data) => [name, data]))).then(
      (loaded) => {
        if (!live) return;
        setLuts((current) => {
          const next = new Map(current);
          let changed = false;
          for (const [name, data] of loaded) {
            if (data && !next.has(name)) {
              next.set(name, data);
              changed = true;
            }
          }
          return changed ? next : current;
        });
      }
    );
    return () => {
      live = false;
    };
  }, [lutNames]);

  // --- The context ---------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    try {
      compositorRef.current = new Compositor(canvas);
    } catch (e) {
      console.error("[monitor] WebGL is unavailable — the preview cannot draw.", e);
      onUnavailable?.(e);
      return undefined;
    }
    const compositor = compositorRef.current;
    return () => {
      compositor.dispose();
      compositorRef.current = null;
    };
  }, [onUnavailable]);

  // --- The frame -----------------------------------------------------------
  // Runs on every scene change, which is every playhead move — the transport
  // already re-renders this component per tick, so there is no animation loop
  // of its own to keep in step with the clock. A video's texture is re-uploaded
  // each time (`animated: true`), which is what stops a playing clip freezing
  // on its first frame.
  useEffect(() => {
    const compositor = compositorRef.current;
    const canvas = canvasRef.current;
    if (!compositor || !canvas || compositor.lost) return;

    const box = canvas.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const density = Math.min(window.devicePixelRatio || 1, MAX_BACKING_PX / box.width);
    const width = Math.max(1, Math.round(box.width * Math.max(1, density)));
    const height = Math.max(1, Math.round(box.height * Math.max(1, density)));
    compositor.resize(width, height);

    const lutTextures = new Map();
    for (const [name, data] of luts) lutTextures.set(name, compositor.lutTexture(name, data));

    const elementFor = (key) => mediaRef.current.get(key) || null;
    // ⚠ The texture key is the media KEY PLUS ITS URL. A panel redrawn on the
    // storyboard comes back at the same clip id and often the same size, so a
    // key of the id alone would keep showing the old drawing — see `texture()`.
    const urlFor = (key) => sources.find((s) => s.key === key)?.url || "";
    const textureKey = (key) => `${key}|${urlFor(key)}`;
    const fit = settings.fit === "cover" ? "cover" : "contain";

    /** One picture: still, video frame or colour card, placed and graded. */
    const drawPicture = (picture, { opacity = 1, clipRight = null, shiftX = 0 } = {}) => {
      if (!picture) return;
      const clip = frames[picture.index];
      if (!clip) return;
      const look = { effects: picture.effects, mask: picture.mask, blend: picture.blend };
      const alpha = (picture.opacity ?? 1) * opacity;
      if (alpha <= 0) return;

      if (picture.kind === "color") {
        // A card fills the frame edge to edge. `fit`, `scale` and the pan are
        // deliberately ignored — there is no picture to letterbox, and a
        // "zoomed" flat colour is the same flat colour.
        let rect = { x: shiftX, y: 0, w: 1, h: 1 };
        let uv = { x: 0, y: 0, w: 1, h: 1 };
        if (clipRight !== null) {
          rect = { ...rect, w: clipRight };
          uv = { ...uv, w: clipRight };
        }
        compositor.layer({
          vertices: quad(rect, uv),
          count: 6,
          mode: compositor.gl.TRIANGLES,
          source: { color: hexToRgb(picture.color) },
          opacity: alpha,
          useAlpha: false,
          look,
          luts: lutTextures,
        });
        return;
      }

      const element = elementFor(clip.id);
      if (!element) return;
      const isVideo = element.tagName === "VIDEO";
      const sourceW = element.naturalWidth || element.videoWidth || 0;
      const sourceH = element.naturalHeight || element.videoHeight || 0;
      const entry = compositor.texture(textureKey(clip.id), element, { animated: isVideo });
      if (!entry || !sourceW || !sourceH) return;

      let rect = placePicture(sourceW, sourceH, width, height, fit,
                             picture.scale, picture.x, picture.y);
      rect = { ...rect, x: rect.x + shiftX };
      let uv = { x: 0, y: 0, w: 1, h: 1 };
      if (clipRight !== null) {
        // A wipe: keep only the part of this picture left of the edge, and cut
        // the UVs by the same fraction so the picture doesn't squash into it.
        const right = Math.min(rect.x + rect.w, clipRight);
        if (right <= rect.x) return;
        uv = { ...uv, w: (right - rect.x) / rect.w };
        rect = { ...rect, w: right - rect.x };
      }
      compositor.layer({
        vertices: quad(rect, uv),
        count: 6,
        mode: compositor.gl.TRIANGLES,
        source: entry,
        opacity: alpha,
        // ⚠ FRAMES ARE FORCED OPAQUE, matching `_picture_layer`'s deliberate
        // `convert("RGB")`. A source PNG's own transparency has never shown the
        // backdrop through a frame, and honouring it now would silently change
        // every animatic that ever used a cut-out still. Alpha on a frame is
        // what the chroma key is for.
        useAlpha: false,
        look,
        luts: lutTextures,
      });
    };

    compositor.begin(settings.background || "#000000");

    const mix = scene.mix || 0;
    const kind = scene.transition;
    if (scene.frame_b && kind) {
      // ⚠ Each branch is the counterpart of one in `_transition_canvas`
      // (animatic.py): the same fractions travelling the same way. The incoming
      // picture is composited OVER the outgoing one on both sides now, which is
      // what closed the old preview/export gap on a faded or keyed clip.
      if (kind === "dip") {
        // Out through the bar colour and back up: only ever ONE picture is up.
        if (mix < 0.5) drawPicture(scene.frame, { opacity: 1 - 2 * mix });
        else drawPicture(scene.frame_b, { opacity: 2 * mix - 1 });
      } else if (kind === "wipe") {
        drawPicture(scene.frame);
        drawPicture(scene.frame_b, { clipRight: mix });
      } else if (kind === "slide") {
        drawPicture(scene.frame, { shiftX: -mix });
        drawPicture(scene.frame_b, { shiftX: 1 - mix });
      } else {
        drawPicture(scene.frame);
        drawPicture(scene.frame_b, { opacity: mix });
      }
    } else {
      drawPicture(scene.frame);
    }

    // Bottom to top: picture → shapes → overlay pictures. Text is DOM, above
    // all of it. `render_frame` stacks them the same way, which is the whole
    // reason the shapes are in here rather than left in the DOM.
    for (const shape of scene.shapes || []) {
      const fan = shapeFan(shape);
      compositor.layer({
        vertices: fan.vertices,
        count: fan.count,
        mode: compositor.gl.TRIANGLE_FAN,
        source: { color: hexToRgb(shape.color || "#c2185b") },
        opacity: shape.opacity ?? 1,
        useAlpha: false,
        look: null,
        luts: lutTextures,
      });
    }

    for (const overlay of scene.overlays || []) {
      const element = elementFor(`ov:${overlay.id}`);
      if (!element) continue;
      const sourceW = element.naturalWidth || 0;
      const sourceH = element.naturalHeight || 0;
      const entry = compositor.texture(textureKey(`ov:${overlay.id}`), element);
      if (!entry || !sourceW || !sourceH) continue;
      const { w, h } = overlayRect(overlay, sourceW, sourceH, width, height);
      if (!(w > 0) || !(h > 0)) continue;
      compositor.layer({
        vertices: rotatedQuad(overlay.x, overlay.y, w, h, overlay.rotation),
        count: 6,
        mode: compositor.gl.TRIANGLES,
        source: entry,
        opacity: overlay.opacity ?? 1,
        // An overlay KEEPS its own alpha — a cut-out PNG logo is the ordinary
        // case here, and the opposite of a frame.
        useAlpha: true,
        look: { effects: overlay.effects, mask: overlay.mask, blend: overlay.blend },
        luts: lutTextures,
      });
    }

    compositor.end();
  }, [scene, frames, settings.fit, settings.background, luts, ready, sources]);

  return (
    <>
      <canvas className="an-screen-gl" ref={canvasRef} />
      {/* Hidden, not absent: a <video> has to be in the document to decode, and
          `useMonitorVideo` drives these through `videoElsRef` exactly as it did
          when they WERE the picture. `aria-hidden` because they are the
          canvas's raw material, not content. */}
      <div className="an-gl-sources" aria-hidden="true">
        {sources.map((source) =>
          source.kind === "video" ? (
            <video
              // Keyed and reffed by CLIP id, not upload id: two clips cut from
              // the same take can both be on screen during a transition, and
              // keying by the source would give them one element between them.
              key={source.key}
              ref={(el) => {
                if (el) {
                  mediaRef.current.set(source.key, el);
                  videoElsRef.current[source.clipId] = el;
                } else {
                  mediaRef.current.delete(source.key);
                  delete videoElsRef.current[source.clipId];
                }
              }}
              src={source.url}
              // ⚠ MUTED, and not an oversight. The export mixes only the
              // animatic's own audio tracks — a video clip's soundtrack is not
              // in the MP4 (the frames are extracted with `-an`) — so letting
              // it play here would be a preview that lies in the one way you
              // can't see. Give a clip's audio its own track and it will be
              // both audible and exported.
              muted
              playsInline
              preload="auto"
              crossOrigin="anonymous"
              onLoadedData={() => setReady((n) => n + 1)}
            />
          ) : (
            <img
              key={source.key + source.url}
              ref={(el) => {
                if (el) mediaRef.current.set(source.key, el);
                else mediaRef.current.delete(source.key);
              }}
              src={source.url}
              alt=""
              crossOrigin="anonymous"
              onLoad={() => setReady((n) => n + 1)}
            />
          )
        )}
      </div>
    </>
  );
}

function hexToRgb(value) {
  let s = String(value || "").trim().replace(/^#/, "");
  if (s.length === 3) s = [...s].map((c) => c + c).join("");
  if (s.length !== 6) return [0, 0, 0];
  const n = Number.parseInt(s, 16);
  if (!Number.isFinite(n)) return [0, 0, 0];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
}
