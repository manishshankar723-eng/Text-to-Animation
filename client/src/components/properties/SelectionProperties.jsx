// SelectionProperties.jsx — what the pane says when MORE THAN ONE thing is
// selected.
//
// One of the Properties panes, and the only one that describes a set rather than
// a clip. It exists because the alternative is worse in both directions: showing
// the first clip's settings would let you edit one thing while forty are lit up
// on the timeline, and showing nothing would make a marquee look like it had
// failed. So the pane names what is selected and offers only the operations that
// mean something for a set — delete, group, ungroup, nudge, deselect.
//
// ⚠ NO per-clip properties here, deliberately. "Set the colour of all of these"
// sounds reasonable until you notice the selection can hold a picture, a caption
// and a piece of audio at once, and that half the fields would be greyed out for
// most selections. Click one clip and you get its whole pane; that is the trade.
//
// Presentational, like every other pane: the editor owns the selection and hands
// this the list plus the handlers.

import Icon from "../Icon.jsx";
import { PropGroup, PropRow, PropNote } from "./PropGroup.jsx";
import {
  countByKind,
  GROUPABLE,
  KINDS,
  selectionLabel,
} from "../../animatic/selection.js";

// What each kind is called in the breakdown, and the same icon its lane carries
// in the gutter — so the list reads as "these rows" rather than as jargon.
const ROWS = {
  frame: { icon: "🖼", one: "picture", many: "pictures" },
  text: { icon: "T", one: "text clip", many: "text clips" },
  shape: { icon: "◆", one: "shape", many: "shapes" },
  overlay: { icon: "🖼", one: "picture layer", many: "picture layers" },
  audio: { icon: "♪", one: "audio clip", many: "audio clips" },
};

// How far the nudge buttons move a selection. A frame at 25fps is 40ms and at
// 30fps is 33ms, so neither is "one frame" on every project — these are the two
// sizes you actually reach for: a touch, and a beat.
const NUDGE_MS = 100;
const NUDGE_BIG_MS = 1000;

export default function SelectionProperties({
  selection,
  // How many of the selected clips already carry a `group_id`. Counted by the
  // editor, which is the side that holds the clips — an item is only `{kind,id}`
  // and cannot answer this itself.
  groupedCount = 0,
  onMove,
  onGroup,
  onUngroup,
  onDelete,
  onClose,
}) {
  const counts = countByKind(selection);
  // Can any of this be moved along the timeline? Pictures cannot — they are a
  // sequence, not free-floating clips — so a selection of nothing but pictures
  // gets no nudge buttons rather than buttons that quietly do nothing.
  const movable = selection.some((item) => GROUPABLE.includes(item.kind));

  return (
    <div className="an-props">
      <div className="an-prop-ident">
        <div className="an-prop-ident-text">
          <span className="an-prop-kind">Selection</span>
          <span className="an-prop-name">{selection.length} clips</span>
          <span className="an-prop-sub">{selectionLabel(selection)}</span>
        </div>
      </div>

      <PropGroup id="selection:what" title="What's selected">
        {KINDS.filter((kind) => counts[kind]).map((kind) => (
          <PropRow key={kind} label={ROWS[kind].icon}>
            <span className="an-sel-count">
              <strong>{counts[kind]}</strong>{" "}
              {counts[kind] === 1 ? ROWS[kind].one : ROWS[kind].many}
            </span>
          </PropRow>
        ))}
        <PropNote>
          Drag any one of them to move them all. Shift-click a clip to add it or
          take it out; drag a box over the empty part of a lane to select more.
        </PropNote>
      </PropGroup>

      {movable && (
        <PropGroup id="selection:move" title="Nudge">
          <PropRow full>
            <span className="an-set-chips">
              <button type="button" className="opt-chip" onClick={() => onMove(-NUDGE_BIG_MS)}>
                ⟵ 1s
              </button>
              <button type="button" className="opt-chip" onClick={() => onMove(-NUDGE_MS)}>
                ⟵ 0.1s
              </button>
              <button type="button" className="opt-chip" onClick={() => onMove(NUDGE_MS)}>
                0.1s ⟶
              </button>
              <button type="button" className="opt-chip" onClick={() => onMove(NUDGE_BIG_MS)}>
                1s ⟶
              </button>
            </span>
          </PropRow>
          <PropNote>
            Moves everything by the same amount, so the spacing between them
            never changes. Pictures stay where they are — a shot starts where the
            one before it ended, so it has nowhere else to be.
          </PropNote>
        </PropGroup>
      )}

      <PropGroup id="selection:group" title="Group">
        <PropRow full>
          <span className="an-set-chips">
            <button type="button" className="opt-chip" onClick={onGroup}>
              <Icon name="link" /> Group
              <span className="opt-chip-note">Ctrl+G</span>
            </button>
            <button type="button" className="opt-chip" onClick={onUngroup}>
              Ungroup
              <span className="opt-chip-note">Ctrl+Shift+G</span>
            </button>
          </span>
        </PropRow>
        <PropNote>
          {groupedCount
            ? `${groupedCount} of these are already in a group — grouping again ties the whole selection together as one.`
            : "Grouped clips are selected, moved and deleted together: click one and you have them all."}
        </PropNote>
      </PropGroup>

      <div className="an-prop-actions">
        <button type="button" className="btn small danger-btn" onClick={onDelete}>
          <Icon name="close" /> Delete {selection.length} clips
        </button>
        <button type="button" className="btn small ghost" onClick={onClose}>
          Deselect
        </button>
      </div>
    </div>
  );
}
