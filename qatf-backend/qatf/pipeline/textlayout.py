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

import functools
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

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


#: Seconds to wait for fontconfig. Mirrors captions.FC_LIST_TIMEOUT — this sits
#: on the render path, where a hang is worse than a skipped style.
FC_MATCH_TIMEOUT = 5.0


@runtime_checkable
class Measurer(Protocol):
    """Anything that can answer how wide a shaped string is, in pixels.

    A protocol rather than a class so the checks can inject a fake and run on a
    host with no uharfbuzz. That is not a convenience: the fallback path is only
    testable if the suite can simulate the dependency being absent."""

    line_height: float

    def advance(self, text: str) -> float: ...


@functools.lru_cache(maxsize=32)
def font_file(family: str) -> Path | None:
    """The file fontconfig resolves this family to, or None.

    None means "cannot tell", never "missing" — the same rule
    `captions.installed_fonts` follows. fc-match always returns SOMETHING (that
    is the silent-fallback behaviour being worked around), so the caller must
    still confirm the family it got is the family it asked for; that check lives
    in `load_measurer`."""
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}\t%{family}", family],
            capture_output=True, text=True, timeout=FC_MATCH_TIMEOUT)
    except (OSError, subprocess.SubprocessError, ValueError):
        # ValueError: `family` is caller-supplied (see CLAUDE.md's trust-boundary
        # table) and a null byte in it makes both os.fsencode (POSIX) and
        # CreateProcess (Windows) refuse the argument with "embedded null
        # character/byte" — a ValueError, not an OSError. Same "cannot tell,
        # never missing" rule as a missing or hanging fc-match.
        return None
    if out.returncode != 0 or "\t" not in out.stdout:
        return None
    path_s, families = out.stdout.split("\t", 1)
    # fc-match substitutes silently. If it handed back a different family, we
    # would be measuring one face and libass would draw another — every capsule
    # would sit off its word. Refuse rather than measure the wrong font.
    wanted = family.casefold()
    if not any(a.strip().casefold() == wanted for a in families.split(",")):
        return None
    p = Path(path_s.strip())
    return p if p.is_file() else None


class _HarfBuzzMeasurer:
    """Advances from HarfBuzz — the engine libass itself shapes with.

    Scale is set to the face's units-per-em and converted to pixels in float,
    rather than scaling HarfBuzz straight to whole pixels. Integer-truncating
    each word's advance accumulates error along the line, and by the last word
    the capsule sits visibly off its word."""

    def __init__(self, path: Path, size: float) -> None:
        import uharfbuzz as hb

        self._hb = hb
        blob = hb.Blob.from_file_path(str(path))
        face = hb.Face(blob)
        self._font = hb.Font(face)
        self._upem = face.upem
        self._font.scale = (self._upem, self._upem)
        self._px = size / self._upem
        self.line_height = self._measure_line_height() * self._px

    def _measure_line_height(self) -> float:
        for name in ("get_font_h_extents", "get_font_extents"):
            fn = getattr(self._font, name, None)
            if fn is None:
                continue
            try:
                ext = fn() if name == "get_font_h_extents" else fn("ltr")
            except Exception:                              # noqa: BLE001
                continue
            asc = getattr(ext, "ascender", None)
            desc = getattr(ext, "descender", None)
            if asc is not None and desc is not None:
                return float(asc) - float(desc)
        # Last resort: the em box. Slightly tight for faces with tall
        # ascenders, and only reached on a uharfbuzz that exposes neither
        # extents call — which the probe in this task's Step 2 checks for.
        return float(self._upem)

    def advance(self, text: str) -> float:
        buf = self._hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        self._hb.shape(self._font, buf)
        return sum(p.x_advance for p in buf.glyph_positions) * self._px


def load_measurer(family: str, size: float) -> Measurer | None:
    """A measurer for this family at this size, or None if one is not possible.

    **Returns None, never raises.** A missing wheel or an unresolvable font must
    degrade to the `pop` caption style, not fail a job that has already spent
    twenty minutes in stage 2. That is the `font_warning` policy, not the
    `--device cuda` policy: there is a correct alternative here to fall back to."""
    path = font_file(family)
    if path is None:
        return None
    try:
        return _HarfBuzzMeasurer(path, size)
    except ImportError:
        return None                      # uharfbuzz not installed
    except Exception:                    # noqa: BLE001 — a corrupt or exotic face
        return None
