# CLI reference

```text
qatf VIDEO [-o OUT] [selection] [transcription] [captions] [encode] [--plan-only | --plan FILE]
```

Also runnable as `python -m qatf` or, via the legacy shim, `python qatf.py`.

The CLI is deliberately importable without pydantic, fastapi or any provider SDK.
`qatf --help` costs nothing.

---

## Selection — stage 3

| Flag | Default | |
| --- | --- | --- |
| `--clips N` | `5` | how many clips to ask for |
| `--min-len S` | `30` | seconds; shorter clips are dropped |
| `--max-len S` | `75` | seconds; see the warning below |

**`--max-len 52`, not 60, for YouTube Shorts.** Stage 4 snaps cut points onto word
boundaries *after* the model picks them, and both boundaries move outward
(−0.15 s lead, +0.35 s tail). Ask for 60 and some clips land at 63, which Shorts
rejects. Reels and TikTok allow longer.

The duration filter allows **±2 s** around the request — an absolute margin, not
a percentage, because what snapping adds is absolute: 0.5 s of lead and tail plus
at most a word of boundary movement. It was `min_len × 0.6` to `max_len × 1.4`,
which admitted a **72.8 s** clip for `--max-len 52` and so defeated the only
reason anyone passes 52. A clip further out than that is the model overrunning
its instruction rather than snapping drifting, and it is **dropped and logged** —
never dropped quietly, because a plan that silently returns 7 clips for
`--clips 8` reads as the model finding nothing, which is a different problem.

---

## Transcription — stage 2

| Flag | Default | |
| --- | --- | --- |
| `--whisper SIZE` | `large-v3` | faster-whisper model size |
| `--device {auto,cuda,cpu}` | `auto` | see below |
| `--language CODE` | autodetect | `ar`, `en`, … |
| `--denoise` | off | speech-band filter + `afftdn` before transcribing |
| `--vocab "TERMS"` | — | space-separated bias terms |
| `--vocab-file PATH` | — | the same, from a file |
| `--prompt "TEXT"` | — | seeds only the first ~30 s |
| `--prompt-file PATH` | — | the same, from a file |
| `--fixups PATH` | — | `wrong = right` substitutions, one per line |

### `--device`

`auto` asks **CTranslate2** — not `nvidia-smi` — whether it can actually target a
CUDA device, and falls back to CPU if not. An explicit `cuda` is honoured and
**will not fall back**: it raises instead, because silently getting CPU turns a
benchmark into a lie.

CPU with `large-v3` is slow enough that stage 2 logs a warning. `--whisper small`
is far quicker if the quality bar allows.

### `--vocab` is the lever; `--prompt` mostly is not

`--vocab` maps to faster-whisper's `hotwords` and applies to the **whole file**.
`--prompt` maps to `initial_prompt`, which seeds only the first ~30 seconds and
then decays — it looks excellent on a short clip and does nothing to a long one.

Write vocabulary terms **the way you want them spelled back**.
[`prompts/ar-tech.txt`](../qatf-backend/prompts/ar-tech.txt) is the working Arabic list.

Both share Whisper's 448-token context with decoding. Overrun it and
faster-whisper dies with `ValueError: The maximum decoding length must be > 0`,
which names neither the vocabulary nor the limit. `check_seed_budget` rejects it
up front instead (`HOTWORD_CHAR_BUDGET` 300, `PROMPT_CHAR_BUDGET` 800).

### `--fixups`

Last resort for words the vocabulary will not take — `بايسون → بايثون`, where the
speaker really does say it that way and Whisper is faithfully spelling what it
heard.

```text
# prompts/ar-fixups.txt
بايسون = بايثون
```

Substitutions touch `Word.text` **only, never timestamps**, so a spelling fix can
change what a caption reads and can never move a cut. Applied on read, not baked
into the cache — so you can edit the map and re-render without re-transcribing.

### Per-word corrections

Fixups are keyed by *value*, so they cannot fix a word that is wrong here and
correct everywhere else — a rule `من = مين` would rewrite every `من` in the file.

For those, write an overlay at `<out>/.work/word-edits.json`. There is no flag:
the file is the interface — it is imported into the `word_edits` table of
`<out>/.work/qatf.db` and re-imported every time the file's mtime moves, so
editing it again after the first run still takes effect (unlike the transcript
cache's `words-*.json`, which is imported once and then ignored — see below).

```json
{
  "edits": [
    { "index": 1247, "was": "من", "text": "مين" }
  ]
}
```

`index` is the word's position in the transcript (`GET /jobs/{id}/transcript`,
or the `transcripts` row in `<out>/.work/qatf.db` — a legacy
`<out>/.work/words-<model>-<lang>.json`, if that is all that is there, works
too), which also gives you its `start` — scrub there to hear what was actually
said before correcting it. `was` is a drift guard: if that word no longer reads `من`, the
correction is reported as **stale** and skipped rather than landing on an
unrelated word.

Corrections change text only, and never the word count or timings. Over HTTP the
same thing is a `PUT /jobs/{id}/transcript` that refuses any submission changing
either — see [api.md](api.md#correcting-misheard-words).

See [quality.md](quality.md) for what each of these was actually worth.

---

## Captions — stage 5a

| Flag | Default | |
| --- | --- | --- |
| `--font NAME` | `Arial` | must be installed on the rendering host |
| `--per-line N` | `4` | max words per caption line |
| `--no-captions` | off | render without burned-in text |

`--font Arial` renders **tofu** on Arabic. libass falls back silently, so a
missing face ships as 50 broken clips rather than an error. Use an Arabic-capable
face — `--font "Traditional Arabic"` is the tested one.

Lines are budgeted by **both** word count and character count
(`CAPTION_MAX_CHARS` 22): four 12-character words at 82 px is wider than the
1080 px frame.

Arabic captions appear and clear per line rather than tracking the spoken word.
That is a deliberate consequence of the RTL fix — see
[troubleshooting.md](troubleshooting.md#the-rtl-caption-bug).

---

## Encode — stage 5b

| Flag | Default | |
| --- | --- | --- |
| `--reframe {crop,blur,track}` | `crop` | `track` needs OpenCV: `pip install -e ".[track]"` |
| `--track-tier {fast,balanced,best}` | `balanced` | face detection at 1 · 3 · 8 fps. `--reframe track` only |
| `--resolution R` | `1080p` | `source` · `1080p` · `1440p` · `4k` · `WxH` |
| `--codec {h264,h265}` | `h265` | |
| `--preset P` | `medium` | `veryslow` … `ultrafast` |
| `--10bit` | off | requires a 10-bit source |
| `--crf N` | `20` | lower is better |

### `--reframe`

`crop` keeps roughly **3× the subject pixels** of `blur` on a 16:9 source and
measures 2.1× the bitrate at the same CRF. It is also the **faster** of the two —
`blur` runs a full-frame gaussian and pays for it in wall clock. So blur is
slower *and* softer; reach for it only when the framing genuinely needs the full
width. Full arithmetic in
[architecture.md](architecture.md#stage-5--reframe), timings in
[quality.md](quality.md#render-performance).

`track` is `crop` with a window that follows the speaker, so it carries the same
subject pixels and costs the same to encode — the extra cost is stage 4b, the
face detection pass, which is the only place a vision model runs. It is a third
mode rather than a change to `crop` on purpose: every measured number above was
scored against the two static graphs, and a mode that did not exist cannot
invalidate them.

Requires OpenCV (`pip install -e ".[track]"`); the YuNet weights ship with the
package, so there is nothing to download. Without OpenCV the run fails at
preflight rather than quietly rendering a static crop — the same contract as
`--device cuda`.

### `--track-tier`

The cost knob, and the only difference between the tiers today: face detection
at **1 fps** (`fast`), **3 fps** (`balanced`, the default) or **8 fps** (`best`).
Detection time is linear in it.

**No tier picks the speaker yet.** The active-speaker model is not implemented,
so every detection reports `speaking=0.0` and stage 4c falls back to framing the
largest face. In a two-shot that will sometimes frame the listener, on every
tier. Do not read `best` as "gets the right person".

`fast` has one further blind spot worth knowing: at one sample per second a cut
that moves the subject less than ~0.75 of frame width is indistinguishable from
someone running, so it is smoothed across rather than re-anchored. That is the
deliberate direction to be wrong in — a missed cut costs one bounded pan, a
false one costs a whip on every walking step.

Detections are cached in the `detections` table of `<work>/qatf.db`, row-keyed
`faces-<detector>-<tier>-<key>.json`, keyed to the footage and the detector
settings, and cover a growing set of spans. Moving a
cut in a plan editor re-solves the crop path from disk in milliseconds instead of
re-running the detector. Each solved path is written to `<work>/track-NN.json`
for review — check the ones the CLI reports as thin or fallen back.

### `--resolution source`

Resolved per video: it probes the input and picks the size that resamples least.
A 3840×2160 source in `crop` mode gives **1214×2160** — the crop region at native
pixels, no scaling at all.

In `blur` mode the same source gives 3840×6826, which is enormous and no platform
accepts. **`source` is really only sensible with `crop`.**

Platforms deliver 1080×1920 whatever you upload, so higher resolutions buy
archival fidelity and re-edit headroom, not a better viewer experience.

### `--codec`

**h265 is the default**: ~40% smaller at equal quality and the better archive.

H.265 output is tagged `hvc1`. **Do not remove that tag** — without it the file is
perfectly valid and QuickTime, Safari and iOS all silently refuse to open it.

It costs encode time — measured **3.06x h264** at the same preset, which is what
`--preset` is for. YouTube accepts HEVC; some Instagram and TikTok upload paths
still prefer H.264, so `--codec h264` is one flag away.

### `--preset`

The encoder speed/quality trade, and the **only knob that measurably moves render
time** — the filter chain and caption burn-in are noise, and rendering clips
concurrently buys almost nothing.

`veryfast` measured 1.6x faster than `medium` on h265. `medium` is the default
because a clip is rendered once and watched many times; use a faster preset while
iterating on framing, fonts or captions, then do the final pass at `medium`.

Full matrix in [quality.md](quality.md#h264-vs-h265-across-presets).

### `--10bit`

Worth it from ProRes (4:2:2 10-bit): the extra precision suppresses banding in
skies and skin even though delivery chroma is still 4:2:0. It narrows device
compatibility, so it is opt-in.

Frame rate is never forced. The source rate is preserved — see
[troubleshooting.md](troubleshooting.md#periodic-micro-stutter-on-2997-footage).

---

## Plan control

| Flag | |
| --- | --- |
| `--plan-only` | transcribe and select, write `plan.json`, stop before rendering |
| `--plan FILE` | render a hand-edited plan; re-snaps by default |

```bash
cd qatf-backend
qatf talk.mov -o out/ --plan-only          # write out/plan.json
$EDITOR out/plan.json
qatf talk.mov -o out/ --plan out/plan.json # render exactly that
```

`--plan` **re-snaps**. A human typing `"start": 20.0` is making the same kind of
semantic guess the model makes, and skipping the snap is how clips end up opening
mid-syllable.

`--plan-only` is also the cheapest way to A/B two providers: same cached
transcript, two runs, diff the two `plan.json` files.

---

## Environment

The CLI reads a `.env` from the working directory or any parent. **The real
environment always wins** over a `.env` entry.

| Variable | | |
| --- | --- | --- |
| `QATF_LLM_PROVIDER` | `anthropic` | `openai` · `kimi` · `glm` · `ollama` · `vllm` · `openrouter` |
| `QATF_LLM_MODEL` | preset default | overrides the preset's model |
| `QATF_LLM_BASE_URL` | preset default | point a preset at another host |
| `QATF_LLM_EFFORT` | `medium` | reasoning-effort hint, where accepted |
| `QATF_LLM_MAX_TOKENS` | `16000` | |
| `QATF_LLM_TIMEOUT` | `600` | seconds |
| `QATF_FFMPEG` / `QATF_FFPROBE` | on `PATH` | explicit binary paths |
| `ANTHROPIC_API_KEY` etc. | — | per-provider; see [providers.md](providers.md) |

---

## Worked example

The invocation this project was built against — a 12-minute Egyptian-Arabic talk
recorded in a moving car, 4K ProRes:

```bash
cd qatf-backend
qatf "متسلمش دماغك.mov" -o reels/ \
  --language ar \
  --clips 8 --min-len 28 --max-len 52 \
  --denoise \
  --vocab-file prompts/ar-tech.txt \
  --fixups prompts/ar-fixups.txt \
  --font "Traditional Arabic"
```

Then, to re-encode at native resolution in H.265 without re-transcribing:

```bash
qatf "متسلمش دماغك.mov" -o reels/ --plan reels/plan.json \
  --language ar --denoise --vocab-file prompts/ar-tech.txt \
  --fixups prompts/ar-fixups.txt --font "Traditional Arabic" \
  --resolution source --codec h265 --10bit
```

That costs one ffmpeg pass per clip and no model call. Two things make the reuse
work, and both are easy to get wrong:

- **Keep the same `-o`.** The transcript cache lives at `<out>/.work/`, so a
  different output directory re-transcribes from scratch. `plan.json` is written
  into `<out>/` on every run, not just under `--plan-only`.
- **Repeat the transcription flags.** `--language`, `--denoise`, `--vocab-file`
  and `--whisper` are all part of the cache key. Drop one and you get a cache
  miss, not the previous transcript. `--fixups` is *not* in the key — it is
  applied on read — but you still need to pass it for the captions to say the
  right thing.

Re-rendering into the same directory overwrites the previous clips. Move them
first if you want both.
