"""Pluggable LLM providers for stage 3.

    base.py           the contract: complete_json + declared Capabilities
    claude.py         Anthropic Messages API, official SDK
    openai_compat.py  everything speaking /v1/chat/completions
    presets.py        named endpoints (openai, kimi, glm, ollama, vllm, ...)

Stage 3 is the only model call in the pipeline and its contract is tiny —
transcript in, JSON clip list out — which is what makes swapping providers a
config change rather than a rewrite. Stages 1, 4 and 5 stay model-free, so a
provider swap cannot affect cut accuracy or rendering; it only changes *which
passages* get picked.

    QATF_LLM_PROVIDER=anthropic   # or openai, kimi, glm, ollama, vllm
    QATF_LLM_MODEL=...            # override the preset default
    QATF_LLM_BASE_URL=...         # point a preset at a different host
"""

from __future__ import annotations

import os

from ..core.errors import ProviderNotConfigured
from .base import Capabilities, Completion, LLMProvider
from .base_url import validate_base_url
from .presets import PRESETS, Preset, describe, resolve, with_overrides

__all__ = [
    "LLMProvider", "Capabilities", "Completion",
    "Preset", "PRESETS", "resolve", "describe", "with_overrides",
    "build_provider", "provider_from_settings", "validate_base_url",
]


def build_provider(key: str, *, model: str | None = None,
                   base_url: str | None = None, api_key: str | None = None,
                   effort: str | None = None,
                   timeout: float = 600.0,
                   env: dict | None = None) -> LLMProvider:
    """Construct a provider from a preset key plus overrides."""
    env = os.environ if env is None else env
    preset = with_overrides(resolve(key), model=model, base_url=base_url)

    resolved_key = api_key
    if resolved_key is None and preset.key_env:
        resolved_key = env.get(preset.key_env)
    if preset.needs_key and not resolved_key:
        raise ProviderNotConfigured(
            f"provider {preset.key!r} needs an API key — set {preset.key_env}"
        )

    provider = preset.factory(
        preset.model,
        api_key=resolved_key,
        base_url=preset.base_url,
        effort=effort,
        timeout=timeout,
    )
    # the preset's declared capabilities win over the class defaults, because
    # the same class serves endpoints with different structured-output tiers
    provider.caps = preset.caps
    provider.name = preset.key
    return provider


def provider_from_settings(settings=None) -> LLMProvider:
    from ..core.config import get_settings

    s = settings or get_settings()
    return build_provider(
        s.llm_provider,
        model=s.llm_model,
        base_url=s.llm_base_url,
        effort=s.llm_effort,
        timeout=s.llm_timeout,
    )
