// Avatar — the round person glyph used for "this is you" throughout the app.
//
// Drawn as an SVG rather than an emoji or an image file: it stays crisp at any
// size, needs no network request (so it can't flash in late), and keeps the same
// silhouette in light and dark themes.
//
// Pass `initial` to show a letter instead of the person glyph once we know who
// the user is — a personalised avatar reads faster than a generic one. With no
// initial it falls back to the neutral figure.
export default function Avatar({ size = 32, initial = "", className = "" }) {
  const label = (initial || "").trim().charAt(0).toUpperCase();

  return (
    <svg
      className={`avatar-svg ${className}`}
      width={size}
      height={size}
      viewBox="0 0 100 100"
      role="img"
      aria-label={label ? `Your account (${label})` : "Your account"}
    >
      <circle cx="50" cy="50" r="50" className="avatar-bg" />
      {label ? (
        <text
          x="50"
          y="50"
          className="avatar-initial"
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="44"
          fontWeight="600"
        >
          {label}
        </text>
      ) : (
        <>
          {/* head */}
          <circle cx="50" cy="38" r="18" className="avatar-fg" />
          {/* shoulders — clipped by the outer circle, so it reads as a bust */}
          <path
            d="M50 62c-16 0-29 11-32 26a50 50 0 0 0 64 0c-3-15-16-26-32-26z"
            className="avatar-fg"
          />
        </>
      )}
    </svg>
  );
}
