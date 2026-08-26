"""
script_chat.py — the "/script-chat" router: the assistant inside the script box.

One route, and it is STATELESS. The browser owns the transcript and posts the
whole thing every turn; nothing is written to a job, a draft or a collection.

⚠ **THAT IS A DECISION, NOT AN OVERSIGHT.** Everything else in this app that
holds a conversation (Plan & Script) does so because the conversation IS the
product — it survives, it is listed, it is renamed, it is exported. Here the
product is the script, and the script already has a durable home: the autosaved
draft (`drafts.py`). Giving the chat its own store would mean a second thing to
own, scope, expire and back up, in exchange for remembering small talk.

⚠ **THE FEATURE GATE IS THE ONE THAT ACTUALLY TURNS IT OFF.** The tab in the
browser reading the same registry is cosmetic — anyone can POST here directly.
It is deliberately the SAME key as the workflow this chat lives inside
(`workflow.script-to-storyboard`): if that workflow is off for an account, its
script box is off with it, chat included.

⚠ **EVERY MODEL CALL IS RECORDED.** `usage_counters.record_tokens` is what makes
the account's monthly text total honest. A route that spends quota and forgets
to add it makes the number on screen quietly wrong — see the same warning at the
top of `plans.py`.

Spends TEXT quota only; this route never generates an image.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from .auth import CurrentUser, get_current_user
from .features import require_feature
from . import usage as usage_counters
from .schemas import ScriptChatRequest, ScriptChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/script-chat", tags=["scripts"])


@router.post("", response_model=ScriptChatResponse)
def script_chat(
    body: ScriptChatRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature("workflow.script-to-storyboard")),
):
    """One turn of the Script → Storyboard chat.

    The last message in `messages` is the user's newest — the client appends it
    before posting rather than sending it separately, so there is exactly one
    representation of the conversation and no way for the two to disagree.
    """
    messages = [{"role": m.role, "text": m.text} for m in body.messages]
    if not messages or messages[-1]["role"] != "user":
        raise HTTPException(
            status_code=400,
            detail="The last message must be the one you just typed.",
        )

    from script_agent import ScriptChatError, build_context, chat

    context = build_context(
        genre=body.genre,
        style=body.style,
        aspect_ratio=body.aspect_ratio,
        title=body.title,
        current_script=body.current_script,
    )

    try:
        result = chat(messages, context=context)
    except ScriptChatError as e:
        logger.warning("[script-chat] failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001 — report the actual cause
        logger.exception("[script-chat] unexpected error")
        raise HTTPException(status_code=502, detail=f"Script assistant error: {e}")

    # ⚠ The account-level sink. See the module docstring.
    from ai_usage import merge

    usage = result.get("usage") or {}
    usage_counters.record_tokens(current.email, merge(None, usage))

    return ScriptChatResponse(
        reply=result.get("reply", ""),
        script=result.get("script", ""),
        title=result.get("title", ""),
        usage=usage,
    )
