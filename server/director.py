"""
director.py — the "/director" router: 🎬 Make Video's brain.

    POST /director/{job_id}/plan       write an edit plan for this board
    GET  /director/config              which provider is wired up, and the languages
    POST /director/{job_id}/veo/quote  what the Veo pass would cost, pass by pass
    POST /director/{job_id}/veo/start  open a resumable run record
    POST /director/{job_id}/veo/state  close it (done / stopped / failed)

⚠ NOT ONE ROUTE IN THIS FILE SPENDS A PENNY, INCLUDING THE THREE WITH "veo" IN
THE NAME. `/plan` spends text quota — two calls — and returns a plan the user
reads before anything happens. The three `/veo` routes are arithmetic and one
record: they quote a pass, open it, and close it. THE MONEY IS SPENT BY
`POST /animatics/{id}/animate`, one pass of `MAX_VIDEO_BATCH` at a time, which is
the door every other paid render in this editor already goes through — so the
spend guards written for ✨ Animate on 2026-08-07 govern the Director's pass too,
without a line of them being repeated here.

⚠ IT MAKES NO EDIT, AND IT COULD NOT. The plan crosses back to the browser as
data and goes through `validatePlan` → `applyGuardrails` → `useDirectorRun`,
exactly as the deterministic Phase 0 planner's does. Nothing here writes to the
timeline; the only thing this route persists is the project's LANGUAGE, because
that is a property of the film rather than of one run (see
`AnimaticSettings.language`).

⚠ IT DOES NOT IMPORT `animatics.py`. Two routers never import each other in this
app — shared route helpers live in `common.py`, which is where `get_owned_job`
comes from. The four lines of ownership check below are the price of that rule
and they are worth it.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from . import config
from .auth import CurrentUser, get_current_user
# ⚠ THE GUARD THAT ACTUALLY TURNS A FEATURE OFF. The sidebar reading the same
# registry is cosmetic — anyone can call these routes directly. See features.py.
from .features import require_feature
from .common import get_owned_job, write_director_run
from .jobs import Job, get_store
from .schemas import (
    AnimaticDirectorRun,
    CostEstimate,
    DirectorPlanRequest,
    DirectorPlanResponse,
    DirectorVeoQuote,
    DirectorVeoRequest,
    JobKind,
    RenderSettings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/director", tags=["director"])


def _owned_animatic(job_id: str, current: CurrentUser) -> Job:
    job = get_owned_job(job_id, current)
    if job.kind != JobKind.ANIMATIC:
        raise HTTPException(status_code=400, detail="Not a project.")
    return job


def _save_language(job: Job, language: str) -> None:
    """Write the film's language onto the project, once, when it changes.

    ⚠ THE ONLY WRITE THIS ROUTER MAKES. It is here rather than in the editor's
    ordinary autosave because the 🎬 popup is where the question is actually
    asked — and a language the user picked, watched a plan get written in, and
    then lost on refresh would be asked again every single run.
    """
    language = (language or "").strip()
    if not language:
        return
    params = dict(job.params or {})
    settings = dict(params.get("settings") or {})
    if (settings.get("language") or "") == language:
        return
    settings["language"] = language
    params["settings"] = settings
    get_store().update(job.job_id, params=params)
    logger.info("[director %s] project language set to %r.", job.job_id, language)


def _quote_veo(shots: int) -> CostEstimate:
    """What rendering these shots with Veo WOULD cost. Advisory, and never spent here.

    Priced through `video_client.estimate_cost_usd` at the project's default
    render settings — the same rate table the animate button and the final-video
    workspace quote from, so the three can never disagree.
    """
    from video_client import estimate_cost_usd

    render = RenderSettings()
    per = estimate_cost_usd(
        render.duration_seconds, render.resolution, render.tier, render.generate_audio
    )
    return CostEstimate(
        shots=shots,
        seconds=shots * render.duration_seconds,
        usd=round(per * shots, 2),
        tier=render.tier,
        resolution=render.resolution,
    )


def _quote_veo_shots(shots: list, render: RenderSettings) -> CostEstimate:
    """What one submission of these shots costs, at their OWN lengths.

    ⚠ PER SHOT, NOT `count × render.duration_seconds`. `_estimate_animate` prices
    a batch at one length because ✨ Animate renders a batch at one length; the
    Director picks a length per shot (`coverSeconds` — the smallest of 4/6/8 that
    covers the hold), so a pass is a mixed bag and pricing it off the settings'
    default would quote the wrong film. The RATE TABLE is still the shared one,
    which is the part that must never fork.
    """
    from video_client import estimate_cost_usd

    usd = 0.0
    seconds = 0
    for shot in shots or []:
        length = int(getattr(shot, "seconds", 0) or render.duration_seconds)
        seconds += length
        usd += estimate_cost_usd(
            length, render.resolution, render.tier, render.generate_audio
        )
    return CostEstimate(
        shots=len(shots or []),
        seconds=seconds,
        usd=round(usd, 2),
        tier=render.tier,
        resolution=render.resolution,
    )


def _quote_veo_run(shots: list, render: RenderSettings) -> DirectorVeoQuote:
    """The pass-by-pass quote, and a total that is the SUM OF THE PASSES.

    ⚠ THE TOTAL IS ADDED UP FROM THE PASSES, NOT COMPUTED AGAIN. Every quote here
    is rounded to the penny, and rounding the whole shot list once gives a
    different answer from rounding four twelfths of it and adding — by a cent,
    often enough to matter. The user is shown a total before they press the
    button and then watches four numbers go by on the rail; if those four do not
    add up to the number they agreed to, the honest reading is that neither can
    be trusted. `tests/director_chunk_check.py` owns the identity.
    """
    batch = max(1, int(config.MAX_VIDEO_BATCH))
    rows = list(shots or [])
    passes = [
        _quote_veo_shots(rows[at : at + batch], render) for at in range(0, len(rows), batch)
    ]
    return DirectorVeoQuote(
        batch=batch,
        passes=passes,
        total=CostEstimate(
            shots=sum(p.shots for p in passes),
            seconds=sum(p.seconds for p in passes),
            usd=round(sum(p.usd for p in passes), 2),
            tier=render.tier,
            resolution=render.resolution,
        ),
    )


@router.post("/{job_id}/veo/quote", response_model=DirectorVeoQuote)
def quote_veo(
    job_id: str,
    body: DirectorVeoRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. What the Director's Veo pass would cost, broken into its passes.

    The number under the preview table and the numbers on the run's rail both
    come from here, which is what makes them the same number. Nothing is written
    and nothing is submitted — see `/veo/start` for the record and
    `POST /animatics/{id}/animate` for the only thing that spends.
    """
    _owned_animatic(job_id, current)
    return _quote_veo_run(body.shots, body.render)


@router.post("/{job_id}/veo/start", response_model=AnimaticDirectorRun)
def start_veo(
    job_id: str,
    body: DirectorVeoRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.veo-render')),
):
    """FREE. Open a resumable run: write down what this pass MEANT to render.

    ⚠ WRITTEN BEFORE THE FIRST SUBMISSION, WHICH IS THE ENTIRE POINT. A record
    written after the money moved would be missing exactly the runs that need it
    — the ones that died on pass one. From this moment a refresh, a crash or a
    closed laptop can be picked up again, because the intention is on the server
    and `veo_clips` says how much of it was bought.

    ⚠ IT REPLACES ANY EARLIER RUN. See `write_director_run` on why there is only
    ever one.
    """
    from datetime import datetime, timezone

    _owned_animatic(job_id, current)
    if not body.shots:
        raise HTTPException(status_code=400, detail="There is nothing to render.")

    quote = _quote_veo_run(body.shots, body.render)
    run = AnimaticDirectorRun(
        id=uuid.uuid4().hex[:12],
        started_at=datetime.now(timezone.utc).isoformat(),
        status="running",
        shots=body.shots,
        render=body.render,
        batch=quote.batch,
        quoted_usd=quote.total.usd,
    )
    write_director_run(job_id, run.model_dump())
    logger.info(
        "[director %s] Veo pass opened: %d shot(s) in %d pass(es), quoted $%.2f.",
        job_id, len(body.shots), len(quote.passes), quote.total.usd,
    )
    return run


@router.post("/{job_id}/veo/state", response_model=AnimaticDirectorRun)
def state_veo(
    job_id: str,
    body: DirectorVeoRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """FREE. Close the run — 'done', 'stopped' or 'failed'.

    ⚠ THE SHOT LIST IS NEVER REWRITTEN, ONLY THE STATUS. What the run intended is
    settled the moment it opened; how far it got is a question for `veo_clips`,
    which is the only record that knows what was actually paid for. A progress
    counter kept here would be a counter a crashed process was halfway through
    updating, which is precisely the state it exists to survive.
    """
    job = _owned_animatic(job_id, current)
    raw = (job.result or {}).get("director_run") or {}
    if not raw:
        raise HTTPException(status_code=404, detail="There is no Veo pass on this project.")
    # ⚠ A REPORT ABOUT A RUN THAT IS NO LONGER THE CURRENT ONE IS IGNORED, not an
    # error: a tab left open on an abandoned pass must not be able to mark a run
    # started since then as finished.
    if body.run_id and raw.get("id") and body.run_id != raw.get("id"):
        return AnimaticDirectorRun(**raw)
    status = (body.status or "").strip() or "done"
    if status not in {"running", "done", "stopped", "failed"}:
        raise HTTPException(status_code=400, detail=f"Unknown run status '{status}'.")
    raw = {**raw, "status": status, "error": body.error or ""}
    write_director_run(job_id, raw)
    logger.info("[director %s] Veo pass %s.", job_id, status)
    return AnimaticDirectorRun(**raw)


@router.get("/config")
def director_config():
    """Which backend the Director is wired to, and the languages that have a table.

    Free, no model call. The editor shows the language list in the 🎬 popup and
    needs to know whether a plan can be asked for at all before offering to.
    """
    from llm_json import model_id, resolve_provider
    from plan_agent import LANGUAGES

    try:
        provider = resolve_provider()
        model = model_id()
        error = ""
    except Exception as e:  # noqa: BLE001 — a bad env var must not 500 the editor
        provider, model, error = "", "", str(e)

    return {
        "provider": provider,
        "model": model,
        "error": error,
        # ⚠ THE BROWSER MUST NOT HARD-CODE THIS. It is `config.MAX_VIDEO_BATCH`, a
        # SPEND guard an operator is expected to change per deployment, and it
        # decides how many passes the Director's Veo run is split into. A client
        # that assumed 12 against a server set to 6 would show the user a plan in
        # four parts and then take a 413 on every one of them.
        "max_video_batch": int(config.MAX_VIDEO_BATCH),
        # ⚠ THE LIST IS A SUGGESTION, NOT A WHITELIST. Anything the user types is
        # passed through — see `plan_agent.language_instruction`.
        "languages": [{"id": key, "label": key.title()} for key in sorted(LANGUAGES)],
    }


@router.post("/{job_id}/plan", response_model=DirectorPlanResponse)
def write_plan(
    job_id: str,
    body: DirectorPlanRequest,
    current: CurrentUser = Depends(get_current_user),
    _gate: CurrentUser = Depends(require_feature('cap.director')),
):
    """SPENDS TEXT QUOTA — two calls, and nothing on the timeline moves.

    ⚠ THE BOARD COMES FROM THE BROWSER, NOT FROM THE STORE, and that is not
    laziness. The editor autosaves, so the saved project is behind whatever the
    user has just done; a plan written against the store would be a plan for a
    film that is one edit stale, and the first thing the user would notice is a
    dissolve on the cut they moved thirty seconds ago. The document on screen is
    the film being edited.

    A failure here is a 502 with the model's actual reason on it, and the browser
    falls back to the deterministic Phase 0 planner — which is why `housePlan`
    stayed after the model arrived.
    """
    from director import DirectorError, direct
    from llm_json import model_id, resolve_provider

    job = _owned_animatic(job_id, current)

    shots = (body.board or {}).get("shots") or []
    if not shots:
        raise HTTPException(status_code=400, detail="There is nothing on the timeline to edit yet.")

    _save_language(job, body.language)

    try:
        result = direct(
            board=body.board or {},
            vocabulary=body.capabilities or {},
            include=body.include or {},
            language=body.language,
            brief_text=body.brief,
        )
    except DirectorError as e:
        logger.warning("[director %s] plan failed: %s", job_id, e)
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001 — report clearly, never a bare 500
        logger.exception("[director %s] unexpected planning error", job_id)
        raise HTTPException(status_code=502, detail=f"Director error: {e}") from None

    try:
        provider, model = resolve_provider(), model_id()
    except Exception:  # noqa: BLE001 — reporting, not the request
        provider, model = "", ""

    logger.info(
        "[director %s] %d shot(s) → %d step(s), %d dropped, %d motion prompt(s), "
        "%d sound cue(s).",
        job_id, len(shots), len(result["plan"]["steps"]), len(result["dropped"]),
        len(result["veo"]), len(result.get("sfx") or []),
    )
    return DirectorPlanResponse(
        provider=provider,
        model=model,
        plan=result["plan"],
        analysis=result["analysis"],
        veo=result["veo"],
        # ⚠ SEARCH TERMS, AND THIS ENDPOINT HAS NOT SEARCHED FOR THEM. The sound
        # library is not touched until `POST /animatics/{id}/soundtrack`, for the
        # same reason no Veo render happens here: the user reads the plan first.
        sfx=result.get("sfx") or [],
        music=result.get("music") or {},
        dropped=result["dropped"],
        notes=result["notes"],
        cost=_quote_veo(len(result["veo"])),
    )
