"""
cleanup_media.py — sweep up the duplicate media an old import left behind.

Every import used to give each file a fresh `upload_id` and a fresh copy on
disk, so importing the same cut twice doubled a project's media — one real
project on this machine ended up holding **52 files for 27 names**. That leak is
closed (`_store_import_media` in server/animatics.py is content-addressed now),
but closing it does not remove the copies already written. This does.

    python cleanup_media.py                     # every project, DRY RUN
    python cleanup_media.py --job <job_id>      # one project, DRY RUN
    python cleanup_media.py --apply             # actually delete

⚠ **DRY RUN IS THE DEFAULT AND `--apply` IS THE ONLY WAY TO DELETE ANYTHING.**
What this removes is somebody's footage; the report has to be readable before it
is trusted.

WHAT IT DELETES, and nothing else: a media file whose bytes are IDENTICAL to
another file in the same project, and which no clip, no Media-pane asset and no
audio track in that project points at. Four separate guards stand in front of
every deletion:

  1. THE JOB MUST BE READABLE. A media folder whose project cannot be loaded is
     skipped whole — without the project there is no way to know what is in use,
     and "no references found" would then mean "delete everything".
  2. IDENTICAL BYTES, NOT AN IDENTICAL NAME. Two files called `logo.png` can be
     two different pictures; giving a clip the wrong one is worse than the
     duplicate this is removing. The digest is of the file ON DISK here (unlike
     the import ledger, which keys on the bytes that ARRIVED) because that is
     the question being asked: are these two files the same file.
  3. A REFERENCED FILE IS NEVER DELETED, even when it is one of several copies.
     Duplicates get deleted from around it.
  4. THE LAST COPY IS NEVER DELETED. A group nobody references still keeps one
     file, so a picture sitting unused in the Media pane cannot vanish.

The import ledger (`.imported.json`) is pruned to match, so a row naming a file
that is no longer there does not have to be discovered at import time.
"""

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import defaultdict

from server import config
from server.jobs import get_store
from server.schemas import JobKind

# Mirrors `_IMPORT_LEDGER` in server/animatics.py — this prunes it, so it has to
# know its name. It is not media and must never be swept as if it were.
LEDGER = ".imported.json"


def animatic_dirs(root: str, only: str = "") -> list[str]:
    """Every project folder under `output/_animatics`, or just the one asked for."""
    base = os.path.join(root, "_animatics")
    if only:
        found = os.path.join(base, only)
        return [found] if os.path.isdir(found) else []
    if not os.path.isdir(base):
        return []
    return sorted(
        os.path.join(base, name)
        for name in os.listdir(base)
        if os.path.isdir(os.path.join(base, name))
    )


def referenced_uploads(params: dict) -> set:
    """Every `upload_id` this project mentions, wherever it mentions it.

    ⚠ **A RECURSIVE SWEEP, NOT A LIST OF THE PLACES WE REMEMBER.** A project
    holds upload ids on frames, on Media-pane assets, on audio tracks, on Veo
    clips and on overlays, and the set grows with the app — an id this missed
    would be an id whose file this deletes. Walking for the KEY is the one form
    that cannot fall behind the schema.
    """
    found: set = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "upload_id" and isinstance(value, str) and value:
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(params or {})
    return found


def upload_id_of(filename: str) -> str:
    """`img_5078856f55ea.png` → `5078856f55ea`. '' for anything else."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    for prefix in ("img_", "vid_", "audio_"):
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return ""


def digest_of(path: str) -> str:
    """sha256 of the file on disk, read in blocks — these are whole videos."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def prune_ledger(media: str, gone: set, apply: bool) -> int:
    """Drop ledger rows naming an upload that is no longer on disk.

    ⚠ TEMP FILE THEN `os.replace`, the same rule `_remember_import` follows: a
    crash midway through rewriting this in place leaves a truncated JSON file,
    which reads as an empty ledger — every dedupe the project had learned, gone.
    """
    path = os.path.join(media, LEDGER)
    try:
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)
        if not isinstance(rows, dict):
            return 0
    except (OSError, ValueError):
        return 0

    kept = {
        key: row for key, row in rows.items()
        if not (isinstance(row, dict) and row.get("upload_id") in gone)
    }
    dropped = len(rows) - len(kept)
    if dropped and apply:
        tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(kept, handle)
        os.replace(tmp, path)
    return dropped


def sweep(folder: str, store, apply: bool) -> tuple:
    """One project. Returns `(files removed, bytes freed, ledger rows dropped)`."""
    job_id = os.path.basename(folder.rstrip(os.sep))
    media = os.path.join(folder, "media")
    if not os.path.isdir(media):
        return (0, 0, 0)

    # GUARD 1 — no project, no sweep. See the module docstring.
    job = store.get(job_id)
    if job is None or job.kind != JobKind.ANIMATIC:
        print(f"  {job_id}: no animatic record — skipped, nothing touched.")
        return (0, 0, 0)

    in_use = referenced_uploads(job.params or {})
    files = [
        name for name in sorted(os.listdir(media))
        if name != LEDGER and os.path.isfile(os.path.join(media, name))
    ]
    if not files:
        return (0, 0, 0)

    # GUARD 2 — grouped by CONTENT.
    groups: dict = defaultdict(list)
    for name in files:
        path = os.path.join(media, name)
        try:
            groups[digest_of(path)].append(name)
        except OSError:
            print(f"  {job_id}: could not read {name} — left alone.")

    removed, freed, gone = 0, 0, set()
    for names in groups.values():
        if len(names) < 2:
            continue
        # GUARD 3 — everything in use stays. GUARD 4 — and if nothing in this
        # group is in use, the first one stays anyway.
        keep = [n for n in names if upload_id_of(n) in in_use] or names[:1]
        for name in names:
            if name in keep:
                continue
            path = os.path.join(media, name)
            size = os.path.getsize(path)
            print(f"  {job_id}: {'delete' if apply else 'would delete'} {name} "
                  f"({size / 1_048_576:.1f} MB, same bytes as {keep[0]})")
            if apply:
                try:
                    os.remove(path)
                except OSError as exc:
                    print(f"  {job_id}: could not delete {name} — {exc}")
                    continue
            removed += 1
            freed += size
            gone.add(upload_id_of(name))

    return (removed, freed, prune_ledger(media, gone, apply) if gone else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", default="", help="one project id (default: all)")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; without it nothing is touched")
    args = parser.parse_args()

    folders = animatic_dirs(config.OUTPUT_DIR, args.job)
    if not folders:
        print("No animatic media folders found.")
        return 0

    store = get_store()
    print(f"{'Deleting' if args.apply else 'DRY RUN over'} {len(folders)} "
          f"project folder(s) under {config.OUTPUT_DIR}\n")

    removed = freed = pruned = 0
    for folder in folders:
        one, bytes_freed, rows = sweep(folder, store, args.apply)
        removed += one
        freed += bytes_freed
        pruned += rows

    print()
    if not removed:
        print("Nothing to remove — no duplicate media found.")
        return 0
    print(f"{removed} duplicate file(s), {freed / 1_048_576:.1f} MB"
          f"{'' if args.apply else ' — nothing was deleted (dry run)'}.")
    if pruned:
        print(f"{pruned} import-ledger row(s) pruned.")
    if not args.apply:
        print("Re-run with --apply to delete them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
