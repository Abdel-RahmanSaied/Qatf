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
from ..core.config import EDITABLE, Settings, effective_settings, get_settings
from ..core.types import Transcript
from ..core.utils import log
from .model import RUNNING_STATE_VALUES, Job, JobState, now


def _settings_as_env(s: Settings) -> dict[str, str]:
    """The injected `Settings` expressed as the env vars that would produce it.

    `effective_settings` layers overrides onto `Settings.from_env`, but this
    store may hold an INJECTED object that never came from the environment —
    that is the whole point of `create_app(settings=...)`. Without translating
    it back first, a settings lookup would layer onto whatever the process
    environment happens to hold and `create_app(settings=...)` would be a
    half-truth exactly where it matters, which is the failure the comment in
    `JobStore.__init__` already warns about."""
    return {
        "QATF_DATA_DIR": str(s.data_dir),
        "QATF_MEDIA_ROOT": str(s.media_root),
        "QATF_WORKERS": str(s.workers),
        "QATF_MAX_UPLOAD_MB": str(s.max_upload_bytes // 1024 // 1024),
        "QATF_LLM_PROVIDER": s.llm_provider,
        "QATF_LLM_MODEL": s.llm_model or "",
        "QATF_LLM_BASE_URL": s.llm_base_url or "",
        "QATF_LLM_EFFORT": s.llm_effort or "",
        "QATF_LLM_MAX_TOKENS": str(s.llm_max_tokens),
        "QATF_LLM_TIMEOUT": str(int(s.llm_timeout)),
        "QATF_HOST": s.host,
        "QATF_PORT": str(s.port),
    }


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

    # -- server settings --------------------------------------------------

    def settings_overrides(self) -> dict[str, object]:
        """Every saved override, JSON-decoded.

        Non-editable keys are dropped HERE as well as on write. The table is a
        file someone can edit, so the allowlist has to hold on the way out too;
        a row naming `media_root` must be inert, not effective."""
        rows = db.connect(self.db_path).execute(
            "SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"])
                for r in rows if r["key"] in EDITABLE}

    def save_setting(self, key: str, value: object) -> None:
        """Store one override. Silently ignores a non-editable key — the router
        refuses those with a 422 before reaching here, and a store that could
        write one would make the allowlist advisory."""
        if key not in EDITABLE:
            return
        with db.transaction(self.db_path) as con:
            con.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value), now()))

    def clear_setting(self, key: str) -> None:
        """Drop an override so the environment takes over again.

        Deleting the row is not the same as saving `""`: an absent row means
        "not overridden", an empty string means "explicitly blank, use the
        preset default"."""
        with db.transaction(self.db_path) as con:
            con.execute("DELETE FROM settings WHERE key = ?", (key,))

    def settings_for_job(self) -> Settings:
        """The settings a job starting NOW should use.

        Computed per call, never held on the store. That is the entire reason a
        save cannot reach a run already in flight: the job captured its own
        frozen snapshot at start, so there is no window to race. The job record
        reports the provider and model used, and a mid-run change that could
        alter them would make the record describe a run that did not happen."""
        return effective_settings(self.settings_overrides(),
                                  _settings_as_env(self.settings))

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

    def create(self, video: Path, source: str, options: dict, url: str = "") -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, video=str(video), source=source, options=options,
                  url=url)
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
        """The cached transcript, or None if this job has not got that far.

        Prefers the key the job RECORDED over one derived from its options —
        see `Job.transcript_key`. A job whose captions were unusable fell back
        to Whisper at runtime, and no reading of `options` can tell you that
        happened."""
        # fixups are deliberately NOT part of the key — they are applied on read
        # (see worker.caption_words), so editing them must not orphan the cache
        key = job.transcript_key or pipeline.cache_key(
            job.options["whisper"],
            job.options.get("language"),
            job.options.get("initial_prompt"),
            job.options.get("hotwords"))
        return pipeline.read_cache(job.work_dir(self.root), key)

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
