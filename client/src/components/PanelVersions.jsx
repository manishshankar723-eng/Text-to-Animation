// Version arrows over a storyboard panel.
//
// Re-drawing a shot used to replace the picture and the previous one was gone.
// Every render is now archived, so this steps between them: ‹ 2 / 3 ›.
//
// Choosing a version SWITCHES the panel — it copies that render back over
// `panel_NN.png` — so the board, the PDF, the ZIP, the key poses and the
// animatic all follow. That is deliberately not a preview-only browser: "I can
// see the old one but can't have it back" would be worse than not showing it.
//
// Shown as soon as the panel has ONE version, with the arrows disabled. Hiding
// it until there were two made the feature undiscoverable — you only saw the
// control after you'd already used the thing it controls, which is backwards,
// and it was asked about twice. "1 / 1" also answers "how many takes of this
// shot do I have?" at a glance.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";

export default function PanelVersions({ jobId, index, disabled, onSwitched }) {
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setInfo(await api.getPanelVersions(jobId, index));
    } catch {
      // A board drawn before versions existed simply has none — not an error.
    }
  }, [jobId, index]);

  useEffect(() => {
    load();
  }, [load]);

  const total = info?.versions || 0;
  const active = info?.active_version ?? 0;
  // Nothing on disk yet (a panel that has never been drawn) — there is no take
  // to count, so stay out of the way.
  if (total < 1) return null;
  // With one take there is nothing to step between, but the pill still shows so
  // the control is findable and the count is legible.
  const only = total < 2;

  async function step(delta) {
    if (busy) return;
    // Wraps, like the key-pose viewer: these are alternatives to compare, and
    // bumping into an end just makes you click back the other way.
    const next = (active + delta + total) % total;
    setBusy(true);
    try {
      await api.usePanelVersion(jobId, index, next);
      setInfo((i) => ({ ...i, active_version: next }));
      // The picture behind panel_NN.png changed but its URL did NOT, so the
      // board has to re-fetch this panel or the old bytes stay put. It refreshes
      // THIS PANEL ONLY (StoryboardBoard.refreshPanelImage): a version switch
      // moves no indices, so there is nothing for the other tiles to re-read,
      // and reloading the whole board made the page blink on every ‹ › press.
      await onSwitched?.();
    } catch {
      // Leave the badge as it was; the board's own error line reports failures.
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel-versions" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className="panel-versions-nav"
        title="Previous version of this panel"
        disabled={only || disabled || busy}
        onClick={() => step(-1)}
      >
        ‹
      </button>
      <span
        className="panel-versions-count"
        title={
          only
            ? "One version of this shot. Press “Generate panel” to draw another — the one you have now is kept, and these arrows step between them."
            : `${total} versions of this shot — every redraw is kept. Use the arrows to switch.`
        }
      >
        {active + 1} / {total}
      </span>
      <button
        type="button"
        className="panel-versions-nav"
        title="Next version of this panel"
        disabled={only || disabled || busy}
        onClick={() => step(1)}
      >
        ›
      </button>
    </div>
  );
}
