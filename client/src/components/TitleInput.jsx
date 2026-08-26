// ONE way to rename a thing, used on every screen that shows a project's name.
//
// ⚠ RENAMING WAS FOUR DIFFERENT INTERACTIONS, WHICH IS THREE TOO MANY. The
// video editor had an always-editable title box in its top bar; the final-video
// workspace had a title you clicked to turn INTO a box; the libraries had an
// inline box behind a menu item; and Plan & Script had `window.prompt()` — a
// grey browser dialog reading "localhost:5173 says", which is the one thing on
// screen that cannot be styled and does not belong in a product.
//
// The editor's is the one that was right, so it is the one everything copies: a
// box that already looks like the title, that you click and type in.
//
// ⚠ IT SAVES ON BLUR AND ON ENTER, NOT ON EVERY KEYSTROKE. A rename is one
// request per rename; typing "Chase sequence" should not be fourteen. Escape
// puts the old name back, which is the only way out of a half-typed name that
// doesn't require remembering what it used to say.
import { useEffect, useRef, useState } from "react";

export default function TitleInput({
  value,
  onSave,
  placeholder = "Untitled",
  ariaLabel = "Title",
  disabled = false,
  // Extra classes for the one thing that legitimately differs between screens:
  // how wide the box is in the row it sits in.
  className = "",
}) {
  const [draft, setDraft] = useState(value || "");
  const [busy, setBusy] = useState(false);
  // What we last handed to `onSave`, so a save that succeeds doesn't get undone
  // by the prop catching up a render later.
  const committed = useRef(value || "");
  // ⚠ ESCAPE HAS TO SUPPRESS THE BLUR THAT FOLLOWS IT. `setDraft` doesn't apply
  // until the next render, so the `commit()` triggered by blurring would still
  // be reading the abandoned text and would save exactly what Escape means to
  // throw away.
  const escaping = useRef(false);

  // Follow the prop while the user is NOT typing. A rename made elsewhere (the
  // library row, another tab) should show up here; one made HERE must not be
  // clobbered by the round trip that is still in the air.
  useEffect(() => {
    if (busy) return;
    if ((value || "") === committed.current) return;
    committed.current = value || "";
    setDraft(value || "");
  }, [value, busy]);

  async function commit() {
    const next = draft.trim();
    // Nothing typed, or nothing changed → no request. This is also the blur
    // path, which fires every time the user clicks away from the box.
    if (!next || next === (value || "").trim()) {
      setDraft(value || "");
      return;
    }
    setBusy(true);
    committed.current = next;
    try {
      await onSave(next);
    } catch {
      // The caller shows the error — it owns the page's error line. All this
      // has to do is stop claiming a name that was never saved.
      committed.current = value || "";
      setDraft(value || "");
    } finally {
      setBusy(false);
    }
  }

  return (
    <input
      className={`title-input ${className}`.trim()}
      value={draft}
      placeholder={placeholder}
      aria-label={ariaLabel}
      disabled={disabled || busy}
      maxLength={120}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (escaping.current) {
          escaping.current = false;
          return;
        }
        commit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          e.currentTarget.blur(); // blur commits, so there is ONE save path
        }
        if (e.key === "Escape") {
          escaping.current = true;
          setDraft(value || "");
          e.currentTarget.blur();
        }
      }}
    />
  );
}
