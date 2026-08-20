"""WHAT A CLIP SENDS WHEN IT IS SAVED — checked against the schema it is sent to.

`frameForSave` in `client/src/animatic/frame_save.js` is a WHITELIST of the
fields a picture clip sends to the server. A field the schema gains and that
function does not mention is computed by the editor, drawn in the monitor, and
then thrown away on the way to the server — with no error anywhere, because
dropping a key is not a failure. Nothing rejects it, nothing logs it, and the
clip simply comes back different.

It has happened twice:

  · the first time it was `scale` / `x` / `y` / `opacity` / `keyframes`, so a
    clip's whole pan-and-zoom never survived a reload;
  · the second time it was `track`, `start_ms`, `effects`, `mask` and `blend` —
    which row a clip is on, where it sits, and its entire look. Every clip came
    back on track 0 with no start, so the multi-track timeline collapsed into one
    row on reload and the clips re-laid themselves end to end.

⚠ AND THE SECOND ONE WAS WORSE THAN LOSING THE DATA, because the same function
builds the dirty-check SIGNATURE. A field that is not in the saved shape is not
in the signature either, so moving a clip to another row did not make the
document look changed: the autosave never fired and Save believed there was
nothing to write. The edit was never sent at all.

So this compares the two sides directly, rather than trusting a reader to notice.
It also pins the three behaviours that a "just add the field" fix gets wrong:

  · `start_ms` STAYS NULL. Null is not 0 — it means "after the last clip on my
    track", which is how every animatic saved before tracks existed still lays
    itself out. Defaulting it to a number would nail every such clip to the head
    of its row.
  · `mask` IS OMITTED WHEN THERE IS NONE, not sent as null. `AnimaticMask` is not
    optional, so an explicit null fails validation on the majority of clips.
  · WHAT COMES OUT VALIDATES. The point of sending a field is that the server
    accepts it, so the result is fed through `AnimaticFrame` itself.

The other half of the same report — "when i see again my video picker layer not
show" — was an EMPTY row rather than a clip: a picture track used to be proved
only by the clips on it, so a row you had added and not filled yet was view state
and could not survive a reload. A row is an `AnimaticLayer` with `kind: "video"`
and a track number now, so the last section here pins that record: an empty row
is a row the document HAS.

    python tests/frame_save_fields_check.py

Needs `node`, which the client build already requires. Without it the schema
comparison is reported as SKIPPED, which is a gap rather than a pass.
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

from server.schemas import AnimaticFrame, AnimaticLayer

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


# Fields the SERVER owns: it fills them on read and ignores them on write, so the
# client sending one back would write a stale value into the saved document.
#
# ⚠ ADDING TO THIS LIST IS HOW YOU SILENCE THIS CHECK, so say why here. "The
# editor doesn't use it" is not a reason — an unused field costs one line and
# survives; a dropped field looks like data loss to whoever hits it.
SERVER_OWNED = {
    "url": "the server resolves it per request; a saved path goes stale",
}

# A clip with EVERY field set to something recognisable, so a field that is
# dropped, renamed or coerced shows up as a difference rather than as a default
# that happens to match.
FULL_CLIP = {
    "id": "f1",
    "src": {"kind": "video", "upload_id": "u1", "storyboard_id": "b1", "index": 3},
    "duration_ms": 4321,
    "label": "Shot 7",
    "kind": "video",
    "track": 2,
    "start_ms": 45_000,
    "scale": 1.25,
    "x": 0.4,
    "y": 0.6,
    "opacity": 0.75,
    "keyframes": {"scale": [{"t": 0, "v": 1.0, "ease": "linear"}]},
    "in_ms": 500,
    "out_ms": 8_000,
    "speed": 1.5,
    "color": "#123456",
    "effects": [{"id": "fx1", "kind": "exposure", "params": {"stops": 0.5}}],
    "mask": {"kind": "ellipse", "x": 0.5, "y": 0.5, "w": 0.4, "h": 0.4, "feather": 0.2},
    "blend": "screen",
    # Not part of the schema — the editor holds it to draw the thumbnail.
    "url": "/animatics/a1/media/u1",
}

# The compatibility case: an animatic saved before tracks existed. No `track`, no
# `start_ms`, no look at all — and `start_ms` must come out NULL, not 0.
OLD_CLIP = {
    "id": "f2",
    "src": {"kind": "upload", "upload_id": "u2"},
    "duration_ms": 2000,
}

HARNESS = """
import { frameForSave } from "%(mod)s";
const clips = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
  full: frameForSave(clips.full),
  old: frameForSave(clips.old),
  // Key ORDER matters for nothing except the signature's stability, but the key
  // SET is the whole point of this test — reported separately so a failure says
  // which side is missing what.
  fullKeys: Object.keys(frameForSave(clips.full)),
  oldKeys: Object.keys(frameForSave(clips.old)),
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="framesave_")
    try:
        src = HARNESS % {
            "mod": (ROOT / "client/src/animatic/frame_save.js").as_uri()
        }
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness, json.dumps({"full": FULL_CLIP, "old": OLD_CLIP})],
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


print("What the client sends is what the schema asks for")
saved = run_node()

schema_fields = set(AnimaticFrame.model_fields)

if saved is None:
    skip("every schema field is sent, or is explicitly server-owned", "node not available")
    skip("a clip saved before tracks keeps a NULL start, not 0", "node not available")
    skip("a clip with no mask omits the key rather than sending null", "node not available")
    skip("what comes out validates as an AnimaticFrame", "node not available")
    skip("nothing is sent that the schema has no field for", "node not available")
else:
    sent = set(saved["fullKeys"])
    missing = sorted(schema_fields - sent - set(SERVER_OWNED))
    check(
        "every schema field is sent, or is explicitly server-owned",
        not missing,
        f"AnimaticFrame has {missing} and frameForSave never sends them — "
        "add them there, or add them to SERVER_OWNED with a reason",
    )

    # The other direction. Pydantic ignores unknown keys, so this is not a
    # failure the server would ever report — it is dead weight in every save and
    # in every signature, and usually a typo'd field name that IS being dropped.
    extra = sorted(sent - schema_fields)
    check(
        "nothing is sent that the schema has no field for",
        not extra,
        f"frameForSave sends {extra}, which AnimaticFrame silently ignores",
    )

    check(
        "a clip saved before tracks keeps a NULL start, not 0",
        saved["old"].get("start_ms", "absent") is None,
        f"start_ms came out as {saved['old'].get('start_ms', 'absent')!r} — "
        "0 would pin it to the head of its row instead of after its neighbour",
    )
    check(
        "…and lands on the base track",
        saved["old"].get("track") == 0,
        f"track={saved['old'].get('track')!r}",
    )
    check(
        "a clip with no mask omits the key rather than sending null",
        "mask" not in saved["old"],
        f"sent mask={saved['old'].get('mask')!r}; AnimaticMask is not optional, "
        "so null fails validation on a clip that simply has no mask",
    )
    check(
        "a clip WITH a look sends all three of it",
        saved["full"].get("blend") == "screen"
        and saved["full"].get("mask", {}).get("kind") == "ellipse"
        and len(saved["full"].get("effects") or []) == 1,
        json.dumps(
            {k: saved["full"].get(k) for k in ("effects", "mask", "blend")}
        )[:300],
    )
    check(
        "the row and the position survive",
        saved["full"].get("track") == 2 and saved["full"].get("start_ms") == 45_000,
        f"track={saved['full'].get('track')!r} start_ms={saved['full'].get('start_ms')!r}",
    )
    check(
        "the url is NOT sent — the server resolves it per request",
        "url" not in saved["full"],
        f"sent url={saved['full'].get('url')!r}",
    )

    # And the whole point: the server accepts what we send, for both clips.
    for name in ("full", "old"):
        try:
            AnimaticFrame(**saved[name])
            ok, why = True, ""
        except Exception as exc:  # noqa: BLE001 — the message is the report
            ok, why = False, str(exc)[:400]
        check(f"what comes out validates as an AnimaticFrame ({name})", ok, why)


# ---------------------------------------------------------------------------
# A VIDEO ROW IS A RECORD, so an EMPTY one is part of the document.
# ---------------------------------------------------------------------------
print()
print("An empty video row survives, because it is a record and not a count")

ROW = {"id": "L1", "kind": "video", "name": "Video 2", "track": 1}
try:
    row = AnimaticLayer(**ROW)
    check(
        "a picture track saves as a layer record, with its number and its name",
        row.kind == "video" and row.track == 1 and row.name == "Video 2",
        f"{row!r}",
    )
except Exception as exc:  # noqa: BLE001 — the message is the report
    check("a picture track saves as a layer record, with its number and its name",
          False, str(exc)[:300])

# The rows of every other kind carry no track, and must not be given one by
# accident — `videoTracks` keys its map on that number, so a stray one would
# draw a picture row where a caption row was meant.
other = AnimaticLayer(id="L2", kind="text", name="Text 3")
check(
    "a row of any other kind carries no track number",
    other.track is None,
    f"track={other.track!r}",
)

# ⚠ THE CAP IS THE FRAME'S CAP. A row numbered higher than `AnimaticFrame.track`
# allows is a row no clip could ever be put on.
frame_cap = AnimaticFrame.model_fields["track"].metadata
layer_cap = AnimaticLayer.model_fields["track"].metadata
check(
    "a row cannot be numbered higher than a clip's track allows",
    str(frame_cap) == str(layer_cap),
    f"frame {frame_cap} vs layer {layer_cap} — a row no clip could sit on",
)

print()
if skipped:
    print(f"{len(skipped)} check(s) SKIPPED — node is not on PATH, so the saved")
    print("shape was never compared against the schema. That is a gap, not a pass.")
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
if skipped:
    sys.exit(2)
print("Everything the schema holds is everything the client sends,")
print("and a row exists whether or not anything is on it.")
