import { useRef, useState } from "react";
import Icon from "./Icon.jsx";

// The script the user pasted or uploaded, shown in full on the review step.
//
// Line-numbered on purpose: each shot card says "FROM YOUR SCRIPT · LINE 12",
// and this is where that number can be looked up. Read-only — the script is the
// source, and editing it here would not re-run the breakdown.
// `defaultOpen` is false on the finished board: the board is about the panels,
// so the script rides along collapsed and opens on demand.
export default function ScriptPanel({ script, defaultOpen = true }) {
  const [copied, setCopied] = useState(false);
  const bodyRef = useRef(null);

  const text = (script || "").replace(/\s+$/, "");
  if (!text.trim()) return null;

  const lines = text.split("\n");
  const words = text.trim().split(/\s+/).length;

  // The reliable way out. This copies `text` — the script exactly as it
  // arrived — not the DOM selection, so line numbers can never ride along.
  async function copy(e) {
    // ⚠ The header is a <summary>: without BOTH of these, copying also
    // collapses the panel the user was reading.
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // The same call PlanScriptModal makes, and the same handling: a
      // denied clipboard permission is not worth a red banner, and the
      // text is on screen and selectable.
      setCopied(false);
    }
  }

  /** ⚠ CTRL+A INSIDE THE BOX MUST MEAN "THE SCRIPT", NOT "THE PAGE".
   *
   *  This panel is a read-only <ol>, not a <textarea>, so the browser has
   *  no reason to treat it as its own selection scope — Ctrl+A selected the
   *  entire review step (world card, every shot card, the sidebar) and the
   *  user could not get the script back out of it. Focus reaches here
   *  because the body carries tabIndex={-1}, which makes it click-focusable
   *  WITHOUT putting it in the tab order. `.spl-num` is already
   *  `user-select: none`, so the line numbers stay out of what gets copied.
   */
  function onKeyDown(e) {
    if (e.key !== "a" && e.key !== "A") return;
    if (!e.ctrlKey && !e.metaKey) return;
    const node = bodyRef.current;
    if (!node) return;
    e.preventDefault();
    const range = document.createRange();
    range.selectNodeContents(node);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  return (
    <details className="card script-panel" open={defaultOpen}>
      <summary className="script-panel-head">
        <span className="script-panel-title">📄 Your script</span>
        <span className="script-panel-meta">
          {lines.length} line{lines.length === 1 ? "" : "s"} · {words} word
          {words === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          className="btn ghost script-panel-copy"
          onClick={copy}
          title="Copy the whole script"
        >
          <Icon name="copy" /> {copied ? "Copied" : "Copy"}
        </button>
      </summary>
      <div
        className="script-panel-body"
        ref={bodyRef}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <ol className="script-panel-lines">
          {lines.map((line, i) => (
            <li key={i}>
              <span className="spl-num">{i + 1}</span>
              {/* nbsp keeps a blank line's row height so numbering stays readable */}
              <span className="spl-text">{line || " "}</span>
            </li>
          ))}
        </ol>
      </div>
    </details>
  );
}
