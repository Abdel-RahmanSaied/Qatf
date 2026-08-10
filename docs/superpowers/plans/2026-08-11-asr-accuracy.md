# Stage 2 Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stage-2 transcription defects measurable, then measurably reduce them — starting with ~15s of speech that collapses into a single token and freezes a caption for 16.6s.

**Architecture:** Defect *detection* goes in a new pipeline module `health.py`, consumed by two callers: a scorer (`tests/score_transcript.py`) that grades a transcript offline, and the read path that logs warnings during a real run. Repetition damage is repaired on read like `fixups`; timing damage is only ever flagged. Decode parameters move into a `DECODE` dict in `asr.py` with an override hook the GPU sweep uses.

**Tech Stack:** Python 3.14, faster-whisper ≥1.0 (CTranslate2), ffmpeg (via `QATF_FFMPEG`), no new dependencies.

## Global Constraints

- **No new dependencies.** The scorer uses `ffmpeg silencedetect`, not `webrtcvad` or `librosa`.
- **No new CLI flags** for decode parameters. Once measured they are product decisions.
- **The transcript cache is never rewritten.** All repair happens on read, so `words-*.json` keeps saying what Whisper actually produced.
- **Word count, positions and timings are preserved by every repair.** `edits.py`'s position-keyed overlay and the `PUT /jobs/{id}/transcript` contract depend on it.
- **Timing defects are flagged, never repaired.** Whisper owns acoustic time; `snap` trusts it completely.
- **Tests use `tests/_harness.py`** (`section`, `check`, `raises`, `report`) — this project deliberately does not use pytest.
- **Every metric is reported whole-file AND past-300s separately.** See the measurement trap in `docs/quality.md`.
- **This machine has no faster-whisper and no GPU.** Tasks 1–6 and 8 are fully testable locally. Task 7's sweep runs on the owner's GPU box.
- **Suites that must stay green:** smoke_pipeline 243, smoke_llm 38, smoke_api 125, load_api 23, verify_render 10, `ruff check .` clean.
- **Reference transcript for all local work:** `run-fixed/.work/words-large-v3-ar-6da1b0b4.json` (1390 words, 717.8s, `language=ar`, `device=cuda`).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `qatf/pipeline/health.py` (new) | Detect and repair transcript defects. One concern, like `fixups.py` and `edits.py`. |
| `qatf/pipeline/captions.py` (modify) | `group_words` skips blanked tokens |
| `qatf/jobs/worker.py` (modify) | Apply repair on the read path |
| `qatf/cli/runner.py` (modify) | Apply repair + log warnings on the read path |
| `qatf/pipeline/asr.py` (modify) | `DECODE` dict + `decode` override |
| `tests/fixtures/ar-terms.json` (new) | Tracked product-name fixture |
| `tests/score_transcript.py` (new) | The scorer |
| `tests/smoke_pipeline.py` (modify) | Checks for health + repair + DECODE |
| `docs/quality.md` (modify) | Experiment protocol and results |

---

### Task 1: Defect detection — `health.py`

**Files:**
- Create: `qatf/pipeline/health.py`
- Test: `tests/smoke_pipeline.py` (new section, appended before `raise SystemExit(report())`)

**Interfaces:**
- Consumes: `qatf.core.types.Word`
- Produces: `RepetitionRun(index, token, count, start, end)`, `TimingDefect(index, kind, text, start, end)`, `find_repetitions(words, min_run=4) -> list[RepetitionRun]`, `find_timing_defects(words, max_span=2.0) -> list[TimingDefect]`

- [ ] **Step 1: Write the failing checks**

Append to `tests/smoke_pipeline.py`, immediately before the final `raise SystemExit(report())`:

```python
section("transcript health — detection")
from qatf.pipeline import health  # noqa: E402

_rep = [Word("a", 0.0, 0.1), Word("x", 0.1, 0.2), Word("x", 0.2, 0.3),
        Word("x", 0.3, 0.4), Word("x", 0.4, 0.5), Word("b", 0.5, 0.6)]
_runs = health.find_repetitions(_rep)
check("a run of four identical tokens is found",
      len(_runs) == 1 and _runs[0].token == "x" and _runs[0].count == 4,
      str(_runs))
check("the run records where it starts, for the log line",
      _runs[0].index == 1 and abs(_runs[0].start - 0.1) < 1e-9)
check("three in a row is not a run — people do repeat themselves",
      health.find_repetitions([Word("x", 0.0, 0.1), Word("x", 0.1, 0.2),
                               Word("x", 0.2, 0.3)]) == [])
check("punctuation does not split a run",
      len(health.find_repetitions([Word("x", 0.0, 0.1), Word("x.", 0.1, 0.2),
                                   Word("x", 0.2, 0.3), Word("x", 0.3, 0.4)])) == 1)

_bad = [Word("ok", 0.0, 0.4), Word("zero", 1.0, 1.0), Word("long", 2.0, 20.0)]
_defects = health.find_timing_defects(_bad)
check("a zero-length word is a defect",
      any(d.kind == "zero" and d.index == 1 for d in _defects), str(_defects))
check("a word longer than the span limit is a defect",
      any(d.kind == "long" and d.index == 2 for d in _defects), str(_defects))
check("an ordinary word is not a defect",
      all(d.index != 0 for d in _defects))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/smoke_pipeline.py`
Expected: `ModuleNotFoundError: No module named 'qatf.pipeline.health'`

- [ ] **Step 3: Write the module**

Create `qatf/pipeline/health.py`:

```python
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
    """One word whose timing cannot be right. `kind` is zero | tiny | long."""

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tests/smoke_pipeline.py`
Expected: the seven new checks PASS, total 250 passed / 0 failed.

- [ ] **Step 5: Verify against the real transcript**

Run:

```bash
PYTHONIOENCODING=utf-8 python -c "
import json,pathlib
from qatf.core.types import words_from_dicts
from qatf.pipeline import health
w=words_from_dicts(json.loads(pathlib.Path('run-fixed/.work/words-large-v3-ar-6da1b0b4.json').read_text(encoding='utf-8'))['words'])
print('runs   :', [(r.token, r.count, round(r.start,1)) for r in health.find_repetitions(w)])
print('defects:', len(health.find_timing_defects(w)))
"
```

Expected: exactly one run, `count == 7`, starting at `242.0`; and **21** timing
defects — 12 `zero`, 2 `tiny`, 7 `long`.

The two `tiny` ones are `الـ` at 0.02s (t=69.2) and `ايه` at 0.04s (t=547.4).
Keep them classified: 20ms is below a single glottal pulse, so it cannot be a
spoken word — `الـ` is a proclitic Whisper split off with a near-zero span, which
is exactly the degenerate-timing case. `tiny_timings` is consumed by the scorer
in Tasks 4 and 6.

- [ ] **Step 6: Run lint and commit**

```bash
python -m ruff check .
git add qatf/pipeline/health.py tests/smoke_pipeline.py
git commit -m "feat(asr): detect repetition loops and degenerate word timings"
```

---

### Task 2: Repair repetition runs, flag timings

**Files:**
- Modify: `qatf/pipeline/health.py` (append)
- Modify: `qatf/pipeline/captions.py:53-68` (`group_words`)
- Test: `tests/smoke_pipeline.py` (append to the health section)

**Interfaces:**
- Consumes: `find_repetitions`, `find_timing_defects` from Task 1
- Produces: `repair(words, min_run=MIN_REPEAT_RUN) -> tuple[list[Word], int]`, `warnings(words) -> list[str]`

- [ ] **Step 1: Write the failing checks**

Append to the health section in `tests/smoke_pipeline.py`:

```python
_src = [Word("a", 0.0, 0.1), Word("x", 0.1, 0.2), Word("x", 0.2, 0.3),
        Word("x", 0.3, 0.4), Word("x", 0.4, 0.5), Word("b", 0.5, 0.6)]
_times = [(w.start, w.end) for w in _src]
_fixed, _n = health.repair(_src)
check("the duplicates are blanked, the first is kept",
      [w.text for w in _fixed] == ["a", "x", "", "", "", "b"], str([w.text for w in _fixed]))
check("three duplicates were blanked", _n == 3, str(_n))
check("REPAIR PRESERVES THE WORD COUNT — edits.py is keyed by position",
      len(_fixed) == len(_times))
check("REPAIR NEVER TOUCHES A TIMING — snap anchors cuts on these",
      [(w.start, w.end) for w in _fixed] == _times)
check("a clean transcript is left alone",
      health.repair([Word("a", 0.0, 0.1), Word("b", 0.1, 0.2)])[1] == 0)

# A LONG word, not a repetition run. `warnings()` reports only what repair does
# NOT fix — repetition runs are repaired, and their count is logged separately
# by the caller. The original version of this check passed a repetition run to a
# timing-warning function, which forced `warnings()` to grow a repetition branch
# that is then unreachable: `repair()` mutates in place, so by the time the CLI
# calls `warnings()` those tokens are blank.
check("warnings name the timestamp so the clip can be inspected",
      any("242.0" in s for s in health.warnings([Word("x", 242.0, 258.0)])))
check("warnings do NOT re-report a repetition run — repair owns those, and the "
      "caller logs how many it blanked",
      health.warnings([Word("x", i / 10, 0.1 + i / 10) for i in range(6)]) == [])

check("captions skip a blanked token instead of emitting an empty word",
      [[w.text for w in line] for line in
       captions.group_words([Word("PHP", 0, 1), Word("", 1, 2), Word("ماتت", 2, 3)])]
      == [["PHP", "ماتت"]])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/smoke_pipeline.py`
Expected: FAIL — `module 'qatf.pipeline.health' has no attribute 'repair'`

- [ ] **Step 3: Append to `health.py`**

```python
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
    """Human-readable flags for defects that are NOT repaired.

    Timing damage is surfaced rather than fixed, so the operator can go and look
    at the clip edges there. Silence would be the wrong answer: a 15s word is
    also a 15s caption, and it will not announce itself in a diff."""
    out: list[str] = []
    for d in find_timing_defects(words):
        if d.kind == "long":
            out.append(f"word {d.index} ({d.text!r}) spans "
                       f"{d.end - d.start:.1f}s at {d.start:.1f}s — the caption "
                       f"holds that long and snap may anchor a cut on it")
    zero = [d for d in find_timing_defects(words) if d.kind in ("zero", "tiny")]
    if zero:
        where = ", ".join(f"{d.start:.1f}s" for d in zero[:5])
        out.append(f"{len(zero)} word(s) have a zero or near-zero duration "
                   f"({where}{', …' if len(zero) > 5 else ''})")
    return out
```

- [ ] **Step 4: Make `group_words` skip blanked tokens**

In `qatf/pipeline/captions.py`, replace the loop header at line 60:

```python
    for w in words:
        if not w.text:
            # a token blanked by health.repair — it keeps its index and timings
            # so the overlay stays aligned, but it must not reach a caption
            continue
        projected = sum(len(x.text) for x in cur) + len(cur) + len(w.text)
```

- [ ] **Step 5: Run to verify it passes**

Run: `python tests/smoke_pipeline.py`
Expected: all new checks PASS, 258 passed / 0 failed.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check .
git add qatf/pipeline/health.py qatf/pipeline/captions.py tests/smoke_pipeline.py
git commit -m "feat(asr): repair repetition loops on read, flag timing defects"
```

---

### Task 3: Wire repair and warnings into the read path

**Files:**
- Modify: `qatf/cli/runner.py:108-112`
- Modify: `qatf/jobs/worker.py:27-36` (`baseline_words`)
- Test: `tests/smoke_api.py` (append to the transcript-correction section)

**Interfaces:**
- Consumes: `health.repair`, `health.warnings` from Task 2
- Produces: no new API; repair becomes part of what every reader sees

Repair goes in `baseline_words`, *after* fixups. That placement is what keeps
`GET /jobs/{id}/transcript`, the `PUT` diff baseline and the burned-in captions
all showing the same text — putting it only in `caption_words` would make the
captions disagree with the transcript endpoint.

- [ ] **Step 1: Write the failing check**

Append to `tests/smoke_api.py` in the `transcript correction round trip` section:

```python
    from qatf.core.types import Word as _W
    from qatf.jobs import worker as _worker

    class _T:
        words = [_W("a", 0.0, 0.1)] + [_W("dup", 0.1 + i / 10, 0.2 + i / 10)
                                       for i in range(5)]
    _base = _worker.baseline_words(_T(), {})
    check("repair reaches the read path, so captions and GET /transcript agree",
          [w.text for w in _base] == ["a", "dup", "", "", "", ""],
          str([w.text for w in _base]))
    check("and the word count the PUT contract depends on is unchanged",
          len(_base) == 6)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/smoke_api.py`
Expected: FAIL — text is `["a","dup","dup","dup","dup","dup"]`

- [ ] **Step 3: Add repair to `baseline_words`**

In `qatf/jobs/worker.py`, replace the body of `baseline_words`:

```python
def baseline_words(transcript, opts: dict) -> list[Word]:
    """Transcript words with the job's fixups and repairs applied, and nothing
    else.

    This is what a per-word correction is diffed against, so it must be what the
    caller was shown minus their own corrections — see `api.routers.plan`.
    Repair belongs here rather than in `caption_words` for that reason: put it
    downstream and the burned-in captions would disagree with what
    `GET /jobs/{id}/transcript` returns."""
    words = transcript.words
    mapping = opts.get("fixups") or {}
    if mapping:
        words, _ = pipeline.fixups.apply(words, mapping)
    words, _ = pipeline.health.repair(words)
    return words
```

- [ ] **Step 4: Export `health` from the pipeline package**

In `qatf/pipeline/__init__.py`, add `health` to the module imports beside `fixups` and `edits` so `pipeline.health` resolves.

- [ ] **Step 5: Add repair and warnings to the CLI**

In `qatf/cli/runner.py`, after the fixups block (line ~112), insert:

```python
    words, blanked = pipeline.health.repair(words)
    if blanked:
        log(f"      blanked {blanked} looped token(s) — the decoder repeated "
            f"itself; timings and word count are untouched")
    for note in pipeline.health.warnings(words):
        log(f"      NOTE {note}")
```

- [ ] **Step 6: Run to verify it passes**

Run: `python tests/smoke_api.py` then `python tests/smoke_pipeline.py`
Expected: both green; smoke_api 127 passed / 0 failed.

- [ ] **Step 7: Verify on the real transcript end to end**

Run the CLI against the seeded work directory and confirm the log now names the loop and the 15.4s word:

```bash
python -m qatf "E:\Youtube content\متسلمش دماغك\متسلمش دماغك.mov" \
  -o E:\Qutf\run-health --plan E:\Qutf\run-fixed\plan-input.json \
  --language ar --vocab-file prompts\ar-tech.txt --fixups prompts\ar-fixups.txt \
  --denoise --plan-only
```

(Seed `run-health/.work` from `run-fixed/.work` first, as in earlier runs.)
Expected: a `blanked 6 looped token(s)` line and `NOTE word ... spans 15.4s`.

- [ ] **Step 8: Lint and commit**

```bash
python -m ruff check .
git add qatf/cli/runner.py qatf/jobs/worker.py qatf/pipeline/__init__.py tests/smoke_api.py
git commit -m "feat(asr): apply repair and surface timing warnings on the read path"
```

---

### Task 4: Tracked-term fixture and the scorer's text metrics

**Files:**
- Create: `tests/fixtures/ar-terms.json`
- Create: `tests/score_transcript.py`

**Interfaces:**
- Consumes: `health.find_repetitions`, `health.find_timing_defects`
- Produces: `score(words, terms, since=0.0) -> dict`, `load_terms(path) -> list[dict]`

`baseline_total` in the fixture is what makes false positives detectable without
a reference: a correct fix converts wrong spellings into right ones, so the
total occurrences of a concept should stay about constant. A term appearing more
often than its baseline total is a term the vocabulary invented.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/ar-terms.json`:

```json
{
  "comment": "Product names measured wrong on words-large-v3-ar-6da1b0b4.json. baseline_total is right+wrong occurrences in that file; a run exceeding it has invented the term.",
  "terms": [
    {"expected": "PHP",        "wrong": ["البيتش", "بيتش"],        "baseline_total": 3},
    {"expected": "جافا سكريبت", "wrong": ["الجفا", "سيكلت"],        "baseline_total": 2},
    {"expected": "هايرنج",      "wrong": ["هيرنج"],                 "baseline_total": 1},
    {"expected": "جونيورز",     "wrong": ["نيورز", "وناشوا"],       "baseline_total": 2},
    {"expected": "سينيورز",     "wrong": ["يورز", "سن"],            "baseline_total": 3},
    {"expected": "لينكد إن",    "wrong": ["الليك"],                 "baseline_total": 1},
    {"expected": "إكس",         "wrong": ["اكسة", "اكس"],           "baseline_total": 3}
  ]
}
```

- [ ] **Step 2: Write the scorer's core**

Create `tests/score_transcript.py`:

```python
"""Score a transcript for the defects stage 2 actually produces.

    python tests/score_transcript.py <words.json> [--audio W.wav] [--baseline B.json]

Reference-free by design. A hand-corrected transcript is the gold standard and
is being produced separately, but it cannot gate day-to-day tuning: every metric
here runs in seconds on any transcript and catches a defect that was measured by
hand on real material.

The point of the coverage and word-count metrics is that they are the GUARD on
the others. The specific way a hallucination fix fails is by deleting real
speech, which improves every other number on the page.

Everything is reported whole-file and past 300s separately. See the measurement
trap in docs/quality.md: a 70s slice starting where a seed was applied once
reported 14 errors going to 0, and the same audio 6.6 minutes in had them back.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _harness import ROOT  # noqa: F401  — puts the project root on sys.path

from qatf.core.types import Word, words_from_dicts
from qatf.pipeline import health

#: Errors before this point can be flattered by a seed that decays. Reported
#: separately for exactly that reason.
DECAY_SECONDS = 300.0


def load_terms(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["terms"]


def load_words(path: Path) -> list[Word]:
    return words_from_dicts(json.loads(path.read_text(encoding="utf-8"))["words"])


def score(words: list[Word], terms: list[dict], since: float = 0.0) -> dict:
    """Every metric for the words at or after `since`."""
    sel = [w for w in words if w.start >= since]
    span = (sel[-1].end - sel[0].start) if sel else 0.0
    runs = health.find_repetitions(sel)
    defects = health.find_timing_defects(sel)
    text = [w.text.strip().strip(".،") for w in sel]

    term_rows = []
    for t in terms:
        right = sum(1 for x in text if x == t["expected"]) or \
            sum(1 for i in range(len(text)) if " ".join(text[i:i + 2]) == t["expected"])
        wrong = sum(1 for x in text if x in t["wrong"])
        term_rows.append({"expected": t["expected"], "right": right, "wrong": wrong,
                          "invented": max(0, right - t["baseline_total"])})

    return {
        "words": len(sel),
        "span_s": round(span, 1),
        "wpm": round(60 * len(sel) / span, 1) if span else 0.0,
        "longest_run": max((r.count for r in runs), default=0),
        "looped_tokens": sum(r.count - 1 for r in runs),
        "zero_timings": sum(1 for d in defects if d.kind == "zero"),
        "tiny_timings": sum(1 for d in defects if d.kind == "tiny"),
        "long_timings": sum(1 for d in defects if d.kind == "long"),
        "max_word_span": round(max((d.end - d.start for d in defects
                                    if d.kind == "long"), default=0.0), 2),
        "terms": term_rows,
        "terms_right": sum(r["right"] for r in term_rows),
        "terms_wrong": sum(r["wrong"] for r in term_rows),
        "terms_invented": sum(r["invented"] for r in term_rows),
    }
```

- [ ] **Step 3: Verify the metrics against the known-bad transcript**

Run:

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys, pathlib; sys.path.insert(0, 'tests')
from score_transcript import load_words, load_terms, score
w = load_words(pathlib.Path('run-fixed/.work/words-large-v3-ar-6da1b0b4.json'))
s = score(w, load_terms(pathlib.Path('tests/fixtures/ar-terms.json')))
print({k: v for k, v in s.items() if k != 'terms'})
"
```

Expected exactly: `words 1390`, `longest_run 7`, `looped_tokens 6`,
`zero_timings 12`, `tiny_timings 2`, `long_timings 7`, `max_word_span 15.42`,
`terms_wrong` ≥ 10, `terms_invented 0`.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/ar-terms.json tests/score_transcript.py
git commit -m "feat(tests): reference-free transcript scorer, text and timing metrics"
```

---

### Task 5: Uncovered-speech metric via ffmpeg

**Files:**
- Modify: `tests/score_transcript.py` (append)

**Interfaces:**
- Consumes: `QATF_FFMPEG` via `qatf.core.utils.binary`
- Produces: `speech_intervals(wav, noise_db=-30.0, min_silence=0.5) -> list[tuple[float, float]]`, `uncovered_speech(words, speech) -> list[tuple[float, float]]`

This is the metric that catches the headline defect: 15s of audio at −17.5 dB
with a single token over it. It uses `silencedetect` because ffmpeg is already a
hard requirement and a VAD library would not be.

- [ ] **Step 1: Append to `tests/score_transcript.py`**

```python
import subprocess

from qatf.core.utils import binary


def speech_intervals(wav: Path, noise_db: float = -30.0,
                     min_silence: float = 0.5) -> list[tuple[float, float]]:
    """Speech spans, as the complement of ffmpeg's detected silences.

    ffmpeg rather than a VAD package: it is already a hard dependency, and this
    is a measurement tool — the no-new-dependencies rule applies to it as much
    as to the pipeline."""
    out = subprocess.run(
        [binary("ffmpeg"), "-hide_banner", "-nostats", "-i", str(wav),
         "-af", f"silencedetect=n={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    silences: list[tuple[float, float]] = []
    start = None
    for line in out.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            silences.append((start, float(line.split("silence_end:")[1].split()[0])))
            start = None
    duration = _probe_duration(wav)
    speech, cursor = [], 0.0
    for a, b in silences:
        if a > cursor:
            speech.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < duration:
        speech.append((cursor, duration))
    return speech


def _probe_duration(wav: Path) -> float:
    from qatf.core.utils import probe_duration
    return probe_duration(wav) or 0.0


def uncovered_speech(words: list[Word], speech: list[tuple[float, float]],
                     min_gap: float = 2.0) -> list[tuple[float, float]]:
    """Speech spans with no word over them for at least `min_gap` seconds.

    A word that spans 15s counts as covering only where it starts and ends, not
    the void between — that void is precisely the defect."""
    edges = sorted({w.start for w in words} | {w.end for w in words})
    gaps: list[tuple[float, float]] = []
    for a, b in speech:
        cursor = a
        for t in edges:
            if t <= cursor:
                continue
            if t >= b:
                break
            if t - cursor >= min_gap:
                gaps.append((cursor, t))
            cursor = t
        if b - cursor >= min_gap:
            gaps.append((cursor, b))
    return gaps
```

- [ ] **Step 2: Verify it finds the measured window**

Run:

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys, pathlib; sys.path.insert(0, 'tests')
from score_transcript import load_words, speech_intervals, uncovered_speech
w = load_words(pathlib.Path('run-fixed/.work/words-large-v3-ar-6da1b0b4.json'))
sp = speech_intervals(pathlib.Path('run-fixed/.work/audio-denoised.wav'))
gaps = uncovered_speech(w, sp)
print('total uncovered %.1fs' % sum(b-a for a,b in gaps))
for a,b in sorted(gaps, key=lambda g: g[1]-g[0], reverse=True)[:3]:
    print('  %.1f-%.1fs  (%.1fs)' % (a, b, b-a))
"
```

Expected: the largest gap lands inside **424–440s** and is ≥ 10s — the `آآ`
window. If it does not, the metric is wrong and must be fixed before any tuning
begins; it is the guard on every later experiment.

- [ ] **Step 3: Commit**

```bash
git add tests/score_transcript.py
git commit -m "feat(tests): uncovered-speech metric via ffmpeg silencedetect"
```

---

### Task 6: Scorer CLI, past-300s split, and baseline diff

**Files:**
- Modify: `tests/score_transcript.py` (append the `__main__` block)

**Interfaces:**
- Consumes: everything from Tasks 4 and 5
- Produces: the command-line contract in the module docstring; exit 1 on regression against `--baseline`

- [ ] **Step 1: Append the entry point**

```python
def _fmt(s: dict, label: str) -> str:
    return (f"  {label:<12} words {s['words']:>5}  wpm {s['wpm']:>5}  "
            f"run {s['longest_run']:>2}  looped {s['looped_tokens']:>3}  "
            f"zero {s['zero_timings']:>3}  long {s['long_timings']:>3}  "
            f"maxspan {s['max_word_span']:>6}  "
            f"terms {s['terms_right']}✓/{s['terms_wrong']}✗"
            f"{'  INVENTED ' + str(s['terms_invented']) if s['terms_invented'] else ''}")


#: Metrics where a HIGHER number is worse. `words` is deliberately absent — a
#: drop in word count is the failure this scorer exists to catch, and it is
#: checked separately below.
_WORSE_IF_UP = ("longest_run", "looped_tokens", "zero_timings", "tiny_timings",
                "long_timings", "max_word_span", "terms_wrong", "terms_invented")

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    path = Path(args[0])
    audio = Path(args[args.index("--audio") + 1]) if "--audio" in args else None
    base = Path(args[args.index("--baseline") + 1]) if "--baseline" in args else None

    words = load_words(path)
    terms = load_terms(Path(__file__).resolve().parent / "fixtures" / "ar-terms.json")
    whole = score(words, terms)
    late = score(words, terms, since=DECAY_SECONDS)

    print(f"\n{path.name}")
    print(_fmt(whole, "whole file"))
    print(_fmt(late, f"past {DECAY_SECONDS:.0f}s"))

    if audio:
        gaps = uncovered_speech(words, speech_intervals(audio))
        total = sum(b - a for a, b in gaps)
        worst = max((b - a for a, b in gaps), default=0.0)
        print(f"  uncovered speech  {total:.1f}s total, worst window {worst:.1f}s")
        for a, b in sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:3]:
            print(f"      {a:7.1f}-{b:7.1f}s  ({b - a:.1f}s)")

    print("\n  per term: " + ", ".join(
        f"{r['expected']} {r['right']}✓/{r['wrong']}✗" for r in whole["terms"]))

    if base:
        prev = score(load_words(base), terms)
        regressions = [k for k in _WORSE_IF_UP if whole[k] > prev[k]]
        if whole["words"] < prev["words"] * 0.97:
            regressions.append(f"words {prev['words']} -> {whole['words']}")
        print(f"\n  vs {base.name}: "
              + ("REGRESSED on " + ", ".join(regressions) if regressions
                 else "no regression"))
        raise SystemExit(1 if regressions else 0)
    raise SystemExit(0)
```

- [ ] **Step 2: Run it on the real transcript**

Run:

```bash
python tests/score_transcript.py run-fixed/.work/words-large-v3-ar-6da1b0b4.json \
  --audio run-fixed/.work/audio-denoised.wav
```

Expected: two metric rows, an uncovered-speech section naming a window in
424–440s, and a per-term line. Exit code 0.

- [ ] **Step 3: Prove the regression gate can fail**

A gate that cannot fail is measuring nothing — the rule `verify_render.py`
enforces by asserting its `crop` control fails. Build a deliberately worse
transcript and confirm exit 1:

`tempfile.gettempdir()`, not a hardcoded `/tmp` — this repo's own host is
Windows, where `/tmp` silently creates a stray `C:\tmp`:

```bash
PYTHONIOENCODING=utf-8 python -c "
import json, pathlib, tempfile
p = pathlib.Path('run-fixed/.work/words-large-v3-ar-6da1b0b4.json')
d = json.loads(p.read_text(encoding='utf-8'))
d['words'] = d['words'][:600]          # a config that 'wins' by transcribing less
out = pathlib.Path(tempfile.gettempdir()) / 'qatf-worse.json'
out.write_text(json.dumps(d), encoding='utf-8')
print(out)
"
python tests/score_transcript.py "$(python -c "import tempfile,pathlib;print(pathlib.Path(tempfile.gettempdir())/'qatf-worse.json')")" \
  --baseline run-fixed/.work/words-large-v3-ar-6da1b0b4.json
echo "exit=$?"
```

Expected: `REGRESSED on words 1390 -> 600` and `exit=1`.

- [ ] **Step 4: Lint and commit**

```bash
python -m ruff check .
git add tests/score_transcript.py
git commit -m "feat(tests): scorer CLI with past-300s split and regression gate"
```

---

### Task 7: `DECODE` parameters and the override hook

**Files:**
- Modify: `qatf/pipeline/asr.py:196-213` (`_transcribe_on`), `:253-256` (`transcribe`), `:341-353` (`transcribe_cached`)
- Test: `tests/smoke_pipeline.py` (append to the health section)

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `asr.DECODE: dict`, and `decode: dict | None` threaded through `transcribe_cached` → `transcribe` → `_transcribe_on`

- [ ] **Step 1: Write the failing checks**

```python
section("decode parameters — stage 2")
check("DECODE carries the VAD settings so a sweep has one place to change",
      "vad_parameters" in asr.DECODE and "min_silence_duration_ms" in asr.DECODE["vad_parameters"])
_merged = asr.merge_decode({"beam_size": 9})
check("an override merges over the defaults", _merged["beam_size"] == 9)
check("and leaves the rest intact",
      _merged["vad_parameters"] == asr.DECODE["vad_parameters"])
check("merging does not mutate DECODE itself",
      "beam_size" not in asr.DECODE or asr.DECODE.get("beam_size") != 9)
_nested = asr.merge_decode({"vad_parameters": {"speech_pad_ms": 200}})
check("a nested override merges rather than replacing the whole dict",
      _nested["vad_parameters"]["min_silence_duration_ms"]
      == asr.DECODE["vad_parameters"]["min_silence_duration_ms"]
      and _nested["vad_parameters"]["speech_pad_ms"] == 200)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python tests/smoke_pipeline.py`
Expected: FAIL — `module 'qatf.pipeline.asr' has no attribute 'DECODE'`

- [ ] **Step 3: Add `DECODE` and `merge_decode` to `asr.py`**

Insert above `_transcribe_on`:

```python
#: Decode parameters, in one place so a sweep changes one thing at a time.
#:
#: EVERY VALUE HERE IS THE CURRENT BEHAVIOUR, NOT A MEASURED OPTIMUM. The only
#: non-default entry is min_silence_duration_ms, which this project set to 500
#: for speed; faster-whisper's default under vad_filter is 160, and a higher
#: value merges across short pauses into longer chunks (capped at
#: max_speech_duration_s = 30s). A 30s window of noisy audio that decodes to
#: almost nothing is how 15 seconds of speech became one `آآ` token. That is
#: hypothesis #1 in docs/quality.md, not a conclusion.
DECODE: dict = {
    "vad_filter": True,
    "vad_parameters": {"min_silence_duration_ms": 500},
}


def merge_decode(overrides: dict | None) -> dict:
    """DECODE with `overrides` merged one level deep, leaving DECODE untouched.

    One level is enough — `vad_parameters` is the only nested key — and a deeper
    merge would hide which knob a sweep actually moved."""
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DECODE.items()}
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged
```

- [ ] **Step 4: Thread `decode` through the three functions**

In `_transcribe_on`, replace the `vad_filter` / `vad_parameters` arguments with
the merged dict, and add the parameter:

```python
def _transcribe_on(wav: Path, model_size: str, device: str,
                   language: str | None,
                   initial_prompt: str | None = None,
                   hotwords: str | None = None,
                   decode: dict | None = None) -> Transcript:
```

and in the `model.transcribe(...)` call replace the two VAD lines with:

```python
        **merge_decode(decode),
```

Add `decode: dict | None = None` to `transcribe` and `transcribe_cached` with
the same default, forwarding it at each call site. `transcribe_cached` must
**not** add `decode` to the cache key — see the open question in the spec.

- [ ] **Step 5: Run to verify it passes**

Run: `python tests/smoke_pipeline.py` and `python tests/smoke_api.py`
Expected: both green.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check .
git add qatf/pipeline/asr.py tests/smoke_pipeline.py
git commit -m "feat(asr): collect decode parameters into DECODE with an override hook"
```

---

### Task 8: The experiment protocol in `docs/quality.md`

**Files:**
- Modify: `docs/quality.md` (new section after `### Tested and rejected`)
- Modify: `CLAUDE.md` (Layout tree gains `health.py`; Commands gains the scorer)

**Interfaces:** documentation only.

- [ ] **Step 1: Add the protocol section to `docs/quality.md`**

Outer fence is four backticks so the inner one does not close it early:

````markdown
### The stage-2 sweep — how to run one

Every run changes ONE thing and is scored the same way:

```bash
python tests/score_transcript.py <new>.json --audio <wav> --baseline <prev>.json
```

Record the result here whether it wins or loses. The table above exists because
three plausible changes were worse, and writing them down is what stops them
being retried.

| Hypothesis | Parameter | Target defect | Result |
| --- | --- | --- | --- |
| Our 500ms silence threshold merges chunks to 30s and starves the decoder | `vad_parameters.min_silence_duration_ms` 500 → 160 | 15s of speech in one token | *pending* |
| Padding changes where word boundaries land | `vad_parameters.speech_pad_ms` | degenerate timings | *pending* |
| A bad window never triggers temperature fallback | `compression_ratio_threshold`, `log_prob_threshold` | dropped speech | *pending* |
| The extended vocabulary fixes product names | `--vocab-file prompts/ar-tech.txt` (38 terms) | PHP, JavaScript, seniors | *pending* |
| N-gram blocking breaks the loop | `no_repeat_ngram_size=3` | `ومكتفي` ×7 | *pending* |
| Penalising repeats breaks the loop more gently | `repetition_penalty` 1.05 / 1.10 | `ومكتفي` ×7 | *pending* |
| Anomalous segments in silence are hallucinations | `hallucination_silence_threshold=2.0` | invented proper nouns | *pending* |

**`hallucination_silence_threshold` cannot fix the 15s `آآ`.** It only skips
segments surrounded by silence longer than the threshold, and that window holds
speech at −17.5 dB against a −18.7 dB reference. Listed against the invented
nouns instead. Established by reading faster-whisper's implementation, before
spending a GPU run on it.
````

Replace each *pending* as runs complete. A row that loses keeps its result.

- [ ] **Step 2: Update `CLAUDE.md`**

Add to the Layout tree beside `edits.py`:

```text
    health.py      2d. loop repair + timing flags  text only, never timestamps
```

Add to Commands, beside the other suites:

```bash
python tests/score_transcript.py <words.json> --audio <wav>   # grade a transcript
```

- [ ] **Step 3: Verify the docs match reality**

Run: `python tests/smoke_pipeline.py && python -m ruff check .`
Expected: green. Re-read the new `docs/quality.md` section and confirm every
parameter name in it appears in `asr.DECODE` or is a documented faster-whisper
argument.

- [ ] **Step 4: Commit**

```bash
git add docs/quality.md CLAUDE.md
git commit -m "docs: stage-2 sweep protocol and the eliminated hallucination hypothesis"
```

---

## After the plan: the GPU sweep

Tasks 1–8 need no GPU. The sweep itself runs on the owner's box:

1. `pip install -e ".[all]"` there, confirm `python tests/smoke_pipeline.py` is green.
2. For each row in the protocol table, edit `DECODE` (or pass `decode=` from a
   short driver script), transcribe the same wav, write to a distinctly named
   json, and score it against the previous best.
3. Paste the scorer output back; the winning values get baked into `DECODE` with
   the measured effect in the comment, and the table row gets its result.
4. Once a winning set exists, decide the open question: whether decode
   parameters join the transcript cache key.

---

## Self-Review

**Spec coverage.** Component 1 (scorer) → Tasks 4, 5, 6. Component 2 (DECODE) →
Task 7. Component 3 (repair and flag) → Tasks 1, 2, 3. Component 4 (protocol) →
Task 8. Success criteria 1–5 are all computed by `score()` or the uncovered-speech
metric; criterion 6 is Task 8. The ground-truth track is the owner's and needs no
task. Both spec open questions are carried into Task 7 Step 4 and the sweep
section rather than silently dropped.

**Placeholders.** The *pending* markers in the protocol table are results not yet
measured, which is the table's purpose; every one names its parameter and target
defect. No TBDs elsewhere.

**Type consistency.** `find_repetitions`/`find_timing_defects` (Task 1) are used
under those exact names in Tasks 2, 4 and 5. `repair` returns
`(words, blanked)` in Task 2 and is unpacked that way in Task 3.
`score(words, terms, since)` in Task 4 is called with those arguments in Task 6.
`speech_intervals`/`uncovered_speech` in Task 5 match their Task 6 call sites.
`merge_decode(overrides)` in Task 7 matches its checks.
