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
    CAPSULE_KAPPA,
    CAPTION_MAX_CHARS,
    CAPTION_MAX_WORDS,
    DEFAULT_CAPTION_STYLE,
    DEFAULT_FONT,
    TARGET_H,
    TARGET_W,
)
from ..core.types import Clip, Word
from ..core.utils import ts_ass
from . import textlayout
from .cuts import words_in
from .textlayout import is_rtl

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


def build_ass_youtube(clip: Clip, words: list[Word], path: Path,
                      measurer, per_line: int, font: str) -> Path:
    """Captions with every word positioned absolutely and the spoken one pilled.

    THE REASON THIS WORKS ON ARABIC: each `Dialogue` event holds exactly one
    word, so there is no multi-word bidi run for an override tag to split. The
    scrambling that forced RTL onto one-cue-per-line simply has no surface here.
    The price is that libass no longer lays out the line, which is what
    `textlayout` is for.

    Three events per word, on three layers:

        0   every word, dimmed, outline kept     spans the WHOLE line window
        1   the capsule                           only while that word is active
        2   the word again, bright, no outline    only while that word is active

    Layer 0 carries identical timings for every word in a line, so only the
    capsule and the bright word need per-word windows. The ordering is also what
    lets the active word have no outline: the dimmed copy underneath still has
    one, and the capsule paints over it — which is why PILL_PAD_X must exceed
    OUTLINE."""
    from ..core.constants import (
        CAPTION_DIM_ALPHA,
        CAPTION_SIDE_MARGIN,
        PILL_FILL,
        PILL_PAD_X,
        PILL_PAD_Y,
    )
    from . import textlayout as tl

    lines = [ASS_HEADER.replace("{FONT}", safe_font(font))
                        .replace("{SIZE}", str(FONT_SIZE))
                        .replace("{OUTLINE}", str(OUTLINE))]

    usable = TARGET_W - 2 * CAPTION_SIDE_MARGIN
    in_clip = [w for w in words_in(clip, words) if w.text]
    texts = [escape(w.text) for w in in_clip]
    chunks = tl.chunk_by_width(texts, measurer, usable, per_line)

    # Vertical: reproduce where MarginV 300 puts a bottom-aligned line today, so
    # a style change does not silently move captions up the frame.
    baseline_y = TARGET_H - 300 - measurer.line_height / 2
    pill_fill = ass_bgr(PILL_FILL)
    dim = f"\\alpha&H{CAPTION_DIM_ALPHA:02X}&"

    for ci, chunk in enumerate(chunks):
        chunk_words = [in_clip[i] for i in chunk]
        chunk_texts = [texts[i] for i in chunk]
        base_rtl = is_rtl(" ".join(chunk_texts))
        line = tl.solve_line(chunk_texts, measurer, usable, base_rtl)

        next_start = (in_clip[chunks[ci + 1][0]].start - clip.start
                      if ci + 1 < len(chunks) else None)
        l_start = chunk_words[0].start - clip.start
        l_end = _clamp(l_start,
                       chunk_words[-1].end - clip.start + LAST_WORD_HOLD,
                       next_start)

        for bi, box in enumerate(line.boxes):
            cx = CAPTION_SIDE_MARGIN + box.x + box.width / 2
            w_active = chunk_words[bi]
            a_start = w_active.start - clip.start
            if bi + 1 < len(chunk_words):
                a_end = _clamp(a_start, chunk_words[bi + 1].start - clip.start,
                               next_start)
            else:
                a_end = l_end

            # 0 — dimmed, whole line window
            lines.append(
                f"Dialogue: 0,{ts_ass(l_start)},{ts_ass(l_end)},Pop,,0,0,0,,"
                f"{{\\pos({cx:.0f},{baseline_y:.0f})\\an5{dim}}}{box.text}")

            # 1 — the capsule
            pw = int(round(box.width)) + 2 * PILL_PAD_X
            ph = int(round(line.height)) + 2 * PILL_PAD_Y
            px = cx - pw / 2
            py = baseline_y - ph / 2
            lines.append(
                f"Dialogue: 1,{ts_ass(a_start)},{ts_ass(a_end)},Pop,,0,0,0,,"
                f"{{\\pos({px:.0f},{py:.0f})\\an7\\p1\\c{pill_fill}"
                f"\\bord0\\shad0}}{capsule_path(pw, ph)}{{\\p0}}")

            # 2 — the active word, no outline; the pill carries contrast
            lines.append(
                f"Dialogue: 2,{ts_ass(a_start)},{ts_ass(a_end)},Pop,,0,0,0,,"
                f"{{\\pos({cx:.0f},{baseline_y:.0f})\\an5\\bord0\\shad0}}{box.text}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_ass(clip: Clip, words: list[Word], path: Path,
              per_line: int = CAPTION_MAX_WORDS, font: str = DEFAULT_FONT,
              highlight: bool | None = None,
              style: str = DEFAULT_CAPTION_STYLE, measurer=None) -> Path:
    """Captions timed relative to the clip start.

    Relative because `-ss` before `-i` resets timestamps to 0.

    `highlight` controls per-word colouring: None (the default) enables it for
    LTR text and disables it for RTL, which is the only combination that renders
    correctly — see the module docstring. Pass True to force it on RTL anyway;
    the line will be scrambled, and the only reason to do that is to re-measure
    the bug.

    `style` picks the emitter: `youtube` (the default) positions every word
    absolutely and pills the active one — see `build_ass_youtube`. `pop` is the
    original single-line style this docstring otherwise describes, and every
    measured number in CLAUDE.md and docs/quality.md depends on `pop` being
    untouched by the addition. `measurer` is normally left to be resolved from
    `font`; the caller passes one directly only where fontconfig or uharfbuzz is
    known to be unavailable (i.e. the test suite)."""
    if style == "youtube":
        m = measurer if measurer is not None else \
            textlayout.load_measurer(safe_font(font), FONT_SIZE)
        if m is not None:
            return build_ass_youtube(clip, words, path, m, per_line, font)
        # No measurement available. Fall THROUGH to `pop` rather than raise —
        # see `resolve_style`. The caller is responsible for warning.

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


def ass_bgr(hex_rgb: str) -> str:
    """`#RRGGBB` -> an ASS `&HAABBGGRR&` literal, fully opaque.

    ASS colours are BGR, not RGB. This has bitten this file before — the
    existing highlight is written `&H00E0FF&` and is yellow, not blue."""
    h = hex_rgb.lstrip("#").upper()
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}&"


def capsule_path(w: int, h: int) -> str:
    """An ASS `\\p1` drawing of a capsule `w` x `h`, origin at its top-left.

    Radius is half the height, which makes it a true capsule at any word length
    and needs no per-word tuning. Coordinates are integers: libass accepts
    floats, but integers are portable across every renderer that reads ASS and
    the sub-pixel difference is invisible at a ~100px pill.

    The path carries NO caller-supplied text — it is pure geometry — so it adds
    no trust-boundary surface. `escape()` still guards every actual word."""
    r = max(1, h // 2)
    w = max(w, 2 * r)                  # never let the caps overlap and invert
    k = round(r * CAPSULE_KAPPA)
    return (f"m {r} 0 l {w - r} 0 "
            f"b {w - r + k} 0 {w} {r - k} {w} {r} "
            f"l {w} {h - r} "
            f"b {w} {h - r + k} {w - r + k} {h} {w - r} {h} "
            f"l {r} {h} "
            f"b {r - k} {h} 0 {h - r + k} 0 {h - r} "
            f"l 0 {r} "
            f"b 0 {r - k} {r - k} 0 {r} 0")
