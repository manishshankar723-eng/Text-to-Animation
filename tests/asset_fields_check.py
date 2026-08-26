"""WHAT A MEDIA-LIBRARY CARD SENDS WHEN IT IS SAVED — against the schema it goes to.

`assetForSave` in `client/src/animatic/assets.js` is a WHITELIST of the fields a
library card sends to the server, and it is the same trap `frameForSave` fell into
twice: a field the schema gains and that function does not mention is computed by
the editor, shown in the pane, and then thrown away on the way to the server —
with no error anywhere, because dropping a key is not a failure.

⚠ AND IT IS WORSE HERE THAN IT WAS THERE, because the library is the thing that
is supposed to SURVIVE. The whole feature exists so that deleting a clip does not
lose its source ("i want when user delete video, storboard image, veo video, audio
and shapes in timeline after upload in media so only clip delete in timeline not
delete in media panel"). A field that silently fails to save is a card that comes
back wrong — or, for `src`, a card that comes back pointing at nothing.

So this compares the two sides directly, and pins the behaviours a "just add the
field" fix gets wrong:

  · THE FOUR KINDS ARE A CLOSED SET. `assetForSave` coerces anything else to
    "image" rather than passing it through, because `kind` decides which of two
    lists a card is placed on (a picture clip, or an audio track) and an unknown
    third answer is a card no drop can ever consume.
  · A COLOUR CARD AND AN AUDIO FILE STILL SEND A `src`. It is unused for both —
    a swatch has no file and audio carries `upload_id` directly — but the shape
    is one shape, so `AnimaticAsset(**sent)` validates for every kind.
  · `duration_ms` IS THE SOURCE'S LENGTH AND IS NEVER NEGATIVE. It is not the
    clip's hold: a 54s take trimmed to 3s on the timeline is still a 54s take in
    the library, which is what makes a clip dragged back out open at full length.
  · `url` IS NOT SENT. The server resolves it per request (`_asset_url`), so a
    saved path goes stale exactly as a frame's does.

It also checks the two pure functions the library's IDENTITY rests on, because
they are the ones with a right answer and no UI to notice a wrong one:
`assetKey` (what makes two additions the same source) and `assetOrigin` (which
section of the pane a card lands in).

    python tests/asset_fields_check.py

Needs node; skips cleanly without it, exactly as `frame_save_fields_check.py` does.
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

from server.schemas import AnimaticAsset, AnimaticOverlay, AnimaticSettings

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


# Fields the SERVER owns: filled on read, ignored on write. Sending one back would
# store a stale value.
#
# ⚠ ADDING TO THIS LIST IS HOW YOU SILENCE THIS CHECK, so say why here. "The
# editor doesn't use it" is not a reason.
SERVER_OWNED = {
    "url": "the server resolves it per request; a saved path goes stale",
}

# One card per KIND, each with every field set to something recognisable — so a
# field that is dropped, renamed or coerced shows up as a difference rather than
# as a default that happens to match.
CARDS = {
    # A board panel: referenced by (board, index), never copied.
    "panel": {
        "id": "a1",
        "kind": "image",
        "src": {"kind": "panel", "storyboard_id": "b1", "index": 3},
        "upload_id": "",
        "label": "Shot 4",
        "duration_ms": 2000,
        "color": "#000000",
        # Not part of the saved shape — the editor holds it to draw the tile.
        "url": "/animatics/a1/panel/b1/3?v=99",
    },
    # A key pose of a panel — the same board, one level finer.
    "pose": {
        "id": "a2",
        "kind": "image",
        "src": {"kind": "pose", "storyboard_id": "b1", "index": 3, "frame": 7},
        "label": "Shot 4 pose 7",
        "duration_ms": 250,
    },
    # An uploaded video. `duration_ms` is the FILE's length.
    "video": {
        "id": "a3",
        "kind": "video",
        "src": {"kind": "video", "upload_id": "u1"},
        "label": "TTBB_EP_1",
        "duration_ms": 54_420,
    },
    "upload": {
        "id": "a4",
        "kind": "image",
        "src": {"kind": "upload", "upload_id": "u2"},
        "label": "still.png",
        "duration_ms": 2000,
    },
    # A colour card: no file at all, so `src` is inert and `color` is everything.
    "color": {
        "id": "a5",
        "kind": "color",
        "src": {"kind": "upload"},
        "label": "",
        "duration_ms": 1000,
        "color": "#123456",
    },
    # A sound file. ⚠ The one kind that does NOT reach its file through `src`.
    "audio": {
        "id": "a6",
        "kind": "audio",
        "src": {"kind": "upload"},
        "upload_id": "u3",
        "label": "voiceover.mp3",
        "duration_ms": 91_000,
    },
    # The hostile cases, in one card: a kind nobody defined, and a negative length.
    "bogus": {
        "id": "a7",
        "kind": "sculpture",
        "src": {"kind": "upload", "upload_id": "u4"},
        "duration_ms": -5,
    },
}

HARNESS = """
import {
  ASSET_KINDS, assetForSave, assetKey, assetOrigin, assetUrl,
  assetFromFrame, assetFromAudio, assetFromOverlay, clipFromAsset,
  libraryFromProject, mergeAssets,
} from "%(mod)s";
const cards = JSON.parse(process.argv[2]);
const saved = {};
const keys = {};
const origin = {};
const url = {};
for (const [name, card] of Object.entries(cards)) {
  saved[name] = assetForSave(card);
  keys[name] = Object.keys(assetForSave(card));
  origin[name] = assetOrigin(card);
  url[name] = assetUrl("job1", card);
}

// A clip made from a card, for the two things that must survive the trip: the
// source, and enough length to open at.
const fromVideo = clipFromAsset(cards.video, { id: "c1", animaticId: "job1" });
const fromPanel = clipFromAsset(cards.panel, { id: "c2", animaticId: "job1", defaultMs: 2000 });
const fromColor = clipFromAsset(cards.color, { id: "c3", animaticId: "job1" });

// Round trip: a clip -> a card -> a clip. The library is only useful if what
// comes back out is the same source that went in.
const roundTrip = assetFromFrame(fromVideo, "a9");

// The backfill, from a project that predates the library. Two clips on ONE panel
// and a razored voiceover in three pieces must give TWO cards, not five.
const derived = libraryFromProject(
  {
    frames: [
      { id: "f1", kind: "image", src: { kind: "panel", storyboard_id: "b1", index: 0 }, duration_ms: 2000, label: "Shot 1" },
      { id: "f2", kind: "image", src: { kind: "panel", storyboard_id: "b1", index: 0 }, duration_ms: 500, label: "Shot 1" },
      { id: "f3", kind: "color", src: { kind: "upload" }, color: "#000000", duration_ms: 1000 },
    ],
    audioTracks: [
      { id: "t1", upload_id: "u3", filename: "vo.mp3", duration_ms: 91000 },
      { id: "t2", upload_id: "u3", filename: "vo.mp3", duration_ms: 91000 },
      { id: "t3", upload_id: "u3", filename: "vo.mp3", duration_ms: 91000 },
    ],
  },
  (() => { let n = 0; return () => `gen${++n}`; })()
);

// ---------------------------------------------------------------------------
// AN OVERLAY IS A PICTURE CLIP THE LIBRARY HAS TO RECOGNISE
// ---------------------------------------------------------------------------
// A picture on an Images lane is an `overlay`, not a `frame`. Until it carried a
// `src` the library could not match one to any card, so the ×N badge
// under-counted, the card's ✕ orphaned the picture, and "Select its clips"
// missed it. These four shapes are the whole of that fix.
//
// ⚠ THE PANEL CASE IS THE ONLY ONE THAT NEEDS THE STORED `src`: a board panel has
// no upload of its own, so `overlayFromFrame` COPIES its bytes and the overlay's
// `upload_id` is a fresh id the library has never heard of.
const panelOverlay = {
  id: "o1", upload_id: "COPY_OF_PANEL", duration_ms: 2000,
  src: { kind: "panel", storyboard_id: "b1", index: 2 },
};
// A dropped file and a generated picture share the CARD's own upload id, so they
// matched before the field existed and must still match without one.
const uploadOverlay = { id: "o2", upload_id: "u4", duration_ms: 2000 };
// ⚠ AND TWO OVERLAYS SAVED BEFORE THE FIELD EXISTED MUST STAY APART. They get the
// schema default — `kind: "panel"` with no ids — which keys as `panel::` for every
// one of them, so reading it raw would fold a whole project's overlays into one
// card. `overlaySource` reads any src with no usable ids as its own upload.
const legacyA = { id: "o3", upload_id: "uA", duration_ms: 2000,
                  src: { kind: "panel", storyboard_id: null, index: null } };
const legacyB = { id: "o4", upload_id: "uB", duration_ms: 2000,
                  src: { kind: "panel", storyboard_id: null, index: null } };

const overlayKeys = {
  panel: assetKey(assetFromOverlay(panelOverlay)),
  upload: assetKey(assetFromOverlay(uploadOverlay)),
  legacyA: assetKey(assetFromOverlay(legacyA)),
  legacyB: assetKey(assetFromOverlay(legacyB)),
};

// The backfill must see the Images lanes too — a project whose only pictures are
// overlays derived an EMPTY library and opened saying "Nothing in Media yet".
// ⚠ AND A FRAME AND AN OVERLAY OF ONE SOURCE ARE ONE CARD, keeping the frame's
// LABEL: `AnimaticOverlay` has no name field at all, so the frame's card must win.
const derivedOverlays = libraryFromProject(
  {
    frames: [
      { id: "f9", kind: "image", src: { kind: "panel", storyboard_id: "b1", index: 2 }, duration_ms: 2000, label: "Shot 3" },
    ],
    overlays: [panelOverlay, uploadOverlay],
    audioTracks: [],
  },
  (() => { let n = 0; return () => `ov${++n}`; })()
);

console.log(JSON.stringify({
  kinds: ASSET_KINDS,
  saved, keys, origin, url,
  identity: Object.fromEntries(Object.entries(cards).map(([n, c]) => [n, assetKey(c)])),
  fromVideo, fromPanel, fromColor, roundTrip,
  derived,
  overlayKeys,
  derivedOverlays,
  // Adding a card already present must be a no-op, whatever its id.
  merged: mergeAssets([cards.video], [{ ...cards.video, id: "different" }]).length,
  audioCard: assetFromAudio({ id: "t1", upload_id: "u3", filename: "vo.mp3", duration_ms: 91000 }, "a8"),
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="assetsave_")
    try:
        src = HARNESS % {"mod": (ROOT / "client/src/animatic/assets.js").as_uri()}
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness, json.dumps(CARDS)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:800])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


LABELS = [
    "every schema field is sent, or is explicitly server-owned",
    "nothing is sent that the schema has no field for",
    "every kind validates as an AnimaticAsset",
    "an unknown kind is coerced, not passed through",
    "a negative length cannot be saved",
    "the url is NOT sent — the server resolves it per request",
    "two additions of one source are ONE card",
    "a panel and one of its poses are DIFFERENT cards",
    "a still and a video sharing an upload id are different cards",
    "each kind lands in the right section of the pane",
    "a panel card is servable without a save",
    "a video card asks for a POSTER, not the MP4",
    "a colour card has no url to fetch",
    "footage dragged out opens at its natural length",
    "a still dragged out opens at the default hold",
    "a clip made from a card carries a url",
    "clip → card → clip keeps the source",
    "the backfill dedupes: 3 clips + 3 audio clips → 3 cards",
    "…and a colour card is kept, not dropped",
    # --- an overlay is a picture clip the library has to recognise ---------
    "a PANEL overlay is matched to the card it was dragged from",
    "…even though its upload is a COPY with an id of its own",
    "an uploaded/generated overlay matches on its upload id alone",
    "two overlays saved before `src` existed stay two cards",
    "the backfill sees the Images lanes, not just the sequence",
    "…and a frame and an overlay of one source are ONE card, named",
]

print("What a library card sends is what the schema asks for")
got = run_node()
schema_fields = set(AnimaticAsset.model_fields)

if got is None:
    for label in LABELS:
        skip(label, "node not available")
else:
    sent = set(got["keys"]["panel"])
    missing = sorted(schema_fields - sent - set(SERVER_OWNED))
    check(LABELS[0], not missing, f"missing: {missing}")
    check(LABELS[1], not sorted(sent - schema_fields), f"extra: {sorted(sent - schema_fields)}")

    bad = []
    for name, payload in got["saved"].items():
        try:
            AnimaticAsset(**payload)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{name}: {str(exc)[:90]}")
    check(LABELS[2], not bad, "; ".join(bad))

    check(LABELS[3], got["saved"]["bogus"]["kind"] == "image",
          f'got {got["saved"]["bogus"]["kind"]!r}, kinds are {got["kinds"]}')
    check(LABELS[4], got["saved"]["bogus"]["duration_ms"] == 0,
          str(got["saved"]["bogus"]["duration_ms"]))
    check(LABELS[5], "url" not in got["keys"]["panel"], str(got["keys"]["panel"]))

    # --- identity ---------------------------------------------------------
    ident = got["identity"]
    check(LABELS[6], got["merged"] == 1, f'{got["merged"]} cards after merging a duplicate')
    check(LABELS[7], ident["panel"] != ident["pose"], f'{ident["panel"]} vs {ident["pose"]}')
    # `img_<id>.png` and `vid_<id>.mp4` are different files under one id space.
    check(LABELS[8], ident["video"] != ident["upload"].replace("upload:u2", "upload:u1"),
          f'{ident["video"]} vs {ident["upload"]}')

    # --- which section ----------------------------------------------------
    # ⚠ A KEY POSE IS FILED APART FROM THE PANELS. It used to answer "board" with
    # them, and that was right while poses only ever arrived with the import.
    # ✨ Animatic images adds a card per DRAWING — sixteen for a four-second shot
    # — so filed together they bury the panels inside a section people keep
    # folded shut, which is how they came to look missing at all: "media panel
    # mai generted iamge nhi dikh rah ahai". See `assetOrigin`, and note that
    # "which section" and "did it come off a board" are two questions now
    # (`isBoardAsset` is the second).
    want_origin = {
        "panel": "board", "pose": "poses", "video": "video",
        "upload": "image", "color": "image", "audio": "audio",
    }
    wrong = {k: got["origin"][k] for k, v in want_origin.items() if got["origin"][k] != v}
    check(LABELS[9], not wrong, str(wrong))

    # --- urls, and the save-independence that is the point of them --------
    check(LABELS[10], got["url"]["panel"] == "/animatics/job1/panel/b1/3",
          got["url"]["panel"])
    check(LABELS[11], got["url"]["video"] == "/animatics/job1/media/u1?poster=1",
          got["url"]["video"])
    check(LABELS[12], got["url"]["color"] == "", repr(got["url"]["color"]))

    # --- what a drag out of the pane makes --------------------------------
    check(LABELS[13],
          got["fromVideo"]["duration_ms"] == 54_420 and got["fromVideo"]["out_ms"] == 54_420,
          json.dumps(got["fromVideo"])[:160])
    check(LABELS[14],
          got["fromPanel"]["duration_ms"] == 2000 and got["fromPanel"]["out_ms"] is None,
          json.dumps(got["fromPanel"])[:160])
    # ⚠ THE FIELD THAT HAS BEEN MISSED TWICE ALREADY (`newVideoClip`, then
    # `attachVeoClip`): no url means no thumbnail fetch, which means a spinner
    # that never resolves and a black monitor.
    check(LABELS[15],
          bool(got["fromVideo"].get("url")) and bool(got["fromPanel"].get("url"))
          and not got["fromColor"].get("url"),
          json.dumps({k: v.get("url") for k, v in
                      {"video": got["fromVideo"], "panel": got["fromPanel"],
                       "color": got["fromColor"]}.items()}))
    check(LABELS[16], got["roundTrip"]["src"] == CARDS["video"]["src"]
          and got["roundTrip"]["duration_ms"] == 54_420,
          json.dumps(got["roundTrip"])[:160])

    # --- the backfill -----------------------------------------------------
    derived = got["derived"]
    keys = [f'{d["kind"]}:{d.get("label") or d.get("color")}' for d in derived]
    # panel (twice, one card) + colour card + voiceover (3 clips, one card)
    check(LABELS[17], len(derived) == 3, f"{len(derived)} cards: {keys}")
    check(LABELS[18], any(d["kind"] == "color" for d in derived), str(keys))

    # --- an overlay is a picture clip the library has to recognise --------
    # ⚠ THESE ARE THE THREE PLACES THAT USED TO MISS IT, asked as one question:
    # can an overlay be matched to a card at all? The ×N badge, the card's ✕ and
    # "Select its clips" all key on `assetKey`, so if these hold, all three do.
    ok = got["overlayKeys"]
    check(LABELS[19], ok["panel"] == "panel:b1:2", json.dumps(ok))
    # The proof that it is the stored `src` doing the work and not the upload id:
    # the overlay's own upload is "COPY_OF_PANEL", which appears in no key.
    check(LABELS[20], "COPY_OF_PANEL" not in json.dumps(ok), json.dumps(ok))
    check(LABELS[21], ok["upload"] == "upload:u4", json.dumps(ok))
    # ⚠ NOT MERELY "they have keys" — they must be DIFFERENT keys. Both carry the
    # schema default (`kind: "panel"`, no ids), which reads raw as `panel::` for
    # every legacy overlay in a project.
    check(LABELS[22], ok["legacyA"] != ok["legacyB"]
          and ok["legacyA"] == "upload:uA" and ok["legacyB"] == "upload:uB",
          json.dumps(ok))

    from_overlays = got["derivedOverlays"]
    labels_of = [d.get("label") for d in from_overlays]
    # One panel (a frame AND an overlay) + one uploaded overlay = two cards.
    check(LABELS[23], len(from_overlays) == 2,
          f"{len(from_overlays)} cards: {labels_of}")
    check(LABELS[24], "Shot 3" in labels_of, str(labels_of))

# The lock rides in `settings`, so its absence is a settings question rather than
# a library one — checked here because it is the same "did the field survive?"
# failure mode and there is no other home for it.
print("\nA lock is saved with the project, like the eye is")
settings_fields = set(AnimaticSettings.model_fields)
check("settings carry locked_lanes as well as hidden_lanes",
      {"hidden_lanes", "locked_lanes"} <= settings_fields,
      str(sorted(settings_fields)))
check("both default to empty, so an old project is neither hidden nor locked",
      AnimaticSettings().hidden_lanes == [] and AnimaticSettings().locked_lanes == [],
      f"{AnimaticSettings().hidden_lanes} / {AnimaticSettings().locked_lanes}")

# An OVERLAY is a picture clip too, so it has to say which SOURCE it plays or the
# Media library cannot recognise it — see `AnimaticOverlay.src`. Checked here for
# the same reason the lock is: it is a "did the field survive the trip?" failure,
# and the JS half above can only prove what the client BUILDS, never what the
# store keeps. `_animatics.py` writes overlays with `model_dump(exclude={"url"})`,
# which is exactly the round trip below.
print("\nAn overlay says which source it is playing")
check("AnimaticOverlay carries a src, like a frame does",
      "src" in set(AnimaticOverlay.model_fields), str(sorted(AnimaticOverlay.model_fields)))
_legacy = AnimaticOverlay(id="o0", upload_id="u0")
# ⚠ THE DEFAULT MATCHES NOTHING AND IS THE SAME FOR EVERY LEGACY OVERLAY — which
# is precisely why the client re-reads a src with no usable ids as its own upload
# (`overlaySource`), rather than keying on `panel::` and folding them into one.
check("…defaulting to a reference that names nothing, for a project saved before it",
      _legacy.src.storyboard_id is None and _legacy.src.upload_id is None,
      _legacy.src.model_dump_json())
_stored = AnimaticOverlay(
    id="o1", upload_id="a_copy",
    src={"kind": "panel", "storyboard_id": "b1", "index": 2},
).model_dump(exclude={"url"})
_back = AnimaticOverlay(**_stored)
check("…and a panel reference survives the store's round trip",
      _back.src.kind == "panel" and _back.src.storyboard_id == "b1" and _back.src.index == 2,
      _back.src.model_dump_json())

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
if skipped:
    print(f"{len(skipped)} check(s) skipped — install node to run them.")
print("A library card saves as the schema's AnimaticAsset, and its identity is its source.")
