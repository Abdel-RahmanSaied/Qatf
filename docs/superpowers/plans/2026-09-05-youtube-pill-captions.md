# YouTube-style pill captions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render word-level captions in the YouTube Shorts idiom — full line visible, unspoken words dimmed, active word in a filled capsule — on Arabic as well as Latin.

**Architecture:** A new pure module `qatf/pipeline/textlayout.py` measures shaped word advances (uharfbuzz) and solves a caption line into positioned boxes. `qatf/pipeline/captions.py` consumes those boxes and emits one `Dialogue` event per word at an absolute `\pos`, plus a `\p1` vector capsule. Because each event holds exactly one word, the libass bidi-run splitting that forced RTL onto line-at-a-time captions has no surface to act on.

**Tech Stack:** Python 3.10+, uharfbuzz (new, optional extra), fontconfig `fc-match` (already a soft dependency), libass via ffmpeg's `ass` filter.

**Spec:** `docs/superpowers/specs/2026-09-05-youtube-pill-captions-design.md`

## Global Constraints

- **Tests are NOT pytest.** The suite is a custom harness: `from _harness import check, raises, report, section`. Run with `python tests/smoke_pipeline.py`. Follow the existing style exactly.
- **Both smoke suites must stay green and `ruff check .` must pass** before any commit. Run from `qatf-backend/`.
- **Layer arrows:** `api -> jobs -> pipeline -> llm -> core`. `textlayout.py` lives in `pipeline/` and imports only from `core/`. A lazy import inside a function body is still a dependency.
- **uharfbuzz is optional.** The pipeline must install and run without it. It goes behind its own extra, exactly like `track`.
- **Never echo caller input in an error message.** Name the allowed set instead.
- **Pill fill `#B4560A`** → ASS `&H000A56B4&` (BGR, byte-reversed).
- **Inactive word alpha `&H8C&`** = 45% opacity (ASS alpha is inverted: `00` opaque, `FF` transparent).
- **Capsule bezier control offset `0.5523 × r`**, radius `r` = half pill height.
- **Usable width 900px** = 1080 frame − 2 × 90px side margins.
- **`PILL_PAD_X` must exceed `OUTLINE` (4)** or the dimmed layer's outline fringes around the active word.
- **Existing `pop` path must not change.** Every number measured against it stays true.

---

### Task 1: Direction runs and visual order

Pure logic, no dependency. This is the correctness core of RTL support.

**Files:**
- Create: `qatf-backend/qatf/pipeline/textlayout.py`
- Modify: `qatf-backend/qatf/pipeline/captions.py` (move `is_rtl`/`_RTL_RANGES` out, re-export)
- Test: `qatf-backend/tests/smoke_pipeline.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `textlayout.is_rtl(text: str) -> bool`, `textlayout.visual_order(words: list[str], base_rtl: bool) -> list[int]`. `captions.is_rtl` remains importable as a re-export.

- [ ] **Step 1: Write the failing test**

Add to `tests/smoke_pipeline.py`, after the existing `section("caption cues must be disjoint")` block:

```python
section("textlayout: direction runs")
from qatf.pipeline import textlayout as tl

check("ltr line keeps logical order",
      tl.visual_order(["a", "b", "c"], base_rtl=False) == [0, 1, 2])
check("rtl line reverses word order",
      tl.visual_order(["واحد", "اثنين", "ثلاثة"], base_rtl=True) == [2, 1, 0])
# A Latin phrase inside Arabic: the RUN moves, the words inside it do not.
# Naive reversal would give [3, 2, 1, 0] and read "is Python" backwards.
check("latin run inside arabic keeps its internal order",
      tl.visual_order(["قال", "Python", "is", "الأفضل"], base_rtl=True) == [3, 1, 2, 0])
check("arabic run inside latin reverses internally",
      tl.visual_order(["The", "كتاب", "جديد", "is"], base_rtl=False) == [0, 2, 1, 3])
check("single word is a no-op in both directions",
      tl.visual_order(["x"], base_rtl=True) == [0]
      and tl.visual_order(["x"], base_rtl=False) == [0])
check("empty line does not crash", tl.visual_order([], base_rtl=True) == [])
check("is_rtl moved but is still importable from captions",
      captions.is_rtl is tl.is_rtl)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && python tests/smoke_pipeline.py`
Expected: `ModuleNotFoundError: No module named 'qatf.pipeline.textlayout'`

- [ ] **Step 3: Create the module**

Create `qatf-backend/qatf/pipeline/textlayout.py`:

```python
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
```

- [ ] **Step 4: Move `is_rtl` out of `captions.py` and re-export**

In `qatf-backend/qatf/pipeline/captions.py`, delete the `_RTL_RANGES` tuple and the `is_rtl` function, and add to the import block:

```python
from . import textlayout
from .textlayout import is_rtl
```

Both lines are needed: `is_rtl` keeps the existing name working, and the module
import is what Tasks 5 and 6 call `textlayout.load_measurer` through. Keep the name exported — `build_ass` calls it and `smoke_pipeline.py` imports it through `captions`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd qatf-backend && python tests/smoke_pipeline.py && ruff check .`
Expected: all checks PASS, including the pre-existing RTL checks that still import `captions.is_rtl`.

- [ ] **Step 6: Commit**

```bash
git add qatf-backend/qatf/pipeline/textlayout.py qatf-backend/qatf/pipeline/captions.py qatf-backend/tests/smoke_pipeline.py
git commit -m "feat(captions): direction-run visual ordering for caption lines"
```

---

### Task 2: Font resolution and shaped measurement

**Files:**
- Modify: `qatf-backend/qatf/pipeline/textlayout.py`
- Modify: `qatf-backend/pyproject.toml`
- Test: `qatf-backend/tests/smoke_pipeline.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `textlayout.font_file(family: str) -> Path | None`, `textlayout.Measurer` (protocol with `advance(text: str) -> float` and attribute `line_height: float`), `textlayout.load_measurer(family: str, size: float) -> Measurer | None`. `load_measurer` returns `None` — never raises — when uharfbuzz or the font file is unavailable.

- [ ] **Step 1: Verify uharfbuzz's licence before adding it**

This project has ruled out dependencies on licence grounds before (ultralytics AGPL-3.0, insightface non-commercial weights). Do not assume.

```bash
pip download uharfbuzz --no-deps -d /tmp/uhb && python -c "
import importlib.metadata as md, glob, zipfile
w = glob.glob('/tmp/uhb/*.whl')[0]
with zipfile.ZipFile(w) as z:
    for n in z.namelist():
        if 'METADATA' in n or 'LICENSE' in n.upper():
            print('---', n)
            print(z.read(n).decode('utf-8', 'replace')[:600])
"
```

Expected: Apache-2.0 (uharfbuzz) wrapping HarfBuzz (MIT-style). **If it is anything copyleft, STOP and report** — the whole approach needs revisiting.

- [ ] **Step 2: Probe the installed uharfbuzz API surface**

Version drift is real here; write code against what the wheel actually exposes.

```bash
pip install uharfbuzz && python -c "
import uharfbuzz as hb
print('version', getattr(hb, '__version__', '?'))
print('Blob.from_file_path', hasattr(hb.Blob, 'from_file_path'))
f = hb.Font(hb.Face(hb.Blob(b'')) ) if False else None
print([n for n in dir(hb.Font) if 'extent' in n.lower() or 'scale' in n.lower()])
print([n for n in dir(hb) if n.startswith('ot_metrics')])
"
```

Record which of `get_font_h_extents` / `get_font_extents` exists. The implementation below tries both and falls back to `ot_metrics_get_position`.

- [ ] **Step 3: Write the failing test**

Append to `tests/smoke_pipeline.py`:

```python
section("textlayout: measurement")

# font_file must not raise when fontconfig is absent — same "cannot tell, never
# missing" rule installed_fonts() follows.
_ff = tl.font_file("Noto Sans Arabic")
check("font_file returns a Path or None, never raises",
      _ff is None or _ff.suffix.lower() in (".ttf", ".otf", ".ttc"), str(_ff))

# The fallback is the load-bearing behaviour: it must be None, not an exception,
# because a missing wheel has to degrade to the `pop` style rather than fail a job.
check("load_measurer on a font that cannot exist returns None",
      tl.load_measurer("NoSuchFamily\u0000Ever", 64) is None)

# A fake measurer is how every layout check runs on a host without uharfbuzz.
class FakeMeasurer:
    line_height = 80.0
    def advance(self, text: str) -> float:
        return 10.0 * len(text)

_fake = FakeMeasurer()
check("fake measurer satisfies the Measurer protocol",
      isinstance(_fake, tl.Measurer))

_m = tl.load_measurer(DEFAULT_FONT, 64)
if _m is None:
    check("SKIP real measurement (uharfbuzz or font unavailable)", True)
else:
    check("a longer word measures wider", _m.advance("hello") > _m.advance("hi"))
    check("the space has a real advance", _m.advance(" ") > 0)
    check("arabic measures non-zero", _m.advance("البرمجة") > 0)
    check("line height is positive and sane for 64px",
          40 < _m.line_height < 200, str(_m.line_height))
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd qatf-backend && python tests/smoke_pipeline.py`
Expected: FAIL with `AttributeError: module 'qatf.pipeline.textlayout' has no attribute 'font_file'`

- [ ] **Step 5: Implement measurement**

Append to `qatf/pipeline/textlayout.py`:

```python
import functools
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

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
    except (OSError, subprocess.SubprocessError):
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
```

- [ ] **Step 6: Add the optional dependency**

In `qatf-backend/pyproject.toml`, after the `youtube` extra:

```toml
# stage 5a — only the `youtube` caption style needs this. Its own extra for the
# same reason `track` and `youtube` have theirs: the pipeline must keep
# installing, and rendering, without it.
#
# uharfbuzz rather than Pillow+Raqm: Raqm is only present if Pillow was BUILT
# with libraqm, and when it is not, Pillow silently falls back to BASIC layout
# and gets Arabic advances wrong without erroring. Silent-and-wrong on the
# Arabic path is this project's worst failure mode.
#
# It is also the engine libass shapes with, so we measure with exactly what will
# draw the frame.
captions = [
    "uharfbuzz>=0.39",
]
```

And add it to `all`:

```toml
all = [
    "qatf[api,anthropic,openai,track,youtube,captions]",
]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd qatf-backend && python tests/smoke_pipeline.py && ruff check .`
Expected: all PASS. The real-measurement block may report the SKIP line if uharfbuzz is absent — that is correct behaviour, not a failure.

- [ ] **Step 8: Commit**

```bash
git add qatf-backend/qatf/pipeline/textlayout.py qatf-backend/pyproject.toml qatf-backend/tests/smoke_pipeline.py
git commit -m "feat(captions): shaped word measurement via uharfbuzz"
```

---

### Task 3: Line solving

**Files:**
- Modify: `qatf-backend/qatf/pipeline/textlayout.py`
- Test: `qatf-backend/tests/smoke_pipeline.py`

**Interfaces:**
- Consumes: `visual_order` (Task 1), `Measurer` (Task 2).
- Produces: `textlayout.Box` (frozen dataclass: `text: str`, `x: float`, `width: float`), `textlayout.Line` (frozen dataclass: `boxes: list[Box]`, `width: float`, `height: float`), `textlayout.solve_line(words: list[str], m: Measurer, usable: float, base_rtl: bool) -> Line`, `textlayout.chunk_by_width(texts: list[str], m: Measurer, usable: float, max_words: int) -> list[list[int]]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/smoke_pipeline.py`:

```python
section("textlayout: line solving")

_M = FakeMeasurer()          # 10px per character, 80px line height

# Three 2-char words: 3*20 advance + 2 spaces of 10 = 80
_line = tl.solve_line(["ab", "cd", "ef"], _M, usable=900, base_rtl=False)
check("line width is words plus measured spaces", abs(_line.width - 80.0) < 1e-6,
      str(_line.width))
check("line height comes from the measurer", _line.height == 80.0)
check("boxes are returned in logical order", [b.text for b in _line.boxes] ==
      ["ab", "cd", "ef"])
check("ltr boxes ascend left to right",
      [b.x for b in _line.boxes] == sorted(b.x for b in _line.boxes))
check("the line is centred in the usable width",
      abs(_line.boxes[0].x - (900 - 80) / 2) < 1e-6, str(_line.boxes[0].x))

# RTL: boxes stay in LOGICAL order in the list, but the FIRST logical word must
# sit furthest RIGHT. Returning them pre-reversed would make every consumer
# guess which order it has.
_rtl = tl.solve_line(["ab", "cd", "ef"], _M, usable=900, base_rtl=True)
check("rtl keeps boxes in logical order in the list",
      [b.text for b in _rtl.boxes] == ["ab", "cd", "ef"])
check("rtl places the first logical word furthest right",
      _rtl.boxes[0].x > _rtl.boxes[-1].x,
      f"{_rtl.boxes[0].x} vs {_rtl.boxes[-1].x}")
check("rtl line is the same width as the ltr one",
      abs(_rtl.width - _line.width) < 1e-6)
check("no box escapes the usable width",
      all(0 <= b.x and b.x + b.width <= 900 for b in _rtl.boxes))

# Chunking by measured width replaces the character-count proxy.
_long = ["aaaaa"] * 12          # 50px each + 10px spaces => 5 fit in 300px
_chunks = tl.chunk_by_width(_long, _M, usable=300, max_words=8)
check("chunking respects measured width",
      all(sum(_M.advance(_long[i]) for i in c) + 10 * (len(c) - 1) <= 300
          for c in _chunks), str([len(c) for c in _chunks]))
check("chunking still honours the word cap",
      all(len(c) <= 8 for c in tl.chunk_by_width(["a"] * 30, _M, 900, 8)))
check("chunking loses no words",
      [i for c in _chunks for i in c] == list(range(len(_long))))

# A single word wider than the frame cannot be chunked away.
_huge = tl.chunk_by_width(["x" * 200], _M, usable=300, max_words=8)
check("an over-wide single word gets its own chunk rather than vanishing",
      _huge == [[0]], str(_huge))
_hl = tl.solve_line(["x" * 200], _M, usable=300, base_rtl=False)
check("an over-wide word is centred, overflowing symmetrically",
      abs(_hl.boxes[0].x - (300 - 2000) / 2) < 1e-6, str(_hl.boxes[0].x))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && python tests/smoke_pipeline.py`
Expected: FAIL with `AttributeError: module 'qatf.pipeline.textlayout' has no attribute 'solve_line'`

- [ ] **Step 3: Implement the solver**

Append to `qatf/pipeline/textlayout.py` (add `from dataclasses import dataclass` to the imports):

```python
@dataclass(frozen=True)
class Box:
    """One word's slot on the line, in PlayRes pixels. `x` is the LEFT edge."""

    text: str
    x: float
    width: float


@dataclass(frozen=True)
class Line:
    """A solved caption line. `boxes` is in LOGICAL order, not visual order.

    Logical, deliberately: the caller pairs boxes with `Word` objects to get
    timings, and a list that silently reorders itself on RTL is a list every
    consumer has to remember to un-reorder. Position lives in `Box.x`, which is
    where the visual order already is."""

    boxes: list[Box]
    width: float
    height: float


def solve_line(words: list[str], m: Measurer, usable: float,
               base_rtl: bool) -> Line:
    """Place each word on one centred line.

    The line's total width is order-independent — it is the words plus the
    measured inter-word spaces — so only the x assignment depends on direction.

    A line wider than `usable` is centred anyway and allowed to overflow. That
    only happens for a single word too wide to break, which `chunk_by_width`
    cannot chunk away; clipping it symmetrically is more honest than silently
    dropping it."""
    if not words:
        return Line([], 0.0, m.line_height)

    widths = [m.advance(w) for w in words]
    space = m.advance(" ")
    total = sum(widths) + space * (len(words) - 1)

    x = (usable - total) / 2.0
    placed: dict[int, float] = {}
    for i in visual_order(words, base_rtl):
        placed[i] = x
        x += widths[i] + space

    return Line([Box(words[i], placed[i], widths[i]) for i in range(len(words))],
                total, m.line_height)


def chunk_by_width(texts: list[str], m: Measurer, usable: float,
                   max_words: int) -> list[list[int]]:
    """Group word indices into lines that fit, by MEASURED width.

    This replaces `captions.CAPTION_MAX_CHARS` on the pill path. That constant
    is only ever a proxy for width — its own comment warns it must be recomputed
    whenever FONT_SIZE moves, from an estimate of "900px usable, ~half the em".
    Measuring makes the proxy unnecessary. It stays in use for the `pop` path.

    A word wider than `usable` on its own still gets a chunk: refusing to emit
    it would delete speech from the captions."""
    out: list[list[int]] = []
    cur: list[int] = []
    cur_w = 0.0
    space = m.advance(" ")
    for i, t in enumerate(texts):
        w = m.advance(t)
        projected = cur_w + (space if cur else 0.0) + w
        if cur and (len(cur) >= max_words or projected > usable):
            out.append(cur)
            cur, cur_w = [], 0.0
            projected = w
        cur.append(i)
        cur_w = projected
    if cur:
        out.append(cur)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd qatf-backend && python tests/smoke_pipeline.py && ruff check .`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add qatf-backend/qatf/pipeline/textlayout.py qatf-backend/tests/smoke_pipeline.py
git commit -m "feat(captions): solve caption lines into positioned word boxes"
```

---

### Task 4: Constants and the capsule path

**Files:**
- Modify: `qatf-backend/qatf/core/constants.py`
- Modify: `qatf-backend/qatf/pipeline/captions.py`
- Test: `qatf-backend/tests/smoke_pipeline.py`

**Interfaces:**
- Consumes: nothing.
- Produces: constants `CAPTION_STYLES`, `DEFAULT_CAPTION_STYLE`, `PILL_FILL`, `PILL_PAD_X`, `PILL_PAD_Y`, `CAPTION_DIM_ALPHA`, `CAPTION_SIDE_MARGIN`, `CAPSULE_KAPPA`; and `captions.ass_bgr(hex_rgb: str) -> str`, `captions.capsule_path(w: int, h: int) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/smoke_pipeline.py`:

```python
section("captions: capsule geometry and colour")
from qatf.core import constants as K

check("ASS colour is BGR, not RGB", captions.ass_bgr("#B4560A") == "&H000A56B4&")
check("ass_bgr tolerates a missing hash", captions.ass_bgr("B4560A") == "&H000A56B4&")
check("the chosen pill fill is the measured 4.91:1 colour", K.PILL_FILL == "#B4560A")
check("dim alpha is 45% opacity, inverted as ASS wants",
      K.CAPTION_DIM_ALPHA == 0x8C)
# Not cosmetic: the capsule paints over the dimmed layer's outline, so padding
# under the outline width leaves a dark fringe around the active word.
check("horizontal pill padding clears the outline",
      K.PILL_PAD_X > captions.OUTLINE, f"{K.PILL_PAD_X} vs {captions.OUTLINE}")

_p = captions.capsule_path(200, 80)
check("capsule path starts with a move", _p.startswith("m "))
check("capsule path uses only drawing commands and integers",
      all(tok in ("m", "l", "b") or re.fullmatch(r"-?\d+", tok)
          for tok in _p.split()), _p[:80])
check("capsule path has four bezier corners", _p.count("b ") == 4)
_toks = _p.split()
check("capsule path closes on its start point",
      _toks[-2:] == [str(80 // 2), "0"], " ".join(_toks[-4:]))
check("a capsule on a tiny box does not invert",
      "-" not in captions.capsule_path(10, 80))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && python tests/smoke_pipeline.py`
Expected: FAIL with `AttributeError: module 'qatf.pipeline.captions' has no attribute 'ass_bgr'`

- [ ] **Step 3: Add the constants**

Append to `qatf-backend/qatf/core/constants.py`:

```python
#: Caption styles. `youtube` positions every word absolutely and puts the
#: spoken one in a filled capsule; `pop` is the original single-line style and
#: remains the fallback whenever measurement is unavailable.
CAPTION_STYLES = ("youtube", "pop")
DEFAULT_CAPTION_STYLE = "youtube"

#: Pill fill. A deepened saffron, and the depth is measured rather than chosen:
#: white on the product's existing highlight yellow (#E8A317) is 2.17:1, below
#: even the 3:1 large-text floor, which is why a saffron pill would have to keep
#: a black outline to stay legible. Outline-on-pill is the muddy combination,
#: and worse on Arabic than Latin — the caption stroke was already cut from 7px
#: to 4px because a heavy outline thickens Naskh's connected joins.
#:
#: #B4560A measures 4.91:1 against white, clears 4.5:1, and lets the active word
#: drop its outline entirely. #A34708 measures 6.07:1 and was rejected for
#: reading as its own colour rather than the product's accent.
PILL_FILL = "#B4560A"

#: Padding between the word's box and the capsule edge.
#:
#: PILL_PAD_X IS NOT COSMETIC. The dimmed word on the layer beneath keeps its
#: outline, and the capsule is what paints over it — so padding at or under
#: `captions.OUTLINE` leaves a dark fringe around the active word. There is a
#: check pinning this; do not "tighten" past it.
PILL_PAD_X = 18
PILL_PAD_Y = 8

#: Opacity of a word that is not currently being spoken, as an ASS alpha byte.
#: ASS alpha is INVERTED — 0x00 is opaque, 0xFF is transparent — so 45% opacity
#: is 0x8C, not 0x73. Applies to fill, outline and shadow together, which is
#: what makes the whole word recede rather than just its face.
CAPTION_DIM_ALPHA = 0x8C

#: Side margin used when solving a caption line, matching MarginL/MarginR on the
#: Style line. Usable width is TARGET_W - 2 * this = 900px at 1080 wide.
CAPTION_SIDE_MARGIN = 90

#: Bezier control-point offset for a quarter circle, as a fraction of the
#: radius. The standard circle-from-beziers constant; ASS has no rounded-rect
#: primitive, so the capsule is a hand-built path.
CAPSULE_KAPPA = 0.5523
```

- [ ] **Step 4: Implement the helpers**

Add to `qatf-backend/qatf/pipeline/captions.py`, importing the new constants:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd qatf-backend && python tests/smoke_pipeline.py && ruff check .`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add qatf-backend/qatf/core/constants.py qatf-backend/qatf/pipeline/captions.py qatf-backend/tests/smoke_pipeline.py
git commit -m "feat(captions): pill constants and ASS capsule geometry"
```

---

### Task 5: Emit the pill style, and reshape the disjointness invariant

The largest task. It also **breaks an existing check on purpose**, and repairing that check correctly is part of the deliverable — which is why it is not a separate task.

**Files:**
- Modify: `qatf-backend/qatf/pipeline/captions.py`
- Test: `qatf-backend/tests/smoke_pipeline.py:132-160` (reshape) and new checks

**Interfaces:**
- Consumes: `textlayout.solve_line`, `textlayout.chunk_by_width`, `textlayout.load_measurer`, `captions.capsule_path`, `captions.ass_bgr`, `captions._clamp`.
- Produces: `captions.build_ass(clip, words, path, per_line, font, highlight, style: str = DEFAULT_CAPTION_STYLE, measurer=None) -> Path` and `captions.build_ass_youtube(clip, words, path, measurer, per_line, font) -> Path`. `build_ass` still returns only a `Path`; the style actually used is reported separately by `captions.resolve_style()` in Task 6. Do NOT add a module-level mutable to carry it — the render path is called from a thread pool.

- [ ] **Step 1: Write the failing test**

Append to `tests/smoke_pipeline.py`:

```python
section("captions: youtube pill style")

def dialogue_lines(body: str) -> list[list[str]]:
    """Each Dialogue split into its 10 header fields plus the text."""
    return [ln.split(",", 9) for ln in body.split("[Events]")[1].splitlines()
            if ln.startswith("Dialogue")]

_ltr = [Word("hello", 0.0, 0.4), Word("world", 0.4, 0.9)]
_ar = [Word("إحنا", 0.0, 0.4), Word("بنتكلم", 0.4, 0.9), Word("عن", 0.9, 1.2)]

_yp = captions.build_ass(Clip(0.0, 5.0, "t"), _ltr, Path("_tmp_yt.ass"),
                         style="youtube", measurer=_M)
_body = _yp.read_text(encoding="utf-8")
_dl = dialogue_lines(_body)

check("every word is positioned absolutely", all("\\pos(" in d[9] for d in _dl))
check("three events per word", len(_dl) == 3 * len(_ltr))
check("layers 0, 1 and 2 are all used",
      {d[0].split(":")[1].strip() for d in _dl} == {"0", "1", "2"})
check("the capsule is a drawing event",
      any("\\p1" in d[9] and "m " in d[9] for d in _dl))
check("the capsule uses the measured pill fill",
      any(captions.ass_bgr(K.PILL_FILL) in d[9] for d in _dl))
check("inactive words are dimmed",
      any(f"\\alpha&H{K.CAPTION_DIM_ALPHA:02X}&" in d[9] for d in _dl))
check("the active word drops its outline",
      any("\\bord0" in d[9] and "\\alpha" not in d[9] for d in _dl))
# The whole point of the feature: one word per event, so nothing can bidi-split.
for d in _dl:
    if "\\p1" in d[9]:
        continue
    _txt = re.sub(r"\{[^}]*\}", "", d[9])
    check(f"event carries exactly one word ({_txt!r})", " " not in _txt.strip())

# RTL: the first logical word must sit furthest RIGHT.
_ap = captions.build_ass(Clip(0.0, 5.0, "t"), _ar, Path("_tmp_yt_ar.ass"),
                         style="youtube", measurer=_M)
_xs = [float(m) for m in re.findall(r"\\pos\((\d+(?:\.\d+)?),", 
                                    _ap.read_text(encoding="utf-8"))]
check("arabic places the first word right of the last", _xs[0] > _xs[len(_ar) - 1],
      f"{_xs[0]} vs {_xs[len(_ar) - 1]}")

# The trust boundary is unchanged.
_evil = [Word("a\nDialogue: 0,0:00:00.00,0:00:01.00,Pop,,0,0,0,,pwned", 0.0, 0.5)]
_ep = captions.build_ass(Clip(0.0, 5.0, "t"), _evil, Path("_tmp_yt_evil.ass"),
                         style="youtube", measurer=_M)
check("a newline in word text cannot inject a Dialogue line",
      len(dialogue_lines(_ep.read_text(encoding="utf-8"))) == 3)

# `pop` must be untouched — every measured number depends on it.
_pop = captions.build_ass(Clip(0.0, 5.0, "t"), _ltr, Path("_tmp_pop.ass"),
                          style="pop")
check("pop emits no positioning and no drawing",
      "\\pos(" not in _pop.read_text(encoding="utf-8")
      and "\\p1" not in _pop.read_text(encoding="utf-8"))

# Spec risk 1: we own line layout but libass still DRAWS each word, so our
# measured advance has to match what it renders. That only holds while the Style
# line neither scales glyphs nor adds letter-spacing. If someone sets ScaleX to
# 105 or Spacing to 1 for a look, every capsule silently drifts off its word and
# nothing else in the suite would notice. Pin it.
_style_line = next(ln for ln in _body.splitlines() if ln.startswith("Style:"))
_f = _style_line.split(",")
check("ScaleX and ScaleY are 100 - our advances assume unscaled glyphs",
      (_f[11].strip(), _f[12].strip()) == ("100", "100"), _style_line)
check("Spacing is 0 - our advances assume no added letter-spacing",
      _f[13].strip() == "0", _style_line)

for _t in (_yp, _ap, _ep, _pop):
    _t.unlink(missing_ok=True)
```

- [ ] **Step 2: Reshape the existing disjointness check**

Replace `tests/smoke_pipeline.py:132-150` (the `section("caption cues must be disjoint")` loop). The invariant is now **per line, not per event** — on the pill path every word of a line is deliberately on screen at once, at different x positions. The bug the check was written for (two *lines* stacked) is still guarded; only the unit changes.

```python
section("caption cues must be disjoint")
# Per LINE, not per event. On the `youtube` style every word of a line is live
# simultaneously by design, at different x positions — that is the feature. The
# bug this check exists for is two LINES on screen at once, which libass stacks
# vertically, and that is still forbidden. Weakening it to "cues may overlap"
# would delete the check rather than update it.
def line_windows(body: str, style: str) -> list[tuple[float, float]]:
    cues = cue_times(body)
    if style == "pop":
        return cues
    # youtube: layer-0 events span the whole line, so the distinct layer-0
    # windows ARE the line windows.
    out = []
    for ln in body.split("[Events]")[1].splitlines():
        if ln.startswith("Dialogue: 0,"):
            f = ln.split(",", 10)
            def secs(t):
                h, m, rest = t.split(":")
                return int(h) * 3600 + int(m) * 60 + float(rest)
            w = (secs(f[1]), secs(f[2]))
            if w not in out:
                out.append(w)
    return out

for style in ("pop", "youtube"):
    for label, mk_words in (
        ("ltr", lambda: words(40)),
        ("rtl", lambda: [Word("مرحبا", i * 0.5, i * 0.5 + 0.45) for i in range(40)]),
        ("gapless speech", lambda: [Word("word", i * 0.3, i * 0.3 + 0.3)
                                    for i in range(40)]),
    ):
        _p = captions.build_ass(Clip(0.0, 30.0, "t"), mk_words(),
                                Path("_tmp_ov.ass"), style=style, measurer=_M)
        _w = line_windows(_p.read_text(encoding="utf-8"), style)
        _bad = [(a, b) for a, b in zip(_w, _w[1:], strict=False) if b[0] < a[1]]
        check(f"{style}/{label}: no two caption LINES are on screen together",
              not _bad, f"{len(_bad)} of {max(0, len(_w) - 1)} pairs overlap, "
                        f"e.g. {_bad[0] if _bad else ''}")
        check(f"{style}/{label}: every line still has a visible duration",
              all(e > s for s, e in _w), str([c for c in _w if c[1] <= c[0]][:3]))
        _p.unlink(missing_ok=True)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd qatf-backend && python tests/smoke_pipeline.py`
Expected: FAIL — `build_ass() got an unexpected keyword argument 'style'`

- [ ] **Step 4: Implement the youtube branch**

In `qatf-backend/qatf/pipeline/captions.py`, add the emitter and route `build_ass` to it:

```python
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
        CAPTION_DIM_ALPHA, CAPTION_SIDE_MARGIN, PILL_FILL, PILL_PAD_X, PILL_PAD_Y,
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
```

Then change `build_ass`'s signature and add the route at the top of its body:

```python
def build_ass(clip: Clip, words: list[Word], path: Path,
              per_line: int = CAPTION_MAX_WORDS, font: str = DEFAULT_FONT,
              highlight: bool | None = None,
              style: str = DEFAULT_CAPTION_STYLE, measurer=None) -> Path:
```

and immediately inside:

```python
    if style == "youtube":
        m = measurer if measurer is not None else \
            textlayout.load_measurer(safe_font(font), FONT_SIZE)
        if m is not None:
            return build_ass_youtube(clip, words, path, m, per_line, font)
        # No measurement available. Fall THROUGH to `pop` rather than raise —
        # see `resolve_style`. The caller is responsible for warning.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd qatf-backend && python tests/smoke_pipeline.py && ruff check .`
Expected: all PASS, including every pre-existing `pop` check unchanged.

- [ ] **Step 6: Commit**

```bash
git add qatf-backend/qatf/pipeline/captions.py qatf-backend/tests/smoke_pipeline.py
git commit -m "feat(captions): emit the youtube pill style with per-word positioning"
```

---

### Task 6: Fallback resolution and reporting

**Files:**
- Modify: `qatf-backend/qatf/pipeline/captions.py`
- Modify: `qatf-backend/qatf/jobs/model.py`, `qatf-backend/qatf/jobs/worker.py`
- Modify: `qatf-backend/qatf/api/routers/meta.py` (`/healthz`)
- Test: `qatf-backend/tests/smoke_pipeline.py`, `qatf-backend/tests/smoke_api.py`

**Interfaces:**
- Consumes: `textlayout.load_measurer`.
- Produces: `captions.resolve_style(style: str, font: str) -> tuple[str, str | None]` returning `(style_actually_used, warning_or_None)`. `Job.caption_style_used: str = ""`.

- [ ] **Step 1: Write the failing test**

Append to `tests/smoke_pipeline.py`:

```python
section("captions: style fallback and reporting")
_used, _warn = captions.resolve_style("pop", DEFAULT_FONT)
check("pop resolves to pop with no warning", (_used, _warn) == ("pop", None))

_used, _warn = captions.resolve_style("youtube", "NoSuchFamily\u0000Ever")
check("youtube on an unresolvable font falls back to pop", _used == "pop")
check("and the fallback is warned about, not silent", bool(_warn))
check("the warning names the reason and the fallback",
      _warn and "pop" in _warn and "youtube" in _warn, str(_warn))
check("the warning does not echo the caller's font name back",
      _warn and "NoSuchFamily" not in _warn, str(_warn))
raises("an unknown style is refused rather than silently defaulted",
       ValueError, captions.resolve_style, "sparkly", DEFAULT_FONT)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && python tests/smoke_pipeline.py`
Expected: FAIL with `AttributeError: module 'qatf.pipeline.captions' has no attribute 'resolve_style'`

- [ ] **Step 3: Implement `resolve_style`**

Add to `captions.py`:

```python
def resolve_style(style: str, font: str) -> tuple[str, str | None]:
    """The caption style that will actually be used, and a warning if it changed.

    A WARNING, NEVER A REFUSAL — the `font_warning` policy, not the
    `--device cuda` policy, and the project's own rule picks between them.
    `--device cuda` and `--reframe track` raise because they have no better
    alternative to fall back to. Here there is one, and it is a
    rendered-and-verified path; failing an hour-long job over a missing wheel
    would be the worse error.

    The returned style is what the caller must RECORD. A silent fallback that
    reports success is the failure mode this codebase keeps re-learning —
    `--language ar` reusing an English transcript, a cache key missing a knob,
    fc-match always returning something."""
    if style not in CAPTION_STYLES:
        raise ValueError(f"unknown caption style. Use one of "
                         f"{', '.join(CAPTION_STYLES)}")
    if style == "pop":
        return "pop", None
    if textlayout.load_measurer(safe_font(font), FONT_SIZE) is not None:
        return "youtube", None
    return "pop", (
        "the 'youtube' caption style needs shaped word measurement, which is "
        "unavailable on this host — either uharfbuzz is not installed "
        "(pip install 'qatf[captions]') or fontconfig cannot resolve the "
        "requested font family to a file. Falling back to the 'pop' style, "
        "which renders one caption line at a time with no pill.")
```

- [ ] **Step 4: Record and report the style**

In `qatf/jobs/model.py`, add to `Job` beside `device`:

```python
    #: caption style actually used, which is not always the one requested — see
    #: `captions.resolve_style`. Recorded for the same reason `device` is: a
    #: silent fallback that reports success is indistinguishable from the
    #: feature working. Empty on records written before this field existed.
    caption_style_used: str = ""
```

In `qatf/jobs/worker.py`, beside the existing `font_warning` call at line ~334:

```python
    style_used, style_note = pipeline.resolve_style(
        opts.get("caption_style", DEFAULT_CAPTION_STYLE), opts["font"])
    if style_note:
        log(f"      WARNING {style_note}")
    store.update(job_id, caption_style_used=style_used)
```

Export `resolve_style` from `qatf/pipeline/__init__.py` alongside `font_warning`.

In `/healthz` (`qatf/api/routers/meta.py`), add a `caption_pill_ready: bool` field computed as `textlayout.load_measurer(DEFAULT_FONT, FONT_SIZE) is not None`, so an operator can tell **before** submitting that the pill path will degrade on this host. Document it in the route docstring — `smoke_api.py` asserts every operation is described.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd qatf-backend && python tests/smoke_pipeline.py && python tests/smoke_api.py && ruff check .`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add qatf-backend/qatf/pipeline qatf-backend/qatf/jobs qatf-backend/qatf/api qatf-backend/tests
git commit -m "feat(captions): resolve and report the caption style actually used"
```

---

### Task 7: Wire the option through the API, CLI and encoder

**Files:**
- Modify: `qatf-backend/qatf/api/schemas.py:200` (beside `captions`/`per_line`)
- Modify: `qatf-backend/qatf/cli/parser.py:110`, `qatf-backend/qatf/cli/runner.py`
- Modify: `qatf-backend/qatf/pipeline/encode.py:313` (`render_all`)
- Test: `qatf-backend/tests/smoke_api.py`

**Interfaces:**
- Consumes: `resolve_style` (Task 6), `build_ass(..., style=)` (Task 5).
- Produces: `JobOptions.caption_style: Literal["youtube", "pop"]`; `render_all(..., caption_style: str = DEFAULT_CAPTION_STYLE)`; CLI `--caption-style`.

- [ ] **Step 1: Write the failing test**

Append to `tests/smoke_api.py`, in the input-validation section:

```python
check("caption_style defaults to youtube",
      JobOptions().caption_style == "youtube")
check("caption_style accepts pop", JobOptions(caption_style="pop").caption_style == "pop")
_r = client.post("/jobs", json={"video": str(VIDEO), "options": {"caption_style": "sparkly"}})
check("an unknown caption_style is refused at the boundary", _r.status_code == 422)
check("and the 422 does not echo the rejected value back",
      "sparkly" not in _r.text, _r.text[:200])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd qatf-backend && python tests/smoke_api.py`
Expected: FAIL — `JobOptions` has no attribute `caption_style`

- [ ] **Step 3: Add the field**

In `qatf/api/schemas.py`, directly after `per_line`:

```python
    caption_style: Literal["youtube", "pop"] = Field(
        DEFAULT_CAPTION_STYLE,
        description="how captions are drawn. `youtube` positions every word "
                    "absolutely and puts the word currently being spoken in a "
                    "filled capsule, on Arabic as well as Latin. `pop` is the "
                    "original style: one caption line at a time, with per-word "
                    "colouring on Latin only. `youtube` needs shaped word "
                    "measurement on the RENDERING host; where that is "
                    "unavailable the job falls back to `pop`, logs why, and "
                    "reports the style it actually used — check "
                    "`caption_pill_ready` on /healthz before submitting.",
    )
```

- [ ] **Step 4: Thread it through**

- `cli/parser.py`, beside `--per-line`:

```python
    ap.add_argument("--caption-style", choices=CAPTION_STYLES,
                    default=DEFAULT_CAPTION_STYLE,
                    help="youtube = per-word pill (needs qatf[captions]); "
                         "pop = one line at a time")
```

- `cli/runner.py`: pass `caption_style=args.caption_style` into `render_all`, and call `resolve_style` in `preflight`, logging the warning — the CLI must warn for the same reason the worker does.
- `encode.render_all`: add `caption_style: str = DEFAULT_CAPTION_STYLE`, and forward it to `build_ass(..., style=caption_style)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd qatf-backend && python tests/smoke_api.py && python tests/smoke_pipeline.py && ruff check .`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add qatf-backend/qatf qatf-backend/tests
git commit -m "feat(captions): expose caption_style on the API and CLI"
```

---

### Task 8: Web UI

**Files:**
- Modify: `qatf-frontend/src/api/types.ts`, `qatf-frontend/src/components/OptionsForm.tsx`, `qatf-frontend/src/pages/JobDetail.tsx`

**Interfaces:**
- Consumes: `JobOptions.caption_style`, `Job.caption_style_used`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Mirror the type**

In `src/api/types.ts`, add `caption_style?: "youtube" | "pop"` to the job-options type and `caption_style_used?: string` to the job type. This is a **declared mirror** of the wire contract, the same way the font default already is.

- [ ] **Step 2: Add the control**

In `OptionsForm.tsx`, add a select beside the font field with the two values, defaulting to `youtube`. Class names only — `styles.css` is the only stylesheet and components carry no inline styles.

- [ ] **Step 3: Report the fallback**

In `JobDetail.tsx`, where `device` is already shown, show `caption_style_used` when it differs from the requested `caption_style`. **A fallback the UI does not surface is a fallback the user discovers in the rendered clip** — the same reason `StageTimeline` refuses to draw a stage that never ran as completed.

- [ ] **Step 4: Verify**

Run: `cd qatf-frontend && npm test && npm run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add qatf-frontend/src
git commit -m "feat(ui): caption style control and fallback reporting"
```

---

### Task 9: Render and measure the pill sweep

**The check that actually proves the feature.** Everything above can pass while the pill sits on the wrong word.

**Files:**
- Modify: `qatf-backend/tests/verify_render.py`

- [ ] **Step 1: Write the measurement**

Add a fixture that renders a short clip in `youtube` style over a flat neutral background, extracts one frame per word, and tracks the **horizontal centroid of pill-coloured pixels** (`#B4560A` within a tolerance). Reuse the existing `raw_frame` / `red_centre` helpers' shape — `red_centre` at `verify_render.py:80` is the template; note it reads **bgr24**, and reading it as rgb24 has already produced a false "feature broken" result once.

```python
def pill_centre(video: Path, t: float) -> float | None:
    """Horizontal centre of the capsule, 0..1 across the frame, or None."""
    # same decode path as red_centre, matching on the pill fill instead
```

- [ ] **Step 2: Assert the sweep direction on both scripts**

```python
check("english: the pill sweeps left to right",
      all(b > a for a, b in zip(en_centres, en_centres[1:], strict=False)),
      str(en_centres))
# THE POINT OF THE FEATURE. If this sweeps left to right, the bidi handling is
# wrong and the feature is broken in exactly the way the original RTL bug was —
# invisible in the .ass file, invisible in a dimension check, visible only here.
check("arabic: the pill sweeps RIGHT TO LEFT",
      all(b < a for a, b in zip(ar_centres, ar_centres[1:], strict=False)),
      str(ar_centres))
```

- [ ] **Step 3: Add the control that must fail**

Every fixture in this file renders a control asserted to FAIL, because a control that cannot fail is measuring nothing — this harness has twice reported a broken *harness* as a broken feature.

```python
check("CONTROL — the pop style has no pill at all, proving the "
      "measurement is finding the capsule and not some other artefact",
      all(c is None for c in pop_centres), str(pop_centres))
```

- [ ] **Step 4: Confirm the traps are avoided**

Re-read the three false-pass traps in CLAUDE.md's RTL section before trusting a green run: do not read the `.ass` file as evidence, do not neutralise the pill colour, do not compare frames byte-for-byte. Render on a **flat neutral background** — a busy test pattern hid the highlight entirely on the first pass of the original measurement.

- [ ] **Step 5: Run it**

Run: `cd qatf-backend && python tests/verify_render.py`
Expected: PASS, with the control FAILING to find a pill on `pop`. Needs ffmpeg; honours `QATF_FFMPEG`.

- [ ] **Step 6: Extract a frame and look at it**

The working agreement is explicit and not satisfied by any assertion above: **render a clip and visually inspect an extracted frame**, in English and Arabic. Confirm the capsule sits under the right word, the padding clears the outline with no dark fringe, and Arabic letterforms stay connected.

- [ ] **Step 7: Commit**

```bash
git add qatf-backend/tests/verify_render.py
git commit -m "test: measure the pill sweep direction on LTR and RTL"
```

---

### Task 10: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/quality.md`, `docs/cli.md`, `docs/api.md`, `docs/troubleshooting.md`

- [ ] **Step 1: Update the numbers, in one place only**

Measured numbers live in `docs/quality.md` and `CLAUDE.md`, nowhere else — a number copied into a third place is a number that will disagree with itself. Record the contrast measurements and the re-measured `build_ass` timing from the spec's risk 3.

- [ ] **Step 2: Correct the RTL section of CLAUDE.md**

The section "The RTL caption bug" currently ends *"If word-level highlight on RTL ever becomes a requirement, it needs per-word `\pos` with measured text widths, or a renderer other than libass. Neither is warranted yet."* That is now done. Rewrite it to say the limitation applies to the `pop` style, that `youtube` resolves it with per-word `\pos`, and **keep the three false-pass traps** — they are still exactly right and cost a rendered frame each to find.

- [ ] **Step 3: Update the verification status**

Add what is now verified and what is not. Be honest: if the Arabic sweep has only been measured on a synthetic transcript, say so.

- [ ] **Step 4: Add a troubleshooting entry**

Symptom: "captions render as one line at a time even though I asked for the pill." Cause: measurement unavailable. Fix: `pip install 'qatf[captions]'`, check `caption_pill_ready` on `/healthz`, check the font family resolves with `fc-match`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs
git commit -m "docs: youtube pill caption style"
```

---

## Notes for the executor

- **Task 5 deliberately breaks a passing check.** Reshaping it to a per-line invariant is part of that task, not a regression to route around. Do not weaken it to "cues may overlap".
- **Verify the reshaped check can still fail.** Temporarily remove `_clamp`'s ceiling and confirm the disjointness check goes red. A check that cannot fail measures nothing — that is how the original overlap bug shipped.
- **`pop` must be byte-identical in behaviour.** Every measured number in the docs was taken against it.
- If Task 2 Step 1 finds an unacceptable licence, **stop and report** rather than substituting a different library — the whole approach depends on measuring with the engine libass shapes with.
