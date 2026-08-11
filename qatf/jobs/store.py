"""Job persistence, accessors and the worker pool.

State only. What the workers actually *do* is in `worker.py` — this file should
stay free of pipeline knowledge so that "how a job is stored" and "what a job
runs" can be read separately.

One thread pool, records and cancel flags in `qatf.db` (SQLite, via
`qatf.core.db`) under the store root. There is no in-memory job dict: `get`,
`require` and `list` all read the database, which is what keeps them correct
when more than one process writes. No broker beyond that — this is a prototype
and the dependency budget is deliberately small. Consequences, all deliberate:

  - Jobs do not survive a restart. On startup anything left in a running state is
    marked failed, because there is no worker still holding it.
  - Cancellation is cooperative; see `worker.py`.
  - `max_workers` defaults to 1. Two concurrent Whisper large-v3 loads will
    fight over the same GPU.

Pre-SQLite `<job_id>/job.json` records are imported once, the first time the
`jobs` table is found empty, and are left on disk afterward — never deleted, so
a bad upgrade stays reversible by checking out the previous commit.
"""

from __future__ import annotations

import json
import shutil
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields
from pathlib import Path

from .. import pipeline
from ..core import db
from ..core.config import Settings, get_settings
from ..core.types import Transcript
from ..core.utils import log
from .model import RUNNING_STATE_VALUES, Job, JobState, now


class Cancelled(Exception):
    """Raised inside a worker when a cancel was requested between stages."""


class JobStore:
    def __init__(self, root: Path, max_workers: int = 1, settings: Settings | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        # Carried so workers read the SAME settings the app was built with.
        # Reaching for the process-wide get_settings() inside a worker makes
        # create_app(settings=...) a half-truth: the HTTP layer honours the
        # injected object while stage 3 quietly uses the environment's.
        self.settings = settings or get_settings()
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="qatf")
        self._recover()

    # -- persistence ------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.root / "qatf.db"

    def _row_to_job(self, row) -> Job:
        """Rebuild a Job from its stored document.

        Unknown keys are dropped rather than raising: a record written by a
        NEWER build must not make an older one refuse to start, which is the
        same tolerance `words_from_dicts` and `track_from_dict` already apply."""
        doc = json.loads(row["doc"])
        known = {f.name for f in fields(Job)}
        return Job(**{k: v for k, v in doc.items() if k in known})

    def _persist(self, job: Job) -> None:
        """Write the whole record in one transaction.

        The predecessor used `path.write_text`, which is not atomic, and
        `_recover` then swallowed the resulting JSONDecodeError with a bare
        `continue` — so a crash mid-write did not corrupt a job, it DELETED
        one, with nothing logged. A transaction cannot half-land."""
        with db.transaction(self.db_path) as con:
            con.execute(
                "INSERT INTO jobs (id, state, created_at, updated_at, doc) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
                "updated_at=excluded.updated_at, doc=excluded.doc",
                (job.id, job.state, job.created_at, job.updated_at,
                 json.dumps(asdict(job), ensure_ascii=False)),
            )

    def _recover(self) -> None:
        """Import any pre-SQLite records, then fail whatever was left running.

        The files are READ, never deleted: a bad upgrade has to be reversible by
        checking out the previous commit, and nothing here is the only copy."""
        con = db.connect(self.db_path)
        empty = con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        if empty:
            for record in sorted(self.root.glob("*/job.json")):
                try:
                    doc = json.loads(record.read_text(encoding="utf-8"))
                    known = {f.name for f in fields(Job)}
                    job = Job(**{k: v for k, v in doc.items() if k in known})
                except (json.JSONDecodeError, TypeError, OSError) as exc:
                    # Reported, not swallowed. The old code's silent `continue`
                    # is exactly the bug this change removes.
                    log(f"could not import {record}: {type(exc).__name__}: {exc}")
                    continue
                self._persist(job)

        for job in self.list():
            if job.state in RUNNING_STATE_VALUES:
                job.state = JobState.failed.value
                job.error = "interrupted by a server restart"
                job.updated_at = now()
                self._persist(job)

    # -- accessors --------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        row = db.connect(self.db_path).execute(
            "SELECT doc FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def require(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def list(self, state: str | None = None) -> list[Job]:
        """Newest first. `state` is pushed into the query so it uses
        ix_jobs_state rather than loading every record to filter in Python."""
        con = db.connect(self.db_path)
        if state:
            rows = con.execute(
                "SELECT doc FROM jobs WHERE state=? ORDER BY created_at DESC",
                (state,))
        else:
            rows = con.execute("SELECT doc FROM jobs ORDER BY created_at DESC")
        return [self._row_to_job(r) for r in rows]

    def update(self, job_id: str, **fields_) -> Job:
        with self._lock:
            job = self.require(job_id)
            for key, value in fields_.items():
                setattr(job, key, value)
            job.updated_at = now()
            self._persist(job)
            return job

    def create(self, video: Path, source: str, options: dict) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, video=str(video), source=source, options=options)
        job.work_dir(self.root).mkdir(parents=True, exist_ok=True)
        job.out_dir(self.root).mkdir(parents=True, exist_ok=True)
        self._persist(job)
        return job

    def delete(self, job_id: str) -> None:
        with db.transaction(self.db_path) as con:
            con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            con.execute("DELETE FROM cancels WHERE job_id=?", (job_id,))
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    def transcript_for(self, job: Job) -> Transcript | None:
        """The cached transcript, or None if this job has not got that far."""
        # fixups are deliberately NOT part of the key — they are applied on read
        # (see worker.caption_words), so editing them must not orphan the cache
        path = pipeline.cache_path(job.work_dir(self.root), job.options["whisper"],
                                   job.options.get("language"),
                                   job.options.get("initial_prompt"),
                                   job.options.get("hotwords"))
        return pipeline.read_cache(path) if path.exists() else None

    # -- cancellation -----------------------------------------------------

    def request_cancel(self, job_id: str) -> None:
        with db.transaction(self.db_path) as con:
            con.execute("INSERT OR IGNORE INTO cancels (job_id) VALUES (?)",
                        (job_id,))

    def cancel_requested(self, job_id: str) -> bool:
        return db.connect(self.db_path).execute(
            "SELECT 1 FROM cancels WHERE job_id=?", (job_id,)).fetchone() is not None

    def clear_cancel(self, job_id: str) -> None:
        with db.transaction(self.db_path) as con:
            con.execute("DELETE FROM cancels WHERE job_id=?", (job_id,))

    def checkpoint(self, job_id: str) -> None:
        """Cooperative cancellation point. Called by workers between stages."""
        if self.cancel_requested(job_id):
            raise Cancelled()

    # -- submission -------------------------------------------------------

    def submit_pipeline(self, job_id: str) -> None:
        from .worker import run_pipeline
        self._enqueue(run_pipeline, job_id)

    def submit_render(self, job_id: str) -> None:
        from .worker import run_render
        self._enqueue(run_render, job_id)

    def _enqueue(self, fn: Callable[[JobStore, str], None], job_id: str) -> None:
        self.clear_cancel(job_id)
        self.update(job_id, state=JobState.queued.value, error=None,
                    message="waiting for a worker")
        self._pool.submit(self._guard, fn, job_id)

    def _guard(self, fn: Callable[[JobStore, str], None], job_id: str) -> None:
        """Every worker failure ends up recorded on the job rather than lost in a
        thread. The traceback still goes to the log for anything unexpected."""
        try:
            fn(self, job_id)
        except Cancelled:
            self.update(job_id, state=JobState.cancelled.value,
                        message="cancelled between stages")
        except Exception as exc:                      # noqa: BLE001 — surfaced to the client
            self.update(job_id, state=JobState.failed.value, message="",
                        error=f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            self.clear_cancel(job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
