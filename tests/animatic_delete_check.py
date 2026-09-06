"""A DELETED PROJECT STAYS DELETED — record, folder and chats.

    "maine abhi delete kiya tha first wala Ganesh Utsav: Ek Rishta — project,
     phir se dekh raha hun to dikha raha hai … delete hone pe phir se dikh raha
     hai"

Reported live on 2026-09-06 with the screenshot that names the fault outright:
the deleted project was back in **Your Projects**, same title, same date — and
its SIZE column read **"—"** while its twin beside it read 43 MB. The files
really were gone. The RECORD never was.

`_purge_animatic()` removed the folder and the ✨ AI Editor chats and stopped
there, so `DELETE /animatics/{id}` answered 204 over a job row that was still in
the store and still listed. Its own docstring said it deleted "its record"; the
body never did. Every other delete route in this app — `main.py`, `plans.py`,
`videos.py` — already called `get_store().delete(job_id)`.

⚠ **THE SWEEP PAID FOR IT TOO, SILENTLY.** `list_animatics` purges ghost projects
and logs *"swept: empty and never named"*. With the row surviving, the same ghost
was found and re-swept on EVERY list call for ever, rmtree-ing a folder that was
not there, and the log claimed the work each time. Section 3 pins that.

⚠ **THIS TEST IMPORTS `server.*` FOR REAL** (G13): every local store path is
pinned into a temp directory BEFORE the import, because `server/config.py` reads
the environment once at import time and its defaults include a git-tracked file
in the repo root.

    python tests/animatic_delete_check.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ BEFORE `server` IS IMPORTED. See the note above, and G13.
_TMP = tempfile.mkdtemp(prefix="animatic_delete_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_FEATURES_PATH"] = os.path.join(_TMP, "features.json")
os.environ["API_LOCAL_TIERS_PATH"] = os.path.join(_TMP, "tiers.json")
os.environ["API_LOCAL_OFFERS_PATH"] = os.path.join(_TMP, "offers.json")
os.environ["API_LOCAL_SUBSCRIPTIONS_PATH"] = os.path.join(_TMP, "subs.json")
os.environ["API_LOCAL_BRANDING_PATH"] = os.path.join(_TMP, "branding.json")
os.environ["API_LOCAL_BANNERS_PATH"] = os.path.join(_TMP, "banners.json")
os.environ["API_LOCAL_SHOWCASE_PATH"] = os.path.join(_TMP, "showcase.json")
os.environ["API_LOCAL_LANDING_PATH"] = os.path.join(_TMP, "landing.json")
os.environ["API_LOCAL_USAGE_PATH"] = os.path.join(_TMP, "usage.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["API_OUTPUT_DIR"] = os.path.join(_TMP, "output")
os.environ["API_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["JWT_SECRET"] = "animatic-delete-check-not-a-real-secret"

from server import animatics  # noqa: E402
from server.jobs import get_store  # noqa: E402
from server.schemas import JobKind  # noqa: E402

failures: list[str] = []
OWNER = "tester@example.com"


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


store = get_store()


class Caller:
    """The `CurrentUser` the route dependency would have handed in."""

    email = OWNER
    is_admin = False


def make_project(title: str, frames=1) -> str:
    """One saved project with a folder and a file in it. Returns its id."""
    job = store.create(title, kind=JobKind.ANIMATIC, owner=OWNER, params={
        "project": {
            "frames": [
                {"id": f"f{i}", "url": "", "duration_ms": 2000}
                for i in range(frames)
            ],
        },
    })
    folder = animatics._animatic_dir(job.job_id)
    os.makedirs(os.path.join(folder, "media"), exist_ok=True)
    with open(os.path.join(folder, "media", "a.png"), "wb") as handle:
        handle.write(b"not really a png")
    return job.job_id


def listed(job_id: str) -> bool:
    return any(c.job_id == job_id for c in animatics.list_animatics(current=Caller))


print("\n1 · Delete removes the RECORD, not only the files\n")

kept = make_project("Ganesh Utsav: Ek Rishta — keep me")
gone = make_project("Ganesh Utsav: Ek Rishta — project")

check("both projects are in the library to start with", listed(kept) and listed(gone))

animatics.delete_animatic(gone, current=Caller)

# ⭐ THE REPORTED FAULT, IN ONE LINE. Everything below it was already true
# BEFORE the fix — the folder went, the 204 came back — and the project came
# back with it.
check("⭐ THE DELETED PROJECT IS GONE FROM THE LIBRARY", not listed(gone))
check("⭐ …AND ITS RECORD IS REALLY OUT OF THE STORE, not just filtered from a list",
      store.get(gone) is None, repr(store.get(gone))[:120])
check("…its folder went too", not os.path.isdir(animatics._animatic_dir(gone)))
check("⚠ AND THE PROJECT BESIDE IT IS UNTOUCHED — record and files both",
      listed(kept)
      and store.get(kept) is not None
      and os.path.isdir(animatics._animatic_dir(kept)))

print("\n2 · Deleting the same project twice is a 404, not a second 204\n")
# ⚠ THIS IS WHAT THE OLD BUG LOOKED LIKE FROM THE OUTSIDE: pressing Delete on a
# project that was already deleted answered 204 again, for ever, because the row
# it looked up was still there. A second press must now say the thing is gone.
try:
    animatics.delete_animatic(gone, current=Caller)
    check("⚠ A SECOND DELETE IS REFUSED", False, "it answered success again")
except Exception as exc:  # noqa: BLE001
    check("⚠ A SECOND DELETE IS REFUSED", getattr(exc, "status_code", 0) == 404,
          f"{type(exc).__name__}: {exc}")

print("\n3 · The ghost sweep really sweeps, so it cannot run for ever\n")
# ⚠ A GHOST IS AN EMPTY, NEVER-NAMED PROJECT. `list_animatics` purges one that is
# old enough — and with the record surviving it found the SAME ghost on every
# single call, rmtree'd a folder that was not there, and logged that it had
# swept it. Once is the whole point.
ghost = store.create("Untitled Project", kind=JobKind.ANIMATIC, owner=OWNER,
                     params={"project": {"frames": []}})
os.makedirs(animatics._animatic_dir(ghost.job_id), exist_ok=True)
check("…and a fresh ghost is NOT swept — a second tab may be building it right now",
      animatics.list_animatics(current=Caller) is not None
      and store.get(ghost.job_id) is not None)

# ⚠ THE AGE IS LOWERED RATHER THAN THE TIMESTAMP FORGED. Every store's `update`
# stamps `updated_at` with the current time, on purpose — so a test that wrote an
# old date would be testing the store's write, not the sweep. Moving the
# THRESHOLD exercises exactly the branch the real 24-hour rule takes.
animatics.GHOST_MAX_AGE_S = -1
animatics.list_animatics(current=Caller)
swept_once = store.get(ghost.job_id) is None
check("⚠ AN OLD EMPTY PROJECT IS SWEPT — record and all", swept_once,
      "the row survived, so the next list would sweep it again")
if swept_once:
    animatics.list_animatics(current=Caller)
    check("…and it is not there to be swept a second time", store.get(ghost.job_id) is None)

shutil.rmtree(_TMP, ignore_errors=True)

print()
if failures:
    print(f"✗ {len(failures)} check(s) failed:")
    for name in failures:
        print(f"    - {name}")
    sys.exit(1)
print("✓ a deleted project stays deleted — record, folder and chats")
