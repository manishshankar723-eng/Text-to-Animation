/**
 * Caption LOOKS — a shelf of ready-made type, and a place to keep your own.
 *
 * ⚠ A STYLE IS A BAG OF ORDINARY CAPTION FIELDS, AND NOTHING ELSE. Exactly the
 * same bargain `text_presets.js` makes about keyframes: a style writes the
 * fields the clip already has — font, size, colour, backdrop, outline, shadow,
 * spacing — and then gets out of the way. Nothing is stored saying which style
 * ran, neither renderer has heard of any of them, and there is no second
 * evaluator anywhere that knows what "Hormozi" means.
 *
 * That is what makes it free and what makes it trustworthy:
 *   · it exports correctly the day it is written, because every field in
 *     `STYLE_FIELDS` is one `draw_texts` and `captionStyle` already draw;
 *   · every one of them stays editable in the inspector afterwards;
 *   · undo treats applying a style as one ordinary document edit;
 *   · and the SERVER needs no vocabulary of styles at all — the browser resolves
 *     a style to plain fields and the captions run stamps them onto each line
 *     (`caption_clips(..., style=…)` in `captions.py`, which already took a
 *     `style` dict and was never given one).
 *
 * The cost is the same honest one: a style is WRITE-ONLY. Nothing records which
 * one was applied, so no picker can show a "current" style — after you have
 * nudged one field there isn't one.
 *
 * ---------------------------------------------------------------------------
 * ⚠ A STYLE IS A LOOK. IT NEVER MOVES A CAPTION AND NEVER TOUCHES ITS ANIMATION.
 * ---------------------------------------------------------------------------
 * `position`, `place`, `x`, `y`, `scale`, `rotation`, `opacity` and `keyframes`
 * are all deliberately OUTSIDE `STYLE_FIELDS`, and the dominant use case is why:
 * restyling a whole run of generated subtitles must change how they look and
 * leave every one of them exactly where it is. A style that also moved things
 * would fight the animation presets — which switch a clip to free placement on
 * purpose — and would undo somebody's hand-placed title the moment they tried a
 * different colour. Timing (`start_ms`, `duration_ms`) and identity (`id`,
 * `layer_id`, `group_id`, `text`) are outside it for the same reason, only more
 * so.
 *
 * ---------------------------------------------------------------------------
 * ⚠ APPLYING A STYLE WRITES *EVERY* FIELD IN THE LIST, INCLUDING THE DEFAULTS.
 * ---------------------------------------------------------------------------
 * A style that only wrote what it cared about would leave the LAST style's
 * leftovers underneath: switch from a 96px yellow title to a plain subtitle and
 * the subtitle would still be 96px, because the new style never mentioned
 * `size_px`. Same rule as `OWNED` in `text_presets.js`, and the same failure if
 * it is dropped — a picker where choosing something changes only some of it.
 *
 * ---------------------------------------------------------------------------
 * ⚠ AND THE FONT IS RESOLVED AGAINST THE CAPTION'S OWN WORDS.
 * ---------------------------------------------------------------------------
 * Anton has no Devanagari. Applying a style that names it to a Hindi subtitle
 * would burn ▯▯▯ into the MP4 — the exact failure `animatic_fonts.py` and
 * RULEBOOK E145/E146 exist to prevent — so every style's font goes through
 * `bestFontForText`, which keeps the style's choice whenever it can draw the
 * text and otherwise picks a face that can. A Latin film gets the style it
 * asked for; a Hindi one gets the nearest face that is not empty boxes.
 */

import { bestFontForText } from "./fonts.js";

/**
 * Every field a style owns, with the value a caption falls back to.
 *
 * ⚠ THE FALLBACKS ARE `AnimaticTextClip`'s OWN DEFAULTS, and they have to stay
 * that way: a style writes all of these, so a field whose fallback here differs
 * from the schema's would silently restyle every caption it touched. `null` is
 * `backdrop_opacity`'s real default — "whatever the backdrop kind is worth" —
 * and is not a number on purpose (0.55 here would make every solid box 55% the
 * moment any style was applied).
 */
export const STYLE_FIELDS = {
  font: "inter",
  size: "medium",
  size_px: 0,
  align: "center",
  color: "#ffffff",
  backdrop: "scrim",
  backdrop_color: "#000000",
  backdrop_opacity: null,
  backdrop_radius: 0.25,
  backdrop_pad: 1,
  stroke_px: 0,
  stroke_color: "#000000",
  shadow: 0,
  shadow_color: "#000000",
  shadow_opacity: 0.55,
  shadow_angle: 45,
  letter_spacing: 0,
  line_height: 1.28,
  text_case: "none",
  wrap: 0.86,
};

export const STYLE_FIELD_NAMES = Object.keys(STYLE_FIELDS);

/**
 * The shelves the picker draws, in order. A VIEW, not the truth — a style whose
 * `category` names no shelf here still appears, under "Other".
 */
export const TEXT_STYLE_CATEGORIES = [
  { id: "subtitle", label: "Subtitles", note: "Made to be read. Safe under a whole film." },
  { id: "shorts", label: "Shorts & reels", note: "Loud, high-contrast, built for a phone." },
  { id: "title", label: "Titles", note: "One line that is the point of the shot." },
  { id: "plain", label: "Quiet", note: "Little or no furniture behind the words." },
  { id: "mine", label: "Saved on this device", note: "Looks you saved yourself." },
];

/**
 * The built-in looks.
 *
 * ⚠ `subtitle` MUST REPRODUCE WHAT A GENERATED CAPTION ALREADY IS — white, small,
 * on a scrim, in Inter — because it is the "put it back" of this whole shelf and
 * because it is what `caption_clips` writes when no style is given. It is also
 * why it is first. ⚠ `fields` is a PARTIAL: everything it does not name comes
 * from `STYLE_FIELDS` above, so a style is a short readable list of what makes
 * it different rather than twenty lines of mostly-defaults.
 */
export const TEXT_STYLES = [
  // --- Subtitles ----------------------------------------------------------
  {
    id: "subtitle",
    label: "Clean subtitle",
    category: "subtitle",
    hint: "White on a soft bar. What captions are written as, and the way back.",
    // ⚠ `size` IS NAMED HERE EVEN THOUGH EVERY OTHER FIELD IS A DEFAULT, and the
    // test that caught it is the reason: `STYLE_FIELDS` falls back to the
    // SCHEMA's default ("medium", what a caption you type by hand gets), while
    // `caption_clips` writes "small", because a subtitle under a whole film is
    // not the size of a title you dropped on one shot. Leaving it out made this
    // style — the way back from every other look in the shelf — quietly one size
    // larger than the captions it claims to restore.
    fields: { size: "small" },
  },
  {
    id: "subtitle-outline",
    label: "Outlined",
    category: "subtitle",
    hint: "No bar at all — a heavy black outline keeps it readable over anything.",
    fields: {
      backdrop: "plain",
      stroke_px: 5,
      font: "montserrat",
      size: "small",
    },
  },
  {
    id: "subtitle-box",
    label: "Solid box",
    category: "subtitle",
    hint: "White on a black block. The most readable thing here, over any picture.",
    fields: {
      backdrop: "box",
      backdrop_opacity: 0.92,
      backdrop_radius: 0.12,
      backdrop_pad: 0.9,
      size: "small",
    },
  },
  {
    id: "subtitle-wide",
    label: "Roomy",
    category: "subtitle",
    hint: "Open line spacing and a narrow measure. Long lines, comfortably read.",
    fields: {
      backdrop: "scrim",
      backdrop_opacity: 0.45,
      line_height: 1.5,
      wrap: 0.62,
      letter_spacing: 0.01,
      font: "noto-sans",
    },
  },
  {
    id: "subtitle-broadcast",
    label: "Broadcast",
    category: "subtitle",
    hint: "The television one: plain white, hard outline, no colour anywhere.",
    fields: {
      backdrop: "plain",
      stroke_px: 4,
      shadow: 0.05,
      shadow_opacity: 0.7,
      size: "small",
      font: "inter",
    },
  },

  // --- Shorts & reels -----------------------------------------------------
  {
    id: "punch-yellow",
    label: "Punch yellow",
    category: "shorts",
    hint: "Big yellow caps with a thick black outline. The one every reel uses.",
    fields: {
      font: "montserrat",
      size_px: 86,
      color: "#ffe14d",
      backdrop: "plain",
      stroke_px: 12,
      text_case: "upper",
      line_height: 1.1,
      wrap: 0.8,
      shadow: 0.05,
      shadow_opacity: 0.5,
    },
  },
  {
    id: "punch-white",
    label: "Punch white",
    category: "shorts",
    hint: "The same weight in white. Reads on any picture, shouts a little less.",
    fields: {
      font: "montserrat",
      size_px: 86,
      color: "#ffffff",
      backdrop: "plain",
      stroke_px: 12,
      text_case: "upper",
      line_height: 1.1,
      wrap: 0.8,
      shadow: 0.05,
      shadow_opacity: 0.5,
    },
  },
  {
    id: "punch-green",
    label: "Punch green",
    category: "shorts",
    hint: "Bright green on black. Loud, and unmistakably a highlight.",
    fields: {
      font: "poppins",
      size_px: 80,
      color: "#63f27a",
      backdrop: "plain",
      stroke_px: 11,
      text_case: "upper",
      line_height: 1.12,
      wrap: 0.8,
    },
  },
  {
    id: "highlight-box",
    label: "Highlight box",
    category: "shorts",
    hint: "Dark words inside a bright block. A word you want somebody to stop on.",
    fields: {
      font: "poppins",
      size_px: 72,
      color: "#101010",
      backdrop: "box",
      backdrop_color: "#ffd93d",
      backdrop_opacity: 1,
      backdrop_radius: 0.18,
      backdrop_pad: 0.7,
      text_case: "upper",
      line_height: 1.14,
      wrap: 0.72,
    },
  },
  {
    id: "sticker",
    label: "Sticker",
    category: "shorts",
    hint: "Dark type on a white rounded pill. Looks stuck onto the picture.",
    fields: {
      font: "nunito",
      size_px: 64,
      color: "#15161a",
      backdrop: "box",
      backdrop_color: "#ffffff",
      backdrop_opacity: 1,
      backdrop_radius: 0.6,
      backdrop_pad: 1.1,
      line_height: 1.2,
      wrap: 0.7,
    },
  },
  {
    id: "neon",
    label: "Neon",
    category: "shorts",
    hint: "Cyan on near-black, with a glow thrown straight down.",
    fields: {
      font: "poppins",
      size_px: 76,
      color: "#4ff0ff",
      backdrop: "box",
      backdrop_color: "#05060a",
      backdrop_opacity: 0.85,
      backdrop_radius: 0.3,
      backdrop_pad: 0.8,
      // ⚠ THE SHADOW IS THE GLOW, AND IT IS HARD-EDGED. Pillow draws no blur, so
      // the browser must not either (`captionStyle` sets a blur of 0 on purpose)
      // — a soft glow here would be a preview that lies. A same-colour offset at
      // low alpha is the closest thing that is the SAME picture on both sides.
      shadow: 0.05,
      shadow_color: "#4ff0ff",
      shadow_opacity: 0.45,
      shadow_angle: 90,
      text_case: "upper",
      letter_spacing: 0.04,
      line_height: 1.18,
      wrap: 0.76,
    },
  },
  {
    id: "hard-shadow",
    label: "Hard shadow",
    category: "shorts",
    hint: "White caps with a solid coloured shadow behind them. Very 90s, very readable.",
    fields: {
      font: "archivo",
      size_px: 84,
      color: "#ffffff",
      backdrop: "plain",
      stroke_px: 3,
      shadow: 0.09,
      shadow_color: "#ff2d6f",
      shadow_opacity: 1,
      shadow_angle: 45,
      text_case: "upper",
      line_height: 1.08,
      wrap: 0.78,
    },
  },
  {
    id: "comic",
    label: "Comic",
    category: "shorts",
    hint: "A comic face with a fat outline. For a joke, a reaction, a sound effect.",
    fields: {
      font: "bangers",
      size_px: 96,
      color: "#fff3c4",
      backdrop: "plain",
      stroke_px: 10,
      stroke_color: "#1b1b1b",
      text_case: "upper",
      letter_spacing: 0.03,
      line_height: 1.05,
      wrap: 0.8,
    },
  },
  {
    id: "handwritten",
    label: "Handwritten",
    category: "shorts",
    hint: "A written hand, outlined so it stays legible. Personal, informal.",
    fields: {
      font: "caveat",
      size_px: 90,
      color: "#ffffff",
      backdrop: "plain",
      stroke_px: 7,
      line_height: 1.15,
      wrap: 0.74,
    },
  },

  // --- Titles -------------------------------------------------------------
  {
    id: "title-condensed",
    label: "Big condensed",
    category: "title",
    hint: "Tall narrow caps, nothing behind them. A title card, or an opener.",
    fields: {
      font: "anton",
      size_px: 132,
      backdrop: "plain",
      stroke_px: 0,
      shadow: 0.05,
      shadow_opacity: 0.45,
      text_case: "upper",
      letter_spacing: 0.01,
      line_height: 1.02,
      wrap: 0.82,
    },
  },
  {
    id: "title-serif",
    label: "Serif title",
    category: "title",
    hint: "A quiet serif, well spaced. Documentary, wedding, anything unhurried.",
    fields: {
      font: "playfair",
      size_px: 112,
      backdrop: "plain",
      shadow: 0.04,
      shadow_opacity: 0.4,
      line_height: 1.15,
      wrap: 0.72,
    },
  },
  {
    id: "kicker",
    label: "Kicker",
    category: "title",
    hint: "Small, wide-spaced caps. The line that sits above the real title.",
    fields: {
      font: "oswald",
      size_px: 34,
      backdrop: "plain",
      stroke_px: 0,
      shadow: 0.06,
      shadow_opacity: 0.5,
      text_case: "upper",
      letter_spacing: 0.32,
      line_height: 1.3,
      wrap: 0.7,
    },
  },
  {
    id: "news-bar",
    label: "News bar",
    category: "title",
    hint: "A solid coloured block, left aligned and tight. A name, a place, a fact.",
    fields: {
      font: "archivo",
      size_px: 46,
      align: "left",
      color: "#ffffff",
      backdrop: "box",
      backdrop_color: "#c81e3a",
      backdrop_opacity: 1,
      backdrop_radius: 0,
      backdrop_pad: 0.85,
      text_case: "upper",
      letter_spacing: 0.03,
      line_height: 1.2,
      wrap: 0.6,
    },
  },
  {
    id: "quote",
    label: "Quote",
    category: "title",
    hint: "Serif, open leading, a narrow measure. Words somebody said.",
    fields: {
      font: "merriweather",
      size_px: 62,
      backdrop: "plain",
      shadow: 0.05,
      shadow_opacity: 0.5,
      line_height: 1.6,
      wrap: 0.58,
    },
  },
  {
    id: "retro",
    label: "Retro",
    category: "title",
    hint: "Cream on a warm brown block. Old film, old paper, a memory.",
    fields: {
      font: "lobster",
      size_px: 88,
      color: "#f6e7c8",
      backdrop: "box",
      backdrop_color: "#3a2415",
      backdrop_opacity: 0.9,
      backdrop_radius: 0.2,
      backdrop_pad: 0.9,
      line_height: 1.2,
      wrap: 0.72,
    },
  },

  // --- Quiet --------------------------------------------------------------
  {
    id: "minimal",
    label: "Just the letters",
    category: "plain",
    hint: "No bar, no outline, no shadow. Only use it over art you control.",
    fields: {
      backdrop: "plain",
      stroke_px: 0,
      shadow: 0,
      font: "inter",
    },
  },
  {
    id: "mono",
    label: "Typewriter",
    category: "plain",
    hint: "A fixed-width face on a dim bar. Terminals, timestamps, log lines.",
    fields: {
      font: "courier",
      size: "small",
      backdrop: "box",
      backdrop_color: "#0b0d10",
      backdrop_opacity: 0.8,
      backdrop_radius: 0.08,
      backdrop_pad: 0.8,
      color: "#c8f5c8",
      letter_spacing: 0.02,
      line_height: 1.35,
      wrap: 0.7,
    },
  },
];

export const TEXT_STYLE_IDS = TEXT_STYLES.map((s) => s.id);

/**
 * The complete field bag a style resolves to — its own values over the defaults.
 *
 * Separate from `applyTextStyle` because the CAPTIONS RUN needs it without a
 * clip to apply it to: the browser resolves the style, sends the plain fields,
 * and `caption_clips` stamps them onto every line it writes. That is the whole
 * reason the server needs no vocabulary of styles.
 */
export function resolveTextStyle(styleId, custom = null) {
  const style =
    (custom || []).find((s) => s.id === styleId) ||
    TEXT_STYLES.find((s) => s.id === styleId);
  if (!style) return null;
  return { ...STYLE_FIELDS, ...(style.fields || {}) };
}

/**
 * Apply a style to one caption. Returns a PATCH for `onChange(id, patch)`.
 *
 * ⚠ THE FONT IS THE ONE FIELD THAT IS NOT COPIED STRAIGHT ACROSS. See the header:
 * a style naming Anton, applied to a Hindi subtitle, is ▯▯▯ burnt into the MP4.
 * `bestFontForText` keeps the style's choice whenever that face can draw the
 * words and otherwise returns one that can, which is the same call
 * `caption_clips` already makes on the server for exactly this reason.
 */
export function applyTextStyle(clip, styleId, custom = null) {
  const fields = resolveTextStyle(styleId, custom);
  if (!fields) return {};
  return {
    ...fields,
    font: bestFontForText(String(clip?.text || ""), fields.font),
  };
}

/**
 * The look currently on a clip, as a style's `fields` — for "save this look".
 *
 * ⚠ EVERY FIELD, NOT JUST THE ONES THAT DIFFER. A saved style is applied the
 * same way a built-in one is, so it has to be able to overwrite whatever the
 * clip it lands on was wearing; a sparse one would let the previous style show
 * through, which is the leftovers problem in the header.
 */
export function styleFromClip(clip) {
  const fields = {};
  for (const [name, fallback] of Object.entries(STYLE_FIELDS)) {
    const value = clip?.[name];
    fields[name] = value === undefined ? fallback : value;
  }
  return fields;
}

// ---------------------------------------------------------------------------
// The styles you saved yourself
// ---------------------------------------------------------------------------
// ⚠ PER BROWSER, LIKE `media_view.js` AND `workspace.js` — and unlike those two,
// that is a LIMITATION rather than the right answer, so the shelf says so on
// screen ("Saved on this device"). A caption look is closer to a brand asset
// than to a pane layout: it belongs on the account, and moving it there is a
// store, a route and a schema, which is not a change to make in the same visit
// as the feature. Written down here so the next person knows it was a decision.
//
// ⚠ EVERY READ AND WRITE IS WRAPPED. Private mode and a disabled-storage browser
// both throw on access, and a picker that cannot open because nobody could save
// a style is worse than a picker with no saved styles in it.

const STORE_KEY = "cas_caption_styles";
const MAX_SAVED = 60;

export function listCustomStyles() {
  let raw = null;
  try {
    raw = localStorage.getItem(STORE_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Somebody else's key, or a half-written value. Not worth a message.
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  // ⚠ FILTERED ON THE WAY OUT, not trusted. This is the one input to the styles
  // shelf that did not come from this file, and a stored entry can be from an
  // older build — a field this build has dropped must not reach a clip.
  return parsed
    .filter((s) => s && typeof s.id === "string" && typeof s.label === "string")
    .map((s) => ({
      id: s.id,
      label: s.label,
      category: "mine",
      hint: s.hint || "A look you saved on this device.",
      custom: true,
      fields: Object.fromEntries(
        Object.entries(s.fields || {}).filter(([name]) => name in STYLE_FIELDS)
      ),
    }));
}

/**
 * Save a look under a name. Returns the new list, or null if storage refused.
 *
 * A name that already exists is REPLACED rather than duplicated — typing the
 * same name twice means "update that one", and two rows with one label is a
 * picker nobody can use.
 */
export function saveCustomStyle(label, fields) {
  const name = String(label || "").trim().slice(0, 40);
  if (!name) return null;
  const existing = listCustomStyles();
  const match = existing.find((s) => s.label.toLowerCase() === name.toLowerCase());
  const entry = {
    id: match ? match.id : `mine-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
    label: name,
    fields: Object.fromEntries(
      Object.entries(fields || {}).filter(([field]) => field in STYLE_FIELDS)
    ),
  };
  const next = match
    ? existing.map((s) => (s.id === match.id ? entry : { id: s.id, label: s.label, fields: s.fields }))
    : [...existing.map((s) => ({ id: s.id, label: s.label, fields: s.fields })), entry];
  return write(next.slice(-MAX_SAVED));
}

export function deleteCustomStyle(id) {
  return write(
    listCustomStyles()
      .filter((s) => s.id !== id)
      .map((s) => ({ id: s.id, label: s.label, fields: s.fields }))
  );
}

function write(list) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(list));
  } catch {
    // Quota, private mode, storage off. The caller gets null and says so once
    // rather than pretending the style was kept.
    return null;
  }
  return listCustomStyles();
}
