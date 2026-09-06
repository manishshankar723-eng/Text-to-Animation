/**
 * fx_overlays.js — THE CATALOGUE ONLY. The pictures are made on the server.
 *
 * ⚠ TWIN OF `fx_overlays.py`, and the split between them is the whole design.
 * That module generates a light leak, a grain pass or a glitch as an ordinary
 * MP4 and stores it as an ordinary video upload; this file is the SHELF the
 * browser draws — ids, labels, notes, blend modes and default lengths — and it
 * computes no pixels at all. `tests/fx_overlay_check.py` compares the two entry
 * for entry by running this file under node, exactly as the font list and the
 * caption styles are compared.
 *
 * ⚠ AND NOTHING HERE IS A NEW KIND OF ANYTHING. An overlay lands on the timeline
 * as a picture clip of kind `video` with a `blend` mode — both of which this
 * editor has had for a long time and both of which the Program monitor and the
 * exporter already honour. So sixteen "wow" effects cost ZERO renderer changes,
 * and every one of them can be trimmed, retimed, faded, keyframed and deleted
 * like any other clip the moment it exists.
 *
 * ⚠ THE BLEND MODE TRAVELS WITH THE EFFECT. A light leak on "normal" is an
 * opaque orange rectangle over the shot; the same file on "screen" is a light
 * leak. So the mode is a property of the entry rather than something a person
 * has to know to set afterwards, and dropping one is a single gesture.
 *
 * ⚠ GENERATED RATHER THAN BOUGHT, WHICH IS A LICENSING DECISION. The usual way
 * to ship this is a purchased pack of stock overlays — money, plus a licence
 * every customer's export has to honour. These are drawn from nothing by numpy,
 * so there is no fee, no attribution, nothing to renew and nothing that can be
 * revoked; and because they are generated on demand they come out at the exact
 * frame size the project needs instead of being scaled to fit.
 */

/**
 * The shelves, in order. A VIEW, not the truth — an overlay whose `category`
 * names none of these still appears, under "Other", the same rule
 * `fx_library.js` and the preset shelves keep.
 */
export const OVERLAY_CATEGORIES = [
  { id: "light", label: "Light", note: "Leaks, flares and sweeps. Drop on 'screen'." },
  { id: "particles", label: "In the air", note: "Things drifting between the lens and the shot." },
  { id: "texture", label: "Texture", note: "Grain, scratches and scanlines over everything." },
  { id: "glitch", label: "Glitch", note: "Digital damage, in bursts." },
];

/**
 * ⚠ APPENDED, NEVER RE-ORDERED, AND AN ID IS FOREVER. A generated overlay turns
 * into an ordinary upload the moment it is made, so renaming an id cannot
 * corrupt an existing project — but this shelf, the route and `fx_overlays.py`
 * are all addressed by it.
 *
 * `seconds` is what the shelf ASKS FOR, not a law: the route clamps it and the
 * clip can be trimmed like any other afterwards.
 */
export const OVERLAYS = [
  { id: "light-leak-warm", label: "Light leak — warm", category: "light", blend: "screen", seconds: 5.0, note: "Orange and gold bleeding in from the edge, drifting." },
  { id: "light-leak-cool", label: "Light leak — cool", category: "light", blend: "screen", seconds: 5.0, note: "The same idea in blue and magenta. Night, neon, a screen's glow." },
  { id: "light-sweep", label: "Light sweep", category: "light", blend: "screen", seconds: 2.0, note: "A soft bar of light travelling across the frame once." },
  { id: "god-rays", label: "Sun rays", category: "light", blend: "screen", seconds: 6.0, note: "Angled shafts from a corner, breathing slowly." },
  { id: "film-burn", label: "Film burn", category: "light", blend: "screen", seconds: 1.6, note: "A flare blooms out of one corner and burns away. Good ON a cut." },
  { id: "flash", label: "Flash", category: "light", blend: "screen", seconds: 0.6, note: "One white pulse. A camera, a hit, a beat." },
  { id: "bokeh", label: "Bokeh", category: "particles", blend: "screen", seconds: 6.0, note: "Out-of-focus circles of light drifting past." },
  { id: "dust-motes", label: "Dust motes", category: "particles", blend: "screen", seconds: 6.0, note: "Specks floating in a sunbeam. Very quiet, very expensive-looking." },
  { id: "sparkle", label: "Sparkle", category: "particles", blend: "screen", seconds: 4.0, note: "Small stars that twinkle on and off. Festive, magical." },
  { id: "snow", label: "Snow", category: "particles", blend: "screen", seconds: 6.0, note: "Flakes drifting down and sideways." },
  { id: "rain", label: "Rain", category: "particles", blend: "screen", seconds: 5.0, note: "Angled streaks falling fast." },
  { id: "grain", label: "Film grain", category: "texture", blend: "overlay", seconds: 4.0, note: "Fine moving grain over everything. Takes the digital edge off." },
  { id: "old-film", label: "Old film", category: "texture", blend: "overlay", seconds: 5.0, note: "Scratches, specks and a flicker in the exposure." },
  { id: "vhs", label: "VHS", category: "texture", blend: "overlay", seconds: 4.0, note: "Scanlines and bands that tear sideways." },
  { id: "vignette", label: "Vignette", category: "texture", blend: "multiply", seconds: 6.0, note: "Darkens the corners and breathes. Pulls the eye to the middle." },
  { id: "glitch", label: "Glitch", category: "glitch", blend: "screen", seconds: 2.0, note: "Torn bands of red and cyan, in bursts. For a hard cut." },
];

export const OVERLAY_IDS = OVERLAYS.map((o) => o.id);

export function overlayEntry(id) {
  return OVERLAYS.find((o) => o.id === id) || null;
}
