"""Stage 4 — snap cut points onto real word boundaries.

Deterministic. No model, ever. This is the stage that makes the whole design
work: semantic boundaries come from the model, acoustic boundaries come from
Whisper, and they meet here.

If a clip opens or closes mid-syllable, the bug is in this file. If the clip is
boring, the bug is in the stage 3 prompt. Diagnose them separately.
"""

from __future__ import annotations

from ..core.constants import SNAP_LEAD, SNAP_TAIL
from ..core.types import Clip, Word


def snap(clip: Clip, words: list[Word],
         lead: float = SNAP_LEAD, tail: float = SNAP_TAIL) -> Clip:
    """Move the boundaries onto the nearest real word start/end.

    Applies to hand-edited plans too. A human typing "start": 20.0 is making the
    same kind of semantic guess the model makes."""
    if not words:
        return clip

    starts = [w.start for w in words]
    ends = [w.end for w in words]

    i = min(range(len(starts)), key=lambda k: abs(starts[k] - clip.start))
    j = min(range(len(ends)), key=lambda k: abs(ends[k] - clip.end))
    if j < i:
        j = i

    clip.start = max(0.0, starts[i] - lead)
    clip.end = ends[j] + tail
    return clip


def words_in(clip: Clip, words: list[Word]) -> list[Word]:
    """Words fully contained in the clip, for caption generation."""
    return [w for w in words if w.start >= clip.start - 0.01 and w.end <= clip.end + 0.01]


def within_duration(clips: list[Clip], lo: int, hi: int) -> list[Clip]:
    """Drop clips the snap pushed well outside the requested range. The margins
    are loose on purpose — snapping legitimately moves a boundary by a word."""
    return [c for c in clips if lo * 0.6 <= c.duration <= hi * 1.4]
