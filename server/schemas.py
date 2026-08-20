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
    # Work in progress that has NOT been submitted yet — currently a storyboard
    # sitting on the review step. It exists so the reviewed shot list, cast,
    # assets and world edits are backed by the database instead of living only
    # in the browser, where a refresh destroyed them (and the breakdown that
    # produced them had already cost AI quota).
    # A draft is deliberately hidden from the storyboard library: it isn't a
    # board yet. It is promoted to QUEUED when the user hits Generate.
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobKind(str, Enum):
    GENERATE = "generate"      # full asset-generation pipeline run
    MESHY = "meshy"            # 3D submission for an already-generated character
    STORYBOARD = "storyboard"  # script → storyboard panel generation
    ANIMATIC = "animatic"      # timed image sequence + audio → video
    # "Plan & Script": the conversation with the content-planning agent plus the
    # calendar it produced. A new workflow needs no storage code of its own —
    # adding the kind is the whole job. See the Storage rule in AGENTS.md.
    PLAN = "plan"              # content plan / script planning session
    # "Animatics to Final Video": per-shot Veo renders, then one assembled cut.
    FINAL_VIDEO = "final_video"


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
    # Only used to name the DRAFT job this breakdown is saved as, so a resumed
    # draft isn't called "Storyboard". Falls back to the script's opening words.
    title: str | None = Field(None, description="Optional title for the saved draft.")


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
    # How the quote matched: "exact" (copied word for word), "fuzzy" (only its
    # head and tail were findable — the middle was rebuilt from the real text),
    # or "" (never found). Diagnostic only; nothing is drawn from it.
    script_line_match: str = ""


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


# ---------------------------------------------------------------------------
# Plan & Script
# ---------------------------------------------------------------------------
class PlanOption(BaseModel):
    """One clickable answer to a planning question."""

    label: str = ""
    description: str = ""


class PlanQuestion(BaseModel):
    """A question the agent asks with pickable answers.

    Rendered above the chat box as tabs + options, so the creator clicks an
    answer instead of writing a paragraph. `header` is the short tab label.
    """

    id: str = ""
    header: str = ""
    question: str = ""
    options: list[PlanOption] = Field(default_factory=list)


class PlanMessage(BaseModel):
    """One turn of the conversation with the planning agent."""

    # "user" or "agent" — stored in the client's vocabulary, not the SDK's, so
    # the transcript stays readable in the database.
    role: str = "user"
    text: str = ""
    at: str = ""
    # Only ever set on an agent turn, and empty when it isn't asking anything.
    # Stored with the message so the panel survives a refresh and so an OLD
    # question can't be mistaken for the live one.
    questions: list[PlanQuestion] = Field(default_factory=list)


class PlanCreateRequest(BaseModel):
    title: str | None = Field(None, max_length=120)


class PlanRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class PlanChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class PlanChannelRequest(BaseModel):
    """A YouTube channel link, handle, or bare channel name."""

    url: str = Field(..., min_length=1, max_length=400)


class PlanGenerateRequest(BaseModel):
    months: int = Field(1, ge=1, le=12, description="How many months to cover.")
    cadence: str | None = Field(
        None, max_length=120, description="How often they publish, in their own words."
    )
    # A creator writes titles in the language their audience speaks, so the plan
    # is written in whatever they choose. "english" | "hinglish" | "hindi", or
    # any language name they type — see plan_agent.LANGUAGES.
    language: str | None = Field(
        None, max_length=60, description="Language to write the plan in."
    )


class PlanSummary(BaseModel):
    """A row in the planning-session library."""

    job_id: str
    title: str
    message_count: int = 0
    item_count: int = 0
    months: int = 0
    channel_title: str = ""
    created_at: str = ""
    updated_at: str = ""


class PlanDetail(BaseModel):
    """A whole planning session.

    `channel` and `plan` are free-form dicts on purpose: they are produced by
    youtube_research and plan_agent, which own their own shapes. Pinning them
    here would mean changing three files to add one field.
    """

    job_id: str
    title: str
    messages: list[PlanMessage] = Field(default_factory=list)
    channel: dict = Field(default_factory=dict)
    plan: dict = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class ScriptDraft(BaseModel):
    """The script the user is currently writing, autosaved (see drafts.py).

    All fields default, so "nothing saved yet" is an empty draft rather than a
    404 the client has to special-case.
    """

    text: str = ""
    title: str = ""
    # ISO-8601, set server-side on every save. Empty when never saved.
    updated_at: str = ""


class ScriptDraftUpdate(BaseModel):
    """Body for PUT /scripts/draft — overwrites the caller's draft."""

    text: str = ""
    title: str = ""


class WeakDescription(BaseModel):
    """A shot whose description shares almost no wording with the script."""

    scene_number: int | None = None
    shot_number: int | None = None
    # 0..1 — share of the description's content words found in the script.
    overlap: float = 0.0


class Grounding(BaseModel):
    """How much of the breakdown the script actually supports.

    Built by `script_breakdown.build_grounding_report`. Advisory: nothing here
    blocks a storyboard, it tells the writer which panels to check. `warnings`
    is the human-readable summary; the counts are for logs and debugging.
    """

    shots_total: int = 0
    quotes_exact: int = 0
    quotes_fuzzy: int = 0
    quotes_missing: int = 0
    # (exact + fuzzy) / total — the headline "is it hallucinating" number.
    quote_rate: float = 0.0
    weak_descriptions: list[WeakDescription] = Field(default_factory=list)
    unknown_characters: list[str] = Field(default_factory=list)
    unknown_assets: list[str] = Field(default_factory=list)
    uncast_shot_characters: list[str] = Field(default_factory=list)
    unlisted_shot_assets: list[str] = Field(default_factory=list)
    uncast_speakers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    # Hallucination report for this breakdown — see Grounding.
    grounding: Grounding = Field(default_factory=Grounding)
    # Id of the DRAFT job this breakdown was saved as. The review step PATCHes
    # its edits back to it, so a refresh no longer throws the work away. None
    # when the draft couldn't be stored — the client then behaves as before.
    draft_job_id: str | None = None


class StoryboardDraft(BaseModel):
    """A storyboard being reviewed but not yet generated.

    Everything the review step holds, so a refresh (or a different machine) can
    pick up exactly where the user left off. `job_id` is None when there is no
    draft to resume — an absent draft is a normal state, not an error.
    """

    job_id: str | None = None
    title: str = ""
    style: str | None = None
    aspect_ratio: str | None = None
    genre: str | None = None
    script: str = ""
    shots: list[Shot] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    world: World = Field(default_factory=World)
    # name → reference_id for cast and props chosen on the reference steps, so a
    # resumed draft keeps the references already generated (they cost quota).
    character_refs: dict[str, str] = Field(default_factory=dict)
    asset_refs: dict[str, str] = Field(default_factory=dict)
    asset_categories: dict[str, str] = Field(default_factory=dict)
    updated_at: str = ""


class StoryboardDraftUpdate(BaseModel):
    """Body for PATCH /storyboards/draft/{job_id}.

    Every field is optional: the review step saves whatever the user just
    changed, and omitted fields are left as they are.
    """

    title: str | None = None
    style: str | None = None
    aspect_ratio: str | None = None
    genre: str | None = None
    shots: list[Shot] | None = None
    characters: list[Character] | None = None
    assets: list[Asset] | None = None
    world: World | None = None
    character_refs: dict[str, str] | None = None
    asset_refs: dict[str, str] | None = None
    asset_categories: dict[str, str] | None = None


class StoryboardCreateRequest(BaseModel):
    """Body for POST /storyboards — generate panels from a reviewed shot list."""

    shots: list[Shot] = Field(..., min_length=1, description="The reviewed shots to draw.")
    style: str = Field("custom", description="Visual style id, e.g. 'sketch', 'comics'.")
    aspect_ratio: str = Field("16:9", description="Panel aspect ratio, e.g. '16:9'.")
    title: str | None = Field(None, description="Optional storyboard title.")
    # Stored (not used for drawing) so the library can label a saved board.
    genre: str | None = Field(None, description="Genre chosen on the form, for the library card.")
    # THE WRITTEN CONTINUITY BIBLE. The reviewed cast and asset lists, each with
    # the visual description the breakdown wrote. Every panel is told what the
    # people and props IN IT look like, which is what stops the same character
    # being drawn as a different person in shot 7.
    #
    # This matters most where there are NO reference images: the rough-sketch
    # style skips the cast step by design, so `character_refs` is empty and the
    # bible is the only thing holding the film together. It costs nothing — the
    # breakdown already produced these words — and both lists are optional, so
    # an older client that omits them keeps working exactly as before.
    characters: list[Character] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    # Character consistency (Stage B): map character name → reference_id (from
    # POST /characters/reference). Those refs are fed into every panel the
    # character appears in, so they stay visually consistent.
    character_refs: dict[str, str] = Field(default_factory=dict)
    # Asset consistency (Stage B2): map asset name → reference_id (from
    # POST /assets/reference). Those prop/background refs are fed into every panel
    # the asset appears in, so props and locations stay visually consistent.
    asset_refs: dict[str, str] = Field(default_factory=dict)
    # The DRAFT this board was reviewed as (from POST /storyboards/breakdown).
    # When present and still a draft, that record is PROMOTED into the board
    # instead of a second job being created. Omit it and a new job is made, as
    # before — so an older client keeps working.
    draft_job_id: str | None = None
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


class PanelSequenceRequest(BaseModel):
    """Body for POST /storyboards/{job_id}/panels/{index}/sequence.

    Turns ONE drawn panel into the key poses for a shot of `duration_seconds`.
    The COUNT is not sent: it is derived from the duration server-side
    (panel_sequence.frame_count_for), so the client can't ask for hundreds.
    """

    duration_seconds: int = Field(
        4, description="Shot length: 2, 4, 6, 8 or 10 seconds."
    )
    # Continue a stopped run instead of starting again. Frames already on disk
    # are kept and only the missing ones are drawn — a stop then Generate costs
    # only what was never drawn.
    resume: bool = True
    # PREVIEW: draw only the first couple of poses, then stop, so the user can
    # see whether the character is actually moving before paying for the rest.
    # A 10s shot is 40 drawings; finding out it didn't work should not cost 40.
    # Continuing afterwards is just an ordinary resume — nothing is redrawn.
    preview: bool = False
    # REDO these exact poses, even though they already exist — the per-drawing
    # "this one came out wrong" button. Takes precedence over resume/preview:
    # only these are drawn, and they reuse the sequence's stored pose plan so
    # pose 7 is redrawn as the same pose 7. Fixing one bad drawing in sixteen
    # costs one image.
    redraw: list[int] = Field(default_factory=list)


class PanelSequenceInfo(BaseModel):
    """What a panel's key-pose sequence currently is."""

    index: int
    frames: int = 0
    planned: int = 0
    duration_seconds: int = 0
    fps: int = 24
    stopped: bool = False
    failed: list[int] = Field(default_factory=list)
    # The planned poses that have NO picture — holes, wherever they fall. A
    # sequence is not "the frames up to the first gap": one refused drawing in
    # the middle used to hide every good one after it. Pressing Generate again
    # fills exactly these.
    missing: list[int] = Field(default_factory=list)
    # Serve paths for the frames that exist, in order. Filled on read.
    urls: list[str] = Field(default_factory=list)
    # Which pose each url IS (`urls[k]` is pose `frame_numbers[k]`), so the
    # strip can label drawing 11 as 11 even when 10 is missing.
    frame_numbers: list[int] = Field(default_factory=list)
    # What each pose was PLANNED to show, in pose order. Lets the strip say what
    # a drawing was meant to be, which is what makes "this one is wrong"
    # judgeable rather than a hunch. Pose 1 is always the panel itself — it is
    # copied in rather than drawn, so the sequence opens on the picture already
    # approved on the board.
    poses: list[str] = Field(default_factory=list)
    # The shot's INVARIANT: one sentence naming what is true in every drawing —
    # "Kabir stays asleep under the quilt, he never wakes or sits up". Handed to
    # every drawing and kept here so a later single-pose redraw is fenced by the
    # same rule the rest of the shot was drawn under.
    hold: str = ""


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


class AnimaticKeyframe(BaseModel):
    """One point on one property's animation curve.

    `t` is RELATIVE to the clip's own start, which is what lets a clip be
    dragged along the timeline without its animation sliding out from under it.
    It may be negative, or run past the clip's end: the value simply HOLDS
    outside the first and last key rather than extrapolating, so trimming a clip
    longer can never fling it off screen.

    The curve named by `ease` governs the span from THIS key to the next one —
    the last key's ease is therefore unused. "hold" makes the property step
    rather than glide.

    ⚠ The interpolation these describe is implemented TWICE, in
    `animatic_render.py` and `client/src/animatic/scene.js`, so that the preview
    and the export agree. `tests/render_parity.py` is what keeps them honest.
    """

    t: int = Field(..., description="Milliseconds from the clip's own start. May be negative.")
    v: float
    # Not constrained to the known set on purpose: both evaluators fall back to
    # "linear" for a curve they don't recognise, so a project written by a newer
    # client with a curve this server hasn't heard of still opens and still
    # plays. Rejecting it would lose the whole animatic over one word.
    ease: str = Field(
        "linear", description="'linear' | 'hold' | 'ease-in' | 'ease-out' | 'ease-in-out'."
    )


# Every animatable clip carries the same field, so it is declared once. An
# animatic saved before keyframes existed has no such key, which reads as an
# empty dict and animates nothing — those projects export exactly as they did.
_KEYFRAMES = Field(
    default_factory=dict,
    description="Animation curves by property name, e.g. {'opacity': [{'t':0,'v':0}, …]}.",
)


class AnimaticEffect(BaseModel):
    """One step in a clip's colour chain — a grade, a LUT, a chroma key.

    ORDER MATTERS and is the order of the list: a LUT after a saturation pull is
    a different picture from a saturation pull after a LUT. The editor moves
    effects up and down for exactly that reason.

    `kind` is NOT constrained to the known set, following the same rule as
    `ease`, a clip's `kind` and a transition's: both evaluators simply skip a
    kind they don't recognise, so a project written by a newer client opens,
    plays and SAVES BACK with its unknown effect intact rather than being lost
    over one word.

    `params` is left as a free dict for the same reason. Every parameter is
    filled from `EFFECT_PARAMS` when it is read (`effect_params` in
    `animatic_render.py`), so a project saved before a parameter existed picks
    up its default instead of failing validation.

    ⚠ A NUMERIC PARAMETER IS KEYFRAMABLE, and its keyframe track lives in the
    CLIP's own `keyframes` under the flat name `fx:<id>:<param>` — which is why
    `id` has to be stable and unique within the clip.
    """

    id: str = Field(..., description="Stable client-side id, unique within the clip.")
    kind: str = Field(
        ...,
        description="'brightness' | 'contrast' | 'saturation' | 'lut' | 'chroma'.",
    )
    params: dict[str, float | str] = Field(default_factory=dict)


class AnimaticMask(BaseModel):
    """The one region of the FRAME a clip is allowed to paint into.

    Geometry is fractions of the frame with `x`/`y` the CENTRE, matching a
    shape, an overlay and a picture's pan — it is dragged with the same handles.
    In frame coordinates rather than the source picture's, because a vignette or
    a spotlight is a region of the film you are making; that is also what lets
    one be keyframed to sweep across a shot while the shot pans underneath.

    `feather` is in the mask's OWN units (1.0 = half its width), so a small mask
    gets a proportionally small softness and the edge doesn't change hardness
    when the mask is animated bigger.

    kind "none" is the default and means no mask at all — every animatic saved
    before this one has it, and is unaffected.
    """

    kind: str = Field("none", description="'none' | 'rect' | 'ellipse'.")
    x: float = Field(0.5, ge=-1.0, le=2.0)
    y: float = Field(0.5, ge=-1.0, le=2.0)
    w: float = Field(0.5, gt=0.0, le=4.0)
    h: float = Field(0.5, gt=0.0, le=4.0)
    feather: float = Field(0.1, ge=0.0, le=1.0)
    # Keeps everything OUTSIDE the region instead — how you punch a hole rather
    # than cut one out. Not keyframable: a half-inverted mask is not a picture.
    invert: bool = False


# The three fields that make up a clip's LOOK. Declared once because a frame and
# an overlay carry exactly the same three — they are the two clips that are
# PICTURES, and the only two with pixels of their own to grade. A shape is
# vector and a caption is text; both are drawn above the finished composite.
#
# All three are optional with defaults that reproduce today's picture exactly:
# no effects, no mask, normal blend. An animatic saved before this opens, plays
# and exports unchanged, which `tests/effects_check.py` asserts on an
# old-shaped payload rather than leaving to inspection.
_EFFECTS = Field(default_factory=list, description="Colour chain, applied in order.")
_MASK = Field(default_factory=lambda: AnimaticMask(), description="The clip's mask.")
_BLEND = Field(
    "normal",
    description=(
        "How this clip composites onto what is under it: 'normal' | 'multiply' "
        "| 'screen' | 'overlay' | 'add' | 'darken' | 'lighten'."
    ),
)


class AnimaticFrameSource(BaseModel):
    """Where one animatic frame's picture comes from.

    A storyboard panel is referenced BY INDEX and never copied, so re-drawing
    that panel on the board updates the animatic too. An upload is a file the
    user dropped into this animatic, stored under its own id.
    """

    kind: str = Field(
        "panel",
        description=(
            "'panel' (a storyboard panel), 'pose' (one key pose of a panel's "
            "sequence), 'upload' (an uploaded still), or 'video' (an uploaded "
            "video file)."
        ),
    )
    storyboard_id: str | None = Field(None, description="Board job id (kind='panel'/'pose').")
    index: int | None = Field(None, description="Panel index on that board (kind='panel'/'pose').")
    # WHICH KEY POSE. A shot that has been through Image to Animatic Image has
    # ~4 drawings per second of screen time; referencing them individually is
    # what makes an animatic actually MOVE instead of being a slideshow of
    # stills. Referenced, never copied — redrawing the pose updates the animatic.
    frame: int | None = Field(None, description="Key-pose number within that panel's sequence (kind='pose').")
    # One field for both upload kinds. A video and a still are stored side by
    # side under the same id space, so the only thing that differs is which
    # file extension is on disk — see `_video_file` / `_image_path`.
    upload_id: str | None = Field(
        None, description="Uploaded image id (kind='upload') or video id (kind='video')."
    )


class AnimaticAsset(BaseModel):
    """ONE ITEM IN THE MEDIA LIBRARY — a source, with no place in the cut.

    ⚠ THE LIBRARY AND THE TIMELINE ARE TWO DIFFERENT LISTS NOW, and this class is
    the whole of that change. The Media pane used to BE the timeline, grouped by
    where each clip came from: it listed `frames`, so deleting a clip deleted the
    only record that the file had ever been added. Reported as "when i upload /
    generate Veo video and then i delete in time so i see in media panel also
    delete … i want when user delete video, storboard image, veo video, audio and
    shapes in timeline after upload in media so only clip delete in timeline not
    delete in media panel".

    So: an ASSET is what you have, a clip is where you put it. Deleting a clip
    leaves the asset; dragging the asset out makes a new clip; the asset only goes
    when its own ✕ is pressed.

    ⚠ IT CARRIES NO TIMING AND NO PLACE. No `start_ms`, no `track`, no `in_ms`,
    no effects — every one of those is a property of a CLIP, and an asset that
    carried them would be a second, competing answer to "where does this play?".
    `duration_ms` here is the source's NATURAL length (0 = unknown), used to open
    a new clip at the right size and to print "54.4s" on the card.

    ⚠ AND IT IS A REFERENCE, exactly as a frame is. A board panel asset holds the
    board id and the index, so re-drawing that panel updates the library card and
    every clip made from it; nothing is ever copied.
    """

    id: str = Field(..., description="Stable client-side id.")
    # What a clip made from this asset IS — the same vocabulary as
    # `AnimaticFrame.kind`, plus "audio" for a sound file, which becomes an audio
    # track rather than a picture clip.
    kind: str = Field(
        "image",
        description="'image' | 'video' | 'color' | 'audio' — what a clip made from this is.",
    )
    # Where the picture comes from. Unused by an audio asset (see `upload_id`) and
    # by a colour card (see `color`), both of which still carry a default so the
    # shape is one shape.
    src: AnimaticFrameSource = Field(default_factory=AnimaticFrameSource)
    # An audio asset's file. Audio has no `AnimaticFrameSource` — an audio track
    # references its upload directly — so this is the one field that is not
    # reachable through `src`.
    upload_id: str = ""
    label: str = ""
    # The SOURCE's natural length, not a clip's hold. 0 = we could not measure it,
    # in which case a new clip opens at the default hold.
    duration_ms: int = Field(0, ge=0, le=24 * 3_600_000)
    color: str = "#000000"
    # Filled on READ, never stored — same rule as `AnimaticFrame.url` and for the
    # same reason: the path is resolved per request, so a saved one goes stale.
    #
    # ⚠ THE CLIENT CAN BUILD EVERY ONE OF THESE ITSELF, and that is deliberate.
    # An upload is served by upload id and a panel by (board, index) — neither
    # needs the asset to be ON the saved project — so a freshly imported library
    # card has a picture before the autosave has run. That is the bug the
    # storyboard import hit from the other direction; see `doBoardImport`.
    url: str | None = None


class AnimaticFrame(BaseModel):
    """ONE PICTURE CLIP: where it sits, which track it is on, how long it holds.

    Called a "frame" throughout for history's sake — it started life as one held
    picture. It is now a clip that may be a still, a piece of a video file, or a
    flat colour card, and `kind` says which.

    ⚠ `duration_ms` IS THE LENGTH ON THE TIMELINE, for every kind. For a video
    clip, `in_ms`/`out_ms` say which part of the SOURCE is read and `speed` how
    fast it is read through — none of which changes how much timeline the clip
    occupies. See `source_at` in `animatic_render.py` for why it is that way
    round rather than speed re-timing the clip.
    """

    id: str = Field(..., description="Stable client-side id (survives reordering).")
    src: AnimaticFrameSource
    # 0.1s–10min. The whole point of the editor is that this is per-frame.
    duration_ms: int = Field(2000, ge=100, le=600_000)
    # --- Where it sits ------------------------------------------------------
    # ⚠ THE PICTURE TRACK IS A STACK NOW, AND THESE TWO FIELDS ARE THE WHOLE OF
    # IT. The picture used to be ONE sequence laid end to end: a clip's place was
    # the sum of the clips before it, so trimming any clip moved every clip after
    # it — reported as "when i do video trim so i see my image layer conetnt move
    # like snip", and true by construction rather than by accident.
    #
    # `track` — 0 is the base track, the bottom of the stack. A HIGHER NUMBER IS
    # DRAWN OVER A LOWER ONE, so a gap on an upper track shows whatever is on the
    # track below it and a moment with nothing on any track shows
    # `settings.background`. See `frame_track` / `frameSpans`.
    #
    # `start_ms` — where the clip sits on the timeline. ⚠ `None` MEANS "AFTER THE
    # LAST CLIP ON MY TRACK", and that is the compatibility hinge: every animatic
    # saved before tracks existed carries no starts at all, so the evaluators lay
    # it out exactly as the old running total did, at every cut, and nothing about
    # such a project renders differently. It is also what makes "add these
    # pictures to the end of the sequence" a write of nothing rather than
    # arithmetic the client would have to keep in step with the server.
    #
    # Both are read through the evaluators and never trusted raw — a negative
    # start, a fractional track or a missing field all fold down there, in ONE
    # place, so the preview and the export cannot fold differently.
    track: int = Field(0, ge=0, le=15, description="Picture track; 0 is the base, higher draws over.")
    start_ms: int | None = Field(
        None,
        ge=0,
        le=24 * 3_600_000,
        description="Where it sits on the timeline. None = after the last clip on its track.",
    )
    label: str = Field("", description="Caption, e.g. 'Shot 3'. Burned in only if show_labels.")
    # What this clip is made of. Optional with an "image" default, so every
    # animatic saved before video clips existed opens as exactly what it was.
    # Not constrained to the known set on purpose — same rule as `ease` and a
    # transition's `kind`: both evaluators fold an unrecognised kind down to
    # "image", so a project written by a newer client still opens and still
    # plays rather than being lost over one word.
    kind: str = Field("image", description="'image' | 'video' | 'color'.")
    # --- Video clips only ---------------------------------------------------
    # The SOURCE window. `in_ms` is where in the file the clip starts; `out_ms`
    # is where it stops, exclusive, and None means "to the end of the file".
    # Past `out_ms` the clip holds its last source frame rather than going black.
    in_ms: int = Field(0, ge=0, le=24 * 3_600_000)
    out_ms: int | None = Field(None, ge=1, le=24 * 3_600_000)
    # How fast the source is read: 2.0 covers twice as much footage in the same
    # stretch of timeline, 0.5 half as much. It does NOT change `duration_ms`,
    # which is what keeps every later cut, caption and transition where it was.
    speed: float = Field(1.0, gt=0.0, le=10.0)
    # --- Colour cards only --------------------------------------------------
    color: str = Field("#000000", description="Fill colour for kind='color', #rrggbb.")
    # The picture's OWN pan and zoom, on top of `settings.fit`. Keyframe `scale`
    # and `x`/`y` and you have a Ken Burns push — which is the move that makes a
    # held storyboard panel read as a shot rather than a slide. `x`/`y` are the
    # picture's CENTRE as a fraction of the canvas, matching every other
    # geometry here; the defaults are an identity transform, so a frame that
    # never touches them is placed exactly as it always was.
    scale: float = Field(1.0, gt=0.0, le=10.0)
    x: float = Field(0.5, ge=-2.0, le=3.0)
    y: float = Field(0.5, ge=-2.0, le=3.0)
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    # --- The LOOK ----------------------------------------------------------
    effects: list[AnimaticEffect] = _EFFECTS
    mask: AnimaticMask = _MASK
    blend: str = _BLEND
    keyframes: dict[str, list[AnimaticKeyframe]] = _KEYFRAMES
    # Filled by the server on read so the client has ONE url shape for both
    # source kinds. Ignored on write.
    url: str | None = None


class AnimaticAudio(BaseModel):
    """One audio CLIP laid under the sequence.

    An animatic can carry several — music under a voiceover is the usual pair —
    and they are MIXED on export, each at its own volume.

    ⚠ A CLIP, NOT A FILE. Since the razor learned to cut audio, several entries
    can share one `upload_id`: cutting a track in half leaves two clips reading
    two different windows of the same upload, and neither is "the track". So the
    identity of an entry is `id`, and `upload_id` says only which file it reads.
    Everything the editor keys per clip — the selection, its <audio> element, a
    patch, a mute — keys on `id`; everything that is about the FILE (the blob
    url, the decoded waveform, a transcript) keys on `upload_id`.
    """

    # The clip's own identity. "" on every animatic saved before the razor could
    # cut audio; `_audio_tracks_of` fills those in with the `upload_id`, which is
    # unique in exactly the projects that predate this and keeps their selection
    # and element keys byte-for-byte what they were.
    id: str = ""
    upload_id: str
    # The lane this track sits on. "" = a lane of its own, which is how every
    # track written before layers existed is laid out.
    layer_id: str = ""
    # Selected, moved and deleted with whatever shares it — see the field's
    # canonical comment on `AnimaticTextClip`. ⚠ Per CLIP, like everything else
    # keyed by `id`: cutting a track leaves two clips and only the piece you
    # grouped is in the group.
    group_id: str = ""
    filename: str = ""
    # Measured in the BROWSER (decodeAudioData) and sent up — the server has no
    # audio decoder of its own, and ffmpeg doesn't need to be told.
    duration_ms: int = 0
    # WHERE ON THE TIMELINE this clip begins. 0 is every animatic saved before
    # the razor could cut audio — a track used to be pinned to the head of the
    # video and could only be trimmed at its ends, which is exactly why a gap in
    # the middle of a take could not be cut out.
    #
    # ⚠ Do not confuse it with `offset_ms`. This one moves the clip along the
    # TIMELINE; `offset_ms` moves the playhead INSIDE the file. A cut in the
    # middle sets both on the second half: it starts later on the timeline and
    # further into the file, by the same amount.
    start_ms: int = Field(0, ge=0)
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
    # --- Shape: how the track comes in and goes out -------------------------
    # Milliseconds of ramp at each end of what the track PLAYS — so a fade out
    # lands on the end of the TRIM, or on the end of the video if that comes
    # first, rather than on the end of the file. 0 is a hard start / stop, which
    # is every track saved before this existed. Both are applied by `afade` on
    # export and by the same window in the editor; see `fade_window`.
    fade_in_ms: int = Field(0, ge=0, le=60_000)
    fade_out_ms: int = Field(0, ge=0, le=60_000)
    # WHICH CURVE each of those two ramps follows — Premiere's three crossfades,
    # which are three curves and not three mechanisms. A crossfade between two
    # clips IS the outgoing one's fade out overlapping the incoming one's fade
    # in, so there is no transition object here to go with `AnimaticTransition`:
    # the pair of fades is the transition, and the export already mixes whatever
    # overlaps.
    #
    # ⚠ PER END, NOT PER CLIP. A crossfade writes one end of one clip and the
    # opposite end of its neighbour, so one curve per clip would mean the second
    # crossfade you laid changed the shape of the first.
    #
    # ⚠ Unconstrained on purpose, exactly like `AnimaticTransition.kind`: both
    # sides fold an unknown curve down to "linear", so a project saved by a newer
    # client opens here instead of failing validation. And "linear" is `afade`'s
    # own default (`curve=tri`), which is what every fade in every project saved
    # before this field already was — so nothing needed migrating.
    fade_in_curve: str = Field(
        "linear",
        description="'linear' (Constant Gain) | 'power' (Constant Power) | 'exponential'.",
    )
    fade_out_curve: str = Field(
        "linear",
        description="'linear' (Constant Gain) | 'power' (Constant Power) | 'exponential'.",
    )
    # --- Tone ---------------------------------------------------------------
    # Three fixed bands, in dB, 0 = untouched. FIXED rather than parametric so
    # that each band is exactly one biquad in the browser and one filter in
    # ffmpeg — see `EQ_BANDS` in animatic.py for why that is the whole design.
    # ±12 dB is a mix control, not a repair tool: past that you want a better
    # recording, not a bigger number.
    eq_low: float = Field(0.0, ge=-12.0, le=12.0)
    eq_mid: float = Field(0.0, ge=-12.0, le=12.0)
    eq_high: float = Field(0.0, ge=-12.0, le=12.0)
    # --- The mix: which track is which, and what ducks under what -----------
    # STATED, never inferred. A duck has to know which track is the voice, and
    # "the other one" is wrong the first time someone lays two music beds. ""
    # is every track saved before this and ducks nothing.
    role: str = Field("", description="'' | 'voice' | 'music'.")
    # How far this track is pulled down WHILE THE VOICE IS TALKING, as a gain:
    # 1.0 = never (the default), 0.3 ≈ −10 dB. It is a compressor keyed off the
    # voice, so the depth is what the voice asks for around this figure rather
    # than a fixed step — see `duck_ratio` in animatic.py.
    duck_to: float = Field(1.0, ge=0.05, le=1.0)
    # WHICH voice opens the duck, by `upload_id`. "" = the first track whose
    # role is "voice", which is the only sensible reading when there is one.
    duck_target: str = ""
    url: str | None = None


class AnimaticLayer(BaseModel):
    """One lane the USER added to the timeline, empty until they fill it.

    "Add a layer" makes one of these and nothing else — no upload dialog, no
    clip. Content is then added INTO a lane, so several captions or shapes can
    be organised on separate rows instead of piling into one.

    Every kind also has an implicit DEFAULT lane (clips with `layer_id == ""`),
    which is what every animatic saved before layers existed is made of.

    ⚠ A PICTURE TRACK IS ONE OF THESE NOW TOO, and it is the odd one out: its
    clips do NOT point at it by `layer_id`. A picture clip says which row it is on
    with a NUMBER (`AnimaticFrame.track`), because that number is also the
    compositing order — higher draws over lower — and the export reads it
    directly. So a `kind: "video"` layer exists to say **"this row exists, and it
    is called this"**, which is the one thing the number cannot say: an EMPTY row
    has no clips to carry it.

    That was the bug. A row you added and had not filled yet was pure view state,
    so it vanished on reload — "when i see again my video picker layer not show".
    A row is a record now, so it survives, and its ✕ can remove it like any other
    layer's.
    """

    id: str
    kind: str = Field(
        ...,
        description=(
            "A PICTURE TRACK: 'board_image' | 'board_video' | 'stills' | 'video'. "
            "Or an overlay/timed row: 'image' | 'text' | 'shape' | 'audio'. "
            "'image' is pictures composited OVER the cut; the four picture kinds "
            "are IN it."
        ),
    )
    name: str = ""
    # WHICH picture track this row is — the four picture kinds only, None
    # everywhere else. Not an id, because `AnimaticFrame.track` is a number and
    # the two have to agree; this is the record for that number, not a second way
    # of naming it.
    #
    # ⚠ SAME CAP AS `AnimaticFrame.track` (le=15). A row numbered higher than a
    # clip's track allows is a row no clip could ever be put on, and
    # `tests/frame_save_fields_check.py` compares the two.
    track: int | None = Field(
        None,
        ge=0,
        le=15,
        description="For a picture-track kind: which track this row is.",
    )


class AnimaticOverlay(BaseModel):
    """A picture composited OVER the sequence — a logo, an inset, a cut-in.

    Geometry matches AnimaticShape exactly (fractions of the frame, `x`/`y` the
    centre) because it is placed the same way and by the same drag handles; the
    only difference is that the fill is an uploaded image rather than a colour.
    """

    id: str
    layer_id: str = ""
    # Selected, moved and deleted with whatever shares it — see the field's
    # canonical comment on `AnimaticTextClip`.
    group_id: str = ""
    upload_id: str
    start_ms: int = Field(0, ge=0)
    duration_ms: int = Field(2000, ge=100, le=600_000)
    x: float = Field(0.5, ge=-1.0, le=2.0)
    y: float = Field(0.5, ge=-1.0, le=2.0)
    w: float = Field(0.3, gt=0.0, le=4.0)
    h: float = Field(0.3, gt=0.0, le=4.0)
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    rotation: float = Field(0.0, ge=-360.0, le=360.0)
    # --- The LOOK, identical to a frame's ----------------------------------
    # `blend` earns its keep most here: an overlay is the one clip that sits ON
    # something, so "screen this flare over the shot" is the whole reason the
    # mode exists.
    effects: list[AnimaticEffect] = _EFFECTS
    mask: AnimaticMask = _MASK
    blend: str = _BLEND
    keyframes: dict[str, list[AnimaticKeyframe]] = _KEYFRAMES
    # Filled by the server on read, like a frame's. Ignored on write.
    url: str | None = None

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


class AnimaticTextClip(BaseModel):
    """One piece of on-screen text, with its OWN start and length.

    Deliberately time-based rather than attached to a frame: a caption often has
    to appear part-way through a held image, or run across a cut. It lives on its
    own timeline track for exactly that reason.
    """

    id: str
    # Which lane it sits on. "" is the default text lane — what every clip
    # written before layers existed belongs to.
    layer_id: str = ""
    # --- Grouping -----------------------------------------------------------
    # ⚠ THE CANONICAL DEFINITION. `AnimaticShape`, `AnimaticOverlay` and
    # `AnimaticAudio` carry the same field with the same meaning; read this one.
    #
    # Clips sharing a `group_id` are selected, moved and deleted as ONE thing.
    # "" is not in a group, which is every clip saved before this existed.
    #
    # A SHARED STRING ON THE MEMBERS, deliberately, rather than a group object
    # holding a list of ids. A container has to be kept in step with every
    # delete, split, duplicate and undo in the app, and one missed path leaves a
    # group pointing at a clip that no longer exists; a string on the clip
    # itself cannot go stale — delete a member and the group is whatever is
    # left. A group may span kinds (a caption and the shape behind it are
    # exactly the pair you want moving together), which is also why it is not a
    # field on the LAYER: grouping is not "same row", it is "same thing".
    #
    # ⚠ EDITOR-ONLY, and the renderer must stay unaware of it: nothing about how
    # a clip DRAWS may depend on who it is grouped with, or the export would
    # change when you group something. See `client/src/animatic/selection.js`.
    group_id: str = ""
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
    # Fades the caption — its backdrop, ink and outline together. Keyframe it
    # and a caption arrives instead of appearing.
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    # --- The TYPE (Phase 5) -------------------------------------------------
    # Every field below is optional with a default that reproduces exactly what
    # this clip drew before Phase 5, so an animatic saved earlier opens, plays
    # and exports unchanged — asserted on an old-shaped payload by
    # `tests/captions_check.py` rather than left to inspection.
    #
    # WHICH FONT, by id from the bundled list — never a family name the machine
    # is asked to resolve. `animatic_fonts.py` and `client/src/animatic/fonts.js`
    # are the two halves of that list and the files ship in
    # `client/public/fonts/`; read either module's header for why a caption set
    # in a font the SERVER looked up by name is a caption that wraps differently
    # in the MP4 than in the monitor. An id this build doesn't know folds down
    # to the default, like every other unrecognised enum here.
    font: str = Field("inter", description="Bundled font id — see animatic_fonts.FONTS.")
    # HOW it is positioned. "flow" is the original behaviour and the default:
    # dropped into its `position` zone, stacking with other captions in that
    # zone so two never land on top of each other. "free" uses x/y instead.
    # Not animatable — half way between two layout algorithms is not a picture.
    place: str = Field("flow", description="'flow' (stacked in its zone) | 'free' (x/y).")
    # The caption's CENTRE as a fraction of the frame, used only when
    # place='free'. Matches a shape, an overlay and a picture's pan, because it
    # is dragged with the same handles. ⚠ ANIMATABLE, and the two properties the
    # in/out presets move: a title sliding up into place is two keys on `y`.
    # Defaults match TEXT_DEFAULTS in `animatic_render.py` and `scene.js`.
    x: float = Field(0.5, ge=-1.0, le=2.0)
    y: float = Field(0.85, ge=-1.0, le=2.0)
    # Outline around the glyphs, in pixels AT 1080p — scaled with the frame, so
    # the same project looks the same exported at 720p or 4K. 0 is no outline,
    # which is what every caption written before this has. (The "none" backdrop
    # still draws its own automatic outline: without a backdrop OR an outline,
    # white text on pale art is invisible, and that has always been true.)
    stroke_px: float = Field(0.0, ge=0.0, le=24.0)
    stroke_color: str = Field("#000000", description="Outline colour, #rrggbb.")
    # Drop shadow OFFSET, as a fraction of the font size — so it is one number
    # in both languages: `shadow`em down and right in CSS, `shadow * font_px` in
    # Pillow. 0 is no shadow. 0.06 is an ordinary one.
    shadow: float = Field(0.0, ge=0.0, le=0.5)
    # Extra space between glyphs, as a fraction of the font size (CSS `em`).
    # Negative tightens. 0 leaves the font's own metrics alone, and the drawing
    # code takes its original per-line path in that case, so an untouched
    # caption is rendered by exactly the code that rendered it before.
    letter_spacing: float = Field(0.0, ge=-0.2, le=1.0)
    keyframes: dict[str, list[AnimaticKeyframe]] = _KEYFRAMES

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
    layer_id: str = ""
    # Selected, moved and deleted with whatever shares it — see the field's
    # canonical comment on `AnimaticTextClip`.
    group_id: str = ""
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
    keyframes: dict[str, list[AnimaticKeyframe]] = _KEYFRAMES

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


class AnimaticTransition(BaseModel):
    """What happens ON one cut — a dissolve, a dip, a wipe, a reveal, a slide.

    Anchored to the frame it comes AFTER rather than to a time, so it rides
    along when that frame is re-timed, moved or trimmed, exactly as a keyframe
    does. One naming a frame that has been deleted — or the LAST frame, where
    there is nothing to cut to — is inert rather than invalid: it is skipped,
    kept, and works again if the sequence changes back.

    ⚠ BOUNDARY-LOCAL. The blend straddles the cut, taking `duration_ms`/2 from
    the tail of the outgoing picture and the same from the head of the incoming
    one. It takes NOTHING away from either, so the timeline is exactly as long
    with transitions as without and every existing timing rule survives. The
    reasoning is in `client/src/animatic/transitions.js`; the implementation is
    written twice, there and in `animatic_render.py`.
    """

    id: str = Field(..., description="Stable client-side id.")
    after_frame_id: str = Field(..., description="The frame this transition follows.")
    # Not constrained to the known set on purpose — same rule as `ease` on a
    # keyframe. Both evaluators fold an unrecognised kind down to "dissolve", so
    # a project written by a newer client still opens and still plays here.
    kind: str = Field(
        "dissolve",
        description=(
            "'dissolve' | 'dip' | 'slide', or one of the REVEALS — 'wipe', "
            "'diagonal', 'split', 'radial', 'diamond', 'box', 'angular', "
            "'blinds', 'checker'. Every reveal is the same code path: a shape "
            "multiplied into the arriving picture's alpha. See MATTE_KINDS in "
            "animatic_render.py."
        ),
    )
    # HOW that kind behaves — which way a wipe or a slide travels, which colour
    # a dip goes out through, how soft a reveal's edge is and how many bands a
    # blinds wipe has. A free dict for exactly the reason `kind` is
    # unconstrained and `AnimaticEffect.params` is free: every parameter is
    # filled from `TRANSITION_PARAMS` when it is read (`transition_params` in
    # `animatic_render.py`), so a project saved before a parameter existed picks
    # up its default instead of failing validation, and one saved by a NEWER
    # client keeps the parameter it wrote rather than losing it on a round trip.
    # No migration, and nothing to write for the animatics that already exist.
    params: dict[str, float | str] = Field(default_factory=dict)
    # Clamped again at render time to the SHORTER of the two holds it joins, so
    # a transition can never eat more than half of either neighbour — which is
    # what stops two of them overlapping on a short picture.
    duration_ms: int = Field(600, ge=100, le=10_000)


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
    # --- Export presets (Phase 8) ------------------------------------------
    # ⚠ ALL THREE DEFAULT TO WHAT THIS ALWAYS DID: no preset, an MP4, and a
    # still taken from the head of the timeline. Every animatic saved before
    # presets existed therefore opens and exports byte for byte as it did.
    #
    # Which named preset the settings currently ARE — see `export_presets.py`.
    # Stored rather than derived so the dialog reopens on the choice that was
    # made, and deliberately NOT authoritative: the fields below are what the
    # encoder reads, so a preset that has since been edited by hand simply
    # stops matching and the dialog says "Custom".
    preset: str = Field("", description="'youtube' | 'tiktok' | 'reels' | 'gif' | 'still' | ''.")
    # What FILE to write. Not constrained to the known set on purpose — same
    # rule as a transition `kind` — because `normalise_container` folds anything
    # it doesn't know down to mp4, so a project written by a newer client still
    # exports here instead of failing validation.
    container: str = Field("mp4", description="'mp4' | 'gif' | 'png'.")
    # Which moment a 'png' export is a picture of; ignored by the other two.
    # The editor sends the playhead, so "export a still" means the frame you are
    # looking at.
    still_ms: int = Field(0, ge=0)
    # --- Rows the user has switched off (the eye in the timeline's gutter) ---
    # ⚠ THE EYE HAS TO REACH THE ENCODER, which is why this is a project setting
    # and not something the browser remembers. A switch that dimmed the preview
    # and then exported the row anyway would be lying at the one moment it
    # matters.
    #
    # ⚠ IT NAMES A ROW, NOT ITS CLIPS: `"<kind>:<layer_id>"` for a lane of clips
    # ("text:" is the default text row, "shape:<id>" one the user added,
    # "image:<id>" an overlay row) and "frames:<n>" for picture track n (the
    # two rows the picture track is drawn as. Emptying a hidden row or adding to
    # it therefore changes nothing about what is switched off. The client writes
    # the same tokens — `laneToken` in AnimaticEditor.jsx — and `_lane_hidden`
    # here is what reads them back off a clip.
    #
    # Audio is NOT in here. A track has `muted`, which is this idea for a row you
    # hear rather than see; two switches for one idea would be worse than either.
    #
    # Empty on every animatic that predates it, which then exports exactly as it
    # always did.
    hidden_lanes: list[str] = Field(default_factory=list)
    # WHICH LANES ARE LOCKED, in the same token vocabulary as `hidden_lanes`
    # (`laneToken` on the client writes both).
    #
    # ⚠ A LOCK IS EDITING ONLY, AND THAT IS THE DIFFERENCE FROM AN EYE. A hidden
    # lane is left out of the monitor AND the export — it changes the film. A
    # locked lane plays and exports exactly as it did; what it refuses is being
    # changed: nothing on it can be moved, trimmed, razored, dropped onto,
    # selected or deleted. So this list is read by the EDITOR and deliberately
    # ignored by `animatic_render.py` and by the exporter.
    locked_lanes: list[str] = Field(default_factory=list)


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
    # Lanes the user added. Empty on every project that predates layers, which
    # then shows exactly the default lanes it always did.
    layers: list[AnimaticLayer] = Field(default_factory=list)
    # THE MEDIA LIBRARY — what has been added to this animatic, whether or not
    # anything is currently on the timeline.
    #
    # ⚠ `None` AND `[]` ARE DIFFERENT ANSWERS, AND THE DEFAULT HAD TO BE `None`
    # FOR THAT TO BE SAYABLE. `None` means this project was saved before the
    # library existed — the editor derives one from its frames and audio the first
    # time it is opened. `[]` means the library is EMPTY ON PURPOSE, because the
    # user pressed ✕ on the last card. With `default_factory=list` the two came
    # out of the API as the same `[]`, so a backfill could not tell them apart and
    # emptying the library would have put every card straight back on reload — the
    # ✕ would have looked broken. See `_assets_of` and `libraryFromProject`.
    assets: list[AnimaticAsset] | None = None
    # Pictures composited over the sequence (image layers).
    overlays: list[AnimaticOverlay] = Field(default_factory=list)
    # What happens on the cuts. Empty on every animatic saved before transitions
    # existed, which then plays exactly as it always did — straight cuts.
    transitions: list[AnimaticTransition] = Field(default_factory=list)
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
    # Every Veo render made from this editor, newest last. READ-ONLY: it is
    # server-owned state living in the job's `result`, and `AnimaticSaveRequest`
    # deliberately has no matching field — see AnimaticVeoClip for why a paid
    # render must not be reachable by the autosave.
    veo_clips: list["AnimaticVeoClip"] = Field(default_factory=list)
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
    layers: list[AnimaticLayer] | None = None
    # The whole library, every time — an empty list empties it. Same rule as the
    # shapes and the audio tracks, and for the same reason: with a list, "none" is
    # just an empty list and needs no companion flag.
    assets: list[AnimaticAsset] | None = None
    overlays: list[AnimaticOverlay] | None = None
    # Send the whole list; an empty list removes every transition, so the
    # sequence goes back to straight cuts.
    transitions: list[AnimaticTransition] | None = None
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


class AnimaticVideoItem(BaseModel):
    """One uploaded video, ready to be dropped onto the timeline."""

    upload_id: str
    filename: str = ""
    # Measured SERVER SIDE, by ffmpeg — unlike audio, whose length the browser
    # reports. The client needs it before it can size the clip, and it needs to
    # be the same number the exporter will work from, so there is one measurer.
    # 0 means "couldn't tell": the clip still works, it just opens at the
    # default hold instead of at its natural length.
    duration_ms: int = 0
    url: str


class AnimaticVideoUploadResponse(BaseModel):
    """Returned from POST /animatics/{id}/videos — uploads in the order sent."""

    items: list[AnimaticVideoItem] = Field(default_factory=list)
    # Files that couldn't be stored or read, named rather than silently dropped
    # — same rule as the image upload response.
    rejected: list[str] = Field(default_factory=list)


class AnimaticBoardImportRequest(BaseModel):
    """Body for POST /animatics/{id}/import-storyboard.

    Bringing a board into an animatic that ALREADY EXISTS, which is not the same
    job as `source_storyboard_id` on create: that one fills a brand-new project,
    this one adds a row to a project someone is in the middle of cutting. The
    frames come back rather than being saved, because WHERE they land is the
    client's decision — the same contract the image and video uploads follow.
    """

    storyboard_id: str = Field(..., description="The board to read panels from.")
    default_duration_ms: int = Field(2000, ge=100, le=600_000)


class AnimaticBoardImportResponse(BaseModel):
    """Returned from POST /animatics/{id}/import-storyboard."""

    frames: list[AnimaticFrame] = Field(default_factory=list)
    # What to call the row these go on, so the name comes from the board rather
    # than from a string built twice on two sides of the wire.
    name: str = ""
    title: str = ""
    # True when the board's key poses would have overflowed the frame cap and one
    # frame per shot was used instead. The client says so — silently importing
    # something other than what was asked for is the kind of thing that reads as
    # a bug months later.
    panels_only: bool = False


class AnimaticAudioResponse(BaseModel):
    """Returned from POST /animatics/{id}/audio."""

    upload_id: str
    filename: str = ""
    url: str


# ---------------------------------------------------------------------------
# Animatics → Final Video
# ---------------------------------------------------------------------------
# Three steps, in the order the workflow screen shows them:
#   1. Apply final art & characters — attach the stills that lock the look
#   2. Render shots                 — one Veo clip per shot
#   3. Assemble the sequence        — concatenate the clips into the cut
#
# The project is a job (JobKind.FINAL_VIDEO), like every other workflow, and the
# job's STATUS describes the assembly — the same convention an animatic uses for
# its export. Per-shot render state lives on the shot.
class ShotStatus(str, Enum):
    """Purely RENDER state, and therefore entirely server-owned.

    Whether a shot belongs in the cut is a separate, user-owned decision — see
    `FinalVideoShot.include`. Keeping the two apart matters: excluding a shot
    from the edit must not erase the record of a render that was paid for.
    """

    PENDING = "pending"      # never rendered
    QUEUED = "queued"        # accepted, waiting for a render worker
    RENDERING = "rendering"  # Veo is working on it
    READY = "ready"          # a clip exists
    FAILED = "failed"        # last render failed; `error` says why


class RenderSettings(BaseModel):
    """What a Veo render costs and looks like. See video_client.render_shot.

    Used as the project default and, optionally, overridden per shot — a hero
    shot can be rendered at 1080p standard while the rest stay fast/720p.
    """

    # Price/quality point: 'lite' | 'fast' | 'standard'. Roughly 1× / 4× / 13×.
    tier: str = Field("fast", description="'lite' | 'fast' | 'standard'.")
    resolution: str = Field("720p", description="'720p' | '1080p'.")
    # Veo offers a fixed menu of lengths, not a free-form number.
    duration_seconds: int = Field(8, description="4, 6 or 8.")
    # Veo generates sound with the picture. Off is cheaper and is what you want
    # when the animatic already carries a scratch voiceover.
    generate_audio: bool = True
    negative_prompt: str = Field("", description="What must NOT appear or happen.")


class FinalArtRef(BaseModel):
    """One still that tells Veo what the world and its people look like.

    These are Flow's "ingredients". Three of them can ride along with a render,
    and feeding our OWN generated turnaround sheets in is the whole reason this
    workflow sits downstream of Text-to-Image: the character is already drawn,
    so it need not be described in words and guessed at again per shot.

    `kind` says where the picture comes from — nothing is copied except uploads,
    so re-drawing a panel or regenerating a character sheet updates every shot
    that references it.
    """

    id: str
    kind: str = Field("upload", description="'upload' | 'panel' | 'asset'.")
    name: str = Field("", description="Label shown in the art tray, e.g. 'Shiva'.")
    # kind == "upload": a file dropped into this project.
    upload_id: str | None = None
    # kind == "panel": a drawn storyboard panel.
    storyboard_id: str | None = None
    index: int | None = None
    # kind == "asset": one view of one part of a Text-to-Image character run.
    asset_job_id: str | None = None
    part: str | None = None
    view: str | None = None
    # Filled by the server on read so the client has ONE url shape. Ignored on write.
    url: str | None = None


class FinalVideoShot(BaseModel):
    """One shot: a starting picture, what should move, and the clip it became."""

    id: str = Field(..., description="Stable client-side id (survives reordering).")
    # Where the FIRST frame comes from. Same vocabulary as an animatic frame, so
    # a project built from an animatic keeps its panels live-linked to the board.
    src: AnimaticFrameSource
    label: str = Field("", description="e.g. 'Sc 2 / Sh 4'. Shown in the UI only.")
    # What should MOVE. Describing the picture again is wasted prompt — the
    # picture is already the input. Empty means the shot can't be rendered yet.
    prompt: str = ""
    # None inherits the project settings; set to override for this shot alone.
    settings: RenderSettings | None = None
    # Ids of FinalArtRef entries in the project's art tray (max 3 reach Veo).
    reference_ids: list[str] = Field(default_factory=list)
    # Interpolate towards this art ref instead of letting Veo invent an ending.
    # Pointing it at the NEXT shot's panel is what makes cuts line up.
    last_frame_ref_id: str | None = None
    # Is this shot part of the film? USER-owned, unlike `status`. Excluding a
    # shot keeps its clip and its cost on the record — it just doesn't reach the
    # cut, and isn't re-rendered by "render remaining".
    include: bool = True

    # --- Render state (server-owned; the client sends these back untouched) ---
    status: ShotStatus = ShotStatus.PENDING
    error: str = ""
    # Set once a clip exists on disk. The clip is named from the shot id.
    rendered_at: str = ""
    duration_ms: int = 0
    size_bytes: int = 0
    # What this shot's last successful render was estimated to cost, kept so the
    # project can show a running total instead of only a forecast.
    cost_usd: float = 0.0
    # Filled by the server on read. Ignored on write.
    url: str | None = None
    image_url: str | None = None


class FinalVideoSettings(BaseModel):
    """Project-wide settings: the render default plus how the cut is assembled."""

    aspect_ratio: str = Field("16:9", description="'16:9' or '9:16' — Veo supports these.")
    # The default every shot inherits unless it overrides.
    render: RenderSettings = Field(default_factory=RenderSettings)
    # --- Assembly (step 3) ---
    transition: str = Field("cut", description="'cut' or 'crossfade'.")
    transition_ms: int = Field(400, ge=100, le=2000)
    # Keep the sound Veo generated with each clip. Off gives a silent cut you
    # can lay the animatic's existing voiceover under.
    include_clip_audio: bool = True
    fps: int = Field(24, ge=1, le=60)


class FinalVideoProject(BaseModel):
    """A saved final-video project: the shots, their clips, and the assembled cut."""

    job_id: str
    title: str
    # Describes the ASSEMBLY, not the renders — a failed assemble leaves every
    # rendered clip intact, exactly as a failed animatic export does.
    status: JobStatus
    # Where the shots came from, for 'back to' navigation and Duplicate.
    source_animatic_id: str | None = None
    source_storyboard_id: str | None = None
    settings: FinalVideoSettings = Field(default_factory=FinalVideoSettings)
    shots: list[FinalVideoShot] = Field(default_factory=list)
    # The art tray shared by every shot (step 1).
    art: list[FinalArtRef] = Field(default_factory=list)
    # Last assembly: {url, size_bytes, assembled_at, duration_ms, stale}.
    video: dict | None = None
    # Sum of cost_usd across rendered shots — what this project has actually spent.
    spent_usd: float = 0.0
    error: str | None = None
    created_at: str
    updated_at: str


class FinalVideoCreateRequest(BaseModel):
    """Body for POST /final-videos — start a project.

    With `source_animatic_id` and no shots, the server fills the shot list from
    that animatic's frames, in order, carrying each frame's label across. Same
    for `source_storyboard_id` and its drawn panels.
    """

    title: str | None = None
    source_animatic_id: str | None = None
    source_storyboard_id: str | None = None
    settings: FinalVideoSettings | None = None
    shots: list[FinalVideoShot] = Field(default_factory=list)


class FinalVideoSaveRequest(BaseModel):
    """Body for PUT /final-videos/{id} — every field optional (partial save).

    Sending `shots` or `art` replaces the whole list, so removing one is sending
    the list without it (same rule as an animatic's audio tracks).
    """

    title: str | None = None
    settings: FinalVideoSettings | None = None
    shots: list[FinalVideoShot] | None = None
    art: list[FinalArtRef] | None = None


class RenderShotsRequest(BaseModel):
    """Body for POST /final-videos/{id}/render — render one or more shots.

    An empty `shot_ids` means "every shot that isn't already ready", which is
    the "Render remaining" button. `force` re-renders shots that already have a
    clip — it costs money again, so the client confirms first.
    """

    shot_ids: list[str] = Field(default_factory=list)
    force: bool = False


class FinalVideoSummary(BaseModel):
    """One saved project, as shown on the library grid (deliberately lean)."""

    job_id: str
    title: str
    status: JobStatus
    aspect_ratio: str = "16:9"
    shot_count: int = 0
    rendered_count: int = 0
    duration_ms: int = 0
    cover_url: str | None = None
    has_video: bool = False
    spent_usd: float = 0.0
    created_at: str
    updated_at: str


class CostEstimate(BaseModel):
    """What a render request is expected to cost, before it is made.

    Advisory: list prices drift and only Google bills. It exists so a 40-shot
    "render all" states its price BEFORE the click rather than after.
    """

    shots: int = 0
    seconds: int = 0
    usd: float = 0.0
    tier: str = "fast"
    resolution: str = "720p"


# --- Animating an ANIMATIC frame with Veo, from inside the editor -----------
# These belong to the animatic editor, not to this workflow, but they live down
# here because they are built on `RenderSettings` and `CostEstimate` above —
# and because sharing those types is the point. The editor quotes and bills
# through the same rate table as the final-video workspace, so the two can never
# disagree about what a render costs.
#
# ⚠ THIS IS THE ONE PATH IN THE ANIMATIC EDITOR THAT SPENDS MONEY. It follows
# the discipline established on 2026-08-07; the reasoning for every rule is in
# that Work Log entry:
#   · nothing renders without a FREE estimate having been shown first
#   · the batch is capped (config.MAX_VIDEO_BATCH)
#   · a frame that already produced a clip is never silently re-rendered
#   · a frame with no motion prompt is never submitted — it could only ever
#     produce a PAID failure
#
# The rendered clip lands as an ordinary VIDEO UPLOAD, exactly like a file
# dragged in from the desktop. That is deliberate and it is the whole trick:
# from the moment it exists, a generated clip and a dropped clip are the same
# object on the timeline, so trimming, speed, extraction and export have one
# code path rather than two that can drift apart.
class AnimaticVeoClip(BaseModel):
    """One Veo render on an animatic, as a SERVER-OWNED record.

    ⚠ Lives in the job's `result`, never in `params`. `params` is rewritten
    wholesale by the editor's autosave every time anything changes, so a render
    recorded there would be rolled back by a save that started before it
    finished — erasing the record of a clip that was paid for. This is the same
    lesson as `FinalVideoShot.include` vs `ShotStatus`: what the USER decides and
    what the RENDER decides must not share a home.
    """

    id: str = Field(..., description="This render's own id (a re-render is a new one).")
    # The frame this was generated FROM, so the editor can attach it to the right
    # clip. Not authoritative: the frame may since have been deleted, which costs
    # the attachment, not the clip.
    frame_id: str = ""
    # Where the finished MP4 lives — an ordinary video upload id, servable and
    # placeable exactly as a dropped file is.
    upload_id: str = ""
    prompt: str = ""
    status: str = Field("queued", description="'queued' | 'rendering' | 'ready' | 'failed'.")
    error: str = ""
    # What we ASKED Veo for, so nothing downstream has to measure it — the same
    # rule the assembler follows, and for the same reason: there is no ffprobe.
    duration_ms: int = 0
    # Advisory, and recorded per render so a running total is a sum of real
    # charges rather than a re-estimate at today's settings.
    cost_usd: float = 0.0
    rendered_at: str = ""


class AnimaticAnimateRequest(BaseModel):
    """Body for the two /animate endpoints — estimate (free) and render (paid).

    Both take the SAME body on purpose: the number shown in the confirm dialog
    can then only be the price of the thing the button goes on to do. Two
    differently-shaped requests is how an estimate starts quoting for something
    other than what actually gets rendered.
    """

    # Which frames to animate.
    frame_ids: list[str] = Field(default_factory=list)
    # frame_id → the motion prompt for that frame. A frame whose prompt is blank
    # is REFUSED rather than rendered: Veo bills for a failure exactly as it
    # bills for a success.
    prompts: dict[str, str] = Field(default_factory=dict)
    render: RenderSettings = Field(default_factory=RenderSettings)
    # Re-render a frame that already produced a clip. It costs again, so it is a
    # separate, differently-worded action in the UI — never a silent retry.
    force: bool = False


# --- Captions and voiceover (Phase 5) ---------------------------------------
# ⚠ THE OTHER TWO PATHS IN THE ANIMATIC EDITOR THAT SPEND QUOTA. They follow the
# discipline ✨ Animate established on 2026-08-07, for the same reason:
#   · nothing runs without a FREE estimate having been shown first, and both
#     endpoints of a pair take the SAME body, so the price quoted can only ever
#     be the price of the thing the button then does
#   · the run is capped (captions.MAX_AUDIO_SECONDS, tts.MAX_CHARACTERS)
#   · the job goes RUNNING, which `save_animatic` already refuses to write
#     through — so an autosave cannot roll back work that was paid for
#
# They are far cheaper than a Veo render, which is exactly why the discipline is
# worth keeping: a cheap call is the one that gets clicked forty times.
class AudioCostEstimate(BaseModel):
    """FREE advisory quote for a captions or a voiceover run.

    A separate type from `CostEstimate` rather than a reuse of it: that one is
    priced per SHOT of video at a tier and a resolution, and neither field means
    anything about a sound. A dialog reading "720p" over a transcription is the
    kind of small lie that makes a price look made up — which, being advisory,
    it can least afford.
    """

    # Whichever of these the run is priced by; the other is 0.
    lines: int = 0
    characters: int = 0
    seconds: float = 0.0
    usd: float = 0.0
    model: str = ""
    # True when the request is over its own spend guard. The client uses it to
    # disable the button and say why, rather than letting the call 413.
    over_limit: bool = False
    limit: str = Field("", description="What the limit is, in words, when over_limit.")


class AnimaticCaptionsRequest(BaseModel):
    """Body for the two /captions endpoints — estimate (free) and run (paid)."""

    # WHICH audio track to caption. Named rather than assumed, because an
    # animatic usually carries music as well as a voice and transcribing the
    # music is a paid way to get nothing.
    upload_id: str = Field(..., description="The audio track to transcribe.")
    language: str = Field("", description="Hint, e.g. 'Hindi'. Blank = let the model tell.")
    # Drop the captions a previous run made before adding these. On by default:
    # running it twice is nearly always a correction, and the alternative is two
    # copies of every subtitle stacked on top of each other.
    replace: bool = True


class AnimaticVoiceoverRequest(BaseModel):
    """Body for the two /voiceover endpoints — estimate (free) and run (paid).

    The lines come from the SOURCE BOARD's dialogue, matched to the frames that
    reference each panel, so every line already knows which shot it belongs to
    and therefore where on the timeline it goes. Nothing here has to be typed —
    that is the whole point, and it is why this belongs in the editor rather
    than being a text box beside it.
    """

    voice: str = Field("Kore", description="Prebuilt voice name — see tts.VOICES.")
    # Restrict to some shots. Empty = every frame that has dialogue.
    frame_ids: list[str] = Field(default_factory=list)
    # Lay the spoken lines down as captions too, at the times they were ACTUALLY
    # read at (which is not always the time they were asked for — see
    # `tts.synthesise_timed`). Free: the timings come back with the audio.
    add_captions: bool = True
    replace: bool = True


# ---------------------------------------------------------------------------
# Phase 7 — reaching back to the BOARD from inside the editor
#
# ⚠ A frame is a REFERENCE to a storyboard panel, never a copy of one
# (`AnimaticFrameSource`), and these three requests are what makes that
# reference worth having: redraw the shot, block out more of it, or re-frame it
# — without leaving the timeline you are cutting on. Everything they change is
# on the BOARD or is an ordinary frame property; nothing here invents a new
# thing for the exporter to know about.
# ---------------------------------------------------------------------------
class AnimaticPanelSource(BaseModel):
    """The board panel behind ONE animatic frame — what the inline pane edits.

    `can_regenerate` is False with a `reason` for every frame that is not a
    board panel (an upload, a video clip, a colour card) and for a panel whose
    board has gone. The pane reads the reason out rather than showing a button
    that 400s.
    """

    frame_id: str
    storyboard_id: str | None = None
    index: int | None = None
    # The prompt fields, so the pane opens on the wording this shot was drawn
    # from rather than making the user retype it to change one word.
    description: str = ""
    camera: str = ""
    location: str = ""
    title: str = ""
    can_regenerate: bool = False
    reason: str = ""


class AnimaticPanelRegenerateRequest(BaseModel):
    """Body for POST /animatics/{id}/frames/{frame_id}/panel — redraw this shot.

    Every field is optional and None means "leave it as the board has it", so
    the plain Regenerate button sends an empty body.
    """

    description: str | None = None
    camera: str | None = None
    location: str | None = None


class AnimaticRelengthRequest(BaseModel):
    """Body for POST /animatics/{id}/frames/{frame_id}/sequence — a longer shot.

    "Make this shot 2s longer". The key poses already on disk are KEPT and only
    the new tail is drawn, so the price is the difference rather than the whole
    sequence — see `panel_sequence.plan_beats`.
    """

    duration_seconds: int = Field(
        ..., description="The shot's new length. One of panel_sequence.ALLOWED_DURATIONS."
    )


class AnimaticReframeRequest(BaseModel):
    """Body for the two /reframe endpoints — estimate (free) and run (paid)."""

    # Which shots. Empty = every frame with a picture, which is the "reframe the
    # whole thing for vertical" the button exists for.
    frame_ids: list[str] = Field(default_factory=list)
    # The shape to frame FOR. Defaults to the project's own aspect ratio, which
    # is what you want after switching the project to 9:16 — the frames are
    # still composed for the shape they were drawn in.
    aspect_ratio: str = Field(
        "", description="Target aspect, e.g. '9:16'. Blank = the project's own."
    )


class ReframeCostEstimate(BaseModel):
    """FREE advisory quote for an auto-reframe pass.

    Priced per SHOT, because it is one vision call per picture — which is why it
    is neither `CostEstimate` (per rendered video shot) nor `AudioCostEstimate`
    (per second of sound).
    """

    frames: int = 0
    usd: float = 0.0
    model: str = ""
    aspect_ratio: str = ""
    over_limit: bool = False
    limit: str = Field("", description="What the limit is, in words, when over_limit.")


class VideoBackendStatus(BaseModel):
    """Whether Veo is actually reachable — surfaced before the first paid call."""

    provider: str = ""
    model: str = ""
    ok: bool = False
    error: str | None = None


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


