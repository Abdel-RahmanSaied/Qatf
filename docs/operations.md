# Operations

Install, GPU setup, Docker, caching and what to know before running this behind
anything real.

---

## Install

```bash
cd qatf-backend
pip install -e ".[all]"              # api + every provider SDK
pip install -e ".[api,anthropic]"    # or just the one you use
pip install -e ".[api,openai]"       # also drives kimi, glm, vllm, ollama, openrouter
pip install -e ".[dev]"              # httpx + ruff, for the smoke suites
```

The base install pulls only `faster-whisper`. **ffmpeg is a system dependency**
and must be on `PATH`, or named explicitly:

```bash
QATF_FFMPEG=C:\tools\ffmpeg\bin\ffmpeg.exe
QATF_FFPROBE=C:\tools\ffmpeg\bin\ffprobe.exe
```

`GET /healthz` reports whether ffmpeg is genuinely reachable. Check it before
concluding an install worked.

No provider SDK is a base dependency: stage 3's provider is chosen at runtime, so
its client library is an extra. Installing every SDK to use one of them is waste,
and it would break the rule that the CLI works without any of them.

### Configuration

```bash
cp ../.env.example ../.env   # from qatf-backend/ — both files belong at the repo root
```

Keep `.env` at the repo root. `docker-compose.yaml` interpolates the provider keys
from there, and because `.env` is read from the working directory **or any parent**,
a root `.env` also serves the CLI and a server started from `qatf-backend/`. **A real environment
variable always wins** over a `.env` entry — see
[`core/dotenv.py`](../qatf-backend/qatf/core/dotenv.py). `.env` is gitignored; `.env.example`
is not.

---

## GPU

```bash
cd qatf-backend
qatf talk.mov -o out/                  # --device auto is the default
curl -s localhost:8000/healthz | jq '{cuda_devices, transcribe_device}'
```

`auto` asks **CTranslate2**, not `nvidia-smi`, whether it can target a CUDA
device. A card the installed CTranslate2 cannot use — compute capability, driver
mismatch — is not a usable device, and only the engine knows that.

Two layers, because availability and usability are different questions:

| Layer | Answers | On failure under `auto` | Under explicit `cuda` |
| --- | --- | --- | --- |
| `resolve_device()` | can CTranslate2 see a device? | pick `cpu` | raise |
| `transcribe()`'s try/except around `_transcribe_on` | can it actually load AND decode? (OOM, missing kernels, or a lazy CUDA failure on first encode) | fall back to `cpu`, reason logged | raise |

**An explicit `--device cuda` never falls back.** Silently getting CPU turns a
benchmark into a lie, so it raises instead. Use the explicit form whenever you
need certainty.

The device actually used is recorded on the `Transcript`, echoed in the job
record and `GET /jobs/{id}` as `device`, and reported up front by `/healthz`.

It is deliberately **not** in the transcript cache key: a CPU and a GPU
transcript of the same audio are interchangeable enough that re-transcribing
after a fallback would be waste.

### If CUDA is present but unused

See [troubleshooting.md](troubleshooting.md#cublas64_12dll-is-not-found--or-the-gpu-is-never-used).
The short version: the failure is **lazy** — it fires during generator
consumption, not at model construction — and on Windows both
`os.add_dll_directory` *and* a `PATH` prepend are required, because ctranslate2
resolves cuBLAS from C++ via a plain `LoadLibrary`.

### If there is no GPU

CPU with `large-v3` is slow enough that stage 2 logs a warning. `--whisper small`
is far quicker if the quality bar allows. `--denoise` also helps: it is ~20%
*faster*, because a cleaner signal makes the decoder fall back to higher
temperatures less often.

---

## Caching

```text
<work>/qatf.db
```

Under the CLI, `<work>` is `<out>/.work/`. Under the API it is
`$QATF_DATA_DIR/<job-id>/.work/`. The transcript is a row in the `transcripts`
table, keyed the same way the old `words-<model>-<lang>.json` filename was; the
per-word correction overlay (`word_edits`) and the face-detection cache
(`detections`) live in the same file. Job records and cancel flags are a
*separate* `qatf.db`, one per store root — `$QATF_DATA_DIR/qatf.db` under the
API — not per job, since the job list itself is what `GET /jobs` queries.

Delete the row (or the whole `qatf.db`) to re-transcribe; otherwise iterating on
clip selection is free.

**In the key:** Whisper size, forced language, hotword vocabulary, initial
prompt.

**Not in the key:** the device, and both text-correction layers — `--fixups` and
the per-word overlay. Those are applied on read, so editing either never orphans
the cache and never changes a timestamp. It also keeps the cache saying what
Whisper *actually* produced, which is what makes the numbers in
[quality.md](quality.md) reproducible — the storage medium changed, that
guarantee did not.

**Upgrading from a pre-SQLite work directory needs nothing.** A `words-*.json`,
`word-edits.json` or `faces-*.json` file left over from an older install is
imported into `qatf.db` the first time it is read and then left on disk,
untouched — nothing in the upgrade path deletes a file. That is what makes a bad
upgrade reversible: checking out the previous commit finds the same files it
left behind and reads them exactly as before, because they were never rewritten
or removed.

> Drop a transcription flag on a re-run and you get a **cache miss**, not the old
> transcript. Keep `--language`, `--whisper`, `--vocab-file` and `--denoise` the
> same across runs you want to reuse. (`--denoise` selects a different wav, so it
> matters too.)

The cheapest iteration loop, in order of cost:

| Change | Re-runs |
| --- | --- |
| `--crf`, `--font`, `--reframe`, `--track-tier`, `--codec`, `--resolution` | stage 5 only (`--reframe track` also re-runs stage 4b/4c, itself cached per video) |
| plan edits (`--plan`, `PUT /plan`) | stages 4–5 |
| `--clips`, `--min-len`, `--max-len`, provider | stages 3–5 |
| `--language`, `--whisper`, `--vocab`, `--denoise` | everything |

Stage 2 dominates a cold run by an order of magnitude, which is what makes the
cache worth its complexity. Everything deterministic between stages 3 and 5 —
snapping, caption generation, plan serialisation — is milliseconds and not worth
tuning. See [quality.md](quality.md#render-performance).

---

## Docker

`docker-compose.yaml` sits at the repo root, alongside `qatf-backend/` and
`qatf-frontend/` — run these from the root, not from either package directory.

```bash
docker compose up qatf                    # hosted provider
docker compose --profile ollama up        # + local GLM-4-9B via Ollama
docker compose --profile vllm up          # + local GLM-4-9B via vLLM (needs a GPU)
docker compose up                         # backend + web UI together
```

The `qatf` service builds from `./qatf-backend` and maps `./qatf-data → /data`
and `./media → /media:ro`, and sets `QATF_MEDIA_ROOT=/media` — so the media root
is both the sandbox and a read-only mount. Provider keys pass through from your
shell or `.env`.

The GPU reservation block ships **commented out**, so the stack starts on any
host. Uncomment it to give the container a GPU — and only then, since Compose
refuses to start the service when the reservation cannot be satisfied.

### The frontend service

`frontend` builds from `./qatf-frontend` and publishes the web UI on `:3000`; it
`depends_on: [qatf]` but does not gate on the backend being healthy, only
started. nginx serves the static build and reverse-proxies `/api/*` to the
`qatf` service on the compose network, so the browser only ever talks to one
origin. **The UI adds no auth of its own** — it is the same unauthenticated
trust model as the API itself (see [security.md](security.md)); whatever you put
in front of `:3000` (or `:8000`, if exposed directly) is the only gate either
way.

### Pointing at a local model

```bash
QATF_LLM_PROVIDER=ollama QATF_LLM_BASE_URL=http://ollama:11434/v1
QATF_LLM_PROVIDER=vllm   QATF_LLM_BASE_URL=http://vllm:8000/v1
```

The `ollama-pull` one-shot service pulls `glm4:9b` up front so the first clip
request is not a cold download.

vLLM supports **guided decoding**, so `json_schema` is real there rather than
best-effort — which is why the `vllm` preset declares it and the `ollama` preset
does not. See [providers.md](providers.md#structured-output-is-three-tiers-not-a-boolean).

**Kimi K2 is ~1T parameters and does not self-host on a consumer GPU.** GLM-4-9B
does fit one 16–24 GB GPU quantised, which is why both local profiles pull it.

---

## Running the server

```bash
cd qatf-backend
uvicorn qatf.api:app --reload      # development
qatf-serve                         # reads QATF_HOST / QATF_PORT
python -m qatf.api                 # same
```

| Variable | Default | |
| --- | --- | --- |
| `QATF_DATA_DIR` | `qatf-data` | job directories |
| `QATF_MEDIA_ROOT` | `.` | the `POST /jobs` sandbox — a **security boundary** |
| `QATF_WORKERS` | `1` | concurrent jobs |
| `QATF_MAX_UPLOAD_MB` | `2048` | |
| `QATF_HOST` / `QATF_PORT` | `127.0.0.1` / `8000` | |

### Set `QATF_MEDIA_ROOT` deliberately

It defaults to `.`, which is fine for development and wrong for anything
exposed. Without a tight root, a `POST /jobs` body naming `../../etc/passwd`
would have the server transcribe any file the process can read. Absolute paths
are allowed but must still resolve inside it.

### Leave `QATF_WORKERS` at 1 on a single GPU

Two concurrent `large-v3` loads fight over the same device. Raise it only for
CPU-bound render-only work.

---

## Before this runs behind anything real

Three gaps, all deliberate given the dependency budget — a thread pool and job
records as SQLite rows, no broker and no cross-process job claiming:

1. **Jobs do not survive a restart.** State persists, the worker does not. On
   startup anything left running is marked `failed: interrupted by a server
   restart`. That is honest, not a bug, but it is **the first thing to fix** for a
   real deployment. The cached transcript survives, so a resubmitted job that died
   during rendering restarts cheaply.
2. **Cancellation is cooperative.** It lands between stages and between clips,
   never mid-ffmpeg.
3. **No auth, no rate limiting, no quota.** Put something in front of it.
4. **Do not run more than one server process against the same `QATF_DATA_DIR`.**
   SQLite's WAL mode makes concurrent *writes* to the job database safe; it does
   not make `QATF_WORKERS` safe *across processes*. Two processes would each
   pull queued jobs onto their own pool and run the same job twice — nothing
   claims a job atomically yet. See
   [api.md](api.md#limits-to-know-before-deploying).

Also unaddressed: nothing cleans up old job directories. A 4K source plus its
wav plus its clips is not small, and `DELETE /jobs/{id}` is the only reaper.

**Read [security.md](security.md) first.** It carries the trust model, where each
boundary is enforced, and the gaps that are your problem rather than the code's —
including that `docker compose up` publishes an unauthenticated API on every
interface.

---

## Tests

Seconds to run. No ffmpeg, GPU, API key or network needed.

```bash
cd qatf-backend
python tests/smoke_db.py          #  23 checks
python tests/smoke_pipeline.py    # 354 checks
python tests/smoke_llm.py         #  38 checks
python tests/smoke_api.py         # 154 checks
python tests/load_api.py          #  23 checks, ~20s
ruff check .
```

**Every suite must stay green, and new behaviour gets a check.** There is no CI,
so this is the only gate there is.

`load_api.py` is a load *test*, not a benchmark: it asserts and exits non-zero.
It found two things a sequential suite could not — `/healthz` spawning a process
per request, and the cost of stat()ing every clip on every list — and its
thresholds exist to stop both coming back. Raise `--jobs` and `--concurrency` to
push harder:

```bash
cd qatf-backend
python tests/load_api.py --jobs 500 --concurrency 48 --rounds 8
```

What they actually cover:

- **`smoke_db.py`** — `core/db.py` in isolation: WAL and `busy_timeout` are
  actually on, migrations are idempotent and carry a v1 database's data forward
  to v2 without loss, each thread gets its own connection, a failed transaction
  leaves nothing behind, and a corrupt file raises rather than reading as empty.
- **`smoke_pipeline.py`** — timestamp formatting and carry, slugify, caption
  grouping under both budgets, ASS escaping, RTL detection and the
  no-per-word-tags rule, filtergraph escaping and mode rejection, encoder flags
  (no forced `-r`, crf forwarded), device resolution and the CUDA-to-CPU
  fallback, transcript cache keys including vocabulary, fixups (with an explicit
  assertion that **timings are untouched**), snap edge cases, model-response
  parsing.
- **`smoke_llm.py`** — provider request shapes with the SDK client faked. Proves
  request *shape*, not that any endpoint accepts it.
- **`smoke_api.py`** — job state machine, transcript cache round trip, plan
  replace with and without re-snap, re-render replacing outputs, upload
  size/extension/JSON limits, media-root escape rejected, download traversal
  rejected, OpenAPI completeness (every operation summarised, tagged, hand-named
  and error-typed), restart recovery. It fakes `pipeline.audio.run`,
  `pipeline.encode.run`, `pipeline.asr.transcribe` and
  `pipeline.select.pick_clips`, so it **proves nothing about those four**.

### Working without a GPU or an API key

Stages 1, 4 and 5 can be exercised by seeding
`<work>/words-<model>-<lang>.json` and running with `--plan`. Use that for
render-path work instead of waiting on transcription. (The legacy filename
still works — `asr.read_cache` imports it into `qatf.db` on first read.)

Any change to a filtergraph or to caption generation must be verified by
**rendering a clip and looking at an extracted frame**. ffprobe reporting correct
dimensions is not sufficient — the caption overflow bug passed every dimension
check.

```bash
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30:duration=20 test.mp4
```
