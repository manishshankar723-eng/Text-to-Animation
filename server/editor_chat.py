"""
editor_chat.py — the "/editor-chat" router: the ✨ AI Editor's one route.

    GET  /editor-chat/config           where the panel opens, and what's left of the quota
    POST /editor-chat/{job_id}/turn    one message in, one turn out

⚠ **NOT ONE ROUTE IN THIS FILE SPENDS A PENNY OF ANYBODY'S MONEY.** `/turn`
spends TEXT quota — one call — and returns a proposal the user reads before
anything happens. The same sentence opens `server/director.py`, and it is the
load-bearing rule of both features: a typed sentence must never be able to start
a Veo render. When the chat decides a render would help, it says so; the spend
still goes through `POST /animatics/{id}/animate` with a price on the button,
which is the door every paid render in this editor already goes through.

⚠ **IT MAKES NO EDIT, AND IT COULD NOT.** What comes back crosses to the browser
as data and goes through `normaliseTurn` → `validatePlan` → `useDirectorRun`,
exactly as the Director's plan does. Nothing here writes to a timeline. Nothing
here writes anything at all except one counter.

⚠ **IT IS STATELESS.** The browser owns the transcript and posts the whole thing
every turn, along with the board as it stands on screen. See
`editor_chat_agent.py` for why remembering the conversation would be worse than
not remembering it.

⚠ **TWO GUARDS, BOTH BEFORE THE BODY RUNS.** `require_feature('cap.editor-chat')`
is the switch that actually turns this off — the sidebar reading the same registry
is cosmetic, and anyone can POST here directly. `require_quota('chat_turns')` is
the tier's monthly count, checked BEFORE the model call rather than after, so a
customer over their limit is never billed for the call that tells them so.

⚠ **THE COUNTER IS INCREMENTED AFTER THE CALL SUCCEEDS**, not before. A turn that
died on a 502 from the provider is not a turn the customer used — and
`increment` never raises, so a counter that cannot be written loses a tick rather
than losing the user's reply.

⚠ **IT DOES NOT IMPORT `animatics.py`.** Two routers never import each other in
this app; shared route helpers live in `common.py`, which is where
`get_owned_job` comes from.
"""

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException

from .auth import CurrentUser, get_current_user
from .common import get_owned_job
from . import chat_settings
from .features import require_feature
from .jobs import Job
from . import usage as usage_counters
from .schemas import (
    EditorChatAsk,
    EditorChatConfig,
    EditorChatOption,
    EditorChatRequest,
    EditorChatResponse,
    JobKind,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/editor-chat", tags=["editor-chat"])

# The counter and the tier limit key. ⚠ ONE SPELLING, and it is the tier's —
# `usage.COUNTERS` says why the two must never diverge.
TURNS_FIELD = "chat_turns"


def _owned_animatic(job_id: str, current: CurrentUser) -> Job:
    job = get_owned_job(job_id, current)
    if job.kind != JobKind.ANIMATIC:
        raise HTTPException(status_code=400, detail="Not a project.")
    return job


@router.get("/config", response_model=EditorChatConfig)
def config(
    current: CurrentUser = Depends(get_current_user),
) -> EditorChatConfig:
    """What the editor needs before it draws the panel. Free — no model call.

    ⚠ **NOT GATED ON THE FEATURE, AND `enabled` IS WHY.** A gate here would answer
    404 to an account the chat is off for, and the editor would have to treat "the
    feature is off" and "the backend is down" as the same thing. It answers
    honestly instead, and the panel simply is not drawn.
    """
    from .features import is_on

    row = chat_settings.get_settings()
    used, limit = _quota(current.email)
    try:
        enabled = bool(is_on(current.email, "cap.editor-chat"))
    except Exception as e:  # noqa: BLE001 — the registry fails open, so do we
        logger.warning("[editor-chat] could not resolve the feature gate (%s).", e)
        enabled = True
    return EditorChatConfig(
        enabled=enabled,
        dock=row.get("dock") or chat_settings.DOCK_RIGHT,
        greeting=row.get("greeting") or "",
        max_turns_per_session=int(row.get("max_turns_per_session") or 0),
        transcript_keep=int(row.get("transcript_keep") or 20),
        turns_used=used,
        turns_limit=limit,
    )


def _quota(email: str) -> tuple[int, int | None]:
    """`(used, limit)` for this account's chat turns. Never raises."""
    try:
        _allowed, used, limit = usage_counters.check(email, TURNS_FIELD, 0)
        return used, limit
    except Exception as e:  # noqa: BLE001 — reporting, never the request
        logger.warning("[editor-chat] could not read the turn quota (%s).", e)
        return 0, None


@router.post("/{job_id}/turn", response_model=EditorChatResponse)
def turn(
    job_id: str,
    body: EditorChatRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature("cap.editor-chat")),
    _quota_gate: CurrentUser = Depends(usage_counters.require_quota(TURNS_FIELD)),
) -> EditorChatResponse:
    """SPENDS TEXT QUOTA — one call, and nothing on the timeline moves.

    A failure is a 502 carrying the model's actual reason. There is no fallback
    planner here, unlike the Director: a conversation with no model is not a
    degraded conversation, it is silence, and pretending otherwise would put
    words in the assistant's mouth that no model said.
    """
    from editor_chat_agent import CAPABILITY, EditorChatError, chat
    from llm_json import model_id, resolve_provider

    _owned_animatic(job_id, current)

    messages = [{"role": m.role, "text": m.text} for m in body.messages]
    if not messages or messages[-1]["role"] != "user":
        raise HTTPException(
            status_code=400,
            detail="The last message must be the one you just typed.",
        )

    settings = chat_settings.get_settings()

    # ⚠ THE TRIM IS THE SERVER'S TOO, NOT ONLY THE BROWSER'S. The client already
    # sends `transcript_keep` messages; this is what stops a hand-rolled POST
    # putting a thousand turns into one prompt. Same belt-and-braces the script
    # chat uses, and the cheaper of the two places to get it wrong.
    keep = max(2, int(settings.get("transcript_keep") or 20))
    messages = messages[-keep:]

    # ⚠ DECODED HERE AND HELD NOWHERE. A picture that will not decode is dropped
    # rather than failing the turn: a look that arrives one still short still
    # answers, and a chat that 400s because one thumbnail was truncated has lost
    # the user's message over a picture.
    pictures = []
    for row in body.look or []:
        try:
            pictures.append({
                "shot": row.shot,
                "mime": row.mime or "image/png",
                "data": base64.b64decode(row.data or "", validate=True),
            })
        except Exception:  # noqa: BLE001 — one bad picture is not a bad request
            logger.warning("[editor-chat %s] a look picture would not decode.", job_id)

    try:
        result = chat(
            messages=messages,
            board=body.board or {},
            vocabulary=body.capabilities or {},
            settings=settings,
            language=body.language or "",
            pictures=tuple(pictures),
        )
    except EditorChatError as e:
        logger.warning("[editor-chat %s] turn failed: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001 — report clearly, never a bare 500
        logger.exception("[editor-chat %s] unexpected error", job_id)
        raise HTTPException(status_code=502, detail=f"AI Editor error: {e}") from None

    # ⚠ AFTER THE CALL, AND ONLY ON SUCCESS. See the module docstring.
    usage_counters.increment(current.email, TURNS_FIELD)
    used, limit = _quota(current.email)

    # ⚠ ASKED WITH THE CAPABILITY, or this line reports the wrong two strings.
    # The chat resolves its provider from `CHAT_PROVIDER` / `GEMINI_KEY_CHAT`, so
    # the bare call answered "vertex" for a turn that ran on the Developer API —
    # a debugging trail pointing at the one backend that was not involved.
    try:
        provider = resolve_provider(capability=CAPABILITY)
        model = model_id(capability=CAPABILITY)
    except Exception:  # noqa: BLE001 — reporting, not the request
        provider, model = "", ""

    ask = None
    if result.get("ask"):
        raw = result["ask"]
        ask = EditorChatAsk(
            question=raw.get("question") or "",
            reason=raw.get("reason") or "",
            options=[
                # ⚠ THE ID IS POSITIONAL AND IS MINTED HERE. Whatever the model
                # called an option is discarded: two options sharing an id would
                # collide as a React key and as the thing the click handler looks
                # up. The client mints them again for the same reason — neither
                # side may assume the other did.
                EditorChatOption(
                    id=f"o{i + 1}",
                    label=o.get("label") or "",
                    note=o.get("note") or "",
                )
                for i, o in enumerate(raw.get("options") or [])
            ],
        )

    steps = len(((result.get("plan") or {}).get("steps")) or [])
    logger.info(
        "[editor-chat %s] %s — %d message(s) in, %d step(s), %d dropped. %s",
        job_id, result.get("kind"), len(messages), steps, len(result.get("dropped") or []),
        f"{used}/{limit} turns" if limit is not None else f"{used} turns (unlimited)",
    )

    return EditorChatResponse(
        kind=result.get("kind") or "answer",
        reply=result.get("reply") or "",
        ask=ask,
        plan=result.get("plan"),
        # ⚠ STILL NOTHING SPENT. Freesound costs no money — it spends the
        # deployment's SHARED rate limit (60 requests a minute for everybody), and
        # not one request is made here. The search happens when the user presses
        # Apply, through `POST /animatics/{id}/soundtrack`, which is the door the
        # Director's own sound pass already goes through.
        sound=result.get("sound"),
        # ⚠ AND STILL NOTHING SPENT BY THESE EITHER. An offer is a door's NAME.
        # The panel draws a button, the button opens ✨ Animate / 🎙 Voiceover /
        # 🖼 Animatic images, and THAT is what asks the server for a price. See
        # `PAID_DOORS` in `editor_chat_agent.py` for why no figure travels here.
        passes=result.get("passes") or [],
        # ⚠ NOT AN ANSWER — a request for pictures. The browser sends the same
        # message again with them attached; see `MAX_LOOK_SHOTS`.
        look=result.get("look"),
        dropped=result.get("dropped") or [],
        provider=provider,
        model=model,
        turns_used=used,
        turns_limit=limit,
    )
