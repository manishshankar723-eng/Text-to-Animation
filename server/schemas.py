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


class AnimaticFrameSource(BaseModel):
    """Where one animatic frame's picture comes from.

    A storyboard panel is referenced BY INDEX and never copied, so re-drawing
    that panel on the board updates the animatic too. An upload is a file the
    user dropped into this animatic, stored under its own id.
    """

    kind: str = Field(
        "panel",
        description="'panel' (a storyboard panel), 'pose' (one key pose of a panel's sequence), or 'upload'.",
    )
    storyboard_id: str | None = Field(None, description="Board job id (kind='panel'/'pose').")
    index: int | None = Field(None, description="Panel index on that board (kind='panel'/'pose').")
    # WHICH KEY POSE. A shot that has been through Image to Animatic Image has
    # ~4 drawings per second of screen time; referencing them individually is
    # what makes an animatic actually MOVE instead of being a slideshow of
    # stills. Referenced, never copied — redrawing the pose updates the animatic.
    frame: int | None = Field(None, description="Key-pose number within that panel's sequence (kind='pose').")
    upload_id: str | None = Field(None, description="Uploaded image id (kind='upload').")


class AnimaticFrame(BaseModel):
    """One held image in the sequence, with how long it stays on screen."""

    id: str = Field(..., description="Stable client-side id (survives reordering).")
    src: AnimaticFrameSource
    # 0.1s–10min. The whole point of the editor is that this is per-frame.
    duration_ms: int = Field(2000, ge=100, le=600_000)
    label: str = Field("", description="Caption, e.g. 'Shot 3'. Burned in only if show_labels.")
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
    keyframes: dict[str, list[AnimaticKeyframe]] = _KEYFRAMES
    # Filled by the server on read so the client has ONE url shape for both
    # source kinds. Ignored on write.
    url: str | None = None


class AnimaticAudio(BaseModel):
    """One audio track laid under the sequence.

    An animatic can carry several — music under a voiceover is the usual pair —
    and they are MIXED on export, each at its own volume.
    """

    upload_id: str
    # The lane this track sits on. "" = a lane of its own, which is how every
    # track written before layers existed is laid out.
    layer_id: str = ""
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


class AnimaticLayer(BaseModel):
    """One lane the USER added to the timeline, empty until they fill it.

    "Add a layer" makes one of these and nothing else — no upload dialog, no
    clip. Content is then added INTO a lane, so several captions or shapes can
    be organised on separate rows instead of piling into one.

    Every kind also has an implicit DEFAULT lane (clips with `layer_id == ""`),
    which is what every animatic saved before layers existed is made of. The
    base picture sequence (`frames`) is not a layer: it is the video.
    """

    id: str
    kind: str = Field(..., description="'image' | 'text' | 'shape' | 'audio'.")
    name: str = ""


class AnimaticOverlay(BaseModel):
    """A picture composited OVER the sequence — a logo, an inset, a cut-in.

    Geometry matches AnimaticShape exactly (fractions of the frame, `x`/`y` the
    centre) because it is placed the same way and by the same drag handles; the
    only difference is that the fill is an uploaded image rather than a colour.
    """

    id: str
    layer_id: str = ""
    upload_id: str
    start_ms: int = Field(0, ge=0)
    duration_ms: int = Field(2000, ge=100, le=600_000)
    x: float = Field(0.5, ge=-1.0, le=2.0)
    y: float = Field(0.5, ge=-1.0, le=2.0)
    w: float = Field(0.3, gt=0.0, le=4.0)
    h: float = Field(0.3, gt=0.0, le=4.0)
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    rotation: float = Field(0.0, ge=-360.0, le=360.0)
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
    # and a caption arrives instead of appearing, which is the one text
    # animation worth having before a full text-animation preset list.
    opacity: float = Field(1.0, ge=0.0, le=1.0)
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
    """What happens ON one cut — a dissolve, a dip, a wipe, a slide.

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
        "dissolve", description="'dissolve' | 'dip' | 'wipe' | 'slide'."
    )
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


