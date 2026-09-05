# YouTube-style pill captions

Status: design approved 2026-09-05.

Word-level captions in the YouTube Shorts idiom: the whole caption line on
screen, unspoken words dimmed, and the word currently being spoken sitting in a
filled capsule. **Including on Arabic**, which has never had word-level captions
at all.

---

## The one constraint that shapes everything

`build_ass` does not highlight per word on RTL text, and the reason is not
taste. libass starts a new bidi run wherever an override tag causes an actual
style change, so `{\c}` around one word chops the line into runs that are
reordered independently — measured by walking the highlight along a line and
watching its horizontal centre travel *left to right* on Arabic. Unicode bidi
controls, pre-reversing, and `\k` karaoke were all tried and all still split.

So today Arabic gets one cue per line: it appears and clears as a block, and
never tracks the spoken word.

**Absolute positioning dissolves the constraint rather than working around it.**
If each word is its own `Dialogue` event containing exactly one word, there is
no multi-word run inside any event for an override tag to split. Bidi has
nothing left to reorder. The cost is that libass stops doing line layout for us,
so we have to do it ourselves — which is the entire reason this design needs a
text-measurement dependency and a new module.

CLAUDE.md already names this exact trade and parks it: *"needs per-word `\pos`
with measured text widths, or a renderer other than libass. Neither is warranted
yet."* This spec is the decision to warrant it.

## Decisions

| | Decision |
| --- | --- |
| Line behaviour | Full line always visible; unspoken words dimmed to 45%. |
| Pill fill | `#B4560A`, a deepened saffron. White text, 4.91:1. |
| Active word | Outline **dropped** — the pill carries contrast. |
| Corner radius | True capsule (radius = half pill height). No per-word tuning. |
| Measurement | uharfbuzz, the same shaping engine libass uses. |
| Font file | `fc-match -f "%{file}"`, reusing the existing fontconfig dependency. |
| Surface | `caption_style: "youtube" \| "pop"`, defaulting to `youtube`. |
| When measurement is unavailable | Warn, render `pop`, report the style used. Never raise. |
| Bidi scope | Direction runs, not the full UBA. Stated as a limit, not hidden. |

### Why `#B4560A` and not the saffron already in the product

White on the existing highlight yellow measures **2.17:1** — below even the 3:1
large-text floor — which is why a saffron pill would have to keep its black
outline to stay legible. Outline-on-pill is the muddy combination, and it is
worse on Arabic than Latin: the stroke was already cut from 7px to 4px because
Naskh and the sans Arabic faces have finer connected strokes and a heavy outline
thickens the joins until letterforms bleed together.

Deepening only the pill keeps every property that was actually wanted — constant
white text, saffron family, only the ground changes — and buys **4.91:1**, which
clears 4.5:1 and lets the active word drop its outline entirely.

```text
white on #E8A317 (current highlight)   2.17:1   needs an outline
white on #B4560A (chosen)              4.91:1   outline can go
white on #A34708 (considered)          6.07:1   stops reading as the accent
```

These are computed, not eyeballed. A contrast ratio is one of very few caption
decisions that arithmetic can settle without a render.

## Architecture

```text
pipeline/textlayout.py   NEW. pure. measures words, solves a line into boxes.
                         no ASS knowledge, no ffmpeg, no timing.
pipeline/captions.py     consumes boxes, emits \pos events and the \p1 capsule.
```

The split follows the shape stage 4 already uses: `detect.py` finds things,
`framing.py` solves them, and neither knows about the other's consumers. It also
keeps `captions.py` from roughly doubling in size — it is 318 lines today and
already carries the ASS trust boundary, the RTL policy and the fontconfig probe.

Layering is unchanged: `pipeline -> core`, and `textlayout` imports nothing above
itself. uharfbuzz is a **pipeline** concern, not a core one.

## `textlayout.py`

### Measurement

`font_file(family) -> Path | None` resolves through `fc-match -f "%{file}"`.
Same tool `installed_fonts()` already depends on, same "None means cannot tell,
never missing" discipline.

The face is loaded **once per clip**, not once per word.

Advances come from HarfBuzz with the scale set in 26.6 fixed point and divided
down. Scaling to whole pixels instead would truncate every word's advance, and
that error accumulates along a line — by the last word the capsule would sit
visibly off its word. The inter-word space is measured in the same font at the
same size; it is not assumed to be any fraction of the em.

### Direction runs

Words are grouped into maximal same-direction runs. The **runs** are ordered by
the line's base direction; word order **inside** a run is never touched.

```text
words:   قال    Python   is    الأفضل          base direction: RTL
runs:   [RTL] [ LTR  LTR  ]   [RTL]
visual:      الأفضل   Python is   قال
             └── run order reversed, order WITHIN the Latin run preserved
```

All-Arabic and all-Latin lines are the degenerate single-run case, which is the
overwhelming majority of real input.

**This is a deliberate simplification of the Unicode Bidi Algorithm, not an
implementation of it.** It is correct for embedded runs at one level. Deeply
nested mixed-direction text is out of scope and must be documented as such
rather than left looking solved — the failure mode of a half-implemented bidi is
text that is subtly wrong in a way nobody notices until a native reader sees it.

### Chunking moves to measured width

On this path `group_words`'s character budget is replaced by "add words until
the measured line exceeds usable width". **Usable width is 900px** — the 1080px
frame less the two 90px side margins already set in the `Style:` line. `per_line`
still applies as a word-count cap.

This retires a documented drift hazard. `CAPTION_MAX_CHARS` exists only as a
*proxy* for width, and its own comment warns it must be recomputed whenever
`FONT_SIZE` moves, from an estimate ("900px usable, ~half the em"). Real
measurement makes the proxy unnecessary. `CAPTION_MAX_CHARS` stays for the `pop`
path, unchanged.

A single word wider than the usable width cannot be chunked away. It is centred,
allowed to overflow, and logged.

### Output

Boxes in PlayRes pixels — text, x, width — plus line width and line height. Line
height comes from the face's own metrics, so the capsule is sized by the font
rather than by a guess.

Vertical placement reproduces where `MarginV 300` puts captions today. A style
change must not silently move captions up the frame.

## ASS emission

Three events per word, on three layers:

```text
Layer 0   every word, dimmed, outline kept      spans the WHOLE line window
Layer 1   the capsule                            only while that word is active
Layer 2   the word again, bright, no outline     only while that word is active
```

Layer 0's timings are identical for every word in a line, which removes a whole
class of off-by-one timing bugs — only the capsule and the bright word need
per-word windows, and those reuse `_clamp` unchanged.

The layer order also resolves something that would otherwise need a special
case. The dimmed word underneath keeps its 4px outline, and the active word is
supposed to have none. The capsule sits *between* them and paints over that
outline.

> **Padding has a functional floor, not just an aesthetic value.** It must
> exceed the outline width or a dark fringe of Layer 0 peeks out around the
> active word. Record it as a constraint; do not let a later "tighten the
> padding" change quietly cross it.

### The capsule

A `\p1` vector path: two horizontal edges and four bezier corners, control-point
offset `0.5523 × r`, with `r` = half the pill height. A true capsule is the one
radius that needs no tuning per word length.

Coordinates are emitted as rounded integers relative to the event's own `\pos`.

**The drawing event's text is pure geometry.** No caller-supplied string reaches
it, so it adds no new trust-boundary surface. `escape()` continues to guard
every actual word, and `safe_font` every font name — unchanged.

### Colours

ASS is BGR and its alpha is inverted. Both have bitten this file before.

| | value | note |
| --- | --- | --- |
| pill fill | `&H000A56B4&` | `#B4560A` byte-reversed |
| active word | `&H00FFFFFF&` + `\bord0\shad0` | outline dropped |
| inactive word | `\alpha&H8C&` | 45% opacity; dims fill, outline and shadow together |

Words are placed with `\an5` on the box centres, which centres on the line box
rather than the ink — so a word with a descender does not sit differently from
one without.

### The disjointness invariant changes shape — and the test must change with it

`smoke_pipeline.py` asserts consecutive caption cues do not overlap. That check
exists because `LAST_WORD_HOLD` once put two `Dialogue:` events on screen at
once on essentially every hand-off (34 of 34 pairs in one real clip), and libass
**stacks** simultaneous events — the viewer saw the upcoming caption sitting
above the one still showing.

On this path many events are simultaneous **by design**: every word of a line is
live at once, at different x positions.

So the invariant is now **per line, not per event**. The test must group events
by caption line and assert the *line windows* are disjoint. The bug it was
written to catch — two lines stacked on screen — is still guarded; what changes
is the unit. Weakening it to "cues may overlap" would delete the check instead
of updating it, and this is a check that has already caught a real shipped bug.

## Fallback and reporting

`caption_style: "youtube"` needs two things at render time: uharfbuzz importable,
and a font file resolvable for the requested family.

If either is missing: **log a warning, render `pop`, and record the style
actually used.** Never raise.

This is the `font_warning` policy, not the `--device cuda` policy, and the
project's own rule decides which applies. `--device cuda` and `--reframe track`
raise *because they have no better alternative to fall back to*. Here there is
one, and it is a rendered-and-verified path. Failing an hour-long job over a
missing wheel would be the worse error.

Reporting follows `transcribe_device` exactly: the device actually used is
recorded on the `Transcript`, echoed in the job record and `GET /jobs/{id}`, and
surfaced by `/healthz` up front. Caption style gets the same treatment —
`/healthz` gains a readiness signal so an operator can tell **before** submitting
that the pill path will silently degrade on this host.

A silent fallback that reports success is the failure mode this codebase keeps
re-learning: `--language ar` reusing an English transcript, a cache key missing a
knob, `fc-match` always returning something. Report what happened.

## Surface

| Where | Change |
| --- | --- |
| `core/constants.py` | pill colour, padding, dim alpha, capsule policy — product decisions |
| `api/schemas.py` | `JobOptions.caption_style`, `Literal["youtube", "pop"]`, default `youtube` |
| `jobs/model.py` | style actually used, on the job record |
| `cli/parser.py` | `--caption-style youtube\|pop` |
| `qatf-frontend` | `OptionsForm` select; `src/api/types.ts` mirror |
| `pyproject.toml` | new `captions` extra, added to `all` |
| `Dockerfile` | none — it installs `[all]` |

Constants live in `core/constants.py` because they are product decisions, the
same reasoning that puts `DEFAULT_FONT` there rather than as a literal in six
callers. The web UI keeps a declared mirror, as it already does for the font.

**Licence check, not an assumption.** uharfbuzz's licence must be verified
before the dependency lands. This project has already ruled out ultralytics
(AGPL-3.0) and insightface (non-commercial weights) on exactly these grounds,
and a spec that assumes a licence is a spec that can invalidate itself.

## Verification

The working agreement is explicit: **any change to caption generation must be
verified by rendering a clip and looking at an extracted frame.** ffprobe
reporting correct dimensions is not sufficient — the WrapStyle overflow bug
passed every dimension check.

### `smoke_pipeline.py` — pure, no dependency required

Must run on a host **without** uharfbuzz, so measurement is injected as a fake
rather than imported. That is not a convenience; the fallback path is only
testable if the suite can simulate the dependency being absent.

- direction runs: RTL reversal, LTR untouched, embedded Latin run order preserved
- chunking by measured width; the over-wide single word is centred, not dropped
- capsule path is well-formed and closed; coordinates are integers
- `escape()` still applied to every word; a newline in `Word.text` cannot reach a `Dialogue` line
- **line windows disjoint** (the reshaped invariant), on LTR, RTL and gapless speech
- fallback: no uharfbuzz → `pop` emitted, warning logged, style reported
- fallback: font file unresolvable → same

### `verify_render.py` — the real measurement

This is the check that matters, and it is the direct analogue of the measurement
that caught the original RTL bug: render, extract frames, and track the
**horizontal centroid of the pill** across a line.

```text
english (LTR)   pill sweeps  >>>>   must move left to right
arabic  (RTL)   pill sweeps  <<<<   must move right to left
```

The Arabic assertion is the whole point of the feature. If it sweeps left to
right, the bidi handling is wrong and the feature is broken in exactly the way
the original bug was — invisible in the `.ass` file, invisible in a dimension
check, visible only here.

**A control that must fail.** Every fixture in `verify_render.py` renders a
control that is asserted to fail, because a control that cannot fail is
measuring nothing — that harness has twice reported a broken *harness* as a
broken feature. Here the control is the `pop` style: it must show **no** pill
movement, since it has no pill. A test that passes on both styles is measuring
the renderer, not the feature.

Three traps this file already documents, all of which apply:

- Reading the `.ass` file proves nothing. It looks correct either way.
- Do not neutralise the highlight colour to compare against plain text — that
  removes the style change being tested and produces a confident false pass.
- Do not compare frames byte-for-byte. Outline seams shift a pixel or two and
  flag correct renders as broken. **Measure position, not pixel equality.**

Render on a flat neutral background: a busy test pattern hid the yellow
highlight entirely on the first pass of the original measurement.

## Non-goals

- Full Unicode Bidi Algorithm. Direction runs, scoped and stated.
- Animated pill transitions. `\t` interpolation between word positions is a
  natural next step and is deliberately not in this design.
- Changing the `pop` path. It stays exactly as measured and verified.
- Per-word highlighting inside a word (syllable level). Not a thing anyone asked
  for and the timings do not exist for it.

## Risks

**1. Our layout must agree with libass's rendering of each word.** We own line
layout, but libass still shapes and draws each individual word, so the advance
we compute must match what it draws. Both use HarfBuzz, which is why this is
approach 1 — but it requires `ScaleX`/`ScaleY` at 100 and `Spacing` at 0, or
every position drifts. Any future style edit touching those silently breaks
alignment. Worth an assertion, not just a comment.

**2. Whisper word timestamps on Arabic are still unmeasured.** This feature makes
that visible for the first time: today a whole Arabic line appears at once, so a
word boundary being 200ms late is invisible. A pill sitting on the wrong word is
not. **This will likely surface an existing stage-2 accuracy problem and it will
look like a stage-5a bug.** Diagnose them separately — clip *edge* problems are
stage 4, caption *quality* problems are upstream.

**3. Event count rises roughly 3x per word.** The `ass` filter is 2.1% of a
render, and `build_ass` x20 measured 48ms on a 27,000-word transcript, so there
is room. But that measurement was taken against one cue per line. Re-measure
rather than assume the headroom transfers.

## Open questions

None blocking. The licence check above is a task, not an unknown.
