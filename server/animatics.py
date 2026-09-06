"""
animatics.py — Storyboard → Animatic: timed image sequence + audio → video.

A saved animatic IS a job (`JobKind.ANIMATIC`), the same call the storyboard
library made: the record is already per-owner and persisted, so the library is a
view over jobs rather than a second store that could drift.

The job's status describes the EXPORT, which is the only long-running thing here:

    queued     — a draft; never exported
    running    — ffmpeg is encoding right now
    succeeded  — a video exists (result["video"])
    failed     — the last export failed (the project is still editable)

Editing a project that already has a video marks it `stale`, so the UI can say
the downloadable file no longer matches what's on screen.

Most of this module calls no AI backend — laying out an animatic costs no
quota. The exceptions are named as such at their routes: ✨ Animate (Veo),
captions, voiceover, reframe, redrawing a panel, and generating a shot
between two others.
"""

import glob
import hashlib
import io
import json
import logging
import os
import re
import shutil
import uuid
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

import animatic_render
import freesound
import panel_sequence

from . import config, worker
from . import chat_sessions
from .auth import CurrentUser, get_current_user
# ⚠ THE GUARD THAT ACTUALLY TURNS A FEATURE OFF. The sidebar reading the same
# registry is cosmetic — anyone can call these routes directly. See features.py.
from .features import require_feature
# ⚠ THE QUOTA GUARD SITS BESIDE `require_feature`, ON THE SAME ROUTES. A limit
# checked AFTER the work is a limit that bills the customer for the call telling
# them they are over. See server/usage.py.
from .usage import require_quota
from . import usage as usage_counters
from .common import (
    board_dir,
    dir_bytes,
    get_owned_job,
    panel_for_index,
    panel_path,
    regenerate_board_panel,
    sequence_summary,
    submit_sequence_run,
    variants_of,
)
from .jobs import get_store
from .schemas import (
    AnimaticAnimateRequest,
    AnimaticDirectorRun,
    AnimaticAudio,
    AnimaticAudioResponse,
    AnimaticBoardImportRequest,
    AnimaticBoardImportResponse,
    AnimaticCaptionsRequest,
    AnimaticCreateRequest,
    AnimaticDialogueLine,
    AnimaticDialogueSheet,
    AnimaticFrame,
    AnimaticGeneratedImage,
    AnimaticImageBackend,
    AnimaticImageGenerateRequest,
    AnimaticImportResponse,
    AnimaticAsset,
    AnimaticLayer,
    AnimaticMediaItem,
    AnimaticNeighbourShotContext,
    AnimaticNeighbourShotRequest,
    AnimaticNeighbourShotResponse,
    AnimaticNeighbourSuggestRequest,
    AnimaticNeighbourSuggestResponse,
    AnimaticOverlay,
    AnimaticPanelRegenerateRequest,
    AnimaticPanelSource,
    AnimaticProject,
    AnimaticReframeRequest,
    AnimaticRelengthRequest,
    AnimaticSaveRequest,
    AnimaticSettings,
    AnimaticShape,
    AnimaticShotWording,
    AnimaticSummary,
    AnimaticTextClip,
    AnimaticTransition,
    AnimaticUploadResponse,
    AnimaticVeoClip,
    AnimaticVideoGenerateRequest,
    AnimaticVideoItem,
    AnimaticVideoUploadResponse,
    AnimaticVoiceoverRequest,
    AudioCostEstimate,
    CostEstimate,
    DialogueLine,
    ImportMissingFile,
    InterchangeLoss,
    InterchangeReport,
    Job,
    JobCreatedResponse,
    JobKind,
    JobStatus,
    PanelSequenceInfo,
    PersonaOption,
    ReframeCostEstimate,
    RenderSettings,
    SoundImportRequest,
    SoundImportResponse,
    SoundtrackItem,
    SoundtrackRequest,
    SoundtrackResponse,
    VoiceOption,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/animatics", tags=["animatics"])

# Upload ids are generated here and only ever used to build a filename, so they
# are validated on the way back IN as well — this is what stops a crafted
# upload_id from walking out of the media folder.
_ID_RE = re.compile(r"^[a-f0-9]{6,32}$")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _animatic_dir(job_id: str) -> str:
    return os.path.join(config.OUTPUT_DIR, "_animatics", job_id)


def _media_dir(job_id: str) -> str:
    return os.path.join(_animatic_dir(job_id), "media")


def _video_path(job_id: str, container: str = "mp4") -> str:
    """Where an export of this container lands. One file per container.

    Keeping the extensions apart rather than writing everything to one name
    means a GIF export does not destroy the MP4 you made ten minutes ago — the
    two are different deliverables of the same cut, and nobody expects making
    one to delete the other.
    """
    import export_presets

    return os.path.join(_animatic_dir(job_id), export_presets.output_name(container))


def _exported_file(job_id: str, container: str = "mp4") -> tuple[str, str] | None:
    """The exported file to serve, as (path, container), or None if there is none.

    Asks for the container the settings name, then FALLS BACK to any other that
    exists. That fallback is what stops the Download button 404ing the moment
    someone changes the preset in the dialog without re-exporting: the file on
    disk is still a real export of this animatic, and handing it over is a
    better answer than pretending there is nothing.
    """
    import export_presets

    wanted = export_presets.normalise_container(container)
    for name in (wanted, *(c for c in export_presets.CONTAINERS if c != wanted)):
        path = _video_path(job_id, name)
        if os.path.isfile(path):
            return path, name
    return None


# ---------------------------------------------------------------------------
# HTTP caching for media
# ---------------------------------------------------------------------------
# A week, immutable. Every picture and clip in this app is fetched with an
# `Authorization` header and turned into an object URL, and `FileResponse` sent
# no `Cache-Control` at all — so a reload re-downloaded every thumbnail and
# every video the project holds, however little had changed.
#
# ⚠ ONLY FOR A URL THAT CANNOT CHANGE MEANING, and there are exactly two shapes:
#
#   1. An upload id. Every one is a fresh `uuid.uuid4().hex[:12]`, so the bytes
#      behind `/media/{upload_id}` are written once and never rewritten.
#   2. A `?v=` stamp. `_frame_version` changes when the picture behind the frame
#      changes, which is what makes the new URL a different cache entry. WITHOUT
#      the stamp there is nothing to invalidate, so the routes below emit this
#      header only when `v` was actually sent — a redrawn panel served from a
#      week-old cache is precisely the bug `_frame_version` exists to prevent.
#
# `private` because the response is one user's: shared caches must not hold it.
_IMMUTABLE_MEDIA = {"Cache-Control": "private, max-age=604800, immutable"}


def _media_headers(versioned: bool = True) -> dict[str, str] | None:
    """Cache headers for a media response, or None to leave it uncacheable."""
    return dict(_IMMUTABLE_MEDIA) if versioned else None


def _image_path(job_id: str, upload_id: str) -> str | None:
    """Local path of an uploaded frame image, or None if the id is bogus."""
    if not upload_id or not _ID_RE.match(upload_id):
        return None
    return os.path.join(_media_dir(job_id), f"img_{upload_id}.png")


def _audio_file(job_id: str, upload_id: str) -> str | None:
    """Local path of the uploaded audio (extension unknown until we look)."""
    if not upload_id or not _ID_RE.match(upload_id):
        return None
    matches = sorted(glob.glob(os.path.join(_media_dir(job_id), f"audio_{upload_id}.*")))
    return matches[0] if matches else None


def _video_file(job_id: str, upload_id: str) -> str | None:
    """Local path of an uploaded VIDEO clip's source file.

    Stored under its own `vid_` prefix so the one media folder can hold stills,
    audio and video without an id collision meaning two different files.
    """
    if not upload_id or not _ID_RE.match(upload_id):
        return None
    matches = sorted(glob.glob(os.path.join(_media_dir(job_id), f"vid_{upload_id}.*")))
    return matches[0] if matches else None


def _stills_dir(job_id: str) -> str:
    """Where this animatic's extracted video frames are cached.

    INSIDE the animatic's own folder on purpose: the stills are pure derived
    data, and putting them here means `delete_animatic`'s existing rmtree
    collects them. There is no separate garbage collector to forget to run.
    """
    return os.path.join(_animatic_dir(job_id), "_stills")


def _proxy_dir(job_id: str) -> str:
    """Where this animatic's preview proxies are cached.

    Inside the animatic's own folder for exactly the reason `_stills_dir` is:
    they are derived data, and `delete_animatic`'s existing rmtree collects them
    with everything else. There is no separate garbage collector to forget.
    """
    return os.path.join(_animatic_dir(job_id), "_proxies")


def _frame_origin(frame: AnimaticFrame) -> str:
    """Where one picture-track clip came from — "board" | "video" | "image".

    ⚠ THE PYTHON HALF OF `frameOrigin` in client/src/animatic/scene.js, and it has
    to keep agreeing with it: the editor draws the picture track as two rows split
    by this, and the eye on either row is applied HERE. Split by `kind` instead and
    hiding "Video" in the editor would blank a different set of clips in the MP4
    than the monitor blanked — the exact class of bug the render-parity tests
    exist to catch.

    Origin, not kind: animating a board shot with Veo makes it a video clip, and
    it stays a board shot for the purpose of which row it is on.
    """
    src = frame.src
    if src.storyboard_id:
        return "board"
    if frame.kind == "video":
        return "video"
    return "image"


def _lane_hidden(hidden: set[str], kind: str, layer_id: str | None) -> bool:
    """Is the row this clip sits on switched off?

    The tokens are written by the editor (`laneToken` in AnimaticEditor.jsx) and
    documented on `AnimaticSettings.hidden_lanes`. Read off the CLIP's own fields
    rather than passed down, so nothing has to keep a parallel list of rows in
    step with the one the user sees.
    """
    return f"{kind}:{layer_id or ''}" in hidden


def _video_poster(job_id: str, upload_id: str, at_ms: int = 0) -> str | None:
    """One still off an uploaded video, by UPLOAD id — extracted on demand.

    ⚠ TAKES AN UPLOAD, NOT A CLIP, and that is the whole reason it exists apart
    from `_video_thumb`. A clip can only be asked about once it is ON the
    project, and the editor's save is debounced — so between dropping a video in
    and the save landing there is no clip to name, the media card has no picture
    to show, and it sits on its loading spinner. That reads as an upload that
    never finished, which was the report: "I upload a video file here but it
    doesn't show in the media panel."

    Returns None rather than raising: a clip with no still shows an empty tile,
    which is what a missing panel already does.
    """
    import video_frames

    source = _video_file(job_id, upload_id or "")
    if not source:
        return None
    at = max(0, int(at_ms or 0))
    try:
        # A single frame: one still, at that moment, cached under its own key —
        # the same cache the export uses, so a still an export already paid for
        # is free here.
        info = video_frames.extract_frames(
            source, 1, _stills_dir(job_id), start_ms=at, span_ms=1
        )
    except Exception:  # noqa: BLE001 — a thumbnail is never worth a 500
        logger.warning(
            "[animatic %s] could not read a poster for upload %s", job_id, upload_id,
            exc_info=True,
        )
        return None
    return video_frames.frame_path(info, at)


def _video_thumb(job_id: str, frame: AnimaticFrame) -> str | None:
    """One still off a video CLIP, for its thumbnail.

    The picture a video clip shows in the Media pane, on the timeline and in
    Properties is the frame at its IN POINT, so re-trimming a clip changes the
    thumbnail to what the clip now opens on.
    """
    return _video_poster(job_id, frame.src.upload_id or "", frame.in_ms or 0)


# ---------------------------------------------------------------------------
# Reading a project off a job record
# ---------------------------------------------------------------------------
def _get_owned_animatic(job_id: str, current: CurrentUser) -> Job:
    job = get_owned_job(job_id, current)
    if job.kind != JobKind.ANIMATIC:
        raise HTTPException(status_code=400, detail="Not a project.")
    return job


def _frames_of(job: Job) -> list[AnimaticFrame]:
    raw = (job.params or {}).get("frames") or []
    out: list[AnimaticFrame] = []
    for item in raw:
        try:
            out.append(AnimaticFrame(**item))
        except Exception:  # noqa: BLE001 — one corrupt frame must not 500 the project
            logger.warning("[animatic %s] dropping unreadable frame %r", job.job_id, item)
    return out


def _frame_by_id(job: Job, frame_id: str) -> AnimaticFrame | None:
    """One frame, without validating the other fifty-nine.

    ⚠ THIS IS THE THUMBNAIL PATH. `/frame/{id}` is requested once per frame when
    the editor opens, and each of those requests used to run `_frames_of` — a
    full Pydantic parse of every frame in the project — to pick one row out of
    it. Sixty frames therefore cost sixty full parses of sixty frames.

    Scans the raw rows for the id and validates only the match, falling back to
    `_frames_of` when the scan finds nothing so a project whose params are
    shaped unexpectedly behaves exactly as it did before.
    """
    for item in (job.params or {}).get("frames") or []:
        if isinstance(item, dict) and item.get("id") == frame_id:
            try:
                return AnimaticFrame(**item)
            except Exception:  # noqa: BLE001 — a corrupt row is a missing frame
                logger.warning(
                    "[animatic %s] unreadable frame %r", job.job_id, frame_id
                )
                return None
    return next((f for f in _frames_of(job) if f.id == frame_id), None)


def _texts_of(job: Job) -> list[AnimaticTextClip]:
    raw = (job.params or {}).get("texts") or []
    out: list[AnimaticTextClip] = []
    for item in raw:
        try:
            out.append(AnimaticTextClip(**item))
        except Exception:  # noqa: BLE001 — one bad clip must not 500 the project
            logger.warning("[animatic %s] dropping unreadable text clip %r", job.job_id, item)
    return out


def _assets_of(job: Job) -> list[AnimaticAsset] | None:
    """The MEDIA LIBRARY — what has been added to this animatic.

    ⚠ RETURNS `None` WHEN THE KEY WAS NEVER WRITTEN, and that is not the same
    answer as an empty list. A project saved before the library existed has no
    `assets` key at all, and the editor derives one from its frames and audio the
    first time it is opened (`libraryFromProject` in `animatic/assets.js`). A
    project WITH an empty list has had its library emptied on purpose — the user
    pressed ✕ on the last card — and re-deriving one from the timeline would put
    every card straight back, so the ✕ would look broken.

    Flattening the two into `[]` is therefore a bug, not a simplification, which
    is why `AnimaticProject.assets` is `| None` rather than defaulting to a list.

    The DERIVATION itself is deliberately not here: it lives in the client's pure
    `assets.js`, in one language, where `tests/asset_fields_check.py` can reach it
    — the same rule `frame_save.js` follows. A Python twin of it would be a second
    thing to keep in step for no gain.
    """
    if "assets" not in (job.params or {}):
        return None
    raw = (job.params or {}).get("assets") or []
    out: list[AnimaticAsset] = []
    for item in raw:
        try:
            out.append(AnimaticAsset(**item))
        except Exception:  # noqa: BLE001 — one bad row must not 500 the project
            logger.warning("[animatic %s] dropping unreadable asset %r", job.job_id, item)
    return out


def _asset_url(job: Job, asset: AnimaticAsset, boards: dict | None = None) -> str | None:
    """Serve path for one library card's picture, or None if it has none.

    ⚠ EVERY SHAPE HERE IS RESOLVABLE WITHOUT THE ASSET BEING ON THE SAVED
    PROJECT — an upload by its upload id, a panel by (board, index). That is the
    point: the client builds the same strings when it adds a card, so a freshly
    imported library has pictures before the autosave has run. Contrast
    `AnimaticFrame.url`, which resolves an id through the saved frame list and
    therefore 404s until a save lands — the bug `doBoardImport` is written around.

    `boards` is the caller's per-request board cache, shared with `_frames_of`.
    Pass it: a panel-backed card asks `_frame_version` for a stamp, and without
    the cache that is one job-store round trip PER CARD for boards the frame
    loop has already fetched. Defaults to None so any other caller behaves as
    it always did.
    """
    src = asset.src
    if asset.kind == "audio":
        return f"/animatics/{job.job_id}/media/{asset.upload_id}" if asset.upload_id else None
    if asset.kind == "color":
        return None  # a swatch, not a file
    if src.kind == "video":
        # A VIDEO wants a still, not the MP4 — an <img> can only fail to draw one.
        return (
            f"/animatics/{job.job_id}/media/{src.upload_id}?poster=1"
            if src.upload_id
            else None
        )
    if src.kind == "upload":
        return f"/animatics/{job.job_id}/media/{src.upload_id}" if src.upload_id else None
    if src.kind in ("panel", "pose") and src.storyboard_id and src.index is not None:
        # ⚠ VERSIONED, so re-drawing the panel on the board changes the library
        # card too. The client caches one blob per path and never re-fetches a
        # path it already holds — see `_frame_version` and `urlSrcRef`.
        stamp = _frame_version(job, AnimaticFrame(id="_asset", src=src.model_dump()), boards)
        pose = f"&frame={int(src.frame)}" if src.kind == "pose" and src.frame is not None else ""
        return (
            f"/animatics/{job.job_id}/panel/{src.storyboard_id}/{int(src.index)}"
            f"?v={stamp}{pose}"
        )
    return None


def _layers_of(job: Job) -> list[AnimaticLayer]:
    """The lanes the user added. Absent (empty) on every pre-layers project,
    which then shows exactly the default lanes it always did."""
    raw = (job.params or {}).get("layers") or []
    out: list[AnimaticLayer] = []
    for item in raw:
        try:
            out.append(AnimaticLayer(**item))
        except Exception:  # noqa: BLE001 — one bad lane must not 500 the project
            logger.warning("[animatic %s] dropping unreadable layer %r", job.job_id, item)
    return out


def _overlays_of(job: Job) -> list[AnimaticOverlay]:
    """Pictures composited over the sequence (the image layers)."""
    raw = (job.params or {}).get("overlays") or []
    out: list[AnimaticOverlay] = []
    for item in raw:
        try:
            out.append(AnimaticOverlay(**item))
        except Exception:  # noqa: BLE001 — one bad overlay must not 500 the project
            logger.warning("[animatic %s] dropping unreadable overlay %r", job.job_id, item)
    return out


def _shapes_of(job: Job) -> list[AnimaticShape]:
    """The shape layer. Absent on every animatic saved before shapes existed,
    which reads as an empty list and changes nothing about those projects."""
    raw = (job.params or {}).get("shapes") or []
    out: list[AnimaticShape] = []
    for item in raw:
        try:
            out.append(AnimaticShape(**item))
        except Exception:  # noqa: BLE001 — one bad shape must not 500 the project
            logger.warning("[animatic %s] dropping unreadable shape %r", job.job_id, item)
    return out


def _transitions_of(job: Job) -> list[AnimaticTransition]:
    """What happens on the cuts. Absent on every animatic saved before
    transitions existed, which then plays as the straight cuts it always did."""
    raw = (job.params or {}).get("transitions") or []
    out: list[AnimaticTransition] = []
    for item in raw:
        try:
            out.append(AnimaticTransition(**item))
        except Exception:  # noqa: BLE001 — one bad transition must not 500 the project
            logger.warning("[animatic %s] dropping unreadable transition %r", job.job_id, item)
    return out


def _audio_tracks_of(job: Job) -> list[AnimaticAudio]:
    """Every audio CLIP on this animatic, oldest first.

    Migrates records written before multi-track, which carried a single `audio`
    object rather than a list. Nothing rewrites those on disk — they're read
    forward, so an old animatic just opens with one track.

    ⚠ AND BACKFILLS `id`, which is the identity every clip is keyed by since the
    razor learned to cut audio. A clip saved before that has none, and the
    `upload_id` is the right value to use: in those projects one file WAS one
    clip, so the editor's selection, element and patch keys stay exactly what
    they were. Read forward only — nothing is rewritten on disk until the next
    ordinary save.
    """
    params = job.params or {}
    raw = params.get("audio_tracks")
    if raw is None:
        single = params.get("audio")
        raw = [single] if single else []

    out: list[AnimaticAudio] = []
    for item in raw or []:
        try:
            track = AnimaticAudio(**item)
        except Exception:  # noqa: BLE001 — one bad track must not 500 the project
            logger.warning("[animatic %s] dropping unreadable audio track %r", job.job_id, item)
            continue
        if not track.id:
            track.id = track.upload_id
        out.append(track)
    return out


def _audio_files_of(job: Job) -> set[str]:
    """The distinct UPLOADS the timeline's audio clips read from.

    The cap on audio is a cap on FILES, not on clips: cutting one track into
    four pieces uploads nothing and costs nothing, so counting clips would make
    the razor run out of room after three cuts.
    """
    return {t.upload_id for t in _audio_tracks_of(job) if t.upload_id}


def _veo_clips_of(job: Job) -> list[AnimaticVeoClip]:
    """Every Veo render made from this editor, oldest first.

    ⚠ Read out of the job's RESULT, not its params. `save_animatic` rewrites
    `params` wholesale from whatever the editor last had in memory, so a render
    recorded there would be erased by any save that started before it finished —
    and that save would be destroying the only record of something the user was
    charged for. `result` is written by the server alone.
    """
    raw = (job.result or {}).get("veo_clips") or []
    out: list[AnimaticVeoClip] = []
    for item in raw:
        try:
            out.append(AnimaticVeoClip(**item))
        except Exception:  # noqa: BLE001 — one bad record must not 500 the project
            logger.warning("[animatic %s] dropping unreadable Veo record %r", job.job_id, item)
    return out


def _write_veo_clip(job_id: str, clip_id: str, **fields) -> None:
    """Create or update one render record. Read-modify-write on the job's result.

    Safe from races because only ONE render batch per animatic can be in flight:
    the endpoint puts the job into RUNNING and refuses a second batch, and
    `save_animatic` already 409s while RUNNING. So for the whole life of a
    render there is exactly one writer to this job, and the editor cannot save
    over it.
    """
    store = get_store()
    job = store.get(job_id)
    if job is None:
        return
    result = dict(job.result or {})
    clips = [dict(c) for c in (result.get("veo_clips") or [])]
    for i, raw in enumerate(clips):
        if raw.get("id") == clip_id:
            clips[i] = {**raw, **fields}
            break
    else:
        clips.append({"id": clip_id, **fields})
    result["veo_clips"] = clips
    try:
        store.update(job_id, result=result)
    except Exception:  # noqa: BLE001 — a lost state write must not kill the batch
        logger.exception("[animatic %s] could not persist Veo record %s", job_id, clip_id)


def _director_run_of(job: Job) -> "AnimaticDirectorRun | None":
    """The last 🎬 Veo pass this project started, or None.

    ⚠ SAME HOME AND SAME REASON AS `veo_clips` — the job's `result`, never its
    `params`. A run record in `params` would be erased by the editor's next
    autosave, and with it the only statement of what the user agreed to pay for
    before the tab was closed.
    """
    raw = (job.result or {}).get("director_run")
    if not raw:
        return None
    try:
        return AnimaticDirectorRun(**raw)
    except Exception:  # noqa: BLE001 — one bad record must not 500 the project
        logger.warning("[animatic %s] dropping unreadable director run %r", job.job_id, raw)
        return None


def _settings_of(job: Job) -> AnimaticSettings:
    try:
        return AnimaticSettings(**((job.params or {}).get("settings") or {}))
    except Exception:  # noqa: BLE001
        return AnimaticSettings()


def _duration_ms(frames: list[AnimaticFrame]) -> int:
    return sum(int(f.duration_ms) for f in frames)


def _project_of(job: Job) -> AnimaticProject:
    """Build the client-facing project, filling in serve URLs.

    Frames get ONE url shape (`/animatics/{id}/frame/{frame_id}`) whether the
    picture is a board panel or an upload, so the editor never has to care which
    it is — and a board panel that gets re-drawn is picked up automatically,
    because the path is resolved on every request.

    ⚠ …AND THE URL CARRIES `?v=`. Resolving the path on every request is what
    makes the SERVER return the new picture; it does nothing about the CLIENT,
    which caches one object URL per path and never re-fetches a path it already
    holds. See `_frame_version`. One board record is fetched per read however
    many frames point at it.
    """
    frames = _frames_of(job)
    boards: dict = {}
    for f in frames:
        f.url = f"/animatics/{job.job_id}/frame/{f.id}?v={_frame_version(job, f, boards)}"
    overlays = _overlays_of(job)
    for overlay in overlays:
        # Same url shape as a frame's picture — the editor fetches both the
        # same way, and an overlay is servable the moment it is uploaded.
        overlay.url = f"/animatics/{job.job_id}/media/{overlay.upload_id}"
    assets = _assets_of(job)
    for asset in assets or []:
        # Same `boards` dict the frame loop filled: a library of panel cards is
        # normally the SAME board the frames came from, so this is free now.
        asset.url = _asset_url(job, asset, boards)
    tracks = _audio_tracks_of(job)
    for track in tracks:
        # By upload id, not the project-level /audio route: straight after an
        # upload the file is on disk but not yet saved onto the project.
        track.url = f"/animatics/{job.job_id}/media/{track.upload_id}"

    return AnimaticProject(
        job_id=job.job_id,
        veo_clips=_veo_clips_of(job),
        director_run=_director_run_of(job),
        title=job.character_name or "Project",
        status=job.status,
        source_storyboard_id=(job.params or {}).get("source_storyboard_id"),
        settings=_settings_of(job),
        frames=frames,
        texts=_texts_of(job),
        shapes=_shapes_of(job),
        layers=_layers_of(job),
        assets=assets,
        overlays=overlays,
        transitions=_transitions_of(job),
        audio_tracks=tracks,
        duration_ms=_duration_ms(frames),
        video=(job.result or {}).get("video"),
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# WHAT A LIBRARY CARD DOES NOT READ — measured against the live collection, not
# guessed. An animatic averages 4.8 KB and HALF of that is `params.frames`; of a
# frame, `src` + `mask` + `keyframes` are ~70% of the bytes and only `src` is
# ever printed. One library page (100 rows) was ~500 KB of documents to draw a
# hundred titles, counts and thumbnails.
#
# ⚠ THIS LIST AND `_summarise` ARE ONE THING IN TWO PLACES, which is a hazard,
# so it is guarded rather than trusted: `tests/summary_projection_check.py`
# rebuilds every real animatic's summary from the full document AND from the
# slimmed one and fails if the two differ. Add a field to `_summarise` that is
# named here and the suite says so on the next run.
#
# ⚠ ONLY OPTIONAL FIELDS MAY APPEAR HERE. A dropped field arrives as its model
# default, so dropping a REQUIRED one makes `Job(**doc)` raise for every row.
# `AnimaticFrame` requires `id` and `src`; `AnimaticTextClip` requires `id`.
SUMMARY_DROP = (
    # Per-frame, and the bulk of the saving: the mask shape, the animation
    # curves and the effect chain. A card prints a count, a length and one
    # thumbnail - it has never opened any of these.
    "params.frames.mask",
    "params.frames.keyframes",
    "params.frames.effects",
    # Text clips are COUNTED, never read, so everything but the id could go;
    # the curves are the only part big enough to be worth naming.
    "params.texts.keyframes",
    # Whole arrays nothing on a card touches.
    "params.overlays",
    "params.transitions",
)


# ---------------------------------------------------------------------------
# GHOST PROJECTS — rows nobody ever put anything into
# ---------------------------------------------------------------------------
# ⚠ A PROJECT IS ONLY A PROJECT ONCE SOMETHING IS IN IT. The New Project tile
# used to POST one of these before the user had done a single thing, so "open
# it, change my mind, go back" left an empty "Untitled Project" on the library
# for ever — four of them in one screenshot, reported as "i did nothing in this
# project but it shows here". The client no longer creates a project until the
# first real edit (`ensureProject` in AnimaticEditor.jsx); this is the other
# half of that fix — the empty rows ALREADY in the database, and anything a
# stale tab still manages to make, never reach the library.
#
# ⚠ THE TITLES ARE THE CLIENT'S `isUntitled`, IN PYTHON. Same sentinel, same
# reason it has a legacy entry: every project made before 2026-08-21 carries the
# older wording, and comparing against the new string alone would quietly
# promote all of them to "named".
_UNTITLED_TITLES = {"Untitled Project", "Untitled animatic"}

# How old an empty row has to be before it is swept off disk as well as hidden.
# A day, because nothing younger can be trusted to be dead: a second tab may be
# building a project RIGHT NOW that has been created but not yet saved.
GHOST_MAX_AGE_S = 24 * 3600

# ⚠ AND ONLY A FEW PER REQUEST. Hiding a row is free; deleting one is an rmtree
# and a store write, and this runs INSIDE the library's own GET. An account
# carrying a hundred of them must not pay for all hundred on the first load —
# the rest go on the next one, and the list is correct either way because
# hiding does not depend on the sweep.
GHOST_SWEEP_PER_CALL = 20


def _is_ghost(job: Job, card: AnimaticSummary) -> bool:
    """Is this row an empty, never-named project — i.e. not a project at all?

    ⚠ `size_bytes == 0` IS DOING REAL WORK HERE, not belt-and-braces. This is
    asked on the LIST route, whose documents are slimmed (`SUMMARY_DROP`), and
    `params.overlays` is one of the fields it drops — so a project whose only
    content is a picture on an Images lane arrives here looking exactly like an
    empty one. Its upload is on disk, so its media folder is not empty, and that
    is what keeps it. Never widen this test to a field that SUMMARY_DROP removes.
    """
    title = (job.character_name or "").strip()
    if title and title not in _UNTITLED_TITLES:
        return False  # a name the user chose is content in itself
    if card.frame_count or card.text_count or card.audio_count or card.has_video:
        return False
    if card.size_bytes:
        return False
    params = job.params or {}
    # Vector shapes and user-added rows carry no file, so the size test above
    # cannot see them.
    if params.get("shapes") or params.get("layers"):
        return False
    return True


def _older_than(stamp: str, seconds: int) -> bool:
    """Is this ISO timestamp further back than `seconds`? Unreadable ⇒ no."""
    from datetime import datetime, timezone

    try:
        t = datetime.fromisoformat(stamp or "")
    except (TypeError, ValueError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() > seconds


def _purge_animatic(job_id: str, owner: str = "") -> None:
    """Delete a project's folder, its record and its ✨ AI Editor chats.

    Shared by DELETE and the sweep.

    ⚠ THE CHATS GO WITH IT, AND THAT IS WHY `owner` IS A PARAMETER. Chats are
    stored keyed by (owner, project), so a deleted film without one would leave
    every conversation about it behind for ever with nothing pointing at them —
    invisible, un-deletable, and still holding what the person typed. An empty
    `owner` sweeps nothing rather than guessing, and says so in the log.

    ⚠ **AND THE RECORD ITSELF — WHICH THIS DID NOT DO, FOR AS LONG AS IT HAS
    EXISTED.** It removed the folder and the chats and left the job row in the
    store, so `DELETE /animatics/{id}` answered 204 and the project came
    straight back on the next visit to the library: same title, same date, and a
    SIZE COLUMN READING "—" because its files really were gone. Reported live on
    2026-09-06 — *"maine abhi delete kiya tha … phir se dekh raha hun to dikha
    raha hai"* — with a screenshot showing exactly that empty size beside a
    project that had been deleted minutes earlier.

    ⚠ **IT COST THE SWEEP TOO, SILENTLY.** `list_animatics` purges ghost
    projects and logs "swept: empty and never named" — but the row survived, so
    the same ghost was re-swept on EVERY list call for ever, rmtree'ing a folder
    that was not there. The log said the work was done each time.

    ⚠ **AND EVERY OTHER DELETE ROUTE IN THIS APP ALREADY DID IT** —
    `main.py`, `plans.py`, `videos.py` all call `get_store().delete(job_id)`.
    Only the projects one had its record deletion in the docstring and not in
    the body.
    """
    folder = _animatic_dir(job_id)
    if os.path.isdir(folder):
        try:
            shutil.rmtree(folder)
        except OSError:
            logger.exception("[animatic %s] could not remove %s", job_id, folder)
    # ⚠ THE RECORD LAST, NOT FIRST. A row removed before its folder is a project
    # nobody can see and nobody can retry the cleanup of — the files would be
    # orphaned on disk with nothing left pointing at them. This order leaves the
    # worst case as "the row is still there and Delete works again", which is
    # recoverable by pressing the button a second time.
    try:
        get_store().delete(job_id)
    except Exception:
        logger.exception("[animatic %s] could not remove its record", job_id)
    if owner:
        try:
            gone = chat_sessions.delete_project(owner, job_id)
            if gone:
                logger.info("[animatic %s] removed %d saved chat(s)", job_id, gone)
        except Exception:
            # ⚠ NEVER THE THING THAT STOPS A DELETE. A chat store that is down
            # must not leave the user unable to remove their own project.
            logger.exception("[animatic %s] could not remove its chats", job_id)
    else:
        logger.warning("[animatic %s] purged with no owner — chats left in place", job_id)


def _summarise(job: Job, boards: dict | None = None) -> AnimaticSummary:
    """One library card.

    ⚠ `boards` IS NOT OPTIONAL WHEN YOU ARE IN A LOOP. The cover's version token
    is `_frame_version`, and for a frame that came from a storyboard that means
    reading the board record. Called with `boards=None`, every card starts a
    FRESH cache and therefore pays its own round trip to the job store — so a
    library of fifty projects made fifty sequential trips to Atlas inside one
    request, which is most of what "the dashboard is slow once I have work in
    it" was. `list_animatics` passes ONE dict for the whole page; a board shared
    by several projects is then read once, not once per card.
    """
    frames = _frames_of(job)
    if boards is None:
        boards = {}
    return AnimaticSummary(
        job_id=job.job_id,
        title=job.character_name or "Project",
        status=job.status,
        aspect_ratio=_settings_of(job).aspect_ratio,
        frame_count=len(frames),
        duration_ms=_duration_ms(frames),
        # Versioned like the frame urls above, so a library card showing a
        # redrawn shot stops being the one picture on screen that never updates.
        cover_url=(
            f"/animatics/{job.job_id}/frame/{frames[0].id}"
            f"?v={_frame_version(job, frames[0], boards)}"
            if frames
            else None
        ),
        text_count=len(_texts_of(job)),
        audio_count=len(_audio_tracks_of(job)),
        has_audio=bool(_audio_tracks_of(job)),
        has_video=bool((job.result or {}).get("video")),
        # Uploads plus any exported video. ⚠ Cached on `updated_at`,
        # so this is a disk walk once per edit and not once per poll
        # — and this library polls every five seconds while an
        # export is running. See `dir_bytes` in common.py.
        size_bytes=dir_bytes(_animatic_dir(job.job_id), job.updated_at),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _mtime_ns(path: str | None) -> int:
    """A file's mtime in NANOSECONDS, or 0 if it isn't there.

    Nanoseconds, not seconds: two redraws of one shot inside the same second are
    easy to do by hand and would otherwise collide back into a stale picture.
    """
    if not path:
        return 0
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def _frame_version(job: Job, frame: AnimaticFrame, boards: dict | None = None) -> str:
    """A token that CHANGES when this frame's picture changes.

    ⚠ THIS IS WHAT MAKES "REGENERATE THIS PANEL" VISIBLE. Every image in this
    app is fetched as an authed blob and cached by URL, and a frame's URL —
    `/animatics/{id}/frame/{frame_id}` — is built from two ids that a redraw
    does not touch. So the panel on the board changed, the animatic pointed at
    the new file, and the editor went on showing the old picture for ever.
    Stamping this into the URL is rule 2 of "regenerating a picture that is
    already on screen": a redrawn image must get a NEW URL.

    ⚠ CHEAP ON PURPOSE — one `stat`, never a decode. `_project_of` calls this
    for every frame on every read and every autosave, so the version of a VIDEO
    clip is taken from its source file and in point rather than by extracting
    the thumbnail those two produce.

    `boards` is a per-request cache of board records. A sixty-pose animatic is
    sixty frames of ONE board, and without it that is sixty round trips to the
    job store to answer one question.
    """
    src = frame.src

    if src.kind == "video":
        # The thumbnail is the frame at the IN POINT, so re-trimming changes the
        # picture without changing the file — the in point is part of the answer.
        path = _video_file(job.job_id, src.upload_id or "")
        return f"{_mtime_ns(path)}-{max(0, int(frame.in_ms or 0))}"

    if src.kind == "upload":
        return str(_mtime_ns(_image_path(job.job_id, src.upload_id or "")))

    if src.kind in ("panel", "pose"):
        board_id = src.storyboard_id or ""
        if not board_id or not _ID_RE.match(board_id) or src.index is None:
            return "0"
        if boards is None:
            boards = {}
        if board_id not in boards:
            board = get_store().get(board_id)
            # Same owner check `_resolve_frame_path` makes, for the same reason:
            # frames are user-editable JSON.
            boards[board_id] = (
                board
                if board is not None
                and board.owner == job.owner
                and board.kind == JobKind.STORYBOARD
                else None
            )
        board = boards[board_id]
        if board is None:
            return "0"
        if src.kind == "pose":
            if src.frame is None:
                return "0"
            return str(
                panel_sequence.frame_version(
                    board_dir(board_id), int(src.index), int(src.frame)
                )
            )
        _, active = variants_of(board.result or {})
        # The VARIANT is in the token as well as the mtime: switching a board's
        # style points this frame at a different file, and two files drawn in
        # the same millisecond is not something to rely on not happening.
        return f"{active}-{_mtime_ns(panel_path(board_id, int(src.index), active))}"

    return "0"


def _resolve_frame_path(job: Job, frame: AnimaticFrame) -> str | None:
    """Local image file behind one frame, or None if it can't be found.

    A board panel is resolved through the board's ACTIVE style variant every
    time, so re-styling or re-drawing on the storyboard shows up in the animatic
    with nothing to re-import.

    ⚠ For a VIDEO clip this answers the THUMBNAIL question — one still, at the
    in point — and nothing more. Which frame a video shows at a given moment is
    decided by `source_at` and resolved during the export; it is not a property
    of the clip and cannot be served from one url.
    """
    src = frame.src
    if src.kind == "video":
        return _video_thumb(job.job_id, frame)

    if src.kind == "upload":
        path = _image_path(job.job_id, src.upload_id or "")
        return path if path and os.path.isfile(path) else None

    if src.kind in ("panel", "pose"):
        board_id = src.storyboard_id or ""
        if not board_id or not _ID_RE.match(board_id) or src.index is None:
            return None
        board = get_store().get(board_id)
        # Owner check matters: frames are user-editable JSON, so without it a
        # crafted board id could read another account's panels.
        if board is None or board.owner != job.owner or board.kind != JobKind.STORYBOARD:
            return None
        if src.kind == "pose":
            if src.frame is None:
                return None
            path = panel_sequence.frame_path(
                board_dir(board_id), int(src.index), int(src.frame)
            )
            return path if os.path.isfile(path) else None
        _, active = variants_of(board.result or {})
        path = panel_path(board_id, int(src.index), active)
        return path if os.path.isfile(path) else None

    return None


# ---------------------------------------------------------------------------
# Create / list / read / save / delete
# ---------------------------------------------------------------------------
def _drawn_board_panels(board: Job) -> list[tuple[int, dict]]:
    """(index, panel) for the board panels that actually have a picture.

    Failed and not-yet-drawn panels are skipped — an animatic made of gaps is
    worse than a shorter one, and the user can add them later.
    """
    variants, active = variants_of(board.result or {})
    return [
        (i, panel or {})
        for i, panel in enumerate(variants[active].get("panels") or [])
        if (panel or {}).get("url") and not (panel or {}).get("failed")
    ]


def _panel_frames_only(board: Job, default_duration_ms: int) -> list[AnimaticFrame]:
    """One held frame per drawn shot — the animatic as it was before key poses.

    The fallback when expanding every pose would blow the frame cap.
    """
    return [
        AnimaticFrame(
            id=uuid.uuid4().hex[:12],
            src={"kind": "panel", "storyboard_id": board.job_id, "index": i},
            duration_ms=default_duration_ms,
            label=f"Shot {i + 1}",
        )
        for i, _ in _drawn_board_panels(board)
    ]


def _board_market(board: Job) -> dict:
    """The audience a storyboard was drawn for, ready to store on a project.

    ⚠ IT LIVES INSIDE THE BOARD'S `world`, not beside it — see schemas.World for
    why the two travel in one dict. This pulls the market half back out so the
    video side can carry it without inheriting the story's wardrobe notes, which
    describe a picture Veo is not drawing.
    """
    import market

    return market.coerce((board.params or {}).get("world") or {})


def _project_market(job: Job) -> dict:
    """This project's audience, or {} — the {} being a real answer (no money)."""
    return dict((job.params or {}).get("market") or {})


def _localise_veo(prompt: str, negative: str | None, job: Job) -> tuple[str, str | None]:
    """Put the project's audience into a Veo call.

    ⚠ THE SAME BUG CROSSES THE HANDOFF. The reported board was re-rendered with
    Veo and the app UI came back priced in dollars again — the storyboard half
    had been taught the market and the video half had not, so the one film was
    made twice in two currencies.

    ⚠ THE RULE IS APPENDED IN ENGLISH, and so is the rest of the motion prompt.
    `director.language_instruction()` already settled this and for a measured
    reason: Veo follows English instructions better, so the INSTRUCTION stays
    English while what appears on screen takes the audience's language. This
    only ever describes what may be written in frame; it never translates the
    camera move.
    """
    import market

    audience = _project_market(job)
    rule = market.on_screen_text_rule(audience)
    unwanted = market.negative_terms(audience)
    return (
        f"{prompt}\n\n{rule}",
        ", ".join(p for p in ((negative or "").strip(), unwanted) if p),
    )


def _frames_from_board(board: Job, default_duration_ms: int) -> list[AnimaticFrame]:
    """A board, in order, as animatic frames — a shot's KEY POSES where it has
    them, otherwise the single panel.

    THE FLIPBOOK. A shot that has been through Image to Animatic Image already
    has ~4 drawings per second of screen time, blocked out to carry its motion.
    This used to ignore every one of them and lay down the one still panel, so
    the work the user paid for never reached the animatic and the animatic was
    a slideshow. Now those poses come across at their real rate
    (1000/KEY_POSES_PER_SECOND ms each, so the shot runs for the length it was
    planned as), and only shots with no sequence fall back to a held panel.

    Poses are REFERENCED, never copied, exactly as panels are: redrawing a pose
    on the board updates the animatic with nothing to re-import.

    Failed and not-yet-drawn panels are skipped — see _drawn_board_panels.
    """
    sequences = (board.result or {}).get("sequences") or {}
    # A pose is a QUARTER of a second at the rate the sequence was planned at,
    # not `default_duration_ms` — holding each drawing for two seconds would
    # turn four seconds of animation into half a minute of stills.
    pose_ms = max(100, round(1000 / panel_sequence.KEY_POSES_PER_SECOND))

    frames: list[AnimaticFrame] = []
    for i, _panel in _drawn_board_panels(board):
        planned = int((sequences.get(str(i)) or {}).get("planned") or 0)
        poses = panel_sequence.frames_on_disk(board_dir(board.job_id), i, planned)
        if poses:
            for position, n in enumerate(poses, start=1):
                frames.append(
                    AnimaticFrame(
                        id=uuid.uuid4().hex[:12],
                        src={
                            "kind": "pose",
                            "storyboard_id": board.job_id,
                            "index": i,
                            "frame": n,
                        },
                        duration_ms=pose_ms,
                        label=f"Shot {i + 1}.{position}",
                    )
                )
            continue

        frames.append(
            AnimaticFrame(
                id=uuid.uuid4().hex[:12],
                src={"kind": "panel", "storyboard_id": board.job_id, "index": i},
                duration_ms=default_duration_ms,
                label=f"Shot {i + 1}",
            )
        )
    return frames


@router.post("", response_model=AnimaticProject, status_code=201)
def create_animatic(
    body: AnimaticCreateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature("workflow.storyboard-to-animatics")),
    _quota: CurrentUser = Depends(require_quota("projects")),
):
    """Start a new animatic — empty, or pre-filled from a storyboard.

    Passing `source_storyboard_id` with no frames is the board's "Make animatic"
    button: every drawn panel becomes a frame, in order, at the default hold.
    """
    settings = body.settings or AnimaticSettings()
    frames = list(body.frames)
    title = (body.title or "").strip()
    source_id = body.source_storyboard_id

    if source_id:
        board = get_owned_job(source_id, current)
        if board.kind != JobKind.STORYBOARD:
            raise HTTPException(status_code=400, detail="That isn't a storyboard.")
        if not frames:
            frames = _frames_from_board(board, body.default_duration_ms)
            if not frames:
                raise HTTPException(
                    status_code=409,
                    detail="That storyboard has no drawn panels yet — generate some first.",
                )
            # A long board whose shots all have key poses can out-run the frame
            # cap. Falling back to one frame per shot beats refusing to make the
            # animatic at all — the user can add the poses of the shots they
            # care about by hand.
            if len(frames) > config.MAX_ANIMATIC_FRAMES:
                logger.info(
                    "[animatic] board %s expands to %d key-pose frames (cap %d) — "
                    "falling back to one frame per shot.",
                    source_id, len(frames), config.MAX_ANIMATIC_FRAMES,
                )
                frames = _panel_frames_only(board, body.default_duration_ms)
        if not title:
            title = f"{board.character_name or 'Storyboard'} — project"
        # Inherit the board's frame shape so panels aren't letterboxed by default.
        if body.settings is None:
            settings.aspect_ratio = (board.params or {}).get("aspect_ratio") or settings.aspect_ratio

    if len(frames) > config.MAX_ANIMATIC_FRAMES:
        raise HTTPException(
            status_code=413,
            detail=f"A project can hold at most {config.MAX_ANIMATIC_FRAMES} frames.",
        )

    # ⚠ THE SAME PLACEHOLDER THE CLIENT USES — `UNTITLED` in
    # client/src/components/AnimaticLibrary.jsx, which the editor compares
    # against to decide whether Save must ask for a real name. Only reachable
    # from a direct API call (the New tile sends the title itself), but a
    # mismatch here would be a project the editor treats as already named.
    usage_counters.increment(current.email, "projects")
    job = get_store().create(
        character_name=title or "Untitled Project",
        kind=JobKind.ANIMATIC,
        owner=current.email,
        params={
            "settings": settings.model_dump(),
            "frames": [f.model_dump(exclude={"url"}) for f in frames],
            "texts": [],
            "shapes": [],
            "layers": [],
            "overlays": [],
            "transitions": [],
            "audio_tracks": [],
            "source_storyboard_id": source_id,
            # ⚠ THE BOARD'S AUDIENCE, CARRIED ACROSS THE HANDOFF. Without this
            # the video half starts from nothing again: the panels were drawn in
            # ₹ and the Veo re-render of the same shot came back in `$`, in the
            # same film. Copied rather than looked up so the project keeps
            # working if the board is later deleted, and so editing the board's
            # market cannot silently relight a video already being cut.
            "market": _board_market(board) if source_id else {},
        },
    )
    os.makedirs(_media_dir(job.job_id), exist_ok=True)
    logger.info(
        "[animatic %s] created by %s (%d frame(s)%s)",
        job.job_id, current.email, len(frames),
        f", from board {source_id}" if source_id else "",
    )
    return _project_of(job)


@router.get("", response_model=list[AnimaticSummary])
def list_animatics(
    limit: int = 100,
    current: CurrentUser = Depends(get_current_user),
):
    """The caller's saved animatics, newest first (the library grid).

    ⚠ ONE `boards` CACHE FOR THE WHOLE PAGE, not one per card — see `_summarise`.
    """
    jobs = get_store().list(
        limit=limit,
        owner=current.email,
        kinds=[JobKind.ANIMATIC],
        # ⚠ THE EDITOR STILL GETS EVERYTHING — this is the LIST route, and
        # `GET /animatics/{id}` is untouched. See SUMMARY_DROP.
        drop=SUMMARY_DROP,
    )
    boards: dict = {}
    cards: list[AnimaticSummary] = []
    swept = 0
    for j in jobs:
        card = _summarise(j, boards)
        # ⚠ EMPTY + NEVER NAMED = NOT A PROJECT. It is never listed, and once it
        # is old enough to be certainly dead it is swept off disk too — see
        # `_is_ghost`. Failures here are logged and ignored: a stuck folder must
        # never be what stops the library loading.
        if _is_ghost(j, card):
            if swept < GHOST_SWEEP_PER_CALL and _older_than(
                j.updated_at or j.created_at, GHOST_MAX_AGE_S
            ):
                swept += 1
                try:
                    _purge_animatic(j.job_id, j.owner or "")
                    logger.info("[animatic %s] swept: empty and never named", j.job_id)
                except Exception:
                    logger.exception("[animatic %s] sweep failed", j.job_id)
            continue
        cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# LUTs
# ---------------------------------------------------------------------------
# ⚠ DECLARED BEFORE `/{job_id}`. FastAPI matches routes in declaration order, so
# putting these after it would make "luts" read as a job id and every request
# 404 as somebody else's animatic.
#
# A LUT is a FILE both sides read: `animatic_effects.py` loads it with
# `Color3DLUT` for the export and the browser fetches the same bytes into a
# texture for the monitor. Serving it rather than inlining the numbers is what
# stops there being a second copy of the table to drift — the mistake
# `_SHAPE_POINTS` made and is still paying for.
@router.get("/luts", response_model=list[str])
def list_luts(current: CurrentUser = Depends(get_current_user)):
    """The built-in LUT names, for the Effects pane's dropdown."""
    import animatic_effects

    return animatic_effects.list_luts()


@router.get("/luts/{name}")
def get_lut(name: str, current: CurrentUser = Depends(get_current_user)):
    """One built-in .cube file, for the monitor's WebGL compositor.

    `lut_path` validates the name against a strict pattern before it touches the
    filesystem — the same rule `_image_path` follows, and for the same reason: a
    name arrives inside a saved project and a project can be edited by hand.
    """
    import animatic_effects

    path = animatic_effects.lut_path(name)
    if not path:
        raise HTTPException(status_code=404, detail=f"No LUT named '{name}'.")
    return FileResponse(path, media_type="text/plain", filename=f"{name}.cube")


@router.get("/image-model", response_model=AnimaticImageBackend)
def get_image_model(current: CurrentUser = Depends(get_current_user)):
    """FREE. Which image model a ✨ in this editor would call.

    ⚠ DECLARED BEFORE `/{job_id}`, like `/luts` above it — a path segment that is
    not an id has to be matched before the catch-all, or "image-model" is read as
    a project id and 404s.

    ⚠ AND IT TAKES NO PROJECT, because the answer does not depend on one: the
    model comes from `IMAGE_PROVIDER` / `*_IMAGE_MODEL` in the environment. It is
    still authed — it names infrastructure, and there is no reason to tell the
    world which backend this instance runs.
    """
    provider, model = _image_model_id(None)
    return AnimaticImageBackend(model=model, provider=provider)


@router.get("/{job_id}", response_model=AnimaticProject)
def get_animatic(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """The full project: frames, timing, audio and settings."""
    return _project_of(_get_owned_animatic(job_id, current))


@router.put("/{job_id}", response_model=AnimaticProject)
def save_animatic(
    job_id: str,
    body: AnimaticSaveRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Save the edited project (the editor's autosave).

    Refused while an export is running: the encoder is reading these exact
    frames, so letting them change underneath it would produce a video that
    matches neither the old nor the new project.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is exporting — wait for it to finish, or stop it first.",
        )

    params = dict(job.params or {})
    fields: dict = {}

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty.")
        fields["character_name"] = title[:120]

    if body.settings is not None:
        params["settings"] = body.settings.model_dump()

    if body.frames is not None:
        if len(body.frames) > config.MAX_ANIMATIC_FRAMES:
            raise HTTPException(
                status_code=413,
                detail=f"A project can hold at most {config.MAX_ANIMATIC_FRAMES} frames.",
            )
        params["frames"] = [f.model_dump(exclude={"url"}) for f in body.frames]

    if body.texts is not None:
        if len(body.texts) > config.MAX_ANIMATIC_TEXTS:
            raise HTTPException(
                status_code=413,
                detail=f"A project can hold at most {config.MAX_ANIMATIC_TEXTS} text clips.",
            )
        params["texts"] = [t.model_dump() for t in body.texts]

    if body.shapes is not None:
        if len(body.shapes) > config.MAX_ANIMATIC_SHAPES:
            raise HTTPException(
                status_code=413,
                detail=f"A project can hold at most {config.MAX_ANIMATIC_SHAPES} shapes.",
            )
        params["shapes"] = [s.model_dump() for s in body.shapes]

    if body.layers is not None:
        if len(body.layers) > config.MAX_ANIMATIC_LAYERS:
            raise HTTPException(
                status_code=413,
                detail=f"A project can hold at most {config.MAX_ANIMATIC_LAYERS} layers.",
            )
        params["layers"] = [layer.model_dump() for layer in body.layers]

    if body.assets is not None:
        if len(body.assets) > config.MAX_ANIMATIC_ASSETS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"The media library can hold at most {config.MAX_ANIMATIC_ASSETS} items."
                ),
            )
        # `url` is filled on read, so it is never stored — see AnimaticAsset.
        params["assets"] = [a.model_dump(exclude={"url"}) for a in body.assets]

    if body.overlays is not None:
        if len(body.overlays) > config.MAX_ANIMATIC_SHAPES:
            raise HTTPException(
                status_code=413,
                detail=f"A project can hold at most {config.MAX_ANIMATIC_SHAPES} overlays.",
            )
        # `url` is filled on read, so it is never stored — see AnimaticOverlay.
        params["overlays"] = [o.model_dump(exclude={"url"}) for o in body.overlays]

    if body.transitions is not None:
        if len(body.transitions) > config.MAX_ANIMATIC_TRANSITIONS:
            raise HTTPException(
                status_code=413,
                detail=f"A project can hold at most {config.MAX_ANIMATIC_TRANSITIONS} transitions.",
            )
        params["transitions"] = [t.model_dump() for t in body.transitions]

    if body.audio_tracks is not None:
        # ⚠ TWO CAPS, and they count two different things. The old one is a cap
        # on FILES — that is what an upload costs and what the storage bill is —
        # and cutting a track with the razor adds neither. The second bounds the
        # number of CLIPS, so a runaway client still can't write an unbounded
        # list, but it is loose enough that nobody editing by hand meets it.
        files = {a.upload_id for a in body.audio_tracks if a.upload_id}
        if len(files) > config.MAX_ANIMATIC_AUDIO_TRACKS:
            raise HTTPException(
                status_code=413,
                detail=f"A project can hold at most {config.MAX_ANIMATIC_AUDIO_TRACKS} audio tracks.",
            )
        if len(body.audio_tracks) > config.MAX_ANIMATIC_AUDIO_CLIPS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"A project can hold at most {config.MAX_ANIMATIC_AUDIO_CLIPS} "
                    "audio clips — that is the razor's limit, not the number of files."
                ),
            )
        # Every clip needs an identity, and a client that predates the razor
        # sends none. Filled here as well as on read, so the id a project is
        # SAVED with is the one it is read back with.
        for a in body.audio_tracks:
            if not a.id:
                a.id = a.upload_id
        params["audio_tracks"] = [a.model_dump(exclude={"url"}) for a in body.audio_tracks]
        # Drop the pre-multi-track field so it can't be resurrected by the
        # migration path on a later read.
        params.pop("audio", None)

    fields["params"] = params

    # The exported file no longer matches what's on screen. Say so rather than
    # deleting it — the old cut is still worth having until the new one exists.
    result = dict(job.result or {})
    if result.get("video"):
        video = dict(result["video"])
        video["stale"] = True
        result["video"] = video
        fields["result"] = result

    updated = get_store().update(job_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Project '{job_id}' not found.")
    return _project_of(updated)


@router.delete("/{job_id}", status_code=204)
def delete_animatic(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Delete an animatic: its record, its uploads and its exported video.

    Board panels are NOT touched — the animatic only ever referenced them.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is still exporting — wait for it to finish first.",
        )
    _purge_animatic(job_id, job.owner or "")
    return None


# ---------------------------------------------------------------------------
# Media: upload images / audio, serve them back
# ---------------------------------------------------------------------------
@router.post("/{job_id}/images", response_model=AnimaticUploadResponse)
async def upload_images(
    job_id: str,
    files: list[UploadFile] = File(..., description="Images to add as frames."),
    current: CurrentUser = Depends(get_current_user),
):
    """Upload one or many images into this animatic.

    They are stored but NOT added to the sequence — the client decides the order
    (it sorts a multi-file drop by filename) and saves the project afterwards.
    Unreadable files are named in `rejected` rather than silently dropped, which
    matters when 40 files are dragged in at once.
    """
    from PIL import Image as PILImage

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")

    media = _media_dir(job_id)
    os.makedirs(media, exist_ok=True)

    items: list[AnimaticMediaItem] = []
    rejected: list[str] = []

    for upload in files:
        name = upload.filename or "image"
        contents = await upload.read()
        if len(contents) > config.MAX_UPLOAD_BYTES:
            rejected.append(f"{name} (larger than {config.MAX_UPLOAD_BYTES // 1_048_576} MB)")
            continue
        upload_id = uuid.uuid4().hex[:12]
        path = _image_path(job_id, upload_id)
        try:
            # Normalise whatever came in (jpg/webp/…) to a clean PNG, exactly as
            # the character-reference upload does — and KEEP the transparency
            # when the source has any, or a cut-out logo is stored as a black
            # card. See `_keeps_alpha`.
            with PILImage.open(io.BytesIO(contents)) as im:
                flat = im.convert("RGBA" if _keeps_alpha(im) else "RGB")
                flat.save(path, "PNG")
                width, height = flat.size
        except Exception:  # noqa: BLE001 — bad/corrupt upload
            rejected.append(f"{name} (not a readable image)")
            continue
        items.append(
            AnimaticMediaItem(upload_id=upload_id, filename=name, width=width, height=height)
        )

    logger.info(
        "[animatic %s] %d image(s) uploaded, %d rejected", job_id, len(items), len(rejected)
    )
    return AnimaticUploadResponse(items=items, rejected=rejected)



# ---------------------------------------------------------------------------
# ONE IMAGE FROM ONE SENTENCE — the Media pane's ✨
# ---------------------------------------------------------------------------
# ⚠ THE SIBLING OF THE UPLOAD ABOVE, AND DELIBERATELY SO: it answers with the
# same `AnimaticMediaItem`, writes to the same folder under the same id space,
# and is placed by the client exactly as an uploaded file is. From the moment it
# returns, nothing downstream can tell the two apart — which is what lets a
# generated picture list in Media, drag onto a lane, export and delete without a
# single code path learning that it was generated.
#
# ⚠ IT IS NOT THE SHOT GENERATOR WITH THE BOARD LEFT OUT. That one draws a SHOT:
# the board's style, its references, its bible, its neighbours, its row. This one
# draws whatever the sentence says and belongs to nothing — see the note on
# `AnimaticImageGenerateRequest`, and `gemini_client.generate_image`, which is
# the only image call in this codebase that imposes no art direction.
def _image_name_from_prompt(prompt: str, limit: int = 42) -> str:
    """A library card's name, from the words that made the picture.

    The opening of the prompt, cut at a WORD boundary — the person who typed it
    recognises the card by its first few words, and "A neon-lit alley in the ra…"
    is a name where "Generated image 3" is a filing reference. Falls back to a
    plain label for a prompt that is all punctuation.
    """
    words = " ".join(str(prompt or "").split())
    if len(words) <= limit:
        return words or "Generated image"
    cut = words[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (cut or words[:limit]).rstrip() + "…"


@router.post("/{job_id}/images/generate", response_model=AnimaticGeneratedImage)
def generate_animatic_image(
    job_id: str,
    body: AnimaticImageGenerateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
):
    """SPENDS QUOTA. Draw one picture from a sentence, and store it as an upload.

    One image, synchronously — the dialog waits on it, which is why the retry
    policy behind `generate_image` fails fast on anything a retry cannot fix.

    ⚠ IT IS RETURNED, NOT PLACED. The client decides where it goes, the same
    contract every other "the server makes the material" route here follows —
    which for this one means the Media library and the overlay Images lane.
    """
    from gemini_client import generate_image
    from storyboard_pipeline import _crop_to_aspect

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Describe the picture you want.")
    aspect = (body.aspect_ratio or "").strip() or _settings_of(job).aspect_ratio or "16:9"

    try:
        image = generate_image(prompt, aspect_ratio=aspect)
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[animatic %s] image generation failed", job_id)
        raise HTTPException(status_code=502, detail=f"Could not draw it: {e}") from None
    if image is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "The model returned no picture (it may have been blocked by a "
                "safety filter). Try describing it differently."
            ),
        )

    # ⚠ CROPPED AS WELL AS ASKED FOR. `generate_image` sends the ratio through
    # the SDK's `image_config`, which an older google-genai does not carry — and
    # a model can round it anyway. This is the guarantee: the picture the client
    # places is the shape the dialog said it would be. A no-op when it already is.
    image = _crop_to_aspect(image, aspect)

    upload_id = uuid.uuid4().hex[:12]
    os.makedirs(_media_dir(job_id), exist_ok=True)
    image.save(_image_path(job_id, upload_id), "PNG")
    logger.info(
        "[animatic %s] drew an image (upload %s, %dx%d) for %s",
        job_id, upload_id, image.width, image.height, current.email,
    )
    return AnimaticGeneratedImage(
        item=AnimaticMediaItem(
            upload_id=upload_id,
            filename=f"{upload_id}.png",
            width=image.width,
            height=image.height,
        ),
        name=_image_name_from_prompt(prompt),
        model=_image_model_id(None)[1],
    )


@router.post("/{job_id}/import-storyboard", response_model=AnimaticBoardImportResponse)
def import_storyboard(
    job_id: str,
    body: AnimaticBoardImportRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Read a board's drawn panels as frames, for an animatic that ALREADY EXISTS.

    ⚠ NOT THE SAME JOB AS `source_storyboard_id` ON CREATE, though it shares the
    builder. That one fills a brand-new project and saves it; this one is pressed
    in the middle of a cut, so it RETURNS the frames and saves nothing — where
    they land is the editor's decision, and it puts them on a row of their own
    ("Storyboard images"). Same contract as the image and video uploads: the
    server produces the material, the client decides the timeline.

    ⚠ BOTH JOBS ARE OWNERSHIP-CHECKED. The animatic is checked because we are
    about to hand back its own future content, and the BOARD is checked
    separately because a board id is a user-supplied string — without the second
    check this would read any storyboard on the instance by id.
    """
    job = _get_owned_animatic(job_id, current)
    board = get_owned_job(body.storyboard_id, current)
    if board.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=400, detail="That isn't a storyboard.")

    frames = _frames_from_board(board, body.default_duration_ms)
    if not frames:
        raise HTTPException(
            status_code=409,
            detail="That storyboard has no drawn panels yet — generate some first.",
        )

    # ⚠ THE CAP IS COUNTED AGAINST WHAT IS ALREADY ON THE TIMELINE, which is the
    # difference that matters here: on create the project is empty, so the board
    # only had to fit on its own. Falling back to one frame per shot beats
    # refusing the import outright — the user can bring the poses of the shots
    # they care about across by hand.
    existing = len((job.params or {}).get("frames") or [])
    panels_only = False
    if existing + len(frames) > config.MAX_ANIMATIC_FRAMES:
        frames = _panel_frames_only(board, body.default_duration_ms)
        panels_only = True
        logger.info(
            "[animatic %s] board %s expands past the frame cap (%d already here, cap %d) "
            "— importing one frame per shot.",
            job_id, body.storyboard_id, existing, config.MAX_ANIMATIC_FRAMES,
        )
    if existing + len(frames) > config.MAX_ANIMATIC_FRAMES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That board needs {len(frames)} frames and this project already has "
                f"{existing}, over the limit of {config.MAX_ANIMATIC_FRAMES}."
            ),
        )

    title = (board.character_name or "Storyboard").strip()
    logger.info(
        "[animatic %s] imported %d frame(s) from board %s%s",
        job_id, len(frames), body.storyboard_id, " (panels only)" if panels_only else "",
    )
    return AnimaticBoardImportResponse(
        frames=frames,
        # The row's name comes from here so it is built once rather than on both
        # sides of the wire.
        name=f"{title} — storyboard",
        title=title,
        panels_only=panels_only,
    )


@router.post("/{job_id}/videos", response_model=AnimaticVideoUploadResponse)
async def upload_videos(
    job_id: str,
    files: list[UploadFile] = File(..., description="Video clips to add to the timeline."),
    current: CurrentUser = Depends(get_current_user),
):
    """Upload one or many video clips into this animatic.

    Like the image upload, they are STORED but not sequenced — the client
    decides where on the timeline they land and saves the project afterwards.

    The file is kept as-is: ffmpeg reads it at export and the browser plays it
    directly in the Program monitor, so the server never has to transcode. What
    it DOES do is measure each one, because unlike audio (which the browser
    reports on) the clip's natural length has to be the same number the exporter
    will work from — one measurer, one answer. A file we can't measure is still
    accepted; it simply opens at the default hold.
    """
    import video_frames

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")

    media = _media_dir(job_id)
    os.makedirs(media, exist_ok=True)

    items: list[AnimaticVideoItem] = []
    rejected: list[str] = []

    for upload in files:
        name = upload.filename or "clip.mp4"
        ext = os.path.splitext(name)[1].lower()
        if (
            upload.content_type not in config.ALLOWED_VIDEO_TYPES
            and ext not in config.ALLOWED_VIDEO_EXTS
        ):
            rejected.append(f"{name} (not a video we can use — try MP4, MOV or WebM)")
            continue
        if ext not in config.ALLOWED_VIDEO_EXTS:
            ext = ".mp4"

        contents = await upload.read()
        if len(contents) > config.MAX_VIDEO_BYTES:
            rejected.append(
                f"{name} (larger than {config.MAX_VIDEO_BYTES // 1_048_576} MB)"
            )
            continue

        upload_id = uuid.uuid4().hex[:12]
        path = os.path.join(media, f"vid_{upload_id}{ext}")
        try:
            with open(path, "wb") as f:
                f.write(contents)
        except OSError:
            logger.exception("[animatic %s] could not store video %s", job_id, name)
            rejected.append(f"{name} (couldn't be stored)")
            continue

        # ⚠ NOT ffprobe — there isn't one on an imageio-ffmpeg install. See the
        # note at the top of video_frames.probe_duration.
        duration_ms = video_frames.probe_duration(path)
        if not duration_ms:
            logger.info("[animatic %s] couldn't measure %s — clip opens at the default hold",
                        job_id, name)
        items.append(
            AnimaticVideoItem(
                upload_id=upload_id,
                filename=name,
                duration_ms=duration_ms,
                # By upload id: playable immediately, before the project is
                # saved. Exactly the rule the audio upload follows, and for the
                # same reason — the editor's save is debounced.
                url=f"/animatics/{job_id}/media/{upload_id}",
            )
        )

    logger.info(
        "[animatic %s] %d video(s) uploaded, %d rejected", job_id, len(items), len(rejected)
    )
    return AnimaticVideoUploadResponse(items=items, rejected=rejected)


@router.post("/{job_id}/audio", response_model=AnimaticAudioResponse)
async def upload_audio(
    job_id: str,
    file: UploadFile = File(..., description="The audio track (MP3, WAV, M4A…)."),
    current: CurrentUser = Depends(get_current_user),
):
    """Upload an audio track and return its id. ADDS a track; replaces nothing.

    The file is stored as-is: ffmpeg reads it directly at export, and the
    browser decodes it for the waveform and for playback. The server never has
    to understand the format, which is why no audio library is needed here.

    Which tracks an animatic HAS is decided by the saved project, not by what
    is on disk — so this only puts the file there and hands back the id.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")

    name = file.filename or "audio.mp3"
    ext = os.path.splitext(name)[1].lower()
    if file.content_type not in config.ALLOWED_AUDIO_TYPES and ext not in config.ALLOWED_AUDIO_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"'{name}' isn't an audio file we can use. Try MP3, WAV or M4A.",
        )
    if ext not in config.ALLOWED_AUDIO_EXTS:
        ext = ".mp3"

    contents = await file.read()
    if len(contents) > config.MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio too large. Max is {config.MAX_AUDIO_BYTES // 1_048_576} MB.",
        )

    media = _media_dir(job_id)
    os.makedirs(media, exist_ok=True)
    # Deliberately does NOT clear existing audio — several tracks can coexist
    # now (music under a voiceover). Files belonging to removed tracks are left
    # on disk and go when the animatic is deleted; the project decides what
    # actually plays.
    upload_id = uuid.uuid4().hex[:12]
    with open(os.path.join(media, f"audio_{upload_id}{ext}"), "wb") as f:
        f.write(contents)

    logger.info("[animatic %s] audio uploaded: %s (%d bytes)", job_id, name, len(contents))
    return AnimaticAudioResponse(
        upload_id=upload_id, filename=name,
        # By upload id: usable immediately, before the project is saved.
        url=f"/animatics/{job_id}/media/{upload_id}"
    )


@router.post("/{job_id}/sounds", response_model=SoundImportResponse)
def import_sound(
    job_id: str,
    body: SoundImportRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Bring one Freesound sound into this project, AS AN ORDINARY AUDIO UPLOAD.

    ⚠ IT LANDS EXACTLY WHERE A DROPPED MP3 LANDS — same `audio_<upload_id>.mp3`
    in the same media directory, same `/media/{upload_id}` to serve it, same
    `AnimaticAudio` track and same library card. That is the whole design: a
    sound the user found in the Sounds tab must be indistinguishable from a sound
    they dragged in, or every downstream thing — the razor, the waveform, the
    exporter, the "fit frames to audio" button — would need a second code path
    for it. The ONLY thing that survives the import as different is the credit
    line, and it rides on the library card (`AnimaticAsset.attribution`).

    ⚠ THE BROWSER SENDS AN ID, NEVER A URL. `freesound.fetch_preview` re-asks
    Freesound where the file is; the alternative — trusting the `preview_url` the
    search result already put on screen — would be our server fetching whatever
    address a crafted request handed it.

    ⚠ AND THE LICENCE IS RE-CHECKED HERE, not just at search time. Search fences
    NonCommercial sounds out of the LIST; this route fences them out of the
    PROJECT, because the id in this body did not have to come from that list.
    `freesound.sound` raises for a licence we do not offer, and 400 is the
    answer — the request is wrong, not the upstream service.

    Spends no AI quota. It does spend the deployment's shared Freesound rate
    limit: one API call for the metadata, then a plain CDN download.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")

    # Counted in FILES, the same rule the voiceover route follows and the same
    # one the editor mirrors: a track razored into four clips is still one file.
    if len(_audio_files_of(job)) >= config.MAX_ANIMATIC_AUDIO_TRACKS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This project already has {config.MAX_ANIMATIC_AUDIO_TRACKS} audio "
                "tracks. Remove one before adding a sound."
            ),
        )

    try:
        data, filename, item = freesound.fetch_preview(
            body.sound_id, config.MAX_AUDIO_BYTES
        )
    except freesound.FreesoundError as exc:
        text = str(exc)
        # A refused LICENCE or a malformed id is the caller's mistake; anything
        # else — no key, a timeout, a 5xx — is the upstream service's.
        bad_request = "NonCommercial" in text or "not a Freesound sound id" in text
        raise HTTPException(status_code=400 if bad_request else 502, detail=text) from exc

    media = _media_dir(job_id)
    os.makedirs(media, exist_ok=True)
    upload_id = uuid.uuid4().hex[:12]
    with open(os.path.join(media, f"audio_{upload_id}.mp3"), "wb") as fh:
        fh.write(data)

    logger.info(
        "[animatic %s] freesound %s imported as %s (%d bytes, %s)",
        job_id, item["id"], upload_id, len(data), item["license"],
    )
    return SoundImportResponse(
        upload_id=upload_id,
        filename=filename,
        url=f"/animatics/{job_id}/media/{upload_id}",
        # ⚠ FREESOUND'S OWN DURATION, not something we measured. There is no
        # ffprobe on an `imageio-ffmpeg` install, so the server cannot measure an
        # audio file at all — the browser normally does it (`measureAudio`). This
        # number means the card is right the instant it appears, and the editor
        # still re-measures once the blob is loaded.
        duration_ms=int(item.get("duration_ms") or 0),
        attribution=item.get("attribution") or "",
        license=item.get("license") or "cc0",
        license_label=item.get("license_label") or "",
        needs_credit=bool(item.get("needs_credit")),
        page_url=item.get("page_url") or "",
    )


# How many results to look at per cue before giving up on it. ⚠ ONE REQUEST
# EITHER WAY — the page size costs nothing extra, and looking at eight rather than
# one means a cue whose best match has no mp3 preview still finds a sound.
_SOUNDTRACK_LOOK = 8

# ⚠ MOST-DOWNLOADED, NOT BEST-MATCH, AND THAT IS A DELIBERATE DEMOTION OF
# RELEVANCE. Nobody is auditioning these: the pass takes the first result and
# lays it on the timeline. Freesound's relevance ranking will happily put a
# 0.3-second phone recording of a door at the top for "heavy door slam", and it
# IS the most relevant thing to those words — it is just not a sound you can put
# in a film. Download count is the closest thing the catalogue has to "other
# editors used this and did not regret it", and it is the right tie-breaker when
# the choice is being made by something that cannot listen.
_SOUNDTRACK_SORT = "downloads"

# ⚠ HOW MANY WAYS ONE CUE IS ASKED FOR BEFORE IT IS GIVEN UP ON. Two, and the
# ceiling is the shared rate limit: ten cues at two attempts is twenty requests
# out of sixty a minute for the WHOLE deployment, which is a run and a bit. Every
# cue that lands on the first attempt costs one, which is most of them.
_SOUNDTRACK_ATTEMPTS = 2


def _cue_attempts(query: str, max_seconds: float, min_seconds: float) -> list[dict]:
    """THE LADDER: the same cue, asked for in narrowing-to-widening order.

    ⚠ THIS EXISTS BECAUSE A FOUR-WORD CUE FINDS NOTHING AND THE FILM GOES SILENT.
    Reported from the screen: "ambient peaceful piano underscore" and "light
    feather rustle" both came back with zero CC0 results, so a plan that promised
    two sound effects and a music bed delivered one effect and no music. The
    library matches EVERY word in the query, so a model that writes one adjective
    too many is a shot with no sound — and the model is instructed not to (see
    `director.sound_instruction`), but an instruction is not a guarantee and the
    cost of being wrong is the whole feature looking broken.

    So each cue gets a SECOND, WIDER attempt before it is written off:

      1. the cue as written, with the length filter for its kind
      2. ⚠ THE LAST TWO WORDS, WITH NO LENGTH FILTER AT ALL

    ⚠ THE LAST TWO WORDS RATHER THAN THE FIRST TWO, because English puts the head
    noun at the END of a noun phrase: the searchable part of "light feather
    rustle" is "feather rustle", and of "ambient peaceful piano underscore" is
    "piano underscore". Dropping from the front throws away adjectives, which is
    exactly what one wants to throw away.

    ⚠ AND THE LENGTH FILTER GOES ENTIRELY ON THE SECOND PASS. It is the other half
    of why these came back empty: a CC0 filter and a "no shorter than 12 seconds"
    filter and four rare words leave nothing standing. Nothing downstream needs
    the bound — a bed shorter than the film is LOOPED (`musicPlacement`) and an
    effect longer than its shot is clamped to the end of the film by
    `trackPlayMs` — so the filter is a preference on the first attempt and never
    a requirement.
    """
    words = (query or "").split()
    attempts = [
        {
            "query": query,
            "max_seconds": max(0.0, float(max_seconds or 0)),
            "min_seconds": max(0.0, float(min_seconds or 0)),
            "relaxed": "",
        }
    ]
    if len(words) > 2:
        wider = " ".join(words[-2:])
    elif len(words) == 2:
        # Two words already: the only thing left to widen is the length filter,
        # and a single word out of two is usually the adjective's turn to go.
        wider = words[-1]
    else:
        wider = query
    attempts.append(
        {
            "query": wider,
            "max_seconds": 0.0,
            "min_seconds": 0.0,
            # Printed on screen when it is the one that found the sound, because
            # "we searched for something else and used what came back" is not a
            # thing to do quietly.
            "relaxed": wider if wider != query else "any length",
        }
    )
    # Identical attempts are not worth a request out of a shared budget.
    seen: set[tuple] = set()
    out: list[dict] = []
    for attempt in attempts[:_SOUNDTRACK_ATTEMPTS]:
        key = (attempt["query"], attempt["max_seconds"], attempt["min_seconds"])
        if key in seen:
            continue
        seen.add(key)
        out.append(attempt)
    return out


@router.post("/{job_id}/soundtrack", response_model=SoundtrackResponse)
def build_soundtrack(
    job_id: str,
    body: SoundtrackRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """THE DIRECTOR'S PHASES D AND E: find a film's worth of sound, and file it in.

    ⚠ IT IS `POST /sounds` IN A LOOP, WITH THE THREE THINGS A LOOP NEEDS. Every
    sound that lands here lands exactly where a hand-picked one does — same
    `audio_<upload_id>.mp3`, same media directory, same serve route, same
    `AnimaticAudio` track and same library card — because a Director-placed sound
    the razor or the exporter had to treat differently would be a second code
    path for no gain. What this route adds over calling that one eleven times is:

      1. ONE CAP CHECK, against the project as it is NOW. Eleven separate imports
         would each measure the room the previous ten just used up, so the tail of
         the list would be refused for a reason the user cannot act on.
      2. DEDUPE. Six shots that all cue "footsteps on gravel" are one search, one
         download and one file — which is also the right film: one recording of
         gravel is one place, six different ones is six places.
      3. A PARTIAL ANSWER. A cue that finds nothing is a row in `skipped`, never a
         failed request: ten of eleven landing is a film with sound in it.

    ⚠ CC0 ONLY, WHICH IS A TIGHTER FENCE THAN THE SOUNDS TAB'S. The tab offers CC
    BY as well, because the person clicking can read the "credit needed" badge and
    accept the obligation. NOBODY IS READING A BADGE HERE — this files eleven
    sounds while the user watches a progress line — and an attribution obligation
    the customer never agreed to is one they discover when somebody complains
    about their published video. `licence="safe"` is not a default here, it is the
    rule, and it is not a parameter this route accepts.

    ⚠ SPENDS NO AI QUOTA AND NO MONEY. It does spend the deployment's shared
    Freesound budget — 60 requests a minute on a free key — at ONE request per
    distinct cue (`freesound.download` skips the metadata re-read, because the
    item came from our own search rather than from the browser). The browser caps
    the cue count for the same reason: see `MAX_SFX_SOUNDS` in `sound_pass.js`.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")
    if not freesound.configured():
        # 503, not 500: the deployment has no key, which is a configuration fact
        # and not a fault in this request. The Director treats it as "no sound was
        # added" and says so, exactly as it treats a failed voiceover.
        raise HTTPException(
            status_code=503,
            detail=(
                "The sound library is not configured on this server, so no sound "
                "effects or music could be fetched."
            ),
        )

    # ⚠ MEASURED OFF THE SAVED PROJECT, WHICH CAN BE BEHIND THE EDITOR. Same
    # limitation `import_sound` has and the same mitigation: the browser counts
    # its own tracks too (`audioFileCount`), and this is the count that is
    # actually enforced. A saved project one track behind means one fewer sound
    # than the preview promised, which is reported rather than hidden.
    room = max(0, config.MAX_ANIMATIC_AUDIO_TRACKS - len(_audio_files_of(job)))

    items: list[SoundtrackItem] = []
    skipped: list[dict] = []
    # Cue key → the item already filed in, so a repeated cue costs nothing.
    done: dict[str, SoundtrackItem] = {}
    media = _media_dir(job_id)

    for cue in body.sounds or []:
        query = (cue.query or "").strip()
        key = (cue.key or "").strip() or query.lower()
        if not query:
            skipped.append({"key": key, "query": query, "why": "the cue was empty"})
            continue
        if key in done:
            # Not a skip and not an error — the caller asked for one sound twice
            # and gets the one file back. It is already in `items`.
            continue
        if len(done) >= room:
            skipped.append(
                {
                    "key": key,
                    "query": query,
                    "why": (
                        f"this project can hold {config.MAX_ANIMATIC_AUDIO_TRACKS} audio "
                        "files and there was no room left for it"
                    ),
                }
            )
            continue

        # ⚠ THE LADDER, AND IT STOPS AT THE FIRST HIT. Most cues land on attempt
        # one and cost one request; a cue the model over-described gets a second,
        # wider ask before the shot is left silent. See `_cue_attempts`.
        picked = None
        relaxed = ""
        failure = ""
        for attempt in _cue_attempts(query, cue.max_seconds, cue.min_seconds):
            try:
                found = freesound.search(
                    query=attempt["query"],
                    # ⚠ NOT A PARAMETER, AND NOT SOMETHING THE LADDER RELAXES
                    # EITHER. The length filter is a preference and goes on the
                    # second attempt; the LICENCE is the rule. See the docstring.
                    licence="safe",
                    min_seconds=attempt["min_seconds"],
                    max_seconds=attempt["max_seconds"],
                    page=1,
                    page_size=_SOUNDTRACK_LOOK,
                    sort=_SOUNDTRACK_SORT,
                )
            except freesound.FreesoundError as exc:
                # ⚠ ONE CUE'S FAILURE IS ONE CUE, NEVER THE PASS. A rate limit hit
                # halfway through a list must leave the sounds already filed in on
                # the timeline; raising here would throw away work that has been
                # done. And a failed attempt does not stop the wider one — a
                # timeout on the narrow ask says nothing about the broad one.
                failure = str(exc)
                continue
            for candidate in found.get("items") or []:
                if candidate.get("preview_url"):
                    picked = candidate
                    relaxed = attempt["relaxed"]
                    break
            if picked:
                break

        if not picked:
            skipped.append(
                {
                    "key": key,
                    "query": query,
                    "why": failure
                    or (
                        f"the sound library has no CC0 result for “{query}”, even "
                        "searched on its last two words alone"
                    ),
                }
            )
            continue

        try:
            data, filename = freesound.download(picked, config.MAX_AUDIO_BYTES)
        except freesound.FreesoundError as exc:
            skipped.append({"key": key, "query": query, "why": str(exc)})
            continue

        os.makedirs(media, exist_ok=True)
        upload_id = uuid.uuid4().hex[:12]
        with open(os.path.join(media, f"audio_{upload_id}.mp3"), "wb") as fh:
            fh.write(data)

        item = SoundtrackItem(
            key=key,
            query=query,
            # ⚠ SAID OUT LOUD WHEN IT IS NOT WHAT WAS ASKED FOR. If the cue only
            # found something on the wider attempt, the sound on the timeline is
            # an answer to a DIFFERENT question than the one the preview printed —
            # and a user who reads "gentle wind chimes" in the plan and hears a
            # gong is owed the sentence "we searched for 'wind chimes' instead".
            relaxed_to=relaxed,
            kind=(cue.kind or "sfx"),
            upload_id=upload_id,
            filename=filename,
            url=f"/animatics/{job_id}/media/{upload_id}",
            # ⚠ FREESOUND'S OWN NUMBER. There is no ffprobe on an `imageio-ffmpeg`
            # install, so the server cannot measure an audio file at all — and the
            # music pass NEEDS this one before it can loop the bed, which is the
            # first time that number has been load-bearing rather than cosmetic.
            # The editor still re-measures the blob once it is loaded.
            duration_ms=int(picked.get("duration_ms") or 0),
            attribution=picked.get("attribution") or "",
            license=picked.get("license") or "cc0",
            license_label=picked.get("license_label") or "",
            needs_credit=bool(picked.get("needs_credit")),
            page_url=picked.get("page_url") or "",
        )
        done[key] = item
        items.append(item)

    logger.info(
        "[animatic %s] soundtrack: %d cue(s) asked, %d filed, %d skipped.",
        job_id, len(body.sounds or []), len(items), len(skipped),
    )
    return SoundtrackResponse(items=items, skipped=skipped, room_left=max(0, room - len(done)))


@router.get("/{job_id}/frame/{frame_id}")
def get_frame_image(
    job_id: str,
    frame_id: str,
    w: int = 0,
    v: str = "",
    current: CurrentUser = Depends(get_current_user),
):
    """Serve one frame's picture — board panel or upload, same URL either way.

    `w` asks for a PROXY: a lossless copy of the same picture whose long edge is
    at most that many pixels, cached on disk (`proxies.py`). The editor asks for
    one because it holds every frame of a sixty-panel board in memory at once to
    draw a monitor 600px wide. Omitted — which is what every other caller does,
    including anything that predates this — the source file is served untouched.

    `v` is `_frame_version`'s stamp. It is not read — the path is resolved fresh
    every time regardless — but its PRESENCE is what makes the response safe to
    cache in the browser, so it is declared here rather than swallowed as an
    unknown query param. See `_media_headers`.

    ⚠ THE EXPORT DOES NOT COME THROUGH HERE. `build_animatic` opens the sources
    directly, so no proxy can ever reach the encoder; see rule 2 in `proxies.py`.
    """
    import proxies

    job = _get_owned_animatic(job_id, current)
    frame = _frame_by_id(job, frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found.")
    path = _resolve_frame_path(job, frame)
    if not path:
        raise HTTPException(status_code=404, detail="This frame's image is missing.")
    if w:
        # Falls back to `path` itself for every reason a proxy can't be made, so
        # the worst case here is exactly what this route did before.
        path = proxies.proxy_for(path, _proxy_dir(job_id), w)
    return FileResponse(path, media_type="image/png", headers=_media_headers(bool(v)))


@router.get("/{job_id}/panel/{storyboard_id}/{index}")
def get_board_panel(
    job_id: str,
    storyboard_id: str,
    index: int,
    frame: int = -1,
    w: int = 0,
    v: str = "",
    current: CurrentUser = Depends(get_current_user),
):
    """Serve one BOARD PANEL by (board, index) — for the media library.

    ⚠ CONTENT-ADDRESSED, NOT ID-ADDRESSED, and that is the whole reason it exists
    beside `get_frame_image`. That route looks a frame up in the SAVED project, so
    it cannot answer for a library card that has no clip on the timeline — and it
    404s for anything the autosave has not written yet. This one is asked "which
    panel?" and answers from the board, so a card is servable the instant it is
    added and stays servable after its last clip is deleted.

    `frame` asks for one KEY POSE of that panel's sequence instead of the panel
    itself; `w` proxies the picture down exactly as the frame route does; `v` is
    the version stamp that makes the answer cacheable, exactly as it is there.

    ⚠ BOTH JOBS ARE OWNERSHIP-CHECKED. The animatic here, and the BOARD inside
    `_resolve_frame_path` — a board id is a user-supplied string, so without the
    second check this would read any storyboard on the instance by id. Same pair
    as `import_storyboard`.
    """
    import proxies

    job = _get_owned_animatic(job_id, current)
    # A synthetic frame, purely to reuse the ONE resolver. Writing a second path
    # lookup here is how the library and the timeline would come to disagree
    # about which variant of a re-styled board is the current one.
    probe = AnimaticFrame(
        id="_panel",
        src={
            "kind": "pose" if frame >= 0 else "panel",
            "storyboard_id": storyboard_id,
            "index": index,
            "frame": frame if frame >= 0 else None,
        },
    )
    path = _resolve_frame_path(job, probe)
    if not path:
        raise HTTPException(status_code=404, detail="That panel's image is missing.")
    if w:
        path = proxies.proxy_for(path, _proxy_dir(job_id), w)
    return FileResponse(path, media_type="image/png", headers=_media_headers(bool(v)))


@router.get("/{job_id}/media/{upload_id}")
def get_upload(
    job_id: str,
    upload_id: str,
    poster: int = 0,
    w: int = 0,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve a just-uploaded file — image, video OR audio — by its upload id.

    `poster=1` asks a VIDEO for one still instead of the file itself: the picture
    a media card wants, from an upload that may not be on the project yet. `w`
    proxies that still down, exactly as the frame route does. Both are ignored
    for an image or an audio file, neither of which has anything else to give.

    This is the route the editor uses for media it has only just uploaded, and
    it exists because the project-level routes can't answer yet: the editor's
    save is debounced, so for the best part of a second the file is on disk but
    is not yet ON the project. Serving by upload id has no such dependency.
    (The audio case was a real bug: the waveform 404'd and never drew until the
    page was reloaded. Browser-tested — don't route audio through /audio here.)

    Every answer here is browser-cacheable without a version stamp: an upload id
    is a fresh uuid per upload, so nothing is ever written over one. See
    `_media_headers`.
    """
    _get_owned_animatic(job_id, current)

    image = _image_path(job_id, upload_id)
    if image and os.path.isfile(image):
        return FileResponse(image, media_type="image/png", headers=_media_headers())

    # Served as a whole file rather than a byte range. The Program monitor's
    # <video> is fed an object URL built from a fetched blob (the same authed
    # path every picture takes), so the browser already has the whole file in
    # memory and seeks inside it without asking the server again — which is why
    # scrubbing a video clip in the editor is instant and needs no Range support.
    video = _video_file(job_id, upload_id)
    if video and os.path.isfile(video):
        if poster:
            still = _video_poster(job_id, upload_id)
            if not still:
                raise HTTPException(
                    status_code=404, detail="Couldn't read a picture from this clip."
                )
            if w:
                import proxies

                still = proxies.proxy_for(still, _proxy_dir(job_id), w)
            return FileResponse(still, media_type="image/png", headers=_media_headers())
        return FileResponse(video, headers=_media_headers())

    audio = _audio_file(job_id, upload_id)
    if audio and os.path.isfile(audio):
        return FileResponse(audio, headers=_media_headers())

    raise HTTPException(status_code=404, detail="Upload not found.")


@router.get("/{job_id}/audio")
def get_audio(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Serve the animatic's audio track (for the waveform and for playback)."""
    job = _get_owned_animatic(job_id, current)
    tracks = _audio_tracks_of(job)
    path = _audio_file(job_id, tracks[0].upload_id) if tracks else None
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No audio on this project.")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@router.post("/{job_id}/export", response_model=JobCreatedResponse, status_code=202)
def export_animatic(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Encode the animatic to MP4 off-request. Poll GET /jobs/{id} for progress.

    Frame paths are resolved HERE, not in the worker: this is the request that
    knows who is asking, so a frame pointing at someone else's board is dropped
    before the encoder ever sees a path.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is already exporting.")

    frames = _frames_of(job)
    if not frames:
        raise HTTPException(
            status_code=409, detail="Add some images before exporting the video."
        )

    settings = _settings_of(job)
    # Resolved here, not in the worker: this is the request that knows who is
    # asking. Muted tracks are dropped rather than mixed in at zero.
    #
    # ⚠ DUMP THE MODEL, never rebuild it field by field — the same rule the
    # frames below follow, and it was learned the same way. This was a dict
    # literal listing the four fields the mixer was thought to need, so
    # `duration_ms` (which is what tells a fade out where the track ends) and
    # every field a later phase added would have been dropped silently: the
    # export would just have sounded wrong.
    audio_tracks = []
    for track in (_audio_tracks_of(job) if settings.include_audio else []):
        path = _audio_file(job_id, track.upload_id)
        if not path or track.muted or track.volume <= 0:
            continue
        item = track.model_dump(exclude={"url"})
        item["path"] = path
        audio_tracks.append(item)

    # Each clip resolved into what `build_animatic` draws it from. The three
    # kinds need different things, and this is the request that knows who is
    # asking — so a clip pointing at someone else's board or upload resolves to
    # nothing here and is dropped before the encoder sees a path.
    #
    # ⚠ DUMP THE MODEL, never rebuild it field by field. This used to be a dict
    # literal listing the fields the exporter was thought to need, and it had
    # silently fallen three features behind the editor — `id` was missing, so
    # EVERY TRANSITION was inert in the MP4 while the monitor blended it;
    # `keyframes`, `scale`, `x`, `y` and `opacity` were missing, so a Ken Burns
    # push exported as a still. Nothing failed; the video was just quietly not
    # what the preview showed. A dump cannot drift: a field added to
    # `AnimaticFrame` arrives here on its own.
    #
    # `src` is dropped because it is the question this loop ANSWERS — where the
    # picture comes from — and `url` because it is a read-only convenience the
    # encoder has no use for.
    #
    # ⚠ A HIDDEN PICTURE TRACK IS BLANKED ON THE BOTTOM OF THE STACK AND DROPPED
    # ABOVE IT, and the asymmetry is not a compromise — the two are the SAME
    # PICTURE where each one applies, and only one of them is safe in each case.
    #
    #   THE BOTTOM OF THE STACK is where what a dropped clip would reveal is the
    #     letterbox colour — which is exactly what a colour card of the letterbox
    #     colour draws. Blanking is chosen because it also HOLDS THE TIME: the
    #     bottom row hidden in full would otherwise leave the export with no
    #     pictures at all, and `build_animatic` cannot encode that.
    #   ABOVE IT a dropped clip reveals the row UNDERNEATH, and an opaque card
    #     would hide it. So those are dropped, which is what an NLE shows for a
    #     row it is not outputting.
    #
    # ⚠ "THE BOTTOM OF THE STACK" IS NOT TRACK 0 ANY MORE. It used to be — a
    # picture row's rank WAS its track number before `settings.lane_order`
    # existed — but a row dragged above another one keeps its track number while
    # its RANK changes, so hard-coding 0 here painted a hidden track 0's opaque
    # card over whatever the user had dragged it above: reported as "when i uper
    # layer off layer hide then see my video layer not view in program panel".
    # `bottom_picture_track` asks the rank instead, and with no saved order it
    # answers 0 every time — so a project nobody has restacked exports exactly as
    # it always did.
    #
    # The monitor does the identical conversion (`shown` in AnimaticEditor.jsx),
    # which is what keeps the preview and the MP4 the same picture.
    hidden = set(settings.hidden_lanes or [])
    lane_order = list(settings.lane_order or [])
    tracks_in_use = sorted({f.track for f in frames} | {0})
    bottom_track = animatic_render.bottom_picture_track(tracks_in_use, lane_order)
    resolved = []
    for f in frames:
        item = f.model_dump(exclude={"url", "src"})
        item["path"] = None
        item["video_path"] = None
        if f"frames:{animatic_render.frame_track(item)}" in hidden:
            if animatic_render.frame_track(item) != bottom_track:
                continue
            item["kind"] = "color"
            item["color"] = settings.background or "#000000"
        elif f.kind == "video":
            item["video_path"] = _video_file(job_id, f.src.upload_id or "")
        elif f.kind != "color":
            item["path"] = _resolve_frame_path(job, f)
        resolved.append(item)

    # Overlay pictures. Resolved here for the same reason the frames are: this
    # is the request that knows who is asking. One whose file has gone is
    # dropped rather than failing the whole export.
    overlays = []
    for overlay in _overlays_of(job):
        # A row switched off is left out entirely — unlike a picture-track clip,
        # an overlay holds no time of its own, so there is nothing to hold open.
        if _lane_hidden(hidden, "image", overlay.layer_id):
            continue
        path = _image_path(job_id, overlay.upload_id)
        if not path or not os.path.isfile(path):
            logger.warning(
                "[animatic %s] overlay %s has no image — skipped", job_id, overlay.id
            )
            continue
        item = overlay.model_dump(exclude={"url"})
        item["path"] = path
        overlays.append(item)
    # The free-floating clips, with the switched-off rows left out. Unlike a
    # picture-track clip, none of these holds time open: they are drawn over
    # whatever is underneath at that moment, so a row that isn't drawn is a row
    # that isn't there. Filtered ONCE, here, because the same lists decide both
    # what is drawn and how long the video is.
    texts = [t for t in _texts_of(job) if not _lane_hidden(hidden, "text", t.layer_id)]
    shapes = [s for s in _shapes_of(job) if not _lane_hidden(hidden, "shape", s.layer_id)]

    # A colour card needs no file, so it counts as usable on its own — an
    # animatic of nothing but colour cards is odd but perfectly encodable, and
    # refusing it would be a rule with no reason behind it.
    if not any(f["path"] or f["video_path"] or f["kind"] == "color" for f in resolved):
        raise HTTPException(
            status_code=409,
            detail="None of these clips have a file any more — re-add them and try again.",
        )

    # How long the video runs. "timeline" holds the last picture while audio or
    # text plays on, so a music bed longer than the pictures isn't cut off; the
    # exporter takes max(frames, this), so it can only ever extend.
    end_ms = 0
    if settings.end_at != "frames":
        for track in _audio_tracks_of(job):
            if track.muted or not settings.include_audio:
                continue
            playable = max(0, (track.duration_ms or 0) - (track.offset_ms or 0))
            if track.trim_ms:
                playable = min(playable, track.trim_ms)
            # ⚠ Measured from where the clip SITS, not from zero. A clip cut out
            # of the middle of a take starts late, and a video that stopped at
            # its length rather than its end would cut it off by exactly the
            # amount it was moved.
            end_ms = max(end_ms, (track.start_ms or 0) + playable)
        # ⚠ A HIDDEN ROW MUST NOT EXTEND THE VIDEO EITHER. A caption row switched
        # off that still decided the length would leave seconds of held picture at
        # the end with nothing on them — the row would be invisible and still be
        # the longest thing in the project. (`overlays` is already filtered above.)
        for clip in texts:
            if (clip.text or "").strip():
                end_ms = max(end_ms, clip.start_ms + clip.duration_ms)
        for shape in shapes:
            end_ms = max(end_ms, shape.start_ms + shape.duration_ms)
        for overlay in overlays:
            end_ms = max(end_ms, overlay["start_ms"] + overlay["duration_ms"])

    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        error=None,
        progress={"percent": 0, "stage": "frames", "message": "Preparing frames…"},
    )
    worker.submit_animatic_export(
        job_id,
        {
            "frames": resolved,
            # ⚠ THE FILTERED LISTS, not `_texts_of(job)` again — this is where a
            # hidden row would otherwise walk straight back into the encoder.
            "texts": [t.model_dump() for t in texts],
            "shapes": [s.model_dump() for s in shapes],
            "overlays": overlays,
            # Transitions are boundary-local, so they change nothing about the
            # length calculated above — they only change what is drawn on the
            # cuts. One naming a frame the export dropped is simply inert.
            "transitions": [t.model_dump() for t in _transitions_of(job)],
            "audio_tracks": audio_tracks,
            "aspect_ratio": settings.aspect_ratio,
            "resolution": settings.resolution,
            "quality": settings.quality,
            "end_ms": end_ms or None,
            "fps": settings.fps,
            "fit": settings.fit,
            "background": settings.background,
            "show_labels": settings.show_labels,
            # ⚠ WHAT DRAWS OVER WHAT. The encoder needs the row order or it
            # exports the DEFAULT stack while the monitor draws the one the user
            # dragged — the one divergence that is invisible until the file comes
            # back. Empty on every project that has never been restacked, which is
            # what makes those export exactly as they always did.
            #
            # ⚠ IT IS NOT FILTERED BY `hidden`, unlike the three lists above. A
            # hidden row's CLIPS are gone by here, so naming it in the order costs
            # nothing — and stripping it would renumber the ranks of every row
            # below it, which is a restack nobody asked for.
            "lane_order": list(settings.lane_order or []),
            # What file to write, and — for a still — which moment of it. Both
            # default to exactly what every export did before presets existed;
            # see `export_presets.py`.
            "container": settings.container,
            "still_ms": settings.still_ms,
            "output_dir": config.OUTPUT_DIR,
        },
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.ANIMATIC,
        character_name=job.character_name or "Project",
        message=f"Exporting your project. Poll GET /jobs/{job_id}.",
    )


@router.post("/{job_id}/stop")
def stop_export(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Stop an export in progress. ffmpeg is terminated; no video is written."""
    job = _get_owned_animatic(job_id, current)
    if job.status != JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project isn't exporting.")

    from cancel import request_cancel

    request_cancel(job_id)
    logger.info("[animatic %s] export stop requested by %s", job_id, current.email)
    return {"stopping": True, "job_id": job_id}


@router.get("/{job_id}/video")
def download_video(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Download the last export — an MP4, a GIF or a PNG (see `container`)."""
    import export_presets

    job = _get_owned_animatic(job_id, current)
    found = _exported_file(job_id, _settings_of(job).container)
    if not found:
        raise HTTPException(
            status_code=404, detail="No video yet — export this project first."
        )
    path, container = found
    safe = "".join(
        c if c.isalnum() or c in "-_ " else " " for c in (job.character_name or "project")
    )
    safe = " ".join(safe.split()).strip(" -_") or "project"
    return FileResponse(
        path,
        media_type=export_presets.CONTAINER_MIME[container],
        filename=f"{safe}.{export_presets.CONTAINER_EXT[container]}",
    )




# ---------------------------------------------------------------------------
# Interchange — hand this cut to Premiere Pro, Resolve, Avid or Final Cut
# ---------------------------------------------------------------------------
# Phase 1 is EXPORT ONLY, in ONE format: FCP7 XML (`xmeml` v4), which is the
# common language every one of those apps still reads. The whole of the format
# lives in `interchange.py`; this is only the part that needs to know WHO IS
# ASKING.
#
# ⚠ RESOLVED HERE, NOT IN `interchange.py`, FOR EXACTLY THE REASON
# `export_animatic` RESOLVES ITS OWN PATHS HERE: this is the request that knows
# the owner, so a clip pointing at somebody else's board resolves to nothing
# before it can be copied into a zip and downloaded. An exporter that took ids
# and went looking for the files itself would be a way to read another account's
# pictures.
#
# ⚠ AND IT IS A PLAIN, SYNCHRONOUS DOWNLOAD, NOT A JOB. A video export is
# minutes of ffmpeg and owns `JobStatus.RUNNING` while it runs; this is a text
# file plus a file copy. Making it a job would mean a project file could not be
# exported while an encode was running (and the other way round), and would put
# the autosave into `serverBusy` for a second's work.
def _interchange_payload(job: Job) -> dict:
    """This project as `interchange.build_sequence` wants it — paths resolved."""
    import animatic

    settings = _settings_of(job)
    frames = _frames_of(job)
    hidden = set(settings.hidden_lanes or [])
    width, height = animatic.resolve_size(settings.aspect_ratio, settings.resolution)

    # ⚠ DUMP THE MODEL, never rebuild it field by field — the same rule, learned
    # the same expensive way, as the two loops in `export_animatic` above. A
    # field this feature does not read today is one the NEXT format (an EDL, an
    # After Effects script) will, and a hand-written dict literal is exactly how
    # `track` and `start_ms` once went missing from an export.
    resolved = []
    for f in frames:
        item = f.model_dump(exclude={"url", "src"})
        item["path"] = None
        item["video_path"] = None
        if f.kind == "video":
            item["video_path"] = _video_file(job.job_id, f.src.upload_id or "")
        elif f.kind != "color":
            item["path"] = _resolve_frame_path(job, f)
        resolved.append(item)

    overlays = []
    for overlay in _overlays_of(job):
        item = overlay.model_dump(exclude={"url"})
        item["path"] = _image_path(job.job_id, overlay.upload_id)
        overlays.append(item)

    # ⚠ A MUTED TRACK IS EXPORTED, DISABLED — unlike the video export, which
    # drops it. This file is a PROJECT, not a render: a track the user silenced
    # while cutting is still part of the cut, and arriving in Premiere as a
    # switched-off clip is exactly what they left behind here. `include_audio`
    # is ignored for the same reason — it is an encoder setting, not a fact
    # about the film.
    audio_tracks = []
    for track in _audio_tracks_of(job):
        item = track.model_dump(exclude={"url"})
        item["path"] = _audio_file(job.job_id, track.upload_id)
        audio_tracks.append(item)

    return {
        "title": job.character_name or "Project",
        "fps": settings.fps,
        "width": width,
        "height": height,
        "background": settings.background,
        "show_labels": settings.show_labels,
        # ⚠ NO `end_ms`. `frame_spans` uses it to HOLD THE LAST PICTURE out to
        # the end of a music bed, which is right for a video file and wrong for
        # a project file: it would hand Premiere a clip stretched past its own
        # length. An NLE shows the real lengths and lets the audio run on.
        "end_ms": 0,
        "frames": resolved,
        "overlays": overlays,
        "audio_tracks": audio_tracks,
        "transitions": [t.model_dump() for t in _transitions_of(job)],
        # Counted, never written: there is no box for either in xmeml, and the
        # report is what tells the user so before they download.
        "texts": [t.model_dump() for t in _texts_of(job)],
        "shapes": [sh.model_dump() for sh in _shapes_of(job)],
        "lane_order": list(settings.lane_order or []),
        "hidden_lanes": list(hidden),
    }


def _interchange_name(job: Job) -> str:
    """The project's title, as a filename. Same rule as `download_video`."""
    safe = "".join(
        c if c.isalnum() or c in "-_ " else " " for c in (job.character_name or "project")
    )
    return " ".join(safe.split()).strip(" -_") or "project"


def _interchange_dir(job_id: str) -> str:
    """Where a built project file waits to be served.

    INSIDE the animatic's own folder, for the reason `_stills_dir` gives:
    `delete_animatic`'s rmtree collects it, and there is no second garbage
    collector to forget to run.
    """
    return os.path.join(_animatic_dir(job_id), "_interchange")


@router.get("/{job_id}/interchange/preview", response_model=InterchangeReport)
def preview_interchange(
    job_id: str,
    format: str = "fcp7",
    current: CurrentUser = Depends(get_current_user),
):
    """What an export WOULD contain — and what it would have to leave behind.

    ⚠ THIS ROUTE IS THE FEATURE'S HONESTY, and it exists because the download
    itself cannot carry a message: it answers with a file. A user told
    afterwards that their colour grades did not travel has already opened
    Premiere and decided the export is broken.

    ⚠ AND IT TAKES THE FORMAT, because the answer changes with it: an EDL holds
    ONE video track and no dissolves, so choosing it in the dropdown has to make
    the list of losses grow before the download, not after the conform. An
    unknown name folds down to `fcp7` rather than answering 422 — the same rule
    an unrecognised transition or clip kind follows everywhere else here.

    Costs nothing: it builds the model and never touches a byte of media.
    """
    import interchange

    job = _get_owned_animatic(job_id, current)
    fmt = interchange.normalise_format(format)
    model = interchange.build_sequence(_interchange_payload(job))
    report = interchange.report_of(model, fmt)
    size = 0
    for entry in model["files"]:
        if entry.get("path") and os.path.isfile(entry["path"]):
            size += os.path.getsize(entry["path"])
    return InterchangeReport(
        media_bytes=size,
        dropped=[InterchangeLoss(**row) for row in report["dropped"]],
        **{k: v for k, v in report.items() if k != "dropped"},
    )


@router.get("/{job_id}/interchange")
def export_interchange(
    job_id: str,
    format: str = "fcp7",
    media: bool = True,
    base_path: str = "",
    current: CurrentUser = Depends(get_current_user),
):
    """Download this cut as a project file another editor can open.

    `format` is `fcp7` (Premiere / Resolve / Avid / Final Cut), `aftereffects`
    (a script AE runs to build the comp) or `edl` (CMX3600, cuts only). An
    unknown name folds down to `fcp7`.

    `media=true` (the default) answers with a ZIP: the document and a `media/`
    folder holding every picture, clip and sound it names. ⚠ THAT IS THE DEFAULT
    BECAUSE A DOCUMENT ON ITS OWN IMPORTS AS A TIMELINE OF OFFLINE CLIPS — the
    file is a recipe, and without the ingredients it is a row of red rectangles.

    `base_path` is the folder on the user's own computer where the media will
    end up. ⚠ IT ONLY MEANS ANYTHING FOR `fcp7`: given one, every `<pathurl>` is
    absolute and the import is silent, and without one they are relative and
    Premiere asks to locate the media once. The AE script finds its own media and
    an EDL names reels rather than paths, so both ignore it.

    ⚠ THE ZIP IS NAMED AFTER THE FORMAT (`Film-fcp7.zip`), because all three are
    exports of the same project and a single `Film.zip` would mean downloading
    the EDL destroyed the XML you fetched a minute ago — one file per format, the
    same rule `_video_path` follows for mp4 / gif / png.
    """
    import interchange

    job = _get_owned_animatic(job_id, current)
    fmt = interchange.normalise_format(format)
    ext = interchange.format_ext(fmt)
    model = interchange.build_sequence(_interchange_payload(job))
    if not model["video"] and not model["audio"]:
        raise HTTPException(
            status_code=409,
            detail="There is nothing on the timeline to export yet.",
        )

    name = _interchange_name(job)
    out_dir = _interchange_dir(job_id)
    os.makedirs(out_dir, exist_ok=True)
    if media:
        path = os.path.join(out_dir, f"{name}-{fmt}.zip")
        interchange.bundle(model, path, f"{name}.{ext}", fmt=fmt, base_path=base_path)
        logger.info(
            "[animatic %s] project file exported — %s, %d clips, %d media files, zip",
            job_id,
            fmt,
            sum(len(lane["clips"]) for lane in model["video"]),
            len(model["files"]),
        )
        return FileResponse(path, media_type="application/zip", filename=f"{name}.zip")

    path = os.path.join(out_dir, f"{name}.{ext}")
    interchange.write_document_only(model, path, fmt=fmt, base_path=base_path)
    logger.info("[animatic %s] project file exported — %s, document only", job_id, fmt)
    return FileResponse(
        path,
        media_type=interchange.FORMATS[fmt]["mime"],
        filename=f"{name}.{ext}",
    )


# ---------------------------------------------------------------------------
# Import — somebody else's cut, brought in here (Phase 3)
# ---------------------------------------------------------------------------
# ⚠ THE SERVER PRODUCES THE MATERIAL, THE CLIENT DECIDES THE TIMELINE. Exactly
# the contract `import_storyboard` and both uploads follow, and here it earns its
# keep twice: the editor puts the clips on rows it creates (only the browser
# knows which row numbers are free), and the whole import lands as ONE entry on
# the undo stack — so a user who does not like what arrived presses Ctrl+Z
# instead of rebuilding their film. Nothing below writes to the project.
#
# ⚠ THE MEDIA IS STORED THOUGH, and has to be: matching a clip to a file means
# having the file. Every one goes through the same checks and the same folder as
# the ordinary uploads, so an import can never be a way to get a file into a
# project that `POST /images` or `/videos` would have refused. Whether the clips
# are then taken or not, the files list in the Media pane.
def _keeps_alpha(im) -> bool:
    """Does this source actually carry transparency worth keeping?

    ⚠ **`convert("RGB")` PAINTS EVERY TRANSPARENT PIXEL BLACK**, and for months
    that is what both upload paths did to every PNG. It was invisible for a
    photograph and ruinous for the one thing people upload a PNG *for*: a
    cut-out logo arrived as a black card with a logo printed on it, and on a row
    above the film that black card is a lid over the whole picture — the same
    fault E57 is about, wearing a different hat. Reported straight after a
    Premiere import: *"mera logo ka background transparent tha but yaha pe black
    aaya"*.

    ⚠ **AND IT IS ASKED OF THE SOURCE, NOT ASSUMED.** A JPEG has no alpha and an
    RGBA file whose alpha is solid is a photograph in a bigger box; storing four
    channels for either buys nothing and costs a third more disk on every frame
    of every project. `P` mode is checked separately because a palette PNG hides
    its transparency in `info`, not in its mode.
    """
    if im.mode in ("RGBA", "LA"):
        return True
    return im.mode == "P" and "transparency" in im.info


# One line per file this project has ever stored from an import, keyed by the
# digest of the bytes that ARRIVED. See `_import_ledger` / `_store_import_media`.
_IMPORT_LEDGER = ".imported.json"


def _import_ledger(job_id: str) -> dict:
    """`{sha256 of the incoming bytes: {"kind", "upload_id", "duration_ms"}}`.

    ⚠ **KEYED ON WHAT ARRIVED, NOT ON WHAT LANDED ON DISK.** An imported picture
    is re-encoded to PNG on the way in, so the stored bytes are not the bytes the
    user sent and hashing the disk would never match a second import of the same
    file. This is also why the ledger is a file rather than a walk of the media
    directory: the only moment the original bytes exist is the moment they are
    stored.

    Missing, empty or corrupt reads as `{}` — a lost ledger costs a duplicate,
    never an import.
    """
    try:
        with open(os.path.join(_media_dir(job_id), _IMPORT_LEDGER), encoding="utf-8") as fh:
            found = json.load(fh)
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_import(job_id: str, digest: str, entry: dict) -> None:
    """One row added to the ledger, written whole and swapped into place.

    ⚠ TEMP FILE THEN `os.replace`, NEVER AN OPEN-FOR-WRITE ON THE REAL PATH. A
    crash midway through rewriting this in place leaves a truncated JSON file,
    which reads as an empty ledger — every dedupe this project had learned, gone.
    """
    path = os.path.join(_media_dir(job_id), _IMPORT_LEDGER)
    ledger = _import_ledger(job_id)
    ledger[digest] = {k: entry[k] for k in ("kind", "upload_id", "duration_ms") if k in entry}
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh)
        os.replace(tmp, path)
    except OSError:
        logger.info("[animatic %s] could not update the import ledger", job_id)
        try:
            os.remove(tmp)
        except OSError:
            pass


def _stored_media_exists(job_id: str, kind: str, upload_id: str) -> bool:
    """Is the file behind a remembered upload_id still on disk?

    ⚠ ASKED EVERY TIME, because the ledger outlives the files it names: a user
    who deletes a clip from the Media pane can take the file with it, and reusing
    that id would resolve a clip to nothing at all — a worse outcome than the
    duplicate this is avoiding.
    """
    if not upload_id or not _ID_RE.match(upload_id):
        return False
    prefix = {"image": "img_", "video": "vid_", "audio": "audio_"}.get(kind)
    if not prefix:
        return False
    return bool(glob.glob(os.path.join(_media_dir(job_id), f"{prefix}{upload_id}.*")))


def _store_import_media(job_id: str, name: str, data: bytes) -> dict | None:
    """One file out of an import, stored the way its own upload route stores it.

    Returns `{"kind", "upload_id", "duration_ms", "filename"}`, or None if it is
    not a kind we take (the caller names it in `rejected`). The returned dict
    carries `"reused": True` when the bytes were already here and nothing new was
    written.

    ⚠ **THE SAME BYTES ARE STORED ONCE PER PROJECT, NOT ONCE PER IMPORT.** Every
    file used to get a fresh `upload_id` and a fresh copy on disk, so importing
    the same cut twice doubled the project's media — a real job on this machine
    ended up holding **52 files for 27 names**. It also leaked WITHIN one import:
    the media pickers add by name across several folders, so the same file
    picked from two of them was written twice and only one copy was ever
    referenced. Content-addressed by the incoming bytes, both go away.
    """
    import interchange

    kind = interchange.media_kind(name)
    if not kind:
        return None
    media = _media_dir(job_id)
    os.makedirs(media, exist_ok=True)

    # ⚠ BEFORE ANY WORK. For a picture this skips a decode and a PNG re-encode,
    # and for a video it skips writing hundreds of megabytes and probing them.
    digest = hashlib.sha256(data).hexdigest()
    known = _import_ledger(job_id).get(digest) or {}
    if known.get("kind") == kind and _stored_media_exists(job_id, kind, known.get("upload_id", "")):
        return {
            "kind": kind,
            "upload_id": known["upload_id"],
            "duration_ms": int(known.get("duration_ms") or 0),
            "filename": name,
            "reused": True,
        }

    upload_id = uuid.uuid4().hex[:12]

    def keep(entry: dict) -> dict:
        _remember_import(job_id, digest, entry)
        return entry

    if kind == "image":
        from PIL import Image as PILImage

        if len(data) > config.MAX_UPLOAD_BYTES:
            return None
        path = _image_path(job_id, upload_id)
        try:
            # Normalised to a clean PNG, exactly as `upload_images` does — one
            # shape of picture on disk, whatever came in — and KEEPING the alpha
            # when there is any. See `_keeps_alpha`.
            with PILImage.open(io.BytesIO(data)) as im:
                im.convert("RGBA" if _keeps_alpha(im) else "RGB").save(path, "PNG")
        except Exception:  # noqa: BLE001 — bad/corrupt upload
            return None
        return keep({"kind": "image", "upload_id": upload_id, "duration_ms": 0,
                     "filename": name})

    if kind == "video":
        import video_frames

        ext = os.path.splitext(name)[1].lower()
        if ext not in config.ALLOWED_VIDEO_EXTS:
            ext = ".mp4"
        if len(data) > config.MAX_VIDEO_BYTES:
            return None
        path = os.path.join(media, f"vid_{upload_id}{ext}")
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            logger.exception("[animatic %s] could not store imported video %s", job_id, name)
            return None
        # ⚠ MEASURED HERE, exactly as `upload_videos` measures — the exporter has
        # to work from the same number, and there is no ffprobe on this install.
        return keep({
            "kind": "video",
            "upload_id": upload_id,
            "duration_ms": video_frames.probe_duration(path),
            "filename": name,
        })

    ext = os.path.splitext(name)[1].lower()
    if ext not in config.ALLOWED_AUDIO_EXTS:
        ext = ".mp3"
    if len(data) > config.MAX_AUDIO_BYTES:
        return None
    try:
        with open(os.path.join(media, f"audio_{upload_id}{ext}"), "wb") as fh:
            fh.write(data)
    except OSError:
        logger.exception("[animatic %s] could not store imported audio %s", job_id, name)
        return None
    # ⚠ 0, NOT A GUESS. This server has no audio decoder; the browser measures a
    # track with `decodeAudioData` and `interchange.to_project` falls back to
    # "offset + what plays", which is an honest lower bound.
    return keep({"kind": "audio", "upload_id": upload_id, "duration_ms": 0, "filename": name})


# Loopback, both families, plus the hostname form a proxy-less dev server sees.
_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


def _may_read_local_media(request: Request | None) -> bool:
    """Is this server allowed to open the paths the project file recorded?

    ⚠ **ONLY WHEN THE BROWSER IS ON THIS MACHINE.** The paths in a project file
    (`C:/Users/…/Clips/music.mp3`) are real for a user running this app on their
    own computer and meaningless for anybody else — on a hosted server that same
    string points at the SERVER's disk, and reading it would be answering one
    user's import with another user's files. Loopback is the check because it is
    the one condition a remote request cannot arrange for itself.

    ⚠ AND THE EXTENSION WHITELIST IN `interchange.local_media_paths` IS THE OTHER
    HALF, not a nicety. A `<pathurl>` is text inside an uploaded document, so a
    hand-written project file can name any path it likes; the whitelist is why
    the worst it can name is a media file.

    `API_IMPORT_LOCAL_MEDIA=on` forces this on for a packaged desktop build whose
    requests arrive through a local proxy and therefore never look like loopback;
    `off` disables the whole feature.
    """
    mode = config.IMPORT_LOCAL_MEDIA
    if mode in ("off", "0", "false", "no"):
        return False
    if mode in ("on", "1", "true", "yes"):
        return True
    host = (getattr(getattr(request, "client", None), "host", "") or "").strip().lower()
    return host in _LOOPBACK


def _fetch_local_media(job_id: str, incoming: dict, stored: dict, library: dict) -> set:
    """Open the files the project file named, for the ones nobody attached.

    ⚠ **LAST IN THE QUEUE, ON PURPOSE.** What the user attached wins, then the
    project's own Media pane, and only then the disk. Putting the disk first
    would store a fresh copy of every file on every import — the duplicate
    storage that already put 52 files behind 27 names in a real project here.

    Returns the names it fetched, so the report can say so — a file that arrived
    without being asked for is a good outcome and a surprising one.
    """
    # Imported here rather than at the top of the module, exactly as the route
    # itself does — see G13 on what importing this module costs.
    import interchange

    fetched: set = set()
    for base, path in interchange.local_media_paths(incoming).items():
        stem = os.path.splitext(base)[0]
        if stored.get(base) or stored.get(stem) or library.get(base) or library.get(stem):
            continue
        try:
            # ⚠ SIZED BEFORE IT IS READ. These paths point at the user's own
            # footage and a feature film's master is a legal thing to find at the
            # end of one — reading it into memory to then refuse it is how a
            # server falls over on an import it was always going to reject.
            if os.path.getsize(path) > config.MAX_VIDEO_BYTES:
                continue
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            logger.info("[animatic %s] could not open %s from disk", job_id, path)
            continue
        entry = _store_import_media(job_id, os.path.basename(path), data)
        if entry is None:
            continue
        stored.setdefault(base, entry)
        stored.setdefault(stem, entry)
        fetched.add(os.path.basename(path))
    return fetched


@router.post("/{job_id}/interchange/import", response_model=AnimaticImportResponse)
async def import_interchange(
    job_id: str,
    request: Request,
    document: UploadFile = File(..., description="A Final Cut Pro XML, an EDL, or a .zip from here."),
    media: list[UploadFile] = File(default=[], description="The footage the document names."),
    experimental: bool = Form(
        default=False,
        description="Read a .prproj with the best-effort reader instead of refusing it.",
    ),
    current: CurrentUser = Depends(get_current_user),
):
    """Read another editor's cut and hand back the clips. **Saves nothing.**

    Takes a **Final Cut Pro XML** (what Premiere Pro, DaVinci Resolve and Avid
    all export), a **CMX3600 EDL**, or a **.zip this app exported** — in which
    case the media inside it is used and nothing else need be attached.

    ⚠ THE MEDIA IS THE HARD HALF. A project file names files by a path on the
    machine that wrote it, and a browser cannot read that path — so the footage
    has to come too, and is matched BY FILENAME.
    ⚠ **UNLESS THIS SERVER IS ON THAT SAME MACHINE**, in which case it opens the
    recorded paths itself for anything that was not attached, and the user never
    goes looking for the folder at all. Last in the queue, media extensions only,
    loopback only — `_may_read_local_media` and `interchange.local_media_paths`.
    ⚠ **A clip whose file did not arrive becomes a labelled colour card** rather
    than being left out: the cut stays whole, every gap is visible and named in
    `placeholders`, and dropping the real file on that row fixes it.

    `.prproj` and `.aep` are refused ON PURPOSE, with a sentence saying what to
    export instead — both are undocumented private formats, and a half-read
    timeline is worse than a clear "no".

    ⚠ `experimental=true` IS THE SECOND ANSWER FOR `.prproj`, NEVER THE FIRST.
    It opens Premiere's private save file with the best-effort reader in
    `interchange._read_prproj` — a guess, unverified against a real Premiere
    file, which puts the sentence saying so at the top of `warnings` on every
    import it produces. The refusal is still what an unflagged request gets,
    because the route it names (export a Final Cut Pro XML) always works and
    this does not. `.aep` has no equivalent and stays refused outright.
    """
    import interchange

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")

    raw = await document.read()
    if len(raw) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That project file is larger than {config.MAX_UPLOAD_BYTES // 1_048_576} MB.",
        )

    # ⚠ A ZIP IS THE ROUND TRIP, and it is the easiest path for a user by a long
    # way: the bundle this app exported already holds the document AND every file
    # it names, so re-importing one needs no second attachment and nothing can be
    # missing. The document inside is found by sniffing, not by extension, for
    # the same reason `detect_format` sniffs at all.
    attached: list[tuple[str, bytes]] = []
    doc_name = document.filename or "project.xml"
    if interchange.detect_format(raw, doc_name) == "zip":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                found_doc = None
                for info in zf.infolist():
                    if info.is_dir() or info.file_size > config.MAX_VIDEO_BYTES:
                        continue
                    base = os.path.basename(info.filename)
                    if not base or base.startswith("."):
                        continue
                    if interchange.media_kind(base):
                        attached.append((base, zf.read(info)))
                    elif found_doc is None:
                        body = zf.read(info)
                        if interchange.detect_format(body, base) in ("fcp7", "edl"):
                            found_doc, doc_name = body, base
                if found_doc is None:
                    raise HTTPException(
                        status_code=415,
                        detail="That zip has no project file in it — expected an .xml or an .edl.",
                    )
                raw = found_doc
        except zipfile.BadZipFile:
            raise HTTPException(status_code=415, detail="That .zip could not be opened.") from None

    for upload in media or []:
        attached.append((upload.filename or "file", await upload.read()))

    settings = _settings_of(job)
    try:
        # ⚠ THE PROJECT'S OWN fps IS THE HINT, and an EDL needs it: a CMX list
        # states drop or non-drop and never its rate, so the only number in the
        # room that anybody chose is this project's. `read_document` warns.
        incoming = interchange.read_document(
            raw, doc_name, fps_hint=settings.fps, experimental=bool(experimental)
        )
    except interchange.ImportRefused as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    stored: dict = {}
    rejected: list[str] = []
    # ⚠ WHAT WAS ALREADY HERE, BYTE FOR BYTE. `_store_import_media` is
    # content-addressed now, so re-importing the same cut reuses the copy this
    # project already holds instead of doubling its media. Counted so the report
    # can say it — a user watching a 27-file upload deserves to know that most of
    # it did not need storing.
    deduped: list[str] = []
    for name, data in attached:
        entry = _store_import_media(job_id, name, data)
        if entry is None:
            rejected.append(name)
            continue
        if entry.get("reused"):
            deduped.append(os.path.basename(name))
        # ⚠ KEYED CASE-INSENSITIVELY, AND ALSO WITHOUT THE EXTENSION. Windows and
        # macOS disagree about case, and an app that transcoded a clip on the way
        # out leaves `shot_03.mov` in the XML and `shot_03.mp4` on disk — matching
        # the stem as well is what saves that import from being all placeholders.
        base = os.path.basename(name)
        stored.setdefault(base.lower(), entry)
        stored.setdefault(os.path.splitext(base)[0].lower(), entry)

    # ⚠ **THE FILES ALREADY IN THIS PROJECT COUNT TOO.** Until this, an import
    # looked ONLY at what was attached to its own request — so a project whose
    # Media pane already held all 27 files still turned every clip into a
    # placeholder unless every one of them was picked again, and a user who
    # dragged the one missing file into Media and re-imported was told it had
    # not arrived while its card sat on screen beside the message.
    # ⚠ FRESH WINS. `stored` is tried first, so re-attaching a file that has been
    # re-exported still replaces the old one — the library is a fallback, not an
    # override.
    # ⚠ COLOUR CARDS ARE NOT MEDIA. An asset with no `upload_id` (a colour card)
    # has no file behind it and must not answer for a filename.
    library = interchange.media_library((job.params or {}).get("assets") or [])

    # ⚠ **AND IF THE FILE IS SITTING RIGHT THERE ON THIS DISK, FETCH IT.** The
    # report already printed the folder every missing file came from, because
    # both readers record the full path — and then sent the user off to find that
    # folder in a file dialog. When this server is on the same computer as the
    # editor that wrote the project, that is asking a person to do a walk we were
    # already holding the map for: *"tum khud usko pickup kyun nhi kar rahe ho
    # jab tumne location mil raha hai"*. Anything still not found stays in the
    # report, named with its path, for the user to attach by hand.
    # ⚠ GATED, AND THE GATE IS THE SECURITY BOUNDARY — see `_may_read_local_media`.
    from_disk: set = set()
    if _may_read_local_media(request):
        from_disk = _fetch_local_media(job_id, incoming, stored, library)

    reused: set = set()

    def resolve(wanted: str):
        base = os.path.basename((wanted or "").replace("\\", "/"))
        stem = os.path.splitext(base)[0].lower()
        found = stored.get(base.lower()) or stored.get(stem)
        if found:
            return found
        found = library.get(base.lower()) or library.get(stem)
        if found:
            reused.add(base)
        return found

    built = interchange.to_project(
        incoming, resolve, background=settings.background, new_id=lambda: uuid.uuid4().hex[:12]
    )
    report = built["report"]
    # ⚠ SAID OUT LOUD. A clip that resolved to a file the user never attached this
    # time is a good outcome and a surprising one — silence there is how somebody
    # ends up wondering which copy of a file their cut is actually using.
    if reused:
        report["warnings"].append(
            f"{len(reused)} file(s) were already in this project's Media and were "
            "used from there rather than being asked for again: "
            + ", ".join(sorted(reused)[:6])
            + ("…" if len(reused) > 6 else "")
        )
    # ⚠ AND SO IS THIS ONE, FOR A STRONGER REASON. A file nobody attached, that
    # nonetheless came in, is the app having opened something on the user's own
    # disk — they are entitled to be told which files and that it happened, in
    # the same report they read everything else in.
    if from_disk:
        report["warnings"].append(
            f"{len(from_disk)} file(s) were not attached but were found on this "
            "computer, at the path the project file recorded, and were used from "
            "there: "
            + ", ".join(sorted(from_disk)[:6])
            + ("…" if len(from_disk) > 6 else "")
        )
    # ⚠ SAID PLAINLY, because the alternative reads as the upload having failed.
    # A user who attaches 27 files and is told nothing was stored would reasonably
    # conclude the import ignored them; what actually happened is that this
    # project already had every one of those bytes.
    if deduped:
        seen_once = sorted(set(deduped))
        report["warnings"].append(
            f"{len(seen_once)} file(s) were already stored in this project and "
            "were not stored a second time — the copy already here is used."
        )

    # ⚠ COUNTED AGAINST WHAT IS ALREADY ON THE TIMELINE, the way the board import
    # counts. Refused BEFORE the clips are handed over, because the editor would
    # otherwise place them and then fail to save.
    existing = len((job.params or {}).get("frames") or [])
    if existing + len(built["frames"]) > config.MAX_ANIMATIC_FRAMES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That file holds {len(built['frames'])} clips and this project already has "
                f"{existing}, over the limit of {config.MAX_ANIMATIC_FRAMES}."
            ),
        )
    existing_audio = len((job.params or {}).get("audio_tracks") or [])
    if existing_audio + len(built["audio_tracks"]) > config.MAX_ANIMATIC_AUDIO_CLIPS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That file holds {len(built['audio_tracks'])} audio clips and this project "
                f"already has {existing_audio}, over the limit of "
                f"{config.MAX_ANIMATIC_AUDIO_CLIPS}."
            ),
        )
    # ⚠ TEXT AND SHAPES COUNT AS SOMETHING. A Premiere sequence whose picture and
    # sound are all offline but whose titles read perfectly is a real import, and
    # answering "there was nothing on that timeline" to it would be a lie the
    # user cannot argue with.
    if not any((built["frames"], built["audio_tracks"],
                built.get("texts"), built.get("shapes"))):
        raise HTTPException(
            status_code=409, detail="There was nothing on that timeline to bring in."
        )

    logger.info(
        "[animatic %s] imported %s%s: %d clips, %d sounds, %d placeholders, %d media matched",
        job_id, report["reader"],
        f" ({incoming.get('route')}, BEST-EFFORT)" if report["reader"] == "prproj" else "",
        report["clips"], report["audio_clips"],
        len(report["placeholders"]), report["matched"],
    )
    return AnimaticImportResponse(
        frames=[AnimaticFrame(**f) for f in built["frames"]],
        texts=[AnimaticTextClip(**t) for t in built.get("texts") or []],
        shapes=[AnimaticShape(**s) for s in built.get("shapes") or []],
        lane_order=list(built.get("lane_order") or []),
        audio_tracks=[AnimaticAudio(**a) for a in built["audio_tracks"]],
        transitions=[AnimaticTransition(**t) for t in built["transitions"]],
        name=report["name"],
        reader=report["reader"],
        fps=report["fps"],
        clips=report["clips"],
        audio_clips=report["audio_clips"],
        video_tracks=report["video_tracks"],
        video_lane_kinds=report.get("video_lane_kinds") or [],
        audio_lanes=report["audio_lanes"],
        text_lanes=report.get("text_lanes", 0),
        shape_lanes=report.get("shape_lanes", 0),
        texts_read=report.get("texts_read", 0),
        shapes_read=report.get("shapes_read", 0),
        transitions_read=report["transitions"],
        matched=report["matched"],
        placeholders=report["placeholders"],
        missing=[ImportMissingFile(**m) for m in report.get("missing") or []],
        warnings=report["warnings"],
        rejected=rejected,
    )


# ---------------------------------------------------------------------------
# Animate a frame with Veo — THE ONE PATH HERE THAT SPENDS MONEY
# ---------------------------------------------------------------------------
# Read the 2026-08-07 Work Log entry before changing any of this. The rules it
# established are not style, they are the difference between a mis-click costing
# nothing and costing hundreds:
#
#   1. Nothing renders until a FREE estimate has been shown. `/animate/estimate`
#      and `/animate` take the same body so the number quoted can only ever be
#      the price of the thing the button then does.
#   2. The batch is capped at config.MAX_VIDEO_BATCH.
#   3. A frame with no motion prompt is REFUSED. Veo bills for a failure exactly
#      as it bills for a success, so submitting one buys nothing.
#   4. A frame that already produced a clip is never silently re-rendered —
#      `force` is a separate, differently-worded action in the UI.
#
# THE OUTPUT IS AN ORDINARY VIDEO UPLOAD. A generated clip and a clip dragged in
# from the desktop are the same object on the timeline from the moment each
# exists, so trimming, speed, frame extraction and export have ONE code path.
# That is the whole reason this belongs in the editor rather than beside it.
def _estimate_animate(lengths: list[int], render: RenderSettings) -> CostEstimate:
    """What rendering takes of these LENGTHS at these settings should cost.

    Advisory, and labelled as such in the UI: list prices drift and only Google
    bills. Priced through `video_client.estimate_cost_usd` — the same rate table
    the final-video workspace quotes from, so the two can never disagree.

    ⚠ IT TAKES A LIST OF LENGTHS, NOT A COUNT, and that is not a generalisation
    for its own sake. ✨ Animate renders a whole batch at one length and passes
    `[render.duration_seconds] * n`; the 🎬 Director picks a length per shot from
    that shot's hold and passes the real mixture. A `count × duration` formula
    would quote the Director's pass for a film it is not going to render.
    """
    from video_client import estimate_cost_usd

    rows = list(lengths or [])
    return CostEstimate(
        shots=len(rows),
        seconds=sum(rows),
        usd=round(
            sum(
                estimate_cost_usd(n, render.resolution, render.tier, render.generate_audio)
                for n in rows
            ),
            2,
        ),
        tier=render.tier,
        resolution=render.resolution,
    )


def render_frame_clip(
    job_id: str, clip_id: str, render: dict, progress_cb=None, cancel_check=None
) -> None:
    """Render ONE animatic frame with Veo and store the clip. Raises on failure.

    The animatic's own adapter around `video_client.render_shot`, which is the
    reusable core. `videos.render_one_shot` is the other adapter — it resolves a
    FINAL_VIDEO job's shots and art, so it could not be called from here without
    dragging that job shape along with it. Two thin adapters over one renderer
    beats one function that knows about two workflows.
    """
    from video_client import VideoGenerationError, estimate_cost_usd, render_shot

    from datetime import datetime, timezone

    settings_render = RenderSettings(**(render or {}))
    job = get_store().get(job_id)
    if job is None:
        raise VideoGenerationError("This project no longer exists.")

    record = next((c for c in _veo_clips_of(job) if c.id == clip_id), None)
    if record is None:
        raise VideoGenerationError("This render is no longer part of the project.")

    # ⚠ TWO KINDS OF RECORD THROUGH ONE RENDERER. A record with a `frame_id` is
    # ✨ Animate: it animates a clip on the timeline and lands over it. A record
    # WITHOUT one is the Media pane's ✨ Video: it renders from a prompt and, at
    # most, one still that was dropped into the dialog. Everything after this
    # branch — the Veo call, the retry policy, the upload, the paid record — is
    # identical, which is the whole reason they share a renderer rather than
    # having one each to drift apart.
    frame = None
    label = record.label or "video"
    image: bytes | None = None
    if record.frame_id:
        frame = next((f for f in _frames_of(job) if f.id == record.frame_id), None)
        if frame is None:
            raise VideoGenerationError(
                "The frame this was going to animate has been deleted from the timeline."
            )
        label = frame.label or record.frame_id
        # The picture Veo animates. `_resolve_frame_path` already answers this
        # for every kind — a board panel, a key pose, an upload, or a video
        # clip's poster — so animating a clip that is already video means "carry
        # on from this frame", which is a real thing to want.
        source = _resolve_frame_path(job, frame)
        if not source or not os.path.isfile(source):
            raise VideoGenerationError(
                "This frame's picture is missing — the panel may have been deleted "
                "from the board, or the upload removed."
            )
        with open(source, "rb") as fh:
            image = fh.read()
    elif record.source_upload_id:
        # A still dropped into the ✨ Video dialog. ⚠ IT IS AN ERROR IF IT HAS
        # GONE, never a silent fall-through to text-to-video: the user chose a
        # starting picture, and rendering without it would bill them for
        # something they did not ask for.
        path = _image_path(job_id, record.source_upload_id)
        if not path or not os.path.isfile(path):
            raise VideoGenerationError(
                "The starting picture for this render is missing — it may have "
                "been removed from the project."
            )
        with open(path, "rb") as fh:
            image = fh.read()

    _write_veo_clip(job_id, clip_id, status="rendering", error="")

    # ⚠ THE RECORD'S OWN LENGTH WINS OVER THE BATCH'S. A 🎬 pass submits a mixed
    # bag — a 4-second take over a short hold and an 8-second one over a long
    # one, in the same submission — because the Director picks each length from
    # the shot it covers. A record with no length of its own is every render made
    # before that existed, and it takes the batch's, exactly as it always did.
    if record.seconds in (4, 6, 8):
        settings_render = settings_render.model_copy(
            update={"duration_seconds": record.seconds}
        )

    # The audience block rides on the prompt, and the other markets' currency
    # signs ride on the negative prompt — belt and braces, because a `$` on a
    # phone screen is the single most-reported thing Veo gets wrong here.
    veo_prompt, veo_negative = _localise_veo(
        record.prompt, settings_render.negative_prompt, job
    )
    data = render_shot(
        image,
        veo_prompt,
        # ⚠ ASKED FOR ON PURPOSE, never inferred from `image` being None. The
        # refusal in `render_shot` is what stops a still that failed to load
        # turning into a paid text-to-video render of the motion notes; this
        # says "there was never meant to be a picture", which is only true for a
        # frame-less record that named no source.
        text_only=not record.frame_id and not record.source_upload_id,
        tier=settings_render.tier,
        aspect_ratio=_settings_of(job).aspect_ratio,
        resolution=settings_render.resolution,
        duration_seconds=settings_render.duration_seconds,
        generate_audio=settings_render.generate_audio,
        negative_prompt=veo_negative or None,
        label=label,
        progress_cb=progress_cb,
        cancel_check=cancel_check,
    )

    # It lands as an ordinary video upload, under the same `vid_` prefix a
    # dropped file uses — from here on nothing downstream can tell the two apart.
    upload_id = uuid.uuid4().hex[:12]
    media = _media_dir(job_id)
    os.makedirs(media, exist_ok=True)
    with open(os.path.join(media, f"vid_{upload_id}.mp4"), "wb") as fh:
        fh.write(data)

    _write_veo_clip(
        job_id,
        clip_id,
        status="ready",
        error="",
        upload_id=upload_id,
        # What we ASKED Veo for. Nothing downstream has to measure it, which
        # matters because there is no ffprobe on this install.
        duration_ms=settings_render.duration_seconds * 1000,
        cost_usd=estimate_cost_usd(
            settings_render.duration_seconds,
            settings_render.resolution,
            settings_render.tier,
            settings_render.generate_audio,
        ),
        rendered_at=datetime.now(timezone.utc).isoformat(),
    )


def _animate_targets(
    job: Job, body: AnimaticAnimateRequest
) -> list[tuple[AnimaticFrame, str, int]]:
    """The (frame, prompt, seconds) triples a request would actually render.

    Everything that could only produce a PAID failure is filtered out here, in
    the one place both the estimate and the render call — so the price quoted is
    the price of the work, and neither can drift from the other.

    ⚠ THE LENGTH IS RESOLVED HERE FOR THE SAME REASON THE REFUSALS ARE. The 🎬
    Director sends one per frame (`durations`) because it picks each take's
    length from that shot's hold; everyone else sends none and gets the settings'
    own. Resolving it in the two endpoints separately is how an estimate starts
    quoting eight seconds for a take that renders at four.
    """
    frames = {f.id: f for f in _frames_of(job)}
    render_settings = body.render
    done = {
        c.frame_id
        for c in _veo_clips_of(job)
        if c.status == "ready" and c.upload_id
    }
    out: list[tuple[AnimaticFrame, str, int]] = []
    for frame_id in body.frame_ids:
        frame = frames.get(frame_id)
        if frame is None:
            continue
        # Never silently re-render something already paid for.
        if frame_id in done and not body.force:
            continue
        prompt = (body.prompts.get(frame_id) or "").strip()
        if not prompt:
            continue
        # ⚠ A LENGTH VEO WILL NOT RENDER IS NOT A SLIGHTLY-WRONG REQUEST, IT IS A
        # PAID FAILURE. The menu is 4/6/8, so anything else falls back to the
        # settings' own length rather than being sent on and refused.
        seconds = int(body.durations.get(frame_id) or 0)
        if seconds not in (4, 6, 8):
            seconds = render_settings.duration_seconds
        out.append((frame, prompt, seconds))
    return out


@router.post("/{job_id}/animate/estimate", response_model=CostEstimate)
def estimate_animate(
    job_id: str,
    body: AnimaticAnimateRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. What animating these frames would cost, before anything is spent.

    The client calls this to fill the confirm dialog, so the price is on screen
    before the button that spends it — the rule every paid path in this codebase
    follows.
    """
    job = _get_owned_animatic(job_id, current)
    return _estimate_animate(
        [seconds for _, _, seconds in _animate_targets(job, body)], body.render
    )


@router.post("/{job_id}/animate", response_model=JobCreatedResponse, status_code=202)
def animate_frames(
    job_id: str,
    body: AnimaticAnimateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.veo-render')),
):
    """SPENDS MONEY. Render the named frames with Veo. Poll GET /jobs/{id}.

    The job goes RUNNING, which `save_animatic` already refuses to write
    through. That is not a side effect — it is what makes the render state safe:
    for the whole life of a batch the server is the only writer to this job, so
    an autosave cannot roll back a clip that has been paid for.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is already busy — wait for it to finish, or stop it.",
        )

    targets = _animate_targets(job, body)
    if not targets:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nothing to render. A frame needs a motion prompt, and a frame "
                "that already has a clip is only re-rendered on purpose."
            ),
        )
    if len(targets) > config.MAX_VIDEO_BATCH:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That would render {len(targets)} frames at once; the limit is "
                f"{config.MAX_VIDEO_BATCH}. Render them in smaller passes — this "
                "is a spend guard, not a technical one."
            ),
        )

    clip_ids: list[str] = []
    for frame, prompt, seconds in targets:
        clip_id = uuid.uuid4().hex[:12]
        clip_ids.append(clip_id)
        # A re-render is a NEW record rather than an overwrite, so the history of
        # what was charged for stays intact even when the picture is replaced.
        _write_veo_clip(
            job_id,
            clip_id,
            frame_id=frame.id,
            prompt=prompt,
            status="queued",
            error="",
            upload_id="",
            duration_ms=0,
            # ⚠ ON THE RECORD, because a 🎬 batch is a MIXTURE of lengths and the
            # batch-wide settings can only carry one of them. `render_frame_clip`
            # reads it back per clip; a record without one (everything rendered
            # before this field existed, and every ✨ Animate batch) falls through
            # to the settings, which is what it always used.
            seconds=seconds,
            cost_usd=0.0,
            rendered_at="",
        )

    estimate = _estimate_animate([seconds for _, _, seconds in targets], body.render)
    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        error=None,
        progress={
            "percent": 0,
            "stage": "rendering",
            "message": f"Animating {len(targets)} frame(s) with Veo…",
        },
    )
    worker.submit_animatic_animate(job_id, clip_ids, body.render.model_dump())
    logger.info(
        "[animatic %s] %d frame(s) queued for Veo by %s (est. $%.2f)",
        job_id, len(targets), current.email, estimate.usd,
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.ANIMATIC,
        character_name=job.character_name or "Project",
        message=f"Animating {len(targets)} frame(s) — estimated ${estimate.usd:.2f}.",
    )


# ---------------------------------------------------------------------------
# ONE VIDEO FROM ONE SENTENCE — the Media pane's ✨ Video
# ---------------------------------------------------------------------------
# ⚠ THE SAME MONEY DISCIPLINE AS ✨ ANIMATE ABOVE, AND FOR THE SAME REASON.
# Nothing here renders until a FREE estimate has been on screen and accepted;
# both routes take the same body, so the number quoted can only be the price of
# what the button then does. Veo is billed per second of OUTPUT — a text-to-video
# clip costs exactly what animating a panel costs — so "it is only one clip" is
# not a reason to skip the step.
#
# ⚠ AND IT IS THE SAME RECORD, THE SAME WORKER AND THE SAME POLL. A standalone
# render is an `AnimaticVeoClip` with NO `frame_id`; `render_frame_clip` branches
# once on that and everything after it is identical, so a paid clip is recovered
# by the same self-heal whichever button bought it. See the note on
# `AnimaticVeoClip.frame_id`.
def _generate_video_source(job: Job, upload_id: str) -> str:
    """Validate the starting still, or "" for text-to-video.

    ⚠ A NAMED PICTURE THAT IS NOT THERE IS A 400, not a quiet fall-through to
    text-to-video: the user chose a starting frame, and rendering without it
    would bill them for something they did not ask for. The id is validated
    against `_ID_RE` as well, because it is a user-supplied string that becomes
    a filename.
    """
    upload_id = (upload_id or "").strip()
    if not upload_id:
        return ""
    if not _ID_RE.match(upload_id):
        raise HTTPException(status_code=400, detail="That isn't a picture in this project.")
    path = _image_path(job.job_id, upload_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=400,
            detail="That starting picture is no longer in this project — add it again.",
        )
    return upload_id


@router.post("/{job_id}/videos/generate/estimate", response_model=CostEstimate)
def estimate_generate_video(
    job_id: str,
    body: AnimaticVideoGenerateRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. What generating this video would cost, before anything is spent."""
    job = _get_owned_animatic(job_id, current)
    _generate_video_source(job, body.source_upload_id)
    return _estimate_animate([body.render.duration_seconds], body.render)


@router.post(
    "/{job_id}/videos/generate", response_model=JobCreatedResponse, status_code=202
)
def generate_animatic_video(
    job_id: str,
    body: AnimaticVideoGenerateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.veo-render')),
):
    """SPENDS MONEY. Render one video from a prompt. Poll GET /jobs/{id}.

    With `source_upload_id` it is image-to-video; without, Veo renders from the
    prompt alone. Either way the finished clip lands as an ordinary video upload
    and the editor puts it on the Video row and in the Media library.

    The job goes RUNNING, which `save_animatic` already refuses to write
    through — so for the life of the render the server is the only writer to
    this job and an autosave cannot roll back a clip that has been paid for.
    """
    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is already busy — wait for it to finish, or stop it.",
        )
    prompt = body.prompt.strip()
    if not prompt:
        # ⚠ Veo bills for a refusal exactly as it bills for a success, so a
        # promptless render is refused HERE rather than sent and charged for.
        raise HTTPException(status_code=400, detail="Say what the video should show.")
    source_upload_id = _generate_video_source(job, body.source_upload_id)

    clip_id = uuid.uuid4().hex[:12]
    _write_veo_clip(
        job_id,
        clip_id,
        # ⚠ NO FRAME. That is the whole of what makes this a standalone render,
        # and what tells the editor to land it on the Video row rather than over
        # a panel.
        frame_id="",
        source_upload_id=source_upload_id,
        label=_image_name_from_prompt(prompt),
        prompt=prompt,
        status="queued",
        error="",
        upload_id="",
        duration_ms=0,
        cost_usd=0.0,
        rendered_at="",
    )

    estimate = _estimate_animate([body.render.duration_seconds], body.render)
    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        error=None,
        progress={"percent": 0, "stage": "rendering", "message": "Generating a video with Veo…"},
    )
    worker.submit_animatic_animate(job_id, [clip_id], body.render.model_dump())
    logger.info(
        "[animatic %s] video queued for Veo by %s (%s, est. $%.2f)",
        job_id, current.email,
        "image-to-video" if source_upload_id else "text-to-video", estimate.usd,
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.ANIMATIC,
        character_name=job.character_name or "Project",
        message=f"Generating a video — estimated ${estimate.usd:.2f}.",
    )



# ---------------------------------------------------------------------------
# Captions and voiceover — THE OTHER TWO PATHS HERE THAT SPEND QUOTA
# ---------------------------------------------------------------------------
# Same discipline as ✨ Animate above, and it is worth restating why it applies
# to calls costing fractions of a cent: a cheap button is the one that gets
# pressed forty times, and forty times nothing is still a bill nobody agreed to.
#
#   1. Nothing runs until a FREE estimate has been shown. `/captions/estimate`
#      and `/captions` take the same body, as do the voiceover pair, so the
#      number quoted can only be the price of the thing the button then does.
#   2. Each run is capped — `captions.MAX_AUDIO_SECONDS`, `tts.MAX_CHARACTERS`.
#   3. The job goes RUNNING, which `save_animatic` already 409s. That is not a
#      side effect: for the life of the run the server is the only writer to
#      this job, so the editor's autosave cannot land on top of the captions it
#      is about to be given.
#
# WHAT THEY PRODUCE IS ORDINARY. A generated caption is an `AnimaticTextClip`
# like any other, and a voiceover is an ordinary audio track — the same rule the
# Veo path follows and for the same reason: from the moment it exists there is
# ONE code path downstream, not two that can drift apart.
def _write_texts(job_id: str, texts: list[dict], layers: list[dict] | None = None) -> None:
    """Replace the caption list on a job. Read-modify-write on `params`.

    Safe because the job is RUNNING for the whole life of a run and
    `save_animatic` refuses to write through that, so there is exactly one
    writer. ⚠ Unlike the Veo records this DOES live in `params` rather than in
    `result`: a caption is ordinary project content that the user then edits,
    and content in `result` would be invisible to every save the editor makes
    afterwards.

    `layers` is written in the SAME update when given, and the captions pass is
    the only caller that gives it: a generated caption sits on a lane of its own
    (`captions.CAPTION_LAYER_ID`) and a clip whose lane doesn't exist is a clip
    with nowhere to be drawn. Two writes could be interrupted between them and
    leave exactly that; one cannot.
    """
    store = get_store()
    job = store.get(job_id)
    if job is None:
        return
    params = dict(job.params or {})
    params["texts"] = texts
    if layers is not None:
        params["layers"] = layers
    store.update(job_id, params=params)


def _with_caption_layer(job: Job) -> list[dict] | None:
    """This project's layers, with the captions lane added if it is missing.

    None means "nothing to write" — either the lane is already there, or there
    is no room for it (see below) and the captions will fall back to the default
    text lane rather than the run failing after it has been paid for.

    ⚠ THE CAP IS NOT OPTIONAL. `save_animatic` refuses a project holding more
    than `MAX_ANIMATIC_LAYERS`, so a lane pushed past that by the server would
    make the editor's every later save 422 — the user would lose work because we
    added a row. `caption_animatic` warns about this BEFORE spending anything;
    this is the belt to that braces.
    """
    import captions as captions_mod

    layers = [l.model_dump() for l in _layers_of(job)]
    if any(l.get("id") == captions_mod.CAPTION_LAYER_ID for l in layers):
        return None
    if len(layers) >= config.MAX_ANIMATIC_LAYERS:
        logger.warning(
            "[animatic %s] no room for a captions lane (%d layers) — using the "
            "default text lane instead.",
            job.job_id, len(layers),
        )
        return None
    layers.append({
        "id": captions_mod.CAPTION_LAYER_ID,
        "kind": "text",
        "name": captions_mod.CAPTION_LAYER_NAME,
    })
    return layers


def _caption_layer_id(job: Job) -> str:
    """The lane generated captions go on for THIS project — the captions lane,
    or "" (the default text lane) when there is no room for one."""
    import captions as captions_mod

    layers = _layers_of(job)
    if any(l.id == captions_mod.CAPTION_LAYER_ID for l in layers):
        return captions_mod.CAPTION_LAYER_ID
    if len(layers) >= config.MAX_ANIMATIC_LAYERS:
        return ""
    return captions_mod.CAPTION_LAYER_ID


def _add_audio_track(job_id: str, track: dict) -> None:
    """Append one audio track to a job. Read-modify-write, as above."""
    store = get_store()
    job = store.get(job_id)
    if job is None:
        return
    params = dict(job.params or {})
    tracks = [dict(t) for t in (params.get("audio_tracks") or [])]
    tracks.append(track)
    params["audio_tracks"] = tracks
    store.update(job_id, params=params)


def _keep_typed_captions(job: Job) -> list[dict]:
    """Every caption on this animatic EXCEPT the ones a previous run generated.

    Generated clips are identified by their id prefix and nothing else — see
    `captions.CAPTION_ID_PREFIX`. A generated caption is deliberately not marked
    with a field of its own, because it has to be an ordinary caption in every
    other respect or half the inspector stops applying to it.
    """
    import captions as captions_mod

    return [
        t.model_dump()
        for t in _texts_of(job)
        if not (t.id or "").startswith(captions_mod.CAPTION_ID_PREFIX)
    ]


def _captioned_clips(job: Job, upload_id: str) -> list[AnimaticAudio]:
    """Every CLIP reading the file a captions request names, in play order.

    ⚠ A LIST, NOT A TRACK, and that is the whole of the cut-audio fix. Since the
    razor learned to cut audio, one file can be three clips sitting anywhere on
    the timeline with any part of it left out. Transcribing is done on the FILE
    (one call, one bill) but the words then have to be placed CLIP BY CLIP —
    reading only the first clip, as this used to, timed every caption after the
    first cut against a window that was no longer the one being heard.

    404 rather than an empty list: the track the request named is gone.
    """
    clips = [t for t in _audio_tracks_of(job) if t.upload_id == upload_id]
    if not clips:
        raise HTTPException(
            status_code=404, detail="That audio track isn't on this project any more."
        )
    return sorted(clips, key=lambda t: (t.start_ms or 0, t.offset_ms or 0))


def _clip_windows(clips: list[AnimaticAudio]) -> list[dict]:
    """`captions.clip_lines` windows for a file's clips — where each one sits on
    the timeline, and which stretch of the file it plays.

    ⚠ `play_ms` comes from `animatic.track_play_ms`, the SAME function the
    exporter measures a clip with. Written out a second time here it would be a
    second answer to "how much of this is heard", and the captions would drift
    from the audio the moment one of them was corrected.

    Measured with NO total: this asks what the clip plays, not how much of it
    survives the end of the video. `tidy_lines(total_ms=…)` cuts that tail off,
    once, where every other caption rule is applied.
    """
    import animatic as animatic_mod

    return [
        {
            "start_ms": animatic_mod.track_start_ms(clip.model_dump()),
            "offset_ms": max(0, int(clip.offset_ms or 0)),
            "play_ms": animatic_mod.track_play_ms(clip.model_dump()),
        }
        for clip in clips
    ]


def _dialogue_sheet(job: Job, frame_ids: list[str] | None = None) -> list[dict]:
    """THE DIALOGUE SHEET: every spoken line, in the order it will be read.

    ⚠ THIS IS THE WHOLE TRICK OF THE VOICEOVER, and the reason it belongs inside
    this editor rather than in a text box beside it: the board already knows who
    says what in which shot, and the timeline already knows when that shot is on
    screen. A line's target time is therefore data we hold, not something to be
    dragged into place afterwards.

    ⚠ AND IT IS SHOWN BEFORE ANYTHING IS SPENT. It used to be invisible — the
    dialog offered a voice and a price, and what would actually be said was
    whatever the board happened to hold. Asked for as "i want i see my Storyborad
    Dialouge in here … so user look if user want chnage so user change/edit
    Dialouge", the same two-step ✨ Animate follows with its prompt.

    Each line carries a PERSONA guessed from the board's cast sheet
    (`tts.persona_from`) — free, keyword-only, and the first thing the user can
    override. It is what tells the model an age and a sex; the voice it casts is
    a consequence of it, not the other way round.

    A shot broken into KEY POSES is many frames referencing one panel. Its
    dialogue is taken from the FIRST of them only — otherwise a four-second shot
    with sixteen poses would have its line read sixteen times, and be billed for
    each one.
    """
    import tts as tts_mod

    frames = _frames_of(job)
    wanted = set(frame_ids or [])
    chosen = [f for f in frames if f.id in wanted] if wanted else list(frames)

    # Where each frame sits, from the same evaluator the preview and the export
    # use — not a second sum written here, which is how a caption ends up one
    # cut out of step with the picture.
    spans, _total = animatic_render.frame_spans([f.model_dump() for f in frames])
    span_of = {f.id: spans[i] for i, f in enumerate(frames) if i < len(spans)}

    boards: dict[str, Job | None] = {}
    casts: dict[str, dict[str, str]] = {}
    seen: set[tuple[str, int]] = set()
    lines: list[dict] = []
    for frame in chosen:
        src = frame.src
        if src.kind not in ("panel", "pose") or src.index is None:
            continue
        board_id = src.storyboard_id or ""
        if not board_id or not _ID_RE.match(board_id):
            continue
        if board_id not in boards:
            board = get_store().get(board_id)
            # Owner check, exactly as `_resolve_frame_path` does it: frames are
            # user-editable JSON, so without it a crafted board id would read
            # another account's dialogue.
            if board is None or board.owner != job.owner or board.kind != JobKind.STORYBOARD:
                board = None
            boards[board_id] = board
            # The cast sheet, once per board: name → the description the
            # breakdown wrote, which is where an age and a sex come from.
            casts[board_id] = {
                str((c or {}).get("name") or "").strip().lower():
                    str((c or {}).get("description") or "")
                for c in ((board.result or {}).get("characters") or [])
            } if board is not None else {}
        board = boards[board_id]
        if board is None:
            continue

        key = (board_id, int(src.index))
        if key in seen:
            continue
        seen.add(key)

        variants, active = variants_of(board.result or {})
        panels = variants[active].get("panels") or []
        if int(src.index) >= len(panels):
            continue
        span = span_of.get(frame.id) or {"start": 0, "end": 0}
        for spoken in (panels[int(src.index)] or {}).get("dialogue") or []:
            line = str((spoken or {}).get("line") or "").strip()
            if not line:
                continue
            who = str((spoken or {}).get("character") or "").strip()
            lines.append({
                "text": line,
                "character": who,
                "persona": tts_mod.persona_from(who, casts[board_id].get(who.lower(), "")),
                # "" — the persona casts it, and the run's own picker is the
                # fallback under that. Filled in only when the user overrides.
                "voice": "",
                "frame_id": frame.id,
                "shot": frame.label or "",
                "start_ms": int(span["start"]),
                "hold_ms": int(span["end"] - span["start"]),
            })
    lines.sort(key=lambda line: line["start_ms"])
    return lines


def _requested_lines(job: Job, request: AnimaticVoiceoverRequest) -> list[dict]:
    """WHAT THIS RUN WILL READ — the edited sheet, or the board as it stands.

    ⚠ AN EDITED SHEET WINS ENTIRELY. If the browser sent lines, those are the
    lines: the words on the confirm dialog have to be the words that get read,
    and a merge with the board would mean neither. What the server still owns is
    WHERE each line goes — `frame_id` is looked up against the timeline here, so
    a stale or crafted one is dropped rather than placed at zero.

    ⚠ AND IT IS DROPPED HERE, IN THE ONE FUNCTION BOTH THE ESTIMATE AND THE RUN
    GO THROUGH, which is what keeps "quoted" and "read" the same set of lines. A
    line the layout would skip — one whose clip is not a storyboard shot, so
    there is nothing to stretch and nothing to ripple — must not survive as far
    as the price, or the user is billed for silence.
    """
    if not request.lines:
        return _dialogue_sheet(job, request.frame_ids)

    frames = _frames_of(job)
    spans, _total = animatic_render.frame_spans([f.model_dump() for f in frames])
    span_of = {f.id: spans[i] for i, f in enumerate(frames) if i < len(spans)}
    label_of = {f.id: f.label or "" for f in frames}
    shots = {f.id for f in frames if _is_board_panel(f)}

    out: list[dict] = []
    for sent in request.lines:
        text = (sent.text or "").strip()
        span = span_of.get(sent.frame_id)
        if not text or span is None or sent.frame_id not in shots:
            continue
        out.append({
            "text": text,
            "character": (sent.character or "").strip(),
            "persona": sent.persona or "",
            "voice": sent.voice or "",
            "frame_id": sent.frame_id,
            "shot": label_of.get(sent.frame_id, ""),
            "start_ms": int(span["start"]),
            "hold_ms": int(span["end"] - span["start"]),
        })
    out.sort(key=lambda line: line["start_ms"])
    return out


# --- A shot holds its own line ----------------------------------------------
# ⚠ ONE CLOCK OVER THE PICTURES AND THE SOUND, AND THAT IS THE WHOLE OF THIS
# SECTION. There used to be two: `tts` advanced its own by `line + gap`, the
# picture row was never touched at all, and a 2-second panel under a 10-second
# line meant the line — and the caption built from it — ran over the four shots
# after it. Reported with two screenshots as "caption and voicerover goes overlap
# other image shots … so my shot 9 image cover voiceover lenght".
#
# The fix is the one this editor already makes for a Veo take: THE ROOM COMES
# FROM THE ROW ITSELF. The shot that owns the line is STRETCHED to cover it, the
# shots after it are pushed clear, and the line is laid at the shot's new start.
# Because one loop decides both, they cannot disagree — which two clocks always
# eventually do, and silently.
#
# ⚠ FORWARD ONLY, AND NEVER PAST WHERE A CLIP ALREADY IS, exactly as
# `spreadPanelsForRenders` in `scene.js` — read that function's header, this is
# the same ripple with the speech included in what a panel has to clear. A panel
# already sitting clear of everything before it does not move, so a gap the user
# opened by hand survives.
#
# ⚠ AND A VEO RENDER MOVES WITH ITS PANEL, by the panel's delta and not onto its
# start, for the reason written out over there: snapping it would undo a nudge
# the user gave it, and leaving it behind would decouple a take from the shot it
# is a take OF.
def _clip_kind(frame: AnimaticFrame) -> str:
    """"image" | "video" | "color", folded down. ⚠ Twin of `clipKind` (scene.js)."""
    kind = (frame.kind or "image").strip().lower()
    return kind if kind in ("image", "video", "color") else "image"


def _is_board_panel(frame: AnimaticFrame) -> bool:
    """Is this a drawn shot off the storyboard — the row the ripple acts on?

    ⚠ Twin of `cardRowKind(kind, fromBoard) === "board_image"`.
    """
    return bool(frame.src and frame.src.storyboard_id) and _clip_kind(frame) != "video"


def _is_veo_render(frame: AnimaticFrame) -> bool:
    """Is this a paid take of a board shot? ⚠ Twin of `isVeoRender` (scene.js)."""
    return bool(frame.src and frame.src.storyboard_id) and _clip_kind(frame) == "video"


def _shot_key(src) -> str:
    """WHICH BOARD SHOT a clip is of — the pair that survives ✨ Animate.

    ⚠ Twin of `shotKey` in `scene.js`, down to the empty third field: a key pose
    and its panel share a board and an index, and without `frame` a render of
    pose 7 would pair with the panel sitting under it.

    ⚠ A GENERATED IN-BETWEEN SHOT HAS NO INDEX AND KEYS ON ITS OWN `shot_id`.
    It is not on the board, so there is no index to key on — and borrowing one
    would pair it with the real panel sitting at that index. The `gen-` prefix
    keeps the two spaces apart for good.
    """
    if not src or not src.storyboard_id:
        return ""
    if getattr(src, "shot_id", ""):
        return f"{src.storyboard_id}:gen-{src.shot_id}:"
    if src.index is None:
        return ""
    return f"{src.storyboard_id}:{src.index}:{'' if src.frame is None else src.frame}"


def _lay_out_speech(
    job: Job,
    lines: list[dict],
    *,
    voice: str | None = None,
    fit_shots: bool = True,
    progress_cb=None,
) -> dict:
    """SPENDS QUOTA. Read every line, and lay the shots out so each holds its own.

    Returns `{"wav", "timings", "frames", "moved", "duration_ms", "windows"}` —
    `frames` is None when nothing had to move, which is what lets the caller skip
    the write and say so honestly.

    `timings` are TIMELINE ms and describe the audio that was actually made, so
    the captions built from them match what is heard rather than what was planned.

    ⚠ `windows` IS WHERE THE SPEECH ACTUALLY IS, and the caller lays ONE CLIP PER
    WINDOW rather than one clip over the whole film. The file is continuous — a
    shot's speech sits at that shot's moment and the room between shots is
    silence — so a single clip spanning it draws a flat empty bar from 0:00 to
    the first word and another one across every pause, which is a timeline the
    user then has to razor by hand before they can move a line. Reported as
    "keep voiceover wave only, not the blank part". The clips read windows of
    ONE upload, which is exactly the shape the razor already leaves behind, so
    nothing downstream has to learn a new one.
    """
    import tts as tts_mod

    frames = _frames_of(job)
    raw = [f.model_dump(exclude={"url"}) for f in frames]
    spans, _total = animatic_render.frame_spans(raw)

    said_in: dict[str, list[dict]] = {}
    for line in lines:
        said_in.setdefault(line["frame_id"], []).append(line)

    # Every take, by the shot it was made from, in list order — a panel animated
    # twice ("Render again with Veo") has two, and must clear both.
    renders_of: dict[str, list[int]] = {}
    for i, frame in enumerate(frames):
        if not _is_veo_render(frame):
            continue
        key = _shot_key(frame.src)
        if key:
            renders_of.setdefault(key, []).append(i)

    # The panels in the order they PLAY, not the order they are stored: a drag on
    # the timeline moves a clip without touching the list.
    panels = [i for i, f in enumerate(frames) if _is_board_panel(f)]
    panels.sort(key=lambda i: (spans[i]["start"], i))

    moved: dict[int, dict] = {}      # frame index → the fields that changed
    pieces: list[tuple[int, bytes]] = []
    timings: list[dict] = []
    clock: dict[int, int] = {}       # track → the first moment free on it
    # ⚠ AND ONE FOR THE SOUND, which is not the same clock and must not be
    # confused with it. With `fit_shots` on the two are identical by
    # construction — a shot always ends after its own line, so speech never runs
    # into the next shot — and this one costs nothing. With `fit_shots` OFF the
    # pictures are left exactly where the user put them, and it is the AUDIO
    # that has to give way: a line longer than its shot pushes the next LINE
    # later rather than being spoken over the top of it. That is the behaviour
    # this pass had before it could stretch anything, kept as the escape hatch.
    said_clock = 0
    paired: set[int] = set()
    done = 0
    spoken_total = sum(len(v) for v in said_in.values())
    for i in panels:
        frame = frames[i]
        span = spans[i]
        track = animatic_render.frame_track(raw[i])
        # ⚠ WITH `fit_shots` OFF THIS PASS TOUCHES NO PICTURE AT ALL — not even
        # to clear a Veo take. Tidying the row is what ✨ Animate's own ripple is
        # for; doing it as a side effect of reading dialogue aloud would move
        # clips the user did not ask to move, in a dialog that never mentioned
        # them.
        start = max(span["start"], clock.get(track, 0)) if fit_shots else span["start"]
        hold = span["end"] - span["start"]

        speech = 0
        said = said_in.get(frame.id) or []
        if said:
            def _tick(line):
                nonlocal done
                if progress_cb:
                    progress_cb(done, spoken_total, line.get("text") or "")
                done += 1

            pcm, rel = tts_mod.speak_lines(said, voice=voice, progress_cb=_tick)
            if pcm:
                at = max(start, said_clock)
                pieces.append((at, pcm))
                for window in rel:
                    timings.append({
                        "start_ms": at + window["start_ms"],
                        "end_ms": at + window["end_ms"],
                        "text": window["text"],
                    })
                # ⚠ THE BREATH AFTER THE LINE IS PART OF WHAT THE SHOT HOLDS.
                # Without it the next shot starts on the last syllable and the
                # picture cuts a beat early on every line in the film; with it
                # the panel ends exactly where the next one begins, so there is
                # no hole in the row either.
                speech = tts_mod.pcm_duration_ms(pcm) + tts_mod.GAP_MS
                said_clock = at + speech

        if fit_shots and speech > hold:
            # `duration_ms` is capped by the schema; a line longer than the cap
            # is not something to 422 a paid run over.
            hold = min(speech, 600_000)
        if not fit_shots:
            continue

        delta = start - span["start"]
        if delta or hold != span["end"] - span["start"]:
            moved[i] = {"start_ms": start, "duration_ms": hold}
        free = start + hold

        # ⚠ ONLY THE FIRST UNPAIRED RENDER OF A SHOT PER PANEL. A duplicated
        # panel shares its `src` with the original, and pairing by key alone
        # would give the copy the same take.
        for j in renders_of.get(_shot_key(frame.src), []):
            if j in paired:
                continue
            paired.add(j)
            length = spans[j]["end"] - spans[j]["start"]
            if delta:
                moved[j] = {"start_ms": spans[j]["start"] + delta, "duration_ms": length}
            free = max(free, spans[j]["start"] + delta + length)
        clock[track] = free

    if not pieces:
        raise tts_mod.VoiceoverError("None of those lines have anything to say.")

    # ⚠ ONE WALK WRITES THE BYTES AND REPORTS THE WINDOWS. See `tts.lay_track`:
    # the clips laid on the timeline are drawn from `windows`, so working the
    # placement out a second time here would be a waveform that starts just
    # outside the box around it.
    wav, windows = tts_mod.lay_track(pieces)
    timings.sort(key=lambda t: t["start_ms"])
    out = None
    if moved:
        out = [{**r, **moved[i]} if i in moved else r for i, r in enumerate(raw)]
    return {
        "wav": wav,
        "timings": timings,
        "frames": out,
        "moved": len(moved),
        # ⚠ THE FILE'S OWN LENGTH, not the last caption's end. Every clip carries
        # it as `duration_ms` — the value `track_play_ms` falls back to when a
        # trim is ever lost — so it has to describe the WAV rather than the words.
        "duration_ms": (windows[-1]["start_ms"] + windows[-1]["duration_ms"]) if windows else 0,
        "windows": windows,
    }


# --- Captions ---------------------------------------------------------------
@router.post("/{job_id}/captions/estimate", response_model=AudioCostEstimate)
def estimate_captions(
    job_id: str,
    body: AnimaticCaptionsRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. What captioning that track would cost, before anything is spent."""
    import captions as captions_mod

    job = _get_owned_animatic(job_id, current)
    # ⚠ Priced by the FILE, not by what is left of it on the timeline. One call
    # sends the whole recording however many pieces it has been cut into, so
    # quoting the audible total would quote less than the run actually costs.
    track = _captioned_clips(job, body.upload_id)[0]
    # ⚠ THE LANGUAGE TRAVELS WITH THE QUOTE. Deepgram charges more for its
    # code-switching mode than for a single named language, so a quote that
    # ignored the field the run is about to use would price a different run.
    quote = captions_mod.estimate(track.duration_ms, language=body.language)
    return AudioCostEstimate(
        seconds=quote["seconds"],
        usd=quote["usd"],
        model=quote["model"],
        over_limit=quote["over_limit"],
        limit=f"{int(quote['limit_seconds'] / 60)} minutes of audio per run",
    )


@router.post("/{job_id}/captions", response_model=JobCreatedResponse, status_code=202)
def caption_animatic(
    job_id: str,
    body: AnimaticCaptionsRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.captions')),
):
    """SPENDS QUOTA. Transcribe one audio track into caption clips."""
    import captions as captions_mod

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is already busy — wait for it to finish, or stop it.",
        )
    track = _captioned_clips(job, body.upload_id)[0]
    if not _audio_file(job_id, track.upload_id):
        raise HTTPException(
            status_code=409, detail="That track's audio file has gone missing."
        )
    # Before anything is spent: is there a row for the captions to land on? They
    # go on a lane of their own, and finding out there is no room for one AFTER
    # paying to transcribe would be a bill for a pass that had to fall back.
    if not _caption_layer_id(job):
        raise HTTPException(
            status_code=409,
            detail=(
                f"This project already has {config.MAX_ANIMATIC_LAYERS} layers, so "
                "there is no room for the Captions lane the words go on. Remove a "
                "layer you aren't using and try again."
            ),
        )
    # ⚠ THE SAME ARGUMENTS AS /estimate ABOVE, and that is not tidiness. Both
    # routes take the same body, so the number quoted can only be the price of
    # the thing this button then does — drop `language` here and the dialog would
    # promise one rate while the run recorded another.
    quote = captions_mod.estimate(track.duration_ms, language=body.language)
    if quote["over_limit"]:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That track is {quote['seconds'] / 60:.0f} minutes long; the limit "
                f"for one captions run is {int(quote['limit_seconds'] / 60)}. "
                "This is a spend guard, not a technical one."
            ),
        )

    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        error=None,
        progress={"percent": 0, "stage": "captions", "message": "Listening to the track…"},
    )
    worker.submit_animatic_captions(job_id, body.model_dump())
    logger.info(
        "[animatic %s] captions requested by %s (%.0fs, est. $%.4f)",
        job_id, current.email, quote["seconds"], quote["usd"],
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.ANIMATIC,
        character_name=job.character_name or "Project",
        message=f"Writing captions from that track — estimated ${quote['usd']:.4f}.",
    )


def run_captions(job_id: str, body: dict, progress_cb=None) -> None:
    """Transcribe and write the captions. Called from the worker; raises on failure.

    The tidy pass is free and is where every "the subtitles overlap" bug lives,
    so it is a separate function from the paid transcription — a failure in the
    rules must not mean paying to listen to the track again.

    ⚠ `progress_cb(percent, message)` IS REPORTED PER STAGE, NOT PER LINE, and
    that is the honest shape of this job: transcription is ONE model call that
    cannot be asked how far through it is, so a bar that crept along during it
    would be an animation pretending to be a measurement. What the stages give
    the user is the thing they asked for — evidence that the work is happening,
    and which part of it is taking the time — without inventing a number.
    """
    import captions as captions_mod

    def say(percent: int, message: str) -> None:
        if progress_cb:
            progress_cb(percent, message)

    request = AnimaticCaptionsRequest(**(body or {}))
    job = get_store().get(job_id)
    if job is None:
        raise captions_mod.CaptionError("This project no longer exists.")

    # ⚠ EVERY CLIP OF THAT FILE, not the first one. See `_captioned_clips`.
    clips_of_file = [t for t in _audio_tracks_of(job) if t.upload_id == request.upload_id]
    if not clips_of_file:
        raise captions_mod.CaptionError(
            "The audio track this was captioning has been removed from the timeline."
        )
    clips_of_file.sort(key=lambda t: (t.start_ms or 0, t.offset_ms or 0))
    path = _audio_file(job_id, clips_of_file[0].upload_id)
    if not path:
        raise captions_mod.CaptionError("That track's audio file has gone missing.")

    # ⚠ MEASURED BEFORE THE MODEL IS CALLED, not after, and not because it reads
    # better: if ffmpeg is going to fail on this file we find out while nothing
    # has been spent yet, and the run carries on with the model's own times
    # rather than having paid for a transcript we then can't place.
    file_ms = int(clips_of_file[0].duration_ms or 0)
    spans: list[dict] = []
    if captions_mod.ALIGN_TO_AUDIO:
        say(5, "Reading the waveform…")
        spans = captions_mod.speech_spans(path, file_ms)

    say(20, "Listening to the track…")
    lines = captions_mod.transcribe(path, language=request.language)

    # ⚠ THE MODEL'S WORDS ARE EXCELLENT AND ITS TIMES ARE A GUESS, which is the
    # whole of the reported "the captions don't match the voiceover": the text
    # was right and it appeared after the sentence had been said. So the times
    # are thrown away and recomputed against the sound MEASURED in the file —
    # the same waveform drawn on the timeline, which is what the user is
    # checking them against. Free, no quota, and it declines to guess: a
    # measurement that fails returns the model's own times unchanged.
    #
    # Still FILE time on both sides, so `clip_lines` below is unaware of it.
    if spans:
        say(80, f"Placing {len(lines)} line(s) on the waveform…")
        lines = captions_mod.align_lines(lines, spans, total_ms=file_ms)

    say(92, "Writing the captions…")
    # ⚠ A transcript's times are relative to the FILE; a clip's are relative to
    # the TIMELINE, and one file can be several clips with a different shift
    # each. `clip_lines` is what walks the transcript through them — it moves
    # every line onto the timeline where it is actually heard and drops the ones
    # whose audio was cut out. Read its docstring before changing any of this;
    # doing the shift with a single number is the bug it replaced.
    through_cuts = captions_mod.clip_lines(lines, _clip_windows(clips_of_file))
    # Already absolute, so no further shift — `tidy_lines` is here only for the
    # rules that make a transcript safe to DRAW (order, overlap, readability).
    tidied = captions_mod.tidy_lines(
        through_cuts,
        total_ms=_duration_ms(_frames_of(job)) or None,
    )
    layer_id = _caption_layer_id(job)
    caption_clips = captions_mod.caption_clips(tidied, layer_id=layer_id)

    kept = (
        _keep_typed_captions(job)
        if request.replace
        else [t.model_dump() for t in _texts_of(job)]
    )
    _write_texts(job_id, kept + caption_clips, layers=_with_caption_layer(job))
    logger.info(
        "[animatic %s] %d caption(s) written from %d line(s) across %d clip(s).",
        job_id, len(caption_clips), len(lines), len(clips_of_file),
    )


# --- Voiceover --------------------------------------------------------------
@router.get("/{job_id}/dialogue", response_model=AnimaticDialogueSheet)
def get_dialogue(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """FREE. The board's dialogue for this timeline, ready to be edited and read.

    ⚠ SPENDS NOTHING AND CALLS NO MODEL — it is a read of the board plus a
    keyword guess at who each speaker is (`tts.persona_from`). That matters: this
    is what fills the dialog the moment 🎙 Voiceover is clicked, and a dialog
    that costs money to open is a dialog nobody opens twice.

    The two pickers come down with it. `tts.CAST` is the only place a voice
    exists — the browser used to carry its own list of six names, which is a
    second source of truth for something the model call has to agree with.
    """
    import tts as tts_mod

    job = _get_owned_animatic(job_id, current)
    frames = _frames_of(job)
    return AnimaticDialogueSheet(
        lines=[AnimaticDialogueLine(**line) for line in _dialogue_sheet(job)],
        voices=[
            VoiceOption(name=v["name"], tone=v["tone"], persona=v["persona"])
            for v in tts_mod.CAST
        ],
        personas=[
            PersonaOption(
                key=key, label=p["label"], voice=p["voice"], direction=p["direction"]
            )
            for key, p in tts_mod.PERSONAS.items()
        ],
        # "No dialogue" and "these clips aren't from a board" are different
        # problems with different answers, and the dialog says which.
        from_board=any(f.src and f.src.storyboard_id for f in frames),
    )


@router.post("/{job_id}/voiceover/estimate", response_model=AudioCostEstimate)
def estimate_voiceover(
    job_id: str,
    body: AnimaticVoiceoverRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. What reading this animatic's dialogue aloud would cost.

    ⚠ PRICED FROM THE SHEET THE USER IS LOOKING AT, not from the board: `body`
    carries the edited lines, and a quote for words nobody is going to hear is a
    price that looks made up the moment a line is shortened.
    """
    import tts as tts_mod

    job = _get_owned_animatic(job_id, current)
    quote = tts_mod.estimate(_requested_lines(job, body))
    return AudioCostEstimate(
        lines=quote["lines"],
        characters=quote["characters"],
        usd=quote["usd"],
        model=quote["model"],
        over_limit=quote["over_limit"],
        limit=f"{quote['limit_characters']:,} characters per run",
    )


@router.post("/{job_id}/voiceover", response_model=JobCreatedResponse, status_code=202)
def voice_animatic(
    job_id: str,
    body: AnimaticVoiceoverRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.tts-voiceover')),
):
    """SPENDS QUOTA. Read the board's dialogue aloud onto the audio layer."""
    import tts as tts_mod

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is already busy — wait for it to finish, or stop it.",
        )
    lines = _requested_lines(job, body)
    if not lines:
        raise HTTPException(
            status_code=409,
            detail=(
                "There is no dialogue to read. A voiceover comes from the "
                "storyboard this project was made from, so the shots on the "
                "timeline need spoken lines on the board."
            ),
        )
    quote = tts_mod.estimate(lines)
    if quote["over_limit"]:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That is {quote['characters']:,} characters of dialogue; the limit "
                f"for one run is {quote['limit_characters']:,}. Do it in passes — "
                "this is a spend guard, not a technical one."
            ),
        )
    # Counted in FILES: a voiceover is a new upload, and a track someone has cut
    # into four pieces still costs one.
    if len(_audio_files_of(job)) >= config.MAX_ANIMATIC_AUDIO_TRACKS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This project already has {config.MAX_ANIMATIC_AUDIO_TRACKS} audio "
                "tracks. Remove one before adding a voiceover."
            ),
        )

    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        error=None,
        progress={"percent": 0, "stage": "voiceover", "message": "Reading the dialogue…"},
    )
    worker.submit_animatic_voiceover(job_id, body.model_dump())
    logger.info(
        "[animatic %s] voiceover requested by %s (%d line(s), est. $%.4f)",
        job_id, current.email, quote["lines"], quote["usd"],
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.ANIMATIC,
        character_name=job.character_name or "Project",
        message=f"Reading {quote['lines']} line(s) — estimated ${quote['usd']:.4f}.",
    )


def run_voiceover(job_id: str, body: dict, progress_cb=None) -> None:
    """Read the dialogue, store the WAV, move the shots, add the track.

    Raises on failure.

    ⚠ THE TIMINGS THAT COME BACK ARE THE ONES THAT HAPPENED, not the ones asked
    for. The captions are built from those, so what is on screen matches what is
    heard — and since `_lay_out_speech` stretched each shot to cover its own
    line, what is heard now matches what is SEEN as well. Read that function's
    header: it is one clock over the pictures and the sound, and the reason this
    one is not two calls.

    ⚠ THE ORDER OF THE THREE WRITES IS DELIBERATE — audio, then the shots, then
    the captions. Each is a read-modify-write of one key, so they cannot clobber
    each other, but a run interrupted between them should leave the most useful
    partial state: the recording exists on the timeline before anything is moved
    to fit it.
    """
    import tts as tts_mod

    request = AnimaticVoiceoverRequest(**(body or {}))
    job = get_store().get(job_id)
    if job is None:
        raise tts_mod.VoiceoverError("This project no longer exists.")

    lines = _requested_lines(job, request)
    if not lines:
        raise tts_mod.VoiceoverError(
            "The shots this was going to read have been removed from the timeline."
        )

    laid = _lay_out_speech(
        job,
        lines,
        voice=request.voice,
        fit_shots=request.fit_shots,
        progress_cb=progress_cb,
    )
    wav, timings = laid["wav"], laid["timings"]

    # It lands as an ordinary audio upload, under the same `audio_` prefix a
    # dropped file uses — from here on nothing downstream can tell the two apart.
    upload_id = uuid.uuid4().hex[:12]
    media = _media_dir(job_id)
    os.makedirs(media, exist_ok=True)
    with open(os.path.join(media, f"audio_{upload_id}.wav"), "wb") as fh:
        fh.write(wav)

    # ⚠ The length comes from the SAMPLES, not from measuring the file. There is
    # no ffprobe on this install and every other audio duration here is supplied
    # by the browser; generated speech is the one case the server can answer for
    # itself, exactly, because it made the samples.
    duration_ms = laid["duration_ms"]
    # ⚠ ONE CLIP PER RUN OF SPEECH, NOT ONE CLIP OVER THE WHOLE FILM.
    #
    # The file is continuous: a shot's speech sits at that shot's moment and the
    # room between shots is silence, because the sound and the pictures share one
    # clock (`_lay_out_speech`). Laid down as a single clip that is right to the
    # millisecond and wrong to look at — a flat empty bar from 0:00 to the first
    # word, and another across every pause — and the user's first job on a paid
    # run was razoring it into the pieces this already knows the bounds of.
    # Reported as "keep the voiceover wave only, not the blank part".
    #
    # ⚠ THEY ARE CLIPS OF ONE UPLOAD, WHICH IS NOT A NEW SHAPE. `start_ms` places
    # the piece on the timeline and `offset_ms` reads the same distance into the
    # file — exactly what `splitClip` writes when a person razors a track, and
    # exactly what `_captioned_clips` / `_clip_windows` already read back. The
    # Media pane dedupes on the upload, so four clips still make one card.
    #
    # ⚠ AND `start_ms == offset_ms` BECAUSE THE FILE STARTS AT 0:00. The pieces
    # were laid at their timeline moments inside the WAV itself, so the file's
    # clock and the timeline's are the same clock. It is written out as two
    # fields rather than one because they mean different things the moment
    # anybody drags one of these clips.
    windows = laid["windows"] or [{"start_ms": 0, "duration_ms": duration_ms}]
    for i, window in enumerate(windows):
        _add_audio_track(job_id, {
            # ⚠ ONE ID PER CLIP, and the upload alone is no longer enough for it:
            # several clips share this file now, and everything the editor keys
            # per clip (the selection, its <audio> element, a patch, a mute)
            # keys on `id`. A single-piece run keeps the bare upload id, which
            # is what `_audio_tracks_of` would have backfilled anyway.
            "id": upload_id if len(windows) == 1 else f"{upload_id}_{i}",
            "upload_id": upload_id,
            "layer_id": "",
            "filename": "Voiceover.wav",
            # The FILE's length on every clip — `track_play_ms` falls back to it
            # when a trim is missing, so it must describe the WAV, not the piece.
            "duration_ms": duration_ms,
            "start_ms": window["start_ms"],
            "offset_ms": window["start_ms"],
            "trim_ms": window["duration_ms"],
            "volume": 1.0,
            "muted": False,
        })

    # THE SHOTS THAT HAD TO MOVE. None when the dialogue fitted inside the
    # pictures as they stood, which is the ordinary case for a board of long
    # holds and short lines — and a write of nothing is how the log below can
    # say honestly whether anything was rearranged.
    if laid["frames"] is not None:
        _write_frames(job_id, laid["frames"])

    if request.add_captions:
        import captions as captions_mod

        job = get_store().get(job_id) or job
        # The same lane the captions pass uses — one row for everything this app
        # wrote, whichever button asked for it. The timings are already timeline
        # time (they describe audio laid down at 0:00), so there is no shift.
        clips = captions_mod.caption_clips(
            captions_mod.tidy_lines(timings), layer_id=_caption_layer_id(job)
        )
        kept = (
            _keep_typed_captions(job)
            if request.replace
            else [t.model_dump() for t in _texts_of(job)]
        )
        _write_texts(job_id, kept + clips, layers=_with_caption_layer(job))

    logger.info(
        "[animatic %s] voiceover written (%d line(s), %.1fs, %d clip(s) moved to fit).",
        job_id, len(timings), duration_ms / 1000, laid["moved"],
    )


# ---------------------------------------------------------------------------
# Phase 7 — reaching back to the BOARD from inside the editor
#
# ⚠ THE WHOLE OF THIS SECTION RESTS ON ONE FACT: an animatic frame is a
# REFERENCE to a storyboard panel, not a copy of one (`AnimaticFrameSource`).
# Redrawing the panel therefore updates the animatic for free — the picture is
# resolved from the board on every request — and everything here is plumbing to
# let the editor ask for that redraw without leaving the timeline.
#
# Two things it must never do, both learned the hard way:
#   1. Reimplement the board's own actions. They are `common.regenerate_board_panel`
#      and `common.submit_sequence_run`, shared with the routes in main.py, so
#      the continuity bible and the resume arithmetic cannot fork.
#   2. Serve a redrawn picture on the URL that showed the old one. See
#      `_frame_version` — the client caches an authed blob per URL, so a path
#      that survives a redraw is a picture that never updates.
# ---------------------------------------------------------------------------
def _frame_or_404(job: Job, frame_id: str) -> AnimaticFrame:
    frame = _frame_by_id(job, frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="That clip is no longer on this project.")
    return frame


def _board_behind(job: Job, frame: AnimaticFrame) -> tuple[Job | None, str]:
    """The storyboard job this frame's picture comes from, and why not.

    Returns `(board, reason)` — exactly one of them is meaningful. The reason is
    written for the person reading it in the Properties pane, because "this clip
    is a file you dropped in" is a perfectly good answer to "why can't I redraw
    this" and a 400 is not.
    """
    src = frame.src
    if src.kind not in ("panel", "pose"):
        what = {
            "upload": "an image you uploaded",
            "video": "a video clip",
        }.get(src.kind, "not a storyboard shot")
        return None, f"This clip is {what}, so there is no panel to re-draw."
    board_id = src.storyboard_id or ""
    if not board_id or not _ID_RE.match(board_id) or src.index is None:
        return None, "This clip has lost its link to the storyboard it came from."
    board = get_store().get(board_id)
    # Owner check: frames are user-editable JSON, so without it a crafted board
    # id would redraw someone else's panel. Same rule as `_resolve_frame_path`.
    if board is None or board.owner != job.owner or board.kind != JobKind.STORYBOARD:
        return None, "The storyboard this shot came from is no longer available."
    return board, ""


@router.get("/{job_id}/frames/{frame_id}/panel", response_model=AnimaticPanelSource)
def get_frame_panel(
    job_id: str,
    frame_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """The board panel behind one clip — the wording the inline pane opens on.

    Free, and it answers "can this be re-drawn at all?" in the same breath, so
    the pane never renders a button that is going to 400.
    """
    job = _get_owned_animatic(job_id, current)
    frame = _frame_or_404(job, frame_id)
    board, reason = _board_behind(job, frame)
    if board is None:
        return AnimaticPanelSource(frame_id=frame_id, reason=reason)

    index = int(frame.src.index or 0)
    panel = panel_for_index(board, index) or {}
    is_panel = frame.src.kind == "panel"
    return AnimaticPanelSource(
        frame_id=frame_id,
        storyboard_id=board.job_id,
        index=index,
        description=str(panel.get("description") or ""),
        camera=str(panel.get("camera") or ""),
        location=str(panel.get("location") or ""),
        # The shot's spoken lines, for ✨ Animate — see `AnimaticPanelSource`.
        # ⚠ A LINE WITH NO WORDS IS NOT DIALOGUE and is dropped here rather than
        # in the UI, the same rule `_dialogue_sheet` follows for the voiceover:
        # an empty line would be an empty quotation in a Veo prompt.
        dialogue=[
            DialogueLine(
                character=str((spoken or {}).get("character") or "").strip(),
                line=str((spoken or {}).get("line") or "").strip(),
            )
            for spoken in (panel.get("dialogue") or [])
            if str((spoken or {}).get("line") or "").strip()
        ],
        title=board.character_name or "Storyboard",
        # A POSE is a drawing OF the panel, so redrawing the panel would leave
        # this clip showing the old pose — the honest answer is no, with the
        # reason, rather than a button that appears to do nothing.
        can_regenerate=is_panel,
        reason=(
            ""
            if is_panel
            else (
                "This clip is one KEY POSE of a shot, not the shot's panel. "
                "Re-block the shot below, or re-draw the panel on the storyboard."
            )
        ),
    )


@router.get("/{job_id}/panels", response_model=list[AnimaticShotWording])
def get_shot_wording(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """FREE. What the board says about EVERY clip on this timeline, in one read.

    ⚠ THIS IS WHAT LETS THE FREE PLANNER SPEND. The Director's "Just the rhythm"
    pass writes no words — it reads the shot lengths and nothing else — so it had
    no motion prompts, and ticking Veo on it rendered nothing at all while the
    panel cheerfully said the plan was free. The fix is not to invent prompts by
    arithmetic (see the header of `house_style.js` on why that is exactly what
    Phase 0 must never do): it is to hand back the sentence the board ALREADY
    holds for each shot — the same one ✨ Animate drafts its prompt from — and
    let the shot be rendered from its own description.

    ⚠ ONE READ PER BOARD, not one per clip. `_shot_wording` takes the cache and
    an animatic is usually forty-eight clips off one storyboard.

    ⚠ A LABEL IS NOT A DESCRIPTION. `_shot_wording` falls back to "Shot 4" for a
    clip it can say nothing else about, which is right for the model — a name is
    better than silence in a brief — and wrong here, because this answer is
    handed to a renderer that BILLS for it. So a wording that is only the label
    comes back empty and the shot is refused with a reason the user can read.
    """
    job = _get_owned_animatic(job_id, current)
    boards: dict[str, Job | None] = {}
    out: list[AnimaticShotWording] = []
    for frame in _frames_of(job):
        said = _shot_wording(job, frame, boards)
        if said and said == (frame.label or "").strip():
            said = ""
        out.append(AnimaticShotWording(frame_id=frame.id, description=said))
    return out


@router.post("/{job_id}/frames/{frame_id}/panel", response_model=AnimaticFrame)
def regenerate_frame_panel(
    job_id: str,
    frame_id: str,
    body: AnimaticPanelRegenerateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
):
    """SPENDS QUOTA. Re-draw the storyboard panel this clip shows.

    One image, synchronously — the same single call the board's own Regenerate
    makes, through the same helper, so the redraw gets the same continuity
    bible and lands in the same style variant.

    ⚠ Returns the FRAME, not the panel, and the frame's `url` carries a fresh
    `?v=`. That is the whole point of answering with it: the client swaps one
    blob for one URL and the shot updates in the monitor, the strip and the
    Properties pane at once. Answering `{"ok": true}` would leave the caller
    with nothing to re-fetch against.
    """
    job = _get_owned_animatic(job_id, current)
    frame = _frame_or_404(job, frame_id)
    board, reason = _board_behind(job, frame)
    if board is None:
        raise HTTPException(status_code=400, detail=reason)
    if frame.src.kind != "panel":
        raise HTTPException(
            status_code=400,
            detail=(
                "This clip is one key pose of a shot, not the shot's panel — "
                "re-block the shot instead."
            ),
        )
    if board.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="That storyboard is busy right now — wait for it to finish, or stop it.",
        )

    index = int(frame.src.index or 0)
    regenerate_board_panel(
        board, index,
        description=body.description, camera=body.camera, location=body.location,
    )
    logger.info(
        "[animatic %s] shot %s re-drew board %s panel %d for %s",
        job_id, frame_id, board.job_id, index, current.email,
    )
    # Re-read: the version has to be computed AFTER the redraw, off the file
    # that was just written.
    job = get_store().get(job_id) or job
    frame = _frame_or_404(job, frame_id)
    frame.url = f"/animatics/{job_id}/frame/{frame.id}?v={_frame_version(job, frame)}"
    return frame


# ---------------------------------------------------------------------------
# A SHOT THAT IS NOT ON THE BOARD — "generate a shot before / after this one"
# ---------------------------------------------------------------------------
# Right-click a storyboard clip on the timeline and the shot that is MISSING
# either side of it can be drawn and dropped into the cut, pushing everything
# after it along exactly as a Veo take does.
#
# ⚠ THE BOARD IS NEVER EDITED, AND THAT IS THE LOAD-BEARING DECISION HERE. The
# obvious implementation is `POST /storyboards/{id}/panels/insert` followed by a
# draw — and it is wrong: that route renumbers panels so that `index ==
# position`, while an animatic frame references a panel BY INDEX. Inserting a
# panel in the middle would therefore re-point every frame after it, in THIS
# project and in every other animatic built from the same board, at the wrong
# picture. So the drawing is stored as an ordinary animatic upload and the clip
# carries `shot_id` instead of an index — see `AnimaticFrameSource`.
#
# Three calls, in the order the dialog makes them: read the context (free),
# suggest the wording (a text call), draw it (an image call).
_SIDES = ("before", "after")


def _neighbour_side(side: str) -> str:
    """Which side of the clip: "before" or "after". Never guessed at."""
    value = (side or "").strip().lower()
    if value not in _SIDES:
        raise HTTPException(
            status_code=400, detail="`side` must be 'before' or 'after'."
        )
    return value


def _shot_wording(job: Job, frame: AnimaticFrame | None, boards: dict) -> str:
    """WHAT THIS CLIP IS, in words — for writing the shot next to it.

    Three sources, in the order of how much they actually say: the board panel
    behind it, the prompt a GENERATED shot was drawn from, and last the clip's
    label ("Shot 4"), which is a name and barely a description — but a name is
    still better than handing the model nothing at all.

    `boards` is a per-request cache of board records, for the same reason
    `_frame_version` takes one: a neighbour lookup asks about a handful of clips
    off one board.
    """
    if frame is None:
        return ""
    src = frame.src
    if src.kind in ("panel", "pose") and src.storyboard_id and src.index is not None:
        board_id = src.storyboard_id
        if board_id not in boards:
            board = get_store().get(board_id)
            # The same owner check `_resolve_frame_path` makes, for the same
            # reason: frames are user-editable JSON.
            boards[board_id] = (
                board
                if board is not None
                and board.owner == job.owner
                and board.kind == JobKind.STORYBOARD
                else None
            )
        board = boards[board_id]
        if board is not None:
            panel = panel_for_index(board, int(src.index)) or {}
            described = str(panel.get("description") or "").strip()
            if described:
                return described
    if (src.prompt or "").strip():
        return src.prompt.strip()
    return (frame.label or "").strip()


def _shot_cast(job: Job, frame: AnimaticFrame | None, boards: dict) -> tuple[list, list]:
    """The characters and named assets in one clip's shot, for its references.

    A shot invented between two others is almost always the same people in the
    same place, so the neighbours' cast is what locks the new drawing's faces to
    the rest of the board. Empty for a clip with no panel behind it — a
    generated shot has no cast list of its own, and guessing one from its prose
    would be a second, worse breakdown.
    """
    if frame is None:
        return [], []
    src = frame.src
    if src.kind not in ("panel", "pose") or not src.storyboard_id or src.index is None:
        return [], []
    board = boards.get(src.storyboard_id)
    if board is None:
        return [], []
    panel = panel_for_index(board, int(src.index)) or {}
    return list(panel.get("characters") or []), list(panel.get("assets") or [])


def _board_row_order(job: Job) -> list[tuple[int, AnimaticFrame]]:
    """Every storyboard STILL, in the order it plays, with its list index.

    ⚠ PLAY ORDER, NOT LIST ORDER. Dragging a clip on the timeline moves it
    without touching the list, so "the shot before this one" is a question about
    `start_ms` — the same sort `_lay_out_speech` and `spreadPanelsForRenders`
    both make before walking the panels.

    ⚠ PER TRACK IS THE CALLER'S JOB. This returns the lot; the caller narrows to
    the row the clicked clip is on, because an animatic may hold a second
    storyboard row and the shot before this one is on the row you right-clicked.
    """
    frames = _frames_of(job)
    raw = [f.model_dump(exclude={"url"}) for f in frames]
    spans, _total = animatic_render.frame_spans(raw)
    order = [i for i, f in enumerate(frames) if _is_board_panel(f)]
    order.sort(key=lambda i: (spans[i]["start"], i))
    return [(i, frames[i]) for i in order]


def _neighbour_pair(
    job: Job, frame: AnimaticFrame, side: str
) -> tuple[AnimaticFrame | None, AnimaticFrame | None, list[AnimaticFrame]]:
    """The two shots the new one goes BETWEEN, plus the stretch around them.

    Returns `(before, after, outline)`. Either of the first two is None at the
    ends of the row — "generate a shot before the first shot" is a real thing to
    ask for, and the prompt says so rather than inventing a neighbour.
    """
    row = [(i, f) for i, f in _board_row_order(job) if f.track == frame.track]
    position = next((n for n, (_i, f) in enumerate(row) if f.id == frame.id), None)
    if position is None:
        return (frame, None, [frame]) if side == "after" else (None, frame, [frame])

    if side == "after":
        before = frame
        after = row[position + 1][1] if position + 1 < len(row) else None
        gap = position + 1
    else:
        before = row[position - 1][1] if position > 0 else None
        after = frame
        gap = position

    # The stretch of film around the gap — two shots either side. Enough for the
    # model to see where the story is going without pasting a fifty-shot board
    # into a prompt that is about two of them.
    window = [f for _i, f in row[max(0, gap - 2): gap + 2]]
    return before, after, window


def _neighbour_board(job: Job, frame: AnimaticFrame) -> tuple[Job | None, str]:
    """The board this clip belongs to, and why there isn't one.

    ⚠ NOT `_board_behind`, WHICH ASKS A DIFFERENT QUESTION. That one wants the
    PANEL behind a clip, so it refuses anything that is not `kind='panel'`. This
    one wants the board's LOOK — its style, aspect, references and bible — which
    a generated in-between shot carries just as much as a panel does, so that it
    can have a neighbour of its own.
    """
    src = frame.src
    if _clip_kind(frame) == "video":
        return None, "This clip is footage. Generate a shot beside the picture underneath it."
    board_id = src.storyboard_id or ""
    if not board_id:
        return None, (
            "This clip did not come from a storyboard, so there is no look to draw "
            "a new shot in."
        )
    if not _ID_RE.match(board_id):
        return None, "This clip has lost its link to the storyboard it came from."
    board = get_store().get(board_id)
    # Owner check: frames are user-editable JSON, so without it a crafted board
    # id would draw with another account's references. Same rule as
    # `_resolve_frame_path`.
    if board is None or board.owner != job.owner or board.kind != JobKind.STORYBOARD:
        return None, "The storyboard this shot came from is no longer available."
    return board, ""


def _neighbour_label(frame: AnimaticFrame, side: str) -> str:
    """The new shot's name — "After Shot 4" — built once, here.

    Named after the clip it is beside rather than by number, because it has no
    number: it is not on the board, and the shots around it keep the numbers
    they already have. Same reason `AnimaticBoardImportResponse.name` is built
    server side — a name assembled on both sides of the wire drifts.
    """
    base = (frame.label or "").strip() or "this shot"
    return f"{'After' if side == 'after' else 'Before'} {base}"


def _neighbour_aspect(job: Job, board: Job | None) -> str:
    """The shape to draw in: the BOARD's, then the project's, then 16:9.

    The board's first, because the picture has to sit beside the board's other
    pictures — a shot drawn 9:16 into a 16:9 board is a letterboxed stranger in
    the cut whatever the project's own frame is set to.
    """
    if board is not None:
        aspect = str((board.params or {}).get("aspect_ratio") or "").strip()
        if aspect:
            return aspect
    return _settings_of(job).aspect_ratio or "16:9"


def _image_model_id(board: Job | None) -> tuple[str, str]:
    """(provider, model) that will draw this — resolved exactly as the draw will.

    ⚠ SHOWN, NOT CHOSEN. There is one image model and it is set in the
    environment (`IMAGE_PROVIDER` / `*_IMAGE_MODEL`), so the dialog says which
    one is about to run rather than offering a picker over a list of one. A
    misconfigured provider is itself a real answer and comes back empty, so the
    dialog still opens.
    """
    from gemini_client import _model_id, _resolve_provider

    try:
        provider = _resolve_provider((board.params or {}).get("provider") if board else None)
        return provider, _model_id(provider)
    except ValueError:
        return "", ""


def _neighbour_context(job: Job, frame_id: str, side: str) -> AnimaticNeighbourShotContext:
    """Everything the dialog opens on. Free — no model is called."""
    frame = _frame_or_404(job, frame_id)
    side = _neighbour_side(side)
    label = _neighbour_label(frame, side)

    board, reason = _neighbour_board(job, frame)
    provider, model = _image_model_id(board)

    if board is None:
        return AnimaticNeighbourShotContext(
            frame_id=frame_id, side=side, label=label,
            aspect_ratio=_neighbour_aspect(job, None),
            model=model, provider=provider,
            can_generate=False, reason=reason,
        )

    boards = {board.job_id: board}
    before, after, _window = _neighbour_pair(job, frame, side)
    return AnimaticNeighbourShotContext(
        frame_id=frame_id,
        side=side,
        label=label,
        storyboard_id=board.job_id,
        title=board.character_name or "Storyboard",
        before_description=_shot_wording(job, before, boards),
        after_description=_shot_wording(job, after, boards),
        aspect_ratio=_neighbour_aspect(job, board),
        model=model,
        provider=provider,
        can_generate=True,
    )


@router.get(
    "/{job_id}/frames/{frame_id}/neighbour",
    response_model=AnimaticNeighbourShotContext,
)
def get_neighbour_shot(
    job_id: str,
    frame_id: str,
    side: str = "after",
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. What the "generate a shot beside this one" dialog opens on.

    Answers "can this be done at all?" in the same breath, the same contract
    `get_frame_panel` follows — the dialog reads the reason out rather than
    offering a button that is going to 400.
    """
    job = _get_owned_animatic(job_id, current)
    return _neighbour_context(job, frame_id, side)


@router.post(
    "/{job_id}/frames/{frame_id}/neighbour/suggest",
    response_model=AnimaticNeighbourSuggestResponse,
)
def suggest_neighbour_shot(
    job_id: str,
    frame_id: str,
    body: AnimaticNeighbourSuggestRequest,
    current: CurrentUser = Depends(get_current_user),
    # Gated with the DRAWING it precedes, not on its own: this route writes the
    # prompt for a shot that `…/neighbour` then draws, so leaving it open would
    # let a customer without image generation fill a box whose button 403s.
    _gate: CurrentUser = Depends(require_feature("cap.image-generate")),
):
    """SPENDS QUOTA — a TEXT call, which costs a fraction of a drawing.

    ⚠ IT WRITES INTO THE BOX, IT DOES NOT DRAW. Nothing is generated until the
    user has read what it wrote and pressed the button below it, which is the
    same two-step every spending path in this editor follows: the expensive
    thing is never the thing a single press does.

    The suggestion is written from the two shots either side and the stretch of
    film around them, so it is a beat that is MISSING rather than a restatement
    of the shot that was right-clicked.
    """
    from script_breakdown import ScriptBreakdownError, suggest_shot_between

    job = _get_owned_animatic(job_id, current)
    frame = _frame_or_404(job, frame_id)
    side = _neighbour_side(body.side)
    board, reason = _neighbour_board(job, frame)
    if board is None:
        raise HTTPException(status_code=400, detail=reason)

    boards = {board.job_id: board}
    before, after, window = _neighbour_pair(job, frame, side)
    outline = [w for w in (_shot_wording(job, f, boards) for f in window) if w]

    try:
        description = suggest_shot_between(
            previous=_shot_wording(job, before, boards),
            following=_shot_wording(job, after, boards),
            outline=outline,
            notes=body.notes,
            title=board.character_name or "",
            provider=(board.params or {}).get("provider"),
        )
    except ScriptBreakdownError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None

    logger.info(
        "[animatic %s] suggested a shot %s %s for %s", job_id, side, frame_id, current.email
    )
    return AnimaticNeighbourSuggestResponse(description=description)


@router.post(
    "/{job_id}/frames/{frame_id}/neighbour",
    response_model=AnimaticNeighbourShotResponse,
)
def generate_neighbour_shot(
    job_id: str,
    frame_id: str,
    body: AnimaticNeighbourShotRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
):
    """SPENDS QUOTA. Draw the shot that goes beside this clip.

    One image, synchronously — the same single call the board's own Regenerate
    makes, through the board's style, aspect, references and continuity bible,
    so the new shot sits between its neighbours instead of looking like it came
    from somewhere else.

    ⚠ IT IS RETURNED, NOT SAVED. The clip comes back and the CLIENT decides
    where in the cut it goes — the same contract the image, video and board
    imports follow, and the reason the timeline can put it beside the clip you
    right-clicked and ripple everything after it in one undoable edit.

    ⚠ AND THE STORYBOARD IS NOT TOUCHED. See the note at the top of this
    section: a panel inserted into the board renumbers the panels after it, and
    animatic frames reference panels by index.
    """
    from storyboard_pipeline import draw_loose_shot

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is exporting.")

    frame = _frame_or_404(job, frame_id)
    side = _neighbour_side(body.side)
    board, reason = _neighbour_board(job, frame)
    if board is None:
        raise HTTPException(status_code=400, detail=reason)
    if board.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="That storyboard is busy right now — wait for it to finish, or stop it.",
        )

    existing = len(_frames_of(job))
    if existing + 1 > config.MAX_ANIMATIC_FRAMES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"This project already has {existing} clips, the limit of "
                f"{config.MAX_ANIMATIC_FRAMES}."
            ),
        )

    boards = {board.job_id: board}
    before, after, _window = _neighbour_pair(job, frame, side)
    params = board.params or {}
    variant_list, active = variants_of(board.result or {})
    variant_style = (
        (variant_list[active].get("style") if variant_list else None)
        or params.get("style")
        or "custom"
    )

    # The look anchor is the clip that was right-clicked, when it is a panel —
    # the nearest drawn picture there is, and the one the new shot will sit
    # directly beside. `_continuity_for_redraw` picks the nearest panel of the
    # same SCENE for a redraw; here the neighbour IS the scene.
    anchor_index = (
        int(frame.src.index)
        if frame.src.kind in ("panel", "pose") and frame.src.index is not None
        else None
    )
    # ⚠ THE NEIGHBOURS' CAST, not this shot's — it has none, because nothing has
    # broken it down. Both sides, so a shot between a two-hander and a reaction
    # gets both faces locked to their references.
    characters: list = []
    assets_named: list = []
    for neighbour in (before, after):
        chars, props = _shot_cast(job, neighbour, boards)
        characters += [c for c in chars if c not in characters]
        assets_named += [a for a in props if a not in assets_named]

    aspect = (body.aspect_ratio or "").strip() or _neighbour_aspect(job, board)

    try:
        image = draw_loose_shot(
            board.job_id,
            body.description,
            style=variant_style,
            # The board's genre, so an inserted shot matches the film's look.
            genre=params.get("genre") or "",
            # …and its brand, so the new shot carries the same logo file.
            brand=params.get("brand") or {},
            aspect_ratio=aspect,
            output_dir=config.OUTPUT_DIR,
            characters=characters,
            assets_named=assets_named,
            character_ref_paths=params.get("character_ref_paths") or {},
            asset_ref_paths=params.get("asset_ref_paths") or {},
            variant=active,
            provider=params.get("provider"),
            world=params.get("world") or {},
            cast=params.get("cast") or [],
            assets=params.get("assets") or [],
            anchor_index=anchor_index,
            story_context={
                "previous": _shot_wording(job, before, boards),
                "next": _shot_wording(job, after, boards),
                "previous_same_scene": before is not None,
            },
        )
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[animatic %s] generating a shot %s %s failed", job_id, side, frame_id)
        raise HTTPException(status_code=502, detail=f"Could not draw the shot: {e}") from None

    if image is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "The model returned no picture (it may have been blocked by a "
                "safety filter). Try rewording the shot."
            ),
        )

    # It lands as an ordinary animatic upload — from here on nothing downstream
    # can tell it from a still that was dragged in, which is the point.
    upload_id = uuid.uuid4().hex[:12]
    os.makedirs(_media_dir(job_id), exist_ok=True)
    image.save(_image_path(job_id, upload_id), "PNG")

    clip = AnimaticFrame(
        id=uuid.uuid4().hex[:12],
        src={
            "kind": "upload",
            "upload_id": upload_id,
            # ⚠ THE BOARD IS CARRIED THOUGH THERE IS NO PANEL. It is what puts
            # this clip on the Storyboard images row (`clipRowKind`) and keeps
            # it filed under Storyboard Frames in the Media pane
            # (`frameOrigin`) — a shot generated into the board's row belongs
            # with the board's shots.
            "storyboard_id": board.job_id,
            # Its identity as a shot, since it has no panel index. This is what
            # a Veo take of it will pair with — see `_shot_key`.
            "shot_id": uuid.uuid4().hex[:12],
            "prompt": body.description.strip(),
        },
        duration_ms=body.duration_ms,
        label=_neighbour_label(frame, side),
        # Servable immediately, before the project is saved — the same url
        # `addFiles` gives a fresh upload.
        url=f"/animatics/{job_id}/media/{upload_id}",
    )
    logger.info(
        "[animatic %s] drew a shot %s %s (upload %s) for %s",
        job_id, side, frame_id, upload_id, current.email,
    )
    return AnimaticNeighbourShotResponse(frame=clip, model=_image_model_id(board)[1])


@router.get("/{job_id}/frames/{frame_id}/sequence", response_model=PanelSequenceInfo)
def get_frame_sequence(
    job_id: str,
    frame_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """The key poses of the shot behind this clip, counted off DISK.

    What the editor reads after a re-block finishes, to find out how many pose
    frames the shot now has and rebuild that run on the timeline.

    ⚠ A CLIP WITH NO BOARD BEHIND IT IS AN ANSWER, NOT AN ERROR — the same rule
    `get_frame_panel` above already follows, and this route was the one place
    that broke it. `RelengthShotInline` asks this for EVERY clip the user
    selects, because "has this shot got key poses?" is exactly what it needs to
    know before it draws anything; on an upload, a video clip or a colour card
    the 400 came back, was swallowed, and left a red failed request in the
    console on every single selection. Reported by `tests/e2e_animatic.py` §14,
    which fails the run on a console error and had no other way to see it.
    ⚠ THE REASON IS CARRIED, NOT DROPPED, so a caller can still tell "no board"
    apart from "a board with nothing drawn yet" — that distinction is the whole
    argument for a 200 over an empty 404.
    """
    job = _get_owned_animatic(job_id, current)
    frame = _frame_or_404(job, frame_id)
    board, reason = _board_behind(job, frame)
    if board is None:
        return PanelSequenceInfo(index=0, reason=reason)
    return PanelSequenceInfo(**sequence_summary(board, int(frame.src.index or 0)))


@router.post(
    "/{job_id}/frames/{frame_id}/sequence",
    response_model=JobCreatedResponse,
    status_code=202,
)
def relength_frame_sequence(
    job_id: str,
    frame_id: str,
    body: AnimaticRelengthRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
):
    """SPENDS QUOTA. Re-block this shot's key poses at a new length.

    "Make this shot 2s longer". Runs off-request on the BOARD's job — the
    drawings belong to the board, not to this animatic — so the returned job_id
    is the STORYBOARD's, and that is what the editor polls.

    ⚠ IT RESUMES. Every pose already on disk is kept, the plan they were drawn
    from is handed to the planner as fixed, and only the new tail is drawn. So
    4s → 6s costs eight drawings rather than twenty-four, and — the part that
    matters more — drawing 17 continues the motion drawings 1–16 actually made.
    See `panel_sequence.plan_beats`.
    """
    job = _get_owned_animatic(job_id, current)
    frame = _frame_or_404(job, frame_id)
    board, reason = _board_behind(job, frame)
    if board is None:
        raise HTTPException(status_code=400, detail=reason)

    index = int(frame.src.index or 0)
    queued = submit_sequence_run(board, index, body.duration_seconds, resume=True)
    logger.info(
        "[animatic %s] shot %s re-blocked board %s panel %d to %ss (%d new)",
        job_id, frame_id, board.job_id, index,
        queued["duration_seconds"], queued["wanted"],
    )
    if queued["wanted"] <= 0:
        message = (
            f"This shot is already blocked out to {queued['duration_seconds']}s — "
            "nothing new to draw."
        )
    else:
        message = (
            f"Carrying this shot on to {queued['duration_seconds']}s — drawing "
            f"{queued['wanted']} more key pose(s). The {queued['have']} already "
            "drawn are kept."
        )
    return JobCreatedResponse(
        job_id=board.job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.STORYBOARD,
        character_name=board.character_name,
        message=message,
    )


# --- Auto-reframe -----------------------------------------------------------
def _reframable(job: Job, frame_ids: list[str] | None = None) -> list[AnimaticFrame]:
    """The clips a reframe pass can actually look at.

    A colour card has no picture and a video clip's framing is a property of the
    footage rather than of a still, so both are skipped — silently, because
    "reframe everything" on a mixed timeline is a perfectly ordinary request and
    a card in the middle of it is not an error.
    """
    wanted = set(frame_ids or [])
    out = []
    for f in _frames_of(job):
        if wanted and f.id not in wanted:
            continue
        if (f.kind or "image") != "image":
            continue
        if f.src.kind not in ("panel", "pose", "upload"):
            continue
        out.append(f)
    return out


def _reframe_target(job: Job, aspect_ratio: str = "") -> str:
    """What shape to frame FOR. The project's own unless told otherwise."""
    return (aspect_ratio or "").strip() or _settings_of(job).aspect_ratio


@router.post("/{job_id}/reframe/estimate", response_model=ReframeCostEstimate)
def estimate_reframe(
    job_id: str,
    body: AnimaticReframeRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. What re-framing these shots would cost, before anything is spent."""
    import autoframe

    job = _get_owned_animatic(job_id, current)
    frames = _reframable(job, body.frame_ids)
    quote = autoframe.estimate(len(frames))
    return ReframeCostEstimate(
        frames=quote["frames"],
        usd=quote["usd"],
        model=quote["model"],
        aspect_ratio=_reframe_target(job, body.aspect_ratio),
        over_limit=quote["over_limit"],
        limit=f"{quote['limit_frames']} shots per run",
    )


@router.post("/{job_id}/reframe", response_model=JobCreatedResponse, status_code=202)
def reframe_animatic(
    job_id: str,
    body: AnimaticReframeRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
):
    """SPENDS QUOTA. Find the subject of each shot and frame it for a new shape."""
    import autoframe

    job = _get_owned_animatic(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is already busy — wait for it to finish, or stop it.",
        )
    frames = _reframable(job, body.frame_ids)
    if not frames:
        raise HTTPException(
            status_code=409,
            detail=(
                "None of these clips is a still with a picture behind it, so "
                "there is nothing to re-frame."
            ),
        )
    quote = autoframe.estimate(len(frames))
    if quote["over_limit"]:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That is {quote['frames']} shots; the limit for one reframe run "
                f"is {quote['limit_frames']}. This is a spend guard, not a "
                "technical one — do it in a couple of passes."
            ),
        )

    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        error=None,
        progress={"percent": 0, "stage": "reframe", "message": "Looking at the first shot…"},
    )
    worker.submit_animatic_reframe(job_id, body.model_dump())
    target = _reframe_target(job, body.aspect_ratio)
    logger.info(
        "[animatic %s] reframe to %s requested by %s (%d shot(s), est. $%.4f)",
        job_id, target, current.email, quote["frames"], quote["usd"],
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.ANIMATIC,
        character_name=job.character_name or "Project",
        message=(
            f"Re-framing {quote['frames']} shot(s) for {target} — "
            f"estimated ${quote['usd']:.4f}."
        ),
    )


def _write_frames(job_id: str, frames: list[dict]) -> None:
    """Replace the frame list on a job. Read-modify-write on `params`.

    Safe for the same reason `_write_texts` is: the job is RUNNING for the whole
    life of the pass and `save_animatic` refuses to write through that, so there
    is exactly one writer. And in `params` rather than `result` for the same
    reason too — a reframe is ordinary project content the user then edits, and
    content in `result` would be invisible to every save the editor makes after.
    """
    store = get_store()
    job = store.get(job_id)
    if job is None:
        return
    params = dict(job.params or {})
    params["frames"] = frames
    store.update(job_id, params=params)


def run_reframe(job_id: str, body: dict, progress_cb=None) -> None:
    """Look at each shot and write its new framing. Called from the worker.

    ⚠ WHAT LANDS ON THE CLIP IS `scale` / `x` / `y` AND NOTHING ELSE — the three
    properties `AnimaticFrame` has carried since the scene model, which the
    monitor and `animatic_render.place_picture` already agree about. There is no
    crop field, no new render path, and an auto-reframed shot is indistinguishable
    from one somebody panned by hand: it keyframes, it undoes, it exports. A clip
    that was ALREADY animated keeps its move — see `autoframe.apply_to_frame`.

    ⚠ EVERY SHOT IS WRITTEN AS IT LANDS, not in one write at the end. Each one
    has been paid for by the time it comes back, and a pass that fails on shot
    nineteen must not throw away the eighteen the user was billed for.
    """
    import autoframe
    from PIL import Image

    import animatic

    request = AnimaticReframeRequest(**(body or {}))
    job = get_store().get(job_id)
    if job is None:
        raise autoframe.AutoframeError("This project no longer exists.")

    settings = _settings_of(job)
    target = _reframe_target(job, request.aspect_ratio)
    size = animatic.resolve_size(target, settings.resolution)
    wanted = [f.id for f in _reframable(job, request.frame_ids)]
    if not wanted:
        raise autoframe.AutoframeError("There is nothing on this timeline to re-frame.")

    done = 0
    reframed = 0
    skipped: list[str] = []
    for frame_id in wanted:
        # Re-read every time: this is a long pass and the frame list is the
        # document. Reading it once at the top would write a stale list back.
        job = get_store().get(job_id)
        if job is None:
            raise autoframe.AutoframeError("This project no longer exists.")
        frames = _frames_of(job)
        frame = next((f for f in frames if f.id == frame_id), None)
        if frame is None:
            done += 1
            continue

        if progress_cb:
            progress_cb(done, len(wanted), frame.label or "this shot")

        path = _resolve_frame_path(job, frame)
        if not path:
            skipped.append(frame.label or frame.id)
            done += 1
            continue
        try:
            with Image.open(path) as im:
                source_size = im.size
            subject = autoframe.detect_subject(path, hint=frame.label or "")
        except (autoframe.AutoframeError, OSError) as e:
            # ONE shot the model wouldn't box is not a failed pass. The rest are
            # still worth doing, and the ones already written are already paid
            # for — see the docstring.
            logger.warning("[animatic %s] reframe skipped %s: %s", job_id, frame_id, e)
            skipped.append(frame.label or frame.id)
            done += 1
            continue

        values = autoframe.reframe_values(subject, source_size, size, fit=settings.fit)
        patch = autoframe.apply_to_frame(frame.model_dump(), values)
        _write_frames(
            job_id,
            [
                {**f.model_dump(exclude={"url"}), **patch}
                if f.id == frame_id
                else f.model_dump(exclude={"url"})
                for f in frames
            ],
        )
        reframed += 1
        done += 1
        logger.info(
            "[animatic %s] %s → %s: scale %.2f at (%.2f, %.2f)%s",
            job_id, frame_id, subject.get("subject") or "subject",
            values["scale"], values["x"], values["y"],
            "" if values["fits"] else "  [SUBJECT TOO BIG TO FIT — framed as close as it goes]",
        )

    logger.info(
        "[animatic %s] reframe to %s done: %d re-framed, %d skipped.",
        job_id, target, reframed, len(skipped),
    )
    if not reframed:
        raise autoframe.AutoframeError(
            "None of those shots could be re-framed — the model returned no "
            "subject for any of them."
        )
