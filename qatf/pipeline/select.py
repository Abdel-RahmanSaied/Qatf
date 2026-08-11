"""Stage 3 — the model proposes clips.

The ONLY thing the model is asked for is *which passages*, at MM:SS resolution.
It is never asked for precise timing, because it has no way to know it and will
confabulate sub-second values. Stage 4 fixes the boundaries.

UNVERIFIED: never executed against the real API.
"""

from __future__ import annotations

import json
import re

from ..core.config import get_settings
from ..core.constants import BLOCK_SECONDS
from ..core.errors import ModelRefused, ModelResponseError, TranscriptTooLong
from ..core.types import Clip, Word
from ..core.utils import mmss_to_seconds, ts_human
from ..llm import LLMProvider, build_provider

PROMPT = """You are selecting standalone short-form clips from a long video transcript.

Each transcript line is prefixed with its start time as [MM:SS].

Pick the {n} strongest candidate clips. A strong clip:
- is self-contained — understandable with zero context from the rest of the video
- opens on a hook: a claim, a question, a number, a contradiction, a story opening
- resolves. It does not stop mid-argument
- runs {lo}-{hi} seconds

Do NOT pick a passage just because it sounds important. Pick passages that make
sense to someone who dropped in cold.

Return ONLY JSON, no prose, no markdown fences:
{{
  "clips": [
    {{
      "start_mmss": "MM:SS",
      "end_mmss": "MM:SS",
      "title": "short punchy title, max 60 chars",
      "hook": "the first sentence a viewer hears, quoted from the transcript",
      "why": "one sentence: why this works standalone",
      "score": 0.0
    }}
  ]
}}
"score" is your confidence 0.0-1.0 that this works as a standalone short.

TRANSCRIPT:
{transcript}"""

#: The object wrapper is not cosmetic: OpenAI strict mode rejects a bare array at
#: the schema root, so the one shape that works everywhere is {"clips": [...]}.
#: `additionalProperties: false` is required by strict mode on every key.
CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_mmss": {"type": "string"},
                    "end_mmss": {"type": "string"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "why": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["start_mmss", "end_mmss", "title", "hook", "why", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


def build_transcript_blocks(words: list[Word],
                            block_seconds: float = BLOCK_SECONDS) -> str:
    """Timestamped transcript. Coarse blocks keep the token count sane and stop
    the model from inventing precise timings it can't actually know."""
    lines, buf, block_start = [], [], words[0].start if words else 0.0
    for w in words:
        if w.start - block_start >= block_seconds and buf:
            lines.append(f"[{ts_human(block_start)}] {' '.join(buf)}")
            buf, block_start = [], w.start
        # `health.repair` blanks a decoder repetition loop's duplicates rather
        # than deleting them, so a blanked token is still a real element of
        # `words` — `w.text == ""`. `captions.group_words` already knows to
        # skip it; this loop didn't, so a blanked run became a run of double
        # spaces in the prompt sent to stage 3. Harmless to the model, but a
        # transcript the operator reads (this string is what gets logged and
        # sent) should not show damage that was supposedly already repaired.
        if w.text:
            buf.append(w.text)
    if buf:
        lines.append(f"[{ts_human(block_start)}] {' '.join(buf)}")
    return "\n".join(lines)


def parse_response(raw: str) -> list[Clip]:
    """Turn a provider's raw text into clips.

    Kept separate from the API call so it can be tested without one — and
    deliberately defensive, because it is the only layer shared by every
    provider. Schema-constrained providers make it redundant; json_object
    providers make it necessary; local models make it load-bearing."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    if not raw:
        raise ModelResponseError("model returned an empty response")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelResponseError(
            f"model did not return valid JSON:\n{raw[:800]}") from exc

    # {"clips": [...]} is the requested shape; a bare array is accepted because
    # json_object-only providers routinely drop the wrapper.
    if isinstance(data, dict):
        for key in ("clips", "results", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ModelResponseError(
                f"expected a 'clips' array, got keys {sorted(data)[:6]}")
    if not isinstance(data, list):
        raise ModelResponseError(f"expected a JSON array, got {type(data).__name__}")

    clips = []
    for item in data:
        try:
            clips.append(Clip(
                start=mmss_to_seconds(item["start_mmss"]),
                end=mmss_to_seconds(item["end_mmss"]),
                title=str(item.get("title", "clip")),
                hook=str(item.get("hook", "")),
                why=str(item.get("why", "")),
                score=float(item.get("score", 0.0)),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelResponseError(f"malformed clip in response: {item!r}") from exc
    return sorted(clips, key=lambda c: c.score, reverse=True)


def build_prompt(words: list[Word], n: int, lo: int, hi: int) -> str:
    return PROMPT.format(n=n, lo=lo, hi=hi,
                         transcript=build_transcript_blocks(words))


def pick_clips(words: list[Word], n: int, lo: int, hi: int,
               model: str | None = None,
               provider: LLMProvider | None = None,
               settings=None) -> list[Clip]:
    """Ask the configured provider which passages to clip.

    `model` is kept for callers that pass one positionally; it overrides the
    configured model on whichever provider is active.

    `settings` must be threaded through from the caller. Falling back to the
    process-wide `get_settings()` here made `create_app(settings=...)` a
    half-truth: the HTTP layer honoured the injected object while stage 3 — the
    only part that opens a network connection and spends a credential — quietly
    read the environment's provider, base_url and timeout instead."""
    settings = settings or get_settings()
    if provider is None:
        provider = build_provider(
            settings.llm_provider,
            model=model or settings.llm_model,
            base_url=settings.llm_base_url,
            effort=settings.llm_effort,
            timeout=settings.llm_timeout,
        )

    prompt = build_prompt(words, n, lo, hi)
    if not provider.fits(prompt, settings.llm_max_tokens):
        raise TranscriptTooLong(
            f"transcript is ~{provider.estimate_tokens(prompt):,} tokens, which "
            f"will not fit {provider.name}:{provider.model} "
            f"({provider.caps.context_tokens:,} tokens). Use a longer-context "
            f"provider, or raise BLOCK_SECONDS to coarsen the transcript."
        )

    result = provider.complete_json(prompt, CLIP_SCHEMA,
                                    max_tokens=settings.llm_max_tokens)
    if result.stop_reason == "refusal":
        raise ModelRefused(
            f"{provider.name}:{provider.model} declined this transcript "
            f"({result.extra.get('refusal')})"
        )
    if result.truncated:
        # a truncated JSON body is unparseable, so say why rather than letting
        # parse_response report a syntax error at the cut point
        raise ModelResponseError(
            f"response hit the {settings.llm_max_tokens} token cap before the "
            f"JSON closed — raise QATF_LLM_MAX_TOKENS or ask for fewer clips"
        )
    return parse_response(result.text)
