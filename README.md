# qatf (قطف)

[![CI](https://github.com/Abdel-RahmanSaied/Qatf/actions/workflows/ci.yml/badge.svg)](https://github.com/Abdel-RahmanSaied/Qatf/actions/workflows/ci.yml)
[![Licence: Apache-2.0](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Contributions welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Turn a long video into vertical short-form clips.** A model picks the moments;
everything about *where* the cuts land is deterministic.

قطف — *to pick, to harvest*.

![The qatf jobs dashboard](docs/images/ui-dashboard.png)

```bash
docker compose up      # web UI on :3000, API on :8000
```

---

## Contents

- [What it does](#what-it-does)
- [What makes it different](#what-makes-it-different)
- [Quickstart](#quickstart)
- [The web UI](#the-web-ui)
- [The CLI](#the-cli)
- [The HTTP API](#the-http-api)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Testing](#testing)
- [Status](#status-working-prototype)
- [Contributing](#contributing)

---

## What it does

Five stages. Only two involve a model.

```text
0. fetch       a URL → a local file            yt-dlp, on an exact-host allowlist
1. audio       demux (optionally denoise)      ffmpeg
2. transcribe  words with start/end times      faster-whisper — or a caption track
3. select      which passages are worth it     the configured LLM        ← model
4. snap        cuts onto real word boundaries  deterministic
5. render      reframe 9:16, burn captions     ffmpeg
```

```text
[1/5] extracting audio (denoising)
[2/5] transcribing with whisper large-v3 on cuda (auto-selected)
      biasing vocabulary (30 terms, whole file)
      detected language: ar (p=1.00)
[3/5] asking anthropic/claude-opus-5 for 8 clips
[4/5] snapped cuts to word boundaries
      03:04-03:53 (49.5s, 0.85)  PHP ماتت؟ الكلام ده بقاله سنين
      08:24-09:14 (50.0s, 0.84)  ما تسلّمش دماغك لأي حد: دوّر بنفسك
      ...
[5/5] rendering 8 clips at 1080x1920 h265 -preset medium
done. 8 clips in reels/
```

---

## What makes it different

**Arabic is a first-class path, not an afterthought.** Every comparable tool
(Opus Clip, Klap, Vizard, Submagic, Choppity, quso, 2short) is English-first.
RTL caption rendering here is *measured*, not assumed — including a libass bidi
bug that scrambles Arabic word order and which every obvious test reports as
passing. See [the RTL caption bug](docs/troubleshooting.md#the-rtl-caption-bug).

**The model never emits timing.** It reads the transcript at ~12-second
resolution and returns `MM:SS`. Stage 4 then snaps those onto real word
boundaries from Whisper. Semantic boundaries come from the model, acoustic
boundaries come from the audio, and they are never mixed. This is the core
invariant — see [architecture.md](docs/architecture.md#the-core-invariant).

**Provider-agnostic clip selection.** Anthropic, OpenAI, Kimi, GLM, OpenRouter,
or a local model via Ollama/vLLM — one config change. Because stages 1, 4 and 5
are model-free, *swapping providers cannot affect cut accuracy or rendering*.
See [providers.md](docs/providers.md).

**Every default has a measurement behind it.** `--preset medium` over `veryfast`,
`crop` over `blur`, h265 over h264 — each is a number somebody recorded,
including the negative results.
[quality.md](docs/quality.md) is the whole playbook, and it lists the things that
were tried and *rejected* so nobody repeats them.

---

## Quickstart

```bash
docker compose up
```

- Web UI — <http://localhost:3000>
- API and Swagger UI — <http://localhost:8000/docs>

Add a provider key first, or stage 3 fails after transcription has already run:

```bash
cp .env.example .env      # from the repo root, then add one key
```

`GET /healthz` tells you whether ffmpeg is reachable, which provider is active,
whether its credential is present, and whether transcription will get a GPU —
all before you submit an hour of audio.

Developing? The live-reload overlay bind-mounts your source into both
containers, so an edit lands without an image rebuild:

```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up
```

Frontend changes hot-reload through Vite; backend changes restart uvicorn. You
only rebuild when a dependency changes. Details in
[operations.md](docs/operations.md#live-reload-while-developing).

---

## The web UI

Four screens over the same HTTP API — jobs, new job, job detail, transcript
editor. The UI is purely additive: no feature in it required a backend change.

| | |
| --- | --- |
| ![Job page with clips and the plan editor](docs/images/ui-job.png) | ![Transcript editor](docs/images/ui-transcript.png) |
| **Job page.** Rendered clips first, then where the picks came from, then the plan you can edit. | **Transcript editor.** Click a word to correct it. There is no timing input, by construction. |

Two things it refuses to let you do, on purpose:

- **Correct a word's timing.** The transcript editor edits text only. A spelling
  fix changes what a caption reads and can never move a cut, which is also what
  makes it safe to do *after* rendering — only stage 5 has to run again.
- **Skip the re-snap.** Hand-edited plan boundaries are always re-snapped onto
  Whisper word times. A second you typed is a semantic guess, exactly like the
  model's, and skipping the snap is how clips open mid-syllable.

The dashboard's **harvest strip** shows where each job's clips were taken from,
so you can see at a glance whether the model clustered its picks or spread them.
It scales to the picked range and says so — the API exposes no source duration,
and the strip does not invent one.

---

## The CLI

```bash
cd qatf-backend
pip install -e ".[all]"            # or ".[api,anthropic]" for just one provider
```

ffmpeg must be on `PATH`, or point `QATF_FFMPEG` at it. A GPU is optional —
`--device auto` uses CUDA when CTranslate2 can actually reach it, and falls back
to CPU otherwise. Naming a device explicitly is honoured and will **not** fall
back, because asking for `cuda` and silently getting `cpu` turns a benchmark
into a lie.

```bash
# see what it would cut, without spending render time
qatf talk.mov -o out/ --plan-only

# edit out/plan.json by hand, then render exactly that
qatf talk.mov -o out/ --plan out/plan.json

# the full real-world invocation
qatf talk.mov -o reels/ --language ar --clips 8 --min-len 28 --max-len 52 \
  --denoise --vocab-file prompts/ar-tech.txt --fixups prompts/ar-fixups.txt \
  --font "Traditional Arabic"

# let the crop follow the subject instead of holding a static centre slice
qatf talk.mov -o reels/ --reframe track
```

Three things worth knowing before your first run:

**Use `--max-len 52`, not 60.** Stage 4 moves cut points onto word boundaries
*after* the model picks them, which routinely adds a few seconds. Ask for 60 and
some clips land at 63 — which YouTube Shorts rejects. Reels and TikTok allow
longer.

**`--reframe track` follows the largest face, not the active speaker.** There is
no active-speaker model yet, so in a two-shot it can frame the listener. The
tracked path is rendered and measured by `verify_render.py`, but every `TRACK_*`
tuning constant is an unmeasured starting value.

**Transcription is cached, so iterating on selection is free.** The cache lives
in a `qatf.db` (SQLite, WAL) in the run's work directory, alongside per-word
corrections and the face-detection cache; under the API, job records sit in a
separate database at the data root. It is keyed on the Whisper
size, the forced language and the vocabulary — passing `--language ar` after an
English run re-transcribes rather than silently reusing the wrong transcript.

Every flag, with the measurement behind its default: [cli.md](docs/cli.md).

---

## The HTTP API

The pipeline takes minutes, so nothing is synchronous: every start returns `202`
and a job id, and the client polls.

```bash
cd qatf-backend && uvicorn qatf.api:app --reload

curl -X POST localhost:8000/jobs -H 'content-type: application/json' \
  -d '{"path": "talk.mov", "clips": 8, "language": "ar", "denoise": true}'
```

```text
POST   /jobs                  start from a server-side path (sandboxed to a media root)
POST   /jobs/upload           multipart
POST   /jobs/url              fetch a YouTube URL. 403 off the allowlist
GET    /jobs, /jobs/{id}      list and poll
GET    /jobs/{id}/transcript  words, as they will be captioned
PUT    /jobs/{id}/transcript  correct misheard words — text only, never timings
GET/PUT /jobs/{id}/plan       the hand-edit round trip; re-snaps unless you opt out
POST   /jobs/{id}/render      encode the current plan
POST   /jobs/{id}/cancel      cooperative — checked between stages and between clips
DELETE /jobs/{id}             refuses while running
GET    /jobs/{id}/clips       + /{name} to download
GET    /healthz               ffmpeg, provider, credential, transcription device
```

States: `queued → fetching → extracting → transcribing → selecting → planned →
rendering → done`, plus `failed` and `cancelled`. Set `auto_render: false` to
stop at `planned` for review.

Full reference: [api.md](docs/api.md), or `/docs` on a running server.

---

## Configuration

```bash
QATF_LLM_PROVIDER=anthropic     # openai | openai-legacy | kimi | glm
                                # ollama | vllm | openrouter
QATF_LLM_MODEL=...              # override the preset's default model
QATF_LLM_BASE_URL=...           # point a preset at another host

QATF_DATA_DIR=/data             # job records, sources, rendered clips
QATF_MEDIA_ROOT=/media          # the sandbox POST /jobs paths must resolve inside
QATF_WORKERS=1                  # concurrent jobs — two large-v3 loads fight over one GPU
QATF_MAX_UPLOAD_MB=...
QATF_FFMPEG=... QATF_FFPROBE=...
```

`media_root` is a security boundary, not a convenience: without it a `POST /jobs`
body naming `../../etc/passwd` would transcribe any file the process can read.

---

## Repository layout

```text
qatf-backend/        the pipeline, CLI and API — Python, no frontend knowledge
  qatf/core/         config, SQLite, types, errors. imports nothing of ours
  qatf/pipeline/     the five stages, one module each
  qatf/llm/          stage 3 providers — the only swappable part
  qatf/jobs/         job records and the worker thread pool
  qatf/api/          FastAPI routers, schemas, OpenAPI
  qatf/cli/          argument surface and the run flow
  tests/             smoke suites, load test, render verification
qatf-frontend/       the web UI — React + Vite + TypeScript, served by nginx
docs/                the human-facing reference
docker-compose.yaml  backend + frontend, one command
CLAUDE.md            the working agreement for agents editing this repo
```

Dependencies run one way only, and an import pointing the other way means
something is defined in the wrong package:

```text
api → jobs → pipeline → llm → core
cli ───────→ pipeline → llm → core
```

`smoke_pipeline.py` enforces this: it reads every module in `core/` as source
text and fails on any import of a sibling package. Source text rather than
`sys.modules`, because the violation it was written for was a *lazy* import
inside a property body — deferred to call time, invisible in the import block,
and absent from `sys.modules` until something happened to call it.

---

## Documentation

| Page | Covers |
| --- | --- |
| [architecture.md](docs/architecture.md) | The five stages, the layering rules, and the invariant everything protects |
| [cli.md](docs/cli.md) | Every flag, with the measurement behind each default |
| [api.md](docs/api.md) | HTTP reference, job lifecycle, the hand-edit round trip |
| [providers.md](docs/providers.md) | Provider matrix, the three structured-output tiers, self-hosting |
| [quality.md](docs/quality.md) | The tuning playbook — what actually moved the numbers, including what didn't |
| [operations.md](docs/operations.md) | GPU, Docker, live reload, caching, deployment limits |
| [security.md](docs/security.md) | Trust model, where each boundary is enforced, known gaps |
| [troubleshooting.md](docs/troubleshooting.md) | The traps, each with the symptom that identifies it |

---

## Testing

All of it runs on [CI](.github/workflows/ci.yml) for every push and pull
request. **626 of the checks need no ffmpeg, GPU, API key or network**, so you
can run them locally in seconds:

```bash
cd qatf-backend
python tests/smoke_db.py          #  23   the SQLite layer, in isolation
python tests/smoke_pipeline.py    # 359   stages 1-5, escaping, caches, trust boundaries
python tests/smoke_llm.py         #  38   provider request shapes, with the SDK faked
python tests/smoke_api.py         # 155   state machine, round trips, the OpenAPI document
python tests/load_api.py          #  23   every endpoint under 24 threads, ~20s
ruff check .

cd ../qatf-frontend
npm test                          #  28   the API client and the server-rule mirrors
npm run build                     #        tsc --noEmit is the type gate
```

One suite needs ffmpeg, because the only honest way to verify a filtergraph is to
render through it and measure the result:

```bash
cd qatf-backend
python tests/verify_render.py     #  19 with OpenCV, 11 without
```

The count depends on your install: the `track` extra (included in `[all]`) pulls
in OpenCV, which unlocks a fixture that runs the real face detector. Without it
that fixture skips rather than fails.

Two of these are worth understanding before you trust any of them:

`load_api.py` **asserts** — it is a test, not a benchmark. It found `/healthz`
spawning an ffmpeg process per request, which no sequential test could see.

Every fixture in `verify_render.py` renders `crop` as a control and asserts the
control **fails**. A check that cannot fail measures nothing — twice a broken
harness reported the subject missing from both renders, which reads exactly like
a broken feature.

---

## Status: working prototype

Not production. Honest about what has and has not run:

**Verified end to end.** A 12-minute 4K ProRes Arabic video recorded in a moving
car — all five stages, real provider, 8 vertical clips with burned-in Arabic
captions. Through the **CLI on a real GPU**, and separately through the **HTTP
API, which ran on CPU** (`cuda_devices: 0` throughout — see below). Caption
rendering confirmed by extracting frames and looking at them, in English *and*
Arabic.

**Not verified.** Every provider except OpenRouter against its real endpoint —
`smoke_llm.py` pins what we *send*, but those endpoints have never replied.
Arabic *selection* quality on any non-Claude provider. Whisper's **word
timestamp accuracy** on Arabic: spelling quality is measured, but nobody has
checked whether the boundaries `snap` depends on land where words actually
start. The API on a GPU host.

**Known gaps.** No loudness normalisation, no silence trimming, no
scene-change detection. Jobs do not survive a restart. Cancellation is
cooperative — it cannot interrupt an ffmpeg or Whisper call already in flight.
Caption cues overlap by ~3 frames on continuous speech
([measured and unfixed](docs/troubleshooting.md#two-captions-are-on-screen-at-once)).

**The API has no authentication.** Anything you put in front of it is the only
gate. Read [security.md](docs/security.md) before exposing a port.

---

## Contributing

**Contributions are welcome — features and bug fixes both.** Start with
[CONTRIBUTING.md](CONTRIBUTING.md); it lists
[good first contributions](CONTRIBUTING.md#good-first-contributions) ranked by
value per effort, and loudness normalisation at the top is close to a one-line
filter add.

The short version: this codebase cares more about *why* a number is what it is
than about the number. So a change to a default needs a measurement, and a
change to a filtergraph or to caption generation needs a rendered frame somebody
actually looked at — ffprobe reporting correct dimensions is not sufficient, and
there is a shipped bug proving it.

Every push and pull request runs the full suite on
[CI](.github/workflows/ci.yml): both Python versions, lint, all five backend
suites, the frontend tests and type check, a real ffmpeg render verification,
and the Docker build. Sign your commits off with `git commit -s` — that is the
[DCO](https://developercertificate.org), certifying you wrote the code and can
contribute it under Apache-2.0.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
Security problems go to [SECURITY.md](SECURITY.md), not a public issue.

---

## Naming

Screened against Maqta, Lamha, Wamda, Nukhba and Zubda — all eliminated on
collisions (`zubda.ai` is a live Arabic-English product). Two items outstanding
before commercial use: a SAIP trademark search (a "Qatf Agricultural Company"
exists — different class, but check), and the Qatif question — القطيف is a Saudi
governorate one vowel away in Latin transliteration.

## Licence

**Apache-2.0** — see [LICENSE](LICENSE). Permissive: use it, modify it, ship it
commercially. It also carries an explicit patent grant from every contributor,
which MIT does not.

One bundled third party: the YuNet face-detection weights used by
`--reframe track` are MIT (Copyright 2020 Shiqi Yu), and `LICENSE.yunet` travels
beside them. [NOTICE](NOTICE) covers attribution;
[docs/licensing.md](docs/licensing.md) is the full dependency audit.

**ffmpeg is a system dependency, invoked as a subprocess and never linked**, so
its licence does not reach this source — but it *is* inside the Docker images
built here, and that build is GPL. If you redistribute those images, comply with
ffmpeg's terms for the build you shipped.
