"""
main.py — FastAPI backend for the character asset generation pipeline (Phase 2).

Job-based, asynchronous API:

    POST /characters/reference  Generate a T-pose reference image from text → preview
    GET  /characters/reference/{id}/image  Serve the generated reference image
    POST /characters          Upload a reference image (or use reference_id) + options → returns job_id
    GET  /jobs                List recent jobs
    GET  /jobs/{id}           Poll a job's status + result
    GET  /jobs/{id}/download  Download the assets zip (local file or GCS redirect)
    POST /jobs/{id}/meshy     Submit generated parts for 3D (Meshy)
    GET  /templates           List available character templates
    GET  /health              Liveness check

Run locally:
    uvicorn server.main:app --reload
"""

import logging
import os
import shutil
import uuid
import zipfile

import yaml
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from . import config
from . import users
from .auth import CurrentUser, get_current_user, router as auth_router
from .jobs import get_store
from .schemas import (
    AssetItem,
    AssetsResponse,
    Job,
    JobCreatedResponse,
    JobKind,
    JobStatus,
    AssetReferenceRequest,
    MeshyRequest,
    ReferenceRequest,
    ReferenceResponse,
    RegeneratePartRequest,
    RegenerateViewRequest,
    PanelRegenerateRequest,
    RestyleRequest,
    ActiveVariantRequest,
    ScriptBreakdownRequest,
    ScriptBreakdownResponse,
    StoryboardCreateRequest,
    StoryboardProject,
    StoryboardRenameRequest,
    StoryboardSummary,
    PublicStoryboard,
    ShareResponse,
    TemplateInfo,
)
from . import worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("api")

app = FastAPI(
    title="Character Asset Generation API",
    description="Generate character turnaround assets (and optional 3D models) from a reference image.",
    version="1.0.0",
)

# Permissive CORS for the Phase 2/3 frontend. Lock down origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount authentication routes (/auth/register, /auth/login, /auth/me).
app.include_router(auth_router)

# View order Meshy expects for multi-image-to-3d.
_MESHY_VIEW_ORDER = ["front", "left", "three_quarter", "back"]

# Valid image backends (kept local so importing the API doesn't pull in the
# heavy google.genai import chain — the worker imports the pipeline lazily).
_SUPPORTED_PROVIDERS = ("vertex", "gemini")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _split_csv(value: str | None) -> list[str] | None:
    """Parse a comma-separated form field into a clean list (or None)."""
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def _load_config() -> dict:
    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_owned_job(job_id: str, current: CurrentUser) -> Job:
    """Fetch a job, returning 404 if it doesn't exist OR isn't owned by the caller.

    Using 404 (not 403) for the not-owned case avoids leaking which job ids exist.
    """
    job = get_store().get(job_id)
    if job is None or job.owner != current.email:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


def _safe_filename(name: str, fallback: str = "item") -> str:
    """Make `name` safe to use as a file/folder name inside a download.

    Punctuation becomes a space rather than an underscore, and runs collapse, so
    "Postmarked: After Death!" reads as "Postmarked After Death" instead of the
    ragged "Postmarked_ After Death_".
    """
    cleaned = "".join(c if c.isalnum() or c in "-_ " else " " for c in (name or ""))
    return " ".join(cleaned.split()).strip(" -_") or fallback


def _mark_ref_source(ref_dir: str, source: str) -> None:
    """Record whether a reference image was 'generated' or 'uploaded'.

    Kept as provenance next to reference.png. The assets ZIP used to read this
    to skip uploaded refs; it now ships everything, so nothing reads it today.
    """
    try:
        with open(os.path.join(ref_dir, "source.txt"), "w", encoding="utf-8") as f:
            f.write(source)
    except OSError:
        logger.debug("[reference] could not write source marker in %s", ref_dir, exc_info=True)


def _variants_of(result: dict) -> tuple[list[dict], int]:
    """Return (variants, active_index) for a storyboard result.

    Older results (pre-restyle) have no `variants` list — synthesise a single
    variant 0 from the flat panels/style so every code path can treat boards
    uniformly.
    """
    result = result or {}
    variants = result.get("variants")
    if not variants:
        variants = [
            {
                "style": result.get("style"),
                "panels": result.get("panels") or [],
                "ok_count": result.get("ok_count", 0),
            }
        ]
    active = int(result.get("active_variant", 0) or 0)
    if active < 0 or active >= len(variants):
        active = 0
    return variants, active


# ---------------------------------------------------------------------------
# Health & metadata
# ---------------------------------------------------------------------------
@app.get("/health")
def health(check_db: bool = True):
    """Liveness + dependency status.

    Set ?check_db=false to skip the MongoDB ping (which can block briefly if
    Mongo is unreachable).
    """
    body = {
        "status": "ok",
        "job_store": config.JOB_STORE,
        "auth": "jwt",
        "insecure_dev_jwt_secret": config.JWT_SECRET_IS_DEV,
        "default_image_provider": os.environ.get("IMAGE_PROVIDER", "vertex"),
    }
    if check_db:
        mongo = users.check_connection()
        body["mongodb"] = mongo
        if not mongo["connected"]:
            # Still 200 (the API is up) but flag degraded so auth issues are visible.
            body["status"] = "degraded"
    return body


@app.get("/templates", response_model=list[TemplateInfo])
def list_templates(current: CurrentUser = Depends(get_current_user)):
    """List character templates defined in prompts.yaml."""
    cfg = _load_config()
    templates = cfg.get("templates", {})
    global_parts = cfg.get("parts_order", [])
    out = []
    for name, tpl in templates.items():
        out.append(
            TemplateInfo(
                name=name,
                label=tpl.get("label"),
                character_defaults=tpl.get("character_defaults", {}),
                slot_renames=tpl.get("slot_renames", {}),
                # This template's own part order, or the global fallback.
                parts=tpl.get("parts_order") or global_parts,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Step 0 — Generate character reference image from text
# ---------------------------------------------------------------------------
@app.post("/characters/reference", response_model=ReferenceResponse)
def generate_reference(
    body: ReferenceRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Generate a T-pose character reference image from a text description.

    This is the optional "Step 0" — use it when the user doesn't have a
    reference photo. The returned reference_id can be passed to
    POST /characters instead of uploading an image file.
    """
    if body.provider is not None and body.provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{body.provider}'. Use one of {list(_SUPPORTED_PROVIDERS)}.",
        )

    # Import lazily to avoid pulling in the heavy google.genai chain at startup.
    from gemini_client import generate_character_reference, ReferenceGenerationError

    try:
        image = generate_character_reference(
            description=body.prompt,
            provider=body.provider,
        )
    except ReferenceGenerationError as e:
        # Surface the ACTUAL reason (API error / block / bad image), not a guess.
        logger.warning("[reference] generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Reference generation failed: {e}")
    except Exception as e:  # noqa: BLE001 — unexpected; still report clearly
        logger.exception("[reference] unexpected error")
        raise HTTPException(status_code=502, detail=f"Reference generation error: {e}")

    if image is None:
        raise HTTPException(
            status_code=502,
            detail="Reference generation returned no image. Try rephrasing your description.",
        )

    # Save the generated reference image under a unique reference id.
    reference_id = uuid.uuid4().hex[:12]
    ref_dir = os.path.join(config.UPLOAD_DIR, "_references", reference_id)
    os.makedirs(ref_dir, exist_ok=True)
    image_path = os.path.join(ref_dir, "reference.png")
    image.save(image_path, "PNG")
    _mark_ref_source(ref_dir, "generated")
    logger.info("[reference %s] saved reference image: %s", reference_id, image_path)

    return ReferenceResponse(
        reference_id=reference_id,
        image_url=f"/characters/reference/{reference_id}/image",
    )


@app.post("/assets/reference", response_model=ReferenceResponse)
def generate_asset_reference_image(
    body: AssetReferenceRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Generate a prop / background reference image (Stage B2 consistency).

    Saved under the SAME _references/{id}/reference.png layout as character
    references, so the returned reference_id plugs straight into POST /storyboards'
    asset_refs and can be previewed via GET /characters/reference/{id}/image.
    """
    if body.provider is not None and body.provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{body.provider}'. Use one of {list(_SUPPORTED_PROVIDERS)}.",
        )

    from gemini_client import generate_asset_reference, ReferenceGenerationError

    try:
        image = generate_asset_reference(
            description=body.prompt,
            category=body.category,
            provider=body.provider,
        )
    except ReferenceGenerationError as e:
        logger.warning("[asset-ref] generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Asset reference generation failed: {e}")
    except Exception as e:  # noqa: BLE001 — unexpected; still report clearly
        logger.exception("[asset-ref] unexpected error")
        raise HTTPException(status_code=502, detail=f"Asset reference generation error: {e}")

    if image is None:
        raise HTTPException(
            status_code=502,
            detail="Asset reference generation returned no image. Try rephrasing your description.",
        )

    reference_id = uuid.uuid4().hex[:12]
    ref_dir = os.path.join(config.UPLOAD_DIR, "_references", reference_id)
    os.makedirs(ref_dir, exist_ok=True)
    image_path = os.path.join(ref_dir, "reference.png")
    image.save(image_path, "PNG")
    _mark_ref_source(ref_dir, "generated")
    logger.info("[asset-ref %s] saved %s reference image", reference_id, body.category)

    return ReferenceResponse(
        reference_id=reference_id,
        image_url=f"/characters/reference/{reference_id}/image",
    )


@app.post("/characters/reference/upload", response_model=ReferenceResponse)
async def upload_reference(
    image: UploadFile = File(..., description="Character image to use as a reference."),
    current: CurrentUser = Depends(get_current_user),
):
    """Upload your OWN character image as a reference (Stage B alternative).

    Saved under the same _references/{id}/reference.png layout as generated
    references, so it plugs straight into POST /storyboards' character_refs.
    """
    if image.content_type not in config.ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type '{image.content_type}'. "
            f"Allowed: {sorted(config.ALLOWED_IMAGE_TYPES)}",
        )
    contents = await image.read()
    if len(contents) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large ({len(contents)} bytes). Max is {config.MAX_UPLOAD_BYTES}.",
        )

    reference_id = uuid.uuid4().hex[:12]
    ref_dir = os.path.join(config.UPLOAD_DIR, "_references", reference_id)
    os.makedirs(ref_dir, exist_ok=True)
    image_path = os.path.join(ref_dir, "reference.png")

    # Normalise whatever format was uploaded to a clean RGB PNG.
    import io
    from PIL import Image as PILImage

    try:
        PILImage.open(io.BytesIO(contents)).convert("RGB").save(image_path, "PNG")
    except Exception as e:  # noqa: BLE001 — bad/corrupt upload
        raise HTTPException(status_code=400, detail=f"Couldn't read that image: {e}")

    _mark_ref_source(ref_dir, "uploaded")
    logger.info("[reference %s] saved uploaded reference image", reference_id)
    return ReferenceResponse(
        reference_id=reference_id,
        image_url=f"/characters/reference/{reference_id}/image",
        message="Reference image uploaded successfully.",
    )


@app.get("/characters/reference/{reference_id}/image")
def get_reference_image(
    reference_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve a previously generated reference image for preview."""
    image_path = os.path.join(
        config.UPLOAD_DIR, "_references", reference_id, "reference.png"
    )
    if not os.path.isfile(image_path):
        raise HTTPException(status_code=404, detail="Reference image not found.")
    return FileResponse(image_path, media_type="image/png")


# ---------------------------------------------------------------------------
# Script → Storyboard — Stage A: break a script into a shot list
# ---------------------------------------------------------------------------
@app.post("/storyboards/breakdown", response_model=ScriptBreakdownResponse)
def breakdown_script(
    body: ScriptBreakdownRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Turn a raw script into an ordered storyboard shot list (Stage A).

    Synchronous: a single text-model call, usually a few seconds. The chosen
    style / aspect_ratio are passed through so the client can carry them into the
    next step (panel generation).
    """
    if body.provider is not None and body.provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{body.provider}'. Use one of {list(_SUPPORTED_PROVIDERS)}.",
        )

    # Import lazily so the heavy google.genai chain isn't pulled in at startup.
    from script_breakdown import break_down_script, ScriptBreakdownError

    try:
        result = break_down_script(body.script, provider=body.provider, genre=body.genre)
    except ScriptBreakdownError as e:
        logger.warning("[breakdown] failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Script breakdown failed: {e}")
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[breakdown] unexpected error")
        raise HTTPException(status_code=502, detail=f"Script breakdown error: {e}")

    shots = result["shots"]
    return ScriptBreakdownResponse(
        shots=shots,
        characters=result.get("characters", []),
        assets=result.get("assets", []),
        count=len(shots),
        style=body.style,
        aspect_ratio=body.aspect_ratio,
    )


# ---------------------------------------------------------------------------
# Script → Storyboard — Stage D: generate panels from a reviewed shot list
# ---------------------------------------------------------------------------
@app.post("/storyboards", response_model=JobCreatedResponse, status_code=202)
def create_storyboard(
    body: StoryboardCreateRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Generate one storyboard panel per reviewed shot (async).

    Returns a job_id immediately; poll GET /jobs/{id} for live progress. Panels
    stream into job.result as each finishes; fetch each via
    GET /storyboards/{job_id}/panel/{index}.
    """
    if body.provider is not None and body.provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{body.provider}'. Use one of {list(_SUPPORTED_PROVIDERS)}.",
        )

    # Resolve reference_ids → image paths (Stage B / B2 consistency). Both
    # character and asset refs share the same _references/{id}/reference.png
    # layout. Silently skip any ref whose image file is missing.
    def _resolve_refs(ref_map: dict | None) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for name, ref_id in (ref_map or {}).items():
            if not ref_id:
                continue
            ref_path = os.path.join(
                config.UPLOAD_DIR, "_references", str(ref_id).strip(), "reference.png"
            )
            if os.path.isfile(ref_path):
                resolved[name] = ref_path
        return resolved

    character_ref_paths = _resolve_refs(body.character_refs)
    asset_ref_paths = _resolve_refs(body.asset_refs)

    title = (body.title or "Storyboard").strip() or "Storyboard"
    shot_dicts = [s.model_dump() for s in body.shots]
    job = get_store().create(
        character_name=title,
        kind=JobKind.STORYBOARD,
        params={
            "style": body.style,
            "aspect_ratio": body.aspect_ratio,
            # Not used for drawing — labels the card in the storyboard library.
            "genre": body.genre,
            "count": len(body.shots),
            "provider": body.provider,
            "character_count": len(character_ref_paths),
            "asset_count": len(asset_ref_paths),
            # Kept so a single panel can be regenerated later with the same refs.
            "character_ref_paths": character_ref_paths,
            "asset_ref_paths": asset_ref_paths,
            # Only used to sort the assets ZIP into props/ and backgrounds/.
            "asset_categories": body.asset_categories,
            # Kept so a panel can always be re-drawn (even if it's missing from the
            # streamed result) and so edited prompts have a source of truth.
            "shots": shot_dicts,
        },
        owner=current.email,
    )

    kwargs = {
        "shots": shot_dicts,
        "style": body.style,
        "aspect_ratio": body.aspect_ratio,
        "output_dir": config.OUTPUT_DIR,
        "provider": body.provider,
        "character_ref_paths": character_ref_paths,
        "asset_ref_paths": asset_ref_paths,
    }
    worker.submit_storyboard_job(job.job_id, kwargs)

    return JobCreatedResponse(
        job_id=job.job_id,
        status=job.status,
        kind=job.kind,
        character_name=title,
        message="Storyboard generation started. Poll GET /jobs/{job_id} for progress.",
    )


# ---------------------------------------------------------------------------
# Storyboard library ("Your Storyboards") — save / reopen / manage past boards
#
# A saved "project" IS a storyboard job: they are already persisted per-owner
# with their shots, style and panels, so the library is a view over them rather
# than a second store that could drift out of sync.
# ---------------------------------------------------------------------------
def _board_dir(job_id: str) -> str:
    return os.path.join(config.OUTPUT_DIR, "_storyboards", job_id)


def _drawn_panels(job: Job) -> list[tuple[int, str]]:
    """(index, serve-url) for panels that actually have an image, in board order.

    Reads the ACTIVE style variant, so a restyled board's cover and shared view
    show the style the owner last picked — not whatever variant 0 happens to be.
    """
    variants, active = _variants_of(job.result or {})
    panels = variants[active].get("panels") or []
    return [
        (i, (p or {}).get("url"))
        for i, p in enumerate(panels)
        if (p or {}).get("url") and not (p or {}).get("failed")
    ]


def _panel_indexes(job: Job) -> list[int]:
    return [i for i, _ in _drawn_panels(job)]


def _summarise_board(job: Job) -> StoryboardSummary:
    params = job.params or {}
    drawn = _drawn_panels(job)
    token = params.get("share_token")
    return StoryboardSummary(
        job_id=job.job_id,
        title=job.character_name or "Storyboard",
        status=job.status,
        style=params.get("style"),
        aspect_ratio=params.get("aspect_ratio"),
        genre=params.get("genre"),
        panel_count=int(params.get("count") or 0),
        cover_index=drawn[0][0] if drawn else None,
        cover_url=drawn[0][1] if drawn else None,
        shared=bool(token),
        share_token=token,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _get_owned_board(job_id: str, current: CurrentUser) -> Job:
    """Like _get_owned_job, but rejects jobs that aren't storyboards."""
    job = _get_owned_job(job_id, current)
    if job.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=400, detail="Not a storyboard job.")
    return job


@app.get("/storyboards", response_model=list[StoryboardSummary])
def list_storyboards(
    limit: int = 100,
    current: CurrentUser = Depends(get_current_user),
):
    """List the caller's saved storyboards, newest first (the library grid)."""
    jobs = get_store().list(limit=limit, owner=current.email)
    return [_summarise_board(j) for j in jobs if j.kind == JobKind.STORYBOARD]


@app.get("/storyboards/{job_id}/project", response_model=StoryboardProject)
def get_storyboard_project(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Return a saved board's reusable inputs (shots + settings) for Duplicate.

    Re-opening a board this way skips the paid script-breakdown call.
    """
    job = _get_owned_board(job_id, current)
    params = job.params or {}
    return StoryboardProject(
        job_id=job.job_id,
        title=job.character_name or "Storyboard",
        style=params.get("style"),
        aspect_ratio=params.get("aspect_ratio"),
        genre=params.get("genre"),
        shots=params.get("shots") or [],
    )


@app.patch("/storyboards/{job_id}", response_model=StoryboardSummary)
def rename_storyboard(
    job_id: str,
    body: StoryboardRenameRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Rename a saved storyboard (the title shown on its library card)."""
    _get_owned_board(job_id, current)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty.")
    job = get_store().update(job_id, character_name=title)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return _summarise_board(job)


@app.delete("/storyboards/{job_id}", status_code=204)
def delete_storyboard(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Delete a saved storyboard: its record AND its generated panel files."""
    job = _get_owned_board(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This storyboard is still generating — wait for it to finish first.",
        )
    # Remove the panels from disk. A failure here must not strand the record,
    # so it's logged and the delete continues.
    board_dir = _board_dir(job_id)
    if os.path.isdir(board_dir):
        try:
            shutil.rmtree(board_dir)
        except OSError:
            logger.exception("[storyboard %s] could not remove %s", job_id, board_dir)
    get_store().delete(job_id)
    return None


@app.post("/storyboards/{job_id}/share", response_model=ShareResponse)
def share_storyboard(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Create (or return) an unguessable public link token for this board.

    Anyone holding the token can view the panels WITHOUT logging in, so it is
    treated as the secret: 32 hex chars from `uuid4`, and it can be revoked.
    Only the drawn panels are exposed — never the shots, refs or owner.
    """
    job = _get_owned_board(job_id, current)
    params = dict(job.params or {})
    token = params.get("share_token")
    if not token:
        token = uuid.uuid4().hex
        params["share_token"] = token
        get_store().update(job_id, params=params)
    return ShareResponse(shared=True, share_token=token)


@app.delete("/storyboards/{job_id}/share", response_model=ShareResponse)
def unshare_storyboard(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Revoke the public link — the old token stops working immediately."""
    job = _get_owned_board(job_id, current)
    params = dict(job.params or {})
    params.pop("share_token", None)
    get_store().update(job_id, params=params)
    return ShareResponse(shared=False, share_token=None)


# ---- Public (NO AUTH) — only reachable with a valid share token ------------
def _get_shared_board(token: str) -> Job:
    job = get_store().find_by_share_token(token)
    if job is None or job.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=404, detail="This shared storyboard is no longer available.")
    return job


@app.get("/public/storyboards/{token}", response_model=PublicStoryboard)
def get_public_storyboard(token: str):
    """A shared board's viewer metadata. No auth — the token IS the credential."""
    job = _get_shared_board(token)
    params = job.params or {}
    drawn = _panel_indexes(job)
    return PublicStoryboard(
        title=job.character_name or "Storyboard",
        style=params.get("style"),
        aspect_ratio=params.get("aspect_ratio"),
        genre=params.get("genre"),
        panel_count=len(drawn),
        panel_indexes=drawn,
        created_at=job.created_at,
    )


@app.get("/public/storyboards/{token}/panel/{index}")
def get_public_storyboard_panel(token: str, index: int):
    """Serve one panel of a shared board (no auth, token-gated).

    Only panels the board actually reports as drawn are served, so the token
    can't be used to probe for other files.
    """
    job = _get_shared_board(token)
    if index not in _panel_indexes(job):
        raise HTTPException(status_code=404, detail=f"Panel {index} not found.")
    variants, active = _variants_of(job.result or {})
    subdir = "" if not active else f"v{active}"
    path = os.path.join(_board_dir(job.job_id), subdir, f"panel_{index:02d}.png")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Panel {index} not found.")
    return FileResponse(path, media_type="image/png")


@app.get("/storyboards/{job_id}/panel/{index}")
def get_storyboard_panel(
    job_id: str,
    index: int,
    v: int = 0,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve one generated storyboard panel PNG (owner-scoped).

    `v` selects the style variant (0 = board root; N = the vN/ subfolder).
    """
    _get_owned_job(job_id, current)  # 404 if missing or not owned
    subdir = "" if not v else f"v{v}"
    path = os.path.join(config.OUTPUT_DIR, "_storyboards", job_id, subdir, f"panel_{index:02d}.png")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Panel {index} not found.")
    return FileResponse(path, media_type="image/png")


@app.post("/storyboards/{job_id}/regenerate-panel")
def regenerate_storyboard_panel(
    job_id: str,
    body: PanelRegenerateRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Re-draw ONE panel (Retry / edit-and-regenerate). Synchronous single call.

    Robust to a panel that isn't in the streamed result yet: it's located by its
    `index` field and, failing that, rebuilt from the shots stored on the job.
    Optional description/camera/location overrides let the user edit the prompt.
    """
    job = _get_owned_job(job_id, current)
    if job.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=400, detail="Not a storyboard job.")

    result = job.result or {}
    # Regenerate within the ACTIVE style variant so its subfolder + style are used.
    variants, active = _variants_of(result)
    panels = list(variants[active].get("panels") or [])
    variant_style = variants[active].get("style") or job.params.get("style", "custom")
    shots = job.params.get("shots") or []
    count = int(job.params.get("count") or len(shots) or len(panels))

    # Find the panel by its index field; fall back to list position, then to the
    # original shot list (so a not-yet-streamed panel can still be re-drawn).
    panel = next((p for p in panels if p.get("index") == body.index), None)
    if panel is None and 0 <= body.index < len(panels):
        panel = panels[body.index]
    if panel is None:
        if 0 <= body.index < len(shots):
            s = shots[body.index]
            panel = {
                "index": body.index,
                "scene_number": s.get("scene_number", 1),
                "shot_number": s.get("shot_number", body.index + 1),
                "description": s.get("description", ""),
                "characters": s.get("characters", []) or [],
                "assets": s.get("assets", []) or [],
                "location": s.get("location", "") or "",
                "camera": s.get("camera", "") or "",
                "url": None,
                "failed": True,
            }
        else:
            raise HTTPException(status_code=404, detail=f"Panel {body.index} not found.")

    # Apply any edited prompt fields before re-drawing.
    panel = dict(panel)
    if body.description is not None:
        panel["description"] = body.description
    if body.camera is not None:
        panel["camera"] = body.camera
    if body.location is not None:
        panel["location"] = body.location

    from storyboard_pipeline import regenerate_panel

    try:
        updated = regenerate_panel(
            job_id=job_id,
            panel=panel,
            style=variant_style,
            aspect_ratio=job.params.get("aspect_ratio", "16:9"),
            output_dir=config.OUTPUT_DIR,
            character_ref_paths=job.params.get("character_ref_paths") or {},
            asset_ref_paths=job.params.get("asset_ref_paths") or {},
            variant=active,
            provider=job.params.get("provider"),
        )
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[storyboard %s] panel %d regen failed", job_id, body.index)
        raise HTTPException(status_code=502, detail=f"Panel regeneration failed: {e}")

    # Write the panel back in place (or insert it, keeping index order).
    replaced = False
    for i, p in enumerate(panels):
        if p.get("index") == body.index:
            panels[i] = updated
            replaced = True
            break
    if not replaced:
        panels.append(updated)
        panels.sort(key=lambda p: p.get("index", 0))

    ok = sum(1 for p in panels if not p.get("failed"))
    variants[active]["panels"] = panels
    variants[active]["ok_count"] = ok
    result["variants"] = variants
    result["active_variant"] = active
    result["panels"] = panels  # mirror the active variant
    result["ok_count"] = ok
    result.setdefault("count", count)
    result.setdefault("style", variant_style)
    result.setdefault("aspect_ratio", job.params.get("aspect_ratio"))
    get_store().update(job_id, result=result)
    return {"panel": updated}


@app.post("/storyboards/{job_id}/restyle", response_model=JobCreatedResponse, status_code=202)
def restyle_storyboard(
    job_id: str,
    body: RestyleRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Re-draw the whole board in a NEW visual style, kept as a new style variant.

    Async (poll GET /jobs/{id}); the new variant streams in and becomes active,
    while every existing variant is preserved so the user can switch back. Each
    panel reuses the locked character/prop/background refs and its previous render
    as a composition reference, so only the art style changes.
    """
    job = _get_owned_job(job_id, current)
    if job.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=400, detail="Not a storyboard job.")
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This storyboard is still generating.")

    shots = job.params.get("shots") or []
    if not shots:
        raise HTTPException(status_code=409, detail="No shots stored for this storyboard.")

    result = job.result or {}
    variants, active = _variants_of(result)
    new_index = len(variants)

    # Composition reference = the currently active variant's panel folder.
    board_dir = os.path.join(config.OUTPUT_DIR, "_storyboards", job_id)
    composition_ref_dir = board_dir if not active else os.path.join(board_dir, f"v{active}")

    # Persist the (possibly synthesised) variants baseline before the run.
    result["variants"] = variants
    result["active_variant"] = active
    get_store().update(job_id, result=result)

    kwargs = {
        "shots": shots,
        "style": body.style,
        "aspect_ratio": job.params.get("aspect_ratio", "16:9"),
        "output_dir": config.OUTPUT_DIR,
        "provider": job.params.get("provider"),
        "character_ref_paths": job.params.get("character_ref_paths") or {},
        "asset_ref_paths": job.params.get("asset_ref_paths") or {},
        "variant": new_index,
        "composition_ref_dir": composition_ref_dir,
        "existing_variants": variants,
    }
    worker.submit_restyle_job(job_id, kwargs)

    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.STORYBOARD,
        character_name=job.character_name,
        message=f"Re-styling the board to '{body.style}'. Poll GET /jobs/{job_id}.",
    )


@app.post("/storyboards/{job_id}/active-variant")
def set_active_variant(
    job_id: str,
    body: ActiveVariantRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Switch which style variant is shown/exported (no regeneration)."""
    job = _get_owned_job(job_id, current)
    if job.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=400, detail="Not a storyboard job.")

    result = job.result or {}
    variants, _ = _variants_of(result)
    if body.index < 0 or body.index >= len(variants):
        raise HTTPException(status_code=404, detail=f"Variant {body.index} not found.")

    v = variants[body.index]
    result["variants"] = variants
    result["active_variant"] = body.index
    result["panels"] = v.get("panels") or []
    result["style"] = v.get("style")
    result["ok_count"] = v.get("ok_count", sum(1 for p in (v.get("panels") or []) if not p.get("failed")))
    get_store().update(job_id, result=result)
    return {"active_variant": body.index, "style": v.get("style")}


@app.get("/storyboards/{job_id}/pdf")
def download_storyboard_pdf(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Export the storyboard board as a printable PDF (Stage F, owner-scoped)."""
    job = _get_owned_job(job_id, current)
    if job.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=400, detail="Not a storyboard job.")

    variants, active = _variants_of(job.result or {})
    panels = variants[active].get("panels") or []
    if not panels:
        raise HTTPException(status_code=409, detail="No panels generated yet.")

    from storyboard_pdf import build_storyboard_pdf

    try:
        pdf_path = build_storyboard_pdf(
            job_id=job_id,
            output_dir=config.OUTPUT_DIR,
            title=job.character_name,
            panels=panels,
            subdir="" if not active else f"v{active}",
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[storyboard %s] PDF export failed", job_id)
        raise HTTPException(status_code=500, detail=f"PDF export failed: {e}")

    safe = _safe_filename(job.character_name, "storyboard")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{safe}.pdf")


@app.get("/storyboards/{job_id}/bundle")
def download_storyboard_bundle(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Download the complete storyboard package as a ZIP.

    Layout (every file prefixed with the board's title, numbered in board order
    so the sequence survives being unzipped into a flat folder):

        panels/<Title>_shot_01.png ...   every drawn panel, full resolution
        characters/<Title>_character_01_<Name>.png
        props/<Title>_prop_01_<Name>.png
        backgrounds/<Title>_background_01_<Name>.png
        <Title>.pdf                      the board with camera/location/cast

    Panels come from the ACTIVE style variant — the one shown on the board.
    Both generated AND uploaded references are included: this is meant to be a
    complete hand-off package, not just the re-usable bits.
    """
    job = _get_owned_board(job_id, current)

    _safe = _safe_filename
    title = _safe(job.character_name, "storyboard")
    char_refs = job.params.get("character_ref_paths") or {}
    asset_refs = job.params.get("asset_ref_paths") or {}
    categories = job.params.get("asset_categories") or {}
    variants, active = _variants_of(job.result or {})
    panels = variants[active].get("panels") or []
    subdir = "" if not active else f"v{active}"
    src_dir = os.path.join(_board_dir(job_id), subdir) if subdir else _board_dir(job_id)

    board_dir = _board_dir(job_id)
    os.makedirs(board_dir, exist_ok=True)
    zip_path = os.path.join(board_dir, "bundle.zip")

    added = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # --- Panels, numbered in board order ---------------------------------
        seq = 0
        for p in panels:
            if p.get("failed") or not p.get("url"):
                continue
            path = os.path.join(src_dir, f"panel_{p['index']:02d}.png")
            if not os.path.isfile(path):
                continue
            seq += 1
            zf.write(path, f"panels/{title}_shot_{seq:02d}.png")
            added += 1

        # --- References, split by kind and numbered ---------------------------
        for i, (name, path) in enumerate(char_refs.items(), start=1):
            if os.path.isfile(path):
                zf.write(path, f"characters/{title}_character_{i:02d}_{_safe(name)}.png")
                added += 1

        prop_n = bg_n = 0
        for name, path in asset_refs.items():
            if not os.path.isfile(path):
                continue
            if str(categories.get(name, "prop")).lower() == "background":
                bg_n += 1
                dest = f"backgrounds/{title}_background_{bg_n:02d}_{_safe(name)}.png"
            else:
                prop_n += 1
                dest = f"props/{title}_prop_{prop_n:02d}_{_safe(name)}.png"
            zf.write(path, dest)
            added += 1

        # --- The board itself -------------------------------------------------
        if panels:
            try:
                from storyboard_pdf import build_storyboard_pdf

                pdf_path = build_storyboard_pdf(
                    job_id=job_id,
                    output_dir=config.OUTPUT_DIR,
                    title=job.character_name,
                    panels=panels,
                    subdir=subdir,
                )
                zf.write(pdf_path, f"{title}.pdf")
                added += 1
            except Exception:  # noqa: BLE001 — PDF is best-effort inside the bundle
                logger.exception("[storyboard %s] bundle PDF build failed", job_id)

    if added == 0:
        raise HTTPException(
            status_code=409,
            detail="Nothing to bundle yet — generate references or panels first.",
        )

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{_safe(job.character_name or 'storyboard')}_assets.zip",
    )


# ---------------------------------------------------------------------------
# Generate (create character pipeline job)
# ---------------------------------------------------------------------------
@app.post("/characters", response_model=JobCreatedResponse, status_code=202)
async def create_character(
    name: str = Form(..., description="Character name (used for output folder names)."),
    image: UploadFile | None = File(None, description="Reference photo (person on white background). Provide this OR reference_id."),
    reference_id: str | None = Form(None, description="ID from POST /characters/reference. Provide this OR image."),
    template: str | None = Form(None, description="Template name, e.g. 'saree'."),
    skip: str | None = Form(None, description="Comma-separated parts to skip."),
    parts: str | None = Form(None, description="Run ONLY these parts (comma-separated)."),
    meshy: str | None = Form(None, description="Comma-separated parts to also submit to Meshy."),
    provider: str | None = Form(None, description="Image backend: 'vertex' or 'gemini'. Defaults to server IMAGE_PROVIDER."),
    local_only: bool = Form(False, description="Skip GCS upload (local output only)."),
    age: str | None = Form(None),
    gender: str | None = Form(None),
    skin_tone: str | None = Form(None),
    current: CurrentUser = Depends(get_current_user),
):
    """Upload a reference image (or use a generated reference_id) and enqueue a full pipeline run.

    Provide exactly one of `image` (file upload) or `reference_id` (from Step 0).
    Returns a job_id immediately; poll GET /jobs/{id} for progress.
    """
    # --- Resolve the reference image path ---
    has_image = image is not None and image.filename
    has_ref = reference_id is not None and reference_id.strip()

    if has_image and has_ref:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'image' (file upload) or 'reference_id', not both.",
        )
    if not has_image and not has_ref:
        raise HTTPException(
            status_code=400,
            detail="Provide either an 'image' file upload or a 'reference_id' from Step 0.",
        )

    if has_image:
        # Existing path: user uploaded a file.
        if image.content_type not in config.ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported image type '{image.content_type}'. "
                f"Allowed: {sorted(config.ALLOWED_IMAGE_TYPES)}",
            )

        contents = await image.read()
        if len(contents) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Image too large ({len(contents)} bytes). "
                f"Max is {config.MAX_UPLOAD_BYTES} bytes.",
            )

    if has_ref:
        # Step 0 path: user generated a reference image via text prompt.
        ref_path = os.path.join(
            config.UPLOAD_DIR, "_references", reference_id.strip(), "reference.png"
        )
        if not os.path.isfile(ref_path):
            raise HTTPException(
                status_code=404,
                detail=f"Reference '{reference_id}' not found. Generate one via POST /characters/reference first.",
            )

    if provider is not None and provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{provider}'. Use one of {list(_SUPPORTED_PROVIDERS)}.",
        )

    # Meshy needs public GCS URLs; refuse the impossible combination up front.
    meshy_parts = _split_csv(meshy)
    if meshy_parts and local_only:
        raise HTTPException(
            status_code=400,
            detail="Meshy requires public image URLs; cannot combine 'meshy' with 'local_only'.",
        )

    store = get_store()

    # Character-var overrides (age/gender/skin_tone). Only pass if any given.
    character_vars = {
        k: v
        for k, v in {"age": age, "gender": gender, "skin_tone": skin_tone}.items()
        if v is not None
    } or None

    params = {
        "skip_parts": _split_csv(skip),
        "only_parts": _split_csv(parts),
        "meshy_parts": meshy_parts,
        "local_only": local_only,
        "character_vars": character_vars,
        "provider": provider,
    }

    job = store.create(
        character_name=name,
        kind=JobKind.GENERATE,
        template=template,
        params=params,
        owner=current.email,
    )

    # Persist / link the reference image for this job.
    upload_dir = os.path.join(config.UPLOAD_DIR, job.job_id)
    os.makedirs(upload_dir, exist_ok=True)

    if has_image:
        filename = os.path.basename(image.filename or "reference")
        image_path = os.path.join(upload_dir, filename)
        with open(image_path, "wb") as f:
            f.write(contents)
        logger.info("[job %s] saved uploaded reference image: %s", job.job_id, image_path)
    else:
        # Copy the generated reference into the job's upload dir.
        import shutil
        image_path = os.path.join(upload_dir, "reference.png")
        shutil.copy2(ref_path, image_path)
        logger.info("[job %s] linked generated reference (ref=%s): %s", job.job_id, reference_id, image_path)

    pipeline_kwargs = {
        "character_name": name,
        "reference_image_path": image_path,
        "template_name": template,
        "config_path": config.CONFIG_PATH,
        "output_dir": config.OUTPUT_DIR,
        **params,  # includes provider, skip_parts, only_parts, meshy_parts, local_only, character_vars
    }
    worker.submit_generate_job(job.job_id, pipeline_kwargs)

    return JobCreatedResponse(
        job_id=job.job_id,
        status=job.status,
        kind=job.kind,
        character_name=name,
        message="Generation started. Poll GET /jobs/{job_id} for status.",
    )


# ---------------------------------------------------------------------------
# Job status / listing
# ---------------------------------------------------------------------------
@app.get("/jobs", response_model=list[Job])
def list_jobs(limit: int = 50, current: CurrentUser = Depends(get_current_user)):
    return get_store().list(limit=limit, owner=current.email)


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, current: CurrentUser = Depends(get_current_user)):
    return _get_owned_job(job_id, current)


@app.get("/jobs/{job_id}/download")
def download_assets(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Download the character's asset zip.

    Redirects to the GCS URL if the run uploaded there; otherwise streams the
    local zip file.
    """
    job = _get_owned_job(job_id, current)
    if job.status != JobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"Job is '{job.status.value}', not ready for download.",
        )

    zip_ref = (job.result or {}).get("zip")
    if not zip_ref:
        raise HTTPException(status_code=404, detail="No zip found on this job.")

    if str(zip_ref).startswith("http"):
        return RedirectResponse(url=zip_ref)

    if not os.path.exists(zip_ref):
        raise HTTPException(status_code=404, detail=f"Zip file missing: {zip_ref}")
    return FileResponse(
        zip_ref,
        media_type="application/zip",
        filename=os.path.basename(zip_ref),
    )


@app.get("/jobs/{job_id}/assets", response_model=AssetsResponse)
def list_assets(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """List individual generated asset URLs (per part / per view) for a job.

    Returns both a nested `parts` map ({part: {view: url}}) and a flat `assets`
    list, plus the zip. URLs are public GCS links unless the run was local_only,
    in which case they are absolute local file paths (see `is_local`).
    """
    job = _get_owned_job(job_id, current)
    # SUCCEEDED → full asset set. RUNNING → whatever parts have finished so far
    # (live preview). Anything else has nothing to show yet.
    if job.status not in (JobStatus.SUCCEEDED, JobStatus.RUNNING):
        raise HTTPException(
            status_code=409,
            detail=f"Job is '{job.status.value}', assets not ready.",
        )

    result = job.result or {}
    parts = result.get("urls", {})
    if not parts:
        # While running, "nothing yet" is expected — tell the client to keep polling.
        raise HTTPException(
            status_code=409 if job.status == JobStatus.RUNNING else 404,
            detail="No assets ready yet." if job.status == JobStatus.RUNNING
            else "No assets found on this job.",
        )

    # A run is "local" if any URL is a filesystem path rather than an http URL.
    is_local = not any(
        str(url).startswith("http")
        for views in parts.values()
        for url in views.values()
    )

    flat = [
        AssetItem(part=part, view=view, url=url)
        for part, views in parts.items()
        for view, url in views.items()
    ]

    return AssetsResponse(
        job_id=job.job_id,
        character_name=job.character_name,
        is_local=is_local,
        parts=parts,
        assets=flat,
        zip=result.get("zip"),
    )


def _job_reference_path(job_id: str) -> str | None:
    """Locate the uploaded/generated reference image stored for a job."""
    upload_dir = os.path.join(config.UPLOAD_DIR, job_id)
    if os.path.exists(upload_dir):
        for f in os.listdir(upload_dir):
            if f.endswith((".png", ".jpg", ".jpeg", ".webp")):
                return os.path.join(upload_dir, f)
    return None


@app.post("/jobs/{job_id}/regenerate-part", response_model=JobCreatedResponse, status_code=202)
def regenerate_part(
    job_id: str,
    req: RegeneratePartRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Regenerate a single part ASYNC — returns immediately; poll the job.

    Runs in the worker so a long image call survives a dropped connection /
    server restart. The parent job flips to RUNNING and back to SUCCEEDED.
    """
    job = _get_owned_job(job_id, current)
    if job.status != JobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"Job is '{job.status.value}', cannot regenerate parts until initial generation succeeds.",
        )

    ref_path = _job_reference_path(job_id)
    if not ref_path or not os.path.isfile(ref_path):
        raise HTTPException(status_code=404, detail="Reference image for this job was not found.")

    kwargs = dict(
        character_name=job.character_name,
        reference_image_path=ref_path,
        part_name=req.part,
        custom_prompt=req.prompt,
        template_name=job.template,
        local_only=job.params.get("local_only", False),
        output_dir=config.OUTPUT_DIR,
        provider=req.provider or job.params.get("provider"),
        existing_result=job.result or {},
    )
    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        progress={
            "percent": 15, "stage": "regenerating", "current_part": req.part,
            "message": f"Regenerating {req.part}…", "done_parts": [], "total_parts": 1,
        },
    )
    worker.submit_regenerate_job(job_id, "part", kwargs)
    return JobCreatedResponse(
        job_id=job_id, status=JobStatus.RUNNING, kind=job.kind,
        character_name=job.character_name, message=f"Regenerating {req.part}…",
    )


@app.post("/jobs/{job_id}/regenerate-view", response_model=JobCreatedResponse, status_code=202)
def regenerate_view(
    job_id: str,
    req: RegenerateViewRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Regenerate ONE view (front/left/three_quarter/back) of a part — ASYNC."""
    job = _get_owned_job(job_id, current)
    if job.status != JobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"Job is '{job.status.value}', cannot regenerate views yet.",
        )

    ref_path = _job_reference_path(job_id)
    if not ref_path or not os.path.isfile(ref_path):
        raise HTTPException(status_code=404, detail="Reference image for this job was not found.")

    kwargs = dict(
        character_name=job.character_name,
        reference_image_path=ref_path,
        part_name=req.part,
        view_name=req.view,
        custom_prompt=req.prompt,
        template_name=job.template,
        local_only=job.params.get("local_only", False),
        output_dir=config.OUTPUT_DIR,
        provider=req.provider or job.params.get("provider"),
        existing_result=job.result or {},
    )
    get_store().update(
        job_id,
        status=JobStatus.RUNNING,
        progress={
            "percent": 15, "stage": "regenerating", "current_part": req.part,
            "message": f"Regenerating {req.part} · {req.view}…", "done_parts": [], "total_parts": 1,
        },
    )
    worker.submit_regenerate_job(job_id, "view", kwargs)
    return JobCreatedResponse(
        job_id=job_id, status=JobStatus.RUNNING, kind=job.kind,
        character_name=job.character_name, message=f"Regenerating {req.part} {req.view}…",
    )


@app.get("/jobs/{job_id}/download/{part}")
def download_part(
    job_id: str,
    part: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Download a single part's 4 view PNGs as a zip (per-section download)."""
    import zipfile

    job = _get_owned_job(job_id, current)
    char_dir = os.path.join(config.OUTPUT_DIR, job.character_name)
    if not os.path.isdir(char_dir):
        raise HTTPException(status_code=404, detail="No assets on disk for this job.")

    files = [
        f for f in os.listdir(char_dir)
        if f.startswith(f"{part}_") and f.endswith(".png") and not f.startswith("_")
    ]
    if not files:
        raise HTTPException(status_code=404, detail=f"No images found for part '{part}'.")

    zip_path = os.path.join(char_dir, f"_{part}_download.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(files):
            zf.write(os.path.join(char_dir, f), f)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{job.character_name}_{part}.zip",
    )


@app.get("/jobs/{job_id}/image/{part}/{view}")
def get_asset_image(
    job_id: str,
    part: str,
    view: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve a generated asset PNG directly."""
    job = _get_owned_job(job_id, current)
    img_path = os.path.join(config.OUTPUT_DIR, job.character_name, f"{part}_{view}.png")
    if not os.path.isfile(img_path):
        raise HTTPException(status_code=404, detail=f"Image {part}_{view}.png not found.")
    return FileResponse(img_path, media_type="image/png")


# ---------------------------------------------------------------------------
# Meshy (standalone 3D for an already-generated character)
# ---------------------------------------------------------------------------
@app.post("/jobs/{job_id}/meshy", response_model=JobCreatedResponse, status_code=202)
def submit_meshy(
    job_id: str,
    req: MeshyRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Submit selected parts of a completed generation job to Meshy for 3D.

    The generation job must have uploaded assets to GCS (public URLs), since
    Meshy fetches the images by URL.
    """
    store = get_store()
    parent = _get_owned_job(job_id, current)
    if parent.kind != JobKind.GENERATE:
        raise HTTPException(status_code=400, detail="Meshy can only follow a generation job.")
    if parent.status != JobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"Generation job is '{parent.status.value}', not ready for Meshy.",
        )

    urls = (parent.result or {}).get("urls", {})

    part_urls: dict[str, list[str]] = {}
    missing: list[str] = []
    for part in req.parts:
        views = urls.get(part)
        if not views:
            missing.append(part)
            continue
        ordered = [views.get(v, "") for v in _MESHY_VIEW_ORDER]
        ordered = [u for u in ordered if u]
        if len(ordered) < 4 or not all(u.startswith("http") for u in ordered):
            missing.append(part)
            continue
        part_urls[part] = ordered

    if not part_urls:
        raise HTTPException(
            status_code=400,
            detail=(
                "None of the requested parts have 4 public URLs. "
                f"Unavailable: {missing}. (Was the job run with local_only?)"
            ),
        )

    # Resolve provider + key: explicit request key → user's saved key → env var.
    provider = (req.provider or "meshy").strip().lower()
    if provider not in ("meshy", "tripo"):
        raise HTTPException(status_code=400, detail=f"Unknown 3D provider '{provider}'.")
    api_key = req.api_key or users.get_api_key(current.email, provider)

    meshy_job = store.create(
        character_name=parent.character_name,
        kind=JobKind.MESHY,
        template=parent.template,
        params={
            "parent_job_id": job_id,
            "parts": list(part_urls),
            "skipped": missing,
            "provider": provider,
        },
        owner=current.email,
    )
    worker.submit_meshy_job(meshy_job.job_id, part_urls, api_key, provider)

    msg = f"{provider.title()} submission started."
    if missing:
        msg += f" Skipped (no public URLs): {missing}."
    return JobCreatedResponse(
        job_id=meshy_job.job_id,
        status=meshy_job.status,
        kind=meshy_job.kind,
        character_name=parent.character_name,
        message=msg,
    )


@app.on_event("startup")
def _on_startup():
    # Import triggers security.py's secret resolution, which sets this flag.
    from . import security  # noqa: F401

    if config.JWT_SECRET_IS_DEV:
        logger.warning(
            "Running with an INSECURE dev JWT secret. Set JWT_SECRET in .env "
            "before exposing this API."
        )
    logger.info("Auth: JWT (email+password) | User store: MongoDB (%s)", config.MONGODB_DB)


@app.on_event("shutdown")
def _on_shutdown():
    worker.shutdown()
