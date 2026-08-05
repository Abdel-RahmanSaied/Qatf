"""Everything that speaks /v1/chat/completions.

One class covers OpenAI itself and every OpenAI-compatible endpoint — Moonshot
(Kimi), Zhipu (GLM), vLLM, Ollama, LM Studio, OpenRouter, Together. They differ
only in `base_url`, the key env var, and which structured-output tier they
support, so those are data (see `presets.py`), not subclasses.

Two shapes vary and both are declared per preset rather than sniffed:

  - `token_param` — the o-series and GPT-5 renamed `max_tokens` to
    `max_completion_tokens` and reject the old name.
  - `sampling` — the same families reject a non-default `temperature`.
"""

from __future__ import annotations

from .base import Capabilities, Completion, LLMProvider


class OpenAICompatProvider(LLMProvider):
    """`base_url=None` means api.openai.com; anything else is a compatible host."""

    name = "openai-compatible"

    def __init__(self, model: str, caps: Capabilities | None = None, **kwargs):
        super().__init__(model, caps or Capabilities(json_object=True), **kwargs)

    def _client(self):
        from openai import OpenAI

        return OpenAI(
            api_key=self.api_key or "not-needed",   # local servers ignore it
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def _response_format(self, schema: dict) -> dict | None:
        """Ask for the strictest mode this endpoint actually supports.

        Requesting json_schema from a server that only knows json_object is a
        400, not a graceful downgrade — which is why the tier is declared."""
        if self.caps.json_schema:
            return {
                "type": "json_schema",
                "json_schema": {"name": "clip_plan", "strict": True, "schema": schema},
            }
        if self.caps.json_object:
            return {"type": "json_object"}
        return None

    def complete_json(self, prompt: str, schema: dict, *,
                      max_tokens: int = 16000) -> Completion:
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            self.caps.token_param: max_tokens,
        }
        if (fmt := self._response_format(schema)) is not None:
            kwargs["response_format"] = fmt
        if self.caps.effort and self.effort:
            kwargs["reasoning_effort"] = self.effort

        resp = self._client().chat.completions.create(**kwargs)
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        return Completion(
            text=choice.message.content or "",
            provider=self.name,
            model=resp.model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            stop_reason=choice.finish_reason,
        )


class OllamaProvider(OpenAICompatProvider):
    """Ollama's OpenAI surface is real but partial.

    It accepts `response_format={"type": "json_object"}` and ignores unknown
    fields rather than erroring, so a json_schema request would silently produce
    unconstrained output — worse than an error. Pinned to json_object.
    """

    name = "ollama"
