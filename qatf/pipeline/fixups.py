"""Deterministic post-transcription word substitutions.

Some errors survive everything the decoder offers. On the Arabic test material
`بايسون` (Python) persists no matter how the vocabulary is written, because the
speaker's pronunciation genuinely is closer to what Whisper produced — the model
is not wrong about the audio, it is wrong about the spelling convention.

A substitution map fixes that class exactly and permanently. Two properties make
it safe:

  - It touches ONLY `Word.text`. Timestamps are untouched, so `snap` and every
    cut boundary are unaffected — this can change what a caption reads, never
    where a clip begins.
  - It is applied on read, not baked into the transcript cache, so editing the
    map costs nothing. Re-transcribing 12 minutes to fix one spelling would be
    absurd.

It is a last resort, not a first one. Prefer `--vocab`: it fixes the cause and
generalises to words you have not thought of. Reach for a fixup when the
vocabulary demonstrably will not take.
"""

from __future__ import annotations

from pathlib import Path

from ..core.types import Word

#: characters that commonly trail a token and should not defeat a match
_TRAILING = ".,!?،؛:…"


def parse(text: str) -> dict[str, str]:
    """`wrong = right` per line. `#` comments and blanks ignored.

    Also accepts `wrong -> right`, because that is how people write these."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            wrong, _, right = line.partition("->")
        else:
            wrong, sep, right = line.partition("=")
            if not sep:
                continue
        wrong, right = wrong.strip(), right.strip()
        if wrong:
            out[wrong] = right
    return out


def load(path: str | Path) -> dict[str, str]:
    file = Path(path)
    return parse(file.read_text(encoding="utf-8")) if file.is_file() else {}


def apply(words: list[Word], mapping: dict[str, str]) -> tuple[list[Word], int]:
    """Substitute token text in place. Returns (words, replacements made).

    Matching is on the whole token, with trailing punctuation preserved — so
    `بايسون.` becomes `بايثون.` rather than failing to match or losing the stop."""
    if not mapping:
        return words, 0
    count = 0
    for w in words:
        token = w.text
        if token in mapping:
            w.text = mapping[token]
            count += 1
            continue
        stripped = token.rstrip(_TRAILING)
        if stripped and stripped != token and stripped in mapping:
            w.text = mapping[stripped] + token[len(stripped):]
            count += 1
    return words, count
