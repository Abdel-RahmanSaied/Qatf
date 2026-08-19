## What this changes

<!-- The diff shows what. Say why: the measurement, the trap, or the reason the
     obvious approach was wrong. -->

## How you know it works

<!-- Not "it should work" — what did you run, and what did it print? -->

---

### Checks

- [ ] `python tests/smoke_pipeline.py`, `smoke_db.py`, `smoke_llm.py`, `smoke_api.py` pass from `qatf-backend/`
- [ ] `ruff check .` clean
- [ ] `npm test && npm run build` pass from `qatf-frontend/` (if the UI changed)
- [ ] New behaviour has a check — ideally one that fails before this fix

### Only if they apply

- [ ] **Touched a filtergraph or caption generation?** Rendered a clip, extracted
      a frame, and *looked at it*. Attach it. Correct ffprobe dimensions are not
      sufficient — that is exactly how the caption-overflow bug shipped.
- [ ] **Text layout?** Measured glyph positions rather than diffing frames
      byte-for-byte, and did not neutralise the thing being tested. Both
      shortcuts produced confident, wrong results on the RTL bug.
- [ ] **Changed a default?** The measurement is in the description, and the
      number went into `docs/quality.md` or `CLAUDE.md` — not a third place.
      Negative results recorded too.
- [ ] **Touched stage 3 or 4?** The core invariant still holds: the model emits
      no precise timing, and every boundary is snapped to a Whisper word time.
- [ ] **New dependency?** The reason is stated, and the CLI still imports
      without it.
- [ ] **New or changed endpoint?** Summary, description, tag, `operationId` and
      declared error shape are on the route decorator, not only in `docs/api.md`.
