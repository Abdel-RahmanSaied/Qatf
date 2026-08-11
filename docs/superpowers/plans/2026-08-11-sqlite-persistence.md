# SQLite Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace file-based persistence with SQLite for both front ends, fixing a torn write that silently deletes a job, a lock that only protects one process, and an unindexed `?state=` scan.

**Architecture:** `qatf/core/db.py` is the only module importing `sqlite3`. `core` may import stdlib, so this keeps `api → jobs → pipeline → llm → core` intact and lets the CLI and the API share one code path with two database locations. The job record moves first, then the three `pipeline`-owned caches. Existing files are imported, never deleted.

**Tech Stack:** Python 3.12+, stdlib `sqlite3` in WAL mode. No new dependencies.

## Global Constraints

- **No new third-party dependencies.** `sqlite3` is stdlib; that is the whole point.
- **`sqlite3` may be imported ONLY in `qatf/core/db.py`.** Anywhere else breaks the layering the design exists to preserve.
- **Layering:** `api → jobs → pipeline → llm → core`. `core` imports nothing of ours.
- **Tests are NOT pytest.** `tests/_harness.py` exports `section`, `check`, `raises`, `report`. Run as `PYTHONIOENCODING=utf-8 python tests/<name>.py` — Arabic output raises `UnicodeEncodeError` otherwise.
- **Lint:** `python -m ruff check .` must be clean. Plain `ruff` is not on PATH.
- **Windows host.** Never hardcode `/tmp`; use `tempfile.gettempdir()`.
- **Baselines that must stay green:** smoke_pipeline 273, smoke_llm 38, smoke_api 127, load_api 23, verify_render 10.
- **The `transcripts` row holds RAW Whisper output.** Fixups, repetition repair and per-word edits are applied on READ and never written back. This keeps every number in `docs/quality.md` reproducible.
- **`--plan FILE` stays file-based** — export/import around the DB.
- **MP4s stay on the filesystem.**
- **Job claiming is OUT OF SCOPE.** WAL makes concurrent writes safe; it does not make `QATF_WORKERS` safe across processes.
- **GIT RULE:** no assistant or AI attribution in any commit message. Plain conventional commits.
- **Nothing in the upgrade path deletes a file.**

---

## File Structure

| File | Responsibility |
| --- | --- |
| `qatf/core/db.py` (new) | The only `sqlite3` importer: connect, WAL pragmas, thread-local handles, schema migration |
| `qatf/jobs/store.py` (modify) | Job record + cancels via `core.db`; `_jobs` removed |
| `qatf/pipeline/asr.py` (modify) | Transcript cache keyed in SQLite, lazy file import |
| `qatf/pipeline/edits.py` (modify) | Per-word overlay in SQLite |
| `qatf/pipeline/detect.py` (modify) | Face cache in SQLite |
| `tests/score_transcript.py` (modify) | Accept a path or a DB key |
| `tests/smoke_db.py` (new) | Checks for `core/db.py` in isolation |

---

### Task 1: `core/db.py`

**Files:**
- Create: `qatf/core/db.py`
- Create: `tests/smoke_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SCHEMA_VERSION: int`, `connect(path: Path) -> sqlite3.Connection`, `close_all() -> None`, `transaction(path: Path)` (context manager yielding a connection inside `BEGIN IMMEDIATE`)

- [ ] **Step 1: Write the failing checks**

Create `tests/smoke_db.py`:

```python
"""Checks for the SQLite layer, in isolation from jobs and pipeline.

Its own suite because `core/db.py` is the one module allowed to import sqlite3,
and the properties that matter here — WAL, thread affinity, migration
idempotence — are invisible from the suites that use it.

    PYTHONIOENCODING=utf-8 python tests/smoke_db.py
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path

from _harness import check, report, section

from qatf.core import db

TMP = Path(tempfile.mkdtemp(prefix="qatf-db-"))

section("core/db — connection and pragmas")
p = TMP / "a.db"
con = db.connect(p)
check("the file is created", p.exists())
check("WAL is on — many readers, one writer, readers never block",
      con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal")
check("busy_timeout is set so a competing writer retries rather than raising",
      con.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000)
check("the schema version is stamped",
      con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION)

section("core/db — migration is idempotent")
before = con.execute(
    "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
db.connect(p)
after = con.execute(
    "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
check("connecting twice does not duplicate or drop tables", before == after,
      f"{before} -> {after}")
check("every table the design names exists",
      {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
      >= {"jobs", "cancels", "transcripts", "word_edits", "detections"})

section("core/db — thread affinity")
# sqlite3 connection objects are NOT safe to share across threads, and the job
# store is a thread pool. Each thread must get its own handle.
seen: dict[str, int] = {}


def grab(name: str) -> None:
    seen[name] = id(db.connect(p))


t = threading.Thread(target=grab, args=("worker",))
t.start()
t.join()
grab("main")
check("each thread gets its own connection object", seen["worker"] != seen["main"],
      str(seen))
check("the same thread reuses its connection", id(db.connect(p)) == seen["main"])

section("core/db — transactions are atomic")
with db.transaction(p) as c:
    c.execute("INSERT INTO cancels (job_id) VALUES ('keep')")
check("a committed row is visible", con.execute(
    "SELECT count(*) FROM cancels WHERE job_id='keep'").fetchone()[0] == 1)
try:
    with db.transaction(p) as c:
        c.execute("INSERT INTO cancels (job_id) VALUES ('rolled-back')")
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("a failed transaction leaves nothing behind — this is the whole point, "
      "since a torn write is what silently deleted a job before",
      con.execute("SELECT count(*) FROM cancels WHERE job_id='rolled-back'"
                  ).fetchone()[0] == 0)

section("core/db — a corrupt file is reported, not swallowed")
bad = TMP / "corrupt.db"
bad.write_bytes(b"this is not a database" * 40)
try:
    db.connect(bad)
    check("a corrupt database raises rather than returning an empty one", False,
          "connect() returned normally")
except sqlite3.DatabaseError:
    check("a corrupt database raises rather than returning an empty one", True)

db.close_all()
raise SystemExit(report())
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python tests/smoke_db.py`
Expected: `ModuleNotFoundError: No module named 'qatf.core.db'`

- [ ] **Step 3: Write the module**

Create `qatf/core/db.py`:

```python
"""The only module in this project that imports sqlite3.

Keeping it here is what preserves `api -> jobs -> pipeline -> llm -> core`:
`core` may import stdlib, so `pipeline` can persist a transcript cache without
knowing anything about jobs or HTTP, and the CLI and the API share one code
path with two database locations.

Two properties are load-bearing and neither is visible from the callers:

  - **WAL.** Many readers and one writer, and readers never block. The previous
    design rewrote a whole JSON file per update with a `threading.RLock` around
    it, which protects one process and nothing else.
  - **Thread-local connections.** A sqlite3 connection object is not safe to
    share across threads and the job store is a thread pool. One handle per
    thread, created on first use.

WAL makes concurrent WRITES safe. It does NOT make `QATF_WORKERS` safe across
processes: two server processes would each pull jobs onto their own pool and run
the same job twice. Claiming a job — an atomic `UPDATE ... WHERE state='queued'`
handing exactly one worker the row — is a separate feature and is not here.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Bumped whenever `_MIGRATIONS` grows. Stored in `PRAGMA user_version`.
SCHEMA_VERSION = 1

#: Milliseconds a writer waits for a competing writer before raising. Without
#: it, two threads writing at once surface as `database is locked` rather than
#: as the brief wait it actually is.
BUSY_TIMEOUT_MS = 5000

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
  id         TEXT PRIMARY KEY,
  state      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  doc        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_state   ON jobs(state);
CREATE INDEX IF NOT EXISTS ix_jobs_created ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS cancels (job_id TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS transcripts (
  key                  TEXT PRIMARY KEY,
  language             TEXT,
  language_probability REAL,
  device               TEXT,
  compute_type         TEXT,
  words                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS word_edits (
  scope TEXT NOT NULL,
  idx   INTEGER NOT NULL,
  was   TEXT NOT NULL,
  text  TEXT NOT NULL,
  PRIMARY KEY (scope, idx)
);

CREATE TABLE IF NOT EXISTS detections (
  key        TEXT PRIMARY KEY,
  spans      TEXT NOT NULL,
  detections TEXT NOT NULL
);
"""

#: index -> the SQL that takes the schema from that version to the next.
_MIGRATIONS = [_SCHEMA_V1]

_local = threading.local()


def _handles() -> dict[str, sqlite3.Connection]:
    if not hasattr(_local, "handles"):
        _local.handles = {}
    return _local.handles


def connect(path: Path) -> sqlite3.Connection:
    """A connection for THIS thread to this database, migrated and ready.

    Cached per (thread, path): callers ask for a connection wherever they need
    one rather than threading a handle through every signature, and the cache
    is what stops that being one `sqlite3.connect` per query."""
    key = str(Path(path).resolve())
    handles = _handles()
    existing = handles.get(key)
    if existing is not None:
        return existing

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(key, timeout=BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA foreign_keys=ON")
    _migrate(con)
    handles[key] = con
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Apply every migration the file has not seen, in one transaction each.

    `PRAGMA user_version` rather than a table of our own: it costs no row, it
    cannot itself be half-written, and it is readable with any sqlite client
    when something has gone wrong."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    for index in range(version, len(_MIGRATIONS)):
        with con:
            con.executescript(_MIGRATIONS[index])
            con.execute(f"PRAGMA user_version={index + 1}")


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    """Run a block inside BEGIN IMMEDIATE, committing on success.

    IMMEDIATE, not the default deferred: the writer lock is taken up front, so
    two concurrent writers queue on `busy_timeout` instead of one discovering
    the conflict at COMMIT and losing its work."""
    con = connect(path)
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.rollback()
        raise
    else:
        con.commit()


def close_all() -> None:
    """Close this thread's handles. For tests and shutdown."""
    for con in _handles().values():
        con.close()
    _handles().clear()
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python tests/smoke_db.py`
Expected: all checks PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check .
git add qatf/core/db.py tests/smoke_db.py
git commit -m "feat(core): SQLite layer with WAL, thread-local handles and migrations"
```

---

### Task 2: the job record and cancels

**Files:**
- Modify: `qatf/jobs/store.py`
- Test: `tests/smoke_api.py` (append to the restart-recovery section)

**Interfaces:**
- Consumes: `db.connect`, `db.transaction`, `db.SCHEMA_VERSION`
- Produces: `JobStore.db_path -> Path` (`root/"qatf.db"`); `JobStore.get/require/list/update/create/delete` keep their existing signatures and return types

`self._jobs` and `self._cancels` are removed. Reads go to SQLite — that is what
makes the store correct when a second process writes, and it is the change that
`load_api`'s budgets will measure in Task 3.

- [ ] **Step 1: Write the failing checks**

Append to `tests/smoke_api.py`, in the `restart recovery` section:

```python
    section("sqlite persistence")
    import sqlite3 as _sqlite3

    _dbp = SETTINGS.data_dir / "qatf.db"
    check("the store keeps its records in one database file", _dbp.exists(),
          str(_dbp))
    _con = _sqlite3.connect(_dbp)
    _rows = _con.execute("SELECT count(*) FROM jobs").fetchone()[0]
    check("jobs are rows, not files", _rows > 0, str(_rows))
    check("?state= has an index to use, rather than scanning every record",
          any("ix_jobs_state" in (r[0] or "") for r in _con.execute(
              "SELECT name FROM sqlite_master WHERE type='index'")))

    # The failure this whole change exists to remove. A record truncated by a
    # crash used to be dropped SILENTLY by _recover's bare `continue`, so the
    # job vanished with no error anywhere.
    _plan = _con.execute("SELECT count(*) FROM jobs WHERE doc IS NULL"
                         ).fetchone()[0]
    check("no record is half-written — a transaction either lands or does not",
          _plan == 0)
    _con.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python tests/smoke_api.py`
Expected: FAIL — `qatf.db` does not exist.

- [ ] **Step 3: Replace the persistence half of `JobStore`**

In `qatf/jobs/store.py`, replace the imports and the persistence/accessor
sections. Keep `Cancelled`, `transcript_for`, the submission helpers and
`shutdown` exactly as they are.

```python
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
from .model import RUNNING_STATE_VALUES, Job, JobState, now
```

Replace `_record_path`, `_persist` and `_recover` with:

```python
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
```

Add `from ..core.utils import log` to the imports for that message.

Replace the accessors:

```python
    def get(self, job_id: str) -> Job | None:
        row = db.connect(self.db_path).execute(
            "SELECT doc FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

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
```

Replace the three cancel helpers:

```python
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
```

`self._lock` stays: `update` is read-modify-write on a dataclass and the lock
keeps that atomic within a process. The transaction is what makes the write
atomic on disk.

- [ ] **Step 4: Point the list route at the indexed query**

In `qatf/api/routers/jobs.py`, the list endpoint filters in Python today. Pass
the filter down instead — `store.list(state=state)` — so `ix_jobs_state` is
used. Leave the response shape untouched.

- [ ] **Step 5: Run every suite**

Run each with `PYTHONIOENCODING=utf-8`:
`tests/smoke_db.py`, `tests/smoke_pipeline.py`, `tests/smoke_api.py`, `tests/load_api.py`
Expected: smoke_pipeline 273, smoke_api 130 (127 + the 3 new), load_api 23.
If `load_api` fails on a latency budget, STOP and report the numbers — that is
Task 3's subject and must not be worked around here.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check .
git add qatf/jobs/store.py qatf/api/routers/jobs.py tests/smoke_api.py
git commit -m "feat(jobs): persist the job record and cancels in SQLite

A torn write used to DELETE a job: _persist wrote the whole record with
write_text, and _recover swallowed the resulting JSONDecodeError with a bare
continue, so a record truncated by a crash vanished with nothing logged. Writes
are now one transaction and a failed import is reported.

Reads move off the in-memory dict, which was only correct while exactly one
process wrote. ?state= is pushed into the query so it uses an index.

Pre-SQLite */job.json records are imported on first start and left on disk."
```

---

### Task 3: measure the read-path budgets

**Files:**
- Modify: `tests/load_api.py` (only if a budget genuinely needs restating, and only with the measurement beside it)

**Interfaces:** none — this task produces a number and a decision.

`load_api` asserts a per-job list cost and a poll latency during an upload.
Those budgets caught two real regressions before. Task 2 turned a dict lookup
into a query plus a JSON parse, so they are the honest place for that cost to
show up.

- [ ] **Step 1: Measure**

Run: `PYTHONIOENCODING=utf-8 python tests/load_api.py`
Record the reported per-job list cost and the p50/p99 poll latency.

- [ ] **Step 2: Compare against the pre-change numbers**

```bash
git stash
PYTHONIOENCODING=utf-8 python tests/load_api.py   # the JSON-file numbers
git stash pop
```

Put both sets of numbers in the commit message.

- [ ] **Step 3: Decide, and write the decision down**

If the suite passes: add the measured numbers to `docs/quality.md` under the API
section, beside the existing `GET /jobs` measurement.

If it fails: **do not loosen the budget.** Add a read-through cache in
`JobStore` keyed on `id -> (updated_at, Job)`, invalidated by a single
`SELECT id, updated_at FROM jobs` on `list()`. That keeps one query per list
call instead of one per row, and it is the smallest change that restores the
budget without going back to a cache that cannot see another process's writes.
Re-run and record both numbers either way.

- [ ] **Step 4: Commit**

```bash
git add docs/quality.md tests/load_api.py
git commit -m "docs(quality): record the read-path cost after the SQLite move"
```

---

### Task 4: the transcript cache

**Files:**
- Modify: `qatf/pipeline/asr.py:321-370`
- Modify: `qatf/jobs/store.py` (`transcript_for`)
- Test: `tests/smoke_pipeline.py` (the transcript-cache section)

**Interfaces:**
- Consumes: `db.connect`, `db.transaction`
- Produces: `asr.cache_key(model_size, language, initial_prompt=None, hotwords=None) -> str`, `asr.db_path(work: Path) -> Path`, `asr.read_cache(work: Path, key: str) -> Transcript | None`, `asr.write_cache(work: Path, key: str, transcript: Transcript) -> None`. `asr.cache_path` is KEPT, unchanged, used only by the importer.

The existing `cache_path` returns a `Path` and callers test `path.exists()`.
That idiom goes: `read_cache` returns `None` when there is no row.

- [ ] **Step 1: Write the failing checks**

Append to the transcript-cache section of `tests/smoke_pipeline.py`:

```python
section("transcript cache — SQLite")
_work = Path(tempfile.mkdtemp(prefix="qatf-tc-"))
_key = asr.cache_key("large-v3", "ar", None, "بايثون فلاتر")
check("the key is the filename stem the old cache used, so every rule already "
      "fought for carries over", _key.startswith("words-large-v3-ar-")
      and len(_key.split("-")[-1]) == 8, _key)
check("a miss is None, not an exception", asr.read_cache(_work, _key) is None)

_t = Transcript(words=[Word("مدار", 0.0, 0.4), Word("المحيطة", 0.4, 0.9)],
                language="ar", language_probability=1.0,
                device="cuda", compute_type="float16")
asr.write_cache(_work, _key, _t)
_back = asr.read_cache(_work, _key)
check("round trip keeps the words", [w.text for w in _back.words] == ["مدار", "المحيطة"])
check("round trip keeps the timings exactly",
      [(w.start, w.end) for w in _back.words] == [(0.0, 0.4), (0.4, 0.9)])
check("round trip keeps the provenance",
      (_back.language, _back.device, _back.compute_type) == ("ar", "cuda", "float16"))
check("a different language is a different key — the --language ar incident",
      asr.cache_key("large-v3", "en") != asr.cache_key("large-v3", "ar"))
check("a different vocabulary is a different key",
      asr.cache_key("large-v3", "ar", None, "x") != asr.cache_key("large-v3", "ar", None, "y"))
check("the database lands in the work directory, beside what it replaced",
      (_work / "qatf.db").exists())
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python tests/smoke_pipeline.py`
Expected: FAIL — `module 'qatf.pipeline.asr' has no attribute 'cache_key'`

- [ ] **Step 3: Implement**

In `qatf/pipeline/asr.py`, add above `cache_path`:

```python
def cache_key(model_size: str, language: str | None,
              initial_prompt: str | None = None,
              hotwords: str | None = None) -> str:
    """The cache key: model, forced language, and a digest of the seed.

    Identical derivation to the filename `cache_path` built, minus the directory
    and the suffix, so every rule already paid for carries over — keying on the
    output directory alone silently reused an English transcript after
    `--language ar`, and a vocabulary outside the key is a vocabulary that
    silently does nothing on a warm cache."""
    stem = f"words-{slugify(model_size)}-{slugify(language) if language else 'auto'}"
    seed = f"{initial_prompt or ''}\x00{hotwords or ''}"
    if seed.strip("\x00"):
        stem += "-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return stem


def db_path(work: Path) -> Path:
    """One database per work directory — `<out>/.work/qatf.db` for the CLI,
    `$QATF_DATA_DIR/<job>/.work/qatf.db` for a job."""
    return Path(work) / "qatf.db"
```

Replace `read_cache`/`write_cache`:

```python
def read_cache(work: Path, key: str) -> Transcript | None:
    """The cached transcript, or None. Imports a pre-SQLite file if one is there.

    What comes back is RAW Whisper output. Fixups, repetition repair and
    per-word corrections are applied by the callers on read and never written
    back — the moment a corrected transcript is indistinguishable from a raw one
    every number in docs/quality.md stops being reproducible."""
    con = db.connect(db_path(work))
    row = con.execute(
        "SELECT language, language_probability, device, compute_type, words "
        "FROM transcripts WHERE key=?", (key,)).fetchone()
    if row is None:
        legacy = Path(work) / f"{key}.json"
        if legacy.is_file():
            transcript = _read_legacy_cache(legacy)
            write_cache(work, key, transcript)   # the file is left in place
            return transcript
        return None
    return Transcript(
        words=words_from_dicts(json.loads(row["words"])),
        language=row["language"],
        language_probability=row["language_probability"],
        device=row["device"],
        compute_type=row["compute_type"],
    )


def write_cache(work: Path, key: str, transcript: Transcript) -> None:
    with db.transaction(db_path(work)) as con:
        con.execute(
            "INSERT INTO transcripts "
            "(key, language, language_probability, device, compute_type, words) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET language=excluded.language, "
            "language_probability=excluded.language_probability, "
            "device=excluded.device, compute_type=excluded.compute_type, "
            "words=excluded.words",
            (key, transcript.language, transcript.language_probability,
             transcript.device, transcript.compute_type,
             json.dumps(words_to_dicts(transcript.words), ensure_ascii=False)),
        )
```

Rename the existing file-reading body to `_read_legacy_cache(path: Path) -> Transcript`,
keeping its pre-0.2 bare-list tolerance.

Update `transcribe_cached` to use `cache_key`/`read_cache`/`write_cache`, and
`JobStore.transcript_for` to call `pipeline.read_cache(job.work_dir(self.root), key)`
with the key built from the same options.

- [ ] **Step 4: Run every suite**

Expected: smoke_pipeline 281 (273 + 8), smoke_api 130, load_api 23, smoke_db pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check .
git add qatf/pipeline/asr.py qatf/jobs/store.py tests/smoke_pipeline.py
git commit -m "feat(asr): transcript cache in SQLite, keyed as the filename was

Same key derivation, so the rules already paid for carry over: the Whisper size
and the forced language are in it, and a vocabulary outside it would silently do
nothing on a warm cache. A pre-SQLite words-*.json is imported on first read and
left on disk. The row holds RAW Whisper output — fixups, repair and per-word
edits stay applied on read."
```

---

### Task 5: the per-word overlay and the face cache

**Files:**
- Modify: `qatf/pipeline/edits.py:61-110`
- Modify: `qatf/pipeline/detect.py:164-200`
- Test: `tests/smoke_pipeline.py`

**Interfaces:**
- Consumes: `db.connect`, `db.transaction`
- Produces: `edits.load(work: Path, scope: str) -> list[Edit]`, `edits.save(work: Path, scope: str, edits: list[Edit]) -> None`; `detect.read_cache(work: Path, key: str) -> tuple[list[tuple[float,float]], list[Detection]]`, `detect.write_cache(work: Path, key: str, spans, dets) -> None`. `detect.cache_key(video, detector, tier) -> str` replaces `cache_path` for lookups.

- [ ] **Step 1: Write the failing checks**

Append to `tests/smoke_pipeline.py`:

```python
section("per-word overlay — SQLite")
_ew = Path(tempfile.mkdtemp(prefix="qatf-ed-"))
check("no overlay is an empty list, not an error", edits.load(_ew, "job1") == [])
edits.save(_ew, "job1", [Edit(index=1, was="من", text="مين")])
_got = edits.load(_ew, "job1")
check("the overlay round trips",
      len(_got) == 1 and _got[0].index == 1 and _got[0].text == "مين")
check("it records what it replaced, so a moved transcript goes stale rather "
      "than landing on an unrelated word", _got[0].was == "من")
check("scopes do not leak into each other", edits.load(_ew, "job2") == [])

section("face cache — SQLite")
_dw = Path(tempfile.mkdtemp(prefix="qatf-fc-"))
_dk = detect.cache_key(Path(__file__), "yunet", "balanced")
check("two videos get different keys — one output directory used to serve the "
      "first video's faces to the second",
      _dk != detect.cache_key(Path(__file__).parent / "_harness.py",
                              "yunet", "balanced"))
detect.write_cache(_dw, _dk, [(0.0, 4.0)],
                   [Detection(t=1.0, cx=0.5, cy=0.5, w=0.1, h=0.15, score=0.9)])
_spans, _dets = detect.read_cache(_dw, _dk)
check("the face cache round trips", _spans == [(0.0, 4.0)] and len(_dets) == 1)
check("a miss is empty, not an error", detect.read_cache(_dw, "nope") == ([], []))
```

- [ ] **Step 2: Run to verify it fails**

Expected: `TypeError` — `edits.load()` takes 1 positional argument but 2 were given.

- [ ] **Step 3: Implement**

`edits.py` — replace `path`, `load` and `save`:

```python
def load(work: str | Path, scope: str) -> list[Edit]:
    """The overlay for `scope`, or an empty list.

    `scope` is the job id on the API path and the resolved output directory on
    the CLI path: one database can hold several output directories' overlays,
    and they must not read each other's."""
    con = db.connect(Path(work) / "qatf.db")
    rows = con.execute(
        "SELECT idx, was, text FROM word_edits WHERE scope=? ORDER BY idx",
        (scope,))
    found = [Edit(index=r["idx"], was=r["was"], text=r["text"]) for r in rows]
    if found:
        return found
    legacy = Path(work) / "word-edits.json"
    if legacy.is_file():
        imported = _load_legacy(legacy)
        save(work, scope, imported)      # the file is left in place
        return imported
    return []


def save(work: str | Path, scope: str, edits: list[Edit]) -> None:
    """Replace the whole overlay for `scope`, in one transaction.

    Wholesale replace, matching `PUT /jobs/{id}/transcript`: re-submitting the
    untouched transcript clears every correction, which is how you undo."""
    with db.transaction(Path(work) / "qatf.db") as con:
        con.execute("DELETE FROM word_edits WHERE scope=?", (scope,))
        con.executemany(
            "INSERT INTO word_edits (scope, idx, was, text) VALUES (?,?,?,?)",
            [(scope, e.index, e.was, e.text) for e in edits])
```

Keep the existing file-reading body as `_load_legacy(path)`.

`detect.py` — replace `cache_path` lookups with:

```python
def cache_key(video: Path, detector: str, tier: str) -> str:
    """The stem the filename used: detector, tier, and a digest of the footage
    plus the settings that shape a detection."""
    return f"faces-{slugify(detector)}-{slugify(tier)}-{cache_key_for(video)}"
```

where `cache_key_for` is the existing `cache_key` renamed (it hashes path, size,
mtime, `DETECT_WIDTH` and `YUNET_SCORE`). Rewrite `read_cache`/`write_cache`
against the `detections` table with the same legacy-import-then-write pattern.

Update `detections_for` to use them.

- [ ] **Step 4: Run every suite, then the render suite**

Expected: smoke_pipeline 290 (281 + 9), smoke_api 130, load_api 23, and
`PYTHONIOENCODING=utf-8 python tests/verify_render.py` still 10 — it exercises
the face cache against real ffmpeg and is the only suite that does.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check .
git add qatf/pipeline/edits.py qatf/pipeline/detect.py tests/smoke_pipeline.py
git commit -m "feat(pipeline): per-word overlay and face cache in SQLite

Both keep their existing key derivations, so the face cache still separates two
videos rendered into one output directory and still re-detects when a detector
knob changes. Pre-SQLite files are imported on first read and left on disk."
```

---

### Task 6: keep the file-based contracts working

**Files:**
- Modify: `tests/score_transcript.py`
- Modify: `qatf/cli/runner.py` (only if `--plan` needs the export half)
- Test: `tests/smoke_pipeline.py`

**Interfaces:**
- Consumes: `asr.read_cache`, `asr.cache_key`
- Produces: `score_transcript.load_words(source: str | Path) -> list[Word]` accepting a JSON path OR `db:<db path>#<key>`

- [ ] **Step 1: Write the failing check**

```python
section("scorer reads a transcript from either store")
_sw = Path(tempfile.mkdtemp(prefix="qatf-sc-"))
_sk = asr.cache_key("large-v3", "ar")
asr.write_cache(_sw, _sk, Transcript(words=[Word("كلمة", 0.0, 0.5)], language="ar"))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "score_transcript", Path(__file__).parent / "score_transcript.py")
_sc = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sc)
check("the scorer still reads a plain words-*.json path",
      len(_sc.load_words(Path("run-fixed/.work/words-large-v3-ar-6da1b0b4.json"))) == 1390)
check("and reads a DB key, so the sweep tooling keeps working",
      [w.text for w in _sc.load_words(f"db:{_sw / 'qatf.db'}#{_sk}")] == ["كلمة"])
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `load_words` treats the `db:` string as a path.

- [ ] **Step 3: Implement**

In `tests/score_transcript.py`:

```python
def load_words(source: str | Path) -> list[Word]:
    """A transcript from either store.

    `db:<database>#<key>` reads a row; anything else is a words-*.json path.
    Both forms stay supported because the sweep in docs/quality.md compares
    runs side by side as files, and a measurement tool that can only read the
    live database cannot compare a run against one taken last week."""
    text = str(source)
    if text.startswith("db:"):
        target, _, key = text[3:].partition("#")
        con = db.connect(Path(target))
        row = con.execute("SELECT words FROM transcripts WHERE key=?",
                          (key,)).fetchone()
        if row is None:
            print(f"error: no transcript {key!r} in {target}", file=sys.stderr)
            raise SystemExit(2)
        return words_from_dicts(json.loads(row["words"]))
    return words_from_dicts(
        json.loads(_require_readable(Path(text), "input transcript")
                   .read_text(encoding="utf-8"))["words"])
```

- [ ] **Step 4: Verify the `--plan` round trip is untouched**

`--plan` reads and writes a JSON file and must keep doing so. Confirm by running
the CLI against the seeded work directory and re-running with `--plan`:

```bash
PYTHONIOENCODING=utf-8 python -m qatf "E:\Youtube content\متسلمش دماغك\متسلمش دماغك.mov" \
  -o E:\Qutf\run-sqlite --plan-only --language ar \
  --vocab-file prompts\ar-tech.txt --denoise
PYTHONIOENCODING=utf-8 python -m qatf "E:\Youtube content\متسلمش دماغك\متسلمش دماغك.mov" \
  -o E:\Qutf\run-sqlite --plan E:\Qutf\run-sqlite\plan.json --language ar \
  --vocab-file prompts\ar-tech.txt --denoise
```

Expected: `plan.json` is a real file after the first run, and the second run
reads it and re-snaps. If the transcript cache imported correctly, the second
run reports `reused cached transcript`.

- [ ] **Step 5: Commit**

```bash
python -m ruff check .
git add tests/score_transcript.py tests/smoke_pipeline.py
git commit -m "feat(tests): scorer reads a transcript from a file or a DB key"
```

---

### Task 7: documentation

**Files:**
- Modify: `docs/api.md`, `docs/architecture.md`, `docs/operations.md`, `CLAUDE.md`

**Interfaces:** documentation only.

- [ ] **Step 1: Correct the on-disk layout**

`docs/api.md` documents the work directory as JSON files. Replace with the real
layout: `qatf.db` at the data root, `qatf.db` inside each `.work/`, MP4s still
under `clips/`, and `plan.json` still a file because `--plan` is a documented
hand-edit round trip.

- [ ] **Step 2: State the boundary that will otherwise be assumed**

In `docs/api.md`'s "Limits to know before deploying" and in `core/db.py`'s
docstring: WAL makes concurrent writes safe; it does NOT make `QATF_WORKERS`
safe across processes, because two server processes would each pull jobs onto
their own pool and run the same job twice. Job claiming is not implemented.

- [ ] **Step 3: Update the layout trees**

`CLAUDE.md` and `docs/architecture.md` both carry a module tree. Add
`core/db.py` with a one-line description, and update the `tests/` line to
include `smoke_db.py`.

- [ ] **Step 4: Record what did not change**

In `docs/quality.md`, next to the transcript-cache entry: the row holds raw
Whisper output and the correction layers still run on read, so the numbers in
that file remain reproducible.

- [ ] **Step 5: Verify and commit**

```bash
python -m ruff check .
PYTHONIOENCODING=utf-8 python tests/smoke_api.py
git add docs CLAUDE.md
git commit -m "docs: SQLite layout, and the multi-process boundary it does not cross"
```

---

## Self-Review

**Spec coverage.** `core/db.py` → Task 1. Job record and cancels → Task 2. The
read-path budget risk → Task 3. Transcripts → Task 4. Word edits and detections
→ Task 5. `--plan` and the scorer → Task 6. Docs, including the
multi-process boundary → Task 7. Migration is folded into the task that owns
each table rather than being a separate pass, because an importer with no reader
cannot be tested. The spec's "MP4s stay on the filesystem" needs no task — no
task moves them, and Task 7 documents it.

**Placeholders.** None. Task 3 is deliberately a measurement with two named
branches and the exact fix for the failing one, not a "handle it" step.

**Type consistency.** `cache_key`/`db_path`/`read_cache`/`write_cache` in Task 4
are used under those names in Task 6. `edits.load(work, scope)` and
`edits.save(work, scope, edits)` in Task 5 match their checks.
`detect.cache_key(video, detector, tier)` in Task 5 matches its check.
`db.connect`/`db.transaction`/`db.close_all`/`SCHEMA_VERSION` from Task 1 are
used under those names in Tasks 2, 4, 5 and 6. `JobStore.list(state=None)` in
Task 2 matches the router change in the same task.
