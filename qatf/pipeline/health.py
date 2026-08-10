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

import math
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
    """One word whose timing cannot be right. `kind` is nonfinite | zero | tiny | long."""

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
        if not (math.isfinite(w.start) and math.isfinite(w.end)):
            # Checked BEFORE the span comparisons, not after: every comparison
            # against NaN is False, so an unchecked NaN falls through all three
            # branches and reports as no defect at all. `edits.diff` learned
            # this first; see the trust-boundary table in CLAUDE.md.
            kind = "nonfinite"
        else:
            span = w.end - w.start
            if span <= 0.0:
                kind = "zero"
            elif span < 0.04:
                kind = "tiny"
            elif span > max_span:
                kind = "long"
            else:
                continue
        out.append(TimingDefect(index=i, kind=kind, text=w.text,
                                start=w.start, end=w.end))
    return out


def repair(words: list[Word], min_run: int = MIN_REPEAT_RUN
           ) -> tuple[list[Word], int]:
    """Blank the duplicates in each repetition run. Returns (words, blanked).

    Blanking rather than deleting is the whole trick. Removing the tokens would
    change the word count, which breaks `edits.py`'s position-keyed overlay and
    the `PUT /jobs/{id}/transcript` contract that refuses any submission
    changing the count. A blanked token keeps its index and both timings and
    simply renders as nothing — `captions.group_words` skips it.

    Applied on read, never written to the cache: the cache has to keep saying
    what Whisper actually produced, or the numbers in docs/quality.md stop being
    reproducible."""
    blanked = 0
    for run in find_repetitions(words, min_run):
        for k in range(run.index + 1, run.index + run.count):
            if words[k].text:
                words[k].text = ""
                blanked += 1
    return words, blanked


def warnings(words: list[Word]) -> list[str]:
    """Human-readable flags for defects that are NOT repaired — timing only.

    Repetition runs are deliberately absent here. `repair` already fixes them,
    and the caller logs how many tokens it blanked — a line in `warnings` too
    would announce the same defect twice. It would also be dead: `repair`
    blanks in place, so by the time anything downstream calls `warnings` the
    duplicate tokens are already gone, and a repetition scan over what's left
    would find nothing (or, worse, find the blanks themselves as a fresh
    "run" of empty tokens — a phantom defect this function must not invent).

    Timing damage IS surfaced here, because nothing fixes it. The operator has
    to go and look at the clip edges. Silence would be the wrong answer: a 15s
    word is also a 15s caption, and it will not announce itself in a diff.

    `nonfinite` is folded into the same near-zero-duration line as `zero`/`tiny`
    rather than given its own paragraph: all three mean `end - start` cannot be
    trusted as a real span (NaN/inf make the subtraction meaningless the same
    way a zero or 20ms span does), and the operator's next move is identical for
    all three — go look at that timestamp in the clip. A dropped kind would be
    the actual bug (see the nonfinite comment in find_timing_defects); grouping
    it is a display choice, not an omission."""
    out: list[str] = []
    for d in find_timing_defects(words):
        if d.kind == "long":
            out.append(f"word {d.index} ({d.text!r}) spans "
                       f"{d.end - d.start:.1f}s at {d.start:.1f}s — the caption "
                       f"holds that long and snap may anchor a cut on it")
    bad = [d for d in find_timing_defects(words) if d.kind in ("zero", "tiny", "nonfinite")]
    if bad:
        where = ", ".join(f"{d.start:.1f}s" for d in bad[:5])
        out.append(f"{len(bad)} word(s) have a zero, near-zero or non-finite "
                   f"duration ({where}{', …' if len(bad) > 5 else ''})")
    return out
