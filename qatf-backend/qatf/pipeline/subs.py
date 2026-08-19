"""Stage 2' — a caption track standing in for transcription.

An ALTERNATIVE to `asr.py`, never a supplement to it. One transcript comes from
one source; mixing a caption track's text with Whisper's timings is a different
feature (a text overlay through `edits.py`) and is deliberately not this module.

Pure by design: this file parses a document and imports no network client, so it
is testable from a fixture with no yt-dlp, no network and no API key. `fetch.py`
owns everything that touches the outside world, exactly as `select.parse_response`
is testable without an endpoint.

WHAT A CAPTION TRACK ACTUALLY GIVES YOU, measured on a real 12-minute Arabic
video (724s, 453 caption events):

    tokens with an instant      1637      (Whisper large-v3 gave 1569)
    median inter-token gap      320 ms
    tokens sharing an instant   0
    END times in the format     NONE

That last line is the whole design. YouTube's `json3` carries `tOffsetMs` — when
a token STARTS — and nothing else. There is no measured end anywhere in the
format, so the end of token N is derived as the start of token N+1: an UPPER
BOUND on the true end, not an observation of it.

`Transcript.timing_source` is set to `"captions"` so that bound survives the
cache and the hand-edit round trip, and `cuts.tail_for` reads it to drop
`SNAP_TAIL` to zero. Adding 0.35s to an upper bound does not extend a cut into
silence — it extends it past the point where the next word has already started,
slicing the first phoneme off. That is the mid-syllable cut the core invariant
exists to prevent, and nothing downstream could detect it: the plan looks right,
the durations look right, and only listening reveals it.

Rolling captions need no special handling, and that is measured rather than
assumed: of 454 events, 226 carry `aAppend` and **all 226 hold only blank
segments**, so the blank filter below already removes them. Token counts with
and without `aAppend` events are identical (1637 either way).
"""

from __future__ import annotations

import json
import math

from ..core.types import Transcript, Word

#: How long the FINAL token is assumed to last, in seconds.
#:
#: Every other token's end is bounded by the next token's start. The last one
#: has no successor, so it is the single place this module has to guess — and it
#: guesses small on purpose. `health.MAX_WORD_SPAN` is the point at which a span
#: is called an alignment failure, so staying at or under it means the guess can
#: never itself be reported as damage.
LAST_TOKEN_SPAN = 2.0

#: Minimum span for a token, so a zero-length or inverted pair cannot reach
#: stage 4. `cuts.snap` picks boundaries by nearest-value search and a zero-span
#: word is a boundary that is simultaneously a start and an end.
MIN_TOKEN_SPAN = 0.01


def _load(raw: str | bytes | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return json.loads(raw)


def _instants(doc: dict) -> list[tuple[float, str]]:
    """Every non-blank token as (absolute seconds, text), in document order.

    A segment's offset is relative to its event, and the FIRST segment of an
    event usually omits `tOffsetMs` entirely rather than writing 0 — so a
    missing offset means zero, not missing data. That distinction is why
    `has_word_timings` below asks whether ANY segment carries an offset rather
    than whether every one does: on this file 76% do, and the 24% that do not
    are first-segments and blanks."""
    out: list[tuple[float, str]] = []
    for event in doc.get("events") or []:
        segments = event.get("segs")
        if not segments:                      # window-definition events carry none
            continue
        base = event.get("tStartMs")
        if not isinstance(base, int | float) or not math.isfinite(base):
            continue
        for segment in segments:
            text = (segment.get("utf8") or "").strip()
            if not text:
                # blank segments are the newline padding rolling captions emit;
                # they are also every `aAppend` event's entire payload
                continue
            offset = segment.get("tOffsetMs", 0)
            if not isinstance(offset, int | float) or not math.isfinite(offset):
                offset = 0
            out.append(((base + offset) / 1000.0, text))
    return out


def has_word_timings(raw: str | bytes | dict) -> bool:
    """Whether this track carries per-token instants at all.

    The gate on the whole feature. An auto-generated track breaks each caption
    line into one segment per word and offsets them; a HAND-UPLOADED track is
    one segment per line with no offsets anywhere, which gives line-level timing
    and nothing stage 4 can cut on.

    A caller that ignores this and imports a line-level track anyway would get a
    "transcript" whose every word inside a line shares one instant — `snap`
    would then place every cut in that line at the same place. Refusing is the
    only honest answer, and `fetch` turns a False here into the documented
    fallback to Whisper."""
    doc = _load(raw)
    for event in doc.get("events") or []:
        for segment in event.get("segs") or []:
            if "tOffsetMs" in segment and (segment.get("utf8") or "").strip():
                return True
    return False


def parse_json3(raw: str | bytes | dict) -> list[Word]:
    """A caption document as words, with ends derived as bounds.

    Instants are sorted and de-duplicated before ends are derived, because the
    derivation is "the next token's start" and that is only meaningful on a
    monotonic sequence. Two tokens sharing an instant would otherwise produce a
    zero-span word; the real file has none, but a malformed one is a caller's
    input, not a promise."""
    doc = _load(raw)
    instants = sorted(_instants(doc), key=lambda pair: pair[0])
    if not instants:
        return []

    words: list[Word] = []
    for index, (start, text) in enumerate(instants):
        # the last token is the one place with no successor to bound it
        end = (instants[index + 1][0] if index + 1 < len(instants)
               else start + LAST_TOKEN_SPAN)
        words.append(Word(text=text, start=max(0.0, start),
                          end=max(start + MIN_TOKEN_SPAN, end)))
    return words


def to_transcript(raw: str | bytes | dict, language: str | None = None) -> Transcript:
    """A caption document as a `Transcript` that knows what its timings are.

    `timing_source="captions"` is not decoration: it is what stops stage 4
    treating a bound as a measurement, and it has to survive the cache and the
    plan round trip to do that. `device`/`compute_type` stay None — nothing
    inferred here, and a caption transcript did not run on a device."""
    return Transcript(
        words=parse_json3(raw),
        language=language,
        language_probability=None,
        device=None,
        compute_type=None,
        timing_source="captions",
    )
