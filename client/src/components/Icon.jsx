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
