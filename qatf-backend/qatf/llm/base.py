"""The provider contract.

Stage 3 is the only place a model is called, and it asks for exactly one thing:
given a timestamped transcript, return JSON describing which passages to clip.
That narrow contract is what makes swapping providers cheap — a provider only
has to answer `complete_json`.

Capabilities are declared, not probed. Sending a parameter a provider rejects is
a hard error at request time (Claude Opus 5 returns 400 for `temperature`; the
GPT-5 family rejects it too), so the caller must know what it may send *before*
it sends it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capabilities:
    """What a provider will actually accept.

    `json_schema` is the good tier: the endpoint constrains generation to a
    schema, so a malformed response is impossible rather than merely unlikely.
    `json_object` is the middle tier: valid JSON guaranteed, shape is not.
    Neither means prompt-only, and `parse_response` has to carry the weight.
    """

    #: constrained decoding against a supplied JSON Schema
    json_schema: bool = False
    #: "return valid JSON" mode, shape unconstrained
    json_object: bool = False
    #: accepts temperature / top_p. Claude Opus 5 and the GPT-5 family do not.
    sampling: bool = False
    #: accepts a reasoning-effort hint
    effort: bool = False
    #: context window, for the pre-flight length check. None = unknown.
    context_tokens: int | None = None
    #: name of the output-token parameter — the o-series and GPT-5 renamed it
    token_param: str = "max_tokens"


@dataclass
class Completion:
    """One provider response. `text` is raw — parsing stays in stage 3."""

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        return self.stop_reason in {"max_tokens", "length"}


class LLMProvider(ABC):
    """Implement `complete_json` and declare `caps`. Nothing else."""

    name: str = "provider"

    def __init__(self, model: str, caps: Capabilities, *,
                 api_key: str | None = None, base_url: str | None = None,
                 effort: str | None = None, timeout: float = 600.0):
        self.model = model
        self.caps = caps
        self.api_key = api_key
        self.base_url = base_url
        self.effort = effort
        self.timeout = timeout

    @abstractmethod
    def complete_json(self, prompt: str, schema: dict, *,
                      max_tokens: int = 16000) -> Completion:
        """Return the model's raw text. Ask for `schema` as strictly as the
        provider allows, and no more strictly than that."""

    # -- shared helpers ----------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        """Crude 4-chars-per-token estimate, for the pre-flight length check only.

        Deliberately not tiktoken: it is OpenAI's tokenizer and undercounts
        Claude by 15-20% (much more on non-Latin scripts — which is exactly the
        Arabic path this project cares about). Anything that needs a real count
        should ask the provider."""
        return len(text) // 4

    def fits(self, prompt: str, max_tokens: int) -> bool:
        if self.caps.context_tokens is None:
            return True
        return self.estimate_tokens(prompt) + max_tokens < self.caps.context_tokens

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name}:{self.model}>"
