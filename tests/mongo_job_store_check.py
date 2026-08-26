"""Contract checks for the MongoDB job store — the system of record.

Runs the SAME assertions against MongoJobStore and MemoryJobStore, so the two
backends are proven to behave alike rather than the Mongo one merely "looking
right". Every job it creates is removed afterwards.

Covers what the app actually relies on: owner scoping, per-workflow (kind)
filtering, `where` (a condition applied BEFORE the limit), `drop` (fields the
caller does not read and must not be sent), share-token lookup, partial updates
under concurrent writers, and that asset URLs (including GCS URLs) survive a
round trip.

    python tests/mongo_job_store_check.py
"""

import os
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from server import config
from server.jobs import MemoryJobStore, MongoJobStore
from server.schemas import JobKind, JobStatus

failures: list[str] = []
created: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"    {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


def run_contract(name: str, store, track=False):
    print(f"\n=== {name} ===")
    owner_a = f"a_{uuid.uuid4().hex[:8]}@t.dev"
    owner_b = f"b_{uuid.uuid4().hex[:8]}@t.dev"

    print("  create / get")
    job = store.create("Test Character", kind=JobKind.GENERATE, owner=owner_a,
                       params={"style": "sketch"})
    if track:
        created.append(job.job_id)
    check("create returns QUEUED", job.status, JobStatus.QUEUED)
    got = store.get(job.job_id)
    check("get round-trips the job", got.job_id, job.job_id)
    check("params survive", got.params.get("style"), "sketch")
    check("owner survives", got.owner, owner_a)
    check("missing id returns None", store.get("does-not-exist"), None)

    print("  update")
    up = store.update(job.job_id, status=JobStatus.RUNNING, progress={"percent": 40})
    check("status updated", up.status, JobStatus.RUNNING)
    check("progress updated", up.progress["percent"], 40)
    check("updated_at moved", up.updated_at != job.updated_at, True)
    check("update of missing id -> None", store.update("nope", status=JobStatus.FAILED), None)

    print("  asset URLs round-trip (this is where GCS URLs land)")
    gcs = {
        "urls": {
            "body_front": "https://storage.googleapis.com/comfyui-assets-cf56be07/characters/x/body_front.png",
            "body_back": "https://storage.googleapis.com/comfyui-assets-cf56be07/characters/x/body_back.png",
        },
        "zip_url": "https://storage.googleapis.com/comfyui-assets-cf56be07/characters/x/assets.zip",
    }
    done = store.mark_succeeded(job.job_id, gcs)
    check("status SUCCEEDED", done.status, JobStatus.SUCCEEDED)
    check("GCS urls stored", store.get(job.job_id).result["urls"]["body_front"], gcs["urls"]["body_front"])
    check("zip url stored", store.get(job.job_id).result["zip_url"], gcs["zip_url"])

    print("  partial update must not clobber a concurrent writer")
    # The worker writes progress while a request writes result. A whole-document
    # read-modify-write would let one erase the other.
    store.update(job.job_id, progress={"percent": 90})
    after = store.get(job.job_id)
    check("progress advanced", after.progress["percent"], 90)
    check("result NOT lost by the progress write", after.result["zip_url"], gcs["zip_url"])

    print("  list: owner scoping + kind filtering")
    sb = store.create("A board", kind=JobKind.STORYBOARD, owner=owner_a)
    an = store.create("An animatic", kind=JobKind.ANIMATIC, owner=owner_a)
    other = store.create("Not yours", kind=JobKind.STORYBOARD, owner=owner_b)
    if track:
        created.extend([sb.job_id, an.job_id, other.job_id])

    mine = store.list(limit=50, owner=owner_a)
    check("only my jobs", all(j.owner == owner_a for j in mine), True)
    check("I see all 3 of mine", len(mine), 3)
    boards = store.list(limit=50, owner=owner_a, kinds=[JobKind.STORYBOARD])
    check("kind filter returns only storyboards", [j.kind for j in boards], [JobKind.STORYBOARD])
    check("other owner's board excluded", all(j.job_id != other.job_id for j in boards), True)
    multi = store.list(limit=50, owner=owner_a, kinds=[JobKind.STORYBOARD, JobKind.ANIMATIC])
    check("multi-kind filter", len(multi), 2)
    check("newest first", multi[0].created_at >= multi[-1].created_at, True)
    check("limit is applied", len(store.list(limit=1, owner=owner_a)), 1)

    # ---------------------------------------------------------------- where
    # THE ORDERING IS THE POINT. `limit` is applied by the store, so a caller
    # that filters afterwards is filtering an already-truncated page - ask for
    # the newest 3 boards of an owner whose newest 3 are all tagged copies and
    # the untagged list comes back EMPTY over a library full of work. That is
    # what `where` exists to prevent, and it has to behave the same on both
    # backends or the bug simply moves to whichever one is in production.
    print("  where: the condition is applied BEFORE the limit")
    owner_c = f"where-{uuid.uuid4().hex[:8]}@test.local"
    plain = store.create("untagged", kind=JobKind.STORYBOARD, owner=owner_c)
    tagged = [
        store.create(f"copy {i}", kind=JobKind.STORYBOARD, owner=owner_c,
                     params={"workflow": "animatic-image"})
        for i in range(3)
    ]
    drafted = store.create("a draft", kind=JobKind.STORYBOARD, owner=owner_c)
    store.update(drafted.job_id, status=JobStatus.DRAFT)
    if track:
        created.append(plain.job_id)
        created.extend(j.job_id for j in tagged)
        created.append(drafted.job_id)

    # The three copies are all NEWER than the untagged board, so a limit of 3
    # applied first would hide it completely.
    untagged = store.list(
        limit=3, owner=owner_c, kinds=[JobKind.STORYBOARD],
        where={"params.workflow": {"$in": [None, ""]}},
    )
    # ⚠ THE DRAFT IS IN HERE TOO, and correctly so - it has no workflow key
    # either. Filtering drafts is a SEPARATE condition (see "two conditions
    # combine" below); asserting one job here would be asserting the wrong
    # thing, and did until this test was run.
    check("$in [None, ''] survives a limit filled by newer tagged copies",
          plain.job_id in [j.job_id for j in untagged], True)
    check("and no tagged copy came back with it",
          any(j.job_id in [t.job_id for t in tagged] for j in untagged), False)
    copies = store.list(
        limit=50, owner=owner_c, kinds=[JobKind.STORYBOARD],
        where={"params.workflow": "animatic-image"},
    )
    check("equality finds only the tagged copies", len(copies), 3)
    live = store.list(
        limit=50, owner=owner_c, kinds=[JobKind.STORYBOARD],
        where={"status": {"$ne": JobStatus.DRAFT.value}},
    )
    check("$ne excludes drafts", all(j.status != JobStatus.DRAFT for j in live), True)
    check("$ne keeps everything else", len(live), 4)
    combined = store.list(
        limit=50, owner=owner_c, kinds=[JobKind.STORYBOARD],
        where={"status": {"$ne": JobStatus.DRAFT.value},
               "params.workflow": {"$in": [None, ""]}},
    )
    check("two conditions combine", [j.job_id for j in combined], [plain.job_id])
    check("a where cannot widen the owner scope",
          all(j.owner == owner_c for j in
              store.list(limit=50, owner=owner_c, where={"owner": owner_a})), True)

    # ----------------------------------------------------------------- drop
    print("  drop: unread fields are left out of the answer, not the store")
    heavy = store.create("heavy", kind=JobKind.STORYBOARD, owner=owner_c,
                         params={"blob": "x" * 4000, "count": 7})
    if track:
        created.append(heavy.job_id)
    lean = store.list(limit=50, owner=owner_c, kinds=[JobKind.STORYBOARD],
                      drop=("params",))
    check("params is empty in the answer", all(j.params == {} for j in lean), True)
    check("the rest of the record is intact",
          all(j.character_name and j.created_at for j in lean), True)
    # ⚠ THE ONE THAT MATTERS: dropping is about the RESPONSE. A backend that
    # handed back its own stored object and let the caller blank it would
    # destroy the project.
    check("THE STORE STILL HAS params",
          (store.get(heavy.job_id).params or {}).get("count"), 7)
    check("and still has the big field",
          len((store.get(heavy.job_id).params or {}).get("blob", "")), 4000)

    print("  share token lookup (public links)")
    token = uuid.uuid4().hex
    store.update(sb.job_id, params={**sb.params, "share_token": token})
    found = store.find_by_share_token(token)
    check("token resolves to the right job", found.job_id if found else None, sb.job_id)
    check("unknown token -> None", store.find_by_share_token("no-such-token"), None)
    check("empty token -> None", store.find_by_share_token(""), None)

    print("  concurrent progress writes all land")
    hot = store.create("Concurrency", kind=JobKind.STORYBOARD, owner=owner_a)
    if track:
        created.append(hot.job_id)
    errors = []

    def bump(i):
        try:
            store.update(hot.job_id, progress={"percent": i})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=bump, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("no errors under concurrent updates", errors, [])
    check("job still readable and valid", store.get(hot.job_id) is not None, True)

    print("  delete")
    check("delete returns True", store.delete(an.job_id), True)
    check("deleted job is gone", store.get(an.job_id), None)
    check("deleting again returns False", store.delete(an.job_id), False)
    if track:
        created.remove(an.job_id)


print(f"config: JOB_STORE={config.JOB_STORE}  collection={config.JOBS_COLLECTION}")

mongo_store = MongoJobStore(collection=config.JOBS_COLLECTION)
before = mongo_store._col.count_documents({})
print(f"existing jobs in Mongo before tests: {before}")

run_contract("MemoryJobStore (reference behaviour)", MemoryJobStore(persist_path=None))
run_contract("MongoJobStore", mongo_store, track=True)

print("\n=== cleanup ===")
removed = 0
for jid in created:
    removed += 1 if mongo_store.delete(jid) else 0
print(f"    removed {removed} test job(s)")
after = mongo_store._col.count_documents({})
check("pre-existing jobs untouched", after, before)

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All job-store checks passed — Mongo behaves identically to the reference store.")
