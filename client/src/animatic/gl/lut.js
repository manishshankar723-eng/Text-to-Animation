/**
 * Fetching the built-in LUTs, and keeping them for the life of the tab.
 *
 * The parsing is in `cube.js`, which imports nothing — see the note there for
 * why that separation matters to the parity test. This file is the browser half:
 * one request per LUT name, shared by every clip that grades with it.
 */

import { getLutFile } from "../../api.js";
import { buildLutPixels, parseCube } from "./cube.js";

export { MAX_LUT_SIZE, LutError, buildLutPixels, parseCube } from "./cube.js";

// One fetch per LUT for the life of the tab. The promise is cached, not the
// result, so twenty clips graded with the same LUT share one request rather
// than racing twenty.
const cache = new Map();

/**
 * A built-in LUT by name, ready to upload. Resolves to null if it can't be had.
 *
 * A missing or broken LUT is a NO-OP on this side too — the monitor shows the
 * ungraded picture and says so in the console, matching what `load_lut` does
 * server-side. Throwing would take the whole preview down over one file.
 */
export function loadLut(name) {
  if (!name) return Promise.resolve(null);
  if (!cache.has(name)) {
    cache.set(
      name,
      getLutFile(name)
        .then((res) => (typeof res?.text === "function" ? res.text() : String(res)))
        .then((text) => buildLutPixels(parseCube(text)))
        .catch((e) => {
          console.warn(`[effects] LUT '${name}' could not be loaded — skipped.`, e);
          return null;
        })
    );
  }
  return cache.get(name);
}

/** Every LUT name a chain wants, so the monitor can wait for them before drawing. */
export function lutNamesIn(clips) {
  const names = new Set();
  for (const clip of clips || []) {
    for (const effect of clip?.effects || []) {
      if (effect.kind === "lut" && effect.params?.name) names.add(effect.params.name);
    }
  }
  return [...names];
}
