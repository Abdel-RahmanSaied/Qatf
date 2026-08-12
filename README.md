# qatf (قطف)

**Turn a long video into vertical short-form clips.** A model picks the moments;
everything about *where* the cuts land is deterministic.

قطف — *to pick, to harvest*.

```bash
qatf talk.mov -o reels/ --clips 8 --language ar --denoise \
  --vocab-file prompts/ar-tech.txt --max-len 52
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
passing. See [docs/troubleshooting.md](docs/troubleshooting.md#the-rtl-caption-bug).

**The model never emits timing.** It reads the transcript at ~12-second
resolution and returns `MM:SS`. Stage 4 then snaps those onto real word
boundaries from Whisper. Semantic boundaries come from the model, acoustic
boundaries come from the audio, and they are never mixed. This is the core
invariant — see [docs/architecture.md](docs/architecture.md#the-core-invariant).

**Provider-agnostic clip selection.** Anthropic, OpenAI, Kimi, GLM, OpenRouter,
or a local model via Ollama/vLLM — one config change. Because stages 1, 4 and 5
are model-free, *swapping providers cannot affect cut accuracy or rendering*.
See [docs/providers.md](docs/providers.md).

---

## Install

```bash
pip install -e ".[all]"          # api + every provider SDK
pip install -e ".[api,anthropic]"  # or just the one you use
```

ffmpeg must be on `PATH`, or point `QATF_FFMPEG` at it. A GPU is optional —
`--device auto` uses CUDA when CTranslate2 can actually reach it and falls back
to CPU otherwise.

```bash
cp .env.example .env      # then add a provider key
```

---

## Quickstart

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

**Use `--max-len 52`, not 60.** Stage 4 moves cut points onto word boundaries
*after* the model picks them, which routinely adds a few seconds. Ask for 60 and
some clips land at 63 — which YouTube Shorts rejects. Reels and TikTok allow
longer.

**`--reframe track` follows the largest face, not the active speaker.** There is
no active-speaker model yet, so in a two-shot it can frame the listener. The
tracked path is rendered and measured by `verify_render.py`, but every `TRACK_*`
tuning constant is an unmeasured starting value — say so before promising
multi-speaker support.

**Transcription is cached, so iterating on clip selection is free.** The cache
lives in one `qatf.db` (SQLite, WAL) per work directory, alongside job records,
per-word corrections and the face-detection cache. It is keyed on the Whisper
size and the forced language — passing `--language ar` after an English run
re-transcribes rather than silently reusing the wrong transcript.

Or run it as a service:

```bash
uvicorn qatf.api:app --reload
curl -X POST localhost:8000/jobs -H 'content-type: application/json' \
  -d '{"path": "talk.mov", "clips": 8, "language": "ar", "denoise": true}'
```

---

## Documentation

| Page | Covers |
| --- | --- |
| [architecture.md](docs/architecture.md) | The five stages, the layering rules, and the invariant everything protects |
| [cli.md](docs/cli.md) | Every flag, with the measurement behind each default |
| [api.md](docs/api.md) | HTTP reference, job lifecycle, the hand-edit round trip |
| [providers.md](docs/providers.md) | Provider matrix, the three structured-output tiers, self-hosting |
| [quality.md](docs/quality.md) | The tuning playbook — what actually moved the numbers |
| [operations.md](docs/operations.md) | GPU, Docker, caching, deployment limits |
| [security.md](docs/security.md) | Trust model, where each boundary is enforced, known gaps |
| [troubleshooting.md](docs/troubleshooting.md) | The traps, each with the symptom that identifies it |

Live API reference: **`/docs`** (Swagger UI) and **`/redoc`** once the server is
running.

`CLAUDE.md` is the working agreement for agents editing this repo.

---

## Status: working prototype

Honest about what has and has not run:

**Verified end to end.** A 12-minute 4K ProRes Arabic video, 75 GB, recorded in a
moving car — all five stages, real GPU, real provider, 8 vertical clips with
burned-in Arabic captions. Caption rendering confirmed by extracting frames and
looking at them.

**Not verified.** The HTTP API against a real video (only the CLI has run one).
Every provider except OpenRouter against its real endpoint. Whisper's *word
timestamp accuracy* on Arabic — spelling quality is measured, but nobody has
checked whether the boundaries `snap` depends on land where words actually start.

No CI. 592 checks run with no ffmpeg, GPU, API key, or network:

```bash
python tests/smoke_db.py          #  23   the SQLite layer, in isolation
python tests/smoke_pipeline.py    # 354
python tests/smoke_llm.py         #  38
python tests/smoke_api.py         # 154
python tests/load_api.py          #  23   concurrent load, ~20s
ruff check .
```

One suite needs ffmpeg, because the only honest way to verify a filtergraph is to
render through it and measure the result:

```bash
python tests/verify_render.py     #  11   renders clips, measures where the subject landed
```

`load_api.py` hammers every endpoint from 24 threads and **asserts** — it found
`/healthz` spawning a process per request, which no sequential test would.

Every fixture in `verify_render.py` renders `crop` as a control and asserts the
control **fails**. A check that cannot fail measures nothing — twice a broken
harness reported the subject missing from both renders, which reads exactly like
a broken feature.

**The API has no authentication.** Put something in front of it before exposing a
port — see [security.md](docs/security.md).

---

## Naming

Screened against Maqta, Lamha, Wamda, Nukhba and Zubda — all eliminated on
collisions (`zubda.ai` is a live Arabic-English product). Two items outstanding
before commercial use: a SAIP trademark search (a "Qatf Agricultural Company"
exists — different class, but check), and the Qatif question — القطيف is a Saudi
governorate one vowel away in Latin transliteration.
