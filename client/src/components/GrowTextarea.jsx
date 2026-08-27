// A multi-line text box that is always exactly as tall as what is in it.
//
// ⚠ IT EXISTS BECAUSE A FIXED-HEIGHT BOX HIDES THE THING THE USER IS EDITING.
// The review step's "Image prompt" was 64px tall with its own scrollbar, so a
// shot description — three or four lines of the sentence that will be drawn —
// was cut off after two. Directly above it, the read-only "FROM YOUR SCRIPT"
// box showed every word of the script line. The box you can only read was
// whole; the box you have to WRITE was the clipped one, which is backwards.
//
// ⚠ THE SISTER OF `GrowText` IN `admin/fields.jsx`, and the height maths is
// deliberately identical — see the note there. What differs is the manners: the
// admin one is a textarea pretending to be a one-line field, so it commits on
// Enter and hides its border until you go near it. This one is an ordinary
// visible textarea that happens to fit its text, so Enter inserts a newline
// like it does in every other prompt box in the app, and it keeps whatever
// `className` the caller gives it (`prompt-textarea`, in every case so far) so
// it looks exactly like the fixed box it replaced.
import { useEffect, useRef } from "react";

export default function GrowTextarea({ value, rows = 3, className = "", ...rest }) {
  const ref = useRef(null);

  // ⚠ `scrollHeight` ALONE IS TWO PIXELS SHORT, and the two it is short by are
  // the borders: `theme.css` puts `box-sizing: border-box` on everything, so a
  // height of exactly `scrollHeight` gives the text a content box smaller than
  // the text — and the `overflow: hidden` the stylesheet sets then eats the
  // bottom of the last line. `offsetHeight - clientHeight` is that border,
  // measured rather than guessed, so this stays right if the border changes.
  //
  // `height: auto` first is what lets the box SHRINK again: measured against
  // its current height, `scrollHeight` can only ever grow.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      // The floor, not the height: with `height: auto` set above, an empty box
      // measures as `rows` tall, so this is the size it never goes below.
      rows={rows}
      value={value}
      className={className}
      {...rest}
    />
  );
}
