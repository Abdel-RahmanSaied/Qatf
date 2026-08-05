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

### Tested and rejected

So nobody repeats them:

| Change | Result |
|---|---|
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
|---|---|
| `HOTWORD_CHAR_BUDGET` | 300 |
| `PROMPT_CHAR_BUDGET` | 800 |

If you need more vocabulary than fits, the terms that earn their place are the
ones that are (a) wrong today and (b) frequent. Everything else is better handled
by `--fixups`, which has no budget at all.

---

## Reframe — crop keeps ~3× the subject pixels

On a 16:9 source:

| Mode | Path | Subject occupies |
|---|---|---|
| `blur` | 3840×2160 → scale to 1080 wide → **1080×608** in a 1920-tall frame | ⅓ of the height, ⅕ of original resolution |
| `crop` | 3840×2160 → **1215×2160** centre slice → 1080×1920 | the full frame, near-native |

Measured on the same clip at the same CRF, **crop carries 2.1× the bitrate** —
there is genuinely that much more detail to encode.

`crop` is the default and is right for a centred talking head. Reach for `blur`
only when the framing genuinely needs the full width, and know what it costs.

Neither mode tracks the subject. Real auto-reframe is deliberately deferred: a
perfectly tracked bad clip is still a bad clip.

---

## Resolution and codec

```bash
--resolution 1080p|1440p|4k|WxH|source     # default 1080p
--codec h264|h265                          # default h264
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
still prefer H.264, so h264 stays the default.

**`--10bit` is worth it from ProRes** (4:2:2 10-bit): the extra precision
suppresses banding in skies and skin even though delivery chroma is still 4:2:0.
It narrows device compatibility, so it is opt-in.

---

## Captions

| Constant | Value | Why |
|---|---|---|
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

**Whisper word-timestamp accuracy on Arabic.** Transcription *spelling* is now
measured; nobody has checked whether the word **boundaries** `snap` relies on land
where the words actually start. This feeds stage 4 directly, so it degrades cut
quality and not just captions. Clip edges are the thing to inspect.

**Arabic selection quality on any non-Claude provider.** See
[providers.md](providers.md#open-question-arabic-selection-quality).

**Loudness.** No normalisation at all. `loudnorm` is a one-line filter add and
the highest value-per-effort item left in the project.
