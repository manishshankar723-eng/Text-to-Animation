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

Spends TEXT quota only — this workflow never generates an image.
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .auth import CurrentUser, get_current_user
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
    PlanSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])

# A transcript is cheap to store but not free to send to the model on every
# turn, and an unbounded one eventually costs real money per message.
MAX_MESSAGES = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _summary(job) -> PlanSummary:
    p = job.params or {}
    plan = p.get("plan") or {}
    channel = p.get("channel") or {}
    return PlanSummary(
        job_id=job.job_id,
        title=job.character_name or "Untitled plan",
        message_count=len(p.get("messages") or []),
        item_count=len(plan.get("items") or []),
        months=int(plan.get("months") or 0),
        channel_title=channel.get("title") or "",
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


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
):
    """Start a planning session. Empty until the user says something."""
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
        reply = chat(messages, channel_context=_context(job))
    except PlanError as e:
        # The user's message is NOT saved when the reply fails — otherwise the
        # transcript grows a question that was never answered, and the next turn
        # re-sends it as if it had been.
        logger.warning("[plan %s] chat failed: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[plan %s] unexpected chat error", job_id)
        raise HTTPException(status_code=502, detail=f"Planning agent error: {e}")

    messages.append({"role": "agent", "text": reply, "at": _now()})
    params["messages"] = messages[-MAX_MESSAGES:]

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
        )
    except PlanError as e:
        logger.warning("[plan %s] generate failed: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("[plan %s] unexpected generate error", job_id)
        raise HTTPException(status_code=502, detail=f"Plan generation error: {e}")

    plan["title"] = job.character_name or "Content plan"
    params["plan"] = plan
    return _detail(get_store().update(job_id, params=params))


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
