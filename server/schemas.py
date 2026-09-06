"""
schemas.py — Pydantic models for API requests and responses.

The generate request comes in as multipart/form-data (because it carries an
uploaded image), so its fields are declared inline in main.py as Form(...)
parameters. These models describe the JSON responses and the persisted job
shape.
"""

from enum import Enum
from typing import Any

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
    # ⚠ A BIG ✨ AI EDITOR MESSAGE, run off the request thread. NOT a workflow and
    # it produces no media — it is a plan being WRITTEN, in parallel, so that one
    # message asking for four jobs over sixty shots cannot time out. It is a job
    # kind rather than a private table for the reason stated at the top of
    # `jobs.py`: every workflow's metadata belongs in Mongo, and adding the kind
    # is the whole job. See `server/editor_chat_work.py`.
    EDITOR_CHAT = "editor_chat"


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


class DeepAuditFinding(BaseModel):
    """One thing the paid check saw in one panel."""

    panel: int
    kind: str  # "money" | "language" | "brand" | "placeholder"
    detail: str = ""


class DeepAuditResponse(BaseModel):
    """The result of POST /storyboards/{job_id}/check.

    ⚠ AN EMPTY `findings` IS THE GOOD ANSWER, and the client says so out loud.
    A checker whose success state looks identical to "it did not run" gets
    pressed twice and then distrusted.
    """

    findings: list[DeepAuditFinding] = Field(default_factory=list)
    checked: int = 0
    sheets: int = 0


class Brand(BaseModel):
    """The brand this film is for — and, above all, its LOGO FILE.

    ⚠ THE LOGO IS UPLOADED, NEVER GENERATED, and that is not a preference. An
    image model reconstructs a mark from its description every time it draws
    one, and two reconstructions are never the same picture: one reported
    28-panel promo came back with four unrelated "Lickyeat" logos in it. So the
    model draws a flat placeholder and `brand.stamp()` pastes this file in
    afterwards, which is bit-identical by construction.

    ⚠ AND `name` IS CONTEXT, NOT LETTERFORMS. It tells the model what the
    product IS; the prompt then forbids lettering it anywhere in the picture,
    because a mis-spelt brand is worse than an absent one.
    """

    name: str = Field("", max_length=80)
    # An id from POST /brand/logo. ⚠ NOT the character-reference upload: that
    # one flattens to RGB and would give every logo a white box around it.
    logo_ref_id: str = Field("", max_length=64)
    primary_color: str = Field("", max_length=40)
    secondary_color: str = Field("", max_length=40)


class Market(BaseModel):
    """Who the finished film is FOR — the audience picked on the form.

    Only the two fields a person can sensibly answer. The currency and the
    units are looked up from the country by `market.resolve()` rather than
    asked for: making someone type "₹" after picking India is inviting a typo
    into every price in the film.
    """

    country: str = Field("", max_length=60)  # code ("IN") or name ("India")
    language: str = Field("", max_length=60)


class World(BaseModel):
    """The story's visual world, read from the script by the breakdown — plus
    the MARKET the finished film is for, which the user chooses.

    Carried into EVERY image prompt — character references, prop/background
    references and each panel — so a Shiva Purana script draws Indian people and
    Indian architecture instead of the image model's Western default. All fields
    are optional: an empty world leaves prompts exactly as they were.

    ⚠ THE LAST FOUR ARE A DIFFERENT KIND OF FACT, AND THEY RIDE HERE ON PURPOSE.
    The first six are what the STORY is and are inferred from the script; the
    market is who the FILM IS FOR and comes from the user (their account
    default, or this board's form), with the breakdown's guess only as a
    fallback. They share this model because `world` was ALREADY threaded into
    every generator and stored on every job — a second parallel dict would have
    meant touching twenty call sites to carry one more fact to the same place.
    The server composes them with `market.resolve()` before anything is drawn;
    see market.py for why a wrong currency is worse than none.
    """

    setting: str = ""  # place + period
    culture: str = ""  # cultural / religious tradition
    ethnicity: str = ""  # what the people of this world look like
    wardrobe: str = ""
    environment: str = ""  # architecture, landscape, everyday objects
    notes: str = ""  # iconography, rituals, symbols, colours
    # --- the market (market.MARKET_FIELDS) ---
    country: str = ""  # readable name once resolved, e.g. "India"
    language: str = ""  # what on-screen text is written in
    currency: str = ""  # symbol + name, e.g. "₹ (Indian rupee)"
    units: str = ""  # "metric" | "imperial"


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
    # Carried for symmetry with the board and the asset route, so all three
    # resolve the market the same way — see main._resolve_market. A T-pose on
    # white shows no prices, but the country still says who these people are.
    market: Market | None = Field(
        None, description="Audience picked on the form: country + language."
    )
    # ⚠ NOT COSMETIC. The sheet this produces becomes a look reference inside
    # every panel the character appears in, so a sheet drawn in the wrong medium
    # drags those panels with it — a Cinematic board whose cast came back as
    # Pixar cartoons is the bug this field exists to fix. Optional so an older
    # client still works; empty just means "no style opinion".
    style: str = Field(
        "",
        max_length=200,
        description="The board's style id (e.g. 'cinematic'), or free text for a custom style.",
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
    # ⚠ THIS ONE MATTERS MOST OF THE THREE. A prop is a phone, a menu, a price
    # tag, a shop front — the exact surfaces money and signage live on — and the
    # reference is drawn ONCE and then fed into every panel the object appears
    # in. A `$` baked in here is a `$` on the whole board.
    market: Market | None = Field(
        None, description="Audience picked on the form: country + language."
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
    # ⚠ ONLY THE NAME IS USED HERE, and only to kill placeholders. A film that
    # went out with "That's why [Your App Name] is built for speed" burnt into
    # its captions is why: the writer's bracket was copied into a shot
    # description and then read aloud. The logo has no part in a text breakdown.
    brand: Brand | None = Field(
        None, description="Brand — only `name` is read, to replace placeholders."
    )
    style: str | None = Field(None, description="Chosen visual style (passed through).")
    aspect_ratio: str | None = Field(None, description="Chosen aspect ratio (passed through).")
    provider: str | None = Field(
        None,
        description="Text backend: 'vertex' or 'gemini'. Defaults to server TEXT_PROVIDER.",
    )
    # Only used to name the DRAFT job this breakdown is saved as, so a resumed
    # draft isn't called "Storyboard". Falls back to the script's opening words.
    title: str | None = Field(None, description="Optional title for the saved draft.")
    # ⚠ HOW LONG THE FILM IS MEANT TO BE — the number the user approved on the
    # concept card, and the one thing the breakdown used to be told nothing
    # about. `concept_seconds()` read 30 off the card and the script writer was
    # given 30 seconds of words; the breakdown, knowing no target, boarded them
    # as 29 shots and 1m 04s. Every extra panel is a drawing that was paid for.
    # None is honest for a pasted script, where nobody has said a length.
    seconds: int | None = Field(
        None,
        ge=0,
        description="Target runtime in seconds — the length the user approved.",
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
    # ⚠ MOVEMENT AND DURATION ARE DIRECTOR'S METADATA AND NEVER REACH AN IMAGE
    # PROMPT — the same arrangement `dialogue` above has, for the same reason. A
    # still panel cannot show a camera move or a length, and asking for one gets
    # motion blur, speed lines or an arrow drawn INTO the frame. They are read
    # by the shot card, the PDF and the animatic step, where motion and timing
    # are real things.
    movement: str = ""
    duration_seconds: int = Field(3, ge=0, le=30)
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
    script_count: int = 0
    months: int = 0
    channel_title: str = ""
    # Total tokens this session has spent, so the library card can say so
    # without opening every session to add them up.
    tokens: int = 0
    created_at: str = ""
    updated_at: str = ""


class PlanScriptRequest(BaseModel):
    """Body for POST /plans/{id}/script — write the script for one video.

    Exactly one of `item_index` (a row of the generated calendar) or `brief` (a
    free-text ask) is the subject; both may be sent, in which case the brief
    refines the calendar row rather than replacing it.
    """

    # Which calendar row this script is for. None = not from the calendar.
    item_index: int | None = Field(None, ge=0, description="Index into plan.items.")
    brief: str = Field(
        "", max_length=4000, description="Free-text ask, when not from the calendar."
    )
    seconds: int = Field(60, ge=10, le=3600, description="Target runtime in seconds.")
    notes: str = Field("", max_length=2000, description="Extra notes for this script.")
    # Same vocabulary as the plan's own language — see plan_agent.LANGUAGES.
    # Defaults to whatever the calendar was written in, resolved server-side.
    language: str | None = Field(None, max_length=60)


class PlanDetail(BaseModel):
    """A whole planning session.

    `channel`, `plan` and each entry of `scripts` are free-form dicts on
    purpose: they are produced by youtube_research and plan_agent, which own
    their own shapes. Pinning them here would mean changing three files to add
    one field.
    """

    job_id: str
    title: str
    messages: list[PlanMessage] = Field(default_factory=list)
    channel: dict = Field(default_factory=dict)
    plan: dict = Field(default_factory=dict)
    # Every script written in this session, newest first. See
    # plan_agent.write_script for what one contains.
    scripts: list[dict] = Field(default_factory=list)
    # Running TOKEN total for the whole session — every chat turn, every
    # calendar, every script, including the retries. See ai_usage.Usage.
    # It is a sum of what actually happened rather than a separately maintained
    # counter, so it cannot drift from the parts it is made of.
    usage: dict = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class ScriptDraft(BaseModel):
    """The script the user is currently writing, autosaved (see drafts.py).

    All fields default, so "nothing saved yet" is an empty draft rather than a
    404 the client has to special-case.
    """

    text: str = ""
    title: str = ""
    # ⚠ THE CARD, NOT ONLY THE BOX. The concept the user is part-way through
    # editing — title, premise, arc, key scenes, length, look. Until this field
    # existed a refresh kept the script and lost the card, so the only way back
    # was to generate again, and generating again returns a DIFFERENT film.
    # `None` means "never had one", which is not the same as an empty one.
    concept: dict | None = None
    # ISO-8601, set server-side on every save. Empty when never saved.
    updated_at: str = ""


class ScriptDraftUpdate(BaseModel):
    """Body for PUT /scripts/draft — overwrites the caller's draft."""

    text: str = ""
    title: str = ""
    concept: dict | None = None


class ScriptChatMessage(BaseModel):
    """One turn of the Script → Storyboard chat.

    `role` is our own vocabulary ("user" / "agent"), not the SDK's, so the
    transcript stays readable on the wire and in the browser's storage; the
    translation to the model's "user"/"model" happens in `plan_agent._to_contents`.
    """

    role: str = Field("user", pattern="^(user|agent)$")
    text: str = Field(..., min_length=1, max_length=8000)


class ScriptChatRequest(BaseModel):
    """Body for POST /script-chat.

    ⚠ THE WHOLE TRANSCRIPT IS SENT EVERY TURN, on purpose: this route is
    stateless. There is no chat record on the server to own, expire or scope to
    a user, and the browser already has the messages on screen. The cap is a
    runaway guard — `plan_agent` trims to its own history limit again anyway.
    """

    messages: list[ScriptChatMessage] = Field(..., min_length=1, max_length=80)
    # The form's current state, so the assistant answers about THIS storyboard
    # instead of asking for things the user has already picked.
    genre: str = ""
    style: str = ""
    aspect_ratio: str = ""
    title: str = ""
    current_script: str = ""


class ScriptChatResponse(BaseModel):
    """One reply.

    `script` is empty on every turn that wasn't a request for a script — which
    is most of them. When it is filled the client offers "Use this script",
    which is the only thing that ever writes the form's script box.
    """

    reply: str = ""
    script: str = ""
    title: str = ""
    # This turn's token count (ai_usage.Usage.as_dict), so the caller can show
    # what the conversation is costing. Advisory — see ai_usage.
    usage: dict = Field(default_factory=dict)


class ScriptIntakeRequest(BaseModel):
    """Body for POST /script-intake — "what did the user actually give us?".

    ⚠ ONE FIELD, AND NO `kind` COMING IN. The whole point of this route is that
    the browser does NOT know what the text is and must not be made to guess:
    the form has one box, and asking the user to label their own paste is the
    friction the one-box form was built to remove. See script_intake.py.

    No `min_length`: an empty box is a real answer here ("empty"), and it is
    answered for free without the model ever seeing it.
    """

    text: str = Field("", max_length=400_000)


class ScriptIntakeResponse(BaseModel):
    """What the text is, and what to say about it.

    `reason` is empty for a script — nobody needs to be told their script is a
    script — and `question` is filled only for `vague`, which is the one kind
    that cannot move forward without an answer.

    `decided_by` is "sniff" when the free reader recognised it (no model call,
    no tokens, no latency) and "model" otherwise. Advisory, but worth having on
    the wire: it is the only way to see how often the cheap path is actually
    paying for itself.
    """

    kind: str = "idea"
    reason: str = ""
    question: str = ""
    decided_by: str = "model"
    # This call's token count (ai_usage.Usage.as_dict); {} on the free path.
    usage: dict = Field(default_factory=dict)


class StoryConcept(BaseModel):
    """What we think the user meant, before anything is drawn.

    ⚠ EVERY FIELD IS EDITABLE ON SCREEN, so this model travels in BOTH
    directions: out of `POST /script-concept` as our reading, and back into
    `POST /script-concept/script` as whatever the user changed it to. The
    version that comes back is the instruction — see `concept_to_brief`.
    """

    title: str = ""
    premise: str = ""
    story_direction: str = ""
    key_scenes: list[str] = Field(default_factory=list, max_length=12)
    duration_seconds: int = Field(60, ge=5, le=600)
    visual_direction: str = ""


class ConceptRequest(BaseModel):
    """Body for POST /script-concept — brief or idea in, one concept out.

    `kind` comes from `/script-intake`, not from the user: it only picks the
    default runtime and the wording of the ask. Anything other than "brief" is
    treated as "idea".
    """

    text: str = Field(..., min_length=1, max_length=200_000)
    kind: str = Field("idea", pattern="^(brief|idea)$")
    # What the form already says, so the concept can't contradict a choice the
    # user has already made on the same screen.
    genre: str = ""
    style: str = ""
    aspect_ratio: str = ""


class ConceptResponse(BaseModel):
    """One concept, for the card that asks 'is this the right direction?'."""

    concept: StoryConcept
    usage: dict = Field(default_factory=dict)


class ConceptScriptRequest(BaseModel):
    """Body for POST /script-concept/script — the APPROVED concept → a script.

    ⚠ THIS IS THE STEP THAT MAKES THE WHOLE FLOW WORK. A concept cannot be
    broken into shots: the review step, `ScriptPanel` and every shot card's
    "FROM YOUR SCRIPT · LINE 12" all need a real script to point at. So an
    approved concept is written out by `plan_agent.write_script()` — which
    already emits the exact layout `script_breakdown.py` reads — and the board
    is built from that text.

    `source` is what the user originally pasted, carried along for the details
    a concept has no field for (a product name, a required line, a platform).
    """

    concept: StoryConcept
    source: str = ""
    # What to WRITE it in. Empty = let the writer follow the source's language.
    language: str = ""


class ConceptScriptResponse(BaseModel):
    """The script, in the layout the breakdown reads."""

    script: str = ""
    title: str = ""
    seconds: int = 0
    usage: dict = Field(default_factory=dict)


class BoardSelection(BaseModel):
    """What the user has clicked on the board, if anything.

    ⚠ THIS IS WHAT MAKES "make this one wider" A SENTENCE THAT WORKS. Without a
    selection the assistant has no referent and the only honest answer is
    "which one?". Shot numbers are 1-BASED here, as printed under the panels.
    """

    kind: str = Field("none", pattern="^(panel|scene|none)$")
    shot: int = Field(0, ge=0, le=500)
    scene: int = Field(0, ge=0, le=500)


class BoardAskRequest(BaseModel):
    """Body for POST /storyboards/{job_id}/ask — "what should change?".

    ⚠ THE PANELS ARE NOT SENT. They are read from the job on the server, so the
    plan is always against what is really stored rather than whatever the tab
    last rendered — a board edited in another tab would otherwise be planned
    against stale shot numbers and edit the wrong pictures.

    Stateless like `/script-chat`: the browser owns the transcript.
    """

    messages: list[ScriptChatMessage] = Field(..., min_length=1, max_length=40)
    selection: BoardSelection = Field(default_factory=BoardSelection)


class BoardAction(BaseModel):
    """One intended edit. A PLAN — the server does not run it.

    `index` is 0-based, for the endpoints. `shot` is 1-based, for the sentence
    on screen. `draws` says whether running it costs an image, which is what the
    Apply button counts up before anything is charged.
    """

    action: str = Field(..., pattern="^(edit|insert|delete)$")
    index: int = Field(..., ge=0)
    shot: int = Field(..., ge=1)
    description: str = ""
    camera: str = ""
    location: str = ""
    why: str = ""
    draws: bool = False


class BoardAskResponse(BaseModel):
    """What the assistant said, and what it would change.

    ⚠ An empty `actions` with a reply is a NORMAL answer, not a failure: it is
    what "I can't do that here" and "which shots do you mean?" both look like.
    """

    reply: str = ""
    actions: list[BoardAction] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)


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


class RefTake(BaseModel):
    """One take of a reference — a picture that was drawn or uploaded for a name.

    ⚠ EVERY TAKE COSTS AN IMAGE, so none of them may be lost. Pressing Generate
    twice mints two reference_ids and the earlier folder is never overwritten,
    so the takes are already durable on disk; this is what lets a RESUMED draft
    find them again instead of showing a card that looks as though nothing was
    ever drawn. `uploaded` rides along per take because a user's own image must
    never be redrawn by "Generate all", whichever take is live.
    """

    reference_id: str
    uploaded: bool = False


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
    # name → EVERY take drawn or uploaded for it, oldest first. The id in
    # `character_refs` / `asset_refs` above is whichever of these is live. An
    # older draft simply has none, and its single reference is adopted as take 1
    # by the client — see RefTake.
    character_takes: dict[str, list[RefTake]] = Field(default_factory=dict)
    asset_takes: dict[str, list[RefTake]] = Field(default_factory=dict)
    asset_categories: dict[str, str] = Field(default_factory=dict)
    updated_at: str = ""


class StoryboardDraftCreate(BaseModel):
    """Body for POST /storyboards/draft — save a shot list that has no draft yet.

    ⚠ THIS IS NOT A BREAKDOWN AND MUST NEVER BECOME ONE. No model is called and
    no quota is spent; the shots already exist and this only gives them a record
    to live in. It exists because a session could reach the review step with no
    draft behind it — Duplicate is the plain case, since it reuses a saved
    board's shots rather than running a paid breakdown — and the autosave is
    keyed on having a draft, so EVERYTHING that session did was silently
    discarded on leaving the workflow, references included. Reported: resuming
    afterwards offered a different project, because the work had never been
    saved at all.
    """

    shots: list[Shot] = Field(..., min_length=1, description="The shots to save.")
    title: str | None = None
    script: str = ""
    style: str | None = None
    aspect_ratio: str | None = None
    genre: str | None = None
    characters: list[Character] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    world: World | None = None


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
    character_takes: dict[str, list[RefTake]] | None = None
    asset_takes: dict[str, list[RefTake]] | None = None
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
    # ⚠ SENT SEPARATELY FROM `world` EVEN THOUGH IT ENDS UP INSIDE IT. What the
    # user picked on THIS board's form has to arrive distinguishable from what
    # the breakdown GUESSED off the script, because they sit at opposite ends of
    # `market.resolve()`'s precedence — merged into one dict by the client, the
    # server could not tell a deliberate choice from a lucky guess. Absent or
    # empty means "the form said nothing", not "no market".
    market: Market | None = Field(
        None, description="Audience picked on the form: country + language."
    )
    # The brand this film sells, if any. Stored on the job so a redraw months
    # later stamps the SAME logo file the rest of the board is carrying.
    brand: Brand | None = Field(
        None, description="Brand name, uploaded logo and colours."
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
    # ⚠ WHY THERE IS NO SEQUENCE, when there is none. Empty means "this clip has
    # a board and these are its poses"; filled means "this clip is not a board
    # panel at all" (an upload, a video clip, a colour card) or its board has
    # gone. Same field, same wording and the same reason as
    # `AnimaticPanelSource.reason` — the pane reads it instead of the route
    # answering 400 to a question every selected clip asks. See
    # `get_frame_sequence`.
    reason: str = ""


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
    # WHICH WORKFLOW MADE IT. Empty for Script to Storyboard's own boards;
    # `"animatic-image"` for a copy refined in 🖼 Image to Animatic Image.
    #
    # ⚠ IT IS ON THE CARD BECAUSE A COPY KEEPS THE ORIGINAL'S NAME. A picker that
    # asks for every workflow (`workflow="*"` — what the animatics and video
    # screens must do, see `list_storyboards`) therefore draws the same title
    # twice with nothing to tell them apart. Reported live on 2026-09-06: six
    # pairs of identical rows in the editor's import dialog. The filter was
    # right; the LIST was unreadable, which is its own bug.
    workflow: str = ""
    # Bytes on disk: every panel, upload and rendered file this project
    # owns. 0 means nothing generated yet, and the client draws that the
    # same way as "no folder" — both are "nothing here" to a reader.
    # Measured by `common.dir_bytes`, which caches on the job's
    # `updated_at` so the library's five-second poll doesn't re-walk the
    # whole output folder.
    size_bytes: int = 0
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
    # ⚠ THE CAST AND PROPS COME BACK TOO, and without them re-opening a board
    # lands on a review step whose cast page knows the NAMES (they are on the
    # shots) and none of the descriptions — which are what a reference is drawn
    # from. Stored on the board job as `cast`, since the day boards were first
    # written; nothing was reading them back.
    characters: list[Character] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    # ⚠ AND THE REFERENCES THAT WERE ALREADY PAID FOR. name → reference_id, the
    # same shape the draft returns. Without these, re-opening a board shows a
    # cast page of empty cards and a "(skip refs)" button — every picture the
    # user has already bought, invisible, and the only visible way forward is
    # to buy them again. Reported once already on drafts:
    # *"mai back aaya to mera ananya wala photo dikh hi nahi raha hai … baar
    # baar generate karna pare, usko paisa lagta hai."*
    character_refs: dict[str, str] = Field(default_factory=dict)
    asset_refs: dict[str, str] = Field(default_factory=dict)
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
    # --- A GENERATED IN-BETWEEN SHOT ---------------------------------------
    # ⚠ THESE TWO ARE WHAT MAKES A SHOT THAT IS *NOT ON THE BOARD* STILL A BOARD
    # SHOT. "Generate a shot after this one" (the timeline's right-click menu)
    # draws a new picture with Gemini and stores it as an ordinary animatic
    # UPLOAD — the board is deliberately NOT edited, because inserting a panel
    # renumbers every panel after it and every OTHER animatic referencing that
    # board by index would then show the wrong picture. So `kind` stays
    # "upload", `storyboard_id` is carried anyway (that is what puts the clip on
    # the Storyboard images row and keeps it in Storyboard Frames in the Media
    # pane), and there is no `index`, because there is no panel.
    #
    # `shot_id` is the clip's own identity as a SHOT, and it exists because
    # `index` cannot be borrowed for the job: `_shot_key` / `shotKey` pair a Veo
    # take with the shot it was made from by (board, index), and a generated
    # shot claiming an index would pair with the real panel sitting at it.
    # ⚠ It survives ✨ Animate, because `attachVeoClip` copies the whole `src`.
    shot_id: str = Field(
        "", description="A generated in-between shot's own identity (no panel index)."
    )
    # ⚠ THE WORDING IT WAS DRAWN FROM, kept ON the clip because there is no
    # panel to read it back off. `GET /frames/{id}/panel` answers "nothing" for
    # these, so this is what ✨ Animate drafts its motion prompt from and what
    # the next in-between shot is written between.
    prompt: str = Field("", description="What a generated shot was drawn from.")


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
    # THE CREDIT THIS SOURCE ARRIVED WITH — "" for anything the user made or
    # uploaded themselves, and a ready-to-print line for a sound taken from the
    # Freesound library (`freesound.credit_line`).
    #
    # ⚠ IT IS STORED, NOT RECOMPUTED, and that is the whole point of it being
    # here. A CC BY sound obliges whoever publishes the video to credit its
    # author; if the credit lived only in the search result it would be gone the
    # moment the pane was closed, and the obligation would outlive the only
    # record of who has to be thanked. It rides on the LIBRARY card rather than
    # on the audio clip for the same reason the card exists at all: the razor
    # makes four clips out of one recording, and the licence belongs to the
    # recording.
    attribution: str = ""
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
            "A PICTURE TRACK: 'board_image' | 'board_video' | 'video'. "
            "Or an overlay/timed row: 'image' | 'text' | 'shape' | 'audio'. "
            "'image' is pictures composited OVER the cut; the three picture kinds "
            "are IN it. ⚠ 'stills' is a RETIRED fourth picture kind — an uploaded "
            "picture goes to an 'image' lane now — and it is still accepted here "
            "because saved projects carry it; the editor reads one as 'video', "
            "which is the row its clips already play on (`rowKindOrLegacy` in "
            "client/src/animatic/scene.js)."
        ),
    )
    name: str = ""
    # WHICH picture track this row is — the picture kinds only, None
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
    # WHERE THE PICTURE CAME FROM — the same reference an `AnimaticFrame` carries.
    #
    # ⚠ IT IS METADATA, NOT A PATH. An overlay's bytes are ALWAYS resolved from
    # `upload_id` — the read fills `url` from it and the exporter opens
    # `_image_path(job_id, upload_id)` — and nothing reads this field to find a
    # file. It exists so the MEDIA LIBRARY can recognise its own: a card's
    # identity is its source (`assetKey` in `client/src/animatic/assets.js`), and
    # without this an overlay is a picture on the timeline that no card can be
    # matched to. That cost three things at once: the ×N badge under-counted, the
    # card's ✕ left the overlay playing from a source no longer listed anywhere,
    # and "Select its clips" could not find it.
    #
    # ⚠ A PANEL OVERLAY'S `upload_id` IS A COPY, WHICH IS WHY MATCHING ON THAT
    # ALONE WAS NOT ENOUGH. A board panel has no upload of its own, so
    # `overlayFromFrame` uploads its bytes into this animatic and gets a NEW id,
    # unrelated to the panel that was dragged. This is the only thing that still
    # says "panel 3 of that board". The other two ways an overlay is made
    # (`addOverlayFiles` and the ✨ generate) reuse the card's own upload id, so
    # they matched on `upload_id` all along and this merely records it.
    #
    # ⚠ AN OVERLAY SAVED BEFORE THIS FIELD EXISTED GETS THE DEFAULT — `kind:
    # "panel"` with no ids, which matches nothing AND is the same for every such
    # overlay, so keying on it would fold them all together. The client reads any
    # `src` with no usable ids as `{kind: "upload", upload_id}`, which is exactly
    # what such an overlay was. See `overlaySource` in `assets.js`.
    src: AnimaticFrameSource = Field(default_factory=AnimaticFrameSource)
    start_ms: int = Field(0, ge=0)
    duration_ms: int = Field(2000, ge=100, le=600_000)
    x: float = Field(0.5, ge=-1.0, le=2.0)
    y: float = Field(0.5, ge=-1.0, le=2.0)
    w: float = Field(0.3, gt=0.0, le=4.0)
    h: float = Field(0.3, gt=0.0, le=4.0)
    # The same multiplier a shape carries, for the same reason — see its
    # comment. An overlay is placed with the identical box and handles, so it
    # gets the identical field.
    scale: float = Field(1.0, gt=0.0, le=16.0)
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
    # ("scrim"), a solid box, an outline only ("none"), or nothing whatsoever
    # ("plain"). ⚠ "none" and "plain" ARE NOT THE SAME. "none" draws no bar but
    # gives the glyphs an automatic dark outline, which is what every caption
    # saved with that value has always drawn; "plain" draws neither, so the
    # letters sit bare on the art. Read `text_backdrop` in `animatic_render.py`
    # — the fold lives there so the preview and the export cannot disagree, and
    # an unrecognised value becomes a scrim rather than nothing.
    backdrop: str = Field("scrim", description="'scrim' | 'box' | 'none' | 'plain'.")
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
    # ⚠ `scale` HAS BEEN ANIMATABLE SINCE PHASE 5 AND HAD NO FIELD HERE UNTIL
    # PHASE 1 OF THE PRESET WORK, WHICH MEANT THE SERVER THREW IT AWAY. Pydantic
    # ignores an unknown key by default, so a caption arriving with `scale: 1.04`
    # came back out of `AnimaticSaveRequest` without it — the KEYS survived
    # (`keyframes` is a field) but the RESTING value did not, and `scene_at`
    # resolves a property to its resting value everywhere the keys do not reach.
    # The effect: `captionPush` in `agent/actions.js` sets `rest.scale` to 1.04
    # at the end of its push, the save dropped it back to 1.0, and every caption
    # the AI editor laid down snapped back to its original size the instant the
    # last key passed. Every zoom, pop and bounce preset in `text_presets.js`
    # would have inherited exactly that.
    #
    # A ZOOM OF THE WHOLE CAPTION — glyphs, backdrop and padding together, laid
    # out at the resting size and then scaled, so the line breaks never move
    # while it animates. Bounds match `AnimaticShape.scale` because it is the
    # same property doing the same job.
    scale: float = Field(1.0, gt=0.0, le=16.0)
    # DEGREES CLOCKWISE, like a shape's and like CSS `rotate()`. 0 is every
    # caption ever written. ⚠ It turns the caption about the SAME anchor `scale`
    # grows about — the browser gives both to one CSS `transform-origin`, so
    # there is only one anchor to be had; `draw_texts` matches it by rotating the
    # measured block about that same point.
    rotation: float = Field(0.0, ge=-360.0, le=360.0, description="Degrees, clockwise.")
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
    # --- The TYPE, part two -------------------------------------------------
    # Same rule as the block above and for the same reason: every field here is
    # optional and its default is the number the drawing code already used, so
    # an animatic saved before these existed opens, plays and exports as the
    # picture it was. `tests/captions_check.py` asserts that byte for byte.
    #
    # AN EXPLICIT FONT SIZE, in pixels at 1080p, scaled by the real frame height
    # like `stroke_px`. 0 means "use the `size` preset", which is every caption
    # written before this — S/M/L are `height / 30 | 21 | 14`, i.e. 36 / 51 / 77
    # at 1080p, and this is the escape hatch for the title that wants 120.
    size_px: float = Field(0.0, ge=0.0, le=400.0)
    # LEADING — the distance between baselines, as a multiple of the face's own
    # (ascent + descent). 1.28 is what every caption before this was set at, and
    # it is CSS `line-height` on the other side with no conversion.
    line_height: float = Field(1.28, ge=0.6, le=3.0)
    # CASE, applied before the text is wrapped — so the wrap is measured on the
    # glyphs that get drawn, which is the only order that can agree with CSS
    # `text-transform`. "none" leaves the typed text alone.
    text_case: str = Field("none", description="'none' | 'upper' | 'lower' | 'title'.")
    # How wide the block may get before it wraps, as a fraction of the FRAME —
    # 0.86 is what `_TEXT_WIDTH` has always been. Narrow it for a title that
    # should break after three words instead of running the width of the shot.
    wrap: float = Field(0.86, ge=0.1, le=1.0)
    # --- The backdrop, in detail --------------------------------------------
    # `backdrop` above still chooses the KIND (bar / box / none); these describe
    # the one it chose. The colour and the padding default to what the drawing
    # code hard-coded, and `backdrop_opacity` is None for "whatever the kind is
    # worth" — 0.55 for a scrim, 0.88 for a box. A number overrides both, which
    # is the only way to say "a box, but at 40%".
    backdrop_color: str = Field("#000000", description="Backdrop fill, #rrggbb.")
    backdrop_opacity: float | None = Field(None, ge=0.0, le=1.0)
    # Corner radius and padding, as multiples of the font size (CSS `em`), so
    # they scale with the frame like everything else on a caption.
    backdrop_radius: float = Field(0.25, ge=0.0, le=2.0)
    backdrop_pad: float = Field(1.0, ge=0.0, le=4.0)
    # --- The shadow, in detail ----------------------------------------------
    # `shadow` above is the DISTANCE (in em); these are its colour, its strength
    # and its direction in degrees clockwise from "right". 45 is down-and-right,
    # which is the offset every caption before this cast, and 0.55 is the alpha
    # it cast it at.
    shadow_color: str = Field("#000000", description="Shadow ink, #rrggbb.")
    shadow_opacity: float = Field(0.55, ge=0.0, le=1.0)
    shadow_angle: float = Field(45.0, ge=0.0, le=360.0)
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
    # ⚠ A NAME FROM `SHAPE_KINDS` in `animatic.py` — forty-one of them, grouped for
    # the picker in `client/src/animatic/shape_points.js`. NOT validated against
    # that list on purpose: an id this build has never heard of draws as a plain
    # box in all three renderers, which is a shape somebody can still see and
    # move, where a 422 on load would be a project that will not open at all.
    kind: str = Field("rect", description="A shape name: 'rect', 'ellipse', 'star6', 'heart', …")
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
    # ⚠ A MULTIPLIER ON w/h, NOT A REPLACEMENT FOR THEM — and it exists for one
    # reason: it is KEYFRAMABLE AS ONE PROPERTY. "Pop this in" is a single curve
    # here; done with w and h it is two curves that have to be kept identical by
    # hand for ever, and the moment they drift the box squashes as it grows.
    # 1.0 is the size w/h already say, so every shape saved before this draws
    # exactly as it did. Read `box_size` in `animatic_render.py` for the rule
    # that every place drawing this box goes through it.
    scale: float = Field(1.0, gt=0.0, le=16.0)
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
    # --- WHAT LANGUAGE THIS FILM IS IN -------------------------------------
    # ⚠ IT IS A PROJECT SETTING, NOT A REQUEST PARAMETER, and the difference is
    # the whole reason it is here. Three separate things write words into an
    # animatic — the Director's on-screen text, the voiceover, the captions —
    # and each of them used to decide the language on its own, which is how a
    # Hinglish film ends up with an English title card over a Hindi voiceover.
    # Written once, on the film, and read by all of them.
    #
    # ⚠ FREE TEXT, NOT AN ENUM. `plan_agent.LANGUAGES` describes the three that
    # need describing (English, Hinglish — which is Hindi in LATIN script, not
    # Devanagari — and Hindi), and anything else is passed through as the user's
    # own name for their language: "Tamil", "Bhojpuri", "Spanish" all work with
    # no code change. Same rule Plan & Script already follows; there is one table
    # and it lives in `plan_agent`.
    #
    # ⚠ EMPTY IS A REAL ANSWER and it is the default: it means "nobody has said",
    # and every reader treats that as "use the language the material is already
    # in". Every animatic saved before this field therefore behaves exactly as it
    # did. ⚠ A BOARD DOES NOT CARRY A LANGUAGE TODAY — nothing in the storyboard
    # job records one — so a new animatic inherits one only if the board it came
    # from has grown the field since; otherwise this stays blank until the 🎬
    # popup asks.
    language: str = Field("", description="e.g. 'english', 'hinglish', 'hindi', or any language name.")
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
    # WHAT ORDER THE USER PUT THE ROWS IN — the timeline's gutter, top of the
    # stack first, in the SAME token vocabulary as the two lists above ("text:" is
    # the default text row, "shape:<id>" one the user added, "frames:<n>" picture
    # track n).
    #
    # ⚠ THIS IS THE COMPOSITING ORDER OF THE WHOLE VISUAL STACK, and it is read by
    # the EXPORTER as well as the editor — which is the opposite of what this field
    # was when it was introduced. It used to name only the overlay rows, and what
    # drew over what was decided elsewhere: by `AnimaticFrame.track` for the
    # picture rows, and by a sequence hard-coded three times over (in `sceneAt`, in
    # `ProgramCanvas` and in `render_frame`) for the four kinds. That is exactly why
    # a row could only be restacked among its own kind — reported as "i check
    # shapes layer move only other shapes layer … i want these all layer move up
    # down each other". Every visual row is on one z-scale now; see `lane_rank` in
    # animatic_render.py and its twin in client/src/animatic/lane_order.js.
    #
    # ⚠ A PICTURE ROW'S `track` IS NO LONGER ITS Z. It is which ROW a clip is on
    # and nothing more. Dragging a picture row therefore rewrites THIS list and
    # touches no clip at all, where it used to renumber every clip on both rows.
    #
    # ⚠ AUDIO AND THE CAPTIONS ROW ARE NEVER IN HERE. Audio rows are mixed rather
    # than stacked, so no order of them makes a different film; captions are pinned
    # to the top, and being absent from this list is WHAT PINS THEM — an unlisted
    # row ranks above every listed one.
    #
    # ⚠ A ROW THIS LIST DOES NOT MENTION KEEPS ITS DERIVED PLACE, ABOVE EVERYTHING
    # THE LIST NAMES — which is what makes an empty list mean "the order this
    # editor has always produced": with nothing listed, every row falls back to the
    # old hard-coded sequence (pictures by track, then shapes, then overlay
    # pictures, then text). So every animatic saved before this opens, previews and
    # exports exactly as it did, and there is no migration pass.
    #
    # ⚠ IT IS NOT FILTERED BY `hidden_lanes` ANYWHERE. A hidden row's clips are
    # dropped before the encoder sees them, so naming it here costs nothing —
    # whereas stripping it would renumber the ranks of every row below it, which is
    # a restack nobody asked for.
    lane_order: list[str] = Field(default_factory=list)


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
    # The shape layer — the vector shapes drawn over the picture, from a plain box
    # to a forty-one-strong library of polygons, stars, flowers and symbols. Timed
    # like the text layer and, like it, independent of the frames.
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
    # The last 🎬 Veo pass, if one was ever started. READ-ONLY and server-owned
    # for the same reason `veo_clips` is. ⚠ THIS IS WHAT MAKES A RUN RESUMABLE:
    # a `status` of "running" on a project that has just been opened means a pass
    # was interrupted, and the editor offers to pick it up rather than quietly
    # abandoning shots the user has already paid for.
    director_run: "AnimaticDirectorRun | None" = None
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
    # Bytes on disk: every panel, upload and rendered file this project
    # owns. 0 means nothing generated yet, and the client draws that the
    # same way as "no folder" — both are "nothing here" to a reader.
    # Measured by `common.dir_bytes`, which caches on the job's
    # `updated_at` so the library's five-second poll doesn't re-walk the
    # whole output folder.
    size_bytes: int = 0
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


class AnimaticOverlayRequest(BaseModel):
    """Body for `POST /animatics/{id}/overlays` — make one FX overlay.

    ⚠ FREE, AND IT CALLS NO MODEL. The overlay is drawn from nothing by numpy in
    `fx_overlays.py`; there is no asset to license, no download and no quota.
    What it costs is a few seconds of CPU, which is why the route is one clip at
    a time rather than a batch.
    """

    kind: str = Field(..., description="An id from `fx_overlays.OVERLAYS`.")
    # How long, in seconds. 0 (the default) means "whatever the catalogue says
    # this one should be" — the shelf sends nothing and gets the house length.
    # Clamped server-side against `fx_overlays.MIN_SECONDS`/`MAX_SECONDS`: a
    # minute of 4K noise is a 300MB file nobody asked for.
    seconds: float = Field(0.0, ge=0.0, le=60.0)


class AnimaticOverlayResponse(BaseModel):
    """The generated overlay, ready to be dropped onto the timeline.

    ⚠ IT IS AN ORDINARY VIDEO UPLOAD. `item` is exactly the shape the video
    upload route returns, because from here on there is nothing special about
    this clip at all — it trims, retimes, fades and deletes like any other.
    """

    item: AnimaticVideoItem
    # ⚠ THE BLEND MODE TRAVELS WITH IT. A light leak on "normal" is an opaque
    # orange rectangle over the shot; the same file on "screen" is a light leak.
    # Sending it back with the clip is what makes dropping one a single gesture
    # instead of a gesture plus a lookup nobody should have to know.
    blend: str = "screen"
    label: str = ""


class AnimaticVideoUploadResponse(BaseModel):
    """Returned from POST /animatics/{id}/videos — uploads in the order sent."""

    items: list[AnimaticVideoItem] = Field(default_factory=list)
    # Files that couldn't be stored or read, named rather than silently dropped
    # — same rule as the image upload response.
    rejected: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ONE IMAGE FROM ONE SENTENCE — the Media pane's ✨
# ---------------------------------------------------------------------------
# ⚠ NOT THE SAME THING AS "generate a shot beside this one", though both draw a
# picture and both are reached from a ✨. That one belongs to the STORYBOARD: it
# is a shot, it is drawn in the board's look with the board's references, it goes
# on the board's row and it sits between two named neighbours. This one belongs
# to nothing — a title card, a texture, an inset, a logo plate — so it takes no
# style, no continuity and no neighbours, and it lands on the overlay "Images"
# lane, which is where every other picture that is not a shot already goes
# (`belongsOnImageLane`). Keeping them apart is what stops the freeform prompt
# acquiring a storyboard rider that would draw "a neon city" as a sketch of one.
class AnimaticImageBackend(BaseModel):
    """WHICH MODEL WILL DRAW — free, and it needs no project.

    Shown in the dialog before anything is generated, the same way the shot
    dialog names its model: there is one image model and it is set in the
    environment, so this is a statement, not a choice.
    """

    model: str = ""
    provider: str = ""


class AnimaticImageGenerateRequest(BaseModel):
    """Body for POST /animatics/{id}/images/generate — SPENDS QUOTA."""

    prompt: str = Field(..., min_length=1, max_length=2000)
    # "" = the project's own shape, which is what the dialog opens on.
    aspect_ratio: str = Field("", max_length=16)


class AnimaticGeneratedImage(BaseModel):
    """The drawn picture, as an UPLOAD the client can place.

    ⚠ IT IS THE SAME `AnimaticMediaItem` A FILE UPLOAD RETURNS, on purpose: from
    here on nothing downstream can tell a generated picture from one that was
    dragged in, so it lists, drags, places, exports and deletes identically.
    """

    item: AnimaticMediaItem
    # What to call it in the Media library. Built HERE rather than on both sides
    # of the wire — the same rule `AnimaticBoardImportResponse.name` follows —
    # and it is the prompt's opening words, because that is what the person who
    # typed it will recognise the card by.
    name: str = ""
    model: str = ""


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
# Interchange — this cut, as another editor can open it
# ---------------------------------------------------------------------------
class InterchangeLoss(BaseModel):
    """ONE THING THIS EXPORT COULD NOT CARRY, and how much of it there was.

    ⚠ THE WHOLE POINT OF THE FEATURE IS THIS LIST. A project-exchange file holds
    the CUT — which clip, where, how long, on which track — and no exchange
    format on earth holds a WebGL grade, a mask, a blend mode or a text clip. An
    export that quietly left those behind would be reported as "it didn't work";
    an export that says "3 colour grades will not come across" is a tool.
    """

    what: str = Field(..., description="e.g. 'effects and colour grades'.")
    count: int = Field(0, ge=0)


class InterchangeReport(BaseModel):
    """What GET /animatics/{id}/interchange/preview answers.

    Shown in the dialog BEFORE the download, which is the only moment saying it
    is any use.
    """

    format: str = Field(
        "fcp7",
        description=(
            "'fcp7' (Premiere / Resolve / Avid / Final Cut), 'aftereffects' "
            "(a script AE runs) or 'edl' (CMX3600). ⚠ THE REPORT IS PER "
            "FORMAT: an EDL holds one video track and no dissolves, so the "
            "losses below grow when it is chosen."
        ),
    )
    clips: int = 0
    audio_clips: int = 0
    video_tracks: int = 0
    audio_tracks: int = 0
    files: int = Field(0, description="Distinct media files the zip will carry.")
    duration_frames: int = 0
    fps: int = 24
    # Rough, and labelled as such in the UI: it is the sum of the source files
    # BEFORE zip compression, which is the honest direction to be wrong in.
    media_bytes: int = Field(0, description="Total size of the media, uncompressed.")
    dropped: list[InterchangeLoss] = Field(default_factory=list)
    missing: list[str] = Field(
        default_factory=list, description="Clips whose file has gone — left out."
    )


class ImportMissingFile(BaseModel):
    """One file the project named that never arrived — and WHERE it lived.

    ⚠ `placeholders` NAMES THE FILE; THIS SAYS WHICH FOLDER TO GO AND GET. The
    media for one cut is routinely spread over several folders, and a project
    can point at a file that lives inside a DIFFERENT project entirely — a
    shared logo, a music bed reused across a series. A user who attached the
    obvious folder and still saw "that .mp3 did not arrive" had nothing in the
    dialog to act on, and read it as this app not taking music. The folder comes
    off the path the editor itself wrote (`pathurl`), so it is the real one.
    """

    name: str = Field(description="The file's own name, as the project spells it.")
    folder: str = Field(
        "",
        description=(
            "The folder it sat in on the machine that made the project — '' when "
            "the format did not carry a path. For the user to FIND it; nothing "
            "here can read it."
        ),
    )
    kind: str = Field("picture", description="'picture' | 'sound'.")
    clips: int = Field(1, description="How many clips on the timeline wanted it.")


class AnimaticImportResponse(BaseModel):
    """What POST /animatics/{id}/interchange/import hands back.

    ⚠ IT SAVES NOTHING, and that is the contract every producer in this router
    follows — `import_storyboard`, the image upload, the video upload: **the
    server produces the material, the client decides the timeline.** Here it also
    buys the one thing an import most needs: the whole thing lands as ONE entry on
    the editor's own undo stack, so a user who does not like what arrived presses
    Ctrl+Z rather than rebuilding their film.

    The media, by contrast, IS stored — it had to be, to be matched at all — and
    it lists in the Media pane whether or not the clips are taken.
    """

    # The clips themselves, in this app's own shapes, ready to be placed.
    # ⚠ `track` AND `layer_id` ARE RELATIVE (0,1,2… / "_import_0"): the client
    # re-bases them onto rows it creates, because only the browser knows which
    # row numbers this project already uses.
    frames: list[AnimaticFrame] = Field(default_factory=list)
    audio_tracks: list[AnimaticAudio] = Field(default_factory=list)
    transitions: list[AnimaticTransition] = Field(default_factory=list)
    # ⚠ THE LETTERING, AND IT IS NOT A FRAME. A Premiere title is a clip with no
    # file, so until this existed every one of them arrived as an invisible
    # placeholder on a picture row and the import told the user to type their
    # titles again. They are `AnimaticTextClip`s on TEXT rows now, addressed by
    # `layer_id` like every other timed clip — `"_import_text_0"`, re-based by
    # the client. ⚠ NEVER `CAPTION_LAYER_ID`: that row belongs to ✨ Auto
    # captions and is rewritten wholesale on every run of it.
    texts: list[AnimaticTextClip] = Field(default_factory=list)
    # Drawn shapes, at the right times and at zero opacity — their geometry and
    # fill are read out of its `Path` and `Appearance` blobs where they can be
    # — a plain rectangle, which is the white or black card people put behind a
    # picture — and stood in for at zero opacity where they cannot. See
    # `interchange._import_shape_clip`.
    shapes: list[AnimaticShape] = Field(default_factory=list)
    # ⚠ **WHICH ROW IS OVER WHICH, TOP FIRST — AND IT IS NOT COSMETIC.** Without
    # it every imported row falls into this app's DERIVED order (pictures →
    # shapes → overlays → text), which puts every imported shape ABOVE every
    # imported picture whatever the sequence said: a background card becomes a
    # lid over the whole film. Relative tokens (`frames:0`, `shape:_import_shape_0`)
    # in `lane_order`'s own spelling, re-based by the client exactly as `track`
    # and `layer_id` are. Empty from a reader that cannot say — an EDL — which
    # means "the derived order", the same as an empty `settings.lane_order`.
    lane_order: list[str] = Field(default_factory=list)

    # --- what to SAY about it ----------------------------------------------
    name: str = Field("", description="What the sequence was called in the file.")
    reader: str = Field(
        "",
        description=(
            "'fcp7' | 'edl' | 'prproj' — what it turned out to be. ⚠ 'prproj' is "
            "the BEST-EFFORT read of Premiere's private save file: the first "
            "entry in `warnings` says so, and the dialog must show it."
        ),
    )
    fps: int = Field(24, description="The rate the document was read at.")
    clips: int = 0
    audio_clips: int = 0
    video_tracks: int = 0
    # What to CALL each picture row: "video" | "image", one per row, in row
    # order. ⚠ THE ROWS USED TO BE NAMED AFTER THE FILE — eight rows all reading
    # "8_MCP_Model Context Prot…", which says nothing about any of them and is
    # already on the project. The client turns these into its own row names
    # ("Video", "Images", "Images 2") with the same helper it uses when a user
    # adds a row by hand, so an imported timeline is named like a built one.
    video_lane_kinds: list[str] = Field(default_factory=list)
    audio_lanes: int = 0
    text_lanes: int = 0
    shape_lanes: int = 0
    texts_read: int = 0
    shapes_read: int = 0
    transitions_read: int = 0
    matched: int = Field(0, description="Media files matched to a clip by name.")
    # ⚠ THE TWO HONEST LISTS. `placeholders` names every clip whose file did not
    # arrive — it is on the timeline as a labelled colour card, so the cut is
    # whole and the gaps are visible. `warnings` is everything the reader had to
    # assume (an EDL's frame rate, an NTSC rate read as a whole number, dissolves
    # read as cuts).
    placeholders: list[str] = Field(default_factory=list)
    # ⚠ THE SAME LOSS, ONE ROW PER FILE AND WITH ITS FOLDER — what the dialog
    # actually shows. `placeholders` stays per CLIP because that is what the
    # gaps on the timeline are counted from.
    missing: list[ImportMissingFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(
        default_factory=list, description="Media files that could not be stored."
    )


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
    # Bytes on disk: every panel, upload and rendered file this project
    # owns. 0 means nothing generated yet, and the client draws that the
    # same way as "no folder" — both are "nothing here" to a reader.
    # Measured by `common.dir_bytes`, which caches on the job's
    # `updated_at` so the library's five-second poll doesn't re-walk the
    # whole output folder.
    size_bytes: int = 0
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
    #
    # ⚠ EMPTY MEANS "THIS RENDER IS OF NOTHING ON THE TIMELINE" — the Media
    # pane's ✨ Video, which renders from a prompt (and optionally one still you
    # dropped in) rather than from a clip. That is the only difference between
    # the two, which is why they share this record, one worker, one poll and one
    # "a paid render must never be lost" self-heal. The editor attaches a
    # frame-less render as an ordinary video clip on the Video row instead of
    # over a panel — see `reconcileVeoClips`.
    frame_id: str = ""
    # The still it was rendered FROM, for a frame-less render. "" is a
    # text-to-video render, which Veo does from the prompt alone.
    source_upload_id: str = ""
    # What to call it in the Media library and on the timeline. Only a
    # frame-less render needs one: a render OF a clip takes the clip's label.
    label: str = ""
    # Where the finished MP4 lives — an ordinary video upload id, servable and
    # placeable exactly as a dropped file is.
    upload_id: str = ""
    prompt: str = ""
    status: str = Field("queued", description="'queued' | 'rendering' | 'ready' | 'failed'.")
    error: str = ""
    # What we ASKED Veo for, so nothing downstream has to measure it — the same
    # rule the assembler follows, and for the same reason: there is no ffprobe.
    duration_ms: int = 0
    # ⚠ THE LENGTH THIS RECORD WAS SUBMITTED AT, in seconds, and it is on the
    # RECORD rather than only in the batch's settings because the 🎬 Director
    # renders a mixed batch — a 4-second take and an 8-second one in the same
    # submission. `render_frame_clip` reads it and falls back to the settings,
    # which is what every render made before this field existed did.
    seconds: int = 0
    # Advisory, and recorded per render so a running total is a sum of real
    # charges rather than a re-estimate at today's settings.
    cost_usd: float = 0.0
    rendered_at: str = ""


class AnimaticVideoGenerateRequest(BaseModel):
    """Body for the two /videos/generate endpoints — estimate (free) and render.

    Both take the SAME body, the rule every paid path here follows: the number
    in the confirm dialog can then only be the price of what the button does.

    ⚠ THIS IS THE MEDIA PANE'S ✨ VIDEO, not ✨ Animate. That one animates a clip
    that is already on the timeline and lands over it; this one renders from a
    sentence — with or without a starting still — and lands as an ordinary video
    on the Video row, belonging to nothing.
    """

    prompt: str = Field(..., min_length=1, max_length=2000)
    # The still to start from, already uploaded through `POST /{id}/images`.
    # "" is text-to-video: Veo renders from the prompt alone.
    source_upload_id: str = Field("", max_length=64)
    render: RenderSettings = Field(default_factory=RenderSettings)


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
    # frame_id → 4, 6 or 8. ⚠ A PER-FRAME OVERRIDE OF `render.duration_seconds`,
    # and the 🎬 Director is the one caller that sets it: it picks each take's
    # length from that shot's own hold (the smallest that covers it — see
    # `coverSeconds` in `veo_pass.js`), so one submission is a mixed bag of
    # lengths where ✨ Animate's is not. A frame with no entry here falls back to
    # the settings' own length, which is every caller that existed before this.
    #
    # ⚠ IT IS READ BY `_animate_targets`, WHICH IS WHAT KEEPS THE ESTIMATE AND
    # THE RENDER HONEST. Both endpoints resolve the length in that one function,
    # so the price quoted is the price of the work whatever the lengths are.
    durations: dict[str, int] = Field(default_factory=dict)
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
    # ⚠ WHO ACTUALLY SENDS THE BILL — "Google", "Sarvam", "Deepgram". The confirm
    # dialog's last line used to say "Google bills the actual amount" whatever
    # was switched on, which is a small lie that gets expensive: somebody
    # watching a Google invoice for a run Deepgram charged for concludes the
    # estimate is fiction. Blank = say nothing rather than guess.
    biller: str = ""
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
    # WHAT THE SUBTITLES SHOULD LOOK LIKE — plain caption fields, already
    # resolved by the browser from whichever entry in `text_styles.js` was
    # picked. Empty is the subtitle `caption_clips` has always written.
    #
    # ⚠ A BAG OF FIELDS, NOT A STYLE NAME, AND THAT IS THE DESIGN. The server has
    # no vocabulary of styles and never needs one: the shelf can grow to a
    # hundred looks, or somebody can save their own, without a line changing
    # here. Same bargain the animation presets make about keyframes.
    #
    # ⚠ AND IT IS WHITELISTED IN `captions.clean_style`, NOT HERE. `dict[str,
    # Any]` on a request body would otherwise be a way to write `text`,
    # `start_ms` or `layer_id` onto every clip a PAID run produces. Unknown keys
    # are dropped rather than refused, so a browser one version ahead styles what
    # it can instead of 422-ing in the middle of a transcription.
    style: dict[str, Any] = Field(default_factory=dict)


class VoiceoverLine(BaseModel):
    """ONE LINE AS IT WILL BE READ — who says it, how, and in which shot.

    ⚠ THIS IS BOTH HALVES OF THE DIALOGUE SHEET. The free `GET /dialogue` fills
    it in from the board and the run takes it back, edited, so what the user saw
    in the dialog is exactly what gets read. The alternative — the browser
    sending only "which shots", the server re-reading the board — meant the words
    on screen were a preview of something else, and an edit had nowhere to go.

    `frame_id` is what anchors a line to a moment: the clip it belongs to already
    sits somewhere on the timeline, so a line's place is a lookup and never a
    number the browser has to compute.
    """

    frame_id: str = Field("", description="The clip this line is spoken over.")
    # Who is speaking. Shown in the sheet and prepended to nothing — it exists so
    # the user can tell two lines apart, and so `persona` has something to be
    # guessed from when the sheet is first built.
    character: str = ""
    # WHO THEY SOUND LIKE — a `tts.PERSONAS` key ("boy", "grandmother", …). This
    # is the field that carries an age and a sex to the model at all: it writes
    # the stage direction the line is read with AND casts the default voice. ""
    # means "as it comes", which is the honest answer for an unattributed line.
    persona: str = ""
    # Override the persona's casting for this one line. "" = let the persona
    # choose, then the run's own `voice`.
    voice: str = ""
    text: str = ""


class VoiceOption(BaseModel):
    """One entry in the voice picker: the name, its tone, who it is cast for."""

    name: str
    tone: str = ""
    persona: str = ""


class PersonaOption(BaseModel):
    """One entry in the "who is speaking" picker, and what it does to a line."""

    key: str
    label: str
    voice: str
    # The stage direction this persona prepends. Shown in the dialog as the
    # reason a line will sound the way it does — it is the only visible sign
    # that an age and a sex reached the model at all. ⚠ EMPTY OFF GOOGLE: the
    # other backends take no instruction, so there is nothing to print.
    direction: str = ""
    # ⚠ WHAT THIS BACKEND CANNOT ACTUALLY DELIVER FOR THIS PART. "" is the normal
    # answer and means the casting is what it says it is. Non-empty means the
    # voice is the nearest available thing rather than the thing asked for —
    # Aura publishes no child voices in any language, Sarvam publishes no ages at
    # all, and some of Aura's languages have two voices in total. It is NOT an
    # error: the run is valid, and the user simply deserves to know what they are
    # buying before they buy it. Printed beside the line in the 🎙 dialog.
    note: str = ""


class AnimaticDialogueLine(VoiceoverLine):
    """A sheet line, with the read-only facts the dialog shows beside it."""

    # The clip's label — "Shot 9". What makes the sheet legible as a script
    # rather than as a list of sentences.
    shot: str = ""
    # Where that shot currently starts and how long it currently holds. Both are
    # display-only: the run recomputes them, because reading the line is what
    # decides how long the shot has to hold for.
    start_ms: int = 0
    hold_ms: int = 0


class AnimaticDialogueSheet(BaseModel):
    """FREE. Everything the "Read the dialogue aloud" dialog needs to open.

    ⚠ THE PICKERS COME FROM THE SERVER, and that is not ceremony: the voice list
    used to be six names typed into the JSX, so adding a voice meant editing the
    browser and the model call and hoping they agreed. `tts.CAST` is the one
    place a voice exists now.
    """

    lines: list[AnimaticDialogueLine] = Field(default_factory=list)
    voices: list[VoiceOption] = Field(default_factory=list)
    personas: list[PersonaOption] = Field(default_factory=list)
    # True when this animatic's clips come from a board at all — what the dialog
    # says instead of showing an empty sheet.
    from_board: bool = False
    # --- WHICH BACKEND WILL READ IT (Phase 3: the voiceover can leave Google) --
    # ⚠ THE PICKERS ABOVE ALREADY BELONG TO THIS BACKEND — "Kore" is a Google
    # voice and "ishita" is a Sarvam speaker — so the dialog has to be able to
    # say which one it is showing. `engine` is the model label the estimate will
    # also quote ("bulbul:v3 (hi-IN)"), and it carries the LANGUAGE for Sarvam
    # because that is the setting most likely to be wrong and least likely to be
    # noticed.
    provider: str = ""
    engine: str = ""
    # ⚠ A SOFT WARNING, WHERE `warning` IS A HARD ONE. `warning` means the run
    # cannot happen; this means it can, but some lines will not sound the way
    # their persona says — "2 lines are cast for children, and this backend has
    # no child voice". Counted from the sheet's OWN lines, so a film with no
    # children never sees it.
    advisory: str = ""
    # ⚠ WHY THE RUN WOULD FAIL, SAID BEFORE ANYTHING IS SPENT. "" is the normal
    # answer. Non-empty means this backend cannot read this film at all (Aura
    # does not speak Hindi; Bulbul does not speak Spanish; no key is set) and the
    # sentence names the `.env` line that fixes it. A voiceover is one call PER
    # LINE, so finding this out on line 1 of 40 is a run that has already moved
    # shots and written a track — finding it out here costs nothing.
    warning: str = ""


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
    # ⚠ THE EDITED SHEET, AND IT WINS OVER THE BOARD ENTIRELY when it is sent.
    # The dialog shows the board's own dialogue, the user rewrites a line or
    # re-casts a speaker, and this is what comes back — so the estimate prices
    # the words on screen and the run reads them. Empty = read the board as it
    # stands, which is what every request written before the sheet existed does.
    lines: list[VoiceoverLine] = Field(default_factory=list)
    # MAKE EACH SHOT HOLD ITS OWN LINE. A picture that holds for 2s under a 10s
    # line is the reported bug ("caption and voicerover goes overlap other image
    # shots"): the shot is stretched to cover what is said over it and the shots
    # after it are pushed along, exactly as animating one does. Off leaves every
    # picture where it is and lets a long line run over the next shot.
    fit_shots: bool = True
    # Lay the spoken lines down as captions too, at the times they were ACTUALLY
    # read at (which is not always the time they were asked for — see
    # `_lay_out_speech` in `server/animatics.py`). Free: the timings come back
    # with the audio.
    add_captions: bool = True
    replace: bool = True
    # ⚠ THE SAME FIELD, ON BOTH DOORS THAT WRITE CAPTIONS, AND THAT IS THE POINT.
    # This route lays the spoken lines down as subtitles too, so a look that
    # worked from the ✎ Captions button and not from here would be RULEBOOK
    # E156's shape of bug — nothing errors, nothing is empty, the styling you
    # chose simply is not there. Whitelisted by `captions.clean_style` like the
    # other one; empty is the subtitle this pass has always written.
    style: dict[str, Any] = Field(default_factory=dict)


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
class AnimaticShotWording(BaseModel):
    """WHAT THE BOARD SAYS ONE CLIP IS — the whole timeline, in one read.

    ⚠ THIS EXISTS SO THE FREE PLANNER CAN RENDER. `GET /frames/{id}/panel`
    already answers this for ONE clip and is what ✨ Animate opens its prompt box
    on; the Director's rules planner needs the same sentence for every shot at
    once, and forty-eight calls to that route is forty-eight reads of the same
    board record.

    ⚠ AND `description` IS EMPTY RATHER THAN A LABEL when the board has nothing
    to say about the shot. "Shot 4" is a name, not a description, and a Veo
    render is billed for whatever prompt it is handed — so a clip with no wording
    behind it comes back blank and is REFUSED by `veoShots` with the reason on
    screen, which is the honest answer and the free one.
    """

    frame_id: str
    description: str = ""


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
    # WHAT IS SPOKEN in this shot, straight off the board. Empty for a silent
    # shot, and every consumer treats that as "draw no dialogue block".
    #
    # ⚠ IT IS HERE FOR ✨ ANIMATE, NOT FOR THE REDRAW PANE. The panel prompt is
    # never given the spoken lines — an image model draws words it is told about
    # as speech bubbles (see `Shot.dialogue`) — but Veo can SAY them, so the
    # animate dialog offers them as something the user appends to the motion
    # prompt on purpose. The redraw pane simply ignores this field.
    dialogue: list[DialogueLine] = Field(default_factory=list)
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


# ---------------------------------------------------------------------------
# A SHOT THAT IS NOT ON THE BOARD — "generate a shot before / after this one"
# ---------------------------------------------------------------------------
# Right-click a storyboard clip on the timeline and the missing shot either side
# of it can be drawn and dropped into the cut, pushing everything after it along
# exactly as a Veo take does. Three calls, in the order the dialog makes them:
# read the context (free), suggest the wording (a text call), draw it (an image
# call). The picture lands as an animatic UPLOAD and the BOARD IS NEVER TOUCHED
# — see the note on `AnimaticFrameSource.shot_id` for why that is not a shortcut.
class AnimaticNeighbourShotContext(BaseModel):
    """What the "generate a shot" dialog opens on. Free — no model is called.

    `can_generate` is False with a `reason` for a clip that has no storyboard
    behind it, the same contract `AnimaticPanelSource` follows: the dialog reads
    the reason out instead of offering a button that is going to 400.
    """

    frame_id: str
    # "before" | "after" — which side of the clip the new shot goes on.
    side: str = "after"
    # WHAT TO CALL IT, built here so the name is written once rather than on
    # both sides of the wire — the same rule `AnimaticBoardImportResponse.name`
    # follows. "After Shot 4".
    label: str = ""
    storyboard_id: str | None = None
    title: str = ""
    # The shots this new one would sit BETWEEN, in timeline order — what the
    # suggestion is written from, and what the dialog shows so the user can see
    # what it is being written between.
    before_description: str = ""
    after_description: str = ""
    # The shape to draw in. The board's own ratio is the default; the dialog
    # offers the storyboard's list and adds this one if it isn't on it.
    aspect_ratio: str = "16:9"
    # WHICH MODEL DRAWS IT, resolved from IMAGE_PROVIDER exactly as the draw
    # itself will resolve it. Shown, not chosen: there is one image model and it
    # is set in the environment, so a picker here would be theatre.
    model: str = ""
    provider: str = ""
    can_generate: bool = False
    reason: str = ""


class AnimaticNeighbourSuggestRequest(BaseModel):
    """Body for …/neighbour/suggest — write the missing shot for me.

    A TEXT call, not an image one: it costs a fraction of a draw and nothing is
    drawn until the user has read what it wrote.
    """

    side: str = Field("after", description="'before' | 'after'.")
    # Anything already typed in the box. Steering, not a replacement — the model
    # is told to honour it while still writing a shot that fits between the two.
    notes: str = Field("", max_length=2000)


class AnimaticNeighbourSuggestResponse(BaseModel):
    """The suggested wording. One shot, one sentence or two."""

    description: str = ""


class AnimaticNeighbourShotRequest(BaseModel):
    """Body for …/neighbour — SPENDS QUOTA. Draw this shot.

    `duration_ms` is how long the new clip HOLDS on the timeline, not anything
    about the image: the picture is a still and the dialog is where its length
    in the cut is decided.
    """

    side: str = Field("after", description="'before' | 'after'.")
    description: str = Field(..., min_length=1, max_length=2000)
    # "" = the board's own shape, which is what the dialog opens on.
    aspect_ratio: str = Field("", max_length=16)
    duration_ms: int = Field(8000, ge=100, le=600_000)


class AnimaticNeighbourShotResponse(BaseModel):
    """The drawn shot, as a CLIP the client can put on the timeline.

    ⚠ IT IS RETURNED, NOT SAVED, exactly as the image/video uploads and the
    board import are: the server makes the material and the client decides where
    in the cut it goes — which here means "beside the clip you right-clicked,
    pushing the rest of the film along".
    """

    frame: AnimaticFrame
    model: str = ""


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


# --- 🎬 THE DIRECTOR (server/director.py) ----------------------------------
# ⚠ THE BOARD AND THE VOCABULARY ARE SENT BY THE BROWSER, and both are
# deliberately loose here. The reason is written at the top of `director.py`: the
# capability manifest is DERIVED on the client from the tables the renderers
# read, and a Pydantic model of it in this file would be a second, hand-written
# answer to "what can this build do" that is right today and wrong the first time
# a transition is added. The same argument applies to the board — the editor's
# live document is ahead of the last autosave, so the plan has to be written
# against what is on SCREEN, not against what is in the store.
#
# Nothing here is trusted. `director.fold_steps` throws away every argument the
# named verb does not take, and the client's own `validatePlan` +
# `applyGuardrails` are still the door the plan comes through.
class DirectorPlanRequest(BaseModel):
    """Body for POST /director/{job_id}/plan — write me an edit for this board."""

    # What the user typed in the 🎬 popup. Empty is normal and fine.
    brief: str = Field("", description="What the film is / what they want, in their words.")
    # The project's language. ⚠ SAVED ONTO THE PROJECT by this call — see the
    # note on AnimaticSettings.language on why it belongs to the film.
    language: str = Field("", description="Blank = leave the project's setting alone.")
    # Which treatments this run may touch. Keys are INCLUDE_KEYS in plan_schema.js.
    include: dict = Field(default_factory=dict)
    # The live timeline: {title, aspect_ratio, fps, total_ms, shots:[…], existing:{…}}.
    board: dict = Field(default_factory=dict)
    # `capabilities()` from client/src/animatic/agent/capabilities.js.
    capabilities: dict = Field(default_factory=dict)


class DirectorPlanResponse(BaseModel):
    """An edit plan, the reading it came from, and what could not be used."""

    provider: str = ""
    model: str = ""
    # The EditPlan — the same shape `house_style.housePlan` produces, and it goes
    # through the same two doors on the way to the timeline.
    plan: dict = Field(default_factory=dict)
    # The story reading: logline, mood, genre, scenes, per-shot beats.
    analysis: dict = Field(default_factory=dict)
    # ⚠ WRITTEN NOW, SPENT LATER. The per-shot Veo motion prompts, in English,
    # with the shot's dialogue beside them in the film's own language. NOTHING IN
    # THIS ENDPOINT RENDERS ANYTHING — see `cost` for what a Veo pass would run to.
    veo: list[dict] = Field(default_factory=list)
    # ⚠ WRITTEN NOW, FETCHED LATER, AND THEY COST NO MONEY AT ALL. `sfx` is
    # `[{shot, query}]` and `music` is `{query, mood, why}` — stock-library SEARCH
    # TERMS, in English, for phases D and E to look up (`sound_pass.js`). Nothing
    # in this endpoint touches the sound library; that is
    # `POST /animatics/{id}/soundtrack`, which spends the deployment's shared
    # Freesound request budget and nobody's cash.
    sfx: list[dict] = Field(default_factory=list)
    music: dict = Field(default_factory=dict)
    # Every step thrown away before the browser saw it, with a reason.
    dropped: list[dict] = Field(default_factory=list)
    # What the reading wanted the editor to know — assumptions, gaps, doubts.
    notes: list[str] = Field(default_factory=list)
    # FREE, ADVISORY quote for the Veo pass these prompts describe. Shown in the
    # preview so a plan can be read before anything is spent; this endpoint
    # spends only text quota.
    cost: CostEstimate = Field(default_factory=CostEstimate)


# --- The Director's Veo pass (Phase 4) --------------------------------------
# ⚠ THE MOST EXPENSIVE THING THE 🎬 BUTTON CAN DO. A 48-shot board is four
# submissions of twelve and roughly $46, and the whole point of these three
# models is that a run which dies halfway through can be picked up again without
# paying for the half already bought.
#
# ⚠ THE RECORD SAYS WHAT WAS INTENDED; `veo_clips` SAYS WHAT WAS PAID FOR, and
# the two are deliberately not the same object. A resume reads the intention off
# this and the truth off those — see `outstanding` in `veo_pass.js`. Writing the
# progress into this record and trusting it would mean trusting a counter that a
# crashed process was in the middle of updating.
class AnimaticDirectorShot(BaseModel):
    """One shot the Director means to render, with the length it chose and why."""

    # 1-based position in the SHOT ROW (takes excluded — see `shotRow`) at the
    # moment the run started. Kept for the log and the panel; `frame_id` is what
    # anything acts on.
    shot: int = 0
    frame_id: str = ""
    label: str = ""
    prompt: str = ""
    # 4, 6 or 8 — the smallest Veo length that covers `hold_ms`. The policy and
    # its reasoning are in the header of `veo_pass.js`.
    seconds: int = 8
    # What the shot held when that length was chosen, so the panel can say "4.0s
    # over a 2.4s hold" rather than leaving the growth unexplained.
    hold_ms: int = 0


class AnimaticDirectorRun(BaseModel):
    """A 🎬 Veo pass, as a SERVER-OWNED record. Survives a refresh and a crash.

    ⚠ Lives in the job's `result` beside `veo_clips`, never in `params`, for
    exactly the reason `AnimaticVeoClip` gives: the editor's autosave rewrites
    `params` wholesale, so a run recorded there would be erased by a save that
    started before it finished — and with it the only statement of what the user
    agreed to pay for.
    """

    id: str = ""
    started_at: str = ""
    # 'running' | 'done' | 'stopped' | 'failed'. ⚠ 'running' is what makes the
    # editor offer to resume on the next load; nothing else does.
    status: str = "running"
    # The whole intention, written ONCE at the start and never rewritten. A run
    # that is resumed is the same run — see `outstanding` for how far it got.
    shots: list[AnimaticDirectorShot] = Field(default_factory=list)
    render: RenderSettings = Field(default_factory=RenderSettings)
    # `config.MAX_VIDEO_BATCH` as it stood when the run was quoted, so a resume
    # against a re-configured server still describes the passes the user saw.
    batch: int = 0
    # What the whole pass was quoted at, before a penny of it was spent. The sum
    # of the per-pass quotes, never a second calculation — see `_quote_veo_shots`.
    quoted_usd: float = 0.0
    error: str = ""


class DirectorVeoRequest(BaseModel):
    """Body for the three /director/{id}/veo routes. All three are FREE.

    ⚠ NOT ONE OF THESE SPENDS ANYTHING. The quote is arithmetic, the start writes
    a record, the state writes a status — the money is spent by
    `POST /animatics/{id}/animate`, one pass at a time, which is the door every
    other paid render in this editor already goes through.
    """

    shots: list[AnimaticDirectorShot] = Field(default_factory=list)
    render: RenderSettings = Field(default_factory=RenderSettings)
    # For /veo/state only: which run is being reported on, and how it ended.
    run_id: str = ""
    status: str = ""
    error: str = ""


class DirectorVeoQuote(BaseModel):
    """What a Veo pass would cost, broken down the way it will be submitted.

    ⚠ `total` IS THE SUM OF `passes`, TO THE PENNY, and it is computed that way
    rather than quoted separately. Two roundings of the same shot list disagree
    by a cent often enough that a user watching four passes add up to something
    other than the number they agreed to would be right not to trust either.
    `tests/director_chunk_check.py` asserts the identity.
    """

    batch: int = 0
    passes: list[CostEstimate] = Field(default_factory=list)
    total: CostEstimate = Field(default_factory=CostEstimate)


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


# ---------------------------------------------------------------------------
# The sound library (Freesound)
# ---------------------------------------------------------------------------
# ⚠ THE LICENCE FIELDS ARE NOT DECORATION. A sound carries its licence into the
# customer's exported video, so `license`, `needs_credit` and `attribution` are
# the difference between a legal advert and an infringing one. They are filled
# by `freesound.py` — the whitelist there is what guarantees "Attribution
# NonCommercial" never reaches this shape at all — and `attribution` is carried
# onto the media-library card (`AnimaticAsset.attribution`) so the credit
# survives the import and can still be printed months later.


class SoundSearchItem(BaseModel):
    """One sound as the Sounds tab draws it."""

    id: str
    name: str = ""
    username: str = ""
    # The SOURCE's natural length, same units and same meaning as
    # `AnimaticAsset.duration_ms`, so an imported card needs no conversion.
    duration_ms: int = 0
    # "cc0" | "by" — our codes, never Freesound's prose. See `freesound.LICENCES`.
    license: str = "cc0"
    # ⚠ TWO STRINGS, TWO JOBS. `license_name` is the licence as it belongs in a
    # CREDIT ("CC BY 4.0"); `license_label` is the BADGE on the card and ends in
    # the obligation ("CC BY 4.0 - credit required"). Putting the badge in a
    # credit publishes "credit required" to the viewer of the finished video.
    license_name: str = ""
    license_label: str = ""
    license_url: str = ""
    # Whether USING this sound obliges the customer to credit somebody. False for
    # CC0, true for CC BY; the UI badges the card from this and nothing else.
    needs_credit: bool = False
    # The ready-to-print credit line. Built once in `freesound.credit_line`.
    attribution: str = ""
    page_url: str = ""
    # Freesound's own CDN, played by the browser directly. Not proxied: see the
    # note in `freesound._normalise` about the 60-requests-a-minute budget.
    preview_url: str = ""
    waveform_url: str = ""
    tags: list[str] = Field(default_factory=list)


class SoundSearchResponse(BaseModel):
    """Returned from GET /sounds/search."""

    items: list[SoundSearchItem] = Field(default_factory=list)
    page: int = 1
    page_size: int = 24
    # ⚠ THE SIZE OF THE SEARCH, NOT OF `items`. Results whose licence we do not
    # recognise are dropped after Freesound counted them, so these two numbers
    # legitimately disagree — and paging has to follow this one.
    total: int = 0
    has_next: bool = False
    licence: str = "safe"


class SoundStatus(BaseModel):
    """Whether the sound library is switched on for this deployment.

    ⚠ IT NEVER CARRIES THE KEY. `configured` is a yes/no; §B.4 of the Freesound
    API terms requires the token to stay on the server. The client uses this only
    to decide whether to draw the Sounds tab.
    """

    configured: bool = False
    # The licence buckets the search box may offer, newest-safest first.
    licences: list[str] = Field(default_factory=list)
    sorts: list[str] = Field(default_factory=list)
    provider: str = "freesound"
    # Said on screen, once, under the search box: what the user is agreeing to by
    # putting one of these files in a video they sell.
    notice: str = ""


# --- The Director's soundtrack pass (phases D and E) ------------------------
# ⚠ ONE REQUEST FOR A WHOLE FILM'S SOUND, and the reason is the audio cap. Eleven
# separate imports from the browser would each check "is there room for one more
# file" against a project that the previous ten have already grown, so the tenth
# would be refused for a reason the user could do nothing about and the run would
# report ten different failures. Taking the list means the cap is arithmetic done
# ONCE, against what the project holds now, and the answer says exactly what
# landed and what did not.
#
# ⚠ AND THE SEARCH IS CC0-ONLY HERE, unlike the Sounds tab. See the long note at
# the top of `client/src/animatic/agent/sound_pass.js`: a person picking a sound
# by hand can read the "credit needed" badge and accept the obligation; a pass
# that files eleven sounds while they watch a progress line cannot ask, so it
# takes only the licence that obliges nobody.


class SoundCueRequest(BaseModel):
    """One sound to find — a search term, never a sound id."""

    # ⚠ THE CALLER'S OWN KEY, ECHOED BACK UNCHANGED. The browser has already
    # decided which shots this cue belongs to (`sfxCues` dedupes by it), and it is
    # the only thing that lets the answer be matched to the placements without the
    # server having to know anything about shots.
    key: str = Field("", description="Opaque to us. Echoed back on the result.")
    query: str = Field(..., description="What to search the sound library for. English.")
    kind: str = Field("sfx", description="'sfx' | 'music' — only which filter to use.")
    # A sound effect is short and a music bed is not, and the difference is a
    # filter on the search rather than a rule about the file.
    max_seconds: float = Field(0, ge=0, le=3600)
    min_seconds: float = Field(0, ge=0, le=3600)


class SoundtrackRequest(BaseModel):
    """POST /animatics/{id}/soundtrack — find and file a film's worth of sound."""

    sounds: list[SoundCueRequest] = Field(default_factory=list)


class SoundtrackItem(BaseModel):
    """One cue that found something, as an ordinary audio upload.

    ⚠ SAME FIELDS AS `SoundImportResponse` PLUS THE `key`, deliberately: a sound
    this pass filed in IS an audio upload, indistinguishable on disk and in the
    editor from a dropped mp3. The `key` is the only thing that makes it a CUE.
    """

    key: str = ""
    query: str = ""
    # ⚠ EMPTY WHEN THE CUE WAS FOUND AS WRITTEN, which is the usual case. Set to
    # the words actually searched for when the cue only turned something up on the
    # wider second attempt — "feather rustle" for a cue of "light feather rustle",
    # or "any length" when only the duration filter had to go. The panel prints
    # it, because a sound found by answering a different question is not the sound
    # the preview promised and the user has to be able to see that.
    relaxed_to: str = ""
    kind: str = "sfx"
    upload_id: str
    filename: str = ""
    url: str
    duration_ms: int = 0
    attribution: str = ""
    license: str = "cc0"
    license_label: str = ""
    needs_credit: bool = False
    page_url: str = ""


class SoundtrackResponse(BaseModel):
    """What landed, and — just as importantly — what did not and why.

    ⚠ A CUE THAT FOUND NOTHING IS NOT AN ERROR. Ten of eleven cues landing is a
    film with sound in it; refusing the request because one search came back empty
    would be a pass that fails whenever a model writes an unusual word. So every
    failure is a row in `skipped` with a reason written for a human, and the panel
    prints them under the run.
    """

    items: list[SoundtrackItem] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)
    # How many audio FILES the project may still take after this call. The editor
    # shows it when a pass had to stop early, so "why did only six land" has an
    # answer on screen.
    room_left: int = 0


class SoundImportRequest(BaseModel):
    """POST /animatics/{id}/sounds — file one Freesound sound into a project."""

    # ⚠ AN ID, NOT A URL. The server re-asks Freesound for the download location
    # rather than fetching whatever the browser hands it; see `freesound.sound`.
    sound_id: str = Field(..., description="The Freesound sound id, digits only.")


class SoundImportResponse(BaseModel):
    """The imported sound, as an ordinary audio upload plus its licence.

    ⚠ SUPERSET OF `AnimaticAudioResponse`, deliberately: an imported sound IS an
    audio upload — same `audio_<id>.mp3` on disk, same serve route, same
    `addAudioTrack` in the editor — and the only thing that makes it different is
    the paperwork it arrived with. One extra field rather than a second pipeline.
    """

    upload_id: str
    filename: str = ""
    url: str
    duration_ms: int = 0
    attribution: str = ""
    license: str = "cc0"
    license_label: str = ""
    needs_credit: bool = False
    page_url: str = ""


# ===========================================================================
# The ✨ AI Editor chat  (server/editor_chat.py)
# ===========================================================================
# ⚠ THE TRANSCRIPT AND THE BOARD BOTH RIDE ON EVERY TURN, and neither is
# stored. Same statelessness as `/script-chat`, and here the reason is stronger:
# the thing being discussed is the TIMELINE, which the editor autosaves anyway.
# A remembered conversation plus a re-sent document is two sources of truth about
# one film, and the remembered one is always the stale one.


class EditorChatMessage(BaseModel):
    """One turn of the editor chat.

    Same `role` vocabulary as `ScriptChatMessage` — "user" / "agent", ours rather
    than any SDK's — so a transcript is readable on the wire and in the browser's
    storage. Deliberately NOT reusing that class: it caps text at 8000 for a
    script conversation, and a reply here is capped much shorter because the panel
    it is drawn in is a narrow rail.
    """

    role: str = Field("user", pattern="^(user|agent)$")
    text: str = Field(..., min_length=1, max_length=4000)


class EditorChatOption(BaseModel):
    """One answer offered by an `ask`.

    `note` is the half-line under the chip saying what this choice would MEAN.
    It is what turns three nouns into a decision somebody can actually make.
    """

    id: str = ""
    label: str = ""
    note: str = ""


class EditorChatAsk(BaseModel):
    """⭐ THE QUESTION, WITH OPTIONS — the reply kind this whole feature exists for.

    ⚠ `allow_other` IS ALWAYS TRUE AND IS NOT READ OFF THE MODEL. "If not these
    then what" is the point; a closed list of options is a form, and this is a
    conversation. It is on the wire so the panel has one place to read it from
    rather than hard-coding the affordance.

    `reason` is which of the three triggers fired — target / spend / destructive.
    Advisory, and worth having: it is the only way to see whether the rails are
    firing where they were meant to.
    """

    question: str = ""
    reason: str = ""
    options: list[EditorChatOption] = Field(default_factory=list)
    allow_other: bool = True


class EditorChatPicture(BaseModel):
    """One shot's picture, on its way to a model that can see it.

    ⚠ **BASE64 IN A JSON BODY, NOT A MULTIPART UPLOAD.** These are a few hundred
    pixels each and they are gone the moment the turn is answered — nothing is
    written to disk, nothing is added to the project. A multipart route would be
    a second way into this feature for the sake of a payload smaller than the
    board that travels beside it.
    """

    shot: int = Field(..., ge=1)
    mime: str = "image/png"
    data: str = ""


class EditorChatRequest(BaseModel):
    """Body for POST /editor-chat/{job_id}/turn.

    ⚠ THE BOARD COMES FROM THE BROWSER, NOT FROM THE STORE, exactly as it does
    for `POST /director/{id}/plan`, and for the same reason: the editor autosaves,
    so the saved project is behind whatever the user has just done. A reply
    written against the store would describe a film one edit stale.
    """

    messages: list[EditorChatMessage] = Field(..., min_length=1, max_length=60)
    # The read-model, from `boardFrom(ctx)` in the browser.
    board: dict = Field(default_factory=dict)
    # The capability manifest, from `capabilities()`. ⚠ SENT RATHER THAN BUILT
    # HERE: the truth about what this build can render lives in the renderer
    # tables the client reads, and a server-side copy would be a second opinion.
    capabilities: dict = Field(default_factory=dict)
    language: str = ""
    # ⚠ THE PICTURES OF A LOOK, SENT BY THE BROWSER — `[{shot, mime, data}]` with
    # `data` base64. The browser sends them rather than the server reading them
    # off disk for two reasons: this router deliberately does not import
    # `animatics.py` (see its docstring), and the browser already HAS every
    # picture, proxied down to a few hundred pixels for the monitor. Asking the
    # disk for a 1920px PNG to shrink it again would be slower and no truer.
    #
    # ⚠ CAPPED IN THE MODEL, NOT IN THE HANDLER. A hand-rolled POST with 400
    # stills on it is a bill, and a request that is refused at the door has cost
    # nobody a model call.
    look: list[EditorChatPicture] = Field(default_factory=list, max_length=12)


class EditorChatResponse(BaseModel):
    """One turn back.

    ⚠ `kind` IS A HINT, NOT A CONTRACT. The client re-derives it from what is
    actually present (`normaliseTurn` in `chat_turn.js`) because a reply labelled
    `plan` whose every step was dropped is not a plan, and drawing an Apply button
    over zero edits is the worst lie a panel can tell.

    ⚠ `plan` IS THE SAME SHAPE THE DIRECTOR RETURNS and goes through the same
    `validatePlan` → `useDirectorRun` path on the other side. Nothing here has
    edited anything: a plan is a proposal until the user presses Apply.
    """

    kind: str = "answer"
    reply: str = ""
    ask: EditorChatAsk | None = None
    plan: dict | None = None
    # ⚠ SOUND RIDES BESIDE THE PLAN, NOT INSIDE IT, because it is not a verb:
    # every verb in the registry is synchronous and finding a sound is a round
    # trip to a stock library. The client runs the existing sound pass AFTER the
    # steps, for the same reason the Director's phases D and E come last — a cue
    # lands on a moment, and the steps have just finished moving the moments.
    # `{sfx: [{shot, query}], music: {query, mood} | null}` or null.
    sound: dict | None = None
    # ⚠ PAID WORK THE CHAT IS OFFERING — `[{door, why, shot?}]`, never a price.
    # The chat cannot start any of it: each entry becomes a BUTTON in the panel
    # that opens the priced door the editor already has (✨ Animate, 🎙 Voiceover,
    # 🖼 Animatic images), and that door is what asks the server what it costs.
    # A price computed here would be a second opinion about money sitting next to
    # the one that charges — see `PAID_DOORS` in `editor_chat_agent.py`.
    passes: list[dict] = Field(default_factory=list)
    # ⚠ "I NEED TO SEE FIRST" — `{shots: [n], why}` or null. Not an edit and not
    # an answer: the browser fetches those shots' pictures and asks the SAME
    # question again with them attached. One look per message; see
    # `MAX_LOOK_SHOTS` in `editor_chat_agent.py` for why it is a request rather
    # than something sent on every turn.
    look: dict | None = None
    # ⚠ A BIG JOB THAT IS NOW RUNNING — not an answer, and NOT something to
    # draw an Apply button over. `work_id` is the job to poll; `work` is the
    # brief, so the panel can say WHAT is being done while it waits
    # ("transitions, then sound") rather than showing a bar with no label on
    # it. Both are null on every ordinary turn, which is what keeps the fast
    # path exactly as fast as it was. See `server/editor_chat_work.py`.
    work_id: str | None = None
    work: dict | None = None
    # Steps the server could not read, so the panel can say "2 steps couldn't be
    # used" rather than quietly showing a shorter plan. The client adds its own.
    dropped: list[dict] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    # `(used, limit)` for this account's `chat_turns` after this turn. `limit` is
    # None for unlimited. Sent so the panel can warn BEFORE the refusal rather
    # than after it — a quota you discover by being blocked is a bad surprise.
    turns_used: int = 0
    turns_limit: int | None = None


class EditorChatSessionSummary(BaseModel):
    """One row of the 🕘 list — a chat WITHOUT its transcript.

    ⚠ THE LIST IS ITS OWN SHAPE, NOT A CHAT WITH THE TURNS LEFT OUT. Forty
    conversations of sixty turns is megabytes to draw a dozen titles, and a
    panel that opened one of THOSE would be reading "no messages" off a chat it
    simply had not loaded. Two shapes, so the difference cannot be mistaken.
    """

    session_id: str = ""
    # Empty until the first message names it — see `titleFor` in the browser.
    title: str = ""
    # ⚠ TRUE ONCE A PERSON HAS RENAMED THIS CHAT BY HAND. Sent so a panel that
    # has just been reloaded still knows not to let the first line of the chat
    # rename it back. The refusal itself is the server's — see `save_session`.
    title_locked: bool = False
    # How many times the PERSON spoke. What the list is really saying is "was
    # anything done in here", and the agent's own replies do not answer that.
    turn_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class EditorChatSessionList(BaseModel):
    """GET /editor-chat/{job_id}/sessions — this project's chats, newest first."""

    sessions: list[EditorChatSessionSummary] = Field(default_factory=list)
    # The ceiling, sent so the panel can say "40 of 40" rather than only
    # discovering the limit by being refused at the ＋ button.
    limit: int = 0


class EditorChatWorkStatus(BaseModel):
    """GET /editor-chat/work/{id} — how a big job is going, and its answer.

    ⚠ **`turn` IS AN ORDINARY TURN AND THAT IS DELIBERATE.** When the job lands,
    what comes back here is the same shape `/turn` returns for a small edit, so
    the panel feeds it to the same `normaliseTurn`, the same `validatePlan`, the
    same preview and the same Apply. A big job is not a second kind of edit with
    a second set of rules; it is the same plan, written faster.
    """

    work_id: str
    # running | done | failed | lost
    # ⚠ `lost` IS A REAL ANSWER, NOT AN ERROR CODE. A job whose server restarted
    # mid-flight has no runner and will never finish, and a progress bar that
    # never moves again is the worst of the three things this could say.
    state: str
    done: int = 0
    total: int = 0
    percent: int = 0
    message: str = ""
    # Set once `state == "done"`. The finished turn — reply, plan, sound.
    turn: dict | None = None
    error: str | None = None
    # True when the person pressed Stop and the plan is what had been written by
    # then. Still applicable, and the reply says so.
    stopped: bool = False


class EditorChatSession(BaseModel):
    """One whole chat, transcript and all.

    ⚠ `turns` IS THE BROWSER'S OWN TURN SHAPE, STORED AS IT ARRIVES. The server
    never reads inside a turn and never sends one to a model — this is a record,
    not a memory (see `chat_sessions.py`). Typing it here would put a second,
    lagging definition of a turn in a second language, and the day they disagreed
    the store would start dropping fields the panel needs.
    """

    session_id: str = ""
    title: str = ""
    # True once a person has renamed this chat by hand — see the summary above.
    title_locked: bool = False
    turns: list[dict] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class EditorChatSessionCreate(BaseModel):
    """Body for POST /editor-chat/{job_id}/sessions — start a new chat.

    `turns` is here for ONE case and it is not a nicety: the editor opens on a
    project that does not exist yet, so the first message is what creates it.
    Those turns already happened in the browser before there was anything to
    save them against, and this is how they arrive rather than being lost.
    """

    title: str = ""
    # See `EditorChatSessionUpdate.title_auto` — a chat is born with an
    # automatic title, so a create says so too rather than being born locked.
    title_auto: bool = False
    turns: list[dict] = Field(default_factory=list)


class EditorChatSessionUpdate(BaseModel):
    """Body for PUT /editor-chat/{job_id}/sessions/{session_id}.

    ⚠ BOTH FIELDS DEFAULT TO `None`, AND THAT IS THE POINT — `None` means
    "leave it alone", `[]` means "it is empty". A rename sends a title and no
    turns; an autosave sends turns and no title. Defaulting either to its empty
    value would make renaming a chat wipe its transcript.
    """

    title: str | None = None
    # ⚠ "THIS TITLE WAS MADE FROM THE FIRST LINE, NOT CHOSEN BY A PERSON."
    # The autosave sets it; the rename box does not. A chat that has been
    # renamed by hand refuses every automatic title after that, in the store —
    # see `chat_sessions.save_session`. Defaulting to False is the safe half:
    # an old client that never sends it is treated as renaming by hand, which
    # keeps a name rather than losing one.
    title_auto: bool = False
    turns: list[dict] | None = None


class EditorChatConfig(BaseModel):
    """GET /editor-chat/config — what the editor needs before it draws the panel.

    ⚠ IT IS NOT THE ADMIN PAYLOAD. An ordinary user is told where the panel opens
    and what to greet them with; they are not told which model is wired up or
    what the operator's spend rails are set to. `chat_settings.admin_payload()` is
    the other half and lives behind `require_admin`.
    """

    enabled: bool = True
    dock: str = "right"
    # How solid the panel is drawn, 40–100. ⚠ A PERCENTAGE, NOT A FRACTION — it
    # goes straight into a CSS custom property the stylesheet multiplies by 1%,
    # and a 0.6 arriving where 60 was expected is a panel nobody can see.
    opacity: int = 100
    # How far the film underneath is blurred, in px, 0–40. ⚠ ONLY APPLIED WHEN
    # `opacity` is below 100 — a solid panel has nothing behind it to blur, and
    # `backdrop-filter` is a per-frame cost over a playing timeline.
    blur: int = 0
    greeting: str = ""
    max_turns_per_session: int = 0
    transcript_keep: int = 20
    # ⚠ HOW OFTEN THE PANEL SHOULD ASK A RUNNING BIG JOB HOW IT IS GOING, in
    # milliseconds. Sent rather than hard-coded for the same reason the timeout
    # above is: a batch takes about as long as the operator's own clock allows,
    # so a deployment with a slower model should be polled less often, not have
    # a constant in the browser guess at it.
    work_poll_ms: int = 1500
    # ⚠ HOW OFTEN THE PANEL SHOULD ASK A RUNNING BIG JOB HOW IT IS GOING, in
    # milliseconds. Sent rather than hard-coded for the same reason the timeout
    # above is: a batch takes about as long as the operator's own clock allows,
    # so a deployment with a slower model should be polled less often, not have
    # a constant in the browser guess at it.
    work_poll_ms: int = 1500
    # ⚠ HOW LONG THE TAB WAITS FOR ONE TURN, IN MILLISECONDS — and it is SENT
    # rather than hard-coded because the operator can now change the model's
    # clock from the admin panel, and these two numbers must never come apart.
    # The browser keeps its own constant as the floor for the moment before this
    # response has arrived; see `CHAT_TURN_TIMEOUT_MS` in `client/src/api.js`.
    # Derived, never typed: `chat_settings.wire_wait_seconds()`.
    turn_timeout_ms: int = 150_000
    turns_used: int = 0
    turns_limit: int | None = None
