"""
main.py — FastAPI backend for the character asset generation pipeline (Phase 2).

Job-based, asynchronous API:

    POST /characters          Upload a reference image + options → returns job_id
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
    MeshyRequest,
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
    parts_order = cfg.get("parts_order", [])
    out = []
    for name, tpl in templates.items():
        out.append(
            TemplateInfo(
                name=name,
                character_defaults=tpl.get("character_defaults", {}),
                slot_renames=tpl.get("slot_renames", {}),
                parts=parts_order,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
@app.post("/characters", response_model=JobCreatedResponse, status_code=202)
async def create_character(
    name: str = Form(..., description="Character name (used for output folder names)."),
    image: UploadFile = File(..., description="Reference photo (person on white background)."),
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
    """Upload a reference image and enqueue a full pipeline run.

    Returns a job_id immediately; poll GET /jobs/{id} for progress.
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
            detail=f"Image too large ({len(contents)} bytes). "
            f"Max is {config.MAX_UPLOAD_BYTES} bytes.",
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

    # Persist the uploaded reference image under this job's id.
    upload_dir = os.path.join(config.UPLOAD_DIR, job.job_id)
    os.makedirs(upload_dir, exist_ok=True)
    filename = os.path.basename(image.filename or "reference")
    image_path = os.path.join(upload_dir, filename)
    with open(image_path, "wb") as f:
        f.write(contents)
    logger.info("[job %s] saved reference image: %s", job.job_id, image_path)

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
    if job.status != JobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"Job is '{job.status.value}', assets not ready.",
        )

    result = job.result or {}
    parts = result.get("urls", {})
    if not parts:
        raise HTTPException(status_code=404, detail="No assets found on this job.")

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

    meshy_job = store.create(
        character_name=parent.character_name,
        kind=JobKind.MESHY,
        template=parent.template,
        params={"parent_job_id": job_id, "parts": list(part_urls), "skipped": missing},
        owner=current.email,
    )
    worker.submit_meshy_job(meshy_job.job_id, part_urls, req.api_key)

    msg = "Meshy submission started."
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
