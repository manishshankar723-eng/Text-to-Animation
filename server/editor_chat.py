"""
editor_chat.py — the "/editor-chat" router: the ✨ AI Editor's one route.

    GET    /editor-chat/config                        where the panel opens, and what's left of the quota
    POST   /editor-chat/{job_id}/turn                 one message in, one turn out
    GET    /editor-chat/{job_id}/sessions             this project's chats, newest first (no transcripts)
    POST   /editor-chat/{job_id}/sessions             start a new chat
    GET    /editor-chat/{job_id}/sessions/{sid}       one whole chat, transcript and all
    PUT    /editor-chat/{job_id}/sessions/{sid}       save it (autosave) or rename it
    DELETE /editor-chat/{job_id}/sessions/{sid}       throw one away

⚠ **THE FIVE SESSION ROUTES ARE A FILING CABINET, NOT A MEMORY.** They spend
nothing, call no model, and the agent never reads them: `/turn` is still
stateless and the browser still posts the whole conversation on every message.
They exist so a person can keep *"the sound pass"* apart from *"the titles pass"*
and still find both next week — asked for outright: *"user new chat bana kar alag
alag baat kar sake … aur sab chat save hona chahiye … project by project"*. See
`chat_sessions.py`.

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
import time

from fastapi import APIRouter, Depends, HTTPException

from .auth import CurrentUser, get_current_user
from .common import fill_board_words, get_owned_job
from . import chat_sessions
from . import chat_settings
from . import editor_chat_work
from . import config as app_config
from .features import require_feature
from .jobs import Job
from . import usage as usage_counters
from .schemas import (
    EditorChatAsk,
    EditorChatConfig,
    EditorChatOption,
    EditorChatRequest,
    EditorChatResponse,
    EditorChatSession,
    EditorChatSessionCreate,
    EditorChatSessionList,
    EditorChatSessionSummary,
    EditorChatSessionUpdate,
    EditorChatWorkStatus,
    JobKind,
    JobStatus,
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
        # ⚠ NOT `or 100`. Zero is a REAL setting now that the floor is gone —
        # a panel with no ground at all — and `0 or 100` is 100, so the one value
        # at the very end of the operator's slider would have arrived as its
        # opposite. Every other field on this response can use `or` because "" and
        # 0 are not answers there; this one they are.
        opacity=int(row["opacity"] if row.get("opacity") is not None else 100),
        # ⚠ `is not None` FOR THE SAME REASON — 0 is the shipped default here, so
        # `or 0` would have been harmless by luck and wrong by rule. Both of these
        # fields have a legal zero; neither may use `or`.
        blur=int(row["blur"] if row.get("blur") is not None else 0),
        greeting=row.get("greeting") or "",
        max_turns_per_session=int(row.get("max_turns_per_session") or 0),
        transcript_keep=int(row.get("transcript_keep") or 20),
        # ⚠ THE TAB'S PATIENCE IS DERIVED FROM THE OPERATOR'S NUMBER, HERE, ONCE.
        # It used to be a constant in three files that had to be raised together
        # by hand, and both times it was raised the risk was the same: move the
        # model's clock alone and the browser starts aborting turns the server is
        # still correctly serving — billed, counted, and reported to the user as
        # the server being stuck. One admin field moves all three now.
        turn_timeout_ms=chat_settings.wire_wait_seconds(row) * 1000,
        # ⚠ A THIRD OF THE MODEL'S OWN CLOCK, so a job that fans out into batches
        # of that length reports about three times per batch — often enough that
        # the bar visibly moves, rarely enough that a long job is not thousands of
        # requests. Derived from the operator's number for the same reason the
        # timeout is: a slower deployment should be asked less often, not have a
        # constant in the browser guess at it.
        work_poll_ms=max(1000, chat_settings.turn_budget_seconds(row) * 1000 // 3),
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

    # ⚠ THE TAB'S CLOCK STARTS HERE, NOT AT THE MODEL CALL. `llm_json` times its
    # own attempts, but the user is waiting for the WHOLE route — the ownership
    # lookup, decoding a look's pictures, building the digest, and the reply
    # going back down the wire. When those two numbers are close the model is the
    # story; when they are not, this app is, and until now nothing measured the
    # difference. See RULEBOOK F7/F8.
    turn_started = time.monotonic()

    job = _owned_animatic(job_id, current)

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
            # ⚠ THE FILM'S OWN WORDS, PUT BACK IN BEFORE THE MODEL SEES IT.
            # The browser cannot send them — a description lives on the
            # storyboard PANEL a frame references, not on the frame — so every
            # turn until now described a fourteen-shot film as "Shot 1 … Shot
            # 14" and the sound came back from a different film entirely. Free:
            # a store read, no model call. See `fill_board_words`.
            board=fill_board_words(body.board or {}, job),
            vocabulary=body.capabilities or {},
            settings=settings,
            language=body.language or "",
            pictures=tuple(pictures),
        )
    except EditorChatError as e:
        logger.warning(
            "[editor-chat %s] turn failed after %.1fs: %s",
            job_id, time.monotonic() - turn_started, e,
        )
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001 — report clearly, never a bare 500
        logger.exception(
            "[editor-chat %s] unexpected error after %.1fs",
            job_id, time.monotonic() - turn_started,
        )
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
    # ⚠ `looking=N` IS ON THIS LINE FOR A REASON. A turn that carries pictures is
    # a different animal — E112 — and a log where the slow turns and the seeing
    # turns cannot be told apart makes that correlation invisible.
    logger.info(
        "[editor-chat %s] %s in %.1fs — %d message(s) in, %d step(s), %d dropped, "
        "looking=%d. %s",
        job_id, result.get("kind"), time.monotonic() - turn_started,
        len(messages), steps, len(result.get("dropped") or []), len(pictures),
        f"{used}/{limit} turns" if limit is not None else f"{used} turns (unlimited)",
    )

    # ⚠ A BIG JOB LEAVES THIS REQUEST BEHIND, AND THAT IS THE POINT. `run_work`
    # is minutes of model calls; holding the socket open for it is the thing that
    # cannot be made reliable at any timeout (E142, and the module docstring in
    # `editor_chat_work.py`). The brief goes to a job, the id comes back now, and
    # the panel watches it. ⚠ THE TURN IS ALREADY BILLED ABOVE — the batches are
    # not billed again; the tier sells messages, and this is one message.
    work_id = None
    if result.get("kind") == "work" and result.get("work"):
        try:
            work_id = editor_chat_work.start(
                animatic_id=job_id,
                owner=current.email,
                work=result["work"],
                # ⚠ THE SAME BOARD THE TURN READ, NOT A FRESH ONE. Re-deriving it
                # in the worker would read a store that may have moved on, and the
                # shot numbers the brief was written against would no longer be
                # the shot numbers the batches write for.
                board=fill_board_words(body.board or {}, job),
                vocabulary=body.capabilities or {},
                settings=settings,
                language=body.language or "",
            )
        except Exception as e:  # noqa: BLE001 — a job that will not start is a turn
            logger.exception("[editor-chat %s] could not start the work job", job_id)
            raise HTTPException(
                status_code=502, detail=f"Could not start that job: {e}"
            ) from None

    return EditorChatResponse(
        kind=result.get("kind") or "answer",
        reply=result.get("reply") or "",
        ask=ask,
        plan=result.get("plan"),
        work_id=work_id,
        work=result.get("work") if work_id else None,
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


# ===========================================================================
# BIG JOBS — a message that is really five jobs over sixty shots
# ===========================================================================
# ⚠ NEITHER OF THESE SPENDS A TURN. The message was billed when it was sent; this
# is watching what it is doing. A poll that counted against the allowance would
# charge a person for the progress bar.


def _work_status(work_id: str, current: CurrentUser) -> EditorChatWorkStatus:
    """One running (or finished) big job, read as the panel needs it."""
    from .jobs import get_store

    row = get_store().get(work_id)
    # ⚠ OWNERSHIP IS CHECKED ON THE RECORD, AND A MISS IS A 404 RATHER THAN A 403.
    # "That job exists but is not yours" is a sentence that confirms somebody
    # else's job id, and there is nothing here worth leaking it for.
    owner = (getattr(row, "owner", "") or "").strip().lower()
    if not row or row.kind != JobKind.EDITOR_CHAT or owner != (current.email or "").strip().lower():
        raise HTTPException(status_code=404, detail="No such job.")

    progress = row.progress or {}
    base = {
        "work_id": work_id,
        "done": int(progress.get("done_parts") or 0),
        "total": int(progress.get("total_parts") or 0),
        "percent": int(progress.get("percent") or 0),
        "message": str(progress.get("message") or ""),
    }
    if row.status == JobStatus.SUCCEEDED:
        turn = row.result if isinstance(row.result, dict) else {}
        return EditorChatWorkStatus(
            state="done", turn=turn, stopped=bool(turn.get("stopped")), **base
        )
    if row.status == JobStatus.FAILED:
        return EditorChatWorkStatus(state="failed", error=row.error or "That job failed.", **base)
    # ⚠ RUNNING, ACCORDING TO THE RECORD — but the record is written by a process
    # that may not be this one any more. A run with no future in `_runs` is a run
    # whose server restarted under it, and it will never finish. Saying so is the
    # only honest answer; the alternative is a bar that never moves again.
    if not editor_chat_work.is_live(work_id):
        if editor_chat_work.known(work_id):
            # It ran here and the thread is gone without writing an ending — a
            # crash the `except` in the runner could not catch.
            return EditorChatWorkStatus(
                state="failed",
                error="That job stopped without finishing. Ask again.",
                **base,
            )
        return EditorChatWorkStatus(
            state="lost",
            error="The server restarted while that was running, so it was lost. "
                  "Nothing was changed — ask again.",
            **base,
        )
    return EditorChatWorkStatus(state="running", **base)


@router.get("/work/{work_id}", response_model=EditorChatWorkStatus)
def work_status(
    work_id: str,
    current: CurrentUser = Depends(get_current_user),
) -> EditorChatWorkStatus:
    """How a big job is going, and its answer once it lands. Free — no model call."""
    return _work_status(work_id, current)


@router.post("/work/{work_id}/stop", response_model=EditorChatWorkStatus)
def work_stop(
    work_id: str,
    current: CurrentUser = Depends(get_current_user),
) -> EditorChatWorkStatus:
    """Stop a big job. ⚠ IT STOPS THE SPEND, NOT THE WAIT.

    A model call already in flight cannot be un-sent and is paid for either way.
    What this prevents is every batch that has not started — most of them, on a
    long film — and what was written by then still comes back as a real plan the
    person can apply. Ownership is checked before the flag is set, by reading the
    record: a stop is a write, and an unauthenticated one would be a way to
    cancel other people's work.
    """
    status = _work_status(work_id, current)
    if status.state == "running":
        editor_chat_work.stop(work_id)
    return status


# ===========================================================================
# THE CHATS THEMSELVES — one project, many conversations, saved
# ===========================================================================
# ⚠ NOT ONE OF THESE FIVE SPENDS ANYTHING, CALLS A MODEL, OR IS READ BY ONE.
# `/turn` above is still stateless: the browser owns the conversation and posts
# it whole every message. This is the filing cabinet beside it — what a person
# opens next week to see what they already had done in this film.
#
# ⚠ AND THE FEATURE GATE IS DELIBERATELY *NOT* ON THEM. `cap.editor-chat` being
# switched off must not make somebody's saved conversations unreadable — a
# feature you lose access to should stop producing new work, not eat the old.
# The quota gate is likewise absent: reading back what you already paid for is
# not a second charge.


def _title(raw: str) -> str:
    """A chat's label, trimmed to something a narrow list can draw."""
    return (raw or "").strip()[: app_config.MAX_CHAT_TITLE_CHARS]


def _rules() -> tuple[int, int, int]:
    """`(chats per project, turns kept per chat, characters per chat)`.

    ⚠ THE OPERATOR'S NUMBERS, READ FRESH FROM THE ADMIN PANEL — not constants
    and no longer environment variables. Asked for outright: *"isme admin panel
    mai v daalo, mai limit set kar dunga — mai jitna daalun wahi hona chahiye"*.
    `get_settings` is cached and never raises; an unreachable store answers with
    the shipped defaults, which is a working chat.
    """
    row = chat_settings.get_settings()
    return (
        int(row.get("max_chats_per_project", 40)),
        int(row.get("chat_history_keep", 60)),
        int(row.get("max_chat_chars", 400_000)),
    )


def _trim(turns: list | None, keep: int) -> list | None:
    """The last `keep` turns. `None` stays `None` — see `save_session`.

    ⚠ THE NEWEST ONES, AND THE OPERATOR SETS HOW MANY. A conversation past the
    ceiling loses its OLDEST turns, which is the only end that can be dropped
    without the chat stopping making sense.
    """
    if turns is None:
        return None
    return turns[-keep:] if keep > 0 and len(turns) > keep else turns


def _too_big(turns: list | None, ceiling: int) -> bool:
    """⚠ MEASURED AS IT WILL BE STORED, not by counting turns. Sixty short lines
    and sixty pasted scripts are the same number of turns and nowhere near the
    same document.

    ⚠ AND IT REFUSES RATHER THAN TRUNCATES. Silently dropping half of somebody's
    conversation to make it fit is the kind of help nobody asked for; the panel
    keeps what is on screen and says it is not being saved.
    """
    if turns is None:
        return False
    try:
        import json as _json

        return len(_json.dumps(turns)) > ceiling
    except (TypeError, ValueError):
        # Not serialisable at all — refuse it rather than store something the
        # next read cannot get back out.
        return True


@router.get("/{job_id}/sessions", response_model=EditorChatSessionList)
def list_sessions(
    job_id: str,
    current: CurrentUser = Depends(get_current_user),
) -> EditorChatSessionList:
    """This project's chats, newest first, WITHOUT their transcripts.

    Never 404s on "none yet" — a project with no conversations is a valid
    state and the panel should not have to treat it as an error.
    """
    _owned_animatic(job_id, current)
    rows = chat_sessions.list_sessions(current.email, job_id)
    ceiling, _keep, _chars = _rules()
    return EditorChatSessionList(
        sessions=[EditorChatSessionSummary(**r) for r in rows],
        # The operator's ceiling, sent so the panel can say "38 of 40" rather
        # than only discovering it by being refused at the ＋ button. 0 is
        # "no limit" and the panel prints nothing.
        limit=ceiling,
    )


@router.post("/{job_id}/sessions", response_model=EditorChatSession)
def create_session(
    job_id: str,
    body: EditorChatSessionCreate,
    current: CurrentUser = Depends(get_current_user),
) -> EditorChatSession:
    """Start a new chat in this project — the ＋ button.

    ⚠ THE ID IS MINTED HERE, NOT IN THE BROWSER. Two tabs open on one project
    must not be able to agree on the same id by accident and write into each
    other's conversation.

    ⚠ AND `turns` MAY ARRIVE FULL ON THE VERY FIRST CREATE. The editor opens on
    a project that does not exist yet, so the first message is what creates it —
    those turns happened before there was anything to save them against, and
    this is how they arrive rather than being lost.
    """
    _owned_animatic(job_id, current)

    ceiling, keep, chars = _rules()
    if _too_big(body.turns, chars):
        raise HTTPException(
            status_code=413,
            detail=f"That conversation is too long to save (limit {chars:,} characters).",
        )

    # ⚠ 0 IS "NO LIMIT", NOT "FALL BACK TO THE DEFAULT". An operator who does
    # not want a ceiling has to be able to say so, and this is how they say it.
    if ceiling > 0 and chat_sessions.count_sessions(current.email, job_id) >= ceiling:
        # ⚠ SWEEP THE EMPTY ONES FIRST, AND ONLY THE EMPTY ONES. A ＋ pressed by
        # mistake must not be what fills somebody's ceiling — but making room by
        # deleting the oldest conversation regardless would throw away work,
        # silently, which is the one thing this store must never do.
        if not chat_sessions.drop_one_unused(current.email, job_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This project already has {ceiling} chats. "
                    "Delete one to start another."
                ),
            )

    sid = chat_sessions.new_session_id()
    row = chat_sessions.save_session(
        current.email,
        job_id,
        sid,
        title=_title(body.title),
        title_auto=bool(body.title_auto),
        turns=_trim(body.turns or [], keep),
    )
    return EditorChatSession(**row)


@router.get("/{job_id}/sessions/{session_id}", response_model=EditorChatSession)
def read_session(
    job_id: str,
    session_id: str,
    current: CurrentUser = Depends(get_current_user),
) -> EditorChatSession:
    """One whole chat, transcript and all — what opening a row in 🕘 asks for."""
    _owned_animatic(job_id, current)
    row = chat_sessions.get_session(current.email, job_id, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That chat is not here.")
    return EditorChatSession(**row)


@router.put("/{job_id}/sessions/{session_id}", response_model=EditorChatSession)
def write_session(
    job_id: str,
    session_id: str,
    body: EditorChatSessionUpdate,
    current: CurrentUser = Depends(get_current_user),
) -> EditorChatSession:
    """Save a chat (the autosave) or rename it.

    ⚠ `None` MEANS "LEAVE IT ALONE" AND `[]` MEANS "IT IS EMPTY", all the way
    down to the store. A rename posts a title and no turns; an autosave posts
    turns and no title. Collapsing the two would make renaming a chat delete
    its transcript.

    ⚠ IT UPSERTS ON PURPOSE. The browser retries an autosave that failed while
    the network was away, and a 404 there would throw away the conversation it
    was trying to rescue.
    """
    _owned_animatic(job_id, current)

    _ceiling, keep, chars = _rules()
    if _too_big(body.turns, chars):
        raise HTTPException(
            status_code=413,
            detail=f"That conversation is too long to save (limit {chars:,} characters).",
        )

    row = chat_sessions.save_session(
        current.email,
        job_id,
        session_id,
        title=None if body.title is None else _title(body.title),
        # ⚠ A NAME A PERSON CHOSE OUTRANKS THE FIRST LINE OF THE CHAT. The
        # autosave marks its title automatic; the rename box does not, and that
        # is what locks it. The store is where the refusal lives, so a reload,
        # a second tab and a retried autosave all obey it.
        title_auto=bool(body.title_auto),
        # ⚠ `_trim` PASSES `None` STRAIGHT THROUGH. A rename sends no turns, and
        # a trim that turned that into `[]` would delete the transcript — the
        # exact bug the `None`/`[]` split exists to prevent.
        turns=_trim(body.turns, keep),
    )
    return EditorChatSession(**row)


@router.delete("/{job_id}/sessions/{session_id}", status_code=204)
def remove_session(
    job_id: str,
    session_id: str,
    current: CurrentUser = Depends(get_current_user),
) -> None:
    """Throw one chat away. Silent when it was already gone — a delete that
    404s on the second click is a delete that looks broken."""
    _owned_animatic(job_id, current)
    chat_sessions.delete_session(current.email, job_id, session_id)
