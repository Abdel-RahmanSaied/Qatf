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

from pathlib import Path

from ..core.constants import CAPTION_MAX_CHARS, CAPTION_MAX_WORDS, TARGET_H, TARGET_W
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
Style: Pop,{{FONT}},82,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,7,3,2,90,90,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HILITE = r"{\c&H00E0FF&}"   # ASS colour is BGR, not RGB -> this is yellow
RESET = r"{\c&HFFFFFF&}"

MIN_CUE = 0.08
LAST_WORD_HOLD = 0.12


def group_words(words: list[Word], max_words: int = CAPTION_MAX_WORDS,
                max_chars: int = CAPTION_MAX_CHARS) -> list[list[Word]]:
    """Chunk into caption lines by BOTH word count and character budget.
    Word count alone overflows the frame on long words — 4 x 12-char words at
    82px is wider than 1080px."""
    out: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
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
    return cleaned[:64] or "Arial"


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


def build_ass(clip: Clip, words: list[Word], path: Path,
              per_line: int = CAPTION_MAX_WORDS, font: str = "Arial",
              highlight: bool | None = None) -> Path:
    """Captions timed relative to the clip start.

    Relative because `-ss` before `-i` resets timestamps to 0.

    `highlight` controls per-word colouring: None (the default) enables it for
    LTR text and disables it for RTL, which is the only combination that renders
    correctly — see the module docstring. Pass True to force it on RTL anyway;
    the line will be scrambled, and the only reason to do that is to re-measure
    the bug."""
    lines = [ASS_HEADER.replace("{FONT}", safe_font(font))]

    for chunk in group_words(words_in(clip, words), max_words=per_line):
        tokens = [escape(w.text) for w in chunk]
        want = highlight if highlight is not None else not is_rtl(" ".join(tokens))

        if not want:
            # One cue for the whole line. Nothing splits the run, so libass
            # applies bidi and shaping across the full text and the word order
            # survives. The trade is that the caption no longer tracks the
            # individual word — it appears and clears with the line.
            start = chunk[0].start - clip.start
            end = chunk[-1].end - clip.start + LAST_WORD_HOLD
            if end <= start:
                end = start + MIN_CUE
            lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Pop,,0,0,0,,"
                         + " ".join(tokens))
            continue

        for idx, active in enumerate(chunk):
            parts = [f"{HILITE}{t}{RESET}" if j == idx else t
                     for j, t in enumerate(tokens)]
            text = " ".join(parts)

            start = active.start - clip.start
            # hold the last word of a group until the next word actually starts
            if idx + 1 < len(chunk):
                end = chunk[idx + 1].start - clip.start
            else:
                end = active.end - clip.start + LAST_WORD_HOLD
            if end <= start:
                end = start + MIN_CUE
            lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Pop,,0,0,0,,{text}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
