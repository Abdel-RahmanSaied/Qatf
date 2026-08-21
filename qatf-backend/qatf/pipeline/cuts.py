"""Stage 4 — snap cut points onto real word boundaries.

Deterministic. No model, ever. This is the stage that makes the whole design
work: semantic boundaries come from the model, acoustic boundaries come from
Whisper, and they meet here.

If a clip opens or closes mid-syllable, the bug is in this file. If the clip is
boring, the bug is in the stage 3 prompt. Diagnose them separately.
"""

from __future__ import annotations

from dataclasses import replace

from ..core.constants import DURATION_SLACK, SNAP_LEAD, SNAP_TAIL, SNAP_TAIL_BOUNDED
from ..core.types import Clip, Word
from ..core.utils import log

#: Float tolerance for recognising a boundary this function itself produced.
_EPS = 1e-6


def tail_for(timing_source: str | None) -> float:
    """How far past a word's end a cut may close, given where the end came from.

    The one place stage 4 is allowed to care about the transcript's provenance,
    and it cares about exactly one thing: whether `Word.end` was MEASURED or
    merely BOUNDED.

    Whisper measures both edges against the audio, so there is real silence
    after a word's end and `SNAP_TAIL` extends into it. A caption track gives
    one instant per token and no end at all, so `end` is the next token's start
    — an upper bound. Extending past an upper bound does not reach silence, it
    reaches the next word, and the cut lands 0.35s after that word has begun.

    Defaulting to `SNAP_TAIL` for an unknown source is the safe direction: rows
    written before `timing_source` existed came from Whisper, and read back
    NULL."""
    return SNAP_TAIL_BOUNDED if timing_source == "captions" else SNAP_TAIL


def snap(clip: Clip, words: list[Word],
         lead: float = SNAP_LEAD, tail: float = SNAP_TAIL) -> Clip:
    """Move the boundaries onto the nearest real word start/end.

    Applies to hand-edited plans too. A human typing "start": 20.0 is making the
    same kind of semantic guess the model makes.

    Returns a NEW Clip. It used to rewrite the one passed in and return the same
    object, which is the kind of aliasing that hides its own bugs: the first
    test written for the drift below compared `before` against `after` and saw
    no difference, because they were one object.

    **Idempotent.** Snapping an already-snapped clip returns it unchanged, and
    that is load-bearing rather than tidy: `--plan` and `PUT /jobs/{id}/plan`
    re-snap by default, so the documented hand-edit round trip runs this
    repeatedly over its own output. Without the fixed-point check below, each
    pass moved the end onto the NEXT word — `tail` is 0.35s while Arabic word
    ends here average ~0.45s apart, so the search landed past the boundary it
    had just created. Measured before the fix: five round trips grew one clip
    from 56.70s to 59.21s, silently, and a clip edited three times could cross
    a platform limit it was chosen to sit under."""
    if not words:
        return clip

    starts = [w.start for w in words]
    ends = [w.end for w in words]

    # The values this function emits, so an input that is already one of them is
    # recognised as a fixed point instead of being re-searched. Checking the
    # OUTPUT set rather than shifting the search key by `lead`/`tail` is what
    # keeps first-pass behaviour identical: biasing the search would move which
    # word gets chosen, and opening a cut after the word the model pointed at is
    # exactly the mid-syllable failure this stage exists to prevent.
    snapped_starts = {round(max(0.0, s - lead), 6) for s in starts}
    snapped_ends = {round(e + tail, 6) for e in ends}

    i = min(range(len(starts)), key=lambda k: abs(starts[k] - clip.start))
    j = min(range(len(ends)), key=lambda k: abs(ends[k] - clip.end))
    if j < i:                      # an inverted range collapses, not explodes
        j = i

    new_start = (clip.start if round(clip.start, 6) in snapped_starts
                 else max(0.0, starts[i] - lead))
    new_end = (clip.end if round(clip.end, 6) in snapped_ends
               else ends[j] + tail)
    return replace(clip, start=new_start, end=max(new_start, new_end))


def words_in(clip: Clip, words: list[Word]) -> list[Word]:
    """Words fully contained in the clip, for caption generation."""
    return [w for w in words if w.start >= clip.start - 0.01 and w.end <= clip.end + 0.01]


def classify_duration(clip: Clip, lo: int, hi: int) -> str:
    """How one clip sits against the requested range: `""`, `"short"` or `"long"`.

    **This classifies. It does not filter.** Clips outside the range stay in the
    plan and get rendered; the label travels with them so a front end can mark
    them and the operator decides. That is a deliberate reversal: this function
    used to be `within_duration`, which DROPPED anything out of range, and the
    reasoning for dropping was sound right up until it met a real transcript.
    Stage 3 kept proposing 24s clips against a `min_len` of 30 — six of eight on
    the run that settled it — and every one of them was a perfectly good 24s
    short, discarded unseen because it missed a number by four seconds. Refusing
    to render a clip is a bigger decision than the flag that produced it.

    The margin is DURATION_SLACK seconds, not a percentage. It used to be
    `lo * 0.6 <= d <= hi * 1.4`, which scales with the request and stops meaning
    anything at the top: `--max-len 52` admitted 72.8s. A proportional margin
    cannot express "a little"; an absolute one can, and the amount snapping can
    actually add is absolute — `SNAP_LEAD + SNAP_TAIL` plus at most a word of
    boundary movement.

    The slack still belongs here even though nothing is dropped. Without it a
    52.4s clip against `--max-len 52` reads as the model overrunning when it is
    really snapping having nudged the boundary onto a word end. The flag has to
    mean "the MODEL missed the range", or nobody will trust it.
    """
    if clip.duration < lo - DURATION_SLACK:
        return "short"
    if clip.duration > hi + DURATION_SLACK:
        return "long"
    return ""


def report_durations(clips: list[Clip], lo: int, hi: int) -> list[Clip]:
    """Log every clip that misses the range, and hand back the ones that did.

    Nothing is removed — the return value is for the caller's own message, not
    a filtered plan. A plan that quietly contains six clips shorter than the
    `--min-len` the operator typed is still something they must be told about;
    the change is that they are told INSTEAD of being overruled."""
    flagged = [c for c in clips if classify_duration(c, lo, hi)]
    for c in flagged:
        log(f"      {classify_duration(c, lo, hi):>5}: {c.duration:.1f}s clip "
            f"(asked for {lo}-{hi}s ±{DURATION_SLACK:g}s) — kept: {c.title}")
    return flagged
