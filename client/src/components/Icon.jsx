// Icon.jsx — the app's icon set, as inline SVG.
//
// Why not emoji: ✏️ and 🗑 carry their OWN colours (a pink pencil, a teal bin)
// and no CSS can recolour them, while ▶ and ⧉ are monochrome text glyphs. Put
// them in a row and they look like they came from three different apps — which
// is exactly what was reported.
//
// Every path here is stroked with `currentColor`, so an icon simply takes the
// colour of the button it sits in. That's what makes hover, the muted default
// and the red danger state work on all of them at once.
//
// Sized in `em` so an icon follows its button's font-size.

const STROKE = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

const PATHS = {
  // Play is the one solid shape — a stroked triangle reads as an outline, not
  // a play button.
  play: <polygon points="7 4 20 12 7 20" fill="currentColor" stroke="none" />,
  pencil: (
    <>
      <path d="M12 20h9" {...STROKE} />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" {...STROKE} />
    </>
  ),
  trash: (
    <>
      <path d="M3 6h18" {...STROKE} />
      <path d="M8 6V4h8v2" {...STROKE} />
      <path d="M18.5 6 17.5 20H6.5L5.5 6" {...STROKE} />
      <path d="M10 11v5M14 11v5" {...STROKE} />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12" {...STROKE} />
      <path d="m7 10 5 5 5-5" {...STROKE} />
      <path d="M5 21h14" {...STROKE} />
    </>
  ),
  link: (
    <>
      <path d="M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07l-1.4 1.4" {...STROKE} />
      <path d="M14 11a5 5 0 0 0-7.07 0L4.1 13.83a5 5 0 0 0 7.07 7.07l1.4-1.4" {...STROKE} />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" {...STROKE} />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" {...STROKE} />
    </>
  ),
  close: <path d="M18 6 6 18M6 6l12 12" {...STROKE} />,
  // ⚠ A PAIR, LIKE eye / eye-off, and the shackle is what differs — closed on
  // the body for `lock`, lifted and open on one side for `unlock`. Drawn rather
  // than the padlock emoji pair for the reason at the top of this file: those
  // carry their own colour and cannot show the app's accent when a row is locked.
  lock: (
    <>
      <rect x="4.5" y="10.5" width="15" height="10.5" rx="2" {...STROKE} />
      <path d="M8 10.5V7a4 4 0 0 1 8 0v3.5" {...STROKE} />
    </>
  ),
  unlock: (
    <>
      <rect x="4.5" y="10.5" width="15" height="10.5" rx="2" {...STROKE} />
      <path d="M8 10.5V7a4 4 0 0 1 7.5-1.9" {...STROKE} />
    </>
  ),
  save: (
    <>
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" {...STROKE} />
      <path d="M17 21v-8H7v8M7 3v5h8" {...STROKE} />
    </>
  ),
  text: (
    <>
      <path d="M4 7V4h16v3" {...STROKE} />
      <path d="M12 4v16M9 20h6" {...STROKE} />
    </>
  ),
  // A colour card — a clip whose whole content is one flat colour. A card with
  // a SOLID block inside it, because a plain stroked rectangle beside these
  // reads as an empty frame, which is the opposite of what this clip is. The
  // fill is `currentColor` like `play`, so it still takes the button's colour
  // rather than carrying one of its own.
  card: (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2.5" {...STROKE} />
      <rect x="6.5" y="8" width="11" height="8" rx="1" fill="currentColor" stroke="none" />
    </>
  ),
  // The two Media-pane views, drawn as what they arrange rather than as
  // letters: four tiles for the grid, three rows for the list. Same 24-box and
  // the same stroke, so the pair reads as one switch with two positions.
  grid: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" {...STROKE} />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" {...STROKE} />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" {...STROKE} />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" {...STROKE} />
    </>
  ),
  list: (
    <>
      <rect x="3.5" y="4.5" width="4.5" height="4.5" rx="1.2" {...STROKE} />
      <rect x="3.5" y="14.5" width="4.5" height="4.5" rx="1.2" {...STROKE} />
      <path d="M11.5 6.75h9M11.5 16.75h9" {...STROKE} />
    </>
  ),
  // --- Is this layer drawn? ------------------------------------------------
  // The timeline gutter's switch for a row you can SEE, and the counterpart of
  // the 🔇 an audio row has always had. Two states of one control, so they are
  // the same eye with one stroke through it rather than two different drawings:
  // whichever is showing, you are looking at the same object.
  eye: (
    <>
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z" {...STROKE} />
      <circle cx="12" cy="12" r="3" {...STROKE} />
    </>
  ),
  "eye-off": (
    <>
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z" {...STROKE} />
      <circle cx="12" cy="12" r="3" {...STROKE} />
      {/* The stroke through it. Drawn last so it reads as one mark ON the eye. */}
      <path d="M4 20 20 4" {...STROKE} />
    </>
  ),

  // --- The two workspaces, drawn as the layout they arrange ----------------
  // ⚠ THESE ARE MAPS, NOT DECORATION. A workspace is a place for every pane, so
  // its icon is that arrangement at 1em: the window's outline, the seams where
  // the real seams are, and the Program pane filled in — because "where does the
  // picture end up, and how big is it?" is the only question anyone opens this
  // menu to answer. Redraw them whenever the CSS in the "Workspaces" block of
  // animatic-editor.css moves a pane, or the icon starts telling a lie.
  //
  // ⚠ THE WINDOW FILLS THE 24-BOX (1.6 → 22.4, not the ~3px inset the symbol
  // icons use). A map is read by its INTERNAL divisions, so the outline has to
  // be as big as the button will allow — inset like a glyph it came out a
  // postage stamp in a 2.3rem square, which is what was reported.

  // Long: Media | Program | Properties across the top, timeline full width
  // under all three. The filled block is the wide monitor.
  "layout-long": (
    <>
      <rect x="1.6" y="2.6" width="20.8" height="18.8" rx="2.6" {...STROKE} />
      <path d="M1.6 15.1h20.8M7.4 2.6v12.5M16.6 2.6v12.5" {...STROKE} />
      <rect x="8.6" y="6.9" width="7" height="3.9" rx="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  // Reel / Shorts: one tall monitor down the WHOLE left side, with Media,
  // Properties and the timeline stacked in the space beside it.
  "layout-reel": (
    <>
      <rect x="1.6" y="2.6" width="20.8" height="18.8" rx="2.6" {...STROKE} />
      <path d="M9.8 2.6v18.8M9.8 15.1h12.6M16.6 2.6v12.5" {...STROKE} />
      <rect x="3.3" y="7.1" width="5" height="9" rx="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  // --- THE TIMELINE'S SIX TOOLS -------------------------------------------
  // They were the LETTERS V / C / B / N / H / Z until now — Premiere's keys,
  // printed straight into the buttons. Replaced on request ("i want you add icon
  // replace V, C, B, N, H, Z leter"), with a reference toolbar handed over and an
  // explicit "you not copy you add your own style and keep comman icon": so these
  // are the ordinary shapes everyone already knows (pointer, scissors,
  // magnifier), drawn in THIS file's 24-box with `STROKE`, not traced.
  //
  // ⚠ THE KEY IS NOW ONLY IN THE TOOLTIP, and that is the one thing this change
  // cost. Every button's `title` is "<label> (<key>) — <hint>", which is where it
  // has to stay: the shortcuts still work, and the letter was the only thing
  // teaching them. Do not drop the key from a title.
  //
  // ⚠ TWO OF THE SIX ARE NOT COMMON ICONS, BECAUSE THE TOOLS ARE NOT COMMON:
  // ripple and rolling have no everyday glyph, so they are drawn as WHAT THEY DO
  // TO CLIPS — which is also how they differ from each other, and the pair has
  // to be read as a pair. Ripple closes a gap (a fixed edge, an arrow, and the
  // clip sliding back to it: the track gets SHORTER). Rolling moves a seam inside
  // a bar whose outer edges never move (one bar, a seam, a two-headed arrow ON
  // the seam: the track stays the SAME LENGTH). If either is redrawn, redraw both.

  // The pointer. Solid, like `play`, for the same reason: an outlined cursor
  // reads as a shape someone drew rather than as the cursor itself.
  select: <path d="M4.5 3.5 11.8 21l2.6-7.6L22 10.8Z" fill="currentColor" stroke="none" />,
  // The razor, as scissors — the one universal "cut here". Premiere's own blade
  // is a lovelier drawing and illegible at 18px, which is the size this button is.
  razor: (
    <>
      <circle cx="6" cy="6" r="2.6" {...STROKE} />
      <circle cx="6" cy="18" r="2.6" {...STROKE} />
      <path d="M20.5 3.5 8.2 15.8" {...STROKE} />
      <path d="M14.3 14.4 20.5 20.5" {...STROKE} />
      <path d="M8.2 8.2 11.6 11.6" {...STROKE} />
    </>
  ),
  // Ripple: the edge that stays, and the clip after it sliding back onto it.
  ripple: (
    <>
      <path d="M3.5 4.5v15" {...STROKE} />
      <rect x="12.5" y="7" width="8" height="10" rx="1.5" {...STROKE} />
      <path d="M10.5 12H5.2" {...STROKE} />
      <path d="M7.4 9.8 5.2 12l2.2 2.2" {...STROKE} />
    </>
  ),
  // Rolling: one bar, both ends nailed down, and the seam in the middle free to
  // move either way.
  // ⚠ THE CHEVRONS HAVE NO SHAFT, AND THAT IS THE WHOLE DRAWING. With a
  // two-headed arrow across the bar the shaft ran straight over the seam and the
  // pair merged into one diamond at 18px — which is the size this button is, so
  // it is the size that decides. Rendered and compared against three
  // alternatives; this is the only one where the SEAM survives, and the seam is
  // the thing that moves.
  rolling: (
    <>
      <rect x="2.5" y="6.5" width="19" height="11" rx="1.5" {...STROKE} />
      <path d="M12 6.5v11" {...STROKE} />
      <path d="M9.6 9.9 7.4 12l2.2 2.1" {...STROKE} />
      <path d="M14.4 9.9 16.6 12l-2.2 2.1" {...STROKE} />
    </>
  ),
  // The hand. Four strokes and no shortcut: a palm is one of the few shapes the
  // eye will not accept simplified — a mitten reads as a mitten.
  hand: (
    <>
      <path d="M18 11V6a2 2 0 0 0-4 0" {...STROKE} />
      <path d="M14 10V4.5a2 2 0 0 0-4 0V6" {...STROKE} />
      <path d="M10 10.5V6.5a2 2 0 0 0-4 0V14" {...STROKE} />
      <path
        d="M18 8a2 2 0 0 1 4 0v6a8 8 0 0 1-8 8h-1.6c-2.6 0-4.3-.9-5.7-2.3l-3.4-3.4a2 2 0 0 1 2.8-2.8L7.6 15"
        {...STROKE}
      />
    </>
  ),
  // Zoom. The + is the DEFAULT direction, which is what a click does; Alt-click
  // zooms out and the tooltip is the only place that can say so.
  zoom: (
    <>
      <circle cx="10.5" cy="10.5" r="6.6" {...STROKE} />
      <path d="M20.5 20.5 15.4 15.4" {...STROKE} />
      <path d="M10.5 7.6v5.8M7.6 10.5h5.8" {...STROKE} />
    </>
  ),

  // The gear. Eight teeth as one ring of short spokes rather than a scalloped
  // outline: at 1em a drawn cog turns to mud, while spokes stay readable.
  settings: (
    <>
      <circle cx="12" cy="12" r="3.2" {...STROKE} />
      <path
        d="M12 2.6v2.6M12 18.8v2.6M21.4 12h-2.6M5.2 12H2.6M18.6 5.4l-1.8 1.8M7.2 16.8l-1.8 1.8M18.6 18.6l-1.8-1.8M7.2 7.2 5.4 5.4"
        {...STROKE}
      />
    </>
  ),

  // The sidebar toggle: a panel with its left rail drawn in. Same glyph for
  // both directions — the rail is the sidebar, and the tooltip says which way
  // a click goes, so the icon never has to flip and jump.
  sidebar: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2.5" {...STROKE} />
      <path d="M9.5 4v16" {...STROKE} />
    </>
  ),
};

export default function Icon({ name, size = "1.05em", className = "", title }) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg
      className={`icon ${className}`}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : "true"}
      focusable="false"
    >
      {title && <title>{title}</title>}
      {path}
    </svg>
  );
}
