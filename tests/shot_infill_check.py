"""A SHOT THAT IS NOT ON THE BOARD — "generate the shot before / after this one".

The report:

    "i want when i rightclick on storyboard image so user get dropdwon popup and
     i want keep two fuction buttun first Generate befor shot and second Generate
     after shot image … so user get Animate this shot popup same as veo video
     gnerete time panle … and keep name like instead of shot 4 name After Shot 4
     and show blank prompt box in corner with ai icon"

---------------------------------------------------------------------------
WHAT THIS FILE IS ACTUALLY GUARDING
---------------------------------------------------------------------------
⚠ THE BOARD IS NEVER EDITED. The obvious implementation of "add a shot between
two shots" is `POST /storyboards/{id}/panels/insert` and then a draw, and it is
WRONG: that route renumbers panels so `index == position`, while an animatic
frame references a panel BY INDEX. One insert would silently re-point every
frame after it — in this project and in every other animatic built from the same
board — at the wrong picture. So the drawing is stored as an ordinary animatic
UPLOAD, the clip carries `src.shot_id` instead of an index, and the two checks
that matter most here are "the board came out unchanged" and "the panel indices
still say what they said".

⚠ AND THE NEIGHBOURS ARE READ IN PLAY ORDER. A drag re-times a clip without
touching the list, so "the shot before this one" is a question about `start_ms`.
A list-order answer is right until the first time anybody drags anything, which
is why one of these frames is deliberately stored out of order.

⚠ NO MODEL IS EVER CALLED. `draw_loose_shot` and `suggest_shot_between` are both
replaced with stubs that record what they were handed — the point of the test is
the routing, the wiring and the continuity that gets passed DOWN to them, none
of which needs a paid call to check.

    python tests/shot_infill_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE ANY `server.*`
# IMPORT.** `server/config.py` reads the environment once, at import time, so
# without this line the suite boots against the developer's real `.env` — it
# registers its test accounts in the production database and spends real monthly
# quota, and then fails when billing refuses it. G13; see `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("shot_infill_check_")

from fastapi.testclient import TestClient
from PIL import Image

import script_breakdown
import storyboard_pipeline
from server.jobs import get_store
from server.main import app
from server.schemas import AnimaticFrameSource, JobKind, JobStatus

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)
store = get_store()


def register():
    email = f"_infill_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "infill-pass-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email


auth, email = register()
other_auth, _ = register()

print(f"\nstore: {type(store).__name__}\n")

# ---------------------------------------------------------------------------
# A board of three shots, and an animatic laid over it OUT OF LIST ORDER
# ---------------------------------------------------------------------------
PANELS = [
    {
        "index": 0,
        "description": "Kabir asleep, the slipper on the floor beside the bed.",
        "characters": ["KABIR"],
        "assets": ["Slipper"],
        "camera": "Wide",
        "location": "Bedroom, night",
    },
    {
        "index": 1,
        "description": "The slipper leaves the doorway, spinning.",
        "characters": ["MAA"],
        "assets": ["Slipper"],
        "camera": "Low angle",
        "location": "Bedroom, night",
    },
    {
        "index": 2,
        "description": "Kabir sits up, startled.",
        "characters": ["KABIR"],
        "assets": [],
        "camera": "Close-up",
        "location": "Bedroom, night",
    },
]
board = store.create(
    character_name="TTBB_EP_One",
    kind=JobKind.STORYBOARD,
    owner=email,
    params={
        "shots": [],
        "style": "rough-sketch",
        "aspect_ratio": "2:3",
        "cast": [{"name": "KABIR", "description": "A boy of ten, in a vest."}],
        "assets": [{"name": "Slipper", "description": "A worn rubber chappal."}],
        "world": {"region": "Kerala"},
    },
)
store.update(board.job_id, status=JobStatus.SUCCEEDED, result={"panels": PANELS})

# ⚠ fr2 IS STORED LAST AND PLAYS FIRST. The list says [fr0, fr1, fr2]; the clock
# says [fr2, fr0, fr1]. Every neighbour answer below is checked against the
# CLOCK, because that is what a drag leaves behind.
frames = [
    {"id": "fr0", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 0},
     "duration_ms": 2000, "start_ms": 2000, "track": 0, "label": "Shot 1"},
    {"id": "fr1", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 1},
     "duration_ms": 2000, "start_ms": 4000, "track": 0, "label": "Shot 2"},
    {"id": "fr2", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 2},
     "duration_ms": 2000, "start_ms": 0, "track": 0, "label": "Shot 3"},
    # A dropped file: no board behind it at all.
    {"id": "fr3", "src": {"kind": "upload", "upload_id": uuid.uuid4().hex[:12]},
     "duration_ms": 2000, "start_ms": 0, "track": 1, "label": "A photo"},
]
r = client.post("/animatics", headers=auth, json={"title": "Infill", "frames": frames})
assert r.status_code == 201, r.text
job_id = r.json()["job_id"]


def context(frame_id, side="after", headers=auth):
    return client.get(
        f"/animatics/{job_id}/frames/{frame_id}/neighbour?side={side}", headers=headers
    )


# ---------------------------------------------------------------------------
print("[1] the dialog opens on the gap, named after the clip it is beside")
r = context("fr0", "after")
check("-> 200", r.status_code, 200)
info = r.json()
check("it can be done", info["can_generate"])
check("THE NAME IS 'After Shot 1', not a shot number", info["label"], "After Shot 1")
check("the board is named", info["title"], "TTBB_EP_One")
check("the shape defaults to the BOARD's, not the project's", info["aspect_ratio"], "2:3")
check("and it says which model will draw it", bool(info["model"]))

# ---------------------------------------------------------------------------
print("\n[2] the neighbours are read in PLAY order, not list order")
# The clock is fr2 (0ms), fr0 (2000ms), fr1 (4000ms). So after fr0 comes fr1.
check("after Shot 1: the shot before the gap is Shot 1's own wording",
      info["before_description"], PANELS[0]["description"])
check("…and the shot after the gap is the one that PLAYS next",
      info["after_description"], PANELS[1]["description"])

r = context("fr0", "before")
check("-> 200", r.status_code, 200)
info_before = r.json()
check("THE NAME IS 'Before Shot 1'", info_before["label"], "Before Shot 1")
check("before Shot 1: the previous shot is the one DRAGGED in front of it",
      info_before["before_description"], PANELS[2]["description"])
check("…and Shot 1 itself is what follows the gap",
      info_before["after_description"], PANELS[0]["description"])

# ---------------------------------------------------------------------------
print("\n[3] the ends of the row have one neighbour, and say so with silence")
r = context("fr2", "before")
check("-> 200", r.status_code, 200)
check("nothing plays before the first shot", r.json()["before_description"], "")
check("…but the shot it opens on is still there",
      r.json()["after_description"], PANELS[2]["description"])
r = context("fr1", "after")
check("nothing plays after the last shot", r.json()["after_description"], "")

# ---------------------------------------------------------------------------
print("\n[4] a clip with no board behind it is refused, with a reason")
r = context("fr3")
check("-> 200, not a 400", r.status_code, 200)
check("it cannot be done", r.json()["can_generate"], False)
check("and it says why", "storyboard" in r.json()["reason"])
check("the name is still offered", r.json()["label"], "After A photo")

print("\n[5] a side that is neither 'before' nor 'after' is a 400")
check("-> 400", context("fr0", "sideways").status_code, 400)
check("a frame that is not here -> 404", context("nope").status_code, 404)

# ---------------------------------------------------------------------------
print("\n[6] ✨ writes the missing beat — from BOTH neighbours and the outline")
sent = {}


def fake_suggest(**kwargs):
    sent.update(kwargs)
    return "The slipper crosses the room in silhouette."


script_breakdown.suggest_shot_between = fake_suggest
r = client.post(
    f"/animatics/{job_id}/frames/fr0/neighbour/suggest",
    headers=auth,
    json={"side": "after", "notes": "keep it wide"},
)
check("-> 200", r.status_code, 200)
check("the suggestion comes back", r.json()["description"],
      "The slipper crosses the room in silhouette.")
check("it was told what plays BEFORE the gap", sent.get("previous"), PANELS[0]["description"])
check("…and what plays AFTER it", sent.get("following"), PANELS[1]["description"])
check("…and the stretch of film around it", len(sent.get("outline") or []) >= 3)
check("the outline is in PLAY order", (sent.get("outline") or [])[0], PANELS[2]["description"])
check("what the user typed is passed as steering", sent.get("notes"), "keep it wide")
check("and the film is named", sent.get("title"), "TTBB_EP_One")

r = client.post(
    f"/animatics/{job_id}/frames/fr3/neighbour/suggest", headers=auth, json={"side": "after"}
)
check("a clip with no board -> 400", r.status_code, 400)

# ⚠ THE FURNITURE COMES OFF BEFORE THE BOX SEES IT. A chat model answers a
# one-line question with a heading and a bullet, and an image model handed "1."
# draws a numeral in the corner of the picture.
tidy = script_breakdown.tidy_shot_line
check("a shot heading is stripped", tidy("Shot 4: He turns."), "He turns.")
check("a bullet is stripped", tidy("- He turns."), "He turns.")
check("a number is stripped", tidy("1) He turns."), "He turns.")
check("stacked furniture is all stripped", tidy(" Shot 4 — * He turns. "), "He turns.")
check("quotes come off both ends", tidy('"He turns."'), "He turns.")
check("a plain sentence is left exactly alone", tidy("He turns."), "He turns.")
check("…including one that starts with a number that is part of the shot",
      tidy("3 boys run past the window."), "3 boys run past the window.")
check("nothing in, nothing out", tidy(""), "")

# ---------------------------------------------------------------------------
print("\n[7] the drawing lands as an UPLOAD, and the board is untouched")
drawn = {}


def fake_draw(board_job_id, description, **kwargs):
    drawn["board"] = board_job_id
    drawn["description"] = description
    drawn.update(kwargs)
    return Image.new("RGB", (64, 96), (12, 34, 56))


storyboard_pipeline.draw_loose_shot = fake_draw

before_panels = list((store.get(board.job_id).result or {}).get("panels") or [])
r = client.post(
    f"/animatics/{job_id}/frames/fr0/neighbour",
    headers=auth,
    json={
        "side": "after",
        "description": "The slipper crosses the room in silhouette.",
        "aspect_ratio": "2:3",
        "duration_ms": 8000,
    },
)
check("-> 200", r.status_code, 200)
clip = r.json()["frame"]
check("it is an UPLOAD, not a panel", clip["src"]["kind"], "upload")
check("there is a file behind it", bool(clip["src"]["upload_id"]))
check("⚠ NO PANEL INDEX — it is not on the board", clip["src"]["index"], None)
check("but it CARRIES the board, so it sits on the storyboard row",
      clip["src"]["storyboard_id"], board.job_id)
check("it has an identity of its own for a take to pair with",
      bool(clip["src"]["shot_id"]))
check("the wording it was drawn from rides along on the clip",
      clip["src"]["prompt"], "The slipper crosses the room in silhouette.")
check("it is called 'After Shot 1'", clip["label"], "After Shot 1")
check("it holds for the length that was asked for", clip["duration_ms"], 8000)
check("and it is servable straight away, before the project is saved",
      clip["url"], f"/animatics/{job_id}/media/{clip['src']['upload_id']}")
check("the model that drew it is named", bool(r.json()["model"]))

after_panels = list((store.get(board.job_id).result or {}).get("panels") or [])
check("⚠ THE BOARD STILL HAS EXACTLY THREE PANELS", len(after_panels), 3)
check("…and every index still means what it meant",
      [p["index"] for p in after_panels], [0, 1, 2])
check("…and no panel's wording was rewritten", after_panels, before_panels)

saved = client.get(f"/animatics/{job_id}", headers=auth).json()
check("⚠ AND THE PROJECT WAS NOT SAVED FOR US — the client places the clip",
      len(saved["frames"]), 4)

# ---------------------------------------------------------------------------
print("\n[8] the draw was handed the BOARD's look and its neighbours' cast")
check("the board's active style", drawn.get("style"), "rough-sketch")
check("the shape that was asked for", drawn.get("aspect_ratio"), "2:3")
check("the written continuity bible", drawn.get("cast"), board.params["cast"])
check("the world", drawn.get("world"), {"region": "Kerala"})
check("the look anchor is the clip that was right-clicked",
      drawn.get("anchor_index"), 0)
check("BOTH neighbours' characters are locked to their references",
      sorted(drawn.get("characters") or []), ["KABIR", "MAA"])
check("…and their props", drawn.get("assets_named"), ["Slipper"])
flow = drawn.get("story_context") or {}
check("it was told what runs before", flow.get("previous"), PANELS[0]["description"])
check("…and after", flow.get("next"), PANELS[1]["description"])

# The PNG is really on disk under the animatic's own media folder.
from server.animatics import _image_path  # noqa: E402  (after the app is built)

path = _image_path(job_id, clip["src"]["upload_id"])
check("the picture is written into the animatic's media folder", os.path.isfile(path))
if os.path.isfile(path):
    with Image.open(path) as im:
        check("…as a PNG of the size the model returned", im.size, (64, 96))

# ---------------------------------------------------------------------------
print("\n[9] a generated shot keys on its OWN id, never on a panel's index")
from server.animatics import _shot_key  # noqa: E402

gen = AnimaticFrameSource(
    kind="upload", upload_id="u1", storyboard_id=board.job_id, shot_id="abc123"
)
take_of_gen = AnimaticFrameSource(
    # What ✨ Animate leaves behind: the whole `src` copied, `kind` and
    # `upload_id` swapped for the video's.
    kind="video", upload_id="vid9", storyboard_id=board.job_id, shot_id="abc123"
)
panel0 = AnimaticFrameSource(kind="panel", storyboard_id=board.job_id, index=0)
check("a take of a generated shot pairs with it", _shot_key(gen), _shot_key(take_of_gen))
check("⚠ AND IT DOES NOT COLLIDE WITH A REAL PANEL",
      _shot_key(gen) == _shot_key(panel0), False)
check("a panel still keys the way it always did",
      _shot_key(panel0), f"{board.job_id}:0:")
check("a clip off no board keys as nothing",
      _shot_key(AnimaticFrameSource(kind="upload", upload_id="u2")), "")

# ---------------------------------------------------------------------------
print("\n[10] another account gets nothing")
check("someone else's animatic -> 404", context("fr0", headers=other_auth).status_code, 404)
stolen = client.post("/animatics", headers=other_auth, json={
    "title": "Crafted",
    "frames": [{"id": "fx", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 0},
                "duration_ms": 2000, "label": "Shot 1"}],
})
assert stolen.status_code == 201, stolen.text
stolen_id = stolen.json()["job_id"]
r = client.get(f"/animatics/{stolen_id}/frames/fx/neighbour?side=after", headers=other_auth)
check("a crafted board id -> 200 with nothing", r.status_code, 200)
check("…and it cannot be drawn", r.json()["can_generate"], False)
check("…and no wording leaks", r.json()["before_description"], "")
r = client.post(
    f"/animatics/{stolen_id}/frames/fx/neighbour",
    headers=other_auth,
    json={"side": "after", "description": "anything"},
)
check("…and drawing through it -> 400", r.status_code, 400)

client.delete(f"/animatics/{stolen_id}", headers=other_auth)
client.delete(f"/animatics/{job_id}", headers=auth)
store.delete(board.job_id)

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("A shot can be drawn into the gap, and the storyboard never notices.")
