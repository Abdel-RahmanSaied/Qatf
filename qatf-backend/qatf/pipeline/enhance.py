"""Stage 2f — the model proposes per-word corrections.

The THIRD model call in this pipeline, and the boundary it must not cross is the
same one stages 1, 4 and 5 respect: this changes `Word.text` and nothing else.
It can change what a caption reads; it can never move a cut. Acoustic timing
still comes from Whisper alone.

Two things make that true by construction rather than by instruction.

**The model never returns a transcript.** It returns `(index, was, text)`
triples. A model asked to "fix this transcript" merges tokens, splits `A I` into
`AI` and drops fillers — each of which changes the word count, which
`edits.diff` refuses outright. A positional contract cannot express those.

**The replacement must be a listed term or empty.** The model sees only text, so
it is guessing from context rather than correcting transcription — `من` -> `مين`
is unfixable by rule because `من` is one of the most common words in Arabic, and
a model will happily "fix" a correct word into a plausible wrong one.
Restricting replacements to the vocabulary makes the agreed scope mechanically
true instead of something the prompt asks for and the model may ignore.

Deleting a filler is still possible: `health.repair` already blanks looped
tokens rather than removing them and `captions.group_words` skips `if not
w.text`, so `""` is a legal correction and the word count survives.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.config import get_settings
from ..core.errors import ModelResponseError
from ..core.types import Word
from ..core.utils import log
from ..llm import build_provider

#: Where the shipped term list lives, relative to the package root. Absent from a
#: wheel that did not bundle it, which is why every reader treats a missing file
#: as an empty list rather than an error.
VOCAB_FILE = Path(__file__).resolve().parents[2] / "prompts" / "ar-tech.txt"


@dataclass
class Suggestion:
    """One proposed correction.

    `was` is the drift guard, exactly as in `edits.Edit` — and here it does more
    work, because it is what makes a miscounted index harmless."""

    index: int
    was: str
    text: str
    why: str = ""


def to_dicts(items: list[Suggestion]) -> list[dict]:
    return [asdict(s) for s in items]


def load_vocab(path: Path | None = None) -> list[str]:
    """Terms from the shipped list, whitespace-separated.

    A missing file is an EMPTY LIST, not an error. `prompts/` is not bundled in
    every install, and an endpoint that 500s because an optional asset is absent
    is worse than one that simply finds fewer corrections."""
    p = VOCAB_FILE if path is None else path
    try:
        return p.read_text(encoding="utf-8").split()
    except OSError:
        return []


PROMPT = """You are proofreading an automatic transcript of Arabic speech.

Each line below is one word, numbered. Some words were misheard by the speech
recogniser and came out either as a near-miss of a technical term, or as
something that is not a word at all.

THE ONLY corrections you may propose:
1. a word that is a near-miss of one of the TERMS listed below -> that exact term
2. a word that is not a word at all (a stray syllable, a decoder artefact) -> an
   empty string, which deletes it

Do NOT correct ordinary Arabic words. You cannot hear the audio, so you would be
guessing from context, and a confident wrong "fix" is worse than a missed error.
If a word is a normal Arabic word used normally, leave it alone. Returning
nothing at all is a perfectly good answer.

Return ONLY JSON, no prose, no markdown fences:
{{
  "suggestions": [
    {{"index": 412,
      "was": "the word exactly as it appears below",
      "text": "the replacement term, or an empty string to delete it",
      "why": "a few words"}}
  ]
}}

Echo `was` exactly as printed. It is checked against the transcript, and any
mismatch discards that suggestion.

TERMS:
{terms}

TRANSCRIPT:
{transcript}"""

SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "was": {"type": "string"},
                    "text": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["index", "was", "text", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def build_prompt(words: list[Word], terms: list[str]) -> str:
    """Numbered tokens plus the term list.

    Blank tokens are skipped: `health.repair` leaves them in the list to keep the
    overlay aligned, and offering the model a blank line to "correct" invites a
    suggestion that can only be a no-op."""
    numbered = "\n".join(f"{i}\t{w.text}" for i, w in enumerate(words) if w.text)
    return PROMPT.format(terms=" ".join(terms), transcript=numbered)


def parse_suggestions(raw: str) -> list[Suggestion]:
    """Provider text -> suggestions.

    Defensive for the same reason `select.parse_response` is: the json_object
    tier guarantees valid JSON and nothing whatsoever about its shape."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    if not raw:
        raise ModelResponseError("model returned an empty response")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelResponseError(
            f"model did not return valid JSON:\n{raw[:400]}") from exc
    if isinstance(data, dict):
        for key in ("suggestions", "edits", "corrections", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ModelResponseError(
                f"expected a 'suggestions' array, got keys {sorted(data)[:6]}")
    if not isinstance(data, list):
        raise ModelResponseError(
            f"expected a JSON array of suggestions, got a bare "
            f"{type(data).__name__}:\n{raw[:400]}")
    out = []
    for item in data:
        try:
            out.append(Suggestion(index=int(item["index"]), was=str(item["was"]),
                                  text=str(item["text"]),
                                  why=str(item.get("why", ""))))
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelResponseError(
                f"malformed suggestion in response: {item!r}") from exc
    return out


def validate(items: list[Suggestion], words: list[Word],
             terms: list[str]) -> tuple[list[Suggestion], list[str]]:
    """Keep only what the agreed scope allows. Returns `(kept, reasons)`.

    THIS is where the scope lives — in the server, not in the prompt. A model
    that ignores its instructions cannot get an out-of-vocabulary "correction"
    past this function however persuasive its `why` was. The prompt asks; this
    enforces.

    The `was` check is the other half. Measurements on this project showed models
    copying transcript labels rather than computing from them, and the same class
    of failure produces a wrong index here. Matching on `(index, was)` turns a
    hallucinated index into a dropped suggestion instead of a silently corrupted
    unrelated word."""
    allowed = set(terms)
    # How often each token occurs, for the deletion gate below.
    seen = Counter(w.text for w in words if w.text)
    kept: list[Suggestion] = []
    dropped: list[str] = []
    for s in items:
        if not 0 <= s.index < len(words):
            dropped.append(f"index {s.index} is out of range")
        elif words[s.index].text != s.was:
            dropped.append(
                f"index {s.index} claims {s.was!r} but the transcript has "
                f"{words[s.index].text!r} — miscounted index")
        elif s.text == s.was:
            dropped.append(f"index {s.index} is a no-op")
        elif s.text != "" and s.text not in allowed:
            dropped.append(
                f"index {s.index} proposes {s.text!r}, which is not a listed "
                f"term — out of scope")
        elif s.text == "" and seen[s.was] > 1:
            # MEASURED: against the real Arabic transcript the model proposed
            # deleting `لك` ("for you"), a word used SIX times. Bounding
            # replacements to the term list left "" unconstrained, so a delete
            # could take any word at all — the one hole in the scope rule.
            #
            # A decoder artefact is by nature a one-off; a real word recurs.
            # This does not make deletion safe — a real word used once can still
            # go — which is why the pass is reviewed rather than applied.
            dropped.append(
                f"index {s.index} would delete {s.was!r}, which occurs "
                f"{seen[s.was]} times — a recurring token is a real word")
        else:
            kept.append(s)
    for reason in dropped:
        log(f"      dropped suggestion: {reason}")
    return kept, dropped


def suggest(words: list[Word], terms: list[str],
            settings=None) -> tuple[list[Suggestion], list[str]]:
    """Ask the configured provider for corrections, then refuse most of them.

    Uses the SAME provider as stage 3 — one place to configure a model, and the
    settings endpoint already governs it.

    Returns `(kept, dropped_reasons)` so a caller can report how many were
    refused. A model that is mostly being refused should be visible rather than
    quiet: it means the vocabulary is wrong, or the model is not up to this."""
    settings = settings or get_settings()
    provider = build_provider(
        settings.llm_provider, model=settings.llm_model,
        base_url=settings.llm_base_url, effort=settings.llm_effort,
        timeout=settings.llm_timeout,
    )
    result = provider.complete_json(build_prompt(words, terms), SUGGEST_SCHEMA,
                                    max_tokens=settings.llm_max_tokens)
    if result.truncated:
        raise ModelResponseError(
            f"response hit the {settings.llm_max_tokens} token cap before the "
            f"JSON closed — raise QATF_LLM_MAX_TOKENS")
    return validate(parse_suggestions(result.text), words, terms)
