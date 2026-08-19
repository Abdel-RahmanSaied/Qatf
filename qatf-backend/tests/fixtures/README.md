# Test fixtures

Small, committed inputs so the suites run with no ffmpeg, GPU, API key or
network. Media never belongs here — `verify_render.py` generates its sources
with `ffmpeg -f lavfi` at test time, and the root `.gitignore` excludes every
video extension precisely so a fixture cannot quietly become a 200 MB blob.

## `ar-terms.json`

The tracked-terms list `score_transcript.py` grades a transcript against — the
Arabic technical vocabulary whose spelling the measurements in
[`docs/quality.md`](../../../docs/quality.md) are reported over.

## `captions-ar.json3`

A YouTube `json3` caption track, for `subs.py` (stage 2'). **Synthetic**: it is
hand-built to the format rather than captured from a real video, so nothing here
depends on a third party's caption track staying available, and no real
recording is redistributed.

It is a faithful miniature rather than a shape invented to satisfy assertions —
every structural feature below appears because a real track has it, and each one
exists to pin a specific parser behaviour:

| What it contains | Why |
| --- | --- |
| A leading event with **no `segs`** | Window definitions carry styling, not text; they must contribute no words. |
| A first segment with **no `tOffsetMs`** | Real tracks omit it rather than writing `0`. A missing offset means zero, not missing data — the parser must not skip the word. |
| Offsets **relative to the event**, not absolute | `1000ms` event + `400ms` offset is a word at 1.4s. |
| Blank `" "` and `"\n"` segments | Rolling-caption padding, which must be dropped before words are counted. |
| `aAppend` events whose payload is **only** a blank | Measured on the real 12-minute Arabic file: of 454 events, 226 carried `aAppend` and all 226 held nothing but blanks, so the blank filter already handles them and no `aAppend` branch is needed. |
| Seven real Arabic words | So the RTL text is genuine, not Latin standing in for it. |
| **No end times anywhere** | The whole point. `json3` has no end field; the parser derives each end as the next word's start — an upper bound, never a measurement. `Transcript.timing_source` carries that distinction downstream so `cuts.tail_for` can drop `SNAP_TAIL` to zero. |

If you regenerate it from a real track (`yt-dlp --write-auto-subs --sub-format
json3 --skip-download`), keep every feature in that table or `smoke_pipeline.py`
stops testing the thing it was written to test.
