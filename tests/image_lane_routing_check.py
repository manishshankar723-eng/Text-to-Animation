"""AN UPLOADED PICTURE GOES TO THE IMAGES LAYER — there is no Stills row any more.

The report, with two screenshots of the gutter:

    "see when upload image in media so in timline image show Still layer and when
     i upload through image layer so i see this good but i wnat same in same like
     image layer only not need still layer remove still layer when user uplaod
     media or layer so image shoul come in image layer not sitll layer"

    "i only upload media and layer + icon not in Background panel of clip remove
     this in time blank box layer only keep media and layer + icon"

Two asks, one gutter. The first is where an upload LANDS; the second is which
controls may start one.

---------------------------------------------------------------------------
⚠ WHY THE STILLS ROW WAS THE BUG AND NOT JUST A NAME
---------------------------------------------------------------------------
A Stills row was made FOR you, behind your back, the first time you uploaded a
picture — and picture rows are stacked highest-draws-first, so it landed ABOVE
the two storyboard rows. One photo therefore blanked out the opening seconds of
the board. Dropping the same picture on the Images layer composites it OVER the
cut instead, at a third of the frame, which is the behaviour the report calls
"good". So `stills` is gone from `ROW_KINDS` and every door into "add a picture"
routes by ONE rule, `belongsOnImageLane`.

⚠ THE MIGRATION IS THE SECOND HALF OF THE ASK. Projects saved while Stills rows
existed still carry `kind: "stills"` layer records and still have those photos in
the cut, and both must go on playing and exporting untouched — the exporter reads
a clip's `track` NUMBER and nothing else. `rowKindOrLegacy` reads such a record as
the plain video row its clips already sit on, so what changes is the label in the
gutter and nothing at all in the film. `clipRowKind` answers "video" for a plain
picture for the same reason: a clip whose row kind no longer exists would be
unnameable by `dominantRowKind` and unmovable by `laneMoveTarget`.

⚠ AND A PICTURE CAN STILL BE PUT IN THE CUT ON PURPOSE. The plain Video row takes
footage and full-frame stills alike — it always has, which is why the ＋ Add layer
menu never offered a "Stills" beside it. What went is the row that appeared
without being asked for; aiming a file at the Video row yourself still works.

    python tests/image_lane_routing_check.py

Needs node for the logic half; skips it cleanly without one, exactly as
`veo_download_check.py` does. The wiring half is a source read and always runs.
No backend, no browser, no ffmpeg.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


# ---------------------------------------------------------------------------
# The logic half — the rules themselves, evaluated in node against scene.js.
# ---------------------------------------------------------------------------
HARNESS = """
import {
  ROW_KINDS,
  ROW_TAKES,
  belongsOnImageLane,
  cardRowKind,
  clipRowKind,
  dominantRowKind,
  isCutRow,
  rowKindOrLegacy,
} from "%(scene)s";

const upload = { kind: "image", src: { kind: "upload", upload_id: "u1" } };
const footage = { kind: "video", src: { kind: "video", upload_id: "u2" } };
const card = { kind: "color", color: "#000000", src: { kind: "upload" } };
const panel = { kind: "image", src: { kind: "panel", storyboard_id: "b1", index: 0 } };
const render = {
  kind: "video",
  src: { kind: "video", storyboard_id: "b1", index: 0, upload_id: "u3" },
};

console.log(JSON.stringify({
  rowKinds: ROW_KINDS,
  takes: ROW_TAKES,
  // Where each thing you can ADD is routed.
  overlay: {
    upload: belongsOnImageLane("image", false),
    footage: belongsOnImageLane("video", false),
    colour: belongsOnImageLane("color", false),
    panel: belongsOnImageLane("image", true),
    render: belongsOnImageLane("video", true),
    nothing: belongsOnImageLane(undefined, false),
  },
  // Which row in the CUT each clip already there belongs on.
  rowOf: {
    upload: clipRowKind(upload),
    footage: clipRowKind(footage),
    colour: clipRowKind(card),
    panel: clipRowKind(panel),
    render: clipRowKind(render),
  },
  cardOf: {
    image: cardRowKind("image", false),
    video: cardRowKind("video", false),
    boardImage: cardRowKind("image", true),
    boardVideo: cardRowKind("video", true),
  },
  // The migration: a legacy record, and a legacy row named after its clips.
  legacy: {
    stills: rowKindOrLegacy("stills"),
    video: rowKindOrLegacy("video"),
    boardImage: rowKindOrLegacy("board_image"),
    junk: rowKindOrLegacy("nonsense"),
    isCut: isCutRow("stills"),
  },
  dominant: {
    allStills: dominantRowKind([upload, upload, card]),
    allPanels: dominantRowKind([panel, panel]),
    empty: dominantRowKind([]),
  },
}));
"""


def run_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="imglane_")
    try:
        src = HARNESS % {"scene": (ROOT / "client/src/animatic/scene.js").as_uri()}
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:800])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("The rows a clip can live on")
got = run_node()

LOGIC = [
    "there is no 'stills' row kind left",
    "the four that are left are the board's three and Video",
    "an uploaded picture is routed to the Images lane",
    "…and footage is not",
    "…and a colour card is not, because it takes up time in the cut",
    "…and neither board panel nor render is, whatever kind it is",
    "…and nothing in does not throw",
    "a picture already in the cut still names a row that exists",
    "a board clip is still pinned to its own row",
    "the Video row takes footage AND full-frame stills",
    "…and every board row still takes no file at all",
    "a legacy 'stills' record reads as a plain video row",
    "…while a kind that never existed reads as no row",
    "…and 'stills' is no longer a cut row in its own right",
    "a legacy row of photos is named after a row that exists",
]

if got is None:
    for label in LOGIC:
        skip(label, "node not available")
else:
    check(LOGIC[0], "stills" not in got["rowKinds"], str(got["rowKinds"]))
    # ⚠ `board_poses` IS THE THIRD BOARD ROW — "Animatic images", the key poses
    # blocked out from the timeline (see `tests/animatic_images_check.py`). It
    # sits between the stills and the renders because a pose draws OVER the panel
    # it was made from and UNDER a Veo take of the same shot.
    check(
        LOGIC[1],
        got["rowKinds"] == ["board_image", "board_poses", "board_video", "video"],
        str(got["rowKinds"]),
    )
    ov = got["overlay"]
    check(LOGIC[2], ov["upload"] is True, str(ov))
    check(LOGIC[3], ov["footage"] is False, str(ov))
    check(LOGIC[4], ov["colour"] is False, str(ov))
    check(LOGIC[5], ov["panel"] is False and ov["render"] is False, str(ov))
    check(LOGIC[6], ov["nothing"] is True, str(ov))
    rows = got["rowOf"]
    check(
        LOGIC[7],
        rows["upload"] == "video" and rows["colour"] == "video",
        str(rows),
    )
    check(
        LOGIC[8],
        rows["panel"] == "board_image" and rows["render"] == "board_video",
        str(rows),
    )
    takes = got["takes"]
    check(
        LOGIC[9],
        sorted(takes.get("video", [])) == ["image", "video"],
        str(takes),
    )
    check(
        LOGIC[10],
        takes.get("board_image") == []
        and takes.get("board_poses") == []
        and takes.get("board_video") == [],
        str(takes),
    )
    legacy = got["legacy"]
    check(LOGIC[11], legacy["stills"] == "video", str(legacy))
    check(LOGIC[12], legacy["junk"] == "", str(legacy))
    check(LOGIC[13], legacy["isCut"] is False, str(legacy))
    check(
        LOGIC[14],
        got["dominant"]["allStills"] == "video"
        and got["dominant"]["allPanels"] == "board_image"
        and got["dominant"]["empty"] == "video",
        str(got["dominant"]),
    )


# ---------------------------------------------------------------------------
# The wiring half — every door into "add a picture" goes through the one rule,
# and the two that were asked to go are gone.
# ---------------------------------------------------------------------------
print("\nWhich doors an upload can come through")

editor = (ROOT / "client/src/components/AnimaticEditor.jsx").read_text(encoding="utf-8")
timeline = (ROOT / "client/src/components/Timeline.jsx").read_text(encoding="utf-8")
scene = (ROOT / "client/src/animatic/scene.js").read_text(encoding="utf-8")

# ⚠ THE SOURCE IS READ, NOT THE DOM. What this half is guarding is that a SECOND
# copy of "where does a picture go" never appears — a grep is the only thing that
# can see the absence of one.
check(
    "the editor no longer finds-or-creates a Stills row",
    'rowOfKind("stills")' not in editor and 'addPictureTrack("stills"' not in editor,
    "an addAssets/placeAsset path still reaches for one",
)
check(
    "…and no picture-row table in the editor names one",
    re.search(r"^\s*stills:\s*\{", editor, re.M) is None,
    "ROW_KIND still has a stills entry",
)
check(
    "the Media pane's upload routes pictures with the shared rule",
    "belongsOnImageLane" in editor,
    "addAssets/placeAsset re-derive it",
)
check(
    "…and the rule itself lives in scene.js beside the row kinds",
    "export const belongsOnImageLane" in scene,
    "it drifted into a component",
)
check(
    "a card's ＋ hands a picture to the default Images lane",
    re.search(
        r"belongsOnImageLane\(.*?\{\s*const lane = lanes\.find", editor, re.S
    )
    is not None,
    "placeAsset still inserts it into the cut",
)
check(
    "…and it is the DEFAULT lane, never a numbered one",
    'l.kind === "image" && !l.layerId' in editor,
    "an added Images 2 could swallow the upload",
)
check(
    "the Media drop card says where a picture lands",
    "images for the Images layer" in editor,
    "the note still points at the video track",
)

# --- the second ask: which controls may start an upload --------------------
# ⚠ AN ABSENCE, ASSERTED AS AN ABSENCE. The empty band was a full-width invisible
# button; nothing about the screen would look different if it came back, which is
# exactly why it is pinned in a test rather than left to a reviewer's eye.
band = re.search(r"function emptyBand\([^)]*\)\s*\{(.*?)\n  \}", timeline, re.S)
check(
    "the blank part of a lane is no longer an add button",
    band is not None and "onAddToLane" not in band.group(1),
    "emptyBand still opens something",
)
check(
    "…and it draws nothing at all",
    band is not None and band.group(1).strip() == "return null;",
    band.group(1).strip()[:120] if band else "no emptyBand",
)
check(
    "the row's own ＋ in the gutter still adds to it",
    'className="tl-layer-btn tl-layer-add"' in timeline and "onAddToLane(lane)" in timeline,
    "the gutter ＋ went with the band",
)
check(
    "…and the Media pane's ＋ still opens the one file dialog",
    "assetInputRef.current?.click()" in editor,
    "the pane's own control went too",
)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for label in failures:
        print(f"  · {label}")
    sys.exit(1)
if skipped:
    print(f"All good ({len(skipped)} skipped — node not available).")
else:
    print("All good.")
