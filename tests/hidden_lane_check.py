"""A HIDDEN LAYER MUST BE HIDDEN IN THE MP4, not just in the monitor.

The eye in the timeline's gutter is the counterpart of the speaker an audio row
has always had, and it makes one promise: this row is left out of the video. A
switch that dimmed the preview and then exported the row anyway would be lying at
the one moment it matters — which is why `hidden_lanes` is a project SETTING and
not something the browser remembers, and why this suite exists.

⚠ NOTHING IS ENCODED HERE. The worker submit is stubbed, so what is asserted is
the PAYLOAD the encoder is handed — the last place the client's intent can still
be dropped. The two rules it has to keep:

1. **A free-floating clip on a hidden row is dropped.** Text, shapes and overlay
   pictures hold no time of their own, so a row that is not drawn is a row that
   is not there — including for the calculation of how LONG the video is. A
   hidden caption row that still decided the length would leave seconds of held
   picture at the end with nothing on them.

2. **A hidden PICTURE row is blanked, never dropped.** `frames` is a sequence laid
   end to end: removing a clip moves every cut after it, shortens the video and
   pulls the audio out of sync — from pressing an eye. Blanked, the clip holds
   exactly the time it always held and draws the letterbox colour, which is what
   an NLE shows for a track it is not outputting. `shown` in AnimaticEditor.jsx
   does the identical conversion, which is what keeps the preview honest.

Plus the one that protects every animatic that already exists: with `hidden_lanes`
empty, the payload is exactly what it was before any of this.

    python tests/hidden_lane_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

from server import worker
from server.main import app
from server.schemas import AnimaticFrame

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


# --- Catch the payload instead of encoding it -------------------------------
sent: list[dict] = []
worker.submit_animatic_export = lambda job_id, payload: sent.append(dict(payload))
# The router imported the name at module load, so the stub has to land there too.
import server.animatics as animatics  # noqa: E402

animatics.worker.submit_animatic_export = worker.submit_animatic_export

client = TestClient(app)
email = f"_hide_{uuid.uuid4().hex[:10]}@example.com"
r = client.post("/auth/register", json={"email": email, "password": "hide-pass-12345"})
assert r.status_code == 201, r.text
auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

BOARD = uuid.uuid4().hex[:12]

# A project with something on every row this feature can switch off. No files
# exist behind any of it — nothing decodes here, and the payload is built before
# anything would.
FRAMES = [
    # A board panel: origin "board", so it belongs to the STILLS row…
    {"id": "f1", "src": {"kind": "panel", "storyboard_id": BOARD, "index": 0},
     "duration_ms": 2000, "label": "Shot 1"},
    # …and so does this one, even though it is a video clip now: animating a board
    # shot with Veo must not move it to another row. This is `_frame_origin`.
    {"id": "f2", "src": {"kind": "video", "storyboard_id": BOARD, "index": 1,
                         "upload_id": uuid.uuid4().hex[:12]},
     "kind": "video", "duration_ms": 3000, "label": "Shot 2 (animated)"},
    # A video FILE the user dropped in, ON A PICTURE TRACK OF ITS OWN — which is
    # where "put the footage on its own track" puts it, and the case the
    # `frames:<n>` tokens exist for. ⚠ It keeps the moment it plays at: the split
    # only ever moves a clip between rows.
    {"id": "f3", "src": {"kind": "video", "upload_id": uuid.uuid4().hex[:12]},
     "kind": "video", "duration_ms": 5000, "label": "clip",
     "track": 1, "start_ms": 5000},
    # An uploaded still, and a colour card: both stills.
    {"id": "f4", "src": {"kind": "upload", "upload_id": uuid.uuid4().hex[:12]},
     "duration_ms": 1000},
    {"id": "f5", "src": {"kind": "upload"}, "kind": "color", "color": "#123456",
     "duration_ms": 1500},
]
TEXTS = [
    {"id": "t1", "text": "on the default row", "start_ms": 0, "duration_ms": 1000},
    {"id": "t2", "text": "on a row of its own", "layer_id": "L-TEXT",
     "start_ms": 0, "duration_ms": 90_000},
]
SHAPES = [
    {"id": "s1", "kind": "star", "start_ms": 0, "duration_ms": 1000},
    {"id": "s2", "kind": "rect", "layer_id": "L-SHAPE", "start_ms": 0, "duration_ms": 1000},
]


def make(hidden):
    """A fresh project with these rows switched off.

    Two calls, because POST only takes the sequence: everything else is an EDIT,
    which is also how the editor puts it there.
    """
    res = client.post("/animatics", headers=auth, json={"title": "Hidden rows", "frames": FRAMES})
    assert res.status_code == 201, res.text
    job_id = res.json()["job_id"]
    res = client.put(f"/animatics/{job_id}", headers=auth, json={
        "texts": TEXTS,
        "shapes": SHAPES,
        "layers": [
            {"id": "L-TEXT", "kind": "text", "name": "Text 2"},
            {"id": "L-SHAPE", "kind": "shape", "name": "Shapes 2"},
        ],
        "settings": {"hidden_lanes": hidden, "background": "#101010"},
    })
    assert res.status_code == 200, res.text
    # The setting has to survive the save, or everything below would pass by
    # accident on a project with no hidden rows at all.
    assert res.json()["settings"]["hidden_lanes"] == hidden, res.json()["settings"]
    return job_id


def export(hidden):
    """Export a project with these rows hidden, and return the payload."""
    sent.clear()
    job_id = make(hidden)
    r = client.post(f"/animatics/{job_id}/export", headers=auth)
    assert r.status_code == 202, r.text
    client.delete(f"/animatics/{job_id}", headers=auth)
    return sent[-1]


TOTAL_MS = sum(f["duration_ms"] for f in FRAMES)

# ---------------------------------------------------------------------------
print("\n[0] the ORIGIN of a clip decides which picture row it is on")
origin = animatics._frame_origin
check("a board panel is a board clip", origin(AnimaticFrame(**FRAMES[0])), "board")
check("⚠ an ANIMATED board shot is still a board clip",
      origin(AnimaticFrame(**FRAMES[1])), "board")
check("a dropped-in video file is footage", origin(AnimaticFrame(**FRAMES[2])), "video")
check("an uploaded still is an image", origin(AnimaticFrame(**FRAMES[3])), "image")
check("a colour card is an image", origin(AnimaticFrame(**FRAMES[4])), "image")

# ---------------------------------------------------------------------------
print("\n[1] nothing hidden: the payload is what it always was")
base = export([])
check("every clip is in the sequence", len(base["frames"]), 5)
check("no clip was blanked", [f["kind"] for f in base["frames"]],
      ["image", "video", "video", "image", "color"])
check("both text clips are exported", len(base["texts"]), 2)
check("both shapes are exported", len(base["shapes"]), 2)
check("the long caption sets the length", base["end_ms"], 90_000)

# ---------------------------------------------------------------------------
print("\n[2] a hidden TEXT row is dropped — and stops deciding the length")
p = export(["text:L-TEXT"])
check("the clip on that row is gone", [t["id"] for t in p["texts"]], ["t1"])
check("the default row is untouched", len(p["texts"]), 1)
# ⚠ NOT "no end_ms at all": the clips still on screen decide it. What must be
# gone is the ninety seconds the HIDDEN caption was asking for.
check("⚠ the hidden caption no longer extends the video", p["end_ms"], 1000)
check("the pictures are untouched", len(p["frames"]), 5)

p = export(["text:"])
check("hiding the DEFAULT row drops its clip, not the other one",
      [t["id"] for t in p["texts"]], ["t2"])
check("and a row with its own id keeps deciding the length", p["end_ms"], 90_000)

# ---------------------------------------------------------------------------
print("\n[3] a hidden SHAPE row is dropped the same way")
p = export(["shape:L-SHAPE"])
check("the clip on that row is gone", [s["id"] for s in p["shapes"]], ["s1"])
p = export(["shape:"])
check("and the default row can be hidden on its own",
      [s["id"] for s in p["shapes"]], ["s2"])

# ---------------------------------------------------------------------------
print("\n[4] ⚠ a hidden PICTURE TRACK: blanked on the base, DROPPED above it")
# ⚠ THE ASYMMETRY IS THE POINT, and the two are the SAME PICTURE where each
# applies. Track 0 is the bottom of the stack, so a dropped clip would reveal the
# letterbox colour — which is exactly what a colour card of that colour draws;
# blanking is chosen there because it also HOLDS THE TIME, and a base track hidden
# in full would otherwise leave the export with no pictures at all. Above it a
# dropped clip reveals the track UNDERNEATH, and an opaque card would hide it.
p = export(["frames:1"])
check("the clip on the hidden track is GONE, not blanked", len(p["frames"]), 4)
check("…and it is the one that was on track 1", [f["id"] for f in p["frames"]],
      ["f1", "f2", "f4", "f5"])
check("the base track is untouched", [f["kind"] for f in p["frames"]],
      ["image", "video", "image", "color"])
check("the ANIMATED BOARD SHOT is still drawn — origin does not decide the row",
      p["frames"][1]["kind"], "video")

p = export(["frames:0"])
kinds = [f["kind"] for f in p["frames"]]
check("hiding the BASE track blanks its clips rather than removing them",
      len(p["frames"]), 5)
check("every base clip draws the letterbox colour", kinds,
      ["color", "color", "video", "color", "color"])
blank = p["frames"][0]
check("a blank holds exactly the time it held", blank["duration_ms"], 2000)
check("it draws the letterbox colour", blank["color"], "#101010")
check("and has no file to draw", (blank["path"], blank["video_path"]), (None, None))
check("the clip on track 1 still plays", p["frames"][2]["kind"], "video")

# ---------------------------------------------------------------------------
print("\n[5] rows switch off independently, and an unknown token is inert")
p = export(["frames:1", "text:L-TEXT", "shape:"])
check("three rows off at once", (len(p["texts"]), len(p["shapes"])), (1, 1))
check("the picture track is dropped", [f["id"] for f in p["frames"]],
      ["f1", "f2", "f4", "f5"])
p = export(["audio:", "nonsense", "text:no-such-layer"])
check("a token naming nothing changes nothing about the pictures",
      [f["kind"] for f in p["frames"]], ["image", "video", "video", "image", "color"])
check("…or the clips", (len(p["texts"]), len(p["shapes"])), (2, 2))

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("A hidden row is hidden in the export, and hides nothing else.")
