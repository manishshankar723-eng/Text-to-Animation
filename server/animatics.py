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

Nothing in this module calls an AI backend — an animatic costs no quota.
"""

import glob
import io
import logging
import os
import re
import shutil
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

import panel_sequence

from . import config, worker
from .auth import CurrentUser, get_current_user
from .common import board_dir, get_owned_job, panel_path, variants_of
from .jobs import get_store
from .schemas import (
    AnimaticAudio,
    AnimaticAudioResponse,
    AnimaticCreateRequest,
    AnimaticFrame,
    AnimaticLayer,
    AnimaticMediaItem,
    AnimaticOverlay,
    AnimaticProject,
    AnimaticSaveRequest,
    AnimaticSettings,
    AnimaticShape,
    AnimaticSummary,
    AnimaticTextClip,
    AnimaticUploadResponse,
    Job,
    JobCreatedResponse,
    JobKind,
    JobStatus,
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


def _video_path(job_id: str) -> str:
    return os.path.join(_animatic_dir(job_id), "animatic.mp4")


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


# ---------------------------------------------------------------------------
# Reading a project off a job record
# ---------------------------------------------------------------------------
def _get_owned_animatic(job_id: str, current: CurrentUser) -> Job:
    job = get_owned_job(job_id, current)
    if job.kind != JobKind.ANIMATIC:
        raise HTTPException(status_code=400, detail="Not an animatic.")
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


def _texts_of(job: Job) -> list[AnimaticTextClip]:
    raw = (job.params or {}).get("texts") or []
    out: list[AnimaticTextClip] = []
    for item in raw:
        try:
            out.append(AnimaticTextClip(**item))
        except Exception:  # noqa: BLE001 — one bad clip must not 500 the project
            logger.warning("[animatic %s] dropping unreadable text clip %r", job.job_id, item)
    return out


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


def _audio_tracks_of(job: Job) -> list[AnimaticAudio]:
    """Every audio track on this animatic, oldest first.

    Migrates records written before multi-track, which carried a single `audio`
    object rather than a list. Nothing rewrites those on disk — they're read
    forward, so an old animatic just opens with one track.
    """
    params = job.params or {}
    raw = params.get("audio_tracks")
    if raw is None:
        single = params.get("audio")
        raw = [single] if single else []

    out: list[AnimaticAudio] = []
    for item in raw or []:
        try:
            out.append(AnimaticAudio(**item))
        except Exception:  # noqa: BLE001 — one bad track must not 500 the project
            logger.warning("[animatic %s] dropping unreadable audio track %r", job.job_id, item)
    return out


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
    """
    frames = _frames_of(job)
    for f in frames:
        f.url = f"/animatics/{job.job_id}/frame/{f.id}"
    overlays = _overlays_of(job)
    for overlay in overlays:
        # Same url shape as a frame's picture — the editor fetches both the
        # same way, and an overlay is servable the moment it is uploaded.
        overlay.url = f"/animatics/{job.job_id}/media/{overlay.upload_id}"
    tracks = _audio_tracks_of(job)
    for track in tracks:
        # By upload id, not the project-level /audio route: straight after an
        # upload the file is on disk but not yet saved onto the project.
        track.url = f"/animatics/{job.job_id}/media/{track.upload_id}"

    return AnimaticProject(
        job_id=job.job_id,
        title=job.character_name or "Animatic",
        status=job.status,
        source_storyboard_id=(job.params or {}).get("source_storyboard_id"),
        settings=_settings_of(job),
        frames=frames,
        texts=_texts_of(job),
        shapes=_shapes_of(job),
        layers=_layers_of(job),
        overlays=overlays,
        audio_tracks=tracks,
        duration_ms=_duration_ms(frames),
        video=(job.result or {}).get("video"),
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _summarise(job: Job) -> AnimaticSummary:
    frames = _frames_of(job)
    return AnimaticSummary(
        job_id=job.job_id,
        title=job.character_name or "Animatic",
        status=job.status,
        aspect_ratio=_settings_of(job).aspect_ratio,
        frame_count=len(frames),
        duration_ms=_duration_ms(frames),
        cover_url=f"/animatics/{job.job_id}/frame/{frames[0].id}" if frames else None,
        text_count=len(_texts_of(job)),
        audio_count=len(_audio_tracks_of(job)),
        has_audio=bool(_audio_tracks_of(job)),
        has_video=bool((job.result or {}).get("video")),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _resolve_frame_path(job: Job, frame: AnimaticFrame) -> str | None:
    """Local image file behind one frame, or None if it can't be found.

    A board panel is resolved through the board's ACTIVE style variant every
    time, so re-styling or re-drawing on the storyboard shows up in the animatic
    with nothing to re-import.
    """
    src = frame.src
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
            title = f"{board.character_name or 'Storyboard'} — animatic"
        # Inherit the board's frame shape so panels aren't letterboxed by default.
        if body.settings is None:
            settings.aspect_ratio = (board.params or {}).get("aspect_ratio") or settings.aspect_ratio

    if len(frames) > config.MAX_ANIMATIC_FRAMES:
        raise HTTPException(
            status_code=413,
            detail=f"An animatic can hold at most {config.MAX_ANIMATIC_FRAMES} frames.",
        )

    job = get_store().create(
        character_name=title or "Untitled animatic",
        kind=JobKind.ANIMATIC,
        owner=current.email,
        params={
            "settings": settings.model_dump(),
            "frames": [f.model_dump(exclude={"url"}) for f in frames],
            "texts": [],
            "shapes": [],
            "layers": [],
            "overlays": [],
            "audio_tracks": [],
            "source_storyboard_id": source_id,
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
    """The caller's saved animatics, newest first (the library grid)."""
    jobs = get_store().list(limit=limit, owner=current.email, kinds=[JobKind.ANIMATIC])
    return [_summarise(j) for j in jobs]


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
            detail="This animatic is exporting — wait for it to finish, or stop it first.",
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
                detail=f"An animatic can hold at most {config.MAX_ANIMATIC_FRAMES} frames.",
            )
        params["frames"] = [f.model_dump(exclude={"url"}) for f in body.frames]

    if body.texts is not None:
        if len(body.texts) > config.MAX_ANIMATIC_TEXTS:
            raise HTTPException(
                status_code=413,
                detail=f"An animatic can hold at most {config.MAX_ANIMATIC_TEXTS} text clips.",
            )
        params["texts"] = [t.model_dump() for t in body.texts]

    if body.shapes is not None:
        if len(body.shapes) > config.MAX_ANIMATIC_SHAPES:
            raise HTTPException(
                status_code=413,
                detail=f"An animatic can hold at most {config.MAX_ANIMATIC_SHAPES} shapes.",
            )
        params["shapes"] = [s.model_dump() for s in body.shapes]

    if body.layers is not None:
        if len(body.layers) > config.MAX_ANIMATIC_LAYERS:
            raise HTTPException(
                status_code=413,
                detail=f"An animatic can hold at most {config.MAX_ANIMATIC_LAYERS} layers.",
            )
        params["layers"] = [layer.model_dump() for layer in body.layers]

    if body.overlays is not None:
        if len(body.overlays) > config.MAX_ANIMATIC_SHAPES:
            raise HTTPException(
                status_code=413,
                detail=f"An animatic can hold at most {config.MAX_ANIMATIC_SHAPES} overlays.",
            )
        # `url` is filled on read, so it is never stored — see AnimaticOverlay.
        params["overlays"] = [o.model_dump(exclude={"url"}) for o in body.overlays]

    if body.audio_tracks is not None:
        if len(body.audio_tracks) > config.MAX_ANIMATIC_AUDIO_TRACKS:
            raise HTTPException(
                status_code=413,
                detail=f"An animatic can hold at most {config.MAX_ANIMATIC_AUDIO_TRACKS} audio tracks.",
            )
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
        raise HTTPException(status_code=404, detail=f"Animatic '{job_id}' not found.")
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
            detail="This animatic is still exporting — wait for it to finish first.",
        )
    folder = _animatic_dir(job_id)
    if os.path.isdir(folder):
        try:
            shutil.rmtree(folder)
        except OSError:
            logger.exception("[animatic %s] could not remove %s", job_id, folder)
    get_store().delete(job_id)
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
        raise HTTPException(status_code=409, detail="This animatic is exporting.")

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
            # Normalise whatever came in (jpg/webp/…) to a clean RGB PNG, exactly
            # as the character-reference upload does.
            with PILImage.open(io.BytesIO(contents)) as im:
                rgb = im.convert("RGB")
                rgb.save(path, "PNG")
                width, height = rgb.size
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
        raise HTTPException(status_code=409, detail="This animatic is exporting.")

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


@router.get("/{job_id}/frame/{frame_id}")
def get_frame_image(
    job_id: str,
    frame_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve one frame's picture — board panel or upload, same URL either way."""
    job = _get_owned_animatic(job_id, current)
    frame = next((f for f in _frames_of(job) if f.id == frame_id), None)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found.")
    path = _resolve_frame_path(job, frame)
    if not path:
        raise HTTPException(status_code=404, detail="This frame's image is missing.")
    return FileResponse(path, media_type="image/png")


@router.get("/{job_id}/media/{upload_id}")
def get_upload(
    job_id: str,
    upload_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve a just-uploaded file — image OR audio — by its upload id.

    This is the route the editor uses for media it has only just uploaded, and
    it exists because the project-level routes can't answer yet: the editor's
    save is debounced, so for the best part of a second the file is on disk but
    is not yet ON the project. Serving by upload id has no such dependency.
    (The audio case was a real bug: the waveform 404'd and never drew until the
    page was reloaded. Browser-tested — don't route audio through /audio here.)
    """
    _get_owned_animatic(job_id, current)

    image = _image_path(job_id, upload_id)
    if image and os.path.isfile(image):
        return FileResponse(image, media_type="image/png")

    audio = _audio_file(job_id, upload_id)
    if audio and os.path.isfile(audio):
        return FileResponse(audio)

    raise HTTPException(status_code=404, detail="Upload not found.")


@router.get("/{job_id}/audio")
def get_audio(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Serve the animatic's audio track (for the waveform and for playback)."""
    job = _get_owned_animatic(job_id, current)
    tracks = _audio_tracks_of(job)
    path = _audio_file(job_id, tracks[0].upload_id) if tracks else None
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No audio on this animatic.")
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
        raise HTTPException(status_code=409, detail="This animatic is already exporting.")

    frames = _frames_of(job)
    if not frames:
        raise HTTPException(
            status_code=409, detail="Add some images before exporting the video."
        )

    settings = _settings_of(job)
    # Resolved here, not in the worker: this is the request that knows who is
    # asking. Muted tracks are dropped rather than mixed in at zero.
    audio_tracks = []
    for track in (_audio_tracks_of(job) if settings.include_audio else []):
        path = _audio_file(job_id, track.upload_id)
        if not path or track.muted or track.volume <= 0:
            continue
        audio_tracks.append({
            "path": path,
            "offset_ms": track.offset_ms,
            "trim_ms": track.trim_ms,
            "volume": track.volume,
        })

    resolved = [
        {
            "path": _resolve_frame_path(job, f),
            "duration_ms": f.duration_ms,
            "label": f.label,
        }
        for f in frames
    ]

    # Overlay pictures. Resolved here for the same reason the frames are: this
    # is the request that knows who is asking. One whose file has gone is
    # dropped rather than failing the whole export.
    overlays = []
    for overlay in _overlays_of(job):
        path = _image_path(job_id, overlay.upload_id)
        if not path or not os.path.isfile(path):
            logger.warning(
                "[animatic %s] overlay %s has no image — skipped", job_id, overlay.id
            )
            continue
        item = overlay.model_dump(exclude={"url"})
        item["path"] = path
        overlays.append(item)
    if not any(f["path"] for f in resolved):
        raise HTTPException(
            status_code=409,
            detail="None of these frames have an image any more — re-add them and try again.",
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
            end_ms = max(end_ms, playable)
        for clip in _texts_of(job):
            if (clip.text or "").strip():
                end_ms = max(end_ms, clip.start_ms + clip.duration_ms)
        for shape in _shapes_of(job):
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
            "texts": [t.model_dump() for t in _texts_of(job)],
            "shapes": [s.model_dump() for s in _shapes_of(job)],
            "overlays": overlays,
            "audio_tracks": audio_tracks,
            "aspect_ratio": settings.aspect_ratio,
            "resolution": settings.resolution,
            "quality": settings.quality,
            "end_ms": end_ms or None,
            "fps": settings.fps,
            "fit": settings.fit,
            "background": settings.background,
            "show_labels": settings.show_labels,
            "output_dir": config.OUTPUT_DIR,
        },
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.ANIMATIC,
        character_name=job.character_name or "Animatic",
        message=f"Exporting your animatic. Poll GET /jobs/{job_id}.",
    )


@router.post("/{job_id}/stop")
def stop_export(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Stop an export in progress. ffmpeg is terminated; no video is written."""
    job = _get_owned_animatic(job_id, current)
    if job.status != JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This animatic isn't exporting.")

    from cancel import request_cancel

    request_cancel(job_id)
    logger.info("[animatic %s] export stop requested by %s", job_id, current.email)
    return {"stopping": True, "job_id": job_id}


@router.get("/{job_id}/video")
def download_video(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Download the exported MP4."""
    job = _get_owned_animatic(job_id, current)
    path = _video_path(job_id)
    if not os.path.isfile(path):
        raise HTTPException(
            status_code=404, detail="No video yet — export this animatic first."
        )
    safe = "".join(
        c if c.isalnum() or c in "-_ " else " " for c in (job.character_name or "animatic")
    )
    safe = " ".join(safe.split()).strip(" -_") or "animatic"
    return FileResponse(path, media_type="video/mp4", filename=f"{safe}.mp4")
