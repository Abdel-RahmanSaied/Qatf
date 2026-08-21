"""Deployment settings, read from the environment exactly once per Settings object.

Previously these were module-level constants in the API module, resolved at import
time. That made them untestable and meant a test could not point the server at a
scratch directory without reimporting. `create_app(settings=...)` now takes an
explicit object; `from_env()` is just the default source.

Plain dataclass rather than pydantic-settings — pydantic is an API-only
dependency and the CLI must not need it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

# No default model name lives here any more. `core` cannot see the preset
# table (the arrows run `... -> llm -> core`), so any constant here is a second
# copy that drifts silently — this one still said `claude-sonnet-5` long after
# the Anthropic preset had moved to `claude-opus-5`, and nothing caught it
# because the only reader was a fallback branch that never ran.
# `llm.presets.resolve_model` is the single answer to "which model".


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    #: where job directories live
    data_dir: Path = Path("qatf-data")
    #: sandbox for POST /jobs. a security boundary, not a convenience — see CLAUDE.md
    media_root: Path = Path(".")
    #: concurrent jobs. 1 by default: two large-v3 loads fight over one GPU
    workers: int = 1
    max_upload_bytes: int = 2048 * 1024 * 1024
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False

    # -- stage 3 provider. See qatf/llm/presets.py for the keys. -----------
    #: preset key: anthropic | openai | kimi | glm | ollama | vllm | openrouter
    llm_provider: str = "anthropic"
    #: None means "use the preset's default model"
    llm_model: str | None = None
    #: point a preset at a different host (proxy, gateway, self-hosted)
    llm_base_url: str | None = None
    #: reasoning-effort hint, on providers that accept one
    llm_effort: str | None = "medium"
    llm_max_tokens: int = 16000
    llm_timeout: float = 600.0

    # There is deliberately no `model` property here. Resolving "which model
    # will actually be called" needs the preset table, and `core` may not
    # import `llm` — the arrows run `... -> llm -> core`. That property used
    # to do a lazy `from ..llm.presets import PRESETS` inside itself, which
    # was the only backwards import in the whole of `core/` and made the
    # layering rule this project states a lie. Use
    # `llm.presets.resolve_model(settings.llm_provider, settings.llm_model)`
    # instead; every caller is in `api`, `jobs` or `cli`, which may all import
    # `llm` already.

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            data_dir=Path(env.get("QATF_DATA_DIR", "qatf-data")).resolve(),
            media_root=Path(env.get("QATF_MEDIA_ROOT", ".")).resolve(),
            workers=max(1, _int(env, "QATF_WORKERS", 1)),
            max_upload_bytes=_int(env, "QATF_MAX_UPLOAD_MB", 2048) * 1024 * 1024,
            llm_provider=env.get("QATF_LLM_PROVIDER", "anthropic"),
            # QATF_MODEL is the pre-0.5 name for QATF_LLM_MODEL; still honoured
            llm_model=env.get("QATF_LLM_MODEL") or env.get("QATF_MODEL") or None,
            llm_base_url=env.get("QATF_LLM_BASE_URL") or None,
            llm_effort=env.get("QATF_LLM_EFFORT", "medium") or None,
            llm_max_tokens=_int(env, "QATF_LLM_MAX_TOKENS", 16000),
            llm_timeout=float(_int(env, "QATF_LLM_TIMEOUT", 600)),
            host=env.get("QATF_HOST", "127.0.0.1"),
            port=_int(env, "QATF_PORT", 8000),
            reload=bool(env.get("QATF_RELOAD")),
        )

    @property
    def max_upload_mb(self) -> int:
        return self.max_upload_bytes // 1024 // 1024


#: The settings a running server may change. Everything else is env-only.
#:
#: `media_root` and `data_dir` are absent on purpose. `media_root` is a security
#: boundary — without it a `POST /jobs` naming `../../etc/passwd` transcribes any
#: file the process can read — so an endpoint that can widen it is an endpoint
#: that can switch the sandbox off. `host`, `port` and `reload` are meaningless
#: once the process is up. No `*_API_KEY` appears here or anywhere on the wire:
#: presets name a credential and read it from the environment.
EDITABLE = frozenset({
    "llm_provider", "llm_model", "llm_base_url", "llm_effort",
    "llm_max_tokens", "llm_timeout", "workers",
})


def effective_settings(overrides: Mapping[str, object],
                       env: Mapping[str, str] | None = None) -> Settings:
    """`Settings` with saved overrides layered over the environment.

    PRECEDENCE INVERTS HERE, and only for `EDITABLE` keys:

        1. a saved override        <- the settings endpoint writes these
        2. QATF_* in the environment
        3. the dataclass default

    That is the opposite of `dotenv.py`, where the real environment always wins,
    and the difference is deliberate rather than an oversight.
    `docker-compose.yaml` sets `QATF_LLM_PROVIDER: "${QATF_LLM_PROVIDER:-anthropic}"`,
    so the variable is ALWAYS present in the container whether or not the
    operator set one. If the environment kept winning, a saved value could never
    take effect under Docker — the only deployment — and the feature would look
    broken rather than opinionated. `dotenv.py` itself is unchanged; a layer now
    sits above the environment for seven named keys.

    Takes the overrides as an ARGUMENT rather than opening the database: `core`
    may not import a store, it keeps every settings read from being a file read,
    and it makes the whole precedence rule testable against a dict with no temp
    directory and no migration.

    Returns a NEW frozen object. Nothing here mutates anything, which is what
    lets a job hold its own snapshot while a save happens underneath it."""
    base = Settings.from_env(env)
    clean = {k: v for k, v in overrides.items() if k in EDITABLE and v is not None}
    return replace(base, **clean) if clean else base


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide default. Cached, so setting QATF_* after first call has no
    effect — pass a Settings object explicitly instead of mutating os.environ."""
    return Settings.from_env()
