# The tuning playbook

Everything here was measured on real material: a 12-minute Egyptian-Arabic talk,
4K ProRes, 75 GB, recorded in a **moving car**. Where a number is not measured,
this page says so.

---

## Transcription — 23 errors to 3

Three levers, in order of how much they moved the file. Tracked-error count
across the whole transcript, with correct-term count and late-file errors
reported separately:

```text
                              wrong  right  wrong after 300s
nothing                          23      8                15
+ --vocab (hotwords)             19     22                14
+ --denoise + tuned vocab         7     32                 5
+ --fixups                        3     33                 1
```

### `--vocab` — the main lever

Maps to faster-whisper's `hotwords`. Applies to the **whole file**.

Write the terms **the way you want them spelled back**.
[`prompts/ar-tech.txt`](../qatf-backend/prompts/ar-tech.txt) is the working list — 30 terms,
170 characters. A 38-term extension was tried and reverted; see the stage-2
sweep below for why.

### `--denoise` — free, and also faster

`highpass=f=80,lowpass=f=7500,afftdn=nr=12:nf=-25`. Took 15 → 11 errors on its own
and is **~20% faster**, because a cleaner signal makes the decoder fall back to
higher temperatures less often. Worth it on any field audio.

It writes a separate `audio-denoised.wav`, so toggling it is safe and cheap.

### `--prompt` — mostly a trap

Maps to `initial_prompt`, which seeds only the **first ~30 seconds** and then
decays. It looks excellent on a short clip and does nothing to a long one.
Prefer `--vocab`. See the measurement trap below — this one nearly shipped as a
"win".

### `--fixups` — the last resort

For words the vocabulary will not take. `بايسون → بايثون`: the speaker really does
say it that way, so Whisper is faithfully spelling what it heard, and no amount
of biasing will change that.

Substitutions touch `Word.text` **only — never timestamps**. A spelling fix can
change what a caption reads and can never move a cut. Applied on read, not baked
into the cache, so you can edit the map and re-render without re-transcribing.

### Per-word corrections — the floor

Whatever survives all four levers is a word Whisper simply got wrong in one
place, and no parameter will fix it. Correct it directly: over HTTP with
`PUT /jobs/{id}/transcript`, or by writing `<work>/word-edits.json` for the
CLI — imported into `<work>/qatf.db` and re-imported whenever the file's mtime
moves, so it stays a live interface rather than a one-time upgrade path.

This is not a tuning lever and does not belong in the table above — it is the
manual floor under it, and it costs one re-render with no model call and no
re-transcription. Keep it out of your measurements: corrections are stored as an
overlay precisely so the cached transcript keeps saying what Whisper actually
produced. The moment a corrected transcript is indistinguishable from a raw one,
every number on this page becomes unreproducible.

**The transcript cache moved to SQLite; that guarantee did not.** The cache is
now a row in `<work>/qatf.db` (`transcripts`, one row per `asr.cache_key`)
instead of a `words-<model>-<lang>.json` file, but `read_cache` still returns
exactly what Whisper produced — fixups, `health.repair` and per-word corrections
are all still applied by the *caller*, on every read, and none of the three is
ever written back into the row. Every table on this page was scored against
that raw row, before or after the move, which is the property that makes them
comparable at all.

### Tested and rejected

So nobody repeats them:

| Change | Result |
| --- | --- |
| `condition_on_previous_text=False` | no effect alone, **worse** combined |
| `beam_size` 8 / 10 | **worse**, and 50% slower |
| `dynaudnorm` | correct terms 24 → **13** |
| Extending Arabic vocabulary 30 → 38 terms | tracked terms 0/17 → 7/0, but uncovered speech 66.1s → 166.9s and 195 fewer words. Rejected. |

### The stage-2 sweep — how to run one

Baseline, measured on the reference transcript
(`words-large-v3-ar-6da1b0b4.json`, 1390 words — the same denoised car
recording as the rest of this page): one repetition run (`ومكتفي` ×7,
242.0-243.9s), 21 timing defects (12 zero + 2 tiny + 7 long; the longest
single word spans 15.42s), 108.1s of uncovered speech across six gaps ≥10s,
and 0.0% tracked-term accuracy (0 right / 17 wrong against
`tests/fixtures/ar-terms.json`). Every hypothesis below targets one of these
four numbers.

**That reference came from an older environment, and the framing above needs
a correction.** Re-running the identical 30-term vocabulary in the current
container (`control`, in the vocabulary results below) beats it on all four
numbers before a single decode parameter changes. Several defects the
reference transcript carries — the repetition loop entirely, 11 of 12
zero-length timings, 42 of the 108.1s of uncovered speech — are artifacts of
that older faster-whisper build, not necessarily something a decode parameter
needs to fix. New sweep rows should be scored against `control`, not the
reference figures above.

The uncovered-speech metric's own top three are worth reading before tuning
anything: 667.9-685.3s (17.5s), 167.9-183.3s (15.4s), 424.6-439.6s (15.0s).
The first of those has **zero words over it** and measures -19.4 dB against a
-22.3 dB known-speech reference — louder than average speech, not silence.
A words-only analysis (no `--audio`) had previously reported it as "largest
silent gap 17.47s"; it never looked at the audio, so dropped speech read as
silence. The third, 424.6-439.6s, is the `آآ` collapse discussed below.

Every run changes ONE thing and is scored the same way:

```bash
python tests/score_transcript.py <new>.json --audio <wav> --baseline <prev>.json
```

Record the result here whether it wins or loses. The table above exists
because three plausible changes were worse, and writing them down is what
stops them being retried.

| Hypothesis | Parameter | Target defect | Result |
| --- | --- | --- | --- |
| Our 500ms silence threshold merges chunks to 30s and starves the decoder | `vad_parameters.min_silence_duration_ms` 500 → 160 | 15s of speech in one token | *pending* |
| Padding changes where word boundaries land | `vad_parameters.speech_pad_ms` | degenerate timings | *pending* |
| A bad window never triggers temperature fallback | `compression_ratio_threshold`, `log_prob_threshold` | dropped speech | *pending* |
| The extended vocabulary fixes product names | `--vocab-file prompts/ar-tech.txt` (38 terms) | PHP, JavaScript, seniors | **Measured — rejected.** Term accuracy improved (2/6 → 7/0) by dropping 195 words; uncovered speech 66.1s → 166.9s. Reverted. Details below. |
| N-gram blocking breaks the loop | `no_repeat_ngram_size=3` | `ومكتفي` ×7 | *pending* |
| Penalising repeats breaks the loop more gently | `repetition_penalty` 1.05 / 1.10 | `ومكتفي` ×7 | *pending* |
| Anomalous segments in silence are hallucinations | `hallucination_silence_threshold=2.0` | invented proper nouns | *pending* |

Replace each *pending* as runs complete. A row that loses keeps its result.
Of the seven hypotheses above, only the vocabulary row has actually run — the
six decode-parameter rows (`min_silence_duration_ms`, `speech_pad_ms`,
`compression_ratio_threshold` / `log_prob_threshold`, `no_repeat_ngram_size`,
`repetition_penalty`, `hallucination_silence_threshold`) are still pending.

### Vocabulary — measured, and it reversed a decision

**The headline finding: every product name added to the hotword list buys
tracked-term accuracy by dropping real speech.** Uncovered speech rises
monotonically with the number of product terms — 66.1s → 86.2s → 125.0s →
166.9s, `control` → `+PHP only` → `lean` → `extended`:

| run | vocabulary | words | repetition run | zero timings | long timings | max word span | tracked terms | uncovered speech |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| reference (older env) | 30 terms | 1390 | 7 | 12 | 7 | 15.42s | 0 / 17 | 108.1s |
| control | 30 terms | 1440 | none | 1 | 4 | 13.34s | 2 / 6 | 66.1s |
| +PHP only | 33 terms | 1419 | 5 | 9 | 15 | 15.82s | 5 / 6 | 86.2s |
| lean | 27 terms | 1316 | none | 12 | 19 | 13.08s | 4 / 4 | 125.0s |
| extended | 38 terms | 1245 | none | 4 | 18 | 23.52s | 7 / 0 | 166.9s |

The 38-term list reached "100% term accuracy" only because the previously
wrong tokens **vanished** along with 145 other words — `wrong` fell to zero
through deletion, not correction. The regression gate caught it and exited 1:

```text
REGRESSED on tiny_timings, long_timings, max_word_span, words 1390 -> 1245
```

That is exactly the failure the word-count guard exists for.

**Second finding: with the 30-term list, Whisper does not mishear the product
names — it omits them.** At 190-212s the reference reads `كله زمانة على
البيتش بي قالك ماتت خلاص`; the control reads `كله زمان على قالك ماتت خلاص`.
PHP is simply gone. That is why those terms score 0 right **and** 0 wrong — a
dropped term is neither, and a fixup cannot recover a token that was never
produced.

**Third finding: the environment upgrade alone was worth more than any
vocabulary change.** Re-running the same 30-term list on the newer
faster-whisper build (`control`, above) cut uncovered speech 108.1s → 66.1s,
removed the `ومكتفي` ×7 repetition loop entirely, and took zero-length timings
from 12 to 1 — before touching the vocabulary at all. Several defects the
reference transcript carried are artifacts of that older build, not something
inherent to the audio.

**The caveat, stated plainly: tracked-term accuracy does not penalise an
omission.** The metric is `right / (right + wrong)`; a term Whisper drops
entirely is neither right nor wrong and never enters the ratio. On `control`,
five of the seven tracked terms are absent outright, so its delivered 100%
(after fixups, below) means "100% of the terms that appear," not "100% of the
terms spoken." Read every tracked-term figure on this page with that in mind,
not only this one.

**Decision: `prompts/ar-tech.txt` is reverted to the 30-term list** (170
characters). The 38-term extension is reverted — it cost 100 extra seconds of
dropped speech for a term-accuracy number the metric was already overstating.
`إكس` is corrected in `prompts/ar-fixups.txt` instead (`اكسة = إكس`,
`اكس = إكس`), which recovers 6 occurrences at no coverage cost and lifts
delivered tracked-term accuracy to 8 right / 0 wrong — with the same caveat
above still attached to that number.

**The open problem is not spelling.** With the best configuration measured —
current environment, 30-term vocabulary, fixups applied — **66 seconds of real
speech across the 12-minute file is still not transcribed at all.** That, not
a wrong word, is the dominant stage-2 defect. The decode-parameter hypotheses
in the table above are the untried levers against it.

**`hallucination_silence_threshold` cannot fix the 15s `آآ`.** Reading
faster-whisper's implementation — before spending a GPU run to find out —
shows it only skips a segment flagged anomalous when that segment is
surrounded by silence longer than the threshold. The 424.6-439.6s window is
not silence: it holds speech at -17.5 dB against a -18.7 dB known-speech
reference. Listed against the invented proper nouns instead, above, where the
mechanism actually applies.

**The prime suspect for the dropped speech is this project's own setting.**
`DECODE` in `asr.py` sets `vad_parameters={"min_silence_duration_ms": 500}`;
faster-whisper's default under `vad_filter=True` is 160, and VAD chunks are
capped at `max_speech_duration_s` regardless (30s). A higher silence threshold
merges speech across short pauses into longer chunks, and a 30s window of
noisy car audio that mostly decodes to nothing is exactly how 15 seconds of
speech becomes one token. The value is justified in the code only as "a big
speed win" and has never been scored for accuracy — that is row 1 of the table
above.

**The acceptance bar is 85% tracked-term accuracy; the baseline is 0.0%.**
`terms_accuracy` is printed by the scorer on every run and is guarded as a
regression by `--baseline`, the same way a word-count drop or a new timing
defect is — a run that pushes it below the previous best fails the gate. The
current delivered figure, 30-term vocabulary plus fixups, is 8/0 — but that
number carries the caveat above and should not be read as "85% cleared."

---

## The measurement trap

Worth its own section because it produced a confident wrong answer.

The first attempt scored `initial_prompt` on a **70-second slice starting at
395 s** and reported 14 errors → 0. The slice *began* where the prompt was
applied. On the full file the same audio sits 6.6 minutes in, and the errors came
straight back.

**Two rules that fall out of this:**

1. **Score seeding parameters over the whole file**, and report the error count
   past 300 s separately. Otherwise a fix that only works near t=0 looks like a
   win.
2. **Watch the word count.** A config that "reduces errors" by dropping speech
   must be visible as a drop in words. An error rate alone cannot tell the
   difference between fixing a word and deleting it.

---

## The seed budget

Whisper's 448-token context is **shared between the seed and decoding**. Overrun
it and faster-whisper dies with:

```text
ValueError: The maximum decoding length must be > 0
```

— which names neither the vocabulary nor the limit. `check_seed_budget` rejects
it up front instead, raising `SeedTooLong` (HTTP 422).

| Budget | Chars |
| --- | --- |
| `HOTWORD_CHAR_BUDGET` | 300 |
| `PROMPT_CHAR_BUDGET` | 800 |

If you need more vocabulary than fits, the terms that earn their place are the
ones that are (a) wrong today and (b) frequent. Everything else is better handled
by `--fixups`, which has no budget at all.

---

## Reframe — crop keeps ~3× the subject pixels

On a 16:9 source:

| Mode | Path | Subject occupies |
| --- | --- | --- |
| `blur` | 3840×2160 → scale to 1080 wide → **1080×608** in a 1920-tall frame | ⅓ of the height, ⅕ of original resolution |
| `crop` | 3840×2160 → **1215×2160** centre slice → 1080×1920 | the full frame, near-native |

Measured on the same clip at the same CRF, **crop carries 2.1× the bitrate** —
there is genuinely that much more detail to encode.

It is also **1.8× faster to render**: 2.59 s against 4.65 s for the same clip.
`gblur=sigma=32` over a full 1080×1920 frame is the expense. So blur costs more
wall-clock for a visibly worse result — measured on a synthetic 1080p source, so
take the ratio and not the absolute times.

`crop` is the default and is right for a centred talking head. Reach for `blur`
only when the framing genuinely needs the full width, and know what it costs.

Neither mode tracks the subject. Real auto-reframe is deliberately deferred: a
perfectly tracked bad clip is still a bad clip.

---

## Resolution and codec

```bash
--resolution 1080p|1440p|4k|WxH|source     # default 1080p
--codec h264|h265                          # default h265
--preset veryslow..ultrafast               # default medium
--10bit                                    # needs a 10-bit source
```

**`--resolution source` resolves per video.** It probes the input and picks the
size that resamples least. A 3840×2160 source in `crop` mode gives **1214×2160** —
the crop region at native pixels, no scaling at all.

In `blur` mode the same source gives **3840×6826**, which is enormous and no
platform accepts. `source` is really only sensible with `crop`.

**Platforms deliver 1080×1920 whatever you upload.** Higher resolutions buy
archival fidelity and re-edit headroom, not a better viewer experience. Upload
1080p; keep the 4K master if you plan to re-cut.

**H.265 must be tagged `hvc1`.** Without `-tag:v hvc1` the file is perfectly valid
and QuickTime, Safari and iOS all silently refuse to open it. `CODECS` sets it;
do not remove it. YouTube accepts HEVC; some Instagram and TikTok upload paths
still prefer H.264, so `--codec h264` stays one flag away.

H.265 is the default and costs encode time — see the preset matrix below.

**`--10bit` is worth it from ProRes** (4:2:2 10-bit): the extra precision
suppresses banding in skies and skin even though delivery chroma is still 4:2:0.
It narrows device compatibility, so it is opt-in.

---

## Render performance

Measured on 4 clips × 18 s → 1080×1920 with captions burned in, 16 cores, a
synthetic 1080p source. **Take the ratios, not the absolute times** — real 4K
ProRes decodes differently.

```text
sequential, -preset medium (current)   10.42 s
4 concurrent, -preset medium            8.63 s   1.21x
sequential, -preset veryfast            6.62 s   1.57x
4 concurrent, -preset veryfast          5.73 s   1.82x
```

**`-preset` is the only real lever**, and it is now a flag (`--preset`, or
`preset` in `JobOptions`). `medium` stays the default — a clip is rendered once
and watched thousands of times, so the default should not trade quality for
minutes — but while iterating on framing or captions a faster preset gives back
most of an hour on a long plan.

### h264 vs h265, across presets

**h265 is the default codec.** It is ~40% smaller at equal quality and the better
archive, and it costs encode time:

```text
preset       h264      h265    h265/h264
medium      2.67s     8.18s      3.06x
fast        2.42s     5.71s      2.36x
faster      2.01s     5.13s      2.55x
veryfast    1.57s     5.09s      3.25x
```

Switching the default to h265 roughly **triples** render time at the same preset
— which is exactly why `--preset` now exists. `h265 veryfast` is 1.61x faster
than `h265 medium`, landing at 1.91x `h264 medium`.

*Sizes in these runs are a synthetic-source artifact: `testsrc2` is pathological
for HEVC prediction, so h265 came out larger. Trust the times, not the sizes;
re-measure sizes on real footage.*

One caveat: `veryfast` produced *smaller* files here (30.9 vs 32.2 MB), which is
atypical. At fixed CRF a faster preset normally costs bitrate. That result is a
synthetic-source artifact; re-measure on real footage before believing the speed
is free.

### Do not parallelise clip rendering

**1.21× on 16 cores.** The render is x264-encode-bound and x264 already saturates
the machine, so running clips concurrently mostly makes them contend. It is not
worth a thread pool that would fight `QATF_WORKERS`, GPU memory, and the
cooperative-cancel checkpoint between clips.

### The filter chain is not where the time goes

Same clip, one stage removed at a time:

```text
full: crop + lanczos + ass + faststart   2.59 s
  without the ass filter                 2.54 s     ass = 2.1% of the render
  bicubic instead of lanczos             2.72 s     lanczos is free
  without -movflags +faststart           2.61 s     free
blur mode                                4.65 s     1.8x crop
```

Three things that look expensive and are not: **burning captions costs 2.1%**,
**`flags=lanczos` costs nothing** over bicubic (it measured faster, within
noise), and **`+faststart` is free**. Leave all three alone.

### Stage 4 and 5a are not worth optimizing

The whole deterministic middle of the pipeline, on a 27,000-word transcript
(3.4 hours) with a 20-clip plan:

```text
snap x20 (whole plan)          94.76 ms
build_ass x20 (whole plan)     47.54 ms
build_transcript_blocks         1.78 ms
json.loads whole transcript    16.26 ms   (1.6 MB)
```

`snap` allocates two 27k-float lists and scans both linearly **per clip** — the
obvious `bisect` target. At 4.7 ms per call against a multi-minute render it is
noise, and converting it would add a sorted-input assumption to the one function
that guards the core invariant. Don't.

### The API layer

`GET /jobs` is linear in the number of jobs, and it is dominated by `stat()`:

```text
 25 jobs x 8 clips     3.96 ms
100 jobs x 8 clips    16.95 ms
400 jobs x 8 clips    71.39 ms      ~0.18 ms/job
GET /jobs/{id}         0.15 ms      flat

per job:  8x path.stat()     76.3 us   <- 75%+ of it
          8x ClipModel        20.5 us
          JobOptions(**opts)   6.9 us
          model_construct     10.3 us  <- SLOWER; not an optimization
```

**Fixed.** The worker records each clip's size when it writes the file, and
`to_response` reads it from the job record. Marginal cost per job in `GET /jobs`
fell from ~180us to **12-20us** — pinned by `tests/load_api.py`, which fails if it
goes back over 250us.

`model_construct` being *slower* than full validation is worth recording: it
still builds the model, and it is the first thing anyone reaches for when they
see pydantic in a hot path.

### The SQLite move: read cost roughly doubled, and stayed inside budget

The in-memory job dict above is gone. It was removed (not merely bypassed) in
the move to SQLite (`qatf/core/db.py`, `qatf/jobs/store.py`): `GET /jobs` and
`GET /jobs/{id}` now run a query plus a `json.loads` of the stored document,
where they used to do a dict lookup. Measured with `tests/load_api.py`, three
back-to-back runs:

```text
                                run 1     run 2     run 3
GET /jobs marginal cost/job    38.2 us   36.1 us   43.9 us
GET /jobs/{id} p99             50.8 ms   50.0 ms   53.1 ms
/healthz serial floor           0.72 ms   0.69 ms   0.70 ms
upload stall, worst poll      191.0 ms  180.0 ms  185.5 ms
```

Before (dict lookup, pre-SQLite, recorded above): **12-20 us/job**. After
(query + `json.loads`): **36-44 us/job across three runs** — roughly a 2x
rise. Every run stayed inside the 250us/job budget `load_api.py` asserts —
5-7x headroom, not a near miss — and `GET /jobs/{id}` p99 (50.0-53.1ms across
the three runs) stayed well under its own 150ms budget. **No assertion in
`load_api.py` was loosened or touched** to get these numbers to pass.

**Why the cost exists.** The dict lookup was cheap because the store kept
every job in memory, but that copy could only ever reflect writes this
process made — a second process, or the same process after a restart mid-run,
writing a job would be invisible to it. Querying SQLite on every read is what
makes `GET /jobs` and `GET /jobs/{id}` correct in the presence of another
writer, rather than merely fast for one that might be the only one. The extra
20-25us/job is the price of that correctness, not a regression to chase down
— and the budget above exists to notice if the price ever climbs past it.

### `/healthz` forked a process per request

The load test found this. A sequential smoke test never would.

```text
                 before        after
p50              200.0 ms      33.7 ms
p99             1051.8 ms      41.9 ms     25x
max             1348.9 ms      51.3 ms
serial floor    ~50-100 ms      1.06 ms
```

`ffmpeg_available()` called `check_ffmpeg()`, which spawns `ffmpeg -version`.
Process creation dominated, on the endpoint monitoring systems and load balancers
poll hardest — it was **20x slower than the endpoint that does real work**.

Two obvious fixes both failed, which is why the code is shaped the way it is:

- **A plain TTL cache** still lets every thread arriving on a cold cache spawn its
  own probe. 24 connections produced 24 concurrent spawns; p99 114ms.
- **A lock around the probe made it worse** — p99 199ms, max 357ms. One spawn
  instead of 24, but 23 threads now block on it. *Serialising a slow thing is not
  the same as removing it.*

The probe never runs on the request path now: `create_app` primes it at startup,
and an expired entry is served stale while a daemon thread refreshes. A
30-second-old health flag is worth far more than a fresh one that costs a request
300ms.

### Under concurrent load

200 jobs, 24 threads, in-process ASGI — so there is no network in these numbers:

```text
read storm       751 req/s    GET /jobs/{id} p50 25ms, p99 43ms
mixed traffic    654 req/s    the shape a polling client produces
write storm      355 req/s    43 concurrent renders correctly refused with 409
upload window    134 MB in 0.59s while 242 polls ran at p50 3.6ms
```

The upload figure is the structural one: that endpoint is `async def` and used to
call a blocking `fh.write`, stalling the event loop for the whole upload. Those
242 polls would have queued behind it.

---

## Captions

| Constant | Value | Why |
| --- | --- | --- |
| `CAPTION_MAX_WORDS` | 4 | |
| `CAPTION_MAX_CHARS` | 22 | four 12-char words at 82 px is wider than 1080 px |

**Both limits are enforced.** Budgeting by word count alone overflows the frame on
long words. `WrapStyle` must be `0` — with `2` (no wrapping) lines get clipped at
both edges, and that passed every dimension check before someone looked at a
frame.

Arabic captions appear and clear **per line** rather than tracking the spoken
word. That is the cost of the RTL fix, and it is deliberate — see
[troubleshooting.md](troubleshooting.md#the-rtl-caption-bug).

---

## How to measure anything here

Three working agreements, each of which exists because a shortcut produced a
confident wrong answer:

**1 · Render and look at a frame.** Any change to a filtergraph or to caption
generation must be verified by rendering a clip and visually inspecting an
extracted frame. ffprobe reporting correct dimensions is **not sufficient** — the
caption overflow bug passed every dimension check.

```bash
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30:duration=20 test.mp4
```

**2 · For text layout, measure glyph positions.** Do not compare frames
byte-for-byte, and do not neutralise the thing you are testing. Both shortcuts
produced confident wrong answers on the RTL bug. Render on a **flat neutral
background** — a busy test pattern hid the yellow highlight entirely on the first
pass.

**3 · Exercise stages 1, 4 and 5 without a GPU or an API key** by seeding
`<work>/words-<model>-<lang>.json` and running with `--plan`. Use that for
render-path work instead of waiting on transcription. (The legacy filename
still works — `asr.read_cache` imports it into `qatf.db` on first read.)

---

## What is still unmeasured

**Stage 2 throughput — and it is the whole ballgame.** Whisper dominates a job by
an order of magnitude over everything on this page, and nothing here has been
tuned beyond `vad_filter=True` (already on, already the big win). faster-whisper
≥ 1.0 ships `BatchedInferencePipeline`, documented at 4–12× on GPU and **not
used**. `pyproject.toml` already pins a version that has it. Measure that on a
real file before optimizing anything else in this document.

**Whisper word-timestamp accuracy on Arabic.** Transcription *spelling* is now
measured; nobody has checked whether the word **boundaries** `snap` relies on land
where the words actually start. This feeds stage 4 directly, so it degrades cut
quality and not just captions. Clip edges are the thing to inspect.

**Arabic selection quality on any non-Claude provider.** See
[providers.md](providers.md#open-question-arabic-selection-quality).

**Loudness.** No normalisation at all. `loudnorm` is a one-line filter add and
the highest value-per-effort item left in the project.
