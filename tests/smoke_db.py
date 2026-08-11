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
