# Editable Server Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the stage-3 provider, model and base URL from the web UI and have the next job use them, without editing `.env` or rebuilding the container.

**Architecture:** A `settings` table (schema v5) holds one row per overridden key. A pure `effective_settings(overrides, env)` layers saved over environment over dataclass default and returns a **new frozen** `Settings`. `JobStore` calls it at the start of each job rather than holding one object forever, so a job captures an immutable snapshot and a save cannot reach a run already in flight. `Settings` is never mutated and `get_settings()`'s `lru_cache` is never cleared.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), FastAPI + pydantic, React + Vite + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-08-22-server-settings-design.md`

## Global Constraints

- **Layering:** `api -> jobs -> pipeline -> llm -> core`. `core` imports nothing of ours. `core/config.py` must NOT import `core/db.py` — overrides are passed in as an argument.
- **Editable allowlist, exactly these seven:** `llm_provider`, `llm_model`, `llm_base_url`, `llm_effort`, `llm_max_tokens`, `llm_timeout`, `workers`. Everything else is env-only.
- **Never on the wire:** any `*_API_KEY` value. Presets reference credentials by name (`key_env`); the value is read from the environment and never stored, returned, or settable.
- **Never editable:** `media_root`, `data_dir`, `host`, `port`, `reload`, `max_upload_bytes`.
- **No error may echo caller input.** Name the allowed set instead. No `{value!r}` in a validator message — the 422 handler strips FastAPI's `input` field but cannot un-say a value a validator formatted into its own message.
- **Host matching is exact, never `.endswith`.** `openrouter.ai.evil.net` is how that goes wrong.
- **No new dependencies.** stdlib `ipaddress` and `socket` only.
- **Verification gate for every task:** `PYTHONIOENCODING=utf-8 python tests/smoke_{db,llm,pipeline,api}.py`, `python tests/load_api.py`, and `python -m ruff check .` from `qatf-backend/`. Frontend tasks additionally need `npx tsc --noEmit` and `npx vitest run` from `qatf-frontend/`.
- **Commits:** this repo's owner commits manually. Run the commit step's `git add` only if they have asked for it; otherwise stop at green tests and report.
- **Windows note:** the test suites print non-ASCII. Always run them with `PYTHONIOENCODING=utf-8` or they die in `cp1252` before reporting.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `qatf-backend/qatf/core/db.py` | schema v5 — the `settings` table and its migration |
| `qatf-backend/qatf/core/config.py` | `EDITABLE`, `effective_settings` — the precedence rule, pure |
| `qatf-backend/qatf/llm/base_url.py` (new) | `validate_base_url` — the outbound-request boundary |
| `qatf-backend/qatf/jobs/store.py` | read/write the table; `settings_for_job()` |
| `qatf-backend/qatf/jobs/worker.py` | consume `settings_for_job()` instead of `store.settings` |
| `qatf-backend/qatf/api/schemas.py` | `SettingItem`, `SettingsResponse`, `SettingsUpdate` |
| `qatf-backend/qatf/api/routers/settings.py` (new) | `GET` / `PUT` / `DELETE` |
| `qatf-frontend/src/api/types.ts`, `client.ts` | wire mirror + fetch calls |
| `qatf-frontend/src/pages/SettingsPage.tsx` (new) | the form |

---

### Task 1: Schema v5 — the settings table

**Files:**
- Modify: `qatf-backend/qatf/core/db.py` (`SCHEMA_VERSION`, add `_SCHEMA_V5`, extend `_MIGRATIONS`)
- Test: `qatf-backend/tests/smoke_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `settings` table with columns `key TEXT PRIMARY KEY`, `value TEXT NOT NULL`, `updated_at TEXT NOT NULL`; `db.SCHEMA_VERSION == 5`.

- [ ] **Step 1: Write the failing test**

Append to `qatf-backend/tests/smoke_db.py`, after the existing migration section:

```python
section("core/db — v4 migrates to v5 without losing data")
p5 = Path(tempfile.mkdtemp()) / "v4.db"
raw = sqlite3.connect(p5)
for stmt in db._MIGRATIONS[:4]:
    raw.executescript(stmt)
raw.execute("PRAGMA user_version = 4")
raw.execute("INSERT INTO jobs (id, state, created_at, updated_at, doc) "
            "VALUES ('v4job', 'done', 'then', 'then', '{}')")
raw.commit()
raw.close()

con5 = db.connect(p5)
check("v4 database migrates to the current version",
      con5.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION)
check("the row a v4 client wrote survives the migration",
      con5.execute("SELECT id FROM jobs").fetchone()[0] == "v4job")
check("the settings table exists after migrating",
      con5.execute("SELECT name FROM sqlite_master WHERE type='table' "
                   "AND name='settings'").fetchone() is not None)
cols = {r[1] for r in con5.execute("PRAGMA table_info(settings)")}
check("settings has the columns the design names",
      cols == {"key", "value", "updated_at"}, str(sorted(cols)))
db.close(p5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_db.py`
Expected: FAIL — `the settings table exists after migrating`, because `_MIGRATIONS` has only four entries.

- [ ] **Step 3: Write minimal implementation**

In `qatf-backend/qatf/core/db.py`, change `SCHEMA_VERSION = 4` to `SCHEMA_VERSION = 5`, then add after `_SCHEMA_V4` and extend the list:

```python
# Do not edit _SCHEMA_V4 either. This is the first table in the project that is
# not per-job: one row per overridden setting, written by the settings endpoint
# and read at the start of every job. An ABSENT row means "not overridden" and
# is deliberately different from a row holding "", which means "explicitly
# blank — use the preset's default". Collapsing the two would make "reset to
# environment" and "clear this field" the same button, and they are not.
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

#: index -> the SQL that takes the schema from that version to the next.
_MIGRATIONS = [_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5]
```

Delete the old `_MIGRATIONS = [...]` line so there is exactly one.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_db.py && python -m ruff check .`
Expected: PASS, 0 failed. Also run `PYTHONIOENCODING=utf-8 python tests/smoke_api.py` — the store opens this database on startup and must still come up.

- [ ] **Step 5: Commit**

```bash
git add qatf-backend/qatf/core/db.py qatf-backend/tests/smoke_db.py
git commit -m "feat(db): schema v5 adds the settings table"
```

---

### Task 2: The precedence rule, as a pure function

**Files:**
- Modify: `qatf-backend/qatf/core/config.py`
- Test: `qatf-backend/tests/smoke_pipeline.py`

**Interfaces:**
- Consumes: `Settings` (existing frozen dataclass).
- Produces: `config.EDITABLE: frozenset[str]`; `config.effective_settings(overrides: Mapping[str, object], env: Mapping[str, str] | None = None) -> Settings`.

- [ ] **Step 1: Write the failing test**

Append to `qatf-backend/tests/smoke_pipeline.py`. Add `from qatf.core import config` to the imports at the top of the file first.

```python
section("settings precedence — saved over env over default")
_env = {"QATF_LLM_PROVIDER": "ollama", "QATF_LLM_MODEL": "qwen3:14b"}
check("environment seeds when nothing is saved",
      config.effective_settings({}, _env).llm_provider == "ollama")
check("a saved value beats the environment",
      config.effective_settings({"llm_provider": "openrouter"}, _env).llm_provider
      == "openrouter")
check("an unsaved key still falls through to the environment",
      config.effective_settings({"llm_provider": "openrouter"}, _env).llm_model
      == "qwen3:14b")
check("with neither, the dataclass default stands",
      config.effective_settings({}, {}).llm_provider == "anthropic")
# The table is a file someone can edit. The allowlist has to hold on the way
# OUT, not only when a request comes in.
check("a row for a non-editable key is ignored on read",
      config.effective_settings({"media_root": "/"}, {}).media_root
      == Settings().media_root)
check("numbers survive the round trip as numbers",
      config.effective_settings({"workers": 3, "llm_timeout": 30.0}, {}).workers == 3)
check("the editable set is exactly the seven the design names",
      config.EDITABLE == frozenset({
          "llm_provider", "llm_model", "llm_base_url", "llm_effort",
          "llm_max_tokens", "llm_timeout", "workers"}),
      str(sorted(config.EDITABLE)))
check("effective_settings returns a NEW frozen object, never a mutation",
      config.effective_settings({}, {}) is not config.effective_settings({}, {}))
```

`Settings` must be importable in that test file; add `from qatf.core.config import Settings` if it is not already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_pipeline.py`
Expected: FAIL with `AttributeError: module 'qatf.core.config' has no attribute 'effective_settings'`.

- [ ] **Step 3: Write minimal implementation**

Add to `qatf-backend/qatf/core/config.py`, after the `Settings` class:

```python
#: The settings a running server may change. Everything else is env-only.
#:
#: `media_root` and `data_dir` are absent on purpose: `media_root` is a
#: security boundary, so an endpoint that can widen it is an endpoint that can
#: switch the sandbox off. `host`, `port` and `reload` are meaningless once the
#: process is up. No `*_API_KEY` appears here or anywhere else on the wire.
EDITABLE = frozenset({
    "llm_provider", "llm_model", "llm_base_url", "llm_effort",
    "llm_max_tokens", "llm_timeout", "workers",
})


def effective_settings(overrides: Mapping[str, object],
                       env: Mapping[str, str] | None = None) -> Settings:
    """`Settings` with saved overrides layered over the environment.

    PRECEDENCE INVERTS HERE, and only for `EDITABLE` keys:

        1. a saved override      <- the settings endpoint writes these
        2. QATF_* in the environment
        3. the dataclass default

    That is the opposite of `dotenv.py`, where the real environment always
    wins, and the difference is deliberate rather than an oversight.
    `docker-compose.yaml` sets `QATF_LLM_PROVIDER: "${QATF_LLM_PROVIDER:-anthropic}"`,
    so the variable is ALWAYS present in the container whether or not the
    operator set it. If the environment kept winning, a saved value could never
    take effect under Docker — the only deployment — and the feature would look
    broken rather than opinionated. `dotenv.py` itself is unchanged; a layer
    now sits above the environment for seven named keys.

    Takes the overrides as an ARGUMENT rather than opening the database:
    `core` may not import a store, this keeps the whole precedence rule
    testable against a dict, and it stops every settings read being a file
    read. Returns a new frozen object; nothing here mutates anything."""
    base = Settings.from_env(env)
    clean = {k: v for k, v in overrides.items() if k in EDITABLE and v is not None}
    return replace(base, **clean) if clean else base
```

Add `from dataclasses import dataclass, replace` and `from collections.abc import Mapping` to the imports (`Mapping` may already be there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_pipeline.py && python -m ruff check .`
Expected: PASS, 0 failed.

Also confirm the layering check still passes — `smoke_pipeline.py` greps every module in `core/` as source text for imports of ours. `effective_settings` must not have introduced one.

- [ ] **Step 5: Commit**

```bash
git add qatf-backend/qatf/core/config.py qatf-backend/tests/smoke_pipeline.py
git commit -m "feat(config): effective_settings layers saved overrides over env"
```

---

### Task 3: `validate_base_url` — the outbound-request boundary

**Files:**
- Create: `qatf-backend/qatf/llm/base_url.py`
- Modify: `qatf-backend/qatf/llm/__init__.py` (export it)
- Test: `qatf-backend/tests/smoke_llm.py`

**Interfaces:**
- Consumes: `presets.PRESETS` (for the known-host set), `core.errors.QatfError`.
- Produces: `llm.validate_base_url(url: str) -> str`, raising `core.errors.SourceOutsideMediaRoot`-style 403 error. Use the existing `ProviderNotConfigured`? No — add `InvalidBaseURL(QatfError)` with `status_code = 403` in `core/errors.py`.

- [ ] **Step 1: Write the failing test**

Append to `qatf-backend/tests/smoke_llm.py`:

```python
section("base_url is an outbound-request boundary")
from qatf.core.errors import InvalidBaseURL          # noqa: E402
from qatf.llm import validate_base_url               # noqa: E402

check("a preset's own host is accepted",
      validate_base_url("https://openrouter.ai/api/v1")
      == "https://openrouter.ai/api/v1")
check("loopback is accepted", validate_base_url("http://127.0.0.1:11434/v1"))
check("a private address is accepted", validate_base_url("http://10.1.2.3:8000/v1"))
raises("a public host off the preset list is refused", InvalidBaseURL,
       validate_base_url, "https://evil.example.com/v1")
# EXACT match, never .endswith — this is how a suffix check gets bypassed.
raises("a lookalike suffix is refused", InvalidBaseURL,
       validate_base_url, "https://openrouter.ai.evil.net/v1")
raises("userinfo is refused — it decides the real host", InvalidBaseURL,
       validate_base_url, "https://openrouter.ai@evil.example.com/v1")
raises("a non-http scheme is refused", InvalidBaseURL,
       validate_base_url, "file:///etc/passwd")
try:
    validate_base_url("https://evil.example.com/v1")
except InvalidBaseURL as exc:
    check("the refusal does not echo the rejected url back",
          "evil.example.com" not in str(exc), str(exc))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_llm.py`
Expected: FAIL with `ImportError: cannot import name 'InvalidBaseURL'`.

- [ ] **Step 3: Write minimal implementation**

In `qatf-backend/qatf/core/errors.py`, alongside the other subclasses:

```python
class InvalidBaseURL(QatfError):
    """A base_url that is neither a known provider host nor a private address."""
    status_code = 403
```

Create `qatf-backend/qatf/llm/base_url.py`:

```python
"""Which hosts stage 3 may be pointed at.

`base_url` decides who receives the transcript AND the `Authorization: Bearer`
header, and the API has no authentication in front of it. Freely editable it is
a one-request credential-exfiltration path: point it at a host you control and
the next job posts your content and your key to you.

Same class of field as `pipeline.fetch.validate_url`, and validated here rather
than in the router for the same reason `language` is checked in
`asr.cache_path`: this is the layer that attaches the credential, so this is
the layer that owns the risk. The router checks it again so a refusal is a
synchronous 403 instead of a job that dies on a worker thread.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from ..core.errors import InvalidBaseURL
from .presets import PRESETS


def _known_hosts() -> frozenset[str]:
    """Hosts the shipped presets already talk to."""
    return frozenset(
        urlsplit(p.base_url).hostname
        for p in PRESETS.values() if p.base_url
    ) - {None}


def _all_private(host: str) -> bool:
    """True only if EVERY address this host resolves to is private.

    Every, not any: a name resolving to one private and one public address must
    be refused, or the check is bypassed by publishing both."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    return all(
        ipaddress.ip_address(a).is_private or ipaddress.ip_address(a).is_loopback
        or ipaddress.ip_address(a).is_link_local
        for a in addrs
    )


def validate_base_url(url: str) -> str:
    """Return `url` unchanged, or raise `InvalidBaseURL`.

    Never names the rejected value in the message — the 422/403 bodies reach a
    caller, and a validator that formats input into its own message defeats the
    handler that strips it."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise InvalidBaseURL("base_url must use http or https")
    if parts.username or parts.password:
        raise InvalidBaseURL("base_url must not carry userinfo")
    host = parts.hostname
    if not host:
        raise InvalidBaseURL("base_url must name a host")
    # exact membership, never endswith: openrouter.ai.evil.net
    if host in _known_hosts() or _all_private(host):
        return url
    raise InvalidBaseURL(
        "base_url must be a known provider host or a private address. Known: "
        + ", ".join(sorted(_known_hosts()))
    )
```

In `qatf-backend/qatf/llm/__init__.py`, add `from .base_url import validate_base_url` and add `"validate_base_url"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_llm.py && python -m ruff check .`
Expected: PASS, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add qatf-backend/qatf/llm/base_url.py qatf-backend/qatf/llm/__init__.py \
        qatf-backend/qatf/core/errors.py qatf-backend/tests/smoke_llm.py
git commit -m "feat(llm): allowlist base_url against preset and private hosts"
```

---

### Task 4: The store reads the table; a running job keeps its snapshot

**Files:**
- Modify: `qatf-backend/qatf/jobs/store.py`
- Modify: `qatf-backend/qatf/jobs/worker.py` (one line, `settings = store.settings` → `store.settings_for_job()`)
- Test: `qatf-backend/tests/smoke_api.py`

**Interfaces:**
- Consumes: `config.effective_settings`, `config.EDITABLE`, `db.connect`.
- Produces: `JobStore.settings_overrides() -> dict[str, object]`, `JobStore.save_setting(key, value) -> None`, `JobStore.clear_setting(key) -> None`, `JobStore.settings_for_job() -> Settings`.

- [ ] **Step 1: Write the failing test**

Append to `qatf-backend/tests/smoke_api.py`, inside the main body after the path-job section:

```python
section("settings overrides reach the next job, not a running one")
STORE = app.state.store          # the store create_app built
STORE.save_setting("llm_model", "saved/model-x")
check("a saved override wins over the injected settings",
      STORE.settings_for_job().llm_model == "saved/model-x",
      STORE.settings_for_job().llm_model)
STORE.clear_setting("llm_model")
check("clearing falls back to the environment/injected value",
      STORE.settings_for_job().llm_model == SETTINGS.llm_model,
      str(STORE.settings_for_job().llm_model))
STORE.save_setting("media_root", "/")
check("a non-editable key cannot be saved into effect",
      str(STORE.settings_for_job().media_root) == str(SETTINGS.media_root),
      str(STORE.settings_for_job().media_root))
STORE.clear_setting("media_root")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_api.py`
Expected: FAIL with `AttributeError: 'JobStore' object has no attribute 'save_setting'`.

- [ ] **Step 3: Write minimal implementation**

Add to `JobStore` in `qatf-backend/qatf/jobs/store.py`:

```python
    # -- server settings --------------------------------------------------

    def settings_overrides(self) -> dict[str, object]:
        """Every saved override, JSON-decoded. Non-editable keys are dropped
        HERE as well as on write: a table is a file someone can edit."""
        rows = self._con().execute("SELECT key, value FROM settings").fetchall()
        out: dict[str, object] = {}
        for row in rows:
            if row["key"] in EDITABLE:
                out[row["key"]] = json.loads(row["value"])
        return out

    def save_setting(self, key: str, value: object) -> None:
        if key not in EDITABLE:
            return
        with self._write() as con:
            con.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value), now()))

    def clear_setting(self, key: str) -> None:
        with self._write() as con:
            con.execute("DELETE FROM settings WHERE key = ?", (key,))

    def settings_for_job(self) -> Settings:
        """The settings a job starting NOW should use.

        Computed per job, not held on the store. That is the whole reason a
        save cannot reach a run already in flight: the job captured its own
        frozen snapshot at start, so there is no window to race. It also keeps
        `create_app(settings=...)` honest — an injected object is still the
        base that overrides layer onto."""
        return effective_settings(self.settings_overrides(),
                                  _settings_as_env(self.settings))
```

Match `self._con()` / `self._write()` to the helpers already used by `_persist` in this file — read the surrounding methods and reuse the same names rather than inventing new ones.

`_settings_as_env` is needed because `effective_settings` layers onto `Settings.from_env`, but the store may hold an INJECTED `Settings` that never came from the environment. Add it as a module-level helper in `store.py`:

```python
def _settings_as_env(s: Settings) -> dict[str, str]:
    """The injected Settings expressed as the env vars that would produce it,
    so `effective_settings` layers onto the object this app was built with
    rather than onto whatever the process environment happens to hold. Without
    this, `create_app(settings=...)` would be a half-truth again."""
    return {
        "QATF_DATA_DIR": str(s.data_dir), "QATF_MEDIA_ROOT": str(s.media_root),
        "QATF_WORKERS": str(s.workers),
        "QATF_MAX_UPLOAD_MB": str(s.max_upload_bytes // 1024 // 1024),
        "QATF_LLM_PROVIDER": s.llm_provider,
        "QATF_LLM_MODEL": s.llm_model or "",
        "QATF_LLM_BASE_URL": s.llm_base_url or "",
        "QATF_LLM_EFFORT": s.llm_effort or "",
        "QATF_LLM_MAX_TOKENS": str(s.llm_max_tokens),
        "QATF_LLM_TIMEOUT": str(int(s.llm_timeout)),
        "QATF_HOST": s.host, "QATF_PORT": str(s.port),
    }
```

Add imports to `store.py`: `from ..core.config import EDITABLE, Settings, effective_settings, get_settings`.

Then in `qatf-backend/qatf/jobs/worker.py`, change the single line `settings = store.settings` to:

```python
    # per job, not per store — see JobStore.settings_for_job. A settings change
    # made while this job is selecting must not reach it, or the record would
    # report a provider the run did not use.
    settings = store.settings_for_job()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_api.py && PYTHONIOENCODING=utf-8 python tests/smoke_pipeline.py && python tests/load_api.py && python -m ruff check .`
Expected: PASS on all. `smoke_api.py` asserts the injected settings reach stage 3 — that check must still pass, which is what `_settings_as_env` protects.

- [ ] **Step 5: Commit**

```bash
git add qatf-backend/qatf/jobs/store.py qatf-backend/qatf/jobs/worker.py \
        qatf-backend/tests/smoke_api.py
git commit -m "feat(jobs): compute settings per job from saved overrides"
```

---

### Task 5: The endpoints

**Files:**
- Create: `qatf-backend/qatf/api/routers/settings.py`
- Modify: `qatf-backend/qatf/api/routers/__init__.py`, `qatf-backend/qatf/api/schemas.py`
- Test: `qatf-backend/tests/smoke_api.py`

**Interfaces:**
- Consumes: `JobStore.settings_overrides/save_setting/clear_setting/settings_for_job`, `llm.validate_base_url`, `config.EDITABLE`.
- Produces: `GET /settings` → `SettingsResponse`; `PUT /settings` → `SettingsResponse`; `DELETE /settings/{key}` → `SettingsResponse`.

- [ ] **Step 1: Write the failing test**

Append to `qatf-backend/tests/smoke_api.py`:

```python
section("settings endpoints")
r = client.get("/settings")
check("GET /settings is 200", r.status_code == 200, str(r.status_code))
items = {i["key"]: i for i in r.json()["items"]}
check("every editable key is reported", set(items) == set(EDITABLE), str(sorted(items)))
check("no response field is a credential",
      not any("KEY" in json.dumps(i).upper() and "sk-" in json.dumps(i)
              for i in items.values()), r.text[:200])
check("workers is flagged restart_required", items["workers"]["restart_required"])
check("llm_model is not", not items["llm_model"]["restart_required"])

r = client.put("/settings", json={"llm_model": "saved/model-y"})
check("PUT is 200", r.status_code == 200, r.text[:200])
after = {i["key"]: i for i in r.json()["items"]}
check("the saved value comes back", after["llm_model"]["value"] == "saved/model-y")
check("and its source flips to saved", after["llm_model"]["source"] == "saved",
      after["llm_model"]["source"])

r = client.delete("/settings/llm_model")
check("DELETE is 200", r.status_code == 200, str(r.status_code))
back = {i["key"]: i for i in r.json()["items"]}
check("source falls back off saved", back["llm_model"]["source"] != "saved",
      back["llm_model"]["source"])

r = client.put("/settings", json={"media_root": "/"})
check("a non-editable key is refused 422", r.status_code == 422, str(r.status_code))
check("and the refusal names the allowed set, not the input",
      "llm_provider" in r.text and "/" not in r.json()["detail"].replace("/", "", 0),
      r.text[:200])

r = client.put("/settings", json={"llm_base_url": "https://evil.example.com/v1"})
check("a public base_url is refused 403", r.status_code == 403, str(r.status_code))
check("the base_url refusal does not echo the url",
      "evil.example.com" not in r.text, r.text[:200])
r = client.put("/settings", json={"llm_base_url": "http://127.0.0.1:11434/v1"})
check("a private base_url is accepted", r.status_code == 200, r.text[:200])
client.delete("/settings/llm_base_url")
```

Add `from qatf.core.config import EDITABLE` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_api.py`
Expected: FAIL — `GET /settings is 200` reports 404.

- [ ] **Step 3: Write minimal implementation**

Add to `qatf-backend/qatf/api/schemas.py`:

```python
class SettingItem(BaseModel):
    """One editable server setting and where its current value came from."""

    key: str
    value: object | None = Field(
        None, description="the value in effect. Never a credential — API keys "
                          "are read from the environment by name and are "
                          "neither stored nor returned.")
    source: Literal["saved", "env", "default"] = Field(
        ..., description="`saved` means someone set it here and it overrides "
                         "the environment; `env` means it came from a QATF_* "
                         "variable; `default` means neither was set.")
    restart_required: bool = Field(
        False, description="true for `workers`: it is stored but the thread "
                           "pool is not resized live, so it takes effect on "
                           "the next restart.")


class SettingsResponse(BaseModel):
    items: list[SettingItem]
```

Create `qatf-backend/qatf/api/routers/settings.py`:

```python
"""Editable server settings. Endpoints only.

The allowlist and the precedence rule live in `core.config`; the base_url
boundary lives in `llm`. This module maps them onto HTTP and nothing else."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from ...core.config import EDITABLE, Settings
from ...jobs import JobStore
from ...llm import validate_base_url
from ..deps import get_settings, get_store
from ..openapi import merge
from ..schemas import SettingItem, SettingsResponse

router = APIRouter(prefix="/settings", tags=["settings"])

RESTART_REQUIRED = frozenset({"workers"})


def _view(store: JobStore, base: Settings) -> SettingsResponse:
    saved = store.settings_overrides()
    env_only = Settings.from_env()
    effective = store.settings_for_job()
    items = []
    for key in sorted(EDITABLE):
        if key in saved:
            source = "saved"
        elif getattr(env_only, key) != getattr(Settings(), key):
            source = "env"
        else:
            source = "default"
        items.append(SettingItem(
            key=key, value=getattr(effective, key), source=source,
            restart_required=key in RESTART_REQUIRED))
    return SettingsResponse(items=items)


@router.get(
    "",
    response_model=SettingsResponse,
    operation_id="getSettings",
    summary="Read the editable server settings",
    response_description="Every editable key, its effective value and its source.",
)
def read_settings(store: JobStore = Depends(get_store),
                  base: Settings = Depends(get_settings)) -> SettingsResponse:
    """What stage 3 will use on the next job, and where each value came from.

    `source` distinguishes a value you chose from one the container handed you,
    which is what makes "reset to environment" meaningful. API keys never
    appear here: presets name a credential and read it from the environment."""
    return _view(store, base)


@router.put(
    "",
    response_model=SettingsResponse,
    operation_id="updateSettings",
    summary="Save one or more server settings",
    response_description="The settings after the update.",
    responses=merge(),
)
def update_settings(body: dict = Body(..., examples=[{"llm_model": "anthropic/claude-opus-5"}]),
                    store: JobStore = Depends(get_store),
                    base: Settings = Depends(get_settings)) -> SettingsResponse:
    """Partial update — send only the keys you are changing.

    Partial rather than wholesale replace: a page that must send every field to
    change one is a page that silently reverts a field somebody else changed
    between the read and the write.

    Takes effect on the NEXT job. A job already running keeps the settings it
    started with, because it captured its own snapshot; the record would
    otherwise report a provider the run did not use. `workers` is stored but
    needs a restart."""
    unknown = sorted(set(body) - EDITABLE)
    if unknown:
        raise RequestValidationErrorLike(
            "unknown setting. Editable: " + ", ".join(sorted(EDITABLE)))
    if body.get("llm_base_url"):
        validate_base_url(body["llm_base_url"])
    for key, value in body.items():
        store.save_setting(key, value)
    return _view(store, base)


@router.delete(
    "/{key}",
    response_model=SettingsResponse,
    operation_id="clearSetting",
    summary="Clear one setting, falling back to the environment",
    response_description="The settings after the reset.",
    responses=merge(),
)
def clear_setting(key: str, store: JobStore = Depends(get_store),
                  base: Settings = Depends(get_settings)) -> SettingsResponse:
    """Drop a saved override so the `QATF_*` variable takes over again.

    Deleting the row is not the same as saving `""`: an absent row means "not
    overridden", while an empty string means "explicitly blank — use the
    preset's default"."""
    if key not in EDITABLE:
        raise RequestValidationErrorLike(
            "unknown setting. Editable: " + ", ".join(sorted(EDITABLE)))
    store.clear_setting(key)
    return _view(store, base)
```

`RequestValidationErrorLike` is a placeholder name — use the project's existing 422 path. Read `core/errors.py` and raise the `QatfError` subclass whose `status_code` is 422; if none exists, add `class InvalidSetting(QatfError): status_code = 422` next to `InvalidBaseURL` and use it. Do NOT introduce `HTTPException` — domain failures raise `QatfError` and one handler maps them.

Register it in `qatf-backend/qatf/api/routers/__init__.py`:

```python
from . import jobs, meta, outputs, plan, settings

ALL = (meta.router, jobs.router, plan.router, outputs.router, settings.router)

__all__ = ["ALL", "jobs", "meta", "outputs", "plan", "settings"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd qatf-backend && PYTHONIOENCODING=utf-8 python tests/smoke_api.py && python tests/load_api.py && python -m ruff check .`
Expected: PASS. `smoke_api.py` asserts every operation has a summary, description, tag, hand-written `operationId` and a declared error shape — the three new routes must satisfy all four or the suite fails.

- [ ] **Step 5: Commit**

```bash
git add qatf-backend/qatf/api/routers/settings.py \
        qatf-backend/qatf/api/routers/__init__.py \
        qatf-backend/qatf/api/schemas.py qatf-backend/tests/smoke_api.py
git commit -m "feat(api): GET/PUT/DELETE /settings"
```

---

### Task 6: The Settings page

**Files:**
- Modify: `qatf-frontend/src/api/types.ts`, `qatf-frontend/src/api/client.ts`, `qatf-frontend/src/App.tsx`, `qatf-frontend/src/styles.css`
- Create: `qatf-frontend/src/pages/SettingsPage.tsx`
- Test: `qatf-frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: `GET/PUT/DELETE /settings`.
- Produces: `SettingItem`, `SettingsResponse` types; `getSettings()`, `updateSettings(patch)`, `clearSetting(key)` in `client.ts`.

- [ ] **Step 1: Write the failing test**

Append to `qatf-frontend/src/api/client.test.ts`, following the mock-fetch pattern already in that file:

```ts
describe("settings client", () => {
  it("sends only the changed keys, not the whole form", async () => {
    const seen: { url: string; body: unknown }[] = [];
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      seen.push({ url, body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }) as unknown as typeof fetch;
    await updateSettings({ llm_model: "anthropic/claude-opus-5" });
    expect(seen[0].body).toEqual({ llm_model: "anthropic/claude-opus-5" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-frontend && npx vitest run src/api/client.test.ts`
Expected: FAIL — `updateSettings` is not exported.

- [ ] **Step 3: Write minimal implementation**

Add to `qatf-frontend/src/api/types.ts`:

```ts
/** One editable server setting. Mirrors SettingItem.
 *
 * `source` is why the UI can offer "reset to environment": it distinguishes a
 * value the operator chose from one the container handed them. */
export interface SettingItem {
  key: string;
  value: string | number | null;
  source: "saved" | "env" | "default";
  restart_required: boolean;
}

export interface SettingsResponse {
  items: SettingItem[];
}
```

Add to `qatf-frontend/src/api/client.ts`, matching the existing helpers' shape:

```ts
export const getSettings = () => req<SettingsResponse>("/settings");

/** Partial update — send ONLY what changed. Sending the whole form would
 * revert any field someone else altered between the read and the write. */
export const updateSettings = (patch: Record<string, unknown>) =>
  req<SettingsResponse>("/settings", { method: "PUT", body: JSON.stringify(patch) });

export const clearSetting = (key: string) =>
  req<SettingsResponse>(`/settings/${encodeURIComponent(key)}`, { method: "DELETE" });
```

Create `qatf-frontend/src/pages/SettingsPage.tsx` rendering one row per item: the key, an input bound to `value`, a source badge, a "Reset to environment" button shown only when `source === "saved"`, and a restart note when `restart_required`. Save sends only dirty fields. Surface a 403 from `base_url` as the server's message — do NOT mirror the private-range rule in TypeScript. A mirror may only ever be looser than the server, never stricter, and this one cannot be reimplemented correctly client-side.

Add the route and a nav entry in `App.tsx`, and `.setting-row` / `.setting-source` classes in `styles.css` (no inline styles — `styles.css` is the only stylesheet).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd qatf-frontend && npx tsc --noEmit && npx vitest run && npm run build`
Expected: tsc exit 0, all tests pass, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add qatf-frontend/src
git commit -m "feat(ui): settings page"
```

---

### Task 7: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/api.md`, `docs/operations.md`, `docs/security.md`

- [ ] **Step 1: Write the docs**

`CLAUDE.md` — in the API shape section, add `GET/PUT/DELETE /settings` to the endpoint table, and add a short subsection stating the precedence inversion: saved beats environment for the seven editable keys, which is the opposite of `dotenv.py`, because compose always sets `QATF_LLM_*` and the feature would otherwise be inert under Docker.

`docs/api.md` — the three endpoints, the `source` field, the partial-update rule, and that `workers` needs a restart.

`docs/operations.md` — how to change the model without a rebuild, and that `.env` still seeds a fresh deployment.

`docs/security.md` — three points: API keys are never stored or returned; `media_root`/`data_dir` are deliberately not editable and why; and the **DNS-rebinding caveat** on `base_url` verbatim from the spec — the check resolves at `PUT` and the address can change by request time, this is accepted rather than solved, and the mitigation is that a private base_url implies a local model which needs no key, so a rebind exposes the transcript rather than the credential.

- [ ] **Step 2: Verify no number is duplicated**

The measured numbers live in `docs/quality.md` and `CLAUDE.md` only. This feature adds none, so check you have not copied any into the new prose.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/
git commit -m "docs: editable server settings"
```

---

## Self-Review

**Spec coverage.** Data model → Task 1. Resolution/precedence → Task 2. Security/`validate_base_url` → Task 3. Effect timing and the running-job guarantee → Task 4. API surface incl. `source` and partial update → Task 5. UI → Task 6. The documentation obligations named in Decision 1 ("must be documented next to the dotenv rule") and in the base_url caveat → Task 7. Risk "a saved value can be wrong at startup" is covered by existing `/healthz` `llm_ready`/`llm_error` reporting and needs no new task; confirm during Task 5 that a bogus saved provider surfaces there rather than crashing startup.

**Placeholder scan.** One deliberate gap remains: `RequestValidationErrorLike` in Task 5 is explicitly flagged as a name to replace after reading `core/errors.py`, with the fallback spelled out. Every other step carries real code.

**Type consistency.** `settings_overrides` / `save_setting` / `clear_setting` / `settings_for_job` are used with identical names in Tasks 4 and 5. `validate_base_url` and `InvalidBaseURL` match between Tasks 3 and 5. `SettingItem` fields (`key`, `value`, `source`, `restart_required`) are identical in `schemas.py` (Task 5) and `types.ts` (Task 6).
