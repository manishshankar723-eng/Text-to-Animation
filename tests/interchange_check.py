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


def _real_clip(oid, name, path, end, start=None, media="Video"):
    """A clip the way Premiere really writes one: everything nested, and no
    `<Start>` element at all when the clip begins at zero."""
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
  <VideoClip ObjectID="{oid + 2}" ClassID="1c31d4c6" Version="1">
    <Source ObjectRef="{oid + 3}"/>
    <InPoint>0</InPoint>
    <OutPoint>{end}</OutPoint>
  </VideoClip>
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
      </TrackItems></ClipItems>
    </ClipTrack>
  </AudioClipTrack>
{_real_clip(700, "Opening", r"C:\Footage\open.png", end=_NTSC_TICKS * 48)}
{_real_clip(720, "Second", r"C:\Footage\two.mp4", end=_NTSC_TICKS * 120, start=_NTSC_TICKS * 48)}
{_real_clip(740, "Overlay", r"C:\Footage\logo.png", end=_NTSC_TICKS * 24)}
{_real_clip(760, "Voice", r"C:\Footage\vo.wav", end=_NTSC_TICKS * 120, media="Audio")}
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
      and sum(len(l["clips"]) for l in real["audio"]) == 1,
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

from server.schemas import AnimaticAudio, AnimaticFrame  # noqa: E402

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
apply_src = editor[apply_at: apply_at + 6000]
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
check("…and every new row claims its place in the saved stack order",
      apply_src.count("seatNewLane(layerTokenOf(row))") == 2, "")

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

# --- the .prproj offer, which must stay the SECOND answer ------------------
# ⚠ THE ORDER IS THE FEATURE. An experimental reader offered as a checkbox at
# the top is one most people tick without ever seeing the reliable door beside
# it, so the flag may only be reachable AFTER a refusal has been printed.
check("the picker will show a .prproj", ".prproj" in modal)
check("the flag is only offered once a read has failed",
      "error && isPrproj" in modal, "")
check("…and only once, not again after it has been taken",
      "!guessed" in modal, "")
check("…and it is offered off the EXTENSION, not the refusal's wording",
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
    (
        "the click event passed in as the experimental flag",
        modal,
        modal.replace("onClick={() => readFile()}", "onClick={readFile}"),
        lambda b: "onClick={readFile}" not in b and "onClick={() => readFile()}" in b,
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
):
    check(f"the guard against “{_name}” really can fail",
          _guard(_good) and not _guard(_broken), "")


shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + ("ALL GREEN" if not failures else f"{len(failures)} FAILED"))
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
