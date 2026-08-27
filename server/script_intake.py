"""
script_intake.py — the "/script-intake" router: what did the user paste?

One route, stateless, and it runs in FRONT of the breakdown. The browser sends
the contents of the one box on the Script → Storyboard form and gets back a
name for it — script / brief / idea / vague / empty — plus a sentence to show
and, for `vague` only, a single question to ask.

⚠ **THIS ROUTE IS A HELPER, NOT A GATE.** If it fails, the client goes straight
on to the breakdown exactly as it did before this existed. That is deliberate
and it is enforced on the CLIENT, because a classifier that can stop somebody
making a storyboard is a worse bug than the one it was added to fix.

⚠ **IT IS OFTEN FREE.** `script_intake.intake()` recognises a real script in
pure Python and never calls a model for it, so most boards pay nothing here.
`decided_by` on the response says which path ran.

⚠ **EVERY MODEL CALL IS RECORDED.** `usage_counters.record_tokens` is what keeps
the account's monthly text total honest — same warning as `script_chat.py` and
`plans.py`. The free path records nothing because it spent nothing.

The feature gate is the SAME key as the workflow this box lives in
(`workflow.script-to-storyboard`): if that workflow is off for an account, its
form is off, intake included.

Spends TEXT quota only; this route never generates an image.
"""

import logging

from fastapi import APIRouter, Depends

from .auth import CurrentUser, get_current_user
from .features import require_feature
from . import usage as usage_counters
from .schemas import ScriptIntakeRequest, ScriptIntakeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/script-intake", tags=["scripts"])


@router.post("", response_model=ScriptIntakeResponse)
def script_intake(
    body: ScriptIntakeRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature("workflow.script-to-storyboard")),
):
    """Read one box of text and say what it is."""
    from script_intake import intake

    try:
        result = intake(body.text or "")
    except Exception as e:  # noqa: BLE001 — never block a storyboard on this
        # ⚠ NO 502 HERE. The caller's next move on an error is to carry on into
        # the breakdown, so raising would only turn "we could not tell" into a
        # red box the user has to dismiss before doing the thing they asked for.
        # Answer honestly instead: we do not know, and we are not guessing
        # "script" — see the same asymmetry argument in script_intake.py.
        logger.warning("[script-intake] failed, answering 'idea': %s", e)
        return ScriptIntakeResponse(kind="idea", decided_by="error")

    usage = result.get("usage") or {}
    if usage:
        # ⚠ The account-level sink. Skipped on the free path, which is most of
        # them: recording a zero would be honest but pointless traffic.
        from ai_usage import merge

        usage_counters.record_tokens(current.email, merge(None, usage))

    return ScriptIntakeResponse(
        kind=result.get("kind", "idea"),
        reason=result.get("reason", ""),
        question=result.get("question", ""),
        decided_by=result.get("decided_by", "model"),
        usage=usage,
    )
