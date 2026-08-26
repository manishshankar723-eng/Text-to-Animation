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

import copy as copy_module
import logging
import os
import shutil
import uuid
import zipfile

import yaml
# Only the paths + the duration→pose arithmetic are needed at request time; the
# heavy generation runs in the worker, so importing this costs nothing.
import panel_sequence
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from . import config
from . import users
from .admin import router as admin_router
from .billing import router as billing_router
from .features import router as features_router, require_feature
# ⚠ THE QUOTA GUARD SITS BESIDE `require_feature`, ON THE SAME ROUTES. A limit
# checked AFTER the work is a limit that bills the customer for the call telling
# them they are over. See server/usage.py.
from .usage import require_quota
from . import usage as usage_counters
from .animatics import router as animatics_router
from .auth import CurrentUser, get_current_user, router as auth_router
from .drafts import router as drafts_router
from .director import router as director_router
from .plans import router as plans_router
from .sounds import router as sounds_router
from .videos import router as videos_router
# Shared with the animatics router — they live in common.py so the two route
# modules don't have to import each other. Aliased to the names used below.
from .common import (
    board_dir as _board_dir,
    get_owned_job as _get_owned_job,
    regenerate_board_panel as _regenerate_board_panel,
    sequence_summary as _sequence_summary,
    submit_sequence_run as _submit_sequence_run,
    variants_of as _variants_of,
)
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
    PanelInsertRequest,
    PanelRegenerateRequest,
    PanelSequenceInfo,
    PanelSequenceRequest,
    RestyleRequest,
    ActiveVariantRequest,
    ScriptBreakdownRequest,
    ScriptBreakdownResponse,
    StoryboardCreateRequest,
    StoryboardDraft,
    StoryboardDraftUpdate,
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
    # Downloads are fetched as authed blobs, so the browser's own filename
    # handling never runs — the client has to READ this header to name the file
    # after the board. Response headers aren't visible to JS across origins
    # unless they're exposed here, so without this every PDF saved as
    # "storyboard.pdf" (and browsers deduped that to "storyboard (7).pdf").
    expose_headers=["Content-Disposition"],
)


# ---------------------------------------------------------------------------
# Compression — JSON only, never media
# ---------------------------------------------------------------------------
# An animatic project is the biggest JSON this API sends: every frame serialises
# every field it has, defaults included (`effects`, `mask`, `blend`, `keyframes`
# per frame), and a sixty-panel board is hundreds of kilobytes of it. That one
# response is what the editor's loading spinner waits on, and it went out raw.
#
# ⚠ NOT A BARE `GZipMiddleware`. That would also compress every `FileResponse`
# this API serves — PNG panels and, worse, whole MP4s, which are already
# compressed and can be a hundred megabytes each. Gzipping those spends real CPU
# to save nothing and delays the first byte of a file the browser wants to start
# playing. So media paths are handed straight through, and everything else gets
# the standard middleware.
_MEDIA_PATH_MARKERS = ("/frame/", "/panel/", "/media/", "/video", "/download", "/image")


class GZipJSONOnlyMiddleware:
    """`GZipMiddleware` for everything except the media routes."""

    def __init__(self, app, minimum_size: int = 1024):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        if any(marker in path for marker in _MEDIA_PATH_MARKERS):
            return await self.app(scope, receive, send)
        return await self.gzip(scope, receive, send)


app.add_middleware(GZipJSONOnlyMiddleware, minimum_size=1024)

# Mount authentication routes (/auth/register, /auth/login, /auth/me).
app.include_router(auth_router)
# Storyboard → Animatic (/animatics/…). Its own module: it shares nothing with
# the image pipeline and spends no AI quota.
app.include_router(animatics_router)
# Autosaved script drafts (/scripts/draft) — what the user is typing, kept safe
# from a page refresh. Spends no AI quota.
app.include_router(drafts_router)
# Plan & Script (/plans/…) — the conversational content planner that sits BEFORE
# the storyboard workflow. Spends text quota only, never image quota.
app.include_router(plans_router)
# Animatics → Final Video (/final-videos/…) — the only workflow that calls Veo.
# Rendering is billed per second of output, so every spending path in there
# estimates first and caps the batch. See server/videos.py.
app.include_router(videos_router)
# 🎬 Make Video (/director/…) — the auto-editor's BRAIN: it reads a board and
# writes an edit plan. Spends TEXT quota only; the plan it returns is applied by
# the browser, through the same validator and fence the deterministic planner's
# plan goes through. See server/director.py and director.py.
app.include_router(director_router)
# The admin panel (/admin/…) — who registered, who signed in, and the levers on
# an account. ⚠ EVERY ROUTE IN IT IS BEHIND `require_admin`, which reads the role
# out of the database rather than out of the token, and answers 404 (not 403) to
# anyone else. Spends no AI quota. See server/admin.py.
app.include_router(admin_router)
# Feature flags. ⚠ ONE ROUTE — `GET /auth/me/entitlements`, what THIS account may
# see and use — and it is the only call the client makes to find out. The rules
# behind it are `server/features.py`; the `require_feature` guards below and on
# the other routers are the same resolver, applied. Spends no AI quota.
app.include_router(features_router)
# Billing tiers (/billing/…). ⚠ `GET /billing/tiers` IS PUBLIC — a price list is
# public by nature, and requiring a session to read one would force a landing
# page to keep a second copy of the prices. ⚠ AND IT IS "TIERS", NOT "PLANS":
# /plans is Plan & Script and has been since long before there was billing.
app.include_router(billing_router)
# The sound library (/sounds/…) — a search box over Freesound's CC0 / CC BY
# catalogue, so the editor's audio layer has something to put on it without the
# user going and finding a file first. ⚠ SEARCHING ONLY: filing a sound into a
# project is `POST /animatics/{id}/sounds`, because that writes into the
# project's media directory. Spends no AI quota; it does spend the deployment's
# SHARED Freesound rate limit. Read the licence note at the top of `freesound.py`
# before shipping this commercially — the free API key is a non-commercial one.
app.include_router(sounds_router)

# View order Meshy expects for multi-image-to-3d.
_MESHY_VIEW_ORDER = ["front", "left", "three_quarter", "back"]

# Valid image backends (kept local so importing the API doesn't pull in the
# heavy google.genai import chain — the worker imports the pipeline lazily).
_SUPPORTED_PROVIDERS = ("vertex", "gemini")
# The source script is stored with a storyboard for display only. Capped so a
# pasted novel can't push the job record past Firestore's 1 MB document limit.
MAX_STORED_SCRIPT_CHARS = 200_000


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


def _safe_filename(name: str, fallback: str = "item") -> str:
    """Make `name` safe to use as a file/folder name inside a download.

    Punctuation becomes a space rather than an underscore, and runs collapse, so
    "Postmarked: After Death!" reads as "Postmarked After Death" instead of the
    ragged "Postmarked_ After Death_". Apostrophes are DELETED rather than
    spaced, so "Kabir's Morning" stays "Kabirs Morning" and not "Kabir s".
    """
    name = (name or "").replace("'", "").replace("’", "")
    cleaned = "".join(c if c.isalnum() or c in "-_ " else " " for c in name)
    return " ".join(cleaned.split()).strip(" -_") or fallback


def _mark_ref_source(ref_dir: str, source: str) -> None:
    """Record whether a reference image was 'generated' or 'uploaded'.

    Written next to reference.png so the assets ZIP can skip UPLOADED refs — the
    user already has those images; only AI-generated ones are worth bundling.
    """
    try:
        with open(os.path.join(ref_dir, "source.txt"), "w", encoding="utf-8") as f:
            f.write(source)
    except OSError:
        logger.debug("[reference] could not write source marker in %s", ref_dir, exc_info=True)


def _ref_is_generated(ref_png_path: str) -> bool:
    """True unless the reference was explicitly marked 'uploaded'.

    A missing marker → treated as generated, so older refs (created before the
    marker existed) still get bundled.
    """
    marker = os.path.join(os.path.dirname(ref_png_path), "source.txt")
    try:
        with open(marker, encoding="utf-8") as f:
            return f.read().strip() != "uploaded"
    except OSError:
        return True


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
    # Animatic export needs ffmpeg. Reported rather than assumed, so a missing
    # binary shows up here instead of only when an export fails.
    try:
        from animatic import ffmpeg_available

        body["ffmpeg"] = ffmpeg_available()
    except Exception:  # noqa: BLE001 — health must never 500
        body["ffmpeg"] = False
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
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
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
            # Draw them as a person of the script's world, not the model's default.
            world=body.world.model_dump() if body.world else None,
            # Unseeded: calling this again with the same description IS how the
            # user asks for a different-looking character. Consistency later
            # comes from the SAVED reference image, not from redrawing it.
            variation=None,
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
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
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
            # A hut, a cooking pot and a temple all differ by culture.
            world=body.world.model_dump() if body.world else None,
            # Unseeded — same re-roll reasoning as /characters/reference above.
            variation=None,
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


# How many opening words of the script name an untitled draft. Mirrors the
# client's own fallback so a draft and the board it becomes read the same.
_TITLE_WORDS = 4


def _title_from_script(script: str) -> str:
    """Name a draft after the script's opening words (never just 'Storyboard')."""
    first = next((ln.strip() for ln in (script or "").splitlines() if ln.strip()), "")
    words = first.split()[:_TITLE_WORDS]
    return " ".join(words) or "Untitled storyboard"


# ---------------------------------------------------------------------------
# Script → Storyboard — Stage A: break a script into a shot list
# ---------------------------------------------------------------------------
@app.post("/storyboards/breakdown", response_model=ScriptBreakdownResponse)
def breakdown_script(
    body: ScriptBreakdownRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('workflow.script-to-storyboard')),
):
    # ⚠ CHECKED BEFORE THE MODEL CALL, which is the whole point — a breakdown
    # spends quota, so a script that is over the plan's length must be refused
    # before it is paid for, not after. `story_pages` is a per-request cap for
    # the same reason `shots_per_project` is: it describes THIS script.
    _pages = max(1, round(len(body.script or "") / usage_counters.PAGE_CHARS))
    _over = usage_counters.cap_exceeded(current.email, "story_pages", _pages)
    if _over is not None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Your plan covers scripts up to about {_over} pages, and this one "
                f"is about {_pages}. Shorten it, or upgrade your plan."
            ),
        )
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

    # Persist the breakdown IMMEDIATELY as a DRAFT job, before the user sees it.
    # This call costs AI quota and everything downstream of it (edited shots,
    # cast, world) is hand-work; until now all of it lived only in the browser
    # and a refresh destroyed it. The draft is the review step's backing store —
    # it becomes the real board when the user hits Generate.
    # A failure here must NOT lose the breakdown the user just paid for, so it
    # degrades to the old stateless behaviour with a loud log.
    draft_job_id = None
    try:
        draft = get_store().create(
            character_name=(body.title or "").strip() or _title_from_script(body.script),
            kind=JobKind.STORYBOARD,
            params={
                "style": body.style,
                "aspect_ratio": body.aspect_ratio,
                "genre": body.genre,
                "count": len(shots),
                "provider": body.provider,
                "shots": shots,
                "characters": result.get("characters", []),
                "assets": result.get("assets", []),
                "world": result.get("world") or {},
                "script": (body.script or "")[:MAX_STORED_SCRIPT_CHARS],
            },
            owner=current.email,
        )
        get_store().update(draft.job_id, status=JobStatus.DRAFT)
        draft_job_id = draft.job_id
        logger.info("[breakdown] saved draft %s (%d shots)", draft_job_id, len(shots))
    except Exception:  # noqa: BLE001 — never lose a paid breakdown over storage
        logger.exception("[breakdown] could not save draft; returning it unsaved")

    return ScriptBreakdownResponse(
        shots=shots,
        characters=result.get("characters", []),
        assets=result.get("assets", []),
        world=result.get("world") or {},
        count=len(shots),
        style=body.style,
        aspect_ratio=body.aspect_ratio,
        # Advisory only — which panels aren't backed by the script. Never blocks
        # the breakdown; the writer decides what to do about it.
        grounding=result.get("grounding") or {},
        # The review step saves back to this. None means the draft couldn't be
        # stored — the client just works statelessly, as it did before.
        draft_job_id=draft_job_id,
    )


# ---------------------------------------------------------------------------
# Script → Storyboard — Stage A2: the DRAFT being reviewed
# ---------------------------------------------------------------------------
# The review step's backing store. A breakdown costs AI quota and everything
# done to it afterwards is hand-work, so it lives in Mongo from the moment it
# exists rather than only in the browser.
def _draft_to_response(job: Job) -> StoryboardDraft:
    p = job.params or {}
    return StoryboardDraft(
        job_id=job.job_id,
        title=job.character_name or "",
        style=p.get("style"),
        aspect_ratio=p.get("aspect_ratio"),
        genre=p.get("genre"),
        script=p.get("script") or "",
        shots=p.get("shots") or [],
        characters=p.get("characters") or [],
        assets=p.get("assets") or [],
        world=p.get("world") or {},
        character_refs=p.get("character_refs") or {},
        asset_refs=p.get("asset_refs") or {},
        asset_categories=p.get("asset_categories") or {},
        updated_at=job.updated_at,
    )


def _get_owned_draft(job_id: str, current: CurrentUser) -> Job:
    """A draft the caller owns, or 404. Refuses jobs that aren't drafts."""
    job = _get_owned_board(job_id, current)
    if job.status != JobStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail="This storyboard has already been generated — it is no longer a draft.",
        )
    return job


@app.get("/storyboards/draft", response_model=StoryboardDraft)
def get_storyboard_draft(current: CurrentUser = Depends(get_current_user)):
    """The caller's most recent unfinished storyboard, for resuming after a refresh.

    Never 404s: no draft is a normal state, and the client shouldn't have to
    treat "nothing in progress" as an error. Returns `job_id: null` then.
    """
    jobs = get_store().list(limit=50, owner=current.email, kinds=[JobKind.STORYBOARD])
    drafts = [j for j in jobs if j.status == JobStatus.DRAFT]
    if not drafts:
        return StoryboardDraft()
    # `list` is newest-first, so the first draft is the one to resume.
    return _draft_to_response(drafts[0])


@app.patch("/storyboards/draft/{job_id}", response_model=StoryboardDraft)
def update_storyboard_draft(
    job_id: str,
    body: StoryboardDraftUpdate,
    current: CurrentUser = Depends(get_current_user),
):
    """Save review-step edits (shots, cast, assets, world, refs) onto the draft.

    Partial: only the fields present in the body are written, so saving an edited
    shot list can't wipe references chosen on another step.
    """
    job = _get_owned_draft(job_id, current)
    params = dict(job.params or {})

    incoming = body.model_dump(exclude_unset=True, mode="json")
    title = incoming.pop("title", None)
    for key, value in incoming.items():
        params[key] = value
    if "shots" in incoming:
        params["count"] = len(incoming["shots"] or [])

    fields: dict = {"params": params}
    if title is not None and title.strip():
        fields["character_name"] = title.strip()

    updated = get_store().update(job_id, **fields)
    return _draft_to_response(updated)


@app.delete("/storyboards/draft/{job_id}", status_code=204)
def delete_storyboard_draft(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Discard a draft (the user started over). Only ever deletes a DRAFT."""
    _get_owned_draft(job_id, current)
    get_store().delete(job_id)
    return None


# ---------------------------------------------------------------------------
# Script → Storyboard — Stage D: generate panels from a reviewed shot list
# ---------------------------------------------------------------------------
@app.post("/storyboards", response_model=JobCreatedResponse, status_code=202)
def create_storyboard(
    body: StoryboardCreateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('workflow.script-to-storyboard')),
    _quota: CurrentUser = Depends(require_quota("projects")),
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

    # ⚠ A PER-REQUEST CAP, NOT A COUNTER. "9 shots per project" is a property
    # of THIS board; accumulating it would turn it into "9 shots ever". See the
    # two kinds of limit at the top of server/usage.py. Checked here, before a
    # job is created or a draft promoted, so a refusal leaves nothing behind.
    _over = usage_counters.cap_exceeded(
        current.email, "shots_per_project", len(body.shots)
    )
    if _over is not None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Your plan allows {_over} shots per storyboard, and this one has "
                f"{len(body.shots)}. Remove some shots, or upgrade your plan."
            ),
        )

    title = (body.title or "Storyboard").strip() or "Storyboard"
    shot_dicts = [s.model_dump() for s in body.shots]
    # The written continuity bible — see StoryboardCreateRequest.characters.
    # Stored on the job, so a re-style, a single-panel redraw or a key-pose run
    # months later still knows what these people look like.
    cast_dicts = [c.model_dump() for c in body.characters]
    asset_dicts = [a.model_dump() for a in body.assets]
    # The script's world rides along with every panel prompt, and is stored so a
    # later re-style / single-panel redraw stays in the same culture and period.
    world = body.world.model_dump() if body.world else {}
    params = {
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
        "cast": cast_dicts,
        "assets": asset_dicts,
        "world": world,
        # Display only. Capped so a pasted novel can't bloat the job record
        # (Firestore documents have a hard size limit).
        "script": (body.script or "")[:MAX_STORED_SCRIPT_CHARS],
    }

    # PROMOTE the draft this board was reviewed as, rather than creating a
    # second record. The draft already holds this script and shot list; making a
    # new job would leave the draft orphaned and show the same work twice.
    # An unknown/foreign//already-generated draft id is ignored rather than
    # fatal — the user still gets their board.
    job = None
    if body.draft_job_id:
        existing = get_store().get(body.draft_job_id)
        if (
            existing is not None
            and existing.owner == current.email
            and existing.kind == JobKind.STORYBOARD
            and existing.status == JobStatus.DRAFT
        ):
            job = get_store().update(
                body.draft_job_id,
                character_name=title,
                params=params,
                status=JobStatus.QUEUED,
            )
            logger.info("[storyboard] promoted draft %s to a board", job.job_id)
        else:
            logger.warning(
                "[storyboard] draft_job_id %s is not a promotable draft — "
                "creating a new job instead.", body.draft_job_id,
            )

    if job is None:
        job = get_store().create(
            character_name=title,
            kind=JobKind.STORYBOARD,
            params=params,
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
        "world": world,
        "cast": cast_dicts,
        "assets": asset_dicts,
    }
    # ⚠ COUNTED HERE, AFTER THE JOB EXISTS AND THE WORK IS QUEUED — and ONCE,
    # whether the job was created above or promoted from a draft. Counting at
    # the create call would miss the promotion path and undercount every board
    # that went through the review step, which is most of them.
    usage_counters.increment(current.email, "projects")
    usage_counters.increment(current.email, "image_generations", len(shot_dicts))

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


# WHAT A BOARD CARD DOES NOT READ. Measured: a storyboard averages 7.8 KB and
# `result` is 54% of it, essentially all `panels` - of which `description` alone
# is ~29% of the panel array, with `location`, `characters`, `camera`,
# `dialogue`, `assets` and `versions` behind it. The card needs `url` and
# `failed`, and nothing else, because all it draws is the first drawn panel.
#
# ⚠ BOTH RESULT SHAPES ARE LISTED. A board written before restyling has flat
# `result.panels`; one written after has `result.variants[].panels` (17 of 69
# live boards today). `variants_of` synthesises the first shape from the second,
# so a drop list that named only one would silently keep shipping the other.
#
# ⚠ GUARDED, NOT TRUSTED — `tests/summary_projection_check.py` builds every real
# board's card from the full document and from the slimmed one and fails if they
# differ. See the same note over `SUMMARY_DROP` in animatics.py.
_PANEL_UNREAD = (
    "description", "location", "characters", "camera",
    "dialogue", "assets", "versions",
)
BOARD_SUMMARY_DROP = (
    # The script and the cast/asset references belong to the EDITOR, not to a
    # card that prints a title, a style and a panel count.
    "params.script",
    "params.cast",
    "params.assets",
    *(f"result.panels.{f}" for f in _PANEL_UNREAD),
    *(f"result.variants.panels.{f}" for f in _PANEL_UNREAD),
)


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
    workflow: str = "",
    current: CurrentUser = Depends(get_current_user),
):
    """List the caller's saved storyboards, newest first (the library grid).

    `workflow` decides WHOSE boards these are. Boards made in Script to
    Storyboard carry no tag; a copy made by Image to Animatic Image carries
    `params["workflow"]` (see `copy_storyboard`). Each library asks for its own,
    so a copy doesn't clutter the workflow it was copied out of, and the
    originals don't appear in the workflow that only works on copies.

    `workflow="*"` returns EVERY board whatever its tag. That is what the
    downstream workflows (animatics, video) ask for: a board refined in Image
    to Animatic Image is exactly the thing you then want to animate, and
    filtering it out would make the copies a dead end.

    ⚠ BOTH CONDITIONS GO INTO THE QUERY, NOT INTO A LIST COMPREHENSION AFTER IT,
    and that is a correctness fix rather than a speed one. `limit` is applied by
    the STORE. Sorting out drafts and other workflows' copies afterwards
    therefore filtered an ALREADY-TRUNCATED page: ask for the newest 8 boards of
    an account whose last 8 are all Image-to-Animatic copies, and Script to
    Storyboard was told it had none. It never bit while every caller asked for
    100, which is exactly the kind of bug that waits for someone to tune a
    number. See `where` in jobs.py.
    """
    where: dict = {"status": {"$ne": JobStatus.DRAFT.value}}
    if workflow != "*":
        # ⚠ THE UNTAGGED BOARDS ARE `$in [None, ""]`, NOT `== ""`. Script to
        # Storyboard's own boards were written before the tag existed and simply
        # have no `workflow` key; in Mongo a missing field reads as null, so
        # matching the empty string alone would hide every board made before the
        # copy feature shipped. `_matches` mirrors this for the other backends.
        where["params.workflow"] = (
            {"$in": [None, ""]} if not workflow else workflow
        )
    jobs = get_store().list(
        limit=limit,
        owner=current.email,
        kinds=[JobKind.STORYBOARD],
        where=where,
        # ⚠ THE LIST ROUTE ONLY. `GET /storyboards/{id}/project` and the public
        # share view still read the whole board. See BOARD_SUMMARY_DROP.
        drop=BOARD_SUMMARY_DROP,
    )
    # DRAFT jobs are storyboards-in-progress sitting on the review step — no
    # panels drawn, nothing to show on a card. They're resumed via
    # GET /storyboards/draft, not listed here as if they were finished boards.
    #
    # ⚠ THE SAME TWO CONDITIONS AGAIN, ON PURPOSE. `where` is what makes the
    # limit land on the right rows; this is what guarantees the RESPONSE is
    # right whatever a backend does with the filter. Against Mongo it removes
    # nothing — and the day it does, the bug is in the query, not on screen.
    return [
        _summarise_board(j)
        for j in jobs
        if j.status != JobStatus.DRAFT
        and (
            workflow == "*"
            or ((j.params or {}).get("workflow") or "") == workflow
        )
    ]


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
        world=params.get("world") or {},
        script=params.get("script") or "",
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


def _repoint_panel_url(url: str, job_id: str, index: int) -> str:
    """Rewrite a panel's serve url onto `job_id`, keeping its ?v=<variant>.

    Used when copying a board. The variant query matters: drop it and a copy of
    a re-styled board would serve variant 0 while claiming to be variant 2.
    """
    suffix = ""
    if "?" in url:
        suffix = "?" + url.split("?", 1)[1]
    return f"/storyboards/{job_id}/panel/{index}{suffix}"


@app.post("/storyboards/{job_id}/copy", response_model=StoryboardSummary, status_code=201)
def copy_storyboard(
    job_id: str,
    workflow: str = "",
    current: CurrentUser = Depends(get_current_user),
):
    """Deep-copy a board into a NEW, fully independent one.

    This is what "From a Storyboard" does in Image to Animatic Image, and the
    independence is the whole point: the copy gets its own job record AND its
    own panel FILES, so redrawing, restyling, inserting or deleting a panel in
    it can never reach back into the board it came from. A reference (the way
    animatics and final videos point at a board by id) would have done exactly
    that, which is why this copies bytes instead.

    `workflow` tags which library the copy belongs to, so each workflow lists
    only its own boards — see `list_storyboards`.

    NOT copied on purpose:
      - the share token: a copy is not published just because its source was.
      - RUNNING state: a half-drawn board would copy a moving target, so it is
        refused; and the copy always lands terminal.
    """
    source = _get_owned_board(job_id, current)
    if source.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="That storyboard is still generating — wait for it to finish, then copy it.",
        )
    if source.status == JobStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail="That storyboard is still a draft — finish generating it first.",
        )

    params = dict(source.params or {})
    params.pop("share_token", None)
    if workflow:
        params["workflow"] = workflow
    # Where it came from. Informational only — nothing resolves through it, or
    # the copy would stop being independent.
    params["copied_from"] = job_id

    # ⚠ A COPY IS A PROJECT. It occupies a slot in the library and can be
    # worked on independently — letting it in free would make "2 projects" mean
    # "2, plus as many duplicates as you like".
    usage_counters.increment(current.email, "projects")
    copy = get_store().create(
        character_name=source.character_name or "Storyboard",
        kind=JobKind.STORYBOARD,
        template=source.template,
        owner=current.email,
        params=params,
    )

    # Copy the panel PNGs, including every style-variant subfolder.
    src_dir = _board_dir(job_id)
    dst_dir = _board_dir(copy.job_id)
    if os.path.isdir(src_dir):
        try:
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        except OSError:
            # Without the files the copy is an empty board, which is worse than
            # no copy at all — take the record back out rather than leave one.
            logger.exception("[storyboard %s] could not copy panels to %s", job_id, dst_dir)
            get_store().delete(copy.job_id)
            raise HTTPException(
                status_code=500,
                detail="The board's panels couldn't be copied. Nothing was created.",
            ) from None

    # Re-point every panel url at the NEW job. Missing this would leave the copy
    # serving the ORIGINAL's files — it would look right and be a live link back
    # into the board this is supposed to be independent of.
    result = copy_module.deepcopy(source.result or {})
    for variant in result.get("variants") or []:
        for i, panel in enumerate(variant.get("panels") or []):
            if panel and panel.get("url"):
                panel["url"] = _repoint_panel_url(panel["url"], copy.job_id, i)
    for i, panel in enumerate(result.get("panels") or []):
        if panel and panel.get("url"):
            panel["url"] = _repoint_panel_url(panel["url"], copy.job_id, i)

    updated = get_store().update(
        copy.job_id, status=source.status, result=result or None, error=None
    ) or copy

    logger.info(
        "[storyboard %s] copied to %s for %s (workflow=%s)",
        job_id, copy.job_id, current.email, workflow or "-",
    )
    return _summarise_board(updated)


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


# ---------------------------------------------------------------------------
# Image to Animatic Image — one panel → its key-pose sequence (the "flipbook")
# ---------------------------------------------------------------------------
def _sequence_info(job: Job, index: int) -> PanelSequenceInfo:
    """This panel's sequence as the client sees it, urls filled from DISK.

    The arithmetic is `common.sequence_summary` — the animatic editor asks the
    same question about the shot behind one of its frames, and one answer to it
    is the point of that module.
    """
    return PanelSequenceInfo(**_sequence_summary(job, index))


@app.get("/storyboards/{job_id}/panels/{index}/sequence", response_model=PanelSequenceInfo)
def get_panel_sequence(
    job_id: str,
    index: int,
    current: CurrentUser = Depends(get_current_user),
):
    """What key poses this panel has so far (the strip under the shot)."""
    job = _get_owned_board(job_id, current)
    return _sequence_info(job, index)


@app.post(
    "/storyboards/{job_id}/panels/{index}/sequence",
    response_model=JobCreatedResponse,
    status_code=202,
)
def generate_panel_sequence(
    job_id: str,
    index: int,
    body: PanelSequenceRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Draw the key poses for ONE shot, off-request. Poll GET /jobs/{id}.

    This is Image to Animatic Image's Generate button. `duration_seconds` is
    the shot length; the number of drawings is derived from it (4 per second, so
    4s = 16), because the model is asked to block out the motion the way an
    animator would rather than render every one of the 96 real frames.

    Stop with POST /storyboards/{id}/stop; pressing Generate again RESUMES from
    the frames already on disk, so nothing already drawn is paid for twice.
    """
    job = _get_owned_board(job_id, current)
    # The whole of this — the busy check, the duration, the panel lookup, the
    # resume arithmetic and the plan handling — is `common.submit_sequence_run`,
    # because the animatic editor queues exactly the same run for the shot
    # behind one of its frames.
    queued = _submit_sequence_run(
        job, index, body.duration_seconds,
        resume=body.resume, preview=body.preview, redraw=body.redraw,
    )
    duration = queued["duration_seconds"]
    total = queued["total"]
    wanted = queued["wanted"]
    redraw = sorted({n for n in (body.redraw or []) if 0 <= n < total})

    message = (
        f"Drawing {wanted} key pose(s) for a {duration}s shot "
        f"({duration}s × {panel_sequence.FPS}fps = {duration * panel_sequence.FPS} frames)."
    )
    if redraw:
        which = ", ".join(str(n + 1) for n in redraw)
        message = f"Re-drawing key pose{'' if len(redraw) == 1 else 's'} {which}."
    elif queued["lengthened"]:
        message = (
            f"Carrying this shot on to {duration}s — drawing {wanted} more key "
            f"pose(s). The {queued['have']} already drawn are kept."
        )
    elif body.preview:
        message = (
            f"Drawing the first {wanted} key pose(s) so you can check the motion "
            f"before paying for all {total}."
        )
    return JobCreatedResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        kind=JobKind.STORYBOARD,
        character_name=job.character_name,
        message=message,
    )


@app.get("/storyboards/{job_id}/panels/{index}/frames/{n}")
def get_panel_frame(
    job_id: str,
    index: int,
    n: int,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve one key-pose frame PNG (owner-scoped)."""
    _get_owned_job(job_id, current)
    path = panel_sequence.frame_path(_board_dir(job_id), index, n)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Frame {n} not found.")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Panel VERSIONS — every render is kept, so a redraw you dislike is undoable
# ---------------------------------------------------------------------------
@app.get("/storyboards/{job_id}/panels/{index}/versions")
def get_panel_versions(
    job_id: str,
    index: int,
    current: CurrentUser = Depends(get_current_user),
):
    """How many renders this panel has, and which is showing.

    Counted from DISK, not from the job record: a board drawn before versions
    existed has none, and a crashed run can leave the two disagreeing. Disk is
    the honest answer, and it makes the feature work on old boards without a
    migration (they simply start collecting versions from their next redraw).
    """
    job = _get_owned_board(job_id, current)
    from storyboard_pipeline import adopt_existing_as_version, count_versions

    variants, active = _variants_of(job.result or {})
    # Adopt a pre-versions picture on READ as well as before a write. It is
    # idempotent and cheap, and it means a board drawn before this feature
    # reports "1 / 1" honestly instead of "0 versions" — and, more usefully, its
    # original is archived from the moment it is looked at rather than only when
    # something is about to overwrite it. Skipped while the board is generating,
    # where a panel file may be half written.
    if job.status != JobStatus.RUNNING:
        adopt_existing_as_version(_board_dir(job_id), active, index)
    total = count_versions(_board_dir(job_id), active, index)
    panels = variants[active].get("panels") or []
    panel = next((p for p in panels if p.get("index") == index), None) or {}
    shown = int(panel.get("active_version", max(0, total - 1)) or 0)
    return {
        "index": index,
        "versions": total,
        "active_version": min(shown, max(0, total - 1)),
        "urls": [
            f"/storyboards/{job_id}/panels/{index}/versions/{n}" for n in range(total)
        ],
    }


@app.get("/storyboards/{job_id}/panels/{index}/versions/{n}")
def get_panel_version(
    job_id: str,
    index: int,
    n: int,
    current: CurrentUser = Depends(get_current_user),
):
    """Serve one archived render of a panel."""
    job = _get_owned_board(job_id, current)
    from storyboard_pipeline import version_path

    _, active = _variants_of(job.result or {})
    path = version_path(_board_dir(job_id), active, index, n)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Version {n} not found.")
    return FileResponse(path, media_type="image/png")


@app.post("/storyboards/{job_id}/panels/{index}/versions/{n}", response_model=StoryboardSummary)
def activate_panel_version(
    job_id: str,
    index: int,
    n: int,
    current: CurrentUser = Depends(get_current_user),
):
    """Make version `n` this panel's picture again.

    Copies it over `panel_NN.png`, so everything downstream — the PDF, the ZIP,
    the key-pose generator, the animatic — picks it up with no knowledge that
    versions exist. Nothing is deleted: the version you were on is still there
    to switch back to.
    """
    job = _get_owned_board(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This board is busy.")
    from storyboard_pipeline import activate_panel_version as _activate

    result = job.result or {}
    variants, active = _variants_of(result)
    if not _activate(_board_dir(job_id), active, index, n):
        raise HTTPException(status_code=404, detail=f"Version {n} not found.")

    panels = list(variants[active].get("panels") or [])
    for i, p in enumerate(panels):
        if p.get("index") == index:
            panels[i] = {**p, "active_version": n, "failed": False}
            break
    variants[active]["panels"] = panels
    result["variants"] = variants
    result["panels"] = panels
    updated = get_store().update(job_id, result=result)
    logger.info("[storyboard %s] panel %d switched to version %d", job_id, index, n)
    return _summarise_board(updated or job)


@app.get("/storyboards/{job_id}/panels/{index}/frames.zip")
def download_panel_frames(
    job_id: str,
    index: int,
    current: CurrentUser = Depends(get_current_user),
):
    """Download one shot's key poses as a ZIP.

    Named `pose_001.png` … in play order, so unzipping gives a folder that
    already flips in the right sequence — drop it straight into an editor.
    """
    job = _get_owned_board(job_id, current)
    info = _sequence_info(job, index)
    if not info.frames:
        raise HTTPException(status_code=404, detail="This shot has no key poses yet.")

    board_dir = _board_dir(job_id)
    stem = _safe_filename(job.character_name, "storyboard")
    tmp = os.path.join(board_dir, f"_frames_{index:02d}.zip")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        # Numbered by PLAY ORDER, not by pose number: a sequence with a hole in
        # it must still unzip into a folder that flips without a stutter in the
        # file names. `info.frame_numbers` says which pose each one really is.
        for position, n in enumerate(info.frame_numbers, start=1):
            path = panel_sequence.frame_path(board_dir, index, n)
            if os.path.isfile(path):
                zf.write(path, f"pose_{position:03d}.png")

    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=f"{stem} - shot {index + 1} key poses.zip",
    )


@app.delete("/storyboards/{job_id}/panels/{index}/sequence", status_code=204)
def delete_panel_sequence(
    job_id: str,
    index: int,
    current: CurrentUser = Depends(get_current_user),
):
    """Throw away a panel's key poses so Generate starts clean.

    Needed because Generate RESUMES by default: without this there would be no
    way to redo a sequence you didn't like.
    """
    job = _get_owned_board(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="This board is busy.")
    folder = panel_sequence.sequence_dir(_board_dir(job_id), index)
    if os.path.isdir(folder):
        try:
            shutil.rmtree(folder)
        except OSError:
            logger.exception("[storyboard %s] could not remove %s", job_id, folder)
    result = dict(job.result or {})
    sequences = dict(result.get("sequences") or {})
    sequences.pop(str(index), None)
    result["sequences"] = sequences
    get_store().update(job_id, result=result)
    return None


@app.post("/storyboards/{job_id}/regenerate-panel")
def regenerate_storyboard_panel(
    job_id: str,
    body: PanelRegenerateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.image-generate')),
):
    """Re-draw ONE panel (Retry / edit-and-regenerate). Synchronous single call.

    Robust to a panel that isn't in the streamed result yet: it's located by its
    `index` field and, failing that, rebuilt from the shots stored on the job.
    Optional description/camera/location overrides let the user edit the prompt.

    The work is `common.regenerate_board_panel` — the animatic editor redraws
    the same panels from its Properties pane, and a second copy of the variant
    handling and the write-back is a second thing to keep in step.
    """
    job = _get_owned_job(job_id, current)
    if job.kind != JobKind.STORYBOARD:
        raise HTTPException(status_code=400, detail="Not a storyboard job.")
    updated = _regenerate_board_panel(
        job, body.index,
        description=body.description, camera=body.camera, location=body.location,
    )
    return {"panel": updated}


# ---------------------------------------------------------------------------
# Board editing — insert / delete a panel in place.
#
# Panels are addressed by position (panel_00.png, panel_01.png…) everywhere —
# the serve route, PDF, ZIP and public view all assume index == position. So
# rather than break that invariant, insert/delete RENUMBER: they shift the PNG
# files and rebuild each panel's index + url, in every style variant, keeping
# index == position intact. That's why nothing else needed to change.
# ---------------------------------------------------------------------------
def _variant_subdir(variant_idx: int) -> str:
    return "" if not variant_idx else f"v{variant_idx}"


def _panel_file(board_dir: str, variant_idx: int, index: int) -> str:
    sub = _variant_subdir(variant_idx)
    return os.path.join(board_dir, sub, f"panel_{index:02d}.png")


def _shift_panel_files(board_dir: str, variant_idx: int, start: int, count: int, delta: int) -> None:
    """Move panel files [start, count) by `delta` positions on disk.

    delta=+1 (insert): walk DOWN so a file never overwrites one not yet moved.
    delta=-1 (delete): walk UP for the same reason. Missing files (failed /
    ungenerated panels) are simply skipped.
    """
    order = range(count - 1, start - 1, -1) if delta > 0 else range(start, count)
    for i in order:
        src = _panel_file(board_dir, variant_idx, i)
        if os.path.isfile(src):
            os.replace(src, _panel_file(board_dir, variant_idx, i + delta))


def _renumber(panels: list[dict], job_id: str, variant_idx: int) -> list[dict]:
    """Set every panel's index to its position and rebuild url for drawn ones."""
    from storyboard_pipeline import _panel_url

    out = []
    for pos, p in enumerate(panels):
        q = dict(p)
        q["index"] = pos
        # A drawn panel keeps its image (which just moved to this position); an
        # empty/failed one has no file, so its url stays None.
        q["url"] = _panel_url(job_id, pos, variant_idx) if p.get("url") else None
        out.append(q)
    return out


def _persist_structural_change(job_id: str, result: dict, variants: list[dict], active: int) -> dict:
    count = len(variants[active].get("panels") or [])
    for v in variants:
        v["ok_count"] = sum(1 for p in (v.get("panels") or []) if p.get("url") and not p.get("failed"))
    result["variants"] = variants
    result["active_variant"] = active
    result["panels"] = variants[active].get("panels") or []
    result["ok_count"] = variants[active].get("ok_count", 0)
    result["count"] = count
    job = get_store().get(job_id)
    params = dict((job.params if job else {}) or {})
    params["count"] = count
    get_store().update(job_id, result=result, params=params)
    return result


@app.post("/storyboards/{job_id}/panels/insert")
def insert_storyboard_panel(
    job_id: str,
    body: PanelInsertRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Insert a blank panel at position `at` (shifts the rest down, all variants)."""
    job = _get_owned_board(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Wait for the board to finish generating first.")

    result = job.result or {}
    variants, active = _variants_of(result)
    board_dir = _board_dir(job_id)
    existing = variants[active].get("panels") or []
    n = len(existing)
    at = max(0, min(body.at, n))  # clamp; == n means append at the end

    # Inherit the scene from the panel it lands next to (the one it pushes down,
    # else the one before it) — a panel added inside scene 3 belongs to scene 3.
    neighbour = existing[at] if at < n else (existing[at - 1] if n else None)

    new_panel_base = {
        "scene_number": (neighbour or {}).get("scene_number", 1) or 1,
        "shot_number": at + 1,
        "description": body.description or "",
        "characters": [],
        # A hand-inserted panel has no script behind it, so nothing is spoken in
        # it until the user types a line on the review step.
        "dialogue": [],
        "assets": [],
        "location": "",
        "camera": "",
        "url": None,
        "failed": False,
    }
    for vi, v in enumerate(variants):
        panels = list(v.get("panels") or [])
        _shift_panel_files(board_dir, vi, at, len(panels), +1)
        panels.insert(at, dict(new_panel_base))
        v["panels"] = _renumber(panels, job_id, vi)

    _persist_structural_change(job_id, result, variants, active)
    return {"panels": variants[active]["panels"], "count": len(variants[active]["panels"])}


@app.delete("/storyboards/{job_id}/panels/{index}")
def delete_storyboard_panel(
    job_id: str,
    index: int,
    current: CurrentUser = Depends(get_current_user),
):
    """Delete the panel at `index` (removes its files, shifts the rest up, all variants)."""
    job = _get_owned_board(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Wait for the board to finish generating first.")

    result = job.result or {}
    variants, active = _variants_of(result)
    board_dir = _board_dir(job_id)
    n = len(variants[active].get("panels") or [])
    if not (0 <= index < n):
        raise HTTPException(status_code=404, detail=f"Panel {index} not found.")
    if n <= 1:
        raise HTTPException(status_code=400, detail="A storyboard needs at least one panel.")

    for vi, v in enumerate(variants):
        panels = list(v.get("panels") or [])
        f = _panel_file(board_dir, vi, index)
        if os.path.isfile(f):
            try:
                os.remove(f)
            except OSError:
                logger.warning("[storyboard %s] could not remove %s", job_id, f)
        if 0 <= index < len(panels):
            panels.pop(index)
        _shift_panel_files(board_dir, vi, index + 1, len(panels) + 1, -1)
        v["panels"] = _renumber(panels, job_id, vi)

    _persist_structural_change(job_id, result, variants, active)
    return {"panels": variants[active]["panels"], "count": len(variants[active]["panels"])}


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
        # A re-style changes the art style, never the story's world or its cast.
        "world": job.params.get("world") or {},
        "cast": job.params.get("cast") or [],
        "assets": job.params.get("assets") or [],
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


def _request_stop(job, email: str, noun: str) -> dict:
    """Flag a running job to stop. Shared by both workflows' Stop buttons.

    Whatever has already been generated is kept and the job finishes normally —
    stopping is about not SPENDING more, not about throwing work away. Work
    already in flight still completes: an HTTP request can't be un-sent.
    """
    if job.status not in (JobStatus.RUNNING, JobStatus.QUEUED):
        raise HTTPException(status_code=409, detail=f"This {noun} isn't generating.")

    from cancel import request_cancel

    request_cancel(job.job_id)
    logger.info("[job %s] stop requested by %s", job.job_id, email)
    return {"stopping": True, "job_id": job.job_id}


@app.post("/storyboards/{job_id}/stop")
def stop_storyboard(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Stop a board that is still generating — the board's "Stop generation".

    Panels not yet started are skipped; the one or two already talking to the
    image API finish. Every skipped panel can then be drawn on its own from the
    board with "Generate this panel".
    """
    return _request_stop(_get_owned_board(job_id, current), current.email, "board")


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
            # Printed after the board so the "FROM YOUR SCRIPT · LINE n"
            # citations on the shot cards can be looked up in the export.
            script=(job.params or {}).get("script") or "",
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

    Files are named the way they are LABELLED IN THE APP — the folder says what
    kind of thing it is, so repeating the board title on every image only made
    the names harder to read:

        panels/Shot 03.png ...      numbered as the board numbers them
        characters/Lubdhaka.png
        props/Bilva Tree.png
        backgrounds/Dense Forest.png
        <Title>.pdf                 the one file that IS the whole board

    Panel numbers are the board's own (a failed panel leaves a gap, which is
    honest — the picture really is missing). Panels come from the ACTIVE style
    variant — the one shown on the board. Only AI-GENERATED references are
    bundled; UPLOADED ones are skipped because the user already has those.
    """
    job = _get_owned_board(job_id, current)

    _safe = _safe_filename
    title = _safe(job.character_name, "storyboard")

    # Two characters can share a name once punctuation is stripped ("Shiva." and
    # "Shiva"), and a zip with two identical entry names is corrupt — so names
    # are made unique per folder: "Shiva.png", "Shiva (2).png".
    def _unique(used: set[str], name: str, fallback: str) -> str:
        base = _safe(name, fallback)
        candidate, n = base, 1
        while candidate.lower() in used:
            n += 1
            candidate = f"{base} ({n})"
        used.add(candidate.lower())
        return candidate
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
        # --- Panels, named as the board numbers them --------------------------
        used_panels: set[str] = set()
        for p in panels:
            if p.get("failed") or not p.get("url"):
                continue
            path = os.path.join(src_dir, f"panel_{p['index']:02d}.png")
            if not os.path.isfile(path):
                continue
            name = _unique(used_panels, f"Shot {p['index'] + 1:02d}", "Shot")
            zf.write(path, f"panels/{name}.png")
            added += 1

        # --- References, named after the character / prop / location ----------
        # UPLOADED refs are skipped on purpose: the user already has those images.
        # Only AI-generated references are worth bundling.
        used_chars: set[str] = set()
        for name, path in char_refs.items():
            if not os.path.isfile(path) or not _ref_is_generated(path):
                continue
            zf.write(path, f"characters/{_unique(used_chars, name, 'character')}.png")
            added += 1

        used_props: set[str] = set()
        used_bgs: set[str] = set()
        for name, path in asset_refs.items():
            if not os.path.isfile(path) or not _ref_is_generated(path):
                continue
            if str(categories.get(name, "prop")).lower() == "background":
                dest = f"backgrounds/{_unique(used_bgs, name, 'background')}.png"
            else:
                dest = f"props/{_unique(used_props, name, 'prop')}.png"
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
    _gate: CurrentUser = Depends(require_feature('workflow.text-to-image')),
    _quota: CurrentUser = Depends(require_quota("projects")),
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

    usage_counters.increment(current.email, "projects")
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
def list_jobs(
    limit: int = 50,
    kind: str | None = None,
    current: CurrentUser = Depends(get_current_user),
):
    """The caller's jobs, newest first.

    `kind` is a comma-separated filter (e.g. `generate,meshy`) that keeps the two
    workflows apart: the Text-to-Image job list asks for the character kinds, so
    storyboards stay in "Your Storyboards" where they belong. Omitted = all kinds.

    ⚠ `params` IS NOT SENT, and this is the one list route that can say that
    honestly. Every reader of this list — the job rail, Home's "Recent work",
    the final-video art picker — prints `character_name`, `status`, `template`,
    `kind` and `created_at`, and reaches for `result.zip`; not one of them opens
    `params`. What `params` DOES hold is the whole run's inputs, reference
    images included, so shipping it made the list heavier the more work an
    account had done — which is exactly backwards, and was most of why a
    long-standing account waited on a dashboard a new one got instantly.
    Anything that needs the inputs asks for the one job: `GET /jobs/{id}`.
    """
    kinds = None
    if kind:
        kinds = []
        for raw in kind.split(","):
            value = raw.strip().lower()
            if not value:
                continue
            try:
                kinds.append(JobKind(value))
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown job kind '{value}'. Use one of "
                    f"{[k.value for k in JobKind]}.",
                )
    return get_store().list(
        limit=limit, owner=current.email, kinds=kinds or None, drop=("params",)
    )


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, current: CurrentUser = Depends(get_current_user)):
    return _get_owned_job(job_id, current)


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Delete a character job: its record, its reference upload, and its assets.

    Asset files live in output/{character_name}/, which is keyed by NAME, not by
    job id — so two runs of the same character share one folder. We only remove
    that folder when no other job of this owner still points at it, otherwise
    deleting one run would wipe the other run's images.
    """
    job = _get_owned_job(job_id, current)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This job is still generating — stop it first, then delete.",
        )

    # The reference upload is keyed by job id, so it is always safe to remove.
    upload_dir = os.path.join(config.UPLOAD_DIR, job_id)
    if os.path.isdir(upload_dir):
        try:
            shutil.rmtree(upload_dir)
        except OSError:
            logger.exception("[job %s] could not remove %s", job_id, upload_dir)

    # Shared-name check before touching generated assets.
    others = [
        j for j in get_store().list(limit=500, owner=current.email)
        if j.job_id != job_id and j.character_name == job.character_name
    ]
    if not others:
        char_dir = os.path.join(config.OUTPUT_DIR, job.character_name)
        if os.path.isdir(char_dir):
            try:
                shutil.rmtree(char_dir)
            except OSError:
                logger.exception("[job %s] could not remove %s", job_id, char_dir)
    else:
        logger.info(
            "[job %s] keeping assets for '%s' — %d other job(s) still use them.",
            job_id, job.character_name, len(others),
        )

    get_store().delete(job_id)
    logger.info("[job %s] deleted by %s", job_id, current.email)
    return None


@app.post("/jobs/{job_id}/stop")
def stop_job(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Stop a character run that is still generating (Text to Image "Stop").

    The part being drawn finishes; nothing after it is started. Parts already
    generated are kept, zipped and downloadable, and any part can be redone
    later with the existing per-part regenerate. 3D submission is skipped
    entirely — stopping means spending nothing more on this run.
    """
    return _request_stop(_get_owned_job(job_id, current), current.email, "job")


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
    _gate: CurrentUser = Depends(require_feature('cap.3d-meshy')),
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


def _reap_orphaned_jobs() -> None:
    """Close out jobs that were mid-flight when the process last died.

    Work runs in this process's thread pool, so a job still marked RUNNING or
    QUEUED at startup has NO worker behind it and never will — the pool it
    belonged to went away with the previous process. Left alone the record says
    "generating" for ever, and the board it belongs to is permanently frozen:
    the toolbar shows "Stop generation", every Regenerate button stays hidden
    because the board thinks it is busy, and nothing the user clicks can help.
    Reported exactly that way ("i cant see regenarte buttun and i see nothing
    happen") after a restart mid-board; four animatics from previous days were
    still sitting QUEUED from the same cause.

    Marked SUCCEEDED, not FAILED, and the `result` is left untouched: the panels
    that WERE drawn are real and the user should keep them. `error` carries the
    explanation, and the board's normal "draw the remaining" / per-panel
    Regenerate buttons finish the job.

    ONE API PROCESS PER JOB STORE is assumed — with two sharing a database this
    would reap the other's live work, so it is behind API_REAP_ORPHANED_JOBS.
    """
    if not config.REAP_ORPHANED_JOBS:
        return
    try:
        store = get_store()
        stale = [
            j for j in store.list(limit=500)
            if j.status in (JobStatus.RUNNING, JobStatus.QUEUED)
        ]
        for job in stale:
            store.update(
                job.job_id,
                status=JobStatus.SUCCEEDED,
                progress=None,
                error=(
                    "The server restarted while this was generating, so it "
                    "stopped early. Anything already generated has been kept — "
                    "use Regenerate to finish the rest."
                ),
            )
            logger.warning(
                "[startup] job %s (%s, %s) was still %s with no worker — closed "
                "it out so its page isn't stuck showing 'generating'.",
                job.job_id, job.kind, job.character_name, job.status,
            )
        if stale:
            logger.warning("[startup] closed out %d interrupted job(s).", len(stale))
    except Exception:  # noqa: BLE001 — never stop the API booting over this
        logger.exception("[startup] could not check for interrupted jobs (ignored)")


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
    _reap_orphaned_jobs()


@app.on_event("shutdown")
def _on_shutdown():
    worker.shutdown()
