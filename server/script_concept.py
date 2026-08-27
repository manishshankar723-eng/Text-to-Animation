"""
script_concept.py — the "/script-concept" router: the approval gate.

Two routes, and they are the two halves of one promise: **nothing gets drawn
from an idea until the user has seen what we made of it and said yes.**

    POST /script-concept          brief/idea → ONE concept, for the card.
    POST /script-concept/script   the APPROVED concept → a real script.

⚠ **UNLIKE `/script-intake`, THIS ONE DOES RAISE.** The intake is a helper and
fails open, because a classifier that can block a storyboard is worse than the
bug it fixes. This is the opposite: it is a GATE. If the concept cannot be
developed, the correct outcome is an error the user can read and retry — NOT
quietly falling through to breaking their raw brief down as though it were a
script, which is the exact silent invention the gate exists to stop.

⚠ **THE SECOND ROUTE IS WHY THE FLOW HANGS TOGETHER.** A concept cannot be
broken into shots: the review step, `ScriptPanel` and every shot card's
"FROM YOUR SCRIPT · LINE 12" need a real script to point at. So the approved
concept goes through `plan_agent.write_script()`, whose output format is already
a contract with `script_breakdown.py`, and the board is built from that text.

⚠ **THE CONCEPT THAT COMES BACK IS NOT NECESSARILY THE ONE WE SENT.** Every
field on the card is editable. What arrives at `/script-concept/script` is the
user's approved version and is treated as the instruction.

⚠ **EVERY MODEL CALL IS RECORDED** — `usage_counters.record_tokens`, same
warning as `script_chat.py`, `script_intake.py` and `plans.py`. Both routes
spend TEXT quota; neither ever generates an image.

Gated on the same key as the workflow the form belongs to
(`workflow.script-to-storyboard`).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from .auth import CurrentUser, get_current_user
from .features import require_feature
from . import usage as usage_counters
from .schemas import (
    ConceptRequest,
    ConceptResponse,
    ConceptScriptRequest,
    ConceptScriptResponse,
    StoryConcept,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/script-concept", tags=["scripts"])


def _record(email: str, usage: dict) -> None:
    """Bank this call's tokens against the account. See the module docstring."""
    if not usage:
        return
    from ai_usage import merge

    usage_counters.record_tokens(email, merge(None, usage))


@router.post("", response_model=ConceptResponse)
def develop_concept(
    body: ConceptRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature("workflow.script-to-storyboard")),
):
    """Brief or idea in, one concept out — nothing is drawn yet."""
    from script_concept import ScriptConceptError, develop

    try:
        result = develop(
            body.text,
            kind=body.kind,
            genre=body.genre,
            style=body.style,
            aspect_ratio=body.aspect_ratio,
        )
    except ScriptConceptError as e:
        logger.warning("[concept] failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001 — report the actual cause
        logger.exception("[concept] unexpected error")
        raise HTTPException(status_code=502, detail=f"Concept error: {e}")

    _record(current.email, result.get("usage") or {})
    return ConceptResponse(
        concept=StoryConcept(**result["concept"]),
        usage=result.get("usage") or {},
    )


@router.post("/script", response_model=ConceptScriptResponse)
def concept_to_script(
    body: ConceptScriptRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature("workflow.script-to-storyboard")),
):
    """The concept the user approved → the script the breakdown will read."""
    from plan_agent import ScriptError, write_script
    from script_concept import concept_seconds, concept_to_brief

    concept = body.concept.model_dump()
    brief = concept_to_brief(concept, body.source)
    seconds = concept_seconds(concept)

    try:
        script = write_script(
            brief=brief,
            seconds=seconds,
            # Empty means "follow the source" — `language_instruction` returns
            # nothing for a blank, which is the behaviour we want when the user
            # left the form's Audience on Auto.
            language=body.language or None,
        )
    except ScriptError as e:
        logger.warning("[concept→script] failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001 — report the actual cause
        logger.exception("[concept→script] unexpected error")
        raise HTTPException(status_code=502, detail=f"Script writer error: {e}")

    _record(current.email, script.get("usage") or {})

    text = (script.get("text") or "").strip()
    if not text:
        # ⚠ An empty script must NOT reach the breakdown. It would be read as a
        # blank story and come back as an invented one — the failure this whole
        # stage exists to prevent, arriving through the back door.
        raise HTTPException(
            status_code=502,
            detail="The script came back empty. Try approving the concept again.",
        )

    logger.info(
        "[concept→script] %r → %d chars, ~%ds",
        script.get("title", ""), len(text), seconds,
    )
    return ConceptScriptResponse(
        script=text,
        # The concept's own title wins: it is what the user just approved on
        # screen, and the writer's is a second opinion nobody asked for.
        title=(concept.get("title") or script.get("title") or "").strip(),
        seconds=seconds,
        usage=script.get("usage") or {},
    )
