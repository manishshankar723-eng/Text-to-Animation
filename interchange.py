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
        key = file_key(item)
        if not key:
            return None
        return {
            "name": (item.findtext("name") or "").strip(),
            "file": key,
            "start": start,
            "end": end,
            "in": max(0, _int_text(item, "in", 0)),
            "out": max(0, _int_text(item, "out", 0)),
            "enabled": (item.findtext("enabled") or "TRUE").strip().upper() != "FALSE",
        }

    video: list = []
    skipped = 0
    for track_el in seq.findall("media/video/track"):
        lane = []
        for item in track_el.findall("clipitem"):
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


def _prproj_detail(by_id: dict, objid: str) -> dict:
    """One track item, followed through the graph until its facts are found.

    BREADTH-FIRST ON PURPOSE. The same tag appears at several depths — a clip has
    a `<Name>`, so does the master clip behind it, and so does the media file
    behind that — and the SHALLOWEST one is the one belonging to this clip. A
    depth-first walk would come back with the name of the file rather than the
    name of the clip roughly at random.
    """
    got = {"start": None, "end": None, "in": None, "name": "", "path": "", "enabled": True}
    seen = {objid}
    queue = [(objid, 0)]
    while queue:
        current, depth = queue.pop(0)
        el = by_id.get(current)
        if el is None or depth > PRPROJ_MAX_DEPTH:
            continue
        tag = el.tag

        # ⚠ THE POSITION IS ON THE PLAIN `TrackItem`, NOT ON THE CLIP. Premiere
        # builds a clip out of parts — `VideoClipTrackItem` → `ClipTrackItem` →
        # `TrackItem` — and only the last of them carries `<Start>`/`<End>`.
        # Both are required together: an object with one and not the other is a
        # different kind of object that happens to share a tag name.
        if got["start"] is None and tag.endswith("TrackItem"):
            start, end = _prproj_int(el, "Start"), _prproj_int(el, "End")
            if start is not None and end is not None:
                got["start"], got["end"] = start, end
            disabled = (el.findtext("Disabled") or "").strip().lower()
            if disabled in ("true", "1"):
                got["enabled"] = False

        # The source window — how far into the file this clip starts — is on the
        # `Clip`. `<OutPoint>` is deliberately not read: the timeline start and
        # end already give the LENGTH, and a second opinion about it is a second
        # chance to be wrong.
        if got["in"] is None and tag.endswith("Clip"):
            got["in"] = _prproj_int(el, "InPoint")

        if not got["name"]:
            got["name"] = (el.findtext("Name") or "").strip()
        if not got["path"]:
            for path_tag in _PRPROJ_PATH_TAGS:
                found = (el.findtext(path_tag) or "").strip()
                if found:
                    got["path"] = found
                    break

        for child in el.iter():
            ref = child.get("ObjectRef")
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
            ref = child.get("ObjectRef")
            if ref:
                visit(ref, track, depth + 1)

    visit(root_ref, None, 0)
    return items, lanes


def _prproj_rate_of(el) -> int:
    """The fps of a sequence, worked back from its ticks-per-frame.

    Premiere stores the RATE as the length of one frame in ticks, so 24fps is
    10,584,000,000. Anything that does not come back as a sane whole number of
    frames per second is ignored rather than believed.
    """
    if el is None:
        return 0
    for tag in ("VideoFrameRate", "FrameRate", "VideoTimebase", "Timebase"):
        for node in el.iter(tag):
            try:
                ticks = int(float((node.text or "").strip()))
            except (TypeError, ValueError):
                continue
            if ticks > 0:
                fps = int(round(PRPROJ_TICKS_PER_SECOND / ticks))
                if 1 <= fps <= 240:
                    return fps
    return 0


def _prproj_rate_near(by_id: dict, seq_el, max_depth: int = 6) -> int:
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
        return 0
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
            ref = child.get("ObjectRef")
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
    return 0


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
        "Only the CUT was read. Effects, titles, colour, speed changes, audio "
        "levels and nested sequences were not — they cannot be read out of this "
        "format with any confidence.",
    ]
    if root.tag != "PremiereData":
        warnings.append(
            "That file did not start the way a Premiere project usually does, so "
            "even less of it may be right than usual."
        )

    by_id: dict = {}
    for el in root.iter():
        objid = el.get("ObjectID")
        if objid and objid not in by_id:
            by_id[objid] = el
    if not by_id:
        raise ImportRefused(
            "Nothing inside that .prproj looked like a Premiere project. In "
            "Premiere: File › Export › Final Cut Pro XML, and bring that instead."
        )

    # --- which sequence -----------------------------------------------------
    # ⚠ A PROJECT HOLDS MANY SEQUENCES AND THIS IMPORTS ONE. Taking every clip
    # item in the file would stack every sequence in the project on top of each
    # other at frame zero. The biggest one is the guess, and the others are named
    # so the user knows one was chosen for them.
    sequences = [el for el in root.iter() if el.tag == "Sequence" and el.get("ObjectID")]
    best, items, lanes = None, [], []
    for seq in sequences:
        found, seq_lanes = _prproj_walk(by_id, seq.get("ObjectID"))
        clips = [i for i in found if i[0].endswith("ClipTrackItem")]
        if len(clips) > len([i for i in items if i[0].endswith("ClipTrackItem")]):
            best, items, lanes = seq, found, seq_lanes
    # ⚠ ONLY WHEN ONE WAS ACTUALLY CHOSEN. If no sequence yielded a clip the
    # flat route runs instead, and saying "the one with the most clips was taken"
    # would describe something that did not happen.
    if len(sequences) > 1 and best is not None:
        warnings.append(
            f"That project holds {len(sequences)} sequences and only one can be "
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
    fps = _prproj_rate_near(by_id, best)
    if not fps:
        loose = _prproj_rate_of(root)
        if loose:
            fps = loose
            warnings.append(
                f"The frame rate was not on that sequence, so the first one found "
                f"anywhere in the project was used ({fps} fps). If this project "
                f"holds sequences at different rates, check the clip lengths."
            )
    if not fps:
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
        # Keyed by PATH where there is one, so two clips cut from the same file
        # resolve to the same upload and two different files that happen to share
        # a clip name do not collide.
        key = (got["path"] or name).lower()
        files.setdefault(key, {
            "name": _basename_of(got["path"]) or name,
            "pathurl": got["path"],
        })
        lane["clips"].append({
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
        })

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
        "matched": 0,
    }
    files = incoming.get("files") or {}
    seen: dict = {}
    tally = {"moved": 0, "shortened": 0}

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

    frames: list = []
    transitions: list = []
    video_lanes = (incoming.get("video") or [])[:MAX_IMPORT_TRACKS]
    for track, lane in enumerate(video_lanes):
        # `by_end` is how a transition finds the clip it comes after: this app
        # anchors one to a FRAME ID, and the XML anchors it to a position.
        by_end: dict = {}
        for clip in sorted(lane.get("clips") or [], key=lambda c: c["start"]):
            start_ms = frames_to_ms(clip["start"], fps)
            # ⚠ THE LENGTH IS TAKEN FROM THE ORIGINAL START, BEFORE IT IS CLAMPED.
            # Clamping first and subtracting after would stretch a clip that began
            # before the sequence did, instead of moving it.
            length_ms = frames_to_ms(clip["end"], fps) - start_ms
            start_ms, length_ms = _fit_clip(start_ms, length_ms, tally)
            found = source_for(clip.get("file") or "")
            frame = {
                "id": mint(),
                "label": clip.get("name") or "",
                "track": track,
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
                frame.update({
                    "kind": "color",
                    "color": background or "#000000",
                    "src": {"kind": "upload"},
                })
                report["placeholders"].append(
                    (files.get(clip.get("file") or "") or {}).get("name")
                    or clip.get("name")
                    or "a clip"
                )
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
            frames.append(frame)
            by_end[start_ms + length_ms] = frame["id"]

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
                report["placeholders"].append(
                    (files.get(clip.get("file") or "") or {}).get("name")
                    or clip.get("name")
                    or "an audio clip"
                )
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
    if tally["moved"]:
        report["warnings"].append(
            f"{tally['moved']} clip(s) sat outside the timeline this app can hold "
            "(before the start, or past 24 hours) and were moved to the nearest "
            "point it can. Check where they landed."
        )
    report["clips"] = len(frames)
    report["audio_clips"] = len(audio_tracks)
    report["video_tracks"] = len(video_lanes)
    report["audio_lanes"] = len({a["layer_id"] for a in audio_tracks})
    report["transitions"] = len(transitions)
    return {
        "frames": frames,
        "audio_tracks": audio_tracks,
        "transitions": transitions,
        "report": report,
    }
