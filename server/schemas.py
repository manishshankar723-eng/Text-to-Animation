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
    ANIMATIC = "animatic"      # timed image sequence + audio → video


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


class DialogueLine(BaseModel):
    """One spoken line in a shot: who says it, and what they say.

    `character` may be empty (a voice off, or a line the breakdown couldn't
    attribute); `line` never is — a speaker with no words isn't dialogue.
    """

    character: str = ""
    line: str = ""


class Shot(BaseModel):
    """One storyboard panel produced by the script breakdown."""

    scene_number: int = 1
    shot_number: int = 1
    description: str
    characters: list[str] = Field(default_factory=list)
    # What is SPOKEN in this shot, in order. Empty for a shot where nobody
    # speaks — the review card, the board and the PDF then show no dialogue
    # block at all. Never fed into the image prompt (image models would draw
    # the words into the picture as speech bubbles).
    dialogue: list[DialogueLine] = Field(default_factory=list)
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


class AnimaticFrameSource(BaseModel):
    """Where one animatic frame's picture comes from.

    A storyboard panel is referenced BY INDEX and never copied, so re-drawing
    that panel on the board updates the animatic too. An upload is a file the
    user dropped into this animatic, stored under its own id.
    """

    kind: str = Field("panel", description="'panel' (a storyboard panel) or 'upload'.")
    storyboard_id: str | None = Field(None, description="Board job id (kind='panel').")
    index: int | None = Field(None, description="Panel index on that board (kind='panel').")
    upload_id: str | None = Field(None, description="Uploaded image id (kind='upload').")


class AnimaticFrame(BaseModel):
    """One held image in the sequence, with how long it stays on screen."""

    id: str = Field(..., description="Stable client-side id (survives reordering).")
    src: AnimaticFrameSource
    # 0.1s–10min. The whole point of the editor is that this is per-frame.
    duration_ms: int = Field(2000, ge=100, le=600_000)
    label: str = Field("", description="Caption, e.g. 'Shot 3'. Burned in only if show_labels.")
    # Filled by the server on read so the client has ONE url shape for both
    # source kinds. Ignored on write.
    url: str | None = None


class AnimaticAudio(BaseModel):
    """One audio track laid under the sequence.

    An animatic can carry several — music under a voiceover is the usual pair —
    and they are MIXED on export, each at its own volume.
    """

    upload_id: str
    filename: str = ""
    # Measured in the BROWSER (decodeAudioData) and sent up — the server has no
    # audio decoder of its own, and ffmpeg doesn't need to be told.
    duration_ms: int = 0
    # How far into the audio file playback starts (skips an intro). Never negative.
    offset_ms: int = Field(0, ge=0)
    # How long the track PLAYS from `offset_ms`. None = to the end of the file.
    # This is the trim you get by dragging the clip's right edge, and it's what
    # lets a 4-minute song sit under a 40-second animatic without being cut by
    # the export instead of by you.
    trim_ms: int | None = Field(None, ge=100)
    # 1.0 = as recorded. Above 1 amplifies, which is why the ceiling is low —
    # a music bed usually wants pulling DOWN under a voice, not pushing up.
    volume: float = Field(1.0, ge=0.0, le=2.0)
    muted: bool = False
    url: str | None = None


class AnimaticTextClip(BaseModel):
    """One piece of on-screen text, with its OWN start and length.

    Deliberately time-based rather than attached to a frame: a caption often has
    to appear part-way through a held image, or run across a cut. It lives on its
    own timeline track for exactly that reason.
    """

    id: str
    text: str = ""
    # Where it sits on the timeline, in video time.
    start_ms: int = Field(0, ge=0)
    duration_ms: int = Field(2000, ge=100, le=600_000)
    # Where it sits on the frame.
    position: str = Field("bottom", description="'top' | 'middle' | 'bottom'.")
    align: str = Field("center", description="'left' | 'center' | 'right'.")
    size: str = Field("medium", description="'small' | 'medium' | 'large'.")
    color: str = Field("#ffffff", description="Text colour, #rrggbb.")
    # How the text is kept readable over busy art: a translucent bar behind it
    # ("scrim"), a solid box, or an outline only ("none").
    backdrop: str = Field("scrim", description="'scrim' | 'box' | 'none'.")

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


class AnimaticShape(BaseModel):
    """One shape drawn over the picture, with its OWN start and length.

    Geometry is stored as FRACTIONS of the frame (0–1), never pixels: the same
    project exports at 720p or 4K, and the preview box is a few hundred pixels
    wide. A fraction means the shape lands in the same place in all of them.
    `x`/`y` are the shape's CENTRE, which is what makes rotation behave.
    """

    id: str
    kind: str = Field("rect", description="'rect' | 'ellipse' | 'pentagon' | 'star'.")
    # Where it sits on the timeline, in video time — same as a text clip.
    start_ms: int = Field(0, ge=0)
    duration_ms: int = Field(2000, ge=100, le=600_000)
    # Where it sits on the frame. Centre and size, as fractions of width/height.
    # Allowed slightly outside 0–1 so a shape can be run off the edge on purpose.
    x: float = Field(0.5, ge=-1.0, le=2.0)
    y: float = Field(0.5, ge=-1.0, le=2.0)
    w: float = Field(0.25, gt=0.0, le=4.0)
    h: float = Field(0.25, gt=0.0, le=4.0)
    color: str = Field("#c2185b", description="Fill colour, #rrggbb.")
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    rotation: float = Field(0.0, ge=-360.0, le=360.0, description="Degrees, clockwise.")

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


class AnimaticSettings(BaseModel):
    """Everything about an animatic that isn't a frame or the audio."""

    aspect_ratio: str = Field("16:9", description="Frame shape, e.g. '16:9', '9:16', '1:1'.")
    fps: int = Field(24, ge=1, le=60)
    # The SHORT edge of the exported frame, so 1080 means 1920×1080 for 16:9 and
    # 1080×1920 for 9:16 — the way "1080p" is normally meant.
    resolution: int = Field(1080, ge=360, le=2160, description="Short edge in pixels.")
    # Maps to an x264 CRF. Stills compress extremely well, so even "high" stays
    # small; this mostly matters once there are lots of frames.
    quality: str = Field("high", description="'high' | 'medium' | 'low'.")
    # Lets an export be made silent without removing the tracks from the project.
    include_audio: bool = True
    # How long the exported video is:
    #   "timeline" — to the end of the LONGEST layer, holding the last picture
    #                while audio or text runs on (the default: if the music is
    #                longer than the pictures you almost always want to hear it)
    #   "frames"   — stop at the last picture, cutting anything after it
    end_at: str = Field("timeline", description="'timeline' | 'frames'.")
    # "contain" letterboxes (whole image visible — the default, because a
    # storyboard frame you cropped is a frame you can't read); "cover" fills.
    fit: str = Field("contain", description="'contain' (letterbox) or 'cover' (crop to fill).")
    background: str = Field("#000000", description="Letterbox colour, #rrggbb.")
    show_labels: bool = Field(False, description="Burn each frame's label into the video.")


class AnimaticProject(BaseModel):
    """A saved animatic: the frames, their timing, the audio and the settings."""

    job_id: str
    title: str
    status: JobStatus
    # The board this was started from, if any (Duplicate / 'back to board').
    source_storyboard_id: str | None = None
    settings: AnimaticSettings = Field(default_factory=AnimaticSettings)
    frames: list[AnimaticFrame] = Field(default_factory=list)
    # The text layer. Independent of the frames — a clip can start mid-frame and
    # run across a cut.
    texts: list[AnimaticTextClip] = Field(default_factory=list)
    # The shape layer — rectangles, ellipses, pentagons and stars drawn over the
    # picture. Timed like the text layer and, like it, independent of the frames.
    shapes: list[AnimaticShape] = Field(default_factory=list)
    # Zero or more audio tracks, mixed together on export. Records written before
    # multi-track carried a single `audio` object; it is migrated on read.
    audio_tracks: list[AnimaticAudio] = Field(default_factory=list)
    # Sum of the frame durations — the length of the video that will be exported.
    # The FRAMES decide the length; a text clip hanging past the end is simply
    # not seen (and is cut by the exporter).
    duration_ms: int = 0
    # Last export: {url, size_bytes, exported_at, duration_ms, stale}. `stale` is
    # set the moment the project is edited afterwards, so the UI can say the
    # downloadable file no longer matches what's on screen.
    video: dict | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class AnimaticCreateRequest(BaseModel):
    """Body for POST /animatics — start a new animatic project.

    With `source_storyboard_id` and no frames, the server fills the sequence
    with that board's drawn panels in order — the board's "Make animatic".
    """

    title: str | None = None
    source_storyboard_id: str | None = None
    settings: AnimaticSettings | None = None
    frames: list[AnimaticFrame] = Field(default_factory=list)
    default_duration_ms: int = Field(2000, ge=100, le=600_000)


class AnimaticSaveRequest(BaseModel):
    """Body for PUT /animatics/{id} — save the edited project.

    Every field is optional so a partial save is possible, which is why removing
    the audio needs its own flag: `audio: null` can't be told apart from
    "audio not included in this save".
    """

    title: str | None = None
    settings: AnimaticSettings | None = None
    frames: list[AnimaticFrame] | None = None
    texts: list[AnimaticTextClip] | None = None
    # Send the whole list; an empty list removes every shape (same rule as the
    # audio tracks below, and for the same reason).
    shapes: list[AnimaticShape] | None = None
    # Send the whole list; an empty list removes every track. (This replaced a
    # single `audio` field plus a `clear_audio` flag — with a list, "none" is
    # just an empty list and needs no companion flag.)
    audio_tracks: list[AnimaticAudio] | None = None


class AnimaticSummary(BaseModel):
    """One saved animatic, as shown on the library grid (deliberately lean)."""

    job_id: str
    title: str
    status: JobStatus
    aspect_ratio: str = "16:9"
    frame_count: int = 0
    duration_ms: int = 0
    # Serve path for the first frame — the card's thumbnail.
    cover_url: str | None = None
    text_count: int = 0
    audio_count: int = 0
    has_audio: bool = False
    has_video: bool = False
    created_at: str
    updated_at: str


class AnimaticMediaItem(BaseModel):
    """One uploaded image, ready to be added to the sequence."""

    upload_id: str
    filename: str = ""
    width: int = 0
    height: int = 0


class AnimaticUploadResponse(BaseModel):
    """Returned from POST /animatics/{id}/images — uploads in the order sent."""

    items: list[AnimaticMediaItem] = Field(default_factory=list)
    # Files that couldn't be read, so the UI can name them instead of silently
    # dropping them from a 40-file drag-and-drop.
    rejected: list[str] = Field(default_factory=list)


class AnimaticAudioResponse(BaseModel):
    """Returned from POST /animatics/{id}/audio."""

    upload_id: str
    filename: str = ""
    url: str


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


