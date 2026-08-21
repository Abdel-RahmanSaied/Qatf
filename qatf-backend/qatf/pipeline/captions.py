"""Stage 5a — ASS subtitle generation.

Every constant here was paid for by rendering a frame and looking at it. Read
the gotchas in CLAUDE.md before changing any of them, and re-verify visually —
ffprobe reporting correct dimensions is not sufficient.

VERIFIED, AND IT BREAKS ON RTL: libass starts a new bidi run wherever an
override tag causes an actual style change. Per-word highlighting therefore
chops an RTL line into independently-reordered runs, and the visual word order
comes out scrambled — measured by walking the highlight along a line and
watching its horizontal centre move LEFT to RIGHT on Arabic and Hebrew, when RTL
must move right to left. Neither Unicode bidi controls (RLE/PDF, RLM, FSI/PDI)
nor `\\k` karaoke avoid the split.

So `build_ass` does not highlight per word on RTL text. It emits one cue per
caption line instead, which lays out correctly because nothing splits the run.
LTR is unaffected and keeps word-by-word highlighting.
"""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path

from ..core.constants import (
    CAPTION_MAX_CHARS,
    CAPTION_MAX_WORDS,
    DEFAULT_FONT,
    TARGET_H,
    TARGET_W,
)
from ..core.types import Clip, Word
from ..core.utils import ts_ass
from .cuts import words_in

# WrapStyle MUST be 0. With 2 (no wrapping) caption lines overflow the 1080px
# frame and get clipped at both edges — it passes every dimension check.
ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_W}
PlayResY: {TARGET_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{{FONT}},{{SIZE}},&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,{{OUTLINE}},3,2,90,90,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HILITE = r"{\c&H00E0FF&}"   # ASS colour is BGR, not RGB -> this is yellow
RESET = r"{\c&HFFFFFF&}"

MIN_CUE = 0.08
#: how long the last word of a caption line is held after it finishes.
#:
#: ALWAYS CLAMPED against the next line's start — see `build_ass`. Unclamped it
#: put two `Dialogue:` events on screen at once on essentially every hand-off
#: (measured: 34 of 34 consecutive pairs in one real clip, 100%), and libass
#: STACKS simultaneous events, so for ~3 frames the viewer saw the upcoming
#: caption sitting above the one still showing. The .ass file reads as perfectly
#: correct either way, which is why this survived so long.
LAST_WORD_HOLD = 0.12

#: Caption type, sized against how short-form editors actually set captions:
#: bold sans, 55-75pt at 1080x1920, 3-4px stroke. This used to be 82/7.
#:
#: 82px was legible but forced `CAPTION_MAX_CHARS` down to 22 — half the 32-42
#: that reads comfortably — so lines turned over about twice as often as they
#: needed to (35 cues in a 46s clip, roughly one every 1.3s). And a 7px stroke
#: is heavy for Arabic specifically: Naskh and the sans Arabic faces have finer
#: connected strokes than Latin, and the outline thickens the joins until
#: letterforms bleed into one another.
FONT_SIZE = 64
OUTLINE = 4


def group_words(words: list[Word], max_words: int = CAPTION_MAX_WORDS,
                max_chars: int = CAPTION_MAX_CHARS) -> list[list[Word]]:
    """Chunk into caption lines by BOTH word count and character budget.
    Word count alone overflows the frame on long words — 4 x 12-char words at
    the old 82px was wider than 1080px, which is what the character budget is
    for. `CAPTION_MAX_CHARS` tracks `FONT_SIZE`; see the note on it."""
    out: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if not w.text:
            # a token blanked by health.repair — it keeps its index and timings
            # so the overlay stays aligned, but it must not reach a caption
            continue
        projected = sum(len(x.text) for x in cur) + len(cur) + len(w.text)
        if cur and (len(cur) >= max_words or projected > max_chars):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


#: Everything that would end a line, plus NUL. ASS is a line-oriented format —
#: one `Dialogue:` per line — so any of these inside caption text terminates the
#: cue and whatever follows is parsed as a fresh directive.
_STRUCTURAL = str.maketrans({
    "\n": " ", "\r": " ", "\x0b": " ", "\x0c": " ",
    "\u2028": " ", "\u2029": " ",     # LINE / PARAGRAPH SEPARATOR
    "\x00": "",                       # truncates the line for a C parser
})


def escape(token: str) -> str:
    """Neutralise everything in caption text that ASS would read as structure.

    `{` and `}` are override-tag delimiters. Unescaped, a caption containing
    braces silently disappears into a parse error.

    The line breaks are the security-relevant half. Word text is **not trusted
    input**: `fixups` values arrive in a `POST /jobs` body and any word can be
    rewritten through `PUT /jobs/{id}/transcript`, and both land in `Word.text`
    verbatim. A newline there ends the `Dialogue:` line and turns the remainder
    into directives the renderer executes — including a `[Fonts]` section, which
    libass will decode and hand to the font engine. This is a trust boundary,
    not tidiness."""
    return token.translate(_STRUCTURAL).replace("{", "(").replace("}", ")")


def safe_font(name: str) -> str:
    """A font name for the `Style:` line.

    That line is comma-delimited, so a comma in the name shifts every field after
    it — size, colours, margins — and a newline injects a whole new directive
    into `[V4+ Styles]`. `font` is caller-supplied on both front ends, so it gets
    the same treatment as caption text."""
    cleaned = " ".join(name.translate(_STRUCTURAL).replace(",", " ").split())
    cleaned = cleaned.replace("{", "(").replace("}", ")")
    return cleaned[:64] or DEFAULT_FONT


#: Seconds to wait for fontconfig. A host with thousands of fonts takes a moment
#: on a cold cache, and this sits in preflight where a hang is worse than a
#: skipped warning.
FC_LIST_TIMEOUT = 5.0


@functools.lru_cache(maxsize=1)
def installed_fonts() -> frozenset[str] | None:
    """Every font family fontconfig can see, casefolded — or None.

    **None means "cannot tell", never "missing".** fontconfig is not installed on
    a stock macOS host, and a warning that fires because the *checker* is absent
    is a warning people learn to ignore. The rendering host that matters is the
    Docker image, where fontconfig is present and the installed set is fixed at
    build time.

    `fc-list` rather than `fc-match`: fc-match always returns something — that IS
    the fallback behaviour being warned about — so it can never answer whether a
    family is present. Nor a render probe: that costs an ffmpeg spawn in
    preflight, and this only has to be good enough to warn.

    Cached for the life of the process. A font installed while a long-running
    server is up will not be noticed, which is the opposite of the deliberately
    self-healing `utils.ffmpeg_available` — the difference is what a stale answer
    costs. A stale ffmpeg probe reports the server unable to work at all; a stale
    font answer costs one spurious or missing log line, and under Docker the font
    set cannot change without a new image anyway."""
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True,
                             text=True, timeout=FC_LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None                      # not installed, or it hung
    if out.returncode != 0:
        return None
    families: set[str] = set()
    for line in out.stdout.splitlines():
        # one line per font file; aliases and localised names are comma-separated
        for alias in line.split(","):
            alias = alias.strip()
            if alias:
                families.add(alias.casefold())
    return frozenset(families)


def font_available(name: str) -> bool | None:
    """Whether libass will find this family. None when fontconfig cannot say.

    Asks about `safe_font(name)`, not `name`: that is the string that reaches the
    `Style:` line, so it is the only one libass is ever asked to resolve."""
    fonts = installed_fonts()
    if fonts is None:
        return None
    return safe_font(name).casefold() in fonts


def font_warning(name: str) -> str | None:
    """A line to log when the requested font is missing, else None.

    A warning and not a refusal, deliberately — unlike `--device cuda` or
    `--reframe track`, which raise rather than degrade. Those two have a correct
    alternative the caller can be handed; a missing font does not, and refusing
    would mean a host with any unusual font name cannot render at all. The cost
    of being wrong is also asymmetric: libass substituting a face is ugly, not
    incorrect, whereas refusing an hour-long job over a font name is.

    `is not False` covers True and None together — a host with no fontconfig
    warns about nothing."""
    if font_available(name) is not False:
        return None
    return (f"font {safe_font(name)!r} is not installed on this host — libass "
            f"will silently substitute a fallback face rather than fail, and on "
            f"Arabic that usually renders as tofu. Install the font on the "
            f"RENDERING host (under the API that is the server, not your "
            f"machine), or pass a family `fc-list : family` reports.")


#: Codepoint ranges whose script is written right-to-left. Arabic and Hebrew are
#: the ones this project will actually meet; the rest are cheap to include and
#: fail the same way.
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
    hits the same libass run-splitting bug as a fully RTL one."""
    return any(any(lo <= ord(ch) <= hi for lo, hi in _RTL_RANGES) for ch in text)


def _clamp(start: float, end: float, next_start: float | None) -> float:
    """A cue end that cannot reach into the next cue.

    Order matters. MIN_CUE runs first so a degenerate line still gets a visible
    duration, and the ceiling runs LAST so that duration can never be bought by
    overlapping the next line — libass draws simultaneous events stacked, and a
    caption appearing above the previous one is far worse than a short cue.

    `next_start` is always greater than `start` in practice: chunks are built
    from ordered words and each chunk opens on a later word than the one before,
    so clamping cannot invert the cue."""
    if end <= start:
        end = start + MIN_CUE
    if next_start is not None:
        end = min(end, next_start)
    return end


def build_ass(clip: Clip, words: list[Word], path: Path,
              per_line: int = CAPTION_MAX_WORDS, font: str = DEFAULT_FONT,
              highlight: bool | None = None) -> Path:
    """Captions timed relative to the clip start.

    Relative because `-ss` before `-i` resets timestamps to 0.

    `highlight` controls per-word colouring: None (the default) enables it for
    LTR text and disables it for RTL, which is the only combination that renders
    correctly — see the module docstring. Pass True to force it on RTL anyway;
    the line will be scrambled, and the only reason to do that is to re-measure
    the bug."""
    lines = [ASS_HEADER.replace("{FONT}", safe_font(font))
                        .replace("{SIZE}", str(FONT_SIZE))
                        .replace("{OUTLINE}", str(OUTLINE))]

    chunks = group_words(words_in(clip, words), max_words=per_line)
    for i, chunk in enumerate(chunks):
        tokens = [escape(w.text) for w in chunk]
        want = highlight if highlight is not None else not is_rtl(" ".join(tokens))
        # Where the NEXT caption line begins, so this one can be clamped off it.
        # None on the last line, which has nothing to collide with.
        next_start = (chunks[i + 1][0].start - clip.start
                      if i + 1 < len(chunks) else None)

        if not want:
            # One cue for the whole line. Nothing splits the run, so libass
            # applies bidi and shaping across the full text and the word order
            # survives. The trade is that the caption no longer tracks the
            # individual word — it appears and clears with the line.
            start = chunk[0].start - clip.start
            end = _clamp(start, chunk[-1].end - clip.start + LAST_WORD_HOLD,
                         next_start)
            lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Pop,,0,0,0,,"
                         + " ".join(tokens))
            continue

        for idx, active in enumerate(chunk):
            parts = [f"{HILITE}{t}{RESET}" if j == idx else t
                     for j, t in enumerate(tokens)]
            text = " ".join(parts)

            start = active.start - clip.start
            # Inside a chunk the hold is already the next word's start, so only
            # the LAST word can spill into the next line — but it is the same
            # clamp either way, and routing both through one call means a future
            # edit cannot fix one path and forget the other.
            if idx + 1 < len(chunk):
                end = _clamp(start, chunk[idx + 1].start - clip.start, next_start)
            else:
                end = _clamp(start, active.end - clip.start + LAST_WORD_HOLD,
                             next_start)
            lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Pop,,0,0,0,,{text}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
