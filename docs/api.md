# HTTP API

```bash
uvicorn qatf.api:app --reload      # or: qatf-serve, or: python -m qatf.api
```

| Surface | Path |
| --- | --- |
| Swagger UI | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |
| Schema | `http://localhost:8000/openapi.json` |

`/docs` is the live contract and carries the same warnings as this page. This
file exists for reading offline and for the parts a schema cannot express.

The pipeline takes minutes, so **nothing is synchronous**. Every start returns
`202` and a job id; the client polls `GET /jobs/{id}`.

---

## Endpoints

| Method | Path | `operationId` | |
| --- | --- | --- | --- |
| `GET` | `/healthz` | `health` | readiness, provider roster, transcription device |
| `POST` | `/jobs` | `createJob` | start from a server-side path |
| `POST` | `/jobs/upload` | `createJobFromUpload` | start from a multipart upload |
| `GET` | `/jobs` | `listJobs` | list, optional `?state=` |
| `GET` | `/jobs/{id}` | `getJob` | **poll this** |
| `DELETE` | `/jobs/{id}` | `deleteJob` | refuses while running |
| `POST` | `/jobs/{id}/cancel` | `cancelJob` | cooperative |
| `GET` | `/jobs/{id}/transcript` | `getTranscript` | words, as they will be captioned |
| `PUT` | `/jobs/{id}/transcript` | `correctTranscript` | fix misheard words |
| `GET` | `/jobs/{id}/plan` | `getPlan` | |
| `PUT` | `/jobs/{id}/plan` | `replacePlan` | the hand-edit round trip |
| `POST` | `/jobs/{id}/render` | `renderPlan` | replaces previous outputs |
| `GET` | `/jobs/{id}/clips` | `listClips` | |
| `GET` | `/jobs/{id}/clips/{name}` | `downloadClip` | |

Operation ids are hand-named so a generated client gets `client.createJob(...)`
rather than `client.create_from_path_jobs_post(...)`. The smoke suite asserts
they stay that way.

---

## Job lifecycle

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> extracting: 1 · demux
    extracting --> transcribing: 2 · whisper
    transcribing --> selecting: 3 · llm
    selecting --> planned: 4 · snap
    planned --> rendering: 5 · ffmpeg
    rendering --> done
    planned --> planned: PUT /plan
    done --> planned: PUT /transcript
    done --> rendering: POST /render
    queued --> cancelled
    rendering --> cancelled
    transcribing --> failed
    selecting --> failed
```

`queued`, `extracting`, `transcribing`, `selecting` and `rendering` are the
**running** states. A job in one of them cannot be deleted, re-planned or
re-rendered — cancel it first.

Set `auto_render: false` to stop at `planned`.

---

## Start a job

```bash
curl -X POST localhost:8000/jobs \
  -H 'content-type: application/json' \
  -d '{
        "path": "talks/keynote.mov",
        "clips": 8,
        "min_len": 28,
        "max_len": 52,
        "language": "ar",
        "denoise": true,
        "font": "Traditional Arabic",
        "auto_render": false
      }'
```

```json
{ "id": "a1b2c3d4e5f6", "state": "queued", "message": "waiting for a worker", ... }
```

`path` resolves under `QATF_MEDIA_ROOT`. That is a **security boundary**: without
it a body naming `../../etc/passwd` would have the server transcribe any file the
process can read. Absolute paths are allowed but must still land inside the root;
anything escaping is `403`.

### From an upload

`options` is a JSON **string** form field, because a multipart body cannot carry
a nested JSON object.

```bash
curl -X POST localhost:8000/jobs/upload \
  -F 'file=@keynote.mov' \
  -F 'options={"clips": 8, "language": "ar", "denoise": true, "max_len": 52}'
```

Streamed to disk in 1 MB chunks and checked against `QATF_MAX_UPLOAD_MB` as it
arrives, so an oversized upload is refused mid-stream rather than after the whole
file has landed. A failed upload takes its job record with it.

---

## Poll

```bash
curl -s localhost:8000/jobs/a1b2c3d4e5f6 | jq '{state, message, device, word_count}'
```

```json
{
  "state": "rendering",
  "message": "[5/5] rendered 3/8: 03-php-lsh-ayshh.mp4",
  "device": "cuda",
  "word_count": 2841
}
```

**Poll `GET /jobs/{id}`, not `GET /jobs`.** The single-job endpoint is flat cost;
the list endpoint is linear in the number of jobs and dominated by a `stat()` per
rendered clip, so polling it turns into thousands of syscalls a second once a
server has accumulated jobs. Numbers in
[quality.md](quality.md#the-api-layer).

Three fields are worth reading on the way past:

- **`device`** — what stage 2 *actually* used. Under `device: auto` this can be
  `cpu` on a GPU host, and a `large-v3` CPU run takes a very long time.
- **`transcript_cached`** — whether stage 2 ran at all.
- **`outputs`** — grows during `rendering` rather than appearing all at once, so
  progress is visible clip by clip.

---

## Correcting misheard words

`GET /jobs/{id}/transcript` returns the transcript **as it will be captioned** —
fixups and any existing corrections already applied. What you read is what gets
burned in.

```bash
curl -s localhost:8000/jobs/$ID/transcript > t.json
```

```json
{ "text": "هو",  "start": 204.11, "end": 204.29 },
{ "text": "من",  "start": 204.29, "end": 204.58 },   ← should be مين
{ "text": "قال", "start": 204.58, "end": 204.91 }
```

The `start` is how you find it: 204.29 s is 3:24, so scrub there, hear what was
actually said, then fix the text and send the whole list back.

```bash
jq '{words: .words}' t.json | \
  curl -X PUT localhost:8000/jobs/$ID/transcript \
       -H 'content-type: application/json' -d @-

curl -X POST localhost:8000/jobs/$ID/render
```

> **Only `text` may differ.** The word count and every `start`/`end` must match
> exactly; anything else is a `422`. That refusal is the core invariant enforced
> at the boundary — a correction can change what a caption reads and can never
> move a cut.
>
> It is also what makes this cheap: the cut points are *provably* identical, so
> only stage 5 runs again. No model call, no re-transcription.

To split one word into two, put both in that word's `text`. There is no honest
timing to give a word Whisper never heard.

### Why not just use `fixups`?

`fixups` is a global find-and-replace, keyed by **value**. It fixes the
systematic errors — a term the decoder always mishears the same way — and it is
the right tool for those, because it generalises across the whole file and across
future videos.

It cannot fix a word that is wrong *here* and correct everywhere else. A rule
`من = مين` would rewrite every `من` in the file, and `من` is one of the most
common words in Arabic. Corrections are keyed by **position**, so they touch that
one word.

Use both. Fixups run first, so a correction wins on the word it names.

### Corrections are an overlay

Stored at `<work>/word-edits.json`, beside the transcript cache and never inside
it, for two reasons:

- re-transcribing must not silently discard them
- the cache has to keep saying what Whisper *actually* produced, or the
  measurements in [quality.md](quality.md) stop meaning anything

Each correction records the text it replaced. If the transcript later moves
underneath it — a re-transcribe at a different `whisper` size, `denoise` toggled
— the correction goes **stale**: skipped, not applied, and counted in
`edits_stale`. An index alone would have landed silently on an unrelated word.

`PUT` is a wholesale replace, so re-submitting the untouched transcript clears
every correction.

A job in `done` drops back to `planned` when you correct it, because its rendered
clips now caption text nobody will see again.

### What it does not fix

Correcting text does not move a caption in time, and does not change which
passages the model picked — selection already ran. If a misheard word made the
model skip a good moment, fix the word and edit the plan directly.

---

## The hand-edit round trip

This is the part worth understanding, because it is what makes iteration cheap:
everything from `planned` onward costs **no model call and no re-transcription**.

```mermaid
sequenceDiagram
    autonumber
    participant C as client
    participant A as qatf
    C->>A: POST /jobs {auto_render: false}
    A-->>C: 202 {id}
    Note over A: stages 1-4
    C->>A: GET /jobs/{id}
    A-->>C: state: planned
    C->>A: GET /jobs/{id}/plan
    A-->>C: 8 clips
    Note over C: edit boundaries and titles
    C->>A: PUT /jobs/{id}/plan {clips, snap: true}
    A-->>C: re-snapped clips
    C->>A: POST /jobs/{id}/render
    A-->>C: 202
    C->>A: GET /jobs/{id}/clips
```

```bash
curl -s localhost:8000/jobs/$ID/plan > plan.json
$EDITOR plan.json

curl -X PUT localhost:8000/jobs/$ID/plan \
  -H 'content-type: application/json' \
  -d "$(jq '{clips: ., snap: true}' plan.json)"

curl -X POST localhost:8000/jobs/$ID/render
```

> **Leave `snap` on.** Your edited boundaries get moved back onto real Whisper
> word times. A hand-typed `"start": 20.0` is a semantic guess exactly like the
> model's, and skipping the snap is how clips end up opening mid-syllable. The
> boundaries you get back will differ slightly from the ones you sent — that is
> the feature working.

`PUT /plan` replaces the plan wholesale. There is no partial update: send the
clips you want, in the order you want them numbered.

`POST /render` **deletes the previous clips first.** Download anything you want
to keep before re-rendering. Passing an `options` body replaces the job's whole
options object — send the full set, not a patch — and only the stage-5 fields
take effect, since the transcript and plan already exist.

---

## `JobOptions`

Every field has a working default, so `{"path": "talk.mov"}` is a complete
request. Same names and defaults as the CLI flags.

| Field | Default | Range / values |
| --- | --- | --- |
| `clips` | `5` | 1–50 |
| `min_len` | `30` | 1–600 s |
| `max_len` | `75` | 1–600 s · **use 52 for Shorts** |
| `reframe` | `crop` | `crop` · `blur` |
| `codec` | `h265` | `h264` · `h265` — h265 is ~3x slower to encode |
| `preset` | `medium` | `veryslow` … `ultrafast`; the render-time lever |
| `resolution` | `1080p` | `source` · `1080p` · `1440p` · `4k` · `WxH` |
| `ten_bit` | `false` | needs a 10-bit source |
| `crf` | `20` | 0–51, lower is better |
| `whisper` | `large-v3` | any faster-whisper size |
| `device` | `auto` | `auto` · `cuda` · `cpu` |
| `language` | `null` | e.g. `ar`; omit to autodetect |
| `denoise` | `false` | |
| `fixups` | `null` | `{"بايسون": "بايثون"}` — text only, never timestamps |
| `hotwords` | `null` | ≤4000 chars; whole-file bias |
| `initial_prompt` | `null` | ≤4000 chars; **first ~30 s only** |
| `font` | `Arial` | must be installed **on the server** |
| `captions` | `true` | |
| `per_line` | `4` | 1–8 words |
| `auto_render` | `true` | `false` stops at `planned` |

`min_len > max_len` is rejected at the boundary, as is an unparseable
`resolution` — better than failing three minutes into a job when the worker
finally reaches stage 5.

Two fields behave differently from what their names suggest:

- **`hotwords` applies to the whole file; `initial_prompt` seeds only the first
  ~30 seconds** and then decays. Prefer `hotwords`. Both are part of the
  transcript cache key.
- **`font` is resolved on the rendering host**, which under the API is the
  server, not the caller's machine. libass falls back silently, so a missing
  Arabic face ships as tofu rather than an error.

---

## Errors

Domain failures carry their own status code and always come back as
`{"detail": "..."}`. One exception handler maps the whole `QatfError` hierarchy —
routers never hand-map a domain failure, which is how HTTP concerns stay out of
the pipeline.

| Status | Means |
| --- | --- |
| `403` | the path escaped `QATF_MEDIA_ROOT` — a security refusal, not a typo check |
| `404` | no such job, no plan yet, no such clip |
| `409` | the job is running, or you asked for something that needs a transcript first |
| `413` | upload over `QATF_MAX_UPLOAD_MB`, or the transcript exceeds the model's context |
| `415` | unsupported video extension |
| `422` | validation failed, no speech was found, or the vocabulary seed is too long |
| `502` | stage 3 returned something that was not the requested JSON, or refused |
| `503` | ffmpeg is missing, or the stage-3 provider has no credential |

A `500` means a bug: every deliberate failure is a `QatfError` subclass, so
anything else escaping the pipeline was not thought through.

Each status is declared per-route in the OpenAPI schema and typed as
`ErrorResponse`, so generated clients get a real error type rather than an
untyped body. Where several distinct failures share a code — `PUT /plan` 409s for
both "job is running" and "no transcript yet" — the descriptions are joined
rather than one silently winning.

---

## `/healthz`

**Call this before submitting an hour of audio.** It never 503s; read `status`
and the individual flags.

```json
{
  "status": "degraded",
  "version": "0.4.0",
  "model": "anthropic/claude-opus-5",
  "ffmpeg": true,
  "media_root": "/srv/media",
  "max_workers": 1,
  "llm_provider": "openrouter",
  "llm_ready": false,
  "llm_error": "provider 'openrouter' needs an API key — set OPENROUTER_API_KEY",
  "cuda_devices": 1,
  "transcribe_device": "cuda",
  "providers": [ ... ]
}
```

`degraded` is not fatal — the server still accepts jobs — but a job that reaches
stage 3 without a credential fails *after* transcription has already run, which
is minutes wasted.

Cheap to poll: the ffmpeg probe is primed at startup and served from cache, so the
handler costs ~1 ms. It used to spawn a process per request and measured p99 over
a second under load — see
[quality.md](quality.md#healthz-forked-a-process-per-request).

- **`cuda_devices`** is what **CTranslate2** can actually target, not what
  `nvidia-smi` reports. A card the installed CTranslate2 cannot use (compute
  capability, driver mismatch) is not a usable device, and only the engine knows.
- **`transcribe_device`** is what stage 2 will pick under `device: auto`.
- **`providers`** carries the whole stage-3 roster with each entry's
  structured-output tier, so a client can offer a provider picker without
  hardcoding one.

---

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `QATF_DATA_DIR` | `qatf-data` | job directories |
| `QATF_MEDIA_ROOT` | `.` | the `POST /jobs` sandbox |
| `QATF_WORKERS` | `1` | concurrent jobs |
| `QATF_MAX_UPLOAD_MB` | `2048` | |
| `QATF_HOST` / `QATF_PORT` | `127.0.0.1` / `8000` | |
| `QATF_LLM_PROVIDER` | `anthropic` | see [providers.md](providers.md) |
| `QATF_LLM_MODEL` | preset default | `QATF_MODEL` is the pre-0.5 name, still honoured |
| `QATF_LLM_BASE_URL` | preset default | |

`Settings` is a plain frozen dataclass, not pydantic-settings — pydantic is an
API-only dependency and the CLI must not need it.

`create_app(settings=...)` takes an explicit object, which is how the tests point
at a scratch directory without touching `os.environ`. `JobStore` carries that
same object so workers read the settings the app was built with; reaching for the
process-wide `get_settings()` inside a worker would make `create_app(settings=…)`
a half-truth.

---

## On-disk layout

```text
$QATF_DATA_DIR/
  <job-id>/
    job.json                       the record — reloaded at startup
    source/                        uploads only
    .work/
      audio.wav                    or audio-denoised.wav
      words-<model>-<lang>.json    the transcript cache — what Whisper produced
      word-edits.json              per-word corrections, applied on read
      *.ass                        generated subtitles
    clips/
      01-slug.mp4
```

Deleting a job removes the whole directory, cached transcript included.

---

## Limits to know before deploying

1. **Jobs do not survive a restart.** State is a JSON file per job, but the
   worker is an in-process thread pool. On startup anything left running is
   marked `failed: interrupted by a server restart`.
2. **Cancellation is cooperative.** The flag is checked between stages and
   between clips. A cancel during stage 2 on a long file lands whenever
   transcription finishes — not straight away. Poll for `state: cancelled` to
   know it actually stopped.
3. **`QATF_WORKERS` defaults to 1.** Two concurrent `large-v3` loads fight over
   the same GPU. Raise it only for render-only work.

**The API has never run against a real video, a real GPU or a real API key.**
Every stage boundary is exercised by `tests/smoke_api.py` (91 checks), which
fakes `pipeline.audio.run`, `pipeline.encode.run`, `pipeline.asr.transcribe` and
`pipeline.select.pick_clips` — so it proves nothing about those four. The CLI has
run the whole thing end to end; the server has not.
