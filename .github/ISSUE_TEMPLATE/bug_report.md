---
name: Bug report
about: Something produced the wrong output, or failed
labels: bug
---

## What happened

<!-- The symptom, as you saw it. -->

## What you expected

## How to reproduce

<!-- The exact command, or the request body. -->

```bash

```

## Output

<!-- Stage log, traceback, or the `error` field from GET /jobs/{id}. -->

```text

```

---

**If it is visual — a caption, a crop, a frame — attach the frame.** For this
project a screenshot is usually the difference between a guess and a diagnosis.
Several bugs here (caption overflow, scrambled RTL word order, overlapping cues)
look completely correct in every file and only appear once somebody renders one
and looks at it.

## Environment

- qatf version / commit:
- How you ran it: <!-- CLI / API / Docker / web UI -->
- OS:
- ffmpeg version: <!-- ffmpeg -version | head -1 -->
- GPU: <!-- and what GET /healthz reports for cuda_devices / transcribe_device -->
- Provider: <!-- QATF_LLM_PROVIDER, and the model -->
- Language of the source audio:

## Before you file

- [ ] Checked [troubleshooting.md](../../docs/troubleshooting.md) — it is indexed
      by symptom
- [ ] Checked the known gaps in [README](../../README.md#status-working-prototype)
      and [security.md](../../docs/security.md)

Please do not open a public issue for a security problem — see the note at the
end of [CONTRIBUTING.md](../../CONTRIBUTING.md).
