// WorkflowIcon.jsx — one drawn glyph per workflow, keyed by the workflow's ID.
//
// ⚠ THIS EXISTS BECAUSE TWO WORKFLOWS SHARED AN EMOJI. "Text to Turnaround
// Image" and "Image to Animatic Image" were both 🖼️ — the same picture, twice,
// in a rail whose whole job is telling six rooms apart. And 🗓️ 📝 🎞️ 🎬 are
// drawn by the OPERATING SYSTEM, so the set looked like four different apps on
// Windows and a fifth thing again on a phone. Same argument as `Icon.jsx`, which
// is where the app already decided this: every path here is stroked in
// `currentColor` and sized in `em`, so a glyph takes the colour and the size of
// whatever it sits in.
//
// ⚠ SILHOUETTE FIRST. These are read at 17px in a nav rail, where detail is
// mud and only the OUTLINE survives. So each one is a different shape before it
// is a different picture: a tall page, a four-up grid, a wide filmstrip, a
// stack of bars. If a seventh workflow is ever added, give it a silhouette none
// of these already own — not a nicer drawing of one of them.
//
// ⚠ KEYED BY ID, WITH THE SERVER'S EMOJI AS THE FALLBACK. The icon an account
// sees comes from `/auth/me/entitlements`, which serves whatever an administrator
// typed into the admin panel (see `features.py`) — it is DATA, and it can be a
// workflow this build has never heard of. So an unknown id renders the emoji it
// was sent rather than nothing at all, and the admin panel keeps working exactly
// as it did.
//
// ⚠ AND THAT IS WHY THE MAP HAS SIX ENTRIES, NOT FOUR. Two workflows are
// switched off in the admin panel today; they are one toggle from being back,
// and a workflow that returns with a hole where its icon was is worse than one
// that never left.

const STROKE = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

// The AI sparkle, small, for the one workflow that spends money on a model.
// Solid, because a 3px stroked star is a smudge.
const SPARK = (
  <path
    d="M19.5 2.2c.3 1.5.9 2.1 2.4 2.4-1.5.3-2.1.9-2.4 2.4-.3-1.5-.9-2.1-2.4-2.4 1.5-.3 2.1-.9 2.4-2.4Z"
    fill="currentColor"
    stroke="none"
  />
);

const PATHS = {
  // Plan & Script — a written page. The tallest, narrowest silhouette in the
  // set, which is what keeps it apart from the storyboard's grid.
  "plan-and-script": (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" {...STROKE} />
      <path d="M8.5 8h7M8.5 12h7M8.5 16h4" {...STROKE} />
    </>
  ),

  // Text to Turnaround Image — a figure, and an arc under it for the turn. The
  // only glyph in the set with a person in it. (Switched off today; see above.)
  "text-to-image": (
    <>
      <circle cx="12" cy="6.5" r="3" {...STROKE} />
      <path d="M7.5 15.5a4.5 4.5 0 0 1 9 0" {...STROKE} />
      <path d="M4 18.5a10 10 0 0 0 16 0" {...STROKE} />
      <path d="M4 18.5l3-1M4 18.5l.6 3" {...STROKE} />
    </>
  ),

  // Script to Storyboard — four panels. The only grid.
  "script-to-storyboard": (
    <>
      <rect x="3" y="4" width="8" height="7" rx="1.5" {...STROKE} />
      <rect x="13" y="4" width="8" height="7" rx="1.5" {...STROKE} />
      <rect x="3" y="13" width="8" height="7" rx="1.5" {...STROKE} />
      <rect x="13" y="13" width="8" height="7" rx="1.5" {...STROKE} />
    </>
  ),

  // Image to Animatic Image — a filmstrip, sprockets and all. Widest, flattest
  // silhouette in the set. It is the same shape as the ribbon in the brand mark
  // (Logo.jsx), on purpose: this workflow is the one that makes things move.
  "create-animatic-image": (
    <>
      <rect x="2" y="5" width="20" height="14" rx="2" {...STROKE} />
      <path d="M2 9h20M2 15h20" {...STROKE} />
      <path d="M7 5v4M12 5v4M17 5v4M7 15v4M12 15v4M17 15v4" {...STROKE} />
    </>
  ),

  // Image to AI Video — a frame with a play in it, and the sparkle that says a
  // model made it. The sparkle is not decoration: this is the workflow that
  // BILLS, and it is worth a glance that the others are not.
  // (Switched off today; see above.)
  "animatics-to-video": (
    <>
      <rect x="2.5" y="6" width="14" height="13" rx="2" {...STROKE} />
      <path d="M8 10.2l4 2.3-4 2.3z" fill="currentColor" stroke="none" />
      {SPARK}
    </>
  ),

  // Video Editor — a timeline: clips of different lengths, and the playhead
  // standing through them. A stack of bars, which nothing else here is.
  "storyboard-to-animatics": (
    <>
      <rect x="2.5" y="5" width="9" height="3.6" rx="1.3" {...STROKE} />
      <rect x="6" y="10.2" width="13" height="3.6" rx="1.3" {...STROKE} />
      <rect x="2.5" y="15.4" width="7" height="3.6" rx="1.3" {...STROKE} />
      <path d="M15.5 2.6v18.8" {...STROKE} />
    </>
  ),
};

/**
 * @param {string} id — the workflow id (`plan-and-script`, …).
 * @param {string} [fallback] — the emoji the server sent, drawn when this build
 *   has no glyph for `id`. Never guess a glyph for an unknown workflow: an
 *   administrator can add one, and the wrong picture is worse than the emoji.
 */
export default function WorkflowIcon({ id, fallback = "", className = "", ...rest }) {
  const paths = PATHS[id];
  if (!paths) return fallback ? <>{fallback}</> : null;
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      className={`wf-glyph ${className}`.trim()}
      role="img"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {paths}
    </svg>
  );
}
