# AI transcript enhancement

Status: design approved 2026-08-22.

A button on the transcript page that proposes corrections for misheard words,
instead of clicking each one by hand across 2,596 words.

---

## The two constraints that shape everything

**1. `edits.diff` refuses anything but a text change.**

```python
if len(submitted) != len(baseline):
    raise TranscriptStructureChanged(...)
```

Ask a model to "fix this transcript" and it merges tokens, splits `A I` into
`AI`, drops fillers and adds punctuation — every one of which changes the word
count and gets the **entire** submission rejected. So the model's contract
cannot be "return a corrected transcript". It is strictly positional: for index
*i*, a replacement or nothing.

Deletion is still expressible: `health.repair` already blanks looped tokens
rather than removing them, and `captions.group_words` skips `if not w.text`. So
"drop this filler" is an edit to `""`, and the count survives.

**2. The model cannot hear the audio.** It sees text, so it is not correcting
transcription — it is guessing from context. `من` → `مين` is unfixable by rule
because `من` is one of the most common words in Arabic, and a model will
happily "fix" a correct word into a plausible wrong one. Unlike a missed error,
a confident wrong correction burns into the captions and is invisible after.

That is why the scope below is enforced by the **server**, not by the prompt.

## Decisions

| | Decision |
| --- | --- |
| Apply vs propose | Propose. One whole-diff preview, accept or discard the pass. |
| Scope | Vocabulary near-misses and non-words only. |
| Vocabulary | `prompts/ar-tech.txt` ∪ the job's `hotwords`, editable per run. |
| Shape | Synchronous `POST`, the documented exception to "nothing is synchronous". |
| Model contract | Index-keyed, model echoes `was`, server matches on `(index, was)`. |

**Whole-diff accept is safe because the per-word editor already exists.** Accept
the pass, then fix a single bad suggestion by clicking that word. You are never
forced to discard 23 good corrections over one bad one.

**Synchronous is the honest exception.** "Nothing is synchronous" exists for
Whisper and ffmpeg, which take minutes. This is one call over ~6,500 tokens —
29s on qwen3-235b, less on Claude. A job state would cost a new `JobState`, a
worker path, stored pending suggestions and polling, for one call that takes
seconds. Starlette runs sync handlers in its own threadpool, so it never blocks
the job pool.

## The scope is a server rule, not a prompt hope

A suggestion is accepted only when its replacement is **exactly a listed term,
or the empty string**. Anything else is dropped no matter how confident the
model's `why` was.

This is the load-bearing decision. It makes "vocabulary and non-words only"
mechanically true rather than an instruction the model may ignore, and it means
the `من → مين` class cannot leak in through a persuasive rationale.

## Validation

Every suggestion is dropped, with a logged reason, when:

| Drop | Because |
| --- | --- |
| index out of range | hallucinated position |
| `was` != baseline token at that index | **the guard against miscounted indices** |
| `text == was` | a no-op |
| `text` not a listed term and not `""` | outside the agreed scope |

The `was` echo is the important one. Today's measurement showed models copying
`[MM:SS]` block labels rather than computing spans — the same class of failure
produces a wrong index here. Matching on `(index, was)` makes a hallucinated
index harmless instead of silently corrupting an unrelated word.

The response reports how many were dropped, so a model that is mostly being
refused is visible rather than quiet.

## The endpoint writes nothing

`POST /jobs/{job_id}/transcript/suggest` is **read-only**. It returns proposals;
the UI applies them to its draft; the existing **Save corrections** button
submits the whole word list through `PUT /transcript` exactly as today.

So `edits.diff` still enforces the count and timing invariant, and there is no
second write path to keep honest. A test asserts the stored transcript is
byte-identical after a suggest call.

## Layering

New `qatf/pipeline/enhance.py` — a text-only step beside `fixups`, `edits` and
`health`. It imports `llm`, which the arrows allow (`pipeline -> llm -> core`).

**This makes CLAUDE.md's "Five stages. Only two are AI" false**, and that line
is load-bearing rather than decorative. It must be updated to say three, with
the boundary that still holds spelled out: stages 1, 4 and 5 remain model-free,
and this step changes `Word.text` only — it can change what a caption reads and
can never move a cut.

## Vocabulary plumbing

`prompts/` is **not in the Docker image** — the Dockerfile copies only
`pyproject.toml`, `README.md` and `qatf/`. Found while writing this spec, not
after shipping. So:

- add `COPY prompts ./prompts` to `qatf-backend/Dockerfile`
- resolve the file relative to the package root, and treat a missing file as an
  **empty list, not an error** — a wheel install without `prompts/` must still
  serve the endpoint, just with only the job's `hotwords` to match against

The request body carries the terms, so the UI can add one for a single run
without writing to the server.

## Testing

- **`smoke_pipeline`** — every validation drop: out-of-range index, `was`
  mismatch, no-op, out-of-vocabulary replacement. Plus that a blank replacement
  is accepted, since that is how a filler is dropped.
- **`smoke_llm`** — request shape and `parse_suggestions` across the tiers.
- **`smoke_api`** — the endpoint returns proposals; the stored transcript is
  byte-identical afterwards; a job with no transcript is 409.
- **Measurement** — `score_transcript.py` before and after on the real Arabic
  transcript. This is the one feature here that can silently make quality
  *worse*, so it has to be shown to help rather than assumed to.

## Measured outcome (2026-08-22)

Run against the real 511-word Arabic transcript with 36 terms on
`qwen/qwen3-235b-a22b`: 6 suggestions, 4 no-ops, **1 correct**, **1 false
positive** — a proposed deletion of `لك`, a word used six times.

`terms_accuracy` did not move: 0.0% before, 0.0% after. The five tracked errors
were never proposed despite their expected terms being in the list.

That found a hole in this spec. "A replacement must be a listed term or the
empty string" bounded replacements and left **deletion unconstrained**.
`validate` now also refuses to delete a token occurring more than once. Numbers
in `docs/quality.md`.

## Risks

- **A confident wrong correction is invisible.** Mitigated by the server-side
  scope rule and the whole-diff preview, not eliminated.
- **The vocabulary is the whole signal.** With an empty list the pass finds
  almost nothing, which is correct behaviour but will read as "broken" unless
  the UI says how many terms it is matching against.
- **A third AI call** in a pipeline whose design statement was "only two". The
  boundary that matters — acoustic timing comes from Whisper alone — is
  untouched, but the statement changes and must be rewritten rather than quietly
  falsified.
