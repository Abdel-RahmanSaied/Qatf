"""Anthropic provider — the official `anthropic` SDK, Messages API.

Deliberately NOT routed through an OpenAI-compatible shim. Anthropic publishes a
first-party SDK, and going through a compatibility layer would cost the three
things that matter most here: schema-constrained output (`output_config.format`),
adaptive thinking, and prompt caching of the transcript prefix.

Model IDs are exact strings with no date suffix. Current tiers:
    claude-opus-5     $5/$25 per MTok, 1M context   <- default
    claude-sonnet-5   $3/$15 per MTok, 1M context
    claude-haiku-4-5  $1/$5  per MTok, 200K context

Sampling parameters are removed on Claude Opus 5 / Sonnet 5 / Opus 4.7+ and
return a 400 — hence `sampling=False` below. The abstraction must never blanket-
forward temperature.
"""

from __future__ import annotations

from .base import Capabilities, Completion, LLMProvider

DEFAULT_MODEL = "claude-opus-5"

#: context windows by prefix. Everything current is 1M except the Haiku tier.
_CONTEXT = {"claude-haiku": 200_000}


def _context_for(model: str) -> int:
    for prefix, size in _CONTEXT.items():
        if model.startswith(prefix):
            return size
    return 1_000_000


class ClaudeProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, **kwargs):
        kwargs.setdefault("effort", "medium")
        super().__init__(
            model,
            Capabilities(
                json_schema=True,
                json_object=True,
                sampling=False,     # 400 on Opus 5 / Sonnet 5 / Opus 4.7+
                effort=True,
                context_tokens=_context_for(model),
            ),
            **kwargs,
        )

    def _client(self):
        import anthropic

        # base_url is for a local proxy/gateway only. For Claude on Bedrock,
        # Vertex or Foundry use that platform's dedicated client class instead —
        # a base_url override does not carry their auth.
        opts = {"timeout": self.timeout}
        if self.api_key:
            opts["api_key"] = self.api_key
        if self.base_url:
            opts["base_url"] = self.base_url
        return anthropic.Anthropic(**opts)

    def complete_json(self, prompt: str, schema: dict, *,
                      max_tokens: int = 16000) -> Completion:
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort

        resp = self._client().messages.create(
            model=self.model,
            max_tokens=max_tokens,
            output_config=output_config,
            messages=[{"role": "user", "content": prompt}],
        )

        # Safety classifiers can decline with HTTP 200 and an empty content list.
        # Reading content[0] unconditionally would raise IndexError here.
        if resp.stop_reason == "refusal":
            return Completion(
                text="", provider=self.name, model=resp.model,
                stop_reason="refusal",
                extra={"refusal": getattr(resp, "stop_details", None)},
            )

        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(
            text=text,
            provider=self.name,
            model=resp.model,
            input_tokens=getattr(resp.usage, "input_tokens", None),
            output_tokens=getattr(resp.usage, "output_tokens", None),
            stop_reason=resp.stop_reason,
        )
