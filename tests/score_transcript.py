"""Score a transcript for the defects stage 2 actually produces.

    python tests/score_transcript.py <words.json> [--audio W.wav] [--baseline B.json]

Reference-free by design. A hand-corrected transcript is the gold standard and
is being produced separately, but it cannot gate day-to-day tuning: every metric
here runs in seconds on any transcript and catches a defect that was measured by
hand on real material.

The point of the coverage and word-count metrics is that they are the GUARD on
the others. The specific way a hallucination fix fails is by deleting real
speech, which improves every other number on the page.

Everything is reported whole-file and past 300s separately. See the measurement
trap in docs/quality.md: a 70s slice starting where a seed was applied once
reported 14 errors going to 0, and the same audio 6.6 minutes in had them back.

The `--audio`/`--baseline` flags and the argv entry point above are Tasks 5-6
(an audio-coverage metric, then the CLI). Until those land this module is a
library — `load_terms`, `load_words`, `score` — called directly, the same
kind of forward reference `pipeline/health.py`'s own docstring makes to this
file.
"""

from __future__ import annotations

import json
from pathlib import Path

from _harness import ROOT  # noqa: F401  — puts the project root on sys.path

from qatf.core.types import Word, words_from_dicts
from qatf.pipeline import health

#: Errors before this point can be flattered by a seed that decays. Reported
#: separately for exactly that reason.
DECAY_SECONDS = 300.0


def load_terms(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["terms"]


def load_words(path: Path) -> list[Word]:
    return words_from_dicts(json.loads(path.read_text(encoding="utf-8"))["words"])


def _count_expected(text: list[str], expected: str) -> int:
    """How many times `expected` occurs in `text`.

    A single-token expected value (`"PHP"`) is matched token for token. A
    multi-word expected value (`"جافا سكريبت"`) can never equal one token —
    Whisper emits one token per word — so it has to be matched against
    consecutive windows of `text` instead. Branching on whether `expected`
    contains a space, rather than summing a single-token pass `or` a window
    pass (as an earlier draft did), means exactly one of the two ever runs:
    there is no case where a real occurrence could be counted by both and no
    case where an `or` silently discards a nonzero window count because a
    stray single-token match, unrelated to this term, made the left side
    truthy.
    """
    parts = expected.split(" ")
    width = len(parts)
    if width == 1:
        return sum(1 for x in text if x == expected)
    return sum(1 for i in range(len(text) - width + 1) if text[i:i + width] == parts)


def score(words: list[Word], terms: list[dict], since: float = 0.0) -> dict:
    """Every metric for the words at or after `since`."""
    sel = [w for w in words if w.start >= since]
    span = (sel[-1].end - sel[0].start) if sel else 0.0
    runs = health.find_repetitions(sel)
    defects = health.find_timing_defects(sel)
    text = [w.text.strip().strip(".،") for w in sel]

    term_rows = []
    for t in terms:
        right = _count_expected(text, t["expected"])
        wrong = sum(1 for x in text if x in t["wrong"])
        term_rows.append({"expected": t["expected"], "right": right, "wrong": wrong,
                          "invented": max(0, right - t["baseline_total"])})

    return {
        "words": len(sel),
        "span_s": round(span, 1),
        "wpm": round(60 * len(sel) / span, 1) if span else 0.0,
        "longest_run": max((r.count for r in runs), default=0),
        "looped_tokens": sum(r.count - 1 for r in runs),
        # Kept alongside zero/tiny/long rather than folded into one of them —
        # dropping this key is exactly the bug CLAUDE.md flags: a NaN bound
        # makes every span comparison False, so a nonfinite timing that isn't
        # its own counted kind is a defect class no regression gate can ever
        # fail on. See TimingDefect.kind in pipeline/health.py.
        "nonfinite_timings": sum(1 for d in defects if d.kind == "nonfinite"),
        "zero_timings": sum(1 for d in defects if d.kind == "zero"),
        "tiny_timings": sum(1 for d in defects if d.kind == "tiny"),
        "long_timings": sum(1 for d in defects if d.kind == "long"),
        "max_word_span": round(max((d.end - d.start for d in defects
                                    if d.kind == "long"), default=0.0), 2),
        "terms": term_rows,
        "terms_right": sum(r["right"] for r in term_rows),
        "terms_wrong": sum(r["wrong"] for r in term_rows),
        "terms_invented": sum(r["invented"] for r in term_rows),
    }
