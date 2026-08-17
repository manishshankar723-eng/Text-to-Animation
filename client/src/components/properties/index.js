// properties/ — the Properties pane, one component per selection state.
//
// The editor decides what is selected (exactly one thing at a time, see
// `selectOnly`) and renders the matching pane. Every one of them is
// presentational: it holds no state, and writes through the handlers it is
// given, so a pane can never disagree with the document.
//
// Two files here are deliberately NOT in this list, because neither is a pane:
//
//   PropGroup.jsx           the layout every pane is built from — sections,
//                           rows, fields. READ ITS HEADER BEFORE EDITING ANY
//                           PANE: the alignment down the whole pane depends on
//                           the two rules stated there.
//   VideoClipProperties.jsx the extra sections a video clip or a colour card
//                           adds to `FrameProperties`, and only that file uses
//                           it.

export { default as AudioProperties } from "./AudioProperties.jsx";
export { default as FrameProperties } from "./FrameProperties.jsx";
export { default as ShapeProperties } from "./ShapeProperties.jsx";
export { default as TextProperties } from "./TextProperties.jsx";
export { default as TransitionProperties } from "./TransitionProperties.jsx";
export { default as VideoProperties } from "./VideoProperties.jsx";
