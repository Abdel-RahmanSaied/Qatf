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
