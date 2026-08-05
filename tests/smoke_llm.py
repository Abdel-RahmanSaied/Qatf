"""Provider-abstraction checks. No network, no API keys, no SDKs required.

Each provider's `_client()` is replaced with a fake that records exactly what
would have gone over the wire, so the assertions are about the *request shape* —
which is where provider differences actually bite (a rejected parameter is a 400,
not a graceful degrade).

    python tests/smoke_llm.py
"""

from __future__ import annotations

import json

from _harness import check, raises, report, section

from qatf.core.errors import ModelRefused, ModelResponseError, ProviderNotConfigured
from qatf.core.types import Word
from qatf.llm import build_provider, describe, resolve
from qatf.llm.base import Capabilities
from qatf.llm.claude import ClaudeProvider
from qatf.llm.openai_compat import OpenAICompatProvider
from qatf.pipeline import select

ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "OPENAI_API_KEY": "sk-test",
    "MOONSHOT_API_KEY": "sk-moon",
    "ZHIPU_API_KEY": "sk-zhipu",
}

CLIPS = {"clips": [
    {"start_mmss": "00:10", "end_mmss": "01:00", "title": "a",
     "hook": "h", "why": "w", "score": 0.9},
]}


# ---- fakes ---------------------------------------------------------------

class FakeAnthropic:
    def __init__(self, payload=None, stop_reason="end_turn"):
        self.captured = {}
        self._payload = payload if payload is not None else json.dumps(CLIPS)
        self._stop = stop_reason
        self.messages = self

    def create(self, **kwargs):
        self.captured = kwargs
        block = type("B", (), {"type": "text", "text": self._payload})()
        usage = type("U", (), {"input_tokens": 11, "output_tokens": 22})()
        return type("R", (), {
            "content": [block] if self._stop != "refusal" else [],
            "model": kwargs["model"], "usage": usage, "stop_reason": self._stop,
            "stop_details": type("D", (), {"category": "cyber"})(),
        })()


class FakeOpenAI:
    def __init__(self, payload=None, finish="stop"):
        self.captured = {}
        self._payload = payload if payload is not None else json.dumps(CLIPS)
        self._finish = finish
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.captured = kwargs
        msg = type("M", (), {"content": self._payload})()
        choice = type("C", (), {"message": msg, "finish_reason": self._finish})()
        usage = type("U", (), {"prompt_tokens": 5, "completion_tokens": 6})()
        return type("R", (), {"choices": [choice], "model": kwargs["model"],
                              "usage": usage})()


def bind(provider, fake):
    provider._client = lambda: fake
    return provider


section("presets")
keys = {p["key"] for p in describe()}
check("all documented providers present",
      {"anthropic", "openai", "kimi", "glm", "ollama", "vllm", "openrouter"} <= keys,
      str(sorted(keys)))
check("local providers need no key",
      not resolve("ollama").needs_key and not resolve("vllm").needs_key)
check("hosted providers declare a key env",
      all(resolve(k).key_env for k in ("anthropic", "openai", "kimi", "glm")))
raises("unknown provider rejected", ProviderNotConfigured, build_provider, "gemini")
raises("missing key rejected", ProviderNotConfigured,
       build_provider, "openai", env={})
check("local provider builds with no key at all",
      build_provider("ollama", env={}).name == "ollama")

section("anthropic request shape")
p = bind(ClaudeProvider("claude-opus-5", api_key="x"), FakeAnthropic())
out = p.complete_json("prompt", select.CLIP_SCHEMA, max_tokens=16000)
sent = p._client().captured
check("schema sent as output_config.format",
      sent["output_config"]["format"]["type"] == "json_schema")
check("effort travels in output_config", sent["output_config"].get("effort") == "medium")
check("no temperature sent", "temperature" not in sent)
check("no top_p / top_k sent", not {"top_p", "top_k"} & set(sent))
check("uses max_tokens", sent["max_tokens"] == 16000)
check("usage captured", out.input_tokens == 11 and out.output_tokens == 22)
check("parses to clips", len(select.parse_response(out.text)) == 1)

section("anthropic refusal")
p = bind(ClaudeProvider("claude-opus-5", api_key="x"), FakeAnthropic(stop_reason="refusal"))
ref = p.complete_json("prompt", select.CLIP_SCHEMA)
check("refusal surfaces without IndexError",
      ref.stop_reason == "refusal" and ref.text == "")

section("openai-compatible request shape")
gpt5 = build_provider("openai", env=ENV)
bind(gpt5, FakeOpenAI())
gpt5.complete_json("prompt", select.CLIP_SCHEMA, max_tokens=9000)
sent = gpt5._client().captured
check("strict json_schema requested",
      sent["response_format"]["json_schema"]["strict"] is True)
check("GPT-5 uses max_completion_tokens",
      "max_completion_tokens" in sent and "max_tokens" not in sent, str(sorted(sent)))
check("no temperature for GPT-5", "temperature" not in sent)

legacy = build_provider("openai-legacy", env=ENV)
bind(legacy, FakeOpenAI())
legacy.complete_json("prompt", select.CLIP_SCHEMA)
check("GPT-4 family uses max_tokens", "max_tokens" in legacy._client().captured)

section("json_object-only providers")
for key, expect_url in (("kimi", "moonshot"), ("glm", "bigmodel")):
    prov = build_provider(key, env=ENV)
    bind(prov, FakeOpenAI())
    prov.complete_json("prompt", select.CLIP_SCHEMA)
    sent = prov._client().captured
    check(f"{key} downgrades to json_object",
          sent["response_format"] == {"type": "json_object"}, str(sent["response_format"]))
    check(f"{key} points at its own host", expect_url in (prov.base_url or ""),
          str(prov.base_url))

ollama = build_provider("ollama", env={})
bind(ollama, FakeOpenAI())
ollama.complete_json("prompt", select.CLIP_SCHEMA)
check("ollama never claims json_schema",
      ollama._client().captured["response_format"] == {"type": "json_object"})
check("vllm does claim json_schema (guided decoding)",
      build_provider("vllm", env={}).caps.json_schema)

section("response parsing across tiers")
check("wrapped object shape", len(select.parse_response(json.dumps(CLIPS))) == 1)
check("bare array shape (wrapper dropped)",
      len(select.parse_response(json.dumps(CLIPS["clips"]))) == 1)
check("fenced output", len(select.parse_response(
    "```json\n" + json.dumps(CLIPS) + "\n```")) == 1)
check("alternate wrapper key", len(select.parse_response(
    json.dumps({"results": CLIPS["clips"]}))) == 1)
raises("empty response", ModelResponseError, select.parse_response, "   ")
raises("prose instead of JSON", ModelResponseError, select.parse_response, "sure!")
raises("object with no clips array", ModelResponseError,
       select.parse_response, '{"answer": 42}')

section("stage 3 wiring")
words = [Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(400)]
prov = bind(ClaudeProvider("claude-opus-5", api_key="x"), FakeAnthropic())
clips = select.pick_clips(words, 3, 30, 75, provider=prov)
check("pick_clips returns parsed clips", len(clips) == 1 and clips[0].title == "a")
check("transcript reached the prompt",
      "TRANSCRIPT:" in prov._client().captured["messages"][0]["content"])

truncated = bind(ClaudeProvider("claude-opus-5", api_key="x"),
                 FakeAnthropic(payload='{"clips": [', stop_reason="max_tokens"))
raises("truncation reported as truncation, not a syntax error",
       ModelResponseError, select.pick_clips, words, 3, 30, 75, None, truncated)

refused = bind(ClaudeProvider("claude-opus-5", api_key="x"),
               FakeAnthropic(stop_reason="refusal"))
raises("refusal surfaces as ModelRefused", ModelRefused,
       select.pick_clips, words, 3, 30, 75, None, refused)

section("context guard")
small = OpenAICompatProvider("tiny", Capabilities(json_object=True,
                                                  context_tokens=1000))
check("oversize transcript detected", not small.fits("x" * 100_000, 16000))
check("normal transcript fits", small.fits("x" * 100, 100))
check("unknown context window never blocks",
      OpenAICompatProvider("x", Capabilities()).fits("x" * 10_000_000, 16000))

raise SystemExit(report())
