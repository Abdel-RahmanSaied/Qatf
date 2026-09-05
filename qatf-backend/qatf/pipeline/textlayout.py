"""Text measurement and caption-line layout.

Pure. No ASS knowledge, no ffmpeg, no timing — it answers "how wide is this
word" and "where does each word sit on the line", and nothing else.

It exists because the pill caption style positions every word absolutely. That
is not a stylistic choice: libass starts a new bidi run wherever an override tag
causes an actual style change, so highlighting one word inside a multi-word
`Dialogue` line scrambles RTL word order (measured — see captions.py). Giving
each word its own event removes the multi-word run entirely, and the price is
that libass stops laying out the line for us.
"""

from __future__ import annotations

#: Codepoint ranges whose script is written right-to-left. Moved here from
#: captions.py: direction is a property of text, and the layout solver is the
#: thing that acts on it. `captions.is_rtl` re-exports so existing callers and
#: the checks written against them keep working.
_RTL_RANGES = (
    (0x0590, 0x05FF),   # Hebrew
    (0x0600, 0x06FF),   # Arabic
    (0x0700, 0x074F),   # Syriac
    (0x0750, 0x077F),   # Arabic Supplement
    (0x0780, 0x07BF),   # Thaana
    (0x07C0, 0x07FF),   # N'Ko
    (0x0800, 0x083F),   # Samaritan
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB1D, 0xFB4F),   # Hebrew presentation forms
    (0xFB50, 0xFDFF),   # Arabic presentation forms-A
    (0xFE70, 0xFEFF),   # Arabic presentation forms-B
)


def is_rtl(text: str) -> bool:
    """True if the text contains any right-to-left character.

    One RTL character is enough: a mixed line still gets bidi-reordered, so it
    hits the same layout question as a fully RTL one."""
    return any(any(lo <= ord(ch) <= hi for lo, hi in _RTL_RANGES) for ch in text)


def visual_order(words: list[str], base_rtl: bool) -> list[int]:
    """Indices of `words` in the order they appear on screen, left to right.

    A DELIBERATE SIMPLIFICATION of the Unicode Bidi Algorithm, not an
    implementation of it. Words are grouped into maximal same-direction runs;
    the runs are ordered by the base direction, and a run whose own direction is
    RTL has its words reversed inside it.

    That is correct for one level of embedding, which covers the real cases: an
    all-Arabic line, an all-Latin line, and a Latin phrase or number quoted
    inside Arabic. Deeply nested mixed-direction text is out of scope, and is
    documented as such rather than left looking solved — a half-implemented bidi
    fails by being subtly wrong in a way only a native reader notices.

    Reversing every word instead would be the obvious shortcut and is wrong: it
    reads an embedded "Python is" back to front."""
    if not words:
        return []

    runs: list[tuple[bool, list[int]]] = []
    for i, w in enumerate(words):
        rtl = is_rtl(w)
        if runs and runs[-1][0] == rtl:
            runs[-1][1].append(i)
        else:
            runs.append((rtl, [i]))

    if base_rtl:
        runs.reverse()

    out: list[int] = []
    for rtl, idx in runs:
        out.extend(reversed(idx) if rtl else idx)
    return out
