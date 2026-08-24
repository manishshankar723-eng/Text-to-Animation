"""
jobs.py — Job persistence.

A JobStore records the lifecycle of each pipeline run so clients can poll
GET /jobs/{id}. Backends are selected by API_JOB_STORE:

    mongo      — MongoDB (DEFAULT). The system of record for everything the app
                 produces except the binary files themselves.
    firestore  — persists jobs in Firestore (legacy; kept for existing deploys)
    memory     — in-process dict, optionally mirrored to a JSON file. Dev only.

All share the same interface: create / get / update / list / delete /
find_by_share_token.

ARCHITECTURE RULE — every workflow's metadata belongs in Mongo. Character runs,
storyboards, animatics, and whatever gets added to the sidebar next all create
their records through this one interface, so a new workflow is persisted simply
by calling `get_store().create(...)` with a new JobKind. Do NOT invent a
per-workflow storage path. The only things that do NOT go in Mongo are the image
and video BYTES; those live on disk (or GCS), and their URLs are stored in the
job's `result`.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

from . import config
from .schemas import Job, JobKind, JobStatus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_job_id() -> str:
    return uuid.uuid4().hex


class JobStore:
    """Abstract job store interface."""

    def create(
        self,
        character_name: str,
        kind: JobKind = JobKind.GENERATE,
        template: str | None = None,
        params: dict | None = None,
        owner: str | None = None,
    ) -> Job:
        raise NotImplementedError

    def get(self, job_id: str) -> Job | None:
        raise NotImplementedError

    def update(self, job_id: str, **fields) -> Job | None:
        raise NotImplementedError

    def list(
        self,
        limit: int = 50,
        owner: str | None = None,
        kinds: "list[JobKind] | tuple[JobKind, ...] | None" = None,
    ) -> list[Job]:
        """Newest-first jobs, optionally restricted to an owner and to `kinds`.

        `kinds` keeps the two workflows apart: the Text-to-Image job list must
        not show storyboards, and the storyboard library must not show character
        runs. Filtering here (rather than in the caller) means the `limit` is
        applied AFTER the filter, so a long run of one kind can't push the other
        kind off the end of the list.
        """
        raise NotImplementedError

    def count_by_kind(self, owner: str | None = None) -> dict[str, int]:
        """`{kind: how many}`, for the whole store or for one owner.

        Exists for the admin panel, which asks "how much has this account
        actually made" on every row of a user table and "what is this app being
        used for" on the dashboard. Both were previously answerable only by
        listing jobs and measuring the list, which caps out at the page size and
        so quietly under-reports anyone busy.

        Kinds with no jobs are omitted; the caller fills in the zeroes.
        """
        raise NotImplementedError

    def delete(self, job_id: str) -> bool:
        """Remove a job record. Returns True if something was deleted."""
        raise NotImplementedError

    def find_by_share_token(self, token: str) -> Job | None:
        """Look up a job by the share token stored in params (public links)."""
        raise NotImplementedError

    # --- Convenience helpers shared by all backends ------------------------
    def mark_running(self, job_id: str) -> Job | None:
        return self.update(job_id, status=JobStatus.RUNNING)

    def mark_succeeded(self, job_id: str, result: dict) -> Job | None:
        return self.update(job_id, status=JobStatus.SUCCEEDED, result=result, error=None)

    def mark_failed(self, job_id: str, error: str) -> Job | None:
        return self.update(job_id, status=JobStatus.FAILED, error=error)


class MemoryJobStore(JobStore):
    """Thread-safe in-process job store (dev / no-Firestore mode).

    Optionally mirrors every job to a JSON file (`persist_path`) so a backend
    restart doesn't lose saved storyboards. The file is the convenience of
    Firestore without the setup; it is NOT meant for production/multi-process.
    """

    def __init__(self, persist_path: str | None = None):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path or None
        self._load()

    # --- file persistence -------------------------------------------------
    def _load(self) -> None:
        if not self._persist_path or not os.path.isfile(self._persist_path):
            return
        try:
            with open(self._persist_path, encoding="utf-8") as f:
                raw = json.load(f)
            for rec in raw:
                job = Job(**rec)
                self._jobs[job.job_id] = job
            logger.info(
                "Loaded %d job(s) from %s", len(self._jobs), self._persist_path
            )
        except Exception as e:  # noqa: BLE001 — a corrupt file must not crash boot
            logger.warning("Could not load jobs from %s (%s).", self._persist_path, e)

    def _save_locked(self) -> None:
        """Write the whole store to disk. Caller must hold self._lock."""
        if not self._persist_path:
            return
        try:
            tmp = f"{self._persist_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([j.model_dump(mode="json") for j in self._jobs.values()], f)
            os.replace(tmp, self._persist_path)  # atomic — never a half-written file
        except OSError as e:
            logger.warning("Could not persist jobs to %s (%s).", self._persist_path, e)

    def create(self, character_name, kind=JobKind.GENERATE, template=None, params=None, owner=None):
        now = _now_iso()
        job = Job(
            job_id=_new_job_id(),
            kind=kind,
            status=JobStatus.QUEUED,
            owner=owner,
            character_name=character_name,
            template=template,
            params=params or {},
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._save_locked()
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            data = job.model_dump()
            data.update(fields)
            data["updated_at"] = _now_iso()
            job = Job(**data)
            self._jobs[job_id] = job
            self._save_locked()
            return job

    def list(self, limit=50, owner=None, kinds=None):
        with self._lock:
            jobs = list(self._jobs.values())
        if owner is not None:
            jobs = [j for j in jobs if j.owner == owner]
        if kinds:
            wanted = set(kinds)
            jobs = [j for j in jobs if j.kind in wanted]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def count_by_kind(self, owner=None):
        with self._lock:
            jobs = list(self._jobs.values())
        counts: dict[str, int] = {}
        for job in jobs:
            if owner is not None and job.owner != owner:
                continue
            key = job.kind.value if hasattr(job.kind, "value") else str(job.kind)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def delete(self, job_id):
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
            if existed:
                self._save_locked()
            return existed

    def find_by_share_token(self, token):
        if not token:
            return None
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if (job.params or {}).get("share_token") == token:
                return job
        return None


class FirestoreJobStore(JobStore):
    """Persists jobs as documents in a Firestore collection."""

    def __init__(self, collection: str, project: str | None = None):
        # Imported lazily so `memory` mode needs no Firestore dependency at runtime.
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)
        logger.info("FirestoreJobStore ready (collection=%s)", collection)

    def _doc(self, job_id: str):
        return self._col.document(job_id)

    def create(self, character_name, kind=JobKind.GENERATE, template=None, params=None, owner=None):
        now = _now_iso()
        job = Job(
            job_id=_new_job_id(),
            kind=kind,
            status=JobStatus.QUEUED,
            owner=owner,
            character_name=character_name,
            template=template,
            params=params or {},
            created_at=now,
            updated_at=now,
        )
        self._doc(job.job_id).set(job.model_dump(mode="json"))
        return job

    def get(self, job_id):
        snap = self._doc(job_id).get()
        if not snap.exists:
            return None
        return Job(**snap.to_dict())

    def update(self, job_id, **fields):
        doc = self._doc(job_id)
        snap = doc.get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        data.update(fields)
        data["updated_at"] = _now_iso()
        job = Job(**data)
        doc.set(job.model_dump(mode="json"))
        return job

    def list(self, limit=50, owner=None, kinds=None):
        from google.cloud import firestore

        query = self._col
        if owner is not None:
            query = query.where("owner", "==", owner)
        # `kinds` is filtered in Python, NOT with a `where("kind", "in", …)`:
        # adding a second equality filter alongside the owner filter and the
        # created_at ordering needs another composite index, which would fail at
        # runtime on any deployment that hasn't created it. Instead we over-fetch
        # and trim, so the caller still gets a full page of the kind it asked for.
        fetch = min(limit * 4, 500) if kinds else limit
        query = query.order_by(
            "created_at", direction=firestore.Query.DESCENDING
        ).limit(fetch)
        jobs = [Job(**snap.to_dict()) for snap in query.stream()]
        if kinds:
            wanted = set(kinds)
            jobs = [j for j in jobs if j.kind in wanted]
        return jobs[:limit]

    def count_by_kind(self, owner=None):
        """⚠ THIS ONE STREAMS, because Firestore has no group-by.

        Only the `kind` field is selected, so it is one small read per job
        rather than a full document — but it IS a read per job, and this backend
        is legacy (see config.JOB_STORE). Mongo, the default, does this in the
        database. If a Firestore deployment ever grows large enough for this to
        hurt, that is the signal to migrate it rather than to cap the number and
        report a wrong one.
        """
        query = self._col
        if owner is not None:
            query = query.where("owner", "==", owner)
        counts: dict[str, int] = {}
        for snap in query.select(["kind"]).stream():
            key = (snap.to_dict() or {}).get("kind") or JobKind.GENERATE.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def delete(self, job_id):
        doc = self._doc(job_id)
        if not doc.get().exists:
            return False
        doc.delete()
        return True

    def find_by_share_token(self, token):
        if not token:
            return None
        snaps = list(self._col.where("params.share_token", "==", token).limit(1).stream())
        return Job(**snaps[0].to_dict()) if snaps else None


class MongoJobStore(JobStore):
    """Persists jobs as documents in a MongoDB collection.

    This is the system of record for EVERYTHING the app produces except the
    binary files themselves: character runs, storyboards, animatics, and any
    workflow added later. All of them already funnel through JobStore, so a new
    workflow is persisted here the moment it calls `create()` — there is no
    per-workflow storage code to remember to write.

    What lives in a job document:
      - `params`  — the inputs (script text, style, aspect, cast/asset refs, …)
      - `result`  — the outputs, INCLUDING asset URLs. When GCS is switched on,
                    `storage.save_character_assets` returns public GCS URLs and
                    they are written straight into `result`, so the URLs are
                    persisted here with no extra plumbing.
      - `progress`, `status`, `error` — the live lifecycle.
    The image/video BYTES stay in object storage (disk today, GCS when enabled);
    Mongo holds the record that points at them.

    `_id` is the job_id, so lookups hit the primary key and a duplicate job id
    is impossible by construction.
    """

    def __init__(self, collection: str):
        from .mongo import get_db

        self._col = get_db()[collection]
        self._ensure_indexes()
        logger.info("MongoJobStore ready (collection=%s)", collection)

    def _ensure_indexes(self) -> None:
        """Indexes for the three ways jobs are actually read.

        Created on startup and idempotent — Mongo ignores a create_index for an
        index that already exists.
        """
        try:
            # The library screens: newest-first, filtered by owner and kind.
            self._col.create_index(
                [("owner", 1), ("kind", 1), ("created_at", -1)], name="owner_kind_created"
            )
            # Public share links resolve a token to a job.
            self._col.create_index(
                "params.share_token", name="share_token", sparse=True
            )
        except Exception as e:  # noqa: BLE001 — indexes are an optimisation
            logger.warning("Could not create job indexes (%s). Queries still work.", e)

    @staticmethod
    def _to_doc(job: Job) -> dict:
        """Job → Mongo document. `_id` is the job id."""
        doc = job.model_dump(mode="json")
        doc["_id"] = job.job_id
        return doc

    @staticmethod
    def _to_job(doc: dict | None) -> Job | None:
        if not doc:
            return None
        doc = dict(doc)
        doc.pop("_id", None)  # `job_id` carries it; _id is storage detail
        return Job(**doc)

    def create(self, character_name, kind=JobKind.GENERATE, template=None, params=None, owner=None):
        now = _now_iso()
        job = Job(
            job_id=_new_job_id(),
            kind=kind,
            status=JobStatus.QUEUED,
            owner=owner,
            character_name=character_name,
            template=template,
            params=params or {},
            created_at=now,
            updated_at=now,
        )
        self._col.insert_one(self._to_doc(job))
        return job

    def get(self, job_id):
        return self._to_job(self._col.find_one({"_id": job_id}))

    def update(self, job_id, **fields):
        """Merge `fields` into the job.

        Validates the merged record through `Job` (same guarantee the other
        backends give) but then writes ONLY the changed keys with `$set`. That
        matters because the worker writes `progress` continuously while a
        request may be writing `result` or `status`: a read-modify-write of the
        whole document would let one silently overwrite the other.
        """
        current = self._col.find_one({"_id": job_id})
        if current is None:
            return None

        merged = dict(current)
        merged.pop("_id", None)
        merged.update(fields)
        merged["updated_at"] = _now_iso()
        job = Job(**merged)  # validate, and normalise enums/None

        # Re-read the validated values so enums and nested models are stored as
        # plain JSON rather than Python objects pymongo can't encode.
        full = job.model_dump(mode="json")
        changed = {k: full[k] for k in fields if k in full}
        changed["updated_at"] = full["updated_at"]
        self._col.update_one({"_id": job_id}, {"$set": changed})
        return job

    def list(self, limit=50, owner=None, kinds=None):
        query: dict = {}
        if owner is not None:
            query["owner"] = owner
        # Unlike Firestore, Mongo needs no composite index permission slip to
        # combine these, so the kind filter runs in the QUERY and `limit` is
        # applied after it — no over-fetch-and-trim needed.
        if kinds:
            query["kind"] = {"$in": [JobKind(k).value for k in kinds]}
        cursor = self._col.find(query).sort("created_at", -1).limit(limit)
        return [self._to_job(d) for d in cursor]

    def count_by_kind(self, owner=None):
        match: dict = {} if owner is None else {"owner": owner}
        pipeline = [{"$match": match}, {"$group": {"_id": "$kind", "n": {"$sum": 1}}}]
        return {
            (row.get("_id") or JobKind.GENERATE.value): row.get("n", 0)
            for row in self._col.aggregate(pipeline)
        }

    def delete(self, job_id):
        return self._col.delete_one({"_id": job_id}).deleted_count > 0

    def find_by_share_token(self, token):
        if not token:
            return None
        return self._to_job(self._col.find_one({"params.share_token": token}))


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_store: JobStore | None = None


def get_store() -> JobStore:
    """Return the configured job store, creating it on first use.

    Falls back to an in-memory store if Firestore was requested but cannot be
    initialised (e.g. no credentials in a local dev environment).
    """
    global _store
    if _store is not None:
        return _store

    if config.JOB_STORE == "memory":
        path = getattr(config, "LOCAL_JOBS_PATH", "") or None
        logger.info(
            "Using in-memory job store (API_JOB_STORE=memory)%s.",
            f", persisting to {path}" if path else "",
        )
        _store = MemoryJobStore(persist_path=path)
        return _store

    if config.JOB_STORE == "mongo":
        try:
            _store = MongoJobStore(collection=config.JOBS_COLLECTION)
            return _store
        except Exception as e:  # noqa: BLE001 — never leave the API unable to boot
            # Deliberately LOUD: falling back means work is being written
            # somewhere it will not be looked for later. Better a screaming log
            # than a user quietly saving boards into a file nobody reads.
            logger.error(
                "MongoDB job store unavailable (%s). Falling back to the local "
                "store — JOBS WILL NOT BE IN MONGO until this is fixed.", e,
            )
            _store = MemoryJobStore(persist_path=getattr(config, "LOCAL_JOBS_PATH", "") or None)
            return _store

    try:
        _store = FirestoreJobStore(
            collection=config.FIRESTORE_COLLECTION,
            project=config.GOOGLE_CLOUD_PROJECT,
        )
    except Exception as e:  # noqa: BLE001 — degrade gracefully for local dev
        logger.warning(
            "Firestore unavailable (%s). Falling back to in-memory job store. "
            "Set API_JOB_STORE=memory to silence this.",
            e,
        )
        _store = MemoryJobStore(persist_path=getattr(config, "LOCAL_JOBS_PATH", "") or None)
    return _store
