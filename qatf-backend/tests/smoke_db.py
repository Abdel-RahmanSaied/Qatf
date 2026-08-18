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
# `db.connect` caches the handle per (thread, path) and never re-runs
# `_migrate` on a cache hit, so calling it again with `con` still open proved
# nothing — a `_migrate` that dropped and recreated every table on each call
# would still pass, because the second `connect` never reached `_migrate` at
# all. `db.close(p)` first forces the second `connect` to be a REAL re-open —
# a fresh handle that runs `_migrate` again against the file on disk — which
# is the only way this check can see a destructive replay.
db.close(p)
con = db.connect(p)
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
      and db.SCHEMA_VERSION == 4, str(db.SCHEMA_VERSION))
check("the v2 table exists after migrating an old file",
      migrated.execute(
          "SELECT name FROM sqlite_master WHERE type='table' AND name='imported'"
      ).fetchone() is not None)
check("a row written under v1 survives the migration to v2 and v3",
      tuple(migrated.execute(
          "SELECT was, text FROM word_edits WHERE scope='job-old' AND idx=0"
      ).fetchone()) == ("X", "Y"))
db.close(v1_path)

section("core/db — a v2 database migrates to v3, rows intact")
# Same shape as the v1 check above, one version later: `_SCHEMA_V3` only adds a
# column to a table `_SCHEMA_V2` already created (`ALTER TABLE imported ADD
# COLUMN mtime REAL`), so this is the migration most likely to be gotten wrong
# by rewriting `imported` instead of altering it in place — a rewrite would
# lose whatever v2 had already written. Build a genuine v2 file BY HAND —
# `_SCHEMA_V1` then `_SCHEMA_V2`, `user_version=2` — rather than through
# `db.connect`, which would apply all three migrations to a fresh file and
# prove nothing about upgrading an old one.
v2_path = TMP / "v2.db"
_raw2 = sqlite3.connect(v2_path)
try:
    with _raw2:
        _raw2.executescript(db._SCHEMA_V1)
        _raw2.executescript(db._SCHEMA_V2)
        _raw2.execute("PRAGMA user_version=2")
        _raw2.execute(
            "INSERT INTO word_edits (scope, idx, was, text) VALUES (?,?,?,?)",
            ("job-v2", 0, "P", "Q"))
        _raw2.execute(
            "INSERT INTO imported (scope, kind) VALUES (?, ?)",
            ("job-v2", "word_edits"))
finally:
    _raw2.close()

# Open it the real way — the migration path an actual upgrade takes.
migrated2 = db.connect(v2_path)
check("an existing v2 database migrates forward to the current version",
      migrated2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
      and db.SCHEMA_VERSION == 4, str(db.SCHEMA_VERSION))
check("the v3 column exists on the v2 table after migrating",
      any(r[1] == "mtime" for r in
          migrated2.execute("PRAGMA table_info(imported)")))
check("a word_edits row written under v2 survives the migration to v3",
      tuple(migrated2.execute(
          "SELECT was, text FROM word_edits WHERE scope='job-v2' AND idx=0"
      ).fetchone()) == ("P", "Q"))
check("an imported row written under v2 survives the migration to v3, with "
      "the new column defaulting to NULL rather than the row being dropped",
      tuple(migrated2.execute(
          "SELECT kind, mtime FROM imported WHERE scope='job-v2'"
      ).fetchone()) == ("word_edits", None))
db.close(v2_path)

section("core/db — a v3 database migrates to v4, transcripts intact")
# `_SCHEMA_V4` adds `timing_source` to `transcripts`, a table `_SCHEMA_V1`
# created and that real installs are already full of. The behavioural risk is
# not the column appearing; it is what an OLD row reads back as. Every row
# written before this column existed came from Whisper, so a NULL must be read
# as "asr" — read it as anything else and `cuts.tail_for` would drop SNAP_TAIL
# to zero on a measured transcript, silently shortening every cached job's
# clips by 0.35s on the next re-snap.
v3_path = TMP / "v3.db"
_raw3 = sqlite3.connect(v3_path)
try:
    with _raw3:
        _raw3.executescript(db._SCHEMA_V1)
        _raw3.executescript(db._SCHEMA_V2)
        _raw3.executescript(db._SCHEMA_V3)
        _raw3.execute("PRAGMA user_version=3")
        _raw3.execute(
            "INSERT INTO transcripts (key, language, language_probability, "
            "device, compute_type, words) VALUES (?,?,?,?,?,?)",
            ("words-large-v3-ar", "ar", 1.0, "cuda", "float16",
             '[{"text": "\u0645\u064a\u0646", "start": 1.0, "end": 1.4}]'))
finally:
    _raw3.close()

migrated3 = db.connect(v3_path)
check("an existing v3 database migrates forward to the current version",
      migrated3.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
      and db.SCHEMA_VERSION == 4, str(db.SCHEMA_VERSION))
check("the v4 column exists on the v3 transcripts table after migrating",
      any(r[1] == "timing_source" for r in
          migrated3.execute("PRAGMA table_info(transcripts)")))
check("a transcripts row written under v3 survives, new column NULL",
      tuple(migrated3.execute(
          "SELECT language, device, timing_source FROM transcripts "
          "WHERE key='words-large-v3-ar'").fetchone()) == ("ar", "cuda", None))
db.close(v3_path)

# The one that actually matters: a pre-v4 row must READ BACK as measured.
from qatf.pipeline import cuts as _cuts  # noqa: E402

_restored = db.connect(v3_path)
_row = _restored.execute(
    "SELECT timing_source FROM transcripts WHERE key='words-large-v3-ar'").fetchone()
check("a NULL timing_source reads back as 'asr', not as captions",
      (_row["timing_source"] or "asr") == "asr")
check("and therefore keeps the full SNAP_TAIL",
      _cuts.tail_for(_row["timing_source"] or "asr") == 0.35,
      str(_cuts.tail_for(_row["timing_source"] or "asr")))
db.close(v3_path)

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
# Read through a BRAND NEW connection, not `con` (or `c`, the same handle that
# just wrote the row): a sqlite3 connection always sees its own uncommitted
# writes, transaction or not, so reading back through the writer proves
# nothing about whether `transaction`'s `con.commit()` actually ran — the
# check would stay green even with that commit deleted. `tests/smoke_api.py`
# reads its torn-write check the same way, for the same reason.
_fresh = sqlite3.connect(p)
try:
    check("a committed row is visible", _fresh.execute(
        "SELECT count(*) FROM cancels WHERE job_id='keep'").fetchone()[0] == 1)
finally:
    _fresh.close()
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
