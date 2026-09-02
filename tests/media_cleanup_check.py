"""THE SWEEP THAT REMOVES OLD DUPLICATE MEDIA MUST NEVER REMOVE ANYTHING ELSE.

    "Purani 52 files disk par abhi bhi hain — dedupe aage se kaam karega,
     peeche wali copies apne aap nahi hatengi (bataiye to safai ka rasta bana
     doon)"

`cleanup_media.py` is that route. Content addressing (E70 /
`tests/import_dedupe_check.py`) stopped the leak; it did not remove the copies
already written, and the only way to do that is to delete somebody's footage —
so every assertion here is about what the sweep REFUSES to touch.

⚠ **THE FOUR GUARDS, AND EACH ONE IS THE DIFFERENCE BETWEEN A TIDY-UP AND A
LOST FILM.** They are pinned one per section below:

  1. A media folder whose project cannot be loaded is skipped WHOLE. Without the
     project there is nothing to ask "is this in use", and "no references found"
     would then mean "delete everything".
  2. IDENTICAL BYTES, never an identical name. Two files called `logo.png` can
     be two different pictures.
  3. A REFERENCED file is never deleted, even when it is one of several copies.
  4. THE LAST COPY is never deleted, so a picture sitting unused in the Media
     pane cannot vanish.

⚠ **THIS TEST IMPORTS `server.*` FOR REAL**, which G13 is about: every local
store path is pinned into a temp directory BEFORE the import, because
`server/config.py` reads the environment once at import time and its defaults
include a git-tracked file in the repo root.

    python tests/media_cleanup_check.py
"""

import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ BEFORE `server` IS IMPORTED. See the note at the top, and G13.
_TMP = tempfile.mkdtemp(prefix="media_cleanup_")
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
os.environ["JWT_SECRET"] = "media-cleanup-check-not-a-real-secret"

from PIL import Image

import cleanup_media
from server import config
from server.jobs import get_store
from server.schemas import JobKind

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


def png(colour) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (12, 12), colour).save(buf, "PNG")
    return buf.getvalue()


store = get_store()


def project(params: dict) -> str:
    """One animatic job with a media folder of its own. Returns its id."""
    job = store.create("Sweep test", kind=JobKind.ANIMATIC, owner="tester",
                       params=params)
    os.makedirs(os.path.join(config.OUTPUT_DIR, "_animatics", job.job_id, "media"),
                exist_ok=True)
    return job.job_id


def put(job_id: str, name: str, data: bytes) -> None:
    path = os.path.join(config.OUTPUT_DIR, "_animatics", job_id, "media", name)
    with open(path, "wb") as handle:
        handle.write(data)


def on_disk(job_id: str) -> list:
    media = os.path.join(config.OUTPUT_DIR, "_animatics", job_id, "media")
    return sorted(f for f in os.listdir(media) if not f.startswith("."))


def sweep(job_id: str, apply: bool = True):
    folder = os.path.join(config.OUTPUT_DIR, "_animatics", job_id)
    return cleanup_media.sweep(folder, store, apply)


# ---------------------------------------------------------------------------
# What it is for: the second copy of a file the project already had
# ---------------------------------------------------------------------------
print("The duplicate an old import left behind")
RED, BLUE = png((200, 30, 30)), png((30, 30, 200))
USED = project({"frames": [{"src": {"upload_id": "keepme"}}], "assets": []})
put(USED, "img_keepme.png", RED)
put(USED, "img_orphan.png", RED)          # the second copy nothing points at
put(USED, "img_other.png", BLUE)          # a different picture entirely
removed, freed, _rows = sweep(USED)
check("the unreferenced second copy goes",
      "img_orphan.png" not in on_disk(USED), str(on_disk(USED)))
# GUARD 3 — the copy a clip points at is the one that stays, whichever order the
# folder happened to list them in.
check("…and the copy a clip points at stays",
      "img_keepme.png" in on_disk(USED), str(on_disk(USED)))
# GUARD 2 — bytes, not names. A different picture is not a duplicate of anything.
check("…while a picture nothing matches is left alone",
      "img_other.png" in on_disk(USED), str(on_disk(USED)))
check("…and it is counted so the report can say what it freed",
      removed == 1 and freed == len(RED), f"{removed}, {freed}")

# ---------------------------------------------------------------------------
# GUARD 1 · No project record, no sweep
# ---------------------------------------------------------------------------
print("\nA folder whose project cannot be read")
# ⚠ THE ONE THAT COULD EMPTY A FOLDER. `referenced_uploads({})` is legitimately
# empty, so a missing job read as "an empty project" makes every file an orphan.
LOST = os.path.join(config.OUTPUT_DIR, "_animatics", "notajob0001")
os.makedirs(os.path.join(LOST, "media"), exist_ok=True)
for name in ("img_a.png", "img_b.png"):
    with open(os.path.join(LOST, "media", name), "wb") as handle:
        handle.write(RED)
_gone, _bytes, _rows = cleanup_media.sweep(LOST, store, True)
check("a folder with no job behind it is skipped, not emptied",
      sorted(os.listdir(os.path.join(LOST, "media"))) == ["img_a.png", "img_b.png"]
      and _gone == 0, str(os.listdir(os.path.join(LOST, "media"))))

# ---------------------------------------------------------------------------
# GUARD 4 · The last copy always stays
# ---------------------------------------------------------------------------
print("\nA file nothing on the timeline points at")
UNUSED = project({"frames": [], "assets": []})
put(UNUSED, "img_one.png", RED)
put(UNUSED, "img_two.png", RED)
sweep(UNUSED)
check("a duplicate pair nobody references still keeps one file",
      len(on_disk(UNUSED)) == 1, str(on_disk(UNUSED)))
SINGLE = project({"frames": [], "assets": []})
put(SINGLE, "img_alone.png", RED)
sweep(SINGLE)
check("…and a lone unreferenced file is never a duplicate of anything",
      on_disk(SINGLE) == ["img_alone.png"], str(on_disk(SINGLE)))

# ---------------------------------------------------------------------------
# GUARD 3 · Two clips, two ids, one picture — both are in use
# ---------------------------------------------------------------------------
print("\nTwo copies that are BOTH pointed at")
# An old import genuinely produced this: two upload ids for one file, and a clip
# on each. Deleting either one empties a clip, which is worse than the duplicate.
BOTH = project({
    "frames": [{"src": {"upload_id": "one"}}],
    "audio_tracks": [],
    "assets": [{"src": {"upload_id": "two"}}],
})
put(BOTH, "img_one.png", RED)
put(BOTH, "img_two.png", RED)
sweep(BOTH)
check("a duplicate that a clip and a Media-pane asset each hold onto stays whole",
      on_disk(BOTH) == ["img_one.png", "img_two.png"], str(on_disk(BOTH)))

# ⚠ THE SWEEP FOR IDS IS RECURSIVE ON PURPOSE. Upload ids live on frames, on
# assets, on audio tracks, on Veo clips and on overlays, and the set grows with
# the app — a place this did not know about is a file it would delete.
print("\nWhere an upload id can hide")
found = cleanup_media.referenced_uploads({
    "frames": [{"src": {"upload_id": "frame1"}}],
    "audio_tracks": [{"upload_id": "sound1"}],
    "assets": [{"src": {"upload_id": "asset1"}, "upload_id": ""}],
    "veo_clips": [{"nested": [{"deep": {"upload_id": "veo1"}}]}],
})
check("an id is found wherever it sits, however deep",
      found == {"frame1", "sound1", "asset1", "veo1"}, str(sorted(found)))

# ---------------------------------------------------------------------------
# Dry run really is dry
# ---------------------------------------------------------------------------
print("\nThe default, which deletes nothing")
DRY = project({"frames": [{"src": {"upload_id": "kept"}}], "assets": []})
put(DRY, "img_kept.png", RED)
put(DRY, "img_copy.png", RED)
would, _bytes, _rows = sweep(DRY, apply=False)
check("a dry run reports the duplicate…", would == 1, str(would))
check("…and leaves both files exactly where they were",
      on_disk(DRY) == ["img_copy.png", "img_kept.png"], str(on_disk(DRY)))

# ---------------------------------------------------------------------------
# The ledger is pruned to match
# ---------------------------------------------------------------------------
print("\nThe import ledger after a sweep")
import json

LEDGER = project({"frames": [{"src": {"upload_id": "live"}}], "assets": []})
put(LEDGER, "img_live.png", RED)
put(LEDGER, "img_dead.png", RED)
ledger_path = os.path.join(config.OUTPUT_DIR, "_animatics", LEDGER, "media",
                           cleanup_media.LEDGER)
with open(ledger_path, "w", encoding="utf-8") as handle:
    json.dump({"digest-live": {"kind": "image", "upload_id": "live"},
               "digest-dead": {"kind": "image", "upload_id": "dead"}}, handle)
_gone, _bytes, pruned = sweep(LEDGER)
after = json.load(open(ledger_path, encoding="utf-8"))
check("a row naming a file that was just deleted is dropped",
      pruned == 1 and "digest-dead" not in after, str(after))
check("…and the row for the file that stayed is untouched",
      after.get("digest-live", {}).get("upload_id") == "live", str(after))
# ⚠ AND THE LEDGER IS NEVER MEDIA. Sweeping it as if it were would delete the
# project's whole dedupe memory the moment two projects had identical ledgers.
check("the ledger itself is never treated as media",
      os.path.isfile(ledger_path), ledger_path)

shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + ("ALL GREEN" if not failures else f"{len(failures)} FAILED"))
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
