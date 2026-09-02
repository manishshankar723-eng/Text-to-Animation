"""The project file another editor opens — is it the same CUT?

`interchange.py` writes THREE documents off ONE model — an FCP7 XML (`xmeml` v4)
for Premiere / Resolve / Avid / Final Cut, an ExtendScript for After Effects, and
a CMX3600 EDL — each with the same `media/` folder beside it. Nothing about that
can be checked by looking at the code, so this builds a REAL project — clips on
two picture tracks with a gap, a video clip with an in point, a colour card, an
overlay, three audio clips on two lanes, a dissolve, and a text clip that has
nowhere to go — writes the export to a scratch directory, PARSES THE XML BACK,
and measures it.

The four things it exists to catch:

  1. **The clock.** Milliseconds in, frames out, ONE rounding rule, half away
     from zero. Python's `round` is banker's rounding and would send 0.5 down —
     the drift that puts a hundred-clip film half a frame out of sync.
  2. **The placement.** A clip with no `start_ms` sits after the last clip on ITS
     track; a gap stays a gap; a clip on track 1 does not slide because a clip on
     track 0 was dropped. `frame_spans` decides all of that and this proves the
     exporter asks it rather than adding lengths up itself.
  3. **Every `<pathurl>` lands on a file that exists.** An export whose XML names
     a picture the zip does not carry is the "Media Offline" bug, and it is
     invisible until somebody opens Premiere.
  4. **The honesty.** What has no box in xmeml — grades, masks, text, shapes —
     is COUNTED and reported, not silently dropped.
  5. **The guess stays a guess.** Section 8c covers the `.prproj` reader, the one
     reader here with no specification behind it. ⚠ It CANNOT prove that reader
     opens a real Premiere project — the fixture is a hand-built imitation and
     there is no writer to round-trip against. What it does prove is that the
     refusal is still the default answer, that every import it produces says it
     is a best-effort read, that the file's own frame rate is used and not the
     caller's, and that a gzip bomb is refused.
  6. **Nothing handed back is a 500.** Section 8d validates everything
     `to_project` returns against the real `AnimaticFrame` / `AnimaticAudio` — the
     models the ROUTE builds out of it — because they cap a clip at ten minutes
     and forbid a negative start, and a fifteen-minute take out of somebody
     else's timeline is ordinary. ⚠ It also proves those models WOULD have
     rejected the unclamped values; without that the section passes on a build
     with no bounds at all. This one is not about any single reader: the clamp is
     in `to_project`, so it is fcp7's, edl's and prproj's alike.

    python tests/interchange_check.py

Needs Pillow (already a dependency). No server, no ffmpeg, no network.
"""

import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ SET BEFORE ANYTHING IMPORTS `server`, which is why it is up here and not
# beside section 8. `server.config` reads the environment at import time, so a
# store chosen after the import is a store nobody is using — and the default is
# Mongo, which this test must never touch.
_TMP = tempfile.mkdtemp(prefix="interchange_api_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_FEATURES_PATH"] = os.path.join(_TMP, "features.json")
os.environ["API_LOCAL_TIERS_PATH"] = os.path.join(_TMP, "tiers.json")
os.environ["API_LOCAL_OFFERS_PATH"] = os.path.join(_TMP, "offers.json")
os.environ["API_LOCAL_SUBSCRIPTIONS_PATH"] = os.path.join(_TMP, "subs.json")
os.environ["API_LOCAL_BRANDING_PATH"] = os.path.join(_TMP, "branding.json")
os.environ["API_LOCAL_BANNERS_PATH"] = os.path.join(_TMP, "banners.json")
os.environ["API_LOCAL_SHOWCASE_PATH"] = os.path.join(_TMP, "showcase.json")
os.environ["API_LOCAL_LANDING_PATH"] = os.path.join(_TMP, "landing.json")
# ⚠ THIS ONE IS TRACKED IN GIT AND DEFAULTS TO THE REPO ROOT. Leaving it out
# does not fail the test — it silently spends the DEVELOPER's monthly project
# quota and leaves two fake accounts in `.local_usage.json`, which is how the
# second run of this file failed with "You've used 2 of your 2 projects".
os.environ["API_LOCAL_USAGE_PATH"] = os.path.join(_TMP, "usage.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
# ⚠ `API_OUTPUT_DIR`, NOT `OUTPUT_DIR`. Getting the name wrong does not fail:
# `server/config.py` quietly falls back to `"output"` in the repo, and this
# test then writes real project folders into the developer's own workspace.
# Caught by the disk assertion below, which could not find the folder it had
# just been told to look in.
os.environ["API_OUTPUT_DIR"] = os.path.join(_TMP, "output")
os.environ["JWT_SECRET"] = "interchange-check-not-a-real-secret"

from PIL import Image

import interchange

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# 1 · The clock
# ---------------------------------------------------------------------------
print("\n1 · ms → frames, one rule, half away from zero")

check("0ms is frame 0", interchange.ms_to_frames(0, 24) == 0)
check("1000ms at 24fps is 24 frames", interchange.ms_to_frames(1000, 24) == 24)
check("2000ms at 24fps is 48 frames", interchange.ms_to_frames(2000, 24) == 48)
# ⚠ THE ONE THAT CATCHES `round()`. At 2fps, 250ms is EXACTLY half a frame and
# 750ms is exactly one and a half. Banker's rounding answers 0 and 2; half-away
# answers 1 and 2. A test using only 750 would pass on the broken version.
check("exactly half a frame rounds UP", interchange.ms_to_frames(250, 2) == 1,
      f"got {interchange.ms_to_frames(250, 2)}")
check("one and a half frames rounds up", interchange.ms_to_frames(750, 2) == 2)
check("None is 0, not a crash", interchange.ms_to_frames(None, 24) == 0)
check("timecode of 0", interchange.frames_to_timecode(0, 24) == "00:00:00:00")
check("timecode of 25 frames at 24fps", interchange.frames_to_timecode(25, 24) == "00:00:01:01",
      interchange.frames_to_timecode(25, 24))


# ---------------------------------------------------------------------------
# A real project on disk
# ---------------------------------------------------------------------------
work = tempfile.mkdtemp(prefix="interchange_")
media = os.path.join(work, "src")
os.makedirs(media, exist_ok=True)


def png(name, colour):
    path = os.path.join(media, name)
    Image.new("RGB", (320, 180), colour).save(path, "PNG")
    return path


def blob(name, size=2048):
    path = os.path.join(media, name)
    with open(path, "wb") as fh:
        fh.write(b"\0" * size)
    return path


shot1 = png("panel_001.png", (200, 30, 30))
shot2 = png("panel_002.png", (30, 200, 30))
logo = png("logo.png", (10, 10, 240))
movie = blob("vid_abc123.mp4")
song = blob("audio_def456.mp3")

PROJECT = {
    "title": "My Test Film",
    "fps": 24,
    "width": 1920,
    "height": 1080,
    "background": "#000000",
    "end_ms": 0,
    "lane_order": [],
    "hidden_lanes": ["frames:9"],
    "show_labels": False,
    "frames": [
        # Track 0, laid end to end the old way — no `start_ms` at all.
        {"id": "f1", "kind": "image", "label": "Shot 1", "duration_ms": 2000,
         "track": 0, "start_ms": None, "path": shot1},
        {"id": "f2", "kind": "image", "label": "Shot 2", "duration_ms": 1000,
         "track": 0, "start_ms": None, "path": shot2,
         # Has a grade and a mask — neither has a box in xmeml.
         "effects": [{"kind": "exposure", "params": {}}],
         "mask": {"kind": "ellipse"}},
        # A GAP: starts at 5s, not at 3s where the clip before it ended.
        {"id": "f3", "kind": "color", "label": "Black card", "duration_ms": 1000,
         "track": 0, "start_ms": 5000, "color": "#101820"},
        # Another track, with an in point and a speed change.
        {"id": "f4", "kind": "video", "label": "Take 4", "duration_ms": 2000,
         "track": 1, "start_ms": 1000, "in_ms": 500, "speed": 2.0,
         "video_path": movie},
        # A hidden row — must not appear at all.
        {"id": "f5", "kind": "image", "label": "Hidden", "duration_ms": 1000,
         "track": 9, "start_ms": 0, "path": shot1},
        # A file that has gone. Named in `missing`, left out of the XML, and
        # ⚠ it must NOT move the clips after it on its own track.
        {"id": "f6", "kind": "image", "label": "Gone", "duration_ms": 1000,
         "track": 2, "start_ms": None, "path": os.path.join(media, "nope.png")},
        {"id": "f7", "kind": "image", "label": "After the gone one", "duration_ms": 1000,
         "track": 2, "start_ms": None, "path": shot2},
    ],
    "overlays": [
        {"id": "o1", "layer_id": "", "upload_id": "u1", "start_ms": 500,
         "duration_ms": 1500, "opacity": 0.8, "x": 0.2, "y": 0.2, "path": logo},
    ],
    "transitions": [
        # f1 → f2 butt up against each other on track 0, so this one places.
        {"id": "t1", "after_frame_id": "f1", "kind": "wipe", "duration_ms": 600},
        # f2 → f3 has a GAP between them; a transition with no cut is inert.
        {"id": "t2", "after_frame_id": "f2", "kind": "dissolve", "duration_ms": 600},
    ],
    "audio_tracks": [
        {"id": "a1", "upload_id": "def456", "layer_id": "L1", "filename": "voice.mp3",
         "duration_ms": 8000, "start_ms": 0, "offset_ms": 0, "trim_ms": 4000,
         "volume": 1.0, "path": song},
        {"id": "a2", "upload_id": "def456", "layer_id": "L1", "filename": "voice.mp3",
         "duration_ms": 8000, "start_ms": 5000, "offset_ms": 5000, "trim_ms": 2000,
         "volume": 0.5, "path": song},
        {"id": "a3", "upload_id": "def456", "layer_id": "L2", "filename": "music.mp3",
         "duration_ms": 8000, "start_ms": 0, "offset_ms": 0, "trim_ms": 8000,
         "volume": 0.25, "fade_in_ms": 500, "duck_to": 0.3, "path": song},
    ],
    "texts": [{"id": "tx1", "text": "Hello", "start_ms": 0, "duration_ms": 1000}],
    "shapes": [{"id": "s1", "start_ms": 0, "duration_ms": 1000}],
}

model = interchange.build_sequence(PROJECT)


# ---------------------------------------------------------------------------
# 2 · The model — where every clip ended up
# ---------------------------------------------------------------------------
print("\n2 · the neutral model")

by_name = {c["name"]: c for lane in model["video"] for c in lane["clips"]}

check("the hidden row is not exported", "Hidden" not in by_name)
check("the clip whose file has gone is not exported", "Gone" not in by_name)
check("…and it is NAMED in `missing`", "Gone" in model["missing"], str(model["missing"]))
check("six clips came across", len(by_name) == 6, f"{sorted(by_name)}")

check("Shot 1 starts at frame 0", by_name["Shot 1"]["start"] == 0)
check("Shot 1 is 48 frames long", by_name["Shot 1"]["end"] == 48)
check("Shot 2 follows it with no start_ms", by_name["Shot 2"]["start"] == 48)
check("the GAP survived — the card starts at 5s (120)", by_name["Black card"]["start"] == 120,
      str(by_name["Black card"]["start"]))
check("the video clip sits where it was put (1s → 24)", by_name["Take 4"]["start"] == 24)
check("…and reads from its in point (0.5s → 12)", by_name["Take 4"]["in"] == 12)
check("…for exactly the timeline length, speed ignored",
      by_name["Take 4"]["out"] - by_name["Take 4"]["in"]
      == by_name["Take 4"]["end"] - by_name["Take 4"]["start"])
# ⚠ THE ONE THAT PROVES THE SPANS ARE COMPUTED BEFORE ANYTHING IS DROPPED.
# "Gone" is 1s long and sits first on track 2 with no start; the clip after it
# must still begin at 24, not at 0.
check("a dropped clip does not slide the clip after it",
      by_name["After the gone one"]["start"] == 24,
      str(by_name["After the gone one"]["start"]))

check("two picture rows plus the overlay row = 4 video tracks",
      len(model["video"]) == 4, str(len(model["video"])))
check("the overlay is on the TOP video track",
      any(c["name"] == "Image 1" for c in model["video"][-1]["clips"]))
check("two audio lanes", len(model["audio"]) == 2, str(len(model["audio"])))
check("the cut lane holds both of its clips", len(model["audio"][0]["clips"]) == 2)
check("the second audio clip starts at 5s (120)",
      model["audio"][0]["clips"][1]["start"] == 120)
check("…and reads from 5s into the file", model["audio"][0]["clips"][1]["in"] == 120)
check("its level came across", abs(model["audio"][0]["clips"][1]["level"] - 0.5) < 1e-6)

placed = [t for lane in model["video"] for t in lane["transitions"]]
check("the transition on a real cut placed", len(placed) == 1, str(len(placed)))
check("…centred on the cut at 2s (48)", placed and placed[0]["cut"] == 48)
check("…half its length either side", placed and placed[0]["start"] == 41
      and placed[0]["end"] == 55, str(placed))

check("one colour card became a file", any(f["kind"] == "color" for f in model["files"]))
check("the same picture used twice is ONE file",
      sum(1 for f in model["files"] if f["path"] == shot2) == 1)


# ---------------------------------------------------------------------------
# 3 · The honesty report
# ---------------------------------------------------------------------------
print("\n3 · what it admits it could not carry")

report = interchange.report_of(model)
dropped = {row["what"]: row["count"] for row in report["dropped"]}
for what in (
    "effects and colour grades",
    "masks",
    "speed changes",
    "text clips",
    "shape clips",
    "audio fades",
    "ducking",
    "transition shapes (exported as a dissolve)",
    "hidden rows (left out)",
    "overlay position and size",
):
    check(f"reported: {what}", what in dropped, str(sorted(dropped)))
check("the biggest loss is listed first",
      report["dropped"] == sorted(report["dropped"], key=lambda r: (-r["count"], r["what"])))
check("the clip count is the real one", report["clips"] == 6, str(report["clips"]))


# ---------------------------------------------------------------------------
# 4 · The XML itself
# ---------------------------------------------------------------------------
print("\n4 · the xmeml document, parsed back")

xml = interchange.write_fcp7_xml(model)
check("it declares the doctype Premiere looks for", "<!DOCTYPE xmeml>" in xml)
root = ET.fromstring(xml[xml.index("<xmeml"):])
check("root is xmeml version 4", root.tag == "xmeml" and root.get("version") == "4")

seq = root.find("sequence")
check("the sequence is named after the project",
      seq.findtext("name") == "My Test Film", seq.findtext("name"))
check("the timebase is the project's fps", seq.find("rate/timebase").text == "24")
check("NTSC is FALSE (an integer fps is never 23.976)",
      seq.find("rate/ntsc").text == "FALSE")
check("the frame size is the project's",
      seq.find("media/video/format/samplecharacteristics/width").text == "1920"
      and seq.find("media/video/format/samplecharacteristics/height").text == "1080")

vtracks = seq.findall("media/video/track")
atracks = seq.findall("media/audio/track")
check("four video tracks in the document", len(vtracks) == 4, str(len(vtracks)))
check("two audio tracks in the document", len(atracks) == 2, str(len(atracks)))
check("a transitionitem was written", len(seq.findall(".//transitionitem")) == 1)
check("…as a Cross Dissolve",
      seq.findtext(".//transitionitem/effect/effectid") == "Cross Dissolve")

clipitems = seq.findall(".//clipitem")
check("every clip and sound is a clipitem", len(clipitems) == 6 + 3, str(len(clipitems)))
# ⚠ A FILE IS DEFINED ONCE AND REFERENCED AFTER. `panel_002.png` is used by two
# clips; two FULL definitions is how a board panel arrives in the bin twice.
full = [f for f in seq.findall(".//file") if f.find("pathurl") is not None]
refs = [f for f in seq.findall(".//file") if f.find("pathurl") is None]
check("no file is defined twice", len(full) == len(model["files"]),
      f"{len(full)} full vs {len(model['files'])} files")
check("repeats are bare references", len(refs) >= 1, str(len(refs)))
check("every reference points at a defined file",
      {f.get("id") for f in refs} <= {f.get("id") for f in full})

levels = seq.findall(".//clipitem/filter/effect[effectid='audiolevels']")
check("every sound carries its level", len(levels) == 3, str(len(levels)))
check("the quiet music bed came through at 0.25",
      any(abs(float(p.findtext("value")) - 0.25) < 1e-4
          for e in levels for p in e.findall("parameter")))

check("every audio clipitem names a source track",
      len(seq.findall(".//clipitem/sourcetrack/mediatype")) == 3)

# ⚠ THE CLIPS ON A TRACK MUST BE IN TIME ORDER. An importer reads a track's
# children as a sequence and refuses documents that jump backwards.
for i, track_el in enumerate(vtracks + atracks):
    starts = [int(c.findtext("start")) for c in track_el.findall("clipitem")]
    check(f"track {i + 1} is in time order", starts == sorted(starts), str(starts))


# ---------------------------------------------------------------------------
# 5 · Every pathurl lands on a real file
# ---------------------------------------------------------------------------
print("\n5 · the recipe names only ingredients that are in the box")

out = os.path.join(work, "out")
interchange.copy_media_to(model, os.path.join(out, interchange.MEDIA_DIR))
from urllib.parse import unquote

named = [unquote(f.findtext("pathurl")) for f in full]
check("every pathurl is under media/",
      all(p.startswith(interchange.MEDIA_DIR + "/") for p in named), str(named))
check("every pathurl exists on disk",
      all(os.path.isfile(os.path.join(out, p)) for p in named),
      str([p for p in named if not os.path.isfile(os.path.join(out, p))]))

absolute = interchange.write_fcp7_xml(model, base_path="D:/Films/Cut")
check("base_path writes absolute file:// urls",
      "file://localhost/D:/Films/Cut/media/" in unquote(absolute), "")

# The colour card is a real PNG of the right colour, not a black rectangle.
card = next(f for f in model["files"] if f["kind"] == "color")
with Image.open(os.path.join(out, interchange.MEDIA_DIR, card["name"])) as im:
    check("the colour card was drawn in its own colour",
          im.convert("RGB").getpixel((5, 5)) == (0x10, 0x18, 0x20),
          str(im.convert("RGB").getpixel((5, 5))))
    check("…at the sequence's frame size", im.size == (1920, 1080), str(im.size))


# ---------------------------------------------------------------------------
# 6 · The bundle
# ---------------------------------------------------------------------------
print("\n6 · the zip a user actually downloads")

zip_path = os.path.join(work, "bundle", "film.zip")
os.makedirs(os.path.dirname(zip_path), exist_ok=True)
bundled = interchange.bundle(model, zip_path, "My Test Film.xml", fmt="fcp7")
with zipfile.ZipFile(zip_path) as zf:
    names = zf.namelist()
check("the xml is in the zip", "My Test Film.xml" in names, str(names[:4]))
check("a README tells the user what to do", "README.txt" in names)
check("the README says how to relink", "Media Offline" in
      zipfile.ZipFile(zip_path).read("README.txt").decode("utf-8"))
check("every media file is in the zip",
      all(f"{interchange.MEDIA_DIR}/{f['name']}" in names for f in model["files"]),
      str(names))
check("the colour card is in there too",
      f"{interchange.MEDIA_DIR}/{card['name']}" in names)
# ⚠ NOTHING HALF-WRITTEN IS LEFT BEHIND — not the scratch colour cards, and not
# the `.part` the zip is built as. The zip is renamed into place at the end so a
# failed export cannot destroy the one the user downloaded an hour ago.
_beside = os.listdir(os.path.dirname(zip_path))
check("no scratch PNG was left beside the zip",
      not any(n.startswith("_colour_") for n in _beside), str(_beside))
check("…and no half-written .part", not any(n.endswith(".part") for n in _beside),
      str(_beside))
check("bundle reports the same numbers", bundled["clips"] == report["clips"])

xml_only = os.path.join(work, "bundle", "film.xml")
interchange.write_document_only(model, xml_only, fmt="fcp7")
check("xml-only export writes a file", os.path.isfile(xml_only))
check("…and leaves no .part behind", not os.path.isfile(xml_only + ".part"))


# ---------------------------------------------------------------------------
# 7 · An empty project must not explode
# ---------------------------------------------------------------------------
print("\n7 · the edges")

empty = interchange.build_sequence({"title": "", "fps": 24, "width": 1920, "height": 1080})
check("an empty project builds", empty["video"] == [] and empty["audio"] == [])
check("…and still writes valid xml",
      ET.fromstring(interchange.write_fcp7_xml(empty).split("\n", 2)[2]) is not None)
check("…named 'Project' rather than blank", empty["name"] == "Project")

odd = interchange.build_sequence({
    "title": "  ", "fps": 999, "width": 1920, "height": 1080,
    "frames": [{"id": "x", "kind": "image", "duration_ms": 1000, "path": shot1}],
})
check("an impossible fps is clamped to 60", odd["fps"] == 60, str(odd["fps"]))

# A filename that cannot go in a zip or a URL.
weird = interchange.safe_name("shot 3 — “final” (v2).png")
check("a filename is made safe", "/" not in weird and " " not in weird and weird, weird)



# ---------------------------------------------------------------------------
# 8 · The two routes, through the real API
# ---------------------------------------------------------------------------
# ⚠ EVERYTHING ABOVE THIS LINE TESTS A MODULE, AND A GREEN MODULE IS NOT A
# WORKING FEATURE. The router has to resolve every clip to a file the way
# `export_animatic` does, refuse a project that is not yours, and answer with a
# zip that is actually a zip — none of which `interchange.py` can be asked
# about. So this registers a user, makes a real animatic with a real uploaded
# picture, and calls the two routes the editor calls.
print("\n8 · the API")

import io as _io  # noqa: E402
import json as _json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

api = TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def register(email):
    r = api.post("/auth/register", json={"email": email, "password": "password123"})
    return r.json()["access_token"]


MINE = register("interchange-owner@example.com")
THEIRS = register("interchange-stranger@example.com")

made = api.post(
    "/animatics",
    json={"title": "API Test Film", "settings": {"aspect_ratio": "9:16", "fps": 25}},
    headers=bearer(MINE),
)
check("a project can be created", made.status_code == 201, made.text[:200])
pid = made.json()["job_id"]

# A real picture, uploaded the way the editor uploads one.
buf = _io.BytesIO()
Image.new("RGB", (640, 360), (120, 60, 200)).save(buf, "PNG")
up = api.post(
    f"/animatics/{pid}/images",
    files={"files": ("shot.png", buf.getvalue(), "image/png")},
    headers=bearer(MINE),
)
check("a picture uploads", up.status_code == 200, up.text[:200])
upload_id = up.json()["items"][0]["upload_id"]

saved = api.put(
    f"/animatics/{pid}",
    json={
        "frames": [
            {"id": "c1", "src": {"kind": "upload", "upload_id": upload_id},
             "duration_ms": 2000, "label": "One", "track": 0},
            {"id": "c2", "src": {"kind": "upload", "upload_id": upload_id},
             "duration_ms": 1500, "label": "Two", "track": 0},
            {"id": "c3", "src": {"kind": "upload", "upload_id": upload_id},
             "duration_ms": 1000, "label": "Graded", "track": 1, "start_ms": 500,
             "effects": [{"id": "fx1", "kind": "saturation", "params": {"amount": 1.4}}]},
        ],
        "transitions": [
            {"id": "t1", "after_frame_id": "c1", "kind": "dissolve", "duration_ms": 400}
        ],
    },
    headers=bearer(MINE),
)
check("the timeline saves", saved.status_code == 200, saved.text[:200])

# --- the preview -----------------------------------------------------------
pv = api.get(f"/animatics/{pid}/interchange/preview", headers=bearer(MINE))
check("preview answers 200", pv.status_code == 200, pv.text[:200])
body = pv.json() if pv.status_code == 200 else {}
check("it counted all three clips", body.get("clips") == 3, str(body.get("clips")))
check("two video tracks", body.get("video_tracks") == 2, str(body.get("video_tracks")))
check("it reports the project's fps, not a default", body.get("fps") == 25,
      str(body.get("fps")))
check("the grade is named as a loss",
      any(row["what"] == "effects and colour grades" for row in body.get("dropped", [])),
      _json.dumps(body.get("dropped")))
check("the media is measured", body.get("media_bytes", 0) > 0, str(body.get("media_bytes")))

# --- the download ----------------------------------------------------------
dl = api.get(f"/animatics/{pid}/interchange", headers=bearer(MINE))
check("the export answers 200", dl.status_code == 200, dl.text[:200])
check("…as a zip", dl.headers.get("content-type") == "application/zip",
      str(dl.headers.get("content-type")))
# ⚠ THE NAME COMES BACK RFC-5987 ENCODED (`filename*=utf-8''...`) BECAUSE THE
# TITLE HAS SPACES IN IT, and `serverFilename` in api.js reads exactly that form
# first. A test asserting the plain `filename=` would pass only for titles with
# no spaces — which is not what a user calls a film.
check("…named after the project",
      "API%20Test%20Film.zip" in (dl.headers.get("content-disposition") or ""),
      str(dl.headers.get("content-disposition")))

with zipfile.ZipFile(_io.BytesIO(dl.content)) as zf:
    inside = zf.namelist()
    doc = ET.fromstring(zf.read("API Test Film.xml").decode("utf-8").split("\n", 2)[2])
check("the zip carries the xml and the media",
      "API Test Film.xml" in inside
      and any(n.startswith(f"{interchange.MEDIA_DIR}/") for n in inside),
      str(inside))
# ⚠ 9:16 AT 1080 IS 1080x1920. The router asks `animatic.resolve_size`, the same
# function the encoder asks — a project file that says 1920x1080 for a Reel is a
# vertical film that arrives letterboxed in Premiere.
check("the sequence is the project's own shape",
      doc.findtext("sequence/media/video/format/samplecharacteristics/width") == "1080"
      and doc.findtext("sequence/media/video/format/samplecharacteristics/height") == "1920",
      doc.findtext("sequence/media/video/format/samplecharacteristics/width"))
check("the timebase is 25, as the project was made",
      doc.findtext("sequence/rate/timebase") == "25")
check("the dissolve came through", len(doc.findall(".//transitionitem")) == 1)

# XML only, for someone who already holds the footage.
plain = api.get(f"/animatics/{pid}/interchange?media=false", headers=bearer(MINE))
check("xml-only answers xml", plain.status_code == 200
      and plain.headers.get("content-type") == "application/xml",
      f"{plain.status_code} {plain.headers.get('content-type')}")
check("…and it really is one", plain.content.lstrip().startswith(b"<?xml"))

# --- it is not a way to read somebody else's film ---------------------------
# ⚠ THE POINT OF RESOLVING PATHS IN THE ROUTER. An exporter that took ids and
# went looking for files itself would be a download button for other people's
# pictures.
for label, r in (
    ("preview", api.get(f"/animatics/{pid}/interchange/preview", headers=bearer(THEIRS))),
    ("export", api.get(f"/animatics/{pid}/interchange", headers=bearer(THEIRS))),
):
    check(f"a stranger cannot {label} this project", r.status_code in (403, 404),
          str(r.status_code))
check("and neither can somebody with no token",
      api.get(f"/animatics/{pid}/interchange").status_code in (401, 403))

# --- the same cut, in the other two formats --------------------------------
# ⚠ THE ROUTE HAS TO CHANGE THE ANSWER, not just the file extension. An EDL holds
# ONE video track, so the preview's list of losses must GROW when it is chosen —
# a dropdown that changes the download but not the warning is worse than no
# dropdown, because the user reads the Premiere warning and gets the EDL.
for fmt, ext, marker in (
    ("edl", "edl", "FCM: NON-DROP FRAME"),
    ("aftereffects", "jsx", "Run Script File"),
):
    pv2 = api.get(f"/animatics/{pid}/interchange/preview?format={fmt}", headers=bearer(MINE))
    check(f"{fmt}: preview answers 200", pv2.status_code == 200, pv2.text[:160])
    said = pv2.json() if pv2.status_code == 200 else {}
    check(f"{fmt}: the report names its own format", said.get("format") == fmt,
          str(said.get("format")))

    doc = api.get(
        f"/animatics/{pid}/interchange?format={fmt}&media=false", headers=bearer(MINE)
    )
    check(f"{fmt}: the document downloads", doc.status_code == 200, doc.text[:160])
    check(f"{fmt}: …with the right extension",
          f".{ext}" in (doc.headers.get("content-disposition") or ""),
          str(doc.headers.get("content-disposition")))
    check(f"{fmt}: …and is really that format", marker in doc.text, doc.text[:120])

    zipped = api.get(f"/animatics/{pid}/interchange?format={fmt}", headers=bearer(MINE))
    check(f"{fmt}: the zip downloads", zipped.status_code == 200, zipped.text[:160])
    with zipfile.ZipFile(_io.BytesIO(zipped.content)) as zf:
        inside = zf.namelist()
    check(f"{fmt}: the zip holds the right document",
          f"API Test Film.{ext}" in inside, str(inside))

edl_said = api.get(
    f"/animatics/{pid}/interchange/preview?format=edl", headers=bearer(MINE)
).json()
xml_said = api.get(
    f"/animatics/{pid}/interchange/preview?format=fcp7", headers=bearer(MINE)
).json()
check("choosing EDL grows the list of losses",
      len(edl_said["dropped"]) > len(xml_said["dropped"]),
      f"edl {len(edl_said['dropped'])} vs xml {len(xml_said['dropped'])}")
check("…and it names the upper video row it cannot hold",
      any("upper video rows" in row["what"] for row in edl_said["dropped"]),
      _json.dumps(edl_said["dropped"]))

# ⚠ ONE ZIP PER FORMAT ON DISK. All three are exports of the same project, so a
# single `<title>.zip` would mean fetching the EDL destroyed the XML downloaded a
# minute earlier — the same rule `_video_path` follows for mp4 / gif / png.
_kept = os.listdir(
    os.path.join(os.environ["API_OUTPUT_DIR"], "_animatics", pid, "_interchange")
)
check("each format keeps its own zip", len([f for f in _kept if f.endswith(".zip")]) == 3,
      str(sorted(_kept)))
check("…and no .part is left behind", not any(f.endswith(".part") for f in _kept),
      str(sorted(_kept)))

# An unknown format is answered, not refused — the same rule an unrecognised
# transition or clip kind follows everywhere else in this app.
odd_fmt = api.get(
    f"/animatics/{pid}/interchange/preview?format=premiere-2027", headers=bearer(MINE)
)
check("an unknown format folds down to the XML rather than 422",
      odd_fmt.status_code == 200 and odd_fmt.json().get("format") == "fcp7",
      f"{odd_fmt.status_code} {odd_fmt.text[:120]}")


# --- IMPORT: the round trip, and the refusals ------------------------------
# ⚠ THE ROUND TRIP IS THE ONE TEST THAT CANNOT LIE. Export this timeline, hand
# the zip straight back to the importer, and every clip has to come home on the
# same frame, on the same row, with its media matched — no placeholders. A
# reader checked only against a hand-written fixture proves it can read that
# fixture; this proves the two halves agree about the same film.
print("\n8b · import")

exported = api.get(f"/animatics/{pid}/interchange?format=fcp7", headers=bearer(MINE))
back = api.post(
    f"/animatics/{pid}/interchange/import",
    files={"document": ("API Test Film.zip", exported.content, "application/zip")},
    headers=bearer(MINE),
)
check("a zip exported from here imports straight back", back.status_code == 200,
      back.text[:200])
came = back.json() if back.status_code == 200 else {}
check("…as an FCP7 XML found inside the zip", came.get("reader") == "fcp7",
      str(came.get("reader")))
check("…with all three clips", came.get("clips") == 3, str(came.get("clips")))
check("…on the two rows they were on", came.get("video_tracks") == 2,
      str(came.get("video_tracks")))
check("…and the dissolve", came.get("transitions_read") == 1,
      str(came.get("transitions_read")))
# ⚠ NO PLACEHOLDERS IS THE WHOLE POINT OF THE ZIP. Every file the XML names is
# inside the archive, so nothing can be missing — if this ever fails, the media
# folder and the `<pathurl>`s have drifted apart.
check("…and nothing is a placeholder", came.get("placeholders") == [],
      str(came.get("placeholders")))
check("…the media was matched by name", came.get("matched", 0) >= 1,
      str(came.get("matched")))
check("it saved nothing — the report is the whole answer",
      len(api.get(f"/animatics/{pid}", headers=bearer(MINE)).json()["frames"]) == 3, "")

home = sorted(came.get("frames") or [], key=lambda f: (f["track"], f["start_ms"]))
# ⚠ THE ROUND TRIP IS **FRAME-EXACT, NOT MILLISECOND-EXACT**, and expecting the
# millisecond back is the mistake this block exists to prevent. This project is
# 25 fps, so one frame is 40ms and a clip put at 500ms sits at frame 12.5 — which
# does not exist. The export rounds it to frame 13 and the import reads frame 13
# as 520ms. Nothing is lost and nothing drifts: export it again and it is frame
# 13 again. Every NLE on earth does this; an editor that did not would be the
# broken one. So: whole frames, and within ONE frame of where it started.
FRAME_MS = 1000 / 25  # this project was made at 25 fps

def near(got, want):
    return abs(got - want) <= FRAME_MS


def on_a_frame(ms):
    return abs(round(ms / FRAME_MS) * FRAME_MS - ms) < 1


check("clip one is back at 0 for 2s",
      home[0]["start_ms"] == 0 and home[0]["duration_ms"] == 2000, str(home[0])[:120])
check("clip two follows it at 2s, to the frame",
      near(home[1]["start_ms"], 2000) and near(home[1]["duration_ms"], 1500),
      f'{home[1]["start_ms"]} / {home[1]["duration_ms"]}')
check("the clip on the upper row kept its row and its start, to the frame",
      home[2]["track"] == 1 and near(home[2]["start_ms"], 500), str(home[2])[:120])
check("every time that came back sits on a whole frame",
      all(on_a_frame(f["start_ms"]) and on_a_frame(f["duration_ms"]) for f in home),
      str([(f["start_ms"], f["duration_ms"]) for f in home]))
check("every returned clip points at a real upload",
      all(f["src"].get("upload_id") for f in home), str([f["src"] for f in home]))
# ⚠ RELATIVE ROWS. The client re-bases these onto rows it creates, so the
# server must never hand back the row numbers the project happens to use today.
check("the rows handed back start at 0", {f["track"] for f in home} == {0, 1},
      str({f["track"] for f in home}))

# --- the XML alone: the cut survives, the pictures become labelled gaps ----
with zipfile.ZipFile(_io.BytesIO(exported.content)) as zf:
    xml_only_bytes = zf.read("API Test Film.xml")
bare = api.post(
    f"/animatics/{pid}/interchange/import",
    files={"document": ("API Test Film.xml", xml_only_bytes, "application/xml")},
    headers=bearer(MINE),
)
check("an XML with no media still imports", bare.status_code == 200, bare.text[:200])
alone = bare.json() if bare.status_code == 200 else {}
check("…the cut is whole", alone.get("clips") == 3, str(alone.get("clips")))
check("…every clip is a labelled placeholder", len(alone.get("placeholders") or []) == 3,
      str(alone.get("placeholders")))
check("…drawn as colour cards, not left out",
      all(f["kind"] == "color" for f in alone.get("frames") or []),
      str([f["kind"] for f in alone.get("frames") or []]))
check("…keeping their names",
      any("Shot" in (f.get("label") or "") or "One" in (f.get("label") or "")
          for f in alone.get("frames") or []),
      str([f.get("label") for f in alone.get("frames") or []]))
check("…and their places, to the frame",
      all(any(near(got, want) for want in (0, 500, 2000))
          for got in (f["start_ms"] for f in alone.get("frames") or [])),
      str(sorted(f["start_ms"] for f in alone.get("frames") or [])))

# --- an EDL, which never says its rate -------------------------------------
edl_bytes = api.get(
    f"/animatics/{pid}/interchange?format=edl&media=false", headers=bearer(MINE)
).content
from_edl = api.post(
    f"/animatics/{pid}/interchange/import",
    files={"document": ("cut.edl", edl_bytes, "text/plain")},
    headers=bearer(MINE),
)
check("an EDL imports", from_edl.status_code == 200, from_edl.text[:200])
listed = from_edl.json() if from_edl.status_code == 200 else {}
check("…recognised as an EDL", listed.get("reader") == "edl", str(listed.get("reader")))
# ⚠ THE RATE WARNING IS NOT OPTIONAL. An EDL states drop/non-drop and never its
# fps, so it is read at the project's rate — a user whose list was cut at 25 has
# no other way to find out why everything is 4% long.
check("…and it says out loud what rate it guessed",
      any("frame rate" in w for w in listed.get("warnings") or []),
      str(listed.get("warnings")))
check("…on one video row, as an EDL always is", listed.get("video_tracks") == 1,
      str(listed.get("video_tracks")))

# --- media attached by hand, matched by name -------------------------------
_buf = _io.BytesIO()
Image.new("RGB", (320, 180), (9, 200, 9)).save(_buf, "PNG")
named = api.post(
    f"/animatics/{pid}/interchange/import",
    files=[
        ("document", ("API Test Film.xml", xml_only_bytes, "application/xml")),
        # The XML names its pictures `img_<id>.png`; this is deliberately a
        # DIFFERENT id, so it must NOT match — the check below is that matching
        # is by name and not "the first picture you gave me".
        ("media", ("not_in_the_xml.png", _buf.getvalue(), "image/png")),
    ],
    headers=bearer(MINE),
)
check("attached media that matches nothing changes nothing",
      named.status_code == 200 and len(named.json().get("placeholders") or []) == 3,
      named.text[:200])

# --- the refusals ----------------------------------------------------------
# ⚠ A REFUSAL IS A FEATURE. `.prproj` and `.aep` are the two files a user reaches
# for first, and both are undocumented private formats — a sentence naming what
# to export instead is a route somebody can walk; a half-read timeline is not.
import gzip as _gzip  # noqa: E402

for label, filename, blob, expect in (
    ("a .prproj", "Sequence.prproj", _gzip.compress(b"<?xml version='1.0'?><PremiereData/>"),
     "Final Cut Pro XML"),
    ("a Final Cut X .fcpxml", "Seq.fcpxml", b"<?xml version='1.0'?><fcpxml version='1.11'/>",
     "Final Cut Pro X"),
    ("junk", "notes.txt", b"this is not a project file at all", "project file"),
):
    r = api.post(
        f"/animatics/{pid}/interchange/import",
        files={"document": (filename, blob, "application/octet-stream")},
        headers=bearer(MINE),
    )
    check(f"{label} is refused", r.status_code == 415, str(r.status_code))
    check(f"…and the refusal says what to do instead",
          expect.lower() in (r.json().get("detail") or "").lower(), r.text[:160])

# --- and it is not a way into somebody else's project ----------------------
r = api.post(
    f"/animatics/{pid}/interchange/import",
    files={"document": ("API Test Film.xml", xml_only_bytes, "application/xml")},
    headers=bearer(THEIRS),
)
check("a stranger cannot import into this project", r.status_code in (403, 404),
      str(r.status_code))


# --- an empty timeline is refused, not a broken file ------------------------
# ⚠ EMPTIED RATHER THAN NEWLY CREATED. `POST /animatics` is behind
# `require_quota("projects")`, so a second project is a plan question and not
# this feature's; emptying the one we have asks the same thing of the route.
api.put(f"/animatics/{pid}", json={"frames": [], "audio_tracks": []}, headers=bearer(MINE))
r = api.get(f"/animatics/{pid}/interchange", headers=bearer(MINE))
check("an empty project is refused with a sentence", r.status_code == 409, str(r.status_code))
check("…and the sentence is a human one",
      "timeline" in (r.json().get("detail") or "").lower(), r.text[:120])


# ---------------------------------------------------------------------------
# 8c · The .prproj guess — the one reader with no specification behind it
# ---------------------------------------------------------------------------
# ⚠ THIS SECTION CANNOT PROVE THE READER IS RIGHT, AND SAYING SO IS THE POINT.
# Every other reader here is measured against a format with a published spec, or
# against this app's own writer via the round trip. A `.prproj` has neither:
# Adobe has never documented it, there is no writer to round-trip against, and
# the fixture below is a HAND-BUILT IMITATION of the object graph Premiere
# writes. So a green run here means "the reader behaves the way it claims to on
# the shape it claims to read" — NOT "it opens your Premiere project".
#
# What it therefore tests is the part that IS knowable, and the part that would
# hurt someone if it broke:
#
#   1. The refusal is still the default. Same bytes, no flag → 415. The reliable
#      route (export a Final Cut Pro XML) must never stop being the first answer.
#   2. It says it is a guess, at the top of `warnings`, every single time.
#   3. Ticks → frames uses the file's OWN rate, not the caller's.
#   4. The flat fallback does not count every clip twice — see the note there;
#      the file really does contain two elements per clip that a naive scan hits.
#   5. A gzip bomb is refused. The route caps the COMPRESSED upload, so a cap on
#      what it unpacks to can only live in the reader.
print("\n8c · the .prproj guess")

_TICKS = interchange.PRPROJ_TICKS_PER_SECOND


def _ticks(seconds):
    return int(round(seconds * _TICKS))


def _pr_clip(oid, name, path, start, end, inpoint=0, media="Video"):
    """One clip, in the five-object chain Premiere builds a clip out of.

    ⚠ THE BACK-POINTER ON `TrackItem` IS DELIBERATE. Premiere's objects reference
    the track they sit on as well as the other way round, which is what makes a
    naive walk out of one clip reach every other clip in the sequence. It is in
    the fixture so `_prproj_is_timeline` has something real to stop.
    """
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return f"""
  <{media}ClipTrackItem ObjectID="{oid}" ClassID="aab0946b" Version="1">
    <ClipTrackItem ObjectRef="{oid + 1}"/>
  </{media}ClipTrackItem>
  <ClipTrackItem ObjectID="{oid + 1}" ClassID="8a05c3d7" Version="1">
    <TrackItem ObjectRef="{oid + 2}"/>
    <SubClip ObjectRef="{oid + 3}"/>
  </ClipTrackItem>
  <TrackItem ObjectID="{oid + 2}" ClassID="d17d1d1a" Version="1">
    <Start>{start}</Start>
    <End>{end}</End>
    <Disabled>false</Disabled>
    <Track ObjectRef="30"/>
  </TrackItem>
  <SubClip ObjectID="{oid + 3}" ClassID="62f4ee9f" Version="1">
    <Name>{name}</Name>
    <Clip ObjectRef="{oid + 4}"/>
  </SubClip>
  <Clip ObjectID="{oid + 4}" ClassID="1c31d4c6" Version="1">
    <InPoint>{inpoint}</InPoint>
    <OutPoint>{inpoint + (end - start)}</OutPoint>
    <Source ObjectRef="{oid + 5}"/>
  </Clip>
  <MasterClip ObjectID="{oid + 5}" ClassID="1a0eb51b" Version="1">
    <Name>{base}</Name>
    <Media ObjectRef="{oid + 6}"/>
  </MasterClip>
  <Media ObjectID="{oid + 6}" ClassID="8d5b5d09" Version="1">
    <ActualMediaFilePath>{path}</ActualMediaFilePath>
    <FilePath>{path}</FilePath>
  </Media>"""


def _pr_transition(oid, start, end):
    return f"""
  <VideoTransitionTrackItem ObjectID="{oid}" ClassID="f7b0b0e2" Version="1">
    <TransitionTrackItem ObjectRef="{oid + 1}"/>
  </VideoTransitionTrackItem>
  <TransitionTrackItem ObjectID="{oid + 1}" ClassID="aa11bb22" Version="1">
    <TrackItem ObjectRef="{oid + 2}"/>
  </TransitionTrackItem>
  <TrackItem ObjectID="{oid + 2}" ClassID="d17d1d1a" Version="1">
    <Start>{start}</Start><End>{end}</End>
  </TrackItem>"""


# Two picture rows, one sound row, a dissolve over the cut at 2s, and a clip
# with an in point — the same film shape the rest of this file uses, so the
# numbers below can be read against section 8b's.
PRPROJ_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <Project ObjectID="1" ClassID="62ad66dd" Version="43">
    <Node Version="1"><Sequence ObjectRef="10"/></Node>
  </Project>
  <Sequence ObjectID="10" ClassID="6a4a9d4a" Version="61">
    <Name>Cut 03</Name>
    <TrackGroups>
      <TrackGroup Version="1"><Second ObjectRef="20"/></TrackGroup>
      <TrackGroup Version="1"><Second ObjectRef="40"/></TrackGroup>
    </TrackGroups>
  </Sequence>
  <VideoTrackGroup ObjectID="20" ClassID="1d7fbd0a" Version="1">
    <TrackGroup ObjectRef="21"/>
  </VideoTrackGroup>
  <TrackGroup ObjectID="21" ClassID="c0b0e3f1" Version="1">
    <VideoFrameRate>{_TICKS // 24}</VideoFrameRate>
    <Tracks><Track Index="0" ObjectRef="30"/><Track Index="1" ObjectRef="31"/></Tracks>
  </TrackGroup>
  <VideoClipTrack ObjectID="30" ClassID="aaa1" Version="1"><ClipTrack ObjectRef="32"/></VideoClipTrack>
  <ClipTrack ObjectID="32" ClassID="bbb1" Version="1">
    <ClipItems>
      <TrackItems Index="0" ObjectRef="100"/>
      <TrackItems Index="1" ObjectRef="110"/>
    </ClipItems>
    <Transitions><TrackItems ObjectRef="300"/></Transitions>
  </ClipTrack>
  <VideoClipTrack ObjectID="31" ClassID="aaa1" Version="1"><ClipTrack ObjectRef="33"/></VideoClipTrack>
  <ClipTrack ObjectID="33" ClassID="bbb1" Version="1">
    <ClipItems><TrackItems Index="0" ObjectRef="120"/></ClipItems>
  </ClipTrack>
  <AudioTrackGroup ObjectID="40" ClassID="2d7fbd0a" Version="1">
    <TrackGroup ObjectRef="41"/>
  </AudioTrackGroup>
  <TrackGroup ObjectID="41" ClassID="c0b0e3f1" Version="1">
    <Tracks><Track Index="0" ObjectRef="50"/></Tracks>
  </TrackGroup>
  <AudioClipTrack ObjectID="50" ClassID="aaa2" Version="1"><ClipTrack ObjectRef="51"/></AudioClipTrack>
  <ClipTrack ObjectID="51" ClassID="bbb1" Version="1">
    <ClipItems><TrackItems Index="0" ObjectRef="200"/></ClipItems>
  </ClipTrack>
{_pr_clip(100, "Shot One", r"C:\Footage\shot_one.png", 0, _ticks(2))}
{_pr_clip(110, "Shot Two", r"C:\Footage\shot_two.mp4", _ticks(2), _ticks(3.5), inpoint=_ticks(1))}
{_pr_clip(120, "Overlay", r"C:\Footage\logo.png", _ticks(0.5), _ticks(1.5))}
{_pr_clip(200, "Voice", r"C:\Footage\vo.wav", 0, _ticks(3.5), media="Audio")}
{_pr_transition(300, _ticks(1.75), _ticks(2.25))}
</PremiereData>
"""
PRPROJ = _gzip.compress(PRPROJ_XML.encode("utf-8"))

# --- it is recognised, compressed or not -----------------------------------
check("a gzipped .prproj is recognised by its bytes",
      interchange.detect_format(PRPROJ, "Cut 03.prproj") == "prproj",
      interchange.detect_format(PRPROJ, "Cut 03.prproj"))
# ⚠ AND UNCOMPRESSED, UNDER THE WRONG NAME. Premiere can be told to save
# uncompressed and asset pipelines unpack the file on the way through — without
# the root-element sniff, one of those reads as `unknown` and gets the wrong
# refusal ("that doesn't look like a project file") instead of the useful one.
check("an UNCOMPRESSED .prproj is recognised even named .xml",
      interchange.detect_format(PRPROJ_XML.encode("utf-8"), "sequence.xml") == "prproj",
      interchange.detect_format(PRPROJ_XML.encode("utf-8"), "sequence.xml"))

# --- the refusal is still the default --------------------------------------
# ⚠ THE SAME BYTES, TWICE, WITH ONE FLAG BETWEEN THEM. This is the check that
# stops the experiment quietly becoming the normal path: the reliable route has
# to be what an unflagged read is told about.
try:
    interchange.read_document(PRPROJ, "Cut 03.prproj", fps_hint=25)
    check("an unflagged .prproj is still refused", False, "it was read")
except interchange.ImportRefused as exc:
    check("an unflagged .prproj is still refused", True)
    check("…and the refusal still names Final Cut Pro XML first",
          "final cut pro xml" in str(exc).lower(), str(exc)[:120])
    check("…and mentions that it can be tried anyway",
          "anyway" in str(exc).lower(), str(exc)[:160])

got = interchange.read_document(PRPROJ, "Cut 03.prproj", fps_hint=25, experimental=True)

# --- it says it is a guess, first, every time ------------------------------
check("the flagged read comes back as 'prproj'", got["reader"] == "prproj", got["reader"])
check("…by the structured route", got.get("route") == "structured", str(got.get("route")))
check("…and the FIRST warning is that it is a best-effort read",
      "best-effort" in (got["warnings"][0] or "").lower(), (got["warnings"] or [""])[0][:100])
check("…which also names the route that always works",
      "final cut pro xml" in (got["warnings"][0] or "").lower(), got["warnings"][0][:160])
check("…and a second warning lists what was not read at all",
      any("effects" in w.lower() and "colour" in w.lower() for w in got["warnings"]),
      str(got["warnings"])[:200])

# --- the rate is the FILE's, not the caller's ------------------------------
# ⚠ 24 WAS ASKED FOR AS 25. A reader that used the hint would put every cut 4%
# adrift and nothing on screen would say why — the fixture is 24fps and the
# caller passed 25 precisely so a fallback cannot pass as a read.
check("the frame rate came out of the file, not the hint", got["fps"] == 24, str(got["fps"]))
check("2s at 24fps is frame 48, not 50",
      got["video"][0]["clips"][0]["end"] == 48,
      str(got["video"][0]["clips"][0]))
check("ticks convert half AWAY from zero, like every other clock here",
      interchange.prproj_ticks_to_frames(_TICKS // 48, 24) == 1
      and interchange.prproj_ticks_to_frames(0, 24) == 0,
      str(interchange.prproj_ticks_to_frames(_TICKS // 48, 24)))

# --- the cut itself ---------------------------------------------------------
check("both picture rows survived", len(got["video"]) == 2, str(len(got["video"])))
check("…in the order the sequence had them",
      [c["name"] for c in got["video"][0]["clips"]] == ["Shot One", "Shot Two"]
      and [c["name"] for c in got["video"][1]["clips"]] == ["Overlay"],
      str([[c["name"] for c in l["clips"]] for l in got["video"]]))
check("the second clip kept its in point (1s in, at 24fps = frame 24)",
      got["video"][0]["clips"][1]["in"] == 24, str(got["video"][0]["clips"][1]["in"]))
check("the sound row survived", len(got["audio"]) == 1 and len(got["audio"][0]["clips"]) == 1,
      str(got["audio"]))
check("the dissolve was found", len(got["video"][0]["transitions"]) == 1,
      str(got["video"][0]["transitions"]))
check("the sequence kept its name", got["name"] == "Cut 03", got["name"])
# Media is named by the BASENAME of a path from somebody else's computer, which
# is the only part of it this server could ever match against.
check("media is named by its basename, not the Windows path it came from",
      sorted(f["name"] for f in got["files"].values())
      == ["logo.png", "shot_one.png", "shot_two.mp4", "vo.wav"],
      str(sorted(f["name"] for f in got["files"].values())))

# --- and it lands on the timeline as this app's own clips -------------------
placed = interchange.to_project(got, lambda _n: None, background="#101010",
                                new_id=lambda: "x")
check("through to_project it is three picture clips",
      placed["report"]["clips"] == 3, str(placed["report"]["clips"]))
check("…at 0ms, 2000ms and 500ms",
      sorted(f["start_ms"] for f in placed["frames"]) == [0, 500, 2000],
      str(sorted(f["start_ms"] for f in placed["frames"])))
# ⚠ THE DISSOLVE HAD TO FIND ITS CUT. It is anchored in the file by a POSITION
# and in this app by the id of the clip it comes after; if that matching breaks,
# the import silently loses every transition.
check("…and the dissolve found the cut it sits over",
      len(placed["transitions"]) == 1
      and placed["transitions"][0]["duration_ms"] == 500,
      str(placed["transitions"]))

# --- the flat fallback ------------------------------------------------------
# ⚠ THE DOUBLE-COUNT THIS PROVES IS NOT HYPOTHETICAL. Premiere writes each clip
# as `VideoClipTrackItem` wrapping a plain `ClipTrackItem`, so BOTH tags end in
# "ClipTrackItem" and a plain scan for that suffix finds every clip twice — and
# puts the second copy on the picture row even when it is a sound, because the
# inner tag has no `Video`/`Audio` prefix to sort it by. The count below is
# asserted against the number a naive scan WOULD have returned.
_naive = PRPROJ_XML.count("ClipTrackItem ObjectID=")
FLAT_XML = PRPROJ_XML
for _oid in ("20", "21", "30", "31", "32", "33", "40", "41", "50", "51"):
    FLAT_XML = FLAT_XML.replace(f'ObjectID="{_oid}"', f'ObjectID="x{_oid}"')
flat = interchange.read_document(
    _gzip.compress(FLAT_XML.encode("utf-8")), "Cut 03.prproj", fps_hint=25, experimental=True
)
check("a file whose tracks make no sense still reads, by the flat route",
      flat.get("route") == "flat", str(flat.get("route")))
check("…and says out loud that the rows are wrong",
      any("row" in w.lower() and "order" in w.lower() for w in flat["warnings"]),
      str(flat["warnings"])[-220:])
check("…with every clip once, though a naive scan would find each twice",
      _naive == 8 and sum(len(l["clips"]) for l in flat["video"]) == 3
      and sum(len(l["clips"]) for l in flat["audio"]) == 1,
      f"naive={_naive} video={sum(len(l['clips']) for l in flat['video'])}")
check("…on one picture row, as a flat read always is", len(flat["video"]) == 1,
      str(len(flat["video"])))

# --- the things it must refuse even when asked nicely ----------------------
# ⚠ THE ROUTE CAPS THE UPLOAD, WHICH IS THE **COMPRESSED** SIZE. XML of this
# shape packs at better than 1000:1, so a few megabytes of upload unpacks to
# gigabytes — a cap inside the reader is the only thing standing there.
_bomb = _gzip.compress(b"<PremiereData>" + (b"<Filler>x</Filler>" * 40) * 200_000)
try:
    interchange.read_document(_bomb, "huge.prproj", fps_hint=25, experimental=True)
    check("a gzip bomb is refused rather than unpacked", False, "it unpacked")
except interchange.ImportRefused as exc:
    check("a gzip bomb is refused rather than unpacked", True)
    check("…with a sentence about the size", "MB" in str(exc), str(exc)[:120])
check("…while a real one of a few kB is not caught by that cap",
      len(PRPROJ) < 64 * 1024, str(len(PRPROJ)))

for _label, _blob, _expect in (
    ("an empty Premiere file", _gzip.compress(b"<?xml version='1.0'?><PremiereData/>"),
     "final cut pro xml"),
    ("a .prproj that is not XML inside", _gzip.compress(b"not xml at all"),
     "final cut pro xml"),
    ("a .prproj that is not gzip at all", b"\x1f\x8bbroken-not-really-gzip",
     "unpacked"),
):
    try:
        interchange.read_document(_blob, "x.prproj", fps_hint=25, experimental=True)
        check(f"{_label} is refused even when asked to guess", False, "it was read")
    except interchange.ImportRefused as exc:
        check(f"{_label} is refused even when asked to guess", True)
        check(f"…and the refusal is a human sentence", _expect in str(exc).lower(),
              str(exc)[:140])

# --- through the API --------------------------------------------------------
_files = {"document": ("Cut 03.prproj", PRPROJ, "application/octet-stream")}
r = api.post(f"/animatics/{pid}/interchange/import", files=_files, headers=bearer(MINE))
check("the API refuses a .prproj by default", r.status_code == 415, str(r.status_code))
r = api.post(
    f"/animatics/{pid}/interchange/import",
    files=_files, data={"experimental": "true"}, headers=bearer(MINE),
)
check("…and reads it when asked to guess", r.status_code == 200, r.text[:200])
guess = r.json() if r.status_code == 200 else {}
check("…reported as a .prproj so the dialog can badge it",
      guess.get("reader") == "prproj", str(guess.get("reader")))
check("…at the file's own 24fps, not this project's 25",
      guess.get("fps") == 24, str(guess.get("fps")))
check("…with the guess warning first, where the dialog prints it",
      "best-effort" in ((guess.get("warnings") or [""])[0]).lower(),
      str((guess.get("warnings") or [""])[0])[:100])
check("…three clips on two rows and one dissolve",
      guess.get("clips") == 3 and guess.get("video_tracks") == 2
      and guess.get("transitions_read") == 1,
      f'{guess.get("clips")}/{guess.get("video_tracks")}/{guess.get("transitions_read")}')
check("…and it saved nothing, like every other import",
      len(api.get(f"/animatics/{pid}", headers=bearer(MINE)).json()["frames"]) == 0, "")



# --- the rate belongs to the SEQUENCE, not to the file ---------------------
# ⚠ THIS ONE WAS A REAL BUG AND IT WAS SILENT. Premiere keeps the rate on a
# track-group object several hops from the sequence, so the first version looked
# only at the sequence and the objects it points STRAIGHT at, found nothing, and
# fell back to "the first rate anywhere in the file". It picked the right
# sequence and then read it at another sequence's rate — every clip 25% adrift,
# with nothing on screen saying why. Sequence A is deliberately FIRST in the file
# and at a DIFFERENT rate, so a reader that searches the document instead of the
# sequence cannot pass.
TWO_SEQ_XML = f"""<?xml version="1.0"?>
<PremiereData Version="3">
  <Sequence ObjectID="10"><Name>Seq A</Name>
    <TrackGroups><TrackGroup><Second ObjectRef="20"/></TrackGroup></TrackGroups>
  </Sequence>
  <VideoTrackGroup ObjectID="20"><TrackGroup ObjectRef="21"/></VideoTrackGroup>
  <TrackGroup ObjectID="21">
    <VideoFrameRate>{_TICKS // 30}</VideoFrameRate>
    <Tracks><Track Index="0" ObjectRef="30"/></Tracks>
  </TrackGroup>
  <VideoClipTrack ObjectID="30"><ClipTrack ObjectRef="31"/></VideoClipTrack>
  <ClipTrack ObjectID="31"><ClipItems><TrackItems ObjectRef="100"/></ClipItems></ClipTrack>

  <Sequence ObjectID="11"><Name>Seq B</Name>
    <TrackGroups><TrackGroup><Second ObjectRef="40"/></TrackGroup></TrackGroups>
  </Sequence>
  <VideoTrackGroup ObjectID="40"><TrackGroup ObjectRef="41"/></VideoTrackGroup>
  <TrackGroup ObjectID="41">
    <VideoFrameRate>{_TICKS // 24}</VideoFrameRate>
    <Tracks><Track Index="0" ObjectRef="50"/></Tracks>
  </TrackGroup>
  <VideoClipTrack ObjectID="50"><ClipTrack ObjectRef="51"/></VideoClipTrack>
  <ClipTrack ObjectID="51"><ClipItems>
    <TrackItems ObjectRef="110"/><TrackItems ObjectRef="120"/><TrackItems ObjectRef="130"/>
  </ClipItems></ClipTrack>
{_pr_clip(100, "A one", r"C:\\F\\a1.png", 0, _ticks(1))}
{_pr_clip(110, "B one", r"C:\\F\\b1.png", 0, _ticks(1))}
{_pr_clip(120, "B two", r"C:\\F\\b2.png", _ticks(1), _ticks(2))}
{_pr_clip(130, "B three", r"C:\\F\\b3.png", _ticks(2), _ticks(3))}
</PremiereData>
"""
two = interchange.read_document(
    _gzip.compress(TWO_SEQ_XML.encode("utf-8")), "Two.prproj", fps_hint=25, experimental=True
)
check("the busiest sequence is the one imported", two["name"] == "Seq B", two["name"])
check("…with its three clips", sum(len(l["clips"]) for l in two["video"]) == 3,
      str(sum(len(l["clips"]) for l in two["video"])))
check("…AT ITS OWN RATE, not the rate of the sequence above it in the file",
      two["fps"] == 24, f'{two["fps"]} (24 = Seq B, 30 = Seq A, 25 = the hint)')
check("…and the user is told a sequence was chosen for them",
      any("sequences" in w for w in two["warnings"]), str(two["warnings"])[-160:])

# A file with no rate anywhere falls back to the project's, and SAYS so.
NO_RATE = PRPROJ_XML.replace(f"<VideoFrameRate>{_TICKS // 24}</VideoFrameRate>", "")
norate = interchange.read_document(
    _gzip.compress(NO_RATE.encode("utf-8")), "x.prproj", fps_hint=25, experimental=True
)
check("a .prproj with no rate at all is read at this project's rate",
      norate["fps"] == 25, str(norate["fps"]))
check("…and says that is what it did",
      any("frame rate could not be found" in w for w in norate["warnings"]),
      str(norate["warnings"])[-160:])

# --- clips it found but could not place ------------------------------------
# ⚠ THE ROUTE'S OWN EMPTY-RESULT MESSAGE IS "There was nothing on that timeline
# to bring in", which is wrong twice over here: there WAS something, and the
# reason it did not arrive is this reader giving up, not the file being empty.
# ⚠ `<End>`, NOT `<Start>`. A missing or unreadable `<Start>` means ZERO — that
# is how Premiere really writes a clip at the head of a track, and treating it
# as unreadable threw away every clip that starts the film. `<End>` is the one
# a clip cannot be without.
BAD_TIMES = PRPROJ_XML
for _n in (_ticks(2), _ticks(3.5), _ticks(1.5), _ticks(2.25)):
    BAD_TIMES = BAD_TIMES.replace(f"<End>{_n}</End>", "<End>not-a-number</End>")
try:
    interchange.read_document(
        _gzip.compress(BAD_TIMES.encode("utf-8")), "x.prproj", fps_hint=25, experimental=True
    )
    check("clips found but none readable is refused, not reported as empty", False, "it was read")
except interchange.ImportRefused as exc:
    check("clips found but none readable is refused, not reported as empty", True)
    check("…and the sentence says the READER gave up, not that the file was empty",
          "none of their positions" in str(exc).lower(), str(exc)[:140])


# --- the shape a REAL Premiere file turned out to have ---------------------
# ⚠ **EVERYTHING ABOVE THIS LINE WAS WRITTEN AGAINST A GUESS, AND THE GUESS WAS
# WRONG.** The first live test — one real `.prproj` from Premiere 2026 — found
# 167 clips and could not place a single one, and the fixture above had been
# passing the whole time. That is the trap this file's own docstring names: a
# reader checked only against a hand-written fixture proves it can read that
# fixture. Three things were different in the real file, and each one on its own
# was fatal:
#
#   1. **`ObjectURef`.** Premiere names objects by a numeric `ObjectID` OR a GUID
#      `ObjectUID`, and points at them with `ObjectRef` OR `ObjectURef`. The link
#      from a track group to its TRACKS is the GUID kind — follow only the
#      numeric one and the timeline has no tracks at all.
#   2. **The times are NESTED, not referenced.** `<VideoClipTrackItem>` contains
#      `<ClipTrackItem>` contains `<TrackItem>` contains `<End>` — one element,
#      no references in between.
#   3. **A `<Start>` of zero is not written down.** Seventeen of the 167 had no
#      `<Start>`; demanding one throws away every clip at the head of a track.
#
# So this fixture is built to the real file's shape, and the one above is kept as
# the OTHER shape a Premiere version might write. Both are read.
_UID_A = "de531674-9015-4a3a-ad0d-9226d691ca65"
_UID_B = "46f7e0a6-89c0-4bd4-a37e-b218a406da14"


def _real_clip(oid, name, path, end, start=None, media="Video", inpoint=0):
    """A clip the way Premiere really writes one: everything nested, and no
    `<Start>` element at all when the clip begins at zero.

    ⚠ `<InPoint>` IS NESTED TOO, and this fixture used to get that wrong. Real
    Premiere writes `<AudioClip><Clip Version="18"><InPoint>` — the source window
    is a GRANDCHILD of the clip object, exactly as `<End>` is a grandchild of the
    track item. The fixture wrote it as a direct child and hard-coded it to 0, so
    it could not have caught a reader that never found it: see §8f."""
    start_el = f"<Start>{start}</Start>" if start is not None else ""
    return f"""
  <{media}ClipTrackItem ObjectID="{oid}" ClassID="aab0946b" Version="1">
    <ClipTrackItem Version="1">
      <ComponentOwner Version="1"><Components ObjectRef="{oid + 9}"/></ComponentOwner>
      <TrackItem Version="4">
        <Node Version="1"><Properties Version="1"/></Node>
        {start_el}<End>{end}</End>
      </TrackItem>
      <SubClip ObjectRef="{oid + 1}"/>
    </ClipTrackItem>
    <FrameRect>0,0,1920,1080</FrameRect>
  </{media}ClipTrackItem>
  <VideoComponentChain ObjectID="{oid + 9}" Version="1"><DefaultMotion>true</DefaultMotion></VideoComponentChain>
  <SubClip ObjectID="{oid + 1}" ClassID="62f4ee9f" Version="1">
    <Clip ObjectRef="{oid + 2}"/>
    <Name>{name}</Name>
  </SubClip>
  <{media}Clip ObjectID="{oid + 2}" ClassID="1c31d4c6" Version="1">
    <Clip Version="18">
      <Source ObjectRef="{oid + 3}"/>
      <ClipID>clip-{oid}</ClipID>
      <InPoint>{inpoint}</InPoint>
      <OutPoint>{inpoint + end - (start or 0)}</OutPoint>
    </Clip>
  </{media}Clip>
  <VideoMediaSource ObjectID="{oid + 3}" Version="1">
    <Media ObjectURef="media-uid-{oid}"/>
  </VideoMediaSource>
  <Media ObjectUID="media-uid-{oid}" Version="1">
    <ActualMediaFilePath>{path}</ActualMediaFilePath>
    <Title>{path.replace(chr(92), "/").rsplit("/", 1)[-1]}</Title>
  </Media>"""


# 23.976 fps, exactly as the real file: 254016000000 / 10594584000.
_NTSC_TICKS = 10594584000
REAL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <VideoTrackGroup ObjectID="260" ClassID="1d7fbd0a" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="{_UID_A}"/>
        <Track Index="1" ObjectURef="{_UID_B}"/>
      </Tracks>
    </TrackGroup>
    <FrameRate>{_NTSC_TICKS}</FrameRate>
    <FrameRect>0,0,1920,1080</FrameRect>
  </VideoTrackGroup>
  <VideoClipTrack ObjectUID="{_UID_A}" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><Track Version="1"><Index>0</Index></Track>
      <ClipItems Version="1"><TrackItems Version="1">
        <TrackItem Index="0" ObjectRef="700"/>
        <TrackItem Index="1" ObjectRef="720"/>
      </TrackItems></ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="{_UID_B}" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><Track Version="1"><Index>1</Index></Track>
      <ClipItems Version="1"><TrackItems Version="1">
        <TrackItem Index="0" ObjectRef="740"/>
      </TrackItems></ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <AudioTrackGroup ObjectID="261" ClassID="2d7fbd0a" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1"><Track Index="0" ObjectURef="audio-track-uid"/></Tracks>
    </TrackGroup>
    <FrameRate>5292000</FrameRate>
  </AudioTrackGroup>
  <AudioClipTrack ObjectUID="audio-track-uid" ClassID="aaa2" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1"><TrackItems Version="1">
        <TrackItem Index="0" ObjectRef="760"/>
        <TrackItem Index="1" ObjectRef="780"/>
        <TrackItem Index="2" ObjectRef="800"/>
      </TrackItems></ClipItems>
    </ClipTrack>
  </AudioClipTrack>
{_real_clip(700, "Opening", r"C:\Footage\open.png", end=_NTSC_TICKS * 48)}
{_real_clip(720, "Second", r"C:\Footage\two.mp4", end=_NTSC_TICKS * 120,
            start=_NTSC_TICKS * 48, inpoint=_NTSC_TICKS * 12)}
{_real_clip(740, "Overlay", r"C:\Footage\logo.png", end=_NTSC_TICKS * 24)}
{_real_clip(760, "Voice", r"C:\Footage\vo.wav", end=_NTSC_TICKS * 24, media="Audio")}
{_real_clip(780, "Voice", r"C:\Footage\vo.wav", end=_NTSC_TICKS * 72,
            start=_NTSC_TICKS * 24, media="Audio", inpoint=_NTSC_TICKS * 36)}
{_real_clip(800, "Voice", r"C:\Footage\vo.wav", end=_NTSC_TICKS * 120,
            start=_NTSC_TICKS * 72, media="Audio", inpoint=_NTSC_TICKS * 96)}
</PremiereData>
"""

real = interchange.read_document(
    _gzip.compress(REAL_XML.encode("utf-8")), "Real.prproj", fps_hint=25, experimental=True
)
# ⚠ STRUCTURED, NOT FLAT. The whole failure was that a perfectly readable
# timeline fell through to the flat route, so this is the assertion that would
# have caught it.
check("a real-shaped .prproj reads by the STRUCTURED route, not the flat one",
      real.get("route") == "structured", str(real.get("route")))
check("…with no <Sequence> object anywhere in it, which a real one has none of",
      "<Sequence" not in REAL_XML, "")
check("…its tracks reached through ObjectURef, not ObjectRef",
      len(real["video"]) == 2, f'{len(real["video"])} picture rows')
check("…and its sound row too", len(real["audio"]) == 1, str(len(real["audio"])))
check("…every clip placed, none skipped",
      sum(len(l["clips"]) for l in real["video"]) == 3
      and sum(len(l["clips"]) for l in real["audio"]) == 3,
      str([[c["name"] for c in l["clips"]] for l in real["video"]]))
# ⚠ THE CLIP WITH NO <Start> ELEMENT. Premiere omits it at zero; the first
# version treated that as "no position" and threw the clip away.
check("a clip with NO <Start> element starts at zero, not nowhere",
      real["video"][0]["clips"][0]["start"] == 0
      and real["video"][0]["clips"][0]["name"] == "Opening",
      str(real["video"][0]["clips"][0]))
check("…and the one after it keeps the start it does have",
      real["video"][0]["clips"][1]["start"] == 48,
      str(real["video"][0]["clips"][1]["start"]))
check("times come out of the NESTED TrackItem, so the lengths are right",
      real["video"][0]["clips"][0]["end"] == 48
      and real["video"][0]["clips"][1]["end"] == 120,
      str([(c["start"], c["end"]) for c in real["video"][0]["clips"]]))
# ⚠ 23.976, WHICH THIS APP CANNOT HOLD. Read as 24 and NAMED — the audio group's
# own 48000 "FrameRate" beside it must not be mistaken for a picture rate.
check("an NTSC 23.976 sequence is read as 24 and said out loud",
      real["fps"] == 24 and any("23.976" in w for w in real["warnings"]),
      f'{real["fps"]} / {[w for w in real["warnings"] if "NTSC" in w]}')
check("…and the sound track's 48000 is not mistaken for a frame rate",
      real["fps"] == 24, str(real["fps"]))
check("media is found through ObjectURef, by basename",
      sorted(f["name"] for f in real["files"].values())
      == ["logo.png", "open.png", "two.mp4", "vo.wav"],
      str(sorted(f["name"] for f in real["files"].values())))


# ---------------------------------------------------------------------------
# 8f · ONE FILE, MANY CLIPS — the source in-point
# ---------------------------------------------------------------------------
# ⚠ THE FAULT THIS PINS IS INVISIBLE ON A TIMELINE OF WHOLE TAKES, which is
# why it shipped. `_prproj_int` reads a DIRECT child, and Premiere writes
# `<AudioClip><Clip Version="18"><InPoint>` — one level deeper, exactly as it
# nests `<End>` inside `<TrackItem>`. So every clip answered None, `to_project`
# read that as 0, and every clip played its file FROM THE BEGINNING.
#
# On the first real import that looked perfectly correct: four video clips, four
# separate files, every in-point genuinely 0. The same project's voiceover was
# ONE mp3 razored into 23 pieces with the silences cut out — and all 23
# restarted the recording. Reported as "har clip audio ka starting hi play ho
# raha hai... pura audio sunai nahi de raha".
#
# ⚠ SO THE ASSERTION IS ABOUT A FILE USED MORE THAN ONCE. One clip proves
# nothing here — its in-point is 0 whether the reader read it or invented it.
# The three voice clips below come off one file at RISING in-points with gaps
# between them, which is what a razored track is, and there is no value a reader
# that found nothing could return that would pass.
print("\n8f · one file cut into several clips")

_voice = real["audio"][0]["clips"]
check("a razored sound row keeps all of its pieces",
      len(_voice) == 3, str(len(_voice)))
# The reader's own units are FRAMES; `to_project` turns them into ms below.
check("…and each piece reads its own in point out of the NESTED <Clip>",
      [c["in"] for c in _voice] == [0, 36, 96],
      f'in points {[c["in"] for c in _voice]} — all zero means it found none')
check("…which RISE, because each piece is further into the recording",
      [c["in"] for c in _voice] == sorted(c["in"] for c in _voice), "")
check("a picture clip trimmed off its own head keeps that in point too",
      real["video"][0]["clips"][1]["in"] == 12,
      str(real["video"][0]["clips"][1]["in"]))

# …and through `to_project`, in the milliseconds the editor actually plays.
_cut = interchange.to_project(
    real,
    lambda n: {"kind": "audio" if n.endswith(".wav") else
               ("video" if n.endswith(".mp4") else "image"),
               "upload_id": "u", "duration_ms": 0},
    background="#000000", new_id=lambda: "x",
)
_tracks = _cut["audio_tracks"]
check("through to_project the sound clips carry an OFFSET, not a zero",
      [a["offset_ms"] for a in _tracks] == [0, 1500, 4000],
      str([a["offset_ms"] for a in _tracks]))
check("…each still placed where it was on the timeline",
      [a["start_ms"] for a in _tracks] == [0, 1000, 3000],
      str([a["start_ms"] for a in _tracks]))
check("…and playing only its own window of the file",
      [a["trim_ms"] for a in _tracks] == [1000, 2000, 2000],
      str([a["trim_ms"] for a in _tracks]))
# ⚠ A CLIP CANNOT PLAY PAST THE END OF WHAT IT SAYS IS THERE. `duration_ms`
# falls back to `offset + what plays` because this server has no audio decoder,
# and with the offset read as 0 that bound was short by the whole offset.
check("…with a file length that at least covers the window it reads",
      all(a["duration_ms"] >= a["offset_ms"] + a["trim_ms"] for a in _tracks),
      str([(a["duration_ms"], a["offset_ms"], a["trim_ms"]) for a in _tracks]))
check("the picture clip's in point survives as in_ms",
      [f["in_ms"] for f in _cut["frames"] if f["kind"] == "video"] == [500],
      str([f["in_ms"] for f in _cut["frames"] if f["kind"] == "video"]))

# ⚠ BOTH SHAPES, the rule E52 was written for. Section 8c's fixture writes
# `<Clip><InPoint>` as a DIRECT child — a shape another Premiere version could
# write, and the one this reader was first built against. Fixing the nested case
# by moving the search deeper must not lose the flat one.
check("the flat `<Clip><InPoint>` shape still reads (8c's fixture)",
      got["video"][0]["clips"][1]["in"] == 24,
      str(got["video"][0]["clips"][1]["in"]))


import base64 as _b64  # noqa: E402
import uuid as _uuid  # noqa: E402

import animatic_fonts  # noqa: E402

# ---------------------------------------------------------------------------
# 8g · THE LETTERING — a Premiere title is not an empty clip
# ---------------------------------------------------------------------------
# ⚠ **THIS APP TOLD USERS THEIR TITLES COULD NOT BE IMPORTED, FOR MONTHS, AND
# THE WORDS WERE IN THE FILE THE WHOLE TIME.** Every title, caption and lower
# third in a Premiere project is a clip NAMED "Graphic" with no media file, so
# reading only `<Name>` sees forty identical empty clips; the import duly made
# forty invisible placeholders and printed "any LETTERING they held has to be
# typed again with the Text tool". A real project of 43 graphics — 40 of them
# captions carrying a full voiceover script — was retyped on that advice.
#
# The words live on a `<VideoFilterComponent>` whose `<MatchName>` is
# `AE.ADBE Text`, in TWO places, and which one is trusted matters:
#
#   · `<InstanceName>` is Premiere naming the layer after its own text. Free to
#     read and STALE the moment somebody renames the layer.
#   · the `Source Text` parameter's base64 FlatBuffer is the text itself.
#
# So the fixtures below are built with the two DISAGREEING on purpose. A reader
# that takes the easy one passes nothing here.
print("\n8g · a Premiere title carries its words")


def _text_blob(text, font="Tahoma"):
    """A `Source Text` payload in the shape Premiere writes.

    ⚠ THE STRINGS ARE WHAT IS READ, AND NOTHING ELSE — `<uint32 length><bytes>
    <NUL>`, font first and text second. The floats and the vtable around them
    move from record to record (83 real ones were measured; no two agreed), so
    the padding here is deliberately NOT a valid FlatBuffer: if the reader ever
    starts needing one, this fixture is what tells you.
    """
    def _s(word):
        raw = word.encode("utf-8")
        return len(raw).to_bytes(4, "little") + raw + b"\x00"
    body = (b"\x58\x01\x00\x00\x00\x00\x00\x00" + b"\x44\x33\x22\x11"
            + b"\x0c\x00\x00\x00" + b"\x00" * 24 + _s(font) + _s(text)
            + b"\x34\x00\x0c\x00" + b"\x00" * 12)
    return _b64.b64encode(body).decode("ascii")


def _graphic_clip(oid, *, end, start=None, texts=(), shape=False,
                  name="Graphic", scale="50.", position="0.2797:0.5219",
                  instance=None, blob=True, master_text=None,
                  motion=None, transform=None):
    """One Premiere GRAPHIC track item, in the shape the real file writes.

    `texts` is one entry per text layer in the graphic — `()` for a shape or an
    Adjustment Layer, two entries for a Graphic Group.

    ⚠ `motion` AND `transform` ARE WHERE THE CLIP IS ON SCREEN, and `position`
    is only where the words sit INSIDE it. Each takes `(position, anchor)` as
    "x:y" strings — `AE.ADBE Motion` is the one every Premiere clip has, and
    `AE.ADBE Geometry2` is the Transform EFFECT somebody adds by hand. See §8l.
    """
    start_el = f"<Start>{start}</Start>" if start is not None else ""
    comps, extra, index = [], [], 0
    for match, pair in (("AE.ADBE Motion", motion), ("AE.ADBE Geometry2", transform)):
        if pair is None:
            continue
        where, anchor = pair
        tid = oid + (80 if match.endswith("Motion") else 90)
        comps.append(f'<Component Index="{index}" ObjectRef="{tid}"/>')
        extra.append(f"""
  <VideoFilterComponent ObjectID="{tid}" Version="9">
    <Component Version="7">
      <Params Version="1">
        <Param Index="0" ObjectRef="{tid + 1}"/>
        <Param Index="1" ObjectRef="{tid + 2}"/>
      </Params>
      <DisplayName>Motion</DisplayName>
    </Component>
    <MatchName>{match}</MatchName>
  </VideoFilterComponent>
  <PointComponentParam ObjectID="{tid + 1}" Version="4">
    <ParameterID>1</ParameterID><Name>Position</Name>
    <StartKeyframe>-91445760000000000,{where},0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>
  </PointComponentParam>
  <PointComponentParam ObjectID="{tid + 2}" Version="4">
    <ParameterID>2</ParameterID><Name>Anchor Point</Name>
    <StartKeyframe>-91445760000000000,{anchor},0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>
  </PointComponentParam>""")
        index += 1
    if shape:
        comps.append(f'<Component Index="{index}" ObjectRef="{oid + 30}"/>')
        extra.append(f'''
  <VideoFilterComponent ObjectID="{oid + 30}" Version="9">
    <Component Version="7"><Params Version="1"/><DisplayName>Shape</DisplayName></Component>
    <MatchName>AE.ADBE Shape</MatchName>
  </VideoFilterComponent>''')
        index += 1
    for n, body in enumerate(texts):
        cid = oid + 40 + n * 10
        shown = instance if instance is not None else body
        source = ""
        if blob and body:
            source = (f'<StartKeyframeValue Encoding="base64" '
                      f'BinaryHash="h-{cid}">{_text_blob(body)}</StartKeyframeValue>')
        comps.append(f'<Component Index="{index}" ObjectRef="{cid}"/>')
        extra.append(f'''
  <VideoFilterComponent ObjectID="{cid}" Version="9">
    <Component Version="7">
      <Params Version="1">
        <Param Index="0" ObjectRef="{cid + 1}"/>
        <Param Index="1" ObjectRef="{cid + 2}"/>
        <Param Index="2" ObjectRef="{cid + 3}"/>
      </Params>
      <DisplayName>Text</DisplayName>
      <InstanceName>{shown}</InstanceName>
    </Component>
    <MatchName>AE.ADBE Text</MatchName>
  </VideoFilterComponent>
  <ArbVideoComponentParam ObjectID="{cid + 1}" Version="3">
    <ParameterID>1</ParameterID><Name>Source Text</Name>{source}
  </ArbVideoComponentParam>
  <VideoComponentParam ObjectID="{cid + 2}" Version="10">
    <ParameterID>4</ParameterID><Name>Scale</Name>
    <StartKeyframe>-91445760000000000,{scale},0,0,0,0,0,0</StartKeyframe>
  </VideoComponentParam>
  <PointComponentParam ObjectID="{cid + 3}" Version="4">
    <ParameterID>3</ParameterID><Name>Position</Name>
    <StartKeyframe>-91445760000000000,{position},0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>
  </PointComponentParam>''')
        index += 1
    # ⚠ THE MASTER CLIP GETS A CHAIN OF ITS OWN, carrying the text the graphic
    # was FIRST made with. In the real project all 82 captions share one master
    # clip, so a reader that keeps descending answers with THIS string 82 times.
    master = ""
    if master_text is not None:
        master = f'''
  <VideoComponentChain ObjectID="{oid + 70}" Version="3">
    <ComponentChain Version="3"><Components Version="1">
      <Component Index="0" ObjectRef="{oid + 71}"/>
    </Components></ComponentChain>
  </VideoComponentChain>
  <VideoFilterComponent ObjectID="{oid + 71}" Version="9">
    <Component Version="7"><Params Version="1"/>
      <DisplayName>Text</DisplayName><InstanceName>{master_text}</InstanceName>
    </Component>
    <MatchName>AE.ADBE Text</MatchName>
  </VideoFilterComponent>'''
    master_ref = (f'<MasterClip ObjectRef="{oid + 70}"/>' if master_text is not None else "")
    return f"""
  <VideoClipTrackItem ObjectID="{oid}" ClassID="aab0946b" Version="1">
    <ClipTrackItem Version="1">
      <ComponentOwner Version="1"><Components ObjectRef="{oid + 9}"/></ComponentOwner>
      <TrackItem Version="4">
        <Node Version="1"><Properties Version="1"/></Node>
        {start_el}<End>{end}</End>
      </TrackItem>
      <SubClip ObjectRef="{oid + 1}"/>
    </ClipTrackItem>
    <FrameRect>0,0,1920,1080</FrameRect>
  </VideoClipTrackItem>
  <VideoComponentChain ObjectID="{oid + 9}" Version="3">
    <ComponentChain Version="3"><Components Version="1">
      {"".join(comps)}
    </Components></ComponentChain>
  </VideoComponentChain>{"".join(extra)}{master}
  <SubClip ObjectID="{oid + 1}" ClassID="62f4ee9f" Version="1">
    <Clip ObjectRef="{oid + 2}"/>{master_ref}
    <Name>{name}</Name>
  </SubClip>
  <VideoClip ObjectID="{oid + 2}" ClassID="1c31d4c6" Version="1">
    <Clip Version="18"><ClipID>clip-{oid}</ClipID><InPoint>0</InPoint></Clip>
  </VideoClip>"""


_T = _NTSC_TICKS
# ⚠ THE POSITION IS THE REAL ONE, off the reference project's first caption:
# left edge 0.2797, 39 characters, Scale 50. Everything about the geometry
# below is checked against where Premiere ACTUALLY put those words.
_CAPTION = "that needs to work with different tools"
LETTER_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <VideoTrackGroup ObjectID="260" ClassID="1d7fbd0a" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="lt-pictures"/>
        <Track Index="1" ObjectURef="lt-adjust"/>
        <Track Index="2" ObjectURef="lt-shapes"/>
        <Track Index="3" ObjectURef="lt-titles"/>
      </Tracks>
    </TrackGroup>
    <FrameRate>{_T}</FrameRate>
    <FrameRect>0,0,1920,1080</FrameRect>
  </VideoTrackGroup>
  <VideoClipTrack ObjectUID="lt-pictures" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><ClipItems Version="1"><TrackItems Version="1">
      <TrackItem Index="0" ObjectRef="700"/>
    </TrackItems></ClipItems></ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="lt-adjust" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><ClipItems Version="1"><TrackItems Version="1">
      <TrackItem Index="0" ObjectRef="1100"/>
    </TrackItems></ClipItems></ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="lt-shapes" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><ClipItems Version="1"><TrackItems Version="1">
      <TrackItem Index="0" ObjectRef="1200"/>
    </TrackItems></ClipItems></ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="lt-titles" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><ClipItems Version="1"><TrackItems Version="1">
      <TrackItem Index="0" ObjectRef="1300"/>
      <TrackItem Index="1" ObjectRef="1400"/>
      <TrackItem Index="2" ObjectRef="1500"/>
    </TrackItems></ClipItems></ClipTrack>
  </VideoClipTrack>
{_real_clip(700, "Opening", r"C:\\Footage\\open.png", end=_T * 240)}
{_graphic_clip(1100, end=_T * 240, name="Adjustment Layer")}
{_graphic_clip(1200, end=_T * 240, shape=True)}
{_graphic_clip(1300, end=_T * 48, texts=[_CAPTION],
               instance="renamed in Premiere", master_text="the ORIGINAL text")}
{_graphic_clip(1400, end=_T * 96, start=_T * 48, texts=["", "In simple words"],
               scale="72.", position="0.3408:0.5242")}
{_graphic_clip(1500, end=_T * 144, start=_T * 96, texts=["No blob here"],
               blob=False, instance="No blob here")}
</PremiereData>
"""

letters = interchange.read_document(
    _gzip.compress(LETTER_XML.encode("utf-8")), "Letters.prproj",
    fps_hint=24, experimental=True,
)
check("a .prproj of titles still reads by the STRUCTURED route",
      letters.get("route") == "structured", str(letters.get("route")))

_rows = letters["video"]
_titles = [c for row in _rows for c in row["clips"]
           if (c.get("graphic") or {}).get("kind") == "text"]
_words = [t["text"] for g in _titles for t in g["graphic"]["texts"]]
check("every title on the timeline is found — not one of them is an empty clip",
      len(_words) == 3, f"{len(_words)} titles: {_words}")

# --- WHICH of the two copies of the text is believed -----------------------
# ⚠ THE FIXTURE MAKES THEM DISAGREE. Clip 1300's `<InstanceName>` says "renamed
# in Premiere" and its blob says the caption. A reader taking the cheap one
# returns the layer NAME as somebody's subtitle.
check("the words come out of the Source Text BLOB, not the layer's name",
      _words[0] == _CAPTION, repr(_words[0]))
check("…and `<InstanceName>` is still the fallback when there is no blob",
      "No blob here" in _words, str(_words))

# --- the master clip must not answer ---------------------------------------
# ⚠ ALL 82 CAPTIONS IN THE REFERENCE PROJECT SHARE ONE MASTER CLIP. A walk that
# keeps descending finds the text the graphic was DUPLICATED FROM and returns it
# for every caption — the same sentence 82 times, which previews as a timeline
# that is full, correct-looking and completely wrong.
check("the MASTER clip's older text is never what comes back",
      "the ORIGINAL text" not in _words, str(_words))

# --- a Graphic Group whose first layer is empty ----------------------------
# ⚠ THIS ONE SHIPPED IN THE FIRST DRAFT AND WAS CAUGHT ON THE REAL FILE. Clip
# 1400 is a Graphic Group: an EMPTY text layer, then the words. Returning at the
# first text component found loses the caption and reports the clip as media.
check("a Graphic Group starting with an EMPTY text layer still gives its words",
      "In simple words" in _words, str(_words))

# --- where it lands --------------------------------------------------------
# ⚠ PREMIERE STORES THE LEFT EDGE OF THE LINE; THIS APP WANTS THE CENTRE. The
# numbers are the reference project's own: left 0.2797, 39 characters, Scale 50
# → size 45px → a centre that has to come back to 0.5, because that caption was
# centred in the frame. See `PRPROJ_TEXT_SIZE_PER_SCALE`.
_first = _titles[0]["graphic"]["texts"][0]
check("a title's SIZE comes from Premiere's Scale (50% → 45px at 1080p)",
      _first["size_px"] == 45.0, str(_first["size_px"]))
check("…and the left edge Premiere stores becomes the CENTRE this app draws at",
      abs(_first["x"] - 0.5) < 0.02, f'x={_first["x"]} (0.5 ± 0.02)')
# ⚠ AND THE VERTICAL IS AN EDGE TOO — see §8l. Premiere stores the BASELINE and
# this app draws from the block's CENTRE, so a caption at 0.5219 set in 45px type
# sits at 0.5219 - 0.36 × 45/1080. This assertion used to read "taken straight
# across", which was the same mistake as reading the left edge as a centre.
check("…and the BASELINE Premiere stores becomes the centre this app draws at",
      abs(_first["y"] - (0.5219 - 0.36 * 45 / 1080)) < 0.0002, str(_first["y"]))
_bigger = [t for g in _titles for t in g["graphic"]["texts"]
           if t["text"] == "In simple words"][0]
check("a title set larger in Premiere is larger here too (72% → 64.8px)",
      _bigger["size_px"] == 64.8, str(_bigger["size_px"]))
check("the font comes across, mapped onto a face this app actually ships",
      _first["font"] == "inter", _first["font"])
check("…and an unbundled face folds down instead of failing",
      interchange.prproj_font_id("Wingdings-Regular")
      == animatic_fonts.DEFAULT_FONT, interchange.prproj_font_id("Wingdings"))
check("…while a face this app DOES ship is matched, weight suffix and all",
      interchange.prproj_font_id("Montserrat-SemiBold") == "montserrat",
      interchange.prproj_font_id("Montserrat-SemiBold"))


# ---------------------------------------------------------------------------
# 8h · Every kind of Premiere row goes to the row of THIS app that holds it
# ---------------------------------------------------------------------------
# ⚠ **A PREMIERE ROW IS NOT A PICTURE ROW.** Three of the four rows in the
# fixture above carry no film — one Adjustment Layer, one shape, three titles —
# and the import used to turn every one of them into a picture row of clips with
# no file. On the real project that was four full-length invisible cards over the
# cut, reported as "audio, image and video show but text not show".
print("\n8h · each Premiere row lands on the row that holds it here")

placed = interchange.to_project(
    letters,
    lambda n: ({"kind": "image", "upload_id": "u", "duration_ms": 0}
               if n.lower().endswith(".png") else None),
    background="#000000", new_id=lambda: _uuid.uuid4().hex[:12],
)
_report = placed["report"]
check("the titles arrive as TEXT clips, not as frames",
      len(placed["texts"]) == 3, str(len(placed["texts"])))
check("…the shape as a shape",
      len(placed["shapes"]) == 1, str(len(placed["shapes"])))
check("…and only the row that holds a PICTURE becomes a picture row",
      _report["video_tracks"] == 1, str(_report["video_tracks"]))
# ⚠ THE NUMBERING IS THE POINT. Picture rows are addressed by NUMBER, so
# leaving the caption row's index in place hands the client "4 picture rows" and
# it draws three empty ones above the film.
check("…numbered from zero with no gap where the other rows were",
      sorted({f["track"] for f in placed["frames"]}) == [0],
      str(sorted({f["track"] for f in placed["frames"]})))
check("a row of stills is called a row of stills, so the client can name it",
      _report["video_lane_kinds"] == ["image"],
      str(_report["video_lane_kinds"]))

# --- the caption row is not ours to write to -------------------------------
# ⚠ `CAPTION_LAYER_ID` IS ✨ AUTO CAPTIONS' ROW AND IT IS REPLACED WHOLESALE ON
# EVERY RUN. An imported title parked there is destroyed by a transcription the
# user paid for, silently. The import mints its own lanes and this is what keeps
# it that way.
check("imported titles go to import lanes of their own",
      all(t["layer_id"].startswith(interchange.IMPORT_TEXT_LANE_PREFIX)
          for t in placed["texts"]),
      str({t["layer_id"] for t in placed["texts"]}))
check("…and NEVER to the row ✨ Auto captions owns",
      all(t["layer_id"] != interchange.IMPORT_CAPTION_LAYER_ID
          for t in placed["texts"]),
      str({t["layer_id"] for t in placed["texts"]}))
check("…nor to the default lane, which is the user's own row",
      all(t["layer_id"] for t in placed["texts"]), "")

# --- timing, which is the half that has to be exact ------------------------
check("a title is on screen exactly when Premiere had it",
      [(t["start_ms"], t["duration_ms"]) for t in placed["texts"]]
      == [(0, 2000), (2000, 2000), (4000, 2000)],
      str([(t["start_ms"], t["duration_ms"]) for t in placed["texts"]]))
check("…positioned freely, not flowed into a stack",
      all(t.get("place") == "free" for t in placed["texts"]),
      str([t.get("place") for t in placed["texts"]]))

# --- the two things that must NOT be invented ------------------------------
# ⚠ THE FILL COLOUR IS NOT IN A .prproj. It was looked for in a real project and
# is in none of the three places it could be — see `_prproj_text_style`. So the
# import uses this app's own default and SAYS SO. A future reader that starts
# guessing a colour has to change this line and read that docstring first.
check("no colour is invented for an imported title",
      {t["color"] for t in placed["texts"]} == {interchange.IMPORT_TEXT_COLOR},
      str({t["color"] for t in placed["texts"]}))
check("…and no black scrim bar is added to somebody's film either",
      {t["backdrop"] for t in placed["texts"]} == {"none"},
      str({t["backdrop"] for t in placed["texts"]}))
check("a shape stands in at zero opacity — its size and fill are unreadable",
      placed["shapes"][0]["opacity"] == 0.0, str(placed["shapes"][0]["opacity"]))

# --- the Adjustment Layer -------------------------------------------------
# Left out on purpose: it is an empty holder for colour effects this format does
# not carry, and importing it put a full-length clip over the film that did
# nothing. Counted and SAID, never silently dropped.
check("an Adjustment Layer is left out rather than made an invisible card",
      not any((f.get("label") or "") == "Adjustment Layer"
              for f in placed["frames"]),
      str([f.get("label") for f in placed["frames"]]))
check("…and the report says so in words",
      any("Adjustment Layer" in w for w in _report["warnings"]),
      str(_report["warnings"][-3:]))
check("the report no longer tells anyone to type their titles again",
      not any("typed again" in w for w in _report["warnings"]),
      str([w for w in _report["warnings"] if "typed" in w]))
check("…it says what came across and what did not",
      any("COLOUR is not stored in a .prproj" in w for w in _report["warnings"]),
      str(_report["warnings"][-3:]))
# ⚠ AND IT POINTS AT THE ROUTE THAT DOES CARRY THE COLOUR (§8j). Telling a user
# their title colours are gone, when a Final Cut Pro XML out of the same
# Premiere would have brought them, is a dead end this app put them in.
check("…and names the export that WOULD have carried the colours",
      any("Final Cut Pro XML" in w and "title" in w for w in _report["warnings"]),
      str([w for w in _report["warnings"] if "Final Cut" in w]))

# --- and none of this disturbed the reader that already worked -------------
# ⚠ THE REGRESSION GUARD FOR §8f. `_prproj_graphic` walks the same graph
# `_prproj_detail` does, on every clip; a file with no graphics in it at all must
# come out byte for byte as it did before.
_again = interchange.to_project(
    real,
    lambda n: {"kind": "audio" if n.endswith(".wav") else
               ("video" if n.endswith(".mp4") else "image"),
               "upload_id": "u", "duration_ms": 0},
    background="#000000", new_id=lambda: "x",
)
check("a .prproj with no graphics in it reads exactly as it did before",
      [a["offset_ms"] for a in _again["audio_tracks"]] == [0, 1500, 4000]
      and len(_again["frames"]) == 3 and not _again["texts"],
      f'{len(_again["frames"])} frames, {len(_again["texts"])} texts')
check("…and its two picture rows are still two picture rows",
      _again["report"]["video_tracks"] == 2,
      str(_again["report"]["video_tracks"]))


# ---------------------------------------------------------------------------
# 8i · The DROP SHADOW — the one part of a title's look a .prproj does give up
# ---------------------------------------------------------------------------
# ⚠ **THE FILL COLOUR IS NOT IN A `.prproj` AND THE SHADOW IS.** That asymmetry
# is not obvious and it was found the hard way, so it is pinned here rather than
# left in a comment: Premiere writes a colour as a 64-BIT INTEGER on a plain
# `<StartKeyframe>` — `18374686479671623680` is big-endian
# `ff 00 00 00 00 00 00 00`, four 16-bit channels A,R,G,B with the 8-bit value in
# the HIGH byte. Every component that OWNS a colour writes it that way (Shadow
# Color, Key Color, Tint's Map White To). The text FILL is owned by no component,
# which is why it is missing and the shadow is not.
#
# ⚠ AND IT IS NOT A RARE EXTRA. Across two real projects and two Premiere
# versions, **every one of 194 text clips carried a Drop Shadow in its own
# component chain** — it is simply how people set type in Premiere.
print("\n8i · a title's drop shadow comes across")

check("Premiere's packed 64-bit colour decodes to hex",
      interchange.prproj_colour("18374686479671623680") == "#000000",
      interchange.prproj_colour("18374686479671623680"))
check("…white too, which is 0xff00 per channel and NOT 0xffff",
      interchange.prproj_colour("18374966859414961920") == "#ffffff",
      interchange.prproj_colour("18374966859414961920"))
# ⚠ NEVER GUESS. A colour this cannot read must leave the app's own in place;
# returning black for junk would paint somebody's titles black and look
# deliberate.
check("…and anything that is not one of those comes back EMPTY, never black",
      [interchange.prproj_colour(v) for v in ("", "abc", "12.5", "-1", None)]
      == ["", "", "", "", ""],
      str([interchange.prproj_colour(v) for v in ("", "abc", "12.5", "-1", None)]))


def _shadow_xml(colour="18374686479671623680", opacity="249.999984741211",
                direction="147.", distance="11.", only="false"):
    """A text graphic with a Drop Shadow beside it, as Premiere writes one."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <VideoTrackGroup ObjectID="260" ClassID="1d7fbd0a" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1"><Track Index="0" ObjectURef="sh-titles"/></Tracks>
    </TrackGroup>
    <FrameRate>{_NTSC_TICKS}</FrameRate>
    <FrameRect>0,0,1920,1080</FrameRect>
  </VideoTrackGroup>
  <VideoClipTrack ObjectUID="sh-titles" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><ClipItems Version="1"><TrackItems Version="1">
      <TrackItem Index="0" ObjectRef="1300"/>
    </TrackItems></ClipItems></ClipTrack>
  </VideoClipTrack>
  <VideoClipTrackItem ObjectID="1300" ClassID="aab0946b" Version="1">
    <ClipTrackItem Version="1">
      <ComponentOwner Version="1"><Components ObjectRef="1309"/></ComponentOwner>
      <TrackItem Version="4"><Node Version="1"><Properties Version="1"/></Node>
        <End>{_NTSC_TICKS * 48}</End></TrackItem>
      <SubClip ObjectRef="1301"/>
    </ClipTrackItem>
  </VideoClipTrackItem>
  <VideoComponentChain ObjectID="1309" Version="3">
    <ComponentChain Version="3"><Components Version="1">
      <Component Index="0" ObjectRef="1320"/>
      <Component Index="1" ObjectRef="1340"/>
    </Components></ComponentChain>
  </VideoComponentChain>
  <VideoFilterComponent ObjectID="1320" Version="9">
    <Component Version="7">
      <Params Version="1">
        <Param Index="0" ObjectRef="1321"/><Param Index="1" ObjectRef="1322"/>
        <Param Index="2" ObjectRef="1323"/><Param Index="3" ObjectRef="1324"/>
        <Param Index="4" ObjectRef="1325"/>
      </Params>
      <DisplayName>Drop Shadow</DisplayName>
    </Component>
    <MatchName>AE.ADBE Drop Shadow</MatchName>
  </VideoFilterComponent>
  <VideoComponentParam ObjectID="1321" Version="10"><Name>Shadow Color</Name>
    <StartKeyframe>-91445760000000000,{colour},0,0,0,0,0,0</StartKeyframe></VideoComponentParam>
  <VideoComponentParam ObjectID="1322" Version="10"><Name>Opacity</Name>
    <StartKeyframe>-91445760000000000,{opacity},0,0,0,0,0,0</StartKeyframe></VideoComponentParam>
  <VideoComponentParam ObjectID="1323" Version="10"><Name>Direction</Name>
    <StartKeyframe>-91445760000000000,{direction},0,0,0,0,0,0</StartKeyframe></VideoComponentParam>
  <VideoComponentParam ObjectID="1324" Version="10"><Name>Distance</Name>
    <StartKeyframe>-91445760000000000,{distance},0,0,0,0,0,0</StartKeyframe></VideoComponentParam>
  <VideoComponentParam ObjectID="1325" Version="10"><Name>Shadow Only</Name>
    <StartKeyframe>-91445760000000000,{only},0,0,0,0,0,0</StartKeyframe></VideoComponentParam>
  <VideoFilterComponent ObjectID="1340" Version="9">
    <Component Version="7">
      <Params Version="1">
        <Param Index="0" ObjectRef="1341"/><Param Index="1" ObjectRef="1342"/>
      </Params>
      <DisplayName>Text</DisplayName><InstanceName>SHADOWED</InstanceName>
    </Component>
    <MatchName>AE.ADBE Text</MatchName>
  </VideoFilterComponent>
  <ArbVideoComponentParam ObjectID="1341" Version="3"><Name>Source Text</Name>
    <StartKeyframeValue Encoding="base64">{_text_blob("SHADOWED")}</StartKeyframeValue>
  </ArbVideoComponentParam>
  <VideoComponentParam ObjectID="1342" Version="10"><Name>Scale</Name>
    <StartKeyframe>-91445760000000000,100.,0,0,0,0,0,0</StartKeyframe></VideoComponentParam>
  <SubClip ObjectID="1301" ClassID="62f4ee9f" Version="1">
    <Clip ObjectRef="1302"/><Name>Graphic</Name>
  </SubClip>
  <VideoClip ObjectID="1302" ClassID="1c31d4c6" Version="1">
    <Clip Version="18"><InPoint>0</InPoint></Clip>
  </VideoClip>
</PremiereData>
"""


def _one_title(xml):
    got = interchange.read_document(
        _gzip.compress(xml.encode("utf-8")), "Shadow.prproj",
        fps_hint=24, experimental=True)
    return interchange.to_project(got, lambda n: None, new_id=lambda: "x")


shadowed = _one_title(_shadow_xml())["texts"][0]
check("a title's shadow COLOUR is read out of the packed integer",
      shadowed["shadow_color"] == "#000000", str(shadowed.get("shadow_color")))
# 249.99998 / 255 — ⚠ AND THE DIVISOR IS 255, NOT 100. A drop shadow's Opacity
# runs 0…255 in Premiere while a clip's runs 0…100; using 100 gives 2.45 and the
# model rejects it, losing the whole import to a 422.
check("…its strength, on Premiere's own 0-255 scale",
      abs(shadowed["shadow_opacity"] - 0.98) < 0.01,
      str(shadowed["shadow_opacity"]))
# ⚠ 147 IN PREMIERE IS 57 HERE. Premiere measures the angle from STRAIGHT UP,
# this app from the RIGHT. Reading it straight across tilts every shadow 90°,
# which looks like a rendering bug rather than an import one.
check("…its direction, turned into this app's own frame (147 up → 57 right)",
      shadowed["shadow_angle"] == 57.0, str(shadowed["shadow_angle"]))
# Distance 11px against a 90px title → 0.1222em. ⚠ The conversion needs the
# caption's SIZE, which is why it happens per caption and not once per clip.
check("…and its distance as a fraction of the type size, not as pixels",
      abs(shadowed["shadow"] - 11.0 / 90.0) < 0.001, str(shadowed["shadow"]))
check("the words still come through with the shadow beside them",
      shadowed["text"] == "SHADOWED", shadowed["text"])

# ⚠ "SHADOW ONLY" DRAWS NO LETTERS AT ALL. This app has no such mode, so
# importing the shadow alone would put a smear where a title should be.
plain = _one_title(_shadow_xml(only="true"))["texts"][0]
check("a “Shadow Only” shadow is dropped, and the title still arrives",
      plain.get("shadow", 0) == 0 and plain["text"] == "SHADOWED",
      str(plain.get("shadow")))
# ⚠ AND A TITLE WITH NO SHADOW MUST NOT BE GIVEN ONE. The keys are only copied
# when the reader actually found them.
check("a title with no Drop Shadow beside it keeps this app's own defaults",
      "shadow" not in [k for k in placed["texts"][0] if k == "shadow"]
      or placed["texts"][0].get("shadow", 0) == 0,
      str(placed["texts"][0].get("shadow")))


# ---------------------------------------------------------------------------
# 8j · An `xmeml` title — the route that DOES carry the colour
# ---------------------------------------------------------------------------
# ⚠ **A TITLE IN FCP7 XML IS A `<generatoritem>`, A SIBLING OF `<clipitem>` AND
# NOT A KIND OF IT** — so a reader that walks only `clipitem` never sees one, and
# this one did not. It has no `<file>` either, so even when it was reached
# `read_clip` refused it for having nothing to resolve. Both had to change.
#
# ⚠ **AND THIS IS THE ROUTE THAT ANSWERS E59's ONE GAP.** A `.prproj` does not
# carry a title's fill colour anywhere; an `xmeml` writes `fontcolor` as plain
# `<red>/<green>/<blue>`. So "export a Final Cut Pro XML instead" stops being
# generic advice and becomes the concrete answer to "why is my text white?".
print("\n8j · an xmeml title, in the colour it was set in")

TITLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4"><sequence><name>Titles</name>
<rate><timebase>25</timebase></rate>
<media><video>
 <format><samplecharacteristics><width>1920</width><height>1080</height></samplecharacteristics></format>
 <track>
  <clipitem id="c1"><name>shot</name><start>0</start><end>50</end><in>0</in><out>50</out>
    <file id="f1"><name>shot.mp4</name><pathurl>file://localhost/C:/x/shot.mp4</pathurl></file>
  </clipitem>
 </track>
 <track>
  <generatoritem id="g1"><name>Title</name><start>25</start><end>75</end><in>0</in><out>50</out>
   <effect><name>Outline Text</name><effectid>Outline Text</effectid>
    <effecttype>generator</effecttype><mediatype>video</mediatype>
    <parameter><parameterid>str</parameterid><name>Text</name><value>HELLO  WORLD</value></parameter>
    <parameter><parameterid>fontname</parameterid><value>Montserrat-SemiBold</value></parameter>
    <parameter><parameterid>fontsize</parameterid><value>72</value></parameter>
    <parameter><parameterid>fontcolor</parameterid>
      <value><alpha>255</alpha><red>48</red><green>124</green><blue>194</blue></value></parameter>
    <parameter><parameterid>linewidth</parameterid><value>6</value></parameter>
    <parameter><parameterid>linecolor</parameterid>
      <value><alpha>255</alpha><red>255</red><green>255</green><blue>255</blue></value></parameter>
    <parameter><parameterid>origin</parameterid><value><horiz>0</horiz><vert>-0.6</vert></value></parameter>
   </effect>
  </generatoritem>
 </track>
</video></media></sequence></xmeml>"""

xtitle = interchange._read_fcp7(TITLE_XML)
xplaced = interchange.to_project(
    xtitle,
    lambda n: {"kind": "video", "upload_id": "u", "duration_ms": 0}
    if n.endswith(".mp4") else None,
    new_id=lambda: _uuid.uuid4().hex[:12],
)
check("a <generatoritem> is read at all — it is not a <clipitem>",
      len(xplaced["texts"]) == 1, str(len(xplaced["texts"])))
xt = xplaced["texts"][0]
check("…with its words, whitespace tidied",
      xt["text"] == "HELLO WORLD", repr(xt["text"]))
# ⚠ THE ONE THING A .prproj CANNOT GIVE (E59). #307cc2 is the blue measured off
# a real render of this project's own colour-text version.
check("…IN ITS OWN COLOUR, which is what a .prproj could never say",
      xt["color"] == "#307cc2", xt["color"])
check("…and its OUTLINE, width and colour both",
      xt["stroke_px"] == 6.0 and xt["stroke_color"] == "#ffffff",
      f'{xt.get("stroke_px")} / {xt.get("stroke_color")}')
check("…its font, mapped onto a face this app ships",
      xt["font"] == "montserrat", xt["font"])
# ⚠ `<vert>` COUNTS UPWARDS AND `y` COUNTS DOWN. Read straight across, every
# lower third lands at the top of the frame.
check("…its place on screen, with vert flipped into this app's y",
      (xt["x"], xt["y"]) == (0.5, 0.8), f'{xt["x"]}, {xt["y"]}')
check("…and when it is on screen",
      (xt["start_ms"], xt["duration_ms"]) == (1000, 2000),
      f'{xt["start_ms"]}, {xt["duration_ms"]}')
# ⚠ THE TITLE ROW IS NOT A PICTURE ROW (E60), in this format either.
check("a row of titles does not become an empty picture row",
      xplaced["report"]["video_tracks"] == 1,
      str(xplaced["report"]["video_tracks"]))
check("…and the footage row is untouched",
      len(xplaced["frames"]) == 1 and xplaced["frames"][0]["kind"] == "video",
      str(len(xplaced["frames"])))
check("…on its own import lane, never the ✨ Auto captions row",
      xt["layer_id"].startswith(interchange.IMPORT_TEXT_LANE_PREFIX)
      and xt["layer_id"] != interchange.IMPORT_CAPTION_LAYER_ID,
      xt["layer_id"])

# ⚠ AN EFFECT THAT IS NOT A GENERATOR MUST NOT BE MINED FOR TEXT. A filter
# carries `<parameter>` elements too, and one of them being called something
# with a string in it does not make the clip a title.
NOT_A_TITLE = TITLE_XML.replace("<effecttype>generator</effecttype>",
                                "<effecttype>filter</effecttype>")
check("an ordinary filter on a clip is not mistaken for lettering",
      not interchange.to_project(
          interchange._read_fcp7(NOT_A_TITLE),
          lambda n: {"kind": "video", "upload_id": "u", "duration_ms": 0}
          if n.endswith(".mp4") else None, new_id=lambda: "x")["texts"], "")
# ⚠ AND A FILE-LESS ITEM WITH NO LETTERING IN IT IS STILL DROPPED. Keeping it
# would put a nameless empty clip on the timeline for every generator this app
# cannot read — a colour matte, a bars-and-tone.
NO_WORDS = TITLE_XML.replace("<value>HELLO  WORLD</value>", "<value></value>")
check("…and a generator with no words in it is left out, as before",
      not interchange.to_project(
          interchange._read_fcp7(NO_WORDS),
          lambda n: {"kind": "video", "upload_id": "u", "duration_ms": 0}
          if n.endswith(".mp4") else None, new_id=lambda: "x")["texts"], "")

# 16-bit channels: some exporters write 0…65535 where FCP7 writes 0…255.
# ⚠ CLAMPING INSTEAD OF SCALING TURNS EVERY 16-BIT COLOUR INTO PURE WHITE.
WIDE = TITLE_XML.replace(
    "<alpha>255</alpha><red>48</red><green>124</green><blue>194</blue>",
    "<alpha>65535</alpha><red>12336</red><green>31868</green><blue>49858</blue>")
wide = interchange.to_project(
    interchange._read_fcp7(WIDE),
    lambda n: {"kind": "video", "upload_id": "u", "duration_ms": 0}
    if n.endswith(".mp4") else None, new_id=lambda: "x")["texts"][0]
check("a 16-bit fontcolor is scaled down, not clamped to white",
      wide["color"] == "#307cc2", wide["color"])




# ---------------------------------------------------------------------------
# 8d · The ceiling every reader shares — the one that answered with a 500
# ---------------------------------------------------------------------------
# ⚠ THIS IS NOT A `.prproj` PROBLEM; IT IS `to_project`'s, SO IT IS EVERY
# READER'S. The route builds `AnimaticFrame` / `AnimaticAudio` straight out of
# what `to_project` returns, and those models cap a clip at TEN MINUTES
# (`le=600_000`) and forbid a negative start (`ge=0`). This app makes animatics;
# somebody else's timeline does not know that. A fifteen-minute interview take, a
# music bed across a whole reel, or a record-in before the sequence starts are
# ordinary things to find in an XML or an EDL — and every one of them used to
# reach the user as **"Internal Server Error"**, with the whole import lost and
# nothing naming the clip that did it.
#
# So they are clamped and COUNTED. The clip arrives, in the right row, and the
# warning says how many were moved or shortened. ⚠ **The last check in this block
# proves the ceiling is real** — a test that asserts a clamped value without
# proving the unclamped one would have been rejected is a test that passes on
# broken code.
print("\n8d · the ceiling every reader shares")

from server.schemas import AnimaticAudio, AnimaticFrame, AnimaticTextClip  # noqa: E402

_FPS = 24


def _incoming(video=(), audio=()):
    """A neutral incoming model — deliberately reader-agnostic, because the
    clamp is in `to_project` and fcp7, edl and prproj all pass through it."""
    return {
        "reader": "test", "name": "Extremes", "fps": _FPS, "width": 0, "height": 0,
        "files": {"f": {"name": "shot.png", "pathurl": ""}},
        "video": [{"clips": list(video), "transitions": []}] if video else [],
        "audio": [{"clips": list(audio)}] if audio else [],
        "warnings": [],
    }


def _c(start_f, end_f, **kw):
    got = {"name": "c", "file": "f", "start": start_f, "end": end_f,
           "in": 0, "out": 0, "enabled": True, "level": 1.0}
    got.update(kw)
    return got


def _as_image(_n):
    return {"kind": "image", "upload_id": "u1", "duration_ms": 0}


def _as_audio(_n):
    return {"kind": "audio", "upload_id": "u1", "duration_ms": 0}


long_take = interchange.to_project(
    _incoming([_c(0, 15 * 60 * _FPS)]), _as_image, background="#000", new_id=lambda: "x"
)
check("a fifteen-minute clip arrives instead of crashing the import",
      len(long_take["frames"]) == 1, str(len(long_take["frames"])))
check("…shortened to the ten minutes a clip can be here",
      long_take["frames"][0]["duration_ms"] == 600_000,
      str(long_take["frames"][0]["duration_ms"]))
check("…and the user is told it was shortened",
      any("shortened" in w for w in long_take["report"]["warnings"]),
      str(long_take["report"]["warnings"]))

before_zero = interchange.to_project(
    _incoming([_c(-24, 48)], [_c(-24, 48)]), _as_image, background="#000", new_id=lambda: "x"
)
check("a picture clip starting before the sequence is moved to the start",
      before_zero["frames"][0]["start_ms"] == 0, str(before_zero["frames"][0]["start_ms"]))
# ⚠ ITS LENGTH IS TAKEN FROM THE ORIGINAL START, BEFORE THE CLAMP. Clamping first
# and subtracting after would STRETCH the clip instead of moving it: -1s→+2s is
# three seconds long wherever it ends up.
check("…keeping the length it had, not stretched to the new start",
      before_zero["frames"][0]["duration_ms"] == 3000,
      str(before_zero["frames"][0]["duration_ms"]))
check("…and it says clips were moved",
      any("moved" in w for w in before_zero["report"]["warnings"]),
      str(before_zero["report"]["warnings"]))

sound_before_zero = interchange.to_project(
    _incoming((), [_c(-24, 48)]), _as_audio, background="#000", new_id=lambda: "x"
)
check("a sound starting before the sequence is moved too",
      sound_before_zero["audio_tracks"][0]["start_ms"] == 0,
      str(sound_before_zero["audio_tracks"][0]["start_ms"]))

far_out = interchange.to_project(
    _incoming([_c(25 * 3600 * _FPS, 25 * 3600 * _FPS + 48, **{"in": 25 * 3600 * _FPS})]),
    lambda _n: {"kind": "video", "upload_id": "u1", "duration_ms": 0},
    background="#000", new_id=lambda: "x",
)
check("a clip past 24 hours is pulled back inside the timeline",
      far_out["frames"][0]["start_ms"] == 24 * 3_600_000,
      str(far_out["frames"][0]["start_ms"]))
check("…and so is a source window past 24 hours",
      far_out["frames"][0]["in_ms"] == 24 * 3_600_000,
      str(far_out["frames"][0]["in_ms"]))

# ⚠ THE WHOLE POINT: everything handed back must survive the models the ROUTE
# builds out of it. This is the check that would have caught the 500.
_every = [long_take, before_zero, sound_before_zero, far_out]
_bad = []
for _built in _every:
    for _f in _built["frames"]:
        try:
            AnimaticFrame(**_f)
        except Exception as _exc:  # noqa: BLE001
            _bad.append(f"frame {_f['start_ms']}/{_f['duration_ms']}: {_exc}")
    for _a in _built["audio_tracks"]:
        try:
            AnimaticAudio(**_a)
        except Exception as _exc:  # noqa: BLE001
            _bad.append(f"audio {_a['start_ms']}: {_exc}")
check("every clip to_project returns is one the response model accepts",
      not _bad, str(_bad)[:200])

# ⚠ AND THE CEILING IS PROVED REAL. Without this, the check above passes just as
# happily on a build where the models have no bounds at all — and the next person
# to widen `AnimaticFrame` would quietly delete this whole section's value.
_unclamped = dict(long_take["frames"][0], duration_ms=900_000)
_moved_back = dict(long_take["frames"][0], start_ms=-1000)
_rejected = 0
for _try in (_unclamped, _moved_back):
    try:
        AnimaticFrame(**_try)
    except Exception:  # noqa: BLE001
        _rejected += 1
check("…and the model really would have rejected the unclamped values (a 500)",
      _rejected == 2, f"only {_rejected} of 2 were rejected")


# ---------------------------------------------------------------------------
# 8e · A placeholder must not hide the film behind it
# ---------------------------------------------------------------------------
# ⚠ ALSO `to_project`'s, SO ALSO EVERY READER'S. E45 says a clip whose file did
# not arrive becomes a labelled COLOUR CARD rather than being left out, and that
# is right — on the BOTTOM row, where the alternative is an empty frame and an
# invisible gap. On any row ABOVE it the clip is an OVERLAY, and an opaque card
# there paints over everything beneath.
#
# Premiere is where this bites and it is not an edge case: a title, a Graphic and
# an Adjustment Layer have NO media file to attach and never will, so every one
# of them arrives unmatched. A real project carried four of them at full length
# over the cut, and the import previewed and exported as 68 seconds of BLACK —
# reported as "audio, image and video show but text not show", because where the
# lettering should have been there was a black rectangle over the whole film.
#
# ⚠ INVISIBLE IS NOT OMITTED. The clip is still on the timeline, still named,
# still counted in `placeholders`, and `opacity` is an ordinary editable field.
# The checks below hold BOTH halves of that at once — a fix that dropped the
# clips would pass an "it is not black" test and fail this section.
print("\n8e · a placeholder does not hide the film")


def _lanes(*lanes):
    """An incoming model with SEVERAL video rows — `_incoming` only makes one,
    and one row cannot express "above" and "below" at all."""
    return {
        "reader": "test", "name": "Titles", "fps": _FPS, "width": 0, "height": 0,
        "files": {"f": {"name": "shot.png", "pathurl": ""}},
        "video": [{"clips": list(clips), "transitions": []} for clips in lanes],
        "audio": [],
        "warnings": [],
    }


# Bottom row: a clip whose picture DID arrive. Above it: two that did not — the
# Graphic and the Adjustment Layer a Premiere sequence always has.
_stack = interchange.to_project(
    _lanes(
        [_c(0, 240, name="footage", file="f")],
        [_c(0, 240, name="Graphic", file="")],
        [_c(0, 240, name="Adjustment Layer", file="")],
    ),
    _as_image,
    background="#000000",
)
_by_track = {f["track"]: f for f in _stack["frames"]}

check("the matched clip on the bottom row is the picture it always was",
      _by_track[0]["kind"] == "image", str(_by_track.get(0)))
# ⚠ TWO ROWS, NOT THREE, AND THAT CHANGED ON PURPOSE — see §8h. The Adjustment
# Layer is no longer carried in at all: it is an empty holder for colour effects
# no interchange format carries, so the only thing it ever contributed was a
# full-length clip over the cut that did nothing and had to be hunted down and
# deleted. The GRAPHIC still arrives, because a graphic can hold something.
check("a graphic on a row ABOVE the film is still on the timeline",
      set(_by_track) == {0, 1}, f"tracks {sorted(_by_track)}")
check("…still carrying its own name",
      _by_track[1]["label"] == "Graphic", str(_by_track[1]["label"]))
check("…still counted as a placeholder, so the dialog names it",
      _stack["report"]["placeholders"] == ["Graphic"],
      str(_stack["report"]["placeholders"]))
# ⚠ THE ONE THE BLACK SCREEN WAS. Two of these at full length over a cut is a
# film nobody can see, and nothing anywhere reported a fault.
check("…and it draws NOTHING, so the film behind it is visible",
      _by_track[1]["opacity"] == 0, str(_by_track[1]["opacity"]))
check("the report says a clip above the film drew nothing",
      any("draw nothing" in w for w in _stack["report"]["warnings"]),
      str(_stack["report"]["warnings"]))
# ⚠ AND IT NO LONGER TELLS ANYBODY TO RETYPE THEIR TITLES. That sentence was in
# this warning for months and it was wrong — the words are in the file. §8g is
# where they come out; this is the guard against the advice coming back.
check("…and no longer tells the user to type their lettering again",
      not any("Text tool" in w for w in _stack["report"]["warnings"]),
      str([w for w in _stack["report"]["warnings"] if "Text tool" in w]))
check("the Adjustment Layer is left out, and the report says which",
      any("Adjustment Layer" in w for w in _stack["report"]["warnings"]),
      str(_stack["report"]["warnings"]))
# ⚠ AND THE NAME IS NOT ENOUGH ON ITS OWN. Somebody's footage really can be
# called "Adjustment Layer.mov"; dropping a clip that RESOLVED to a file would
# be this app deleting a shot out of a cut on the strength of its filename.
_named = interchange.to_project(
    _lanes([_c(0, 240, name="Adjustment Layer", file="f")]),
    _as_image, background="#000000",
)
check("…but a clip that resolved to a real FILE is kept whatever it is called",
      len(_named["frames"]) == 1 and _named["frames"][0]["kind"] == "image",
      str(_named["frames"]))

# ⚠ THE BOTTOM ROW KEEPS ITS CARD. Blanking that one would put the gap back to
# being invisible, which is the whole of E45 undone in the name of fixing this.
_ground = interchange.to_project(
    _lanes([_c(0, 240, name="Graphic", file="")]), _as_image, background="#000000"
)
check("a missing clip on the BOTTOM row is still an opaque card (E45 holds)",
      _ground["frames"][0]["kind"] == "color"
      and _ground["frames"][0]["opacity"] == 1,
      str(_ground["frames"][0]))
check("…and one row of nothing but gaps raises no overlay warning",
      not any("draw nothing" in w for w in _ground["report"]["warnings"]),
      str(_ground["report"]["warnings"]))

# ⚠ AND "BOTTOM" IS THE LOWEST ROW WITH CLIPS ON IT, not row 0. A sequence whose
# V1 is empty still lists V1, and reading the count instead of the clips would
# blank the real background and put the black screen back.
_empty_v1 = interchange.to_project(
    _lanes([], [_c(0, 240, name="Graphic", file="")]), _as_image, background="#000000"
)
check("an empty V1 does not make row 2 an overlay",
      _empty_v1["frames"][0]["opacity"] == 1, str(_empty_v1["frames"][0]))

# Every frame this produced must still satisfy the model the route builds — an
# opacity outside 0..1 would be the 8d failure over again.
try:
    for _f in _stack["frames"] + _ground["frames"] + _empty_v1["frames"]:
        AnimaticFrame(**_f)
    _ok = True
except Exception as exc:  # noqa: BLE001
    _ok = False
    _why = str(exc)
check("every frame still validates as an AnimaticFrame", _ok,
      "" if _ok else _why)


# ---------------------------------------------------------------------------
# 8l · WHERE a Premiere caption actually sits — Motion, not the text component
# ---------------------------------------------------------------------------
#     "dusri baar mera text sab middle screen mai aaya hai kyun … Premiere pro
#      mai transform pe key laga kar har text clip par daala hua hai so wo
#      transform ka value nhi aa raha hai"
#
# ⚠ **`AE.ADBE Text`'s OWN `Position` IS WHERE THE WORDS SIT INSIDE THE GRAPHIC,
# NOT ON THE SCREEN.** For a caption built from Premiere's own template that is
# ~0.52 — the middle — and reading only it put all 40 of a real import's captions
# within 0.03 of the frame centre when every one of them belonged at the bottom.
# What puts the graphic at the bottom is the clip's `AE.ADBE Motion`, the
# Position/Anchor Point every Premiere clip has, which this reader never opened.
# In the reference project **78 of 82 captions carry `Motion.Position
# 0.5:0.9211`** and the remaining four are title cards at `0.5:0.5`.
#
# ⚠ **AND THERE ARE TWO OF THEM.** `AE.ADBE Geometry2` is the Transform EFFECT
# added by hand — the user who reported this had keyframed one onto every caption
# — and it STACKS on Motion rather than replacing it. In that project it happens
# to be identity, so a reader that took only Motion would have looked correct on
# this film and been wrong on the next.
#
# ⚠ **THE VERTICAL IS AN EDGE, EXACTLY LIKE THE HORIZONTAL IN §8g.** What
# Premiere stores is the BASELINE and what this app draws from is the block's
# CENTRE. Measured against a REAL RENDER of episode 7 of the same series: the
# stored numbers compose to y = 0.9430 and the exported .mp4's lettering has its
# band bottom at 0.944 and its visual centre at 0.928 ± 0.003 over five frames —
# which at `size_px` 45 in a 1080-high frame is 0.36 em, half a cap height.
print(chr(10) + "8l \u00b7 a caption is where MOTION puts it")

_PLACED_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <VideoTrackGroup ObjectID="260" ClassID="1d7fbd0a" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1"><Track Index="0" ObjectURef="pl-titles"/></Tracks>
    </TrackGroup>
    <FrameRate>{_T}</FrameRate>
    <FrameRect>0,0,1920,1080</FrameRect>
  </VideoTrackGroup>
  <VideoClipTrack ObjectUID="pl-titles" ClassID="aaa1" Version="1">
    <ClipTrack Version="1"><ClipItems Version="1"><TrackItems Version="1">
      <TrackItem Index="0" ObjectRef="2100"/>
      <TrackItem Index="1" ObjectRef="2200"/>
      <TrackItem Index="2" ObjectRef="2300"/>
      <TrackItem Index="3" ObjectRef="2400"/>
    </TrackItems></ClipItems></ClipTrack>
  </VideoClipTrack>
{_graphic_clip(2100, end=_T * 48, texts=["the lower third"],
               motion=("0.5:0.9211387038230896", "0.5:0.5"),
               transform=("0.5:0.5", "0.5:0.5"))}
{_graphic_clip(2200, end=_T * 96, start=_T * 48, texts=["the title card"],
               position="0.2797:0.4298", motion=("0.5:0.5", "0.5:0.5"))}
{_graphic_clip(2300, end=_T * 144, start=_T * 96, texts=["moved by BOTH"],
               motion=("0.5:0.75", "0.5:0.5"),
               transform=("0.6:0.6", "0.5:0.5"))}
{_graphic_clip(2400, end=_T * 192, start=_T * 144, texts=["parked off screen"],
               motion=("0.5:9.0", "0.5:0.5"))}
</PremiereData>
"""

_placed = interchange.read_document(
    _gzip.compress(_PLACED_XML.encode("utf-8")), "Placed.prproj",
    fps_hint=24, experimental=True,
)
_where = {}
for _row in _placed["video"]:
    for _clip in _row["clips"]:
        for _t in ((_clip.get("graphic") or {}).get("texts") or []):
            _where[_t["text"]] = _t

check("every placed caption still comes through",
      len(_where) == 4, str(sorted(_where)))

# 0.5219 (inside the graphic) + (0.9211 - 0.5) (where the clip is) - 0.36×45/1080
_LOWER = 0.5219 + (0.9211387038230896 - 0.5) - 0.36 * 45 / 1080
check("a caption lands where MOTION puts it, not where the text layer sits",
      abs(_where["the lower third"]["y"] - _LOWER) < 0.0002,
      f'y={_where["the lower third"]["y"]} (wanted {_LOWER:.4f})')
# ⚠ THE NUMBER THE RENDER WAS MEASURED AT. This is the assertion that would have
# caught the whole fault: 0.928 is the bottom of the frame, 0.52 is the middle,
# and the old reader answered 0.52 for all 82.
check("…which is the 0.928 measured off the real export, not the 0.52 stored",
      abs(_where["the lower third"]["y"] - 0.928) < 0.002,
      str(_where["the lower third"]["y"]))

# A clip Premiere has NOT moved contributes nothing, so the text layer's own
# height is the answer — which is what keeps a two-line title card two lines.
_CARD = 0.4298 - 0.36 * 45 / 1080
check("a clip at the frame centre leaves the words where they were set",
      abs(_where["the title card"]["y"] - _CARD) < 0.0002,
      f'y={_where["the title card"]["y"]} (wanted {_CARD:.4f})')

# ⚠ BOTH TRANSFORMS, SUMMED. Reading only Motion gives 0.7719 here and looks
# perfectly plausible; the Transform effect is worth another 0.1.
_BOTH_Y = 0.5219 + (0.75 - 0.5) + (0.6 - 0.5) - 0.36 * 45 / 1080
_BOTH_X = _where["moved by BOTH"]["x"]
check("the Transform EFFECT stacks on Motion rather than being ignored",
      abs(_where["moved by BOTH"]["y"] - _BOTH_Y) < 0.0002,
      f'y={_where["moved by BOTH"]["y"]} (wanted {_BOTH_Y:.4f})')
# ⚠ AGAINST THE SAME CAPTION WITHOUT THE SIDEWAYS MOVE, not against a number
# typed here: the base x is the left edge PLUS half the line's own width (§8g),
# so a literal would be re-deriving that fit and would drift the moment the
# fixture's wording changed.
_BASE_X = 0.2797 + (len("moved by BOTH") * 45 * 0.5 / 2) / 1920
check("…and it moves the caption sideways too, not only down",
      abs(_BOTH_X - (_BASE_X + 0.1)) < 0.0002, f"x={_BOTH_X} (wanted {_BASE_X + 0.1:.4f})")

# ⚠ AND A CAPTION PREMIERE PARKED OFF-SCREEN MUST NOT FAIL THE IMPORT.
# `AnimaticTextClip` takes -1..2; anything outside that is a 422 on the whole
# timeline, which is the 8d failure over again.
check("a caption parked far outside the frame is clamped, not left to 422",
      _where["parked off screen"]["y"] == 2.0,
      str(_where["parked off screen"]["y"]))
try:
    for _t in _where.values():
        AnimaticTextClip(**{
            "id": "t", "layer_id": "l", "text": _t["text"],
            "start_ms": 0, "duration_ms": 1000, "place": "free",
            "x": _t["x"], "y": _t["y"], "size_px": _t["size_px"],
        })
    _ok_text = True
except Exception as exc:  # noqa: BLE001
    _ok_text, _why_text = False, str(exc)
check("every placed caption still validates as an AnimaticTextClip", _ok_text,
      "" if _ok_text else _why_text)


# ---------------------------------------------------------------------------
# 8m · The files already IN the project count as arrived
# ---------------------------------------------------------------------------
#     "maine location diya tha bg music and logo ka fir v nhi aaya"
#
# ⚠ **AN IMPORT USED TO SEE ONLY WHAT WAS ATTACHED TO ITS OWN REQUEST.** Nothing
# looked at the project's Media pane, so a project already holding all 27 of its
# files still turned every clip into a placeholder unless every one of them was
# picked again — and the obvious repair, dragging the ONE missing file into Media
# and importing again, could not work: the card sat on screen while the report
# said the file had not arrived. It also meant a second import stored a second
# copy of everything (a real job on this machine holds 52 files for 27 names).
#
# ⚠ **FRESH STILL WINS.** The library is a FALLBACK. Re-attaching a file that has
# been re-exported must still replace the older copy, or an import silently uses
# a stale cut of a shot the user deliberately re-rendered.
print(chr(10) + "8m · the project's own Media counts as arrived")

_LIB = interchange.media_library([
    {"kind": "audio", "label": "music.mp3", "upload_id": "old-music", "duration_ms": 90},
    {"kind": "image", "label": "logo.png", "upload_id": "old-logo"},
    {"kind": "video", "label": "shot_03.mp4", "upload_id": "old-shot"},
    # ⚠ A COLOUR CARD. No file behind it, so it must not answer for a name.
    {"kind": "color", "label": "Black", "upload_id": "", "color": "#000000"},
    # An asset the client made before an upload finished — no id, no answer.
    {"kind": "image", "label": "half.png", "upload_id": ""},
])
check("a stored file is found by its full name",
      _LIB.get("music.mp3", {}).get("upload_id") == "old-music", str(_LIB.get("music.mp3")))
# ⚠ THE STEM TOO, for the same reason the attached files are keyed both ways: an
# app that transcodes on the way out leaves `shot_03.mov` in the project file.
check("…and by its stem, so a re-encoded clip still matches",
      _LIB.get("shot_03", {}).get("upload_id") == "old-shot", str(_LIB.get("shot_03")))
check("its kind and length come across, so audio stays audio",
      _LIB["music.mp3"]["kind"] == "audio" and _LIB["music.mp3"]["duration_ms"] == 90,
      str(_LIB["music.mp3"]))
check("a colour card is NOT offered as a file",
      "black" not in _LIB, str(sorted(_LIB)))
check("…nor is an asset whose upload never finished",
      "half.png" not in _LIB, str(sorted(_LIB)))

# And the whole point, end to end: a timeline naming two files, with NOTHING
# attached and both of them already in the project.
_HAVE = {"shot.png": {"kind": "image", "upload_id": "u-pic", "duration_ms": 0},
         "vo.mp3": {"kind": "audio", "upload_id": "u-vo", "duration_ms": 0}}


def _library_only(name):
    """`resolve` with an EMPTY upload — the library is all there is."""
    return _HAVE.get((name or "").lower())


_reused = interchange.to_project(
    {
        "reader": "test", "name": "Reused", "fps": _FPS, "width": 0, "height": 0,
        "files": {"p": {"name": "shot.png", "pathurl": ""},
                  "a": {"name": "vo.mp3", "pathurl": ""}},
        "video": [{"clips": [_c(0, 240, name="pic", file="p")], "transitions": []}],
        "audio": [{"clips": [_c(0, 240, name="vo", file="a")]}],
        "warnings": [],
    },
    _library_only, background="#000000",
)
check("a picture already in the project is NOT a placeholder",
      _reused["frames"][0]["kind"] == "image", str(_reused["frames"][0]["kind"]))
check("…and the sound already in the project actually plays",
      len(_reused["audio_tracks"]) == 1, str(_reused["audio_tracks"]))
check("…so nothing is reported missing at all",
      _reused["report"]["missing"] == [] and _reused["report"]["placeholders"] == [],
      str(_reused["report"]["missing"]))

# ⚠ AND THE ROUTE HAS TO SAY IT HAPPENED. A clip that resolved to a file the user
# never attached this time is a good outcome and a surprising one; silence is how
# somebody ends up unsure which copy of a file their cut is using.
# ⚠ READ AS TEXT. Importing `server/animatics.py` drags in config, and G13 is
# the entry about what that cost the last time a test did it.
_route_src = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "server", "animatics.py"),
    encoding="utf-8",
).read()
check("the route tells the user when it used the project's own copy",
      "already in this project's Media" in _route_src, "")
check("…and the library is a FALLBACK — what was attached is tried first",
      _route_src.index("found = stored.get(base.lower())")
      < _route_src.index("found = library.get(base.lower())"), "")


# ---------------------------------------------------------------------------
# 8n · The path was always there — stop printing it at the user
# ---------------------------------------------------------------------------
#     "tum jo missing hai project mai uska local path bata rahe ho magar tum
#      khud usko pickup kyun nhi kar rahe ho jab tumne location mil raha hai to
#      user se kyun karwa rahe ho"
#
# ⚠ **THE REPORT KNEW WHERE EVERY MISSING FILE LIVED AND SENT A PERSON TO GO AND
# GET IT.** §8k made "missing" actionable by naming the folder; this is the next
# step, and it is the obvious one once the folder is on screen — when this server
# is running on the same computer as the editor that wrote the project, that
# folder is right there. Asking the user to reproduce a path we are holding, by
# hand, in a file dialog, is the work the machine should have done.
#
# ⚠ **AND THE EXTENSION WHITELIST IS THE SECURITY BOUNDARY, NOT A TIDINESS
# RULE.** A `<pathurl>` is text inside an UPLOADED document, so a hand-written
# project file can name any path on the disk it likes. `media_kind` is what stops
# `.../id_rsa` or `.../.env` from being opened, stored, and served back — the
# checks below are the ones that must never be allowed to go green by accident.
print(chr(10) + "8n · a missing file whose path is on this disk is fetched, not asked for")

_DISK = {os.path.normpath(p) for p in (
    "C:/Users/Admin/Clips/music.mp3",
    "C:/Users/Admin/Series/ID logo.png",
    "/Users/me/Movies/shot.mov",
    "C:/Users/Admin/.ssh/id_rsa",
    "C:/Users/Admin/secrets.env",
    "C:/Users/Admin/Clips/notes.txt",
    # ⚠ THESE TWO ARE THE POINT OF THE RELATIVE-PATH CHECKS BELOW, and without
    # them those checks pass for the wrong reason. A relative path is resolved
    # against whatever directory the server was started in, so the only way to
    # prove it is being REFUSED rather than merely not found is to put a file
    # exactly where the refusal is stopping it from looking.
    "Clips/music.mp3",
    "music.mp3",
)}


def _on_disk(path):
    """A disk described rather than written — see `local_media_paths(exists=…)`."""
    return os.path.normpath(path) in _DISK


def _paths(*pathurls):
    return interchange.local_media_paths(
        {"files": {str(i): {"name": "", "pathurl": p}
                   for i, p in enumerate(pathurls)}},
        exists=_on_disk,
    )


# A `.prproj` writes a bare Windows path; an `xmeml` writes a `file://` URL.
_found = _paths(
    "C:\\Users\\Admin\\Clips\\music.mp3",
    "file://localhost/C:/Users/Admin/Series/ID%20logo.png",
    "file:///Users/me/Movies/shot.mov",
)
check("a Windows path out of a .prproj is found on this disk",
      os.path.normpath(_found.get("music.mp3", "")) ==
      os.path.normpath("C:/Users/Admin/Clips/music.mp3"), str(_found))
# ⚠ UNQUOTED. Every real path in the project this was reported from has spaces in
# it, and an XML writes them as `%20` — matching without unquoting finds nothing
# and looks exactly like the feature not working.
check("…and a file:// URL with %20 in it, unquoted first",
      "id logo.png" in _found, str(sorted(_found)))
# ⚠ THE LEADING SLASH GOES BACK ON. Stripping `file:///` off a Mac path leaves a
# RELATIVE one, which would then resolve against the server's own directory.
check("…and a Mac path keeps the / that the prefix took with it",
      os.path.normpath(_found.get("shot.mov", "")) ==
      os.path.normpath("/Users/me/Movies/shot.mov"), str(_found))
check("keys are lowercased, the way `resolve` looks a name up",
      sorted(_found) == ["id logo.png", "music.mp3", "shot.mov"], str(sorted(_found)))

# --- and now the ones that must stay shut -----------------------------------
check("a PRIVATE KEY on the same disk is not a media file and is not opened",
      _paths("C:/Users/Admin/.ssh/id_rsa") == {}, str(_paths("C:/Users/Admin/.ssh/id_rsa")))
check("…nor is a .env sitting beside the footage",
      _paths("C:/Users/Admin/secrets.env") == {}, "")
check("…nor a .txt, even one in the project's own Clips folder",
      _paths("C:/Users/Admin/Clips/notes.txt") == {}, "")
# ⚠ A RELATIVE PATH IS RELATIVE TO A MACHINE WE DO NOT HAVE. Resolving it here
# reads a same-named file out of whatever directory uvicorn was started in.
check("a relative path is refused rather than resolved against the server's cwd",
      _paths("Clips/music.mp3") == {}, str(_paths("Clips/music.mp3")))
check("…and so is a bare filename",
      _paths("music.mp3") == {}, "")
check("a media path that is NOT on this disk simply does not come back",
      _paths("C:/Users/Admin/Clips/gone.mp3") == {}, "")
check("an empty pathurl — an EDL carries none — is skipped, not crashed on",
      _paths("", "   ") == {}, "")

# --- the route's half: gated, last in the queue, and said out loud ----------
check("the route asks permission before it opens anything on this disk",
      "_may_read_local_media(request)" in _route_src, "")
check("…and loopback is what that permission means by default",
      "_LOOPBACK" in _route_src and "127.0.0.1" in _route_src, "")
# ⚠ LAST. Disk BEFORE the project's own Media would store a fresh copy of every
# file on every import — the duplicate storage that already put 52 files behind
# 27 names in a real project on this machine.
check("the disk is tried only for what neither the upload nor the library had",
      "stored.get(base) or stored.get(stem) or library.get(base) or library.get(stem)"
      in _route_src, "")
check("…and the report says which files were taken off this computer",
      "found on this " in _route_src and "from_disk" in _route_src, "")

# --- and the dialog stops making the user press twice for a .prproj ---------
# ⚠ THE RED PANEL AND THE SECOND BUTTON ARE GONE, and the flag moved onto the
# FIRST request — a strict read followed by a retry is two uploads of the same
# 27 files, which is the wait the user was complaining about.
_modal_src = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "client", "src", "components", "ProjectImportModal.jsx"),
    encoding="utf-8",
).read()
check("a .prproj is read with the best-effort reader on the FIRST press",
      "const wantGuess = experimental || isPrproj;" in _modal_src, "")
check("…so the refusal panel is no longer in the dialog at all",
      "offerGuess" not in _modal_src, "")
# ⚠ AND THE HONESTY IT CARRIED HAS TO STILL BE SOMEWHERE. The badge is now the
# only thing on screen that says "guess" before the report is read.
check("…but the BEST GUESS badge that replaced it is still drawn",
      "isGuess" in _modal_src and 'read?.reader === "prproj"' in _modal_src, "")


# ---------------------------------------------------------------------------
# 8o · The move, the zoom and the fade — everything after the first keyframe
# ---------------------------------------------------------------------------
#     "Motion ka Scale, aur pehle keyframe ke baad kuch bhi, abhi nahi padha
#      jaata — ye fix kro"
#
# ⚠ **THE READER OPENED `<StartKeyframe>` AND STOPPED.** One value per parameter,
# so a shot that pushed in over four seconds arrived frozen at its first frame,
# and `Scale` was not looked at anywhere at all. In the reference project that is
# 21 shots with a keyframed zoom and 78 captions that fade in and out — most of
# what a viewer would call the film's motion.
#
# The shape, read off a real `.prproj` rather than guessed at:
#   · `<StartKeyframe>time,value,…>` is the RESTING value, stamped a hundred
#     hours before the film. It is NOT a keyframe and emitting it as one puts a
#     snap on the head of every clip (100 → 80 in thirteen milliseconds, in the
#     file this was read from).
#   · `<Keyframes>` is `ticks,value,…;ticks,value,…;` — the real ones.
#   · The ticks are in the clip's own SOURCE clock, so `<InPoint>` is the zero.
#     Four clips at 0.0s, 3.6s, 7.7s and 9.5s on the timeline all carry the same
#     keyframe ticks and all share an `<InPoint>` — rebasing against the TIMELINE
#     would have put every key an hour past the end of its own clip.
#   · With Uniform Scale on, Premiere writes the value into `Scale Height` and
#     parks `Scale Width` at 100 — reading Width as a second axis reports every
#     one of those clips as squashed.
print(chr(10) + "8o · Motion, Transform and Opacity — the whole curve, not the first key")

_TPS = interchange.PRPROJ_TICKS_PER_SECOND


def _param(name, start, keys=""):
    """One `<VideoComponentParam>`, the way a real .prproj writes one."""
    rows = "".join(f"{int(t)},{v},0,0,0,0,0,0;" for t, v in keys) if keys else ""
    return (f'<VideoComponentParam ObjectID="{_param.n}">'
            f"<Name>{name}</Name>"
            f"<StartKeyframe>-91445760000000000,{start},0,0,0,0,0,0</StartKeyframe>"
            + (f"<Keyframes>{rows}</Keyframes>" if rows else "")
            + "</VideoComponentParam>")


def _prproj_fragment(components) -> tuple:
    """`(by_id, root object id)` for a track item carrying these components."""
    _param.n = 900
    body = []
    refs = []
    for match, params in components:
        cid = _param.n = _param.n + 1
        holders = []
        for name, start, keys in params:
            _param.n += 1
            holders.append((_param.n, _param(name, start, keys)))
        body.append(
            f'<VideoFilterComponent ObjectID="{cid}"><Component><Params>'
            + "".join(f'<Param ObjectRef="{pid}"/>' for pid, _ in holders)
            + f"</Params></Component><MatchName>{match}</MatchName>"
              "</VideoFilterComponent>"
            + "".join(xml for _, xml in holders)
        )
        refs.append(cid)
    doc = ('<PremiereData><VideoClipTrackItem ObjectID="1">'
           + "".join(f'<Component ObjectRef="{r}"/>' for r in refs)
           + "</VideoClipTrackItem>" + "".join(body) + "</PremiereData>")
    root = ET.fromstring(doc)
    return interchange._prproj_index(root), "1"


_param.n = 900

# --- the rows themselves ----------------------------------------------------
_rows_el = ET.fromstring(_param("Opacity", "100.", [(10, "100."), (20, "0.")]))
_rows = interchange._prproj_keyframe_rows(_rows_el)
check("every keyframe is read, not just the first",
      [v for _, v in _rows] == ["100.", "0."], str(_rows))
# ⚠ THE ONE THAT WOULD PUT A SNAP ON EVERY CLIP. `<StartKeyframe>` sits a hundred
# hours before the film; emitting it as a key makes the value jump from the
# resting one to the first real one in whatever gap is left.
check("…and the resting <StartKeyframe> is NOT one of them",
      all(t >= 0 for t, _ in _rows), str(_rows))
check("a parameter with no <Keyframes> reads as no animation",
      interchange._prproj_keyframe_rows(ET.fromstring(_param("Scale", "50."))) == [], "")
check("…while its resting value still reads as before",
      interchange._prproj_keyframe_value(ET.fromstring(_param("Scale", "50."))) == "50.", "")

# --- one clip's whole transform ---------------------------------------------
# A Motion parked at 150% (a punch-in Premiere measures its own way), a Transform
# zooming 80 → 100 with Uniform Scale ON, and a separate Opacity component fading
# out. Exactly the three components a real clip carries.
_by_id, _root = _prproj_fragment([
    ("AE.ADBE Motion", [("Scale", "150.", ()), ("Position", "0.6:0.5", ()),
                        ("Anchor Point", "0.5:0.5", ())]),
    ("AE.ADBE Geometry2", [("Scale Height", "100.", ((0, "80."), (2 * _TPS, "100."))),
                           ("Scale Width", "100.", ())]),
    ("AE.ADBE Opacity", [("Opacity", "100.", ((0, "100."), (_TPS, "0.")))]),
])
_tr = interchange._prproj_transform(_by_id, _root)
check("the Position offset is summed off Anchor Point as before",
      abs(_tr["x"] - 0.1) < 1e-9 and abs(_tr["y"]) < 1e-9, str((_tr["x"], _tr["y"])))
# ⚠ `Scale Width` IS NOT A SECOND AXIS. With Uniform Scale on it is parked at 100
# and `Scale Height` carries the value; reading Width would squash the clip.
check("Scale Height is taken as the uniform scale when there is no Scale",
      "scale" in _tr["tracks"] and len(_tr["tracks"]["scale"]) == 2, str(_tr["tracks"].keys()))
check("the fade is found on its own AE.ADBE Opacity component",
      "opacity" in _tr["tracks"], str(sorted(_tr["tracks"])))
check("…and Motion's resting Scale is collected as a static, not as a track",
      abs(_tr["scale"] - 1.5) < 1e-9, str(_tr["scale"]))

# --- rebasing, normalising, clamping ----------------------------------------
_keys = interchange.prproj_transform_keys(_tr, in_ticks=0, length_ms=3000)
# ⚠ NORMALISED. Premiere measures Scale against the FILE's pixels and this app
# fits every picture to the frame, so 80 → 100 travels as "×1.25 by the end",
# never as 0.8 → 1.0. The reference project's statics run 24 to 150; mapping
# those directly turns a full-frame 4K still into a postage stamp.
check("a scale animation arrives RELATIVE to where the clip starts",
      [k["v"] for k in _keys["keyframes"]["scale"]] == [1.0, 1.25],
      str(_keys["keyframes"]["scale"]))
check("…and the fade arrives as 0..1, not 0..100",
      [k["v"] for k in _keys["keyframes"]["opacity"]] == [1.0, 0.0],
      str(_keys["keyframes"]["opacity"]))
check("…in milliseconds from the clip's own start",
      [k["t"] for k in _keys["keyframes"]["opacity"]] == [0, 1000],
      str(_keys["keyframes"]["opacity"]))
# ⚠ THE IN-POINT IS THE ZERO, and this is the assertion that pins it. The same
# component read against a clip whose source starts one second in must produce
# keys one second earlier.
_shifted = interchange.prproj_transform_keys(_tr, in_ticks=_TPS, length_ms=3000)
check("the clip's <InPoint> is what the times are measured from",
      [k["t"] for k in _shifted["keyframes"]["opacity"]] == [-1000, 0],
      str(_shifted["keyframes"]["opacity"]))
# ⚠ A WRONG BASE DOES NOT FAIL — IT SILENTLY PARKS EVERY KEY AN HOUR AWAY, where
# the value HOLDS and the clip looks un-animated while carrying a hundred
# meaningless keys. Refused and named instead.
_lost = interchange.prproj_transform_keys(_tr, in_ticks=3600 * _TPS, length_ms=3000)
check("a track whose times land nowhere near the clip is refused, not used",
      _lost["keyframes"] == {} and sorted(_lost["dropped"]) == ["opacity", "scale"],
      str(_lost))
# Faithful, but worth saying: Premiere holds it at one value too.
_held = interchange.prproj_transform_keys(_tr, in_ticks=-4 * _TPS, length_ms=1000)
check("an animation trimmed entirely off its own clip is flagged, not dropped",
      _held["keyframes"] and sorted(_held["outside"]) == ["opacity", "scale"], str(_held))

# A parameter that is keyframed but never actually changes — the reference
# project keyframes Position on 21 clips and moves it nowhere.
_flat, _flat_root = _prproj_fragment([
    ("AE.ADBE Geometry2", [("Position", "0.5:0.5", ((0, "0.5:0.5"), (_TPS, "0.5:0.5"))),
                           ("Anchor Point", "0.5:0.5", ())]),
])
_flat_keys = interchange.prproj_transform_keys(
    interchange._prproj_transform(_flat, _flat_root), in_ticks=0, length_ms=2000
)
check("a keyframed property that never moves writes no track at all",
      _flat_keys["keyframes"] == {}, str(_flat_keys["keyframes"]))

# ⚠ EVERY VALUE IS CLAMPED TO WHAT `AnimaticFrame` ACCEPTS. A clip Premiere
# scaled to 4000% is legal there; here it is a 500 with no message and the whole
# import lost, which is the rule `IMPORT_MAX_CLIP_MS` exists for.
_wild, _wild_root = _prproj_fragment([
    ("AE.ADBE Motion", [("Scale", "100.", ((0, "100."), (_TPS, "400000."))),
                        ("Position", "0.5:0.5", ((0, "0.5:0.5"), (_TPS, "-40.:80."))),
                        ("Anchor Point", "0.5:0.5", ())]),
])
_wild_keys = interchange.prproj_transform_keys(
    interchange._prproj_transform(_wild, _wild_root), in_ticks=0, length_ms=2000
)["keyframes"]
check("a runaway scale is clamped to what the schema takes",
      max(k["v"] for k in _wild_keys["scale"]) == 10.0, str(_wild_keys["scale"]))
check("…and a position parked off the world with it",
      min(k["v"] for k in _wild_keys["x"]) == -2.0
      and max(k["v"] for k in _wild_keys["y"]) == 3.0, str(_wild_keys))

# ---------------------------------------------------------------------------
# 8p · A FIXED Scale, carried across through the file's own pixel size
# ---------------------------------------------------------------------------
# What this section is about, and it is one sum:
#
#   Premiere  width fraction = source_w × scale / frame_w
#   here      width fraction = our_scale × min(1, source_aspect / frame_aspect)
#
# Both `<FrameRect>`s are in the project file — the clip item's is the SEQUENCE
# frame, a `VideoStream`'s is the FILE — so the sum can be done, which is what
# the reader used to say it could not. The numbers below are the reference
# project's: a 1672×941 slide at Scale 114.77 (Premiere's "Set to Frame Size"),
# 1280×720 footage at 150, and the 1920×309 logo at 24 that arrived four times
# too wide with its left half off the screen.
print(chr(10) + "8p · A fixed Scale, through the file's own pixel size")

_rects = interchange._prproj_index(ET.fromstring(
    '<PremiereData><VideoClipTrackItem ObjectID="1">'
    "<FrameRect>0,0,1920,1080</FrameRect><SubClip ObjectRef=\"2\"/>"
    '</VideoClipTrackItem>'
    '<VideoStream ObjectID="2"><FrameRect>0,0,1920,309</FrameRect></VideoStream>'
    "</PremiereData>"
))
_sized = interchange._prproj_detail(_rects, "1")
# ⚠ TOLD APART BY TAG, NOT BY DEPTH. Both are `<FrameRect>`; "the shallowest
# wins" — the rule the rest of `_prproj_detail` runs on — answers the SEQUENCE
# size for both, and the sum then divides a number by itself and reports every
# clip as untouched.
check("the clip item's own <FrameRect> is read as the sequence frame",
      _sized["frame"] == (1920, 1080), str(_sized["frame"]))
check("…and the VideoStream's as the file's own pixels",
      _sized["source"] == (1920, 309), str(_sized["source"]))
check("a <FrameRect> with no size in it reads as nothing at all",
      interchange._prproj_rect(ET.fromstring("<X><FrameRect>0,0,0,10</FrameRect></X>")) is None
      and interchange._prproj_rect(ET.fromstring("<X/>")) is None, "")

_base = interchange.prproj_scale_base
check("a letterhead at Scale 24 arrives at 0.24, not at full frame",
      abs(_base({"scale": 0.24, "scale_rest": 1.0}, (1920, 309), (1920, 1080)) - 0.24) < 1e-9)
# ⚠ THE CHECK THAT SAYS THIS IS SAFE. "Set to Frame Size" IS this app's fit, so
# every ordinary clip in a real project has to come out at 1.0 — anything else
# would resize a film nobody asked to have resized.
check("…while a slide at Premiere's own fit-to-frame number stays 1.0",
      abs(_base({"scale": 1.1477152, "scale_rest": 1.0}, (1672, 941), (1920, 1080)) - 1.0) < 1e-4)
check("…and 720p footage at Scale 150 in a 1080 sequence stays 1.0 too",
      abs(_base({"scale": 1.5, "scale_rest": 1.0}, (1280, 720), (1920, 1080)) - 1.0) < 1e-9)
# A source SHORTER than the frame is fitted by height, so its width fraction is
# already below 1 before Premiere's number is applied.
check("…and a tall source is measured against the height it was fitted by",
      abs(_base({"scale": 0.5, "scale_rest": 1.0}, (1080, 1920), (1920, 1080)) - 0.8889) < 1e-4)
# ⚠ THE GUARD. Premiere has two ways to make a small file fill a frame and only
# one of them writes a number: *Set to Frame Size* writes 114.77, *Scale to
# Frame Size* resamples the media and leaves Scale at 100. Nothing tells that
# clip apart from one nobody ever touched, so 100 keeps this app's fit — which
# is what BOTH of those Premiere clips look like.
check("an untouched Scale of exactly 100 is left fitted to the frame",
      _base({"scale": 1.0, "scale_rest": 1.0}, (1280, 720), (1920, 1080)) is None)
check("…and so is a clip whose file has no pixel size recorded",
      _base({"scale": 0.24, "scale_rest": 1.0}, None, (1920, 1080)) is None
      and _base({"scale": 0.24, "scale_rest": 1.0}, (1920, 309), None) is None)

# --- the same thing, once it is animated ------------------------------------
# The reference project's slides exactly: Motion parked at the fit-to-frame
# number, and a hand-added Transform zooming 80 → 100 on top of it.
_zoom, _zoom_root = _prproj_fragment([
    ("AE.ADBE Motion", [("Scale", "114.771522521973", ())]),
    ("AE.ADBE Geometry2", [("Scale Height", "100.", ((0, "80."), (2 * _TPS, "100."))),
                           ("Scale Width", "100.", ())]),
])
_zoom_tr = interchange._prproj_transform(_zoom, _zoom_root)
# ⚠ WHICH FACTOR THE KEYS REPLACE. `scale` is the PRODUCT of both components'
# resting values; the track overwrites the Transform's 100 and leaves Motion's
# 114.77 standing. Get this wrong and the slide zooms from full frame instead of
# to it.
check("the resting value the keyframes replace is recorded on its own",
      abs(_zoom_tr["scale_rest"] - 1.0) < 1e-9, str(_zoom_tr["scale_rest"]))
_zoom_keys = interchange.prproj_transform_keys(
    _zoom_tr, in_ticks=0, length_ms=3000,
    scale_base=_base(_zoom_tr, (1672, 941), (1920, 1080)),
)["keyframes"]
check("a zoom under a fitted Motion arrives as 0.80 → 1.00, the size Premiere shows",
      [k["v"] for k in _zoom_keys["scale"]] == [0.8, 1.0], str(_zoom_keys["scale"]))
# ⚠ AND THE OLD BEHAVIOUR IS STILL THERE FOR EVERYTHING THE SUM CANNOT REACH.
_relative = interchange.prproj_transform_keys(_zoom_tr, in_ticks=0, length_ms=3000)
check("…and with no pixel size to convert by, it is still the relative push",
      [k["v"] for k in _relative["keyframes"]["scale"]] == [1.0, 1.25],
      str(_relative["keyframes"]["scale"]))

# --- and what `to_project` does with it -------------------------------------
_kept = interchange.to_project(
    {
        "reader": "prproj", "name": "Sized", "fps": _FPS, "width": 0, "height": 0,
        "files": {"p": {"name": "logo.png", "pathurl": ""}},
        "video": [{"clips": [dict(_c(0, 240, name="logo", file="p"), scale=0.24)],
                    "transitions": []}],
        "audio": [], "warnings": [],
    },
    lambda name: {"kind": "image", "upload_id": "u-logo", "duration_ms": 0},
    background="#000000",
)
check("a clip's imported size reaches the frame it becomes",
      _kept["frames"][0]["scale"] == 0.24, str(_kept["frames"][0]["scale"]))
# ⚠ ABSENT MEANS FITTED, and it has to stay that way: `scale` is on every clip
# every reader has ever produced, so a missing one writing 0 (or anything but
# 1.0) would resize every import that predates this.
_unsized = interchange.to_project(
    {
        "reader": "fcp7", "name": "Plain", "fps": _FPS, "width": 0, "height": 0,
        "files": {"p": {"name": "shot.png", "pathurl": ""}},
        "video": [{"clips": [_c(0, 240, name="pic", file="p")], "transitions": []}],
        "audio": [], "warnings": [],
    },
    lambda name: {"kind": "image", "upload_id": "u-pic", "duration_ms": 0},
    background="#000000",
)
check("…and a clip that carries no size is still fitted to the frame",
      _unsized["frames"][0]["scale"] == 1.0, str(_unsized["frames"][0]["scale"]))

# --- and what `to_project` does with it -------------------------------------
_moved = interchange.to_project(
    {
        "reader": "prproj", "name": "Moved", "fps": _FPS, "width": 0, "height": 0,
        "files": {"p": {"name": "shot.png", "pathurl": ""}},
        "video": [{"clips": [dict(_c(0, 240, name="pic", file="p"),
                                  offset=[0.1, -0.1], opacity=0.5,
                                  keyframes={"scale": [{"t": 0, "v": 1.0, "ease": "linear"},
                                                       {"t": 500, "v": 1.2, "ease": "linear"}]})],
                    "transitions": []}],
        "audio": [], "warnings": [],
    },
    lambda name: {"kind": "image", "upload_id": "u-pic", "duration_ms": 0},
    background="#000000",
)
_frame = _moved["frames"][0]
check("a moved clip lands where Premiere put it",
      (_frame["x"], _frame["y"]) == (0.6, 0.4), str((_frame["x"], _frame["y"])))
check("…keeps its opacity",
      _frame["opacity"] == 0.5, str(_frame["opacity"]))
check("…and carries its animation onto the timeline",
      len(_frame["keyframes"].get("scale") or []) == 2, str(_frame["keyframes"]))

# ⚠ **AND IT MUST NOT TOUCH A PLACEHOLDER CARD.** A card on a row above the
# bottom one is parked at `opacity: 0` on purpose; an imported fade writing 1.0
# over it puts an opaque rectangle back across the whole film, which is the
# sixty-eight-seconds-of-black fault that branch exists to prevent.
_gap = interchange.to_project(
    {
        "reader": "prproj", "name": "Gap", "fps": _FPS, "width": 0, "height": 0,
        "files": {"a": {"name": "there.png", "pathurl": ""},
                  "b": {"name": "gone.png", "pathurl": ""}},
        "video": [
            {"clips": [_c(0, 240, name="base", file="a")], "transitions": []},
            {"clips": [dict(_c(0, 240, name="over", file="b"), opacity=1.0,
                            keyframes={"opacity": [{"t": 0, "v": 1.0, "ease": "linear"},
                                                   {"t": 100, "v": 1.0, "ease": "linear"}]})],
             "transitions": []},
        ],
        "audio": [], "warnings": [],
    },
    lambda name: ({"kind": "image", "upload_id": "u-a", "duration_ms": 0}
                  if (name or "").lower() == "there.png" else None),
    background="#000000",
)
_card = [f for f in _gap["frames"] if f["kind"] == "color"][0]
check("an imported fade never lights up a placeholder card over the film",
      _card["opacity"] == 0.0 and not _card["keyframes"], str(_card["opacity"]))


# ---------------------------------------------------------------------------
# 8k · A file that did not arrive must say WHERE it lived
# ---------------------------------------------------------------------------
# ⚠ THE NAME OF A MISSING FILE IS NOT SOMETHING A USER CAN ACT ON. The report
# named the file and stopped there, and the live import that exposed it lost
# exactly three files out of twenty-eight: the VOICEOVER sat inside the project
# folder and arrived on all 23 of its clips, while the music bed and a logo lived
# inside ANOTHER project's folder — which is ordinary, because a logo and a music
# bed are reused across a whole series. The user attached the only folder there
# was any reason to attach, read "tech_oasis-….mp3 did not arrive", and had
# nothing to do next; it read as this app being unable to take music at all.
#
# Both readers already know the answer: `files[key]["pathurl"]` is the full path
# the editor itself wrote. `report["missing"]` is that, one row per FILE, with
# the folder, the kind and how many clips wanted it — while `placeholders` stays
# per CLIP, because that is what the gaps on the timeline are counted from.
print(chr(10) + "8k · a missing file names its folder")

# A Windows path, built rather than written, so this source file never has to
# carry a backslash escape that a later edit can quietly change.
BS = chr(92)
_WIN = BS.join(["C:", "Films", "Ep8", "Audio", "music.mp3"])
_URL = "file://localhost/C:/Films/Shared%20Art/logo.png"


def _spread():
    """One cut whose media is spread over three folders — one of them attached.

    ⚠ THE LOGO IS ON TWO CLIPS ON PURPOSE. One file used twice printed its own
    name twice in the dialog, which reads as two different broken files rather
    than one folder nobody attached.
    """
    return {
        "reader": "test", "name": "Spread", "fps": _FPS, "width": 0, "height": 0,
        "files": {
            "here": {"name": "shot.png",
                     "pathurl": BS.join(["C:", "Films", "Ep8", "shot.png"])},
            "vo": {"name": "vo.mp3",
                   "pathurl": BS.join(["C:", "Films", "Ep8", "Audio", "vo.mp3"])},
            "logo": {"name": "logo.png", "pathurl": _URL},
            "bed": {"name": "music.mp3", "pathurl": _WIN},
        },
        "video": [{"clips": [
            _c(0, 240, name="shot", file="here"),
            _c(240, 480, name="logo", file="logo"),
            _c(480, 720, name="logo again", file="logo"),
        ], "transitions": []}],
        "audio": [{"clips": [
            _c(0, 240, name="vo", file="vo"),
            _c(0, 720, name="music", file="bed"),
        ]}],
        "warnings": [],
    }


def _only_project_folder(name):
    """What the route's `resolve` does when ONE folder was attached — the
    project's own. ⚠ The voiceover resolves and the music does not, which is
    exactly the live case: both are .mp3, and only one of them lives here."""
    return {
        "shot.png": {"kind": "image", "upload_id": "u1", "duration_ms": 0},
        "vo.mp3": {"kind": "audio", "upload_id": "u2", "duration_ms": 0},
    }.get((name or "").lower())


_spr = interchange.to_project(_spread(), _only_project_folder, background="#000000")
_missing = _spr["report"]["missing"]
_by_name = {m["name"]: m for m in _missing}

check("a missing file is listed once, however many clips wanted it",
      len(_missing) == 2, str([m["name"] for m in _missing]))
check("…and says how many clips that was",
      _by_name["logo.png"]["clips"] == 2, str(_by_name.get("logo.png")))
# ⚠ THE WHOLE POINT OF THE SECTION. A path this app cannot read is still the one
# thing that tells a person which folder to go and add.
check("a Windows path gives up the folder it sat in",
      _by_name["music.mp3"]["folder"] == "C:/Films/Ep8/Audio",
      str(_by_name["music.mp3"]["folder"]))
# ⚠ AND AN `xmeml` WRITES A `file://` URL WITH ITS SPACES PERCENT-ENCODED, so
# reading it raw hands the user a folder called "Shared%20Art" that they will not
# find on their own disk.
check("…and so does a percent-encoded file:// URL",
      _by_name["logo.png"]["folder"] == "file://localhost/C:/Films/Shared Art",
      str(_by_name["logo.png"]["folder"]))
# ⚠ A MISSING PICTURE BECOMES A CARD ON THE TIMELINE; A MISSING SOUND BECOMES
# NOTHING AT ALL (E45 and the branch above it). They are not the same loss and
# the list must not print them as though they were.
check("the sound is marked as a sound, and the picture as a picture",
      _by_name["music.mp3"]["kind"] == "sound"
      and _by_name["logo.png"]["kind"] == "picture",
      str([(m["name"], m["kind"]) for m in _missing]))
check("…and the sound is listed FIRST, because it left no trace to find",
      _missing[0]["name"] == "music.mp3", str([m["name"] for m in _missing]))
# ⚠ `placeholders` IS PER CLIP AND STAYS THAT WAY. It is what the gaps on the
# timeline are counted from — collapsing it here would under-count them.
check("placeholders is still one entry per CLIP",
      sorted(_spr["report"]["placeholders"])
      == ["logo.png", "logo.png", "music.mp3"],
      str(_spr["report"]["placeholders"]))

# ⚠ "NO SOUND WAS BROUGHT IN" WAS A LIE WHENEVER SOME OF IT WAS. The live report
# said it while 23 clips of voiceover sat on the timeline — which makes every
# other sentence in the report suspect too.
_sound_line = " ".join(w for w in _spr["report"]["warnings"] if "sound clip(s)" in w)
check("the warning does not claim no sound arrived when some did",
      "no sound was brought in" not in _sound_line, _sound_line)
check("…and it points at the folders instead of just saying 'attach the files'",
      "folder" in _sound_line, _sound_line)

# ⚠ AND THE OTHER HALF: when NOTHING resolved, "0 sounds on 0 rows" is true and
# still reads like a file with no audio in it, so that sentence must still be
# there. Losing it is how the fix above turns into a different silent failure.
_none = interchange.to_project(_spread(), lambda _n: None, background="#000000")
check("…but when no sound arrived at all it still says so plainly",
      any("no sound was brought in" in w for w in _none["report"]["warnings"]),
      str([w for w in _none["report"]["warnings"] if "sound" in w]))

# A format that carries no path at all (an EDL) must still list the file — with
# an empty folder, which the dialog words rather than printing a blank line.
_nopath = interchange.to_project(
    _lanes([_c(0, 240, name="Graphic", file="")]), lambda _n: None, background="#000000"
)
check("a file with no recorded path is still listed, with no folder",
      [(m["name"], m["folder"]) for m in _nopath["report"]["missing"]]
      == [("Graphic", "")],
      str(_nopath["report"]["missing"]))

# ⚠ AND THE DIALOG HAS TO SHOW IT. The whole fix is invisible if the modal keeps
# rendering `placeholders` — the server would be carrying folders nobody sees.
_modal_8k = open(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client", "src", "components", "ProjectImportModal.jsx",
    ),
    encoding="utf-8",
).read()
check("the dialog reads the located list, not the bare names",
      "read?.missing" in _modal_8k and "missingByFolder" in _modal_8k, "")
check("…and prints the folder for each group",
      "group.folder" in _modal_8k, "")


# ---------------------------------------------------------------------------
# 9 · The EDL — the floor, and it has to be frame-exact
# ---------------------------------------------------------------------------
# ⚠ AN EDL IS A CONFORM REFERENCE, so "roughly right" is worthless: every
# timecode in it is a frame number somebody will cut against. And it holds ONE
# video track, which this asserts is REPORTED rather than quietly flattened.
print("\n9 · the EDL")

edl = interchange.write_edl(model)
edl_lines = [ln for ln in edl.split("\n") if ln.strip()]
events = [ln for ln in edl_lines if ln[:3].isdigit()]

check("it opens with a TITLE", edl.startswith("TITLE: My Test Film"), edl[:40])
check("…and declares non-drop", "FCM: NON-DROP FRAME" in edl)
# The base row holds Shot 1, Shot 2 and the colour card; the two audio lanes
# hold three clips between them. The video clip on track 1, the clip on track 2
# and the overlay are all on upper rows and must NOT be here.
check("only the base video row and the audio are events", len(events) == 3 + 3,
      f"{len(events)}\n" + "\n".join(events))
check("every event names its clip", edl.count("* FROM CLIP NAME:") == len(events))
check("the upper rows are left out", "Take 4" not in edl and "Image 1" not in edl, "")

first = events[0].split()
check("event numbers start at 001", events[0].startswith("001"), events[0])
check("the first event is video", " V " in events[0], events[0])
check("…starting at 00:00:00:00 and running to 00:00:02:00",
      first[-2] == "00:00:00:00" and first[-1] == "00:00:02:00", events[0])
# ⚠ THE RECORD TIMES MUST BE CONTINUOUS AND ASCENDING, and the gap must survive:
# the colour card sits at 5s, not at 3s where the clip before it ended.
video_events = [e for e in events if " V " in e]
check("the gap is in the EDL too",
      video_events[2].split()[-2] == "00:00:05:00", video_events[2])
check("the audio events use A and A2",
      any(" A " in e for e in events) and any(" A2 " in e for e in events),
      "\n".join(events))
# ⚠ REEL NAMES: eight characters, and never two files sharing one. A collision
# would conform two different pictures as one tape.
reels = [e.split()[1] for e in events]
check("every reel name is 8 characters or fewer", all(len(r) <= 8 for r in reels), str(reels))
check("no two different files share a reel name",
      len({r for r in reels}) == len({e.split()[1] for e in events}), str(reels))

edl_report = interchange.report_of(model, "edl")
edl_dropped = {row["what"]: row["count"] for row in edl_report["dropped"]}
check("the report says which format it is about", edl_report["format"] == "edl")
for what in ("clips on upper video rows (an EDL holds one)",
             "dissolves (an EDL here is cuts only)"):
    check(f"EDL reports: {what}", what in edl_dropped, str(sorted(edl_dropped)))
# ⚠ AND THE BASE LIST IS STILL THERE. The format's ceiling is ADDED to what no
# format can carry, never substituted for it.
check("…on top of the grades and the text, not instead of them",
      "effects and colour grades" in edl_dropped and "text clips" in edl_dropped,
      str(sorted(edl_dropped)))
check("the XML report does NOT carry the EDL's losses",
      "dissolves (an EDL here is cuts only)"
      not in {r["what"] for r in interchange.report_of(model, "fcp7")["dropped"]})


# ---------------------------------------------------------------------------
# 10 · The After Effects script
# ---------------------------------------------------------------------------
# ⚠ EXTENDSCRIPT IS ES3, AND A SYNTAX ERROR MEANS THE SCRIPT DOES NOT LOAD AT
# ALL — no comp, no message the user can act on. There is no ExtendScript here to
# run it in, so this checks the two things that would break it silently: a
# reserved word used as a key, and a non-ASCII byte in a file whose encoding
# ExtendScript decides for itself.
print("\n10 · the After Effects script")

jsx = interchange.write_ae_jsx(model)
check("it tells the user how to run it", "Run Script File" in jsx)
check("it is wrapped in one function call", jsx.rstrip().endswith("})();"))
check("it opens an undo group", "app.beginUndoGroup(" in jsx and "app.endUndoGroup()" in jsx)
check("it finds its own media next to itself", "$.fileName" in jsx
      and "Folder.selectDialog" in jsx)
check("it builds a comp at the project's size and rate",
      "proj.items.addComp(" in jsx and '"width": 1920' in jsx and '"fps": 24' in jsx)

# ⚠ `in` IS A RESERVED WORD IN ES3 — `{in: 0}` is a syntax error and the whole
# script fails to load. The data block says `srcIn` for exactly this reason.
for word in ("\"in\":", "\"for\":", "\"new\":", "\"class\":", "\"delete\":"):
    check(f"no reserved word as a key: {word}", word not in jsx, word)
check("the source in point is called srcIn", '"srcIn"' in jsx)

check("the script is pure ASCII", all(ord(c) < 128 for c in jsx),
      repr([c for c in jsx if ord(c) >= 128][:5]))

# Every layer the model holds is in the data, bottom lane first.
import re as _re  # noqa: E402

names = _re.findall(r'"name": "([^"]+)"', jsx)
check("every clip and sound is a layer",
      names.count("Shot 1") == 1 and names.count("Take 4") == 1
      and names.count("Image 1") == 1, str(names))
check("the overlay is LAST of the pictures, so it lands on top",
      names.index("Image 1") > names.index("Take 4"), str(names))
check("the audio comes after the pictures",
      names.index("voice.mp3") > names.index("Image 1"), str(names))
check("an audio layer is marked as one", '"audio": true' in jsx)
check("…and carries its level", '"level": 0.5' in jsx)

# The times are SECONDS here, not frames — AE's own unit.
check("the second clip starts at 2 seconds", '"tlStart": 2.0' in jsx, "")
check("the video clip's in point is half a second", '"srcIn": 0.5' in jsx, "")

ae_dropped = {r["what"]: r["count"] for r in interchange.report_of(model, "aftereffects")["dropped"]}
check("AE reports that it cannot do the dissolve",
      "dissolves (After Effects has no transition object)" in ae_dropped,
      str(sorted(ae_dropped)))
check("…but keeps the upper rows, unlike the EDL",
      "clips on upper video rows (an EDL holds one)" not in ae_dropped)


# ---------------------------------------------------------------------------
# 11 · One model, three writers
# ---------------------------------------------------------------------------
print("\n11 · the format table")

check("three formats", sorted(interchange.FORMATS) == ["aftereffects", "edl", "fcp7"],
      str(sorted(interchange.FORMATS)))
check("an unknown format folds down to the default",
      interchange.normalise_format("premiere-2027") == "fcp7")
check("…and so does an empty one", interchange.normalise_format("") == "fcp7")
check("extensions", [interchange.format_ext(f) for f in ("fcp7", "aftereffects", "edl")]
      == ["xml", "jsx", "edl"])
# ⚠ EVERY WRITER TAKES THE SAME TWO ARGUMENTS, even the two that ignore
# `base_path` — `write_document` calls them all the same way.
for fmt in interchange.FORMATS:
    doc = interchange.write_document(model, fmt, base_path="D:/Films/Cut")
    check(f"{fmt}: write_document returns text", isinstance(doc, str) and len(doc) > 100)

# ⚠ THE MEDIA FOLDER IS THE SAME WHICHEVER FORMAT IS CHOSEN. Only the one
# document at the top of the zip changes — a user who downloads two of them must
# not get two different sets of pictures.
zips = {}
for fmt in ("fcp7", "aftereffects", "edl"):
    path = os.path.join(work, "bundle", f"film-{fmt}.zip")
    interchange.bundle(model, path, f"Film.{interchange.format_ext(fmt)}", fmt=fmt)
    with zipfile.ZipFile(path) as zf:
        zips[fmt] = sorted(n for n in zf.namelist() if n.startswith(interchange.MEDIA_DIR + "/"))
        check(f"{fmt}: the right document is in the zip",
              f"Film.{interchange.format_ext(fmt)}" in zf.namelist(), str(zf.namelist()[:3]))
        readme = zf.read("README.txt").decode("utf-8")
        check(f"{fmt}: the README is the one for this format",
              ("Run Script File" in readme) == (fmt == "aftereffects")
              and ("CMX3600" in readme) == (fmt == "edl"), readme[:60])
check("all three zips carry the identical media folder",
      zips["fcp7"] == zips["aftereffects"] == zips["edl"], str(zips))


# ---------------------------------------------------------------------------
# 12 · The editor's half of the import
# ---------------------------------------------------------------------------
# ⚠ A SOURCE CHECK, AND IT IS NOT A SUBSTITUTE FOR OPENING THE EDITOR (G7). It
# is here for one class of fault that a browser would show only by ACTING NORMAL:
# the server hands back clips and the client places them, so a key with the wrong
# spelling, a row number left un-rebased, or a `setNotice` where a `flush` should
# be, all produce an import that looks like it worked and saves nothing.
print("\n12 · the editor's half")

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
editor = open(
    os.path.join(root, "client", "src", "components", "AnimaticEditor.jsx"),
    encoding="utf-8",
).read()
modal = open(
    os.path.join(root, "client", "src", "components", "ProjectImportModal.jsx"),
    encoding="utf-8",
).read()
apijs = open(os.path.join(root, "client", "src", "api.js"), encoding="utf-8").read()
prproj_src = open(os.path.join(root, "interchange.py"), encoding="utf-8").read()
# ⚠ READ AS TEXT, NOT IMPORTED. Importing `server/animatics.py` drags in the
# config module, and G13 is the entry about what that cost the last time a test
# did it — real project folders written, real quota spent. A guard on the source
# is the whole assertion here anyway.
animsrv = open(os.path.join(root, "server", "animatics.py"), encoding="utf-8").read()
editorcss = open(
    os.path.join(root, "client", "src", "styles", "animatic-editor.css"), encoding="utf-8"
).read()

check("the gear offers both directions",
      '"project-import"' in editor and '"project-file"' in editor)
# ⚠ IMPORT ABOVE EXPORT — `extra` is drawn in the order it is written, and the
# whole reason it is an array is that the order is the menu.
check("…with Import above Export",
      editor.index('"project-import"') < editor.index('"project-file"'))
check("…and Delete still last",
      editor.index('"project-file"') < editor.index('id: "delete"'))

check("the dialog is rendered", "<ProjectImportModal" in editor)
check("…and cannot be closed mid-write", "!importBusy && setProjectImportOpen(false)" in editor)

# ⚠ THE ONE KEY THAT WOULD FAIL SILENTLY. `flush` builds the saved document by
# spreading the patch over it, and the document's field is `audioTracks`. A patch
# written `audio_tracks` (which is what the API answers with, and therefore what
# a copy-paste produces) is simply an unknown key: the import looks perfect on
# screen and the sounds are gone on the next reload.
apply_at = editor.index("async function applyProjectImport")
# ⚠ TO THE END OF THE FUNCTION, NOT A FIXED NUMBER OF CHARACTERS. This used to
# read `apply_at + 6000`, and the day the function grew past that every check
# below went green-to-red at once for a reason that had nothing to do with what
# they test — the `flush` call had simply fallen off the end of the window. The
# next declaration at the component's own indentation is where this function
# stops.
_apply_end = min(
    (at for at in (editor.find(mark, apply_at + 80)
                   for mark in ("\n  /**", "\n  async function ", "\n  function ",
                                "\n  const "))
     if at > 0),
    default=len(editor),
)
apply_src = editor[apply_at:_apply_end]
check("the save patch uses the document's own spelling for audio",
      "audioTracks: nextAudio" in apply_src, "")
check("…and audio_tracks is NOT used as a patch key",
      "audio_tracks:" not in apply_src, "")
for key in ("frames:", "transitions:", "layers:", "assets:"):
    check(f"the import saves {key.strip(':')} in the same write",
          key + " next" in apply_src or key + " cards" in apply_src, "")

# ⚠ RE-BASED, NOT TAKEN AS SENT. The server's `track` is relative (0,1,2…); using
# it raw drops an import on top of the rows the project already has.
check("picture rows are re-based onto free rows",
      "base + (f.track || 0)" in apply_src, "")
check("…counting EMPTY rows too, or an import lands on an unfilled one",
      "videoTracks.map((r) => r.track)" in apply_src, "")
check("…and it refuses rather than overflowing the row cap",
      "MAX_PICTURE_TRACK" in apply_src, "")
# ⚠ AN AUDIO CLIP WITH layer_id "" GOES ON THE DEFAULT LANE, i.e. straight into
# whatever is already there. Every imported lane gets a row of its own.
check("audio lanes are created rather than defaulted", "laneFor.set(" in apply_src, "")
# ⚠ EVERY LIST OF ROWS THIS MAKES HAS TO BE SEATED, and the check is written
# against the LISTS rather than against a count. It used to assert "exactly 2",
# which is a number that goes stale the moment a row kind is added — and it did:
# text and shape rows arrived and the count said 4. Naming the lists means a new
# row kind that is never seated fails here, which is the fault worth catching.
_row_lists = re.findall(r"for \(const row of (\w+)\) seatNewLane", apply_src)
check("…and every new row claims its place in the saved stack order",
      set(_row_lists) == {"newRows", "audioRows", "textRows", "shapeRows"},
      str(_row_lists))

# ⚠ ONE WRITE = ONE UNDO STEP, which is the real safety net behind "adds, never
# replaces". Two flushes would make Ctrl+Z take half the import back out.
check("the whole import is ONE write", apply_src.count("await flush(") == 1, "")
check("…and the notice says undo works", "Undo takes it all back out" in apply_src, "")

# The dialog must not be able to change anything on its own.
check("the dialog only READS", "importProjectFile" in modal and "saveAnimatic" not in modal)
check("…and applying is the caller's job", "onApply(read)" in modal)
check("…the report is dropped when the file changes", "setRead(null)" in modal)
# A folder of footage is a long upload; the default timeout is for calls that
# answer in a second.
check("the upload is given a long timeout", "timeoutMs" in apijs
      and "interchange/import" in apijs)

# ⚠ E65 — THE DIALOG CANNOT BE DISMISSED BY ACCIDENT, AND SO IT MUST MOVE.
# A stray click on the backdrop threw away a read, its folder list and its
# warnings, half an hour into a real `.prproj` import. Escape did the same. Both
# are one line each to bring back, and neither would fail any other check here.
check("the backdrop closes nothing",
      'className="modal-overlay">' in modal, "")
check("…and Escape closes nothing either",
      '"Escape"' not in modal, "")
check("…so ✕ and Cancel are the only ways out",
      modal.count("onClose()") == 1 and "onClick={onClose}" in modal, "")
# ⚠ AND THE DRAGGING ITSELF IS NOT IN THIS FILE — do not add it back here.
# `client/src/dialog_move.js` carries one implementation for every dialog in the
# app; `tests/dialog_frame_check.py` pins it, and the app-wide rule with it.
check("the dragging is the shared one, not a second copy",
      "onPointerDown" not in modal and "setPointerCapture" not in modal, "")
# ⚠ THE TITLE BAR STICKS AND CARRIES THE ✕. This dialog scrolls further than any
# other — a report can name a dozen folders — and a ✕ that scrolls away on a
# dialog that no longer closes on the backdrop leaves no exit on screen at all.
check("the title bar sticks to the top of the card",
      '"an-xchg-bar"' in modal and ".an-xchg-bar {" in editorcss
      and "position: sticky" in editorcss, "")
check("…and the ✕ rides in it",
      modal.index('"an-xchg-bar"') < modal.index('className="modal-close"'), "")
# ⚠ IN THE CORNER, like every other dialog's ✕ and like a window's. In the
# flow of the bar it lined up with the middle of the heading and read as a
# control belonging to the title rather than to the window.
check("…pinned to the corner of it, not sitting in the flow",
      ".an-xchg-bar .modal-close {" in editorcss
      and "position: absolute" in editorcss[editorcss.index(".an-xchg-bar .modal-close {"):]
      [:200], "")

# --- the .prproj route, which is now the FIRST answer and used to be the second
# ⚠ THIS INVERTED, AND §8n IS WHY. The rule here was "the flag may only be
# reachable AFTER a refusal has been printed", so that nobody took an
# experimental reader without seeing the reliable door beside it. In practice the
# reliable door is not one most users have — they came here BECAUSE they cannot
# open Premiere — and what the rule actually bought was a red panel, a second
# button, and a SECOND upload of the same 27 files before anything appeared.
# The honesty it was protecting now travels with the result instead: the BEST
# GUESS badge, and the first line of `warnings`, which still names File › Export
# › Final Cut Pro XML. ⚠ The ROUTE's refusal is untouched — see the guard on
# `experimental: bool = False` below; this is the dialog knowing its own file.
check("the picker will show a .prproj", ".prproj" in modal)
check("a .prproj asks for the best-effort read on the first press",
      "const wantGuess = experimental || isPrproj;" in modal, "")
check("…so there is no refusal panel left to press through",
      "offerGuess" not in modal, "")
check("…and it is decided off the EXTENSION, not the refusal's wording",
      "/\.prproj$/i" in modal, "")
check("what comes back is badged a guess on screen",
      'read?.reader === "prproj"' in modal and "an-xchg-guess" in modal, "")
check("the flag reaches the server as form data",
      'fd.append("experimental"' in apijs, "")

# --- the footage picker ADDS, because one project's media is in many folders
# ⚠ REPORTED FROM A LIVE TEST: "maine ek image select kiya to sirf wahi image
# aaya". It was not the user's mistake. A file picker can only see inside ONE
# folder at a time, and a real project keeps its media in `Images/`, `Videos/`
# and `Audio/` — so a picker that REPLACES its list on every pick can never
# collect more than one of them, and silently drops what was chosen before.
check("choosing footage adds to what is there rather than replacing it",
      "const addMedia" in modal and "setMedia((prev)" in modal, "")
check("…de-duplicated by NAME, which is what the server matches on",
      "byName.set(file.name.toLowerCase()" in modal, "")
check("…and a whole folder can be taken in one go",
      "webkitdirectory" in modal, "")
check("…with only real media sent, not the project file beside it",
      "MEDIA_RE.test" in modal, "")
check("…and a way to undo the choice",
      "Forget the footage chosen so far" in modal, "")
# ⚠ SEEN LIVE: uvicorn's --reload was restarting when the button was pressed, so
# the guess died on a dead backend and the offer to try it vanished — leaving a
# network error and no way back to the button. A server that never answered said
# nothing about the file, so the offer must survive it. A FLAG from `api.js`,
# not a match on the sentence, which is written for a person and will be reworded.
check("a dead backend does not count as having spent the .prproj offer",
      "e?.offline" in modal and "offline.offline = true" in apijs, "")
# ⚠ AND THE RETRY HAS TO OUTLAST A `--reload` RESTART. Paid for live: a source
# edit restarted uvicorn mid-import, all three attempts hit a refused connection
# inside 3ms — 0 bytes sent, so it was never the upload — and the user was told
# the backend was not running about a server that was fine two seconds later. A
# flat 700ms x3 covers 1.4s; a restart that reconnects to Mongo takes 3-5s.
check("a refused connection is retried with a DOUBLING wait, not a flat one",
      "delayMs * 2 ** (i - 1)" in apijs, "")
# ⚠ WHITESPACE-INSENSITIVE, because the two arguments sit on their own lines
# and any reformat would break a literal match without the behaviour changing.
# `5` alone appears everywhere in this file and the delay alone says nothing
# about how many times it is waited, so they are matched TOGETHER.
_retry_args = re.sub(r"\s+", " ", apijs)
check("…for long enough to outlast a backend restart",
      "body: payload }, 5, 700," in _retry_args, "attempts/delay pair not found")

# --- two buttons side by side are one pair, and one size -------------------
# ⚠ REPORTED FROM A SCREENSHOT, AND IT IS A REPEAT. `.btn.primary` in `base.css`
# carries a global `margin-top: 1.1rem` because it is normally the last control
# in a FORM. In a ROW it pushes the gold button down; the ghost beside it
# stretches to the taller line and ends up BOTTOM-aligned and visibly bigger.
# Nine other rows in the app already reset it, each with a comment; this dialog
# shipped without one. Pinned because it costs one line to lose again and the
# only thing that reports it is somebody looking at the screen.
# ⚠ SLICED TO THE CLOSING BRACE, not to a fixed number of characters. A fixed
# window silently stops covering the end of the rule the moment somebody adds a
# comment inside it — which is exactly what happened the first time this was
# written, and it read as the FIX being missing rather than the slice.
_actions_at = editorcss.index(".an-xchg-actions .btn {")
_actions_css = editorcss[_actions_at: editorcss.index("}", _actions_at) + 1]
check("the dialog's button row cancels the primary's form margin",
      "margin-top: 0" in _actions_css, _actions_css[:80])
check("…and gives both buttons the same box, not just the same minimum",
      "height: 40px" in _actions_css and "min-width" in _actions_css, "")
check("…and the row centres them rather than letting one stretch taller",
      "align-items: center" in editorcss[
          editorcss.index(".an-xchg-actions {"): _actions_at], "")

# ⚠ AND THE FOUR THAT MATTER ARE PROVED TO READ **False** WHEN THE CODE IS
# BROKEN. A source check that cannot fail is decoration, and three of these guard
# faults that a browser would show only by ACTING NORMAL — the import looks
# perfect and the work is gone on the next reload. Each guard is re-run against a
# deliberately broken copy of the same function.
#
# ⚠ EACH ROW CARRIES THE SOURCE IT GUARDS. Every guard used to be re-run against
# `apply_src` whatever it was written about, so the first guard over a different
# file read False on the GOOD source and reported itself broken.
_EOL = chr(10)
_ALPHA_PICK = '"RGBA" if _keeps_alpha(im) else "RGB"'

for _name, _good, _broken, _guard in (
    (
        "the audio key misspelt as the API's own",
        apply_src,
        apply_src.replace("audioTracks: nextAudio", "audio_tracks: nextAudio"),
        lambda b: "audioTracks: nextAudio" in b and "audio_tracks:" not in b,
    ),
    (
        "rows taken as sent instead of re-based",
        apply_src,
        apply_src.replace("base + (f.track || 0)", "f.track || 0"),
        lambda b: "base + (f.track || 0)" in b,
    ),
    (
        "two writes, so undo would only half work",
        apply_src,
        apply_src.replace("await flush({", "await flush({}); await flush({", 1),
        lambda b: b.count("await flush(") == 1,
    ),
    (
        "empty rows ignored when choosing the first free row",
        apply_src,
        apply_src.replace("videoTracks.map((r) => r.track)", ""),
        lambda b: "videoTracks.map((r) => r.track)" in b,
    ),
    # ⚠ THE QUIETEST FAULT IN THIS WHOLE FEATURE. `readFile` takes
    # `experimental` as its first argument, and React hands a click EVENT to a
    # bare `onClick={readFile}` — an event object is truthy, so the ordinary
    # "Read the file" button would ask the server to guess at every file, and
    # the only visible sign would be a .prproj that was never refused.
    # ⚠ AND THE ARGUMENT IS `guessed`, NOT NOTHING. The footer re-reads after
    # footage is attached, and `readFile()` there asks for the STRICT read — which
    # for a `.prproj` is the refusal, with the experimental offer already spent
    # and hidden. See `editor_project_import_check.py`.
    (
        "the click event passed in as the experimental flag",
        modal,
        modal.replace("onClick={() => readFile(guessed)}", "onClick={readFile}"),
        lambda b: ("onClick={readFile}" not in b
                   and "onClick={() => readFile(guessed)}" in b),
    ),
    # ⚠ AND THE REFUSAL ITSELF, which is the safety rail the whole of section 8c
    # rests on: `experimental` defaulting to true would turn the one guess in
    # `interchange.py` into what every import does.
    (
        "the experimental reader made the default",
        prproj_src,
        prproj_src.replace("experimental: bool = False", "experimental: bool = True"),
        lambda b: "experimental: bool = False" in b,
    ),
    # ⚠ The one the user reported twice. Losing this line does not break a test
    # anywhere else — it just makes the pair look like two different controls.
    (
        "the primary's form margin left on in a button row",
        _actions_css,
        _actions_css.replace("margin-top: 0;", ""),
        lambda b: "margin-top: 0" in b,
    ),
    # ⚠ The one the live test found. Going back to `setMedia(Array.from(...))`
    # looks harmless and costs the user every folder but the last — with no
    # error, just an import where most of the pictures are missing.
    (
        "the footage picker replacing its list instead of adding to it",
        modal,
        modal.replace("setMedia((prev) => {", "setMedia(() => {"),
        lambda b: "setMedia((prev) => {" in b,
    ),
    # ⚠ The one §8k was written for. Going back to the bare `placeholders` list
    # leaves the server carrying folders nobody ever sees, and the report returns
    # to naming files a user has no way to go and find.
    (
        "the dialog printing bare names instead of the located list",
        modal,
        modal.replace("missingByFolder.slice(0, 4)", "[].slice(0, 4)"),
        lambda b: "missingByFolder.slice(0, 4)" in b,
    ),
    # ⚠ THE ONE THAT COST AN IMPORT. Clearing the report when footage is attached
    # takes away the folders the user went to fetch, and flips the footer back to
    # the read a `.prproj` is refused for. Driven end to end in
    # `editor_project_import_check.py`; this is the cheap guard beside it.
    (
        "the report thrown away when footage is added",
        modal,
        modal.replace("setStale(true);" + _EOL + "  };",
                      "setRead(null);" + _EOL + "  };"),
        lambda b: "setStale(true);" + _EOL + "  };" in b,
    ),
    # ⚠ And the folder the report sends somebody to is another film's, full of
    # media this cut never asked for.
    (
        "the whole second folder uploaded instead of what was asked for",
        modal,
        modal.replace("if (onlyWanted.length) picked = onlyWanted;", ""),
        lambda b: "if (onlyWanted.length) picked = onlyWanted;" in b,
    ),
    # ⚠ E65. The backdrop handler is one line, it is on nearly every other
    # dialog in this app, and putting it back here throws away a long read on a
    # click that missed the card.
    (
        "the backdrop closing the dialog again",
        modal,
        modal.replace('className="modal-overlay">',
                      'className="modal-overlay" onClick={onClose}>'),
        lambda b: 'className="modal-overlay">' in b and "modal-overlay\" onClick" not in b,
    ),
    # ⚠ And the ✕ going back to the corner of a card that scrolls, where it
    # leaves the screen the moment the report is read.
    (
        "the ✕ taken back out of the sticky title bar",
        modal,
        modal.replace('<div className="an-xchg-bar">', "<div>"),
        lambda b: '<div className="an-xchg-bar">' in b,
    ),
    # ⚠ THE UPLOAD HALF OF THE CUT-OUT FIX, and it has no test of its own because
    # importing this module is what G13 is about. `convert("RGB")` on the way IN
    # destroys the alpha permanently — the renderer honouring it afterwards then
    # fixes nothing, which is exactly the shape of half-fix that looks done.
    # `tests/effects_check.py` holds the other half.
    (
        "a transparent upload flattened onto black before it is even stored",
        animsrv,
        animsrv.replace(_ALPHA_PICK, '"RGB"'),
        lambda b: b.count(_ALPHA_PICK) == 2,
    ),
    # ⚠ THE THREE LINES THE FIXED-SCALE FIX HANGS ON, none of which fails loudly.
    # Each one produces a film that renders, exports and is simply the wrong
    # size — which is exactly how the logo shipped four times too wide.
    #
    # Both `<FrameRect>`s are the SEQUENCE's if the tag is not asked for, so the
    # sum divides a number by itself and reports every clip as untouched.
    (
        "the sequence's frame size taken as the source file's own",
        prproj_src,
        prproj_src.replace('if got["source"] is None and tag == "VideoStream":',
                           'if got["source"] is None:'),
        lambda b: 'if got["source"] is None and tag == "VideoStream":' in b,
    ),
    # Without the guard, a clip nobody ever touched is resized on the strength of
    # a Scale of 100 that means "leave me alone" — see `prproj_scale_base`.
    (
        "an untouched Scale of 100 carried across as if somebody had set it",
        prproj_src,
        prproj_src.replace("    if abs(resting - 1.0) <= 1e-6:\n        return None\n", ""),
        lambda b: "    if abs(resting - 1.0) <= 1e-6:\n        return None\n" in b,
    ),
    # And without this one the keyframes are multiplied by a base that still has
    # their own component's resting value in it: the reference project's slides
    # would zoom from full frame instead of to it.
    (
        "the resting value the keyframes replace left inside the base",
        prproj_src,
        prproj_src.replace("return (source_w / frame_w) / fitted * resting / owner",
                           "return (source_w / frame_w) / fitted * resting"),
        lambda b: "return (source_w / frame_w) / fitted * resting / owner" in b,
    ),
):
    check(f"the guard against “{_name}” really can fail",
          _guard(_good) and not _guard(_broken), "")


shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + ("ALL GREEN" if not failures else f"{len(failures)} FAILED"))
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
