"""
editor_chat_work.py — the ✨ AI Editor's BIG jobs, run off the request thread.

WHY THIS EXISTS
---------------
⚠ **A SINGLE HTTP REQUEST CANNOT BE MADE RELIABLE AT TEN MINUTES, AND RAISING
THE TIMEOUT ONLY MOVES WHERE IT BREAKS.** E142 made the chat's clock an admin
field and that was right as far as it went — but it went as far as 180 seconds
and stopped, because past that the socket, the proxy and the browser each cut
the call on their own schedule. The operator asked the question that follows
from that:

    *"agar mai 3 ke jagah 5/10 kuchh kaam karwaye jaise full editing to ek baar
     mai kaise legega … agar user unlimited wala plan liya hai to woh ek baar mai
     sab kaam karwa sakta hai na"*

Yes — and the answer is not a longer request. **An unlimited plan means
unlimited MESSAGES, not one message allowed to run for ever.** So a big message
stops being a turn and becomes a JOB: it returns immediately with an id, runs
behind the request, reports progress, can be stopped, and cannot time out
because nothing is holding a socket open waiting for it.

WHAT RUNS HERE
--------------
`editor_chat_agent.run_work` — the fan-out. One brief becomes N batches of a
dozen shots each, four calls at a time, merged back into ONE ordinary plan. The
thinking is not compressed: every batch still reads real shot descriptions and
still decides cut by cut. See the BIG WORK block in `editor_chat_agent.py`.

⚠ **THE RECORD IS IN THE JOB STORE; THE INPUTS ARE NOT.** Status, progress,
result and error go through `jobs.get_store()` like every other workflow in this
app, so a run is visible and survives being read from anywhere. The BOARD and the
capability manifest do not: they are megabytes of the browser's document, needed
only while the run is alive, and writing them to Mongo on every big message would
be write amplification bought for nothing. They live in `_runs` beside the pool
that reads them — the same decision, for the same reason, that `cancel.py` made.

⚠ **AND THAT MAKES A RESTART A REAL STATE, SO IT IS HANDLED RATHER THAN HOPED
AWAY.** A record left RUNNING with no runner in this process is a run whose
server went away mid-flight. `status()` says so, in words, instead of returning
a progress bar that will never move again — a spinner that spins for ever is the
worst of the three possible answers.

WHAT IT COSTS
-------------
⚠ **THE TURN IS BILLED ONCE, AT THE TURN, AND THE BATCHES ARE NOT BILLED AGAIN.**
The tier sells `chat_turns` — messages — and this is one message. Charging per
batch would mean a person asking for one thing on a long film pays more than the
same request on a short one, which is a price nobody agreed to and cannot
predict. The MODEL calls are real and the deployment pays for them; that is what
`MAX_WORK_BATCHES` is a ceiling on.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cancel

from .jobs import get_store
from .schemas import JobKind, JobStatus

logger = logging.getLogger(__name__)

# ⚠ ITS OWN POOL, NOT THE PIPELINE'S. A chat job is a minute of model calls; a
# render holds its thread for many. Sharing would let one final-video project
# park every chat in the deployment behind it — the same reasoning `worker.py`
# already applied when it gave video its own pool.
#
# ⚠ AND IT IS SMALL ON PURPOSE. Each run fans out to `MAX_PARALLEL_CALLS` model
# calls of its own, so three concurrent runs are already a dozen calls at the
# provider. This number multiplies that one; it is not a queue depth.
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="chatwork")

_lock = threading.Lock()
# job_id -> {"owner", "started", "future"}. Only what this process needs to know
# that a run is really alive. Everything a CLIENT needs is in the job store.
_runs: dict[str, dict] = {}

# How long a finished run's record stays readable before the store's own cleanup
# is the only thing keeping it. The panel polls every second or two; a minute is
# many chances to collect the answer, and a run nobody collected is a browser
# that was closed.
KEEP_FINISHED_S = 15 * 60


def start(*, animatic_id: str, owner: str, work: dict, board: dict,
          vocabulary: dict, settings: dict, language: str = "") -> str:
    """Queue a work brief. Returns the job id — immediately, before anything runs.

    ⚠ THE RECORD IS CREATED BEFORE THE FUTURE IS SUBMITTED. A client that polls
    the instant it gets the id must find something; creating the row afterwards
    leaves a window in which a perfectly healthy job answers 404 and the panel
    tells the person it was lost.
    """
    from editor_chat_agent import work_batches

    store = get_store()
    total = len(work_batches(work or {}, len((board or {}).get("shots") or [])))
    job = store.create(
        character_name=f"AI Editor job on {animatic_id}",
        kind=JobKind.EDITOR_CHAT,
        owner=owner,
        params={"animatic_id": animatic_id, "batches": total},
    )
    job_id = job.job_id
    store.update(
        job_id,
        status=JobStatus.RUNNING,
        progress={
            "percent": 0,
            "stage": "starting",
            "done_parts": 0,
            "total_parts": total,
            "message": "Reading the film…",
        },
    )

    def run():
        from editor_chat_agent import EditorChatError, run_work

        started = time.monotonic()

        def on_progress(done: int, total_parts: int, message: str):
            store.update(
                job_id,
                progress={
                    "percent": int(100 * done / max(1, total_parts)),
                    "stage": "writing",
                    "done_parts": done,
                    "total_parts": total_parts,
                    "message": message,
                },
            )

        try:
            result = run_work(
                work=work,
                board=board,
                vocabulary=vocabulary,
                settings=settings,
                language=language,
                on_progress=on_progress,
                cancelled=lambda: cancel.is_cancelled(job_id),
            )
        except EditorChatError as e:
            logger.warning("[chat-work %s] failed after %.1fs: %s",
                           job_id, time.monotonic() - started, e)
            store.update(job_id, status=JobStatus.FAILED, error=str(e))
            return
        except Exception as e:  # noqa: BLE001 — a job must never die silently
            logger.exception("[chat-work %s] unexpected error", job_id)
            store.update(job_id, status=JobStatus.FAILED, error=f"AI Editor error: {e}")
            return
        finally:
            # ⚠ CLEARED WHATEVER HAPPENED. A stop flag left behind would refuse
            # the FIRST batch of the next job to be handed this id by the store.
            cancel.clear_cancel(job_id)

        logger.info(
            "[chat-work %s] done in %.1fs — %d step(s), %s",
            job_id, time.monotonic() - started,
            len((result.get("plan") or {}).get("steps") or []),
            "stopped" if result.get("stopped") else "complete",
        )
        store.update(job_id, status=JobStatus.SUCCEEDED, result=result, progress={
            "percent": 100,
            "stage": "done",
            "done_parts": total,
            "total_parts": total,
            "message": "Done",
        })

    with _lock:
        _runs[job_id] = {
            "owner": (owner or "").strip().lower(),
            "started": time.time(),
            "future": _pool.submit(run),
        }
    _sweep()
    return job_id


def _sweep() -> None:
    """Forget runs nobody is watching any more. Cheap, and called on every start."""
    now = time.time()
    with _lock:
        for job_id in [
            k for k, v in _runs.items()
            if v["future"].done() and now - v["started"] > KEEP_FINISHED_S
        ]:
            _runs.pop(job_id, None)


def is_live(job_id: str) -> bool:
    """Is this run actually running IN THIS PROCESS right now?"""
    with _lock:
        row = _runs.get(job_id)
    return bool(row and not row["future"].done())


def known(job_id: str) -> bool:
    """Has this process ever run this job? False after a restart."""
    with _lock:
        return job_id in _runs


def stop(job_id: str) -> None:
    """Ask a run to stop. ⚠ IT STOPS THE SPEND, NOT THE WAIT.

    A model call already in flight cannot be un-sent and is paid for either way.
    What this prevents is every batch that has not started yet — which on a long
    film is most of them, and is what the person pressing Stop actually wants.
    The batches that did land are still returned as a real, applicable plan.
    """
    cancel.request_cancel(job_id)
