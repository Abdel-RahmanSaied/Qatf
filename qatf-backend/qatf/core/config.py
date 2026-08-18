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
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_MODEL = "claude-sonnet-5"


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

    @property
    def model(self) -> str:
        """Back-compat: the old single-provider `model` field.

        Reports the configured model, or the active preset's default when none
        is set, so job messages and /healthz keep naming a real model."""
        if self.llm_model:
            return self.llm_model
        from ..llm.presets import PRESETS
        preset = PRESETS.get(self.llm_provider)
        return preset.model if preset else DEFAULT_MODEL

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide default. Cached, so setting QATF_* after first call has no
    effect — pass a Settings object explicitly instead of mutating os.environ."""
    return Settings.from_env()
