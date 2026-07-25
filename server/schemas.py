"""
schemas.py — Pydantic models for API requests and responses.

The generate request comes in as multipart/form-data (because it carries an
uploaded image), so its fields are declared inline in main.py as Form(...)
parameters. These models describe the JSON responses and the persisted job
shape.
"""

from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobKind(str, Enum):
    GENERATE = "generate"      # full asset-generation pipeline run
    MESHY = "meshy"            # 3D submission for an already-generated character
    STORYBOARD = "storyboard"  # script → storyboard panel generation


class Job(BaseModel):
    """The persisted job record (stored in Firestore / memory)."""

    job_id: str
    kind: JobKind = JobKind.GENERATE
    status: JobStatus = JobStatus.QUEUED
    # Email of the user who created the job (for per-user scoping).
    owner: str | None = None
    character_name: str
    template: str | None = None
    params: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str
    # Live progress while status == RUNNING: {percent, stage, current_part,
    # message, done_parts, total_parts}. Cleared/ignored once finished.
    progress: dict | None = None
    # Populated when status == SUCCEEDED — this is the pipeline summary dict.
    # May be partially populated (urls only) while RUNNING for live preview.
    result: dict | None = None
    # Populated when status == FAILED.
    error: str | None = None


class JobCreatedResponse(BaseModel):
    """Returned immediately from POST endpoints that enqueue a job."""

    job_id: str
    status: JobStatus
    kind: JobKind
    character_name: str
    message: str


class TemplateInfo(BaseModel):
    name: str
    label: str | None = None  # friendly display name for the client dropdown
    character_defaults: dict = Field(default_factory=dict)
    slot_renames: dict = Field(default_factory=dict)
    parts: list[str] = Field(default_factory=list)  # this template's part order


class AssetItem(BaseModel):
    """One generated view image of one part."""

    part: str
    view: str  # front | left | three_quarter | back
    url: str   # public GCS URL or absolute local path (for local_only runs)


class AssetsResponse(BaseModel):
    """Individual asset URLs for a completed generation job."""

    job_id: str
    character_name: str
    is_local: bool = Field(
        ...,
        description="True if URLs are local file paths (local_only run), False if public GCS URLs.",
    )
    # Nested view: {part: {view: url}} — convenient for building a gallery per part.
    parts: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Flat view: one entry per image — convenient for iterating.
    assets: list[AssetItem] = Field(default_factory=list)
    zip: str | None = None


class MeshyRequest(BaseModel):
    """Body for POST /jobs/{job_id}/meshy — submit generated parts for 3D."""

    parts: list[str] = Field(
        ...,
        description="Output part names to submit for 3D generation, e.g. ['hair', 'saree'].",
        min_length=1,
    )
    # 3D backend: "meshy" (tested) or "tripo" (unverified).
    provider: str = Field("meshy", description="3D provider: 'meshy' or 'tripo'.")
    # Optional per-request key; falls back to the user's saved key, then env var.
    api_key: str | None = None


class World(BaseModel):
    """The story's visual world, read from the script by the breakdown.

    Carried into EVERY image prompt — character references, prop/background
    references and each panel — so a Shiva Purana script draws Indian people and
    Indian architecture instead of the image model's Western default. All fields
    are optional: an empty world leaves prompts exactly as they were.
    """

    setting: str = ""  # place + period
    culture: str = ""  # cultural / religious tradition
    ethnicity: str = ""  # what the people of this world look like
    wardrobe: str = ""
    environment: str = ""  # architecture, landscape, everyday objects
    notes: str = ""  # iconography, rituals, symbols, colours


class ReferenceRequest(BaseModel):
    """Body for POST /characters/reference — generate a character from text."""

    prompt: str = Field(
        ...,
        description="Free-form character description, e.g. 'An Indian woman in a red saree, age 30'.",
        min_length=5,
    )
    world: World | None = Field(
        None,
        description="Story world (region/period/culture) to draw this character within.",
    )
    provider: str | None = Field(
        None,
        description="Image backend: 'vertex' or 'gemini'. Defaults to server IMAGE_PROVIDER.",
    )


class ReferenceResponse(BaseModel):
    """Returned from POST /characters/reference after generating the image."""

    reference_id: str
    image_url: str
    message: str = "Reference image generated successfully."


class AssetReferenceRequest(BaseModel):
    """Body for POST /assets/reference — generate a prop/background reference."""

    prompt: str = Field(
        ...,
        description="Free-form asset description, e.g. 'a worn brown leather slipper'.",
        min_length=3,
    )
    category: str = Field(
        "prop",
        description="'prop' (a specific object) or 'background' (a location/set).",
    )
    world: World | None = Field(
        None,
        description="Story world (region/period/culture) this prop/location belongs to.",
    )
    provider: str | None = Field(
        None,
        description="Image backend: 'vertex' or 'gemini'. Defaults to server IMAGE_PROVIDER.",
    )


class RegeneratePartRequest(BaseModel):
    """Body for POST /jobs/{job_id}/regenerate-part."""

    part: str = Field(
        ...,
        description="Part name to regenerate, e.g. 'fullbody', 'hair', 'face'.",
    )
    prompt: str | None = Field(
        None,
        description="Custom prompt text to use for this part.",
    )
    provider: str | None = Field(
        None,
        description="Image backend: 'vertex' or 'gemini'. Defaults to server IMAGE_PROVIDER.",
    )


class RegenerateViewRequest(BaseModel):
    """Body for POST /jobs/{job_id}/regenerate-view — redo ONE view of a part."""

    part: str = Field(..., description="Part name, e.g. 'fullbody', 'hair'.")
    view: str = Field(..., description="View: front | left | three_quarter | back.")
    prompt: str | None = Field(None, description="Optional custom prompt for the part.")
    provider: str | None = Field(None, description="Image backend override.")


# ---------------------------------------------------------------------------
# Script → Storyboard (Stage A: script breakdown)
# ---------------------------------------------------------------------------
class ScriptBreakdownRequest(BaseModel):
    """Body for POST /storyboards/breakdown — turn a script into a shot list."""

    script: str = Field(
        ...,
        description="The raw script / story text to break into storyboard shots.",
        min_length=20,
    )
    genre: str | None = Field(None, description="Optional genre — shapes tone/pacing.")
    style: str | None = Field(None, description="Chosen visual style (passed through).")
    aspect_ratio: str | None = Field(None, description="Chosen aspect ratio (passed through).")
    provider: str | None = Field(
        None,
        description="Text backend: 'vertex' or 'gemini'. Defaults to server TEXT_PROVIDER.",
    )


class Shot(BaseModel):
    """One storyboard panel produced by the script breakdown."""

    scene_number: int = 1
    shot_number: int = 1
    description: str
    characters: list[str] = Field(default_factory=list)
    # Names of props / backgrounds visible in this shot (match Asset.name).
    assets: list[str] = Field(default_factory=list)
    location: str = ""
    camera: str = ""
    # Traceability: the VERBATIM script text this panel was drawn from, with its
    # 1-based line range, so the writer can see which lines became this shot.
    # Empty when the breakdown's quote couldn't be found in the script (it is
    # matched back against the real text — never taken on trust).
    script_line: str = ""
    script_line_start: int | None = None
    script_line_end: int | None = None


class Character(BaseModel):
    """A named character with a visual description (the 'cast')."""

    name: str
    description: str = ""


class Asset(BaseModel):
    """A locked prop or background/location for cross-panel consistency."""

    name: str
    # "prop" (a specific object) or "background" (a location/set).
    category: str = "prop"
    description: str = ""


class ScriptBreakdownResponse(BaseModel):
    """Returned from POST /storyboards/breakdown."""

    shots: list[Shot]
    characters: list[Character] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    # The story's region/period/culture, so every later image prompt can carry it.
    world: World = Field(default_factory=World)
    count: int
    style: str | None = None
    aspect_ratio: str | None = None


class StoryboardCreateRequest(BaseModel):
    """Body for POST /storyboards — generate panels from a reviewed shot list."""

    shots: list[Shot] = Field(..., min_length=1, description="The reviewed shots to draw.")
    style: str = Field("custom", description="Visual style id, e.g. 'sketch', 'comics'.")
    aspect_ratio: str = Field("16:9", description="Panel aspect ratio, e.g. '16:9'.")
    title: str | None = Field(None, description="Optional storyboard title.")
    # Stored (not used for drawing) so the library can label a saved board.
    genre: str | None = Field(None, description="Genre chosen on the form, for the library card.")
    # Character consistency (Stage B): map character name → reference_id (from
    # POST /characters/reference). Those refs are fed into every panel the
    # character appears in, so they stay visually consistent.
    character_refs: dict[str, str] = Field(default_factory=dict)
    # Asset consistency (Stage B2): map asset name → reference_id (from
    # POST /assets/reference). Those prop/background refs are fed into every panel
    # the asset appears in, so props and locations stay visually consistent.
    asset_refs: dict[str, str] = Field(default_factory=dict)
    # asset name → "prop" | "background". Not used for drawing — it's what lets
    # the assets ZIP file them into props/ and backgrounds/ folders.
    asset_categories: dict[str, str] = Field(default_factory=dict)
    # The script's world — prefixed onto every panel prompt so the whole board
    # stays true to the story's region, period and culture.
    world: World | None = Field(
        None, description="Story world (region/period/culture) for every panel."
    )
    # The source script. Not used for drawing — kept so a re-opened or duplicated
    # board can still show the writer the text its shots were traced from.
    script: str | None = Field(None, description="The source script text.")
    provider: str | None = Field(
        None,
        description="Image backend: 'vertex' or 'gemini'. Defaults to server IMAGE_PROVIDER.",
    )


class PanelRegenerateRequest(BaseModel):
    """Body for POST /storyboards/{job_id}/regenerate-panel — redo one panel.

    The optional overrides let the user edit a shot's prompt on the board and
    re-draw it with the new wording (persisted onto the panel).
    """

    index: int = Field(..., ge=0, description="Panel index to regenerate.")
    description: str | None = Field(None, description="Edited shot description (overrides the stored one).")
    camera: str | None = Field(None, description="Edited camera/angle.")
    location: str | None = Field(None, description="Edited location.")


class PanelInsertRequest(BaseModel):
    """Body for POST /storyboards/{job_id}/panels/insert — add a blank panel.

    `at` is the 0-based position the new panel takes; everything from there on
    shifts down by one. The panel starts empty (no image) — the user types a
    prompt on the board and generates it with the normal regenerate-panel call.
    """

    at: int = Field(..., ge=0, description="Position to insert the new panel at.")
    description: str = Field("", description="Optional starting prompt for the new panel.")


class RestyleRequest(BaseModel):
    """Body for POST /storyboards/{job_id}/restyle — re-draw the board in a new style."""

    style: str = Field(..., min_length=1, description="New visual style id (or freeform 'custom' text).")


class ActiveVariantRequest(BaseModel):
    """Body for POST /storyboards/{job_id}/active-variant — switch which style shows."""

    index: int = Field(..., ge=0, description="Variant index to make active.")


# ---------------------------------------------------------------------------
# Storyboard library ("Your Storyboards")
# ---------------------------------------------------------------------------
class StoryboardSummary(BaseModel):
    """One saved storyboard project, as shown on the library grid.

    Deliberately lean — the library renders dozens of these, so it must not drag
    the whole panel list / shot list of every board across the wire.
    """

    job_id: str
    title: str
    status: JobStatus
    style: str | None = None
    aspect_ratio: str | None = None
    genre: str | None = None
    panel_count: int = 0
    # First successfully drawn panel of the ACTIVE style variant — the cover.
    # Both are None when nothing is drawn yet (the card shows a placeholder).
    cover_index: int | None = None
    # Serve path for that panel, carrying its ?v=<variant> so a restyled board
    # shows the style the owner last picked.
    cover_url: str | None = None
    shared: bool = False
    share_token: str | None = None
    created_at: str
    updated_at: str


class StoryboardProject(BaseModel):
    """A saved storyboard's reusable inputs — powers 'Duplicate'.

    Returns what the workflow needs to re-open the board's shot list on the
    review step, without re-running the (paid) script breakdown.
    """

    job_id: str
    title: str
    style: str | None = None
    aspect_ratio: str | None = None
    genre: str | None = None
    shots: list[Shot] = Field(default_factory=list)
    # So a duplicated board redraws in the same culture/period as the original.
    world: World = Field(default_factory=World)
    # The source script, so the review step can still show it line by line.
    script: str = ""


class StoryboardRenameRequest(BaseModel):
    """Body for PATCH /storyboards/{job_id} — rename a saved storyboard."""

    title: str = Field(..., min_length=1, max_length=120)


class ShareResponse(BaseModel):
    """Returned by the share endpoints — the token for a public view link."""

    shared: bool
    share_token: str | None = None


class PublicStoryboard(BaseModel):
    """A shared storyboard as seen by someone who is NOT logged in.

    Carries only what the read-only viewer renders — no owner email, no shot
    prompts, no reference paths.
    """

    title: str
    style: str | None = None
    aspect_ratio: str | None = None
    genre: str | None = None
    panel_count: int = 0
    # Panel indexes that actually have an image (the viewer requests only these).
    panel_indexes: list[int] = Field(default_factory=list)
    created_at: str


