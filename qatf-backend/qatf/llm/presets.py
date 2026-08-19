"""Named provider presets.

A preset is the four things that differ between endpoints: where it lives, which
env var holds the key, what its default model is, and which structured-output
tier it honours. Adding a vendor should be adding a row here, not a class.

Capability values are from each vendor's documentation, not probed at runtime.
Where a vendor is ambiguous the preset is deliberately pessimistic — a provider
that claims json_schema and doesn't have it fails at request time, while one that
under-claims merely leans on `parse_response`, which is there anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core.errors import ProviderNotConfigured
from .base import Capabilities
from .claude import DEFAULT_MODEL as CLAUDE_DEFAULT
from .claude import ClaudeProvider
from .openai_compat import OllamaProvider, OpenAICompatProvider


@dataclass(frozen=True)
class Preset:
    key: str
    factory: type
    model: str
    caps: Capabilities
    base_url: str | None = None
    key_env: str | None = None
    #: hosted APIs need a key; a local server does not
    needs_key: bool = True
    note: str = ""


# GPT-5 / o-series: `max_tokens` is rejected in favour of `max_completion_tokens`,
# and a non-default `temperature` is rejected outright.
_GPT5 = Capabilities(
    json_schema=True, json_object=True, sampling=False, effort=True,
    context_tokens=400_000, token_param="max_completion_tokens",
)
_GPT4 = Capabilities(
    json_schema=True, json_object=True, sampling=True, effort=False,
    context_tokens=128_000,
)

#: Named only when the configured provider key matches no preset, which
#: `provider_for` refuses anyway — so this exists purely so a progress line or
#: a health check can still print something true instead of raising.
FALLBACK_MODEL = CLAUDE_DEFAULT

PRESETS: dict[str, Preset] = {
    "anthropic": Preset(
        key="anthropic", factory=ClaudeProvider, model=CLAUDE_DEFAULT,
        caps=Capabilities(json_schema=True, json_object=True, effort=True,
                          context_tokens=1_000_000),
        key_env="ANTHROPIC_API_KEY",
        note="Messages API via the official SDK. Schema-constrained output.",
    ),
    "openai": Preset(
        key="openai", factory=OpenAICompatProvider, model="gpt-5.1",
        caps=_GPT5, key_env="OPENAI_API_KEY",
        note="ChatGPT models. Strict json_schema; max_completion_tokens.",
    ),
    "openai-legacy": Preset(
        key="openai-legacy", factory=OpenAICompatProvider, model="gpt-4.1",
        caps=_GPT4, key_env="OPENAI_API_KEY",
        note="GPT-4 family — still takes max_tokens and temperature.",
    ),
    "kimi": Preset(
        key="kimi", factory=OpenAICompatProvider, model="kimi-k2-thinking",
        caps=Capabilities(json_object=True, sampling=True, context_tokens=256_000),
        base_url="https://api.moonshot.ai/v1", key_env="MOONSHOT_API_KEY",
        note="Moonshot Kimi. json_object only — no schema constraint. "
             "K2 is ~1T params: hosted API only, not self-hostable on one GPU.",
    ),
    "glm": Preset(
        key="glm", factory=OpenAICompatProvider, model="glm-4.6",
        caps=Capabilities(json_object=True, sampling=True, context_tokens=200_000),
        base_url="https://open.bigmodel.cn/api/paas/v4", key_env="ZHIPU_API_KEY",
        note="Zhipu GLM. json_object only. Smaller GLM variants self-host.",
    ),
    "ollama": Preset(
        key="ollama", factory=OllamaProvider, model="glm4:9b",
        caps=Capabilities(json_object=True, sampling=True, context_tokens=128_000),
        base_url="http://localhost:11434/v1", needs_key=False,
        note="Local, no key. Easiest self-host path; see docker-compose.yml.",
    ),
    "vllm": Preset(
        key="vllm", factory=OpenAICompatProvider, model="zai-org/GLM-4-9B-0414",
        caps=Capabilities(json_schema=True, json_object=True, sampling=True,
                          context_tokens=128_000),
        base_url="http://localhost:8001/v1", needs_key=False,
        note="Local vLLM. Supports guided decoding, so json_schema is real here.",
    ),
    "openrouter": Preset(
        key="openrouter", factory=OpenAICompatProvider,
        model="anthropic/claude-opus-5",
        caps=Capabilities(json_object=True, sampling=True, context_tokens=1_000_000),
        base_url="https://openrouter.ai/api/v1", key_env="OPENROUTER_API_KEY",
        note="One key, many models — the cheapest way to A/B providers. Model IDs "
             "are vendor-prefixed and move fast (kimi-k2 -> kimi-k3): check "
             "GET /api/v1/models rather than trusting a hardcoded default.",
    ),
}


def resolve(key: str) -> Preset:
    if key not in PRESETS:
        raise ProviderNotConfigured(
            f"unknown provider {key!r}. Known: {', '.join(sorted(PRESETS))}"
        )
    return PRESETS[key]


def describe() -> list[dict]:
    """For `GET /healthz` and `--list-providers` — what is available and why."""
    return [
        {
            "key": p.key,
            "default_model": p.model,
            "base_url": p.base_url,
            "key_env": p.key_env,
            "structured_output": ("json_schema" if p.caps.json_schema
                                  else "json_object" if p.caps.json_object
                                  else "prompt_only"),
            "context_tokens": p.caps.context_tokens,
            "note": p.note,
        }
        for p in PRESETS.values()
    ]


def resolve_model(provider: str, configured: str | None = None) -> str:
    """The model that will actually be called, for reporting.

    `configured` wins; otherwise the preset's default; otherwise
    `FALLBACK_MODEL`, so an unknown provider key still names a real model
    rather than crashing a progress line or a health check.

    This lives here rather than on `Settings` because `core` may not import
    `llm` — the arrows run `... -> llm -> core`. It used to be a property on
    `Settings` doing a lazy `from ..llm.presets import PRESETS` inside itself,
    which was the only backwards import in `core/` and quietly made the
    layering rule a lie. Callers are all in `api`, `jobs` and `cli`, every one
    of which may already import `llm`.
    """
    if configured:
        return configured
    preset = PRESETS.get(provider)
    return preset.model if preset else FALLBACK_MODEL


def with_overrides(preset: Preset, *, model: str | None = None,
                   base_url: str | None = None) -> Preset:
    """A preset is a starting point, not a cage — any field can be overridden,
    which is how a self-hosted Kimi or a proxied OpenAI endpoint is configured
    without adding a row."""
    return replace(preset,
                   model=model or preset.model,
                   base_url=base_url or preset.base_url)
