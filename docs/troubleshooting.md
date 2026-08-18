# Troubleshooting

Each entry starts with the **symptom**, because that is what you have when you
arrive here. Several of these are silent failures — valid files, clean exit
codes, wrong output.

---

## Arabic captions render as boxes (tofu)

**Cause.** `--font Arial` has no Arabic glyphs. libass falls back **silently**,
which is how you ship 50 clips in the wrong typeface without noticing.

**Fix.** Use an Arabic-capable face that is installed on the **rendering host** —
under the API that is the server, not the caller's machine.

```bash
--font "Traditional Arabic"      # only if the RENDERING host has it
```

`Traditional Arabic` is a Windows face and is **not** present on a stock macOS
host or in the Docker image. `Geeza Pro` works on macOS; the image carries
`Noto Naskh Arabic`. Ask the host, do not guess:

```bash
fc-list : family | tr ',' '\n' | sort -u | grep -i naskh
```

**A missing family is now warned about.** Preflight (CLI) and the worker (API)
log `WARNING font '...' is not installed on this host` before stage 5 runs. Two
things it deliberately does not do: it never refuses the run, and where
`fc-list` is absent it skips silently rather than warning — so **no warning is
not proof the font is present.**

It also only checks that the *family* exists. An installed Latin font passes the
check and still renders tofu, because nothing here inspects glyph coverage.

Verify by rendering one clip and extracting a frame. Do not trust the .ass file.

---

## The RTL caption bug

Fixed, but worth its own section: it is subtle, invisible in the .ass file, and
**the obvious ways to test it all give false passes.**

**Symptom.** Arabic captions render with the words in the wrong order, and the
order changes depending on which word is highlighted.

**Mechanism.** libass starts a new bidi run wherever an ASS override tag causes an
*actual* style change. `build_ass` wrapped the active word in `{\c...}`, which
splits the line into runs that get bidi-reordered independently.

**How it was measured.** Walk the highlight along a line, render one frame per
position, and track the horizontal centre of the yellow pixels:

```text
english (LTR)   sweep >>>>   OK   — left to right, as it should be
arabic  (RTL)   sweep >>>    BAD  — should be right to left
hebrew  (RTL)   sweep >>>    BAD
```

### Three tests that give a false pass — do not trust them

- **Reading the .ass file.** It looks correct either way.
- **Neutralising the highlight colour to compare against plain text.** Setting the
  highlight to the style's own colour removes the style *change*, so libass never
  splits the run. The comparison then measures a line with no effective override
  in it and reports a perfect match. *This produced a confident, wrong "verified"
  result before the position measurement caught it.*
- **Comparing rendered frames byte-for-byte.** Splitting a line also shifts
  outline seams by a pixel or two, so a strict pixel diff flags **English** —
  which renders perfectly — as broken.

**Measure glyph position, not pixel equality.**

### What does not fix it

Unicode bidi controls (RLE/PDF, RLM, FSI/PDI isolates, per-word isolates),
pre-reversing the words, and `\k` karaoke. All still split the run.

### The fix in place

`build_ass` does not emit per-word tags when the line contains any RTL character
(`captions.is_rtl`). RTL lines get one cue spanning the whole caption line, which
lays out correctly because nothing splits the run. LTR is untouched and keeps
word-by-word highlighting.

Pass `highlight=True` to force the old behaviour — the only reason to is to
re-measure the bug.

**The cost.** Arabic captions appear and clear per line rather than tracking the
spoken word. If word-level highlight on RTL ever becomes a requirement, it needs
per-word `\pos` with measured text widths, or a renderer other than libass.
Neither is warranted yet.

---

## The H.265 file will not open in QuickTime, Safari or iOS

**Symptom.** The file is perfectly valid. ffprobe is happy. VLC plays it. Apple
software silently refuses.

**Cause.** Missing `-tag:v hvc1`.

**Fix.** `CODECS` in [`encode.py`](../qatf-backend/qatf/pipeline/encode.py) sets it. **Do not
remove it.** If you are hand-rolling an ffmpeg command, add it yourself.

---

## `cublas64_12.dll is not found` — or the GPU is never used

**Symptom.** `--device cuda` fails with a missing-DLL error, or `auto` quietly
picks `cpu` on a machine with a working GPU.

**Cause, and why the obvious fix is not enough.** `WhisperModel()` constructs
fine; the failure fires **lazily**, during generator consumption, past any
try/except around construction. And `os.add_dll_directory` alone does not fix it:
ctranslate2 resolves cuBLAS from C++ via a plain `LoadLibrary`, which searches
`PATH`. **Both mechanisms are required** — `register_cuda_dlls` does both.

**Check what the engine actually sees:**

```bash
curl -s localhost:8000/healthz | jq '{cuda_devices, transcribe_device}'
```

`cuda_devices` asks CTranslate2, **not `nvidia-smi`**. A card the installed
CTranslate2 cannot target (compute capability, driver mismatch) is not a usable
device, and only the engine knows that.

**If you need to know for certain:** pass `--device cuda` explicitly. It is
honoured and **will not fall back** — it raises instead. Asking for `cuda` and
silently getting `cpu` turns a benchmark into a lie.

---

## `ValueError: The maximum decoding length must be > 0`

**Cause.** The vocabulary or prompt overran Whisper's 448-token context, which is
shared between the seed and decoding. The error names neither.

**Fix.** `check_seed_budget` rejects it up front (`SeedTooLong`, HTTP 422).
Budgets are 300 characters of hotwords, 800 of prompt. Trim the vocabulary to
terms that are both *wrong today* and *frequent*; move the rest to `--fixups`,
which has no budget.

---

## Periodic micro-stutter on 29.97 footage

**Symptom.** Invisible in a still, a periodic hitch in motion — roughly one
duplicated frame every 33 seconds.

**Cause.** `render` used to hardcode `-r 30`. On a 30000/1001 source that makes
ffmpeg duplicate frames to hit the forced rate.

**Fix.** `fps=None` is the default and preserves the source rate. **Never force
`-r` on NTSC-rate footage.**

---

## Captions overflow the frame edges

**Cause.** ASS `WrapStyle` must be `0`. With `2` (no wrapping) caption lines
overflow the 1080 px frame and get clipped at both edges.

**Related.** Caption line length must be budgeted by **characters, not word
count** — four 12-character words at 82 px is wider than 1080 px. `group_words`
enforces both limits (`CAPTION_MAX_WORDS` 4, `CAPTION_MAX_CHARS` 22).

Both bugs pass every dimension check. Only a rendered frame catches them.

---

## Caption colours come out wrong

**ASS colours are BGR, not RGB.** `&H00E0FF&` is yellow.

---

## Literal `{` or `}` appears on screen

Braces in caption text are parsed as ASS override tags. `captions.escape` maps
them to parentheses. If you are building cue text by hand, escape it yourself —
and do not nest a construct that already carries braces inside another one.

---

## The `ass` filter fails on a Windows path

ffmpeg filter arguments need `:` escaped and backslashes turned into forward
slashes. `filtergraph` does this; if you are composing a command by hand, do the
same.

---

## One word in the captions is wrong

**First ask which kind it is**, because the two have different fixes:

| The word is | Fix | Why |
| --- | --- | --- |
| always misheard the same way | `--fixups` / `fixups` | one rule, whole file, reusable across videos |
| wrong here, correct elsewhere | `PUT /jobs/{id}/transcript`, or `<work>/word-edits.json` | keyed by position, touches only that word |

The second case is the one that bites on Arabic. Whisper writing `من` for `مين`
cannot be fixed by substitution — `من` is one of the most common words in the
language and correct almost everywhere else it appears. A global rule would fix
one caption and wreck a dozen.

`<work>/word-edits.json` is imported into the `word_edits` table of
`<work>/qatf.db` and re-imported every time the file's mtime moves, so editing
it again after the first run still takes effect — it stays a live interface
rather than a one-time upgrade path.

Either way the correction changes text only. Cut points are provably unchanged,
so a re-render is stage 5 alone: no model call, no re-transcription.

---

## `PUT /transcript` returns 422

You changed something other than word text. The message names the word and what
moved.

Word timings come from the audio and are what every cut point is snapped to, so
they are reported and never accepted back. The word count is locked for the same
reason: there is no honest timing to give a word Whisper never heard.

**To split one word into two, put both in that word's `text`.** The caption
renders the string; the interval is untouched.

---

## A correction stopped working (`edits_stale` is non-zero)

Corrections are keyed by word index and carry the text they replaced. The
transcript moved underneath them — usually a re-transcribe at a different
`whisper` size, or `denoise` toggled — so every index shifted.

**This is the guard working.** Without the recorded `was`, correction #1247 would
have landed silently on an unrelated word. Stale corrections are skipped and
counted, never applied.

Re-fetch the transcript, re-apply your corrections, `PUT` again.

---

## A clip opens or closes mid-syllable

**This is always a stage 4 problem, never stage 3.**

| Symptom | Stage | Look at |
| --- | --- | --- |
| the clip is boring or cuts an argument in half | 3 | the selection prompt |
| the clip opens mid-syllable | 4 | word timings, then `snap` |

Check `GET /jobs/{id}/transcript` first — the word boundaries `snap` relies on are
the only acoustic truth in the system, and their accuracy on Arabic is
**unmeasured**.

If you hand-edited a plan, confirm `snap` was on. A typed `"start": 20.0` is a
semantic guess exactly like the model's.

---

## Rendering got about 3x slower

**h265 is the default codec.** libx265 measured 3.06x libx264 at the same preset
— that is the codec doing more work for a ~40% smaller file, not a regression.

Two ways out, depending on what you want:

- `--preset veryfast` — 1.6x back, still h265. Good while iterating.
- `--codec h264` — back to the old speed, larger files.

Numbers in [quality.md](quality.md#h264-vs-h265-across-presets).

---

## Rendering takes longer than expected

**Check `--reframe` first.** `blur` runs a full-frame gaussian and is markedly
slower than `crop` — for a result that also has ~3× fewer subject pixels. If you
did not deliberately choose it, you are paying twice.

Beyond that, a render is **x264-encode-bound**, which has two consequences people
usually get backwards:

- **Running clips concurrently barely helps.** x264 already saturates the
  machine, so parallel encodes mostly contend.
- **The filter chain is not the problem.** Burning captions, `flags=lanczos` and
  `+faststart` are all effectively free. Stripping them buys nothing.

The lever that works is the encoder speed preset, currently fixed at `medium`.
Measurements in [quality.md](quality.md#render-performance).

If a *whole job* feels slow rather than the render, it is stage 2 — see below.

---

## A job spends most of its time transcribing

Working as intended: Whisper dominates a cold run by an order of magnitude.
Things that actually move it:

- **`--device`** — confirm you got the GPU. `GET /jobs/{id}` reports the device
  stage 2 *actually* used, and `/healthz` reports what `auto` will pick. A
  `large-v3` run on CPU is very slow.
- **`--whisper small`** if the quality bar allows.
- **`--denoise`** — it is ~20% *faster*, not slower, because a cleaner signal
  makes the decoder fall back to higher temperatures less often.
- **The transcript cache.** A second run over the same audio should be free; if
  it is not, you changed a flag that is part of the cache key — see
  [A cached transcript is in the wrong language](#a-cached-transcript-is-in-the-wrong-language).

---

## Clips come out longer than `--max-len`

**Working as intended.** `snap` moves the start to the nearest word start
**minus** 0.15 s and the end to the nearest word end **plus** 0.35 s. Both move
outward, so a clip the model sized at 58 s routinely lands at 60–63 s.

**Fix.** Ask for `--max-len 52` if you are targeting YouTube Shorts' 60-second
limit. Reels and TikTok allow longer.

---

## A cached transcript is in the wrong language

**Cause.** The cache key includes the Whisper size and the forced language —
keying on the output directory alone silently reused an English transcript after
`--language ar`. That specific bug is fixed, but the general shape recurs: **drop
a transcription flag on a re-run and you get a cache miss, not the old
transcript.**

`--language`, `--whisper`, `--vocab`/`--vocab-file` and `--prompt` are all in the
key. `--fixups` and the device are deliberately **not**.

**Fix.** The cache is a row in the `transcripts` table of
`<out>/.work/qatf.db`, keyed by `asr.cache_key`. Deleting
`<out>/.work/words-*.json` alone does nothing once that row exists — it is
only the pre-SQLite format, imported into the database on first read and left
on disk. Delete the row (or the whole `qatf.db`, which holds nothing that
isn't reproducible) to actually force a re-transcription.

---

## The output filename is `02-clip.mp4`

`slugify` is ASCII-only, so an all-Arabic title has nothing to transliterate.
Fine while filenames are internal; not fine once a user sees them. Known gap.

---

## `POST /jobs` returns 403

The path escaped `QATF_MEDIA_ROOT`. That is a **security boundary, not a typo
check** — without it a body naming `../../etc/passwd` would have the server
transcribe any file the process can read. Absolute paths are allowed but must
still resolve inside the root.

A `404` means the opposite: the path was legal and the file is not there.

---

## A job says `failed: interrupted by a server restart`

**Working as intended.** State is a JSON file per job, but the worker is an
in-process thread pool — nothing survives the process. On startup anything left
running is marked failed, because there is no worker still holding it.

Resubmit. The cached transcript survives if the work directory did, so a job that
died during rendering restarts cheaply.

---

## Cancel does not stop the job

**Cancellation is cooperative.** The flag is checked between stages
(`store.checkpoint`) and between clips (`should_stop`). It cannot interrupt an
ffmpeg or Whisper call already in flight, so a cancel during stage 2 on a long
file lands whenever transcription finishes.

Poll for `state: cancelled` to know it actually stopped.

---

## Two jobs are slower than one

`QATF_WORKERS` defaults to `1` on purpose: two concurrent `large-v3` loads fight
over the same GPU. Raise it only for CPU-bound render-only work.

---

## "I verified the endpoints exist" — but only found four

`app.routes` **does not show included routers** since FastAPI 0.141; they are
wrapped in `_IncludedRouter` with no `.path`. Enumerate via
`app.openapi()["paths"]` or you will verify four built-in routes and nothing
else.

Generating the schema also proves every `response_model` resolves, which walking
routes does not.

---

## `python qatf.py` imports itself

It does not, and the reason is worth knowing: the **package directory shadows the
sibling `qatf.py`** on import, because Python's path finder checks directories
before same-named modules. That is what makes the legacy shim work.

**Do not add a third `qatf.py` inside the package.**
