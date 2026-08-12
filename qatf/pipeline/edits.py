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
`word-edits.json` is imported into that table on read and left on disk, same as
`asr.read_cache` does for `words-*.json` — but unlike the transcript cache, this
file stays a live interface rather than a one-time upgrade path: `save` has
exactly one caller (the API's `PUT /transcript`), so a CLI user has no other way
to write a correction. `load` re-imports it whenever its mtime has moved since
the last import, tracked in the `imported` table alongside the mtime it was
imported at — see `load` and `save` for why a bare "already imported" marker
was not enough.
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


def _import_legacy(work: str | Path, scope: str, edits: list[Edit],
                   mtime: float | None) -> None:
    """REPLACE a scope's rows with a legacy file's content, and record the
    file's mtime, in ONE transaction.

    Called on the first import AND on every re-import `load` decides to do
    (the file's mtime has moved since the recorded one) — see `load`. REPLACE,
    not merge: a DELETE before the INSERT, matching `save`. Upserting by
    (scope, idx) alone would leave a row behind forever once its line was
    removed from the file, since nothing would ever delete it — a merge cannot
    represent "this correction was taken out."

    The marker records mtime, not just that an import happened, which is the
    difference from the version of this function that shipped with the
    `imported` table: that version wrote the marker once via
    `ON CONFLICT ... DO NOTHING`, so `load`'s "already imported" check could
    never be false again for that scope — re-editing `word-edits.json` (the
    CLI's only interface, since `save` has exactly one caller and it is the
    API) was silently ignored from then on, and there was no flag to force a
    re-import short of hand-editing qatf.db. Recording the mtime instead
    means a changed file is detected and re-imported, while an unchanged file
    still trusts the table — an explicitly cleared overlay does not resurrect
    itself just because the file didn't move.

    `ON CONFLICT ... DO UPDATE` on the marker (not `DO NOTHING` any more):
    two threads racing to import or re-import the same scope (a render
    running `caption_words` while a client calls `GET /transcript`) both read
    the same file and produce the same edits and the same mtime, so the
    second writer safely rewrites the same values rather than crashing on a
    duplicate key.

    Closes its own connection before returning — see `load`."""
    try:
        with db.transaction(_db_path(work)) as con:
            con.execute("DELETE FROM word_edits WHERE scope=?", (scope,))
            con.executemany(
                "INSERT INTO word_edits (scope, idx, was, text) VALUES (?,?,?,?)",
                [(scope, e.index, e.was, e.text) for e in edits])
            con.execute(
                "INSERT INTO imported (scope, kind, mtime) VALUES (?, 'word_edits', ?) "
                "ON CONFLICT(scope, kind) DO UPDATE SET mtime=excluded.mtime",
                (scope, mtime))
    finally:
        db.close(_db_path(work))


def load(work: str | Path, scope: str) -> list[Edit]:
    """The overlay for `scope`, or an empty list.

    `scope` is the job id on the API path and the resolved output directory on
    the CLI path: one database can hold several output directories' overlays,
    and they must not read each other's.

    Re-imports the legacy file whenever it EXISTS and its mtime differs from
    the one recorded at the last import — not a bare "have we imported this
    scope before" marker. A bare marker cannot serve both callers this file
    has: `save` has exactly one caller (the API's `PUT /transcript`), so
    `<out>/.work/word-edits.json` IS the CLI's interface — there is no CLI
    write path, and four documentation sites promise that editing the file is
    how a CLI user corrects or removes a correction. A marker that only ever
    gets set could never be unset by re-editing it: the first version of this
    function imported once, set the marker, and every later `load` returned
    the stored rows straight from the `if found: return found` early exit —
    the file kept being "the interface" in name only. Comparing mtimes fixes
    that without giving up the other half of the guarantee: when the file
    HASN'T moved, the table is trusted as-is, including when it is empty —
    an overlay explicitly cleared via `save(work, scope, [])` (the documented
    way to undo a correction, over HTTP or by emptying the file) must stay
    cleared rather than resurrecting from the untouched file on the next read.

    Re-importing REPLACES the scope's rows rather than merging into them — see
    `_import_legacy` — so a line removed from the file is actually gone after
    the next `load`, not left behind because nothing ever deleted it.

    Closes its own connection before returning — see `db.close`. This database
    lives inside a job's own directory, deletable at any time from whatever
    thread handles the request; a handle left cached here would sit open until
    process exit, and on Windows an open handle blocks `shutil.rmtree` on the
    job directory regardless of which thread asks for the delete."""
    try:
        con = db.connect(_db_path(work))
        legacy = Path(work) / FILENAME
        current_mtime = legacy.stat().st_mtime if legacy.is_file() else None
        recorded = con.execute(
            "SELECT mtime FROM imported WHERE scope=? AND kind='word_edits'",
            (scope,)).fetchone()
        needs_import = current_mtime is not None and (
            recorded is None or recorded["mtime"] != current_mtime)
        if needs_import:
            imported = _load_legacy(legacy)
            _import_legacy(work, scope, imported, current_mtime)  # file left in place
            return imported
        rows = con.execute(
            "SELECT idx, was, text FROM word_edits WHERE scope=? ORDER BY idx",
            (scope,))
        return [Edit(index=r["idx"], was=r["was"], text=r["text"]) for r in rows]
    finally:
        db.close(_db_path(work))


def save(work: str | Path, scope: str, edits: list[Edit]) -> None:
    """Replace the whole overlay for `scope`, in one transaction.

    Wholesale replace, matching `PUT /jobs/{id}/transcript`: re-submitting the
    untouched transcript clears every correction, which is how you undo one. An
    empty `edits` list still runs the DELETE, so a cleared overlay is truly gone
    (zero rows) rather than a special "empty but present" case.

    Now ALSO upserts the `imported` marker, recording the legacy file's current
    mtime (or NULL if there is none). The previous version of this docstring
    said `save` deliberately left the marker alone, on the reasoning that the
    normal case is an already-imported scope and touching it was unnecessary.
    That reasoning covered only the already-imported case — it missed that
    `put_transcript` (api/routers/plan.py) never calls `load` before calling
    `save`, so a pre-SQLite scope whose `word-edits.json` still exists and had
    NEVER been read through `load` reached `save` with no marker row at all. A
    client that PUTs a correction without ever GETting the transcript first —
    entirely legitimate; nothing requires the GET — left `load`'s next call
    treating the scope as never-imported, reimporting the legacy file, and
    silently reverting the very PUT that just ran. Writing the marker here,
    with the mtime the file has *right now*, closes that gap: the next `load`
    sees a recorded mtime that matches (or a legacy file that doesn't exist)
    and trusts what this call just wrote instead of going back to the file.

    Closes its own connection before returning — see `load`."""
    try:
        legacy = Path(work) / FILENAME
        mtime = legacy.stat().st_mtime if legacy.is_file() else None
        with db.transaction(_db_path(work)) as con:
            con.execute("DELETE FROM word_edits WHERE scope=?", (scope,))
            con.executemany(
                "INSERT INTO word_edits (scope, idx, was, text) VALUES (?,?,?,?)",
                [(scope, e.index, e.was, e.text) for e in edits])
            con.execute(
                "INSERT INTO imported (scope, kind, mtime) VALUES (?, 'word_edits', ?) "
                "ON CONFLICT(scope, kind) DO UPDATE SET mtime=excluded.mtime",
                (scope, mtime))
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
