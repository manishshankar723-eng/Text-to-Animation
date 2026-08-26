// fields.jsx — the one editable text box the panel uses where a form must not
// look like a form.
//
// ⚠ IT EXISTS BECAUSE THREE SCREENS EDIT TEXT THAT HAS TO KEEP READING LIKE THE
// THING IT CONTROLS. A pricing card's bullet is what a customer will read; a
// feature's name is what the sidebar prints. Drawn as ordinary form fields, the
// pricing screen's two columns stop being comparable at a glance and the
// switchboard turns into a spreadsheet — so the box only appears when you go
// near it (`.admin-quiet-field` in `admin.css`), and the text underneath keeps
// the weight and colour it had when it was a `<span>`.
import { useEffect, useRef } from "react";

// A one-line-looking field that is really a textarea, and grows to fit its text.
//
// ⚠ A PLAIN `<input>` WOULD HIDE THE END OF THE COPY. The pricing card's columns
// are ~180px of a 440px card and their lines already wrap on the real pricing
// page — "Unlimited image generations" takes two lines there — so a single-line
// field would show an administrator a TRIMMED version of the very sentence a
// customer reads in full, with nothing on screen to say it had been trimmed.
// `rows={1}` plus the effect below means the box is exactly as tall as what is
// in it.
//
// ⚠ ENTER COMMITS, IT DOES NOT INSERT A NEWLINE. Every caller is editing one
// line of something, not a paragraph, and a stored "\n" would reach the pricing
// page as a line the layout never planned for.
export function GrowText({ value, className = "", ...rest }) {
  const ref = useRef(null);

  // ⚠ `scrollHeight` ALONE IS TWO PIXELS SHORT, and the two it is short by are
  // the borders: `theme.css` puts `box-sizing: border-box` on everything, so a
  // height of exactly `scrollHeight` gives the text a content box smaller than
  // the text — and `overflow: hidden` then eats the bottom of the last line.
  // `offsetHeight - clientHeight` is that border, measured rather than guessed,
  // so the field stays right if the border ever changes.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      className={`admin-quiet-field ${className}`.trim()}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          e.currentTarget.blur();
        }
      }}
      {...rest}
    />
  );
}
