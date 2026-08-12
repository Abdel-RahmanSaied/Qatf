# qatf (قطف)

Turns a long video into vertical short-form clips. Python, wrapping ffmpeg +
faster-whisper + Claude, with a CLI and a FastAPI job server over one pipeline.

**Status: working prototype, not production.** No CI. Treat everything below
marked UNVERIFIED as unknown rather than working.

Inherits the global `~/.claude/CLAUDE.md` conventions. This file only covers what is
specific to this project.

---

## Layout

Every module lives in a subpackage. Only `__init__.py` and `__main__.py` sit
loose at the package top level.

```text
qatf/
  core/            depends on nothing. imports no pipeline, no jobs, no HTTP
    config.py      Settings, read from the environment once
    constants.py   product decisions (9:16, caption budget, snap margins)
    db.py          the only module that imports sqlite3 — WAL, thread-local
                   connections, PRAGMA-versioned migrations
    dotenv.py      .env parser; the real environment always wins
    errors.py      QatfError hierarchy, each carrying its HTTP status
    types.py       Word, Transcript, Clip — plain dataclasses
    utils.py       subprocess, timestamps, slugs, logging
  pipeline/        the five stages, one module each. the ONLY pipeline logic
    fetch.py       0.  url -> local file        yt-dlp, owns the url allowlist
    audio.py       1.  demux                    ffmpeg
    asr.py         2.  transcribe + word times  faster-whisper
    subs.py        2'. caption track -> words   pure, ALTERNATIVE to asr.py
    fixups.py      2b. substitutions by value   text only, never timestamps
    edits.py       2c. corrections by position  text only, never timestamps
    health.py      2d. loop repair + timing flags  text only, never timestamps
    select.py      3.  pick clips               the configured provider
    cuts.py        4.  snap to word bounds      deterministic
    detect.py      4b. find faces               OpenCV, cached against the video
    framing.py     4c. solve the crop path      deterministic
    captions.py    5a. ASS generation           deterministic
    encode.py      5b. reframe + burn + encode  ffmpeg
  llm/             stage 3 providers — the only swappable part of the pipeline
    base.py        the contract: complete_json + declared Capabilities
    claude.py      Anthropic Messages API, official SDK
    openai_compat.py  everything speaking /v1/chat/completions
    presets.py     named endpoints: openai kimi glm ollama vllm openrouter
  jobs/            knows nothing about HTTP
    model.py       Job record, JobState, on-disk layout
    store.py       persistence, accessors, the thread pool
    worker.py      what a job actually runs
  api/             endpoints only
    __init__.py    create_app + the OpenAPI description
    deps.py        shared dependencies and the media-root boundary
    openapi.py     reusable failure declarations for the schema
    schemas.py     pydantic wire contract
    routers/       meta.py jobs.py plan.py outputs.py
  cli/
    parser.py      the argument surface
    runner.py      preflight + the run flow
qatf.py            legacy shim for `python qatf.py`
tests/             smoke_{db,pipeline,llm,api}.py, load_api.py, score_transcript.py,
                   verify_render.py, fixtures/, _harness.py
                   api_full_flow.py  every endpoint against a RUNNING server and a
                                     real video — the one test the in-process
                                     fakes cannot stand in for
                   sweep_asr.py      one stage-2 run per decode override, in Docker
                   sweep_all.sh      drives sweep_asr.py across the sweep table
docs/              human-facing reference — see "Documentation" below
```

**Dependencies run one way only:**

```text
api -> jobs -> pipeline -> llm -> core
cli --------> pipeline -> llm -> core
```

`JobState` lives in `jobs/model.py`, not in `api/schemas.py`, precisely because
of that arrow: the job lifecycle is a domain concept and `jobs` may not import
from `api`. It still serialises correctly in responses because it is a `str`
Enum. If you ever need to import "upward", the thing you are reaching for is in
the wrong layer.

One module per stage is not decoration either: the core invariant below is a rule
about what may cross a stage boundary, and file boundaries make a violation
visible in a diff. Stages 1, 4 and 5 import no model client, and it should stay
that way.

Import cost is deliberate too. `qatf.cli` pulls in neither pydantic nor fastapi
(there is a check for this); faster-whisper and anthropic are imported inside the
functions that use them.

---

## Commands

```bash
pip install -e ".[all]"                       # ffmpeg must be on PATH
# or pick one provider: pip install -e ".[api,anthropic]" / ".[api,openai]"
export ANTHROPIC_API_KEY=...                  # or OPENAI_API_KEY / MOONSHOT_API_KEY / ZHIPU_API_KEY

# CLI  (also: python -m qatf, or the legacy python qatf.py)
qatf talk.mp4 -o out/ --plan-only             # transcribe + select, no render
qatf talk.mp4 -o out/ --plan out/plan.json    # render a hand-edited plan

# what a real run looks like — noisy Arabic source, clips sized for Shorts
qatf talk.mov -o reels/ --language ar --clips 8 --min-len 28 --max-len 52 \
  --denoise --vocab-file prompts/ar-tech.txt --fixups prompts/ar-fixups.txt \
  --font "Traditional Arabic"
```

`--max-len 52`, not 60: `snap` moves cut points onto word boundaries *after* the
model picks them, so it routinely adds a few seconds. Ask for 60 and some clips
land at 63, which YouTube Shorts rejects. Reels and TikTok allow longer.

`within_duration` allows `DURATION_SLACK` (2s) either side, absolute rather than
proportional — the old `hi * 1.4` admitted 72.8s for `--max-len 52`, which
defeats the whole point of passing 52. Anything further out is the model
overrunning its instruction, and it is dropped **and logged**: a plan silently
returning 7 clips for `--clips 8` reads as the model finding nothing.

```bash

# API
uvicorn qatf.api:app --reload                 # or: qatf-serve / python -m qatf.api

# open-source provider, self-hosted
docker compose --profile ollama up            # GLM-4-9B, easy path
docker compose --profile vllm up              # GLM-4-9B, guided decoding

# tests — no ffmpeg / GPU / API key / network needed
python tests/smoke_pipeline.py                # seconds
python tests/smoke_llm.py
python tests/smoke_api.py
python tests/load_api.py                      # ~20s, 24 threads, asserts
ruff check .

# the one suite that DOES need ffmpeg — it renders clips and measures where the
# subject landed, and exercises stage 4b's decoder directly. Fixture B (real
# face, so the detector too) additionally needs OpenCV and one network fetch
# (cached after the first run); it skips rather than fails without them.
# It honours QATF_FFMPEG, so it does not silently no-op where ffmpeg is off PATH.
python tests/verify_render.py                 # ~40s

# grade a transcript against docs/quality.md's tracked terms (--audio needs ffmpeg)
python tests/score_transcript.py <words.json> --audio <wav>   # grade a transcript
```

Transcription is cached in the `transcripts` table of `<work>/qatf.db`, keyed
on the Whisper size and the forced language (`asr.cache_key`) — keying on the
output directory alone silently reused an English transcript after
`--language ar`. A pre-SQLite `<work>/words-<model>-<lang>.json` is imported
into that table on first read and left on disk; **deleting the JSON alone does
not force a re-transcription** once the row exists — delete the row (or the
whole `qatf.db`) instead. Otherwise iterating on clip selection is free.

---

## Documentation

This file is the working agreement for agents. `README.md` and `docs/` are the
human-facing reference, and the OpenAPI description in `qatf/api/__init__.py` is
the reference for anyone calling the server.

```text
README.md                  entry point: what it is, install, quickstart, honest status
docs/architecture.md       five stages, layering, the core invariant, open risks
docs/cli.md                every flag, with the measurement behind each default
docs/api.md                endpoints, lifecycle, JobOptions, the hand-edit round trip
docs/providers.md          provider matrix, the three output tiers, self-hosting
docs/quality.md            the tuning playbook — every number that was measured
docs/operations.md         install, GPU, Docker, caching, deployment limits
docs/security.md           trust model, where each boundary lives, known gaps
docs/troubleshooting.md    the traps, indexed by symptom
```

Three rules, because documentation that drifts is worse than none:

- **The measured numbers live in `docs/quality.md` and here, nowhere else.** Other
  pages link to them. A number copied into a third place is a number that will
  disagree with itself.
- **Endpoint behaviour is documented in the route decorator and docstring**, not
  only in `docs/api.md` — `/docs` is where a caller actually looks. `smoke_api.py`
  asserts every operation has a summary, a description, a tag, a hand-written
  `operationId` and a declared error shape, so an undocumented route fails the
  suite rather than shipping quietly.
- **Reusable failure declarations go in `qatf/api/openapi.py`.** A status code
  documented on one route and forgotten on the next is how a generated client
  ends up with no error type for a case it will definitely hit.

---

## Trust boundaries

`docs/security.md` is the full picture. The rule that matters while editing:

**Caller text reaches two file formats — the ASS subtitle file and the transcript
cache path — and both are enforced in `pipeline/`, not only at the HTTP layer**,
so the CLI gets the same treatment.

| Boundary | Lives in | Do not weaken |
| --- | --- | --- |
| ASS structure | `captions.escape` / `captions.safe_font` | line terminators and `,` in the Style line |
| cache filename | `asr.cache_path` | both components slugified — `language` is caller-supplied |
| model name | `asr.MODEL_SIZES` + `JobOptions` | `WhisperModel` takes a size **or a path** |
| filter quoting | `encode.filtergraph` | `'` needs ffmpeg's `'\''` idiom |
| cut timings | `edits.diff` | check finiteness *before* comparing — NaN defeats `>` |
| media root / downloads | `api/deps.py` | resolve first, check second — that is what catches symlinks |
| source URL | `pipeline/fetch.validate_url` | https + exact-host allowlist. NOT `.endswith` |

Two habits behind those:

- **Validate at the layer that owns the risk.** `language` is checked in
  `cache_path` because the risk is a path, not a request. The schema check is a
  second line, not the line.
- **Never echo caller input in an error.** The 422 handler in `api/__init__.py`
  reports location and reason only — FastAPI's default embeds the input, which
  both reflects content and fails outright on a non-serialisable `inf`.
  **The handler cannot finish this job alone**: it strips FastAPI's `input`
  field, but it cannot un-say a value a validator formatted into its own
  message, which arrives as part of the *reason*. `whisper`, `preset` and
  `resolution` all quoted the rejected value back for exactly that reason, while
  `smoke_api.py` reported the rule as held — its check only exercised the
  FastAPI-composed path. A live server found it. So: no `{value!r}` in a
  validator message; name the allowed set instead.

---

## Architecture

Five stages. Only two are AI. Keeping that boundary clean is the whole design.

### Device selection (stage 2)

`--device auto` / `"device": "auto"` is the default: it asks CTranslate2 how many
CUDA devices it can use and picks `cuda` if any, `cpu` otherwise. Naming a device
explicitly is honoured and will **not** fall back — asking for `cuda` and
silently getting `cpu` turns a benchmark into a lie.

Availability and usability are separate questions, so there are two layers:

- `resolve_device()` asks CTranslate2, not `nvidia-smi`. A card the installed
  CTranslate2 cannot target (compute capability, driver mismatch) is not a usable
  device, and only the engine knows that.
- `transcribe()` wraps the whole of `_transcribe_on` — model construction AND
  consuming the segment generator — in a try/except, because a device can pass
  the availability check and still fail to load, and faster-whisper builds
  happily on a GPU whose CUDA libraries are missing: the real failure only
  surfaces lazily, on the first encode, which happens while iterating segments.
  A guard around construction alone never sees that, which is why the fallback
  wraps the full call rather than being its own construction-only layer. Under
  `auto` that falls back to CPU with the reason logged; under an explicit
  `cuda` it raises.

The device actually used is recorded on the `Transcript`, echoed in the job record
and `GET /jobs/{id}`, and reported up front by `/healthz` (`cuda_devices`,
`transcribe_device`) — so you can tell *before* submitting an hour of audio
whether it will run on a GPU or crawl. It is deliberately **not** part of the
transcript cache key: a CPU and a GPU transcript of the same audio are
interchangeable enough that re-transcribing after a fallback would be waste.

CPU with `large-v3` is slow enough to warrant the warning stage 2 logs;
`--whisper small` is far quicker if the quality bar allows.

### Transcription quality (stage 2) — measured on real Arabic

Three levers, in order of how much they moved a 12-minute Egyptian-Arabic file
recorded in a moving car. Tracked-error count across the whole transcript:

```text
nothing                       23 wrong,  8 right, 15 wrong after 300s
+ --vocab (hotwords)          19 wrong, 22 right, 14 wrong after 300s
+ --denoise + tuned vocab      7 wrong, 32 right,  5 wrong after 300s
+ --fixups                     3 wrong, 33 right,  1 wrong after 300s
```

- **`--vocab` (faster-whisper `hotwords`)** is the main lever. Write the terms
  the way you want them spelled back. `prompts/ar-tech.txt` is the working list.
- **`--prompt` (`initial_prompt`) seeds only the FIRST ~30s** and then decays.
  It looks excellent on a short clip and does nothing to a long one — see the
  measurement trap below. Prefer `--vocab`.
- **`--denoise`** (speech band + `afftdn`) took 15 → 11 on its own and is ~20%
  *faster*, because a cleaner signal makes the decoder fall back to higher
  temperatures less often. Worth it on any field audio.
- **`--fixups`** is the last resort for words the vocabulary will not take
  (`بايسون` → `بايثون`: the speaker really does say it that way, so Whisper is
  spelling what it heard). Substitutions touch `Word.text` only — **never
  timestamps** — so a spelling fix can change what a caption reads and can never
  move a cut. Applied on read, not baked into the cache.
- **Per-word corrections** (`edits.py`) are the floor under all of it, for the
  errors a substitution structurally cannot reach: a word misheard once where the
  same string is correct elsewhere. `من` → `مين` is unfixable by rule — `من` is
  one of the most common words in Arabic. Keyed by position, stored as an overlay
  in the `word_edits` table of `<work>/qatf.db`, applied on read. For the CLI,
  `<work>/word-edits.json` is still the interface — there is no flag — imported
  into that table and re-imported whenever the file's mtime moves, so editing
  it again after the first run keeps working. `PUT /jobs/{id}/transcript`
  refuses any submission that changes the word count or a timing, so the
  invariant is enforced by the contract, not by discipline. Each correction
  records the text it replaced; if the transcript moves underneath it (different
  Whisper size, `--denoise` toggled) it goes **stale** and is reported rather than
  landing on an unrelated word.

**The overlay is never merged into the cache**, and that is not only about cost:
the cache has to keep saying what Whisper *actually* produced. The moment a
corrected transcript is indistinguishable from a raw one, every number in the
table above becomes unreproducible.

Tested and rejected, so nobody repeats them: `condition_on_previous_text=False`
(no effect alone, worse combined), `beam_size` 8/10 (worse *and* 50% slower),
`dynaudnorm` (correct terms 24 → 13).

**The measurement trap.** The first attempt scored `initial_prompt` on a 70s
slice starting at 395s and reported 14 errors → 0. The slice *began* where the
prompt was applied. On the full file the same audio sits 6.6 minutes in and the
errors came straight back. Score seeding parameters over the whole file, and
report the error count past 300s separately — otherwise a fix that only works
near t=0 looks like a win. Also watch the word count: a config that "reduces
errors" by dropping speech must be visible as a drop in words.

Whisper's 448-token context is shared between the seed and decoding. Overrun it
and faster-whisper dies with `ValueError: The maximum decoding length must be > 0`,
which names neither the vocabulary nor the limit. `check_seed_budget` rejects it
up front instead.

### Stage 0 and stage 2' — a YouTube URL, and captions instead of Whisper

`POST /jobs/url`, or a URL as the CLI's positional argument. Stage 0 downloads
to the job's `source/` directory and rewrites `video` to a local path, so stages
1-5 never learn a URL was involved. A new **source** must not become a new
pipeline.

**The URL is a trust boundary, and a new kind for this project.** `media_root`
bounds which *files* a request may name; nothing bounded which *URLs* it could,
because until now none could. yt-dlp reads `file://` and carries extractors for a
thousand sites, so an unvalidated string is a local-file read and an
outbound-request primitive in one field. `fetch.validate_url` allows https only,
on an exact-host allowlist, refusing userinfo (`https://youtube.com@evil/x`
resolves to `evil`) and ports. **Exact match, never `.endswith`** — that is how
`youtube.com.evil.net` gets in. It is enforced in `pipeline/`, and *again* in the
router so a refusal is a synchronous 403 rather than a 202 and a job that dies on
a worker thread. The suite caught that one: the first version returned 202 for
`file:///etc/passwd`.

**What a caption track actually gives you**, measured on the real 12-minute
Arabic video (724s) against a large-v3 run of the same audio:

```text
                     YouTube ASR   Whisper large-v3
words                       1637               1569
wpm                        135.9              130.5
repetition loops               0                  0
tracked terms            2✓ / 4✗            0✓ / 6✗
cost                  173 KB, ~2s          18m43s CPU
```

Two caveats, and both matter more than the table:

- **That Whisper run used no `hotwords`.** The vocabulary is the main quality
  lever (23 wrong → 19, and → 7 with denoise), so this is YouTube beating an
  *unoptimised* Whisper. A rematch against `prompts/ar-tech.txt` has not been run.
- **`json3` carries `tOffsetMs` — a START — and no end anywhere.** So a caption
  word's `end` is the *next* word's start: an upper bound, not a measurement.

That second point is the whole design. `Transcript.timing_source` records it,
survives the cache (schema v4) and the plan round trip, and `cuts.tail_for`
reads it to drop `SNAP_TAIL` to **zero**. Adding 0.35s to an upper bound does not
extend into silence — it extends past the point the next word already started,
slicing its first phoneme. That is the mid-syllable cut the core invariant exists
to prevent, and nothing downstream could see it: the plan looks right, the
durations look right, only listening reveals it.

A hand-uploaded caption track is line-level, has no per-word offsets, and is
**refused** for this purpose — `subs.has_word_timings` is the gate. The job then
falls back to Whisper and says so. That fallback is deliberately a log, not a
failure, unlike `--device cuda` and `--reframe track`: those two refuse because
they have no better alternative to fall back *to*, whereas here the fallback is a
fall *up* in quality and costs only time.

`subs.py` imports no network client and parses a document, so it is testable from
a fixture with no yt-dlp and no network — same split as `select.parse_response`.
Rolling `aAppend` events need no special handling, which is measured rather than
assumed: of 454 events, 226 carry `aAppend` and all 226 hold only blank segments.

### Reframe: crop keeps ~3x the subject pixels

On a 16:9 source, `blur` scales the whole frame to 1080 wide — a 3840x2160
source becomes **1080x608** sitting in a 1920-tall frame, so the subject
occupies a third of the height at a fifth of its original resolution. `crop`
takes a 1215x2160 centre slice and scales it to 1080x1920: nearly native, full
frame. Measured on the same clip at the same CRF, crop carries **2.1x the
bitrate** because there is genuinely that much more detail to encode.

`blur` is also **1.8x slower to render** (4.65s vs 2.59s on the same clip) —
`gblur=sigma=32` over a full 1080x1920 frame. So it is slower *and* softer.

`crop` is the default and is right for a centred talking head. Reach for `blur`
only when the framing genuinely needs the full width — and know what it costs.

`track` is `crop` with a moving x, so it carries the same subject pixels and
costs the same to encode; the extra cost is stage 4b, which is linear in
`--track-tier`. It is a third mode rather than a change to `crop` deliberately:
every number above was measured against the two static graphs, and a mode that
did not exist cannot invalidate them. Read open risk #2 before promising anything
about it.

### Performance — measured, and mostly negative results

Synthetic 1080p source, 4 clips x 18s to 1080x1920, 16 cores. Ratios transfer;
absolute times do not.

```text
sequential, -preset medium (current)   10.42s
4 concurrent, -preset medium            8.63s   1.21x
sequential, -preset veryfast            6.62s   1.57x
```

**`-preset` is the only real lever in stage 5.** Now a flag on both front ends,
defaulting to `medium` — a clip is rendered once and watched many times, so the
default must not trade quality for minutes.

```text
preset       h264      h265    h265/h264
medium      2.67s     8.18s      3.06x
veryfast    1.57s     5.09s      3.25x
```

Four things that look worth optimizing and are not:

- **Parallel clip rendering: 1.21x.** The render is x264-encode-bound and x264
  already saturates the machine. Not worth a pool that would fight
  `QATF_WORKERS`, GPU memory and the cancel checkpoint between clips.
- **The filter chain.** The `ass` filter is 2.1% of a render; `flags=lanczos`
  measured *faster* than bicubic; `+faststart` is free. Leave all three.
- **Stage 4 and 5a.** On a 27,000-word transcript with a 20-clip plan: `snap`
  x20 is 95ms, `build_ass` x20 is 48ms. `snap`'s two linear scans per clip are
  the obvious `bisect` target and converting them would buy nothing while adding
  a sorted-input assumption to the function that guards the core invariant.
- **`model_construct` in `to_response`.** It is *slower* than full validation
  (10.3us vs 6.9us) — it still builds the model.

Where the API time actually goes, and both are now fixed:

- **`GET /jobs` stat()ed every clip on every request** — 75% of its cost, scaling
  with job count on the endpoint clients poll. The worker records each size when
  it writes the file; marginal cost fell ~180us -> 12-20us per job.
- **`/healthz` spawned an ffmpeg process per request.** p99 was over a second
  under load, twenty times `GET /jobs/{id}`. Two obvious fixes both failed: a
  plain TTL cache still lets a cold-cache herd spawn 24 probes (p99 114ms), and a
  lock around the probe made it *worse* (p99 199ms, max 357ms) because 23 threads
  then block on one slow thing. **Serialising a slow thing is not removing it.**
  The probe is primed at startup and served stale-while-refreshing; the handler is
  now ~1ms.

Both regressions are pinned by `tests/load_api.py`, which fails on them.

**Stage 2 dominates a job by an order of magnitude and is untuned** beyond
`vad_filter=True`. faster-whisper >=1.0 ships `BatchedInferencePipeline`
(documented 4-12x on GPU, unmeasured here, not used). Measure that before
optimizing anything else.

### Output resolution and codec

```bash
--resolution 1080p|1440p|4k|WxH|source     # default 1080p
--codec h264|h265                          # default h265
--preset veryslow..ultrafast               # default medium — THE render lever
--10bit                                    # needs a 10-bit source
```

`--resolution source` resolves per-video: it probes the input and picks the size
that resamples least. A 3840x2160 source in `crop` mode gives **1214x2160** —
the crop region at native pixels, no scaling at all. In `blur` mode the same
source gives 3840x6826, which is enormous and no platform accepts; `source` is
really only sensible with `crop`.

Platforms deliver 1080x1920 whatever you upload, so higher resolutions buy
archival fidelity and re-edit headroom, not a better viewer experience.

**H.265 is the default** — ~40% smaller at equal quality, the better archive,
and measured **3.06x libx264 at the same preset**. That is why `--preset` exists;
`h265 veryfast` gets 1.61x back. YouTube accepts HEVC; some Instagram and TikTok
upload paths still prefer H.264, so `--codec h264` is one flag away.

**H.265 must be tagged `hvc1`.** Without `-tag:v hvc1` the file is perfectly
valid and QuickTime, Safari and iOS all silently refuse to open it. `CODECS`
sets it; do not remove it.

`--10bit` is worth it from ProRes (4:2:2 10-bit): the extra precision suppresses
banding in skies and skin even though delivery chroma is still 4:2:0. It narrows
device compatibility, so it is opt-in.

### Core invariant — do not violate

**The model never emits precise timing.** It sees the transcript in ~12s blocks
(`select.build_transcript_blocks`) and returns `MM:SS`. Stage 4 (`cuts.snap`)
then moves those onto real word start/end times from Whisper.

If you are ever tempted to ask the model for millisecond timestamps, or to skip
`snap` because the model's numbers "look fine" — don't. The model will confabulate
sub-second values it has no way to know, and clips will open mid-syllable. Semantic
boundaries come from the model; acoustic boundaries come from Whisper. Never mix.

This applies to **hand-edited plans too**, which is why `PUT /jobs/{id}/plan` and
`--plan` re-snap by default. A human typing `"start": 20.0` is making the same kind
of semantic guess the model makes.

Because of that default, **`snap` must stay idempotent** — the round trip runs it
over its own output, so a value it already produced has to be a fixed point. It
was not: `SNAP_TAIL` is 0.35s while Arabic word ends measure ~0.45s apart, so the
re-search landed on the *next* word and every edit-and-resubmit cycle pushed the
end out ~0.5s. Five round trips grew a real clip from 56.70s to 59.21s — a clip
chosen to sit under 60s crossing it, silently, with nothing in the diff to show
for it. `snap` also returns a **new** `Clip` rather than rewriting the caller's:
the first test written for that drift compared `before` with `after` and saw
nothing, because the in-place mutation had made them one object.

Corollary: clip quality problems are usually a stage-3 prompt issue. Clip *edge*
problems are always a stage-4 issue. Diagnose them separately.

---

## Stage 3 providers

Stage 3 is the only model call in the pipeline, and its contract is one line:
transcript in, JSON clip list out. That is what makes the provider swappable —
and it bounds the blast radius. **A provider swap cannot affect cut accuracy or
rendering**, because stages 1, 4 and 5 are model-free. It changes only *which
passages* get picked.

```bash
QATF_LLM_PROVIDER=anthropic   # openai | kimi | glm | ollama | vllm | openrouter
QATF_LLM_MODEL=...            # override the preset's default model
QATF_LLM_BASE_URL=...         # point a preset at another host (proxy, self-host)
```

Two SDK families, deliberately: the official `anthropic` SDK for Claude, and the
`openai` SDK for everything OpenAI-compatible. **Never route Claude through an
OpenAI-compatible shim** — it costs schema-constrained output, adaptive thinking
and prompt caching of the transcript prefix, all of which this stage uses.

### Structured output is three tiers, not a boolean

| Tier | Providers | What you get |
| --- | --- | --- |
| `json_schema` | Anthropic, OpenAI, vLLM | Constrained decoding. Malformed output is impossible. |
| `json_object` | Kimi, GLM, Ollama, OpenRouter | Valid JSON guaranteed, shape is not. |
| `prompt_only` | anything else | Nothing but the prompt. |

`parse_response` is the layer every tier shares, which is why it stays defensive
(fence stripping, wrapper-key tolerance, bare-array fallback) even though the top
tier makes it redundant. Deleting that defence would break the bottom two tiers
silently, at selection time, on someone else's video.

The requested shape is `{"clips": [...]}` and not a bare array because **OpenAI
strict mode rejects an array at the schema root**. A bare array is still accepted
on parse, since json_object-only providers routinely drop the wrapper.

### Capabilities are declared, never probed

A rejected parameter is a 400, not a graceful degrade. Claude Opus 5 and the
GPT-5 family both reject `temperature`; the GPT-5 family renamed `max_tokens` to
`max_completion_tokens` and rejects the old name. So `Capabilities` on each
preset states what may be sent, and the abstraction never blanket-forwards a
parameter. Adding a vendor should be **a row in `presets.py`**, not a subclass.

Where a vendor's support is ambiguous the preset is deliberately pessimistic:
over-claiming fails the request, under-claiming just leans on `parse_response`.
Ollama is pinned to `json_object` for a sharper reason — it *ignores* unknown
fields rather than erroring, so a `json_schema` request there would silently
produce unconstrained output, which is worse than a failure.

### Self-hosting: what actually fits

`docker compose --profile ollama up` (easy) or `--profile vllm up` (guided
decoding, so `json_schema` is real). Both pull GLM-4-9B, which runs on one
16-24GB GPU quantised.

**Kimi K2 is ~1T parameters and does not self-host on a consumer GPU.** Use the
hosted Moonshot API or OpenRouter. Anyone who reads "open-source model" as
"therefore local" will burn an afternoon on this.

### Open question: Arabic selection quality

The provider roster is untested against the thing this project exists for.
GLM and Kimi are strongest on Chinese and English; their Arabic *judgment* —
picking a self-contained passage, hearing a hook — is unmeasured. Claude and GPT
are the safer assumption there, and that is an assumption, not a measurement.
Before switching the Arabic path to an open model, A/B it on the same transcript
and read the clips. `--plan-only` makes that cheap: same cached transcript, two
providers, diff the two plan.json files.

---

## API shape

The pipeline takes minutes, so nothing is synchronous. Every start returns `202`
and a job id; the client polls `GET /jobs/{id}`.

```http
POST   /jobs                  start from a server-side path (sandboxed to media root)
POST   /jobs/upload           multipart; options is a JSON string form field
POST   /jobs/url              fetch a YouTube URL (stage 0). 403 off the allowlist
GET    /jobs                  list, optional ?state=
GET    /jobs/{id}             state, message, error, plan, outputs
POST   /jobs/{id}/cancel      cooperative
DELETE /jobs/{id}             refuses while running
GET    /jobs/{id}/transcript  words, as they will be captioned
PUT    /jobs/{id}/transcript  correct misheard words; text only, never timings
GET    /jobs/{id}/plan
PUT    /jobs/{id}/plan        the hand-edit round trip; re-snaps unless snap:false
POST   /jobs/{id}/render      encode the current plan; replaces previous outputs
GET    /jobs/{id}/clips       + /{name} to download
GET    /healthz               reports whether ffmpeg is actually on PATH
```

States: `queued → fetching → extracting → transcribing → selecting → planned →
rendering → done`, plus `failed` and `cancelled`. `fetching` only occurs on a
`source="youtube"` job and is its own state because it is the one stage whose
duration depends on somebody else's network. Set `auto_render: false` to stop at
`planned` for review.

Settings come from `qatf.config.Settings`; `create_app(settings=...)` takes an
explicit one, which is how the tests point at a scratch directory without
touching `os.environ`. Env names: `QATF_DATA_DIR`, `QATF_MEDIA_ROOT`,
`QATF_WORKERS`, `QATF_MAX_UPLOAD_MB`, `QATF_MODEL`, `QATF_HOST`, `QATF_PORT`.

Routers raise `QatfError` subclasses and a single exception handler maps
`status_code`. Don't reintroduce per-case `HTTPException` mapping for domain
failures — HTTP concerns stay out of the pipeline entirely.

### Three things the job model does not do

Deliberate, given the dependency budget. Know them before promising anything:

- **Jobs do not survive a restart.** State is a JSON file per job, but the worker
  is an in-process thread pool. On startup anything left running is marked
  `failed: interrupted by a server restart`. That is honest, not a bug — but it
  is the first thing to fix if this ever runs behind a real deployment.
- **Cancellation is cooperative.** The flag is checked between stages and between
  clips. It cannot interrupt an ffmpeg or Whisper call already in flight.
- **`QATF_WORKERS` defaults to 1.** Two concurrent `large-v3` loads fight over the
  same GPU. Raise it only for CPU-bound render-only work.

`media_root` is a security boundary, not a convenience: without it a `POST /jobs`
body naming `../../etc/passwd` would transcribe any file the process can read.
Absolute paths must still resolve inside it.

---

## Verification status

Be honest about this in any session. It is the difference between a demo and a tool.

**Verified — executed and inspected:**
- **The pipeline has now run end to end** (stages 1, 4, 5 against real ffmpeg,
  with a seeded transcript cache standing in for stage 2 and `--plan` for stage
  3). Output is 1080x1920, 30fps, yuv420p, audio intact, captions burned in.
- Both filtergraphs (`crop`, `blur`) produce 1080x1920 output with correct duration
- `tests/verify_render.py` (10 checks without OpenCV, more with it): the `track`
  path rendered through real ffmpeg and measured — the tracked render holds the
  subject in every probed frame and the `crop` control provably loses it. Plus
  stage 4b's decoder, which needs ffmpeg but **not** OpenCV and so runs where
  fixture B skips: grid anchoring, that a re-sampled span reproduces the same
  instants, that a failed decode raises instead of reading as "no faces", that
  abandoning a decode does not deadlock, and that a missing ffmpeg arrives as
  `FFmpegNotFound`. The re-sampling check found a real drift bug (3.334 vs
  3.333 for one instant) that one run alone cannot show.
- Captions burn in and render inside frame — confirmed by extracting PNGs and
  looking at them, in English **and** Arabic
- RTL word order, after the fix — Arabic reads correctly right-to-left with
  connected letterforms, on both Arial and Traditional Arabic
- `tests/load_api.py` (23 checks): every endpoint under 24 concurrent threads —
  seed, read storm, list scaling, write storm against one job, upload while
  polling, mixed traffic, concurrent deletion. Asserts no 5xx anywhere, no
  corrupted job record, that concurrent renders are refused with 409, and holds
  budgets for per-job list cost, `/healthz` serial cost and poll latency during
  an upload.
- `tests/smoke_db.py` (23 checks): `core/db.py` in isolation from `jobs` and
  `pipeline` — WAL and `busy_timeout` are actually on, the schema version is
  stamped, connecting twice neither duplicates nor drops a table, a v1 database
  migrates forward to v2 without losing a row a v1 client wrote, each thread
  gets its own connection object while the same thread reuses one, a failed
  transaction leaves nothing behind, and a corrupt file raises rather than
  quietly returning an empty database.
- `tests/smoke_pipeline.py` (354 checks): timestamp formatting and carry, slugify,
  caption grouping under both budgets, ASS escaping, RTL detection and the
  no-per-word-tags rule, filtergraph escaping and mode rejection, encoder flags
  (no forced `-r`, crf forwarded), device resolution and the CUDA-to-CPU
  fallback, transcript cache keys including vocabulary, fixups (with an explicit
  assertion that timings are untouched), per-word corrections (that `diff`
  refuses a retiming or a changed word count, that a shifted overlay goes stale
  rather than corrupting a word, and again that timings are untouched), snap edge
  cases, model-response parsing, the font-availability warning (that a missing
  family warns, that an absent `fc-list` skips the check rather than warning,
  and that the lookup uses `safe_font`'s output), and the trust boundaries —
  that caption text and font names cannot inject ASS directives, and that
  `language` cannot escape the work directory through the cache filename
- `tests/smoke_llm.py` (38 checks): provider request shapes with the SDK client
  faked — that Anthropic gets `output_config.format` and no sampling params,
  that GPT-5 gets `max_completion_tokens`, that Kimi/GLM/Ollama downgrade to
  `json_object` rather than erroring, that vLLM keeps `json_schema`, refusal and
  truncation handling, the context guard, and `parse_response` across all three
  output tiers. Proves request *shape*, not that any endpoint accepts it.
- `tests/smoke_api.py` (154 checks): job state machine, transcript cache round
  trip, the transcript correction round trip (correction reaches the burned-in
  captions, cut points provably unchanged, retiming/add/remove all refused, the
  overlay stays out of the cache file), plan replace with and without re-snap,
  re-render replacing outputs,
  upload size/extension/JSON limits, media-root escape rejected, download
  traversal rejected, restart recovery, input validation at the boundary
  (traversal in `language`, an unlisted `whisper` model, an oversized plan,
  non-finite timings, and that a 422 never echoes the rejected input back),
  and the OpenAPI document — that every
  operation is summarised, described, tagged and hand-named, that every failure
  a caller can hit is declared and typed as `ErrorResponse`, and that a status
  code shared by two failures keeps both descriptions. It fakes
  `pipeline.audio.run`, `pipeline.encode.run`, `pipeline.asr.transcribe` and
  `pipeline.select.pick_clips`, so it proves nothing about those four.

- **The whole product has now run on real material** — a 12-minute 4K ProRes
  Arabic video, 75 GB, recorded in a car. All five stages: demux, Whisper on a
  real GPU, clip selection through OpenRouter, snap, and render. Output is
  8 vertical clips under 60s with burned-in Arabic captions. Stage 2 and stage 3
  interiors are therefore no longer unverified.

- **The HTTP API has now run the same material end to end** — `POST /jobs` on the
  12-minute 4K ProRes Arabic file (73 GB), all five stages through the server:
  demux + denoise, `large-v3` on CPU (1569 words, `language=ar` at p=1.000),
  stage 3 through OpenRouter, snap, and 8 rendered clips. Every clip is
  1080x1920 hevc, tagged `hvc1`, AAC audio intact, duration matching the plan,
  and `30000/1001` preserved rather than forced to 30. Also confirmed live, none
  of which the in-process fakes can show: `snap` is idempotent across five plan
  round trips on real Arabic word spacing (clip 1 flat at 50.680s); a transcript
  correction leaves the plan byte-identical while a retiming, an added word, a
  removed word and a non-finite timing are each refused 422; `GET /clips` sizes
  match `stat()`; a download is byte-identical to the file on disk; and no
  validator echoes a rejected value back. **Stage 5a is NOT clean** — see open
  risk #4.

**UNVERIFIED — never executed:**
- **Every provider except OpenRouter, against its real endpoint.** OpenRouter has
  now served a real request (Claude Opus 5, json_object tier). The rest —
  Anthropic direct, OpenAI, Kimi, GLM, Ollama, vLLM — remain documentation-only.
  `smoke_llm.py` pins what we *send*; those endpoints have never replied.
  OpenRouter's own default model ID was already stale when checked
  (`kimi-k2` → `kimi-k3`), so expect the same of the others.
- Arabic *selection* quality on any non-Claude provider (see Stage 3 providers).
- Whisper word-timestamp *accuracy* on Arabic. Transcription spelling is now
  measured, but nobody has checked whether the word boundaries `snap` relies on
  land where the words actually start. Clip edges are the thing to inspect.
- **The API on a GPU host.** It has now run a real video end to end with a real
  key, but `transcribe_device` was `cpu` throughout — `cuda_devices: 0`. Nothing
  about the server's GPU behaviour, or `QATF_WORKERS > 1` contending for one, has
  been executed.
- **The entire Arabic path.** See below.

---

## Open risks, in priority order

### 1. Arabic captions — the shaping question is ANSWERED; timing is not

This is the differentiator. Every competitor (Opus Clip, Klap, Vizard, Submagic,
Choppity, quso, 2short) is English-first.

- **RTL shaping and bidi: measured, and it was broken.** libass starts a new bidi
  run wherever an override tag causes an actual style change, so per-word
  highlighting chopped an RTL line into independently-reordered runs and
  scrambled the word order. Fixed by not highlighting per word on RTL — see
  "The RTL caption bug" below. Arabic now renders correctly: right-to-left order,
  connected letterforms, inside frame, confirmed on rendered frames.
- **Whisper word timestamps on Arabic** degrade relative to English. This feeds
  `snap` directly, so it degrades cut quality, not just captions. **Still
  unmeasured** — it needs a real Arabic recording, not a synthetic transcript.
- **Fonts.** `--font Arial` renders tofu. Needs an Arabic-capable face installed on
  the rendering host. libass falls back silently, which is how you ship 50 clips in
  the wrong typeface without noticing. Under the API the rendering host is the
  server, not the caller's machine.

  **Now warned about, not fixed.** `captions.font_warning` asks `fc-list` whether
  the requested family exists and logs a warning if it does not — from the CLI's
  `preflight` *and* from `jobs/worker.py` before stage 5, because under Docker the
  rendering host is the server and warning only in the CLI would leave the
  deployment that actually renders silent. Three deliberate properties:
  - **A warning, never a refusal.** Unlike `--device cuda` and `--reframe track`,
    which raise rather than degrade, there is no correct alternative to hand the
    caller, and refusing an hour-long job over a font name costs more than an
    ugly substitution.
  - **No fontconfig means "cannot tell", never "missing".** A stock macOS host has
    no `fc-list`; a warning that fires because the *checker* is absent is one
    people learn to ignore. The check silently skips instead.
  - It asks about `safe_font(name)`, the string that actually reaches the
    `Style:` line — not the raw argument, which libass is never asked to resolve.

  Verified in the image: `python:3.12-slim` + the Dockerfile's apt line already
  carries `/usr/bin/fc-list` (fontconfig 2.15.0, pulled in as a dependency) and
  5 Naskh families, so no Dockerfile change was needed. **This is a warning about
  a fallback, not a check that the face has Arabic glyphs** — an installed Latin
  font passes it and still renders tofu.

Related and already visible: `slugify` is ASCII-only, so an all-Arabic title
produces `02-clip.mp4`. Fine while filenames are internal; not fine once a user
sees them.

### 2. Tracking frames a face, not a speaker

`--reframe track` exists: stage 4b (`detect.py`, YuNet via OpenCV) samples face
positions, stage 4c (`framing.py`) solves them into a crop path, stage 5b drives
`crop=x` with `sendcmd`. `tests/verify_render.py` renders and measures where the
subject actually landed, with `crop` as a control that must fail.

**The active-speaker model is not built.** Every `Detection` carries
`speaking=0.0`, so `framing.subject` always takes its largest-face fallback and
will frame the listener in a two-shot — on every tier, `best` included. The tiers
differ only in sample rate today. Say so before claiming multi-speaker support.

**Every `TRACK_*` constant is an unmeasured starting value.** They are product
decisions so they live in `core/constants.py`, but none has been scored against
real footage, which is why none of them is in `docs/quality.md` — that file is
for numbers somebody measured. Two of them encode a real trade worth knowing:

- `TRACK_SHOT_JUMP` (a distance floor) and `TRACK_SHOT_SPEED` (frame widths per
  second) together decide what counts as a cut. One number cannot: the tiers
  sample 8x apart, so a per-sample distance means something different in each.
  A jump must clear both, and it must be confirmed on **both sides** — the new
  position persists into the next sample, and the position it left was itself
  established. A lone bad detection produces two large steps, and confirming
  only the first half still whips on the way back.
- Consequence: at `fast` (1 fps) a cut moving the subject under ~0.75 of frame
  width is indistinguishable from a sprint and gets smoothed across. That is the
  right direction to be wrong in — a missed cut costs one bounded pan, a false
  one costs a whip on every walking step.

Deliberately still behind selection quality: a perfectly tracked bad clip is
still a bad clip.

**The face cache keys on the footage, not the output directory.** Stored in the
`detections` table of `<work>/qatf.db`, row-keyed
`faces-<detector>-<tier>-<key>.json` (still that exact string — a row key now,
not a filename), where `key` hashes (resolved path, size,
mtime) plus `DETECT_WIDTH` and `YUNET_SCORE`. Both halves earn their place: two
videos rendered into one `-o` directory shared a cache and the second inherited
the first's face positions with `fallback=False`, and a knob outside the key is a
knob that silently does nothing on a warm work directory. Same lesson as
`--language ar` and `asr.cache_path`. Bias a cache key toward re-computing:
over-triggering costs one re-detection, under-triggering ships fifty confidently
mis-framed clips.

### 3. Missing basics

No loudness normalization (`loudnorm` is a one-line filter add, highest
value-per-effort item here), no silence trimming, no scene-change detection.

### 4. Caption cues overlap — measured, unfixed

Listed last only to avoid renumbering; **by impact it outranks 2 and 3**, because
it is visible in every clip the tool currently ships.

`LAST_WORD_HOLD` (0.12s) is added to the end of every caption line and nothing
clamps it against the next line's start. On continuous speech the next line
almost always begins inside that window, so two `Dialogue:` events are live at
once and libass stacks them — for ~3 frames the viewer sees **the upcoming
caption sitting above the one still on screen**.

Measured on the real 12-minute Arabic file, first three clips:

```text
01-clip.ass   28 cues, 22 overlapping pairs   e.g. 5.74-9.31 vs 9.19-10.57
02-clip.ass   26 cues, 20 overlapping pairs
03-clip.ass   30 cues, 29 overlapping pairs
              71 of 83 consecutive pairs — 85%
```

It hits **LTR as well as RTL**: the highlight path holds each word until the next
word starts *within* a chunk, but the last word of every chunk still takes the
unclamped `+ LAST_WORD_HOLD`.

Found by rendering a clip and extracting a frame — the `.ass` file reads as
entirely correct, which is the same trap the RTL bug set. A cue-timing assertion
in `smoke_pipeline.py` would have caught it without a render, and there is no
such assertion today: nothing anywhere checks that consecutive cues are disjoint.

The fix is a clamp in `build_ass`, but the product decision is not obvious and
should be made deliberately: clamping the end to the next cue's start removes the
hold wherever speech is continuous (captions become gap-free hand-offs), whereas
holding the line and pushing the *next* cue later trades the overlap for a brief
gap with no caption at all.

---

## The RTL caption bug (found by rendering, 2026-08)

Worth its own section because it is subtle, it is invisible in the .ass file, and
the obvious ways to test it all give false passes.

**Mechanism.** libass starts a new bidi run wherever an ASS override tag causes
an *actual* style change. `build_ass` wraps the active word in `{\c...}`, which
splits the line into runs that get bidi-reordered independently — so on RTL text
the visual word order changes depending on which word is highlighted.

**How it was measured.** Walk the highlight along a line, render one frame per
position, and track the horizontal centre of the yellow pixels:

```text
english (LTR)   sweep >>>>   OK   — left to right, as it should be
arabic  (RTL)   sweep >>>    BAD  — should be right to left
hebrew  (RTL)   sweep >>>    BAD
```

**Three tests that give a false pass — do not trust them:**

- *Reading the .ass file.* It looks correct either way. This is the trap
  CLAUDE.md already warned about, and it is real.
- *Neutralising the highlight colour to compare against plain text.* Setting the
  highlight to the style's own colour removes the style **change**, so libass
  never splits the run. The comparison then measures a line with no effective
  override in it and reports a perfect match. This produced a confident, wrong
  "verified" result before the position measurement caught it.
- *Comparing rendered frames byte-for-byte.* Splitting a line also shifts outline
  seams by a pixel or two, so a strict pixel diff flags **English** — which
  renders perfectly — as broken. Measure glyph position, not pixel equality.

**What does not fix it:** Unicode bidi controls (RLE/PDF, RLM, FSI/PDI isolates,
per-word isolates), pre-reversing the words, and `\k` karaoke. All still split.

**The fix in place:** `build_ass` does not emit per-word tags when the line
contains any RTL character (`captions.is_rtl`). RTL lines get one cue spanning
the whole caption line, which lays out correctly because nothing splits the run.
LTR is untouched and keeps word-by-word highlighting. Pass `highlight=True` to
force the old behaviour — the only reason to is to re-measure the bug.

**The cost:** Arabic captions appear and clear per line rather than tracking the
spoken word. If word-level highlight on RTL ever becomes a requirement, it needs
per-word `\pos` with measured text widths, or a renderer other than libass.
Neither is warranted yet.

---

## Gotchas found the hard way

- **ASS `WrapStyle`.** Must be `0`. With `2` (no wrapping) caption lines overflow
  the 1080px frame and get clipped at both edges. Caught only by looking at a
  rendered frame.
- **Caption line length must be budgeted by characters, not word count.** Four
  12-char words at 82px is wider than 1080px. `group_words` enforces both limits.
- **ASS colours are BGR, not RGB.** `&H00E0FF&` is yellow.
- **`{` and `}` in caption text must be escaped** or they're parsed as ASS override
  tags. `captions.escape` maps them to parens.
- **ffmpeg filter paths need `:` escaped** when passed to the `ass` filter, and
  backslashes turned into forward slashes.
- `-ss` goes before `-i` (fast seek). Timestamps reset to 0, which is why ASS times
  are written relative to clip start.
- **Never force `-r` on NTSC-rate footage.** `render` used to hardcode `-r 30`;
  on a 30000/1001 (29.97) source that makes ffmpeg duplicate roughly one frame
  every 33 seconds — invisible in a still, a periodic micro-stutter in motion.
  `fps=None` (the default) preserves the source rate.
- **Round to centiseconds before decomposing a timestamp, not after.** The original
  `ts_ass` bumped seconds on a centisecond spill but never carried 60s into a
  minute, so `59.999` formatted as the invalid `0:00:60.00`. Reachable — cue times
  are clip-relative and clips run to 75s.
- **`app.routes` does not show included routers** since FastAPI 0.141; they are
  wrapped in `_IncludedRouter` with no `.path`. Enumerate endpoints via
  `app.openapi()["paths"]` or you will "verify" four built-in routes and nothing else.
- **The package directory shadows the sibling `qatf.py`** on import — Python's
  path finder checks directories before same-named modules — which is what stops
  the legacy shim importing itself. Verified after the move. Don't add a third
  `qatf.py` inside the package.

---

## Working agreements

- **Any change to a filtergraph or to caption generation must be verified by
  rendering a clip and visually inspecting an extracted frame.** ffprobe reporting
  correct dimensions is not sufficient — the overflow bug passed every dimension
  check. Generate a test source with
  `ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30:duration=20`.
  For the reframe path that inspection is now automated: `tests/verify_render.py`
  renders and measures where the subject landed. **Every fixture there renders
  `crop` as a control and asserts the control FAILS**, because a control that
  cannot fail is measuring nothing — twice now a broken harness reported the
  subject absent from both renders and read exactly like a broken feature
  (`drawbox` evaluating `x` at init; then bgr24 read as rgb24).
- **For anything text-layout related, measure glyph positions — do not compare
  frames byte-for-byte, and do not neutralise the thing you are testing.** Both
  shortcuts produced confident wrong answers on the RTL bug (see above). Render
  on a flat neutral background; a busy test pattern hid the yellow highlight
  entirely on the first pass.
- **Stages 1, 4 and 5 can be exercised without a GPU or an API key** by seeding
  `<work>/words-<model>-<lang>.json` and running with `--plan`. Use that for
  render-path work instead of waiting on transcription. (The legacy filename
  still works — `asr.read_cache` imports it into `qatf.db` on first read.)
- **Both smoke suites must stay green, and new behaviour gets a check.** They run
  in seconds with no external dependencies; there is no excuse for skipping them.
  `ruff check .` too.
- **Pipeline logic goes in `qatf/pipeline/`, one stage per module.** If you find
  yourself computing a timestamp in a router or in `cli/runner.py`, it is in the
  wrong file. Front ends parse input, report progress, and set exit codes.
- **Respect the layer arrows.** `api -> jobs -> pipeline -> core`, and `core`
  imports nothing of ours. An import that points the other way is the signal that
  something is defined in the wrong package — move the definition, don't add the
  import.
- Deliberate failures raise `QatfError` subclasses. Bare `RuntimeError` escaping
  the pipeline means something was not thought through.
- **Adding a provider is a row in `presets.py`.** If it needs a subclass, the
  reason must be a real protocol difference, not a different `base_url`. Declare
  capabilities pessimistically and never blanket-forward a parameter — a
  rejected one is a 400, not a degrade.
- No new dependencies without a stated reason. The pipeline needs only ffmpeg
  and faster-whisper; fastapi/uvicorn/pydantic sit behind `[api]` and every
  provider SDK behind its own extra, so installing one provider does not pull
  the others. The CLI must keep working without any of them. The job queue is a
  thread pool on purpose; Celery and Redis are not warranted yet.
- **Load-test concurrent behaviour; a sequential suite cannot see it.**
  `tests/load_api.py` asserts and exits non-zero — it is a test, not a benchmark.
  It found both API regressions above, neither of which is visible in a single
  request. It also caught one of its own: a threshold that was really measuring
  the harness's synchronized burst rather than the endpoint, which is why
  `/healthz` is asserted serially and everything else on p50/p99.
- **Profile before optimizing anything, and record the negative results too.**
  The measured numbers above say the deterministic middle of the pipeline is
  milliseconds, parallel rendering buys 1.21x, and the filter chain is free. Each
  of those is an optimization someone will otherwise attempt. A job's time is in
  stage 2 and stage 5's encoder; nothing else is worth a diff.
- Prefer deterministic Python over another model call. Stages 1, 4, 5 must stay
  model-free.

---

## Naming

`qatf` (قطف) = to pick, to harvest. Chosen after screening; Maqta, Lamha, Wamda,
Nukhba and Zubda were all eliminated on collisions (`zubda.ai` is a live
Arabic-English AI meeting-notes product).

Two items outstanding before commercial use: **SAIP trademark search** (a "Qatf
Agricultural Company" exists — different class, but check), and **the Qatif
question** — القطيف is a Saudi governorate one vowel away in Latin transliteration.
Not resolved. Do not print anything until it is.
