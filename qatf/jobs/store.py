"""Job persistence, accessors and the worker pool.

State only. What the workers actually *do* is in `worker.py` — this file should
stay free of pipeline knowledge so that "how a job is stored" and "what a job
runs" can be read separately.

One thread pool, one JSON file per job on disk. No broker, no database — this is
a prototype and the dependency budget is deliberately small. Consequences, all
deliberate:

  - Jobs do not survive a restart. On startup anything left in a running state is
    marked failed, because there is no worker still holding it.
  - Cancellation is cooperative; see `worker.py`.
  - `max_workers` defaults to 1. Two concurrent Whisper large-v3 loads will
    fight over the same GPU.
"""

from __future__ import annotations

import json
import shutil
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .. import pipeline
from ..core.config import Settings, get_settings
from ..core.types import Transcript
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
        self._jobs: dict[str, Job] = {}
        self._cancels: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="qatf")
        self._recover()

    # -- persistence ------------------------------------------------------

    def _record_path(self, job_id: str) -> Path:
        return self.root / job_id / "job.json"

    def _persist(self, job: Job) -> None:
        path = self._record_path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(job), indent=2, ensure_ascii=False),
                        encoding="utf-8")

    def _recover(self) -> None:
        """Reload jobs from disk. Anything that was running when the process died
        is failed — there is no worker left to finish it."""
        for record in sorted(self.root.glob("*/job.json")):
            try:
                job = Job(**json.loads(record.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, TypeError, OSError):
                continue
            if job.state in RUNNING_STATE_VALUES:
                job.state = JobState.failed.value
                job.error = "interrupted by a server restart"
                job.updated_at = now()
                self._persist(job)
            self._jobs[job.id] = job

    # -- accessors --------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def require(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def update(self, job_id: str, **fields) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = now()
            self._persist(job)
            return job

    def create(self, video: Path, source: str, options: dict) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, video=str(video), source=source, options=options)
        with self._lock:
            self._jobs[job_id] = job
            job.work_dir(self.root).mkdir(parents=True, exist_ok=True)
            job.out_dir(self.root).mkdir(parents=True, exist_ok=True)
            self._persist(job)
        return job

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            self._cancels.discard(job_id)
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
        with self._lock:
            self._cancels.add(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancels

    def clear_cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancels.discard(job_id)

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
