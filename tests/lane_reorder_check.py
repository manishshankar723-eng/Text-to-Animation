"""ANY VISUAL ROW CAN BE DRAGGED ANYWHERE IN THE STACK. This checks the maths.

The report, twice — the second one correcting what the first version shipped:

    "i want move layer up - down in timline only those layer: Text, shapes,
     Image, Video, Story..images, and Story..video   audio and Caption not move
     okay"
    "i check shapes layer move only other shapes layer, text layer only move
     other texts layer, and these three move each other video, Story..iamge,
     Story..video … i want these all layer move up down each other … because i
     want video layer move up Image and shapes and shapes down video"

The first build could only restack a row among its OWN KIND, because what drew
over what was decided in three places at once: a picture clip's `track` NUMBER
ordered the picture rows, and a sequence hard-coded in `sceneAt`, in
`ProgramCanvas` and in `render_frame` ordered the four kinds against each other.
There is ONE z-scale now — `laneRank` and its Python twin `lane_rank` — and this
file is what holds the two sides of it together.

---------------------------------------------------------------------------
⚠ WHY THIS IS A PYTHON TEST OF A JAVASCRIPT FILE
---------------------------------------------------------------------------
A restack rewrites the compositing order of a finished film, and it has no
visible symptom until two things overlap in space AND in time — so "it looked
right in the browser" proves very little and a screenshot proves less. Worse, the
monitor and the exporter reach the order by different code, so the failure that
matters most is the one you cannot see at all: a preview that disagrees with the
MP4. The maths therefore lives in `client/src/animatic/lane_order.js` as pure
functions over plain data, this file drives them through node, and every ranking
question is asked of BOTH languages and compared — the same bridge
`render_parity.py` uses for `scene.js`.

THE TWO CHECKS THAT MATTER MOST, if you are reading this to decide what not to
break:

  · THE MIGRATION. With no saved order at all — every animatic in existence
    before this feature — the ranks must reproduce the old hard-coded sequence
    exactly: pictures by track, then shapes, then overlay pictures, then text,
    each group in its own array order. That is what lets the whole change ship
    without a migration pass, and it is asserted rather than trusted.
  · THE PARITY. `sceneAt(...).layers` and `scene_at(...)["layers"]` must be the
    same list of the same clips in the same order, for a restacked project as
    well as a virgin one.

    python tests/lane_reorder_check.py

Needs `node` on PATH (the same one `npm run build` uses). No browser, no backend.
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

from animatic_render import bottom_picture_track, lane_rank, layer_runs, scene_at
from server.schemas import AnimaticSettings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANE_ORDER_JS = os.path.join(ROOT, "client", "src", "animatic", "lane_order.js")
SCENE_JS = os.path.join(ROOT, "client", "src", "animatic", "scene.js")
EDITOR_JSX = os.path.join(ROOT, "client", "src", "components", "AnimaticEditor.jsx")
TIMELINE_JSX = os.path.join(ROOT, "client", "src", "components", "Timeline.jsx")
PROGRAM_JSX = os.path.join(ROOT, "client", "src", "components", "ProgramCanvas.jsx")

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# The fixture — THE STACK FROM THE REPORT, row for row
# ---------------------------------------------------------------------------
# ⚠ IT IS THE USER'S OWN TIMELINE, because the shape of it is what the feature is
# about: one text row, one shape row, one image row, FOUR picture rows on tracks
# 3/2/1/0, an audio row, and the captions row on top. A fixture with two rows of
# one kind would pass while the interesting case — a kind whose only member is the
# DEFAULT row, which is what "Text" and "Shapes" are here — went unchecked. Under
# the old build those rows could not be dragged at all.
LANES = [
    {"key": "captions", "kind": "text", "layerId": "captions", "name": "Captions"},
    {"key": "text:", "kind": "text", "layerId": "", "name": "Text"},
    {"key": "shape:", "kind": "shape", "layerId": "", "name": "Shapes"},
    {"key": "image:", "kind": "image", "layerId": "", "name": "Images"},
    {"key": "frames:3", "kind": "frames", "track": 3, "name": "Video 2"},
    {"key": "frames:2", "kind": "frames", "track": 2, "name": "Story..Video"},
    {"key": "frames:1", "kind": "frames", "track": 1, "name": "Story..Image"},
    {"key": "frames:0", "kind": "frames", "track": 0, "name": "Video"},
    {"key": "vo.wav", "kind": "audio", "layerId": "", "name": "Voiceover.wav"},
]

# Every token the ranker will ever be asked about, plus two it should not know:
# an audio row (which has no token at all) and a kind from some future client.
TOKENS = [
    "frames:0", "frames:1", "frames:2", "frames:3", "frames:15",
    "shape:", "shape:s2", "image:", "image:i2", "text:", "text:t2", "text:captions",
    "sparkle:x1", "",
]

# Three orders to rank those tokens under: none at all (the migration), the
# user's own restack ("video layer move up Image and shapes"), and one that names
# only half the stack (a row added after a reorder).
ORDERS = {
    "empty": [],
    "restacked": [
        "frames:0",   # Video, dragged to the TOP of the movable stack
        "text:",
        "image:",
        "shape:s2",
        "frames:3",
        "frames:2",
        "frames:1",
        "shape:",
        "image:i2",
        "text:t2",
    ],
    "partial": ["image:", "frames:0"],
}

# A one-moment project where every overlay kind has two rows alive at once, in a
# deliberately un-sorted-looking order ("z" before "a"), so a renderer that sorted
# by id or by start time could not pass by accident.
#
# ⚠ AN ORPHAN IS IN HERE ON PURPOSE (`layer_id: "gone"`). A lane record can go
# missing while its clips remain — the captions row's safety net in `lanes` exists
# for exactly that — and those clips still draw.
PROJECT = {
    "frames": [
        {"id": "f0", "kind": "color", "color": "#101010", "duration_ms": 2000, "track": 0},
        {"id": "f1", "kind": "color", "color": "#202020", "duration_ms": 2000, "track": 1},
        {"id": "f3", "kind": "color", "color": "#303030", "duration_ms": 2000, "track": 3},
    ],
    "texts": [
        {"id": "z", "layer_id": "t2", "text": "two", "start_ms": 0, "duration_ms": 2000},
        {"id": "a", "layer_id": "", "text": "one", "start_ms": 0, "duration_ms": 2000},
        {"id": "m", "layer_id": "captions", "text": "cap", "start_ms": 0, "duration_ms": 2000},
        {"id": "o", "layer_id": "gone", "text": "orphan", "start_ms": 0, "duration_ms": 2000},
    ],
    "shapes": [
        {"id": "sz", "layer_id": "s2", "kind": "rect", "start_ms": 0, "duration_ms": 2000},
        {"id": "sa", "layer_id": "", "kind": "ellipse", "start_ms": 0, "duration_ms": 2000},
    ],
    "overlays": [
        {"id": "oz", "layer_id": "i2", "upload_id": "u1", "start_ms": 0, "duration_ms": 2000},
        {"id": "oa", "layer_id": "", "upload_id": "u2", "start_ms": 0, "duration_ms": 2000},
    ],
    "transitions": [],
    "settings": {"aspect_ratio": "16:9", "fps": 24, "background": "#000000"},
}

HARNESS = """
import {
  MOVABLE_LANE_KINDS, bottomPictureTrack, clipLaneToken, laneMovable, laneRank,
  laneTokenFor, moveInList, restack, seatLane, stackKey, unseatLane,
} from %(lane_order)s;
import { layerRuns, sceneAt } from %(scene)s;

const { lanes, tokens, orders, project } = JSON.parse(process.argv[2]);

// The scene's draw list, flattened to something a human can read in a diff:
// "picture:f0" bottom first. The INDEX is resolved to the clip it names, because
// an index that lines up by accident is exactly the false pass this is guarding.
const stackOf = (scene) =>
  (scene.layers || []).map((l) => {
    if (l.kind === "picture") return `picture:${(scene.pictures[l.index].frame || {}).id}`;
    const list = { shape: scene.shapes, overlay: scene.overlays, text: scene.texts }[l.kind];
    return `${l.kind}:${list[l.index].id}`;
  });

const sceneFor = (order) =>
  sceneAt({ ...project, settings: { ...project.settings, lane_order: order } }, 500, null);

process.stdout.write(JSON.stringify({
  movableKinds: MOVABLE_LANE_KINDS,
  movable: lanes.map((l) => ({ key: l.key, movable: laneMovable(l) })),
  tokens: lanes.map((l) => laneTokenFor(l.kind, l.layerId, l.track)),

  // The rank of every token under every order — the numbers both languages have
  // to agree on, one by one.
  ranks: Object.fromEntries(
    Object.entries(orders).map(([name, order]) => [
      name, tokens.map((t) => laneRank(t, order)),
    ])
  ),

  // --- the gesture ------------------------------------------------------
  moveDown: moveInList(["a", "b", "c", "d"], 0, 2),
  moveUp: moveInList(["a", "b", "c", "d"], 3, 1),
  moveNowhere: moveInList(["a", "b", "c"], 1, 1),
  moveOffEnd: moveInList(["a", "b", "c"], 0, 9),
  // "video layer move up Image and shapes": the base picture row dropped on the
  // Images row's place, across two kind boundaries.
  restacked: restack(
    lanes.filter(laneMovable).map((l) => laneTokenFor(l.kind, l.layerId, l.track)),
    "frames:0", "image:"
  ),

  // --- a row created AFTER a restack ------------------------------------
  seatText: seatLane(orders.restacked, "text:t9"),
  seatShape: seatLane(orders.restacked, "shape:s9"),
  seatTrack: seatLane(orders.restacked, "frames:2"),
  seatFirstOfKind: seatLane(["text:", "shape:"], "frames:0"),
  seatVirgin: seatLane([], "text:t9"),
  seatTwice: seatLane(orders.restacked, "frames:0"),
  unseat: unseatLane(orders.restacked, "image:"),
  unseatMissing: unseatLane(orders.restacked, "text:nope"),

  // --- the draw order ---------------------------------------------------
  stacks: Object.fromEntries(
    Object.entries(orders).map(([name, order]) => [name, stackOf(sceneFor(order))])
  ),
  runs: Object.fromEntries(
    Object.entries(orders).map(([name, order]) => [
      name, layerRuns(sceneFor(order).layers).map((r) => `${r.kind}x${r.indices.length}`),
    ])
  ),
  stackKeys: Object.fromEntries(
    Object.entries(orders).map(([name, order]) => [name, stackKey(order)])
  ),
  sceneKeys: Object.keys(sceneFor([])).sort(),

  // The four lists must still come back in ARRAY order — `layers` points into
  // them by index, so a renderer that sorted one would silently mis-address it.
  texts: (sceneFor([]).texts || []).map((c) => c.id),
  shapes: (sceneFor([]).shapes || []).map((s) => s.id),
  overlays: (sceneFor([]).overlays || []).map((o) => o.id),
  clipTokens: [
    clipLaneToken("picture", { track: 2 }),
    clipLaneToken("overlay", { layer_id: "i2" }),
    clipLaneToken("text", { layer_id: "" }),
  ],

  // --- which picture track is the BOTTOM of the stack --------------------
  // ⚠ THE BUG: "when i uper layer off layer hide then see my video layer not
  // view in program panel". A hidden picture-track clip is blanked (kept, as an
  // opaque colour card) if it is on the BOTTOM track and DROPPED otherwise — and
  // "bottom" used to mean "track 0", hard-coded, which stopped being true the
  // moment track 0 could be dragged above another row.
  bottomTracks: Object.fromEntries(
    Object.entries(orders).map(([name, order]) => [
      name, bottomPictureTrack([0, 1, 2, 3], order),
    ])
  ),
  bottomTrackEmpty: bottomPictureTrack([], []),
  bottomTrackOne: bottomPictureTrack([7], []),
}));
"""


def run_node() -> dict:
    if not shutil.which("node"):
        print("  node is not on PATH — cannot drive lane_order.js.")
        print("  This test is the only thing checking the restack maths and the")
        print("  preview/export parity of it; a skip here is a real gap, not a pass.")
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="laneorder_")
    try:
        path = os.path.join(tmp, "harness.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS
                % {
                    "lane_order": json.dumps(Path(LANE_ORDER_JS).resolve().as_uri()),
                    "scene": json.dumps(Path(SCENE_JS).resolve().as_uri()),
                }
            )
        payload = json.dumps(
            {"lanes": LANES, "tokens": TOKENS, "orders": ORDERS, "project": PROJECT}
        )
        proc = subprocess.run(
            ["node", path, payload],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:2000])
            print("  lane_order.js could not be evaluated (see above).")
            sys.exit(1)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def py_stack(order: list[str]) -> list[str]:
    """The Python side's draw list, in the same readable form the harness makes."""
    project = {**PROJECT, "settings": {**PROJECT["settings"], "lane_order": order}}
    scene = scene_at(project, 500, None)
    out = []
    for item in scene["layers"]:
        if item["kind"] == "picture":
            out.append(f"picture:{(scene['pictures'][item['index']]['frame'] or {}).get('id')}")
            continue
        lists = {
            "shape": scene["shapes"],
            "overlay": scene["overlays"],
            "text": scene["texts"],
        }
        out.append(f"{item['kind']}:{lists[item['kind']][item['index']]['id']}")
    return out


js = run_node()

print("\nWHICH ROWS MOVE — the user's list, and only the user's list")
check("the four movable kinds are text, shape, image and the picture rows",
      sorted(js["movableKinds"]) == ["frames", "image", "shape", "text"],
      js["movableKinds"])
movable = {m["key"]: m["movable"] for m in js["movable"]}
check("Text, Shapes and Images move — even as the ONLY row of their kind",
      movable["text:"] and movable["shape:"] and movable["image:"], movable)
check("every picture row moves, board rows included",
      all(movable[f"frames:{n}"] for n in (0, 1, 2, 3)), movable)
check("the CAPTIONS row does not move ('Caption not move okay')",
      not movable["captions"], movable["captions"])
check("an AUDIO row does not move — tracks are mixed, not stacked",
      not movable["vo.wav"], movable["vo.wav"])
check("a lane's token is the same string the eye and the padlock use",
      js["tokens"][:5] == ["text:captions", "text:", "shape:", "image:", "frames:3"],
      js["tokens"])
check("a clip's row is read off `track` for a picture and `layer_id` for the rest",
      js["clipTokens"] == ["frames:2", "image:i2", "text:"], js["clipTokens"])

print("\nTHE RANK — one scale, and both languages on it")
for name, order in ORDERS.items():
    theirs = js["ranks"][name]
    ours = [lane_rank(t, order) for t in TOKENS]
    check(f"JS and Python rank every token identically ({name} order)",
          theirs == ours,
          next((f"{t}: js={a} py={b}" for t, a, b in zip(TOKENS, theirs, ours) if a != b), ""))

empty = dict(zip(TOKENS, js["ranks"]["empty"]))
check("with NO saved order the picture rows rank by their track number",
      empty["frames:0"] < empty["frames:1"] < empty["frames:2"] < empty["frames:3"],
      empty)
check("…and every picture row still ranks under every shape row",
      empty["frames:15"] < empty["shape:"], empty)
check("…and the old sequence holds: shapes < overlays < text",
      empty["shape:"] < empty["image:"] < empty["text:"], empty)
check("an unknown kind from a newer client ranks as text — over the film, not under it",
      empty["sparkle:x1"] == empty["text:"], empty)

restacked = dict(zip(TOKENS, js["ranks"]["restacked"]))
check("after the drag, Video out-ranks Images and Shapes ('video layer move up Image')",
      restacked["frames:0"] > restacked["image:"] > restacked["shape:s2"], restacked)
check("…and the captions row still out-ranks everything, being unlisted",
      restacked["text:captions"] > max(restacked[t] for t in ORDERS["restacked"]),
      restacked)
partial = dict(zip(TOKENS, js["ranks"]["partial"]))
check("a row the saved order has never heard of ranks ABOVE every row it names",
      partial["text:t2"] > partial["image:"] and partial["shape:"] > partial["frames:0"],
      partial)

print("\nTHE GESTURE — insert at the target, never swap with it")
check("dragged DOWN, it lands after the rows it passed",
      js["moveDown"] == ["b", "c", "a", "d"], js["moveDown"])
check("dragged UP, it lands before them",
      js["moveUp"] == ["a", "d", "b", "c"], js["moveUp"])
check("a drop on itself changes nothing", js["moveNowhere"] == ["a", "b", "c"])
check("a drop off the end changes nothing", js["moveOffEnd"] == ["a", "b", "c"])
check("a restack writes the WHOLE stack, not the two rows that moved",
      len(js["restacked"]) == 7, js["restacked"])
check("Video is dropped exactly where Images was, and Images moves down one",
      js["restacked"][:4] == ["text:", "shape:", "frames:0", "image:"],
      js["restacked"])
check("the captions row is never written into the saved order",
      "text:captions" not in js["restacked"], js["restacked"])
check("nor is the audio row", not any(t.startswith("audio") for t in js["restacked"]))

print("\nA ROW ADDED AFTER A RESTACK — it sits with its own kind, not on top")
# ⚠ THE FALLBACK PUTS AN UNLISTED ROW ON TOP OF EVERYTHING, which is the right
# rule for a row nobody placed (visible beats hidden) and the wrong behaviour for
# the ＋ Add layer button: adding a picture row would drop it over the film.
order = ORDERS["restacked"]
# ⚠ UNDER THE *LAST* ROW OF ITS KIND, not next to the first one. The overlay
# kinds all tie on derived rank, so there is no basis for putting a new one
# between two existing rows — and "under the ones you already have" is where the
# derived order always drew an added row relative to the default one.
def seated_under_last(listing, token, kind):
    kin = [t for t in listing if t.startswith(kind) and t != token]
    return listing.index(token) == listing.index(kin[-1]) + 1

check("a new TEXT row lands under the LAST text row, where 'Text 2' always was",
      seated_under_last(js["seatText"], "text:t9", "text:"), js["seatText"])
check("a new SHAPE row lands under the last shape row",
      seated_under_last(js["seatShape"], "shape:s9", "shape:"), js["seatShape"])
check("a new PICTURE row lands between the tracks either side of it, by number",
      js["seatTrack"].index("frames:3") < js["seatTrack"].index("frames:2")
      < js["seatTrack"].index("frames:1"),
      js["seatTrack"])
check("the FIRST row of its kind falls back to the derived scale — a picture "
      "row under the shapes",
      js["seatFirstOfKind"] == ["text:", "shape:", "frames:0"],
      js["seatFirstOfKind"])
check("an EMPTY order is left empty — it means 'the order that always was'",
      js["seatVirgin"] == [], js["seatVirgin"])
check("seating a row that is already listed changes nothing",
      js["seatTwice"] == order, js["seatTwice"])
check("a deleted row leaves the order, so a reused track cannot inherit its place",
      "image:" not in js["unseat"] and len(js["unseat"]) == len(order) - 1,
      js["unseat"])
check("…and unseating something that was never there is a no-op",
      js["unseatMissing"] == order, js["unseatMissing"])

print("\nWHICH PICTURE TRACK IS THE BOTTOM — the fix for a real bug")
# ⚠ THE REPORT: "when i uper layer off layer hide then see my video layer not
# view in program panel". A hidden picture-track clip is blanked to an opaque
# colour card rather than dropped ONLY on the track that is the physical bottom
# of the stack — everywhere else, blanking would paint over whatever is below
# it. That used to be hard-coded as track 0; it is asked of the rank now.
check("with NO saved order, track 0 is the bottom — same as it always was",
      js["bottomTracks"]["empty"] == 0, js["bottomTracks"]["empty"])
check("once track 0 is dragged to the top, it is NO LONGER the bottom",
      js["bottomTracks"]["restacked"] != 0, js["bottomTracks"]["restacked"])
check("…track 1 is, because it is what the drag left lowest-ranked",
      js["bottomTracks"]["restacked"] == 1, js["bottomTracks"]["restacked"])
check("a row explicitly seated under everything else is the bottom",
      js["bottomTracks"]["partial"] == 0, js["bottomTracks"]["partial"])
check("an empty track list has no bottom", js["bottomTrackEmpty"] is None)
check("a single track is its own bottom, whatever the order says",
      js["bottomTrackOne"] == 7, js["bottomTrackOne"])
py_bottoms = {
    name: bottom_picture_track([0, 1, 2, 3], order) for name, order in ORDERS.items()
}
check("JS and Python agree on the bottom track, for every order",
      js["bottomTracks"] == py_bottoms,
      f"js={js['bottomTracks']} py={py_bottoms}")
check("…and agree on the empty-list and single-track edge cases",
      bottom_picture_track([], []) is None and bottom_picture_track([7], []) == 7)

print("\nTHE DRAW ORDER — what the two renderers actually stack")
check("the scene's shape is the same on both sides",
      js["sceneKeys"] == sorted(scene_at(PROJECT, 500, None).keys()),
      js["sceneKeys"])
for name in ORDERS:
    check(f"JS and Python draw the same clips in the same order ({name} order)",
          js["stacks"][name] == py_stack(ORDERS[name]),
          f"js={js['stacks'][name]} py={py_stack(ORDERS[name])}")

# ⚠ THE MIGRATION, WRITTEN OUT IN FULL. This exact list is what the three
# renderers drew before any of this existed: every picture track bottom-first,
# then the shapes, then the overlay pictures, then the captions — each group in
# its own array order, orphan and all. If this line ever has to change, every
# animatic ever saved changes with it.
check("with NO saved order the stack is the old hard-coded sequence, clip for clip",
      js["stacks"]["empty"] == [
          "picture:f0", "picture:f1", "picture:f3",
          "shape:sz", "shape:sa",
          "overlay:oz", "overlay:oa",
          "text:z", "text:a", "text:m", "text:o",
      ], js["stacks"]["empty"])
check("…and the four lists still come back in ARRAY order, which `layers` indexes",
      js["texts"] == ["z", "a", "m", "o"] and js["shapes"] == ["sz", "sa"]
      and js["overlays"] == ["oz", "oa"],
      [js["texts"], js["shapes"], js["overlays"]])
check("the base picture row really does end up over the overlays once dragged",
      js["stacks"]["restacked"].index("picture:f0")
      > js["stacks"]["restacked"].index("overlay:oa"),
      js["stacks"]["restacked"])
check("…and a text row dragged under it draws under it",
      js["stacks"]["restacked"].index("text:a")
      < js["stacks"]["restacked"].index("picture:f0"),
      js["stacks"]["restacked"])

print("\nRUNS — adjacent rows of one kind are drawn in ONE call")
for name in ORDERS:
    ours = [f"{r['kind']}x{len(r['indices'])}" for r in layer_runs(
        scene_at({**PROJECT, "settings": {**PROJECT["settings"], "lane_order": ORDERS[name]}},
                 500, None)["layers"])]
    check(f"JS and Python fold the same runs ({name} order)",
          js["runs"][name] == ours, f"js={js['runs'][name]} py={ours}")
check("un-restacked, the four captions are ONE text run — so they still stack in a zone",
      js["runs"]["empty"] == ["picturex1", "picturex1", "picturex1", "shapex2",
                              "overlayx2", "textx4"],
      js["runs"]["empty"])
check("a picture is always its own run, however many are adjacent",
      all(r == "picturex1" for r in js["runs"]["empty"] if r.startswith("picture")),
      js["runs"]["empty"])

print("\nTHE RENDER CACHE — a restack must not come back as the last export")
check("an un-restacked project has an EMPTY stack key, so it signs as it always did",
      js["stackKeys"]["empty"] == "", js["stackKeys"]["empty"])
check("a restacked one does not", js["stackKeys"]["restacked"] != "")
check("JS and Python agree on the key",
      all(js["stackKeys"][n] == ("|".join(ORDERS[n]) if ORDERS[n] else "")
          for n in ORDERS),
      js["stackKeys"])
sig_plain = scene_at(PROJECT, 500, None)
sig_moved = scene_at(
    {**PROJECT, "settings": {**PROJECT["settings"], "lane_order": ORDERS["restacked"]}},
    500, None,
)
from animatic_render import scene_signature  # noqa: E402  — read after the scenes above

check("the same moment signs DIFFERENTLY once the rows are restacked",
      scene_signature(sig_plain) != scene_signature(sig_moved))
check("…and the un-restacked signature has no stack part in it at all",
      "|z" not in scene_signature(sig_plain), scene_signature(sig_plain)[:120])

print("\nTHE FIELD — the server keeps the order, and defaults to none")
settings = AnimaticSettings()
check("`lane_order` defaults to empty, which means the order that always was",
      settings.lane_order == [], settings.lane_order)
check("a saved order round-trips through the model",
      AnimaticSettings(lane_order=ORDERS["restacked"]).lane_order == ORDERS["restacked"])

print("\nTHE WIRING — the gesture, the gutter and the monitor")
editor = read(EDITOR_JSX)
timeline = read(TIMELINE_JSX)
program = read(PROGRAM_JSX)
check("the editor ranks the gutter with the same function the renderers rank with",
      "laneRank(laneToken(lane), settings.lane_order)" in editor)
check("a restack writes `lane_order` and nothing else",
      "lane_order: order" in editor and "remapPictureTracks" not in editor,
      "still renumbering clips" if "remapPictureTracks" in editor else "")
check("no clip list is re-sorted by a drag any more",
      "sortClipsByLane" not in editor)
check("a locked row is neither moved nor moved past, by name",
      "is locked — unlock it to restack" in editor)
check("every row the editor CREATES takes its seat in the saved order",
      editor.count("seatNewLane(") + editor.count("seatNewLaneRef.current?.(") >= 5,
      f"{editor.count('seatNewLane(')} direct + "
      f"{editor.count('seatNewLaneRef.current?.(')} via the ref")
check("…and a row it DELETES gives its seat up",
      "unseatOldLane(layerTokenOf(layer))" in editor)
check("the timeline no longer refuses a drop for being another kind of row",
      "to.group === lane.group" not in timeline)
check("a row that cannot move is never picked up",
      "!lane.movable || lane.locked" in timeline)
check("the monitor walks the scene's order rather than one of its own",
      "for (const run of band.runs)" in program
      and "for (const shape of scene.shapes" not in program)
check("the monitor bands the picture at each caption row",
      'kind: "text", indices: run.indices' in program)
# ⚠ THE TWO THINGS THAT MADE THE BAND SPLIT ACTUALLY WORK, both found by pixels
# rather than by reading — see `tests/editor_lane_restack_check.py`.
shader = read(os.path.join(ROOT, "client", "src", "animatic", "gl", "shaders", "layer.js"))
check("an upper band can be SEEN THROUGH — the compositor carries real alpha",
      "gl_FragColor = vec4(co, ao);" in shader
      and "gl_FragColor = texture2D(uTexture, vUV);" in shader,
      "a hard-coded alpha of 1.0 makes the top band an opaque black sheet")
check("…and a blend mode on it can see the band below",
      "uniform sampler2D uUnder;" in shader
      and "compositor.under(under);" in program)
check("the exporter walks it too",
      "runs = layer_runs(layers) if layers is not None else None"
      in read(os.path.join(ROOT, "animatic.py")))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for name in failures:
        print(f"  · {name}")
    sys.exit(1)
print("Every visual row can be restacked, and the monitor and the export agree on it.")
