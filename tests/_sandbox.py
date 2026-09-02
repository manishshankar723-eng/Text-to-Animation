"""EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE `server.config` LOADS.

⚠ **THIRTEEN SUITES USED TO BOOT THE REAL APP AGAINST THE REAL DATABASE.** They
import `server.main`, register a user and POST their way through a workflow —
and with nothing pinned, `server/config.py` reads the developer's `.env`, so
every run wrote test accounts and test jobs into the production Mongo cluster
and spent the developer's own monthly quota. `tests/hidden_lane_check.py` is how
it surfaced: it failed on its third project with *"You've used 2 of your 2
projects this month"*, which reads like a broken test and was billing refusing a
test suite. G13 is the rule; this is the tool that keeps it.

⚠ **CONFIG READS THE ENVIRONMENT ONCE, AT IMPORT TIME.** So `pin()` has to run
BEFORE any `server.*` import — which is why it is a call at the top of the file
rather than a fixture, and why the `server` imports below it carry `# noqa: E402`.

    from _sandbox import pin          # ⚠ FIRST, before any server import
    _TMP = pin("my_check_")

    from server.main import app       # noqa: E402

⚠ **`tests/` IS ON `sys.path` BECAUSE THESE ARE RUN AS SCRIPTS** — `python
tests/foo.py` puts the script's own directory first — so the bare `_sandbox`
import needs no path juggling. It is named with a leading underscore so it is
never mistaken for a suite by a `tests/*_check.py` sweep.

⚠ **THE OLDER SUITES SPELL THIS BLOCK OUT INLINE** (`admin_check.py`,
`import_dedupe_check.py`, `media_cleanup_check.py`, `hidden_lane_check.py`) and
were left that way on purpose: several of them set extra variables of their own
around it, and rewriting a green suite to save duplication is a change with no
upside and a real downside.
"""

import atexit
import json
import os
import shutil
import tempfile

# ⚠ EVERY LOCAL STORE PATH, not the ones a suite happens to touch. A path left
# out defaults to a git-tracked file in the repo root, and the failure mode is a
# test suite quietly editing the developer's own data — which is exactly what
# this module exists to stop. When `server/config.py` grows a new
# `API_LOCAL_*_PATH`, add it here.
_LOCAL_PATHS = (
    "API_LOCAL_USERS_PATH", "API_LOCAL_JOBS_PATH", "API_LOCAL_DRAFTS_PATH",
    "API_LOCAL_EVENTS_PATH", "API_LOCAL_FEATURES_PATH", "API_LOCAL_TIERS_PATH",
    "API_LOCAL_OFFERS_PATH", "API_LOCAL_SUBSCRIPTIONS_PATH",
    "API_LOCAL_BRANDING_PATH", "API_LOCAL_BANNERS_PATH",
    "API_LOCAL_SHOWCASE_PATH", "API_LOCAL_LANDING_PATH", "API_LOCAL_USAGE_PATH",
)


def pin(prefix: str = "sandbox_") -> str:
    """Point every store at a fresh temp directory. Returns the directory.

    ⚠ CALL IT BEFORE ANY `server.*` IMPORT. See the module docstring.
    """
    tmp = tempfile.mkdtemp(prefix=prefix)
    # ⚠ SWEPT UP HERE RATHER THAN BY EACH SUITE. A suite that ends in
    # `sys.exit(1)` still runs its atexit handlers, so the failing run — the one
    # most likely to be repeated — cleans up too.
    atexit.register(shutil.rmtree, tmp, True)
    os.environ["API_USER_STORE"] = "local"
    os.environ["API_JOB_STORE"] = "memory"
    for name in _LOCAL_PATHS:
        os.environ[name] = os.path.join(tmp, name[10:-5].lower() + ".json")
    os.environ["API_OUTPUT_DIR"] = os.path.join(tmp, "output")
    os.environ["API_UPLOAD_DIR"] = os.path.join(tmp, "uploads")
    # ⚠ THE STARTUP SWEEP CLOSES OUT EVERY QUEUED JOB IT FINDS. Harmless against
    # a fresh temp store and not worth the wait on every suite.
    os.environ["API_REAP_ORPHANED_JOBS"] = "0"
    os.environ.setdefault("JWT_SECRET", "sandbox-not-a-real-secret-0123456789")

    # ⚠ **AND THE QUOTA IS LIFTED, OR THE SUITE IS BILLED.** A test account starts
    # on the default tier like anyone else, and that tier's project allowance is a
    # BUSINESS DECISION — it was 2 when this was written and it will move. A suite
    # that creates four projects must not fail the day marketing changes a number.
    #
    # ⚠ **DONE BY OVERRIDING THE TIER, NOT BY MAKING THE USER AN ADMIN.** Admins
    # skip every quota AND every feature gate, so `ADMIN_EMAILS` would also
    # silently switch off the `require_feature` guards these suites are partly
    # there to exercise — and several of them register a SECOND, deliberately
    # ordinary user to check what it cannot reach. The stored tier merges over
    # the built-in catalogue (`billing.all_tiers`), and an empty `limits` means
    # unlimited (`usage.limit_of`: missing and None both mean no ceiling).
    with open(os.environ["API_LOCAL_TIERS_PATH"], "w", encoding="utf-8") as fh:
        json.dump({"trial": {"id": "trial", "limits": {}}}, fh)
    return tmp
