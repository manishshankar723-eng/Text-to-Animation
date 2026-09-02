"""THE SAME BYTES ARE STORED ONCE PER PROJECT, NOT ONCE PER IMPORT.

    "Duplicate storage abhi bhi khula hai (upload doosri copy store kar deta
     hai) ... ye fix kro"

⚠ **EVERY IMPORTED FILE USED TO GET A FRESH `upload_id` AND A FRESH COPY ON
DISK.** Two things fell out of that, and the second one is the expensive one:

  1. **WITHIN one import.** The footage pickers add by NAME across several
     folders (E64), so the same file picked from two of them was written twice
     and only one of the two copies was ever referenced — the other sat on disk
     forever, pointed at by nothing.

  2. **ACROSS imports, which is the one that was measured.** Re-reading the same
     cut — which is the ordinary thing to do after attaching a missing folder —
     stored a second copy of all twenty-seven files. A real job on this machine
     ended up holding **52 files for 27 names**, and this is now the third fix in
     a row that has had to work around that pile (E68 taught `resolve` to reuse
     the project's own Media; E69 taught it to fetch from disk; neither stopped
     the copies being made).

The fix is content addressing: `_store_import_media` hashes the bytes that
ARRIVED and keeps a ledger of what it has already stored for that project.

⚠ **HASHED ON THE WAY IN, NOT OFF THE DISK.** An imported picture is re-encoded
to a clean PNG on the way in, so the stored bytes are not the bytes the user
sent — a walk of the media directory could never match a second import of the
same JPEG. The one moment the original bytes exist is the moment they are
stored, which is why the ledger is written there and why it is a file.

⚠ **THIS TEST IMPORTS `server.animatics` FOR REAL**, which G13 is about: every
local store path is pinned into a temp directory BEFORE the import, because
`server/config.py` reads the environment once at import time and its defaults
include a git-tracked file in the repo root.

    python tests/import_dedupe_check.py
"""

import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ BEFORE `server` IS IMPORTED. See the note at the top — and G13, which was
# paid for by a test that spent the developer's own project quota.
_TMP = tempfile.mkdtemp(prefix="import_dedupe_")
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
os.environ["JWT_SECRET"] = "import-dedupe-check-not-a-real-secret"

from PIL import Image

from server import animatics

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


def png(colour) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (12, 12), colour).save(buf, "PNG")
    return buf.getvalue()


def jpeg(colour) -> bytes:
    """⚠ A JPEG ON PURPOSE — it is re-encoded to PNG on the way in, so its stored
    bytes differ from what arrived. Hashing the disk instead of the upload would
    pass every other assertion here and fail this one."""
    buf = io.BytesIO()
    Image.new("RGB", (12, 12), colour).save(buf, "JPEG")
    return buf.getvalue()


JOB = "dedupe000001"
MEDIA = animatics._media_dir(JOB)


def stored_files() -> list:
    """What is actually on disk, ledger excluded."""
    return sorted(f for f in os.listdir(MEDIA) if not f.startswith("."))


os.makedirs(MEDIA, exist_ok=True)

print("The same bytes, twice")
LOGO = png((10, 20, 30))
first = animatics._store_import_media(JOB, "logo.png", LOGO)
again = animatics._store_import_media(JOB, "logo.png", LOGO)
check("the same file imported twice keeps ONE upload id",
      first["upload_id"] == again["upload_id"], f"{first['upload_id']} vs {again['upload_id']}")
check("…and says so, so the report can tell the user",
      again.get("reused") is True and not first.get("reused"), str(again))
check("…and only one copy is on disk",
      len(stored_files()) == 1, str(stored_files()))

print("\nThe same bytes under a different name — E64's two folders")
# ⚠ THIS IS THE WITHIN-ONE-IMPORT LEAK. A file picked from two folders arrives
# twice; only one of the two copies was ever referenced by the timeline.
elsewhere = animatics._store_import_media(JOB, "Shared/logo.png", LOGO)
check("the same bytes under another name reuse the same copy",
      elsewhere["upload_id"] == first["upload_id"], str(elsewhere))
check("…and still nothing new was written",
      len(stored_files()) == 1, str(stored_files()))

print("\nA different file that happens to share a name")
# ⚠ NAME IS NOT IDENTITY. Deduping by filename would silently give this clip the
# WRONG picture — the one failure mode worse than a duplicate.
other = animatics._store_import_media(JOB, "logo.png", png((200, 100, 0)))
check("different bytes under the same name get their own copy",
      other["upload_id"] != first["upload_id"], str(other))
check("…so there are two files now",
      len(stored_files()) == 2, str(stored_files()))

print("\nA re-encoded picture — the reason the ledger is not a walk of the disk")
SHOT = jpeg((7, 90, 140))
j1 = animatics._store_import_media(JOB, "shot.jpg", SHOT)
j2 = animatics._store_import_media(JOB, "shot.jpg", SHOT)
check("a JPEG stored as PNG still dedupes on the bytes that arrived",
      j1["upload_id"] == j2["upload_id"] and j2.get("reused") is True, str(j2))
check("…and one PNG landed for it, not two",
      len(stored_files()) == 3, str(stored_files()))

print("\nSound and video take the same road")
TUNE = b"ID3\x03\x00\x00\x00\x00\x00\x00" + bytes(4096)
a1 = animatics._store_import_media(JOB, "music.mp3", TUNE)
a2 = animatics._store_import_media(JOB, "Other Project/music.mp3", TUNE)
check("audio dedupes too",
      a1["upload_id"] == a2["upload_id"] and a2.get("reused") is True, str(a2))
check("…and its measured length comes back with the reused copy",
      a2["duration_ms"] == a1["duration_ms"], f"{a1['duration_ms']} vs {a2['duration_ms']}")

print("\nThe ledger is not a promise about the disk")
# ⚠ ASKED EVERY TIME. The ledger outlives the files it names: deleting a clip
# from the Media pane can take the file with it, and handing back that id would
# resolve a clip to NOTHING — worse than the duplicate this avoids.
gone = animatics._image_path(JOB, first["upload_id"])
os.remove(gone)
back = animatics._store_import_media(JOB, "logo.png", LOGO)
check("a remembered id whose file has been deleted is stored again",
      back["upload_id"] != first["upload_id"] and not back.get("reused"), str(back))
check("…and the file is really back",
      os.path.exists(animatics._image_path(JOB, back["upload_id"])), "")

print("\nA damaged ledger costs a duplicate, never an import")
with open(os.path.join(MEDIA, animatics._IMPORT_LEDGER), "w", encoding="utf-8") as fh:
    fh.write("{not json at all")
survived = animatics._store_import_media(JOB, "logo.png", LOGO)
check("a corrupt ledger reads as empty rather than raising",
      bool(survived and survived["upload_id"]), str(survived))
check("…and writing repairs it",
      isinstance(animatics._import_ledger(JOB), dict)
      and animatics._import_ledger(JOB) != {}, str(animatics._import_ledger(JOB))[:120])

print("\nAnd the whole point, counted")
# 10 store calls have been made above. Without content addressing every one of
# them writes a file; the ledger is what makes that number the count of DISTINCT
# files instead.
check("ten stores of five distinct files left five files on disk",
      len(stored_files()) == 5, f"{len(stored_files())}: {stored_files()}")

print("\nOne project cannot reuse another's copy")
# ⚠ THE LEDGER IS PER PROJECT, and it has to be: an `upload_id` is only
# resolvable inside the animatic that owns the folder it lives in.
OTHER_JOB = "dedupe000002"
os.makedirs(animatics._media_dir(OTHER_JOB), exist_ok=True)
neighbour = animatics._store_import_media(OTHER_JOB, "logo.png", LOGO)
check("the same bytes in another project are stored there too",
      neighbour["upload_id"] != back["upload_id"] and not neighbour.get("reused"),
      str(neighbour))

shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + ("ALL GREEN" if not failures else f"{len(failures)} FAILED"))
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
