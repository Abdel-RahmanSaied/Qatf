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
[`prompts/ar-tech.txt`](../prompts/ar-tech.txt) is the working list — 30 terms,
227 characters.

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
`PUT /jobs/{id}/transcript`, or by writing `<work>/word-edits.json` for the CLI.

This is not a tuning lever and does not belong in the table above — it is the
manual floor under it, and it costs one re-render with no model call and no
re-transcription. Keep it out of your measurements: corrections are stored as an
overlay precisely so the cached transcript keeps saying what Whisper actually
produced. The moment a corrected transcript is indistinguishable from a raw one,
every number on this page becomes unreproducible.

### Tested and rejected

So nobody repeats them:

| Change | Result |
| --- | --- |
| `condition_on_previous_text=False` | no effect alone, **worse** combined |
| `beam_size` 8 / 10 | **worse**, and 50% slower |
| `dynaudnorm` | correct terms 24 → **13** |

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
render-path work instead of waiting on transcription.

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
