"""
jobs.py — Job persistence.

A JobStore records the lifecycle of each pipeline run so clients can poll
GET /jobs/{id}. Two backends are available (selected by API_JOB_STORE):

    firestore  — persists jobs in Firestore (survives restarts, multi-process)
    memory     — in-process dict (no external deps; lost on restart)

Both share the same interface: create / get / update_status / list.
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

    def list(self, limit: int = 50, owner: str | None = None) -> list[Job]:
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

    def list(self, limit=50, owner=None):
        with self._lock:
            jobs = list(self._jobs.values())
        if owner is not None:
            jobs = [j for j in jobs if j.owner == owner]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

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

    def list(self, limit=50, owner=None):
        from google.cloud import firestore

        query = self._col
        if owner is not None:
            query = query.where("owner", "==", owner)
        query = query.order_by(
            "created_at", direction=firestore.Query.DESCENDING
        ).limit(limit)
        return [Job(**snap.to_dict()) for snap in query.stream()]

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
