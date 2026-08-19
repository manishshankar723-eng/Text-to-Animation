/**
 * FX_LIBRARY — everything you can DROP onto the timeline, filed in folders.
 *
 * The Media pane's Effects tab is a browser you take from, exactly like the
 * Shapes tab beside it: a library, not a list of what this animatic contains.
 * An NLE files these in folders because a flat list of everything the renderer
 * can do stops being findable at about a dozen entries, and this one is going
 * to grow — so the folders are here from the start rather than retrofitted once
 * the list is already unreadable.
 *
 * ---------------------------------------------------------------------------
 * ⚠ AN ENTRY IS A PRESET, NOT A KIND. `kind` IS NOT UNIQUE IN HERE.
 * ---------------------------------------------------------------------------
 * There is one `wipe` in the renderer and it takes a direction, but the library
 * lists FOUR wipes — one per direction — because "Wipe up" is the thing you
 * actually want to drag, and reaching it as "drag Wipe, then find the direction
 * chip in another pane" is two steps and a hunt for what should be one gesture.
 * So every entry carries the `params` it applies, and is addressed by its own
 * `id` ("wipe:up"), never by its kind.
 *
 * That is the whole reason `fxEntry` takes an ENTRY ID: a payload saying "wipe"
 * cannot say which one, and `kind` as a key would silently collapse the four
 * into whichever was found first.
 *
 * ---------------------------------------------------------------------------
 * ⚠ THE FOLDERS ARE A VIEW. THE KINDS ARE THE TRUTH.
 * ---------------------------------------------------------------------------
 * Nothing here decides what an effect or a transition IS — `EFFECT_PARAMS` in
 * `scene.js`, `TRANSITIONS` in `transitions.js` and `FADE_CURVES` in
 * `audio_mix.js` do, and all three are twinned in Python. This file only says
 * which presets are worth a row and what to call them. So a kind added to any of
 * those tables appears in this browser WITHOUT being
 * added here: anything whose kind is not named in `SHELVES` below falls into
 * "Uncategorised", which is the same reasoning as the family fill on the
 * timeline's transition badge — an entry nobody filed should be visible and
 * ugly, never invisible.
 *
 * A spec naming a kind this build doesn't have is dropped on the way through,
 * so removing an effect can't leave a tile that drags a payload nothing accepts.
 */

import { FADE_CURVE_INFO, FADE_CURVES } from "./audio_mix.js";
import { EFFECT_KINDS } from "./scene.js";
import {
  TRANSITIONS,
  TRANSITION_DIRECTIONS,
  TRANSITION_KINDS,
} from "./transitions.js";

/**
 * What each effect is called, and the one line that says what it does.
 *
 * ⚠ The labels MATCH `EFFECT_LABEL` in `EffectsPanel.jsx` on purpose — the
 * browser you drag from and the pane you land in have to name the same thing
 * the same way, or dropping "Colour look (LUT)" and getting a section called
 * something else reads as having added the wrong effect.
 */
export const EFFECT_INFO = {
  brightness: { label: "Brightness", note: "Lift or crush the whole picture" },
  contrast: { label: "Contrast", note: "Push the darks and lights apart" },
  saturation: { label: "Saturation", note: "How strong the colour is" },
  lut: { label: "Colour look (LUT)", note: "A .cube table, dialled in by amount" },
  chroma: { label: "Chroma key", note: "Key out a green screen" },
  exposure: { label: "Exposure", note: "In stops, the way a camera means it" },
  gamma: { label: "Gamma", note: "Bend the midtones without moving the ends" },
  temperature: { label: "Temperature & tint", note: "Warm/cool and green/magenta" },
  hue: { label: "Hue rotate", note: "Spin every colour round the wheel" },
  sepia: { label: "Sepia", note: "The old-photograph matrix" },
  posterize: { label: "Posterize", note: "Crush to a few flat bands" },
};

// The arrow a directional preset wears, and the word its note uses. Both are
// about the direction of TRAVEL, which is what `direction` means on both kinds
// that take one — see the note on `TRANSITION_PARAMS`.
const TRAVEL = {
  left: { glyph: "←", word: "leftwards" },
  right: { glyph: "→", word: "rightwards" },
  up: { glyph: "↑", word: "upwards" },
  down: { glyph: "↓", word: "downwards" },
};

/**
 * One preset per direction, in `TRANSITION_DIRECTIONS` order.
 *
 * ⚠ DERIVED, so a fifth direction added to that list appears here on its own
 * rather than being a row somebody has to remember to write. And in the SAME
 * order the Properties pane draws its chips in — two orderings of four arrows
 * on one screen is a thing to double-take at every time.
 */
function directional(kind, note) {
  const name = TRANSITIONS.find((t) => t.id === kind)?.label || kind;
  return TRANSITION_DIRECTIONS.map((d) => ({
    kind,
    id: `${kind}:${d}`,
    label: `${name} ${d}`,
    glyph: TRAVEL[d]?.glyph,
    note: note(TRAVEL[d]?.word || d),
    params: { direction: d },
  }));
}

// Which folder each preset is filed under, top level then section — the shape
// Premiere's Effects panel uses, and the one people arrive already knowing.
// `type` is what the drop payload carries: an effect joins a clip's chain, a
// transition goes on a cut, and the two land in different places.
const SHELVES = [
  {
    id: "video-effects",
    label: "Video Effects",
    note: "Drop one on a picture to grade it",
    type: "effect",
    sections: [
      {
        id: "colour-correction",
        label: "Colour Correction",
        entries: [
          { kind: "brightness" },
          { kind: "contrast" },
          { kind: "saturation" },
          { kind: "exposure" },
          { kind: "gamma" },
          { kind: "temperature" },
        ],
      },
      {
        id: "colour-looks",
        label: "Colour Looks",
        entries: [{ kind: "lut" }, { kind: "hue" }, { kind: "sepia" }],
      },
      // Stylise is its own shelf rather than a third colour section, because
      // posterize is not a correction — it is a look that throws information
      // away, and it is the first of what will be several.
      { id: "stylise", label: "Stylise", entries: [{ kind: "posterize" }] },
      { id: "keying", label: "Keying", entries: [{ kind: "chroma" }] },
    ],
  },
  {
    id: "video-transitions",
    label: "Video Transitions",
    note: "Drop one on a cut between two shots",
    type: "transition",
    sections: [
      {
        // Dips live under Dissolve because that is where an editor looks for
        // "Dip to Black", and because a dip IS the degenerate cross-fade —
        // through a colour instead of through the other shot.
        id: "dissolve",
        label: "Dissolve",
        entries: [
          { kind: "dissolve" },
          // ⚠ THE BARE DIP IS NAMED FOR WHAT IT DOES, not left as "Dip". Its
          // colour defaults to the BAR colour, which is black in a default
          // project and therefore identical to the preset below it — so
          // without the longer name the two rows look like a duplicate rather
          // than like a choice about which colour follows the letterbox.
          {
            kind: "dip",
            id: "dip",
            label: "Dip to the bar colour",
            note: "Out through whatever the letterbox is",
          },
          {
            kind: "dip",
            id: "dip:black",
            label: "Dip to black",
            note: "Out through black, whatever the letterbox is",
            params: { color: "#000000" },
          },
          {
            kind: "dip",
            id: "dip:white",
            label: "Dip to white",
            note: "Out through white — a flash rather than a beat",
            params: { color: "#ffffff" },
          },
        ],
      },
      {
        id: "wipe",
        label: "Wipe",
        entries: directional("wipe", (word) => `The edge sweeps ${word}`),
      },
      {
        id: "slide",
        label: "Slide",
        entries: directional("slide", (word) => `Both shots travel ${word}`),
      },
      {
        // The IRISES: one shape growing out of the middle. Filed together
        // because "a shape opens from the centre" is how you go looking for
        // them, and they are one line apart in the shader for the same reason.
        id: "iris",
        label: "Iris",
        entries: [
          { kind: "radial" },
          { kind: "diamond" },
          { kind: "box" },
          { kind: "angular" },
        ],
      },
      {
        id: "pattern",
        label: "Wipe Patterns",
        entries: [
          ...directional("diagonal", (word) => `An angled edge sweeps ${word}`),
          ...directional("blinds", (word) => `Bands wipe ${word} together`),
          // ⚠ ONLY TWO PRESETS, not four. A split is an AXIS: doors that open
          // left and right are the same picture, so generating one preset per
          // direction the way the sweeps do would put a visible duplicate in
          // the folder. The `direction` parameter still takes all four, and
          // both members of a pair resolve to the same matte.
          {
            kind: "split",
            id: "split:across",
            label: "Split across",
            glyph: "↔",
            note: "Barn doors open left and right",
            params: { direction: "right" },
          },
          {
            kind: "split",
            id: "split:down",
            label: "Split down",
            glyph: "↕",
            note: "Barn doors open up and down",
            params: { direction: "down" },
          },
          { kind: "checker" },
        ],
      },
    ],
  },
  {
    // ⚠ ITS OWN TOP-LEVEL FOLDER, not a section inside Video Transitions, and
    // not because Premiere files it that way. An audio transition lands on a
    // different KIND OF ROW: `laneTakes` in the timeline says yes to the picture
    // rows for one and the audio rows for the other, so a folder that mixed them
    // would be a folder where half the rows go grey the moment you start
    // dragging. The folder IS the answer to "where can I put this".
    id: "audio-transitions",
    label: "Audio Transitions",
    note: "Drop one on a cut between two sounds, or on the end of one",
    type: "audioTransition",
    sections: [
      {
        // One section holding three, with room for the folder Premiere has
        // beside it (its Crossfade folder is one of two). ⚠ AND THE KINDS ARE
        // THE CURVES: there is no fourth thing a crossfade could be here, so a
        // curve added to `FADE_CURVES` becomes a row in this folder on its own.
        id: "crossfade",
        label: "Crossfade",
        entries: FADE_CURVES.map((curve) => ({ kind: curve })),
      },
    ],
  },
];

/**
 * The truth table each family is checked against, so an entry naming something
 * this build cannot do is dropped rather than drawn.
 *
 * ⚠ THREE FAMILIES, ONE PER PLACE A DROP CAN LAND: an effect joins a picture
 * clip's chain, a video transition goes on a cut in the picture sequence, an
 * audio transition shapes the ends of audio clips. Nothing else about them
 * differs in this file, which is why they share every function below.
 */
const KNOWN = {
  effect: EFFECT_KINDS,
  transition: TRANSITION_KINDS,
  // ⚠ A CURVE IS THE KIND. There is no separate table of audio transitions to
  // keep in step with `FADE_CURVES` — the curves ARE the list, so the browser
  // cannot offer a crossfade the mixer has never heard of.
  audioTransition: FADE_CURVES,
};

/** One library entry from its spec, or null if this build has no such kind. */
function entryFrom(type, spec) {
  const kind = spec.kind;
  if (!(KNOWN[type] || []).includes(kind)) return null;
  const base =
    type === "effect"
      ? EFFECT_INFO[kind] || {}
      : type === "audioTransition"
        ? FADE_CURVE_INFO[kind] || {}
        : TRANSITIONS.find((t) => t.id === kind) || {};
  return {
    type,
    // A preset without an id of its own is the plain kind, and its id is the
    // kind — which is what keeps the five effects (none of which have presets
    // yet) reading as "brightness" rather than "brightness:default".
    id: spec.id || kind,
    kind,
    label: spec.label || base.label || kind,
    note: spec.note || base.note || "",
    glyph: spec.glyph || "",
    // ⚠ ALWAYS AN OBJECT, never absent. The editor spreads it onto a new
    // effect or transition without asking, and `{}` means "every default",
    // which is exactly what a preset with nothing to say wants.
    params: spec.params || {},
  };
}

function shelve() {
  const filedKinds = new Set();
  const out = SHELVES.map((shelf) => ({
    id: shelf.id,
    label: shelf.label,
    note: shelf.note,
    sections: shelf.sections
      .map((section) => ({
        id: `${shelf.id}/${section.id}`,
        label: section.label,
        items: section.entries
          .map((spec) => {
            const made = entryFrom(shelf.type, spec);
            if (made) filedKinds.add(`${shelf.type}:${made.kind}`);
            return made;
          })
          .filter(Boolean),
      }))
      // A section whose every kind has gone is not an empty folder to open, it
      // is a folder that should not be drawn.
      .filter((section) => section.items.length > 0),
  }));

  // ⚠ THE CATCH-ALL, and it is the point of resolving specs rather than writing
  // the list out twice. A kind added to `EFFECT_PARAMS` or `TRANSITIONS` and
  // not filed above still shows up, still drags and still works — it is simply
  // in the wrong-looking folder until someone files it. The alternative is an
  // effect that exists in both renderers and cannot be reached from the UI,
  // which is the kind of gap that survives for months because nothing anywhere
  // reports it. Tracked by KIND, so filing one preset of a kind files the kind.
  const loose = Object.entries(KNOWN)
    .flatMap(([type, kinds]) => kinds.map((k) => [type, k]))
    .filter(([type, kind]) => !filedKinds.has(`${type}:${kind}`))
    .map(([type, kind]) => entryFrom(type, { kind }))
    .filter(Boolean);
  if (loose.length) {
    out.push({
      id: "uncategorised",
      label: "Uncategorised",
      note: "Added to the renderer but not filed in a folder yet",
      sections: [{ id: "uncategorised/all", label: "Everything else", items: loose }],
    });
  }
  return out;
}

export const FX_LIBRARY = shelve();

/** How many draggable things the browser holds — the Media pane's count chip. */
export const FX_ITEM_COUNT = FX_LIBRARY.reduce(
  (sum, shelf) => sum + shelf.sections.reduce((n, s) => n + s.items.length, 0),
  0
);

/**
 * The drag payload, and the empty marker type beside it.
 *
 * ⚠ TWO ENTRIES ON THE CLIPBOARD, for the reason `Timeline.dragKind` explains:
 * `getData` is blank during `dragover` in every browser, so a lane can only
 * learn what is being dragged from the TYPE LIST. The marker is what lets the
 * picture rows light up (and the audio rows refuse) before the drop.
 *
 * ⚠ THE PAYLOAD CARRIES THE ENTRY ID, NOT THE KIND, and not the parameters
 * either. The id is looked back up on the other side, so the preset's values
 * come from this file at DROP time — a payload that carried them could be from
 * a tab open since before they were last edited.
 */
export const FX_DRAG_TYPE = "application/x-anim-fx";
/** The audio half of the same trick — see `fxMarkerType` at the bottom. */
export const AFX_DRAG_TYPE = "application/x-anim-afx";

/**
 * The entry a drop payload names, or null.
 *
 * ⚠ NULL IS THE POINT, not a nuisance. A payload is a string that crossed a
 * drag-and-drop boundary — it can come from an older tab, a build that had a
 * preset this one has dropped, or (in principle) anywhere. Looking it back up
 * is what stops an unknown id becoming an effect the renderer will silently
 * skip forever.
 */
export function fxEntry(type, id) {
  for (const shelf of FX_LIBRARY) {
    for (const section of shelf.sections) {
      for (const entry of section.items) {
        if (entry.type === type && entry.id === id) return entry;
      }
    }
  }
  return null;
}

const PAYLOAD_KIND = {
  effect: "fxEffect",
  transition: "fxTransition",
  audioTransition: "fxAudioTransition",
};

export function fxPayload(entry) {
  return { kind: PAYLOAD_KIND[entry.type] || "fxEffect", id: entry.id };
}

/**
 * The empty marker type a drag of this entry stamps beside its payload.
 *
 * ⚠ TWO MARKERS, NOT ONE, and the split is exactly where the timeline's rows
 * disagree. A picture row takes an effect or a video transition; an audio row
 * takes a crossfade and nothing else. Since `getData` is blank until the drop,
 * the marker is the ONLY thing a row can decide on mid-drag — so one shared
 * marker would light up every row for every drag and refuse half of them
 * afterwards, which is the "no entry" cursor arriving one gesture too late.
 *
 * Effects and video transitions still share theirs: they land on the same rows,
 * and `dropAsset` is where a transition dropped on an image layer is refused.
 */
export function fxMarkerType(entry) {
  return entry.type === "audioTransition" ? AFX_DRAG_TYPE : FX_DRAG_TYPE;
}
