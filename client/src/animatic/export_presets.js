// export_presets.js — "make me a file for X", as a named set of export settings.
//
// ⚠ TWIN FILE: `export_presets.py`. The dialog has to show the size and the
// frame rate the ENCODER will use, before anything is encoded — the same reason
// `BASE_SIZES` in `AnimaticEditor.jsx` mirrors `resolve_size()`. Every number
// below is compared against the Python table, field for field, by
// `tests/export_perf_check.py` (which runs this file through node). If you add
// a preset here, add it there in the same position.
//
// Three rules carry the table, and they are stated in full in the Python twin:
//   1. A preset states ONLY what it means — GIF and Still deliberately do not
//      state an aspect ratio, so exporting a thumbnail cannot reshape the film.
//   2. Applying one is a plain object spread, never a rebuild: a field the
//      preset doesn't state keeps whatever the project had.
//   3. `container` is the only field that isn't a number — "mp4" | "gif" |
//      "png" — and `animatic.py` is what honours it.

export const GIF_SHORT_EDGE = 480;
export const GIF_FPS = 12;

// ⚠ ORDER IS THE DIALOG'S ORDER, and it is the Python table's order.
export const PRESETS = [
  {
    id: "youtube",
    label: "YouTube",
    hint: "1080p · 16:9 · 30 fps · MP4",
    aspect_ratio: "16:9",
    resolution: 1080,
    fps: 30,
    quality: "high",
    container: "mp4",
    audio: true,
  },
  {
    id: "tiktok",
    label: "TikTok",
    hint: "1080×1920 · 9:16 · 30 fps · MP4",
    aspect_ratio: "9:16",
    resolution: 1080,
    fps: 30,
    quality: "high",
    container: "mp4",
    audio: true,
  },
  {
    // Technically identical to TikTok, and deliberately still its own row — the
    // person exporting is thinking about a destination, not a codec.
    id: "reels",
    label: "Instagram Reels",
    hint: "1080×1920 · 9:16 · 30 fps · MP4",
    aspect_ratio: "9:16",
    resolution: 1080,
    fps: 30,
    quality: "high",
    container: "mp4",
    audio: true,
  },
  {
    id: "gif",
    label: "Animated GIF",
    hint: `${GIF_SHORT_EDGE}p · ${GIF_FPS} fps · silent · keeps your shape`,
    resolution: GIF_SHORT_EDGE,
    fps: GIF_FPS,
    container: "gif",
    audio: false,
  },
  {
    id: "still",
    label: "Still image (PNG)",
    hint: "one frame, at the playhead · keeps your shape",
    resolution: 1080,
    container: "png",
    audio: false,
  },
];

export const SETTING_FIELDS = ["aspect_ratio", "resolution", "fps", "quality", "container"];
export const CONTAINERS = ["mp4", "gif", "png"];
export const CONTAINER_EXT = { mp4: "mp4", gif: "gif", png: "png" };
export const SILENT_CONTAINERS = ["gif", "png"];

// One row of the table, or null for an id we don't know. An unknown id is never
// an error: a project saved by a newer client can name a preset this build has
// not heard of, and falling back to the settings already on it is the same rule
// an unrecognised transition `kind` follows.
export function preset(id) {
  const key = String(id || "").trim().toLowerCase();
  return PRESETS.find((p) => p.id === key) || null;
}

// `settings` with the named preset written over it — a COPY, never in place.
export function applyPreset(id, settings) {
  const row = preset(id);
  const out = { ...(settings || {}), preset: row ? row.id : "" };
  if (!row) return out;
  for (const field of SETTING_FIELDS) {
    if (field in row) out[field] = row[field];
  }
  // A GIF and a PNG have no audio track, so the flag is settled here rather
  // than left on for the encoder to ignore.
  if (SILENT_CONTAINERS.includes(out.container)) out.include_audio = false;
  return out;
}

// Which preset these settings ARE, or "" for none of them. Compared on the
// fields the preset states and nothing else, which makes this the exact inverse
// of `applyPreset`: change a stated field by hand and the dialog drops to
// Custom; change the background colour and it does not.
export function matchPreset(settings) {
  const s = settings || {};
  for (const row of PRESETS) {
    const same = SETTING_FIELDS.every((f) => !(f in row) || fieldsEqual(s[f], row[f]));
    if (same) return row.id;
  }
  return "";
}

function fieldsEqual(a, b) {
  if (typeof b === "number") return Number(a) === b;
  return (a || "") === b;
}

// The container we will actually encode. Anything unrecognised is "mp4" — the
// format every animatic has ever been written in, so the fallback can only ever
// produce the file the user was already expecting.
export function normaliseContainer(value) {
  const key = String(value || "").trim().toLowerCase();
  return CONTAINERS.includes(key) ? key : "mp4";
}

// The extension the downloaded file should carry.
export function containerExt(value) {
  return CONTAINER_EXT[normaliseContainer(value)];
}
