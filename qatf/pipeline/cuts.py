"""Stage 4 — snap cut points onto real word boundaries.

Deterministic. No model, ever. This is the stage that makes the whole design
work: semantic boundaries come from the model, acoustic boundaries come from
Whisper, and they meet here.

If a clip opens or closes mid-syllable, the bug is in this file. If the clip is
boring, the bug is in the stage 3 prompt. Diagnose them separately.
"""

from __future__ import annotations

from dataclasses import replace

from ..core.constants import DURATION_SLACK, SNAP_LEAD, SNAP_TAIL
from ..core.types import Clip, Word
from ..core.utils import log

#: Float tolerance for recognising a boundary this function itself produced.
_EPS = 1e-6


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


def within_duration(clips: list[Clip], lo: int, hi: int) -> list[Clip]:
    """Drop clips that fall outside the requested range by more than snapping
    can account for, and say which ones went.

    The margin is DURATION_SLACK seconds, not a percentage. It used to be
    `lo * 0.6 <= d <= hi * 1.4`, which scales with the request and stops meaning
    anything at the top: `--max-len 52` admitted 72.8s. That silently
    contradicts the reason the flag is set to 52 in the first place — snapping
    adds a little, so ask for 52 to land under YouTube Shorts' 60s ceiling — and
    a proportional margin cannot express "a little". An absolute one can, and
    the amount snapping can actually add is absolute: `SNAP_LEAD + SNAP_TAIL`
    plus at most a word of boundary movement.

    A clip well over `hi` is the MODEL overrunning its instruction, not snapping
    drifting, and dropping it is what the flag asked for. It is logged rather
    than dropped quietly: a plan that silently returns 7 clips for `--clips 8`
    reads as the model finding nothing, which is a different problem with a
    different fix."""
    kept, dropped = [], []
    for c in clips:
        (kept if lo - DURATION_SLACK <= c.duration <= hi + DURATION_SLACK
         else dropped).append(c)
    for c in dropped:
        log(f"      dropped {c.duration:.1f}s clip (asked for {lo}-{hi}s "
            f"±{DURATION_SLACK:g}s): {c.title}")
    return kept
