"""SELECTING MORE THAN ONE THING: the list, the groups, and the rubber band.

Until this, the editor held six "the selected X" ids and exactly one of them
could be set, so every operation was one clip at a time — deleting a row of
forty auto-captions was forty clicks and forty presses of Delete. A selection is
now a LIST, and three gestures produce one: a rubber band over the lanes, a
shift-click, and a group.

The parts worth pinning are the pure ones, and they are all in
`client/src/animatic/selection.js`:

  · **the toggle**, because "shift-click a selected clip" must REMOVE it, while
    shift-clicking one member of a group whose others are in must not — the
    answer for a set is "in only if every one of them is in";
  · **group expansion**, because `group_id` is a shared string rather than a
    container, so "who else is in this group" is computed and can be got wrong;
  · **the band's hit test**, which must catch a clip it merely TOUCHES — every
    editor works that way, and requiring the whole clip inside would make a long
    one impossible to pick up without zooming out first.

⚠ JS-ONLY, on purpose, like `audio_razor_check.py`. A selection is not part of
the project — it is what you are pointing at right now — so `animatic.py` has no
counterpart. The one part that IS saved is `group_id`, and that is checked here
against the schema: a group the server drops on the next save is a group that
silently unties itself.

    python tests/selection_check.py

Needs `node`, which the client build already requires. Without it every check
here is reported as SKIPPED, which is a gap rather than a pass.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from server.schemas import (
    AnimaticAudio,
    AnimaticOverlay,
    AnimaticShape,
    AnimaticTextClip,
)

ROOT = Path(__file__).resolve().parent.parent
failures: list[str] = []
skipped: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def skip(label, why):
    print(f"  skip {label}  ({why})")
    skipped.append(label)


# A small project: two captions and a shape tied together, one loose caption, and
# two pieces of one audio file of which only the second is in the group. The
# audio pool is keyed by CLIP id — after a cut one file is several clips, and
# grouping one piece must not drag the other in with it.
POOLS = {
    "text": [
        {"id": "t1", "group_id": "gA"},
        {"id": "t2", "group_id": "gA"},
        {"id": "t3", "group_id": ""},
    ],
    "shape": [{"id": "s1", "group_id": "gA"}],
    "overlay": [{"id": "o1", "group_id": ""}],
    "audio": [{"id": "a1", "group_id": ""}, {"id": "a2", "group_id": "gA"}],
}

HARNESS = """
import {
  boxFromCorners,
  boxesOverlap,
  countByKind,
  dragged,
  expandGroup,
  expandSelection,
  groupOf,
  hasItem,
  keySet,
  parseKey,
  selKey,
  selectionLabel,
  toggleItems,
  uniqueItems,
} from "%(sel)s";

const pools = JSON.parse(process.argv[2]);
const out = {};
const keys = (list) => list.map((i) => selKey(i.kind, i.id));

// --- Keys -------------------------------------------------------------------
out.key = selKey("text", "t1");
out.parsed = parseKey("text:t1");
// An id may contain a colon (nothing generates one, but a hand-edited project
// can carry one); the KIND may not, so only the first separator counts.
out.parsedOdd = parseKey("audio:a:b");
out.parsedJunk = parseKey("nonsense");

// --- Group expansion --------------------------------------------------------
out.loneClip = keys(expandGroup({ kind: "text", id: "t3" }, pools));
out.grouped = keys(expandGroup({ kind: "text", id: "t2" }, pools));
// The clicked item comes back FIRST — it is the one the pane describes.
out.groupedHead = keys(expandGroup({ kind: "shape", id: "s1" }, pools))[0];
out.unknown = keys(expandGroup({ kind: "text", id: "nope" }, pools));
out.expandAll = keys(
  expandSelection([{ kind: "text", id: "t1" }, { kind: "overlay", id: "o1" }], pools)
);

// --- The toggle -------------------------------------------------------------
const one = [{ kind: "text", id: "t3" }];
out.toggleIn = keys(toggleItems(one, [{ kind: "overlay", id: "o1" }]));
out.toggleOut = keys(toggleItems(one, [{ kind: "text", id: "t3" }]));
// A group toggles as ONE thing: all in → out, partly in → all in.
const groupItems = expandGroup({ kind: "text", id: "t1" }, pools);
out.groupIn = keys(toggleItems([], groupItems));
out.groupOut = keys(toggleItems(groupItems, groupItems));
out.groupPartly = keys(toggleItems([{ kind: "text", id: "t1" }], groupItems)).length;

// --- Housekeeping -----------------------------------------------------------
out.unique = keys(uniqueItems([
  { kind: "text", id: "t1" },
  { kind: "text", id: "t1" },
  { kind: "audio", id: "t1" },   // same id, different kind: NOT a duplicate
]));
out.has = [hasItem(one, "text", "t3"), hasItem(one, "shape", "t3")];
out.setSize = keySet(groupItems).size;
out.counts = countByKind(groupItems);
out.label = selectionLabel(groupItems);
out.labelOne = selectionLabel([{ kind: "audio", id: "a1" }]);
out.labelNone = selectionLabel([]);
out.groupOf = [groupOf({ group_id: "gA" }), groupOf({}), groupOf(null)];

// --- The band ---------------------------------------------------------------
const clip = { left: 100, top: 10, right: 200, bottom: 40 };
out.touching = boxesOverlap({ left: 150, top: 0, right: 300, bottom: 100 }, clip);
out.covering = boxesOverlap({ left: 0, top: 0, right: 400, bottom: 100 }, clip);
out.pastIt = boxesOverlap({ left: 210, top: 0, right: 300, bottom: 100 }, clip);
// Edge to edge is NOT a hit: a band dragged up to a clip's left edge has not
// reached it, and half-open ranges are the rule everywhere else here too.
out.edge = boxesOverlap({ left: 0, top: 0, right: 100, bottom: 100 }, clip);
// Right time, wrong lane.
out.wrongLane = boxesOverlap({ left: 100, top: 60, right: 300, bottom: 90 }, clip);
// Dragged from bottom-right to top-left: same box.
out.backwards = boxFromCorners(300, 90, 100, 10);
out.slop = [dragged(0, 0, 2, 2), dragged(0, 0, 9, 0), dragged(0, 0, 0, 9)];

console.log(JSON.stringify(out));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="selection_")
    try:
        src = HARNESS % {"sel": (ROOT / "client/src/animatic/selection.js").as_uri()}
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness, json.dumps(POOLS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:600])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


LABELS = [
    "an item is known by its kind AND its id",
    "an id containing a colon still parses",
    "junk parses to nothing rather than to a broken item",
    "a clip in no group expands to itself",
    "a clip in a group expands to every member, across kinds",
    "the clicked clip comes back first, so the pane describes it",
    "expanding something that no longer exists is not a crash",
    "a whole selection expands, without duplicating a shared group",
    "shift-clicking an unselected clip adds it",
    "shift-clicking a selected clip removes it",
    "a group goes in as one",
    "…and comes out as one",
    "…and a partly-selected group goes fully in rather than out",
    "the same id on two different lanes is two different items",
    "an item is found by kind and id, not by id alone",
    "the selection counts by kind",
    "the selection reads as a sentence",
    "a group with no id set reads as ungrouped",
    "the band catches a clip it only touches",
    "…and one it covers",
    "…and nothing it has gone past",
    "…and not one it stops exactly at",
    "…and nothing on a lane it never reached",
    "a band dragged backwards is the same band",
    "a few pixels is a click, not a drag",
]

browser = run_node()
if browser is None:
    print("\nThe selection model")
    for label in LABELS:
        skip(label, "node not available")
else:
    js = browser
    print("\nWhat a selection is made of")
    check(LABELS[0], js["key"] == "text:t1" and js["parsed"] == {"kind": "text", "id": "t1"},
          f"({js['key']}, {js['parsed']})")
    check(LABELS[1], js["parsedOdd"] == {"kind": "audio", "id": "a:b"}, str(js["parsedOdd"]))
    check(LABELS[2], js["parsedJunk"] is None, str(js["parsedJunk"]))

    print("\nGroups — a shared string, not a container")
    check(LABELS[3], js["loneClip"] == ["text:t3"], str(js["loneClip"]))
    check(LABELS[4], sorted(js["grouped"]) == ["audio:a2", "shape:s1", "text:t1", "text:t2"],
          str(js["grouped"]))
    check(LABELS[5], js["groupedHead"] == "shape:s1", js["groupedHead"])
    check(LABELS[6], js["unknown"] == ["text:nope"], str(js["unknown"]))
    check(LABELS[7],
          sorted(js["expandAll"]) == ["audio:a2", "overlay:o1", "shape:s1", "text:t1", "text:t2"]
          and len(js["expandAll"]) == 5,
          str(js["expandAll"]))

    print("\nShift-click")
    check(LABELS[8], sorted(js["toggleIn"]) == ["overlay:o1", "text:t3"], str(js["toggleIn"]))
    check(LABELS[9], js["toggleOut"] == [], str(js["toggleOut"]))
    check(LABELS[10], len(js["groupIn"]) == 4, str(js["groupIn"]))
    check(LABELS[11], js["groupOut"] == [], str(js["groupOut"]))
    check(LABELS[12], js["groupPartly"] == 4, str(js["groupPartly"]))

    print("\nHousekeeping")
    check(LABELS[13], js["unique"] == ["text:t1", "audio:t1"], str(js["unique"]))
    check(LABELS[14], js["has"] == [True, False], str(js["has"]))
    check(LABELS[15],
          js["setSize"] == 4 and js["counts"] == {"text": 2, "shape": 1, "audio": 1},
          str(js["counts"]))
    check(LABELS[16],
          js["label"] == "2 text clips, 1 shape and 1 audio clip"
          and js["labelOne"] == "1 audio clip"
          and js["labelNone"] == "nothing",
          f"({js['label']!r} / {js['labelOne']!r} / {js['labelNone']!r})")
    check(LABELS[17], js["groupOf"] == ["gA", "", ""], str(js["groupOf"]))

    print("\nThe rubber band")
    check(LABELS[18], js["touching"] is True)
    check(LABELS[19], js["covering"] is True)
    check(LABELS[20], js["pastIt"] is False)
    check(LABELS[21], js["edge"] is False)
    check(LABELS[22], js["wrongLane"] is False)
    check(LABELS[23],
          js["backwards"] == {"left": 100, "top": 10, "right": 300, "bottom": 90},
          str(js["backwards"]))
    check(LABELS[24], js["slop"] == [False, True, True], str(js["slop"]))


# ---------------------------------------------------------------------------
# `group_id` has to SURVIVE A SAVE, or a group unties itself on reload
# ---------------------------------------------------------------------------
print("\nA group is saved with the clips — server/schemas.py")

CLIPS = {
    "a text clip": (AnimaticTextClip, {"id": "t1", "text": "hi"}),
    "a shape": (AnimaticShape, {"id": "s1"}),
    "an overlay": (AnimaticOverlay, {"id": "o1", "upload_id": "u1"}),
    "an audio clip": (AnimaticAudio, {"id": "a1", "upload_id": "u1"}),
}
for name, (model, fields) in CLIPS.items():
    kept = model(**fields, group_id="gA")
    check(f"{name} keeps its group through a round trip",
          model(**kept.model_dump()).group_id == "gA")
    check(f"{name} saved before groups existed reads as ungrouped",
          model(**fields).group_id == "")

# ⚠ The renderer must not know about groups. Grouping two captions is an EDITING
# convenience; if it changed a single pixel of the export, "tidy up the timeline"
# would silently be "change the film".
import animatic  # noqa: E402  — imported here so a missing Pillow only fails this

from PIL import Image  # noqa: E402

plain = Image.new("RGB", (320, 180), (60, 60, 60))
grouped = Image.new("RGB", (320, 180), (60, 60, 60))
CAPTION = {
    "id": "t1", "text": "Grouped or not", "start_ms": 0, "duration_ms": 1000,
    "position": "bottom", "color": "#ffffff", "backdrop": "scrim", "opacity": 1.0,
}
animatic.draw_texts(plain, [CAPTION])
animatic.draw_texts(grouped, [{**CAPTION, "group_id": "gA"}])
check("grouping a caption changes nothing about the picture it draws",
      plain.tobytes() == grouped.tobytes())


print()
if skipped:
    print(f"{len(skipped)} check(s) SKIPPED — node is not on PATH, so the")
    print("selection model was not exercised at all. That is a gap, not a pass.")
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
if skipped:
    sys.exit(2)
print("The selection list, the groups and the rubber band all hold.")
