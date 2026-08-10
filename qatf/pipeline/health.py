"""Transcript defect detection and repair.

Its own module for the same reason `fixups.py` and `edits.py` are: one
text-correction concern each. This one owns damage the DECODER did, as opposed
to words it heard wrong — a repetition loop and a degenerate timestamp are not
spelling mistakes and no substitution can reach them.

Two callers, deliberately: `tests/score_transcript.py` grades a transcript with
these, and the read path logs warnings with the same functions. Detection stated
once means the scorer cannot disagree with the runtime about what a defect is.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Word

#: A run must reach this length before it is called a loop. People genuinely
#: repeat a word twice for emphasis, and occasionally three times. Seven
#: identical tokens inside 1.9s — the measured case — is a decoder loop.
MIN_REPEAT_RUN = 4

#: Longest plausible single word, in seconds. The measured transcript held seven
#: words over this, one spanning 15.42s; those are alignment failures, and they
#: matter because `snap` anchors cut points on exactly these boundaries.
MAX_WORD_SPAN = 2.0

#: Trailing punctuation, stripped before comparing tokens — `x` and `x.` are the
#: same word repeated. Same set as `fixups._TRAILING`, for the same reason.
_TRAILING = ".,!?،؛:…"


@dataclass
class RepetitionRun:
    """A stretch of identical consecutive tokens."""

    index: int          # position of the FIRST token in the run
    token: str
    count: int
    start: float
    end: float


@dataclass
class TimingDefect:
    """One word whose timing cannot be right. `kind` is zero | long."""

    index: int
    kind: str
    text: str
    start: float
    end: float


def _key(token: str) -> str:
    return token.rstrip(_TRAILING)


def find_repetitions(words: list[Word], min_run: int = MIN_REPEAT_RUN
                     ) -> list[RepetitionRun]:
    """Runs of `min_run` or more identical consecutive tokens."""
    runs: list[RepetitionRun] = []
    i = 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and _key(words[j + 1].text) == _key(words[i].text):
            j += 1
        if j - i + 1 >= min_run:
            runs.append(RepetitionRun(index=i, token=words[i].text,
                                      count=j - i + 1,
                                      start=words[i].start, end=words[j].end))
        i = j + 1
    return runs


def find_timing_defects(words: list[Word], max_span: float = MAX_WORD_SPAN
                        ) -> list[TimingDefect]:
    """Words whose start/end cannot be a real acoustic boundary.

    Reported, never corrected. A timing this module invented would be
    indistinguishable downstream from one Whisper measured, and `snap` cannot
    tell them apart."""
    out: list[TimingDefect] = []
    for i, w in enumerate(words):
        span = w.end - w.start
        if span <= 0.0:
            kind = "zero"
        elif span > max_span:
            kind = "long"
        else:
            continue
        out.append(TimingDefect(index=i, kind=kind, text=w.text,
                                start=w.start, end=w.end))
    return out
