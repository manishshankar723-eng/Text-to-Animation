// util.js — the two lines of arithmetic that every part of the editor needs and
// none of them owns.
//
// `clamp` was written out four times over (the editor, the transport, the
// properties panes and the video clip rows) once those files were separated, so
// it lives here instead. Nothing else belongs in this file: it is not a
// dumping ground, and anything about the scene model goes in `scene.js`.

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
