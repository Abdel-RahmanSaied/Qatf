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

The `--audio`/`--baseline` flags and the argv entry point above are Tasks 5-6
(an audio-coverage metric, then the CLI). Until those land this module is a
library — `load_terms`, `load_words`, `score` — called directly, the same
kind of forward reference `pipeline/health.py`'s own docstring makes to this
file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from _harness import ROOT  # noqa: F401  — puts the project root on sys.path

from qatf.core.errors import CommandFailed
from qatf.core.types import Word, words_from_dicts
from qatf.core.utils import binary, probe_duration
from qatf.pipeline import health
from qatf.pipeline.health import _TRAILING

#: Errors before this point can be flattered by a seed that decays. Reported
#: separately for exactly that reason.
DECAY_SECONDS = 300.0


def load_terms(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["terms"]


def load_words(path: Path) -> list[Word]:
    return words_from_dicts(json.loads(path.read_text(encoding="utf-8"))["words"])


def _count_expected(text: list[str], expected: str) -> int:
    """How many times `expected` occurs in `text`.

    A single-token expected value (`"PHP"`) is matched token for token. A
    multi-word expected value (`"جافا سكريبت"`) can never equal one token —
    Whisper emits one token per word — so it has to be matched against
    consecutive windows of `text` instead. Branching on whether `expected`
    contains a space, rather than summing a single-token pass `or` a window
    pass (as an earlier draft did), means exactly one of the two ever runs:
    there is no case where a real occurrence could be counted by both and no
    case where an `or` silently discards a nonzero window count because a
    stray single-token match, unrelated to this term, made the left side
    truthy.
    """
    parts = expected.split(" ")
    width = len(parts)
    if width == 1:
        return sum(1 for x in text if x == expected)
    return sum(1 for i in range(len(text) - width + 1) if text[i:i + width] == parts)


def score(words: list[Word], terms: list[dict], since: float = 0.0) -> dict:
    """Every metric for the words at or after `since`."""
    sel = [w for w in words if w.start >= since]
    span = (sel[-1].end - sel[0].start) if sel else 0.0
    runs = health.find_repetitions(sel)
    defects = health.find_timing_defects(sel)
    # Strip with health._TRAILING, not a hand-picked ".،": the scorer used to
    # strip only ".،", so a tracked term followed by "؟", "؛", "!" or ":"
    # never matched and terms_right understated real accuracy. _TRAILING is
    # what the pipeline itself strips (health.py, shared with fixups._TRAILING)
    # — using anything narrower here means the scorer and the pipeline
    # disagree about what a "word" is.
    text = [w.text.strip().rstrip(_TRAILING) for w in sel]

    term_rows = []
    for t in terms:
        right = _count_expected(text, t["expected"])
        wrong = sum(1 for x in text if x in t["wrong"])
        term_rows.append({"expected": t["expected"], "right": right, "wrong": wrong,
                          "invented": max(0, right - t["baseline_total"])})

    right_total = sum(r["right"] for r in term_rows)
    wrong_total = sum(r["wrong"] for r in term_rows)

    return {
        "words": len(sel),
        "span_s": round(span, 1),
        "wpm": round(60 * len(sel) / span, 1) if span else 0.0,
        "longest_run": max((r.count for r in runs), default=0),
        "looped_tokens": sum(r.count - 1 for r in runs),
        # Kept alongside zero/tiny/long rather than folded into one of them —
        # dropping this key is exactly the bug CLAUDE.md flags: a NaN bound
        # makes every span comparison False, so a nonfinite timing that isn't
        # its own counted kind is a defect class no regression gate can ever
        # fail on. See TimingDefect.kind in pipeline/health.py.
        "nonfinite_timings": sum(1 for d in defects if d.kind == "nonfinite"),
        "zero_timings": sum(1 for d in defects if d.kind == "zero"),
        "tiny_timings": sum(1 for d in defects if d.kind == "tiny"),
        "long_timings": sum(1 for d in defects if d.kind == "long"),
        "max_word_span": round(max((d.end - d.start for d in defects
                                    if d.kind == "long"), default=0.0), 2),
        "terms": term_rows,
        "terms_right": right_total,
        "terms_wrong": wrong_total,
        "terms_invented": sum(r["invented"] for r in term_rows),
        # Project owner's acceptance bar is 85% tracked-term accuracy. Higher
        # is better here, unlike everything in _WORSE_IF_UP, so it is
        # deliberately excluded from that tuple — the --baseline comparison
        # below checks it as a drop, the same shape as the word-count guard.
        "terms_accuracy": (round(100.0 * right_total / (right_total + wrong_total), 1)
                           if (right_total + wrong_total) else 0.0),
    }


def speech_intervals(wav: Path, noise_db: float = -30.0,
                     min_silence: float = 0.5) -> list[tuple[float, float]]:
    """Speech spans, as the complement of ffmpeg's detected silences.

    ffmpeg rather than a VAD package (webrtcvad, silero, librosa): ffmpeg is
    already a hard dependency of the pipeline, and this is a measurement tool —
    the no-new-dependencies rule applies to it as much as to the pipeline
    itself. `binary("ffmpeg")` resolves QATF_FFMPEG the same way every other
    caller in this codebase does; a bare "ffmpeg" string (or `shutil.which`)
    silently does nothing useful on a host where ffmpeg isn't on PATH — that
    exact mistake previously made a whole test suite exit 0 without running
    anything.

    This is the metric that catches the headline defect: on the real 12-minute
    Arabic recording, ~15s of ordinary speech at -17.5 dB (unambiguously
    talking — a known-speech stretch nearby measured -18.7 dB) collapsed into a
    single filler token spanning 424.55-439.59s. No text metric can see that;
    only comparing where there is SPEECH against where there are WORDS can.
    """
    # Refuse to guess, and do it before spending a real subprocess call on
    # silencedetect. `probe_duration` returns None for a raw stream, a
    # still-growing recording, or missing duration metadata (see its own
    # docstring). `detect.clip_spans` treats a missing duration as "do not
    # narrow a range" and skips its clamp — safe there, because degrading
    # only means a span is left wider than it needs to be. Here a missing
    # duration means "we do not know where speech ends," and the old
    # `or 0.0` fallback turned that into an empty speech list, which makes
    # `uncovered_speech` report zero uncovered speech: a failed measurement
    # rendered as a clean pass, on the one metric whose job is to be the
    # guard on every later experiment. Same shape as the NaN trap CLAUDE.md
    # calls out for `edits.diff` — an unchecked non-answer reads as success.
    duration = probe_duration(wav)
    if duration is None:
        raise CommandFailed(
            f"could not determine the duration of {wav}, so the span after "
            f"the last detected silence cannot be bounded. Returning a "
            f"partial speech list here would report zero uncovered speech, "
            f"which reads as a clean result rather than a failed measurement."
        )

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
    if start is not None:
        # ffmpeg reached EOF still inside a silence — the file ends mid-silence,
        # or this build doesn't flush a final silence_end at EOF. Dropping a
        # pending start here would sweep the trailing silence into "speech" by
        # omission, producing a false uncovered-speech gap over what is
        # actually silence. Close it at the probed duration instead.
        silences.append((start, duration))

    speech, cursor = [], 0.0
    for a, b in silences:
        if a > cursor:
            speech.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < duration:
        speech.append((cursor, duration))
    return speech


def uncovered_speech(words: list[Word], speech: list[tuple[float, float]],
                     min_gap: float = 2.0) -> list[tuple[float, float]]:
    """Speech spans with no word over them for at least `min_gap` seconds.

    A word that spans 15s counts as covering only where it starts and ends,
    not the void between — that void is precisely the defect this metric
    exists to catch. Comparing edges (word boundaries), not word intervals,
    against the speech spans means one long word can never masquerade as
    coverage of the seconds inside it.
    """
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


def _fmt(s: dict, label: str) -> str:
    return (f"  {label:<12} words {s['words']:>5}  wpm {s['wpm']:>5}  "
            f"run {s['longest_run']:>2}  looped {s['looped_tokens']:>3}  "
            f"zero {s['zero_timings']:>3}  long {s['long_timings']:>3}  "
            f"maxspan {s['max_word_span']:>6}  "
            f"terms {s['terms_right']}✓/{s['terms_wrong']}✗  "
            f"acc {s['terms_accuracy']:.1f}%"
            f"{'  INVENTED ' + str(s['terms_invented']) if s['terms_invented'] else ''}")


#: Metrics where a HIGHER number is worse. `words` is deliberately absent — a
#: drop in word count is the failure this scorer exists to catch, and it is
#: checked separately below. `terms_accuracy` is also absent — higher is
#: better there, so it gets its own drop check next to the word-count one,
#: not a place in a tuple whose whole premise is "up is bad".
#: `nonfinite_timings` is deliberately present, unlike the brief draft this
#: was built from: a defect class missing from this tuple is one no run can
#: ever fail the gate on, and NaN/inf timings are exactly the kind of thing a
#: "quieter" config could introduce unnoticed.
_WORSE_IF_UP = ("longest_run", "looped_tokens", "nonfinite_timings", "zero_timings",
                "tiny_timings", "long_timings", "max_word_span", "terms_wrong",
                "terms_invented")

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
        # Report total uncovered seconds, the worst window, and the top
        # three — nothing here asserts which window ranks first. Running
        # this on the reference file found six gaps >=10s: the documented
        # "آآ" window (424-440s) is real but only the THIRD largest, behind
        # 667.9-685.3s (17.4s, zero words) and 167.9-183.3s. A report that
        # named one window as "the" gap would be wrong the moment a fix
        # landed for it and a different gap took over as worst.
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
        # Same shape as the word-count guard just above: terms_accuracy is
        # higher-is-better, so a drop — not a rise — is the regression here.
        if whole["terms_accuracy"] < prev["terms_accuracy"]:
            regressions.append(
                f"terms_accuracy {prev['terms_accuracy']} -> {whole['terms_accuracy']}")
        print(f"\n  vs {base.name}: "
              + ("REGRESSED on " + ", ".join(regressions) if regressions
                 else "no regression"))
        raise SystemExit(1 if regressions else 0)
    raise SystemExit(0)
