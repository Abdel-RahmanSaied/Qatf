# Contributing to qatf

Thanks for looking — **features and bug fixes are both welcome.** This is a
working prototype with a small, opinionated codebase, and most of its rules
exist because something went wrong once and the fix was written down. This page
is the short version of those rules for humans.

If you want somewhere to start, jump to
[good first contributions](#good-first-contributions). If you want to propose
something larger, open an issue first — not for permission, but because a lot of
this project's design is load-bearing in ways that are not obvious from the
diff, and ten minutes of discussion can save a rewrite.
`CLAUDE.md` is the same agreement written for agents editing the repo; it is
longer and more specific, and it is the authority where the two disagree.

---

## Getting set up

```bash
git clone <your fork> && cd qatf

# backend
cd qatf-backend
pip install -e ".[all,dev]"        # dev adds httpx + ruff for the suites
python tests/smoke_pipeline.py     # should print "354 passed, 0 failed"

# frontend
cd ../qatf-frontend
npm ci && npm test
```

ffmpeg must be on `PATH`, or set `QATF_FFMPEG` and `QATF_FFPROBE`. You do **not**
need a GPU, an API key, or a network connection to run the test suites — that is
deliberate, and any test you add should keep it true.

For anything involving the running stack, use the live-reload overlay so you are
not rebuilding an image on every edit:

```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up
```

---

## Before you write code

**Read [architecture.md](docs/architecture.md) first, especially the core
invariant.** Most of this project's design falls out of one rule, and a change
that violates it will be rejected however good it looks.

> The model never emits precise timing. It sees the transcript in ~12s blocks
> and returns `MM:SS`. Stage 4 snaps those onto real word start/end times from
> Whisper. **Semantic boundaries come from the model; acoustic boundaries come
> from the audio. Never mix them.**

Two practical consequences that catch people out:

- If you are tempted to ask the model for millisecond timestamps, or to skip
  `snap` because the model's numbers "look fine" — don't. It will confabulate
  sub-second values it has no way to know, and clips will open mid-syllable.
- This applies to hand-edited plans too, which is why the API and the UI
  re-snap by default. A human typing `"start": 20.0` is making the same kind of
  guess the model makes.

Corollary worth memorising: **clip quality problems are a stage-3 prompt issue;
clip *edge* problems are a stage-4 issue.** Diagnose them separately.

---

## The rules that will get a change sent back

### 1. Respect the layer arrows

```text
api → jobs → pipeline → llm → core        core imports nothing of ours
cli ───────→ pipeline → llm → core
```

An import pointing the other way is the signal that something is defined in the
wrong package. Move the definition; don't add the import. `JobState` lives in
`jobs/model.py` rather than `api/schemas.py` for exactly this reason.

### 2. Pipeline logic goes in `qatf/pipeline/`, one stage per module

If you find yourself computing a timestamp in a router or in `cli/runner.py`,
it is in the wrong file. Front ends parse input, report progress, and set exit
codes. Stages 1, 4 and 5 import no model client and must stay that way.

### 3. Changing a filtergraph or caption generation? Render a frame and look at it

ffprobe reporting correct dimensions is **not sufficient** — a caption-overflow
bug passed every dimension check while clipping text off both edges of the
frame. Generate a test source and inspect the output:

```bash
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30:duration=20 test.mp4
```

For text layout specifically, **measure glyph positions — never compare frames
byte-for-byte, and never neutralise the thing you are testing.** Both shortcuts
produced confident, wrong "verified" results on the RTL caption bug. The full
story is in [troubleshooting.md](docs/troubleshooting.md#the-rtl-caption-bug)
and it is worth reading before you touch `captions.py`.

### 4. Changing a default? Bring the measurement

Every default in this project has a number behind it, and the numbers live in
[quality.md](docs/quality.md) and `CLAUDE.md` — **nowhere else.** A number copied
into a third place is a number that will disagree with itself.

Record negative results too. `beam_size 8`, `condition_on_previous_text=False`
and `dynaudnorm` are all documented as *tried and rejected* precisely so nobody
spends an afternoon rediscovering them.

Two traps when measuring transcription quality:

- **Score over the whole file, not a slice.** `initial_prompt` once looked like
  a 14-errors-to-0 win because the test slice began exactly where the prompt
  applied. On the full file the errors came straight back.
- **Watch the word count.** A config that "reduces errors" by dropping speech
  must show up as a drop in words.

### 5. New behaviour gets a check, and both suites stay green

They run in seconds with no external dependencies, so there is no excuse for
skipping them. `ruff check .` too. If you are fixing a bug, add the check that
would have caught it — ideally one that fails before your fix.

Prefer a test that pins the *property* over one that pins the output. And if a
test cannot fail, it is measuring nothing: every fixture in `verify_render.py`
renders a `crop` control and asserts that the control **fails**, because twice a
broken harness reported the subject missing from both renders and read exactly
like a broken feature.

### 6. No new dependencies without a stated reason

The pipeline needs only ffmpeg and faster-whisper. fastapi/uvicorn/pydantic sit
behind the `[api]` extra and every provider SDK behind its own, so installing
one provider does not pull the others, and **the CLI must keep working without
any of them** (there is a test for this). The frontend's runtime dependencies
are React and the router; that is the whole list.

Adding a stage-3 provider should be **a row in `llm/presets.py`**, not a
subclass. If it needs a subclass, the reason must be a real protocol
difference, not a different `base_url`.

### 7. Deliberate failures raise `QatfError` subclasses

Each carries its own HTTP status, and one exception handler maps them. A bare
`RuntimeError` escaping the pipeline means something was not thought through.
Don't reintroduce per-case `HTTPException` mapping for domain failures — HTTP
concerns stay out of the pipeline entirely.

### 8. Never echo caller input back in an error

The 422 handler reports location and reason only. It strips FastAPI's `input`
field, but it **cannot** un-say a value a validator formatted into its own
message. So: no `{value!r}` in a validator message — name the allowed set
instead. Three validators had to be fixed for exactly this, while the test suite
reported the rule as held.

---

## Frontend specifics

The web UI is **purely additive: no UI feature may require a backend change.**
If you think one does, that is worth discussing in an issue first.

- `src/styles.css` is the only stylesheet. Components carry class names, never
  inline styles — the sole exception being a percentage computed at runtime,
  which cannot live in a stylesheet.
- Client-side validation in `src/lib/rules.ts` mirrors the server for instant
  feedback. **The server stays the authority.** A mirror may be looser than the
  server by accident; it must never be *stricter*, which refuses input the
  server would accept and reads as a broken UI with no error to explain it.
- Anything derived from the API's own rules (which states are "running", what
  the length slack is) belongs in one place and gets imported, not re-derived
  per component.

---

## Committing

- Small, focused commits. Conventional-ish prefixes (`feat:`, `fix:`, `docs:`,
  `refactor:`, `build:`, `style:`) — match what is already in `git log`.
- **Say why, not what.** The diff shows what changed. A good message here
  records the measurement, the trap, or the reason the obvious approach was
  wrong. Look at recent commits for the register.
- No attribution or co-author trailers.
- **Sign off every commit: `git commit -s`.** Required, and CI enforces it.
- Run both suites and `ruff check .` before you push.

### Sign-off, and what it means

The sign-off line `git commit -s` adds is the
[Developer Certificate of Origin](https://developercertificate.org). It is you
certifying that you wrote the contribution, or otherwise have the right to
submit it, and that you are contributing it under this project's licence.

```text
Signed-off-by: Your Name <your.email@example.com>
```

It uses your `git config user.name` and `user.email`, so set those to something
real. Forgot on a branch you already pushed?

```bash
git rebase --signoff main
git push --force-with-lease
```

There is no CLA and no paperwork beyond this.

### Licence

This project is **Apache-2.0** ([LICENSE](LICENSE)). Contributions are accepted
under the same terms — Section 5 of the licence says so explicitly, and the
sign-off is your acknowledgement of it. You keep the copyright in what you
write; there is no assignment.

Two things that follow, and are worth knowing before you open a PR:

- **Do not paste in code you did not write** unless its licence is compatible
  and you say where it came from. Apache-2.0 and MIT/BSD are fine with
  attribution; **GPL and AGPL code cannot be accepted here**, because it would
  relicense the project out from under everyone using it.
- **The same applies to model weights and other binaries.** The one bundled
  third-party artifact is the YuNet detector, and it is here specifically
  because it is MIT — the obvious alternatives were rejected on licence grounds
  (ultralytics/YOLO is AGPL-3.0; insightface/SCRFD ships MIT code with weights
  licensed for non-commercial research only). If you propose a new model, its
  *weights* licence is the first thing to check, not the code's.
  [docs/licensing.md](docs/licensing.md) has the full audit.

## Continuous integration

Every push and pull request runs [CI](.github/workflows/ci.yml):

| Job | What it runs |
| --- | --- |
| `backend` | `ruff` + all five suites, on Python 3.10 **and** 3.12 |
| `frontend` | `npm test` and `npm run build` (which is the `tsc` type gate) |
| `render` | `verify_render.py` with real ffmpeg installed |
| `docker` | Builds the frontend image, validates both compose files |
| `dco` | Every commit carries a sign-off |

Everything except `render` and `docker` runs locally in seconds with no ffmpeg,
GPU, key or network — so there is rarely a reason to discover a failure on CI
rather than before you push. If a test you add needs one of those, it belongs in
the `render` job, not in the smoke suites; keeping them dependency-free is a
property worth protecting.

The backend Docker image is deliberately not built on every PR: it installs CUDA
wheels and weighs over 6 GB.

---

## Documentation

Documentation that drifts is worse than none, so three rules:

- **Measured numbers live in `docs/quality.md` and `CLAUDE.md`, nowhere else.**
  Other pages link to them.
- **Endpoint behaviour is documented in the route decorator and docstring**, not
  only in `docs/api.md` — `/docs` is where a caller actually looks. `smoke_api.py`
  asserts every operation has a summary, description, tag, hand-written
  `operationId` and a declared error shape, so an undocumented route fails the
  suite rather than shipping quietly.
- **Reusable failure declarations go in `qatf/api/openapi.py`.** A status code
  documented on one route and forgotten on the next is how a generated client
  ends up with no error type for a case it will definitely hit.

---

## Good first contributions

Roughly in order of value per effort:

1. **Loudness normalisation.** `loudnorm` is close to a one-line filter add and
   is the highest value-per-effort item in the project right now.
2. **Clamp overlapping caption cues.** Measured on real material: 71 of 81
   consecutive cue pairs overlap, so two lines are on screen at once for ~3
   frames. The fix is a clamp in `build_ass`; the *product* decision is not
   obvious and is worth settling first — see
   [troubleshooting.md](docs/troubleshooting.md#two-captions-are-on-screen-at-once).
3. **Run a provider against its real endpoint** and report back. Eight presets
   ship; only OpenRouter has ever replied to a real request. Even a "this one
   works" is valuable; a stale default model id is more so — OpenRouter's own
   was already stale when it was checked.
4. **Measure Whisper's word-timestamp accuracy on Arabic.** This feeds `snap`
   directly, so it affects cut quality, not just captions. It needs a real
   recording, not a synthetic transcript.
5. **Make `slugify` non-ASCII aware.** An all-Arabic clip title currently
   produces `02-clip.mp4`. Fine while filenames are internal; not fine once a
   user sees them.

---

## Reporting a problem

Include the symptom, what you expected, and the command or request that produced
it. For anything visual, attach the frame — for this project a screenshot is
usually the difference between a guess and a diagnosis, and
[troubleshooting.md](docs/troubleshooting.md) is indexed by symptom precisely
because so many failures here look correct in every file and only appear when
somebody renders one.

For a security issue, please don't open a public issue. The known gaps are
already catalogued in [security.md](docs/security.md); anything not on that list
is worth raising privately first.
