"""ONE FOLDER FOR EVERY TEST SCREENSHOT, AND THAT FOLDER IS GIT-IGNORED.

⚠ **THE BROWSER SUITES USED TO DROP THEIR SCREENSHOTS IN THE REPO ROOT.** A
failing probe wrote `restack_probe_bands.png`, `bin_probe_failed.png`,
`row_routing_failed.png` … straight next to `server/` and `client/`, so the next
`git status` showed fifty-odd pending changes and the debugging trail from one
afternoon's test run started getting committed as if it were source.
`row_routing_failed.png` actually made it into a commit that way. `.gitignore`
only knew the `*_probe_failed.png` spelling, so every screenshot named anything
else — a passing-run reference shot, a `_bands`/`_blend` variant — slipped past
it.

⚠ **SO NO SUITE PICKS ITS OWN LOCATION ANY MORE.** Everything goes under
`test_shots/` at the repo root, which `.gitignore` drops whole (all but its
README). Use it like this:

    from _shots import shot          # bare import: `tests/` is on sys.path

    page.screenshot(path=shot("bin_probe_failed.png"))

and for a suite that keeps a set of related frames, give it a subfolder so one
run's shots stay together:

    page.screenshot(path=shot("bands.png", "restack"))

⚠ **THE DIRECTORY IS MADE ON DEMAND, NEVER ASSUMED.** `shot()` and
`shots_dir()` create the folder (and any subfolder) on every call, so no suite
needs an `os.makedirs` of its own and none should keep one. Ask for a SUBfolder
at the moment you write into it rather than at import time — a suite that never
reaches its screenshots should not leave an empty folder behind.

⚠ **`tests/` IS ON `sys.path` BECAUSE THESE ARE RUN AS SCRIPTS** (`python
tests/foo.py`), so the bare `_shots` import needs no path juggling — same
reasoning as `tests/_sandbox.py`. The leading underscore keeps it out of any
`tests/*_check.py` sweep.
"""

import os

# The repo root, from `tests/_shots.py` → `tests/` → root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠ IF THIS NAME CHANGES, `.gitignore` CHANGES WITH IT. The two are a pair;
# renaming only one puts the whole debugging trail back into `git status`.
SHOTS_DIR = os.path.join(ROOT, "test_shots")


def shots_dir(sub=None):
    """The screenshot folder (or a subfolder of it), created if missing."""
    path = os.path.join(SHOTS_DIR, sub) if sub else SHOTS_DIR
    os.makedirs(path, exist_ok=True)
    return path


def shot(name, sub=None):
    """Full path to write one screenshot to. Pass this straight to Playwright."""
    return os.path.join(shots_dir(sub), name)
