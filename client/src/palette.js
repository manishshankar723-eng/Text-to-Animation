// palette.js — THE APP'S COLOURS, CHOSEN IN THE ADMIN PANEL INSTEAD OF IN CODE.
//
// THE PROBLEM THIS SOLVES, in one sentence: the whole product was one accent
// (champagne gold) on one ground (midnight blue), and trying a different look
// meant asking a developer and waiting.
//
//     "mai chahta hun ki tum theme colour style banao brand ke under hi user
//      user mai change kar saku … only dark blue colour hai isliye mujhe change
//      kar ke dekhna hai kismai thik lagega so fir mai ohi set kar dunga kyunki
//      mai baar baar tumko nhi bol sakta ki tum ye colour karo fir ye"
//
// ⚠ **TWO COLOURS IN, EVERY TOKEN OUT — THAT IS THE WHOLE DESIGN.** A palette
// here is an ACCENT and a GROUND, nothing more, because those are the two things
// somebody looking at the screen can actually name. Every other value the app
// paints with — the page behind the panels, the panel, the raised panel, the
// borders, the body text, the muted text, the nav strokes, the six gold
// gradients, the glow, the button ink, the timeline's selection tints — is
// DERIVED from those two by the same relationships `styles/theme.css` already
// uses. Asking an administrator for thirty hex codes would be asking them to do
// the job this file exists to do.
//
// ⚠ **AND BOTH THEMES COME OUT OF THE SAME TWO COLOURS.** Light mode is not a
// second thing to pick: it is the same accent taken DOWN in lightness until it
// is legible on white, and the same hue used to tint the light greys. A panel
// that let somebody set a beautiful dark theme and silently wreck the light one
// would be worse than no panel — and half the app's users are on the other one.
//
// ⚠ **THE BUILT-IN LOOK INJECTS NOTHING AT ALL.** `DEFAULT_PALETTE` is not
// "the palette that happens to match the stylesheet" — it is the ABSENCE of an
// override, and `cssFor` returns "" for it. The values in `theme.css` were
// hand-tuned over a lot of live tests (read the comments in it), and no
// derivation is going to reproduce them to the byte. So a deployment that never
// touches this screen renders through exactly the CSS it always did, and the
// derived tokens only ever exist once somebody has deliberately chosen
// something else. This is the difference between adding a feature and quietly
// restyling everybody's app.
//
// ⚠ **THIS FILE IS THE ONLY PLACE THE MATHS LIVES.** The server stores two hex
// strings and validates that they are hex; it does not know what a `--panel-2`
// is. `tests/palette_check.py` runs THIS module under node rather than
// reimplementing it in Python, and then checks the CONTRAST of what comes out —
// because the one way a colour picker ships a broken product is by letting
// somebody pick grey text on a grey panel. See RULEBOOK E103.

// ---------------------------------------------------------------------------
// Colour maths
// ---------------------------------------------------------------------------
// Small and exact on purpose: no colour library, because everything below is
// hue/lightness nudges and one WCAG contrast ratio, and a dependency for that
// would be a megabyte on the front door of a page that draws a logo.

/** "#e5c158" (or "e5c158", or "#e5c") → {r,g,b} 0-255. Null on anything else. */
export function parseHex(hex) {
  const s = String(hex || "").trim().replace(/^#/, "");
  const full = s.length === 3 ? s.replace(/./g, (c) => c + c) : s;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return {
    r: parseInt(full.slice(0, 2), 16),
    g: parseInt(full.slice(2, 4), 16),
    b: parseInt(full.slice(4, 6), 16),
  };
}

/** Is this something we can paint with? The server checks the same shape. */
export function isHex(hex) {
  return parseHex(hex) !== null;
}

/**
 * Anything paintable → the one canonical `#rrggbb`.
 *
 * ⚠ EXPANDED HERE AND NOWHERE ELSE. `#fff` and `#ffffff` are the same colour,
 * and letting both into the store means "has the palette changed?" can answer
 * yes when nothing changed — which arms a Save button over an unchanged form
 * and writes a no-op to the database. `branding.clean_hex` does the identical
 * expansion on the server for the identical reason.
 */
export function toHex6(hex) {
  const rgb = parseHex(hex);
  return rgb ? rgbToHex(rgb) : null;
}

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
const to255 = (n) => clamp(Math.round(n), 0, 255);

function rgbToHex({ r, g, b }) {
  return "#" + [r, g, b].map((v) => to255(v).toString(16).padStart(2, "0")).join("");
}

/** {r,g,b} → {h: 0-360, s: 0-100, l: 0-100}. */
export function rgbToHsl({ r, g, b }) {
  const rr = r / 255, gg = g / 255, bb = b / 255;
  const max = Math.max(rr, gg, bb), min = Math.min(rr, gg, bb);
  const l = (max + min) / 2;
  let h = 0, s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === rr) h = (gg - bb) / d + (gg < bb ? 6 : 0);
    else if (max === gg) h = (bb - rr) / d + 2;
    else h = (rr - gg) / d + 4;
    h /= 6;
  }
  return { h: h * 360, s: s * 100, l: l * 100 };
}

/** {h,s,l} → {r,g,b}. */
export function hslToRgb({ h, s, l }) {
  const hh = ((h % 360) + 360) % 360 / 360;
  const ss = clamp(s, 0, 100) / 100;
  const ll = clamp(l, 0, 100) / 100;
  if (ss === 0) {
    const v = ll * 255;
    return { r: v, g: v, b: v };
  }
  const q = ll < 0.5 ? ll * (1 + ss) : ll + ss - ll * ss;
  const p = 2 * ll - q;
  const channel = (t) => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  return {
    r: channel(hh + 1 / 3) * 255,
    g: channel(hh) * 255,
    b: channel(hh - 1 / 3) * 255,
  };
}

export function hexToHsl(hex) {
  const rgb = parseHex(hex);
  return rgb ? rgbToHsl(rgb) : null;
}

export function hslToHex(hsl) {
  return rgbToHex(hslToRgb(hsl));
}

/** The same colour with lightness/saturation nudged. `l` and `s` are absolute. */
function tone(hex, { l, s, dl = 0, ds = 0 }) {
  const base = hexToHsl(hex);
  if (!base) return hex;
  return hslToHex({
    h: base.h,
    s: clamp(s === undefined ? base.s + ds : s, 0, 100),
    l: clamp(l === undefined ? base.l + dl : l, 0, 100),
  });
}

/** "rgba(229, 193, 88, 0.35)" — the shape theme.css writes its tints in. */
function rgba(hex, alpha) {
  const c = parseHex(hex) || { r: 0, g: 0, b: 0 };
  return `rgba(${c.r}, ${c.g}, ${c.b}, ${alpha})`;
}

/** "229, 193, 88" — channels, for the `rgba(var(--accent-rgb), 0.1)` shape.
 *
 * ⚠ `rgba()` CANNOT TAKE A HEX OUT OF A VARIABLE, which is the whole reason
 * this exists. 69 rules across 20 stylesheets spend the accent at twenty
 * different alphas — a tinted row, a glow, a selected clip — and before
 * `--accent-rgb` every one of them was the literal gold and would have stayed
 * gold on a deployment that chose green. */
function channels(hex) {
  const c = parseHex(hex) || { r: 0, g: 0, b: 0 };
  return `${c.r}, ${c.g}, ${c.b}`;
}

/** WCAG relative luminance, 0-1. */
export function luminance(hex) {
  const c = parseHex(hex);
  if (!c) return 0;
  const f = (v) => {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
}

/** WCAG contrast ratio between two colours, 1-21. */
export function contrast(a, b) {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/**
 * Walk `hex` toward black or white until it clears `want` against `on`.
 *
 * ⚠ THIS IS WHAT KEEPS A BAD PICK FROM SHIPPING AN UNREADABLE APP. A yellow
 * accent is lovely on a dark panel and invisible as link text on white, and the
 * light theme in `theme.css` already solves that by hand — its `--primary` is
 * #8a6b17, a gold darkened until it is legible, not the dark theme's #e5c158.
 * This is that same move, done arithmetically for whatever colour was chosen.
 * The hue is never touched: the answer has to still look like the colour the
 * administrator picked.
 */
export function readable(hex, on, want, { toward = "auto" } = {}) {
  if (contrast(hex, on) >= want) return hex;
  const base = hexToHsl(hex);
  if (!base) return hex;

  // ⚠ BOTH DIRECTIONS ARE TRIED, AND PICKING ONE BY A LUMINANCE THRESHOLD WAS
  // A REAL BUG THIS FILE SHIPPED FOR AN HOUR. "The ground is darkish, so go
  // lighter" is right almost everywhere and exactly wrong at the boundary: a
  // #a0a0a0 ground sits just under the threshold, the accent was walked UP into
  // the panel it was supposed to stand out from, and the answer came back at
  // 2.6:1 having tried its hardest. Whichever way actually gets there wins, and
  // when neither does, the one that got FURTHER wins.
  const walk = (dir) => {
    let best = hex;
    for (let step = 1; step <= 100; step += 1) {
      const candidate = hslToHex({
        h: base.h, s: base.s, l: clamp(base.l + dir * step, 0, 100),
      });
      best = candidate;
      if (contrast(candidate, on) >= want) return { hex: candidate, hit: true };
    }
    return { hex: best, hit: false };   // pure black or white; nothing more to give
  };

  // `toward` is still honoured when it is stated, because two of the callers
  // know something the measurement does not: a light-mode FILL has to darken
  // even when lightening would technically score better, or the button turns
  // pale and stops reading as a button.
  if (toward !== "auto") return walk(toward === "dark" ? -1 : 1).hex;

  const down = walk(-1);
  const up = walk(1);
  if (down.hit && up.hit) {
    // Both work: stay nearer the colour that was chosen.
    return Math.abs(hexToHsl(down.hex).l - base.l) <= Math.abs(hexToHsl(up.hex).l - base.l)
      ? down.hex : up.hex;
  }
  if (down.hit) return down.hex;
  if (up.hit) return up.hex;
  return contrast(down.hex, on) >= contrast(up.hex, on) ? down.hex : up.hex;
}

/** Whichever of the two inks reads better on `fill` — the button-label rule. */
function inkFor(fill, darkInk) {
  return contrast(fill, "#ffffff") >= contrast(fill, darkInk) ? "#ffffff" : darkInk;
}

// ---------------------------------------------------------------------------
// The presets
// ---------------------------------------------------------------------------
// ⚠ EIGHT, NOT THIRTY. This is a menu somebody scans in one look and then goes
// back to work; a wall of swatches is the same "you decide" problem as an empty
// colour picker. Each one is a real pairing — an accent that is legible on its
// own ground in dark mode AND survives being darkened for light mode, which
// `tests/palette_check.py` asserts for every entry rather than trusting the eye
// that picked them.
//
// ⚠ `gold` IS THE BUILT-IN AND ITS HEXES ARE COPIES OF `theme.css`, FOR THE
// SWATCH ONLY. Choosing it injects NOTHING (see `cssFor`) — it is the app as it
// ships, not a derivation that happens to look similar. Same copy-not-reference
// arrangement, and the same warning, as `.admin-brand-slot`'s hard-coded
// grounds: if those two tokens in `theme.css` are ever retuned, retune these.
export const PRESETS = Object.freeze([
  {
    id: "gold",
    label: "Champagne Gold",
    blurb: "The built-in look — warm gold on midnight blue.",
    accent: "#e5c158",
    ground: "#13161f",
    builtIn: true,
  },
  {
    id: "azure",
    label: "Electric Azure",
    blurb: "A bright blue on cool slate. The most neutral of the eight.",
    accent: "#4c9aff",
    ground: "#12161d",
  },
  {
    id: "violet",
    label: "Amethyst",
    blurb: "Purple on deep indigo. Reads as creative software.",
    accent: "#a78bfa",
    ground: "#15131f",
  },
  {
    id: "emerald",
    label: "Emerald",
    blurb: "Green on a near-black forest grey. Calm, low contrast.",
    accent: "#34d399",
    ground: "#101815",
  },
  {
    id: "coral",
    label: "Coral",
    blurb: "Warm orange-red on a warm charcoal. The loudest one here.",
    accent: "#fb7185",
    ground: "#1a1416",
  },
  {
    id: "teal",
    label: "Deep Sea",
    blurb: "Cyan on a blue-green ground. Cold and technical.",
    accent: "#2dd4bf",
    ground: "#0f1a1d",
  },
  {
    id: "amber",
    label: "Amber",
    blurb: "Orange on a warm brown-grey. Closest to the built-in gold.",
    accent: "#fbbf24",
    ground: "#181410",
  },
  {
    id: "slate",
    label: "Graphite",
    blurb: "No hue at all — a silver accent on neutral grey.",
    accent: "#cbd5e1",
    ground: "#15171a",
  },
]);

export const DEFAULT_PALETTE = Object.freeze({
  id: "gold",
  accent: PRESETS[0].accent,
  ground: PRESETS[0].ground,
});

/** The preset with this id, or null. `"custom"` is not a preset. */
export function presetById(id) {
  return PRESETS.find((p) => p.id === id) || null;
}

/**
 * Anything at all → a palette this file can paint with.
 *
 * ⚠ NEVER THROWS AND NEVER RETURNS A PARTIAL ONE. It is fed the branding
 * payload, `localStorage`, and whatever an administrator has half-typed into a
 * colour field, and every one of those can be nonsense. A bad value falls back
 * to the built-in, because the alternative is an app with no colours.
 */
export function normalisePalette(raw) {
  const id = String(raw?.id || raw?.theme_id || "").trim().toLowerCase();
  const preset = presetById(id);
  const accent = toHex6(raw?.accent);
  const ground = toHex6(raw?.ground);

  // ⚠ NO COLOURS AND NO PRESET WE KNOW MEANS "NOTHING WAS CHOSEN", AND THAT IS
  // THE BUILT-IN — never "custom". An id from an older release, a typo in the
  // store, an empty document: every one of those must land on the shipped look,
  // because "custom" with the default hexes is a palette that INJECTS CSS
  // (see `isBuiltIn`) and would restyle an app nobody asked to restyle.
  if (!accent && !ground) {
    const p = preset || presetById(DEFAULT_PALETTE.id);
    return Object.freeze({ id: p.id, accent: p.accent, ground: p.ground });
  }
  const finalAccent = accent || (preset ? preset.accent : DEFAULT_PALETTE.accent);
  const finalGround = ground || (preset ? preset.ground : DEFAULT_PALETTE.ground);

  // ⚠ THE NAME FOLLOWS THE COLOURS, NOT THE OTHER WAY ROUND. A palette that
  // still says "emerald" while carrying an orange accent is how the panel ends
  // up ringing the Emerald card over an orange screen — and how a store row
  // ends up claiming a preset it is not. The id is only kept when the colours
  // ARE that preset's.
  const matches = preset
    && preset.accent === finalAccent
    && preset.ground === finalGround;

  return Object.freeze({
    id: matches ? preset.id : "custom",
    accent: finalAccent,
    ground: finalGround,
  });
}

/** Is this the app exactly as it ships? Then nothing is injected. */
export function isBuiltIn(palette) {
  const p = normalisePalette(palette);
  return p.id === "gold"
    && p.accent === DEFAULT_PALETTE.accent
    && p.ground === DEFAULT_PALETTE.ground;
}

// ---------------------------------------------------------------------------
// The derivation
// ---------------------------------------------------------------------------
// ⚠ EVERY NUMBER BELOW IS READ OFF `theme.css`, NOT INVENTED. The shipped dark
// theme is bg 6% / panel 10% / panel-2 14% / border 20% lightness at ~225°, and
// its light theme is panel 100% / bg 96% / panel-2 93% / border 85%. Keeping
// those exact steps is what makes a new accent look like THIS app in a new
// colour rather than like a different app: the depth of the panels, the weight
// of the borders and the distance between the text and the ground all stay put.
//
// ⚠ AND THE CONTENT COLOURS ARE NOT IN HERE ON PURPOSE. The timeline's video
// orange, image pink, Veo purple, caption green and shape violet mean WHAT A
// CLIP HOLDS — asked for in those words ("i want keep color of content so user
// understand easily color byies content"), and pinned by the notes in
// `theme.css`. Re-hueing them with the accent would trade a real, learned
// signal for decoration. Only the SELECTION tints (`--tl-clip-*`), which are
// the accent by definition, follow the palette.

const DARK = { bg: -4, panel2: 4, border: 10 };
const LIGHT = { bg: -4, panel2: -7, border: -15 };

/**
 * Which way "deeper" is, for a ground that may not be dark at all.
 *
 * ⚠ THE DARK THEME'S STEPS GO UP IN LIGHTNESS, AND THAT BREAKS IF SOMEBODY
 * PUTS WHITE IN THE GROUND BOX. `panel + 10%` on a 99% ground clamps to 100 and
 * the borders vanish — a whole app of edgeless cards, from one typed colour.
 * Nothing stops that being typed, so the steps turn round instead. The panel
 * still warns about the pick; this is what stops it being unusable meanwhile.
 */
function stepSign(lightness) {
  return lightness > 70 ? -1 : 1;
}

function darkTokens(accent, ground) {
  const g = hexToHsl(ground) || hexToHsl(DEFAULT_PALETTE.ground);
  const panel = hslToHex(g);
  const at = hexToHsl(accent) || hexToHsl(DEFAULT_PALETTE.accent);
  const sign = stepSign(g.l);

  // The ink that sits ON a filled accent surface: the accent's own hue taken
  // almost to black, which is what `--primary-ink: #141005` is under the gold.
  const darkInk = hslToHex({ h: at.h, s: clamp(at.s * 0.6, 0, 70), l: 5 });
  const fillInk = inkFor(accent, darkInk);

  // ⚠ THE ACCENT IS ALSO TEXT (links, the running state, a selected row's
  // label), so it has to clear 4.5:1 on the panel it sits on. A deep accent
  // that looks fine as a button fill is unreadable as a word.
  //
  // ⚠ AND THE DIRECTION IS `auto`, NOT "lighter". Lighter is right on a dark
  // ground and is precisely the wrong way on a pale one — `readable` picks by
  // measuring the panel, which is the only thing that knows.
  const primary = readable(accent, panel, 4.5);

  // ⚠ THE TEXT IS CORRECTED AGAINST THE GROUND IT LANDS ON, NOT ASSUMED. A
  // 96%-lightness body text is right on a midnight panel and INVISIBLE on a
  // near-white one, and the colour box will accept a near-white one. Everything
  // that carries a sentence goes through `readable`, which keeps the hue and
  // moves only the lightness — so it still looks like the chosen theme.
  const text = readable(hslToHex({ h: g.h, s: clamp(g.s * 1.3, 8, 40), l: 96 }), panel, 4.5);
  const muted = readable(hslToHex({ h: g.h, s: clamp(g.s * 0.6, 6, 24), l: 66 }), panel, 4.5);

  return {
    "--bg": tone(panel, { dl: sign * DARK.bg }),
    "--panel": panel,
    "--panel-2": tone(panel, { dl: sign * DARK.panel2 }),
    "--border": tone(panel, { dl: sign * DARK.border }),
    "--nav-stroke": rgba(hslToHex({ h: g.h, s: 100, l: 86 }), 0.26),
    "--nav-stroke-hover": rgba(hslToHex({ h: g.h, s: 100, l: 89 }), 0.55),
    "--border-gold": rgba(accent, 0.35),
    "--text": text,
    "--muted": muted,
    "--primary": primary,
    "--primary-hover": tone(primary, { dl: 9 }),
    "--primary-ink": darkInk,
    "--running": primary,
    "--gold-glow": `0 4px 20px ${rgba(accent, 0.22)}`,
    "--gold-grad": `linear-gradient(135deg, ${accent} 0%, ${tone(accent, { dl: -8 })} 100%)`,
    "--gold-grad-hover":
      `linear-gradient(135deg, ${tone(accent, { dl: 9 })} 0%, ${tone(accent, { dl: 1 })} 100%)`,
    "--gold-grad-rich":
      `linear-gradient(135deg, ${tone(accent, { dl: 9 })} 0%, ${accent} 45%, ${tone(accent, { dl: -8 })} 100%)`,
    "--gold-grad-rich-hover":
      `linear-gradient(135deg, ${tone(accent, { dl: 13 })} 0%, ${tone(accent, { dl: 6 })} 45%, ${tone(accent, { dl: 1 })} 100%)`,
    "--gold-bar":
      `linear-gradient(90deg, ${tone(accent, { dl: -8 })} 0%, ${accent} 50%, ${tone(accent, { dl: 9 })} 100%)`,
    "--gold-fill": accent,
    "--gold-ink": fillInk,
    "--tl-clip-bg": rgba(accent, 0.13),
    "--tl-clip-bg-alt": rgba(accent, 0.2),
    "--tl-clip-seam": rgba(accent, 0.34),
    // ⚠ THESE THREE ARE EMITTED IN THE `:root` BLOCK ONLY, AND SO THEY REACH
    // BOTH THEMES — which is exactly how theme.css defines them (once, not per
    // theme). The washes they feed were the DARK gold in light mode too before
    // any of this existed; splitting them per theme here would change the
    // shipped light mode, which is the one thing this file must never do.
    "--accent-rgb": channels(accent),
    "--accent-deep-rgb": channels(onWhite(accent)),
    "--accent-on-white": onWhite(accent),
  };
}

/**
 * The accent, darkened until it holds up on WHITE.
 *
 * A few surfaces are white whatever the theme is — a landing-page tile, the
 * Explore billboards — and the bright accent on those is a smear. This is the
 * same 3:1 large-text move `lightTokens` makes for its fill, pulled out because
 * both need it and one of them needs it in the OTHER theme's block.
 */
function onWhite(accent) {
  return readable(accent, "#ffffff", 3.0, { toward: "dark" });
}

function lightTokens(accent, ground) {
  const g = hexToHsl(ground) || hexToHsl(DEFAULT_PALETTE.ground);
  const at = hexToHsl(accent) || hexToHsl(DEFAULT_PALETTE.accent);

  // ⚠ WHITE, TINTED BY THE CHOSEN HUE — NOT THE DARK GROUND LIGHTENED. A light
  // theme built by inverting the dark one gives a pastel page, and this app's
  // light mode is a white one (`--panel: #ffffff`). The hue survives in the
  // greys around it, which is what makes light mode still look like the theme
  // that was chosen.
  const panel = hslToHex({ h: g.h, s: clamp(g.s * 0.35, 0, 30), l: 100 });
  const darkInk = hslToHex({ h: at.h, s: clamp(at.s * 0.6, 0, 70), l: 5 });

  // The accent as TEXT on the light panel: darkened until it clears 4.5:1.
  // This is the move `theme.css` makes by hand (#e5c158 → #8a6b17).
  const primary = readable(accent, panel, 4.5);
  // The accent as a FILL under a short bold label: 3:1 is the WCAG large-text
  // bar, and `theme.css` says in as many words that these surfaces carry only
  // short bold labels for exactly this reason.
  const fill = readable(accent, "#ffffff", 3.0, { toward: "dark" });

  return {
    "--bg": hslToHex({ h: g.h, s: clamp(g.s * 0.45, 0, 30), l: 100 + LIGHT.bg }),
    "--panel": panel,
    "--panel-2": hslToHex({ h: g.h, s: clamp(g.s * 0.45, 0, 30), l: 100 + LIGHT.panel2 }),
    "--border": hslToHex({ h: g.h, s: clamp(g.s * 0.4, 0, 25), l: 100 + LIGHT.border }),
    "--nav-stroke": rgba(hslToHex({ h: g.h, s: 35, l: 43 }), 0.28),
    "--nav-stroke-hover": rgba(hslToHex({ h: g.h, s: 41, l: 37 }), 0.55),
    "--border-gold": rgba(primary, 0.4),
    // Corrected against the panel for the same reason as the dark set above.
    "--text": readable(hslToHex({ h: g.h, s: clamp(g.s * 1.1, 8, 30), l: 11 }), panel, 4.5),
    "--muted": readable(hslToHex({ h: g.h, s: clamp(g.s * 0.55, 6, 20), l: 41 }), panel, 4.5),
    "--primary": primary,
    "--primary-hover": tone(primary, { dl: -7 }),
    "--primary-ink": darkInk,
    "--running": tone(primary, { dl: 7 }),
    "--gold-glow": `0 4px 18px ${rgba(fill, 0.26)}`,
    "--gold-grad": `linear-gradient(135deg, ${fill} 0%, ${tone(fill, { dl: -6 })} 100%)`,
    "--gold-grad-hover":
      `linear-gradient(135deg, ${tone(fill, { dl: -6 })} 0%, ${tone(fill, { dl: -11 })} 100%)`,
    "--gold-grad-rich":
      `linear-gradient(135deg, ${tone(fill, { dl: 3 })} 0%, ${fill} 45%, ${tone(fill, { dl: -6 })} 100%)`,
    "--gold-grad-rich-hover":
      `linear-gradient(135deg, ${tone(fill, { dl: -3 })} 0%, ${tone(fill, { dl: -6 })} 45%, ${tone(fill, { dl: -12 })} 100%)`,
    "--gold-bar":
      `linear-gradient(90deg, ${tone(fill, { dl: -6 })} 0%, ${fill} 50%, ${tone(fill, { dl: 6 })} 100%)`,
    "--gold-fill": fill,
    "--gold-ink": inkFor(fill, darkInk),
    "--tl-clip-bg": rgba(primary, 0.1),
    "--tl-clip-bg-alt": rgba(primary, 0.17),
    "--tl-clip-seam": rgba(primary, 0.34),
  };
}

/** Both themes' tokens for one palette. `{dark: {…}, light: {…}}`. */
export function derive(palette) {
  const p = normalisePalette(palette);
  return { dark: darkTokens(p.accent, p.ground), light: lightTokens(p.accent, p.ground) };
}

function block(selector, tokens) {
  const body = Object.entries(tokens)
    .map(([k, v]) => `  ${k}: ${v};`)
    .join("\n");
  return `${selector} {\n${body}\n}`;
}

/**
 * The stylesheet for one palette — "" for the built-in look.
 *
 * ⚠ BOTH BLOCKS, AND THE LIGHT ONE MUST CARRY THE ATTRIBUTE SELECTOR. A plain
 * `:root` cannot override `theme.css`'s `:root[data-theme="light"]` — that is a
 * specificity loss, not a cascade order one — so a single block would recolour
 * dark mode and leave light mode gold. The two selectors here are character for
 * character the two in `theme.css`; they win only on being later in the
 * document, which they are because this is appended to `<head>`.
 */
export function cssFor(palette) {
  const p = normalisePalette(palette);
  if (isBuiltIn(p)) return "";
  const t = derive(p);
  return [
    `/* Palette "${p.id}" — accent ${p.accent}, ground ${p.ground}. Written by`,
    "   client/src/palette.js from the Brand screen. Do not hand-edit. */",
    block(":root", t.dark),
    block(':root[data-theme="light"]', t.light),
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Putting it on the page
// ---------------------------------------------------------------------------
const STYLE_ID = "cas-palette";

/**
 * Paint the app in this palette. Idempotent; safe before React mounts.
 *
 * ⚠ ONE `<style>` ELEMENT, REPLACED IN PLACE, and never a second one. The admin
 * panel calls this on every keystroke of the colour picker so the change is
 * visible while it is being chosen — appending instead of replacing would leave
 * a hundred dead blocks in `<head>`, each still winning over the one before it.
 */
export function applyPalette(palette) {
  if (typeof document === "undefined") return "";
  const css = cssFor(palette);
  let el = document.getElementById(STYLE_ID);
  if (!css) {
    if (el) el.remove();                    // back to the built-in stylesheet
    return "";
  }
  if (!el) {
    el = document.createElement("style");
    el.id = STYLE_ID;
    document.head.appendChild(el);
  }
  if (el.textContent !== css) el.textContent = css;
  return css;
}
