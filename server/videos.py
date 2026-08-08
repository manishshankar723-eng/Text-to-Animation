"""
videos.py — Animatics → Final Video: per-shot Veo renders, then one cut.

The last workflow in the pipeline. An animatic is timed pictures; this turns
each picture into MOVING footage and joins the results:

    1. Apply final art & characters — build the art tray (step 1). Stills that
       lock the look: generated character sheets, a drawn panel, an upload.
    2. Render shots (step 2)        — one Veo clip per shot. THIS COSTS MONEY.
    3. Assemble the sequence (step 3) — concatenate the clips. Free, repeatable.

A project IS a job (`JobKind.FINAL_VIDEO`), like every other workflow — see the
storage rule in AGENTS.md. The job's STATUS describes whichever long operation
is in flight (a render batch or an assembly), exactly as an animatic's status
describes its export; each shot carries its own render state, so a half-rendered
project is legible and a failed shot never hides the ones that worked.

WHY NOT GOOGLE FLOW: Flow has no API and its credits are not API credits — see
the header of video_client.py. This calls Veo, which is the model Flow runs on.

MONEY: rendering is billed per second of output. Every endpoint that can spend
reports an estimate first, caps the batch (config.MAX_VIDEO_BATCH), and refuses
to silently re-render a shot that already has a clip.
"""

import io
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import config, worker
from .auth import CurrentUser, get_current_user
from .common import get_owned_job, panel_path, variants_of
from .jobs import get_store
from .schemas import (
    AnimaticFrameSource,
    CostEstimate,
    FinalArtRef,
    FinalVideoCreateRequest,
    FinalVideoProject,
    FinalVideoSaveRequest,
    FinalVideoSettings,
    FinalVideoShot,
    FinalVideoSummary,
    Job,
    JobCreatedResponse,
    JobKind,
    JobStatus,
    RenderSettings,
    RenderShotsRequest,
    ShotStatus,
    VideoBackendStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/final-videos", tags=["final video"])

# Upload ids are minted here, so they are validated on the way back IN as well —
# this is what stops a crafted id from walking out of the media folder.
_ID_RE = re.compile(r"^[a-f0-9]{6,32}$")
# Shot and art ids come from the client and end up in FILENAMES, so they are
# held to a stricter alphabet than "any string" before touching a path.
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _project_dir(job_id: str) -> str:
    return os.path.join(config.OUTPUT_DIR, "_final_videos", job_id)


def _media_dir(job_id: str) -> str:
    return os.path.join(_project_dir(job_id), "media")


def _clips_dir(job_id: str) -> str:
    return os.path.join(_project_dir(job_id), "clips")


def _final_path(job_id: str) -> str:
    return os.path.join(_project_dir(job_id), "final.mp4")


def _image_path(job_id: str, upload_id: str) -> str | None:
    """Local path of an uploaded still, or None if the id is bogus."""
    if not upload_id or not _ID_RE.match(upload_id):
        return None
    return os.path.join(_media_dir(job_id), f"img_{upload_id}.png")


def _clip_path(job_id: str, shot_id: str) -> str | None:
    """Local path of one shot's rendered MP4, or None if the id is bogus."""
    if not shot_id or not _CLIENT_ID_RE.match(shot_id):
        return None
    return os.path.join(_clips_dir(job_id), f"shot_{shot_id}.mp4")


# ---------------------------------------------------------------------------
# Reading a project off a job record
# ---------------------------------------------------------------------------
def _get_owned_video(job_id: str, current: CurrentUser) -> Job:
    job = get_owned_job(job_id, current)
    if job.kind != JobKind.FINAL_VIDEO:
        raise HTTPException(status_code=400, detail="Not a final-video project.")
    return job


def _shots_of(job: Job) -> list[FinalVideoShot]:
    raw = (job.params or {}).get("shots") or []
    out: list[FinalVideoShot] = []
    for item in raw:
        try:
            out.append(FinalVideoShot(**item))
        except Exception:  # noqa: BLE001 — one corrupt shot must not 500 the project
            logger.warning("[final %s] dropping unreadable shot %r", job.job_id, item)
    return out


def _art_of(job: Job) -> list[FinalArtRef]:
    raw = (job.params or {}).get("art") or []
    out: list[FinalArtRef] = []
    for item in raw:
        try:
            out.append(FinalArtRef(**item))
        except Exception:  # noqa: BLE001 — one bad ref must not 500 the project
            logger.warning("[final %s] dropping unreadable art ref %r", job.job_id, item)
    return out


def _settings_of(job: Job) -> FinalVideoSettings:
    try:
        return FinalVideoSettings(**((job.params or {}).get("settings") or {}))
    except Exception:  # noqa: BLE001
        return FinalVideoSettings()


def _effective_render(shot: FinalVideoShot, settings: FinalVideoSettings) -> RenderSettings:
    """The render settings this shot will actually use (its own, or inherited)."""
    return shot.settings or settings.render


def _spent(shots: list[FinalVideoShot]) -> float:
    return round(sum(float(s.cost_usd or 0) for s in shots), 2)


def _project_of(job: Job) -> FinalVideoProject:
    """Build the client-facing project, filling in serve URLs.

    Like an animatic's frames, every picture gets ONE url shape whatever it is
    underneath, and the path is re-resolved on each request — so re-drawing a
    panel on the board updates the shot here with nothing to re-import.
    """
    shots = _shots_of(job)
    for s in shots:
        s.image_url = f"/final-videos/{job.job_id}/shot/{s.id}/image"
        s.url = f"/final-videos/{job.job_id}/shot/{s.id}/clip" if s.status == ShotStatus.READY else None
    art = _art_of(job)
    for a in art:
        a.url = f"/final-videos/{job.job_id}/art/{a.id}"

    return FinalVideoProject(
        job_id=job.job_id,
        title=job.character_name or "Final video",
        status=job.status,
        source_animatic_id=(job.params or {}).get("source_animatic_id"),
        source_storyboard_id=(job.params or {}).get("source_storyboard_id"),
        settings=_settings_of(job),
        shots=shots,
        art=art,
        video=(job.result or {}).get("video"),
        spent_usd=_spent(shots),
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _summarise(job: Job) -> FinalVideoSummary:
    shots = _shots_of(job)
    rendered = [s for s in shots if s.status == ShotStatus.READY]
    # The card's LENGTH is the length of the cut, so excluded shots don't count
    # towards it — but they do stay in `rendered_count`, because they were paid
    # for and pretending otherwise would misreport what the project has done.
    in_cut = [s for s in rendered if s.include]
    return FinalVideoSummary(
        job_id=job.job_id,
        title=job.character_name or "Final video",
        status=job.status,
        aspect_ratio=_settings_of(job).aspect_ratio,
        shot_count=len(shots),
        rendered_count=len(rendered),
        duration_ms=sum(int(s.duration_ms or 0) for s in in_cut),
        cover_url=f"/final-videos/{job.job_id}/shot/{shots[0].id}/image" if shots else None,
        has_video=bool((job.result or {}).get("video")),
        spent_usd=_spent(shots),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# ---------------------------------------------------------------------------
# Resolving a picture to a file on disk
# ---------------------------------------------------------------------------
def _resolve_panel(job: Job, storyboard_id: str | None, index: int | None) -> str | None:
    """A drawn panel of a board, through its ACTIVE style variant.

    The owner check is not optional: sources are user-editable JSON, so without
    it a crafted board id would read another account's panels.
    """
    if not storyboard_id or not _ID_RE.match(storyboard_id) or index is None:
        return None
    board = get_store().get(storyboard_id)
    if board is None or board.owner != job.owner or board.kind != JobKind.STORYBOARD:
        return None
    _, active = variants_of(board.result or {})
    path = panel_path(storyboard_id, int(index), active)
    return path if os.path.isfile(path) else None


def _resolve_src(job: Job, src: AnimaticFrameSource) -> str | None:
    """The still behind one shot's first frame, or None if it can't be found."""
    if src.kind == "upload":
        path = _image_path(job.job_id, src.upload_id or "")
        return path if path and os.path.isfile(path) else None
    if src.kind == "panel":
        return _resolve_panel(job, src.storyboard_id, src.index)
    return None


def _resolve_art(job: Job, ref: FinalArtRef) -> str | None:
    """The still behind one art reference, or None if it can't be found.

    'asset' reaches into a Text-to-Image character run — the whole point of
    putting this workflow downstream of that one. Same owner check as a panel,
    and for the same reason.
    """
    if ref.kind == "upload":
        path = _image_path(job.job_id, ref.upload_id or "")
        return path if path and os.path.isfile(path) else None

    if ref.kind == "panel":
        return _resolve_panel(job, ref.storyboard_id, ref.index)

    if ref.kind == "asset":
        asset_job_id = ref.asset_job_id or ""
        if not asset_job_id or not _ID_RE.match(asset_job_id):
            return None
        source = get_store().get(asset_job_id)
        if source is None or source.owner != job.owner or source.kind != JobKind.GENERATE:
            return None
        # Part/view land in a filename, so they are held to the same alphabet
        # as every other client-supplied path component.
        part, view = ref.part or "", ref.view or ""
        if not _CLIENT_ID_RE.match(part) or not _CLIENT_ID_RE.match(view):
            return None
        path = os.path.join(config.OUTPUT_DIR, source.character_name, f"{part}_{view}.png")
        return path if os.path.isfile(path) else None

    return None


def _read(path: str | None) -> bytes | None:
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Called by the worker (server.worker._run_shot_renders)
# ---------------------------------------------------------------------------
def update_shot(job_id: str, shot_id: str, **fields) -> None:
    """Write render state back onto one shot, leaving the rest untouched.

    Read-modify-write on the job's params. Safe because only ONE render batch
    per project can be in flight — the render endpoint 409s otherwise — so there
    is no second writer to race with.
    """
    store = get_store()
    job = store.get(job_id)
    if job is None:
        return
    params = dict(job.params or {})
    shots = list(params.get("shots") or [])
    for i, raw in enumerate(shots):
        if (raw or {}).get("id") == shot_id:
            merged = dict(raw)
            merged.update(fields)
            shots[i] = merged
            break
    else:
        return
    params["shots"] = shots
    try:
        store.update(job_id, params=params)
    except Exception:  # noqa: BLE001 — a lost state write must not kill the batch
        logger.exception("[final %s] could not persist shot %s state", job_id, shot_id)


def render_one_shot(job_id: str, shot_id: str, progress_cb=None, cancel_check=None) -> None:
    """Render ONE shot with Veo and store the clip. Raises on failure.

    Everything the render needs is resolved here rather than being handed in by
    the request: the job record carries its owner, so the same checks apply and
    the endpoint stays a thin, fast accept.
    """
    from video_client import VideoGenerationError, estimate_cost_usd, render_shot

    store = get_store()
    job = store.get(job_id)
    if job is None:
        raise VideoGenerationError("This project no longer exists.")

    shot = next((s for s in _shots_of(job) if s.id == shot_id), None)
    if shot is None:
        raise VideoGenerationError("This shot is no longer part of the project.")

    settings = _settings_of(job)
    render = _effective_render(shot, settings)

    image = _read(_resolve_src(job, shot.src))
    if not image:
        raise VideoGenerationError(
            "This shot's source picture is missing — the panel may have been "
            "deleted from the board, or the upload removed."
        )

    art_by_id = {a.id: a for a in _art_of(job)}
    references: list[bytes] = []
    for ref_id in (shot.reference_ids or [])[: config.MAX_VIDEO_REFERENCES]:
        ref = art_by_id.get(ref_id)
        data = _read(_resolve_art(job, ref)) if ref else None
        if data:
            references.append(data)
        elif ref is not None:
            # Worth saying out loud: a silently dropped reference looks like Veo
            # ignoring the character sheet, which is a confusing thing to debug.
            logger.warning("[final %s] art ref %s has no readable file", job_id, ref_id)

    last_frame = None
    if shot.last_frame_ref_id:
        ref = art_by_id.get(shot.last_frame_ref_id)
        last_frame = _read(_resolve_art(job, ref)) if ref else None

    clip_path = _clip_path(job_id, shot_id)
    if not clip_path:
        raise VideoGenerationError("This shot has an unusable id.")
    os.makedirs(_clips_dir(job_id), exist_ok=True)

    data = render_shot(
        image,
        shot.prompt,
        tier=render.tier,
        aspect_ratio=settings.aspect_ratio,
        resolution=render.resolution,
        duration_seconds=render.duration_seconds,
        generate_audio=render.generate_audio,
        negative_prompt=render.negative_prompt or None,
        reference_images=references or None,
        last_frame=last_frame,
        label=shot.label or shot_id,
        progress_cb=progress_cb,
        cancel_check=cancel_check,
    )

    with open(clip_path, "wb") as f:
        f.write(data)

    update_shot(
        job_id, shot_id,
        status=ShotStatus.READY.value,
        error="",
        rendered_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=render.duration_seconds * 1000,
        size_bytes=len(data),
        cost_usd=estimate_cost_usd(
            render.duration_seconds, render.resolution, render.tier, render.generate_audio
        ),
    )


# ---------------------------------------------------------------------------
# Create / list / read / save / delete
# ---------------------------------------------------------------------------
def _starting_prompt(description: str) -> str:
    """A panel's description, as the first draft of that shot's motion prompt.

    Not a finished prompt — a description says what the picture IS, and Veo
    wants to hear what MOVES. But a starting draft the writer edits beats an
    empty box: the shot is already described, and re-typing it is the boring
    part. The placeholder in the UI explains the difference.
    """
    return (description or "").strip()[:600]


def _board_descriptions(owner: str | None, storyboard_id: str) -> dict[int, str]:
    """{panel index: description} for one board, or {} if it can't be read.

    Owner-checked like every other cross-job read here, and cached by the caller
    so filling 26 shots from one board is ONE fetch, not 26.
    """
    if not storyboard_id or not _ID_RE.match(storyboard_id):
        return {}
    board = get_store().get(storyboard_id)
    if board is None or board.owner != owner or board.kind != JobKind.STORYBOARD:
        return {}
    variants, active = variants_of(board.result or {})
    panels = variants[active].get("panels") or []
    return {i: str((p or {}).get("description") or "") for i, p in enumerate(panels)}


def _shots_from_animatic(animatic: Job) -> list[FinalVideoShot]:
    """Every frame of an animatic, in order, as an unrendered shot.

    Panel sources are carried across BY REFERENCE, so the board stays the source
    of truth. Upload sources are copied into this project, because the animatic's
    media folder is its own and could be deleted out from under us.

    A frame that points at a storyboard panel also brings that panel's
    DESCRIPTION across as its starting prompt — the animatic dropped the text
    when it became pictures and timing, but the board still has it, so there is
    no reason to hand the user 26 empty prompt boxes.
    """
    from .animatics import _frames_of as animatic_frames

    descriptions: dict[str, dict[int, str]] = {}  # board id → {index: text}
    shots: list[FinalVideoShot] = []
    for i, frame in enumerate(animatic_frames(animatic)):
        prompt = ""
        if frame.src.kind == "panel" and frame.src.storyboard_id and frame.src.index is not None:
            board_id = frame.src.storyboard_id
            if board_id not in descriptions:
                descriptions[board_id] = _board_descriptions(animatic.owner, board_id)
            prompt = _starting_prompt(descriptions[board_id].get(int(frame.src.index), ""))
        shots.append(
            FinalVideoShot(
                id=uuid.uuid4().hex[:12],
                src=frame.src,
                label=frame.label or f"Shot {i + 1}",
                prompt=prompt,
            )
        )
    return shots


def _shots_from_board(board: Job) -> list[FinalVideoShot]:
    """Every DRAWN panel of a board, in order, as an unrendered shot.

    Failed and undrawn panels are skipped: a shot with no picture can't be
    rendered, and offering it would only produce a paid failure.
    """
    variants, active = variants_of(board.result or {})
    panels = variants[active].get("panels") or []
    shots: list[FinalVideoShot] = []
    for i, panel in enumerate(panels):
        panel = panel or {}
        if not panel.get("url") or panel.get("failed"):
            continue
        shots.append(
            FinalVideoShot(
                id=uuid.uuid4().hex[:12],
                src=AnimaticFrameSource(kind="panel", storyboard_id=board.job_id, index=i),
                label=f"Shot {i + 1}",
                # The panel's own description is the best first draft of a
                # motion prompt — see _starting_prompt.
                prompt=_starting_prompt(panel.get("description") or ""),
            )
        )
    return shots


def _copy_animatic_uploads(animatic: Job, video_job_id: str, shots: list[FinalVideoShot]) -> None:
    """Copy an animatic's uploaded stills into this project's media folder.

    Panels stay referenced; only uploads are copied, and the shot's source is
    rewritten to point at the copy so deleting the animatic can't blind a shot.
    """
    from .animatics import _image_path as animatic_image_path

    dest_dir = _media_dir(video_job_id)
    os.makedirs(dest_dir, exist_ok=True)
    for shot in shots:
        if shot.src.kind != "upload" or not shot.src.upload_id:
            continue
        source = animatic_image_path(animatic.job_id, shot.src.upload_id)
        if not source or not os.path.isfile(source):
            continue
        new_id = uuid.uuid4().hex[:12]
        dest = _image_path(video_job_id, new_id)
        try:
            shutil.copyfile(source, dest)
            shot.src = AnimaticFrameSource(kind="upload", upload_id=new_id)
        except OSError:
            logger.exception("[final %s] could not copy %s", video_job_id, source)


@router.post("", response_model=FinalVideoProject, status_code=201)
def create_final_video(
    body: FinalVideoCreateRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Start a project — empty, or pre-filled from an animatic or a storyboard.

    Passing `source_animatic_id` with no shots is the animatic's "Make final
    video": every frame becomes a shot, in order, ready for a motion prompt.
    """
    settings = body.settings or FinalVideoSettings()
    shots = list(body.shots)
    title = (body.title or "").strip()
    animatic_id = body.source_animatic_id
    board_id = body.source_storyboard_id
    source_animatic: Job | None = None

    if animatic_id:
        source_animatic = get_owned_job(animatic_id, current)
        if source_animatic.kind != JobKind.ANIMATIC:
            raise HTTPException(status_code=400, detail="That isn't an animatic.")
        if not shots:
            shots = _shots_from_animatic(source_animatic)
            if not shots:
                raise HTTPException(
                    status_code=409,
                    detail="That animatic has no frames yet — add some first.",
                )
        if not title:
            title = f"{source_animatic.character_name or 'Animatic'} — final video"
        if body.settings is None:
            aspect = (source_animatic.params or {}).get("settings", {}).get("aspect_ratio")
            # Veo renders 16:9 and 9:16 only, so a square animatic still has to
            # pick a side rather than silently producing a letterboxed render.
            if aspect in ("16:9", "9:16"):
                settings.aspect_ratio = aspect

    elif board_id:
        board = get_owned_job(board_id, current)
        if board.kind != JobKind.STORYBOARD:
            raise HTTPException(status_code=400, detail="That isn't a storyboard.")
        if not shots:
            shots = _shots_from_board(board)
            if not shots:
                raise HTTPException(
                    status_code=409,
                    detail="That storyboard has no drawn panels yet — generate some first.",
                )
        if not title:
            title = f"{board.character_name or 'Storyboard'} — final video"

    if len(shots) > config.MAX_VIDEO_SHOTS:
        raise HTTPException(
            status_code=413,
            detail=f"A final video can hold at most {config.MAX_VIDEO_SHOTS} shots.",
        )

    job = get_store().create(
        character_name=title or "Untitled final video",
        kind=JobKind.FINAL_VIDEO,
        owner=current.email,
        params={
            "settings": settings.model_dump(),
            "shots": [],  # filled below, after any uploads are copied
            "art": [],
            "source_animatic_id": animatic_id,
            "source_storyboard_id": board_id,
        },
    )
    # NO folders are created here. Pressing "New Final Video" and changing your
    # mind must not leave a directory behind, so every writer makes its own dir
    # when it actually has something to put in it (upload_art, render_one_shot,
    # _copy_animatic_uploads, assemble_final_video all makedirs(exist_ok=True)).
    # The client discards an untouched project on the way out; see the
    # workspace's handleBack.
    if source_animatic is not None:
        _copy_animatic_uploads(source_animatic, job.job_id, shots)

    params = dict(job.params or {})
    params["shots"] = [s.model_dump(exclude={"url", "image_url"}) for s in shots]
    updated = get_store().update(job.job_id, params=params) or job

    logger.info(
        "[final %s] created by %s (%d shot(s)%s)",
        job.job_id, current.email, len(shots),
        f", from animatic {animatic_id}" if animatic_id else
        f", from board {board_id}" if board_id else "",
    )
    return _project_of(updated)


@router.get("", response_model=list[FinalVideoSummary])
def list_final_videos(
    limit: int = 100,
    current: CurrentUser = Depends(get_current_user),
):
    """The caller's saved final-video projects, newest first (library grid)."""
    jobs = get_store().list(limit=limit, owner=current.email, kinds=[JobKind.FINAL_VIDEO])
    return [_summarise(j) for j in jobs]


@router.get("/backend", response_model=VideoBackendStatus)
def get_backend_status(current: CurrentUser = Depends(get_current_user)):
    """Is Veo actually reachable? Checked BEFORE the first paid call.

    Declared above /{job_id} so the literal path wins the route match.
    """
    from video_client import verify_access

    return VideoBackendStatus(**verify_access())


@router.get("/{job_id}", response_model=FinalVideoProject)
def get_final_video(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """The full project: shots, art tray, render state and settings."""
    return _project_of(_get_owned_video(job_id, current))


@router.put("/{job_id}", response_model=FinalVideoProject)
def save_final_video(
    job_id: str,
    body: FinalVideoSaveRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Save the edited project (the workflow's autosave).

    Refused while a render batch or an assembly is running: both read these
    exact shots, and letting them change underneath would spend money on a shot
    the user just deleted.

    Render STATE (status, cost, timings) is server-owned. A client that echoes a
    shot back keeps whatever the server last recorded, so an autosave racing a
    finished render can't roll it back to "pending" and lose a paid-for clip.
    """
    job = _get_owned_video(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is busy — wait for it to finish, or stop it first.",
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

    if body.art is not None:
        for ref in body.art:
            if not _CLIENT_ID_RE.match(ref.id):
                raise HTTPException(status_code=400, detail=f"Bad art id '{ref.id}'.")
        params["art"] = [a.model_dump(exclude={"url"}) for a in body.art]

    if body.shots is not None:
        if len(body.shots) > config.MAX_VIDEO_SHOTS:
            raise HTTPException(
                status_code=413,
                detail=f"A final video can hold at most {config.MAX_VIDEO_SHOTS} shots.",
            )
        existing = {s.id: s for s in _shots_of(job)}
        merged: list[dict] = []
        for shot in body.shots:
            if not _CLIENT_ID_RE.match(shot.id):
                raise HTTPException(status_code=400, detail=f"Bad shot id '{shot.id}'.")
            row = shot.model_dump(exclude={"url", "image_url"})
            was = existing.get(shot.id)
            if was is not None:
                # Server-owned render state always wins — see the docstring.
                row.update({
                    "status": was.status.value,
                    "error": was.error,
                    "rendered_at": was.rendered_at,
                    "duration_ms": was.duration_ms,
                    "size_bytes": was.size_bytes,
                    "cost_usd": was.cost_usd,
                })
            else:
                row.update({
                    "status": ShotStatus.PENDING.value, "error": "", "rendered_at": "",
                    "duration_ms": 0, "size_bytes": 0, "cost_usd": 0.0,
                })
            merged.append(row)
        params["shots"] = merged

    fields["params"] = params

    # The assembled cut no longer matches what's on screen. Say so rather than
    # deleting it — the old cut is worth having until the new one exists.
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
def delete_final_video(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Delete a project: its record, its uploads, its clips and its cut.

    Board panels and character assets are NOT touched — this only referenced them.
    """
    job = _get_owned_video(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This project is still busy — wait for it to finish first.",
        )
    folder = _project_dir(job_id)
    if os.path.isdir(folder):
        try:
            shutil.rmtree(folder)
        except OSError:
            logger.exception("[final %s] could not remove %s", job_id, folder)
    get_store().delete(job_id)
    return None


# ---------------------------------------------------------------------------
# Step 1 — final art & characters
# ---------------------------------------------------------------------------
@router.post("/{job_id}/art", response_model=list[FinalArtRef])
async def upload_art(
    job_id: str,
    files: list[UploadFile] = File(..., description="Final art / character stills."),
    current: CurrentUser = Depends(get_current_user),
):
    """Upload stills into the art tray.

    Stored but NOT attached to any shot — step 1 puts art in the tray, and each
    shot then picks which references it wants. The client saves the project
    afterwards with the returned refs appended to `art`.
    """
    from PIL import Image as PILImage

    job = _get_owned_video(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is busy.")

    os.makedirs(_media_dir(job_id), exist_ok=True)
    refs: list[FinalArtRef] = []

    for upload in files:
        name = upload.filename or "art"
        contents = await upload.read()
        if len(contents) > config.MAX_UPLOAD_BYTES:
            logger.info("[final %s] rejected %s (too large)", job_id, name)
            continue
        upload_id = uuid.uuid4().hex[:12]
        path = _image_path(job_id, upload_id)
        try:
            # Normalise whatever came in to a clean RGB PNG, as every other
            # image upload in the app does.
            with PILImage.open(io.BytesIO(contents)) as im:
                im.convert("RGB").save(path, "PNG")
        except Exception:  # noqa: BLE001 — bad/corrupt upload
            logger.info("[final %s] rejected %s (unreadable)", job_id, name)
            continue
        ref_id = uuid.uuid4().hex[:12]
        refs.append(
            FinalArtRef(
                id=ref_id,
                kind="upload",
                name=os.path.splitext(name)[0][:60],
                upload_id=upload_id,
                # By UPLOAD id, not ref id: the tray shows a thumbnail straight
                # away, and /art/{ref_id} can't answer until the project is
                # saved with this ref on it.
                url=f"/final-videos/{job_id}/media/{upload_id}",
            )
        )

    logger.info("[final %s] %d art still(s) uploaded", job_id, len(refs))
    return refs


@router.get("/{job_id}/art/{ref_id}")
def get_art_image(job_id: str, ref_id: str, current: CurrentUser = Depends(get_current_user)):
    """Serve one art reference — upload, panel or character asset, same URL."""
    job = _get_owned_video(job_id, current)
    ref = next((a for a in _art_of(job) if a.id == ref_id), None)
    if ref is None:
        raise HTTPException(status_code=404, detail="Art reference not found.")
    path = _resolve_art(job, ref)
    if not path:
        raise HTTPException(status_code=404, detail="This reference's image is missing.")
    return FileResponse(path, media_type="image/png")


@router.get("/{job_id}/media/{upload_id}")
def get_upload(job_id: str, upload_id: str, current: CurrentUser = Depends(get_current_user)):
    """Serve a just-uploaded still by its upload id.

    Exists for the same reason the animatic editor's /media route does: the
    save is debounced, so for a moment the file is on disk but not yet ON the
    project, and the art tray still has to show a thumbnail.
    """
    _get_owned_video(job_id, current)
    path = _image_path(job_id, upload_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Upload not found.")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Step 2 — render shots
# ---------------------------------------------------------------------------
def _estimate(shots: list[FinalVideoShot], settings: FinalVideoSettings) -> CostEstimate:
    from video_client import estimate_cost_usd

    total = 0.0
    seconds = 0
    for shot in shots:
        render = _effective_render(shot, settings)
        seconds += render.duration_seconds
        total += estimate_cost_usd(
            render.duration_seconds, render.resolution, render.tier, render.generate_audio
        )
    return CostEstimate(
        shots=len(shots),
        seconds=seconds,
        usd=round(total, 2),
        tier=settings.render.tier,
        resolution=settings.render.resolution,
    )


def _shots_to_render(job: Job, req: RenderShotsRequest) -> list[FinalVideoShot]:
    """Which shots a render request actually covers, with the reasons applied."""
    shots = _shots_of(job)
    by_id = {s.id: s for s in shots}

    if req.shot_ids:
        # An explicit pick is honoured even for an excluded shot: the user
        # pointed at that one shot, so they mean that one shot.
        chosen = [by_id[sid] for sid in req.shot_ids if sid in by_id]
    else:
        # "Render remaining": everything in the film that has no clip yet.
        chosen = [s for s in shots if s.include and s.status != ShotStatus.READY]

    if not req.force:
        chosen = [s for s in chosen if s.status != ShotStatus.READY]
    # A shot with no motion prompt can only produce a paid failure.
    return [s for s in chosen if (s.prompt or "").strip()]


@router.post("/{job_id}/estimate", response_model=CostEstimate)
def estimate_render(
    job_id: str,
    req: RenderShotsRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """What would this render request cost? Advisory — see CostEstimate.

    The client calls this to fill the confirm dialog, so the price is on screen
    BEFORE the button that spends it.
    """
    job = _get_owned_video(job_id, current)
    return _estimate(_shots_to_render(job, req), _settings_of(job))


@router.post("/{job_id}/render", response_model=JobCreatedResponse, status_code=202)
def render_shots(
    job_id: str,
    req: RenderShotsRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Render shots with Veo, off-request. Poll GET /jobs/{id} for progress.

    THIS SPENDS MONEY — one billed clip per shot. The batch is capped so a
    mis-click can't empty an account, already-rendered shots are skipped unless
    `force` says otherwise, and a prompt-less shot is never submitted.
    """
    job = _get_owned_video(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is already busy.")

    chosen = _shots_to_render(job, req)
    if not chosen:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nothing to render. Shots need a motion prompt, and shots that "
                "already have a clip are only re-rendered on purpose."
            ),
        )
    if len(chosen) > config.MAX_VIDEO_BATCH:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That would render {len(chosen)} shots at once; the limit is "
                f"{config.MAX_VIDEO_BATCH}. Render them in smaller passes — this "
                f"is a spend guard, not a technical one."
            ),
        )

    for shot in chosen:
        update_shot(job_id, shot.id, status=ShotStatus.QUEUED.value, error="")

    worker.submit_shot_renders(job_id, [s.id for s in chosen])
    estimate = _estimate(chosen, _settings_of(job))
    logger.info(
        "[final %s] %d shot(s) queued for render by %s (est. $%.2f)",
        job_id, len(chosen), current.email, estimate.usd,
    )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.FINAL_VIDEO,
        character_name=job.character_name,
        message=f"Rendering {len(chosen)} shot(s) — estimated ${estimate.usd:.2f}.",
    )


@router.get("/{job_id}/shot/{shot_id}/image")
def get_shot_image(
    job_id: str, shot_id: str, current: CurrentUser = Depends(get_current_user)
):
    """Serve one shot's source still — panel or upload, same URL either way."""
    job = _get_owned_video(job_id, current)
    shot = next((s for s in _shots_of(job) if s.id == shot_id), None)
    if shot is None:
        raise HTTPException(status_code=404, detail="Shot not found.")
    path = _resolve_src(job, shot.src)
    if not path:
        raise HTTPException(status_code=404, detail="This shot's picture is missing.")
    return FileResponse(path, media_type="image/png")


@router.get("/{job_id}/shot/{shot_id}/clip")
def get_shot_clip(
    job_id: str, shot_id: str, current: CurrentUser = Depends(get_current_user)
):
    """Serve one shot's rendered MP4 (the per-shot preview player)."""
    _get_owned_video(job_id, current)
    path = _clip_path(job_id, shot_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="This shot hasn't been rendered yet.")
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# Step 3 — assemble the sequence
# ---------------------------------------------------------------------------
@router.post("/{job_id}/assemble", response_model=JobCreatedResponse, status_code=202)
def assemble(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Join the rendered clips into the final cut. Poll GET /jobs/{id}.

    Clip paths are resolved HERE, not in the worker — this is the request that
    knows who is asking. Costs nothing: assembly is ffmpeg, so re-cutting is
    free and can be done as often as the edit changes.
    """
    job = _get_owned_video(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This project is already busy.")

    settings = _settings_of(job)
    clips: list[str] = []
    durations: list[int] = []
    for shot in _shots_of(job):
        # Not rendered, or deliberately left out of the film. An excluded shot
        # keeps its clip on disk — it is simply not in this cut.
        if shot.status != ShotStatus.READY or not shot.include:
            continue
        path = _clip_path(job_id, shot.id)
        if path and os.path.isfile(path):
            clips.append(path)
            # We asked Veo for this length, so we know it — the assembler must
            # not have to guess (there is no ffprobe on a bundled-ffmpeg
            # install, and a crossfade with guessed offsets renders black).
            durations.append(int(shot.duration_ms or 0))

    if not clips:
        raise HTTPException(
            status_code=409,
            detail="No rendered clips yet — render at least one shot on step 2 first.",
        )

    worker.submit_final_assemble(job_id, {
        "clips": clips,
        "durations_ms": durations,
        "aspect_ratio": settings.aspect_ratio,
        "fps": settings.fps,
        "transition": settings.transition,
        "transition_ms": settings.transition_ms,
        "include_clip_audio": settings.include_clip_audio,
        "output_dir": config.OUTPUT_DIR,
    })
    logger.info("[final %s] assembling %d clip(s) for %s", job_id, len(clips), current.email)
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.FINAL_VIDEO,
        character_name=job.character_name,
        message=f"Assembling {len(clips)} clip(s) into the final cut.",
    )


@router.post("/{job_id}/stop", status_code=202)
def stop(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Stop whatever this project is doing — a render batch or an assembly.

    A stopped render keeps every clip already paid for; a stopped assembly keeps
    the previous cut. Neither throws work away.
    """
    from cancel import request_cancel

    job = _get_owned_video(job_id, current)
    if job.status != JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Nothing is running on this project.")
    request_cancel(job_id)
    logger.info("[final %s] stop requested by %s", job_id, current.email)
    return {"stopping": True}


@router.get("/{job_id}/video")
def get_final_cut(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Serve the assembled final video."""
    _get_owned_video(job_id, current)
    path = _final_path(job_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="This project hasn't been assembled yet.")
    return FileResponse(path, media_type="video/mp4", filename="final.mp4")
