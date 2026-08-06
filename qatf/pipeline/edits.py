"""Stage 2c — per-word transcript corrections.

`fixups.py` handles the systematic errors: a term the decoder always mishears the
same way, fixed once by value and fixed forever. It cannot touch the other kind —
a word misheard *once*, in one place, where the identical string is correct
everywhere else. On Egyptian Arabic that is most of what is left after the
vocabulary has done its work: Whisper writing `من` where the speaker said `مين`
is unfixable by substitution, because `من` is one of the most common words in the
language and correct almost everywhere else it appears.

This module fixes exactly that class, by position rather than by value.

Two properties, both load-bearing:

  - **Text only.** `Word.start` and `Word.end` are never written here, and
    `diff()` refuses a submission that changes either. A correction can change
    what a caption reads and can never move a cut — so the core invariant is
    enforced by the contract rather than by discipline. It also means a
    correction can be made at any point after stage 2 without re-planning: the
    cut points are provably identical.
  - **An overlay, not a rewrite.** Corrections live beside the transcript cache,
    never inside it. Re-transcribing must not silently discard them, and the
    cache has to keep saying what Whisper *actually* produced — the moment a
    corrected transcript is indistinguishable from a raw one, every measurement
    in docs/quality.md stops meaning anything.

Corrections are keyed by word index and carry the text they replaced. An index
alone is not safe: re-transcribing with a different Whisper size, or toggling
`--denoise`, shifts every position, and correction #1247 would land silently on
an unrelated word. With `was` recorded, a shifted overlay goes **stale** and is
reported rather than applied.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.errors import TranscriptStructureChanged
from ..core.types import Word

FILENAME = "word-edits.json"

#: Timestamps round-trip through JSON as floats. Compare with a tolerance far
#: below one frame at any sane rate, so re-serialisation never reads as an edit
#: while a genuine retiming still does.
TIMING_EPSILON = 1e-6


@dataclass
class Edit:
    """One correction. `was` is the drift guard, not decoration."""

    index: int
    was: str
    text: str


def path(work: str | Path) -> Path:
    """Beside the transcript cache, deliberately not inside it."""
    return Path(work) / FILENAME


def to_dicts(edits: list[Edit]) -> list[dict]:
    return [asdict(e) for e in edits]


def from_dicts(data: object) -> list[Edit]:
    """Tolerant of both the wrapped and bare forms, since this file is meant to
    be hand-edited."""
    if isinstance(data, dict):
        data = data.get("edits", [])
    if not isinstance(data, list):
        return []
    out: list[Edit] = []
    for item in data:
        try:
            out.append(Edit(index=int(item["index"]),
                            was=str(item.get("was", "")),
                            text=str(item["text"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def load(file: str | Path) -> list[Edit]:
    target = Path(file)
    if not target.is_file():
        return []
    try:
        return from_dicts(json.loads(target.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return []


def save(file: str | Path, edits: list[Edit]) -> None:
    """Write the overlay, or remove it when there is nothing left to record —
    an empty overlay and no overlay must not behave differently."""
    target = Path(file)
    if not edits:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"edits": to_dicts(edits)}, indent=2, ensure_ascii=False),
        encoding="utf-8")


def diff(baseline: list[Word], submitted: list[Word]) -> list[Edit]:
    """Derive an overlay from a whole submitted word list.

    This is where the invariant is enforced. Anything other than `text` differing
    is refused, so there is no code path — API, CLI or hand-edited file — by
    which a caller can move a cut point while claiming to fix a spelling."""
    if len(submitted) != len(baseline):
        raise TranscriptStructureChanged(
            f"transcript has {len(baseline)} words, got {len(submitted)} — "
            "word text may be corrected, but words cannot be added or removed. "
            "To split one word into two, put both in that word's text.")
    for i, (base, sub) in enumerate(zip(baseline, submitted, strict=True)):
        # NaN defeats the comparison below — every comparison against NaN is
        # False, so a NaN timing would sail through as "unchanged". Check
        # finiteness first rather than relying on the difference.
        if not (math.isfinite(sub.start) and math.isfinite(sub.end)):
            raise TranscriptStructureChanged(
                f"word {i} ({base.text!r}) has a non-finite timing "
                f"({sub.start}-{sub.end}). Word timings come from the audio.")
        if (abs(base.start - sub.start) > TIMING_EPSILON
                or abs(base.end - sub.end) > TIMING_EPSILON):
            raise TranscriptStructureChanged(
                f"word {i} ({base.text!r}) changed timing "
                f"{base.start:.3f}-{base.end:.3f} -> {sub.start:.3f}-{sub.end:.3f}. "
                "Word timings come from the audio and are not editable — they are "
                "what every cut point is snapped to.")
    return [Edit(index=i, was=base.text, text=sub.text)
            for i, (base, sub) in enumerate(zip(baseline, submitted, strict=True))
            if base.text != sub.text]


def apply(words: list[Word], edits: list[Edit]) -> tuple[list[Word], int, list[Edit]]:
    """Overlay corrections onto the words. Returns (words, applied, stale).

    A correction whose `was` no longer matches the word at that index is **not**
    applied. That is the transcript having moved underneath it — a re-transcribe
    at a different Whisper size, or `--denoise` toggled — and applying it anyway
    would corrupt an unrelated word silently. Stale corrections come back so a
    caller can say so rather than swallowing them."""
    if not edits:
        return words, 0, []
    applied = 0
    stale: list[Edit] = []
    for edit in edits:
        if not 0 <= edit.index < len(words):
            stale.append(edit)
            continue
        word = words[edit.index]
        if edit.was and word.text != edit.was and word.text != edit.text:
            stale.append(edit)
            continue
        if word.text != edit.text:
            word.text = edit.text
            applied += 1
    return words, applied, stale
