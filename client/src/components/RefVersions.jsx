// Version arrows over a character / prop reference thumbnail.
//
// Pressing Generate twice used to REPLACE the picture: the first one was drawn,
// paid for, and then gone before it could be looked at. Reported — "mai pehla
// image nahi dekh pa raha hun". Every take is kept now and this steps between
// them: ‹ 2 / 3 ›.
//
// ⚠ THE ONE ON SCREEN IS THE ONE THAT SHIPS. Picking a version swaps the card's
// active `referenceId`, and that id is what `handleGenerate` hands to the board
// — so the take you are looking at is the take every panel is drawn from. This
// is deliberately not a preview-only browser: "I can see the old one but can't
// have it back" would be worse than not showing it. Same rule as the board's
// `PanelVersions`, which this is the sibling of.
//
// ⚠ NOTHING IS RE-FETCHED. A generated reference gets a FRESH `reference_id`
// server-side (`uuid4().hex[:12]`, POST /characters/reference) and the old
// folder is never touched, so every take a session has made is still on the
// server and its blob preview is still in memory. The board needs an archive
// endpoint because a redraw overwrites `panel_NN.png`; a reference does not.
//
// Shown as soon as there is ONE take, with the arrows disabled — hiding it
// until there were two made the board's version control undiscoverable (you
// only saw it after using the thing it controls), and that was reported twice.
export default function RefVersions({ total, active, disabled, onPick }) {
  if (!total || total < 1) return null;
  // One take: nothing to step between, but the pill still shows so the control
  // is findable and "1 / 1" answers "how many did I draw?" at a glance.
  const only = total < 2;

  function step(delta) {
    // Wraps, like the board's arrows: these are alternatives to compare, and
    // bumping into an end just makes you click back the other way.
    onPick?.((active + delta + total) % total);
  }

  return (
    // Stop the click here — the thumbnail behind this opens the lightbox.
    <div className="panel-versions" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className="panel-versions-nav"
        title="Previous version of this reference"
        disabled={only || disabled}
        onClick={() => step(-1)}
      >
        ‹
      </button>
      <span
        className="panel-versions-count"
        title={
          only
            ? "One version of this reference. Press “Regenerate” to draw another — this one is kept, and these arrows step between them."
            : `${total} versions — every take is kept. The one shown here is the one the panels are drawn from.`
        }
      >
        {active + 1} / {total}
      </span>
      <button
        type="button"
        className="panel-versions-nav"
        title="Next version of this reference"
        disabled={only || disabled}
        onClick={() => step(1)}
      >
        ›
      </button>
    </div>
  );
}
