# SQLite persistence — design

**Date:** 2026-08-11
**Status:** approved, not yet implemented
**Scope:** replace file-based persistence with SQLite for both front ends.

---

## Why

Not performance. Measured on the real job record (2,256 bytes):

```text
per-record write      JSON 264 us    SQLite WAL  30 us    8.8x
per-record read       JSON  66 us    SQLite WAL  17 us    3.9x
startup scan, n=5000  JSON 400 ms    SQLite      65 ms    6.1x
```

Those ratios are real and irrelevant. Reads never touch disk today — `JobStore`
holds every job in `self._jobs` and JSON is write-behind — and a job performs
about 15 record writes against a run that takes 70 seconds. Persistence is
0.006% of a job. Migrating for speed would be the fifth entry in this project's
list of things that looked worth optimizing and were not.

Three correctness problems motivate it instead:

1. **A torn write loses the job entirely.** `_persist` uses `path.write_text`,
   which is not atomic. `_recover` then does:

   ```python
   except (json.JSONDecodeError, TypeError, OSError):
       continue
   ```

   A record truncated by a crash is not reported, not quarantined, and not
   half-loaded — it is silently dropped, and the job disappears.

2. **The lock protects one process.** `JobStore._lock` is a `threading.RLock`.
   Two uvicorn workers would interleave whole-file rewrites of the same record
   today. Nothing in the code says "single process only".

3. **`?state=` is a full scan** over every record.

## Decisions taken

The project owner chose the maximal scope after the narrower options and their
costs were presented: **both front ends, all metadata and text artifacts**. The
constraints below are the consequences that must be engineered around, not
re-litigated.

---

## Architecture

`qatf/core/db.py` is the only module that imports `sqlite3`. `core` is allowed
stdlib, so this preserves `api -> jobs -> pipeline -> llm -> core` — `pipeline`
can use it for the transcript, edits and detection caches without knowing
anything about jobs or HTTP.

```text
$QATF_DATA_DIR/qatf.db      the API path
<out>/.work/qatf.db         the CLI path
```

One schema, one code path, two locations — which is how the two front ends
already differ. `core/db.py` exposes `connect(path)`, `migrate(conn)` and
thread-local connection handling. No ORM and no new dependency.

### What stays on the filesystem

Rendered MP4s. Putting 22 MB blobs in SQLite bloats the file, defeats range
requests on `GET /clips/{name}`, and buys nothing — they are addressed by name
and streamed, never queried.

## Schema

Hybrid, not normalised. Only columns that are queried get promoted; the rest of
the record stays in a JSON `doc`.

```sql
PRAGMA user_version = 1;

CREATE TABLE jobs (
  id         TEXT PRIMARY KEY,
  state      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  doc        TEXT NOT NULL
);
CREATE INDEX ix_jobs_state   ON jobs(state);
CREATE INDEX ix_jobs_created ON jobs(created_at DESC);

CREATE TABLE cancels (job_id TEXT PRIMARY KEY);

CREATE TABLE transcripts (
  key                  TEXT PRIMARY KEY,   -- what asr.cache_path encodes today
  language             TEXT,
  language_probability REAL,
  device               TEXT,
  compute_type         TEXT,
  words                TEXT NOT NULL       -- the word array, as JSON
);

CREATE TABLE word_edits (
  scope TEXT NOT NULL,      -- the work scope the overlay belongs to
  idx   INTEGER NOT NULL,
  was   TEXT NOT NULL,
  text  TEXT NOT NULL,
  PRIMARY KEY (scope, idx)
);

CREATE TABLE detections (
  key        TEXT PRIMARY KEY,   -- faces-<detector>-<tier>-<video key>
  spans      TEXT NOT NULL,
  detections TEXT NOT NULL
);
```

`Job` is a dataclass that gains fields — `output_sizes` did, recently — so
keeping the body as JSON means adding one is not a schema migration. It also
preserves the existing behaviour where an older record simply lacks a key and
`api.deps` falls back rather than reporting a wrong number.

### What the keys are, exactly

Two columns are string keys and both must be derived from what the code already
computes, not invented:

- **`transcripts.key`** is the *stem* `asr.cache_path` builds today —
  `words-<model>-<language>` plus the `-<8 hex>` seed digest when a prompt or
  vocabulary is set — without the directory or the `.json` suffix. Keeping the
  same derivation means the cache-key rules already fought for (the Whisper size
  and the forced language are in it; `--language ar` must not reuse an English
  transcript) carry over unchanged, and the lazy import can find the file that
  corresponds to a row.
- **`word_edits.scope`** identifies the work directory an overlay belongs to. On
  the API path that is the job id; on the CLI path it is the resolved `<out>`
  path. One DB per data root already separates jobs from each other on disk, so
  the column exists to keep the CLI's several output directories apart inside
  one CLI database, and to leave room for a future shared database.
- **`detections.key`** is the existing `faces-<detector>-<tier>-<video key>`
  filename stem, same reasoning.

## Concurrency

- **WAL.** Many readers, one writer, readers never block.
- **`busy_timeout=5000`.** A competing writer retries instead of raising.
- **Thread-local connections.** `sqlite3` connection objects are not safe to
  share across threads and this store is a thread pool. One connection per
  thread, created on first use.

**Multi-process correctness is storage-only, and that boundary must be stated
where someone will read it.** WAL makes concurrent writes safe. It does not make
`QATF_WORKERS` safe across processes: two uvicorn workers would each pull jobs
onto their own pool and run the same job twice. Job *claiming* — an atomic
`UPDATE ... WHERE state='queued'` handing exactly one worker the row — is a
separate feature and is **out of scope here**. Say so in the module docstring
and in `docs/api.md`, or the next person will assume this bought something it
did not.

## The read path

`self._jobs` is removed. Reads go to SQLite.

This is the part that actually buys multi-process correctness: a cached dict is
stale the moment another process writes, so keeping it would mean shipping a
store that is only correct by accident.

**It is also the main risk.** It converts a dict lookup into a query plus a JSON
parse, and `tests/load_api.py` asserts budgets on per-job list cost and on poll
latency during an upload. Those assertions exist because `GET /jobs` is the
endpoint clients poll, and they have caught two real regressions before.

If they fail, that is data, not an obstacle: bring the measurement back rather
than loosening the budget. A read-through cache keyed on `updated_at` is the
obvious next move if it comes to that, and it is deliberately not being built
speculatively.

## Migration

On startup, if `jobs` is empty and `*/job.json` records exist, import them and
**leave the files in place**. Nothing in the upgrade path deletes anything, so a
bad upgrade is reversible by checking out the previous commit.

`words-*.json` and the face caches are imported lazily on first read for the
same reason.

`PRAGMA user_version` carries the schema version so a later change has somewhere
to hang.

## What must not regress

- **`--plan FILE` stays file-based.** The DB is the store; `--plan` exports to a
  real file and imports back. It is the documented hand-edit round trip and the
  cheapest way to A/B two providers.
- **`tests/score_transcript.py` accepts a path or a DB key**, so the sweep
  tooling built today keeps working.
- **The `transcripts` row holds raw Whisper output.** Fixups, repetition repair
  and per-word edits stay applied *on read* and are never written back. That is
  what keeps every number in `docs/quality.md` reproducible, and changing the
  storage medium does not change the rule.
- **`GET /jobs/{id}/transcript` keeps returning blanked words**, and `PUT` keeps
  refusing a word-count or timing change.

## Testing

- `smoke_api.py` (127) and `load_api.py` (23) stay green, or a failure is
  reported with its numbers rather than worked around.
- **A torn write leaves the record intact.** Today the job vanishes; the new
  behaviour must be asserted, not assumed.
- **A corrupt row is reported, not silently skipped.**
- `?state=` uses the index rather than scanning.
- Migration imports existing `job.json` files and leaves them on disk.
- Two connections writing concurrently do not corrupt the file.

## Risks

| Risk | Guard |
| --- | --- |
| `load_api` latency budgets fail once reads hit SQLite | Measure and report; do not loosen the budget |
| `sqlite3` thread affinity | Thread-local connections, exercised by `load_api`'s 24 threads |
| Windows file locking under WAL | The suites run on this host; WAL creates `-wal`/`-shm` siblings that must be handled on delete |
| The CLI now writes a DB into every `-o` directory | Lives under `.work/`, which is already gitignored and already per-run scratch |
| Someone reads "SQLite" as "multi-process ready" | Stated in the module docstring, `docs/api.md`, and CLAUDE.md |

## Out of scope

Job claiming; a shared DB across hosts; Postgres; moving rendered MP4s.
