# Editable server settings

Status: design approved 2026-08-22. Not implemented.

Sub-project **B** of two. **A** — job settings (per-job option editing, defaults
for new jobs, caption style as options) — is a separate spec and shares no code
with this one beyond the UI shell.

---

## Why

Changing the stage-3 model today means editing `.env` and recreating the
container. That happened three times in one session; one rebuild took thirty
minutes. Nothing about swapping `qwen/qwen3-235b-a22b` for
`anthropic/claude-opus-5` needs a new image — it is one string that stage 3
reads once per job.

The goal is to change that string from the UI and have the next job use it.

## Non-goals

- **API keys.** Presets name a credential (`key_env="OPENROUTER_API_KEY"`) and
  read it from the environment. Keys are never stored, never returned, never
  settable. `/healthz` already reports `llm_ready` and `llm_error` without
  exposing one; that is the pattern.
- **`media_root` and `data_dir`.** `media_root` is a security boundary: without
  it a `POST /jobs` naming `../../etc/passwd` transcribes any file the process
  can read. An endpoint that can widen it is an endpoint that can switch the
  sandbox off.
- **`host`, `port`, `reload`.** Meaningless after the process is up.
- **Live worker-pool resizing.** See "Effect timing".
- **Authentication.** The API has none, and this design does not add any. It
  does mean an editable setting must not become a capability — see "Security".

## Decisions

Three questions were settled before the design; each one bounds it.

### 1. Saved wins, environment seeds

`docker-compose.yaml` sets `QATF_LLM_PROVIDER: "${QATF_LLM_PROVIDER:-anthropic}"`,
so that variable is **always present in the container** whether or not the
operator set it. If the environment kept winning — the rule `core/dotenv.py`
establishes for `.env` parsing — a saved value could never take effect under
Docker, which is the only deployment. The feature would appear broken.

So for the editable keys only, precedence inverts:

```
1. saved override in qatf.db      <- the UI writes here
2. QATF_* from the environment    <- seed and fallback
3. the dataclass default
```

**This inversion is deliberate and must be documented next to the dotenv rule**,
or the next person to read both will file it as a bug. `dotenv.py` is unchanged:
it still refuses to overwrite a real environment variable. What changes is that
a layer now sits *above* the environment for seven named keys.

Clearing a saved value deletes the row and falls back to the environment. That
is why an absent row and a row holding `""` mean different things — see the data
model.

### 2. `base_url` is editable but allowlisted

`llm_base_url` decides which host receives the transcript **and** the
`Authorization: Bearer <key>` header. The API has no authentication in front of
it. Freely editable, it is a one-request credential-exfiltration path: point it
at a host you control and the next job posts the content and the credential to
you.

This is the same class of field as `pipeline/fetch.validate_url`, which is
already gated by an exact-host allowlist for exactly this reasoning — an
unvalidated URL is an outbound-request primitive.

A blunt "https on a public allowlist" rule cannot be used, because the
documented self-hosting path is `http://ollama:11434/v1`: plain HTTP, internal
container hostname. Refusing that would break a shipped feature.

Accepted:

- a URL whose host **exactly** matches the host of some preset's own `base_url`
- a URL whose host resolves entirely to loopback (`127.0.0.0/8`, `::1`),
  RFC1918 (`10/8`, `172.16/12`, `192.168/16`), or link-local — which is what
  covers `ollama`, `vllm`, and a box on the LAN

A host that does NOT resolve is accepted only when it is **single-label**
(`ollama`, `vllm`). Found in live verification: with the ollama container
stopped the name did not resolve, so "every address is private" was vacuously
false and its own URL was refused — you could not configure a service before
starting it. A single label cannot be a public DNS name; anything dotted stays
refused because it could begin resolving anywhere.

Refused, with 403: anything else, plus any URL carrying userinfo
(`https://openrouter.ai@evil/x` resolves to `evil`) — the same refusal
`fetch.validate_url` already makes.

Two details that decide whether this holds:

- **Every** resolved address must be private. A hostname resolving to one
  private and one public address is refused, not accepted on the first hit.
- The check resolves DNS, so it is **time-of-check/time-of-use**: a name that
  resolves privately at `PUT` can resolve publicly at request time. This is
  accepted rather than solved. Closing it needs the resolved IP pinned into the
  connection, which the `openai` SDK does not expose. The mitigation that
  matters is the credential: a private-range base_url is for a local model,
  which needs no key, so the exposure of a rebind is the transcript rather than
  the key. Say so in `docs/security.md` rather than implying the check is
  airtight.

### 3. Editable set is the LLM group plus `workers`

```
EDITABLE
  llm_provider    llm_effort        workers  (restart required)
  llm_model       llm_max_tokens
  llm_base_url    llm_timeout

ENV-ONLY
  media_root  data_dir  host  port  reload  max_upload_bytes  *_API_KEY
```

The six LLM keys are read fresh at the start of each job, so "applies to the
next job" needs no machinery. `workers` is different and is handled below.

---

## Data model

Schema v5 in `core/db.py`, which already does PRAGMA-versioned migrations and is
the only module that imports `sqlite3`.

```sql
CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,   -- JSON-encoded scalar
  updated_at TEXT NOT NULL
);
```

One row per overridden key; **an absent row means "not overridden"**. That is
deliberately distinct from a row holding `""`, which means "explicitly blank —
use the preset's default model / the preset's own base_url". Collapsing the two
would make "reset to environment" and "clear this field" the same button, and
they are not: one restores `QATF_LLM_MODEL`, the other asks the preset.

`value` is JSON rather than raw text so `workers` and `llm_timeout` round-trip as
numbers instead of being re-parsed from strings at every read.

Rows for keys outside `EDITABLE` are ignored on read, not just refused on write.
A table is a file someone can edit; the allowlist has to hold on the way out too.

## Resolution

`core/config.py` gains:

```python
EDITABLE: frozenset[str]

def effective_settings(overrides: Mapping[str, object],
                       env: Mapping[str, str] | None = None) -> Settings
```

`effective_settings` is **pure** — it takes the overrides as an argument rather
than opening the database. Two reasons: `core/config.py` doing I/O would make
every settings read a file read, and the whole precedence rule becomes unit
testable against a dict, with no temp directory and no migration.

`Settings` stays a frozen dataclass. Nothing is mutated; each call returns a new
one.

**`JobStore` computes the effective settings at job start, not at construction.**
That single choice is what makes the running-job guarantee in "Effect timing"
free rather than engineered: a job holds its own immutable snapshot, so there is
no window in which a save could reach it.

`get_settings()` and its `lru_cache` are untouched, and routers keep using the
injected/env `Settings` for `media_root` and the upload limit — both env-only, so
there is nothing to layer. Only stage 3 needs the effective object.

`create_app(settings=...)` keeps working: an explicitly injected `Settings`
bypasses the table entirely, so the tests that point at a scratch directory are
unaffected and cannot be perturbed by a stray row.

## API

New `api/routers/settings.py`.

| Method | Path | Does |
| --- | --- | --- |
| `GET` | `/settings` | every editable key, its effective value, and its **source** |
| `PUT` | `/settings` | partial update; allowlist enforced |
| `DELETE` | `/settings/{key}` | drop the override, fall back to the environment |

`GET` returns `source` per key — `saved` / `env` / `default` — because a UI that
shows a value without saying where it came from cannot offer a meaningful "reset
to environment", and the operator cannot tell a value they chose from one the
container handed them.

`PUT` is partial rather than wholesale replace: a settings page that must send
every field to change one is a settings page that overwrites a field someone
else changed between the read and the write.

Errors follow the existing rules. Unknown key → 422 naming the allowed set and
**never echoing the input** — the 422 handler strips FastAPI's `input` field but
cannot un-say a value a validator formatted into its own message, which is why
`whisper`, `preset` and `resolution` all had to stop quoting rejected values
back. Refused `base_url` → 403, describing the rule, not the value.

Every operation needs a summary, description, tag, hand-written `operationId`
and a declared error shape, or `smoke_api.py` fails the suite.

## Security

`validate_base_url` lives in **`llm/`**, not in the router. The risk is an
outbound request carrying a credential, and `llm/` is the layer that attaches
the credential — the same reasoning that puts `language` validation in
`asr.cache_path` because the risk is a path.

It is enforced **again** in the router, so a refusal is a synchronous 403 rather
than a 202 and a job that dies on a worker thread. That is the shape
`fetch.validate_url` already has, and the suite caught the first version of that
one returning 202 for `file:///etc/passwd`.

Host matching is exact, never `.endswith` — `openrouter.ai.evil.net` is how that
goes wrong.

## Effect timing

The six LLM keys apply to **the next job**. A job already running keeps the
settings it started with, because it captured its own snapshot at start; a
change cannot reach it. This matters beyond tidiness: the job record reports the
provider and model used, and if a mid-run change could alter them the record
would describe a run that did not happen.

`workers` is stored and shown with `restart_required: true`. We are not
resizing a live `ThreadPoolExecutor` — it cannot be done cleanly with jobs in
flight, and `QATF_WORKERS` is 1 by design because two concurrent `large-v3`
loads fight over one GPU. A flag that says so is honest; a resize that
half-works is not.

## Layering

```
api -> jobs -> pipeline -> llm -> core
```

- the table and its migration: `core/db.py`
- `EDITABLE` and `effective_settings`: `core/config.py`, pure
- `validate_base_url`: `llm/`
- reading the table and computing per-job settings: `jobs/store.py`
- the endpoints: `api/routers/settings.py`

No new arrows. `core/config.py` does not import `core/db.py`; the overrides are
passed in.

## Testing

**`smoke_db.py`** — v4 migrates to v5 without losing a row a v4 client wrote;
the settings table exists and is keyed.

**`smoke_pipeline.py`** — `effective_settings` precedence against a dict: saved
over env, env over default, absent row falls through, a row for a non-editable
key is ignored on read.

**`smoke_llm.py`** — `validate_base_url`: preset hosts accepted, loopback and
private accepted, public refused, `openrouter.ai.evil.net` refused (the
`.endswith` trap), userinfo refused.

**`smoke_api.py`** — the round trip; `source` transitions `env` → `saved` →
`env` across a PUT and a DELETE; unknown key 422 without echoing it; `base_url`
403 on a public host and 200 on a private one; **no response anywhere contains a
credential**; a job started before a PUT still reports the old model; the
OpenAPI document declares every operation and error.

`load_api.py` — `GET /settings` joins the read storm; it must not become a
per-request database scan on an endpoint the UI polls.

## UI

A Settings page and a nav entry. Per field: the value, a source badge, and a
per-field reset. `workers` carries the restart note. `RUNNING_STATES` already
exists for "is anything in flight" — a save while a job runs is allowed and
simply does not affect it, but the page should say so rather than let the
operator assume it retroactively applied.

No client-side mirror of the `base_url` rule. A mirror may only ever be looser
than the server, never stricter, and the private-range test is not something to
reimplement in TypeScript — the UI shows the server's 403.

## Risks

- **A saved value can be wrong at startup** — a provider whose key env var has
  since been removed, or a model ID that has moved (`kimi-k2` → `kimi-k3` was
  already stale when checked). Startup must not fail on it. `/healthz` already
  reports `llm_ready` and `llm_error`; a bad saved provider surfaces there and
  the page shows it, rather than every job failing after transcription.
- **The precedence inversion is surprising.** Mitigated only by documenting it
  in `CLAUDE.md` and `docs/operations.md` next to the dotenv rule.
- **This is the first mutable server state the project has.** Every other record
  is per-job. Concurrent writes are two threads writing one row; the store's
  existing WAL and `busy_timeout` cover it, and `load_api.py` should prove it.
