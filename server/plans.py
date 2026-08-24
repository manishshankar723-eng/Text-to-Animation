"""
plans.py — the "/plans" router: Plan & Script.

A planning session is a conversation with the strategist agent plus the calendar
it produced. It is stored as a normal job (`JobKind.PLAN`), so it inherits
owner-scoping, listing, renaming and deletion from the same store everything
else uses — see the Storage rule in AGENTS.md.

Shape of `job.params`:
    title      — what the session is called in the library
    messages   — [{role: "user"|"agent", text, at}] the whole transcript
    channel    — the YouTube research result (see youtube_research.py), or {}
    plan       — the structured calendar from plan_agent.generate_plan(), or {}
    scripts    — [{id, …}] every script written here, newest first; see
                 plan_agent.write_script
    usage      — the session's running TOKEN total (see ai_usage.Usage)

Spends TEXT quota only — this workflow never generates an image.

⚠ **EVERY ROUTE THAT CALLS A MODEL MUST GO THROUGH `_record_usage`.** The
session total is only trustworthy because it is the sum of every call actually
made, retries included. A route that spends quota and forgets to add it makes
the number on screen quietly wrong, which is worse than not showing one.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .auth import CurrentUser, get_current_user
# ⚠ THE GUARD THAT ACTUALLY TURNS A FEATURE OFF. The sidebar reading the same
# registry is cosmetic — anyone can call these routes directly. See features.py.
from .features import require_feature
# ⚠ THE QUOTA GUARD SITS BESIDE `require_feature`, ON THE SAME ROUTES. A limit
# checked AFTER the work is a limit that bills the customer for the call telling
# them they are over. See server/usage.py.
from .usage import require_quota
from . import usage as usage_counters
from .jobs import get_store
from .schemas import (
    JobKind,
    JobStatus,
    PlanChannelRequest,
    PlanChatRequest,
    PlanCreateRequest,
    PlanDetail,
    PlanGenerateRequest,
    PlanRenameRequest,
    PlanScriptRequest,
    PlanSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])

# A transcript is cheap to store but not free to send to the model on every
# turn, and an unbounded one eventually costs real money per message.
MAX_MESSAGES = 200

# Scripts are much bigger than messages and a session accumulates them slowly.
# The cap is a runaway guard, not a working limit.
MAX_SCRIPTS = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_usage(params: dict, spent, email: str = "") -> dict:
    """Add one call's tokens to the session total, in place. Returns `params`.

    Takes the usage dict straight off whatever plan_agent returned, so the
    caller never has to know the shape — only to remember to call this.

    ⚠ AND IT IS ALSO THE ACCOUNT-LEVEL SINK. Every model-calling route in this
    router already goes through here, so hooking the per-account monthly counter
    in at this one point means there is no second list of routes to keep in step
    — which is the same reason the docstring at the top of this file insists
    every such route calls this function at all. `email` is optional so an older
    caller keeps working; it simply does not contribute to the monthly total.
    """
    from ai_usage import merge
    from . import usage as usage_counters

    total = merge(params.get("usage"), spent)
    params["usage"] = total.as_dict()
    if email:
        usage_counters.record_tokens(email, merge(None, spent))
    return params


# ---------------------------------------------------------------------------
# Naming a session from its opening message
# ---------------------------------------------------------------------------
# A session used to be named with the first 60 characters of whatever the user
# typed, which put a whole sentence on the library card — "I run a YouTube
# channel about myt…". A name should be a NAME: a few words about the subject.
#
# So: take the first sentence, drop the filler people open a request with, drop
# leading articles, and keep a handful of words. "I run a YouTube channel about
# mythology. Plan my next 3 months." becomes "YouTube channel about mythology".
MAX_TITLE_WORDS = 5
MAX_TITLE_CHARS = 42

# Openings that describe the REQUEST rather than the subject.
_TITLE_FILLER = re.compile(
    r"^(?:hi|hello|hey|ok|okay)?[\s,.!]*"
    r"(?:"
    r"i\s+(?:run|have|own|make|do|need|want|manage|post|edit|create|shoot|design|sell|teach)\b|"
    r"i'?m\b|i\s+am\b|i'?d\s+like\b|i\s+would\s+like\b|"
    r"can\s+you\b|could\s+you\b|please\b|help\s+me\b|"
    r"plan\s+(?:me|my|out)\b|give\s+me\b|make\s+me\b|create\s+me\b|"
    r"build\s+me\b|write\s+me\b|suggest\b"
    r")\s*",
    re.I,
)
# Words that shouldn't start a name.
_TITLE_LEADING_STOP = {"a", "an", "the", "some", "my", "our", "for", "to", "of", "about", "on"}


def _short_title(text: str) -> str:
    """A few words naming the subject, from the user's opening message."""
    # Strip filler FIRST, then cut the clause. The other order truncates
    # "Hi, can you please plan my 6 months…" to "Hi" — the comma that ends the
    # greeting is not the end of the subject.
    stripped = (text or "").strip()
    previous = None
    while previous != stripped:
        previous = stripped
        stripped = _TITLE_FILLER.sub("", stripped).strip()

    # A comma ends the clause too: "I run a mythology channel, plan me 3 months"
    # is about the channel, not about the request that follows it.
    first = re.split(r"[.!?\n,;]", stripped, maxsplit=1)[0].strip()

    words = first.split()
    while words and words[0].lower().strip(",;:'\"") in _TITLE_LEADING_STOP:
        words.pop(0)

    title = " ".join(words[:MAX_TITLE_WORDS]).strip(" ,;:-—")
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    if not title:
        # Nothing usable (all filler, or emoji) — fall back to the raw opening.
        title = (text or "").strip()[:MAX_TITLE_CHARS] or "Untitled plan"
    return title[0].upper() + title[1:] if title else "Untitled plan"


def _get_owned_plan(job_id: str, current: CurrentUser):
    """A planning session the caller owns, or 404. Never leaks another user's."""
    job = get_store().get(job_id)
    if job is None or job.owner != current.email or job.kind != JobKind.PLAN:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return job


def _detail(job) -> PlanDetail:
    p = job.params or {}
    return PlanDetail(
        job_id=job.job_id,
        title=job.character_name or "Untitled plan",
        messages=p.get("messages") or [],
        channel=p.get("channel") or {},
        plan=p.get("plan") or {},
        scripts=p.get("scripts") or [],
        usage=p.get("usage") or {},
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _summary(job) -> PlanSummary:
    p = job.params or {}
    plan = p.get("plan") or {}
    channel = p.get("channel") or {}
    usage = p.get("usage") or {}
    return PlanSummary(
        job_id=job.job_id,
        title=job.character_name or "Untitled plan",
        message_count=len(p.get("messages") or []),
        item_count=len(plan.get("items") or []),
        script_count=len(p.get("scripts") or []),
        months=int(plan.get("months") or 0),
        channel_title=channel.get("title") or "",
        tokens=int(usage.get("total") or 0),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _get_script(job, script_id: str) -> tuple[list[dict], int]:
    """(all scripts, index of `script_id`), or 404.

    Returns the list too because every caller that finds a script is about to
    write the list back, and re-reading it would be a second chance to read a
    different one.
    """
    scripts = list((job.params or {}).get("scripts") or [])
    for i, s in enumerate(scripts):
        if s.get("id") == script_id:
            return scripts, i
    raise HTTPException(status_code=404, detail="Script not found.")


def _context(job) -> str:
    """The channel facts block handed to the agent (empty when none)."""
    from youtube_research import as_context

    return as_context((job.params or {}).get("channel") or {})


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
@router.post("", response_model=PlanDetail, status_code=201)
def create_plan(
    body: PlanCreateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature("workflow.plan-and-script")),
    _quota: CurrentUser = Depends(require_quota("projects")),
):
    """Start a planning session. Empty until the user says something.

    ⚠ THE GATE IS ON CREATING AND SPENDING, NEVER ON READING — the rule for
    every `require_feature` in this codebase. So this route, `/chat`,
    `/generate` and `/script` refuse when the workflow is switched off, while
    listing, opening, renaming, deleting and EXPORTING a session that already
    exists stay open. Turning a workflow off is a product decision; it is not a
    reason to lock a customer out of work they have already paid for and may
    need to get out of the app.
    """
    usage_counters.increment(current.email, "projects")
    job = get_store().create(
        character_name=(body.title or "").strip() or "Untitled plan",
        kind=JobKind.PLAN,
        params={"messages": [], "channel": {}, "plan": {}},
        owner=current.email,
    )
    # A conversation is never "queued for work" — it is ready the moment it
    # exists. SUCCEEDED keeps it out of any "still running" UI.
    job = get_store().update(job.job_id, status=JobStatus.SUCCEEDED)
    logger.info("[plan %s] session created", job.job_id)
    return _detail(job)


@router.get("", response_model=list[PlanSummary])
def list_plans(
    limit: int = Query(50, ge=1, le=200),
    current: CurrentUser = Depends(get_current_user),
):
    """The caller's planning sessions, newest first."""
    jobs = get_store().list(limit=limit, owner=current.email, kinds=[JobKind.PLAN])
    return [_summary(j) for j in jobs]


@router.get("/{job_id}", response_model=PlanDetail)
def get_plan(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """One session: the whole transcript, the channel research and the plan."""
    return _detail(_get_owned_plan(job_id, current))


@router.patch("/{job_id}", response_model=PlanDetail)
def rename_plan(
    job_id: str,
    body: PlanRenameRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Rename a session."""
    _get_owned_plan(job_id, current)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Give the plan a name.")
    return _detail(get_store().update(job_id, character_name=title))


@router.delete("/{job_id}", status_code=204)
def delete_plan(job_id: str, current: CurrentUser = Depends(get_current_user)):
    """Delete a session permanently. Nothing on disk belongs to a plan."""
    _get_owned_plan(job_id, current)
    get_store().delete(job_id)
    return None


# ---------------------------------------------------------------------------
# Talking to the agent
# ---------------------------------------------------------------------------
@router.post("/{job_id}/chat", response_model=PlanDetail)
def chat_to_plan(
    job_id: str,
    body: PlanChatRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('workflow.plan-and-script')),
):
    """Send one message and get the agent's reply.

    Both the message and the reply are persisted, so the conversation survives a
    refresh exactly like everything else in the app.
    """
    job = _get_owned_plan(job_id, current)
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type a message first.")

    params = dict(job.params or {})
    messages = list(params.get("messages") or [])
    messages.append({"role": "user", "text": text, "at": _now()})

    from plan_agent import PlanError, chat

    try:
        result = chat(messages, channel_context=_context(job))
    except PlanError as e:
        # The user's message is NOT saved when the reply fails — otherwise the
        # transcript grows a question that was never answered, and the next turn
        # re-sends it as if it had been.
        logger.warning("[plan %s] chat failed: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[plan %s] unexpected chat error", job_id)
        raise HTTPException(status_code=502, detail=f"Planning agent error: {e}")

    # Questions ride along on the agent's turn, so the clickable panel survives
    # a refresh and an OLD question can never be mistaken for the live one.
    messages.append(
        {
            "role": "agent",
            "text": result.get("reply", ""),
            "questions": result.get("questions") or [],
            "at": _now(),
        }
    )
    params["messages"] = messages[-MAX_MESSAGES:]
    _record_usage(params, result.get("usage"), current.email)

    fields = {"params": params}
    # Name the session after what the opening message is ABOUT, so the library
    # isn't a column of "Untitled plan" — or of full sentences.
    if (job.character_name or "").strip() in ("", "Untitled plan"):
        fields["character_name"] = _short_title(text)

    return _detail(get_store().update(job_id, **fields))


@router.post("/{job_id}/channel", response_model=PlanDetail)
def attach_channel(
    job_id: str,
    body: PlanChannelRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Research the user's YouTube channel and attach the findings.

    Never fails the request when research is unavailable: the result records
    WHY, and the agent is told to ask the user instead of inventing numbers.
    """
    job = _get_owned_plan(job_id, current)
    from youtube_research import research_channel

    research = research_channel(body.url or "")
    params = dict(job.params or {})
    params["channel"] = research
    updated = get_store().update(job_id, params=params)

    if research.get("available"):
        logger.info(
            "[plan %s] channel: %s (%s subs)",
            job_id, research.get("title"), research.get("subscribers"),
        )
    else:
        logger.info("[plan %s] channel unavailable: %s", job_id, research.get("reason"))
    return _detail(updated)


@router.post("/{job_id}/generate", response_model=PlanDetail)
def generate(
    job_id: str,
    body: PlanGenerateRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('workflow.plan-and-script')),
):
    """Turn the conversation into a structured calendar."""
    job = _get_owned_plan(job_id, current)
    params = dict(job.params or {})
    messages = params.get("messages") or []
    if not messages:
        raise HTTPException(
            status_code=400,
            detail="Tell the agent about your channel first, then generate a plan.",
        )

    from plan_agent import PlanError, generate_plan

    try:
        plan = generate_plan(
            messages,
            months=body.months,
            cadence=body.cadence or "",
            channel_context=_context(job),
            language=body.language or "",
        )
    except PlanError as e:
        logger.warning("[plan %s] generate failed: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("[plan %s] unexpected generate error", job_id)
        raise HTTPException(status_code=502, detail=f"Plan generation error: {e}")

    plan["title"] = job.character_name or "Content plan"
    params["plan"] = plan
    _record_usage(params, plan.get("usage"), current.email)
    return _detail(get_store().update(job_id, params=params))


# ---------------------------------------------------------------------------
# Writing the script — the "& Script" half of the workflow
# ---------------------------------------------------------------------------
@router.post("/{job_id}/script", response_model=PlanDetail)
def write_script_route(
    job_id: str,
    body: PlanScriptRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('workflow.plan-and-script')),
):
    """Write the script for one upload, and keep it on the session.

    Two ways in, and they are the same route because they are the same job:
      - `item_index` — the script for a row of the generated calendar. The row
        supplies the brief, so there is nothing else to type.
      - `brief` — a script for something that was never on the calendar.

    ⚠ THE CALENDAR ROW IS READ SERVER-SIDE FROM THE STORED PLAN, not taken from
    the request body. The browser has the row on screen and could have sent it,
    but then a stale tab would write a script for an upload that was regenerated
    away, and the script would claim to be for a calendar entry that no longer
    says that.
    """
    job = _get_owned_plan(job_id, current)
    params = dict(job.params or {})
    plan = params.get("plan") or {}
    items = plan.get("items") or []

    item = None
    if body.item_index is not None:
        if body.item_index >= len(items):
            raise HTTPException(
                status_code=409,
                detail=(
                    "That upload is no longer in the plan — it may have been "
                    "regenerated. Reopen the plan and pick it again."
                ),
            )
        item = items[body.item_index]

    if item is None and not body.brief.strip():
        raise HTTPException(
            status_code=400,
            detail="Pick an upload from the calendar, or describe the video you want.",
        )

    from plan_agent import ScriptError, write_script

    try:
        script = write_script(
            messages=params.get("messages") or [],
            item=item,
            brief=body.brief,
            seconds=body.seconds,
            # Default to the language the calendar was written in: a script for
            # a Hinglish plan should not silently arrive in English.
            language=body.language or plan.get("language") or "",
            channel_context=_context(job),
            notes=body.notes,
        )
    except ScriptError as e:
        logger.warning("[plan %s] script failed: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[plan %s] unexpected script error", job_id)
        raise HTTPException(status_code=502, detail=f"Script writing error: {e}")

    script["id"] = uuid.uuid4().hex[:12]
    script["at"] = _now()
    # Recorded so the script's card can say which upload it belongs to, and so
    # a regenerate can be aimed at the same row. The INDEX alone would go stale
    # the moment the calendar is rebuilt, so the title travels with it.
    script["item_index"] = body.item_index
    script["item_title"] = str((item or {}).get("title", "")).strip()
    script["item_slot"] = str((item or {}).get("slot", "")).strip()
    script["brief"] = body.brief.strip()

    # Newest first — the one just written is the one being looked at.
    scripts = [script] + list(params.get("scripts") or [])
    params["scripts"] = scripts[:MAX_SCRIPTS]
    _record_usage(params, script.get("usage"), current.email)

    logger.info(
        "[plan %s] script %s written: %r (%d scene(s), ~%ds)",
        job_id, script["id"], script["title"], len(script["scenes"]),
        script.get("estimated_seconds", 0),
    )
    return _detail(get_store().update(job_id, params=params))


@router.delete("/{job_id}/scripts/{script_id}", response_model=PlanDetail)
def delete_script(
    job_id: str,
    script_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Delete one script. The session's token total is NOT reduced — those
    tokens were spent, and a total that shrinks when you tidy up is a lie."""
    job = _get_owned_plan(job_id, current)
    scripts, i = _get_script(job, script_id)
    scripts.pop(i)
    params = dict(job.params or {})
    params["scripts"] = scripts
    return _detail(get_store().update(job_id, params=params))


@router.post("/{job_id}/scripts/{script_id}/to-draft")
def script_to_draft(
    job_id: str,
    script_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Load this script into the caller's script draft, for Script to Storyboard.

    ⚠ THIS OVERWRITES THE ONE DRAFT PER USER (see drafts.py) — the browser warns
    first. It is done SERVER-SIDE rather than by the browser PUTting the text
    because the text the storyboard reads must be the text this workflow
    produced (`plan_agent.script_to_text`), byte for byte, and a browser that
    rebuilt it from the scenes would be a second implementation of that format.
    """
    job = _get_owned_plan(job_id, current)
    scripts, i = _get_script(job, script_id)
    script = scripts[i]

    text = str(script.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=409, detail="That script has no text to send.")

    from .drafts import save_draft

    saved = save_draft(current.email, text, script.get("title") or "")
    logger.info(
        "[plan %s] script %s → script draft (%d chars)", job_id, script_id, len(text)
    )
    return {"ok": True, "chars": len(text), "updated_at": saved.get("updated_at", "")}


@router.get("/{job_id}/scripts/{script_id}/export")
def export_script(
    job_id: str,
    script_id: str,
    format: str = Query("txt", pattern="^(txt|docx)$"),
    current: CurrentUser = Depends(get_current_user),
):
    """Download one script as .txt or .docx.

    .txt is the SAME bytes the storyboard breakdown reads, so a creator who
    exports, edits in a text editor and pastes it back gets exactly the document
    the app would have handed over itself.
    """
    job = _get_owned_plan(job_id, current)
    scripts, i = _get_script(job, script_id)
    script = scripts[i]

    from plan_export import SCRIPT_EXPORTERS

    build, media_type = SCRIPT_EXPORTERS[format]
    try:
        data = build(script)
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[plan %s] script %s export failed", job_id, format)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    stem = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in (script.get("title") or "script")
    )
    filename = f"{stem.strip() or 'script'}.{format}"
    logger.info("[plan %s] script %s exported %s (%d bytes)", job_id, script_id, format, len(data))
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@router.get("/{job_id}/export")
def export_plan(
    job_id: str,
    format: str = Query("xlsx", pattern="^(xlsx|docx|csv)$"),
    current: CurrentUser = Depends(get_current_user),
):
    """Download the calendar as .xlsx, .docx or .csv."""
    job = _get_owned_plan(job_id, current)
    plan = (job.params or {}).get("plan") or {}
    if not (plan.get("items") or []):
        raise HTTPException(status_code=409, detail="Generate a plan before exporting it.")

    from plan_export import EXPORTERS

    build, media_type = EXPORTERS[format]
    plan = {**plan, "title": job.character_name or "Content plan"}
    try:
        data = build(plan)
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[plan %s] %s export failed", job_id, format)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in (job.character_name or "plan"))
    filename = f"{safe.strip() or 'plan'}.{format}"
    logger.info("[plan %s] exported %s (%d bytes)", job_id, format, len(data))
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/config/youtube")
def youtube_config(current: CurrentUser = Depends(get_current_user)):
    """Whether real channel research is available on this server.

    The client uses this to say "channel research is off" up front, rather than
    letting the user paste a link and wonder why nothing was looked up.
    """
    from youtube_research import is_configured

    return {"configured": is_configured()}
