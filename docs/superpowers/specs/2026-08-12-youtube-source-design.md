# YouTube as a source, and captions instead of Whisper

**Date:** 2026-08-12
**Status:** implemented
**Decision owner:** Saied

## What was asked for

Two things: fetch a video from a YouTube link, and use YouTube's own captions
rather than transcribing. Of the three approaches offered — ingest only (A),
captions as a *text overlay* onto Whisper's timings (B), captions as a full
transcript source skipping stage 2 (C) — **C was chosen**, having been told what
it costs.

## The measurement that shaped it

Run before designing, on the real 12-minute Arabic video (724s), graded with the
project's own `tests/score_transcript.py`:

```text
                     YouTube ASR   Whisper large-v3
words                       1637               1569
wpm                        135.9              130.5
repetition loops               0                  0
zero-span words                0                  1
tracked terms            2✓ / 4✗            0✓ / 6✗
cost                  173 KB, ~2s          18m43s CPU
```

**Caveat kept deliberately visible:** that Whisper run used no `hotwords`, and
the vocabulary is the main quality lever (23 wrong → 19 → 7 with denoise). So
this is YouTube beating an *unoptimised* Whisper, not the shipping
configuration. A rematch has not been run.

### The finding that decides the design

`json3` carries `tOffsetMs` per token — a **start** — and no end anywhere in the
format. Structural check on the same file:

```text
events with segs        453     tokens              1637
segments w/ tOffsetMs  1410     median gap        320 ms
duplicate instants        0     END times          NONE
```

So a caption word's `end` must be derived as the next word's start: an **upper
bound**, not a measurement.

This collides with stage 4. `snap` computes `clip.end = word.end + SNAP_TAIL`.
Against a bound, "the word ended" means "the next word began", so every clip end
would land 0.35s past the following word's onset — clipping its first phoneme.
That is exactly the mid-syllable failure the core invariant exists to prevent,
and it is invisible: the plan looks right, the durations look right, and only
listening reveals it. `health.find_timing_defects` already states the principle —
an invented timing is indistinguishable downstream from a measured one.

## Design

### Modules

```text
pipeline/fetch.py    stage 0   URL -> local file + caption track. yt-dlp, network.
pipeline/subs.py     stage 2'  json3 -> list[Word]. Pure: no network, no yt-dlp.
```

The split mirrors `select.py`, where `parse_response` is testable without an
endpoint. `subs.py` is exercised offline from `tests/fixtures/captions-ar.json3`.

### Provenance, and the bounded tail

`Transcript.timing_source: "asr" | "captions"` is the mechanism.

- `cuts.tail_for(timing_source)` returns `SNAP_TAIL` (0.35) for measured ends and
  `SNAP_TAIL_BOUNDED` (0.0) for bounded ones.
- It must survive the cache and the plan round trip, or `PUT /jobs/{id}/plan`
  would re-snap with a different tail than the run that produced the plan and
  drift every boundary on each edit — the 56.70 → 59.21 bug in a new costume.
- An unknown or NULL source reads as `"asr"`. Every row written before the field
  existed came from Whisper, so that is both the true answer for old rows and the
  safe direction for a corrupt one: too-late a cut, never a clipped word.

Schema **v4** adds `transcripts.timing_source`. Cache keys cannot collide by
construction: `subs-<lang>-<track>` against `words-<model>-<lang>-<seed>`.
`Job.transcript_key` records which row a job actually used, because whether
captions were usable is a runtime decision no reading of `options` can rebuild.

### Trust boundary

A caller-supplied URL reaching a subprocess is new for this project.
`fetch.validate_url` allows **https only**, on an **exact-host** allowlist, and
refuses userinfo and ports. Exact match, never `.endswith` — that is how
`youtube.com.evil.net` gets in. Refusals never echo the URL back.

Enforced in `pipeline/` (the layer owning the risk, so the CLI is held to it too)
**and** in the router, so a bad URL is a synchronous 403 rather than a 202 plus a
job that dies later. The first implementation lacked the router check and
returned 202 for `file:///etc/passwd`; the suite caught it.

### Fallback

When no caption track exists, or the track is line-level (hand-uploaded tracks
have no per-word offsets), the job **falls back to Whisper and says so**. A log,
not a failure — unlike `--device cuda` and `--reframe track`, which refuse
because they have no better alternative. Here the fallback is a fall *up* in
quality and costs only time.

### Surfaces

- `POST /jobs/url` — mirrors `/jobs/upload`, so `JobCreate.path` stays required.
- `JobOptions.transcript_source: auto | captions | whisper`.
- New state `fetching`; `Job.url` and `JobResponse.url` preserve the origin after
  stage 0 rewrites `video`.
- CLI: a URL as the positional argument, plus `--transcript-source`. The argument
  is `str`, not `Path` — `Path("https://x/y")` collapses the double slash.
- `yt-dlp` behind a `[youtube]` extra, imported lazily,
  `FetcherNotAvailable` (503) when missing.

## Verified

592 checks green, ruff clean. Against the live URL: 957.8 MB fetched, 1637
tokens, `timing_source=captions`, every end equal to the next start, snap tail
0.0, a probe clip closing exactly on a word onset, and idempotent under re-snap.

## Not done

- The Whisper-with-vocab rematch, which would settle whether captions are
  actually better on text.
- Approach B (captions as a text overlay through `edits.py`). The alignment
  between two tokenisations is unsolved and wants a prototype, not a promise.
- Rights: fetching is unrestricted within the allowlist. Fine for one's own
  channel; the docs should stay honest that the general case is the caller's
  responsibility.
