# Stage 2 accuracy — design

**Date:** 2026-08-11
**Status:** approved, not yet implemented
**Scope:** transcription accuracy only. Throughput, loudness and subject tracking are out.

---

## Why

Stage 2 is the only stage whose output every later stage consumes verbatim.
Captions burn it in; `snap` anchors cuts on its timings; stage 3 reads it to pick
clips. It has never been tuned beyond `vad_filter=True`, and every decode
parameter other than that is a faster-whisper default.

A full run on real material — a 12-minute Egyptian-Arabic recording made in a
moving car, 4K ProRes, `large-v3` on CUDA/float16 — produced 1390 words and five
distinct classes of defect. All numbers below are measured on that file
(`words-large-v3-ar-6da1b0b4.json`), not estimated.

### Measured baseline

```text
words                     1390        median word duration   0.320s
speech span               717.8s      mean word duration     0.451s
rate                      116 wpm     longest word          15.42s
zero-length words           12  (0.9%)    end < start            0
sub-40ms words               2  (0.1%)    overlapping words      0  (0.0%)
words longer than 2s         7            largest silent gap   17.47s
```

The timing *structure* is sound — nothing inverted, nothing overlapping. The
damage is in outliers, not jitter.

| # | Defect | Evidence |
| --- | --- | --- |
| 1 | ~15s of real speech collapsed into one filler token | `آآ` spans 424.55→439.59s. Audio there measures **−17.5 dB mean**, against −18.7 dB in a known-speech stretch — it is ordinary talking, not silence. Caused a single caption to sit frozen on screen for **16.62s** (40.04→56.66s of clip 03, 29% of the clip). Two more of the same shape: `عنده` 167.90→183.32s (15.42s), `لك` 492.80→503.72s (10.92s). |
| 2 | Product names mangled | PHP → `البيتش بي` (194.2, 202.7, 208.1s); JavaScript → `الجفا سيكلت` (200.6s); hiring → `هيرنج` (257.1s); juniors → `وناشوا نيورز` (262.4s); seniors → `كاسة سن` / `سن يورز` (266.0–267.3s); LinkedIn → `الليك` (662.3s) **despite `لينكد إن` already being in the vocabulary**; X → `اكسة` (663.5, 663.9, 696.1s). The clip whose title is about PHP never once says PHP. |
| 3 | Decoder repetition loop | `ومكتفي` ×7 across 242.0→243.9s. Six of the seven share an identical `end` timestamp. |
| 4 | Hallucinated proper nouns | `فلسطين` (234.4s), `يابان` (61.3s, for `يبان`), `عمانة` (635.9s, for `أمانة`), `بسم الله` (321.8s). |
| 5 | Degenerate word timings | The 12 zero-length words and 7 over 2s above. These feed `snap` directly, so they degrade cut quality rather than captions — CLAUDE.md open risk #1, previously unmeasured. |

Priority order, set by the project owner: **1 → 2 → 3 → 5**. (Defect 4 rides
along with 3; both are decoder-behaviour problems.)

### One hypothesis already eliminated, before spending a GPU run

`hallucination_silence_threshold` looks like the obvious fix for defect 1 and is
**not**. Reading faster-whisper's implementation: it skips segments flagged by
`is_segment_anomaly` only when they are *surrounded by silence* exceeding the
threshold. The `آآ` window contains speech at normal level, so neither
`silence_before` nor `silence_after` holds and the parameter never fires there.

It is still worth testing against defect 4, where invented nouns may sit in
genuinely low-energy regions.

The likelier cause of defect 1 is our own setting. `asr.py` passes
`vad_parameters={"min_silence_duration_ms": 500}`. faster-whisper's default when
`vad_filter=True` is **160**, and it also caps chunks at
`max_speech_duration_s = chunk_length` (30s). Raising the silence threshold
merges across short pauses into longer speech chunks; a 30s window of noisy car
audio that decodes to almost nothing is exactly how 15 seconds becomes one
token. That value is ours, non-default, and justified in the code only as a
"big speed win" — never scored for accuracy.

---

## Non-goals

- **The model.** `large-v3` stays. Swapping models changes every number here.
- **`BatchedInferencePipeline`.** Throughput, not accuracy. Separate work.
- **`loudnorm`.** Tracked separately as the highest value-per-effort item.
- **The active-speaker model.** Stage 4b, unrelated.
- **New CLI flags for decode parameters.** Once measured they are product
  decisions, not per-run knobs.

---

## Component 1 — `tests/score_transcript.py`

A scorer that runs on any `words-*.json` and needs **no reference transcript**.
This is built first because it is the guard on everything else: the specific way
a hallucination fix fails is by deleting real speech, and only a coverage metric
can see that.

```text
python tests/score_transcript.py <words.json> [--audio <wav>] [--baseline <other.json>]
```

| Metric | Detects |
| --- | --- |
| words, wpm | a config that "improves" by transcribing less |
| longest identical-token run; tokens inside runs ≥ 3 | defect 3 |
| zero-length, <40ms, >2s counts; max single-word span | defect 5 |
| **uncovered speech** — `ffmpeg silencedetect` intervals differenced against word spans | defect 1 |
| tracked terms: right, wrong, and **false positives** | defect 2 |

Two hard requirements:

- **Every metric is reported whole-file and past-300s separately.** The
  measurement trap in `docs/quality.md` exists because a 70s slice starting where
  a seed was applied reported 14 errors → 0, and the same audio 6.6 minutes in
  had them all back.
- **Uncovered speech uses ffmpeg, not a new dependency.** `silencedetect` is
  already available; `webrtcvad` or similar would violate the no-new-dependencies
  rule for a measurement tool.

`--baseline` diffs two transcripts and exits non-zero on regression, so it works
as a test and not only as a report.

**Tracked terms** live in a small JSON fixture: expected spelling, plus the
known-wrong variants observed. Counting false positives matters as much as
misses — a vocabulary can inject a term that was never spoken.

## Component 2 — decode parameters in `asr.py`

A module-level `DECODE` dict of tuned defaults, each key carrying its measured
effect in a comment. This follows the pattern already in `transcribe`, where the
`hotwords` vs `initial_prompt` measurement table sits inline at the call site.

`transcribe()` and `_transcribe_on()` gain `decode: dict | None = None`, merged
over `DECODE`. Only the experiment harness passes it. No CLI surface.

The transcript cache key does **not** change with decode parameters during the
experiment phase — each run writes to an explicitly named output file so runs can
be compared side by side. When a winner is baked into `DECODE`, the cache key
question is revisited (see Open questions).

## Component 3 — repair and flag, on read

Follows the `fixups` precedent exactly: applied when the transcript is read,
**never written back to the cache**, so the cache keeps saying what Whisper
actually produced and every number in `docs/quality.md` stays reproducible.

**Repetition runs (defect 3) — repaired.** A run of ≥ 4 identical consecutive
tokens has its duplicates' text blanked, keeping the first. Word count,
positions and timings are all preserved, so `edits.py`'s position-keyed overlay
and the `PUT /jobs/{id}/transcript` word-count contract are untouched.
`captions.group_words` learns to skip empty tokens. Always logged.

**Timing defects (defect 5) — flagged only, never repaired.** A degenerate span
is surfaced as a log line naming the timestamp, so the clip edges there can be
inspected. Re-timing them silently would cross the boundary the whole design
rests on: Whisper owns acoustic time and `snap` trusts it completely. A word
whose timing we invented is indistinguishable, downstream, from one Whisper
measured.

## Component 4 — experiment protocol

One variable per run. Score whole-file and past-300s. **Record losers too** — the
"tested and rejected" table is the reason nobody re-runs `beam_size 8`.

Ordered by the owner's priority:

1. **Dropped speech.** `min_silence_duration_ms` 500 → 160 (removing our own
   override); then `speech_pad_ms`; then `compression_ratio_threshold` and
   `log_prob_threshold`, which control whether a bad window triggers temperature
   fallback at all.
2. **Product names.** The vocabulary extended on 2026-08-11 — 38 terms, 222/300
   chars, adds `PHP سكريبت هايرنج جونيور جونيورز سينيور سينيورز إكس`. Staged and
   unvalidated; this is its first real scoring.
3. **Loops and hallucinations.** `no_repeat_ngram_size=3`;
   `repetition_penalty` 1.05 and 1.10; `hallucination_silence_threshold` against
   defect 4 only.
4. **Timings.** `speech_pad_ms`, measured for its effect on word boundaries
   rather than on word count.

Already rejected by prior measurement and not to be re-run:
`condition_on_previous_text=False`, `beam_size` 8/10, `dynaudnorm`.

## Ground truth (parallel track)

A hand-corrected reference over three spread 60-second windows, **one of them
past 300s**, produced by the project owner. It anchors the reference-free
metrics with real WER/CER. The fast loop is not blocked on it.

---

## Testing

- **The scorer is validated against the known-bad transcript above.** It must
  independently find the `ومكتفي` run, the 15.42s word, the 12 zero-length
  timings and the uncovered-speech window at 424–439s. A scorer that cannot
  detect defects already confirmed by hand is measuring nothing — the same rule
  that makes `verify_render.py` assert its `crop` control *fails*.
- `smoke_pipeline.py`: repair preserves word count and every timing; blanking
  fires at ≥ 4 and not at 3; captions skip empty tokens; the flagging path
  reports the right timestamps.
- All existing suites stay green. `ruff` clean.

## Risks

| Risk | Guard |
| --- | --- |
| A hallucination fix deletes real speech | The uncovered-speech and word-count metrics, which is why the scorer is built first |
| Vocabulary injects terms never spoken | The tracked-term list counts false positives, not just misses |
| Blanking hides a genuine repetition | Threshold ≥ 4 consecutive, and always logged |
| A change helps early and decays | Every metric reported past-300s separately |

## Success criteria

Stated as thresholds rather than directions, so a run either clears them or does
not:

1. **No caption cue on screen longer than 6.0s** while the audio under it
   measures within 3 dB of the file's speech level. Baseline: one at 16.62s.
2. **No single uncovered-speech window longer than 5.0s**, and total uncovered
   speech at most half the baseline. Baseline: 15.04s in one window.
3. **Longest identical-token run ≤ 3.** Baseline: 7.
4. **All 7 tracked product names correct, with zero false positives** — a term
   appearing where it was not spoken counts against the run.
5. **Word count within ±3% of baseline** (1390). A config that clears 1–4 by
   transcribing less has failed, and this is the criterion that catches it.
6. Every result — win or loss — recorded in `docs/quality.md`.

## Open questions

- **Should decode parameters join the transcript cache key?** They change the
  output, so by the same argument that put the Whisper size, language and
  vocabulary in the key, they should. Deferred until a winning set exists,
  because adding them now would invalidate the cached transcript that the
  offline half of this work depends on.
- **Does the repair layer belong in `asr.py` or its own module?** `fixups.py` and
  `edits.py` are each their own module for one text-correction concern; a third
  concern suggests a third module. Decide during planning.
