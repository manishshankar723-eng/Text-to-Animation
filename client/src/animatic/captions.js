// captions.js — which clips this app WROTE, and which lane they live on.
//
// ⚠ TWIN FILE: `captions.py` (`CAPTION_LAYER_ID`, `CAPTION_LAYER_NAME`,
// `CAPTION_ID_PREFIX`). The SERVER writes generated captions — the editor never
// makes one — so these three strings are the entire contract between the two
// halves: the server puts a clip on a lane, and the browser has to know which
// lane that was in order to draw it, name it and keep it at the top of the
// timeline. `tests/captions_check.py` compares them by running this file
// through node, exactly as it does the font list.
//
// WHY GENERATED CAPTIONS GET A LANE OF THEIR OWN. They used to be dropped onto
// the default text lane, on top of whatever the user had typed there — two
// unrelated things in one row, overlapping, with no way to tell by looking which
// was which. The lane is the separation: your text stays where you put it, a
// captions run fills its own row, and deleting that row deletes the pass rather
// than your work.

/** The reserved lane id. A user's own lanes carry random ids; this one name is
 *  spoken for, because the server has to be able to write it without asking. */
export const CAPTION_LAYER_ID = "captions";

/** What that lane is called in the gutter. */
export const CAPTION_LAYER_NAME = "Captions";

/** Every clip a captions or voiceover run made is named `cap…`, and that prefix
 *  is the ONLY record that a caption was generated rather than typed — which is
 *  what lets a second run replace the first instead of doubling every subtitle.
 *  Deliberately not a field on the clip: a generated caption has to be an
 *  ordinary caption in every other respect. */
export const CAPTION_ID_PREFIX = "cap";

/** Did a run write this clip, or did the user type it? */
export function isGeneratedCaption(clip) {
  return String(clip?.id || "").startsWith(CAPTION_ID_PREFIX);
}

/** Is this clip on the captions lane? */
export function onCaptionLayer(clip) {
  return (clip?.layer_id || "") === CAPTION_LAYER_ID;
}
