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

section("core/db — a v1 database migrates forward, not rewritten")
# `_migrate` replays only the migrations a file has not seen yet (PRAGMA
# user_version onward), so a file already at v1 must pick up v2's `imported`
# table on its next `connect()` without losing what v1 already wrote. Build a
# genuine v1 file BY HAND — applying only `_SCHEMA_V1`, exactly what `connect`
# did before `_SCHEMA_V2` existed — rather than through `db.connect`, which
# would apply both migrations to a fresh file and prove nothing about
# upgrading an old one.
v1_path = TMP / "v1.db"
_raw = sqlite3.connect(v1_path)
try:
    with _raw:
        _raw.executescript(db._SCHEMA_V1)
        _raw.execute("PRAGMA user_version=1")
        _raw.execute(
            "INSERT INTO word_edits (scope, idx, was, text) VALUES (?,?,?,?)",
            ("job-old", 0, "X", "Y"))
finally:
    _raw.close()

# Now open it the real way — the migration path an actual upgrade takes.
migrated = db.connect(v1_path)
check("an existing v1 database migrates forward to the current version",
      migrated.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
      and db.SCHEMA_VERSION == 2, str(db.SCHEMA_VERSION))
check("the v2 table exists after migrating an old file",
      migrated.execute(
          "SELECT name FROM sqlite_master WHERE type='table' AND name='imported'"
      ).fetchone() is not None)
check("a row written under v1 survives the migration to v2",
      tuple(migrated.execute(
          "SELECT was, text FROM word_edits WHERE scope='job-old' AND idx=0"
      ).fetchone()) == ("X", "Y"))
db.close(v1_path)

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
