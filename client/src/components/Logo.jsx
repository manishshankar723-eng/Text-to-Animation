// Logo.jsx — the Aniwala mark, as inline SVG.
//
// ⚠ THIS REPLACES AN EMOJI (🎭), AND THE EMOJI WAS WRONG TWICE OVER. It was the
// theatre-masks glyph, picked back when the whole product was "Character Asset
// Studio" — a name for the ONE workflow that is now switched off — and it is an
// emoji, which every operating system draws in its own style. The parent brand
// is a game-art studio; a Windows emoji sitting next to a 3D portfolio reads as
// a placeholder. Same reasoning as `Icon.jsx`, one level up.
//
// THE MARK IS THE COMPANY'S OWN LOGO, REDUCED TO GLYPH SIZE. Three parts, and
// each one is doing a job:
//
//   1. The **A-frame** — Aniwala's initial, drawn as a solid chevron. At 20px
//      the counter (the hole in a normal "A") fills in and turns to mud, so the
//      crossbar is left out entirely rather than drawn and lost.
//   2. The **film ribbon** sweeping through it — the studio half of the story.
//      Its sprocket holes are PUNCHED with a mask, not painted in the page
//      colour: the mark sits on `--panel` in the rail, on `--bg` on the landing
//      page and on white in a favicon, and holes painted one colour would show
//      up as a smear on the other two.
//   3. The **sparkles** — the AI half, and the reason the existing logo needed
//      almost no reinvention: a four-point sparkle is already the symbol the
//      whole industry uses for "a machine made this". The company logo has one.
//      This mark has TWO — one large, one small — which is the specific pairing
//      that reads as *AI* rather than as *shiny*, and it is the only thing that
//      separates the product mark from the studio's own.
//
// ⚠ THE SPARKLES ARE GOLD, THE REST TAKES `currentColor`. Gold is the app's one
// accent (see theme.css), and putting it on exactly the AI element is what makes
// the mark say "Aniwala AI Studio" instead of just "Aniwala". Everything else
// inherits, so the mark turns dark on the light theme and light on the dark one
// without a second copy of it existing anywhere.
//
// ⚠ SIZED IN `em`, LIKE `Icon.jsx`. The mark is 1em square and follows the
// font-size of whatever it sits in — which is why the sidebar's collapsed rail
// grows it (`.sb-logo` goes 1.3rem → 1.5rem) with no rule of its own here.
import { useId } from "react";

// The A, the ribbon and its centreline, in a 64×64 box.
//
// ⚠ THE CENTRELINE IS NOT DRAWN — it exists only to carry the dashes that cut
// the sprocket holes, so it has to stay INSIDE the ribbon along its whole
// length. If the ribbon's curve is ever retuned, this one has to be retuned
// with it or the holes will wander off the edge and bite chunks out of it.
const A_FRAME = "M31 7 L54 57 H43.5 L31 28.5 L18.5 57 H8 Z";
const RIBBON = "M4 43 C 12 31, 28 20, 50 15 C 32 26, 19 38, 9 49 Z";
const RIBBON_SPINE = "M9 45 C 17 35, 30 26, 47 18";

// One four-point sparkle, centred on the origin at radius 1 — `use` scales and
// places it, so the two on the mark are literally the same shape and cannot
// drift apart when one is edited.
const SPARKLE =
  "M0 -1 C 0.13 -0.38, 0.38 -0.13, 1 0 C 0.38 0.13, 0.13 0.38, 0 1 " +
  "C -0.13 0.38, -0.38 0.13, -1 0 C -0.38 -0.13, -0.13 -0.38, 0 -1 Z";

/**
 * The Aniwala AI Studio mark.
 *
 * @param {string} [className] — extra classes; `.brand-mark` (base.css) is
 *   always applied and carries the baseline nudge that keeps it sitting level
 *   with the text beside it.
 * @param {boolean} [plain] — draw it in ONE colour (no gold sparkles). For
 *   places where the mark is already inside a coloured or inverted surface.
 */
export default function Logo({ className = "", plain = false, ...rest }) {
  // ⚠ ONE ID PER INSTANCE, AND IT MATTERS. The landing page draws this mark
  // twice — nav and footer — and two `<mask id="film">` in one document is a
  // duplicate id: the browser resolves both references to the FIRST one, so
  // editing either mark would silently change the other.
  const uid = useId();
  const maskId = `film-${uid}`;
  const starId = `star-${uid}`;

  return (
    <svg
      viewBox="0 0 64 64"
      width="1em"
      height="1em"
      className={`brand-mark ${className}`.trim()}
      role="img"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <defs>
        <path id={starId} d={SPARKLE} />
        {/* White keeps, black cuts. The ribbon is painted white so it survives,
            then the dashed spine is stroked black over it — every dash removes
            a rectangle of ribbon, which is exactly a sprocket hole. */}
        <mask id={maskId}>
          <rect width="64" height="64" fill="black" />
          <path d={RIBBON} fill="white" />
          <path
            d={RIBBON_SPINE}
            fill="none"
            stroke="black"
            strokeWidth="4.6"
            strokeDasharray="2.6 5"
            strokeLinecap="butt"
          />
        </mask>
      </defs>

      <path d={A_FRAME} fill="currentColor" />
      <path d={RIBBON} fill="currentColor" mask={`url(#${maskId})`} />

      {/* The AI half. `plain` drops them to the same colour rather than dropping
          them altogether — a mark with no sparkle is the STUDIO's logo, and the
          two should not be confusable. */}
      <use
        href={`#${starId}`}
        transform="translate(56 9) scale(7)"
        fill={plain ? "currentColor" : "var(--primary)"}
      />
      <use
        href={`#${starId}`}
        transform="translate(45.5 5) scale(3.4)"
        fill={plain ? "currentColor" : "var(--primary)"}
      />
    </svg>
  );
}
