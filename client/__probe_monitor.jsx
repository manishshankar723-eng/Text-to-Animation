
import React, { useState } from "react";
import { createRoot } from "react-dom/client";

import { Compositor } from "/src/animatic/gl/compositor.js";
import ProgramCanvas from "/src/components/ProgramCanvas.jsx";
import { sceneAt } from "/src/animatic/scene.js";

const probe = { renders: 0, disposes: 0, errors: [], ready: false };
window.__probe = probe;

window.addEventListener("error", (e) => probe.errors.push(String(e.message || e)));
window.addEventListener("unhandledrejection", (e) =>
  probe.errors.push("unhandled rejection: " + String(e.reason))
);
const realError = console.error;
console.error = (...args) => {
  probe.errors.push(args.map(String).join(" "));
  realError.apply(console, args);
};

const realEnd = Compositor.prototype.end;
Compositor.prototype.end = function patchedEnd() {
  probe.compositor = this;
  return realEnd.apply(this, arguments);
};
const realDispose = Compositor.prototype.dispose;
Compositor.prototype.dispose = function patchedDispose() {
  probe.disposes += 1;
  return realDispose.apply(this, arguments);
};

const SETTINGS = { fit: "contain", background: "#000000", aspect_ratio: "16:9" };

function Harness() {
  probe.renders += 1;
  const [effects, setEffects] = useState([]);
  probe.setEffects = setEffects;
  // A COLOUR CARD, so there is no image to load, no fit and no resample — the
  // read-back value is the card's colour with the chain applied and nothing else.
  const frames = [{ id: "f1", kind: "color", color: "#4a86c8",
                    duration_ms: 2000, effects }];
  const scene = sceneAt({ frames, texts: [], shapes: [], overlays: [],
                          transitions: [], settings: SETTINGS }, 0);
  return (
    <ProgramCanvas
      scene={scene}
      frames={frames}
      urls={{}}
      videoUrls={{}}
      overlayUrls={{}}
      settings={SETTINGS}
      videoElsRef={{ current: {} }}
      onUnavailable={(e) => { probe.unavailable = String(e); }}
    />
  );
}

createRoot(document.getElementById("root")).render(<Harness />);

/** The centre pixel of the finished frame, straight out of the framebuffer. */
probe.read = () => {
  const comp = probe.compositor;
  if (!comp || !document.querySelector("canvas")) return null;
  const [w, h] = comp.size;
  const px = comp.readPixels();
  const i = (Math.round(h / 2) * w + Math.round(w / 2)) * 4;
  return {
    rgb: [px[i], px[i + 1], px[i + 2]],
    glError: comp.gl.getError(),
    renders: probe.renders,
    disposes: probe.disposes,
    errors: probe.errors.slice(),
    canvas: Boolean(document.querySelector("canvas")),
  };
};

/** `dispose()` with a LUT texture in the map — the exact call that threw. */
probe.disposeIsSafe = () => {
  try {
    const comp = probe.compositor;
    if (!comp) return "no compositor";
    if (!comp.luts.size) return "no LUT was uploaded — the check would prove nothing";
    comp.dispose();
    return true;
  } catch (e) {
    return String(e && e.message || e);
  }
};

probe.ready = true;
