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
import math
import subprocess
import sys
from pathlib import Path

from _harness import ROOT  # noqa: F401  — puts the project root on sys.path

from qatf.core.errors import CommandFailed
from qatf.core.types import Word, words_from_dicts
from qatf.core.utils import binary, probe_duration
from qatf.pipeline import asr, fixups, health
from qatf.pipeline.health import _TRAILING

#: Errors before this point can be flattered by a seed that decays. Reported
#: separately for exactly that reason.
DECAY_SECONDS = 300.0


def load_terms(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["terms"]


def load_words(source: str | Path) -> list[Word]:
    """A transcript from either store.

    `db:<database path>#<key>` reads a row from that SQLite database (the same
    database `qatf.pipeline.asr` writes); anything else is treated as a plain
    words-*.json path, exactly as before. Both forms have to keep working: the
    stage-2 sweep recorded in docs/quality.md compares runs SIDE BY SIDE as
    files, and a measurement tool that can only read the live database could
    never compare today's run against a `sweep-*.json` taken last week — several
    of which sit in the repo root from a real measurement session.

    The `db:` branch goes through `asr.read_cache` rather than querying
    `transcripts` directly — a hand-rolled `SELECT` was tried first and
    dropped two things `read_cache` already handles: it falls back to
    importing a legacy `words-*.json` sitting next to a not-yet-created
    `qatf.db` (exactly the shape `run-fixed/.work/` is in — a transcript file
    with no database beside it, which the plain-path branch below has always
    read directly), and it closes its own connection when it is done (see
    `read_cache`'s own docstring on why: an open handle blocks
    `shutil.rmtree` on Windows). Duplicating the query here would have to
    duplicate both, and the first one silently regresses exactly the fixture
    this task verifies against."""
    text = str(source)
    if text.startswith("db:"):
        target, _, key = text[3:].partition("#")
        db_file = Path(target)
        # Same legacy filename `read_cache` itself checks for (`work /
        # f"{key}.json"`, `work` here being `db_file.parent`) — computed here
        # too so the guard below can tell "nothing to read" apart from "the
        # database doesn't exist YET, but importing it is about to create it
        # correctly", which are different situations with the same
        # `db_file.is_file()` answer.
        legacy = db_file.parent / f"{key}.json"
        if not db_file.is_file() and not legacy.is_file():
            # Checked BEFORE calling read_cache, not left to it: `db.connect`
            # creates the database file (and its parent directory) on first
            # use, as a side effect of opening a connection, regardless of
            # whether anything is actually there to read. A scoring run given
            # a bad path is a usage error, not licence to create a stray,
            # empty qatf.db on disk — a read-only tool must leave no trace
            # behind it. Same message shape as _require_readable below, for
            # the same reason: a typo here should read as exit 2 (usage
            # error), not exit 1 (a real scoring regression).
            #
            # A database that does not exist YET but has a legacy transcript
            # sitting next to it is NOT rejected here — that is exactly the
            # shape `run-fixed/.work/` is in (a `words-*.json` with no
            # `qatf.db` beside it, which the plain-path branch below has
            # always read directly), and `read_cache` creating the database
            # while importing that file is the correct, intended write, not
            # the stray one this check exists to prevent.
            print(f"error: database not found: {db_file}", file=sys.stderr)
            raise SystemExit(2)
        transcript = asr.read_cache(db_file.parent, key)
        if transcript is None:
            print(f"error: no transcript {key!r} in {db_file}", file=sys.stderr)
            raise SystemExit(2)
        return transcript.words
    return words_from_dicts(
        json.loads(_require_readable(Path(text), "input transcript")
                   .read_text(encoding="utf-8"))["words"])


def delivered_words(words: list[Word], fixups_path: Path | None = None) -> list[Word]:
    """The words as they will actually be BURNED IN, not as Whisper emitted them.

    Two numbers matter and they answer different questions. Scoring the raw
    cache measures the MODEL — it is what a decode parameter or a vocabulary
    term moves, and it must stay visible or a sweep cannot tell whether a change
    helped. Scoring this measures the PRODUCT — what a viewer reads.

    Reporting only the raw figure understates the tool, because `fixups` and
    `repair` are first-class stages that run on every read. Reporting only this
    one hides a model regression behind a substitution table. So the CLI prints
    both, and neither is allowed to stand in for the other.

    The order matches the read path exactly (`jobs.worker.baseline_words`):
    fixups first as a global rule, then repair. Anything else would score text
    the pipeline never produces."""
    out = [Word(w.text, w.start, w.end) for w in words]      # never mutate the caller's
    if fixups_path and fixups_path.is_file():
        out, _ = fixups.apply(out, fixups.load(fixups_path))
    out, _ = health.repair(out)
    # Drop the blanks. `repair` keeps them so word COUNT and every position
    # survive — `edits.py` is keyed by position and the PUT contract refuses a
    # count change — but nothing blank is delivered: `captions.group_words`
    # skips them. Scoring them would be actively misleading, because six
    # consecutive empty tokens are six identical consecutive tokens, so
    # `find_repetitions` reports the loop repair just FIXED as still present.
    return [w for w in out if w.text]


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
    # Finiteness must be checked BEFORE the >= comparison, not after: NaN >= 0.0
    # is False, so `w.start >= since` alone silently drops every NaN-start word
    # from every window, whole-file included. That is exactly the trap
    # `health.find_timing_defects` was fixed for (see its own comment) —
    # reintroduced one layer up here would mean `nonfinite_timings` could never
    # be nonzero, and the `words` guard would under-count on the same transcript
    # it's supposed to be watching.
    sel = [w for w in words if not math.isfinite(w.start) or w.start >= since]
    # A non-finite sel[0].start or sel[-1].end must not poison `span` (and
    # therefore `wpm`) with a NaN/inf result — same rule, one line down.
    span = 0.0
    if sel and math.isfinite(sel[0].start) and math.isfinite(sel[-1].end):
        span = sel[-1].end - sel[0].start
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
    # nonfinite and tiny both gate via _WORSE_IF_UP (zero and long always did,
    # and got printed); a defect kind that gates but never appears on this
    # line is invisible to anyone reading a run instead of diffing the dict.
    return (f"  {label:<12} words {s['words']:>5}  wpm {s['wpm']:>5}  "
            f"run {s['longest_run']:>2}  looped {s['looped_tokens']:>3}  "
            f"nonfinite {s['nonfinite_timings']:>2}  "
            f"zero {s['zero_timings']:>3}  tiny {s['tiny_timings']:>2}  "
            f"long {s['long_timings']:>3}  "
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
#: `terms_invented` is deliberately ABSENT, unlike an earlier draft that gated
#: on it. `invented = max(0, right - baseline_total)` compares two different
#: units: `right` counts OCCURRENCES of the expected term, `baseline_total`
#: was populated from WRONG-TOKEN counts on the reference file. Those units
#: coincide only when a term's wrong spelling happens to be one token per
#: occurrence; "جونيورز" mangled to "وناشوا نيورز" is 2 wrong tokens for one
#: occurrence, so a run that fixes it goes from 0 right to 1 right while
#: baseline_total stayed sized for 2. Worse, the ar-terms.json fixture shipped
#: with "إكس" at baseline_total 3 when the reference file actually has 5 wrong
#: occurrences there — so a transcription of that term with 0 errors reported
#: `terms_invented > 0` and this gate fired on a CORRECT result. A gate that
#: fires on a correct result is worse than no gate: it teaches its operator to
#: ignore it. `terms_invented` is still computed and printed (see `_fmt`) as an
#: advisory figure — worth a look, not worth failing a run over.
_WORSE_IF_UP = ("longest_run", "looped_tokens", "nonfinite_timings", "zero_timings",
                "tiny_timings", "long_timings", "max_word_span", "terms_wrong")

def _require_readable(path: Path, what: str) -> Path:
    """Exit 2 — this file's usage-error code — on a missing or unreadable
    input path, rather than letting an exception propagate and exit 1.

    An uncaught exception here would exit 1, the same code `raise
    SystemExit(1 if regressions else 0)` below uses for an actual scoring
    regression. A typo'd `--baseline` path and a real regression would then be
    indistinguishable to a caller checking $? — exactly the ambiguity exit
    codes exist to prevent. `path.is_file()` before the open: a bare `open()`
    failure would also raise, but checking first lets one function cover
    "missing" and "exists but unreadable" (permissions, a directory passed by
    mistake) with one message shape.

    The probe reads one byte in BINARY mode, not text. This function is
    shared by three different path arguments and only one of them
    (`--audio`) is guaranteed text-free — a WAV is binary from its first byte
    (RIFF header), and `path.read_text(encoding="utf-8")` on one raises
    `UnicodeDecodeError`, which isn't `OSError` and isn't caught, so the run
    dies exactly the way this helper exists to prevent. This function's job
    is only "is it there and can I open it" — the two JSON paths get parsed
    by `load_words`/`load_terms` immediately afterwards anyway, so malformed
    JSON is already reported there, with its own error, on its own path
    length. Deciding "is this valid JSON / a valid WAV" does not belong here
    twice."""
    if not path.is_file():
        print(f"error: {what} not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        with path.open("rb") as f:
            f.read(1)
    except OSError as exc:
        print(f"error: {what} not readable: {path} ({exc})", file=sys.stderr)
        raise SystemExit(2) from exc
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    # Not validated with `_require_readable` here, unlike --audio/--baseline
    # below: args[0] may be a `db:<database>#<key>` string rather than a
    # filesystem path, and `load_words` does its own validation for both
    # forms (a real `_require_readable` call for the plain-file case, an
    # explicit exit-2 for a missing DB key).
    source = args[0]
    audio = (_require_readable(Path(args[args.index("--audio") + 1]), "--audio file")
             if "--audio" in args else None)
    base = (_require_readable(Path(args[args.index("--baseline") + 1]), "--baseline file")
            if "--baseline" in args else None)

    words = load_words(source)
    terms = load_terms(Path(__file__).resolve().parent / "fixtures" / "ar-terms.json")
    whole = score(words, terms)
    late = score(words, terms, since=DECAY_SECONDS)

    # `Path(...).name` on a `db:` string would just be its own tail, not a
    # useful label — print the source as given for that form.
    label = source if source.startswith("db:") else Path(source).name
    print(f"\n{label}")
    print(_fmt(whole, "whole file"))
    print(_fmt(late, f"past {DECAY_SECONDS:.0f}s"))

    # What the viewer actually reads, after the read-path stages. Printed
    # alongside the raw rows rather than instead of them: the raw figure is what
    # a decode parameter moves, and collapsing the two would let a substitution
    # table hide a model regression.
    fx = ROOT / "prompts" / "ar-fixups.txt"
    delivered = score(delivered_words(words, fx), terms)
    print(_fmt(delivered, "delivered"))
    print(f"      (delivered = after {fx.name} + repetition repair, "
          f"i.e. the text burned into the captions)")

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
