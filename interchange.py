"""interchange.py — THIS EDITOR'S CUT, WRITTEN SO ANOTHER EDITOR CAN OPEN IT.

The interchange work, all four phases: **out** in three formats, and **in**
from three — the last of which is a guess and says so.

OUT (phases 1 and 2), in **three formats**:

    fcp7          FCP7 XML (`xmeml` v4) — Premiere Pro, DaVinci Resolve, Avid,
                  Final Cut. Verified against Adobe's own docs: Premiere 2026
                  still carries `File > Import` and `File > Export > Final Cut
                  Pro XML`, and Adobe's stated route for OpenTimelineIO
                  interchange is this same format.
    aftereffects  An **ExtendScript** that BUILDS the comp. After Effects reads
                  no exchange format at all — see `write_ae_jsx` for why a
                  script is sturdier than forging its private project file.
    edl           CMX3600 — the floor. One video track, cuts only, frame-exact,
                  and every system since 1980 opens one.

⚠ **ONE MODEL, THREE WRITERS.** `build_sequence` reads the timeline ONCE and
decides everything with a right answer; a format is a way of SPELLING that,
plus `format_losses` — its own list of what it cannot hold. A fourth format is a
row in `FORMATS` and a writer, never a second reading of the project.

IN (phases 3 and 4), from **three**:

    fcp7          the same `xmeml` this writes — which is what Premiere,
                  Resolve, Avid and Final Cut all export.
    edl           CMX3600. It never states its frame rate, so the caller's is
                  used and warned about.
    prproj        ⚠ **THE ONE GUESS IN THIS FILE.** Premiere's private save
                  file, whose structure Adobe has never published. Opt-in
                  (`read_document(..., experimental=True)`), best-effort, and
                  every import it makes carries a warning saying so. Read the
                  section header above `_read_prproj` before touching it.

⚠ **ONE MODEL BOTH WAYS.** Each reader returns the same neutral incoming model
and `to_project` turns that into this app's own clips exactly once — so a fourth
reader is a `_read_*` and a row in `detect_format`, never a second opinion about
what a clip is.

⚠ **A PROJECT FILE IS A RECIPE, NOT THE FOOD.** The document holds "clip A, from
this file, from 2s to 5s, on track 1" and NOTHING ELSE — no pixels. So an export
that is only the document lands in Premiere as a timeline of offline clips. That
is why `bundle()` writes a ZIP with a `media/` folder beside it: the recipe and
the ingredients travel together or the user gets red rectangles. ⚠ **The media
folder is IDENTICAL for all three formats** — only the one document at the top of
the zip changes when the dropdown does.

⚠ **THE CUT TRANSFERS, THE LOOK DOES NOT.** Every clip, its place, its length,
its source window, the track it is on, the audio and its level, and a dissolve on
a cut — all of that has a box to go in. A WebGL colour grade, a LUT, a mask, a
blend mode, one of the app's fourteen transition shapes, a text clip, a shape
clip: none of those exist in `xmeml`, and inventing an approximation for them
would be worse than saying so. `build_sequence` therefore RETURNS what it had to
leave behind (`dropped`), the endpoint shows it BEFORE the download, and nobody
is surprised by a timeline that plays flat.

⚠ **MILLISECONDS IN, FRAMES OUT, AND THE CONVERSION HAPPENS EXACTLY ONCE.** This
editor thinks in ms (`start_ms`, `duration_ms`); every NLE thinks in frames. A
second rounding rule somewhere else is how a hundred clips end up half a frame
adrift and the voiceover leaves the lips. `ms_to_frames` is that one rule, it
rounds half UP away from zero (Python's `round` is banker's rounding and would
send 0.5 to 0), and `tests/interchange_check.py` pins it.

⚠ **PURE, AND IT TAKES PATHS RATHER THAN FINDING THEM.** Ownership is the
router's business — `server/animatics.py` resolves every clip to a file the way
`export_animatic` already does, because that is the request that knows who is
asking. This module is handed the answers, which is also what lets the test call
it with a directory of scratch files and no server at all. Same rule as
`frame_save.js` and `animatic_render.py`.

⚠ **PATHURL: RELATIVE UNLESS THE USER SAYS OTHERWISE.** An FCP7 XML links its
media by `<pathurl>`, and an ABSOLUTE `file://localhost/...` is the only thing
that re-links with no dialog — but at export time nobody knows where the user
will unzip. So the default is a relative `media/<name>`, which costs ONE "Locate"
in Premiere's Link Media dialog (every file is in one folder, so locating one
finds the rest), and `base_path` writes absolute urls for the user who does know
where the folder will live.

⚠ **PATHURL IS AN `fcp7` PROBLEM ONLY.** The After Effects script finds the media
folder sitting next to itself (`$.fileName`), and an EDL names REELS rather than
paths — so `base_path` is accepted and ignored by both. It is on their signatures
so that every writer in `FORMATS` takes the same two arguments.

⚠ **NOTHING HERE WRITES TO A PROJECT.** Every reader is pure: the route hands
the clips back and the EDITOR decides where they land, which is what makes an
import ONE entry on the undo stack instead of a write nobody can take back.

⚠ **AND THE `.prproj` READER IS STILL NOT A PROMISE.** It is unverified against a
real Premiere save file — only against fixtures written here — so it fails
loudly and partially rather than quietly and completely, and the refusal that
points at Final Cut Pro XML remains the DEFAULT answer.
"""

import base64
import json
import os
import posixpath
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
import zlib
import uuid
from urllib.parse import quote, unquote

import animatic_fonts
import animatic_render

# Where the media goes INSIDE the zip, and the first half of every relative
# pathurl. One name, because the XML and the copier must agree or every clip is
# offline.
MEDIA_DIR = "media"

# What a still's `<file>` claims to be long. A still has no length of its own, so
# the number only has to be BIGGER than any clip cut from it — an understated
# file duration is what makes an importer trim the clip back. An hour is far past
# `MAX_FRAME_MS` (10 minutes) and costs nothing.
STILL_FILE_SECONDS = 3600

# A file element is written out ONCE and referenced by id afterwards; this is the
# `<file id="...">` prefix. Both halves matter: repeating the full element for
# every clip is what turns a 40-shot board into a megabyte of XML, and some
# importers treat the second full definition as a second file.
FILE_ID = "file-"
CLIP_ID = "clipitem-"
TRANSITION_NAME = "Cross Dissolve"


# ---------------------------------------------------------------------------
# Time — the one conversion
# ---------------------------------------------------------------------------
def ms_to_frames(ms, fps: int) -> int:
    """Milliseconds → whole frames, rounding half AWAY FROM ZERO.

    ⚠ NOT `round()`. Python rounds half to EVEN, so at 24fps a clip landing on
    exactly half a frame would round down on one cut and up on the next — the
    drift is invisible for ten clips and is a lip-sync bug for a hundred.
    """
    try:
        value = float(ms or 0) * int(fps) / 1000.0
    except (TypeError, ValueError):
        return 0
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def frames_to_timecode(frames: int, fps: int) -> str:
    """HH:MM:SS:FF for the sequence's start timecode.

    Non-drop only, which is not a simplification: `AnimaticSettings.fps` is an
    INTEGER 1–60, so this project can never be 23.976 or 29.97 and there is no
    drop-frame case to get wrong. `<ntsc>` is FALSE everywhere for the same
    reason.
    """
    fps = max(1, int(fps))
    frames = max(0, int(frames))
    seconds, rest = divmod(frames, fps)
    minutes, secs = divmod(seconds, 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours % 24:02d}:{mins:02d}:{secs:02d}:{rest:02d}"


# ---------------------------------------------------------------------------
# Naming — what a file is called inside the zip
# ---------------------------------------------------------------------------
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str, fallback: str = "clip") -> str:
    """A filename that survives Windows, macOS, a zip and a URL alike."""
    cleaned = _SAFE.sub("_", (name or "").strip()).strip("._-")
    return cleaned or fallback


def _unique(taken: set, name: str) -> str:
    """`name`, or `name_2` / `name_3` … if that is already claimed.

    Board panels are the reason this exists: two different boards both hold a
    `panel_003.png`, and a zip with one of them in it is a project where half
    the shots are the wrong picture.
    """
    if name not in taken:
        taken.add(name)
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while f"{stem}_{n}{ext}" in taken:
        n += 1
    out = f"{stem}_{n}{ext}"
    taken.add(out)
    return out


# ---------------------------------------------------------------------------
# The neutral model — one pass over the project, no XML in sight
# ---------------------------------------------------------------------------
def _dropped(report: dict, what: str) -> None:
    report[what] = report.get(what, 0) + 1


def _identity_motion(clip: dict) -> bool:
    """True when a clip is placed plainly — nothing for `xmeml` to lose."""
    if clip.get("keyframes"):
        return False
    return (
        abs(float(clip.get("scale") or 1.0) - 1.0) < 1e-6
        and abs(float(clip.get("x") if clip.get("x") is not None else 0.5) - 0.5) < 1e-6
        and abs(float(clip.get("y") if clip.get("y") is not None else 0.5) - 0.5) < 1e-6
    )


def build_sequence(project: dict) -> dict:
    """The project as ONE neutral edit model — the thing a writer turns into XML.

    Everything with a right answer is decided here, once, so that a second
    format (an EDL, an After Effects script) is a second WRITER and not a second
    reading of the timeline.

    `project` is what `server/animatics.py` hands over, already resolved:

        title, fps, width, height, background, end_ms
        frames[]        the picture clips, each with `path` OR `video_path`
                        (exactly as `export_animatic` resolves them)
        overlays[]      the Images lane, each with `path`
        audio_tracks[]  each with `path`
        transitions[]   untouched
        texts[], shapes[]   COUNTED ONLY — there is no box for them in xmeml
        lane_order[], hidden_lanes[]

    Returns:

        name, fps, width, height, duration (frames)
        video[]   one entry per video track, LOWEST FIRST (V1 first), each
                  `{"clips": [...], "transitions": [...]}`
        audio[]   one entry per audio track, `{"clips": [...]}`
        files[]   every distinct source, `{"key", "name", "path", "kind",
                  "color", "duration"}` — `path` is None for a colour card,
                  which `bundle` draws instead of copying
        dropped   {what: how many} — what has no box in xmeml
        missing[] clips whose file has gone, named, and left out
    """
    fps = max(1, min(60, int(project.get("fps") or 24)))
    frames = list(project.get("frames") or [])
    hidden = set(project.get("hidden_lanes") or [])
    order = list(project.get("lane_order") or [])
    report: dict = {}
    missing: list = []

    # ⚠ SPANS COME FROM THE FULL LIST, INCLUDING HIDDEN AND UNRESOLVABLE CLIPS.
    # `frame_spans` places a clip with no `start_ms` after the last clip ON ITS
    # TRACK, so dropping a clip before this runs would slide every later clip on
    # that row up by its length — the export would be a different film. Filter
    # after, never before. `transition_window` indexes the same list.
    spans, total_ms = animatic_render.frame_spans(
        frames, int(project.get("end_ms") or 0) or None
    )

    files: dict = {}
    taken: set = set()

    def file_for(key: str, path, kind: str, name_hint: str, duration_ms: int, color=""):
        """Register a source once; hand back its id. None if it has no bytes."""
        if key in files:
            entry = files[key]
            entry["duration"] = max(entry["duration"], ms_to_frames(duration_ms, fps))
            return entry["key"]
        if kind != "color" and not path:
            return None
        base = safe_name(name_hint or os.path.basename(path or ""), "clip")
        files[key] = {
            "key": key,
            "id": f"{FILE_ID}{len(files) + 1}",
            "name": _unique(taken, base),
            "path": path,
            "kind": kind,
            "color": color,
            "duration": ms_to_frames(duration_ms, fps),
        }
        return key

    # --- which lane becomes which video track ------------------------------
    # ⚠ THE SAVED STACK ORDER DECIDES, NOT THE TRACK NUMBER. A row dragged above
    # another keeps its number and changes its RANK (`lane_rank`), and V1…Vn in
    # an NLE is a stack from the bottom up — so ranking here is what makes the
    # imported timeline look like the monitor did. With no saved order
    # `lane_rank` reproduces the old sequence exactly: pictures, then overlays.
    lanes: list = []
    for track in animatic_render.picture_tracks(frames):
        token = f"frames:{track}"
        if token in hidden:
            _dropped(report, "hidden rows (left out)")
            continue
        lanes.append((animatic_render.lane_rank(token, order), token))
    seen_overlay = []
    for overlay in project.get("overlays") or []:
        token = animatic_render.clip_lane_token("overlay", overlay)
        if token in hidden or token in seen_overlay:
            continue
        seen_overlay.append(token)
        lanes.append((animatic_render.lane_rank(token, order), token))
    lanes.sort(key=lambda pair: pair[0])
    lane_index = {token: i for i, (_, token) in enumerate(lanes)}
    video = [{"clips": [], "transitions": []} for _ in lanes]

    # --- the picture clips --------------------------------------------------
    for i, clip in enumerate(frames):
        track = animatic_render.frame_track(clip)
        token = f"frames:{track}"
        if token not in lane_index:
            continue
        span = spans[i]
        length_ms = span["end"] - span["start"]
        kind = animatic_render.clip_kind(clip)
        label = clip.get("label") or f"Shot {i + 1}"

        if kind == "color":
            color = (clip.get("color") or "#000000").lower()
            key = file_for(
                f"color:{color}", None, "color", f"colour_{color.lstrip('#')}.png",
                STILL_FILE_SECONDS * 1000, color=color,
            )
            source_in = 0
        elif kind == "video":
            path = clip.get("video_path")
            if not path or not os.path.isfile(path):
                missing.append(label)
                continue
            key = file_for(
                f"video:{path}", path, "video", os.path.basename(path),
                (clip.get("in_ms") or 0) + length_ms,
            )
            source_in = ms_to_frames(clip.get("in_ms") or 0, fps)
        else:
            path = clip.get("path")
            if not path or not os.path.isfile(path):
                missing.append(label)
                continue
            key = file_for(
                f"image:{path}", path, "image", os.path.basename(path),
                STILL_FILE_SECONDS * 1000,
            )
            source_in = 0
        if key is None:
            missing.append(label)
            continue

        # ⚠ THE SOURCE WINDOW IS FORCED TO THE TIMELINE LENGTH, and `speed` is
        # reported instead of exported. In xmeml a clip is played at speed when
        # `out - in` differs from `end - start`, and an importer that does not
        # read that difference as a speed change plays the wrong footage at the
        # wrong length. A clip that is the right length with the right first
        # frame is a cut somebody can finish; a clip of the wrong footage is not.
        span_frames = ms_to_frames(span["end"], fps) - ms_to_frames(span["start"], fps)
        if abs(float(clip.get("speed") or 1.0) - 1.0) > 1e-6:
            _dropped(report, "speed changes")
        if clip.get("effects"):
            _dropped(report, "effects and colour grades")
        if (clip.get("mask") or {}).get("kind", "none") not in ("none", ""):
            _dropped(report, "masks")
        if (clip.get("blend") or "normal") != "normal":
            _dropped(report, "blend modes")
        if not _identity_motion(clip):
            _dropped(report, "pan / zoom moves")

        video[lane_index[token]]["clips"].append(
            {
                "name": label,
                "file": key,
                "start": ms_to_frames(span["start"], fps),
                "end": ms_to_frames(span["end"], fps),
                "in": source_in,
                "out": source_in + span_frames,
                "opacity": float(clip.get("opacity") if clip.get("opacity") is not None else 1.0),
            }
        )

    # --- the Images lane ----------------------------------------------------
    for n, overlay in enumerate(project.get("overlays") or []):
        token = animatic_render.clip_lane_token("overlay", overlay)
        if token not in lane_index:
            continue
        path = overlay.get("path")
        name = f"Image {n + 1}"
        if not path or not os.path.isfile(path):
            missing.append(name)
            continue
        key = file_for(
            f"image:{path}", path, "image", os.path.basename(path),
            STILL_FILE_SECONDS * 1000,
        )
        start = int(overlay.get("start_ms") or 0)
        length = int(overlay.get("duration_ms") or 2000)
        # An overlay is a picture placed in a BOX — position, size, rotation.
        # xmeml carries none of that on a plain clipitem, so it arrives
        # full-frame and is said so out loud.
        _dropped(report, "overlay position and size")
        video[lane_index[token]]["clips"].append(
            {
                "name": name,
                "file": key,
                "start": ms_to_frames(start, fps),
                "end": ms_to_frames(start + length, fps),
                "in": 0,
                "out": ms_to_frames(start + length, fps) - ms_to_frames(start, fps),
                "opacity": float(overlay.get("opacity") if overlay.get("opacity") is not None else 1.0),
            }
        )

    # --- transitions --------------------------------------------------------
    # ⚠ EVERY KIND ARRIVES AS A CROSS DISSOLVE, and that is a decision rather
    # than a gap. This app has fourteen (dips, wipes, slides, nine reveal
    # mattes); xmeml can name a transition but the receiving app supplies the
    # effect, so anything but the one transition every NLE has is a coin toss
    # between "wrong shape" and "the whole import is refused". A dissolve on the
    # right cut, of the right length, is the honest half.
    for transition in project.get("transitions") or []:
        window = animatic_render.transition_window(frames, spans, transition)
        if window is None:
            continue
        token = f"frames:{window['track']}"
        if token not in lane_index:
            continue
        if window["kind"] != "dissolve":
            _dropped(report, "transition shapes (exported as a dissolve)")
        video[lane_index[token]]["transitions"].append(
            {
                "start": ms_to_frames(window["start_ms"], fps),
                "end": ms_to_frames(window["end_ms"], fps),
                "cut": ms_to_frames(window["cut_ms"], fps),
            }
        )

    # --- audio --------------------------------------------------------------
    # One lane per `layer_id`, in the order the lanes first appear — the same
    # thing the timeline draws, so A1 in Premiere is the top audio row here.
    audio_lanes: list = []
    audio: list = []
    for n, track in enumerate(project.get("audio_tracks") or []):
        path = track.get("path")
        name = track.get("filename") or f"Audio {n + 1}"
        if not path or not os.path.isfile(path):
            missing.append(name)
            continue
        lane = track.get("layer_id") or f"_solo_{track.get('id') or n}"
        if lane not in audio_lanes:
            audio_lanes.append(lane)
            audio.append({"clips": []})
        duration_ms = int(track.get("duration_ms") or 0)
        offset = int(track.get("offset_ms") or 0)
        playable = max(0, duration_ms - offset) if duration_ms else 0
        trim = track.get("trim_ms")
        if trim:
            playable = min(playable, int(trim)) if playable else int(trim)
        if not playable:
            playable = int(trim or duration_ms or 0)
        if playable <= 0:
            missing.append(name)
            continue
        key = file_for(
            f"audio:{path}", path, "audio", os.path.basename(path),
            duration_ms or (offset + playable),
        )
        start = int(track.get("start_ms") or 0)
        if track.get("fade_in_ms") or track.get("fade_out_ms"):
            _dropped(report, "audio fades")
        if track.get("eq_low") or track.get("eq_mid") or track.get("eq_high"):
            _dropped(report, "audio EQ")
        if float(track.get("duck_to") or 1.0) < 1.0:
            _dropped(report, "ducking")
        audio[audio_lanes.index(lane)]["clips"].append(
            {
                "name": safe_name(name, f"audio_{n + 1}"),
                "file": key,
                "start": ms_to_frames(start, fps),
                "end": ms_to_frames(start + playable, fps),
                "in": ms_to_frames(offset, fps),
                "out": ms_to_frames(offset + playable, fps),
                "muted": bool(track.get("muted")),
                "level": float(track.get("volume") if track.get("volume") is not None else 1.0),
            }
        )

    # --- what else has no box ----------------------------------------------
    for text in project.get("texts") or []:
        if (text.get("text") or "").strip():
            _dropped(report, "text clips")
    for _ in project.get("shapes") or []:
        _dropped(report, "shape clips")
    if project.get("show_labels"):
        _dropped(report, "burnt-in shot labels")

    # ⚠ AN EMPTY ROW IS DROPPED, AND ONLY AT THE VERY END. `picture_tracks`
    # always includes track 0 even when nothing sits on it, and a project whose
    # pictures all live on track 1 would otherwise arrive with an empty V1 under
    # everything. Filtered here rather than earlier because `lane_index` is what
    # every clip and every transition above was placed by — renumbering the lanes
    # mid-pass is how a dissolve ends up on the wrong row.
    video = [lane for lane in video if lane["clips"] or lane["transitions"]]

    duration = max(
        [c["end"] for lane in video for c in lane["clips"]]
        + [c["end"] for lane in audio for c in lane["clips"]]
        + [ms_to_frames(total_ms, fps)]
        + [0]
    )

    return {
        "name": (project.get("title") or "Project").strip() or "Project",
        "fps": fps,
        "width": int(project.get("width") or 1920),
        "height": int(project.get("height") or 1080),
        "duration": duration,
        "video": video,
        "audio": audio,
        "files": list(files.values()),
        "dropped": report,
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# The FCP7 XML writer
# ---------------------------------------------------------------------------
def _rate(parent, fps: int):
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    # Always FALSE: `AnimaticSettings.fps` is an integer, so this project is
    # never 23.976 or 29.97 and there is no NTSC pulldown to declare.
    ET.SubElement(rate, "ntsc").text = "FALSE"
    return rate


def _timecode(parent, fps: int):
    tc = ET.SubElement(parent, "timecode")
    _rate(tc, fps)
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"
    return tc


def pathurl(name: str, base_path: str = "") -> str:
    """The `<pathurl>` for one media file.

    ⚠ RELATIVE BY DEFAULT AND THAT COSTS ONE DIALOG. Premiere re-links with no
    questions only from an absolute `file://localhost/…`, and at export time
    nobody knows where the zip will be unpacked — so the default is
    `media/<name>`, which lands as "Media Offline" once, and locating ANY ONE
    file in Premiere's Link Media dialog finds the rest, because they are all in
    the one folder. `base_path` is for the user who does know: give it the folder
    the zip will be unpacked into and the import is silent.
    """
    rel = posixpath.join(MEDIA_DIR, name)
    if not base_path:
        return quote(rel)
    root = str(base_path).replace("\\", "/").rstrip("/")
    if not root.startswith("/"):
        root = "/" + root  # C:/films → /C:/films, which is what file:// wants
    return "file://localhost" + quote(f"{root}/{rel}")


def _file_element(parent, entry: dict, fps: int, size: tuple, base_path: str, written: set):
    """`<file>` — in full the first time, a bare reference every time after.

    ⚠ THE SECOND FULL DEFINITION IS NOT HARMLESS. A file repeated in full is a
    second file to some importers, so a board panel used in four shots arrives as
    four separate items in the project bin.

    `size` is the SEQUENCE's frame size, not the file's — this app has no
    `ffprobe` (see `video_assemble.py`), so the true pixel size of an uploaded
    clip is not knowable here. It is a hint an importer overrides the moment it
    opens the real file, and stating the sequence size is closer than stating
    nothing.
    """
    el = ET.SubElement(parent, "file", {"id": entry["id"]})
    if entry["key"] in written:
        return el
    written.add(entry["key"])
    ET.SubElement(el, "name").text = entry["name"]
    ET.SubElement(el, "pathurl").text = pathurl(entry["name"], base_path)
    _rate(el, fps)
    ET.SubElement(el, "duration").text = str(max(1, int(entry["duration"])))
    _timecode(el, fps)
    media = ET.SubElement(el, "media")
    if entry["kind"] == "audio":
        sound = ET.SubElement(media, "audio")
        chars = ET.SubElement(sound, "samplecharacteristics")
        ET.SubElement(chars, "depth").text = "16"
        ET.SubElement(chars, "samplerate").text = "48000"
        ET.SubElement(sound, "channelcount").text = "2"
    else:
        vid = ET.SubElement(media, "video")
        ET.SubElement(vid, "duration").text = str(max(1, int(entry["duration"])))
        chars = ET.SubElement(vid, "samplecharacteristics")
        _rate(chars, fps)
        ET.SubElement(chars, "width").text = str(int(size[0]))
        ET.SubElement(chars, "height").text = str(int(size[1]))
        ET.SubElement(chars, "anamorphic").text = "FALSE"
        ET.SubElement(chars, "pixelaspectratio").text = "square"
        ET.SubElement(chars, "fielddominance").text = "none"
    return el


def _video_clipitem(track_el, clip: dict, entry: dict, fps: int, size: tuple, base_path: str, written: set, n: int):
    item = ET.SubElement(track_el, "clipitem", {"id": f"{CLIP_ID}{n}"})
    ET.SubElement(item, "name").text = clip["name"]
    ET.SubElement(item, "enabled").text = "TRUE"
    ET.SubElement(item, "duration").text = str(max(1, int(entry["duration"])))
    _rate(item, fps)
    ET.SubElement(item, "start").text = str(clip["start"])
    ET.SubElement(item, "end").text = str(clip["end"])
    ET.SubElement(item, "in").text = str(clip["in"])
    ET.SubElement(item, "out").text = str(clip["out"])
    _file_element(item, entry, fps, size, base_path, written)
    ET.SubElement(item, "compositemode").text = "normal"
    return item


def _audio_clipitem(track_el, clip: dict, entry: dict, fps: int, size: tuple, base_path: str, written: set, n: int):
    item = ET.SubElement(track_el, "clipitem", {"id": f"{CLIP_ID}{n}"})
    ET.SubElement(item, "name").text = clip["name"]
    ET.SubElement(item, "enabled").text = "FALSE" if clip["muted"] else "TRUE"
    ET.SubElement(item, "duration").text = str(max(1, int(entry["duration"])))
    _rate(item, fps)
    ET.SubElement(item, "start").text = str(clip["start"])
    ET.SubElement(item, "end").text = str(clip["end"])
    ET.SubElement(item, "in").text = str(clip["in"])
    ET.SubElement(item, "out").text = str(clip["out"])
    _file_element(item, entry, fps, size, base_path, written)
    source = ET.SubElement(item, "sourcetrack")
    ET.SubElement(source, "mediatype").text = "audio"
    ET.SubElement(source, "trackindex").text = "1"
    # The level, as Premiere's own audiolevels filter. This is the one piece of
    # the mix that has a documented box, so it is the one piece that travels.
    flt = ET.SubElement(item, "filter")
    effect = ET.SubElement(flt, "effect")
    ET.SubElement(effect, "name").text = "Audio Levels"
    ET.SubElement(effect, "effectid").text = "audiolevels"
    ET.SubElement(effect, "effectcategory").text = "audiolevels"
    ET.SubElement(effect, "effecttype").text = "audiolevels"
    ET.SubElement(effect, "mediatype").text = "audio"
    ET.SubElement(effect, "pproBypass").text = "false"
    param = ET.SubElement(effect, "parameter", {"authoringApp": "PremierePro"})
    ET.SubElement(param, "parameterid").text = "level"
    ET.SubElement(param, "name").text = "Level"
    ET.SubElement(param, "valuemin").text = "0"
    ET.SubElement(param, "valuemax").text = "3.98108"
    ET.SubElement(param, "value").text = f"{max(0.0, min(2.0, clip['level'])):.6f}"
    return item


def _transitionitem(track_el, window: dict, fps: int):
    item = ET.SubElement(track_el, "transitionitem")
    ET.SubElement(item, "start").text = str(window["start"])
    ET.SubElement(item, "end").text = str(window["end"])
    ET.SubElement(item, "alignment").text = "center"
    _rate(item, fps)
    # Ticks are FCP's 254016000000-per-second clock. Premiere reads it, and an
    # absent one has been seen to centre the transition on the wrong edit.
    ET.SubElement(item, "cutPointTicks").text = str(
        int(round(window["cut"] * 254016000000 / max(1, fps)))
    )
    effect = ET.SubElement(item, "effect")
    ET.SubElement(effect, "name").text = TRANSITION_NAME
    ET.SubElement(effect, "effectid").text = TRANSITION_NAME
    ET.SubElement(effect, "effectcategory").text = "Dissolve"
    ET.SubElement(effect, "effecttype").text = "transition"
    ET.SubElement(effect, "mediatype").text = "video"
    ET.SubElement(effect, "wipecode").text = "0"
    ET.SubElement(effect, "wipeaccuracy").text = "100"
    ET.SubElement(effect, "startratio").text = "0"
    ET.SubElement(effect, "endratio").text = "1"
    ET.SubElement(effect, "reverse").text = "FALSE"
    return item


def write_fcp7_xml(model: dict, base_path: str = "") -> str:
    """The model as an `xmeml` version 4 document — the bytes Premiere imports."""
    fps = model["fps"]
    size = (model["width"], model["height"])
    by_key = {entry["key"]: entry for entry in model["files"]}
    written: set = set()
    counter = [0]

    root = ET.Element("xmeml", {"version": "4"})
    seq = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    ET.SubElement(seq, "name").text = model["name"]
    ET.SubElement(seq, "duration").text = str(max(1, int(model["duration"])))
    _rate(seq, fps)
    ET.SubElement(seq, "in").text = "-1"
    ET.SubElement(seq, "out").text = "-1"
    _timecode(seq, fps)
    media = ET.SubElement(seq, "media")

    vid = ET.SubElement(media, "video")
    fmt = ET.SubElement(vid, "format")
    chars = ET.SubElement(fmt, "samplecharacteristics")
    _rate(chars, fps)
    ET.SubElement(chars, "width").text = str(model["width"])
    ET.SubElement(chars, "height").text = str(model["height"])
    ET.SubElement(chars, "anamorphic").text = "FALSE"
    ET.SubElement(chars, "pixelaspectratio").text = "square"
    ET.SubElement(chars, "fielddominance").text = "none"
    for lane in model["video"]:
        track_el = ET.SubElement(vid, "track")
        # ⚠ CLIPS IN TIME ORDER, THEN THE TRANSITIONS THAT SIT BETWEEN THEM.
        # A track's children are read as a sequence, and a clipitem out of order
        # is where an importer starts refusing the whole document.
        items = [("clip", c["start"], c) for c in lane["clips"]]
        items += [("transition", t["start"], t) for t in lane["transitions"]]
        items.sort(key=lambda row: (row[1], row[0] == "transition"))
        for what, _, payload in items:
            if what == "clip":
                counter[0] += 1
                _video_clipitem(
                    track_el, payload, by_key[payload["file"]], fps, size, base_path,
                    written, counter[0],
                )
            else:
                _transitionitem(track_el, payload, fps)
        ET.SubElement(track_el, "enabled").text = "TRUE"
        ET.SubElement(track_el, "locked").text = "FALSE"

    sound = ET.SubElement(media, "audio")
    ET.SubElement(sound, "numOutputChannels").text = "2"
    fmt = ET.SubElement(sound, "format")
    chars = ET.SubElement(fmt, "samplecharacteristics")
    ET.SubElement(chars, "depth").text = "16"
    ET.SubElement(chars, "samplerate").text = "48000"
    for lane in model["audio"]:
        track_el = ET.SubElement(sound, "track")
        for clip in sorted(lane["clips"], key=lambda c: c["start"]):
            counter[0] += 1
            _audio_clipitem(
                track_el, clip, by_key[clip["file"]], fps, size, base_path,
                written, counter[0],
            )
        ET.SubElement(track_el, "enabled").text = "TRUE"
        ET.SubElement(track_el, "locked").text = "FALSE"

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n{body}\n'


# ---------------------------------------------------------------------------
# The EDL writer — the oldest, smallest, most certain format there is
# ---------------------------------------------------------------------------
# An EDL (CMX3600) is a plain-text list of edits, and every editing system built
# since 1980 reads one. It is here as the FLOOR: when an XML import goes wrong in
# some version of some app, this still opens.
#
# ⚠ AN EDL HOLDS ONE VIDEO TRACK. Not "one is easier" — the format has one, so
# every clip above the base picture row (and the whole Images lane) is left out
# and SAID so. That is the trade the format is: exact, small, and less.
#
# ⚠ AND IT CARRIES CUTS ONLY, ON PURPOSE. A CMX dissolve is written as a pair of
# events whose record times START at the edit point, while this app's dissolves
# are BOUNDARY-LOCAL — half from each side of the cut (see `AnimaticTransition`).
# Representing one as the other moves the cut by half the transition and changes
# the length of both neighbours. An EDL is a CONFORM reference: every frame
# number in it has to be exactly right, so the dissolve is reported as a loss
# rather than approximated into a wrong edit point.
EDL_REEL_LEN = 8


def _reel_names(model: dict) -> dict:
    """One 8-character reel name per source file — the EDL's only id space.

    ⚠ EIGHT CHARACTERS, UPPERCASE, NO PUNCTUATION, and unique. A CMX3600 reel
    field is a fixed width and older systems truncate rather than complain, so two
    files that differ only after character eight would conform as ONE tape. The
    real filename goes in the `* FROM CLIP NAME:` comment underneath, which is
    where every modern tool looks anyway.
    """
    out: dict = {}
    taken: set = set()
    for entry in model["files"]:
        stem = os.path.splitext(entry["name"])[0].upper()
        base = "".join(ch for ch in stem if ch.isalnum())[:EDL_REEL_LEN] or "AX"
        name = base
        n = 2
        while name in taken:
            tail = str(n)
            name = base[: EDL_REEL_LEN - len(tail)] + tail
            n += 1
        taken.add(name)
        out[entry["key"]] = name
    return out


def write_edl(model: dict, base_path: str = "") -> str:
    """The model as a CMX3600 EDL.

    `base_path` is accepted and ignored: an EDL names REELS, never paths, so
    there is nothing for it to change. The parameter is here so every writer in
    `FORMATS` has one signature.
    """
    fps = model["fps"]
    reels = _reel_names(model)
    by_key = {entry["key"]: entry for entry in model["files"]}
    tc = lambda f: frames_to_timecode(f, fps)  # noqa: E731

    # ⚠ ONE VIDEO LANE — the FIRST, which `build_sequence` has already sorted to
    # be the bottom of the stack. Everything above it is counted in
    # `format_losses` and left out here.
    events = []
    for clip in (model["video"][0]["clips"] if model["video"] else []):
        events.append(("V", clip))
    # Two audio channels, which is what CMX3600 addresses. A third lane is a
    # reported loss, not a silently mixed-down one.
    for i, lane in enumerate(model["audio"][:2]):
        for clip in lane["clips"]:
            events.append(("A" if i == 0 else "A2", clip))
    # ⚠ RECORD ORDER, AND VIDEO FIRST WHERE TWO EVENTS SHARE A FRAME. Sorting the
    # channel as a STRING put "A" before "V", so a film whose voiceover starts at
    # 00:00:00:00 opened with its audio event — legal, and wrong to every human
    # reading the list, because event 001 of an EDL is understood to be the first
    # picture. Ranked explicitly rather than sorted alphabetically.
    order = {"V": 0, "A": 1, "A2": 2}
    events.sort(key=lambda row: (row[1]["start"], order.get(row[0], 9)))

    lines = [f"TITLE: {model['name'][:70]}", "FCM: NON-DROP FRAME", ""]
    for n, (channel, clip) in enumerate(events, start=1):
        entry = by_key[clip["file"]]
        reel = reels[clip["file"]]
        lines.append(
            f"{n:03d}  {reel:<8} {channel:<5}{'C':<4}{'':<4}"
            f"{tc(clip['in'])} {tc(clip['out'])} {tc(clip['start'])} {tc(clip['end'])}"
        )
        lines.append(f"* FROM CLIP NAME: {entry['name']}")
        if clip.get("name") and clip["name"] != entry["name"]:
            lines.append(f"* COMMENT: {clip['name']}")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The After Effects writer — a SCRIPT, not a project file
# ---------------------------------------------------------------------------
# ⚠ AFTER EFFECTS DOES NOT READ FCP7 XML, EDL OR AAF. It reads `.aep` (binary,
# undocumented) and `.aepx` (its own XML, and extremely brittle to write by
# hand). So this writes neither: it writes an **ExtendScript** that TELLS After
# Effects to build the comp — `File > Scripts > Run Script File…`.
#
# That is not a workaround, it is the sturdier answer. Forging a project file
# means guessing at a private format; a script uses AE's own public API, so what
# arrives is exactly what AE itself would have made, and it keeps working across
# versions.
#
# ⚠ THE SCRIPT FINDS ITS OWN MEDIA. `$.fileName` is the running script's path, so
# it looks for the `media` folder sitting beside itself — the zip is
# self-locating and there is no "where did you unzip it" question to answer. If
# somebody moves the script away from its media it asks, once, with a folder
# picker.
#
# ⚠ NO TRANSITIONS. After Effects has no transition object at all — a dissolve
# there is opacity keyframes on a layer that has been extended backwards over its
# neighbour, which needs handles this app's boundary-local model does not carry.
# Reported as a loss, exactly like the EDL's.
# ⚠ THE HEADER CARRIES NO PROJECT TITLE, AND THAT IS DELIBERATE.
# Everything this script emits has to be pure ASCII (see `write_ae_jsx`), and a
# film titled in Devanagari or with a stray em dash would put non-ASCII straight
# into this comment block. The title reaches After Effects through the DATA
# block instead, where `json.dumps` escapes it and it arrives intact as the
# comp's name.
AE_HEADER = """/*  Built by AI Studio - your timeline, as an After Effects comp.

    HOW TO RUN THIS
      1. Keep this file and the "{media}" folder next to each other.
      2. In After Effects:  File > Scripts > Run Script File...
      3. Choose this file. A composition is created, with every clip on its
         own layer, in the right place.

    WHAT CAME ACROSS
      Every clip, where it sits, how long it holds, which part of each video
      is used, the layer stack, opacity, and the audio with its levels.

    WHAT DID NOT
      Colour grades, LUTs, masks, blend modes, pan/zoom moves, transitions,
      on-screen text and shapes. Those are this app's own look; After Effects
      is where you would rebuild them anyway.
*/
"""

# The engine. Deliberately ES3 — ExtendScript is not modern JavaScript: no
# `const`, no `let`, no arrow functions, no `JSON`. And ⚠ NO KEY MAY BE A
# RESERVED WORD, which is why the data below says `srcIn` and not `in`: `{in: 0}`
# is a syntax error in ExtendScript and the script would not even load.
AE_BODY = """
(function () {
  var missing = [];
  var made = 0;

  var here = null;
  try { here = new File($.fileName).parent; } catch (e) { here = null; }
  var media = here ? new Folder(here.fsName + "/" + DATA.mediaDir) : null;
  if (media === null || !media.exists) {
    media = Folder.selectDialog(
      "Where is the \\"" + DATA.mediaDir + "\\" folder that came with this script?"
    );
  }
  if (media === null) {
    alert("No media folder was chosen, so nothing was imported.");
    return;
  }

  app.beginUndoGroup("Import " + DATA.name);
  try {
    var proj = app.project ? app.project : app.newProject();
    var bin = proj.items.addFolder(DATA.name);
    var items = {};

    for (var i = 0; i < DATA.files.length; i++) {
      var spec = DATA.files[i];
      var file = new File(media.fsName + "/" + spec.name);
      if (!file.exists) { missing.push(spec.name); continue; }
      try {
        var opts = new ImportOptions(file);
        opts.importAs = ImportAsType.FOOTAGE;
        var item = proj.importFile(opts);
        item.parentFolder = bin;
        items[spec.key] = item;
      } catch (err) {
        missing.push(spec.name);
      }
    }

    var comp = proj.items.addComp(
      DATA.name, DATA.width, DATA.height, 1, DATA.duration, DATA.fps
    );
    comp.parentFolder = bin;

    /* !! BOTTOM LAYER FIRST. comp.layers.add() always inserts at index 1, i.e.
       the TOP, so the last one added ends up over everything. The list is
       written lowest-lane-first for exactly this loop. */
    for (var n = 0; n < DATA.layers.length; n++) {
      var row = DATA.layers[n];
      var source = items[row.file];
      if (!source) { continue; }
      var layer = comp.layers.add(source);
      layer.name = row.name;
      /* startTime is where the SOURCE's first frame would fall; the in/out
         points are the window of it that plays. Setting all three is what
         puts a trimmed video clip on the right frame. */
      layer.startTime = row.tlStart - row.srcIn;
      layer.inPoint = row.tlStart;
      layer.outPoint = row.tlEnd;
      if (row.audio) {
        if (row.muted) { layer.audioEnabled = false; }
        try {
          var db = row.level > 0 ? (20 * Math.log(row.level) / Math.LN10) : -96;
          layer.property("Audio").property("Audio Levels").setValue([db, db]);
        } catch (errAudio) { /* a still has no Audio group */ }
      } else if (row.opacity < 1) {
        try {
          layer.property("Opacity").setValue(row.opacity * 100);
        } catch (errOpacity) { /* nothing to do */ }
      }
      made = made + 1;
    }

    comp.openInViewer();
  } finally {
    app.endUndoGroup();
  }

  var says = "Built \\"" + DATA.name + "\\" with " + made + " layers.";
  if (missing.length > 0) {
    says = says + "\\n\\n" + missing.length + " file(s) could not be found:\\n"
         + missing.join("\\n");
  }
  alert(says);
})();
"""


def write_ae_jsx(model: dict, base_path: str = "") -> str:
    """The model as an After Effects script.

    `base_path` is accepted and ignored — the script locates its own media next
    to itself, which is strictly better than any path we could write in.
    """
    fps = max(1, int(model["fps"]))
    seconds = lambda f: round(float(f) / fps, 6)  # noqa: E731

    files = [{"key": e["key"], "name": e["name"]} for e in model["files"]]
    layers = []
    # ⚠ LOWEST LANE FIRST — see the loop in AE_BODY. `model["video"]` is already
    # in rank order (bottom of the stack first), and the audio goes last, which
    # is where an editor expects to find it in an AE timeline.
    for lane in model["video"]:
        for clip in sorted(lane["clips"], key=lambda c: c["start"]):
            layers.append({
                "file": clip["file"],
                "name": clip["name"],
                "tlStart": seconds(clip["start"]),
                "tlEnd": seconds(clip["end"]),
                "srcIn": seconds(clip["in"]),
                "opacity": round(float(clip.get("opacity", 1.0)), 4),
                "audio": False,
                "muted": False,
                "level": 1.0,
            })
    for lane in model["audio"]:
        for clip in sorted(lane["clips"], key=lambda c: c["start"]):
            layers.append({
                "file": clip["file"],
                "name": clip["name"],
                "tlStart": seconds(clip["start"]),
                "tlEnd": seconds(clip["end"]),
                "srcIn": seconds(clip["in"]),
                "opacity": 1.0,
                "audio": True,
                "muted": bool(clip.get("muted")),
                "level": round(float(clip.get("level", 1.0)), 4),
            })

    data = {
        "name": model["name"],
        "mediaDir": MEDIA_DIR,
        "width": model["width"],
        "height": model["height"],
        "fps": fps,
        # A comp of length zero cannot be created, so an empty project still
        # gets one frame to exist in.
        "duration": max(seconds(model["duration"]), 1.0 / fps),
        "files": files,
        "layers": layers,
    }
    # ⚠ `ensure_ascii=True` ON PURPOSE. A shot called "Shiv ji ka ghar" is fine,
    # but ExtendScript's idea of a file's encoding depends on a BOM and on the
    # host's locale; escaping every non-ASCII character to \\uXXXX means the
    # script is pure ASCII and cannot be mis-decoded. JSON is a subset of
    # JavaScript object syntax, so the dump IS a valid literal.
    body = json.dumps(data, ensure_ascii=True, indent=2)
    return (
        AE_HEADER.format(media=MEDIA_DIR)
        + "\nvar DATA = "
        + body
        + ";\n"
        + AE_BODY
    )


# ---------------------------------------------------------------------------
# The formats, and what each one costs
# ---------------------------------------------------------------------------
# ⚠ ONE MODEL, THREE WRITERS. `build_sequence` decided everything with a right
# answer; a format is a way of SPELLING that, plus its own list of what it cannot
# hold. Adding a fourth means adding a row here and a writer above it — never a
# second reading of the timeline.
FORMATS = {
    "fcp7": {
        "label": "Premiere Pro, Resolve, Avid, Final Cut",
        "ext": "xml",
        "mime": "application/xml",
        "write": None,  # filled below — the writers are defined further up
    },
    "aftereffects": {
        "label": "After Effects (a script that builds the comp)",
        "ext": "jsx",
        "mime": "application/javascript",
        "write": None,
    },
    "edl": {
        "label": "EDL — cuts only, opens anywhere",
        "ext": "edl",
        "mime": "text/plain",
        "write": None,
    },
}
FORMATS["fcp7"]["write"] = write_fcp7_xml
FORMATS["aftereffects"]["write"] = write_ae_jsx
FORMATS["edl"]["write"] = write_edl

DEFAULT_FORMAT = "fcp7"


def normalise_format(name: str) -> str:
    """An unknown format folds down to the default rather than failing.

    Same rule as a transition `kind` or a clip `kind` everywhere else in this
    app: a value a newer client invented still exports something instead of
    answering 422.
    """
    key = (name or "").strip().lower()
    return key if key in FORMATS else DEFAULT_FORMAT


def format_ext(fmt: str) -> str:
    return FORMATS[normalise_format(fmt)]["ext"]


def write_document(model: dict, fmt: str = DEFAULT_FORMAT, base_path: str = "") -> str:
    """The model, spelled in one format."""
    return FORMATS[normalise_format(fmt)]["write"](model, base_path=base_path)


def format_losses(model: dict, fmt: str) -> dict:
    """What THIS FORMAT cannot hold, on top of what no format can.

    ⚠ SEPARATE FROM `model["dropped"]` AND MERGED ONLY IN THE REPORT. The base
    list is about this app's look (grades, masks, text) and is true whichever
    format is chosen; this one is about the format's own ceiling, so switching the
    dropdown must change it — that is the whole reason the preview takes a format.
    """
    fmt = normalise_format(fmt)
    extra: dict = {}

    def add(what, count):
        if count > 0:
            extra[what] = count

    placed = sum(len(lane["transitions"]) for lane in model["video"])
    if fmt == "edl":
        # ⚠ AN EDL HAS ONE VIDEO TRACK AND TWO AUDIO CHANNELS. Everything above
        # the base picture row is named here rather than quietly flattened.
        add("clips on upper video rows (an EDL holds one)",
            sum(len(lane["clips"]) for lane in model["video"][1:]))
        add("audio rows past the second", sum(len(lane["clips"]) for lane in model["audio"][2:]))
        add("dissolves (an EDL here is cuts only)", placed)
        add("clip opacity", sum(
            1 for lane in model["video"] for c in lane["clips"] if c.get("opacity", 1.0) < 1.0
        ))
    elif fmt == "aftereffects":
        # AE has no transition object — a dissolve there is keyframes on a layer
        # extended over its neighbour, which needs handles this app has not got.
        add("dissolves (After Effects has no transition object)", placed)
    return extra


# ---------------------------------------------------------------------------
# The bundle — recipe and ingredients in one zip
# ---------------------------------------------------------------------------
def solid_png(color: str, width: int, height: int, path: str) -> str:
    """Draw a colour card as a real PNG.

    ⚠ A COLOUR CARD HAS NO FILE, AND xmeml's ANSWER (`<generatoritem>`) IS THE
    ONE THING EVERY APP IMPLEMENTS DIFFERENTLY. Drawing the card as a picture
    turns "will this import" into "this is a PNG", which every editor on earth
    opens. It costs one small file per distinct colour.
    """
    from PIL import Image

    value = (color or "#000000").lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        rgb = tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        rgb = (0, 0, 0)
    Image.new("RGB", (max(2, int(width)), max(2, int(height))), rgb).save(path, "PNG")
    return path


# ⚠ ONE README PER FORMAT, because the first thing a user has to do differs:
# Premiere IMPORTS a file, After Effects RUNS one, and an EDL is conformed
# against media it already has. A single generic note would be wrong for two of
# the three, and this file is the only instruction most people will read.
READMES = {
    "fcp7": """HOW TO OPEN THIS IN PREMIERE PRO (or Resolve, or Avid, or Final Cut)

1. Unzip this folder somewhere and KEEP THE TWO PARTS TOGETHER:
       {doc}
       {media}/      <- every picture, clip and sound the timeline uses

2. In Premiere Pro:  File > Import...  and choose {doc}

3. If the clips come in red ("Media Offline"), that is normal and it is one
   click to fix: Premiere asks you to locate the media - point it at the
   "{media}" folder next to the XML. Locating ONE file finds all the rest,
   because they are all in that one folder.

WHAT CAME ACROSS
   Every clip, where it sits, how long it is, which track it is on, the part
   of each video that is used, the audio and its volume, and a dissolve on a cut.

WHAT DID NOT
   Colour grades, LUTs, masks, blend modes, pan/zoom moves, on-screen text and
   shapes. Those are this app's own effects and no project-exchange format
   carries them - they have to be redone in Premiere. The CUT is what travels.
""",
    "aftereffects": """HOW TO OPEN THIS IN AFTER EFFECTS

1. Unzip this folder somewhere and KEEP THE TWO PARTS TOGETHER:
       {doc}
       {media}/      <- every picture, clip and sound the timeline uses

2. In After Effects:  File > Scripts > Run Script File...

3. Choose {doc}. A composition is built with every clip on its own
   layer, in the right place, at the right length.

   After Effects cannot read a Premiere XML or an EDL, so this is a SCRIPT
   rather than a project file - it asks After Effects to build the comp using
   its own tools. Nothing is guessed at, and it keeps working across versions.

   The script looks for the "{media}" folder sitting next to itself. If you
   move it away, it will ask you where the folder is.

WHAT CAME ACROSS
   Every clip, where it sits, how long it holds, the part of each video that is
   used, the layer stack, opacity, and the audio with its levels.

WHAT DID NOT
   Colour grades, LUTs, masks, blend modes, pan/zoom moves, transitions,
   on-screen text and shapes. After Effects is where you would build those
   anyway - this brings you the CUT to build them on.
""",
    "edl": """WHAT THIS IS

   {doc} is an EDL (CMX3600) - the oldest and most widely read edit list
   there is. Almost every editing system will open it:

       Premiere Pro     File > Import...
       DaVinci Resolve  File > Import Timeline > Pre-Conformed EDL
       Avid             File > Input > EDL Import

   The media is in the "{media}" folder beside it. An EDL names REELS rather
   than file paths, so you will normally import the media first and then
   conform the EDL against it.

WHAT CAME ACROSS
   Your base video row and up to two audio channels - every clip, its place,
   its length and the part of each source that is used. Frame-exact.

WHAT DID NOT
   An EDL holds ONE video track, so anything on an upper row (including the
   Images lane) is not in here - use the Premiere XML for those. Dissolves are
   left out too, on purpose: a CMX dissolve starts at the cut where this app's
   sits across it, and writing one as the other would move your edit points.
   Colour grades, masks, text and shapes are not in any exchange format.
""",
}


def readme_for(fmt: str, doc_name: str) -> str:
    """The note that goes in the zip, for the format that was asked for."""
    return READMES[normalise_format(fmt)].format(doc=doc_name, media=MEDIA_DIR)


def bundle(
    model: dict,
    zip_path: str,
    doc_name: str,
    fmt: str = DEFAULT_FORMAT,
    base_path: str = "",
) -> dict:
    """Write the document, the media and a README into one zip. Returns the report.

    ⚠ THE MEDIA IS THE POINT. A project file on its own is a timeline of offline
    clips, which is exactly the thing users report as "it didn't work" — so this
    is the default shape of an export and `media=0` is the deliberate exception
    for somebody who already holds the footage.

    ⚠ AND THE MEDIA FOLDER IS THE SAME WHICHEVER FORMAT IS CHOSEN. All three
    writers name files in `media/` by the same names, so the zip's contents do
    not change when the dropdown does — only the one document at the top of it.
    """
    work = os.path.dirname(zip_path) or "."
    os.makedirs(work, exist_ok=True)
    fmt = normalise_format(fmt)
    doc = write_document(model, fmt, base_path=base_path)

    # ⚠ BUILT BESIDE AND RENAMED, never written over the last one. Zipping a
    # project's whole media folder is the slow part of this feature, and an
    # export that fails half way through — a file deleted under it, a full disk —
    # would otherwise have already destroyed the zip the user downloaded an hour
    # ago and left a truncated archive in its place.
    part = zip_path + ".part"
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(doc_name, doc)
        zf.writestr("README.txt", readme_for(fmt, doc_name))
        for entry in model["files"]:
            inside = posixpath.join(MEDIA_DIR, entry["name"])
            if entry["kind"] == "color":
                tmp = os.path.join(work, f"_colour_{safe_name(entry['color'], 'card')}.png")
                solid_png(entry["color"], model["width"], model["height"], tmp)
                zf.write(tmp, inside)
                os.remove(tmp)
                continue
            if entry["path"] and os.path.isfile(entry["path"]):
                zf.write(entry["path"], inside)
            else:
                # Named rather than skipped in silence: the user has to know
                # which shot will come in red.
                model["missing"].append(entry["name"])
    os.replace(part, zip_path)
    return report_of(model, fmt)


def write_document_only(
    model: dict, doc_path: str, fmt: str = DEFAULT_FORMAT, base_path: str = ""
) -> dict:
    """Just the recipe. For somebody whose media is already on their disk."""
    fmt = normalise_format(fmt)
    os.makedirs(os.path.dirname(doc_path) or ".", exist_ok=True)
    tmp = doc_path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(write_document(model, fmt, base_path=base_path))
    # ⚠ Written beside and RENAMED, never opened over the top of the last one.
    # A failed write that has already truncated the previous export leaves the
    # user with neither file.
    os.replace(tmp, doc_path)
    return report_of(model, fmt)


def report_of(model: dict, fmt: str = DEFAULT_FORMAT) -> dict:
    """What to SAY about an export — the shape the editor's dialog draws.

    ⚠ IT TAKES THE FORMAT, because the honest answer changes with it: an EDL
    leaves out every upper video row and every dissolve, and a user choosing it
    has to see that BEFORE the download rather than after the conform.
    """
    fmt = normalise_format(fmt)
    clips = sum(len(lane["clips"]) for lane in model["video"])
    sounds = sum(len(lane["clips"]) for lane in model["audio"])
    losses = dict(model["dropped"])
    for what, count in format_losses(model, fmt).items():
        losses[what] = losses.get(what, 0) + count
    return {
        "format": fmt,
        "clips": clips,
        "audio_clips": sounds,
        "video_tracks": len(model["video"]),
        "audio_tracks": len(model["audio"]),
        "files": len(model["files"]),
        "duration_frames": model["duration"],
        "fps": model["fps"],
        # Sorted by how much of it there is, so the biggest loss is the first
        # line the user reads.
        "dropped": [
            {"what": what, "count": count}
            for what, count in sorted(losses.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "missing": list(model["missing"]),
    }


def copy_media_to(model: dict, dest_dir: str) -> list:
    """The zip's `media/` folder, written to a real directory instead.

    Only the test uses this today — it is how `tests/interchange_check.py`
    checks that every `<pathurl>` in the XML lands on a file that exists,
    without unpacking a zip.
    """
    os.makedirs(dest_dir, exist_ok=True)
    written = []
    for entry in model["files"]:
        target = os.path.join(dest_dir, entry["name"])
        if entry["kind"] == "color":
            solid_png(entry["color"], model["width"], model["height"], target)
        elif entry["path"] and os.path.isfile(entry["path"]):
            shutil.copyfile(entry["path"], target)
        else:
            continue
        written.append(target)
    return written


# ===========================================================================
# READING — Phase 3. Somebody else's cut, brought in here.
# ===========================================================================
# ⚠ THE HARD PART OF AN IMPORT IS NOT THE PARSER, IT IS THE MEDIA. A project
# file names files by a path on the machine it was written on; a browser cannot
# read that path, and neither can this server. So the user hands over the
# document AND the footage (or the zip this app exported, which already holds
# both), and `to_project` matches them BY NAME. Anything unmatched becomes a
# labelled colour card so the CUT still arrives intact — see `to_project`.
#
# ⚠ AND NOTHING HERE WRITES TO A PROJECT. `read_document` and `to_project` are
# pure; the route hands the clips back and the EDITOR decides where they land,
# which is the contract `import_storyboard` and both uploads already follow
# ("the server produces the material, the client decides the timeline"). It is
# also what makes an import undoable: it becomes one entry on the editor's own
# undo stack instead of a write the user cannot take back.

# The extensions we can tell apart without opening the file. Deliberately the
# same three families the uploads already accept — an import must not be a way
# to get a file into a project that the upload routes would have refused.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac"}


class ImportRefused(Exception):
    """A file we will not pretend to read, with the sentence to show the user.

    ⚠ A REFUSAL IS A FEATURE HERE. `.prproj` and `.aep` are the two files people
    will reach for first, and both are undocumented private formats. Saying
    "export a Final Cut Pro XML from Premiere and give me that" is a route the
    user can actually walk; a half-parsed timeline is not.
    """


def media_kind(name: str) -> str:
    """'image' | 'video' | 'audio' from a filename, or '' if we can't tell."""
    ext = os.path.splitext(name or "")[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return ""


def detect_format(data: bytes, filename: str = "") -> str:
    """What kind of file this is — by its BYTES, not by its extension.

    ⚠ SNIFFED, because the extension is the one thing a user can get wrong for
    free (renaming a file, a browser that drops it, a download called
    `sequence.xml.txt`). The magic numbers are unambiguous and the two XML
    dialects announce themselves in their root element.

    Returns one of: `zip` (this app's own export bundle), `fcp7`, `edl`, or —
    for the three we refuse — `prproj`, `fcpx`, `aep`. `unknown` otherwise.
    """
    head = (data or b"")[:4096]
    if head[:2] == b"PK":
        return "zip"
    # ⚠ 1F 8B IS GZIP, AND A `.prproj` IS GZIPPED XML. Recognising it is what
    # lets the refusal say something useful instead of "not a valid file".
    if head[:2] == b"\x1f\x8b":
        return "prproj"
    if head[:4] == b"RIFX" or head[:4] == b"RIFF" and b"Egg!" in head:
        return "aep"
    try:
        text = head.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        text = ""
    lowered = text.lower()
    if "<xmeml" in lowered:
        return "fcp7"
    if "<fcpxml" in lowered:
        return "fcpx"
    # ⚠ A `.prproj` IS NOT ALWAYS GZIPPED. Premiere has a "save uncompressed"
    # preference, and a file that has been through an asset pipeline is often
    # unpacked already — so the root element has to be recognised as well as the
    # gzip header above, or a renamed uncompressed project reads as `unknown`.
    if "<premieredata" in lowered:
        return "prproj"
    if "<aftereffectsproject" in lowered:
        return "aep"
    if "fcm:" in lowered or _EDL_EVENT.search(text):
        return "edl"
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".edl":
        return "edl"
    if ext in (".prproj",):
        return "prproj"
    if ext in (".aep", ".aepx"):
        return "aep"
    return "unknown"


REFUSALS = {
    "prproj": (
        "A .prproj is Premiere's own private file and nothing outside Premiere "
        "can read it reliably. In Premiere: File › Export › Final Cut Pro XML, "
        "and bring that .xml here instead. If you cannot open Premiere, this can "
        "try to read it anyway — but the result is a guess you must check."
    ),
    "fcpx": (
        "That is a Final Cut Pro X file (.fcpxml), which is a different format "
        "from the one this reads. Final Cut can also write the older Final Cut "
        "Pro XML — use that, or bring an EDL."
    ),
    "aep": (
        "After Effects project files can only be opened by After Effects. If the "
        "edit came from Premiere, export a Final Cut Pro XML from there instead."
    ),
    "unknown": (
        "That doesn't look like a project file. This reads a Final Cut Pro XML "
        "(what Premiere Pro, Resolve and Avid all export), an EDL, or a .zip "
        "exported from here."
    ),
}


def timecode_to_frames(tc: str, fps: int) -> int:
    """HH:MM:SS:FF → a frame number. The inverse of `frames_to_timecode`.

    ⚠ THE LAST SEPARATOR MAY BE A SEMICOLON. In an EDL that means DROP FRAME,
    which this app can never produce (an integer fps is never 29.97) but can
    certainly be handed. It is read as if it were non-drop and the caller warns:
    a drop-frame list read as non-drop drifts by ~2 frames a minute, which is
    wrong slowly rather than wrong immediately, and refusing the whole import
    over it would be worse.
    """
    parts = re.split(r"[:;]", (tc or "").strip())
    if len(parts) != 4:
        return 0
    try:
        h, m, s, f = (int(p) for p in parts)
    except ValueError:
        return 0
    return ((h * 60 + m) * 60 + s) * max(1, int(fps)) + f


def frames_to_ms(frames, fps: int) -> int:
    """Frames → milliseconds, the way in.

    ⚠ MS IS THE MEETING POINT, AND THAT IS WHY AN IMPORT AT 25fps OPENS
    CORRECTLY IN A 24fps PROJECT. The incoming document's own rate is used here;
    the editor then holds absolute time, and the export converts back at whatever
    rate the project is set to. Converting frames straight to frames would nail
    somebody else's rate onto this film.
    """
    return int(round(float(frames or 0) * 1000.0 / max(1, int(fps))))


# ---------------------------------------------------------------------------
# FCP7 XML in
# ---------------------------------------------------------------------------
def _int_text(el, path, default=0):
    node = el.find(path) if el is not None else None
    if node is None or not (node.text or "").strip():
        return default
    try:
        return int(float(node.text.strip()))
    except ValueError:
        return default


def _basename_of(pathurl: str) -> str:
    """The filename out of a `<pathurl>`, whatever shape the path is in."""
    raw = unquote((pathurl or "").strip())
    raw = raw.split("?")[0].replace("\\", "/")
    return raw.rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# THE LETTERING IN AN `xmeml` — and this is the route that carries the COLOUR
# ---------------------------------------------------------------------------
# ⚠ **A TITLE IN FCP7 XML IS A GENERATOR, NOT A FILE**, which is why this reader
# used to drop every one of them: `read_clip` returns None for an item with no
# `<file>` to resolve, and a title has none. It has an `<effect>` instead, with
# `<effecttype>generator</effecttype>` and its whole appearance spelled out in
# `<parameter>` elements.
#
# ⚠ **AND UNLIKE A `.prproj`, THIS ONE GIVES UP THE FILL COLOUR.** `fontcolor`
# is a plain `<alpha>/<red>/<green>/<blue>` block in the XML. E59 established
# that a Premiere project file does not carry it anywhere — so for anybody who
# needs their title colours, "export a Final Cut Pro XML instead" stops being
# generic advice and becomes a concrete reason. Outline width and colour come
# across for the same reason.
#
# The parameter ids are FCP7's own and every app that writes this format uses
# them: `str` (the words), `fontname`, `fontsize`, `fontcolor`, `fontstyle`,
# `alignment`, `origin`, `linewidth` / `linecolor` for an outlined title.
_FCP7_TEXT_IDS = ("str", "text")
_FCP7_GENERATOR_TYPES = ("generator",)

# `<origin>` is the FCP convention: `horiz`/`vert` from -1 to 1 with 0 at the
# centre, and ⚠ **VERT COUNTS UPWARDS** while this app's `y` counts downwards.
FCP7_ORIGIN_SPAN = 2.0

# The frame FCP7 sizes type against. `fontsize` is in pixels of the sequence, so
# a 36pt title in a 720p sequence is not a 36px title at 1080p — `size_px` in
# this app is always "pixels at 1080p" (see `AnimaticTextClip.size_px`).
FCP7_REFERENCE_HEIGHT = 1080


def _fcp7_params(effect) -> dict:
    """`<parameter>` elements of one effect → `{parameterid: element}`.

    Keyed by `parameterid` lower-cased, because FCP writes `str` and some
    exporters write `Str`. An effect with a repeated id keeps the FIRST — a
    later duplicate is an exporter bug, and picking the last would silently
    prefer it.
    """
    out: dict = {}
    for param in effect.findall("parameter"):
        key = (param.findtext("parameterid") or "").strip().lower()
        if key and key not in out:
            out[key] = param
    return out


def _fcp7_colour(param) -> str:
    """An FCP `<value><alpha><red><green><blue>` block → `#rrggbb`. '' if absent.

    ⚠ ALPHA IS READ SEPARATELY BY THE CALLER, not folded in here: this app keeps
    a caption's colour and its opacity in two different fields, and squashing a
    50%-alpha red into a dark red would be a colour the user never chose and
    cannot undo.

    ⚠ AND THE CHANNELS MAY BE 0…255 OR 0…65535. FCP7 writes bytes; some
    exporters write 16-bit. A value over 255 is scaled down rather than clamped,
    because clamping turns every 16-bit colour into pure white.
    """
    if param is None:
        return ""
    value = param.find("value")
    if value is None:
        return ""
    channels = []
    for tag in ("red", "green", "blue"):
        text = (value.findtext(tag) or "").strip()
        if not text:
            return ""
        try:
            channels.append(float(text))
        except ValueError:
            return ""
    scale = 255.0 / 65535.0 if max(channels) > 255 else 1.0
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(c * scale)))) for c in channels
    )


def _fcp7_number(params: dict, key: str, default=None):
    """One numeric `<parameter><value>`, or `default`."""
    param = params.get(key)
    if param is None:
        return default
    text = (param.findtext("value") or "").strip()
    try:
        return float(text)
    except ValueError:
        return default


def _fcp7_text(item, height: int) -> dict:
    """A `<clipitem>`/`<generatoritem>` → the lettering in it, or `{}`.

    Returns the same shape `_prproj_graphic` does, so `to_project` cannot tell
    the two readers apart — one place decides what a caption becomes, whichever
    format it arrived in.
    """
    for effect in item.findall(".//effect"):
        kind = (effect.findtext("effecttype") or "").strip().lower()
        if kind and kind not in _FCP7_GENERATOR_TYPES:
            continue
        params = _fcp7_params(effect)
        words = ""
        for key in _FCP7_TEXT_IDS:
            if key in params:
                words = " ".join((params[key].findtext("value") or "").split())
                if words:
                    break
        if not words:
            continue
        got = {"text": words[:PRPROJ_MAX_TEXT_CHARS]}
        font = params.get("fontname")
        if font is not None:
            got["font"] = prproj_font_id(font.findtext("value") or "")
        # ⚠ SCALED INTO A 1080p FRAME. `fontsize` is pixels of THIS sequence.
        size = _fcp7_number(params, "fontsize")
        if size and height > 0:
            got["size_px"] = round(
                max(8.0, min(400.0, size * FCP7_REFERENCE_HEIGHT / height)), 2)
        elif size:
            got["size_px"] = round(max(8.0, min(400.0, size)), 2)
        # ⚠ THE ONE THING A `.prproj` COULD NOT GIVE (E59). Only set when the
        # block is really there — an absent `fontcolor` must leave this app's
        # own default in place rather than becoming black.
        ink = _fcp7_colour(params.get("fontcolor"))
        if ink:
            got["color"] = ink
        alpha = params.get("fontcolor")
        if alpha is not None:
            node = alpha.find("value")
            raw = (node.findtext("alpha") or "").strip() if node is not None else ""
            try:
                value = float(raw)
                span = 65535.0 if value > 255 else 255.0
                got["opacity"] = max(0.0, min(1.0, value / span))
            except ValueError:
                pass
        outline = _fcp7_number(params, "linewidth")
        if outline and outline > 0:
            got["stroke_px"] = max(0.0, min(24.0, outline))
            edge = _fcp7_colour(params.get("linecolor"))
            if edge:
                got["stroke_color"] = edge
        origin = params.get("origin")
        if origin is not None:
            node = origin.find("value")
            if node is not None:
                try:
                    horiz = float((node.findtext("horiz") or "").strip())
                    vert = float((node.findtext("vert") or "").strip())
                except ValueError:
                    return got
                # ⚠ VERT IS UP, `y` IS DOWN. Getting this backwards puts every
                # lower third at the top of the frame, which reads as the import
                # having ignored position rather than having inverted it.
                got["x"] = round(0.5 + horiz / FCP7_ORIGIN_SPAN, 4)
                got["y"] = round(0.5 - vert / FCP7_ORIGIN_SPAN, 4)
        return got
    return {}
def _read_fcp7(text: str) -> dict:
    """An `xmeml` document → the neutral incoming model."""
    warnings: list = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ImportRefused(f"That XML could not be read ({exc}).") from exc

    seq = root.find(".//sequence")
    if seq is None:
        raise ImportRefused("That XML has no sequence in it.")

    fps = _int_text(seq, "rate/timebase", 0)
    if not fps:
        fps = 24
        warnings.append("The XML did not say its frame rate; it was read at 24 fps.")
    # ⚠ NTSC MEANS THE RATE IS 1000/1001 OF THE TIMEBASE. Read as the whole
    # number, a 23.976 sequence drifts by ~3.6 frames a minute. Named rather than
    # corrected, because correcting it means fractional frame numbers everywhere
    # and this app's own projects are always integer rates.
    ntsc = (seq.findtext("rate/ntsc") or "").strip().upper() == "TRUE"
    if ntsc:
        warnings.append(
            f"This sequence is {fps} fps NTSC ({fps * 1000 / 1001:.3f}); it was read as "
            f"{fps} fps, so long clips may drift by a frame or two."
        )

    width = _int_text(seq, "media/video/format/samplecharacteristics/width", 0)
    height = _int_text(seq, "media/video/format/samplecharacteristics/height", 0)

    # ⚠ ONE FILE TABLE FOR THE WHOLE DOCUMENT. `<file id="file-3">` is written
    # out in full ONCE and referenced by id everywhere after — including from a
    # different track — so a per-track table loses every repeat of a picture.
    files: dict = {}

    def file_key(item) -> str:
        el = item.find("file")
        if el is None:
            return ""
        fid = el.get("id") or ""
        pathurl = el.findtext("pathurl")
        if pathurl:
            name = (el.findtext("name") or "").strip() or _basename_of(pathurl)
            files[fid] = {"name": name, "pathurl": pathurl.strip()}
        elif fid not in files:
            name = (el.findtext("name") or "").strip()
            if name:
                files[fid] = {"name": name, "pathurl": ""}
        return fid

    def read_clip(item, fps_) -> dict | None:
        start = _int_text(item, "start", -1)
        end = _int_text(item, "end", -1)
        # ⚠ -1 IS NOT A POSITION, IT IS "ASK THE TRANSITION". FCP writes it for a
        # clipitem whose place is defined by the transitionitem beside it. There
        # is nothing to place, so it is counted and left out rather than dropped
        # at frame zero, which is where a naive int() would put it.
        if start < 0 or end <= start:
            return None
        # ⚠ A TITLE HAS NO `<file>` AND USED TO BE DROPPED HERE. It is a
        # GENERATOR — see `_fcp7_text` — so the lettering is looked for BEFORE
        # the file key refuses the item, and a clip that turns out to be a title
        # is kept with no file at all. `to_project` knows what to do with that:
        # the same thing it does for a Premiere graphic.
        lettering = _fcp7_text(item, height)
        key = file_key(item)
        if not key and not lettering:
            return None
        clip = {
            "name": (item.findtext("name") or "").strip(),
            "file": key,
            "start": start,
            "end": end,
            "in": max(0, _int_text(item, "in", 0)),
            "out": max(0, _int_text(item, "out", 0)),
            "enabled": (item.findtext("enabled") or "TRUE").strip().upper() != "FALSE",
        }
        if lettering:
            clip["graphic"] = {"kind": "text", "texts": [lettering], "shapes": 0}
        return clip

    video: list = []
    skipped = 0
    for track_el in seq.findall("media/video/track"):
        lane = []
        # ⚠ `<generatoritem>` IS A SIBLING OF `<clipitem>`, NOT A KIND OF IT.
        # FCP7 gives a title its own element name, so a reader that walks only
        # `clipitem` never sees one — which is how every title in an `xmeml`
        # went missing. Both are read, in document order, so a title keeps its
        # place among the clips on its row.
        for item in list(track_el.findall("clipitem")) + list(
                track_el.findall("generatoritem")):
            clip = read_clip(item, fps)
            if clip is None:
                skipped += 1
                continue
            lane.append(clip)
        # A transition sits BETWEEN two clipitems on this track. It is attached
        # to the clip it comes after, which is how this app anchors one too.
        transitions = []
        for item in track_el.findall("transitionitem"):
            t_start = _int_text(item, "start", -1)
            t_end = _int_text(item, "end", -1)
            if t_start < 0 or t_end <= t_start:
                continue
            transitions.append({"start": t_start, "end": t_end})
        if lane or transitions:
            video.append({"clips": lane, "transitions": transitions})

    audio: list = []
    for track_el in seq.findall("media/audio/track"):
        lane = []
        for item in track_el.findall("clipitem"):
            clip = read_clip(item, fps)
            if clip is None:
                skipped += 1
                continue
            level = item.find(".//effect[effectid='audiolevels']/parameter/value")
            try:
                clip["level"] = float(level.text) if level is not None else 1.0
            except (TypeError, ValueError):
                clip["level"] = 1.0
            lane.append(clip)
        if lane:
            audio.append({"clips": lane})

    if skipped:
        warnings.append(
            f"{skipped} item(s) had no usable position in the XML and were left out."
        )

    return {
        "reader": "fcp7",
        "name": (seq.findtext("name") or "").strip() or "Imported sequence",
        "fps": fps,
        "width": width,
        "height": height,
        "files": files,
        "video": video,
        "audio": audio,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# EDL in
# ---------------------------------------------------------------------------
# `001  REELNAME V     C        <src in> <src out> <rec in> <rec out>`
_EDL_EVENT = re.compile(
    r"^(?P<num>\d{1,4})\s+(?P<reel>\S+)\s+(?P<chan>V|A|A2|AA|B|A2/V|AA/V)\s+"
    r"(?P<code>C|D|W\d*)\s*(?P<dur>\d+)?\s+"
    r"(?P<si>[\d:;]{11})\s+(?P<so>[\d:;]{11})\s+(?P<ri>[\d:;]{11})\s+(?P<ro>[\d:;]{11})",
    re.M,
)
_EDL_CLIP_NAME = re.compile(r"^\*\s*FROM CLIP NAME:\s*(?P<name>.+?)\s*$", re.I)


def _read_edl(text: str, fps_hint: int) -> dict:
    """A CMX3600 EDL → the neutral incoming model.

    ⚠ AN EDL DOES NOT SAY ITS FRAME RATE. `FCM:` says drop or non-drop and
    nothing else, so the rate has to come from the caller — this app uses the
    PROJECT's fps, which is the only number in the room that anybody chose. It is
    always warned about, because reading a 25fps list at 24 puts every cut 4%
    late and the user has no other way to find out.
    """
    warnings = [
        f"An EDL never states its frame rate, so it was read at this project's "
        f"{fps_hint} fps. If the cut looks stretched, that is why."
    ]
    if ";" in text:
        warnings.append(
            "This EDL is drop-frame. It was read as non-drop, which can drift by "
            "a couple of frames a minute over a long list."
        )

    title = "Imported EDL"
    for line in text.splitlines():
        if line.upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip() or title
            break

    # ⚠ THE NAME COMES FROM THE COMMENT UNDER THE EVENT, and the comment follows
    # the line it belongs to. Walking the lines in order is the only way to pair
    # them; a regex over the whole file loses which name went with which event.
    lines = text.splitlines()
    video: list = []
    audio: dict = {}
    files: dict = {}
    events = 0
    for i, line in enumerate(lines):
        match = _EDL_EVENT.match(line.strip())
        if not match:
            continue
        events += 1
        name = ""
        for follow in lines[i + 1: i + 4]:
            got = _EDL_CLIP_NAME.match(follow.strip())
            if got:
                name = got.group("name")
                break
        reel = match.group("reel")
        key = name or reel
        files.setdefault(key, {"name": name or reel, "pathurl": ""})
        clip = {
            "name": name or reel,
            "file": key,
            "start": timecode_to_frames(match.group("ri"), fps_hint),
            "end": timecode_to_frames(match.group("ro"), fps_hint),
            "in": timecode_to_frames(match.group("si"), fps_hint),
            "out": timecode_to_frames(match.group("so"), fps_hint),
            "enabled": True,
            "level": 1.0,
        }
        if clip["end"] <= clip["start"]:
            continue
        chan = match.group("chan")
        if chan.startswith("V") or chan.endswith("/V") or chan == "B":
            if not video:
                video.append({"clips": [], "transitions": []})
            video[0]["clips"].append(clip)
        else:
            audio.setdefault(chan, []).append(clip)
        if match.group("code") != "C":
            warnings.append(
                "This EDL has dissolves in it. They were read as straight cuts — "
                "the clips are in the right places, the blends are not."
            )

    if not events:
        raise ImportRefused("No edit events were found in that EDL.")

    return {
        "reader": "edl",
        "name": title,
        "fps": fps_hint,
        "width": 0,
        "height": 0,
        "files": files,
        "video": video,
        "audio": [{"clips": clips} for _, clips in sorted(audio.items())],
        # De-duplicated: one dissolve warning, not forty.
        "warnings": list(dict.fromkeys(warnings)),
    }


# ---------------------------------------------------------------------------
# .prproj in — PHASE 4, AND THE ONLY GUESS IN THIS FILE
# ---------------------------------------------------------------------------
# ⚠ **EVERY OTHER READER HERE READS A PUBLISHED FORMAT. THIS ONE DOES NOT.**
# `xmeml` and CMX3600 have specifications anyone can check. A `.prproj` is
# Premiere's own save file: Adobe has never published its structure, and it moved
# again in Premiere 2026. So this is a GUESS — a careful one, with a fallback,
# that says so on every import it produces — and it is **opt-in**:
# `read_document` still REFUSES a `.prproj` unless the caller passes
# `experimental=True`, which the route only does after the user has read the
# refusal and asked for it anyway. The refusal stays the default answer because
# it names a route that always works; this is the second answer, not the first.
#
# ⚠ IT IS A GRAPH, NOT A TREE, AND THAT IS THE WHOLE DIFFICULTY. A `.prproj` is a
# flat pile of objects, each carrying an `ObjectID`, wired together by
# `ObjectRef` attributes — a clip's POSITION is four hops away from the clip
# itself. So nothing here uses a fixed path like `sequence/media/video/track`,
# the way `_read_fcp7` safely can. It indexes the objects and follows the
# references, which is the only shape that survives Adobe moving a level.
#
# ⚠ TWO LAYERS, AND THE MODEL SAYS WHICH ONE ANSWERED. A reader for an
# undocumented format that works exactly one way is a reader that works until the
# next release:
#
#   structured  the sequence's own graph was walked, so every clip kept the
#               TRACK it was on and the order it was in.
#   flat        no sequence could be recognised, so every clip item in the file
#               is taken in document order onto one row per media type. The cut
#               is still right; the ROWS are not, and the user is told so.
#
# ⚠ TICKS, NOT FRAMES. Premiere counts time in 254,016,000,000 ticks per second —
# a number picked because every common frame rate and audio rate divides into it
# exactly. It becomes frames HERE and milliseconds in `to_project`, so a
# `.prproj` goes through the same single rounding rule as everything else.
#
# ⚠ WHAT IS NOT ATTEMPTED, ON PURPOSE: effects, titles, colour, speed changes,
# audio levels, and nested sequences. Guessing at those out of an undocumented
# format would put numbers on the timeline that the user has no way to check;
# leaving them out leaves a gap they can see. Each one is named in `warnings`.

# Premiere's time base. Every position in the file is an integer count of these.
PRPROJ_TICKS_PER_SECOND = 254_016_000_000

# ⚠ A DECOMPRESSION CAP, BECAUSE THE ROUTE'S UPLOAD LIMIT IS ON THE COMPRESSED
# BYTES. A `.prproj` is gzip, and XML of this shape packs at better than 50:1 —
# so an upload that passes `MAX_UPLOAD_BYTES` can still unpack to something that
# takes the process down. This is the only place that can stop it, so the limit
# lives here rather than at the route.
PRPROJ_MAX_XML_BYTES = 96 * 1024 * 1024

# How far a chain of references is followed. A real one is six or seven hops
# (sequence → track groups → group → tracks → track → items → item); the ceiling
# exists only so a file with a cycle in it cannot hang the request.
PRPROJ_MAX_DEPTH = 32

# The longest caption a Premiere graphic is allowed to hand over. It is a guard
# on a BINARY SCAN, not a style rule: `_prproj_arb_strings` decides where a
# string ends by trusting a length it read out of the file, so without a ceiling
# a corrupt four bytes could claim the rest of the blob. Long enough that no real
# title is ever cut (the longest in the reference project is 46 characters).
PRPROJ_MAX_TEXT_CHARS = 2000

# Where Premiere writes the thing this app actually needs: the path of the file
# on the machine that saved the project. Only the BASENAME is ever used — see
# `to_project`, which matches media by name because a path from someone else's
# computer is not a path this server can open.
_PRPROJ_PATH_TAGS = (
    "ActualMediaFilePath",
    "FilePath",
    "MediaFilePath",
    "ActualMediaFilePathURL",
)


def prproj_ticks_to_frames(ticks, fps: int) -> int:
    """Premiere ticks → whole frames, rounding half AWAY FROM ZERO.

    The same rule as `ms_to_frames` and for the same reason: two rounding rules
    in one file is how a hundred clips end up half a frame apart.
    """
    try:
        value = float(ticks or 0) * int(max(1, fps)) / float(PRPROJ_TICKS_PER_SECOND)
    except (TypeError, ValueError):
        return 0
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _gunzip_capped(data: bytes, limit: int = PRPROJ_MAX_XML_BYTES) -> bytes:
    """A gzip member unpacked, refusing anything that expands past `limit`."""
    dec = zlib.decompressobj(zlib.MAX_WBITS | 16)
    try:
        out = dec.decompress(data, limit + 1)
    except zlib.error as exc:
        raise ImportRefused(
            "That .prproj could not be unpacked — it may be damaged, or only "
            "partly downloaded."
        ) from exc
    if len(out) > limit or dec.unconsumed_tail:
        raise ImportRefused(
            f"That .prproj unpacks to more than {limit // (1024 * 1024)} MB, which is "
            "past what this can read."
        )
    return out


def _prproj_index(root) -> dict:
    """Every object in the file, keyed by BOTH kinds of name it can be called by.

    ⚠ **PREMIERE USES TWO KINDS OF REFERENCE AND MISSING ONE COSTS THE WHOLE
    TIMELINE.** An object is named either by a numeric `ObjectID` or by a GUID
    `ObjectUID`, and it is pointed at by `ObjectRef` or `ObjectURef` to match.
    The first version of this reader followed only `ObjectRef` — and the link
    from a track group to its TRACKS is an `ObjectURef`, so it found no tracks
    at all, fell back to the flat route, and reported 167 clips it could not
    place. One dictionary holds both: a GUID and a decimal id cannot collide.
    """
    found: dict = {}
    for el in root.iter():
        for attr in ("ObjectID", "ObjectUID"):
            key = el.get(attr)
            if key and key not in found:
                found[key] = el
    return found


def _prproj_ref(el) -> str:
    """What this element points at, whichever kind of reference it uses."""
    return el.get("ObjectRef") or el.get("ObjectURef") or ""


def _prproj_int(el, tag: str):
    """A direct child read as an integer, or None when there isn't one.

    None rather than 0 on purpose: a missing `<Start>` and a clip that starts at
    the head of the sequence are different facts, and only one of them is a clip
    this reader understood.
    """
    if el is None:
        return None
    node = el.find(tag)
    if node is None or not (node.text or "").strip():
        return None
    try:
        return int(float(node.text.strip()))
    except ValueError:
        return None


def _prproj_looks_like_path(value: str) -> bool:
    """Is this `<FilePath>` really a path, or one of Premiere's internal ids?

    ⚠ **A TITLE HAS A `<FilePath>` AND IT IS A NUMBER.** A Graphic is drawn
    inside Premiere and has no file on disk, but it still carries the tag — with
    something like `1196574294` in it. Taken at face value that becomes a
    "missing media" line reading `1196574294`, which names a file the user cannot
    go and find and cannot ever match. A real path has a separator or an
    extension; an id has neither.
    """
    value = (value or "").strip()
    if not value:
        return False
    if "/" in value or "\\" in value:
        return True
    return "." in os.path.basename(value)


def _prproj_is_timeline(tag: str) -> bool:
    """Objects a CLIP's walk must never climb into.

    ⚠ THE MEDIA IS SHARED, AND THAT IS THE TRAP. One `Media` object is pointed at
    by every clip cut from that file, and it is also pointed at from the bin —
    so following references out of it reaches the project, and from the project
    every other sequence in the file. Without this stop list, working out where
    ONE clip sits reads the whole document and picks up somebody else's numbers.
    """
    return (
        tag.endswith("Track")
        or tag.endswith("TrackGroup")
        or tag.endswith("TrackGroups")
        or tag.endswith("ProjectItem")
        or tag in ("Sequence", "Project")
    )


def _prproj_times(el) -> tuple:
    """A clip item's place on the timeline: `(start, end)` in ticks, or `(None, None)`.

    ⚠ **THE TIMES ARE NESTED INSIDE THE CLIP, NOT A SEPARATE OBJECT.** Premiere
    writes `<VideoClipTrackItem><ClipTrackItem><TrackItem><End>` — all one
    element, no references in between. The first version of this reader looked
    for `<Start>`/`<End>` as DIRECT children and followed `ObjectRef`s to find
    the `TrackItem`, so on a real project it read nothing at all from any of the
    167 clips it had found.

    ⚠ **AND A `<Start>` OF ZERO IS SIMPLY NOT WRITTEN.** Of those 167, seventeen
    had no `<Start>` element because they begin at the head of their track;
    requiring one (as the first version did, demanding Start AND End together)
    throws away every clip that starts the film. Missing means zero. `<End>` is
    the one that must really be there — without it there is no clip.
    """
    # ⚠ NESTED FIRST, THEN THE ELEMENT ITSELF. `.//` searches DESCENDANTS and
    # never the node it is called on, so a file that DOES keep its `TrackItem`
    # as its own referenced object (which is the shape this reader was first
    # written against) would answer None for the very object holding the times.
    # Real Premiere nests it; both shapes are read.
    track_item = el.find(".//TrackItem")
    if track_item is None and el.tag.endswith("TrackItem"):
        track_item = el
    if track_item is None:
        return None, None
    end = _prproj_int(track_item, "End")
    if end is None:
        return None, None
    return (_prproj_int(track_item, "Start") or 0), end


def _prproj_in_point(el):
    """How far into the FILE this clip starts reading, in ticks, or None.

    ⚠ NESTED, EXACTLY LIKE THE TIMES ABOVE — and missing it cost a whole
    soundtrack. Premiere writes `<AudioClip><Clip><InPoint>`, so `<InPoint>` is a
    GRANDCHILD of the clip object, and `_prproj_int` reads a DIRECT child. Every
    clip therefore answered None, `to_project` read that as 0, and every clip
    played its file **from the beginning**.

    ⚠ IT IS INVISIBLE UNTIL ONE FILE IS CUT INTO SEVERAL CLIPS. A timeline of
    whole takes has every in-point at 0 already, which is why the four video
    clips in the first real import looked perfectly correct. The same project's
    voiceover was ONE mp3 razored into 23 pieces with the silences taken out —
    and all 23 restarted the recording, so the film played its first few seconds
    over and over. Reported as "har clip audio ka starting hi play ho raha hai".

    ⚠ AND `MasterClip` CARRIES NO `<InPoint>` (verified against a real project:
    only `VideoClip` and `AudioClip` do, both at the same depth), so searching
    descendants cannot pick up the bin's in-point by mistake — which is the one
    thing that would make this worse than reading nothing.
    """
    if el is None:
        return None
    # `.//` searches descendants and never the node itself, so the element's own
    # `<InPoint>` is tried too — the shape the hand-built fixture was written in.
    node = el.find(".//InPoint")
    if node is None:
        node = el.find("InPoint")
    if node is None or not (node.text or "").strip():
        return None
    try:
        return int(float(node.text.strip()))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# THE LETTERING — reading a Premiere title out of a .prproj
# ---------------------------------------------------------------------------
# ⚠ **THIS APP TOLD USERS THAT PREMIERE TITLES COULD NOT BE IMPORTED, AND THAT
# WAS WRONG.** It was wrong because only the clip's `<Name>` was ever looked at.
# Every title, caption and lower third in a Premiere project is a clip NAMED
# "Graphic" — the name says nothing at all — so a project with forty captions in
# it reads as forty empty clips, and the honest-sounding warning that came out
# of that ("any LETTERING has to be typed again") sent users off to retype work
# the file was holding the whole time.
#
# The words are one level further in, on a `<VideoFilterComponent>` whose
# `<MatchName>` is `AE.ADBE Text`, and they are there TWICE:
#
#   1. `<InstanceName>` — Premiere names a text layer after its own text. Plain
#      XML, trivially readable, and STALE the moment somebody renames the layer
#      in Premiere, which is a thing people do.
#   2. The `Source Text` parameter's `<StartKeyframeValue>` — a base64 FlatBuffer
#      holding the real string. It cannot go stale, so it is read FIRST and
#      `<InstanceName>` is only the fallback.
#
# ⚠ **THE BLOB IS READ FOR ITS STRINGS AND NOTHING ELSE.** A FlatBuffer addresses
# its fields through a vtable, so field N sits at a different byte offset in
# every record — 83 records out of one real project were measured and the floats
# moved in every one. What does NOT move is how a string is encoded:
# `<uint32 length><bytes><NUL>`. Two appear in every text record, in this order,
# and that is the whole of what this reads:
#
#       [0] the FONT's PostScript name  ("Tahoma", "Tahoma-Bold")
#       [1] the TEXT                    ("like a database, a calendar")
#
# Anything that needs a vtable to find is deliberately NOT attempted — and the
# FILL COLOUR, which is the first thing anybody asks for, is exactly that. See
# `_prproj_text_style` for where it was looked for and why it is not there.
_PRPROJ_TEXT_MATCH = "AE.ADBE Text"
_PRPROJ_SHAPE_MATCH = "AE.ADBE Shape"

# How big the type is. Premiere keeps the point size inside the FlatBuffer (not
# reachable — see above) and the `Scale` percentage in plain XML (reachable), so
# the SIZE has to be carried by the scale and this constant.
#
# ⚠ IT IS FITTED, NOT GUESSED, and the fit is checkable. Premiere stores a text
# layer's position as the LEFT EDGE of the line, so for a row of CENTRED captions
# `left + half the line's width` has to come back to 0.5 for every one of them.
# Solving that over 78 captions of 10–46 characters gives a mean of 0.4956 with a
# spread of 0.013 — this constant reproduces where Premiere actually put the
# words to within half a percent of the frame width. 0.85 and 0.95 both come out
# visibly worse (0.4866 / 0.5046). `tests/interchange_check.py` §8h pins the fit,
# so a tidier-looking number cannot be swapped in unnoticed.
PRPROJ_TEXT_SIZE_PER_SCALE = 0.9
# Average glyph advance as a fraction of the size, used ONLY to turn that left
# edge into a centre. Half the size is the ordinary rule of thumb for a
# proportional sans, and it is what the fit above was solved with.
PRPROJ_TEXT_ASPECT = 0.5
# The frame a caption is measured in. `size_px` means "pixels at 1080p"
# everywhere else in this app, so the width has to be worked in the same frame or
# the centre lands somewhere else the moment the project is 720p.
PRPROJ_TEXT_FRAME_W = 1920
PRPROJ_TEXT_FRAME_H = 1080

# ⚠ **AND THE VERTICAL IS AN EDGE TOO — THE BASELINE.** The same lesson as the
# left edge above, on the other axis, and it was found the same way: by
# measuring a RENDER. In episode 7 of the reference series every caption sits at
# `Motion.Position 0.5:0.9211` with the text layer at `0.5219`, which composes to
# a y of **0.9430** — and in the exported .mp4 the white lettering's band bottom
# is at **0.944**. So what Premiere stores is where the letters SIT, and this
# app's `y` is the block's CENTRE (`draw_texts`: `top = cy - height / 2`).
#
# The gap between the two is a fraction of the FONT SIZE, so it is written that
# way rather than as a flat number: measured over five frames of that render the
# lettering's visual centre is 0.928 ± 0.003 against a stored 0.9430, which at
# `size_px` 45 in a 1080-high frame is **0.36 em**. ⚠ THAT NUMBER AGREES WITH
# TYPOGRAPHY RATHER THAN JUST FITTING ONE FILM — half a cap height is ~0.35 em
# for a proportional sans — which is the only reason one measured size is enough
# to write it down. Pinned in `tests/interchange_check.py` §8l.
PRPROJ_TEXT_BASELINE_TO_CENTRE = 0.36

# Premiere writes a font's POSTSCRIPT name ("Tahoma-Bold", "ArialMT"); this app
# ships fourteen faces and can only draw one of those. The mapping is by FAMILY,
# lower-cased, with the weight suffix already stripped — so "Tahoma-Bold" and
# "Tahoma" land on the same face, which is right: this app has one weight per
# family too.
#
# ⚠ A FONT NOBODY HAS IS NOT AN ERROR. Anything unlisted folds down to the
# default, exactly the way an unknown font id already does in `font_entry`.
_PRPROJ_FONT_FAMILIES = {
    "arial": "inter", "helvetica": "inter", "helveticaneue": "inter",
    "tahoma": "inter", "verdana": "inter", "segoeui": "inter",
    "calibri": "inter", "opensans": "inter", "roboto": "inter",
    "sourcesans": "inter", "lato": "inter", "notosans": "inter",
    "inter": "inter",
    "montserrat": "montserrat", "poppins": "poppins", "nunito": "nunito",
    "anton": "anton", "bebasneue": "bebas", "bebas": "bebas",
    "oswald": "oswald", "impact": "anton",
    "archivoblack": "archivo", "archivo": "archivo",
    "playfairdisplay": "playfair", "playfair": "playfair",
    "merriweather": "merriweather", "georgia": "merriweather",
    "timesnewroman": "merriweather", "times": "merriweather",
    "garamond": "playfair", "baskerville": "playfair",
    "bangers": "bangers", "lobster": "lobster", "caveat": "caveat",
    "brushscript": "caveat",
    "courierprime": "courier", "couriernew": "courier", "courier": "courier",
    "consolas": "courier", "menlo": "courier", "monaco": "courier",
}

# The weight words Premiere hangs off a PostScript name, stripped before the
# lookup so "Montserrat-SemiBold" finds "montserrat". Longest first, so
# "semibold" is never left as a trailing "bold".
_PRPROJ_FONT_WEIGHTS = (
    "bolditalic", "boldoblique", "extralight", "ultralight", "extrabold",
    "semibold", "demibold", "oblique", "regular", "italic", "medium", "light",
    "black", "heavy", "roman", "book", "bold", "thin",
    "psmt", "std", "pro", "mt", "ms",
)


def prproj_font_id(name: str) -> str:
    """A Premiere PostScript font name → one of this app's bundled font ids.

    ⚠ NEVER RAISES AND NEVER RETURNS SOMETHING UNBUNDLED. An unknown face is the
    ordinary case, not a failure: this app has fourteen fonts and a Premiere user
    has hundreds. See `_PRPROJ_FONT_FAMILIES`.
    """
    key = re.sub(r"[^a-z]", "", str(name or "").split("-")[0].lower())
    while True:
        for suffix in _PRPROJ_FONT_WEIGHTS:
            if key.endswith(suffix) and len(key) > len(suffix):
                key = key[: -len(suffix)]
                break
        else:
            break
    return _PRPROJ_FONT_FAMILIES.get(key, animatic_fonts.DEFAULT_FONT)


def _prproj_arb_strings(payload: str) -> list:
    """The printable strings inside one base64 `Arb…Param` blob, in order.

    `<uint32 length><bytes><NUL>`, scanned byte by byte. ⚠ THE SCAN STEPS OVER A
    STRING IT ACCEPTS rather than moving on one byte, so the bytes of a caption
    can never be re-read as the length of another — a sentence whose own bytes
    happen to spell a plausible length would otherwise yield a second, invented
    entry, and this reader's whole job is to not invent anything.

    Returns `[]` for anything that will not decode. This runs on a file format
    nobody documented: it has to come back empty, never raise.
    """
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", payload or ""), validate=False)
    except Exception:
        return []
    out: list = []
    i, n = 0, len(raw)
    limit = PRPROJ_MAX_TEXT_CHARS * 4
    while i + 4 < n:
        size = int.from_bytes(raw[i:i + 4], "little")
        end = i + 4 + size
        if 1 <= size <= limit and end < n and raw[end] == 0:
            try:
                word = raw[i + 4:end].decode("utf-8")
            except UnicodeDecodeError:
                word = ""
            if word and word.isprintable():
                out.append(word)
                i = end + 1
                continue
        i += 1
    return out


def _prproj_param(by_id: dict, comp, wanted: str):
    """One named `<Param>` of a component, followed through its ObjectRef."""
    for ref in comp.iter("Param"):
        target = by_id.get(_prproj_ref(ref) or "")
        if target is None:
            continue
        if (target.findtext("Name") or "").strip() == wanted:
            return target
    return None


def _prproj_point(el):
    """A `<Param>` holding an "x:y" keyframe → `(x, y)`, or None."""
    if el is None:
        return None
    value = _prproj_keyframe_value(el)
    if ":" not in value:
        return None
    try:
        left, top = (float(v) for v in value.split(":", 1))
    except ValueError:
        return None
    return (left, top)


def _prproj_rect(el) -> tuple | None:
    """A `<FrameRect>` (`"0,0,1920,1080"`) as `(width, height)`, or None.

    ⚠ **THIS IS THE NUMBER THAT MAKES A FIXED `Scale` MEAN SOMETHING HERE**, and
    it was sitting in the file the whole time the reader was telling users their
    logo could not be sized. Premiere measures Scale against the SOURCE's own
    pixels, so 24% is a postage stamp or a full frame depending entirely on how
    big the file is — and both halves of that sum are `<FrameRect>`:

        `VideoClipTrackItem` › `<FrameRect>`   the SEQUENCE frame (1920×1080)
        `VideoStream`        › `<FrameRect>`   the SOURCE's own pixels

    Verified on the reference project: 253 clip items all read 1920×1080 while
    the streams behind them read 1672×941 (the slides), 1280×720 (the footage)
    and 1920×309 (the logo). `_prproj_detail` collects both, one per clip.
    """
    parts = (el.findtext("FrameRect") or "").split(",") if el is not None else []
    if len(parts) != 4:
        return None
    try:
        width, height = int(float(parts[2])), int(float(parts[3]))
    except ValueError:
        return None
    return (width, height) if width > 0 and height > 0 else None


# ⚠ **A PREMIERE CLIP'S POSITION IS NOT ON THE TEXT COMPONENT AT ALL, AND THAT
# IS WHY EVERY IMPORTED CAPTION LANDED IN THE MIDDLE OF THE SCREEN.**
# `AE.ADBE Text`'s own `Position` is where the lettering sits INSIDE the graphic
# — for a caption built from Premiere's own template that is 0.52, i.e. the
# middle — while what puts the graphic at the BOTTOM of the frame is the clip's
# `AE.ADBE Motion`, the Position/Anchor Point every Premiere clip has and which
# this reader never opened. In the reference project **78 of 82 captions carry
# `Motion.Position 0.5:0.9211`** and the other four are title cards at
# `0.5:0.5`; reading only the text component put all 82 within 0.03 of the frame
# centre. It read as "the position was ignored", and it nearly was.
#
# ⚠ **THERE ARE TWO OF THESE AND BOTH APPLY.** `AE.ADBE Geometry2` is the
# **Transform effect** somebody adds by hand — the user who reported this had
# keyframed one on every caption — and it stacks on top of Motion rather than
# replacing it. In that project it happens to be identity (`0.5:0.5`), so
# reading only Motion would have looked right on this film and been wrong on the
# next one. Sum the offsets.
#
# ⚠ **AN OFFSET, NOT A POSITION.** Both components move the layer so the point at
# `Anchor Point` lands on `Position`, so what they contribute is
# `Position - Anchor Point` — zero for an untouched clip, which is what makes it
# safe to add to every caption.
_PRPROJ_MOTION_MATCH = "AE.ADBE Motion"
_PRPROJ_GEOMETRY_MATCH = "AE.ADBE Geometry2"
# ⚠ THE CLIP'S OPACITY IS ITS OWN COMPONENT, not a parameter of Motion. Premiere
# keeps `Opacity` (and the blend mode with it) in a separate intrinsic filter, so
# a reader that only opened Motion and Geometry2 could not see a single fade.
_PRPROJ_OPACITY_MATCH = "AE.ADBE Opacity"
_PRPROJ_TRANSFORM_MATCHES = (
    _PRPROJ_MOTION_MATCH,
    _PRPROJ_GEOMETRY_MATCH,
    _PRPROJ_OPACITY_MATCH,
)


def _prproj_placement(by_id: dict, comp) -> tuple:
    """One transform component → how far it MOVES the layer, in frame fractions."""
    point = _prproj_point(_prproj_param(by_id, comp, "Position"))
    if point is None:
        return (0.0, 0.0)
    anchor = _prproj_point(_prproj_param(by_id, comp, "Anchor Point")) or (0.5, 0.5)
    return (point[0] - anchor[0], point[1] - anchor[1])


def _prproj_scale_param(by_id: dict, comp):
    """The one parameter carrying a component's UNIFORM scale, or None.

    ⚠ **WITH "UNIFORM SCALE" TICKED, PREMIERE WRITES THE VALUE INTO `Scale
    Height` AND PARKS `Scale Width` AT 100.** Verified in a real project: of 101
    Transform effects, 99 have `Scale Height`/`Scale Width` and no `Scale` at
    all, `Scale Width` is a static `100.` on every one of them, and `Scale
    Height` is what the zoom preset animates. Reading `Scale Width` as a second
    axis would therefore report every one of those clips as squashed.

    So: `Scale` when the component has one (that is Motion's own, and a Transform
    with uniform scale off), otherwise `Scale Height`.
    """
    return (_prproj_param(by_id, comp, "Scale")
            or _prproj_param(by_id, comp, "Scale Height"))


def _prproj_keyframe_value(el) -> str:
    """The RESTING value of a parameter, as text. '' when there is none.

    A keyframe reads `<time>,<value>,0,0,…`; the value is one field and may be a
    number ("50."), a boolean ("true") or a point ("0.279:0.521"). This reads
    `<StartKeyframe>`, which is the value the parameter holds before its first
    real key — for an un-animated parameter that is simply its value.

    ⚠ **THE ANIMATION IS NO LONGER THROWN AWAY HERE — `_prproj_keyframe_rows`
    READS IT.** This function keeps its old job (one number, for the parameters
    that are read as a single number: a colour, a shadow angle, a boolean), and
    callers that want the movement ask for the rows instead.
    """
    row = (el.findtext("StartKeyframe") or "").split(",")
    return row[1].strip() if len(row) > 1 else ""


def _prproj_keyframe_rows(el) -> list:
    """Every real keyframe on one `<Param>` as `(ticks, value string)`, in order.

    ⚠ **`<StartKeyframe>` IS NOT ONE OF THEM, AND ADDING IT WOULD BE A BUG.** It
    is stamped at tick `-91445760000000000` — a hundred hours before the film —
    so Premiere interpolates from it across that whole span and its value has no
    measurable effect at the clip. In the reference project it is `100.` while
    the first real key, thirteen milliseconds later, is `80.`: emitting both
    would put a 100→80 snap at the head of every clip that was never there. It is
    the value the parameter RESTS at, which is what `_prproj_keyframe_value`
    returns and is only meaningful when there are no real keys at all.

    ⚠ THE TIMES ARE IN THE CLIP'S OWN SOURCE CLOCK, not the timeline's. They are
    rebased against the clip's `<InPoint>` — see `prproj_transform_keys`, which
    also refuses a track whose times land nowhere near the clip rather than
    trusting a base it cannot verify.
    """
    out: list = []
    for row in (el.findtext("Keyframes") or "").split(";"):
        fields = row.split(",")
        if len(fields) < 2:
            continue
        try:
            ticks = int(float(fields[0].strip()))
        except ValueError:
            continue
        out.append((ticks, fields[1].strip()))
    out.sort(key=lambda pair: pair[0])
    return out


def _prproj_text_style(by_id: dict, comp) -> dict:
    """The reachable half of a text component's look. See the section header.

    ⚠ **THE FILL COLOUR IS NOT IN HERE, AND THAT IS A FINDING RATHER THAN A GAP.**
    It was looked for in a real project, in every place it could be:
      · there is no colour `<Param>` on the component — its eighteen named
        parameters are Source Text, Transform, Position, Scale, Horizontal Scale,
        Rotation, Opacity, Anchor Point, start, end and Parent W/H/Rotation;
      · the `Source Text` blob holds no float in 0..1, no `00 00 80 3F` (1.0f) and
        no `FF FF FF FF` in ANY of its 83 records — white would have shown as one
        of the three;
      · the `<PremiereFilterPrivateData>` elements that would carry a serialised
        appearance are EMPTY. They carry a `BinaryHash` attribute and no body,
        because Premiere keeps that payload outside the project XML altogether —
        184 of the 206 in the reference file are self-closing.
    So an imported caption takes THIS APP'S colour, and `to_project` says so in
    the import report rather than inventing a white that might have been yellow.
    """
    style = {"font": animatic_fonts.DEFAULT_FONT, "scale": 100.0, "opacity": 1.0}
    scale = _prproj_param(by_id, comp, "Scale")
    if scale is not None:
        try:
            style["scale"] = float(_prproj_keyframe_value(scale).rstrip("."))
        except ValueError:
            pass
    opacity = _prproj_param(by_id, comp, "Opacity")
    if opacity is not None:
        try:
            style["opacity"] = max(0.0, min(1.0, float(
                _prproj_keyframe_value(opacity).rstrip(".")) / 100.0))
        except ValueError:
            pass
    point = _prproj_param(by_id, comp, "Position")
    value = _prproj_keyframe_value(point) if point is not None else ""
    if ":" in value:
        try:
            left, top = (float(v) for v in value.split(":", 1))
        except ValueError:
            return style
        style["left"], style["y"] = left, top
    return style


def _prproj_text_component(by_id: dict, el) -> dict:
    """One `AE.ADBE Text` component → a caption, or `{}` if it holds no words.

    An EMPTY text layer is a real thing and not an error: Premiere leaves one
    behind whenever a graphic is built from a template and a field is not filled
    in. `{}` means "nothing to import", never "stop looking" — see
    `_prproj_graphic`, which was returning the first answer and losing the second.
    """
    source = _prproj_param(by_id, el, "Source Text")
    words: list = []
    if source is not None:
        node = source.find("StartKeyframeValue")
        if node is not None:
            words = _prproj_arb_strings(node.text or "")
    # [font, text] — see the section header. The LAST string is the caption: a
    # record carrying only one string has lost its font, not its words.
    text = words[-1] if words else ""
    font_name = words[0] if len(words) > 1 else ""
    if not text:
        # The stale-able fallback, and the reason it is second.
        node = el.find(".//InstanceName")
        text = (node.text or "").strip() if node is not None else ""
    text = " ".join(text.split())
    if not text:
        return {}
    style = _prproj_text_style(by_id, el)
    size_px = max(8.0, min(400.0, style["scale"] * PRPROJ_TEXT_SIZE_PER_SCALE))
    got = {
        "text": text[:PRPROJ_MAX_TEXT_CHARS],
        "font": prproj_font_id(font_name),
        "size_px": round(size_px, 2),
        "opacity": style["opacity"],
    }
    if "left" in style:
        # LEFT EDGE → CENTRE. See `PRPROJ_TEXT_SIZE_PER_SCALE`.
        half = (len(got["text"]) * size_px * PRPROJ_TEXT_ASPECT) / 2.0
        got["x"] = round(style["left"] + half / PRPROJ_TEXT_FRAME_W, 4)
        # ⚠ AND BASELINE → CENTRE ON THE OTHER AXIS, for the same reason: what is
        # stored is an EDGE of the lettering and what this app draws from is the
        # middle of the block. See `PRPROJ_TEXT_BASELINE_TO_CENTRE`.
        got["y"] = round(
            style["y"]
            - PRPROJ_TEXT_BASELINE_TO_CENTRE * size_px / PRPROJ_TEXT_FRAME_H,
            4,
        )
    return got


# --- THE DROP SHADOW, WHICH *IS* IN PLAIN XML -------------------------------
# ⚠ **PREMIERE WRITES A COLOUR AS A 64-BIT INTEGER, AND THAT IS HOW THE ONE
# COLOUR THIS FORMAT DOES GIVE UP WAS FOUND.** `<StartKeyframe>` on a colour
# parameter reads `…,18374686479671623680,0,0,…`; big-endian that is
# `ff 00 00 00 00 00 00 00`, i.e. FOUR 16-BIT CHANNELS in A,R,G,B order, each
# holding its 8-bit value in the HIGH byte (white = 0xff00, not 0xffff).
#
# It is worth knowing exactly which colours this unlocks, because the fill is
# NOT one of them (see `_prproj_text_style`): `Shadow Color`, `Key Color`,
# `Tint`'s `Map Black To` / `Map White To`. In two real projects — 194 text
# clips between them, two different Premiere versions — **every single text clip
# carried a Drop Shadow in its own component chain**, so this is not a rare
# extra; it is part of how people actually set type in Premiere.
_PRPROJ_SHADOW_MATCH = "AE.ADBE Drop Shadow"

# Premiere's `Opacity` on a drop shadow runs 0…255, not 0…100 like the opacity
# on a clip. Measured: 249.99998 for "98%" and 127.5 for "50%".
PRPROJ_SHADOW_OPACITY_FULL = 255.0

# ⚠ THE TWO APPS MEASURE THE ANGLE FROM DIFFERENT PLACES. Premiere's `Direction`
# is degrees clockwise from STRAIGHT UP; `AnimaticTextClip.shadow_angle` is
# degrees clockwise from RIGHT, where its long-standing default 45 means
# down-and-right. Down-and-right is 135 in Premiere's frame, so the offset is 90.
PRPROJ_SHADOW_ANGLE_OFFSET = 90.0


def prproj_colour(value: str) -> str:
    """Premiere's packed 64-bit colour → `#rrggbb`. '' when it is not one.

    ⚠ NEVER GUESSES. A value that is not a whole number in range comes back as
    the empty string and the caller keeps this app's own colour — an invented
    colour is worse than a default one, because nobody can tell it was invented.
    """
    text = str(value or "").strip().rstrip(".")
    if not text.isdigit():
        return ""
    try:
        packed = int(text)
    except ValueError:
        return ""
    if not 0 <= packed < 1 << 64:
        return ""
    raw = packed.to_bytes(8, "big")
    # a,r,g,b — each 16 bits, the 8-bit value in the high byte
    return "#%02x%02x%02x" % (raw[2], raw[4], raw[6])


def _prproj_shadow(by_id: dict, comp) -> dict:
    """An `AE.ADBE Drop Shadow` component → what this app can draw of it.

    Returns `{}` if nothing usable was read. ⚠ `Softness` IS DELIBERATELY
    DROPPED: this app's shadow is hard-edged (Pillow draws it as an offset copy),
    so carrying a blur radius across would be a number the renderer ignores and
    the user cannot see — see `AnimaticTextClip.shadow`.
    """
    got: dict = {}
    colour = _prproj_param(by_id, comp, "Shadow Color")
    if colour is not None:
        ink = prproj_colour(_prproj_keyframe_value(colour))
        if ink:
            got["shadow_color"] = ink
    opacity = _prproj_param(by_id, comp, "Opacity")
    if opacity is not None:
        try:
            got["shadow_opacity"] = max(0.0, min(1.0, float(
                _prproj_keyframe_value(opacity).rstrip("."))
                / PRPROJ_SHADOW_OPACITY_FULL))
        except ValueError:
            pass
    direction = _prproj_param(by_id, comp, "Direction")
    if direction is not None:
        try:
            got["shadow_angle"] = (
                float(_prproj_keyframe_value(direction).rstrip("."))
                - PRPROJ_SHADOW_ANGLE_OFFSET
            ) % 360.0
        except ValueError:
            pass
    distance = _prproj_param(by_id, comp, "Distance")
    if distance is not None:
        try:
            got["shadow_px"] = max(0.0, float(
                _prproj_keyframe_value(distance).rstrip(".")))
        except ValueError:
            pass
    # ⚠ "SHADOW ONLY" MEANS THE LETTERS ARE NOT DRAWN AT ALL. This app has no
    # such mode, and importing the shadow without its text would put a smear on
    # the timeline where a title should be — so the whole shadow is dropped and
    # the title comes in plain, which is the readable half of what was there.
    only = _prproj_param(by_id, comp, "Shadow Only")
    if only is not None and _prproj_keyframe_value(only).lower() == "true":
        return {}
    return got
def _prproj_graphic(by_id: dict, objid: str) -> dict:
    """A track item → what its Premiere GRAPHIC actually is, if it is one.

    `{}` for an ordinary clip; otherwise `{"kind": "text"|"shape", "texts": […]}`
    where each entry is `{text, font, size_px, opacity, x, y}`.

    ⚠ **ONE PREMIERE GRAPHIC CAN HOLD SEVERAL TEXT LAYERS** — that is what an
    `AE.ADBE Graphic Group` is, and it is how anybody builds a title with a
    subtitle under it. The first version of this returned the FIRST text
    component it reached and stopped; in the reference project one clip's group
    began with an EMPTY layer, so that clip imported as no text at all while its
    words sat in the very next component. Every layer at the shallowest level is
    read now, and an empty one is skipped rather than treated as an answer.

    ⚠ SHALLOWEST LEVEL ONLY, and for a sharper reason than tidiness. A clip's own
    component chain hangs off the TRACK ITEM, while the MASTER clip behind it
    carries a chain of its own holding the text it was FIRST made with. In the
    reference project all 82 captions share ONE master clip — so a walk that kept
    descending returns the same wrong sentence 82 times, which is precisely the
    shape of bug that looks like it works. `found` freezes the depth as soon as
    anything is found and nothing deeper is read.
    """
    seen = {objid}
    queue = [(objid, 0)]
    texts: list = []
    shapes = 0
    shadow: dict = {}
    # Where the CLIP has been moved to, summed over Motion and the Transform
    # effect — see `_prproj_placement`. Collected during the walk and applied at
    # the end, because a component chain lists its components in whatever order
    # it likes and the text may well be reached first.
    offset = [0.0, 0.0]
    found = None
    while queue:
        current, depth = queue.pop(0)
        if found is not None and depth > found:
            break
        el = by_id.get(current)
        if el is None or depth > PRPROJ_MAX_DEPTH:
            continue
        match = (el.findtext("MatchName") or "").strip()
        if match == _PRPROJ_SHAPE_MATCH:
            found = depth if found is None else found
            shapes += 1
            continue
        if match in (_PRPROJ_MOTION_MATCH, _PRPROJ_GEOMETRY_MATCH):
            # ⚠ NOT `found`. A Motion component is on EVERY clip in the project,
            # so letting one freeze the walk's depth would stop it before it ever
            # reached the lettering — the same trap the shadow below avoids.
            dx, dy = _prproj_placement(by_id, el)
            offset[0] += dx
            offset[1] += dy
            continue
        if match == _PRPROJ_SHADOW_MATCH:
            # ⚠ THE SHADOW BELONGS TO THE CLIP, NOT TO ONE LAYER OF IT. It sits
            # beside the text components in the same chain, so it is collected
            # here and applied to every caption the clip holds. `found` is NOT
            # set by a shadow on its own: a clip with a shadow and no lettering
            # is not a title, and letting it freeze the depth would stop the
            # walk before it reached the words.
            shadow = shadow or _prproj_shadow(by_id, el)
            continue
        if match == _PRPROJ_TEXT_MATCH:
            caption = _prproj_text_component(by_id, el)
            if caption:
                found = depth if found is None else found
                texts.append(caption)
            continue
        for child in el.iter():
            ref = _prproj_ref(child)
            if not ref or ref in seen:
                continue
            target = by_id.get(ref)
            if target is None or _prproj_is_timeline(target.tag):
                continue
            seen.add(ref)
            queue.append((ref, depth + 1))
    if texts:
        if shadow:
            # ⚠ THE DISTANCE BECOMES AN `em`, WHICH IS WHY IT IS APPLIED HERE
            # AND NOT IN `_prproj_shadow`. Premiere writes the offset in PIXELS
            # and `AnimaticTextClip.shadow` is a fraction of the FONT SIZE, so
            # the conversion needs the caption's own size — and two captions on
            # one clip can be set at different sizes.
            for item in texts:
                for key, value in shadow.items():
                    if key == "shadow_px":
                        continue
                    item[key] = value
                if "shadow_px" in shadow and item.get("size_px"):
                    item["shadow"] = round(
                        max(0.0, min(0.5, shadow["shadow_px"] / item["size_px"])), 4
                    )
        # ⚠ WHERE THE CLIP WAS PUT, ADDED TO WHERE THE WORDS SIT INSIDE IT.
        # Applied per caption rather than once, because one graphic can hold a
        # title and a subtitle at different heights and the clip's own move
        # applies equally to both. Clamped to the range `AnimaticTextClip`
        # accepts — a caption Premiere parked off-screen must not fail the whole
        # import, and -1..2 still leaves it draggable back into frame.
        if offset[0] or offset[1]:
            for item in texts:
                if "x" not in item:
                    continue
                item["x"] = round(max(-1.0, min(2.0, item["x"] + offset[0])), 4)
                item["y"] = round(max(-1.0, min(2.0, item["y"] + offset[1])), 4)
        # ⚠ TEXT WINS OVER SHAPE when a graphic holds both — which is the usual
        # lower third: a coloured bar with words on it. The words are the part
        # this app can reproduce exactly; the bar is the part it approximates.
        return {"kind": "text", "texts": texts, "shapes": shapes}
    if shapes:
        return {"kind": "shape", "texts": [], "shapes": shapes}
    return {}


def _prproj_transform(by_id: dict, objid: str) -> dict:
    """One clip's move, zoom and fade — the STATIC values and the ANIMATION.

    ⚠ **THIS IS THE HALF THE READER USED TO THROW AWAY.** `_prproj_keyframe_value`
    read `<StartKeyframe>` and stopped, so a clip that pushed in over four seconds
    imported frozen at its first frame, and `Scale` was not looked at anywhere at
    all. In the reference project that is 21 shots with a keyframed
    position-and-zoom and 78 captions that fade — reported as *"Motion ka Scale,
    aur pehle keyframe ke baad kuch bhi, abhi nahi padha jaata"*.

    Returns `{"x", "y", "opacity", "scale", "tracks", "shared"}` — offsets and
    factors, with `tracks` holding raw `(ticks, value)` rows in the clip's own
    SOURCE clock. `prproj_transform_keys` is what turns those into this app's
    keyframes; the split is deliberate, because rebasing needs the clip's
    in-point and its length and this function has neither.

    ⚠ **THE SHALLOWEST DEPTH WINS AND THEN THE WALK STOPS.** A clip's own
    components hang off the track item, while the MASTER clip behind it carries a
    chain of its own — the same trap `_prproj_graphic` documents, where 82
    captions all came back with one master's text. A Motion component exists on
    every clip in the project, so a walk that kept descending would sum this
    clip's transform with the bin's.

    ⚠ **ONE COMPONENT OWNS EACH ANIMATED PROPERTY; THE REST CONTRIBUTE THEIR
    RESTING VALUE.** Motion and a Transform effect can both animate Scale, and
    merging two curves on different time bases is a second interpolator to get
    wrong. In the reference project no clip animates the same property twice, so
    the first one found is taken and `shared` counts the rest for a warning.
    """
    got = {"x": 0.0, "y": 0.0, "opacity": 1.0, "scale": 1.0, "scale_rest": 1.0,
           "tracks": {}, "shared": 0}
    seen = {objid}
    queue = [(objid, 0)]
    found = None
    comps: list = []
    while queue:
        current, depth = queue.pop(0)
        if found is not None and depth > found:
            break
        el = by_id.get(current)
        if el is None or depth > PRPROJ_MAX_DEPTH:
            continue
        if (el.findtext("MatchName") or "").strip() in _PRPROJ_TRANSFORM_MATCHES:
            found = depth if found is None else found
            comps.append(el)
            continue
        for child in el.iter():
            ref = _prproj_ref(child)
            if not ref or ref in seen:
                continue
            target = by_id.get(ref)
            if target is None or _prproj_is_timeline(target.tag):
                continue
            seen.add(ref)
            queue.append((ref, depth + 1))

    def number(text: str, fallback: float) -> float:
        try:
            return float((text or "").rstrip("."))
        except ValueError:
            return fallback

    for comp in comps:
        # --- where it sits ---------------------------------------------------
        dx, dy = _prproj_placement(by_id, comp)
        got["x"] += dx
        got["y"] += dy
        position = _prproj_param(by_id, comp, "Position")
        rows = _prproj_keyframe_rows(position) if position is not None else []
        if rows:
            # ⚠ THE ANCHOR IS TAKEN AT REST EVEN WHEN IT MOVES. An animated
            # Anchor Point is a second curve on the same property and Premiere
            # composes them; taking the resting one keeps the shape of the move
            # and is wrong only by however far the anchor itself travelled — a
            # far smaller error than dropping the move altogether.
            anchor = _prproj_point(_prproj_param(by_id, comp, "Anchor Point")) or (0.5, 0.5)
            for axis, index in (("x", 0), ("y", 1)):
                if axis in got["tracks"]:
                    got["shared"] += 1
                    continue
                track = []
                for ticks, value in rows:
                    if ":" not in value:
                        continue
                    try:
                        point = [float(v) for v in value.split(":", 1)]
                    except ValueError:
                        continue
                    track.append((ticks, point[index] - anchor[index]))
                if track:
                    got["tracks"][axis] = track

        # --- how big it is ---------------------------------------------------
        scale = _prproj_scale_param(by_id, comp)
        if scale is not None:
            resting = number(_prproj_keyframe_value(scale), 100.0) / 100.0
            got["scale"] *= resting
            rows = _prproj_keyframe_rows(scale)
            if rows and "scale" in got["tracks"]:
                got["shared"] += 1
            elif rows:
                got["tracks"]["scale"] = [
                    (ticks, number(value, 100.0) / 100.0) for ticks, value in rows
                ]
                # ⚠ **WHICH COMPONENT'S RESTING VALUE THE TRACK REPLACES.**
                # `scale` above is the PRODUCT of every component's resting value
                # — Motion's 114.77 ("Set to Frame Size") times a hand-added
                # Transform's 100 — and exactly one of those factors is the one
                # the keyframes overwrite. Without this the animated clip's real
                # size cannot be rebuilt: `prproj_scale_base` divides it back out
                # so the track can be multiplied in, which for the reference
                # project is the difference between a slide that zooms 80 → 100
                # (what Premiere shows) and one that zooms 100 → 125.
                got["scale_rest"] = resting

        # --- and whether you can see it --------------------------------------
        opacity = _prproj_param(by_id, comp, "Opacity")
        if opacity is not None:
            got["opacity"] *= number(_prproj_keyframe_value(opacity), 100.0) / 100.0
            rows = _prproj_keyframe_rows(opacity)
            if rows and "opacity" in got["tracks"]:
                got["shared"] += 1
            elif rows:
                got["tracks"]["opacity"] = [
                    (ticks, number(value, 100.0) / 100.0) for ticks, value in rows
                ]
    return got


def prproj_scale_base(transform: dict, source, frame) -> float | None:
    """Premiere's `Scale` → this app's `scale`, or None when it cannot be had.

    ⚠ **THIS IS THE SUM THAT USED TO BE CALLED IMPOSSIBLE, AND THE MISSING TERM
    WAS THE FILE'S OWN PIXEL SIZE.** Premiere measures Scale against the source;
    this app fits every picture to the frame first and then multiplies. So the
    two numbers only meet through how much of the FRAME the picture covers:

        Premiere  width fraction = source_w × scale / frame_w
        here      width fraction = our_scale × min(1, source_aspect/frame_aspect)

    Setting those equal is the whole function. It is not a guess — on the
    reference project it lands a 1672×941 slide at `Scale 114.77` on exactly
    1.0 (Premiere's "Set to Frame Size" and this app's fit ARE the same thing,
    which is why nobody noticed), 1280×720 footage at `Scale 150` on 1.0, and
    the 1920×309 logo at `Scale 24` on 0.24 — which is the bug that was
    reported: *"logo sahi se set nahi hua"*, a letterhead 4× too wide with its
    left half off the screen.

    ⚠ **THE FIT IS ASSUMED TO BE "CONTAIN" AND THE FRAME TO BE PREMIERE'S OWN.**
    Both are this app's defaults and a user is importing their own cut into a
    project shaped like it. A project set to "cover", or to a different shape
    from the sequence, is already reframing every clip — the arithmetic below is
    then approximate in the same way the rest of that import is.

    ⚠ **AN UNTOUCHED `Scale` (exactly 100) IS LEFT ALONE — AND THAT IS A GUARD,
    NOT AN OVERSIGHT.** Premiere has two ways to make a small file fill a frame:
    *Set to Frame Size* writes the fitting number into Scale (114.77 above), and
    *Scale to Frame Size* resamples the media and leaves Scale at 100. Nothing
    in the project file tells the two apart from a clip nobody ever touched. So
    a Scale of exactly 100 keeps this app's fit-to-frame — which is what it has
    always done, and what BOTH of those Premiere clips look like — and only a
    Scale somebody moved is carried across.

    @param transform  from `_prproj_transform`; `scale` is the product of every
                      component's resting value and `scale_rest` is the one
                      factor a keyframe track replaces, so the return multiplies
                      cleanly by that track — see `prproj_transform_keys`.
    @param source     the file's own `(width, height)`, from `_prproj_detail`.
    @param frame      the sequence's `(width, height)`, from the same place.
    """
    if not source or not frame:
        return None
    resting = float(transform.get("scale") or 1.0)
    if abs(resting - 1.0) <= 1e-6:
        return None
    owner = float(transform.get("scale_rest") or 1.0)
    if owner <= 0:
        return None
    source_w, source_h = source
    frame_w, frame_h = frame
    if min(source_w, source_h, frame_w, frame_h) <= 0:
        return None
    # What "contain" alone gives this picture, as a fraction of the frame's
    # width. Mirrors `place_picture` in animatic_render.py.
    fitted = min(1.0, (source_w / source_h) / (frame_w / frame_h))
    if fitted <= 0:
        return None
    return (source_w / frame_w) / fitted * resting / owner


# ⚠ MIRRORS `AnimaticFrame` IN `server/schemas.py`, for the same reason
# `IMPORT_MAX_CLIP_MS` does further down: a keyframe value outside these reaches
# the user as a 500 with no message and the whole import lost. `scale` is `gt=0`
# there, so the floor here is a small positive number rather than zero — a clip
# scaled to nothing is a clip Pydantic refuses.
# ⚠ These are the values a KEY may hold. The clip's own resting `x`/`y` accept a
# wider range (-2..3); the narrower one is used for both because a keyframe that
# has to be clamped has already lost the shape of the move.
_IMPORT_RANGES = {
    "scale": (0.01, 10.0),
    "x": (-2.0, 3.0),
    "y": (-2.0, 3.0),
    "opacity": (0.0, 1.0),
}


def _at_ticks(track: list, ticks: int) -> float:
    """One raw track sampled at a tick, holding outside its ends."""
    if ticks <= track[0][0]:
        return track[0][1]
    if ticks >= track[-1][0]:
        return track[-1][1]
    for (at, av), (bt, bv) in zip(track, track[1:]):
        if at <= ticks <= bt:
            span = bt - at
            return av if span <= 0 else av + (bv - av) * (ticks - at) / span
    return track[-1][1]


def prproj_transform_keys(
    transform: dict, in_ticks, length_ms: int, scale_base: float | None = None
) -> dict:
    """A clip's raw transform → this app's `keyframes`, in ms from its start.

    Returns `{"keyframes": {...}, "dropped": [...], "outside": [...]}` —
    `dropped` is what could not be placed at all, `outside` what landed entirely
    before or after its own clip and therefore holds at one value for the whole
    of it. Both are counted in `warnings`: the second is FAITHFUL (Premiere shows
    the same held value) and is still the kind of thing somebody should be told,
    because a caption whose fade-out finished before its own first frame is
    invisible here exactly as it is there, and that looks like a lost caption.

    ⚠ **THE TIMES ARE IN THE CLIP'S SOURCE CLOCK AND THE IN-POINT IS THE ZERO.**
    Verified against a real project: four clips at 0.0s, 3.6s, 7.7s and 9.5s on
    the timeline all carry keys at exactly the same ticks (≈3599.98s), and all
    four share an `<InPoint>` of ≈3599.97s — so the keys are 13ms and 11.0s into
    each clip, which is what a preset dropped on four clips means. Rebasing
    against the TIMELINE position instead would have put every one of them an
    hour past the end of its own clip.

    ⚠ **AND IF THE ANSWER IS ABSURD, THE TRACK IS DROPPED RATHER THAN USED.** The
    in-point is the one number here that can be missing, and a wrong zero does
    not fail — it silently parks every key an hour away, where the value simply
    HOLDS and the clip looks un-animated while the project carries a hundred
    meaningless keys. A track none of whose keys land anywhere near the clip is
    refused, and named, which is the difference between a gap and a lie.

    ⚠ **SCALE HAS TWO MODES AND `scale_base` PICKS BETWEEN THEM.**

      GIVEN ONE (the ordinary case now): the track is Premiere's real size,
        converted — see `prproj_scale_base`, which needs the file's own pixel
        count and the sequence's, both of which `_prproj_detail` reads.
      GIVEN NONE: the old behaviour, and still the right answer when the pixel
        sizes are not in the file or the clip's Scale was never touched. Premiere
        measures Scale against the source's own pixels while this app FITS every
        picture to the frame, so without the source size the NUMBER cannot be
        carried across at all — but how much it CHANGES over the clip can, so the
        track is divided by its own value at the clip's start and a push from 80
        to 100 arrives as 1.0 → 1.25.
    """
    out: dict = {}
    dropped: list = []
    outside: list = []
    base = int(in_ticks or 0)
    # How far outside its own clip a key may land before the base is not believed.
    # Generous on purpose: a preset's keys routinely run past a clip that was
    # trimmed short (in the reference project, an 11s zoom on a 3.6s clip).
    window = max(int(length_ms), 0) + 60_000

    for prop, track in (transform.get("tracks") or {}).items():
        if not track:
            continue
        keys = [
            (int(round((ticks - base) * 1000.0 / PRPROJ_TICKS_PER_SECOND)), value)
            for ticks, value in track
        ]
        if not any(-window <= t <= window for t, _ in keys):
            dropped.append(prop)
            continue
        if prop == "scale" and scale_base is not None:
            # ⚠ THE REAL SIZE, because `prproj_scale_base` found the file's own
            # pixel count and could therefore convert Premiere's number into
            # this app's. `scale_base` already carries the resting value the
            # track replaces divided back out, so multiplying is all that is
            # left: a slide whose Transform runs 80 → 100 under a Motion of
            # 114.77 arrives as 0.80 → 1.00, which is what Premiere shows.
            keys = [(t, scale_base * value) for t, value in keys]
        elif prop == "scale":
            # The value at the clip's own start, which is what 1.0 must mean.
            reference = _at_ticks(track, base)
            if reference <= 0:
                dropped.append(prop)
                continue
            keys = [(t, value / reference) for t, value in keys]
        elif prop in ("x", "y"):
            # An OFFSET becomes a position: 0.5 is the middle of the frame.
            keys = [(t, 0.5 + value) for t, value in keys]

        lo, hi = _IMPORT_RANGES[prop]
        rounded = [
            {"t": t, "v": round(max(lo, min(hi, value)), 4), "ease": "linear"}
            for t, value in keys
        ]
        # A track that never actually changes is not an animation — it is a
        # static value wearing keyframes, and writing it would put a diamond row
        # on the timeline for every clip in the film.
        if len({key["v"] for key in rounded}) < 2:
            continue
        if not any(0 <= key["t"] <= max(int(length_ms), 0) for key in rounded):
            outside.append(prop)
        out[prop] = rounded
    return {"keyframes": out, "dropped": dropped, "outside": outside}


def _prproj_detail(by_id: dict, objid: str) -> dict:
    """One track item, followed through the graph until its facts are found.

    BREADTH-FIRST ON PURPOSE. The same tag appears at several depths — a clip has
    a `<Name>`, so does the master clip behind it, and so does the media file
    behind that — and the SHALLOWEST one is the one belonging to this clip. A
    depth-first walk would come back with the name of the file rather than the
    name of the clip roughly at random.
    """
    got = {"start": None, "end": None, "in": None, "name": "", "path": "",
           "enabled": True, "frame": None, "source": None}
    seen = {objid}
    queue = [(objid, 0)]
    while queue:
        current, depth = queue.pop(0)
        el = by_id.get(current)
        if el is None or depth > PRPROJ_MAX_DEPTH:
            continue
        tag = el.tag

        # ⚠ THE TWO FRAME SIZES, AND THEY ARE TOLD APART BY TAG RATHER THAN BY
        # DEPTH. Both are `<FrameRect>` and the walk meets several of each, so
        # "the shallowest wins" — the rule the rest of this function runs on —
        # would answer the sequence's size for both. The clip item's own rect IS
        # the sequence frame; a `VideoStream`'s is the file's own pixels. See
        # `_prproj_rect`, and `prproj_scale_base` for what they are for.
        if got["frame"] is None and tag.endswith("ClipTrackItem"):
            got["frame"] = _prproj_rect(el)
        if got["source"] is None and tag == "VideoStream":
            got["source"] = _prproj_rect(el)

        # The place on the timeline — see `_prproj_times`, which reads it out of
        # the clip's own nested `<TrackItem>`. It is tried on every object the
        # walk reaches rather than only the first, so a file that DOES keep its
        # `TrackItem` as a separate referenced object still works.
        if got["start"] is None:
            start, end = _prproj_times(el)
            if end is not None:
                got["start"], got["end"] = start, end
        if tag.endswith("TrackItem"):
            disabled = (el.findtext("Disabled") or "").strip().lower()
            if disabled in ("true", "1"):
                got["enabled"] = False

        # The source window — how far into the file this clip starts — is on the
        # `Clip`, NESTED one level inside it. See `_prproj_in_point`: reading it
        # as a direct child answered None for every clip in a real project, and
        # a soundtrack razored into 23 pieces played its first seconds 23 times.
        # `<OutPoint>` is deliberately not read: the timeline start and end
        # already give the LENGTH, and a second opinion about it is a second
        # chance to be wrong.
        if got["in"] is None and tag.endswith("Clip"):
            got["in"] = _prproj_in_point(el)

        if not got["name"]:
            got["name"] = (el.findtext("Name") or "").strip()
        if not got["path"]:
            for path_tag in _PRPROJ_PATH_TAGS:
                found = (el.findtext(path_tag) or "").strip()
                if _prproj_looks_like_path(found):
                    got["path"] = found
                    break

        for child in el.iter():
            ref = _prproj_ref(child)
            if not ref or ref in seen:
                continue
            target = by_id.get(ref)
            if target is None or _prproj_is_timeline(target.tag):
                continue
            seen.add(ref)
            queue.append((ref, depth + 1))
    return got


def _prproj_walk(by_id: dict, root_ref: str):
    """One sequence's graph → its track items in order, each with its track.

    Returns `(items, lanes)` — `items` as `(tag, track_key, object_id)` in
    document order, `lanes` as the track keys in the order they were reached, so
    row 1 of the sequence stays row 1 here.
    """
    items: list = []
    lanes: list = []
    seen: set = set()

    def visit(objid, track, depth: int) -> None:
        if depth > PRPROJ_MAX_DEPTH or objid in seen:
            return
        el = by_id.get(objid)
        if el is None:
            return
        seen.add(objid)
        tag = el.tag
        if tag.endswith("ClipTrackItem") or tag.endswith("TransitionTrackItem"):
            items.append((tag, track, objid))
            return
        # ⚠ THE INNERMOST `…Track` WINS. Premiere nests them — a `VideoClipTrack`
        # holding a `ClipTrack` — so taking the outer one would fold every row of
        # the sequence into one. Each real row still ends at exactly one
        # innermost track object; the outer ones collect no clips and are dropped
        # at the end for being empty.
        if tag.endswith("Track"):
            track = objid
            if track not in lanes:
                lanes.append(track)
        for child in el.iter():
            ref = _prproj_ref(child)
            if ref:
                visit(ref, track, depth + 1)

    visit(root_ref, None, 0)
    return items, lanes


def _prproj_rate_of(el) -> float:
    """The fps of a sequence, worked back from its ticks-per-frame. EXACT.

    Premiere stores the RATE as the length of one frame in ticks, so 24fps is
    10,584,000,000. ⚠ **THE EXACT VALUE IS RETURNED, NOT THE ROUNDED ONE**, so
    the caller can tell 24 from 23.976 — a real project turned out to be
    23.976, and rounding it away silently is how a long cut drifts.

    ⚠ AND `<FrameRate>` IS ALSO WHAT AUDIO USES. The same tag carries 44100 and
    48000 on the sound streams, so anything outside a sane picture rate is
    ignored rather than believed.
    """
    if el is None:
        return 0.0
    for tag in ("VideoFrameRate", "FrameRate", "VideoTimebase", "Timebase"):
        for node in el.iter(tag):
            try:
                ticks = int(float((node.text or "").strip()))
            except (TypeError, ValueError):
                continue
            if ticks > 0:
                fps = PRPROJ_TICKS_PER_SECOND / ticks
                if 1 <= fps <= 240:
                    return fps
    return 0.0


def _prproj_rate_near(by_id: dict, seq_el, max_depth: int = 6) -> float:
    """The frame rate belonging to THIS sequence, found by following its refs.

    ⚠ **NOT A DOCUMENT-WIDE SEARCH, AND THAT IS THE WHOLE POINT.** Premiere does
    not keep the rate on the sequence: it lives on a track-group object several
    hops away, so looking only at the sequence and the objects it points straight
    at finds nothing. Falling back to "the first rate anywhere in the file" then
    answers with whichever sequence happens to come FIRST — so a project holding a
    30fps sequence above the 24fps one being imported reads every clip 25% adrift,
    with nothing on screen saying why. That is the exact class of fault E41 and
    E46 exist about, and it is silent, so the search is bounded to this
    sequence's own subgraph and another `Sequence` is never crossed into.

    Clip items are not descended into either — there is no rate down there and
    there are thousands of them.
    """
    if seq_el is None:
        return 0.0
    found = _prproj_rate_of(seq_el)
    if found:
        return found
    seen = {seq_el.get("ObjectID")}
    queue = [(seq_el, 0)]
    while queue:
        el, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for child in el.iter():
            ref = _prproj_ref(child)
            if not ref or ref in seen:
                continue
            seen.add(ref)
            target = by_id.get(ref)
            if target is None or target.tag == "Sequence":
                continue
            if target.tag.endswith("TrackItem"):
                continue
            rate = _prproj_rate_of(target)
            if rate:
                return rate
            queue.append((target, depth + 1))
    return 0.0


def _read_prproj(data: bytes, fps_hint: int) -> dict:
    """A Premiere Pro project file → the neutral incoming model. Best-effort.

    Read the section header above before changing anything here. In one line:
    this is the only reader in the file with no specification behind it, so it
    fails LOUDLY and PARTIALLY rather than quietly and completely.
    """
    if (data or b"")[:2] == b"\x1f\x8b":
        raw = _gunzip_capped(data)
    else:
        # ⚠ NOT ALWAYS COMPRESSED. Premiere has a "save uncompressed" preference,
        # and plenty of asset pipelines unpack the file on the way through, so
        # the gzip header is a hint and not a requirement.
        raw = data or b""
        if len(raw) > PRPROJ_MAX_XML_BYTES:
            raise ImportRefused("That .prproj is larger than this can read.")

    try:
        root = ET.fromstring(raw.decode("utf-8", "replace"))
    except ET.ParseError as exc:
        raise ImportRefused(
            f"That .prproj could not be read as XML ({exc}). In Premiere: "
            "File › Export › Final Cut Pro XML, and bring that .xml instead."
        ) from exc

    # ⚠ THE FIRST WARNING IS THE POINT OF THE WHOLE FEATURE. Everything else this
    # module produces can be trusted; this cannot, and the sentence saying so
    # travels with the import all the way to the dialog.
    warnings = [
        "This was read from a .prproj — Premiere's private save file, whose "
        "structure Adobe has never published. It is a BEST-EFFORT read: check "
        "every clip against Premiere before you trust it. The route that always "
        "works is File › Export › Final Cut Pro XML.",
        # ⚠ "TITLES" USED TO BE IN THIS LIST AND IT WAS NOT TRUE. A Premiere
        # title carries its words, its font, its size and its position in plain
        # reach of this reader — see the LETTERING section. What is genuinely out
        # of reach is everything Premiere keeps in a binary it does not put in
        # the project XML, and the fill colour of a title is one of those.
        "Only the CUT and the LETTERING were read. Effects, colour (including "
        "the colour of a title), speed changes, audio levels and nested "
        "sequences were not — they cannot be read out of this format with any "
        "confidence.",
    ]
    if root.tag != "PremiereData":
        warnings.append(
            "That file did not start the way a Premiere project usually does, so "
            "even less of it may be right than usual."
        )

    by_id = _prproj_index(root)
    if not by_id:
        raise ImportRefused(
            "Nothing inside that .prproj looked like a Premiere project. In "
            "Premiere: File › Export › Final Cut Pro XML, and bring that instead."
        )

    # --- which timeline -----------------------------------------------------
    # ⚠ **A REAL PROJECT HAS NO `<Sequence>` OBJECT IN IT AT ALL.** The first
    # version looked for one, found none, and dropped straight to the flat route
    # on a file whose tracks were perfectly readable. What a sequence actually IS
    # here is a `VideoTrackGroup` and an `AudioTrackGroup` — each holding
    # `<Tracks>`, each `<Track>` pointing at a `VideoClipTrack` BY `ObjectURef`,
    # and that track listing its clips by `ObjectRef`. `<Sequence>` is still
    # tried first in case another Premiere version writes one.
    #
    # ⚠ A PROJECT HOLDS MANY TIMELINES AND THIS IMPORTS ONE. Taking every clip in
    # the file would stack every sequence on top of the others at frame zero, so
    # the busiest is the guess and the user is told one was chosen.
    def _key(el):
        return el.get("ObjectID") or el.get("ObjectUID") or ""

    def _busiest(roots):
        """The root with the most CLIPS, and what walking it found."""
        best_el, best_items, best_lanes = None, [], []
        for candidate in roots:
            found, found_lanes = _prproj_walk(by_id, _key(candidate))
            clips = [i for i in found if i[0].endswith("ClipTrackItem")]
            if len(clips) > len([i for i in best_items if i[0].endswith("ClipTrackItem")]):
                best_el, best_items, best_lanes = candidate, found, found_lanes
        return best_el, best_items, best_lanes

    sequences = [el for el in root.iter() if el.tag == "Sequence" and _key(el)]
    if sequences:
        best, items, lanes = _busiest(sequences)
        timelines = len(sequences)
    else:
        # ⚠ PICTURE AND SOUND ARE SEPARATE ROOTS HERE, so each is chosen on its
        # own and the two are merged. `DataTrackGroup` holds captions and markers
        # and is deliberately left alone.
        groups = [el for el in root.iter() if el.tag.endswith("TrackGroup") and _key(el)]
        video_root, video_items, video_lanes_ = _busiest(
            [g for g in groups if g.tag.startswith("Video")])
        audio_root, audio_items, audio_lanes_ = _busiest(
            [g for g in groups if g.tag.startswith("Audio")])
        best = video_root if video_root is not None else audio_root
        items = video_items + audio_items
        lanes = video_lanes_ + audio_lanes_
        timelines = max(
            len([g for g in groups if g.tag.startswith("Video")]),
            len([g for g in groups if g.tag.startswith("Audio")]),
        )
        # ⚠ SAID OUT LOUD, because picture and sound were chosen SEPARATELY: with
        # nothing in the file tying a picture group to its sound group, two
        # sequences in one project can in principle contribute a row each.
        if video_root is not None and audio_root is not None and timelines > 1:
            warnings.append(
                "This project holds more than one sequence, and nothing in the "
                "file says which sound belongs to which picture — the busiest of "
                "each was taken. Check that the sound matches the cut."
            )
    # ⚠ ONLY WHEN ONE WAS ACTUALLY CHOSEN. If nothing yielded a clip the flat
    # route runs instead, and saying "the one with the most clips was taken"
    # would describe something that did not happen.
    if timelines > 1 and best is not None and sequences:
        warnings.append(
            f"That project holds {timelines} sequences and only one can be "
            "imported; the one with the most clips was taken."
        )

    route = "structured" if items else "flat"
    if not items:
        # ⚠ THE FALLBACK IS WORTH HAVING EVEN THOUGH IT LOSES THE ROWS. A cut
        # whose clips are all on one row is a few minutes of dragging; a refusal
        # is the whole edit rebuilt by hand.
        warnings.append(
            "The track layout in that .prproj could not be recognised, so every "
            "clip was put on one row in file order. The clips, their lengths and "
            "their order are right; which row each was on is not."
        )
        # ⚠ THE OUTERMOST WRAPPER ONLY, OR EVERY CLIP ARRIVES TWICE. Premiere
        # builds a clip as `VideoClipTrackItem` → `ClipTrackItem` → `TrackItem`,
        # and BOTH of the first two end in "ClipTrackItem" — so a plain scan for
        # that suffix finds each clip twice and puts the second copy on the wrong
        # row (the inner one has no `Video`/`Audio` prefix to sort it by). The
        # prefixed wrapper is the clip; the bare tag is only taken when a file
        # turns out to have no prefixed ones at all.
        wrappers = [
            el for el in root.iter()
            if el.get("ObjectID")
            and (el.tag.endswith("ClipTrackItem") or el.tag.endswith("TransitionTrackItem"))
            and el.tag not in ("ClipTrackItem", "TransitionTrackItem")
        ]
        if not wrappers:
            wrappers = [
                el for el in root.iter()
                if el.get("ObjectID") and el.tag == "ClipTrackItem"
            ]
        for el in wrappers:
            items.append((el.tag, None, el.get("ObjectID")))
    if not items:
        raise ImportRefused(
            "No clips could be found in that .prproj. In Premiere: File › Export "
            "› Final Cut Pro XML, and bring that .xml here instead."
        )

    # --- the rate -----------------------------------------------------------
    # ⚠ THE CHOSEN SEQUENCE'S OWN RATE FIRST, and both fallbacks below say so.
    # A wrong rate does not look like an error: every clip is simply the wrong
    # length and the whole cut drifts, which is why neither fallback is silent.
    exact = _prproj_rate_near(by_id, best)
    if not exact:
        exact = _prproj_rate_of(root)
        if exact:
            warnings.append(
                f"The frame rate was not on that sequence, so the first one found "
                f"anywhere in the project was used ({exact:.3f} fps). If this "
                f"project holds sequences at different rates, check the clip lengths."
            )
    if exact:
        fps = max(1, int(round(exact)))
        # ⚠ 23.976 AND 29.97 ARE REAL AND THIS PROJECT CANNOT HOLD THEM.
        # `AnimaticSettings.fps` is a whole number, so an NTSC rate is READ as the
        # nearest one and NAMED rather than silently rounded — over a long cut it
        # is about a frame a minute, which is worth knowing and not worth
        # refusing an import over.
        if abs(exact - fps) > 0.001:
            warnings.append(
                f"That sequence is {exact:.3f} fps (NTSC). It was read as {fps} fps, "
                f"because this app's timeline only holds whole numbers \u2014 over a "
                f"long cut expect a frame or two of drift."
            )
    else:
        fps = max(1, int(fps_hint))
        warnings.append(
            f"The frame rate could not be found in that .prproj, so it was read at "
            f"this project's {fps} fps. If the cut looks stretched, that is why."
        )

    # --- the clips ----------------------------------------------------------
    files: dict = {}
    video_lanes: dict = {}
    audio_lanes: dict = {}
    skipped = 0
    transitions_seen = 0
    # What the transform pass found, each one a sentence the report owes the user.
    animated = 0      # clips whose move / zoom / fade came across
    unbased = 0       # tracks whose times landed nowhere near their own clip
    stacked = 0       # a property animated by two components at once
    sized = 0         # a Scale carried across through the file's own pixel size
    fixed_scale = 0   # a Scale with no pixel size to convert it by
    held = 0          # an animation that begins and ends outside its own clip
    for tag, track, objid in items:
        got = _prproj_detail(by_id, objid)
        if got["start"] is None or got["end"] is None or got["end"] <= got["start"]:
            # Nothing usable was found for it. Counted rather than guessed at,
            # because a clip dropped at frame zero is worse than a clip missing.
            skipped += 1
            continue
        is_audio = tag.startswith("Audio")
        bucket = audio_lanes if is_audio else video_lanes
        lane = bucket.setdefault(track or ("a" if is_audio else "v"),
                                 {"clips": [], "transitions": []})
        start = prproj_ticks_to_frames(got["start"], fps)
        end = prproj_ticks_to_frames(got["end"], fps)
        if end <= start:
            skipped += 1
            continue

        if tag.endswith("TransitionTrackItem"):
            # ⚠ EVERY TRANSITION BECOMES A DISSOLVE, because that is the only one
            # this app and every exchange format agree on. `to_project` then
            # matches it to the cut it sits over. Audio transitions are dropped:
            # a fade on a sound has nowhere to go here.
            if not is_audio:
                lane["transitions"].append({"start": start, "end": end})
                transitions_seen += 1
            continue

        name = got["name"] or _basename_of(got["path"]) or "Clip"
        # ⚠ WHAT IS THIS CLIP, REALLY. Premiere calls a title, a caption, a lower
        # third and a drawn rectangle all "Graphic", and gives none of them a
        # file — so by NAME they are indistinguishable from each other and from
        # genuinely missing footage. Only sound is exempt: an audio item has no
        # component chain worth walking, and skipping it keeps this off the hot
        # path for the razored-voiceover case (23 clips of one mp3).
        graphic = {} if is_audio else _prproj_graphic(by_id, objid)
        # Keyed by PATH where there is one, so two clips cut from the same file
        # resolve to the same upload and two different files that happen to share
        # a clip name do not collide.
        key = (got["path"] or name).lower()
        files.setdefault(key, {
            "name": _basename_of(got["path"]) or name,
            "pathurl": got["path"],
        })
        clip = {
            "name": name,
            "file": key,
            "start": start,
            "end": end,
            "in": max(0, prproj_ticks_to_frames(got["in"] or 0, fps)),
            "out": 0,
            "enabled": got["enabled"],
            # Not read — see the second warning. 1.0 is what an untouched clip
            # plays at, which is the honest guess when the real number is unknown.
            "level": 1.0,
        }
        if graphic:
            # ⚠ `graphic` RIDES ALONG; it does not replace anything. A reader
            # that swapped the clip out here would break every consumer that
            # walks `clips` expecting a file — `to_project` is where a caption
            # stops being a clip, because that is the layer that knows what a
            # caption IS in this app.
            clip["graphic"] = graphic
        # ⚠ THE MOVE, THE ZOOM AND THE FADE — see `_prproj_transform`. Audio is
        # exempt for the same reason it skips `_prproj_graphic`: a sound has no
        # transform worth walking, and 23 clips of one razored voiceover is the
        # hot path this reader was slowest on.
        if not is_audio:
            motion = _prproj_transform(by_id, objid)
            length_ms = max(0, int(round((end - start) * 1000.0 / max(1, fps))))
            # ⚠ THE SIZE IT SITS AT, worked out from the file's own pixel count —
            # see `prproj_scale_base`. None means the sum could not be done (the
            # pixel sizes are not in the file) or must not be (an untouched
            # Scale), and the keys then fall back to the relative push.
            scale_base = prproj_scale_base(motion, got["source"], got["frame"])
            keys = prproj_transform_keys(motion, got["in"], length_ms, scale_base)
            if keys["keyframes"]:
                clip["keyframes"] = keys["keyframes"]
                animated += 1
            if keys["dropped"]:
                unbased += len(keys["dropped"])
            if keys["outside"]:
                held += len(keys["outside"])
            if motion["shared"]:
                stacked += motion["shared"]
            if motion["x"] or motion["y"]:
                clip["offset"] = [round(motion["x"], 4), round(motion["y"], 4)]
            if abs(motion["opacity"] - 1.0) > 1e-6:
                clip["opacity"] = max(0.0, min(1.0, motion["opacity"]))
            # ⚠ **THE SIZE THE CLIP SITS AT — AND IT IS TAKEN FROM THE TRACK'S
            # OWN VALUE AT THE CLIP'S START, NOT FROM THE RESTING ONE.** A clip
            # whose Scale is keyframed rests at a value a hundred hours before
            # the film (`_prproj_keyframe_rows` says why), so the resting number
            # is the size it NEVER plays at: on the reference project's slides it
            # is 114.77 while the clip actually opens at 80% of that. The keys
            # carry the movement and this carries what the Properties panel shows
            # — they must agree, or deleting the keyframes jumps the picture.
            if scale_base is not None:
                at_start = 1.0
                track = (motion.get("tracks") or {}).get("scale")
                if track and "scale" not in keys["dropped"]:
                    at_start = _at_ticks(track, int(got["in"] or 0))
                lo, hi = _IMPORT_RANGES["scale"]
                clip["scale"] = round(max(lo, min(hi, scale_base * at_start)), 4)
                sized += 1
            elif abs(motion["scale"] - 1.0) > 1e-6:
                # The sum could not be done — the file's pixel size is not in the
                # project — so the picture stays fitted to the frame. Counted,
                # and named in the warnings.
                fixed_scale += 1
        lane["clips"].append(clip)

    def ordered(bucket: dict) -> list:
        """The lanes in the order the sequence had them, empties dropped."""
        keys = [k for k in lanes if k in bucket]
        keys += [k for k in bucket if k not in keys]
        return [
            bucket[k] for k in keys
            if bucket[k]["clips"] or bucket[k]["transitions"]
        ]

    # ⚠ CLIPS WERE FOUND AND NOT ONE OF THEM COULD BE PLACED. Falling through here
    # hands the route an empty result, and its answer for that is "There was
    # nothing on that timeline to bring in" — which is the wrong sentence twice
    # over: there WAS something, and the reason it did not arrive is this reader
    # giving up, not the file being empty. Refusing here is what puts the true
    # reason in front of the user and points at the door that always works.
    if skipped and not any(l["clips"] for l in list(video_lanes.values()) + list(audio_lanes.values())):
        raise ImportRefused(
            f"{skipped} clip(s) were found in that .prproj but none of their "
            "positions could be read — this is where the best-effort reader gives "
            "up. In Premiere: File › Export › Final Cut Pro XML, and bring that "
            ".xml here instead."
        )
    if skipped:
        warnings.append(
            f"{skipped} item(s) in that .prproj had no position this could read "
            "and were left out."
        )
    if transitions_seen:
        warnings.append(
            f"{transitions_seen} transition(s) were read as cross dissolves — the "
            "shape of a Premiere transition does not survive the trip."
        )
    # ⚠ EVERY ONE OF THESE IS ABOUT THE TRANSFORM PASS, and they are worth the
    # room: an animation that arrives is a surprise, and one that does not is a
    # question the user will otherwise ask this app's author.
    if animated:
        warnings.append(
            f"{animated} clip(s) brought their Motion / Transform animation across "
            "— position, zoom and opacity keyframes. Every curve is read as a "
            "straight line between its keys, so an eased move arrives evenly paced."
        )
    if sized:
        warnings.append(
            f"{sized} clip(s) kept the size they were given in Premiere. Premiere "
            "measures Scale against the file's own pixel size, so that size was "
            "worked out from the pixel count the project file records for each "
            "one — a clip whose file has no size recorded stays fitted to the "
            "frame instead."
        )
    if fixed_scale:
        warnings.append(
            f"{fixed_scale} clip(s) sit at a fixed Scale in Premiere with no pixel "
            "size recorded for their file, so that size could not be converted "
            "and they are shown here fitted to the frame instead."
        )
    if held:
        warnings.append(
            f"{held} animation(s) begin and end outside the clip they are on — "
            "the clip was trimmed away from them — so they hold at one value for "
            "its whole length, which is what Premiere shows too. Worth a look if "
            "something arrives invisible."
        )
    if unbased:
        warnings.append(
            f"{unbased} animation(s) had keyframe times this could not place "
            "against their own clip and were left out rather than guessed at."
        )
    if stacked:
        warnings.append(
            f"{stacked} property(ies) were animated by two effects at once; the "
            "first was read and the second left out."
        )

    return {
        "reader": "prproj",
        # Which layer answered, so a report can say it and a test can pin it.
        "route": route,
        "name": ((best.findtext("Name") or "").strip() if best is not None else "")
        or "Imported Premiere sequence",
        "fps": fps,
        "width": 0,
        "height": 0,
        "files": files,
        "video": ordered(video_lanes),
        "audio": [{"clips": lane["clips"]} for lane in ordered(audio_lanes)],
        "warnings": list(dict.fromkeys(warnings)),
    }


def read_document(
    data: bytes,
    filename: str = "",
    fps_hint: int = 24,
    experimental: bool = False,
) -> dict:
    """One uploaded project file → the neutral incoming model.

    Raises `ImportRefused` with a sentence for the user on anything we will not
    pretend to read.

    @param experimental  open a `.prproj` with the best-effort reader instead of
                         refusing it. ⚠ **OFF BY DEFAULT AND IT MUST STAY THAT
                         WAY.** The refusal names a route that always works
                         (export a Final Cut Pro XML); this flag is the answer
                         for somebody who no longer has Premiere to export from,
                         and every import it produces carries the warning saying
                         so. Defaulting it to True would quietly turn the one
                         guess in this module into the normal path.
    """
    kind = detect_format(data, filename)
    if kind == "prproj" and experimental:
        return _read_prproj(data, fps_hint)
    if kind in REFUSALS:
        raise ImportRefused(REFUSALS[kind])
    text = (data or b"").decode("utf-8", "replace")
    if kind == "fcp7":
        return _read_fcp7(text)
    if kind == "edl":
        return _read_edl(text, fps_hint)
    raise ImportRefused(REFUSALS["unknown"])


# ---------------------------------------------------------------------------
# The incoming model → this app's own clips
# ---------------------------------------------------------------------------
# ⚠ PLACEHOLDERS, NOT SILENCE, FOR MEDIA THAT DID NOT ARRIVE. A clip whose file
# is missing becomes a colour card carrying that clip's NAME and its exact place
# and length. The alternative — leaving it out — hands back a timeline with holes
# in it and no way to see what is missing or where; this way the CUT is whole,
# every gap is labelled, and dropping the real file onto that row fixes it.
# `report["placeholders"]` names every one of them.
MAX_IMPORT_TRACKS = 16

# ⚠ **THESE MIRROR `AnimaticFrame` AND `AnimaticAudio` IN `server/schemas.py`,
# AND A VALUE OUTSIDE THEM IS NOT A BAD CLIP — IT IS A 500 WITH NO MESSAGE.** The
# import route builds those models straight out of what this function returns, so
# anything Pydantic rejects reaches the user as "Internal Server Error" with the
# whole import lost and nothing saying which clip did it.
#
# ⚠ AND THE FILES THAT TRIP IT ARE ORDINARY ONES, not malformed ones. `AnimaticFrame`
# caps a clip at TEN MINUTES because this app makes animatics; somebody else's
# timeline does not know that, and a fifteen-minute interview take, a music bed
# laid across a whole reel, or a record-in that starts before the sequence does
# are all perfectly normal things to find in an XML or an EDL.
#
# So they are CLAMPED here and COUNTED in `warnings` — the clip arrives, in the
# right row, shortened or nudged, and the user is told how many and by what. A
# clip that is present and named is something somebody can fix; a 500 is not.
# ⚠ If the schema's bounds move, move these with them.
IMPORT_MAX_START_MS = 24 * 3_600_000
IMPORT_MAX_CLIP_MS = 600_000
IMPORT_MIN_CLIP_MS = 100


def _fit_clip(start_ms: int, length_ms: int, tally: dict) -> tuple:
    """One clip's place and length, brought inside what the schema will accept.

    Returns `(start_ms, length_ms)` and counts what it had to change in `tally`,
    so the caller can say so out loud. See the note above `IMPORT_MAX_START_MS`
    for why this is a clamp and not a rejection.
    """
    fitted_start = min(max(0, int(start_ms)), IMPORT_MAX_START_MS)
    if fitted_start != start_ms:
        tally["moved"] += 1
    # The floor is not counted: a clip shorter than `IMPORT_MIN_CLIP_MS` is a
    # sub-frame sliver at any sane rate, and rounding it up to a tenth of a
    # second is not news. The CEILING is, because it shortens real footage.
    fitted_length = max(IMPORT_MIN_CLIP_MS, int(length_ms))
    if fitted_length > IMPORT_MAX_CLIP_MS:
        fitted_length = IMPORT_MAX_CLIP_MS
        tally["shortened"] += 1
    return fitted_start, fitted_length


# ---------------------------------------------------------------------------
# WHAT A PREMIERE CLIP BECOMES HERE
# ---------------------------------------------------------------------------
# ⚠ **A PREMIERE ROW IS NOT A PICTURE ROW.** Four of the eight rows in the
# project this was written against carry no film at all — two hold captions, two
# hold a drawn bar, one holds an Adjustment Layer — and the first version of this
# import turned every one of them into a picture row full of clips with no file,
# which is where "audio, image and video show but text not show" came from.
# A clip is sorted HERE, once, and each kind goes to the row of this app that
# actually holds it:
#
#   text        → an `AnimaticTextClip` on a TEXT row     (never the caption row:
#                 that one belongs to ✨ Auto captions, and an import writing
#                 into it would silently overwrite work the user paid for)
#   shape       → an `AnimaticShape` on a SHAPES row
#   adjustment  → NOTHING, and counted so the report can say so
#   picture     → a frame on a picture row, exactly as before
_IMPORT_ADJUSTMENT_NAMES = ("adjustment layer", "adjustment")

# ⚠ THE ONE LAYER ID AN IMPORT MAY NEVER WRITE TO. `CAPTION_LAYER_ID` in the
# editor is the row ✨ Auto captions owns; captions there are the product of a
# paid transcription and are replaced wholesale on every run. An imported
# caption landing in it would be destroyed by the next auto-caption run without
# anybody being told. Import rows are minted under their own prefixes below and
# `tests/interchange_check.py` §8j asserts that none of them is this string.
IMPORT_CAPTION_LAYER_ID = "captions"
IMPORT_TEXT_LANE_PREFIX = "_import_text_"
IMPORT_SHAPE_LANE_PREFIX = "_import_shape_"

# What an imported caption is drawn with when the file does not say — which,
# for the colour, is ALWAYS. See `_prproj_text_style` for the search that
# established the colour is not in a .prproj at all.
IMPORT_TEXT_COLOR = "#ffffff"
IMPORT_TEXT_BACKDROP = "none"


def _import_clip_role(clip: dict, found) -> str:
    """One incoming clip → 'text' | 'shape' | 'adjustment' | 'picture'.

    ⚠ A CLIP THAT RESOLVED TO A FILE IS ALWAYS A PICTURE, whatever else it looks
    like. A real film called "Adjustment Layer.mp4" is somebody's footage, and
    the name of a file is not permission to throw it away.
    """
    if found:
        return "picture"
    kind = (clip.get("graphic") or {}).get("kind") or ""
    if kind in ("text", "shape"):
        return kind
    if str(clip.get("name") or "").strip().lower() in _IMPORT_ADJUSTMENT_NAMES:
        # ⚠ DROPPED ON PURPOSE, and the decision is the user's to revisit. An
        # Adjustment Layer is an empty holder for a colour effect; this app has
        # no such row, and the effects it would have held cannot be read out of a
        # .prproj anyway. Carrying it in as an invisible full-length clip — which
        # is what happened before — put a card over the whole film that did
        # nothing, could not be explained, and had to be found and deleted.
        # If it is ever wanted, this is the line to change: give it a row of its
        # own rather than making it a picture again.
        return "adjustment"
    return "picture"


def _import_text_clips(
    graphic: dict, *, start_ms: int, length_ms: int, layer_id: str, mint,
    keyframes: dict | None = None,
) -> list:
    """One Premiere graphic → the `AnimaticTextClip`s it holds. Usually one.

    ⚠ `place: "free"` AND NOT THE DEFAULT "flow". A flowed caption is dropped
    into a zone and stacked with its neighbours, which is right for a caption
    somebody types here and wrong for one that arrives with a position: two
    titles that Premiere put in opposite corners would stack in the same corner.
    x/y come across as fractions of the frame, which is what both sides already
    speak — see `_prproj_text_component` for how the left edge Premiere stores
    becomes the centre this app wants.

    ⚠ NO COLOUR IS INVENTED. `IMPORT_TEXT_COLOR` is this app's own default, not
    a reading of the file, and `backdrop: "none"` is chosen over the usual scrim
    for the same reason: a scrim is a black bar this app would be ADDING to
    somebody's film. "none" still draws its own outline, so white lettering on
    pale art stays readable.

    @param keyframes  the CLIP's animation, from `_prproj_transform`. ⚠ **ONLY
        `opacity` AND `scale` ARE TAKEN, AND LEAVING `x`/`y` OUT IS THE POINT.**
        A caption's resting position is already the sum of two things — where the
        graphic sits (`_prproj_graphic`'s offset) and where the lettering sits
        INSIDE it (`AE.ADBE Text`'s own Position, which is what E5x's
        middle-of-the-screen fault was about). A position track measured on the
        graphic alone would throw the second half away and jump every caption on
        its first frame. The fade and the pop are the two that belong to the
        whole clip, and in the reference project they are the only two any
        caption animates: 78 of them fade, 78 of them scale, none of them move.
    """
    out: list = []
    for item in graphic.get("texts") or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        clip = {
            "id": mint(),
            "layer_id": layer_id,
            "text": text,
            "start_ms": start_ms,
            "duration_ms": length_ms,
            "font": item.get("font") or animatic_fonts.DEFAULT_FONT,
            "size_px": float(item.get("size_px") or 0.0),
            # ⚠ THE FILE'S COLOUR IF IT HAS ONE, THIS APP'S IF IT HAS NOT — and
            # WHICH of those happened depends entirely on the format. An `xmeml`
            # writes `fontcolor` as plain `<red>/<green>/<blue>`, so a title
            # imported from one arrives in the colour it was set in. A `.prproj`
            # does not carry the fill anywhere (E59), so those keep the default
            # and the report says so. Never invent one for the format that is
            # silent: a white that might have been yellow is a lie the user
            # cannot see.
            "color": str(item.get("color") or IMPORT_TEXT_COLOR),
            "backdrop": IMPORT_TEXT_BACKDROP,
            "opacity": max(0.0, min(1.0, float(item.get("opacity", 1.0)))),
        }
        if item.get("stroke_px"):
            clip["stroke_px"] = max(0.0, min(24.0, float(item["stroke_px"])))
            if item.get("stroke_color"):
                clip["stroke_color"] = str(item["stroke_color"])
        if "x" in item and "y" in item:
            clip["place"] = "free"
            # Clamped to the field's own range rather than trusted: a title
            # parked off-screen in Premiere is legal there and a 422 here.
            clip["x"] = max(-1.0, min(2.0, float(item["x"])))
            clip["y"] = max(-1.0, min(2.0, float(item["y"])))
        # ⚠ THE DROP SHADOW, WHICH *IS* READABLE — see `_prproj_shadow`. Only
        # keys the reader actually found are copied, so a title with no shadow
        # keeps this app's defaults rather than being given a black one at 0
        # distance (which is a shadow the renderer still has to think about).
        # Every value is clamped to its field's range: these come from another
        # program and a 422 at the route is a whole import lost.
        if item.get("shadow"):
            clip["shadow"] = max(0.0, min(0.5, float(item["shadow"])))
        for key, lo, hi in (("shadow_opacity", 0.0, 1.0),
                            ("shadow_angle", 0.0, 360.0)):
            if key in item:
                clip[key] = max(lo, min(hi, float(item[key])))
        if item.get("shadow_color"):
            clip["shadow_color"] = str(item["shadow_color"])
        # ⚠ THE CLIP'S OWN FADE AND POP, given to EVERY caption it holds —
        # because that is what they are. Premiere's Opacity and Scale sit on the
        # graphic, not on one layer inside it, so a title and the subtitle under
        # it fade together. See the note in this function's docstring for why
        # `x`/`y` are deliberately not among them.
        wanted = {
            prop: track for prop, track in (keyframes or {}).items()
            if prop in ("opacity", "scale") and track
        }
        if wanted:
            clip["keyframes"] = wanted
        out.append(clip)
    return out


def _import_shape_clip(
    *, start_ms: int, length_ms: int, layer_id: str, mint
) -> dict:
    """A Premiere drawn shape → an `AnimaticShape` standing in its place.

    ⚠ **A PLACEHOLDER. WHAT CAME ACROSS IS WHEN IT IS ON SCREEN, AND NOTHING
    ELSE.** The shape's geometry and its fill live in an `Appearance` FlatBuffer
    this reader does not decode — the same wall the text colour hits, and for the
    same reason (see `_prproj_text_style`). `AnimaticShape` has no name field, so
    what says where these came from is the ROW: the client names it from
    `shape_lanes`.

    ⚠ AND IT IS DRAWN AT ZERO OPACITY, which is the rule this file already
    follows for a placeholder on a row above the bottom one. Two of the shapes in
    the reference project run the full 68 seconds; a visible box in a colour
    nobody chose, over the whole film, is not a placeholder but a defacement. The
    clip is still ON the timeline, still on a row that says what it is, still
    selectable, and one drag of Opacity in Properties brings it back — INVISIBLE
    IS NOT OMITTED.
    """
    return {
        "id": mint(),
        "layer_id": layer_id,
        "kind": "rect",
        "start_ms": start_ms,
        "duration_ms": length_ms,
        "opacity": 0.0,
    }


def media_library(assets) -> dict:
    """The project's OWN Media pane, keyed the way `resolve` looks things up.

    ⚠ **THE FILES ALREADY IN THE PROJECT COUNT AS ARRIVED.** An import used to
    look only at what was attached to its own request, so a project whose Media
    pane already held every file still turned each clip into a placeholder unless
    all of them were picked again — and somebody who dragged the ONE missing file
    into Media and re-imported was told it had not arrived while its card sat on
    screen beside the message.

    ⚠ **A COLOUR CARD IS NOT MEDIA.** An asset with no `upload_id` has no file
    behind it and must never answer for a filename, or a clip resolves to a
    rectangle of colour that the timeline then treats as footage.

    ⚠ Keyed by name AND by stem, exactly as the freshly-attached files are — an
    app that transcoded a clip on the way out leaves `shot_03.mov` in the project
    file and `shot_03.mp4` in the library.
    """
    out: dict = {}
    for asset in assets or []:
        kind = str(asset.get("kind") or "image").strip().lower()
        label = _basename_of(str(asset.get("label") or ""))
        upload_id = str(asset.get("upload_id") or "")
        if kind not in ("image", "video", "audio") or not label or not upload_id:
            continue
        entry = {
            "kind": kind,
            "upload_id": upload_id,
            "duration_ms": int(asset.get("duration_ms") or 0),
        }
        out.setdefault(label.lower(), entry)
        out.setdefault(os.path.splitext(label)[0].lower(), entry)
    return out


# A `<pathurl>` is a URL in an `xmeml` and a bare Windows path in a `.prproj`.
# Only these three shapes are ever seen, and the trailing slash is part of the
# prefix so what is left is the path itself.
_FILE_URL_PREFIXES = ("file://localhost/", "file:///", "file://")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:/")


def local_media_paths(incoming: dict, *, exists=os.path.isfile) -> dict:
    """`{filename: the file's own path}` for what this MACHINE can actually see.

    ⚠ **THE PATH WAS ALWAYS THERE — IT WAS JUST BEING PRINTED AT THE USER.** The
    report names the folder a missing file came from (see `note_missing`) so
    somebody can go and attach it. But when this server runs on the same computer
    that wrote the project file, that folder is *right there*: asking a person to
    walk to a path we are already holding, and pick it out of a file dialog, is
    work the machine was in a position to do. Reported as exactly that —
    *"tum khud usko pickup kyun nhi kar rahe ho jab tumne location mil raha
    hai"*.

    ⚠ **AND IT IS ONLY EVER AN OFFER, NEVER AN ASSUMPTION.** A path that is not
    on this disk is not an error here; it comes back absent and the file stays in
    the report for the user to attach by hand. The route decides whether it is
    ALLOWED to look at all — see `_may_read_local_media` in `server/animatics.py`
    — because on a hosted server this path belongs to somebody else's computer
    and reading it is neither possible nor wanted.

    ⚠ **MEDIA EXTENSIONS ONLY, AND THAT IS A SECURITY RULE, NOT A TIDINESS ONE.**
    A `<pathurl>` is a string inside an uploaded document, so it is
    attacker-controlled: without this filter a hand-written project file naming
    `.../id_rsa` or `.../.env` would have this function hand the route a private
    file to store and serve back. `media_kind` is the whitelist, and it is the
    only reason this is safe to run at all.

    @param exists  injected so a test can describe a disk without writing one.
    """
    out: dict = {}
    for entry in (incoming.get("files") or {}).values():
        raw = unquote(str((entry or {}).get("pathurl") or "").strip())
        if not raw:
            continue
        raw = raw.split("?")[0].replace("\\", "/")
        low = raw.lower()
        for prefix in _FILE_URL_PREFIXES:
            if low.startswith(prefix):
                raw = raw[len(prefix):]
                # ⚠ THE LEADING SLASH GOES BACK ON FOR EVERYONE BUT WINDOWS.
                # `file://localhost/Users/me/a.mov` is what a Mac editor writes,
                # and stripping the prefix off it leaves a RELATIVE path that
                # would then be resolved against whatever directory this server
                # happens to be running in.
                if not _WINDOWS_DRIVE.match(raw):
                    raw = "/" + raw.lstrip("/")
                break
        # ⚠ ABSOLUTE ONLY. A relative path in a project file is relative to a
        # folder on the machine that wrote it, which we do not have — resolving
        # it here would read a same-named file out of the server's own directory.
        if not (_WINDOWS_DRIVE.match(raw) or raw.startswith("/")):
            continue
        name = raw.rstrip("/").rsplit("/", 1)[-1]
        if not name or not media_kind(name):
            continue
        path = os.path.normpath(raw)
        if not exists(path):
            continue
        out.setdefault(name.lower(), path)
    return out


def to_project(
    incoming: dict,
    resolve,
    *,
    background: str = "#000000",
    new_id=None,
) -> dict:
    """The incoming model as `AnimaticFrame` / `AnimaticAudio` / transitions.

    @param resolve  `name -> {"kind", "upload_id", "duration_ms"}` or None. The
                    ROUTE supplies it, because matching a name to a stored file
                    is the half that knows about this account's uploads.
    @param new_id   how to mint clip ids; the route passes the same generator the
                    uploads use. Injected so a test can make the output stable.

    Returns `{frames, audio_tracks, transitions, video_tracks, audio_lanes,
    report}` — and nothing is saved. See the note at the top of this section.
    """
    mint = new_id or (lambda: uuid.uuid4().hex[:12])
    fps = max(1, int(incoming.get("fps") or 24))
    report = {
        "name": incoming.get("name") or "Imported sequence",
        "reader": incoming.get("reader") or "",
        "fps": fps,
        "warnings": list(incoming.get("warnings") or []),
        "placeholders": [],
        # ⚠ ONE ROW PER FILE THAT DID NOT ARRIVE, WITH THE FOLDER IT CAME FROM.
        # See `note_missing` — `placeholders` stays per CLIP and is what the
        # timeline's gaps are counted from; this is what the dialog can act on.
        "missing": [],
        "matched": 0,
    }
    files = incoming.get("files") or {}
    seen: dict = {}
    tally = {
        "moved": 0, "shortened": 0, "silent": 0, "overlaid": 0,
        # Captions read out of the file, shapes stood in for, and Adjustment
        # Layers left behind — each one a sentence the report owes the user.
        "lettered": 0, "drawn": 0, "adjustment": 0,
    }

    def source_for(key: str):
        """One file, resolved once — several clips usually share it."""
        if key in seen:
            return seen[key]
        entry = files.get(key) or {}
        found = resolve(entry.get("name") or key) if key else None
        seen[key] = found
        if found:
            report["matched"] += 1
        return found

    # ⚠ NAMING THE FILE IS NOT ENOUGH — SAY WHICH FOLDER IT CAME FROM. A real
    # import lost exactly three files out of twenty-eight and the reason was
    # invisible from the report: the voiceover sat inside the project folder and
    # resolved, while the background music and the logo lived in ANOTHER
    # project's folder entirely. The user attached the only folder there was any
    # reason to attach, read "tech_oasis-….mp3 did not arrive", and had no way to
    # learn that a second folder was needed — so it read as this app being unable
    # to take music at all. **Both readers already know**: `files[key]["pathurl"]`
    # is the full path the editor wrote, and the folder off it is the one piece
    # of information that turns "missing" into an instruction.
    # ⚠ ONE ROW PER FILE, NOT PER CLIP. `placeholders` is per clip on purpose —
    # every gap on the timeline is counted there — but a logo used on two clips
    # printed its own name twice in the dialog, which reads as two different
    # broken files rather than one folder nobody attached.
    # ⚠ AND IT SAYS WHICH ARE SOUNDS. A missing picture becomes a labelled card
    # and a missing sound becomes nothing at all (see the audio branch below), so
    # the two are not the same kind of loss and the list must not pretend they
    # are.
    missing: dict = {}

    def note_missing(clip: dict, kind: str) -> None:
        """One clip whose file never arrived — counted, and located if we can."""
        entry = files.get(clip.get("file") or "") or {}
        name = (
            entry.get("name")
            or clip.get("name")
            or ("an audio clip" if kind == "sound" else "a clip")
        )
        report["placeholders"].append(name)
        # `pathurl` is a Windows path from a `.prproj` and a `file://` URL from an
        # `xmeml`; `_basename_of` already unquotes and normalises, so take the
        # folder the same way rather than a second time by hand.
        full = unquote((entry.get("pathurl") or "").strip())
        full = full.split("?")[0].replace("\\", "/").rstrip("/")
        folder = full.rsplit("/", 1)[0] if "/" in full else ""
        row = missing.setdefault(name.lower(), {
            "name": name, "folder": folder, "kind": kind, "clips": 0,
        })
        row["clips"] += 1
        # A later clip can know the path when the first one did not.
        if folder and not row["folder"]:
            row["folder"] = folder

    frames: list = []
    texts: list = []
    shapes: list = []
    transitions: list = []
    video_lanes = (incoming.get("video") or [])[:MAX_IMPORT_TRACKS]

    # --- WHICH PREMIERE ROW BECOMES WHICH ROW HERE --------------------------
    # ⚠ **A PREMIERE ROW OF CAPTIONS MUST NOT MINT A PICTURE ROW.** Picture rows
    # are addressed by NUMBER (`AnimaticFrame.track`), so if row 5 of eight holds
    # nothing but text, leaving the numbering alone hands the client "this import
    # needs 8 picture rows" and it creates three empty ones above the film. The
    # lanes are therefore sorted FIRST, once, and only the ones that still hold a
    # picture are numbered — `row_of` is that renumbering and it is the only
    # place a Premiere lane index becomes an app track number.
    lane_roles: list = []
    for lane in video_lanes:
        roles = []
        for clip in lane.get("clips") or []:
            roles.append(_import_clip_role(clip, source_for(clip.get("file") or "")))
        lane_roles.append(roles)
    picture_lanes = [
        i for i, roles in enumerate(lane_roles) if any(r == "picture" for r in roles)
    ]
    row_of = {lane: row for row, lane in enumerate(picture_lanes)}
    # A picture row's KIND, so the client can call a row of stills "Images" and a
    # row of footage "Video" instead of stamping the file's name onto all of them.
    # A row holding both is a video row: that is what its bottom clip behaves like.
    lane_kinds: list = []
    # ⚠ THE LOWEST PICTURE ROW — the BACKGROUND of this cut, and the only row on
    # which a placeholder card may be opaque. See the `not found` branch below.
    # It is now the lowest row that holds a PICTURE rather than the lowest that
    # holds anything: a caption row under the film would otherwise claim it and
    # every missing file above would go invisible.
    base_track = 0
    text_lane_of: dict = {}
    shape_lane_of: dict = {}
    for lane_index, roles in enumerate(lane_roles):
        if lane_index in row_of:
            continue
        if any(r == "text" for r in roles):
            text_lane_of[lane_index] = (
                f"{IMPORT_TEXT_LANE_PREFIX}{len(text_lane_of)}"
            )
        elif any(r == "shape" for r in roles):
            shape_lane_of[lane_index] = (
                f"{IMPORT_SHAPE_LANE_PREFIX}{len(shape_lane_of)}"
            )
    # ⚠ A ROW CAN HOLD BOTH — a lower third is a bar with words on it, and the
    # bar is a picture-less shape sitting on the same Premiere row as the film.
    # So text and shape lane ids are minted for MIXED rows too, after the pure
    # ones, rather than dropping whatever shares a row with a picture.
    for lane_index, roles in enumerate(lane_roles):
        if any(r == "text" for r in roles) and lane_index not in text_lane_of:
            text_lane_of[lane_index] = f"{IMPORT_TEXT_LANE_PREFIX}{len(text_lane_of)}"
        if any(r == "shape" for r in roles) and lane_index not in shape_lane_of:
            shape_lane_of[lane_index] = f"{IMPORT_SHAPE_LANE_PREFIX}{len(shape_lane_of)}"

    for track, lane in enumerate(video_lanes):
        # `by_end` is how a transition finds the clip it comes after: this app
        # anchors one to a FRAME ID, and the XML anchors it to a position.
        by_end: dict = {}
        row = row_of.get(track)
        kinds_here: set = set()
        for clip in sorted(lane.get("clips") or [], key=lambda c: c["start"]):
            start_ms = frames_to_ms(clip["start"], fps)
            # ⚠ THE LENGTH IS TAKEN FROM THE ORIGINAL START, BEFORE IT IS CLAMPED.
            # Clamping first and subtracting after would stretch a clip that began
            # before the sequence did, instead of moving it.
            length_ms = frames_to_ms(clip["end"], fps) - start_ms
            start_ms, length_ms = _fit_clip(start_ms, length_ms, tally)
            found = source_for(clip.get("file") or "")
            role = _import_clip_role(clip, found)
            if role == "text":
                made = _import_text_clips(
                    clip.get("graphic") or {},
                    start_ms=start_ms,
                    length_ms=length_ms,
                    layer_id=text_lane_of.get(track, f"{IMPORT_TEXT_LANE_PREFIX}0"),
                    mint=mint,
                    keyframes=clip.get("keyframes"),
                )
                texts.extend(made)
                tally["lettered"] += len(made)
                continue
            if role == "shape":
                shapes.append(_import_shape_clip(
                    start_ms=start_ms,
                    length_ms=length_ms,
                    layer_id=shape_lane_of.get(track, f"{IMPORT_SHAPE_LANE_PREFIX}0"),
                    mint=mint,
                ))
                tally["drawn"] += 1
                continue
            if role == "adjustment":
                tally["adjustment"] += 1
                continue
            if row is None:
                # Cannot happen — a lane with a picture in it is in `row_of` by
                # construction. Asserted rather than assumed, because the cost of
                # being wrong is a clip silently dropped from somebody's cut.
                raise RuntimeError(
                    "picture clip on a lane that was not given a row — "
                    "`row_of` and `_import_clip_role` disagree"
                )
            if found:
                # ⚠ ONLY A CLIP THAT RESOLVED VOTES. An unmatched clip has no
                # kind — calling it a still would turn a row of missing FOOTAGE
                # into a row named "Images".
                kinds_here.add(found.get("kind") or "image")
            frame = {
                "id": mint(),
                "label": clip.get("name") or "",
                "track": row,
                "start_ms": start_ms,
                "duration_ms": length_ms,
                "scale": 1.0, "x": 0.5, "y": 0.5, "opacity": 1.0,
                # Clamped for the same reason as the two above: `in_ms` is
                # `ge=0, le=24h` and a source window from another editor need not be.
                "in_ms": 0, "out_ms": None, "speed": 1.0,
                "effects": [], "keyframes": {},
            }
            if not found:
                # The labelled gap. `src` is still sent because the schema wants
                # one; nothing resolves it for a colour clip.
                #
                # ⚠ AND IT ONLY PAINTS ON THE BOTTOM ROW. A card is the right
                # answer for missing footage in the BACKGROUND — the frame would
                # otherwise be empty and the gap invisible. On any row above it
                # the clip is an OVERLAY, and an opaque card there hides the
                # whole film behind it. Premiere is where this bites: a title, a
                # Graphic and an Adjustment Layer have no media file to attach,
                # so every one of them arrives here unmatched — and a real
                # project put four of them full-length over the cut, which
                # previewed and exported as **68 seconds of black**. Reported as
                # "audio, image and video show but text not show": where the
                # lettering should have been there was a black rectangle over
                # everything, so it read as the TEXT being broken.
                #
                # ⚠ INVISIBLE IS NOT OMITTED, which is what E45 is about. The
                # clip is still on the timeline, still carries its own name,
                # still selectable, and still counted in `placeholders` — and
                # `opacity` is an ordinary field, so anyone who wants to see
                # where the gap is drags it back up in Properties.
                blank = row != base_track
                frame.update({
                    "kind": "color",
                    "color": background or "#000000",
                    "src": {"kind": "upload"},
                    "opacity": 0.0 if blank else 1.0,
                })
                if blank:
                    tally["overlaid"] += 1
                note_missing(clip, "picture")
            elif found["kind"] == "video":
                frame.update({
                    "kind": "video",
                    "src": {"kind": "video", "upload_id": found["upload_id"]},
                    "in_ms": min(
                        max(0, frames_to_ms(clip.get("in") or 0, fps)),
                        IMPORT_MAX_START_MS,
                    ),
                })
            else:
                frame.update({
                    "kind": "image",
                    "src": {"kind": "upload", "upload_id": found["upload_id"]},
                })
            # ⚠ **THE MOVE, THE ZOOM AND THE FADE — AND ONLY ON A CLIP THAT
            # RESOLVED.** A placeholder card above the bottom row is parked at
            # `opacity: 0` on purpose (see the branch above): an imported fade
            # writing 1.0 over that would put an opaque colour card back across
            # the whole film, which is the 68-seconds-of-black fault that branch
            # exists to prevent. A gap has nothing to animate anyway.
            if found:
                offset = clip.get("offset")
                if offset:
                    frame["x"] = round(max(-2.0, min(3.0, 0.5 + offset[0])), 4)
                    frame["y"] = round(max(-2.0, min(3.0, 0.5 + offset[1])), 4)
                # ⚠ THE SIZE, when the reader could work one out — see
                # `prproj_scale_base`. Absent on every clip that was fitted to
                # the frame, which is what leaves the schema default of 1.0 in
                # place and every import written before this unchanged.
                if clip.get("scale") is not None:
                    frame["scale"] = round(
                        max(0.01, min(10.0, float(clip["scale"]))), 4
                    )
                if clip.get("opacity") is not None:
                    frame["opacity"] = round(
                        max(0.0, min(1.0, float(clip["opacity"]))), 4
                    )
                if clip.get("keyframes"):
                    frame["keyframes"] = clip["keyframes"]
            frames.append(frame)
            by_end[start_ms + length_ms] = frame["id"]

        if row is not None:
            # ⚠ A ROW WITH ONE VIDEO ON IT IS A VIDEO ROW, and a row nothing
            # resolved on is a video row too — "Video" is this app's generic
            # name for a picture row, so it is the safe answer when the clips
            # cannot say. Only a row where every resolved clip is a still gets
            # called "Images".
            lane_kinds.append(
                "image" if kinds_here == {"image"} else "video"
            )

        for window in lane.get("transitions") or []:
            start_ms = frames_to_ms(window["start"], fps)
            end_ms = frames_to_ms(window["end"], fps)
            cut = (start_ms + end_ms) // 2
            # ⚠ NEAREST CUT WITHIN HALF A FRAME, not an exact match. The two
            # sides round independently — the transition's own start and the
            # clip's end are separate integers in the file — so demanding
            # equality drops perfectly good dissolves.
            slack = max(1, frames_to_ms(1, fps))
            after = next(
                (fid for at, fid in by_end.items() if abs(at - cut) <= slack), None
            )
            if not after:
                continue
            transitions.append({
                "id": mint(),
                "after_frame_id": after,
                "kind": "dissolve",
                "params": {},
                "duration_ms": max(100, min(10_000, end_ms - start_ms)),
            })

    audio_tracks: list = []
    audio_lanes = (incoming.get("audio") or [])[:MAX_IMPORT_TRACKS]
    for lane_index, lane in enumerate(audio_lanes):
        for clip in sorted(lane.get("clips") or [], key=lambda c: c["start"]):
            found = source_for(clip.get("file") or "")
            if not found or found["kind"] != "audio":
                # ⚠ NO PLACEHOLDER FOR SOUND. A silent card in the picture keeps
                # the cut readable; a silent audio clip is a lie you cannot see.
                note_missing(clip, "sound")
                tally["silent"] += 1
                continue
            start_ms = frames_to_ms(clip["start"], fps)
            # ⚠ SAME ORDER AS THE PICTURE: length from the original start, then
            # clamp. `AnimaticAudio` has no ceiling on what plays, so only the
            # start and the offset can put this over the side.
            play_ms = max(IMPORT_MIN_CLIP_MS, frames_to_ms(clip["end"], fps) - start_ms)
            if start_ms < 0:
                start_ms = 0
                tally["moved"] += 1
            start_ms = min(start_ms, IMPORT_MAX_START_MS)
            offset_ms = max(0, frames_to_ms(clip.get("in") or 0, fps))
            audio_tracks.append({
                "id": mint(),
                "upload_id": found["upload_id"],
                "layer_id": f"_import_{lane_index}",
                "filename": (files.get(clip.get("file") or "") or {}).get("name") or "",
                # ⚠ THE FILE'S OWN LENGTH IS NOT IN THE DOCUMENT and this server
                # has no audio decoder. `offset + what plays` is the honest lower
                # bound: it makes the clip play exactly its window, and the only
                # thing it under-states is how much more of the file there is to
                # drag out — which the user can see the moment they try.
                "duration_ms": found.get("duration_ms") or (offset_ms + play_ms),
                "start_ms": start_ms,
                "offset_ms": offset_ms,
                "trim_ms": play_ms,
                "volume": max(0.0, min(2.0, float(clip.get("level") or 1.0))),
                "muted": not clip.get("enabled", True),
            })

    # ⚠ SAID OUT LOUD, because a clip that quietly moved or was quietly
    # shortened is a change to somebody's film that they did not make. The
    # alternative was a 500 with no message at all — see `IMPORT_MAX_START_MS`.
    if tally["shortened"]:
        report["warnings"].append(
            f"{tally['shortened']} clip(s) were longer than the "
            f"{IMPORT_MAX_CLIP_MS // 60_000} minutes a single clip can be here and "
            "were shortened to fit. They are in the right places; their tails are not."
        )
    # ⚠ "0 sounds on 0 rows" IS TRUE AND STILL MISLEADING ON ITS OWN. The reader
    # found the sound; what it could not find was the FILES, and a summary line
    # reading zero looks like a file with no audio in it. Sound gets no
    # placeholder (see above), so this sentence is the only thing that can say
    # the difference between "there was none" and "none of it arrived".
    if tally["silent"]:
        # ⚠ "NONE OF IT ARRIVED" WAS A LIE WHENEVER SOME OF IT DID. This sentence
        # used to read "none of their files were attached, so no sound was
        # brought in" whatever the count was — and the case that exposed it is
        # the ordinary one: a cut whose VOICEOVER sits in the project folder and
        # arrives on all 23 of its clips, and whose music bed lives in another
        # project's folder and does not. The user was told no sound came in while
        # 23 clips of sound sat on the timeline, which makes the whole report
        # untrustworthy. Count what actually landed and say that instead.
        report["warnings"].append(
            f"{tally['silent']} sound clip(s) had no file attached and were left "
            + ("out. " if audio_tracks else "out, so no sound was brought in. ")
            + "Sound cannot stand in the way a picture can — a silent clip is a "
            "gap you cannot see — so those clips are simply not there. The names "
            "and the folders they came from are listed below; add those folders "
            "and read the file again."
        )
    if tally["moved"]:
        report["warnings"].append(
            f"{tally['moved']} clip(s) sat outside the timeline this app can hold "
            "(before the start, or past 24 hours) and were moved to the nearest "
            "point it can. Check where they landed."
        )
    # ⚠ THE SENTENCE THAT NAMES THE TITLES. Most unmatched clips on an upper row
    # are not missing footage at all — they are a Graphic, a title or an
    # Adjustment Layer, which have no file to attach and never will. Saying
    # "attach the files" to somebody whose only gaps are titles sends them
    # looking for files that do not exist, and saying nothing leaves the words
    # they typed in Premiere simply absent with no explanation.
    if tally["overlaid"]:
        report["warnings"].append(
            f"{tally['overlaid']} clip(s) on rows above the bottom one had no file "
            "to go with them, so they are on the timeline but draw nothing — a "
            "solid card there would hide the whole film behind it. Attach the "
            "files and import again to see them."
        )
    # ⚠ THE SENTENCES THAT REPLACED "TYPE IT AGAIN". Until this feature landed
    # the warning above ended with "any LETTERING they held has to be typed again
    # with the Text tool" — which was WRONG, and expensively so: the words were in
    # the file the whole time and users were told to retype them. What is said
    # here now is exactly what came across and exactly what did not.
    if tally["lettered"]:
        # ⚠ WHAT CAME ACROSS DEPENDS ON THE FORMAT, SO THE SENTENCE HAS TO TOO.
        # An `xmeml` writes `fontcolor` and an outline as plain XML; a `.prproj`
        # carries neither anywhere (E59). Saying "the colour could not be read"
        # after an import that read it perfectly is the kind of wrong that makes
        # a user distrust the parts that ARE right.
        line = (
            f"{tally['lettered']} title(s) were read out of the file with their "
            "words, their font, their size and their place on screen, and are on "
            "text rows of their own."
        )
        if (report.get("reader") or "") == "prproj":
            line += (
                " Their COLOUR is not stored in a .prproj anywhere, so they are "
                "this app's white whatever they were in Premiere — set one and "
                "use the row to apply it to the rest. Their DROP SHADOW did come "
                "across. For the colours too, export a Final Cut Pro XML from "
                "Premiere and import that instead."
            )
        else:
            line += " Their colour and outline came across with them."
        report["warnings"].append(line)
    if tally["drawn"]:
        report["warnings"].append(
            f"{tally['drawn']} drawn shape(s) (the bar behind a lower third is the "
            "usual one) are on a Shapes row at the right times, but at zero "
            "opacity: their size and colour are not readable from a .prproj. Drag "
            "Opacity up in Properties to see one and set it how you want it."
        )
    if tally["adjustment"]:
        report["warnings"].append(
            f"{tally['adjustment']} Adjustment Layer(s) were left out. An "
            "Adjustment Layer is an empty holder for colour effects, and those "
            "cannot be read out of a .prproj — bringing it in would have put a "
            "clip over the whole film that does nothing."
        )
    # ⚠ SOUNDS FIRST. A missing picture is a card on the timeline the user can
    # see; a missing sound is silence they cannot, so it is the row that has to
    # be read before the list is scrolled past.
    report["missing"] = sorted(
        missing.values(), key=lambda m: (m["kind"] != "sound", m["name"].lower())
    )
    report["clips"] = len(frames)
    report["audio_clips"] = len(audio_tracks)
    # ⚠ COUNTED FROM THE ROWS THAT WERE ACTUALLY MADE, not from the lanes the
    # file had. This is the number the client turns into empty rows, so a
    # caption-only Premiere row counted here is an empty picture row on screen.
    report["video_tracks"] = len(picture_lanes)
    report["video_lane_kinds"] = lane_kinds
    report["audio_lanes"] = len({a["layer_id"] for a in audio_tracks})
    report["text_lanes"] = len({t["layer_id"] for t in texts})
    report["shape_lanes"] = len({s["layer_id"] for s in shapes})
    report["texts_read"] = len(texts)
    report["shapes_read"] = len(shapes)
    report["transitions"] = len(transitions)
    return {
        "frames": frames,
        "texts": texts,
        "shapes": shapes,
        "audio_tracks": audio_tracks,
        "transitions": transitions,
        "report": report,
    }
