"""
migrate_jobs_to_mongo.py — move existing jobs into MongoDB.

Copies every job from the local store (`.local_jobs.json`, written by
API_JOB_STORE=memory) into the Mongo `jobs` collection, so switching
API_JOB_STORE=mongo doesn't strand storyboards, character runs or animatics that
already exist.

Safe to run more than once: jobs are keyed by job_id, and an id already present
in Mongo is SKIPPED rather than overwritten — a re-run can't clobber newer work.

    python migrate_jobs_to_mongo.py --dry-run   # report what would move
    python migrate_jobs_to_mongo.py             # do it
    python migrate_jobs_to_mongo.py --overwrite # replace docs that already exist

The source file is never modified, so the old store stays intact as a backup.
"""

import argparse
import json
import os
import sys
from collections import Counter

from server import config
from server.jobs import MongoJobStore
from server.schemas import Job


def load_local(path: str) -> list[dict]:
    if not os.path.isfile(path):
        print(f"No local job file at {path} — nothing to migrate.")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=config.LOCAL_JOBS_PATH, help="source JSON file")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--overwrite", action="store_true", help="replace existing docs")
    args = ap.parse_args()

    records = load_local(args.path)
    if not records:
        return 0

    print(f"Source     : {args.path} ({len(records)} job(s))")
    print(f"Destination: {config.MONGODB_DB}.{config.JOBS_COLLECTION}")
    print(f"By kind    : {dict(Counter(r.get('kind') for r in records))}")
    print()

    # Validate everything BEFORE writing anything: a corrupt record should stop
    # the migration, not leave it half-applied.
    jobs: list[Job] = []
    bad = 0
    for rec in records:
        try:
            jobs.append(Job(**rec))
        except Exception as e:  # noqa: BLE001 — report and keep going
            bad += 1
            print(f"  SKIP unreadable job {rec.get('job_id', '?')}: {e}")
    if bad:
        print(f"  ({bad} record(s) could not be read and will not be migrated)\n")

    store = MongoJobStore(collection=config.JOBS_COLLECTION)
    col = store._col

    existing = {d["_id"] for d in col.find({}, {"_id": 1})}
    to_insert = [j for j in jobs if j.job_id not in existing]
    already = [j for j in jobs if j.job_id in existing]

    print(f"Already in Mongo : {len(already)}")
    print(f"To migrate       : {len(to_insert)}")

    if args.dry_run:
        for j in to_insert:
            print(f"  would insert {j.job_id}  {j.kind.value:<11} {j.character_name!r}")
        print("\nDry run — nothing written.")
        return 0

    inserted = 0
    for j in to_insert:
        col.insert_one(MongoJobStore._to_doc(j))
        inserted += 1

    replaced = 0
    if args.overwrite and already:
        for j in already:
            col.replace_one({"_id": j.job_id}, MongoJobStore._to_doc(j))
            replaced += 1

    print(f"\nInserted : {inserted}")
    if args.overwrite:
        print(f"Replaced : {replaced}")
    print(f"Total now in Mongo: {col.count_documents({})}")
    print(f"\nSource file left untouched at {args.path} (keep it as a backup).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
