"""Stage 2c — per-word transcript corrections.

`fixups.py` handles the systematic errors: a term the decoder always mishears the
same way, fixed once by value and fixed forever. It cannot touch the other kind —
a word misheard *once*, in one place, where the identical string is correct
everywhere else. On Egyptian Arabic that is most of what is left after the
vocabulary has done its work: Whisper writing `من` where the speaker said `مين`
is unfixable by substitution, because `من` is one of the most common words in the
language and correct almost everywhere else it appears.

This module fixes exactly that class, by position rather than by value.

Two properties, both load-bearing:

  - **Text only.** `Word.start` and `Word.end` are never written here, and
    `diff()` refuses a submission that changes either. A correction can change
    what a caption reads and can never move a cut — so the core invariant is
    enforced by the contract rather than by discipline. It also means a
    correction can be made at any point after stage 2 without re-planning: the
    cut points are provably identical.
  - **An overlay, not a rewrite.** Corrections live beside the transcript cache,
    never inside it. Re-transcribing must not silently discard them, and the
    cache has to keep saying what Whisper *actually* produced — the moment a
    corrected transcript is indistinguishable from a raw one, every measurement
    in docs/quality.md stops meaning anything.

Corrections are keyed by word index and carry the text they replaced. An index
alone is not safe: re-transcribing with a different Whisper size, or toggling
`--denoise`, shifts every position, and correction #1247 would land silently on
an unrelated word. With `was` recorded, a shifted overlay goes **stale** and is
reported rather than applied.

The overlay lives in `word_edits` in `<work>/qatf.db` — the same database the
transcript cache uses (`asr.db_path`) — keyed by `scope` so several overlays can
share one database without reading each other's: `scope` is the job id on the
API path and the resolved output directory on the CLI path. A pre-SQLite
`word-edits.json` is imported into that table on first read and left on disk,
same as `asr.read_cache` does for `words-*.json`.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core import db
from ..core.errors import TranscriptStructureChanged
from ..core.types import Word

FILENAME = "word-edits.json"

#: Timestamps round-trip through JSON as floats. Compare with a tolerance far
#: below one frame at any sane rate, so re-serialisation never reads as an edit
#: while a genuine retiming still does.
TIMING_EPSILON = 1e-6


@dataclass
class Edit:
    """One correction. `was` is the drift guard, not decoration."""

    index: int
    was: str
    text: str


def to_dicts(edits: list[Edit]) -> list[dict]:
    return [asdict(e) for e in edits]


def from_dicts(data: object) -> list[Edit]:
    """Tolerant of both the wrapped and bare forms, since the pre-SQLite file
    this reads was meant to be hand-edited."""
    if isinstance(data, dict):
        data = data.get("edits", [])
    if not isinstance(data, list):
        return []
    out: list[Edit] = []
    for item in data:
        try:
            out.append(Edit(index=int(item["index"]),
                            was=str(item.get("was", "")),
                            text=str(item["text"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _db_path(work: str | Path) -> Path:
    """One database per work directory — same file `asr.db_path` names. Not
    imported from `asr` to keep the stage modules independent of each other;
    both derivations are one line and must agree only on the constant below."""
    return Path(work) / "qatf.db"


def _load_legacy(path: Path) -> list[Edit]:
    """Parse a pre-SQLite `word-edits.json` overlay. `load` calls this once, on
    the first read after upgrading, and writes the result into the database —
    the file itself is left on disk, never deleted, so a bad upgrade stays
    reversible by checking out the previous commit."""
    try:
        return from_dicts(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return []


def _import_legacy(work: str | Path, scope: str, edits: list[Edit]) -> None:
    """Write a legacy file's rows AND the `imported` marker in ONE transaction.

    The marker is what makes "zero rows" mean "zero corrections" permanently
    once a scope has been imported, rather than "go check the file again" —
    without it, `load` could not tell a scope that was explicitly cleared via
    `save(work, scope, [])` apart from one that has never been imported, and
    the legacy file (deliberately left on disk — see `_load_legacy`) would
    resurrect the just-cleared corrections on the very next read. Both writes
    must land together: a marker with no rows would silently discard the
    import, and rows with no marker would leave the resurrection bug in place
    for exactly the scope this is trying to fix.

    `ON CONFLICT ... DO UPDATE`/`DO NOTHING` rather than a bare INSERT: two
    threads racing to import the same never-before-seen scope (a render
    running `caption_words` while a client calls `GET /transcript`) both pass
    the "not yet imported" check before either commits, since that check and
    this write are two separate statements. The first writer wins the race
    honestly; the second must not crash on a duplicate key — it just
    rewrites the same values.

    Closes its own connection before returning — see `load`."""
    try:
        with db.transaction(_db_path(work)) as con:
            con.executemany(
                "INSERT INTO word_edits (scope, idx, was, text) VALUES (?,?,?,?) "
                "ON CONFLICT(scope, idx) DO UPDATE SET was=excluded.was, "
                "text=excluded.text",
                [(scope, e.index, e.was, e.text) for e in edits])
            con.execute(
                "INSERT INTO imported (scope, kind) VALUES (?, 'word_edits') "
                "ON CONFLICT(scope, kind) DO NOTHING",
                (scope,))
    finally:
        db.close(_db_path(work))


def load(work: str | Path, scope: str) -> list[Edit]:
    """The overlay for `scope`, or an empty list.

    `scope` is the job id on the API path and the resolved output directory on
    the CLI path: one database can hold several output directories' overlays,
    and they must not read each other's.

    The legacy file is imported AT MOST ONCE per scope — tracked in the
    `imported` table, not by "are there zero rows" — because zero rows is
    ambiguous between "never imported" and "explicitly cleared". Before the
    `imported` table existed, every pre-SQLite job (which by design leaves its
    `word-edits.json` on disk after the first import) would silently resurrect
    a cleared overlay on the read after `save(work, scope, [])`: `PUT
    /jobs/{id}/transcript` with the pristine transcript is how a user undoes a
    correction, and that undo was reverting itself on the next `GET`.

    Closes its own connection before returning — see `db.close`. This database
    lives inside a job's own directory, deletable at any time from whatever
    thread handles the request; a handle left cached here would sit open until
    process exit, and on Windows an open handle blocks `shutil.rmtree` on the
    job directory regardless of which thread asks for the delete."""
    try:
        con = db.connect(_db_path(work))
        rows = con.execute(
            "SELECT idx, was, text FROM word_edits WHERE scope=? ORDER BY idx",
            (scope,))
        found = [Edit(index=r["idx"], was=r["was"], text=r["text"]) for r in rows]
        if found:
            return found
        already_imported = con.execute(
            "SELECT 1 FROM imported WHERE scope=? AND kind='word_edits'",
            (scope,)).fetchone()
        if already_imported is not None:
            return []      # imported once already — zero rows now means zero corrections
        legacy = Path(work) / FILENAME
        if legacy.is_file():
            imported = _load_legacy(legacy)
            _import_legacy(work, scope, imported)   # the file is left in place
            return imported
        return []
    finally:
        db.close(_db_path(work))


def save(work: str | Path, scope: str, edits: list[Edit]) -> None:
    """Replace the whole overlay for `scope`, in one transaction.

    Wholesale replace, matching `PUT /jobs/{id}/transcript`: re-submitting the
    untouched transcript clears every correction, which is how you undo one. An
    empty `edits` list still runs the DELETE, so a cleared overlay is truly gone
    (zero rows) rather than a special "empty but present" case. This does NOT
    touch the `imported` marker — only `_import_legacy` does — so calling this
    on an already-imported scope (the normal case) correctly leaves "already
    imported" set and a later `load` does not go looking at the legacy file
    again just because this call happened to empty the table.

    Closes its own connection before returning — see `load`."""
    try:
        with db.transaction(_db_path(work)) as con:
            con.execute("DELETE FROM word_edits WHERE scope=?", (scope,))
            con.executemany(
                "INSERT INTO word_edits (scope, idx, was, text) VALUES (?,?,?,?)",
                [(scope, e.index, e.was, e.text) for e in edits])
    finally:
        db.close(_db_path(work))


def diff(baseline: list[Word], submitted: list[Word]) -> list[Edit]:
    """Derive an overlay from a whole submitted word list.

    This is where the invariant is enforced. Anything other than `text` differing
    is refused, so there is no code path — API, CLI or hand-edited file — by
    which a caller can move a cut point while claiming to fix a spelling."""
    if len(submitted) != len(baseline):
        raise TranscriptStructureChanged(
            f"transcript has {len(baseline)} words, got {len(submitted)} — "
            "word text may be corrected, but words cannot be added or removed. "
            "To split one word into two, put both in that word's text.")
    for i, (base, sub) in enumerate(zip(baseline, submitted, strict=True)):
        # NaN defeats the comparison below — every comparison against NaN is
        # False, so a NaN timing would sail through as "unchanged". Check
        # finiteness first rather than relying on the difference.
        if not (math.isfinite(sub.start) and math.isfinite(sub.end)):
            raise TranscriptStructureChanged(
                f"word {i} ({base.text!r}) has a non-finite timing "
                f"({sub.start}-{sub.end}). Word timings come from the audio.")
        if (abs(base.start - sub.start) > TIMING_EPSILON
                or abs(base.end - sub.end) > TIMING_EPSILON):
            raise TranscriptStructureChanged(
                f"word {i} ({base.text!r}) changed timing "
                f"{base.start:.3f}-{base.end:.3f} -> {sub.start:.3f}-{sub.end:.3f}. "
                "Word timings come from the audio and are not editable — they are "
                "what every cut point is snapped to.")
    return [Edit(index=i, was=base.text, text=sub.text)
            for i, (base, sub) in enumerate(zip(baseline, submitted, strict=True))
            if base.text != sub.text]


def apply(words: list[Word], edits: list[Edit]) -> tuple[list[Word], int, list[Edit]]:
    """Overlay corrections onto the words. Returns (words, applied, stale).

    A correction whose `was` no longer matches the word at that index is **not**
    applied. That is the transcript having moved underneath it — a re-transcribe
    at a different Whisper size, or `--denoise` toggled — and applying it anyway
    would corrupt an unrelated word silently. Stale corrections come back so a
    caller can say so rather than swallowing them."""
    if not edits:
        return words, 0, []
    applied = 0
    stale: list[Edit] = []
    for edit in edits:
        if not 0 <= edit.index < len(words):
            stale.append(edit)
            continue
        word = words[edit.index]
        if edit.was and word.text != edit.was and word.text != edit.text:
            stale.append(edit)
            continue
        if word.text != edit.text:
            word.text = edit.text
            applied += 1
    return words, applied, stale
